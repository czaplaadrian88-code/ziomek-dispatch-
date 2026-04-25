"""V3.26 BUG-3 STEP 1 — OSRM traffic multiplier tests.

Standalone executable (sys.path insert pattern, parytet z test_route_simulator_c1).

Coverage:
  1. Helper weekday buckets — every Adrian table row returns expected mult.
  2. Helper weekend all day → 1.0.
  3. Helper boundary lower-inclusive (17:00 sharp → 1.2, NOT 1.6).
  4. Helper boundary upper-exclusive (16:59:59 → 1.6).
  5. Helper naive datetime raises TypeError.
  6. route() flag=False identity — duration_s unchanged, no raw fields added.
  7. route() flag=True — duration_s = raw × mult, osrm_raw_duration_s preserved.
  8. route() cache idempotency — re-fetch across hours re-multiplies from raw.
"""
import os
import sys
from datetime import datetime, timezone, timedelta
from unittest import mock

sys.path.insert(0, '/root/.openclaw/workspace/scripts')

from dispatch_v2 import common
from dispatch_v2 import osrm_client


WARSAW = common.WARSAW


def _utc_for_warsaw(year, month, day, hour, minute=0, second=0):
    """Build aware UTC datetime from a Warsaw-local wall clock."""
    local = datetime(year, month, day, hour, minute, second, tzinfo=WARSAW)
    return local.astimezone(timezone.utc)


# ─── Helper tests ──────────────────────────────────────────────────

def test_helper_weekday_buckets():
    """Each Adrian table weekday row maps to expected multiplier."""
    # 2026-04-20 is a Monday (weekday()==0)
    cases = [
        (5, 1.0),    # 00-06
        (7, 1.0),    # 06-08
        (9, 1.1),    # 08-10
        (11, 1.1),   # 10-12
        (12, 1.2),   # 12-13
        (14, 1.3),   # 13-15
        (16, 1.6),   # 15-17 peak
        (18, 1.2),   # 17-19
        (20, 1.1),   # 19-21
        (22, 1.0),   # 21-24
    ]
    for h, expected in cases:
        ts = _utc_for_warsaw(2026, 4, 20, h, 0)
        got = common.get_traffic_multiplier(ts)
        assert got == expected, f"Mon hour={h}: expected {expected}, got {got}"
    print("PASS test_helper_weekday_buckets")


def test_helper_weekend_all_day():
    """Saturday and Sunday all-day → 1.0."""
    # 2026-04-25 Sat (weekday()==5), 2026-04-26 Sun (weekday()==6)
    sat = _utc_for_warsaw(2026, 4, 25, 9, 0)
    sun = _utc_for_warsaw(2026, 4, 26, 16, 0)
    assert common.get_traffic_multiplier(sat) == 1.0
    assert common.get_traffic_multiplier(sun) == 1.0
    # Even at would-be peak hour 16:00
    sat_peak = _utc_for_warsaw(2026, 4, 25, 16, 0)
    assert common.get_traffic_multiplier(sat_peak) == 1.0
    print("PASS test_helper_weekend_all_day")


def test_helper_boundary_lower_inclusive():
    """17:00:00 sharp → 1.2 (z 17-19), nie 1.6 (z 15-17)."""
    ts = _utc_for_warsaw(2026, 4, 20, 17, 0, 0)
    assert common.get_traffic_multiplier(ts) == 1.2
    print("PASS test_helper_boundary_lower_inclusive")


def test_helper_boundary_upper_exclusive():
    """16:59:59 → 1.6 (still in 15-17)."""
    ts = _utc_for_warsaw(2026, 4, 20, 16, 59, 59)
    assert common.get_traffic_multiplier(ts) == 1.6
    print("PASS test_helper_boundary_upper_exclusive")


def test_helper_naive_raises():
    """Naive datetime (no tzinfo) → TypeError fail-fast."""
    naive = datetime(2026, 4, 20, 14, 0)
    try:
        common.get_traffic_multiplier(naive)
    except TypeError:
        print("PASS test_helper_naive_raises")
        return
    raise AssertionError("Expected TypeError for naive datetime")


# ─── route() tests ─────────────────────────────────────────────────

class _FakeURLResp:
    def __init__(self, payload):
        import json
        self._data = json.dumps(payload).encode()
    def read(self):
        return self._data
    def __enter__(self):
        return self
    def __exit__(self, *a):
        return False


def _patch_urlopen_returning(payload):
    return mock.patch.object(
        osrm_client.urllib.request, "urlopen",
        return_value=_FakeURLResp(payload),
    )


def _osrm_route_payload(duration_s=600, distance_m=4000):
    return {
        "code": "Ok",
        "routes": [{"duration": duration_s, "distance": distance_m}],
    }


def _reset_state():
    """Clear cache + reset stats + reset circuit breaker."""
    osrm_client._route_cache.clear()
    osrm_client._osrm_stats["calls_total"] = 0
    osrm_client._osrm_stats["calls_fallback"] = 0
    osrm_client._osrm_stats["circuit_opens"] = 0
    osrm_client._osrm_stats["traffic_mult_sum"] = 0.0
    osrm_client._osrm_stats["traffic_mult_calls"] = 0
    osrm_client._osrm_stats["traffic_mult_buckets"] = {}
    osrm_client._osrm_circuit_open_until = 0.0
    osrm_client._osrm_failures = 0


def test_route_flag_false_shadow_records_no_mutation():
    """Block 4D 2026-04-25: flag=False (shadow) records osrm_raw_* +
    traffic_multiplier_shadow + stats, ALE duration_s/min NIE zmienione.
    Continuous validation drift bez behavior change w produkcji."""
    _reset_state()
    with mock.patch.object(osrm_client, "ENABLE_V326_OSRM_TRAFFIC_MULTIPLIER", False):
        with _patch_urlopen_returning(_osrm_route_payload(duration_s=600)):
            r = osrm_client.route((53.13, 23.16), (53.10, 23.20), use_cache=False)
    # Duration_s NIE zmienione (no mutation w shadow)
    assert r["duration_s"] == 600, f"flag=False duration_s NIE zmienione (raw 600); got {r['duration_s']}"
    # Shadow fields recorded (Block 4D instrumentation)
    assert r.get("osrm_raw_duration_s") == 600, "osrm_raw_duration_s must be recorded in shadow"
    assert "traffic_multiplier_shadow" in r, "traffic_multiplier_shadow must be recorded in shadow"
    # Live key NIE w shadow result
    assert "traffic_multiplier" not in r, "flag=False MUST NOT inject 'traffic_multiplier' (live key)"
    # Stats inkrementowane ZAWSZE (drift validation regardless of flag)
    assert osrm_client._osrm_stats["traffic_mult_calls"] == 1, (
        "Block 4D: stats inkrementowane w shadow (continuous validation)"
    )
    print("PASS test_route_flag_false_shadow_records_no_mutation")


def test_route_flag_true_applies_and_preserves_raw():
    """flag=True at peak hour: duration_s = 600 × 1.6 = 960; raw preserved."""
    _reset_state()
    # Mock now_utc inside osrm_client.route via patching datetime.datetime.now
    peak_utc = _utc_for_warsaw(2026, 4, 20, 16, 0)  # Mon 16:00 → mult 1.6

    fake_dt = mock.MagicMock(wraps=datetime)
    fake_dt.now = mock.MagicMock(return_value=peak_utc)

    with mock.patch.object(osrm_client, "ENABLE_V326_OSRM_TRAFFIC_MULTIPLIER", True), \
         mock.patch.object(osrm_client, "datetime", fake_dt), \
         _patch_urlopen_returning(_osrm_route_payload(duration_s=600)):
        r = osrm_client.route((53.13, 23.16), (53.10, 23.20), use_cache=False)
    assert r["traffic_multiplier"] == 1.6, f"expected mult 1.6, got {r.get('traffic_multiplier')}"
    assert r["osrm_raw_duration_s"] == 600, f"raw should be 600, got {r['osrm_raw_duration_s']}"
    assert r["duration_s"] == 960.0, f"adjusted should be 960, got {r['duration_s']}"
    assert r["duration_min"] == 16.0, f"adjusted min should be 16.0, got {r['duration_min']}"
    assert r["osrm_raw_duration_min"] == 10.0, f"raw min should be 10.0, got {r['osrm_raw_duration_min']}"
    assert osrm_client._osrm_stats["traffic_mult_calls"] == 1
    print("PASS test_route_flag_true_applies_and_preserves_raw")


def test_route_cache_idempotency_across_hours():
    """Cache stores raw; re-fetch in different hour returns adjusted from RAW (no double-mult)."""
    _reset_state()
    # First call at off-peak (mult=1.0): caches raw
    off_peak_utc = _utc_for_warsaw(2026, 4, 20, 5, 0)  # Mon 05:00 → 1.0
    fake_dt_off = mock.MagicMock(wraps=datetime)
    fake_dt_off.now = mock.MagicMock(return_value=off_peak_utc)

    with mock.patch.object(osrm_client, "ENABLE_V326_OSRM_TRAFFIC_MULTIPLIER", True), \
         mock.patch.object(osrm_client, "datetime", fake_dt_off), \
         _patch_urlopen_returning(_osrm_route_payload(duration_s=600)):
        r1 = osrm_client.route((53.13, 23.16), (53.10, 23.20), use_cache=True)
    assert r1["duration_s"] == 600.0, f"off-peak: 600 × 1.0 = 600, got {r1['duration_s']}"
    assert r1["traffic_multiplier"] == 1.0

    # Cache now contains RAW (no traffic fields, by design — _cache_set stores PRE-multiplier copy)
    cached_keys = list(osrm_client._route_cache.values())
    assert len(cached_keys) == 1, "exactly one cache entry"
    cached_dict = cached_keys[0][1]
    # NOTE: cache stored the original `result` dict, then route() applied multiplier
    # to a COPY. The original dict in cache was mutated only if not copied.
    # We expect cache holds RAW because _apply_traffic_multiplier received dict(result),
    # leaving cache entry untouched.
    assert cached_dict.get("duration_s") == 600, (
        f"cache must hold RAW 600, got {cached_dict.get('duration_s')}"
    )

    # Second call at PEAK (mult=1.6): should adjust from RAW 600 → 960 (NOT 600×1.0×1.6 either way; correct = 960)
    peak_utc = _utc_for_warsaw(2026, 4, 20, 16, 0)
    fake_dt_peak = mock.MagicMock(wraps=datetime)
    fake_dt_peak.now = mock.MagicMock(return_value=peak_utc)

    with mock.patch.object(osrm_client, "ENABLE_V326_OSRM_TRAFFIC_MULTIPLIER", True), \
         mock.patch.object(osrm_client, "datetime", fake_dt_peak):
        r2 = osrm_client.route((53.13, 23.16), (53.10, 23.20), use_cache=True)
    assert r2["traffic_multiplier"] == 1.6, f"peak: expected mult 1.6, got {r2['traffic_multiplier']}"
    assert r2["osrm_raw_duration_s"] == 600, f"raw still 600, got {r2['osrm_raw_duration_s']}"
    assert r2["duration_s"] == 960.0, (
        f"peak from cache: 600 × 1.6 = 960, got {r2['duration_s']} "
        f"(double-mult bug would give 600×1.6×1.6=1536)"
    )
    print("PASS test_route_cache_idempotency_across_hours")


# ─── Run all ──────────────────────────────────────────────────────

if __name__ == "__main__":
    tests = [
        test_helper_weekday_buckets,
        test_helper_weekend_all_day,
        test_helper_boundary_lower_inclusive,
        test_helper_boundary_upper_exclusive,
        test_helper_naive_raises,
        test_route_flag_false_shadow_records_no_mutation,
        test_route_flag_true_applies_and_preserves_raw,
        test_route_cache_idempotency_across_hours,
    ]
    failed = 0
    for t in tests:
        try:
            t()
        except Exception as e:
            print(f"FAIL {t.__name__}: {e}")
            failed += 1
    print(f"\n{'='*50}")
    print(f"Total: {len(tests)} tests, {len(tests) - failed} PASS, {failed} FAIL")
    sys.exit(1 if failed else 0)
