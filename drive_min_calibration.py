"""Drive_min calibration v1 — pos_source offset table + floor guard.

Sprint Drive_min Calibration v2 (2026-05-27, Alt A per design doc).

**Problem:** `predicted_drive_min` (assign→pickup proxy printed przez Ziomka)
jest systematycznie zaniżony o median +12.9 min vs realny `assign_to_pickup_min`
na 3013-row backfill. 69.2% case'ów under-estimate >5 min.

**Mechanism:** Per-pos_source offset table + global physical floor.

  drive_min_calibrated = max(FLOOR_MIN, predicted_drive_min + OFFSET[pos_source])

**Why pos_source alone (NIE +tier +peak):** Step 1.11 audytu empirycznego —
dodawanie features (tier, peak, district, pred_bucket) NIE poprawia residual
(7.88 vs 7.89 min), za to fragmentuje dane i wymaga 2x więcej grup. YAGNI.

**Offset values (median Δ z backfill n=3013, /tmp/drive_min_bias_report.txt §1.2):**
  no_gps                  +6.5   n=1797  (synthetic BIALYSTOK_CENTER + max(15,prep) — already conservative)
  pre_shift              +15.3   n=455
  gps                    +35.1   n=41    (parked-fresh GPS slip-through)
  last_assigned_pickup   +30.9   n=317   (kurier od ostatniego pickupu pojechał dalej)
  last_picked_up_pickup  +34.7   n=194   (jak wyżej + carry)
  last_picked_up_delivery +30.5  n=16
  post_wave              +30.9   n=193   (po komicie fali — pozycja przeszłości)
  last_picked_up_interp   +10.0  placeholder dla post-F4-K2 (re-calibrate after LIVE)

**Floor (8.0 min):** Step 1.10 pokazał constant +33 min bias dla bucket ≤5 min
(99.8% under-estimate). Physical floor pickup = parking + entry + DWELL + handover
≈ 8 min minimum. Empirycznie nie ma realnych <5 min cases assign→pickup.

**Hot-reload:** Re-import modułu nie wymagany — `OFFSET_TABLE` i `FLOOR_MIN` są
top-level constants. Re-calibration miesięczna via cron (zob. design §3.5) podmienia
te wartości manualnie (po review Adriana).

**Integration:** Caller wywołuje `apply_calibration(raw, ctx)`. Flag-gated przez
`ENABLE_DRIVE_MIN_CALIBRATION_V2` z flags.json. Shadow log zawsze ON (`_SHADOW`).

References:
  /tmp/drive_min_calibration_design.md  — full spec 580 linii
  /tmp/drive_min_bias_report.txt        — empirical analysis 3013 rows
  /tmp/drive_min_bias_analysis.py       — re-runnable analysis script
"""
from __future__ import annotations

from typing import Any, Dict, Optional, Tuple


# Empirical offsets — median Δ per pos_source (Step 1.2, /tmp/drive_min_bias_report.txt).
# Dodanie nowego pos_source: wartość domyślna 0.0 (no-op) jeśli klucza brak — zob.
# `compute_pos_source_offset()`. Bezpieczna degradacja gdy `courier_resolver`
# emituje nowy enum value zanim re-calibration go pokryje.
OFFSET_TABLE: Dict[str, float] = {
    "no_gps":                   6.5,
    "pre_shift":               15.3,
    "gps":                     35.1,
    "last_assigned_pickup":    30.9,
    "last_picked_up_pickup":   34.7,
    "last_picked_up_delivery": 30.5,
    "post_wave":               30.9,
    # F4 K2 interp placeholder — re-calibrate gdy F4 K2 LIVE 1 month
    "last_picked_up_interp":   10.0,
}

# Absolute physical floor — pickup obejmuje parking + DWELL + handover, fizycznie
# nigdy <8 min (verified empirically Step 1.10: bucket ≤5 ma constant +33 bias).
FLOOR_MIN: float = 8.0

# Version tag — bumpowane przy każdej re-kalibracji (cron monthly).
CALIBRATION_VERSION: str = "v1_2026-05-27"


def compute_pos_source_offset(
    pos_source: Optional[str],
    raw_drive_min: float,        # noqa: ARG001 — reserved dla future per-magnitude offset
    peak_window: bool,           # noqa: ARG001 — reserved dla Faza 2 peak bump
    tier: Optional[str],         # noqa: ARG001 — reserved dla Faza 2 per-tier secondary
) -> float:
    """Zwraca offset (w minutach) dla danego pos_source.

    Niemapowany lub None pos_source → 0.0 (no-op, bezpieczna degradacja).
    `raw_drive_min`/`peak_window`/`tier` zarezerwowane na przyszłą rozbudowę
    (Step 1.11 odrzucił multi-feature offsets jako fragmentację bez korzyści).

    Args:
        pos_source: enum z courier_resolver — {"no_gps", "pre_shift", "gps",
            "last_assigned_pickup", "last_picked_up_pickup",
            "last_picked_up_delivery", "post_wave", "last_picked_up_interp", None}.
        raw_drive_min: surowy `predicted_drive_min` (PLACEHOLDER dla future).
        peak_window: True jeśli lunch/dinner peak (PLACEHOLDER dla future).
        tier: tier_bag kuriera (PLACEHOLDER dla future).

    Returns:
        Offset float w minutach (zawsze ≥0 w current implementation).
    """
    if not pos_source:
        return 0.0
    return float(OFFSET_TABLE.get(pos_source, 0.0))


def apply_calibration(
    raw_drive_min: Optional[float],
    ctx: Dict[str, Any],
) -> Tuple[float, Dict[str, Any]]:
    """Apply pos_source offset + floor guard do raw drive_min.

    Pure function — no I/O, no side-effects. Caller decyduje czy logować
    shadow entry (na podstawie `debug` dict).

    Args:
        raw_drive_min: surowy `predicted_drive_min` z chain_eta / OSRM. None
            traktowane jako 0.0 (sentinel — caller powinien wcześniej guardować
            None ale defensive default).
        ctx: dict z polami (wszystkie opcjonalne):
            - pos_source: str | None
            - tier: str | None
            - peak_window: bool (default False)
            - order_id: str | None (for log only)
            - courier_id: str | None (for log only)

    Returns:
        Tuple (calibrated_min, debug_dict). `debug_dict` zawiera:
            - raw_drive_min: float (input echo)
            - offset_applied: float
            - pre_floor_value: float (raw + offset)
            - floor_hit: bool
            - calibrated_drive_min: float
            - pos_source: str | None
            - tier: str | None
            - peak_window: bool
            - calibration_version: str
    """
    raw = float(raw_drive_min) if raw_drive_min is not None else 0.0

    pos_source: Optional[str] = ctx.get("pos_source")
    tier: Optional[str] = ctx.get("tier")
    peak_window: bool = bool(ctx.get("peak_window", False))

    offset = compute_pos_source_offset(pos_source, raw, peak_window, tier)
    pre_floor = raw + offset
    calibrated = max(FLOOR_MIN, pre_floor)
    floor_hit = calibrated == FLOOR_MIN and pre_floor < FLOOR_MIN

    debug: Dict[str, Any] = {
        "raw_drive_min": round(raw, 2),
        "offset_applied": round(offset, 2),
        "pre_floor_value": round(pre_floor, 2),
        "floor_hit": floor_hit,
        "calibrated_drive_min": round(calibrated, 2),
        "pos_source": pos_source,
        "tier": tier,
        "peak_window": peak_window,
        "calibration_version": CALIBRATION_VERSION,
    }
    return calibrated, debug
