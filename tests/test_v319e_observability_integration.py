"""V3.19e integration test — end-to-end serializer propagation.

Problem diagnosed w shadow-deploy 2026-04-20 17:24 UTC: post-restart decision
oid=467498 (pos=last_assigned_pickup) NIE miał v319e_r1_prime_hypothetical w
learning_log mimo że test_v319e_r1_prime_observability PASS. Root cause:
shadow_dispatcher._serialize_candidate + _serialize_result.best ręcznie
projektują PODZBIÓR kluczy z enriched_metrics; nowe pole było droppowane.

Ten test zamyka gap: budujemy Candidate + PipelineResult z v319e field
w metrics, wywołujemy oba serializery, sprawdzamy że field jest OBECNY
po serializacji (nie tylko w metrics dict in-memory).
"""
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


def _make_plan():
    return RoutePlanV2(
        sequence=["TEST"],
        predicted_delivered_at={},
        pickup_at={},
        total_duration_min=5.0,
        strategy="greedy",
        sla_violations=0,
        osrm_fallback_used=False,
    )


def _make_cand(cid, pos_source, v319e_dict):
    return Candidate(
        courier_id=cid,
        name=f"nick_{cid}",
        score=0.0,
        feasibility_verdict="MAYBE",
        feasibility_reason="ok",
        plan=_make_plan(),
        metrics={
            "pos_source": pos_source,
            "km_to_pickup": 1.0,
            "v319e_r1_prime_hypothetical": v319e_dict,
        },
        best_effort=False,
    )


print("=== V3.19e observability — serializer integration ===")

# Test 1 — _serialize_candidate propaguje field gdy dict populated.
hyp = {
    "pos_source_raw": "last_assigned_pickup",
    "drive_min": 2.5,
    "pickup_ready_delta_min": 10.0,
    "would_trigger_floor": True,
    "hypothetical_min_eta_min": 10.0,
}
cand_r1 = _make_cand("C1", "last_assigned_pickup", hyp)
s_cand = _serialize_candidate(cand_r1)
check("1. _serialize_candidate ma klucz v319e_r1_prime_hypothetical",
      "v319e_r1_prime_hypothetical" in s_cand,
      detail=f"keys tail: {list(s_cand.keys())[-3:]}")
check("1b. value to dict z poprawnymi polami",
      isinstance(s_cand.get("v319e_r1_prime_hypothetical"), dict) and
      s_cand.get("v319e_r1_prime_hypothetical", {}).get("would_trigger_floor") is True)

# Test 2 — _serialize_candidate zachowuje None dla pos!=last_assigned_pickup.
cand_gps = _make_cand("C2", "gps", None)
s_gps = _serialize_candidate(cand_gps)
check("2. klucz obecny ale None dla pos=gps",
      "v319e_r1_prime_hypothetical" in s_gps and
      s_gps.get("v319e_r1_prime_hypothetical") is None)

# Test 3 — _serialize_result.best propaguje field.
result = PipelineResult(
    order_id="TEST123",
    verdict="PROPOSE",
    reason="ok",
    best=cand_r1,
    candidates=[cand_r1, cand_gps],
    pickup_ready_at=None,
    restaurant="R",
    delivery_address="A",
)
ser = _serialize_result(result, "evt_test", 12.3)
best_ser = ser.get("best")
check("3. _serialize_result.best ma v319e_r1_prime_hypothetical",
      isinstance(best_ser, dict) and "v319e_r1_prime_hypothetical" in best_ser)
check("3b. best.v319e_r1_prime_hypothetical == dict z fixtury",
      isinstance(best_ser, dict) and
      isinstance(best_ser.get("v319e_r1_prime_hypothetical"), dict) and
      best_ser.get("v319e_r1_prime_hypothetical", {}).get("drive_min") == 2.5)

# Test 4 — alternatives (via _serialize_candidate) też mają pole.
alts = ser.get("alternatives", [])
check("4. alternatives[0] ma v319e_r1_prime_hypothetical=None (gps)",
      len(alts) == 1 and "v319e_r1_prime_hypothetical" in alts[0] and
      alts[0].get("v319e_r1_prime_hypothetical") is None)

# Test 5 — regression guard: pozostałe klucze nadal serializowane.
for expected in ("courier_id", "pos_source", "plan", "bag_context", "km_to_pickup"):
    check(f"5. regression: key '{expected}' present in serialized candidate",
          expected in s_cand)

print("\n" + "=" * 60)
print(f"V3.19e OBSERVABILITY INTEGRATION: {passed}/{passed + failed} PASS"
      if failed == 0 else
      f"V3.19e OBSERVABILITY INTEGRATION: {passed}/{passed + failed} PASS, {failed} FAIL")
print("=" * 60)

if failed:
    sys.exit(1)
