"""pending_global_resweep — co-minutowy GLOBALNY re-ranking WISZĄCYCH propozycji.

PROBLEM (diagnoza 2026-06-24, case 483138 Chinatown→Plażowa):
  Ziomek liczy propozycję JEDNORAZOWO przy `NEW_ORDER` (shadow_dispatcher konsumuje
  tylko to zdarzenie) i już jej nie re-rankuje. Skutek 1: gdy w oknie oczekiwania
  świat się zmienia (proponowany kurier się obładuje, inny się zwolni) — stara
  propozycja zostaje nieaktualna. Skutek 2: każde zlecenie oceniane NIEZALEŻNIE
  (greedy per-order) — gdy wisi kilka zleceń naraz, ten sam „najlepszy" kurier
  (np. stojący pod restauracją) bywa proponowany do WSZYSTKICH, choć część jedzie
  w inne strony i powinna trafić do różnych kurierów.

ROZWIĄZANIE (ten plik):
  Co minutę bierze WSZYSTKIE wiszące (nieprzypisane) zlecenia i alokuje je GLOBALNIE
  na dispatchowalną flotę — sekwencyjny greedy z aktualizacją stanu floty: po
  wirtualnym przypisaniu zlecenia kurierowi jego worek rośnie, więc kolejne zlecenia
  „w przeciwną stronę" dostają u niego gorszy score → trafiają do innych kurierów.
  Używa PRAWDZIWEGO `dispatch_pipeline.assess_order` (zero dryftu scoringu).

TRYB: SHADOW (default). Loguje `would_repropose` do jsonl — NIE dotyka Telegrama
  ani pending_proposals.json. Jednocześnie MIERZY ile propozycji by się odwróciło
  i ile „pile-on-jednego-kuriera" by rozbił. Flip na żywe re-proponowanie = osobna
  flaga PENDING_RESWEEP_LIVE (default OFF) po ACK + dniu danych.

Wzorzec: tools/reassignment_forward_shadow.py (read-only assess_order, jsonl, flag-gate).

Uruchomienie ręczne:
  cd /root/.openclaw/workspace/scripts
  /root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.tools.pending_global_resweep
"""
from __future__ import annotations
import sys
import json
import os
import tempfile
import logging
import time
import copy as _copy
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import common as C
from dispatch_v2 import dispatch_pipeline as DP
from dispatch_v2 import courier_resolver as CR

_log = logging.getLogger("pending_global_resweep")


def _disable_replay_capture() -> None:
    """Wyłącz obj_replay_capture w TYM procesie. Robimy syntetyczne assess_order z
    wirtualnie zmienionymi workami (alokacja) — te wywołania NIE mogą trafić do
    obj_replay_capture.jsonl (skaziłyby zestaw kalibracyjny). capture() czyta
    getattr(C,...) per-call → override modułowy trzyma się procesu. NIE robimy tego
    na poziomie importu (gdyby ktoś zaimportował moduł do żywego dispatchu → utrata
    capture); robimy w global_allocate (jedyny punkt wołający _assess)."""
    C.ENABLE_OBJ_REPLAY_CAPTURE = False

STATE_DIR = "/root/.openclaw/workspace/dispatch_state"
PENDING_PATH = f"{STATE_DIR}/pending_proposals.json"
ORDERS_STATE = f"{STATE_DIR}/orders_state.json"
OUT_JSONL = f"{STATE_DIR}/pending_global_resweep.jsonl"
KURIER_IDS_PATH = f"{STATE_DIR}/kurier_ids.json"

FLAG = "ENABLE_PENDING_RESWEEP"          # master on/off (shadow). default OFF = no-op.
FLAG_LIVE = "PENDING_RESWEEP_LIVE"       # faktyczne re-proponowanie (edit msg). default OFF.
MARGIN_KEY = "PENDING_RESWEEP_MARGIN"
DEFAULT_MARGIN = 15.0                    # pkt — jak DEFAULT_MARGIN reassignment_fwd / auto-proximity
MAX_HANGING = 8                          # bezpiecznik: max wiszących zleceń/ tick

_EVENT_FIELDS = (
    "order_id", "restaurant", "delivery_address", "pickup_coords", "delivery_coords",
    "czas_kuriera_warsaw", "pickup_at_warsaw", "pickup_at", "address_id", "order_type",
    "created_at_utc", "created_at", "delivery_city", "uwagi_pickup_parsed", "prep_minutes",
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _state_to_order_event(rec: dict) -> dict:
    return {k: rec.get(k) for k in _EVENT_FIELDS if rec.get(k) is not None}


def _alias_map() -> Dict[str, str]:
    try:
        with open(KURIER_IDS_PATH, encoding="utf-8") as f:
            raw = json.load(f)
        return {str(cid): str(name) for name, cid in raw.items()}
    except (OSError, ValueError):
        return {}


def _append_jsonl(rows: List[dict]) -> None:
    if not rows:
        return
    try:
        with open(OUT_JSONL, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
    except OSError as e:
        _log.warning(f"jsonl append fail: {e}")


def _cand_by_cid(res, cid: str):
    """Candidate dla danego cid z wyniku assess_order (lub None)."""
    if res is None or cid is None:
        return None
    for c in (getattr(res, "candidates", None) or []):
        if str(getattr(c, "courier_id", "")) == str(cid):
            return c
    return None


def _bag_entry_from_order(rec: dict) -> dict:
    """Wirtualny wpis do worka kuriera (kopia rekordu zlecenia, status=assigned)."""
    e = dict(rec)
    e["status"] = "assigned"
    e["commitment_level"] = "assigned"
    return e


def _tentative_assign(fleet: Dict[str, Any], cid: str, order_rec: dict) -> Dict[str, Any]:
    """Płytka kopia floty z `order_rec` DOklejonym do worka kuriera `cid`
    (kontrfaktyk 'gdyby ten kurier dostał to zlecenie'). NIE mutuje wejścia."""
    out = dict(fleet)
    cs = out.get(cid)
    if cs is None:
        return out
    cs2 = _copy.copy(cs)
    cs2.bag = list(cs.bag or []) + [_bag_entry_from_order(order_rec)]
    out[cid] = cs2
    return out


def _assess(order_event: dict, fleet: Dict[str, Any], now: datetime):
    try:
        return DP.assess_order(order_event, fleet, now=now, _bypass_early_bird=True)
    except Exception as e:  # noqa: BLE001 — pojedyncze zlecenie nie wywala sweepu
        _log.warning(f"assess_order fail oid={order_event.get('order_id')}: {type(e).__name__}: {e}")
        return None


def global_allocate(hanging: List[Tuple[str, dict]], fleet0: Dict[str, Any],
                    now: datetime) -> Dict[str, dict]:
    """Sekwencyjny greedy z aktualizacją stanu floty.

    hanging: [(oid, orders_state_rec)]. Zwraca {oid: {cid,name,score,feasibility,
    pool_total,pool_feasible,km,r6,cos,spread}} = globalna alokacja.

    Zasada: w każdej rundzie oceniamy WSZYSTKIE jeszcze-niealokowane zlecenia żywym
    assess_order nad BIEŻĄCĄ flotą; przypisujemy to o najwyższym best-score; doklejamy
    je do worka wybranego kuriera; PONOWNIE oceniamy tylko te zlecenia, których
    dotychczasowy best był tym właśnie kurierem (reszta nie mogła się zmienić — zmienił
    się stan tylko jednego kuriera). Tak orderzy w różne strony rozjeżdżają się na
    różnych kurierów.
    """
    _disable_replay_capture()
    events = {oid: _state_to_order_event(rec) for oid, rec in hanging}
    recs = {oid: rec for oid, rec in hanging}
    fleet = dict(fleet0)
    # cache wyników assess per oid (pierwszy pełny przebieg)
    assessed = {oid: _assess(events[oid], fleet, now) for oid in events}
    remaining = set(events.keys())
    allocation: Dict[str, dict] = {}

    def _best_tuple(oid):
        res = assessed.get(oid)
        b = getattr(res, "best", None) if res is not None else None
        if b is None:
            return (None, None, res)
        return (str(b.courier_id), float(b.score), res)

    while remaining:
        # wybierz zlecenie o najwyższym best-score; brak best → None (najniższy priorytet)
        ranked = []
        for oid in remaining:
            cid, score, res = _best_tuple(oid)
            ranked.append((oid, cid, score, res))
        # najpierw te z realnym best, najwyższy score pierwszy
        ranked.sort(key=lambda t: (t[2] is None, -(t[2] if t[2] is not None else -1e18)))
        oid, cid, score, res = ranked[0]

        # mapa BIEŻĄCYCH score wszystkich kandydatów dla tego zlecenia (stan floty w
        # chwili alokacji — uwzględnia obciążenie kurierów z wcześniejszych rund).
        # Pozwala run_once porównać proponowanego kuriera po jego AKTUALNYM score,
        # nie po score sprzed obciążenia (klucz do single-rerank case 483138).
        cand_scores = {str(getattr(c, "courier_id", "")): float(c.score)
                       for c in (getattr(res, "candidates", None) or []) if c.score is not None}

        if cid is None:
            # brak feasible kuriera dla tego zlecenia → KOORD, nie alokujemy, nie ruszamy floty
            allocation[oid] = {"cid": None, "name": None, "score": None,
                               "feasibility": None, "no_courier": True,
                               "cand_scores": cand_scores,
                               "pool_total": int(getattr(res, "pool_total_count", 0) or 0),
                               "pool_feasible": int(getattr(res, "pool_feasible_count", 0) or 0)}
            remaining.discard(oid)
            continue

        b = res.best
        m = getattr(b, "metrics", None) or {}
        allocation[oid] = {
            "cid": cid, "name": getattr(b, "name", None), "score": round(score, 1),
            "feasibility": getattr(b, "feasibility_verdict", None),
            "km": m.get("km_to_pickup"), "r6": m.get("r6_max_bag_time_min"),
            "cos": m.get("r1_new_drop_cosine"), "spread": m.get("deliv_spread_km"),
            "cand_scores": cand_scores,
            "pool_total": int(getattr(res, "pool_total_count", 0) or 0),
            "pool_feasible": int(getattr(res, "pool_feasible_count", 0) or 0),
            "no_courier": False,
        }
        # wirtualnie doklej zlecenie do worka kuriera → kolejne re-oceny widzą obciążenie
        fleet = _tentative_assign(fleet, cid, recs[oid])
        remaining.discard(oid)
        # re-oceń tylko te, których best był tym kurierem (reszta niezmieniona)
        for other in list(remaining):
            ocid, _, _ = _best_tuple(other)
            if ocid == cid:
                assessed[other] = _assess(events[other], fleet, now)
    return allocation


def run_once(now: Optional[datetime] = None, margin: Optional[float] = None) -> dict:
    """Jeden sweep. No-op gdy flaga master OFF."""
    if not C.flag(FLAG, False):
        return {"skipped": "flag_off"}
    now = now or _now_utc()
    _t0 = time.monotonic()
    flags = C.load_flags()
    if margin is None:
        margin = float(flags.get(MARGIN_KEY, DEFAULT_MARGIN))

    try:
        with open(PENDING_PATH, encoding="utf-8") as f:
            pending = json.load(f)
    except (OSError, ValueError) as e:
        _log.warning(f"pending load fail: {e}")
        return {"error": "pending_load"}
    try:
        with open(ORDERS_STATE, encoding="utf-8") as f:
            orders = json.load(f)
    except (OSError, ValueError) as e:
        _log.warning(f"orders_state load fail: {e}")
        return {"error": "state_load"}

    # wiszące = w pending_proposals ORAZ wciąż nieprzypisane (status planned)
    hanging: List[Tuple[str, dict]] = []
    proposed: Dict[str, dict] = {}   # oid -> {cid, score} z propozycji Ziomka
    for oid, p in pending.items():
        rec = orders.get(oid)
        if not rec or rec.get("status") != "planned":
            continue
        if not rec.get("pickup_coords") or not rec.get("delivery_coords"):
            continue
        dr = p.get("decision_record") or {}
        best = dr.get("best") or {}
        proposed[oid] = {"cid": str(best.get("courier_id")) if best.get("courier_id") is not None else None,
                         "score": best.get("score"),
                         "sent_at": p.get("sent_at"), "expires_at": p.get("expires_at"),
                         "auto_route": dr.get("auto_route")}
        hanging.append((oid, rec))

    if not hanging:
        return {"hanging": 0, "would_repropose": 0,
                "duration_s": round(time.monotonic() - _t0, 2), "ts": now.isoformat()}
    hanging = hanging[:MAX_HANGING]

    fleet_list = CR.dispatchable_fleet()
    fleet = {str(cs.courier_id): cs for cs in fleet_list}

    allocation = global_allocate(hanging, fleet, now)

    # metryki rozjazdu (pile-on jednego kuriera) przed/po
    def _pile(d):
        from collections import Counter
        c = Counter(v for v in d.values() if v)
        return (len(c), (max(c.values()) if c else 0))
    before_cids = {oid: proposed[oid]["cid"] for oid in allocation}
    after_cids = {oid: allocation[oid]["cid"] for oid in allocation}
    couriers_before, maxpile_before = _pile(before_cids)
    couriers_after, maxpile_after = _pile(after_cids)
    spread_improved = maxpile_after < maxpile_before

    names = _alias_map()
    rows: List[dict] = []
    n_would = 0
    for oid in allocation:
        a = allocation[oid]
        prop = proposed[oid]
        prop_cid = prop["cid"]
        new_cid = a["cid"]
        prop_orig_score = prop["score"]            # score z chwili propozycji (info)
        new_score = a["score"]
        cand_scores = a.get("cand_scores") or {}
        # BIEŻĄCY score proponowanego kuriera dla tego zlecenia (po globalnej alokacji
        # innych) — None gdy wypadł z puli feasible. To jest właściwa baza porównania.
        prop_now_score = cand_scores.get(str(prop_cid)) if prop_cid else None
        changed = (new_cid != prop_cid)
        delta_now = None
        if new_score is not None and prop_now_score is not None:
            delta_now = round(new_score - float(prop_now_score), 1)
        delta_orig = None
        if new_score is not None and prop_orig_score is not None:
            delta_orig = round(new_score - float(prop_orig_score), 1)
        # would_repropose: kurier się zmienił I (rozbicie pile-on LUB proponowany wypadł
        # z puli LUB nowy istotnie lepszy od AKTUALNego score proponowanego).
        better_now = (prop_now_score is None) or (delta_now is not None and delta_now >= margin)
        would = bool(changed and not a.get("no_courier") and (spread_improved or better_now))
        if a.get("no_courier"):
            reason = "brak_feasible_kuriera_KOORD"
            would = False
        elif not changed:
            reason = "bez_zmian"
        elif prop_now_score is None:
            reason = "proponowany_wypadl"   # proponowany kurier zniknął z puli feasible
        elif spread_improved:
            reason = "rozjazd_kierunkow"     # globalny fix: rozbicie pile-on jednego kuriera (nagłówek)
        elif delta_now is not None and delta_now >= margin:
            reason = "lepszy_kurier"         # proponowany się obładował / ktoś bliżej i lepszy
        else:
            reason = "zmiana_marginalna"
            would = False
        if would:
            n_would += 1
        rows.append({
            "ts": now.isoformat(),
            "order_id": oid,
            "restaurant": orders.get(oid, {}).get("restaurant"),
            "delivery_address": orders.get(oid, {}).get("delivery_address"),
            "proposed_cid": prop_cid,
            "proposed_name": names.get(str(prop_cid)) if prop_cid else None,
            "proposed_orig_score": round(float(prop_orig_score), 1) if prop_orig_score is not None else None,
            "proposed_now_score": round(float(prop_now_score), 1) if prop_now_score is not None else None,
            "new_cid": new_cid,
            "new_name": a.get("name") or (names.get(str(new_cid)) if new_cid else None),
            "new_score": new_score,
            "delta_vs_now": delta_now,
            "delta_vs_orig": delta_orig,
            "would_repropose": would,
            "reason": reason,
            "no_courier": a.get("no_courier", False),
            "new_km_to_pickup": round(a["km"], 2) if a.get("km") is not None else None,
            "new_r6_min": round(a["r6"], 1) if a.get("r6") is not None else None,
            "new_deliv_spread_km": round(a["spread"], 1) if a.get("spread") is not None else None,
            "pool_total": a.get("pool_total"),
            "pool_feasible": a.get("pool_feasible"),
            "auto_route": prop.get("auto_route"),
            "expires_at": prop.get("expires_at"),
            "post_expiry": bool(prop.get("expires_at") and prop["expires_at"] < now.isoformat()),
            # globalny kontekst rozjazdu (te same wartości w każdym wierszu ticku)
            "g_hanging": len(allocation),
            "g_couriers_before": couriers_before,
            "g_couriers_after": couriers_after,
            "g_maxpile_before": maxpile_before,
            "g_maxpile_after": maxpile_after,
            "g_spread_improved": spread_improved,
        })

    _append_jsonl(rows)

    # LIVE re-proponowanie (edit istniejącej wiadomości TG + update pending_proposals)
    # — NIEzaimplementowane dopóki PENDING_RESWEEP_LIVE; wymaga lockowania pliku
    # współdzielonego z żywym telegram_approver. Shadow: tylko log.
    live_acted = 0
    if C.flag(FLAG_LIVE, False):
        _log.warning("PENDING_RESWEEP_LIVE=ON ale ścieżka live niewpięta — shadow-only (patrz docstring)")

    summary = {
        "hanging": len(hanging),
        "would_repropose": n_would,
        "couriers_before": couriers_before, "couriers_after": couriers_after,
        "maxpile_before": maxpile_before, "maxpile_after": maxpile_after,
        "spread_improved": spread_improved,
        "live_acted": live_acted,
        "margin": margin,
        "duration_s": round(time.monotonic() - _t0, 2),
        "ts": now.isoformat(),
    }
    _log.info(f"PENDING_RESWEEP sweep {summary}")
    return summary


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    summary = run_once()
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
