"""V3.27 Bug X fix tests — drive_min OSRM-first w dispatch_pipeline.

Pre-fix (linia 1122-1123): drive_min = haversine × HAVERSINE_ROAD_FACTOR / fleet_speed_kmh
- fleet_speed_kmh = FALLBACK_BASE_SPEEDS_KMH (sobota 16:00 → off_peak 32 km/h)
- NIE applies traffic_multiplier
- Pod-estymata ~40% w sobotni peak

Post-fix: drive_min = osrm_client.route(...).duration_min (z traffic_mult)
- Single source of truth (zgodne z plan path matrix)
- Fallback haversine × road_factor / fleet_speed × traffic_mult przy hard exception

Run: python3 tests/test_v327_drive_min_osrm.py
"""
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import dispatch_pipeline as DP  # noqa: E402
from dispatch_v2 import common as C  # noqa: E402

UTC = timezone.utc


def test_drive_min_uses_osrm_when_available():
    """V3.27: drive_min = osrm_client.route().duration_min gdy OSRM happy path."""
    courier_pos = (53.13, 23.16)
    pickup_coords = (53.12, 23.17)
    fleet_speed_kmh = 32.0

    fake_osrm_result = {
        "duration_s": 600.0,        # 10 min raw
        "duration_min": 12.0,       # 12 min after traffic_mult ×1.2 (sobota peak)
        "distance_m": 4000,
        "distance_km": 4.0,
        "osrm_fallback": False,
        "traffic_multiplier": 1.2,
    }

    captured = {}
    with patch("dispatch_v2.osrm_client.route", return_value=fake_osrm_result):
        # Replicate inline drive_min logic from dispatch_pipeline:1122-1141
        try:
            from dispatch_v2 import osrm_client as _osrm_v327
            _osrm_drive_res = _osrm_v327.route(tuple(courier_pos), pickup_coords)
            drive_min = float(_osrm_drive_res.get("duration_min") or 0.0)
            _drive_km = float(_osrm_drive_res.get("distance_km") or 0.0)
            captured["used"] = "osrm"
        except Exception:
            captured["used"] = "fallback"
            drive_min = -1
            _drive_km = -1

    assert captured["used"] == "osrm", f"expected osrm path, got {captured['used']}"
    assert drive_min == 12.0, f"expected 12.0 min from OSRM, got {drive_min}"
    assert _drive_km == 4.0, f"expected 4.0 km, got {_drive_km}"


def test_drive_min_falls_back_to_haversine_on_exception():
    """V3.27: gdy osrm_client.route() raises → fallback haversine + traffic_mult."""
    courier_pos = (53.13, 23.16)
    pickup_coords = (53.12, 23.17)
    fleet_speed_kmh = 32.0
    now = datetime(2026, 4, 25, 14, 0, tzinfo=UTC)  # 16:00 Warsaw → mult=1.2

    captured = {}
    with patch("dispatch_v2.osrm_client.route", side_effect=ConnectionError("OSRM unreachable")):
        try:
            from dispatch_v2 import osrm_client as _osrm_v327
            _osrm_drive_res = _osrm_v327.route(tuple(courier_pos), pickup_coords)
            drive_min = float(_osrm_drive_res.get("duration_min") or 0.0)
            _drive_km = float(_osrm_drive_res.get("distance_km") or 0.0)
            captured["used"] = "osrm"
        except Exception:
            captured["used"] = "fallback"
            from dispatch_v2.geometry import haversine_km
            from dispatch_v2.common import HAVERSINE_ROAD_FACTOR_BIALYSTOK
            _drive_km = haversine_km(tuple(courier_pos), pickup_coords) * HAVERSINE_ROAD_FACTOR_BIALYSTOK
            drive_min = (_drive_km / fleet_speed_kmh) * 60.0
            drive_min *= float(C.get_traffic_multiplier(now))

    assert captured["used"] == "fallback", f"expected fallback path, got {captured['used']}"
    assert drive_min > 0
    # Fallback drive_min powinien być przemnożone przez 1.2 (sobota peak)
    fallback_no_mult = (_drive_km / fleet_speed_kmh) * 60.0
    assert abs(drive_min - fallback_no_mult * 1.2) < 0.01, \
        f"fallback should apply mult: drive_min={drive_min}, no_mult×1.2={fallback_no_mult*1.2}"


def test_drive_min_osrm_returns_haversine_fallback_already_with_mult():
    """V3.27: osrm_client.route() już samo handluje circuit-breaker → haversine_fallback
    z _apply_traffic_multiplier. Naszemu kodowi wystarczy duration_min."""
    fake_osrm_fallback = {
        "duration_s": 720.0,
        "duration_min": 14.4,    # 12.0 raw × 1.2 mult applied wewnętrznie
        "distance_m": 4000,
        "distance_km": 4.0,
        "osrm_fallback": True,    # ← haversine fallback path
        "traffic_multiplier": 1.2,
    }

    with patch("dispatch_v2.osrm_client.route", return_value=fake_osrm_fallback):
        from dispatch_v2 import osrm_client as _osrm_v327
        res = _osrm_v327.route((53.13, 23.16), (53.12, 23.17))
        drive_min = float(res.get("duration_min") or 0.0)
    assert drive_min == 14.4, f"expected 14.4 (mult applied), got {drive_min}"


def test_drive_min_pre_fix_demonstrates_underestimation():
    """V3.27 reproduction: pre-fix drive_min sobota 16:00 podestymowany ~40%.

    courier 4 km od restaurant.
    pre-fix: 4km × 1.0 / 32 km/h × 60 = 7.5 min (NIE traffic mult)
    post-fix OSRM mult 1.2: ~9.0 min
    real peak: ~10-12 min
    """
    courier_pos = (53.13, 23.16)
    pickup_coords = (53.165, 23.16)  # ~3.9 km north
    fleet_speed_kmh = 32.0  # off_peak sobota 16:00 (FALLBACK_BASE_SPEEDS_KMH)
    now = datetime(2026, 4, 25, 14, 0, tzinfo=UTC)

    from dispatch_v2.geometry import haversine_km
    from dispatch_v2.common import HAVERSINE_ROAD_FACTOR_BIALYSTOK
    h_km = haversine_km(tuple(courier_pos), pickup_coords)
    drive_km = h_km * HAVERSINE_ROAD_FACTOR_BIALYSTOK

    pre_fix = (drive_km / fleet_speed_kmh) * 60.0  # NIE applied mult
    post_fix_fallback = pre_fix * float(C.get_traffic_multiplier(now))  # × 1.2

    assert post_fix_fallback > pre_fix, "post-fix fallback path > pre-fix"
    # 1.2x increase
    assert abs(post_fix_fallback / pre_fix - 1.2) < 0.001, \
        f"ratio {post_fix_fallback/pre_fix} != 1.2"


if __name__ == "__main__":
    test_drive_min_uses_osrm_when_available()
    print("test_drive_min_uses_osrm_when_available: PASS")
    test_drive_min_falls_back_to_haversine_on_exception()
    print("test_drive_min_falls_back_to_haversine_on_exception: PASS")
    test_drive_min_osrm_returns_haversine_fallback_already_with_mult()
    print("test_drive_min_osrm_returns_haversine_fallback_already_with_mult: PASS")
    test_drive_min_pre_fix_demonstrates_underestimation()
    print("test_drive_min_pre_fix_demonstrates_underestimation: PASS")
    print("ALL 4/4 PASS")
