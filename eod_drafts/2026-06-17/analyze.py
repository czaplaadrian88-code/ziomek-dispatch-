#!/usr/bin/env python3
"""Full-window deterministic pass v2: dominance + artifact filter + verdict split + buckets."""
import json, sys, statistics as st
from collections import Counter
sys.path.insert(0, "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-17")
import audit_lib as L

print("building slim shadow index...")
idx = L.build_slim_shadow_index(save_path=f"{L.SCRATCH}/slim_shadow_index.json")
print(f"  orders={len(idx)} decisions={sum(len(v) for v in idx.values())}")

bf = L.load_backfill_window()
oc = {}
for r in bf:
    oid = str(r.get("order_id")); o = r.get("outcome") or {}
    cur = oc.get(oid)
    if cur is None or (o.get("status")=="delivered" and (cur.get("outcome") or {}).get("status")!="delivered"):
        oc[oid] = r

results = []
all_decisions = 0
for oid, recs in idx.items():
    for sr in recs:
        all_decisions += 1
        a = L.analyze_decision(sr)
        if not a or not a["dominators"]: continue
        doms = a["dominators"]
        def sev_key(dd):
            de = dd["deltas"]; return (de["d_breach_count"], de["d_committed_max"], de["d_r6_max_bag"], de["d_new_pickup_late"])
        top = max(doms, key=sev_key); maxd = top["deltas"]
        best_r6 = L.num(a["best_r6"]) or 0
        is_artifact = best_r6 > L.R6_ARTIFACT_MIN or maxd["d_r6_max_bag"] > L.R6_ARTIFACT_MIN
        feas_upgrade = any(d["deltas"]["B_feas"]>=2 and d["deltas"]["A_feas"]==0 for d in doms)
        ev = oc.get(oid) or {}; o = ev.get("outcome") or {}
        results.append({
            "order_id": oid, "ts": sr["ts"], "day": sr["day"], "verdict": sr.get("verdict"),
            "restaurant": sr.get("restaurant"), "auto_route_reason": sr.get("auto_route_reason"),
            "pool_total": sr.get("pool_total"), "pool_feas": sr.get("pool_feas"),
            "saturation": sr.get("pool_feas")==0, "e2_sig": L.e2_signature(oid), "is_artifact": is_artifact,
            "best_cid": a["best_cid"], "best_score": a["best_score"], "best_feasibility": a["best_feasibility"],
            "best_effort": a["best_effort"], "best_r6": a["best_r6"], "best_committed_max": a["best_committed_max"],
            "best_committed_breach": a["best_committed_breach"], "best_new_late": a["best_new_late"],
            "best_pos_source": a["best_pos_source"], "best_is_score_top": a["best_is_score_top"],
            "score_top_cid": a["score_top_cid"], "n_dominators": len(doms), "feas_upgrade": feas_upgrade,
            "top_dominator": top, "all_dominators": doms,
            "outcome_status": o.get("status"), "outcome_courier_final": o.get("courier_id_final"),
            "outcome_assign_to_delivery_min": o.get("assign_to_delivery_min"),
            "outcome_pickup_to_delivery_min": o.get("pickup_to_delivery_min"),
        })

def bucket(r):
    if r["is_artifact"]: return "ARTIFACT_zombie_r6"
    if str(r["verdict"]).upper()=="KOORD": return "KOORD_dominated"
    if r["saturation"]: return "SATURATION_leastbad"
    td = r["top_dominator"]["deltas"]
    if td["d_committed_max"] > 2.0: return "PROPOSE_committed_avoidable"
    if td["d_r6_max_bag"] > 10.0: return "PROPOSE_r6_avoidable"
    return "PROPOSE_minor"
for r in results: r["bucket"] = bucket(r)

print("\n================= DOMINANCE PASS v2 =================")
print(f"decisions analyzed: {all_decisions}")
print(f"dominated (strict): {len(results)} ({100*len(results)/all_decisions:.1f}%)")
bc = Counter(r["bucket"] for r in results)
print("\n--- by bucket (E2 / non-E2) ---")
for b,_ in bc.most_common():
    sub=[r for r in results if r["bucket"]==b]
    e2=sum(1 for r in sub if r["e2_sig"])
    print(f"  {b:32s} n={len(sub):4d}   E2={e2:3d}  nonE2={len(sub)-e2:3d}")

real = [r for r in results if r["bucket"].startswith("PROPOSE") or r["bucket"]=="SATURATION_leastbad"]
print(f"\nREAL proposal-dominated (excl artifact+KOORD): {len(real)}  (E2={sum(1 for r in real if r['e2_sig'])} nonE2={sum(1 for r in real if not r['e2_sig'])})")
ca = [r for r in results if r["bucket"]=="PROPOSE_committed_avoidable"]
print(f"  committed-breach AVOIDABLE (dCommit>2min, PROPOSE feasible-pool): {len(ca)}")
if ca:
    cm=[r['top_dominator']['deltas']['d_committed_max'] for r in ca]
    print(f"    d_committed_max: median={st.median(cm):.1f} max={max(cm):.1f}  sum_min_avoidable={sum(cm):.0f}")

# severity for deep-dive ranking (exclude artifacts)
def severity(r):
    if r["is_artifact"]: return -1
    td=r["top_dominator"]["deltas"]; s=0.0
    s += 60*max(0,td["d_breach_count"]) + 4.0*max(0,td["d_committed_max"]) + 1.0*max(0,td["d_r6_max_bag"]) + 0.5*max(0,td["d_new_pickup_late"])
    if r["feas_upgrade"]: s+=100
    if str(r["verdict"]).upper()=="KOORD": s*=0.3
    if not r["e2_sig"]: s+=8
    s += 0.2*max(0,(L.num(r.get("outcome_assign_to_delivery_min")) or 0)-35)
    return round(s,1)
for r in results: r["_sev"]=severity(r)
results.sort(key=lambda r:-r["_sev"])
with open(f"{L.SCRATCH}/dominated_cases.json","w") as g: json.dump(results,g,ensure_ascii=False,indent=1)
print(f"\nsaved {len(results)} dominated cases -> dominated_cases.json")

# ---- diagnostics: WHY does Ziomek pick the dominated one? ----
real = [r for r in results if not r["is_artifact"] and str(r["verdict"]).upper()!="KOORD"]
score_top_best = sum(1 for r in real if r["best_is_score_top"])
print("\n--- diagnostics on REAL proposal-dominated (n=%d) ---" % len(real))
print(f"  best WAS score-top (=> SCORE MISCALIBRATION, score itself ranked worse cand #1): {score_top_best} ({100*score_top_best/max(1,len(real)):.0f}%)")
print(f"  best NOT score-top (=> RE-RANK OVERRIDE demoted a better cand): {len(real)-score_top_best}")
domcid = Counter(str(r["top_dominator"]["cid"]) for r in real)
print(f"  top-dominator cid freq (top8): {dict(domcid.most_common(8))}")
dompos = Counter(str(r["top_dominator"]["pos_source"]) for r in real)
print(f"  top-dominator pos_source: {dict(dompos.most_common())}")
bestpos = Counter(str(r["best_pos_source"]) for r in real)
print(f"  chosen-best pos_source:   {dict(bestpos.most_common())}")
# dominator has higher score than best? (override) vs lower (miscalib)
dom_higher=sum(1 for r in real if (L.num(r['top_dominator']['score']) or -9e9) > (L.num(r['best_score']) or -9e9))
print(f"  dominator had HIGHER score than chosen best: {dom_higher}/{len(real)} (override demoted it) ; LOWER: {len(real)-dom_higher} (score undervalues true objective)")
dom_coord=sum(1 for r in real if r['top_dominator'].get('is_coordinator'))
dom_ramp=sum(1 for r in real if r['top_dominator'].get('new_courier_ramp'))
dom_123=sum(1 for r in real if str(r['top_dominator']['cid'])=='123')
dom_123_coord=sum(1 for r in real if str(r['top_dominator']['cid'])=='123' and r['top_dominator'].get('is_coordinator'))
print(f"  POTENTIAL FALSE-POSITIVE dominators: is_coordinator={dom_coord}  new_courier_ramp={dom_ramp}  cid123={dom_123} (of which coord={dom_123_coord})")
dommaybe=sum(1 for r in real if str(r['top_dominator'].get('feasibility','')).upper()=='YES' and str(r['best_feasibility']).upper()!='YES')
print(f"  dominator feasibility=YES while best!=YES (feasibility upgrade): {dommaybe}")

# ---- select deep-dive set + export full records ----
deep = []
for r in results:
    if r["is_artifact"]: continue
    deep.append(r)
# prioritise: all committed_avoidable + r6_avoidable + sample minor + saturation + few KOORD
prio = [r for r in deep if r["bucket"]=="PROPOSE_committed_avoidable"] \
     + [r for r in deep if r["bucket"]=="PROPOSE_r6_avoidable"] \
     + [r for r in deep if r["bucket"]=="SATURATION_leastbad"] \
     + [r for r in deep if r["bucket"]=="PROPOSE_minor"][:12] \
     + [r for r in deep if r["bucket"]=="KOORD_dominated"][:6]
seen=set(); deepsel=[]
for r in prio:
    k=(r["order_id"],r["ts"])
    if k in seen: continue
    seen.add(k); deepsel.append(r)
deep_oids = sorted(set(r["order_id"] for r in deepsel))
print(f"\ndeep-dive set: {len(deepsel)} cases across {len(deep_oids)} orders")
with open(f"{L.SCRATCH}/deepdive_cases.json","w") as g: json.dump(deepsel,g,ensure_ascii=False,indent=1)

# second pass: extract FULL shadow records for deep-dive oids
want=set(deep_oids); full={}
with open(L.SHADOW) as f:
    for line in f:
        if not line.strip(): continue
        try: rr=json.loads(line)
        except: continue
        if str(rr.get("order_id")) in want and L.inwin(rr.get("ts")):
            full.setdefault(str(rr.get("order_id")),[]).append(rr)
with open(f"{L.SCRATCH}/deepdive_full_records.json","w") as g: json.dump(full,g,ensure_ascii=False)
print(f"saved deepdive_cases.json ({len(deepsel)}) + deepdive_full_records.json ({len(full)} orders)")

print("\n=== TOP 35 NON-ARTIFACT dominated (ranked) ===")
print(f"{'oid':>7} {'d':>2} {'sev':>6} {'bucket':<26} {'E2':>2} {'verd':>7} {'sTop':>4} {'bCID':>5} {'bFeas':>5} {'dCID':>5} {'dR6':>6} {'dCom':>6} {'dNew':>6} {'a2d':>6}")
shown=0
for r in results:
    if r["is_artifact"]: continue
    de=r["top_dominator"]["deltas"]; dc=r["top_dominator"]["cid"]
    print(f"{r['order_id']:>7} {r['day'][-2:]:>2} {r['_sev']:6.1f} {r['bucket']:<26} {'Y' if r['e2_sig'] else '.':>2} {str(r['verdict'])[:7]:>7} {'Y' if r['best_is_score_top'] else '.':>4} {str(r['best_cid']):>5} {str(r['best_feasibility'])[:5]:>5} {str(dc):>5} {de['d_r6_max_bag']:6.1f} {de['d_committed_max']:6.1f} {de['d_new_pickup_late']:6.1f} {str(r.get('outcome_assign_to_delivery_min'))[:6]:>6}")
    shown+=1
    if shown>=35: break
