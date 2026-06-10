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

import json
import os
import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

# Sprint 2 (2026-05-27): Kebab Król dinner carry penalty fix.
# Forensic agent D (`/tmp/kebab_krol_diagnostic.md`): R6 breach 22.5% w dinner
# peak (17-21 Warsaw) vs 7-8% baseline. Root cause = carry/bag-stack penalty —
# KK siedzi 15-30 min w torbie gdy kurier dostarcza inną restaurację pierwszą.
# ML scorer ślepy (1/71 predicted vs 16/71 actual). Conditional exclusion:
# dinner window = ALERT zamiast routingu AUTO. Lunch / off-peak nieruszane.
_WARSAW_TZ = ZoneInfo("Europe/Warsaw")
KEBAB_KROL_NAME_SUBSTR = "kebab król"  # case-insensitive substring
KEBAB_KROL_DINNER_START_HOUR_WARSAW = 17  # inclusive
KEBAB_KROL_DINNER_END_HOUR_WARSAW = 21    # exclusive (17..20 fires)

from dispatch_v2 import drive_min_calibration as _drive_calib


# Sprint Drive_min Calibration v2 (2026-05-27): shadow log destination.
# Default OFF na main path; SHADOW_LOG zawsze ON (zero side-effect na dispatch).
# Override path via env DRIVE_MIN_CALIBRATION_SHADOW_LOG_PATH (tests / replay tools).
DRIVE_MIN_CALIBRATION_SHADOW_LOG_PATH = (
    "/root/.openclaw/workspace/dispatch_state/drive_min_calibration_log_v2.jsonl"
)


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

# Z-10 (audyt 2026-06-10): margin liczony na FINALNYM rankingu, nie surowym score.
# Stary margin = top1−top2 po surowym score wśród feasible, a result.best wybierany
# jest PO demote/tieringu (V3.16 blind-empty demote, late_pickup Opcja B, …) —
# margin potrafił opisywać dwóch NIE-wybranych kandydatów, a AUTO mogło odpalić
# na best który nie jest score-topem. Nowy: margin = score(best) − max(score
# POZOSTAŁYCH feasible); AUTO dodatkowo wymaga best==score-top (C7).
# Prerequisite flipu Fazy 7. Env default ON; hot-reload kill-switch:
# flags.json ENABLE_F7_MARGIN_FINAL_RANKING=false. AUTO_PROXIMITY jest live-OFF
# (shadow-only) → zmiana wpływa wyłącznie na shadow-klasyfikację.
_ENV_F7_MARGIN_FINAL_RANKING_DEFAULT = os.environ.get(
    "ENABLE_F7_MARGIN_FINAL_RANKING", "1") == "1"


def _f7_margin_final_ranking_on(flags: Dict[str, Any]) -> bool:
    return bool(flags.get("ENABLE_F7_MARGIN_FINAL_RANKING",
                          _ENV_F7_MARGIN_FINAL_RANKING_DEFAULT))


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
    # Z-10 (audyt 2026-06-10): czy result.best jest score-topem wśród feasible.
    # False gdy selekcja post-score (demote/tiering) wybrała innego niż argmax —
    # AUTO wymaga True (C7). Default True = zachowanie legacy (flaga OFF).
    best_is_score_top: bool = True


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
    """Mass fail = >50% candidates rejected AND pool meaningful (>=4).

    DEPRECATED 2026-05-18 (kalibracja auto_route): USUNIĘTY z `_detect_edge_routing`
    — odpalał 85% propozycji jako fałszywy ALERT. ">=50% NO" to norma dispatchu,
    nie anomalia. Funkcja zachowana (pure, bez side-effectów) — może wrócić jako
    sygnał telemetryczny, NIE jako trigger routingu.
    """
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


def _peak_window_for(now: Optional[datetime]) -> bool:
    """True jeśli lunch (12-14) lub dinner (18-20) peak (Europe/Warsaw).

    Sprint Drive_min Calibration v2 — używane jako placeholder dla Faza 2
    per-peak bump (current `apply_calibration` ignoruje, ale logujemy do
    shadow log dla forward-compat).
    """
    if now is None:
        return False
    try:
        from zoneinfo import ZoneInfo
        warsaw = now.astimezone(ZoneInfo("Europe/Warsaw"))
        h = warsaw.hour
        return (12 <= h < 14) or (18 <= h < 20)
    except Exception:
        return False


def _append_drive_min_calibration_shadow(entry: Dict[str, Any]) -> None:
    """Append-only JSONL log dla drive_min calibration shadow.

    Side-effect: jeden write per call. Fail-safe — jakikolwiek I/O error
    swallowed (classifier MUSI być deterministyczny, no-throw na log fail).
    """
    path = os.environ.get(
        "DRIVE_MIN_CALIBRATION_SHADOW_LOG_PATH",
        DRIVE_MIN_CALIBRATION_SHADOW_LOG_PATH,
    )
    try:
        line = json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n"
        # Append-mode write — atomic ENOUGH dla JSONL (POSIX append <= PIPE_BUF).
        # NIE używamy temp+rename bo to log append, nie state file.
        with open(path, "a", encoding="utf-8") as f:
            f.write(line)
    except Exception:
        # Defensive: brak directory, permission, dysk full — log fail nie blokuje routingu.
        pass


def _maybe_apply_drive_min_calibration(
    metrics: Dict[str, Any],
    cs: Any,
    flags: Dict[str, Any],
    now: Optional[datetime],
    order_id: Optional[str],
    courier_id: Optional[str],
    tier: Optional[str],
    emit_shadow_log: bool = True,
) -> Dict[str, Any]:
    """Apply calibration do metrics["drive_min"] gdy flag ON. Always shadow-log.

    Zwraca: enriched metrics dict (kopia) z `drive_min_raw`+`drive_min_calibrated`+
    `drive_min_calibration_offset`+`drive_min_calibration_floor_hit` zawsze, oraz
    `drive_min` zamienione na calibrated **tylko gdy** flag main=ON.

    Shadow log entry zapisywany ZAWSZE gdy flag SHADOW=True (default True).
    """
    enable_main = bool(flags.get("ENABLE_DRIVE_MIN_CALIBRATION_V2", False))
    enable_shadow = bool(flags.get("ENABLE_DRIVE_MIN_CALIBRATION_V2_SHADOW", True))

    pos_source = metrics.get("pos_source") or (
        getattr(cs, "pos_source", None) if cs is not None else None
    )
    raw_drive_min = metrics.get("drive_min")

    if raw_drive_min is None:
        # Nic do kalibracji — propagate metrics jak są.
        return metrics

    peak_window = _peak_window_for(now)
    ctx_dict = {
        "pos_source": pos_source,
        "tier": tier,
        "peak_window": peak_window,
        "order_id": order_id,
        "courier_id": courier_id,
    }
    calibrated, debug = _drive_calib.apply_calibration(raw_drive_min, ctx_dict)

    enriched = dict(metrics)
    enriched["drive_min_raw"] = debug["raw_drive_min"]
    enriched["drive_min_calibrated"] = debug["calibrated_drive_min"]
    enriched["drive_min_calibration_offset"] = debug["offset_applied"]
    enriched["drive_min_calibration_floor_hit"] = debug["floor_hit"]
    enriched["drive_min_calibration_version"] = debug["calibration_version"]

    if enable_main:
        # Substytucja main path — downstream consumer (telegram, score) zobaczy calibrated.
        enriched["drive_min"] = debug["calibrated_drive_min"]

    if enable_shadow and emit_shadow_log:
        ts_iso = (now or datetime.now(timezone.utc)).astimezone(timezone.utc).isoformat()
        log_entry = {
            "ts": ts_iso,
            "order_id": str(order_id) if order_id is not None else None,
            "courier_id": str(courier_id) if courier_id is not None else None,
            "pos_source": pos_source,
            "tier": tier,
            "peak_window": peak_window,
            "raw_drive_min": debug["raw_drive_min"],
            "offset_applied": debug["offset_applied"],
            "calibrated_drive_min": debug["calibrated_drive_min"],
            "floor_applied": debug["floor_hit"],
            "calibration_version": debug["calibration_version"],
            "main_path_active": enable_main,
        }
        _append_drive_min_calibration_shadow(log_entry)

    return enriched


def _build_context(
    result: Any,                                # PipelineResult duck-type (avoid circular import)
    fleet_snapshot: Dict[str, Any],
    order_event: Optional[Dict[str, Any]],
    flags: Dict[str, Any],
    now: Optional[datetime] = None,
    emit_calibration_shadow: bool = False,
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

    # Score margin (only over MAYBE candidates — NO scores meaningless).
    # Z-10 (audyt 2026-06-10, flaga ENABLE_F7_MARGIN_FINAL_RANKING): margin liczony
    # względem FAKTYCZNIE WYBRANEGO best — score(best) − max(score POZOSTAŁYCH
    # feasible). Legacy (flaga OFF): top1−top2 po surowym score, niezależnie od
    # tego kim jest best (mógł opisywać dwóch NIE-wybranych kandydatów).
    best_is_score_top = True
    if _f7_margin_final_ranking_on(flags):
        best_score_val = float(getattr(best, "score", 0.0) or 0.0)
        _best_cid = str(getattr(best, "courier_id", ""))
        other_scores = [
            float(getattr(c, "score", 0.0) or 0.0)
            for c in feasible
            if str(getattr(c, "courier_id", "")) != _best_cid
        ]
        if other_scores:
            top_other = max(other_scores)
            margin = best_score_val - top_other
            # tolerancja float — remis traktujemy jako score-top
            best_is_score_top = best_score_val >= top_other - 1e-9
        else:
            margin = 0.0  # solo win — margin undefined (will fail T1/T2 min_pool=2 anyway)
    else:
        feasible_sorted = sorted(feasible, key=lambda c: -getattr(c, "score", 0.0))
        if len(feasible_sorted) >= 2:
            margin = feasible_sorted[0].score - feasible_sorted[1].score
        else:
            margin = 0.0  # solo win — margin undefined, treat as 0 (will fail T1/T2 min_pool=2 anyway)

    cs = fleet_snapshot.get(best.courier_id) if fleet_snapshot else None
    tier = getattr(cs, "tier_bag", None) if cs is not None else None

    metrics = best.metrics or {}

    # Sprint Drive_min Calibration v2 (2026-05-27) — apply calibration + shadow log.
    # Flag-gated: main path tylko gdy ENABLE_DRIVE_MIN_CALIBRATION_V2=True. Shadow log
    # zawsze gdy SHADOW=True (default). Returns enriched metrics z drive_min_raw +
    # drive_min_calibrated zawsze, oraz drive_min sub'd na calibrated gdy main ON.
    metrics = _maybe_apply_drive_min_calibration(
        metrics=metrics,
        cs=cs,
        flags=flags,
        now=now,
        order_id=getattr(result, "order_id", None),
        courier_id=getattr(best, "courier_id", None),
        tier=tier,
        # G5: _build_context jest wołany 2× per order (classify_auto_route +
        # build_context_for_logging) — shadow-log tylko z realnej ścieżki, by nie dublować.
        emit_shadow_log=emit_calibration_shadow,
    )

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
        best_is_score_top=best_is_score_top,
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

def _detect_edge_routing(
    ctx: ClassifierContext,
    weak_pick_floor: Optional[float] = None,
) -> Optional[Tuple[str, str]]:
    """Returns (route, reason) if edge case detected, else None.

    Order matters: ALERT > ACK. ALERT = realny problem wymagający decyzji
    człowieka. ACK = wybór sensowny, wymaga tylko rzutu okiem.

    Kalibracja 2026-05-18 (rolling replay, 397 zleceń niedz. 17.05):
      • `mass_fail` USUNIĘTY z routingu — odpalał 85% propozycji jako ALERT
        (324/324 ALERT = mass_fail). ">=50% kurierów NO" to NORMA dispatchu
        (większość floty jest po drugiej stronie miasta / z pełną torbą dla
        danego zlecenia), nie anomalia systemu.
      • `best_effort`/SLA-violation przeniesione z ACK do ALERT — to JEST
        przypadek "0 feasible, Ziomek realnie zgaduje → człowiek decyduje".
      Efekt: ALERT 85% → ~16% (tylko realne problemy).

    F4 (2026-05-24): weak_pick_floor (z flagi ENABLE_AUTO_ROUTE_WEAK_PICK_ALERT)
      — gdy najlepszy pick ma score < floor (default 0.0 = ujemny), to obiektywnie
      słaby/wymuszony wybór (Case D korpusu: -20.34 prezentowane jako "🟡 sensowny
      wybór"). → ALERT "wymaga decyzji", NIE "sensowny wybór". Czasówki wykluczone
      (Adrian: czasówki ZAWSZE ACK, Bartek wave-line judgment).
    """
    # ALERT path — realne problemy: człowiek MUSI zdecydować
    if ctx.parser_degraded:
        return ROUTE_ALERT, "parser_degraded"
    if _has_frozen_window_violation(ctx.best_metrics):
        return ROUTE_ALERT, "frozen_window_violation"
    if ctx.best_effort or ctx.best_plan_violations > 0:
        return ROUTE_ALERT, f"best_effort_no_feasible (sla_viol={ctx.best_plan_violations})"
    if (weak_pick_floor is not None and not ctx.czasowka
            and ctx.best_score < weak_pick_floor):
        return ROUTE_ALERT, f"weak_pick_score={ctx.best_score:.1f}<{weak_pick_floor:g}"

    # ACK path — operacyjne edge: wybór sensowny, wymaga rzutu okiem
    if ctx.czasowka:
        return ROUTE_ACK, "czasowka_60min"
    if ctx.best_metrics.get("solo_fallback"):
        return ROUTE_ACK, "solo_fallback (R1/R5/R8 ignored)"
    if ctx.shift_end_edge:
        return ROUTE_ACK, "shift_end_edge_<=15min"

    return None


# -------------- Threshold check -> AUTO or ACK --------------

def _meets_high_conf(ctx: ClassifierContext, thresholds: Dict[str, Any]) -> Tuple[bool, str]:
    """C1-C7 conditions per spec sekcja 2.2 (+C7 Z-10). Returns (passed, reason)."""
    # C1: pool_feasible_count
    if ctx.pool_feasible_count < thresholds["min_pool_feasible"]:
        return False, f"C1_pool_feasible={ctx.pool_feasible_count}<{thresholds['min_pool_feasible']}"

    # C7 (Z-10, audyt 2026-06-10): AUTO tylko gdy best jest score-topem wśród
    # feasible. Selekcja post-score (demote blind-empty, late_pickup tiering)
    # może wybrać NIE-argmax — wtedy margin nie opisuje przewagi best → ACK.
    if not ctx.best_is_score_top:
        return False, "best_not_score_top"

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

    ctx = _build_context(result, fleet_snapshot, order_event, flags, now=now,
                         emit_calibration_shadow=True)

    # F4 (2026-05-24): słaby pick (ujemny score) → ALERT zamiast "sensowny wybór".
    weak_pick_floor: Optional[float] = None
    if flags.get("ENABLE_AUTO_ROUTE_WEAK_PICK_ALERT", False):
        try:
            weak_pick_floor = float(flags.get("AUTO_ROUTE_WEAK_PICK_SCORE_FLOOR", 0.0))
        except (TypeError, ValueError):
            weak_pick_floor = 0.0

    # Edge cases override threshold check
    edge = _detect_edge_routing(ctx, weak_pick_floor=weak_pick_floor)
    if edge is not None:
        return edge

    # Sprint 2 Etap 2.1 (2026-05-27): Kebab Król dinner carry-penalty exclusion.
    # Forensic agent D — KK dinner R6 breach 22.5% (vs lunch 0%, peer dinner 7.7%).
    # Default flag TRUE: niski risk, conditional (tylko KK + dinner 17-21 Warsaw),
    # lunch/off-peak/inne restauracje nieruszane. Adrian może flipować pre-commit.
    if flags.get("ENABLE_KEBAB_KROL_DINNER_EXCLUSION", True):
        restaurant_name = ((order_event or {}).get("restaurant") or "")
        if isinstance(restaurant_name, str) and KEBAB_KROL_NAME_SUBSTR in restaurant_name.lower():
            warsaw_now = (now or datetime.now(timezone.utc)).astimezone(_WARSAW_TZ)
            warsaw_hour = warsaw_now.hour
            if KEBAB_KROL_DINNER_START_HOUR_WARSAW <= warsaw_hour < KEBAB_KROL_DINNER_END_HOUR_WARSAW:
                return ROUTE_ALERT, "kk_dinner_carry_risk_v2"

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
    # G5: telemetria — NIE loguj calibration shadow (realna ścieżka classify_auto_route
    # już zalogowała ten order z poprawnym now/peak_window).
    ctx = _build_context(result, fleet_snapshot or {}, order_event, flags, now=None,
                         emit_calibration_shadow=False)
    return {
        "auto_route_pool_feasible": ctx.pool_feasible_count,
        "auto_route_pool_total": ctx.pool_total_count,
        "auto_route_score_margin": round(ctx.best_score_margin, 2),
        "auto_route_tier_best": ctx.best_tier,
        "auto_route_pos_source_best": ctx.best_pos_source,
        "auto_route_czasowka": ctx.czasowka,
        "auto_route_best_effort": ctx.best_effort,
        "auto_route_shift_end_edge": ctx.shift_end_edge,
        # Z-10 (audyt 2026-06-10): telemetria rozjazdu best vs score-top.
        "auto_route_best_is_score_top": ctx.best_is_score_top,
    }
