"""perf_pipeline_contention_probe — D1 (Sprint D, 2026-07-08).

READ-ONLY diagnostyka: GDZIE w pipelinie decyzji formuje się ogon peak-p95 pod
KONTENCJĄ (nie solver-config — to Sprint A). Punkt wyjścia = A1 (ogon = flota/
kontencja, solver=podłoga). Ten probe schodzi GŁĘBIEJ: czy pula ThreadPoolExecutor
jest GIL/CPU-bound czy I/O-bound, ile redundantnej pracy OSRM robią kandydaci,
jaki udział ma solver vs reszta.

Bazuje 1:1 na syntezie floty + replay realnych NEW_ORDER z perf_lazy_harness
(ta sama metoda co A-team, determinizm md5-seed). Woła REALNĄ fasadę decide()
(pełny pipeline + pula). OSRM :5001 realny. Zero dotknięcia żywych serwisów/flag
na dysku (toggluje flagi TYLKO in-proc na czas pomiaru).

Tryby:
  scaling    — wall p50/p95 + EFEKTYWNE RDZENIE (process_time/wall) dla fleet
               1,2,4,6,8,10,13. Pokazuje sufit równoległości (GIL) empirycznie.
  osrm       — liczba round-tripów OSRM (route+table) per COLD decyzję + ile z nich
               redundantnych (te same coords między kandydatami) → czy pre-warm
               wspólnych legów tnie kontencję. + wall cold vs warm.
  solversplit— wall+cpu z ENABLE_V326_OR_TOOLS_TSP ON vs OFF (in-proc, pomiarowy) →
               udział solver-callback (GIL) w ogonie vs reszta pipeline'u.

Użycie (kanon lub worktree; ZIOMEK_SCRIPTS_ROOT steruje importem):
  PYTHONHASHSEED=0 nice -n 19 /root/.openclaw/venvs/dispatch/bin/python \
    -m dispatch_v2.tools.perf_pipeline_contention_probe scaling --n 60
"""
from __future__ import annotations
import argparse
import os
import statistics
import sys
import time
from pathlib import Path

_ROOT = os.environ.get("ZIOMEK_SCRIPTS_ROOT") or str(Path(__file__).resolve().parents[2])
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

import logging
logging.disable(logging.WARNING)

from dispatch_v2.core.decide import decide as _decide          # noqa: E402  K09 fasada
from dispatch_v2.core.world_state import WorldState            # noqa: E402
from dispatch_v2 import common as C                            # noqa: E402
from dispatch_v2 import osrm_client as OSRM                    # noqa: E402
# reuse fleet synthesis + event replay from A-team harness (identyczna metoda)
from dispatch_v2.tools.perf_lazy_harness import (              # noqa: E402
    _load_events, _synth_fleet, _det_seed,
)
import random  # noqa: E402


def _pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(len(xs) * q))]


def _build_cases(n, seed, fleet_size):
    """Lista (order_event, now, fleet_dict) dla stałego rozmiaru floty."""
    out = []
    for oe, now in _load_events(n):
        fl = _synth_fleet(oe, now, fleet_size,
                          random.Random(_det_seed(oe["order_id"], seed)))
        out.append((oe, now, fl))
    return out


def _run_once(oe, now, fl):
    return _decide(WorldState(fleet_snapshot=dict(fl), now=now), oe)


# ─────────────────────────── scaling ───────────────────────────

def cmd_scaling(a):
    C.ENABLE_V327_PRE_PROPOSAL_RECHECK = False  # bez żywego fetchu panelu (izolacja puli)
    sizes = [int(x) for x in a.sizes.split(",")]
    print(f"# scaling: n={a.n} repeats={a.repeats} sizes={sizes} "
          f"(cpu-cores={os.cpu_count()})")
    print(f"# {'fleet':>5} {'ndec':>5} {'wall_p50':>9} {'wall_p95':>9} "
          f"{'wall_mean':>9} {'cpu/dec':>8} {'eff_cores':>9} {'par_eff%':>8}")
    ncores = os.cpu_count() or 4
    for fs in sizes:
        cases = _build_cases(a.n, a.seed, fs)
        # warm (cache OSRM + import ortools)
        for oe, now, fl in cases[:3]:
            _run_once(oe, now, fl)
        walls = []
        cpu0 = time.process_time()
        wall0 = time.perf_counter()
        for _ in range(a.repeats):
            for oe, now, fl in cases:
                t0 = time.perf_counter()
                _run_once(oe, now, fl)
                walls.append((time.perf_counter() - t0) * 1000)
        cpu_total = time.process_time() - cpu0
        wall_total = time.perf_counter() - wall0
        ndec = len(walls)
        cpu_per = cpu_total / ndec * 1000
        wall_mean = statistics.mean(walls)
        # eff_cores = ile rdzeni realnie pracuje (process_time / wall). 1.0=GIL-serial,
        # ~ncores=pełna równoległość. par_eff = eff_cores/min(ncores,fleet).
        eff_cores = cpu_total / wall_total if wall_total else 0
        par_ceiling = min(ncores, max(1, fs))
        par_eff = eff_cores / par_ceiling * 100
        print(f"  {fs:>5} {ndec:>5} {_pct(walls,.5):>9.1f} {_pct(walls,.95):>9.1f} "
              f"{wall_mean:>9.1f} {cpu_per:>8.1f} {eff_cores:>9.2f} {par_eff:>8.1f}")
    print("# eff_cores≈1.0 => decyzja GIL-serializowana (pula nie daje równoległości);")
    print("# eff_cores rośnie z flotą => część pracy realnie równoległa (OSRM I/O / C++ solve).")


# ─────────────────────────── osrm redundancy ───────────────────────────

def cmd_osrm(a):
    C.ENABLE_V327_PRE_PROPOSAL_RECHECK = False
    fs = a.fleet
    cases = _build_cases(a.n, a.seed, fs)

    # liczniki per-decyzja: ile round-tripów HTTP + ile DISTINCT coord-keys.
    stats = {"http": 0, "keys": []}
    orig_urlopen = OSRM.urllib.request.urlopen

    def _counting_urlopen(url, *args, **kw):
        stats["http"] += 1
        # klucz = sama ścieżka coords (redundancja = te same coords różni kandydaci)
        try:
            stats["keys"].append(str(url).split("/driving/")[-1].split("?")[0])
        except Exception:
            pass
        return orig_urlopen(url, *args, **kw)

    OSRM.urllib.request.urlopen = _counting_urlopen
    try:
        per_dec = []
        walls_cold, walls_warm = [], []
        for oe, now, fl in cases:
            # COLD: wyczyść cache OSRM przed decyzją → mierzymy redundancję W JEDNEJ decyzji
            with OSRM._module_lock:
                OSRM._route_cache.clear()
                OSRM._table_cell_cache.clear()
            stats["http"] = 0
            stats["keys"] = []
            t0 = time.perf_counter()
            _run_once(oe, now, fl)
            walls_cold.append((time.perf_counter() - t0) * 1000)
            http = stats["http"]
            distinct = len(set(stats["keys"]))
            per_dec.append((http, distinct))
            # WARM: druga decyzja bez czyszczenia (cache pełny)
            t0 = time.perf_counter()
            _run_once(oe, now, fl)
            walls_warm.append((time.perf_counter() - t0) * 1000)
        https = [h for h, _ in per_dec]
        dist = [d for _, d in per_dec]
        redun = [(h - d) for h, d in per_dec]  # nadmiarowe (te same coords)
        print(f"# osrm redundancy: n={len(per_dec)} fleet={fs}")
        print(f"  HTTP round-trips/COLD-decyzję:  p50={_pct(https,.5)} p95={_pct(https,.95)} "
              f"mean={statistics.mean(https):.1f} max={max(https)}")
        print(f"  DISTINCT coord-keys/decyzję:    p50={_pct(dist,.5)} p95={_pct(dist,.95)} "
              f"mean={statistics.mean(dist):.1f}")
        print(f"  REDUNDANT (http-distinct):      p50={_pct(redun,.5)} p95={_pct(redun,.95)} "
              f"mean={statistics.mean(redun):.1f}  "
              f"(udział {statistics.mean(redun)/max(1,statistics.mean(https))*100:.0f}%)")
        print(f"  wall COLD p50={_pct(walls_cold,.5):.1f} p95={_pct(walls_cold,.95):.1f} | "
              f"wall WARM p50={_pct(walls_warm,.5):.1f} p95={_pct(walls_warm,.95):.1f}")
        print(f"  Δwall cold-warm p50={_pct(walls_cold,.5)-_pct(walls_warm,.5):.1f}ms "
              f"= udział OSRM-HTTP w ścieżce krytycznej")
    finally:
        OSRM.urllib.request.urlopen = orig_urlopen


# ─────────────────────────── solver split ───────────────────────────

def _measure_wall_cpu(cases, repeats):
    for oe, now, fl in cases[:3]:
        _run_once(oe, now, fl)
    walls = []
    cpu0 = time.process_time(); w0 = time.perf_counter()
    for _ in range(repeats):
        for oe, now, fl in cases:
            t0 = time.perf_counter()
            _run_once(oe, now, fl)
            walls.append((time.perf_counter() - t0) * 1000)
    cpu = (time.process_time() - cpu0) / len(walls) * 1000
    return dict(p50=round(_pct(walls, .5), 1), p95=round(_pct(walls, .95), 1),
                mean=round(statistics.mean(walls), 1), cpu_per=round(cpu, 1))


def cmd_solversplit(a):
    C.ENABLE_V327_PRE_PROPOSAL_RECHECK = False
    cases = _build_cases(a.n, a.seed, a.fleet)
    orig = C.ENABLE_V326_OR_TOOLS_TSP
    print(f"# solversplit: n={a.n} fleet={a.fleet} repeats={a.repeats}")
    C.ENABLE_V326_OR_TOOLS_TSP = True
    on = _measure_wall_cpu(cases, a.repeats)
    C.ENABLE_V326_OR_TOOLS_TSP = False
    off = _measure_wall_cpu(cases, a.repeats)
    C.ENABLE_V326_OR_TOOLS_TSP = orig
    print(f"  ORTOOLS ON : wall p50={on['p50']} p95={on['p95']} mean={on['mean']} cpu/dec={on['cpu_per']}")
    print(f"  ORTOOLS OFF: wall p50={off['p50']} p95={off['p95']} mean={off['mean']} cpu/dec={off['cpu_per']}")
    print(f"  Δ (solver): wall p50={on['p50']-off['p50']:.1f} p95={on['p95']-off['p95']:.1f} "
          f"mean={on['mean']-off['mean']:.1f} cpu/dec={on['cpu_per']-off['cpu_per']:.1f}")
    print(f"  udział solvera w wall-mean: {(on['mean']-off['mean'])/max(1,on['mean'])*100:.0f}%  "
          f"| w cpu: {(on['cpu_per']-off['cpu_per'])/max(1,on['cpu_per'])*100:.0f}%")
    print("# UWAGA: OFF => greedy/bruteforce (INNE decyzje) — to pomiar KOSZTU, nie parytet.")


# ─────────────────────────── timelimit sensitivity ───────────────────────────

def cmd_timelimit(a):
    """READ-ONLY pomiar czułości OGONA decyzji na wall-budget solvera OR-Tools.
    Monkeypatch IN-PROC `common.V326_OR_TOOLS_TIME_LIMIT_MS` (NIE edycja pliku, NIE
    flip) — mierzy, czy krótszy budżet tnie p95 i CZY zachowuje decyzję (parytet
    serializowanego wyniku vs baseline 200ms). To DIAGNOZA „gdzie siedzi ogon";
    właściwy lewar (solution_limit) = A2 Sprintu A, NIE ten sprint."""
    from dispatch_v2 import shadow_dispatcher as SD
    from dispatch_v2.tools.perf_lazy_harness import _strip  # rekurencyjny strip pól czasowych
    import json
    C.ENABLE_V327_PRE_PROPOSAL_RECHECK = False
    cases = _build_cases(a.n, a.seed, a.fleet)
    limits = [int(x) for x in a.limits.split(",")]
    orig_tl = C.V326_OR_TOOLS_TIME_LIMIT_MS

    def _serialize(res):
        try:
            d = SD._serialize_result(res, "PROBE", 0.0)
        except Exception as e:
            return "ERR:" + repr(e)
        # rekurencyjnie wytnij pola czysto-czasowe → porównujemy DECYZJĘ nie czas
        return json.dumps(_strip(d), ensure_ascii=False, sort_keys=True, default=str)

    # baseline @200ms: serializuj decyzje
    C.V326_OR_TOOLS_TIME_LIMIT_MS = orig_tl
    for oe, now, fl in cases[:3]:
        _run_once(oe, now, fl)
    base = [_serialize(_run_once(oe, now, fl)) for oe, now, fl in cases]

    print(f"# timelimit sensitivity: n={a.n} fleet={a.fleet} baseline_tl={orig_tl}ms")
    print(f"# {'tl_ms':>6} {'wall_p50':>9} {'wall_p95':>9} {'wall_mean':>9} "
          f"{'decyzja=base':>13}")
    for tl in limits:
        C.V326_OR_TOOLS_TIME_LIMIT_MS = tl
        walls, outs = [], []
        for _ in range(a.repeats):
            for oe, now, fl in cases:
                t0 = time.perf_counter()
                r = _run_once(oe, now, fl)
                walls.append((time.perf_counter() - t0) * 1000)
                if _ == 0:
                    outs.append(_serialize(r))
        same = sum(1 for b, o in zip(base, outs) if b == o)
        print(f"  {tl:>6} {_pct(walls,.5):>9.1f} {_pct(walls,.95):>9.1f} "
              f"{statistics.mean(walls):>9.1f} {same:>6}/{len(base):<6}")
    C.V326_OR_TOOLS_TIME_LIMIT_MS = orig_tl
    print("# decyzja=base: ile decyzji identycznych z baseline 200ms (parytet).")
    print("# Krótszy tl tnie p95 przy zachowaniu decyzji => ogon = wall-budget solvera")
    print("# (lewar = A2 solution_limit Sprintu A, NIE ten sprint — kontencja tego nie ruszy).")


def main():
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    for name in ("scaling", "osrm", "solversplit", "timelimit"):
        s = sub.add_parser(name)
        s.add_argument("--n", type=int, default=60)
        s.add_argument("--seed", type=int, default=7)
        s.add_argument("--repeats", type=int, default=2)
        if name == "scaling":
            s.add_argument("--sizes", default="1,2,4,6,8,10,13")
        else:
            s.add_argument("--fleet", type=int, default=10)
        if name == "timelimit":
            s.add_argument("--limits", default="200,120,80,50")
    a = ap.parse_args()
    {"scaling": cmd_scaling, "osrm": cmd_osrm, "solversplit": cmd_solversplit,
     "timelimit": cmd_timelimit}[a.cmd](a)


if __name__ == "__main__":
    main()
