"""V3.28 P1 — R1 directionality + R5 pickup detour tests (Adrian doktryna 2026-05-10).

R1 corridor cosine bonus:
- avg_pairwise_cosine > 0.85 → +20 (tight corridor)
- 0.5..0.85 → +5 (good direction)
- 0..0.5 → 0 (neutral)
- -0.5..0 → -15 (orthogonal)
- < -0.5 → -40 (opposite split)

R5 pickup detour bonus:
- detour_per_order_km < 0.5 → 0 (po drodze)
- 0.5..1.5 → -5
- 1.5..3.0 → -15
- > 3.0 → -40
"""

from __future__ import annotations
from datetime import datetime, timezone
from dispatch_v2.feasibility_v2 import check_feasibility_v2
from dispatch_v2.route_simulator_v2 import OrderSim


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _make_order(oid, p_lat, p_lon, d_lat, d_lon, ready=None, status="assigned"):
    return OrderSim(
        order_id=oid,
        pickup_coords=(p_lat, p_lon),
        delivery_coords=(d_lat, d_lon),
        pickup_ready_at=ready or _utc("2026-05-10T13:50:00"),
        picked_up_at=None,
        status=status,
    )


def test_r1_tight_corridor_cosine_close_to_1():
    """3 drops na N (Nowe Miasto), all w tym samym kierunku → cosine > 0.85."""
    now = _utc("2026-05-10T13:53:00")
    courier_pos = (53.13, 23.16)  # centrum
    # 3 drops na N: lat 53.16, 53.17, 53.18 (różne dystanse, ten sam kierunek)
    bag = [
        _make_order("B1", 53.13, 23.16, 53.16, 23.16, ready=_utc("2026-05-10T13:48:00")),
        _make_order("B2", 53.13, 23.16, 53.17, 23.16, ready=_utc("2026-05-10T13:49:00")),
    ]
    new_o = _make_order("NEW", 53.13, 23.16, 53.18, 23.16, ready=_utc("2026-05-10T13:52:00"))

    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=courier_pos,
        bag=bag,
        new_order=new_o,
        shift_end=_utc("2026-05-10T22:00:00"),
        shift_start=_utc("2026-05-10T08:00:00"),
        now=now,
    )

    cos = metrics.get("r1_avg_pairwise_cosine")
    assert cos is not None, f"r1_avg_pairwise_cosine missing; metrics={list(metrics.keys())}"
    assert cos > 0.85, f"Expected tight corridor cosine > 0.85, got {cos}"


def test_r1_opposite_drops_cosine_negative():
    """Drops N i S z punktu courier = opposite → cosine ≈ -1."""
    now = _utc("2026-05-10T13:53:00")
    courier_pos = (53.13, 23.16)
    bag = [
        _make_order("B1", 53.13, 23.16, 53.20, 23.16, ready=_utc("2026-05-10T13:48:00")),  # N
    ]
    new_o = _make_order("NEW", 53.13, 23.16, 53.06, 23.16, ready=_utc("2026-05-10T13:52:00"))  # S

    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=courier_pos,
        bag=bag,
        new_order=new_o,
        shift_end=_utc("2026-05-10T22:00:00"),
        shift_start=_utc("2026-05-10T08:00:00"),
        now=now,
    )

    cos = metrics.get("r1_avg_pairwise_cosine")
    assert cos is not None
    assert cos < -0.5, f"Expected opposite cosine < -0.5, got {cos}"


def test_r5_pickup_detour_po_drodze_minimal():
    """2 pickups w jednym korytarzu → minimal detour."""
    now = _utc("2026-05-10T13:53:00")
    courier_pos = (53.13, 23.16)
    # B1 pickup very close to courier (53.135, 23.16), new pickup nieco dalej (53.14, 23.16) — po drodze
    bag = [
        _make_order("B1", 53.135, 23.16, 53.20, 23.16, ready=_utc("2026-05-10T13:48:00")),
    ]
    new_o = _make_order("NEW", 53.14, 23.16, 53.21, 23.16, ready=_utc("2026-05-10T13:52:00"))

    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=courier_pos,
        bag=bag,
        new_order=new_o,
        shift_end=_utc("2026-05-10T22:00:00"),
        shift_start=_utc("2026-05-10T08:00:00"),
        now=now,
    )

    detour = metrics.get("r5_pickup_detour_per_order_km")
    assert detour is not None, f"r5_pickup_detour_per_order_km missing; metrics={list(metrics.keys())}"
    assert detour < 1.5, f"Expected po-drodze detour < 1.5 km, got {detour}"


def test_r5_pickup_zigzag_high_detour():
    """2 pickups w przeciwnych kierunkach od kuriera → wysoki detour."""
    now = _utc("2026-05-10T13:53:00")
    courier_pos = (53.13, 23.16)
    # B1 pickup na N (~5 km), new pickup na S (~5 km) — courier musi jechać w obie strony
    bag = [
        _make_order("B1", 53.18, 23.16, 53.18, 23.18, ready=_utc("2026-05-10T13:48:00")),
    ]
    new_o = _make_order("NEW", 53.08, 23.16, 53.08, 23.18, ready=_utc("2026-05-10T13:52:00"))

    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=courier_pos,
        bag=bag,
        new_order=new_o,
        shift_end=_utc("2026-05-10T22:00:00"),
        shift_start=_utc("2026-05-10T08:00:00"),
        now=now,
    )

    detour = metrics.get("r5_pickup_detour_per_order_km")
    assert detour is not None
    # 5 km N + 5 km S courier-to-pickup → multi-route forces backtrack
    # Detour znacząco > po drodze case (>0.5 = soft penalty zone)
    assert detour > 0.5, f"Expected zigzag detour > 0.5 km, got {detour}"


def test_metric_names_persistent():
    """Smoke: verify metric keys exist in any feasibility call."""
    now = _utc("2026-05-10T13:53:00")
    courier_pos = (53.13, 23.16)
    bag = [_make_order("B1", 53.135, 23.165, 53.16, 23.18, ready=_utc("2026-05-10T13:48:00"))]
    new_o = _make_order("NEW", 53.14, 23.16, 53.17, 23.18, ready=_utc("2026-05-10T13:52:00"))

    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=courier_pos,
        bag=bag,
        new_order=new_o,
        shift_end=_utc("2026-05-10T22:00:00"),
        shift_start=_utc("2026-05-10T08:00:00"),
        now=now,
    )

    expected = ["r1_avg_pairwise_cosine", "r5_pickup_detour_total_km", "r5_pickup_detour_per_order_km"]
    for k in expected:
        assert k in metrics, f"Missing metric '{k}'; have: {sorted(metrics.keys())}"


if __name__ == "__main__":
    import traceback
    tests = [
        test_r1_tight_corridor_cosine_close_to_1,
        test_r1_opposite_drops_cosine_negative,
        test_r5_pickup_detour_po_drodze_minimal,
        test_r5_pickup_zigzag_high_detour,
        test_metric_names_persistent,
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
