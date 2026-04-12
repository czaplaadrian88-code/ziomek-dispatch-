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
}

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
