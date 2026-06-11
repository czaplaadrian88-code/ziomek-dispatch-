"""SP-B2-PEAKWIN (2026-06-11, roadmapa BARTEK 2.0) — testy.

1. Okna peak klasyfikatora wyrównane do doktryny (11-14/17-20 Warsaw, było
   12-14/18-20) + wspólne sloty calib_maps.time_slot_warsaw.
2. Bucket HIGH_RISK 14-17 (strefa śmierci): margin +5, tiery zawężone do
   gold/std+; flaga ENABLE_F7_HIGH_RISK_BUCKET; tylko przy jawnym now.
3. calib_maps: fail-soft (brak map), poprawny lookup ETA-quantile + prep-bias.
4. Serializer LOCATION A+B: travel_min_cal; result-level: prep_bias_min +
   effective_ready_shadow (lekcja #109 — presence w SERIALIZED output).
"""
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from dispatch_v2 import calib_maps
from dispatch_v2 import shadow_dispatcher
from dispatch_v2.auto_proximity_classifier import (
    classify_auto_route,
    build_context_for_logging,
    _peak_window_for,
    _time_bucket_for,
    ROUTE_AUTO,
    ROUTE_ACK,
)

# CEST (czerwiec) = UTC+2: 13:00 UTC = 15:00 Warsaw (high_risk), 10:00 UTC = 12:00 (lunch)
T_LUNCH = datetime(2026, 6, 11, 10, 0, tzinfo=timezone.utc)
T_HIGH_RISK = datetime(2026, 6, 11, 13, 0, tzinfo=timezone.utc)
T_DINNER = datetime(2026, 6, 11, 16, 0, tzinfo=timezone.utc)
T_OFF = datetime(2026, 6, 11, 5, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _fresh_map_caches():
    calib_maps.reset_caches()
    yield
    calib_maps.reset_caches()


# ---------------------------------------------------------------------------
# 1. Okna peak + sloty
# ---------------------------------------------------------------------------

def test_time_slot_warsaw_boundaries():
    cases = [
        (10, "off"),           # 10:59... użyjemy pełnych godzin Warsaw
        (11, "peak_lunch"),
        (13, "peak_lunch"),
        (14, "high_risk"),
        (16, "high_risk"),
        (17, "peak_dinner"),
        (19, "peak_dinner"),
        (20, "off"),
        (23, "off"),
    ]
    for h_warsaw, want in cases:
        dt = datetime(2026, 6, 11, h_warsaw - 2, 30, tzinfo=timezone.utc)  # CEST=UTC+2
        assert calib_maps.time_slot_warsaw(dt) == want, f"h={h_warsaw}"


def test_peak_window_doctrine_11_14_17_20():
    # 11:30 / 17:30 Warsaw = peak; 14:30 (high-risk) i 20:30 = nie-peak
    assert _peak_window_for(datetime(2026, 6, 11, 9, 30, tzinfo=timezone.utc)) is True
    assert _peak_window_for(datetime(2026, 6, 11, 15, 30, tzinfo=timezone.utc)) is True
    assert _peak_window_for(datetime(2026, 6, 11, 12, 30, tzinfo=timezone.utc)) is False
    assert _peak_window_for(datetime(2026, 6, 11, 18, 30, tzinfo=timezone.utc)) is False
    # Stare okno 12-14: 12:30 Warsaw dalej peak (subset 11-14) — regresja OK
    assert _peak_window_for(datetime(2026, 6, 11, 10, 30, tzinfo=timezone.utc)) is True
    assert _peak_window_for(None) is False


def test_time_bucket_for_none_is_off_label():
    # _time_bucket_for(None) czyta realny zegar (telemetria), ale classify
    # NIE stosuje HIGH_RISK przy now=None — patrz testy niżej.
    assert _time_bucket_for(T_HIGH_RISK) == "high_risk"
    assert _time_bucket_for(T_LUNCH) == "peak_lunch"


# ---------------------------------------------------------------------------
# 2. HIGH_RISK bucket w classify_auto_route
# ---------------------------------------------------------------------------

def _mk_candidate(cid="c1", score=80.0, verdict="MAYBE"):
    return SimpleNamespace(
        courier_id=cid, name="Test", score=score, plan=SimpleNamespace(sla_violations=0),
        feasibility_verdict=verdict, feasibility_reason="ok",
        best_effort=False, metrics={},
    )


def _mk_result(best_score=80.0, second_score=62.0):
    best = _mk_candidate("c1", best_score)
    cands = [best, _mk_candidate("c2", second_score), _mk_candidate("c3", 40.0)]
    return SimpleNamespace(
        verdict="PROPOSE", best=best, candidates=cands,
        pool_feasible_count=3, pool_total_count=5,
        pickup_ready_at=datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc),
    ), {best.courier_id: SimpleNamespace(
        tier_bag="gold",
        shift_end=datetime(2026, 6, 11, 20, 0, tzinfo=timezone.utc),
        shift_start=datetime(2026, 6, 11, 8, 0, tzinfo=timezone.utc),
        pos_source="gps",
    )}


_FLAGS_T1 = {"AUTO_PROXIMITY_ENABLED": True, "AUTO_PROXIMITY_THRESHOLD": "T1"}


def test_high_risk_margin_bump_blocks_auto():
    """Margin 18 ≥ T1(15) → AUTO w lunchu, ale < 20 w HIGH_RISK → ACK."""
    result, fleet = _mk_result(best_score=80.0, second_score=62.0)  # margin 18
    route_lunch, _ = classify_auto_route(result, fleet, now=T_LUNCH, flags=dict(_FLAGS_T1))
    assert route_lunch == ROUTE_AUTO
    route_hr, reason_hr = classify_auto_route(result, fleet, now=T_HIGH_RISK, flags=dict(_FLAGS_T1))
    assert route_hr == ROUTE_ACK, reason_hr
    assert reason_hr.startswith("hr1417|C2_score_margin"), reason_hr


def test_high_risk_margin_pass_keeps_auto_with_hr_label():
    """Margin 25 ≥ 20 → AUTO także w HIGH_RISK, reason z sufiksem _HR."""
    result, fleet = _mk_result(best_score=80.0, second_score=55.0)  # margin 25
    route, reason = classify_auto_route(result, fleet, now=T_HIGH_RISK, flags=dict(_FLAGS_T1))
    assert route == ROUTE_AUTO, reason
    assert "high_conf_T1_HR" in reason, reason


def test_high_risk_tier_restriction_t2_std_rejected():
    """T2 dopuszcza std; HIGH_RISK zawęża do gold/std+ → std = ACK (C3)."""
    result, fleet = _mk_result(best_score=80.0, second_score=50.0)  # margin 30
    fleet["c1"].tier_bag = "std"
    flags = {"AUTO_PROXIMITY_ENABLED": True, "AUTO_PROXIMITY_THRESHOLD": "T2"}
    route_lunch, r_lunch = classify_auto_route(result, fleet, now=T_LUNCH, flags=dict(flags))
    assert route_lunch == ROUTE_AUTO, r_lunch
    route_hr, r_hr = classify_auto_route(result, fleet, now=T_HIGH_RISK, flags=dict(flags))
    assert route_hr == ROUTE_ACK, r_hr
    assert "C3_tier=std" in r_hr, r_hr


def test_high_risk_flag_off_kill_switch():
    result, fleet = _mk_result(best_score=80.0, second_score=62.0)  # margin 18
    flags = dict(_FLAGS_T1, ENABLE_F7_HIGH_RISK_BUCKET=False)
    route, reason = classify_auto_route(result, fleet, now=T_HIGH_RISK, flags=flags)
    assert route == ROUTE_AUTO, reason
    assert "_HR" not in reason


def test_high_risk_requires_explicit_now():
    """now=None → bucket NIE aplikowany (determinizm testów/replay)."""
    result, fleet = _mk_result(best_score=80.0, second_score=62.0)  # margin 18
    route, reason = classify_auto_route(result, fleet, now=None, flags=dict(_FLAGS_T1))
    assert route == ROUTE_AUTO, reason


def test_dinner_and_off_not_high_risk():
    result, fleet = _mk_result(best_score=80.0, second_score=62.0)  # margin 18
    for t in (T_DINNER, T_OFF):
        route, reason = classify_auto_route(result, fleet, now=t, flags=dict(_FLAGS_T1))
        assert route == ROUTE_AUTO, (t, reason)


def test_build_context_exposes_time_bucket():
    result, fleet = _mk_result()
    ctx = build_context_for_logging(result, fleet, flags=dict(_FLAGS_T1), now=T_HIGH_RISK)
    assert ctx["auto_route_time_bucket"] == "high_risk"
    ctx_none = build_context_for_logging(result, fleet, flags=dict(_FLAGS_T1))
    assert ctx_none["auto_route_time_bucket"] is None


# ---------------------------------------------------------------------------
# 3. calib_maps — fail-soft + lookup
# ---------------------------------------------------------------------------

def test_eta_quantile_missing_file_fail_soft(tmp_path, monkeypatch):
    monkeypatch.setattr(calib_maps, "ETA_QUANTILE_MAP_PATH", str(tmp_path / "nie_ma.json"))
    assert calib_maps.eta_quantile_calibrate(25.0, T_LUNCH) is None


def test_eta_quantile_malformed_fail_soft(tmp_path, monkeypatch):
    p = tmp_path / "eta.json"
    p.write_text("{zepsuty json", encoding="utf-8")
    monkeypatch.setattr(calib_maps, "ETA_QUANTILE_MAP_PATH", str(p))
    assert calib_maps.eta_quantile_calibrate(25.0, T_LUNCH) is None


def test_eta_quantile_lookup_slot_and_all_fallback(tmp_path, monkeypatch):
    p = tmp_path / "eta.json"
    p.write_text(json.dumps({
        "version": 1,
        "buckets": [
            {"slot": "peak_lunch", "pred_lo": 20.0, "pred_hi": 30.0, "p50": 14.0, "p80": 19.0, "n": 100},
            {"slot": "all", "pred_lo": 20.0, "pred_hi": 30.0, "p50": 16.0, "p80": 21.0, "n": 500},
            {"slot": "all", "pred_lo": 30.0, "pred_hi": 60.0, "p50": 17.0, "p80": 25.0, "n": 300},
        ],
    }), encoding="utf-8")
    monkeypatch.setattr(calib_maps, "ETA_QUANTILE_MAP_PATH", str(p))
    # slot-match (lunch): 25 → 14.0
    assert calib_maps.eta_quantile_calibrate(25.0, T_LUNCH) == 14.0
    # high_risk nie ma koszyka → fallback "all": 25 → 16.0
    assert calib_maps.eta_quantile_calibrate(25.0, T_HIGH_RISK) == 16.0
    # drugi koszyk all: 45 → 17.0 (lunch też spada do all przy 45)
    assert calib_maps.eta_quantile_calibrate(45.0, T_LUNCH) == 17.0
    # poza zakresem → None
    assert calib_maps.eta_quantile_calibrate(99.0, T_LUNCH) is None
    # p80 na żądanie
    assert calib_maps.eta_quantile_calibrate(25.0, T_LUNCH, quantile="p80") == 19.0
    # nie-liczbowy pred → None
    assert calib_maps.eta_quantile_calibrate(None, T_LUNCH) is None


def test_prep_bias_lookup_and_global_fallback(tmp_path, monkeypatch):
    p = tmp_path / "bias.json"
    p.write_text(json.dumps({
        "version": 1,
        "global": {"peak_lunch": {"bias_med": 5.0, "n": 900, "std": 8.0},
                   "all": {"bias_med": 4.0, "n": 2000, "std": 9.0}},
        "restaurants": {
            "pizzeria 105": {"peak_lunch": {"bias_med": 12.0, "n": 45, "std": 11.0}},
        },
    }), encoding="utf-8")
    monkeypatch.setattr(calib_maps, "PREP_BIAS_MAP_PATH", str(p))
    # komórka restauracji (normalizacja strip+lower)
    assert calib_maps.prep_bias_for("  Pizzeria 105 ", T_LUNCH) == 12.0
    # restauracja bez komórki slotu → global slot
    assert calib_maps.prep_bias_for("Inna Knajpa", T_LUNCH) == 5.0
    # slot bez globala → global all
    assert calib_maps.prep_bias_for("Inna Knajpa", T_HIGH_RISK) == 4.0
    # brak pliku → None
    monkeypatch.setattr(calib_maps, "PREP_BIAS_MAP_PATH", str(tmp_path / "brak.json"))
    calib_maps.reset_caches()
    assert calib_maps.prep_bias_for("Pizzeria 105", T_LUNCH) is None


def test_map_hot_reload_on_mtime_change(tmp_path, monkeypatch):
    import os
    p = tmp_path / "eta.json"
    p.write_text(json.dumps({"buckets": [
        {"slot": "all", "pred_lo": 0.0, "pred_hi": 99.0, "p50": 10.0, "p80": 12.0, "n": 10}]}),
        encoding="utf-8")
    monkeypatch.setattr(calib_maps, "ETA_QUANTILE_MAP_PATH", str(p))
    assert calib_maps.eta_quantile_calibrate(5.0, T_LUNCH) == 10.0
    p.write_text(json.dumps({"buckets": [
        {"slot": "all", "pred_lo": 0.0, "pred_hi": 99.0, "p50": 11.5, "p80": 13.0, "n": 10}]}),
        encoding="utf-8")
    os.utime(p, (p.stat().st_atime + 5, p.stat().st_mtime + 5))
    assert calib_maps.eta_quantile_calibrate(5.0, T_LUNCH) == 11.5


# ---------------------------------------------------------------------------
# 4. Serializer LOCATION A+B + pola result-level (lekcja #109)
# ---------------------------------------------------------------------------

def _mk_ser_candidate(cid="c1", score=70.0, travel_min_cal=13.5):
    return SimpleNamespace(
        courier_id=cid, name="Test", score=score, plan=None,
        feasibility_verdict="MAYBE", feasibility_reason="ok", best_effort=False,
        metrics={"travel_min": 22.0, "travel_min_cal": travel_min_cal, "pos_source": "gps"},
    )


def _mk_ser_result(best, candidates, restaurant="Pizzeria 105"):
    return SimpleNamespace(
        order_id="471999", restaurant=restaurant, delivery_address="Testowa 1",
        verdict="PROPOSE", reason="ok", best=best, candidates=candidates,
        pickup_ready_at=datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc),
    )


def test_serializer_location_a_travel_min_cal():
    out = shadow_dispatcher._serialize_candidate(_mk_ser_candidate(travel_min_cal=13.5))
    assert out["travel_min_cal"] == 13.5
    # None propagowane (mapa nie istnieje / flaga OFF)
    out_none = shadow_dispatcher._serialize_candidate(_mk_ser_candidate(travel_min_cal=None))
    assert out_none["travel_min_cal"] is None


def test_serializer_location_b_best_travel_min_cal_and_prep_bias(tmp_path, monkeypatch):
    p = tmp_path / "bias.json"
    p.write_text(json.dumps({
        "global": {"all": {"bias_med": 7.0, "n": 100, "std": 5.0}},
        "restaurants": {},
    }), encoding="utf-8")
    monkeypatch.setattr(calib_maps, "PREP_BIAS_MAP_PATH", str(p))
    best = _mk_ser_candidate("c1", 80.0, travel_min_cal=13.5)
    alt = _mk_ser_candidate("c2", 60.0, travel_min_cal=9.0)
    result = _mk_ser_result(best, [best, alt])
    out = shadow_dispatcher._serialize_result(result, event_id="ev1", latency_ms=10.0)
    assert out["best"]["travel_min_cal"] == 13.5
    # prep-bias: global all = 7.0 → effective_ready = 12:00Z + 7 min
    assert out["prep_bias_min"] == 7.0
    eff = datetime.fromisoformat(out["effective_ready_shadow"])
    assert eff == datetime(2026, 6, 11, 12, 7, tzinfo=timezone.utc)


def test_serializer_result_fields_fail_soft_without_map(tmp_path, monkeypatch):
    monkeypatch.setattr(calib_maps, "PREP_BIAS_MAP_PATH", str(tmp_path / "brak.json"))
    best = _mk_ser_candidate()
    result = _mk_ser_result(best, [best])
    out = shadow_dispatcher._serialize_result(result, event_id="ev2", latency_ms=5.0)
    assert "prep_bias_min" in out and out["prep_bias_min"] is None
    assert "effective_ready_shadow" in out and out["effective_ready_shadow"] is None
