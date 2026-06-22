#!/usr/bin/env python3
"""Monitor shadowowych „później" — propozycji przesuniętego czasu odbioru Ziomka.

Adrian 2026-06-22: po decyzji że tie-breaker (bez regresji dostaw) + shadow „później"
to właściwy model (eskalacja = no-op), monitorujemy JAK CZĘSTO Ziomek faktycznie
składa nową, późniejszą obietnicę na żywych danych.

Źródło: scripts/logs/shadow_decisions.jsonl (append-only, serializowane przez
shadow_dispatcher._serialize_result).

Rodzina „później":
  pickup_extension_redirect  — tier 1 (przesunięty odbiór w tolerancji) / tier 2
                               (committed NARUSZONY: obietnica restauracji nie do
                               dotrzymania → realny późniejszy czas). PROPOSE→Telegram.
  commit_divergence_redirect — plan vs commit > próg → KOORD (shadow-only).
  best_effort_r6_redirect    — 0 feasible, kompromis SLA.
  difficult_case_redirect    — drop max score < floor → KOORD.

Użycie: python3 -m dispatch_v2.tools.monitor_later_promises [--since YYYY-MM-DD] [--log PATH]
Read-only. Bezpieczny do crona/timera.
"""
import sys, json, argparse
from collections import defaultdict, Counter

DEFAULT_LOG = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
REDIR = ["pickup_extension_redirect", "commit_divergence_redirect",
         "best_effort_r6_redirect", "difficult_case_redirect"]


def _stats(xs):
    if not xs:
        return (0, 0, 0)
    xs = sorted(xs)
    n = len(xs)
    med = xs[n // 2] if n % 2 else (xs[n // 2 - 1] + xs[n // 2]) / 2
    p90 = xs[min(n - 1, int(0.9 * n))]
    return (med, p90, xs[-1])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None, help="YYYY-MM-DD (włącznie)")
    ap.add_argument("--log", default=DEFAULT_LOG)
    args = ap.parse_args()

    per_day = defaultdict(lambda: {"dec": 0, "later": 0, "t1": 0, "t2": 0})
    redir_cnt = Counter()
    late_min = []          # new_pickup_late_min (tier 1+2)
    breach_min = []        # committed_breach_min (tier 2 — realne złamanie obietnicy)
    by_rest = Counter()    # restauracje z later-promise
    by_cid = Counter()
    t2_rest = Counter()    # restauracje z committed-breach (tier 2)
    by_hour = Counter()
    total = 0; parsed = 0; dmin = None; dmax = None

    for line in open(args.log, encoding="utf-8", errors="ignore"):
        line = line.strip()
        if not line:
            continue
        total += 1
        try:
            d = json.loads(line)
        except Exception:
            continue
        ts = d.get("ts") or ""
        day = ts[:10]
        if args.since and day and day < args.since:
            continue
        parsed += 1
        if day:
            dmin = day if dmin is None else min(dmin, day)
            dmax = day if dmax is None else max(dmax, day)
            per_day[day]["dec"] += 1
        for k in REDIR:
            if d.get(k):
                redir_cnt[k] += 1
        pe = d.get("pickup_extension_redirect")
        if pe:
            tier = pe.get("tier")
            if day:
                per_day[day]["later"] += 1
                per_day[day]["t1" if tier == 1 else "t2"] += 1
            lm = pe.get("new_pickup_late_min")
            if isinstance(lm, (int, float)):
                late_min.append(lm)
            by_rest[d.get("restaurant", "?")] += 1
            by_cid[pe.get("courier_id", "?")] += 1
            if ts[11:13].isdigit():
                by_hour[ts[11:13]] += 1
            if tier == 2:
                bm = pe.get("committed_breach_min")
                if isinstance(bm, (int, float)):
                    breach_min.append(bm)
                wr = pe.get("committed_worst_restaurant") or d.get("restaurant", "?")
                t2_rest[wr] += 1

    print("=" * 64)
    print("MONITOR 'PÓŹNIEJ' (shadow pickup_extension_redirect)")
    print(f"log: {args.log}")
    print(f"okno: {dmin} … {dmax} | rekordów sparsowanych: {parsed}/{total}")
    print("=" * 64)
    tot_dec = sum(v["dec"] for v in per_day.values())
    tot_later = sum(v["later"] for v in per_day.values())
    print(f"\nDECYZJE z propozycją PÓŹNIEJSZEGO odbioru: {tot_later}/{tot_dec}"
          f" = {100*tot_later/max(tot_dec,1):.1f}%")
    print(f"  tier 1 (przesunięty w tolerancji): {sum(v['t1'] for v in per_day.values())}")
    print(f"  tier 2 (OBIETNICA restauracji NARUSZONA → nowy czas): {sum(v['t2'] for v in per_day.values())}")

    med, p90, mx = _stats(late_min)
    print(f"\nO ILE PÓŹNIEJ (new_pickup_late_min, tier 1+2): "
          f"mediana {med:.1f} | p90 {p90:.1f} | max {mx:.1f} min")
    if breach_min:
        med2, p902, mx2 = _stats(breach_min)
        print(f"NARUSZENIE committed (tier 2, ile ponad ±5): "
              f"mediana {med2:.1f} | p90 {p902:.1f} | max {mx2:.1f} min  (n={len(breach_min)})")

    print(f"\nPER DZIEŃ (decyzje / później / %):")
    for day in sorted(per_day):
        v = per_day[day]
        print(f"  {day}: {v['dec']:4d} dec | {v['later']:3d} później "
              f"({100*v['later']/max(v['dec'],1):4.1f}%) | t1={v['t1']} t2={v['t2']}")

    print("\nTOP restauracje z 'później': "
          + ", ".join(f"{r}({n})" for r, n in by_rest.most_common(6)))
    if t2_rest:
        print(f"TOP restauracje z NARUSZONĄ obietnicą (tier 2): "
              + ", ".join(f"{r}({n})" for r, n in t2_rest.most_common(6)))
    print(f"TOP kurierzy: " + ", ".join(f"{c}({n})" for c, n in by_cid.most_common(6)))
    if by_hour:
        print(f"Rozkład godzinowy (UTC): "
              + " ".join(f"{h}:{n}" for h, n in sorted(by_hour.items())))

    other = {k: redir_cnt[k] for k in REDIR if k != "pickup_extension_redirect"}
    print(f"\nPozostałe redirecty (KOORD-shadow): {other}")


if __name__ == "__main__":
    main()
