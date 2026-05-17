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

from dispatch_v2 import common as C, event_bus, state_machine
from dispatch_v2.common import load_config, now_iso, setup_logger
from dispatch_v2.core.broadcast_handlers import dispatch_config_reload
from dispatch_v2.core.config_reload_subscriber import BroadcastSubscriber
from dispatch_v2.courier_resolver import build_fleet_snapshot, dispatchable_fleet
from dispatch_v2.dispatch_pipeline import assess_order, PipelineResult
from dispatch_v2.monitoring.consumer_stuck_alert import (
    StuckAlertConfig,
    StuckAlertState,
    append_evaluation_log,
    compute_heartbeat,
    evaluate_stuck_alert,
    render_telegram_message,
)


POLL_INTERVAL_SEC = 5
HEARTBEAT_INTERVAL_SEC = 60
POLL_BATCH_SIZE = 50

_log = setup_logger(
    "shadow_dispatcher",
    "/root/.openclaw/workspace/scripts/logs/shadow.log",
)
# V3.28 (2026-05-09) — observability gap fix (FAZA 0 finding):
# route_simulator_v2 logger nie miał handlera w shadow_dispatcher process,
# więc V3274_OR_TOOLS_VIOLATION + V3274_TIMEWINDOW_FALLBACK + V3274_RENDER_DIVERGENCE
# warnings z shadow path były lost (nie propagowane do file). Czasówka path
# je łapał (handler na czasowka_scheduler logger), shadow path NIE.
# Fix: explicit setup dla route_simulator_v2 logger w shadow_dispatcher entry point.
_route_simulator_log = setup_logger(
    "route_simulator_v2",
    "/root/.openclaw/workspace/scripts/logs/route_simulator.log",
)
_telegram_approver_log = setup_logger(
    "telegram_approver",
    "/root/.openclaw/workspace/scripts/logs/telegram_approver.log",
)
_shutdown = False


def _sigterm_handler(signum, frame):
    global _shutdown
    _log.info(f"signal {signum} received → graceful shutdown")
    _shutdown = True


# V3.28 R-04 v2.0: lazy mtime cache dla tier_suggestions.json (5-min TTL).
# Phase 1 SHADOW: serializes r04 fields to decision_record bez behavior change.
_R04_TIER_SUGGESTIONS_PATH = "/root/.openclaw/workspace/dispatch_state/tier_suggestions.json"
_R04_CACHE: Dict[str, object] = {"mtime": 0.0, "checked_at": 0.0, "data": {}}
_R04_CACHE_TTL_SEC = 300


def _load_r04_suggestions() -> Dict[str, dict]:
    """Lazy-load tier_suggestions.json z mtime + TTL cache. Fail-open na missing/parse error."""
    try:
        from dispatch_v2 import common as C
        if not getattr(C, "ENABLE_R04_SHADOW", False):
            return {}
    except Exception:
        return {}
    now_ts = time.time()
    try:
        if now_ts - _R04_CACHE["checked_at"] < _R04_CACHE_TTL_SEC:
            return _R04_CACHE["data"]
        st = os.stat(_R04_TIER_SUGGESTIONS_PATH)
        _R04_CACHE["checked_at"] = now_ts
        if st.st_mtime == _R04_CACHE["mtime"]:
            return _R04_CACHE["data"]
        with open(_R04_TIER_SUGGESTIONS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        # Strip _meta key — keep cid → suggestion dict
        data = {k: v for k, v in data.items() if k != "_meta"}
        _R04_CACHE["mtime"] = st.st_mtime
        _R04_CACHE["data"] = data
        return data
    except FileNotFoundError:
        _R04_CACHE["data"] = {}
        return {}
    except Exception as e:
        _log.warning(f"_load_r04_suggestions fail: {e}")
        return _R04_CACHE.get("data", {})


def _r04_field_for_cid(cid: Optional[str]) -> Optional[dict]:
    """Returns compact r04 field dla decision_record. None gdy brak suggestion albo flag OFF."""
    if not cid:
        return None
    s = _load_r04_suggestions().get(str(cid))
    if not s:
        return None
    return {
        "current_tier": s.get("current_tier"),
        "suggested_tier": s.get("suggested_tier"),
        "tier_match": s.get("tier_match"),
        "gold_candidate": s.get("gold_candidate"),
        "insufficient_data": s.get("insufficient_data"),
        "evaluated_at": s.get("evaluated_at"),
        "schema_version": s.get("schema_version"),
    }


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
_AUTO_PROP_PREFIXES = ("v325_", "v326_", "v3273_", "v3274_", "v319_", "r07_", "bonus_", "rule_", "intra_",
                       "dwell_", "drive_speed_",  # 2026-05-17: tier-aware DWELL + drive-speed metryki (#109)
                       "objm_")  # sprint OBJ F0.3: metryki jakości planu (idle/thermal/r6_breach/span)


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
        # Fix 2026-05-17 (#474227): r6_bag_size jest null gdy feasibility_v2
        # robi early-return PRZED blokiem R6 (np. bramka sla_violation:538).
        # bag_size_before (feasibility_v2:276) ustawiane bezwarunkowo = len(bag).
        # Bez propagacji telegram render fallback chain go nie dostaje →
        # kurier z pełną torbą renderuje się jako "🟢 0 / Wolny od ręki".
        "bag_size_before": m.get("bag_size_before"),
        # V3.28 ANCHOR FIX 2026-05-10 — per-order thermal violations (anchor=ready_at)
        "r6_per_order_violations": m.get("r6_per_order_violations"),
        "r6_picked_up_violations": m.get("r6_picked_up_violations"),
        # V3.28 P1 — R1 directionality + R5 pickup detour (Adrian doktryna 2026-05-10)
        "r1_avg_pairwise_cosine": m.get("r1_avg_pairwise_cosine"),
        "r5_pickup_detour_total_km": m.get("r5_pickup_detour_total_km"),
        "r5_pickup_detour_per_order_km": m.get("r5_pickup_detour_per_order_km"),
        "bonus_r1_corridor": m.get("bonus_r1_corridor"),
        "bonus_r5_detour": m.get("bonus_r5_detour"),
        # V3.28 P3-D4 (2026-05-11): R6 picked_up delta-based reject — heurystyka czy
        # nowy order CAUSES carry time increase dla picked_up violation (Boboli 44 min
        # case 10.05). True gdy reject path active. False default. Audit 11.05 ujawnił
        # że bez serializer propagation 7-day FAZA 3 decision tree window blind.
        "r6_picked_up_delta_reject": m.get("r6_picked_up_delta_reject"),
        # V3.28 P3-D5 (2026-05-11): R1 corridor deliv_spread mnożnik (1.0 baseline,
        # linear scale 8km→16+km cap 2.0x). Tylko negatywny bonus multiplied. Visibility
        # dla future R1 calibration sprintów + LGBM feature engineering.
        "r1_corridor_spread_mult": m.get("r1_corridor_spread_mult"),
        # V3.28 P2 — wave detection (Adrian doktryna 2026-05-10)
        "n_waves": m.get("n_waves"),
        "inter_wave_deadhead_total_km": m.get("inter_wave_deadhead_total_km"),
        "inter_wave_deadhead_max_km": m.get("inter_wave_deadhead_max_km"),
        "inter_wave_n_segments": m.get("inter_wave_n_segments"),
        "bonus_wave_clean": m.get("bonus_wave_clean"),
        "bonus_inter_wave_deadhead": m.get("bonus_inter_wave_deadhead"),
        # V3.28 P3 (B) — state-vs-panel mismatch (Adrian doktryna 2026-05-10)
        "panel_packs_signal_size": m.get("panel_packs_signal_size"),
        "panel_packs_cache_age_s": m.get("panel_packs_cache_age_s"),
        "bonus_state_panel_mismatch": m.get("bonus_state_panel_mismatch"),
        # V3.28 P4 — coordinator hybrid duty (Adrian doktryna 2026-05-10 wieczór)
        "is_coordinator": m.get("is_coordinator"),
        "coordinator_active": m.get("coordinator_active"),
        "bonus_coordinator_idle": m.get("bonus_coordinator_idle"),
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
        # V3.28 FIX_C: bundle deliv_spread cap (LOCATION A).
        "fix_c_applied": m.get("fix_c_applied"),
        "fix_c_deliv_spread_km": m.get("fix_c_deliv_spread_km"),
        "fix_c_cap_km": m.get("fix_c_cap_km"),
        # V3.28 R-04 v2.0: tier suggestion (LOCATION A) — Phase 1 SHADOW only.
        "r04": _r04_field_for_cid(str(m.get("courier_id") or "")),
        # V3.28 Faza 6 LGBM shadow (LOCATION A) — parallel BC ranker prediction.
        "lgbm_shadow": m.get("lgbm_shadow"),
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
        # Faza 7-AUTO-PROXIMITY (2026-05-06) — auto-route classification + telemetry.
        # Caller (dispatch_pipeline) populated auto_route per spec
        # eod_drafts/2026-05-06/faza_7_auto_proximity_design_spec.md sekcja 3.3.
        "auto_route": getattr(result, "auto_route", "ACK"),
        "auto_route_reason": getattr(result, "auto_route_reason", ""),
        "auto_route_context": getattr(result, "auto_route_context", {}) or {},
        # MP-#13 (2026-05-08): L3 caller propagation. degraded_osrm True gdy
        # osrm_client.is_degraded() przy entry do assess_order. Telegram_approver
        # format_proposal może hint'ować "⚠ OSRM degraded" gdy True. Snapshots
        # cache_age + degraded_since_ts dla post-mortem.
        "decision_meta": {
            "degraded_osrm": bool(getattr(result, "degraded_osrm", False)),
            "osrm_cache_age_s": getattr(result, "osrm_cache_age_s", None),
            "osrm_degraded_since_ts": getattr(result, "osrm_degraded_since_ts", None),
        },
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
            # Fix 2026-05-17 (#474227): bag_size_before propagation (LOCATION B
            # — best). Patrz _serialize_candidate dla detali. r6_bag_size null
            # przy feasibility early-return przed R6 → fallback do bag_size_before.
            "bag_size_before": best_m.get("bag_size_before"),
            # V3.28 ANCHOR FIX 2026-05-10 — per-order thermal violations (anchor=ready_at)
            "r6_per_order_violations": best_m.get("r6_per_order_violations"),
            "r6_picked_up_violations": best_m.get("r6_picked_up_violations"),
            # V3.28 P1 — R1 directionality + R5 pickup detour (Adrian doktryna 2026-05-10)
            "r1_avg_pairwise_cosine": best_m.get("r1_avg_pairwise_cosine"),
            "r5_pickup_detour_total_km": best_m.get("r5_pickup_detour_total_km"),
            "r5_pickup_detour_per_order_km": best_m.get("r5_pickup_detour_per_order_km"),
            "bonus_r1_corridor": best_m.get("bonus_r1_corridor"),
            "bonus_r5_detour": best_m.get("bonus_r5_detour"),
            # V3.28 P3-D4 (2026-05-11): R6 picked_up delta reject (LOCATION B — best)
            # Patrz _serialize_candidate dla detali; sprint #32 obs serializer fix.
            "r6_picked_up_delta_reject": best_m.get("r6_picked_up_delta_reject"),
            # V3.28 P3-D5 (2026-05-11): R1 corridor spread mult (LOCATION B — best)
            # Patrz _serialize_candidate dla detali; sprint #32 obs serializer fix.
            "r1_corridor_spread_mult": best_m.get("r1_corridor_spread_mult"),
            # V3.28 P2 — wave detection (Adrian doktryna 2026-05-10)
            "n_waves": best_m.get("n_waves"),
            "inter_wave_deadhead_total_km": best_m.get("inter_wave_deadhead_total_km"),
            "inter_wave_deadhead_max_km": best_m.get("inter_wave_deadhead_max_km"),
            "inter_wave_n_segments": best_m.get("inter_wave_n_segments"),
            "bonus_wave_clean": best_m.get("bonus_wave_clean"),
            "bonus_inter_wave_deadhead": best_m.get("bonus_inter_wave_deadhead"),
            # V3.28 P3 (B) — state-vs-panel mismatch (Adrian doktryna 2026-05-10)
            "panel_packs_signal_size": best_m.get("panel_packs_signal_size"),
            "panel_packs_cache_age_s": best_m.get("panel_packs_cache_age_s"),
            "bonus_state_panel_mismatch": best_m.get("bonus_state_panel_mismatch"),
            # V3.28 P4 — coordinator hybrid duty (Adrian doktryna 2026-05-10 wieczór)
            "is_coordinator": best_m.get("is_coordinator"),
            "coordinator_active": best_m.get("coordinator_active"),
            "bonus_coordinator_idle": best_m.get("bonus_coordinator_idle"),
            # V3.28 ETAP 2: effective_start_at = shift_start gdy pre_shift clamp
            # odpalił, inaczej None. Telegram _route_lines_v2 użyje go zamiast
            # real now dla "start" line w trasie. pre_shift_clamp_applied flag
            # dla shadow log audit + downstream consumers.
            "effective_start_at": best_m.get("earliest_departure_utc"),
            "pre_shift_clamp_applied": bool(best_m.get("pre_shift_clamp_applied")),
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
            # V3.28 FIX_C: bundle deliv_spread cap (LOCATION B).
            "fix_c_applied": best_m.get("fix_c_applied"),
            "fix_c_deliv_spread_km": best_m.get("fix_c_deliv_spread_km"),
            "fix_c_cap_km": best_m.get("fix_c_cap_km"),
            # V3.28 R-04 v2.0: tier suggestion (LOCATION B) — Phase 1 SHADOW only.
            "r04": _r04_field_for_cid(str(best_m.get("courier_id") or "")),
            # V3.28 Faza 6 LGBM shadow (LOCATION B) — best courier z metrics.
            "lgbm_shadow": best_m.get("lgbm_shadow"),
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
    """MP-#11 (2026-05-08): atomic JSONL append via core helper.

    Wcześniej `with open(path, 'a')` bez lock — race przy konkurencyjnych
    write'ach z innych procesów. shadow_decisions.jsonl jest single-writer
    (tylko shadow_dispatcher) ale pattern unifikowany cross-codebase
    (eliminuje cargo-cult `open('a')` przy kolejnym dodaniu writer'a).
    """
    from dispatch_v2.core.jsonl_appender import append_jsonl
    append_jsonl(path, record)


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
            # Propagate raw restaurant pickup time (pre-extension) — telegram_approver
            # liczy `pickup_extension_min = pickup_ready_at - pickup_at_warsaw` aby
            # pokazać "(+N min)" gdy Ziomek przedłużył deklarację restauracji.
            record["pickup_at_warsaw"] = payload.get("pickup_at_warsaw")
            # Etap 1 pickup-label (2026-05-08): order_created_at = moment złożenia
            # zamówienia (panel created_at, UTC). Telegram pokazuje "(N min od
            # złożenia)" w linii Odbiór. Fallback w telegram_approver gdy None.
            record["order_created_at"] = payload.get("created_at_utc")
            # Compute mins_since_creation (best ETA pickup vs created_at) raz tutaj
            # gdy oba są dostępne — telegram tylko renderuje, brak parsing TZ tam.
            try:
                _best = record.get("best") or {}
                _created_iso = record.get("order_created_at")
                _eta_iso = (result.best.metrics.get("eta_pickup_utc")
                            if result.best is not None else None)
                if _created_iso and _eta_iso:
                    from datetime import datetime as _dt, timezone as _tz
                    _c = _dt.fromisoformat(_created_iso.replace("Z", "+00:00"))
                    _e = _dt.fromisoformat(_eta_iso.replace("Z", "+00:00"))
                    if _c.tzinfo is None: _c = _c.replace(tzinfo=_tz.utc)
                    if _e.tzinfo is None: _e = _e.replace(tzinfo=_tz.utc)
                    _delta = (_e - _c).total_seconds() / 60.0
                    if _delta < 0: _delta = 0.0
                    _best["mins_since_creation"] = int(round(_delta))
                    record["best"] = _best
            except Exception as _ex:
                _log.debug(f"mins_since_creation compute failed: {_ex}")

            # Adrian decision 2026-05-07: suppress Telegram proposals for firmowe
            # konto Nadajesz.pl (address_id=161). Adrian zarządza firmowymi przez
            # panel — Telegram noise. Pre-write filter w shadow (eliminuje noise
            # w shadow_decisions.jsonl + telegram_approver consumer naturalnie
            # filtruje verdict=PROPOSE only). Zero telegram restart needed.
            # Hot-reload: ENABLE_FIRMOWE_KONTO_TELEGRAM_PROPOSALS=true odwraca.
            _aid = (ev.get("payload") or {}).get("address_id")
            try:
                _aid_int = int(_aid) if _aid is not None else None
            except (TypeError, ValueError):
                _aid_int = None
            record["address_id"] = _aid  # audit trail in shadow log
            if (record.get("verdict") == "PROPOSE"
                    and _aid_int is not None
                    and _aid_int in C.FIRMOWE_KONTO_ADDRESS_IDS
                    and not C.flag("ENABLE_FIRMOWE_KONTO_TELEGRAM_PROPOSALS", False)):
                _log.info(
                    f"SHADOW {oid} firmowe-konto aid={_aid}: PROPOSE suppressed "
                    f"(flag ENABLE_FIRMOWE_KONTO_TELEGRAM_PROPOSALS=false). "
                    f"verdict→SUPPRESSED_FIRMOWE_KONTO"
                )
                record["verdict"] = "SUPPRESSED_FIRMOWE_KONTO"
                record["reason"] = (
                    (record.get("reason") or "PROPOSE")
                    + " | telegram_suppressed_firmowe_konto"
                )

            _append_decision(shadow_log_path, record)
            event_bus.mark_processed(eid)
            stats["processed"] += 1
            _log.info(
                f"SHADOW {oid} → {record.get('verdict', result.verdict)} "
                f"best={record['best']['courier_id'] if record['best'] else None} "
                f"latency={record['latency_ms']}ms"
            )
        except Exception as e:
            stats["failed"] += 1
            _log.error(f"process_event fail {eid}: {e}\n{traceback.format_exc()}")
            event_bus.mark_failed(eid, str(e))
    return stats


# V3.28 Fix 3 (incident 03.05.2026): worker liveness thresholds.
# Module-level dla testowalności + env override.
import os as _os_v328
V328_WORKER_STUCK_AGE_SEC = int(_os_v328.environ.get("V328_WORKER_STUCK_AGE_SEC", "300"))
V328_WORKER_STUCK_PENDING_THRESHOLD = int(_os_v328.environ.get("V328_WORKER_STUCK_PENDING_THRESHOLD", "100"))
# V3.28 #35 (2026-05-11 wieczór): hysteresis + sustain + re-alert (long-term fix).
# Incydent ~18:30 Warsaw: alert "STUCK age=310s pending=191" co ~10 min spam dla
# Adriana. Root cause: pre-#35 latch resetował się gdy is_stuck=False (single
# successful process_event flipuje age→0 → latch reset → re-fire 5-10 min później
# gdy age znów przekroczy 300s, mimo że pending=191 wciąż wisi). Worker pod peak
# load przetwarza wolno (1 event/N min), age oscyluje wokół threshold → spam.
# Meta-class Lekcja #109/#110/#111 ("alert technically firing ALE doesn't
# communicate truth"). Pre-#35 mieszał dwie różne klasy failure: WORKER_FROZEN
# (process martwy) vs BACKLOG_OVERLOAD (worker żywy ale przeciążony). Recovery
# semantics dla obu różne → trzeba hysteresis + sustain.
V328_WORKER_STUCK_PENDING_LOW_WATER = int(
    _os_v328.environ.get("V328_WORKER_STUCK_PENDING_LOW_WATER", "30")
)  # Pending musi spaść <= low_water żeby reset latch (hysteresis exit). 30 << 100 daje materialny gap.
V328_WORKER_STUCK_SUSTAIN_CYCLES = int(
    _os_v328.environ.get("V328_WORKER_STUCK_SUSTAIN_CYCLES", "2")
)  # N kolejnych heartbeatów (60s każdy) z is_stuck=True przed ENTER alert. Anti-flap.
V328_WORKER_STUCK_REALERT_INTERVAL_SEC = int(
    _os_v328.environ.get("V328_WORKER_STUCK_REALERT_INTERVAL_SEC", "1800")
)  # SUSTAINED re-alert co N sekund podczas sustained stuck (default 30 min). Reminder że problem trwa.

# Sprint #37 v2 (2026-05-13): per-consumer stuck alert config. event_types
# filtered TYLKO `["NEW_ORDER"]` (consumer attribution per Lekcja #113) —
# pre-#37 alert sygnał używał QUEUE_EVENT_TYPES (NEW_ORDER + COURIER_PICKED_UP
# + COURIER_DELIVERED) → backlog sla_tracker'a (PICKED_UP+DELIVERED) firował
# alert "shadow STUCK" mimo że shadow zdrowy (NEW_ORDER=0).
# Env override: STUCK_ALERT_SHADOW_AGE_SEC, _PENDING_THRESHOLD, _PENDING_LOW_WATER,
# _SUSTAIN_CYCLES, _REALERT_INTERVAL_SEC, _SHADOW_MODE_ONLY.
_SHADOW_STUCK_CONFIG = StuckAlertConfig.from_env(
    consumer_id="shadow",
    consumer_display_name="Ziomek shadow worker",
    event_types=frozenset(["NEW_ORDER"]),
    age_threshold_sec=V328_WORKER_STUCK_AGE_SEC,
    pending_threshold=V328_WORKER_STUCK_PENDING_THRESHOLD,
    pending_low_water=V328_WORKER_STUCK_PENDING_LOW_WATER,
    sustain_cycles=V328_WORKER_STUCK_SUSTAIN_CYCLES,
    realert_interval_sec=V328_WORKER_STUCK_REALERT_INTERVAL_SEC,
    shadow_mode_only=False,  # battle-tested via #33/#35 — emit Telegram by default
)


def _v328_compute_heartbeat_state(
    last_processed_ts: float,
    now: float,
    pending: int,
    pending_low_water: int = None,
) -> dict:
    """V3.28 Fix 3 + #35 helper: pure function compute heartbeat liveness state.

    Multi-signal stuck detection (Lekcja #66): age>threshold AND pending>threshold.
    Quiet period (low pending, no orders to process) → NOT stuck (worker idle).

    V3.28 #35 (2026-05-11): added `is_recovered` (hysteresis exit condition) =
    pending <= low_water. Recovery decoupled od is_stuck — pojedynczy process_event
    flipuje age=0 (is_stuck=False) ALE pending=191 trzyma się wciąż wysoko → NIE
    jest to recovery. Recovery musi widzieć drop kolejki, nie tylko 1 event.

    Returns dict z 4 polami:
    - age_sec: seconds od last successful process_event
    - worker_alive: True jeśli age < V328_WORKER_STUCK_AGE_SEC
    - is_stuck: True jeśli age > threshold AND pending > threshold (alert candidate)
    - is_recovered: True jeśli pending <= low_water (latch reset condition)
    """
    if pending_low_water is None:
        pending_low_water = V328_WORKER_STUCK_PENDING_LOW_WATER
    age_sec = max(0.0, now - last_processed_ts)
    worker_alive = age_sec < V328_WORKER_STUCK_AGE_SEC
    is_stuck = (
        age_sec > V328_WORKER_STUCK_AGE_SEC
        and pending > V328_WORKER_STUCK_PENDING_THRESHOLD
    )
    is_recovered = pending <= pending_low_water
    return {
        "age_sec": age_sec,
        "worker_alive": worker_alive,
        "is_stuck": is_stuck,
        "is_recovered": is_recovered,
    }


def _v328_should_emit_stuck_alert(
    is_stuck: bool,
    is_recovered: bool,
    alert_sent: bool,
    high_water_streak: int,
    last_alert_ts: float,
    now: float,
    sustain_cycles: int = None,
    realert_interval_sec: float = None,
) -> tuple:
    """V3.28 #33+#35 (2026-05-11): stuck alert state machine z hysteresis + sustain.

    Pre-#35 (Lekcja #112 root cause): single is_stuck=False flipował latch →
    re-fire spam. Fix: dwie ortogonalne osie:
      1. ENTER guard: sustain N kolejnych is_stuck=True cycles (anti-flap)
      2. RECOVERY guard: latch reset TYLKO gdy is_recovered (pending<=low_water),
         NIE na pojedyncze is_stuck=False (age dropped przez 1 event)
      3. SUSTAINED reminder: re-alert co realert_interval_sec gdy stuck trwa
         (operator visibility — problem nie znika)

    Returns 5-tuple: (emit, kind, new_alert_sent, new_streak, new_last_alert_ts)
      kind ∈ {'ENTER', 'SUSTAINED', 'RECOVERY', None}

    Cztery transitions:
    - ENTER: streak>=sustain AND not latched → emit ONCE, set latch, reset streak counter (kept for telemetry)
    - SUSTAINED: latched AND is_stuck AND elapsed>=interval → re-emit, update ts
    - RECOVERY: latched AND is_recovered (pending<=low_water) → emit recovery, reset latch+streak+ts
    - NO-OP: wszystkie inne; streak inc gdy is_stuck else reset

    Pure function — żadnego I/O ani globalnego stanu. Wszystkie wartości env-overrideable.
    """
    if sustain_cycles is None:
        sustain_cycles = V328_WORKER_STUCK_SUSTAIN_CYCLES
    if realert_interval_sec is None:
        realert_interval_sec = V328_WORKER_STUCK_REALERT_INTERVAL_SEC

    # Streak counter (consecutive is_stuck=True cycles); reset gdy is_stuck=False.
    new_streak = high_water_streak + 1 if is_stuck else 0

    # RECOVERY: latched + pending dropped poniżej low_water → recovery alert, reset all.
    if alert_sent and is_recovered:
        return (True, "RECOVERY", False, 0, 0.0)

    # ENTER: streak >= sustain cycles AND latch nie set → entry alert.
    if (not alert_sent) and new_streak >= sustain_cycles:
        return (True, "ENTER", True, new_streak, now)

    # SUSTAINED: latch set, wciąż stuck, re-alert interval upłynął → reminder.
    if alert_sent and is_stuck and (now - last_alert_ts) >= realert_interval_sec:
        return (True, "SUSTAINED", True, new_streak, now)

    # No-op transitions (healthy / sub-sustain / dedup-within-interval / single-event-flap).
    return (False, None, alert_sent, new_streak, last_alert_ts)


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

    # A4.1 (2026-05-09): BroadcastSubscriber dla CONFIG_RELOAD events.
    # Defense-in-depth: init fail NIE crash worker (subscriber=None →
    # poll skip w tick).
    _broadcast_sub: Optional[BroadcastSubscriber] = None
    try:
        _broadcast_sub = BroadcastSubscriber(
            consumer_id="shadow_dispatcher",
            state_path=Path(
                "/root/.openclaw/workspace/dispatch_state/event_subscribers/shadow.json"
            ),
        )
        _log.info("A4.1 BroadcastSubscriber init OK consumer=shadow_dispatcher")
    except Exception as _bs_e:
        _log.warning(
            f"A4.1 BroadcastSubscriber init fail "
            f"({type(_bs_e).__name__}: {_bs_e}) — broadcast disabled"
        )

    totals = {"processed": 0, "failed": 0, "skipped": 0}
    last_heartbeat = time.time()
    last_broadcast_poll = 0.0
    BROADCAST_POLL_INTERVAL_S = 30.0  # poll co 30s rate-limited
    # V3.28 Fix 3 (incident 03.05.2026): worker liveness tracking.
    # Pre-fix: HEARTBEAT loguje processed=N stagnate (NIE distinguish "worker
    # alive" vs "worker stuck"). Production incident 02.05 23:03 → 03.05 ~10:00:
    # processed=10675 → 10675 przez 10+ godzin (event_bus pending rosło 14025 →
    # 14052 ale processed nie ruszał). Detection failure.
    # Post-fix: track last_processed_ts, emit age_sec + worker_alive boolean,
    # log.critical V328_WORKER_STUCK gdy age>300s AND pending>100 (multi-signal
    # per Lekcja #66 — quiet period z low pending NIE jest stuck).
    last_processed_ts = time.time()
    # V3.28 #33+#35 + Sprint #37 v2 (2026-05-13): stuck alert state machine
    # zrefaktorowany do `monitoring/consumer_stuck_alert.py` (reusable abstraction
    # — sla_tracker dostanie własną instancję w Phase B). In-memory state,
    # restart-clean (sustain_cycles=2 zapobiega false-positive natychmiast po
    # restart). Legacy `_v328_*` thin wrappery zachowują contract dla 25 testów
    # backward-compat (do delete po Sprint #37+1 stable 7d).
    _shadow_stuck_state = StuckAlertState()

    while not _shutdown:
        try:
            tick_stats = _tick(shadow_log_path, meta)
            for k, v in tick_stats.items():
                totals[k] += v
            # V3.28 Fix 3: update last_processed_ts gdy tick miał >=1 successful processing
            if tick_stats.get("processed", 0) > 0:
                last_processed_ts = time.time()
        except Exception as e:
            _log.error(f"tick loop error: {e}\n{traceback.format_exc()}")

        # A4.1: poll CONFIG_RELOAD broadcast events co 30s rate-limited.
        # Defense-in-depth try/except — poll/handler fail NIE blocks tick.
        if _broadcast_sub is not None and time.time() - last_broadcast_poll >= BROADCAST_POLL_INTERVAL_S:
            try:
                _new_events = _broadcast_sub.poll(["CONFIG_RELOAD"], limit=50)
                if _new_events:
                    dispatch_config_reload(_new_events, "shadow_dispatcher")
            except Exception as _bp_e:
                _log.warning(
                    f"A4.1 broadcast poll fail "
                    f"({type(_bp_e).__name__}: {_bp_e}) — skip, retry next interval"
                )
            last_broadcast_poll = time.time()

        if time.time() - last_heartbeat >= HEARTBEAT_INTERVAL_SEC:
            eb = event_bus.stats()
            # Sprint #37 v2 (2026-05-13): pending filtered TYLKO `["NEW_ORDER"]`
            # (event_types z _SHADOW_STUCK_CONFIG). Pre-#37 sumowało multi-consumer
            # queue (NEW_ORDER+COURIER_PICKED_UP+COURIER_DELIVERED) → wrong attribution
            # gdy sla_tracker miał backlog. Lekcja #113.
            pending_queue = event_bus.get_pending_count(
                event_types=list(_SHADOW_STUCK_CONFIG.event_types)
            )
            _v328_now = time.time()
            _snapshot = compute_heartbeat(
                last_processed_ts=last_processed_ts,
                now=_v328_now,
                pending=pending_queue,
                config=_SHADOW_STUCK_CONFIG,
            )
            _log.info(
                f"HEARTBEAT totals={totals} "
                f"event_bus=pending:{eb['pending']}(NEW_ORDER:{pending_queue})"
                f"/processed:{eb['processed']}/failed:{eb['failed']} "
                f"last_processed_age_sec={_snapshot.age_sec:.0f} "
                f"worker_alive={_snapshot.worker_alive}"
            )
            if _snapshot.is_stuck:
                _log.critical(
                    f"V328_WORKER_STUCK age={_snapshot.age_sec:.0f}s "
                    f"pending_NEW_ORDER={pending_queue} "
                    f"threshold_age={_SHADOW_STUCK_CONFIG.age_threshold_sec}s "
                    f"threshold_pending={_SHADOW_STUCK_CONFIG.pending_threshold}"
                )
            # Sprint #37 v2: pure state machine via consumer_stuck_alert helper.
            _state_before = _shadow_stuck_state
            _emit_alert, _alert_kind, _shadow_stuck_state = evaluate_stuck_alert(
                state=_state_before,
                snapshot=_snapshot,
                now=_v328_now,
                config=_SHADOW_STUCK_CONFIG,
            )
            # Audit trail per tick — odzysk historyczny + future calibration.
            # Defensive (helper łapie własne exceptions).
            append_evaluation_log(
                snapshot=_snapshot,
                state_before=_state_before,
                state_after=_shadow_stuck_state,
                emit=_emit_alert,
                kind=_alert_kind,
                config=_SHADOW_STUCK_CONFIG,
                now=_v328_now,
            )
            if _emit_alert and not _SHADOW_STUCK_CONFIG.shadow_mode_only:
                # Defensive try/except — Telegram unreachable NIE blokuje main loop (Lekcja #87/#110).
                try:
                    from dispatch_v2.telegram_utils import send_admin_alert as _v328_send_alert
                    _msg = render_telegram_message(
                        kind=_alert_kind,
                        snapshot=_snapshot,
                        state=_shadow_stuck_state,
                        config=_SHADOW_STUCK_CONFIG,
                        now=_v328_now,
                    )
                    _v328_send_alert(_msg)
                except Exception as _v328_alert_e:
                    _log.error(
                        f"V328_WORKER_STUCK telegram alert fail "
                        f"({type(_v328_alert_e).__name__}: {_v328_alert_e}) — log only"
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
