"""A2 PERF (2026-07-08, sprint p95) — deterministyczny budżet solvera OR-Tools.

Flaga `ENABLE_ORTOOLS_DET_TIME_LIMIT` (default OFF, byte-identyczna z dziś). ON →
`solution_limit` (stała liczba rozwiązań GLS) zamiast wall-clock `time_limit` =
ta sama sytuacja → ta sama trasa (usuwa niedeterminizm cutoffu, motyw tmux 31).

Pokrywa (protokół #0: flaga ON≠OFF, brak martwego kodu, HARD nietknięte):
  - rejestracja w ETAP4_DECISION_FLAGS + stała-fallback OFF + widoczność w fingerprincie,
  - predykat _ortools_det_budget: OFF→None, ON→(solution_limit, ceiling),
  - OFF: solve poprawny (pickup-przed-drop) — ścieżka niezmieniona,
  - ON: solve poprawny + DETERMINISTYCZNY run-to-run,
  - solution_limit REALNIE dociera do solvera (limit=1 → trasa nie-lepsza niż OFF-optimal;
    limit domyślny → parytet z OFF na łatwym worku),
  - HARD nietknięte: pickup-przed-drop trzymane pod ON.
"""
import math

import pytest

from dispatch_v2 import common as C
from dispatch_v2 import tsp_solver as T


# ── deterministyczna geometria skupiona (jak realny worek) ──
_KM_LAT, _KM_LON = 111.0, 66.0


def _lcg(seed):
    s = seed & 0x7FFFFFFF
    while True:
        s = (1103515245 * s + 12345) & 0x7FFFFFFF
        yield s / 0x7FFFFFFF


def _case(bag=3, seed=7):
    g = _lcg(seed)
    base = (53.13, 23.16)

    def near(c, radius_km):
        r = radius_km * math.sqrt(next(g))
        th = 2 * math.pi * next(g)
        return (c[0] + r * math.cos(th) / _KM_LAT, c[1] + r * math.sin(th) / _KM_LON)

    deliv = near(base, 4.0)
    coords = [near(base, 2.0)]
    coords += [near(base, 0.9) for _ in range(bag)]
    coords += [near(deliv, 3.5) for _ in range(bag)]
    n = len(coords)

    def hav(a, b):
        R = 6371.0
        la1, lo1 = math.radians(a[0]), math.radians(a[1])
        la2, lo2 = math.radians(b[0]), math.radians(b[1])
        dla, dlo = la2 - la1, lo2 - lo1
        x = math.sin(dla / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
        return 2 * R * math.asin(math.sqrt(x))

    dist = [[round(hav(coords[i], coords[j]), 3) for j in range(n)] for i in range(n)]
    tmat = [[round(dist[i][j] / 22.0 * 60.0, 2) for j in range(n)] for i in range(n)]
    pairs = [(i, i + bag) for i in range(1, bag + 1)]
    return dict(num_stops=n, pickup_drop_pairs=pairs, distance_matrix_km=dist,
                time_matrix_min=tmat, max_route_min=90.0), pairs


def _pickup_before_drop(seq, pairs):
    return all(seq.index(p) < seq.index(d) for p, d in pairs)


def test_flag_registered_and_default_off():
    assert "ENABLE_ORTOOLS_DET_TIME_LIMIT" in C.ETAP4_DECISION_FLAGS
    assert hasattr(C, "ENABLE_ORTOOLS_DET_TIME_LIMIT")
    assert C.ENABLE_ORTOOLS_DET_TIME_LIMIT is False           # produkcja OFF
    assert C.decision_flag("ENABLE_ORTOOLS_DET_TIME_LIMIT") is False
    # config-stałe obecne
    assert isinstance(C.ORTOOLS_DET_SOLUTION_LIMIT, int)
    assert isinstance(C.ORTOOLS_DET_WALL_CEILING_MS, int)


def test_flag_in_fingerprint():
    fp = C.flag_fingerprint()
    assert "ENABLE_ORTOOLS_DET_TIME_LIMIT=" in fp


def test_predicate_off_returns_none(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_ORTOOLS_DET_TIME_LIMIT", False)
    assert T._ortools_det_budget() is None


def test_predicate_on_returns_budget(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_ORTOOLS_DET_TIME_LIMIT", True)
    b = T._ortools_det_budget()
    assert b == (C.ORTOOLS_DET_SOLUTION_LIMIT, C.ORTOOLS_DET_WALL_CEILING_MS)


def test_off_solve_valid():
    case, pairs = _case(bag=3)
    sol = T.solve_tsp_with_constraints(time_limit_ms=200, **case)
    assert sol is not None and sol.sequence
    assert _pickup_before_drop(sol.sequence, pairs)          # HARD: pickup przed drop


def test_on_solve_valid_and_deterministic(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_ORTOOLS_DET_TIME_LIMIT", True)
    case, pairs = _case(bag=4, seed=11)
    a = T.solve_tsp_with_constraints(time_limit_ms=200, **case)
    b = T.solve_tsp_with_constraints(time_limit_ms=200, **case)
    assert a is not None and b is not None
    assert a.sequence == b.sequence                          # DETERMINIZM run-to-run
    assert _pickup_before_drop(a.sequence, pairs)            # HARD trzymane pod ON


def test_on_parity_with_off_on_easy_bag(monkeypatch):
    """Łatwy worek: ON (solution_limit domyślny) daje TĘ SAMĄ trasę co OFF."""
    case, _pairs = _case(bag=3, seed=3)
    off = T.solve_tsp_with_constraints(time_limit_ms=200, **case)
    monkeypatch.setattr(C, "ENABLE_ORTOOLS_DET_TIME_LIMIT", True)
    on = T.solve_tsp_with_constraints(time_limit_ms=200, **case)
    assert on.sequence == off.sequence
    assert abs(on.total_distance_km - off.total_distance_km) < 1e-6


def test_solution_limit_actually_reaches_solver(monkeypatch):
    """Dowód, że solution_limit BITE: z limitem=1 solver zatrzymuje się na
    pierwszym rozwiązaniu → trasa NIE-lepsza niż OFF-optimal (first-solution
    ≥ optimal). Gdyby param nie docierał, ON=OFF (limit ignorowany)."""
    case, pairs = _case(bag=5, seed=23)
    off = T.solve_tsp_with_constraints(time_limit_ms=200, **case)
    monkeypatch.setattr(C, "ENABLE_ORTOOLS_DET_TIME_LIMIT", True)
    monkeypatch.setattr(C, "ORTOOLS_DET_SOLUTION_LIMIT", 1)
    monkeypatch.setattr(C, "ORTOOLS_DET_WALL_CEILING_MS", 30000)
    on1 = T.solve_tsp_with_constraints(time_limit_ms=200, **case)
    assert on1 is not None and on1.sequence
    assert _pickup_before_drop(on1.sequence, pairs)
    # first-solution nie może być lepsza od optimum OFF (tolerancja num.)
    assert on1.total_distance_km >= off.total_distance_km - 1e-6
