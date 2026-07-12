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
import time
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, List, Optional
from zoneinfo import ZoneInfo

from dispatch_v2.common import load_config, now_iso, setup_logger
from dispatch_v2.event_envelope import (
    ENVELOPE_VERSION,
    IDENTITY_SCHEME,
    EventEnvelope,
    canonical_payload_json,
)

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

# Wersja semantyki koperty, NIE wybrana polityka retry ani flaga runtime.
ORDER_EVENT_POLICY_VERSION = "order_fsm.v1"


# Zamkniety katalog typow eventow
EVENT_TYPES = {
    "NEW_ORDER",
    "ORDER_READY",
    "COURIER_PICKED_UP",
    "COURIER_DELIVERED",
    "COURIER_ASSIGNED",
    "COURIER_REJECTED_PROPOSAL",
    "ORDER_RETURNED_TO_POOL",
    "ORDER_CANCELLED",
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
    "ORDER_CANCELLED",
}

# Korekty E0 sa formalnym FSM, ale historycznie nie byly publicznym eventem
# legacy bus. E1 wlacza je tylko wewnatrz jawnej sciezki durable, aby OFF nie
# zmienial katalogu ani zachowania dotychczasowych consumerow.
DURABLE_ONLY_AUDIT_EVENT_TYPES = frozenset({"ORDER_RESURRECTED"})

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


def create_order_envelope(
    *,
    event_id: str,
    event_type: str,
    order_id: Optional[str],
    courier_id: Optional[str],
    payload: dict,
    created_at,
    source: str,
    policy_version: str,
    producer_key: str,
) -> EventEnvelope:
    """Fabryka bez zegara/ID fallbacku; call-site przekazuje wszystkie fakty."""
    return EventEnvelope.from_parts(
        event_id=event_id,
        event_type=event_type,
        order_id=order_id,
        courier_id=courier_id,
        payload=payload,
        created_at=created_at,
        source=source,
        envelope_version=ENVELOPE_VERSION,
        policy_version=policy_version,
        producer_key=producer_key,
        identity_scheme=IDENTITY_SCHEME,
    )


def maybe_create_order_envelope(**kwargs) -> Optional[EventEnvelope]:
    """Lazy boundary: OFF nie konstruuje ani nie waliduje koperty E1."""
    from dispatch_v2 import event_outbox

    if not event_outbox.DURABLE_EVENT_OUTBOX_ENABLED:
        return None
    return create_order_envelope(**kwargs)


def durable_envelope_kwargs(
    envelope: Optional[EventEnvelope],
) -> dict[str, EventEnvelope]:
    """Przekazuje kopertę przez boundary tylko w syntetycznie wlaczonym E1.

    OFF nie dodaje nowego argumentu do legacy call graphu. To utrzymuje rowniez
    parytet boundary-mockow, ktore sa wykonywalnym kontraktem starych callerow.
    """
    from dispatch_v2 import event_outbox

    if not event_outbox.DURABLE_EVENT_OUTBOX_ENABLED:
        return {}
    if envelope is None:
        raise ValueError("durable event publish requires a call-site envelope")
    return {"envelope": envelope}


def _validate_envelope_call(
    envelope: EventEnvelope,
    *,
    event_type: str,
    order_id: Optional[str],
    courier_id: Optional[str],
    payload: Optional[dict],
    event_id: Optional[str],
) -> None:
    expected_courier = str(courier_id) if courier_id not in (None, "") else None
    expected_order = str(order_id) if order_id is not None else None
    if (
        envelope.event_type != event_type
        or envelope.order_id != expected_order
        or envelope.courier_id != expected_courier
        or envelope.event_id != str(event_id or "")
        or envelope.payload_json != canonical_payload_json(payload or {})
    ):
        raise ValueError("durable envelope does not match emit call")


def _durable_publish(
    envelope: EventEnvelope,
    *,
    delivery_kind: str,
    retention_policy_id: Optional[str],
    allow_test_policy: bool,
) -> Optional[str]:
    from dispatch_v2 import event_outbox

    policy_id = str(retention_policy_id or "").strip()
    if not policy_id:
        raise event_outbox.RetentionPolicyRequired(
            "durable publish requires an explicit retention_policy_id"
        )
    mirror_audit = envelope.event_type in AUDIT_MIRRORED_QUEUE_TYPES
    if delivery_kind == "audit" or mirror_audit:
        _ensure_audit_log_initialized()
    with _conn() as conn:
        result = event_outbox.publish(
            conn,
            envelope,
            delivery_kind=delivery_kind,
            retention_policy_id=policy_id,
            allow_test_policy=allow_test_policy,
            mirror_audit=mirror_audit,
            write_legacy_compat=True,
        )
    return result.event_id if result.inserted else None


def _emit_inner(
    event_type: str,
    order_id: Optional[str],
    courier_id: Optional[str],
    payload_json: str,
    created_at: str,
    event_id: str,
    idempotency_key_digest: str,
) -> Optional[str]:
    """Inner emit z transaction body. Wrapped przez emit() w _retry_on_locked."""
    with _conn() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE;")
            cur = conn.execute(
                "SELECT 1 FROM processed_events WHERE event_id = ?",
                (event_id,),
            )
            if cur.fetchone():
                conn.execute("COMMIT;")
                _log.debug(f"DUP (processed): {event_id}")
                return None

            from dispatch_v2 import event_retry

            if event_retry.has_retry_schema(conn):
                cur = conn.execute(
                    """INSERT OR IGNORE INTO events
                       (event_id, event_type, order_id, courier_id, payload,
                        created_at, status, idempotency_key)
                       VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)""",
                    (
                        event_id,
                        event_type,
                        order_id,
                        courier_id,
                        payload_json,
                        created_at,
                        idempotency_key_digest,
                    ),
                )
            else:
                cur = conn.execute(
                    """INSERT OR IGNORE INTO events
                       (event_id, event_type, order_id, courier_id, payload,
                        created_at, status)
                       VALUES (?, ?, ?, ?, ?, ?, 'pending')""",
                    (
                        event_id,
                        event_type,
                        order_id,
                        courier_id,
                        payload_json,
                        created_at,
                    ),
                )
            if cur.rowcount == 0:
                conn.execute("COMMIT;")
                _log.debug(f"DUP (pending): {event_id}")
                return None

            conn.execute("COMMIT;")
            _log.info(f"EMIT {event_type} order={order_id} courier={courier_id} id={event_id}")
        except sqlite3.OperationalError:
            conn.execute("ROLLBACK;")
            raise
        except Exception as e:
            conn.execute("ROLLBACK;")
            _log.error(f"emit() error: {e}")
            raise

    # Tech debt #39: mirror queue types that need 90-day analytics retention.
    # Called AFTER queue conn closes — own connection, isolated failure (best-effort).
    if event_type in AUDIT_MIRRORED_QUEUE_TYPES:
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
    idempotency_key: Optional[str] = None,
    envelope: Optional[EventEnvelope] = None,
    retention_policy_id: Optional[str] = None,
    allow_test_policy: bool = False,
) -> Optional[str]:
    """Emituje event. Zwraca event_id lub None jesli duplikat.

    Jesli event_id nie podany -> generowany deterministycznie.
    Jesli event_id juz istnieje w bazie -> ZWRACA None (idempotent skip).

    MP-#5: Transient SQLite lock errors retry'owane (3x exp backoff).

    Tech debt #39: COURIER_PICKED_UP and COURIER_DELIVERED are automatically
    mirrored to audit_log (90‑day retention) for analytics consumers such as
    the R‑04 evaluator.  The mirror is best‑effort and never blocks the queue
    emit.
    """
    if event_type not in EVENT_TYPES:
        raise ValueError(f"Nieznany event_type: {event_type}. Dozwolone: {EVENT_TYPES}")

    from dispatch_v2 import event_outbox

    if event_outbox.DURABLE_EVENT_OUTBOX_ENABLED:
        if envelope is None:
            raise ValueError("durable event publish requires a call-site envelope")
        _validate_envelope_call(
            envelope,
            event_type=event_type,
            order_id=order_id,
            courier_id=courier_id,
            payload=payload,
            event_id=event_id,
        )
        return _retry_on_locked(
            _durable_publish,
            envelope,
            delivery_kind="queue",
            retention_policy_id=retention_policy_id,
            allow_test_policy=allow_test_policy,
        )

    if event_id is None:
        event_id = make_event_id(event_type, order_id)

    from dispatch_v2 import event_retry

    idempotency_key_digest = event_retry.idempotency_key(
        idempotency_key if idempotency_key is not None else event_id
    )

    payload_json = json.dumps(payload or {}, ensure_ascii=False)
    created_at = now_iso()

    return _retry_on_locked(
        _emit_inner,
        event_type,
        order_id,
        courier_id,
        payload_json,
        created_at,
        event_id,
        idempotency_key_digest,
    )


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


def mark_processed(
    event_id: str,
    *,
    retry_consumer_enabled: bool = False,
) -> bool:
    """Oznacza event jako processed; default zachowuje historyczny kontrakt.

    ``retry_consumer_enabled=False`` zawsze wykonuje bezwarunkowy UPDATE po
    ``event_id``, INSERT OR IGNORE do ``processed_events`` i zwraca ``True`` --
    rowniez dla missing/failed. Obecnosc migracji niczego tu nie aktywuje.
    Restrykcyjny CAS i czyszczenie obu aliasow wymagaja jawnego opt-in przyszlego
    consumera retry.
    """
    processed_at = now_iso()
    with _conn() as conn:
        try:
            conn.execute("BEGIN IMMEDIATE;")
            if not retry_consumer_enabled:
                conn.execute(
                    """UPDATE events SET status='processed', processed_at=?
                       WHERE event_id=?""",
                    (processed_at, event_id),
                )
                conn.execute(
                    """INSERT OR IGNORE INTO processed_events
                       (event_id, processed_at) VALUES (?, ?)""",
                    (event_id, processed_at),
                )
                result = True
            else:
                from dispatch_v2 import event_retry

                if not event_retry.has_retry_schema(conn):
                    raise RuntimeError(
                        "retry consumer requires the complete retry metadata schema"
                    )
                cur = conn.execute(
                    """UPDATE events
                       SET status='processed', processed_at=?,
                           next_attempt_at=NULL, next_retry_at=NULL
                       WHERE event_id=?
                         AND status IN ('pending','retry_scheduled')""",
                    (processed_at, event_id),
                )
            if retry_consumer_enabled:
                if cur.rowcount == 1:
                    conn.execute(
                        """INSERT OR IGNORE INTO processed_events
                           (event_id, processed_at) VALUES (?, ?)""",
                        (event_id, processed_at),
                    )
                    result = True
                else:
                    row = conn.execute(
                        "SELECT status FROM events WHERE event_id=?", (event_id,)
                    ).fetchone()
                    result = bool(row and row[0] == "processed")
                    if result:
                        conn.execute(
                            """INSERT OR IGNORE INTO processed_events
                               (event_id, processed_at) VALUES (?, ?)""",
                            (event_id, processed_at),
                        )
            conn.execute("COMMIT;")
            return result
        except Exception as e:
            conn.execute("ROLLBACK;")
            _log.error(
                "mark_processed() error "
                f"error_class={type(e).__name__}"
            )
            return False


def mark_failed(
    event_id: str,
    error,
    *,
    enabled: bool = False,
    policy=None,
    policy_id: Optional[str] = None,
) -> bool:
    """Oznacza event jako failed (do ponownej analizy).

    Z-P0-05 Faza A: jesli operator JAWNIE zastosowal addytywna migracje retry,
    zapisujemy rowniez ``attempt_count``, ``last_error`` i ``last_failed_at``.
    Przy domyslnym ``enabled=False`` kazda klasa bledu konczy jako ``failed``
    bez terminu retry. Retry/DLQ wymaga jednoczesnie ``enabled=True``, jawnej
    polityki i jej wersjonowanego ``policy_id``. Normalne ``pending -> failed``
    na starym schemacie zostaje bez zmian; spoznione wywolanie dla
    ``processed``/``failed`` jest bezpiecznym no-op.
    """
    with _conn() as conn:
        from dispatch_v2 import event_retry

        descriptor = event_retry.classify_failure(error)
        event_ref = event_retry.idempotency_key(event_id)[:12]
        if enabled and policy is None:
            raise ValueError("enabled retry/DLQ requires an explicit RetryPolicy")
        if enabled and not str(policy_id or "").strip():
            raise ValueError("enabled retry/DLQ requires an explicit policy_id")
        try:
            if event_retry.has_retry_schema(conn):
                transition = event_retry.record_failure(
                    conn,
                    event_id,
                    error,
                    failed_at=datetime.now(timezone.utc),
                    expected_status="pending",
                    enabled=enabled,
                    policy=policy,
                    policy_id=policy_id,
                )
                if not transition.changed:
                    _log.warning(
                        f"mark_failed stale/no-op event_ref={event_ref}: "
                        "expected status=pending"
                    )
                _log.warning(
                    "EVENT_FAILURE "
                    f"event_ref={event_ref} class={descriptor.failure_class.value} "
                    f"code={descriptor.error_code} status={transition.status}"
                )
                return transition.changed
            if enabled:
                raise RuntimeError(
                    "enabled retry/DLQ requires the event retry metadata schema"
                )
        except Exception as metadata_exc:
            if enabled:
                _log.error(
                    f"mark_failed enforced transition error event_ref={event_ref} "
                    f"error_class={type(metadata_exc).__name__}"
                )
                raise
            # Metadane sa addytywne; ich awaria nie moze zmienic starego
            # kontraktu wyjecia poison eventu z pending queue. Fallback nadal
            # ma CAS na pending, wiec nie clobberuje processed/failed.
            _log.error(
                f"mark_failed retry metadata error event_ref={event_ref} "
                f"error_class={type(metadata_exc).__name__}"
            )
        cur = conn.execute(
            """UPDATE events SET status = 'failed', processed_at = ?
               WHERE event_id = ? AND status = 'pending'""",
            (now_iso(), event_id),
        )
        _log.warning(
            "EVENT_FAILURE_LEGACY_SCHEMA "
            f"event_ref={event_ref} class={descriptor.failure_class.value} "
            f"code={descriptor.error_code}"
        )
        return cur.rowcount == 1


@dataclass(frozen=True)
class StateEffectResult:
    """Wynik pojedynczego publish->state effect; worker retry nadal nie istnieje."""

    record: Optional[dict]
    changed: bool
    duplicate: bool
    quarantined: bool
    enforcement_enabled: bool
    should_run_followups: bool
    error_code: Optional[str] = None


def apply_state_event(
    event: dict[str, Any],
    *,
    event_id: Optional[str],
    emitted: bool,
    enforce: Optional[bool] = None,
    envelope: Optional[EventEnvelope] = None,
) -> StateEffectResult:
    """Jeden owner aplikacji eventu do state z jawnym OFF/ON.

    OFF zachowuje dotychczasowy kontrakt bajtowy call-site'ow: duplikat emit
    nie uruchamia ponownie legacy writera. ON jest w tym sprincie wylacznie
    handoffem do outboxa: producer nie aplikuje stanu ani follow-upow inline.
    Jawny prymityw consumera ``state_machine.commit_durable_state_claim`` jest
    uruchamiany tylko przez syntetyczny golden case; worker nie istnieje.
    """
    from dispatch_v2 import event_outbox, state_machine

    if event_outbox.DURABLE_EVENT_OUTBOX_ENABLED:
        if envelope is None:
            raise ValueError("durable state handoff requires the canonical envelope")
        if (
            envelope.event_id != str(event_id or "")
            or envelope.event_type != str(event.get("event_type") or "")
            or envelope.order_id
            != (str(event.get("order_id")) if event.get("order_id") is not None else None)
        ):
            raise ValueError("durable state handoff identity differs from envelope")
        # Jeden owner stanu jest przyszlym consumerem outbox. Inline producer
        # nie mutuje JSON i nie wykonuje follow-upow. Brak workera w tym sprincie
        # oznacza swiadomy HOLD, a nie ciche przejscie sciezka E0.
        return StateEffectResult(
            record=None,
            changed=False,
            duplicate=not bool(emitted),
            quarantined=False,
            enforcement_enabled=True,
            should_run_followups=False,
        )

    enforcement_enabled = (
        state_machine.ORDER_FSM_ENFORCEMENT_ENABLED
        if enforce is None
        else bool(enforce)
    )
    if not emitted and not enforcement_enabled:
        return StateEffectResult(
            record=None,
            changed=False,
            duplicate=True,
            quarantined=False,
            enforcement_enabled=False,
            should_run_followups=False,
        )

    state_event = dict(event)
    state_event["payload"] = dict(event.get("payload") or {})
    if event_id:
        state_event["event_id"] = event_id
        state_event["idempotency_key"] = event_id
    try:
        applied = state_machine.apply_order_event(
            state_event,
            enforce=enforcement_enabled,
        )
    except state_machine.OrderEventRejected as exc:
        if event_id:
            from dispatch_v2 import event_retry

            mark_failed(
                event_id,
                exc,
                enabled=True,
                policy=event_retry.FSM_QUARANTINE_POLICY,
                policy_id=event_retry.FSM_QUARANTINE_POLICY_ID,
            )
        return StateEffectResult(
            record=None,
            changed=False,
            duplicate=False,
            quarantined=True,
            enforcement_enabled=enforcement_enabled,
            should_run_followups=False,
            error_code=exc.error_code,
        )
    except state_machine.ConcurrentOrderEvent as exc:
        if event_id:
            mark_failed(event_id, exc)
        return StateEffectResult(
            record=None,
            changed=False,
            duplicate=False,
            quarantined=False,
            enforcement_enabled=enforcement_enabled,
            should_run_followups=False,
            error_code=exc.error_code,
        )

    if event_id and enforcement_enabled:
        from dispatch_v2 import event_retry

        try:
            with _conn() as conn:
                if event_retry.has_retry_schema(conn):
                    event_retry.mark_effect_applied(
                        conn,
                        event_id,
                        applied_at=datetime.now(timezone.utc),
                    )
        except Exception as exc:
            # State receipt jest autorytatywnym dedupem. Brak markera DB jest
            # naprawialny kolejnym replayem i nie moze wywolac drugiego efektu.
            _log.warning(
                "STATE_EFFECT_MARKER_DEFERRED "
                f"event_ref={event_retry.idempotency_key(event_id)[:12]} "
                f"error_class={type(exc).__name__}"
            )

    should_run_followups = (
        applied.changed if enforcement_enabled else bool(emitted)
    )
    return StateEffectResult(
        record=applied.record,
        changed=applied.changed,
        duplicate=applied.duplicate,
        quarantined=False,
        enforcement_enabled=enforcement_enabled,
        should_run_followups=should_run_followups,
    )


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


def _emit_audit_inner(
    event_type: str,
    order_id: Optional[str],
    courier_id: Optional[str],
    payload_json: str,
    created_at: str,
    event_id: str,
) -> Optional[str]:
    """Inner emit_audit. Wrapped przez emit_audit() w _retry_on_locked."""
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
        except sqlite3.OperationalError:
            raise
        except Exception as e:
            _log.error(f"emit_audit() error: {e}")
            raise


def emit_audit(
    event_type: str,
    order_id: Optional[str] = None,
    courier_id: Optional[str] = None,
    payload: Optional[dict] = None,
    event_id: Optional[str] = None,
    envelope: Optional[EventEnvelope] = None,
    retention_policy_id: Optional[str] = None,
    allow_test_policy: bool = False,
) -> Optional[str]:
    """Zapisuje event audit-only do tabeli audit_log (append-only).

    Idempotent przez INSERT OR IGNORE na PRIMARY KEY event_id.
    Dla typów z AUDIT_EVENT_TYPES (COURIER_ASSIGNED, CZAS_KURIERA_UPDATED,
    PANEL_UNREACHABLE, ORDER_RETURNED_TO_POOL).

    Stan persistowany przez state_machine.update_from_event wywoływany inline
    z call site — ta funkcja jest TYLKO audit history. Brak status/processed_at.

    MP-#5: Transient SQLite lock errors retry'owane (3x exp backoff).

    Zwraca event_id przy zapisie, None przy idempotent skip (duplikat).
    """
    from dispatch_v2 import event_outbox

    durable_only = (
        event_outbox.DURABLE_EVENT_OUTBOX_ENABLED
        and event_type in DURABLE_ONLY_AUDIT_EVENT_TYPES
    )
    if event_type not in AUDIT_EVENT_TYPES and not durable_only:
        raise ValueError(
            f"emit_audit: event_type '{event_type}' nie jest audit type. "
            f"Dozwolone: {AUDIT_EVENT_TYPES}. Użyj emit() dla queue typów."
        )

    if event_outbox.DURABLE_EVENT_OUTBOX_ENABLED:
        if envelope is None:
            raise ValueError("durable audit publish requires a call-site envelope")
        _validate_envelope_call(
            envelope,
            event_type=event_type,
            order_id=order_id,
            courier_id=courier_id,
            payload=payload,
            event_id=event_id,
        )
        return _retry_on_locked(
            _durable_publish,
            envelope,
            delivery_kind="audit",
            retention_policy_id=retention_policy_id,
            allow_test_policy=allow_test_policy,
        )

    if event_id is None:
        event_id = make_event_id(event_type, order_id)

    _ensure_audit_log_initialized()
    payload_json = json.dumps(payload or {}, ensure_ascii=False)
    created_at = now_iso()

    return _retry_on_locked(
        _emit_audit_inner, event_type, order_id, courier_id, payload_json, created_at, event_id,
    )


def cleanup_audit_log(retention_days: int = 90) -> int:
    """Czysci audit_log starsze niz retention_days. Zwraca liczbe usunietych.

    MP-#5: Skip podczas peak window (Warsaw lunch/dinner) — DELETE blokuje readers.
    """
    if _is_peak_window():
        _log.info("cleanup_audit_log: skip — peak window (Warsaw lunch/dinner)")
        return 0

    _ensure_audit_log_initialized()
    with _conn() as conn:
        cur = conn.execute(
            """DELETE FROM audit_log WHERE created_at < datetime('now', ?)""",
            (f"-{retention_days} days",),
        )
        deleted = cur.rowcount
        _log.info(f"cleanup_audit_log: usunieto {deleted} audit_log entries (retention={retention_days}d)")
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
