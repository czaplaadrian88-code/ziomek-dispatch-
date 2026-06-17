#!/usr/bin/env python3
"""SHORTAGE REGIME isolation + outcome contrast (backfill, READ-ONLY, stdlib).
Defines the niedobor (scarcity) subset by pool_feasible and contrasts it with the
normal regime on: selection slack (score_margin), KOORD/best_effort rate, predicted &
ACTUAL R6 breach, pickup/delivery latency. Excludes contaminated windows.
Central question: at shortage, is there anything to SELECT among (score_margin) or is the
lever defer/extend + fleet?  Warsaw TZ for daypart."""
import json, collections, statistics as st
from datetime import datetime, timezone, timedelta

BACKFILL = "/root/.openclaw/workspace/dispatch_state/backfill_decisions_outcomes_v1.jsonl"
WARSAW = timezone(timedelta(hours=2))
# contaminated (UTC) — from score_axis_double_count_audit.py
CONT = [(datetime(2026, 6, 6, 17, 53, tzinfo=timezone.utc), datetime(2026, 6, 10, 18, 24, tzinfo=timezone.utc)),
        (datetime(2026, 6, 11, 14, 28, tzinfo=timezone.utc), datetime(2026, 6, 12, 18, 32, tzinfo=timezone.utc))]


def pts(s):
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def cont(t):
    return t is None or any(lo <= t <= hi for lo, hi in CONT)


def num(d, k):
    v = d.get(k)
    try:
        return float(v) if v is not None else None
    except Exception:
        return None


def bucket(pf):
    if pf is None:
        return "?"
    if pf == 0:
        return "0_collapse"
    if pf <= 2:
        return "1-2_scarcity"
    if pf <= 4:
        return "3-4_tight"
    return "5+_normal"


ORDER = ["0_collapse", "1-2_scarcity", "3-4_tight", "5+_normal"]


def agg():
    return {"n": 0, "koord": 0, "besteff": 0, "czas": 0, "margin": [], "score": [],
            "pred_r6": [], "pred_breach": 0, "pred_n": 0,
            "out_p2d": [], "out_breach": 0, "out_n": 0, "out_a2p": [], "deliv": 0,
            "tiers": collections.Counter(), "hours": collections.Counter()}


def main():
    B = {b: agg() for b in ORDER}
    n_tot = n_clean = 0
    for line in open(BACKFILL, "rb"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        n_tot += 1
        t = pts(d.get("decision_ts") or d.get("ts"))
        if cont(t):
            continue
        n_clean += 1
        pf = d.get("pool_feasible")
        b = bucket(pf)
        if b not in B:
            continue
        a = B[b]
        a["n"] += 1
        if d.get("verdict") == "KOORD":
            a["koord"] += 1
        if d.get("best_effort"):
            a["besteff"] += 1
        if d.get("czasowka"):
            a["czas"] += 1
        m = num(d, "score_margin")
        if m is not None:
            a["margin"].append(m)
        s = num(d, "proposed_score")
        if s is not None:
            a["score"].append(s)
        pr = num(d, "predicted_r6_max_bag_min")
        if pr is not None:
            a["pred_r6"].append(pr)
            a["pred_n"] += 1
            if pr > 35.0:
                a["pred_breach"] += 1
        if d.get("tier"):
            a["tiers"][d["tier"]] += 1
        if t:
            a["hours"][t.astimezone(WARSAW).hour] += 1
        o = d.get("outcome") or {}
        p2d = o.get("pickup_to_delivery_min")
        if isinstance(p2d, (int, float)):
            a["out_p2d"].append(p2d)
            a["out_n"] += 1
            if p2d > 35.0:
                a["out_breach"] += 1
        a2p = o.get("assign_to_pickup_min")
        if isinstance(a2p, (int, float)):
            a["out_a2p"].append(a2p)
        if (o.get("status") or "") == "delivered":
            a["deliv"] += 1

    def med(v):
        return round(st.median(v), 1) if v else None

    def mean(v):
        return round(st.mean(v), 1) if v else None

    print(f"backfill rows={n_tot} clean(excl contaminated)={n_clean}\n")
    print("REGIME BUCKETS by pool_feasible (AUTO_ASSIGN_MIN_POOL_FEASIBLE=3 → <3 = scarcity)")
    print(f"{'bucket':14s} {'n':>5} {'%dec':>5} {'KOORD%':>7} {'bestEff%':>8} {'czas%':>6} "
          f"{'margin_p50':>10} {'margin_p90':>10} {'score_p50':>9}")
    tot = sum(B[b]["n"] for b in ORDER)
    for b in ORDER:
        a = B[b]
        n = a["n"] or 1
        mg = sorted(a["margin"])
        mp50 = med(a["margin"])
        mp90 = round(mg[min(len(mg) - 1, int(len(mg) * 0.9))], 1) if mg else None
        print(f"{b:14s} {a['n']:>5} {100*a['n']/max(1,tot):>4.1f}% {100*a['koord']/n:>6.1f}% "
              f"{100*a['besteff']/n:>7.1f}% {100*a['czas']/n:>5.1f}% {str(mp50):>10} {str(mp90):>10} {str(med(a['score'])):>9}")

    print("\nPREDICTED vs ACTUAL R6 (delivery) + latency, per bucket")
    print(f"{'bucket':14s} {'predR6_p50':>10} {'pred_brch%':>10} {'OUTp2d_p50':>10} {'out_brch%':>9} "
          f"{'out_n':>6} {'a2pickup_p50':>12} {'deliv%':>7}")
    for b in ORDER:
        a = B[b]
        pn = a["pred_n"] or 1
        on = a["out_n"] or 1
        print(f"{b:14s} {str(med(a['pred_r6'])):>10} {100*a['pred_breach']/pn:>9.1f}% "
              f"{str(med(a['out_p2d'])):>10} {100*a['out_breach']/on:>8.1f}% {a['out_n']:>6} "
              f"{str(med(a['out_a2p'])):>12} {100*a['deliv']/max(1,a['n']):>6.1f}%")

    print("\nTIER MIX per bucket (%):")
    for b in ORDER:
        a = B[b]
        n = sum(a["tiers"].values()) or 1
        mix = {t: f"{100*c/n:.0f}%" for t, c in a["tiers"].most_common()}
        print(f"  {b:14s} {mix}")

    print("\nDAYPART (Warsaw hour) — share of scarcity (0_collapse + 1-2) vs all decisions:")
    allh = collections.Counter()
    sch = collections.Counter()
    for b in ORDER:
        for h, c in B[b]["hours"].items():
            allh[h] += c
            if b in ("0_collapse", "1-2_scarcity"):
                sch[h] += c
    for h in sorted(allh):
        tot_h = allh[h]
        sc_h = sch.get(h, 0)
        bar = "#" * int(40 * sc_h / max(1, tot_h))
        print(f"  {h:02d}:00  scarcity {sc_h:>4}/{tot_h:<4} ({100*sc_h/max(1,tot_h):>4.0f}%) {bar}")

    # margin==0 share = "no real choice" proxy
    print("\nSELECTION SLACK — share of decisions with score_margin <= 1.0 (≈ no real alternative):")
    for b in ORDER:
        a = B[b]
        z = sum(1 for m in a["margin"] if m <= 1.0)
        print(f"  {b:14s} {z}/{len(a['margin'])} = {100*z/max(1,len(a['margin'])):.1f}%  "
              f"(margin>10: {sum(1 for m in a['margin'] if m>10)}/{len(a['margin'])})")


if __name__ == "__main__":
    main()
