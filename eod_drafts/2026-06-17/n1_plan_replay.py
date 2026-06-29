#!/usr/bin/env python3
"""
N1 PLAN-LEVEL REPLAY (gold standard for effect-on-plan) — 2026-06-17  READ-ONLY.

For each qualifying candidate (all-assigned >=2, no-GPS => courier_pos == an assigned
pickup), reconstruct the EXACT solver inputs from obj_replay_capture and run the REAL
simulate_bag_route_v2 TWICE:
    OLD: courier_pos = captured courier_pos (newest-assigned pickup, flag OFF behaviour)
    NEW: courier_pos = earliest-due assigned pickup (flag ON behaviour)
Then compare the planned PICKUP time of every committed order (czas_kuriera set) vs its
committed time. This is the genuine plan the pipeline would produce; no proxy.

Metrics per record (minutes; planned_pickup - committed, >0 = late):
  committed_late_max  = max over committed orders of (plan.pickup_at[oid] - czas_kuriera)
Delta = OLD.committed_late_max - NEW.committed_late_max
  >0  => N1 reduces committed-pickup lateness in the PLAN (the goal)
  <0  => N1 makes a committed pickup later in the plan (REGRESSION)

Also tracks new_order planned pickup lateness vs its pickup_ready (R6-ish) to catch
whether straightening the committed pickup hurts the NEW order.

Proposal-window filter on NEW order pickup readiness (same as n1_regr_validate).
"""
import json, sys
from datetime import datetime, timedelta, timezone
from collections import defaultdict
sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from dispatch_v2.route_simulator_v2 import simulate_bag_route_v2, OrderSim

CAP = "/root/.openclaw/workspace/dispatch_state/obj_replay_capture.jsonl"
DAYS = ("2026-06-14", "2026-06-15", "2026-06-16")
WINDOW_MAX_MIN = 65.0
BORDERLINE_MIN = 1.0
WARSAW_OFF = 2

def pts(s):
    if not s: return None
    try: return datetime.fromisoformat(str(s).replace("Z","+00:00")).astimezone(timezone.utc)
    except: return None
def wday(ts):
    d=pts(ts)
    return (d+timedelta(hours=WARSAW_OFF)).strftime("%Y-%m-%d") if d else None
def aeq(a,b,t=1e-5): return abs(a[0]-b[0])<t and abs(a[1]-b[1])<t
def edue(o):
    ts=pts(o.get("czas_kuriera_warsaw")) or pts(o.get("pickup_ready_at"))
    return (0,ts) if ts else (1,datetime(1,1,1,tzinfo=timezone.utc))

def mk(o):
    return OrderSim(
        order_id=str(o.get("order_id")),
        pickup_coords=tuple(o["pickup_coords"]),
        delivery_coords=tuple(o["delivery_coords"]) if o.get("delivery_coords") else tuple(o["pickup_coords"]),
        picked_up_at=pts(o.get("picked_up_at")),
        status=o.get("status") or "assigned",
        pickup_ready_at=pts(o.get("pickup_ready_at")),
    )

def committed_late_max(plan, bag_objs, ck_by_oid):
    """max over committed orders (czas_kuriera set) of planned pickup minus committed time."""
    worst=None; worst_oid=None
    if plan is None or not getattr(plan,"pickup_at",None): return (None,None)
    for oid, ck in ck_by_oid.items():
        pat = plan.pickup_at.get(oid)
        if pat is None:
            # not picked in this plan (already picked_up) — skip
            continue
        late = (pat - ck).total_seconds()/60.0
        if worst is None or late>worst:
            worst=late; worst_oid=oid
    return (worst, worst_oid)

def main():
    per_day = defaultdict(lambda: dict(qual=0, changed=0, red=[], regr=[], regrlist=[],
                                        borderline=0, certain=0, simfail=0, no_committed=0))
    n_seen=0
    with open(CAP) as f:
        for line in f:
            try: d=json.loads(line)
            except: continue
            day=wday(d.get("now"))
            if day not in DAYS: continue
            bag=d.get("bag") or []
            assigned=[o for o in bag if o.get("status")=="assigned" and o.get("pickup_coords")]
            pickedup=[o for o in bag if o.get("status")=="picked_up"]
            if pickedup or len(assigned)<2: continue
            cp=d.get("courier_pos")
            if not cp: continue
            cp=tuple(cp)
            if not any(aeq(tuple(o["pickup_coords"]),cp) for o in assigned): continue
            no=d.get("new_order") or {}
            now=pts(d.get("now")); ready=pts(no.get("pickup_ready_at"))
            if now and ready and (ready-now).total_seconds()/60.0 > WINDOW_MAX_MIN:
                continue
            P=per_day[day]; P["qual"]+=1

            new_anchor=min(assigned,key=edue)
            new_pos=tuple(new_anchor["pickup_coords"])
            if aeq(new_pos,cp):
                continue  # anchor unchanged
            P["changed"]+=1

            # committed orders in bag (czas_kuriera set) and their committed time
            ck_by_oid={}
            for o in assigned:
                ck=pts(o.get("czas_kuriera_warsaw"))
                if ck: ck_by_oid[str(o.get("order_id"))]=ck
            if not ck_by_oid:
                P["no_committed"]+=1
                continue

            bag_objs=[mk(o) for o in bag]
            new_obj=mk(no)
            try:
                plan_old=simulate_bag_route_v2(cp, bag_objs, new_obj, now=now)
                plan_new=simulate_bag_route_v2(new_pos, bag_objs, new_obj, now=now)
            except Exception as e:
                P["simfail"]+=1
                continue
            lo,_=committed_late_max(plan_old, bag_objs, ck_by_oid)
            ln,_=committed_late_max(plan_new, bag_objs, ck_by_oid)
            if lo is None or ln is None:
                P["no_committed"]+=1
                continue
            delta=lo-ln
            if delta>=0: P["red"].append(delta)
            else:
                P["regr"].append(-delta)
                P["regrlist"].append(dict(oid=no.get("order_id"), now=d.get("now"),
                                          old_late=round(lo,2), new_late=round(ln,2), delta=round(delta,2)))
            if abs(delta)<BORDERLINE_MIN: P["borderline"]+=1
            else: P["certain"]+=1
            n_seen+=1

    def med(xs): xs=sorted(xs); return xs[len(xs)//2] if xs else 0.0
    def p90(xs): xs=sorted(xs); return xs[int(len(xs)*0.9)] if xs else 0.0
    print("="*96)
    print("N1 PLAN-LEVEL REPLAY — real simulate_bag_route_v2, OLD vs NEW courier_pos")
    print("Metric: committed-pickup lateness in the PLAN (planned pickup_at - czas_kuriera), max per bag")
    print("="*96)
    print(f"{'day':<11}{'qualif':>8}{'changed':>9}{'reduced':>9}{'regr':>6}{'red min(med/p90)':>20}{'regr min(sum)':>16}{'certain':>9}{'border':>8}{'simfail':>8}")
    T=defaultdict(float); TR=[]; Tred=[]; Tregr=[]
    for day in DAYS:
        P=per_day[day]
        print(f"{day:<11}{P['qual']:>8}{P['changed']:>9}{len(P['red']):>9}{len(P['regr']):>6}"
              f"{(str(round(med(P['red']),1))+'/'+str(round(p90(P['red']),1))):>20}"
              f"{round(sum(P['regr']),1):>16}{P['certain']:>9}{P['borderline']:>8}{P['simfail']:>8}")
        for k in ("qual","changed","certain","borderline","simfail","no_committed"): T[k]+=P[k]
        Tred+=P['red']; Tregr+=P['regr']; TR+=[dict(r,day=day) for r in P['regrlist']]
    print("-"*96)
    print(f"{'TOTAL':<11}{int(T['qual']):>8}{int(T['changed']):>9}{len(Tred):>9}{len(Tregr):>6}"
          f"{(str(round(med(Tred),1))+'/'+str(round(p90(Tred),1))):>20}{round(sum(Tregr),1):>16}{int(T['certain']):>9}{int(T['borderline']):>8}{int(T['simfail']):>8}")
    print(f"\nanchor-changed records replayed thru real TSP: {int(T['changed'])}")
    print(f"committed-late REDUCED by N1: {len(Tred)} (sum {round(sum(Tred),0)} min, median {round(med(Tred),1)}, p90 {round(p90(Tred),1)})")
    print(f"committed-late INCREASED by N1 (REGRESSION): {len(Tregr)} (sum {round(sum(Tregr),1)} min)")
    print(f"records with no committed pickup in bag plan (skipped): {int(T['no_committed'])}")
    if TR:
        print("\n--- PLAN REGRESSIONS (committed pickup later under N1) ---")
        for r in sorted(TR,key=lambda x:x['delta'])[:40]:
            print(f"  {r['day']} oid={r['oid']} OLD_late={r['old_late']} NEW_late={r['new_late']} delta={r['delta']}")
    else:
        print("\n--- PLAN REGRESSIONS: NONE ---")

if __name__=="__main__":
    main()
