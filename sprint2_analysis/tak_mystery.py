#!/usr/bin/env python3
"""SCRIPT 2 — Solve 'TAK=0 last 48h' mystery.

Window: last 48h (configurable via --hours).
Hypotheses tested:
  A) Telegram delivery / outcome ratio
  B) Race condition (panel change <30s after propose)
  C) Fast assign panel-first (<60s)
  D) Genuine ignore (no override, no assign, just timeout)
"""
import argparse
import statistics
from collections import Counter
from datetime import timedelta
from _common import load_entries, parse_ts, now_utc, fmt_warsaw


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--hours", type=int, default=48)
    args = ap.parse_args()

    end = now_utc()
    since = end - timedelta(hours=args.hours)
    entries = list(load_entries(since_utc=since, until_utc=end))

    propose_count = 0
    action_counter = Counter()
    deltas_panel = []          # propose -> panel_change (PANEL_OVERRIDE / TIMEOUT_SUPERSEDED)
    deltas_assign = []         # propose -> ASSIGN_DIRECT
    panel_lt30 = 0
    panel_total = 0
    fast_assign_lt60 = 0
    super_count = 0
    panel_first_count = 0     # NEW: PROPOSE entries where override happened <60s after propose

    for e in entries:
        d = e.get("decision") or {}
        propose_ts = parse_ts(d.get("ts"))
        outcome_ts = parse_ts(e.get("ts"))
        if propose_ts is None or outcome_ts is None:
            continue
        propose_count += 1
        act = e.get("action") or "?"
        action_counter[act] += 1
        delta_s = (outcome_ts - propose_ts).total_seconds()

        if act in ("PANEL_OVERRIDE", "TIMEOUT_SUPERSEDED"):
            panel_total += 1
            deltas_panel.append(delta_s)
            if delta_s < 30:
                panel_lt30 += 1
            if delta_s < 60:
                panel_first_count += 1
        if act == "TIMEOUT_SUPERSEDED":
            super_count += 1
            if delta_s < 60:
                fast_assign_lt60 += 1
        if act == "ASSIGN_DIRECT":
            deltas_assign.append(delta_s)

    tak = action_counter.get("TAK", 0)
    timeout_skip = action_counter.get("TIMEOUT_SKIP", 0)
    timeout_super = action_counter.get("TIMEOUT_SUPERSEDED", 0)
    panel_override = action_counter.get("PANEL_OVERRIDE", 0)
    assign_direct = action_counter.get("ASSIGN_DIRECT", 0)

    # Telegram delivery proxy: % of proposes where SOMETHING in Telegram was clicked
    tg_clicks = sum(action_counter.get(a, 0) for a in ("TAK", "NIE", "INNY", "KOORD", "TG_REASON"))
    tg_click_rate = (100.0 * tg_clicks / propose_count) if propose_count else 0.0

    pmedian = statistics.median(deltas_panel) if deltas_panel else 0
    pct_lt30 = (100.0 * panel_lt30 / panel_total) if panel_total else 0
    pct_lt60_super = (100.0 * fast_assign_lt60 / super_count) if super_count else 0
    pct_panel_first = (100.0 * panel_first_count / propose_count) if propose_count else 0

    # Verdict diagnosis
    A = tg_click_rate                             # Adrian DOES click TG (if low → A=high)
    A_score = max(0.0, 100.0 - tg_click_rate)     # higher = more 'doesn't see TG'
    B_score = pct_lt30
    C_score = pct_lt60_super
    D_share = (100.0 * timeout_skip / propose_count) if propose_count else 0

    print("=== TAK=0 MYSTERY ===")
    print(f"Window: last {args.hours}h ({fmt_warsaw(since)} → {fmt_warsaw(end)} Warsaw)")
    print(f"Total proposes (entries with decision): {propose_count}")
    print()
    print("Outcome distribution:")
    for k in ("TAK", "NIE", "INNY", "KOORD", "TG_REASON",
              "PANEL_OVERRIDE", "TIMEOUT_SUPERSEDED", "ASSIGN_DIRECT",
              "TIMEOUT_SKIP", "REPLY_OVERRIDE", "OPERATOR_COMMENT"):
        print(f"  {k}: {action_counter.get(k, 0)}")
    print()
    print(f"Telegram-click rate (TAK/NIE/INNY/KOORD/TG_REASON): {tg_click_rate:.1f}%  (TAK={tak})")
    print(f"Median time propose → panel_change: {pmedian:.1f} s  (n={panel_total})")
    print(f"% panel_change <30s after propose: {pct_lt30:.1f}%  ({panel_lt30}/{panel_total})")
    print(f"% TIMEOUT_SUPERSEDED with assign <60s: {pct_lt60_super:.1f}%  ({fast_assign_lt60}/{super_count})")
    print(f"% PROPOSE with panel-change <60s: {pct_panel_first:.1f}%")
    print()
    print("DIAGNOSIS scores (relative weights):")
    print(f"  A) Adrian doesn't see Telegram (1 - TG-click rate): {A_score:.1f}")
    print(f"  B) Race condition (panel <30s):                     {B_score:.1f}")
    print(f"  C) Fast assign panel-first (<60s of TIMEOUT_SUPER): {C_score:.1f}")
    print(f"  D) Genuine ignore (TIMEOUT_SKIP share):             {D_share:.1f}")
    print()
    if pct_panel_first > 50:
        verdict = "PANEL-FIRST INHERENT (bet C confirmed) — Adrian operates panel before Telegram"
    elif tg_click_rate < 5:
        verdict = "TELEGRAM IGNORED IN PEAK — operator workload too high for TG approval"
    elif pct_lt30 > 40:
        verdict = "RACE CONDITION DOMINANT — propose arrives after panel decision"
    else:
        verdict = "MIXED — no single dominant cause"
    print(f"Verdict: {verdict}")


if __name__ == "__main__":
    main()
