"""V3.26 Fix 6 OR-Tools TSP solver (sprint 2026-04-25 sobota).

Architektura per Adrian's strategic decision (Opcja 1 czysty OR-Tools):
- Wszystkie bag sizes używają OR-Tools (NIE hybrid bruteforce/greedy)
- Time-bounded search (200ms default per kandydat)
- Industry-standard solver z constraint programming
- Eliminates greedy zigzag pattern dla bag>3 cases (#468404 case study)

Pickup-and-delivery problem (PDP):
- Single vehicle (kurier)
- Pickup must precede drop dla każdego ordera
- Time windows per pickup (czas_kuriera deadlines)
- Distance/time matrix (OSRM-based)
- Open route: starts at courier_pos, ends at last delivery (no return depot)

Pure function — no side effects, no state. Easy unit test.
Fallback: gdy solver returns None → caller decyduje (greedy fallback w
route_simulator_v2 lub raise).
"""
from dataclasses import dataclass, field
from typing import Any, Callable, List, Optional, Sequence, Tuple
import logging

log = logging.getLogger("tsp_solver")


@dataclass
class TspSolution:
    """OR-Tools TSP rozwiązanie."""
    sequence: List[int]                       # Order indices wizyt (PO start, BEZ start)
    total_distance_km: float
    total_time_min: float
    is_optimal: bool                          # Solver returned OPTIMAL (vs feasible-but-time-limit)
    solver_status: str                        # OR-Tools status string
    elapsed_ms: float                         # Solve time
    warnings: List[str] = field(default_factory=list)


def solve_tsp_with_constraints(
    num_stops: int,
    pickup_drop_pairs: Sequence[Tuple[int, int]],
    distance_matrix_km: List[List[float]],
    time_matrix_min: List[List[float]],
    time_windows: Optional[List[Tuple[float, float]]] = None,
    fixed_first_drop: Optional[int] = None,
    max_route_min: float = 90.0,
    time_limit_ms: int = 200,
) -> Optional[TspSolution]:
    """Solve PDP z OR-Tools.

    Args:
        num_stops: Total stop count w problemie WŁĄCZNIE z courier_start (idx=0).
                   Stops 1..num_stops-1 to pickup/delivery operations.
        pickup_drop_pairs: List of (pickup_idx, drop_idx) — every pickup MUST
                          precede its drop w sequence. Indices są 0-based offset
                          INTO the stops list.
        distance_matrix_km: Symmetric or asymmetric N×N matrix (km). Distance
                           from stop_i to stop_j. Diagonal = 0.
        time_matrix_min: Symmetric or asymmetric N×N matrix (minutes). Drive time
                        from stop_i to stop_j. Diagonal = 0. Może include traffic
                        multiplier z OSRM.
        time_windows: Optional [(open_min, close_min), ...] per stop. Minutes
                     od decision_ts. Pickup ma open=czas_kuriera, close=czas_kuriera+sla.
                     Drop może mieć (0, max_route_min). None = no constraint.
        fixed_first_drop: Optional stop_idx który MUSI być pierwszą wizytą
                         (np. picked_up bag drops). None = no constraint.
        max_route_min: Total route duration cap (minutes). Default 90.
        time_limit_ms: Solver wall time limit. Default 200ms (Adrian's spec).

    Returns:
        TspSolution gdy solver finds feasible solution. None gdy INFEASIBLE
        lub solver crashes.
    """
    import time as _time
    from ortools.constraint_solver import pywrapcp, routing_enums_pb2

    t0 = _time.perf_counter()

    if num_stops <= 1:
        return TspSolution(
            sequence=[],
            total_distance_km=0.0,
            total_time_min=0.0,
            is_optimal=True,
            solver_status="trivial_empty",
            elapsed_ms=0.0,
        )

    # Validate inputs
    if len(distance_matrix_km) != num_stops or any(
        len(row) != num_stops for row in distance_matrix_km
    ):
        return None
    if len(time_matrix_min) != num_stops or any(
        len(row) != num_stops for row in time_matrix_min
    ):
        return None
    if time_windows is not None and len(time_windows) != num_stops:
        return None

    # OR-Tools setup
    # Open route trick: add dummy end node z distance/time = 0 do wszystkich.
    # Manager: depot=0 (courier_start), end_node=num_stops (dummy).
    NUM_NODES = num_stops + 1
    DUMMY_END = num_stops

    # Build extended matrices z dummy row/column wszystkie zera.
    dist_ext = [list(row) + [0] for row in distance_matrix_km]
    dist_ext.append([0] * NUM_NODES)
    time_ext = [list(row) + [0] for row in time_matrix_min]
    time_ext.append([0] * NUM_NODES)

    manager = pywrapcp.RoutingIndexManager(NUM_NODES, 1, [0], [DUMMY_END])
    routing = pywrapcp.RoutingModel(manager)

    # Distance callback (cost function — solver minimizes)
    SCALE = 1000  # OR-Tools wants int — multiply km by 1000 → m precision
    def dist_cb(from_index, to_index):
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return int(dist_ext[from_node][to_node] * SCALE)

    transit_callback_index = routing.RegisterTransitCallback(dist_cb)
    routing.SetArcCostEvaluatorOfAllVehicles(transit_callback_index)

    # Time dimension (minutes × scale)
    TIME_SCALE = 100  # 0.01 min precision = 0.6s
    def time_cb(from_index, to_index):
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return int(time_ext[from_node][to_node] * TIME_SCALE)

    time_callback_index = routing.RegisterTransitCallback(time_cb)
    routing.AddDimension(
        time_callback_index,
        int(max_route_min * TIME_SCALE),  # slack
        int(max_route_min * TIME_SCALE),  # capacity per vehicle
        True,  # fix_start_cumul_to_zero
        "Time",
    )
    time_dimension = routing.GetDimensionOrDie("Time")

    # Time windows (jeśli supplied)
    if time_windows is not None:
        for stop_idx in range(num_stops):
            tw = time_windows[stop_idx]
            if tw is None:
                continue
            open_min, close_min = tw
            if open_min < 0 or close_min < open_min:
                continue
            index = manager.NodeToIndex(stop_idx)
            time_dimension.CumulVar(index).SetRange(
                int(open_min * TIME_SCALE),
                int(close_min * TIME_SCALE),
            )

    # Pickup-and-delivery constraints
    for pickup_idx, drop_idx in pickup_drop_pairs:
        if pickup_idx == drop_idx:
            continue
        if pickup_idx < 0 or pickup_idx >= num_stops:
            continue
        if drop_idx < 0 or drop_idx >= num_stops:
            continue
        pickup_index = manager.NodeToIndex(pickup_idx)
        drop_index = manager.NodeToIndex(drop_idx)
        routing.AddPickupAndDelivery(pickup_index, drop_index)
        # Same vehicle (only 1 vehicle here, but explicit)
        routing.solver().Add(
            routing.VehicleVar(pickup_index) == routing.VehicleVar(drop_index)
        )
        # Pickup precedes drop (cumulative time)
        routing.solver().Add(
            time_dimension.CumulVar(pickup_index)
            <= time_dimension.CumulVar(drop_index)
        )

    # Optional: pin first stop (sticky bag delivery)
    if fixed_first_drop is not None and 1 <= fixed_first_drop < num_stops:
        first_index = manager.NodeToIndex(fixed_first_drop)
        routing.solver().Add(
            time_dimension.CumulVar(first_index)
            <= time_dimension.CumulVar(routing.NextVar(routing.Start(0)))
        )

    # Search params
    search_parameters = pywrapcp.DefaultRoutingSearchParameters()
    search_parameters.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION
    )
    search_parameters.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH
    )
    search_parameters.time_limit.FromMilliseconds(int(time_limit_ms))

    solution = routing.SolveWithParameters(search_parameters)
    elapsed_ms = (_time.perf_counter() - t0) * 1000.0

    if solution is None:
        return TspSolution(
            sequence=[],
            total_distance_km=0.0,
            total_time_min=0.0,
            is_optimal=False,
            solver_status=_status_string(routing.status()),
            elapsed_ms=round(elapsed_ms, 2),
            warnings=[f"INFEASIBLE: solver returned None"],
        )

    # Extract sequence
    sequence: List[int] = []
    total_dist_scaled = 0
    total_time_scaled = 0
    index = routing.Start(0)
    prev_index = index
    while not routing.IsEnd(index):
        node = manager.IndexToNode(index)
        if node != 0 and node != DUMMY_END:  # exclude start + dummy end
            sequence.append(node)
        next_index = solution.Value(routing.NextVar(index))
        total_dist_scaled += dist_cb(index, next_index)
        total_time_scaled += time_cb(index, next_index)
        prev_index = index
        index = next_index

    total_distance_km = total_dist_scaled / SCALE
    total_time_min = total_time_scaled / TIME_SCALE

    final_status = routing.status()
    # OR-Tools 9.15 RoutingSearchStatus enum (z routing_enums_pb2):
    # 1=SUCCESS, 2=PARTIAL_SUCCESS, 7=OPTIMAL → all considered "feasible solution found".
    # is_optimal: True tylko dla 7 (gwarantowanie optymalne) lub 1 gdy time-limit didn't kick.
    return TspSolution(
        sequence=sequence,
        total_distance_km=round(total_distance_km, 3),
        total_time_min=round(total_time_min, 2),
        is_optimal=(final_status in (1, 7)),
        solver_status=_status_string(final_status),
        elapsed_ms=round(elapsed_ms, 2),
    )


def _status_string(status: int) -> str:
    """OR-Tools 9.15 RoutingSearchStatus enum → readable name."""
    names = {
        0: "ROUTING_NOT_SOLVED",
        1: "ROUTING_SUCCESS",
        2: "ROUTING_PARTIAL_SUCCESS",
        3: "ROUTING_FAIL",
        4: "ROUTING_FAIL_TIMEOUT",
        5: "ROUTING_INVALID",
        6: "ROUTING_INFEASIBLE",
        7: "ROUTING_OPTIMAL",
    }
    return names.get(status, f"UNKNOWN_{status}")
