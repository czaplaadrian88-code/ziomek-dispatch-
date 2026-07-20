"""Test B1b (base_score_decompose): klasyfikacja STRUKTURA vs ALGORYTM +
dominujący komponent kary, na syntetycznych rekordach.

Przypadki:
  - jawny v325 score-block → STRUKTURA / v325_score_blocked
  - legacy sentinel ze starego logu pozostaje rozpoznawalny
  - pool_feasible_count<=1 → STRUKTURA / pool<=1
  - R6 dominuje + realnie łamany (r6_max_bag_time_min>35) → STRUKTURA / r6_hard_breach
  - km_to_pickup>4.5 → STRUKTURA / longhaul_pickup
  - committed_late>10 → STRUKTURA / committed_late
  - blisko, R6 OK, pula>1, kara R8 stackuje → ALGORYTM
  - dominujący komponent = najgłębsza pojedyncza kara
  - PROPOSE/non-low-score ignorowany; parse-fail liczony
"""
import json
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from tools import base_score_decompose as D  # noqa: E402


def _rec(score=-200.0, pool=4, reason="all_candidates_low_score (best=1 score=-200<-100; feasible=4)",
         verdict="KOORD", ts="2026-06-12T11:30:00+00:00", **best_extra):
    best = {
        "score": score, "r6_bag_size": 3, "r6_max_bag_time_min": 25.0,
        "km_to_pickup": 1.0, "objm_r6_breach_count": 0,
        "late_pickup_committed_max": 0.0,
    }
    best.update(best_extra)
    return {"verdict": verdict, "reason": reason, "ts": ts,
            "order_id": 1, "pool_feasible_count": pool, "best": best}


def _run(records):
    fd, p = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    try:
        return D.analyze([p])
    finally:
        os.unlink(p)


def test_legacy_sentinel_is_struktura():
    s = _run([_rec(score=-1000000034.0, bonus_r_return_rest=-100.0)])
    assert s["struktura"] == 1 and s["algorytm"] == 0
    assert s["struct_reason_counts"]["sentinel_new_courier_legacy"] == 1
    assert s["score_hist"]["sentinel(<=-1e8)"] == 1


def test_explicit_v325_score_block_is_struktura_with_finite_score():
    s = _run([_rec(score=68.5, v325_score_blocked=True)])
    assert s["struktura"] == 1 and s["algorytm"] == 0
    assert s["struct_reason_counts"]["v325_score_blocked"] == 1
    assert s["score_hist"][">=-100"] == 1


def test_single_pool_is_struktura():
    s = _run([_rec(score=-160.0, pool=1, bonus_r8_soft_pen=-70.0)])
    assert s["struktura"] == 1
    assert s["struct_reason_counts"]["pool<=1"] == 1


def test_r6_hard_breach_is_struktura():
    # R6 dominuje (najgłębsza kara) I r6_max_bag_time_min > 35 → strukturalny
    s = _run([_rec(score=-180.0, r6_max_bag_time_min=37.0, bonus_r6_soft_pen=-90.0,
                   bonus_r8_soft_pen=-10.0)])
    assert s["struktura"] == 1
    assert s["struct_reason_counts"]["r6_hard_breach"] == 1
    assert s["dominant_component"]["bonus_r6_soft_pen"] == 1


def test_r6_dominates_but_not_breached_is_not_r6_struct():
    # R6 dominuje ale bag_time 28<35 i brak breach → NIE r6_hard_breach.
    # Pozostałe sygnały też nie → ALGORYTM.
    s = _run([_rec(score=-140.0, r6_max_bag_time_min=28.0, objm_r6_breach_count=0,
                   bonus_r6_soft_pen=-40.0)])
    assert s["algorytm"] == 1
    assert s["struct_reason_counts"].get("r6_hard_breach", 0) == 0


def test_longhaul_is_struktura():
    s = _run([_rec(score=-130.0, km_to_pickup=7.9, bonus_r5_detour=-40.0)])
    assert s["struktura"] == 1
    assert s["struct_reason_counts"]["longhaul_pickup"] == 1


def test_committed_late_is_struktura():
    s = _run([_rec(score=-260.0, late_pickup_committed_max=18.0, bonus_r8_soft_pen=-100.0)])
    assert s["struktura"] == 1
    assert s["struct_reason_counts"]["committed_late"] == 1


def test_near_gate_stacked_is_algorytm():
    # blisko (km 2.0), R6 OK (24 min, brak breach), pula 4, kara R8 stackuje → ALGORYTM
    s = _run([_rec(score=-121.0, pool=4, km_to_pickup=2.0, r6_max_bag_time_min=24.0,
                   bonus_r8_soft_pen=-67.0)])
    assert s["algorytm"] == 1 and s["struktura"] == 0
    assert s["dominant_component"]["bonus_r8_soft_pen"] == 1


def test_dominant_is_deepest_component():
    s = _run([_rec(score=-200.0, km_to_pickup=2.0, r6_max_bag_time_min=24.0,
                   bonus_r8_soft_pen=-30.0, bonus_r9_stopover=-50.0,
                   bonus_r1_soft_pen=-12.0)])
    # r9_stopover (-50) najgłębszy
    assert s["dominant_component"]["bonus_r9_stopover"] == 1


def test_propose_and_non_lowscore_ignored():
    recs = [
        _rec(verdict="PROPOSE", reason="feasible=1 best=1"),
        _rec(verdict="KOORD", reason="early_bird (foo)"),
    ]
    s = _run(recs)
    assert s["low_score"] == 0
    assert s["struktura"] == 0 and s["algorytm"] == 0
    assert s["koord_total"] == 1  # tylko KOORD early_bird liczony do KOORD total


def test_peak_offpeak_split():
    recs = [
        _rec(score=-130.0, km_to_pickup=7.0, ts="2026-06-12T11:30:00+00:00"),  # peak STRUKTURA
        _rec(score=-121.0, km_to_pickup=2.0, r6_max_bag_time_min=24.0,
             bonus_r8_soft_pen=-67.0, ts="2026-06-12T06:00:00+00:00"),         # off-peak ALGORYTM
    ]
    s = _run(recs)
    assert s["cls_peak"].get("STRUKTURA") == 1
    assert s["cls_offpeak"].get("ALGORYTM") == 1


def test_parse_fail_counted_not_crash():
    fd, p = tempfile.mkstemp(suffix=".jsonl")
    with os.fdopen(fd, "w") as f:
        f.write("nie-json\n")
        f.write(json.dumps(_rec(score=-130.0, km_to_pickup=7.0)) + "\n")
    try:
        s = D.analyze([p])
    finally:
        os.unlink(p)
    assert s["parse_fail"] == 1
    assert s["struktura"] == 1
