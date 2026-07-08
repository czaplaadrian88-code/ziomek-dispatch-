"""Testy modeli: shrinkage EB, monotoniczność kwantyli, leakage-safe historia kuriera."""
from dispatch_v2.tools.eta_calibration import models as M


def _pickup_row(cid, slip, load=1, slot="off", hour=12):
    return {"courier_id": cid, "pickup_slip_koord_min": slip, "load": load,
            "slot": slot, "hour": hour, "weekday": 1, "was_czasowka": 0,
            "pace_deliv": None, "actual_deliver_min": None}


def test_shrinkage_pulls_small_sample_courier_toward_global():
    # kurier BIG: dużo próbek daleko od globalu; kurier SMALL: mało próbek tak samo daleko.
    rows = []
    for _ in range(200):
        rows.append(_pickup_row("BIG", 20.0))
    for _ in range(3):
        rows.append(_pickup_row("SMALL", 20.0))
    for _ in range(400):
        rows.append(_pickup_row("REST", 0.0))  # globalna masa ~0
    m = M.EmpiricalQuantileModel(M.PICKUP, [0.5, 0.75, 0.9]).fit(rows)
    off_big = m.courier_off["BIG"]
    off_small = m.courier_off["SMALL"]
    # oba dodatnie, ale SMALL mocniej ściągnięty do globalu (mniejszy offset)
    assert off_big > off_small > 0
    # SMALL (n=3) offset ~0.5x BIG (n=200): shrinkage n/(n+K), K=3 → 3/6=0.5 vs 200/203≈1
    assert off_small < off_big * 0.6   # mała próba = silniejszy shrinkage ku globalowi


def test_quantiles_monotonic_empirical():
    rows = [_pickup_row("C", s) for s in range(-10, 30)]
    m = M.EmpiricalQuantileModel(M.PICKUP, [0.5, 0.75, 0.9]).fit(rows)
    q = m.predict_quantiles(_pickup_row("C", 0))
    assert q[0.5] <= q[0.75] <= q[0.9]


def test_build_courier_history_leakage_safe():
    # historia liczona TYLKO z podanych rekordów (train) — nie widzi innych
    train = [_pickup_row("C", 10.0), _pickup_row("C", 12.0)]
    h = M.build_courier_history(train)
    assert "C" in h
    assert abs(h["C"]["med_slip"] - 11.0) < 0.01
    assert h["C"]["n_slip"] == 2


def test_empirical_operational_quantile_higher_than_median():
    # P75 poślizgu powinien być >= P50 (asymetria bezpieczeństwa)
    rows = [_pickup_row("C", s) for s in [0, 1, 2, 3, 4, 5, 6, 8, 10, 15]]
    m = M.EmpiricalQuantileModel(M.PICKUP, [0.5, 0.75, 0.9]).fit(rows)
    q = m.predict_quantiles(_pickup_row("C", 0))
    assert q[0.75] >= q[0.5]


def test_delivery_predictions_positive():
    rows = [{"courier_id": "C", "actual_deliver_min": d, "load": 1, "slot": "off",
             "osrm_deliv_km": 2.0, "pace_deliv": 3.0, "pickup_slip_koord_min": None}
            for d in [10, 12, 15, 18, 20, 25]]
    m = M.EmpiricalQuantileModel(M.DELIVERY, [0.5, 0.75, 0.9]).fit(rows)
    q = m.predict_quantiles(rows[0])
    assert all(v >= 1.0 for v in q.values())   # czas dostawy zawsze dodatni
