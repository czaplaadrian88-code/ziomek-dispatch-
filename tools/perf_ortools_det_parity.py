#!/usr/bin/env python3
"""A2 PERF (2026-07-08, sprint p95) — harness PARYTETU + DETERMINIZMU + LATENCJI
budżetu solvera OR-Tools (READ-ONLY, zero dotknięcia produkcji).

Cel: udowodnić ZANIM ktokolwiek flipnie `ENABLE_ORTOOLS_DET_TIME_LIMIT`, że
zamiana wall-clock `time_limit` (200 ms) na deterministyczny `solution_limit`:
  (1) daje TĘ SAMĄ trasę (parytet ON↔OFF) — lub udokumentowaną, akceptowalną różnicę,
  (2) usuwa niedeterminizm run-to-run (motyw tmux 31: ~1,7% podłogi replayu),
  (3) nie pogarsza (a najpewniej poprawia) latencji — bo OFF „przepala" pełne
      200 ms nawet po zbieżności GLS, a ON zatrzymuje się po N rozwiązaniach.

Metoda — korpus realistycznych PDP (pickup-and-delivery) w skali Białegostoku:
  * rozmiary worka 2..8 (num_stops = 1 start + 2·bag), losowe ale SEEDOWANE
    (powtarzalny korpus) coords w bbox miasta, macierze dist(haversine)/time(22 km/h),
    część z oknami odbioru (committed czas_kuriera) i twardym SLA dostawy (35 min).
  * NIE dotyka route_simulator_v2 / feasibility / scorer — woła bezpośrednio
    tsp_solver.solve_tsp_with_constraints (warstwa solvera).

Pomiary per case:
  - OFF (wall-clock time_limit=200 ms) ×K → parytet run-to-run + latencja.
  - ON  (solution_limit z common, sufit wall-clock) ×K → determinizm + latencja.
  - ON-vs-OFF: identyczność sekwencji + Δdist_km + Δtime_min.
  - STRESS: OFF z małym time_limit (np. 5 ms) ×K → demonstracja mechanizmu
    (cutoff „na zegarek" tnie w środku szukania → run-to-run rozjazd), którego ON nie ma.

Wyjście: tabela tekstowa + JSON (--out). Determinizm harnessu: seed stały →
te same casey; solve OR-Tools jest deterministyczny w kolejności szukania —
migotanie bierze się WYŁĄCZNIE z wall-clock cutoffu (to właśnie mierzymy).

Użycie:
  ZIOMEK_SCRIPTS_ROOT=<pkgroot> PYTHONPATH=<pkgroot> \
    python -m dispatch_v2.tools.perf_ortools_det_parity --cases 200 --repeats 4
"""
from __future__ import annotations

import argparse
import json
import math
import os
import statistics
import sys
import time
from typing import List, Optional, Tuple

_SCRIPTS_ROOT = os.environ.get("ZIOMEK_SCRIPTS_ROOT") or "/root/.openclaw/workspace/scripts"
if _SCRIPTS_ROOT not in sys.path:
    sys.path.insert(0, _SCRIPTS_ROOT)

from dispatch_v2 import common as C  # noqa: E402
from dispatch_v2 import tsp_solver as T  # noqa: E402

# Białystok bbox (zgrubnie centrum + dzielnice) — realistyczna geometria worka.
_LAT0, _LAT1 = 53.098, 53.170
_LON0, _LON1 = 23.100, 23.230
_SPEED_KMH = 22.0
_SLA_MIN = 35.0


def _hav(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    R = 6371.0
    la1, lo1 = math.radians(a[0]), math.radians(a[1])
    la2, lo2 = math.radians(b[0]), math.radians(b[1])
    dla, dlo = la2 - la1, lo2 - lo1
    x = math.sin(dla / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
    return 2 * R * math.asin(math.sqrt(x))


class _Rng:
    """Deterministyczny LCG (nie Math.random/os.urandom — powtarzalny korpus)."""

    def __init__(self, seed: int):
        self.s = seed & 0xFFFFFFFF

    def next(self) -> float:
        self.s = (1103515245 * self.s + 12345) & 0x7FFFFFFF
        return self.s / 0x7FFFFFFF

    def unif(self, a: float, b: float) -> float:
        return a + (b - a) * self.next()

    def randint(self, a: int, b: int) -> int:
        return a + int(self.next() * (b - a + 1)) % (b - a + 1)


_KM_PER_DEG_LAT = 111.0
_KM_PER_DEG_LON = 66.0  # ~111·cos(53°)


def _pt_near(center: Tuple[float, float], radius_km: float, rng: _Rng) -> Tuple[float, float]:
    """Losowy punkt w promieniu radius_km od center (jednorodnie w kole)."""
    r = radius_km * math.sqrt(rng.next())
    theta = 2 * math.pi * rng.next()
    dlat = (r * math.cos(theta)) / _KM_PER_DEG_LAT
    dlon = (r * math.sin(theta)) / _KM_PER_DEG_LON
    lat = min(_LAT1, max(_LAT0, center[0] + dlat))
    lon = min(_LON1, max(_LON0, center[1] + dlon))
    return (lat, lon)


def _make_case(rng: _Rng, bag: int, with_windows: bool):
    """Buduje 1 REALISTYCZNY PDP: start(0) + bag pickupów + bag dropów.

    Geometria SKUPIONA jak realny worek przechodzący feasibility (inaczej korpus
    mierzy patologie, które R1 spread≤8km / R5 pickup≤1,8km HARD-odrzuca zanim
    trafią do solvera): pickupy w promieniu ~R5 od kotwicy restauracji, dostawy
    w promieniu ~R1/2 od kotwicy dostaw, start kuriera blisko puli. Zwraca kwargs
    dla solve_tsp_with_constraints (bez time_limit / flagi)."""
    n = 1 + 2 * bag
    # kotwice w wewnętrznym bbox (margines, żeby promienie nie wypadały za miasto)
    pick_anchor = (rng.unif(_LAT0 + 0.02, _LAT1 - 0.02), rng.unif(_LON0 + 0.03, _LON1 - 0.03))
    # kotwica dostaw ~ do 4 km od restauracji (realny kierunek dowozu)
    deliv_anchor = _pt_near(pick_anchor, 4.0, rng)
    coords = [_pt_near(pick_anchor, 2.0, rng)]              # 0 = start kuriera blisko puli
    for _ in range(bag):                                    # pickupy: R5 spread ≤ ~1,8 km
        coords.append(_pt_near(pick_anchor, 0.9, rng))
    for _ in range(bag):                                    # dostawy: R1 spread ≤ ~7 km wokół kotwicy
        coords.append(_pt_near(deliv_anchor, 3.5, rng))
    dist = [[round(_hav(coords[i], coords[j]), 3) for j in range(n)] for i in range(n)]
    tmat = [[round(dist[i][j] / _SPEED_KMH * 60.0, 2) for j in range(n)] for i in range(n)]
    # pickupy = 1..bag, dropy = bag+1..2bag (para i → i+bag)
    pairs = [(i, i + bag) for i in range(1, bag + 1)]
    tw = None
    sla_bounds = None
    if with_windows:
        # okna odbioru: committed czas_kuriera ~ [open, open+10] od decyzji;
        # dropy 0..max_route; SLA twardy: dostawa ≤ SLA od "teraz" (realistyczny gate).
        tw = [(0.0, 90.0)] * n
        for p in range(1, bag + 1):
            open_min = rng.unif(0.0, 25.0)
            tw[p] = (open_min, open_min + 10.0)
        sla_bounds = [None] * n
        for d in range(bag + 1, 2 * bag + 1):
            sla_bounds[d] = _SLA_MIN + rng.unif(0.0, 15.0)
    return dict(
        num_stops=n,
        pickup_drop_pairs=pairs,
        distance_matrix_km=dist,
        time_matrix_min=tmat,
        time_windows=tw,
        max_route_min=90.0,
        delivery_sla_hard_span=bool(with_windows),
        delivery_sla_hard_bounds=sla_bounds,
        sla_minutes_hard=_SLA_MIN,
    )


def _solve(case: dict, time_limit_ms: int):
    t0 = time.perf_counter()
    sol = T.solve_tsp_with_constraints(time_limit_ms=time_limit_ms, **case)
    ms = (time.perf_counter() - t0) * 1000.0
    seq = tuple(sol.sequence) if sol else None
    dist = sol.total_distance_km if sol else None
    tmin = sol.total_time_min if sol else None
    return seq, dist, tmin, ms


def _pctile(xs: List[float], q: float) -> float:
    if not xs:
        return float("nan")
    s = sorted(xs)
    k = max(0, min(len(s) - 1, int(math.ceil(q / 100.0 * len(s))) - 1))
    return s[k]


def run(cases: int, repeats: int, prod_ms: int, stress_ms: int, seed: int) -> dict:
    rng = _Rng(seed)
    per_bag = {}
    rows = []
    off_nondet = on_nondet = stress_off_nondet = 0
    parity_same = parity_diff = 0
    diff_dist = []
    diff_time = []
    off_lat = []
    on_lat = []
    total = 0

    for i in range(cases):
        bag = 2 + (i % 7)                      # 2..8, równomiernie
        with_windows = (i % 2 == 0)            # połowa z oknami/SLA (trudniejsze)
        case = _make_case(rng, bag, with_windows)
        total += 1

        # OFF (produkcyjny wall-clock)
        C.ENABLE_ORTOOLS_DET_TIME_LIMIT = False
        off_runs = [_solve(case, prod_ms) for _ in range(repeats)]
        off_seqs = {r[0] for r in off_runs}
        off_lat.extend(r[3] for r in off_runs)
        if len(off_seqs) > 1:
            off_nondet += 1

        # STRESS OFF (mały wall-clock → wymusza cutoff w środku szukania)
        stress_runs = [_solve(case, stress_ms) for _ in range(repeats)]
        if len({r[0] for r in stress_runs}) > 1:
            stress_off_nondet += 1

        # ON (deterministyczny budżet)
        C.ENABLE_ORTOOLS_DET_TIME_LIMIT = True
        on_runs = [_solve(case, prod_ms) for _ in range(repeats)]
        on_seqs = {r[0] for r in on_runs}
        on_lat.extend(r[3] for r in on_runs)
        if len(on_seqs) > 1:
            on_nondet += 1
        C.ENABLE_ORTOOLS_DET_TIME_LIMIT = False

        off_ref = off_runs[0]
        on_ref = on_runs[0]
        same = (off_ref[0] == on_ref[0])
        if same:
            parity_same += 1
        else:
            parity_diff += 1
            if off_ref[1] is not None and on_ref[1] is not None:
                diff_dist.append(on_ref[1] - off_ref[1])
                diff_time.append(on_ref[2] - off_ref[2])

        b = per_bag.setdefault(bag, {"n": 0, "parity_same": 0, "off_lat": [], "on_lat": []})
        b["n"] += 1
        b["parity_same"] += int(same)
        b["off_lat"].append(off_ref[3])
        b["on_lat"].append(on_ref[3])

        rows.append({
            "i": i, "bag": bag, "windows": with_windows,
            "off_seq": list(off_ref[0]) if off_ref[0] else None,
            "on_seq": list(on_ref[0]) if on_ref[0] else None,
            "same": same,
            "off_ms": round(off_ref[3], 1), "on_ms": round(on_ref[3], 1),
            "d_dist_km": None if same or off_ref[1] is None else round(on_ref[1] - off_ref[1], 3),
        })

    def _lat_stats(xs):
        return {"p50": round(statistics.median(xs), 1) if xs else None,
                "p95": round(_pctile(xs, 95), 1) if xs else None,
                "max": round(max(xs), 1) if xs else None,
                "mean": round(statistics.mean(xs), 1) if xs else None}

    bag_summary = {}
    for bag, b in sorted(per_bag.items()):
        bag_summary[bag] = {
            "n": b["n"],
            "parity_pct": round(100 * b["parity_same"] / b["n"], 1),
            "off_lat": _lat_stats(b["off_lat"]),
            "on_lat": _lat_stats(b["on_lat"]),
        }

    return {
        "config": {"cases": cases, "repeats": repeats, "prod_ms": prod_ms,
                   "stress_ms": stress_ms, "seed": seed,
                   "solution_limit": C.ORTOOLS_DET_SOLUTION_LIMIT,
                   "wall_ceiling_ms": C.ORTOOLS_DET_WALL_CEILING_MS},
        "totals": {
            "cases": total,
            "parity_same": parity_same,
            "parity_diff": parity_diff,
            "parity_pct": round(100 * parity_same / max(1, total), 2),
            "off_nondet_cases": off_nondet,
            "off_nondet_pct": round(100 * off_nondet / max(1, total), 2),
            "on_nondet_cases": on_nondet,
            "on_nondet_pct": round(100 * on_nondet / max(1, total), 2),
            "stress_off_nondet_cases": stress_off_nondet,
            "stress_off_nondet_pct": round(100 * stress_off_nondet / max(1, total), 2),
        },
        "parity_diff_deltas": {
            "n": len(diff_dist),
            "d_dist_km": {"mean": round(statistics.mean(diff_dist), 3) if diff_dist else None,
                          "min": round(min(diff_dist), 3) if diff_dist else None,
                          "max": round(max(diff_dist), 3) if diff_dist else None},
            "d_time_min": {"mean": round(statistics.mean(diff_time), 3) if diff_time else None,
                           "min": round(min(diff_time), 3) if diff_time else None,
                           "max": round(max(diff_time), 3) if diff_time else None},
        },
        "latency": {"off": _lat_stats(off_lat), "on": _lat_stats(on_lat)},
        "per_bag": bag_summary,
        "rows_head": rows[:12],
    }


def _fmt(r: dict) -> str:
    t = r["totals"]
    lo, ln = r["latency"]["off"], r["latency"]["on"]
    out = []
    out.append("═" * 72)
    out.append("A2 OR-Tools deterministyczny budżet — PARYTET + DETERMINIZM + LATENCJA")
    out.append("═" * 72)
    cfg = r["config"]
    out.append(f"korpus: {cfg['cases']} casów × {cfg['repeats']} powtórzeń | prod={cfg['prod_ms']}ms "
               f"stress={cfg['stress_ms']}ms | solution_limit={cfg['solution_limit']} "
               f"ceiling={cfg['wall_ceiling_ms']}ms | seed={cfg['seed']}")
    out.append("")
    out.append(f"PARYTET ON↔OFF:  {t['parity_same']}/{t['cases']} identycznych sekwencji "
               f"({t['parity_pct']}%)  | różne: {t['parity_diff']}")
    dd = r["parity_diff_deltas"]
    if dd["n"]:
        out.append(f"  różnice (n={dd['n']}): Δdist_km mean={dd['d_dist_km']['mean']} "
                   f"[{dd['d_dist_km']['min']}..{dd['d_dist_km']['max']}] | "
                   f"Δtime_min mean={dd['d_time_min']['mean']} "
                   f"[{dd['d_time_min']['min']}..{dd['d_time_min']['max']}]")
    out.append("")
    out.append(f"DETERMINIZM (run-to-run, prod {cfg['prod_ms']}ms):")
    out.append(f"  OFF niedeterministycznych casów: {t['off_nondet_cases']} ({t['off_nondet_pct']}%)")
    out.append(f"  ON  niedeterministycznych casów: {t['on_nondet_cases']} ({t['on_nondet_pct']}%)  ← cel 0")
    out.append(f"  STRESS OFF ({cfg['stress_ms']}ms) niedet.: {t['stress_off_nondet_cases']} "
               f"({t['stress_off_nondet_pct']}%)  ← demonstracja mechanizmu wall-clock")
    out.append("")
    out.append(f"LATENCJA solve (ms):  OFF p50={lo['p50']} p95={lo['p95']} max={lo['max']} | "
               f"ON p50={ln['p50']} p95={ln['p95']} max={ln['max']}")
    out.append("")
    out.append("per rozmiar worka:")
    out.append("  bag |  n | parytet% | OFF p50/p95/max | ON p50/p95/max")
    for bag, b in r["per_bag"].items():
        o, n = b["off_lat"], b["on_lat"]
        out.append(f"   {bag}  | {b['n']:2d} |  {b['parity_pct']:5.1f}  | "
                   f"{o['p50']}/{o['p95']}/{o['max']} | {n['p50']}/{n['p95']}/{n['max']}")
    out.append("═" * 72)
    return "\n".join(out)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", type=int, default=200)
    ap.add_argument("--repeats", type=int, default=4)
    ap.add_argument("--prod-ms", type=int, default=200)
    ap.add_argument("--stress-ms", type=int, default=5)
    ap.add_argument("--seed", type=int, default=20260708)
    ap.add_argument("--sol-limit", type=int, default=None,
                    help="override common.ORTOOLS_DET_SOLUTION_LIMIT dla tego biegu (sweep)")
    ap.add_argument("--ceiling", type=int, default=None,
                    help="override common.ORTOOLS_DET_WALL_CEILING_MS dla tego biegu (sweep sufitu)")
    ap.add_argument("--out", default="/tmp/perf_ortools_det_parity.json")
    a = ap.parse_args()
    if a.sol_limit is not None:
        C.ORTOOLS_DET_SOLUTION_LIMIT = a.sol_limit
    if a.ceiling is not None:
        C.ORTOOLS_DET_WALL_CEILING_MS = a.ceiling
    r = run(a.cases, a.repeats, a.prod_ms, a.stress_ms, a.seed)
    print(_fmt(r))
    try:
        with open(a.out, "w") as f:
            json.dump(r, f, indent=2, ensure_ascii=False)
        print(f"\nJSON → {a.out}")
    except Exception as e:
        print(f"[uwaga] zapis JSON nieudany: {e}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
