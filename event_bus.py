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
import logging
import os
import sqlite3
import threading
import time
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional
from zoneinfo import ZoneInfo

from dispatch_v2.common import load_config, now_iso, setup_logger

# MP-#5 (2026-05-08): retry transient SQLite locks + peak-aware cleanup.
# Background: emit() w hot path (panel_watcher, state_machine, telegram_approver
# via dispatch_pipeline) — pojedynczy SQLite lock burst (concurrent writer)
# drop'ował event. Retry 3x z exp backoff = transient resilience. Cleanup w peak
# (lunch 11-14 / dinner 17-20 Warsaw) skip — DELETE blocks readers shadow_dispatcher.
_RETRY_BACKOFF_MS = (100, 500, 2000)  # 3 attempts after first try
_PEAK_WINDOWS_WARSAW = (
    (11, 14),  # lunch peak
    (17, 20),  # dinner peak
)
_WARSAW_TZ = ZoneInfo("Europe/Warsaw")


# Zamkniety katalog typow eventow
EVENT_TYPES = {
    "NEW_ORDER",
    "ORDER_READY",
    "COURIER_PICKED_UP",
    "COURIER_DELIVERED",
    "COURIER_ASSIGNED",
    "COURIER_REJECTED_PROPOSAL",
    "ORDER_RETURNED_TO_POOL",
    "ORDER_RESURRECTED",
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
    # PICKUP_TIME_UPDATED (oid 474577 root cause, 2026-05-19): panel_watcher
    # wykrył zmianę pickup_at_warsaw (czas odbioru z restauracji) dla czasówki
    # planned / assigned / picked_up. state_machine.py ma handler — odświeża
    # pickup_at_warsaw + prep_minutes + decision_deadline. Audit type (niżej).
    "PICKUP_TIME_UPDATED",
    # A4 (audit META RC2 2026-05-07): CONFIG_RELOAD broadcast event dla
    # cache invalidation cross-process. Każdy long-running service polling'uje
    # via poll_broadcast() + invalidate per-process caches gdy scope match.
    # Zamyka Decyzja #4 Redis "post-Postgres lub never" (events.db wystarczy).
    "CONFIG_RELOAD",
}

# A4: broadcast events nie są queue (no consumer mark_processed) ani audit
# (nie historical only). Status='broadcast' w events table → invisible dla
# get_pending(NEW_ORDER) etc. Subscribery używają poll_broadcast() z cursor.
BROADCAST_EVENT_TYPES = {
    "CONFIG_RELOAD",
}

# Opcja C (2026-05-07): rozdzielenie ról events.db (queue vs audit log).
# AUDIT_EVENT_TYPES — typy zapisywane do osobnej tabeli audit_log (append-only,
# retention 90d). Stan persistowany przez state_machine.update_from_event
# wywoływany inline z call site (dual-write pattern). NIKT nie konsumuje queue-style.
# Czytelnicy: learning_analyzer, parser_health_endpoint, r04_evaluator
# (queries WHERE event_type=...).
AUDIT_EVENT_TYPES = {
    "COURIER_ASSIGNED",
    "CZAS_KURIERA_UPDATED",
    "PICKUP_TIME_UPDATED",
    "PANEL_UNREACHABLE",
    "ORDER_RETURNED_TO_POOL",
    "ORDER_RESURRECTED",
}

# Tech debt #39 (2026-05-13): queue types that are ALSO mirrored to audit_log
# for 90‑day analytics retention.  The primary source for R‑04 evaluator is
# audit_log (guaranteed 90d).  Events table still holds the queue lifecycle
# (pending → processed) but is purged after 48h.
AUDIT_MIRRORED_QUEUE_TYPES = frozenset({"COURIER_PICKED_UP", "COURIER_DELIVERED"})

# QUEUE_EVENT_TYPES — typy z lifecycle pending → processed w tabeli events.
# Konsumenci: shadow_dispatcher (NEW_ORDER) + sla_tracker (PICKED_UP, DELIVERED).
QUEUE_EVENT_TYPES = EVENT_TYPES - AUDIT_EVENT_TYPES - BROADCAST_EVENT_TYPES

if os.environ.get("DISPATCH_UNDER_PYTEST"):
    # Hermetyczne testy nie powinny nawet stat/mkdir produkcyjnej sciezki logow.
    _log = logging.getLogger("event_bus")
else:
    _log = setup_logger("event_bus", "/root/.openclaw/workspace/scripts/logs/events.log")


def _is_peak_window(now: Optional[datetime] = None) -> bool:
    """True if current Warsaw time within peak windows (lunch 11-14, dinner 17-20)."""
    if now is None:
        now = datetime.now(_WARSAW_TZ)
    elif now.tzinfo is None:
        now = now.replace(tzinfo=_WARSAW_TZ)
    h = now.hour
    return any(start <= h < end for start, end in _PEAK_WINDOWS_WARSAW)


def _retry_on_locked(fn: Callable, *args, **kwargs):
    """Wywołuje fn z retry na sqlite3.OperationalError 'database is locked'.

    Inne wyjątki propagują natychmiast. Backoff [100, 500, 2000]ms (3 dodatkowe
    próby po pierwszej). Cel: transient WAL contention przy concurrent writer.
    """
    last_exc: Optional[Exception] = None
    for attempt in range(len(_RETRY_BACKOFF_MS) + 1):
        try:
            return fn(*args, **kwargs)
        except sqlite3.OperationalError as e:
            msg = str(e).lower()
            if "locked" not in msg and "busy" not in msg:
                raise
            last_exc = e
            if attempt < len(_RETRY_BACKOFF_MS):
                delay_ms = _RETRY_BACKOFF_MS[attempt]
                _log.warning(
                    f"event_bus: SQLite locked, retry {attempt+1}/{len(_RETRY_BACKOFF_MS)} "
                    f"after {delay_ms}ms ({e})"
                )
                time.sleep(delay_ms / 1000.0)
            else:
                _log.error(f"event_bus: SQLite locked, retry exhausted: {e}")
    raise last_exc  # type: ignore[misc]


def _emit_audit_mirror(
    event_id: str,
    event_type: str,
    order_id: Optional[str],
    courier_id: Optional[str],
    payload_json: str,
    created_at: str,
) -> None:
    """Best‑effort mirror of a queue event into audit_log.

    Must NEVER raise – queue write integrity > audit completeness.
    """
    try:
        _ensure_audit_log_initialized()
        with _conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO audit_log
                   (event_id, event_type, order_id, courier_id, payload, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (event_id, event_type, order_id, courier_id, payload_json, created_at),
            )
    except Exception as exc:
        _log.warning(
            f"audit_mirror FAIL event_id={event_id} type={event_type}: "
            f"{type(exc).__name__}: {exc}"
        )


def _insert_audit_mirror_tx(
    conn: sqlite3.Connection,
    event_id: str,
    event_type: str,
    order_id: Optional[str],
    courier_id: Optional[str],
    payload_json: str,
    created_at: str,
) -> None:
    """Mirror w transakcji callera dla durable lifecycle eventow."""
    conn.execute(
        """INSERT OR IGNORE INTO audit_log
           (event_id, event_type, order_id, courier_id, payload, created_at)
           VALUES (?, ?, ?, ?, ?, ?)""",
        (event_id, event_type, order_id, courier_id, payload_json, created_at),
    )


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


# C3-01 (audit 2026-07-19): event i jego zlecenie aplikacji do orders_state
# musza powstac w TEJ SAMEJ transakcji SQLite. Sam deterministyczny event_id
# chroni tylko event log; bez trwalego receipt nie da sie odroznic lost-apply od
# starego duplikatu. Outbox jest addytywny i nie zmienia kontraktu istniejacych
# emit()/emit_audit() call-site'ow, dopoki nie podadza state_event.
_state_apply_outbox_initialized = False
_state_apply_outbox_db_path: Optional[str] = None
_state_apply_outbox_init_lock = threading.Lock()


def _init_state_apply_outbox_table() -> None:
    """Utworz addytywny outbox faz state/downstream (idempotentnie)."""
    global _state_apply_outbox_initialized, _state_apply_outbox_db_path
    target_db_path = _db_path()
    with _state_apply_outbox_init_lock:
        if (
            _state_apply_outbox_initialized
            and _state_apply_outbox_db_path == target_db_path
        ):
            return
        with _conn() as conn:
            conn.execute("BEGIN IMMEDIATE;")
            try:
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS state_apply_outbox (
                        event_id TEXT PRIMARY KEY,
                        event_key TEXT NOT NULL,
                        order_id TEXT NOT NULL,
                        expected_state_version TEXT,
                        expected_state_marker TEXT,
                        expected_state_token TEXT,
                        predecessor_event_id TEXT,
                        state_event TEXT NOT NULL,
                        state_status TEXT NOT NULL DEFAULT 'pending',
                        state_applied_at TEXT,
                        downstream_status TEXT NOT NULL DEFAULT 'pending',
                        downstream_applied_at TEXT,
                        downstream_attempts INTEGER NOT NULL DEFAULT 0,
                        state_attempts INTEGER NOT NULL DEFAULT 0,
                        state_retry_seq INTEGER NOT NULL DEFAULT 0,
                        downstream_retry_seq INTEGER NOT NULL DEFAULT 0,
                        last_error TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )"""
                )
                columns = {
                    str(row[1])
                    for row in conn.execute("PRAGMA table_info(state_apply_outbox)")
                }
                if "expected_state_marker" not in columns:
                    conn.execute(
                        "ALTER TABLE state_apply_outbox "
                        "ADD COLUMN expected_state_marker TEXT"
                    )
                if "expected_state_token" not in columns:
                    conn.execute(
                        "ALTER TABLE state_apply_outbox "
                        "ADD COLUMN expected_state_token TEXT"
                    )
                if "predecessor_event_id" not in columns:
                    conn.execute(
                        "ALTER TABLE state_apply_outbox "
                        "ADD COLUMN predecessor_event_id TEXT"
                    )
                if "downstream_attempts" not in columns:
                    # Existing rows come from a version that could execute the
                    # callback but could not persist an attempt counter. Treat
                    # them conservatively as indeterminate retries; new rows
                    # explicitly insert zero below.
                    conn.execute(
                        "ALTER TABLE state_apply_outbox "
                        "ADD COLUMN downstream_attempts INTEGER NOT NULL DEFAULT 1"
                    )
                if "state_retry_seq" not in columns:
                    conn.execute(
                        "ALTER TABLE state_apply_outbox "
                        "ADD COLUMN state_retry_seq INTEGER NOT NULL DEFAULT 0"
                    )
                if "downstream_retry_seq" not in columns:
                    conn.execute(
                        "ALTER TABLE state_apply_outbox "
                        "ADD COLUMN downstream_retry_seq INTEGER NOT NULL DEFAULT 0"
                    )
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS state_apply_retry_clock (
                        singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
                        value INTEGER NOT NULL
                    )"""
                )
                conn.execute(
                    "INSERT OR IGNORE INTO state_apply_retry_clock "
                    "(singleton, value) VALUES (1, 0)"
                )
                # Migracja zachowuje stabilne FIFO bez zaufania do zegara.
                # Surowe legacy inserty z default=0 trafiaja na poczatek kolejki.
                conn.execute(
                    "UPDATE state_apply_outbox SET state_retry_seq=rowid "
                    "WHERE state_retry_seq=0"
                )
                conn.execute(
                    "UPDATE state_apply_outbox SET downstream_retry_seq=rowid "
                    "WHERE downstream_retry_seq=0"
                )
                conn.execute(
                    """UPDATE state_apply_retry_clock
                       SET value = MAX(
                           value,
                           COALESCE((SELECT MAX(rowid) FROM state_apply_outbox), 0),
                           COALESCE((SELECT MAX(state_retry_seq)
                                     FROM state_apply_outbox), 0),
                           COALESCE((SELECT MAX(downstream_retry_seq)
                                     FROM state_apply_outbox), 0)
                       )
                       WHERE singleton=1"""
                )
                conn.execute(
                    """CREATE TABLE IF NOT EXISTS durable_learning_projection (
                        effect_id TEXT PRIMARY KEY,
                        lifecycle_event_id TEXT NOT NULL,
                        effect_name TEXT NOT NULL,
                        record_json TEXT NOT NULL,
                        projected_at TEXT,
                        created_at TEXT NOT NULL,
                        updated_at TEXT NOT NULL
                    )"""
                )
                conn.execute(
                    """CREATE INDEX IF NOT EXISTS idx_durable_learning_event
                       ON durable_learning_projection(lifecycle_event_id)"""
                )
                conn.execute(
                    """CREATE INDEX IF NOT EXISTS idx_state_apply_outbox_key
                       ON state_apply_outbox(order_id, event_key, created_at)"""
                )
                conn.execute(
                    """CREATE INDEX IF NOT EXISTS idx_state_apply_outbox_pending
                       ON state_apply_outbox(state_status, downstream_status, created_at)"""
                )
                conn.execute(
                    """CREATE INDEX IF NOT EXISTS idx_state_apply_outbox_retry
                       ON state_apply_outbox(state_status, downstream_status, updated_at)"""
                )
                conn.execute(
                    """CREATE INDEX IF NOT EXISTS idx_state_apply_outbox_state_retry
                       ON state_apply_outbox(state_status, state_retry_seq)"""
                )
                conn.execute(
                    """CREATE INDEX IF NOT EXISTS idx_state_apply_outbox_downstream_retry
                       ON state_apply_outbox(
                           state_status, downstream_status, downstream_retry_seq
                       )"""
                )
                conn.execute("COMMIT;")
            except Exception:
                if conn.in_transaction:
                    conn.execute("ROLLBACK;")
                raise
        _state_apply_outbox_initialized = True
        _state_apply_outbox_db_path = target_db_path


def _ensure_state_apply_outbox_initialized() -> None:
    if (
        not _state_apply_outbox_initialized
        or _state_apply_outbox_db_path != _db_path()
    ):
        _init_state_apply_outbox_table()


def _next_state_apply_retry_seq(conn: sqlite3.Connection) -> int:
    """Monotoniczny bilet round-robin; caller trzyma transakcje write."""
    conn.execute(
        "UPDATE state_apply_retry_clock SET value=value+1 WHERE singleton=1"
    )
    row = conn.execute(
        "SELECT value FROM state_apply_retry_clock WHERE singleton=1"
    ).fetchone()
    if row is None:
        raise RuntimeError("state_apply_retry_clock is not initialized")
    return int(row[0])


def _insert_state_apply_outbox(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    event_key: str,
    order_id: str,
    expected_state_version: Optional[str],
    expected_state_marker: Optional[str],
    expected_state_token: Optional[str],
    predecessor_event_id: Optional[str],
    state_event_json: str,
    created_at: str,
) -> None:
    """Wstaw pending receipt. Caller trzyma transakcje eventu."""
    retry_seq = _next_state_apply_retry_seq(conn)
    conn.execute(
        """INSERT INTO state_apply_outbox
           (event_id, event_key, order_id, expected_state_version,
            expected_state_marker, expected_state_token, predecessor_event_id,
            state_event,
            state_status, downstream_status, downstream_attempts, state_attempts,
            state_retry_seq, downstream_retry_seq,
            created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'pending', 0, 0,
                   ?, ?, ?, ?)""",
        (
            event_id,
            event_key,
            order_id,
            expected_state_version,
            expected_state_marker,
            expected_state_token,
            predecessor_event_id,
            state_event_json,
            retry_seq,
            retry_seq,
            created_at,
            created_at,
        ),
    )


def _upgrade_existing_source_with_outbox_tx(
    conn: sqlite3.Connection,
    *,
    source_table: str,
    event_id: str,
    event_type: str,
    order_id: Optional[str],
    courier_id: Optional[str],
    payload_json: str,
    event_key: str,
    expected_state_version: Optional[str],
    expected_state_marker: Optional[str],
    expected_state_token: Optional[str],
    predecessor_event_id: Optional[str],
    state_event_json: str,
    created_at: str,
) -> bool:
    """Atomically add the receipt omitted by an older/legacy writer.

    A deterministic ID may already name a source row written before the
    durable bridge existed.  Treating that row as a plain duplicate would
    make ``outbox_missing`` permanent.  Upgrade only an exactly matching
    source row; an ID collision with different source semantics fails loudly.
    """
    if source_table not in {"events", "audit_log"}:
        raise ValueError(f"unsupported durable source table: {source_table}")
    source = conn.execute(
        f"""SELECT event_type, order_id, courier_id, payload
            FROM {source_table} WHERE event_id = ?""",
        (event_id,),
    ).fetchone()
    if source is None:
        raise ValueError(
            f"event_id collision: durable source row missing for {event_id}"
        )
    try:
        existing_payload = json.loads(source["payload"] or "{}")
        requested_payload = json.loads(payload_json or "{}")
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise ValueError(
            f"event_id collision: invalid source payload for {event_id}"
        ) from exc
    source_matches = (
        str(source["event_type"] or "") == str(event_type or "")
        and str(source["order_id"] or "") == str(order_id or "")
        and str(source["courier_id"] or "") == str(courier_id or "")
        and existing_payload == requested_payload
    )
    if not source_matches:
        raise ValueError(
            f"event_id collision: existing {source_table} row differs for {event_id}"
        )
    existing_receipt = conn.execute(
        "SELECT 1 FROM state_apply_outbox WHERE event_id = ?",
        (event_id,),
    ).fetchone()
    if existing_receipt is not None:
        return False
    _insert_state_apply_outbox(
        conn,
        event_id=event_id,
        event_key=event_key,
        order_id=str(order_id),
        expected_state_version=expected_state_version,
        expected_state_marker=expected_state_marker,
        expected_state_token=expected_state_token,
        predecessor_event_id=predecessor_event_id,
        state_event_json=state_event_json,
        created_at=created_at,
    )
    return True


def has_matching_legacy_source_without_outbox(
    event_id: str,
    event_type: str,
    order_id: Optional[str],
    courier_id: Optional[str],
    payload: Optional[dict],
) -> bool:
    """Return whether an exact pre-C3 source row should be upgraded in place.

    Old lifecycle call-sites used their semantic key directly as ``event_id``.
    The versioned bridge must discover that exact row before deriving ``K_v…``;
    otherwise it forks a second queue/audit source instead of attaching the
    missing receipt. Rows with different source semantics are not collapsed.
    """
    _ensure_state_apply_outbox_initialized()
    requested_payload = payload or {}
    with _conn() as conn:
        if conn.execute(
            "SELECT 1 FROM state_apply_outbox WHERE event_id = ?",
            (str(event_id),),
        ).fetchone() is not None:
            return False
        for table in ("events", "audit_log"):
            exists = conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (table,),
            ).fetchone()
            if exists is None:
                continue
            row = conn.execute(
                f"""SELECT event_type, order_id, courier_id, payload
                    FROM {table} WHERE event_id = ?""",
                (str(event_id),),
            ).fetchone()
            if row is None:
                continue
            try:
                existing_payload = json.loads(row["payload"] or "{}")
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if (
                str(row["event_type"] or "") == str(event_type or "")
                and str(row["order_id"] or "") == str(order_id or "")
                and str(row["courier_id"] or "") == str(courier_id or "")
                and existing_payload == requested_payload
            ):
                return True
    return False


def _decode_outbox_row(row: Optional[sqlite3.Row]) -> Optional[Dict[str, Any]]:
    if row is None:
        return None
    record: Dict[str, Any] = dict(row)
    try:
        record["state_event"] = json.loads(record["state_event"])
    except (TypeError, ValueError, json.JSONDecodeError):
        record["state_event"] = None
    return record


def prepare_durable_learning_projection(
    lifecycle_event_id: str,
    effect_name: str,
    record: dict,
) -> tuple[Dict[str, Any], bool]:
    """Create one canonical learning record; first committed payload wins.

    The SQLite row is the durable per-effect receipt. JSONL is only its
    materialized projection, so a later failure in the composite lifecycle
    callback cannot make the same learning record run again after rotations.
    """
    event_id = str(lifecycle_event_id or "")
    name = str(effect_name or "")
    if not event_id or not name or not isinstance(record, dict):
        raise ValueError("durable learning projection requires event, effect and dict")
    _ensure_state_apply_outbox_initialized()
    effect_id = f"{event_id}:{name}"
    record_json = json.dumps(
        record,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    ts = now_iso()
    with _conn() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE;")
            cur = conn.execute(
                """INSERT OR IGNORE INTO durable_learning_projection
                   (effect_id, lifecycle_event_id, effect_name, record_json,
                    projected_at, created_at, updated_at)
                   VALUES (?, ?, ?, ?, NULL, ?, ?)""",
                (effect_id, event_id, name, record_json, ts, ts),
            )
            row = conn.execute(
                "SELECT * FROM durable_learning_projection WHERE effect_id = ?",
                (effect_id,),
            ).fetchone()
            conn.execute("COMMIT;")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK;")
            raise
    if row is None:
        raise RuntimeError(f"durable learning projection disappeared: {effect_id}")
    result: Dict[str, Any] = dict(row)
    try:
        result["record"] = json.loads(result["record_json"])
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"invalid durable learning record: {effect_id}") from exc
    return result, cur.rowcount == 1


def mark_durable_learning_projected(effect_id: str) -> bool:
    """Persist completion of the JSONL projection after its file+dir fsync."""
    _ensure_state_apply_outbox_initialized()
    ts = now_iso()
    with _conn() as conn:
        cur = conn.execute(
            """UPDATE durable_learning_projection
               SET projected_at = COALESCE(projected_at, ?), updated_at = ?
               WHERE effect_id = ?""",
            (ts, ts, str(effect_id)),
        )
    return cur.rowcount == 1


def get_durable_learning_projection(effect_id: str) -> Optional[Dict[str, Any]]:
    _ensure_state_apply_outbox_initialized()
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM durable_learning_projection WHERE effect_id = ?",
            (str(effect_id),),
        ).fetchone()
    if row is None:
        return None
    result: Dict[str, Any] = dict(row)
    result["record"] = json.loads(result["record_json"])
    return result


def _validate_durable_state_event(
    event_type: str,
    order_id: Optional[str],
    courier_id: Optional[str],
    event_id: str,
    state_event: dict,
    event_key: Optional[str],
) -> None:
    """Reject poison outbox rows before the source event can commit."""
    if not isinstance(state_event, dict):
        raise ValueError("state_event must be a dict")
    if not event_key or not order_id:
        raise ValueError("state_event requires non-empty order_id and event_key")
    if state_event.get("event_type") != event_type:
        raise ValueError("state_event.event_type does not match source event")
    if str(state_event.get("order_id") or "") != str(order_id):
        raise ValueError("state_event.order_id does not match source event")
    if str(state_event.get("event_id") or "") != str(event_id):
        raise ValueError("state_event.event_id does not match source event")
    if not isinstance(state_event.get("payload"), dict):
        raise ValueError("state_event.payload must be a dict")
    if str(state_event.get("courier_id") or "") != str(courier_id or ""):
        raise ValueError("state_event.courier_id does not match source event")


def get_state_apply_outbox(event_id: str) -> Optional[Dict[str, Any]]:
    _ensure_state_apply_outbox_initialized()
    with _conn() as conn:
        row = conn.execute(
            "SELECT * FROM state_apply_outbox WHERE event_id = ?",
            (event_id,),
        ).fetchone()
    return _decode_outbox_row(row)


def bind_state_apply_previous_courier(
    event_id: str, previous_courier_id: str
) -> Optional[Dict[str, Any]]:
    """Persist event-local reassignment/return provenance before state apply.

    Downstream may run much later than the state transition.  The mutable
    current order therefore cannot be the authority for which old courier's
    plan must be pruned.  First committed provenance wins and is written while
    the receipt is still pending, before the JSON state mutation.
    """
    _ensure_state_apply_outbox_initialized()
    previous = str(previous_courier_id or "")
    if not previous:
        return get_state_apply_outbox(event_id)
    ts = now_iso()
    with _conn() as conn:
        conn.execute("BEGIN IMMEDIATE;")
        try:
            row = conn.execute(
                "SELECT state_event, state_status FROM state_apply_outbox "
                "WHERE event_id = ?",
                (str(event_id),),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT;")
                return None
            if str(row["state_status"] or "") == "pending":
                try:
                    state_event = json.loads(row["state_event"])
                except (TypeError, ValueError, json.JSONDecodeError):
                    state_event = None
                if isinstance(state_event, dict):
                    bound = str(state_event.get("previous_courier_id") or "")
                    if not bound:
                        state_event["previous_courier_id"] = previous
                        conn.execute(
                            """UPDATE state_apply_outbox
                               SET state_event = ?, updated_at = ?
                               WHERE event_id = ? AND state_status = 'pending'""",
                            (
                                json.dumps(
                                    state_event,
                                    ensure_ascii=False,
                                    separators=(",", ":"),
                                ),
                                ts,
                                str(event_id),
                            ),
                        )
            conn.execute("COMMIT;")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK;")
            raise
    return get_state_apply_outbox(event_id)


def get_pending_state_apply_for_order(
    order_id: str,
) -> Optional[Dict[str, Any]]:
    """Najstarsza niedomknieta faza state dla zlecenia.

    Serializacja nie moze byc tylko per ``event_key``. Pozniejszy event innego
    typu moglby wtedy zmienic globalny lifecycle marker i wyprzec starsza,
    trwale zapisana intencje, ktorej state apply jeszcze oczekuje na retry.
    Applied/downstream-pending nie blokuje kolejnego state: globalny downstream
    FIFO zachowuje osobno kolejnosc callbackow.
    """
    _ensure_state_apply_outbox_initialized()
    with _conn() as conn:
        row = conn.execute(
            """SELECT * FROM state_apply_outbox
               WHERE order_id = ?
                 AND state_status = 'pending'
               ORDER BY rowid ASC
               LIMIT 1""",
            (str(order_id),),
        ).fetchone()
    return _decode_outbox_row(row)


def get_latest_pending_state_apply_for_order(
    order_id: str,
) -> Optional[Dict[str, Any]]:
    """Ostatnia trwale zapisana intencja state dla causal dependency chain."""
    _ensure_state_apply_outbox_initialized()
    with _conn() as conn:
        row = conn.execute(
            """SELECT * FROM state_apply_outbox
               WHERE order_id = ?
                 AND state_status = 'pending'
               ORDER BY rowid DESC
               LIMIT 1""",
            (str(order_id),),
        ).fetchone()
    return _decode_outbox_row(row)


def get_unresolved_state_apply(
    event_key: str,
    order_id: str,
) -> Optional[Dict[str, Any]]:
    """Niedomknieta generacja dokladnie tego samego requestu.

    Istniejacy ``state=pending`` ma pierwszenstwo przed starszym
    ``applied/downstream=pending`` tego samego klucza. Ten drugi moze byc
    poprzednia generacja po cyklu A->B->A; gdy successor A juz istnieje, retry
    musi wznowic jego exact payload zamiast utworzyc trzecia generacje.
    """
    _ensure_state_apply_outbox_initialized()
    with _conn() as conn:
        row = conn.execute(
            """SELECT * FROM state_apply_outbox
               WHERE event_key = ? AND order_id = ?
                 AND (state_status = 'pending'
                      OR (state_status = 'applied' AND downstream_status = 'pending'))
               ORDER BY CASE WHEN state_status = 'pending' THEN 0 ELSE 1 END,
                        rowid ASC
               LIMIT 1""",
            (event_key, str(order_id)),
        ).fetchone()
    return _decode_outbox_row(row)


def list_pending_state_applies_for_key(
    event_key: str,
    order_id: str,
) -> List[Dict[str, Any]]:
    """All pending generations for exact-intent selection by the bridge."""
    _ensure_state_apply_outbox_initialized()
    with _conn() as conn:
        rows = conn.execute(
            """SELECT * FROM state_apply_outbox
               WHERE event_key = ? AND order_id = ?
                 AND state_status = 'pending'
               ORDER BY rowid ASC""",
            (str(event_key), str(order_id)),
        ).fetchall()
    return [
        decoded
        for row in rows
        if (decoded := _decode_outbox_row(row)) is not None
    ]


def get_latest_state_apply(
    event_key: str,
    order_id: str,
) -> Optional[Dict[str, Any]]:
    _ensure_state_apply_outbox_initialized()
    with _conn() as conn:
        row = conn.execute(
            """SELECT * FROM state_apply_outbox
               WHERE event_key = ? AND order_id = ?
               ORDER BY rowid DESC
               LIMIT 1""",
            (event_key, str(order_id)),
        ).fetchone()
    return _decode_outbox_row(row)


def list_unresolved_state_applies(
    limit: int = 100,
    *,
    updated_before: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """Pending fazy state, fair po trwałym bilecie round-robin.

    Rows z juz zastosowanym state i oczekujacym downstream maja osobny lane.
    Mieszanie obu klas pod jednym LIMIT pozwalalo duzemu backlogowi downstream
    trwale zaglodzic nowszy zapis orders_state. ``updated_before`` jest brama
    wieku dla lekkiego sweepa; None zachowuje natychmiastowy drain call-site'u.
    """
    _ensure_state_apply_outbox_initialized()
    age_clause = " AND updated_at <= ?" if updated_before is not None else ""
    params: tuple[Any, ...] = (
        (str(updated_before), max(1, int(limit)))
        if updated_before is not None
        else (max(1, int(limit)),)
    )
    with _conn() as conn:
        rows = conn.execute(
            f"""SELECT * FROM state_apply_outbox
               WHERE state_status = 'pending'{age_clause}
               ORDER BY state_retry_seq ASC, rowid ASC
               LIMIT ?""",
            params,
        ).fetchall()
    return [_decode_outbox_row(row) for row in rows if row is not None]  # type: ignore[misc]


def get_oldest_pending_downstream(
    *,
    updated_before: Optional[str] = None,
    include_event_versions: Optional[Dict[str, tuple[str, int]]] = None,
    exclude_event_ids: Optional[set[str]] = None,
) -> Optional[Dict[str, Any]]:
    """Następny gotowy callback, fair po trwałym bilecie round-robin.

    ``state_status=pending`` należy do osobnego lane i nie może blokować
    niezależnych, już zastosowanych eventów. Trwały bilet jest przesuwany przed
    próbą callbacku, więc awaria schodzi za starsze wpisy, ale nie może być
    głodzona stałym napływem nowszych. ``updated_before`` ogranicza sweep do
    receiptow bez postepu przez skonfigurowany grace period.
    """
    _ensure_state_apply_outbox_initialized()
    included = tuple(
        sorted(
            (str(event_id), str(version[0]), int(version[1]))
            for event_id, version in (include_event_versions or {}).items()
            if event_id and version and version[0]
        )
    )
    excluded = tuple(
        sorted(str(event_id) for event_id in (exclude_event_ids or set()) if event_id)
    )
    if updated_before is not None and included:
        exact_clauses = " OR ".join(
            "(candidate.event_id = ? AND candidate.updated_at = ? "
            "AND candidate.downstream_attempts = ?)"
            for _ in included
        )
        age_clause = (
            " AND (candidate.updated_at <= ? "
            f"OR ({exact_clauses}))"
        )
        exact_params = tuple(value for pair in included for value in pair)
        age_params: tuple[Any, ...] = (str(updated_before), *exact_params)
    elif updated_before is not None:
        age_clause = " AND candidate.updated_at <= ?"
        age_params = (str(updated_before),)
    else:
        age_clause = ""
        age_params = ()
    if excluded:
        exclude_clause = (
            " AND candidate.event_id NOT IN ("
            + ",".join("?" for _ in excluded)
            + ")"
        )
    else:
        exclude_clause = ""
    params: tuple[Any, ...] = (
        *age_params,
        *excluded,
    )
    with _conn() as conn:
        row = conn.execute(
            f"""SELECT candidate.* FROM state_apply_outbox AS candidate
               WHERE candidate.state_status = 'applied'
                 AND candidate.downstream_status = 'pending'
                 {age_clause}
                 {exclude_clause}
                 AND NOT EXISTS (
                     SELECT 1 FROM state_apply_outbox AS older
                     WHERE older.order_id = candidate.order_id
                       AND older.rowid < candidate.rowid
                       AND older.state_status = 'applied'
                       AND older.downstream_status = 'pending'
               )
               ORDER BY candidate.downstream_retry_seq ASC,
                        candidate.rowid ASC
               LIMIT 1""",
            params,
        ).fetchone()
    return _decode_outbox_row(row)


def get_oldest_unfinished_apply() -> Optional[Dict[str, Any]]:
    """Globalnie najstarszy receipt bez obu faz zakonczonych.

    Downstream nie moze przeskoczyc starszego ``state_status=pending``: jego
    JSON commit mogl juz nastapic, a proces mogl zginac przed zapisem receiptu.
    SQLite ``rowid`` odzwierciedla serializowaną kolejność insertów/commitów i
    nie zależy od zegara ani leksykografii event_id.
    """
    _ensure_state_apply_outbox_initialized()
    with _conn() as conn:
        row = conn.execute(
            """SELECT * FROM state_apply_outbox
               WHERE state_status = 'pending'
                  OR (state_status = 'applied' AND downstream_status = 'pending')
               ORDER BY rowid ASC
               LIMIT 1"""
        ).fetchone()
    return _decode_outbox_row(row)


def begin_state_apply_downstream(event_id: str) -> Optional[int]:
    """Trwale zaznacz probe callbacku i zwroc jej numer (1 = pierwszy raz)."""
    _ensure_state_apply_outbox_initialized()
    ts = now_iso()
    with _conn() as conn:
        conn.execute("BEGIN IMMEDIATE;")
        try:
            retry_seq = _next_state_apply_retry_seq(conn)
            cur = conn.execute(
                """UPDATE state_apply_outbox
                   SET downstream_attempts = downstream_attempts + 1,
                       downstream_retry_seq = ?, updated_at = ?
                   WHERE event_id = ? AND state_status = 'applied'
                     AND downstream_status = 'pending'""",
                (retry_seq, ts, event_id),
            )
            row = (
                conn.execute(
                    "SELECT downstream_attempts FROM state_apply_outbox "
                    "WHERE event_id = ?",
                    (event_id,),
                ).fetchone()
                if cur.rowcount == 1
                else None
            )
            conn.execute("COMMIT;")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK;")
            raise
    return int(row[0]) if row is not None else None


def mark_state_apply_applied(event_id: str) -> bool:
    _ensure_state_apply_outbox_initialized()
    ts = now_iso()
    with _conn() as conn:
        conn.execute("BEGIN IMMEDIATE;")
        try:
            retry_seq = _next_state_apply_retry_seq(conn)
            cur = conn.execute(
                """UPDATE state_apply_outbox
                   SET state_status = 'applied',
                       state_applied_at = COALESCE(state_applied_at, ?),
                       state_attempts = state_attempts + 1,
                       downstream_retry_seq = ?,
                       last_error = NULL, updated_at = ?
                   WHERE event_id = ? AND state_status = 'pending'""",
                (ts, retry_seq, ts, event_id),
            )
            conn.execute("COMMIT;")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK;")
            raise
    return cur.rowcount == 1


def mark_state_apply_superseded(event_id: str, reason: str) -> bool:
    _ensure_state_apply_outbox_initialized()
    ts = now_iso()
    with _conn() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE;")
            cur = conn.execute(
                """UPDATE state_apply_outbox
                   SET state_status = 'superseded', downstream_status = 'skipped',
                       state_attempts = state_attempts + 1, last_error = ?, updated_at = ?
                   WHERE event_id = ? AND state_status = 'pending'""",
                (reason[:1000], ts, event_id),
            )
            if cur.rowcount == 1:
                # Durable queue event wyparte przez nowszy stan nie moze juz
                # trafic do legacy consumera. Domknij jego queue lifecycle w
                # tej samej transakcji co supersede receiptu. Audit-only event
                # po prostu nie ma odpowiadajacego wiersza w ``events``.
                queue_cur = conn.execute(
                    """UPDATE events
                       SET status = 'processed', processed_at = ?
                       WHERE event_id = ? AND status = 'pending'""",
                    (ts, event_id),
                )
                if queue_cur.rowcount == 1:
                    conn.execute(
                        """INSERT OR IGNORE INTO processed_events
                           (event_id, processed_at) VALUES (?, ?)""",
                        (event_id, ts),
                    )
            conn.execute("COMMIT;")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK;")
            raise
    return cur.rowcount == 1


def mark_state_apply_invalid(event_id: str, reason: str) -> bool:
    """Terminalize a permanently malformed receipt so it cannot poison FIFO.

    Validation at emit time prevents new malformed rows.  This recovery path
    is still required for corruption or rows created by an older binary: a
    payload that cannot ever be applied must be retained with an explicit
    error, skipped, and removed from both state and downstream work queues.
    """
    _ensure_state_apply_outbox_initialized()
    ts = now_iso()
    error = f"invalid outbox state_event: {reason}"[:1000]
    with _conn() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE;")
            cur = conn.execute(
                """UPDATE state_apply_outbox
                   SET state_status = CASE
                           WHEN state_status = 'pending' THEN 'superseded'
                           ELSE state_status
                       END,
                       downstream_status = 'skipped',
                       state_attempts = state_attempts + CASE
                           WHEN state_status = 'pending' THEN 1 ELSE 0
                       END,
                       last_error = ?, updated_at = ?
                   WHERE event_id = ?
                     AND (state_status = 'pending'
                          OR (state_status = 'applied'
                              AND downstream_status = 'pending'))""",
                (error, ts, str(event_id)),
            )
            if cur.rowcount == 1:
                queue_cur = conn.execute(
                    """UPDATE events
                       SET status = 'processed', processed_at = ?
                       WHERE event_id = ? AND status = 'pending'""",
                    (ts, str(event_id)),
                )
                if queue_cur.rowcount == 1:
                    conn.execute(
                        """INSERT OR IGNORE INTO processed_events
                           (event_id, processed_at) VALUES (?, ?)""",
                        (str(event_id), ts),
                    )
            conn.execute("COMMIT;")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK;")
            raise
    return cur.rowcount == 1


def record_state_apply_error(event_id: str, error: str) -> bool:
    _ensure_state_apply_outbox_initialized()
    ts = now_iso()
    with _conn() as conn:
        conn.execute("BEGIN IMMEDIATE;")
        try:
            retry_seq = _next_state_apply_retry_seq(conn)
            cur = conn.execute(
                """UPDATE state_apply_outbox
                   SET state_attempts = state_attempts + 1,
                       state_retry_seq = CASE
                           WHEN state_status = 'pending' THEN ?
                           ELSE state_retry_seq
                       END,
                       last_error = ?, updated_at = ?
                   WHERE event_id = ?
                     AND (state_status = 'pending' OR downstream_status = 'pending')""",
                (retry_seq, error[:1000], ts, event_id),
            )
            conn.execute("COMMIT;")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK;")
            raise
    return cur.rowcount == 1


def record_state_apply_downstream_error(event_id: str, error: str) -> bool:
    """Zapisz błąd callbacku bez fałszywego zwiększania state_attempts.

    Numer próby downstream jest zwiększany atomowo przez
    ``begin_state_apply_downstream`` jeszcze przed callbackiem. Ten zapis
    jedynie utrwala błąd i odświeża cooldown retry.
    """
    _ensure_state_apply_outbox_initialized()
    ts = now_iso()
    with _conn() as conn:
        cur = conn.execute(
            """UPDATE state_apply_outbox
               SET last_error = ?, updated_at = ?
               WHERE event_id = ? AND state_status = 'applied'
                 AND downstream_status = 'pending'""",
            (error[:1000], ts, event_id),
        )
    return cur.rowcount == 1


def mark_state_apply_downstream(event_id: str) -> bool:
    _ensure_state_apply_outbox_initialized()
    ts = now_iso()
    with _conn() as conn:
        cur = conn.execute(
            """UPDATE state_apply_outbox
               SET downstream_status = 'applied', downstream_applied_at = ?,
                   last_error = NULL, updated_at = ?
               WHERE event_id = ? AND state_status = 'applied'
                 AND downstream_status = 'pending'""",
            (ts, ts, event_id),
        )
    return cur.rowcount == 1


def make_event_id(event_type: str, order_id: Optional[str], timestamp_ms: Optional[int] = None) -> str:
    """Deterministyczne event_id. Ten sam event z tego samego momentu = ten sam ID."""
    if timestamp_ms is None:
        timestamp_ms = int(time.time() * 1000)
    oid = order_id if order_id else "none"
    return f"{oid}_{event_type}_{timestamp_ms}"


def _emit_inner(
    event_type: str,
    order_id: Optional[str],
    courier_id: Optional[str],
    payload_json: str,
    created_at: str,
    event_id: str,
    state_event_json: Optional[str] = None,
    event_key: Optional[str] = None,
    expected_state_version: Optional[str] = None,
    expected_state_marker: Optional[str] = None,
    expected_state_token: Optional[str] = None,
    predecessor_event_id: Optional[str] = None,
) -> Optional[str]:
    """Inner emit z transaction body. Wrapped przez emit() w _retry_on_locked."""
    atomic_audit_mirror = (
        state_event_json is not None
        and event_type in AUDIT_MIRRORED_QUEUE_TYPES
    )
    with _conn() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE;")
            cur = conn.execute(
                "SELECT 1 FROM processed_events WHERE event_id = ?",
                (event_id,),
            )
            if cur.fetchone():
                if state_event_json is not None:
                    if not order_id or not event_key:
                        raise ValueError(
                            "state_event wymaga niepustych order_id i event_key"
                        )
                    _upgrade_existing_source_with_outbox_tx(
                        conn,
                        source_table="events",
                        event_id=event_id,
                        event_type=event_type,
                        order_id=order_id,
                        courier_id=courier_id,
                        payload_json=payload_json,
                        event_key=event_key,
                        expected_state_version=expected_state_version,
                        expected_state_marker=expected_state_marker,
                        expected_state_token=expected_state_token,
                        predecessor_event_id=predecessor_event_id,
                        state_event_json=state_event_json,
                        created_at=created_at,
                    )
                if atomic_audit_mirror:
                    _insert_audit_mirror_tx(
                        conn, event_id, event_type, order_id, courier_id,
                        payload_json, created_at,
                    )
                conn.execute("COMMIT;")
                _log.debug(f"DUP (processed): {event_id}")
                return None

            cur = conn.execute(
                """INSERT OR IGNORE INTO events
                   (event_id, event_type, order_id, courier_id, payload, created_at, status)
                   VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
                (event_id, event_type, order_id, courier_id, payload_json, created_at),
            )
            if cur.rowcount == 0:
                if state_event_json is not None:
                    if not order_id or not event_key:
                        raise ValueError(
                            "state_event wymaga niepustych order_id i event_key"
                        )
                    _upgrade_existing_source_with_outbox_tx(
                        conn,
                        source_table="events",
                        event_id=event_id,
                        event_type=event_type,
                        order_id=order_id,
                        courier_id=courier_id,
                        payload_json=payload_json,
                        event_key=event_key,
                        expected_state_version=expected_state_version,
                        expected_state_marker=expected_state_marker,
                        expected_state_token=expected_state_token,
                        predecessor_event_id=predecessor_event_id,
                        state_event_json=state_event_json,
                        created_at=created_at,
                    )
                if atomic_audit_mirror:
                    _insert_audit_mirror_tx(
                        conn, event_id, event_type, order_id, courier_id,
                        payload_json, created_at,
                    )
                conn.execute("COMMIT;")
                _log.debug(f"DUP (pending): {event_id}")
                return None

            if state_event_json is not None:
                if not order_id or not event_key:
                    raise ValueError(
                        "state_event wymaga niepustych order_id i event_key"
                    )
                _insert_state_apply_outbox(
                    conn,
                    event_id=event_id,
                    event_key=event_key,
                    order_id=str(order_id),
                    expected_state_version=expected_state_version,
                    expected_state_marker=expected_state_marker,
                    expected_state_token=expected_state_token,
                    predecessor_event_id=predecessor_event_id,
                    state_event_json=state_event_json,
                    created_at=created_at,
                )

            if atomic_audit_mirror:
                # Queue event, durable receipt i wymagany mirror stanowia jeden
                # commit. Legacy emit bez state_event zachowuje historyczny
                # best-effort kontrakt ponizej.
                _insert_audit_mirror_tx(
                    conn, event_id, event_type, order_id, courier_id,
                    payload_json, created_at,
                )

            conn.execute("COMMIT;")
            _log.info(f"EMIT {event_type} order={order_id} courier={courier_id} id={event_id}")
        except sqlite3.OperationalError:
            # A10-04: BEGIN IMMEDIATE moze polec zanim transakcja powstanie;
            # ROLLBACK bez guardu maskowal wtedy pierwotny 'database is locked'.
            if conn.in_transaction:
                conn.execute("ROLLBACK;")
            raise
        except Exception as e:
            if conn.in_transaction:
                conn.execute("ROLLBACK;")
            _log.error(f"emit() error: {e}")
            raise

    # Legacy call-site bez durable receipt zachowuje best-effort mirror. Dla
    # state_event mirror byl czescia tego samego commitu powyzej, wiec nie ma
    # crash-window event/outbox→audit ani potrzeby drugiego zapisu.
    if event_type in AUDIT_MIRRORED_QUEUE_TYPES and state_event_json is None:
        try:
            _emit_audit_mirror(
                event_id, event_type, order_id, courier_id,
                payload_json, created_at,
            )
        except Exception as e:
            _log.warning(f"audit_mirror failed for {event_id}: {e}")
    return event_id


def emit(
    event_type: str,
    order_id: Optional[str] = None,
    courier_id: Optional[str] = None,
    payload: Optional[dict] = None,
    event_id: Optional[str] = None,
    *,
    state_event: Optional[dict] = None,
    event_key: Optional[str] = None,
    expected_state_version: Optional[str] = None,
    expected_state_marker: Optional[str] = None,
    expected_state_token: Optional[str] = None,
    predecessor_event_id: Optional[str] = None,
) -> Optional[str]:
    """Emituje event. Zwraca event_id lub None jesli duplikat.

    Jesli event_id nie podany -> generowany deterministycznie.
    Jesli event_id juz istnieje w bazie -> ZWRACA None (idempotent skip).

    MP-#5: Transient SQLite lock errors retry'owane (3x exp backoff).

    Tech debt #39: COURIER_PICKED_UP and COURIER_DELIVERED are automatically
    mirrored to audit_log (90‑day retention) for analytics consumers such as
    the R‑04 evaluator. Legacy emit bez ``state_event`` pozostaje best-effort;
    durable lifecycle emit zapisuje queue+outbox+mirror w jednym commicie.
    """
    if event_type not in EVENT_TYPES:
        raise ValueError(f"Nieznany event_type: {event_type}. Dozwolone: {EVENT_TYPES}")

    if event_id is None:
        event_id = make_event_id(event_type, order_id)

    if state_event is not None:
        _validate_durable_state_event(
            event_type, order_id, courier_id, event_id, state_event, event_key
        )
        _ensure_state_apply_outbox_initialized()
        if event_type in AUDIT_MIRRORED_QUEUE_TYPES:
            _ensure_audit_log_initialized()

    payload_json = json.dumps(payload or {}, ensure_ascii=False)
    state_event_json = (
        json.dumps(state_event, ensure_ascii=False, sort_keys=True)
        if state_event is not None else None
    )
    created_at = now_iso()

    return _retry_on_locked(
        _emit_inner,
        event_type,
        order_id,
        courier_id,
        payload_json,
        created_at,
        event_id,
        state_event_json,
        event_key,
        expected_state_version,
        expected_state_marker,
        expected_state_token,
        predecessor_event_id,
    )


def get_pending(limit: int = 100, event_types: Optional[list] = None) -> list:
    """Zwraca gotowe pending events posortowane po created_at (FIFO).

    Legacy event bez durable receiptu jest gotowy od razu. Event zapisany z
    ``state_apply_outbox`` staje sie widoczny dla queue consumerow dopiero po
    ``state_status='applied'``. To egzekwuje event -> orders_state -> consumer
    rowniez wtedy, gdy consumer nie uzywa lifecycle locka.
    """
    _ensure_state_apply_outbox_initialized()
    with _conn() as conn:
        if event_types:
            placeholders = ",".join("?" * len(event_types))
            params = tuple(event_types) + (limit,)
            cur = conn.execute(
                f"""SELECT e.event_id, e.event_type, e.order_id, e.courier_id,
                           e.payload, e.created_at
                    FROM events AS e
                    LEFT JOIN state_apply_outbox AS s ON s.event_id = e.event_id
                    WHERE e.status = 'pending'
                      AND (s.event_id IS NULL OR s.state_status = 'applied')
                      AND e.event_type IN ({placeholders})
                    ORDER BY e.created_at ASC, e.rowid ASC LIMIT ?""",
                params,
            )
        else:
            cur = conn.execute(
                """SELECT e.event_id, e.event_type, e.order_id, e.courier_id,
                          e.payload, e.created_at
                   FROM events AS e
                   LEFT JOIN state_apply_outbox AS s ON s.event_id = e.event_id
                   WHERE e.status = 'pending'
                     AND (s.event_id IS NULL OR s.state_status = 'applied')
                   ORDER BY e.created_at ASC, e.rowid ASC LIMIT ?""",
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
    """Oznacza event jako failed (do ponownej analizy).

    Z-P0-05 Faza A: jesli operator JAWNIE zastosowal addytywna migracje retry,
    zapisujemy rowniez ``attempt_count``, ``last_error`` i ``last_failed_at``.
    Nie planujemy kolejnej proby -- automatyczny retry pozostaje OFF. Normalne
    ``pending -> failed`` na starym schemacie zostaje bez zmian; spoznione
    wywolanie dla ``processed``/``failed`` jest teraz bezpiecznym no-op.
    """
    with _conn() as conn:
        try:
            from dispatch_v2 import event_retry

            if event_retry.has_retry_schema(conn):
                changed = event_retry.record_failed_attempt(
                    conn,
                    event_id,
                    error,
                    failed_at=datetime.now(timezone.utc),
                    expected_status="pending",
                )
                if not changed:
                    _log.warning(
                        f"mark_failed stale/no-op event_id={event_id}: "
                        "expected status=pending"
                    )
                _log.warning(f"FAILED {event_id}: {error}")
                return changed
        except Exception as metadata_exc:
            # Metadane sa addytywne; ich awaria nie moze zmienic starego
            # kontraktu wyjecia poison eventu z pending queue. Fallback nadal
            # ma CAS na pending, wiec nie clobberuje processed/failed.
            _log.error(
                f"mark_failed retry metadata error event_id={event_id}: "
                f"{type(metadata_exc).__name__}: {metadata_exc}"
            )
        cur = conn.execute(
            """UPDATE events SET status = 'failed', processed_at = ?
               WHERE event_id = ? AND status = 'pending'""",
            (now_iso(), event_id),
        )
        _log.warning(f"FAILED {event_id}: {error}")
        return cur.rowcount == 1


def stats() -> dict:
    """Statystyki event busa — do safe mode i debugowania."""
    with _conn() as conn:
        cur = conn.execute(
            "SELECT status, COUNT(*) as cnt FROM events GROUP BY status"
        )
        by_status = {row["status"]: row["cnt"] for row in cur.fetchall()}
        cur = conn.execute("SELECT COUNT(*) as cnt FROM processed_events")
        processed_total = cur.fetchone()["cnt"]
        result = {
            "pending": by_status.get("pending", 0),
            "processed": by_status.get("processed", 0),
            "failed": by_status.get("failed", 0),
            "retry_scheduled": by_status.get("retry_scheduled", 0),
            "dead_letter": by_status.get("dead_letter", 0),
            "processed_history": processed_total,
        }
        # Addytywne metryki wieku sa dostepne dopiero po jawnej migracji.
        # Brak/blad metadanych nigdy nie psuje dotychczasowego health path.
        try:
            from dispatch_v2 import event_retry

            if event_retry.has_retry_schema(conn):
                result["retry"] = event_retry.queue_retry_stats(
                    conn, now=datetime.now(timezone.utc)
                )
        except Exception as retry_stats_exc:
            _log.debug(f"retry stats unavailable: {retry_stats_exc}")
        return result


def cleanup(retention_hours: int = 48) -> int:
    """Czysci stare processed events. Zwraca liczbe usunietych.

    MP-#5: Skip podczas peak window (lunch 11-14 / dinner 17-20 Warsaw) — DELETE
    blokuje shadow_dispatcher reads, co negatywnie wpływa na latency propozycji.
    Cleanup uruchamia się przez dispatch-event-bus-cleanup.timer (co godzinę);
    pominięty tick = +1h retention, brak korupcji.
    """
    if _is_peak_window():
        _log.info("cleanup: skip — peak window (Warsaw lunch/dinner)")
        return 0

    _ensure_state_apply_outbox_initialized()
    cutoff = f"-{retention_hours} hours"
    with _conn() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE;")
            # Durable queue receipt musi zniknac atomowo z eventem i jego
            # processed dedupe. Nierozwiazany state/downstream receipt blokuje
            # retencje calej trojki, aby cleanup nie utworzyl orphan/collision
            # window przy ponownej emisji tego samego deterministic event_id.
            outbox_cur = conn.execute(
                """DELETE FROM state_apply_outbox
                   WHERE event_id IN (
                       SELECT event_id FROM events
                       WHERE status = 'processed'
                         AND processed_at < datetime('now', ?)
                   )
                     AND state_status IN ('applied', 'superseded')
                     AND downstream_status IN ('applied', 'skipped')
                     AND NOT EXISTS (
                         SELECT 1 FROM state_apply_outbox AS child
                         WHERE child.predecessor_event_id = state_apply_outbox.event_id
                           AND (
                               child.state_status = 'pending'
                               OR (child.state_status = 'applied'
                                   AND child.downstream_status = 'pending')
                           )
                     )""",
                (cutoff,),
            )
            cur = conn.execute(
                """DELETE FROM processed_events
                   WHERE processed_at < datetime('now', ?)
                     AND NOT EXISTS (
                         SELECT 1 FROM state_apply_outbox AS s
                         WHERE s.event_id = processed_events.event_id
                     )""",
                (cutoff,),
            )
            deleted1 = cur.rowcount
            cur = conn.execute(
                """DELETE FROM events
                   WHERE status = 'processed'
                     AND processed_at < datetime('now', ?)
                     AND NOT EXISTS (
                         SELECT 1 FROM state_apply_outbox AS s
                         WHERE s.event_id = events.event_id
                     )""",
                (cutoff,),
            )
            deleted2 = cur.rowcount
            conn.execute("COMMIT;")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK;")
            raise
        _log.info(
            "cleanup: usunieto "
            f"{deleted1} processed_events, {deleted2} events, "
            f"{outbox_cur.rowcount} domknietych queue outbox"
        )
        return deleted1 + deleted2


# ─────────────────────────────────────────────────────────────────────────
# Opcja C (2026-05-07): audit_log table — append-only, retention 90d.
# Rozdziela role events.db: queue (pending→processed) vs audit log (append-only).
# Typy audit-only uzywaja emit_audit(); gdy caller podaje state_event, wpis
# audit i durable outbox powstaja atomowo w tej samej transakcji.
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


def _emit_audit_inner(
    event_type: str,
    order_id: Optional[str],
    courier_id: Optional[str],
    payload_json: str,
    created_at: str,
    event_id: str,
    state_event_json: Optional[str] = None,
    event_key: Optional[str] = None,
    expected_state_version: Optional[str] = None,
    expected_state_marker: Optional[str] = None,
    expected_state_token: Optional[str] = None,
    predecessor_event_id: Optional[str] = None,
) -> Optional[str]:
    """Inner emit_audit. Wrapped przez emit_audit() w _retry_on_locked."""
    with _conn() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE;")
            cur = conn.execute(
                """INSERT OR IGNORE INTO audit_log
                   (event_id, event_type, order_id, courier_id, payload, created_at)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (event_id, event_type, order_id, courier_id, payload_json, created_at),
            )
            if cur.rowcount == 0:
                if state_event_json is not None:
                    if not order_id or not event_key:
                        raise ValueError(
                            "state_event wymaga niepustych order_id i event_key"
                        )
                    _upgrade_existing_source_with_outbox_tx(
                        conn,
                        source_table="audit_log",
                        event_id=event_id,
                        event_type=event_type,
                        order_id=order_id,
                        courier_id=courier_id,
                        payload_json=payload_json,
                        event_key=event_key,
                        expected_state_version=expected_state_version,
                        expected_state_marker=expected_state_marker,
                        expected_state_token=expected_state_token,
                        predecessor_event_id=predecessor_event_id,
                        state_event_json=state_event_json,
                        created_at=created_at,
                    )
                conn.execute("COMMIT;")
                _log.debug(f"AUDIT DUP: {event_id}")
                return None
            if state_event_json is not None:
                if not order_id or not event_key:
                    raise ValueError(
                        "state_event wymaga niepustych order_id i event_key"
                    )
                _insert_state_apply_outbox(
                    conn,
                    event_id=event_id,
                    event_key=event_key,
                    order_id=str(order_id),
                    expected_state_version=expected_state_version,
                    expected_state_marker=expected_state_marker,
                    expected_state_token=expected_state_token,
                    predecessor_event_id=predecessor_event_id,
                    state_event_json=state_event_json,
                    created_at=created_at,
                )
            conn.execute("COMMIT;")
            _log.info(f"AUDIT {event_type} order={order_id} courier={courier_id} id={event_id}")
            return event_id
        except sqlite3.OperationalError:
            if conn.in_transaction:
                conn.execute("ROLLBACK;")
            raise
        except Exception as e:
            if conn.in_transaction:
                conn.execute("ROLLBACK;")
            _log.error(f"emit_audit() error: {e}")
            raise


def emit_audit(
    event_type: str,
    order_id: Optional[str] = None,
    courier_id: Optional[str] = None,
    payload: Optional[dict] = None,
    event_id: Optional[str] = None,
    *,
    state_event: Optional[dict] = None,
    event_key: Optional[str] = None,
    expected_state_version: Optional[str] = None,
    expected_state_marker: Optional[str] = None,
    expected_state_token: Optional[str] = None,
    predecessor_event_id: Optional[str] = None,
) -> Optional[str]:
    """Zapisuje event audit-only do tabeli audit_log (append-only).

    Idempotent przez INSERT OR IGNORE na PRIMARY KEY event_id.
    Dla typów z AUDIT_EVENT_TYPES (COURIER_ASSIGNED, CZAS_KURIERA_UPDATED,
    PANEL_UNREACHABLE, ORDER_RETURNED_TO_POOL).

    Bez ``state_event`` funkcja zapisuje tylko historię audytową (brak
    status/processed_at). Z ``state_event`` atomowo dopisuje także durable
    receipt; faktyczny state apply wykonuje wspólny bridge C3.

    MP-#5: Transient SQLite lock errors retry'owane (3x exp backoff).

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
    if state_event is not None:
        _validate_durable_state_event(
            event_type, order_id, courier_id, event_id, state_event, event_key
        )
        _ensure_state_apply_outbox_initialized()
    payload_json = json.dumps(payload or {}, ensure_ascii=False)
    state_event_json = (
        json.dumps(state_event, ensure_ascii=False, sort_keys=True)
        if state_event is not None else None
    )
    created_at = now_iso()

    return _retry_on_locked(
        _emit_audit_inner,
        event_type,
        order_id,
        courier_id,
        payload_json,
        created_at,
        event_id,
        state_event_json,
        event_key,
        expected_state_version,
        expected_state_marker,
        expected_state_token,
        predecessor_event_id,
    )


def cleanup_audit_log(retention_days: int = 90) -> int:
    """Czysci audit_log starsze niz retention_days. Zwraca liczbe usunietych.

    MP-#5: Skip podczas peak window (Warsaw lunch/dinner) — DELETE blokuje readers.
    """
    if _is_peak_window():
        _log.info("cleanup_audit_log: skip — peak window (Warsaw lunch/dinner)")
        return 0

    _ensure_audit_log_initialized()
    _ensure_state_apply_outbox_initialized()
    with _conn() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE;")
            cur = conn.execute(
                """DELETE FROM audit_log
                   WHERE created_at < datetime('now', ?)
                     AND NOT EXISTS (
                         SELECT 1 FROM state_apply_outbox AS receipt
                         WHERE receipt.event_id = audit_log.event_id
                           AND (
                               receipt.state_status = 'pending'
                               OR (receipt.state_status = 'applied'
                                   AND receipt.downstream_status = 'pending')
                               OR EXISTS (
                                   SELECT 1 FROM state_apply_outbox AS child
                                   WHERE child.predecessor_event_id = receipt.event_id
                                     AND (
                                         child.state_status = 'pending'
                                         OR (child.state_status = 'applied'
                                             AND child.downstream_status = 'pending')
                                     )
                               OR EXISTS (
                                   SELECT 1 FROM events AS queue_event
                                   WHERE queue_event.event_id = receipt.event_id
                                     AND queue_event.status != 'processed'
                               )
                           )
                           )
                     )""",
                (f"-{retention_days} days",),
            )
            deleted = cur.rowcount
            outbox_cur = conn.execute(
                """DELETE FROM state_apply_outbox
                   WHERE state_status IN ('applied', 'superseded')
                     AND downstream_status IN ('applied', 'skipped')
                     AND created_at < datetime('now', ?)
                     AND NOT EXISTS (
                         SELECT 1 FROM events AS queue_event
                         WHERE queue_event.event_id = state_apply_outbox.event_id
                           AND queue_event.status != 'processed'
                     )
                     AND NOT EXISTS (
                         SELECT 1 FROM state_apply_outbox AS child
                         WHERE child.predecessor_event_id = state_apply_outbox.event_id
                           AND (
                               child.state_status = 'pending'
                               OR (child.state_status = 'applied'
                                   AND child.downstream_status = 'pending')
                           )
                     )""",
                (f"-{retention_days} days",),
            )
            conn.execute("COMMIT;")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK;")
            raise
        _log.info(f"cleanup_audit_log: usunieto {deleted} audit_log entries (retention={retention_days}d)")
        if outbox_cur.rowcount:
            _log.info(
                "cleanup_audit_log: usunieto "
                f"{outbox_cur.rowcount} domknietych state_apply_outbox"
            )
        return deleted


def cleanup_broadcast(retention_days: int = 7) -> int:
    """Czysci broadcast events (status='broadcast') starsze niz retention_days.

    A4 follow-up (2026-05-08): broadcast events nie są konsumowane przez
    cleanup() (filtruje po status='processed') ani cleanup_audit_log() (osobna
    tabela). Subscribers konsumują przez cursor (NIE zmieniają status), więc
    bez tego GC events table puchnie liniowo z liczbą broadcast emit.

    Default 7d retention: dłużej niż jakikolwiek realistyczny subscriber gap
    (consumer offline >7d to incydent), krócej niż audit_log 90d (broadcast NIE
    służy audit — od tego jest audit_log dla typów w AUDIT_EVENT_TYPES).

    MP-#5: Skip podczas peak window (Warsaw lunch/dinner) — DELETE blokuje readers.
    """
    if _is_peak_window():
        _log.info("cleanup_broadcast: skip — peak window (Warsaw lunch/dinner)")
        return 0

    with _conn() as conn:
        cur = conn.execute(
            """DELETE FROM events
               WHERE status = 'broadcast' AND created_at < datetime('now', ?)""",
            (f"-{retention_days} days",),
        )
        deleted = cur.rowcount
        _log.info(f"cleanup_broadcast: usunieto {deleted} broadcast events (retention={retention_days}d)")
        return deleted


def get_pending_count(event_types: Optional[list] = None) -> int:
    """Zwraca liczbę pending events w tabeli events.
    Opcjonalnie filtruje po event_types (np. tylko queue typy dla WORKER_STUCK alert).

    To celowo surowy backlog storage, nie liczba eventow gotowych z
    ``get_pending``: durable event oczekujacy na state apply ma pozostac
    widoczny dla alarmu stuck, mimo ze consumer nie moze go jeszcze pobrac.
    """
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


# ===========================================================================
# A4 (audit META RC2 2026-05-07) — CONFIG_RELOAD broadcast pub/sub
# ===========================================================================


def make_broadcast_event_id(scope: str) -> str:
    """Collision-immune event_id dla broadcast events.

    Pattern: CONFIG_RELOAD_<scope>_<ns>_<hex>
    ns = time.time_ns() (nanoseconds since epoch, monotonic enough)
    hex = 8-char random suffix dla cross-process collision safety
    """
    import secrets
    return f"CONFIG_RELOAD_{scope}_{time.time_ns()}_{secrets.token_hex(4)}"


def _emit_broadcast_inner(
    event_type: str,
    scope: str,
    payload_json: str,
    created_at: str,
    event_id: str,
) -> Optional[str]:
    """Inner emit dla broadcast events. status='broadcast' (NIE pending) — invisible
    dla queue consumers (get_pending). Wrapped przez emit_config_reload."""
    with _conn() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE;")
            cur = conn.execute(
                """INSERT OR IGNORE INTO events
                   (event_id, event_type, order_id, courier_id, payload, created_at, status)
                   VALUES (?, ?, NULL, NULL, ?, ?, 'broadcast')""",
                (event_id, event_type, payload_json, created_at),
            )
            if cur.rowcount == 0:
                conn.execute("COMMIT;")
                _log.debug(f"DUP broadcast: {event_id}")
                return None
            conn.execute("COMMIT;")
            _log.info(f"BROADCAST {event_type} scope={scope} id={event_id}")
            return event_id
        except sqlite3.OperationalError:
            conn.execute("ROLLBACK;")
            raise
        except Exception as e:
            conn.execute("ROLLBACK;")
            _log.error(f"emit_broadcast() error: {e}")
            raise


def emit_config_reload(scope: str, payload: Optional[dict] = None) -> Optional[str]:
    """Broadcast CONFIG_RELOAD event do wszystkich subscriberów.

    scope: identifier co się zmieniło (np. 'flags', 'courier_tiers', 'kurier_ids').
    payload: dict z details (np. {"name": "FLAG_X", "value": True}) — subscriber
             może użyć do targeted invalidation albo zignorować i zrobić full reload.

    Returns event_id (or None gdy SQLite locked exhausted retry).
    Defensywne: emit fail NIE crashuje callera (logowane, return None).

    Używa status='broadcast' w events table — invisible dla get_pending/queue
    consumers. Subscriber używa poll_broadcast() z per-process cursor.
    """
    payload_dict = dict(payload or {})
    payload_dict.setdefault("scope", scope)
    payload_json = json.dumps(payload_dict, ensure_ascii=False)
    created_at = now_iso()
    event_id = make_broadcast_event_id(scope)
    try:
        return _retry_on_locked(
            _emit_broadcast_inner, "CONFIG_RELOAD", scope, payload_json, created_at, event_id,
        )
    except Exception as e:
        _log.error(f"emit_config_reload(scope={scope}) FAIL ({type(e).__name__}: {e})")
        return None


def poll_broadcast(
    event_types: List[str],
    since_event_id: Optional[str] = None,
    limit: int = 100,
) -> List[dict]:
    """Subscriber poll dla broadcast events.

    event_types: lista typów do filter (musi być subset BROADCAST_EVENT_TYPES).
    since_event_id: cursor — zwracane TYLKO eventy z event_id > since (lex order).
                    None → returns all broadcast events od początku (use cap dla limit).
    limit: max events zwróconych w jednym poll.

    Returns lista dictów {event_id, event_type, payload, created_at}.
    Subscriber persistuje max(event_id) jako new cursor.
    """
    bad = [t for t in event_types if t not in BROADCAST_EVENT_TYPES]
    if bad:
        raise ValueError(f"poll_broadcast: event_types {bad} not in BROADCAST_EVENT_TYPES={BROADCAST_EVENT_TYPES}")
    placeholders = ",".join("?" * len(event_types))
    with _conn() as conn:
        if since_event_id:
            cur = conn.execute(
                f"""SELECT event_id, event_type, payload, created_at
                    FROM events
                    WHERE status = 'broadcast'
                      AND event_type IN ({placeholders})
                      AND event_id > ?
                    ORDER BY event_id ASC LIMIT ?""",
                tuple(event_types) + (since_event_id, limit),
            )
        else:
            cur = conn.execute(
                f"""SELECT event_id, event_type, payload, created_at
                    FROM events
                    WHERE status = 'broadcast'
                      AND event_type IN ({placeholders})
                    ORDER BY event_id ASC LIMIT ?""",
                tuple(event_types) + (limit,),
            )
        return [
            {
                "event_id": row["event_id"],
                "event_type": row["event_type"],
                "payload": json.loads(row["payload"]) if row["payload"] else {},
                "created_at": row["created_at"],
            }
            for row in cur.fetchall()
        ]
