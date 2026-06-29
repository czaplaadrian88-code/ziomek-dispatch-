#!/usr/bin/env python3
"""Extract & categorize all StructuredOutput results from workflow agents."""
import json, glob, os
from collections import Counter
D="/root/.claude/projects/-root/6ddc95a2-f7c5-49cf-8826-85e752da3613/subagents/workflows/wf_4b0b7718-c88"

def structured_outputs(path):
    outs=[]
    for l in open(path):
        l=l.strip()
        if not l: continue
        try: r=json.loads(l)
        except: continue
        msg=r.get("message") or r
        cont=msg.get("content") if isinstance(msg,dict) else None
        if isinstance(cont,list):
            for b in cont:
                if isinstance(b,dict) and b.get("type")=="tool_use" and "tructured" in str(b.get("name","")):
                    outs.append(b.get("input"))
    return outs

recon=None; deepdive=[]; verify=[]; synth=None
for f in glob.glob(f"{D}/agent-*.jsonl"):
    for o in structured_outputs(f):
        if not isinstance(o,dict): continue
        if "score_axes_summary" in o: recon=o
        elif "verdicts" in o: deepdive.append(o)
        elif "refuted" in o: verify.append(o)
        elif "clusters_markdown" in o or "top_findings_pl" in o: synth=o

allv=[v for dd in deepdive for v in (dd.get("verdicts") or [])]
print(f"agents with structured output: recon={recon is not None} deepdive_batches={len(deepdive)} verify={len(verify)} synth={synth is not None}")
print(f"total deepdive verdicts: {len(allv)}")
cls=Counter(v.get("classification") for v in allv)
print("\n=== CLASSIFICATION COUNTS (deepdive 52) ===")
for k,n in cls.most_common(): print(f"  {k:30s} {n}")
pol=[v for v in allv if v.get("policy_review")]
print(f"  policy_review flagged: {len(pol)}")

print("\n=== score_miscalibration_REAL cases ===")
real=[v for v in allv if v.get("classification")=="score_miscalibration_REAL"]
for v in real:
    print(f"  {v.get('order_id')} best={v.get('best_cid')}->dom={v.get('dominator_cid')} dR6={v.get('d_r6')} dCom={v.get('d_committed')} axis={str(v.get('culprit_axis'))[:60]} conf={v.get('confidence')}")

print(f"\n=== VERIFY results ({len(verify)}) ===")
byo={}
for r in verify: byo.setdefault(r.get("order_id"),[]).append(r)
for oid,rs in byo.items():
    ref=sum(1 for r in rs if r.get("refuted")); holds=sum(1 for r in rs if r.get("better_option_holds"))
    print(f"  {oid}: refuted={ref}/{len(rs)} holds={holds}/{len(rs)}  -> {'CONFIRMED' if holds>=ref and holds>0 else 'refuted/uncertain'}")
    for r in rs: print(f"      {str(r.get('reason'))[:130]}")

if synth:
    print("\n=== SYNTH present: keys ===", list(synth.keys()))
    print("top_findings_pl:", len(synth.get("top_findings_pl") or []))
    print("backlog items:", len(synth.get("backlog") or []))
# save aggregate
json.dump({"recon":recon,"deepdive_verdicts":allv,"classification_counts":dict(cls),
           "verify":verify,"synth":synth},
          open("/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-17/wf_results.json","w"),
          ensure_ascii=False, indent=1)
print("\nsaved wf_results.json")
