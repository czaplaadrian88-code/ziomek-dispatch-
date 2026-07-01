#!/usr/bin/env python3
"""Strażnik pickup-floor — READ-ONLY detektor łamania INV-FEAS-PICKUP-FLOOR.

Inwariant: odbiór (pickup) NIGDY nie może być zaplanowany wcześniej niż
`max(now, shift_start)` kuriera. Audyt spójności 30.06 (preshift-pickup-floor)
znalazł 17 miejsc liczących czas odbioru, tylko 4 z podłogą do shift_start i
ZERO strażników mierzących naruszenie. Ten strażnik NICZEGO nie zmienia — tylko
MIERZY, a jego jsonl jest BASELINE'em dla fal L3 (leak `plan_recheck` co 5min)
i L4 (`available_from`).

Trzy powierzchnie (2 persystowane + 1 podzbiór):
  1. proposal  — regression-sentinel: świeże rekordy shadow_decisions (best z
                 pos_source∈{pre_shift,no_gps}). Naruszenie = clamp NIE zadziałał
                 (`pre_shift_clamp_applied`/`v324a_pickup_clamped_to_shift_start`
                 falsy) a pickup-ETA < podłoga. Dziś ≈0 (clamp działa) — jak
                 skoczy, clamp się cofnął.
  2. plan      — główny sygnał: `courier_plans.json`, stopy pickup/assigned z
                 `predicted_at < shift_start(cid) - 60s` (60 s = parytet
                 `plan_recheck._floor_pickups_to_committed min_delta_sec`).
  3. recheck_leak — izolacja leaku #5: podzbiór plan gdzie `czas_kuriera_warsaw`
                 is None (bez committed ck `_floor_pickups_to_committed` NIE
                 clampuje → surowy pre-shift czas przecieka do następnego ticku).

Read-only, oneshot (timer co 3 min). Zapis kanonem `core.jsonl_appender`
(nie własny open — audyt: 10 kopii write-side to anty-wzorzec). Podsumowanie
per tick ZAWSZE (też przy zerach — to jest baseline). Dedup rekordów naruszeń
kluczem (surface, cid, oid), TTL 24 h, żeby ten sam przeciekający plan nie
spamował jsonl co 3 min (podsumowanie liczy KAŻDE naruszenie — dedup gatuje
tylko szczegółowy rekord).

Uruchomienie: `python3 -m dispatch_v2.tools.pickup_floor_guard [--dry]`
(--dry = pełny przebieg bez zapisu, tylko print podsumowania).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Iterable, List, Optional, Tuple

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2.core.jsonl_appender import append_jsonl  # noqa: E402
from dispatch_v2 import plan_recheck as PR  # noqa: E402

# Kanoniczny parser ISO (obsługuje "Z", naive→UTC oraz offset +02:00 z
# effective_start_at / czas_kuriera_warsaw). Reuse silnika, nie druga kopia.
_parse_dt = PR._parse_dt

STATE_DIR = "/root/.openclaw/workspace/dispatch_state"
GUARD_LOG = os.path.join(STATE_DIR, "pickup_floor_guard.jsonl")
DEDUP_STATE = os.path.join(STATE_DIR, "pickup_floor_guard_state.json")
COURIER_PLANS_PATH = os.path.join(STATE_DIR, "courier_plans.json")

INVARIANT = "INV_FEAS_PICKUP_FLOOR"
# 60 s = parytet z plan_recheck._floor_pickups_to_committed(min_delta_sec=60.0):
# przesunięcia < 1 min silnik traktuje jak no-op, więc i strażnik nie alarmuje.
PICKUP_FLOOR_TOL_SEC = 60.0
# |now - shift_start| lub |now - predicted| > 12 h → naiwny _shift_start_dt liczy
# dziś-HH:MM (nocna zmiana zawija) albo plan jest nieświeży (stary dzień) →
# NIE naruszenie, tylko podejrzenie (osobny bucket, żeby nie zafałszować bazy).
SUSPECT_HORIZON_SEC = 12 * 3600
DEDUP_TTL_SEC = 24 * 3600
PARCEL_OID_MIN = 900_000_000
PROPOSAL_LOOKBACK_MIN = 15
LEDGER_MAX_BYTES = 16 * 1024 * 1024

_UNSET: Any = object()


# ---------------------------------------------------------------------------
# Ładowanie źródeł (produkcyjny default; testy wstrzykują deps)
# ---------------------------------------------------------------------------
def _load_plans() -> Dict[str, Any]:
    try:
        with open(COURIER_PLANS_PATH) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception:
        return {}


def _load_fleet_map() -> Dict[str, Dict[str, Any]]:
    """{cid: {shift_start, name, pos_source}} z dispatchable_fleet (offline ~0.1 s;
    wieczorem może zwrócić 0 kurierów → wszystkie plany → shift_start_unknown).
    Fail-soft: awaria floty → {} (plan-floor degraduje do unknown, nie crash)."""
    try:
        from dispatch_v2 import courier_resolver as CR
        fleet = CR.dispatchable_fleet()
    except Exception as e:  # pragma: no cover - defensive
        print(f"[pickup_floor_guard] WARN dispatchable_fleet failed "
              f"({type(e).__name__}: {e}) — plan floor = shift_start_unknown")
        return {}
    out: Dict[str, Dict[str, Any]] = {}
    for cs in fleet or []:
        out[str(cs.courier_id)] = {
            "shift_start": cs.shift_start,
            "name": cs.name,
            "pos_source": cs.pos_source,
        }
    return out


def _ledger_proposals(now: datetime) -> Tuple[Iterable[Dict[str, Any]], bool]:
    """(iterator rekordów shadow z ostatnich 15 min, degraded). ledger_io powstaje
    RÓWNOLEGLE — jeśli go jeszcze nie ma, głośny WARN (nie cichy fallback) i
    pomiń powierzchnię proposal, reszta strażnika działa."""
    cutoff = now - timedelta(minutes=PROPOSAL_LOOKBACK_MIN)
    try:
        from dispatch_v2.tools import ledger_io
    except Exception as e:
        print(f"[pickup_floor_guard] WARN ledger_io unavailable "
              f"({type(e).__name__}: {e}) — proposal surface SKIPPED (degraded)")
        return iter(()), True
    try:
        return ledger_io.iter_shadow_decisions(cutoff_dt=cutoff,
                                               max_bytes=LEDGER_MAX_BYTES), False
    except Exception as e:
        print(f"[pickup_floor_guard] WARN ledger_io.iter_shadow_decisions failed "
              f"({type(e).__name__}: {e}) — proposal surface SKIPPED (degraded)")
        return iter(()), True


# ---------------------------------------------------------------------------
# Dedup (TTL 24 h, atomic temp+fsync+rename)
# ---------------------------------------------------------------------------
def _load_dedup(now: datetime) -> Dict[str, str]:
    try:
        with open(DEDUP_STATE) as fh:
            d = json.load(fh)
        if not isinstance(d, dict):
            return {}
    except Exception:
        return {}
    cutoff = now - timedelta(seconds=DEDUP_TTL_SEC)
    out: Dict[str, str] = {}
    for k, v in d.items():
        ts = _parse_dt(v) if isinstance(v, str) else None
        if ts is not None and ts >= cutoff:
            out[k] = v
    return out


def _save_dedup(state: Dict[str, str]) -> None:
    tmp = DEDUP_STATE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump(state, fh)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(tmp, DEDUP_STATE)


def _dedup_pass(state: Dict[str, str], rec: Dict[str, Any], now: datetime) -> bool:
    """True gdy rekord należy zapisać (nie widziany w TTL). Mutuje `state`."""
    key = f"{rec['surface']}|{rec['cid']}|{rec['oid']}"
    prev = state.get(key)
    if prev:
        pt = _parse_dt(prev)
        if pt is not None and (now - pt).total_seconds() < DEDUP_TTL_SEC:
            return False
    state[key] = now.isoformat()
    return True


# ---------------------------------------------------------------------------
# Budowa rekordu naruszenia
# ---------------------------------------------------------------------------
def _violation_record(now: datetime, *, surface: str, cid: str, name: Optional[str],
                      oid: str, pos_source: Optional[str], floor_kind: str,
                      pickup_at: Optional[datetime], floor_dt: Optional[datetime],
                      late_min: float, clamp_applied: bool,
                      shift_start_source: str, parcel: bool,
                      czasowka: bool) -> Dict[str, Any]:
    return {
        "ts": now.isoformat(),
        "invariant": INVARIANT,
        "surface": surface,
        "cid": cid,
        "name": name,
        "oid": oid,
        "pos_source": pos_source,
        "floor_kind": floor_kind,           # shift_start | now | committed
        "pickup_at": pickup_at.isoformat() if pickup_at else None,
        "shift_start": floor_dt.isoformat() if floor_dt else None,  # =podłoga
        "late_min": round(late_min, 2),
        "clamp_applied": clamp_applied,
        "shift_start_source": shift_start_source,  # record | dispatchable_fleet | unknown
        "parcel": parcel,
        "czasowka": czasowka,
        "verdict": f"pickup_{round(late_min, 1)}min_before_{floor_kind}",
    }


# ---------------------------------------------------------------------------
# Ewaluacja pojedynczych stopów/rekordów → (bucket, record_or_None)
# ---------------------------------------------------------------------------
def _eval_proposal(rec: Dict[str, Any], fleet_map: Dict[str, Dict[str, Any]],
                   now: datetime) -> Tuple[str, Optional[Dict[str, Any]]]:
    best = rec.get("best") or {}
    ps = best.get("pos_source")
    if ps not in ("pre_shift", "no_gps"):
        return "na", None
    oid = str(rec.get("order_id") or "")
    cid = str(best.get("courier_id") or "")
    parcel = oid.isdigit() and int(oid) >= PARCEL_OID_MIN
    # committed (czas_kuriera_warsaw) → floor_kind=committed, NIE naruszenie
    if best.get("czas_kuriera_warsaw"):
        return "committed", None
    clamp = bool(best.get("pre_shift_clamp_applied")) or \
        bool(best.get("v324a_pickup_clamped_to_shift_start"))
    if clamp:
        return "clamped", None
    pickup_at = _parse_dt(best.get("target_pickup_at")) or \
        _parse_dt(best.get("new_pickup_eta_iso"))
    if pickup_at is None:
        return "na", None
    rec_ts = _parse_dt(rec.get("ts")) or now
    if ps == "pre_shift":
        floor_dt = _parse_dt(best.get("effective_start_at"))
        src = "record"
        if floor_dt is None:
            fm = fleet_map.get(cid)
            floor_dt = fm.get("shift_start") if fm else None
            src = "dispatchable_fleet"
        floor_kind = "shift_start"
    else:  # no_gps on-shift → podłoga = now (moment decyzji = rec_ts)
        floor_dt = rec_ts
        src = "record"
        floor_kind = "now"
    if floor_dt is None:
        return "unknown", None
    if abs((rec_ts - floor_dt).total_seconds()) > SUSPECT_HORIZON_SEC:
        return "suspect", None
    late_sec = (floor_dt - pickup_at).total_seconds()
    if late_sec <= PICKUP_FLOOR_TOL_SEC:
        return "ok", None
    record = _violation_record(
        now, surface="proposal", cid=cid,
        name=best.get("name") or (fleet_map.get(cid) or {}).get("name"),
        oid=oid, pos_source=ps, floor_kind=floor_kind,
        pickup_at=pickup_at, floor_dt=floor_dt, late_min=late_sec / 60.0,
        clamp_applied=False, shift_start_source=src, parcel=parcel, czasowka=False)
    return ("viol_parcel" if parcel else "viol"), record


def _eval_plan_stop(cid: str, stop: Dict[str, Any], fleet_map: Dict[str, Dict[str, Any]],
                    orders_state: Dict[str, Any], now: datetime
                    ) -> Tuple[str, Optional[Dict[str, Any]]]:
    if stop.get("type") != "pickup":
        return "na", None
    # status assigned tylko — picked_up (i inne) pomijane
    if stop.get("status_at_plan_time") != "assigned":
        return "na", None
    oid = str(stop.get("order_id") or "")
    predicted = _parse_dt(stop.get("predicted_at"))
    if predicted is None:
        return "na", None
    parcel = oid.isdigit() and int(oid) >= PARCEL_OID_MIN
    fm = fleet_map.get(cid)
    shift_start = fm.get("shift_start") if fm else None
    if shift_start is None:
        return "unknown", None
    # nocna zmiana zawija / plan nieświeży → podejrzenie, nie naruszenie
    if abs((now - shift_start).total_seconds()) > SUSPECT_HORIZON_SEC:
        return "suspect", None
    if (now - predicted).total_seconds() > SUSPECT_HORIZON_SEC:
        return "suspect", None
    # czasówka (scheduled_at) LUB committed (czas_kuriera_warsaw z orders_state)
    # → floor_kind=committed, NIE naruszenie shift-start-floor
    czasowka = bool(stop.get("scheduled_at"))
    ck = (orders_state.get(oid) or {}).get("czas_kuriera_warsaw")
    if czasowka or ck:
        return "committed", None
    late_sec = (shift_start - predicted).total_seconds()
    if late_sec <= PICKUP_FLOOR_TOL_SEC:
        return "ok", None
    # ck is None → surowy pre-shift czas, którego _floor_pickups_to_committed nie
    # clampuje = dokładnie leak #5 → surface recheck_leak
    record = _violation_record(
        now, surface="recheck_leak", cid=cid,
        name=(fm or {}).get("name"), oid=oid,
        pos_source=(fm or {}).get("pos_source"), floor_kind="shift_start",
        pickup_at=predicted, floor_dt=shift_start, late_min=late_sec / 60.0,
        clamp_applied=False, shift_start_source="dispatchable_fleet",
        parcel=parcel, czasowka=False)
    return ("viol_parcel" if parcel else "viol"), record


# ---------------------------------------------------------------------------
# Podsumowanie + główna ewaluacja
# ---------------------------------------------------------------------------
def _new_summary(now: datetime) -> Dict[str, Any]:
    return {
        "ts": now.isoformat(),
        "tick_summary": True,
        "invariant": INVARIANT,
        # pola wprost ze spec:
        "n_proposals_scanned": 0,
        "n_plans_active": 0,
        "viol_proposal": 0,
        "viol_plan": 0,
        "viol_recheck_leak": 0,
        "shift_start_unknown_plans": 0,
        # extras (przejrzystość — nie mieszane w główne liczniki):
        "viol_proposal_parcel": 0,
        "viol_plan_parcel": 0,
        "committed_skipped_plans": 0,
        "suspect_plans": 0,
        "proposal_committed": 0,
        "proposal_clamped": 0,
        "proposal_unknown": 0,
        "proposal_suspect": 0,
        "degraded_proposal": False,
    }


def evaluate(*, plans: Optional[Dict[str, Any]] = None,
             orders_state: Optional[Dict[str, Any]] = None,
             fleet_map: Optional[Dict[str, Dict[str, Any]]] = None,
             proposals: Any = _UNSET,
             now: Optional[datetime] = None,
             write: bool = True,
             dedup_state: Optional[Dict[str, str]] = None) -> Dict[str, Any]:
    """Zwraca {summary, violations, appended, degraded}. Deps wstrzykiwalne (test).

    - `proposals` _UNSET → czytaj z ledger_io; podana iterable (nawet []) →
      użyj wprost (bez ledgera, bez degraded).
    - `dedup_state` None → wczytaj/zapisz plik (tylko gdy write); podany dict →
      użyj wprost (test), bez I/O.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    if plans is None:
        plans = _load_plans()
    if orders_state is None:
        orders_state = PR._load_orders_state()
    if fleet_map is None:
        fleet_map = _load_fleet_map()

    own_dedup = dedup_state is None
    if own_dedup:
        dedup_state = _load_dedup(now)

    summary = _new_summary(now)
    appended: List[Dict[str, Any]] = []

    def _handle(bucket: str, record: Optional[Dict[str, Any]],
                main_key: str, parcel_key: str, extra: Dict[str, str]) -> None:
        if bucket in extra:
            summary[extra[bucket]] += 1
            return
        if bucket == "viol":
            summary[main_key] += 1
            if main_key == "viol_plan":
                summary["viol_recheck_leak"] += 1
        elif bucket == "viol_parcel":
            summary[parcel_key] += 1
        else:
            return
        if record is not None and _dedup_pass(dedup_state, record, now):
            appended.append(record)
            if write:
                append_jsonl(GUARD_LOG, record)

    # --- proposal surface ---
    degraded = False
    if proposals is _UNSET:
        prop_iter, degraded = _ledger_proposals(now)
    else:
        prop_iter = proposals
    try:
        for rec in prop_iter:
            summary["n_proposals_scanned"] += 1
            bucket, record = _eval_proposal(rec, fleet_map, now)
            _handle(bucket, record, "viol_proposal", "viol_proposal_parcel",
                    {"committed": "proposal_committed", "clamped": "proposal_clamped",
                     "unknown": "proposal_unknown", "suspect": "proposal_suspect"})
    except Exception as e:  # ledger niekompletny / rekord uszkodzony → degraduj głośno
        degraded = True
        print(f"[pickup_floor_guard] WARN proposal iteration failed "
              f"({type(e).__name__}: {e}) — proposal surface partial (degraded)")
    summary["degraded_proposal"] = degraded

    # --- plan + recheck_leak surface ---
    active = [(str(cid), p) for cid, p in plans.items()
              if isinstance(p, dict) and not p.get("invalidated_at")]
    summary["n_plans_active"] = len(active)
    for cid, plan in active:
        for stop in (plan.get("stops") or []):
            bucket, record = _eval_plan_stop(cid, stop, fleet_map, orders_state, now)
            _handle(bucket, record, "viol_plan", "viol_plan_parcel",
                    {"unknown": "shift_start_unknown_plans", "suspect": "suspect_plans",
                     "committed": "committed_skipped_plans"})

    # --- podsumowanie ZAWSZE (też przy zerach = baseline) ---
    if write:
        append_jsonl(GUARD_LOG, summary)
        if own_dedup:
            _save_dedup(dedup_state)

    return {"summary": summary, "violations": appended,
            "appended": len(appended), "degraded": degraded}


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(description="Strażnik pickup-floor (READ-ONLY).")
    ap.add_argument("--dry", action="store_true",
                    help="pełny przebieg bez zapisu (print podsumowania)")
    args = ap.parse_args(argv)
    res = evaluate(write=not args.dry)
    s = res["summary"]
    print(f"[pickup_floor_guard] proposals={s['n_proposals_scanned']} "
          f"plans_active={s['n_plans_active']} viol_proposal={s['viol_proposal']} "
          f"viol_plan={s['viol_plan']} viol_recheck_leak={s['viol_recheck_leak']} "
          f"unknown_plans={s['shift_start_unknown_plans']} "
          f"committed_skip={s['committed_skipped_plans']} suspect={s['suspect_plans']} "
          f"degraded_proposal={s['degraded_proposal']}{' DRY' if args.dry else ''}")
    for v in res["violations"]:
        print("  VIOL " + json.dumps(v, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
