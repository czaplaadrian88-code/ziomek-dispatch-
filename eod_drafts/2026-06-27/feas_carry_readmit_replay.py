#!/usr/bin/env python3
"""ETAP 5 replay ON↔OFF dla B2 / #483000 (feas-carry-readmit) — dowód pozytywnego wpływu.

Źródło: dispatch_state/feas_carry_blind_shadow.jsonl (shadow LIVE od ~24.06). Każdy rekord
to JEDNA decyzja w której zwycięzca (chosen) niósł WYBACZONY breach (asymetria bramki #483000),
z policzonym carry-inclusive porównaniem do odrzuconych (NO blocking sla/r6):
  would_redirect / redirect_objm (worst breach re-admita) / regret_min (= chosen_objm−rej_objm)
  / redirect_kind / redirect_over_by / marginal (over_by≤5).

OFF = dzisiejsza selekcja (chosen zostaje). ON = re-admit gdy carry-inclusive lepszy ORAZ
nowy order ≤ cap (Tier-3, 40). Cap na NEWBAG nie jest w logu → projekcja KONSERWATYWNA:
  redirect_live ⟺ would_redirect AND redirect_objm ≤ CAP   (worst≤40 ⟹ new≤40; lower-bound).
Górna granica = wszystkie would_redirect (55%). Realny LIVE leży między (gdy carried jest
breacherem, new≤40 a worst>40 → moje LIVE bierze, projekcja nie → projekcja UNDER-liczy zysk).

Metryki werdyktu (Adrian: „pewność progresu → flaga ON"):
  - redirect_rate_live vs all decyzji → materialność (≥20%?).
  - benefit = redukcja worst-breach floty = regret_min na redirectach (median/sum/dni).
  - koszt nowego ordera = ≤ (CAP−35) min twardo (cap-40 ⟹ ≤5 min ponad R6).
  - Pareto: każdy redirect ma objm(re-admit) < objm(chosen) z definicji → worst-breach
    ŚCIŚLE maleje (nigdy nie rośnie) = brak regresji floty na osi R6-worst.
Użycie: python3 -m dispatch_v2.eod_drafts.2026-06-27.feas_carry_readmit_replay [--cap 40] [--since 2026-06-26]
"""
import argparse
import json
import sys
from collections import defaultdict

LOG = "/root/.openclaw/workspace/dispatch_state/feas_carry_blind_shadow.jsonl"


def _day(ts):
    return (ts or "")[:10]


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cap", type=float, default=40.0)
    ap.add_argument("--since", default=None, help="YYYY-MM-DD włącznie")
    args = ap.parse_args()

    rows = []
    with open(LOG, encoding="utf-8", errors="replace") as fh:
        for ln in fh:
            ln = ln.strip()
            if not ln:
                continue
            try:
                r = json.loads(ln)
            except Exception:
                continue
            if args.since and _day(r.get("ts")) < args.since:
                continue
            rows.append(r)

    n = len(rows)
    if not n:
        print("brak danych w oknie")
        return 1

    wr = [r for r in rows if r.get("would_redirect")]
    # LIVE konserwatywny: would_redirect AND redirect_objm ≤ cap (worst≤cap ⟹ new≤cap).
    live = [r for r in wr
            if isinstance(r.get("redirect_objm"), (int, float)) and r["redirect_objm"] <= args.cap]
    # alternatywne pasmo: r6_new + marginal (new=worst, over_by≤5 ⟺ new≤40) — sanity cross-check
    live_marginal = [r for r in wr if r.get("marginal")]

    def _med(xs):
        xs = sorted(x for x in xs if isinstance(x, (int, float)))
        if not xs:
            return None
        m = len(xs) // 2
        return xs[m] if len(xs) % 2 else round((xs[m - 1] + xs[m]) / 2, 1)

    regrets = [r.get("regret_min") for r in live if isinstance(r.get("regret_min"), (int, float))]
    regrets_all = [r.get("regret_min") for r in wr if isinstance(r.get("regret_min"), (int, float))]

    # per-dzień
    per_day = defaultdict(lambda: [0, 0, 0])  # day -> [decyzje, would_redirect, live]
    for r in rows:
        d = _day(r.get("ts"))
        per_day[d][0] += 1
    for r in wr:
        per_day[_day(r.get("ts"))][1] += 1
    for r in live:
        per_day[_day(r.get("ts"))][2] += 1

    # rozkład kind na LIVE
    kinds = defaultdict(int)
    for r in live:
        kinds[r.get("redirect_kind") or "?"] += 1

    print(f"=== B2 feas-carry-readmit REPLAY (cap={args.cap}, since={args.since or 'all'}) ===")
    print(f"decyzji (chosen niósł forgiven breach): {n}")
    print(f"would_redirect (OFF→ON bez capa):       {len(wr)} ({100*len(wr)/n:.1f}%)")
    print(f"LIVE redirect (konserwatywny cap):      {len(live)} ({100*len(live)/n:.1f}%)")
    print(f"  (cross-check r6_new+marginal:         {len(live_marginal)})")
    print(f"redirect_kind na LIVE:                  {dict(kinds)}")
    print()
    print(f"BENEFIT (redukcja worst-breach floty = regret_min):")
    print(f"  LIVE: median={_med(regrets)}min  sum={round(sum(regrets),1)}min  n={len(regrets)}")
    print(f"  (all would_redirect: median={_med(regrets_all)}min sum={round(sum(regrets_all),1)}min)")
    print(f"KOSZT nowego ordera: ≤ {args.cap-35:.0f} min ponad R6=35 (cap-{args.cap:.0f} Tier-3, twardo)")
    print()
    print("per-dzień (decyzje / would_redirect / LIVE-cap):")
    for d in sorted(per_day):
        a, b, c = per_day[d]
        print(f"  {d}:  {a:4d} / {b:4d} / {c:4d}")
    print()

    # WERDYKT
    rate_live = 100 * len(live) / n
    materiality_ok = rate_live >= 20.0
    benefit_ok = (_med(regrets) or 0) >= 1.5 and len(regrets) >= 20
    pareto_ok = all(
        isinstance(r.get("redirect_objm"), (int, float))
        and isinstance(r.get("chosen_forgiven_breach"), (int, float))
        and r["redirect_objm"] < r["chosen_forgiven_breach"]
        for r in live
    )
    print("WERDYKT:")
    print(f"  materialność (LIVE≥20%):           {'✅' if materiality_ok else '❌'} {rate_live:.1f}%")
    print(f"  benefit (regret med≥1.5, n≥20):    {'✅' if benefit_ok else '❌'} med={_med(regrets)} n={len(regrets)}")
    print(f"  Pareto worst-breach (ON≤OFF zawsze):{'✅' if pareto_ok else '❌'} (każdy redirect: re-admit objm<chosen)")
    go = materiality_ok and benefit_ok and pareto_ok
    print()
    print(f"  → {'✅ GO: pozytywny wpływ udowodniony (flip ON za ACK)' if go else '⏸ WAIT: próg niespełniony'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
