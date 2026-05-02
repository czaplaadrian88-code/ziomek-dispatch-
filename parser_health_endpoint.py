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

        snapshot = {
            "endpoint_version": "1",
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
