"""V3.28 Fix 5c (Sprint Z 03.05.2026) — auto Telegram alert tests.

Lekcja #67 closing loop: pre-flight diagnostic (Fix 5+5b) ma truthful multi-signal,
ALE Adrian dowiaduje się o critical TYLKO ręcznie via curl. Fix 5c = proactive
Telegram push z cooldown gating (NIE spam, Lekcja #65 adaptive thresholds).

Tests:
- _v328_should_alert (cooldown logic)
- _v328_send_health_alert (test mode = log only)
- _v328_load/save_alert_state (atomic persist)
"""
import json
import os
import sys
import tempfile
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import parser_health_endpoint as phe  # noqa: E402


def test_should_alert_critical_first_time():
    """Empty state + critical → alert (no cooldown applies)."""
    state = {"last_critical_ts": None, "last_degraded_ts": None}
    assert phe._v328_should_alert("critical", state, 1000.0) is True


def test_should_alert_critical_within_cooldown():
    """Last critical 10 min ago + critical → NO alert (cooldown 30 min)."""
    now_ts = 10000.0
    state = {"last_critical_ts": now_ts - 600, "last_degraded_ts": None}  # 10 min ago
    assert phe._v328_should_alert("critical", state, now_ts) is False


def test_should_alert_critical_after_cooldown():
    """Last critical 31 min ago + critical → alert (cooldown expired)."""
    now_ts = 10000.0
    state = {"last_critical_ts": now_ts - 1860, "last_degraded_ts": None}  # 31 min ago
    assert phe._v328_should_alert("critical", state, now_ts) is True


def test_should_alert_degraded_cooldown_60min():
    """Last degraded 45 min ago + degraded → NO alert (cooldown 60 min)."""
    now_ts = 10000.0
    state = {"last_critical_ts": None, "last_degraded_ts": now_ts - 2700}  # 45 min ago
    assert phe._v328_should_alert("degraded", state, now_ts) is False


def test_should_alert_degraded_after_cooldown():
    """Last degraded 61 min ago + degraded → alert."""
    now_ts = 10000.0
    state = {"last_critical_ts": None, "last_degraded_ts": now_ts - 3660}  # 61 min ago
    assert phe._v328_should_alert("degraded", state, now_ts) is True


def test_should_alert_ok_never():
    """ok status → never alert (regardless of state)."""
    state = {"last_critical_ts": None, "last_degraded_ts": None}
    assert phe._v328_should_alert("ok", state, 1000.0) is False


def test_should_alert_disabled_env(monkeypatch):
    """DISABLE_HEALTH_AUTO_ALERT=1 → never alert (rollback flag)."""
    monkeypatch.setenv("DISABLE_HEALTH_AUTO_ALERT", "1")
    state = {"last_critical_ts": None, "last_degraded_ts": None}
    assert phe._v328_should_alert("critical", state, 1000.0) is False


def test_send_health_alert_test_mode(monkeypatch):
    """HEALTH_ALERT_TEST_MODE=1 → log only, returns True (NIE real Telegram)."""
    monkeypatch.setenv("HEALTH_ALERT_TEST_MODE", "1")
    result = phe._v328_send_health_alert("critical", "pipeline_silent_despite_work")
    assert result is True


def test_alert_state_atomic_save_load(tmp_path, monkeypatch):
    """Save + load roundtrip — atomic temp+rename."""
    state_file = tmp_path / "alert_state.json"
    monkeypatch.setattr(phe, "ALERT_STATE_PATH", str(state_file))

    state_in = {"last_critical_ts": 12345.6, "last_degraded_ts": 98765.4}
    phe._v328_save_alert_state(state_in)
    assert state_file.exists()

    state_out = phe._v328_load_alert_state()
    assert state_out["last_critical_ts"] == 12345.6
    assert state_out["last_degraded_ts"] == 98765.4


def test_alert_state_load_missing_file_safe(monkeypatch):
    """Missing state file → return defensive defaults."""
    monkeypatch.setattr(phe, "ALERT_STATE_PATH", "/nonexistent/path/state.json")
    state = phe._v328_load_alert_state()
    assert state == {"last_critical_ts": None, "last_degraded_ts": None}


def test_constants_module_level():
    """Module-level constants z reasonable defaults."""
    assert phe.ALERT_COOLDOWN_CRITICAL_MIN == 30
    assert phe.ALERT_COOLDOWN_DEGRADED_MIN == 60
    assert phe.HEALTH_ALERT_GROUP_CHAT_ID == -5149910559


def test_should_alert_unknown_status():
    """Unknown status string (np. 'warning') → NO alert (defensive)."""
    state = {"last_critical_ts": None, "last_degraded_ts": None}
    assert phe._v328_should_alert("warning", state, 1000.0) is False
    assert phe._v328_should_alert("", state, 1000.0) is False
