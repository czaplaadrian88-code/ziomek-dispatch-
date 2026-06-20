"""Test NO-GPS-RESCUE (no_gps_rescue_coverage): klasyfikacja 76 no_gps na
RESCUE_WOULD_FIRE / STORE_EMPTY_BUT_SCORE_OK / STORE_EMPTY_AND_INFEASIBLE +
detekcja „no_gps bije aktywny best".

Przypadki:
  - no_gps soonest, pos_from_store=True → RESCUE_WOULD_FIRE
  - no_gps soonest, score>=-100, R6-clean, store pusty → STORE_EMPTY_BUT_SCORE_OK
  - no_gps soonest, score<-100 → STORE_EMPTY_AND_INFEASIBLE
  - no_gps soonest, R6-breached → STORE_EMPTY_AND_INFEASIBLE
  - soonest NIE no_gps (np. pre_shift) → nie liczony do nogps_soonest
  - no_gps score > active best → nogps_beats_active_best++
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import no_gps_rescue_coverage as N  # noqa: E402


def _alt(cid="2", free_at=5.0, pos="no_gps", pos_from_store=None,
         r6_breach=0, r6_bag=20.0, committed=0.0, score=60.0):
    a = {
        "courier_id": cid, "free_at_min": free_at, "pos_source": pos,
        "objm_r6_breach_count": r6_breach, "r6_max_bag_time_min": r6_bag,
        "late_pickup_committed_max": committed, "score": score,
    }
    if pos_from_store is not None:
        a["pos_from_store"] = pos_from_store
    return a


def _rec(alts=None, pool=None, best_score=-200.0,
         reason="all_candidates_low_score (best=1 score=-200<-100; feasible=3)",
         km_pickup=7.0):
    best = {
        "score": best_score, "r6_bag_size": 3, "r6_max_bag_time_min": 25.0,
        "km_to_pickup": km_pickup, "objm_r6_breach_count": 0,
        "late_pickup_committed_max": 0.0, "free_at_min": 60.0,
        "pos_source": "last_picked_up_pickup",
    }
    if alts is None:
        alts = [_alt()]
    if pool is None:
        pool = len(alts)
    return {"verdict": "KOORD", "reason": reason, "order_id": 1,
            "pool_feasible_count": pool, "best": best, "alternatives": alts}


def _run(records):
    fd, p = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    try:
        return N.analyze([p])
    finally:
        os.unlink(p)


def test_rescue_would_fire():
    s = _run([_rec(alts=[_alt(pos_from_store=True)])])
    assert s["nogps_soonest"] == 1
    assert s["classes"]["RESCUE_WOULD_FIRE"] == 1


def test_store_empty_but_score_ok():
    s = _run([_rec(best_score=-486.0, alts=[_alt(score=109.0, r6_bag=15.0)])])
    assert s["nogps_soonest"] == 1
    assert s["classes"]["STORE_EMPTY_BUT_SCORE_OK"] == 1
    assert s["nogps_beats_active_best"] == 1  # 109 > -486


def test_store_empty_and_infeasible_low_score():
    s = _run([_rec(best_score=-90.0, alts=[_alt(score=-150.0)])])
    assert s["classes"]["STORE_EMPTY_AND_INFEASIBLE"] == 1


def test_store_empty_and_infeasible_r6_breach():
    s = _run([_rec(alts=[_alt(score=60.0, r6_breach=1, r6_bag=40.0)])])
    assert s["classes"]["STORE_EMPTY_AND_INFEASIBLE"] == 1


def test_soonest_not_nogps_excluded():
    # soonest is pre_shift (mieszana pula), no_gps is slower → nogps_soonest=0
    s = _run([_rec(pool=3, alts=[
        _alt(cid="2", free_at=0.0, pos="pre_shift", score=50.0),
        _alt(cid="3", free_at=10.0, pos="no_gps", score=60.0),
    ])])
    assert s["deferral"] == 1
    assert s["nogps_soonest"] == 0


def test_nogps_beats_active_best_counter():
    s = _run([
        _rec(best_score=-300.0, alts=[_alt(cid="2", score=55.0)]),         # beats
        _rec(best_score=-10.0, alts=[_alt(cid="3", score=-150.0)]),        # not beats; infeasible
    ])
    # second rec: best -10 is NOT structural? km_pickup=7 (longhaul) → structural.
    # no_gps score -150 < best -10 → not beats. classes: 1 SCORE_OK + 1 INFEASIBLE
    assert s["nogps_beats_active_best"] == 1


def test_parse_fail_counted():
    fd, p = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        f.write("nie-json\n")
        f.write(json.dumps(_rec(alts=[_alt(pos_from_store=True)])) + "\n")
    try:
        s = N.analyze([p])
    finally:
        os.unlink(p)
    assert s["parse_fail"] == 1
    assert s["nogps_soonest"] == 1
