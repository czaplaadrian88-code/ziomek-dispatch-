#!/usr/bin/env python3
"""SCRIPT 1 — Data inventory for Sprint 2 readiness."""
from collections import Counter, defaultdict
from _common import load_entries, SPRINT1_DEPLOY_UTC, V319I_DEPLOY_UTC, now_utc, fmt_warsaw


def main():
    since = SPRINT1_DEPLOY_UTC
    end = now_utc()
    entries = list(load_entries(since_utc=since, until_utc=end))

    by_action = Counter()
    has_pool_total = 0
    has_pool_feasible = 0
    has_top_n_16 = 0           # heuristic: alternatives length >= 12
    alts_gt_4 = 0
    tg_per_op = defaultdict(Counter)

    for e in entries:
        act = e.get("action") or "_no_action_"
        by_action[act] += 1
        d = e.get("decision") or {}
        if "pool_total_count" in d:
            has_pool_total += 1
        if "pool_feasible_count" in d:
            has_pool_feasible += 1
        alts = d.get("alternatives") or []
        if len(alts) >= 12:
            has_top_n_16 += 1
        if len(alts) > 4:
            alts_gt_4 += 1
        if act == "TG_REASON":
            op = e.get("operator") or "?"
            tg_per_op[op][e.get("reason_code", "?")] += 1

    total = len(entries)
    overrides = by_action.get("PANEL_OVERRIDE", 0)

    if overrides >= 30:
        readiness = "GREEN"
    elif overrides >= 15:
        readiness = "YELLOW"
    else:
        readiness = "RED"

    pct = lambda n: f"{(100*n/total):.1f}%" if total else "n/a"

    print("=== DATA INVENTORY ===")
    print(f"Window: {fmt_warsaw(since)} → {fmt_warsaw(end)} (Warsaw)")
    print(f"Total entries: {total}")
    print()
    print("By action:")
    for act in ("PROPOSE", "PANEL_OVERRIDE", "TIMEOUT_SUPERSEDED", "TG_REASON",
                "ASSIGN_DIRECT", "TIMEOUT", "TIMEOUT_SKIP", "TAK", "NIE",
                "INNY", "OPERATOR_COMMENT", "REPLY_OVERRIDE", "KOORD"):
        print(f"  {act}: {by_action.get(act, 0)}")
    print(f"  (every entry contains a PROPOSE decision; 'action' = OUTCOME)")
    print()
    print("Extended logging coverage (Sprint 1):")
    print(f"  pool_total_count present:    {has_pool_total} ({pct(has_pool_total)})")
    print(f"  pool_feasible_count present: {has_pool_feasible} ({pct(has_pool_feasible)})")
    print(f"  alternatives len > 4:        {alts_gt_4} ({pct(alts_gt_4)})")
    print(f"  alternatives len >= 12 (TOP_N=16 heuristic): {has_top_n_16} ({pct(has_top_n_16)})")
    print()
    print(f"TG_REASON distribution (since V3.19i deploy {fmt_warsaw(V319I_DEPLOY_UTC)}):")
    if not tg_per_op:
        print("  (no TG_REASON entries — Telegram approval skipped in peak)")
    else:
        for op, codes in tg_per_op.items():
            inner = ", ".join(f"{k}: {v}" for k, v in codes.most_common())
            print(f"  {op}: {inner}")
    print()
    print(f"Sprint 2 readiness: {readiness}  (PANEL_OVERRIDE n={overrides}; thresholds GREEN>=30 / YELLOW>=15)")


if __name__ == "__main__":
    main()
