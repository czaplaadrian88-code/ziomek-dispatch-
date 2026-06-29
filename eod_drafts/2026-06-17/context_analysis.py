#!/usr/bin/env python3
"""Independent context: fleet saturation per day, E2-fix coverage proof, parser_degraded, KOORD window."""
import json, sys, statistics as st
from collections import Counter, defaultdict
sys.path.insert(0, "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-17")
import audit_lib as L

bf = L.load_backfill_window()
idx = json.load(open(f"{L.SCRATCH}/slim_shadow_index.json"))
def num(x):
    try: return float(x)
    except: return None

# ============ 1. FLEET SATURATION per Warsaw day ============
print("="*70)
print("FLEET SATURATION per day (Warsaw)")
print(f"{'day':>10} {'props':>5} {'KOORD':>5} {'KOORD%':>6} {'feas0':>5} {'feas<=1':>7} {'medA2D':>6} {'p90A2D':>6} {'R6>35pred%':>10}")
days = defaultdict(lambda: {"props":0,"koord":0,"feas0":0,"feasle1":0,"a2d":[],"r6pred":[]})
# shadow index: verdict + pool_feas per decision
for oid, recs in idx.items():
    for sr in recs:
        d = sr.get("day")
        if not d: continue
        dd = days[d]
        dd["props"]+=1
        if str(sr.get("verdict")).upper()=="KOORD": dd["koord"]+=1
        pf = sr.get("pool_feas")
        if pf==0: dd["feas0"]+=1
        if pf is not None and pf<=1: dd["feasle1"]+=1
# backfill outcomes per day
for r in bf:
    d = L.wday(r.get("decision_ts"))
    if not d: continue
    o=r.get("outcome") or {}
    a=num(o.get("assign_to_delivery_min"))
    if a: days[d]["a2d"].append(a)
    rp=num(r.get("predicted_r6_max_bag_min"))
    if rp is not None: days[d]["r6pred"].append(rp)
for d in sorted(days):
    if not("2026-06-10"<=d<="2026-06-16"): continue
    x=days[d]
    med = st.median(x["a2d"]) if x["a2d"] else 0
    p90 = sorted(x["a2d"])[int(len(x["a2d"])*0.9)] if x["a2d"] else 0
    r6b = 100*sum(1 for v in x["r6pred"] if v>35)/len(x["r6pred"]) if x["r6pred"] else 0
    kp = 100*x["koord"]/x["props"] if x["props"] else 0
    print(f"{d:>10} {x['props']:>5} {x['koord']:>5} {kp:6.1f} {x['feas0']:>5} {x['feasle1']:>7} {med:6.1f} {p90:6.1f} {r6b:10.1f}")

# ============ 2. E2-FIX COVERAGE PROOF ============
print("\n"+"="*70)
print("E2-FIX (within-tier: tier2-breaker sorts LAST) COVERAGE on dominated set")
dom = json.load(open(f"{L.SCRATCH}/dominated_cases.json"))
e2 = [c for c in dom if c["e2_sig"] and not c["is_artifact"]]
# fix flips a case if: best breaks committed (tier2) AND a dominator does NOT break committed
covered=0; not_tier2=0; both_tier2=0
for c in e2:
    best_breaks = bool(c.get("best_committed_breach")) or (num(c.get("best_committed_max")) or 0)>0.5
    dom_nonbreak = any((num(d.get("committed_max")) or 0)<=0.5 for d in c["all_dominators"])
    if best_breaks and dom_nonbreak: covered+=1
    elif not best_breaks: not_tier2+=1
    else: both_tier2+=1
print(f"E2-sig non-artifact dominated cases: {len(e2)}")
print(f"  COVERED by fix B (best breaks committed + non-breaking dominator exists): {covered}")
print(f"  best does NOT break committed (fix B no-op; other mechanism): {not_tier2}")
print(f"  best breaks + all dominators also break (within-tier pln decides): {both_tier2}")
# also: among the 10 committed_avoidable, how many E2 vs non-E2
ca=[c for c in dom if c["bucket"]=="PROPOSE_committed_avoidable"]
print(f"  committed_avoidable total={len(ca)} E2={sum(1 for c in ca if c['e2_sig'])} nonE2={sum(1 for c in ca if not c['e2_sig'])}")

# ============ 3. parser_degraded ============
print("\n"+"="*70)
print("parser_degraded analysis (auto_route_reason)")
pd_rows=[r for r in bf if "parser_degraded" in str(r.get("auto_route_reason"))]
oth_rows=[r for r in bf if "parser_degraded" not in str(r.get("auto_route_reason"))]
def med_a2d(rows):
    v=[num((r.get('outcome') or {}).get('assign_to_delivery_min')) for r in rows]
    v=[x for x in v if x]
    return (st.median(v) if v else 0, len(v))
print(f"parser_degraded proposals: {len(pd_rows)} / {len(bf)}")
mpd=med_a2d(pd_rows); mot=med_a2d(oth_rows)
print(f"  median assign_to_delivery: parser_degraded={mpd[0]:.1f} (n={mpd[1]}) vs others={mot[0]:.1f} (n={mot[1]})")
pdday=Counter(L.wday(r.get("decision_ts")) for r in pd_rows)
print(f"  by day: {dict(sorted((k,v) for k,v in pdday.items() if k))}")
pdhour=Counter()
for r in pd_rows:
    d=L.parse_ts(r.get("decision_ts"))
    if d: pdhour[(d.hour+2)%24]+=1
print(f"  by Warsaw hour: {dict(sorted(pdhour.items()))}")
# status distribution
sd=Counter(str((r.get('outcome') or {}).get('status')) for r in pd_rows)
print(f"  outcome status: {dict(sd)}")

# ============ 4. KOORD window (auto_koord_log) ============
print("\n"+"="*70)
print("KOORD window (auto_koord_log.jsonl)")
kk=[]
with open(L.KOORD) as f:
    for line in f:
        if not line.strip(): continue
        try: r=json.loads(line)
        except: continue
        if L.inwin(r.get("ts")): kk.append(r)
print(f"KOORD events in window: {len(kk)}")
ev=Counter(r.get("event") for r in kk)
print(f"  event types: {dict(ev)}")
rs=Counter(str(r.get("reason"))[:30] for r in kk)
print(f"  reasons: {dict(rs.most_common(8))}")
sk=sum(1 for r in kk if r.get("skipped"))
print(f"  skipped: {sk}")
czas=[num(r.get("czas_odbioru_min")) for r in kk if num(r.get("czas_odbioru_min"))]
if czas: print(f"  czas_odbioru_min: median={st.median(czas):.0f} max={max(czas):.0f} (>=60=czasowka: {sum(1 for c in czas if c>=60)})")
