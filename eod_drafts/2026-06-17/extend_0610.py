#!/usr/bin/env python3
"""Extend dominance pass to 2026-06-10 (in rotated shadow_decisions.jsonl.1) for true 7-day window."""
import json, sys, statistics as st
from collections import Counter
sys.path.insert(0, "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-17")
import audit_lib as L

ROT = f"{L.WS}/scripts/logs/shadow_decisions.jsonl.1"
recs_0610 = []
with open(ROT) as f:
    for line in f:
        if not line.strip(): continue
        try: r = json.loads(line)
        except: continue
        if L.wday(r.get("ts")) == "2026-06-10":
            recs_0610.append(L.slim_record(r))
print(f"06-10 shadow decisions: {len(recs_0610)}")

verd = Counter(); feas0=0; feasle1=0
dominated=[]; n=0
for sr in recs_0610:
    n+=1
    verd[str(sr.get("verdict")).upper()]+=1
    pf = sr.get("pool_feas")
    if pf==0: feas0+=1
    if pf is not None and pf<=1: feasle1+=1
    a = L.analyze_decision(sr)
    if a and a["dominators"]:
        doms=a["dominators"]
        best_r6=L.num(a["best_r6"]) or 0
        top=max(doms, key=lambda d:(d["deltas"]["d_breach_count"],d["deltas"]["d_committed_max"],d["deltas"]["d_r6_max_bag"]))
        is_art = best_r6>L.R6_ARTIFACT_MIN or top["deltas"]["d_r6_max_bag"]>L.R6_ARTIFACT_MIN
        dominated.append({"order_id":sr["order_id"],"verdict":sr["verdict"],"e2":L.e2_signature(sr["order_id"]),
                          "saturation":pf==0,"is_artifact":is_art,"best_cid":a["best_cid"],
                          "best_committed_breach":a["best_committed_breach"],
                          "dom_cid":top["cid"],"d_r6":top["deltas"]["d_r6_max_bag"],
                          "d_committed":top["deltas"]["d_committed_max"],"best_is_score_top":a["best_is_score_top"]})
print(f"verdict dist: {dict(verd)}  KOORD%={100*verd.get('KOORD',0)/max(1,n):.1f}")
print(f"feas0={feas0} feas<=1={feasle1}")
real=[d for d in dominated if not d["is_artifact"] and str(d["verdict"]).upper()!="KOORD"]
print(f"dominated total={len(dominated)} (artifact={sum(1 for d in dominated if d['is_artifact'])} KOORD={sum(1 for d in dominated if str(d['verdict']).upper()=='KOORD')})")
print(f"REAL propose-dominated 06-10: {len(real)}  E2={sum(1 for d in real if d['e2'])} nonE2={sum(1 for d in real if not d['e2'])}")
cb=[d for d in real if (L.num(d['d_committed']) or 0)>2]
print(f"  committed-avoidable (dCommit>2): {len(cb)}")
print(f"  best_is_score_top among real: {sum(1 for d in real if d['best_is_score_top'])}/{len(real)}")
# combined 7-day headline
import os
prev=json.load(open(f"{L.SCRATCH}/dominated_cases.json"))
prev_real=[c for c in prev if not c['is_artifact'] and str(c['verdict']).upper()!='KOORD']
print(f"\n=== 7-DAY COMBINED (06-10..06-16) ===")
print(f"decisions: {len(recs_0610)} (06-10) + 1712 (06-11..16) = {len(recs_0610)+1712}")
print(f"REAL propose-dominated: {len(real)} (06-10) + {len(prev_real)} (06-11..16) = {len(real)+len(prev_real)}")
