#!/usr/bin/env python3
"""Track 1 v2 — per-day regime, SOLO-primary, BUNDLE-separate, recent stable window."""
import json, statistics, math
from collections import defaultdict, Counter
from datetime import datetime, timedelta, timezone

PATH="/tmp/claude-0/-root/f14f1e5b-ad36-45b3-941e-c61aa4e524a1/scratchpad/decisions_outcomes_loadbucketed.jsonl"
rows=[json.loads(l) for l in open(PATH)]

def med(xs):
    xs=[x for x in xs if x is not None]; return statistics.median(xs) if xs else None
def tmean(xs, frac=0.10):
    xs=sorted(x for x in xs if x is not None); n=len(xs)
    if n==0: return None
    k=int(n*frac); core=xs[k:n-k] if n-2*k>0 else xs
    return statistics.fmean(core)
def pct(xs,p):
    xs=sorted(x for x in xs if x is not None)
    if not xs: return None
    if len(xs)==1: return xs[0]
    idx=(len(xs)-1)*p; lo=math.floor(idx); hi=math.ceil(idx)
    return xs[lo] if lo==hi else xs[lo]+(xs[hi]-xs[lo])*(idx-lo)
def cnt(xs): return sum(1 for x in xs if x is not None)

# Warsaw operational day (June = UTC+2, no DST change in window)
def wday(r):
    dt=datetime.fromisoformat(r["decision_ts_utc"])
    return (dt+timedelta(hours=2)).strftime("%Y-%m-%d")
def segment(r):
    pf=r.get("pool_feasible")
    if pf is None: return None
    if pf==0: return "pf0"
    if pf==1: return "ciasno"
    if pf in (2,3,4): return "srednio"
    return "luzno"
for r in rows:
    r["wday"]=wday(r); r["seg"]=segment(r)
    b=r.get("bag_size")
    r["kind"]= None if b is None else ("solo" if b==1 else "bundle")

def ok(r): return r.get("eta_error_min") is not None and not r.get("is_koord")

# ============ (a) PER-DAY SOLO vs BUNDLE sign table ============
print("=== (a) PER-DAY eta_error: SOLO vs BUNDLE (Warsaw day, non-koord) ===")
print(f"{'day':12s} | {'SOLO med':>8s} {'n':>4s} {'tmean':>6s} | {'BUND med':>8s} {'n':>4s} {'tmean':>6s} | {'ALL med':>7s} {'n':>4s}")
days=sorted(set(r["wday"] for r in rows))
daily={}
for d in days:
    solo=[r["eta_error_min"] for r in rows if ok(r) and r["wday"]==d and r["kind"]=="solo"]
    bund=[r["eta_error_min"] for r in rows if ok(r) and r["wday"]==d and r["kind"]=="bundle"]
    allr=[r["eta_error_min"] for r in rows if ok(r) and r["wday"]==d]
    daily[d]=(med(solo),len(solo),med(bund),len(bund))
    sm=med(solo); bm=med(bund)
    print(f"{d:12s} | {sm if sm is not None else float('nan'):8.1f} {len(solo):4d} {tmean(solo) if solo else float('nan'):6.1f} | "
          f"{bm if bm is not None else float('nan'):8.1f} {len(bund):4d} {tmean(bund) if bund else float('nan'):6.1f} | "
          f"{med(allr):7.1f} {len(allr):4d}")

# sign flips
print("\nSIGN of SOLO median per day:", " ".join(f"{d[5:]}:{'+' if daily[d][0] and daily[d][0]>0 else '-' if daily[d][0] is not None and daily[d][0]<0 else '?'}" for d in days))
print("SIGN of BUND median per day:", " ".join(f"{d[5:]}:{'+' if daily[d][2] and daily[d][2]>0 else '-' if daily[d][2] is not None and daily[d][2]<0 else '?'}" for d in days))

# ============ (b) choose recent stable 2-3d window ============
print("\n=== (b) recent-window stability scan (SOLO medians, last 6 days) ===")
recent=days[-6:]
for d in recent:
    sm,sn,bm,bn=daily[d]
    print(f"  {d}: SOLO med={sm:.1f} (n={sn})  BUND med={bm:.1f} (n={bn})")
# candidate windows
print("\n  candidate consecutive windows (SOLO):")
for w in [days[-2:],days[-3:],days[-4:-1],days[-3:-0] if False else days[-4:-2]]:
    pass
for combo in [days[-3:], days[-2:], days[-4:-1], [days[-3],days[-2]], [days[-4],days[-3],days[-2]]]:
    label="+".join(c[5:] for c in combo)
    solo=[r["eta_error_min"] for r in rows if ok(r) and r["wday"] in combo and r["kind"]=="solo"]
    bund=[r["eta_error_min"] for r in rows if ok(r) and r["wday"] in combo and r["kind"]=="bundle"]
    print(f"   [{label}] SOLO n={len(solo):3d} med={med(solo):.1f} p25={pct(solo,.25):.1f} p75={pct(solo,.75):.1f} | BUND n={len(bund):3d} med={med(bund):.1f}")

# ============ (c) CALIBRATION on chosen window — SOLO first, by load ============
# choose window after inspecting; set here, re-run prints both
for WIN in [["2026-06-26","2026-06-27","2026-06-28"], ["2026-06-27","2026-06-28"]]:
    label="+".join(c[5:] for c in WIN)
    print(f"\n=== (c) CALIBRATION window {label} ===")
    win=[r for r in rows if ok(r) and r["wday"] in WIN]
    print(f"  total non-koord eta rows in window: {len(win)}  (solo {sum(1 for r in win if r['kind']=='solo')}, bundle {sum(1 for r in win if r['kind']=='bundle')})")
    for kind in ["solo","bundle"]:
        print(f"  --- {kind.upper()} x load ---")
        for seg in ["luzno","srednio","ciasno","pf0"]:
            xs=[r["eta_error_min"] for r in win if r["kind"]==kind and r["seg"]==seg]
            tag="CALIB" if len(xs)>=30 else "thin "
            if xs:
                print(f"    {tag} {seg:8s} n={len(xs):3d} med={med(xs):6.1f} tmean={tmean(xs):6.1f} p25={pct(xs,.25):6.1f} p75={pct(xs,.75):6.1f}")
            else:
                print(f"    ----- {seg:8s} n=0")
    # class breakdown feasibility in window (solo)
    print("  --- SOLO x load x class cell counts (is class breakdown viable?) ---")
    cc=Counter((r["seg"],r["tier"]) for r in win if r["kind"]=="solo" and r["seg"] in ("luzno","srednio","ciasno"))
    viable=sum(1 for k,v in cc.items() if v>=30)
    print(f"    cells with n>=30: {viable} / {len(cc)} -> {'class breakdown OK' if viable>=3 else 'TOO THIN, drop class, report solo/bundle x load only'}")
    for k in sorted(cc): print(f"      {k} n={cc[k]}")
PY