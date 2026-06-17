#!/usr/bin/env python3
"""Z3 SCORE ATTRIBUTION on the SHORTAGE subset vs normal (READ-ONLY, stdlib).
For each PROPOSE decision (clean window), within best's (tier,bucket) eligible group:
 (1) BEST-candidate term magnitudes per pool_feasible bucket (mean/|mean|/%active/std)
 (2) ARGMAX-IMPORTANCE: for each additive term, how often does removing it from the score
     flip the in-group winner (additive-removal sensitivity) — the real "does it change the decision".
 (3) DEAD axes: ~0 variance / never pivotal.
 (4) DOUBLE-COUNT: Pearson corr between the wait penalties + R6 terms.
Excludes contaminated windows. Buckets by top-level pool_feasible_count."""
import json, collections, statistics as st
from datetime import datetime, timezone

SHADOW = ["/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1",
          "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"]
CONT = [(datetime(2026, 6, 6, 17, 53, tzinfo=timezone.utc), datetime(2026, 6, 10, 18, 24, tzinfo=timezone.utc)),
        (datetime(2026, 6, 11, 14, 28, tzinfo=timezone.utc), datetime(2026, 6, 12, 18, 32, tzinfo=timezone.utc))]

# additive score terms (the calibratable axes) — grouped to lever
TERMS = {
 "bonus_v3273_wait_courier": "D-wait", "bonus_r9_wait_pen": "D-wait", "bonus_r9_stopover": "LOAD",
 "bonus_r8_soft_pen": "LOAD/A", "v326_fleet_load_adjustment": "LOAD", "bonus_bug4_cap_soft": "LOAD-cap",
 "bonus_r6_soft_pen": "R6", "bonus_r1_corridor": "C-dir", "bonus_r5_detour": "C-dir",
 "bonus_r5_pickup_detour_penalty": "C-dir", "bonus_r1_soft_pen": "C-dir", "bonus_r_return_rest": "C-dir",
 "bonus_inter_wave_deadhead": "C-dir", "bonus_wave_clean": "C-wave", "bundle_bonus": "C-bundle",
 "timing_gap_bonus": "NO-WASTE", "v324a_extension_penalty": "A-extend", "bonus_coordinator_idle": "policy",
 "bonus_r4": "R4", "bonus_state_panel_mismatch": "data", "bonus_r_paczki_flex": "paczka",
 "bonus_fifo_violation": "R6-fair",
}
INFORMED = {"gps", "last_picked_up_recent", "last_picked_up_pickup", "last_picked_up_delivery",
            "last_assigned_pickup", "last_assigned_delivery", "post_wave", "pos_from_store",
            "last_known", "last_delivered"}


def pts(s):
    try:
        return datetime.fromisoformat(str(s))
    except Exception:
        return None


def cont(t):
    return t is None or any(lo <= t <= hi for lo, hi in CONT)


def num(c, k, d=0.0):
    v = c.get(k)
    try:
        return float(v) if v is not None else d
    except Exception:
        return d


def tier_rank(c):
    if c.get("late_pickup_committed_breach") is True:
        return 2
    if c.get("new_pickup_needs_extension") is True:
        return 1
    return 0


def bucket_rank(c):
    ps = c.get("pos_source"); bag = num(c, "r6_bag_size")
    if ps in INFORMED:
        return 0
    if (ps in ("no_gps", "pre_shift", "blind", None, "")) and bag == 0:
        return 2
    return 1


def pf_bucket(pf):
    if pf is None:
        return "?"
    pf = int(pf)
    if pf == 0:
        return "0_collapse"
    if pf <= 2:
        return "1-2_scarce"
    if pf <= 4:
        return "3-4_tight"
    return "5+_normal"


ORDER = ["0_collapse", "1-2_scarce", "3-4_tight", "5+_normal"]


def load():
    out = []
    for path in SHADOW:
        try:
            f = open(path, "rb")
        except FileNotFoundError:
            continue
        for line in f:
            try:
                r = json.loads(line)
            except Exception:
                continue
            if r.get("verdict") != "PROPOSE":
                continue
            best = r.get("best")
            if not isinstance(best, dict) or "score" not in best:
                continue
            if cont(pts(r.get("ts"))):
                continue
            pf = r.get("pool_feasible_count")
            cands = [best] + [a for a in (r.get("alternatives") or []) if isinstance(a, dict) and "score" in a]
            out.append({"oid": str(r.get("order_id")), "pf": pf, "best": best, "cands": cands})
        f.close()
    return out


def eligible(d):
    b = d["best"]; tb = (tier_rank(b), bucket_rank(b))
    return [c for c in d["cands"] if (tier_rank(c), bucket_rank(c)) == tb]


def pearson(xs, ys):
    n = len(xs)
    if n < 3:
        return None
    mx = sum(xs) / n; my = sum(ys) / n
    sxy = sum((x - mx) * (y - my) for x, y in zip(xs, ys))
    sxx = sum((x - mx) ** 2 for x in xs); syy = sum((y - my) ** 2 for y in ys)
    return sxy / (sxx ** 0.5 * syy ** 0.5) if sxx > 0 and syy > 0 else 0.0


def main():
    decs = load()
    byb = collections.defaultdict(list)
    for d in decs:
        byb[pf_bucket(d["pf"])].append(d)
    print(f"clean PROPOSE decisions={len(decs)}  (excl. contaminated)")
    print("bucket sizes:", {b: len(byb[b]) for b in ORDER})

    # (1) BEST term magnitudes per bucket
    print("\n" + "=" * 96)
    print("(1) BEST-CANDIDATE additive-term magnitude per bucket  [mean | %active(nonzero)]")
    print("=" * 96)
    hdr = f"{'term':34s} {'lever':9s}"
    for b in ORDER:
        hdr += f" {b[:9]:>16s}"
    print(hdr)
    for term, lev in sorted(TERMS.items(), key=lambda kv: kv[1]):
        row = f"{term:34s} {lev:9s}"
        for b in ORDER:
            vals = [num(d["best"], term) for d in byb[b]]
            if vals:
                mean = sum(vals) / len(vals)
                act = 100.0 * sum(1 for v in vals if abs(v) > 1e-6) / len(vals)
                row += f" {mean:>8.1f}/{act:>4.0f}%"
            else:
                row += f" {'-':>16s}"
        print(row)

    # (2) ARGMAX importance: removing a term flips in-group winner?  (decisions with >=2 eligible)
    print("\n" + "=" * 96)
    print("(2) ARGMAX-IMPORTANCE: % of multi-candidate decisions where REMOVING the term flips the winner")
    print("    (additive-removal sensitivity; only decisions with >=2 eligible candidates)")
    print("=" * 96)
    multi = {b: [d for d in byb[b] if len(eligible(d)) >= 2] for b in ORDER}
    print("multi-candidate (>=2 eligible) per bucket:", {b: len(multi[b]) for b in ORDER})
    hdr = f"{'term':34s} {'lever':9s}"
    for b in ORDER:
        hdr += f" {b[:10]:>11s}"
    print(hdr)
    piv_overall = collections.Counter()
    for term, lev in sorted(TERMS.items(), key=lambda kv: kv[1]):
        row = f"{term:34s} {lev:9s}"
        for b in ORDER:
            grp = multi[b]
            if not grp:
                row += f" {'-':>11s}"; continue
            flips = 0
            for d in grp:
                g = eligible(d)
                w0 = max(g, key=lambda c: num(c, "score"))
                w1 = max(g, key=lambda c: num(c, "score") - num(c, term))
                if str(w1.get("courier_id")) != str(w0.get("courier_id")):
                    flips += 1
                    if b in ("0_collapse", "1-2_scarce"):
                        piv_overall[term] += 1
            row += f" {100.0*flips/len(grp):>9.1f}%"
        print(row)

    # (3) variance (dead detector): std of term across ALL candidates per bucket
    print("\n" + "=" * 96)
    print("(3) TERM VARIANCE across candidates (std); ~0 std => cannot change argmax => DEAD/inert")
    print("=" * 96)
    for term in ["bonus_wave_clean", "bonus_r1_corridor", "bonus_r5_detour", "bonus_v3273_wait_courier",
                 "bonus_r8_soft_pen", "bonus_r6_soft_pen", "v326_fleet_load_adjustment",
                 "bonus_coordinator_idle", "timing_gap_bonus", "v324a_extension_penalty"]:
        allv = [num(c, term) for d in decs for c in d["cands"]]
        nz = [v for v in allv if abs(v) > 1e-6]
        sd = round(st.pstdev(allv), 2) if len(allv) > 1 else 0
        print(f"  {term:34s} std={sd:>8}  %nonzero={100.0*len(nz)/max(1,len(allv)):>5.1f}%  "
              f"range=[{min(allv) if allv else 0:.0f},{max(allv) if allv else 0:.0f}]")

    # (4) double-count correlations on BEST candidates
    print("\n" + "=" * 96)
    print("(4) DOUBLE-COUNT — Pearson corr between penalty axes (on best candidates, where both nonzero-ish)")
    print("=" * 96)
    pairs = [("bonus_v3273_wait_courier", "bonus_r9_wait_pen"),
             ("bonus_v3273_wait_courier", "bonus_r9_stopover"),
             ("bonus_r9_wait_pen", "bonus_r9_stopover"),
             ("bonus_v3273_wait_courier", "bonus_r8_soft_pen"),
             ("bonus_r6_soft_pen", "bonus_v3273_wait_courier"),
             ("bonus_r5_detour", "bonus_r5_pickup_detour_penalty"),
             ("bonus_r6_soft_pen", "timing_gap_bonus"),
             ("bonus_r8_soft_pen", "v326_fleet_load_adjustment")]
    for a, b in pairs:
        xs = []; ys = []
        for d in decs:
            x = num(d["best"], a); y = num(d["best"], b)
            xs.append(x); ys.append(y)
        # restrict to rows where at least one is active
        fxs = [x for x, y in zip(xs, ys) if abs(x) > 1e-6 or abs(y) > 1e-6]
        fys = [y for x, y in zip(xs, ys) if abs(x) > 1e-6 or abs(y) > 1e-6]
        r = pearson(fxs, fys)
        print(f"  corr({a[:26]:26s}, {b[:26]:26s}) = {r if r is None else round(r,3)}  (n_active={len(fxs)})")

    print("\nTop pivotal terms in SHORTAGE (0+1-2), by #decisions flipped:")
    for term, c in piv_overall.most_common(12):
        print(f"  {term:34s} {c}")


if __name__ == "__main__":
    main()
