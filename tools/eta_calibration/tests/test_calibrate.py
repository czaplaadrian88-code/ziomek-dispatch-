"""Integracja joba: legacy/missing champion = HOLD, wszystkie outputy w tmp_path."""
from __future__ import annotations

import copy
from datetime import datetime, timezone

from dispatch_v2.tools.eta_calibration import calibrate as C
from dispatch_v2.tools.eta_calibration import evaluate as E
from dispatch_v2.tools.eta_calibration import features as F
from dispatch_v2.tools.eta_calibration import models as M
from dispatch_v2.tools.eta_calibration import promotion as P
from dispatch_v2.tools import health_scoreboard as HSB


def _rows():
    out = []
    for day in range(1, 21):
        for j in range(4):
            i = (day - 1) * 4 + j
            deliver = float(10 + (i * 3) % 20)
            out.append({
                "order_id": f"synthetic-{i}", "courier_id": f"C{i % 5}",
                "day": f"2026-06-{day:02d}",
                "ts_pickup": f"2026-06-{day:02d}T12:{j:02d}:00+00:00",
                "ts_deliver": f"2026-06-{day:02d}T12:{20+j:02d}:00+00:00",
                "restaurant_key": f"R{i % 6}", "rest_lat": None, "rest_lon": None,
                "deliv_lat": None, "deliv_lon": None,
                "osrm_deliv_km": float(1 + i % 6),
                "osrm_deliv_ff_min": float(4 + i % 7),
                "actual_deliver_min": deliver, "czas_kuriera": "14:00",
                "pickup_slip_koord_min": float((i * 5) % 17),
                "eng_pickup_slip_min": float((i * 2) % 15),
                "eng_deliver_pred_min": deliver + float((i % 5) - 2),
                "load": 1 + i % 4, "hour": 12 + i % 8,
                "slot": "peak_lunch", "weekday": day % 7,
                "is_bundle": int(i % 4 > 0), "was_czasowka": i % 2,
                "prep_var_med": float(i % 10),
                "pace_deliv": deliver / float(4 + i % 7),
            })
    return out


def _cfg(tmp_path):
    cfg = copy.deepcopy(F.load_config())
    cfg["paths"].update({
        "db": str(tmp_path / "eta.db"),
        "pickup_map": str(tmp_path / "pickup_champion.json"),
        "delivery_map": str(tmp_path / "delivery_champion.json"),
        "pickup_candidate_map": str(tmp_path / "pickup_candidate.json"),
        "delivery_candidate_map": str(tmp_path / "delivery_candidate.json"),
        "shadow_log": str(tmp_path / "shadow.jsonl"),
        "metrics_log": str(tmp_path / "metrics.jsonl"),
    })
    cfg["window"]["holdout_days"] = 3
    cfg["model"]["conformal"] = False
    cfg["model"]["lgbm"].update(
        num_leaves=7, min_child_samples=2, n_estimators=8, learning_rate=0.1,
    )
    cfg["acceptance"]["min_paired_records"] = 5
    return cfg


def test_run_missing_incumbent_holds_and_writes_only_tmp_candidates(
    tmp_path, monkeypatch,
):
    rows = _rows()
    cfg = _cfg(tmp_path)
    monkeypatch.setattr(E, "load_store", lambda _path: copy.deepcopy(rows))

    record = C.run(
        cfg, rebuild=False,
        now=datetime(2026, 7, 11, 20, 0, tzinfo=timezone.utc),
    )

    assert record["instrument_status"] == "HOLD"
    assert record["promoted"] is False
    for leg in (M.PICKUP, M.DELIVERY):
        decision = record["decision"][leg]
        assert decision["status"] == "HOLD"
        assert decision["reason"] == "incumbent_artifact_missing"
        assert record["map_writes"][leg]["candidate_written"] is True
        assert record["map_writes"][leg]["champion_written"] is False

        candidate = cfg["paths"][f"{leg}_candidate_map"]
        payload, reason = P.load_model_payload(candidate, leg)
        assert reason is None
        assert payload["feature_contract"] == M.FEATURE_CONTRACT_VERSION

    assert not (tmp_path / "pickup_champion.json").exists()
    assert not (tmp_path / "delivery_champion.json").exists()
    assert (tmp_path / "metrics.jsonl").exists()
    assert (tmp_path / "shadow.jsonl").exists()

    # Zastany konsument metryk pozostaje zgodny mimo nowego HOLD/evidence.
    health = HSB.load_eta_calib(str(tmp_path / "metrics.jsonl"))
    assert health["data_ok"] is True
    assert health[M.PICKUP]["mae"] is not None
    assert health[M.DELIVERY]["mae"] is not None
