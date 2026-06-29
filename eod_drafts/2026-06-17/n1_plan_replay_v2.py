#!/usr/bin/env python3
"""
N1 PLAN-LEVEL REPLAY v2 — noise-controlled. READ-ONLY. 2026-06-17.

The v1 replay used a single OR-Tools solve per position. OR-Tools hits its 200ms time
limit every call and is NON-DETERMINISTIC, so OLD-vs-NEW deltas conflate the N1 effect
with solver noise. v2 controls for this:

  1. NOISE FLOOR: solve the OLD position TWICE (same inputs) -> any nonzero committed-late
     delta is pure solver noise. Report its distribution.
  2. N1 EFFECT: solve OLD and NEW. Classify a record's committed-late delta as a GENUINE
     change only if |delta| exceeds the noise floor (p95 of the OLD-vs-OLD |delta|).

Committed-late metric: max over committed (czas_kuriera set) orders of (plan.pickup_at - ck).
Only future-vs-now context matters operationally; we additionally split by whether the
EARLIEST-DUE bag pickup is within the proposal window (<= WINDOW_MAX_MIN from now), because
bags whose committed pickups are far in the future are not realistically (re)planned now and
their TSP reshuffles are not decisions Ziomek would actually act on.
"""
import json, sys
from datetime import datetime, timedelta, timezone
from collections import defaultdict
sys.path.insert(0,"/root/.openclaw/workspace/scripts")
from dispatch_v2.route_simulator_v2 import simulate_bag_route_v2, OrderSim

CAP="/root/.openclaw/workspace/dispatch_state/obj_replay_capture.jsonl"
DAYS=("2026-06-14","2026-06-15","2026-06-16")
WINDOW_MAX_MIN=65.0

def pts(s):
    if not s: return None
    try: return datetime.fromisoformat(str(s).replace("Z","+00:00")).astimezone(timezone.utc)
    except: return None
def wday(ts):
    d=pts(ts); return (d+timedelta(hours=2)).strftime("%Y-%m-%d") if d else None
def aeq(a,b,t=1e-5): return abs(a[0]-b[0])<t and abs(a[1]-b[1])<t
def edue(o):
    t=pts(o.get("czas_kuriera_warsaw")) or pts(o.get("pickup_ready_at"))
    return (0,t) if t else (1,datetime(1,1,1,tzinfo=timezone.utc))
def mk(o):
    return OrderSim(order_id=str(o["order_id"]),pickup_coords=tuple(o["pickup_coords"]),
        delivery_coords=tuple(o["delivery_coords"]) if o.get("delivery_coords") else tuple(o["pickup_coords"]),
        picked_up_at=pts(o.get("picked_up_at")),status=o.get("status") or "assigned",
        pickup_ready_at=pts(o.get("pickup_ready_at")))
def committed_late_max(plan, ck_by_oid):
    if plan is None or not getattr(plan,"pickup_at",None): return None
    worst=None
    for oid,ck in ck_by_oid.items():
        pat=plan.pickup_at.get(oid)
        if pat is None: continue
        late=(pat-ck).total_seconds()/60.0
        if worst is None or late>worst: worst=late
    return worst

def main():
    recs=[]
    for line in open(CAP):
        try: d=json.loads(line)
        except: continue
        if wday(d.get("now")) not in DAYS: continue
        bag=d.get("bag") or []
        assigned=[o for o in bag if o.get("status")=="assigned" and o.get("pickup_coords")]
        if [o for o in bag if o.get("status")=="picked_up"] or len(assigned)<2: continue
        cp=d.get("courier_pos")
        if not cp: continue
        cp=tuple(cp)
        if not any(aeq(tuple(o["pickup_coords"]),cp) for o in assigned): continue
        no=d.get("new_order") or {}
        now=pts(d.get("now")); ready=pts(no.get("pickup_ready_at"))
        if now and ready and (ready-now).total_seconds()/60.0>WINDOW_MAX_MIN: continue
        na=min(assigned,key=edue); npos=tuple(na["pickup_coords"])
        if aeq(npos,cp): continue
        ck_by_oid={str(o["order_id"]):pts(o.get("czas_kuriera_warsaw")) for o in assigned if o.get("czas_kuriera_warsaw")}
        if not ck_by_oid: continue
        recs.append((d, cp, npos, now, bag, no, ck_by_oid, na))
    print(f"qualifying anchor-changed records (committed bag, window-ok new order): {len(recs)}")

    noise=[]; effect=[]; effect_due=[]; regr_due=[]; red_due=[]
    regr_all=[]; red_all=[]
    for (d,cp,npos,now,bag,no,ck_by_oid,na) in recs:
        bag_objs=[mk(o) for o in bag]; new_obj=mk(no)
        try:
            old1=committed_late_max(simulate_bag_route_v2(cp,bag_objs,new_obj,now=now),ck_by_oid)
            old2=committed_late_max(simulate_bag_route_v2(cp,bag_objs,new_obj,now=now),ck_by_oid)
            new1=committed_late_max(simulate_bag_route_v2(npos,bag_objs,new_obj,now=now),ck_by_oid)
        except Exception:
            continue
        if old1 is None or old2 is None or new1 is None: continue
        noise.append(abs(old1-old2))
        delta = old1 - new1     # >0 reduce, <0 regression
        effect.append(delta)
        if delta>=0: red_all.append(delta)
        else: regr_all.append(-delta)
        # is earliest-due bag pickup within proposal window? (operationally actionable now)
        edue_ts = edue(na)[1]
        horizon = (edue_ts-now).total_seconds()/60.0 if edue_ts.year>1 else 9999
        due_now = horizon <= WINDOW_MAX_MIN
        if due_now:
            effect_due.append(delta)
            if delta>=0: red_due.append(delta)
            else: regr_due.append(dict(oid=no.get("order_id"), day=wday(d.get("now")),
                    old=round(old1,1), new=round(new1,1), delta=round(delta,1),
                    earliest_due_in_min=round(horizon,1)))

    def stats(xs):
        xs=sorted(xs)
        if not xs: return (0,0,0,0)
        return (len(xs), round(xs[len(xs)//2],2), round(xs[int(len(xs)*0.95)],2), round(max(xs),2))
    n,med,p95,mx = stats(noise)
    print(f"\nNOISE FLOOR (OLD-vs-OLD |committed-late delta|, pure OR-Tools nondeterminism):")
    print(f"  n={n} median={med} p95={p95} max={mx}  ->  treat |delta|<={p95} as solver noise, not N1")
    NF=p95

    print(f"\nN1 EFFECT (OLD-vs-NEW committed-late delta), ALL anchor-changed window-ok records:")
    print(f"  reductions (delta>{NF}): {sum(1 for x in effect if x>NF)}  (sum {round(sum(x for x in effect if x>NF),0)} min)")
    print(f"  GENUINE regressions (delta<{-NF}): {sum(1 for x in effect if x< -NF)}  (sum {round(sum(-x for x in effect if x< -NF),1)} min)")
    print(f"  within-noise (|delta|<={NF}): {sum(1 for x in effect if abs(x)<=NF)}")

    print(f"\n--- OPERATIONALLY ACTIONABLE SUBSET (earliest-due bag pickup within {WINDOW_MAX_MIN}min of now) ---")
    print(f"  records: {len(effect_due)}")
    genr=[r for r in regr_due if abs(r['delta'])>NF]
    print(f"  reductions (delta>{NF}): {sum(1 for x in effect_due if x>NF)}  (sum {round(sum(x for x in effect_due if x>NF),0)} min)")
    print(f"  GENUINE regressions (delta<{-NF}): {len(genr)}  (sum {round(sum(abs(r['delta']) for r in genr),1)} min)")
    if genr:
        print("  --- genuine actionable regressions ---")
        for r in sorted(genr,key=lambda x:x['delta'])[:30]:
            print(f"    {r['day']} oid={r['oid']} OLD_late={r['old']} NEW_late={r['new']} delta={r['delta']} earliest_due_in={r['earliest_due_in_min']}min")

if __name__=="__main__":
    main()
