#!/usr/bin/env python3
"""
[D2] Post-analiza dumpu z coeff_gridsearch (READ-ONLY). 2026-06-20.

Czyta JSON dump (--dump z coeff_gridsearch) i wypisuje:
  - tabelę G1/G2/G3 + przyrosty marginalne (ΔG1 / Δregr między sąsiednimi coeff)
  - front Pareto + kolano (re-liczone z tej samej czystej logiki)
  - interpretację „opłacalności" wzrostu coeff (zysk na minutę regresji)

Nie dotyka silnika ani danych live — tylko czyta artefakt dumpu.
"""
import argparse
import json
import sys

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from dispatch_v2.tools.coeff_gridsearch import pareto_front, knee_point  # noqa: E402


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump")
    args = ap.parse_args()
    with open(args.dump) as fh:
        data = json.load(fh)
    rows = data["rows"]
    rows = sorted(rows, key=lambda r: r["coeff"])

    print("=" * 92)
    print("[D2] POST-ANALIZA: marginalna opłacalność coeff (ΔG1 na 1 min Δregresji)")
    print("=" * 92)
    print(f"{'coeff':>6}{'G1_net':>10}{'regr_sum':>10}{'regr#':>7}"
          f"{'ΔG1':>10}{'Δregr':>9}{'ΔG1/Δregr':>11}{'G3':>5}")
    prev = None
    for r in rows:
        if prev is None:
            print(f"{r['coeff']:>6}{r['g1']:>10}{r['regr_sum']:>10}{r['regr']:>7}"
                  f"{'—':>10}{'—':>9}{'—':>11}{r['g3']:>5}")
        else:
            dg1 = round(r["g1"] - prev["g1"], 1)
            dre = round(r["regr_sum"] - prev["regr_sum"], 1)
            ratio = round(dg1 / dre, 2) if abs(dre) > 1e-9 else float("inf")
            print(f"{r['coeff']:>6}{r['g1']:>10}{r['regr_sum']:>10}{r['regr']:>7}"
                  f"{dg1:>10}{dre:>9}{str(ratio):>11}{r['g3']:>5}")
        prev = r

    knee, front = knee_point(rows)
    print("\nFRONT PARETO (G1↑ vs regr_sum↓):")
    for p in front:
        mk = "  <== KOLANO" if knee and p["coeff"] == knee["coeff"] else ""
        print(f"  coeff={p['coeff']:>4}  G1={p['g1']:>9}  regr_sum={p['regr_sum']:>8}  G3={p['g3']}{mk}")

    # best G1 absolutnie + interpretacja
    best_g1 = max(rows, key=lambda r: r["g1"])
    print(f"\nMaks G1 absolutnie: coeff={best_g1['coeff']} (G1={best_g1['g1']}, "
          f"regr_sum={best_g1['regr_sum']}, G3={best_g1['g3']})")
    if knee:
        print(f"Kolano Pareto:      coeff={knee['coeff']} (G1={knee['g1']}, "
              f"regr_sum={knee['regr_sum']}, G3={knee['g3']})")
        share = round(100.0 * knee["g1"] / best_g1["g1"], 1) if best_g1["g1"] else 0.0
        print(f"  → kolano realizuje {share}% maks. G1 przy regr_sum "
              f"{knee['regr_sum']} vs {best_g1['regr_sum']}")


if __name__ == "__main__":
    main()
