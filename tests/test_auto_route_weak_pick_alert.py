"""F4 AUTO-ROUTE WEAK-PICK ALERT (2026-05-24).

Słaby pick (ujemny score = obiektywnie wymuszony/zły wybór, Case D korpusu -20.34)
→ ALERT "wymaga decyzji" zamiast "🟡 ACK sensowny wybór". Flaga
ENABLE_AUTO_ROUTE_WEAK_PICK_ALERT (default OFF). Czasówki wykluczone (ZAWSZE ACK).
"""
import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2.auto_proximity_classifier import (
    classify_auto_route, ROUTE_ACK, ROUTE_ALERT,
)


def _cand(cid="c1", score=80.0, verdict="MAYBE", best_effort=False):
    return SimpleNamespace(courier_id=cid, score=score, feasibility_verdict=verdict,
                           plan=SimpleNamespace(sla_violations=0), metrics={},
                           best_effort=best_effort)


def _result(best_score, pool_feasible=1, candidates=None):
    best = _cand("c1", score=best_score)
    if candidates is None:
        candidates = [best]
    return SimpleNamespace(verdict="PROPOSE", best=best, candidates=candidates,
                           pool_feasible_count=pool_feasible, pool_total_count=6,
                           pickup_ready_at=datetime.now(timezone.utc) + timedelta(minutes=30))


def _fleet(best, tier="std"):
    return {best.courier_id: SimpleNamespace(tier_bag=tier,
            shift_end=datetime.now(timezone.utc) + timedelta(hours=2),
            shift_start=datetime.now(timezone.utc) - timedelta(hours=4),
            pos_source="gps")}


_ON = {"AUTO_PROXIMITY_SHADOW_ONLY": True, "ENABLE_AUTO_ROUTE_WEAK_PICK_ALERT": True}
_OFF = {"AUTO_PROXIMITY_SHADOW_ONLY": True, "ENABLE_AUTO_ROUTE_WEAK_PICK_ALERT": False}


def test_negative_score_flag_on_alerts():
    r = _result(best_score=-20.34)        # Case D
    route, reason = classify_auto_route(r, fleet_snapshot=_fleet(r.best), flags=_ON)
    assert route == ROUTE_ALERT
    assert "weak_pick_score=-20.3" in reason


def test_negative_score_flag_off_not_alert():
    r = _result(best_score=-20.34)
    route, reason = classify_auto_route(r, fleet_snapshot=_fleet(r.best), flags=_OFF)
    assert route == ROUTE_ACK            # bez flagi: stary flow (ACK)
    assert "weak_pick" not in reason


def test_positive_score_flag_on_not_weak_alert():
    r = _result(best_score=1.8)           # niski ale dodatni — nie weak-pick
    route, reason = classify_auto_route(r, fleet_snapshot=_fleet(r.best), flags=_ON)
    assert route != ROUTE_ALERT or "weak_pick" not in reason


def test_czasowka_negative_score_stays_ack():
    r = _result(best_score=-20.34)
    route, reason = classify_auto_route(r, fleet_snapshot=_fleet(r.best), flags=_ON,
                                        order_event={"prep_minutes": 60})
    assert route == ROUTE_ACK            # czasówka ZAWSZE ACK
    assert "czasowka" in reason


def test_custom_floor():
    r = _result(best_score=3.0)
    flags = dict(_ON, AUTO_ROUTE_WEAK_PICK_SCORE_FLOOR=5.0)   # floor 5 → score 3 < 5 → ALERT
    route, reason = classify_auto_route(r, fleet_snapshot=_fleet(r.best), flags=flags)
    assert route == ROUTE_ALERT
    assert "weak_pick_score=3.0<5" in reason


def test_best_effort_still_takes_priority():
    """best_effort ALERT ma priorytet (sprawdzany przed weak_pick) — reason best_effort."""
    best = _cand("c1", score=-5.0, best_effort=True)
    r = SimpleNamespace(verdict="PROPOSE", best=best, candidates=[best],
                        pool_feasible_count=0, pool_total_count=6,
                        pickup_ready_at=datetime.now(timezone.utc) + timedelta(minutes=30))
    route, reason = classify_auto_route(r, fleet_snapshot=_fleet(best), flags=_ON)
    assert route == ROUTE_ALERT
    assert "best_effort" in reason
