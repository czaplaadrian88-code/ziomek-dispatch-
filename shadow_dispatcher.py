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


# H1 (2026-04-25): auto-propagation of prefixed metrics keys.
# Pipeline regularly adds nowe v325_/v326_ keys do `metrics` ale serializer
# trzymał hardcoded explicit list — 14+ kluczy droppowane do learning_log
# (cross-review B#H1). Loop po prefixach zapewnia że *_reject_reason,
# *_speed_*, *_fleet_*, etc. trafia do logu bez ręcznego dodawania pole-po-polu.
_AUTO_PROP_PREFIXES = ("v325_", "v326_", "v3273_", "v3274_", "v319_", "r07_", "bonus_", "rule_")


def _propagate_prefixed_metrics(base: dict, metrics) -> None:
    if not metrics:
        return
    for k, v in metrics.items():
        if k in base:
            continue
        if any(k.startswith(p) for p in _AUTO_PROP_PREFIXES):
            base[k] = v


def _serialize_candidate(c) -> dict:
    plan = c.plan
    m = c.metrics or {}
    out = {
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
        # V3.27.1 A/B comparison (LOCATION A — alts): legacy zawsze, v327 = 0 gdy flag=False
        "bonus_r9_wait_pen_legacy": m.get("bonus_r9_wait_pen_legacy"),
        "bonus_r9_wait_pen_v327": m.get("bonus_r9_wait_pen_v327"),
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
        # V3.19e Opcja B R1' observability (2026-04-20): None gdy pos != last_assigned_pickup.
        # Pole dodane do enriched_metrics w dispatch_pipeline, MUSI być propagowane
        # przez serializer żeby trafiło do learning_log. (Serializer gap V3.19e step 4.)
        "v319e_r1_prime_hypothetical": m.get("v319e_r1_prime_hypothetical"),
        # V3.19f: czas_kuriera 2-pole — ISO Warsaw + raw HH:MM. Serializujemy OBA
        # dla offline diagnostyki rozjazdu (sanity check w state_machine).
        "czas_kuriera_warsaw": m.get("czas_kuriera_warsaw"),
        "czas_kuriera_hhmm": m.get("czas_kuriera_hhmm"),
        # V3.19h BUG-4: tier × pora cap matrix tracking (soft penalty).
        "v319h_bug4_tier_cap_used": m.get("v319h_bug4_tier_cap_used"),
        "v319h_bug4_cap_violation": m.get("v319h_bug4_cap_violation"),
        "bonus_bug4_cap_soft": m.get("bonus_bug4_cap_soft"),
        # V3.19h BUG-1: SR bundle × drop_proximity_factor.
        "v319h_bug1_drop_proximity_factor": m.get("v319h_bug1_drop_proximity_factor"),
        "v319h_bug1_sr_bundle_adjusted": m.get("v319h_bug1_sr_bundle_adjusted"),
        # V3.19h BUG-2: wave continuation bonus tracking.
        "v319h_bug2_interleave_gap_min": m.get("v319h_bug2_interleave_gap_min"),
        "v319h_bug2_continuation_bonus": m.get("v319h_bug2_continuation_bonus"),
        # V3.19g1: czas_kuriera change detection + kid diagnostic (LOCATION A).
        "v319g_ck_changed": m.get("v319g_ck_changed"),
        "v319g_ck_old": m.get("v319g_ck_old"),
        "v319g_ck_new": m.get("v319g_ck_new"),
        "v319g_ck_delta_min": m.get("v319g_ck_delta_min"),
        "v319g_ck_change_count": m.get("v319g_ck_change_count"),
        "v319g_kid_state": m.get("v319g_kid_state"),
        "v319g_kid_panel": m.get("v319g_kid_panel"),
        "v319g_kid_mismatch": m.get("v319g_kid_mismatch"),
        # V3.24-A: schedule integration (extension penalty + pre_shift clamp +
        # post-shift dropoff check). 5 fields — flaga ENABLE_V324A_* gateuje
        # wartości None gdy off. Serializowane zawsze (LOCATION A).
        "v324a_extension_min": m.get("v324a_extension_min"),
        "v324a_extension_penalty": m.get("v324a_extension_penalty"),
        "v324a_pickup_clamped_to_shift_start": m.get("v324a_pickup_clamped_to_shift_start"),
        "v324a_planned_dropoff_iso": m.get("v324a_planned_dropoff_iso"),
        "v324a_dropoff_excess_min": m.get("v324a_dropoff_excess_min"),
        # V3.26 STEP 1 (R-11): transparency rationale (LOCATION A — alts).
        "v326_rationale": m.get("v326_rationale"),
        # V3.26 STEP 5 (R-06): multi-stop trajectory (LOCATION A — alts).
        "v326_r06_relation": m.get("v326_r06_relation"),
        "v326_r06_bonus": m.get("v326_r06_bonus"),
        "v326_r06_drop_district": m.get("v326_r06_drop_district"),
        "v326_r06_pickup_district": m.get("v326_r06_pickup_district"),
        "v326_r06_detail": m.get("v326_r06_detail"),
        "v326_r06_skip_reason": m.get("v326_r06_skip_reason"),
        # V3.26 STEP 6 (R-07 v2): chain-ETA engine (LOCATION A — alts). Shadow ALWAYS.
        "r07_chain_eta_min": m.get("r07_chain_eta_min"),
        "r07_starting_point": m.get("r07_starting_point"),
        "r07_chain_details": m.get("r07_chain_details"),
        "r07_delta_vs_naive_min": m.get("r07_delta_vs_naive_min"),
        "r07_chain_truncated_count": m.get("r07_chain_truncated_count"),
        "r07_chain_warnings": m.get("r07_chain_warnings"),
        "r07_compute_latency_ms": m.get("r07_compute_latency_ms"),
    }
    _propagate_prefixed_metrics(out, m)
    return out


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

    out = {
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
            # V3.27.1 A/B comparison (LOCATION B — best): legacy zawsze, v327 = 0 gdy flag=False
            "bonus_r9_wait_pen_legacy": best_m.get("bonus_r9_wait_pen_legacy"),
            "bonus_r9_wait_pen_v327": best_m.get("bonus_r9_wait_pen_v327"),
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
            # V3.19e Opcja B R1' observability (2026-04-20) — patrz _serialize_candidate.
            "v319e_r1_prime_hypothetical": best_m.get("v319e_r1_prime_hypothetical"),
            # V3.19f: czas_kuriera OBA pola (ISO + raw HH:MM) — patrz _serialize_candidate.
            "czas_kuriera_warsaw": best_m.get("czas_kuriera_warsaw"),
            "czas_kuriera_hhmm": best_m.get("czas_kuriera_hhmm"),
            # V3.19h BUG-4: tier × pora cap matrix tracking — patrz _serialize_candidate.
            "v319h_bug4_tier_cap_used": best_m.get("v319h_bug4_tier_cap_used"),
            "v319h_bug4_cap_violation": best_m.get("v319h_bug4_cap_violation"),
            "bonus_bug4_cap_soft": best_m.get("bonus_bug4_cap_soft"),
            # V3.19h BUG-1: SR bundle × drop_proximity_factor — patrz _serialize_candidate.
            "v319h_bug1_drop_proximity_factor": best_m.get("v319h_bug1_drop_proximity_factor"),
            "v319h_bug1_sr_bundle_adjusted": best_m.get("v319h_bug1_sr_bundle_adjusted"),
            # V3.19h BUG-2: wave continuation — patrz _serialize_candidate.
            "v319h_bug2_interleave_gap_min": best_m.get("v319h_bug2_interleave_gap_min"),
            "v319h_bug2_continuation_bonus": best_m.get("v319h_bug2_continuation_bonus"),
            # V3.19g1: czas_kuriera change detection + kid diagnostic (LOCATION B).
            "v319g_ck_changed": best_m.get("v319g_ck_changed"),
            "v319g_ck_old": best_m.get("v319g_ck_old"),
            "v319g_ck_new": best_m.get("v319g_ck_new"),
            "v319g_ck_delta_min": best_m.get("v319g_ck_delta_min"),
            "v319g_ck_change_count": best_m.get("v319g_ck_change_count"),
            "v319g_kid_state": best_m.get("v319g_kid_state"),
            "v319g_kid_panel": best_m.get("v319g_kid_panel"),
            "v319g_kid_mismatch": best_m.get("v319g_kid_mismatch"),
            # V3.24-A: schedule integration (LOCATION B) — patrz _serialize_candidate.
            "v324a_extension_min": best_m.get("v324a_extension_min"),
            "v324a_extension_penalty": best_m.get("v324a_extension_penalty"),
            "v324a_pickup_clamped_to_shift_start": best_m.get("v324a_pickup_clamped_to_shift_start"),
            "v324a_planned_dropoff_iso": best_m.get("v324a_planned_dropoff_iso"),
            "v324a_dropoff_excess_min": best_m.get("v324a_dropoff_excess_min"),
            # V3.26 STEP 1 (R-11): transparency rationale (LOCATION B — best).
            "v326_rationale": best_m.get("v326_rationale"),
            # V3.26 STEP 5 (R-06): multi-stop trajectory (LOCATION B — best).
            "v326_r06_relation": best_m.get("v326_r06_relation"),
            "v326_r06_bonus": best_m.get("v326_r06_bonus"),
            "v326_r06_drop_district": best_m.get("v326_r06_drop_district"),
            "v326_r06_pickup_district": best_m.get("v326_r06_pickup_district"),
            "v326_r06_detail": best_m.get("v326_r06_detail"),
            "v326_r06_skip_reason": best_m.get("v326_r06_skip_reason"),
            # V3.26 STEP 6 (R-07 v2): chain-ETA engine (LOCATION B — best). Shadow ALWAYS.
            "r07_chain_eta_min": best_m.get("r07_chain_eta_min"),
            "r07_starting_point": best_m.get("r07_starting_point"),
            "r07_chain_details": best_m.get("r07_chain_details"),
            "r07_delta_vs_naive_min": best_m.get("r07_delta_vs_naive_min"),
            "r07_chain_truncated_count": best_m.get("r07_chain_truncated_count"),
            "r07_chain_warnings": best_m.get("r07_chain_warnings"),
            "r07_compute_latency_ms": best_m.get("r07_compute_latency_ms"),
        },
        "alternatives": [
            _serialize_candidate(c) for c in result.candidates[1:]
        ],
        "pickup_ready_at": (
            result.pickup_ready_at.isoformat()
            if result.pickup_ready_at else None
        ),
        "latency_ms": round(latency_ms, 1),
        # Sprint-1 2026-04-30 (logging extension): pool size scalars dla
        # counterfactual pairwise analysis. pool_total = pre-feasibility,
        # pool_feasible = post-feasibility (MAYBE) candidates count.
        # Defensive getattr — fallback do None gdy starsza struktura
        # PipelineResult bez tych pól (dla replay zaszłych eventów).
        "pool_total_count": getattr(result, "pool_total_count", None),
        "pool_feasible_count": getattr(result, "pool_feasible_count", None),
    }
    if out["best"] is not None:
        _propagate_prefixed_metrics(out["best"], best_m)
    return out


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
        # V3.19f: czas_kuriera passthrough z payload do order_event dla
        # dispatch_pipeline consumer (pod flagą ENABLE_CZAS_KURIERA_PROPAGATION).
        "czas_kuriera_warsaw": payload.get("czas_kuriera_warsaw"),
        "czas_kuriera_hhmm": payload.get("czas_kuriera_hhmm"),
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

    # V3.27 Phase 1F (2026-04-25 wieczór): warm-up ortools import na startup.
    # D2 verified pierwszy thread cold import 153.5ms — eliminujemy z ścieżki
    # critical pierwszego proposal po restart. Idempotent, no-op gdy already
    # imported. Try/except defensive — jeśli ortools absent (test env), skip
    # bez fail (run-time imports w tsp_solver wciąż handle).
    try:
        _wu_t0 = time.perf_counter()
        from ortools.constraint_solver import pywrapcp as _wu_pywrapcp  # noqa: F401
        from ortools.constraint_solver import routing_enums_pb2 as _wu_enums  # noqa: F401
        _wu_ms = (time.perf_counter() - _wu_t0) * 1000.0
        _log.info(f"V3.27 ortools warm-up complete: {_wu_ms:.1f}ms")
    except Exception as _wu_e:
        _log.warning(
            f"V3.27 ortools warm-up skipped ({type(_wu_e).__name__}: {_wu_e}) — "
            f"runtime import w tsp_solver still active"
        )

    # V3.27.1 sesja 4 (2026-04-27): pre-warm panel_client login na startup.
    # Lekcja #29: pre_proposal_recheck (V3.27.1 sesja 3) używa panel_client.fetch_order_details
    # synchronicznie w dispatch_pipeline → CSRF login refresh (~6-7s blocking)
    # propagates do proposal latency. Pre-warm eliminates first-proposal cold
    # login penalty. Login refresh co 22 min nadal trafi proposal (oczekiwane
    # 3-6% rate w peak — V3.28 background refresh thread to pełen fix).
    # Defensive try/except — jeśli panel unreachable, skip bez fail (proposal
    # path lazy fetch handle).
    try:
        from dispatch_v2 import panel_client as _wu_panel
        _wu_login_t0 = time.perf_counter()
        _wu_panel.login(force=True)
        _wu_login_ms = (time.perf_counter() - _wu_login_t0) * 1000.0
        _log.info(f"V3.27.1 sesja 4 panel_client pre-warm login complete: {_wu_login_ms:.1f}ms")
        # V3.27.7 TECH_DEBT #20: spawn bg refresh thread post pre-warm
        _wu_panel.start_bg_refresh()
        _log.info("V3.27.7 panel_bg_refresh thread started post pre-warm")
    except Exception as _wu_login_e:
        _log.warning(
            f"V3.27.1 sesja 4 panel_client pre-warm login skipped "
            f"({type(_wu_login_e).__name__}: {_wu_login_e}) — "
            f"first-proposal lazy login still active"
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
