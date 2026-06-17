#!/usr/bin/env python3
"""Per-case: why hard + why Ziomek's pick + alternatives + does the D2/objm shadow fix it?"""
import json,sys
sys.path.insert(0,"/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-17")
import audit_lib as L
W=json.load(open(f"{L.SCRATCH}/wf_results.json"))
FULL=json.load(open(f"{L.SCRATCH}/deepdive_full_records.json"))
verdicts={(v["order_id"],v.get("ts")):v for v in W["deepdive_verdicts"]}
# fallback index by oid
by_oid={}
for v in W["deepdive_verdicts"]: by_oid.setdefault(v["order_id"],[]).append(v)

INFORMED={"gps","last_picked_up_recent","last_picked_up_pickup","last_picked_up_delivery","last_assigned_pickup","last_assigned_delivery","post_wave","pos_from_store","last_known","last_delivered"}
def num(x,d=0.0):
    try: return float(x) if x is not None else d
    except: return d
def tier(c):
    if c.get("late_pickup_committed_breach") is True: return 2
    if c.get("new_pickup_needs_extension") is True: return 1
    return 0
def bucket(c):
    ps=c.get("pos_source"); bag=num(c.get("r6_bag_size"))
    if ps in INFORMED: return 0
    if ps in ("no_gps","pre_shift","blind",None,"") and bag==0: return 2
    return 1
def lexkey(c):   # D2: R6-BREACH primary (0 jeśli ≤35)
    r6=c.get("objm_r6_breach_max_min")
    return (num(r6,9e9) if r6 is not None else 9e9, num(c.get("late_pickup_committed_max")), num(c.get("new_pickup_late_min")))
def lexkey_raw(c):  # D2b: RAW R6 primary (zawsze rozróżnia) → committed → new-late
    r6=c.get("r6_max_bag_time_min")
    return (num(r6,9e9) if r6 is not None else 9e9, num(c.get("late_pickup_committed_max")), num(c.get("new_pickup_late_min")))

def d2_pick(rec, key=lexkey):
    best=rec.get("best") or {}
    cands=[best]+[a for a in (rec.get("alternatives") or []) if "score" in a]
    if "score" not in best: return None,None
    wtb=(tier(best),bucket(best))
    grp=[c for c in cands if (tier(c),bucket(c))==wtb]
    if not grp: return best,grp
    return min(grp,key=key),grp

rows=[]
for oid,recs in FULL.items():
    vs=by_oid.get(oid) or []
    for rec in recs:
        ts=rec.get("ts")
        v=verdicts.get((oid,ts)) or (vs[0] if vs else None)
        if not v: continue
        best=rec.get("best") or {}
        if "score" not in best: continue
        d2,grp=d2_pick(rec)
        d2b,_=d2_pick(rec, lexkey_raw)
        d2cid=str(d2.get("courier_id")) if d2 else None
        d2bcid=str(d2b.get("courier_id")) if d2b else None
        bestcid=str(best.get("courier_id"))
        domcid=str(v.get("dominator_cid"))
        d2_fixes = (d2cid!=bestcid)          # D2 (breach-primary) would change Ziomek's pick
        d2b_fixes = (d2bcid!=bestcid)        # D2b (raw-R6-primary) would change pick
        d2_is_dom = (d2cid==domcid)
        rows.append({
            "oid":oid,"ts":ts,"rest":rec.get("restaurant"),
            "cls":v.get("classification"),"best":bestcid,"dom":domcid,
            "axis":str(v.get("culprit_axis") or "")[:70],"hid":str(v.get("hidden_disqualifier") or "")[:50],
            "d2":d2cid,"d2_fixes":d2_fixes,"d2b":d2bcid,"d2b_fixes":d2b_fixes,"d2_is_dom":d2_is_dom,"grp":len(grp) if grp else 0,
            "dR6":v.get("d_r6"),"dCom":v.get("d_committed"),"conf":v.get("confidence"),
            "out":v.get("outcome_note","")[:60],
        })
# dedup by (oid,ts)
seen=set();out=[]
for r in rows:
    k=(r["oid"],r["ts"])
    if k in seen: continue
    seen.add(k);out.append(r)

order={"score_miscalibration_REAL":0,"E2_fixed":1,"saturation_leastbad":2,"minor_noise":3,"hidden_disqualifier":4,"data_artifact":5}
out.sort(key=lambda r:(order.get(r["cls"],9), r["oid"]))
# correct E2 mislabels (axis names E2)
for r in out:
    if r["cls"]=="score_miscalibration_REAL" and ("E2-pln" in r["axis"] or "_pln_pure_resort" in r["axis"]): r["cls"]="E2_fixed"

print(f"{'oid':>7} {'cls':<26} {'best→dom':<9} {'D2pick':>6} {'D2fix?':>6} {'=dom?':>5} {'grp':>3} {'dR6':>5} {'dCom':>5} {'axis'}")
for r in out:
    print(f"{r['oid']:>7} {r['cls']:<26} {r['best']+'→'+r['dom']:<9} {str(r['d2']):>6} {'YES' if r['d2_fixes'] else 'no':>6} {'Y' if r['d2_is_dom'] else '.':>5} {r['grp']:>3} {str(r['dR6']):>5} {str(r['dCom']):>5} {r['axis'][:46]}")

# summary: of REAL, how many does D2 fix?
real=[r for r in out if r["cls"]=="score_miscalibration_REAL"]
print(f"\nREAL={len(real)}  D2(breach) changes pick: {sum(1 for r in real if r['d2_fixes'])}  D2b(rawR6) changes pick: {sum(1 for r in real if r['d2b_fixes'])}  D2 picks dominator: {sum(1 for r in real if r['d2_is_dom'])}")
e2=[r for r in out if r["cls"]=="E2_fixed"]
print(f"E2_fixed={len(e2)} (naprawione Fix B+C live 17.06)")
print(f"saturation={sum(1 for r in out if r['cls']=='saturation_leastbad')} minor={sum(1 for r in out if r['cls']=='minor_noise')} hidden/FP={sum(1 for r in out if r['cls']=='hidden_disqualifier')} artifact={sum(1 for r in out if r['cls']=='data_artifact')}")
json.dump(out,open(f"{L.SCRATCH}/per_case_fix.json","w"),ensure_ascii=False,indent=1)
