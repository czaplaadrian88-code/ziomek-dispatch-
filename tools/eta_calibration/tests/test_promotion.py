"""Known-answer i mutation probes exact-support champion/challenger."""
from __future__ import annotations

import copy

import pytest

from dispatch_v2.tools.eta_calibration import models as M
from dispatch_v2.tools.eta_calibration import promotion as P


def _row(i, target=10.0):
    return {
        "order_id": f"order-{i}", "day": "2026-06-20",
        "courier_id": "C", "restaurant_key": "R", "was_czasowka": 0,
        "pickup_slip_koord_min": float(target), "actual_deliver_min": 20.0,
        "osrm_deliv_km": 2.0, "osrm_deliv_ff_min": 5.0, "pace_deliv": 4.0,
    }


def _incumbent_payload(rows):
    train = [_row(1000 + i, target=0.0) for i in range(40)]
    model = M.EmpiricalQuantileModel(M.PICKUP, [0.5, 0.8, 0.9]).fit(train)
    return P.build_model_payload(
        leg=M.PICKUP, model_name="L1_empirical", runtime_model=model,
        evaluation_model=model, evidence_rows=rows, holdout_cut_day="2026-06-20",
        generated_at="2026-07-11T00:00:00+00:00", operational_quantile=0.8,
    )


def _cfg():
    return {"acceptance": {
        "pickup_mae_improve_pct": 12.0, "delivery_mae_improve_pct": 5.0,
        "significance_alpha": 0.05, "min_paired_records": 30,
        "non_inferiority_margin_pct": 0.0,
    }}


class TargetOffsetModel:
    def __init__(self, offset, missing_key=None):
        self.offset = offset
        self.missing_key = missing_key

    def predict_quantiles(self, row):
        if P.support_key(row) == self.missing_key:
            return None
        pred = row["pickup_slip_koord_min"] - self.offset
        return {0.5: pred, 0.8: pred, 0.9: pred}


def test_known_answer_material_paired_improvement_promotes():
    rows = [_row(i) for i in range(60)]
    decision = P.compare_on_frozen_support(
        TargetOffsetModel(5.0), rows, _incumbent_payload(rows), _cfg(),
        M.PICKUP, "challenger",
    )
    assert decision["status"] == "PROMOTE"
    assert decision["support_exact"] is True
    assert decision["n_common"] == 60
    assert decision["improve_pct"] == pytest.approx(50.0)
    assert decision["paired"]["ci"][1] < 0


@pytest.mark.parametrize("regression_pct", [1.0, 2.0])
def test_mutation_model_one_or_two_percent_worse_never_promotes(regression_pct):
    rows = [_row(i) for i in range(60)]
    decision = P.compare_on_frozen_support(
        TargetOffsetModel(10.0 * (1.0 + regression_pct / 100.0)),
        rows, _incumbent_payload(rows), _cfg(), M.PICKUP, "worse",
    )
    assert decision["status"] == "HOLD"
    assert decision["promote"] is False
    assert decision["challenger_mae"] > decision["incumbent_mae"]


def test_mutation_different_support_holds():
    rows = [_row(i) for i in range(60)]
    missing = P.support_key(rows[0])
    decision = P.compare_on_frozen_support(
        TargetOffsetModel(5.0, missing_key=missing), rows,
        _incumbent_payload(rows), _cfg(), M.PICKUP, "partial",
    )
    assert decision["status"] == "HOLD"
    assert decision["reason"] == "model_support_mismatch"
    assert decision["support_exact"] is False


def test_mutation_target_drift_holds():
    rows = [_row(i) for i in range(60)]
    payload = _incumbent_payload(rows)
    mutated = copy.deepcopy(rows)
    mutated[0]["pickup_slip_koord_min"] += 1.0
    decision = P.compare_on_frozen_support(
        TargetOffsetModel(5.0), mutated, payload, _cfg(), M.PICKUP, "drift",
    )
    assert decision["status"] == "HOLD"
    assert decision["reason"] == "frozen_support_target_drift"


def test_mutation_artifact_integrity_and_missing_artifact_hold(tmp_path):
    rows = [_row(i) for i in range(40)]
    payload = _incumbent_payload(rows)
    payload["promotion_evidence"]["predictions"][0]["prediction"] += 0.5
    assert P.validate_model_payload(payload, M.PICKUP) == "artifact_integrity_mismatch"

    got, reason = P.load_model_payload(str(tmp_path / "missing.json"), M.PICKUP)
    assert got is None
    assert reason == "incumbent_artifact_missing"
