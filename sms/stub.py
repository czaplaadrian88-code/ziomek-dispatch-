"""Stub SMS provider — logs only, no real delivery (MP-#9 dev/test default).

Used when:
  - SMS_PROVIDER not set (default fallback)
  - Adrian nie skonfigurował OVH account jeszcze
  - Tests (no network)

Writes attempted SMS do `dispatch_state/sms_log.jsonl` z {ts, provider, recipient,
message, accepted: True}. Operator może `tail -f` log by zobaczyć "co byłoby wysłane".

Returns True always (treats local file write as "accepted by provider"). NIE
crashuje na file write fail — stub musi być maksymalnie tolerant.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

from dispatch_v2.sms.provider import SMSProvider

_log = logging.getLogger(__name__)

DEFAULT_SMS_LOG_PATH = "/root/.openclaw/workspace/dispatch_state/sms_log.jsonl"


class StubSMSProvider(SMSProvider):
    """Stub provider — logs do JSONL, no real SMS sent."""

    name = "stub"

    def __init__(self, log_path: str = DEFAULT_SMS_LOG_PATH):
        self.log_path = log_path

    def send(self, message: str, recipient: str) -> bool:
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "provider": "stub",
            "recipient": recipient,
            "message": message,
            "accepted": True,
            "note": "STUB — no real SMS sent. Configure SMS_PROVIDER=ovh + OVH creds dla LIVE.",
        }
        try:
            # Use core/jsonl_appender for atomic write (eliminuje race ze sla_tracker writes)
            from dispatch_v2.core.jsonl_appender import append_jsonl
            append_jsonl(self.log_path, rec)
        except Exception as e:
            # Stub MUST NOT crash. Fallback to direct write z basic atomicity.
            _log.warning(f"StubSMSProvider append_jsonl fail: {e} — fallback write")
            try:
                Path(self.log_path).parent.mkdir(parents=True, exist_ok=True)
                with open(self.log_path, "a", encoding="utf-8") as f:
                    f.write(json.dumps(rec, ensure_ascii=False) + "\n")
            except Exception as e2:
                _log.error(f"StubSMSProvider direct write also fail: {e2} — SMS log lost")
                # Still return True — stub success contract
        _log.info(
            f"[STUB SMS] to={recipient} msg={message[:80]!r}{'...' if len(message) > 80 else ''}"
        )
        return True

    def is_configured(self) -> bool:
        return True  # Stub always works
