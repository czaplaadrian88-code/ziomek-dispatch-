"""shadow_dispatcher - systemd loop konsumujący NEW_ORDER z event_bus.

Tryb shadow (D15): imituje koordynatora BEZ faktycznego przypisania kuriera.
Dla każdego NEW_ORDER:
    1. build_fleet_snapshot() → dispatch_pipeline.assess_order()
    2. log decyzji do shadow_decisions.jsonl (append-only)
    3. event_bus.mark_processed()

Nie emituje żadnych eventów, nie dotyka panel_client, nie wysyła Telegramów.
Czysty obserwator dla Fazy 1 (Ziomek imituje koordynatora).

Testowanie:
    process_event(event, fleet, meta, now) -- pure function, wywoływalna z testów.
"""
import json
import os
import signal
import sys
import time
import traceback
from dispatch_v2.geocoding import geocode
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional

from dispatch_v2 import event_bus, state_machine
from dispatch_v2.common import load_config, now_iso, setup_logger
from dispatch_v2.courier_resolver import build_fleet_snapshot, dispatchable_fleet
from dispatch_v2.dispatch_pipeline import assess_order, PipelineResult


POLL_INTERVAL_SEC = 5
HEARTBEAT_INTERVAL_SEC = 60
POLL_BATCH_SIZE = 50

_log = setup_logger(
    "shadow_dispatcher",
    "/root/.openclaw/workspace/scripts/logs/shadow.log",
)
_shutdown = False


def _sigterm_handler(signum, frame):
    global _shutdown
    _log.info(f"signal {signum} received → graceful shutdown")
    _shutdown = True


def _load_restaurant_meta(path: str) -> Optional[dict]:
    try:
        with open(path) as f:
            return json.load(f)
    except FileNotFoundError:
        _log.warning(f"restaurant_meta not found: {path} — using fleet fallback only")
        return None
    except Exception as e:
        _log.warning(f"restaurant_meta load fail: {e}")
        return None


def _eta_hhmm_warsaw(iso_utc: Optional[str]) -> Optional[str]:
    """ISO UTC → 'HH:MM' Warsaw local (F1.3)."""
    if not iso_utc:
        return None
    try:
        from dispatch_v2.common import WARSAW
        dt = datetime.fromisoformat(iso_utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(WARSAW).strftime("%H:%M")
    except Exception:
        return None


def _serialize_dt_map(m):
    """V3.17: {oid: datetime} → {oid: ISO UTC str}. Empty/None → None (compact)."""
    if not m:
        return None
    out = {}
    for k, v in m.items():
        if v is None:
            continue
        try:
            if v.tzinfo is None:
                v = v.replace(tzinfo=timezone.utc)
            out[k] = v.isoformat()
        except Exception:
            continue
    return out or None


def _serialize_candidate(c) -> dict:
    plan = c.plan
    m = c.metrics or {}
    return {
        "courier_id": c.courier_id,
        "name": c.name,
        "score": c.score,
        "feasibility": c.feasibility_verdict,
        "reason": c.feasibility_reason,
        "best_effort": c.best_effort,
        "km_to_pickup": m.get("km_to_pickup"),
        "travel_min": m.get("travel_min"),
        "drive_min": m.get("drive_min"),
        "eta_pickup_hhmm": _eta_hhmm_warsaw(m.get("eta_pickup_utc")),
        "eta_drive_hhmm": _eta_hhmm_warsaw(m.get("eta_drive_utc")),
        "pos_source": m.get("pos_source"),
        "bundle_level1": m.get("bundle_level1"),
        "bundle_level2": m.get("bundle_level2"),
        "bundle_level2_dist": m.get("bundle_level2_dist"),
        "bundle_level3": m.get("bundle_level3"),
        "bundle_level3_dev": m.get("bundle_level3_dev"),
        "bonus_l1": m.get("bonus_l1"),
        "bonus_l2": m.get("bonus_l2"),
        "bonus_r4_raw": m.get("bonus_r4_raw"),
        "bonus_r4": m.get("bonus_r4"),
        "bundle_bonus": m.get("bundle_bonus"),
        "timing_gap_bonus": m.get("timing_gap_bonus"),
        "timing_gap_min": m.get("timing_gap_min"),
        "time_to_pickup_ready_min": m.get("time_to_pickup_ready_min"),
        "free_at_utc": m.get("free_at_utc"),
        "free_at_min": m.get("free_at_min"),
        "deliv_spread_km": m.get("deliv_spread_km"),
        "pickup_spread_km": m.get("pickup_spread_km"),
        "dynamic_bag_cap": m.get("dynamic_bag_cap"),
        # F2.1b step 3 metrics (feasibility_v2 R6/R7 telemetry)
        "r6_max_bag_time_min": m.get("r6_max_bag_time_min"),
        "r6_worst_oid": m.get("r6_worst_oid"),
        "r6_is_solo": m.get("r6_is_solo"),
        "r6_bag_size": m.get("r6_bag_size"),
        "r7_ride_km": m.get("r7_ride_km"),
        "r7_warsaw_hour": m.get("r7_warsaw_hour"),
        "r7_in_peak": m.get("r7_in_peak"),
        "r7_is_longhaul": m.get("r7_is_longhaul"),
        "r7_bag_size": m.get("r7_bag_size"),
        # F2.1c step 1 R8 pickup_span metric (raw span w minutach)
        "r8_pickup_span_min": m.get("r8_pickup_span_min"),
        # F2.1b step 4 scoring penalties + F2.1c R8 soft penalty
        "bonus_r6_soft_pen": m.get("bonus_r6_soft_pen"),
        "bonus_r8_soft_pen": m.get("bonus_r8_soft_pen"),
        "bonus_r9_stopover": m.get("bonus_r9_stopover"),
        "bonus_r9_wait_pen": m.get("bonus_r9_wait_pen"),
        "bonus_penalty_sum": m.get("bonus_penalty_sum"),
        "plan": None if plan is None else {
            "sequence": plan.sequence,
            "total_duration_min": plan.total_duration_min,
            "strategy": plan.strategy,
            "sla_violations": plan.sla_violations,
            "osrm_fallback_used": plan.osrm_fallback_used,
            # V3.17 (2026-04-19): per-stop timeline propagation for telegram formatter.
            "per_order_delivery_times": (
                dict(plan.per_order_delivery_times)
                if plan.per_order_delivery_times else None
            ),
            "predicted_delivered_at": _serialize_dt_map(plan.predicted_delivered_at),
            "pickup_at": _serialize_dt_map(plan.pickup_at),
        },
        # Transparency OPCJA A (2026-04-19): bag snapshot for route section mapping
        "bag_context": m.get("bag_context"),
    }


def _serialize_result(result: PipelineResult, event_id: str, latency_ms: float) -> dict:
    from datetime import datetime, timezone
    best = result.best
    best_m = (best.metrics if best is not None else {}) or {}

    # F1.8 fix: target_pickup_at = absolutny moment kiedy kurier ma być w restauracji.
    # Liczone JEDEN raz przy tworzeniu propozycji, używane w handle_callback przy TAK
    # do świeżego (target - now) → time_param. Dzięki temu opóźnione kliknięcia TAK
    # automatycznie zmniejszają deklarowany time bez przesuwania target time.
    target_pickup_at_iso = None
    if best is not None:
        eta_iso = best_m.get("eta_pickup_utc")
        try:
            eta_dt = datetime.fromisoformat(eta_iso.replace("Z", "+00:00")) if eta_iso else None
        except Exception:
            eta_dt = None
        if eta_dt is not None and eta_dt.tzinfo is None:
            eta_dt = eta_dt.replace(tzinfo=timezone.utc)
        ready_dt = result.pickup_ready_at
        if ready_dt is not None and ready_dt.tzinfo is None:
            ready_dt = ready_dt.replace(tzinfo=timezone.utc)
        if eta_dt is not None and ready_dt is not None:
            target_dt = max(eta_dt, ready_dt)
        else:
            target_dt = eta_dt or ready_dt
        if target_dt is not None:
            target_pickup_at_iso = target_dt.isoformat()

    return {
        "ts": now_iso(),
        "event_id": event_id,
        "order_id": result.order_id,
        "restaurant": result.restaurant,
        "delivery_address": result.delivery_address,
        "verdict": result.verdict,
        "reason": result.reason,
        "best": None if best is None else {
            "courier_id": best.courier_id,
            "name": best.name,
            "score": best.score,
            "feasibility": best.feasibility_verdict,
            "reason": best.feasibility_reason,
            "best_effort": best.best_effort,
            "km_to_pickup": best_m.get("km_to_pickup"),
            "travel_min": best_m.get("travel_min"),
            "drive_min": best_m.get("drive_min"),
            "eta_pickup_hhmm": _eta_hhmm_warsaw(best_m.get("eta_pickup_utc")),
            "eta_drive_hhmm": _eta_hhmm_warsaw(best_m.get("eta_drive_utc")),
            "target_pickup_at": target_pickup_at_iso,
            "pos_source": best_m.get("pos_source"),
            "bundle_level1": best_m.get("bundle_level1"),
            "bundle_level2": best_m.get("bundle_level2"),
            "bundle_level2_dist": best_m.get("bundle_level2_dist"),
            "bundle_level3": best_m.get("bundle_level3"),
            "bundle_level3_dev": best_m.get("bundle_level3_dev"),
            "bonus_l1": best_m.get("bonus_l1"),
            "bonus_l2": best_m.get("bonus_l2"),
            "bonus_r4_raw": best_m.get("bonus_r4_raw"),
            "bonus_r4": best_m.get("bonus_r4"),
            "bundle_bonus": best_m.get("bundle_bonus"),
            "timing_gap_bonus": best_m.get("timing_gap_bonus"),
            "timing_gap_min": best_m.get("timing_gap_min"),
            "time_to_pickup_ready_min": best_m.get("time_to_pickup_ready_min"),
            "free_at_utc": best_m.get("free_at_utc"),
            "free_at_min": best_m.get("free_at_min"),
            "deliv_spread_km": best_m.get("deliv_spread_km"),
            "pickup_spread_km": best_m.get("pickup_spread_km"),
            "dynamic_bag_cap": best_m.get("dynamic_bag_cap"),
            # F2.1b step 3 metrics (feasibility_v2 R6/R7 telemetry)
            "r6_max_bag_time_min": best_m.get("r6_max_bag_time_min"),
            "r6_worst_oid": best_m.get("r6_worst_oid"),
            "r6_is_solo": best_m.get("r6_is_solo"),
            "r6_bag_size": best_m.get("r6_bag_size"),
            "r7_ride_km": best_m.get("r7_ride_km"),
            "r7_warsaw_hour": best_m.get("r7_warsaw_hour"),
            "r7_in_peak": best_m.get("r7_in_peak"),
            "r7_is_longhaul": best_m.get("r7_is_longhaul"),
            "r7_bag_size": best_m.get("r7_bag_size"),
            # F2.1c step 1 R8 pickup_span metric (raw span w minutach)
            "r8_pickup_span_min": best_m.get("r8_pickup_span_min"),
            # F2.1b step 4 scoring penalties + F2.1c R8 soft penalty
            "bonus_r6_soft_pen": best_m.get("bonus_r6_soft_pen"),
            "bonus_r8_soft_pen": best_m.get("bonus_r8_soft_pen"),
            "bonus_r9_stopover": best_m.get("bonus_r9_stopover"),
            "bonus_r9_wait_pen": best_m.get("bonus_r9_wait_pen"),
            "bonus_penalty_sum": best_m.get("bonus_penalty_sum"),
            # Transparency OPCJA A (2026-04-19): plan + bag_context for Telegram route section
            "plan": None if (best is None or best.plan is None) else {
                "sequence": best.plan.sequence,
                "total_duration_min": best.plan.total_duration_min,
                "strategy": best.plan.strategy,
                "sla_violations": best.plan.sla_violations,
                "osrm_fallback_used": best.plan.osrm_fallback_used,
                # V3.17 (2026-04-19): per-stop timeline propagation for telegram formatter.
                "per_order_delivery_times": (
                    dict(best.plan.per_order_delivery_times)
                    if best.plan.per_order_delivery_times else None
                ),
                "predicted_delivered_at": _serialize_dt_map(best.plan.predicted_delivered_at),
                "pickup_at": _serialize_dt_map(best.plan.pickup_at),
            },
            "bag_context": best_m.get("bag_context"),
        },
        "alternatives": [
            _serialize_candidate(c) for c in result.candidates[1:]
        ],
        "pickup_ready_at": (
            result.pickup_ready_at.isoformat()
            if result.pickup_ready_at else None
        ),
        "latency_ms": round(latency_ms, 1),
    }


def _append_decision(path: str, record: dict) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a") as f:
        f.write(json.dumps(record, ensure_ascii=False) + "\n")


def process_event(
    event: dict,
    fleet: Dict,
    meta: Optional[dict],
    now: Optional[datetime] = None,
) -> PipelineResult:
    """Pure: NEW_ORDER event + snapshot → PipelineResult. Safe to test."""
    payload = event.get("payload") or {}
    order_event = {
        "order_id": event.get("order_id"),
        "restaurant": payload.get("restaurant"),
        "delivery_address": payload.get("delivery_address"),
        "pickup_coords": payload.get("pickup_coords"),
        "delivery_coords": payload.get("delivery_coords"),
        "pickup_at_warsaw": payload.get("pickup_at_warsaw"),
        "pickup_time_minutes": payload.get("pickup_time_minutes"),
    }
    return assess_order(order_event, fleet, meta, now=now)


def _tick(shadow_log_path: str, meta: Optional[dict]) -> dict:
    """One poll cycle. Returns {processed, failed, skipped}."""
    stats = {"processed": 0, "failed": 0, "skipped": 0}
    events = event_bus.get_pending(limit=POLL_BATCH_SIZE, event_types=["NEW_ORDER"])
    if not events:
        return stats

    fleet = {cs.courier_id: cs for cs in dispatchable_fleet()}

    state_all = state_machine.get_all()
    TERMINAL = ("delivered", "cancelled", "returned_to_pool", "picked_up")

    for ev in events:
        eid = ev["event_id"]
        oid = ev.get("order_id")
        t0 = time.time()
        # Race-condition guard: order mógł zostać anulowany / dostarczony / już
        # przypisany między emit NEW_ORDER a teraz. Jeśli state_machine zna
        # aktualny stan i jest terminalny — skip bez tworzenia propozycji.
        cur = state_all.get(str(oid)) if oid is not None else None
        if cur and cur.get("status") in TERMINAL:
            _log.info(f"SKIP {oid}: status={cur.get('status')} (race guard)")
            event_bus.mark_processed(eid)
            stats["skipped"] += 1
            continue
        try:
            payload = ev.get("payload") or {}
            # Geocode missing coords on-the-fly (city z payloadu — NEW_ORDER event)
            if not payload.get("pickup_coords"):
                addr = payload.get("pickup_address", "")
                p_city = payload.get("pickup_city")
                coords = geocode(addr, city=p_city) if addr else None
                if coords:
                    payload["pickup_coords"] = list(coords)
                    ev["payload"] = payload
                    _log.info(f"geocoded pickup {oid}: {addr} / city={p_city} -> {coords}")
                else:
                    _log.warning(f"skip {eid}: missing pickup_coords (order={oid} city={p_city!r})")
                    event_bus.mark_processed(eid)
                    stats["skipped"] += 1
                    continue
            if not payload.get("delivery_coords"):
                addr = payload.get("delivery_address", "")
                d_city = payload.get("delivery_city")
                coords = geocode(addr, city=d_city) if addr else None
                if coords:
                    payload["delivery_coords"] = list(coords)
                    ev["payload"] = payload
                    _log.info(f"geocoded delivery {oid}: {addr} / city={d_city} -> {coords}")
                else:
                    _log.warning(f"skip {eid}: missing delivery_coords (order={oid} city={d_city!r})")
                    event_bus.mark_processed(eid)
                    stats["skipped"] += 1
                    continue

            result = process_event(ev, fleet, meta)
            latency_ms = (time.time() - t0) * 1000.0
            record = _serialize_result(result, eid, latency_ms)
            _append_decision(shadow_log_path, record)
            event_bus.mark_processed(eid)
            stats["processed"] += 1
            _log.info(
                f"SHADOW {oid} → {result.verdict} "
                f"best={record['best']['courier_id'] if record['best'] else None} "
                f"latency={record['latency_ms']}ms"
            )
        except Exception as e:
            stats["failed"] += 1
            _log.error(f"process_event fail {eid}: {e}\n{traceback.format_exc()}")
            event_bus.mark_failed(eid, str(e))
    return stats


def run() -> int:
    signal.signal(signal.SIGTERM, _sigterm_handler)
    signal.signal(signal.SIGINT, _sigterm_handler)

    cfg = load_config()
    shadow_log_path = cfg["paths"]["shadow_log"]
    meta_path = cfg["paths"]["restaurant_meta"]
    meta = _load_restaurant_meta(meta_path)

    _log.info(
        f"shadow_dispatcher START poll={POLL_INTERVAL_SEC}s "
        f"log={shadow_log_path} meta_n={len((meta or {}).get('restaurants', {}))}"
    )

    totals = {"processed": 0, "failed": 0, "skipped": 0}
    last_heartbeat = time.time()

    while not _shutdown:
        try:
            tick_stats = _tick(shadow_log_path, meta)
            for k, v in tick_stats.items():
                totals[k] += v
        except Exception as e:
            _log.error(f"tick loop error: {e}\n{traceback.format_exc()}")

        if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL_SEC:
            eb = event_bus.stats()
            _log.info(
                f"HEARTBEAT totals={totals} "
                f"event_bus=pending:{eb['pending']}/processed:{eb['processed']}/failed:{eb['failed']}"
            )
            last_heartbeat = time.time()

        # Sleep in short slices so shutdown signal is responsive
        for _ in range(POLL_INTERVAL_SEC * 2):
            if _shutdown:
                break
            time.sleep(0.5)

    _log.info(f"shadow_dispatcher STOP totals={totals}")
    return 0


if __name__ == "__main__":
    sys.exit(run())
