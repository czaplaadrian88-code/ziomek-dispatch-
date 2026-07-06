"""Analiza zależności poślizgu obietnicy (eta_error_min) od zmiennych — Adrian 06.07.

Populacja: matched_courier (jechał proponowany kurier), bez czasówek, |err|<=120.
Zmienne: bag_size, tier kuriera, kurier, godzina (korki), weekend, długość
predykcji, wiek predykcji, R6 worka. Walidacja czasowa (train=starsze 70% dni).
"""
import json
import sys
from collections import defaultdict
from datetime import datetime
from statistics import median

import numpy as np

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

ETA_CAL = "/root/.openclaw/workspace/dispatch_state/eta_calibration_log.jsonl"
TIERS = "/root/.openclaw/workspace/dispatch_state/courier_tiers.json"

tiers_raw = json.load(open(TIERS))
tier_of = {}
for cid, v in tiers_raw.items():
    if cid == "_meta" or not isinstance(v, dict):
        continue
    tier_of[str(cid)] = (v.get("bag") or {}).get("tier") or "unknown"

rows = []
for line in open(ETA_CAL):
    try:
        d = json.loads(line)
    except Exception:
        continue
    err = d.get("eta_error_min")
    if not isinstance(err, (int, float)) or abs(err) > 120:
        continue
    if not d.get("matched_courier") or d.get("was_czasowka"):
        continue
    bag = d.get("bag_size")
    hour = d.get("hour_warsaw")
    if not isinstance(bag, int) or bag < 1 or not isinstance(hour, int):
        continue
    la = d.get("logged_at") or ""
    try:
        day = datetime.fromisoformat(la).date()
    except Exception:
        continue
    cid = str(d.get("real_courier_id") or "")
    rows.append({
        "err": float(err),
        "bag": min(bag, 4),
        "hour": hour,
        "weekend": 1 if d.get("is_weekend") else 0,
        "weekday": d.get("weekday") if isinstance(d.get("weekday"), int) else -1,
        "tier": tier_of.get(cid, "unknown"),
        "cid": cid,
        "pred_min": float(d.get("predicted_delivery_min") or 0),
        "pred_age": float(d.get("prediction_age_min") or 0),
        "r6max": float(d.get("r6_max_bag_time_min") or 0),
        "day": day,
    })

print(f"n = {len(rows)} (matched, bez czasówek, |err|<=120)")
days = sorted({r["day"] for r in rows})
print(f"okres: {days[0]} .. {days[-1]} ({len(days)} dni)")

def cell_stats(keyf, label, min_n=40):
    groups = defaultdict(list)
    for r in rows:
        groups[keyf(r)].append(r["err"])
    print(f"\n--- {label} ---")
    for k in sorted(groups, key=str):
        v = sorted(groups[k])
        if len(v) < min_n:
            continue
        q = lambda p: v[int(p * len(v))]
        print(f"  {str(k):<14} n={len(v):>5}  med={median(v):>6.1f}  p25={q(.25):>6.1f}  p75={q(.75):>6.1f}")

def hourb(h):
    if 7 <= h < 11: return "07-11 rano"
    if 11 <= h < 14: return "11-14 lunch"
    if 14 <= h < 17: return "14-17 popol"
    if 17 <= h < 21: return "17-21 kolacja"
    return "21+ noc"

cell_stats(lambda r: r["bag"], "OBJĘTOŚĆ WORKA (bag_size, 4=4+)")
cell_stats(lambda r: r["tier"], "TIER KURIERA")
cell_stats(lambda r: hourb(r["hour"]), "PORA DNIA (korki)")
cell_stats(lambda r: ("weekend" if r["weekend"] else "tydzień"), "WEEKEND")
cell_stats(lambda r: (r["bag"], hourb(r["hour"])), "WOREK × PORA DNIA", min_n=60)

# ---- model LGBM (mediana, obj=quantile a=0.5) + walidacja czasowa ----
import lightgbm as lgb

tier_levels = sorted({r["tier"] for r in rows})
tier_idx = {t: i for i, t in enumerate(tier_levels)}
X = np.array([[r["bag"], r["hour"], r["weekday"], r["weekend"],
               tier_idx[r["tier"]], r["pred_min"], r["pred_age"], r["r6max"]]
              for r in rows], dtype=float)
y = np.array([r["err"] for r in rows])
dts = np.array([days.index(r["day"]) for r in rows])
split_day = int(len(days) * 0.7)
tr, te = dts < split_day, dts >= split_day
feat_names = ["bag", "hour", "weekday", "weekend", "tier", "pred_min", "pred_age", "r6max"]

m = lgb.LGBMRegressor(objective="quantile", alpha=0.5, n_estimators=300,
                      learning_rate=0.05, num_leaves=15, min_child_samples=60,
                      random_state=42, verbose=-1)
m.fit(X[tr], y[tr], categorical_feature=[2, 4])
pred = m.predict(X[te])

const = np.median(y[tr])
mae = lambda p: float(np.mean(np.abs(y[te] - p)))
medae = lambda p: float(np.median(np.abs(y[te] - p)))
print(f"\n--- WALIDACJA CZASOWA (test = ostatnie 30 proc. dni, n_test={te.sum()}) ---")
print(f"  stała mediana ({const:.1f}):  MAE={mae(const):.2f}  medAE={medae(const):.2f}")

# tabela v2-podobna (worek solo/multi x pora) z TRAIN jako baseline tabelaryczny
tab = defaultdict(list)
for i in np.where(tr)[0]:
    r = rows[i]
    tab[(r["bag"], hourb(r["hour"]))].append(r["err"])
tabm = {k: median(v) for k, v in tab.items() if len(v) >= 30}
tpred = np.array([tabm.get((rows[i]["bag"], hourb(rows[i]["hour"])), const)
                  for i in np.where(te)[0]])
print(f"  tabela worek×pora:        MAE={mae(tpred):.2f}  medAE={medae(tpred):.2f}")
print(f"  LGBM (8 zmiennych):       MAE={mae(pred):.2f}  medAE={medae(pred):.2f}")

imp = sorted(zip(feat_names, m.feature_importances_), key=lambda x: -x[1])
print("\n--- WAŻNOŚĆ ZMIENNYCH (LGBM gain-split) ---")
for n_, v_ in imp:
    print(f"  {n_:<10} {v_}")

# per-kurier: mediana resztek po modelu (czy kurierzy różnią się PONAD model)
res = defaultdict(list)
pall = m.predict(X)
for i, r in enumerate(rows):
    if dts[i] >= split_day:
        res[r["cid"]].append(y[i] - pall[i])
print("\n--- KURIERZY: mediana reszty PONAD model (test, n>=25) ---")
outliers = [(cid, median(v), len(v)) for cid, v in res.items() if len(v) >= 25]
for cid, mr, n_ in sorted(outliers, key=lambda x: -abs(x[1]))[:8]:
    print(f"  cid {cid:<5} tier={tier_of.get(cid,'?'):<8} n={n_:>4}  med reszta={mr:+.1f} min")
