"""Tests dla ETA R3 shadow inference (eta_residual_infer) + wiring w eta_calibration_logger.
Gwarancje: (1) feature-vector parity z treningiem (kolejność/peak/tier/rest_freq/braki=-1),
(2) corrected = base + residual, (3) brak base → (None,None), (4) logger flaga OFF = zero pól R3,
(5) logger flaga ON = pola policzone i corrected==base+resid. Fail-soft wszędzie."""
import pytest
import dispatch_v2.eta_residual_infer as R


# --- build_features (nie wymaga modelu poza rest_freq/tiers) ---------------

def test_feature_vector_shape_and_order():
    R.is_available()  # załaduj rest_freq/tiers
    f = R.build_features(bag_size=3, predicted_delivery_min=22.0, hour_warsaw=12,
                         is_weekend=True, is_bundle=True, restaurant="Baanko",
                         courier_id="999999", pool_feasible=4)
    assert len(f) == 9
    assert f[0] == 3            # bag_size
    assert f[1] == 22.0         # pred_delivery_min
    assert f[2] == 12           # hour
    assert f[3] == 1            # is_weekend
    assert f[4] == 1            # is_bundle
    assert f[5] == 1            # peak (12 in 11-14)
    assert f[6] == 2            # tier_ord: nieznany cid → default std=2
    assert f[7] >= 0            # rest_freq (>=0)
    assert f[8] == 4            # pool_feasible


def test_peak_encoding():
    assert R.build_features(bag_size=1, predicted_delivery_min=10, hour_warsaw=12,
                            is_weekend=False, is_bundle=False, restaurant="x",
                            courier_id=None, pool_feasible=1)[5] == 1   # 12 peak
    assert R.build_features(bag_size=1, predicted_delivery_min=10, hour_warsaw=18,
                            is_weekend=False, is_bundle=False, restaurant="x",
                            courier_id=None, pool_feasible=1)[5] == 1   # 18 peak
    assert R.build_features(bag_size=1, predicted_delivery_min=10, hour_warsaw=15,
                            is_weekend=False, is_bundle=False, restaurant="x",
                            courier_id=None, pool_feasible=1)[5] == 0   # 15 off


def test_missing_values_become_minus1():
    f = R.build_features(bag_size=None, predicted_delivery_min=20, hour_warsaw=None,
                         is_weekend=False, is_bundle=False, restaurant=None,
                         courier_id=None, pool_feasible=None)
    assert f[0] == -1           # bag_size None → -1
    assert f[2] == -1           # hour None → -1
    assert f[5] == 0            # hour None → peak 0
    # restaurant None → "" → train-time bucket braków (faithful train/serve; trening liczył "")
    assert f[7] == (R._state["rest_freq"] or {}).get("", 0)
    assert f[8] == -1           # pool None → -1


def test_unknown_restaurant_freq_zero():
    f = R.build_features(bag_size=1, predicted_delivery_min=10, hour_warsaw=10,
                         is_weekend=False, is_bundle=False,
                         restaurant="zzz_nieistniejaca_restauracja_xyz",
                         courier_id=None, pool_feasible=1)
    assert f[7] == 0


# --- predict (wymaga modelu) ----------------------------------------------

@pytest.mark.skipif(not R.is_available(), reason="model eta_residual_v1 niedostępny")
def test_corrected_equals_base_plus_residual():
    corrected, resid = R.predict_corrected(
        bag_size=3, predicted_delivery_min=25.0, hour_warsaw=13, is_weekend=False,
        is_bundle=True, restaurant="Baanko", courier_id="21", pool_feasible=3)
    assert corrected is not None and resid is not None
    assert abs(corrected - (25.0 + resid)) < 0.011


@pytest.mark.skipif(not R.is_available(), reason="model eta_residual_v1 niedostępny")
def test_residual_deterministic():
    a = R.predict_corrected(bag_size=2, predicted_delivery_min=18.0, hour_warsaw=12,
                            is_weekend=False, is_bundle=False, restaurant="x",
                            courier_id=None, pool_feasible=2)
    b = R.predict_corrected(bag_size=2, predicted_delivery_min=18.0, hour_warsaw=12,
                            is_weekend=False, is_bundle=False, restaurant="x",
                            courier_id=None, pool_feasible=2)
    assert a == b


def test_no_base_returns_none():
    assert R.predict_corrected(bag_size=2, predicted_delivery_min=None, hour_warsaw=12,
                               is_weekend=False, is_bundle=False, restaurant="x",
                               courier_id=None, pool_feasible=2) == (None, None)


# --- logger wiring (flaga gate) -------------------------------------------

def _mk_shadow_index(oid, cid, pred_deliv_at, pred_min, pool):
    """Minimalny shadow_index z 1 rekordem, którego best=realny kurier z predykcją dla oid."""
    from datetime import datetime
    from zoneinfo import ZoneInfo
    ts = datetime(2026, 6, 15, 12, 0, tzinfo=ZoneInfo("Europe/Warsaw"))
    rec = {
        "order_id": oid, "ts": ts.isoformat(), "verdict": "PROPOSE",
        "pool_feasible_count": pool,
        "best": {"courier_id": cid, "r6_bag_size": 1,
                 "plan": {"predicted_delivered_at": {oid: pred_deliv_at},
                          "per_order_delivery_times": {oid: pred_min},
                          "total_duration_min": 30, "strategy": "ortools"}},
        "alternatives": [],
    }
    return {oid: [(ts, rec)]}


def _sla_rec(oid, cid):
    return {"order_id": oid, "courier_id": cid, "restaurant": "Baanko",
            "delivery_address": "Testowa 1", "delivery_time_minutes": 28.0,
            "picked_up_at": "2026-06-15 12:05:00", "delivered_at": "2026-06-15 12:33:00",
            "sla_ok": True, "was_czasowka": False}


def test_logger_flag_off_no_r3_fields():
    import dispatch_v2.eta_calibration_logger as L
    oid, cid = "T_R3_1", "21"
    idx = _mk_shadow_index(oid, cid, "2026-06-15T12:30:00+02:00", 25.0, 3)
    L._R3_SHADOW_ON = False
    row = L.extract_row(_sla_rec(oid, cid), idx)
    assert row["eta_r3_corrected_delivery_min"] is None
    assert row["eta_r3_residual_pred"] is None


@pytest.mark.skipif(not R.is_available(), reason="model eta_residual_v1 niedostępny")
def test_logger_flag_on_populates_and_consistent():
    import dispatch_v2.eta_calibration_logger as L
    oid, cid = "T_R3_2", "21"
    idx = _mk_shadow_index(oid, cid, "2026-06-15T12:30:00+02:00", 25.0, 3)
    L._R3_SHADOW_ON = True
    try:
        row = L.extract_row(_sla_rec(oid, cid), idx)
        assert row["predicted_delivery_min"] == 25.0
        c = row["eta_r3_corrected_delivery_min"]
        resid = row["eta_r3_residual_pred"]
        assert c is not None and resid is not None
        assert abs(c - (25.0 + resid)) < 0.011
        # error_min = real(28) − corrected
        assert abs(row["eta_r3_corrected_error_min"] - (28.0 - c)) < 0.011
    finally:
        L._R3_SHADOW_ON = False
