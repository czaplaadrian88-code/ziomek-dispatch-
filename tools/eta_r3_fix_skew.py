#!/usr/bin/env python3
"""ETA R3 — naprawa train/serve skew cechy `pool_feasible` (offline, 2026-06-20).

KONTEKST (z forward-val C5): KS D=0,867 na `pool_feasible`. Root cause udowodniony w kodzie:
- TRENING (`eod_drafts/2026-06-18/eta_residual_model.py:40`) budował pool_feasible z
  `backfill_decisions_outcomes_v1.jsonl` po oid; backfill istnieje DOPIERO od 2026-06-08,
  więc ~89,5% wierszy treningowych (data <06-08) dostało sentinel −1.
- SERWOWANIE (`eta_calibration_logger.py:296`) karmi model REALNYM `rec.pool_feasible_count`.
→ model uczył się głównie na −1, a w produkcji dostaje realne liczby (μ serve ≈ 4,5).

Dwie ścieżki naprawy, każda zapisuje KANDYDATA OBOK produkcji (NIE nadpisuje eta_residual_v1):
  (A) RETRAIN „distribution-matched": trening ograniczony do okna backfill-era
      (2026-06-08..TRAIN_MAX) gdzie realny pool_feasible ISTNIEJE i ma rozkład jak serve.
      9 cech, realny per-oid pool z backfilla. Artefakt: models/eta_residual_v2_retrain/.
      (Pełnego okna nie da się „naprawić" — dla wierszy <06-08 pool_feasible po prostu nie
       istnieje; jedyne uczciwe dopasowanie rozkładu to ograniczenie do ery backfilla.)
  (B) DROP cechy `pool_feasible": pełne 7945 wierszy, 8 cech. Artefakt: models/eta_residual_v2_drop/.

Reszta (hiperparametry LGBM, kodowanie cech, valid(), resid(), clip, TRAIN_MAX) IDENTYCZNA
z oryginałem — żeby porównanie baza/obecny/A/B było apples-to-apples.

Każdy artefakt: model.txt + features.json + rest_freq.json (+meta.json z opisem). Fail-soft.
NIE commituje, NIE flipuje, NIE dotyka produkcyjnego eta_residual_v1."""
import json
import os
from collections import defaultdict

import numpy as np

DS = "/root/.openclaw/workspace/dispatch_state"
MODELS = "/root/.openclaw/workspace/scripts/ml_data_prep/models"
TRAIN_MAX = "2026-06-13"
BACKFILL_START = "2026-06-08"      # pierwszy dzień z pool_feasible w backfillu
TIER_ORD = {"gold": 4, "std+": 3, "std": 2, "slow": 1, "new": 0}
FN_FULL = ["bag_size", "pred_delivery_min", "hour", "is_weekend",
           "is_bundle", "peak", "tier_ord", "rest_freq", "pool_feasible"]
FN_DROP = ["bag_size", "pred_delivery_min", "hour", "is_weekend",
           "is_bundle", "peak", "tier_ord", "rest_freq"]


def num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _tof(v):
    return ((v.get("bag") or {}).get("tier") or v.get("tier") or v.get("tier_label")) \
        if isinstance(v, dict) else v


def load_inputs():
    T = json.load(open(f"{DS}/courier_tiers.json"))
    cid2tier = {k: _tof(v) for k, v in T.items() if k != "_meta"}
    pool = {}
    for l in open(f"{DS}/backfill_decisions_outcomes_v1.jsonl"):
        try:
            d = json.loads(l)
            pool[str(d.get("order_id"))] = num(d.get("pool_feasible"))
        except Exception:
            pass
    ETA = [json.loads(l) for l in open(f"{DS}/eta_calibration_log.jsonl") if l.strip()]
    restcnt = defaultdict(int)
    for r in ETA:
        if (r.get("logged_at") or "")[:10] <= TRAIN_MAX:
            restcnt[(r.get("restaurant") or "").lower()] += 1
    return cid2tier, pool, ETA, dict(restcnt)


def feats(r, *, cid2tier, restcnt, pool, drop_pool):
    """LUSTRO feats() z treningu. drop_pool=True → pomija pool_feasible (8 cech)."""
    bs = num(r.get("bag_size"))
    pdm = num(r.get("predicted_delivery_min"))
    hr = num(r.get("hour_warsaw"))
    cid = str(r.get("real_courier_id") or r.get("best_courier_id") or "")
    tier = cid2tier.get(cid)
    out = [
        bs if bs is not None else -1,
        pdm if pdm is not None else -1,
        hr if hr is not None else -1,
        1 if r.get("is_weekend") else 0,
        1 if r.get("is_bundle") else 0,
        1 if (hr is not None and (11 <= hr < 14 or 17 <= hr < 20)) else 0,
        TIER_ORD.get(tier, 2),
        restcnt.get((r.get("restaurant") or "").lower(), 0),
    ]
    if not drop_pool:
        pv = pool.get(str(r.get("oid") or r.get("order_id")))
        out.append(pv if pv is not None else -1)
    return out


def valid(r):
    return num(r.get("predicted_delivery_min")) is not None \
        and num(r.get("real_delivery_min")) is not None and not r.get("was_czasowka")


def resid(r):
    return num(r.get("real_delivery_min")) - num(r.get("predicted_delivery_min"))


def _fit(Xtr, ytr):
    import lightgbm as lgb
    # n_jobs=1: silnik dzieli 2 vCPU z innymi sesjami; domyślne n_jobs=-1 → oversubskrypcja
    # wątków i patologiczny stall. Trening 8k×9 z n_jobs=1 = ~1s, deterministyczny.
    m = lgb.LGBMRegressor(n_estimators=400, learning_rate=0.05, num_leaves=31,
                          min_child_samples=30, subsample=0.8, colsample_bytree=0.8,
                          reg_lambda=0.1, random_state=42, verbose=-1, n_jobs=1)
    m.fit(Xtr, np.clip(ytr, -60, 180))
    return m


def _heldout_mae(m, te, fn_for_pred, *, cid2tier, restcnt, pool, drop_pool):
    Xte = np.array([feats(r, cid2tier=cid2tier, restcnt=restcnt, pool=pool, drop_pool=drop_pool)
                    for r in te], dtype=float)
    base = np.array([num(r.get("predicted_delivery_min")) for r in te])
    real = np.array([num(r.get("real_delivery_min")) for r in te])
    corr = base + m.predict(Xte)
    mae_b = float(np.mean(np.abs(real - base)))
    mae_c = float(np.mean(np.abs(real - corr)))
    return mae_b, mae_c, (100 * (mae_b - mae_c) / mae_b if mae_b > 0 else 0.0), len(te)


def _save(model, fn, restcnt, outdir, meta):
    os.makedirs(outdir, exist_ok=True)
    model.booster_.save_model(f"{outdir}/model.txt")
    json.dump(fn, open(f"{outdir}/features.json", "w"))
    json.dump(restcnt, open(f"{outdir}/rest_freq.json", "w"), ensure_ascii=False)
    json.dump(meta, open(f"{outdir}/meta.json", "w"), ensure_ascii=False, indent=2)


def build_both(verbose=True):
    cid2tier, pool, ETA, restcnt = load_inputs()
    tr_full = [r for r in ETA if valid(r) and (r.get("logged_at") or "")[:10] <= TRAIN_MAX]
    te = [r for r in ETA if valid(r) and (r.get("logged_at") or "")[:10] > TRAIN_MAX]
    # (A) okno backfill-era: pool_feasible realny i rozkładem jak serve
    tr_era = [r for r in tr_full if (r.get("logged_at") or "")[:10] >= BACKFILL_START]

    out = {}

    # --- (B) DROP pool_feasible, pełne dane ---
    XtrB = np.array([feats(r, cid2tier=cid2tier, restcnt=restcnt, pool=pool, drop_pool=True)
                     for r in tr_full], dtype=float)
    ytrB = np.array([resid(r) for r in tr_full])
    mB = _fit(XtrB, ytrB)
    maeB = _heldout_mae(mB, te, FN_DROP, cid2tier=cid2tier, restcnt=restcnt, pool=pool, drop_pool=True)
    dirB = f"{MODELS}/eta_residual_v2_drop"
    _save(mB, FN_DROP, restcnt, dirB, {
        "variant": "B_drop_pool_feasible", "n_train": len(tr_full), "features": FN_DROP,
        "note": "pełne dane, 8 cech bez pool_feasible (usuwa skew źródłowo)",
        "heldout_mae_base": maeB[0], "heldout_mae_corr": maeB[1], "heldout_impr_pct": maeB[2]})
    out["B"] = {"dir": dirB, "mae": maeB, "n_train": len(tr_full), "model": mB, "fn": FN_DROP}

    # --- (A) RETRAIN distribution-matched (okno backfill-era, 9 cech, realny pool) ---
    XtrA = np.array([feats(r, cid2tier=cid2tier, restcnt=restcnt, pool=pool, drop_pool=False)
                     for r in tr_era], dtype=float)
    ytrA = np.array([resid(r) for r in tr_era])
    mA = _fit(XtrA, ytrA)
    maeA = _heldout_mae(mA, te, FN_FULL, cid2tier=cid2tier, restcnt=restcnt, pool=pool, drop_pool=False)
    dirA = f"{MODELS}/eta_residual_v2_retrain"
    _save(mA, FN_FULL, restcnt, dirA, {
        "variant": "A_retrain_distribution_matched",
        "n_train": len(tr_era), "train_window": f"{BACKFILL_START}..{TRAIN_MAX}", "features": FN_FULL,
        "note": "trening ograniczony do ery backfilla — realny pool_feasible o rozkładzie jak serve",
        "heldout_mae_base": maeA[0], "heldout_mae_corr": maeA[1], "heldout_impr_pct": maeA[2]})
    out["A"] = {"dir": dirA, "mae": maeA, "n_train": len(tr_era), "model": mA, "fn": FN_FULL}

    if verbose:
        print(f"train_full={len(tr_full)}  train_era(A)={len(tr_era)}  heldout={len(te)}")
        for k in ("A", "B"):
            b, c, p, n = out[k]["mae"]
            print(f"  ({k}) n_train={out[k]['n_train']:5d}  held-out MAE base={b:.2f} corr={c:.2f} "
                  f"({p:+.1f}%)  → {out[k]['dir']}")
    return out


if __name__ == "__main__":
    build_both()
