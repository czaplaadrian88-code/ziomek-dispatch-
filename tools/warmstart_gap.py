#!/usr/bin/env python3
"""
[#3] LUKA MAKESPAN: solver 200ms (obecny) vs 2000ms (dłuższy budżet) — READ-ONLY.
2026-06-20. Czy warm-start + dłuższy czas solvera są WARTE, czy 200ms już wystarcza?

KONTEKST MECHANIZMU (ustalone w D2):
  route_simulator_v2._ortools_plan importuje limit z common.V326_OR_TOOLS_TIME_LIMIT_MS
  (default 200ms) per-wywołanie (l.895) → tsp_solver.solve_tsp_with_constraints(
  time_limit_ms=...). solver = guided local search z first-solution heuristic;
  na INFEASIBLE z time-windows robi retry bez constraints, a gdy i to nie da
  sekwencji → caller spada do GREEDY (strategy zawiera 'greedy'/'fallback').

CO MIERZYMY (per worek bag_size>=3, gdzie sekwencja faktycznie ma znaczenie):
  - makespan = RoutePlanV2.total_duration_min (czas ukończenia trasy od startu)
  - re-solve @200ms i @2000ms (monkeypatch common.V326_OR_TOOLS_TIME_LIMIT_MS;
    funkcja czyta atrybut na świeżo, więc patch działa)
  - GAP = makespan(200) − makespan(2000) [min]; >0 = 2000ms LEPSZY (krótszy plan)
  - strategy obu (wykrycie greedy-fallback)

OSOBNO (inny problem niż czas solvera — modelowanie ograniczeń/time-windows):
  - % worków gdzie OR-Tools w ogóle nie znajduje feasible → strategy greedy/fallback
    (przy 200ms i przy 2000ms — czy dłuższy czas to ratuje?)

PRÓG ISTOTNOŚCI luki (Adrian): GAP > 1.0 min ALBO GAP/makespan > 5%.

⚠ Determinizm: OR-Tools per-proces deterministyczny (sprawdzone w D2: OFF 2× = 0
rozjazdu). Dlatego 1 worker wystarcza i jest UCZCIWY (200 i 2000 dostają ten sam
heurystyk startowy, różni je tylko budżet). Single-worker też SZANUJE obciążenie
serwera (spec: próbkuj skromnie).

URUCHOMIENIE (ortools → venv dispatch):
  /root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.tools.warmstart_gap \\
      --sample 150 --budget-hi 2000 --dump out.json
"""
import argparse
import json
import sys
import time

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
sys.path.insert(0, "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-17")

import dispatch_v2.common as C  # noqa: E402
from dispatch_v2.route_simulator_v2 import simulate_bag_route_v2  # noqa: E402
import n5s2_committed_penalty_replay as M  # noqa: E402  (reuse pts/mk helpers)

CAP = M.CAP
SIG_ABS_MIN = 1.0     # próg istotnej luki (minuty)
SIG_REL = 0.05        # albo 5% makespanu
BUDGET_LO = 200       # obecny limit produkcyjny


def is_greedy(plan):
    s = (getattr(plan, "strategy", "") or "").lower()
    return ("greedy" in s) or ("fallback" in s) or ("rejected" in s) \
        or (not getattr(plan, "sequence", None))


def makespan(plan):
    return getattr(plan, "total_duration_min", None)


def select_worki(min_bag, target, every):
    """Strumieniowo wybierz worki bag_size>=min_bag. every>1 → co every-ty
    (rozłożenie po całym oknie capture, nie tylko poranek). Zwraca listę dictów."""
    out = []
    seen = 0
    with open(CAP) as f:
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            bag = d.get("bag") or []
            if len(bag) < min_bag:
                continue
            if not d.get("courier_pos"):
                continue
            seen += 1
            if every > 1 and (seen % every) != 0:
                continue
            out.append(d)
            if len(out) >= target:
                break
    return out, seen


def solve_at(d, budget_ms):
    """Re-solve worek pod dany budżet; zwraca (makespan, strategy, wall_s)."""
    C.V326_OR_TOOLS_TIME_LIMIT_MS = int(budget_ms)
    cp = tuple(d["courier_pos"])
    now = M.pts(d["now"])
    t0 = time.time()
    plan = simulate_bag_route_v2(
        cp, [M.mk(o) for o in d["bag"]], M.mk(d["new_order"]), now=now)
    wall = time.time() - t0
    return makespan(plan), (getattr(plan, "strategy", "") or ""), wall, is_greedy(plan)


def pctl(xs, q):
    xs = sorted(xs)
    if not xs:
        return 0.0
    return xs[min(len(xs) - 1, int(len(xs) * q))]


def significant(gap, ms_lo):
    """Luka istotna gdy >1 min ALBO >5% makespanu (na korzyść 2000ms, gap>0)."""
    if gap is None or ms_lo is None or ms_lo <= 0:
        return False
    return gap > SIG_ABS_MIN or (gap / ms_lo) > SIG_REL


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--sample", type=int, default=150)
    ap.add_argument("--min-bag", type=int, default=3)
    ap.add_argument("--every", type=int, default=7,
                    help="co every-ty worek bag>=min (rozkład po oknie); 1=pierwsze N")
    ap.add_argument("--budget-hi", type=int, default=2000)
    ap.add_argument("--dump", default="")
    args = ap.parse_args()

    worki, seen = select_worki(args.min_bag, args.sample, args.every)

    print("=" * 96)
    print("[#3] LUKA MAKESPAN solver 200ms vs %dms — worki bag>=%d (READ-ONLY)"
          % (args.budget_hi, args.min_bag))
    print("=" * 96)
    print(f"worki bag>=%d napotkane w capture (do limitu próbki): %d" % (args.min_bag, seen))
    print(f"PRÓBKA: {len(worki)} worków (co {args.every}-ty), single-worker")
    print(f"próg istotności: GAP > {SIG_ABS_MIN} min LUB > {SIG_REL*100:.0f}% makespanu",
          flush=True)

    rows = []
    gaps = []
    sig = 0
    greedy_lo = greedy_hi = 0
    greedy_lo_only = 0      # greedy @200 ale OR-Tools @2000 (czas ratuje?)
    by_bag = {}
    t_start = time.time()
    for i, d in enumerate(worki):
        bag_n = len(d["bag"])
        m_lo, s_lo, w_lo, g_lo = solve_at(d, BUDGET_LO)
        m_hi, s_hi, w_hi, g_hi = solve_at(d, args.budget_hi)
        C.V326_OR_TOOLS_TIME_LIMIT_MS = BUDGET_LO  # restore
        gap = (m_lo - m_hi) if (m_lo is not None and m_hi is not None) else None
        if gap is not None:
            gaps.append(gap)
        if significant(gap, m_lo):
            sig += 1
        if g_lo:
            greedy_lo += 1
        if g_hi:
            greedy_hi += 1
        if g_lo and not g_hi:
            greedy_lo_only += 1
        bb = by_bag.setdefault(bag_n, dict(n=0, sig=0, greedy=0, gapsum=0.0))
        bb["n"] += 1
        if significant(gap, m_lo):
            bb["sig"] += 1
        if g_lo:
            bb["greedy"] += 1
        if gap is not None:
            bb["gapsum"] += gap
        rows.append(dict(
            order_id=d.get("order_id"), bag=bag_n,
            makespan_200=round(m_lo, 2) if m_lo is not None else None,
            makespan_hi=round(m_hi, 2) if m_hi is not None else None,
            gap_min=round(gap, 3) if gap is not None else None,
            strat_200=s_lo, strat_hi=s_hi,
            greedy_200=g_lo, greedy_hi=g_hi,
            significant=significant(gap, m_lo)))
        if (i + 1) % 25 == 0:
            print(f"  ... {i+1}/{len(worki)} ({round(time.time()-t_start,0)}s)", flush=True)

    n = len(rows)
    valid = [g for g in gaps if g is not None]
    pos = [g for g in valid if g > 0]
    print("\n" + "#" * 96)
    print("### WYNIK")
    print("#" * 96)
    print(f"worków zbadanych:                 {n}")
    print(f"luka GAP (200−2000) mediana:      {round(pctl(valid,0.5),3)} min")
    print(f"luka GAP średnia:                 {round(sum(valid)/len(valid),3) if valid else 0} min")
    print(f"luka GAP p90:                     {round(pctl(valid,0.9),3)} min")
    print(f"luka GAP max:                     {round(max(valid),3) if valid else 0} min")
    print(f"worki z DODATNIĄ luką (2000 lepszy): {len(pos)}/{n} = {round(100.0*len(pos)/n,1)}%")
    print(f"worki z ISTOTNĄ luką (>1min lub >5%): {sig}/{n} = {round(100.0*sig/n,1)}%")
    print()
    print(f"greedy-fallback @200ms:           {greedy_lo}/{n} = {round(100.0*greedy_lo/n,1)}%")
    print(f"greedy-fallback @{args.budget_hi}ms:          {greedy_hi}/{n} = {round(100.0*greedy_hi/n,1)}%")
    print(f"greedy @200 ale OR-Tools @{args.budget_hi}:   {greedy_lo_only}/{n} "
          f"(czy dłuższy czas RATUJE infeasible? — {'TAK, częściowo' if greedy_lo_only else 'NIE'})")

    print("\nPer bag_size:")
    print(f"{'bag':>5}{'n':>6}{'%istotna_luka':>16}{'%greedy@200':>14}{'śr_gap_min':>12}")
    for b in sorted(by_bag):
        bb = by_bag[b]
        print(f"{b:>5}{bb['n']:>6}"
              f"{round(100.0*bb['sig']/bb['n'],1):>15}%"
              f"{round(100.0*bb['greedy']/bb['n'],1):>13}%"
              f"{round(bb['gapsum']/bb['n'],3):>12}")

    # werdykt
    sig_pct = 100.0 * sig / n if n else 0
    greedy_pct = 100.0 * greedy_lo / n if n else 0
    print("\n" + "=" * 96)
    print("WERDYKT:")
    if sig_pct < 5:
        print(f"  WARM-START / 2000ms = NO-OP. Tylko {round(sig_pct,1)}% worków ma istotną")
        print(f"  lukę makespan — 200ms już praktycznie osiąga to samo optimum dla")
        print(f"  rozmiarów bag>=%d w realnym ruchu. Dłuższy budżet/warm-start NIE warty." % args.min_bag)
    elif sig_pct < 20:
        print(f"  WARM-START marginalny: {round(sig_pct,1)}% worków z istotną luką (ogon).")
        print(f"  Warto rozważyć TYLKO dla największych bagów; dla typowych = no-op.")
    else:
        print(f"  WARM-START WART: {round(sig_pct,1)}% worków zyskuje istotnie na 2000ms.")
        print(f"  Jest realna luka makespan między 200ms a 2000ms.")
    print(f"  INFEASIBLE→greedy: {round(greedy_pct,1)}% worków (osobny problem — "
          f"{'modelowanie ograniczeń/time-windows, NIE czas solvera' if greedy_lo_only == 0 else 'częściowo ratowalny dłuższym czasem'}).")
    print("=" * 96)

    if args.dump:
        with open(args.dump, "w") as fh:
            json.dump(dict(
                n=n, sample=len(worki), seen=seen, every=args.every,
                budget_lo=BUDGET_LO, budget_hi=args.budget_hi,
                gap_median=round(pctl(valid, 0.5), 3),
                gap_mean=round(sum(valid)/len(valid), 3) if valid else 0,
                gap_p90=round(pctl(valid, 0.9), 3),
                gap_max=round(max(valid), 3) if valid else 0,
                pct_positive=round(100.0*len(pos)/n, 1) if n else 0,
                pct_significant=round(sig_pct, 1),
                greedy_lo=greedy_lo, greedy_hi=greedy_hi,
                greedy_lo_only=greedy_lo_only,
                pct_greedy_lo=round(greedy_pct, 1),
                by_bag={str(k): v for k, v in by_bag.items()},
                rows=rows), fh, indent=2, ensure_ascii=False)
        print(f"\ndump -> {args.dump}")


if __name__ == "__main__":
    main()
