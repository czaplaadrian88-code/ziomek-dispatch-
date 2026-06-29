#!/usr/bin/env python3
"""Inspect shadow_decisions.jsonl structure for one in-window record + alternatives schema."""
import json
from datetime import datetime, timedelta

SD = "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"

def parse(s):
    if not s: return None
    try: return datetime.fromisoformat(str(s).replace("Z","+00:00"))
    except: return None

def wday(ts):
    d=parse(ts)
    return (d+timedelta(hours=2)).strftime("%Y-%m-%d") if d else None

def keytypes(d):
    out={}
    for k,v in d.items():
        if isinstance(v,(list,)): out[k]=f"list[{len(v)}]"
        elif isinstance(v,dict): out[k]=f"dict({len(v)})"
        else: out[k]=type(v).__name__
    return out

found=0
with open(SD) as f:
    for line in f:
        if not line.strip(): continue
        try: r=json.loads(line)
        except: continue
        # find ts field
        ts = r.get("ts") or r.get("decision_ts") or r.get("now")
        d = wday(ts)
        if not d or d < "2026-06-12" or d > "2026-06-15": continue
        # want a record with alternatives and a low best score
        best = r.get("best") or {}
        alts = r.get("alternatives") or []
        if not alts: continue
        found+=1
        if found==1:
            print("=== TOP-LEVEL keys+types ===")
            print(json.dumps(keytypes(r), indent=1, ensure_ascii=False))
            print("\n=== order id / ts fields ===")
            for k in ("order_id","ts","decision_ts","now","verdict","restaurant","pool_total_count","pool_feasible_count"):
                if k in r: print(f"  {k} = {r[k]}")
            print(f"\n=== best keys ({len(best)}) ===")
            print(list(best.keys()))
            print("\n=== best core fields ===")
            for k in ("courier_id","score","tier","pos_source","pos","lat","lng","r6_max_bag_min","predicted_travel_min","late_pickup_tier","czas_odbioru","czas_kuriera","bag","feasible"):
                if k in best:
                    v=best[k]
                    print(f"  best.{k} = {json.dumps(v,ensure_ascii=False)[:200]}")
            print(f"\n=== alternatives: count={len(alts)} ; alt[0] keys ===")
            a0=alts[0]
            print(list(a0.keys()))
            print("\n=== alt[0] core fields ===")
            for k in ("courier_id","score","tier","pos_source","pos","lat","lng","r6_max_bag_min","predicted_travel_min","late_pickup_tier","feasible","bag","reason","demote","reject_reason"):
                if k in a0:
                    print(f"  alt0.{k} = {json.dumps(a0[k],ensure_ascii=False)[:200]}")
            print("\n=== all alt courier_id/score/feasible/tier (full pool this decision) ===")
            for a in alts:
                print(f"  cid={a.get('courier_id')} score={a.get('score')} feas={a.get('feasible')} lp_tier={a.get('late_pickup_tier')} pos_src={a.get('pos_source')} r6={a.get('r6_max_bag_min')} bag={len(a.get('bag') or []) if isinstance(a.get('bag'),list) else a.get('bag')}")
        if found>=1: break
print(f"\nfound_in_window={found}")
