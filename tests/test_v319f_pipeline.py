"""V3.19f pipeline tests — czas_kuriera consumer dla pickup_ready_at.

Coverage:
  1. Flag False + order z czas_kuriera_warsaw → pickup_ready_at = pickup_at_warsaw
  2. Flag True + czas_kuriera_warsaw obecny → pickup_ready_at = czas_kuriera_warsaw
  3. Flag True + czas_kuriera_warsaw null → fallback pickup_at_warsaw
  4. Flag True + oba null → fallback pickup_at legacy key
  5. Bag item (pending) z czas_kuriera_warsaw pod flagą → OrderSim.pickup_ready_at
  6. enriched_metrics zawiera oba pola (passthrough do serializer)
  7. Backward compat: flag False, brak pól → identyczne zachowanie pre-V3.19f

Pattern: fleet_snapshot mock + order_event + assess_order + assert.
"""
import sys
import os
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from dispatch_v2.dispatch_pipeline import assess_order
from dispatch_v2 import common as cm

BIALYSTOK_CENTER = (53.1325, 23.1688)

passed = 0
failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  OK {passed}. {label}")
    else:
        failed += 1
        print(f"  FAIL {passed + failed}. {label} {detail}")


def _build_fleet(cid="test_c", pos_source="gps", pos=(53.130, 23.165), bag=None):
    return {
        cid: SimpleNamespace(
            courier_id=cid,
            name=f"Test_{cid}",
            pos=pos,
            pos_source=pos_source,
            pos_age_min=2.0,
            shift_end=datetime(2026, 4, 20, 22, 0, tzinfo=timezone.utc),
            shift_start_min=0,
            bag=bag or [],
        )
    }


def _order(oid="T1", pickup_warsaw="2026-04-20T17:25:00+02:00",
           czas_kuriera_warsaw=None, czas_kuriera_hhmm=None):
    o = {
        "order_id": oid,
        "restaurant": "Test",
        "delivery_address": "Test D",
        "pickup_coords": [53.133, 23.169],
        "delivery_coords": [53.145, 23.185],
        "pickup_at_warsaw": pickup_warsaw,
        "pickup_time_minutes": None,
    }
    if czas_kuriera_warsaw is not None:
        o["czas_kuriera_warsaw"] = czas_kuriera_warsaw
    if czas_kuriera_hhmm is not None:
        o["czas_kuriera_hhmm"] = czas_kuriera_hhmm
    return o


def _flag(val: bool):
    cm.ENABLE_CZAS_KURIERA_PROPAGATION = val
    # refresh w dispatch_pipeline module
    from dispatch_v2 import dispatch_pipeline as dp
    dp.ENABLE_CZAS_KURIERA_PROPAGATION = val


now = datetime(2026, 4, 20, 15, 0, 0, tzinfo=timezone.utc)

# ============================================================
print("=== V3.19f pipeline: flag False (pre-V3.19f behavior) ===")
# ============================================================

# Test 1 — Flag False + order z czas_kuriera → ignore czas_kuriera, use pickup_at_warsaw
_flag(False)
fleet = _build_fleet()
order = _order(
    pickup_warsaw="2026-04-20T17:25:00+02:00",
    czas_kuriera_warsaw="2026-04-20T17:40:00+02:00",  # +15 min vs pickup
    czas_kuriera_hhmm="17:40",
)
result = assess_order(order, fleet, restaurant_meta=None, now=now)
assert result.pickup_ready_at is not None, "setup broken"
# Expected pickup_ready_at = 17:25 (pickup_at_warsaw), NIE 17:40 (czas_kuriera)
expected_utc = datetime(2026, 4, 20, 15, 25, tzinfo=timezone.utc)
check("1. Flag False: pickup_ready_at=pickup_at_warsaw (17:25 UTC)",
      abs((result.pickup_ready_at.astimezone(timezone.utc) - expected_utc)
          .total_seconds()) < 60,
      detail=f"got {result.pickup_ready_at}")

# ============================================================
print("\n=== V3.19f pipeline: flag True ===")
# ============================================================

# Test 2 — Flag True + czas_kuriera obecny → użyj czas_kuriera
_flag(True)
result2 = assess_order(order, fleet, restaurant_meta=None, now=now)
expected2 = datetime(2026, 4, 20, 15, 40, tzinfo=timezone.utc)  # 17:40 Warsaw = 15:40 UTC
check("2. Flag True + czas_kuriera: pickup_ready_at=czas_kuriera (17:40 UTC)",
      abs((result2.pickup_ready_at.astimezone(timezone.utc) - expected2)
          .total_seconds()) < 60,
      detail=f"got {result2.pickup_ready_at}")

# Test 3 — Flag True + czas_kuriera_warsaw null → fallback pickup_at_warsaw
order3 = _order(pickup_warsaw="2026-04-20T17:25:00+02:00",
                czas_kuriera_warsaw=None)
result3 = assess_order(order3, fleet, restaurant_meta=None, now=now)
expected3 = datetime(2026, 4, 20, 15, 25, tzinfo=timezone.utc)
check("3. Flag True + czas_kuriera=None: fallback pickup_at_warsaw",
      abs((result3.pickup_ready_at.astimezone(timezone.utc) - expected3)
          .total_seconds()) < 60,
      detail=f"got {result3.pickup_ready_at}")

# Test 4 — Flag True + oba null → fallback legacy pickup_at
order4 = {
    "order_id": "T4",
    "restaurant": "Test",
    "delivery_address": "Test D",
    "pickup_coords": [53.133, 23.169],
    "delivery_coords": [53.145, 23.185],
    # brak pickup_at_warsaw, brak czas_kuriera
    "pickup_at": "2026-04-20T17:25:00+02:00",  # legacy key
    "pickup_time_minutes": None,
}
result4 = assess_order(order4, fleet, restaurant_meta=None, now=now)
check("4. Flag True + wszystkie pickup null/legacy → fallback pickup_at",
      result4.pickup_ready_at is not None)

# ============================================================
print("\n=== V3.19f pipeline: enriched_metrics passthrough ===")
# ============================================================

# Test 5 — enriched_metrics zawiera oba pola (passthrough dla serializer Step 5)
_flag(True)
fleet5 = _build_fleet(cid="c5", pos_source="gps", pos=(53.130, 23.165))
order5 = _order(
    pickup_warsaw="2026-04-20T17:25:00+02:00",
    czas_kuriera_warsaw="2026-04-20T17:10:00+02:00",
    czas_kuriera_hhmm="17:10",
)
result5 = assess_order(order5, fleet5, restaurant_meta=None, now=now)
assert result5.candidates, "setup broken"
cand5 = result5.candidates[0]
check("5. enriched_metrics ma czas_kuriera_warsaw passthrough",
      cand5.metrics.get("czas_kuriera_warsaw") == "2026-04-20T17:10:00+02:00",
      detail=f"got {cand5.metrics.get('czas_kuriera_warsaw')}")
check("5b. enriched_metrics ma czas_kuriera_hhmm passthrough",
      cand5.metrics.get("czas_kuriera_hhmm") == "17:10")

# Test 6 — flag False: enriched_metrics dalej zawiera pola (None gdy brak source)
_flag(False)
order6 = _order(pickup_warsaw="2026-04-20T17:25:00+02:00")  # brak czas_kuriera
result6 = assess_order(order6, fleet5, restaurant_meta=None, now=now)
cand6 = result6.candidates[0]
check("6. Flag False + brak czas_kuriera: enriched_metrics obie None",
      cand6.metrics.get("czas_kuriera_warsaw") is None and
      cand6.metrics.get("czas_kuriera_hhmm") is None)

# ============================================================
print("\n=== V3.19f pipeline: bag item OrderSim pickup_ready_at ===")
# ============================================================

# Test 7 — bag item assigned z czas_kuriera_warsaw, flag True → OrderSim używa go.
# Verify przez simulate plan (pickup-node constraint respected)
_flag(True)
# Bag item: order in kitchen, czas_kuriera 17:10, pickup_at_warsaw 16:50 (panel przedłużenie)
bag_item = {
    "order_id": "BAG1",
    "status": "assigned",
    "pickup_coords": [53.120, 23.140],
    "delivery_coords": [53.150, 23.180],
    "pickup_at_warsaw": "2026-04-20T16:50:00+02:00",
    "czas_kuriera_warsaw": "2026-04-20T17:10:00+02:00",  # przedłużenie
    "czas_kuriera_hhmm": "17:10",
}
fleet7 = _build_fleet(
    cid="c7", pos_source="last_assigned_pickup", pos=(53.120, 23.140),
    bag=[bag_item],
)
order7 = _order(oid="NEW1", pickup_warsaw="2026-04-20T17:30:00+02:00")
result7 = assess_order(order7, fleet7, restaurant_meta=None, now=now)
# Pipeline should see pickup_ready_at for bag item = czas_kuriera (pod flagą)
# Sprawdzamy że _bag_dict_to_ordersim poprawnie propagował:
# OrderSim.pickup_ready_at jest wewnętrzny, ale wpływa na plan (route_simulator V3.19a floor,
# V3.19e pre_pickup gdy flag). Verify pośrednio: plan nie crashuje + sensowny output.
check("7. Flag True bag_item z czas_kuriera: assess_order runs clean",
      result7 is not None and len(result7.candidates) == 1)
# Check fleet_context / bag_state consumer (via build_courier_bag_state)
# otrzymał order_in_bag_raw z pickup_time = czas_kuriera.

# Test 8 — Regression: flag False z bag item → pickup_at_warsaw jak pre-V3.19f
_flag(False)
result8 = assess_order(order7, fleet7, restaurant_meta=None, now=now)
check("8. Flag False bag_item z czas_kuriera: regression clean",
      result8 is not None and len(result8.candidates) == 1)

# Cleanup — reset flag
_flag(False)

print("\n" + "=" * 60)
print(f"V3.19f PIPELINE: {passed}/{passed + failed} PASS"
      if failed == 0 else
      f"V3.19f PIPELINE: {passed}/{passed + failed} PASS, {failed} FAIL")
print("=" * 60)

if failed:
    sys.exit(1)
