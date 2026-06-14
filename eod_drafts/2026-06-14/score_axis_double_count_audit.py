#!/usr/bin/env python3
"""
SCORE-03/04/06 DOUBLE-COUNT AUDIT — READ-ONLY measurement.

Quantifies the audit claim: "46% of BEST candidates have >=2 overlapping
penalty axes (timing_gap + r6_soft + wait + R9)". Verify or refute with real
numbers from shadow_decisions.

READ-ONLY. Does NOT touch prod, flags, dispatch_state, or any live file.
Only reads logs + backfill, prints to stdout.

Penalty axes inspected (per BEST candidate dict in shadow_decisions):
  - timing_gap_bonus      (R-NO-WASTE de-facto; free_at gap vs pickup_ready)
  - bonus_r6_soft_pen     (R6 thermal/bag-time soft zone 30-35min)
  - bonus_r9_wait_pen     (R9 courier idle pre-pickup wait penalty)
  - bonus_bug4_cap_soft   (V3.19h tier-cap soft; bag-load axis #2)
  - bonus_r5_detour       (R5 pickup detour penalty)
Plus s_obciazenie / base-load effect reconstructed from bag_size_before
(scoring weight 0.25, /5 normalization).

Sources (READ-ONLY):
  - logs/shadow_decisions.jsonl       (current, 06-11..06-14)
  - logs/shadow_decisions.jsonl.1     (rotated, 06-02..06-10)
  - dispatch_state/backfill_decisions_outcomes_v1.jsonl (decision->outcome)

Contaminated windows EXCLUDED from all quality analysis (UTC):
  A) PARSER_DEGRADED: 2026-06-06T17:53 .. 2026-06-10T18:24
  B) SYNCWORKA:       2026-06-11T14:28 .. 2026-06-12T18:32
"""
import json
import collections
import itertools
from datetime import datetime, timezone

SHADOW_CUR = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
SHADOW_ROT = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1"
BACKFILL = "/root/.openclaw/workspace/dispatch_state/backfill_decisions_outcomes_v1.jsonl"

PARSER_DEG_LO = datetime(2026, 6, 6, 17, 53, tzinfo=timezone.utc)
PARSER_DEG_HI = datetime(2026, 6, 10, 18, 24, tzinfo=timezone.utc)
SYNC_LO = datetime(2026, 6, 11, 14, 28, tzinfo=timezone.utc)
SYNC_HI = datetime(2026, 6, 12, 18, 32, tzinfo=timezone.utc)

# The 5 penalty axes (per task spec)
AXES = [
    "timing_gap_bonus",
    "bonus_r6_soft_pen",
    "bonus_r9_wait_pen",
    "bonus_bug4_cap_soft",
    "bonus_r5_detour",
]
AXIS_SHORT = {
    "timing_gap_bonus": "timing_gap",
    "bonus_r6_soft_pen": "r6_soft",
    "bonus_r9_wait_pen": "r9_wait",
    "bonus_bug4_cap_soft": "bug4_cap",
    "bonus_r5_detour": "r5_detour",
}

NEG_EPS = -1e-9  # value < NEG_EPS counts as a negative (active) penalty axis
MAX_BAG = 5.0
OBC_WEIGHT = 0.25


def parse_ts(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except Exception:
        return None


def is_contaminated(ts):
    if ts is None:
        return True
    if PARSER_DEG_LO <= ts <= PARSER_DEG_HI:
        return True
    if SYNC_LO <= ts <= SYNC_HI:
        return True
    return False


def g(d, k, default=0.0):
    v = d.get(k)
    if v is None:
        return default
    try:
        return float(v)
    except Exception:
        return default


def load_shadow():
    """Return list of (record, ts) for PROPOSE records that have a best dict."""
    out = []
    for path in (SHADOW_ROT, SHADOW_CUR):
        try:
            f = open(path, "rb")
        except FileNotFoundError:
            continue
        for line in f:
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("verdict") != "PROPOSE":
                continue
            best = d.get("best")
            if not isinstance(best, dict):
                continue
            ts = parse_ts(d.get("ts"))
            out.append((d, ts))
        f.close()
    return out


def stats(vals):
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    vs = sorted(vals)
    n = len(vs)
    return {
        "n": n,
        "min": round(vs[0], 2),
        "p50": round(vs[n // 2], 2),
        "mean": round(sum(vs) / n, 2),
        "p95": round(vs[min(n - 1, int(n * 0.95))], 2),
        "max": round(vs[-1], 2),
    }


def neg_axes(best):
    """Return list of axis-keys that are strictly negative on this best dict."""
    out = []
    for a in AXES:
        v = best.get(a)
        if isinstance(v, (int, float)) and v < NEG_EPS:
            out.append(a)
    return out


def main():
    rows = load_shadow()
    clean = [(d, ts) for (d, ts) in rows if not is_contaminated(ts)]
    cont = [(d, ts) for (d, ts) in rows if is_contaminated(ts)]

    print("=" * 80)
    print("SCORE-03/04/06 DOUBLE-COUNT AUDIT — shadow_decisions (READ-ONLY)")
    print("=" * 80)
    print(f"PROPOSE w/ best dict: total={len(rows)}  clean={len(clean)}  contaminated={len(cont)}")
    tss = sorted([ts for _, ts in clean if ts])
    if tss:
        print(f"clean ts range: {tss[0]} .. {tss[-1]}")
    print(f"Axes: {[AXIS_SHORT[a] for a in AXES]}  (negative = active penalty)")

    # ---------------- 1. Distribution of # negative penalty axes ----------------
    print("\n" + "-" * 80)
    print("1. NUMBER OF SIMULTANEOUSLY-NEGATIVE PENALTY AXES per BEST candidate")
    print("-" * 80)
    n_clean = len(clean)
    dist = collections.Counter()
    neg_lists = []
    for d, _ in clean:
        na = neg_axes(d["best"])
        neg_lists.append((d, na))
        k = len(na)
        dist[k if k < 4 else 4] += 1  # bucket 4+ together as "4+"
    labels = {0: "0 axes", 1: "1 axis", 2: "2 axes", 3: "3 axes", 4: "4+ axes"}
    cum_ge2 = 0
    for k in range(0, 5):
        c = dist.get(k, 0)
        pct = 100.0 * c / max(1, n_clean)
        print(f"  {labels[k]:10s} : {c:5d}  ({pct:5.1f}%)")
        if k >= 2:
            cum_ge2 += c
    pct_ge2 = 100.0 * cum_ge2 / max(1, n_clean)
    print(f"\n  >=2 axes (the '46%' claim): {cum_ge2}/{n_clean} = {pct_ge2:.1f}%")
    verdict_claim = "CONFIRMED" if 40 <= pct_ge2 <= 52 else ("REFUTED (lower)" if pct_ge2 < 40 else "REFUTED (higher)")
    print(f"  -> claim of ~46%: {verdict_claim}")
    # how many candidates have ANY single negative axis
    any_neg = sum(1 for _, na in neg_lists if na)
    print(f"  (for reference: >=1 axis = {any_neg}/{n_clean} = {100.0*any_neg/max(1,n_clean):.1f}%)")

    # ---------------- 2. Combined penalty magnitude (>=2 axes) + co-occurring pairs ----------------
    print("\n" + "-" * 80)
    print("2. COMBINED PENALTY MAGNITUDE (records with >=2 negative axes) + AXIS PAIRS")
    print("-" * 80)
    ge2 = [(d, na) for d, na in neg_lists if len(na) >= 2]
    combined_mag = []
    pair_counts = collections.Counter()
    single_axis_counts = collections.Counter()  # within >=2 records, how often each axis appears
    for d, na in ge2:
        b = d["best"]
        mag = sum(g(b, a) for a in na)  # sum of the negative axis values (negative number)
        combined_mag.append(mag)
        for a in na:
            single_axis_counts[AXIS_SHORT[a]] += 1
        for x, y in itertools.combinations(sorted(na), 2):
            pair_counts[(AXIS_SHORT[x], AXIS_SHORT[y])] += 1
    print(f"  records with >=2 axes: {len(ge2)}")
    s = stats([abs(m) for m in combined_mag])
    print(f"  combined |penalty| (sum of negative axes) magnitude: {s}")
    print(f"  axis frequency WITHIN >=2-axis records:")
    for a, c in single_axis_counts.most_common():
        print(f"    {a:12s} {c:5d}  ({100.0*c/max(1,len(ge2)):4.0f}% of >=2 records)")
    print(f"  most common co-occurring PAIRS (the real redundancy):")
    for (x, y), c in pair_counts.most_common(10):
        print(f"    {x:12s} + {y:12s} : {c:5d}  ({100.0*c/max(1,len(ge2)):4.0f}% of >=2 records)")

    # ---------------- 3. Bag-load double-count: s_obciazenie AND thermal/bag penalty ----------------
    print("\n" + "-" * 80)
    print("3. BAG-LOAD DOUBLE-COUNT: s_obciazenie(bag_size_before>=1) AND (r6_soft<0 OR bug4_cap<0)")
    print("-" * 80)
    bag_ge1 = 0
    thermal_active = 0
    both = 0
    obc_loss = []
    for d, _ in clean:
        b = d["best"]
        bag = int(g(b, "bag_size_before", 0))
        r6 = g(b, "bonus_r6_soft_pen", 0.0)
        bug4 = g(b, "bonus_bug4_cap_soft", 0.0)
        thermal = (r6 < NEG_EPS) or (bug4 < NEG_EPS)
        if bag >= 1:
            bag_ge1 += 1
            # s_obciazenie base-load contribution loss vs empty bag
            s_obc = 100.0 * (1.0 - bag / MAX_BAG) if bag < MAX_BAG else 0.0
            loss = (100.0 - s_obc) * OBC_WEIGHT  # how many base points lost to load
            obc_loss.append(loss)
        if thermal:
            thermal_active += 1
        if bag >= 1 and thermal:
            both += 1
    print(f"  bag_size_before>=1 (s_obciazenie lowers base): {bag_ge1}/{n_clean} ({100.0*bag_ge1/max(1,n_clean):.1f}%)")
    print(f"  thermal/bag penalty active (r6_soft<0 OR bug4_cap<0): {thermal_active}/{n_clean} ({100.0*thermal_active/max(1,n_clean):.1f}%)")
    print(f"  BOTH (bag>=1 AND thermal) = bag-load double-count: {both}/{n_clean} ({100.0*both/max(1,n_clean):.1f}%)")
    print(f"  base-load points lost to s_obciazenie when bag>=1 (weight 0.25): {stats(obc_loss)}")

    # ---------------- 4. Redundancy vs additive: concrete examples ----------------
    print("\n" + "-" * 80)
    print("4. REDUNDANCY vs ADDITIVE — concrete examples")
    print("-" * 80)
    print("  REDUNDANT-LOOKING: timing_gap<0 AND r6_soft<0 (both can be driven by lateness/thermal age)")
    red_examples = []
    for d, na in ge2:
        b = d["best"]
        if "timing_gap_bonus" in na and "bonus_r6_soft_pen" in na:
            red_examples.append((d, b))
    print(f"  count timing_gap<0 & r6_soft<0: {len(red_examples)}")
    for d, b in red_examples[:3]:
        print("   ", json.dumps({
            "oid": d.get("order_id"), "ts": str(parse_ts(d.get("ts")))[:16],
            "rest": (d.get("restaurant") or "")[:24],
            "timing_gap": round(g(b, "timing_gap_bonus"), 1),
            "timing_gap_min": round(g(b, "timing_gap_min"), 1),
            "r6_soft": round(g(b, "bonus_r6_soft_pen"), 1),
            "r6_max_bag_min": round(g(b, "r6_max_bag_time_min"), 1),
            "r9_wait": round(g(b, "bonus_r9_wait_pen"), 1),
            "score": round(g(b, "score"), 1),
        }, ensure_ascii=False))

    print("\n  ADDITIVE-LOOKING: r5_detour<0 AND r9_wait<0 (geometry detour vs idle wait — distinct axes)")
    add_examples = []
    for d, na in ge2:
        b = d["best"]
        if "bonus_r5_detour" in na and "bonus_r9_wait_pen" in na:
            add_examples.append((d, b))
    print(f"  count r5_detour<0 & r9_wait<0: {len(add_examples)}")
    for d, b in add_examples[:3]:
        print("   ", json.dumps({
            "oid": d.get("order_id"), "ts": str(parse_ts(d.get("ts")))[:16],
            "rest": (d.get("restaurant") or "")[:24],
            "r5_detour": round(g(b, "bonus_r5_detour"), 1),
            "r5_detour_km": round(g(b, "r5_pickup_detour_total_km"), 2),
            "r9_wait": round(g(b, "bonus_r9_wait_pen"), 1),
            "timing_gap": round(g(b, "timing_gap_bonus"), 1),
            "score": round(g(b, "score"), 1),
        }, ensure_ascii=False))

    # Correlation between timing_gap and r6_soft (both negative) — Pearson on overlap
    pairs = []
    for d, _ in clean:
        b = d["best"]
        tg = g(b, "timing_gap_bonus", None)
        r6 = g(b, "bonus_r6_soft_pen", None)
        if tg is not None and r6 is not None:
            pairs.append((tg, r6))
    if pairs:
        n = len(pairs)
        mx = sum(p[0] for p in pairs) / n
        my = sum(p[1] for p in pairs) / n
        sxy = sum((x - mx) * (y - my) for x, y in pairs)
        sxx = sum((x - mx) ** 2 for x, y in pairs)
        syy = sum((y - my) ** 2 for x, y in pairs)
        r = sxy / (sxx ** 0.5 * syy ** 0.5) if sxx > 0 and syy > 0 else 0.0
        print(f"\n  Pearson corr(timing_gap_bonus, r6_soft_pen) over all clean best: r={r:.3f} (n={n})")
        print("   (r near 0 => axes measure DIFFERENT things => stacking is additive, not redundant)")

    # ---------------- 5. Outcome link via order_id join to backfill ----------------
    print("\n" + "-" * 80)
    print("5. OUTCOME LINK — do heavily-stacked winners breach more? (join via order_id)")
    print("-" * 80)
    # build outcome map from backfill (clean only)
    bf = {}
    for line in open(BACKFILL, "rb"):
        try:
            d = json.loads(line)
        except Exception:
            continue
        ts = parse_ts(d.get("decision_ts"))
        if is_contaminated(ts):
            continue
        oid = str(d.get("order_id"))
        bf[oid] = d

    def breach_min(rec):
        o = rec.get("outcome") or {}
        v = o.get("pickup_to_delivery_min")
        if v is None:
            return None
        try:
            return float(v)
        except Exception:
            return None

    # Group clean PROPOSE best by # negative axes; join to outcome.
    grp = collections.defaultdict(lambda: {"n": 0, "joined": 0, "deliv": 0, "breach35": 0,
                                           "breach_vals": []})
    joined_total = 0
    for d, na in neg_lists:
        oid = str(d.get("order_id"))
        k = len(na)
        bucket = "0-1" if k <= 1 else ("2" if k == 2 else "3+")
        grp[bucket]["n"] += 1
        rec = bf.get(oid)
        if rec is None:
            continue
        grp[bucket]["joined"] += 1
        joined_total += 1
        bm = breach_min(rec)
        if bm is not None:
            grp[bucket]["deliv"] += 1
            grp[bucket]["breach_vals"].append(bm)
            if bm > 35.0:
                grp[bucket]["breach35"] += 1
    print(f"  shadow PROPOSE clean joined to backfill outcome by order_id: {joined_total}/{n_clean}")
    if joined_total < 30:
        print("  -> join too thin for a clean outcome signal; treat as indicative only.")
    print("  breach = pickup_to_delivery_min > 35 (R6 hard max). p50 = median delivery time.")
    print(f"  {'axes':6s} {'n':>5s} {'joined':>7s} {'deliv':>6s} {'breach%':>8s} {'p50_min':>8s} {'p95_min':>8s}")
    for bucket in ["0-1", "2", "3+"]:
        a = grp.get(bucket)
        if not a:
            continue
        deliv = a["deliv"]
        brp = 100.0 * a["breach35"] / max(1, deliv)
        bs = stats(a["breach_vals"]) or {}
        p50 = bs.get("p50", "-")
        p95 = bs.get("p95", "-")
        print(f"  {bucket:6s} {a['n']:5d} {a['joined']:7d} {deliv:6d} {brp:7.0f}% {str(p50):>8s} {str(p95):>8s}")
    # fleet baseline breach over all joined clean
    all_breach = [breach_min(bf[str(d.get('order_id'))]) for d, _ in neg_lists
                  if str(d.get('order_id')) in bf]
    all_breach = [b for b in all_breach if b is not None]
    if all_breach:
        base_brp = 100.0 * sum(1 for b in all_breach if b > 35.0) / len(all_breach)
        print(f"  FLEET BASELINE (all joined clean, n_deliv={len(all_breach)}): breach={base_brp:.0f}%  p50={stats(all_breach)['p50']}min")

    print("\nDONE.")


if __name__ == "__main__":
    main()
