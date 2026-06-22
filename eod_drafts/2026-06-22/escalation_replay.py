#!/usr/bin/env python3
"""REPLAY ESKALACJI (Adrian D1): czy tier-2 (eskalująca kara od +6) dociska ogon
spóźnień odbioru vs obecna LINIOWA tier-1 — bez regresji SLA dostaw, bez blowupu
latencji. Trójstronnie per worek: baseline (bez committed) / liniowa (tier-1) /
eskalująca (tier-1+tier-2), tie-breaker (adopt vs baseline po SLA dostaw) na obu.
Read-only.
"""
import os, sys, json, sqlite3, re, time
from datetime import datetime, timezone, timedelta
sys.path.insert(0, "/root/.openclaw/workspace/scripts")
os.environ.setdefault("ENABLE_V326_OR_TOOLS_TSP", "1")
from dispatch_v2 import route_simulator_v2 as R
from dispatch_v2 import common as C

STATE = "/root/.openclaw/workspace/dispatch_state"
LOG = "/root/.openclaw/workspace/scripts/logs/plan_recheck.log"
orders_state = json.load(open(f"{STATE}/orders_state.json"))
db = sqlite3.connect(f"{STATE}/courier_api.db")

def pdt(s):
    if not s: return None
    d = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
def gps_at(cid, ts):
    e = int(ts.timestamp())
    r = list(db.execute("select lat,lon from gps_history where courier_id=? and recorded_at between ? and ? order by abs(recorded_at-?) limit 1",(str(cid),e-900,e+900,e)))
    return (float(r[0][0]), float(r[0][1])) if r else None
def cok(c): return isinstance(c,(list,tuple)) and len(c)==2 and c[0] not in (None,0,0.0) and c[1] not in (None,0,0.0)
def build(oids, fixed):
    sims={}
    for oid in oids:
        rec=orders_state.get(oid) or {}; dc=rec.get("delivery_coords"); pc=rec.get("pickup_coords")
        if not cok(dc) or not cok(pc): return None
        s=R.OrderSim(order_id=oid,pickup_coords=(float(pc[0]),float(pc[1])),delivery_coords=(float(dc[0]),float(dc[1])),picked_up_at=None,status="assigned",pickup_ready_at=pdt(rec.get("czas_kuriera_warsaw")))
        if fixed: s.czas_kuriera_warsaw=rec.get("czas_kuriera_warsaw")
        sims[oid]=s
    return sims
def sweep(pos, sims, now):
    ordered=list(sims.keys()); best=None
    for nw in ordered:
        bag=[sims[o] for o in ordered if o!=nw]
        p=R.simulate_bag_route_v2(pos,bag,sims[nw],now=now,sla_minutes=35,earliest_departure=None)
        k=(p.sla_violations,round(p.total_duration_min,3),tuple(p.sequence))
        if best is None or k<best[0]: best=(k,p)
    return best[1]
def lateness(plan, oids):
    out={}
    for oid in oids:
        ck=pdt((orders_state.get(oid) or {}).get("czas_kuriera_warsaw")); pu=plan.pickup_at.get(oid)
        if ck and pu: out[oid]=(pu-ck.astimezone(timezone.utc)).total_seconds()/60.0
    return out

# korpus (jak committed_propagation_replay)
seen=set(); corpus=[]
pat=re.compile(r"^(\S+ \S+) .*BAG_PLAN_GENERATED cid=(\d+) .*seq=\[([^\]]*)\]")
for line in open(LOG,encoding="utf-8",errors="ignore"):
    if "BAG_PLAN_GENERATED" not in line or "2026-06-2" not in line: continue
    m=pat.search(line)
    if not m: continue
    ts_s,cid,seq=m.groups()
    if not (ts_s.startswith("2026-06-21") or ts_s.startswith("2026-06-22")): continue
    oids=re.findall(r"'(\d+)'",seq)
    if len(oids)<2: continue
    key=(cid,frozenset(oids))
    if key in seen: continue
    seen.add(key)
    corpus.append((datetime.strptime(ts_s,"%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc),cid,oids))

rows=[]; t_lin=0.0; t_esc=0.0; none_cnt=0
for ts,cid,oids in corpus:
    if sum(1 for o in oids if (orders_state.get(o) or {}).get("czas_kuriera_warsaw"))<2: continue
    pos=gps_at(cid,ts)
    if pos is None: continue
    sb=build(oids,False); sf1=build(oids,True); sf2=build(oids,True)
    if sb is None or sf1 is None: continue
    try:
        pbase=sweep(pos,sb,ts)
        C.ENABLE_OBJ_COMMITTED_PICKUP_ESCALATION=False
        t0=time.time(); plin=sweep(pos,sf1,ts); t_lin+=time.time()-t0
        C.ENABLE_OBJ_COMMITTED_PICKUP_ESCALATION=True
        C.OBJ_COMMITTED_PICKUP_ESCALATION_T2_MIN=float(os.environ.get("T2","6.0"))
        t0=time.time(); pesc=sweep(pos,build(oids,True),ts); t_esc+=time.time()-t0
        C.ENABLE_OBJ_COMMITTED_PICKUP_ESCALATION=False
    except Exception as e:
        none_cnt+=1; continue
    if pbase is None or plin is None or pesc is None: none_cnt+=1; continue
    # tie-breaker: adopt vs baseline po SLA dostaw
    lin_use = plin.sla_violations<=pbase.sla_violations
    esc_use = pesc.sla_violations<=pbase.sla_violations
    rows.append({
        "cid":cid,"oids":oids,"n":len(oids),
        "lat_lin":lateness(plin if lin_use else pbase,oids),
        "lat_esc":lateness(pesc if esc_use else pbase,oids),
        "sla_base":pbase.sla_violations,
        "sla_lin":(plin.sla_violations if lin_use else pbase.sla_violations),
        "sla_esc":(pesc.sla_violations if esc_use else pbase.sla_violations),
        "seq_lin":tuple(plin.sequence),"seq_esc":tuple(pesc.sequence),
        "pu_lin":{k:v.isoformat() for k,v in plin.pickup_at.items()},
        "pu_esc":{k:v.isoformat() for k,v in pesc.pickup_at.items()},
    })

N=len(rows)
print(f"REPLAY ESKALACJI: {N} worków (≥2 committed, GPS)")
print(f"solver INFEASIBLE/None: {none_cnt}")
print(f"latencja sweep: liniowa {1000*t_lin/max(N,1):.0f} ms/worek | eskalująca {1000*t_esc/max(N,1):.0f} ms/worek (Δ {1000*(t_esc-t_lin)/max(N,1):+.0f})")
def flat(k): return [v for r in rows for v in r[k].values()]
ll,le=flat("lat_lin"),flat("lat_esc")
def pct(xs,op): return 100*sum(1 for x in xs if op(x))/len(xs) if xs else 0
def med(xs): xs=sorted(xs); n=len(xs); return (xs[n//2] if n%2 else (xs[n//2-1]+xs[n//2])/2) if xs else 0
def p90(xs): xs=sorted(xs); return xs[min(len(xs)-1,int(0.9*len(xs)))] if xs else 0
print(f"\nPUNKTUALNOŚĆ ODBIORU committed — LINIOWA (tier-1) vs ESKALUJĄCA (tier-1+2), oba po tie-breakerze:")
print(f"  odbiorów: {len(ll)}")
print(f"  ≤+5 min:   liniowa {pct(ll,lambda x:x<=5):.1f}%  → eskalująca {pct(le,lambda x:x<=5):.1f}%")
print(f"  >+10 min (ogon): liniowa {pct(ll,lambda x:x>10):.1f}%  → eskalująca {pct(le,lambda x:x>10):.1f}%")
print(f"  >+15 min (ogon): liniowa {pct(ll,lambda x:x>15):.1f}%  → eskalująca {pct(le,lambda x:x>15):.1f}%")
print(f"  mediana: {med(ll):+.1f} → {med(le):+.1f} | p90: {p90(ll):+.1f} → {p90(le):+.1f} | max: {max(ll):+.1f} → {max(le):+.1f}")
dsla_lin=sum(r["sla_lin"]-r["sla_base"] for r in rows)
dsla_esc=sum(r["sla_esc"]-r["sla_base"] for r in rows)
print(f"\nKOSZT SLA dostaw (po tie-breakerze, vs baseline): liniowa {dsla_lin:+d} | eskalująca {dsla_esc:+d}  (gwarancja ≤0)")
chg=sum(1 for r in rows if r["seq_lin"]!=r["seq_esc"] or r["pu_lin"]!=r["pu_esc"])
print(f"eskalacja zmieniła plan vs liniowa: {chg}/{N} worków")
print("\nPrzykłady gdzie eskalacja dodatkowo ścięła ogon:")
ex=[r for r in rows if r["lat_lin"] and r["lat_esc"] and max(r["lat_esc"].values())<max(r["lat_lin"].values())-1]
for r in sorted(ex,key=lambda r:max(r['lat_lin'].values())-max(r['lat_esc'].values()),reverse=True)[:6]:
    rs=[(orders_state.get(o) or {}).get("restaurant","?")[:12] for o in r["oids"]]
    print(f"  cid={r['cid']} {rs} late max {max(r['lat_lin'].values()):+.1f}→{max(r['lat_esc'].values()):+.1f} | SLA {r['sla_lin']}→{r['sla_esc']}")
