#!/usr/bin/env python3
"""Track2 weight calibration — outcome-grounded directional analysis.
READ-ONLY. Joins raw shadow alternatives to realized outcomes (foundation).
"""
import json, math, statistics
from collections import defaultdict, Counter

FND="/tmp/claude-0/-root/f14f1e5b-ad36-45b3-941e-c61aa4e524a1/scratchpad/decisions_outcomes_loadbucketed.jsonl"
SHA="/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl"
DIST_DECAY_KM=5.0

fnd={}
for line in open(FND):
    line=line.strip()
    if line:
        d=json.loads(line); fnd[str(d["order_id"])]=d

def load_sh(path):
    out=[]
    try:
        for line in open(path):
            line=line.strip()
            if not line: continue
            try: d=json.loads(line)
            except: continue
            if d.get("alternatives") and len(d["alternatives"])>=2: out.append(d)
    except FileNotFoundError: pass
    return out
sh=load_sh(SHA)+load_sh(SHA+".1")
sh.sort(key=lambda d: d.get("ts",""))
byoid={}
for d in sh: byoid[str(d["order_id"])]=d  # latest per order_id

def segof(pf):
    if pf is None: return 'unk'
    if pf>=5: return 'luzno'
    if pf in (2,3,4): return 'srednio'
    if pf==1: return 'ciasno'
    if pf==0: return 'pf0'
    return 'unk'

def num(x):
    return x if isinstance(x,(int,float)) else None

# Build per-decision records
recs=[]
for oid,d in byoid.items():
    f=fnd.get(oid)
    if not f: continue
    alts=d["alternatives"]
    best=d.get("best") or {}
    bcid=best.get("courier_id")
    # find chosen alt object (match courier_id); fallback to best dict
    chosen=None
    for c in alts:
        if str(c.get("courier_id"))==str(bcid): chosen=c; break
    if chosen is None: chosen=best
    # feasible non-NO candidates
    feas=[c for c in alts if c.get("feasibility")!="NO"]
    # runner-up = highest score feasible != chosen
    others=[c for c in feas if str(c.get("courier_id"))!=str(bcid)]
    runner=max(others, key=lambda c:(c.get("score") if isinstance(c.get("score"),(int,float)) else -1e18), default=None)
    pf=d.get("pool_feasible_count")
    rec=dict(
        oid=oid, seg=segof(pf), pf=pf,
        load_ewma=d.get("loadaware_shadow") if isinstance(d.get("loadaware_shadow"),(int,float)) else None,
        # realized outcome of chosen (==assigned 99.5%)
        eta_error=num(f.get("eta_error_min")),
        r6_breach=f.get("r6_breach"),
        sla_ok=f.get("sla_ok"),
        r6_actual=num(f.get("r6_actual_min")),
        delivered_source=f.get("delivered_source"),
        bag_size=f.get("bag_size"),
        is_bundle=f.get("is_bundle"),
        czasowka=f.get("czasowka"),
        # chosen weight-features
        c_score=num(chosen.get("score")),
        c_stopover=num(chosen.get("bonus_r9_stopover")) or 0.0,
        c_bundle_bonus=num(chosen.get("bundle_bonus")) or 0.0,
        c_mult=num(chosen.get("v327_bundle_score_mult")),
        c_v3273=num(chosen.get("bonus_v3273_wait_courier")) or 0.0,
        c_r6max=num(chosen.get("r6_max_bag_time_min")),
        c_r6soft=num(chosen.get("bonus_r6_soft_pen")) or 0.0,
        c_loadgov=num(chosen.get("bonus_loadgov_shadow_delta")) or 0.0,
        c_bug4=num(chosen.get("bonus_bug4_cap_soft")) or 0.0,
        c_km=num(chosen.get("km_to_pickup")),
        c_travel_cal=num(chosen.get("travel_min_cal")),
        c_pos=chosen.get("pos_source"),
        c_posage=num(chosen.get("pos_age_min")),
        c_r6bag=num(chosen.get("r6_bag_size")),
        c_feas=chosen.get("feasibility"),
        # runner-up predicted features + margin
        ru=runner is not None,
        ru_score=num(runner.get("score")) if runner else None,
        ru_km=num(runner.get("km_to_pickup")) if runner else None,
        ru_r6max=num(runner.get("r6_max_bag_time_min")) if runner else None,
        ru_travel_cal=num(runner.get("travel_min_cal")) if runner else None,
        ru_pos=runner.get("pos_source") if runner else None,
        ru_mult=num(runner.get("v327_bundle_score_mult")) if runner else None,
        ru_r6bag=num(runner.get("r6_bag_size")) if runner else None,
        n_feas=len(feas),
    )
    rec["margin"]= (rec["c_score"]-rec["ru_score"]) if (rec["c_score"] is not None and rec["ru_score"] is not None) else None
    recs.append(rec)

print(f"=== records: {len(recs)} ===")
segc=Counter(r["seg"] for r in recs)
print("segments:",dict(segc))

def bad(r):
    # realized-bad flag
    flags=[]
    if r["r6_breach"] is True: flags.append("r6")
    if r["eta_error"] is not None and r["eta_error"]>10: flags.append("late")
    if r["sla_ok"] is False: flags.append("sla")
    return flags

def stats(sub, label):
    n=len(sub)
    if n==0:
        print(f"  {label}: n=0"); return
    eta=[r["eta_error"] for r in sub if r["eta_error"] is not None]
    r6=[r["r6_breach"] for r in sub if r["r6_breach"] is not None]
    sla=[r["sla_ok"] for r in sub if r["sla_ok"] is not None]
    me=statistics.mean(eta) if eta else float('nan')
    mede=statistics.median(eta) if eta else float('nan')
    p90=sorted(eta)[int(0.9*len(eta))] if len(eta)>=5 else float('nan')
    r6r=100*sum(1 for x in r6 if x)/len(r6) if r6 else float('nan')
    slar=100*sum(1 for x in sla if x)/len(sla) if sla else float('nan')
    print(f"  {label}: n={n} | eta_err mean={me:.1f} med={mede:.1f} p90={p90:.1f} (n={len(eta)}) | r6_breach={r6r:.0f}% (n={len(r6)}) | sla_ok={slar:.0f}% (n={len(sla)})")

print("\n=== BASELINE per segment (chosen=assigned realized outcomes) ===")
for s in ['luzno','srednio','ciasno','pf0','ALL']:
    sub=recs if s=='ALL' else [r for r in recs if r["seg"]==s]
    stats(sub, s)

# ---------- (A) weight-feature correlations ----------
print("\n=== (A) OUTCOME by chosen weight-feature (ALL, then per major segment) ===")

def feature_split(name, pred):
    print(f"\n-- feature: {name} --")
    for s in ['ALL','luzno','srednio']:
        base=recs if s=='ALL' else [r for r in recs if r["seg"]==s]
        yes=[r for r in base if pred(r) is True]
        no=[r for r in base if pred(r) is False]
        print(f" [{s}]")
        stats(yes, "  WITH ")
        stats(no,  "  WITHOUT")

# stopover present (chosen carrying >=1 stop)
feature_split("chosen has stopover penalty (bag>0)", lambda r: (r["c_stopover"]<0) if r["c_stopover"] is not None else None)
# cross-quadrant bundle (mult<1)
feature_split("chosen cross/adjacent-quadrant bundle (v327 mult<1)", lambda r: (r["c_mult"]<1.0) if r["c_mult"] is not None else None)
feature_split("chosen HARD cross-quadrant (mult==0.1)", lambda r: (abs(r["c_mult"]-0.1)<1e-6) if r["c_mult"] is not None else None)
# v3273 idle wait penalty present
feature_split("chosen idle-wait penalty (v3273<0)", lambda r: (r["c_v3273"]<0) if r["c_v3273"] is not None else None)
# r6 soft penalty present (near R6)
feature_split("chosen R6-soft penalty present", lambda r: (r["c_r6soft"]<0) if r["c_r6soft"] is not None else None)
# near R6 by predicted r6max>=30
feature_split("chosen predicted r6_max>=30min", lambda r: (r["c_r6max"]>=30) if r["c_r6max"] is not None else None)
# loadgov penalty present
feature_split("chosen loadgov penalty present (tight)", lambda r: (r["c_loadgov"]<0) if r["c_loadgov"] is not None else None)
# bug4 cap soft present
feature_split("chosen bug4 cap soft penalty present", lambda r: (r["c_bug4"]<0) if r["c_bug4"] is not None else None)
# stale position (not gps)
feature_split("chosen pos_source != gps (stale/blind)", lambda r: (r["c_pos"] not in (None,"gps")) if r["c_pos"] is not None else None)
# far pickup
feature_split("chosen km_to_pickup>=4km", lambda r: (r["c_km"]>=4) if r["c_km"] is not None else None)
PY_END=0
