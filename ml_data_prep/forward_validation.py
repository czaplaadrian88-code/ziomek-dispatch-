"""[ML-PROD] Forward-validation OUT-OF-TIME dla dwumodelu solo/bundle.

CAŁKOWICIE OFFLINE. NIE commituje, NIE flipuje, NIE dotyka produkcyjnego modelu.

Różnica vs E3 (train_two_models.py):
  E3 użył istniejącego splitu v2.0 (train ≤02-21, val 02-21→03-21, test 03-21→04-20).
  Ten split JEST już temporalny — ale audyt prosi o STRICT forward-val: trenuj na
  starszych dniach, ewaluuj na ŚWIEŻYCH dniach trzymanych całkowicie z dala od
  treningu, raport pairwise PER DZIEŃ / PER OKNO. Cel: udowodnić że solo>0.80
  trzyma się poza czasem treningu, nie tylko na losowym held-out.

Metoda:
  1. Zbierz wszystkie pary z v2.0, sortuj po `date`.
  2. Cutoff = ostatnie FORWARD_DAYS dni jako okno forward (out-of-time), reszta = train.
     (decyzje z jednego dnia NIE są dzielone — split po dacie.)
  3. Trenuj LGBM_solo (bez cech bundlowych) i LGBM_bundle na train.
  4. Ewaluuj pairwise OSOBNO per reżim, dodatkowo rozbij okno forward na pod-okna
     (per-dzień i per-tydzień), żeby zobaczyć stabilność w czasie.
  5. Dla odniesienia: pairwise per-dzień również dla STAREGO modelu v1.1 (solo).

Uruchom:
  /root/.openclaw/workspace/scripts/ml_data_prep/venv/bin/python3 \
      /root/.openclaw/workspace/scripts/dispatch_v2/ml_data_prep/forward_validation.py
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import pickle
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

# Serwer współdzielony (4 vCPU, load avg często ~9 od żywego dispatchera + innych
# sesji). Domyślne wielowątkowanie LightGBM/OMP thrashuje → trening wisi. Ograniczamy
# do 2 wątków, żeby kooperować z obciążeniem (offline, nie dotyka prod modelu).
os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "2")

import numpy as np
import pandas as pd
import lightgbm as lgb

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
PROD_ML = Path("/root/.openclaw/workspace/scripts/ml_data_prep")
sys.path.insert(0, str(PROD_ML))

import train_two_models as tm  # noqa: E402  (reuse cały aparat: pointwise/onehot/encode/pa)
from twomodel_common import (  # noqa: E402
    DATASET_DIR,
    solo_mask,
    load_split,
)
from src.lgbm_training import build_pointwise_dataset, transform_categorical  # noqa: E402


def _fast_pairwise_from_scores(df_pairs: pd.DataFrame, score_map: Dict) -> Tuple[float, int]:
    """Wektorowo: pairwise = frac par gdzie score(winner) > score(loser).

    score_map: {(decision_id_str, courier_name_str): score}. O(n) bez .iterrows().
    """
    if df_pairs.empty:
        return float("nan"), 0
    did = df_pairs["decision_id"].astype(str).to_numpy()
    wn = df_pairs["winner_courier_name"].astype(str).to_numpy()
    ln = df_pairs["loser_courier_name"].astype(str).to_numpy()
    ws = np.array([score_map.get((did[i], wn[i]), np.nan) for i in range(len(did))])
    ls = np.array([score_map.get((did[i], ln[i]), np.nan) for i in range(len(did))])
    valid = ~(np.isnan(ws) | np.isnan(ls))
    n = int(valid.sum())
    if n == 0:
        return float("nan"), 0
    correct = int((ws[valid] > ls[valid]).sum())
    return correct / n, n


def _score_map_new(df_pairs, booster, label_enc, tier_categories, feature_order, drop_bundle) -> Dict:
    """Zbuduj score_map dla NOWEGO modelu (pełna ścieżka serwowania, raz)."""
    if df_pairs.empty:
        return {}
    pw = tm.build_pointwise(df_pairs, drop_bundle=drop_bundle)
    pw = tm.apply_tier_onehot(tm.apply_label_encoders(pw, label_enc), tier_categories)
    X, _, _ = tm.to_arrays(pw, feature_order)
    pw = pw.sort_values("decision_id").reset_index(drop=True)
    preds = booster.predict(X)
    did = pw["decision_id"].astype(str).to_numpy()
    cn = pw["courier_name"].astype(str).to_numpy()
    return {(did[i], cn[i]): float(preds[i]) for i in range(len(did))}

OUT_DIR = HERE / "models_twomodel" / "forward"
FORWARD_DAYS = 14  # ostatnie 14 dni = okno out-of-time (test forward)

log = logging.getLogger("forward_val")


def _load_all_pairs() -> pd.DataFrame:
    frames = []
    for sp in ("train", "val", "test"):
        df = load_split(sp)
        frames.append(df)
    allp = pd.concat(frames, ignore_index=True)
    allp["_date"] = pd.to_datetime(allp["date"], errors="coerce").dt.normalize()
    return allp


def _train_regime_on(
    train_pairs: pd.DataFrame, drop_bundle: bool
) -> Tuple[lgb.Booster, dict, list, list]:
    """Trenuj jeden reżim. Zwraca (booster, label_enc, tier_categories, feature_order).

    Mały held-out time-based val (ostatnie ~10% dni train) na early-stopping.
    """
    # time-based val: ostatnie 10% dni okna treningowego
    days = sorted(train_pairs["_date"].dropna().unique())
    n_val_days = max(1, int(len(days) * 0.10))
    val_days = set(days[-n_val_days:])
    tr = train_pairs[~train_pairs["_date"].isin(val_days)]
    va = train_pairs[train_pairs["_date"].isin(val_days)]

    train_pw = tm.build_pointwise(tr, drop_bundle=drop_bundle)
    val_pw = tm.build_pointwise(va, drop_bundle=drop_bundle)

    label_enc = tm.fit_label_encoders(train_pw)
    tier_categories = tm.fit_tier_categories(train_pw)
    train_pw = tm.apply_tier_onehot(tm.apply_label_encoders(train_pw, label_enc), tier_categories)
    val_pw = tm.apply_tier_onehot(tm.apply_label_encoders(val_pw, label_enc), tier_categories)

    feature_order = tm.feature_columns_of(train_pw)
    X_tr, y_tr, g_tr = tm.to_arrays(train_pw, feature_order)
    X_va, y_va, g_va = tm.to_arrays(val_pw, feature_order)

    hp = dict(tm.HYPERPARAMS)
    hp["num_threads"] = 2  # serwer współdzielony pod obciążeniem
    model = lgb.LGBMRanker(**hp)
    model.fit(
        X_tr, y_tr, group=g_tr,
        eval_set=[(X_va, y_va)], eval_group=[g_va],
        eval_at=[5, 10],
        callbacks=[lgb.early_stopping(50, verbose=False)],
    )
    return model.booster_, label_enc, tier_categories, feature_order


def _score_map_old(df_pairs: pd.DataFrame) -> Dict:
    """Score_map STAREGO modelu v1.1 (label-encode, bez one-hot) — model ładowany RAZ przez caller."""
    if df_pairs.empty:
        return {}
    pw = build_pointwise_dataset(df_pairs).drop_duplicates(
        subset=["decision_id", "courier_name", "label"]
    ).reset_index(drop=True)
    pw = transform_categorical(pw, _OLD["encoders"])
    X = pw.reindex(columns=_OLD["feat_cols"])
    for c in X.columns:
        if X[c].dtype == "object":
            X[c] = pd.to_numeric(X[c], errors="coerce")
        if X[c].dtype == bool:
            X[c] = X[c].astype(np.int8)
    X = X.fillna(-1)
    preds = _OLD["model"].predict(X)
    did = pw["decision_id"].astype(str).to_numpy()
    cn = pw["courier_name"].astype(str).to_numpy()
    return {(did[i], cn[i]): float(preds[i]) for i in range(len(did))}


# stary model v1.1 ładowany RAZ (singleton)
_OLD: Dict = {}


def _load_old_once() -> None:
    _OLD["model"] = lgb.Booster(model_file=str(PROD_ML / "models" / "v1.1" / "lgbm_ranker.txt"))
    _OLD["encoders"] = pickle.load(open(PROD_ML / "models" / "v1.1" / "encoders.pkl", "rb"))
    _OLD["feat_cols"] = json.load(open(PROD_ML / "models" / "v1.1" / "feature_columns.json"))


def main() -> int:
    parser = argparse.ArgumentParser(description="[ML-PROD] forward-validation out-of-time")
    parser.add_argument("--forward-days", type=int, default=FORWARD_DAYS)
    parser.add_argument("--report", default=str(OUT_DIR / "forward_report.json"))
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )

    allp = _load_all_pairs()
    days = sorted(allp["_date"].dropna().unique())
    cutoff = days[-args.forward_days]
    log.info(f"dni total={len(days)} | zakres {days[0].date()} → {days[-1].date()}")
    log.info(f"forward okno = ostatnie {args.forward_days} dni: cutoff = {pd.Timestamp(cutoff).date()}")

    train_pairs = allp[allp["_date"] < cutoff].reset_index(drop=True)
    fwd_pairs = allp[allp["_date"] >= cutoff].reset_index(drop=True)
    log.info(f"TRAIN pairs={len(train_pairs)} (dni {train_pairs['_date'].nunique()}) | "
             f"FORWARD pairs={len(fwd_pairs)} (dni {fwd_pairs['_date'].nunique()})")

    # podział forward na reżimy
    fwd_solo = fwd_pairs[solo_mask(fwd_pairs)].reset_index(drop=True)
    fwd_bundle = fwd_pairs[~solo_mask(fwd_pairs)].reset_index(drop=True)
    log.info(f"FORWARD solo pairs={len(fwd_solo)} | bundle pairs={len(fwd_bundle)}")

    # trening obu reżimów WYŁĄCZNIE na train (< cutoff)
    train_solo = train_pairs[solo_mask(train_pairs)].reset_index(drop=True)
    train_bundle = train_pairs[~solo_mask(train_pairs)].reset_index(drop=True)
    log.info(f"TRAIN solo pairs={len(train_solo)} | bundle pairs={len(train_bundle)}")

    t0 = time.time()
    solo_booster, solo_enc, solo_cats, solo_feats = _train_regime_on(train_solo, drop_bundle=True)
    bundle_booster, bundle_enc, bundle_cats, bundle_feats = _train_regime_on(train_bundle, drop_bundle=False)
    _load_old_once()
    log.info(f"trening obu modeli {time.time()-t0:.1f}s; stary v1.1 wczytany")

    # score_mapy zbudowane RAZ na całym oknie forward (potem szybkie cięcie per-dzień/tydzień)
    t1 = time.time()
    sm_solo_new = _score_map_new(fwd_solo, solo_booster, solo_enc, solo_cats, solo_feats, drop_bundle=True)
    sm_bundle_new = _score_map_new(fwd_bundle, bundle_booster, bundle_enc, bundle_cats, bundle_feats, drop_bundle=False)
    sm_solo_old = _score_map_old(fwd_solo)
    log.info(f"score_mapy zbudowane {time.time()-t1:.1f}s")

    # agregaty forward
    solo_pa, solo_n = _fast_pairwise_from_scores(fwd_solo, sm_solo_new)
    bundle_pa, bundle_n = _fast_pairwise_from_scores(fwd_bundle, sm_bundle_new)
    old_solo_pa, old_solo_n = _fast_pairwise_from_scores(fwd_solo, sm_solo_old)
    log.info(f"FORWARD AGG: solo new={solo_pa:.4f} (n={solo_n}) | solo OLD v1.1={old_solo_pa:.4f} (n={old_solo_n}) | "
             f"bundle new={bundle_pa:.4f} (n={bundle_n})")

    # per-dzień (cięcie par + wspólny score_map)
    per_day = []
    for d in sorted(fwd_pairs["_date"].dropna().unique()):
        day_solo = fwd_solo[fwd_solo["_date"] == d]
        day_bundle = fwd_bundle[fwd_bundle["_date"] == d]
        s_pa, s_n = _fast_pairwise_from_scores(day_solo, sm_solo_new)
        b_pa, b_n = _fast_pairwise_from_scores(day_bundle, sm_bundle_new)
        o_pa, o_n = _fast_pairwise_from_scores(day_solo, sm_solo_old)
        per_day.append({
            "date": str(pd.Timestamp(d).date()),
            "solo_new_pa": round(s_pa, 4) if s_n else None, "solo_n": s_n,
            "solo_old_pa": round(o_pa, 4) if o_n else None,
            "bundle_new_pa": round(b_pa, 4) if b_n else None, "bundle_n": b_n,
        })
        log.info(f"  {pd.Timestamp(d).date()}: solo new={s_pa:.3f} (n={s_n}) old={o_pa:.3f} | bundle={b_pa:.3f} (n={b_n})")

    # per-tydzień (ISO week)
    fwd_solo = fwd_solo.copy()
    fwd_solo["_week"] = fwd_solo["_date"].dt.isocalendar().week
    fwd_bundle = fwd_bundle.copy()
    fwd_bundle["_week"] = fwd_bundle["_date"].dt.isocalendar().week
    per_week = []
    for w in sorted(set(fwd_solo["_week"]).union(set(fwd_bundle["_week"]))):
        ws = fwd_solo[fwd_solo["_week"] == w]
        wb = fwd_bundle[fwd_bundle["_week"] == w]
        s_pa, s_n = _fast_pairwise_from_scores(ws, sm_solo_new)
        b_pa, b_n = _fast_pairwise_from_scores(wb, sm_bundle_new)
        per_week.append({
            "iso_week": int(w),
            "solo_new_pa": round(s_pa, 4) if s_n else None, "solo_n": s_n,
            "bundle_new_pa": round(b_pa, 4) if b_n else None, "bundle_n": b_n,
        })

    # bramka: solo forward > 0.80 (agregat ORAZ każdy dzień z n>=50)
    solo_days_meaningful = [d for d in per_day if d["solo_n"] >= 50 and d["solo_new_pa"] is not None]
    min_day_solo = min((d["solo_new_pa"] for d in solo_days_meaningful), default=None)
    gate_agg = solo_pa > 0.80
    gate_all_days = (min_day_solo is not None and min_day_solo > 0.80)

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "method": "strict out-of-time: train < cutoff, forward = ostatnie N dni held-out, split po dacie",
        "forward_days": args.forward_days,
        "cutoff_date": str(pd.Timestamp(cutoff).date()),
        "train": {
            "date_min": str(train_pairs["_date"].min().date()),
            "date_max": str(train_pairs["_date"].max().date()),
            "n_pairs": len(train_pairs),
            "n_days": int(train_pairs["_date"].nunique()),
            "solo_pairs": len(train_solo), "bundle_pairs": len(train_bundle),
        },
        "forward_window": {
            "date_min": str(fwd_pairs["_date"].min().date()),
            "date_max": str(fwd_pairs["_date"].max().date()),
            "n_pairs": len(fwd_pairs),
            "n_days": int(fwd_pairs["_date"].nunique()),
            "solo_pairs": len(fwd_solo), "bundle_pairs": len(fwd_bundle),
        },
        "forward_aggregate": {
            "lgbm_solo_pairwise": round(solo_pa, 4), "solo_n": solo_n,
            "old_model_v1_1_solo_pairwise": round(old_solo_pa, 4), "old_solo_n": old_solo_n,
            "lgbm_bundle_pairwise": round(bundle_pa, 4), "bundle_n": bundle_n,
        },
        "per_day": per_day,
        "per_week": per_week,
        "gate": {
            "solo_forward_aggregate_gt_80": bool(gate_agg),
            "solo_min_day_pa_n50": min_day_solo,
            "solo_all_meaningful_days_gt_80": bool(gate_all_days),
        },
    }
    json.dump(report, open(args.report, "w"), indent=2, default=str)
    log.info("=" * 70)
    log.info(f"FORWARD solo agg = {solo_pa:.4f} (cel >0.80) | min-dzień(n≥50) = {min_day_solo}")
    log.info(f"BRAMKA forward solo>0.80: agg={'PASS' if gate_agg else 'FAIL'} "
             f"all-days={'PASS' if gate_all_days else 'FAIL'}")
    log.info(f"raport -> {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
