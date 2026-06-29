#!/usr/bin/env python3
"""reassignment_global_select — GLOBALNE rozbijanie pile-on PROPOZYCJI PRZERZUTU.

PROBLEM (Adrian 2026-06-29):
  Konsola koordynatora dostaje ~10 propozycji PRZERZUTU wszystkie na jednego kuriera
  (np. Jakub W). Powód: `reassignment_forward_shadow.evaluate_order` ocenia KAŻDE
  zlecenie NIEZALEŻNIE na stałym zdjęciu floty → jeśli Jakub dobrze ustawiony, dla
  10 zleceń każda osobna ocena mówi „best=Jakub". `feed.py` ma tylko flagę `pile_on`
  (wizualne ostrzeżenie). Brak JOINT-feasibility — przypisanie wszystkich Jakubowi =
  „cała praca do niczego" (przeładowany, R6 breach).

ROZWIĄZANIE (ten plik) — OSOBNA WARSTWA SELEKCJI nad propozycjami generatora:
  Bierze ŚWIEŻE kandydaty przerzutu (quality_reassign=True z reassignment_shadow.jsonl),
  i jeśli ≥2, re-alokuje je GLOBALNIE sekwencyjnym alokatorem `pending_global_resweep.
  global_allocate` (ten sam sprawdzony silnik co dla NOWYCH zleceń): każde zlecenie
  zdjęte z holdera → re-oceniane PRAWDZIWYM assess_order z aktualizacją worka → po 1-2
  zleceniach worek Jakuba rośnie, jego score dla kolejnych spada → reszta idzie do
  innych kurierów albo zostaje u holdera. Survivor = zlecenie którego globalny kurier
  ≠ holder I przechodzi `_quality_gate` (ratunek/oszczędność, reserve-aware,
  rescue-require-holder-absent — DZIEDZICZONE z reassignment_forward_shadow, ZERO 2.
  kopii reguł). Tak Ziomek pokazuje 1-2 tworzące dobry worek, nie 10.

WZORZEC: global_alloc_store (resweep→plik→feed overlay, LIVE od 27.06 dla nowych zleceń).
  Pisze dedykowany kanał `reassign_global_alloc.json` (OVERWRITE per tick, written_at
  TTL) który `feed.py` overlay FILTRUJE do survivorów. shadow_decisions/analityka NIETKNIĘTE.

TRYB: SHADOW (flaga `ENABLE_REASSIGN_GLOBAL_SELECT` default OFF → no-op). Loguje werdykt
  do `reassign_global_select.jsonl` (candidates_in/survivors_out/maxpile before↔after).
  ZERO mutacji stanu dispatchu/Telegrama. Display-only, ręcznie zatwierdzane w konsoli.

OSOBNY MODUŁ/PROCES (NIE hook w reassignment_forward_shadow): izolacja od generatora
  (inny właściciel/sesja), read-only assess we własnym timerze = zero ryzyka dla dispatchu.

Uruchomienie ręczne:
  cd /root/.openclaw/workspace/scripts
  /root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.tools.reassignment_global_select
"""
from __future__ import annotations
import sys
sys.path.insert(0, "/root/.openclaw/workspace/scripts")

import json
import os
import tempfile
import logging
import time
from collections import Counter
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple

from dispatch_v2 import common as C
from dispatch_v2 import courier_resolver as CR
# Reuse rdzenia i reguł — ZERO duplikacji (Przykazanie #0 / Załącznik A):
from dispatch_v2.tools.pending_global_resweep import global_allocate
from dispatch_v2.tools.reassignment_forward_shadow import (
    _fleet_without_order, _quality_gate, _SYNTH_POS,
    OUT_JSONL as REASSIGN_JSONL,
)

_log = logging.getLogger("reassignment_global_select")

STATE_DIR = "/root/.openclaw/workspace/dispatch_state"
ORDERS_STATE = f"{STATE_DIR}/orders_state.json"
KURIER_IDS_PATH = f"{STATE_DIR}/kurier_ids.json"
OUT_CHANNEL = f"{STATE_DIR}/reassign_global_alloc.json"     # kanał DLA KONSOLI (overwrite per tick)
OUT_VERDICT = f"{STATE_DIR}/reassign_global_select.jsonl"   # log shadow-werdyktu (append)

FLAG = "ENABLE_REASSIGN_GLOBAL_SELECT"          # master on/off (shadow). default OFF = no-op.
CAND_TTL_KEY = "REASSIGN_GLOBAL_SELECT_CAND_TTL_SEC"
DEFAULT_CAND_TTL_SEC = 420.0                     # spójne z feed._REASSIGN_TTL_SEC (okno reakcji koordynatora)
MAX_CANDIDATES = 20                              # latency-guard (assess ~O(|S|) initial + re-assess)
TAIL_BYTES = 4_000_000                           # spójne z panel ziomek_feed_tail_bytes


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _alias_map() -> Dict[str, str]:
    try:
        with open(KURIER_IDS_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        return {str(cid): str(name) for name, cid in raw.items()}
    except (OSError, ValueError):
        return {}


def _tail_lines(path: str, tail_bytes: int) -> List[str]:
    """Ostatnie linie pliku append-only (czyta tylko ogon). Wzorzec feed._tail_lines."""
    try:
        size = os.path.getsize(path)
        with open(path, "rb") as f:
            if size > tail_bytes:
                f.seek(size - tail_bytes)
                f.readline()
            data = f.read()
    except OSError:
        return []
    return [ln for ln in data.decode("utf-8", errors="replace").splitlines() if ln.strip()]


def _parse_iso(s: Optional[str]) -> Optional[datetime]:
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00")) if s else None
        if dt is not None and dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except (ValueError, TypeError):
        return None


def _fresh_candidates(orders: dict, now: datetime, ttl_sec: float) -> List[Tuple[str, str, dict]]:
    """Świeże kandydaty przerzutu z reassignment_shadow.jsonl: quality_reassign=True,
    dedup per order (najnowszy), w oknie TTL, RE-WALIDOWANE vs żywy orders_state
    (status assigned ∧ nieodebrane ∧ coords). Holder = BIEŻĄCY z orders_state (nie z
    jsonl — mógł się zmienić). Zwraca [(oid, holder_cid, rec)] — to samo uniwersum co
    feed._load_reassign_proposals (spójność konsola↔selekcja)."""
    lines = _tail_lines(REASSIGN_JSONL, TAIL_BYTES)
    seen: set = set()
    out: List[Tuple[str, str, dict]] = []
    for ln in reversed(lines):
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        if not d.get("quality_reassign"):
            continue
        oid = str(d.get("order_id") or "")
        if not oid or oid in seen:
            continue
        seen.add(oid)
        tsd = _parse_iso(d.get("ts"))
        if tsd is not None and (now - tsd).total_seconds() > ttl_sec:
            continue
        rec = orders.get(oid)
        if not isinstance(rec, dict):
            continue
        if rec.get("picked_up_at"):
            continue                                  # już odebrane — nie przerzucamy z ręki
        if rec.get("status") not in (None, "assigned", "new", "planned"):
            continue                                  # dostarczone/anulowane
        if not rec.get("pickup_coords") or not rec.get("delivery_coords"):
            continue
        cid = rec.get("courier_id")
        holder = str(cid) if cid is not None else ""
        if holder in ("", "None", "26"):              # 26 = Koordynator (holding) — pomiń
            continue
        out.append((oid, holder, rec))
    return out


def _atomic_write_channel(decisions: Dict[str, dict], now: datetime) -> int:
    """Atomowo nadpisz kanał konsoli bieżącymi decyzjami {oid:{action:show|hide,...}}.
    Zwraca liczbę. Fail-soft: błąd → -1 (NIGDY nie wywala sweepu). Wzorzec global_alloc_store.write."""
    try:
        payload = {"written_at": now.isoformat(), "decisions": decisions or {}}
        fd, t = tempfile.mkstemp(dir=os.path.dirname(OUT_CHANNEL))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            f.flush(); os.fsync(f.fileno())
        os.replace(t, OUT_CHANNEL)
        return len(decisions or {})
    except Exception as e:  # noqa: BLE001
        _log.warning(f"channel write fail: {e}")
        return -1


def _append_verdict(row: dict) -> None:
    try:
        with open(OUT_VERDICT, "a", encoding="utf-8") as f:
            f.write(json.dumps(row, ensure_ascii=False) + "\n")
            f.flush(); os.fsync(f.fileno())
    except OSError as e:
        _log.warning(f"verdict append fail: {e}")


def _find_cand(res, cid: str):
    for c in (getattr(res, "candidates", None) or []):
        if str(getattr(c, "courier_id", "")) == str(cid):
            return c
    return None


def select(cands: List[Tuple[str, str, dict]], fleet: Dict[str, Any],
           now: datetime, names: Dict[str, str],
           cand_best: Dict[str, str]) -> Tuple[Dict[str, dict], dict]:
    """Globalna selekcja survivorów. Zwraca (selected, metrics).

    cand_best: {oid: best_cid generatora} (źródło pile-on) — do metryki maxpile_before.
    selected = {oid: {best_cid,best_name,holder_cid,holder_name,arm,reason,save_min,
                      a_late, depiled}} — to co konsola ma POKAZAĆ (overlay filtruje do tego).
    metrics = podsumowanie ticku (pile-on before↔after, kept/dropped + powody)."""
    rec_of = {oid: rec for oid, _, rec in cands}
    holder_of = {oid: holder for oid, holder, _ in cands}
    decisions: Dict[str, dict] = {}
    dropped: List[dict] = []

    # GRUPUJ po CELU generatora (best_cid) — pile-on = ≥2 propozycje na TEN SAM cel (skarga
    # Adriana „10 na Jakuba"). Singleton (unikalny cel / brak) NIE jest kolizją → propozycja
    # generatora stoi (SHOW passthrough, overlay zostawia oryginał z feedu). De-pile TYLKO
    # kolizje — inaczej usunięcie wszystkich z holderów zafałszowuje (holder wygląda na wolnego
    # → order błędnie „stays_with_holder"; bug złapany 29.06 na case 484222/484195).
    groups: Dict[Any, List[str]] = {}
    for oid, holder, _ in cands:
        groups.setdefault(cand_best.get(oid), []).append(oid)

    pile_oids: List[str] = []
    for tgt, members in groups.items():
        if tgt is None or len(members) < 2:
            for oid in members:                       # brak kolizji → pokaż jak proponuje generator
                decisions[oid] = {"action": "show", "order_id": oid,
                                  "holder_cid": holder_of[oid], "best_cid": None,
                                  "passthrough": True, "depiled": False}
        else:
            pile_oids.extend(members)

    # de-pile TYLKO kolidujące: zdejmij je z holderów → global_allocate (sekwencyjna wirtualna
    # alokacja) → 1-2 najlepsze dostają cel, reszta reroute (inny kurier) albo zostaje (hide).
    if pile_oids:
        fleet_minus = dict(fleet)
        for oid in pile_oids:
            fleet_minus = _fleet_without_order(fleet_minus, oid, holder_of[oid])
        res_map: Dict[str, Any] = {}
        allocation = global_allocate([(oid, rec_of[oid]) for oid in pile_oids],
                                     fleet_minus, now, _results_out=res_map)
        # wirtualny rozmiar worka G (kolejność = insertion order allocation) → reserve-aware b_bag
        prior_to_courier: Dict[str, int] = {}
        earlier_count: Dict[str, int] = {}
        for oid in allocation:
            g = (allocation.get(oid) or {}).get("cid")
            earlier_count[oid] = prior_to_courier.get(str(g), 0) if g else 0
            if g:
                prior_to_courier[str(g)] = prior_to_courier.get(str(g), 0) + 1

        for oid in pile_oids:
            holder = holder_of[oid]
            rec = rec_of[oid]
            a = allocation.get(oid) or {}
            g = a.get("cid")
            def _hide(why, extra=None):
                d = {"action": "hide", "order_id": oid, "holder_cid": holder, "why": why}
                if extra:
                    d.update(extra)
                decisions[oid] = d
                dropped.append({"oid": oid, "why": why, **(extra or {})})
            if a.get("no_courier") or g is None:
                _hide("no_feasible_courier_KOORD")
                continue
            g = str(g)
            if g == holder:
                _hide("stays_with_holder", {"g": g})
                continue
            res = res_map.get(oid)
            if res is None or getattr(res, "best", None) is None:
                _hide("no_result")
                continue
            a_cand = _find_cand(res, holder)
            best = res.best
            cs_a = fleet.get(holder)
            cs_g0 = fleet_minus.get(g)
            a_pos = getattr(cs_a, "pos_source", None) if cs_a is not None else None
            b_pos = getattr(fleet.get(g), "pos_source", None) if fleet.get(g) is not None else None
            base_g_bag = len(cs_g0.bag) if (cs_g0 is not None and cs_g0.bag is not None) else 0
            b_bag = base_g_bag + earlier_count.get(oid, 0)   # realny + wirtualnie doklejeni wcześniejsi
            q = _quality_gate(a_cand, best, oid, a_pos, b_pos, holder, g,
                              b_bag=b_bag, a_in_fleet=(cs_a is not None))
            if not q.get("quality_reassign"):
                _hide("quality_failed_vs_global", {"g": g, "q_reason": q.get("quality_reason")})
                continue
            decisions[oid] = {
                "action": "show", "order_id": oid, "restaurant": rec.get("restaurant"),
                "holder_cid": holder, "holder_name": names.get(holder, holder),
                "best_cid": g, "best_name": names.get(g, getattr(best, "name", None) or g),
                "arm": ("ratunek" if q.get("a_late") else "oszczędność"),
                "reason": q.get("quality_reason"), "save_min": q.get("save_min"),
                "a_late": bool(q.get("a_late")), "depiled": True,
            }

    survivors = sum(1 for d in decisions.values() if d.get("action") == "show")
    # metryki pile-on: before = max grupa celu generatora; after = max efektywny cel pokazanych
    before_counts = Counter(str(cand_best[oid]) for oid, _, _ in cands if cand_best.get(oid))
    after_counts: Counter = Counter()
    for oid, d in decisions.items():
        if d.get("action") == "show":
            tgt = d.get("best_cid") or cand_best.get(oid)   # de-piled→nowy cel; singleton→cel generatora
            if tgt:
                after_counts[str(tgt)] += 1
    metrics = {
        "candidates_in": len(cands),
        "survivors_out": survivors,
        "hidden_out": len(cands) - survivors,
        "depiled_groups": sum(1 for t, m in groups.items() if t is not None and len(m) >= 2),
        "maxpile_before": (max(before_counts.values()) if before_counts else 0),
        "maxpile_after": (max(after_counts.values()) if after_counts else 0),
        "couriers_after": len(after_counts),
        "dropped": dropped,
    }
    return decisions, metrics


def run_once(now: Optional[datetime] = None) -> dict:
    """Jeden sweep. No-op (natychmiastowy) gdy flaga master OFF."""
    if not C.flag(FLAG, False):
        return {"skipped": "flag_off"}
    now = now or _now_utc()
    _t0 = time.monotonic()
    flags = C.load_flags()
    ttl = float(flags.get(CAND_TTL_KEY, DEFAULT_CAND_TTL_SEC))

    try:
        with open(ORDERS_STATE, encoding="utf-8") as f:
            d = json.load(f)
        orders = d.get("orders", d) if isinstance(d, dict) else d
    except (OSError, ValueError) as e:
        _log.warning(f"orders_state load fail: {e}")
        return {"error": "state_load"}

    cands = _fresh_candidates(orders, now, ttl)
    capped = False
    if len(cands) > MAX_CANDIDATES:
        capped = True
        cands = cands[:MAX_CANDIDATES]   # NIE silent — logowane niżej (Przykazanie #0: no silent caps)

    names = _alias_map()

    # |S|<2 → brak pile-on do rozbicia: passthrough (action=show, BEZ override), tak by
    # overlay konsoli NIE ukrył jedynej legalnej propozycji.
    if len(cands) < 2:
        decisions = {oid: {
            "action": "show", "order_id": oid, "restaurant": rec.get("restaurant"),
            "holder_cid": holder, "holder_name": names.get(holder, holder),
            "best_cid": None, "best_name": None, "arm": None, "reason": None,
            "save_min": None, "a_late": None, "depiled": False, "passthrough": True,
        } for oid, holder, rec in cands}
        n = _atomic_write_channel(decisions, now)
        summary = {"candidates_in": len(cands), "survivors_out": len(decisions),
                   "passthrough": True, "written": n,
                   "duration_s": round(time.monotonic() - _t0, 2), "ts": now.isoformat()}
        _log.info(f"REASSIGN_GLOBAL_SELECT {summary}")
        return summary

    fleet_list = CR.dispatchable_fleet()              # ⚠ enriched (shift_end) — NIE build_fleet_snapshot
    fleet = {str(cs.courier_id): cs for cs in fleet_list}

    # best_cid generatora (źródło pile-on) — z najnowszego rekordu per oid
    cand_best = _candidate_best_cids(cands)
    decisions, metrics = select(cands, fleet, now, names, cand_best)

    n = _atomic_write_channel(decisions, now)
    verdict = {
        "ts": now.isoformat(), **{k: metrics[k] for k in
            ("candidates_in", "survivors_out", "hidden_out", "maxpile_before", "maxpile_after", "couriers_after")},
        "spread_improved": metrics["maxpile_after"] < metrics["maxpile_before"],
        "capped": capped, "written": n,
        "dropped": metrics["dropped"][:20],
        "duration_s": round(time.monotonic() - _t0, 2),
    }
    _append_verdict(verdict)
    summary = {**{k: verdict[k] for k in
                  ("candidates_in", "survivors_out", "hidden_out", "maxpile_before", "maxpile_after",
                   "spread_improved", "capped", "written", "duration_s")},
               "ts": now.isoformat()}
    _log.info(f"REASSIGN_GLOBAL_SELECT sweep {summary}")
    return summary


def _candidate_best_cids(cands) -> Dict[str, str]:
    """best_cid z generatora per oid (najnowszy rekord quality_reassign) — do metryki pile-on
    before. Czyta ten sam ogon jsonl co _fresh_candidates."""
    lines = _tail_lines(REASSIGN_JSONL, TAIL_BYTES)
    want = {oid for oid, _, _ in cands}
    out: Dict[str, str] = {}
    for ln in reversed(lines):
        if not want:
            break
        try:
            d = json.loads(ln)
        except ValueError:
            continue
        oid = str(d.get("order_id") or "")
        if oid in want and d.get("quality_reassign"):
            bc = d.get("best_cid")
            if bc is not None:
                out[oid] = str(bc)
            want.discard(oid)
    return out


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    print(json.dumps(run_once(), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
