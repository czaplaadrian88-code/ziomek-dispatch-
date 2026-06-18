#!/usr/bin/env python3
"""ETA R3 — residual LightGBM na bazie OSRM, z cechami OBCIĄŻENIA. Trening eta_calibration_log.
Cel: poprawiona ETA = predicted + pred_residual, niżej MAE na held-out (zwł. w reżimie load),
bez wzrostu fałszywego optymizmu R6. Naive (restauracja×godzina) padł — to wersja z cechami load."""
import json, statistics as st
from collections import defaultdict
import numpy as np
import lightgbm as lgb
DS="/root/.openclaw/workspace/dispatch_state"
def num(x): return x if isinstance(x,(int,float)) else None
TRAIN_MAX="2026-06-13"
TIER_ORD={"gold":4,"std+":3,"std":2,"slow":1,"new":0}
# tier per courier
T=json.load(open(f"{DS}/courier_tiers.json"))
def tof(v): return ((v.get("bag") or {}).get("tier") or v.get("tier") or v.get("tier_label")) if isinstance(v,dict) else v
cid2tier={k:tof(v) for k,v in T.items() if k!="_meta"}
# pool_feasible per oid z backfilla
pool={}
for l in open(f"{DS}/backfill_decisions_outcomes_v1.jsonl"):
    try: d=json.loads(l); pool[str(d.get("order_id"))]=num(d.get("pool_feasible"))
    except: pass
# restaurant frequency (z train)
ETA=[json.loads(l) for l in open(f"{DS}/eta_calibration_log.jsonl")]
restcnt=defaultdict(int)
for r in ETA:
    if (r.get("logged_at") or "")[:10]<=TRAIN_MAX: restcnt[(r.get("restaurant") or "").lower()]+=1

def feats(r):
    bs=num(r.get("bag_size")); pdm=num(r.get("predicted_delivery_min")); hr=num(r.get("hour_warsaw"))
    cid=str(r.get("real_courier_id") or r.get("best_courier_id") or "")
    tier=cid2tier.get(cid)
    return [bs if bs is not None else -1,
            pdm if pdm is not None else -1,
            hr if hr is not None else -1,
            1 if r.get("is_weekend") else 0,
            1 if r.get("is_bundle") else 0,
            1 if (hr is not None and (11<=hr<14 or 17<=hr<20)) else 0,  # peak
            TIER_ORD.get(tier,2),
            restcnt.get((r.get("restaurant") or "").lower(),0),
            pool.get(str(r.get("oid") or r.get("order_id")),-1) if pool.get(str(r.get("oid") or r.get("order_id"))) is not None else -1]
FN=["bag_size","pred_delivery_min","hour","is_weekend","is_bundle","peak","tier_ord","rest_freq","pool_feasible"]
def valid(r):
    return num(r.get("predicted_delivery_min")) is not None and num(r.get("real_delivery_min")) is not None and not r.get("was_czasowka")
def resid(r): return num(r.get("real_delivery_min"))-num(r.get("predicted_delivery_min"))

tr=[r for r in ETA if valid(r) and (r.get("logged_at") or "")[:10]<=TRAIN_MAX]
te=[r for r in ETA if valid(r) and (r.get("logged_at") or "")[:10]>TRAIN_MAX]
Xtr=np.array([feats(r) for r in tr]); ytr=np.clip(np.array([resid(r) for r in tr]),-60,180)
Xte=np.array([feats(r) for r in te]); yte_resid=np.array([resid(r) for r in te])
base_te=np.array([num(r.get("predicted_delivery_min")) for r in te]); real_te=np.array([num(r.get("real_delivery_min")) for r in te])
print(f"train={len(tr)} test={len(te)} (held-out > {TRAIN_MAX})")

m=lgb.LGBMRegressor(n_estimators=400,learning_rate=0.05,num_leaves=31,min_child_samples=30,
                    subsample=0.8,colsample_bytree=0.8,reg_lambda=0.1,random_state=42,verbose=-1)
m.fit(Xtr,ytr)
pred_resid=m.predict(Xte)
corr_te=base_te+pred_resid
mae_base=np.mean(np.abs(real_te-base_te)); mae_corr=np.mean(np.abs(real_te-corr_te))
print(f"\n=== HELD-OUT MAE: bazowa(OSRM)={mae_base:.2f} -> po korekcie={mae_corr:.2f}  (poprawa {100*(mae_base-mae_corr)/mae_base:.1f}%) ===")
# wg reżimu obciążenia
print("wg wielkości worka (MAE base -> corr):")
bsz=np.array([num(r.get('bag_size')) or 0 for r in te])
for lo,hi,lab in [(1,2,"1-2"),(3,4,"3-4"),(5,8,"5+")]:
    msk=(bsz>=lo)&(bsz<=hi)
    if msk.sum()>10:
        print(f"  bag {lab:4s} n={msk.sum():4d}: {np.mean(np.abs(real_te-base_te)[msk]):5.1f} -> {np.mean(np.abs(real_te-corr_te)[msk]):5.1f}")
# fałszywy pesymizm/optymizm @35
fp_b=int(((base_te>35)&(real_te<=35)).sum()); fp_c=int(((corr_te>35)&(real_te<=35)).sum())
fo_b=int(((base_te<=35)&(real_te>35)).sum()); fo_c=int(((corr_te<=35)&(real_te>35)).sum())
print(f"\nfałszywy PESYMIZM (pred>35,real≤35): {fp_b} -> {fp_c} | fałszywy OPTYMIZM (pred≤35,real>35): {fo_b} -> {fo_c}")
imp=dict(zip(FN,m.feature_importances_))
print("ważność cech:", {k:int(v) for k,v in sorted(imp.items(),key=lambda x:-x[1])})
G = (mae_base-mae_corr)/mae_base>=0.10 and fp_c<=fp_b and (fo_c-fo_b)<=max(3,0.02*len(te))
print(f"\nBRAMKA: MAE↓≥10% & pesymizm nie rośnie & optymizm rośnie ≤2% → {'✅ PASS — gotowe do shadow-wiring' if G else '❌ FAIL — dopracować cechy/dane'}")
if G:
    import os; os.makedirs(f"{DS}/../scripts/ml_data_prep/models/eta_residual_v1",exist_ok=True)
    MDIR="/root/.openclaw/workspace/scripts/ml_data_prep/models/eta_residual_v1"
    m.booster_.save_model(f"{MDIR}/model.txt")
    json.dump(FN,open(f"{MDIR}/features.json","w"))
    # rest_freq = train-time częstość restauracji (NIE-żywa) — MUSI być persystowana,
    # inaczej inference nie odtworzy cechy `rest_freq` (train/serve skew). Shadow-wiring czyta ten plik.
    json.dump(dict(restcnt),open(f"{MDIR}/rest_freq.json","w"),ensure_ascii=False)
    print("model zapisany → ml_data_prep/models/eta_residual_v1/ (model.txt + features.json + rest_freq.json)")
