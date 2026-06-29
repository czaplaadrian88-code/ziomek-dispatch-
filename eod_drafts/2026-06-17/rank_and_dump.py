#!/usr/bin/env python3
"""First-pass ranking from backfill + detailed field dump for a few weak cases."""
import json
from datetime import datetime, timedelta

WS="/root/.openclaw/workspace"
BF=f"{WS}/dispatch_state/backfill_decisions_outcomes_v1.jsonl"
SD=f"{WS}/scripts/logs/shadow_decisions.jsonl"

def parse(s):
    try: return datetime.fromisoformat(str(s).replace("Z","+00:00"))
    except: return None
def wday(ts):
    d=parse(ts); return (d+timedelta(hours=2)).strftime("%Y-%m-%d") if d else None
def inwin(ts):
    d=wday(ts); return bool(d and "2026-06-10"<=d<="2026-06-16")

# 1) load backfill window
rows=[]
with open(BF) as f:
    for line in f:
        if not line.strip(): continue
        try: r=json.loads(line)
        except: continue
        if not inwin(r.get("decision_ts")): continue
        rows.append(r)
print(f"backfill window rows={len(rows)}")

def num(x,d=0.0):
    try: return float(x)
    except: return d

# weakness/hardness composite
def score_weak(r):
    o=r.get("outcome") or {}
    s=0.0
    ps=num(r.get("proposed_score"),0)
    if ps<0: s+= min(300, -ps)          # how negative
    if (r.get("pool_feasible") or 0)<=1: s+=40
    s+= max(0, num(r.get("predicted_r6_max_bag_min"))-35)*3
    a2d=num(o.get("assign_to_delivery_min"))
    if a2d: s+= max(0, a2d-35)*2
    if r.get("best_effort"): s+=30
    if r.get("shift_end_edge"): s+=15
    if r.get("czasowka"): s+=10
    if o.get("status") and o.get("status")!="delivered": s+=60
    af=o.get("courier_id_final"); pc=r.get("proposed_courier_id")
    if af and pc and str(af)!=str(pc): s+=25
    if r.get("action")=="TIMEOUT_SUPERSEDED": s+=10
    return s

for r in rows: r["_w"]=score_weak(r)
rows.sort(key=lambda r:-r["_w"])

def oid_int(r):
    try: return int(str(r.get("order_id")))
    except: return -1
def e2sig(r):
    oi=oid_int(r); return oi>=0 and oi%5==0

print("\n=== TOP 25 weakest/hardest (backfill) ===")
print(f"{'oid':>7} {'day':>5} {'w':>5} {'pscore':>8} {'feas':>5} {'tot':>4} {'pr6':>5} {'a2d':>6} {'tier':>5} {'E2':>3} {'reason':>22} {'restaurant':<16} {'status'}")
for r in rows[:25]:
    o=r.get("outcome") or {}
    print(f"{r.get('order_id'):>7} {wday(r['decision_ts'])[-2:]:>5} {r['_w']:5.0f} {num(r.get('proposed_score')):8.1f} {r.get('pool_feasible'):>5} {r.get('pool_total'):>4} {num(r.get('predicted_r6_max_bag_min')):5.1f} {num(o.get('assign_to_delivery_min')):6.1f} {str(r.get('tier')):>5} {'Y' if e2sig(r) else '.':>3} {str(r.get('auto_route_reason'))[:22]:>22} {str(r.get('restaurant'))[:16]:<16} {o.get('status')}")

# distributions
import statistics as st
ps=[num(r.get("proposed_score")) for r in rows]
neg=[p for p in ps if p<0]
print(f"\nproposed_score: n={len(ps)} <0:{len(neg)} ({100*len(neg)/len(ps):.0f}%) min={min(ps):.0f} median={st.median(ps):.1f} p10={sorted(ps)[len(ps)//10]:.1f}")
a2d=[num((r.get('outcome') or {}).get('assign_to_delivery_min')) for r in rows if (r.get('outcome') or {}).get('assign_to_delivery_min')]
print(f"assign_to_delivery_min: n={len(a2d)} median={st.median(a2d):.1f} p90={sorted(a2d)[int(len(a2d)*0.9)]:.1f} >35:{sum(1 for x in a2d if x>35)} >45:{sum(1 for x in a2d if x>45)} >60:{sum(1 for x in a2d if x>60)}")
feas=[r.get('pool_feasible') or 0 for r in rows]
print(f"pool_feasible: <=1:{sum(1 for x in feas if x<=1)} ==0:{sum(1 for x in feas if x==0)}")
print(f"E2-signature (oid%5==0) rows: {sum(1 for r in rows if e2sig(r))} / {len(rows)}")
print(f"tier values: ", {t: sum(1 for r in rows if str(r.get('tier'))==t) for t in set(str(r.get('tier')) for r in rows)})
print(f"action values: ", {t: sum(1 for r in rows if str(r.get('action'))==t) for t in set(str(r.get('action')) for r in rows)})

# 2) build shadow_decisions index for a few selected oids
sel = [r.get("order_id") for r in rows[:6]]
print(f"\n=== detailed dump for selected oids: {sel} ===")
want=set(str(x) for x in sel)
recs={}
with open(SD) as f:
    for line in f:
        if not line.strip(): continue
        try: r=json.loads(line)
        except: continue
        if str(r.get("order_id")) in want and inwin(r.get("ts")):
            recs.setdefault(str(r.get("order_id")),[]).append(r)

OBJF=["courier_id","name","score","feasibility","reason","best_effort","pos_source",
      "km_to_pickup","drive_min","travel_min","travel_min_cal","time_to_pickup_ready_min","free_at_min",
      "r6_max_bag_time_min","r6_worst_oid","r6_is_solo","r6_bag_size","bag_size_before",
      "objm_r6_breach_max_min","objm_r6_breach_count","objm_route_span_min",
      "late_pickup_committed_breach","late_pickup_committed_max","late_pickup_committed_worst_oid",
      "new_pickup_late_min","new_pickup_needs_extension","late_pickup_max_min",
      "czas_kuriera_hhmm","eta_pickup_hhmm","eta_drive_hhmm"]
def slim(c):
    return {k:c.get(k) for k in OBJF if k in c}
for oid in sel:
    rr=recs.get(str(oid)) or []
    print(f"\n----- oid={oid} ({len(rr)} shadow recs) -----")
    if not rr: print("  (no shadow_decisions record)"); continue
    r=rr[0]
    print(f"  ts={r.get('ts')} verdict={r.get('verdict')} reason={str(r.get('reason'))[:80]} pool_total={r.get('pool_total_count')} pool_feas={r.get('pool_feasible_count')}")
    print(f"  auto_route={r.get('auto_route')} / {r.get('auto_route_reason')}")
    b=r.get("best") or {}
    print("  BEST:", json.dumps(slim(b),ensure_ascii=False))
    for i,a in enumerate(r.get("alternatives") or []):
        print(f"  ALT{i}:", json.dumps(slim(a),ensure_ascii=False))
