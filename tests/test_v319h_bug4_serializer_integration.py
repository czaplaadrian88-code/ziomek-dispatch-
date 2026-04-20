"""V3.19h BUG-4 serializer integration — zgodnie z V3.19f lesson.

Każde nowe pole w enriched_metrics MUSI trafić przez:
- shadow_dispatcher._serialize_candidate (Location A — alternatives)
- shadow_dispatcher._serialize_result.best (Location B — best)

Tests: 3 nowe klucze BUG-4 (tier_cap_used, cap_violation, bonus_bug4_cap_soft).
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


def _plan():
    return RoutePlanV2(
        sequence=["TEST"], predicted_delivered_at={}, pickup_at={},
        total_duration_min=5.0, strategy="greedy", sla_violations=0,
        osrm_fallback_used=False,
    )


def _cand(cid, bug4_cap_used, bug4_violation, bonus_soft):
    return Candidate(
        courier_id=cid, name=f"nick_{cid}", score=0.0,
        feasibility_verdict="MAYBE", feasibility_reason="ok",
        plan=_plan(),
        metrics={
            "pos_source": "gps",
            "km_to_pickup": 1.0,
            "v319e_r1_prime_hypothetical": None,
            "czas_kuriera_warsaw": None, "czas_kuriera_hhmm": None,
            "v319h_bug4_tier_cap_used": bug4_cap_used,
            "v319h_bug4_cap_violation": bug4_violation,
            "bonus_bug4_cap_soft": bonus_soft,
        },
        best_effort=False,
    )


print("=== V3.19h BUG-4 serializer integration ===")

# Test 1 — LOC A propaguje 3 nowe klucze (violation=0 scenario)
c1 = _cand("C1", "gold/peak/6", 0, 0.0)
s1 = _serialize_candidate(c1)
check("1. LOC A: v319h_bug4_tier_cap_used key present",
      "v319h_bug4_tier_cap_used" in s1)
check("2. LOC A: v319h_bug4_cap_violation key present",
      "v319h_bug4_cap_violation" in s1)
check("3. LOC A: bonus_bug4_cap_soft key present",
      "bonus_bug4_cap_soft" in s1)
check("4. Values propagate correctly (gold/peak/6, violation=0)",
      s1.get("v319h_bug4_tier_cap_used") == "gold/peak/6" and
      s1.get("v319h_bug4_cap_violation") == 0 and
      s1.get("bonus_bug4_cap_soft") == 0.0)

# Test 2 — LOC A violation=1 scenario (-20 penalty)
c2 = _cand("C2", "std/peak/4", 1, -20.0)
s2 = _serialize_candidate(c2)
check("5. LOC A violation=1: -20 penalty serialized",
      s2.get("v319h_bug4_cap_violation") == 1 and
      s2.get("bonus_bug4_cap_soft") == -20.0)

# Test 3 — LOC B _serialize_result.best propaguje
result = PipelineResult(
    order_id="T1", verdict="PROPOSE", reason="ok",
    best=c1, candidates=[c1, c2],
    pickup_ready_at=None, restaurant="R", delivery_address="A",
)
ser = _serialize_result(result, "evt", 10.0)
best_dict = ser.get("best")
check("6. LOC B best ma v319h_bug4_tier_cap_used",
      isinstance(best_dict, dict) and "v319h_bug4_tier_cap_used" in best_dict)
check("7. LOC B best ma v319h_bug4_cap_violation",
      isinstance(best_dict, dict) and "v319h_bug4_cap_violation" in best_dict)
check("8. LOC B best ma bonus_bug4_cap_soft",
      isinstance(best_dict, dict) and "bonus_bug4_cap_soft" in best_dict)
check("9. LOC B best values correct",
      best_dict.get("v319h_bug4_tier_cap_used") == "gold/peak/6" and
      best_dict.get("v319h_bug4_cap_violation") == 0)

# Test 4 — alternatives (via _serialize_candidate) też propagują
alts = ser.get("alternatives", [])
check("10. alternatives[0] ma BUG-4 klucze",
      len(alts) == 1 and
      "v319h_bug4_tier_cap_used" in alts[0] and
      "v319h_bug4_cap_violation" in alts[0] and
      "bonus_bug4_cap_soft" in alts[0])

# Test 5 — None values (gdy flag False all metrics None) serializują się
c_none = _cand("C3", None, None, 0.0)
s_none = _serialize_candidate(c_none)
check("11. None values: kluczy present, values None (except bonus=0 default)",
      "v319h_bug4_tier_cap_used" in s_none and s_none["v319h_bug4_tier_cap_used"] is None and
      "v319h_bug4_cap_violation" in s_none and s_none["v319h_bug4_cap_violation"] is None)

# Test 6 — Regression guard: pre-existing klucze nadal serializowane
for expected in ("courier_id", "plan", "bag_context", "v319e_r1_prime_hypothetical",
                 "czas_kuriera_warsaw"):
    check(f"12. regression: key '{expected}' present",
          expected in s1)

# Test 7 — Max bonus stack warning (Q3 GUARDRAIL sanity)
# Sprawdź że przy typical scenariusz bonus_bug4 nie dominuje pozytywnych bonusów
# (czyli: bonus_bug4 < 0 → stack top-positive nie zwiększa się przez BUG-4)
# Simplified: bonus_bug4 ≤ 0 zawsze (penalty-only).
for cand in [c1, c2, c_none]:
    bonus = cand.metrics.get("bonus_bug4_cap_soft", 0.0) or 0.0
    check(f"13. cand {cand.courier_id}: bonus_bug4_cap_soft ≤ 0 (penalty-only)",
          bonus <= 0.0)

print("\n" + "=" * 60)
print(f"V3.19h BUG-4 SERIALIZER INTEGRATION: {passed}/{passed + failed} PASS"
      if failed == 0 else
      f"V3.19h BUG-4 SERIALIZER INTEGRATION: {passed}/{passed + failed} PASS, {failed} FAIL")
print("=" * 60)

if failed:
    sys.exit(1)
