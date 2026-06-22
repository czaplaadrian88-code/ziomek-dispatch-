#!/usr/bin/env python3
"""gps_coverage_diagnostic.py — READ-ONLY per-courier GPS coverage + no_gps->KOORD funnel trend.

ZERO writes, ZERO flips. Complements tools/no_gps_who.py (which does the KOORD attribution): this
gives the full per-courier COVERAGE table (who never sends GPS) + the funnel TREND over time (is the
no_gps->KOORD problem growing or shrinking, esp. relative to the B3 trial live since 2026-06-20).

pos_source per candidate in shadow_decisions tells whether Ziomek had a real GPS fix for that courier
or fell back to a fiction (no_gps -> BIALYSTOK_CENTER) / pre_shift / stale anchor.

Run: cd /root/.openclaw/workspace/scripts && PYTHONPATH=. \
     /root/.openclaw/venvs/dispatch/bin/python dispatch_v2/eod_drafts/2026-06-21/gps_coverage_diagnostic.py
"""
import json
from collections import defaultdict, Counter

SHADOW = ["/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1",
          "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"]
BLIND = ("no_gps", "pre_shift", "none")
INFORMED = ("gps", "last_assigned_pickup", "last_picked_up_pickup", "last_picked_up_delivery",
            "last_picked_up_recent", "last_picked_up_interp", "last_delivered", "post_wave")


def main():
    appear = Counter()
    blind = Counter()
    gps_real = Counter()       # actual live GPS fix (pos_source == 'gps')
    name = {}
    byd = defaultdict(lambda: {"n": 0, "koord": 0, "koord_nogps_proxy": 0})

    for fn in SHADOW:
        try:
            fh = open(fn)
        except OSError:
            continue
        for line in fh:
            try:
                d = json.loads(line)
            except ValueError:
                continue
            ts = (d.get("ts") or d.get("shadow_ts") or "")[:10]
            if ts:
                r = byd[ts]; r["n"] += 1
                if d.get("verdict") == "KOORD":
                    r["koord"] += 1
                    # proxy: a blind+empty candidate that was score-OK (would've been proposable) got demoted
                    cands = ([d["best"]] if d.get("best") else []) + (d.get("alternatives") or [])
                    if "all_candidates_low_score" in (d.get("reason") or "") and any(
                        c.get("pos_source") in BLIND and (c.get("r6_bag_size") in (0, None))
                        and (c.get("score") or -1e9) >= -100 for c in cands):
                        r["koord_nogps_proxy"] += 1
            cands = ([d["best"]] if d.get("best") else []) + (d.get("alternatives") or [])
            for c in cands:
                ps, cid = c.get("pos_source"), c.get("courier_id")
                if ps is None or cid is None:
                    continue
                appear[cid] += 1
                if c.get("name"):
                    name[cid] = c["name"]
                if ps in BLIND:
                    blind[cid] += 1
                if ps == "gps":
                    gps_real[cid] += 1

    tot = sum(appear.values()); tb = sum(blind.values())
    print(f"=== (A) FLEET GPS COVERAGE ===  blind(no_gps/pre_shift/none) = {tb}/{tot} = {100*tb/tot:.0f}% of candidate appearances")
    print(f"     real-GPS fixes = {sum(gps_real.values())}/{tot} = {100*sum(gps_real.values())/tot:.0f}%\n")

    print("=== (B) PER-COURIER COVERAGE (sorted by blind volume; CHRONIC = >=90% blind) ===")
    print(f"  {'cid':>5} {'name':<20} {'blind':>6} {'real-gps':>8} {'total':>6} {'blind%':>7}  class")
    for cid, b in blind.most_common(15):
        a = appear[cid]
        rate = 100 * b / a
        cls = "CHRONIC (app never sends GPS)" if rate >= 90 else ("FREQUENT" if rate >= 45 else "sporadic")
        print(f"  {cid:>5} {name.get(cid,'?'):<20} {b:>6} {gps_real[cid]:>8} {a:>6} {rate:>6.0f}%  {cls}")
    print()

    print("=== (C) no_gps->KOORD FUNNEL TREND (per date; proxy mirrors tools/no_gps_who.py logic) ===")
    print(f"  {'date':<12} {'decisions':>9} {'KOORD_all':>10} {'KOORD_nogps':>12}")
    for ts in sorted(byd):
        r = byd[ts]
        print(f"  {ts:<12} {r['n']:>9} {r['koord']:>10} {r['koord_nogps_proxy']:>12}")
    print("\n(Authoritative KOORD attribution = tools/no_gps_who.py: 75 no_gps-blocked KOORD, 7 couriers,\n"
          " cid=518 Michal Rogucki = 57%. This script adds the coverage table + the trend.)")


if __name__ == "__main__":
    main()
