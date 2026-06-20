"""[E3] Trening dwóch wyspecjalizowanych modeli LGBM: solo (bundle=False) i bundle (bundle=True).

CAŁKOWICIE OFFLINE — zero ryzyka live. NIE nadpisuje produkcyjnego modelu.
Artefakty -> dispatch_v2/ml_data_prep/models_twomodel/{solo,bundle}/.

Pipeline (per reżim):
  1. wczytaj produkcyjne pary v2.0 (read-only), podziel na SOLO / BUNDLE (twomodel_common.solo_mask)
  2. pointwise-reframe (winner label=1, loser label=0, group=decision_id)
  3. kategoryczne (district/season/...) -> label-encode (jak produkcja),
     ALE `level` -> ONE-HOT (zamiast porządkowego label-encode = "tier_ord")
  4. dla modelu SOLO: usuń cechy bundlowe (bag_* ładunek worka) — w solo są stałe 0
  5. trenuj LGBMRanker (lambdarank), wczesny stop na val
  6. ewaluacja pairwise OSOBNO na podzbiorze (solo-only / bundle-only)

Dodatkowo: scoring STAREGO modelu produkcyjnego (v1.1) na podzbiorze SOLO,
żeby odtworzyć audytowy ~44.6% (gorzej niż losowo).

Uruchom (venv pipeline'u ML ma pyarrow+lightgbm+sklearn):
  /root/.openclaw/workspace/scripts/ml_data_prep/venv/bin/python3 \
      /root/.openclaw/workspace/scripts/dispatch_v2/ml_data_prep/train_two_models.py
"""
from __future__ import annotations

import argparse
import json
import logging
import pickle
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List, Tuple

import numpy as np
import pandas as pd
import lightgbm as lgb
from sklearn.preprocessing import LabelEncoder

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))           # ml_data_prep/ (twomodel_common)
PROD_ML = Path("/root/.openclaw/workspace/scripts/ml_data_prep")
sys.path.insert(0, str(PROD_ML))        # produkcyjne src.lgbm_training (read-only)

from twomodel_common import (  # noqa: E402
    BUNDLE_ONLY_BASE_FEATURES,
    RECON_ONLY_DECISION_FEATURES,
    TIER_ORD_COL,
    DATASET_DIR,
    apply_prod_feature_shaping,
    load_split,
    load_name_to_tier,
    solo_mask,
)
from src.lgbm_training import (  # noqa: E402  (reuse proven column defs)
    WINNER_COLS,
    LOSER_COLS,
    DECISION_LEVEL_COLS,
    CATEGORICAL_COLS,
)

OUT_DIR = HERE / "models_twomodel"

# Kategoryczne label-encode jak produkcja, ale BEZ `level` (idzie one-hot).
LABEL_ENCODE_COLS = [c for c in CATEGORICAL_COLS if c != TIER_ORD_COL]

# Kolumny pomocnicze nie będące cechami.
NON_FEATURE = {"label", "decision_id", "courier_name", "_key"}

HYPERPARAMS = dict(
    objective="lambdarank",
    metric="ndcg",
    n_estimators=500,
    learning_rate=0.05,
    num_leaves=31,
    max_depth=-1,
    min_child_samples=20,
    reg_alpha=0.1,
    reg_lambda=0.1,
    random_state=42,
    verbosity=-1,
)

log = logging.getLogger("twomodel")


# ── pointwise reframe ────────────────────────────────────────────────────────
def _side_to_pointwise(df: pd.DataFrame, side_cols: List[str], prefix: str) -> pd.DataFrame:
    rename = {c: c.replace(f"{prefix}_", "") for c in side_cols}
    out = df[side_cols + DECISION_LEVEL_COLS + ["decision_id"]].rename(columns=rename)
    out["label"] = 1 if prefix == "winner" else 0
    return out


def build_pointwise(df: pd.DataFrame, drop_bundle: bool) -> pd.DataFrame:
    """Pairwise (1 row/para) -> pointwise (2 rows/para). Opcjonalnie zdejmij cechy bundlowe.

    PROD-shaping (naprawa skew #2 delta_dist + #3 haversine) zastosowany ZAWSZE,
    żeby trening widział cechy ciągłe w definicjach produkcyjnych — żywa ścieżka
    serwowania (ml_inference.py) NIETKNIĘTA. delta_dist_km liczony PER decyzja
    (pool-mean) PRZED dedup/drop, ale dedup na (decision_id, courier_name, label)
    nie narusza grupy decyzji (pool-mean stały w obrębie decyzji).
    """
    win = _side_to_pointwise(df, WINNER_COLS, "winner")
    los = _side_to_pointwise(df, LOSER_COLS, "loser")
    pw = pd.concat([win, los], ignore_index=True)
    pw = pw.drop_duplicates(subset=["decision_id", "courier_name", "label"]).reset_index(drop=True)
    # PROD-shaping cech ciągłych (haversine ×1.42 + delta=kandydat−pool_mean per decyzja).
    pw = apply_prod_feature_shaping(pw)
    # Usuń cechy rekonstrukcyjne NIEdostępne live (parity z żywym serwowaniem).
    recon = [c for c in RECON_ONLY_DECISION_FEATURES if c in pw.columns]
    if recon:
        pw = pw.drop(columns=recon)
    if drop_bundle:
        cols = [c for c in BUNDLE_ONLY_BASE_FEATURES if c in pw.columns]
        pw = pw.drop(columns=cols)
    return pw


# ── kodowanie: label-encode (jak produkcja) + ONE-HOT dla `level` ────────────
def fit_label_encoders(train_pw: pd.DataFrame) -> Dict[str, LabelEncoder]:
    enc: Dict[str, LabelEncoder] = {}
    for col in LABEL_ENCODE_COLS:
        if col not in train_pw.columns:
            continue
        le = LabelEncoder()
        vals = train_pw[col].fillna("UNK").astype(str).unique().tolist()
        if "UNK" not in vals:
            vals.append("UNK")
        le.fit(vals)
        enc[col] = le
    return enc


def apply_label_encoders(df: pd.DataFrame, enc: Dict[str, LabelEncoder]) -> pd.DataFrame:
    df = df.copy()
    for col, le in enc.items():
        if col not in df.columns:
            continue
        vals = df[col].fillna("UNK").astype(str)
        known = set(le.classes_)
        vals = vals.where(vals.isin(known), "UNK")
        df[col] = le.transform(vals)
    return df


def fit_tier_categories(train_pw: pd.DataFrame) -> List[str]:
    """Kategorie `level` (tier_ord) zafiksowane na TRAIN — porządek deterministyczny.

    Te kategorie definiują kolumny one-hot serwowane potem identycznie (parity).
    """
    if TIER_ORD_COL not in train_pw.columns:
        return []
    cats = sorted(train_pw[TIER_ORD_COL].fillna("UNK").astype(str).unique().tolist())
    if "UNK" not in cats:
        cats.append("UNK")
    return cats


def apply_tier_onehot(df: pd.DataFrame, tier_categories: List[str]) -> pd.DataFrame:
    """Zamień kolumnę `level` na deterministyczny one-hot wg `tier_categories`.

    Nieznane wartości -> kolumna `level__UNK`. Kolumny i ich kolejność są stałe,
    niezależne od zawartości `df` (kluczowe dla parity train-vs-serve).
    """
    df = df.copy()
    if TIER_ORD_COL not in df.columns:
        return df
    raw = df[TIER_ORD_COL].fillna("UNK").astype(str)
    known = set(tier_categories)
    raw = raw.where(raw.isin(known), "UNK")
    for cat in tier_categories:
        df[f"{TIER_ORD_COL}__{cat}"] = (raw == cat).astype(np.int8)
    df = df.drop(columns=[TIER_ORD_COL])
    return df


# ── przygotowanie macierzy X/y/group ─────────────────────────────────────────
def to_arrays(pw: pd.DataFrame, feature_order: List[str]) -> Tuple[pd.DataFrame, np.ndarray, np.ndarray]:
    pw = pw.sort_values("decision_id").reset_index(drop=True)
    y = pw["label"].values
    group_sizes = pw.groupby("decision_id", sort=False).size().values
    X = pw.reindex(columns=feature_order)
    for col in X.columns:
        if X[col].dtype == "object":
            X[col] = pd.to_numeric(X[col], errors="coerce")
        if X[col].dtype == bool:
            X[col] = X[col].astype(np.int8)
    X = X.fillna(-1)
    return X, y, group_sizes


def feature_columns_of(pw: pd.DataFrame) -> List[str]:
    return [c for c in pw.columns if c not in NON_FEATURE]


# ── pairwise accuracy (winner score > loser score) ───────────────────────────
def pairwise_accuracy(
    df_pairs: pd.DataFrame,
    model: lgb.Booster,
    label_enc: Dict[str, LabelEncoder],
    tier_categories: List[str],
    feature_order: List[str],
    drop_bundle: bool,
) -> Tuple[float, int]:
    """Odtwarza pełną ścieżkę serwowania: pointwise -> encode -> one-hot -> predict.

    Zwraca (accuracy, n_par_ocenionych).
    """
    if df_pairs.empty:
        return 0.0, 0
    pw = build_pointwise(df_pairs, drop_bundle=drop_bundle)
    pw = apply_label_encoders(pw, label_enc)
    pw = apply_tier_onehot(pw, tier_categories)
    X, _, _ = to_arrays(pw, feature_order)
    pw = pw.sort_values("decision_id").reset_index(drop=True)
    pw["pred_score"] = model.predict(X)
    score_map = {
        (str(r["decision_id"]), str(r["courier_name"])): float(r["pred_score"])
        for _, r in pw.iterrows()
    }
    correct = total = 0
    for _, r in df_pairs.iterrows():
        did = str(r["decision_id"])
        ws = score_map.get((did, str(r["winner_courier_name"])))
        ls = score_map.get((did, str(r["loser_courier_name"])))
        if ws is None or ls is None:
            continue
        total += 1
        if ws > ls:
            correct += 1
    return (correct / total if total else 0.0), total


def ndcg_at_k(
    df_pairs: pd.DataFrame,
    model: lgb.Booster,
    label_enc: Dict[str, LabelEncoder],
    tier_categories: List[str],
    feature_order: List[str],
    drop_bundle: bool,
    k_values=(5, 10),
) -> Dict[int, float]:
    if df_pairs.empty:
        return {k: 0.0 for k in k_values}
    pw = build_pointwise(df_pairs, drop_bundle=drop_bundle)
    pw = apply_label_encoders(pw, label_enc)
    pw = apply_tier_onehot(pw, tier_categories)
    X, _, _ = to_arrays(pw, feature_order)
    pw = pw.sort_values("decision_id").reset_index(drop=True)
    pw["pred_score"] = model.predict(X)
    out: Dict[int, List[float]] = {k: [] for k in k_values}
    for _, group in pw.groupby("decision_id"):
        sg = group.sort_values("pred_score", ascending=False).reset_index(drop=True)
        labels = sg["label"].values
        for k in k_values:
            top_k = labels[:k]
            dcg = sum(rel / np.log2(i + 2) for i, rel in enumerate(top_k))
            out[k].append(dcg / 1.0)
    return {k: float(np.mean(v)) if v else 0.0 for k, v in out.items()}


# ── trening jednego reżimu ───────────────────────────────────────────────────
def train_regime(
    name: str,
    train_pairs: pd.DataFrame,
    val_pairs: pd.DataFrame,
    test_pairs: pd.DataFrame,
    drop_bundle: bool,
) -> Dict:
    log.info(f"=== Trening reżimu '{name}' (drop_bundle={drop_bundle}) ===")
    log.info(
        f"  pairs: train={len(train_pairs)} val={len(val_pairs)} test={len(test_pairs)} | "
        f"decisions: train={train_pairs['decision_id'].nunique()} "
        f"val={val_pairs['decision_id'].nunique()} test={test_pairs['decision_id'].nunique()}"
    )

    train_pw = build_pointwise(train_pairs, drop_bundle=drop_bundle)
    val_pw = build_pointwise(val_pairs, drop_bundle=drop_bundle)

    label_enc = fit_label_encoders(train_pw)
    tier_categories = fit_tier_categories(train_pw)
    log.info(f"  one-hot tier ({TIER_ORD_COL}) kategorie: {tier_categories}")

    train_pw = apply_label_encoders(train_pw, label_enc)
    train_pw = apply_tier_onehot(train_pw, tier_categories)
    val_pw = apply_label_encoders(val_pw, label_enc)
    val_pw = apply_tier_onehot(val_pw, tier_categories)

    feature_order = feature_columns_of(train_pw)
    log.info(f"  n_features={len(feature_order)}")
    if drop_bundle:
        log.info(f"  USUNIĘTE cechy bundlowe: {BUNDLE_ONLY_BASE_FEATURES}")

    X_train, y_train, g_train = to_arrays(train_pw, feature_order)
    X_val, y_val, g_val = to_arrays(val_pw, feature_order)

    t0 = time.time()
    model = lgb.LGBMRanker(**HYPERPARAMS)
    model.fit(
        X_train, y_train, group=g_train,
        eval_set=[(X_val, y_val)], eval_group=[g_val],
        eval_at=[5, 10],
        callbacks=[lgb.early_stopping(50, verbose=False)],
    )
    booster = model.booster_
    train_time = time.time() - t0
    log.info(f"  trening {train_time:.1f}s best_iter={model.best_iteration_}")

    pa_test, n_test = pairwise_accuracy(
        test_pairs, booster, label_enc, tier_categories, feature_order, drop_bundle
    )
    pa_val, _ = pairwise_accuracy(
        val_pairs, booster, label_enc, tier_categories, feature_order, drop_bundle
    )
    ndcg_test = ndcg_at_k(
        test_pairs, booster, label_enc, tier_categories, feature_order, drop_bundle
    )
    log.info(f"  TEST pairwise={pa_test:.4f} (n={n_test}) NDCG@5={ndcg_test[5]:.4f} NDCG@10={ndcg_test[10]:.4f}")
    log.info(f"  VAL  pairwise={pa_val:.4f}")

    # zapis artefaktów
    out = OUT_DIR / name
    out.mkdir(parents=True, exist_ok=True)
    booster.save_model(str(out / "lgbm_ranker.txt"))
    with open(out / "label_encoders.pkl", "wb") as f:
        pickle.dump(label_enc, f)
    json.dump(tier_categories, open(out / "tier_categories.json", "w"), indent=2)
    json.dump(feature_order, open(out / "feature_columns.json", "w"), indent=2)
    json.dump(
        {"drop_bundle": drop_bundle, "bundle_features_removed": (BUNDLE_ONLY_BASE_FEATURES if drop_bundle else [])},
        open(out / "config.json", "w"),
        indent=2,
    )

    return {
        "regime": name,
        "drop_bundle": drop_bundle,
        "n_features": len(feature_order),
        "bundle_features_removed": BUNDLE_ONLY_BASE_FEATURES if drop_bundle else [],
        "tier_onehot_categories": tier_categories,
        "n_pairs": {"train": len(train_pairs), "val": len(val_pairs), "test": len(test_pairs)},
        "n_decisions": {
            "train": int(train_pairs["decision_id"].nunique()),
            "val": int(val_pairs["decision_id"].nunique()),
            "test": int(test_pairs["decision_id"].nunique()),
        },
        "test_pairwise_accuracy": round(pa_test, 4),
        "val_pairwise_accuracy": round(pa_val, 4),
        "test_ndcg_at_5": round(ndcg_test[5], 4),
        "test_ndcg_at_10": round(ndcg_test[10], 4),
        "train_time_sec": round(train_time, 1),
        "best_iteration": int(model.best_iteration_) if model.best_iteration_ else None,
    }


# ── stary model produkcyjny na podzbiorze SOLO (odtworzenie ~44.6%) ──────────
def score_old_model_on_subset(df_pairs: pd.DataFrame, label: str) -> Dict:
    """Załaduj produkcyjny v1.1 i policz pairwise na podanym podzbiorze par.

    Używa produkcyjnej ścieżki encodingu (label-encode, BEZ one-hot) — to dokładnie
    to, co robi żywy `ml_inference.py`. Cel: odtworzyć audytowy ~44.6% na solo.
    """
    from src.lgbm_training import (
        build_pointwise_dataset,
        transform_categorical,
    )

    model = lgb.Booster(model_file=str(PROD_ML / "models" / "v1.1" / "lgbm_ranker.txt"))
    encoders = pickle.load(open(PROD_ML / "models" / "v1.1" / "encoders.pkl", "rb"))
    feat_cols = json.load(open(PROD_ML / "models" / "v1.1" / "feature_columns.json"))

    if df_pairs.empty:
        return {"subset": label, "n_pairs": 0, "pairwise_accuracy": None}

    pw = build_pointwise_dataset(df_pairs)
    pw = pw.drop_duplicates(subset=["decision_id", "courier_name", "label"]).reset_index(drop=True)
    pw = transform_categorical(pw, encoders)
    X = pw.reindex(columns=feat_cols)
    for col in X.columns:
        if X[col].dtype == "object":
            X[col] = pd.to_numeric(X[col], errors="coerce")
        if X[col].dtype == bool:
            X[col] = X[col].astype(np.int8)
    X = X.fillna(-1)
    pw["pred_score"] = model.predict(X)
    score_map = {
        (str(r["decision_id"]), str(r["courier_name"])): float(r["pred_score"])
        for _, r in pw.iterrows()
    }
    correct = total = 0
    for _, r in df_pairs.iterrows():
        did = str(r["decision_id"])
        ws = score_map.get((did, str(r["winner_courier_name"])))
        ls = score_map.get((did, str(r["loser_courier_name"])))
        if ws is None or ls is None:
            continue
        total += 1
        if ws > ls:
            correct += 1
    pa = correct / total if total else 0.0
    log.info(f"  STARY model v1.1 na podzbiorze '{label}': pairwise={pa:.4f} (n={total})")
    return {"subset": label, "n_pairs": total, "pairwise_accuracy": round(pa, 4)}


def main() -> int:
    parser = argparse.ArgumentParser(description="[E3] Trening dwóch modeli LGBM solo/bundle")
    parser.add_argument("--report", default=str(OUT_DIR / "twomodel_report.json"))
    args = parser.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(message)s",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    log.info(f"[E3] lightgbm {lgb.__version__}; dataset={DATASET_DIR}")

    train_pairs = load_split("train")
    val_pairs = load_split("val")
    test_pairs = load_split("test")

    # podział na reżimy
    splits = {}
    for nm, pairs in (("train", train_pairs), ("val", val_pairs), ("test", test_pairs)):
        sm = solo_mask(pairs)
        splits[nm] = {"solo": pairs[sm].reset_index(drop=True), "bundle": pairs[~sm].reset_index(drop=True)}
        log.info(
            f"{nm}: SOLO pairs={int(sm.sum())} ({100*sm.mean():.1f}%) | "
            f"BUNDLE pairs={int((~sm).sum())} ({100*(~sm).mean():.1f}%)"
        )

    # 1) STARY model na solo (i bundle dla porównania)
    old_solo = score_old_model_on_subset(splits["test"]["solo"], "test_solo")
    old_bundle = score_old_model_on_subset(splits["test"]["bundle"], "test_bundle")

    # 2) LGBM_solo (bez cech bundlowych) i LGBM_bundle (pełny zestaw)
    solo_res = train_regime(
        "solo",
        splits["train"]["solo"], splits["val"]["solo"], splits["test"]["solo"],
        drop_bundle=True,
    )
    bundle_res = train_regime(
        "bundle",
        splits["train"]["bundle"], splits["val"]["bundle"], splits["test"]["bundle"],
        drop_bundle=False,
    )

    gate_pass = solo_res["test_pairwise_accuracy"] > 0.80

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "lightgbm_version": lgb.__version__,
        "dataset": str(DATASET_DIR),
        "bundle_definition": "SOLO = winner pusty worek (bag_size==0 & drops==0 & pickup==0) == winner_level B",
        "subset_sizes": {
            nm: {
                "solo_pairs": len(splits[nm]["solo"]),
                "bundle_pairs": len(splits[nm]["bundle"]),
                "solo_decisions": int(splits[nm]["solo"]["decision_id"].nunique()) if len(splits[nm]["solo"]) else 0,
                "bundle_decisions": int(splits[nm]["bundle"]["decision_id"].nunique()) if len(splits[nm]["bundle"]) else 0,
            }
            for nm in ("train", "val", "test")
        },
        "old_model_v1_1": {"solo": old_solo, "bundle": old_bundle},
        "lgbm_solo": solo_res,
        "lgbm_bundle": bundle_res,
        "gate_solo_pairwise_gt_80pct": {
            "threshold": 0.80,
            "lgbm_solo_test_pairwise": solo_res["test_pairwise_accuracy"],
            "PASS": bool(gate_pass),
        },
    }
    json.dump(report, open(args.report, "w"), indent=2, default=str)

    log.info("=" * 70)
    log.info("PODSUMOWANIE [E3]")
    log.info(f"  STARY model v1.1  solo  pairwise = {old_solo['pairwise_accuracy']}  (oczekiwane ~0.446)")
    log.info(f"  STARY model v1.1  bundle pairwise = {old_bundle['pairwise_accuracy']}")
    log.info(f"  LGBM_solo          test pairwise = {solo_res['test_pairwise_accuracy']}  (cel >0.80)")
    log.info(f"  LGBM_bundle        test pairwise = {bundle_res['test_pairwise_accuracy']}")
    log.info(f"  BRAMKA solo>80%: {'PASS' if gate_pass else 'FAIL'}")
    log.info(f"  raport -> {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
