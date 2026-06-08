"""Puls Ziomka dla asystenta operacyjnego (Faza 1).

Zapisuje atomowo `dispatch_state/ziomek_heartbeat.json` na każdym HEARTBEAT ticku
shadow_dispatchera (~60s). Czytany przez:
  - assistant-watcher (alert R1 „Ziomek milczy >3 min" + mirror do tabeli PG),
  - narzędzie get_ziomek_status (asystent konwersacyjny).

ZASADA: ten moduł NIGDY nie rzuca wyjątku do pętli dispatchu. Każdy błąd =
log+return. Dispatch jest mózgiem produkcyjnym — heartbeat to dodatek obserwacyjny,
nie wolno mu wywrócić głównej pętli (analogicznie do GPS-01 / V328 hooków).
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone

HEARTBEAT_PATH = os.getenv(
    "ZIOMEK_HEARTBEAT_PATH",
    "/root/.openclaw/workspace/dispatch_state/ziomek_heartbeat.json",
)
# Informacyjna etykieta wersji modelu/silnika (override przez env przy bumpie).
MODEL_VERSION = os.getenv("ZIOMEK_MODEL_VERSION", "V3.28")


def write_heartbeat(
    *,
    optimizer_running: bool,
    queue_depth: int,
    processed: int,
    failed: int,
    worker_alive: bool,
    fallback_active: bool,
    avg_assignment_ms: int | None = None,
    active_orders: int | None = None,
    active_couriers: int | None = None,
    pending_reoptimization: int = 0,
    logger=None,
) -> None:
    """Atomowy, fail-safe zapis pulsu. active_orders/active_couriers domyślnie None —
    wzbogaca je watcher przy mirrorze do PG (czyta orders_state niezależnie)."""
    try:
        payload = {
            "optimizer_running": bool(optimizer_running),
            "last_run": datetime.now(timezone.utc).isoformat(),
            "active_orders": active_orders,
            "active_couriers": active_couriers,
            "avg_assignment_ms": avg_assignment_ms,
            "pending_reoptimization": int(pending_reoptimization or 0),
            "model_version": MODEL_VERSION,
            "fallback_active": bool(fallback_active),
            "queue_depth": int(queue_depth or 0),
            "worker_alive": bool(worker_alive),
            "processed_total": int(processed or 0),
            "failed_total": int(failed or 0),
        }
        directory = os.path.dirname(HEARTBEAT_PATH)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".hb_", suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as fh:
                json.dump(payload, fh, ensure_ascii=False)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, HEARTBEAT_PATH)
        finally:
            if os.path.exists(tmp):
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
    except Exception as exc:  # noqa: BLE001 — fail-safe boundary, nigdy nie propaguj
        if logger is not None:
            try:
                logger.warning(
                    f"assistant_heartbeat write fail "
                    f"({type(exc).__name__}: {exc}) — pominięto"
                )
            except Exception:
                pass
