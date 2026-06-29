#!/usr/bin/env python3
"""Track2 refinement — SOLO vs BUNDLE split + stable sub-window. READ-ONLY."""
import json, statistics
from collections import Counter
# reuse rec builder
exec(open("/tmp/claude-0/-root/f14f1e5b-ad36-45b3-941e-c61aa4e524a1/scratchpad/track2_analyze.py").read().split("print(f\"=== records")[0])

for r in recs:
    r["day"]=(byoid[r["oid"]].get("ts") or "")[:10]

def kind(r):
    if r["is_bundle"] is True: return "BUNDLE"
    if r["is_bundle"] is False: return "SOLO"
    return "?"

def block(sub):
    eta=[r["eta_error"] for r in sub if r["eta_error"] is not None]
    r6=[r["r6_breach"] for r in sub if r["r6_breach"] is not None]
    sla=[r["sla_ok"] for r in sub if r["sla_ok"] is not None]
    med=statistics.median(eta) if eta else float('nan')
    p90=sorted(eta)[int(0.9*len(eta))] if len(eta)>=5 else float('nan')
    r6r=100*sum(1 for x in r6 if x)/len(r6) if r6 else float('nan')
    slar=100*sum(1 for x in sla if x)/len(sla) if sla else float('nan')
    return f"n={len(sub):4d} etaMed={med:5.1f} p90={p90:5.1f} r6={r6r:4.0f}%(n{len(r6)}) sla={slar:3.0f}%(n{len(sla)})"

print("=== BASELINE by SOLO/BUNDLE × segment (full window 06-18..28; weights stable) ===")
for k in ["SOLO","BUNDLE"]:
    print(f" [{k}]")
    for s in ['luzno','srednio','pf0','ALL']:
        sub=[r for r in recs if kind(r)==k and (s=='ALL' or r["seg"]==s)]
        print(f"   {s:8s}: {block(sub)}")

def feat(name, pred):
    print(f"\n-- {name} (WITH vs WITHOUT) --")
    for k in ["SOLO","BUNDLE"]:
        base=[r for r in recs if kind(r)==k]
        yes=[r for r in base if pred(r) is True]; no=[r for r in base if pred(r) is False]
        print(f"  [{k}] WITH   {block(yes)}")
        print(f"  [{k}] WO     {block(no)}")

feat("near-R6 (chosen r6_max>=30)", lambda r:(r["c_r6max"]>=30) if r["c_r6max"] is not None else None)
feat("idle-wait v3273 fired", lambda r:(r["c_v3273"]<0) if r["c_v3273"] is not None else None)
feat("stopover (bag>0)", lambda r:(r["c_stopover"]<0) if r["c_stopover"] is not None else None)
feat("v327 cross-quad mult==0.1", lambda r:(abs(r["c_mult"]-0.1)<1e-6) if r["c_mult"] is not None else None)
feat("stale pos (!=gps)", lambda r:(r["c_pos"] not in (None,"gps")) if r["c_pos"] is not None else None)
feat("loadgov fired", lambda r:(r["c_loadgov"]<0) if r["c_loadgov"] is not None else None)
feat("bug4 cap fired", lambda r:(r["c_bug4"]<0) if r["c_bug4"] is not None else None)

# stale-pos before/after checkpoint-tz fix (06-26) — SOLO
print("\n=== pos/freshness stability: SOLO stale-vs-gps before/after 06-26 (checkpoint-tz) ===")
for win,lbl in [(lambda d: d<"2026-06-26","pre-0626"),(lambda d: d>="2026-06-26","0626+")]:
    base=[r for r in recs if kind(r)=="SOLO" and win(r["day"])]
    gps=[r for r in base if r["c_pos"]=="gps"]; stale=[r for r in base if r["c_pos"] not in (None,"gps")]
    print(f"  [{lbl}] gps: {block(gps)}")
    print(f"  [{lbl}] stale:{block(stale)}")

# margin/flip on SOLO only (cleanest)
print("\n=== margin / better-alt within reach — SOLO only ===")
def bad(r):
    if r["r6_breach"] is True: return True
    if r["sla_ok"] is False: return True
    if r["eta_error"] is not None and r["eta_error"]>15: return True
    return False
solo=[r for r in recs if kind(r)=="SOLO"]
solo_bad=[r for r in solo if bad(r) and r["ru"]]
print(f"SOLO total={len(solo)}  bad-with-runnerup={len(solo_bad)}")
for dim,th,lbl in [("r6",3,"runner r6_max>=3 lower"),("km",1.5,"runner>=1.5km closer")]:
    def pb(r):
        if dim=="r6": a,b=r["c_r6max"],r["ru_r6max"]
        else: a,b=r["c_km"],r["ru_km"]
        if a is None or b is None: return False
        return (a-b)>=th
    bett=[r for r in solo_bad if pb(r)]
    wm=[r for r in bett if r["margin"] is not None and r["margin"]<25]
    r6b=sum(1 for r in wm if r["r6_breach"] is True)
    print(f"  {lbl}: {len(bett)} predicted-better ({len(wm)} within 25pt; {r6b} chosen R6-breached)")
