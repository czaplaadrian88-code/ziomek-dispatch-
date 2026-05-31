"""MP-#14 /health/all aggregator tests (2026-05-08).

Verifies worst-status-wins logic + 6-component aggregation:
parser, downstream, reconciliation, cron_timers, shadow_worker, events_pipeline.
"""
import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from dispatch_v2.parser_health_endpoint import (  # noqa: E402
    _mp14_build_all_snapshot,
    _mp14_load_cron_summary,
    _mp14_worst_status,
    _MP14_STATUS_PRIORITY,
)


# ---- worst-status-wins unit tests ----

def test_worst_status_all_ok():
    assert _mp14_worst_status(["ok", "ok", "healthy", "active"]) == "ok"


def test_worst_status_stale_promotes_to_degraded():
    assert _mp14_worst_status(["ok", "stale", "ok"]) == "degraded"


def test_worst_status_critical_dominates():
    assert _mp14_worst_status(["ok", "degraded", "critical"]) == "critical"


def test_worst_status_unknown_treated_as_ok():
    assert _mp14_worst_status(["unknown", "ok"]) == "ok"


def test_worst_status_empty_returns_ok():
    assert _mp14_worst_status([]) == "ok"


def test_worst_status_priority_table_complete():
    """Sanity: every key musi mieć priority 0-4."""
    for status, prio in _MP14_STATUS_PRIORITY.items():
        assert 0 <= prio <= 4, f"{status}={prio} out of range"


# ---- cron_summary z mock JSON ----

def test_cron_summary_all_units_ok():
    """15 units z status=ok → status=ok, no stale/failed."""
    fake_data = {
        "_meta": {"schema_version": 1, "last_write_ts": "2026-05-08T16:00:00+00:00"},
        "units": {
            f"unit-{i}": {
                "status": "ok",
                "type": "cron_timer",
                "expected_max_silence_h": 24.0,
                "last_success": datetime.now(timezone.utc).isoformat(),
                "consecutive_failures": 0,
            }
            for i in range(15)
        },
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(fake_data, f)
        tmp_path = f.name
    try:
        with patch("dispatch_v2.observability.cron_health.CRON_HEALTH_PATH", tmp_path):
            summary = _mp14_load_cron_summary()
            assert summary["status"] == "ok"
            assert summary["units_count"] == 15
            assert summary["stale_units"] == []
            assert summary["failed_units"] == []
    finally:
        os.unlink(tmp_path)


def test_cron_summary_stale_unit_degraded():
    """Unit z last_success > expected_max_silence_h → stale, status=degraded."""
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    fake_data = {
        "_meta": {"schema_version": 1, "last_write_ts": "2026-05-08T16:00:00+00:00"},
        "units": {
            "ok-unit": {
                "status": "ok", "type": "cron_timer", "expected_max_silence_h": 24.0,
                "last_success": datetime.now(timezone.utc).isoformat(),
                "consecutive_failures": 0,
            },
            "stale-unit": {
                "status": "ok", "type": "cron_timer", "expected_max_silence_h": 24.0,
                "last_success": old_ts, "consecutive_failures": 0,
            },
        },
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(fake_data, f)
        tmp_path = f.name
    try:
        with patch("dispatch_v2.observability.cron_health.CRON_HEALTH_PATH", tmp_path):
            summary = _mp14_load_cron_summary()
            assert summary["status"] == "degraded"
            assert "stale-unit" in summary["stale_units"]
            assert "ok-unit" not in summary["stale_units"]
            assert "stale_units=" in summary["reason"]
    finally:
        os.unlink(tmp_path)


def test_cron_summary_failed_unit_degraded():
    """Unit z consecutive_failures >= 3 → failed_units, status=degraded."""
    fake_data = {
        "_meta": {"schema_version": 1, "last_write_ts": "2026-05-08T16:00:00+00:00"},
        "units": {
            "broken-unit": {
                "status": "failed", "type": "cron_timer", "expected_max_silence_h": 24.0,
                "last_success": None, "consecutive_failures": 5,
            },
        },
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(fake_data, f)
        tmp_path = f.name
    try:
        with patch("dispatch_v2.observability.cron_health.CRON_HEALTH_PATH", tmp_path):
            summary = _mp14_load_cron_summary()
            assert summary["status"] == "degraded"
            assert "broken-unit" in summary["failed_units"]
            assert "failed_units=" in summary["reason"]
    finally:
        os.unlink(tmp_path)


def test_cron_summary_long_running_skip_stale_check():
    """type=long_running NIE liczy się jako stale (continuous, NIE timer)."""
    old_ts = (datetime.now(timezone.utc) - timedelta(hours=48)).isoformat()
    fake_data = {
        "_meta": {"schema_version": 1, "last_write_ts": "2026-05-08T16:00:00+00:00"},
        "units": {
            "long-svc": {
                "status": "active", "type": "long_running",
                "expected_max_silence_h": 1.0,
                "last_success": old_ts, "consecutive_failures": 0,
            },
        },
    }
    with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
        json.dump(fake_data, f)
        tmp_path = f.name
    try:
        with patch("dispatch_v2.observability.cron_health.CRON_HEALTH_PATH", tmp_path):
            summary = _mp14_load_cron_summary()
            assert summary["status"] == "ok"
            assert summary["stale_units"] == []
    finally:
        os.unlink(tmp_path)


# ---- _mp14_build_all_snapshot integration ----

def _make_parser_snap(downstream_status="ok", downstream_reason=None,
                     status="healthy", anomaly_reason=None,
                     worker_age=30.0, failed_1h=0, new_orders_1h=10,
                     last_propose_age=120.0, pending_new_orders=None):
    return {
        "status": status,
        "anomaly_reason": anomaly_reason,
        "downstream_status": downstream_status,
        "downstream_reason": downstream_reason,
        "worker_processed_age_sec": worker_age,
        "events_failed_last_1h_count": failed_1h,
        "new_orders_last_1h_count": new_orders_1h,
        "last_proposal_sent_age_sec": last_propose_age,
        "pending_new_orders": pending_new_orders,
    }


def test_build_all_snapshot_healthy():
    """Wszystkie sygnały OK → overall_status=ok."""
    snap = _mp14_build_all_snapshot(_make_parser_snap())
    assert snap["overall_status"] == "ok"
    assert snap["overall_reason"] is None
    assert snap["endpoint_version"] == "1"
    assert "components" in snap
    assert set(snap["components"].keys()) == {
        "parser", "downstream", "reconciliation", "cron_timers",
        "shadow_worker", "events_pipeline"
    }


def test_build_all_snapshot_critical_worker_stuck():
    """worker_age > slow*2 (1200s default) → shadow_worker=critical → overall=critical."""
    snap = _mp14_build_all_snapshot(_make_parser_snap(worker_age=1500.0))
    assert snap["overall_status"] == "critical"
    assert snap["components"]["shadow_worker"]["status"] == "critical"
    assert snap["components"]["shadow_worker"]["reason"] == "worker_stuck"
    assert "shadow_worker" in snap["overall_reason"]


def test_build_all_snapshot_sparse_traffic_worker_NOT_critical():
    """2026-05-31 (#160): worker_age=1500s + new_orders_1h=10 ALE pending_new_orders=0
    → shadow_worker NIE critical. Spójnie z /health/parser — off-peak sparse traffic
    (zlecenia z ostatniej h już przetworzone) nie jest worker_stuck."""
    snap = _mp14_build_all_snapshot(
        _make_parser_snap(worker_age=1500.0, new_orders_1h=10, pending_new_orders=0)
    )
    assert snap["components"]["shadow_worker"]["status"] == "ok"
    assert snap["components"]["shadow_worker"]["reason"] is None


def test_build_all_snapshot_real_backlog_worker_stuck():
    """2026-05-31 (#160): worker_age=1500s + pending_new_orders=3 (realna zaległość)
    → shadow_worker=critical worker_stuck. Bramka pending wciąż łapie prawdziwą awarię."""
    snap = _mp14_build_all_snapshot(
        _make_parser_snap(worker_age=1500.0, pending_new_orders=3)
    )
    assert snap["components"]["shadow_worker"]["status"] == "critical"
    assert snap["components"]["shadow_worker"]["reason"] == "worker_stuck"


def test_build_all_snapshot_degraded_failed_events():
    """events_failed_1h > threshold (5) → events_pipeline=degraded → overall=degraded."""
    snap = _mp14_build_all_snapshot(_make_parser_snap(failed_1h=10))
    assert snap["overall_status"] == "degraded"
    assert snap["components"]["events_pipeline"]["status"] == "degraded"
    assert snap["components"]["events_pipeline"]["reason"] == "elevated_failure_rate"


def test_build_all_snapshot_critical_pipeline_silent():
    """last_propose_age > 30min AND new_orders > 0 → events_pipeline=critical."""
    snap = _mp14_build_all_snapshot(_make_parser_snap(
        last_propose_age=2000.0, new_orders_1h=15,
    ))
    assert snap["components"]["events_pipeline"]["status"] == "critical"
    assert snap["components"]["events_pipeline"]["reason"] == "pipeline_silent_despite_work"
    assert snap["overall_status"] == "critical"


def test_build_all_snapshot_no_worker_heartbeat():
    """worker_age=None → shadow_worker=unknown (NIE error)."""
    snap = _mp14_build_all_snapshot(_make_parser_snap(worker_age=None))
    assert snap["components"]["shadow_worker"]["status"] == "unknown"
    assert snap["components"]["shadow_worker"]["age_sec"] is None
    # unknown nie eskaluje overall — overall pozostaje ok jeśli reszta zdrowa
    assert snap["overall_status"] == "ok"


def test_build_all_snapshot_downstream_critical_propagated():
    """parser_snapshot.downstream_status=critical → component + overall=critical."""
    snap = _mp14_build_all_snapshot(_make_parser_snap(
        downstream_status="critical", downstream_reason="pipeline_silent_despite_work",
    ))
    assert snap["components"]["downstream"]["status"] == "critical"
    assert snap["overall_status"] == "critical"
    assert "downstream" in (snap["overall_reason"] or "")


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"FAIL {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{passed+failed} passed")
    sys.exit(0 if failed == 0 else 1)
