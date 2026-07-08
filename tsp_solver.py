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


def _ortools_det_budget() -> Optional[Tuple[int, int]]:
    """A2 PERF (2026-07-08): budżet solvera DETERMINISTYCZNY zamiast wall-clock.

    Zwraca (solution_limit, wall_ceiling_ms) gdy flaga
    `ENABLE_ORTOOLS_DET_TIME_LIMIT` ON — wtedy solver zatrzymuje się po stałej
    liczbie rozwiązań GLS (powtarzalna trasa). wall_ceiling_ms>0 = jawny OVERRIDE
    budżetu (tryb offline-replay determinism-first); wall_ceiling_ms==0 (default) =
    ZOSTAW budżet callera time_limit_ms jako sufit → produkcyjnie ON ≤ OFF latencja
    (wall-clock tnie identycznie jak OFF gdy solution_limit nie zdąży = bajt-w-bajt).
    None = wall-clock jak dziś (OFF, default flagi).

    Motyw tmux 31: `time_limit` (zegarek) → liczba iteracji GLS zależy od
    obciążenia CPU → ta sama sytuacja daje inną trasę (~1,7% niedeterminizmu
    replayu). Wzór zwalidowany w tools/sequential_replay.

    Import common LAZY (tsp_solver jest czystą f-cją bez zależności od common
    przy imporcie) + fail-soft None — brak common / błąd = zachowanie dzisiejsze.
    Czytane decision_flag (flags.json → stała common OFF), świeżo per solve
    (hot-reload; cross-proces shadow/plan-recheck/czasowka spójne).
    """
    try:
        import dispatch_v2.common as _C
    except Exception:
        try:
            import common as _C  # bieg spoza pakietu (np. harness z cwd=dispatch_v2)
        except Exception:
            return None
    try:
        if not _C.decision_flag("ENABLE_ORTOOLS_DET_TIME_LIMIT"):
            return None
        return (int(getattr(_C, "ORTOOLS_DET_SOLUTION_LIMIT", 120)),
                int(getattr(_C, "ORTOOLS_DET_WALL_CEILING_MS", 30000)))
    except Exception:
        return None


def solve_tsp_with_constraints(
    num_stops: int,
    pickup_drop_pairs: Sequence[Tuple[int, int]],
    distance_matrix_km: List[List[float]],
    time_matrix_min: List[List[float]],
    time_windows: Optional[List[Tuple[float, float]]] = None,
    fixed_first_drop: Optional[int] = None,
    max_route_min: float = 90.0,
    time_limit_ms: int = 200,
    cost_matrix_min: Optional[List[List[float]]] = None,
    delivery_soft_deadlines: Optional[List[Optional[Tuple[float, float]]]] = None,
    pickup_freshness_penalties: Optional[List[Optional[Tuple[float, float]]]] = None,
    pickup_committed_penalties: Optional[List[Optional[Tuple[float, float]]]] = None,
    pickup_committed_penalties_t2: Optional[List[Optional[Tuple[float, float]]]] = None,
    delivery_food_age_penalties: Optional[List[Optional[Tuple[float, float]]]] = None,
    span_cost_coeff: float = 0.0,
    delivery_sla_hard_span: bool = False,
    delivery_sla_hard_bounds: Optional[List[Optional[float]]] = None,
    sla_minutes_hard: float = 35.0,
    warm_start_routes: Optional[List[List[int]]] = None,
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
    # V3.28-P3-D1: cost_matrix_min validation (jeśli podany)
    if cost_matrix_min is not None and (
        len(cost_matrix_min) != num_stops
        or any(len(row) != num_stops for row in cost_matrix_min)
    ):
        return None
    # Sprint OBJ F1: delivery_soft_deadlines validation
    if delivery_soft_deadlines is not None and len(delivery_soft_deadlines) != num_stops:
        return None
    # Sprint OBJ FRESH (2026-05-30): pickup_freshness_penalties validation
    if pickup_freshness_penalties is not None and len(pickup_freshness_penalties) != num_stops:
        return None
    # N5 krok 2 (2026-06-17): pickup_committed_penalties validation
    if pickup_committed_penalties is not None and len(pickup_committed_penalties) != num_stops:
        return None
    # Eskalacja (2026-06-22): pickup_committed_penalties_t2 validation
    if pickup_committed_penalties_t2 is not None and len(pickup_committed_penalties_t2) != num_stops:
        return None
    # Sprint OBJ FOOD-AGE ADDITIVE (2026-06-14): delivery_food_age_penalties validation
    if delivery_food_age_penalties is not None and len(delivery_food_age_penalties) != num_stops:
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
    # V3.28-P3-D1: cost_matrix override gdy podany (idle-aware cost). Fallback
    # na distance_matrix_km (legacy). cost_ext jest osobny od time_ext żeby
    # constraint dimension (cumul) zostało clean drive-only — augment tylko
    # objective. Bez tego cumul bywa double-counted (matrix ma wait_estimate +
    # SetRange podbija do ready_at).
    if cost_matrix_min is not None:
        cost_ext = [list(row) + [0] for row in cost_matrix_min]
        cost_ext.append([0] * NUM_NODES)
    else:
        cost_ext = dist_ext

    manager = pywrapcp.RoutingIndexManager(NUM_NODES, 1, [0], [DUMMY_END])
    routing = pywrapcp.RoutingModel(manager)

    # Cost callback (objective — solver minimizes). V3.28-P3-D1: cost_ext
    # może zawierać wait_at_pickup penalty per Adrian's "kurierzy wolą jeździć".
    SCALE = 1000  # OR-Tools wants int — multiply km by 1000 → m precision
    def dist_cb(from_index, to_index):
        from_node = manager.IndexToNode(from_index)
        to_node = manager.IndexToNode(to_index)
        return int(cost_ext[from_node][to_node] * SCALE)

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

    # Sprint OBJ F2 (2026-05-18): koszt SPAN trasy. SetSpanCostCoefficient
    # dolicza coeff×span do objective; span = makespan (cumul end), zawiera
    # slack (idle — czekanie kuriera pod restauracją na gotowość pickupu).
    # Bez tego idle = darmowy slack w Time dim (diagnoza 474253). Konwersja
    # jednostek: arc cost = min × SCALE, span (Time dim cumul) = min × TIME_SCALE.
    # By span_cost_coeff=1.0 znaczyło "1 min span = 1 min jazdy w arc-cost":
    #   internal = coeff × SCALE / TIME_SCALE.
    if span_cost_coeff and span_cost_coeff > 0:
        _span_int = int(round(span_cost_coeff * SCALE / TIME_SCALE))
        if _span_int > 0:
            time_dimension.SetSpanCostCoefficientForAllVehicles(_span_int)

    # Time windows (jeśli supplied)
    # V3.28 Fix 2 (incident 03.05.2026): SetRange domain validation.
    # CumulVar domain = AddDimension capacity = int(max_route_min * TIME_SCALE).
    # SetRange poza domain → OR-Tools raise "Exception: CP Solver fail"
    # (incident 470208/209/210 — synthetic Test 2 reproducer 100% match).
    # Walidacja extension:
    #   2.A: NaN/Inf → skip stop's window
    #   2.B: scaled_open > capacity_max → skip (empty intersection w domain)
    #   2.C: scaled_close > capacity_max → clamp do capacity_max (zachowaj feasible upper bound)
    if time_windows is not None:
        import math as _math
        capacity_max = int(max_route_min * TIME_SCALE)
        for stop_idx in range(num_stops):
            tw = time_windows[stop_idx]
            if tw is None:
                continue
            open_min, close_min = tw
            if open_min < 0 or close_min < open_min:
                continue
            # Fix 2.A: NaN/Inf guard (np. ready - now z TZ mismatch może produkować Inf)
            if (
                _math.isnan(open_min) or _math.isnan(close_min)
                or _math.isinf(open_min) or _math.isinf(close_min)
            ):
                log.warning(
                    f"V328_TSP_SETRANGE_NAN_INF stop={stop_idx} "
                    f"tw=({open_min}, {close_min}) — skip window"
                )
                continue
            scaled_open = int(open_min * TIME_SCALE)
            scaled_close = int(close_min * TIME_SCALE)
            # Fix 2.B: open poza domain capacity → empty intersection → skip
            if scaled_open > capacity_max:
                log.warning(
                    f"V328_TSP_SETRANGE_OPEN_OOD stop={stop_idx} "
                    f"open={open_min:.1f}min > max_route_min={max_route_min}min — skip window"
                )
                continue
            # Fix 2.C: close clamped do capacity_max (zachowaj feasible upper bound)
            scaled_close = min(scaled_close, capacity_max)
            index = manager.NodeToIndex(stop_idx)
            time_dimension.CumulVar(index).SetRange(scaled_open, scaled_close)

    # Sprint OBJ F1 (2026-05-17): R6 soft upper bound na węzłach delivery.
    # CumulVar(delivery) > deadline → kara coeff×overshoot w objective. Solver
    # respektuje R6 (35 min) gdy wykonalne; gdy R6-doomed MINIMALIZUJE
    # przekroczenie (dostarcza ASAP) zamiast parkować doomed dostawy na końcu.
    # Picked-up jedzenie: deadline blisko/0 → front-load. Soft — feasibility R6
    # hard-gate zostaje post-hoc; tu tylko prowadzenie sekwencji solvera.
    if delivery_soft_deadlines is not None:
        import math as _m_dl
        capacity_max = int(max_route_min * TIME_SCALE)
        for stop_idx in range(num_stops):
            spec = delivery_soft_deadlines[stop_idx]
            if spec is None:
                continue
            deadline_min, coeff = spec
            if deadline_min is None or coeff is None or coeff <= 0:
                continue
            if _m_dl.isnan(deadline_min) or _m_dl.isinf(deadline_min):
                continue
            # bound w domenie [0, capacity]; deadline ujemny (jedzenie już
            # przeterminowane) → 0 → każda minuta zwłoki karana → ASAP.
            scaled_bound = max(0, min(int(deadline_min * TIME_SCALE), capacity_max))
            idx = manager.NodeToIndex(stop_idx)
            time_dimension.SetCumulVarSoftUpperBound(
                idx, scaled_bound, int(round(coeff)))

    # Sprint OBJ FRESH (2026-05-30): świeżość odbioru — soft upper bound na
    # węzłach pickup. CumulVar(pickup) > (ready_at + threshold) → kara
    # coeff×overshoot w objective. Symetryczny do delivery_soft_deadlines:
    # ten sam prymityw OR-Tools (SetCumulVarSoftUpperBound), tylko anchor =
    # ready_at (gotowość jedzenia) zamiast deadline dostawy. Soft — NIGDY nie
    # powoduje INFEASIBLE (czysto objective); celowany w ogon (~18% odbiorów
    # projektowanych ≥10 min po gotowości — replay 2026-05-30). Próg odejmuje
    # medianę (clamp +1 min do ready), karze dopiero gratuitous staleness.
    if pickup_freshness_penalties is not None:
        import math as _m_pf
        capacity_max = int(max_route_min * TIME_SCALE)
        for stop_idx in range(num_stops):
            spec = pickup_freshness_penalties[stop_idx]
            if spec is None:
                continue
            bound_min, coeff = spec
            if bound_min is None or coeff is None or coeff <= 0:
                continue
            if _m_pf.isnan(bound_min) or _m_pf.isinf(bound_min):
                continue
            scaled_bound = max(0, min(int(bound_min * TIME_SCALE), capacity_max))
            idx = manager.NodeToIndex(stop_idx)
            time_dimension.SetCumulVarSoftUpperBound(
                idx, scaled_bound, int(round(coeff)))

    # N5 krok 2 (2026-06-17): KARA PUNKTUALNOŚCI COMMITTED — soft upper bound na
    # węzłach pickup z committed czas_kuriera. CumulVar(pickup) > (czas_kuriera +
    # tolerancja) → kara coeff×overshoot. TEN SAM prymityw co FRESH/R6 (Soft —
    # NIGDY INFEASIBLE). Chroni OBIETNICĘ dla restauracji: solver przestaje ślizgać
    # committed odbiór dla skrótu jazdy (tier2 breach z #1/#3). Tolerancja load-aware
    # (5 strict / 10 przy niedoborze) wstrzyknięta w bound_min przez route_simulator.
    if pickup_committed_penalties is not None:
        import math as _m_pc
        capacity_max = int(max_route_min * TIME_SCALE)
        for stop_idx in range(num_stops):
            spec = pickup_committed_penalties[stop_idx]
            if spec is None:
                continue
            bound_min, coeff = spec
            if bound_min is None or coeff is None or coeff <= 0:
                continue
            if _m_pc.isnan(bound_min) or _m_pc.isinf(bound_min):
                continue
            scaled_bound = max(0, min(int(bound_min * TIME_SCALE), capacity_max))
            idx = manager.NodeToIndex(stop_idx)
            time_dimension.SetCumulVarSoftUpperBound(
                idx, scaled_bound, int(round(coeff)))

    # ESKALACJA committed (2026-06-22 D1, Adrian): tier-2 soft upper bound na CUMUL
    # PICKUPÓW committed przez OSOBNY wymiar (OR-Tools nie stackuje 2 soft-boundów na
    # 1 węźle Time) + TWARDA równość CumulVar(CommittedLate)==CumulVar(Time) → mirror
    # realnego (wait-inclusive) harmonogramu. Łącznie z tier-1 (Time, próg ck+tol) =
    # kara WYPUKŁA: 0 do ck+tol, slope c1 do ck+T2, slope c1+c2 powyżej ("mocno rosnąca").
    if pickup_committed_penalties_t2 is not None and any(
            p is not None for p in pickup_committed_penalties_t2):
        import math as _m_pc2
        routing.AddDimension(
            time_callback_index,
            int(max_route_min * TIME_SCALE),   # slack
            int(max_route_min * TIME_SCALE),   # capacity
            True,                              # fix_start_cumul_to_zero
            "CommittedLate",
        )
        _cl_dim = routing.GetDimensionOrDie("CommittedLate")
        capacity_max = int(max_route_min * TIME_SCALE)
        for stop_idx in range(num_stops):
            idx = manager.NodeToIndex(stop_idx)
            # mirror realnego (wait-inclusive) czasu z wymiaru Time
            routing.solver().Add(
                _cl_dim.CumulVar(idx) == time_dimension.CumulVar(idx))
            spec = pickup_committed_penalties_t2[stop_idx]
            if spec is None:
                continue
            bound_min, coeff = spec
            if bound_min is None or coeff is None or coeff <= 0:
                continue
            if _m_pc2.isnan(bound_min) or _m_pc2.isinf(bound_min):
                continue
            scaled_bound = max(0, min(int(bound_min * TIME_SCALE), capacity_max))
            _cl_dim.SetCumulVarSoftUpperBound(idx, scaled_bound, int(round(coeff)))

    # Sprint OBJ FOOD-AGE ADDITIVE (2026-06-14 redesign): drugi soft upper bound na
    # CUMUL DOSTAWY, ADDYTYWNY do R6 (delivery_soft_deadlines). OR-Tools nie stackuje
    # dwóch soft-boundów na jednym węźle Time → osobny wymiar "FoodAge" (ten sam
    # transit) + TWARDA równość CumulVar(FoodAge)==CumulVar(Time) na każdym węźle →
    # FoodAge mirroruje REALNY harmonogram (z czekaniem), więc kara liczy się od
    # rzeczywistego czasu dostawy. Soft bound food-age: kotwica=gotowość (sla=0),
    # coeff gentle. Łączny koszt dostawy = R6(ready+sla, coeff~100) + food-age
    # (ready, coeff~6) = dwukawałkowa wypukła kara: stroma chroni SLA, łagodna
    # nudguje świeżość TYLKO gdzie R6 obojętne (obie dostawy < deadline = case Jakuba).
    # Poprzednia wersja (food-age ZASTĘPUJĄCA R6) regresowała SLA 9.4% na replay n=891.
    if delivery_food_age_penalties is not None and any(
            p is not None for p in delivery_food_age_penalties):
        import math as _m_fa
        routing.AddDimension(
            time_callback_index,
            int(max_route_min * TIME_SCALE),   # slack
            int(max_route_min * TIME_SCALE),   # capacity
            True,                              # fix_start_cumul_to_zero
            "FoodAge",
        )
        _food_dim = routing.GetDimensionOrDie("FoodAge")
        capacity_max = int(max_route_min * TIME_SCALE)
        for stop_idx in range(num_stops):
            idx = manager.NodeToIndex(stop_idx)
            # mirror realnego (wait-inclusive) czasu z wymiaru Time
            routing.solver().Add(
                _food_dim.CumulVar(idx) == time_dimension.CumulVar(idx))
            spec = delivery_food_age_penalties[stop_idx]
            if spec is None:
                continue
            bound_min, coeff = spec
            if bound_min is None or coeff is None or coeff <= 0:
                continue
            if _m_fa.isnan(bound_min) or _m_fa.isinf(bound_min):
                continue
            scaled_bound = max(0, min(int(bound_min * TIME_SCALE), capacity_max))
            _food_dim.SetCumulVarSoftUpperBound(idx, scaled_bound, int(round(coeff)))

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
        # FOOD-AGE HARD-SLA (2026-06-17): TWARDY span pickup→delivery ≤ sla dla
        # zleceń odbieranych w tym planie. Kotwica = węzeł pickup TSP = DOKŁADNIE
        # kotwica metryki _count_sla_violations (NIE ready/R6-soft). Gwarantuje
        # że solver nie wybierze sekwencji łamiącej SLA (strukturalne 38%) ani jej
        # nie zwróci jako sub-optimum 200ms (budżet 62%). Infeasible → fallback OFF.
        if delivery_sla_hard_span:
            routing.solver().Add(
                time_dimension.CumulVar(drop_index)
                - time_dimension.CumulVar(pickup_index)
                <= int(round(sla_minutes_hard * TIME_SCALE))
            )

    # FOOD-AGE HARD-SLA (2026-06-17): TWARDY SetMax dla zleceń JUŻ-ODEBRANYCH
    # (delivery node spoza par — bez węzła pickup). Bound = (picked_up_at−now)+sla
    # [min od startu trasy] = kotwica picked_up_at metryki. Ujemny (odebrane dawno)
    # → clamp do 0 → dowieź ASAP. None → pomiń (pending/new chronione spanem wyżej).
    if delivery_sla_hard_bounds is not None:
        _cap = int(max_route_min * TIME_SCALE)
        for _stop in range(num_stops):
            _b = delivery_sla_hard_bounds[_stop]
            if _b is None:
                continue
            _scaled = max(0, min(int(_b * TIME_SCALE), _cap))
            time_dimension.CumulVar(manager.NodeToIndex(_stop)).SetMax(_scaled)

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

    # A2 PERF (2026-07-08): deterministyczny budżet solvera zamiast wall-clock.
    # ON → `solution_limit` (stała liczba rozwiązań GLS) wiąże budżet = ta sama
    # sytuacja daje tę samą trasę (usuwa ~1,7% niedeterminizmu replayu z cutoffu
    # „na zegarek", tmux 31); `time_limit` podniesiony do luźnego sufitu = tylko
    # bezpiecznik anty-zawis. OFF (default) → ten blok nie rusza params = goły
    # time_limit powyżej, BAJT-W-BAJT z dziś. MUSI być PRZED warm-start
    # CloseModelWithParameters/SolveWithParameters (params czytane przy zamknięciu
    # modelu). decision_flag → flags.json→stała OFF (cross-proces).
    _det_budget = _ortools_det_budget()
    if _det_budget is not None:
        search_parameters.solution_limit = _det_budget[0]
        # sufit >0 = jawny OVERRIDE (tryb offline-replay determinism-first); 0 =
        # ZOSTAW budżet callera z linii wyżej (produkcja: ON ≤ OFF latencja, bo
        # wall-clock tnie identycznie jak OFF gdy solution_limit nie zdąży).
        if _det_budget[1] > 0:
            search_parameters.time_limit.FromMilliseconds(int(_det_budget[1]))

    # FOOD-AGE HARD-SLA (2026-06-17): warm-start hintem (sekwencja bazowa node-idx,
    # bez węzła startu/dummy — format jak self.sequence). Przyspiesza zbieżność pod
    # twardymi ograniczeniami → kasuje artefakt budżetu 200ms (62% regresji). Hint
    # niespójny z ograniczeniami (np. base łamie span) → initial=None → graceful
    # fallback do zwykłego solve (nie crash). ReadAssignmentFromRoutes wymaga
    # zamkniętego modelu → jawny CloseModelWithParameters.
    if warm_start_routes:
        try:
            routing.CloseModelWithParameters(search_parameters)
            _initial = routing.ReadAssignmentFromRoutes(warm_start_routes, True)
            if _initial is not None:
                solution = routing.SolveFromAssignmentWithParameters(
                    _initial, search_parameters)
            else:
                solution = routing.SolveWithParameters(search_parameters)
        except Exception:
            solution = routing.SolveWithParameters(search_parameters)
    else:
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
