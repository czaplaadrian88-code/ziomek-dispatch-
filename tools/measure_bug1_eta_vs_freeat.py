#!/usr/bin/env python3
"""Pomiar bug #1 (read-only, zero wpływu): ile propozycji pokazuje czas odbioru
WCZEŚNIEJSZY niż kurier realnie wolny (target_pickup_at < free_at_utc), gdy kurier
ma niepusty worek. Wyświetlany ETA = `best.eta_pickup_hhmm`/`target_pickup_at`;
realny-najwcześniejszy = `best.free_at_utc` (kiedy skończy obecny worek).

Materialność = rozjazd (free_at − target_pickup) w minutach dla zajętych kurierów.
Czyta tylko shadow_decisions.jsonl. Użycie:
  measure_bug1_eta_vs_freeat.py [--since YYYY-MM-DD] [--log PATH]
"""
import argparse, json, sys
from datetime import datetime, timezone

LOG = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"

def parse(iso):
    if not iso:
        return None
    try:
        d = datetime.fromisoformat(str(iso).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except Exception:
        return None

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--since", default=None, help="YYYY-MM-DD (UTC) — tylko od tej daty")
    ap.add_argument("--log", default=LOG)
    ap.add_argument("--min-gap", type=float, default=5.0, help="próg materialności minut")
    a = ap.parse_args()
    since = parse(a.since + "T00:00:00+00:00") if a.since else None

    MAX_SANE = 180.0           # rozjazd > 180 min = zombie free_at (zepsuty picked_up_at)
    total_propose = 0          # wszystkie PROPOSE z best
    busy = 0                   # best ma niepusty worek (free_at_min > drive_min lub bag_context)
    bug = 0                    # target_pickup_at < free_at_utc, w granicy rozsądku
    bug_naive = 0              # j.w. ORAZ bug2_pickup_src=ready_time (naiwny floor = realny bug #1)
    material = 0               # rozjazd >= min-gap (po cap)
    zombie = 0                 # rozjazd > MAX_SANE (odfiltrowane)
    gaps = []                  # rozjazdy minut (bug, po cap)
    by_day = {}                # data -> [propose, busy, bug, material]
    worst = []                 # największe rozjazdy do podglądu

    with open(a.log, "r", encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if '"PROPOSE"' not in line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if d.get("verdict") != "PROPOSE":
                continue
            ts = parse(d.get("ts"))
            if since and (ts is None or ts < since):
                continue
            b = d.get("best")
            if not isinstance(b, dict):
                continue
            total_propose += 1
            day = (d.get("ts") or "")[:10]
            slot = by_day.setdefault(day, [0, 0, 0, 0])
            slot[0] += 1

            free_at = parse(b.get("free_at_utc"))
            tgt = parse(b.get("target_pickup_at"))
            free_min = b.get("free_at_min")
            drive_min = b.get("drive_min")
            bag = b.get("bag_context") or []
            is_busy = bool(bag) or (isinstance(free_min, (int, float)) and isinstance(drive_min, (int, float)) and free_min > drive_min + 1.0)
            if not is_busy:
                continue
            busy += 1
            slot[1] += 1
            if free_at is None or tgt is None:
                continue
            gap = (free_at - tgt).total_seconds() / 60.0
            if gap > MAX_SANE:
                zombie += 1
                continue
            if gap > 0.5:  # target wcześniejszy niż wolność
                bug += 1
                slot[2] += 1
                gaps.append(gap)
                if b.get("bug2_pickup_src") == "ready_time":
                    bug_naive += 1
                worst.append((gap, d.get("ts"), d.get("order_id"), d.get("restaurant"),
                              b.get("courier_id"), b.get("eta_pickup_hhmm"), b.get("bug2_pickup_src")))
                if gap >= a.min_gap:
                    material += 1
                    slot[3] += 1

    gaps.sort()
    n = len(gaps)
    med = gaps[n // 2] if n else 0.0
    p90 = gaps[int(n * 0.9)] if n else 0.0
    print(f"=== BUG #1 — wyświetlany odbiór WCZEŚNIEJ niż kurier wolny (read-only) ===")
    print(f"okno: {a.since or 'całość'} | log: {a.log}")
    print(f"PROPOSE z best:            {total_propose}")
    print(f"  z niepustym workiem:     {busy}  ({100*busy/max(1,total_propose):.1f}%)")
    print(f"  target_pickup < free_at: {bug}  ({100*bug/max(1,busy):.1f}% zajętych)  [zombie odfiltr.: {zombie}]")
    print(f"    z czego NAIVE ready_time (realny bug #1): {bug_naive}  ({100*bug_naive/max(1,busy):.1f}% zajętych)")
    print(f"    reszta = interleave plan.pickup_at (legalny przed/w trakcie — Twój scenariusz): {bug-bug_naive}")
    print(f"  rozjazd >= {a.min_gap:g} min:      {material}  ({100*material/max(1,busy):.1f}% zajętych)")
    print(f"  rozjazd minut (po cap {MAX_SANE:g}): median={med:.1f}  p90={p90:.1f}  max={gaps[-1] if gaps else 0:.1f}")
    print(f"\n--- per dzień (propose / busy / bug / material) ---")
    for day in sorted(by_day):
        s = by_day[day]
        print(f"  {day}: {s[0]:5d} / {s[1]:5d} / {s[2]:4d} / {s[3]:4d}")
    print(f"\n--- 8 największych rozjazdów ---")
    for gap, ts, oid, rest, cid, eta, src in sorted(worst, reverse=True)[:8]:
        print(f"  +{gap:5.1f}min  {ts[:19]}  oid={oid} {rest} cid={cid} pokazane={eta} src={src}")

if __name__ == "__main__":
    main()
