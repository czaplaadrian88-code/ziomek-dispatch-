"""V3.27 Bug X fix tests — get_traffic_multiplier weekend buckets (sat/sun split).

Pre-fix: weekend = 1.0 flat → matrix = raw OSRM free-flow w sobotni peak 16-21
→ #468508/#468509 30-50% pod-estymata timing.

Post-fix V3.27: saturday peak 12-21 max ×1.2, sunday flat ×1.0.
RECALIB WEEKEND 2026-06-12 (smoothed, GATE B): saturday peak 16-17 ×1.55,
sunday lunch/popołudnie 1.15-1.50 (recalib_weekend_verdict_2026-06-05.txt
+ validate_weekend_smoothed.py).

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
    """RECALIB 2026-06-05 (wariant B): krzywa godzinowa median-based zastąpiła
    statyczną tabelę V3.27.3 TASK G. Test name historical, weryfikuje POST-RECALIB
    values (recalib_verdict_B_2026-06-05.txt). Weekend (sat/sun) nietknięty."""
    # Wednesday = 22 kwietnia 2026 (weekday 2)
    cases = [
        (5, 1.0),     # 0-9
        (7, 1.0),     # 0-9
        (9, 1.15),    # 9-10 (recalib: was 1.1)
        (11, 1.25),   # 10-12 (recalib: was 1.1)
        (12, 1.40),   # 12-13 (recalib: was 1.2)
        (13, 1.50),   # 13-14 (recalib: was 1.2)
        (14, 1.35),   # 14-15 (recalib: was 1.2)
        (15, 1.55),   # 15-17 (recalib: was 1.5)
        (16, 1.55),   # 15-17 (recalib: was 1.3)
        (18, 1.25),   # 18-19 wariant B (doc-curve 1.35)
        (19, 1.25),   # 19-20 (recalib: was 1.1)
        (20, 1.10),   # 20-21 (recalib: was 1.0)
        (22, 1.05),   # 21-24 (recalib: was 1.0)
    ]
    for h, expected in cases:
        dt = _wsa(2026, 4, 22, h)  # Wednesday
        actual = C.get_traffic_multiplier(dt)
        assert actual == expected, f"weekday h={h}: expected {expected}, got {actual}"


def test_saturday_peak_12_21():
    """RECALIB WEEKEND 2026-06-12 sobota (smoothed): 00-12=1.0, 12-13=1.3,
    13-16=1.2, 16-17=1.55, 17-18=1.45, 18-21=1.25, 21-22=1.1, 22-24=1.0."""
    # Saturday 25.04.2026 (weekday 5)
    cases = [
        (0, 1.0),     # 00-12
        (8, 1.0),     # 00-12
        (11, 1.0),    # 00-12 (boundary just before)
        (12, 1.30),   # 12-13
        (14, 1.20),   # 13-16
        (15, 1.20),   # 13-16
        (16, 1.55),   # 16-17 — peak (#468508/#468509 case, było 1.2)
        (17, 1.45),   # 17-18
        (20, 1.25),   # 18-21
        (21, 1.10),   # 21-22
        (23, 1.0),    # 22-24
    ]
    for h, expected in cases:
        dt = _wsa(2026, 4, 25, h)  # Saturday
        actual = C.get_traffic_multiplier(dt)
        assert actual == expected, f"saturday h={h}: expected {expected}, got {actual}"


def test_sunday_flat_1_0():
    """RECALIB WEEKEND 2026-06-12 niedziela: NIE-płaska — lunch/popołudnie realnie
    obciążone (stara flat 1.0 zaniżała do bias −3.96 OOS). Nazwa funkcji zachowana
    dla ciągłości historii uruchomień."""
    cases = [(0, 1.0), (8, 1.0), (11, 1.50), (12, 1.40), (16, 1.30), (19, 1.15),
             (20, 1.0), (23, 1.0)]
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
    RECALIB WEEKEND 06-12: sobota 12:00 sharp → 1.3 (12-13), 13:00 sharp → 1.2 (13-16).
    """
    dt_12 = _wsa(2026, 4, 25, 12, 0)
    assert C.get_traffic_multiplier(dt_12) == 1.30, "sobota 12:00 sharp = 1.30 (12-13)"
    dt_13 = _wsa(2026, 4, 25, 13, 0)
    assert C.get_traffic_multiplier(dt_13) == 1.20, "sobota 13:00 sharp = 1.20 (13-16)"
    dt_17 = _wsa(2026, 4, 25, 17, 0)
    assert C.get_traffic_multiplier(dt_17) == 1.45, "sobota 17:00 sharp = 1.45 (17-18)"
    dt_22 = _wsa(2026, 4, 25, 22, 0)
    assert C.get_traffic_multiplier(dt_22) == 1.0, "sobota 22:00 sharp = 1.0 (22-24)"


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
    """#468508 reproduction: sobota 14:00 UTC = 16:00 Warsaw → mult>1.0 (NIE 1.0).
    Pre-V3.27 matrix was raw OSRM free-flow → 30% pod-estymata; V3.27 dało 1.2,
    RECALIB WEEKEND 06-12 (median-based) → 1.55 (bucket 16-17)."""
    dt = datetime(2026, 4, 25, 14, 0, tzinfo=UTC)  # 16:00 Warsaw Saturday
    actual = C.get_traffic_multiplier(dt)
    assert actual == 1.55, f"#468508 case (sobota 16:00 Warsaw): expected 1.55, got {actual}"


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
