#!/usr/bin/env python3
"""N1 plan-level per-day + severity (fresh bag_objs per solve). READ-ONLY."""
import json, sys
from datetime import datetime, timedelta, timezone
from collections import defaultdict
sys.path.insert(0,"/root/.openclaw/workspace/scripts")
from dispatch_v2.route_simulator_v2 import simulate_bag_route_v2, OrderSim
CAP="/root/.openclaw/workspace/dispatch_state/obj_replay_capture.jsonl"
DAYS=("2026-06-14","2026-06-15","2026-06-16"); WIN=65.0
def pts(s):
    try: return datetime.fromisoformat(str(s).replace("Z","+00:00")).astimezone(timezone.utc)
    except: return None
def wday(ts):
    d=pts(ts); return (d+timedelta(hours=2)).strftime("%Y-%m-%d") if d else None
def edue(o):
    t=pts(o.get("czas_kuriera_warsaw")) or pts(o.get("pickup_ready_at"))
    return (0,t) if t else (1,datetime(1,1,1,tzinfo=timezone.utc))
def mk(o):
    return OrderSim(order_id=str(o["order_id"]),pickup_coords=tuple(o["pickup_coords"]),
        delivery_coords=tuple(o["delivery_coords"]) if o.get("delivery_coords") else tuple(o["pickup_coords"]),
        picked_up_at=pts(o.get("picked_up_at")),status=o.get("status") or "assigned",
        pickup_ready_at=pts(o.get("pickup_ready_at")))
def aeq(a,b,t=1e-5): return abs(a[0]-b[0])<t and abs(a[1]-b[1])<t
def clm(plan,ck):
    if not plan or not plan.pickup_at: return None
    w=None
    for oid,c in ck.items():
        pat=plan.pickup_at.get(oid)
        if pat is None: continue
        l=(pat-c).total_seconds()/60.0
        if w is None or l>w: w=l
    return w
per=defaultdict(lambda: dict(chg=0,red=[],regr=[],unch=0,nbag2=0,nbag3=0))
regr_bag=defaultdict(int)
for line in open(CAP):
    try: d=json.loads(line)
    except: continue
    day=wday(d.get("now"))
    if day not in DAYS: continue
    bag=d.get("bag") or []
    assigned=[o for o in bag if o.get("status")=="assigned" and o.get("pickup_coords")]
    if [o for o in bag if o.get("status")=="picked_up"] or len(assigned)<2: continue
    cp=tuple(d["courier_pos"])
    if not any(aeq(tuple(o["pickup_coords"]),cp) for o in assigned): continue
    no=d.get("new_order") or {}; now=pts(d.get("now")); ready=pts(no.get("pickup_ready_at"))
    if now and ready and (ready-now).total_seconds()/60.0>WIN: continue
    na=min(assigned,key=edue); npos=tuple(na["pickup_coords"])
    if aeq(npos,cp): continue
    ck={str(o["order_id"]):pts(o["czas_kuriera_warsaw"]) for o in assigned if o.get("czas_kuriera_warsaw")}
    if not ck: continue
    try:
        o1=simulate_bag_route_v2(cp,[mk(o) for o in bag],mk(no),now=now)
        n1=simulate_bag_route_v2(npos,[mk(o) for o in bag],mk(no),now=now)
    except Exception: continue
    lo,ln=clm(o1,ck),clm(n1,ck)
    if lo is None or ln is None: continue
    P=per[day]; P["chg"]+=1
    nass=len(assigned)
    if nass>=3: P["nbag3"]+=1
    else: P["nbag2"]+=1
    delta=lo-ln
    if delta>0.05: P["red"].append(delta)
    elif delta<-0.05:
        P["regr"].append(-delta); regr_bag[nass]+=1
    else: P["unch"]+=1
print(f"{'day':<11}{'changed':>8}{'bag2':>6}{'bag3+':>6}{'reduced':>8}{'regressed':>10}{'unchg':>7}{'redSum':>8}{'regrSum':>8}{'regrMed':>8}{'regrMax':>8}")
T=defaultdict(float); Tred=[];Tregr=[]
for day in DAYS:
    P=per[day]; rs=sorted(P['regr'])
    print(f"{day:<11}{P['chg']:>8}{P['nbag2']:>6}{P['nbag3']:>6}{len(P['red']):>8}{len(P['regr']):>10}{P['unch']:>7}"
          f"{round(sum(P['red'])):>8}{round(sum(P['regr'])):>8}{(round(rs[len(rs)//2],1) if rs else 0):>8}{(round(max(rs),1) if rs else 0):>8}")
    for k in ('chg','unch','nbag2','nbag3'): T[k]+=P[k]
    Tred+=P['red']; Tregr+=P['regr']
rs=sorted(Tregr)
print(f"{'TOTAL':<11}{int(T['chg']):>8}{int(T['nbag2']):>6}{int(T['nbag3']):>6}{len(Tred):>8}{len(Tregr):>10}{int(T['unch']):>7}"
      f"{round(sum(Tred)):>8}{round(sum(Tregr)):>8}{(round(rs[len(rs)//2],1) if rs else 0):>8}{(round(max(rs),1) if rs else 0):>8}")
print("\nregressions by bag size (assigned count):", dict(sorted(regr_bag.items())))
print(f"net minutes (reduced - regressed): {round(sum(Tred)-sum(Tregr))} (reduced {round(sum(Tred))} - regressed {round(sum(Tregr))})")
