#!/usr/bin/env python3
"""Track2 part B — margin/flip directional analysis + additive counterfactual (caveated)."""
import json, statistics
from collections import Counter, defaultdict
exec(open("/tmp/claude-0/-root/f14f1e5b-ad36-45b3-941e-c61aa4e524a1/scratchpad/track2_analyze.py").read().split("print(f\"=== records")[0])
# recs now built (the analyze script builds recs before the first print)

def bad(r):
    if r["r6_breach"] is True: return True
    if r["sla_ok"] is False: return True
    if r["eta_error"] is not None and r["eta_error"]>15: return True
    return False

print("=== (B) MARGIN / FLIP directional analysis ===")
print(f"records={len(recs)}; with runner-up={sum(1 for r in recs if r['ru'])}")

# margin distribution among decisions with a runner-up
margins=[r["margin"] for r in recs if r["margin"] is not None]
print(f"score margin (chosen - runnerup): n={len(margins)} median={statistics.median(margins):.1f} "
      f"p25={sorted(margins)[len(margins)//4]:.1f} p75={sorted(margins)[3*len(margins)//4]:.1f}")
small=[m for m in margins if m<20]
print(f"  margin<20pts: {len(small)} ({100*len(small)/len(margins):.0f}%)  margin<10: {sum(1 for m in margins if m<10)}")

# For BAD-outcome decisions, was a runner-up predicted-better on a relevant dim?
def predbetter(r, dim, thresh):
    """runner-up materially better than chosen on dim."""
    if not r["ru"]: return None
    if dim=="r6":
        a,b=r["c_r6max"],r["ru_r6max"]
        if a is None or b is None: return None
        return (a-b)>=thresh   # runner lower r6 by thresh+
    if dim=="km":
        a,b=r["c_km"],r["ru_km"]
        if a is None or b is None: return None
        return (a-b)>=thresh
    if dim=="fresh":
        # chosen stale, runner gps
        return (r["c_pos"] not in (None,"gps")) and (r["ru_pos"]=="gps")
    return None

print("\n-- Among BAD-outcome decisions: fraction where a within-margin runner-up was predicted-better --")
for s in ['ALL','luzno','srednio']:
    base=recs if s=='ALL' else [r for r in recs if r["seg"]==s]
    badset=[r for r in base if bad(r) and r["ru"]]
    print(f"\n [{s}] bad-outcome decisions with runner-up: {len(badset)}")
    for dim,th,lbl in [("r6",3,"runner-up r6_max >=3min lower"),
                       ("km",1.5,"runner-up >=1.5km closer"),
                       ("fresh",0,"runner-up gps vs chosen stale")]:
        better=[r for r in badset if predbetter(r,dim,th)]
        # within-margin subset (margin<25 = plausibly flippable by a weight tweak)
        wm=[r for r in better if r["margin"] is not None and r["margin"]<25]
        # of these, what was chosen's realized badness type
        r6b=sum(1 for r in wm if r["r6_breach"] is True)
        print(f"    {lbl}: {len(better)} predicted-better ({len(wm)} within 25pt margin; {r6b} of those the chosen R6-breached)")

# ---------- additive counterfactual (CAVEATED) on a clean weight: stopover & loadgov & v327-relax ----------
print("\n=== (C) Additive counterfactual flips (DIRECTIONAL; approx for positive-score+mult) ===")
def get(c,k,default=0.0):
    v=c.get(k); return v if isinstance(v,(int,float)) else default
def eff_delta(c, comp_delta):
    """approx effect of adding comp_delta to pre-mult score on FINAL score."""
    sc=c.get("score")
    m=c.get("v327_bundle_score_mult")
    if not isinstance(sc,(int,float)): return 0.0
    if isinstance(m,(int,float)) and m<1.0 and sc>0:  # mult was applied
        return comp_delta*m
    return comp_delta

def rerank_flip(decision, comp_fn):
    """comp_fn(cand)->additive pre-mult delta to apply. Return (orig_cid,new_cid)."""
    feas=[c for c in decision["alternatives"] if c.get("feasibility")!="NO" and isinstance(c.get("score"),(int,float))]
    if len(feas)<2: return None
    orig=max(feas,key=lambda c:c["score"])
    def ns(c): return c["score"]+eff_delta(c,comp_fn(c))
    new=max(feas,key=ns)
    if str(new.get("courier_id"))!=str(orig.get("courier_id")):
        return (orig,new)
    return None

scenarios={
  "STOPOVER x0.5 (8->4/stop)":   lambda c: -0.5*get(c,"bonus_r9_stopover"),
  "STOPOVER x2 (8->16/stop)":    lambda c: +1.0*get(c,"bonus_r9_stopover"),
  "R6-soft x2 (deter near-R6)":  lambda c: +1.0*get(c,"bonus_r6_soft_pen"),
  "BUG4-cap x2 (deter overcap)": lambda c: +1.0*get(c,"bonus_bug4_cap_soft"),
  "v3273 idle-wait x2":          lambda c: +1.0*get(c,"bonus_v3273_wait_courier"),
  "LOADGOV turn-up +1x delta":   lambda c: +1.0*get(c,"bonus_loadgov_shadow_delta"),
  "v327-mult RELAX 0.1->0.5 (less harsh cross-q)": None,  # special
}
# build decision list joined to outcome
decs=[]
for oid,d in byoid.items():
    if oid in fnd: decs.append((oid,d,fnd[oid]))
print(f"decisions for counterfactual: {len(decs)}")

def v327_relax_newscore(c):
    pre=c.get("v327_score_pre_mult"); m=c.get("v327_bundle_score_mult"); sc=c.get("score")
    if not isinstance(sc,(int,float)): return None
    if isinstance(pre,(int,float)) and isinstance(m,(int,float)) and m==0.1 and pre>0:
        # relax to 0.5: difference vs current applied
        return sc + (pre*0.5 - pre*0.1)
    return sc

for name,fn in scenarios.items():
    flips=0; flip_recs=[]
    for oid,d,f in decs:
        if name.startswith("v327-mult RELAX"):
            feas=[c for c in d["alternatives"] if c.get("feasibility")!="NO" and isinstance(c.get("score"),(int,float))]
            if len(feas)<2: continue
            orig=max(feas,key=lambda c:c["score"])
            new=max(feas,key=lambda c:(v327_relax_newscore(c) if v327_relax_newscore(c) is not None else -1e18))
            res=(orig,new) if str(new.get("courier_id"))!=str(orig.get("courier_id")) else None
        else:
            res=rerank_flip(d,fn)
        if res:
            orig,new=res
            flips+=1
            flip_recs.append((oid,orig,new,f))
    # of flips, how often did the ORIGINAL (current) choice have a bad realized outcome?
    badorig=0; r6orig=0
    seg_flips=Counter()
    for oid,orig,new,f in flip_recs:
        rr=next((r for r in recs if r["oid"]==oid),None)
        if rr and bad(rr): badorig+=1
        if rr and rr["r6_breach"] is True: r6orig+=1
        if rr: seg_flips[rr["seg"]]+=1
    print(f"  {name}: flips={flips}/{len(decs)} | of those, current-choice realized BAD={badorig} (R6-breach={r6orig}) | seg={dict(seg_flips)}")
