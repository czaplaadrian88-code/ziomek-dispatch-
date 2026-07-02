"""cron-health-truth (FALA-2) — systemd truth cross-check kills false-"failed".

Audyt 2.0 motyw #2: the failure-only ledger freezes a unit as "failed"/stale when its
success recorder never fired, so the watchdog emits a FALSE stale alert for a unit that
systemd knows ran fine today. These tests drive `is_stale` / `scan_stale` /
`watchdog.run_once` (the real alerting path) with a FAKE systemd runner (never the real
`systemctl`) and assert the alert/no-alert verdict — behavioral (C13), plus two
mutation guards (polarity of the rescue).

The systemd boundary is `cron_health._run_systemctl`; every test monkeypatches it, so no
test shells out. Suppression is opt-in here (`use_systemd=True` or the env var) because
under pytest the default is OFF (hermetic; see test_systemd_disabled_by_default).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from dispatch_v2.observability import cron_health, watchdog

UTC = timezone.utc
NOW = datetime(2026, 7, 2, 6, 0, tzinfo=UTC)


# ── fake systemd ─────────────────────────────────────────────────────────────────────

def _ts(dt: datetime | None) -> str:
    return f"@{int(dt.timestamp())}" if dt is not None else ""


def _show(
    *,
    load: str = "loaded",
    active: str = "inactive",
    result: str = "success",
    exit_status: str = "0",
    exec_exit: datetime | None = None,
    active_enter: datetime | None = None,
    typ: str = "oneshot",
) -> str:
    """Render a `systemctl show --timestamp=unix` stdout block."""
    props = {
        "Type": typ,
        "LoadState": load,
        "ActiveState": active,
        "Result": result,
        "ExecMainStatus": exit_status,
        "ExecMainExitTimestamp": _ts(exec_exit),
        "ActiveEnterTimestamp": _ts(active_enter),
    }
    return "\n".join(f"{k}={v}" for k, v in props.items())


def _runner(mapping: dict[str, str | None]):
    """Build a fake `_run_systemctl`. Value None → systemctl unavailable; a unit absent
    from the mapping → a not-found unit (LoadState=not-found)."""
    def runner(args, timeout):  # noqa: ANN001 - matches _run_systemctl signature
        unit = args[-1]
        if unit not in mapping:
            return (0, _show(load="not-found"))
        out = mapping[unit]
        if out is None:
            return None
        return (0, out)
    return runner


@pytest.fixture
def ledger(tmp_path):
    return tmp_path / "cron_health.json"


def _seed_frozen_failed(path, unit: str, threshold: float, *, failed_days_ago: int = 17):
    """Recreate the pre-FALA-1 live shape: status=failed, last_success=None, old ts."""
    cron_health.record_run_failure(unit, exit_code=1, path=path)
    data = cron_health.load_health(path)
    e = data["units"][unit]
    e["expected_max_silence_h"] = threshold
    e["last_success"] = None
    e["last_updated"] = (NOW - timedelta(days=failed_days_ago)).isoformat()
    cron_health._atomic_write_json(path, data)


# ── core: ON ≠ OFF on the exact false-positive class ─────────────────────────────────

def test_frozen_failed_healthy_oneshot_rescued_on_not_off(ledger, monkeypatch):
    unit = "dispatch-retro-learning.service"
    _seed_frozen_failed(ledger, unit, 25.0)
    monkeypatch.setattr(
        cron_health, "_run_systemctl",
        _runner({unit: _show(active="inactive", result="success", exit_status="0",
                             exec_exit=NOW - timedelta(hours=1))}),
    )
    # OFF: pure failure-only ledger → stale (the false positive the watchdog acts on).
    assert cron_health.is_stale(unit, now=NOW, path=ledger, use_systemd=False) is True
    # ON: systemd confirms a recent clean run the ledger missed → not stale.
    assert cron_health.is_stale(unit, now=NOW, path=ledger, use_systemd=True) is False


def test_watchdog_no_false_alert_when_systemd_healthy(ledger, monkeypatch):
    unit = "dispatch-cod-panel-ingest.service"
    _seed_frozen_failed(ledger, unit, 192.0)
    monkeypatch.setenv("CRON_HEALTH_SYSTEMD_TRUTH", "1")
    monkeypatch.setattr(
        cron_health, "_run_systemctl",
        _runner({unit: _show(active="inactive", result="success", exit_status="0",
                             exec_exit=NOW - timedelta(hours=2))}),
    )
    sent: list[str] = []
    monkeypatch.setattr(watchdog, "_send_telegram", lambda t: sent.append(t) or True)

    counters = watchdog.run_once(now=NOW, path=ledger)

    assert counters["stale"] == 0
    assert sent == []


# ── safety: a genuinely-failed, actually-stale unit MUST still alert ──────────────────

def test_genuinely_failed_unit_still_alerts(ledger, monkeypatch):
    unit = "dispatch-cod-weekly.service"
    _seed_frozen_failed(ledger, unit, 192.0, failed_days_ago=13)  # 312h > 192h
    monkeypatch.setenv("CRON_HEALTH_SYSTEMD_TRUTH", "1")
    monkeypatch.setattr(
        cron_health, "_run_systemctl",
        _runner({unit: _show(active="failed", result="exit-code", exit_status="1")}),
    )
    sent: list[str] = []
    monkeypatch.setattr(watchdog, "_send_telegram", lambda t: sent.append(t) or True)

    counters = watchdog.run_once(now=NOW, path=ledger)

    assert counters["stale"] >= 1
    assert any("cod-weekly" in t and "STALE" in t for t in sent)


# ── fail-safe / edge cases ───────────────────────────────────────────────────────────

def test_systemctl_unavailable_falls_back_to_ledger(ledger, monkeypatch):
    unit = "dispatch-retro-learning.service"
    _seed_frozen_failed(ledger, unit, 25.0)
    monkeypatch.setattr(cron_health, "_run_systemctl", _runner({unit: None}))
    # systemctl missing/timeout → keep the ledger verdict (do not silently clear).
    assert cron_health.is_stale(unit, now=NOW, path=ledger, use_systemd=True) is True


def test_not_loaded_unit_not_rescued(ledger, monkeypatch):
    unit = "dispatch-retro-learning.service"
    _seed_frozen_failed(ledger, unit, 25.0)
    # A not-found unit reports Result=success by default; LoadState guards that.
    monkeypatch.setattr(
        cron_health, "_run_systemctl",
        _runner({unit: _show(load="not-found", active="inactive", result="success")}),
    )
    assert cron_health.is_stale(unit, now=NOW, path=ledger, use_systemd=True) is True


def test_systemd_success_older_than_threshold_not_rescued(ledger, monkeypatch):
    unit = "dispatch-retro-learning.service"
    _seed_frozen_failed(ledger, unit, 25.0)
    # Clean run, but 30h ago > 25h threshold → the systemd success is itself stale.
    monkeypatch.setattr(
        cron_health, "_run_systemctl",
        _runner({unit: _show(active="inactive", result="success", exit_status="0",
                             exec_exit=NOW - timedelta(hours=30))}),
    )
    assert cron_health.is_stale(unit, now=NOW, path=ledger, use_systemd=True) is True


def test_currently_active_without_timestamp_rescued(ledger, monkeypatch):
    unit = "dispatch-downstream-crosscheck.service"
    _seed_frozen_failed(ledger, unit, 1.0)
    monkeypatch.setattr(
        cron_health, "_run_systemctl",
        _runner({unit: _show(active="active", result="success", exit_status="0",
                             active_enter=None, typ="simple")}),
    )
    # Running right now → definitely not stale (freshness anchors on "now").
    assert cron_health.is_stale(unit, now=NOW, path=ledger, use_systemd=True) is False


# ── scan_stale exposes both the raw ledger verdict and the reconciled one ─────────────

def test_scan_stale_exposes_ledger_and_reconciled(ledger, monkeypatch):
    unit = "dispatch-retro-learning.service"
    _seed_frozen_failed(ledger, unit, 25.0)
    monkeypatch.setenv("CRON_HEALTH_SYSTEMD_TRUTH", "1")
    monkeypatch.setattr(
        cron_health, "_run_systemctl",
        _runner({unit: _show(active="inactive", result="success", exit_status="0",
                             exec_exit=NOW - timedelta(hours=1))}),
    )
    rows = {r["unit"]: r for r in cron_health.scan_stale(now=NOW, path=ledger)}
    r = rows[unit]
    assert r["stale_ledger"] is True    # failure-only ledger would alert
    assert r["stale"] is False          # reconciled verdict does not
    assert r["systemd_healthy"] is True


# ── hermetic by default under pytest (no env / no use_systemd) ───────────────────────

def test_systemd_disabled_by_default_under_pytest(ledger, monkeypatch):
    unit = "dispatch-retro-learning.service"
    _seed_frozen_failed(ledger, unit, 25.0)
    monkeypatch.delenv("CRON_HEALTH_SYSTEMD_TRUTH", raising=False)
    called: list = []
    monkeypatch.setattr(cron_health, "_run_systemctl", lambda a, t: called.append(a) or None)
    # Under pytest, no explicit opt-in → systemd cross-check off → ledger verdict,
    # and _run_systemctl is never invoked.
    assert cron_health.is_stale(unit, now=NOW, path=ledger) is True
    assert called == []


# ── systemd_probe interpretation (unit) ──────────────────────────────────────────────

def test_systemd_probe_interprets_states(monkeypatch):
    m = {
        "u-active.service": _show(active="active", result="success", exit_status="0",
                                  active_enter=NOW - timedelta(minutes=5), typ="simple"),
        "u-oneshot-ok.service": _show(active="inactive", result="success", exit_status="0",
                                      exec_exit=NOW - timedelta(hours=1)),
        "u-failed.service": _show(active="failed", result="exit-code", exit_status="1"),
        "u-notfound.service": _show(load="not-found", active="inactive"),
    }
    monkeypatch.setattr(cron_health, "_run_systemctl", _runner(m))
    assert cron_health.systemd_probe("u-active.service", now=NOW)["healthy"] is True
    assert cron_health.systemd_probe("u-oneshot-ok.service", now=NOW)["healthy"] is True
    assert cron_health.systemd_probe("u-failed.service", now=NOW)["healthy"] is False
    nf = cron_health.systemd_probe("u-notfound.service", now=NOW)
    assert nf["available"] is False and nf["healthy"] is None


# ── mutation guards (C13) ────────────────────────────────────────────────────────────

def test_rescue_freshness_polarity_mutation_guard(ledger, monkeypatch):
    """Kills a flip of `(now - fresh_ts) <= threshold` in _systemd_rescues_stale.

    A FRESH systemd success MUST rescue and an OLD one MUST NOT; inverting the
    comparison flips both asserts (mutant killed).
    """
    unit = "dispatch-retro-learning.service"
    _seed_frozen_failed(ledger, unit, 25.0)

    monkeypatch.setattr(
        cron_health, "_run_systemctl",
        _runner({unit: _show(active="inactive", result="success", exit_status="0",
                             exec_exit=NOW - timedelta(hours=1))}),  # fresh (<25h)
    )
    assert cron_health.is_stale(unit, now=NOW, path=ledger, use_systemd=True) is False

    monkeypatch.setattr(
        cron_health, "_run_systemctl",
        _runner({unit: _show(active="inactive", result="success", exit_status="0",
                             exec_exit=NOW - timedelta(hours=100))}),  # old (>25h)
    )
    assert cron_health.is_stale(unit, now=NOW, path=ledger, use_systemd=True) is True


def test_healthy_polarity_mutation_guard(ledger, monkeypatch):
    """Kills a flip of the healthy verdict / Result parsing in systemd_probe.

    A healthy unit rescues an otherwise-stale ledger; a failed unit never does.
    Treating failed as healthy (or healthy as failed) flips one of the asserts.
    """
    unit = "dispatch-cod-panel-ingest.service"
    _seed_frozen_failed(ledger, unit, 192.0, failed_days_ago=20)  # 480h > 192h

    monkeypatch.setattr(
        cron_health, "_run_systemctl",
        _runner({unit: _show(active="inactive", result="success", exit_status="0",
                             exec_exit=NOW - timedelta(hours=2))}),
    )
    assert cron_health.is_stale(unit, now=NOW, path=ledger, use_systemd=True) is False

    monkeypatch.setattr(
        cron_health, "_run_systemctl",
        _runner({unit: _show(active="failed", result="exit-code", exit_status="1")}),
    )
    assert cron_health.is_stale(unit, now=NOW, path=ledger, use_systemd=True) is True
