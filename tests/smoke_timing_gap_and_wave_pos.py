"""Smoke test dla Fix 1 (timing_gap_bonus) + Fix 2 (last_wave_pos).

Uruchomienie: python3 -m dispatch_v2.tests.smoke_timing_gap_and_wave_pos

Testy — bez pytest, czysty Python + assert. Exit 0 = pass, 1 = fail.

Co weryfikuje:
  Fix 1: timing_gap_bonus poprawnie klasyfikuje gap na 5 tier'ów
         (+25/+15/+5/penalty>15/penalty<-15) + fallback pickup_ready=None → travel_min
  Fix 2: km_to_pickup liczony od effective_start_pos (last bag delivery_coords) dla bag,
         a od courier_pos dla solo; drive_min zawsze od courier_pos.
"""
from __future__ import annotations
import sys
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import dispatch_pipeline
from dispatch_v2.osrm_client import haversine
from dispatch_v2.common import HAVERSINE_ROAD_FACTOR_BIALYSTOK


# ---- test fleet builders (duck-typed; assess_order używa getattr) ----

@dataclass
class MockCourier:
    pos: Optional[Tuple[float, float]] = None
    bag: List[Dict[str, Any]] = field(default_factory=list)
    shift_end: Optional[datetime] = None
    pos_source: str = "gps"
    shift_start_min: Optional[float] = None
    name: str = "TestCourier"


def mk_bag_entry(order_id: str, pickup: Tuple[float, float], delivery: Tuple[float, float],
                 status: str = "assigned", restaurant: str = "TestR") -> Dict[str, Any]:
    return {
        "order_id": order_id,
        "pickup_coords": pickup,
        "delivery_coords": delivery,
        "status": status,
        "restaurant": restaurant,
        "picked_up_at": None,
        "pickup_ready_at": None,
    }


def make_order_event(
    oid: str,
    pickup: Tuple[float, float],
    delivery: Tuple[float, float],
    now_warsaw: datetime,
    pickup_minutes_ahead: float,
    restaurant: str = "TestPickupR",
) -> Dict[str, Any]:
    """pickup_at_warsaw = now + pickup_minutes_ahead (Warsaw TZ)."""
    from zoneinfo import ZoneInfo
    pu = now_warsaw + timedelta(minutes=pickup_minutes_ahead)
    pu_warsaw = pu.astimezone(ZoneInfo("Europe/Warsaw"))
    return {
        "order_id": oid,
        "restaurant": restaurant,
        "delivery_address": "Test Address 1",
        "pickup_coords": pickup,
        "delivery_coords": delivery,
        "pickup_at_warsaw": pu_warsaw.strftime("%Y-%m-%d %H:%M:%S"),
    }


# ---- test runners ----

FAILURES: List[str] = []


def check(cond: bool, msg: str) -> None:
    if cond:
        print(f"  ✓ {msg}")
    else:
        FAILURES.append(msg)
        print(f"  ✗ {msg}")


def test_fix1_timing_gap_buckets():
    """Fix 1: 5 buckets timing_gap_bonus + fallback pickup_ready=None.

    Scenariusz: kurier Z BAGIEM (żeby free_at_min > 0). Bag delivery ustawiony
    tak by simulator zwrócił deterministyczny free_at_min ≈ target_free.
    """
    print("\n[Fix 1] timing_gap_bonus buckets:")
    now = datetime(2026, 4, 15, 18, 0, 0, tzinfo=timezone.utc)

    # Bialystok center (approx)
    CENTER = (53.1325, 23.1688)
    # Pozycja ~2 km od centrum
    pos = (53.1500, 23.1688)

    # New pickup — ta sama lokacja co bag delivery (żeby km_to_pickup ≈ 0 dla bag case)
    # Bag: restaurant ~1 km od pos, delivery ~3 km od pos, oczekiwany free_at ≈ X min
    bag_restaurant = (53.1400, 23.1688)  # ~1 km
    bag_delivery = (53.1700, 23.1688)     # ~4 km

    # Scenariusze gap: free_at_min zależy od bag travel (symulator). Żeby było
    # deterministyczne, użyjemy picked_up_at=None + krótkiego bagu, i będziemy
    # zmieniać TYLKO pickup_minutes_ahead dla nowego ordera — free_at_min zostanie
    # stały (~x min), a gap = free_at - pickup_minutes_ahead.

    courier = MockCourier(
        pos=pos,
        bag=[mk_bag_entry("BAG1", bag_restaurant, bag_delivery, status="assigned",
                          restaurant="BagR")],
        pos_source="gps",
    )
    fleet = {"C1": courier}

    # NEW order pickup dalej od pos (żeby km_to_pickup > 0 i nie same-restaurant)
    new_pickup = (53.1900, 23.2000)
    new_delivery = (53.2100, 23.2200)

    # Run 1: pickup_minutes_ahead=20 → measure gap for different free_at
    event = make_order_event("NEW1", new_pickup, new_delivery, now, pickup_minutes_ahead=20,
                              restaurant="NewR")
    result = dispatch_pipeline.assess_order(event, fleet, restaurant_meta=None, now=now)
    assert result.candidates, "brak kandydatów"
    m = result.candidates[0].metrics
    free_at = m.get("free_at_min")
    ttpr = m.get("time_to_pickup_ready_min")
    gap = m.get("timing_gap_min")
    bonus = m.get("timing_gap_bonus")
    print(f"  (reference) free_at={free_at}, time_to_pickup_ready={ttpr}, gap={gap}, bonus={bonus}")

    # Bucket verification — manual gap probing
    # Buduję scenariusz gdzie gap jest predictable dzięki znanemu pickup_minutes_ahead.
    # free_at_min jest policzone przez symulator — fixuję scenariusz na jego wartości.
    free_at_ref = free_at

    # Scenariusz |gap| ≤ 5 → +25: pickup_minutes_ahead = free_at_ref (gap=0)
    for delta, expected_bonus in [
        (0.0, 25.0),     # gap=0 → +25
        (4.9, 25.0),     # gap=-4.9 → +25
        (-4.9, 25.0),    # gap=+4.9 → +25
        (-7.0, 15.0),    # gap=+7 → +15
        (7.0, 15.0),     # gap=-7 → +15
        (-13.0, 5.0),    # gap=+13 → +5
        (13.0, 5.0),     # gap=-13 → +5
    ]:
        pickup_ahead = free_at_ref + delta  # time_to_pickup_ready ≈ pickup_ahead
        event = make_order_event(f"T-{delta}", new_pickup, new_delivery, now,
                                  pickup_minutes_ahead=pickup_ahead, restaurant="NewR")
        res = dispatch_pipeline.assess_order(event, fleet, restaurant_meta=None, now=now)
        if not res.candidates:
            FAILURES.append(f"delta={delta}: brak kandydatów")
            continue
        mx = res.candidates[0].metrics
        actual_bonus = mx.get("timing_gap_bonus")
        actual_gap = mx.get("timing_gap_min")
        check(abs(actual_bonus - expected_bonus) < 0.01,
              f"gap≈{actual_gap} → bonus={actual_bonus} (expected {expected_bonus}) [delta={delta}]")

    # Scenariusz gap > 15 → -3/min. Potrzebujemy free_at > 15+pickup_ahead.
    # Bag z bardzo daleką delivery (~30 km N) → długi free_at (35+ min).
    far_courier = MockCourier(
        pos=pos,
        bag=[mk_bag_entry("FARBAG", bag_restaurant, (53.40, 23.50), status="assigned",
                          restaurant="BagR")],
        pos_source="gps",
    )
    far_fleet = {"CFAR": far_courier}
    event = make_order_event("T-big-positive", new_pickup, new_delivery, now,
                              pickup_minutes_ahead=5.0, restaurant="NewR")  # small pickup_ahead
    res = dispatch_pipeline.assess_order(event, far_fleet, restaurant_meta=None, now=now)
    if res.candidates:
        mx = res.candidates[0].metrics
        actual_gap = mx["timing_gap_min"]
        if actual_gap > 15:
            expected = -3.0 * (actual_gap - 15.0)
            # Tolerance 0.5 dla round-off (gap rounded do 1 dec, bonus z unrounded)
            check(abs(mx["timing_gap_bonus"] - expected) < 0.5,
                  f"gap={actual_gap:.1f} > 15 → bonus={mx['timing_gap_bonus']} (expected ~{expected:.1f}, tol 0.5)")
        else:
            FAILURES.append(f"test setup: gap={actual_gap} nie trafił do >15 bucket (free_at={mx['free_at_min']})")
            print(f"  ✗ test setup: gap={actual_gap} nie trafił >15 bucket")

    # Scenariusz gap < -15 → -2/min: pickup_ahead = free_at + 25 (gap≈-25)
    pickup_ahead = free_at_ref + 25.0
    event = make_order_event("T-big-negative", new_pickup, new_delivery, now,
                              pickup_minutes_ahead=pickup_ahead, restaurant="NewR")
    res = dispatch_pipeline.assess_order(event, fleet, restaurant_meta=None, now=now)
    if res.candidates:
        mx = res.candidates[0].metrics
        actual_gap = mx["timing_gap_min"]
        if actual_gap < -15:
            expected = -2.0 * (-actual_gap - 15.0)
            # Tolerance 0.5 dla round-off
            check(abs(mx["timing_gap_bonus"] - expected) < 0.5,
                  f"gap={actual_gap:.1f} < -15 → bonus={mx['timing_gap_bonus']} (expected ~{expected:.1f}, tol 0.5)")
        else:
            FAILURES.append(f"test setup: gap={actual_gap} nie trafił <-15 bucket")
            print(f"  ✗ test setup: gap={actual_gap} nie trafił <-15 bucket")


def test_fix2_last_wave_pos_km():
    """Fix 2: km_to_pickup od effective_start_pos (last bag delivery) dla bag case.

    Scenariusz: kurier w (A), bag delivery w (B), new pickup w (C).
      - z bagiem: km_to_pickup ≈ haversine(B, C) * 1.37
      - bez bagu: km_to_pickup ≈ haversine(A, C) * 1.37
    """
    print("\n[Fix 2] last_wave_pos → km_to_pickup:")
    now = datetime(2026, 4, 15, 18, 0, 0, tzinfo=timezone.utc)

    A = (53.1325, 23.1688)   # courier pos
    B = (53.1700, 23.2000)   # bag delivery (~4 km NE od A)
    C = (53.1800, 23.2200)   # new pickup (~1.6 km od B, ~5.5 km od A)
    D = (53.1900, 23.2400)   # new delivery

    # Z bagiem
    courier_bag = MockCourier(
        pos=A,
        bag=[mk_bag_entry("BAG1", (53.1350, 23.1700), B, status="assigned",
                          restaurant="BagR")],
        pos_source="gps",
    )
    event = make_order_event("NEW-bag", C, D, now, pickup_minutes_ahead=15, restaurant="NewR")
    res = dispatch_pipeline.assess_order(event, {"C1": courier_bag}, restaurant_meta=None, now=now)
    assert res.candidates, "brak kandydatów (bag case)"
    m = res.candidates[0].metrics
    km_bag = m.get("km_to_pickup")
    drive_bag = m.get("drive_min")

    expected_km_from_B = haversine(B, C) * HAVERSINE_ROAD_FACTOR_BIALYSTOK
    expected_km_from_A = haversine(A, C) * HAVERSINE_ROAD_FACTOR_BIALYSTOK
    check(abs(km_bag - expected_km_from_B) < 0.1,
          f"bag: km_to_pickup={km_bag} ≈ haversine(B,C)*1.37={expected_km_from_B:.2f} (NIE courier_pos A: {expected_km_from_A:.2f})")
    # Drive_min liczone od courier_pos A → dystans A-C większy niż B-C → drive_min
    # powinno być większe niż km_bag / speed. Sanity check:
    # (tu tylko sprawdzamy że drive_min > km_bag/60*60, czyli > ~5 min)
    check(drive_bag is not None and drive_bag > 0, f"bag: drive_min={drive_bag} > 0 (liczone od courier_pos)")

    # Bez bagu
    courier_solo = MockCourier(pos=A, bag=[], pos_source="gps")
    event = make_order_event("NEW-solo", C, D, now, pickup_minutes_ahead=15, restaurant="NewR")
    res = dispatch_pipeline.assess_order(event, {"C2": courier_solo}, restaurant_meta=None, now=now)
    assert res.candidates, "brak kandydatów (solo case)"
    m = res.candidates[0].metrics
    km_solo = m.get("km_to_pickup")
    check(abs(km_solo - expected_km_from_A) < 0.1,
          f"solo: km_to_pickup={km_solo} ≈ haversine(A,C)*1.37={expected_km_from_A:.2f} (effective==courier_pos)")


def test_fix1_fallback_pickup_ready_none():
    """Fix 1 fallback: pickup_ready_at=None → time_to_pickup_ready=travel_min.

    Trudne do wywołania z assess_order bo get_pickup_ready_at zwraca non-None
    gdy mamy pickup_at. Zamiast tego: test z pickup_at=None (brak pickup_time)
    → pickup_ready_at=None → fallback aktywowany.
    """
    print("\n[Fix 1] fallback pickup_ready=None → travel_min:")
    now = datetime(2026, 4, 15, 18, 0, 0, tzinfo=timezone.utc)
    A = (53.1325, 23.1688)
    C = (53.1800, 23.2200)
    D = (53.1900, 23.2400)

    courier = MockCourier(pos=A, bag=[], pos_source="gps")
    event = {
        "order_id": "NO-PICKUP-AT",
        "restaurant": "NoTimeR",
        "delivery_address": "NoTime Addr",
        "pickup_coords": C,
        "delivery_coords": D,
        # brak pickup_at_warsaw → get_pickup_ready_at zwróci None
    }
    res = dispatch_pipeline.assess_order(event, {"C1": courier}, restaurant_meta=None, now=now)
    assert res.candidates, "brak kandydatów (no pickup_at)"
    m = res.candidates[0].metrics
    ttpr = m.get("time_to_pickup_ready_min")
    tm = m.get("travel_min")
    check(ttpr == tm,
          f"fallback: time_to_pickup_ready_min={ttpr} == travel_min={tm} (pickup_ready=None)")


def main():
    print("=== SMOKE TEST Fix 1 + Fix 2 ===")
    test_fix1_timing_gap_buckets()
    test_fix2_last_wave_pos_km()
    test_fix1_fallback_pickup_ready_none()

    print("\n=== WYNIK ===")
    if FAILURES:
        print(f"FAIL: {len(FAILURES)} failure(s):")
        for f in FAILURES:
            print(f"  - {f}")
        sys.exit(1)
    print("PASS (wszystkie asercje OK)")


if __name__ == "__main__":
    main()
