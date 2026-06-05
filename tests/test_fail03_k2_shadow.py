"""FAIL-03-K2 SHADOW faza 1 — testy pure func (log-only, zero mutacji verdiktu).

Decyzje Adriana 06-05: (1) zawsze PROPOSE+baner, (2) brak cap -> soft kara rosnaca,
(3) obejmuje no-GPS. Faza 1 = obecny best_effort + defer-est, bez re-symulacji.
"""
import sys, os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

from dispatch_v2.shadow_dispatcher import _fail03_k2_shadow, _fail03_defer_soft_penalty
from dispatch_v2 import common as C


# ---- soft-penalty curve (decyzja #2: rosnaca, bez cap) ----
def test_penalty_zero_within_free_window():
    assert _fail03_defer_soft_penalty(0) == 0.0
    assert _fail03_defer_soft_penalty(5) == 0.0
    assert _fail03_defer_soft_penalty(None) == 0.0


def test_penalty_rises_monotonic_no_cap():
    vals = [_fail03_defer_soft_penalty(d) for d in (6, 10, 22, 40, 80, 200)]
    # rosnaca (coraz bardziej ujemna), bez plateau/cap
    assert all(vals[i] > vals[i+1] for i in range(len(vals)-1))
    assert vals[-1] < -300  # 200min nie jest cap'owane


def test_penalty_steeper_above_threshold():
    # nachylenie powyzej 20min-nad-free musi byc wieksze niz ponizej
    low = _fail03_defer_soft_penalty(15) - _fail03_defer_soft_penalty(10)   # w strefie liniowej
    high = _fail03_defer_soft_penalty(40) - _fail03_defer_soft_penalty(35)  # w strefie stromej
    assert abs(high) > abs(low)


# ---- decyzja func ----
def _rec(best):
    return {"best": best, "verdict": "KOORD"}


def test_r6_breach_with_defer_always_proposes():
    best = {"courier_id": "179", "name": "Gabriel", "score": -18.7, "max_bag_time_min": 37.0,
            "free_at_min": 40.0, "time_to_pickup_ready_min": 15.0, "pos_source": "gps"}
    out = _fail03_k2_shadow(_rec(best), {"path": "best_effort_r6_breach_v2"})
    assert out["would_propose"] is True              # decyzja #1
    assert out["reason"] == "fail03_k2_defer"
    assert out["est_breach_min"] == 2.0
    assert out["defer_est_min"] == 25.0              # 40 - 15
    assert out["defer_soft_penalty"] < 0
    assert out["no_gps"] is False
    assert "R6" in out["banner"]


def test_lowscore_path_always_proposes():
    best = {"courier_id": "123", "score": -103.9, "max_bag_time_min": 20.0,
            "free_at_min": 30.0, "time_to_pickup_ready_min": 16.0, "pos_source": "gps"}
    out = _fail03_k2_shadow(_rec(best), {"path": "all_candidates_low_score"})
    assert out["would_propose"] is True
    assert out["reason"] == "fail03_k2_lowscore"
    assert out["est_breach_min"] == 0.0
    assert "słaba opcja" in out["banner"]


def test_no_gps_flagged():
    best = {"courier_id": "509", "score": -50.0, "max_bag_time_min": 40.0,
            "free_at_min": 20.0, "time_to_pickup_ready_min": 14.0, "pos_source": "no_gps"}
    out = _fail03_k2_shadow(_rec(best), {"path": "best_effort_r6_breach_v2"})
    assert out["no_gps"] is True
    assert "pozycja szacowana" in out["banner"]


def test_last_assigned_pickup_counts_as_no_live_gps():
    best = {"courier_id": "179", "score": -18.7, "max_bag_time_min": 37.0,
            "pos_source": "last_assigned_pickup"}
    out = _fail03_k2_shadow(_rec(best), {"path": "best_effort_r6_breach_v2"})
    assert out["no_gps"] is True


def test_no_best_flags_needs_fleet_phase2():
    out = _fail03_k2_shadow({"best": None, "verdict": "KOORD"}, {"path": "no_solo_candidates"})
    assert out["would_propose"] is False
    assert out["reason"] == "fail03_k2_no_candidate"


def test_defer_none_when_timing_missing():
    best = {"courier_id": "179", "score": -10.0, "max_bag_time_min": 38.0, "pos_source": "gps"}
    out = _fail03_k2_shadow(_rec(best), {"path": "best_effort_r6_breach_v2"})
    assert out["defer_est_min"] is None
    assert out["defer_soft_penalty"] == 0.0
    assert out["reason"] == "fail03_k2_best_effort"


def test_flag_off_returns_none(monkeypatch):
    monkeypatch.setattr(C, "flag", lambda name, default=False: False if name == "ENABLE_FAIL03_K2_SHADOW" else default)
    out = _fail03_k2_shadow(_rec({"courier_id": "1", "max_bag_time_min": 40}), {"path": "best_effort_r6_breach_v2"})
    assert out is None


def test_zero_mutation_of_input_record():
    # func nie moze mutowac record (zero side-effect na verdict)
    rec = _rec({"courier_id": "179", "max_bag_time_min": 37.0, "free_at_min": 30, "time_to_pickup_ready_min": 15, "pos_source": "gps"})
    before = dict(rec)
    _fail03_k2_shadow(rec, {"path": "best_effort_r6_breach_v2"})
    assert rec["verdict"] == before["verdict"]
    assert "fail03_k2_shadow" not in rec  # func zwraca, NIE wpina sama
