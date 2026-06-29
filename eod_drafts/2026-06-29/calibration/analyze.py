#!/usr/bin/env python3
import json, statistics, math
from collections import Counter, defaultdict

OUT = "/tmp/claude-0/-root/f14f1e5b-ad36-45b3-941e-c61aa4e524a1/scratchpad"
rows = [json.loads(l) for l in open(f"{OUT}/decisions_outcomes_loadbucketed.jsonl")]
N = len(rows)
print(f"=== ROWS: {N} ===\n")

def cov(field):
    return sum(1 for r in rows if r.get(field) is not None)

print("=== FIELD COVERAGE (non-null) ===")
for f in ["decision_ts_utc","tier","pos_source","load_ewma","pool_feasible","load_bucket",
          "bag_size","is_bundle","predicted_travel_min","predicted_delivery_min",
          "picked_up_at_utc","delivered_at_utc","actual_delivery_min","actual_delivery_min_gps",
          "eta_error_min","eta_error_min_gps","r6_actual_min","r6_breach","pickup_slip_min",
          "dwell_actual_min","prep_bias_min","has_shadow","has_gps_truth","n_alternatives"]:
    c = cov(f); print(f"  {f:28s} {c:5d}  ({100*c/N:5.1f}%)")

print("\n=== load_source dist ===", dict(Counter(r["load_source"] for r in rows)))
print("=== load_bucket dist ===", dict(Counter(r["load_bucket"] for r in rows)))
print("=== bag_source dist ===", dict(Counter(r["bag_source"] for r in rows)))
print("=== delivered_source dist ===", dict(Counter(r["delivered_source"] for r in rows)))
print("=== actual_delivery_min_source ===", dict(Counter(r["actual_delivery_min_source"] for r in rows)))
print("=== tier dist ===", dict(Counter(r["tier"] for r in rows)))
print("=== is_koord ===", dict(Counter(r["is_koord"] for r in rows)))

def pctl(xs, p):
    if not xs: return None
    xs = sorted(xs); k = (len(xs)-1)*p; f = math.floor(k); c = math.ceil(k)
    if f == c: return xs[int(k)]
    return xs[f]*(c-k) + xs[c]*(k-f)

def stats(xs):
    xs = [x for x in xs if x is not None]
    if not xs: return None
    return dict(n=len(xs), median=round(statistics.median(xs),2),
               p20=round(pctl(xs,0.2),2), p80=round(pctl(xs,0.8),2),
               mean=round(statistics.mean(xs),2),
               std=round(statistics.pstdev(xs),2) if len(xs)>1 else 0.0)

# ---------------- SANITY: eta_error distribution overall + per load_bucket
print("\n=== SANITY: eta_error_min (actual - predicted; +=optimistic/under-predict) ===")
eta_all = [r["eta_error_min"] for r in rows if r["eta_error_min"] is not None]
print("  OVERALL:", stats(eta_all))
# trim extreme outliers for robust view
eta_trim = [e for e in eta_all if -120 <= e <= 240]
print(f"  OVERALL (trim -120..240, dropped {len(eta_all)-len(eta_trim)}):", stats(eta_trim))

print("\n=== eta_error_min by load_bucket (does it GROW luzno->niedobor?) ===")
order = ["luzno","srednio","ciasno","niedobor"]
by_bucket = defaultdict(list)
for r in rows:
    if r["eta_error_min"] is not None and -120 <= r["eta_error_min"] <= 240:
        by_bucket[r["load_bucket"]].append(r["eta_error_min"])
for b in order:
    print(f"  {b:9s}", stats(by_bucket.get(b,[])))

# ewma-only subset (cleaner load signal, includes ciasno properly)
print("\n=== eta_error_min by load_bucket -- EWMA-derived rows only ===")
by_bucket_ewma = defaultdict(list)
for r in rows:
    if r["load_source"]=="ewma" and r["eta_error_min"] is not None and -120 <= r["eta_error_min"] <= 240:
        by_bucket_ewma[r["load_bucket"]].append(r["eta_error_min"])
for b in order:
    print(f"  {b:9s}", stats(by_bucket_ewma.get(b,[])))

# ---------------- KEY VALIDATION: load_bucket vs hour-of-day variance explained
print("\n=== KEY VALIDATION: variance-explained eta_error  load_bucket vs decision_hour_warsaw ===")
def variance_explained(rows, groupfn, valfn):
    groups = defaultdict(list)
    allv = []
    for r in rows:
        v = valfn(r)
        g = groupfn(r)
        if v is None or g is None: continue
        groups[g].append(v); allv.append(v)
    if len(allv) < 2: return None
    grand = statistics.mean(allv)
    sst = sum((v-grand)**2 for v in allv)
    ssb = sum(len(vs)*(statistics.mean(vs)-grand)**2 for vs in groups.values() if vs)
    eta2 = ssb/sst if sst>0 else 0.0
    # median spread = max group median - min group median (groups with n>=20)
    meds = {g: statistics.median(vs) for g,vs in groups.items() if len(vs)>=20}
    spread = (max(meds.values())-min(meds.values())) if meds else None
    return dict(eta2=round(eta2,4), n=len(allv), n_groups=len(groups),
                median_spread=round(spread,2) if spread is not None else None,
                n_groups_ge20=len(meds))

valfn = lambda r: r["eta_error_min"] if (r["eta_error_min"] is not None and -120<=r["eta_error_min"]<=240) else None
ve_load = variance_explained(rows, lambda r: r["load_bucket"], valfn)
ve_hour = variance_explained(rows, lambda r: r["decision_hour_warsaw"], valfn)
ve_pool = variance_explained(rows, lambda r: r["pool_feasible"], valfn)
print("  by load_bucket   :", ve_load)
print("  by hour_warsaw   :", ve_hour)
print("  by pool_feasible :", ve_pool)

# EWMA-only (cleanest load axis) vs hour on same subset
ewma_rows = [r for r in rows if r["load_source"]=="ewma"]
print("\n  -- on EWMA-derived subset (cleanest load signal) --")
print("  by load_bucket :", variance_explained(ewma_rows, lambda r: r["load_bucket"], valfn))
print("  by ewma(round) :", variance_explained(ewma_rows, lambda r: round(r["load_ewma"]) if r["load_ewma"] is not None else None, valfn))
print("  by hour_warsaw :", variance_explained(ewma_rows, lambda r: r["decision_hour_warsaw"], valfn))

# hour-of-day eta_error medians to show clock pattern
print("\n=== eta_error_min median by decision_hour_warsaw (n>=20) ===")
hour_groups = defaultdict(list)
for r in rows:
    v = valfn(r)
    if v is not None and r["decision_hour_warsaw"] is not None:
        hour_groups[r["decision_hour_warsaw"]].append(v)
for h in sorted(hour_groups):
    vs = hour_groups[h]
    if len(vs)>=20:
        print(f"  h{h:02d}  n={len(vs):4d}  median={statistics.median(vs):6.1f}  p80={pctl(vs,0.8):6.1f}")

# ---------------- CELL COUNTS load_bucket x tier x bag_size
print("\n=== CELL COUNTS  (load_bucket x tier x bag_bucket) ===")
def bag_bucket(b):
    if b is None: return "NA"
    if b == 0: return "0(solo)"
    if b == 1: return "1"
    if b == 2: return "2"
    return "3+"
cells = Counter()
for r in rows:
    cells[(r["load_bucket"], r["tier"], bag_bucket(r["bag_size"]))] += 1
print(f"{'load':9s} {'tier':6s} {'bag':8s} {'n':>5s}  flag")
for (lb,ti,bg),n in sorted(cells.items(), key=lambda x:(-x[1])):
    flag = "THIN(<30)" if n<30 else ""
    print(f"{str(lb):9s} {str(ti):6s} {str(bg):8s} {n:5d}  {flag}")
