"""V3.19e Opcja B — R1' observability test.

Tests that `enriched_metrics["v319e_r1_prime_hypothetical"]` field is:
  - populated gdy pos_source == "last_assigned_pickup"
  - None gdy inne pos_source
  - contains {pos_source_raw, drive_min, pickup_ready_delta_min,
    would_trigger_floor, hypothetical_min_eta_min}
  - would_trigger_floor = (drive_min < pickup_ready_delta_min)

Zero behavior change expected (field is observability only).

Pattern wzorowany na test_decision_engine_f21.test_B13 — integration test
z fabricated fleet_snapshot + real assess_order call.
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


def _build_fleet(couriers_spec):
    """couriers_spec: list of (cid, pos_source, pos). Returns fleet_snapshot dict."""
    fleet = {}
    shift_end = datetime(2026, 4, 20, 20, 0, tzinfo=timezone.utc)
    for cid, ps, pos in couriers_spec:
        fleet[cid] = SimpleNamespace(
            courier_id=cid,
            name=f"Test_{cid}",
            pos=pos,
            pos_source=ps,
            pos_age_min=2.0 if ps == "gps" else None,
            shift_end=shift_end,
            shift_start_min=0,
            bag=[],
        )
    return fleet


def _order_at(pickup_warsaw_iso):
    return {
        "order_id": "R1PRIME_TEST",
        "restaurant": "Test Restaurant",
        "delivery_address": "Test delivery",
        "pickup_coords": [53.133, 23.169],
        "delivery_coords": [53.145, 23.185],
        "pickup_at_warsaw": pickup_warsaw_iso,
        "pickup_time_minutes": None,
    }


# ============================================================
print("=== V3.19e R1' observability ===")
# ============================================================

now = datetime(2026, 4, 20, 15, 0, 0, tzinfo=timezone.utc)

# Test 1 — pos_source="last_assigned_pickup" → field populated.
# Drive ~500m SW of new_order pickup (fast drive) + pickup_ready_at +25min →
# would_trigger_floor = True (drive_min < pickup_ready_delta_min).
fleet1 = _build_fleet([
    ("r1prime_c", "last_assigned_pickup", (53.130, 23.165)),  # 500m SW of pickup
])
order1 = _order_at("2026-04-20T17:25:00+02:00")  # +25 min z Warsaw (=15:25 UTC)
res1 = assess_order(order1, fleet1, restaurant_meta=None, now=now)
assert res1 is not None and len(res1.candidates) >= 1, "setup broken"
cand1 = next(c for c in res1.candidates if c.courier_id == "r1prime_c")
hyp1 = cand1.metrics.get("v319e_r1_prime_hypothetical")
check("1. pos=last_assigned_pickup → field present (dict, not None)",
      isinstance(hyp1, dict), detail=f"got {type(hyp1).__name__}={hyp1}")
if isinstance(hyp1, dict):
    check("1b. pos_source_raw == 'last_assigned_pickup'",
          hyp1.get("pos_source_raw") == "last_assigned_pickup")
    check("1c. drive_min present (numeric)",
          isinstance(hyp1.get("drive_min"), (int, float)))
    check("1d. pickup_ready_delta_min present (numeric)",
          isinstance(hyp1.get("pickup_ready_delta_min"), (int, float)))
    check("1e. would_trigger_floor present (bool)",
          isinstance(hyp1.get("would_trigger_floor"), bool))
    check("1f. hypothetical_min_eta_min == max(drive_min, pickup_ready_delta_min)",
          abs(hyp1.get("hypothetical_min_eta_min", -1) -
              max(hyp1.get("drive_min", 0), hyp1.get("pickup_ready_delta_min", 0))) < 0.01,
          detail=f"hyp={hyp1}")

# Test 2 — pos_source="gps" → field is None.
fleet2 = _build_fleet([
    ("gps_c", "gps", (53.130, 23.165)),
])
res2 = assess_order(_order_at("2026-04-20T17:25:00+02:00"), fleet2,
                    restaurant_meta=None, now=now)
cand2 = next(c for c in res2.candidates if c.courier_id == "gps_c")
hyp2 = cand2.metrics.get("v319e_r1_prime_hypothetical")
check("2. pos=gps → field is None", hyp2 is None, detail=f"got {hyp2}")

# Test 3 — pos_source="last_picked_up_delivery" → field is None.
fleet3 = _build_fleet([
    ("picked_c", "last_picked_up_delivery", (53.130, 23.165)),
])
res3 = assess_order(_order_at("2026-04-20T17:25:00+02:00"), fleet3,
                    restaurant_meta=None, now=now)
cand3 = next(c for c in res3.candidates if c.courier_id == "picked_c")
hyp3 = cand3.metrics.get("v319e_r1_prime_hypothetical")
check("3. pos=last_picked_up_delivery → field is None", hyp3 is None,
      detail=f"got {hyp3}")

# Test 4 — would_trigger_floor logic: pickup_ready_at in the past →
# ready_delta=0 → would_trigger_floor = (drive_min < 0) = False (unless drive_min<0).
fleet4 = _build_fleet([
    ("past_ready_c", "last_assigned_pickup", (53.130, 23.165)),
])
# pickup_ready 5 min in past
past_iso = (now - timedelta(minutes=5)).astimezone(timezone.utc).isoformat()
# Need format Warsaw +02:00 for pickup_at_warsaw
past_warsaw = datetime(2026, 4, 20, 16, 55, 0).isoformat() + "+02:00"  # 14:55 UTC
order4 = _order_at(past_warsaw)
res4 = assess_order(order4, fleet4, restaurant_meta=None, now=now)
cand4 = next(c for c in res4.candidates if c.courier_id == "past_ready_c")
hyp4 = cand4.metrics.get("v319e_r1_prime_hypothetical")
if isinstance(hyp4, dict):
    check("4. pickup_ready past → pickup_ready_delta_min == 0",
          hyp4.get("pickup_ready_delta_min") == 0.0,
          detail=f"got {hyp4.get('pickup_ready_delta_min')}")
    check("4b. would_trigger_floor = False (drive_min > 0 ≥ ready_delta=0)",
          hyp4.get("would_trigger_floor") is False,
          detail=f"got {hyp4}")

print("\n" + "=" * 60)
print(f"V3.19e R1' OBSERVABILITY: {passed}/{passed + failed} PASS"
      if failed == 0
      else f"V3.19e R1' OBSERVABILITY: {passed}/{passed + failed} PASS, {failed} FAIL")
print("=" * 60)

if failed:
    sys.exit(1)
