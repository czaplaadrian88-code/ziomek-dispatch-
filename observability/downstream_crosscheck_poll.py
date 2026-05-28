#!/usr/bin/env python3
"""Downstream cross-check driver (audit E3b follow-up — arm dormant :8888).

The V3.28 Fix 5 downstream cross-check (Lekcja #67 — the fix for the 12h silent
pipeline failure) lives server-side inside `_HealthHandler._build_health_snapshot`
and only runs on HTTP GET. Nothing polled it, so the protection was DORMANT — it
could never fire autonomously. This oneshot GETs /health/parser on a 5-min timer
so the server-side cross-check + Telegram alert (cooldown 30/60 min) actually run.

Separation of concerns (Lekcja #153): the E1 liveness probe TCP-connects :8888
(proves the health thread is alive, NO side-effect). THIS driver intentionally
issues a GET (triggers the cross-check + alert side-effect). Two distinct jobs,
two units — do not merge them.

Exit discipline (Z2 never-silent):
  - GET ok            -> log observed downstream_status, exit 0
                         (server already fired the alert if critical/degraded)
  - HTTP error code   -> log "endpoint up but HTTP {code}", exit 0 (noted)
  - :8888 unreachable -> log + exit 0  (E1's TCP probe owns "endpoint down";
                         exiting non-zero here would double-alert with E1)
  - unexpected error  -> propagate -> exit != 0 -> systemd OnFailure Telegram
                         (the "who watches the watcher" poller-breakage path)
"""
import json
import sys
import urllib.error
import urllib.request

HEALTH_URL = "http://127.0.0.1:8888/health/parser"
TIMEOUT_SEC = 10.0


def poll_once(url: str = HEALTH_URL, timeout: float = TIMEOUT_SEC) -> int:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            body = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        # Endpoint is up but returned an error code — noted, not the poller's fault.
        print(f"DOWNSTREAM_CROSSCHECK endpoint up but HTTP {e.code}: {e!r}", flush=True)
        return 0
    except (urllib.error.URLError, OSError) as e:
        # Connection refused / timeout = endpoint down = E1's TCP-probe domain.
        # Exit 0 to avoid double-alerting with the liveness probe.
        print(f"DOWNSTREAM_CROSSCHECK unreachable (E1 owns endpoint-down): {e!r}", flush=True)
        return 0
    try:
        data = json.loads(body)
        status = data.get("downstream_status")
        reason = data.get("downstream_reason")
    except (ValueError, AttributeError):
        status, reason = "unparseable", None
    print(
        f"DOWNSTREAM_CROSSCHECK polled :8888/health/parser "
        f"downstream_status={status} reason={reason}",
        flush=True,
    )
    return 0


if __name__ == "__main__":
    sys.exit(poll_once())
