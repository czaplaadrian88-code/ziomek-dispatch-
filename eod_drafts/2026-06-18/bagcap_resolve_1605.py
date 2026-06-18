#!/usr/bin/env python3
"""B_load: ile realnych breachy R6 kasuje TWARDY cap worka per tier + koszt floty (przełożone zlecenia).
16.05 (dzień B_load, duże worki) — real bags sla_log → PROD engine. Zero proxy.
Dla worka > cap(tier): baseline (pełny) vs capped (zostają pierwsze `cap` wg pickup_ready) re-solve.
breach_removed = breach(pełny) − breach(capped) ; displaced = len−cap (koszt: ktoś inny musi wziąć)."""
import json, sys, statistics as st
from datetime import datetime, timezone
from collections import defaultdict
sys.path.insert(0,"/root/.openclaw/workspace/scripts")
import dispatch_v2.common as CC
from dispatch_v2 import geocoding
from dispatch_v2.route_simulator_v2 import simulate_bag_route_v2, OrderSim
from dispatch_v2.route_metrics import compute_plan_metrics
CC.V326_OR_TOOLS_TIME_LIMIT_MS=200
DAY="2026-05-16"; SLA=35
SLA_LOG="/root/.openclaw/workspace/scripts/logs/sla_log.jsonl"
DS="/root/.openclaw/workspace/dispatch_state"
_rc={k.lower():v for k,v in json.load(open(f"{DS}/restaurant_coords.json")).items() if isinstance(v,dict)}
T=json.load(open(f"{DS}/courier_tiers.json"))
def tof(v):
    if isinstance(v,dict): return (v.get("bag") or {}).get("tier") or v.get("tier") or v.get("tier_label")
    return v
cid2tier={k:tof(v) for k,v in T.items() if k!="_meta"}
# empiryczne capy (breach<20%)
CAP={"gold":6,"std+":6,"std":4,"slow":4,"new":4}
DEFAULT_CAP=4
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
            if len(grp)>=2: bags.append((cid,[g[2] for g in grp]))  # zachowaj kolejność pickup
            i=j
    return O,bags
def breaches(O,oids):
    sims={}; ready0={}
    for o in oids:
        pc=rest_coord(O[o].get("restaurant")); dc=deliv_coord(O[o].get("delivery_address"))
        if pc is None or dc is None: return None
        r=pdt(O[o].get("picked_up_at")); ready0[o]=r
        sims[o]=OrderSim(o,pc,dc,None,"assigned",pickup_ready_at=r)
    start=min(oids,key=lambda o:ready0[o]); cp=sims[start].pickup_coords; now=ready0[start]
    best=None
    for newo in oids:
        bag=[sims[o] for o in oids if o!=newo]
        p=simulate_bag_route_v2(cp,bag,sims[newo],now=now,sla_minutes=SLA)
        key=(p.sla_violations,round(p.total_duration_min,3))
        if best is None or key<best[0]: best=(key,p)
    return (compute_plan_metrics(best[1],1.0).get("r6_breach_count") or 0)
def main():
    O,bags=load_day(); sys.stderr.write(f"[bagcap] zlecenia={len(O)} worki={len(bags)}\n")
    overcap=0; base_br=0; cap_br=0; displaced=0; n=0
    small_rate={"gold":0.05,"std+":0.06,"std":0.09,"slow":0.14,"new":0.13}  # breach-rate w małym worku (z tabeli) dla est. losu przełożonych
    disp_breach_est=0.0
    for cid,oids in bags:
        tier=cid2tier.get(str(cid)) or "std"; cap=CAP.get(tier,DEFAULT_CAP)
        if len(oids)<=cap: continue
        kept=oids[:cap]  # pierwsze `cap` wg czasu odbioru
        try:
            bb=breaches(O,oids); cb=breaches(O,kept)
        except Exception as e: sys.stderr.write(f"skip {cid}: {e}\n"); continue
        if bb is None or cb is None: continue
        n+=1; overcap+=1; base_br+=bb; cap_br+=cb
        disp=len(oids)-cap; displaced+=disp
        disp_breach_est += disp*small_rate.get(tier,0.1)
        if n%10==0: sys.stderr.write(f"  done {n}\n")
    removed=base_br-cap_br
    L=[f"=== B_LOAD: twardy cap worka per tier — {DAY} (real bags → prod engine) ===",
       f"cap empiryczny: {CAP} | worki > cap: {overcap}",f"",
       f"  breachy R6 w workach-nad-capem (pełne):        {base_br}",
       f"  breachy R6 po ucięciu do capa (zostają):       {cap_br}",
       f"  >>> breachy USUNIĘTE u przeciążonego kuriera:  {removed}",f"",
       f"  zlecenia PRZEŁOŻONE (koszt floty — ktoś inny musi wziąć): {displaced}",
       f"  szac. breachy przełożonych gdyby szły w MAŁYM worku: ~{disp_breach_est:.1f}",
       f"  >>> NETTO breachy mniej (jeśli flota ma zapas): ~{removed-disp_breach_est:.1f}",f"",
       "INTERPRETACJA:",
       "  • removed = breachy znikające u przeładowanego kuriera po utwardzeniu capa.",
       "  • displaced = ile zleceń trzeba przekierować = wymagana nadwyżka floty/godzin.",
       "  • NETTO dodatnie TYLKO gdy flota ma zapas wziąć przełożone bez breacha (na 16.05 niedobór! → netto mniejsze).",
       "BRAMKA: removed>0 i displaced realistycznie wchłanialne. KILL: displaced ~= flota której nie ma → cap tylko przesuwa breach."]
    OUT="/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-18/bagcap_resolve_1605_result.txt"
    rep="\n".join(L); open(OUT,"w").write(rep+"\n"); print(rep)
if __name__=="__main__": main()
