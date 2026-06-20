"""Testy DRUGIEJ ścieżki shadow ETA R3 — wariant B_drop (eta_residual_infer, 2026-06-20, TOR1).

Gwarancje:
  (1) build_features_drop = 8 cech w kolejności [bag,pdm,hour,is_weekend,is_bundle,peak,tier,rest]
      (= v1 build_features bez ostatniej pool_feasible) — parytet kodowania peak/tier/braki=-1.
  (2) predict_corrected_drop: corrected == base + residual, deterministyczny, brak base → (None,None).
  (3) FLAGA OFF (ENABLE_ETA_R3_DROP_SHADOW absent/false): drop_shadow_enabled()==False oraz
      predict_corrected_drop_if_enabled → (None,None) — zero wpływu na zachowanie domyślne.
  (4) FLAGA ON (monkeypatch drop_shadow_enabled): guarded wrapper liczy i == bezpośredni
      predict_corrected_drop. Ścieżka v1 (predict_corrected) NIETKNIĘTA niezależnie od flagi.

Wariant B_drop NIE używa pool_feasible (usuwa źródłowo train/serve skew). Fail-soft wszędzie.
"""
import pytest

import dispatch_v2.eta_residual_infer as R


# ───────────────────────── (1) build_features_drop — 8 cech, parytet kodowania ─────────────────────────
def test_drop_feature_vector_shape_and_order():
    R.is_available_drop()  # załaduj rest_freq/tiers wariantu drop
    f = R.build_features_drop(bag_size=3, predicted_delivery_min=22.0, hour_warsaw=12,
                              is_weekend=True, is_bundle=True, restaurant="Baanko",
                              courier_id="999999")
    assert len(f) == 8                 # bez pool_feasible (v1 ma 9)
    assert f[0] == 3                   # bag_size
    assert f[1] == 22.0                # pred_delivery_min
    assert f[2] == 12                  # hour
    assert f[3] == 1                   # is_weekend
    assert f[4] == 1                   # is_bundle
    assert f[5] == 1                   # peak (12 in 11-14)
    assert f[6] == 2                   # tier_ord: nieznany cid → default std=2
    assert f[7] >= 0                   # rest_freq (>=0)


def test_drop_is_v1_features_without_pool():
    """DROP[0:8] musi być identyczne z v1 build_features[0:8] (te same cechy, ta sama kolejność)."""
    kw = dict(bag_size=2, predicted_delivery_min=18.0, hour_warsaw=18, is_weekend=False,
              is_bundle=True, restaurant="Baanko", courier_id="21")
    full = R.build_features(pool_feasible=4, **kw)         # 9 cech
    drop = R.build_features_drop(**kw)                     # 8 cech
    assert len(full) == 9 and len(drop) == 8
    assert drop == full[:8]            # drop = v1 bez pool_feasible (ostatniej)


def test_drop_missing_values_minus1_and_peak():
    f = R.build_features_drop(bag_size=None, predicted_delivery_min=20, hour_warsaw=None,
                              is_weekend=False, is_bundle=False, restaurant=None, courier_id=None)
    assert f[0] == -1                  # bag_size None → -1
    assert f[2] == -1                  # hour None → -1
    assert f[5] == 0                   # hour None → peak 0


# ───────────────────────── (2) predict_corrected_drop ─────────────────────────
@pytest.mark.skipif(not R.is_available_drop(), reason="model eta_residual_v2_drop niedostępny")
def test_drop_corrected_equals_base_plus_residual():
    corrected, resid = R.predict_corrected_drop(
        bag_size=3, predicted_delivery_min=25.0, hour_warsaw=13, is_weekend=False,
        is_bundle=True, restaurant="Baanko", courier_id="21")
    assert corrected is not None and resid is not None
    assert abs(corrected - (25.0 + resid)) < 0.011


@pytest.mark.skipif(not R.is_available_drop(), reason="model eta_residual_v2_drop niedostępny")
def test_drop_deterministic():
    kw = dict(bag_size=2, predicted_delivery_min=18.0, hour_warsaw=12, is_weekend=False,
              is_bundle=False, restaurant="x", courier_id=None)
    assert R.predict_corrected_drop(**kw) == R.predict_corrected_drop(**kw)


def test_drop_no_base_returns_none():
    assert R.predict_corrected_drop(
        bag_size=2, predicted_delivery_min=None, hour_warsaw=12, is_weekend=False,
        is_bundle=False, restaurant="x", courier_id=None) == (None, None)


# ───────────────────────── (3) FLAGA OFF = zero wpływu ─────────────────────────
def test_drop_flag_off_guarded_returns_none(monkeypatch):
    monkeypatch.setattr(R, "drop_shadow_enabled", lambda: False)
    out = R.predict_corrected_drop_if_enabled(
        bag_size=3, predicted_delivery_min=25.0, hour_warsaw=13, is_weekend=False,
        is_bundle=True, restaurant="Baanko", courier_id="21")
    assert out == (None, None)


def test_drop_flag_default_off_when_absent():
    """Bez wpisu ENABLE_ETA_R3_DROP_SHADOW w flags.json (stan obecny) flaga = OFF."""
    # czyta realny flags.json — wpis nie istnieje → False (fail-soft default)
    assert R.drop_shadow_enabled() is False


# ───────────────────────── (4) FLAGA ON = liczy, v1 nietknięte ─────────────────────────
@pytest.mark.skipif(not R.is_available_drop(), reason="model eta_residual_v2_drop niedostępny")
def test_drop_flag_on_guarded_matches_direct(monkeypatch):
    monkeypatch.setattr(R, "drop_shadow_enabled", lambda: True)
    kw = dict(bag_size=3, predicted_delivery_min=25.0, hour_warsaw=13, is_weekend=False,
              is_bundle=True, restaurant="Baanko", courier_id="21")
    guarded = R.predict_corrected_drop_if_enabled(**kw)
    direct = R.predict_corrected_drop(**kw)
    assert guarded == direct
    assert guarded[0] is not None


@pytest.mark.skipif(not (R.is_available() and R.is_available_drop()),
                    reason="oba modele wymagane")
def test_v1_path_unaffected_by_drop(monkeypatch):
    """Ścieżka v1 daje ten sam wynik niezależnie od flagi DROP (rozłączne singletony)."""
    kw9 = dict(bag_size=3, predicted_delivery_min=25.0, hour_warsaw=13, is_weekend=False,
               is_bundle=True, restaurant="Baanko", courier_id="21", pool_feasible=3)
    before = R.predict_corrected(**kw9)
    monkeypatch.setattr(R, "drop_shadow_enabled", lambda: True)
    after = R.predict_corrected(**kw9)
    assert before == after
    # v1 (9 cech, z pool) i drop (8 cech, bez pool) to różne modele → zwykle różne resid
    kw8 = {k: v for k, v in kw9.items() if k != "pool_feasible"}
    assert R.predict_corrected_drop(**kw8)[1] is not None


if __name__ == "__main__":
    import sys
    sys.exit(pytest.main([__file__, "-q"]))
