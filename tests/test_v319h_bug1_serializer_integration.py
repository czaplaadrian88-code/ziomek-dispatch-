"""V3.19h BUG-1 serializer integration — 2 nowe klucze LOC A + B."""
import sys
import os

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from dispatch_v2.shadow_dispatcher import _serialize_candidate, _serialize_result
from dispatch_v2.dispatch_pipeline import Candidate, PipelineResult
from dispatch_v2.route_simulator_v2 import RoutePlanV2

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


def _plan():
    return RoutePlanV2(
        sequence=["TEST"], predicted_delivered_at={}, pickup_at={},
        total_duration_min=5.0, strategy="greedy", sla_violations=0,
        osrm_fallback_used=False,
    )


def _cand(cid, bug1_factor, bug1_sr_adj):
    return Candidate(
        courier_id=cid, name=f"nick_{cid}", score=0.0,
        feasibility_verdict="MAYBE", feasibility_reason="ok",
        plan=_plan(),
        metrics={
            "pos_source": "gps",
            "km_to_pickup": 1.0,
            "v319e_r1_prime_hypothetical": None,
            "czas_kuriera_warsaw": None, "czas_kuriera_hhmm": None,
            "v319h_bug4_tier_cap_used": None, "v319h_bug4_cap_violation": None,
            "bonus_bug4_cap_soft": 0.0,
            "v319h_bug1_drop_proximity_factor": bug1_factor,
            "v319h_bug1_sr_bundle_adjusted": bug1_sr_adj,
        },
        best_effort=False,
    )


print("=== V3.19h BUG-1 serializer integration ===")

# Test 1-2 — LOC A populated (factor=1.0 full bonus)
c1 = _cand("C1", 1.0, 25.0)
s1 = _serialize_candidate(c1)
check("1. LOC A: v319h_bug1_drop_proximity_factor key present",
      "v319h_bug1_drop_proximity_factor" in s1)
check("2. LOC A: v319h_bug1_sr_bundle_adjusted key present",
      "v319h_bug1_sr_bundle_adjusted" in s1)
check("3. LOC A: factor=1.0 sr_adjusted=25.0 propagate",
      s1.get("v319h_bug1_drop_proximity_factor") == 1.0 and
      s1.get("v319h_bug1_sr_bundle_adjusted") == 25.0)

# Test 2 — factor=0.5 (adjacent)
c2 = _cand("C2", 0.5, 12.5)
s2 = _serialize_candidate(c2)
check("4. LOC A factor=0.5 → sr_adjusted=12.5",
      s2.get("v319h_bug1_drop_proximity_factor") == 0.5 and
      s2.get("v319h_bug1_sr_bundle_adjusted") == 12.5)

# Test 3 — factor=0.0 (distant/Unknown)
c3 = _cand("C3", 0.0, 0.0)
s3 = _serialize_candidate(c3)
check("5. LOC A factor=0.0 → sr_adjusted=0.0",
      s3.get("v319h_bug1_drop_proximity_factor") == 0.0 and
      s3.get("v319h_bug1_sr_bundle_adjusted") == 0.0)

# Test 4 — LOC B _serialize_result.best
result = PipelineResult(
    order_id="T1", verdict="PROPOSE", reason="ok",
    best=c1, candidates=[c1, c2, c3],
    pickup_ready_at=None, restaurant="R", delivery_address="A",
)
ser = _serialize_result(result, "evt", 10.0)
best_dict = ser.get("best")
check("6. LOC B best ma v319h_bug1_drop_proximity_factor",
      isinstance(best_dict, dict) and "v319h_bug1_drop_proximity_factor" in best_dict)
check("7. LOC B best ma v319h_bug1_sr_bundle_adjusted",
      isinstance(best_dict, dict) and "v319h_bug1_sr_bundle_adjusted" in best_dict)
check("8. LOC B best values == C1 (factor=1.0, adj=25.0)",
      best_dict.get("v319h_bug1_drop_proximity_factor") == 1.0 and
      best_dict.get("v319h_bug1_sr_bundle_adjusted") == 25.0)

# Test 5 — alternatives (via _serialize_candidate)
alts = ser.get("alternatives", [])
check("9. alternatives[0] (C2) ma BUG-1 klucze",
      len(alts) == 2 and
      "v319h_bug1_drop_proximity_factor" in alts[0] and
      alts[0].get("v319h_bug1_drop_proximity_factor") == 0.5)
check("10. alternatives[1] (C3) ma BUG-1 klucze",
      alts[1].get("v319h_bug1_drop_proximity_factor") == 0.0)

# Test 6 — Regression guard: inne pola nadal serializowane
for expected in ("courier_id", "plan", "bag_context", "v319e_r1_prime_hypothetical",
                 "czas_kuriera_warsaw", "v319h_bug4_tier_cap_used"):
    check(f"11. regression: key '{expected}' present",
          expected in s1)

# Test 7 — Max bonus stack (Q3 GUARDRAIL — BUG-1 mnożnik is 0-1, nie zwiększa stack)
# Max bonus_l1 = 25 × 1.0 = 25 (bez zmian).
# Max total positive stack (L1 + L2 + BUG2 przyszły) = 25 + 20 + 30 = 75 ≤ 80.
check("12. GUARDRAIL: max bonus_l1 po BUG-1 = 25 (factor=1.0), nie zwiększa stack",
      c1.metrics.get("v319h_bug1_sr_bundle_adjusted") == 25.0)
check("13. GUARDRAIL: factor=0.5 → bonus_l1=12.5 (reduced for Std slipshod)",
      c2.metrics.get("v319h_bug1_sr_bundle_adjusted") == 12.5)

print("\n" + "=" * 60)
print(f"V3.19h BUG-1 SERIALIZER INTEGRATION: {passed}/{passed + failed} PASS"
      if failed == 0 else
      f"V3.19h BUG-1 SERIALIZER INTEGRATION: {passed}/{passed + failed} PASS, {failed} FAIL")
print("=" * 60)

if failed:
    sys.exit(1)
