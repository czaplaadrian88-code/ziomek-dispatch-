"""MP-#9 SMS heartbeat watchdog tests (2026-05-08).

Per master plan TOP-15 #9 + audit OPERATIONAL_RESILIENCE R3. Eliminuje
chicken-egg "Telegram bot down → admin alert via Telegram = gone".

Coverage:
  SMS provider abstraction (5):
    - get_provider returns stub by default
    - get_provider(name) selects by explicit name
    - get_provider unknown name raises ValueError
    - StubSMSProvider always configured + writes log + returns True
    - SMSDeliveryError carries provider/status/body fields

  OVH provider config validation (3):
    - is_configured False when env missing
    - is_configured True when full env set (mocked)
    - send raises SMSDeliveryError when not configured

  Heartbeat watchdog state machine (10):
    - tick success → consecutive_failures reset, last_success_ts updated
    - tick fail count increment (1, 2 — sub-threshold, no alert)
    - tick fail × 3 → SMS alert fires
    - tick fail × 4+ during continuous outage → dedup (NIE re-alert)
    - tick success after alert → recovery SMS + reset alert flag
    - state persists across ticks (load from file)
    - state corrupt JSON → fresh start, no crash
    - missing TELEGRAM_BOT_TOKEN → no-op exit 0
    - missing SMS_TARGET_NUMBER → log error, no SMS attempted
    - SMS provider not configured → log error, no false alerts
"""
from __future__ import annotations

import json
import os
import urllib.error
from unittest.mock import patch, MagicMock

import pytest


# ---------------------------------------------------------------------------
# SMS provider abstraction
# ---------------------------------------------------------------------------


def test_get_provider_default_is_stub(monkeypatch):
    monkeypatch.delenv("SMS_PROVIDER", raising=False)
    from dispatch_v2.sms import get_provider
    p = get_provider()
    assert p.name == "stub"


def test_get_provider_explicit_name(monkeypatch):
    from dispatch_v2.sms import get_provider
    p_stub = get_provider("stub")
    assert p_stub.name == "stub"
    p_ovh = get_provider("ovh")
    assert p_ovh.name == "ovh"


def test_get_provider_unknown_raises():
    from dispatch_v2.sms import get_provider
    with pytest.raises(ValueError, match="unknown SMS_PROVIDER"):
        get_provider("twilio_not_yet_supported")


def test_stub_provider_writes_log_returns_true(tmp_path):
    from dispatch_v2.sms.stub import StubSMSProvider
    log_path = tmp_path / "sms_log.jsonl"
    p = StubSMSProvider(log_path=str(log_path))
    assert p.is_configured() is True
    ok = p.send("test message", "+48123456789")
    assert ok is True
    assert log_path.exists()
    rec = json.loads(log_path.read_text())
    assert rec["provider"] == "stub"
    assert rec["recipient"] == "+48123456789"
    assert rec["message"] == "test message"
    assert rec["accepted"] is True


def test_sms_delivery_error_carries_diagnostics():
    from dispatch_v2.sms import SMSDeliveryError
    err = SMSDeliveryError("test fail", provider="ovh", status_code=500, body="server error")
    assert err.provider == "ovh"
    assert err.status_code == 500
    assert err.body == "server error"


# ---------------------------------------------------------------------------
# OVH provider config validation
# ---------------------------------------------------------------------------


def test_ovh_is_configured_false_when_env_missing(monkeypatch):
    for var in ("OVH_SMS_APP_KEY", "OVH_SMS_APP_SECRET", "OVH_SMS_CONSUMER_KEY", "OVH_SMS_SERVICE_NAME"):
        monkeypatch.delenv(var, raising=False)
    from dispatch_v2.sms.ovh import OVHSMSProvider
    p = OVHSMSProvider()
    assert p.is_configured() is False
    missing = p._missing_creds_summary()
    assert "OVH_SMS_APP_KEY" in missing


def test_ovh_is_configured_true_when_full_env(monkeypatch):
    monkeypatch.setenv("OVH_SMS_APP_KEY", "AK_TEST")
    monkeypatch.setenv("OVH_SMS_APP_SECRET", "AS_TEST")
    monkeypatch.setenv("OVH_SMS_CONSUMER_KEY", "CK_TEST")
    monkeypatch.setenv("OVH_SMS_SERVICE_NAME", "sms-test-1")
    from dispatch_v2.sms.ovh import OVHSMSProvider
    p = OVHSMSProvider()
    assert p.is_configured() is True


def test_ovh_send_raises_when_not_configured(monkeypatch):
    for var in ("OVH_SMS_APP_KEY", "OVH_SMS_APP_SECRET", "OVH_SMS_CONSUMER_KEY", "OVH_SMS_SERVICE_NAME"):
        monkeypatch.delenv(var, raising=False)
    from dispatch_v2.sms.ovh import OVHSMSProvider
    from dispatch_v2.sms import SMSDeliveryError
    p = OVHSMSProvider()
    with pytest.raises(SMSDeliveryError, match="missing creds"):
        p.send("msg", "+48123456789")


# ---------------------------------------------------------------------------
# Heartbeat watchdog state machine
# ---------------------------------------------------------------------------


@pytest.fixture
def hb_env(tmp_path, monkeypatch):
    """Isolated state path + stub SMS provider."""
    state_path = tmp_path / "hb_state.json"
    sms_log = tmp_path / "sms_log.jsonl"
    monkeypatch.setenv("SMS_PROVIDER", "stub")
    monkeypatch.setenv("SMS_TARGET_NUMBER", "+48999000111")
    # Re-import to pick up env
    yield {
        "state_path": str(state_path),
        "sms_log": str(sms_log),
    }


def _mock_telegram_ok():
    """Mock urlopen returning Telegram getMe OK."""
    mock_resp = MagicMock()
    mock_resp.status = 200
    mock_resp.read.return_value = b'{"ok":true,"result":{"id":1,"is_bot":true}}'
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


def _mock_telegram_fail_http(code: int = 502):
    """Mock urlopen raising HTTPError."""
    return urllib.error.HTTPError(
        "https://api.telegram.org/", code, "Bad Gateway", {}, None
    )


def _mock_telegram_fail_network():
    """Mock urlopen raising URLError (network down)."""
    return urllib.error.URLError("connection refused")


def test_hb_tick_success_resets_failures(hb_env, monkeypatch):
    from dispatch_v2 import tg_heartbeat as hb
    # Pre-state: 2 prior failures
    state_initial = {"consecutive_failures": 2, "first_failure_ts": 1000}
    with open(hb_env["state_path"], "w") as f:
        json.dump(state_initial, f)

    with patch.object(hb, "urllib") as mock_urllib:
        mock_urllib.request.urlopen.return_value = _mock_telegram_ok()
        mock_urllib.error.HTTPError = urllib.error.HTTPError
        mock_urllib.error.URLError = urllib.error.URLError
        rc = hb.tick(state_path=hb_env["state_path"], token="dummy_token")
    assert rc == 0

    with open(hb_env["state_path"]) as f:
        state = json.load(f)
    assert state["consecutive_failures"] == 0
    assert state.get("last_success_ts") is not None


def test_hb_tick_fail_increments_counter_below_threshold(hb_env, monkeypatch):
    from dispatch_v2 import tg_heartbeat as hb

    sent = []
    monkeypatch.setattr(hb, "_send_sms_alert", lambda msg: (sent.append(msg) or True))

    with patch.object(hb, "urllib") as mock_urllib:
        mock_urllib.request.urlopen.side_effect = _mock_telegram_fail_network()
        mock_urllib.error.HTTPError = urllib.error.HTTPError
        mock_urllib.error.URLError = urllib.error.URLError
        # Tick twice — below threshold (3)
        hb.tick(state_path=hb_env["state_path"], threshold=3, token="dummy_token")
        hb.tick(state_path=hb_env["state_path"], threshold=3, token="dummy_token")

    with open(hb_env["state_path"]) as f:
        state = json.load(f)
    assert state["consecutive_failures"] == 2
    assert state.get("alert_sent_for_current_outage", False) is False
    assert sent == [], "no SMS at sub-threshold"


def test_hb_tick_third_failure_fires_alert(hb_env, monkeypatch):
    from dispatch_v2 import tg_heartbeat as hb

    sent = []
    monkeypatch.setattr(hb, "_send_sms_alert", lambda msg: (sent.append(msg) or True))

    with patch.object(hb, "urllib") as mock_urllib:
        mock_urllib.request.urlopen.side_effect = _mock_telegram_fail_network()
        mock_urllib.error.HTTPError = urllib.error.HTTPError
        mock_urllib.error.URLError = urllib.error.URLError
        # 3 ticks → alert at 3rd
        for _ in range(3):
            hb.tick(state_path=hb_env["state_path"], threshold=3, token="dummy_token_xxxx")

    with open(hb_env["state_path"]) as f:
        state = json.load(f)
    assert state["consecutive_failures"] == 3
    assert state["alert_sent_for_current_outage"] is True
    assert len(sent) == 1
    assert "DOWN" in sent[0]
    assert "3 consecutive" in sent[0]


def test_hb_tick_continued_outage_dedups_alert(hb_env, monkeypatch):
    from dispatch_v2 import tg_heartbeat as hb

    sent = []
    monkeypatch.setattr(hb, "_send_sms_alert", lambda msg: (sent.append(msg) or True))

    with patch.object(hb, "urllib") as mock_urllib:
        mock_urllib.request.urlopen.side_effect = _mock_telegram_fail_network()
        mock_urllib.error.HTTPError = urllib.error.HTTPError
        mock_urllib.error.URLError = urllib.error.URLError
        for _ in range(7):  # well past threshold
            hb.tick(state_path=hb_env["state_path"], threshold=3, token="dummy_token_xxxx")

    with open(hb_env["state_path"]) as f:
        state = json.load(f)
    assert state["consecutive_failures"] == 7
    # Dedup — only 1 alert fired despite 7 ticks
    assert len(sent) == 1, f"expected 1 alert (dedup), got {len(sent)}"


def test_hb_tick_recovery_after_alert_fires_recovery_sms(hb_env, monkeypatch):
    from dispatch_v2 import tg_heartbeat as hb

    sent = []
    monkeypatch.setattr(hb, "_send_sms_alert", lambda msg: (sent.append(msg) or True))

    # Phase 1: 3 fails → entry alert
    with patch.object(hb, "urllib") as mock_urllib:
        mock_urllib.request.urlopen.side_effect = _mock_telegram_fail_network()
        mock_urllib.error.HTTPError = urllib.error.HTTPError
        mock_urllib.error.URLError = urllib.error.URLError
        for _ in range(3):
            hb.tick(state_path=hb_env["state_path"], threshold=3, token="dummy_token_xxxx")

    assert len(sent) == 1
    assert "DOWN" in sent[0]

    # Phase 2: success → recovery alert
    with patch.object(hb, "urllib") as mock_urllib:
        mock_urllib.request.urlopen.return_value = _mock_telegram_ok()
        mock_urllib.error.HTTPError = urllib.error.HTTPError
        mock_urllib.error.URLError = urllib.error.URLError
        hb.tick(state_path=hb_env["state_path"], token="dummy_token_xxxx")

    with open(hb_env["state_path"]) as f:
        state = json.load(f)
    assert state["consecutive_failures"] == 0
    assert state["alert_sent_for_current_outage"] is False
    assert len(sent) == 2
    assert "RECOVERY" in sent[1]


def test_hb_tick_state_persists_across_ticks(hb_env, monkeypatch):
    from dispatch_v2 import tg_heartbeat as hb

    monkeypatch.setattr(hb, "_send_sms_alert", lambda msg: True)
    with patch.object(hb, "urllib") as mock_urllib:
        mock_urllib.request.urlopen.side_effect = _mock_telegram_fail_network()
        mock_urllib.error.HTTPError = urllib.error.HTTPError
        mock_urllib.error.URLError = urllib.error.URLError
        hb.tick(state_path=hb_env["state_path"], threshold=3, token="t")
    # Re-load state simulates next-tick boot
    state1 = hb._load_state(hb_env["state_path"])
    assert state1["consecutive_failures"] == 1
    with patch.object(hb, "urllib") as mock_urllib:
        mock_urllib.request.urlopen.side_effect = _mock_telegram_fail_network()
        mock_urllib.error.HTTPError = urllib.error.HTTPError
        mock_urllib.error.URLError = urllib.error.URLError
        hb.tick(state_path=hb_env["state_path"], threshold=3, token="t")
    state2 = hb._load_state(hb_env["state_path"])
    assert state2["consecutive_failures"] == 2


def test_hb_tick_corrupt_state_resets_gracefully(hb_env, monkeypatch):
    from dispatch_v2 import tg_heartbeat as hb
    # Write garbage
    with open(hb_env["state_path"], "w") as f:
        f.write("{not valid json")
    monkeypatch.setattr(hb, "_send_sms_alert", lambda msg: True)

    with patch.object(hb, "urllib") as mock_urllib:
        mock_urllib.request.urlopen.return_value = _mock_telegram_ok()
        mock_urllib.error.HTTPError = urllib.error.HTTPError
        mock_urllib.error.URLError = urllib.error.URLError
        rc = hb.tick(state_path=hb_env["state_path"], token="t")
    assert rc == 0  # no crash
    state = hb._load_state(hb_env["state_path"])
    assert state["consecutive_failures"] == 0  # reset gracefully


def test_hb_tick_missing_token_no_op(hb_env, monkeypatch, caplog):
    import logging
    from dispatch_v2 import tg_heartbeat as hb
    caplog.set_level(logging.ERROR)
    monkeypatch.setattr(hb, "_load_telegram_token", lambda: None)
    rc = hb.tick(state_path=hb_env["state_path"])
    assert rc == 0  # exit 0 — no false alerts
    assert any("TELEGRAM_BOT_TOKEN missing" in r.message for r in caplog.records)


def test_hb_tick_missing_sms_target_logs_error(hb_env, monkeypatch, caplog):
    import logging
    from dispatch_v2 import tg_heartbeat as hb
    caplog.set_level(logging.ERROR)
    monkeypatch.delenv("SMS_TARGET_NUMBER", raising=False)
    # 3 fails to trigger alert
    with patch.object(hb, "urllib") as mock_urllib:
        mock_urllib.request.urlopen.side_effect = _mock_telegram_fail_network()
        mock_urllib.error.HTTPError = urllib.error.HTTPError
        mock_urllib.error.URLError = urllib.error.URLError
        for _ in range(3):
            hb.tick(state_path=hb_env["state_path"], threshold=3, token="t")
    assert any("SMS_TARGET_NUMBER not set" in r.message for r in caplog.records)


def test_hb_tick_provider_not_configured_logs_error(hb_env, monkeypatch, caplog):
    import logging
    from dispatch_v2 import tg_heartbeat as hb
    caplog.set_level(logging.ERROR)
    monkeypatch.setenv("SMS_PROVIDER", "ovh")  # OVH not configured (no creds)
    for var in ("OVH_SMS_APP_KEY", "OVH_SMS_APP_SECRET", "OVH_SMS_CONSUMER_KEY", "OVH_SMS_SERVICE_NAME"):
        monkeypatch.delenv(var, raising=False)
    with patch.object(hb, "urllib") as mock_urllib:
        mock_urllib.request.urlopen.side_effect = _mock_telegram_fail_network()
        mock_urllib.error.HTTPError = urllib.error.HTTPError
        mock_urllib.error.URLError = urllib.error.URLError
        for _ in range(3):
            hb.tick(state_path=hb_env["state_path"], threshold=3, token="t")
    assert any("NOT configured" in r.message for r in caplog.records)
