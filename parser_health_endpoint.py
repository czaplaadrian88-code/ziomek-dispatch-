"""V3.28 PARSER-RESILIENCE Layer 4 — HTTP health endpoint dla parser observability.

Endpoint: GET http://localhost:8888/health/parser

Returns JSON snapshot z parser_health monitor stats. Dla operacyjnego monitoring
(curl, Prometheus exporter, dashboard polling).

Architecture (Z3):
- stdlib `http.server` (zero deps — NIE flask/fastapi/aiohttp)
- Daemon thread (NIE blokuje panel_watcher startup/shutdown)
- Sentinel ENABLE_PARSER_HEALTH_ENDPOINT (default ON)
- Defense-in-depth: każdy handler try/except, NIE crash thread
- Lazy bind (start gdy wywołano start_health_endpoint())
- Bind 127.0.0.1 only — local monitoring (NIE expose na public)

Response schema:
    {
      "status": "healthy" | "degraded" | "critical",
      "last_fetch_ts": "2026-05-02T13:30:00+00:00",
      "orders_count": 180,
      "delta_last_5_cycles": [180, 180, 180, 180, 180],
      "anomaly_detected": bool,
      "anomaly_reason": list | null,
      "parser_version": "v2",
      "uptime_seconds": int,
      "known_ids_window_size": int,
      "endpoint_version": "1"
    }

Status: NIE WDROŻONE. Wymaga ACK Adriana po Gate 3.
"""
from __future__ import annotations

import json
import logging
import os
import threading
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from typing import Any, Dict, Optional

log = logging.getLogger(__name__)


HEALTH_ENDPOINT_HOST = os.environ.get("PARSER_HEALTH_ENDPOINT_HOST", "127.0.0.1")
HEALTH_ENDPOINT_PORT = int(os.environ.get("PARSER_HEALTH_ENDPOINT_PORT", "8888"))
ENABLE_HEALTH_ENDPOINT = os.environ.get("ENABLE_PARSER_HEALTH_ENDPOINT", "1") == "1"

_started_at: float = time.time()
_server_thread: Optional[threading.Thread] = None
_server_instance: Optional[HTTPServer] = None
_lock = threading.Lock()


# V3.28 Fix 5 (incident 03.05.2026, Lekcja #67): downstream pipeline cross-check.
# Pre-flight 9:55 raportował GREEN (parser healthy) mimo że pipeline silent 12h.
# Health endpoint extends z 4 cross-check signals + computed downstream_status.
EVENTS_DB_PATH = "/root/.openclaw/workspace/dispatch_state/events.db"
DISPATCH_LOG_PATH = "/root/.openclaw/workspace/scripts/logs/dispatch.log"

# V3.28 Fix 5c (Sprint Z 03.05.2026): auto Telegram alert dla downstream critical/degraded.
# Lekcja #67 closing loop: pre-flight diagnostic ma truthful multi-signal (Fix 5+5b) ALE
# Adrian dowiaduje się o critical TYLKO ręcznie via curl. Fix 5c = proactive Telegram push
# z cooldown gating (NIE spam — Lekcja #65 adaptive thresholds).
ALERT_STATE_PATH = "/tmp/health_alert_state.json"
ALERT_COOLDOWN_CRITICAL_MIN = int(os.environ.get("ALERT_COOLDOWN_CRITICAL_MIN", "30"))
ALERT_COOLDOWN_DEGRADED_MIN = int(os.environ.get("ALERT_COOLDOWN_DEGRADED_MIN", "60"))
HEALTH_ALERT_GROUP_CHAT_ID = -5149910559  # Grupa ziomka


def _v328_load_alert_state() -> Dict[str, Any]:
    """V3.28 Fix 5c: load last alert timestamps z atomic state file."""
    try:
        with open(ALERT_STATE_PATH) as f:
            return json.load(f)
    except Exception:
        return {"last_critical_ts": None, "last_degraded_ts": None}


def _v328_save_alert_state(state: Dict[str, Any]) -> None:
    """V3.28 Fix 5c: atomic temp+rename write."""
    tmp = ALERT_STATE_PATH + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(state, f)
        os.replace(tmp, ALERT_STATE_PATH)
    except Exception as e:
        log.warning(f"V328_HEALTH_ALERT_STATE_SAVE_FAIL: {e}")


def _v328_should_alert(downstream_status: str, state: Dict[str, Any], now_ts: float) -> bool:
    """V3.28 Fix 5c: cooldown-aware alert decision.

    - critical → cooldown 30 min default (env ALERT_COOLDOWN_CRITICAL_MIN)
    - degraded → cooldown 60 min default (env ALERT_COOLDOWN_DEGRADED_MIN)
    - ok → never alert
    - DISABLE_HEALTH_AUTO_ALERT=1 → never alert (rollback flag)
    """
    if downstream_status == "ok":
        return False
    if os.environ.get("DISABLE_HEALTH_AUTO_ALERT", "0") == "1":
        return False
    if downstream_status == "critical":
        last_ts = state.get("last_critical_ts")
        cooldown_min = ALERT_COOLDOWN_CRITICAL_MIN
    elif downstream_status == "degraded":
        last_ts = state.get("last_degraded_ts")
        cooldown_min = ALERT_COOLDOWN_DEGRADED_MIN
    else:
        return False
    if last_ts is None:
        return True
    elapsed_min = (now_ts - float(last_ts)) / 60.0
    return elapsed_min >= cooldown_min


def _v328_send_health_alert(downstream_status: str, downstream_reason: Optional[str]) -> bool:
    """V3.28 Fix 5c: send Telegram alert via subprocess curl Telegram API.

    Test mode (HEALTH_ALERT_TEST_MODE=1): log "would_send" only, NIE real send.
    Returns True jeśli sent (lub test logged), False jeśli error.
    """
    icon = "\U0001F534" if downstream_status == "critical" else "\U0001F7E1"
    msg = (
        f"{icon} ZIOMEK HEALTH {downstream_status.upper()}: {downstream_reason or '?'}\n"
        f"Adrian sprawdz: curl http://localhost:8888/health/parser"
    )
    if os.environ.get("HEALTH_ALERT_TEST_MODE", "0") == "1":
        log.info(f"V328_HEALTH_ALERT_TEST would_send status={downstream_status} msg_preview={msg[:120]!r}")
        return True
    try:
        import subprocess
        token_file = "/root/.openclaw/workspace/.secrets/telegram.env"
        token = None
        if os.path.exists(token_file):
            with open(token_file) as f:
                for line in f:
                    if line.strip().startswith("TELEGRAM_BOT_TOKEN="):
                        token = line.split("=", 1)[1].strip().strip('"').strip("'")
                        break
        if not token:
            log.warning("V328_HEALTH_ALERT_NO_TOKEN — skip send")
            return False
        result = subprocess.run(
            [
                "curl", "-s", "-X", "POST",
                f"https://api.telegram.org/bot{token}/sendMessage",
                "-d", f"chat_id={HEALTH_ALERT_GROUP_CHAT_ID}",
                "-d", f"text={msg}",
            ],
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0 and '"ok":true' in result.stdout:
            log.info(f"V328_HEALTH_ALERT_SENT status={downstream_status} reason={downstream_reason!r}")
            return True
        log.warning(f"V328_HEALTH_ALERT_FAIL response={result.stdout[:200]}")
        return False
    except Exception as e:
        log.warning(f"V328_HEALTH_ALERT_EXCEPTION: {e}")
        return False

V328_DOWNSTREAM_PIPELINE_SILENT_AGE_SEC = int(
    os.environ.get("V328_DOWNSTREAM_PIPELINE_SILENT_AGE_SEC", "1800")
)
V328_DOWNSTREAM_FAILED_1H_THRESHOLD = int(
    os.environ.get("V328_DOWNSTREAM_FAILED_1H_THRESHOLD", "5")
)
V328_DOWNSTREAM_WORKER_SLOW_AGE_SEC = int(
    os.environ.get("V328_DOWNSTREAM_WORKER_SLOW_AGE_SEC", "600")
)


def _v328_query_events_stats(events_db_path: str = EVENTS_DB_PATH) -> Dict[str, Any]:
    """V3.28 Fix 5b (incident 03.05.2026): query events.db dla failed_1h + new_orders_1h.

    Pre-Fix-5b: try query PROPOSAL_SENT (event_type NIE ISTNIEJE w events.db,
    zawsze None). Top event_types empirically: COURIER_ASSIGNED, COURIER_DELIVERED,
    NEW_ORDER, COURIER_PICKED_UP, CZAS_KURIERA_UPDATED — NO PROPOSAL_SENT.
    Post-Fix-5b: PROPOSAL_SENT moved to separate function reading journalctl
    dispatch-telegram (osobny source).

    Returns dict:
    - events_failed_last_1h_count: int
    - new_orders_last_1h_count: int (cross-check signal)
    """
    import sqlite3
    result = {
        "events_failed_last_1h_count": 0,
        "new_orders_last_1h_count": 0,
    }
    try:
        conn = sqlite3.connect(f"file:{events_db_path}?mode=ro", uri=True, timeout=2.0)
        cur = conn.cursor()
        # failed last 1h
        cur.execute(
            """
            SELECT COUNT(*) FROM events
            WHERE status='failed'
              AND datetime(created_at) > datetime('now', '-1 hour')
            """
        )
        row = cur.fetchone()
        result["events_failed_last_1h_count"] = int(row[0]) if row else 0
        # new orders last 1h (cross-check signal)
        cur.execute(
            """
            SELECT COUNT(*) FROM events
            WHERE event_type='NEW_ORDER'
              AND datetime(created_at) > datetime('now', '-1 hour')
            """
        )
        row = cur.fetchone()
        result["new_orders_last_1h_count"] = int(row[0]) if row else 0
        conn.close()
    except Exception as e:
        log.debug(f"health endpoint: events.db query fail: {e}")
    return result


def _v328_parse_last_propose_age_from_journal() -> Optional[float]:
    """V3.28 Fix 5b (incident 03.05.2026): last propose age via journalctl dispatch-telegram.

    Pre-Fix-5b: SQL on events.db PROPOSAL_SENT zawsze None (event_type NIE ISTNIEJE).
    Post-Fix-5b: parse journalctl dispatch-telegram dla `SENT oid=` linii.
    Format: 'May 03 16:24:12 Ziomek python[N]: 2026-05-03 16:24:12 [INFO]
            telegram_approver: SENT oid=X msg=Y'

    Returns float age_sec lub None gdy żadnych SENT w 2h window.
    """
    try:
        import subprocess
        result = subprocess.run(
            ["journalctl", "-u", "dispatch-telegram", "--since", "2 hours ago",
             "--no-pager", "-q"],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        if result.returncode != 0:
            return None
        import re
        ts_pattern = re.compile(r"(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}).*SENT oid=")
        last_ts = None
        for line in result.stdout.splitlines():
            m = ts_pattern.search(line)
            if m:
                last_ts = m.group(1)  # last match = most recent
        if last_ts:
            last_dt = datetime.fromisoformat(last_ts).replace(tzinfo=timezone.utc)
            age_sec = (datetime.now(timezone.utc) - last_dt).total_seconds()
            return max(0.0, age_sec)
    except Exception as e:
        log.debug(f"health endpoint: parse last_propose via journalctl fail: {e}")
    return None


def _v328_parse_worker_age_from_log(_unused_path: str = "") -> Optional[float]:
    """V3.28 Fix 5b (incident 03.05.2026): worker_age via journalctl dispatch-shadow.

    Pre-Fix-5b: parsed dispatch.log (= panel_watcher log file) → null bo
    HEARTBEAT shadow_dispatcher idzie do journalctl, NIE do dispatch.log.
    Post-Fix-5b: subprocess journalctl --since 5 minutes ago dispatch-shadow
    → grep HEARTBEAT → regex last_processed_age_sec=K.

    Empirically verified TRACK 2-A research: extracted ages [15.0, 30.0, 40.0]
    z subprocess output. Defensive None fallback preserved.

    Args:
        _unused_path: legacy parameter, ignored (was DISPATCH_LOG_PATH).
    """
    try:
        import subprocess
        result = subprocess.run(
            ["journalctl", "-u", "dispatch-shadow", "--since", "5 minutes ago",
             "--no-pager", "-q"],
            capture_output=True,
            text=True,
            timeout=5.0,
        )
        if result.returncode != 0:
            return None
        import re
        matches = re.findall(r"last_processed_age_sec=([0-9.]+)", result.stdout)
        if matches:
            return float(matches[-1])  # last = najnowszy HEARTBEAT
    except Exception as e:
        log.debug(f"health endpoint: parse worker_age via journalctl fail: {e}")
    return None


def _v328_compute_downstream_status(
    last_proposal_age_sec: Optional[float],
    events_failed_1h: int,
    new_orders_1h: int,
    worker_age_sec: Optional[float],
) -> Dict[str, Any]:
    """V3.28 Fix 5 helper: compute downstream_status + reason z cross-check signals.

    Lekcja #67: pre-flight diagnostic MUST cross-check primary output produced
    RIGHT NOW, NIE tylko parser metadata.

    Priority order (critical first):
    1. PIPELINE_SILENT_DESPITE_WORK (critical) — last_proposal_age > 30min AND new_orders > 0
    2. WORKER_STUCK (critical) — worker_age > worker_slow * 2 (twice slow threshold)
    3. EVENTS_FAILED_HIGH (degraded) — events_failed_1h > threshold (5)
    4. WORKER_SLOW (degraded) — worker_age > slow threshold
    5. ok (no anomaly)

    Returns dict z 'downstream_status' (ok|degraded|critical) + 'downstream_reason'.
    """
    # Critical priority — pipeline silent despite work
    if (
        last_proposal_age_sec is not None
        and last_proposal_age_sec > V328_DOWNSTREAM_PIPELINE_SILENT_AGE_SEC
        and new_orders_1h > 0
    ):
        return {
            "downstream_status": "critical",
            "downstream_reason": "pipeline_silent_despite_work",
        }
    # Critical — worker hard stuck (twice slow threshold)
    if worker_age_sec is not None and worker_age_sec > V328_DOWNSTREAM_WORKER_SLOW_AGE_SEC * 2:
        return {
            "downstream_status": "critical",
            "downstream_reason": "worker_stuck",
        }
    # Degraded — elevated failures
    if events_failed_1h > V328_DOWNSTREAM_FAILED_1H_THRESHOLD:
        return {
            "downstream_status": "degraded",
            "downstream_reason": "elevated_failure_rate",
        }
    # Degraded — worker slow
    if worker_age_sec is not None and worker_age_sec > V328_DOWNSTREAM_WORKER_SLOW_AGE_SEC:
        return {
            "downstream_status": "degraded",
            "downstream_reason": "worker_slow",
        }
    return {
        "downstream_status": "ok",
        "downstream_reason": None,
    }


class _HealthHandler(BaseHTTPRequestHandler):
    """Quiet, defensive handler. NIE log każdy request (zero spam)."""

    def log_message(self, format, *args):
        # Suppress default request logs — tylko error level w naszym logger
        pass

    def _send_json(self, status_code: int, payload: Dict[str, Any]) -> None:
        try:
            body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
            self.send_response(status_code)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-cache, no-store, must-revalidate")
            self.end_headers()
            self.wfile.write(body)
        except Exception as e:
            log.warning(f"_send_json fail (non-blocking): {e}")

    def do_GET(self):
        try:
            if self.path == "/health/parser":
                payload = self._build_health_snapshot()
                http_status = 200 if payload.get("status") in ("healthy", "degraded") else 503
                self._send_json(http_status, payload)
            elif self.path == "/health":
                # Top-level health alias (simpler curl-able)
                payload = {"status": "ok", "endpoint": "/health/parser", "uptime_seconds": int(time.time() - _started_at)}
                self._send_json(200, payload)
            else:
                self._send_json(404, {"error": "not_found", "available": ["/health/parser", "/health"]})
        except Exception as e:
            log.warning(f"_HealthHandler.do_GET fail (non-blocking): {e}")
            try:
                self._send_json(500, {"error": "internal", "type": type(e).__name__})
            except Exception:
                pass

    def _build_health_snapshot(self) -> Dict[str, Any]:
        """Compose snapshot z monitor + KnownIdsWindow + uptime."""
        try:
            from dispatch_v2.parser_health import get_monitor
            monitor = get_monitor()
            base_snap = monitor.get_health_snapshot()
        except Exception as e:
            log.warning(f"health endpoint: get_monitor fail: {e}")
            base_snap = {"status": "error", "reason": f"monitor_unavailable: {type(e).__name__}", "cycles_recorded": 0}

        # Layer 3: known_ids_window size (jeśli installed)
        known_ids_size = 0
        try:
            if hasattr(monitor, "_known_ids_window") and monitor._known_ids_window is not None:
                known_ids_size = len(monitor._known_ids_window.get_known())
        except Exception as e:
            log.debug(f"health endpoint: known_ids size fail: {e}")

        # Parser version (try detect z env / running config)
        parser_version = "v2" if os.environ.get("USE_V2_PARSER", "0") == "1" else "v1"
        if os.environ.get("ENABLE_V2_SHADOW_COMPARE", "1") == "1" and parser_version == "v1":
            parser_version = "v1+v2_shadow"

        # V3.28 Fix 5 (incident 03.05.2026, Lekcja #67): downstream cross-check.
        # Pre-flight diagnostic 9:55 dał false GREEN bo health endpoint widział
        # parser zdrowy, ALE pipeline silent 12h (CP Solver crash). Lekcja #67:
        # "system MUST cross-check primary output produced RIGHT NOW, NIE tylko
        # parser metadata".
        events_stats = _v328_query_events_stats()
        worker_age_sec = _v328_parse_worker_age_from_log()
        # V3.28 Fix 5b: last_proposal_age z osobnego źródła (journalctl dispatch-telegram)
        last_proposal_age_sec = _v328_parse_last_propose_age_from_journal()
        downstream = _v328_compute_downstream_status(
            last_proposal_age_sec=last_proposal_age_sec,
            events_failed_1h=events_stats.get("events_failed_last_1h_count", 0),
            new_orders_1h=events_stats.get("new_orders_last_1h_count", 0),
            worker_age_sec=worker_age_sec,
        )

        snapshot = {
            "endpoint_version": "2",  # V3.28 Fix 5 — bump dla downstream fields
            "checked_at": datetime.now(timezone.utc).isoformat(),
            "status": base_snap.get("status", "unknown"),
            "last_fetch_ts": base_snap.get("last_tick_ts"),
            "orders_count": base_snap.get("last_orders_in_panel"),
            "delta_last_5_cycles": (base_snap.get("recent_orders_window") or [])[-5:],
            "anomaly_detected": bool(base_snap.get("anomalies_active")),
            "anomaly_reason": base_snap.get("anomalies_active") or None,
            "parser_version": parser_version,
            "uptime_seconds": int(time.time() - _started_at),
            "known_ids_window_size": known_ids_size,
            "cycles_recorded": base_snap.get("cycles_recorded", 0),
            "init_count": base_snap.get("init_count", 0),
            "error_count": base_snap.get("error_count", 0),
            "thresholds": base_snap.get("thresholds", {}),
            # V3.28 Fix 5 downstream cross-check fields (post Fix 5b: source switched)
            "last_proposal_sent_age_sec": last_proposal_age_sec,  # Fix 5b: journalctl dispatch-telegram
            "events_failed_last_1h_count": events_stats.get("events_failed_last_1h_count", 0),
            "new_orders_last_1h_count": events_stats.get("new_orders_last_1h_count", 0),
            "worker_processed_age_sec": worker_age_sec,  # Fix 5b: journalctl dispatch-shadow
            "downstream_status": downstream["downstream_status"],
            "downstream_reason": downstream["downstream_reason"],
        }

        # V3.28 Fix 5c (Sprint Z 03.05): auto Telegram alert na critical/degraded.
        # Cooldown gated (Lekcja #65 adaptive 30/60 min). NIE blocking endpoint response —
        # try/except defensive (alert fail NIE wpływa na health response).
        try:
            if downstream["downstream_status"] in ("critical", "degraded"):
                state = _v328_load_alert_state()
                now_ts = time.time()
                if _v328_should_alert(downstream["downstream_status"], state, now_ts):
                    sent = _v328_send_health_alert(
                        downstream["downstream_status"],
                        downstream["downstream_reason"],
                    )
                    if sent:
                        if downstream["downstream_status"] == "critical":
                            state["last_critical_ts"] = now_ts
                        else:
                            state["last_degraded_ts"] = now_ts
                        _v328_save_alert_state(state)
        except Exception as _v328_alert_e:
            log.warning(f"V328_HEALTH_ALERT_HOOK_FAIL: {_v328_alert_e}")

        return snapshot


def start_health_endpoint(host: str = HEALTH_ENDPOINT_HOST, port: int = HEALTH_ENDPOINT_PORT) -> bool:
    """Idempotent start daemon thread serving health endpoint.

    Returns True jeśli started (or already running), False on bind fail.
    Defense-in-depth: NIGDY raise — fail silent z log WARNING.
    """
    global _server_thread, _server_instance, _started_at
    if not ENABLE_HEALTH_ENDPOINT:
        log.info("parser_health_endpoint DISABLED (env ENABLE_PARSER_HEALTH_ENDPOINT=0)")
        return False
    with _lock:
        if _server_thread is not None and _server_thread.is_alive():
            log.debug("parser_health_endpoint already running")
            return True
        try:
            _server_instance = HTTPServer((host, port), _HealthHandler)
        except OSError as e:
            log.warning(f"parser_health_endpoint bind {host}:{port} fail: {e} (non-blocking)")
            return False

        def _serve():
            try:
                _server_instance.serve_forever(poll_interval=1.0)
            except Exception as e:
                log.warning(f"parser_health_endpoint serve fail: {e}")

        _started_at = time.time()
        _server_thread = threading.Thread(
            target=_serve, name="parser_health_endpoint", daemon=True
        )
        _server_thread.start()
        log.info(f"parser_health_endpoint started: http://{host}:{port}/health/parser")
        return True


def stop_health_endpoint() -> None:
    """Stop endpoint (głównie dla testów). Production NIE musi wywołać — daemon dies z procesem."""
    global _server_instance, _server_thread
    with _lock:
        if _server_instance is not None:
            try:
                _server_instance.shutdown()
                _server_instance.server_close()
            except Exception as e:
                log.warning(f"stop_health_endpoint fail: {e}")
            _server_instance = None
        if _server_thread is not None:
            _server_thread.join(timeout=2.0)
            _server_thread = None
