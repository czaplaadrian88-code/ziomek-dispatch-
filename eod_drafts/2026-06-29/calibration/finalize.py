#!/usr/bin/env python3
import json, statistics, math
from collections import Counter, defaultdict
OUT = "/tmp/claude-0/-root/f14f1e5b-ad36-45b3-941e-c61aa4e524a1/scratchpad"
rows = [json.loads(l) for l in open(f"{OUT}/decisions_outcomes_loadbucketed.jsonl")]
N=len(rows)

def pctl(xs,p):
    xs=sorted(xs); k=(len(xs)-1)*p; f=math.floor(k); c=math.ceil(k)
    return xs[int(k)] if f==c else xs[f]*(c-k)+xs[c]*(k-f)

# ---- outlier / tail quantification ----
eta=[r["eta_error_min"] for r in rows if r["eta_error_min"] is not None]
extreme=[e for e in eta if e<-120 or e>240]
big=[e for e in eta if abs(e)>60]
print(f"eta_error non-null={len(eta)}  |e|>60min={len(big)} ({100*len(big)/len(eta):.1f}%)  beyond[-120,240]={len(extreme)}")
print(f"  full p1={pctl(eta,.01):.1f} p99={pctl(eta,.99):.1f}  trimmed median={statistics.median([e for e in eta if -120<=e<=240]):.2f}")

# ---- KOORD effect ----
def val(r):
    e=r["eta_error_min"]; return e if (e is not None and -120<=e<=240) else None
prop=[val(r) for r in rows if not r["is_koord"]]; prop=[v for v in prop if v is not None]
koord=[val(r) for r in rows if r["is_koord"]]; koord=[v for v in koord if v is not None]
print(f"\nPROPOSE-only eta_error median={statistics.median(prop):.2f} n={len(prop)}")
print(f"KOORD-flagged eta_error median={statistics.median(koord):.2f} n={len(koord)}")

# ---- gps vs sla cross-check ----
pairs=[(r["eta_error_min"],r["eta_error_min_gps"]) for r in rows
       if r["eta_error_min"] is not None and r["eta_error_min_gps"] is not None]
diffs=[a-b for a,b in pairs]
print(f"\neta_error (best-actual) vs eta_error_gps (physical): n={len(pairs)} median_diff={statistics.median(diffs):.2f} "
      f"(button is ~this many min later than physical)")

# ---- WRITE cell counts table ----
def bag_bucket(b):
    if b is None: return "NA"
    return "0(solo)" if b==0 else "1" if b==1 else "2" if b==2 else "3+"
cells=Counter()
for r in rows: cells[(r["load_bucket"],r["tier"],bag_bucket(r["bag_size"]))]+=1
lines=["# CELL COUNTS  load_bucket x tier x bag_bucket  (n<30 = THIN)\n",
       f"{'load_bucket':10s} {'tier':6s} {'bag':8s} {'n':>5s}  flag"]
for (lb,ti,bg),n in sorted(cells.items(), key=lambda x:(-x[1])):
    lines.append(f"{str(lb):10s} {str(ti):6s} {str(bg):8s} {n:5d}  {'THIN' if n<30 else ''}")
# 2-way margins
lines.append("\n# 2-way: load_bucket x tier")
c2=Counter((r["load_bucket"],r["tier"]) for r in rows)
for (lb,ti),n in sorted(c2.items(), key=lambda x:(-x[1])):
    lines.append(f"{str(lb):10s} {str(ti):6s} {n:5d}  {'THIN' if n<30 else ''}")
lines.append("\n# 1-way load_bucket: "+str(dict(Counter(r['load_bucket'] for r in rows))))
lines.append("# 1-way tier: "+str(dict(Counter(r['tier'] for r in rows))))
lines.append("# 1-way bag: "+str(dict(Counter(bag_bucket(r['bag_size']) for r in rows))))
open(f"{OUT}/cell_counts.txt","w").write("\n".join(lines))
print(f"\nwrote cell_counts.txt ({len(cells)} 3-way cells, {sum(1 for v in cells.values() if v>=30)} with n>=30)")
