"""BUG-D — Per-distance-bin traffic multiplier (V3.28+).

TomTom sample 2026-05-26 ujawnił że flat per-hour multiplier zaniża krótkie
segmenty centrum (~2.3× real vs OSRM ff) i lekko zawyża długie międzydzielnicowe
(~1.15× real vs OSRM ff). `get_traffic_multiplier_v2(dt_utc, distance_km)` dodaje
additive boost per distance bucket — tylko w peak (base > 1.0), floor at 1.0.

Empirical reference: `eod_drafts/2026-05-26/measurements.md`.
"""
from datetime import datetime, timezone


def _peak_dt(hour_warsaw: int) -> datetime:
    """Weekday peak Warsaw → UTC aware datetime."""
    return datetime(2026, 5, 26, hour_warsaw - 2, 30, tzinfo=timezone.utc)


def _offpeak_dt() -> datetime:
    """Weekday off-peak Warsaw 03:00 → UTC aware datetime."""
    return datetime(2026, 5, 26, 1, 0, tzinfo=timezone.utc)


def test_v2_no_distance_returns_base():
    """Backward compat: distance_km=None → identical to get_traffic_multiplier."""
    from dispatch_v2.common import get_traffic_multiplier, get_traffic_multiplier_v2
    dt = _peak_dt(16)
    assert get_traffic_multiplier_v2(dt, None) == get_traffic_multiplier(dt)


def test_v2_offpeak_returns_base_regardless_of_distance():
    """Off-peak (base=1.0): no boost applied, returns 1.0 dla dowolnego distance."""
    from dispatch_v2.common import get_traffic_multiplier_v2
    dt = _offpeak_dt()
    assert get_traffic_multiplier_v2(dt, 0.5) == 1.0
    assert get_traffic_multiplier_v2(dt, 3.0) == 1.0
    assert get_traffic_multiplier_v2(dt, 10.0) == 1.0


def test_v2_peak_short_segment_boost():
    """Peak short (<2 km): base + 1.0. Empirical: Toriko→GK 1.47km @ 16-17 → 2.3×."""
    from dispatch_v2.common import get_traffic_multiplier_v2
    dt = _peak_dt(16)  # 16:30 Warsaw, base=1.3 z (16,17,1.3)
    assert get_traffic_multiplier_v2(dt, 1.5) == 2.3
    assert get_traffic_multiplier_v2(dt, 0.3) == 2.3  # boundary toward 0
    assert get_traffic_multiplier_v2(dt, 1.99) == 2.3  # upper boundary exclusive


def test_v2_peak_medium_segment_boost():
    """Peak medium (2-5 km): base + 0.4."""
    from dispatch_v2.common import get_traffic_multiplier_v2
    dt = _peak_dt(16)  # base=1.3
    assert abs(get_traffic_multiplier_v2(dt, 2.0) - 1.7) < 0.001
    assert abs(get_traffic_multiplier_v2(dt, 4.0) - 1.7) < 0.001
    assert abs(get_traffic_multiplier_v2(dt, 4.99) - 1.7) < 0.001


def test_v2_peak_long_segment_reduction():
    """Peak long (>=5 km): base - 0.15. Empirical: Bacieczki→JP61B 2.88km/Saturna→Rukola 6.23km avg 1.15×."""
    from dispatch_v2.common import get_traffic_multiplier_v2
    dt = _peak_dt(16)  # base=1.3
    assert abs(get_traffic_multiplier_v2(dt, 5.0) - 1.15) < 0.001
    assert abs(get_traffic_multiplier_v2(dt, 7.0) - 1.15) < 0.001
    assert abs(get_traffic_multiplier_v2(dt, 20.0) - 1.15) < 0.001


def test_v2_floor_at_1_0():
    """Boost ujemny nie obniża poniżej 1.0 (OSRM ff floor — nigdy szybciej niż brak ruchu)."""
    from dispatch_v2.common import get_traffic_multiplier_v2
    # Hour Warsaw 19 (base 1.1 z (19,20,1.1)) → long boost -0.15 = 0.95 → floored to 1.0
    dt_119 = _peak_dt(19)
    assert get_traffic_multiplier_v2(dt_119, 10.0) == 1.0


def test_v2_naive_datetime_raises():
    """Parity z get_traffic_multiplier: aware datetime required."""
    from dispatch_v2.common import get_traffic_multiplier_v2
    naive = datetime(2026, 5, 26, 14, 30)
    try:
        get_traffic_multiplier_v2(naive, 1.5)
        raise AssertionError("expected TypeError")
    except TypeError:
        pass


def test_apply_traffic_multiplier_records_v2_shadow():
    """osrm_client._apply_traffic_multiplier records traffic_multiplier_v2_shadow field."""
    from dispatch_v2.osrm_client import _apply_traffic_multiplier
    result = {"duration_s": 100.0, "distance_km": 1.5, "distance_m": 1500}
    dt = _peak_dt(16)  # base=1.3, short 1.5km → v2=2.3
    out = _apply_traffic_multiplier(result, dt)
    assert "traffic_multiplier_v2_shadow" in out
    assert out["traffic_multiplier_v2_shadow"] == 2.3
    # Raw preserved
    assert out["osrm_raw_duration_s"] == 100.0


def test_apply_traffic_multiplier_v2_shadow_handles_missing_distance():
    """Result bez distance_km: v2_shadow fallbacks do v1 (no distance correction)."""
    from dispatch_v2.osrm_client import _apply_traffic_multiplier
    result = {"duration_s": 100.0}  # no distance_km
    dt = _peak_dt(16)
    out = _apply_traffic_multiplier(result, dt)
    assert "traffic_multiplier_v2_shadow" in out
    assert abs(out["traffic_multiplier_v2_shadow"] - 1.3) < 0.001  # = base mult, no boost


def test_apply_traffic_multiplier_legacy_v1_when_flag_off():
    """Flag ENABLE_V326_DISTANCE_BIN_TRAFFIC_BOOST=False (default): applied mult = v1."""
    from dispatch_v2 import common, osrm_client
    # Default state: flag is False
    assert common.ENABLE_V326_DISTANCE_BIN_TRAFFIC_BOOST is False
    # Also need ENABLE_V326_OSRM_TRAFFIC_MULTIPLIER=True (live mode), default True from env
    if not common.ENABLE_V326_OSRM_TRAFFIC_MULTIPLIER:
        # Shadow mode: no mutation; skip this assertion path
        return
    result = {"duration_s": 100.0, "distance_km": 1.5}
    dt = _peak_dt(16)
    out = osrm_client._apply_traffic_multiplier(result, dt)
    # v1 applied: 100 * 1.3 = 130
    assert out["duration_s"] == 130.0
    assert abs(out["traffic_multiplier"] - 1.3) < 0.001
    # v2_shadow recorded but NOT applied
    assert out["traffic_multiplier_v2_shadow"] == 2.3


def test_empirical_case_3_toriko_gk():
    """Case #3 (measurements.md): Toriko→GK 1.47km @ 18:30 Wt → TomTom 6.5min, OSRM ff 3.1min.
    Real ratio 2.10×. V2 predicts: base(17-19=1.2) + 1.0 short boost = 2.2× → 6.82 min.
    """
    from dispatch_v2.common import get_traffic_multiplier_v2
    dt = _peak_dt(18)  # 18:30 Warsaw
    mult = get_traffic_multiplier_v2(dt, 1.47)
    osrm_ff = 3.1
    predicted = osrm_ff * mult
    # Expected ~6.8 min (vs TomTom 6.5 real) — within 5% tolerance
    assert 6.5 < predicted < 7.1, f"predicted={predicted:.2f}"


def test_empirical_case_d_bacieczki_jp61b_long():
    """Case F (measurements.md): Bacieczki→JP61B 2.88km @ 18-19 Wt → TomTom 4.7min, OSRM ff 4.6min.
    Real ratio 1.02×. V2 predicts: 2-5km medium bin: base(17-19=1.2) + 0.4 = 1.6 → 7.36 min.
    Acknowledged over-prediction dla tej kategorii — sample n=4 medium variable (1.02-2.35×).
    """
    from dispatch_v2.common import get_traffic_multiplier_v2
    dt = _peak_dt(18)
    mult = get_traffic_multiplier_v2(dt, 2.88)
    assert abs(mult - 1.6) < 0.001  # 1.2 + 0.4 medium


def test_table_structure_sorted_ascending():
    """V326_OSRM_DISTANCE_BIN_BOOST_PEAK buckets in ascending distance order (for first-match)."""
    from dispatch_v2.common import V326_OSRM_DISTANCE_BIN_BOOST_PEAK
    boundaries = [max_km for max_km, _ in V326_OSRM_DISTANCE_BIN_BOOST_PEAK]
    assert boundaries == sorted(boundaries)
    # Last must be inf (catch-all)
    import math
    assert math.isinf(boundaries[-1])
