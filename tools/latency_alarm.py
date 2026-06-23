#!/usr/bin/env python3
"""latency_alarm — wykrywa regresję latencji decyzji Ziomka (rec#6, 2026-06-23).

READ-ONLY. Czyta latency_ms z shadow_decisions.jsonl, liczy percentyle w oknie i
ALARMUJE gdy p95 przekroczy próg LUB gdy nastąpił wzrost vs baseline. Powód
istnienia: regresja p50 ~375 ms → ~900 ms przeszła NIEZAUWAŻONA, bo latency_ms był
logowany ale nigdy nie alarmowany (audyt 2026-06-23).

Exit code 1 gdy ALARM (żeby timer/onfailure mógł zareagować). Domyślnie tylko
raport na stdout; wpięcie w timer + Telegram = osobny krok z ACK.

Uruchom:
  cd /root/.openclaw/workspace/scripts
  PYTHONPATH=. /root/.openclaw/venvs/dispatch/bin/python dispatch_v2/tools/latency_alarm.py --window-min 120 --p95-ms 2000 --p50-ms 1200
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
BASE = "/root/.openclaw/workspace"
SHADOW_LOGS = [
    f"{BASE}/scripts/logs/shadow_decisions.jsonl",
    f"{BASE}/scripts/logs/shadow_decisions.jsonl.1",
]


def _read_jsonl(path):
    if not os.path.exists(path):
        return
    with open(path, encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except (json.JSONDecodeError, ValueError):
                continue


def _parse_dt(s):
    if not s or not isinstance(s, str):
        return None
    try:
        return datetime.fromisoformat(s.strip().replace("Z", "+00:00"))
    except (ValueError, TypeError):
        return None


def _pctl(v, q):
    if not v:
        return None
    s = sorted(v)
    return s[min(len(s) - 1, int(len(s) * q))]


def _collect(cutoff, until=None):
    out = []
    for path in SHADOW_LOGS:
        for r in _read_jsonl(path):
            ts = _parse_dt(r.get("ts"))
            lm = r.get("latency_ms")
            if ts is None or not isinstance(lm, (int, float)):
                continue
            if ts < cutoff:
                continue
            if until is not None and ts >= until:
                continue
            out.append(lm)
    return out


def main():
    ap = argparse.ArgumentParser(description="Alarm regresji latencji decyzji (read-only).")
    ap.add_argument("--window-min", type=float, default=120.0, help="okno bieżące (min)")
    ap.add_argument("--p95-ms", type=float, default=1200.0, help="próg ALARM dla p95 (zdrowo ~624 ms)")
    ap.add_argument("--p50-ms", type=float, default=600.0, help="próg ALARM dla p50 (zdrowo ~375 ms)")
    ap.add_argument("--baseline-days", type=float, default=7.0, help="okno baseline do wykrycia wzrostu")
    ap.add_argument("--regress-factor", type=float, default=1.5, help="ALARM gdy p50 > regress_factor × baseline p50")
    args = ap.parse_args()

    now = datetime.now(WARSAW)
    cur = _collect(now - timedelta(minutes=args.window_min))
    base = _collect(now - timedelta(days=args.baseline_days), until=now - timedelta(minutes=args.window_min))

    print(f"[latency_alarm] {now.isoformat()}  okno={args.window_min:.0f} min")
    if not cur:
        print("  brak decyzji w oknie — nic do oceny (możliwe: noc / worker idle). Bez alarmu.")
        return 0

    p50, p90, p95, p99 = (_pctl(cur, q) for q in (0.5, 0.9, 0.95, 0.99))
    mx = max(cur)
    print(f"  n={len(cur)}  p50={p50:.0f}  p90={p90:.0f}  p95={p95:.0f}  p99={p99:.0f}  max={mx:.0f} ms")

    base_p50 = _pctl(base, 0.5) if base else None
    if base_p50:
        print(f"  baseline ({args.baseline_days:.0f}d, n={len(base)}): p50={base_p50:.0f} ms")

    alarms = []
    if p95 > args.p95_ms:
        alarms.append(f"p95 {p95:.0f} ms > próg {args.p95_ms:.0f}")
    if p50 > args.p50_ms:
        alarms.append(f"p50 {p50:.0f} ms > próg {args.p50_ms:.0f} (zdrowo ~375)")
    if base_p50 and p50 > args.regress_factor * base_p50:
        alarms.append(f"REGRESJA: p50 {p50:.0f} > {args.regress_factor}× baseline {base_p50:.0f}")

    if alarms:
        print("  🔴 ALARM:")
        for a in alarms:
            print(f"    - {a}")
        print("  (read-only; exit 1 dla timer/onfailure)")
        return 1
    print("  🟢 OK — latencja w normie.")
    print("  (read-only; zero wpływu na decyzje/stan)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
