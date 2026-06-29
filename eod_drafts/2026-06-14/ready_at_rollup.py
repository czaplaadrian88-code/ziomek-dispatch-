#!/usr/bin/env python3
"""Rollup ready_at_log.jsonl → prep-bias per restauracja (Fala 1).

Czyta dispatch_state/ready_at_log.jsonl, agreguje prep_bias_min per restauracja:
median / p90 / n / % z sygnałem przyjazdu (status4). Wskazuje „spóźnialskich"
(restauracje gdzie jedzenie systematycznie późni vs deklaracja) — wejście do
rekalibracji pokazywanego ETA i prep-bias table.
"""
import json
import statistics as st
from collections import defaultdict

F = "/root/.openclaw/workspace/dispatch_state/ready_at_log.jsonl"


def main():
    rows = []
    with open(F) as f:
        for line in f:
            line = line.strip()
            if line:
                try:
                    rows.append(json.loads(line))
                except Exception:
                    pass
    if not rows:
        print("brak rekordów ready_at jeszcze")
        return

    by_rest = defaultdict(list)
    for r in rows:
        pb = r.get("prep_bias_min")
        if isinstance(pb, (int, float)):
            by_rest[r.get("restaurant") or "?"].append((pb, r.get("ready_basis")))

    def p90(xs):
        xs = sorted(xs)
        return round(xs[min(len(xs) - 1, int(0.9 * len(xs)))], 1)

    rolls = []
    for rest, items in by_rest.items():
        pbs = [x[0] for x in items]
        waited = sum(1 for x in items if x[1] == "waited")
        rolls.append({
            "restaurant": rest, "n": len(pbs),
            "median_prep_bias": round(st.median(pbs), 1),
            "p90_prep_bias": p90(pbs),
            "max_prep_bias": round(max(pbs), 1),
            "pct_waited": round(100 * waited / len(pbs)),
        })
    rolls.sort(key=lambda x: -x["median_prep_bias"])

    print(f"=== READY_AT ROLLUP — prep-bias per restauracja (n={len(rows)} odbiorów, "
          f"{len(rolls)} restauracji) ===")
    print(f"{'restauracja':24} {'n':>3} {'med':>6} {'p90':>6} {'max':>6} {'%czekał':>7}")
    for r in rolls:
        print(f"{str(r['restaurant'])[:24]:24} {r['n']:>3} "
              f"{r['median_prep_bias']:>6} {r['p90_prep_bias']:>6} "
              f"{r['max_prep_bias']:>6} {r['pct_waited']:>6}%")
    allpb = [x[0] for items in by_rest.values() for x in items]
    print(f"\nGLOBALNIE: median prep_bias = {round(st.median(allpb),1)} min, "
          f"p90 = {p90(allpb)} min, n = {len(allpb)}")
    print("(prep_bias = picked − declared; dodatnie = jedzenie późni vs deklaracja)")


if __name__ == "__main__":
    main()
