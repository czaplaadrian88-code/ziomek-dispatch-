"""K6: decision_eta.v1 carries labelled pickup P50/P80 without breaking v1."""
from __future__ import annotations

import json
import sqlite3
import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from dispatch_v2 import common as C
from dispatch_v2 import decision_eta_log as dtlog
from dispatch_v2 import eta_calib_serving
from dispatch_v2.tools import decision_eta_coverage
from dispatch_v2.tools import gps_decision_eta_remeasure as remeasure


NOW = datetime(2026, 7, 27, 10, 0, tzinfo=timezone.utc)


def _candidate(cid: str) -> SimpleNamespace:
    return SimpleNamespace(
        courier_id=cid,
        score=100.0,
        feasibility_verdict="MAYBE",
        best_effort=False,
        metrics={},
        plan=SimpleNamespace(
            strategy="append",
            pickup_at={"O-1": NOW + timedelta(minutes=14)},
            predicted_delivered_at={"O-1": NOW + timedelta(minutes=30)},
        ),
    )


def _legacy_eta_row(order_id: str, cid: str, decision_ts: datetime) -> dict:
    return {
        "schema": "decision_eta.v1",
        "decision_id": f"shadow_dispatcher:E-{order_id}",
        "decision_ts": decision_ts.isoformat(),
        "recorded_at": decision_ts.isoformat(),
        "decision_kind": "dispatch_selection",
        "source": "shadow_dispatcher",
        "order_id": order_id,
        "selected_cid": cid,
        "outcome": "PROPOSE",
        "candidate_pool_scope": "full_pool_pre_top_n",
        "candidate_count": 1,
        "candidates": [{
            "cid": cid,
            "selected": True,
            "position_source": "gps_fresh",
            "legs": [{
                "order_id": order_id,
                "pickup_eta_at": (decision_ts + timedelta(minutes=14)).isoformat(),
                "delivery_eta_at": None,
                "missing": ["delivery_eta_unavailable"],
            }],
        }],
        "model": {},
        "calibration": {},
    }


def test_canonical_producer_labels_every_candidate_and_mutation_is_red(
    tmp_path, monkeypatch,
):
    """Usunięcie wywołania producenta z loggera usuwa pola i czerwieni test."""
    target = tmp_path / "decision_eta.jsonl"
    monkeypatch.setattr(dtlog, "LOG_PATH", target)
    monkeypatch.setattr(C, "decision_flag", lambda name: name == dtlog.FLAG)
    monkeypatch.setattr(dtlog, "_calibration_provenance", lambda: {})
    calls: list[str] = []

    def prediction(candidate, order_event):
        calls.append(candidate.courier_id)
        assert order_event["pickup_coords"] == (53.13, 23.16)
        return {
            "pred_op": 4.25,
            "p80": 7.5,
            "prediction_version": "eta_pickup_quantiles.v1",
            "prediction_provenance": {
                "producer": "eta_calib_serving.predict_pickup_quantiles_batch",
                "model_artifact_sha256_12": "abc123def456",
                "feature_contract_version": "decision_time_v2",
                "target": "pickup_slip_vs_czas_kuriera_min",
                "quantiles": {"pred_op": 0.5, "p80": 0.8},
            },
        }, None

    monkeypatch.setattr(
        eta_calib_serving,
        "predict_pickup_quantiles_batch",
        lambda candidates, order_event: [
            prediction(candidate, order_event) for candidate in candidates
        ],
    )
    first, second = _candidate("C-1"), _candidate("C-2")
    assert dtlog.record_candidate_decision(
        decision_id="shadow_dispatcher:E-1",
        decision_ts=NOW,
        decision_kind="dispatch_selection",
        source="shadow_dispatcher",
        order_id="O-1",
        outcome="PROPOSE",
        candidates=[first, second],
        selected=first,
        prediction_context={"pickup_coords": (53.13, 23.16)},
    )

    row = json.loads(target.read_text(encoding="utf-8"))
    assert calls == ["C-1", "C-2"]
    for candidate in row["candidates"]:
        assert candidate["pred_op"] == 4.25
        assert candidate["p80"] == 7.5
        assert candidate["prediction_version"] == "eta_pickup_quantiles.v1"
        assert candidate["prediction_provenance"]["quantiles"] == {
            "pred_op": 0.5,
            "p80": 0.8,
        }


def test_old_decision_eta_v1_remains_valid_without_optional_prediction_fields():
    row = _legacy_eta_row("O-1", "C-1", NOW)
    assert decision_eta_coverage.validate_record(row) == []


def test_prediction_loss_keeps_base_snapshot_and_omits_optional_pair(
    tmp_path, monkeypatch,
):
    target = tmp_path / "decision_eta.jsonl"
    monkeypatch.setattr(dtlog, "LOG_PATH", target)
    monkeypatch.setattr(C, "decision_flag", lambda name: name == dtlog.FLAG)
    monkeypatch.setattr(dtlog, "_calibration_provenance", lambda: {})
    monkeypatch.setattr(
        eta_calib_serving,
        "predict_pickup_quantiles_batch",
        lambda candidates, order_event: [
            (None, "champion_missing") for _ in candidates
        ],
    )
    candidate = _candidate("C-1")
    assert dtlog.record_candidate_decision(
        decision_id="shadow_dispatcher:E-1",
        decision_ts=NOW,
        decision_kind="dispatch_selection",
        source="shadow_dispatcher",
        order_id="O-1",
        outcome="PROPOSE",
        candidates=[candidate],
        selected=candidate,
        prediction_context={"pickup_coords": (53.13, 23.16)},
    )
    saved = json.loads(target.read_text(encoding="utf-8"))["candidates"][0]
    assert saved["cid"] == "C-1"
    assert {"pred_op", "p80", "prediction_version"}.isdisjoint(saved)


def test_single_prediction_producer_and_all_nonplan_hooks_are_ratcheted():
    root = Path(__file__).resolve().parents[1]
    producers = []
    for path in root.rglob("*.py"):
        if "tests" in path.parts or "eod_drafts" in path.parts:
            continue
        if "def predict_pickup_quantiles_batch(" in path.read_text(
            encoding="utf-8"
        ):
            producers.append(path.relative_to(root).as_posix())
    assert producers == ["eta_calib_serving.py"]
    assert "predict_pickup_quantiles_batch(" in inspect.getsource(
        dtlog.record_candidate_decision
    )
    for relative in (
        "shadow_dispatcher.py",
        "czasowka_scheduler.py",
        "tools/reassignment_forward_shadow.py",
        "tools/pending_global_resweep.py",
    ):
        source = (root / relative).read_text(encoding="utf-8")
        assert "prediction_context=" in source, relative
    # plan_manager has no decision-time order features; optional pair is N-D.
    assert "prediction_context=" not in (
        root / "plan_manager.py"
    ).read_text(encoding="utf-8")


def _write_measurement_inputs(
    root: Path, *, count: int, labelled_count: int,
) -> tuple[Path, Path, Path, Path]:
    eta_path = root / "decision_eta.jsonl"
    outcome_path = root / "outcomes.jsonl"
    dwell_path = root / "dwell.json"
    db_path = root / "eta.db"
    dwell = {}
    eta_lines = []
    outcome_lines = []

    connection = sqlite3.connect(db_path)
    connection.execute(
        """
        CREATE TABLE eta_calib_features (
            order_id TEXT, courier_id TEXT, czas_kuriera TEXT,
            eng_pickup_slip_min REAL, slot TEXT, is_bundle INTEGER,
            was_czasowka INTEGER, load INTEGER
        )
        """
    )
    for index in range(count):
        order_id = f"O-{index:04d}"
        cid = f"C-{index % 5}"
        decision_ts = NOW + timedelta(seconds=index)
        row = _legacy_eta_row(order_id, cid, decision_ts)
        if index < labelled_count:
            row["candidates"][0].update({
                "pred_op": 4.0,
                "p80": 6.0,
                "prediction_version": "eta_pickup_quantiles.v1",
                "prediction_provenance": {
                    "producer": "eta_calib_serving.predict_pickup_quantiles_batch",
                    "model_artifact_sha256_12": "abc123def456",
                    "feature_contract_version": "decision_time_v2",
                    "target": "pickup_slip_vs_czas_kuriera_min",
                    "quantiles": {"pred_op": 0.5, "p80": 0.8},
                },
            })
        eta_lines.append(json.dumps(row))
        outcome_lines.append(json.dumps({
            "order_id": order_id,
            "actual_cid": cid,
            "action": "PANEL_AGREE",
            "ts_decision": (decision_ts + timedelta(minutes=1)).isoformat(),
        }))
        departed = decision_ts.replace(
            hour=10, minute=15, second=0, microsecond=0,
        )
        dwell[order_id] = {
            "_source": "gps_geofence",
            "_n_in_geofence": 2,
            "_min_dist_m": 5.0,
            "_radius_m": 25.0,
            "departed_restaurant": departed.isoformat(),
            "courier_id": cid,
        }
        connection.execute(
            "INSERT INTO eta_calib_features VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (order_id, cid, "12:10", 10.0, "off", 0, 0, 1),
        )
    connection.commit()
    connection.close()
    eta_path.write_text("\n".join(eta_lines) + "\n", encoding="utf-8")
    outcome_path.write_text("\n".join(outcome_lines) + "\n", encoding="utf-8")
    dwell_path.write_text(json.dumps(dwell), encoding="utf-8")
    return eta_path, outcome_path, dwell_path, db_path


def test_checker_old_rows_are_explicitly_uncomputable_at_n_200(tmp_path):
    paths = _write_measurement_inputs(tmp_path, count=200, labelled_count=0)
    report = remeasure.calculate(
        eta_log_path=paths[0],
        outcomes_path=paths[1],
        dwell_path=paths[2],
        db_path=paths[3],
        since=NOW - timedelta(days=1),
    )
    assert report["coverage"]["complete_n"] == 200
    assert report["coverage"]["prediction_complete_n"] == 0
    assert report["instrument"]["calculable"] is False
    assert report["instrument"]["reason"] == "missing_labelled_pred_op_or_p80"
    assert report["verdict"] == "HOLD_PREDICTION_UNCOMPUTABLE"


def test_checker_counts_labelled_n_and_computes_p50_p80_metrics(tmp_path):
    paths = _write_measurement_inputs(tmp_path, count=200, labelled_count=200)
    report = remeasure.calculate(
        eta_log_path=paths[0],
        outcomes_path=paths[1],
        dwell_path=paths[2],
        db_path=paths[3],
        since=NOW - timedelta(days=1),
    )
    assert report["coverage"]["complete_n"] == 200
    assert report["coverage"]["prediction_complete_n"] == 200
    assert report["coverage"]["gate_min_n"] == 200
    assert report["instrument"]["calculable"] is True
    assert report["metrics"]["p50_mae_min"] == 1.0
    assert report["metrics"]["p50_bias_median_min"] == -1.0
    assert report["metrics"]["p80_late_pct"] == 0.0
    assert report["confidence"]["level"] == 0.95
    assert report["confidence"]["paired_bootstrap"]["resamples"] == 2000


def test_checker_requires_labelled_coverage_as_well_as_n_200(tmp_path):
    paths = _write_measurement_inputs(
        tmp_path, count=400, labelled_count=200,
    )
    report = remeasure.calculate(
        eta_log_path=paths[0],
        outcomes_path=paths[1],
        dwell_path=paths[2],
        db_path=paths[3],
        since=NOW - timedelta(days=1),
    )
    assert report["coverage"]["prediction_complete_n"] == 200
    assert report["coverage"]["prediction_complete_pct"] == 50.0
    assert report["instrument"]["calculable"] is False
    assert (
        report["instrument"]["reason"]
        == "labelled_prediction_coverage_below_min_pct"
    )
    assert report["verdict"] == "HOLD_PREDICTION_UNCOMPUTABLE"
