"""Testy modeli: shrinkage EB, monotoniczność kwantyli, leakage-safe historia kuriera."""
import copy

import pytest

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


def test_served_feature_contract_excludes_outcome_only_fields():
    forbidden_names = {"load", "hour", "wd", "prep", "slot", "weekday", "is_bundle"}
    for leg in (M.PICKUP, M.DELIVERY):
        assert forbidden_names.isdisjoint(M.served_feature_names(leg))
    assert {"hour", "load", "slot", "weekday", "prep_var_med"} <= M.OUTCOME_ONLY_FIELDS


def test_empirical_prediction_ignores_future_hour_load_and_prep_mutation():
    rows = [_pickup_row("C", float(i % 17), load=(i % 4) + 1,
                        slot="peak_lunch" if i % 2 else "off", hour=10 + i % 10)
            for i in range(80)]
    model = M.EmpiricalQuantileModel(M.PICKUP, [0.5, 0.8, 0.9]).fit(rows)
    rec = dict(rows[0], was_czasowka=0, prep_var_med=2.0, weekday=1, is_bundle=0)
    mutated = dict(rec, hour=23, slot="peak_dinner", weekday=6, load=99,
                   is_bundle=1, prep_var_med=999.0)
    assert model.predict_quantiles(rec) == model.predict_quantiles(mutated)


def _lgbm_rows(leg, n=90):
    rows = []
    for i in range(n):
        row = {
            "order_id": f"o{i}", "day": f"2026-06-{1 + i // 5:02d}",
            "courier_id": f"C{i % 4}", "restaurant_key": f"R{i % 5}",
            "was_czasowka": i % 2, "osrm_deliv_km": 1.0 + (i % 6),
            "osrm_deliv_ff_min": 4.0 + (i % 7), "pickup_slip_koord_min": None,
            "actual_deliver_min": None, "pace_deliv": None,
            # jawnie outcome-only; mutation nie moze zmienic predykcji
            "hour": 10 + i % 10, "slot": "off", "weekday": i % 7,
            "load": 1 + i % 4, "is_bundle": int(i % 4 > 0),
            "prep_var_med": float(i % 12),
        }
        if leg == M.PICKUP:
            row["pickup_slip_koord_min"] = float((i * 3) % 19)
        else:
            row["actual_deliver_min"] = float(8 + (i * 5) % 24)
            row["pace_deliv"] = row["actual_deliver_min"] / row["osrm_deliv_ff_min"]
        rows.append(row)
    return rows


@pytest.mark.parametrize("leg", [M.PICKUP, M.DELIVERY])
def test_lgbm_future_feature_mutation_and_artifact_roundtrip(leg):
    rows = _lgbm_rows(leg)
    params = {"num_leaves": 7, "min_child_samples": 4, "learning_rate": 0.1,
              "n_estimators": 12, "lambda_l2": 1.0}
    model = M.LGBMQuantileModel(leg, [0.5, 0.8, 0.9], params).fit(
        rows, M.build_courier_history(rows),
    )
    rec = rows[-1]
    mutated = copy.deepcopy(rec)
    mutated.update(hour=1, slot="peak_dinner", weekday=6, load=100,
                   is_bundle=1, prep_var_med=1000.0)
    expected = model.predict_quantiles(rec)
    assert model.predict_quantiles(mutated) == expected

    restored = M.model_from_artifact(M.model_to_artifact(model))
    got = restored.predict_quantiles(rec)
    assert got == pytest.approx(expected, rel=1e-10, abs=1e-10)
    assert restored.feat_names == list(M.served_feature_names(leg))


def test_l1_artifact_roundtrip_known_answer():
    rows = [_pickup_row("C", float(i)) for i in range(20)]
    model = M.EmpiricalQuantileModel(M.PICKUP, [0.5, 0.8, 0.9]).fit(rows)
    restored = M.model_from_artifact(M.model_to_artifact(model))
    assert restored.predict_quantiles(rows[0]) == pytest.approx(
        model.predict_quantiles(rows[0]), rel=1e-12, abs=1e-12,
    )
