"""Event Bus — idempotentna kolejka zdarzen oparta o SQLite.

Klucze architektoniczne:
- event_id jest deterministyczny: {order_id}_{event_type}_{timestamp_ms}
  -> emit dwa razy tego samego eventu = brak duplikatu
- WAL mode = bezpieczny dostep z wielu procesow
- Transakcje atomic (BEGIN IMMEDIATE) = zero race conditions
- Zamkniety katalog typow eventow (EVENT_TYPES) = zero chaosu
- Retencja: processed_events starsze niz 48h czyszczone w cleanup()
"""
import json
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from dispatch_v2.common import load_config, now_iso, setup_logger

# Zamkniety katalog typow eventow
EVENT_TYPES = {
    "NEW_ORDER",
    "ORDER_READY",
    "COURIER_PICKED_UP",
    "COURIER_DELIVERED",
    "COURIER_ASSIGNED",
    "COURIER_REJECTED_PROPOSAL",
    "ORDER_RETURNED_TO_POOL",
    "KOORDYNATOR_DEADLINE",
    "GPS_STALE",
    "PANEL_UNREACHABLE",
    "HEARTBEAT_STALL",
    "SHIFT_END_APPROACHING",
    # V3.19g1 incomplete deployment fix (Bug B sprint 2026-04-25):
    # panel_watcher detected czas_kuriera changes (panel "+15min" button) and
    # emitted this event, ale brakowało w allowlist → event_bus.py:76 raises
    # ValueError → state_machine NIE dostaje update → orders_state stale czas_kuriera.
    # state_machine.py:316 ma już handler. Dodanie do allowlist completes V3.19g1.
    "CZAS_KURIERA_UPDATED",
}

# Opcja C (2026-05-07): rozdzielenie ról events.db (queue vs audit log).
# AUDIT_EVENT_TYPES — typy zapisywane do osobnej tabeli audit_log (append-only,
# retention 90d). Stan persistowany przez state_machine.update_from_event
# wywoływany inline z call site (dual-write pattern). NIKT nie konsumuje queue-style.
# Czytelnicy: learning_analyzer, parser_health_endpoint, r04_evaluator,
# sprint2_analysis (queries WHERE event_type=...).
AUDIT_EVENT_TYPES = {
    "COURIER_ASSIGNED",
    "CZAS_KURIERA_UPDATED",
    "PANEL_UNREACHABLE",
    "ORDER_RETURNED_TO_POOL",
}

# QUEUE_EVENT_TYPES — typy z lifecycle pending → processed w tabeli events.
# Konsumenci: shadow_dispatcher (NEW_ORDER) + sla_tracker (PICKED_UP, DELIVERED).
QUEUE_EVENT_TYPES = EVENT_TYPES - AUDIT_EVENT_TYPES

_log = setup_logger("event_bus", "/root/.openclaw/workspace/scripts/logs/events.log")


def _db_path() -> str:
    return load_config()["paths"]["events_db"]


@contextmanager
def _conn():
    """Kontekstowy connection manager z WAL + busy timeout."""
    conn = sqlite3.connect(_db_path(), timeout=10.0, isolation_level=None)
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA busy_timeout=5000;")
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def make_event_id(event_type: str, order_id: Optional[str], timestamp_ms: Optional[int] = None) -> str:
    """Deterministyczne event_id. Ten sam event z tego samego momentu = ten sam ID."""
    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)
    oid = order_id if order_id else "none"
    return f"{oid}_{event_type}_{timestamp_ms}"


def emit(
    event_type: str,
    order_id: Optional[str] = None,
    courier_id: Optional[str] = None,
    payload: Optional[dict] = None,
    event_id: Optional[str] = None,
) -> Optional[str]:
    """Emituje event. Zwraca event_id lub None jesli duplikat.

    Jesli event_id nie podany -> generowany deterministycznie.
    Jesli event_id juz istnieje w bazie -> ZWRACA None (idempotent skip).
    """
    if event_type not in EVENT_TYPES:
        raise ValueError(f"Nieznany event_type: {event_type}. Dozwolone: {EVENT_TYPES}")

    if event_id is None:
        event_id = make_event_id(event_type, order_id)

    payload_json = json.dumps(payload or {}, ensure_ascii=False)
    created_at = now_iso()

    with _conn() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE;")
            # Sprawdz processed (dedup historyczny)
            cur = conn.execute(
                "SELECT 1 FROM processed_events WHERE event_id = ?",
                (event_id,),
            )
            if cur.fetchone():
                conn.execute("COMMIT;")
                _log.debug(f"DUP (processed): {event_id}")
                return None

            # Wstaw (jesli juz w events -> INSERT OR IGNORE)
            cur = conn.execute(
                """INSERT OR IGNORE INTO events
                   (event_id, event_type, order_id, courier_id, payload, created_at, status)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
                (event_id, event_type, order_id, courier_id, payload_json, created_at),
            )
            if cur.rowcount == 0:
                conn.execute("COMMIT;")
                _log.debug(f"DUP (pending): {event_id}")
                return None

            conn.execute("COMMIT;")
            _log.info(f"EMIT {event_type} order={order_id} courier={courier_id} id={event_id}")
            return event_id
        except Exception as e:
            conn.execute("ROLLBACK;")
            _log.error(f"emit() error: {e}")
            raise


def get_pending(limit: int = 100, event_types: Optional[list] = None) -> list:
    """Zwraca liste pending events posortowanych po created_at (FIFO).
    Opcjonalnie filtruje po event_types (lista stringow)."""
    with _conn() as conn:
        if event_types:
            placeholders = ",".join("?" * len(event_types))
            params = tuple(event_types) + (limit,)
            cur = conn.execute(
                f"""SELECT event_id, event_type, order_id, courier_id, payload, created_at
                    FROM events WHERE status = 'pending' AND event_type IN ({placeholders})
                    ORDER BY created_at ASC LIMIT ?""",
                params,
            )
        else:
            cur = conn.execute(
                """SELECT event_id, event_type, order_id, courier_id, payload, created_at
                   FROM events WHERE status = 'pending'
                   ORDER BY created_at ASC LIMIT ?""",
                (limit,),
            )
        return [dict(row) | {"payload": json.loads(row["payload"])} for row in cur.fetchall()]


def mark_processed(event_id: str) -> bool:
    """Oznacza event jako processed i przenosi do processed_events (dedup)."""
    processed_at = now_iso()
    with _conn() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE;")
            conn.execute(
                "UPDATE events SET status = 'processed', processed_at = ? WHERE event_id = ?",
                (processed_at, event_id),
            )
            conn.execute(
                "INSERT OR IGNORE INTO processed_events (event_id, processed_at) VALUES (?, ?)",
                (event_id, processed_at),
            )
            conn.execute("COMMIT;")
            return True
        except Exception as e:
            conn.execute("ROLLBACK;")
            _log.error(f"mark_processed() error: {e}")
            return False


def mark_failed(event_id: str, error: str) -> bool:
    """Oznacza event jako failed (do ponownej analizy)."""
    with _conn() as conn:
        conn.execute(
            "UPDATE events SET status = 'failed', processed_at = ? WHERE event_id = ?",
            (now_iso(), event_id),
        )
        _log.warning(f"FAILED {event_id}: {error}")
        return True


def stats() -> dict:
    """Statystyki event busa — do safe mode i debugowania."""
    with _conn() as conn:
        cur = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM events GROUP BY status"
        )
        by_status = {row["status"]: row["cnt"] for row in cur.fetchall()}
        cur = conn.execute("SELECT COUNT(*) as cnt FROM processed_events")
        processed_total = cur.fetchone()["cnt"]
        return {
            "pending": by_status.get("pending", 0),
            "processed": by_status.get("processed", 0),
            "failed": by_status.get("failed", 0),
            "processed_history": processed_total,
        }


def cleanup(retention_hours: int = 48) -> int:
    """Czysci stare processed events. Zwraca liczbe usunietych."""
    cutoff_seconds = retention_hours * 3600
    cutoff_iso = now_iso()  # uzywamy current time - retention w SQL

    with _conn() as conn:
        # SQLite: usun processed_events starsze niz cutoff
        cur = conn.execute(
            """DELETE FROM processed_events
               WHERE processed_at < datetime('now', ?)""",
            (f"-{retention_hours} hours",),
        )
        deleted1 = cur.rowcount
        # Usun rowniez events z tabeli events jesli processed
        cur = conn.execute(
            """DELETE FROM events
               WHERE status = 'processed' AND processed_at < datetime('now', ?)""",
            (f"-{retention_hours} hours",),
        )
        deleted2 = cur.rowcount
        _log.info(f"cleanup: usunieto {deleted1} processed_events, {deleted2} events")
        return deleted1 + deleted2


# ─────────────────────────────────────────────────────────────────────────
# Opcja C (2026-05-07): audit_log table — append-only, retention 90d.
# Rozdziela role events.db: queue (pending→processed) vs audit log (append-only).
# Dual-write call sites (panel_watcher, dispatch_pipeline) używają emit_audit()
# zamiast emit() dla typów w AUDIT_EVENT_TYPES.
# ─────────────────────────────────────────────────────────────────────────


_audit_log_initialized = False


def _init_audit_log_table() -> None:
    """Idempotentna inicjalizacja tabeli audit_log + indeksów. CREATE IF NOT EXISTS.

    Wywoływane lazy (przy pierwszym emit_audit/cleanup_audit_log/get_pending_count)
    żeby nie crashować module load w test env gdzie _db_path() może nie być dostępny.
    """
    global _audit_log_initialized
    with _conn() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS audit_log (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                order_id TEXT,
                courier_id TEXT,
                payload TEXT,
                created_at TEXT NOT NULL
            )"""
        )
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_type ON audit_log(event_type)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_created ON audit_log(created_at)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_audit_log_order ON audit_log(order_id)")
    _audit_log_initialized = True


def _ensure_audit_log_initialized() -> None:
    """Lazy init guard. Wywoływany przed każdą operacją na audit_log."""
    if not _audit_log_initialized:
        _init_audit_log_table()


def emit_audit(
    event_type: str,
    order_id: Optional[str] = None,
    courier_id: Optional[str] = None,
    payload: Optional[dict] = None,
    event_id: Optional[str] = None,
) -> Optional[str]:
    """Zapisuje event audit-only do tabeli audit_log (append-only).

    Idempotent przez INSERT OR IGNORE na PRIMARY KEY event_id.
    Dla typów z AUDIT_EVENT_TYPES (COURIER_ASSIGNED, CZAS_KURIERA_UPDATED,
    PANEL_UNREACHABLE, ORDER_RETURNED_TO_POOL).

    Stan persistowany przez state_machine.update_from_event wywoływany inline
    z call site — ta funkcja jest TYLKO audit history. Brak status/processed_at.

    Zwraca event_id przy zapisie, None przy idempotent skip (duplikat).
    """
    if event_type not in AUDIT_EVENT_TYPES:
        raise ValueError(
            f"emit_audit: event_type '{event_type}' nie jest audit type. "
            f"Dozwolone: {AUDIT_EVENT_TYPES}. Użyj emit() dla queue typów."
        )

    if event_id is None:
        event_id = make_event_id(event_type, order_id)

    _ensure_audit_log_initialized()
    payload_json = json.dumps(payload or {}, ensure_ascii=False)
    created_at = now_iso()

    with _conn() as conn:
        try:
            cur = conn.execute(
                """INSERT OR IGNORE INTO audit_log
                   (event_id, event_type, order_id, courier_id, payload, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (event_id, event_type, order_id, courier_id, payload_json, created_at),
            )
            if cur.rowcount == 0:
                _log.debug(f"AUDIT DUP: {event_id}")
                return None
            _log.info(f"AUDIT {event_type} order={order_id} courier={courier_id} id={event_id}")
            return event_id
        except Exception as e:
            _log.error(f"emit_audit() error: {e}")
            raise


def cleanup_audit_log(retention_days: int = 90) -> int:
    """Czysci audit_log starsze niz retention_days. Zwraca liczbe usunietych."""
    _ensure_audit_log_initialized()
    with _conn() as conn:
        cur = conn.execute(
            """DELETE FROM audit_log WHERE created_at < datetime('now', ?)""",
            (f"-{retention_days} days",),
        )
        deleted = cur.rowcount
        _log.info(f"cleanup_audit_log: usunieto {deleted} audit_log entries (retention={retention_days}d)")
        return deleted


def get_pending_count(event_types: Optional[list] = None) -> int:
    """Zwraca liczbę pending events w tabeli events.
    Opcjonalnie filtruje po event_types (np. tylko queue typy dla WORKER_STUCK alert)."""
    with _conn() as conn:
        if event_types:
            placeholders = ",".join("?" * len(event_types))
            cur = conn.execute(
                f"""SELECT COUNT(*) as cnt FROM events
                    WHERE status = 'pending' AND event_type IN ({placeholders})""",
                tuple(event_types),
            )
        else:
            cur = conn.execute(
                "SELECT COUNT(*) as cnt FROM events WHERE status = 'pending'"
            )
        return cur.fetchone()["cnt"]


# Lazy init: _init_audit_log_table() wywoływane przy pierwszym emit_audit /
# cleanup_audit_log via _ensure_audit_log_initialized(). NIE robimy module-load
# init bo test env może nie mieć dostępu do _db_path() / load_config()['paths'].
