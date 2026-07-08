"""Testy walidacji: split czasowy (zero wycieku), metryki, pokrycie, pinball."""
from dispatch_v2.tools.eta_calibration import evaluate as EV


def _r(day, oid="x"):
    return {"day": day, "ts_pickup": day + "T12:00:00", "order_id": oid,
            "courier_id": "C", "pickup_slip_koord_min": 1.0, "actual_deliver_min": 15.0,
            "load": 1, "slot": "off", "hour": 12, "weekday": 1}


def test_time_split_no_leakage():
    rows = [_r(f"2026-06-{d:02d}") for d in range(1, 30)]
    train, hold, cut = EV.time_split(rows, holdout_days=5)
    assert train and hold
    # KLUCZOWE: każdy dzień train ściśle < każdy dzień holdout (brak wycieku z przyszłości)
    assert max(r["day"] for r in train) < min(r["day"] for r in hold)
    assert all(r["day"] < cut for r in train)
    assert all(r["day"] >= cut for r in hold)


def test_metrics_known_values():
    errs = [-4.0, 0.0, 4.0]           # MAE=8/3, bias=0
    m = EV._metrics(errs)
    assert m["n"] == 3
    assert abs(m["bias"]) < 1e-9
    assert abs(m["mae"] - 8 / 3) < 0.01
    assert m["w5"] == 100.0            # wszystkie |err|<=5
    assert m["w10"] == 100.0


def test_coverage_exact():
    actual = [10, 12, 14, 20]
    pred_q = [15, 15, 15, 15]          # 3/4 <= 15
    assert EV.coverage(actual, pred_q) == 75.0


def test_pinball_asymmetric():
    # przy q=0.75 spóźnienie (actual>pred) karane 3x mocniej niż zapas
    late = EV.pinball([10.0], [6.0], 0.75)   # d=+4 → 0.75*4 = 3.0
    early = EV.pinball([2.0], [6.0], 0.75)   # d=-4 → 0.25*4 = 1.0
    assert abs(late - 3.0) < 1e-6
    assert abs(early - 1.0) < 1e-6
    assert late == 3.0 * early                # dokładnie 3:1 (koszt Adriana)


def test_paired_delta_ci_direction():
    err_a = [1.0] * 50    # model A dobry
    err_b = [5.0] * 50    # model B słaby
    d = EV._paired_delta_ci(err_a, err_b)
    assert d["delta_mae"] < 0            # A lepszy (ujemna delta)
    assert d["ci"][1] < 0               # cały CI < 0 = istotne
