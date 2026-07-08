"""perf_tsp_parallel_ceiling — D1/D2 (Sprint D, 2026-07-08).

READ-ONLY. Kwantyfikuje SUFIT równoległości dla dominującej pracy ogona
(OR-Tools solve = 92% wall wg solversplit) i sprawdza, czy równoległość PROCESOWA
(omija GIL) odzyskuje bezczynne rdzenie — oraz czy daje PARYTET tras.

Metoda: przechwyć REALNE wywołania `tsp_solver.solve_tsp_with_constraints`
z kilku decyzji `decide()` (monkeypatch, deepcopy argumentów + wynik-baseline),
potem ZREPLAY-uj TEN SAM zbiór wywołań:
  - sequential
  - ThreadPoolExecutor(k)   (jak dziś w dispatch_pipeline — GIL)
  - ProcessPoolExecutor(k)  (kandydat-lewar — omija GIL)
i porównaj: wall, eff_cores (process_time/wall), speedup, PARYTET tras vs baseline.

Args solvera to czyste prymitywy (macierze/listy/krotki) → picklowalne między procesy.
Solve małych worków TERMINUJE naturalnie (przed sufitem 200ms) → deterministyczny,
więc parytet jest ROZSTRZYGALNY (rozjazd = wall-cutoff ortools, nie proces).

Użycie (cwd=pkgroot):
  PYTHONHASHSEED=0 ZIOMEK_SCRIPTS_ROOT=$PKG /root/.openclaw/venvs/dispatch/bin/python \
    -m dispatch_v2.tools.perf_tsp_parallel_ceiling --n 30 --workers 4
"""
from __future__ import annotations
import argparse
import copy
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

from dispatch_v2.core.decide import decide as _decide          # noqa: E402
from dispatch_v2.core.world_state import WorldState            # noqa: E402
from dispatch_v2 import common as C                            # noqa: E402
from dispatch_v2 import tsp_solver as TSP                      # noqa: E402
from dispatch_v2.tools.perf_lazy_harness import (              # noqa: E402
    _load_events, _synth_fleet, _det_seed,
)
import random  # noqa: E402


def _route_of(sol):
    """Kanoniczna reprezentacja trasy do porównania parytetu."""
    if sol is None:
        return None
    for attr in ("visit_order", "route", "sequence", "order", "stops"):
        v = getattr(sol, attr, None)
        if v is not None:
            return tuple(v)
    return repr(sol)


def _capture_solve_calls(n, seed, fleet_size):
    """Uruchom decide() na n zdarzeniach, przechwyć każde wywołanie solvera."""
    C.ENABLE_V327_PRE_PROPOSAL_RECHECK = False
    calls = []  # [(args_tuple, kwargs_dict, baseline_route)]
    orig = TSP.solve_tsp_with_constraints

    def _spy(*args, **kw):
        res = orig(*args, **kw)
        try:
            calls.append((copy.deepcopy(args), copy.deepcopy(kw), _route_of(res)))
        except Exception:
            pass
        return res

    TSP.solve_tsp_with_constraints = _spy
    try:
        for oe, now in _load_events(n):
            fl = _synth_fleet(oe, now, fleet_size,
                              random.Random(_det_seed(oe["order_id"], seed)))
            _decide(WorldState(fleet_snapshot=dict(fl), now=now), oe)
    finally:
        TSP.solve_tsp_with_constraints = orig
    return calls


# worker top-level (picklowalny) dla ProcessPoolExecutor
def _solve_worker(payload):
    import dispatch_v2.tsp_solver as _t
    args, kw = payload
    sol = _t.solve_tsp_with_constraints(*args, **kw)
    # zwróć tylko trasę (picklowalna) + że nie None
    for attr in ("visit_order", "route", "sequence", "order", "stops"):
        v = getattr(sol, attr, None)
        if v is not None:
            return tuple(v)
    return None if sol is None else repr(sol)


def _run_seq(payloads):
    t0 = time.perf_counter(); c0 = time.process_time()
    out = [_solve_worker(p) for p in payloads]
    return out, (time.perf_counter() - t0), (time.process_time() - c0)


def _run_thread(payloads, k):
    from concurrent.futures import ThreadPoolExecutor
    t0 = time.perf_counter(); c0 = time.process_time()
    with ThreadPoolExecutor(max_workers=k) as ex:
        out = list(ex.map(_solve_worker, payloads))
    return out, (time.perf_counter() - t0), (time.process_time() - c0)


def _run_process(payloads, k):
    from concurrent.futures import ProcessPoolExecutor
    import multiprocessing as mp
    ctx = mp.get_context("fork")
    t0 = time.perf_counter()
    # process_time NIE liczy CPU subprocesów → mierzymy tylko wall dla procesów
    with ProcessPoolExecutor(max_workers=k, mp_context=ctx) as ex:
        out = list(ex.map(_solve_worker, payloads, chunksize=1))
    return out, (time.perf_counter() - t0), None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n", type=int, default=30)
    ap.add_argument("--seed", type=int, default=7)
    ap.add_argument("--fleet", type=int, default=10)
    ap.add_argument("--workers", type=int, default=4)
    ap.add_argument("--repeats", type=int, default=3)
    a = ap.parse_args()

    print(f"# capture: decide() n={a.n} fleet={a.fleet} → zbieram wywołania solvera...")
    calls = _capture_solve_calls(a.n, a.seed, a.fleet)
    payloads = [(args, kw) for args, kw, _ in calls]
    baseline = [r for _, _, r in calls]
    nsolve = len(payloads)
    print(f"# przechwycono {nsolve} realnych wywołań solvera "
          f"({nsolve/max(1,a.n):.1f}/decyzję)")
    if nsolve == 0:
        print("BRAK wywołań solvera — zwiększ --fleet lub --n"); return
    ncores = os.cpu_count() or 4

    # warm (fork/import w subprocesach + ortools)
    _run_process(payloads[:min(8, nsolve)], a.workers)

    def _stats(fn, *fa):
        walls, cpus, outs = [], [], None
        for _ in range(a.repeats):
            out, w, cpu = fn(*fa)
            walls.append(w); outs = out
            if cpu is not None:
                cpus.append(cpu)
        wall = statistics.median(walls)
        cpu = statistics.median(cpus) if cpus else None
        return wall, cpu, outs

    seq_w, seq_cpu, seq_out = _stats(_run_seq, payloads)
    thr_w, thr_cpu, thr_out = _stats(_run_thread, payloads, a.workers)
    prc_w, _, prc_out = _stats(_run_process, payloads, a.workers)

    def _eff(cpu, wall):
        return f"{cpu/wall:.2f}" if (cpu and wall) else "n/a"

    print(f"\n# {nsolve} solve'ów, workers={a.workers}, cores={ncores}, "
          f"repeats={a.repeats} (mediana wall)")
    print(f"  {'tryb':<14} {'wall_ms':>9} {'cpu_ms':>9} {'eff_cores':>9} {'speedup_vs_seq':>14}")
    print(f"  {'sequential':<14} {seq_w*1000:>9.0f} {seq_cpu*1000:>9.0f} "
          f"{_eff(seq_cpu,seq_w):>9} {'1.00x':>14}")
    print(f"  {'thread('+str(a.workers)+')':<14} {thr_w*1000:>9.0f} {thr_cpu*1000:>9.0f} "
          f"{_eff(thr_cpu,thr_w):>9} {seq_w/thr_w:>13.2f}x")
    print(f"  {'process('+str(a.workers)+')':<14} {prc_w*1000:>9.0f} {'n/a':>9} "
          f"{'n/a':>9} {seq_w/prc_w:>13.2f}x")

    # PARYTET tras: baseline (capture, w GIL-owym decide) vs każdy tryb
    def _parity(out):
        same = sum(1 for b, o in zip(baseline, out) if b == o)
        return same, len(out)
    for name, out in (("sequential", seq_out), ("thread", thr_out), ("process", prc_out)):
        same, tot = _parity(out)
        print(f"  parytet {name:<10} {same}/{tot} tras identycznych vs baseline-capture "
              f"({(tot-same)} rozjazd = ortools wall-cutoff / niedeterminizm)")


if __name__ == "__main__":
    main()
