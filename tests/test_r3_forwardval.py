#!/usr/bin/env python3
"""Testy harnessu forward-validation ETA R3 (tools/eta_r3_forward_val.py).

Pokrywa dwie własności wymagane przy odbiorze:
  (1) PARITY wykrywa sztucznie wstrzyknięty skew (przesunięty rest_freq / pool /
      bag_size) — i NIE flaguje, gdy rozkłady są identyczne.
  (2) MAE / median / p90 / p95 / błąd-ze-znakiem liczone poprawnie na ZNANYM wejściu
      (ręcznie policzone wartości referencyjne).

Plus sanity KS-testu własnej implementacji (zgodność ze scipy gdy dostępne) oraz
poprawność degradacji okien kroczących przy zbyt krótkim zakresie danych.

Czysto offline, bez I/O do prawdziwych logów (operuje na syntetycznych rekordach)."""
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "tools"))

import eta_r3_forward_val as H  # noqa: E402


def _has_scipy():
    try:
        import scipy  # noqa: F401
        return True
    except Exception:
        return False


# ───────────────────────── (2) metryki MAE/p95/signed na znanym wejściu ─────────────────────────
def test_error_stats_known_input():
    # err = pred - real: [+2, -4, +6, 0, -2]  → |err| = [2,4,6,0,2]
    preds = [12.0, 6.0, 16.0, 10.0, 8.0]
    reals = [10.0, 10.0, 10.0, 10.0, 10.0]
    s = H.error_stats(preds, reals)
    assert s["n"] == 5
    assert s["mae"] == pytest.approx((2 + 4 + 6 + 0 + 2) / 5)        # 2.8
    assert s["median_abs"] == pytest.approx(2.0)                      # sorted |e|=[0,2,2,4,6]
    # signed: [2,-4,6,0,-2] → median 0, mean 0.4
    assert s["median_signed"] == pytest.approx(0.0)
    assert s["mean_signed"] == pytest.approx(0.4)
    # under (err<0): -4,-2 → 2/5 ; over (err>0): 2,6 → 2/5
    assert s["frac_under"] == pytest.approx(2 / 5)
    assert s["frac_over"] == pytest.approx(2 / 5)


def test_percentile_linear_interp():
    vals = [0.0, 2.0, 2.0, 4.0, 6.0]   # już posortowane
    # nearest-rank/linear: p90 z 5 elementów → indeks 0.9*4=3.6 → 4 + (6-4)*0.6 = 5.2
    assert H._pct(vals, 90) == pytest.approx(5.2)
    assert H._pct(vals, 95) == pytest.approx(5.6)
    assert H._pct(vals, 0) == 0.0
    assert H._pct(vals, 100) == 6.0
    assert H._pct([], 95) is None
    assert H._pct([7.0], 95) == 7.0


def test_p95_improvement_direction():
    # baza ma gruby ogon, R3 go obcina → p95 R3 < p95 baza
    reals = [10.0] * 20
    base = [10.0] * 18 + [60.0, 70.0]           # 2 grube outliery
    r3 = [10.0] * 18 + [15.0, 16.0]             # korekta obcina ogon
    sb = H.error_stats(base, reals)
    sr = H.error_stats(r3, reals)
    assert sb["p95_abs"] > sr["p95_abs"]
    assert sb["mae"] > sr["mae"]


# ───────────────────────── KS-test sanity ─────────────────────────
def test_ks_identical_samples_no_skew():
    a = [1.0, 2.0, 3.0, 4.0, 5.0] * 4
    D, p = H.ks_2samp(a, list(a))
    assert D == pytest.approx(0.0)
    assert p == pytest.approx(1.0)


def test_ks_disjoint_samples_max_D():
    a = [0.0] * 50
    b = [1.0] * 50
    D, p = H.ks_2samp(a, b)
    assert D == pytest.approx(1.0)
    assert p < 0.01


@pytest.mark.skipif(not _has_scipy(), reason="scipy nieobecne")
def test_ks_matches_scipy():
    from scipy import stats
    import random
    random.seed(7)
    a = [random.gauss(0, 1) for _ in range(200)]
    b = [random.gauss(0.6, 1) for _ in range(180)]
    D_ours, p_ours = H.ks_2samp(a, b)
    r = stats.ks_2samp(a, b)
    assert D_ours == pytest.approx(r.statistic, abs=1e-9)
    # p asymptotyczne ≈ scipy (scipy używa tej samej asymptotyki dla większych n)
    assert p_ours == pytest.approx(r.pvalue, abs=0.05)


# ───────────────────────── (1) parity wykrywa wstrzyknięty skew ─────────────────────────
def _rec(*, oid, bag, pdm, real, hour, weekend, bundle, rest, cid):
    return {
        "oid": str(oid), "bag_size": bag, "predicted_delivery_min": pdm,
        "real_delivery_min": real, "hour_warsaw": hour, "is_weekend": weekend,
        "is_bundle": bundle, "restaurant": rest, "real_courier_id": cid,
        "best_courier_id": cid, "was_czasowka": False,
        "logged_at": "2026-06-19T12:00:00+02:00",
    }


def _mk_set(n, *, rest, bag=2, cid="100", start_oid=0):
    return [_rec(oid=start_oid + i, bag=bag, pdm=20.0, real=22.0, hour=12,
                 weekend=False, bundle=True, rest=rest, cid=cid) for i in range(n)]


def test_parity_no_skew_when_distributions_match():
    cid2tier = {"100": "std"}
    rest_freq = {"resta": 100, "restb": 100}
    pool = {}
    train = _mk_set(60, rest="resta", bag=2, start_oid=0)
    serve = _mk_set(60, rest="resta", bag=2, start_oid=1000)
    rows = H.parity_report(train, serve, cid2tier=cid2tier, rest_freq=rest_freq, pool=pool)
    assert not any(r["skew"] for r in rows), \
        "identyczne rozkłady NIE powinny dawać skew: " + \
        str([(r["feature"], r["p"]) for r in rows if r["skew"]])


def test_parity_detects_rest_freq_skew():
    # train: restauracja o freq=100; serve: restauracja o freq=900 → rest_freq przesunięty
    cid2tier = {"100": "std"}
    rest_freq = {"common": 100, "rare": 900}
    pool = {}
    train = _mk_set(60, rest="common", start_oid=0)
    serve = _mk_set(60, rest="rare", start_oid=1000)
    rows = {r["feature"]: r for r in H.parity_report(
        train, serve, cid2tier=cid2tier, rest_freq=rest_freq, pool=pool)}
    assert rows["rest_freq"]["skew"], f"rest_freq skew nie wykryty: p={rows['rest_freq']['p']}"
    assert rows["rest_freq"]["train_mean"] == pytest.approx(100.0)
    assert rows["rest_freq"]["serve_mean"] == pytest.approx(900.0)


def test_parity_detects_pool_feasible_skew():
    # train: brak w backfill → pool=-1; serve: obecne z realnymi wartościami → skew
    cid2tier = {"100": "std"}
    rest_freq = {"resta": 100}
    train = _mk_set(60, rest="resta", start_oid=0)            # oids 0..59
    serve = _mk_set(60, rest="resta", start_oid=1000)         # oids 1000..1059
    pool = {str(1000 + i): 5 for i in range(60)}              # tylko serve w backfill
    rows = {r["feature"]: r for r in H.parity_report(
        train, serve, cid2tier=cid2tier, rest_freq=rest_freq, pool=pool)}
    assert rows["pool_feasible"]["skew"], "pool_feasible skew nie wykryty"
    assert rows["pool_feasible"]["train_mean"] == pytest.approx(-1.0)
    assert rows["pool_feasible"]["serve_mean"] == pytest.approx(5.0)


def test_parity_detects_bag_size_skew():
    cid2tier = {"100": "std"}
    rest_freq = {"resta": 100}
    pool = {}
    train = _mk_set(60, rest="resta", bag=1, start_oid=0)
    serve = _mk_set(60, rest="resta", bag=5, start_oid=1000)
    rows = {r["feature"]: r for r in H.parity_report(
        train, serve, cid2tier=cid2tier, rest_freq=rest_freq, pool=pool)}
    assert rows["bag_size"]["skew"]
    assert rows["bag_size"]["train_mean"] == pytest.approx(1.0)
    assert rows["bag_size"]["serve_mean"] == pytest.approx(5.0)


def test_parity_tier_ord_is_onehot():
    # tier_ord rozbity na one-hot: po jednym wpisie na poziom TIER_ORD
    cid2tier = {"100": "std", "200": "gold"}
    rest_freq = {"resta": 100}
    pool = {}
    train = _mk_set(40, rest="resta", cid="100", start_oid=0)       # std → tier_ord=2
    serve = _mk_set(40, rest="resta", cid="200", start_oid=1000)    # gold → tier_ord=4
    rows = {r["feature"]: r for r in H.parity_report(
        train, serve, cid2tier=cid2tier, rest_freq=rest_freq, pool=pool)}
    # są wpisy one-hot dla każdego poziomu
    for lvl in sorted(H.TIER_ORD.values()):
        assert f"tier_ord==%d" % lvl in rows
    # std (poziom 2) i gold (poziom 4) muszą wykazać skew (train all std, serve all gold)
    assert rows["tier_ord==2"]["skew"]
    assert rows["tier_ord==4"]["skew"]


# ───────────────────────── okna kroczące — degradacja ─────────────────────────
def test_rolling_windows_degrade_when_span_short():
    # dane z 2 dni, okno 7d → degradacja: 1 okno (całość, degraded) + okna dzienne
    recs = []
    for i in range(20):
        r = _rec(oid=i, bag=2, pdm=20, real=22, hour=12, weekend=False,
                 bundle=True, rest="resta", cid="100")
        r["logged_at"] = "2026-06-19T12:00:00+02:00"
        r["eta_r3_corrected_delivery_min"] = 21.0
        recs.append(r)
    for i in range(20, 40):
        r = _rec(oid=i, bag=2, pdm=20, real=22, hour=12, weekend=False,
                 bundle=True, rest="resta", cid="100")
        r["logged_at"] = "2026-06-20T12:00:00+02:00"
        r["eta_r3_corrected_delivery_min"] = 21.0
        recs.append(r)
    ws = H.rolling_windows(recs, window_days=7)
    assert all(w["degraded"] for w in ws)
    # 1 okno-całość + 2 dzienne
    assert sum(1 for w in ws if w.get("daily")) == 2
    assert any(w["start"] == "2026-06-19" and w["end"] == "2026-06-20" for w in ws)


def test_rolling_windows_full_when_span_enough():
    recs = []
    from datetime import date, timedelta
    d0 = date(2026, 6, 1)
    for k in range(8):                      # 8 dni → mieści okno 7d (2 okna krok=1)
        day = (d0 + timedelta(days=k)).isoformat()
        for i in range(15):
            r = _rec(oid=k * 100 + i, bag=2, pdm=20, real=22, hour=12, weekend=False,
                     bundle=True, rest="resta", cid="100")
            r["logged_at"] = day + "T12:00:00+02:00"
            r["eta_r3_corrected_delivery_min"] = 21.0
            recs.append(r)
    ws = H.rolling_windows(recs, window_days=7)
    assert ws and not any(w["degraded"] for w in ws)
    assert all((w["end"] != w["start"]) for w in ws)   # pełne 7d okna


def test_window_metrics_improvement_and_insufficient():
    # n < min_n → insufficient
    small = {"start": "d", "end": "d", "records": [
        {**_rec(oid=1, bag=2, pdm=20, real=22, hour=12, weekend=False, bundle=True,
                rest="r", cid="1"), "eta_r3_corrected_delivery_min": 21.0}]}
    m = H.window_metrics(small, min_n=10)
    assert m["insufficient"]
    # dość rekordów, R3 bliżej prawdy niż baza → improvement>0
    recs = []
    for i in range(20):
        r = _rec(oid=i, bag=2, pdm=15.0, real=25.0, hour=12, weekend=False,
                 bundle=True, rest="r", cid="1")           # baza |err|=10
        r["eta_r3_corrected_delivery_min"] = 24.0          # R3 |err|=1
        recs.append(r)
    m2 = H.window_metrics({"start": "d", "end": "d", "records": recs}, min_n=10)
    assert not m2["insufficient"]
    assert m2["base"]["mae"] == pytest.approx(10.0)
    assert m2["r3"]["mae"] == pytest.approx(1.0)
    assert m2["improvement_pct"] == pytest.approx(90.0)
    assert m2["meets_target"]


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-q"]))
