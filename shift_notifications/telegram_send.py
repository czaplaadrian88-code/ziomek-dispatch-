"""TASK B SHIFT NOTIFICATIONS — Telegram HTTP send (2026-05-04).

Fire-and-forget POST to api.telegram.org/bot{token}/sendMessage.
NEVER calls getUpdates. NEVER long-polls. The polling is owned by the
existing telegram_approver service.

TEST MODE: env SHIFT_NOTIFY_TELEGRAM_TEST_MODE=1 → log "would_send" and
return True without HTTP call. Used by tests + local smokes.

Token resolution order: env TELEGRAM_BOT_TOKEN, env BOT_TOKEN,
/root/.openclaw/workspace/.secrets/telegram.env (TELEGRAM_BOT_TOKEN=...).
Mirrors auto_koord.send_telegram_info pattern.

Chat id resolution: explicit arg → env TELEGRAM_CHAT_ID → env
AUTO_KOORD_TG_CHAT_ID → flags.json SHIFT_NOTIFY_TARGET_CHAT_ID
(Issue #1 routing fix 2026-05-05 — Z3 konsystencja z czasówkami) →
ADRIAN_CHAT_ID_FALLBACK 8765130486 (backward compat).
"""
from __future__ import annotations

import json
import os
import subprocess
from typing import Optional

from dispatch_v2.common import setup_logger

LOG_DIR = "/root/.openclaw/workspace/scripts/logs/"
_log = setup_logger("shift_notifications.telegram", LOG_DIR + "shift_notifications.log")

TELEGRAM_SECRETS_PATH = "/root/.openclaw/workspace/.secrets/telegram.env"
ADRIAN_CHAT_ID_FALLBACK = 8765130486


def _resolve_token() -> Optional[str]:
    token = os.environ.get("TELEGRAM_BOT_TOKEN") or os.environ.get("BOT_TOKEN")
    if token:
        return token
    try:
        if os.path.exists(TELEGRAM_SECRETS_PATH):
            with open(TELEGRAM_SECRETS_PATH) as f:
                for line in f:
                    if line.strip().startswith("TELEGRAM_BOT_TOKEN="):
                        return line.split("=", 1)[1].strip().strip('"').strip("'")
    except Exception as e:
        _log.warning(f"_resolve_token: secrets read fail {type(e).__name__}: {e}")
    return None


def _resolve_shift_notify_target_chat() -> int:
    """Resolve SHIFT notification target chat (Issue #1 fix 2026-05-05).

    Priority:
      1. flags.json SHIFT_NOTIFY_TARGET_CHAT_ID (preferred — grupa ziomka
         -5149910559, Z3 konsystencja z czasówkami)
      2. ADRIAN_CHAT_ID_FALLBACK (backward compat — 8765130486 DM Adriana)

    Hot-reload: load_flags() re-reads flags.json on mtime change → flag flip
    has natychmiastowy efekt bez restart dispatch-telegram service.
    """
    try:
        from dispatch_v2.common import load_flags
        cfg = load_flags() or {}
        target = cfg.get("SHIFT_NOTIFY_TARGET_CHAT_ID")
        if isinstance(target, int) and target != 0:
            return target
    except Exception as e:
        _log.warning(
            f"_resolve_shift_notify_target_chat fallback: {type(e).__name__}: {e}"
        )
    return ADRIAN_CHAT_ID_FALLBACK


def _resolve_chat_id(explicit: Optional[int]) -> Optional[int]:
    if explicit is not None:
        return int(explicit)
    raw = os.environ.get("TELEGRAM_CHAT_ID") or os.environ.get("AUTO_KOORD_TG_CHAT_ID")
    if raw:
        try:
            return int(raw)
        except (ValueError, TypeError):
            _log.warning(f"_resolve_chat_id: invalid env value {raw!r}")
    # Issue #1 (2026-05-05): single source of truth — flag-based resolver
    # zamiast hardcoded ADRIAN_CHAT_ID_FALLBACK. Worker callers nie muszą
    # nic zmieniać, hot-reload via load_flags().
    return _resolve_shift_notify_target_chat()


def tg_send_text_with_keyboard(
    text: str,
    inline_keyboard: list,
    chat_id: Optional[int] = None,
    *,
    disable_notification: bool = False,
) -> bool:
    """POST sendMessage with inline_keyboard reply_markup.

    disable_notification=True → cicha dostawa (wiadomość trafia do grupy, ale
    bez pinga/dźwięku). Używane dla rutynowych „LOW" alertów grupowych
    (np. nowy kurier wpięty) — patrz centrum powiadomień (2026-06-15).

    Returns True on success. Best-effort: NEVER raises (caller never blocked).
    """
    if os.environ.get("SHIFT_NOTIFY_TELEGRAM_TEST_MODE", "0") == "1":
        _log.info(
            f"SHIFT_NOTIFY_TG_TEST would_send chat={chat_id} "
            f"text={text[:120]!r} buttons={len(inline_keyboard)} rows"
        )
        return True

    token = _resolve_token()
    if not token:
        _log.warning("SHIFT_NOTIFY_TG no token — skip send")
        return False

    target_chat = _resolve_chat_id(chat_id)
    if target_chat is None:
        _log.warning("SHIFT_NOTIFY_TG no chat_id — skip send")
        return False

    reply_markup = {"inline_keyboard": inline_keyboard}
    try:
        result = subprocess.run(
            [
                "curl", "-s", "-X", "POST",
                f"https://api.telegram.org/bot{token}/sendMessage",
                "-d", f"chat_id={target_chat}",
                "-d", f"text={text}",
                "-d", "parse_mode=HTML",
                "-d", f"disable_notification={'true' if disable_notification else 'false'}",
                "-d", f"reply_markup={json.dumps(reply_markup, ensure_ascii=False)}",
            ],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and '"ok":true' in result.stdout:
            _log.info(f"SHIFT_NOTIFY_TG_SENT chat={target_chat}")
            return True
        _log.warning(f"SHIFT_NOTIFY_TG fail: rc={result.returncode} body={result.stdout[:200]}")
        return False
    except Exception as e:
        _log.warning(f"SHIFT_NOTIFY_TG exception: {type(e).__name__}: {e}")
        return False
