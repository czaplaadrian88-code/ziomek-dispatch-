"""Testy sprint OBJ FRESH — świeżość odbioru w tsp_solver (2026-05-30).

pickup_freshness_penalties: per-węzeł (bound_min, penalty_coeff) gdzie
bound_min = (ready_at - now) + THRESHOLD. CumulVar pickupu ponad bound → kara →
solver przesuwa odbiór wcześniej / wybiera świeższą sekwencję. Symetryczny do
delivery_soft_deadlines (ten sam prymityw SetCumulVarSoftUpperBound), tylko
anchor = gotowość jedzenia zamiast deadline dostawy. Soft — nigdy INFEASIBLE.

Diagnoza: replay 2026-05-30 (n=1627 food-only) — ~18% odbiorów projektowanych
≥10 min po gotowości, bo objective karał tylko spóźnione DOSTAWY, nie odbiory.
"""
from dispatch_v2.tsp_solver import solve_tsp_with_constraints


def test_freshness_reorders_toward_fresher_pickup():
    """2 pickupy gotowe teraz; geometria daje ~równy dystans dla dwóch sekwencji
    o RÓŻNYM max-czasie odbioru. Kara świeżości MUSI wybrać świeższą bez
    zwiększania jazdy (tie-break). pos: start0, pA5, pB6, dA1, dB0.5."""
    N = 5
    pos = {0: 0.0, 1: 5.0, 2: 6.0, 3: 1.0, 4: 0.5}
    tm = [[abs(pos[i] - pos[j]) for j in range(N)] for i in range(N)]
    pairs = [(1, 3), (2, 4)]
    tw = [(0.0, 120.0), (0.0, 60.0), (0.0, 60.0), (0.0, 120.0), (0.0, 120.0)]

    def maxpick(sol):
        t, prev, mx = 0.0, 0, 0.0
        for n in sol.sequence:
            t += tm[prev][n]
            if n in (1, 2):
                mx = max(mx, t)
            prev = n
        return mx

    base = solve_tsp_with_constraints(
        num_stops=N, pickup_drop_pairs=pairs,
        distance_matrix_km=tm, time_matrix_min=tm, time_windows=tw,
        pickup_freshness_penalties=None, time_limit_ms=300)
    fresh = solve_tsp_with_constraints(
        num_stops=N, pickup_drop_pairs=pairs,
        distance_matrix_km=tm, time_matrix_min=tm, time_windows=tw,
        pickup_freshness_penalties=[None, (0.0, 300.0), (0.0, 300.0), None, None],
        time_limit_ms=300)

    assert base is not None and base.sequence
    assert fresh is not None and fresh.sequence
    # świeższa sekwencja: max-czas odbioru nie gorszy, a tu ściśle mniejszy
    assert maxpick(fresh) <= maxpick(base)
    assert maxpick(fresh) < maxpick(base), \
        f"kara świeżości powinna obniżyć max odbiór; base={maxpick(base)} fresh={maxpick(fresh)}"
    # bez kosztu jazdy (tie-break, nie deadhead)
    assert fresh.total_time_min <= base.total_time_min + 1e-6


def test_freshness_none_is_noop():
    """pickup_freshness_penalties=None → rozwiązuje normalnie (deploy-safe)."""
    dm = [[0.0 if i == j else 2.0 for j in range(3)] for i in range(3)]
    sol = solve_tsp_with_constraints(
        num_stops=3, pickup_drop_pairs=[],
        distance_matrix_km=dm, time_matrix_min=dm,
        pickup_freshness_penalties=None, time_limit_ms=200)
    assert sol is not None and len(sol.sequence) == 2


def test_freshness_wrong_length_rejected():
    """Lista o złej długości (≠ num_stops) → None (walidacja wejścia)."""
    dm = [[0.0 if i == j else 2.0 for j in range(3)] for i in range(3)]
    sol = solve_tsp_with_constraints(
        num_stops=3, pickup_drop_pairs=[],
        distance_matrix_km=dm, time_matrix_min=dm,
        pickup_freshness_penalties=[None, None], time_limit_ms=200)
    assert sol is None


def test_freshness_never_infeasible():
    """Kara soft — nawet absurdalny coeff nie powoduje INFEASIBLE."""
    dm = [[0.0 if i == j else 3.0 for j in range(3)] for i in range(3)]
    sol = solve_tsp_with_constraints(
        num_stops=3, pickup_drop_pairs=[(1, 2)],
        distance_matrix_km=dm, time_matrix_min=dm,
        pickup_freshness_penalties=[None, (0.0, 99999.0), None],
        time_limit_ms=200)
    assert sol is not None and sol.sequence
