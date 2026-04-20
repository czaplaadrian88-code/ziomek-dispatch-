"""V3.19f integration test — end-to-end czas_kuriera propagation.

Full chain: normalize_order → NEW_ORDER event → state_machine persist →
assess_order (pod flagą) → shadow_dispatcher serialize → output dict ma
oba pola czas_kuriera (Location A + B per V3.19e lesson).

Coverage:
  1. _serialize_candidate propaguje czas_kuriera_warsaw + czas_kuriera_hhmm
  2. _serialize_result.best propaguje oba pola
  3. Shadow pipeline end-to-end: payload → order_event → assess → serialize
  4. Flag False: field dalej serializowane (passthrough od pipeline), None
  5. Flag True + czas_kuriera obecny: pickup_ready_at reflects czas_kuriera
  6. Regression guard: klucze present both in alternatives i best
"""
import sys
import os
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from dispatch_v2.shadow_dispatcher import _serialize_candidate, _serialize_result, process_event
from dispatch_v2.dispatch_pipeline import Candidate, PipelineResult, assess_order
from dispatch_v2.route_simulator_v2 import RoutePlanV2
from dispatch_v2 import common as cm

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


def _make_cand(cid, ck_warsaw, ck_hhmm, pos_source="gps"):
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
            "v319e_r1_prime_hypothetical": None,
            "czas_kuriera_warsaw": ck_warsaw,
            "czas_kuriera_hhmm": ck_hhmm,
        },
        best_effort=False,
    )


def _flag(val: bool):
    cm.ENABLE_CZAS_KURIERA_PROPAGATION = val
    from dispatch_v2 import dispatch_pipeline as dp
    dp.ENABLE_CZAS_KURIERA_PROPAGATION = val


# ============================================================
print("=== V3.19f integration: serializer propagation ===")
# ============================================================

# Test 1 — _serialize_candidate ma OBA pola (populated)
c = _make_cand("C1", "2026-04-20T17:10:00+02:00", "17:10")
s = _serialize_candidate(c)
check("1. _serialize_candidate ma czas_kuriera_warsaw",
      s.get("czas_kuriera_warsaw") == "2026-04-20T17:10:00+02:00")
check("1b. _serialize_candidate ma czas_kuriera_hhmm",
      s.get("czas_kuriera_hhmm") == "17:10")

# Test 2 — _serialize_candidate pola None gdy brak w metrics
c_none = _make_cand("C2", None, None)
s_none = _serialize_candidate(c_none)
check("2. Pola obecne ale None",
      "czas_kuriera_warsaw" in s_none and s_none["czas_kuriera_warsaw"] is None and
      "czas_kuriera_hhmm" in s_none and s_none["czas_kuriera_hhmm"] is None)

# Test 3 — _serialize_result.best propaguje oba
result = PipelineResult(
    order_id="T1",
    verdict="PROPOSE",
    reason="ok",
    best=c,
    candidates=[c, c_none],
    pickup_ready_at=None,
    restaurant="R",
    delivery_address="A",
)
ser = _serialize_result(result, "evt", 10.0)
best_dict = ser.get("best")
check("3. _serialize_result.best ma czas_kuriera_warsaw",
      isinstance(best_dict, dict) and
      best_dict.get("czas_kuriera_warsaw") == "2026-04-20T17:10:00+02:00")
check("3b. _serialize_result.best ma czas_kuriera_hhmm",
      isinstance(best_dict, dict) and best_dict.get("czas_kuriera_hhmm") == "17:10")

# Test 4 — alternatives (via _serialize_candidate) też mają pola
alts = ser.get("alternatives", [])
check("4. alternatives[0] (None variant) ma klucze present",
      len(alts) == 1 and
      "czas_kuriera_warsaw" in alts[0] and
      "czas_kuriera_hhmm" in alts[0])

# ============================================================
print("\n=== V3.19f integration: shadow process_event full chain ===")
# ============================================================

# Test 5 — Shadow process_event end-to-end: payload → order_event → serialize
# Setup: real assess_order call z flag True i czas_kuriera w payload
_flag(True)
BIALYSTOK_CENTER = (53.1325, 23.1688)
now_int = datetime(2026, 4, 20, 15, 0, 0, tzinfo=timezone.utc)
fleet_int = {
    "c_int": SimpleNamespace(
        courier_id="c_int",
        name="Test",
        pos=(53.130, 23.165),
        pos_source="gps",
        pos_age_min=2.0,
        shift_end=datetime(2026, 4, 20, 22, 0, tzinfo=timezone.utc),
        shift_start_min=0,
        bag=[],
    )
}
# NEW_ORDER event jak przyszłyby z panel_watcher po V3.19f Step 2 parse
payload_int = {
    "restaurant": "Grill Kebab",
    "delivery_address": "Test",
    "pickup_coords": [53.133, 23.169],
    "delivery_coords": [53.145, 23.185],
    "pickup_at_warsaw": "2026-04-20T17:25:00+02:00",
    "pickup_time_minutes": None,
    "czas_kuriera_warsaw": "2026-04-20T17:10:00+02:00",
    "czas_kuriera_hhmm": "17:10",
}
ev_int = {"order_id": "INT1", "payload": payload_int}
res_int = process_event(ev_int, fleet_int, meta=None, now=now_int)
# Verify: assess_order saw czas_kuriera → pickup_ready_at = 17:10 Warsaw = 15:10 UTC
expected_17_10 = datetime(2026, 4, 20, 15, 10, tzinfo=timezone.utc)
check("5. process_event → pickup_ready_at = czas_kuriera_warsaw (flag True)",
      res_int.pickup_ready_at is not None and
      abs((res_int.pickup_ready_at.astimezone(timezone.utc) - expected_17_10)
          .total_seconds()) < 60,
      detail=f"got {res_int.pickup_ready_at}")

# Verify: serialized result ma oba pola w best
ser_int = _serialize_result(res_int, "evt_int", 15.0)
best_int = ser_int.get("best")
check("5b. serialized best ma czas_kuriera_warsaw",
      isinstance(best_int, dict) and
      best_int.get("czas_kuriera_warsaw") == "2026-04-20T17:10:00+02:00")
check("5c. serialized best ma czas_kuriera_hhmm",
      isinstance(best_int, dict) and best_int.get("czas_kuriera_hhmm") == "17:10")

# Test 6 — Flag False: pole dalej serializowane (passthrough)
_flag(False)
res_f = process_event(ev_int, fleet_int, meta=None, now=now_int)
ser_f = _serialize_result(res_f, "evt_f", 12.0)
best_f = ser_f.get("best")
check("6. Flag False: czas_kuriera dalej w serialized (passthrough)",
      isinstance(best_f, dict) and "czas_kuriera_warsaw" in best_f and
      best_f.get("czas_kuriera_warsaw") == "2026-04-20T17:10:00+02:00")
# Ale pickup_ready_at = pickup_at_warsaw (pre-V3.19f)
expected_17_25 = datetime(2026, 4, 20, 15, 25, tzinfo=timezone.utc)
check("6b. Flag False: pickup_ready_at = pickup_at_warsaw (17:25 UTC)",
      res_f.pickup_ready_at is not None and
      abs((res_f.pickup_ready_at.astimezone(timezone.utc) - expected_17_25)
          .total_seconds()) < 60)

# Test 7 — Regression guard: other V3.19e/V3.19f keys still serialized
check("7. Regression: v319e_r1_prime_hypothetical key present",
      "v319e_r1_prime_hypothetical" in best_int)
check("7b. Regression: pos_source key present",
      "pos_source" in best_int)
check("7c. Regression: plan dict present",
      isinstance(best_int.get("plan"), dict))

# Cleanup
_flag(False)

print("\n" + "=" * 60)
print(f"V3.19f INTEGRATION: {passed}/{passed + failed} PASS"
      if failed == 0 else
      f"V3.19f INTEGRATION: {passed}/{passed + failed} PASS, {failed} FAIL")
print("=" * 60)

if failed:
    sys.exit(1)
