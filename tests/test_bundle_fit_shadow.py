"""BUNDLE-06 Faza 1 + BUNDLE-03 (Front D, 2026-06-12): bundle_fit + FIX_C addytywna.

Testuje logikę składników na poziomie czystych formuł (mirror obliczeń z
dispatch_pipeline — wartości liczone na słownikach metryk, bez pełnego
pipeline'u) oraz kontrakt: delta zawsze liczona, flagi OFF = zero wpływu
na score (lekcja #186).
"""
import pytest

from dispatch_v2 import common as C


def _bundle_fit(metrics, bag_n, flags=None):
    """Mirror formuły bundle_fit z dispatch_pipeline (testowalna kopia 1:1)."""
    fl = flags or {}
    if bag_n < 1:
        return None, 0.0
    bf = 0.0
    cos = metrics.get("r1_new_drop_cosine")
    thermal = metrics.get("objm_max_thermal_age_min")
    span = metrics.get("r8_pickup_span_min")
    if isinstance(cos, (int, float)):
        bf += float(fl.get("BUNDLE_FIT_W_COS", C.BUNDLE_FIT_W_COS)) * float(cos)
    if isinstance(thermal, (int, float)):
        bf -= float(fl.get("BUNDLE_FIT_THERMAL_PER_MIN", C.BUNDLE_FIT_THERMAL_PER_MIN)) * max(
            0.0, float(thermal) - float(fl.get("BUNDLE_FIT_THERMAL_FREE_MIN",
                                               C.BUNDLE_FIT_THERMAL_FREE_MIN)))
    if isinstance(span, (int, float)):
        bf -= float(fl.get("BUNDLE_FIT_SPAN_PER_MIN", C.BUNDLE_FIT_SPAN_PER_MIN)) * max(
            0.0, float(span) - float(fl.get("BUNDLE_FIT_SPAN_FREE_MIN",
                                            C.BUNDLE_FIT_SPAN_FREE_MIN)))
    return round(bf, 2), round(bf, 2)


def _fix_c_additive(metrics, bag_n, spread, flags=None):
    """Mirror formuły BUNDLE-03."""
    fl = flags or {}
    if bag_n < 1 or spread is None:
        return 0.0
    pen = float(fl.get("FIX_C_ADDITIVE_PEN_PER_KM", C.FIX_C_ADDITIVE_PEN_PER_KM))
    trig = float(fl.get("FIX_C_ADDITIVE_COS_TRIGGER", C.FIX_C_ADDITIVE_COS_TRIGGER))
    cos = metrics.get("r1_new_drop_cosine")
    over = max(0.0, spread - C.BUNDLE_MAX_DELIV_SPREAD_KM)
    if isinstance(cos, (int, float)) and cos < trig:
        return round(-pen * spread, 2)
    if over > 0.0:
        return round(-pen * over, 2)
    return 0.0


# ───────────────────────────── bundle_fit ─────────────────────────────

def test_bundle_fit_solo_is_none():
    fit, delta = _bundle_fit({"r1_new_drop_cosine": 0.9}, bag_n=0)
    assert fit is None and delta == 0.0


def test_bundle_fit_coherent_bag_positive():
    m = {"r1_new_drop_cosine": 0.9, "objm_max_thermal_age_min": 18.0,
         "r8_pickup_span_min": 4.0}
    fit, _ = _bundle_fit(m, bag_n=2)
    assert fit == pytest.approx(12.0 * 0.9)  # thermal/span pod free → 0 kar


def test_bundle_fit_scattered_bag_negative():
    m = {"r1_new_drop_cosine": -0.8, "objm_max_thermal_age_min": 33.0,
         "r8_pickup_span_min": 16.5}
    fit, _ = _bundle_fit(m, bag_n=2)
    # -9.6 (cos) - 1.5*8 (thermal nad 25) - 1.0*8.5 (span nad 8) = -30.1
    assert fit == pytest.approx(-9.6 - 12.0 - 8.5)


def test_bundle_fit_missing_signals_are_neutral():
    fit, _ = _bundle_fit({}, bag_n=1)
    assert fit == 0.0  # None-sygnały nie karzą (brak danych ≠ zły worek)


def test_bundle_fit_weights_from_flags():
    m = {"r1_new_drop_cosine": 1.0}
    fit, _ = _bundle_fit(m, bag_n=1, flags={"BUNDLE_FIT_W_COS": 50.0})
    assert fit == pytest.approx(50.0)


# ─────────────────────────── FIX_C addytywna ───────────────────────────

def test_fix_c_additive_over_cap():
    pen = _fix_c_additive({}, bag_n=2, spread=C.BUNDLE_MAX_DELIV_SPREAD_KM + 3.0)
    assert pen == pytest.approx(-3.0 * 3.0)


def test_fix_c_additive_cross_direction_full_spread():
    # spread POD capem, ale przeciw-kierunkowy → kara od PEŁNEGO spreadu
    m = {"r1_new_drop_cosine": -0.6}
    pen = _fix_c_additive(m, bag_n=2, spread=5.0)
    assert pen == pytest.approx(-3.0 * 5.0)


def test_fix_c_additive_no_trigger_no_pen():
    m = {"r1_new_drop_cosine": 0.4}
    assert _fix_c_additive(m, bag_n=2, spread=5.0) == 0.0
    assert _fix_c_additive(m, bag_n=0, spread=20.0) == 0.0


# ───────────────── kontrakt lekcji #186 (flagi w kanonie) ─────────────────

def test_flags_in_etap4_canon_and_numeric_overrides():
    assert "ENABLE_BUNDLE_VALUE_SCORING" in C.ETAP4_DECISION_FLAGS
    assert "ENABLE_FIX_C_ADDITIVE_PENALTY" in C.ETAP4_DECISION_FLAGS
    for k in ("BUNDLE_FIT_W_COS", "BUNDLE_FIT_THERMAL_PER_MIN",
              "BUNDLE_FIT_SPAN_PER_MIN", "FIX_C_ADDITIVE_PEN_PER_KM",
              "FIX_C_ADDITIVE_COS_TRIGGER"):
        assert k in C.FLAGS_JSON_NUMERIC_OVERRIDES


def test_flags_default_off():
    # decision_flag: brak w flags.json (conftest wycina) → stała modułu → False
    assert C.decision_flag("ENABLE_BUNDLE_VALUE_SCORING") is False
    assert C.decision_flag("ENABLE_FIX_C_ADDITIVE_PENALTY") is False


def test_serializer_has_fields():
    import inspect
    from dispatch_v2 import shadow_dispatcher as sd
    # L1.1 (2026-07-01): prefix-allowlist zastapiona deny-lista — bundle_fit_*
    # dociera do ledgera behawioralnie (nie przez literal prefiksu w zrodle).
    base: dict = {}
    sd._propagate_prefixed_metrics(base, {"bundle_fit_shadow": True})
    assert base.get("bundle_fit_shadow") is True
    src = inspect.getsource(sd)
    assert src.count('"fix_c_additive_pen_shadow"') >= 2  # LOCATION A + B
