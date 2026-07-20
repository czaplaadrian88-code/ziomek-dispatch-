"""Regresja 489107 (20.07.2026): -1e9 z V325 nie może trafić do best.score.

Żywa sygnatura była deterministyczna: jedyny feasible cid=538, tier ``new``
(V326 multiplier 1.2 => -10), po rampie z bag>=2. V325 wpisywał -1e9,
V326 dopisywał -10 i serializer emitował best.score=-1000000010.0 mimo
MAYBE/ok_sla_fits. Stan hard-skipu jest teraz booleanem, a score pozostaje
realną agregacją. Lex-R6 ON/OFF ma wskazać tego samego singletona.
"""
from datetime import datetime, timezone
from types import SimpleNamespace

import pytest

from dispatch_v2 import common as C
from dispatch_v2 import dispatch_pipeline as DP
from dispatch_v2 import shadow_dispatcher
from dispatch_v2.core import selection


NOW = datetime(2026, 7, 20, 11, 55, tzinfo=timezone.utc)


def _candidate(cid="538", score=78.5, *, tier="new", bag=2):
    return DP.Candidate(
        courier_id=cid,
        name=f"K{cid}",
        score=score,
        feasibility_verdict="MAYBE",
        feasibility_reason="ok_sla_fits",
        plan=None,
        metrics={
            "cs_tier_label": tier,
            "cs_tier_bag": tier,
            "bag_size_before": bag,
            "km_to_pickup": 1.2,
            "bundle_level3_dev": None,
            "pos_source": "gps",
            "objm_r6_breach_max_min": 0.0,
            "late_pickup_committed_max": 0.0,
            "new_pickup_late_min": 0.0,
        },
    )


def _flags(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_V325_NEW_COURIER_CAP", True)
    monkeypatch.setattr(C, "ENABLE_V326_SPEED_MULTIPLIER", True)
    monkeypatch.setattr(DP, "_new_courier_deliveries", lambda _cid: 45)
    monkeypatch.setattr(DP, "_load_speed_data", lambda: None)
    monkeypatch.setattr(
        C,
        "flag",
        lambda name, default=False: True
        if name == "ENABLE_NEW_COURIER_RAMP"
        else False,
    )
    monkeypatch.setattr(C, "decision_flag", lambda _name: False)


def _run_489107(monkeypatch):
    _flags(monkeypatch)
    cand = _candidate()
    feasible = DP._v325_new_courier_penalty([cand], order_id="489107", now=NOW)
    feasible = DP._v326_speed_multiplier_adjust(feasible, order_id="489107")
    return cand, feasible


def _selection_pool():
    """Odtwórz pool_total=8 / pool_feasible=1 bez wpuszczania HARD-NO do L7."""
    winner = _candidate()
    rejected = [
        DP.Candidate(
            courier_id=str(600 + idx),
            name=f"NO{idx}",
            score=200.0 + idx,
            feasibility_verdict="NO",
            feasibility_reason="hard_reject_fixture",
            plan=None,
            metrics={"bag_size_before": 0, "pos_source": "gps"},
        )
        for idx in range(7)
    ]
    return winner, [winner, *rejected]


def _select_489107(monkeypatch, *, lexr6_on):
    _flags(monkeypatch)
    monkeypatch.setattr(C, "ENABLE_V326_FLEET_LOAD_BALANCE", False)
    monkeypatch.setattr(C, "ENABLE_V326_MULTISTOP_TRAJECTORY", False)
    monkeypatch.setattr(C, "ENABLE_V326_TRANSPARENCY_RATIONALE", False)
    monkeypatch.setattr(C, "ENABLE_A2_RELIABILITY_SOFT_SCORE", False)
    monkeypatch.setattr(C, "ENABLE_GPS_AGE_DISCOUNT", False)
    monkeypatch.setattr(C, "load_flags", lambda: {})
    monkeypatch.setattr(
        C,
        "flag",
        lambda name, default=False: {
            "ENABLE_NEW_COURIER_RAMP": True,
            "ENABLE_ALWAYS_PROPOSE_ON_SATURATION": True,
            "ENABLE_OBJM_LEXR6_SELECT": lexr6_on,
        }.get(name, False),
    )
    winner, pool = _selection_pool()
    ctx = selection.SelectionContext(
        now=NOW,
        order_event={"order_id": "489107"},
        order_id="489107",
        restaurant="R",
        delivery_address="A",
        pickup_coords=(53.13, 23.16),
        delivery_coords=(53.14, 23.17),
        pickup_ready_at=None,
        new_order=SimpleNamespace(order_id="489107"),
        fleet_snapshot={},
        v328_fail_causes={},
    )
    return winner, selection.select_and_emit(ctx, pool)


def _numbers(value):
    if isinstance(value, bool):
        return
    if isinstance(value, (int, float)):
        yield float(value)
    elif isinstance(value, dict):
        for child in value.values():
            yield from _numbers(child)
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _numbers(child)


def test_489107_lexr6_on_off_same_winner_and_serialized_score_is_finite(monkeypatch):
    cand, feasible = _run_489107(monkeypatch)

    # OFF = bieżący pierwszy element po sortach score; ON = lex-R6 D2 pick.
    winner_off = feasible[0]
    winner_on = DP._objm_lexr6_d2_pick(feasible)
    assert winner_off is winner_on is cand
    assert winner_off.courier_id == "538"

    # 78.5 pozostaje realnym score V325, potem legalna kara V326 new=1.2: -10.
    assert cand.score == 68.5
    assert cand.metrics["v326_speed_score_adjustment"] == -10.0
    assert cand.metrics["v325_score_blocked"] is True
    assert cand.metrics["v325_blocked_rank_delta"] == pytest.approx(-10.0)
    assert cand.feasibility_verdict == "MAYBE"
    assert cand.feasibility_reason == "ok_sla_fits"

    result = DP.PipelineResult(
        order_id="489107",
        verdict="PROPOSE",
        reason="feasible=1 best=538",
        best=winner_on,
        candidates=[winner_on],
        pickup_ready_at=None,
        restaurant="R",
        delivery_address="A",
        pool_total_count=8,
        pool_feasible_count=1,
    )
    serialized = shadow_dispatcher._serialize_result(
        result, event_id="489107-test", latency_ms=1.0
    )
    assert serialized["best"]["score"] == 68.5
    assert serialized["best"]["feasibility"] == "MAYBE"
    assert serialized["best"]["reason"] == "ok_sla_fits"
    assert serialized["best"]["v325_score_blocked"] is True
    nums = list(_numbers(serialized["best"]))
    assert nums and min(nums) > -1e6


def test_489107_real_selection_flag_on_off_same_winner_in_pool_one_of_eight(monkeypatch):
    winner_off, result_off = _select_489107(monkeypatch, lexr6_on=False)
    winner_on, result_on = _select_489107(monkeypatch, lexr6_on=True)

    assert result_off.pool_total_count == result_on.pool_total_count == 8
    assert result_off.pool_feasible_count == result_on.pool_feasible_count == 1
    assert result_off.best is winner_off
    assert result_on.best is winner_on
    assert result_off.best.courier_id == result_on.best.courier_id == "538"
    assert result_off.best.score == result_on.best.score == 68.5
    assert result_off.best.metrics["v325_score_blocked"] is True
    assert result_on.best.metrics["v325_score_blocked"] is True


def test_blocked_rank_survives_v326_without_magic_score(monkeypatch):
    """Dopuszczony kandydat nadal bije blocked mimo znacznie niższego raw-score."""
    _flags(monkeypatch)
    blocked = _candidate(score=120.0)
    allowed = _candidate("400", score=-500.0, tier="std", bag=0)
    feasible = DP._v325_new_courier_penalty(
        [blocked, allowed], order_id="489107", now=NOW
    )
    feasible = DP._v326_speed_multiplier_adjust(feasible, order_id="489107")
    assert feasible == [allowed, blocked]
    assert blocked.score == 110.0
    assert min(blocked.score, allowed.score) > -1e6
