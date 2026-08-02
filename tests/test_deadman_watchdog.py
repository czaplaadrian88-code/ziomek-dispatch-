"""Testy DEAD-MAN watchdog (Z0-3) — niezależny czuwak nad sprawnością alertowania.

Granica systemd = `deadman_watchdog._run_systemctl_failed` (monkeypatch w każdym teście;
żaden test nie dotyka realnego systemd). Cały stan (cooldown, shadow-log, heartbeaty)
przekierowany do tmp_path przez env — HERMETIC-GUARD compliant, zero zapisu do live.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dispatch_v2.observability import deadman_watchdog as dw

NOW = datetime(2026, 8, 2, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Kieruje cały stan modułu do tmp_path i domyślnie ustawia świeży heartbeat + czysty ledger."""
    state = tmp_path / "deadman_state.json"
    shadow = tmp_path / "deadman_shadow.jsonl"
    ng = tmp_path / "night_guard_history.jsonl"
    ch = tmp_path / "cron_health.json"
    monkeypatch.setenv("DEADMAN_STATE_PATH", str(state))
    monkeypatch.setenv("DEADMAN_SHADOW_LOG", str(shadow))
    monkeypatch.setenv("DEADMAN_NIGHT_GUARD_PATH", str(ng))
    monkeypatch.setenv("DEADMAN_CRON_HEALTH_PATH", str(ch))
    monkeypatch.delenv("DEADMAN_ENABLED", raising=False)
    monkeypatch.delenv("DEADMAN_DRY_RUN", raising=False)
    monkeypatch.delenv("DEADMAN_EXTERNAL_PING_URL", raising=False)
    monkeypatch.delenv("DEADMAN_SUPPRESS_UNITS", raising=False)
    # domyślnie: świeży nocny strażnik + czytelny cron_health (żeby nie generować pobocznych findings)
    ng.write_text(json.dumps({"ts": (NOW - timedelta(hours=1)).isoformat(), "verdict": "OK"}) + "\n")
    ch.write_text(json.dumps({"units": {}}))
    return {"state": state, "shadow": shadow, "ng": ng, "ch": ch, "tmp": tmp_path}


def _mock_failed(monkeypatch, units):
    monkeypatch.setattr(dw, "_run_systemctl_failed", lambda: list(units))


def _no_network(monkeypatch):
    def boom(*a, **k):
        raise AssertionError("send_independent nie wolno wołać w DRY-RUN / bez zgody")
    monkeypatch.setattr(dw, "send_independent", boom)


# ------------------------------------------------------------------ detekcja
def test_failed_critical_unit_escalates_and_dryrun_logs(env, monkeypatch):
    _mock_failed(monkeypatch, ["code-offsite-sync.service"])
    _no_network(monkeypatch)  # DRY-RUN nie może wołać sieci
    res = dw.run(now=NOW)  # dry-run z env (domyślnie shadow-first)
    assert res["dry_run"] is True
    assert res["status"] == "dry_run"
    assert "failed:code-offsite-sync.service" in res["escalated"]
    # brak alertu w cron_health → korelacja: kanał NIEMY → CRITICAL
    f = next(x for x in res["findings"] if x["key"] == "failed:code-offsite-sync.service")
    assert f["severity"] == "CRITICAL"
    assert f["detail"]["primary_alert_silent"] is True
    # dowód DRY-RUN: shadow-log zawiera CO by wysłał
    lines = [json.loads(l) for l in env["shadow"].read_text().splitlines() if l.strip()]
    assert lines and lines[-1]["would_send"] is True
    assert "code-offsite-sync" in lines[-1]["message"]
    # cooldown NIE nabity w dry-run (repro musi się powtarzać)
    assert not env["state"].exists()


def test_multiple_real_failures_night_guard_and_offsite(env, monkeypatch):
    # replikuje realny stan hosta 02.08: obie usługi failed
    _mock_failed(monkeypatch, ["code-offsite-sync.service", "dispatch-night-guard.service"])
    _no_network(monkeypatch)
    res = dw.run(now=NOW)
    keys = set(res["escalated"])
    assert "failed:code-offsite-sync.service" in keys
    assert "failed:dispatch-night-guard.service" in keys


# ------------------------------------------------------------------ anti-noise
def test_known_intentional_suppressed(env, monkeypatch):
    # dispatch-telegram wyciszony 26.06 — nawet w stanie failed NIE alarmuje
    _mock_failed(monkeypatch, ["dispatch-telegram.service"])
    _no_network(monkeypatch)
    res = dw.run(now=NOW)
    assert res["status"] == "quiet"
    assert res["escalated"] == []


def test_noncritical_failed_unit_ignored(env, monkeypatch):
    _mock_failed(monkeypatch, ["some-random-thing.service"])
    _no_network(monkeypatch)
    res = dw.run(now=NOW)
    assert res["status"] == "quiet"


def test_env_suppress_list_additive(env, monkeypatch):
    monkeypatch.setenv("DEADMAN_SUPPRESS_UNITS", "dispatch-cod-weekly-preflight.service")
    _mock_failed(monkeypatch, ["dispatch-cod-weekly-preflight.service"])
    _no_network(monkeypatch)
    res = dw.run(now=NOW)
    assert res["status"] == "quiet"


# ------------------------------------------------------------------ cooldown / dedup
def test_cooldown_dedup(env, monkeypatch):
    _mock_failed(monkeypatch, ["code-offsite-sync.service"])
    sent = []
    monkeypatch.setattr(dw, "send_independent", lambda t: sent.append(t) or True)
    # przebieg 1 (realna wysyłka wymuszona explicit dry_run=False) → wysyła + nabija cooldown
    r1 = dw.run(now=NOW, dry_run=False)
    assert r1["status"] == "sent" and r1["sent"] is True
    assert len(sent) == 1
    assert env["state"].exists()
    # przebieg 2, 1h później, w oknie cooldown (6h) → NIE wysyła ponownie
    r2 = dw.run(now=NOW + timedelta(hours=1), dry_run=False)
    assert r2["escalated"] == []
    assert "failed:code-offsite-sync.service" in r2["cooled_down"]
    assert len(sent) == 1  # brak drugiej wysyłki
    # przebieg 3, po wygaśnięciu cooldownu (7h) → znów wysyła
    r3 = dw.run(now=NOW + timedelta(hours=7), dry_run=False)
    assert r3["sent"] is True
    assert len(sent) == 2


# ------------------------------------------------------------------ night-guard freshness
def test_night_guard_stale_triggers(env, monkeypatch):
    env["ng"].write_text(json.dumps({"ts": (NOW - timedelta(hours=40)).isoformat(), "verdict": "OK"}) + "\n")
    _mock_failed(monkeypatch, [])  # brak failed units, tylko cisza strażnika
    _no_network(monkeypatch)
    res = dw.run(now=NOW)
    assert "night_guard:stale" in res["escalated"]


def test_night_guard_fresh_no_finding(env, monkeypatch):
    _mock_failed(monkeypatch, [])
    _no_network(monkeypatch)
    res = dw.run(now=NOW)
    assert res["status"] == "quiet"


def test_night_guard_missing_file_triggers(env, monkeypatch):
    env["ng"].unlink()
    _mock_failed(monkeypatch, [])
    _no_network(monkeypatch)
    res = dw.run(now=NOW)
    assert "night_guard:missing" in res["escalated"]


# ------------------------------------------------------------------ korelacja „kanał niemy"
def test_primary_alert_recent_downgrades_to_p1(env, monkeypatch):
    # cron_health pokazuje ŚWIEŻY alert dla tej jednostki → dostawa mogła zadziałać → P1, nie CRITICAL
    env["ch"].write_text(json.dumps({"units": {
        "dispatch-shadow.service": {"last_alert_ts": (NOW - timedelta(hours=1)).isoformat()}
    }}))
    _mock_failed(monkeypatch, ["dispatch-shadow.service"])
    _no_network(monkeypatch)
    res = dw.run(now=NOW)
    f = next(x for x in res["findings"] if x["key"] == "failed:dispatch-shadow.service")
    assert f["severity"] == "P1"
    assert f["detail"]["primary_alert_silent"] is False


def test_primary_alert_stale_is_critical(env, monkeypatch):
    env["ch"].write_text(json.dumps({"units": {
        "dispatch-shadow.service": {"last_alert_ts": (NOW - timedelta(hours=48)).isoformat()}
    }}))
    _mock_failed(monkeypatch, ["dispatch-shadow.service"])
    _no_network(monkeypatch)
    res = dw.run(now=NOW)
    f = next(x for x in res["findings"] if x["key"] == "failed:dispatch-shadow.service")
    assert f["severity"] == "CRITICAL"


# ------------------------------------------------------------------ systemctl niedostępny
def test_systemctl_unavailable_is_a_finding(env, monkeypatch):
    monkeypatch.setattr(dw, "_run_systemctl_failed", lambda: None)
    _no_network(monkeypatch)
    res = dw.run(now=NOW)
    assert "systemctl_unavailable" in res["escalated"]


# ------------------------------------------------------------------ niezależność kanału (ratchet)
def test_module_does_not_import_compromised_path():
    src = Path(dw.__file__).read_text(encoding="utf-8")
    # dead-man NIE wolno dzielić ścieżki wyciszonego głównego bota
    for banned in ("telegram_utils", "telegram_approver", "notify_router"):
        assert f"import {banned}" not in src, f"dead-man nie może importować {banned}"
        assert f"from dispatch_v2 import {banned}" not in src


def test_dry_run_is_default_shadow_first(env, monkeypatch):
    # bez DEADMAN_ENABLED/DRY_RUN → shadow-first (dry)
    _mock_failed(monkeypatch, ["code-offsite-sync.service"])
    _no_network(monkeypatch)
    res = dw.run(now=NOW)
    assert res["dry_run"] is True


def test_enabled_and_not_dry_allows_send(env, monkeypatch):
    monkeypatch.setenv("DEADMAN_ENABLED", "1")
    monkeypatch.setenv("DEADMAN_DRY_RUN", "0")
    _mock_failed(monkeypatch, ["code-offsite-sync.service"])
    sent = []
    monkeypatch.setattr(dw, "send_independent", lambda t: sent.append(t) or True)
    res = dw.run(now=NOW)  # dry z env → False
    assert res["dry_run"] is False
    assert res["sent"] is True and len(sent) == 1


def test_creds_missing_send_returns_false(env, monkeypatch):
    monkeypatch.delenv("DEADMAN_ALERT_TOKEN", raising=False)
    monkeypatch.delenv("ASSISTANT_TELEGRAM_TOKEN", raising=False)
    monkeypatch.delenv("DEADMAN_ALERT_CHAT_ID", raising=False)
    monkeypatch.delenv("ASSISTANT_TELEGRAM_ADMIN_ID", raising=False)
    # nie woła sieci gdy brak creds
    assert dw.send_independent("x") is False
