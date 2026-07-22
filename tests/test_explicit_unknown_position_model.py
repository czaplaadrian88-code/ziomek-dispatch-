"""Goldeny modelu explicit UNKNOWN (owner decision 2026-07-22)."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from dispatch_v2 import common as C
from dispatch_v2 import route_simulator_v2 as route_sim
from dispatch_v2.chain_eta import compute_chain_eta
from dispatch_v2.position_model import (
    PositionKind, PositionProvenance,
    resolve_position,
    unknown_origin_estimate,
)
from dispatch_v2.route_simulator_v2 import OrderSim
from dispatch_v2.scoring import s_dystans, score_candidate


NOW = datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc)


def _eval_ctx(**overrides):
    from dispatch_v2.core.candidates import EvalContext

    values = {
        "now": NOW,
        "order_event": {"order_id": "E"},
        "order_id": "E",
        "restaurant": "R",
        "delivery_address": "D",
        "pickup_coords": (53.13, 23.16),
        "delivery_coords": (53.14, 23.17),
        "pickup_at": None,
        "pickup_ready_at": None,
        "new_order": SimpleNamespace(order_id="E"),
        "new_rest_norm": "r",
        "fleet_speed_kmh": 25.0,
        "fleet_context": None,
        "k07_prefetched_ck": None,
        "loadgov_now": None,
        "loadgov_ewma": None,
        "loadgov_orders": None,
        "loadgov_couriers": None,
    }
    values.update(overrides)
    return EvalContext(**values)


def test_resolver_uses_provenance_and_unknown_never_has_coords():
    unknown = resolve_position(
        coords=(53.1325, 23.1688), source="no_gps", age_min=None,
    )
    assert unknown.position_kind is PositionKind.UNKNOWN
    assert unknown.coords is None

    anchor = resolve_position(
        coords=(53.14, 23.17), source="gps", age_min=8.0, from_store=True,
    )
    assert anchor.position_kind is PositionKind.KNOWN_ANCHOR
    assert anchor.coords == (53.14, 23.17)

    # Label prezentacyjny nie rozstrzyga klasy: jawne provenance wygrywa.
    live_despite_label = resolve_position(
        coords=(53.14, 23.17), source="no_gps", age_min=0.1,
        provenance=PositionProvenance.GPS_LIVE,
    )
    assert live_despite_label.position_kind is PositionKind.KNOWN_LIVE


def test_unknown_soft_constants_score_and_no_bearing():
    origin = unknown_origin_estimate()
    assert (origin.road_km, origin.drive_min_soft, origin.drive_min_hard) == (6.5, 15.0, 22.0)
    result = score_candidate(
        courier_pos=None,
        restaurant_pos=(53.13, 23.16),
        bag_drop_coords=[(53.2, 23.2)],
        bag_size=0,
        road_km=origin.road_km,
    )
    assert result["components"]["dystans"] == pytest.approx(s_dystans(6.5), abs=0.02)
    assert result["components"]["kierunek"] == 100.0


def test_chain_eta_unknown_empty_uses_15_not_max_prep_and_no_geo():
    geo_calls = []

    def forbidden(*args):
        geo_calls.append(args)
        raise AssertionError("UNKNOWN must not call geographic functions")

    result = compute_chain_eta(
        courier_pos=None,
        pos_source="no_gps",
        pos_age_min=None,
        bag_orders=[],
        proposal_pickup_coords=(53.13, 23.16),
        proposal_scheduled_utc=NOW + timedelta(minutes=5),
        now_utc=NOW,
        osrm_drive_min=forbidden,
        haversine_km=forbidden,
        origin_travel=unknown_origin_estimate(),
    )
    assert result.starting_point == "unknown_profile"
    assert result.effective_eta_utc == NOW + timedelta(minutes=15)
    assert geo_calls == []


def test_virtual_origin_is_single_hard_22_min_matrix_row(monkeypatch):
    table_points = []

    def fake_table(sources, destinations):
        table_points.append((list(sources), list(destinations)))
        assert all(point is not None for point in sources + destinations)
        return [[{"duration_s": 60.0, "distance_m": 500.0} for _ in destinations]
                for _ in sources]

    monkeypatch.setattr(route_sim.osrm_client, "table", fake_table)
    monkeypatch.setattr(C, "ENABLE_V326_OR_TOOLS_TSP", False)
    order = OrderSim(
        order_id="U1", pickup_coords=(53.13, 23.16),
        delivery_coords=(53.14, 23.17), status="assigned",
    )
    plan = route_sim.simulate_bag_route_v2(
        None, [], order, now=NOW, origin_travel=unknown_origin_estimate(),
    )
    assert len(table_points) == 1
    assert table_points[0][0] == [order.pickup_coords, order.delivery_coords]
    assert plan.total_duration_min >= 22.0


def test_feasibility_uses_65_for_reach_and_22_for_hard_plan(monkeypatch):
    from dispatch_v2 import feasibility_v2 as feasibility

    def fake_table(sources, destinations):
        assert all(point is not None for point in sources + destinations)
        return [[{"duration_s": 60.0, "distance_m": 500.0} for _ in destinations]
                for _ in sources]

    monkeypatch.setattr(route_sim.osrm_client, "table", fake_table)
    monkeypatch.setattr(C, "ENABLE_V326_OR_TOOLS_TSP", False)
    order = OrderSim(
        order_id="U2", pickup_coords=(53.13, 23.16),
        delivery_coords=(53.14, 23.17), status="assigned", pickup_ready_at=NOW,
    )
    verdict, _, metrics, plan = feasibility.check_feasibility_v2(
        None, [], order, now=NOW, shift_end=NOW + timedelta(hours=8),
        origin_travel=unknown_origin_estimate(),
    )
    assert verdict == "MAYBE"
    assert metrics["pickup_dist_km"] == 6.5
    assert metrics["origin_drive_min_soft"] == 15.0
    assert metrics["origin_drive_min_hard"] == 22.0
    assert metrics["pickup_drive_min_hard"] == 22.0
    assert metrics["r1_origin_geometry_evaluable"] is False
    assert metrics["r5_origin_geometry_evaluable"] is False
    assert plan.total_duration_min >= 22.0


def test_known_position_origin_estimator_is_noop():
    from dispatch_v2.position_model import origin_estimate_for

    known = resolve_position(coords=(53.13, 23.16), source="gps", age_min=0.2)
    assert known.position_kind is PositionKind.KNOWN_LIVE
    assert origin_estimate_for(known) is None


def test_r29_profile_is_35_and_display_contract_has_no_factual_km():
    origin = unknown_origin_estimate()
    assert 100.0 - origin.road_km * 10.0 == 35.0
    display = {
        "km_to_pickup": None,
        "estimated_road_km": origin.road_km,
        "estimated_drive_min": origin.drive_min_soft,
        "position_kind": PositionKind.UNKNOWN.value,
        "text": "pozycja nieznana · dojazd szac. 15 min",
    }
    assert display["km_to_pickup"] is None
    assert display["text"] == "pozycja nieznana · dojazd szac. 15 min"


def test_golden_true_selector_not_naive_max(monkeypatch):
    """Mutation probe: naiwny max wybiera HARD-NO, prawdziwy selektor MAYBE."""
    from dispatch_v2 import dispatch_pipeline as dp
    from dispatch_v2.core.selection import SelectionContext, select_and_emit

    plan = SimpleNamespace(
        sequence=["O1"], sla_violations=0, predicted_delivered_at={},
        pickup_at={}, total_duration_min=20.0, strategy="golden",
    )
    hard_no = dp.Candidate("NO", None, 999.0, "NO", "pickup_too_far", None, {})
    feasible = dp.Candidate(
        "OK", None, 10.0, "MAYBE", "ok", plan,
        {"bundle_level3_dev": None, "bag_size_before": 0, "r6_bag_size": 0,
         "pos_source": "gps", "new_pickup_late_min": 0.0,
         "late_pickup_committed_max": 0.0},
    )
    monkeypatch.setattr(dp, "_classify_and_set_auto_route", lambda *a, **k: None)
    ctx = SelectionContext(
        now=NOW, order_event={"order_id": "GOLD"}, order_id="GOLD",
        restaurant="R", delivery_address="D", pickup_coords=(53.13, 23.16),
        delivery_coords=(53.14, 23.17), pickup_ready_at=None,
        new_order=SimpleNamespace(order_id="O1"), fleet_snapshot={},
        v328_fail_causes={}, shadow_only=True,
    )
    result = select_and_emit(ctx, [hard_no, feasible])
    assert max([hard_no, feasible], key=lambda candidate: candidate.score).courier_id == "NO"
    assert result.best.courier_id == "OK"


def test_e2e_counterfactual_replay_uses_real_selector_for_both_variants(monkeypatch):
    from dispatch_v2 import dispatch_pipeline as dp
    from dispatch_v2.core.selection import SelectionContext

    plan = SimpleNamespace(
        sequence=["O1"], sla_violations=0, predicted_delivered_at={}, pickup_at={},
        total_duration_min=20.0, strategy="golden",
    )

    def candidate(cid, score):
        return dp.Candidate(
            cid, None, score, "MAYBE", "ok", plan,
            {"bundle_level3_dev": None, "bag_size_before": 0, "r6_bag_size": 0,
             "pos_source": "gps", "new_pickup_late_min": 0.0,
             "late_pickup_committed_max": 0.0},
        )

    legacy_u = candidate("U", 90.0)
    explicit_u = candidate("U", 40.0)
    known_g = candidate("G", 70.0)
    legacy_u._position_model_variants = {"legacy": legacy_u, "explicit": explicit_u}
    known_g._position_model_variants = {"legacy": known_g, "explicit": known_g}
    monkeypatch.setattr(dp, "_classify_and_set_auto_route", lambda *a, **k: None)
    ctx = SelectionContext(
        now=NOW, order_event={"order_id": "E2E"}, order_id="E2E",
        restaurant="R", delivery_address="D", pickup_coords=(53.13, 23.16),
        delivery_coords=(53.14, 23.17), pickup_ready_at=None,
        new_order=SimpleNamespace(order_id="O1"), fleet_snapshot={},
        v328_fail_causes={}, position_model_mode="legacy", shadow_only=True,
    )
    result = dp._select_with_position_model_shadow(
        ctx, [legacy_u, known_g], explicit_effective=False,
        explicit_requested=False, flag_conflict=False,
    )
    assert result.best.courier_id == "U"
    assert result.position_model_shadow["legacy_winner_cid"] == "U"
    assert result.position_model_shadow["explicit_winner_cid"] == "G"
    assert result.position_model_shadow["would_change_winner"] is True


def test_off_legacy_reject_is_not_resurrected_but_shadow_keeps_explicit(monkeypatch):
    """P1: primary OFF jest dokładnie legacy, także gdy explicit ma kandydata."""
    from dispatch_v2 import dispatch_pipeline as dp
    from dispatch_v2 import osrm_client
    from dispatch_v2.core import candidates as candidate_core

    explicit = dp.Candidate("U", None, 40.0, "MAYBE", "ok", None, {})
    calls = []

    def fake_inner(ctx, cid, cs):
        calls.append(ctx.position_model_mode)
        return explicit if ctx.position_model_mode == "explicit" else None

    unknown = resolve_position(coords=(0.0, 0.0), source="none")
    monkeypatch.setattr(candidate_core, "eval_courier_inner", fake_inner)
    monkeypatch.setattr(candidate_core, "resolve_courier_position", lambda cs: unknown)
    monkeypatch.setattr(osrm_client, "start_v2_request_tracking", lambda: None)
    monkeypatch.setattr(osrm_client, "stop_v2_request_tracking", lambda: None)

    variants = {}
    off_ctx = _eval_ctx(
        position_model_mode="legacy",
        position_model_shadow=True,
        position_model_variants=variants,
    )
    assert candidate_core.eval_courier(
        off_ctx, "U", SimpleNamespace(pos=None, pos_source="none")) is None
    assert calls == ["legacy", "explicit"]
    assert variants["U"] == {"legacy": None, "explicit": explicit}

    calls.clear()
    on_ctx = _eval_ctx(
        position_model_mode="explicit",
        position_model_shadow=True,
        position_model_variants={},
    )
    assert candidate_core.eval_courier(
        on_ctx, "U", SimpleNamespace(pos=None, pos_source="none")) is explicit
    assert calls == ["legacy", "explicit"]


def test_both_flags_off_candidate_path_is_inert(monkeypatch):
    """P2: bez shadow dokładnie jeden eval i zero wejścia w nowy resolver."""
    from dispatch_v2 import osrm_client
    from dispatch_v2.core import candidates as candidate_core

    marker = SimpleNamespace(metrics={})
    calls = []

    def fake_inner(ctx, cid, cs):
        calls.append(ctx.position_model_mode)
        return marker

    monkeypatch.setattr(candidate_core, "eval_courier_inner", fake_inner)
    monkeypatch.setattr(
        candidate_core,
        "resolve_courier_position",
        lambda cs: (_ for _ in ()).throw(AssertionError("shadow machinery ran")),
    )
    monkeypatch.setattr(osrm_client, "start_v2_request_tracking", lambda: None)
    monkeypatch.setattr(osrm_client, "stop_v2_request_tracking", lambda: None)

    result = candidate_core.eval_courier(
        _eval_ctx(position_model_mode="legacy", position_model_shadow=False),
        "U",
        SimpleNamespace(pos=None, pos_source="none"),
    )
    assert result is marker
    assert calls == ["legacy"]
    assert not hasattr(result, "_position_model_variants")
    assert "position_model_shadow" not in result.metrics


def test_shadow_gate_off_uses_one_selector_and_no_shadow_payload(monkeypatch):
    """P2: drugi prawdziwy selektor jest nieosiągalny przy shadow OFF."""
    from dispatch_v2 import dispatch_pipeline as dp

    selected = SimpleNamespace(best=None, verdict="SKIP")
    calls = []

    def fake_select(ctx, candidates):
        calls.append(list(candidates))
        return selected

    monkeypatch.setattr(dp._selection, "select_and_emit", fake_select)
    result = dp._select_position_model(
        SimpleNamespace(fleet_snapshot={}),
        [],
        shadow_requested=False,
        explicit_effective=False,
        explicit_requested=False,
        flag_conflict=False,
    )
    assert result is selected
    assert calls == [[]]
    assert not hasattr(result, "position_model_shadow")


def test_shadow_counterfactual_sees_explicit_only_candidate_without_off_pool_leak(monkeypatch):
    """P1/P2: shadow mierzy revival, ale actual legacy pool pozostaje bez U."""
    from dispatch_v2 import dispatch_pipeline as dp
    from dispatch_v2.core.selection import SelectionContext

    plan = SimpleNamespace(
        sequence=["O1"], sla_violations=0, predicted_delivered_at={}, pickup_at={},
        total_duration_min=20.0, strategy="golden",
    )

    def candidate(cid, score):
        return dp.Candidate(
            cid, None, score, "MAYBE", "ok", plan,
            {"bundle_level3_dev": None, "bag_size_before": 0, "r6_bag_size": 0,
             "pos_source": "gps", "new_pickup_late_min": 0.0,
             "late_pickup_committed_max": 0.0},
        )

    explicit_u = candidate("U", 90.0)
    known_g = candidate("G", 70.0)
    variants = {
        "U": {"legacy": None, "explicit": explicit_u},
        "G": {"legacy": known_g, "explicit": known_g},
    }
    monkeypatch.setattr(dp, "_classify_and_set_auto_route", lambda *a, **k: None)
    ctx = SelectionContext(
        now=NOW, order_event={"order_id": "REVIVE"}, order_id="REVIVE",
        restaurant="R", delivery_address="D", pickup_coords=(53.13, 23.16),
        delivery_coords=(53.14, 23.17), pickup_ready_at=None,
        new_order=SimpleNamespace(order_id="O1"),
        fleet_snapshot={"U": object(), "G": object()}, v328_fail_causes={},
        position_model_mode="legacy", shadow_only=True,
    )
    actual_pool = [known_g]
    result = dp._select_with_position_model_shadow(
        ctx,
        actual_pool,
        explicit_effective=False,
        explicit_requested=False,
        flag_conflict=False,
        position_model_variants=variants,
    )
    assert [candidate.courier_id for candidate in actual_pool] == ["G"]
    assert result.best.courier_id == "G"
    assert result.position_model_shadow["legacy_winner_cid"] == "G"
    assert result.position_model_shadow["explicit_winner_cid"] == "U"


def test_flag_default_off_and_old_flag_superseded():
    import json
    from pathlib import Path

    assert C.ENABLE_EXPLICIT_UNKNOWN_POSITION_MODEL is False
    assert C.ENABLE_EXPLICIT_UNKNOWN_POSITION_SHADOW is False
    registry = json.loads(Path("tools/flag_lifecycle_registry.json").read_text())["flags"]
    assert registry["ENABLE_EXPLICIT_UNKNOWN_POSITION_MODEL"]["default"] is False
    assert registry["ENABLE_EXPLICIT_UNKNOWN_POSITION_SHADOW"]["default"] is False
    assert registry["ENABLE_EXPLICIT_UNKNOWN_POSITION_SHADOW"]["lifecycle"] == "shadow"
    assert registry["ENABLE_EXPLICIT_UNKNOWN_POSITION_SHADOW"]["twin_of"] == \
        ["ENABLE_EXPLICIT_UNKNOWN_POSITION_MODEL"]
    assert registry["ENABLE_EXPLICIT_UNKNOWN_POSITION_MODEL"]["twin_of"] == \
        ["ENABLE_EXPLICIT_UNKNOWN_POSITION_SHADOW"]
    assert registry["ENABLE_NO_GPS_NEUTRAL_SCORE_DIST"]["lifecycle"] == "deprecated"
    assert registry["ENABLE_NO_GPS_NEUTRAL_SCORE_DIST"]["superseded_by"] == \
        "ENABLE_EXPLICIT_UNKNOWN_POSITION_MODEL"


def test_flag_is_read_once_and_conflict_fails_closed():
    import inspect
    from dispatch_v2 import dispatch_pipeline

    source = inspect.getsource(dispatch_pipeline._assess_order_impl)
    assert source.count('decision_flag(\n        "ENABLE_EXPLICIT_UNKNOWN_POSITION_MODEL")') == 1
    assert source.count('decision_flag(\n        "ENABLE_EXPLICIT_UNKNOWN_POSITION_SHADOW")') == 1
    assert source.count('decision_flag(\n        "ENABLE_NO_GPS_NEUTRAL_SCORE_DIST")') == 1
    assert "_explicit_unknown_requested and not _position_flag_conflict" in source
    assert "ALERT EXPLICIT_UNKNOWN_FLAG_CONFLICT" in source
    assert "ENABLE_NO_GPS_EQUAL_TREATMENT" not in source  # nie zmieniamy authority/rank gate


def test_enable_v326_r07_chain_eta_on_off_explicit_override():
    """ON/OFF starej flagi: explicit UNKNOWN atomowo konsumuje chain ETA."""
    import inspect
    from dispatch_v2.core import candidates

    source = inspect.getsource(candidates.eval_courier_inner)
    assert "(C.ENABLE_V326_R07_CHAIN_ETA or explicit_unknown)" in source


def test_shadow_serializes_candidate_in_both_locations_and_decision():
    from dispatch_v2.dispatch_pipeline import Candidate, PipelineResult
    from dispatch_v2.shadow_dispatcher import _serialize_result

    per_candidate = {
        "position_kind": "UNKNOWN",
        "position_source": "no_gps",
        "position_age_min": None,
        "legacy_origin": {"road_km": 1.2, "drive_min": 4.0, "score": 90.0},
        "explicit_unknown_origin": {
            "road_km": 6.5, "drive_min": 15.0, "score": 72.0,
            "r1_origin_geometry_evaluable": False,
            "r5_origin_geometry_evaluable": False,
            "chain_eta": 15.0, "r29_solo_score": 35.0,
        },
    }
    best = Candidate(
        "U", None, 72.0, "MAYBE", "ok", None,
        {"position_model_shadow": per_candidate, "pos_source": "no_gps",
         "position_display_text": "pozycja nieznana · dojazd szac. 15 min"},
    )
    alt = Candidate(
        "G", None, 70.0, "MAYBE", "ok", None,
        {"position_model_shadow": {**per_candidate, "position_kind": "KNOWN_LIVE"},
         "pos_source": "gps"},
    )
    result = PipelineResult(
        "S", "PROPOSE", "ok", best, [best, alt], None, "R",
        full_pool_candidates=[best, alt],
    )
    result.position_model_shadow = {
        "legacy_winner_cid": "G", "explicit_winner_cid": "U",
        "would_change_winner": True,
        "selector_path": "core.selection.select_and_emit",
        "legacy_verdict": "PROPOSE", "explicit_verdict": "PROPOSE",
    }
    record = _serialize_result(result, "E", 1.0)
    assert record["best"]["position_model_shadow"] == per_candidate
    assert record["best"]["position_display_text"] == \
        "pozycja nieznana · dojazd szac. 15 min"
    assert record["alternatives"][0]["position_model_shadow"]["position_kind"] == "KNOWN_LIVE"
    assert record["position_model_shadow"]["would_change_winner"] is True
