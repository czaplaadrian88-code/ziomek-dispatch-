"""dispatch-tg-heartbeat — out-of-band Telegram bot health watchdog (MP-#9, 2026-05-08).

Per master plan TOP-15 #9 + audit OPERATIONAL_RESILIENCE R3:
  Eliminuje chicken-egg: "Telegram bot down → admin alert via Telegram = gone".

Mechanism (per-tick logic, oneshot timer):
  1. Read prior state z `dispatch_state/tg_heartbeat_state.json`.
  2. HTTP GET https://api.telegram.org/bot{TOKEN}/getMe (timeout 10s).
  3. On success: reset consecutive_failures=0, update last_success_ts.
  4. On fail: increment consecutive_failures.
  5. If consecutive_failures >= 3 AND alert NIE wysłany w current outage period:
       SMS Adrian via dispatch_v2.sms.get_provider().send(...).
       Mark alert_sent_for_current_outage=True.
  6. On recovery (failures → 0 after alert was sent): SMS recovery + reset alert flag.

State persistence (atomic JSON via tempfile + os.replace):
  - consecutive_failures: int
  - last_success_ts: epoch float
  - last_failure_ts: epoch float
  - alert_sent_for_current_outage: bool (dedup — jeden SMS na outage period)
  - last_alert_ts: epoch (audit trail)
  - last_recovery_alert_ts: epoch

Run via systemd oneshot timer (every 60s):
  systemctl enable --now dispatch-tg-heartbeat.timer

Required env:
  TELEGRAM_BOT_TOKEN — same as dispatch-telegram service
  SMS_PROVIDER       — 'ovh' (production) | 'stub' (default, dev)
  SMS_TARGET_NUMBER  — Adrian's E.164 number (e.g. +48...)

Optional env:
  TG_HEARTBEAT_THRESHOLD     — default 3 consecutive failures
  TG_HEARTBEAT_TIMEOUT_SEC   — default 10s for getMe call
  TG_HEARTBEAT_STATE_PATH    — default /root/.openclaw/workspace/dispatch_state/tg_heartbeat_state.json

Returns exit code 0 always (oneshot timer should succeed even when Telegram down —
the SMS path IS the value). Hard errors logged via stderr.

Defense-in-depth:
  - Provider crash → log + skip SMS this tick (state unchanged for next tick retry)
  - State file corrupt → fresh state (counter resets, alert may flap once)
  - Token missing → log fatal, exit 0 (no false alerts)
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

# Logging — stderr (systemd journal capture)
_log = logging.getLogger("tg_heartbeat")
if not _log.handlers:
    h = logging.StreamHandler(sys.stderr)
    h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] tg_heartbeat: %(message)s"))
    _log.addHandler(h)
    _log.setLevel(logging.INFO)


DEFAULT_STATE_PATH = "/root/.openclaw/workspace/dispatch_state/tg_heartbeat_state.json"
DEFAULT_THRESHOLD = 3
DEFAULT_TIMEOUT_SEC = 10.0
DEFAULT_TOKEN_ENV_PATH = "/root/.openclaw/workspace/.secrets/telegram.env"

TELEGRAM_API_URL = "https://api.telegram.org"


def _load_telegram_token() -> Optional[str]:
    """Read TELEGRAM_BOT_TOKEN z env lub .secrets/telegram.env."""
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if token:
        return token
    secrets_path = Path(DEFAULT_TOKEN_ENV_PATH)
    if secrets_path.exists():
        try:
            for line in secrets_path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                if k.strip() == "TELEGRAM_BOT_TOKEN":
                    return v.strip().strip('"').strip("'")
        except Exception as e:
            _log.warning(f"reading {secrets_path} fail: {e}")
    return None


def _load_state(path: str) -> dict:
    """Load heartbeat state (fresh dict on missing/corrupt)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        _log.warning(f"state load fail ({type(e).__name__}: {e}) — fresh state")
        return {}


def _save_state(path: str, state: dict) -> None:
    """Atomic state save via tempfile + os.replace."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".tg_heartbeat_", suffix=".tmp", dir=str(target.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(state, f, indent=2, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, target)
    except Exception as e:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        _log.error(f"state save fail: {type(e).__name__}: {e}")


def _ping_telegram(token: str, timeout: float) -> tuple[bool, str]:
    """Single getMe call. Returns (ok, reason_short).

    Success criterion: HTTP 200 + JSON {"ok": true} from response body.
    Returns False on any HTTP error, network error, parse error, or "ok": false.
    """
    url = f"{TELEGRAM_API_URL}/bot{token}/getMe"
    try:
        req = urllib.request.Request(url, method="GET")
        with urllib.request.urlopen(req, timeout=timeout) as r:
            if r.status != 200:
                return False, f"http_{r.status}"
            body = r.read().decode("utf-8", errors="ignore")
            data = json.loads(body)
            if data.get("ok"):
                return True, "ok"
            return False, f"api_not_ok:{str(data.get('description', '?'))[:40]}"
    except urllib.error.HTTPError as e:
        return False, f"http_{e.code}"
    except urllib.error.URLError as e:
        return False, f"network:{type(e.reason).__name__}"
    except (json.JSONDecodeError, ValueError):
        return False, "parse_fail"
    except Exception as e:
        return False, f"unexpected:{type(e).__name__}"


def _send_sms_alert(message: str) -> bool:
    """Send SMS via configured provider. Returns True on accepted, False on fail.

    Defensive: provider exceptions logged ale NIE propagują (heartbeat must complete).
    """
    target = os.environ.get("SMS_TARGET_NUMBER", "")
    if not target:
        _log.error("SMS_TARGET_NUMBER not set — cannot send alert")
        return False
    try:
        from dispatch_v2.sms import get_provider, SMSDeliveryError
        provider = get_provider()
        if not provider.is_configured():
            _log.error(f"SMS provider {provider.name} NOT configured — alert NOT sent. msg={message[:80]!r}")
            return False
        try:
            return provider.send(message, target)
        except SMSDeliveryError as e:
            _log.error(f"SMS delivery error (provider={e.provider} status={e.status_code}): {e}")
            return False
    except Exception as e:
        _log.error(f"SMS provider unexpected fail: {type(e).__name__}: {e}")
        return False


def tick(
    state_path: str = DEFAULT_STATE_PATH,
    threshold: int = DEFAULT_THRESHOLD,
    timeout_sec: float = DEFAULT_TIMEOUT_SEC,
    token: Optional[str] = None,
) -> int:
    """Single heartbeat tick. Idempotent. Returns exit code (0 always per design).

    State machine:
      healthy (failures=0, alert=False)
        ↓ ping fail × N
      counting (failures=N<3, alert=False)
        ↓ ping fail × 3rd time
      alerting (failures>=3, alert=True) → send entry SMS
        ↓ continued fail
      alerting (failures keeps growing, alert=True) → NIE re-send (dedup)
        ↓ ping success
      recovering (failures=0, alert=True still) → send recovery SMS, reset alert=False
        ↓ next tick
      healthy (failures=0, alert=False)
    """
    if token is None:
        token = _load_telegram_token()
    if not token:
        _log.error("TELEGRAM_BOT_TOKEN missing — heartbeat skip (no alerts possible)")
        return 0

    state = _load_state(state_path)
    consecutive_failures = int(state.get("consecutive_failures", 0))
    alert_sent = bool(state.get("alert_sent_for_current_outage", False))

    ok, reason = _ping_telegram(token, timeout_sec)
    now_ts = time.time()
    now_iso = datetime.now(timezone.utc).isoformat()

    if ok:
        # Success path
        was_failing = consecutive_failures > 0
        state["consecutive_failures"] = 0
        state["last_success_ts"] = now_ts
        state["last_success_at"] = now_iso

        if alert_sent:
            # Recovery: send SMS + reset alert flag
            outage_dur_sec = now_ts - float(state.get("first_failure_ts", now_ts))
            recovery_msg = (
                f"✅ Ziomek Telegram bot RECOVERY — back online po {int(outage_dur_sec / 60)}min outage. "
                f"getMe returned ok at {now_iso[:19]}Z."
            )
            sms_ok = _send_sms_alert(recovery_msg)
            state["alert_sent_for_current_outage"] = False
            state["last_recovery_alert_ts"] = now_ts
            state["last_recovery_alert_sms_accepted"] = sms_ok
            state["first_failure_ts"] = None
            _log.info(f"recovery: outage_dur={int(outage_dur_sec)}s sms_ok={sms_ok}")
        elif was_failing:
            _log.info(f"sub-threshold recovery: failures {consecutive_failures}→0 (no alert was sent)")

        _save_state(state_path, state)
        return 0

    # Failure path
    state["consecutive_failures"] = consecutive_failures + 1
    state["last_failure_ts"] = now_ts
    state["last_failure_at"] = now_iso
    state["last_failure_reason"] = reason
    if state["consecutive_failures"] == 1 or not state.get("first_failure_ts"):
        state["first_failure_ts"] = now_ts

    new_failures = state["consecutive_failures"]
    if new_failures >= threshold and not alert_sent:
        alert_msg = (
            f"⚠ Ziomek Telegram bot DOWN — {new_failures} consecutive getMe failures "
            f"(reason: {reason}). Started ~{datetime.fromtimestamp(state['first_failure_ts'], tz=timezone.utc).isoformat()[:19]}Z. "
            f"Bot health check: https://api.telegram.org/bot{token[:8]}.../getMe"
        )
        sms_ok = _send_sms_alert(alert_msg)
        state["alert_sent_for_current_outage"] = True
        state["last_alert_ts"] = now_ts
        state["last_alert_sms_accepted"] = sms_ok
        _log.warning(
            f"OUTAGE alert sent: failures={new_failures} reason={reason} sms_accepted={sms_ok}"
        )
    else:
        _log.info(
            f"failure: count={new_failures}/{threshold} reason={reason} "
            f"alert_sent={alert_sent}"
        )

    _save_state(state_path, state)
    return 0


def main() -> int:
    """CLI entry. Reads env config, runs single tick."""
    state_path = os.environ.get("TG_HEARTBEAT_STATE_PATH", DEFAULT_STATE_PATH)
    threshold = int(os.environ.get("TG_HEARTBEAT_THRESHOLD", str(DEFAULT_THRESHOLD)))
    timeout_sec = float(os.environ.get("TG_HEARTBEAT_TIMEOUT_SEC", str(DEFAULT_TIMEOUT_SEC)))
    return tick(state_path=state_path, threshold=threshold, timeout_sec=timeout_sec)


if __name__ == "__main__":
    sys.exit(main())
