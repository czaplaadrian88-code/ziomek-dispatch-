#!/usr/bin/env python3
"""
N1 FLIP JOIN (READ-ONLY) — 2026-06-17.

For every decision (14-16.06) whose logged WINNER has an affected NON-winner in the SAME
(tier_rank,bucket_rank) group, join that alternative to obj_replay_capture via its bag_context
order_ids (unique match), and determine:
  - does the affected alt's anchor ACTUALLY change under N1 (newest-assigned != earliest-due)?
    (if not -> N1 is a no-op for it -> cannot cause a flip)
  - if it changes: OSRM drive reduction to the NEW ORDER pickup (proxy for score uplift sign;
    closer => higher score) and committed-lateness comparison alt-vs-winner (flip direction).
A potential flip = anchor changes AND alt is close in score to winner. We report direction
(BETTER/WORSE committed) for any such case.
"""
import json, sys
from datetime import datetime, timedelta, timezone
from collections import defaultdict
sys.path.insert(0,"/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-17")
import audit_lib as AL

SHADOW="/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
CAP="/root/.openclaw/workspace/dispatch_state/obj_replay_capture.jsonl"
DAYS=("2026-06-14","2026-06-15","2026-06-16")
WINDOW_MAX_MIN=65.0

INFORMED={"gps","last_picked_up_recent","last_picked_up_pickup","last_picked_up_delivery","last_assigned_pickup","last_assigned_delivery","post_wave","pos_from_store","last_known","last_delivered"}
def num(x,dv=0.0):
    try: return float(x) if x is not None else dv
    except: return dv
def tier_rank(c):
    if c.get("late_pickup_committed_breach") is True: return 2
    if c.get("new_pickup_needs_extension") is True: return 1
    return 0
def bucket_rank(c):
    ps=c.get("pos_source"); bag=num(c.get("r6_bag_size"))
    if ps in INFORMED: return 0
    if (ps in ("no_gps","pre_shift","blind",None,"")) and bag==0: return 2
    return 1
def is_sentinel(c): return num(c.get("score"),0.0)<-1e6
def affectable(c): return (c.get("pos_source")=="last_assigned_pickup") and ((c.get("bag_size_before") or 0)>=2)
def pts(s):
    try: return datetime.fromisoformat(str(s).replace("Z","+00:00"))
    except: return None
def wday(ts):
    d=pts(ts); return (d.astimezone(timezone.utc)+timedelta(hours=2)).strftime("%Y-%m-%d") if d else None
def aeq(a,b,t=1e-5): return abs(a[0]-b[0])<t and abs(a[1]-b[1])<t
def edue(o):
    t=pts(o.get("czas_kuriera_warsaw")) or pts(o.get("pickup_ready_at"))
    return (0,t) if t else (1,datetime(1,1,1,tzinfo=timezone.utc))

# capture index by new order id
by_new=defaultdict(list)
for line in open(CAP):
    try: d=json.loads(line)
    except: continue
    if d.get("now","")[:10] < "2026-06-14": continue
    by_new[str(d.get("order_id"))].append(d)

_oc={}
def drive_to(a,b):
    if aeq(a,b): return 0.0
    k=(round(a[0],6),round(a[1],6),round(b[0],6),round(b[1],6))
    if k in _oc: return _oc[k]
    dur,_=AL.osrm_route(a,b); _oc[k]=dur; return dur

def match_capture(oid, bag_oids):
    for r in by_new.get(oid,[]):
        roids=set(str(o.get("order_id")) for o in (r.get("bag") or []))
        if roids==bag_oids and bag_oids:
            return r
    return None

def main():
    n_eligible=0; n_joined=0; n_anchor_change=0; n_noop=0; n_nomatch=0
    flips_possible=[]
    for line in open(SHADOW):
        try: d=json.loads(line)
        except: continue
        if wday(d.get("ts")) not in DAYS: continue
        now=pts(d.get("ts")); ready=pts(d.get("pickup_ready_at"))
        if now and ready and (ready.astimezone(timezone.utc)-now.astimezone(timezone.utc)).total_seconds()/60.0>WINDOW_MAX_MIN:
            continue
        b=d.get("best") or {}
        wtb=(tier_rank(b),bucket_rank(b)); wscore=num(b.get("score"))
        if is_sentinel(b):
            continue
        alts=d.get("alternatives") or []
        aff=[a for a in alts if affectable(a) and not is_sentinel(a) and (tier_rank(a),bucket_rank(a))==wtb]
        for a in aff:
            n_eligible+=1
            bag_oids=set(str(x.get("order_id")) for x in (a.get("bag_context") or []))
            r=match_capture(str(d.get("order_id")), bag_oids)
            if r is None:
                n_nomatch+=1; continue
            n_joined+=1
            assigned=[o for o in r["bag"] if o.get("status")=="assigned" and o.get("pickup_coords")]
            if len(assigned)<2:
                n_noop+=1; continue
            cp=tuple(r["courier_pos"])
            na=min(assigned,key=edue); npos=tuple(na["pickup_coords"])
            if aeq(npos,cp):
                n_noop+=1; continue
            n_anchor_change+=1
            # drive reduction to NEW ORDER pickup (what scoring uses for km_to_pickup/ETA)
            no=r.get("new_order") or {}
            npk=tuple(no["pickup_coords"]) if no.get("pickup_coords") else None
            red=None
            if npk:
                d_old=drive_to(cp,npk); d_new=drive_to(npos,npk)
                if d_old is not None and d_new is not None:
                    red=d_old-d_new   # >0 => NEW closer to new pickup => score uplift => could overtake
            gap=wscore-num(a.get("score"))
            altcl=num(a.get("late_pickup_committed_max")); wincl=num(b.get("late_pickup_committed_max"))
            direction="BETTER-committed" if altcl<wincl else ("EQUAL" if altcl==wincl else "WORSE-committed")
            # potential flip if gap is modest and uplift positive
            potential = (red is not None and red>0 and gap<=60.0)
            if potential:
                flips_possible.append(dict(day=wday(d.get("ts")), oid=d.get("order_id"),
                    win_cid=b.get("courier_id"), win_ps=b.get("pos_source"), win_score=round(wscore,1), win_cl=round(wincl,1),
                    alt_cid=a.get("courier_id"), alt_score=round(num(a.get("score")),1), alt_cl=round(altcl,1),
                    gap=round(gap,1), drive_red_to_newpickup=round(red,2), direction=direction))

    print("="*96)
    print("N1 FLIP JOIN — affected non-winners (same tier/bucket as winner), joined to capture by bag")
    print("="*96)
    print(f"affected-non-winner instances (eligible):       {n_eligible}")
    print(f"  joined to capture by bag_context:             {n_joined}  (no-match: {n_nomatch})")
    print(f"  N1 NO-OP (anchor unchanged / <2 assigned):    {n_noop}")
    print(f"  N1 anchor CHANGES (position moves):           {n_anchor_change}")
    print(f"  of those, POTENTIAL flips (uplift>0 & gap<=60): {len(flips_possible)}")
    if flips_possible:
        print("\n--- POTENTIAL FLIP CASES (would N1 flip winner? toward better/worse committed timeliness) ---")
        for r in sorted(flips_possible,key=lambda x:x['gap']):
            print(f"  {r['day']} oid={r['oid']} winner cid={r['win_cid']}({r['win_ps']},s={r['win_score']},cl={r['win_cl']}) "
                  f"| affAlt cid={r['alt_cid']}(s={r['alt_score']},cl={r['alt_cl']}) gap={r['gap']} driveRed={r['drive_red_to_newpickup']}min -> {r['direction']}")
        nbet=sum(1 for r in flips_possible if r['direction']=='BETTER-committed')
        neq =sum(1 for r in flips_possible if r['direction']=='EQUAL')
        nwor=sum(1 for r in flips_possible if r['direction']=='WORSE-committed')
        print(f"\n  flip direction tally: BETTER-committed={nbet}, EQUAL={neq}, WORSE-committed={nwor}")
        print("  (a flip toward WORSE-committed with a tiny gap would be the only true selection regression risk)")
    else:
        print("\n  NO potential flips: every affected non-winner is either an N1 no-op or too far behind to overtake.")

if __name__=="__main__":
    main()
