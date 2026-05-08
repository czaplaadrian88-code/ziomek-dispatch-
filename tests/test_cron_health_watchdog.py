"""Tests dla observability.cron_health + alert_onfailure + watchdog (MP-#4).

Master plan TOP-15 #4 acceptance criteria:
- record_run_success — writes ts, resets consecutive_failures
- record_run_failure — increments consecutive_failures
- is_stale — threshold calculation correct
- atomic_write_resists_partial_fail
- alert_dedup_30min
- watchdog_run_once_idempotent
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from dispatch_v2.observability import cron_health, watchdog
from dispatch_v2.observability.cron_health import (
    is_alert_dedup_active,
    is_stale,
    load_health,
    record_alert_sent,
    record_run_failure,
    record_run_success,
)


@pytest.fixture
def tmp_health_path(tmp_path):
    """Empty cron_health.json w tmp_path."""
    p = tmp_path / "cron_health.json"
    return p


def test_record_run_success_writes_ts(tmp_health_path):
    record_run_success("dispatch-test", expected_max_silence_h=24.0, path=tmp_health_path)

    data = json.loads(tmp_health_path.read_text(encoding="utf-8"))
    entry = data["units"]["dispatch-test"]
    assert entry["last_success"] is not None
    assert entry["consecutive_failures"] == 0
    assert entry["status"] == "ok"
    assert entry["expected_max_silence_h"] == 24.0


def test_record_run_failure_increments_consecutive(tmp_health_path):
    record_run_failure("dispatch-test", result="failed", exit_code=1, path=tmp_health_path)
    record_run_failure("dispatch-test", result="failed", exit_code=1, path=tmp_health_path)
    record_run_failure("dispatch-test", result="timeout", exit_code=143, path=tmp_health_path)

    data = json.loads(tmp_health_path.read_text(encoding="utf-8"))
    entry = data["units"]["dispatch-test"]
    assert entry["consecutive_failures"] == 3
    assert entry["last_failure_result"] == "timeout"
    assert entry["last_failure_exit"] == 143
    assert entry["status"] == "failed"


def test_record_run_success_resets_consecutive(tmp_health_path):
    record_run_failure("dispatch-test", path=tmp_health_path)
    record_run_failure("dispatch-test", path=tmp_health_path)
    record_run_success("dispatch-test", path=tmp_health_path)

    data = json.loads(tmp_health_path.read_text(encoding="utf-8"))
    entry = data["units"]["dispatch-test"]
    assert entry["consecutive_failures"] == 0
    assert entry["status"] == "ok"


def test_is_stale_threshold_calculation(tmp_health_path):
    # Mock: ostatni sukces 26h temu, threshold 25h → stale
    fake_now = datetime(2026, 5, 8, 6, 0, 0, tzinfo=timezone.utc)
    last_success = (fake_now - timedelta(hours=26)).isoformat()

    record_run_success("dispatch-test", expected_max_silence_h=25.0, path=tmp_health_path)
    # Manually overwrite last_success do testu
    data = load_health(tmp_health_path)
    data["units"]["dispatch-test"]["last_success"] = last_success
    cron_health._atomic_write_json(tmp_health_path, data)

    assert is_stale("dispatch-test", expected_max_silence_h=25.0, now=fake_now, path=tmp_health_path) is True
    assert is_stale("dispatch-test", expected_max_silence_h=27.0, now=fake_now, path=tmp_health_path) is False


def test_is_stale_long_running_returns_false(tmp_health_path):
    record_run_success(
        "dispatch-shadow",
        unit_type="long_running",
        path=tmp_health_path,
    )

    # Even with very small threshold, long_running NEVER stale
    assert is_stale("dispatch-shadow", expected_max_silence_h=0.001, path=tmp_health_path) is False


def test_is_stale_unknown_unit_returns_false(tmp_health_path):
    assert is_stale("never-registered", expected_max_silence_h=1.0, path=tmp_health_path) is False


def test_atomic_write_resists_partial_fail(tmp_health_path):
    """Mock OSError podczas json.dump → original preserved + no orphan tempfiles."""
    record_run_success("dispatch-test", path=tmp_health_path)
    original_content = tmp_health_path.read_text(encoding="utf-8")

    with patch(
        "dispatch_v2.observability.cron_health.json.dump",
        side_effect=OSError("disk full"),
    ):
        with pytest.raises(OSError, match="disk full"):
            record_run_failure("dispatch-test", path=tmp_health_path)

    # Original preserved
    assert tmp_health_path.read_text(encoding="utf-8") == original_content
    # No orphan tempfiles
    orphans = list(tmp_health_path.parent.glob(".cron_health_*"))
    assert orphans == []


def test_alert_dedup_30min(tmp_health_path):
    fake_now = datetime(2026, 5, 8, 6, 0, 0, tzinfo=timezone.utc)
    record_alert_sent("dispatch-test", path=tmp_health_path)
    # Manually set last_alert_ts do 25min ago
    data = load_health(tmp_health_path)
    data["units"]["dispatch-test"]["last_alert_ts"] = (fake_now - timedelta(minutes=25)).isoformat()
    cron_health._atomic_write_json(tmp_health_path, data)

    assert is_alert_dedup_active("dispatch-test", dedup_window_min=30, now=fake_now, path=tmp_health_path) is True
    assert is_alert_dedup_active("dispatch-test", dedup_window_min=20, now=fake_now, path=tmp_health_path) is False


def test_alert_dedup_no_history_returns_false(tmp_health_path):
    record_run_success("dispatch-test", path=tmp_health_path)
    # last_alert_ts None
    assert is_alert_dedup_active("dispatch-test", path=tmp_health_path) is False


def test_watchdog_run_once_skips_long_running(tmp_health_path, monkeypatch):
    """Long-running services nie biorą udziału w stale check."""
    # Pass tmp_health_path explicit do run_once (workaround dla Python default-arg binding)
    pass  # monkeypatch nie potrzebny, run_once przyjmuje path arg

    record_run_success(
        "dispatch-shadow",
        unit_type="long_running",
        path=tmp_health_path,
    )

    fake_now = datetime(2026, 5, 8, 6, 0, 0, tzinfo=timezone.utc)
    counters = watchdog.run_once(now=fake_now, path=tmp_health_path)

    assert counters["checked"] == 0  # long_running skipped
    assert counters["stale"] == 0


def test_watchdog_run_once_alerts_stale_unit(tmp_health_path, monkeypatch):
    monkeypatch.setattr(cron_health, "CRON_HEALTH_PATH", tmp_health_path)
    monkeypatch.setattr(watchdog.cron_health, "CRON_HEALTH_PATH", tmp_health_path)

    fake_now = datetime(2026, 5, 8, 6, 0, 0, tzinfo=timezone.utc)
    last_success = (fake_now - timedelta(hours=30)).isoformat()

    record_run_success(
        "dispatch-overrides-reset.service",
        unit_type="cron_timer",
        expected_max_silence_h=25.0,
        path=tmp_health_path,
    )
    data = load_health(tmp_health_path)
    data["units"]["dispatch-overrides-reset.service"]["last_success"] = last_success
    cron_health._atomic_write_json(tmp_health_path, data)

    sent_alerts = []
    def _capture(text):
        sent_alerts.append(text)
        return True

    monkeypatch.setattr(watchdog, "_send_telegram", _capture)

    counters = watchdog.run_once(now=fake_now, path=tmp_health_path)

    assert counters["checked"] == 1
    assert counters["stale"] == 1
    assert counters["alerted"] == 1
    assert len(sent_alerts) == 1
    assert "STALE" in sent_alerts[0]
    assert "dispatch-overrides-reset" in sent_alerts[0]
    assert "30.0" in sent_alerts[0]  # hours silent


def test_watchdog_run_once_idempotent_dedup(tmp_health_path, monkeypatch):
    """Drugi run w 30-min window NIE alertuje (dedup)."""
    fake_now = datetime(2026, 5, 8, 6, 0, 0, tzinfo=timezone.utc)
    # Mock _now_iso, aby record_alert_sent zapisał fake_now (NIE real now)
    monkeypatch.setattr(cron_health, "_now_iso", lambda: fake_now.isoformat())

    last_success = (fake_now - timedelta(hours=30)).isoformat()

    record_run_success(
        "dispatch-overrides-reset.service",
        unit_type="cron_timer",
        expected_max_silence_h=25.0,
        path=tmp_health_path,
    )
    data = load_health(tmp_health_path)
    data["units"]["dispatch-overrides-reset.service"]["last_success"] = last_success
    cron_health._atomic_write_json(tmp_health_path, data)

    monkeypatch.setattr(watchdog, "_send_telegram", lambda text: True)

    # First run — alerts
    counters_1 = watchdog.run_once(now=fake_now, path=tmp_health_path)
    assert counters_1["alerted"] == 1

    # Second run 5min later — dedup skip
    fake_now_2 = fake_now + timedelta(minutes=5)
    counters_2 = watchdog.run_once(now=fake_now_2, path=tmp_health_path)

    assert counters_2["stale"] == 1
    assert counters_2["dedup_skipped"] == 1
    assert counters_2["alerted"] == 0


def test_alert_onfailure_format_includes_8_fields(tmp_health_path, monkeypatch):
    """Alert text powinien zawierać kluczowe 8 fields per master plan AC."""
    from dispatch_v2.observability import alert_onfailure

    monkeypatch.setattr(cron_health, "CRON_HEALTH_PATH", tmp_health_path)
    monkeypatch.setattr(alert_onfailure.cron_health, "CRON_HEALTH_PATH", tmp_health_path)

    # Mock systemctl + journalctl
    monkeypatch.setattr(alert_onfailure, "_systemctl_status", lambda u: ("failed", 1))
    monkeypatch.setattr(alert_onfailure, "_journal_tail", lambda u, n=20: "FAKE_LOG_LINE_1\nFAKE_LOG_LINE_2")

    record_run_success("dispatch-overrides-reset.service", path=tmp_health_path)

    text = alert_onfailure._format_alert("dispatch-overrides-reset.service")

    # 8 fields per master plan: Service / Result / Exit / LastSuccess / Logs / Playbook / Severity / Hint
    assert "Service:" in text
    assert "Result:" in text
    assert "Exit:" in text
    assert "Ostatni sukces" in text  # LastSuccess in PL
    assert "FAKE_LOG_LINE_1" in text  # Logs
    assert "Runbook" in text  # Playbook
    assert "P1" in text or "P2" in text  # Severity
    assert "Co robić" in text  # Hint


def test_alert_onfailure_dedup_second_call_skips(tmp_health_path, monkeypatch):
    """Drugi alert dla tego samego unitu w 30-min window NIE wysyła Telegram."""
    from dispatch_v2.observability import alert_onfailure

    # Override module-level CRON_HEALTH_PATH (alert_onfailure czyta przy każdym call)
    monkeypatch.setattr(cron_health, "CRON_HEALTH_PATH", tmp_health_path)
    monkeypatch.setattr(alert_onfailure.cron_health, "CRON_HEALTH_PATH", tmp_health_path)
    monkeypatch.setattr(alert_onfailure, "_systemctl_status", lambda u: ("failed", 1))
    monkeypatch.setattr(alert_onfailure, "_journal_tail", lambda u, n=20: "log")

    sent = []
    monkeypatch.setattr(alert_onfailure, "_send_telegram", lambda t: sent.append(t) or True)

    # First call — alerts
    rc1 = alert_onfailure.main(["dispatch-test.service"])
    assert rc1 == 0
    assert len(sent) == 1

    # Second call — dedup skip
    rc2 = alert_onfailure.main(["dispatch-test.service"])
    assert rc2 == 0  # graceful skip
    assert len(sent) == 1  # NIE drugie sent


def test_load_health_corrupt_file_returns_empty(tmp_health_path):
    """Defensive: corrupt JSON → empty schema (caller decyduje)."""
    tmp_health_path.write_text("{ NOT VALID JSON", encoding="utf-8")
    data = load_health(tmp_health_path)
    assert data["units"] == {}
    assert data["_meta"]["schema_version"] == cron_health.SCHEMA_VERSION
