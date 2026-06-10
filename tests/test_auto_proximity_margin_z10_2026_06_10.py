"""Z-10 (audyt 2026-06-10) — margin AUTO na FINALNYM rankingu + C7 best==score-top.

Bug: margin = top1−top2 po surowym score wśród feasible, a result.best wybierany
PO demote/tieringu (V3.16 blind-empty demote, late_pickup Opcja B) → margin
potrafił opisywać dwóch NIE-wybranych kandydatów; AUTO mogło odpalić na best
który NIE jest score-topem.

Fix (flaga ENABLE_F7_MARGIN_FINAL_RANKING, env default ON, hot-reload przez
flags dict): margin = score(result.best) − max(score POZOSTAŁYCH feasible);
C7 w _meets_high_conf: not best_is_score_top → ACK reason "best_not_score_top".
Prerequisite flipu Fazy 7.
"""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2.auto_proximity_classifier import (
    classify_auto_route,
    build_context_for_logging,
    ROUTE_AUTO,
    ROUTE_ACK,
)

_FLAGS_BASE = {
    "AUTO_PROXIMITY_ENABLED": False,
    "AUTO_PROXIMITY_SHADOW_ONLY": True,
    "AUTO_PROXIMITY_THRESHOLD": "T1",
    "PARSER_DEGRADED": False,
    "ENABLE_KEBAB_KROL_DINNER_EXCLUSION": False,
    "ENABLE_F7_MARGIN_FINAL_RANKING": True,
}


def _cand(cid, score, verdict="MAYBE"):
    return SimpleNamespace(
        courier_id=cid, score=score, feasibility_verdict=verdict,
        plan=SimpleNamespace(sla_violations=0), metrics={}, best_effort=False,
    )


def _result(best, candidates, pool_feasible=None):
    feasible = [c for c in candidates if c.feasibility_verdict == "MAYBE"]
    return SimpleNamespace(
        verdict="PROPOSE", best=best, candidates=candidates,
        pool_feasible_count=pool_feasible if pool_feasible is not None else len(feasible),
        pool_total_count=len(candidates),
        pickup_ready_at=datetime.now(timezone.utc) + timedelta(minutes=30),
    )


def _fleet(best, tier="gold"):
    return {best.courier_id: SimpleNamespace(
        tier_bag=tier,
        shift_end=datetime.now(timezone.utc) + timedelta(hours=2),
        shift_start=datetime.now(timezone.utc) - timedelta(hours=4),
        pos_source="gps",
    )}


def test_auto_when_best_is_score_top():
    """Happy path: best=argmax, margin 20 ≥ T1 min 15 → AUTO."""
    best = _cand("c1", 80.0)
    res = _result(best, [best, _cand("c2", 60.0), _cand("c3", 40.0)])
    route, reason = classify_auto_route(res, _fleet(best), flags=dict(_FLAGS_BASE))
    assert route == ROUTE_AUTO, reason


def test_best_not_score_top_returns_ack_with_reason():
    """KIERUNKOWY: best wybrany po demote (60.0) gdy argmax=80.0 → ACK best_not_score_top.

    Legacy liczyłby margin top1−top2 = 80−60 = 20 ≥ 15 i dał AUTO na best
    który NIE jest score-topem.
    """
    best = _cand("c2", 60.0)  # wybrany po demote — NIE argmax
    res = _result(best, [_cand("c1", 80.0), best, _cand("c3", 40.0)])
    route, reason = classify_auto_route(res, _fleet(best), flags=dict(_FLAGS_BASE))
    assert route == ROUTE_ACK
    assert reason == "best_not_score_top", reason


def test_margin_measured_from_best_not_top2():
    """Margin = score(best) − max(pozostali): 80 vs 70 = 10 < 15 (T1) → ACK C2.

    Legacy: top1−top2 też 10 tutaj — przypadek różnicujący jest w teście wyżej;
    ten pilnuje że nowy margin przechodzi przez próg C2 poprawnie.
    """
    best = _cand("c1", 80.0)
    res = _result(best, [best, _cand("c2", 70.0), _cand("c3", 40.0)])
    route, reason = classify_auto_route(res, _fleet(best), flags=dict(_FLAGS_BASE))
    assert route == ROUTE_ACK
    assert reason.startswith("C2_score_margin=10.0"), reason


def test_margin_excludes_best_by_courier_id_not_position():
    """Best na ostatniej pozycji listy — margin liczony po courier_id, nie indeksie."""
    best = _cand("c3", 90.0)
    res = _result(best, [_cand("c1", 70.0), _cand("c2", 60.0), best])
    route, reason = classify_auto_route(res, _fleet(best), flags=dict(_FLAGS_BASE))
    assert route == ROUTE_AUTO, reason  # margin 90−70=20 ≥ 15


def test_tie_treated_as_score_top():
    """Remis score (float equality) → best wciąż score-top; margin 0 → ACK C2 (nie C7)."""
    best = _cand("c1", 80.0)
    res = _result(best, [best, _cand("c2", 80.0)])
    route, reason = classify_auto_route(res, _fleet(best), flags=dict(_FLAGS_BASE))
    assert route == ROUTE_ACK
    assert reason.startswith("C2_score_margin"), reason


def test_solo_feasible_margin_zero():
    """Solo feasible: margin 0 (undefined) → C1/C2 odetnie zależnie od progu."""
    best = _cand("c1", 80.0)
    res = _result(best, [best], pool_feasible=1)
    route, reason = classify_auto_route(res, _fleet(best), flags=dict(_FLAGS_BASE))
    assert route == ROUTE_ACK
    assert reason.startswith("C1_pool_feasible"), reason


def test_flag_off_legacy_margin_and_no_c7():
    """Kill-switch OFF: stary margin top1−top2 + brak C7 → AUTO na nie-argmax best (legacy bug)."""
    flags = dict(_FLAGS_BASE)
    flags["ENABLE_F7_MARGIN_FINAL_RANKING"] = False
    best = _cand("c2", 60.0)
    res = _result(best, [_cand("c1", 80.0), best, _cand("c3", 40.0)])
    route, reason = classify_auto_route(res, _fleet(best), flags=flags)
    assert route == ROUTE_AUTO, reason  # legacy zachowanie zachowane dla rollbacku


def test_context_logging_exposes_best_is_score_top():
    best = _cand("c2", 60.0)
    res = _result(best, [_cand("c1", 80.0), best])
    ctx = build_context_for_logging(res, _fleet(best), flags=dict(_FLAGS_BASE))
    assert ctx["auto_route_best_is_score_top"] is False
    assert ctx["auto_route_score_margin"] == -20.0

    best2 = _cand("c1", 80.0)
    res2 = _result(best2, [best2, _cand("c2", 60.0)])
    ctx2 = build_context_for_logging(res2, _fleet(best2), flags=dict(_FLAGS_BASE))
    assert ctx2["auto_route_best_is_score_top"] is True
    assert ctx2["auto_route_score_margin"] == 20.0
