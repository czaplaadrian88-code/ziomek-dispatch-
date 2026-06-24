#!/usr/bin/env python3
"""P-3 pomiar (Adrian 2026-06-24): gdy odpala greedy_fallback (OR-Tools INFEASIBLE),
ILE worków greedy (drive-first, ŚLEPY na committed/R6) tworzy NAPRAWIALNE naruszenie
R6 / okna committed, którego R6/committed-aware kolejność by uniknęła = headroom fixu.

READ-ONLY. Reużywa walidowanej infrastruktury infeasible_bags_probe:
  simulate_bag_route_v2 (prod flagi) → is_greedy → greedy_fallback worki;
  build_solver_inputs (nodes/matrix/pairs/tw jak _ortools_plan);
  measure_breaches(inp, seq) → (committed_breach_max, r6_breach_count).

Dla każdego greedy_fallback worka, na TYM SAMYM modelu (measure_breaches), brute po
precedence-valid permutacjach:
  GREEDY-PROXY = argmin makespan (drive-first, window-blind = cel greedy)  [dolny bound szkody]
  R6-AWARE     = argmin (r6_breach, committed_breach, makespan)            [co dałby fix]
FLIP R6        = greedy r6>0 AND aware r6==0  (greedy tworzy unikalny breach → kandydat
                 dostaje verdict NO w feasibility = wypada z puli = możliwy flip kuriera)
FLIP committed = greedy committed>tol AND aware committed<=tol
Uwaga: greedy-proxy = NAJLEPSZY greedy (globalne min-drive) → realny heurystyk greedy ≥ tej
szkody, więc liczby = DOLNY BOUND. "Flip kuriera" wymaga kontekstu decyzji (brak w capture)
→ FLIP R6 jest proxy wpływu na decyzję (R6 to twarda bramka).

Uruchom: /root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.tools.greedy_fallback_flip_probe --scan 6000
"""
import argparse
import itertools
import sys

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from dispatch_v2.tools import infeasible_bags_probe as IP  # noqa: E402

R6 = IP.R6_MAX_MIN          # 35
TOL = IP.R27_TOL_MIN        # 5
MAX_BRUTE_NODES = 8         # N-1 (po courierze); >8 → pomiń (cap, zliczamy)


def _prec_ok(perm, pairs):
    pos = {nd: i for i, nd in enumerate(perm)}
    return all(pos[p] < pos[d] for p, d in pairs)


def best_orders(inp):
    """Brute po perm node'ów 1..N-1 (0=courier fixed). Zwraca (greedy_proxy, aware) jako
    dict(committed, r6, makespan) albo None gdy za duże/brak feasible."""
    n = inp["N"]
    movable = list(range(1, n))
    if len(movable) > MAX_BRUTE_NODES:
        return None
    pairs = inp["pairs"]
    greedy = None   # (makespan, committed, r6)
    aware = None    # (r6, committed, makespan)
    for perm in itertools.permutations(movable):
        if not _prec_ok(perm, pairs):
            continue
        seq = list(perm)
        committed, r6 = IP.measure_breaches(inp, seq)
        arr = IP._walk_cumul(inp, seq)
        mk = max(arr.values()) if arr else 0.0
        gk = (round(mk, 1), r6, round(committed, 1))
        ak = (r6, round(committed, 1), round(mk, 1))
        if greedy is None or gk < greedy[0]:
            greedy = (gk, dict(committed=committed, r6=r6, makespan=mk))
        if aware is None or ak < aware[0]:
            aware = (ak, dict(committed=committed, r6=r6, makespan=mk))
    if greedy is None or aware is None:
        return None
    return greedy[1], aware[1]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--scan", type=int, default=6000)
    ap.add_argument("--min-bag", type=int, default=2)
    args = ap.parse_args()

    worki = IP.select_worki(args.scan, args.min_bag)
    # ETAP A: greedy_fallback (prod simulate)
    gf = []
    for d in worki:
        cp = tuple(d["courier_pos"])
        now = IP.M.pts(d["now"])
        try:
            plan = IP.simulate_bag_route_v2(
                cp, [IP.M.mk(o) for o in d["bag"]], IP.M.mk(d["new_order"]), now=now)
        except Exception:
            continue
        if IP.is_greedy(plan):
            gf.append((d, now, cp))
    n_gf = len(gf)
    print("=" * 88)
    print(f"przeskanowano worków bag>={args.min_bag}: {len(worki)} | "
          f"greedy_fallback (INFEASIBLE): {n_gf} ({100.0*n_gf/max(1,len(worki)):.1f}%)")
    print("=" * 88)
    if not n_gf:
        print("brak greedy_fallback w próbce — zwiększ --scan"); return

    evald = too_big = 0
    flip_r6 = flip_committed = 0
    greedy_r6_any = aware_r6_any = 0
    sum_committed_saved = 0.0
    examples = []
    for (d, now, cp) in gf:
        inp = IP.build_solver_inputs([], [], None, now) if False else \
            IP.build_solver_inputs(cp, [IP.M.mk(o) for o in d["bag"]], IP.M.mk(d["new_order"]), now)
        bo = best_orders(inp)
        if bo is None:
            too_big += 1
            continue
        greedy, aware = bo
        evald += 1
        if greedy["r6"] > 0:
            greedy_r6_any += 1
        if aware["r6"] > 0:
            aware_r6_any += 1
        if greedy["r6"] > aware["r6"] and aware["r6"] == 0:
            flip_r6 += 1
            if len(examples) < 12:
                examples.append((d.get("order_id"), len(d["bag"]),
                                 greedy["r6"], aware["r6"],
                                 round(greedy["committed"], 1), round(aware["committed"], 1)))
        if greedy["committed"] > TOL and aware["committed"] <= TOL:
            flip_committed += 1
        if greedy["committed"] > aware["committed"]:
            sum_committed_saved += (greedy["committed"] - aware["committed"])

    print(f"ocenione (brute ≤{MAX_BRUTE_NODES} węzłów): {evald} | pominięte za duże: {too_big}")
    print(f"\n--- R6 (twarda bramka 35 min) ---")
    print(f"  greedy-proxy tworzy ≥1 R6-breach:  {greedy_r6_any}/{evald} "
          f"({100.0*greedy_r6_any/max(1,evald):.1f}%)")
    print(f"  R6-aware tworzy ≥1 R6-breach:      {aware_r6_any}/{evald} "
          f"({100.0*aware_r6_any/max(1,evald):.1f}%)")
    print(f"  ⭐ FLIP R6 (greedy breach → aware 0): {flip_r6}/{evald} "
          f"({100.0*flip_r6/max(1,evald):.1f}%)  ← kandydat wypadałby z puli niepotrzebnie")
    print(f"\n--- OKNO committed (R-DECLARED-TIME ±{TOL:.0f}) ---")
    print(f"  ⭐ FLIP committed (greedy >tol → aware ≤tol): {flip_committed}/{evald} "
          f"({100.0*flip_committed/max(1,evald):.1f}%)")
    print(f"  Σ committed-breach do odzyskania (greedy−aware): {sum_committed_saved:.0f} min")
    print("\n--- przykłady FLIP R6 (greedy tworzy unikalny breach) ---")
    for oid, bag, gr6, ar6, gc, ac in examples:
        print(f"  oid={oid} bag={bag}  R6-breach greedy={gr6}→aware={ar6}  committed greedy={gc}→aware={ac}min")
    print("=" * 88)
    print("WERDYKT: FLIP R6 > 0 = greedy realnie wypycha kandydatów z puli feasible (możliwy")
    print("flip kuriera); FLIP committed = okno do naprawy (część gasi P-1 w kanonie). FLIP≈0 =")
    print("greedy już ~optymalny na twardych regułach → fix kosmetyczny.")


if __name__ == "__main__":
    main()
