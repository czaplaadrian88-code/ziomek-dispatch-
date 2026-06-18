#!/usr/bin/env python3
"""ETA R3 residual inference (shadow-only, 2026-06-18).

Ładuje wytrenowany LightGBM booster (`ml_data_prep/models/eta_residual_v1/`) i liczy
skorygowaną ETA = predicted_delivery_min (baza OSRM) + pred_residual. NA RAZIE TYLKO SHADOW:
konsumowane przez eta_calibration_logger (off-hot-path) do pomiaru MAE(base) vs MAE(corrected)
na ŻYWYCH, held-out danych — bramka PRZED jakimkolwiek wpięciem do feasibility/chain_eta.

KRYTYCZNE — feature parity z treningiem (eod_drafts/2026-06-18/eta_residual_model.py:feats):
kolejność i kodowanie cech MUSZĄ być identyczne, inaczej predykcja jest śmieciem. Stąd
`build_features` jest lustrem `feats()`; `rest_freq`/`tier_ord`/braki(-1) liczone tak samo.

Lazy singleton (booster ładowany raz). Fail-soft: każdy błąd → None (zero wpływu na cokolwiek)."""
import json
import os
import threading

import numpy as np

_MODEL_DIR = "/root/.openclaw/workspace/scripts/ml_data_prep/models/eta_residual_v1"
_TIERS_PATH = "/root/.openclaw/workspace/dispatch_state/courier_tiers.json"

# Musi być identyczne z treningiem (TIER_ORD w eta_residual_model.py).
_TIER_ORD = {"gold": 4, "std+": 3, "std": 2, "slow": 1, "new": 0}
_TIER_ORD_DEFAULT = 2  # tier nieznany → std (jak trening)

_lock = threading.Lock()
_state = {"loaded": False, "booster": None, "features": None,
          "rest_freq": None, "cid2tier": None, "ok": False}


def _num(x):
    return x if isinstance(x, (int, float)) and not isinstance(x, bool) else None


def _tier_of(v):
    """Wyciąga tier z wpisu courier_tiers.json — lustro tof() z treningu."""
    if isinstance(v, dict):
        return ((v.get("bag") or {}).get("tier") or v.get("tier") or v.get("tier_label"))
    return v


def _load():
    """Idempotentny lazy-load artefaktów. Nie rzuca — ustawia ok=False przy błędzie."""
    if _state["loaded"]:
        return _state["ok"]
    with _lock:
        if _state["loaded"]:
            return _state["ok"]
        try:
            import lightgbm as lgb
            booster = lgb.Booster(model_file=f"{_MODEL_DIR}/model.txt")
            with open(f"{_MODEL_DIR}/features.json", encoding="utf-8") as f:
                features = json.load(f)
            with open(f"{_MODEL_DIR}/rest_freq.json", encoding="utf-8") as f:
                rest_freq = json.load(f)
            cid2tier = {}
            if os.path.exists(_TIERS_PATH):
                with open(_TIERS_PATH, encoding="utf-8") as f:
                    T = json.load(f)
                cid2tier = {k: _tier_of(v) for k, v in T.items() if k != "_meta"}
            _state.update(booster=booster, features=features, rest_freq=rest_freq,
                          cid2tier=cid2tier, ok=True)
        except Exception:
            _state["ok"] = False
        finally:
            _state["loaded"] = True
    return _state["ok"]


def build_features(*, bag_size, predicted_delivery_min, hour_warsaw, is_weekend,
                   is_bundle, restaurant, courier_id, pool_feasible):
    """Buduje wektor cech w KOLEJNOŚCI features.json. Lustro feats() z treningu:
    [bag_size, pred_delivery_min, hour, is_weekend, is_bundle, peak, tier_ord, rest_freq, pool_feasible]
    Braki → -1 (numeryczne) / 0 (bool), peak = 11-14 lub 17-20, tier z courier_tiers."""
    rest_freq_tbl = _state["rest_freq"] or {}
    cid2tier = _state["cid2tier"] or {}
    bs = _num(bag_size)
    pdm = _num(predicted_delivery_min)
    hr = _num(hour_warsaw)
    pf = _num(pool_feasible)
    tier = cid2tier.get(str(courier_id) if courier_id is not None else "")
    peak = 1 if (hr is not None and (11 <= hr < 14 or 17 <= hr < 20)) else 0
    rfreq = rest_freq_tbl.get((restaurant or "").lower(), 0)
    return [
        bs if bs is not None else -1,
        pdm if pdm is not None else -1,
        hr if hr is not None else -1,
        1 if is_weekend else 0,
        1 if is_bundle else 0,
        peak,
        _TIER_ORD.get(tier, _TIER_ORD_DEFAULT),
        rfreq,
        pf if pf is not None else -1,
    ]


def predict_residual(features):
    """Zwraca przewidziany residual (real−pred) w minutach albo None (fail-soft)."""
    if not _load():
        return None
    try:
        arr = np.array([features], dtype=float)
        return float(_state["booster"].predict(arr)[0])
    except Exception:
        return None


def predict_corrected(*, bag_size, predicted_delivery_min, hour_warsaw, is_weekend,
                      is_bundle, restaurant, courier_id, pool_feasible):
    """Wygodny wrapper: zwraca (corrected_min, residual_pred) albo (None, None).
    corrected = predicted_delivery_min + residual_pred. Wymaga predicted_delivery_min (baza)."""
    pdm = _num(predicted_delivery_min)
    if pdm is None or not _load():
        return (None, None)
    feats = build_features(
        bag_size=bag_size, predicted_delivery_min=pdm, hour_warsaw=hour_warsaw,
        is_weekend=is_weekend, is_bundle=is_bundle, restaurant=restaurant,
        courier_id=courier_id, pool_feasible=pool_feasible)
    resid = predict_residual(feats)
    if resid is None:
        return (None, None)
    return (round(pdm + resid, 2), round(resid, 2))


def is_available():
    """Czy artefakty modelu da się załadować (do health-check/testów)."""
    return _load()
