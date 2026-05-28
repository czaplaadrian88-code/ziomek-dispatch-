"""BUG-D Faza 2a — OSRM traffic multiplier v2 shadow stats report.

Parsuje `dispatch.log` (default `/root/.openclaw/workspace/scripts/logs/dispatch.log`)
dla linii `OSRM traffic-mult-v2 hourly (shadow|live): ...` emit'owanych przez
`osrm_client._maybe_log_stats()` co godzinę.

Output:
  - per-hour table z calls/avg_mult/bins per distance bucket (short/medium/long/none)
  - aggregated summary over window z weighted averages
  - comparison vs v1 (same window, `OSRM traffic-mult hourly`)

CLI:
  python3 -m dispatch_v2.tools.osrm_traffic_v2_stats [--hours N] [--log PATH] [--out PATH]

Default: last 24h, log `/root/.openclaw/workspace/scripts/logs/dispatch.log`, stdout.
"""
from __future__ import annotations

import argparse
import ast
import re
import sys
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

DEFAULT_LOG = "/root/.openclaw/workspace/scripts/logs/dispatch.log"

# Log line format (from osrm_client._maybe_log_stats):
# 2026-05-28 06:23:14 [INFO] osrm_client: OSRM traffic-mult-v2 hourly (shadow): calls=1247 avg_mult_v2=1.687 bins={'short': {'n': 412, 'avg': 2.31}, 'medium': {'n': 587, 'avg': 1.62}, ...}
LINE_TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
V2_LINE_RE = re.compile(
    r"OSRM traffic-mult-v2 hourly \((shadow|live)\): calls=(\d+) avg_mult_v2=([0-9.]+) bins=(\{.*\})\s*$"
)
V1_LINE_RE = re.compile(
    r"OSRM traffic-mult hourly \((shadow|live)\): calls=(\d+) avg_mult=([0-9.]+) buckets=(\{.*\})\s*$"
)


def _parse_log_ts(ts_str: str) -> datetime:
    """Parser: '2026-05-28 06:23:14' → aware datetime UTC (log emits UTC)."""
    dt = datetime.strptime(ts_str, "%Y-%m-%d %H:%M:%S")
    return dt.replace(tzinfo=timezone.utc)


def iter_lines(log_path: str, since: datetime) -> Iterator[tuple[datetime, str]]:
    """Yields (ts, line) for log entries newer than `since`."""
    p = Path(log_path)
    if not p.exists():
        return
    # Read whole file; rotate handled by logrotate (newest in main file)
    with p.open("r", encoding="utf-8", errors="ignore") as f:
        for line in f:
            m = LINE_TS_RE.match(line)
            if not m:
                continue
            try:
                ts = _parse_log_ts(m.group(1))
            except ValueError:
                continue
            if ts < since:
                continue
            yield ts, line.rstrip("\n")


def parse_v2_records(log_path: str, since: datetime) -> list[dict]:
    """Returns list of dicts: {ts, mode, calls, avg_mult, bins}."""
    out = []
    for ts, line in iter_lines(log_path, since):
        m = V2_LINE_RE.search(line)
        if not m:
            continue
        mode, calls_s, avg_s, bins_s = m.groups()
        try:
            bins = ast.literal_eval(bins_s)
        except (ValueError, SyntaxError):
            continue
        out.append({
            "ts": ts,
            "mode": mode,
            "calls": int(calls_s),
            "avg_mult": float(avg_s),
            "bins": bins,
        })
    return out


def parse_v1_records(log_path: str, since: datetime) -> list[dict]:
    """v1 baseline records for comparison."""
    out = []
    for ts, line in iter_lines(log_path, since):
        m = V1_LINE_RE.search(line)
        if not m:
            continue
        mode, calls_s, avg_s, buckets_s = m.groups()
        out.append({
            "ts": ts,
            "mode": mode,
            "calls": int(calls_s),
            "avg_mult": float(avg_s),
        })
    return out


def aggregate(records: list[dict]) -> dict:
    """Sum bins across records, compute weighted avg per bin."""
    bins_agg: dict[str, dict[str, float]] = defaultdict(lambda: {"n": 0, "weighted_sum": 0.0})
    total_calls = 0
    weighted_mult_sum = 0.0
    for r in records:
        total_calls += r["calls"]
        weighted_mult_sum += r["avg_mult"] * r["calls"]
        for bin_name, bin_data in r["bins"].items():
            n = bin_data.get("n", 0)
            avg = bin_data.get("avg", 0.0)
            if n > 0:
                bins_agg[bin_name]["n"] += n
                bins_agg[bin_name]["weighted_sum"] += avg * n
    result = {
        "total_calls": total_calls,
        "weighted_avg_mult": (weighted_mult_sum / total_calls) if total_calls else None,
        "bins": {
            name: {
                "n": int(data["n"]),
                "avg": round(data["weighted_sum"] / data["n"], 3) if data["n"] else None,
                "pct_of_total": round(100 * data["n"] / total_calls, 1) if total_calls else None,
            }
            for name, data in bins_agg.items()
        },
    }
    return result


def format_report(v2_records: list[dict], v1_records: list[dict], hours: int) -> str:
    lines = []
    lines.append(f"# OSRM Traffic Multiplier v2 Shadow Stats — Last {hours}h\n")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}\n")

    if not v2_records:
        lines.append("\n⚠ NO v2 records found in log window. Check:")
        lines.append("- dispatch-shadow service running (`systemctl is-active`)")
        lines.append("- BUG-D Faza 2a deployed (commit needed + restart for code to be live)")
        lines.append("- Hourly cycle elapsed (stats logged once per 3600s)")
        return "\n".join(lines)

    v2_agg = aggregate(v2_records)
    lines.append(f"\n## Aggregate v2 ({len(v2_records)} hourly snapshots)\n")
    lines.append(f"- Total OSRM calls: **{v2_agg['total_calls']:,}**")
    lines.append(f"- Weighted avg v2 multiplier: **{v2_agg['weighted_avg_mult']:.3f}**")
    if v1_records:
        v1_agg = aggregate([{"calls": r["calls"], "avg_mult": r["avg_mult"], "bins": {}} for r in v1_records])
        lines.append(f"- Weighted avg v1 multiplier (baseline): **{v1_agg['weighted_avg_mult']:.3f}**")
        if v1_agg['weighted_avg_mult']:
            delta_pct = (v2_agg['weighted_avg_mult'] - v1_agg['weighted_avg_mult']) / v1_agg['weighted_avg_mult'] * 100
            lines.append(f"- **v2 − v1 delta**: {delta_pct:+.1f}% (boost overall = {v2_agg['weighted_avg_mult'] - v1_agg['weighted_avg_mult']:+.3f})")

    lines.append("\n## Per-distance bin breakdown\n")
    lines.append("| Bin | n calls | % of total | avg v2 mult |")
    lines.append("|---|---:|---:|---:|")
    for bin_name in ("short", "medium", "long", "none"):
        b = v2_agg["bins"].get(bin_name, {})
        if not b.get("n"):
            lines.append(f"| {bin_name} | 0 | 0.0% | — |")
            continue
        lines.append(f"| {bin_name} | {b['n']:,} | {b['pct_of_total']}% | {b['avg']} |")

    lines.append("\n## Hourly timeline (most recent first)\n")
    lines.append("| Hour (UTC) | Mode | Calls | avg v2 | short | medium | long | none |")
    lines.append("|---|---|---:|---:|---:|---:|---:|---:|")
    for r in sorted(v2_records, key=lambda x: x["ts"], reverse=True)[:24]:
        bins = r["bins"]
        cells = []
        for bn in ("short", "medium", "long", "none"):
            bd = bins.get(bn) or {}
            cells.append(f"{bd.get('n', 0)}/{bd.get('avg', '—')}" if bd.get("n") else "—")
        lines.append(
            f"| {r['ts'].strftime('%Y-%m-%d %H:%M')} | {r['mode']} | "
            f"{r['calls']} | {r['avg_mult']:.3f} | " + " | ".join(cells) + " |"
        )

    lines.append("\n## Calibration check (vs TomTom sample 2026-05-26)")
    lines.append("\n| Bin | TomTom baseline ratio | v2 shadow avg | Recommendation |")
    lines.append("|---|---:|---:|---|")
    expected = {"short": 2.30, "medium": 1.50, "long": 1.15}
    for bin_name, baseline in expected.items():
        b = v2_agg["bins"].get(bin_name, {})
        if not b.get("avg"):
            lines.append(f"| {bin_name} | {baseline} | — | (no data yet) |")
            continue
        diff = b["avg"] - baseline
        verdict = "OK" if abs(diff) < 0.15 else f"adjust ({diff:+.2f})"
        lines.append(f"| {bin_name} | {baseline} | {b['avg']} | {verdict} |")

    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    p.add_argument("--hours", type=int, default=24, help="Log window in hours (default 24)")
    p.add_argument("--log", default=DEFAULT_LOG, help="Path to dispatch.log")
    p.add_argument("--out", default="-", help="Output file ('-' = stdout)")
    args = p.parse_args(argv)

    since = datetime.now(timezone.utc) - timedelta(hours=args.hours)
    v2_records = parse_v2_records(args.log, since)
    v1_records = parse_v1_records(args.log, since)
    report = format_report(v2_records, v1_records, args.hours)

    if args.out == "-":
        print(report)
    else:
        Path(args.out).write_text(report)
        print(f"Wrote: {args.out}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
