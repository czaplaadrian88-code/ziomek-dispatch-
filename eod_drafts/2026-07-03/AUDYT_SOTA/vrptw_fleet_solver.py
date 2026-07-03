#!/usr/bin/env python3
"""
DRAFT (audyt SOTA 2026-07-03) — flotowy VRPTW OR-Tools: 10 aut, time windows,
POJEMNOŚĆ (wymiar, którego produkcyjny tsp_solver.py NIE ma).

NIE JEST WPIĘTY. Kontekst i uczciwe zastrzeżenie ROI:
  * Produkcja (tsp_solver.py:86+) używa OR-Tools per-KURIER (1 pojazd, PDP,
    time windows, GLS 200 ms) — bardzo dobrze. Luka = (a) brak wymiaru
    Capacity (cap bagu egzekwuje osobno feasibility_v2.py, solver może ułożyć
    sekwencję ignorującą limit toreb), (b) selekcja przydziału jest greedy
    per-order (dispatch_pipeline.py:970-977 sort po score), nie flotowa.
  * Replay 02.07 zmierzył globalną selekcję (LAP) na ~0,12 min/zlecenie —
    MARGINALNE przy 10 autach. Dlatego ten solver ma sens WYŁĄCZNIE jako
    komparator SHADOW (drugi głos przy peak/scarcity), nie zamiennik pętli.
Użycie shadow: co N minut zbuduj snapshot z orders_state + courier_plans,
porównaj koszt planu VRPTW vs suma planów per-kurier, loguj deltę do JSONL.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from ortools.constraint_solver import pywrapcp, routing_enums_pb2

NUM_VEHICLES = 10
TIME_LIMIT_S = 2            # shadow, nie hot-path — stać nas na >200 ms
MAX_ROUTE_MIN = 90          # jak V326 cap trasy
DEFAULT_BAG_CAP = 4         # per-tier: gold 5 / std 4 / slow 3 (przykład)


@dataclass
class Stop:
    """Węzeł 0 = wirtualny depot; pozycje startowe aut = węzły 1..V."""
    name: str
    demand: int = 0                      # +1 pickup, -1 delivery, 0 start
    tw_open_min: int = 0                 # okno czasowe [min od "teraz"]
    tw_close_min: int = MAX_ROUTE_MIN


@dataclass
class FleetProblem:
    stops: list[Stop]
    travel_min: list[list[int]]          # macierz z osrm_client.table()
    pickup_delivery_pairs: list[tuple[int, int]]
    vehicle_start_nodes: list[int]
    vehicle_caps: list[int] = field(default_factory=list)


def solve_fleet_vrptw(p: FleetProblem) -> dict | None:
    n = len(p.stops)
    caps = p.vehicle_caps or [DEFAULT_BAG_CAP] * NUM_VEHICLES
    starts = p.vehicle_start_nodes
    ends = [0] * len(starts)             # otwarte trasy: powrót do dummy-depot

    manager = pywrapcp.RoutingIndexManager(n, len(starts), starts, ends)
    routing = pywrapcp.RoutingModel(manager)

    def transit(from_idx: int, to_idx: int) -> int:
        f, t = manager.IndexToNode(from_idx), manager.IndexToNode(to_idx)
        if t == 0:
            return 0                     # dojazd do dummy-depot darmowy
        return int(p.travel_min[f][t])

    cb = routing.RegisterTransitCallback(transit)
    routing.SetArcCostEvaluatorOfAllVehicles(cb)

    # --- wymiar CZAS + okna czasowe (jak w produkcyjnym tsp_solver.py) ------
    routing.AddDimension(cb, MAX_ROUTE_MIN, MAX_ROUTE_MIN, False, "Time")
    time_dim = routing.GetDimensionOrDie("Time")
    for node, stop in enumerate(p.stops):
        if node == 0:
            continue
        idx = manager.NodeToIndex(node)
        if idx >= 0:
            # clamp jak Fix 2.B/2.C po incydencie 470208
            lo = max(0, min(stop.tw_open_min, MAX_ROUTE_MIN))
            hi = max(lo, min(stop.tw_close_min, MAX_ROUTE_MIN))
            time_dim.CumulVar(idx).SetRange(lo, hi)
    # minimalizuj też makespan (przestoje) — odpowiednik span cost z produkcji
    time_dim.SetGlobalSpanCostCoefficient(3)

    # --- wymiar POJEMNOŚĆ (BRAKUJĄCY w tsp_solver.py) -----------------------
    def demand(from_idx: int) -> int:
        return p.stops[manager.IndexToNode(from_idx)].demand

    dcb = routing.RegisterUnaryTransitCallback(demand)
    routing.AddDimensionWithVehicleCapacity(dcb, 0, caps, True, "Bag")

    # --- pary pickup→delivery: ten sam pojazd, poprawna kolejność ----------
    solver = routing.solver()
    for pu, dr in p.pickup_delivery_pairs:
        pi, di = manager.NodeToIndex(pu), manager.NodeToIndex(dr)
        routing.AddPickupAndDelivery(pi, di)
        solver.Add(routing.VehicleVar(pi) == routing.VehicleVar(di))
        solver.Add(time_dim.CumulVar(pi) <= time_dim.CumulVar(di))
        # miękki deadline R6: dostawa ≤ 35 min od odbioru, kara wypukła
        solver.Add(time_dim.CumulVar(di) <= time_dim.CumulVar(pi) + 35 + 30)
        time_dim.SetCumulVarSoftUpperBound(di, 35, 100)

    # zlecenie może zostać nieobsłużone za wysoką karą (droppable) — bez tego
    # przeciążony snapshot = INFEASIBLE zamiast częściowego planu
    for node in range(1, n):
        if p.stops[node].demand != 0:
            routing.AddDisjunction([manager.NodeToIndex(node)], 10_000)

    params = pywrapcp.DefaultRoutingSearchParameters()
    params.first_solution_strategy = (
        routing_enums_pb2.FirstSolutionStrategy.PARALLEL_CHEAPEST_INSERTION)
    params.local_search_metaheuristic = (
        routing_enums_pb2.LocalSearchMetaheuristic.GUIDED_LOCAL_SEARCH)
    params.time_limit.FromSeconds(TIME_LIMIT_S)

    sol = routing.SolveWithParameters(params)
    if sol is None:
        return None

    routes, dropped = [], []
    for v in range(len(starts)):
        idx, route = routing.Start(v), []
        while not routing.IsEnd(idx):
            node = manager.IndexToNode(idx)
            route.append({"stop": p.stops[node].name,
                          "eta_min": sol.Value(time_dim.CumulVar(idx))})
            idx = sol.Value(routing.NextVar(idx))
        routes.append(route)
    for node in range(1, n):
        if sol.Value(routing.NextVar(manager.NodeToIndex(node))) == \
           manager.NodeToIndex(node):
            dropped.append(p.stops[node].name)
    return {"objective": sol.ObjectiveValue(), "routes": routes,
            "dropped": dropped}


if __name__ == "__main__":
    # mini-demo: 2 auta, 2 zlecenia (4 stopy) + dummy depot + 2 starty
    stops = [Stop("depot"),
             Stop("car1_start"), Stop("car2_start"),
             Stop("pickup_A", 1, 10, 25), Stop("drop_A", -1, 10, 60),
             Stop("pickup_B", 1, 0, 15), Stop("drop_B", -1, 0, 50)]
    m = [[0] * 7 for _ in range(7)]
    demo_times = {(1, 3): 8, (3, 4): 12, (2, 5): 5, (5, 6): 9, (4, 5): 15,
                  (6, 3): 15, (1, 5): 20, (2, 3): 20, (3, 5): 6, (5, 4): 14,
                  (4, 6): 7, (6, 4): 7}
    for (a, b), t in demo_times.items():
        m[a][b] = m[b][a] = t
    prob = FleetProblem(stops, m, [(3, 4), (5, 6)],
                        vehicle_start_nodes=[1, 2], vehicle_caps=[4, 4])
    import json
    print(json.dumps(solve_fleet_vrptw(prob), indent=2, ensure_ascii=False))
