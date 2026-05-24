"""F5 RETURN-TO-RESTAURANT (2026-05-24).

detect_return_to_restaurant: zakazany powrót do tej samej restauracji niosąc jej
dowóz (Case B korpusu 475698). Commit-aware (czas_kuriera wcześniejszego zlecenia
wymusza osobną wizytę, której plan-ETA nie widzi).
"""
from datetime import datetime, timezone, timedelta

from dispatch_v2.feasibility_v2 import detect_return_to_restaurant
from dispatch_v2.route_simulator_v2 import OrderSim, RoutePlanV2

R = (53.1350, 23.1490)          # restauracja R (Retrospekcja)
R_DROP_A = (53.1450, 23.1530)   # dostawa B (Piłsudskiego-ish)
R_DROP_N = (53.1300, 23.1700)   # dostawa new (sudecka-ish)
FAR = (53.1000, 23.2200)        # inna restauracja
NOW = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)


def _ord(oid, pickup, drop, ck_warsaw=None, picked_up_at=None, status="assigned"):
    o = OrderSim(order_id=oid, pickup_coords=pickup, delivery_coords=drop,
                 picked_up_at=picked_up_at, status=status)
    o.czas_kuriera_warsaw = ck_warsaw
    return o


def _plan(pickup_at, delivered_at):
    return RoutePlanV2(sequence=list(delivered_at.keys()),
                       predicted_delivered_at=delivered_at, pickup_at=pickup_at,
                       total_duration_min=30.0, strategy="ortools",
                       sla_violations=0, osrm_fallback_used=False)


def test_case_b_forbidden_return():
    """B z R commit 14:50, new z R odbiór 15:01, B doręczany 15:11 → ZAKAZANE."""
    # commit Warsaw (+02:00): 14:50 = 12:50 UTC; new pickup 13:01 UTC; B deliv 13:11 UTC
    b = _ord("B", R, R_DROP_A, ck_warsaw="2026-05-24T14:50:00+02:00")
    n = _ord("N", R, R_DROP_N)
    plan = _plan(pickup_at={"N": NOW + timedelta(minutes=61)},          # 13:01
                 delivered_at={"B": NOW + timedelta(minutes=71),        # 13:11 (po N)
                               "N": NOW + timedelta(minutes=82)})
    assert detect_return_to_restaurant([b], n, plan) == "B"


def test_groupable_single_visit_ok():
    """B commit 13:00, new 13:01 (luka 1min < tol) → jedna wizyta, None."""
    b = _ord("B", R, R_DROP_A, ck_warsaw="2026-05-24T15:00:00+02:00")  # 13:00 UTC
    n = _ord("N", R, R_DROP_N)
    plan = _plan(pickup_at={"N": NOW + timedelta(minutes=61)},          # 13:01
                 delivered_at={"B": NOW + timedelta(minutes=71),
                               "N": NOW + timedelta(minutes=82)})
    assert detect_return_to_restaurant([b], n, plan) is None


def test_different_restaurant_ok():
    """B z innej restauracji (daleko) → None."""
    b = _ord("B", FAR, R_DROP_A, ck_warsaw="2026-05-24T14:50:00+02:00")
    n = _ord("N", R, R_DROP_N)
    plan = _plan(pickup_at={"N": NOW + timedelta(minutes=61)},
                 delivered_at={"B": NOW + timedelta(minutes=71),
                               "N": NOW + timedelta(minutes=82)})
    assert detect_return_to_restaurant([b], n, plan) is None


def test_b_delivered_before_return_ok():
    """B doręczony 12:55 PRZED odbiorem new 13:01 → R wyczyszczony, None (Adrian:
    może wrócić bez ich dowozów)."""
    b = _ord("B", R, R_DROP_A, ck_warsaw="2026-05-24T14:50:00+02:00")
    n = _ord("N", R, R_DROP_N)
    plan = _plan(pickup_at={"N": NOW + timedelta(minutes=61)},          # 13:01
                 delivered_at={"B": NOW + timedelta(minutes=55),        # 12:55 (przed N)
                               "N": NOW + timedelta(minutes=82)})
    assert detect_return_to_restaurant([b], n, plan) is None


def test_already_picked_up_forbidden():
    """B już picked_up (osobna wcześniejsza wizyta), wciąż w bagu na powrocie → ZAKAZANE."""
    b = _ord("B", R, R_DROP_A, picked_up_at=NOW + timedelta(minutes=50),
             status="picked_up")
    n = _ord("N", R, R_DROP_N)
    plan = _plan(pickup_at={"N": NOW + timedelta(minutes=61)},
                 delivered_at={"B": NOW + timedelta(minutes=71),
                               "N": NOW + timedelta(minutes=82)})
    assert detect_return_to_restaurant([b], n, plan) == "B"


def test_no_plan_pickup_for_new_returns_none():
    b = _ord("B", R, R_DROP_A, ck_warsaw="2026-05-24T14:50:00+02:00")
    n = _ord("N", R, R_DROP_N)
    plan = _plan(pickup_at={}, delivered_at={"B": NOW + timedelta(minutes=71)})
    assert detect_return_to_restaurant([b], n, plan) is None
