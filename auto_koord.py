"""TASK 4 (2026-05-04) — Auto-KOORD on NEW_ORDER for czasówki.

Adrian's wizja from original brief: gdy NEW_ORDER ma czas_odbioru >= 60min
AND id_kurier=None → automatyczne przypisanie do Koordynatora (cid=26).
Czasówka leży u Koordynatora aż do T-50/T-40 proactive proposal (TASK A
przyszłego sprintu).

Z3 architectural notes:
  - Subprocess gastro_assign — isolowany CookieJar (żaden konflikt z panel_watcher)
  - 3× retry z exponential backoff (5s, 15s, 45s)
  - Pre-assign safety re-fetch (race-condition guard)
  - Cancelled (status=9) → SKIP
  - Sequential (jedno wywołanie per tick) — dla CookieJar safety
  - Defensive: NIGDY nie raise w caller (panel_watcher stability)

Flag gate:
  AUTO_KOORD_ON_NEW_ORDER_ENABLED (default False)
"""
from __future__ import annotations

import logging
import os
import subprocess
import time
from datetime import datetime, timezone
from typing import Any, Dict, Optional, Tuple


# --- Constants ---
GASTRO_ASSIGN_PATH = "/root/.openclaw/workspace/scripts/gastro_assign.py"
KOORDYNATOR_CID = 26
CZASOWKA_THRESHOLD_MIN = 60  # czas_odbioru >= 60 = czasówka

# Retry parameters
RETRY_BACKOFF_SEC = (5, 15, 45)  # 3 attempts
SUBPROCESS_TIMEOUT_SEC = 30

_log = logging.getLogger("auto_koord")


def is_czasowka(prep_minutes: Optional[int]) -> bool:
    """Czasówka detection: czas_odbioru >= 60 min (>= jest hard rule per Adrian).

    Edge: <60 = elastyk, dispatcher decides nie my.
    """
    if prep_minutes is None:
        return False
    try:
        return int(prep_minutes) >= CZASOWKA_THRESHOLD_MIN
    except (ValueError, TypeError):
        return False


def is_unassigned(raw_order: Dict[str, Any]) -> bool:
    """id_kurier=None lub 0 → unassigned. Koordynator (id=26) NIE liczy się jako
    unassigned — już przypisany (no auto-KOORD potrzebny)."""
    cid = raw_order.get("id_kurier")
    if cid in (None, "", 0, "0"):
        return True
    try:
        # Already-Koordynator NIE wymaga ponownego auto-KOORD
        return int(cid) == 0
    except (ValueError, TypeError):
        return False


def is_cancelled(raw_order: Dict[str, Any]) -> bool:
    """status_id=9 = anulowane. Skip auto-KOORD."""
    sid = raw_order.get("id_status_zamowienia")
    try:
        return int(sid) == 9
    except (ValueError, TypeError):
        return False


def needs_auto_koord(
    raw_order: Dict[str, Any],
    flag_enabled: bool,
) -> Tuple[bool, str]:
    """Decision: should we auto-assign this order to Koordynator?

    Returns (decision, reason). reason zawsze populated dla audit trail.
    """
    if not flag_enabled:
        return False, "flag_disabled"
    prep = raw_order.get("czas_odbioru") or raw_order.get("prep_minutes")
    if not is_czasowka(prep):
        return False, f"not_czasowka (prep_minutes={prep})"
    if is_cancelled(raw_order):
        return False, f"already_cancelled (status_id={raw_order.get('id_status_zamowienia')})"
    if not is_unassigned(raw_order):
        return False, f"already_assigned (id_kurier={raw_order.get('id_kurier')})"
    return True, "czasowka_unassigned"


def perform_auto_koord(
    order_id: str,
    fetch_details_fn=None,
    subprocess_fn=None,
    sleep_fn=None,
) -> Dict[str, Any]:
    """Execute auto-KOORD assignment z retry + safety re-check.

    Args (test injection):
      fetch_details_fn(zid) -> raw_order|None (race-condition pre-check)
      subprocess_fn(cmd, **kwargs) -> CompletedProcess (gastro_assign)
      sleep_fn(seconds) -> None (mockable backoff)

    Returns dict z polami:
      success: bool
      attempts: int
      reason: str
      panel_response: str|None (last attempt stdout/stderr tail)
      skipped: bool (True gdy pre-check race detected)
    """
    _sleep = sleep_fn or time.sleep
    _subproc = subprocess_fn or subprocess.run

    # Pre-assign safety: re-fetch (race condition — może ktoś już przypisał)
    if fetch_details_fn is not None:
        try:
            raw = fetch_details_fn(order_id)
            if raw is None:
                return {
                    "success": False, "attempts": 0, "skipped": True,
                    "reason": "pre_check_fetch_none", "panel_response": None,
                }
            if is_cancelled(raw):
                return {
                    "success": False, "attempts": 0, "skipped": True,
                    "reason": "race_avoided_cancelled", "panel_response": None,
                }
            if not is_unassigned(raw):
                return {
                    "success": False, "attempts": 0, "skipped": True,
                    "reason": f"race_avoided_assigned_to_{raw.get('id_kurier')}",
                    "panel_response": None,
                }
        except Exception as e:
            _log.warning(f"pre-assign re-fetch fail oid={order_id}: {e} — proceeding cautiously")

    # Execute z retry
    cmd = ["python3", GASTRO_ASSIGN_PATH, "--id", str(order_id), "--koordynator"]
    last_response = None
    for attempt_idx, delay in enumerate(RETRY_BACKOFF_SEC, start=1):
        try:
            r = _subproc(cmd, capture_output=True, text=True, timeout=SUBPROCESS_TIMEOUT_SEC)
            if r.returncode == 0:
                last_response = (r.stdout or "").strip()[-400:]
                _log.info(f"AUTO_KOORD oid={order_id} success attempt={attempt_idx}")
                return {
                    "success": True, "attempts": attempt_idx, "skipped": False,
                    "reason": "ok", "panel_response": last_response,
                }
            last_response = f"exit={r.returncode} {(r.stderr or '').strip()[-400:]}"
            _log.warning(f"AUTO_KOORD oid={order_id} attempt={attempt_idx} fail: {last_response}")
        except subprocess.TimeoutExpired:
            last_response = "subprocess_timeout"
            _log.warning(f"AUTO_KOORD oid={order_id} attempt={attempt_idx} timeout")
        except Exception as e:
            last_response = f"{type(e).__name__}: {e}"
            _log.warning(f"AUTO_KOORD oid={order_id} attempt={attempt_idx} exception: {last_response}")

        # Backoff before next attempt (NIE po ostatnim)
        if attempt_idx < len(RETRY_BACKOFF_SEC):
            _sleep(delay)

    return {
        "success": False, "attempts": len(RETRY_BACKOFF_SEC), "skipped": False,
        "reason": "all_retries_exhausted", "panel_response": last_response,
    }


def make_telegram_info_message(order_state: Dict[str, Any], result: Dict[str, Any]) -> str:
    """Format compact info-only message dla Telegram (no buttons)."""
    pickup_at = order_state.get("pickup_at_warsaw") or order_state.get("pickup_at") or "?"
    if isinstance(pickup_at, str) and "T" in pickup_at:
        pickup_at = pickup_at.split("T")[1][:5]
    rest = order_state.get("restaurant") or "?"
    addr = order_state.get("delivery_address") or "?"
    if result["success"]:
        return f"⏳ Czasówka {pickup_at} → Koordynator\n{rest} → {addr}"
    return (
        f"⚠️ Czasówka {pickup_at} — auto-KOORD FAIL\n"
        f"{rest} → {addr}\n"
        f"reason: {result.get('reason')} (attempts={result.get('attempts')})"
    )


def emit_event_log(
    order_id: str,
    order_state: Dict[str, Any],
    result: Dict[str, Any],
    log_fn=None,
) -> None:
    """Emit structured log entry. Defensive: NIGDY raise."""
    try:
        record = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "AUTO_KOORD_ASSIGNED" if result["success"] else "AUTO_KOORD_FAILED",
            "order_id": order_id,
            "czas_odbioru_min": order_state.get("prep_minutes") or order_state.get("czas_odbioru"),
            "panel_response": result.get("panel_response"),
            "attempts": result.get("attempts"),
            "reason": result.get("reason"),
            "skipped": result.get("skipped", False),
        }
        if log_fn:
            log_fn(record)
        else:
            _log.info(f"AUTO_KOORD_LOG {record}")
            # Persistent JSONL log (TASK 4 audit trail)
            try:
                import json
                from pathlib import Path
                log_path = Path("/root/.openclaw/workspace/dispatch_state/auto_koord_log.jsonl")
                log_path.parent.mkdir(parents=True, exist_ok=True)
                with open(log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(record, ensure_ascii=False) + "\n")
            except Exception as ie:
                _log.warning(f"auto_koord_log.jsonl write fail: {ie}")
    except Exception as e:
        _log.warning(f"emit_event_log fail: {e}")


def send_telegram_info(
    text: str,
    chat_id: Optional[int] = None,
    token: Optional[str] = None,
) -> bool:
    """Best-effort Telegram info-only message (NO buttons).

    Defensive: NIGDY raise — caller never blocked by tg fail.
    Test mode (AUTO_KOORD_TELEGRAM_TEST_MODE=1): log "would_send", NIE real call.
    """
    if os.environ.get("AUTO_KOORD_TELEGRAM_TEST_MODE", "0") == "1":
        _log.info(f"AUTO_KOORD_TG_TEST would_send: {text[:120]!r}")
        return True
    # Resolve token
    if token is None:
        token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("BOT_TOKEN")
        if not token:
            try:
                env_path = "/root/.openclaw/workspace/.secrets/telegram.env"
                if os.path.exists(env_path):
                    with open(env_path) as f:
                        for line in f:
                            if line.strip().startswith("TELEGRAM_BOT_TOKEN="):
                                token = line.split("=", 1)[1].strip().strip('"').strip("'")
                                break
            except Exception:
                pass
    if not token:
        _log.warning("AUTO_KOORD_TG no token — skip send")
        return False
    # Resolve chat
    if chat_id is None:
        raw = os.environ.get("AUTO_KOORD_TG_CHAT_ID") or os.environ.get("CZASOWKA_ALERT_CHAT_ID")
        if not raw:
            _log.warning("AUTO_KOORD_TG no chat_id — skip send")
            return False
        try:
            chat_id = int(raw)
        except (ValueError, TypeError):
            return False
    try:
        result = subprocess.run(
            ["curl", "-s", "-X", "POST",
             f"https://api.telegram.org/bot{token}/sendMessage",
             "-d", f"chat_id={chat_id}",
             "-d", f"text={text}"],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and '"ok":true' in result.stdout:
            _log.info(f"AUTO_KOORD_TG_SENT chat={chat_id}")
            return True
        _log.warning(f"AUTO_KOORD_TG fail: {result.stdout[:200]}")
        return False
    except Exception as e:
        _log.warning(f"AUTO_KOORD_TG exception: {e}")
        return False
