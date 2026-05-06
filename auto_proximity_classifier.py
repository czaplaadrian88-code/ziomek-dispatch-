"""Faza 7-AUTO-PROXIMITY classifier — rule-based AUTO/ACK/ALERT decision routing.

Post-pivot 03.05.2026 (rule-based autonomy, NOT LGBM PRIMARY which was cancelled).
Spec: eod_drafts/2026-05-06/faza_7_auto_proximity_design_spec.md

Flow:
  dispatch_pipeline.assess_order() returns PipelineResult(verdict="PROPOSE", ...)
    ↓
  classify_auto_route(result, fleet_snapshot, now, flags)
    ↓
  returns "AUTO" | "ACK" | "ALERT" + reason string

Pure function: no I/O, no logging side-effects, no telemetry emit. Caller writes
telemetry. Deterministic for identical inputs.

Tydzień 1 (T1, gate 30%): conservative thresholds, edge cases → ACK.
Tydzień 2 (T2, 70%): relaxed score margin + tier scope.
Tydzień 3 (T3, 100% non-edge): aggressive thresholds, edge cases ALWAYS bypass to ACK.

Adrian decyzje 2026-05-06:
  - GPS NOT required (pos_source any) — kurier post-shift_start 5min traktowany jako "pod restauracją"
  - Tier: T1 gold/std+, T2 +std, T3 wszystkie (default; flag-overridable)
  - Czasówki (czas_odbioru >= 60) ZAWSZE → ACK (Bartek wave-line judgment)
  - ALERT zawsze human gate (no auto-KOORD w T1-T3)
  - Edge cases ZAWSZE bypass do ACK lub ALERT
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple


# Routes (sentinel constants — caller imports these)
ROUTE_AUTO = "AUTO"
ROUTE_ACK = "ACK"
ROUTE_ALERT = "ALERT"

# Threshold table — default values; override via flags["AUTO_PROXIMITY_THRESHOLDS"].
# Keys: T1 (Tydzień 1, gate 30%), T2 (Tydzień 2, 70%), T3 (Tydzień 3, 100%).
DEFAULT_THRESHOLDS: Dict[str, Dict[str, Any]] = {
    "T1": {
        "min_pool_feasible": 2,
        "min_score_margin": 15.0,    # placeholder — calibration after 1-week shadow
        "tiers": ("gold", "std+"),
        "min_score": 50.0,
        "strict_gps": False,         # Adrian decyzja: GPS off OK
    },
    "T2": {
        "min_pool_feasible": 2,
        "min_score_margin": 10.0,
        "tiers": ("gold", "std+", "std"),
        "min_score": 40.0,
        "strict_gps": False,
    },
    "T3": {
        "min_pool_feasible": 1,
        "min_score_margin": 5.0,
        "tiers": ("gold", "std+", "std", "new", "slow"),
        "min_score": 30.0,
        "strict_gps": False,
    },
}

# Czasówka detection (aligns with czasowka_scheduler._is_czasowka — czas_odbioru >= 60)
CZASOWKA_PREP_MIN = 60

# Mass-fail threshold: only triggers ALERT when pool is meaningful (>=4 candidates)
MASS_FAIL_MIN_POOL = 4
MASS_FAIL_RATIO = 0.5  # >=50% NO verdict → mass fail signal

# Schedule edge: shift ending soon after pickup_ready
SHIFT_END_EDGE_MIN = 15.0  # if shift_end - pickup_ready_at <= 15min → ACK


@dataclass(frozen=True)
class ClassifierContext:
    """Immutable input snapshot for classifier — easier to test, easier to reason about."""
    pool_feasible_count: int
    pool_total_count: int
    best_courier_id: str
    best_score: float
    best_score_margin: float        # top1 - top2 (0 if only 1 feasible)
    best_tier: Optional[str]        # gold/std+/std/new/slow/None
    best_pos_source: str            # gps | pre_shift | last_delivered | last_picked_up | none
    best_metrics: Dict[str, Any]
    best_plan_violations: int       # 0 if plan exists and clean, >0 if best_effort SLA violations
    best_effort: bool
    czasowka: bool                  # czas_odbioru >= 60
    shift_end_edge: bool            # shift_end - pickup_ready_at <= SHIFT_END_EDGE_MIN
    no_feasible_count: int          # number of candidates with verdict=NO
    parser_degraded: bool


# -------------- Detector helpers (deterministic, side-effect-free) --------------

def _is_czasowka(order_event: Dict[str, Any]) -> bool:
    """Czasówka if prep_minutes >= 60 (aligns with panel API czas_odbioru semantic)."""
    prep = order_event.get("prep_minutes") or order_event.get("czas_odbioru") or 0
    try:
        return float(prep) >= CZASOWKA_PREP_MIN
    except (TypeError, ValueError):
        return False


def _shift_end_edge(
    courier_state: Optional[Any],
    pickup_ready_at: Optional[datetime],
) -> bool:
    """True if shift ends within SHIFT_END_EDGE_MIN of pickup_ready_at."""
    if courier_state is None or pickup_ready_at is None:
        return False
    shift_end = getattr(courier_state, "shift_end", None)
    if shift_end is None:
        return False
    if shift_end.tzinfo is None:
        shift_end = shift_end.replace(tzinfo=timezone.utc)
    if pickup_ready_at.tzinfo is None:
        pickup_ready_at = pickup_ready_at.replace(tzinfo=timezone.utc)
    delta_min = (shift_end - pickup_ready_at).total_seconds() / 60.0
    return 0 < delta_min <= SHIFT_END_EDGE_MIN


def _has_frozen_window_violation(best_metrics: Dict[str, Any]) -> bool:
    """V3.27.4 frozen pickup window violation — anomalia, ALERT."""
    return bool(best_metrics.get("v3274_frozen_window_violation"))


def _mass_fail(pool_feasible: int, pool_total: int) -> bool:
    """Mass fail = >50% candidates rejected AND pool meaningful (>=4)."""
    if pool_total < MASS_FAIL_MIN_POOL:
        return False
    no_count = pool_total - pool_feasible
    return no_count >= MASS_FAIL_RATIO * pool_total


def _parser_degraded(flags: Dict[str, Any]) -> bool:
    """Caller is expected to pass parser_degraded via flags or env-driven check.

    We don't make HTTP calls from classifier. Caller polls /health/parser
    asynchronously and surfaces flag.
    """
    return bool(flags.get("PARSER_DEGRADED", False))


def _build_context(
    result: Any,                                # PipelineResult duck-type (avoid circular import)
    fleet_snapshot: Dict[str, Any],
    order_event: Optional[Dict[str, Any]],
    flags: Dict[str, Any],
) -> ClassifierContext:
    """Extract pure-data context from PipelineResult + fleet — single allocation."""
    candidates = result.candidates or []
    feasible = [c for c in candidates if getattr(c, "feasibility_verdict", None) == "MAYBE"]
    no_count = sum(1 for c in candidates if getattr(c, "feasibility_verdict", None) == "NO")
    best = result.best

    if best is None:
        return ClassifierContext(
            pool_feasible_count=0,
            pool_total_count=getattr(result, "pool_total_count", 0) or 0,
            best_courier_id="",
            best_score=0.0,
            best_score_margin=0.0,
            best_tier=None,
            best_pos_source="none",
            best_metrics={},
            best_plan_violations=0,
            best_effort=False,
            czasowka=False,
            shift_end_edge=False,
            no_feasible_count=no_count,
            parser_degraded=_parser_degraded(flags),
        )

    # Score margin: top1 - top2 (only over MAYBE candidates — NO scores meaningless)
    feasible_sorted = sorted(feasible, key=lambda c: -getattr(c, "score", 0.0))
    if len(feasible_sorted) >= 2:
        margin = feasible_sorted[0].score - feasible_sorted[1].score
    else:
        margin = 0.0  # solo win — margin undefined, treat as 0 (will fail T1/T2 min_pool=2 anyway)

    cs = fleet_snapshot.get(best.courier_id) if fleet_snapshot else None
    tier = getattr(cs, "tier_bag", None) if cs is not None else None

    metrics = best.metrics or {}
    pos_source = metrics.get("pos_source") or (getattr(cs, "pos_source", "none") if cs else "none")

    plan_violations = 0
    if best.plan is not None:
        plan_violations = int(getattr(best.plan, "sla_violations", 0) or 0)

    czasowka = _is_czasowka(order_event or {})
    shift_edge = _shift_end_edge(cs, getattr(result, "pickup_ready_at", None))

    return ClassifierContext(
        pool_feasible_count=len(feasible),
        pool_total_count=getattr(result, "pool_total_count", 0) or len(candidates),
        best_courier_id=str(best.courier_id),
        best_score=float(getattr(best, "score", 0.0) or 0.0),
        best_score_margin=float(margin),
        best_tier=tier,
        best_pos_source=pos_source,
        best_metrics=metrics,
        best_plan_violations=plan_violations,
        best_effort=bool(getattr(best, "best_effort", False)),
        czasowka=czasowka,
        shift_end_edge=shift_edge,
        no_feasible_count=no_count,
        parser_degraded=_parser_degraded(flags),
    )


def _resolve_thresholds(flags: Dict[str, Any]) -> Tuple[str, Dict[str, Any]]:
    """Read AUTO_PROXIMITY_THRESHOLD ('T1'|'T2'|'T3') + optional override table."""
    tier_key = str(flags.get("AUTO_PROXIMITY_THRESHOLD", "T1")).upper()
    if tier_key not in ("T1", "T2", "T3"):
        tier_key = "T1"
    table = flags.get("AUTO_PROXIMITY_THRESHOLDS")
    if isinstance(table, dict) and tier_key in table:
        return tier_key, table[tier_key]
    return tier_key, DEFAULT_THRESHOLDS[tier_key]


# -------------- Edge case detection -> ACK or ALERT --------------

def _detect_edge_routing(ctx: ClassifierContext) -> Optional[Tuple[str, str]]:
    """Returns (route, reason) if edge case detected, else None.

    Order matters: ALERT > ACK. ALERT routes are reserved for system anomalies.
    ACK routes are operational edge cases requiring human judgment.
    """
    # ALERT path — system anomalies / hard signals to escalate
    if ctx.parser_degraded:
        return ROUTE_ALERT, "parser_degraded"
    if _has_frozen_window_violation(ctx.best_metrics):
        return ROUTE_ALERT, "frozen_window_violation"
    if _mass_fail(ctx.pool_feasible_count, ctx.pool_total_count):
        return ROUTE_ALERT, f"mass_fail (no={ctx.no_feasible_count}/total={ctx.pool_total_count})"

    # ACK path — operational edge cases (best != None already validated by caller)
    if ctx.czasowka:
        return ROUTE_ACK, "czasowka_60min"
    if ctx.best_effort or ctx.best_plan_violations > 0:
        return ROUTE_ACK, f"best_effort_or_sla_violations ({ctx.best_plan_violations})"
    if ctx.best_metrics.get("solo_fallback"):
        return ROUTE_ACK, "solo_fallback (R1/R5/R8 ignored)"
    if ctx.shift_end_edge:
        return ROUTE_ACK, "shift_end_edge_<=15min"

    return None


# -------------- Threshold check -> AUTO or ACK --------------

def _meets_high_conf(ctx: ClassifierContext, thresholds: Dict[str, Any]) -> Tuple[bool, str]:
    """C1-C6 conditions per spec sekcja 2.2. Returns (passed, reason)."""
    # C1: pool_feasible_count
    if ctx.pool_feasible_count < thresholds["min_pool_feasible"]:
        return False, f"C1_pool_feasible={ctx.pool_feasible_count}<{thresholds['min_pool_feasible']}"

    # C2: score margin
    if ctx.best_score_margin < thresholds["min_score_margin"]:
        return False, f"C2_score_margin={ctx.best_score_margin:.1f}<{thresholds['min_score_margin']}"

    # C3: tier whitelist
    allowed_tiers = thresholds["tiers"]
    if ctx.best_tier is None:
        return False, "C3_tier_unknown"
    if ctx.best_tier not in allowed_tiers:
        return False, f"C3_tier={ctx.best_tier}_not_in_{allowed_tiers}"

    # C4: pos_source (Adrian relax — strict_gps default False)
    if thresholds.get("strict_gps", False) and ctx.best_pos_source != "gps":
        return False, f"C4_pos_source={ctx.best_pos_source}"

    # C5: edge cases — handled in _detect_edge_routing before reaching here

    # C6: absolute score floor
    if ctx.best_score < thresholds["min_score"]:
        return False, f"C6_score={ctx.best_score:.1f}<{thresholds['min_score']}"

    return True, f"all_conditions_met_{thresholds.get('_label', '')}".rstrip("_")


# -------------- Public entry point --------------

def classify_auto_route(
    result: Any,
    fleet_snapshot: Optional[Dict[str, Any]] = None,
    now: Optional[datetime] = None,
    flags: Optional[Dict[str, Any]] = None,
    order_event: Optional[Dict[str, Any]] = None,
) -> Tuple[str, str]:
    """Main classifier — pure function.

    Args:
        result: PipelineResult-like (must have: best, candidates, pool_feasible_count,
                pool_total_count, pickup_ready_at).
        fleet_snapshot: cid -> CourierState (used for tier_bag, shift_end). Optional —
                if None, tier check returns C3_tier_unknown → ACK.
        now: current time (UTC) — reserved for time-based gating future.
        flags: dict with keys:
            - AUTO_PROXIMITY_ENABLED (bool, default False — global kill)
            - AUTO_PROXIMITY_SHADOW_ONLY (bool, default True — observation mode)
            - AUTO_PROXIMITY_THRESHOLD ("T1"|"T2"|"T3", default "T1")
            - AUTO_PROXIMITY_THRESHOLDS (optional dict override)
            - PARSER_DEGRADED (bool, default False — caller responsible for polling /health/parser)
        order_event: original order dict (for czasówka detection).

    Returns:
        (route, reason) where route ∈ {AUTO, ACK, ALERT}, reason is short debug string.

    Determinism: identical inputs → identical output. No I/O, no logging.
    """
    flags = flags or {}
    fleet_snapshot = fleet_snapshot or {}

    # Global kill switch — fail closed to standard flow
    if not flags.get("AUTO_PROXIMITY_ENABLED", False) and not flags.get("AUTO_PROXIMITY_SHADOW_ONLY", False):
        return ROUTE_ACK, "auto_proximity_disabled_global"

    # Verdict precondition: classifier only operates on PROPOSE outcomes
    verdict = getattr(result, "verdict", None)
    if verdict != "PROPOSE":
        return ROUTE_ACK, f"verdict_not_propose ({verdict})"

    if getattr(result, "best", None) is None:
        return ROUTE_ACK, "no_best_candidate"

    ctx = _build_context(result, fleet_snapshot, order_event, flags)

    # Edge cases override threshold check
    edge = _detect_edge_routing(ctx)
    if edge is not None:
        return edge

    # Threshold check
    tier_key, thresholds = _resolve_thresholds(flags)
    thresholds = dict(thresholds)
    thresholds.setdefault("_label", tier_key)
    passed, reason = _meets_high_conf(ctx, thresholds)
    if passed:
        return ROUTE_AUTO, f"high_conf_{tier_key}|margin={ctx.best_score_margin:.1f}|tier={ctx.best_tier}"
    return ROUTE_ACK, reason


# -------------- Public introspection (for telemetry) --------------

def build_context_for_logging(
    result: Any,
    fleet_snapshot: Optional[Dict[str, Any]] = None,
    flags: Optional[Dict[str, Any]] = None,
    order_event: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """Caller-friendly dict snapshot of ClassifierContext — for shadow log enrichment.

    Used by shadow_dispatcher LOCATION B serialization. Returns flat dict.
    """
    flags = flags or {}
    if getattr(result, "best", None) is None:
        return {
            "auto_route_pool_feasible": getattr(result, "pool_feasible_count", 0),
            "auto_route_pool_total": getattr(result, "pool_total_count", 0),
            "auto_route_score_margin": 0.0,
            "auto_route_tier_best": None,
            "auto_route_pos_source_best": None,
        }
    ctx = _build_context(result, fleet_snapshot or {}, order_event, flags)
    return {
        "auto_route_pool_feasible": ctx.pool_feasible_count,
        "auto_route_pool_total": ctx.pool_total_count,
        "auto_route_score_margin": round(ctx.best_score_margin, 2),
        "auto_route_tier_best": ctx.best_tier,
        "auto_route_pos_source_best": ctx.best_pos_source,
        "auto_route_czasowka": ctx.czasowka,
        "auto_route_best_effort": ctx.best_effort,
        "auto_route_shift_end_edge": ctx.shift_end_edge,
    }
