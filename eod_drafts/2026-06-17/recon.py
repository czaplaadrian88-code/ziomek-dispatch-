#!/usr/bin/env python3
"""Read-only recon of audit data files. Streaming, Warsaw-day bucketed."""
import json, os, sys
from collections import Counter, defaultdict

WS = "/root/.openclaw/workspace"
BACKFILL = f"{WS}/dispatch_state/backfill_decisions_outcomes_v1.jsonl"
CAPTURE  = f"{WS}/dispatch_state/obj_replay_capture.jsonl"
KOORD    = f"{WS}/dispatch_state/auto_koord_log.jsonl"

def parse_ts(s):
    if s is None: return None
    s = str(s).replace("Z", "+00:00")
    try:
        from datetime import datetime
        return datetime.fromisoformat(s)
    except Exception:
        return None

def wday(ts):
    """UTC ts -> Warsaw date string (CEST=+2)."""
    from datetime import timedelta
    d = parse_ts(ts)
    if d is None: return None
    return (d + timedelta(hours=2)).strftime("%Y-%m-%d")

def inspect(path, ts_field_candidates, label):
    if not os.path.exists(path):
        print(f"[{label}] MISSING {path}"); return
    n=0; first=None; last=None; keys=None; daycount=Counter()
    tsf=None
    with open(path) as f:
        for line in f:
            line=line.strip()
            if not line: continue
            try: r=json.loads(line)
            except Exception: continue
            n+=1
            if keys is None:
                keys=list(r.keys())
                for c in ts_field_candidates:
                    if c in r: tsf=c; break
            ts = r.get(tsf) if tsf else None
            if ts:
                if first is None: first=ts
                last=ts
                d=wday(ts)
                if d: daycount[d]+=1
    print(f"\n===== [{label}] {path}")
    print(f"  rows={n}  ts_field={tsf}")
    print(f"  first_ts={first}")
    print(f"  last_ts ={last}")
    print(f"  keys({len(keys) if keys else 0})={keys}")
    print(f"  Warsaw-day counts (window 06-10..06-16):")
    for d in sorted(daycount):
        if "2026-06-1" in d and d>="2026-06-09":
            print(f"    {d}: {daycount[d]}")
    return tsf

# 1. backfill
tsf=inspect(BACKFILL, ["decision_ts","ts","action_event_ts"], "BACKFILL")
# sample one record fully
print("\n--- BACKFILL sample record (first) ---")
with open(BACKFILL) as f:
    for line in f:
        if line.strip():
            r=json.loads(line); print(json.dumps(r, indent=1, ensure_ascii=False)[:2500]); break

# 2. capture: is 1 row = 1 candidate or 1 decision?
inspect(CAPTURE, ["ts"], "CAPTURE")
print("\n--- CAPTURE: rows per (ts,order_id) for first 5000 rows ---")
grp=Counter(); seen=0
with open(CAPTURE) as f:
    for line in f:
        if not line.strip(): continue
        try: r=json.loads(line)
        except: continue
        grp[(r.get("ts"), r.get("order_id"))]+=1
        seen+=1
        if seen>=5000: break
mult=Counter(grp.values())
print(f"  rows_scanned={seen} distinct(ts,order_id)={len(grp)} rows-per-key dist={dict(sorted(mult.items()))}")
print("\n--- CAPTURE sample record (first) keys+head ---")
with open(CAPTURE) as f:
    for line in f:
        if line.strip():
            r=json.loads(line); print("keys:",list(r.keys())); print(json.dumps(r,indent=1,ensure_ascii=False)[:1800]); break

# 3. koord
inspect(KOORD, ["ts","decision_ts"], "KOORD")
print("\n--- KOORD sample ---")
with open(KOORD) as f:
    for line in f:
        if line.strip():
            r=json.loads(line); print("keys:",list(r.keys())); print(json.dumps(r,indent=1,ensure_ascii=False)[:1200]); break
