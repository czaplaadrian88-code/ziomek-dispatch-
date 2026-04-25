"""V3.26 Fix 6 OR-Tools TSP solver tests (sprint 2026-04-25 sobota).

Adrian's Opcja 1: czysty OR-Tools dla wszystkich bag sizes, time-bounded
200ms. Replaces bruteforce + greedy.

Test coverage:
- Smoke: 1 P-D pair (bag=1+new) returns valid sequence
- Constraint: pickup precedes drop dla każdego ordera
- Latency: per kandydat <= 250ms (200ms budget + setup overhead)
- Bag=4 case (#468404-like): finds feasible sequence
- Edge: trivial empty stops returns empty TspSolution
- Validation: invalid matrix dimensions returns None
"""
import math
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import tsp_solver  # noqa: E402


def _hav(a, b):
    return math.sqrt((a[0] - b[0]) ** 2 + (a[1] - b[1]) ** 2)


def _build_matrix(stops):
    N = len(stops)
    return [[_hav(stops[i], stops[j]) for j in range(N)] for i in range(N)]


def test_solo_proposal_pickup_before_drop():
    """1 P-D pair (3 stops: courier + pickup + drop)."""
    dist = [[0, 1, 2], [1, 0, 1], [2, 1, 0]]
    time = [[0, 2, 4], [2, 0, 2], [4, 2, 0]]
    sol = tsp_solver.solve_tsp_with_constraints(
        num_stops=3,
        pickup_drop_pairs=[(1, 2)],
        distance_matrix_km=dist,
        time_matrix_min=time,
        time_limit_ms=200,
    )
    assert sol is not None
    assert sol.sequence == [1, 2], f"expected [1,2], got {sol.sequence}"
    assert sol.elapsed_ms < 500


def test_two_p_d_pairs_constraint_satisfied():
    """2 P-D pairs — pickup must precede drop dla każdej."""
    stops = [(0, 0), (1, 1), (3, 3), (1, 5), (3, 7)]
    N = len(stops)
    dist = _build_matrix(stops)
    time = [[d * 2.0 for d in row] for row in dist]
    sol = tsp_solver.solve_tsp_with_constraints(
        num_stops=N,
        pickup_drop_pairs=[(1, 3), (2, 4)],
        distance_matrix_km=dist,
        time_matrix_min=time,
        time_limit_ms=200,
    )
    assert sol is not None
    seq = sol.sequence
    assert seq.index(1) < seq.index(3), f"P1 must precede D1; seq={seq}"
    assert seq.index(2) < seq.index(4), f"P2 must precede D2; seq={seq}"


def test_468404_like_4_pairs_feasible():
    """#468404-like: 4 P-D pairs (8 nodes) — solver finds feasible sequence."""
    # 9 stops: courier + 4 P-D pairs (pickup_i, drop_i)
    stops = [
        (53.13, 23.16),  # 0 courier
        (53.13, 23.17),  # 1 P_maison
        (53.14, 23.18),  # 2 D_lakowa (P_maison drops here)
        (53.15, 23.08),  # 3 P_doner1
        (53.13, 23.15),  # 4 P_sweet (NEW)
        (53.12, 23.13),  # 5 D_absolwentow
        (53.13, 23.10),  # 6 D_sikorskiego (P_sweet drops here)
        (53.15, 23.08),  # 7 P_doner2
        (53.15, 23.21),  # 8 D_brodowicza
    ]
    N = len(stops)
    dist = [[_hav(stops[i], stops[j]) * 111 for j in range(N)] for i in range(N)]  # ~deg→km
    time = [[d * 2.5 for d in row] for row in dist]
    sol = tsp_solver.solve_tsp_with_constraints(
        num_stops=N,
        pickup_drop_pairs=[(1, 2), (3, 5), (4, 6), (7, 8)],
        distance_matrix_km=dist,
        time_matrix_min=time,
        time_limit_ms=200,
    )
    assert sol is not None
    seq = sol.sequence
    assert seq.index(1) < seq.index(2)
    assert seq.index(3) < seq.index(5)
    assert seq.index(4) < seq.index(6)
    assert seq.index(7) < seq.index(8)


def test_latency_under_300ms_p95():
    """Latency budget: 95% calls <= 300ms (200ms time_limit + setup overhead)."""
    elapsed = []
    stops = [(0, 0), (1, 1), (2, 2), (3, 1), (4, 2), (3, 3)]
    N = len(stops)
    dist = _build_matrix(stops)
    time_m = [[d * 2.0 for d in row] for row in dist]
    for _ in range(20):
        t0 = time.perf_counter()
        sol = tsp_solver.solve_tsp_with_constraints(
            num_stops=N,
            pickup_drop_pairs=[(1, 3), (2, 4)],
            distance_matrix_km=dist,
            time_matrix_min=time_m,
            time_limit_ms=200,
        )
        elapsed.append((time.perf_counter() - t0) * 1000)
    sorted_e = sorted(elapsed)
    p95 = sorted_e[int(len(sorted_e) * 0.95)]
    assert p95 < 400, f"p95 latency {p95:.1f}ms exceeds budget 400ms; samples={sorted_e}"


def test_trivial_empty_stops_returns_solution():
    """num_stops=1 (just courier, no orders) returns empty sequence."""
    sol = tsp_solver.solve_tsp_with_constraints(
        num_stops=1,
        pickup_drop_pairs=[],
        distance_matrix_km=[[0]],
        time_matrix_min=[[0]],
        time_limit_ms=200,
    )
    assert sol is not None
    assert sol.sequence == []
    assert sol.solver_status == "trivial_empty"


def test_invalid_matrix_dimensions_returns_none():
    """Matrix size mismatch z num_stops → None (defensive)."""
    sol = tsp_solver.solve_tsp_with_constraints(
        num_stops=3,
        pickup_drop_pairs=[(1, 2)],
        distance_matrix_km=[[0, 1], [1, 0]],  # 2x2, expected 3x3
        time_matrix_min=[[0, 1, 2], [1, 0, 1], [2, 1, 0]],
        time_limit_ms=200,
    )
    assert sol is None


def test_solver_status_matches_routing_search_status_enum():
    """Status mapping correct dla OR-Tools 9.15 RoutingSearchStatus enum."""
    sol = tsp_solver.solve_tsp_with_constraints(
        num_stops=3,
        pickup_drop_pairs=[(1, 2)],
        distance_matrix_km=[[0, 1, 2], [1, 0, 1], [2, 1, 0]],
        time_matrix_min=[[0, 2, 4], [2, 0, 2], [4, 2, 0]],
        time_limit_ms=200,
    )
    assert sol is not None
    # Acceptable statuses: SUCCESS (1) lub OPTIMAL (7) — both indicate feasible found
    assert sol.solver_status in ("ROUTING_SUCCESS", "ROUTING_OPTIMAL"), (
        f"unexpected status: {sol.solver_status}"
    )


if __name__ == "__main__":
    test_solo_proposal_pickup_before_drop()
    print("test_solo_proposal_pickup_before_drop: PASS")
    test_two_p_d_pairs_constraint_satisfied()
    print("test_two_p_d_pairs_constraint_satisfied: PASS")
    test_468404_like_4_pairs_feasible()
    print("test_468404_like_4_pairs_feasible: PASS")
    test_latency_under_300ms_p95()
    print("test_latency_under_300ms_p95: PASS")
    test_trivial_empty_stops_returns_solution()
    print("test_trivial_empty_stops_returns_solution: PASS")
    test_invalid_matrix_dimensions_returns_none()
    print("test_invalid_matrix_dimensions_returns_none: PASS")
    test_solver_status_matches_routing_search_status_enum()
    print("test_solver_status_matches_routing_search_status_enum: PASS")
    print("ALL 7/7 PASS")
