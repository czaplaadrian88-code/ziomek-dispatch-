#!/usr/bin/env python3
"""PKT 6 iter-2 — rozstrzyga handoff: ile breachy R6 da się odzyskać LEGALNIE (respektując
committed) vs tylko ŁAMIĄC committed (handoffowa górna granica 68% B_seq).
Real bags 06-13..17 (sla_log) → PROD engine. Tryby:
  A baseline = dziś (food-age OFF)
  B legit    = food-age ON + V3274 frozen-committed ON  (najlepsza świeżość RESPEKTUJĄC committed)
  C free     = food-age ON + V3274 OFF + V327 OFF       (ignoruje committed = górna granica)
recovery_legit = breach(A)-breach(B) ; recovery_free = breach(A)-breach(C).
Zero proxy (lekcja food-age). V3274 ON/OFF = wyznacznik legalności (nie zepsuty join)."""
import json, sys, statistics as st
from datetime import datetime, timezone
from collections import defaultdict
sys.path.insert(0, "/root/.openclaw/workspace/scripts")
import dispatch_v2.common as CC
from dispatch_v2 import common as C
from dispatch_v2 import geocoding
from dispatch_v2.route_simulator_v2 import simulate_bag_route_v2, OrderSim
from dispatch_v2.route_metrics import compute_plan_metrics
CC.V326_OR_TOOLS_TIME_LIMIT_MS = 200
DAYS = ["2026-06-13","2026-06-14","2026-06-15","2026-06-16","2026-06-17"]
SLA=35; SLA_LOG="/root/.openclaw/workspace/scripts/logs/sla_log.jsonl"
_rc={k.lower():v for k,v in json.load(open("/root/.openclaw/workspace/dispatch_state/restaurant_coords.json")).items() if isinstance(v,dict)}
def pdt(s):
    if not s: return None
    try:
        d=datetime.fromisoformat(s); return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except: return None
def rest_coord(name):
    v=_rc.get((name or "").lower())
    if v and v.get("lat"): return (float(v["lat"]),float(v.get("lon") or v.get("lng")))
    try:
        c=geocoding.geocode((name or "")+" Białystok",city="Białystok")
        if c and abs(c[0])>1: return (float(c[0]),float(c[1]))
    except: pass
    return None
def deliv_coord(addr):
    try:
        c=geocoding.geocode(addr,city="Białystok")
        if c and abs(c[0])>1: return (float(c[0]),float(c[1]))
    except: pass
    return None
def load_days():
    O={}
    for line in open(SLA_LOG):
        if not any(d in line for d in DAYS): continue
        try: d=json.loads(line)
        except: continue
        if (d.get("logged_at") or "")[:10] in DAYS and d.get("order_id"): O[d["order_id"]]=d
    by=defaultdict(list)
    for oid,d in O.items():
        pt,dt=pdt(d.get("picked_up_at")),pdt(d.get("delivered_at"))
        if pt and dt and dt>pt: by[(d.get("courier_id"),(d.get("logged_at") or "")[:10])].append((pt,dt,oid))
    bags=[]
    for k,lst in by.items():
        lst.sort(); i=0
        while i<len(lst):
            grp=[lst[i]]; mx=lst[i][1]; j=i+1
            while j<len(lst) and lst[j][0]<mx: grp.append(lst[j]); mx=max(mx,lst[j][1]); j+=1
            if 2<=len(grp)<=4: bags.append((k[0],[g[2] for g in grp]))
            i=j
    return O,bags
def set_mode(mode):
    CC.ENABLE_V327_TSP_TIME_WINDOWS=True; CC.ENABLE_V3274_FROZEN_PICKUP_WINDOW=True
    CC.ENABLE_OBJ_FOOD_AGE_HARD_SLA=False
    if mode=="legit": CC.ENABLE_OBJ_FOOD_AGE_HARD_SLA=True
    elif mode=="free": CC.ENABLE_OBJ_FOOD_AGE_HARD_SLA=True; CC.ENABLE_V3274_FROZEN_PICKUP_WINDOW=False; CC.ENABLE_V327_TSP_TIME_WINDOWS=False
def plan_for_bag(O,oids,mode):
    sims={}; ready0={}
    for o in oids:
        pc=rest_coord(O[o].get("restaurant")); dc=deliv_coord(O[o].get("delivery_address"))
        if pc is None or dc is None: return None
        r=pdt(O[o].get("picked_up_at")); ready0[o]=r
        sims[o]=OrderSim(o,pc,dc,None,"assigned",pickup_ready_at=r)
    start=min(oids,key=lambda o:ready0[o]); cp=sims[start].pickup_coords; now=ready0[start]
    set_mode(mode); best=None
    fa = mode in ("legit","free")
    for newo in oids:
        bag=[sims[o] for o in oids if o!=newo]
        if fa:
            with C.food_age_override(True): p=simulate_bag_route_v2(cp,bag,sims[newo],now=now,sla_minutes=SLA)
        else:
            p=simulate_bag_route_v2(cp,bag,sims[newo],now=now,sla_minutes=SLA)
        key=(p.sla_violations,round(p.total_duration_min,3))
        if best is None or key<best[0]: best=(key,p)
    set_mode("baseline")
    p=best[1]; m=compute_plan_metrics(p,1.0)
    return {"r6":m.get("r6_breach_count") or 0,"sla":p.sla_violations or 0,"mk":round(p.total_duration_min,1)}
def main():
    O,bags=load_days(); sys.stderr.write(f"[v2] zlecenia={len(O)} worki2-4={len(bags)}\n")
    n=0; base_breach=0; rl=0; rf=0; new_b=0; new_f=0; breach_bags=0
    for cid,oids in bags:
        try: A=plan_for_bag(O,oids,"baseline")
        except Exception as e: sys.stderr.write(f"skip {cid}: {e}\n"); continue
        if A is None: continue
        n+=1
        if A["r6"]>0:
            base_breach+=A["r6"]; breach_bags+=1
            try: B=plan_for_bag(O,oids,"legit"); F=plan_for_bag(O,oids,"free")
            except Exception: continue
            if B: rl+=max(0,A["r6"]-B["r6"]); new_b+=max(0,B["r6"]-A["r6"])
            if F: rf+=max(0,A["r6"]-F["r6"]); new_f+=max(0,F["r6"]-A["r6"])
        if n%40==0: sys.stderr.write(f"  done {n}\n")
    L=[f"=== PKT 6 iter-2 — legalna vs free recovery R6 (06-13..17, real bags, prod engine) ===",
       f"worki 2-4: n={n} | worki z breach: {breach_bags} | suma breachy baseline: {base_breach}","",
       f"  recovery_LEGIT (food-age ON, committed RESPEKTOWANY):  {rl} breachy odzyskane | nowe: {new_b}",
       f"  recovery_FREE  (committed ZIGNOROWANY = górna granica): {rf} breachy odzyskane | nowe: {new_f}","",
       "INTERPRETACJA:",
       f"  • legit≈0 → reorder NIE ratuje breachy bez łamania committed → reguły NIE są darmową dźwignią (spójne z food-age no-op).",
       f"  • free>>legit → handoffowy '68% B_seq' to głównie ŁAMANIE committed (nielegalne) → potwierdza caveat handoffu.",
       f"  • free≈legit≈0 → breachy STRUKTURALNE (B_load/flota), nie kolejność.",
       "BRAMKA dźwigni: recovery_legit istotnie >0 przy new_b≈0. KILL: legit≈0 (jak tu/16.05 prawdopodobnie)."]
    OUT="/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-18/route_constraints_replay_v2_result.txt"
    rep="\n".join(L); open(OUT,"w").write(rep+"\n"); print(rep)
if __name__=="__main__": main()
