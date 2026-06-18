#!/usr/bin/env python3
"""PKT 6 — test reguł trasy (14.06): które TWARDE ograniczenie blokuje świeższą kolejność.
Realne worki 14.06 z sla_log (REAL dowozy) przez PRODUKCYJNY silnik (simulate_bag_route_v2).
BASELINE (wszystkie ograniczenia ON) vs po wyłączeniu POJEDYNCZEGO ograniczenia:
  - V327 time-windows · V3273 wait-courier · V3274 frozen-committed-pickup
Liczy: breachy R6 odzyskane − nowe (regresja) − return-to-restaurant.
Committed (czas_kuriera_warsaw z obj_replay_capture) rozdziela LEGALNY blok od ZA-CIASNEGO okna.
NIE proxy: real bags + prod engine (lekcja food-age)."""
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
DAY="2026-06-14"; SLA=35
SLA_LOG="/root/.openclaw/workspace/scripts/logs/sla_log.jsonl"
CAP="/root/.openclaw/workspace/dispatch_state/obj_replay_capture.jsonl"
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

def load_day():
    O={}
    for line in open(SLA_LOG):
        if DAY not in line: continue
        try: d=json.loads(line)
        except: continue
        if (d.get("logged_at") or "")[:10]==DAY and d.get("order_id"): O[d["order_id"]]=d
    by=defaultdict(list)
    for oid,d in O.items():
        pt,dt=pdt(d.get("picked_up_at")),pdt(d.get("delivered_at"))
        if pt and dt and dt>pt: by[d.get("courier_id")].append((pt,dt,oid))
    bags=[]
    for cid,lst in by.items():
        lst.sort(); i=0
        while i<len(lst):
            grp=[lst[i]]; mx=lst[i][1]; j=i+1
            while j<len(lst) and lst[j][0]<mx: grp.append(lst[j]); mx=max(mx,lst[j][1]); j+=1
            if len(grp)>=2: bags.append((cid,[g[2] for g in grp]))
            i=j
    return O,bags

def committed_map():
    m={}
    for l in open(CAP):
        if DAY not in l[:40] and DAY not in l: continue
        try: d=json.loads(l)
        except: continue
        no=d.get("new_order") or {}
        oid=no.get("order_id"); ck=no.get("czas_kuriera_warsaw")
        if oid and ck and oid not in m: m[oid]=ck
    return m

# trzy ograniczenia do przełączania
TOGGLES=[("baseline",None),
         ("V327_timewindows_OFF","ENABLE_V327_TSP_TIME_WINDOWS"),
         ("V3273_wait_OFF","ENABLE_V3273_WAIT_COURIER_PENALTY"),
         ("V3274_frozen_committed_OFF","ENABLE_V3274_FROZEN_PICKUP_WINDOW")]

def set_flags(disabled):
    for f in ("ENABLE_V327_TSP_TIME_WINDOWS","ENABLE_V3273_WAIT_COURIER_PENALTY","ENABLE_V3274_FROZEN_PICKUP_WINDOW"):
        setattr(CC,f,True)
    if disabled: setattr(CC,disabled,False)

def plan_for_bag(O,oids,disabled):
    sims={}; ready0={}
    for o in oids:
        pc=rest_coord(O[o].get("restaurant")); dc=deliv_coord(O[o].get("delivery_address"))
        if pc is None or dc is None: return None
        r=pdt(O[o].get("picked_up_at")); ready0[o]=r
        sims[o]=OrderSim(o,pc,dc,None,"assigned",pickup_ready_at=r)
    start=min(oids,key=lambda o:ready0[o]); cp=sims[start].pickup_coords; now=ready0[start]
    set_flags(disabled)
    best=None
    for newo in oids:
        bag=[sims[o] for o in oids if o!=newo]
        p=simulate_bag_route_v2(cp,bag,sims[newo],now=now,sla_minutes=SLA)
        key=(p.sla_violations,round(p.total_duration_min,3))
        if best is None or key<best[0]: best=(key,p)
    set_flags(None)
    p=best[1]; m=compute_plan_metrics(p,1.0)
    return {"r6":m.get("r6_breach_count") or 0,"thermal":m.get("max_thermal_age_min"),
            "mk":round(p.total_duration_min,1),"sla":p.sla_violations or 0,
            "ret":1 if getattr(p,"return_to_restaurant",False) else 0}

def main():
    O,bags=load_day(); CK=committed_map()
    bags=[(c,o) for c,o in bags if 2<=len(o)<=4]
    sys.stderr.write(f"[1406] zlecenia={len(O)} worki2-4={len(bags)} committed-map={len(CK)}\n")
    res={name:{"recov":0,"new":0,"ret":0,"recov_committed":0,"recov_free":0} for name,_ in TOGGLES if name!="baseline"}
    base_breach=0; n=0
    for cid,oids in bags:
        try:
            base=plan_for_bag(O,oids,None)
            if base is None: continue
            toggled={name:plan_for_bag(O,oids,fl) for name,fl in TOGGLES if name!="baseline"}
        except Exception as e:
            sys.stderr.write(f"skip {cid}: {type(e).__name__}: {e}\n"); continue
        n+=1
        if base["r6"]>0: base_breach+=1
        has_committed=any(o in CK for o in oids)
        for name,t in toggled.items():
            if t is None: continue
            if t["r6"]<base["r6"]:
                res[name]["recov"]+=1
                res[name]["recov_committed" if has_committed else "recov_free"]+=1
            if t["r6"]>base["r6"]: res[name]["new"]+=1
            if t["ret"]>base["ret"]: res[name]["ret"]+=1
        if n%20==0: sys.stderr.write(f"  done {n}\n")
    L=[f"=== PKT 6 — test reguł trasy {DAY} (real bags sla_log → prod engine, OR-Tools 200ms) ===",
       f"worki 2-4 ocenione: n={n} | z R6-breach w baseline: {base_breach} | committed-map z capture: {len(CK)}","",
       "Wyłączenie POJEDYNCZEGO ograniczenia — ile breachy R6 odzyskuje (NET = recov−new):",""]
    for name in res:
        r=res[name]; net=r["recov"]-r["new"]
        L.append(f"  {name:30s} recov={r['recov']:2d} (free-window={r['recov_free']} / committed-blocked={r['recov_committed']}) | new={r['new']} | return-to-rest={r['ret']} | NET={net:+d}")
    L+=["",
        "Legenda: free-window = worek BEZ committed → odzysk z 'za ciasnego okna' (KANDYDAT do rozluźnienia).",
        "         committed-blocked = worek z committed czas_kuriera → odzysk złamałby zobowiązanie (NIE ruszać).",
        "BRAMKA: rozluźnienie z NET>0 i recov_free>0 przy new≈0 i return-to-rest=0 → realna dźwignia.",
        "KILL: jeśli cały recov to committed-blocked, albo new≥recov → to NIE jest darmowa dźwignia (jak food-age)."]
    OUT="/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-18/route_constraints_replay_1406_result.txt"
    rep="\n".join(L); open(OUT,"w").write(rep+"\n"); print(rep)

if __name__=="__main__": main()
