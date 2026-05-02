"""V3.28 Faza 6 LGBM Validation Gate Computation Pipeline.

Reads learning_log.jsonl entries z time window, computes agreement_rate
+ fallback distribution + per-tier breakdown + latency p50/p95.
Output: VERDICT GO / NO-GO / EXTENDED-OBS dla decyzji Faza 7 deploy.

Usage:
    /root/.openclaw/venvs/dispatch/bin/python3 \\
        /root/.openclaw/workspace/scripts/dispatch_v2/validation_gate_lgbm.py \\
        --since "2026-05-03 11:00" --until "2026-05-03 14:00"

    # Lub: today (default since 11:00, until 14:00 jeśli current >14:00, else now)
    /root/.openclaw/venvs/dispatch/bin/python3 \\
        /root/.openclaw/workspace/scripts/dispatch_v2/validation_gate_lgbm.py --today

VERDICT criteria:
  - GO: agreement_rate >= 75% AND valid_count >= 20 AND p95_latency <= 50ms
  - NO-GO: agreement_rate < 60% OR p95_latency > 100ms
  - EXTENDED-OBS: agreement 60-75% OR valid_count < 20

Input: learning_log.jsonl entries z `decision.best.lgbm_shadow` field.
Skip: TIMEOUT_SUPERSEDED, OPERATOR_COMMENT (no lgbm data).
Skip "valid" (NOT fallback): entries z fallback_reason != None lub != "all_bag_zero" są counted ale NOT valid dla agreement metric.
"""
import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


LEARNING_LOG_PATH = Path("/root/.openclaw/workspace/dispatch_state/learning_log.jsonl")


def parse_ts(ts_str: str) -> Optional[datetime]:
    if not ts_str:
        return None
    try:
        if ts_str.endswith("Z"):
            ts_str = ts_str[:-1] + "+00:00"
        return datetime.fromisoformat(ts_str)
    except (ValueError, TypeError):
        return None


def parse_arg_ts(arg: str) -> datetime:
    """Parse '2026-05-03 11:00' lub ISO format. Treats as Warsaw if no tz."""
    if not arg:
        return None
    try:
        dt = datetime.fromisoformat(arg)
        if dt.tzinfo is None:
            # Assume Warsaw (UTC+2 CEST May)
            dt = dt.replace(tzinfo=timezone(timedelta(hours=2)))
        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        try:
            dt = datetime.strptime(arg, "%Y-%m-%d %H:%M")
            dt = dt.replace(tzinfo=timezone(timedelta(hours=2)))
            return dt.astimezone(timezone.utc)
        except (ValueError, TypeError):
            return None


def load_entries(log_path: Path, since_utc: datetime, until_utc: datetime) -> List[Dict[str, Any]]:
    """Load learning_log entries w window. Filter dla lgbm_shadow data presence."""
    entries = []
    if not log_path.exists():
        print(f"ERROR: learning_log not found at {log_path}", file=sys.stderr)
        return entries
    try:
        with open(log_path, "r", encoding="utf-8") as f:
            for line in f:
                try:
                    d = json.loads(line)
                    ts = parse_ts(d.get("ts", ""))
                    if ts is None:
                        continue
                    if since_utc and ts < since_utc:
                        continue
                    if until_utc and ts >= until_utc:
                        continue
                    decision = d.get("decision", {}) or {}
                    best = decision.get("best", {}) or {}
                    lgbm = best.get("lgbm_shadow")
                    if not lgbm:
                        continue
                    entries.append({
                        "ts": ts,
                        "order_id": d.get("order_id"),
                        "current_winner_cid": str(best.get("courier_id") or ""),
                        "lgbm": lgbm,
                    })
                except Exception:
                    continue
    except (OSError, json.JSONDecodeError) as e:
        print(f"ERROR loading log: {e}", file=sys.stderr)
    return entries


def compute_metrics(entries: List[Dict[str, Any]]) -> Dict[str, Any]:
    total = len(entries)
    fallback_counts: Counter = Counter()
    valid_entries = []
    for e in entries:
        lgbm = e["lgbm"]
        fb = lgbm.get("fallback_reason")
        fallback_counts[fb or "NONE"] += 1
        # "Valid" = no fallback (LGBM faktycznie evaluated, NIE all_bag_zero etc.)
        if not fb and lgbm.get("enabled"):
            valid_entries.append(e)

    agreements = []
    latencies = []
    for e in valid_entries:
        lgbm = e["lgbm"]
        lgbm_winner = str(lgbm.get("winner_cid") or "")
        current_winner = e["current_winner_cid"]
        ag = lgbm.get("agreement_with_primary")
        if ag is None and lgbm_winner and current_winner:
            ag = (lgbm_winner == current_winner)
        if ag is not None:
            agreements.append(bool(ag))
        lat = lgbm.get("latency_ms")
        if lat is not None and lat > 0:
            latencies.append(float(lat))

    agreement_rate = (sum(agreements) / len(agreements) * 100) if agreements else 0.0
    p50 = p95 = 0.0
    if latencies:
        sorted_lat = sorted(latencies)
        p50 = sorted_lat[len(sorted_lat) // 2]
        p95 = sorted_lat[int(len(sorted_lat) * 0.95)] if len(sorted_lat) > 1 else sorted_lat[0]

    return {
        "total": total,
        "valid": len(valid_entries),
        "agreements_computed": len(agreements),
        "agreement_rate_pct": agreement_rate,
        "fallback_distribution": dict(fallback_counts),
        "latency_p50_ms": p50,
        "latency_p95_ms": p95,
        "valid_entries_sample_oids": [e["order_id"] for e in valid_entries[:5]],
    }


def compute_verdict(metrics: Dict[str, Any]) -> Tuple[str, str]:
    """Returns (verdict, reason)."""
    valid = metrics["valid"]
    agreement = metrics["agreement_rate_pct"]
    p95 = metrics["latency_p95_ms"]

    if valid < 20:
        return "EXTENDED-OBS", f"valid count {valid} < 20 minimum"
    if agreement < 60:
        return "NO-GO", f"agreement {agreement:.1f}% < 60% threshold"
    if p95 > 100:
        return "NO-GO", f"p95 latency {p95:.1f}ms > 100ms threshold"
    if agreement < 75:
        return "EXTENDED-OBS", f"agreement {agreement:.1f}% in [60, 75) — wait for more data"
    if p95 > 50:
        return "EXTENDED-OBS", f"p95 latency {p95:.1f}ms > 50ms — borderline"
    return "GO", f"agreement {agreement:.1f}% >= 75%, valid {valid} >= 20, p95 {p95:.1f}ms <= 50ms"


def main():
    ap = argparse.ArgumentParser(description="V3.28 Faza 6 LGBM Validation Gate Pipeline")
    ap.add_argument("--since", help="Start time (Warsaw, ISO format e.g. '2026-05-03 11:00')")
    ap.add_argument("--until", help="End time (Warsaw)")
    ap.add_argument("--today", action="store_true", help="Today 11:00-14:00 Warsaw (lunch peak default)")
    ap.add_argument("--all-today", action="store_true", help="Today since 00:00 Warsaw")
    ap.add_argument("--log-path", default=str(LEARNING_LOG_PATH), help="learning_log.jsonl path")
    args = ap.parse_args()

    if args.today:
        now_warsaw = datetime.now(timezone(timedelta(hours=2)))
        today_str = now_warsaw.strftime("%Y-%m-%d")
        since_utc = parse_arg_ts(f"{today_str} 11:00")
        until_utc = parse_arg_ts(f"{today_str} 14:00")
    elif args.all_today:
        now_warsaw = datetime.now(timezone(timedelta(hours=2)))
        today_str = now_warsaw.strftime("%Y-%m-%d")
        since_utc = parse_arg_ts(f"{today_str} 00:00")
        until_utc = datetime.now(timezone.utc)
    else:
        since_utc = parse_arg_ts(args.since) if args.since else None
        until_utc = parse_arg_ts(args.until) if args.until else datetime.now(timezone.utc)

    if since_utc is None:
        print("ERROR: --since required (or --today/--all-today)", file=sys.stderr)
        sys.exit(2)

    print(f"=== V3.28 Faza 6 LGBM Validation Gate ===")
    print(f"Window: {since_utc.isoformat()} → {until_utc.isoformat() if until_utc else 'now'}")
    print(f"Log: {args.log_path}")
    print()

    log_path = Path(args.log_path)
    entries = load_entries(log_path, since_utc, until_utc)
    metrics = compute_metrics(entries)
    verdict, reason = compute_verdict(metrics)

    print(f"Total entries z lgbm_shadow: {metrics['total']}")
    print(f"Valid (NOT fallback): {metrics['valid']}")
    print(f"Agreement computed: {metrics['agreements_computed']}/{metrics['valid']}")
    print(f"Agreement rate: {metrics['agreement_rate_pct']:.1f}%")
    print(f"Latency: p50={metrics['latency_p50_ms']:.1f}ms p95={metrics['latency_p95_ms']:.1f}ms")
    print()
    print(f"Fallback distribution:")
    for fb, count in sorted(metrics['fallback_distribution'].items(), key=lambda x: -x[1]):
        print(f"  {fb}: {count}")
    print()
    if metrics['valid_entries_sample_oids']:
        print(f"Valid sample OIDs: {metrics['valid_entries_sample_oids']}")
        print()
    print(f"=== VERDICT: {verdict} ===")
    print(f"Reason: {reason}")
    print()
    print(f"Decision criteria:")
    print(f"  GO: agreement_rate >= 75% AND valid_count >= 20 AND p95_latency <= 50ms")
    print(f"  NO-GO: agreement_rate < 60% OR p95_latency > 100ms")
    print(f"  EXTENDED-OBS: agreement 60-75% OR valid_count < 20 OR p95 50-100ms")

    return 0 if verdict == "GO" else (1 if verdict == "NO-GO" else 2)


if __name__ == "__main__":
    sys.exit(main())
