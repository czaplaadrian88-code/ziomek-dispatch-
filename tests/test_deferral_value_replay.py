"""DEFERRAL-VALUE — testy klasyfikacji opportunity busy-finishing.

Kluczowe rozróżnienie: busy-finishing JUŻ-best (system go wybiera) NIE jest
opportunity; opportunity = busy-finishing POMINIĘTY na rzecz gorszego (KOORD z
INNYM best, lub blind-best z busy-finishing altem).
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dispatch_v2.tools import deferral_value_replay as M


def _cand(cid, pos="gps", bag=1, free_at=4.0, soon_free_free=None,
          score=-50.0, r6_bag=25.0):
    c = {"courier_id": cid, "pos_source": pos, "r6_bag_size": bag,
         "free_at_min": free_at, "score": score, "r6_max_bag_time_min": r6_bag,
         "soon_free_last_drop_km": 1.0}
    if soon_free_free is not None:
        c["soon_free_free_at_min"] = soon_free_free
    return c


def _rec(best, alts=None, verdict="PROPOSE", reason="feasible",
         ts="2026-06-12T11:30:00+00:00", pickup_ready="2026-06-12T11:50:00+00:00"):
    return {"verdict": verdict, "reason": reason, "ts": ts, "order_id": 1,
            "pickup_ready_at": pickup_ready, "best": best,
            "alternatives": alts or []}


def _run(records, window=8.0):
    fd, p = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    try:
        return M.analyze([p], window=window)
    finally:
        os.unlink(p)


def test_bf_free_window():
    # busy-finishing via soon_free_free_at_min ≤ window
    assert M._bf_free(_cand("1", soon_free_free=4.0)) == 4.0
    # outside window → None
    assert M._bf_free(_cand("1", soon_free_free=20.0, free_at=20.0)) is None
    # no bag → not busy-finishing
    assert M._bf_free(_cand("1", bag=0, free_at=2.0)) is None
    # no_gps → not real GPS
    assert M._bf_free(_cand("1", pos="no_gps", free_at=2.0)) is None


def test_bf_is_best_not_opportunity():
    # best IS busy-finishing → system already picks it → NOT opportunity
    best = _cand("1", pos="gps", bag=1, free_at=4.0)
    s = _run([_rec(best, verdict="KOORD", reason="all_candidates_low_score (best=1 ...)")])
    assert s["with_bf"] == 1
    assert s["bf_is_best"] == 1
    assert s["opp_koord"] == 0  # KEY: bf-was-best is not an opportunity


def test_koord_with_different_bf_is_opportunity():
    # best is NOT busy-finishing (no_gps), a DIFFERENT busy-finishing alt exists
    best = _cand("99", pos="no_gps", bag=0, free_at=60.0, score=-200.0)
    bf = _cand("1", pos="gps", bag=1, free_at=4.0, soon_free_free=4.0)
    s = _run([_rec(best, alts=[bf], verdict="KOORD",
                   reason="all_candidates_low_score (best=99 ...)")])
    assert s["opp_koord"] == 1
    assert s["opp_koord_tight"] == 1  # free 4 ≤ 5


def test_blind_best_with_bf_alt_is_opportunity():
    best = _cand("99", pos="pre_shift", bag=0, free_at=0.0, score=80.0)
    bf = _cand("1", pos="gps", bag=1, free_at=4.0, soon_free_free=4.0)
    s = _run([_rec(best, alts=[bf], verdict="PROPOSE")])
    assert s["opp_blind_best"] == 1


def test_propose_real_best_no_opportunity():
    # best is real-GPS empty (informed), bf alt exists → best NOT blind → not opp B
    best = _cand("99", pos="gps", bag=0, free_at=2.0, score=50.0)
    bf = _cand("1", pos="gps", bag=1, free_at=4.0, soon_free_free=4.0)
    s = _run([_rec(best, alts=[bf], verdict="PROPOSE")])
    assert s["opp_blind_best"] == 0
    assert s["opp_koord"] == 0


def test_binding_constraint_delivery_when_courier_beats_prep():
    # courier free 4min + ~2.5min drive (1km) = 6.5min < prep 20min → pickup NOT binding
    best = _cand("99", pos="no_gps", bag=0, score=-200.0)
    bf = _cand("1", pos="gps", bag=1, soon_free_free=4.0)
    bf["soon_free_last_drop_km"] = 1.0  # ~2.5 min drive
    s = _run([_rec(best, alts=[bf], verdict="KOORD",
                   reason="all_candidates_low_score (...)",
                   ts="2026-06-12T11:30:00+00:00",
                   pickup_ready="2026-06-12T11:50:00+00:00")])  # prep 20min
    assert s["binding_delivery_or_other"] == 1
    assert s["binding_pickup"] == 0


def test_no_bf_skipped():
    best = _cand("1", pos="no_gps", bag=0)
    s = _run([_rec(best)])
    assert s["with_bf"] == 0


def test_parse_fail_counted():
    fd, p = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        f.write("nie-json\n")
        best = _cand("99", pos="no_gps", bag=0, score=-200.0)
        bf = _cand("1", pos="gps", bag=1, soon_free_free=4.0)
        f.write(json.dumps(_rec(best, alts=[bf], verdict="KOORD",
                                reason="all_candidates_low_score (...)")) + "\n")
    try:
        s = M.analyze([p])
    finally:
        os.unlink(p)
    assert s["parse_fail"] == 1
    assert s["opp_koord"] == 1
