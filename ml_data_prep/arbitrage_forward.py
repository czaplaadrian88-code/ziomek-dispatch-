"""[ARB] Cross-regime score arbitrage — czy dwumodel (solo+bundle) bije baseline'y
na PRAWDZIWYCH forward held-out decyzjach dispatchu.

CAŁKOWICIE OFFLINE / READ-ONLY. ZERO wpływu na produkcję: nie restartuje usług,
nie flipuje flag, nie dotyka żywego modelu prod, czyta dataset read-only, pisze
WYŁĄCZNIE do models_twomodel/arbitrage/.

KONTEKST (named blocker). Dwa wyspecjalizowane modele LGBM:
  - SOLO   — kandydat z PUSTYM workiem (bag_size==0 & drops==0 & pickup==0)
  - BUNDLE — kandydat z czymkolwiek w worku / pending
routują się per-kandydat po JEGO WŁASNYM stanie worka. Problem: w decyzji MIESZANEJ
(część kandydatów empty, część z workiem) trzeba PORÓWNAĆ score'y z DWÓCH różnych
modeli — a te modele żyją w innych skalach score'u. Naiwne „argmax po surowym
regime_score" może być przekłamane skalą. Ten harness mierzy, czy jakakolwiek
KALIBRACJA arbitrażu (zscore / offset δ / Pwin / stacking) pozwala dwumodelowi
bić DWA baseline'y (B1 solo-first, B2 clean-unified) na decyzjach MIXED —
out-of-time, na prawdziwych forward dniach.

METODA (bez wycieku):
  - cutoff = dzień na indeksie -forward_days; train = dni < cutoff; forward = dni >= cutoff.
  - calib = ostatnie forward_days dni TRAIN-a; core_train = reszta train-a.
    Wszystkie kalibratory arbitrażu fitowane WYŁĄCZNIE na calib (NIGDY na forward).
  - SOLO/BUNDLE/UNIFIED modele uczone WYŁĄCZNIE na core_train (< cutoff − calib).
  - metryka = decision-level top-1 (wybrany kandydat ma label==1).

Uruchom (venv pipeline'u ML ma pyarrow+lightgbm+sklearn):
  /root/.openclaw/workspace/scripts/ml_data_prep/venv/bin/python3 \
      /root/.openclaw/workspace/scripts/dispatch_v2/ml_data_prep/arbitrage_forward.py
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

os.environ.setdefault("OMP_NUM_THREADS", "2")
os.environ.setdefault("OPENBLAS_NUM_THREADS", "2")

# Mirror online_shadow_parity.py sys.path setup.
HERE = Path(__file__).resolve().parent
SCRIPTS = Path("/root/.openclaw/workspace/scripts")
PROD_ML = SCRIPTS / "ml_data_prep"
for _p in (str(HERE), str(SCRIPTS), str(PROD_ML)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

OUT_DIR = HERE / "models_twomodel" / "arbitrage"
OUT = OUT_DIR / "arbitrage_report.json"

# Próg pewności „pustego worka" — kandydat trafia do SOLO gdy te trzy = 0.
# Tożsame z osią worka serwowania (_bag_axis_level) + solo_mask na poziomie decyzji.


# ─────────────────────────────────────────────────────────────────────────────
# CZYSTE HELPERY (importowalne w testach BEZ modeli / parquet)
# ─────────────────────────────────────────────────────────────────────────────
def is_empty_bag(bag_size: Any, drops_pending: Any, pickup_pending: Any) -> bool:
    """Stan worka kandydata == pusty (→ reżim SOLO). Braki traktujemy jako 0/empty.

    UWAGA: routing per-KANDYDAT idzie po JEGO własnym worku, nie po `level` decyzji.
    """
    def _z(v: Any) -> float:
        try:
            if v is None:
                return 0.0
            f = float(v)
            if f != f:  # NaN
                return 0.0
            return f
        except (TypeError, ValueError):
            return 0.0
    return _z(bag_size) == 0.0 and _z(drops_pending) == 0.0 and _z(pickup_pending) == 0.0


def regime_of(empty: bool) -> str:
    return "solo" if empty else "bundle"


def classify_decision(empties: List[bool]) -> str:
    """MIXED / PURE_SOLO / PURE_BUNDLE z listy flag is_empty kandydatów puli."""
    if not empties:
        return "PURE_BUNDLE"
    n_empty = sum(1 for e in empties if e)
    n_bag = sum(1 for e in empties if not e)
    if n_empty >= 1 and n_bag >= 1:
        return "MIXED"
    if n_bag == 0:
        return "PURE_SOLO"
    return "PURE_BUNDLE"


def regime_score(empty: bool, solo_score: float, bundle_score: float) -> float:
    """Efektywny score kandydata wg jego reżimu (solo gdy empty, inaczej bundle)."""
    return solo_score if empty else bundle_score


def argmax_idx(values: List[float]) -> int:
    """Indeks maksimum (pierwszy przy remisie). Pusta lista → -1."""
    if not values:
        return -1
    best_i, best_v = 0, values[0]
    for i in range(1, len(values)):
        if values[i] > best_v:
            best_i, best_v = i, values[i]
    return best_i


def top1_is_correct(pick_idx: int, labels: List[int]) -> bool:
    """Czy wybrany kandydat to zwycięzca (label==1)."""
    if pick_idx < 0 or pick_idx >= len(labels):
        return False
    return int(labels[pick_idx]) == 1


def apply_offset(empties: List[bool], solo_scores: List[float],
                 bundle_scores: List[float], delta: float) -> List[float]:
    """Efektywny score z offsetem δ: solo gdy empty, inaczej (bundle + δ).

    δ przesuwa skalę BUNDLE względem SOLO. Rośnie δ → kandydaci z workiem
    stają się „atrakcyjniejsi" i przy pewnym progu przejmują top-1.
    """
    out = []
    for e, ss, bs in zip(empties, solo_scores, bundle_scores):
        out.append(ss if e else (bs + delta))
    return out


def zscore_standardize(score: float, mean: float, std: float) -> float:
    """(score − mean) / std, ze strażnikiem zerowej wariancji (std<=0 → 0.0)."""
    if std is None or std <= 0:
        return 0.0
    return (score - mean) / std


def apply_zscore(empties: List[bool], solo_scores: List[float], bundle_scores: List[float],
                 solo_mean: float, solo_std: float,
                 bundle_mean: float, bundle_std: float) -> List[float]:
    """Efektywny zestandaryzowany regime_score po puli."""
    out = []
    for e, ss, bs in zip(empties, solo_scores, bundle_scores):
        if e:
            out.append(zscore_standardize(ss, solo_mean, solo_std))
        else:
            out.append(zscore_standardize(bs, bundle_mean, bundle_std))
    return out


def grid_for_offset(solo_scores: List[float], bundle_scores: List[float]) -> List[float]:
    """Rozsądny grid δ pokrywający lukę skali solo↔bundle (z marginesem).

    δ = solo_center − bundle_center ± span, gdzie span ~ rozstęp score'ów.
    Zwraca posortowaną listę kandydatów δ (zawsze zawiera 0.0).
    """
    import numpy as np
    s = np.asarray([v for v in solo_scores if v == v], dtype=float)
    b = np.asarray([v for v in bundle_scores if v == v], dtype=float)
    if s.size == 0 or b.size == 0:
        return [0.0]
    s_med, b_med = float(np.median(s)), float(np.median(b))
    center = s_med - b_med
    span = float(max(np.std(s), np.std(b), abs(s_med), abs(b_med), 1.0)) * 3.0
    lo, hi = center - span, center + span
    grid = list(np.linspace(lo, hi, 81))
    grid.append(0.0)
    return sorted(set(round(g, 4) for g in grid))


# ─────────────────────────────────────────────────────────────────────────────
# CZĘŚĆ MODELOWA (wymaga lightgbm + parquet) — uruchamiana tylko w mainie
# ─────────────────────────────────────────────────────────────────────────────
def _train_regime_model(pairs, drop_bundle: bool, seed: int = 42):
    """Czysty retrain JEDNEGO reżimu na podanych parach (wzór authoritative_pairwise).

    Zwraca (booster, label_enc, tier_categories, feature_order). Trening wyłącznie
    na `pairs` (które caller gwarantuje że są < cutoff i bez calib).
    """
    import lightgbm as lgb
    import train_two_models as tm

    d = sorted(pairs["_date"].dropna().unique())
    nval = max(1, int(len(d) * 0.10))
    vd = set(d[-nval:])
    tr = pairs[~pairs["_date"].isin(vd)]
    va = pairs[pairs["_date"].isin(vd)]
    tpw = tm.build_pointwise(tr, drop_bundle=drop_bundle)
    vpw = tm.build_pointwise(va, drop_bundle=drop_bundle)
    le = tm.fit_label_encoders(tpw)
    tc = tm.fit_tier_categories(tpw)
    tpw = tm.apply_tier_onehot(tm.apply_label_encoders(tpw, le), tc)
    vpw = tm.apply_tier_onehot(tm.apply_label_encoders(vpw, le), tc)
    fo = tm.feature_columns_of(tpw)
    Xtr, ytr, gtr = tm.to_arrays(tpw, fo)
    Xva, yva, gva = tm.to_arrays(vpw, fo)
    hp = dict(tm.HYPERPARAMS)
    hp["num_threads"] = 2
    hp["random_state"] = seed
    m = lgb.LGBMRanker(**hp)
    m.fit(Xtr, ytr, group=gtr, eval_set=[(Xva, yva)], eval_group=[gva],
          eval_at=[5], callbacks=[lgb.early_stopping(50, verbose=False)])
    return m.booster_, le, tc, fo


def _score_pointwise(pw, model_pack, drop_bundle: bool) -> Dict[Tuple[str, str], float]:
    """Skoruj gotowy pointwise frame danym modelem; zwróć {(decision_id,courier_name): score}.

    `pw` musi mieć kolumny bazowe (po build_pointwise(drop_bundle=False)). Dla
    modelu SOLO zdejmujemy cechy bundlowe TYLKO na potrzeby macierzy X (przez
    reindex na feature_order modelu) — wystarczy że feature_order solo ich nie ma.
    """
    import numpy as np
    import pandas as pd
    import train_two_models as tm
    booster, le, tc, fo = model_pack
    enc = tm.apply_tier_onehot(tm.apply_label_encoders(pw, le), tc)
    # BUGFIX (2026-06-20): tm.to_arrays SORTUJE wewnętrznie po decision_id, więc preds
    # wracały w innej kolejności niż did/cn czytane z NIEposortowanego `pw` (build_pointwise
    # zwraca concat([winners, losers]), nie po decision_id) → score'y trafiały do ZŁYCH
    # kandydatów (cały wynik losowy). Budujemy X w PORZĄDKU `enc` (bez sortu), wiernie
    # replikując kształtowanie X z to_arrays (reindex→numeric→bool→fillna(-1)).
    X = enc.reindex(columns=fo)
    for col in X.columns:
        if X[col].dtype == "object":
            X[col] = pd.to_numeric(X[col], errors="coerce")
        if X[col].dtype == bool:
            X[col] = X[col].astype(np.int8)
    X = X.fillna(-1)
    preds = booster.predict(X)
    did = enc["decision_id"].astype(str).to_numpy()
    cn = enc["courier_name"].astype(str).to_numpy()
    return {(did[i], cn[i]): float(preds[i]) for i in range(len(did))}


def _build_decisions(fwd_pw, solo_pack, bundle_pack, unified_pack) -> List[Dict[str, Any]]:
    """Zbuduj listę decyzji forward z per-kandydat score'ami solo/bundle/unified.

    Każda decyzja: {decision_id, date, klasa, kandydaci[...]}. Kandydat:
    {idx, empty, label, solo, bundle, unified, dist, delta, rank, pool, bag}.
    """
    import numpy as np
    import pandas as pd

    # Score'y trzech modeli na CAŁYM forward pointwise (raz).
    solo_map = _score_pointwise(fwd_pw, solo_pack, drop_bundle=True)
    bundle_map = _score_pointwise(fwd_pw, bundle_pack, drop_bundle=False)
    unified_map = _score_pointwise(fwd_pw, unified_pack, drop_bundle=False)

    decisions: List[Dict[str, Any]] = []
    for did, grp in fwd_pw.groupby("decision_id"):
        did_s = str(did)
        cands = []
        for _, row in grp.iterrows():
            cn = str(row["courier_name"])
            empty = is_empty_bag(row.get("bag_size"), row.get("bag_drops_pending"),
                                 row.get("bag_pickup_pending"))
            key = (did_s, cn)
            cands.append({
                "empty": empty,
                "label": int(row["label"]),
                "solo": solo_map.get(key, float("nan")),
                "bundle": bundle_map.get(key, float("nan")),
                "unified": unified_map.get(key, float("nan")),
                "dist": float(row.get("dist_to_pickup_km", -1.0)) if pd.notna(row.get("dist_to_pickup_km")) else -1.0,
                "delta": float(row.get("delta_dist_km", 0.0)) if pd.notna(row.get("delta_dist_km")) else 0.0,
                "rank": float(row.get("rank_by_dist", 9999)) if pd.notna(row.get("rank_by_dist")) else 9999.0,
                "pool": float(row.get("pool_size", len(grp))) if pd.notna(row.get("pool_size")) else float(len(grp)),
                "bag": float(row.get("bag_size", 0.0)) if pd.notna(row.get("bag_size")) else 0.0,
            })
        if not cands or sum(c["label"] for c in cands) != 1:
            # decyzja musi mieć dokładnie jednego zwycięzcę
            continue
        klass = classify_decision([c["empty"] for c in cands])
        # data decyzji (pierwszy wiersz grupy)
        dval = grp["_date"].iloc[0] if "_date" in grp.columns else None
        decisions.append({
            "decision_id": did_s,
            "date": str(pd.Timestamp(dval).date()) if dval is not None and pd.notna(dval) else None,
            "klass": klass,
            "cands": cands,
        })
    return decisions


# ── kalibratory arbitrażu (fit WYŁĄCZNIE na calib) ───────────────────────────
def _fit_zscore_stats(calib_decisions) -> Dict[str, float]:
    """mean/std score'ów solo i bundle po kandydatach calib (per model)."""
    import numpy as np
    solo_vals, bundle_vals = [], []
    for d in calib_decisions:
        for c in d["cands"]:
            if c["solo"] == c["solo"]:
                solo_vals.append(c["solo"])
            if c["bundle"] == c["bundle"]:
                bundle_vals.append(c["bundle"])
    sv = np.asarray(solo_vals, dtype=float)
    bv = np.asarray(bundle_vals, dtype=float)
    return {
        "solo_mean": float(sv.mean()) if sv.size else 0.0,
        "solo_std": float(sv.std()) if sv.size else 1.0,
        "bundle_mean": float(bv.mean()) if bv.size else 0.0,
        "bundle_std": float(bv.std()) if bv.size else 1.0,
    }


def _fit_offset_delta(calib_decisions) -> float:
    """Dobierz skalar δ maksymalizujący top-1 na MIXED decyzjach calib (grid search)."""
    mixed = [d for d in calib_decisions if d["klass"] == "MIXED"]
    if not mixed:
        return 0.0
    all_solo = [c["solo"] for d in mixed for c in d["cands"]]
    all_bundle = [c["bundle"] for d in mixed for c in d["cands"]]
    grid = grid_for_offset(all_solo, all_bundle)
    best_delta, best_acc = 0.0, -1.0
    for delta in grid:
        correct = 0
        for d in mixed:
            empties = [c["empty"] for c in d["cands"]]
            ss = [c["solo"] for c in d["cands"]]
            bs = [c["bundle"] for c in d["cands"]]
            eff = apply_offset(empties, ss, bs, delta)
            pick = argmax_idx(eff)
            if top1_is_correct(pick, [c["label"] for c in d["cands"]]):
                correct += 1
        acc = correct / len(mixed)
        if acc > best_acc:
            best_acc, best_delta = acc, delta
    return best_delta


def _fit_pwin_calibrators(calib_decisions):
    """Per-model mapowanie raw_score → P(label==1) (isotonic, fallback logistic).

    Zwraca (solo_cal, bundle_cal) — obiekty z metodą .predict(np.array)->np.array.
    """
    import numpy as np
    from sklearn.isotonic import IsotonicRegression
    from sklearn.linear_model import LogisticRegression

    def _fit_one(key):
        xs, ys = [], []
        for d in calib_decisions:
            for c in d["cands"]:
                v = c[key]
                if v == v:
                    xs.append(v)
                    ys.append(int(c["label"]))
        x = np.asarray(xs, dtype=float)
        y = np.asarray(ys, dtype=int)
        if x.size == 0 or len(set(y.tolist())) < 2:
            # brak sygnału / jedna klasa → mapowanie tożsamościowe (rank-preserving)
            class _Identity:
                def predict(self, arr):
                    return np.asarray(arr, dtype=float)
            return _Identity()
        try:
            iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
            iso.fit(x, y)
            # walidacja stabilności: isotonic musi dać >1 unikalną wartość
            uniq = np.unique(iso.predict(x))
            if uniq.size < 2:
                raise ValueError("isotonic zdegenerowany")
            return iso
        except Exception:
            lr = LogisticRegression()
            lr.fit(x.reshape(-1, 1), y)

            class _LRWrap:
                def __init__(self, m):
                    self.m = m
                def predict(self, arr):
                    a = np.asarray(arr, dtype=float).reshape(-1, 1)
                    return self.m.predict_proba(a)[:, 1]
            return _LRWrap(lr)

    return _fit_one("solo"), _fit_one("bundle")


def _fit_stacker(calib_decisions, seed: int = 42):
    """Meta-LGBMRanker na decyzjach calib: cechy per-kandydat + group=decision_id."""
    import numpy as np
    import lightgbm as lgb

    X, y, groups = [], [], []
    for d in calib_decisions:
        cands = d["cands"]
        if not cands:
            continue
        for c in cands:
            rs = regime_score(c["empty"], c["solo"], c["bundle"])
            X.append([
                rs if rs == rs else -1.0,
                1.0 if c["empty"] else 0.0,
                c["dist"], c["delta"], c["rank"], c["pool"], c["bag"],
            ])
            y.append(int(c["label"]))
        groups.append(len(cands))
    if not X or sum(groups) == 0:
        return None
    Xn = np.asarray(X, dtype=float)
    yn = np.asarray(y, dtype=int)
    hp = dict(
        objective="lambdarank", metric="ndcg", n_estimators=200,
        learning_rate=0.05, num_leaves=15, min_child_samples=10,
        reg_alpha=0.1, reg_lambda=0.1, random_state=seed,
        num_threads=2, verbosity=-1,
    )
    m = lgb.LGBMRanker(**hp)
    m.fit(Xn, yn, group=np.asarray(groups, dtype=int))
    return m.booster_


def _stacker_features_for(cands) -> "Any":
    import numpy as np
    rows = []
    for c in cands:
        rs = regime_score(c["empty"], c["solo"], c["bundle"])
        rows.append([
            rs if rs == rs else -1.0,
            1.0 if c["empty"] else 0.0,
            c["dist"], c["delta"], c["rank"], c["pool"], c["bag"],
        ])
    return np.asarray(rows, dtype=float)


# ── pickery (decision-level top-1) ───────────────────────────────────────────
def _pick_b1_solo_first(cands) -> int:
    """B1: jeśli istnieje empty → argmax solo wśród empty; inaczej argmax bundle wśród bagged."""
    empties = [i for i, c in enumerate(cands) if c["empty"]]
    if empties:
        vals = [cands[i]["solo"] for i in empties]
        return empties[argmax_idx(vals)]
    bagged = list(range(len(cands)))
    vals = [cands[i]["bundle"] for i in bagged]
    return bagged[argmax_idx(vals)]


def _pick_b2_unified(cands) -> int:
    return argmax_idx([c["unified"] for c in cands])


def _pick_b4_nearest(cands) -> int:
    """B4: rank_by_dist==1 jeśli jest; inaczej min dist_to_pickup_km."""
    for i, c in enumerate(cands):
        if c["rank"] == 1.0:
            return i
    # min dist (ignoruj sentinel -1)
    best_i, best_d = -1, float("inf")
    for i, c in enumerate(cands):
        d = c["dist"]
        if d is None or d < 0:
            continue
        if d < best_d:
            best_i, best_d = i, d
    return best_i if best_i >= 0 else 0


def _pick_v0_raw(cands) -> int:
    return argmax_idx([regime_score(c["empty"], c["solo"], c["bundle"]) for c in cands])


def _pick_v1_zscore(cands, zs) -> int:
    empties = [c["empty"] for c in cands]
    ss = [c["solo"] for c in cands]
    bs = [c["bundle"] for c in cands]
    eff = apply_zscore(empties, ss, bs, zs["solo_mean"], zs["solo_std"],
                       zs["bundle_mean"], zs["bundle_std"])
    return argmax_idx(eff)


def _pick_v2_offset(cands, delta) -> int:
    empties = [c["empty"] for c in cands]
    ss = [c["solo"] for c in cands]
    bs = [c["bundle"] for c in cands]
    return argmax_idx(apply_offset(empties, ss, bs, delta))


def _pick_v3_pwin(cands, solo_cal, bundle_cal) -> int:
    import numpy as np
    eff = []
    for c in cands:
        if c["empty"]:
            p = float(solo_cal.predict(np.asarray([c["solo"]], dtype=float))[0]) if c["solo"] == c["solo"] else -1.0
        else:
            p = float(bundle_cal.predict(np.asarray([c["bundle"]], dtype=float))[0]) if c["bundle"] == c["bundle"] else -1.0
        eff.append(p)
    return argmax_idx(eff)


def _pick_v4_stacker(cands, stacker) -> int:
    if stacker is None:
        return _pick_v0_raw(cands)
    feats = _stacker_features_for(cands)
    preds = stacker.predict(feats)
    return argmax_idx([float(p) for p in preds])


VARIANTS = ["B1", "B2", "B4", "V0", "V1", "V2", "V3", "V4"]
SUBSETS = ["ALL", "MIXED", "PURE_SOLO", "PURE_BUNDLE"]


def _eval_all(decisions, calibrators) -> Dict[str, Any]:
    """Policz top-1 dla każdego wariantu × subset oraz per-day MIXED dla B1/B2/best-arb."""
    zs = calibrators["zscore"]
    delta = calibrators["delta"]
    solo_cal, bundle_cal = calibrators["pwin"]
    stacker = calibrators["stacker"]

    def pick(variant, cands):
        if variant == "B1":
            return _pick_b1_solo_first(cands)
        if variant == "B2":
            return _pick_b2_unified(cands)
        if variant == "B4":
            return _pick_b4_nearest(cands)
        if variant == "V0":
            return _pick_v0_raw(cands)
        if variant == "V1":
            return _pick_v1_zscore(cands, zs)
        if variant == "V2":
            return _pick_v2_offset(cands, delta)
        if variant == "V3":
            return _pick_v3_pwin(cands, solo_cal, bundle_cal)
        if variant == "V4":
            return _pick_v4_stacker(cands, stacker)
        raise ValueError(variant)

    # liczniki: variant -> subset -> [correct, total]
    acc = {v: {s: [0, 0] for s in SUBSETS} for v in VARIANTS}
    for d in decisions:
        labels = [c["label"] for c in d["cands"]]
        for v in VARIANTS:
            p = pick(v, d["cands"])
            ok = top1_is_correct(p, labels)
            for s in ("ALL", d["klass"]):
                acc[v][s][1] += 1
                if ok:
                    acc[v][s][0] += 1

    table = {}
    for v in VARIANTS:
        table[v] = {}
        for s in SUBSETS:
            c, n = acc[v][s]
            table[v][s] = {"top1": round(c / n, 4) if n else None, "n": n}

    return table


def _per_day_mixed(decisions, calibrators, best_arb: str) -> List[Dict[str, Any]]:
    """Per-day MIXED top-1 dla B1, B2 i najlepszego wariantu arbitrażu."""
    zs = calibrators["zscore"]
    delta = calibrators["delta"]
    solo_cal, bundle_cal = calibrators["pwin"]
    stacker = calibrators["stacker"]

    def pick(variant, cands):
        if variant == "B1":
            return _pick_b1_solo_first(cands)
        if variant == "B2":
            return _pick_b2_unified(cands)
        if variant == "V0":
            return _pick_v0_raw(cands)
        if variant == "V1":
            return _pick_v1_zscore(cands, zs)
        if variant == "V2":
            return _pick_v2_offset(cands, delta)
        if variant == "V3":
            return _pick_v3_pwin(cands, solo_cal, bundle_cal)
        if variant == "V4":
            return _pick_v4_stacker(cands, stacker)
        return _pick_v0_raw(cands)

    by_day: Dict[str, List[Dict[str, Any]]] = {}
    for d in decisions:
        if d["klass"] != "MIXED":
            continue
        by_day.setdefault(d["date"] or "unknown", []).append(d)

    rows = []
    for day in sorted(by_day):
        ds = by_day[day]
        counts = {"B1": 0, "B2": 0, best_arb: 0}
        for d in ds:
            labels = [c["label"] for c in d["cands"]]
            for v in ("B1", "B2", best_arb):
                if top1_is_correct(pick(v, d["cands"]), labels):
                    counts[v] += 1
        n = len(ds)
        rows.append({
            "date": day, "n_mixed": n,
            "B1": round(counts["B1"] / n, 4) if n else None,
            "B2": round(counts["B2"] / n, 4) if n else None,
            best_arb: round(counts[best_arb] / n, 4) if n else None,
        })
    return rows


def run(forward_days: int = 14, seed: int = 42, end_offset: int = 0) -> Dict[str, Any]:
    import pandas as pd
    import train_two_models as tm  # noqa: F401  (import wcześnie by złapać brak pyarrow czytelnie)
    from twomodel_common import load_split, apply_prod_feature_shaping, solo_mask

    # 1) wczytaj + shape + daty + cutoff
    frames = [load_split(s) for s in ("train", "val", "test")]
    allp = pd.concat(frames, ignore_index=True)
    allp = apply_prod_feature_shaping(allp)
    allp["_date"] = pd.to_datetime(allp["date"], errors="coerce").dt.normalize()
    days = sorted(allp["_date"].dropna().unique())
    n_days = len(days)
    if n_days <= forward_days + end_offset:
        raise ValueError(f"za mało dni ({n_days}) na forward_days={forward_days}+end_offset={end_offset}")
    # forward = okno [cutoff, fwd_end) przesunięte o end_offset dni od końca (rolling-window robustness).
    # end_offset=0 → zachowanie pierwotne (cutoff=days[-forward_days], forward=ogon).
    cutoff = days[n_days - forward_days - end_offset]
    train_df = allp[allp["_date"] < cutoff].reset_index(drop=True)
    if end_offset > 0:
        fwd_end = days[n_days - end_offset]
        fwd_df = allp[(allp["_date"] >= cutoff) & (allp["_date"] < fwd_end)].reset_index(drop=True)
    else:
        fwd_df = allp[allp["_date"] >= cutoff].reset_index(drop=True)

    # 2) calib = ostatnie forward_days dni TRAIN-a; core_train = reszta
    train_days = sorted(train_df["_date"].dropna().unique())
    if len(train_days) <= forward_days:
        # awaryjnie: weź ostatnie 20% dni train jako calib
        n_calib = max(1, int(len(train_days) * 0.20))
    else:
        n_calib = forward_days
    calib_cut = train_days[-n_calib]
    core_train = train_df[train_df["_date"] < calib_cut].reset_index(drop=True)
    calib_df = train_df[train_df["_date"] >= calib_cut].reset_index(drop=True)

    print(f"[ARB] dni total={len(days)} | cutoff={pd.Timestamp(cutoff).date()} "
          f"| calib_cut={pd.Timestamp(calib_cut).date()}")
    print(f"[ARB] core_train pairs={len(core_train)} ({core_train['_date'].nunique()}d) | "
          f"calib pairs={len(calib_df)} ({calib_df['_date'].nunique()}d) | "
          f"forward pairs={len(fwd_df)} ({fwd_df['_date'].nunique()}d)")

    # 3) czysty retrain SOLO / BUNDLE / UNIFIED na core_train
    sm = solo_mask(core_train)
    solo_pack = _train_regime_model(core_train[sm].reset_index(drop=True), drop_bundle=True, seed=seed)
    print("[ARB] solo model wytrenowany")
    bundle_pack = _train_regime_model(core_train[~sm].reset_index(drop=True), drop_bundle=False, seed=seed)
    print("[ARB] bundle model wytrenowany")
    unified_pack = _train_regime_model(core_train, drop_bundle=False, seed=seed)
    print("[ARB] unified (baseline) model wytrenowany")

    # 4) decyzje forward + calib z per-kandydat score'ami
    fwd_pw = tm.build_pointwise(fwd_df, drop_bundle=False)
    # przenieś _date do pointwise (po decision_id) — potrzebne do per-day MIXED
    date_map = (fwd_df.drop_duplicates("decision_id").set_index("decision_id")["_date"])
    fwd_pw = fwd_pw.copy()
    fwd_pw["_date"] = fwd_pw["decision_id"].map(date_map)
    fwd_decisions = _build_decisions(fwd_pw, solo_pack, bundle_pack, unified_pack)

    calib_pw = tm.build_pointwise(calib_df, drop_bundle=False)
    calib_decisions = _build_decisions(calib_pw, solo_pack, bundle_pack, unified_pack)
    print(f"[ARB] forward decyzje={len(fwd_decisions)} | calib decyzje={len(calib_decisions)}")

    # 5) fit kalibratorów arbitrażu WYŁĄCZNIE na calib
    calibrators = {
        "zscore": _fit_zscore_stats(calib_decisions),
        "delta": _fit_offset_delta(calib_decisions),
        "pwin": _fit_pwin_calibrators(calib_decisions),
        "stacker": _fit_stacker(calib_decisions, seed=seed),
    }
    print(f"[ARB] δ (offset) dobrany na calib MIXED = {calibrators['delta']:.4f}")

    # 6) ewaluacja na forward
    table = _eval_all(fwd_decisions, calibrators)

    # subset n
    subset_n = {s: 0 for s in SUBSETS}
    for d in fwd_decisions:
        subset_n["ALL"] += 1
        subset_n[d["klass"]] += 1

    # który wariant arbitrażu (V*) bije OBA baseline'y na MIXED
    b1_mixed = table["B1"]["MIXED"]["top1"]
    b2_mixed = table["B2"]["MIXED"]["top1"]
    baseline_mixed = max(v for v in (b1_mixed, b2_mixed) if v is not None) if (b1_mixed is not None or b2_mixed is not None) else None
    arb_variants = ["V0", "V1", "V2", "V3", "V4"]
    best_arb, best_arb_mixed = None, -1.0
    for v in arb_variants:
        m = table[v]["MIXED"]["top1"]
        if m is not None and m > best_arb_mixed:
            best_arb, best_arb_mixed = v, m
    beats_both = []
    for v in arb_variants:
        m = table[v]["MIXED"]["top1"]
        if m is None or b1_mixed is None or b2_mixed is None:
            continue
        if m > b1_mixed and m > b2_mixed:
            beats_both.append((v, m))

    # per-day robustness (B1, B2, best arbitrage)
    per_day = _per_day_mixed(fwd_decisions, calibrators, best_arb or "V0")
    # czy najlepszy arbitraż bije OBA baseline'y robustly (≥ na każdym dniu z n_mixed>=5)
    robust_days = [r for r in per_day if (r["n_mixed"] or 0) >= 5]
    robust_beat = bool(robust_days) and all(
        (r[best_arb or "V0"] is not None and r["B1"] is not None and r["B2"] is not None
         and r[best_arb or "V0"] >= r["B1"] and r[best_arb or "V0"] >= r["B2"])
        for r in robust_days
    )

    if beats_both:
        margin = round(best_arb_mixed - baseline_mixed, 4) if baseline_mixed is not None else None
        verdict = (f"{best_arb} bije OBA baseline'y na MIXED: {best_arb_mixed} vs "
                   f"B1={b1_mixed}/B2={b2_mixed} (margines +{margin} nad lepszym baselinem); "
                   f"robust-across-days(n>=5)={robust_beat}")
    else:
        verdict = (f"ŻADEN wariant arbitrażu NIE bije obu baseline'ów na MIXED. "
                   f"best_arb={best_arb}={best_arb_mixed} vs B1={b1_mixed}/B2={b2_mixed}")

    report = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "forward_days": forward_days,
        "cutoff_date": str(pd.Timestamp(cutoff).date()),
        "calib_cut_date": str(pd.Timestamp(calib_cut).date()),
        "seed": seed,
        "n_pairs": {
            "core_train": len(core_train), "calib": len(calib_df), "forward": len(fwd_df),
        },
        "n_decisions": {
            "forward": len(fwd_decisions), "calib": len(calib_decisions),
            "subset": subset_n,
        },
        "offset_delta_fitted_on_calib_mixed": round(float(calibrators["delta"]), 4),
        "zscore_stats_calib": {k: round(v, 4) for k, v in calibrators["zscore"].items()},
        "variant_definitions": {
            "B1": "solo-first rule (argmax solo wśród empty, else argmax bundle) — obecny naiwny merge",
            "B2": "clean-unified (argmax unified po całej puli)",
            "B4": "nearest-dist floor (rank_by_dist==1 / min dist)",
            "V0": "raw regime_score argmax (bez kalibracji)",
            "V1": "zscore-standardized regime_score (mean/std z calib)",
            "V2": "offset δ (bundle+δ), δ dobrany na calib MIXED",
            "V3": "Pwin calibration (isotonic per-model raw_score→P(label==1) z calib)",
            "V4": "stacking meta-LGBMRanker na calib (per-candidate features)",
        },
        "top1_by_variant_subset": table,
        "per_day_mixed_robustness": per_day,
        "best_arbitrage_variant": best_arb,
        "arbitrage_beats_both_baselines_on_mixed": [
            {"variant": v, "mixed_top1": m} for v, m in beats_both
        ],
        "robust_across_days_n5": robust_beat,
        "verdict": verdict,
    }
    return report


def main() -> int:
    ap = argparse.ArgumentParser(description="[ARB] cross-regime score arbitrage forward measurement")
    ap.add_argument("--forward-days", type=int, default=14)
    ap.add_argument("--seed", type=int, default=42)
    ap.add_argument("--report", default=str(OUT))
    args = ap.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    report = run(forward_days=args.forward_days, seed=args.seed)

    # zapis
    json.dump(report, open(args.report, "w"), indent=2, default=str, ensure_ascii=False)

    # czytelna tabela do stdout
    print("\n" + "=" * 78)
    print(f"ARBITRAGE FORWARD — cutoff={report['cutoff_date']} forward_days={report['forward_days']}")
    print(f"forward decisions: ALL={report['n_decisions']['subset']['ALL']} "
          f"MIXED={report['n_decisions']['subset']['MIXED']} "
          f"PURE_SOLO={report['n_decisions']['subset']['PURE_SOLO']} "
          f"PURE_BUNDLE={report['n_decisions']['subset']['PURE_BUNDLE']}")
    print(f"δ offset (calib MIXED) = {report['offset_delta_fitted_on_calib_mixed']}")
    print("-" * 78)
    hdr = f"{'variant':<7}" + "".join(f"{s:<16}" for s in SUBSETS)
    print(hdr)
    for v in VARIANTS:
        row = f"{v:<7}"
        for s in SUBSETS:
            cell = report["top1_by_variant_subset"][v][s]
            txt = f"{cell['top1']} (n={cell['n']})" if cell["top1"] is not None else f"- (n={cell['n']})"
            row += f"{txt:<16}"
        print(row)
    print("-" * 78)
    print("per-day MIXED robustness (B1 / B2 / best-arb):")
    barb = report["best_arbitrage_variant"] or "V0"
    for r in report["per_day_mixed_robustness"]:
        print(f"  {r['date']}: n={r['n_mixed']:<4} B1={r['B1']} B2={r['B2']} {barb}={r[barb]}")
    print("-" * 78)
    print("VERDICT:", report["verdict"])
    print(f"\nraport -> {args.report}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
