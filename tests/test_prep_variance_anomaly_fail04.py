"""FAIL-04: prep-variance anomaly detection (shadow-first).

Pokrywa:
 - restaurant_prep_variance() (parsowanie meta, dopasowanie nazwy, brak danych)
 - detect_prep_variance_anomaly() (prog, low_confidence, not-high, edge cases)
 - _detect_and_set_prep_variance_anomaly() hook (flaga OFF/ON, fail-soft)
 - integracja z realnym restaurant_meta.json (smoke)
 - INVARIANT: NIE dotyka pickup_ready_at (F1.8g landmine)

NIE wysyla nic do Telegrama, NIE restartuje uslug.
"""
import sys

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import dispatch_pipeline as dp  # noqa: E402
from dispatch_v2 import common as C  # noqa: E402

META = {
    "restaurants": {
        "Aztek Tex-Mex": {
            "prep_variance_min": {"median": 29.0, "p90": 56.0, "sample_n": 40},
            "flags": {"prep_variance_high": True, "low_confidence": False,
                      "chronically_late": True},
        },
        "Bar Merino": {
            "prep_variance_min": {"median": 19.0, "p90": 32.0, "sample_n": 12},
            "flags": {"prep_variance_high": True, "low_confidence": False},
        },
        "LowConf": {
            "prep_variance_min": {"median": 30.0},
            "flags": {"prep_variance_high": True, "low_confidence": True},
        },
        "Normal": {
            "prep_variance_min": {"median": 8.0},
            "flags": {"prep_variance_high": False},
        },
        "NoMedian": {
            "prep_variance_min": {"p90": 40.0},
            "flags": {"prep_variance_high": True},
        },
    }
}


def _result(restaurant="Aztek Tex-Mex", oid="1"):
    return dp.PipelineResult(
        order_id=oid, verdict="PROPOSE", reason="t", best=None,
        candidates=[], pickup_ready_at=None, restaurant=restaurant,
    )


# ─── restaurant_prep_variance ───
def test_prep_variance_high_restaurant():
    pv = dp.restaurant_prep_variance("Aztek Tex-Mex", meta=META)
    assert pv["median"] == 29.0 and pv["high"] is True and pv["low_confidence"] is False


def test_prep_variance_lowercase_match():
    assert dp.restaurant_prep_variance("aztek tex-mex", meta=META) is not None


def test_prep_variance_unknown_returns_none():
    assert dp.restaurant_prep_variance("Nieznana XYZ", meta=META) is None


def test_prep_variance_none_name():
    assert dp.restaurant_prep_variance(None, meta=META) is None


def test_prep_variance_missing_median():
    assert dp.restaurant_prep_variance("NoMedian", meta=META) is None


# ─── detect_prep_variance_anomaly ───
def test_anomaly_fires_high_gap():
    a = dp.detect_prep_variance_anomaly("Aztek Tex-Mex", 10, meta=META)
    assert a and a["gap_min"] == 19.0 and a["empirical_median_min"] == 29.0
    assert a["threshold_min"] == float(C.RESTAURANT_PREP_VARIANCE_HARD_MIN)


def test_anomaly_below_threshold_none():
    # gap 9 < 15
    assert dp.detect_prep_variance_anomaly("Aztek Tex-Mex", 20, meta=META) is None


def test_anomaly_exactly_at_threshold_fires():
    # Bar Merino median 19, declared 4 -> gap 15 == HARD_MIN -> fires (>=)
    assert dp.detect_prep_variance_anomaly("Bar Merino", 4, meta=META) is not None


def test_anomaly_one_below_threshold_none():
    # Bar Merino median 19, declared 5 -> gap 14 < 15 -> None
    assert dp.detect_prep_variance_anomaly("Bar Merino", 5, meta=META) is None


def test_anomaly_low_confidence_skipped():
    assert dp.detect_prep_variance_anomaly("LowConf", 0, meta=META) is None


def test_anomaly_not_high_skipped():
    assert dp.detect_prep_variance_anomaly("Normal", 0, meta=META) is None


def test_anomaly_declared_none_treated_zero():
    assert dp.detect_prep_variance_anomaly("Aztek Tex-Mex", None, meta=META) is not None


# ─── hook: flaga OFF/ON ───
def test_hook_flag_off_sets_nothing(monkeypatch):
    monkeypatch.setattr(dp.C, "flag", lambda name, default=False: False)
    r = _result()
    dp._detect_and_set_prep_variance_anomaly(r, {"restaurant": "Aztek Tex-Mex",
                                                 "prep_minutes": 5})
    assert r.prep_variance_anomaly is None


def test_hook_flag_on_sets_anomaly(monkeypatch):
    monkeypatch.setattr(dp.C, "flag", lambda name, default=False: True)
    monkeypatch.setattr(dp, "detect_prep_variance_anomaly",
                        lambda rest, declared, meta=None: {"restaurant": rest,
                                                           "gap_min": 20.0})
    r = _result()
    dp._detect_and_set_prep_variance_anomaly(r, {"restaurant": "Aztek Tex-Mex",
                                                 "prep_minutes": 5})
    assert r.prep_variance_anomaly == {"restaurant": "Aztek Tex-Mex", "gap_min": 20.0}


def test_hook_flag_on_no_anomaly_stays_none(monkeypatch):
    monkeypatch.setattr(dp.C, "flag", lambda name, default=False: True)
    monkeypatch.setattr(dp, "detect_prep_variance_anomaly",
                        lambda rest, declared, meta=None: None)
    r = _result(restaurant="Normal")
    dp._detect_and_set_prep_variance_anomaly(r, {"restaurant": "Normal",
                                                 "prep_minutes": 30})
    assert r.prep_variance_anomaly is None


def test_hook_never_raises_on_bad_input(monkeypatch):
    monkeypatch.setattr(dp.C, "flag", lambda name, default=False: True)
    r = _result()
    # order_event=None nie moze wywrocic hooka
    dp._detect_and_set_prep_variance_anomaly(r, None)  # brak wyjatku = pass


# ─── INVARIANT F1.8g: hook NIE dotyka pickup_ready_at ───
def test_hook_does_not_touch_pickup_ready_at(monkeypatch):
    monkeypatch.setattr(dp.C, "flag", lambda name, default=False: True)
    monkeypatch.setattr(dp, "detect_prep_variance_anomaly",
                        lambda rest, declared, meta=None: {"gap_min": 99})
    r = _result()
    before = r.pickup_ready_at
    dp._detect_and_set_prep_variance_anomaly(r, {"restaurant": "Aztek Tex-Mex",
                                                 "prep_minutes": 1})
    assert r.pickup_ready_at is before  # bez zmiany czasu (F1.8g)


# ─── integracja: realny restaurant_meta.json ───
def test_real_meta_loads_and_has_high_variance():
    meta = dp._load_restaurant_meta_cached()
    assert meta is not None and "restaurants" in meta
    # co najmniej jedna realna restauracja prep_variance_high z mediana
    found = any(
        dp.restaurant_prep_variance(name) and dp.restaurant_prep_variance(name)["high"]
        for name in list(meta["restaurants"].keys())
    )
    assert found
