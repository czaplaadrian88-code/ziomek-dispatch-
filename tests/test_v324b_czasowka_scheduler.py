"""V3.24-B czasówka scheduler tests — decision matrix + helpers.

Strategia: monkey-patch assess_order + build_fleet_snapshot żeby kontrolować
outcome scoring per test case. Testujemy eval_czasowka() decision matrix,
plus unit tests helperów (_is_czasowka, _classify_match,
_early_morning_blocked, _interval_gate_blocks, _minutes_to_pickup).

Flag ENABLE_V324B_CZASOWKA_SCHEDULER set via env przed importami.
"""
import os
import sys
from datetime import datetime, timezone, timedelta
from zoneinfo import ZoneInfo

os.environ["ENABLE_V324B_CZASOWKA_SCHEDULER"] = "1"
os.environ["ENABLE_V324A_SCHEDULE_INTEGRATION"] = "1"

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from dispatch_v2 import czasowka_scheduler as CS
from dispatch_v2 import common as C

WARSAW = ZoneInfo("Europe/Warsaw")


# ---- Fake candidates & PipelineResult ----
class FakeCandidate:
    def __init__(self, courier_id, name, score, feasibility_verdict="MAYBE",
                 feasibility_reason="ok_sla_fits", km_to_pickup=1.0,
                 drop_proximity_factor=0.5):
        self.courier_id = courier_id
        self.name = name
        self.score = score
        self.feasibility_verdict = feasibility_verdict
        self.feasibility_reason = feasibility_reason
        self.plan = None
        self.metrics = {
            "km_to_pickup": km_to_pickup,
            "v319h_bug1_drop_proximity_factor": drop_proximity_factor,
        }


class FakePipelineResult:
    def __init__(self, candidates, best=None):
        self.candidates = candidates or []
        self.best = best if best is not None else (candidates[0] if candidates else None)
        self.order_id = "fake"
        self.restaurant = "FakeResto"
        self.delivery_address = "Fake 1"
        self.verdict = "PROPOSE" if best else "KOORD"
        self.reason = ""
        self.pickup_ready_at = None


def _make_order_state(pickup_warsaw_iso, prep_minutes=90, courier_id="26"):
    return {
        "pickup_at_warsaw": pickup_warsaw_iso,
        "prep_minutes": prep_minutes,
        "courier_id": courier_id,
        "restaurant": "FakeResto",
        "pickup_address": "Fake Rest 1",
        "pickup_city": "Białystok",
        "delivery_address": "Fake Addr 1",
        "delivery_city": "Białystok",
        "status_id": 2,
        "first_seen": "2026-04-23T09:00:00+00:00",
        "address_id": "999",
        "pickup_coords": [53.13, 23.17],
        "delivery_coords": [53.14, 23.18],
    }


def _assert(cond, label):
    if cond:
        print(f"  OK  {label}")
        return True
    print(f"  FAIL {label}")
    return False


# ---- Unit tests helpers ----

def test_1_is_czasowka_yes():
    osrec = _make_order_state("2026-04-23T12:00:00+02:00", prep_minutes=90, courier_id="26")
    return _assert(CS._is_czasowka(osrec), "prep=90 + cid=26 → czasówka")


def test_2_is_czasowka_no_prep():
    osrec = _make_order_state("2026-04-23T12:00:00+02:00", prep_minutes=30, courier_id="26")
    return _assert(not CS._is_czasowka(osrec), "prep=30 (elastyk) → NOT czasówka")


def test_3_is_czasowka_assigned():
    osrec = _make_order_state("2026-04-23T12:00:00+02:00", prep_minutes=90, courier_id="414")
    return _assert(not CS._is_czasowka(osrec), "prep=90 + cid=414 (assigned) → NOT czasówka")


def test_4_classify_match_ideal():
    m = {"km_to_pickup": 0.8, "v319h_bug1_drop_proximity_factor": 0.6}
    return _assert(CS._classify_match(m) == "ideal", "km=0.8 drop=0.6 → ideal")


def test_5_classify_match_good_km():
    m = {"km_to_pickup": 1.5, "v319h_bug1_drop_proximity_factor": 0.0}
    return _assert(CS._classify_match(m) == "good", "km=1.5 drop=0.0 → good (km OK)")


def test_6_classify_match_good_drop_only():
    m = {"km_to_pickup": 2.5, "v319h_bug1_drop_proximity_factor": 0.6}
    return _assert(CS._classify_match(m) == "good", "km=2.5 drop=0.6 → good (drop OK)")


def test_7_classify_match_none():
    m = {"km_to_pickup": 3.5, "v319h_bug1_drop_proximity_factor": 0.2}
    return _assert(CS._classify_match(m) == "none", "km=3.5 drop=0.2 → none")


def test_8_early_morning_before_910():
    now_warsaw = datetime(2026, 4, 23, 8, 30, tzinfo=WARSAW)
    return _assert(CS._early_morning_blocked(now_warsaw), "08:30 Warsaw → blocked")


def test_9_early_morning_at_910():
    now_warsaw = datetime(2026, 4, 23, 9, 10, tzinfo=WARSAW)
    return _assert(not CS._early_morning_blocked(now_warsaw), "09:10 Warsaw → NOT blocked")


def test_10_interval_gate():
    now = datetime(2026, 4, 23, 12, 0, tzinfo=timezone.utc)
    # 3 min ago → blocked (< 5 min interval)
    info_recent = {"last_eval_ts": (now - timedelta(minutes=3)).isoformat()}
    # 6 min ago → not blocked
    info_old = {"last_eval_ts": (now - timedelta(minutes=6)).isoformat()}
    ok1 = _assert(CS._interval_gate_blocks(info_recent, now), "3min ago → blocked")
    ok2 = _assert(not CS._interval_gate_blocks(info_old, now), "6min ago → not blocked")
    ok3 = _assert(not CS._interval_gate_blocks(None, now), "no info → not blocked")
    return ok1 and ok2 and ok3


# ---- Integration tests eval_czasowka (monkey-patch assess_order + build_fleet_snapshot) ----

_FAKE_RESULT = None
_FAKE_FLEET = {}


def _mock_assess_order(*args, **kwargs):
    return _FAKE_RESULT


def _mock_build_fleet_snapshot(*args, **kwargs):
    return _FAKE_FLEET


# Monkey-patch
from dispatch_v2 import dispatch_pipeline as _dp, courier_resolver as _cr
CS.assess_order = _mock_assess_order
CS.courier_resolver.build_fleet_snapshot = _mock_build_fleet_snapshot


def _run_eval(pickup_iso, now_utc, candidates, best=None):
    """Helper: set fake result + call eval_czasowka."""
    global _FAKE_RESULT
    _FAKE_RESULT = FakePipelineResult(candidates, best=best or (candidates[0] if candidates else None))
    osrec = _make_order_state(pickup_iso, prep_minutes=90, courier_id="26")
    return CS.eval_czasowka("test_oid", osrec, now_utc)


def test_11_dont_emit_before_910():
    # Warsaw 08:30 → 06:30 UTC; pickup 12:00 Warsaw
    now_utc = datetime(2026, 4, 23, 6, 30, tzinfo=timezone.utc)
    pickup_w = "2026-04-23T12:00:00+02:00"
    cand = [FakeCandidate("1", "A", 50.0)]
    res = _run_eval(pickup_w, now_utc, cand)
    return _assert(res["decision"] == "DONT_EMIT", f"08:30 Warsaw → DONT_EMIT (got {res['decision']})")


def test_12_wait_over_60min():
    # pickup 12:00 Warsaw, now 10:50 Warsaw (70 min before)
    now_utc = datetime(2026, 4, 23, 8, 50, tzinfo=timezone.utc)
    pickup_w = "2026-04-23T12:00:00+02:00"
    cand = [FakeCandidate("1", "A", 50.0)]
    res = _run_eval(pickup_w, now_utc, cand)
    return _assert(res["decision"] == "WAIT", f"70min → WAIT (got {res['decision']})")


def test_13_emit_ideal_60_50():
    # pickup 12:00 Warsaw, now 11:05 Warsaw (55 min), km=0.8 drop=0.6
    now_utc = datetime(2026, 4, 23, 9, 5, tzinfo=timezone.utc)
    pickup_w = "2026-04-23T12:00:00+02:00"
    cand = [FakeCandidate("1", "A", 50.0, km_to_pickup=0.8, drop_proximity_factor=0.6)]
    res = _run_eval(pickup_w, now_utc, cand)
    ok_d = _assert(res["decision"] == "EMIT", f"55min ideal → EMIT (got {res['decision']})")
    ok_q = _assert(res["match_quality"] == "ideal", f"match=ideal (got {res['match_quality']})")
    return ok_d and ok_q


def test_14_wait_no_ideal_60_50():
    now_utc = datetime(2026, 4, 23, 9, 5, tzinfo=timezone.utc)
    pickup_w = "2026-04-23T12:00:00+02:00"
    cand = [FakeCandidate("1", "A", 50.0, km_to_pickup=2.5, drop_proximity_factor=0.3)]
    res = _run_eval(pickup_w, now_utc, cand)
    return _assert(res["decision"] == "WAIT",
                   f"55min quality=none → WAIT (got {res['decision']} q={res['match_quality']})")


def test_15_emit_good_50_40():
    # pickup 12:00, now 11:15 (45 min), km=1.5 drop=0
    now_utc = datetime(2026, 4, 23, 9, 15, tzinfo=timezone.utc)
    pickup_w = "2026-04-23T12:00:00+02:00"
    cand = [FakeCandidate("1", "A", 50.0, km_to_pickup=1.5, drop_proximity_factor=0.0)]
    res = _run_eval(pickup_w, now_utc, cand)
    return _assert(res["decision"] == "EMIT",
                   f"45min good → EMIT (got {res['decision']} q={res['match_quality']})")


def test_16_wait_no_good_50_40():
    now_utc = datetime(2026, 4, 23, 9, 15, tzinfo=timezone.utc)
    pickup_w = "2026-04-23T12:00:00+02:00"
    cand = [FakeCandidate("1", "A", 50.0, km_to_pickup=3.5, drop_proximity_factor=0.2)]
    res = _run_eval(pickup_w, now_utc, cand)
    return _assert(res["decision"] == "WAIT",
                   f"45min quality=none → WAIT (got {res['decision']} q={res['match_quality']})")


def test_17_force_assign_under_40():
    # pickup 12:00, now 11:25 (35 min), any candidate MAYBE
    now_utc = datetime(2026, 4, 23, 9, 25, tzinfo=timezone.utc)
    pickup_w = "2026-04-23T12:00:00+02:00"
    cand = [FakeCandidate("1", "A", 20.0, km_to_pickup=3.0, drop_proximity_factor=0.2)]
    res = _run_eval(pickup_w, now_utc, cand)
    return _assert(res["decision"] == "FORCE_ASSIGN",
                   f"35min any MAYBE → FORCE_ASSIGN (got {res['decision']})")


def test_18_force_assign_negative_score():
    # pickup 12:00, now 11:25 (35 min), negative score MAYBE
    now_utc = datetime(2026, 4, 23, 9, 25, tzinfo=timezone.utc)
    pickup_w = "2026-04-23T12:00:00+02:00"
    cand = [FakeCandidate("1", "A", -150.0, km_to_pickup=5.0, drop_proximity_factor=0.0)]
    res = _run_eval(pickup_w, now_utc, cand)
    return _assert(res["decision"] == "FORCE_ASSIGN",
                   f"35min negative score MAYBE → FORCE_ASSIGN (got {res['decision']})")


def test_19_koord_zero_maybe_under_40():
    # All candidates NO
    now_utc = datetime(2026, 4, 23, 9, 25, tzinfo=timezone.utc)
    pickup_w = "2026-04-23T12:00:00+02:00"
    cand = [
        FakeCandidate("1", "A", 50.0, feasibility_verdict="NO", feasibility_reason="extension>60"),
        FakeCandidate("2", "B", 40.0, feasibility_verdict="NO", feasibility_reason="shift_ended"),
    ]
    res = _run_eval(pickup_w, now_utc, cand, best=cand[0])
    return _assert(res["decision"] == "KOORD",
                   f"35min zero MAYBE → KOORD (got {res['decision']})")


def test_20_negative_minutes_force():
    # pickup 11:55, now 12:00 (-5 min, already past)
    now_utc = datetime(2026, 4, 23, 10, 0, tzinfo=timezone.utc)
    pickup_w = "2026-04-23T11:55:00+02:00"
    cand = [FakeCandidate("1", "A", 50.0)]
    res = _run_eval(pickup_w, now_utc, cand)
    return _assert(res["decision"] == "FORCE_ASSIGN",
                   f"negative minutes → FORCE_ASSIGN (got {res['decision']})")


def test_21_no_pickup_timestamp_skip():
    global _FAKE_RESULT
    _FAKE_RESULT = FakePipelineResult([FakeCandidate("1", "A", 50.0)])
    osrec = _make_order_state(None, prep_minutes=90, courier_id="26")
    res = CS.eval_czasowka("test_oid", osrec, datetime(2026, 4, 23, 10, 0, tzinfo=timezone.utc))
    return _assert(res["decision"] == "SKIP",
                   f"no pickup_ts → SKIP (got {res['decision']})")


# ---- Main ----

def main():
    tests = [
        test_1_is_czasowka_yes,
        test_2_is_czasowka_no_prep,
        test_3_is_czasowka_assigned,
        test_4_classify_match_ideal,
        test_5_classify_match_good_km,
        test_6_classify_match_good_drop_only,
        test_7_classify_match_none,
        test_8_early_morning_before_910,
        test_9_early_morning_at_910,
        test_10_interval_gate,
        test_11_dont_emit_before_910,
        test_12_wait_over_60min,
        test_13_emit_ideal_60_50,
        test_14_wait_no_ideal_60_50,
        test_15_emit_good_50_40,
        test_16_wait_no_good_50_40,
        test_17_force_assign_under_40,
        test_18_force_assign_negative_score,
        test_19_koord_zero_maybe_under_40,
        test_20_negative_minutes_force,
        test_21_no_pickup_timestamp_skip,
    ]
    passed = 0
    failed = 0
    for t in tests:
        print(f"\n{t.__name__}:")
        try:
            ok = t()
            if ok:
                passed += 1
            else:
                failed += 1
        except Exception as e:
            print(f"  EXCEPTION {type(e).__name__}: {e}")
            import traceback
            traceback.print_exc()
            failed += 1
    print(f"\n=== RESULT: {passed} passed, {failed} failed ===")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
