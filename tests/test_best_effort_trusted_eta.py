"""Warunkowe 90/30 dla best-effort Tier 2 przy zaufanym ETA (SHADOW)."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import dispatch_v2.common as C
from dispatch_v2 import dispatch_pipeline as DP
from dispatch_v2 import eta_trust as T
from dispatch_v2 import shadow_dispatcher as SD
from dispatch_v2.tools import best_effort_escalation_report as REPORT


NOW = datetime(2026, 7, 21, 12, 0, tzinfo=timezone.utc)


def _good_evidence(**changes):
    values = dict(
        load_reason="ok",
        courier_delivery_n={"501": 45},
        artifact_model="L2_lgbm",
        artifact_verified=True,
        current_model_verified=True,
        current_model_mae_min=7.4,
        current_model_n=2400,
        rolling_model="L2_lgbm",
        rolling_mae_min=7.6,
        rolling_n=2500,
        evaluated_at="2026-07-21T05:20:00+00:00",
        holdout_cut_day="2026-07-07",
    )
    values.update(changes)
    return T.EtaTrustEvidence(**values)


def _position_metrics(*, source="gps", synthetic=False):
    return {
        "pos_source": source,
        "road_km_from_synthetic_pos": synthetic,
    }


def test_pure_signal_requires_all_three_evidence_classes():
    got = T.eta_trust_metrics("501", _position_metrics(), _good_evidence(), NOW)
    assert got["eta_trust_ok"] is True
    assert got["eta_trust_reason"] == "ok"
    assert got["eta_trust_calib_n"] == 45
    assert got["eta_trust_recent_mae_min"] == 7.6


def test_synthetic_fixture_rejects_even_when_effective_source_looks_known():
    # core.candidates może przepisać źródło na post_wave, ale zachowuje ten
    # marker geometrii. Sam pos_source nie wystarcza do zaufania.
    got = T.eta_trust_metrics(
        "501", _position_metrics(source="post_wave", synthetic=True),
        _good_evidence(), NOW,
    )
    assert got["eta_trust_pos_known"] is True
    assert got["eta_trust_pos_nonsynthetic"] is False
    assert got["eta_trust_ok"] is False
    assert "position_synthetic_or_unproven" in got["eta_trust_reason"]


def test_signal_fails_closed_for_unknown_low_coverage_bad_or_stale_error():
    bad = _good_evidence(
        courier_delivery_n={"501": 29},
        rolling_mae_min=8.01,
        evaluated_at="2026-07-19T00:00:00+00:00",
    )
    got = T.eta_trust_metrics(
        "501", _position_metrics(source="no_gps"), bad, NOW,
    )
    assert got["eta_trust_ok"] is False
    for reason in (
        "position_unknown", "courier_coverage_below_min",
        "recent_error_above_max", "error_evidence_stale",
    ):
        assert reason in got["eta_trust_reason"]


def _write_evidence_files(tmp_path):
    artifact = {
        "schema": "eta_calib_model.v2",
        "leg": "delivery",
        "champion": "L2_lgbm",
        "runtime_model": {
            "kind": "L2_lgbm",
            "cid_map": {"501": 0},
            "courier_history": {"501": {"n_pace": 45}},
        },
    }
    artifact["artifact_sha256"] = T._artifact_hash(artifact)
    delivery = tmp_path / "eta_calib_delivery_map.json"
    delivery.write_text(json.dumps(artifact), encoding="utf-8")
    record = {
        "logged_at": "2026-07-21T05:20:00+00:00",
        "holdout_cut_day": "2026-07-07",
        "decision": {"delivery": {
            "support_exact": True,
            "incumbent": "L2_lgbm",
            "incumbent_mae": 7.4,
            "n_common": 2400,
        }},
        "map_writes": {"delivery": {"champion_written": False}},
        "legs": {"delivery": {
            "champion": "L2_lgbm", "champion_mae": 7.6,
            "n_holdout": 2500,
        }},
    }
    metrics = tmp_path / "eta_calib_metrics.jsonl"
    metrics.write_text(json.dumps(record) + "\n{partial", encoding="utf-8")
    return delivery, metrics


def test_loader_binds_current_artifact_rolling_error_and_courier_coverage(tmp_path):
    delivery, metrics = _write_evidence_files(tmp_path)
    evidence = T.load_eta_trust_evidence(str(delivery), str(metrics))
    assert evidence.load_reason == "ok"
    assert evidence.artifact_verified is True
    assert evidence.current_model_verified is True
    assert evidence.courier_delivery_n == {"501": 45}
    assert evidence.current_model_mae_min == 7.4
    assert evidence.rolling_mae_min == 7.6
    assert T.eta_trust_metrics(
        "501", _position_metrics(), evidence, NOW,
    )["eta_trust_ok"] is True


def test_loader_rejects_artifact_integrity_mutation(tmp_path):
    delivery, metrics = _write_evidence_files(tmp_path)
    payload = json.loads(delivery.read_text(encoding="utf-8"))
    payload["runtime_model"]["courier_history"]["501"]["n_pace"] = 999
    delivery.write_text(json.dumps(payload), encoding="utf-8")
    evidence = T.load_eta_trust_evidence(str(delivery), str(metrics))
    got = T.eta_trust_metrics("501", _position_metrics(), evidence, NOW)
    assert evidence.artifact_verified is False
    assert got["eta_trust_ok"] is False
    assert "calibration_artifact_unavailable" in got["eta_trust_reason"]


@dataclass
class _Cand:
    courier_id: str = "501"
    metrics: dict = field(default_factory=dict)
    plan: object = None
    name: str = "Test"
    score: float = 10.0
    feasibility_verdict: str = "NO"
    feasibility_reason: str = "test"
    best_effort: bool = True


def _candidate(*, synthetic=False):
    metrics = {
        **_position_metrics(source="post_wave" if synthetic else "gps",
                            synthetic=synthetic),
        "free_at_min": 60.0,
        "bag_size_before": 1,
        "objm_r6_breach_max_min": 1.0,
        "late_pickup_committed_max": 0.0,
        "new_pickup_late_min": 0.0,
        "sum_bag_time_min": 20.0,
    }
    plan = SimpleNamespace(per_order_delivery_times={"NEW": 20.0})
    return _Cand(metrics=metrics, plan=plan)


def _flags(monkeypatch, *, trust_on):
    monkeypatch.setattr(
        C, "decision_flag",
        lambda name: bool(trust_on) if name == "ENABLE_BEST_EFFORT_ESC_TRUSTED_ETA" else False,
    )
    monkeypatch.setattr(
        C, "flag",
        lambda name, default=None: 90.0
        if name == "BEST_EFFORT_ESC_TIER2_MAX_FREE_MIN" else default,
    )


def test_off_is_legacy_90_and_does_not_touch_eta_metrics(monkeypatch):
    _flags(monkeypatch, trust_on=False)
    monkeypatch.setattr(
        T, "load_eta_trust_evidence",
        lambda: (_ for _ in ()).throw(AssertionError("OFF nie może czytać artefaktów")),
    )
    cand = _candidate(synthetic=True)
    DP._best_effort_objm_shadow([cand], cand, "NEW", now=NOW)
    assert cand.metrics["best_effort_objm_esc_max_free"] == 90.0
    assert cand.metrics["best_effort_objm_esc_tier"] == 2
    assert not any(key.startswith("eta_trust_") for key in cand.metrics)


def test_on_good_signal_keeps_90(monkeypatch):
    _flags(monkeypatch, trust_on=True)
    monkeypatch.setattr(T, "load_eta_trust_evidence", _good_evidence)
    cand = _candidate()
    DP._best_effort_objm_shadow([cand], cand, "NEW", now=NOW)
    assert cand.metrics["eta_trust_ok"] is True
    assert cand.metrics["best_effort_objm_esc_max_free"] == 90.0
    assert cand.metrics["best_effort_objm_esc_tier"] == 2


def test_on_not_equal_off_and_mutation_direction_bad_signal_means_30(monkeypatch):
    _flags(monkeypatch, trust_on=True)
    monkeypatch.setattr(
        T, "load_eta_trust_evidence",
        lambda: _good_evidence(rolling_mae_min=8.01),
    )
    cand = _candidate()
    DP._best_effort_objm_shadow([cand], cand, "NEW", now=NOW)
    assert cand.metrics["eta_trust_ok"] is False
    # Mutation-probe kierunku: odwrócenie gałęzi lub pozostawienie 90 zabija test.
    assert cand.metrics["best_effort_objm_esc_max_free"] == 30.0
    assert cand.metrics["best_effort_objm_esc_tier"] == 3


def test_on_synthetic_position_falls_back_to_30(monkeypatch):
    _flags(monkeypatch, trust_on=True)
    monkeypatch.setattr(T, "load_eta_trust_evidence", _good_evidence)
    cand = _candidate(synthetic=True)
    DP._best_effort_objm_shadow([cand], cand, "NEW", now=NOW)
    assert cand.metrics["eta_trust_pos_known"] is True
    assert cand.metrics["eta_trust_pos_nonsynthetic"] is False
    assert cand.metrics["best_effort_objm_esc_max_free"] == 30.0


def test_eta_trust_telemetry_reaches_serializer_a_and_b(monkeypatch):
    _flags(monkeypatch, trust_on=True)
    monkeypatch.setattr(T, "load_eta_trust_evidence", _good_evidence)
    produced = _candidate()
    DP._best_effort_objm_shadow([produced], produced, "NEW", now=NOW)
    eta_metrics = {
        key: value for key, value in produced.metrics.items()
        if key.startswith("eta_trust_")
    }
    serial = _Cand(metrics=dict(eta_metrics), plan=None)
    out_a = SD._serialize_candidate(serial)
    result = DP.PipelineResult(
        order_id="NEW", verdict="KOORD", reason="test", best=serial,
        candidates=[serial], pickup_ready_at=None, restaurant="Test",
    )
    out_b = SD._serialize_result(result, "evt", 1.0)["best"]
    assert eta_metrics
    for key, value in eta_metrics.items():
        assert out_a[key] == value
        assert out_b[key] == value


def test_flag_default_off_etap4_and_lifecycle_shadow():
    assert C.ENABLE_BEST_EFFORT_ESC_TRUSTED_ETA is False
    assert "ENABLE_BEST_EFFORT_ESC_TRUSTED_ETA" in C.ETAP4_DECISION_FLAGS
    registry = json.loads(
        (Path(__file__).parents[1] / "tools/flag_lifecycle_registry.json")
        .read_text(encoding="utf-8")
    )["flags"]["ENABLE_BEST_EFFORT_ESC_TRUSTED_ETA"]
    assert registry["default"] is False
    assert registry["lifecycle"] == "shadow"


def test_report_keeps_conditional_30_instead_of_hiding_fail_closed(tmp_path):
    def record(max_free, *, trust_marker=None):
        best = {
            "best_effort_objm_esc_tier": 3,
            "best_effort_objm_esc_max_free": max_free,
        }
        if trust_marker is not None:
            best.update(eta_trust_ok=trust_marker, eta_trust_reason="bad")
        return {"ts": "2026-07-21T12:00:00+00:00", "best": best}

    log = tmp_path / "shadow.jsonl"
    log.write_text("\n".join(json.dumps(row) for row in (
        record(90),                    # legacy — zostaje
        record(30, trust_marker=False),  # nowy fail-closed — MUSI zostać
        record(40),                    # obca historyczna konfiguracja — filtr
    )) + "\n", encoding="utf-8")
    rows = REPORT.collect_rows(str(log))
    assert [row["esc_max"] for row in rows] == [90.0, 30.0]
