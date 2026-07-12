"""Mechanika retry/DLQ dla event busa -- bez polityki i bez auto-wiringu.

Faza A Z-P0-05 celowo rozdziela mechanike od decyzji operacyjnych:

* :class:`RetryPolicy` nie ma wartosci domyslnych. Caller musi jawnie podac
  limit prob i backoff.
* :func:`schedule_retry` oraz :func:`requeue_dead_letter` sa domyslnie
  zablokowane. Sam import modulu ani wdrozenie kodu nie uruchamia retry.
* helpery pracuja na przekazanym polaczeniu SQLite. Nie otwieraja produkcyjnej
  bazy i nie wykonuja migracji schematu.

Docelowy worker moze uzyc tych prymitywow po osobnym ACK dla polityki, migracji
i flipa. Do tego czasu obecny lifecycle ``pending -> processed|failed`` zostaje
bez zmian, z wyjatkiem addytywnego zapisu diagnozy przez ``mark_failed`` wtedy,
gdy operator wczesniej jawnie zastosowal migracje metadanych.
"""
from __future__ import annotations

import json
import hashlib
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional, Sequence


AUTOMATIC_RETRY_ENABLED = False
"""Twardy bezpieczny default Fazy A. Nigdzie w runtime nie jest flipowany."""

SELECTED_RETRY_POLICY_ID: Optional[str] = None
"""Brak decyzji operacyjnej: worker nie ma wybranej polityki wykonawczej."""

RETRY_STATUS = "retry_scheduled"
DEAD_LETTER_STATUS = "dead_letter"

RETRY_METADATA_COLUMNS = (
    "attempt_count",
    "last_error",
    "failure_class",
    "error_code",
    "next_attempt_at",
    "next_retry_at",
    "last_failed_at",
    "dead_lettered_at",
    "replay_count",
    "last_replayed_at",
    "last_replay_reason",
    "idempotency_key",
    "effect_applied_at",
    "retry_policy_id",
)

SAFE_REPLAY_REASON_CODES = frozenset({
    "operator_verified_fix",
    "source_repaired",
    "false_positive_review",
    "test_only",
})


class FailureClass(str, Enum):
    """Bezpieczna, zamknieta klasyfikacja decyzji retry/DLQ."""

    TRANSIENT = "transient"
    PERMANENT = "permanent"
    ILLEGAL = "illegal"


@dataclass(frozen=True)
class FailureDescriptor:
    failure_class: FailureClass
    error_code: str

    @property
    def terminal(self) -> bool:
        return self.failure_class in {FailureClass.PERMANENT, FailureClass.ILLEGAL}


_SAFE_ERROR_CODES = frozenset({
    "sqlite_busy",
    "timeout",
    "connection_unavailable",
    "invalid_payload",
    "invalid_timestamp",
    "illegal_transition",
    "stale_event",
    "concurrent_state_change",
    "untyped_failure",
    "unexpected_failure",
})


def _utc_iso(value: datetime) -> str:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat()


def _parse_iso(value: Optional[str]) -> Optional[datetime]:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def idempotency_key(value: Any) -> str:
    """Stabilny digest SHA-256 do dedupu, nie pseudonimizacja ani ochrona PII."""
    return hashlib.sha256(str(value).encode("utf-8")).hexdigest()


def event_reference(
    event_id: Any,
    *,
    stored_idempotency_key: Any = None,
    length: int = 12,
) -> str:
    """Krotki digest korelacyjny; nie zwraca surowego ani stored klucza.

    Stored wartosc jest zawsze hashowana ponownie. Gdy jej brak, najpierw
    wyliczamy techniczny klucz dedupu z event_id, a potem digest referencji.
    Funkcja nie jest mechanizmem anonimizacji ani ochrona low-entropy PII.
    """
    width = max(1, min(64, int(length)))
    stored = str(stored_idempotency_key or "").strip()
    stored_is_digest = len(stored) == 64 and all(
        character in "0123456789abcdefABCDEF" for character in stored
    )
    source = stored.lower() if stored_is_digest else idempotency_key(event_id)
    return idempotency_key(source)[:width]


def _safe_descriptor(failure_class: Any, error_code: Any) -> FailureDescriptor:
    if isinstance(failure_class, FailureClass):
        klass = failure_class
    else:
        try:
            klass = FailureClass(str(failure_class))
        except ValueError:
            klass = FailureClass.PERMANENT
    code = str(error_code or "unexpected_failure")
    if code not in _SAFE_ERROR_CODES:
        code = "unexpected_failure"
    return FailureDescriptor(klass, code)


def normalize_failure_metadata(
    failure_class: Any,
    error_code: Any,
) -> FailureDescriptor:
    """Waliduje legacy metadata do zamknietej klasy i bezpiecznego kodu."""
    return _safe_descriptor(failure_class, error_code)


def sanitize_replay_reason(value: Any) -> Optional[str]:
    """Zwraca tylko jawny kod allowlisty; legacy free-text znika z outputu."""
    reason = str(value or "").strip()
    return reason if reason in SAFE_REPLAY_REASON_CODES else None


def classify_failure(error: Any) -> FailureDescriptor:
    """Klasyfikuje bez zapisu ``str(error)`` ani ``repr(error)``.

    Wyjatki FSM wystawiaja zamkniete atrybuty ``failure_class``/``error_code``.
    Dla SQLite uzywamy kodu biblioteki, nie tekstu bledu. Nieznany albo juz
    zserializowany string jest poison/permanent: bez typu nie wolno zgadywac,
    ze ponowienie bedzie bezpieczne.
    """
    if isinstance(error, FailureDescriptor):
        return _safe_descriptor(error.failure_class, error.error_code)
    declared_class = getattr(error, "failure_class", None)
    declared_code = getattr(error, "error_code", None)
    if declared_class is not None or declared_code is not None:
        return _safe_descriptor(declared_class, declared_code)
    if isinstance(error, sqlite3.OperationalError):
        sqlite_code = getattr(error, "sqlite_errorcode", None)
        primary_code = sqlite_code & 0xFF if type(sqlite_code) is int else None
        if primary_code in {sqlite3.SQLITE_BUSY, sqlite3.SQLITE_LOCKED}:
            return FailureDescriptor(FailureClass.TRANSIENT, "sqlite_busy")
        return FailureDescriptor(FailureClass.PERMANENT, "unexpected_failure")
    if isinstance(error, TimeoutError):
        return FailureDescriptor(FailureClass.TRANSIENT, "timeout")
    if isinstance(error, ConnectionError):
        return FailureDescriptor(
            FailureClass.TRANSIENT, "connection_unavailable"
        )
    if isinstance(error, (json.JSONDecodeError, KeyError, TypeError, ValueError)):
        return FailureDescriptor(FailureClass.PERMANENT, "invalid_payload")
    if isinstance(error, str):
        return FailureDescriptor(FailureClass.PERMANENT, "untyped_failure")
    return FailureDescriptor(FailureClass.PERMANENT, "unexpected_failure")


def sanitize_error(error: Any, limit: int = 2000) -> str:
    """Kompatybilny alias zwracajacy wylacznie bezpieczny kod, nigdy tekst."""
    del limit
    return classify_failure(error).error_code


@dataclass(frozen=True)
class RetryPolicy:
    """Jawna polityka przekazywana przez przyszlego consumera.

    ``attempt_count`` obejmuje probe, ktora wlasnie sie nie udala. Dla
    ``max_attempts=4`` caller musi podac trzy opoznienia: po probie 1, 2 i 3.
    Brak domyslnych wartosci jest zamierzony -- Faza A nie wybiera limitow.
    """

    max_attempts: int
    backoff_seconds: tuple[float, ...]

    def __post_init__(self) -> None:
        if type(self.max_attempts) is not int or self.max_attempts < 1:
            raise ValueError("max_attempts must be an integer >= 1")
        if len(self.backoff_seconds) != self.max_attempts - 1:
            raise ValueError(
                "backoff_seconds must contain exactly max_attempts - 1 values"
            )
        try:
            invalid_delay = any(
                not math.isfinite(float(delay)) or float(delay) < 0
                for delay in self.backoff_seconds
            )
        except (TypeError, ValueError) as exc:
            raise ValueError("backoff delays must be finite numbers >= 0") from exc
        if invalid_delay:
            raise ValueError("backoff delays must be finite numbers >= 0")

    def next_attempt_at(
        self,
        *,
        attempt_count: int,
        failed_at: datetime,
    ) -> Optional[datetime]:
        """Wylicza termin kolejnej proby lub ``None`` po wyczerpaniu limitu."""
        if attempt_count < 1:
            raise ValueError("attempt_count must include the failed attempt")
        if attempt_count >= self.max_attempts:
            return None
        delay = self.backoff_seconds[attempt_count - 1]
        return failed_at + timedelta(seconds=float(delay))


FSM_QUARANTINE_POLICY_ID = "fsm_quarantine_v1"
"""Jawny identyfikator testowej polityki kwarantanny dla enforcement ON."""

FSM_QUARANTINE_POLICY = RetryPolicy(max_attempts=1, backoff_seconds=())
"""Brak retry dla permanent/illegal; nie jest wybrana ani aktywna przy OFF."""


@dataclass(frozen=True)
class RetryPolicyOption:
    """Wersjonowana opcja do decyzji; sama obecność nie wybiera polityki."""

    policy_id: str
    scope: str
    policy: Optional[RetryPolicy]
    provenance: str
    decision_required: bool = True


RETRY_POLICY_OPTIONS: tuple[RetryPolicyOption, ...] = (
    RetryPolicyOption(
        policy_id="manual_hold_v1",
        scope="all_failures",
        policy=RetryPolicy(max_attempts=1, backoff_seconds=()),
        provenance="current_phase_a_no_automatic_retry",
        decision_required=False,
    ),
    RetryPolicyOption(
        policy_id="sqlite_busy_parity_v1",
        scope="sqlite_busy_only",
        policy=RetryPolicy(
            max_attempts=4,
            backoff_seconds=(0.1, 0.5, 2.0),
        ),
        provenance="event_bus_existing_lock_retry_100_500_2000ms",
    ),
    RetryPolicyOption(
        policy_id="business_transient_pending_v1",
        scope="timeout_and_connection_unavailable",
        policy=None,
        provenance="requires_empirical_window_and_business_ack",
    ),
    RetryPolicyOption(
        policy_id=FSM_QUARANTINE_POLICY_ID,
        scope="fsm_permanent_or_illegal_when_enforcement_enabled",
        policy=FSM_QUARANTINE_POLICY,
        provenance="fsm_fail_loud_quarantine_contract",
    ),
)


def policy_options_summary() -> list[dict[str, Any]]:
    """Bezpieczny opis opcji dla operatora/raportu; niczego nie aktywuje."""
    return [
        {
            "policy_id": option.policy_id,
            "scope": option.scope,
            "max_attempts": (
                option.policy.max_attempts if option.policy else None
            ),
            "backoff_seconds": (
                list(option.policy.backoff_seconds) if option.policy else None
            ),
            "provenance": option.provenance,
            "decision_required": option.decision_required,
            "selected": option.policy_id == SELECTED_RETRY_POLICY_ID,
        }
        for option in RETRY_POLICY_OPTIONS
    ]


@dataclass(frozen=True)
class RetryPlan:
    action: str  # retry_scheduled | dead_letter
    attempt_count: int
    next_retry_at: Optional[str]

    @property
    def next_attempt_at(self) -> Optional[str]:
        """Legacy reader alias; kanoniczny zapis to ``next_retry_at``."""
        return self.next_retry_at


@dataclass(frozen=True)
class FailureTransition:
    """Wynik pojedynczej, atomowej obslugi porazki eventu."""

    changed: bool
    status: Optional[str]
    attempt_count: Optional[int]
    next_retry_at: Optional[str]
    failure_class: Optional[str] = None
    error_code: Optional[str] = None


def plan_failure(
    policy: RetryPolicy,
    *,
    previous_attempt_count: int,
    failed_at: datetime,
) -> RetryPlan:
    """Czysty plan jednej porazki; nie czyta ani nie zapisuje bazy."""
    attempt_count = int(previous_attempt_count) + 1
    due = policy.next_attempt_at(attempt_count=attempt_count, failed_at=failed_at)
    if due is None:
        return RetryPlan(DEAD_LETTER_STATUS, attempt_count, None)
    return RetryPlan(RETRY_STATUS, attempt_count, _utc_iso(due))


def schema_columns(conn: sqlite3.Connection) -> set[str]:
    return {str(row[1]) for row in conn.execute("PRAGMA table_info(events)")}


def has_retry_schema(conn: sqlite3.Connection) -> bool:
    return set(RETRY_METADATA_COLUMNS).issubset(schema_columns(conn))


def require_retry_schema(conn: sqlite3.Connection) -> None:
    missing = set(RETRY_METADATA_COLUMNS) - schema_columns(conn)
    if missing:
        raise RuntimeError(
            "events.db retry metadata migration not applied; missing="
            + ",".join(sorted(missing))
        )


def _begin_immediate(conn: sqlite3.Connection) -> None:
    if conn.in_transaction:
        raise RuntimeError("retry helper requires a connection outside a transaction")
    conn.execute("BEGIN IMMEDIATE")


def _finish_noop(
    conn: sqlite3.Connection,
    *,
    status: Optional[str],
    attempt_count: Optional[int],
) -> FailureTransition:
    conn.execute("COMMIT")
    return FailureTransition(False, status, attempt_count, None)


def record_failure(
    conn: sqlite3.Connection,
    event_id: str,
    error: Any,
    *,
    failed_at: datetime,
    expected_status: str = "pending",
    expected_attempt_count: Optional[int] = None,
    policy: Optional[RetryPolicy] = None,
    policy_id: Optional[str] = None,
    enabled: bool = AUTOMATIC_RETRY_ENABLED,
) -> FailureTransition:
    """Atomowo zapisuje jedna porazke i opcjonalnie stosuje jawna polityke.

    Przy domyslnym ``enabled=False`` jedynym dozwolonym przejsciem jest
    ``expected_status -> failed``.  Retry/DLQ wymaga jednoczesnie
    ``enabled=True`` i jawnego :class:`RetryPolicy`; odczyt licznika, plan oraz
    CAS UPDATE odbywaja sie w jednym ``BEGIN IMMEDIATE``.

    ``expected_attempt_count`` jest opcjonalnym tokenem optimistic-lock dla
    callera. Nawet bez niego UPDATE ma CAS na status i count odczytane pod
    blokada transakcji. Staly ``expected_status`` zapobiega clobberowaniu
    eventu, ktory inny worker zdazyl juz przetworzyc lub oznaczyc jako failed.
    """
    descriptor = classify_failure(error)
    if enabled and policy is None:
        raise ValueError("enabled retry/DLQ requires an explicit RetryPolicy")
    if enabled and not str(policy_id or "").strip():
        raise ValueError("enabled retry/DLQ requires an explicit policy_id")
    if expected_status not in {"pending", RETRY_STATUS}:
        raise ValueError(
            "failure transition expected_status must be pending or retry_scheduled"
        )
    if expected_attempt_count is not None and expected_attempt_count < 0:
        raise ValueError("expected_attempt_count must be >= 0")
    require_retry_schema(conn)
    failed_iso = _utc_iso(failed_at)
    _begin_immediate(conn)
    try:
        row = conn.execute(
            "SELECT status, COALESCE(attempt_count, 0) FROM events WHERE event_id=?",
            (event_id,),
        ).fetchone()
        if row is None:
            return _finish_noop(conn, status=None, attempt_count=None)

        current_status = str(row[0])
        current_count = int(row[1])
        if current_status != expected_status:
            return _finish_noop(
                conn, status=current_status, attempt_count=current_count
            )
        if (
            expected_attempt_count is not None
            and current_count != int(expected_attempt_count)
        ):
            return _finish_noop(
                conn, status=current_status, attempt_count=current_count
            )

        next_count = current_count + 1
        if not enabled:
            target_status = "failed"
            next_retry_iso = None
        elif descriptor.terminal:
            target_status = DEAD_LETTER_STATUS
            next_retry_iso = None
        else:
            plan = plan_failure(
                policy,  # type: ignore[arg-type]  # checked above
                previous_attempt_count=current_count,
                failed_at=failed_at,
            )
            target_status = plan.action
            next_retry_iso = plan.next_retry_at

        effective_policy_id = policy_id if enabled else None
        processed_at = failed_iso if target_status != RETRY_STATUS else None
        dead_lettered_at = failed_iso if target_status == DEAD_LETTER_STATUS else None
        cur = conn.execute(
            """UPDATE events
               SET status=?, processed_at=?, attempt_count=?, last_error=?,
                   failure_class=?, error_code=?, last_failed_at=?,
                   next_attempt_at=?, next_retry_at=?,
                   dead_lettered_at=COALESCE(?, dead_lettered_at),
                   retry_policy_id=?
               WHERE event_id=? AND status=?
                 AND COALESCE(attempt_count, 0)=?""",
            (
                target_status,
                processed_at,
                next_count,
                descriptor.error_code,
                descriptor.failure_class.value,
                descriptor.error_code,
                failed_iso,
                next_retry_iso,
                next_retry_iso,
                dead_lettered_at,
                effective_policy_id,
                event_id,
                current_status,
                current_count,
            ),
        )
        if cur.rowcount != 1:
            conn.execute("ROLLBACK")
            return FailureTransition(False, current_status, current_count, None)
        conn.execute("COMMIT")
        return FailureTransition(
            True,
            target_status,
            next_count,
            next_retry_iso,
            descriptor.failure_class.value,
            descriptor.error_code,
        )
    except Exception:
        if conn.in_transaction:
            conn.execute("ROLLBACK")
        raise


def record_failed_attempt(
    conn: sqlite3.Connection,
    event_id: str,
    error: Any,
    *,
    failed_at: datetime,
    expected_status: str = "pending",
    expected_attempt_count: Optional[int] = None,
) -> bool:
    """Utrwala diagnoze porazki bez planowania kolejnej proby.

    To jedyny helper bez bramki ``enabled``: zapis metadanych nie zmienia
    decyzji biznesowej ani nie powoduje ponownego wykonania eventu.
    """
    return record_failure(
        conn,
        event_id,
        error,
        failed_at=failed_at,
        expected_status=expected_status,
        expected_attempt_count=expected_attempt_count,
        enabled=False,
    ).changed


def schedule_retry(
    conn: sqlite3.Connection,
    event_id: str,
    *,
    expected_attempt_count: int,
    next_attempt_at: datetime,
    enabled: bool = AUTOMATIC_RETRY_ENABLED,
) -> bool:
    """Planuje juz zapisana porazke; CAS dopuszcza tylko ``failed``.

    Helper nie nalicza nowej proby i nie moze wskrzesic ``processed``,
    ``broadcast`` ani ``dead_letter``. Default ``enabled=False`` jest no-op.
    """
    if not enabled:
        return False
    if expected_attempt_count < 1:
        raise ValueError("expected_attempt_count must be >= 1")
    require_retry_schema(conn)
    due_iso = _utc_iso(next_attempt_at)
    _begin_immediate(conn)
    try:
        cur = conn.execute(
            """UPDATE events
               SET status=?, processed_at=NULL, next_attempt_at=?, next_retry_at=?
               WHERE event_id=? AND status='failed'
                 AND COALESCE(attempt_count, 0)=?
                 AND failure_class=?""",
            (
                RETRY_STATUS,
                due_iso,
                due_iso,
                event_id,
                int(expected_attempt_count),
                FailureClass.TRANSIENT.value,
            ),
        )
        conn.execute("COMMIT")
        return cur.rowcount == 1
    except Exception:
        conn.execute("ROLLBACK")
        raise


def move_to_dead_letter(
    conn: sqlite3.Connection,
    event_id: str,
    *,
    expected_attempt_count: int,
    dead_lettered_at: datetime,
) -> bool:
    """Izoluje zapisane ``failed`` przez CAS, zachowujac czas porazki."""
    if expected_attempt_count < 1:
        raise ValueError("expected_attempt_count must be >= 1")
    require_retry_schema(conn)
    at = _utc_iso(dead_lettered_at)
    _begin_immediate(conn)
    try:
        cur = conn.execute(
            """UPDATE events
               SET status=?, next_attempt_at=NULL, next_retry_at=NULL,
                   dead_lettered_at=?
               WHERE event_id=? AND status='failed'
                 AND COALESCE(attempt_count, 0)=?""",
            (
                DEAD_LETTER_STATUS,
                at,
                event_id,
                int(expected_attempt_count),
            ),
        )
        conn.execute("COMMIT")
        return cur.rowcount == 1
    except Exception:
        conn.execute("ROLLBACK")
        raise


def requeue_dead_letter(
    conn: sqlite3.Connection,
    event_id: str,
    *,
    reset_attempt_count: bool,
    reason: str,
    replayed_at: datetime,
    enabled: bool = False,
) -> bool:
    """Przenosi DLQ z powrotem do ``pending`` tylko po jawnym opt-in.

    Caller musi rowniez jawnie zdecydowac, czy resetuje budzet prob. Diagnoza
    ``last_error`` i ``dead_lettered_at`` zostaje jako slad audytowy.
    """
    if not enabled:
        return False
    reason_code = str(reason or "").strip()
    if reason_code not in SAFE_REPLAY_REASON_CODES:
        raise ValueError(
            "replay reason must be one of: "
            + ",".join(sorted(SAFE_REPLAY_REASON_CODES))
        )
    require_retry_schema(conn)
    attempt_expr = "0" if reset_attempt_count else "attempt_count"
    replayed_iso = _utc_iso(replayed_at)
    _begin_immediate(conn)
    try:
        cur = conn.execute(
            f"""UPDATE events
                SET status='pending', processed_at=NULL, next_attempt_at=NULL,
                    next_retry_at=NULL,
                    replay_count=COALESCE(replay_count, 0) + 1,
                    last_replayed_at=?, last_replay_reason=?,
                    attempt_count={attempt_expr}
                WHERE event_id=? AND status=?""",
            (
                replayed_iso,
                reason_code,
                event_id,
                DEAD_LETTER_STATUS,
            ),
        )
        conn.execute("COMMIT")
        return cur.rowcount == 1
    except Exception:
        conn.execute("ROLLBACK")
        raise


def mark_effect_applied(
    conn: sqlite3.Connection,
    event_id: str,
    *,
    applied_at: datetime,
) -> bool:
    """Idempotentny marker po atomowym efekcie state; nie zmienia queue status."""
    require_retry_schema(conn)
    applied_iso = _utc_iso(applied_at)
    _begin_immediate(conn)
    try:
        cur = conn.execute(
            """UPDATE events
               SET effect_applied_at=COALESCE(effect_applied_at, ?)
               WHERE event_id=?""",
            (applied_iso, event_id),
        )
        conn.execute("COMMIT")
        return cur.rowcount == 1
    except Exception:
        conn.execute("ROLLBACK")
        raise


def mark_replay_processed(
    conn: sqlite3.Connection,
    event_id: str,
    *,
    processed_at: datetime,
) -> bool:
    """Legacy replay adapter: jeden owner statusu zamiast surowego SQL w toolu."""
    processed_iso = _utc_iso(processed_at)
    _begin_immediate(conn)
    try:
        has_processed_history = conn.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type='table' AND name='processed_events'"""
        ).fetchone() is not None
        columns = schema_columns(conn)
        if {"next_attempt_at", "next_retry_at"} <= columns:
            cur = conn.execute(
                """UPDATE events
                   SET status='processed', processed_at=?,
                       next_attempt_at=NULL, next_retry_at=NULL
                   WHERE event_id=? AND status='failed'""",
                (processed_iso, event_id),
            )
        else:
            cur = conn.execute(
                """UPDATE events SET status='processed', processed_at=?
                   WHERE event_id=? AND status='failed'""",
                (processed_iso, event_id),
            )
        if cur.rowcount == 1 and has_processed_history:
            conn.execute(
                """INSERT OR IGNORE INTO processed_events(event_id, processed_at)
                   VALUES (?, ?)""",
                (event_id, processed_iso),
            )
        conn.execute("COMMIT")
        return cur.rowcount == 1
    except Exception:
        conn.execute("ROLLBACK")
        raise


def mark_replays_processed(
    conn: sqlite3.Connection,
    event_ids: Sequence[str],
    *,
    processed_at: datetime,
) -> int:
    """Atomowy batch adapter dla legacy ``replay_failed --apply``."""
    processed_iso = _utc_iso(processed_at)
    _begin_immediate(conn)
    flipped = 0
    try:
        has_processed_history = conn.execute(
            """SELECT 1 FROM sqlite_master
               WHERE type='table' AND name='processed_events'"""
        ).fetchone() is not None
        for event_id in event_ids:
            columns = schema_columns(conn)
            if {"next_attempt_at", "next_retry_at"} <= columns:
                cur = conn.execute(
                    """UPDATE events
                       SET status='processed', processed_at=?,
                           next_attempt_at=NULL, next_retry_at=NULL
                       WHERE event_id=? AND status='failed'""",
                    (processed_iso, event_id),
                )
            else:
                cur = conn.execute(
                    """UPDATE events SET status='processed', processed_at=?
                       WHERE event_id=? AND status='failed'""",
                    (processed_iso, event_id),
                )
            if cur.rowcount == 1:
                flipped += 1
                if has_processed_history:
                    conn.execute(
                        """INSERT OR IGNORE INTO processed_events
                           (event_id, processed_at) VALUES (?, ?)""",
                        (event_id, processed_iso),
                    )
        conn.execute("COMMIT")
        return flipped
    except Exception:
        conn.execute("ROLLBACK")
        raise


def due_retry_events(
    conn: sqlite3.Connection,
    *,
    now: datetime,
    limit: int = 100,
    event_types: Optional[Sequence[str]] = None,
) -> list[dict[str, Any]]:
    """Read-only lista due retry w stabilnej kolejnosci ``created_at,event_id``."""
    require_retry_schema(conn)
    if limit < 1:
        return []
    params: list[Any] = [RETRY_STATUS, _utc_iso(now)]
    type_clause = ""
    if event_types:
        placeholders = ",".join("?" for _ in event_types)
        type_clause = f" AND event_type IN ({placeholders})"
        params.extend(str(t) for t in event_types)
    params.append(int(limit))
    rows = conn.execute(
        """SELECT event_id, event_type, order_id, courier_id, payload,
                  created_at, processed_at, status, attempt_count, last_error,
                  failure_class, error_code, next_attempt_at, next_retry_at,
                  last_failed_at, dead_lettered_at, replay_count,
                  last_replayed_at, last_replay_reason, idempotency_key,
                  effect_applied_at, retry_policy_id
           FROM events
           WHERE status=? AND next_retry_at<=?"""
        + type_clause
        + " ORDER BY created_at ASC, event_id ASC LIMIT ?",
        tuple(params),
    ).fetchall()
    result = []
    for row in rows:
        record = dict(row) if isinstance(row, sqlite3.Row) else {
            key: row[index]
            for index, key in enumerate(
                (
                    "event_id", "event_type", "order_id", "courier_id", "payload",
                    "created_at", "processed_at", "status", "attempt_count",
                    "last_error", "failure_class", "error_code",
                    "next_attempt_at", "next_retry_at", "last_failed_at",
                    "dead_lettered_at", "replay_count", "last_replayed_at",
                    "last_replay_reason", "idempotency_key",
                    "effect_applied_at", "retry_policy_id",
                )
            )
        }
        try:
            record["payload"] = json.loads(record.get("payload") or "{}")
        except (TypeError, ValueError):
            pass
        result.append(record)
    return result


def queue_retry_stats(
    conn: sqlite3.Connection,
    *,
    now: datetime,
) -> dict[str, Any]:
    """Read-only metryki Fazy A, w tym wiek najstarszego retry/DLQ."""
    require_retry_schema(conn)
    by_status = {
        str(row[0]): int(row[1])
        for row in conn.execute(
            """SELECT status, COUNT(*) FROM events
               WHERE status IN (?, ?) GROUP BY status""",
            (RETRY_STATUS, DEAD_LETTER_STATUS),
        ).fetchall()
    }
    oldest_retry = conn.execute(
        "SELECT MIN(created_at), MIN(next_retry_at) FROM events WHERE status=?",
        (RETRY_STATUS,),
    ).fetchone()
    oldest_dlq = conn.execute(
        "SELECT MIN(created_at), MIN(dead_lettered_at) FROM events WHERE status=?",
        (DEAD_LETTER_STATUS,),
    ).fetchone()
    now_utc = now if now.tzinfo else now.replace(tzinfo=timezone.utc)
    now_utc = now_utc.astimezone(timezone.utc)

    def _age(value: Optional[str]) -> Optional[float]:
        parsed = _parse_iso(value)
        if parsed is None:
            return None
        return max(0.0, (now_utc - parsed).total_seconds())

    return {
        "retry_scheduled": by_status.get(RETRY_STATUS, 0),
        "dead_letter": by_status.get(DEAD_LETTER_STATUS, 0),
        "oldest_retry_age_seconds": _age(oldest_retry[0] if oldest_retry else None),
        "oldest_retry_due_at": oldest_retry[1] if oldest_retry else None,
        "oldest_dead_letter_age_seconds": _age(oldest_dlq[0] if oldest_dlq else None),
        "oldest_dead_lettered_at": oldest_dlq[1] if oldest_dlq else None,
    }
