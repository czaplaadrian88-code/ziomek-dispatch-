"""Test SOON-FREE-COVERAGE (soon_free_coverage): pokrycie deferral przez
_soon_free_probe + klasyfikacja typu zwalniającego kandydata.

Przypadki:
  - busy kurier z soon_free_eligible=True, R6-clean → covered_soon_free + covered_feasible
  - soon_free_eligible=True ale R6-breached → covered_soon_free, NIE covered_feasible
  - pre_shift freeing (free_at=0, brak soon_free) → residuum, freeing_type PRE_SHIFT_START
  - no_gps freeing bez soon_free → residuum, freeing_type OTHER_FREE
  - freeing cand sam łamie committed → defer_risk_new_breach
  - non-structural / non-deferral / staffing → poza zakresem
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import soon_free_coverage as S  # noqa: E402


def _alt(cid="2", free_at=10.0, pos="last_picked_up_pickup",
         soon_free=None, r6_breach=0, r6_bag=25.0, committed=0.0, score=-50.0):
    a = {
        "courier_id": cid, "free_at_min": free_at, "pos_source": pos,
        "objm_r6_breach_count": r6_breach, "r6_max_bag_time_min": r6_bag,
        "late_pickup_committed_max": committed, "score": score,
    }
    if soon_free is not None:
        a["soon_free_eligible"] = soon_free
    return a


def _rec(best_extra=None, alts=None, pool=None, score=-200.0,
         reason="all_candidates_low_score (best=1 score=-200<-100; feasible=3)",
         km_pickup=7.0):
    best = {
        "score": score, "r6_bag_size": 3, "r6_max_bag_time_min": 25.0,
        "km_to_pickup": km_pickup, "objm_r6_breach_count": 0,
        "late_pickup_committed_max": 0.0, "free_at_min": 60.0,
        "pos_source": "last_picked_up_pickup",
    }
    if best_extra:
        best.update(best_extra)
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
        return S.analyze([p])
    finally:
        os.unlink(p)


def test_busy_soon_free_clean_is_covered():
    s = _run([_rec(alts=[_alt(free_at=8.0, soon_free=True, r6_bag=22.0)])])
    assert s["deferral"] == 1
    assert s["covered_soon_free"] == 1
    assert s["covered_feasible"] == 1
    assert s["freeing_type"]["SOON_FREE_BUSY"] == 1


def test_soon_free_but_r6_breached_not_feasible():
    s = _run([_rec(alts=[_alt(free_at=8.0, soon_free=True, r6_breach=1, r6_bag=38.0)])])
    assert s["covered_soon_free"] == 1
    assert s["covered_feasible"] == 0  # would breach R6 even after freeing


def test_pre_shift_freeing_is_residuum():
    # mieszana pula (nie all-pre_shift → nie STAFFING); soonest-freeing = pre_shift
    s = _run([_rec(pool=3, alts=[
        _alt(cid="2", free_at=0.0, pos="pre_shift", soon_free=None, score=56.0),
        _alt(cid="3", free_at=50.0, pos="last_picked_up_pickup", score=-40.0),
    ])])
    assert s["deferral"] == 1
    assert s["residuum"] == 1
    assert s["covered_soon_free"] == 0
    assert s["freeing_type"]["PRE_SHIFT_START"] == 1


def test_no_gps_freeing_is_other_free_residuum():
    s = _run([_rec(alts=[_alt(free_at=0.0, pos="no_gps", soon_free=False, score=40.0)])])
    assert s["residuum"] == 1
    assert s["freeing_type"]["OTHER_FREE"] == 1


def test_deferral_risk_new_breach_flagged():
    # freeing cand itself would breach committed-late
    s = _run([_rec(alts=[_alt(free_at=10.0, committed=18.0)])])
    assert s["deferral"] == 1
    assert s["defer_risk_new_breach"] == 1


def test_staffing_all_preshift_excluded():
    s = _run([_rec(alts=[_alt(pos="pre_shift"), _alt(cid="3", pos="pre_shift")])])
    assert s["deferral"] == 0


def test_non_structural_excluded():
    # best near, R6 ok, pool>1, no committed → not structural → skip
    s = _run([_rec(km_pickup=2.0, best_extra={"r6_max_bag_time_min": 24.0},
                   pool=4, alts=[_alt(), _alt(cid="3"), _alt(cid="4"), _alt(cid="5")])])
    assert s["deferral"] == 0


def test_no_freeing_candidate_not_deferral():
    # everyone busy >15, none idle → not deferral (LOAD)
    s = _run([_rec(alts=[_alt(free_at=40.0), _alt(cid="3", free_at=55.0)])])
    assert s["deferral"] == 0


def test_parse_fail_counted():
    fd, p = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        f.write("nie-json\n")
        f.write(json.dumps(_rec(alts=[_alt(free_at=8.0, soon_free=True, r6_bag=22.0)])) + "\n")
    try:
        s = S.analyze([p])
    finally:
        os.unlink(p)
    assert s["parse_fail"] == 1
    assert s["deferral"] == 1
