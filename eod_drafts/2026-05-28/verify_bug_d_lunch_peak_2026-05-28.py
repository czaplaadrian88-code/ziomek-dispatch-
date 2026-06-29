#!/usr/bin/env python3
"""BUG-D Faza 1+2a+2b lunch-peak verify (28.05 post-restart 06:29 UTC).

Cel: post lunch peak (~12:30 UTC = 14:30 Warsaw) sprawdzić czy:
1. shadow_decisions.jsonl ma świeże recordy z `traffic_v2_shadow_route` field
2. Hourly `OSRM traffic-mult-v2 hourly (shadow)` log line obecna w dispatch.log
3. Sample 3 v2_shadow_route entries (n_legs, avg_v2_mult, bins distribution)
4. Compose Telegram summary do Adriana

Triggered by `at` job 12:30 UTC. Cross-ref: commit b6acbd7 + sprint plan
eod_drafts/2026-05-26/SPRINT_PLAN_geometry_fairness_bugs.md.
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

SHADOW_LOG = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
DISPATCH_LOG = "/root/.openclaw/workspace/scripts/logs/dispatch.log"
# Restart timestamp pre-lunch (verified earlier).
RESTART_TS = datetime(2026, 5, 28, 6, 29, 11, tzinfo=timezone.utc)


def tail_jsonl(path: str, n: int = 200) -> list:
    """Tail N last JSONL records (cheap; uses `tail -n` shell call)."""
    try:
        out = subprocess.check_output(["tail", "-n", str(n), path], text=True)
    except Exception as e:
        return [{"_error": f"tail failed: {e}"}]
    records = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except Exception:
            continue
    return records


def _parse_iso(ts_str: str | None):
    if not ts_str:
        return None
    try:
        return datetime.fromisoformat(ts_str)
    except Exception:
        return None


def analyze_shadow(records: list) -> dict:
    """Per record post-restart: czy traffic_v2_shadow_route field obecny?"""
    post_restart = []
    for r in records:
        ts = _parse_iso(r.get("ts"))
        if ts is None or ts < RESTART_TS:
            continue
        post_restart.append(r)

    n = len(post_restart)
    if n == 0:
        return {"n_post_restart": 0, "v2_field_present": 0, "samples": []}

    v2_present = 0
    samples = []
    for r in post_restart:
        best = r.get("best") if isinstance(r.get("best"), dict) else None
        if best and "traffic_v2_shadow_route" in best:
            v2_present += 1
            v2_data = best["traffic_v2_shadow_route"]
            if v2_data and len(samples) < 3:
                samples.append({
                    "oid": r.get("order_id"),
                    "ts": r.get("ts"),
                    "n_legs": v2_data.get("n_legs"),
                    "avg_v2_mult": v2_data.get("avg_v2_mult"),
                    "max_v2_mult": v2_data.get("max_v2_mult"),
                    "min_v2_mult": v2_data.get("min_v2_mult"),
                    "bins": v2_data.get("bins_count"),
                    "v2_v1_delta_min": v2_data.get("v2_v1_delta_min"),
                })
    return {
        "n_post_restart": n,
        "v2_field_present": v2_present,
        "v2_field_present_pct": round(100 * v2_present / n, 1) if n else 0,
        "samples": samples,
    }


def analyze_hourly_log() -> dict:
    """Count `OSRM traffic-mult-v2 hourly` lines + parse latest."""
    try:
        out = subprocess.check_output(
            ["grep", "OSRM traffic-mult-v2 hourly", DISPATCH_LOG],
            text=True,
        )
    except subprocess.CalledProcessError:
        return {"count": 0, "latest": None}
    lines = [ln for ln in out.splitlines() if ln.strip()]
    if not lines:
        return {"count": 0, "latest": None}
    return {
        "count": len(lines),
        "latest": lines[-1][:300],
    }


def compose_summary(shadow: dict, hourly: dict) -> str:
    msg_lines = [
        "🔍 BUG-D Faza 1+2a+2b verify — lunch peak (2026-05-28)",
        f"Restart: 2026-05-28 06:29 UTC. Verify: {datetime.now(timezone.utc).strftime('%H:%M UTC')}",
        "",
        "## Shadow decisions post-restart",
        f"- Records: {shadow['n_post_restart']}",
    ]
    if shadow["n_post_restart"] > 0:
        msg_lines.append(
            f"- `traffic_v2_shadow_route` present: {shadow['v2_field_present']}/{shadow['n_post_restart']} "
            f"({shadow['v2_field_present_pct']}%)"
        )
        if shadow["samples"]:
            msg_lines.append("\n## Sample records:")
            for s in shadow["samples"]:
                msg_lines.append(
                    f"- oid={s['oid']} ts={s['ts'][11:19]} legs={s['n_legs']} "
                    f"avg_v2={s['avg_v2_mult']} max={s['max_v2_mult']} min={s['min_v2_mult']} "
                    f"bins={s['bins']} Δ_v2v1={s['v2_v1_delta_min']}min"
                )
        else:
            msg_lines.append("⚠ NO non-null traffic_v2_shadow_route samples — best=None paths only?")
    else:
        msg_lines.append("⚠ ZERO shadow records post-restart — sprawdź czy peak był aktywny + service running")

    msg_lines.extend([
        "",
        "## Hourly v2 stats log lines",
        f"- Total v2 hourly lines w dispatch.log: {hourly['count']}",
    ])
    if hourly["latest"]:
        msg_lines.append(f"- Latest: {hourly['latest']}")
    else:
        msg_lines.append("⚠ Brak hourly v2 stats line — sprawdź czy >1h od restartu i czy są OSRM calls")

    return "\n".join(msg_lines)


def send_telegram(msg: str) -> None:
    """Reuse telegram_utils.send_admin_alert (defense: log on fail)."""
    try:
        sys.path.insert(0, "/root/.openclaw/workspace/scripts")
        from dispatch_v2 import telegram_utils
        telegram_utils.send_admin_alert(msg)
    except Exception as e:
        # Fallback: write to local file dla manual review
        out = Path("/tmp/verify_bug_d_2026-05-28.log")
        out.write_text(f"telegram_send_fail: {e}\n\n{msg}\n")
        print(f"telegram fail: {e}; wrote /tmp/verify_bug_d_2026-05-28.log", file=sys.stderr)


def main() -> int:
    import argparse
    p = argparse.ArgumentParser()
    p.add_argument("--dry-run", action="store_true", help="Skip Telegram send (test mode)")
    args = p.parse_args()

    records = tail_jsonl(SHADOW_LOG, n=500)
    shadow = analyze_shadow(records)
    hourly = analyze_hourly_log()
    summary = compose_summary(shadow, hourly)
    print(summary)
    # Persist na disk dla audit trail
    log_path = Path("/tmp/verify_bug_d_2026-05-28.log")
    log_path.write_text(summary + "\n")
    if not args.dry_run:
        send_telegram(summary)
    else:
        print("\n[dry-run mode — Telegram alert NOT sent]")
    return 0


if __name__ == "__main__":
    sys.exit(main())
