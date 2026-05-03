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
    """V3.28 Fix 5 helper: query events.db dla downstream cross-check signals.

    Read-only sqlite query. Defensive — return zero defaults gdy DB unavailable.

    Returns dict z polami:
    - last_proposal_sent_age_sec: Optional[float] — seconds od ostatniego
      PROPOSAL_SENT (None gdy NIGDY nie było)
    - events_failed_last_1h_count: int — failed events w ostatniej godzinie
    - new_orders_last_1h_count: int — NEW_ORDER events w ostatniej godzinie
      (used dla cross-check "pipeline silent despite work")
    """
    import sqlite3
    result = {
        "last_proposal_sent_age_sec": None,
        "events_failed_last_1h_count": 0,
        "new_orders_last_1h_count": 0,
    }
    try:
        conn = sqlite3.connect(f"file:{events_db_path}?mode=ro", uri=True, timeout=2.0)
        cur = conn.cursor()
        # last PROPOSAL_SENT — NIE gwarantuje events.db ma ten event_type w schema
        # z normalizacją statusu. Fallback do `processed_at` z najnowszej processed.
        cur.execute(
            "SELECT MAX(processed_at) FROM events WHERE event_type='PROPOSAL_SENT' AND status='processed'"
        )
        row = cur.fetchone()
        last_propose_iso = row[0] if row else None
        if last_propose_iso:
            try:
                last_dt = datetime.fromisoformat(str(last_propose_iso).replace("Z", "+00:00"))
                if last_dt.tzinfo is None:
                    last_dt = last_dt.replace(tzinfo=timezone.utc)
                age_sec = (datetime.now(timezone.utc) - last_dt).total_seconds()
                result["last_proposal_sent_age_sec"] = max(0.0, age_sec)
            except Exception as e:
                log.debug(f"health endpoint: parse last_propose_iso fail: {e}")
        # failed last 1h — default windowing przez created_at (recent)
        cur.execute(
            """
            SELECT COUNT(*) FROM events
            WHERE status='failed'
              AND datetime(created_at) > datetime('now', '-1 hour')
            """
        )
        row = cur.fetchone()
        result["events_failed_last_1h_count"] = int(row[0]) if row else 0
        # new orders last 1h
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


def _v328_parse_worker_age_from_log(log_path: str = DISPATCH_LOG_PATH) -> Optional[float]:
    """V3.28 Fix 5 helper: parse last HEARTBEAT z dispatch.log dla worker_processed_age.

    Read tail of log file (last 200 lines) i find last HEARTBEAT line z polem
    `last_processed_age_sec=K` (Fix 3 format). Returns float or None.
    """
    try:
        import subprocess
        out = subprocess.check_output(
            ["tail", "-n", "200", log_path],
            stderr=subprocess.DEVNULL,
            timeout=2.0,
        ).decode("utf-8", errors="ignore")
        # Search backwards dla najnowszego HEARTBEAT z last_processed_age_sec
        import re
        matches = re.findall(r"last_processed_age_sec=([0-9.]+)", out)
        if matches:
            return float(matches[-1])
    except Exception as e:
        log.debug(f"health endpoint: parse worker_age fail: {e}")
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
        downstream = _v328_compute_downstream_status(
            last_proposal_age_sec=events_stats.get("last_proposal_sent_age_sec"),
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
            # V3.28 Fix 5 downstream cross-check fields
            "last_proposal_sent_age_sec": events_stats.get("last_proposal_sent_age_sec"),
            "events_failed_last_1h_count": events_stats.get("events_failed_last_1h_count", 0),
            "new_orders_last_1h_count": events_stats.get("new_orders_last_1h_count", 0),
            "worker_processed_age_sec": worker_age_sec,
            "downstream_status": downstream["downstream_status"],
            "downstream_reason": downstream["downstream_reason"],
        }
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
