"""tsp_solver — generyczny parametr cost_matrix_min (objective override).

HISTORIA: plik powstał dla V3.28-P3-D1 (idle-as-cost augmentujący cost_matrix).
P3-D1 został RETIRED sprintem OBJ F2 (2026-05-18) — był strukturalnie wadliwy
(per-edge idle estimate dominował objective szumem ~6:1, diagnoza 474253).
Idle wyceniany jest teraz przez SetSpanCostCoefficient — patrz
test_obj_f2_span_cost.py.

Co ZOSTAJE: parametr `cost_matrix_min` w tsp_solver.solve_tsp_with_constraints
jest generyczną zdolnością solvera (osobny objective od constraint-dimension
cumul). Nie jest już używany przez _ortools_plan, ale pozostaje jako czysty,
przetestowany punkt rozszerzeń. T1-T3 pokrywają jego kontrakt.
"""
from __future__ import annotations

from dispatch_v2 import tsp_solver


def test_t1_cost_matrix_none_legacy_behavior():
    """cost_matrix_min=None → solver używa distance_matrix_km jako cost (legacy)."""
    distance = [[0, 5, 10], [5, 0, 5], [10, 5, 0]]
    time = [[0, 5, 10], [5, 0, 5], [10, 5, 0]]
    sol = tsp_solver.solve_tsp_with_constraints(
        num_stops=3,
        pickup_drop_pairs=[(1, 2)],
        distance_matrix_km=distance,
        time_matrix_min=time,
        cost_matrix_min=None,
    )
    assert sol is not None
    assert sol.sequence == [1, 2]


def test_t2_cost_matrix_wrong_shape_returns_none():
    """cost_matrix_min ze złą shape → return None (input validation)."""
    distance = [[0, 5, 10], [5, 0, 5], [10, 5, 0]]
    time = [[0, 5, 10], [5, 0, 5], [10, 5, 0]]
    bad_cost = [[0, 5], [5, 0]]  # 2x2 zamiast 3x3
    sol = tsp_solver.solve_tsp_with_constraints(
        num_stops=3,
        pickup_drop_pairs=[(1, 2)],
        distance_matrix_km=distance,
        time_matrix_min=time,
        cost_matrix_min=bad_cost,
    )
    assert sol is None
    bad_cost2 = [[0, 5], [5, 0, 5], [10, 5, 0]]  # 3x2 row mismatch
    sol2 = tsp_solver.solve_tsp_with_constraints(
        num_stops=3,
        pickup_drop_pairs=[(1, 2)],
        distance_matrix_km=distance,
        time_matrix_min=time,
        cost_matrix_min=bad_cost2,
    )
    assert sol2 is None


def test_t3_cost_matrix_overrides_objective():
    """cost_matrix_min override: solver minimalizuje cost_matrix, nie distance."""
    distance = [
        [0, 5, 5, 5],
        [5, 0, 5, 5],
        [5, 5, 0, 5],
        [5, 5, 5, 0],
    ]
    time = [
        [0, 5, 5, 5],
        [5, 0, 5, 5],
        [5, 5, 0, 5],
        [5, 5, 5, 0],
    ]
    # Cost: heavy penalty na 1→2 (cluster pickup edge)
    cost = [
        [0, 5, 5, 5],
        [5, 0, 100, 5],
        [5, 5, 0, 5],
        [5, 5, 5, 0],
    ]
    sol = tsp_solver.solve_tsp_with_constraints(
        num_stops=4,
        pickup_drop_pairs=[(1, 3)],
        distance_matrix_km=distance,
        time_matrix_min=time,
        cost_matrix_min=cost,
    )
    assert sol is not None
    seq = sol.sequence
    edges = list(zip([0] + seq, seq))
    assert (1, 2) not in edges, (
        f"Solver should avoid edge 1→2 (cost penalty), seq={seq}, edges={edges}"
    )
