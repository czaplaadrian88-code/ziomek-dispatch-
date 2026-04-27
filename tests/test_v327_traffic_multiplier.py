"""V3.27 Bug X fix tests — get_traffic_multiplier weekend buckets (sat/sun split).

Pre-fix: weekend = 1.0 flat → matrix = raw OSRM free-flow w sobotni peak 16-21
→ #468508/#468509 30-50% pod-estymata timing.

Post-fix:
- saturday: peak 12-21 max ×1.2 (Adrian's conservative table)
- sunday: flat ×1.0 (drogi puste)

Run: python3 tests/test_v327_traffic_multiplier.py
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import common as C  # noqa: E402

WARSAW = ZoneInfo("Europe/Warsaw")
UTC = timezone.utc


def _wsa(year, month, day, hh, mm=0):
    """Build aware UTC datetime from Warsaw local hh:mm."""
    local = datetime(year, month, day, hh, mm, tzinfo=WARSAW)
    return local.astimezone(UTC)


def test_weekday_buckets_unchanged():
    """V3.27.3 TASK G update 2026-04-27: 5 buckets adjusted (Adrian's domain
    knowledge). Test name historical, weryfikuje POST-TASK G values."""
    # Wednesday = 22 kwietnia 2026 (weekday 2)
    cases = [
        (5, 1.0),    # 0-6
        (7, 1.0),    # 6-8
        (9, 1.1),    # 8-10
        (11, 1.1),   # 10-12
        (12, 1.2),   # 12-13
        (13, 1.2),   # 13-14 (TASK G: was 1.3)
        (14, 1.2),   # 14-15 (TASK G: was 1.3)
        (15, 1.5),   # 15-16 (TASK G: was 1.6)
        (16, 1.3),   # 16-17 (TASK G: was 1.6, largest delta)
        (18, 1.2),   # 17-19
        (19, 1.1),   # 19-20
        (20, 1.0),   # 20-21 (TASK G: was 1.1)
        (22, 1.0),   # 21-24
    ]
    for h, expected in cases:
        dt = _wsa(2026, 4, 22, h)  # Wednesday
        actual = C.get_traffic_multiplier(dt)
        assert actual == expected, f"weekday h={h}: expected {expected}, got {actual}"


def test_saturday_peak_12_21():
    """V3.27 sobota peak buckety: 00-12=1.0, 12-15=1.1, 15-17=1.2, 17-21=1.2, 21-24=1.0."""
    # Saturday 25.04.2026 (weekday 5) — proposal #468508/#468509 reproduction
    cases = [
        (0, 1.0),    # 00-12
        (8, 1.0),    # 00-12
        (11, 1.0),   # 00-12 (boundary just before)
        (12, 1.1),   # 12-15
        (14, 1.1),   # 12-15
        (15, 1.2),   # 15-17
        (16, 1.2),   # 15-17 — #468508/#468509 reproduction case
        (17, 1.2),   # 17-21
        (20, 1.2),   # 17-21
        (21, 1.0),   # 21-24
        (23, 1.0),   # 21-24
    ]
    for h, expected in cases:
        dt = _wsa(2026, 4, 25, h)  # Saturday
        actual = C.get_traffic_multiplier(dt)
        assert actual == expected, f"saturday h={h}: expected {expected}, got {actual}"


def test_sunday_flat_1_0():
    """V3.27 niedziela płaska 1.0 całą dobę — drogi puste."""
    cases = [(0, 1.0), (8, 1.0), (12, 1.0), (16, 1.0), (20, 1.0), (23, 1.0)]
    for h, expected in cases:
        dt = _wsa(2026, 4, 26, h)  # Sunday
        actual = C.get_traffic_multiplier(dt)
        assert actual == expected, f"sunday h={h}: expected {expected}, got {actual}"


def test_naive_datetime_raises():
    """Fail-fast na naive datetime (parytet z get_time_bucket)."""
    naive = datetime(2026, 4, 25, 16, 0)  # no tzinfo
    try:
        C.get_traffic_multiplier(naive)
    except TypeError as e:
        assert "aware" in str(e).lower()
        return
    raise AssertionError("Expected TypeError for naive datetime")


def test_boundary_inclusive_lower_exclusive_upper():
    """V3.27 bucket convention [lo, hi) — lower inclusive, upper exclusive.
    Sobota 12:00 sharp → 1.1 (12-15), 15:00 sharp → 1.2 (15-17).
    """
    dt_12 = _wsa(2026, 4, 25, 12, 0)
    assert C.get_traffic_multiplier(dt_12) == 1.1, "sobota 12:00 sharp = 1.1"
    dt_15 = _wsa(2026, 4, 25, 15, 0)
    assert C.get_traffic_multiplier(dt_15) == 1.2, "sobota 15:00 sharp = 1.2"
    dt_17 = _wsa(2026, 4, 25, 17, 0)
    assert C.get_traffic_multiplier(dt_17) == 1.2, "sobota 17:00 sharp = 1.2 (17-21)"
    dt_21 = _wsa(2026, 4, 25, 21, 0)
    assert C.get_traffic_multiplier(dt_21) == 1.0, "sobota 21:00 sharp = 1.0 (21-24)"


def test_table_structure_consistency():
    """V3.27: weekday/saturday/sunday all list-based [(lo, hi, mult), ...]."""
    for key in ("weekday", "saturday", "sunday"):
        assert key in C.V326_OSRM_TRAFFIC_TABLE, f"missing key '{key}'"
        table = C.V326_OSRM_TRAFFIC_TABLE[key]
        assert isinstance(table, list), f"'{key}' must be list (got {type(table)})"
        assert len(table) >= 1, f"'{key}' empty"
        # Coverage 0-24
        prev_hi = 0
        for lo, hi, mult in table:
            assert lo == prev_hi, f"'{key}': gap or overlap at {lo} (prev_hi={prev_hi})"
            assert hi > lo, f"'{key}': inverted bucket {lo}-{hi}"
            assert isinstance(mult, (int, float)) and mult >= 1.0
            prev_hi = hi
        assert prev_hi == 24, f"'{key}': table doesn't cover full day (ends at {prev_hi})"


def test_proposal_468508_reproduction():
    """#468508 reproduction: sobota 14:00 UTC = 16:00 Warsaw → mult=1.2 (NIE 1.0).
    Pre-fix matrix was raw OSRM free-flow → 30% pod-estymata.
    Post-fix matrix × 1.2 → reflects sobotni peak."""
    dt = datetime(2026, 4, 25, 14, 0, tzinfo=UTC)  # 16:00 Warsaw Saturday
    actual = C.get_traffic_multiplier(dt)
    assert actual == 1.2, f"#468508 case (sobota 16:00 Warsaw): expected 1.2, got {actual}"


if __name__ == "__main__":
    test_weekday_buckets_unchanged()
    print("test_weekday_buckets_unchanged: PASS")
    test_saturday_peak_12_21()
    print("test_saturday_peak_12_21: PASS")
    test_sunday_flat_1_0()
    print("test_sunday_flat_1_0: PASS")
    test_naive_datetime_raises()
    print("test_naive_datetime_raises: PASS")
    test_boundary_inclusive_lower_exclusive_upper()
    print("test_boundary_inclusive_lower_exclusive_upper: PASS")
    test_table_structure_consistency()
    print("test_table_structure_consistency: PASS")
    test_proposal_468508_reproduction()
    print("test_proposal_468508_reproduction: PASS")
    print("ALL 7/7 PASS")
