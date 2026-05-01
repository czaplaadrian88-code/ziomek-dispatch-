"""V3.28 R-04 Graduation Schema v2.0 evaluator.

Peak-quality based courier tier suggestion engine. Phase 1 SHADOW ONLY:
- Reads schema config from /root/.openclaw/workspace/dispatch_state/r04_schema.json
- Computes per-courier peak metrics from events.db + learning_log.jsonl (30d window)
- Outputs tier_suggestions.json (per-cid replace) + appends tier_evolution.jsonl
- Flag-gated by ENABLE_R04_SHADOW (default ON, logging-only safe)
- ZERO scoring impact Phase 1 — courier_tiers.json remains source of truth

Run:
  python3 -m dispatch_v2.r04_evaluator
  python3 -m dispatch_v2.r04_evaluator --dry-run

Cron (Phase 2):
  0 3 * * * /root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.r04_evaluator
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sqlite3
import statistics
import sys
from dataclasses import dataclass, asdict, field
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")

EVENTS_DB = "/root/.openclaw/workspace/dispatch_state/events.db"
LEARNING_LOG = "/root/.openclaw/workspace/dispatch_state/learning_log.jsonl"
SCHEMA_PATH = "/root/.openclaw/workspace/dispatch_state/r04_schema.json"
COURIER_TIERS_PATH = "/root/.openclaw/workspace/dispatch_state/courier_tiers.json"
SUGGESTIONS_OUT = "/root/.openclaw/workspace/dispatch_state/tier_suggestions.json"
EVOLUTION_LOG = "/root/.openclaw/workspace/dispatch_state/tier_evolution.jsonl"

log = logging.getLogger(__name__)


# ────────────────────────────────────────────────────────────────────────────
# Data classes
# ────────────────────────────────────────────────────────────────────────────

@dataclass
class CourierMetrics:
    cid: str
    name: Optional[str]
    peak_deliveries_30d: int = 0
    off_peak_deliveries_30d: int = 0
    peak_active_days_30d: int = 0
    peak_speed_n: int = 0
    peak_speed_med_min: Optional[float] = None
    peak_speed_p25_min: Optional[float] = None
    peak_speed_p75_min: Optional[float] = None
    speed_data_completeness_pct: float = 0.0
    tg_negative_30d: int = 0
    days_since_first_delivery: Optional[int] = None


@dataclass
class TierSuggestion:
    cid: str
    name: Optional[str]
    current_tier: Optional[str]
    suggested_tier: Optional[str]
    tier_match: bool
    insufficient_data: bool
    insufficient_data_reason: Optional[str]
    gold_candidate: bool
    promotion_eligible: bool
    demotion_required: bool
    gates_evaluated: Dict[str, Any]
    metrics: Dict[str, Any]
    reasoning: str
    schema_version: str
    evaluated_at: str


# ────────────────────────────────────────────────────────────────────────────
# Schema + courier_tiers loaders
# ────────────────────────────────────────────────────────────────────────────

def load_schema(path: str = SCHEMA_PATH) -> Dict[str, Any]:
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def load_courier_tiers(path: str = COURIER_TIERS_PATH) -> Dict[str, Any]:
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        log.error(f"load_courier_tiers fail: {e}")
        return {}


# ────────────────────────────────────────────────────────────────────────────
# Metrics computation
# ────────────────────────────────────────────────────────────────────────────

def _to_warsaw_hour_day(iso_utc: str) -> Tuple[int, str]:
    dt = datetime.fromisoformat(iso_utc.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    w = dt.astimezone(WARSAW)
    return w.hour, w.date().isoformat()


def compute_courier_metrics(
    cid: str,
    name: Optional[str],
    schema: Dict[str, Any],
    db_path: str = EVENTS_DB,
    log_path: str = LEARNING_LOG,
    window_days: int = 30,
    now_utc: Optional[datetime] = None,
) -> CourierMetrics:
    """Compute peak-quality metrics dla single courier 30d window."""
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    cutoff = (now_utc - timedelta(days=window_days)).isoformat()
    peak_hours = set(schema.get("peak_window_warsaw_hours", []))

    m = CourierMetrics(cid=cid, name=name)

    # Pull all events for this courier in window from events.db
    conn = sqlite3.connect(db_path)
    try:
        cur = conn.execute(
            "SELECT order_id, event_type, created_at FROM events "
            "WHERE courier_id=? AND created_at>=? "
            "ORDER BY order_id, created_at",
            (cid, cutoff),
        )
        events_by_order: Dict[str, Dict[str, str]] = {}
        for oid, etype, ts in cur.fetchall():
            events_by_order.setdefault(oid, {})[etype] = ts
    except Exception as e:
        log.error(f"compute_courier_metrics events.db cid={cid}: {e}")
        events_by_order = {}
    finally:
        conn.close()

    peak_active_days: set = set()
    peak_speeds_min: List[float] = []
    speed_complete_count = 0
    first_delivery_day: Optional[str] = None

    for oid, evts in events_by_order.items():
        deliv_ts = evts.get("COURIER_DELIVERED")
        if not deliv_ts:
            continue
        try:
            hour, day = _to_warsaw_hour_day(deliv_ts)
        except Exception:
            continue
        if first_delivery_day is None or day < first_delivery_day:
            first_delivery_day = day
        if hour in peak_hours:
            m.peak_deliveries_30d += 1
            peak_active_days.add(day)
            pick_ts = evts.get("COURIER_PICKED_UP")
            if pick_ts:
                try:
                    pdt = datetime.fromisoformat(pick_ts.replace("Z", "+00:00"))
                    ddt = datetime.fromisoformat(deliv_ts.replace("Z", "+00:00"))
                    speed_min = (ddt - pdt).total_seconds() / 60.0
                    # Filter outliers (negative or absurd)
                    if 0 < speed_min < 60:
                        peak_speeds_min.append(speed_min)
                        speed_complete_count += 1
                except Exception:
                    pass
        else:
            m.off_peak_deliveries_30d += 1

    m.peak_active_days_30d = len(peak_active_days)
    m.peak_speed_n = len(peak_speeds_min)
    if peak_speeds_min:
        m.peak_speed_med_min = round(statistics.median(peak_speeds_min), 2)
        if len(peak_speeds_min) >= 4:
            qs = statistics.quantiles(peak_speeds_min, n=4)
            m.peak_speed_p25_min = round(qs[0], 2)
            m.peak_speed_p75_min = round(qs[2], 2)
    if m.peak_deliveries_30d > 0:
        m.speed_data_completeness_pct = round(100.0 * speed_complete_count / m.peak_deliveries_30d, 1)
    if first_delivery_day:
        try:
            fd = datetime.fromisoformat(first_delivery_day).replace(tzinfo=timezone.utc)
            m.days_since_first_delivery = max(0, (now_utc - fd).days)
        except Exception:
            pass

    # tg_negative_30d from learning_log
    try:
        tg_neg = 0
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                if d.get("ts", "") < cutoff:
                    continue
                action = d.get("action")
                if action not in ("INNY", "TG_REASON"):
                    continue
                bcid = str((d.get("decision") or {}).get("best", {}).get("courier_id") or "")
                if bcid == cid:
                    tg_neg += 1
        m.tg_negative_30d = tg_neg
    except FileNotFoundError:
        log.warning(f"learning_log not found: {log_path}")
    except Exception as e:
        log.error(f"tg_negative_30d cid={cid}: {e}")

    return m


# ────────────────────────────────────────────────────────────────────────────
# Gate evaluation
# ────────────────────────────────────────────────────────────────────────────

_OPS = {
    ">": lambda a, b: a > b,
    ">=": lambda a, b: a >= b,
    "<": lambda a, b: a < b,
    "<=": lambda a, b: a <= b,
    "==": lambda a, b: a == b,
}


def _eval_rule(rule: Dict[str, Any], metrics_dict: Dict[str, Any]) -> Tuple[bool, Any]:
    """Evaluate single threshold rule.

    Phase 1: rules with `sustained_days` flag are SUPPRESSED (return False) bo
    nie mamy 14d historical evolution data — tier_evolution.jsonl jest empty
    przy first run. Phase 2 will replay evolution log to validate sustained
    breaches before firing gate. For now sustained gates log-only (informational).
    """
    metric = rule["metric"]
    op = rule["op"]
    thr = rule["threshold"]
    val = metrics_dict.get(metric)
    if val is None:
        return False, None
    # Phase 1 sustained_days suppression
    if rule.get("sustained_days"):
        return False, val
    fn = _OPS.get(op)
    if fn is None:
        return False, val
    try:
        return bool(fn(val, thr)), val
    except Exception:
        return False, val


def _eval_gate_block(block: Optional[Dict[str, Any]], metrics_dict: Dict[str, Any]) -> Tuple[bool, List[Dict[str, Any]]]:
    """Returns (block_passed, per_rule_details). Empty/None block → False (no trigger)."""
    if not block or not block.get("rules"):
        return False, []
    operator = block.get("operator", "ALL").upper()
    results = []
    for rule in block["rules"]:
        passed, val = _eval_rule(rule, metrics_dict)
        results.append({
            "metric": rule["metric"],
            "op": rule["op"],
            "threshold": rule["threshold"],
            "value": val,
            "passed": passed,
            "sustained_days": rule.get("sustained_days"),
        })
    if operator == "ALL":
        block_passed = all(r["passed"] for r in results)
    elif operator == "ANY":
        block_passed = any(r["passed"] for r in results)
    else:
        block_passed = False
    return block_passed, results


def _check_insufficient_data(m: CourierMetrics, schema: Dict[str, Any]) -> Tuple[bool, Optional[str]]:
    cfg = schema.get("insufficient_data", {})
    reasons = []
    if m.peak_deliveries_30d < cfg.get("min_peak_deliveries_30d", 40):
        reasons.append(f"peak_deliveries={m.peak_deliveries_30d}<{cfg.get('min_peak_deliveries_30d', 40)}")
    if m.peak_active_days_30d < cfg.get("min_peak_active_days_30d", 5):
        reasons.append(f"peak_active_days={m.peak_active_days_30d}<{cfg.get('min_peak_active_days_30d', 5)}")
    if m.peak_deliveries_30d > 0 and m.speed_data_completeness_pct < cfg.get("min_speed_data_completeness_pct", 70.0):
        reasons.append(f"speed_completeness={m.speed_data_completeness_pct}%<{cfg.get('min_speed_data_completeness_pct', 70.0)}%")
    if reasons:
        return True, "; ".join(reasons)
    return False, None


def evaluate_courier_tier(
    cid: str,
    name: Optional[str],
    metrics: CourierMetrics,
    current_tier: Optional[str],
    schema: Dict[str, Any],
    now_iso: Optional[str] = None,
) -> TierSuggestion:
    """Evaluate tier suggestion based on metrics + schema."""
    if now_iso is None:
        now_iso = datetime.now(timezone.utc).isoformat()
    metrics_dict = asdict(metrics)
    insufficient, reason = _check_insufficient_data(metrics, schema)
    gates_evaluated: Dict[str, Any] = {}
    gold_candidate = False
    suggested = current_tier
    promotion = False
    demotion = False
    reasoning = ""

    if insufficient:
        suggested = current_tier
        reasoning = f"insufficient_data: {reason} → keep_current_tier"
    else:
        # Check gold_candidate flag (advisory, doesn't change suggestion)
        gc_block = schema.get("gold_candidate_flag", {}).get("triggers")
        gc_passed, gc_details = _eval_gate_block(gc_block, metrics_dict)
        gold_candidate = gc_passed
        gates_evaluated["gold_candidate"] = gc_details

        # Tier evaluation logic:
        # 1. If current is gold → check demotion only (auto_promotion_blocked)
        # 2. If current is std/std+/slow/new → check promotion (highest tier first), then demotion
        tiers = schema.get("tiers", {})
        tier_order_promote = ["standard_plus"]  # std → std+ only (gold blocked)
        # Note: slow → std promotion handled separately below

        if current_tier == "gold":
            # Demotion check only (auto_promotion blocked)
            dem_block = tiers.get("gold", {}).get("demotion_gates")
            dem_passed, dem_details = _eval_gate_block(dem_block, metrics_dict)
            gates_evaluated["gold_demotion"] = dem_details
            if dem_passed:
                suggested = tiers.get("gold", {}).get("demotion_target", "standard_plus")
                demotion = True
                reasoning = "gold demotion gates triggered"
            else:
                suggested = "gold"
                reasoning = "gold maintained (demotion gates not triggered, manual promotion required)"

        elif current_tier in ("standard", "std"):
            # Try promotion to std+
            stdp = tiers.get("standard_plus", {})
            prom_passed, prom_details = _eval_gate_block(stdp.get("promotion_gates"), metrics_dict)
            gates_evaluated["std_to_std_plus_promotion"] = prom_details
            if prom_passed:
                suggested = "standard_plus"
                promotion = True
                reasoning = "std → standard_plus promotion: all gates passed"
            else:
                # Check std demotion
                std_dem = tiers.get("standard", {}).get("demotion_gates")
                dem_passed, dem_details = _eval_gate_block(std_dem, metrics_dict)
                gates_evaluated["std_demotion"] = dem_details
                if dem_passed:
                    suggested = tiers.get("standard", {}).get("demotion_target", "slow")
                    demotion = True
                    reasoning = "std → slow demotion: tg_negative gate triggered"
                else:
                    suggested = "standard"
                    reasoning = "std maintained"

        elif current_tier in ("standard_plus", "std+"):
            # Check std+ demotion
            stdp_dem = tiers.get("standard_plus", {}).get("demotion_gates")
            dem_passed, dem_details = _eval_gate_block(stdp_dem, metrics_dict)
            gates_evaluated["std_plus_demotion"] = dem_details
            if dem_passed:
                suggested = tiers.get("standard_plus", {}).get("demotion_target", "standard")
                demotion = True
                reasoning = "std+ → std demotion: gates triggered"
            else:
                suggested = "standard_plus"
                reasoning = "std+ maintained"

        elif current_tier == "slow":
            slow_prom = tiers.get("slow", {}).get("promotion_gates")
            prom_passed, prom_details = _eval_gate_block(slow_prom, metrics_dict)
            gates_evaluated["slow_to_std_promotion"] = prom_details
            if prom_passed:
                suggested = tiers.get("slow", {}).get("promotion_target", "standard")
                promotion = True
                reasoning = "slow → std promotion"
            else:
                suggested = "slow"
                reasoning = "slow maintained"

        elif current_tier == "new":
            # Re-evaluate: if days_since_first >= 14 AND peak_deliveries >= 50 AND active_days >= 5 → standard
            days_ok = (metrics.days_since_first_delivery or 0) >= 14
            deliv_ok = metrics.peak_deliveries_30d >= 50
            days_active_ok = metrics.peak_active_days_30d >= 5
            gates_evaluated["new_graduation"] = {
                "days_since_first": metrics.days_since_first_delivery,
                "peak_deliveries": metrics.peak_deliveries_30d,
                "peak_active_days": metrics.peak_active_days_30d,
                "qualifies_for_standard": days_ok and deliv_ok and days_active_ok,
            }
            if days_ok and deliv_ok and days_active_ok:
                suggested = "standard"
                promotion = True
                reasoning = "new → standard graduation: 14+ days + qualifier metrics met"
            else:
                suggested = "new"
                reasoning = "new tier maintained (insufficient tenure or volume)"
        else:
            # Unknown current_tier → leave as-is, log
            suggested = current_tier
            reasoning = f"unknown current_tier '{current_tier}' — keep as-is"

    # Normalize tier names for match comparison: courier_tiers.json uses short
    # form ('std', 'std+'), schema uses long form ('standard', 'standard_plus').
    _NORM = {"std": "standard", "std+": "standard_plus"}
    cur_norm = _NORM.get(current_tier, current_tier)
    sug_norm = _NORM.get(suggested, suggested)
    tier_match = (cur_norm == sug_norm)
    return TierSuggestion(
        cid=cid,
        name=name,
        current_tier=current_tier,
        suggested_tier=suggested,
        tier_match=tier_match,
        insufficient_data=insufficient,
        insufficient_data_reason=reason,
        gold_candidate=gold_candidate,
        promotion_eligible=promotion,
        demotion_required=demotion,
        gates_evaluated=gates_evaluated,
        metrics=metrics_dict,
        reasoning=reasoning,
        schema_version=schema.get("_meta", {}).get("version", "2.0"),
        evaluated_at=now_iso,
    )


# ────────────────────────────────────────────────────────────────────────────
# Orchestrator
# ────────────────────────────────────────────────────────────────────────────

def evaluate_all(
    schema: Optional[Dict[str, Any]] = None,
    tiers_data: Optional[Dict[str, Any]] = None,
    db_path: str = EVENTS_DB,
    log_path: str = LEARNING_LOG,
    now_utc: Optional[datetime] = None,
) -> Dict[str, TierSuggestion]:
    if schema is None:
        schema = load_schema()
    if tiers_data is None:
        tiers_data = load_courier_tiers()
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)
    now_iso = now_utc.isoformat()

    suggestions: Dict[str, TierSuggestion] = {}
    for cid, info in tiers_data.items():
        if cid == "_meta":
            continue
        name = (info or {}).get("name")
        bag_info = (info or {}).get("bag", {}) or {}
        current_tier = bag_info.get("tier")
        # Normalize tier_label 'new' (V3.25 STEP C compatibility)
        tier_label = (info or {}).get("tier_label")
        if tier_label == "new":
            current_tier = "new"
        try:
            metrics = compute_courier_metrics(cid, name, schema, db_path, log_path, now_utc=now_utc)
            sugg = evaluate_courier_tier(cid, name, metrics, current_tier, schema, now_iso=now_iso)
            suggestions[cid] = sugg
        except Exception as e:
            log.error(f"evaluate cid={cid} fail: {e}")
    return suggestions


def write_suggestions(suggestions: Dict[str, TierSuggestion], path: str = SUGGESTIONS_OUT) -> None:
    """Atomic write: temp + fsync + rename."""
    out: Dict[str, Any] = {
        "_meta": {
            "schema_version": "2.0",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "count": len(suggestions),
        }
    }
    for cid, s in suggestions.items():
        out[cid] = asdict(s)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2, ensure_ascii=False)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, path)


def append_evolution(suggestions: Dict[str, TierSuggestion], evolution_path: str = EVOLUTION_LOG) -> int:
    """Append entries dla mismatched suggestions OR gold_candidate flag changes."""
    n = 0
    try:
        with open(evolution_path, "a", encoding="utf-8") as f:
            for cid, s in suggestions.items():
                if s.tier_match and not s.gold_candidate:
                    continue  # no change to log
                rec = {
                    "ts": s.evaluated_at,
                    "cid": cid,
                    "name": s.name,
                    "prev_tier": s.current_tier,
                    "suggested": s.suggested_tier,
                    "tier_match": s.tier_match,
                    "gold_candidate": s.gold_candidate,
                    "insufficient_data": s.insufficient_data,
                    "promotion_eligible": s.promotion_eligible,
                    "demotion_required": s.demotion_required,
                    "reasoning": s.reasoning,
                    "applied": False,
                    "phase": "shadow",
                    "schema_version": s.schema_version,
                }
                f.write(json.dumps(rec, ensure_ascii=False) + "\n")
                n += 1
    except Exception as e:
        log.error(f"append_evolution fail: {e}")
    return n


def main() -> int:
    parser = argparse.ArgumentParser(description="R-04 v2.0 graduation evaluator")
    parser.add_argument("--dry-run", action="store_true", help="compute + print, no write")
    parser.add_argument("--cid", help="single cid for debug")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] r04_evaluator: %(message)s",
    )

    schema = load_schema()
    tiers = load_courier_tiers()
    if args.cid:
        tiers = {args.cid: tiers.get(args.cid, {"bag": {"tier": "?"}})}

    suggestions = evaluate_all(schema=schema, tiers_data=tiers)
    log.info(f"Evaluated {len(suggestions)} couriers")

    # Summary counts
    cnt_match = sum(1 for s in suggestions.values() if s.tier_match)
    cnt_promotion = sum(1 for s in suggestions.values() if s.promotion_eligible)
    cnt_demotion = sum(1 for s in suggestions.values() if s.demotion_required)
    cnt_insufficient = sum(1 for s in suggestions.values() if s.insufficient_data)
    cnt_gold_cand = sum(1 for s in suggestions.values() if s.gold_candidate)
    log.info(
        f"Summary: match={cnt_match} promotion={cnt_promotion} demotion={cnt_demotion} "
        f"insufficient_data={cnt_insufficient} gold_candidates={cnt_gold_cand}"
    )

    if args.dry_run:
        for cid, s in suggestions.items():
            print(json.dumps({"cid": cid, "name": s.name, "current": s.current_tier,
                              "suggested": s.suggested_tier, "gold_candidate": s.gold_candidate,
                              "insufficient": s.insufficient_data,
                              "metrics_speed_med": s.metrics.get("peak_speed_med_min"),
                              "metrics_speed_p25": s.metrics.get("peak_speed_p25_min"),
                              "peak_deliv": s.metrics.get("peak_deliveries_30d"),
                              "tg_neg": s.metrics.get("tg_negative_30d"),
                              "reasoning": s.reasoning}, ensure_ascii=False))
        return 0

    write_suggestions(suggestions)
    n_evol = append_evolution(suggestions)
    log.info(f"Wrote {SUGGESTIONS_OUT} ({len(suggestions)} cids), appended {n_evol} evolution entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
