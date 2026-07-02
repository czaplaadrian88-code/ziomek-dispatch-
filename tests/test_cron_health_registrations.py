"""FALA1 watchdog-close — behavioral tests for the stale-threshold registry + CLI.

Closes audyt 2.0 findings A/B at the test layer:
- cod-weekly + the thr=None cron-timers get a real stale threshold (registry -> ledger),
  so the (unmodified) watchdog can actually stale-check them.
- oneshots that record success flip out of the frozen "failed" state and stop being
  flagged stale (the 3 false-"failed" units).
- the stale verdict is polarity-correct (mutation-guarded).

Behavioral (C13): each test drives a fixture ledger and asserts the real
alert/no-alert verdict via watchdog.run_once (the production alerting path) or via
is_stale / scan_stale, never merely the presence of a key.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from dispatch_v2.observability import cron_health, watchdog

UTC = timezone.utc


@pytest.fixture
def ledger(tmp_path):
    """Empty cron_health.json in an isolated tmp dir."""
    return tmp_path / "cron_health.json"


def _set_last_success(path, unit: str, dt: datetime) -> None:
    data = cron_health.load_health(path)
    data["units"][unit]["last_success"] = dt.isoformat()
    cron_health._atomic_write_json(path, data)


# ── (a) registration: cod-weekly + the thr=None cron-timers get a threshold ──────────

def test_sync_thresholds_registers_cod_weekly_and_is_idempotent(ledger):
    changed = cron_health.sync_thresholds(path=ledger)
    data = cron_health.load_health(ledger)

    # cod-weekly is newly registered with the weekly (168h + 24h margin) threshold.
    cw = data["units"]["dispatch-cod-weekly.service"]
    assert cw["expected_max_silence_h"] == 192.0
    assert cw["type"] == "cron_timer"
    assert any("cod-weekly" in c for c in changed)

    # every registry unit is present with its exact threshold.
    for unit, thr in cron_health._DEFAULT_STALE_THRESHOLDS_H.items():
        assert data["units"][unit]["expected_max_silence_h"] == thr

    # idempotent: a second sync changes nothing.
    assert cron_health.sync_thresholds(path=ledger) == []


def test_record_success_backfills_threshold_from_registry(ledger):
    # record_oneshot_success.sh passes NO threshold; the registry must backfill it so
    # the ExecStopPost tick alone is enough to make the unit stale-checkable.
    cron_health.record_run_success("dispatch-downstream-crosscheck.service", path=ledger)
    entry = cron_health.load_health(ledger)["units"]["dispatch-downstream-crosscheck.service"]
    assert entry["expected_max_silence_h"] == 1.0
    assert entry["status"] == "ok"


def test_record_failure_also_backfills_threshold(ledger):
    # A unit that only ever fails must still become stale-checkable.
    cron_health.record_run_failure("dispatch-retro-learning.service", exit_code=1, path=ledger)
    entry = cron_health.load_health(ledger)["units"]["dispatch-retro-learning.service"]
    assert entry["expected_max_silence_h"] == 25.0


# ── (i) cod-weekly stale 9d -> ALERT (through the real watchdog alerting path) ────────

def test_cod_weekly_stale_9d_alerts(ledger, monkeypatch):
    now = datetime(2026, 7, 2, 6, 0, tzinfo=UTC)
    cron_health.sync_thresholds(path=ledger)
    _set_last_success(ledger, "dispatch-cod-weekly.service", now - timedelta(days=9))

    sent: list[str] = []
    monkeypatch.setattr(watchdog, "_send_telegram", lambda text: sent.append(text) or True)

    counters = watchdog.run_once(now=now, path=ledger)

    assert counters["alerted"] >= 1
    assert any("cod-weekly" in t and "STALE" in t for t in sent)


# ── (ii) healthy oneshot with record_run_success -> NO alert ─────────────────────────

def test_healthy_oneshot_no_alert(ledger, monkeypatch):
    # Simulate the ExecStopPost success recorder firing for a just-run oneshot.
    cron_health.record_run_success("dispatch-retro-learning.service", path=ledger)

    sent: list[str] = []
    monkeypatch.setattr(watchdog, "_send_telegram", lambda text: sent.append(text) or True)

    counters = watchdog.run_once(path=ledger)  # now=None -> real now, silence ~0

    assert counters["stale"] == 0
    assert sent == []


def test_false_failed_oneshot_cleared_by_record_success(ledger, monkeypatch):
    """The live-ledger shape: status=failed + last_success=None + old last_updated.

    Before: watchdog would flag it stale (frozen failure). After record_run_success
    (ExecStopPost on the next healthy run): status=ok, fresh, no alert.
    """
    now = datetime(2026, 7, 2, 6, 0, tzinfo=UTC)
    cron_health.sync_thresholds(path=ledger)
    # Freeze it like the real ledger: a failure weeks ago, never a success.
    cron_health.record_run_failure("dispatch-cod-panel-ingest.service", exit_code=1, path=ledger)
    _stale_dt = now - timedelta(days=17)
    data = cron_health.load_health(ledger)
    data["units"]["dispatch-cod-panel-ingest.service"]["last_updated"] = _stale_dt.isoformat()
    cron_health._atomic_write_json(ledger, data)

    sent: list[str] = []
    monkeypatch.setattr(watchdog, "_send_telegram", lambda text: sent.append(text) or True)

    # BEFORE: frozen-failed + old last_updated -> stale verdict fires.
    before = watchdog.run_once(now=now, path=ledger)
    assert any("cod-panel-ingest" in t for t in sent)
    assert before["stale"] >= 1

    # Recorder fires -> success now.
    cron_health.record_run_success("dispatch-cod-panel-ingest.service", path=ledger)
    entry = cron_health.load_health(ledger)["units"]["dispatch-cod-panel-ingest.service"]
    assert entry["status"] == "ok"

    # AFTER: not stale anymore (evaluate at the recorded-now to avoid clock skew).
    assert cron_health.is_stale("dispatch-cod-panel-ingest.service", path=ledger) is False


# ── (iii) threshold edge / boundary ─────────────────────────────────────────────────

def test_threshold_edge_boundary(ledger):
    now = datetime(2026, 7, 2, 6, 0, tzinfo=UTC)
    cron_health.sync_thresholds(path=ledger)

    _set_last_success(ledger, "dispatch-cod-weekly.service", now - timedelta(hours=191.9))
    assert cron_health.is_stale("dispatch-cod-weekly.service", now=now, path=ledger) is False

    _set_last_success(ledger, "dispatch-cod-weekly.service", now - timedelta(hours=192.1))
    assert cron_health.is_stale("dispatch-cod-weekly.service", now=now, path=ledger) is True


# ── mutation guard: verdict polarity (kills a flipped `silence_h > threshold`) ────────

def test_stale_verdict_polarity_mutation_guard(ledger):
    """A stale unit MUST be True and a fresh unit MUST be False at their thresholds.

    Inverting the staleness comparison in is_stale flips BOTH -> both asserts fail
    (mutant killed). See FALA1_watchdog_raport.md for the executed mutation proof.
    """
    now = datetime(2026, 7, 2, 6, 0, tzinfo=UTC)
    cron_health.sync_thresholds(path=ledger)

    _set_last_success(ledger, "dispatch-cod-weekly.service", now - timedelta(hours=300))  # > 192
    _set_last_success(ledger, "dispatch-faza7-kpi.service", now - timedelta(hours=1))       # < 25

    assert cron_health.is_stale("dispatch-cod-weekly.service", now=now, path=ledger) is True
    assert cron_health.is_stale("dispatch-faza7-kpi.service", now=now, path=ledger) is False


# ── dry-run scan mirrors the watchdog verdict (read-only, no writes) ─────────────────

def test_scan_stale_preview_matches_verdict(ledger):
    now = datetime(2026, 7, 2, 6, 0, tzinfo=UTC)
    cron_health.sync_thresholds(path=ledger)
    _set_last_success(ledger, "dispatch-cod-weekly.service", now - timedelta(days=9))

    rows = {r["unit"]: r for r in cron_health.scan_stale(now=now, path=ledger)}
    assert rows["dispatch-cod-weekly.service"]["stale"] is True
    assert rows["dispatch-cod-weekly.service"]["threshold_h"] == 192.0
    # long-running units are never in the scan.
    assert "dispatch-shadow.service" not in rows
