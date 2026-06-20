#!/usr/bin/env python3
"""
[D2] Testy grid-search coeff committed-pickup penalty (READ-ONLY tool).

Sprawdza CZYSTĄ logikę (bez ortools/replay):
  - build_metrics liczy G1 (NET = red_sum − regr_sum), G2a (śr. regresja),
    G2b (#regr), G3 (delta INFEASIBLE) poprawnie na sztucznej próbce
  - krzywa NET-benefit vs regresja G2 rośnie/spada jak oczekiwano
  - pareto_front zwraca tylko niedominowane punkty
  - knee_point jest DETERMINISTYCZNE i wybiera kolano (max zysk/koszt)

Uruchom:
  /root/.openclaw/venvs/dispatch/bin/python -m pytest \\
      dispatch_v2/tests/test_coeff_grid.py -q
"""
import sys
from collections import defaultdict

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2.tools.coeff_gridsearch import (  # noqa: E402
    build_metrics, pareto_front, knee_point, DAYS)


# --------------------------------------------------------------------------
# Pomocnik: zbuduj stats[coeff][day] z prostej specyfikacji per coeff:
#   spec[coeff] = dict(red=[...], regr=[...], inf_on=int, inf_off=int, border=int)
# Cała populacja wrzucona w pierwszy dzień DAYS (reszta zerowa) — build_metrics
# sumuje po dniach, więc rozkład między dni nie zmienia agregatu.
# --------------------------------------------------------------------------
def make_stats(spec):
    coeffs = list(spec.keys())
    stats = {c: defaultdict(lambda: dict(pop=0, red=[], regr=[],
             infeasible_on=0, infeasible_off=0, border=0, simfail=0, off_nolate=0))
             for c in coeffs}
    d0 = DAYS[0]
    for c, s in spec.items():
        red = list(s.get("red", []))
        regr = list(s.get("regr", []))
        border = int(s.get("border", 0))
        pop = len(red) + len(regr) + border
        cell = stats[c][d0]
        cell["pop"] = pop
        cell["red"] = red
        cell["regr"] = regr
        cell["border"] = border
        cell["infeasible_on"] = int(s.get("inf_on", 0))
        cell["infeasible_off"] = int(s.get("inf_off", 0))
    return stats, coeffs


# =========================================================================
# G1/G2/G3 liczone poprawnie
# =========================================================================
def test_g1_net_benefit_is_red_minus_regr():
    spec = {100: dict(red=[5.0, 10.0, 3.0], regr=[2.0], border=4, inf_on=1, inf_off=1)}
    rows = build_metrics(*make_stats(spec))
    r = rows[0]
    assert r["red_sum"] == 18.0
    assert r["regr_sum"] == 2.0
    assert r["g1"] == 16.0                 # NET = 18 − 2
    assert r["reduced"] == 3
    assert r["regr"] == 1                   # G2b = liczba regresji
    assert r["g2b"] == 1
    # G2a = śr. regresja na rekord = regr_sum / pop ; pop = 3+1+4 = 8
    assert r["pop"] == 8
    assert abs(r["g2a"] - (2.0 / 8)) < 1e-9
    assert r["g3"] == 0                     # inf_on − inf_off = 0


def test_g3_counts_new_infeasible_as_koord_pressure():
    # ON wprowadza 3 nowe fallbacki ponad baseline OFF (2) → G3 = +1
    spec = {200: dict(red=[4.0], regr=[], inf_on=3, inf_off=2)}
    rows = build_metrics(*make_stats(spec))
    assert rows[0]["g3"] == 1              # rośnie KOORD
    # brak wzrostu gdy równe
    spec2 = {200: dict(red=[4.0], regr=[], inf_on=2, inf_off=2)}
    assert build_metrics(*make_stats(spec2))[0]["g3"] == 0


def test_zero_pop_does_not_divide_by_zero():
    spec = {50: dict(red=[], regr=[], border=0)}
    rows = build_metrics(*make_stats(spec))
    assert rows[0]["pop"] == 0
    assert rows[0]["g2a"] == 0.0          # guard


# =========================================================================
# Krzywa NET-benefit vs regresja G2 — kształt diminishing returns
# =========================================================================
def _diminishing_grid():
    """Realistyczny kształt: G1 rośnie z malejącym przyrostem; regresja rośnie
    z rosnącym przyrostem (jak w danych: coeff 100→200 dał +3 tys. min G1, ale
    większe pojedyncze regresje). Kolano oczekiwane ~150."""
    return {
        25:  dict(red=_rep(10.0, 5),   regr=_rep(2.0, 1)),   # G1=48,  regr_sum=2
        50:  dict(red=_rep(10.0, 30),  regr=_rep(2.0, 3)),   # G1=294, regr_sum=6
        75:  dict(red=_rep(10.0, 80),  regr=_rep(2.0, 8)),   # G1=784, regr_sum=16
        100: dict(red=_rep(10.0, 150), regr=_rep(2.0, 18)),  # G1=1464,regr_sum=36
        125: dict(red=_rep(10.0, 200), regr=_rep(2.0, 30)),  # G1=1940,regr_sum=60
        150: dict(red=_rep(10.0, 235), regr=_rep(2.0, 48)),  # G1=2254,regr_sum=96
        200: dict(red=_rep(10.0, 250), regr=_rep(2.0, 90)),  # G1=2320,regr_sum=180
        300: dict(red=_rep(10.0, 255), regr=_rep(2.0, 160)), # G1=2230,regr_sum=320
    }


def _rep(v, n):
    return [v] * n


def test_net_benefit_curve_monotonic_then_saturates():
    rows = build_metrics(*make_stats(_diminishing_grid()))
    g1 = {r["coeff"]: r["g1"] for r in rows}
    # G1 rośnie do pewnego coeff, potem saturuje/spada (regresja dogania)
    assert g1[25] < g1[50] < g1[100] < g1[150]
    assert g1[300] < g1[200]              # za wysoki coeff — regresja zjada zysk
    # regresja G2 rośnie monotonicznie z coeff
    regr_sum = {r["coeff"]: r["regr_sum"] for r in rows}
    seq = [regr_sum[c] for c in (25, 50, 75, 100, 125, 150, 200, 300)]
    assert seq == sorted(seq)
    assert all(seq[i] < seq[i + 1] for i in range(len(seq) - 1))


# =========================================================================
# Front Pareto
# =========================================================================
def test_pareto_drops_dominated_points():
    rows = build_metrics(*make_stats(_diminishing_grid()))
    front = pareto_front(rows)
    fcoeffs = {p["coeff"] for p in front}
    # coeff=300 dominowany przez 200 (mniej G1, więcej regresji) → poza frontem
    assert 300 not in fcoeffs
    # front posortowany rosnąco po regr_sum
    rs = [p["regr_sum"] for p in front]
    assert rs == sorted(rs)
    # na froncie G1 musi rosnąć wraz z regr_sum (inaczej dominacja)
    g1s = [p["g1"] for p in front]
    assert g1s == sorted(g1s)


def test_pareto_strict_dominance_definition():
    # B(g1=100,regr=10) dominuje A(g1=90,regr=10) [równy koszt, gorszy zysk]
    pts = [dict(coeff=1, g1=90.0, regr_sum=10.0),
           dict(coeff=2, g1=100.0, regr_sum=10.0)]
    front = pareto_front(pts)
    assert {p["coeff"] for p in front} == {2}


# =========================================================================
# Kolano — deterministyczne i sensowne
# =========================================================================
def test_knee_deterministic_and_repeatable():
    rows = build_metrics(*make_stats(_diminishing_grid()))
    k1, _ = knee_point(rows)
    k2, _ = knee_point(list(reversed(rows)))   # kolejność wejścia nie zmienia wyniku
    assert k1 is not None
    assert k1["coeff"] == k2["coeff"]


def test_knee_picks_elbow_not_extreme():
    """Kolano nie powinno być skrajem (najtańszy ani najdroższy) gdy istnieje
    wyraźny łokieć — powinno wpaść w środkowy zakres siatki."""
    rows = build_metrics(*make_stats(_diminishing_grid()))
    knee, front = knee_point(rows)
    assert knee["coeff"] not in (front[0]["coeff"], front[-1]["coeff"]) \
        or len(front) < 3
    # przy tym kształcie kolano w przedziale 75..200 (środek)
    assert 75 <= knee["coeff"] <= 200


def test_knee_fallback_when_front_small():
    # tylko 2 niedominowane punkty → kolano = maks G1 przy min regr
    spec = {
        100: dict(red=_rep(10.0, 10), regr=_rep(2.0, 1)),    # G1=98, regr=2
        200: dict(red=_rep(10.0, 20), regr=_rep(2.0, 2)),    # G1=196,regr=4
    }
    rows = build_metrics(*make_stats(spec))
    knee, front = knee_point(rows)
    assert len(front) == 2
    assert knee["coeff"] == 200            # wyższy G1 wygrywa przy <3 punktach


def test_knee_handles_flat_g1_chooses_cheapest():
    """Gdy G1 płaskie a regresja rośnie — front zwija się do najtańszego."""
    spec = {
        50:  dict(red=_rep(10.0, 100), regr=_rep(2.0, 5)),   # G1=990, regr=10
        100: dict(red=_rep(10.0, 100), regr=_rep(2.0, 10)),  # G1=980, regr=20
        200: dict(red=_rep(10.0, 100), regr=_rep(2.0, 20)),  # G1=960, regr=40
    }
    rows = build_metrics(*make_stats(spec))
    front = pareto_front(rows)
    # tylko coeff=50 niedominowany (najwyższy G1 i najniższa regresja)
    assert {p["coeff"] for p in front} == {50}
    knee, _ = knee_point(rows)
    assert knee["coeff"] == 50


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
