#!/usr/bin/env python
"""Pomiar ogona świeżości odbioru = (a) planner slack = projected_pickup - ready_at.

Sprint OBJ FRESH (2026-05-30). Czyta plan.pickup_at (projekcja planisty) + top-level
pickup_ready_at z shadow_decisions.jsonl → rozkład luzu odbioru, tylko food (firmy
Panel Bridge wykluczone). Uruchom przed flipem (baseline) i +7 dni (post) → porównaj
ogon (>5 / >10 min). Read-only.

Usage:
    measure_pickup_freshness_tail.py [--since-iso 2026-05-30T19:13:00+00:00]
                                     [--until-iso ...]
Bez --since liczy CAŁĄ historię (baseline pre-flip). Z --since = okno post-flip.
"""
import argparse
import json
import statistics as st
from datetime import datetime
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
SHADOW = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"

FIRM_SUBSTR = ["nadajesz", "orthdruk", "dr tusz", "drtusz", "dentomax",
               "3giga", "interpap", "bravilor", "street-sport", "street sport",
               "mali wojownic", "matka polka", "kurier gastro"]


def is_firm(name):
    if not name:
        return False
    n = name.lower()
    return any(s in n for s in FIRM_SUBSTR)


def parse_aware(s):
    if not s or not isinstance(s, str):
        return None
    s = s.strip()
    try:
        if s.endswith("Z"):
            s = s[:-1] + "+00:00"
        dt = datetime.fromisoformat(s)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo("UTC"))
    return dt


def pctl(xs, p):
    if not xs:
        return None
    xs = sorted(xs)
    k = (len(xs) - 1) * p / 100
    lo = int(k)
    hi = min(lo + 1, len(xs) - 1)
    return xs[lo] + (xs[hi] - xs[lo]) * (k - lo)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since-iso", default=None)
    ap.add_argument("--until-iso", default=None)
    args = ap.parse_args()
    since = parse_aware(args.since_iso) if args.since_iso else None
    until = parse_aware(args.until_iso) if args.until_iso else None

    a_vals, a_peak = [], []
    firm_skipped = no_proj = total = 0

    with open(SHADOW) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                rec = json.loads(line)
            except Exception:
                continue
            ts = parse_aware(rec.get("ts"))
            if since and (ts is None or ts < since):
                continue
            if until and (ts is None or ts > until):
                continue
            total += 1
            oid = str(rec.get("order_id") or "")
            if is_firm(rec.get("restaurant")):
                firm_skipped += 1
                continue
            plan = (rec.get("best") or {}).get("plan") or {}
            proj = parse_aware((plan.get("pickup_at") or {}).get(oid))
            ready = parse_aware(rec.get("pickup_ready_at"))
            if proj is None or ready is None:
                no_proj += 1
                continue
            slack = (proj - ready).total_seconds() / 60.0
            a_vals.append(slack)
            if 11 <= proj.astimezone(WARSAW).hour <= 21:
                a_peak.append(slack)

    print(f"window since={args.since_iso} until={args.until_iso}")
    print(f"records={total} firm_skipped={firm_skipped} no_projection={no_proj}")
    for label, xs in (("all-hours", a_vals), ("peak 11-21h", a_peak)):
        if not xs:
            print(f"  {label}: n=0")
            continue
        print(f"  {label}: n={len(xs)} median={st.median(xs):.1f} mean={st.mean(xs):.1f} "
              f"p75={pctl(xs,75):.1f} p90={pctl(xs,90):.1f} p95={pctl(xs,95):.1f} max={max(xs):.1f}")
    if a_vals:
        n = len(a_vals)
        print(f"  TAIL: >5min={sum(1 for x in a_vals if x>5)/n*100:.1f}%  "
              f">10min={sum(1 for x in a_vals if x>10)/n*100:.1f}%  "
              f">15min={sum(1 for x in a_vals if x>15)/n*100:.1f}%")


if __name__ == "__main__":
    main()
