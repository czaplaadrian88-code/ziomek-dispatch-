"""V3.28 Fix 2 (incident 03.05.2026) — TSP SetRange domain validation tests.

Root cause: tsp_solver.py:153 SetRange poza CumulVar domain [0, capacity_max]
→ OR-Tools raise "Exception: CP Solver fail" (production 470208/209/210).

Fix 2 walidacja:
- 2.A: NaN/Inf in time_window → skip stop's window
- 2.B: scaled_open > capacity_max → skip (empty intersection w domain)
- 2.C: scaled_close > capacity_max → clamp do capacity_max

Reproducer baseline: /tmp/incident_03_05/replay_minimal_setrange.py
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import tsp_solver  # noqa: E402


def _minimal_problem():
    """3-stop minimal P-D problem dla testów time window edge cases."""
    return {
        "num_stops": 3,
        "pickup_drop_pairs": [(1, 2)],
        "distance_matrix_km": [[0.0, 1.0, 2.0], [1.0, 0.0, 1.5], [2.0, 1.5, 0.0]],
        "time_matrix_min": [[0.0, 5.0, 10.0], [5.0, 0.0, 7.0], [10.0, 7.0, 0.0]],
        "time_limit_ms": 200,
        "max_route_min": 120.0,
    }


def test_in_domain_passes():
    """tw=(15, 75) within domain [0, 12000] → ROUTING_SUCCESS no crash."""
    p = _minimal_problem()
    sol = tsp_solver.solve_tsp_with_constraints(
        time_windows=[(0.0, 0.0), (15.0, 75.0), (0.0, 120.0)],
        **p,
    )
    assert sol is not None
    assert sol.solver_status == "ROUTING_SUCCESS"
    assert sol.sequence == [1, 2]


def test_open_out_of_domain_skipped():
    """tw_open=157 > max_route_min=120 → skip window, NO CRASH (Fix 2.B).

    Regression: production 470208/209/210 — pickup_ready_at - now > 120 min.
    """
    p = _minimal_problem()
    sol = tsp_solver.solve_tsp_with_constraints(
        time_windows=[(0.0, 0.0), (157.0, 217.0), (0.0, 120.0)],
        **p,
    )
    # Fix 2.B skips this window — solver continues bez tego constraint.
    # Result powinien być ROUTING_SUCCESS bo solver może find feasible bez time constraint.
    assert sol is not None
    assert sol.solver_status == "ROUTING_SUCCESS"
    assert sol.sequence == [1, 2]


def test_open_extreme_out_of_domain_skipped():
    """tw_open=1000 → skip, NO CRASH."""
    p = _minimal_problem()
    sol = tsp_solver.solve_tsp_with_constraints(
        time_windows=[(0.0, 0.0), (1000.0, 1500.0), (0.0, 120.0)],
        **p,
    )
    assert sol is not None
    assert sol.solver_status == "ROUTING_SUCCESS"


def test_nan_skipped():
    """tw=(NaN, NaN) → skip, NO CRASH (Fix 2.A)."""
    p = _minimal_problem()
    sol = tsp_solver.solve_tsp_with_constraints(
        time_windows=[(0.0, 0.0), (float("nan"), float("nan")), (0.0, 120.0)],
        **p,
    )
    assert sol is not None
    assert sol.solver_status == "ROUTING_SUCCESS"


def test_inf_skipped():
    """tw=(Inf, Inf) → skip, NO CRASH (Fix 2.A)."""
    p = _minimal_problem()
    sol = tsp_solver.solve_tsp_with_constraints(
        time_windows=[(0.0, 0.0), (float("inf"), float("inf")), (0.0, 120.0)],
        **p,
    )
    assert sol is not None
    assert sol.solver_status == "ROUTING_SUCCESS"


def test_close_clamped_to_capacity_max():
    """tw=(50, 200), max_route_min=120 → SetRange(5000, 12000) clamped (Fix 2.C).

    Verify że solver dostaje feasible upper bound zamiast crashować przy 200>120.
    """
    p = _minimal_problem()
    sol = tsp_solver.solve_tsp_with_constraints(
        time_windows=[(0.0, 0.0), (50.0, 200.0), (0.0, 120.0)],
        **p,
    )
    assert sol is not None
    assert sol.solver_status == "ROUTING_SUCCESS"
    assert sol.sequence == [1, 2]


def test_zero_zero_no_crash():
    """tw=(0, 0) — existing behavior (impossible window) → ROUTING_FAIL no crash."""
    p = _minimal_problem()
    sol = tsp_solver.solve_tsp_with_constraints(
        time_windows=[(0.0, 0.0), (0.0, 0.0), (0.0, 120.0)],
        **p,
    )
    assert sol is not None
    # ROUTING_FAIL (infeasible — pickup MUSI być w 0 min ale travel time > 0)
    assert sol.sequence == []


def test_negative_skipped_existing_behavior():
    """tw=(-5, 60) → existing behavior (open<0 skip) preserved."""
    p = _minimal_problem()
    sol = tsp_solver.solve_tsp_with_constraints(
        time_windows=[(0.0, 0.0), (-5.0, 60.0), (0.0, 120.0)],
        **p,
    )
    # Existing validation (open_min < 0) skips this window — no regression
    assert sol is not None
    assert sol.solver_status == "ROUTING_SUCCESS"


def test_close_less_than_open_skipped_existing_behavior():
    """tw=(60, 30) — close<open existing skip preserved."""
    p = _minimal_problem()
    sol = tsp_solver.solve_tsp_with_constraints(
        time_windows=[(0.0, 0.0), (60.0, 30.0), (0.0, 120.0)],
        **p,
    )
    assert sol is not None


def test_capacity_max_boundary_passes():
    """tw_open=120 exactly == max_route_min → SetRange(12000, 12000) within domain."""
    p = _minimal_problem()
    sol = tsp_solver.solve_tsp_with_constraints(
        time_windows=[(0.0, 0.0), (120.0, 120.0), (0.0, 120.0)],
        **p,
    )
    # Boundary: scaled_open=12000 == capacity_max (12000) → NIE > → przechodzi (Fix 2.B
    # check is strict `>`, not `>=`). Ale prawdopodobnie ROUTING_FAIL bo travel time
    # od courier_start nigdy <= 0.
    assert sol is not None  # NO CRASH — może być ROUTING_FAIL/SUCCESS, oba OK


def test_capacity_max_plus_one_skipped():
    """tw_open=121 (just over max=120) → skip window."""
    p = _minimal_problem()
    sol = tsp_solver.solve_tsp_with_constraints(
        time_windows=[(0.0, 0.0), (121.0, 200.0), (0.0, 120.0)],
        **p,
    )
    assert sol is not None
    assert sol.solver_status == "ROUTING_SUCCESS"


def test_pre_fix_baseline_reproducer_test2_now_passes():
    """REGRESSION: synthetic reproducer Test 2 (production 470208/209/210 pattern).

    Pre-Fix 2: CRASH "Exception: CP Solver fail".
    Post-Fix 2: ROUTING_SUCCESS via Fix 2.B skip window.
    """
    p = _minimal_problem()
    # Identyczny input jak /tmp/incident_03_05/replay_minimal_setrange.py Test 2
    sol = tsp_solver.solve_tsp_with_constraints(
        time_windows=[(0.0, 0.0), (157.0, 217.0), (0.0, 120.0)],
        **p,
    )
    assert sol is not None, "Fix 2.B should absorb out-of-domain (no crash)"
    assert sol.solver_status == "ROUTING_SUCCESS"


def test_pre_fix_baseline_reproducer_test5_nan_now_passes():
    """REGRESSION: synthetic Test 5 (NaN) was ValueError pre-fix."""
    p = _minimal_problem()
    sol = tsp_solver.solve_tsp_with_constraints(
        time_windows=[(0.0, 0.0), (float("nan"), float("nan")), (0.0, 120.0)],
        **p,
    )
    assert sol is not None
    assert sol.solver_status == "ROUTING_SUCCESS"


def test_partial_windows_some_valid_some_skipped():
    """Mix: stop 1 valid window, stop 2 NaN — only stop 2 skipped."""
    p = dict(_minimal_problem())
    p["num_stops"] = 5
    p["pickup_drop_pairs"] = [(1, 2), (3, 4)]
    # 5x5 matrix
    p["distance_matrix_km"] = [
        [0.0, 1.0, 2.0, 1.5, 2.5],
        [1.0, 0.0, 1.0, 2.0, 3.0],
        [2.0, 1.0, 0.0, 2.5, 3.5],
        [1.5, 2.0, 2.5, 0.0, 1.0],
        [2.5, 3.0, 3.5, 1.0, 0.0],
    ]
    p["time_matrix_min"] = [[d * 2 for d in row] for row in p["distance_matrix_km"]]
    sol = tsp_solver.solve_tsp_with_constraints(
        time_windows=[
            (0.0, 0.0),  # courier
            (15.0, 75.0),  # stop 1 OK
            (0.0, 120.0),  # stop 2 OK
            (float("nan"), float("nan")),  # stop 3 NaN — skip
            (0.0, 120.0),  # stop 4 OK
        ],
        **p,
    )
    assert sol is not None
    assert sol.solver_status == "ROUTING_SUCCESS"


def test_setrange_does_not_affect_callers_without_time_windows():
    """Backward compat: time_windows=None preserves pre-Fix 2 behavior."""
    p = _minimal_problem()
    sol = tsp_solver.solve_tsp_with_constraints(time_windows=None, **p)
    assert sol is not None
    assert sol.solver_status == "ROUTING_SUCCESS"
    assert sol.sequence == [1, 2]
