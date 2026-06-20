"""Test NO-GPS-WHO (no_gps_who): per-cid Pareto + name + chronic/sporadic.

Przypadki:
  - 2 KOORD od cid A + 1 od cid B → cid_koord {A:2,B:1}, total 3
  - name z alternatives[].name dołączony
  - chronic: cid z no_gps w ~wszystkich wystąpieniach → CHRONIC
  - sporadic: cid z no_gps w <40% wystąpień → SPORADIC
  - peak/off split per cid
  - tylko STORE_EMPTY_BUT_SCORE_OK liczone (score<-100 / R6-breach / pre_shift wykluczone)
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import no_gps_who as W  # noqa: E402


def _alt(cid="518", name="Michał Rogucki", free_at=5.0, pos="no_gps",
         r6_breach=0, r6_bag=18.0, committed=0.0, score=60.0):
    return {
        "courier_id": cid, "name": name, "free_at_min": free_at,
        "pos_source": pos, "objm_r6_breach_count": r6_breach,
        "r6_max_bag_time_min": r6_bag, "late_pickup_committed_max": committed,
        "score": score,
    }


def _rec(alts=None, pool=None, best_score=-300.0, ts="2026-06-12T11:30:00+00:00",
         reason="all_candidates_low_score (best=1 score=-300<-100; feasible=3)",
         km_pickup=7.0):
    best = {
        "score": best_score, "r6_bag_size": 3, "r6_max_bag_time_min": 25.0,
        "km_to_pickup": km_pickup, "objm_r6_breach_count": 0,
        "late_pickup_committed_max": 0.0, "free_at_min": 60.0,
        "pos_source": "last_picked_up_pickup", "courier_id": "999",
    }
    if alts is None:
        alts = [_alt()]
    if pool is None:
        pool = len(alts)
    return {"verdict": "KOORD", "reason": reason, "ts": ts, "order_id": 1,
            "pool_feasible_count": pool, "best": best, "alternatives": alts}


def _run(records):
    fd, p = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    try:
        return W.analyze([p])
    finally:
        os.unlink(p)


def test_per_cid_pareto_and_name():
    s = _run([
        _rec(alts=[_alt(cid="518", name="Michał Rogucki")]),
        _rec(alts=[_alt(cid="518", name="Michał Rogucki")]),
        _rec(alts=[_alt(cid="123", name="Bartek Ołdziej")]),
    ])
    assert s["total_koord"] == 3
    assert s["cid_koord"]["518"] == 2
    assert s["cid_koord"]["123"] == 1
    assert s["cid_name"]["518"] == "Michał Rogucki"
    assert s["cid_name"]["123"] == "Bartek Ołdziej"


def test_chronic_classification():
    # cid 518 appears 3× total, all no_gps → frac 1.0 → CHRONIC
    s = _run([
        _rec(alts=[_alt(cid="518")]),
        _rec(alts=[_alt(cid="518")]),
        _rec(alts=[_alt(cid="518")]),
    ])
    frac = s["cid_nogps_appear"]["518"] / s["cid_total_appear"]["518"]
    assert W._chronic_label(frac) == "CHRONIC"


def test_sporadic_classification():
    # cid 123: 1 KOORD (no_gps) + many PROPOSE appearances WITH gps → low frac
    recs = [_rec(alts=[_alt(cid="123", pos="no_gps")])]
    # add 5 propose records where cid 123 has gps
    for i in range(5):
        recs.append({
            "verdict": "PROPOSE", "reason": "feasible best=123", "order_id": 10 + i,
            "best": {"courier_id": "123", "pos_source": "gps", "score": 50.0},
            "alternatives": [],
        })
    s = _run(recs)
    frac = s["cid_nogps_appear"]["123"] / s["cid_total_appear"]["123"]
    assert frac < W.SPORADIC_FRAC
    assert W._chronic_label(frac) == "SPORADIC"


def test_peak_off_split():
    s = _run([
        _rec(alts=[_alt(cid="518")], ts="2026-06-12T11:30:00+00:00"),  # peak
        _rec(alts=[_alt(cid="518")], ts="2026-06-12T06:00:00+00:00"),  # off
    ])
    assert s["cid_peak"]["518"] == 1
    assert s["cid_off"]["518"] == 1


def test_low_score_nogps_excluded():
    # no_gps but score<-100 → STORE_EMPTY_AND_INFEASIBLE → not counted
    s = _run([_rec(best_score=-90.0, alts=[_alt(cid="518", score=-150.0)])])
    assert s["total_koord"] == 0


def test_r6_breach_nogps_excluded():
    s = _run([_rec(alts=[_alt(cid="518", score=60.0, r6_breach=1, r6_bag=40.0)])])
    assert s["total_koord"] == 0


def test_pre_shift_soonest_excluded():
    # soonest is pre_shift not no_gps → not counted
    s = _run([_rec(pool=3, alts=[
        _alt(cid="518", free_at=0.0, pos="pre_shift", score=50.0),
        _alt(cid="123", free_at=10.0, pos="no_gps", score=60.0),
    ])])
    assert s["total_koord"] == 0


def test_parse_fail_counted():
    fd, p = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        f.write("nie-json\n")
        f.write(json.dumps(_rec(alts=[_alt(cid="518")])) + "\n")
    try:
        s = W.analyze([p])
    finally:
        os.unlink(p)
    assert s["parse_fail"] == 1
    assert s["cid_koord"]["518"] == 1
