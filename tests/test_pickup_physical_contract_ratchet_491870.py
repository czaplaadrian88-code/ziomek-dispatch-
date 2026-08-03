"""Strukturalny ratchet jednego kontraktu fizycznego stopu Iteracji 2."""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

from dispatch_v2 import feasibility_v2
from dispatch_v2 import plan_recheck
from dispatch_v2 import route_order
from dispatch_v2 import same_restaurant_grouper
from dispatch_v2 import shadow_dispatcher


ROOT = Path(__file__).resolve().parents[1]


def _called_attributes(fn):
    tree = ast.parse(inspect.getsource(fn))
    return {
        (node.func.value.id, node.func.attr)
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
    }


def _called_names(fn):
    tree = ast.parse(inspect.getsource(fn))
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def test_identity_owner_is_address_and_time_b_not_distance():
    point_source = inspect.getsource(route_order.same_physical_pickup_point)
    point_calls = _called_names(route_order.same_physical_pickup_point)
    stop_calls = _called_names(route_order.same_physical_pickup_stop)
    assert "pickup_physical_key" in point_calls
    assert {"same_physical_pickup_point", "_pickup_spread_ok"} <= stop_calls
    assert "pickup_coords" not in point_source
    assert "haversine" not in point_source
    assert "PICKUP_MERGE_MIN" in inspect.getsource(route_order._pickup_spread_ok)


def test_all_twin_consumers_delegate_to_route_order_owner():
    expected = {
        plan_recheck._detect_departed_pickup_revisit: (
            "_route_order",
            "same_physical_pickup_point",
        ),
        feasibility_v2.detect_return_to_restaurant: (
            "_route_order",
            "same_physical_pickup_point",
        ),
        same_restaurant_grouper.group_orders_by_restaurant: (
            "_route_order",
            "group_physical_pickup_stops",
        ),
        shadow_dispatcher._probe_same_restaurant_race: (
            "_route_order",
            "same_physical_pickup_point",
        ),
    }
    for consumer, call in expected.items():
        assert call in _called_attributes(consumer), consumer.__name__


def test_no_second_plan_time_evaluator_beside_merged_time_b_authority():
    assert (ROOT / "committed_pickup_authority.py").is_file()
    assert not (ROOT / "core" / "pickup_time_rules.py").exists()
    plan_source = inspect.getsource(plan_recheck._lex_committed_window_reorder)
    assert "committed_pickup_authority" not in plan_source
    assert "czas_kuriera_warsaw" in plan_source


def test_new_behavior_is_structurally_inside_one_flag_boundary():
    lex_source = inspect.getsource(plan_recheck._lex_committed_window_reorder)
    f5_source = inspect.getsource(feasibility_v2.detect_return_to_restaurant)
    assert route_order.PHYSICAL_PICKUP_FLAG == (
        "ENABLE_NONCARRIED_COMMITTED_PICKUP_REORDER"
    )
    assert inspect.signature(route_order.order_podjazdy).parameters[
        "physical_contract"
    ].default is False
    assert inspect.signature(route_order.build_route_stops).parameters[
        "physical_contract"
    ].default is False
    assert "ENABLE_NONCARRIED_COMMITTED_PICKUP_REORDER" in lex_source
    assert "ENABLE_NONCARRIED_COMMITTED_PICKUP_REORDER" in f5_source
    assert "_r6_candidate_not_worse" in lex_source
    assert "HARD_NO_RETURN" in lex_source


def test_route_order_owner_stays_pure_and_flag_is_injected_by_consumers():
    module_tree = ast.parse((ROOT / "route_order.py").read_text(encoding="utf-8"))
    imported = {
        alias.name
        for node in ast.walk(module_tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
        for alias in node.names
    }
    assert not any(name.startswith("dispatch_v2") for name in imported)
    assert "ENABLE_NONCARRIED_COMMITTED_PICKUP_REORDER" in inspect.getsource(
        same_restaurant_grouper.group_orders_by_restaurant
    )
    assert "ENABLE_NONCARRIED_COMMITTED_PICKUP_REORDER" in inspect.getsource(
        shadow_dispatcher._probe_same_restaurant_race
    )
