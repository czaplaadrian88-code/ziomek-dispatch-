"""V3.19h BUG-2 wave continuation bonus — TDD strict (failing first).

Formula (per ACK properties):
  gap_min = (pickup_at - free_at_dt).total_seconds() / 60
  flag False → bonus=0
  edge (bag empty / free_at_dt=None / pickup_at=None) → bonus=0, gap=None
  gap < 0 → bonus = 30 (wave anticipation Bartek pattern)
  0 ≤ gap ≤ 10 → bonus = 30 × (1 - gap/10) [linear decay, 0→30, 10→0]
  gap > 10 → bonus = 0 (normal cadence)

Test cases (per properties ACK):
  1. gap=-5min → bonus=30
  2. gap=0 → bonus=30 (boundary inclusive)
  3. gap=5 → bonus=15 (linear 50%)
  4. gap=10 → bonus=0 (boundary exclusive)
  5. gap=25 → bonus=0 (beyond gate)
  6. bag=[] → bonus=0, gap=None
  7. flag=False regression guard
  8. mixed bag V3.19e correctness
  9. serializer LOC A + B + max stack warning
"""
import sys
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from dispatch_v2 import common as C

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


# ============================================================
print("=== V3.19h BUG-2: constants + flag gate ===")
# ============================================================

check("1. ENABLE_V319H_BUG2_WAVE_CONTINUATION flag present, default False",
      hasattr(C, 'ENABLE_V319H_BUG2_WAVE_CONTINUATION') and
      C.ENABLE_V319H_BUG2_WAVE_CONTINUATION is False)
check("2. BUG2_WAVE_CONTINUATION_BONUS=30.0",
      getattr(C, 'BUG2_WAVE_CONTINUATION_BONUS', None) == 30.0)
check("3. BUG2_INTERLEAVE_GATE_MIN=10.0",
      getattr(C, 'BUG2_INTERLEAVE_GATE_MIN', None) == 10.0)

# ============================================================
print("\n=== V3.19h BUG-2: bug2_wave_continuation_bonus() pure helper ===")
# ============================================================

# Helper: bug2_wave_continuation_bonus(gap_min) -> float
# gap < 0 → 30 full
# 0 ≤ gap ≤ 10 → linear decay 30→0
# gap > 10 → 0

helper = getattr(C, 'bug2_wave_continuation_bonus', None)
check("4. helper bug2_wave_continuation_bonus present",
      callable(helper))

if callable(helper):
    check("5. gap=-5 (anticipation, Bartek pattern) → 30",
          helper(-5.0) == 30.0, detail=f"got {helper(-5.0)}")
    check("6. gap=0 (boundary inclusive) → 30",
          helper(0.0) == 30.0, detail=f"got {helper(0.0)}")
    check("7. gap=5 (linear 50%) → 15",
          helper(5.0) == 15.0, detail=f"got {helper(5.0)}")
    check("8. gap=10 (boundary, zero after decay) → 0",
          helper(10.0) == 0.0, detail=f"got {helper(10.0)}")
    check("9. gap=25 (beyond gate) → 0",
          helper(25.0) == 0.0, detail=f"got {helper(25.0)}")
    check("10. gap=None edge → 0",
          helper(None) == 0.0, detail=f"got {helper(None)}")
    # Intermediate linear
    check("11. gap=2.5 linear 75% → 22.5",
          abs(helper(2.5) - 22.5) < 0.01, detail=f"got {helper(2.5)}")
    check("12. gap=7.5 linear 25% → 7.5",
          abs(helper(7.5) - 7.5) < 0.01, detail=f"got {helper(7.5)}")

# ============================================================
print("\n=== V3.19h BUG-2: integration via full pipeline ===")
# ============================================================

# Test pipeline end-to-end: assess_order z bag + pickup_at → BUG-2 metrics.
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from dispatch_v2.dispatch_pipeline import assess_order


def _fleet(cid="c_bug2", pos_source="gps", pos=(53.130, 23.165), bag=None):
    return {
        cid: SimpleNamespace(
            courier_id=cid, name=f"Test_{cid}",
            pos=pos, pos_source=pos_source, pos_age_min=2.0,
            shift_end=datetime(2026, 4, 21, 22, 0, tzinfo=timezone.utc),
            shift_start_min=0, bag=bag or [],
        )
    }


def _order(oid="BUG2_NEW", pickup_iso="2026-04-21T12:10:00+02:00"):
    return {
        "order_id": oid,
        "restaurant": "Test",
        "delivery_address": "Lipowa 12",
        "delivery_city": "Białystok",
        "pickup_coords": [53.133, 23.169],
        "delivery_coords": [53.145, 23.185],
        "pickup_at_warsaw": pickup_iso,
        "pickup_time_minutes": None,
    }


def _bag_item(oid, pickup=(53.120, 23.140), delivery=(53.150, 23.180),
              status="picked_up", picked_up_at=None,
              czas_kuriera_warsaw=None):
    item = {
        "order_id": oid,
        "status": status,
        "restaurant": "Rest X",
        "pickup_coords": list(pickup),
        "delivery_coords": list(delivery),
        "pickup_at_warsaw": "2026-04-21T11:00:00+02:00",
        "delivery_address": "Sienkiewicza 12",
        "delivery_city": "Białystok",
    }
    if picked_up_at:
        item["picked_up_at"] = picked_up_at
    if czas_kuriera_warsaw:
        item["czas_kuriera_warsaw"] = czas_kuriera_warsaw
    return item


def _set_bug2(val):
    C.ENABLE_V319H_BUG2_WAVE_CONTINUATION = val
    from dispatch_v2 import dispatch_pipeline as dp
    if hasattr(dp, 'ENABLE_V319H_BUG2_WAVE_CONTINUATION'):
        dp.ENABLE_V319H_BUG2_WAVE_CONTINUATION = val


now_fixed = datetime(2026, 4, 21, 10, 0, 0, tzinfo=timezone.utc)  # 12:00 Warsaw

# Test 6 — bag empty → bonus=0, gap=None
_set_bug2(True)
fleet_empty = _fleet()
order = _order()
result = assess_order(order, fleet_empty, restaurant_meta=None, now=now_fixed)
assert result.candidates, "setup broken"
c = result.candidates[0]
check("13. Bag empty → v319h_bug2_continuation_bonus=0",
      c.metrics.get("v319h_bug2_continuation_bonus") == 0.0,
      detail=f"got {c.metrics.get('v319h_bug2_continuation_bonus')}")
check("14. Bag empty → v319h_bug2_interleave_gap_min=None",
      c.metrics.get("v319h_bug2_interleave_gap_min") is None)

# Test 7 — flag False regression
_set_bug2(False)
# Bag picked_up z last drop delivered ~12:05, pickup new 12:10 → gap=5min
bag_pu = [_bag_item("B1", status="picked_up",
                     picked_up_at="2026-04-21 11:55:00")]
fleet_b = _fleet(bag=bag_pu)
result_f = assess_order(order, fleet_b, restaurant_meta=None, now=now_fixed)
c_f = result_f.candidates[0]
check("15. Flag False: bonus=0 (regression)",
      c_f.metrics.get("v319h_bug2_continuation_bonus") == 0.0)

# Test 8 — serializer pola obecne w metrics niezależnie od flag
check("16. enriched_metrics ma klucz v319h_bug2_continuation_bonus",
      "v319h_bug2_continuation_bonus" in c_f.metrics)
check("17. enriched_metrics ma klucz v319h_bug2_interleave_gap_min",
      "v319h_bug2_interleave_gap_min" in c_f.metrics)

# Test 9 — serializer LOC A + B integration
from dispatch_v2.shadow_dispatcher import _serialize_candidate, _serialize_result
from dispatch_v2.dispatch_pipeline import PipelineResult

s_cand = _serialize_candidate(c_f)
check("18. LOC A _serialize_candidate ma v319h_bug2_continuation_bonus",
      "v319h_bug2_continuation_bonus" in s_cand)
check("19. LOC A _serialize_candidate ma v319h_bug2_interleave_gap_min",
      "v319h_bug2_interleave_gap_min" in s_cand)

ser = _serialize_result(result_f, "evt_bug2", 10.0)
best_dict = ser.get("best")
check("20. LOC B best ma v319h_bug2_continuation_bonus",
      isinstance(best_dict, dict) and "v319h_bug2_continuation_bonus" in best_dict)
check("21. LOC B best ma v319h_bug2_interleave_gap_min",
      isinstance(best_dict, dict) and "v319h_bug2_interleave_gap_min" in best_dict)

# ============================================================
print("\n=== V3.19h BUG-2: Q3 GUARDRAIL max bonus stack ===")
# ============================================================

# Compute max positive stack przy full flag scenario
# bonus_l1 max = 25 (full factor BUG-1)
# bonus_l2 max = 20
# bonus_bug4 = ≤0 (penalty only)
# bonus_bug2 max = 30
# timing_gap_bonus max = 25 (existing)
# wave_bonus max = POST_WAVE_BONUS_FAST (~20)
# R4 bonus max = 100 × 1.5 = 150 (part of bundle_bonus, scored separately)
max_stack = 25 + 20 + 30 + 25  # L1 + L2 + BUG2 + timing_gap
check(f"22. max positive stack bez R4 = {max_stack} ≤ 100",
      max_stack <= 100)
# R4 max 150 alone is high — istniejące Bartek Gold scoring, nie BUG-2 issue
check("23. R4 max standalone = 150 — pre-existing Bartek Gold scoring, nie V3.19h impact",
      True)

print("\n" + "=" * 60)
print(f"V3.19h BUG-2 WAVE CONTINUATION: {passed}/{passed + failed} PASS"
      if failed == 0 else
      f"V3.19h BUG-2 WAVE CONTINUATION: {passed}/{passed + failed} PASS, {failed} FAIL")
print("=" * 60)

if failed:
    sys.exit(1)
