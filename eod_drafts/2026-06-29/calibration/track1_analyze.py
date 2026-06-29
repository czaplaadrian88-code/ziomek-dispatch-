#!/usr/bin/env python3
"""Track 1 prediction calibration analysis. READ-ONLY on repo; outputs to scratchpad."""
import json, statistics, math
from collections import defaultdict, Counter

PATH="/tmp/claude-0/-root/f14f1e5b-ad36-45b3-941e-c61aa4e524a1/scratchpad/decisions_outcomes_loadbucketed.jsonl"
rows=[json.loads(l) for l in open(PATH)]

# ---------- helpers ----------
def med(xs):
    xs=[x for x in xs if x is not None]
    return statistics.median(xs) if xs else None
def trimmed_mean(xs, frac=0.10):
    xs=sorted(x for x in xs if x is not None)
    n=len(xs)
    if n==0: return None
    k=int(n*frac)
    core=xs[k:n-k] if n-2*k>0 else xs
    return statistics.fmean(core)
def pct(xs,p):
    xs=sorted(x for x in xs if x is not None)
    if not xs: return None
    if len(xs)==1: return xs[0]
    idx=(len(xs)-1)*p
    lo=math.floor(idx); hi=math.ceil(idx)
    if lo==hi: return xs[lo]
    return xs[lo]+(xs[hi]-xs[lo])*(idx-lo)
def cnt(xs): return sum(1 for x in xs if x is not None)

# ---------- recompute segment / class / bag ----------
def segment(r):
    pf=r.get("pool_feasible")
    if pf is None:
        ew=r.get("load_ewma")
        if ew is None: return None
        if ew<=1.8: return "luzno"
        if ew<=3.0: return "srednio"
        return "ciasno"
    if pf==0: return "pf0_degenerate"
    if pf==1: return "ciasno"
    if pf in (2,3,4): return "srednio"
    return "luzno"  # >=5

def bag3(r):
    b=r.get("bag_size")
    if b is None: return None
    if b==1: return "1solo"
    if b==2: return "2"
    return "3+"

for r in rows:
    r["seg"]=segment(r)
    r["bag3"]=bag3(r)
    # day key
    r["day"]=r["decision_ts_utc"][:10]
    # drive miss
    ad=r.get("actual_delivery_min"); pd=r.get("predicted_drive_min"); cd=r.get("calibrated_drive_min")
    r["drive_miss_pred"]= (ad-pd) if (ad is not None and pd is not None) else None
    r["drive_miss_cal"]= (ad-cd) if (ad is not None and cd is not None) else None
    # pickup-side residual = eta_error - drive_miss_pred
    e=r.get("eta_error_min")
    r["pickup_resid"]= (e - r["drive_miss_pred"]) if (e is not None and r["drive_miss_pred"] is not None) else None

SEG_ORDER=["luzno","srednio","ciasno","pf0_degenerate"]
CLASS_ORDER=["gold","std+","std","new","slow"]
BAG_ORDER=["1solo","2","3+"]

# calibratable population = eta_error present, exclude koord & pf0 for the *fit*
def is_fitrow(r):
    return (r.get("eta_error_min") is not None and not r.get("is_koord")
            and r.get("seg") in ("luzno","srednio","ciasno"))

print("=== population ===")
print("rows total", len(rows))
print("eta present", cnt([r.get("eta_error_min") for r in rows]))
print("koord", sum(1 for r in rows if r.get("is_koord")))
print("seg counts", Counter(r["seg"] for r in rows))
print("fit rows (non-koord, seg in luzno/srednio/ciasno, eta present)", sum(1 for r in rows if is_fitrow(r)))

# ============ 1. CELL COUNTS seg x class x bag ============
print("\n=== CELL COUNTS seg x class x bag (eta-present, non-koord) ===")
cell=defaultdict(list)
for r in rows:
    if r.get("eta_error_min") is None or r.get("is_koord"): continue
    if r["seg"]=="pf0_degenerate": continue
    if r["seg"] is None or r["bag3"] is None: continue
    cell[(r["seg"],r["tier"],r["bag3"])].append(r["eta_error_min"])
calib=[]; thin=[]
for k in sorted(cell, key=lambda k:(SEG_ORDER.index(k[0]),CLASS_ORDER.index(k[1]),BAG_ORDER.index(k[2]))):
    n=len(cell[k])
    (calib if n>=30 else thin).append((k,n))
print(f"calibratable cells (n>=30): {len(calib)} ;  thin cells (<30): {len(thin)}")
for k,n in calib: print(f"  CALIB {k} n={n} median={med(cell[k]):.1f}")
print("  --- thin (inherit from margin) ---")
for k,n in thin: print(f"  thin  {k} n={n}")

# ============ 2. HEADLINE TABLE + margins ============
def stats_block(xs):
    return dict(n=cnt(xs), median=med(xs), tmean=trimmed_mean(xs,0.10),
                p25=pct(xs,0.25), p75=pct(xs,0.75))

print("\n=== seg x class x bag headline (calibratable cells) ===")
for k,n in calib:
    s=stats_block(cell[k])
    print(f"  {k[0]:8s} {k[1]:5s} {k[2]:5s} n={s['n']:4d} med={s['median']:6.1f} tmean={s['tmean']:6.1f} p25={s['p25']:6.1f} p75={s['p75']:6.1f}")

# 1-way margins
def margin(keyfn, label):
    g=defaultdict(list)
    for r in rows:
        if r.get("eta_error_min") is None or r.get("is_koord"): continue
        if r["seg"]=="pf0_degenerate" or r["seg"] is None: continue
        kk=keyfn(r)
        if kk is None: continue
        g[kk].append(r["eta_error_min"])
    print(f"\n--- margin: {label} ---")
    order={"seg":SEG_ORDER,"class":CLASS_ORDER,"bag":BAG_ORDER}.get(label)
    keys=sorted(g, key=lambda x: order.index(x)) if order else sorted(g)
    for kk in keys:
        s=stats_block(g[kk])
        print(f"  {str(kk):10s} n={s['n']:4d} med={s['median']:6.1f} tmean={s['tmean']:6.1f} p25={s['p25']:6.1f} p75={s['p75']:6.1f}")
    return g
seg_m=margin(lambda r:r["seg"],"seg")
cls_m=margin(lambda r:r["tier"],"class")
bag_m=margin(lambda r:r["bag3"],"bag")

# 2-way margins
def margin2(f1,f2,l1,l2,o1,o2):
    g=defaultdict(list)
    for r in rows:
        if r.get("eta_error_min") is None or r.get("is_koord"): continue
        if r["seg"]=="pf0_degenerate" or r["seg"] is None: continue
        a=f1(r); b=f2(r)
        if a is None or b is None: continue
        g[(a,b)].append(r["eta_error_min"])
    print(f"\n--- 2-way margin: {l1} x {l2} ---")
    for a in o1:
        for b in o2:
            xs=g.get((a,b),[])
            if not xs: continue
            print(f"  {a:8s} {b:6s} n={len(xs):4d} med={med(xs):6.1f} tmean={trimmed_mean(xs):6.1f}")
    return g
margin2(lambda r:r["seg"],lambda r:r["bag3"],"seg","bag",SEG_ORDER[:3],BAG_ORDER)
margin2(lambda r:r["seg"],lambda r:r["tier"],"seg","class",SEG_ORDER[:3],CLASS_ORDER)

# pf0 + koord reported separately
print("\n=== pf0_degenerate (reported, excluded from fit) ===")
pf0=[r["eta_error_min"] for r in rows if r["seg"]=="pf0_degenerate" and r.get("eta_error_min") is not None]
print(f"  n={len(pf0)} med={med(pf0):.1f} tmean={trimmed_mean(pf0):.1f} p25={pct(pf0,.25):.1f} p75={pct(pf0,.75):.1f}")
print("=== KOORD (reported, excluded) ===")
ko=[r["eta_error_min"] for r in rows if r.get("is_koord") and r.get("eta_error_min") is not None]
print(f"  n={len(ko)} med={med(ko):.1f}")

# ============ 3. DECOMPOSITION ============
print("\n=== DECOMPOSITION by segment (medians; honest n per source) ===")
for seg in ["luzno","srednio","ciasno"]:
    rs=[r for r in rows if r["seg"]==seg and not r.get("is_koord")]
    e=[r.get("eta_error_min") for r in rs]
    dmp=[r.get("drive_miss_pred") for r in rs]
    dmc=[r.get("drive_miss_cal") for r in rs]
    presid=[r.get("pickup_resid") for r in rs]
    slip=[r.get("pickup_slip_min") for r in rs]
    dwell=[r.get("dwell_actual_min") for r in rs]
    prep=[r.get("prep_bias_min") for r in rs]
    print(f"\n  [{seg}] rows={len(rs)}")
    print(f"    eta_error          med={med(e):6.2f}  n={cnt(e)}")
    print(f"    drive_miss(pred)   med={med(dmp):6.2f}  n={cnt(dmp)}   (actual_deliv - predicted_drive)")
    print(f"    drive_miss(calib)  med={med(dmc):6.2f}  n={cnt(dmc)}   (actual_deliv - calibrated_drive)")
    print(f"    pickup_resid       med={med(presid):6.2f}  n={cnt(presid)}   (eta_error - drive_miss_pred)")
    print(f"    pickup_slip        med={med(slip):6.2f}  n={cnt(slip)}   (picked - declared_ready)")
    print(f"    dwell_actual       med={med(dwell):6.2f}  n={cnt(dwell)}   (picked - arrived)")
    print(f"    prep_bias          med={med(prep):6.2f}  n={cnt(prep)}")

# ============ 4. R6 BREACH ============
print("\n=== R6-REALITY breach (engine deemed feasible predicted_r6<=35, did it actually breach 35?) ===")
def r6_block(rs):
    # rows where engine predicted feasible (<=35) AND we have r6_actual
    feas=[r for r in rs if r.get("predicted_r6_max_bag_min") is not None and r["predicted_r6_max_bag_min"]<=35.0
          and r.get("r6_actual_min") is not None]
    if not feas: return None
    breaches=[r for r in feas if r["r6_actual_min"]>35.0]
    return len(feas), len(breaches), 100*len(breaches)/len(feas), med([r["r6_actual_min"] for r in feas]), med([r["r6_actual_min"] for r in breaches]) if breaches else None
print(" by segment (predicted-feasible bags only):")
for seg in ["luzno","srednio","ciasno"]:
    rs=[r for r in rows if r["seg"]==seg and not r.get("is_koord")]
    b=r6_block(rs)
    if b: print(f"   {seg:8s} feasN={b[0]:4d} breachN={b[1]:4d} rate={b[2]:5.1f}%  med_r6_actual={b[3]:.1f} med_r6_breaching={b[4] if b[4] else float('nan'):.1f}")
    else: print(f"   {seg:8s} no data")
print(" by bag size:")
for bg in BAG_ORDER:
    rs=[r for r in rows if r["bag3"]==bg and not r.get("is_koord") and r["seg"] in ("luzno","srednio","ciasno")]
    b=r6_block(rs)
    if b: print(f"   {bg:6s} feasN={b[0]:4d} breachN={b[1]:4d} rate={b[2]:5.1f}%  med_r6_actual={b[3]:.1f}")
print(" by seg x bag:")
for seg in ["luzno","srednio","ciasno"]:
    for bg in BAG_ORDER:
        rs=[r for r in rows if r["seg"]==seg and r["bag3"]==bg and not r.get("is_koord")]
        b=r6_block(rs)
        if b and b[0]>=15: print(f"   {seg:8s} {bg:6s} feasN={b[0]:4d} breachN={b[1]:4d} rate={b[2]:5.1f}%")

# r6 coverage check
print("\n r6 coverage by segment:")
for seg in ["luzno","srednio","ciasno"]:
    rs=[r for r in rows if r["seg"]==seg and not r.get("is_koord")]
    have=sum(1 for r in rs if r.get("r6_actual_min") is not None)
    print(f"   {seg}: {have}/{len(rs)} have r6_actual")

# ============ 5+6. CORRECTION FIT + OOS VALIDATION ============
# split by day. train = earlier 10 days (06-15..06-24), test = last 4 (06-25..06-28)
TEST_DAYS={"2026-06-25","2026-06-26","2026-06-27","2026-06-28"}
def split(r): return "test" if r["day"] in TEST_DAYS else "train"
train=[r for r in rows if is_fitrow(r) and split(r)=="train"]
test =[r for r in rows if is_fitrow(r) and split(r)=="test"]
print(f"\n=== OOS split: train n={len(train)} (days <=06-24), test n={len(test)} (06-25..28) ===")

# correction per segment = train median eta_error (additive buffer to ADD to predicted promise)
seg_corr={}
for seg in ["luzno","srednio","ciasno"]:
    xs=[r["eta_error_min"] for r in train if r["seg"]==seg]
    seg_corr[seg]=med(xs)
print("per-segment correction (train median eta_error = buffer to add):")
for seg in ["luzno","srednio","ciasno"]:
    print(f"   {seg}: +{seg_corr[seg]:.1f} min (train n={sum(1 for r in train if r['seg']==seg)})")

# also seg x bag correction
segbag_corr={}
for seg in ["luzno","srednio","ciasno"]:
    for bg in BAG_ORDER:
        xs=[r["eta_error_min"] for r in train if r["seg"]==seg and r["bag3"]==bg]
        if cnt(xs)>=30:
            segbag_corr[(seg,bg)]=med(xs)
print("per-seg x bag correction (train median, n>=30):")
for k,v in segbag_corr.items(): print(f"   {k}: +{v:.1f}")

# global correction (single number)
glob_corr=med([r["eta_error_min"] for r in train])
print(f"global correction: +{glob_corr:.1f}")

# OOS eval: residual = actual - (predicted + correction) = eta_error - correction
def eval_oos(corr_fn, label):
    base=[abs(r["eta_error_min"]) for r in test]
    after=[abs(r["eta_error_min"]-corr_fn(r)) for r in test]
    mb=med(base); ma=med(after)
    # signed median (bias)
    sb=med([r["eta_error_min"] for r in test]); sa=med([r["eta_error_min"]-corr_fn(r) for r in test])
    print(f"  [{label}] median|err| {mb:.2f} -> {ma:.2f} ({100*(mb-ma)/mb:+.1f}%)   signed median {sb:+.2f} -> {sa:+.2f}   n={len(test)}")
    return mb,ma

print("\n=== OOS VALIDATION (held-out 06-25..28), median |eta_error| before->after ===")
eval_oos(lambda r:0.0, "no correction (baseline)")
eval_oos(lambda r:glob_corr, "global +{:.1f}".format(glob_corr))
eval_oos(lambda r:seg_corr.get(r["seg"],0.0), "per-segment")
eval_oos(lambda r:segbag_corr.get((r["seg"],r["bag3"]), seg_corr.get(r["seg"],0.0)), "per-seg x bag (fallback seg)")

# per-segment OOS breakdown
print("\n per-segment OOS detail (per-segment correction):")
for seg in ["luzno","srednio","ciasno"]:
    ts=[r for r in test if r["seg"]==seg]
    if not ts: continue
    base=[abs(r["eta_error_min"]) for r in ts]
    after=[abs(r["eta_error_min"]-seg_corr[seg]) for r in ts]
    print(f"   {seg:8s} n={len(ts):4d} corr+{seg_corr[seg]:4.1f}  med|err| {med(base):5.2f} -> {med(after):5.2f}  signed {med([r['eta_error_min'] for r in ts]):+5.2f} -> {med([r['eta_error_min']-seg_corr[seg] for r in ts]):+5.2f}")

# R6-tied buffer: what additive buffer to predicted_r6 makes predicted-feasible bags land <=35 actual?
print("\n=== R6-tied buffer (per segment): buffer s.t. predicted_r6 + buffer >= actual_r6 for ~target% ===")
for seg in ["luzno","srednio","ciasno"]:
    rs=[r for r in rows if r["seg"]==seg and not r.get("is_koord")
        and r.get("predicted_r6_max_bag_min") is not None and r["predicted_r6_max_bag_min"]<=35
        and r.get("r6_actual_min") is not None]
    if len(rs)<15:
        print(f"   {seg}: n={len(rs)} too thin"); continue
    gaps=[r["r6_actual_min"]-r["predicted_r6_max_bag_min"] for r in rs]
    print(f"   {seg}: n={len(rs)} median r6 gap(actual-pred)={med(gaps):.1f}  p75={pct(gaps,.75):.1f}  p90={pct(gaps,.90):.1f}")

# sensitivity: train on first 9 days vs test last 5 (alt split) to confirm robustness
print("\n=== alt split robustness: train<=06-23 (9d), test 06-24..28 (5d) ===")
TEST2={"2026-06-24","2026-06-25","2026-06-26","2026-06-27","2026-06-28"}
tr2=[r for r in rows if is_fitrow(r) and r["day"] not in TEST2]
te2=[r for r in rows if is_fitrow(r) and r["day"] in TEST2]
sc2={seg:med([r["eta_error_min"] for r in tr2 if r["seg"]==seg]) for seg in ["luzno","srednio","ciasno"]}
print("  train corr:", {k:round(v,1) for k,v in sc2.items()}, f"(train n={len(tr2)}, test n={len(te2)})")
base=[abs(r["eta_error_min"]) for r in te2]; after=[abs(r["eta_error_min"]-sc2.get(r["seg"],0)) for r in te2]
print(f"  per-seg OOS: med|err| {med(base):.2f} -> {med(after):.2f} ({100*(med(base)-med(after))/med(base):+.1f}%)")
