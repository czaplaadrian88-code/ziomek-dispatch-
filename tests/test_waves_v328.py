"""V3.28 P2 — wave detection + inter-wave deadhead tests (Adrian doktryna 2026-05-10).

Wave = pickupy z `pickup_ready_at` w oknie ±12 min i pickup_coords ≤1.5 km od
poprzedniej w grupie. Bag z 1 falą = idealny ("linia/okrąg"). 2+ fal = OK gdy
inter-wave deadhead sensowny.

Score modifiers:
- n_waves == 1: bonus_wave_clean = +10
- n_waves >= 2 and max_inter_wave_deadhead > 4 km: penalty -3/km nadmiar
"""

from __future__ import annotations
import math
import pytest
from datetime import datetime, timezone, timedelta
from dispatch_v2 import feasibility_v2 as fv2
from dispatch_v2.feasibility_v2 import _detect_waves, _inter_wave_deadhead_km, check_feasibility_v2
from dispatch_v2.route_simulator_v2 import OrderSim


def _pure_haversine_km(a, b):
    """Deterministic distance — eliminuje OSRM cache state leak między testami."""
    if not a or not b:
        return 0.0
    lat1, lon1 = a
    lat2, lon2 = b
    R = 6371.0
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    h = math.sin(dlat / 2) ** 2 + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h)) * 1.4  # Białystok road factor


@pytest.fixture(autouse=True)
def _isolate_osrm_state(monkeypatch):
    """Wave detection wymaga deterministic _road_km — patching dla test isolation."""
    monkeypatch.setattr(fv2, "_road_km", _pure_haversine_km)


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _make_order(oid, p_lat, p_lon, d_lat, d_lon, ready, status="assigned"):
    return OrderSim(
        order_id=oid,
        pickup_coords=(p_lat, p_lon),
        delivery_coords=(d_lat, d_lon),
        pickup_ready_at=ready,
        picked_up_at=None,
        status=status,
    )


def test_one_wave_close_pickups_close_times():
    """3 ordery, pickupy w 1 km i ready_at w 6 min → 1 fala."""
    bag = [
        _make_order("B1", 53.13, 23.16, 53.16, 23.18, _utc("2026-05-10T13:00:00")),
        _make_order("B2", 53.135, 23.165, 53.17, 23.18, _utc("2026-05-10T13:05:00")),
    ]
    new_o = _make_order("NEW", 53.14, 23.17, 53.18, 23.19, _utc("2026-05-10T13:08:00"))
    waves = _detect_waves(bag, new_o)
    assert len(waves) == 1, f"Expected 1 wave, got {len(waves)}: {waves}"
    assert len(waves[0]) == 3


def test_two_waves_time_gap():
    """Adrian's przykład: fala 1 centrum (13:00-13:08), fala 2 po 50 min (13:50)."""
    bag = [
        _make_order("B1", 53.13, 23.16, 53.16, 23.18, _utc("2026-05-10T13:00:00")),
        _make_order("B2", 53.135, 23.165, 53.17, 23.18, _utc("2026-05-10T13:05:00")),
    ]
    # Fala 2 — pickup czas 50 min later (znacznie >12 min window)
    new_o = _make_order("NEW", 53.13, 23.16, 53.20, 23.20, _utc("2026-05-10T13:55:00"))
    waves = _detect_waves(bag, new_o)
    assert len(waves) == 2, f"Expected 2 waves, got {len(waves)}: {waves}"
    assert waves[0] == ["B1", "B2"]
    assert waves[1] == ["NEW"]


def test_two_waves_space_gap():
    """Pickupy z tym samym ready_at ale daleko geograficznie → 2 fale."""
    bag = [
        _make_order("B1", 53.13, 23.16, 53.16, 23.18, _utc("2026-05-10T13:00:00")),
    ]
    # Pickup nieco później ALE 5 km dalej (>1.5 km threshold)
    new_o = _make_order("NEW", 53.18, 23.20, 53.20, 23.22, _utc("2026-05-10T13:05:00"))
    waves = _detect_waves(bag, new_o)
    assert len(waves) == 2, f"Expected 2 waves due to spatial gap, got {len(waves)}"


def test_picked_up_excluded_from_waves():
    """Picked-up bag orders nie liczą się do wave detection."""
    bag = [
        OrderSim(
            order_id="B_PICKED",
            pickup_coords=(53.13, 23.16),
            delivery_coords=(53.16, 23.18),
            picked_up_at=_utc("2026-05-10T12:50:00"),
            status="picked_up",
            pickup_ready_at=_utc("2026-05-10T12:45:00"),
        ),
    ]
    new_o = _make_order("NEW", 53.14, 23.17, 53.18, 23.19, _utc("2026-05-10T13:05:00"))
    waves = _detect_waves(bag, new_o)
    # Picked-up wyłączony, tylko NEW liczy się — 1 fala z 1 elementem
    assert len(waves) == 1
    assert waves[0] == ["NEW"]


def test_inter_wave_deadhead_calc():
    """2 fale: deadhead między ostatnim drop fali 1 a pierwszym pickup fali 2."""
    o1 = _make_order("W1A", 53.13, 23.16, 53.16, 23.18, _utc("2026-05-10T13:00:00"))
    o2 = _make_order("W1B", 53.135, 23.165, 53.17, 23.19, _utc("2026-05-10T13:03:00"))
    o3 = _make_order("W2A", 53.20, 23.20, 53.22, 23.22, _utc("2026-05-10T14:00:00"))
    waves = [["W1A", "W1B"], ["W2A"]]
    total, mx, segs = _inter_wave_deadhead_km(waves, [o1, o2, o3])
    assert segs == 1
    assert mx > 0  # niezerowy deadhead
    assert total == mx


def test_metric_n_waves_in_feasibility():
    """check_feasibility_v2 emit metric n_waves + inter_wave_deadhead_*."""
    bag = [
        _make_order("B1", 53.13, 23.16, 53.16, 23.18, _utc("2026-05-10T13:00:00")),
    ]
    new_o = _make_order("NEW", 53.14, 23.17, 53.18, 23.19, _utc("2026-05-10T13:05:00"))
    courier_pos = (53.13, 23.16)
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=courier_pos,
        bag=bag,
        new_order=new_o,
        shift_end=_utc("2026-05-10T22:00:00"),
        shift_start=_utc("2026-05-10T08:00:00"),
        now=_utc("2026-05-10T13:53:00"),
    )
    for k in ("n_waves", "inter_wave_deadhead_total_km", "inter_wave_deadhead_max_km"):
        assert k in metrics, f"Missing metric {k}"


if __name__ == "__main__":
    import traceback
    tests = [
        test_one_wave_close_pickups_close_times,
        test_two_waves_time_gap,
        test_two_waves_space_gap,
        test_picked_up_excluded_from_waves,
        test_inter_wave_deadhead_calc,
        test_metric_n_waves_in_feasibility,
    ]
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS {t.__name__}")
        except AssertionError as e:
            print(f"  FAIL {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    import sys
    sys.exit(0 if failed == 0 else 1)
