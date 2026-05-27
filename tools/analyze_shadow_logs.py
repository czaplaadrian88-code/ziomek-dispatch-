#!/usr/bin/env python3
"""analyze_shadow_logs — weekly summary z shadow logów Fazy 7 sprintów.

Czyta:
  - dispatch_state/drive_min_calibration_log_v2.jsonl (Sprint 1 shadow)
  - dispatch_state/c2_shadow_log.jsonl (existing)
  - dispatch_state/c5_shadow_log.jsonl (existing)
  - dispatch_state/carry_chain_shadow_log.jsonl (post Sprint 2.2)

Produkuje weekly summary do `/tmp/shadow_weekly_summary_YYYY-MM-DD.md`.

CLI:
  python3 -m dispatch_v2.tools.analyze_shadow_logs
  python3 -m dispatch_v2.tools.analyze_shadow_logs --days 7 --out /tmp/x.md

ZERO writes poza --out (atomic).
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

DEFAULT_PATHS = {
    "drive_calibration": "/root/.openclaw/workspace/dispatch_state/drive_min_calibration_log_v2.jsonl",
    "c2_shadow": "/root/.openclaw/workspace/dispatch_state/c2_shadow_log.jsonl",
    "c5_shadow": "/root/.openclaw/workspace/dispatch_state/c5_shadow_log.jsonl",
    "carry_chain": "/root/.openclaw/workspace/dispatch_state/carry_chain_shadow_log.jsonl",
}


# ──────────────────────── helpers ───────────────────────────────────────
def _iter_jsonl(path: str, cutoff: datetime | None):
    if not os.path.exists(path):
        return
    with open(path) as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except json.JSONDecodeError:
                continue
            if cutoff:
                ts_raw = d.get("ts") or d.get("timestamp") or d.get("decision_ts")
                if ts_raw:
                    try:
                        ts = datetime.fromisoformat(str(ts_raw).replace("Z", "+00:00"))
                        if ts < cutoff:
                            continue
                    except Exception:
                        pass
            yield d


def _median(xs):
    if not xs:
        return None
    s = sorted(xs)
    n = len(s)
    return s[n // 2] if n % 2 else (s[n // 2 - 1] + s[n // 2]) / 2


def _percentile(xs, p):
    if not xs:
        return None
    s = sorted(xs)
    idx = int(round(p * (len(s) - 1)))
    return s[idx]


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


# ──────────────────────── per-log analysis ──────────────────────────────
def analyze_drive_calibration(path: str, cutoff: datetime) -> dict:
    """Bias raw vs calibrated, per pos_source + per tier."""
    raw_deltas = []
    cal_deltas = []
    per_pos = defaultdict(lambda: {"raw": [], "cal": []})
    per_tier = defaultdict(lambda: {"raw": [], "cal": []})
    n_lines = 0
    for d in _iter_jsonl(path, cutoff):
        n_lines += 1
        raw = d.get("raw_predicted")
        cal = d.get("calibrated_predicted")
        actual = d.get("actual")
        if actual is None:
            continue
        pos = d.get("pos_source") or "unknown"
        tier = d.get("tier") or "unknown"
        if raw is not None:
            r = actual - raw
            raw_deltas.append(r)
            per_pos[pos]["raw"].append(r)
            per_tier[tier]["raw"].append(r)
        if cal is not None:
            c = actual - cal
            cal_deltas.append(c)
            per_pos[pos]["cal"].append(c)
            per_tier[tier]["cal"].append(c)
    return {
        "n_lines": n_lines,
        "n_raw": len(raw_deltas),
        "n_cal": len(cal_deltas),
        "median_raw": _median(raw_deltas),
        "median_cal": _median(cal_deltas),
        "p25_raw": _percentile(raw_deltas, 0.25),
        "p75_raw": _percentile(raw_deltas, 0.75),
        "p25_cal": _percentile(cal_deltas, 0.25),
        "p75_cal": _percentile(cal_deltas, 0.75),
        "per_pos": dict(per_pos),
        "per_tier": dict(per_tier),
    }


def analyze_generic_shadow(path: str, cutoff: datetime) -> dict:
    """Najprostszy summary: n, action/verdict distribution."""
    n = 0
    actions = Counter()
    verdicts = Counter()
    for d in _iter_jsonl(path, cutoff):
        n += 1
        if d.get("action"):
            actions[d["action"]] += 1
        if d.get("verdict"):
            verdicts[d["verdict"]] += 1
    return {"n": n, "actions": dict(actions), "verdicts": dict(verdicts)}


def analyze_carry_chain(path: str, cutoff: datetime) -> dict:
    """Carry chain shadow: triggered counts + flag distribution.

    Schema expected (Sprint 2.2 design):
      {ts, oid, courier_id, chain_depth, would_block: bool, reason}
    """
    n = 0
    would_block = 0
    depth_dist = Counter()
    reason_dist = Counter()
    for d in _iter_jsonl(path, cutoff):
        n += 1
        if d.get("would_block"):
            would_block += 1
        depth_dist[d.get("chain_depth", 0)] += 1
        if d.get("reason"):
            reason_dist[d["reason"]] += 1
    return {
        "n": n,
        "would_block": would_block,
        "would_block_rate": round(would_block / n, 4) if n else 0.0,
        "depth_distribution": dict(depth_dist),
        "reason_distribution": dict(reason_dist),
    }


# ──────────────────────── markdown render ───────────────────────────────
def render_md(date_str: str, days: int, drive, c2, c5, carry) -> str:
    lines = []
    lines.append(f"# Shadow logs weekly summary — {date_str}\n")
    lines.append(f"Generated: {datetime.now(timezone.utc).isoformat()}  (last {days}d)\n")

    lines.append("\n## 1. drive_min calibration (Sprint 1)\n")
    if drive["n_lines"]:
        lines.append(
            f"- entries: **{drive['n_lines']}** (raw deltas: {drive['n_raw']}, cal deltas: {drive['n_cal']})"
        )
        lines.append(
            f"- raw bias: median **{drive['median_raw']}**, p25 {drive['p25_raw']}, p75 {drive['p75_raw']}"
        )
        lines.append(
            f"- calibrated bias: median **{drive['median_cal']}**, p25 {drive['p25_cal']}, p75 {drive['p75_cal']}"
        )
        lines.append("\n### Per pos_source\n")
        lines.append("| pos_source | n_raw | median_raw | n_cal | median_cal |")
        lines.append("|---|---:|---:|---:|---:|")
        for ps, d in drive["per_pos"].items():
            mr = _median(d["raw"]) if d["raw"] else None
            mc = _median(d["cal"]) if d["cal"] else None
            lines.append(f"| {ps} | {len(d['raw'])} | {mr} | {len(d['cal'])} | {mc} |")
        lines.append("\n### Per tier\n")
        lines.append("| tier | n_raw | median_raw | n_cal | median_cal |")
        lines.append("|---|---:|---:|---:|---:|")
        for tier, d in drive["per_tier"].items():
            mr = _median(d["raw"]) if d["raw"] else None
            mc = _median(d["cal"]) if d["cal"] else None
            lines.append(f"| {tier} | {len(d['raw'])} | {mr} | {len(d['cal'])} | {mc} |")
    else:
        lines.append("_no entries — log path missing or Sprint 1 not LIVE_")

    for label, data in (("2. c2 shadow", c2), ("3. c5 shadow", c5)):
        lines.append(f"\n## {label}\n")
        if data["n"]:
            lines.append(f"- entries: **{data['n']}**")
            if data["actions"]:
                lines.append("- actions: " + ", ".join(f"{k}={v}" for k, v in data["actions"].items()))
            if data["verdicts"]:
                lines.append("- verdicts: " + ", ".join(f"{k}={v}" for k, v in data["verdicts"].items()))
        else:
            lines.append("_no entries in window_")

    lines.append("\n## 4. carry_chain shadow (Sprint 2.2)\n")
    if carry["n"]:
        lines.append(
            f"- entries: **{carry['n']}**, would_block: **{carry['would_block']}** "
            f"({carry['would_block_rate']*100:.1f}%)"
        )
        if carry["depth_distribution"]:
            lines.append("- depth dist: " + ", ".join(f"d{k}={v}" for k, v in sorted(carry["depth_distribution"].items())))
        if carry["reason_distribution"]:
            lines.append("- top reasons: " + ", ".join(f"{k}={v}" for k, v in list(carry["reason_distribution"].items())[:5]))
    else:
        lines.append("_no entries — Sprint 2.2 not LIVE yet_")

    return "\n".join(lines)


# ──────────────────────── main / CLI ────────────────────────────────────
def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Weekly summary of Faza 7 shadow logs",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--out", default=None)
    parser.add_argument("--drive-log", default=DEFAULT_PATHS["drive_calibration"])
    parser.add_argument("--c2-log", default=DEFAULT_PATHS["c2_shadow"])
    parser.add_argument("--c5-log", default=DEFAULT_PATHS["c5_shadow"])
    parser.add_argument("--carry-log", default=DEFAULT_PATHS["carry_chain"])
    parser.add_argument("--quiet", action="store_true")
    args = parser.parse_args(argv)

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(days=args.days)
    date_str = now.strftime("%Y-%m-%d")
    out_path = args.out or f"/tmp/shadow_weekly_summary_{date_str}.md"

    drive = analyze_drive_calibration(args.drive_log, cutoff)
    c2 = analyze_generic_shadow(args.c2_log, cutoff)
    c5 = analyze_generic_shadow(args.c5_log, cutoff)
    carry = analyze_carry_chain(args.carry_log, cutoff)

    md = render_md(date_str, args.days, drive, c2, c5, carry)
    _atomic_write(out_path, md)

    if not args.quiet:
        print(f"Wrote: {out_path}")
        print(f"drive_n={drive['n_lines']} c2_n={c2['n']} c5_n={c5['n']} carry_n={carry['n']}")

    return 0


if __name__ == "__main__":
    sys.exit(main())
