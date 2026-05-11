"""V3.28 P3-D6 sprint: _greedy_plan geometry-aware tie-break.

Tech debt #29 + Lekcja #108. Case 472338 Ogniomistrz 10.05: _greedy_plan
geometry-blind → plan sequence cluster pickups w jednym końcu miasta,
deliv_spread=12.63km, r1_cos=-0.326. PRZESZŁO przez score gate.

Path A (this commit): sequence-aware trajectory smoothness tie-break.
Trajectory smoothness = average cosine between consecutive legs.
Higher = straighter trajectory (less zigzag).

Path B (deferred post-empirical): escalate KOORD gdy strategy=ortools_rejected_v3274
+ feasibility cosine < 0 dla wszystkich kandydatów (candidate-level pre-filter
w dispatch_pipeline, NIE plan-level tie-break).

Helper integrates do `_select_best_with_tie_breaker` jako 3a layer (przed legacy
first_drop_arrival_min). Flag-gated via existing ENABLE_V327_BUG_FIXES_BUNDLE
(no new flag), backward-compat: gdy `nodes` not passed, fall through legacy path.
"""
from datetime import datetime, timezone, timedelta

from dispatch_v2.route_simulator_v2 import (
    _plan_trajectory_smoothness,
    _select_best_with_tie_breaker,
    RoutePlanV2,
)


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s).replace(tzinfo=timezone.utc)


def _mk_plan(seq, total_min, sla_v=0):
    """Minimal plan stub — only fields used by tie-breaker."""
    return RoutePlanV2(
        sequence=seq,
        predicted_delivered_at={
            oid: _utc("2026-05-10T14:00:00") + timedelta(minutes=10 * i)
            for i, oid in enumerate(seq)
        },
        pickup_at={},
        total_duration_min=total_min,
        strategy="greedy",
        sla_violations=sla_v,
        osrm_fallback_used=False,
    )


def _mk_nodes(courier_pos, drops_by_oid):
    """Build nodes list: courier at [0], then delivery nodes."""
    nodes = [{"kind": "courier", "coords": courier_pos, "order_id": None, "ref": None}]
    for oid, coords in drops_by_oid.items():
        nodes.append({"kind": "delivery", "coords": coords, "order_id": oid, "ref": None})
    return nodes


# ─── _plan_trajectory_smoothness unit tests ──────────────────────────


def test_trajectory_smoothness_straight_line_high():
    """3 drops na N kierunku → straight line → smoothness ≈ 1.0."""
    courier = (53.13, 23.16)
    drops = {"A": (53.14, 23.16), "B": (53.15, 23.16), "C": (53.16, 23.16)}
    plan = _mk_plan(["A", "B", "C"], 30.0)
    nodes_by_oid = {oid: {"coords": c, "order_id": oid, "kind": "delivery"} for oid, c in drops.items()}
    s = _plan_trajectory_smoothness(plan, courier, nodes_by_oid)
    assert s is not None
    assert s > 0.95, f"Expected straight line smoothness ~1.0, got {s}"


def test_trajectory_smoothness_zigzag_low():
    """Drops zigzag (N, S, N, S) → smoothness ≈ -1.0."""
    courier = (53.13, 23.16)
    drops = {
        "N1": (53.15, 23.16),  # N
        "S1": (53.11, 23.16),  # S
        "N2": (53.15, 23.16),  # N
        "S2": (53.11, 23.16),  # S
    }
    plan = _mk_plan(["N1", "S1", "N2", "S2"], 30.0)
    nodes_by_oid = {oid: {"coords": c, "order_id": oid, "kind": "delivery"} for oid, c in drops.items()}
    s = _plan_trajectory_smoothness(plan, courier, nodes_by_oid)
    assert s is not None
    assert s < -0.95, f"Expected zigzag smoothness ~-1.0, got {s}"


def test_trajectory_smoothness_perpendicular_zero():
    """Drops 90° turns → smoothness ≈ 0.0."""
    courier = (53.13, 23.16)
    drops = {
        "N": (53.15, 23.16),   # courier→N
        "E": (53.15, 23.18),   # N→E (90° right)
        "S": (53.13, 23.18),   # E→S (90° right)
    }
    plan = _mk_plan(["N", "E", "S"], 30.0)
    nodes_by_oid = {oid: {"coords": c, "order_id": oid, "kind": "delivery"} for oid, c in drops.items()}
    s = _plan_trajectory_smoothness(plan, courier, nodes_by_oid)
    assert s is not None
    assert abs(s) < 0.1, f"Expected ~0 (perpendicular), got {s}"


def test_trajectory_smoothness_insufficient_points_returns_none():
    """Plan z 1 drop → tylko 1 leg → smoothness None."""
    courier = (53.13, 23.16)
    plan = _mk_plan(["A"], 10.0)
    nodes_by_oid = {"A": {"coords": (53.14, 23.16), "order_id": "A", "kind": "delivery"}}
    s = _plan_trajectory_smoothness(plan, courier, nodes_by_oid)
    assert s is None


def test_trajectory_smoothness_missing_node_skip():
    """Plan sequence ma oid nie w nodes_by_oid → skip gracefully."""
    courier = (53.13, 23.16)
    plan = _mk_plan(["A", "MISSING", "B"], 30.0)
    nodes_by_oid = {
        "A": {"coords": (53.14, 23.16), "order_id": "A", "kind": "delivery"},
        "B": {"coords": (53.15, 23.16), "order_id": "B", "kind": "delivery"},
    }
    # Only A and B valid → 2 drops → 2 legs → 1 cosine OK
    s = _plan_trajectory_smoothness(plan, courier, nodes_by_oid)
    assert s is not None  # Straight line


def test_trajectory_smoothness_no_courier_pos_returns_none():
    plan = _mk_plan(["A", "B"], 20.0)
    s = _plan_trajectory_smoothness(plan, None, {})
    assert s is None


# ─── _select_best_with_tie_breaker integration tests ─────────────────


def test_tie_break_with_nodes_prefers_straighter_plan():
    """Two plans z same total_duration ±2 min — wybierz straighter trajectory."""
    courier = (53.13, 23.16)
    # Plan A: straight line N
    plan_a = _mk_plan(["A1", "A2", "A3"], 25.0)
    # Plan B: zigzag (same SLA, same duration ±0.5 min)
    plan_b = _mk_plan(["Z1", "Z2", "Z3"], 25.3)

    # All drops in nodes
    nodes = _mk_nodes(courier, {
        "A1": (53.14, 23.16), "A2": (53.15, 23.16), "A3": (53.16, 23.16),
        "Z1": (53.15, 23.16), "Z2": (53.11, 23.16), "Z3": (53.15, 23.16),
    })

    now = _utc("2026-05-10T13:00:00")
    best = _select_best_with_tie_breaker([plan_a, plan_b], now, nodes=nodes)
    # Plan A straight → higher smoothness → preferred
    assert best.sequence == ["A1", "A2", "A3"], (
        f"Expected A (straight) preferred, got {best.sequence}"
    )


def test_tie_break_without_nodes_legacy_fallback():
    """nodes=None → legacy first_drop_arrival_min behavior (no regression)."""
    plan_a = _mk_plan(["A1", "A2"], 25.0)
    plan_b = _mk_plan(["B1", "B2"], 25.3)
    now = _utc("2026-05-10T13:00:00")
    # Without nodes, fall back to legacy — both should run
    best = _select_best_with_tie_breaker([plan_a, plan_b], now, nodes=None)
    assert best is not None


def test_tie_break_no_ties_returns_leader():
    """Pojedynczy plan z lower sla_v → no tie → return leader, smoothness irrelevant."""
    plan_a = _mk_plan(["A"], 25.0, sla_v=0)
    plan_b = _mk_plan(["B"], 30.0, sla_v=2)
    courier = (53.13, 23.16)
    nodes = _mk_nodes(courier, {"A": (53.14, 23.16), "B": (53.15, 23.16)})
    now = _utc("2026-05-10T13:00:00")
    best = _select_best_with_tie_breaker([plan_a, plan_b], now, nodes=nodes)
    assert best.sequence == ["A"]  # Lower sla_v wins primary sort


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
