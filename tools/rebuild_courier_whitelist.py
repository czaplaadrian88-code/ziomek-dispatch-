#!/usr/bin/env python3
"""rebuild_courier_whitelist — daily-rebuildable Faza 7 AUTO whitelist.

Adaptacja `/tmp/build_whitelist.py` (Sprint 4 Agent B) jako stały tool w
workspace, cron-able. Czyta backfill outcomes + per-day load + state files
(`courier_tiers.json`, `courier_names.json`) i produkuje JSON whitelist do
karmienia classifier'a Fazy 7 przez flag
`AUTO_PROXIMITY_COURIER_WHITELIST_FROM_FILE`.

LENS DUAL (v2 — patrz `criteria_rationale` w outpucie):
  L1 (Ziomek-proposal lens, Adrian's original): override_rate when proposed.
  L2 (operator-trust lens): courier_id_final share = how often operator
       actually picks this courier (proxy of trust). High actual-delivery
       share + low R6 breach = operator trusts them.

Baseline PANEL_OVERRIDE rate ≈ 75-85% → strict `override<30%` criterion daje
zero couriers. Tool używa RELATIVE-TO-BASELINE thresholds, tier-aware.

CLI:
  python3 -m dispatch_v2.tools.rebuild_courier_whitelist [--days N]
      [--out /path/to/whitelist.json]
      [--backfill /tmp/backfill_decisions_outcomes_v1.jsonl]
      [--load /tmp/courier_load_per_day.json]
      [--md /path/to/whitelist.md]

Domyślnie:
  --days 14
  --out  /root/.openclaw/workspace/dispatch_state/courier_whitelist_v1.json

Cron design: patrz `/tmp/faza7_rollback_runbook.md` (dispatch-faza7-whitelist.timer).

ZERO writes poza --out i opcjonalnym --md. ZERO dotknięcia produkcji.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ── domyślne ścieżki (env-overridable dla testów) ───────────────────────
DEFAULT_BACKFILL = os.environ.get(
    "FAZA7_BACKFILL_PATH",
    "/root/.openclaw/workspace/dispatch_state/backfill_decisions_outcomes_v1.jsonl",  # G2: /tmp → dispatch_state
)
DEFAULT_LOAD = os.environ.get(
    "FAZA7_LOAD_PATH", "/tmp/courier_load_per_day.json"
)
DEFAULT_TIERS = os.environ.get(
    "FAZA7_TIERS_PATH",
    "/root/.openclaw/workspace/dispatch_state/courier_tiers.json",
)
DEFAULT_NAMES = os.environ.get(
    "FAZA7_NAMES_PATH",
    "/root/.openclaw/workspace/dispatch_state/courier_names.json",
)
DEFAULT_OUT = os.environ.get(
    "FAZA7_WHITELIST_OUT",
    "/root/.openclaw/workspace/dispatch_state/courier_whitelist_v1.json",
)

R6_LIMIT_MIN = 35.0


# ──────────────────────────── load helpers ──────────────────────────────
def _load_json(path: str) -> dict:
    with open(path) as f:
        return json.load(f)


def _tier_of(tiers_raw: dict, cid) -> str | None:
    t = tiers_raw.get(str(cid))
    if not t:
        return None
    bag = t.get("bag") or {}
    return bag.get("tier")


def _name_of(names: dict, tiers_raw: dict, cid) -> str:
    s = str(cid)
    if s in names:
        return names[s]
    t = tiers_raw.get(s)
    if t:
        return t.get("name") or f"cid={s}"
    return f"cid={s}"


def _is_inactive(tiers_raw: dict, cid) -> bool:
    t = tiers_raw.get(str(cid))
    return bool(t and t.get("inactive"))


# ──────────────────────── core aggregation ──────────────────────────────
def aggregate(backfill_path: str, load_path: str, days: int, now: datetime | None = None) -> dict:
    """Zwraca dict {orders_action, orders_proposed, orders_final,
    orders_pickup_delivery, total_deliveries}.

    `days` filtruje per `decision_ts` (UTC) ostatnie N dni od `now` (default
    datetime.now(timezone.utc)). Filter best-effort: rekordy bez parseable
    decision_ts są zachowane (zero-data robustness).
    """
    if now is None:
        now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=days)

    orders_action = defaultdict(set)
    orders_proposed = defaultdict(set)
    orders_final: dict = {}
    orders_pickup_delivery: dict = {}

    with open(backfill_path) as f:
        for line in f:
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if not d.get("outcome"):
                continue
            # decision_ts cut
            ts_raw = d.get("decision_ts")
            if ts_raw:
                try:
                    ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
                    if ts < cutoff:
                        continue
                except Exception:
                    pass  # robust: zachowuj rekord
            oid = d.get("order_id")
            orders_action[oid].add(d.get("action"))
            if d.get("proposed_courier_id"):
                orders_proposed[oid].add(str(d["proposed_courier_id"]))
            o = d.get("outcome") or {}
            if o.get("courier_id_final"):
                orders_final[oid] = str(o["courier_id_final"])
            if o.get("picked_up_ts") and o.get("delivered_ts"):
                orders_pickup_delivery[oid] = (o["picked_up_ts"], o["delivered_ts"])

    # per-day load (no cutoff — caller's `--days` doesn't directly map to dates
    # in load file, but `total_deliveries` is informational)
    total_deliveries: Counter = Counter()
    if os.path.exists(load_path):
        load_per_day = _load_json(load_path)
        for _day, courier_counts in load_per_day.items():
            for cid, n in courier_counts.items():
                total_deliveries[str(cid)] += n

    return {
        "orders_action": orders_action,
        "orders_proposed": orders_proposed,
        "orders_final": orders_final,
        "orders_pickup_delivery": orders_pickup_delivery,
        "total_deliveries": total_deliveries,
    }


# ──────────────────────── per-courier metrics ───────────────────────────
def per_courier_metrics(agg: dict) -> dict:
    """Zwraca dict per-cid metrics: n_proposed, n_override, n_proposed_and_won,
    n_actual, n_actual_was_proposed, n_actual_not_proposed, n_r6_total,
    n_r6_breach, baseline_override_rate, median_or."""
    orders_action = agg["orders_action"]
    orders_proposed = agg["orders_proposed"]
    orders_final = agg["orders_final"]
    orders_pickup_delivery = agg["orders_pickup_delivery"]

    n_proposed = Counter()
    n_override = Counter()
    n_proposed_and_won = Counter()
    n_actual = Counter()
    n_actual_was_proposed = Counter()
    n_actual_not_proposed = Counter()
    n_r6_total = Counter()
    n_r6_breach = Counter()

    for oid, props in orders_proposed.items():
        final = orders_final.get(oid)
        has_override = "PANEL_OVERRIDE" in orders_action[oid]
        for prop in props:
            n_proposed[prop] += 1
            if final == prop:
                n_proposed_and_won[prop] += 1
            elif has_override and final and final != prop:
                n_override[prop] += 1
            elif not final and has_override:
                n_override[prop] += 1

    for oid, final in orders_final.items():
        n_actual[final] += 1
        if final in orders_proposed.get(oid, set()):
            n_actual_was_proposed[final] += 1
        else:
            n_actual_not_proposed[final] += 1
        if oid in orders_pickup_delivery:
            pu, dl = orders_pickup_delivery[oid]
            try:
                pu_dt = datetime.fromisoformat(pu.replace("Z", "+00:00"))
                dl_dt = datetime.fromisoformat(dl.replace("Z", "+00:00"))
                mins = (dl_dt - pu_dt).total_seconds() / 60.0
                n_r6_total[final] += 1
                if mins > R6_LIMIT_MIN:
                    n_r6_breach[final] += 1
            except Exception:
                pass

    total_orders = len(orders_action)
    override_orders = sum(1 for s in orders_action.values() if "PANEL_OVERRIDE" in s)
    override_baseline = override_orders / total_orders if total_orders else 0.0

    or_rates = []
    for cid, p in n_proposed.items():
        if p >= 30:
            or_rates.append(n_override.get(cid, 0) / p)
    or_rates.sort()
    median_or = or_rates[len(or_rates) // 2] if or_rates else 0.0

    return {
        "n_proposed": n_proposed,
        "n_override": n_override,
        "n_proposed_and_won": n_proposed_and_won,
        "n_actual": n_actual,
        "n_actual_was_proposed": n_actual_was_proposed,
        "n_actual_not_proposed": n_actual_not_proposed,
        "n_r6_total": n_r6_total,
        "n_r6_breach": n_r6_breach,
        "baseline_override_rate": override_baseline,
        "median_or": median_or,
        "total_orders": total_orders,
        "override_orders": override_orders,
    }


# ──────────────────────── classification ────────────────────────────────
def classify(prop, ov, r6_rate, r6_n, tier, baseline) -> str:
    """Tier-aware RELATIVE-TO-BASELINE classifier.

    Adrian's STRICT criterion `override<30% AND n>=50 AND r6<10%` jest
    reported per-entry jako boolean `meets_strict_original` — bucket
    używa relative-to-baseline (patrz `criteria_rationale`).
    """
    override_rate = ov / prop if prop else 0.0
    delta_baseline = baseline - override_rate

    if tier == "gold":
        win_threshold_pp, min_n = 0.10, 30
    elif tier == "std+":
        win_threshold_pp, min_n = 0.15, 50
    elif tier == "std":
        win_threshold_pp, min_n = 0.20, 80
    elif tier in ("new", "slow"):
        if prop >= 30 and override_rate < baseline:
            return "CONDITIONAL"
        return "INSUFFICIENT_DATA"
    else:
        win_threshold_pp, min_n = 0.20, 80

    if prop < 30:
        return "INSUFFICIENT_DATA"
    if r6_n >= 20 and r6_rate > 0.15:
        return "BLACKLIST"
    if prop >= min_n and delta_baseline >= win_threshold_pp:
        return "WHITELIST"
    if prop >= 30 and delta_baseline >= (win_threshold_pp - 0.05):
        return "CONDITIONAL"
    if delta_baseline <= -0.10:
        return "BLACKLIST"
    return "CONDITIONAL"


def meets_strict_original(prop, ov, r6_rate, r6_n) -> bool:
    if prop < 50:
        return False
    if ov / prop >= 0.30:
        return False
    if r6_n >= 20 and r6_rate > 0.10:
        return False
    return True


# ──────────────────────── build buckets ─────────────────────────────────
def build_buckets(agg: dict, metrics: dict, tiers_raw: dict, names: dict) -> dict:
    n_proposed = metrics["n_proposed"]
    n_override = metrics["n_override"]
    n_proposed_and_won = metrics["n_proposed_and_won"]
    n_actual = metrics["n_actual"]
    n_actual_was_proposed = metrics["n_actual_was_proposed"]
    n_actual_not_proposed = metrics["n_actual_not_proposed"]
    n_r6_total = metrics["n_r6_total"]
    n_r6_breach = metrics["n_r6_breach"]
    baseline = metrics["baseline_override_rate"]
    total_deliveries = agg["total_deliveries"]

    buckets = {"WHITELIST": [], "CONDITIONAL": [], "BLACKLIST": [], "INSUFFICIENT_DATA": []}
    all_cids = set(n_proposed.keys()) | set(n_actual.keys()) | set(total_deliveries.keys())

    for cid in all_cids:
        if _is_inactive(tiers_raw, cid):
            continue
        prop = n_proposed.get(cid, 0)
        ov = n_override.get(cid, 0)
        won = n_proposed_and_won.get(cid, 0)
        td = total_deliveries.get(cid, 0)
        r6_n = n_r6_total.get(cid, 0)
        r6_br = n_r6_breach.get(cid, 0)
        r6_rate = (r6_br / r6_n) if r6_n else 0.0
        override_rate = (ov / prop) if prop else 0.0
        tier = _tier_of(tiers_raw, cid) or "unknown"
        actual = n_actual.get(cid, 0)
        a_proposed = n_actual_was_proposed.get(cid, 0)
        a_not_prop = n_actual_not_proposed.get(cid, 0)
        operator_force_share = (a_not_prop / actual) if actual else 0.0

        bucket = classify(prop, ov, r6_rate, r6_n, tier, baseline)

        reasons = []
        if prop < 30:
            reasons.append(f"n_proposed<30 ({prop})")
        else:
            delta = baseline - override_rate
            sign = "better" if delta > 0 else "worse"
            reasons.append(
                f"override {override_rate*100:.1f}% vs baseline "
                f"{baseline*100:.1f}% ({sign} by {abs(delta)*100:.1f}pp)"
            )
        if r6_n >= 20:
            reasons.append(f"r6_breach {r6_rate*100:.1f}% (n={r6_n})")
        elif r6_n > 0:
            reasons.append(f"r6 sample low (n={r6_n})")
        if operator_force_share > 0.5 and actual >= 30:
            reasons.append(
                f"operator-favorite: {operator_force_share*100:.0f}% actuals not proposed"
            )

        entry = {
            "cid": cid,
            "name": _name_of(names, tiers_raw, cid),
            "tier": tier,
            "override_rate": round(override_rate, 4),
            "n_proposed": prop,
            "n_override": ov,
            "n_proposed_and_won": won,
            "total_deliveries_14d": td,
            "n_actual_delivered": actual,
            "actual_was_proposed": a_proposed,
            "actual_not_proposed": a_not_prop,
            "operator_force_share": round(operator_force_share, 4),
            "r6_breach_rate": round(r6_rate, 4),
            "r6_breach_n": r6_br,
            "r6_n_total": r6_n,
            "meets_strict_original": meets_strict_original(prop, ov, r6_rate, r6_n),
            "reason": "; ".join(reasons),
        }
        buckets[bucket].append(entry)

    def _sort_pos(e):
        return (1 - e["override_rate"]) * e["total_deliveries_14d"]

    buckets["WHITELIST"].sort(key=_sort_pos, reverse=True)
    buckets["CONDITIONAL"].sort(key=_sort_pos, reverse=True)
    buckets["BLACKLIST"].sort(key=lambda e: e["override_rate"], reverse=True)
    buckets["INSUFFICIENT_DATA"].sort(key=lambda e: e["total_deliveries_14d"], reverse=True)
    return buckets


# ──────────────────────── atomic write ──────────────────────────────────
def _atomic_write(path: str, content: str) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(p.parent), prefix=p.name + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w") as f:
            f.write(content)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


# ──────────────────────── markdown summary ──────────────────────────────
def write_markdown(buckets: dict, metrics: dict, md_path: str, days: int) -> None:
    baseline = metrics["baseline_override_rate"]
    median_or = metrics["median_or"]
    total_orders = metrics["total_orders"]
    override_orders = metrics["override_orders"]

    lines = []
    lines.append(f"# Courier Whitelist (auto-rebuilt) — last {days}d\n")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}\n")
    lines.append("\n## Executive summary\n")
    lines.append(
        f"- **Baseline PANEL_OVERRIDE: {baseline*100:.1f}%** "
        f"({override_orders}/{total_orders} unique orders)"
    )
    lines.append(
        f"- **Median override rate (couriers with n_proposed>=30): {median_or*100:.1f}%**"
    )
    lines.append(
        f"- Strict criterion `override<30% AND n_proposed>=50 AND r6<10%`: "
        f"**{sum(1 for e in buckets['WHITELIST'] if e['meets_strict_original'])} couriers qualify**\n"
    )
    lines.append("| Bucket | Count |")
    lines.append("|---|---:|")
    for b in ("WHITELIST", "CONDITIONAL", "BLACKLIST", "INSUFFICIENT_DATA"):
        lines.append(f"| **{b}** | {len(buckets[b])} |")

    def _table(rows):
        out = [
            "| cid | name | tier | override | n_prop | n_won | actual | r6_breach | strict? |",
            "|---|---|---|---:|---:|---:|---:|---:|:---:|",
        ]
        for e in rows[:30]:
            strict = "✓" if e["meets_strict_original"] else "—"
            r6 = (
                f"{e['r6_breach_rate']*100:.1f}% (n={e['r6_n_total']})"
                if e["r6_n_total"]
                else "n/a"
            )
            out.append(
                f"| {e['cid']} | {e['name']} | {e['tier']} | "
                f"{e['override_rate']*100:.1f}% | {e['n_proposed']} | "
                f"{e['n_proposed_and_won']} | {e['n_actual_delivered']} | {r6} | {strict} |"
            )
        return "\n".join(out)

    lines.append("\n## WHITELIST\n")
    lines.append(_table(buckets["WHITELIST"]) if buckets["WHITELIST"] else "_empty_")
    lines.append("\n## CONDITIONAL (top 30)\n")
    lines.append(_table(buckets["CONDITIONAL"]) if buckets["CONDITIONAL"] else "_empty_")
    lines.append("\n## BLACKLIST\n")
    lines.append(_table(buckets["BLACKLIST"]) if buckets["BLACKLIST"] else "_empty_")

    _atomic_write(md_path, "\n".join(lines))


# ──────────────────────── main / CLI ────────────────────────────────────
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Rebuild Faza 7 AUTO courier whitelist (relative-to-baseline, tier-aware).",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--days", type=int, default=14, help="Backfill window in days (default 14)")
    parser.add_argument("--out", default=DEFAULT_OUT, help="Output JSON path (default: dispatch_state/courier_whitelist_v1.json)")
    parser.add_argument("--md", default=None, help="Optional markdown summary path")
    parser.add_argument("--backfill", default=DEFAULT_BACKFILL, help="Backfill JSONL input")
    parser.add_argument("--load", default=DEFAULT_LOAD, help="Per-day load JSON input")
    parser.add_argument("--tiers", default=DEFAULT_TIERS, help="Courier tiers JSON path")
    parser.add_argument("--names", default=DEFAULT_NAMES, help="Courier names JSON path")
    parser.add_argument("--quiet", action="store_true", help="Suppress console summary")
    args = parser.parse_args(argv)

    if not os.path.exists(args.backfill):
        print(f"ERROR: backfill not found: {args.backfill}", file=sys.stderr)
        return 2

    tiers_raw = _load_json(args.tiers) if os.path.exists(args.tiers) else {}
    names = _load_json(args.names) if os.path.exists(args.names) else {}

    agg = aggregate(args.backfill, args.load, args.days)
    metrics = per_courier_metrics(agg)
    buckets = build_buckets(agg, metrics, tiers_raw, names)

    meta = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "window_days": args.days,
        "source_backfill": args.backfill,
        "source_load": args.load,
        "n_unique_orders": metrics["total_orders"],
        "panel_override_baseline_rate": round(metrics["baseline_override_rate"], 4),
        "median_override_rate_couriers_n_ge_30": round(metrics["median_or"], 4),
        "r6_limit_min": R6_LIMIT_MIN,
        "active_couriers_considered": sum(len(v) for v in buckets.values()),
        "criteria_rationale": (
            "Baseline PANEL_OVERRIDE rate is ~75-85% — Ziomek's proposals are "
            "overridden across the board. Adrian's strict <30% threshold yields "
            "zero whitelist. Recalibrated to RELATIVE-TO-BASELINE: WHITELIST = "
            "beat baseline by 10-20pp (tier-dependent). Each entry tagged "
            "`meets_strict_original` for the original <30% rule. "
            "L1 (Ziomek-proposal lens) drives bucketing; L2 (operator-trust "
            "lens) reported via actual_was_proposed/actual_not_proposed/"
            "operator_force_share. AUTO ramp-up via WHITELIST is NOT recommended "
            "until the dispatch-vs-operator divergence root cause is understood."
        ),
        "criteria": {
            "WHITELIST_gold": "n_proposed>=30 AND override_rate <= baseline-10pp AND r6<15%",
            "WHITELIST_std+": "n_proposed>=50 AND override_rate <= baseline-15pp AND r6<15%",
            "WHITELIST_std": "n_proposed>=80 AND override_rate <= baseline-20pp AND r6<15%",
            "CONDITIONAL": "30<=n_proposed AND override_rate <= baseline-(tier_threshold-5pp)",
            "BLACKLIST": "(r6_n>=20 AND r6>15%) OR override_rate >= baseline+10pp",
            "INSUFFICIENT_DATA": "n_proposed<30",
        },
        "lens": {
            "L1_Ziomek_proposal": "override_rate when proposed (drives buckets)",
            "L2_operator_trust": "actual delivery share (proxy of trust)",
        },
        "consumer_flag": "AUTO_PROXIMITY_COURIER_WHITELIST_FROM_FILE",
        "consumer_format": (
            "Classifier should iterate buckets['WHITELIST'] (list of dicts), "
            "extract cid, AND additionally check tier in {gold, std+} at runtime "
            "(belt-and-suspenders defense)."
        ),
    }

    out = {"_meta": meta, **buckets}
    _atomic_write(args.out, json.dumps(out, ensure_ascii=False, indent=2))

    if args.md:
        write_markdown(buckets, metrics, args.md, args.days)

    if not args.quiet:
        print("=== BUCKETS ===")
        for b in ("WHITELIST", "CONDITIONAL", "BLACKLIST", "INSUFFICIENT_DATA"):
            print(f"  {b}: {len(buckets[b])}")
        print(
            f"\nbaseline_override={metrics['baseline_override_rate']*100:.1f}%  "
            f"median={metrics['median_or']*100:.1f}%"
        )
        print(f"\nWrote: {args.out}")
        if args.md:
            print(f"Wrote: {args.md}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
