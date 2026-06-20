#!/usr/bin/env python3
"""ETA R3 residual inference (shadow-only, 2026-06-18).

Ładuje wytrenowany LightGBM booster (`ml_data_prep/models/eta_residual_v1/`) i liczy
skorygowaną ETA = predicted_delivery_min (baza OSRM) + pred_residual. NA RAZIE TYLKO SHADOW:
konsumowane przez eta_calibration_logger (off-hot-path) do pomiaru MAE(base) vs MAE(corrected)
na ŻYWYCH, held-out danych — bramka PRZED jakimkolwiek wpięciem do feasibility/chain_eta.

KRYTYCZNE — feature parity z treningiem (eod_drafts/2026-06-18/eta_residual_model.py:feats):
kolejność i kodowanie cech MUSZĄ być identyczne, inaczej predykcja jest śmieciem. Stąd
`build_features` jest lustrem `feats()`; `rest_freq`/`tier_ord`/braki(-1) liczone tak samo.

Lazy singleton (booster ładowany raz). Fail-soft: każdy błąd → None (zero wpływu na cokolwiek).

DRUGA ŚCIEŻKA SHADOW (B_drop, 2026-06-20) — ADDITIVE, NIE zmienia v1:
`predict_corrected_drop` ładuje artefakt `eta_residual_v2_drop/` (8 cech, BEZ pool_feasible —
usuwa źródłowo train/serve skew KS D=0,87 cechy pool_feasible). Osobny lazy singleton
(`_state_drop`), osobny loader (`_load_drop`), osobny build (`build_features_drop`). Konsument
(eta_calibration_logger) woła tę ścieżkę WYŁĄCZNIE gdy ENABLE_ETA_R3_DROP_SHADOW=true; przy
domyślnym OFF moduł zachowuje się 1:1 jak wcześniej (v1). Forward-walidacja 06-20 (TOR1): DROP
+12,3% MAE na dni robocze (≥8%) ale +6,2% weekend (<8%) → BRAMKA flipu primary = NO-GO (patrz
/root/TOR1_R3_ETA_2026-06-20.md). Ścieżka shadow zostaje do dalszego pomiaru forward."""
import json
import os
import threading

import numpy as np

_MODEL_DIR = "/root/.openclaw/workspace/scripts/ml_data_prep/models/eta_residual_v1"
_MODEL_DIR_DROP = "/root/.openclaw/workspace/scripts/ml_data_prep/models/eta_residual_v2_drop"
_TIERS_PATH = "/root/.openclaw/workspace/dispatch_state/courier_tiers.json"

# Musi być identyczne z treningiem (TIER_ORD w eta_residual_model.py).
_TIER_ORD = {"gold": 4, "std+": 3, "std": 2, "slow": 1, "new": 0}
_TIER_ORD_DEFAULT = 2  # tier nieznany → std (jak trening)

_lock = threading.Lock()
_state = {"loaded": False, "booster": None, "features": None,
          "rest_freq": None, "cid2tier": None, "ok": False}
# Osobny singleton dla wariantu B_drop (8 cech). Współdzieli cid2tier z _state (te same tiery),
# ale ma własny booster + rest_freq (zapisany przy treningu v2_drop).
_state_drop = {"loaded": False, "booster": None, "features": None,
               "rest_freq": None, "ok": False}


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


def _load_drop():
    """Lazy-load wariantu B_drop (8 cech). Współdzieli cid2tier z _load() (te same tiery),
    ale ładuje własny booster + rest_freq z eta_residual_v2_drop/. Fail-soft → ok=False."""
    if _state_drop["loaded"]:
        return _state_drop["ok"]
    with _lock:
        if _state_drop["loaded"]:
            return _state_drop["ok"]
        try:
            import lightgbm as lgb
            booster = lgb.Booster(model_file=f"{_MODEL_DIR_DROP}/model.txt")
            with open(f"{_MODEL_DIR_DROP}/features.json", encoding="utf-8") as f:
                features = json.load(f)
            with open(f"{_MODEL_DIR_DROP}/rest_freq.json", encoding="utf-8") as f:
                rest_freq = json.load(f)
            # cid2tier ten sam co v1 — załaduj _state jeśli trzeba (idempotentnie poza lockiem
            # nie da się, ale _load() ma własny re-entrancy guard; tu już trzymamy _lock,
            # więc czytamy tiery bezpośrednio, by nie wchodzić w zagnieżdżony _load()).
            cid2tier = {}
            if os.path.exists(_TIERS_PATH):
                with open(_TIERS_PATH, encoding="utf-8") as f:
                    T = json.load(f)
                cid2tier = {k: _tier_of(v) for k, v in T.items() if k != "_meta"}
            _state_drop.update(booster=booster, features=features, rest_freq=rest_freq,
                               cid2tier=cid2tier, ok=True)
        except Exception:
            _state_drop["ok"] = False
        finally:
            _state_drop["loaded"] = True
    return _state_drop["ok"]


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


# ───────────────────────── WARIANT B_drop (8 cech, bez pool_feasible) ─────────────────────────
def build_features_drop(*, bag_size, predicted_delivery_min, hour_warsaw, is_weekend,
                        is_bundle, restaurant, courier_id):
    """Wektor 8 cech w KOLEJNOŚCI features.json wariantu B_drop:
    [bag_size, pred_delivery_min, hour, is_weekend, is_bundle, peak, tier_ord, rest_freq]
    = build_features() bez ostatniej cechy pool_feasible. Lustro FN_DROP z eta_r3_fix_skew.feats.
    rest_freq czytany z rest_freq.json wariantu DROP (osobny od v1, choć trening ten sam zbiór)."""
    rest_freq_tbl = _state_drop["rest_freq"] or {}
    cid2tier = _state_drop["cid2tier"] or {}
    bs = _num(bag_size)
    pdm = _num(predicted_delivery_min)
    hr = _num(hour_warsaw)
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
    ]


def predict_residual_drop(features):
    """Residual (real−pred) z wariantu B_drop albo None (fail-soft). Wymaga 8 cech."""
    if not _load_drop():
        return None
    try:
        arr = np.array([features], dtype=float)
        return float(_state_drop["booster"].predict(arr)[0])
    except Exception:
        return None


def predict_corrected_drop(*, bag_size, predicted_delivery_min, hour_warsaw, is_weekend,
                           is_bundle, restaurant, courier_id):
    """Wariant B_drop: (corrected_min, residual_pred) albo (None, None). corrected = base + resid.
    Brak argumentu pool_feasible — cecha usunięta źródłowo (eliminacja train/serve skew)."""
    pdm = _num(predicted_delivery_min)
    if pdm is None or not _load_drop():
        return (None, None)
    feats = build_features_drop(
        bag_size=bag_size, predicted_delivery_min=pdm, hour_warsaw=hour_warsaw,
        is_weekend=is_weekend, is_bundle=is_bundle, restaurant=restaurant,
        courier_id=courier_id)
    resid = predict_residual_drop(feats)
    if resid is None:
        return (None, None)
    return (round(pdm + resid, 2), round(resid, 2))


def is_available_drop():
    """Czy artefakt wariantu B_drop da się załadować (health-check/testy)."""
    return _load_drop()


# ───────────────────────── flaga + guarded entry point dla wariantu B_drop ─────────────────────────
# Flagę czytamy WPROST z flags.json (KANON hot) — ten sam wzorzec co _read_r3_flag w
# eta_calibration_logger. dispatch_v2/ → parent = scripts/ → tam leży flags.json.
_FLAGS_PATH = os.path.dirname(os.path.dirname(os.path.abspath(__file__))) + "/flags.json"


def drop_shadow_enabled():
    """Czy ENABLE_ETA_R3_DROP_SHADOW=true w flags.json (fail-soft → False = OFF domyślnie)."""
    try:
        with open(_FLAGS_PATH, encoding="utf-8") as fh:
            return bool(json.load(fh).get("ENABLE_ETA_R3_DROP_SHADOW", False))
    except Exception:
        return False


def predict_corrected_drop_if_enabled(*, bag_size, predicted_delivery_min, hour_warsaw,
                                      is_weekend, is_bundle, restaurant, courier_id):
    """Guarded wrapper: zwraca (corrected, resid) TYLKO gdy ENABLE_ETA_R3_DROP_SHADOW=true,
    inaczej (None, None). Pozwala konsumentowi (eta_calibration_logger) dopiąć ścieżkę B_drop
    jednym wywołaniem, bez własnego czytania flagi. Domyślnie OFF → zero nowych pól w logu."""
    if not drop_shadow_enabled():
        return (None, None)
    return predict_corrected_drop(
        bag_size=bag_size, predicted_delivery_min=predicted_delivery_min,
        hour_warsaw=hour_warsaw, is_weekend=is_weekend, is_bundle=is_bundle,
        restaurant=restaurant, courier_id=courier_id)
