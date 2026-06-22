#!/usr/bin/env python3
"""Odzyskiwalność no_gps v2 — z REALNEJ aktywności kuriera (2026-06-22, read-only).

v1 miała ślepą plamę: oś czasu z `best` widzi kuriera tylko gdy wygrał propozycję.
v2 używa eta_calibration_log: realne `delivered_at`/`picked_up_at` per
real_courier_id = kiedy kurier NAPRAWDĘ był w terenie (gdzie odbierał/doręczał).

Dla każdego momentu no_gps+empty (shadow, cid=best @ T_utc) pytam: czy ten kurier
miał realne doręczenie/odbiór w ostatnich W min przed T? Jeśli tak → łańcuch MIAŁ
świeżą pozycję do zakotwiczenia, a mimo to spadł do fikcji centrum = luka pokrycia.

TZ: delivered_at/picked_up_at = Warsaw naive (czerwiec = UTC+2) → −2h = UTC.
shadow ts = UTC. Fail-soft.
"""
import json
import os
from collections import defaultdict, Counter
from datetime import datetime, timezone, timedelta

SHADOW = [
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl.1",
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl",
]
ETA_CAL = "/root/.openclaw/workspace/dispatch_state/eta_calibration_log.jsonl"
WARSAW_OFFSET_H = 2  # czerwiec CEST
BUCKETS = [25, 45, 60, 90, 120]


def _utc_from_warsaw_naive(s):
    try:
        dt = datetime.strptime(str(s), "%Y-%m-%d %H:%M:%S")
        return (dt - timedelta(hours=WARSAW_OFFSET_H)).replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _utc_iso(s):
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except Exception:
        return None


def _pos_source(c):
    if not isinstance(c, dict):
        return None
    ps = c.get("pos_source")
    if ps is None and isinstance(c.get("metrics"), dict):
        ps = c["metrics"].get("pos_source")
    return ps


def _is_no_gps_empty(c):
    if _pos_source(c) != "no_gps":
        return False
    m = c.get("metrics") if isinstance(c.get("metrics"), dict) else c
    bsize = (m.get("r6_bag_size") or m.get("bag_size_before") or m.get("r7_bag_size") or 0)
    return (bsize or 0) == 0


def main():
    # 1. aktywność per kurier (UTC ts realnych odbiorów+doręczeń)
    activity = defaultdict(list)
    if os.path.exists(ETA_CAL):
        with open(ETA_CAL, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                cid = str(d.get("real_courier_id") or "")
                if not cid:
                    continue
                for k in ("picked_up_at", "delivered_at"):
                    t = _utc_from_warsaw_naive(d.get(k))
                    if t:
                        activity[cid].append(t)
    for cid in activity:
        activity[cid].sort()

    def last_activity_before(cid, t):
        ev = activity.get(cid)
        if not ev:
            return None
        prev = None
        for x in ev:
            if x < t:
                prev = x
            else:
                break
        return prev

    # 2. momenty no_gps+empty z shadow
    moments = []
    for p in SHADOW:
        if not os.path.exists(p):
            continue
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:
                    continue
                best = d.get("best") or {}
                if not _is_no_gps_empty(best):
                    continue
                t = _utc_iso(d.get("ts"))
                cid = str(best.get("courier_id"))
                if t and cid:
                    moments.append((cid, t))

    total = len(moments)
    rec = Counter()
    no_recent = 0
    no_activity_at_all = 0
    per_cid_recoverable = Counter()
    per_cid_total = Counter()
    cids_no_activity = Counter()
    for cid, t in moments:
        per_cid_total[cid] += 1
        if cid not in activity:
            no_activity_at_all += 1
            cids_no_activity[cid] += 1
            continue
        la = last_activity_before(cid, t)
        if la is None:
            no_recent += 1
            continue
        gap = (t - la).total_seconds() / 60.0
        placed = False
        for b in BUCKETS:
            if gap <= b:
                rec[b] += 1
                if b <= 60:
                    per_cid_recoverable[cid] += 1
                placed = True
                break
        if not placed:
            no_recent += 1

    print("=== ODZYSKIWALNOŚĆ no_gps v2 — z REALNEJ aktywności (eta_calibration_log) ===")
    print(f"momentów no_gps+empty: {total}")
    print(f"kurierów z jakąkolwiek aktywnością w logu: {len(activity)}")
    print()
    print("Czy kurier miał REALNE doręczenie/odbiór tuż przed momentem fikcji?")
    cum = 0
    for b in BUCKETS:
        cum += rec[b]
        print(f"  aktywność ≤{b:3d} min wstecz: +{rec[b]:4d}   (skumul. ≤{b}: {cum} = {100.0*cum/total:.1f}%)")
    print(f"  aktywność była, ale >120 min temu: {no_recent} ({100.0*no_recent/total:.1f}%)")
    print(f"  ZERO aktywności kuriera w całym logu: {no_activity_at_all} ({100.0*no_activity_at_all/total:.1f}%)")
    print()
    rec60 = rec[25] + rec[45] + rec[60]
    print(f">>> LUKA POKRYCIA (realna pozycja ≤60 min, a mimo to fikcja): "
          f"{rec60} ({100.0*rec60/total:.1f}%)")
    print(f">>> GENUINIE bez danych (zero aktywności): {no_activity_at_all} "
          f"({100.0*no_activity_at_all/total:.1f}%)")
    print()
    print("=== Dominanci fikcji — czy mieli aktywność (=łańcuch zignorował) ===")
    for cid, n in per_cid_total.most_common(10):
        act = len(activity.get(cid, []))
        recn = per_cid_recoverable.get(cid, 0)
        if act == 0:
            tag = "ZERO aktywności (operacyjne — zepsuty GPS/apka)"
        else:
            tag = f"{act} realnych zdarzeń w logu, {recn} fikcji odzyskiwalnych ≤60min → ŁAŃCUCH ZIGNOROWAŁ"
        print(f"  cid={cid:5s} fikcji={n:3d} | {tag}")


if __name__ == "__main__":
    main()
