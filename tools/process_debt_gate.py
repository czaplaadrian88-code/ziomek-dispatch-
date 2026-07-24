#!/usr/bin/env python3
"""Kanoniczny, mechaniczny rejestr długu procesowego Ziomka.

To jest jedyny moduł, który otwiera bazę SQLite. Pozostałe narzędzia używają
klasy :class:`GateStore`; dzięki temu nie obchodzą walidacji, CAS ani audytu.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sqlite3
import stat
import sys
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator, Mapping, Sequence


DEFAULT_DB = Path(
    os.environ.get(
        "ZIOMEK_PROCESS_GATE_DB",
        "/var/lib/ziomek-process-gates/gates.sqlite3",
    )
)

MAIN_STATES = (
    "BUILT_OFF",
    "WAIT_DATA",
    "READY_FOR_REVIEW",
    "READY_FOR_OWNER",
    "OWNER_ACKED",
    "APPLIED",
    "VERIFIED",
    "CLOSED",
)
TERMINAL_STATES = frozenset({"CLOSED", "REJECTED", "SUPERSEDED"})
ALL_STATES = MAIN_STATES + ("REJECTED", "SUPERSEDED")
ACTIVE_STATES = frozenset(ALL_STATES) - TERMINAL_STATES

ALLOWED_TRANSITIONS: dict[str, frozenset[str]] = {
    state: frozenset({MAIN_STATES[index + 1], "REJECTED", "SUPERSEDED"})
    for index, state in enumerate(MAIN_STATES[:-1])
}
ALLOWED_TRANSITIONS["CLOSED"] = frozenset()
ALLOWED_TRANSITIONS["REJECTED"] = frozenset()
ALLOWED_TRANSITIONS["SUPERSEDED"] = frozenset()

AT_ACTIVE_STATUSES = frozenset({"SUBMITTING", "SCHEDULED", "MISSING_ALARM"})
AT_TERMINAL_STATUSES = frozenset(
    {"SUCCEEDED", "FAILED", "SUBMISSION_FAILED", "CANCELLED"}
)

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{2,127}$")
_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class GateError(RuntimeError):
    """Błąd kontraktu rejestru."""


class GateNotFound(GateError):
    """Rekord nie istnieje."""


class GateAlreadyExists(GateError):
    """Rekord o tym identyfikatorze już istnieje."""


class IllegalTransition(GateError):
    """Przejście łamie automat stanów."""


class CASConflict(GateError):
    """Wersja rekordu zmieniła się od czasu odczytu."""


class ValidationError(GateError):
    """Dane wejściowe nie spełniają kontraktu."""


def utc_now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso_utc(value: datetime) -> str:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValidationError("czas musi zawierać strefę czasową")
    return value.astimezone(timezone.utc).replace(microsecond=0).isoformat().replace(
        "+00:00", "Z"
    )


def parse_timestamp(value: str, field: str = "timestamp") -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValidationError(f"{field}: niepoprawny ISO-8601: {value!r}") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValidationError(f"{field}: wymagana strefa czasowa")
    return parsed.astimezone(timezone.utc)


def canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_json(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _required_text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValidationError(f"{field}: wymagana niepusta wartość")
    if "\x00" in value:
        raise ValidationError(f"{field}: niedozwolony znak NUL")
    return value.strip()


def _validate_gate_id(value: str) -> str:
    value = _required_text(value, "gate_id")
    if not _ID_RE.fullmatch(value):
        raise ValidationError(
            "gate_id: dozwolone 3-128 znaków [a-z0-9._:-], pierwszy alfanumeryczny"
        )
    return value


def _validate_code_sha(value: str) -> str:
    value = _required_text(value, "code_sha").lower()
    if not _SHA_RE.fullmatch(value):
        raise ValidationError("code_sha: wymagany pełny SHA-1 albo SHA-256")
    return value


def _validate_evidence_hash(value: str) -> str:
    value = _required_text(value, "evidence_hash").lower()
    if not _HASH_RE.fullmatch(value):
        raise ValidationError("evidence_hash: wymagany SHA-256 (64 znaki hex)")
    return value


def _metadata_json(value: Mapping[str, Any] | None) -> str:
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise ValidationError("metadata musi być obiektem JSON")
    return canonical_json(dict(value))


SCHEMA = f"""
CREATE TABLE IF NOT EXISTS gates (
    gate_id TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    kind TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ({','.join(repr(s) for s in ALL_STATES)})),
    owner TEXT NOT NULL,
    due_at TEXT NOT NULL,
    next_step TEXT NOT NULL,
    blocker TEXT NOT NULL,
    code_sha TEXT NOT NULL,
    evidence_hash TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    alarm INTEGER NOT NULL DEFAULT 0 CHECK (alarm IN (0, 1)),
    alarm_reason TEXT NOT NULL DEFAULT '',
    opened_at TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    closed_at TEXT,
    metadata_json TEXT NOT NULL DEFAULT '{{}}'
);

CREATE INDEX IF NOT EXISTS gates_open_order
ON gates(state, opened_at, gate_id);

CREATE TABLE IF NOT EXISTS gate_events (
    event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    gate_id TEXT NOT NULL REFERENCES gates(gate_id),
    from_state TEXT,
    to_state TEXT NOT NULL,
    expected_version INTEGER,
    result_version INTEGER NOT NULL,
    actor TEXT NOT NULL,
    reason TEXT NOT NULL,
    occurred_at TEXT NOT NULL,
    snapshot_json TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS gate_events_gate
ON gate_events(gate_id, event_id);

CREATE TABLE IF NOT EXISTS at_jobs (
    job_key TEXT PRIMARY KEY,
    gate_id TEXT NOT NULL REFERENCES gates(gate_id),
    at_job_id TEXT,
    status TEXT NOT NULL CHECK (status IN (
        'SUBMITTING', 'SCHEDULED', 'MISSING_ALARM', 'SUCCEEDED', 'FAILED',
        'SUBMISSION_FAILED', 'CANCELLED'
    )),
    scheduled_for TEXT NOT NULL,
    command_json TEXT NOT NULL,
    runner_token_hash TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    last_seen_at TEXT,
    finished_at TEXT,
    exit_code INTEGER,
    result_evidence_hash TEXT,
    reconcile_note TEXT NOT NULL DEFAULT ''
);

CREATE UNIQUE INDEX IF NOT EXISTS at_jobs_queue_id
ON at_jobs(at_job_id) WHERE at_job_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS at_jobs_one_active_per_gate
ON at_jobs(gate_id) WHERE status IN ('SUBMITTING', 'SCHEDULED', 'MISSING_ALARM');
"""

REQUIRED_COLUMNS = {
    "gates": {
        "gate_id", "title", "kind", "state", "owner", "due_at", "next_step",
        "blocker", "code_sha", "evidence_hash", "version", "alarm",
        "alarm_reason", "opened_at", "created_at", "updated_at", "closed_at",
        "metadata_json",
    },
    "gate_events": {
        "event_id", "gate_id", "from_state", "to_state", "expected_version",
        "result_version", "actor", "reason", "occurred_at", "snapshot_json",
    },
    "at_jobs": {
        "job_key", "gate_id", "at_job_id", "status", "scheduled_for",
        "command_json", "runner_token_hash", "created_at", "updated_at",
        "last_seen_at", "finished_at", "exit_code", "result_evidence_hash",
        "reconcile_note",
    },
}


class GateStore:
    """Jedyny interfejs zapisu i odczytu kanonicznej bazy."""

    def __init__(self, db_path: str | os.PathLike[str] = DEFAULT_DB):
        self.db_path = Path(db_path).expanduser()

    def initialize(self) -> None:
        parent = self.db_path.parent
        if not parent.exists():
            parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        if self.db_path.is_symlink():
            raise GateError(f"baza nie może być symlinkiem: {self.db_path}")
        try:
            descriptor = os.open(
                self.db_path,
                os.O_CREAT | os.O_EXCL | os.O_RDWR,
                0o600,
            )
        except FileExistsError:
            descriptor = None
        if descriptor is not None:
            os.close(descriptor)
        try:
            mode = self.db_path.stat().st_mode
        except OSError as exc:
            raise GateError(f"nie można sprawdzić bazy {self.db_path}: {exc}") from exc
        if not stat.S_ISREG(mode):
            raise GateError(f"baza nie jest zwykłym plikiem: {self.db_path}")
        with self._write_connection() as connection:
            version = int(connection.execute("PRAGMA user_version").fetchone()[0])
            # Wersja 2 = kanoniczny żywy ledger po hardeningu auth at-jobów (dodatkowe
            # kolumny at_jobs). Tabele gates/gate_events są wersjonowo stabilne (identyczne
            # w 1 i 2), więc transition bramek działa poprawnie na obu.
            if version not in (0, 1, 2):
                raise GateError(
                    f"nieobsługiwana wersja schematu SQLite: {version}; oczekiwano 0, 1 albo 2"
                )
            existing_tables = {
                str(row[0])
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                ).fetchall()
            }
            if version == 0 and existing_tables.intersection(REQUIRED_COLUMNS):
                raise GateError(
                    "odmowa przejęcia niewersjonowanej bazy zawierającej tabele kanoniczne"
                )
            try:
                connection.executescript(SCHEMA)
            except sqlite3.Error as exc:
                raise GateError(f"niezgodny schemat SQLite: {exc}") from exc
            for table, expected in REQUIRED_COLUMNS.items():
                actual = {
                    str(row[1])
                    for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
                }
                missing = sorted(expected - actual)
                if missing:
                    raise GateError(
                        f"niezgodny schemat {table}; brak kolumn: {', '.join(missing)}"
                    )
            # Nigdy nie degraduj kanonicznej wersji: v2 (auth at-jobów) MUSI przetrwać,
            # inaczej at_gate traci kontrakt sealed-payload. Świeża/legacy baza = 1.
            # Atomowo pod blokadą zapisu (BEGIN IMMEDIATE + re-read) — zamyka wyścig z
            # równoległą migracją 1→2 (Sol cross-check 2026-07-24): bez tego odczyt sprzed
            # cudzej migracji mógłby ją nadpisać z powrotem w dół.
            connection.execute("BEGIN IMMEDIATE")
            current = int(connection.execute("PRAGMA user_version").fetchone()[0])
            connection.execute(f"PRAGMA user_version = {current if current >= 1 else 1}")
            connection.execute("COMMIT")
        try:
            os.chmod(self.db_path, 0o600)
        except PermissionError:
            pass

    @contextmanager
    def _write_connection(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self.db_path, timeout=15.0, isolation_level=None)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute("PRAGMA journal_mode = WAL")
        connection.execute("PRAGMA synchronous = FULL")
        connection.execute("PRAGMA busy_timeout = 15000")
        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def _read_connection(self) -> Iterator[sqlite3.Connection]:
        if not self.db_path.is_file():
            raise GateNotFound(f"baza nie istnieje: {self.db_path}")
        uri = self.db_path.resolve().as_uri() + "?mode=ro"
        connection = sqlite3.connect(uri, uri=True, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA query_only = ON")
        try:
            yield connection
        finally:
            connection.close()

    @staticmethod
    def _row_to_gate(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["alarm"] = bool(result["alarm"])
        result["metadata"] = json.loads(result.pop("metadata_json"))
        return result

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["command"] = json.loads(result.pop("command_json"))
        result.pop("runner_token_hash", None)
        return result

    @staticmethod
    def _event_snapshot(row: Mapping[str, Any]) -> str:
        snapshot = dict(row)
        if "metadata_json" in snapshot:
            snapshot["metadata"] = json.loads(snapshot.pop("metadata_json"))
        snapshot["alarm"] = bool(snapshot.get("alarm", False))
        return canonical_json(snapshot)

    def add_gate(
        self,
        *,
        gate_id: str,
        title: str,
        kind: str,
        owner: str,
        due_at: str,
        next_step: str,
        blocker: str,
        code_sha: str,
        evidence_hash: str,
        opened_at: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        actor: str = "process_debt_gate/add",
        reason: str = "utworzenie rekordu",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        gate_id = _validate_gate_id(gate_id)
        title = _required_text(title, "title")
        kind = _required_text(kind, "kind")
        owner = _required_text(owner, "owner")
        due_at = iso_utc(parse_timestamp(due_at, "due_at"))
        next_step = _required_text(next_step, "next_step")
        blocker = _required_text(blocker, "blocker")
        code_sha = _validate_code_sha(code_sha)
        evidence_hash = _validate_evidence_hash(evidence_hash)
        actor = _required_text(actor, "actor")
        reason = _required_text(reason, "reason")
        timestamp = iso_utc(now or utc_now())
        opened = iso_utc(parse_timestamp(opened_at, "opened_at")) if opened_at else timestamp
        metadata_value = _metadata_json(metadata)

        self.initialize()
        try:
            with self._write_connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    INSERT INTO gates (
                        gate_id, title, kind, state, owner, due_at, next_step,
                        blocker, code_sha, evidence_hash, version, alarm,
                        alarm_reason, opened_at, created_at, updated_at,
                        closed_at, metadata_json
                    ) VALUES (?, ?, ?, 'BUILT_OFF', ?, ?, ?, ?, ?, ?, 1, 0, '', ?, ?, ?, NULL, ?)
                    """,
                    (
                        gate_id,
                        title,
                        kind,
                        owner,
                        due_at,
                        next_step,
                        blocker,
                        code_sha,
                        evidence_hash,
                        opened,
                        timestamp,
                        timestamp,
                        metadata_value,
                    ),
                )
                row = connection.execute(
                    "SELECT * FROM gates WHERE gate_id = ?", (gate_id,)
                ).fetchone()
                assert row is not None
                connection.execute(
                    """
                    INSERT INTO gate_events (
                        gate_id, from_state, to_state, expected_version,
                        result_version, actor, reason, occurred_at, snapshot_json
                    ) VALUES (?, NULL, 'BUILT_OFF', NULL, 1, ?, ?, ?, ?)
                    """,
                    (gate_id, actor, reason, timestamp, self._event_snapshot(row)),
                )
                connection.commit()
        except sqlite3.IntegrityError as exc:
            if "gates.gate_id" in str(exc) or "UNIQUE constraint failed: gates.gate_id" in str(exc):
                raise GateAlreadyExists(f"rekord już istnieje: {gate_id}") from exc
            raise GateError(f"nie udało się dodać rekordu: {exc}") from exc
        return self.show_gate(gate_id)

    def transition(
        self,
        gate_id: str,
        to_state: str,
        *,
        expected_version: int,
        actor: str,
        reason: str,
        owner: str | None = None,
        due_at: str | None = None,
        next_step: str | None = None,
        blocker: str | None = None,
        code_sha: str | None = None,
        evidence_hash: str | None = None,
        metadata: Mapping[str, Any] | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        gate_id = _validate_gate_id(gate_id)
        to_state = _required_text(to_state, "to_state").upper()
        if to_state not in ALL_STATES:
            raise ValidationError(f"nieznany stan: {to_state}")
        if not isinstance(expected_version, int) or expected_version < 1:
            raise ValidationError("expected_version musi być dodatnią liczbą całkowitą")
        actor = _required_text(actor, "actor")
        reason = _required_text(reason, "reason")
        timestamp = iso_utc(now or utc_now())

        self.initialize()
        with self._write_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT * FROM gates WHERE gate_id = ?", (gate_id,)
            ).fetchone()
            if row is None:
                connection.rollback()
                raise GateNotFound(f"brak rekordu: {gate_id}")
            current_state = str(row["state"])
            if int(row["version"]) != expected_version:
                connection.rollback()
                raise CASConflict(
                    f"CAS konflikt {gate_id}: oczekiwano v{expected_version}, "
                    f"jest v{row['version']}"
                )
            if to_state not in ALLOWED_TRANSITIONS[current_state]:
                connection.rollback()
                allowed = ", ".join(sorted(ALLOWED_TRANSITIONS[current_state])) or "brak"
                raise IllegalTransition(
                    f"niedozwolone {current_state} -> {to_state}; dozwolone: {allowed}"
                )

            updates: dict[str, Any] = {
                "owner": _required_text(owner, "owner") if owner is not None else row["owner"],
                "due_at": (
                    iso_utc(parse_timestamp(due_at, "due_at"))
                    if due_at is not None
                    else row["due_at"]
                ),
                "next_step": (
                    _required_text(next_step, "next_step")
                    if next_step is not None
                    else row["next_step"]
                ),
                "blocker": (
                    _required_text(blocker, "blocker")
                    if blocker is not None
                    else row["blocker"]
                ),
                "code_sha": (
                    _validate_code_sha(code_sha) if code_sha is not None else row["code_sha"]
                ),
                "evidence_hash": (
                    _validate_evidence_hash(evidence_hash)
                    if evidence_hash is not None
                    else row["evidence_hash"]
                ),
                "metadata_json": (
                    _metadata_json(metadata) if metadata is not None else row["metadata_json"]
                ),
            }
            closed_at = timestamp if to_state in TERMINAL_STATES else None
            cursor = connection.execute(
                """
                UPDATE gates
                SET state = ?, owner = ?, due_at = ?, next_step = ?, blocker = ?,
                    code_sha = ?, evidence_hash = ?, metadata_json = ?,
                    version = version + 1, updated_at = ?, closed_at = ?,
                    alarm = CASE WHEN ? IN ('CLOSED', 'REJECTED', 'SUPERSEDED') THEN 0 ELSE alarm END,
                    alarm_reason = CASE WHEN ? IN ('CLOSED', 'REJECTED', 'SUPERSEDED') THEN '' ELSE alarm_reason END
                WHERE gate_id = ? AND version = ?
                """,
                (
                    to_state,
                    updates["owner"],
                    updates["due_at"],
                    updates["next_step"],
                    updates["blocker"],
                    updates["code_sha"],
                    updates["evidence_hash"],
                    updates["metadata_json"],
                    timestamp,
                    closed_at,
                    to_state,
                    to_state,
                    gate_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise CASConflict(f"CAS konflikt podczas zapisu: {gate_id}")
            updated = connection.execute(
                "SELECT * FROM gates WHERE gate_id = ?", (gate_id,)
            ).fetchone()
            assert updated is not None
            connection.execute(
                """
                INSERT INTO gate_events (
                    gate_id, from_state, to_state, expected_version,
                    result_version, actor, reason, occurred_at, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    gate_id,
                    current_state,
                    to_state,
                    expected_version,
                    int(updated["version"]),
                    actor,
                    reason,
                    timestamp,
                    self._event_snapshot(updated),
                ),
            )
            connection.commit()
        return self.show_gate(gate_id)

    def show_gate(self, gate_id: str) -> dict[str, Any]:
        gate_id = _validate_gate_id(gate_id)
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM gates WHERE gate_id = ?", (gate_id,)
            ).fetchone()
            if row is None:
                raise GateNotFound(f"brak rekordu: {gate_id}")
            gate = self._row_to_gate(row)
            events = connection.execute(
                """
                SELECT event_id, from_state, to_state, expected_version,
                       result_version, actor, reason, occurred_at
                FROM gate_events WHERE gate_id = ? ORDER BY event_id
                """,
                (gate_id,),
            ).fetchall()
        gate["events"] = [dict(event) for event in events]
        return gate

    def list_gates(
        self,
        *,
        states: Iterable[str] | None = None,
        owner: str | None = None,
        alarm_only: bool = False,
        include_terminal: bool = True,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        if not self.db_path.is_file():
            return []
        clauses: list[str] = []
        parameters: list[Any] = []
        if states:
            normalized = [str(state).upper() for state in states]
            unknown = sorted(set(normalized) - set(ALL_STATES))
            if unknown:
                raise ValidationError(f"nieznane stany: {', '.join(unknown)}")
            clauses.append("state IN (" + ",".join("?" for _ in normalized) + ")")
            parameters.extend(normalized)
        elif not include_terminal:
            clauses.append("state NOT IN ('CLOSED', 'REJECTED', 'SUPERSEDED')")
        if owner is not None:
            clauses.append("owner = ?")
            parameters.append(_required_text(owner, "owner"))
        if alarm_only:
            clauses.append("alarm = 1")
        query = "SELECT * FROM gates"
        if clauses:
            query += " WHERE " + " AND ".join(clauses)
        query += " ORDER BY opened_at ASC, gate_id ASC"
        if limit is not None:
            if limit < 1:
                raise ValidationError("limit musi być dodatni")
            query += " LIMIT ?"
            parameters.append(limit)
        with self._read_connection() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._row_to_gate(row) for row in rows]

    def register_at_intent(
        self,
        *,
        gate_id: str,
        job_key: str,
        runner_token_hash: str,
        scheduled_for: str,
        command: Sequence[str],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        gate_id = _validate_gate_id(gate_id)
        job_key = _required_text(job_key, "job_key")
        runner_token_hash = _validate_evidence_hash(runner_token_hash)
        scheduled_for = iso_utc(parse_timestamp(scheduled_for, "scheduled_for"))
        if not command or any(not isinstance(part, str) or not part for part in command):
            raise ValidationError("command musi być niepustą listą argumentów")
        timestamp = iso_utc(now or utc_now())
        self.initialize()
        try:
            with self._write_connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                gate = connection.execute(
                    "SELECT state FROM gates WHERE gate_id = ?", (gate_id,)
                ).fetchone()
                if gate is None:
                    connection.rollback()
                    raise GateNotFound(f"brak rekordu: {gate_id}")
                if gate["state"] != "BUILT_OFF":
                    connection.rollback()
                    raise IllegalTransition(
                        "zadanie at można rejestrować tylko dla bramki BUILT_OFF"
                    )
                connection.execute(
                    """
                    INSERT INTO at_jobs (
                        job_key, gate_id, at_job_id, status, scheduled_for,
                        command_json, runner_token_hash, created_at, updated_at
                    ) VALUES (?, ?, NULL, 'SUBMITTING', ?, ?, ?, ?, ?)
                    """,
                    (
                        job_key,
                        gate_id,
                        scheduled_for,
                        canonical_json(
                            {
                                "argv_sha256": sha256_json(list(command)),
                                "argc": len(command),
                            }
                        ),
                        runner_token_hash,
                        timestamp,
                        timestamp,
                    ),
                )
                connection.commit()
        except sqlite3.IntegrityError as exc:
            raise GateError(f"nie udało się zarejestrować intencji at: {exc}") from exc
        return self.show_at_job(job_key)

    def confirm_at_job(
        self,
        job_key: str,
        at_job_id: str,
        *,
        actor: str = "at_gate/schedule",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        job_key = _required_text(job_key, "job_key")
        at_job_id = _required_text(at_job_id, "at_job_id")
        if not at_job_id.isdigit():
            raise ValidationError("at_job_id musi być liczbą")
        actor = _required_text(actor, "actor")
        timestamp = iso_utc(now or utc_now())
        self.initialize()
        with self._write_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            job = connection.execute(
                "SELECT * FROM at_jobs WHERE job_key = ?", (job_key,)
            ).fetchone()
            if job is None:
                connection.rollback()
                raise GateNotFound(f"brak at job: {job_key}")
            if job["status"] != "SUBMITTING":
                connection.rollback()
                raise GateError(f"at job {job_key} nie jest w stanie SUBMITTING")
            gate = connection.execute(
                "SELECT * FROM gates WHERE gate_id = ?", (job["gate_id"],)
            ).fetchone()
            assert gate is not None
            if gate["state"] != "BUILT_OFF":
                connection.rollback()
                raise IllegalTransition(
                    f"potwierdzenie at wymaga BUILT_OFF, jest {gate['state']}"
                )
            gate_version = int(gate["version"])
            connection.execute(
                """
                UPDATE at_jobs SET at_job_id = ?, status = 'SCHEDULED',
                    updated_at = ?, last_seen_at = ? WHERE job_key = ?
                """,
                (at_job_id, timestamp, timestamp, job_key),
            )
            cursor = connection.execute(
                """
                UPDATE gates SET state = 'WAIT_DATA', version = version + 1,
                    updated_at = ?, next_step = 'Poczekaj na wykonanie zarejestrowanego at-joba',
                    blocker = 'Oczekiwanie na at-job #' || ?, alarm = 0,
                    alarm_reason = ''
                WHERE gate_id = ? AND version = ?
                """,
                (timestamp, at_job_id, job["gate_id"], gate_version),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise CASConflict(f"CAS konflikt bramki {job['gate_id']}")
            updated = connection.execute(
                "SELECT * FROM gates WHERE gate_id = ?", (job["gate_id"],)
            ).fetchone()
            assert updated is not None
            connection.execute(
                """
                INSERT INTO gate_events (
                    gate_id, from_state, to_state, expected_version,
                    result_version, actor, reason, occurred_at, snapshot_json
                ) VALUES (?, 'BUILT_OFF', 'WAIT_DATA', ?, ?, ?, ?, ?, ?)
                """,
                (
                    job["gate_id"],
                    gate_version,
                    int(updated["version"]),
                    actor,
                    f"at-job #{at_job_id} zarejestrowany",
                    timestamp,
                    self._event_snapshot(updated),
                ),
            )
            connection.commit()
        return self.show_at_job(job_key)

    def fail_at_submission(
        self,
        job_key: str,
        reason: str,
        *,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        job_key = _required_text(job_key, "job_key")
        reason = _required_text(reason, "reason")
        timestamp = iso_utc(now or utc_now())
        self.initialize()
        with self._write_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            job = connection.execute(
                "SELECT * FROM at_jobs WHERE job_key = ?", (job_key,)
            ).fetchone()
            if job is None:
                connection.rollback()
                raise GateNotFound(f"brak at job: {job_key}")
            if job["status"] != "SUBMITTING":
                connection.rollback()
                raise GateError(f"nie można oznaczyć {job['status']} jako błąd wysyłki")
            gate = connection.execute(
                "SELECT * FROM gates WHERE gate_id = ?", (job["gate_id"],)
            ).fetchone()
            assert gate is not None
            gate_version = int(gate["version"])
            connection.execute(
                """
                UPDATE at_jobs SET status = 'SUBMISSION_FAILED', updated_at = ?,
                    finished_at = ?, reconcile_note = ? WHERE job_key = ?
                """,
                (timestamp, timestamp, reason, job_key),
            )
            cursor = connection.execute(
                """
                UPDATE gates SET alarm = 1, alarm_reason = ?, blocker = ?,
                    next_step = 'Napraw planowanie i ponów wyłącznie przez at_gate.py',
                    version = version + 1, updated_at = ?
                WHERE gate_id = ? AND version = ?
                """,
                (reason, reason, timestamp, job["gate_id"], gate_version),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise CASConflict(f"CAS konflikt bramki {job['gate_id']}")
            updated = connection.execute(
                "SELECT * FROM gates WHERE gate_id = ?", (job["gate_id"],)
            ).fetchone()
            assert updated is not None
            connection.execute(
                """
                INSERT INTO gate_events (
                    gate_id, from_state, to_state, expected_version,
                    result_version, actor, reason, occurred_at, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, 'at_gate/schedule', ?, ?, ?)
                """,
                (
                    job["gate_id"],
                    gate["state"],
                    gate["state"],
                    gate_version,
                    int(updated["version"]),
                    reason,
                    timestamp,
                    self._event_snapshot(updated),
                ),
            )
            connection.commit()
        return self.show_at_job(job_key)

    def finish_at_job(
        self,
        job_key: str,
        *,
        runner_token: str,
        exit_code: int,
        evidence_hash: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        job_key = _required_text(job_key, "job_key")
        runner_token = _required_text(runner_token, "runner_token")
        evidence_hash = _validate_evidence_hash(evidence_hash)
        if not isinstance(exit_code, int):
            raise ValidationError("exit_code musi być liczbą całkowitą")
        timestamp = iso_utc(now or utc_now())
        token_hash = hashlib.sha256(runner_token.encode("utf-8")).hexdigest()
        self.initialize()
        with self._write_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            job = connection.execute(
                "SELECT * FROM at_jobs WHERE job_key = ?", (job_key,)
            ).fetchone()
            if job is None:
                connection.rollback()
                raise GateNotFound(f"brak at job: {job_key}")
            if not hmac.compare_digest(token_hash, str(job["runner_token_hash"])):
                connection.rollback()
                raise ValidationError("niepoprawny token wykonawcy")
            if job["status"] not in {"SCHEDULED", "MISSING_ALARM"}:
                connection.rollback()
                raise GateError(f"at job ma stan terminalny lub niegotowy: {job['status']}")
            new_status = "SUCCEEDED" if exit_code == 0 else "FAILED"
            connection.execute(
                """
                UPDATE at_jobs SET status = ?, exit_code = ?,
                    result_evidence_hash = ?, updated_at = ?, finished_at = ?,
                    reconcile_note = '' WHERE job_key = ?
                """,
                (new_status, exit_code, evidence_hash, timestamp, timestamp, job_key),
            )
            gate = connection.execute(
                "SELECT * FROM gates WHERE gate_id = ?", (job["gate_id"],)
            ).fetchone()
            assert gate is not None
            gate_version = int(gate["version"])
            if exit_code == 0 and gate["state"] == "WAIT_DATA":
                cursor = connection.execute(
                    """
                    UPDATE gates SET state = 'READY_FOR_REVIEW',
                        evidence_hash = ?, blocker = 'BRAK',
                        next_step = 'Niezależny review wyniku at-joba',
                        alarm = 0, alarm_reason = '', version = version + 1,
                        updated_at = ? WHERE gate_id = ? AND version = ?
                    """,
                    (evidence_hash, timestamp, job["gate_id"], gate_version),
                )
                if cursor.rowcount != 1:
                    connection.rollback()
                    raise CASConflict(f"CAS konflikt bramki {job['gate_id']}")
                updated = connection.execute(
                    "SELECT * FROM gates WHERE gate_id = ?", (job["gate_id"],)
                ).fetchone()
                assert updated is not None
                connection.execute(
                    """
                    INSERT INTO gate_events (
                        gate_id, from_state, to_state, expected_version,
                        result_version, actor, reason, occurred_at, snapshot_json
                    ) VALUES (?, 'WAIT_DATA', 'READY_FOR_REVIEW', ?, ?,
                              'at_gate/run', ?, ?, ?)
                    """,
                    (
                        job["gate_id"],
                        gate_version,
                        int(updated["version"]),
                        f"at-job zakończony kodem 0; dowód {evidence_hash}",
                        timestamp,
                        self._event_snapshot(updated),
                    ),
                )
            elif exit_code != 0:
                reason = f"at-job zakończył się kodem {exit_code}"
                cursor = connection.execute(
                    """
                    UPDATE gates SET evidence_hash = ?, alarm = 1,
                        alarm_reason = ?, blocker = ?,
                        next_step = 'Przeanalizuj błąd i utwórz nową kontrolowaną bramkę',
                        version = version + 1, updated_at = ?
                    WHERE gate_id = ? AND version = ?
                    """,
                    (
                        evidence_hash,
                        reason,
                        reason,
                        timestamp,
                        job["gate_id"],
                        gate_version,
                    ),
                )
                if cursor.rowcount != 1:
                    connection.rollback()
                    raise CASConflict(f"CAS konflikt bramki {job['gate_id']}")
                updated = connection.execute(
                    "SELECT * FROM gates WHERE gate_id = ?", (job["gate_id"],)
                ).fetchone()
                assert updated is not None
                connection.execute(
                    """
                    INSERT INTO gate_events (
                        gate_id, from_state, to_state, expected_version,
                        result_version, actor, reason, occurred_at, snapshot_json
                    ) VALUES (?, ?, ?, ?, ?, 'at_gate/run', ?, ?, ?)
                    """,
                    (
                        job["gate_id"],
                        gate["state"],
                        gate["state"],
                        gate_version,
                        int(updated["version"]),
                        reason,
                        timestamp,
                        self._event_snapshot(updated),
                    ),
                )
            connection.commit()
        return self.show_at_job(job_key)

    def reconcile_at_jobs(
        self,
        present_job_ids: set[str] | None,
        *,
        note: str = "",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        timestamp = iso_utc(now or utc_now())
        self.initialize()
        if present_job_ids is None:
            with self._write_connection() as connection:
                connection.execute("BEGIN IMMEDIATE")
                connection.execute(
                    """
                    UPDATE at_jobs SET reconcile_note = ?, updated_at = ?
                    WHERE status IN ('SUBMITTING', 'SCHEDULED', 'MISSING_ALARM')
                    """,
                    (_required_text(note or "atq UNAVAILABLE", "note"), timestamp),
                )
                count = connection.execute(
                    """SELECT COUNT(*) FROM at_jobs
                       WHERE status IN ('SUBMITTING', 'SCHEDULED', 'MISSING_ALARM')"""
                ).fetchone()[0]
                connection.commit()
            return {"status": "UNAVAILABLE", "active": count, "alarms": []}

        normalized = {str(value) for value in present_job_ids if str(value).isdigit()}
        alarms: list[dict[str, str]] = []
        seen: list[str] = []
        with self._write_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            jobs = connection.execute(
                """
                SELECT * FROM at_jobs
                WHERE status IN ('SUBMITTING', 'SCHEDULED', 'MISSING_ALARM')
                ORDER BY job_key
                """
            ).fetchall()
            for job in jobs:
                queue_id = str(job["at_job_id"] or "")
                if job["status"] == "SUBMITTING":
                    created = parse_timestamp(str(job["created_at"]), "created_at")
                    if (parse_timestamp(timestamp) - created).total_seconds() <= 300:
                        continue
                    reason = "ALARM: intencja at pozostała bez identyfikatora kolejki ponad 5 minut"
                    connection.execute(
                        """
                        UPDATE at_jobs SET status = 'MISSING_ALARM', updated_at = ?,
                            reconcile_note = ? WHERE job_key = ?
                        """,
                        (timestamp, reason, job["job_key"]),
                    )
                    gate = connection.execute(
                        "SELECT * FROM gates WHERE gate_id = ?", (job["gate_id"],)
                    ).fetchone()
                    assert gate is not None
                    gate_version = int(gate["version"])
                    cursor = connection.execute(
                        """
                        UPDATE gates SET alarm = 1, alarm_reason = ?, blocker = ?,
                            next_step = 'Sprawdź kolejkę i rozlicz przerwane planowanie',
                            version = version + 1, updated_at = ?
                        WHERE gate_id = ? AND version = ?
                        """,
                        (reason, reason, timestamp, job["gate_id"], gate_version),
                    )
                    if cursor.rowcount != 1:
                        connection.rollback()
                        raise CASConflict(f"CAS konflikt bramki {job['gate_id']}")
                    updated = connection.execute(
                        "SELECT * FROM gates WHERE gate_id = ?", (job["gate_id"],)
                    ).fetchone()
                    assert updated is not None
                    connection.execute(
                        """
                        INSERT INTO gate_events (
                            gate_id, from_state, to_state, expected_version,
                            result_version, actor, reason, occurred_at, snapshot_json
                        ) VALUES (?, ?, ?, ?, ?, 'at_gate/reconcile', ?, ?, ?)
                        """,
                        (
                            job["gate_id"],
                            gate["state"],
                            gate["state"],
                            gate_version,
                            int(updated["version"]),
                            reason,
                            timestamp,
                            self._event_snapshot(updated),
                        ),
                    )
                    alarms.append(
                        {"job_key": job["job_key"], "gate_id": job["gate_id"], "at_job_id": ""}
                    )
                    continue
                if queue_id in normalized:
                    seen.append(queue_id)
                    connection.execute(
                        """
                        UPDATE at_jobs SET last_seen_at = ?, updated_at = ?,
                            reconcile_note = '' WHERE job_key = ?
                        """,
                        (timestamp, timestamp, job["job_key"]),
                    )
                    continue
                if job["status"] == "MISSING_ALARM":
                    alarms.append(
                        {"job_key": job["job_key"], "gate_id": job["gate_id"], "at_job_id": queue_id}
                    )
                    continue
                reason = f"ALARM: at-job #{queue_id} zniknął z atq bez statusu terminalnego"
                connection.execute(
                    """
                    UPDATE at_jobs SET status = 'MISSING_ALARM', updated_at = ?,
                        reconcile_note = ? WHERE job_key = ?
                    """,
                    (timestamp, reason, job["job_key"]),
                )
                gate = connection.execute(
                    "SELECT * FROM gates WHERE gate_id = ?", (job["gate_id"],)
                ).fetchone()
                assert gate is not None
                gate_version = int(gate["version"])
                connection.execute(
                    """
                    UPDATE gates SET alarm = 1, alarm_reason = ?, blocker = ?,
                        next_step = 'Ustal wynik z logu i oznacz status terminalny',
                        version = version + 1, updated_at = ?
                    WHERE gate_id = ? AND version = ?
                    """,
                    (reason, reason, timestamp, job["gate_id"], gate_version),
                )
                updated = connection.execute(
                    "SELECT * FROM gates WHERE gate_id = ?", (job["gate_id"],)
                ).fetchone()
                assert updated is not None
                connection.execute(
                    """
                    INSERT INTO gate_events (
                        gate_id, from_state, to_state, expected_version,
                        result_version, actor, reason, occurred_at, snapshot_json
                    ) VALUES (?, ?, ?, ?, ?, 'at_gate/reconcile', ?, ?, ?)
                    """,
                    (
                        job["gate_id"],
                        gate["state"],
                        gate["state"],
                        gate_version,
                        int(updated["version"]),
                        reason,
                        timestamp,
                        self._event_snapshot(updated),
                    ),
                )
                alarms.append(
                    {"job_key": job["job_key"], "gate_id": job["gate_id"], "at_job_id": queue_id}
                )
            connection.commit()
        return {"status": "OK", "seen": sorted(seen, key=int), "alarms": alarms}

    def show_at_job(self, job_key: str) -> dict[str, Any]:
        job_key = _required_text(job_key, "job_key")
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM at_jobs WHERE job_key = ?", (job_key,)
            ).fetchone()
            if row is None:
                raise GateNotFound(f"brak at job: {job_key}")
        return self._row_to_job(row)

    def list_at_jobs(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        if not self.db_path.is_file():
            return []
        query = "SELECT * FROM at_jobs"
        if active_only:
            query += " WHERE status IN ('SUBMITTING', 'SCHEDULED', 'MISSING_ALARM')"
        query += " ORDER BY created_at, job_key"
        with self._read_connection() as connection:
            rows = connection.execute(query).fetchall()
        return [self._row_to_job(row) for row in rows]


def _display(value: Any, limit: int) -> str:
    text = str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_open_gates(
    gates: Sequence[Mapping[str, Any]],
    *,
    as_of: datetime,
    source: str = "gates.sqlite3",
    ledger_hash: str | None = None,
) -> str:
    """Renderuj deterministyczny widok o gwarantowanej długości 20–30 linii."""
    if as_of.tzinfo is None or as_of.utcoffset() is None:
        raise ValidationError("as_of musi zawierać strefę czasową")
    as_of = as_of.astimezone(timezone.utc).replace(microsecond=0)
    ledger_hash = ledger_hash or sha256_json(list(gates))
    ledger_hash = _validate_evidence_hash(ledger_hash)
    open_rows: list[tuple[int, Mapping[str, Any]]] = []
    for gate in gates:
        if gate.get("state") in TERMINAL_STATES:
            continue
        opened = parse_timestamp(str(gate["opened_at"]), "opened_at")
        days = max(0, int((as_of - opened).total_seconds() // 86400))
        open_rows.append((days, gate))
    open_rows.sort(key=lambda item: (-item[0], str(item[1]["gate_id"])))
    visible = open_rows[:10]
    alarms = sum(bool(gate.get("alarm")) for _, gate in open_rows)
    overdue = sum(parse_timestamp(str(gate["due_at"]), "due_at") < as_of for _, gate in open_rows)
    oldest = f"{open_rows[0][0]} dni / {open_rows[0][1]['gate_id']}" if open_rows else "brak"

    lines = [
        "# OPEN GATES",
        "",
        "> GENERATED — edycja bezcelowa; źródłem prawdy jest kanoniczna baza SQLite.",
        f"> Źródło: `{_display(source, 100)}`",
        f"> Ledger SHA-256: `{ledger_hash}`",
        f"> Stan na: `{iso_utc(as_of)}`",
        "",
        f"Otwarte: **{len(open_rows)}** | po terminie: **{overdue}** | ALARM: **{alarms}**",
        "",
        "| dni | ID | stan | owner | termin | alarm |",
        "|---:|---|---|---|---|---|",
    ]
    if visible:
        for days, gate in visible:
            due_date = parse_timestamp(str(gate["due_at"]), "due_at").date().isoformat()
            alarm = "ALARM" if gate.get("alarm") else "—"
            lines.append(
                "| "
                + " | ".join(
                    (
                        str(days),
                        _display(gate["gate_id"], 38),
                        _display(gate["state"], 22),
                        _display(gate["owner"], 20),
                        due_date,
                        alarm,
                    )
                )
                + " |"
            )
    else:
        lines.append("| — | brak otwartych bramek | — | — | — | — |")
    lines.extend(
        [
            "",
            "## Kontrola",
            "",
            f"- Najstarsza: {oldest}.",
            f"- Pominięte z tabeli: {max(0, len(open_rows) - len(visible))}.",
            "- Kolejność: dni wiszenia malejąco, potem ID rosnąco.",
            "- Terminalne: CLOSED, REJECTED i SUPERSEDED nie są pokazywane.",
            "- ALARM oznacza brak terminalnego wyniku zarejestrowanego at-joba.",
            "- Odświeżenie: `process_debt_gate.py export --format open-gates`.",
        ]
    )
    if not 20 <= len(lines) <= 30:
        raise AssertionError(f"widok ma {len(lines)} linii, oczekiwano 20-30")
    return "\n".join(lines) + "\n"


def export_payload(store: GateStore, *, as_of: datetime) -> dict[str, Any]:
    gates = store.list_gates(include_terminal=True)
    at_jobs = store.list_at_jobs()
    return {
        "schema_version": 1,
        "generated_at": iso_utc(as_of),
        "source": str(store.db_path),
        "ledger_hash": sha256_json(gates),
        "gates": gates,
        "at_jobs": at_jobs,
    }


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary, 0o600)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _json_object(value: str) -> dict[str, Any]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"niepoprawny JSON: {exc}") from exc
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("wymagany obiekt JSON")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB), help="ścieżka bazy SQLite")
    subparsers = parser.add_subparsers(dest="command", required=True)

    add = subparsers.add_parser("add", help="dodaj rekord w stanie BUILT_OFF")
    add.add_argument("--id", required=True, dest="gate_id")
    add.add_argument("--title", required=True)
    add.add_argument("--kind", required=True)
    add.add_argument("--owner", required=True)
    add.add_argument("--due", required=True, dest="due_at")
    add.add_argument("--next-step", required=True)
    add.add_argument("--blocker", required=True)
    add.add_argument("--code-sha", required=True)
    add.add_argument("--evidence-hash", required=True)
    add.add_argument("--opened-at")
    add.add_argument("--metadata", type=_json_object, default={})
    add.add_argument("--actor", default="process_debt_gate/add")
    add.add_argument("--reason", default="utworzenie rekordu")

    transition = subparsers.add_parser("transition", help="atomowe przejście CAS")
    transition.add_argument("gate_id")
    transition.add_argument("to_state", choices=ALL_STATES)
    transition.add_argument("--expected-version", required=True, type=int)
    transition.add_argument("--actor", required=True)
    transition.add_argument("--reason", required=True)
    transition.add_argument("--owner")
    transition.add_argument("--due", dest="due_at")
    transition.add_argument("--next-step")
    transition.add_argument("--blocker")
    transition.add_argument("--code-sha")
    transition.add_argument("--evidence-hash")
    transition.add_argument("--metadata", type=_json_object)

    list_parser = subparsers.add_parser("list", help="lista rekordów")
    list_parser.add_argument("--state", action="append", choices=ALL_STATES)
    list_parser.add_argument("--owner")
    list_parser.add_argument("--alarm", action="store_true")
    list_parser.add_argument("--open-only", action="store_true")
    list_parser.add_argument("--limit", type=int)

    show = subparsers.add_parser("show", help="rekord wraz z historią")
    show.add_argument("gate_id")

    export = subparsers.add_parser("export", help="eksport JSON albo OPEN_GATES.md")
    export.add_argument("--format", choices=("json", "open-gates"), default="json")
    export.add_argument("--output", default="-")
    export.add_argument("--as-of", help="czas deterministycznego renderu ISO-8601")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    store = GateStore(args.db)
    try:
        if args.command == "add":
            result = store.add_gate(
                gate_id=args.gate_id,
                title=args.title,
                kind=args.kind,
                owner=args.owner,
                due_at=args.due_at,
                next_step=args.next_step,
                blocker=args.blocker,
                code_sha=args.code_sha,
                evidence_hash=args.evidence_hash,
                opened_at=args.opened_at,
                metadata=args.metadata,
                actor=args.actor,
                reason=args.reason,
            )
            output = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        elif args.command == "transition":
            result = store.transition(
                args.gate_id,
                args.to_state,
                expected_version=args.expected_version,
                actor=args.actor,
                reason=args.reason,
                owner=args.owner,
                due_at=args.due_at,
                next_step=args.next_step,
                blocker=args.blocker,
                code_sha=args.code_sha,
                evidence_hash=args.evidence_hash,
                metadata=args.metadata,
            )
            output = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        elif args.command == "list":
            result = store.list_gates(
                states=args.state,
                owner=args.owner,
                alarm_only=args.alarm,
                include_terminal=not args.open_only,
                limit=args.limit,
            )
            output = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        elif args.command == "show":
            result = store.show_gate(args.gate_id)
            output = json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
        else:
            as_of = parse_timestamp(args.as_of, "as_of") if args.as_of else utc_now()
            if args.format == "json":
                output = json.dumps(
                    export_payload(store, as_of=as_of),
                    ensure_ascii=False,
                    indent=2,
                    sort_keys=True,
                ) + "\n"
            else:
                gates = store.list_gates(include_terminal=True)
                output = render_open_gates(
                    gates,
                    as_of=as_of,
                    source=str(store.db_path),
                    ledger_hash=sha256_json(gates),
                )
            if args.output != "-":
                atomic_write(Path(args.output), output)
                print(json.dumps({"written": args.output}, ensure_ascii=False))
                return 0
        sys.stdout.write(output)
        return 0
    except (GateError, sqlite3.Error) as exc:
        print(json.dumps({"error": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
