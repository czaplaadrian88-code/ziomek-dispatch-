#!/usr/bin/env python3
"""SCRIPT 4 — Propose flow uptime per hour.

Quality vs uptime distinction: if propose flow stops for chunks of the peak,
'override rate' is meaningless because there are no proposes to override.
"""
import statistics
from collections import Counter
from datetime import datetime, timedelta, timezone
from _common import load_entries, parse_ts, now_utc, fmt_warsaw, to_warsaw


PEAK_HOURS = set(list(range(11, 14)) + list(range(17, 20)))   # weekday peaks


def main():
    end = now_utc()
    # Today since 06:00 Warsaw = 04:00 UTC
    today_start = end.replace(hour=4, minute=0, second=0, microsecond=0)
    if end < today_start:
        today_start -= timedelta(days=1)

    entries_today = list(load_entries(since_utc=today_start, until_utc=end))
    propose_today = [e for e in entries_today if (e.get("decision") or {}).get("verdict") == "PROPOSE"]

    # baseline: last 7 days, same time-of-day window
    baseline_since = end - timedelta(days=8)
    baseline_until = end - timedelta(days=1)
    baseline_entries = list(load_entries(since_utc=baseline_since, until_utc=baseline_until))
    baseline_proposes = [e for e in baseline_entries if (e.get("decision") or {}).get("verdict") == "PROPOSE"]

    # A. Per-hour distribution today
    by_hour_today = Counter()
    timestamps_today = []
    for e in propose_today:
        ts = parse_ts(e.get("ts"))
        if ts is None:
            continue
        timestamps_today.append(ts)
        by_hour_today[to_warsaw(ts).hour] += 1
    timestamps_today.sort()

    # B. Stoppage windows (gaps > 5 min between consecutive proposes)
    gaps = []
    for a, b in zip(timestamps_today, timestamps_today[1:]):
        delta_min = (b - a).total_seconds() / 60.0
        if delta_min > 5:
            gaps.append((a, b, delta_min))

    total_stoppage_min = sum(g[2] for g in gaps)
    peak_stoppage_min = sum(g[2] for g in gaps if to_warsaw(g[0]).hour in PEAK_HOURS)

    # Window length today (in min) capped to peak
    win_start = timestamps_today[0] if timestamps_today else today_start
    win_end = timestamps_today[-1] if timestamps_today else end
    window_min = (win_end - win_start).total_seconds() / 60.0
    peak_window_min = sum(60.0 for h in PEAK_HOURS
                          if to_warsaw(win_start).hour <= h <= to_warsaw(win_end).hour)
    peak_window_min = peak_window_min or 1

    uptime_pct = 100.0 * (1 - total_stoppage_min / window_min) if window_min > 0 else 0
    peak_stoppage_pct = min(100.0, 100.0 * peak_stoppage_min / peak_window_min)

    # C. Baseline compare — average proposes/hour across last 7d
    by_hour_baseline = Counter()
    for e in baseline_proposes:
        ts = parse_ts(e.get("ts"))
        if ts is None:
            continue
        by_hour_baseline[to_warsaw(ts).hour] += 1
    baseline_per_hour = {h: by_hour_baseline[h] / 7.0 for h in by_hour_baseline}

    # D. Verdict
    if uptime_pct >= 90:
        verdict = "A) Quality issue (uptime >=90%): focus na score/feasibility fix"
    elif uptime_pct >= 70:
        verdict = "B) Mixed (uptime 70-90%): both issues"
    else:
        verdict = "C) Uptime issue (uptime <70%): focus na propose flow stability"

    print("=== PROPOSE FLOW UPTIME ===")
    print(f"Window: {fmt_warsaw(today_start)} → {fmt_warsaw(end)} Warsaw")
    print(f"Total propose entries today: {len(propose_today)}")
    print()
    print("Per-hour count today vs baseline (last 7d avg):")
    for h in sorted(set(by_hour_today) | set(by_hour_baseline)):
        peak_marker = " [PEAK]" if h in PEAK_HOURS else ""
        b = baseline_per_hour.get(h, 0)
        t = by_hour_today.get(h, 0)
        flag = " ⚠ <50% baseline" if b > 0 and t < 0.5 * b else ""
        print(f"  {h:02d}h: today={t:>3} | baseline_avg={b:>5.1f}{peak_marker}{flag}")
    print()
    print(f"Stoppage windows detected (gap > 5 min): {len(gaps)}")
    for a, b, m in gaps:
        peak = " [PEAK]" if to_warsaw(a).hour in PEAK_HOURS else ""
        print(f"  {fmt_warsaw(a)} → {fmt_warsaw(b)}: {m:.1f} min{peak}")
    print()
    print(f"Total stoppage min today: {total_stoppage_min:.1f}")
    print(f"Peak hours stoppage min: {peak_stoppage_min:.1f} ({peak_stoppage_pct:.1f}% of peak window)")
    print(f"Today's uptime: {uptime_pct:.1f}%")
    print()
    print(f"VERDICT: {verdict}")


if __name__ == "__main__":
    main()
