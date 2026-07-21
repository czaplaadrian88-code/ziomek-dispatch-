"""Contract tests for default-OFF decision-time ETA observability."""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from dispatch_v2 import common as C
from dispatch_v2 import calib_maps
from dispatch_v2 import decision_eta_log as dtlog
from dispatch_v2.core import jsonl_rotation
from dispatch_v2.tools import decision_eta_coverage


NOW = datetime(2026, 7, 21, 10, 15, tzinfo=timezone.utc)


def _candidate(cid: str, *, offset: int = 0):
    pickup = datetime(2026, 7, 21, 10, 25 + offset, tzinfo=timezone.utc)
    delivery = datetime(2026, 7, 21, 10, 45 + offset, tzinfo=timezone.utc)
    return SimpleNamespace(
        courier_id=cid,
        score=100 - offset,
        feasibility_verdict="MAYBE",
        best_effort=False,
        metrics={
            "pos_source": "gps_fresh",
            "pos_from_store": True,
            "pos_age_min": 1.25,
            "eta_source": "osrm",
            "travel_min": 10 + offset,
            "travel_min_cal": 11 + offset,
            "lgbm_shadow": {"model_version": "eta-lgbm-7"},
        },
        plan=SimpleNamespace(
            strategy="append",
            pickup_at={"O-1": pickup},
            predicted_delivered_at={"O-1": delivery},
        ),
    )


def _result():
    selected = _candidate("C-1")
    other = _candidate("C-2", offset=1)
    return SimpleNamespace(
        order_id="O-1",
        verdict="PROPOSE",
        best=selected,
        candidates=[selected],
        full_pool_candidates=[other, selected],
    )


def _records(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_flag_off_is_inert_and_on_writes_full_pool(tmp_path, monkeypatch):
    target = tmp_path / "decision_eta_log.jsonl"
    monkeypatch.setattr(dtlog, "LOG_PATH", target)
    monkeypatch.setattr(
        dtlog,
        "_calibration_provenance",
        lambda: {"eta_quantile": {"status": "loaded", "version": 3}},
    )
    dtlog._reset_stats_for_tests()

    monkeypatch.setattr(C, "decision_flag", lambda name: False)
    assert not dtlog.record_pipeline_decision(
        _result(),
        decision_id="shadow_dispatcher:E-1",
        decision_ts=NOW,
        decision_kind="dispatch_selection",
        source="shadow_dispatcher",
        context={"event_id": "E-1", "delivery_address": "forbidden"},
    )
    assert not target.exists()
    assert dtlog.get_stats() == {"written": 0, "errors": 0, "skipped_off": 1}

    monkeypatch.setattr(C, "decision_flag", lambda name: name == dtlog.FLAG)
    assert dtlog.record_pipeline_decision(
        _result(),
        decision_id="shadow_dispatcher:E-1",
        decision_ts=NOW,
        decision_kind="dispatch_selection",
        source="shadow_dispatcher",
        context={"event_id": "E-1", "delivery_address": "forbidden"},
    )
    rows = _records(target)
    assert len(rows) == 1
    row = rows[0]
    assert row["candidate_pool_scope"] == "full_pool_pre_top_n"
    assert row["candidate_count"] == 2
    assert [c["cid"] for c in row["candidates"]] == ["C-1", "C-2"]
    assert row["candidates"][0]["selected"] is True
    assert row["candidates"][0]["position_source"] == "gps_fresh"
    assert row["candidates"][0]["legs"] == [{
        "order_id": "O-1",
        "pickup_eta_at": "2026-07-21T10:25:00+00:00",
        "delivery_eta_at": "2026-07-21T10:45:00+00:00",
        "missing": [],
    }]
    assert row["model"]["lgbm_shadow_versions"] == ["eta-lgbm-7"]
    assert row["calibration"]["eta_quantile"]["version"] == 3
    assert row["context"] == {"event_id": "E-1"}
    serialized = json.dumps(row)
    for forbidden in ("courier_name", "address", "coords", "latitude", "longitude"):
        assert forbidden not in serialized


def test_append_error_is_swallowed_and_counted(tmp_path, monkeypatch):
    monkeypatch.setattr(dtlog, "LOG_PATH", tmp_path / "never-created.jsonl")
    monkeypatch.setattr(C, "decision_flag", lambda name: True)
    monkeypatch.setattr(dtlog, "_calibration_provenance", lambda: {})
    dtlog._reset_stats_for_tests()

    from dispatch_v2.core import jsonl_appender
    monkeypatch.setattr(
        jsonl_appender,
        "append_jsonl_batch",
        lambda *args, **kwargs: (_ for _ in ()).throw(OSError("disk full")),
    )
    result = _result()
    assert not dtlog.record_pipeline_decision(
        result,
        decision_id="shadow_dispatcher:E-2",
        decision_ts=NOW,
        decision_kind="dispatch_selection",
        source="shadow_dispatcher",
    )
    assert result.best.courier_id == "C-1"
    assert dtlog.get_stats() == {"written": 0, "errors": 1, "skipped_off": 0}


def test_plan_commit_writes_one_snapshot_per_order(tmp_path, monkeypatch):
    target = tmp_path / "plans.jsonl"
    monkeypatch.setattr(dtlog, "LOG_PATH", target)
    monkeypatch.setattr(C, "decision_flag", lambda name: True)
    monkeypatch.setattr(dtlog, "_calibration_provenance", lambda: {})
    dtlog._reset_stats_for_tests()
    saved = {
        "plan_version": 8,
        "last_modified_at": NOW.isoformat(),
        "optimization_method": "route_v2",
        "start_pos": {"source": "gps_fresh"},
        "stops": [
            {"order_id": "O-1", "type": "pickup", "predicted_at": "2026-07-21T10:25:00+00:00"},
            {"order_id": "O-1", "type": "dropoff", "predicted_at": "2026-07-21T10:45:00+00:00"},
            {"order_id": "O-2", "type": "pickup", "scheduled_at": "2026-07-21T10:35:00+00:00"},
        ],
    }
    assert dtlog.record_plan_commit("C-9", saved)
    rows = _records(target)
    assert [row["order_id"] for row in rows] == ["O-1", "O-2"]
    assert all(row["selected_cid"] == "C-9" for row in rows)
    assert rows[0]["candidates"][0]["position_source"] == "gps_fresh"
    assert rows[1]["candidates"][0]["legs"][0]["missing"] == [
        "delivery_eta_unavailable"
    ]


def test_calibration_provenance_reports_versions_without_map_content(monkeypatch):
    payloads = {
        calib_maps.ETA_QUANTILE_MAP_PATH: {
            "version": 3, "generated_at": "2026-07-21T00:00:00+00:00",
            "buckets": [{"private": "not-for-record"}],
        },
        calib_maps.PREP_BIAS_MAP_PATH: None,
        calib_maps.ETA_CELL_RESIDUAL_MAP_PATH: {"version": "cell-8"},
    }
    monkeypatch.setattr(
        calib_maps,
        "_load_cached",
        lambda path, cache: payloads[path],
    )
    provenance = calib_maps.calibration_provenance()
    assert provenance["eta_quantile"] == {
        "status": "loaded",
        "version": 3,
        "generated_at": "2026-07-21T00:00:00+00:00",
    }
    assert provenance["restaurant_prep_bias"]["status"] == "missing_or_invalid"
    assert provenance["eta_cell_residual"]["version"] == "cell-8"
    assert "buckets" not in json.dumps(provenance)


def _eta_row(event_id: str, *, source: str = "shadow_dispatcher") -> dict:
    return {
        "schema": "decision_eta.v1",
        "decision_id": f"shadow_dispatcher:{event_id}",
        "decision_ts": NOW.isoformat(),
        "recorded_at": NOW.isoformat(),
        "decision_kind": "dispatch_selection",
        "source": source,
        "order_id": "O-1",
        "selected_cid": "C-1",
        "outcome": "PROPOSE",
        "candidate_pool_scope": "full_pool_pre_top_n",
        "candidate_count": 1,
        "candidates": [{
            "cid": "C-1",
            "selected": True,
            "position_source": "gps_fresh",
            "legs": [{
                "order_id": "O-1",
                "pickup_eta_at": "2026-07-21T10:25:00+00:00",
                "delivery_eta_at": "2026-07-21T10:45:00+00:00",
                "missing": [],
            }],
        }],
        "model": {},
        "calibration": {},
    }


def test_daily_coverage_uses_unique_shadow_decisions_as_denominator(tmp_path):
    decisions = tmp_path / "shadow_decisions.jsonl"
    eta_log = tmp_path / "decision_eta_log.jsonl"
    decisions.write_text("".join(
        json.dumps({"event_id": event, "ts": NOW.isoformat()}) + "\n"
        for event in ("E-1", "E-2")
    ))
    eta_log.write_text(json.dumps(_eta_row("E-1")) + "\n")

    report = decision_eta_coverage.calculate(
        day=date(2026, 7, 21),
        decisions_path=str(decisions),
        eta_log_path=str(eta_log),
    )
    assert report["decision_count"] == 2
    assert report["matched_decisions"] == 1
    assert report["missing_decisions"] == 1
    assert report["coverage"] == 0.5
    assert report["invalid_eta_records"] == 0


def test_contract_registry_rotation_and_source_hooks():
    assert C.ENABLE_DECISION_ETA_LOG is False
    assert "ENABLE_DECISION_ETA_LOG" in C.ETAP4_DECISION_FLAGS
    assert str(dtlog.LOG_PATH) in jsonl_rotation.JSONL_PATHS
    root = Path(__file__).resolve().parents[1]
    expected = {
        "shadow_dispatcher.py": "record_pipeline_decision",
        "czasowka_scheduler.py": "record_candidate_decision",
        "plan_manager.py": "record_plan_commit",
        "tools/reassignment_forward_shadow.py": "record_pipeline_decision",
        "tools/pending_global_resweep.py": "record_pipeline_decision",
    }
    for relative, call in expected.items():
        assert call in (root / relative).read_text(encoding="utf-8")
    assert "decision_eta_log.jsonl" in (
        root / "deploy/dispatch-v2-jsonl-logrotate.conf"
    ).read_text(encoding="utf-8")
