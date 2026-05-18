"""Sprint OBJ F2 (2026-05-18) — koszt SPAN trasy w objective solvera TSP.

F2 wycenia idle (czekanie kuriera pod restauracją na gotowość pickupu) przez
SetSpanCostCoefficient na Time dimension. Span = makespan trasy (cumul end),
zawiera slack czekania. Bez tego idle = darmowy slack (diagnoza 474253).
Zastępuje strukturalnie zepsute P3-D1 (retired).

Tests:
- F2-T1: span_cost_coeff=0 → zachowanie legacy (objective = sama jazda).
- F2-T2: span_cost_coeff>0 → solver wybiera trasę o mniejszym makespanie,
         nawet kosztem nieco dłuższej jazdy (konwersja idle → produktywny ruch).
- F2-T3: edge — num_stops trywialny / wysoki coeff → brak crasha.
- F2-T4: _ortools_plan z flag ON → span_cost_coeff>0 dolatuje do solvera.
- F2-T5: _ortools_plan z flag OFF → span_cost_coeff=0.0.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from dispatch_v2 import common, tsp_solver
from dispatch_v2 import route_simulator_v2 as rs


# ─── F2-T1: coeff=0 → legacy ─────────────────────────────────────────

def test_f2_t1_span_coeff_zero_legacy_behavior():
    """span_cost_coeff=0.0 (default) → objective = sama jazda, jak przed F2."""
    distance = [[0, 5, 10], [5, 0, 5], [10, 5, 0]]
    time = [[0, 5, 10], [5, 0, 5], [10, 5, 0]]
    sol = tsp_solver.solve_tsp_with_constraints(
        num_stops=3,
        pickup_drop_pairs=[(1, 2)],
        distance_matrix_km=distance,
        time_matrix_min=time,
        span_cost_coeff=0.0,
    )
    assert sol is not None
    assert sol.sequence == [1, 2]


# ─── F2-T2: coeff>0 → wybór trasy o mniejszym makespanie ─────────────

def _span_case():
    """5 węzłów: start(0), P1(1), D1(2), P2(3, gotowy dopiero w min 30), D2(4).
    Dwie trasy używające tylko tanich krawędzi:
      A=[1,2,3,4]: jazda 20 min, przyjazd P2 w min 15 → idle 15 → span 35.
      B=[3,1,2,4]: jazda 19 min, przyjazd P2 w min 4  → idle 26 → span 45.
    Bez kosztu span: solver minimalizuje jazdę → B (19 < 20).
    Z kosztem span (coeff>0.1): A wygrywa (mniejszy makespan mimo +1 min jazdy).
    """
    BIG = 100
    N = 5
    m = [[0 if i == j else BIG for j in range(N)] for i in range(N)]
    # trasa A
    m[0][1] = 5; m[1][2] = 5; m[2][3] = 5; m[3][4] = 5
    # trasa B (dzieli krawędź 1→2)
    m[0][3] = 4; m[3][1] = 5; m[2][4] = 5
    time_windows = [(0.0, 120.0), (0.0, 120.0), (0.0, 120.0),
                    (30.0, 120.0), (0.0, 120.0)]
    return m, time_windows


def test_f2_t2_span_coeff_zero_picks_min_drive():
    """Bez kosztu span solver minimalizuje jazdę → trasa B (krótsza o 1 min)."""
    m, tw = _span_case()
    sol = tsp_solver.solve_tsp_with_constraints(
        num_stops=5,
        pickup_drop_pairs=[(1, 2), (3, 4)],
        distance_matrix_km=m,
        time_matrix_min=m,
        time_windows=tw,
        max_route_min=120.0,
        time_limit_ms=2000,
        span_cost_coeff=0.0,
    )
    assert sol is not None
    assert sol.sequence == [3, 1, 2, 4], (
        f"coeff=0 → min-jazda B oczekiwane, got {sol.sequence}")


def test_f2_t2_span_coeff_positive_picks_min_span():
    """Z kosztem span solver wybiera trasę o mniejszym makespanie → trasa A."""
    m, tw = _span_case()
    sol = tsp_solver.solve_tsp_with_constraints(
        num_stops=5,
        pickup_drop_pairs=[(1, 2), (3, 4)],
        distance_matrix_km=m,
        time_matrix_min=m,
        time_windows=tw,
        max_route_min=120.0,
        time_limit_ms=2000,
        span_cost_coeff=0.5,
    )
    assert sol is not None
    assert sol.sequence == [1, 2, 3, 4], (
        f"coeff=0.5 → min-span A oczekiwane, got {sol.sequence}")


# ─── F2-T3: edge ─────────────────────────────────────────────────────

def test_f2_t3_trivial_problem_no_crash():
    """num_stops<=1 + coeff>0 → trivial_empty, brak crasha."""
    sol = tsp_solver.solve_tsp_with_constraints(
        num_stops=1,
        pickup_drop_pairs=[],
        distance_matrix_km=[[0]],
        time_matrix_min=[[0]],
        span_cost_coeff=0.5,
    )
    assert sol is not None
    assert sol.sequence == []


def test_f2_t3_high_coeff_still_feasible():
    """Bardzo wysoki coeff → solver nadal zwraca wykonalne rozwiązanie."""
    distance = [[0, 5, 10], [5, 0, 5], [10, 5, 0]]
    sol = tsp_solver.solve_tsp_with_constraints(
        num_stops=3,
        pickup_drop_pairs=[(1, 2)],
        distance_matrix_km=distance,
        time_matrix_min=distance,
        span_cost_coeff=1000.0,
    )
    assert sol is not None
    assert sol.sequence == [1, 2]


# ─── F2-T4/T5: integracja _ortools_plan (flag → coeff) ───────────────

def _bialystok_orders():
    """Mały bag w Białymstoku — wymusza ścieżkę OR-Tools (bag_after>=2)."""
    now = datetime(2026, 5, 18, 12, 0, tzinfo=timezone.utc)
    bag = [rs.OrderSim(
        order_id="BAG1",
        pickup_coords=(53.130, 23.150),
        delivery_coords=(53.140, 23.170),
        picked_up_at=now - timedelta(minutes=5),
        status="picked_up",
    )]
    new_order = rs.OrderSim(
        order_id="NEW1",
        pickup_coords=(53.125, 23.160),
        delivery_coords=(53.145, 23.140),
        status="assigned",
        pickup_ready_at=now + timedelta(minutes=8),
    )
    return (53.128, 23.155), bag, new_order, now


def _capture_solver(captured):
    """Zwraca stub solvera, który zapisuje kwargs i sygnalizuje INFEASIBLE
    (→ _ortools_plan fallback do greedy; capture i tak się wykonuje)."""
    def _stub(**kwargs):
        captured.append(kwargs)
        return None
    return _stub


def test_f2_t4_ortools_plan_flag_on_passes_coeff():
    """Flag ON → _ortools_plan przekazuje span_cost_coeff>0 do solvera."""
    courier_pos, bag, new_order, now = _bialystok_orders()
    captured: list = []
    with patch.object(common, "ENABLE_OBJ_SPAN_COST", True), \
         patch.object(common, "OBJ_SPAN_COST_COEFF", 0.5), \
         patch.object(tsp_solver, "solve_tsp_with_constraints",
                      _capture_solver(captured)):
        rs.simulate_bag_route_v2(courier_pos, bag, new_order, now=now)
    assert captured, "solver nie został wywołany — ścieżka OR-Tools nieosiągnięta"
    assert all(c.get("span_cost_coeff") == 0.5 for c in captured), (
        f"oczekiwano span_cost_coeff=0.5 w każdym wywołaniu, got "
        f"{[c.get('span_cost_coeff') for c in captured]}")


def test_f2_t5_ortools_plan_flag_off_coeff_zero():
    """Flag OFF → _ortools_plan przekazuje span_cost_coeff=0.0 (no-op)."""
    courier_pos, bag, new_order, now = _bialystok_orders()
    captured: list = []
    with patch.object(common, "ENABLE_OBJ_SPAN_COST", False), \
         patch.object(tsp_solver, "solve_tsp_with_constraints",
                      _capture_solver(captured)):
        rs.simulate_bag_route_v2(courier_pos, bag, new_order, now=now)
    assert captured, "solver nie został wywołany — ścieżka OR-Tools nieosiągnięta"
    assert all(c.get("span_cost_coeff") == 0.0 for c in captured), (
        f"oczekiwano span_cost_coeff=0.0, got "
        f"{[c.get('span_cost_coeff') for c in captured]}")
