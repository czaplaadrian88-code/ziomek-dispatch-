#!/usr/bin/env python3
"""reassignment_forward_shadow.py — v2 FORWARD shadow przerzutów (READ-ONLY, OSOBNY PROCES).

Następca offline `reassignment_shadow.py` (v1, 2026-06-07 — werdykt "niejednoznaczny",
bo 85% przerzutów było nieocenialnych: martwe logi nie miały geokodu adresu dostawy).

v2 czyta ŻYWY stan (`orders_state` ma `delivery_coords` + `pickup_coords` per zlecenie)
i dla każdego NIEODEBRANEGO zlecenia O (przypisanego kurierowi A) pyta kontrfaktycznie:
    "gdyby O było TERAZ nieprzypisane, kogo wskazałby Ziomek?"
— wołając PRAWDZIWY `dispatch_pipeline.assess_order` nad pełną (dispatchable) flotą,
z O WYJĘTYM z worka A. Jeśli best != A o margines => `would_reassign=True`.

DLACZEGO PRAWDZIWY assess_order (nie własny scoring): zero dryftu — shadow rankuje
DOKŁADNIE tym samym silnikiem co prod (feasibility_v2 + scoring + OSRM + R6 + A2).

DLACZEGO OSOBNY PROCES (nie hook w shadow_dispatcher hot-path): doktryna projektu —
shadow w hot-path raz wywalił produkcję (V3.27.4 NameError; patrz docstring v1).
Tu wołamy assess_order read-only we WŁASNYM procesie/timerze => latency izolowana,
ZERO ryzyka dla żywego dispatchu. Flaga `ENABLE_REASSIGNMENT_FORWARD_SHADOW` (default OFF).

ZERO MUTACJI: nie pisze orders_state, nie emituje eventów, nie woła Telegrama
(filtrujemy zlecenia bez pickup_coords => omijamy ścieżkę admin-alert w assess_order).
Jedyny zapis: append do `dispatch_state/reassignment_shadow.jsonl`.

⚠ dispatchable_fleet() (NIE surowe build_fleet_snapshot) — wzbogaca shift_end,
inaczej feasibility hard-rejectuje całą flotę (bug czasówki #471036 / Lekcja #80).

Użycie:
  cd /root/.openclaw/workspace/scripts
  /root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.tools.reassignment_forward_shadow
"""
from __future__ import annotations
import sys
sys.path.insert(0, "/root/.openclaw/workspace/scripts")

import json
import os
import tempfile
import logging
import time
import copy as _copy
from datetime import datetime, timezone
from typing import Optional, Dict, Any, List, Tuple

from dispatch_v2 import common as C
from dispatch_v2 import dispatch_pipeline as DP
from dispatch_v2 import courier_resolver as CR

_log = logging.getLogger("reassignment_forward_shadow")

ORDERS_STATE = "/root/.openclaw/workspace/dispatch_state/orders_state.json"
OUT_JSONL = "/root/.openclaw/workspace/dispatch_state/reassignment_shadow.jsonl"

FLAG = "ENABLE_REASSIGNMENT_FORWARD_SHADOW"
MARGIN_KEY = "REASSIGN_FWD_MARGIN"
MAX_ORDERS_KEY = "REASSIGN_FWD_MAX_ORDERS"
DEFAULT_MARGIN = 15.0          # pkt score — rząd wielkości jak AUTO_PROXIMITY min_score_margin
DEFAULT_MAX_ORDERS = 60        # cap zleceń na sweep (latency-guard na 2-vCPU w peaku)
KOORDYNATOR_CID = "26"         # virtual holding bucket (czasówki) — NIE przerzucamy
FLAG_TG = "REASSIGN_FWD_TELEGRAM_LIVE"   # podgląd live na grupę ziomka (default OFF)
NOTIFIED_PATH = "/root/.openclaw/workspace/dispatch_state/reassignment_shadow_notified.json"
TG_CAP = 8                     # max pozycji w 1 komunikacie/sweep (anty-spam grupy)
_SYNTH_POS = {"none", "pin", "pre_shift", ""}  # brak realnej lokalizacji → fikcja/grafik (oznacz „zgadnięta")

# Pola czytane przez assess_order (zweryfikowane dispatch_pipeline.py:2881-3055).
_EVENT_FIELDS = (
    "order_id", "restaurant", "delivery_address", "pickup_coords", "delivery_coords",
    "czas_kuriera_warsaw", "pickup_at_warsaw", "pickup_at", "address_id", "order_type",
    "created_at_utc", "created_at", "delivery_city", "uwagi_pickup_parsed", "prep_minutes",
)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _state_to_order_event(rec: dict) -> dict:
    """Rekord orders_state → order_event (kopia pól, które czyta assess_order)."""
    return {k: rec.get(k) for k in _EVENT_FIELDS if rec.get(k) is not None}


def _active_assigned_orders(orders: dict) -> List[Tuple[str, str, dict]]:
    """Zlecenia NIEODEBRANE (status=assigned, NIE picked_up/delivered), z coords i realnym
    kurierem (nie Koordynator/None). Zwraca [(oid, cid, rekord)]."""
    out: List[Tuple[str, str, dict]] = []
    for oid, r in orders.items():
        if not isinstance(r, dict):
            continue
        if r.get("status") != "assigned":
            continue
        cid = r.get("courier_id")
        scid = str(cid) if cid is not None else ""
        if scid in ("", "None", KOORDYNATOR_CID):
            continue
        if not r.get("pickup_coords") or not r.get("delivery_coords"):
            continue
        out.append((str(oid), scid, r))
    return out


def _bag_oid(b: dict) -> str:
    return str(b.get("order_id") or b.get("id") or "")


def _fleet_without_order(fleet: Dict[str, Any], oid: str, holder_cid: str) -> Dict[str, Any]:
    """Płytka kopia floty z O wyjętym z worka kuriera-posiadacza A (kontrfaktyk
    'gdyby O było teraz nieprzypisane'). NIE mutuje żywego snapshotu (kopiujemy
    tylko zmienianego kuriera + jego listę bag)."""
    out = dict(fleet)
    cs = out.get(holder_cid)
    if cs is None:
        return out
    bag = list(cs.bag or [])
    new_bag = [b for b in bag if _bag_oid(b) != oid]
    if len(new_bag) != len(bag):
        cs2 = _copy.copy(cs)
        cs2.bag = new_bag
        out[holder_cid] = cs2
    return out


def evaluate_order(rec: dict, holder_cid: str, fleet: Dict[str, Any],
                   now: Optional[datetime] = None, margin: float = DEFAULT_MARGIN) -> Optional[dict]:
    """Dla nieodebranego O (u A): policz PRAWDZIWYM assess_order nad flotą z O wyjętym
    z worka A. Zwraca rekord shadow (would_reassign True/False) lub None gdy nieoceniane
    (brak oid / wyjątek silnika / brak jakiegokolwiek feasible kandydata)."""
    now = now or _now_utc()
    oid = str(rec.get("order_id") or "")
    if not oid:
        return None
    order_event = _state_to_order_event(rec)
    fleet_cf = _fleet_without_order(fleet, oid, holder_cid)
    try:
        res = DP.assess_order(order_event, fleet_cf, now=now, _bypass_early_bird=True)
    except Exception as e:
        _log.warning(f"assess_order fail oid={oid}: {type(e).__name__}: {e}")
        return None

    best = getattr(res, "best", None)
    cands = getattr(res, "candidates", None) or []
    a_cand = next((c for c in cands if str(getattr(c, "courier_id", "")) == holder_cid), None)
    a_score = float(getattr(a_cand, "score", 0.0) or 0.0) if a_cand is not None else None

    if best is None:
        return None  # brak feasible kandydata = sytuacja KOORD-owa, NIE przerzut (osobny temat)

    b_cid = str(getattr(best, "courier_id", ""))
    b_score = float(getattr(best, "score", 0.0) or 0.0)
    delta = (b_score - a_score) if a_score is not None else None
    would = (b_cid != holder_cid) and (a_score is None or (b_score - a_score) >= margin)

    cs_b = fleet_cf.get(b_cid)
    cs_a = fleet.get(holder_cid)
    return {
        "ts": now.isoformat(),
        "order_id": oid,
        "restaurant": rec.get("restaurant"),
        "holder_cid": holder_cid,
        "best_cid": b_cid,
        "would_reassign": bool(would),
        "a_in_pool": a_cand is not None,
        "a_score": round(a_score, 2) if a_score is not None else None,
        "b_score": round(b_score, 2),
        "delta_score": round(delta, 2) if delta is not None else None,
        "verdict": getattr(res, "verdict", None),
        "pool_feasible": int(getattr(res, "pool_feasible_count", 0) or 0),
        "a_pos_source": getattr(cs_a, "pos_source", None) if cs_a is not None else None,
        "a_bag_size": len(cs_a.bag) if cs_a is not None and cs_a.bag is not None else None,
        "b_pos_source": getattr(cs_b, "pos_source", None) if cs_b is not None else None,
        "b_bag_size": len(cs_b.bag) if cs_b is not None and cs_b.bag is not None else None,
        "b_tier": getattr(cs_b, "tier_bag", None) if cs_b is not None else None,
        "pickup_coords": rec.get("pickup_coords"),
        "delivery_coords": rec.get("delivery_coords"),
    }


def _append_jsonl(rows: List[dict], path: str = OUT_JSONL) -> None:
    """Append-only log (jak shadow_decisions.jsonl). flush+fsync dla trwałości."""
    if not rows:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:
        _log.warning(f"append_jsonl fail: {e}")


def _load_notified() -> dict:
    try:
        with open(NOTIFIED_PATH, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, ValueError):
        return {}


def _save_notified(d: dict) -> None:
    try:
        fd, t = tempfile.mkstemp(dir=os.path.dirname(NOTIFIED_PATH))
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False)
            f.flush(); os.fsync(f.fileno())
        os.replace(t, NOTIFIED_PATH)
    except Exception as e:
        _log.warning(f"save_notified fail: {e}")


def _notify_telegram(new_rows: list) -> int:
    """JEDEN komunikat SHADOW per sweep na grupę ziomka (send_admin_alert →
    chat_id=admin_id=-5149910559). Wyraźnie NIE-do-wykonania — to grupa operacyjna."""
    if not new_rows:
        return 0
    lines = ["🔁 SHADOW przerzutów (PODGLĄD Ziomka — NIE wykonane, NIE przydzielaj ręcznie):"]
    for r in new_rows[:TG_CAP]:
        real = (r.get("a_pos_source") not in _SYNTH_POS) and (r.get("b_pos_source") not in _SYNTH_POS)
        d = r.get("delta_score")
        lines.append(f"• #{r['order_id']} {r.get('restaurant') or ''}: {r['holder_cid']}→{r['best_cid']} "
                     f"(Δ{('+%.0f' % d) if d is not None else '?'} pkt, {'GPS' if real else 'poz.~zgadnięta'})")
    extra = len(new_rows) - TG_CAP
    if extra > 0:
        lines.append(f"…+{extra} więcej w tym ticku")
    try:
        from dispatch_v2.telegram_utils import send_admin_alert
        send_admin_alert("\n".join(lines), source="reassignment_fwd_live")
        return min(len(new_rows), TG_CAP)
    except Exception as e:  # noqa: BLE001 — notyfikacja nie może wywalić sweepu
        _log.warning(f"reassign tg notify fail: {e}")
        return 0


def run_once(now: Optional[datetime] = None, max_orders: Optional[int] = None,
             margin: Optional[float] = None) -> dict:
    """Jeden sweep: czyta żywy stan, buduje dispatchable flotę, ocenia aktywne zlecenia,
    dopisuje do jsonl. No-op (natychmiastowy) gdy flaga OFF."""
    if not C.flag(FLAG, False):
        return {"skipped": "flag_off"}
    now = now or _now_utc()
    _t0 = time.monotonic()
    flags = C.load_flags()
    if margin is None:
        margin = float(flags.get(MARGIN_KEY, DEFAULT_MARGIN))
    if max_orders is None:
        max_orders = int(flags.get(MAX_ORDERS_KEY, DEFAULT_MAX_ORDERS))

    try:
        with open(ORDERS_STATE, encoding="utf-8") as f:
            d = json.load(f)
        orders = d.get("orders", d) if isinstance(d, dict) else d
    except Exception as e:
        _log.warning(f"orders_state load fail: {e}")
        return {"error": "state_load"}

    active = _active_assigned_orders(orders)
    # priorytet: najstarsze (najpilniejsze) najpierw, potem cap
    active.sort(key=lambda t: t[2].get("assigned_at") or t[2].get("created_at_utc") or "")
    active = active[:max_orders]
    if not active:
        return {"active": 0, "evaluated": 0, "would_reassign": 0,
                "duration_s": round(time.monotonic() - _t0, 2), "ts": now.isoformat()}

    fleet_list = CR.dispatchable_fleet()   # ⚠ enriched (shift_end) — NIE build_fleet_snapshot
    fleet = {str(cs.courier_id): cs for cs in fleet_list}

    rows: List[dict] = []
    n_would = 0
    for oid, cid, rec in active:
        r = evaluate_order(rec, cid, fleet, now=now, margin=margin)
        if r is None:
            continue
        rows.append(r)
        if r["would_reassign"]:
            n_would += 1

    _append_jsonl(rows)

    # Live podgląd na grupę ziomka (flag OFF default): 1 komunikat/sweep, dedup per zlecenie.
    tg_sent = 0
    if C.flag(FLAG_TG, False):
        notified = _load_notified()
        new_rows = [r for r in rows if r.get("would_reassign")
                    and notified.get(r["order_id"]) != r["best_cid"]]
        if new_rows:
            tg_sent = _notify_telegram(new_rows)
        active_oids = {r["order_id"] for r in rows}
        merged = {oid: bc for oid, bc in notified.items() if oid in active_oids}  # auto-clean
        for r in rows:
            if r.get("would_reassign"):
                merged[r["order_id"]] = r["best_cid"]
        _save_notified(merged)

    summary = {
        "active": len(active),
        "evaluated": len(rows),
        "would_reassign": n_would,
        "tg_sent": tg_sent,
        "margin": margin,
        "duration_s": round(time.monotonic() - _t0, 2),
        "ts": now.isoformat(),
    }
    _log.info(f"REASSIGN_FWD sweep {summary}")
    return summary


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    summary = run_once()
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
