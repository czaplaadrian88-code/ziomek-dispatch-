#!/usr/bin/env python3
"""Testy narzędzi naprawy skew pool_feasible (tools/eta_r3_fix_skew.py + eta_r3_compare_variants.py).

Pokrywa:
  (1) feats() — rekonstrukcja cech: 9 cech (full) vs 8 cech (drop) — kolejność, wartości,
      pool_feasible z backfilla / sentinel −1, tier z real||best_courier_id.
  (2) recompute_corrected — corrected = base + booster.predict(feats); kształt 8 vs 9 cech
      wnioskowany z features.json (drop → 8); spójność z bezpośrednią predykcją.
  (3) ks_parity_for — wykrywa skew pool_feasible w wariancie FULL, NIE liczy go w DROP
      (cecha usunięta), i potrafi wykryć czysty skew na innej cesze.

Bez I/O do prawdziwych logów (syntetyczne rekordy + maleńki booster trenowany w teście).
Wymaga lightgbm (skip gdy brak)."""
import os
import sys

import numpy as np
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import eta_r3_fix_skew as FIX  # noqa: E402


def _has_lgbm():
    try:
        import lightgbm  # noqa: F401
        return True
    except Exception:
        return False


def _rec(*, oid, bag=2, pdm=20.0, real=22.0, hour=12, weekend=False, bundle=True,
         rest="resta", cid="100"):
    return {"oid": str(oid), "bag_size": bag, "predicted_delivery_min": pdm,
            "real_delivery_min": real, "hour_warsaw": hour, "is_weekend": weekend,
            "is_bundle": bundle, "restaurant": rest, "real_courier_id": cid,
            "best_courier_id": cid, "was_czasowka": False,
            "logged_at": "2026-06-19T12:00:00+02:00"}


# ───────────────────────── (1) feats() ─────────────────────────
def test_feats_full_9_features_order_and_values():
    cid2tier = {"100": "gold"}      # gold → tier_ord 4
    restcnt = {"resta": 77}
    pool = {"5": 6}
    r = _rec(oid=5, bag=3, pdm=25.0, hour=18, weekend=True, bundle=True, rest="resta", cid="100")
    fv = FIX.feats(r, cid2tier=cid2tier, restcnt=restcnt, pool=pool, drop_pool=False)
    # [bag, pdm, hour, is_weekend, is_bundle, peak(18→1), tier_ord(gold=4), rest_freq, pool]
    assert fv == [3, 25.0, 18, 1, 1, 1, 4, 77, 6]
    assert len(fv) == 9


def test_feats_drop_omits_pool_feasible():
    cid2tier = {"100": "std"}
    restcnt = {"resta": 50}
    pool = {"5": 9}
    r = _rec(oid=5, bag=2, pdm=20.0, hour=10, weekend=False, rest="resta", cid="100")
    full = FIX.feats(r, cid2tier=cid2tier, restcnt=restcnt, pool=pool, drop_pool=False)
    drop = FIX.feats(r, cid2tier=cid2tier, restcnt=restcnt, pool=pool, drop_pool=True)
    assert len(full) == 9 and len(drop) == 8
    assert drop == full[:8]            # drop = full bez ostatniej (pool_feasible)
    assert full[-1] == 9               # pool z backfilla


def test_feats_pool_sentinel_when_missing_from_backfill():
    cid2tier = {"100": "std"}
    restcnt = {"resta": 1}
    r = _rec(oid=999, rest="resta", cid="100")
    fv = FIX.feats(r, cid2tier=cid2tier, restcnt=restcnt, pool={}, drop_pool=False)
    assert fv[-1] == -1                # brak w backfillu → sentinel −1 (jak trening)


def test_feats_tier_from_real_then_best_courier():
    cid2tier = {"REAL": "slow", "BEST": "gold"}   # slow=1, gold=4
    restcnt = {"resta": 1}
    r = _rec(oid=1, rest="resta")
    r["real_courier_id"] = "REAL"
    r["best_courier_id"] = "BEST"
    fv = FIX.feats(r, cid2tier=cid2tier, restcnt=restcnt, pool={}, drop_pool=False)
    assert fv[6] == 1                  # real_courier_id ma priorytet → slow=1
    # gdy real brak → fallback best
    r["real_courier_id"] = None
    fv2 = FIX.feats(r, cid2tier=cid2tier, restcnt=restcnt, pool={}, drop_pool=False)
    assert fv2[6] == 4                 # best → gold=4


def test_feats_peak_window():
    cid2tier = {}
    restcnt = {}
    for hr, exp in [(11, 1), (13, 1), (14, 0), (17, 1), (19, 1), (20, 0), (9, 0)]:
        r = _rec(oid=1, hour=hr, cid="x")
        fv = FIX.feats(r, cid2tier=cid2tier, restcnt=restcnt, pool={}, drop_pool=True)
        assert fv[5] == exp, f"hour={hr} peak expected {exp}"


# ───────────────────────── (2) recompute_corrected ─────────────────────────
@pytest.mark.skipif(not _has_lgbm(), reason="lightgbm nieobecne")
def test_recompute_corrected_matches_direct_predict(tmp_path):
    import lightgbm as lgb
    import json
    import eta_r3_compare_variants as CMP
    # maleńki booster na 8 cechach (drop)
    rng = np.random.RandomState(0)
    X = rng.rand(200, 8)
    y = rng.rand(200)
    m = lgb.LGBMRegressor(n_estimators=20, num_leaves=7, random_state=0, verbose=-1, n_jobs=1)
    m.fit(X, y)
    mdir = tmp_path / "model_drop"
    mdir.mkdir()
    m.booster_.save_model(str(mdir / "model.txt"))
    json.dump(FIX.FN_DROP, open(mdir / "features.json", "w"))
    # rekord: corrected = base + predict(feats)
    cid2tier = {"100": "std"}
    restcnt = {"resta": 5}
    recs = [_rec(oid=7, pdm=20.0, rest="resta", cid="100")]
    out = CMP.recompute_corrected(recs, str(mdir), cid2tier=cid2tier, restcnt=restcnt, pool={})
    fv = FIX.feats(recs[0], cid2tier=cid2tier, restcnt=restcnt, pool={}, drop_pool=True)
    booster = lgb.Booster(model_file=str(mdir / "model.txt"))
    expect = round(20.0 + float(booster.predict(np.array([fv], dtype=float))[0]), 2)
    assert out["7"] == pytest.approx(expect)


@pytest.mark.skipif(not _has_lgbm(), reason="lightgbm nieobecne")
def test_recompute_infers_feature_count_from_features_json(tmp_path):
    import lightgbm as lgb
    import json
    import eta_r3_compare_variants as CMP
    rng = np.random.RandomState(1)
    # model na 9 cechach (full)
    m = lgb.LGBMRegressor(n_estimators=10, num_leaves=5, random_state=1, verbose=-1, n_jobs=1)
    m.fit(rng.rand(120, 9), rng.rand(120))
    mdir = tmp_path / "model_full"
    mdir.mkdir()
    m.booster_.save_model(str(mdir / "model.txt"))
    json.dump(FIX.FN_FULL, open(mdir / "features.json", "w"))
    recs = [_rec(oid=3, pdm=15.0, rest="resta", cid="100")]
    # nie rzuca mimo 9 cech (drop_pool wnioskowany False bo pool_feasible w features.json)
    out = CMP.recompute_corrected(recs, str(mdir), cid2tier={"100": "std"},
                                  restcnt={"resta": 1}, pool={"3": 4})
    assert "3" in out and isinstance(out["3"], float)


# ───────────────────────── (3) ks_parity_for ─────────────────────────
def test_parity_detects_pool_skew_in_full_variant():
    import eta_r3_compare_variants as CMP
    cid2tier = {"100": "std"}
    restcnt = {"resta": 10}
    train = [_rec(oid=i, rest="resta", cid="100") for i in range(60)]        # brak w backfillu → pool=−1
    serve = [_rec(oid=1000 + i, rest="resta", cid="100") for i in range(60)]
    pool = {str(1000 + i): 5 for i in range(60)}                              # tylko serve realny
    rows = {r["feature"]: r for r in CMP.ks_parity_for(
        FIX.FN_FULL, train, serve, cid2tier=cid2tier, restcnt=restcnt, pool=pool)}
    assert "pool_feasible" in rows and rows["pool_feasible"]["skew"]
    assert rows["pool_feasible"]["train_mean"] == pytest.approx(-1.0)
    assert rows["pool_feasible"]["serve_mean"] == pytest.approx(5.0)


def test_parity_drop_variant_has_no_pool_feature():
    import eta_r3_compare_variants as CMP
    cid2tier = {"100": "std"}
    restcnt = {"resta": 10}
    train = [_rec(oid=i, rest="resta", cid="100") for i in range(40)]
    serve = [_rec(oid=1000 + i, rest="resta", cid="100") for i in range(40)]
    pool = {str(1000 + i): 5 for i in range(40)}
    feats = {r["feature"] for r in CMP.ks_parity_for(
        FIX.FN_DROP, train, serve, cid2tier=cid2tier, restcnt=restcnt, pool=pool)}
    assert "pool_feasible" not in feats           # cecha usunięta → parity jej nie mierzy
    assert "rest_freq" in feats                   # pozostałe są


def test_parity_no_skew_when_pool_matches():
    import eta_r3_compare_variants as CMP
    cid2tier = {"100": "std"}
    restcnt = {"resta": 10}
    train = [_rec(oid=i, rest="resta", cid="100") for i in range(60)]
    serve = [_rec(oid=1000 + i, rest="resta", cid="100") for i in range(60)]
    # pool identyczny po obu stronach → brak skew
    pool = {str(i): 4 for i in range(60)}
    pool.update({str(1000 + i): 4 for i in range(60)})
    rows = {r["feature"]: r for r in CMP.ks_parity_for(
        FIX.FN_FULL, train, serve, cid2tier=cid2tier, restcnt=restcnt, pool=pool)}
    assert not rows["pool_feasible"]["skew"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
