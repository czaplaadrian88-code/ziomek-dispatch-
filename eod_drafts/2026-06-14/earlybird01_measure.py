#!/usr/bin/env python3
"""EARLYBIRD-01 measurement (READ-ONLY).

Verifies the audit claim that early_bird = 44-46% of ALL KOORD escalations, and
assesses whether merging early_bird into czasowka_scheduler + auto_koord with a
T-30 re-trigger is worth implementing.

Data source (PRIMARY): /root/.openclaw/workspace/dispatch_state/observability/
    candidate_decisions_YYYYMMDD.jsonl
  - Written by observability/candidate_logger.CandidateLogger.log_evaluation
  - source="dispatch_pipeline.assess_order" carries the KOORD verdict where
    early_bird is triggered (dispatch_pipeline.py:2536-2549).
  - decision.reason == "early_bird (<N> min ahead)" identifies early_bird KOORD;
    N = minutes the (raw declared) pickup_ready is ahead of "now" at decision time.
  - context.{pool_total_count,pool_feasible_count}, fleet_size_total,
    fleet_size_on_shift give the fleet-load proxy for deferability.

KEY METHODOLOGY DECISIONS
-------------------------
1. assess_order RE-EVALUATES the same order many times (every panel tick) while
   it is still early. Raw record counts (~3.8k KOORD/day) hugely overstate
   coordinator load. The coordinator is paged AT MOST ONCE per order. So all
   headline metrics are computed over DISTINCT order_id, NOT raw records.
2. Exclude synthetic/test orders: non-numeric order_id OR restaurant containing
   "test" (e.g. TEST_EARLY_BIRD, "Test Restauracja").
3. Exclude CONTAMINATED UTC windows (PARSER_DEGRADED, SYNCWORKA).
4. For an order seen multiple times, we keep its FIRST early_bird record (the
   moment it would first be punted) and also its MAX minutes-ahead (earliest
   firing) for the how-early distribution.

NOTE: in current shadow mode a KOORD verdict means "Ziomek declines to
auto-propose and leaves the order to the human" (shadow_dispatcher routes only
verdict=PROPOSE to Telegram). early_bird KOORD = Ziomek punting an order that is
simply too early to act on. That is the noise EARLYBIRD-01 wants to remove from
the "real escalation" stream.
"""
from __future__ import annotations

import glob
import json
import re
import statistics
from collections import defaultdict
from datetime import datetime, timezone

OBS_GLOB = "/root/.openclaw/workspace/dispatch_state/observability/candidate_decisions_*.jsonl"

# Contaminated windows (UTC) — EXCLUDE
CONTAMINATED = [
    (datetime(2026, 6, 6, 17, 53, tzinfo=timezone.utc), datetime(2026, 6, 10, 18, 24, tzinfo=timezone.utc)),  # PARSER_DEGRADED
    (datetime(2026, 6, 11, 14, 28, tzinfo=timezone.utc), datetime(2026, 6, 12, 18, 32, tzinfo=timezone.utc)),  # SYNCWORKA
]

# Only analyse the period where the obs stream is populated and recent enough to
# be representative of current behaviour. Use full available range but report it.
EB_RE = re.compile(r"early_bird\s*\((-?\d+)\s*min ahead\)")

# OVERNIGHT REPLAY/BACKFILL FILTER.
# assess_order is also driven by offline replay/backfill jobs that run overnight
# (21:00-06:00 UTC = ~23:00-08:00 Białystok, when no live dispatch happens).
# On normal days the overnight window has 0-7 incidental records; on 2026-05-18,
# 2026-06-13 and 2026-06-14 it has 273 / 1038 / 555 records — clearly batch
# replays writing into the same obs file (same oid re-hit within seconds,
# PANEL_QUOTE_* synthetic ids). These do NOT page the coordinator. We drop the
# overnight window so metrics reflect only live coordinator-facing decisions.
OVERNIGHT_START_UTC_H = 21   # inclusive
OVERNIGHT_END_UTC_H = 6      # exclusive (records with hour < 6 also dropped)


def is_overnight(dt):
    if dt is None:
        return False
    h = dt.hour
    return h >= OVERNIGHT_START_UTC_H or h < OVERNIGHT_END_UTC_H


def parse_ts(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def is_contaminated(dt):
    if dt is None:
        return False
    return any(a <= dt <= b for a, b in CONTAMINATED)


def is_synthetic(oid, restaurant):
    oid = str(oid)
    if not oid.isdigit():
        return True
    if "test" in (restaurant or "").lower():
        return True
    return False


def main():
    files = sorted(glob.glob(OBS_GLOB))

    # Per distinct order aggregates (assess_order source only)
    # order -> dict
    orders = {}
    # day (UTC date) -> sets of distinct order_ids
    koord_orders_by_day = defaultdict(set)
    eb_orders_by_day = defaultdict(set)

    raw_koord_records = 0
    raw_eb_records = 0
    skipped_contaminated = 0
    skipped_overnight = 0
    skipped_synth_records = 0

    first_ts = None
    last_ts = None

    for path in files:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("source") != "dispatch_pipeline.assess_order":
                    continue
                dec = d.get("decision") or {}
                if dec.get("verdict") != "KOORD":
                    continue

                ts = parse_ts(d.get("ts"))
                if is_contaminated(ts):
                    skipped_contaminated += 1
                    continue
                if is_overnight(ts):
                    skipped_overnight += 1
                    continue

                ctx = d.get("context") or {}
                oid = str(d.get("order_id"))
                rest = ctx.get("restaurant") or ""
                if is_synthetic(oid, rest):
                    skipped_synth_records += 1
                    continue

                if first_ts is None or (ts and ts < first_ts):
                    first_ts = ts
                if last_ts is None or (ts and ts > last_ts):
                    last_ts = ts

                reason = dec.get("reason") or ""
                is_eb = reason.startswith("early_bird")
                raw_koord_records += 1
                day = ts.date().isoformat() if ts else "?"
                koord_orders_by_day[day].add(oid)

                rec = orders.get(oid)
                if rec is None:
                    rec = {
                        "first_ts": ts,
                        "restaurant": rest,
                        "is_eb_ever": False,
                        "first_eb_ts": None,
                        "eb_minutes_first": None,   # minutes-ahead at FIRST eb fire
                        "eb_minutes_max": None,     # max minutes-ahead (earliest fire)
                        "last_koord_reason": reason,
                        # fleet/pool snapshot at first eb fire
                        "eb_pool_total": None,
                        "eb_pool_feasible": None,
                        "eb_fleet_total": None,
                        "eb_fleet_on_shift": None,
                        "days": set(),
                    }
                    orders[oid] = rec
                rec["days"].add(day)
                rec["last_koord_reason"] = reason

                if is_eb:
                    raw_eb_records += 1
                    eb_orders_by_day[day].add(oid)
                    m = EB_RE.search(reason)
                    mins = int(m.group(1)) if m else None
                    if not rec["is_eb_ever"]:
                        rec["is_eb_ever"] = True
                        rec["first_eb_ts"] = ts
                        rec["eb_minutes_first"] = mins
                        rec["eb_pool_total"] = ctx.get("pool_total_count")
                        rec["eb_pool_feasible"] = ctx.get("pool_feasible_count")
                        rec["eb_fleet_total"] = d.get("fleet_size_total")
                        rec["eb_fleet_on_shift"] = d.get("fleet_size_on_shift")
                    if mins is not None:
                        if rec["eb_minutes_max"] is None or mins > rec["eb_minutes_max"]:
                            rec["eb_minutes_max"] = mins

    # ---- DISTINCT-ORDER metrics ----
    distinct_koord = len(orders)
    distinct_eb = sum(1 for r in orders.values() if r["is_eb_ever"])
    eb_share = (distinct_eb / distinct_koord * 100.0) if distinct_koord else 0.0

    # Orders whose ONLY KOORD reason ever was early_bird (pure early_bird) vs
    # orders that hit early_bird AND later a "real" KOORD reason.
    pure_eb = 0
    eb_then_real = 0
    for r in orders.values():
        if not r["is_eb_ever"]:
            continue
        # last_koord_reason is the most recent KOORD reason we saw for this order
        if r["last_koord_reason"].startswith("early_bird"):
            pure_eb += 1
        else:
            eb_then_real += 1

    # ---- HOW-EARLY distribution (minutes ahead at earliest fire) ----
    mins_first = [r["eb_minutes_first"] for r in orders.values()
                  if r["is_eb_ever"] and r["eb_minutes_first"] is not None]
    mins_max = [r["eb_minutes_max"] for r in orders.values()
                if r["is_eb_ever"] and r["eb_minutes_max"] is not None]

    def dist(xs):
        if not xs:
            return {}
        xs = sorted(xs)
        return {
            "n": len(xs),
            "min": xs[0],
            "p25": xs[int(0.25 * (len(xs) - 1))],
            "median": statistics.median(xs),
            "p75": xs[int(0.75 * (len(xs) - 1))],
            "p90": xs[int(0.90 * (len(xs) - 1))],
            "max": xs[-1],
            "mean": round(statistics.mean(xs), 1),
        }

    # Buckets for "would a T-30 re-trigger defer most of them?"
    buckets = [(30, 45), (45, 60), (60, 90), (90, 120), (120, 99999)]
    bucket_counts = {f"{a}-{b if b < 99999 else '∞'}": 0 for a, b in buckets}
    below_30 = 0
    for m in mins_first:
        if m < 30:
            below_30 += 1
            continue
        for a, b in buckets:
            if a <= m < b:
                bucket_counts[f"{a}-{b if b < 99999 else '∞'}"] += 1
                break

    # ---- DEFERABILITY PROXY ----
    # Proxy: at the moment early_bird fired, was there fleet capacity such that by
    # T-30 (30 min before ready, food still being prepped) a normal auto-assign
    # could plausibly handle it WITHOUT a human?
    #   - fleet_on_shift (or fleet_total if on_shift None) > 0  => couriers exist
    #   - pool_feasible_count is ~always 0 here because early_bird SHORT-CIRCUITS
    #     before feasibility loop (dispatch_pipeline returns KOORD before building
    #     the pool), so pool counts are NOT informative for deferability.
    # Therefore the proxy keys on whether ANY fleet is on shift at fire time.
    eb_recs = [r for r in orders.values() if r["is_eb_ever"]]
    fleet_known = [r for r in eb_recs if (r["eb_fleet_on_shift"] is not None or r["eb_fleet_total"] is not None)]

    def eff_fleet(r):
        if r["eb_fleet_on_shift"] is not None:
            return r["eb_fleet_on_shift"]
        return r["eb_fleet_total"]

    fleet_gt0 = sum(1 for r in fleet_known if (eff_fleet(r) or 0) > 0)
    fleet_eq0 = sum(1 for r in fleet_known if (eff_fleet(r) or 0) == 0)
    pool_total_known = [r for r in eb_recs if r["eb_pool_total"] is not None]
    pool_total_gt0 = sum(1 for r in pool_total_known if (r["eb_pool_total"] or 0) > 0)

    # ---- ALERTS-SAVED-PER-DAY ----
    # Clean days = days present in koord_orders_by_day, excluding days fully inside
    # contaminated windows (already filtered per-record). Report per-day distinct
    # KOORD orders and distinct early_bird orders.
    days = sorted(koord_orders_by_day.keys())
    per_day = []
    for day in days:
        k = len(koord_orders_by_day[day])
        e = len(eb_orders_by_day.get(day, set()))
        per_day.append((day, k, e))

    # Average over clean window. Drop partial first/last day? Keep all but report.
    n_days = len(per_day)
    avg_koord_per_day = statistics.mean([k for _, k, _ in per_day]) if per_day else 0
    avg_eb_per_day = statistics.mean([e for _, _, e in per_day]) if per_day else 0

    # ---- OUTPUT ----
    print("=" * 78)
    print("EARLYBIRD-01 MEASUREMENT  (READ-ONLY)")
    print("=" * 78)
    print(f"Source files: {len(files)} obs candidate_decisions_*.jsonl")
    print(f"Decision-time range (clean records): {first_ts} .. {last_ts}")
    print(f"Excluded contaminated KOORD records: {skipped_contaminated}")
    print(f"Excluded overnight replay/backfill KOORD records (21-06 UTC): {skipped_overnight}")
    print(f"Excluded synthetic/test KOORD records: {skipped_synth_records}")
    print(f"Raw KOORD records (assess_order, clean, real): {raw_koord_records}")
    print(f"Raw early_bird KOORD records (clean, real):    {raw_eb_records}")
    print()
    print("-" * 78)
    print("1) KOORD TOTAL & EARLY_BIRD SHARE  (DISTINCT ORDERS — the honest unit)")
    print("-" * 78)
    print(f"Distinct orders that hit KOORD at least once: {distinct_koord}")
    print(f"Distinct orders that hit early_bird KOORD:    {distinct_eb}")
    print(f"==> early_bird share of KOORD orders: {eb_share:.1f}%")
    print(f"    (audit claim: 44-46%)")
    print()
    print(f"   of early_bird orders: pure early_bird (last KOORD still eb): {pure_eb}")
    print(f"   of early_bird orders: eb THEN later a 'real' KOORD reason:   {eb_then_real}")
    raw_share = (raw_eb_records / raw_koord_records * 100.0) if raw_koord_records else 0
    print(f"   [for reference, RAW-RECORD share (inflated): {raw_share:.1f}%]")
    print()
    print("-" * 78)
    print("2) HOW EARLY DO THEY FIRE  (minutes BEFORE declared pickup/ready)")
    print("-" * 78)
    print(f"   minutes-ahead at FIRST early_bird fire: {dist(mins_first)}")
    print(f"   minutes-ahead at EARLIEST (max) fire:   {dist(mins_max)}")
    print(f"   threshold is EARLY_BIRD_THRESHOLD_MIN (env-default 60).")
    print()
    print("   First-fire minutes-ahead buckets:")
    if below_30:
        print(f"     <30 (already inside T-30!): {below_30}")
    for k, v in bucket_counts.items():
        print(f"     {k:>8} min : {v}")
    print()
    print("-" * 78)
    print("3) DEFERABILITY PROXY — would a T-30 re-trigger likely auto-resolve?")
    print("-" * 78)
    print("   NOTE: early_bird short-circuits BEFORE the feasibility pool is built,")
    print("   so pool_feasible_count is ~0 by construction and NOT informative.")
    print("   Proxy = fleet on shift > 0 at fire time (couriers exist to assign).")
    print(f"   early_bird orders with fleet-size info: {len(fleet_known)}/{len(eb_recs)}")
    print(f"     fleet on shift > 0 at fire: {fleet_gt0} "
          f"({(fleet_gt0/len(fleet_known)*100 if fleet_known else 0):.1f}%)")
    print(f"     fleet on shift == 0 at fire: {fleet_eq0} "
          f"({(fleet_eq0/len(fleet_known)*100 if fleet_known else 0):.1f}%)")
    print(f"   [pool_total_count>0 at fire (rare, info only): "
          f"{pool_total_gt0}/{len(pool_total_known)}]")
    print()
    print("-" * 78)
    print("4) COORDINATOR-NOISE IMPACT (per-day distinct orders)")
    print("-" * 78)
    print(f"   clean days observed: {n_days}")
    print(f"   {'day':<12}{'KOORD_orders':>14}{'early_bird':>12}")
    for day, k, e in per_day:
        print(f"   {day:<12}{k:>14}{e:>12}")
    print(f"   avg KOORD orders/day:      {avg_koord_per_day:.1f}")
    print(f"   avg early_bird orders/day: {avg_eb_per_day:.1f}  <-- alerts saved/day if deferred")
    print("=" * 78)


if __name__ == "__main__":
    main()
