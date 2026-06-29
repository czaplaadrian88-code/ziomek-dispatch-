#!/usr/bin/env python3
"""Rigorous load-vs-clock: marginal eta2 + PARTIAL eta2 (control for the other axis)."""
import json, statistics
from collections import defaultdict
OUT = "/tmp/claude-0/-root/f14f1e5b-ad36-45b3-941e-c61aa4e524a1/scratchpad"
rows = [json.loads(l) for l in open(f"{OUT}/decisions_outcomes_loadbucketed.jsonl")]

def val(r):
    e = r["eta_error_min"]
    return e if (e is not None and -120 <= e <= 240) else None

# ---- eta_error by pool_feasible (the load curve) ----
print("=== eta_error_min median by pool_feasible (the LOAD curve) ===")
g = defaultdict(list)
for r in rows:
    v = val(r)
    if v is not None and r["pool_feasible"] is not None:
        g[r["pool_feasible"]].append(v)
for pf in sorted(g):
    vs = g[pf]
    if len(vs) >= 20:
        print(f"  pool_feasible={pf:2d}  n={len(vs):4d}  median={statistics.median(vs):6.1f}  mean={statistics.mean(vs):6.1f}")
print("  (low pool_feasible = high load/niedobor; high = luzno)")

# ---- eta_error by load_active_orders/couriers ratio where available ----
print("\n=== eta_error by active_orders-per-courier (shadow ewma subset) ===")
g2 = defaultdict(list)
for r in rows:
    v = val(r)
    ao, ac = r.get("load_active_orders"), r.get("load_active_couriers")
    if v is not None and ao is not None and ac:
        ratio = ao/ac
        bucket = "<=0.5" if ratio<=0.5 else "0.5-1.0" if ratio<=1.0 else "1.0-1.5" if ratio<=1.5 else ">1.5"
        g2[bucket].append(v)
for b in ["<=0.5","0.5-1.0","1.0-1.5",">1.5"]:
    vs = g2.get(b,[])
    if vs: print(f"  ratio {b:8s} n={len(vs):4d} median={statistics.median(vs):6.1f}")

# ---- variance-explained helpers ----
def eta2(pairs):
    # pairs: list of (group, value)
    groups = defaultdict(list); allv=[]
    for gkey,v in pairs:
        if gkey is None or v is None: continue
        groups[gkey].append(v); allv.append(v)
    if len(allv)<2: return None,0
    grand = statistics.mean(allv)
    sst = sum((v-grand)**2 for v in allv)
    ssb = sum(len(vs)*(statistics.mean(vs)-grand)**2 for vs in groups.values())
    return (ssb/sst if sst>0 else 0.0), len(allv)

def residualize(rows, groupfn):
    """Return dict order_id-> residual eta_error after removing group mean."""
    groups = defaultdict(list)
    base = []
    for i,r in enumerate(rows):
        v = val(r); gk = groupfn(r)
        if v is None or gk is None:
            base.append(None); continue
        base.append((i,gk,v)); groups[gk].append(v)
    gmean = {gk: statistics.mean(vs) for gk,vs in groups.items()}
    resid = {}
    for item in base:
        if item is None: continue
        i,gk,v = item
        resid[i] = v - gmean[gk]
    return resid

# marginal
load_fn = lambda r: r["pool_feasible"]
hour_fn = lambda r: r["decision_hour_warsaw"]
lb_fn   = lambda r: r["load_bucket"]

m_load,_ = eta2([(load_fn(r), val(r)) for r in rows])
m_hour,_ = eta2([(hour_fn(r), val(r)) for r in rows])
m_lb,_   = eta2([(lb_fn(r), val(r)) for r in rows])
print(f"\n=== MARGINAL eta2 (full {sum(1 for r in rows if val(r) is not None)} rows) ===")
print(f"  pool_feasible (14 grp): {m_load:.4f}")
print(f"  load_bucket   ( 4 grp): {m_lb:.4f}")
print(f"  hour_warsaw   (15 grp): {m_hour:.4f}")

# PARTIAL: explain hour-residuals by load (load's unique contribution) and vice versa
resid_hour = residualize(rows, hour_fn)   # remove hour effect
resid_load = residualize(rows, load_fn)   # remove load(pool) effect
# load explaining hour-residuals:
p_load_after_hour,_ = eta2([(load_fn(r), resid_hour.get(i)) for i,r in enumerate(rows) if i in resid_hour])
# hour explaining load-residuals:
p_hour_after_load,_ = eta2([(hour_fn(r), resid_load.get(i)) for i,r in enumerate(rows) if i in resid_load])
print(f"\n=== PARTIAL eta2 (unique contribution after controlling for the other) ===")
print(f"  pool_feasible | after removing hour : {p_load_after_hour:.4f}   <- load's UNIQUE power")
print(f"  hour_warsaw   | after removing load : {p_hour_after_load:.4f}   <- clock's UNIQUE power")
print("  (larger = stronger independent axis)")

# also load_bucket partials
resid_hour2 = residualize(rows, hour_fn)
p_lb_after_hour,_ = eta2([(lb_fn(r), resid_hour2.get(i)) for i,r in enumerate(rows) if i in resid_hour2])
resid_lb = residualize(rows, lb_fn)
p_hour_after_lb,_ = eta2([(hour_fn(r), resid_lb.get(i)) for i,r in enumerate(rows) if i in resid_lb])
print(f"  load_bucket   | after removing hour : {p_lb_after_hour:.4f}")
print(f"  hour_warsaw   | after removing load_bucket : {p_hour_after_lb:.4f}")

# correlation check: are load and hour confounded?
print("\n=== confound check: mean pool_feasible by hour ===")
ph = defaultdict(list)
for r in rows:
    if r["pool_feasible"] is not None and r["decision_hour_warsaw"] is not None:
        ph[r["decision_hour_warsaw"]].append(r["pool_feasible"])
for h in sorted(ph):
    if len(ph[h])>=20:
        print(f"  h{h:02d} mean_pool_feasible={statistics.mean(ph[h]):.2f} n={len(ph[h])}")
