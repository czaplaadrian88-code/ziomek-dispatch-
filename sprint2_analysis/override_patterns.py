#!/usr/bin/env python3
"""SCRIPT 3 — 7 patterns from PANEL_OVERRIDE entries (Sprint 1 enhanced data)."""
import statistics
from collections import Counter, defaultdict
from datetime import timedelta
from _common import load_entries, SPRINT1_DEPLOY_UTC, parse_ts, now_utc, fmt_warsaw, to_warsaw


def find_alt(alts, courier_id):
    for a in alts or []:
        if str(a.get("courier_id")) == str(courier_id):
            return a
    return None


def main():
    end = now_utc()
    since = SPRINT1_DEPLOY_UTC
    entries = list(load_entries(since_utc=since, until_utc=end))

    # collect propose totals per courier (denominator)
    per_courier_proposed = Counter()
    for e in entries:
        d = e.get("decision") or {}
        cid = ((d.get("best") or {}).get("courier_id"))
        if cid:
            per_courier_proposed[str(cid)] += 1

    overrides = [e for e in entries if e.get("action") == "PANEL_OVERRIDE"]
    n_over = len(overrides)
    n_total = len(entries)

    # P1 — per-courier override rate
    over_per_courier = Counter()
    for e in overrides:
        cid = str(e.get("proposed_courier_id") or "?")
        over_per_courier[cid] += 1

    courier_rates = []
    for cid, prop_n in per_courier_proposed.items():
        if prop_n < 3:
            continue  # ignore noise
        ovr = over_per_courier.get(cid, 0)
        courier_rates.append((cid, ovr, prop_n, 100.0 * ovr / prop_n))
    courier_rates.sort(key=lambda x: x[3], reverse=True)

    # P2 — score gap distribution
    gaps = []
    for e in overrides:
        proposed_score = e.get("proposed_score")
        actual_cid = str(e.get("actual_courier_id"))
        d = e.get("decision") or {}
        chosen = find_alt(d.get("alternatives"), actual_cid)
        if proposed_score is None or chosen is None:
            continue
        chosen_score = chosen.get("score")
        if chosen_score is None:
            continue
        gaps.append(proposed_score - chosen_score)

    # P3 — strategy used by proposed best
    strat_proposed = Counter()
    strat_overridden = Counter()
    for e in entries:
        d = e.get("decision") or {}
        plan = ((d.get("best") or {}).get("plan") or {})
        s = plan.get("strategy", "?")
        strat_proposed[s] += 1
        if e.get("action") == "PANEL_OVERRIDE":
            strat_overridden[s] += 1

    strat_rates = []
    for s, n in strat_proposed.items():
        ovr = strat_overridden.get(s, 0)
        strat_rates.append((s, ovr, n, 100.0 * ovr / n if n else 0))
    strat_rates.sort(key=lambda x: x[3], reverse=True)

    # P4 — bag overload pattern (proposed vs chosen bag_size)
    bag_proposed = []
    bag_chosen = []
    for e in overrides:
        d = e.get("decision") or {}
        best = d.get("best") or {}
        bp = best.get("r6_bag_size")
        chosen = find_alt(d.get("alternatives"), str(e.get("actual_courier_id")))
        bc = (chosen or {}).get("r6_bag_size")
        if bp is not None:
            bag_proposed.append(bp)
        if bc is not None:
            bag_chosen.append(bc)

    # P5 — drop-zone pattern: proposed has no drop in pickup district, chosen does
    # We don't have raw districts here; proxy = bundle_level1/level2 presence
    p5_chosen_has_bundle = 0
    p5_proposed_has_bundle = 0
    p5_n = 0
    for e in overrides:
        d = e.get("decision") or {}
        proposed = d.get("best") or {}
        chosen = find_alt(d.get("alternatives"), str(e.get("actual_courier_id"))) or {}
        prop_b = bool(proposed.get("bundle_level1") or proposed.get("bundle_level2"))
        chos_b = bool(chosen.get("bundle_level1") or chosen.get("bundle_level2"))
        if prop_b:
            p5_proposed_has_bundle += 1
        if chos_b:
            p5_chosen_has_bundle += 1
        p5_n += 1

    # P6 — restaurant override rates
    rest_propose = Counter()
    rest_override = Counter()
    for e in entries:
        d = e.get("decision") or {}
        r = d.get("restaurant", "?")
        rest_propose[r] += 1
        if e.get("action") == "PANEL_OVERRIDE":
            rest_override[r] += 1
    rest_rates = []
    for r, n in rest_propose.items():
        if n < 3:
            continue
        ovr = rest_override.get(r, 0)
        rest_rates.append((r, ovr, n, 100.0 * ovr / n))
    rest_rates.sort(key=lambda x: x[3], reverse=True)

    # P7 — hour-of-day
    by_hour_propose = Counter()
    by_hour_override = Counter()
    for e in entries:
        ts = parse_ts(e.get("ts"))
        if ts is None:
            continue
        h = to_warsaw(ts).hour
        by_hour_propose[h] += 1
        if e.get("action") == "PANEL_OVERRIDE":
            by_hour_override[h] += 1

    # ---- output ----
    print("=== OVERRIDE PATTERNS ===")
    print(f"Window: {fmt_warsaw(since)} → {fmt_warsaw(end)} Warsaw")
    print(f"Total entries: {n_total} | PANEL_OVERRIDE: {n_over} ({100.0*n_over/n_total:.1f}%)")
    print()

    print("--- P1: Per-courier override rate (min 3 proposes) ---")
    print("  Top-5 most-overridden:")
    for cid, ovr, n, r in courier_rates[:5]:
        print(f"    {cid}: {r:.0f}%  ({ovr}/{n})")
    print("  Bottom-5 least-overridden:")
    for cid, ovr, n, r in sorted(courier_rates, key=lambda x: x[3])[:5]:
        print(f"    {cid}: {r:.0f}%  ({ovr}/{n})")
    print()

    print("--- P2: Score gap (proposed - chosen) ---")
    if gaps:
        print(f"  n={len(gaps)} | median={statistics.median(gaps):.2f} | "
              f"mean={statistics.mean(gaps):.2f} | "
              f"stdev={statistics.pstdev(gaps):.2f}")
        print(f"  range: [{min(gaps):.1f}, {max(gaps):.1f}]")
    else:
        print("  no resolvable gaps (chosen courier_id often outside pool)")
    print()

    print("--- P3: Strategy of proposed best vs override rate ---")
    for s, ovr, n, r in strat_rates:
        print(f"  {s}: {r:.0f}%  ({ovr}/{n})")
    print()

    print("--- P4: Bag size proposed vs chosen (override only) ---")
    if bag_proposed:
        print(f"  proposed median={statistics.median(bag_proposed):.1f}  "
              f"chosen median={statistics.median(bag_chosen) if bag_chosen else 'n/a'}")
    else:
        print("  insufficient bag data")
    print()

    print("--- P5: Bundle proxy (level1/level2 presence) ---")
    print(f"  overrides with proposed-bundled: {p5_proposed_has_bundle}/{p5_n}")
    print(f"  overrides with chosen-bundled:   {p5_chosen_has_bundle}/{p5_n}")
    print()

    print("--- P6: Restaurant override rate (top-10, min 3 proposes) ---")
    for r, ovr, n, rate in rest_rates[:10]:
        print(f"  {r}: {rate:.0f}%  ({ovr}/{n})")
    print()

    print("--- P7: Hour-of-day override rate (Warsaw) ---")
    for h in sorted(by_hour_propose):
        n = by_hour_propose[h]
        ovr = by_hour_override.get(h, 0)
        print(f"  {h:02d}h: {100.0*ovr/n:.0f}%  ({ovr}/{n})")
    print()

    # Sprint 3 priority: largest impact = override count weighted, lowest effort = clear single dimension
    print("--- Sprint 3 priority list (impact-ranked) ---")
    impact = []
    if courier_rates:
        c = courier_rates[0]
        impact.append((c[1], f"P1: courier {c[0]} {c[3]:.0f}% override rate ({c[1]} cases) — courier-specific scoring penalty"))
    if rest_rates:
        r = rest_rates[0]
        impact.append((r[1], f"P6: restaurant '{r[0]}' {r[3]:.0f}% override ({r[1]} cases) — restaurant-specific bonus tweak"))
    if strat_rates:
        s = strat_rates[0]
        impact.append((s[1], f"P3: strategy '{s[0]}' {s[3]:.0f}% override — review TSP fallback path"))
    if gaps and abs(statistics.median(gaps)) > 5:
        impact.append((len(gaps), f"P2: median gap {statistics.median(gaps):.1f} — chosen often LOWER score than proposed → operator domain knowledge missing in scoring"))
    impact.sort(key=lambda x: x[0], reverse=True)
    for i, (_, msg) in enumerate(impact[:5], 1):
        print(f"  {i}. {msg}")


if __name__ == "__main__":
    main()
