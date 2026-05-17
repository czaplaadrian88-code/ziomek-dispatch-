"""Testy sprint OBJ F1 — R6 soft upper bound w tsp_solver (2026-05-17).

delivery_soft_deadlines: per-węzeł (deadline_min, penalty_coeff). CumulVar węzła
ponad deadline → kara → solver przesuwa węzeł wcześniej w trasie.
"""
from dispatch_v2.tsp_solver import solve_tsp_with_constraints


def _matrix(n, val):
    """N×N — przekątna 0, reszta `val` (symetryczna)."""
    return [[0.0 if i == j else val for j in range(n)] for i in range(n)]


def test_soft_deadline_pulls_node_earlier():
    """3 węzły (courier+2 dostawy), dystanse symetryczne → bez deadline [1,2] i
    [2,1] równokosztowe. Ciasny deadline na stop 1 → solver stawia 1 przed 2."""
    dm = _matrix(3, 2.0)
    sol = solve_tsp_with_constraints(
        num_stops=3, pickup_drop_pairs=[],
        distance_matrix_km=dm, time_matrix_min=dm,
        delivery_soft_deadlines=[None, (0.0, 1000.0), None],
        time_limit_ms=200,
    )
    assert sol is not None and sol.sequence
    assert sol.sequence.index(1) < sol.sequence.index(2), \
        f"deadline na 1 powinien dać 1 przed 2; got {sol.sequence}"


def test_soft_deadline_other_node_symmetric():
    """Deadline na stop 2 → 2 przed 1 (kontrola: efekt nie jest artefaktem)."""
    dm = _matrix(3, 2.0)
    sol = solve_tsp_with_constraints(
        num_stops=3, pickup_drop_pairs=[],
        distance_matrix_km=dm, time_matrix_min=dm,
        delivery_soft_deadlines=[None, None, (0.0, 1000.0)],
        time_limit_ms=200,
    )
    assert sol is not None and sol.sequence
    assert sol.sequence.index(2) < sol.sequence.index(1), \
        f"deadline na 2 powinien dać 2 przed 1; got {sol.sequence}"


def test_soft_deadline_none_is_noop():
    """delivery_soft_deadlines=None → zachowanie bez zmian (rozwiązuje normalnie)."""
    dm = _matrix(3, 2.0)
    sol = solve_tsp_with_constraints(
        num_stops=3, pickup_drop_pairs=[],
        distance_matrix_km=dm, time_matrix_min=dm,
        delivery_soft_deadlines=None, time_limit_ms=200,
    )
    assert sol is not None and len(sol.sequence) == 2


def test_soft_deadline_wrong_length_rejected():
    """Lista o złej długości (≠ num_stops) → None (walidacja wejścia)."""
    dm = _matrix(3, 2.0)
    sol = solve_tsp_with_constraints(
        num_stops=3, pickup_drop_pairs=[],
        distance_matrix_km=dm, time_matrix_min=dm,
        delivery_soft_deadlines=[None, None],  # len 2 ≠ 3
        time_limit_ms=200,
    )
    assert sol is None


def test_soft_deadline_negative_deadline_safe():
    """Deadline ujemny (jedzenie przeterminowane) → clamp do 0, brak crasha."""
    dm = _matrix(3, 2.0)
    sol = solve_tsp_with_constraints(
        num_stops=3, pickup_drop_pairs=[],
        distance_matrix_km=dm, time_matrix_min=dm,
        delivery_soft_deadlines=[None, (-99.0, 1000.0), None],
        time_limit_ms=200,
    )
    assert sol is not None and sol.sequence.index(1) < sol.sequence.index(2)


def test_soft_deadline_nan_inf_coeff_skipped():
    """NaN/inf deadline lub coeff<=0 → węzeł pominięty, brak crasha."""
    dm = _matrix(3, 2.0)
    sol = solve_tsp_with_constraints(
        num_stops=3, pickup_drop_pairs=[],
        distance_matrix_km=dm, time_matrix_min=dm,
        delivery_soft_deadlines=[None, (float("nan"), 1000.0), (0.0, 0.0)],
        time_limit_ms=200,
    )
    assert sol is not None and len(sol.sequence) == 2


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
