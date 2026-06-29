#!/usr/bin/env python3
import json
from collections import Counter
from datetime import datetime, timedelta

DS="/root/.openclaw/workspace/dispatch_state"
SD="/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"

def parse(s):
    try: return datetime.fromisoformat(str(s).replace("Z","+00:00"))
    except: return None
def wday(ts):
    d=parse(ts); return (d+timedelta(hours=2)).strftime("%Y-%m-%d") if d else None
def inwin(ts):
    d=wday(ts); return d and "2026-06-10"<=d<="2026-06-16"

print("############ courier_match_debug.jsonl ############")
p=f"{DS}/courier_match_debug.jsonl"
n=0; keys=None; first=None; last=None
perdec=Counter()
import os
with open(p) as f:
    for line in f:
        if not line.strip(): continue
        try: r=json.loads(line)
        except: continue
        n+=1
        if keys is None:
            keys=list(r.keys())
            print("keys:",keys)
            print("SAMPLE:",json.dumps(r,ensure_ascii=False)[:1500])
        ts=r.get("ts") or r.get("now") or r.get("decision_ts")
        if first is None: first=ts
        last=ts
        # group key candidates
        ev=r.get("event_id") or r.get("order_id") or r.get("oid")
        if ev is not None: perdec[(ts if 'ts' in r else None, ev)] += 1
print(f"rows={n} first={first} last={last}")
mult=Counter(perdec.values())
print("rows-per-(ts,order) dist (top):", dict(sorted(mult.items())[:12]))

print("\n############ shadow_decisions: len(alternatives) + pool_total over window ############")
altlen=Counter(); pooltot=Counter(); poolfeas=Counter(); nwin=0
verdicts=Counter()
with open(SD) as f:
    for line in f:
        if not line.strip(): continue
        try: r=json.loads(line)
        except: continue
        ts=r.get("ts")
        if not inwin(ts): continue
        nwin+=1
        altlen[len(r.get("alternatives") or [])]+=1
        pooltot[r.get("pool_total_count")]+=1
        poolfeas[r.get("pool_feasible_count")]+=1
        verdicts[r.get("verdict")]+=1
print(f"shadow_decisions in-window records={nwin}")
print("len(alternatives) dist:", dict(sorted(altlen.items())))
print("verdict dist:", dict(verdicts))
print("pool_total_count dist (top12):", dict(sorted(pooltot.items(), key=lambda x:-x[1])[:12]))
print("pool_feasible_count dist:", dict(sorted(poolfeas.items())))

print("\n############ pending_pool_log.jsonl ############")
p=f"{DS}/pending_pool_log.jsonl"
n=0
with open(p) as f:
    for line in f:
        if not line.strip(): continue
        try: r=json.loads(line)
        except: continue
        n+=1
        if n==1:
            print("keys:",list(r.keys())); print("SAMPLE:",json.dumps(r,ensure_ascii=False)[:900])
        if n>=1: break
