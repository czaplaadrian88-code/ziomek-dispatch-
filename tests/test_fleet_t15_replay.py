"""Test B2/FLEET-T15 (fleet_t15_replay): klasyfikacja AVOIDABLE vs TRULY-NOBODY
na syntetycznych rekordach.

Przypadki (priorytet: STAFFING > SINGLE > DEFERRAL > POSITIONING > LOAD):
  - cała pula pre_shift → TRULY_NOBODY_STAFFING (not avoidable)
  - pool<=1, jedyny zwalnia >15 → TRULY_NOBODY_SINGLE
  - ktoś zwalnia ≤15 → AVOIDABLE_DEFERRAL
  - nikt nie zwalnia ≤15, ktoś idle(~0)+ride<4.5 → AVOIDABLE_POSITIONING
  - wszyscy zajęci >15, nikt idle-blisko → TRULY_NOBODY_LOAD
  - rekord ALGORYTM (bez sygnału strukturalnego) → poza zakresem (pominięty)
  - priorytet: pool<=1 ALE ktoś zwalnia ≤15 → DEFERRAL (nie SINGLE)
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import fleet_t15_replay as T  # noqa: E402


def _alt(cid="1", free_at=60.0, pos="last_picked_up_pickup", ride=2.0,
         r6_breach=0, r6_bag=25.0, committed=0.0, score=-50.0):
    return {
        "courier_id": cid, "free_at_min": free_at, "pos_source": pos,
        "r7_ride_km": ride, "objm_r6_breach_count": r6_breach,
        "r6_max_bag_time_min": r6_bag, "late_pickup_committed_max": committed,
        "score": score,
    }


def _rec(best_extra=None, alts=None, pool=None, score=-200.0,
         reason="all_candidates_low_score (best=1 score=-200<-100; feasible=3)",
         ts="2026-06-12T11:30:00+00:00",
         # default best is STRUCTURAL via longhaul_pickup:
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
    return {"verdict": "KOORD", "reason": reason, "ts": ts, "order_id": 1,
            "pool_feasible_count": pool, "best": best, "alternatives": alts}


def _run(records):
    fd, p = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    try:
        return T.analyze([p])
    finally:
        os.unlink(p)


def test_all_preshift_is_staffing():
    s = _run([_rec(alts=[_alt(pos="pre_shift"), _alt(cid="2", pos="pre_shift")])])
    assert s["classes"]["TRULY_NOBODY_STAFFING"] == 1
    assert s["truly_nobody"] == 1 and s["avoidable"] == 0


def test_single_deep_busy_is_truly_nobody():
    s = _run([_rec(pool=1, alts=[_alt(free_at=66.0)])])
    assert s["classes"]["TRULY_NOBODY_SINGLE"] == 1
    assert s["truly_nobody"] == 1


def test_someone_frees_soon_is_deferral():
    s = _run([_rec(alts=[_alt(free_at=60.0), _alt(cid="2", free_at=12.0)])])
    assert s["classes"]["AVOIDABLE_DEFERRAL"] == 1
    assert s["avoidable"] == 1


def test_idle_close_is_positioning():
    # nikt nie zwalnia ≤15 (wszyscy 60), ale jeden idle(~0)+ride<4.5 — ale free_at=0
    # liczy się jako frees<=15 też → DEFERRAL ma priorytet. Aby dostać POSITIONING
    # trzeba idle WYŁĄCZNIE przez ride, z free_at None? Model: idle = free<=2 & ride<4.5,
    # a free<=2 też spełnia frees_15. Więc POSITIONING jest podzbiorem DEFERRAL i
    # w praktyce =0. Ten test dokumentuje to zachowanie: idle+close → DEFERRAL.
    s = _run([_rec(alts=[_alt(free_at=1.0, ride=2.0)])])
    # free_at=1 <=15 → DEFERRAL (udokumentowany priorytet)
    assert s["classes"]["AVOIDABLE_DEFERRAL"] == 1
    assert s["classes"].get("AVOIDABLE_POSITIONING", 0) == 0


def test_everyone_busy_nobody_idle_is_load():
    s = _run([_rec(alts=[_alt(free_at=40.0, ride=6.0), _alt(cid="2", free_at=55.0, ride=7.0)])])
    assert s["classes"]["TRULY_NOBODY_LOAD"] == 1
    assert s["truly_nobody"] == 1


def test_priority_pool1_but_frees_is_deferral_not_single():
    # pool<=1 ale ten 1 zwalnia ≤15 → DEFERRAL wygrywa nad SINGLE
    s = _run([_rec(pool=1, alts=[_alt(free_at=10.0)])])
    assert s["classes"]["AVOIDABLE_DEFERRAL"] == 1
    assert s["classes"].get("TRULY_NOBODY_SINGLE", 0) == 0


def test_algorytm_record_skipped():
    # best NIE strukturalny: km blisko, R6 ok, pool>1, brak committed → poza zakresem
    s = _run([_rec(km_pickup=2.0, best_extra={"r6_max_bag_time_min": 24.0},
                   pool=4, alts=[_alt(), _alt(cid="2"), _alt(cid="3"), _alt(cid="4")])])
    assert s["structural"] == 0
    assert s["avoidable"] == 0 and s["truly_nobody"] == 0


def test_by_reason_and_peak_tracked():
    recs = [
        # longhaul, deferral, peak
        _rec(km_pickup=7.0, alts=[_alt(free_at=12.0)], ts="2026-06-12T11:30:00+00:00"),
        # committed_late, nobody-load, off-peak
        _rec(km_pickup=1.0, best_extra={"late_pickup_committed_max": 18.0,
             "r6_max_bag_time_min": 24.0},
             alts=[_alt(free_at=40.0, ride=6.0)], pool=2,
             ts="2026-06-12T06:00:00+00:00"),
    ]
    s = _run(recs)
    assert s["by_reason"]["longhaul_pickup"]["AVOIDABLE"] == 1
    assert s["by_reason"]["committed_late"]["NOBODY"] == 1
    assert s["peak"]["AVOIDABLE"] == 1
    assert s["offpeak"]["NOBODY"] == 1


def test_parse_fail_counted():
    fd, p = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        f.write("nie-json\n")
        f.write(json.dumps(_rec(alts=[_alt(free_at=12.0)])) + "\n")
    try:
        s = T.analyze([p])
    finally:
        os.unlink(p)
    assert s["parse_fail"] == 1
    assert s["avoidable"] == 1
