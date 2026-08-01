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
import uuid
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
CLAIM_ACTIVE_STATUSES = frozenset(
    {"CLAIMED", "RECEIPT_READY", "OUTCOME_UNKNOWN"}
)
DB_SCHEMA_VERSION = 4
SEALED_AUTH_VERSION = 2
CLAIM_BINDING_VERSION = 2
AT_RUN_CLAIM_STALE_SECONDS = 12 * 60 * 60
AT_CANCEL_CLAIM_STALE_SECONDS = 5 * 60
AT_LAUNCH_GRACE_SECONDS = 2 * 60
MAX_PRIVATE_FILE_BYTES = 4 * 1024 * 1024
RESULT_RECEIPT_KEYS = frozenset(
    {
        "schema_version",
        "job_key",
        "gate_id",
        "claim_id",
        "binding_sha256",
        "command_sha256",
        "exit_code",
        "created_at",
        "execution",
        "stdout",
        "stderr",
    }
)
RESULT_EXECUTION_KEYS = frozenset(
    {"child_started", "direct_child_exit_observed", "stdio_eof_observed"}
)
RESULT_STREAM_KEYS = frozenset(
    {"path", "sha256", "device", "inode", "ctime_ns", "size"}
)

_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._:-]{2,127}$")
_SHA_RE = re.compile(r"^(?:[0-9a-f]{40}|[0-9a-f]{64})$")
_HASH_RE = re.compile(r"^[0-9a-f]{64}$")


class GateError(RuntimeError):
    """Błąd kontraktu rejestru."""


class StorageError(GateError):
    """Błąd SQLite przetłumaczony na publicznej granicy GateStore."""


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


class ClaimConflict(GateError):
    """At-job ma już claim RUN albo CANCEL; drugi skutek jest zabroniony."""


class ReceiptError(GateError):
    """Trwały receipt nie zgadza się z claimem albo zmienił identity."""


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


def canonical_argv_hash(command: Sequence[str]) -> str:
    if not command or any(not isinstance(part, str) or not part for part in command):
        raise ValidationError("command musi być niepustą listą argumentów")
    return sha256_json(list(command))


def runner_auth_binding(
    *,
    job_key: str,
    gate_id: str,
    scheduled_for: str,
    command_sha256: str,
    payload_sha256: str,
    artifact_root: str,
) -> dict[str, Any]:
    """Jedno kanoniczne związanie sealed payloadu z exact jobem i argv."""

    artifact_root = _required_text(artifact_root, "artifact_root")
    if not Path(artifact_root).is_absolute():
        raise ValidationError("artifact_root musi być absolutna")
    return {
        "schema_version": SEALED_AUTH_VERSION,
        "operation": "RUN",
        "job_key": _required_text(job_key, "job_key"),
        "gate_id": _validate_gate_id(gate_id),
        "scheduled_for": iso_utc(parse_timestamp(scheduled_for, "scheduled_for")),
        "command_sha256": _validate_evidence_hash(command_sha256),
        "payload_sha256": _validate_evidence_hash(payload_sha256),
        "artifact_root": artifact_root,
    }


def runner_auth_tag(token: str, binding: Mapping[str, Any]) -> str:
    token = _required_text(token, "runner_token")
    return hmac.new(
        token.encode("utf-8"),
        canonical_json(dict(binding)).encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()


def claim_receipt_path(*, artifact_root: str, job_key: str, claim_id: str) -> str:
    """Jedyna kanoniczna lokalizacja trwałego receiptu RUN claimu."""

    root_text = _required_text(artifact_root, "artifact_root")
    root = Path(root_text)
    if not root.is_absolute() or str(root.absolute()) != root_text:
        raise ValidationError("artifact_root musi być kanoniczną ścieżką absolutną")
    job_key = _required_text(job_key, "job_key")
    claim_id = _required_text(claim_id, "claim_id")
    if claim_id in {".", ".."} or "/" in claim_id:
        raise ValidationError("claim_id nie może zmieniać drzewa artefaktów")
    job_component = hashlib.sha256(job_key.encode("utf-8")).hexdigest()[:24]
    return str(root / job_component / claim_id / "receipt.json")


def outcome_unknown_reason(operation: str) -> str:
    operation = str(operation or "UNKNOWN").upper()
    stale_after = (
        AT_RUN_CLAIM_STALE_SECONDS
        if operation == "RUN"
        else AT_CANCEL_CLAIM_STALE_SECONDS
    )
    return (
        f"ALARM: {operation} claim ma OUTCOME_UNKNOWN; brak durable receipt "
        f"ponad {stale_after} sekund, re-exec zabroniony"
    )


def receipt_stalled_reason() -> str:
    return (
        "ALARM: RUN receipt pozostał niesfinalizowany ponad "
        f"{AT_RUN_CLAIM_STALE_SECONDS} sekund, re-exec zabroniony"
    )


def _validated_identity(value: Mapping[str, Any] | None, field: str) -> dict[str, Any]:
    if not isinstance(value, Mapping):
        raise ValidationError(f"{field}: wymagany obiekt identity")
    expected = {"sha256", "device", "inode", "ctime_ns", "size"}
    if set(value) != expected:
        raise ValidationError(f"{field}: wymagane exact pola {sorted(expected)}")
    try:
        result = {
            "sha256": _validate_evidence_hash(str(value["sha256"])),
            "device": int(value["device"]),
            "inode": int(value["inode"]),
            "ctime_ns": int(value["ctime_ns"]),
            "size": int(value["size"]),
        }
    except (TypeError, ValueError) as exc:
        raise ValidationError(f"{field}: niepoprawna tożsamość pliku") from exc
    if any(result[key] < 0 for key in ("device", "inode", "ctime_ns", "size")):
        raise ValidationError(f"{field}: wartości identity nie mogą być ujemne")
    return result


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


def ensure_private_directory(
    path: str | os.PathLike[str],
    *,
    create: bool,
) -> Path:
    """Wymuś root-owned 0700 i brak symlinku w całej ścieżce katalogu."""

    selected = Path(path).absolute()
    if selected.exists() or selected.is_symlink():
        info = selected.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ValidationError(
                f"prywatny katalog nie jest zwykłym katalogiem: {selected}"
            )
    else:
        if not create:
            raise ReceiptError(f"brak prywatnego katalogu: {selected}")
        selected.mkdir(mode=0o700, parents=True, exist_ok=False)
        parent_fd = os.open(
            selected.parent,
            os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
        )
        try:
            os.fsync(parent_fd)
        finally:
            os.close(parent_fd)
        info = selected.lstat()
    if selected.resolve() != selected:
        raise ValidationError(f"prywatny katalog ma symlink w ścieżce: {selected}")
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValidationError(
            f"prywatny katalog wymaga ownera procesu i mode 0700: {selected}"
        )
    return selected


def read_private_bytes(path: str | os.PathLike[str]) -> tuple[bytes, dict[str, Any]]:
    """Odczytaj stabilny root-owned plik 0600 bez podążania za finalnym symlinkiem."""

    selected = Path(path)
    if not selected.is_absolute():
        raise ValidationError("prywatny artefakt musi mieć ścieżkę absolutną")
    ensure_private_directory(selected.parent, create=False)
    try:
        descriptor = os.open(
            selected,
            os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0),
        )
    except OSError as exc:
        raise ReceiptError(f"prywatny artefakt jest niedostępny: {exc}") from exc
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size > MAX_PRIVATE_FILE_BYTES
        ):
            raise ReceiptError("prywatny artefakt nie spełnia owner/mode/size")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(
                descriptor,
                min(1024 * 1024, MAX_PRIVATE_FILE_BYTES + 1 - total),
            )
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > MAX_PRIVATE_FILE_BYTES:
                raise ReceiptError("prywatny artefakt przekracza limit")
        data = b"".join(chunks)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_ctime_ns,
            before.st_size,
        ) != (after.st_dev, after.st_ino, after.st_ctime_ns, after.st_size):
            raise ReceiptError("prywatny artefakt zmienił identity podczas odczytu")
    finally:
        os.close(descriptor)
    return data, {
        "sha256": hashlib.sha256(data).hexdigest(),
        "device": int(after.st_dev),
        "inode": int(after.st_ino),
        "ctime_ns": int(after.st_ctime_ns),
        "size": int(after.st_size),
    }


def load_claim_receipt(
    path: str | os.PathLike[str],
    *,
    claim: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], bytes, bytes]:
    """Zweryfikuj exact receipt i oba trwałe streamy względem RUN claimu."""

    selected = Path(path)
    data, identity = read_private_bytes(selected)
    try:
        receipt = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ReceiptError("durable receipt ma niepoprawny JSON") from exc
    if not isinstance(receipt, dict) or set(receipt) != RESULT_RECEIPT_KEYS:
        raise ReceiptError("durable receipt exact shape mismatch")
    binding = claim.get("binding")
    if not isinstance(binding, Mapping):
        try:
            binding = json.loads(str(claim["binding_json"]))
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise ReceiptError("claim nie zawiera kanonicznego binding") from exc
    binding_digest = str(claim.get("binding_sha256") or "")
    if binding_digest != sha256_json(dict(binding)):
        raise ReceiptError("claim binding digest mismatch")
    if binding.get("operation") != "RUN":
        raise ReceiptError("durable receipt nie należy do RUN claimu")
    try:
        canonical_path = claim_receipt_path(
            artifact_root=str(binding.get("artifact_root") or ""),
            job_key=str(claim.get("job_key") or ""),
            claim_id=str(claim.get("claim_id") or ""),
        )
    except ValidationError as exc:
        raise ReceiptError("claim ma niekanoniczne drzewo receiptu") from exc
    if str(selected) != canonical_path:
        raise ReceiptError("durable receipt path nie pochodzi z claimu")
    expected = {
        "schema_version": 3,
        "job_key": claim.get("job_key"),
        "gate_id": claim.get("gate_id"),
        "claim_id": claim.get("claim_id"),
        "binding_sha256": binding_digest,
        "command_sha256": binding.get("command_sha256"),
    }
    for key, value in expected.items():
        if receipt.get(key) != value:
            raise ReceiptError(f"durable receipt.{key} nie zgadza się z claimem")
    if str(selected) != str(claim.get("receipt_path") or ""):
        raise ReceiptError("durable receipt path nie zgadza się z claimem")
    if not isinstance(receipt["exit_code"], int) or isinstance(
        receipt["exit_code"], bool
    ):
        raise ReceiptError("durable receipt exit_code ma zły typ")
    parse_timestamp(str(receipt["created_at"]), "receipt.created_at")
    execution = receipt["execution"]
    if not isinstance(execution, dict) or set(execution) != RESULT_EXECUTION_KEYS:
        raise ReceiptError("durable receipt execution shape mismatch")
    if any(not isinstance(execution[key], bool) for key in RESULT_EXECUTION_KEYS):
        raise ReceiptError("durable receipt execution ma zły typ")
    if execution["stdio_eof_observed"] is not True:
        raise ReceiptError("durable receipt bez poświadczonego EOF stdout/stderr")
    if execution["direct_child_exit_observed"] is not execution["child_started"]:
        raise ReceiptError("durable receipt ma niespójny stan direct child")
    streams: dict[str, bytes] = {}
    for name in ("stdout", "stderr"):
        stream = receipt[name]
        if not isinstance(stream, dict) or set(stream) != RESULT_STREAM_KEYS:
            raise ReceiptError(f"durable receipt {name} shape mismatch")
        stream_path = selected.parent / f"{name}.bin"
        if str(stream_path) != str(stream.get("path") or ""):
            raise ReceiptError(f"durable receipt {name} path drift")
        stream_data, stream_identity = read_private_bytes(stream_path)
        expected_identity = _validated_identity(
            {key: stream[key] for key in RESULT_STREAM_KEYS if key != "path"},
            f"receipt.{name}",
        )
        if stream_identity != expected_identity:
            raise ReceiptError(f"durable receipt {name} identity mismatch")
        streams[name] = stream_data
    return receipt, identity, streams["stdout"], streams["stderr"]


def _metadata_json(value: Mapping[str, Any] | None) -> str:
    if value is None:
        value = {}
    if not isinstance(value, Mapping):
        raise ValidationError("metadata musi być obiektem JSON")
    return canonical_json(dict(value))


AT_JOB_CLAIMS_SCHEMA = """
CREATE TABLE IF NOT EXISTS at_job_claims (
    claim_id TEXT PRIMARY KEY,
    job_key TEXT NOT NULL UNIQUE,
    gate_id TEXT NOT NULL REFERENCES gates(gate_id),
    status TEXT NOT NULL CHECK (status IN (
        'CLAIMED', 'RECEIPT_READY', 'FINALIZED',
        'OUTCOME_UNKNOWN'
    )),
    binding_json TEXT NOT NULL,
    binding_sha256 TEXT NOT NULL,
    receipt_path TEXT NOT NULL DEFAULT '',
    receipt_sha256 TEXT,
    receipt_dev INTEGER,
    receipt_ino INTEGER,
    receipt_ctime_ns INTEGER,
    receipt_size INTEGER,
    exit_code INTEGER,
    stdout_sha256 TEXT,
    stderr_sha256 TEXT,
    claimed_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finalized_at TEXT,
    FOREIGN KEY (job_key, gate_id) REFERENCES at_jobs(job_key, gate_id),
    CHECK (json_valid(binding_json) = 1 AND json_type(binding_json) = 'object'),
    CHECK (
        COALESCE(json_extract(binding_json, '$.operation'), '') IN ('RUN', 'CANCEL')
    ),
    CHECK (
        json_extract(binding_json, '$.claim_id') = claim_id
    ),
    CHECK (
        (
            status IN ('CLAIMED', 'OUTCOME_UNKNOWN')
            AND finalized_at IS NULL
            AND receipt_sha256 IS NULL AND receipt_dev IS NULL
            AND receipt_ino IS NULL AND receipt_ctime_ns IS NULL
            AND receipt_size IS NULL AND exit_code IS NULL
            AND stdout_sha256 IS NULL AND stderr_sha256 IS NULL
            AND (
                (json_extract(binding_json, '$.operation') = 'RUN' AND receipt_path <> '')
                OR
                (json_extract(binding_json, '$.operation') = 'CANCEL' AND receipt_path = '')
            )
        )
        OR (
            status = 'RECEIPT_READY'
            AND json_extract(binding_json, '$.operation') = 'RUN'
            AND receipt_path <> '' AND receipt_sha256 IS NOT NULL
            AND receipt_dev IS NOT NULL AND receipt_ino IS NOT NULL
            AND receipt_ctime_ns IS NOT NULL AND receipt_size IS NOT NULL
            AND exit_code IS NOT NULL AND stdout_sha256 IS NOT NULL
            AND stderr_sha256 IS NOT NULL AND finalized_at IS NULL
        )
        OR (
            status = 'FINALIZED' AND finalized_at IS NOT NULL
            AND (
                (
                    json_extract(binding_json, '$.operation') = 'RUN'
                    AND receipt_path <> '' AND receipt_sha256 IS NOT NULL
                    AND receipt_dev IS NOT NULL AND receipt_ino IS NOT NULL
                    AND receipt_ctime_ns IS NOT NULL AND receipt_size IS NOT NULL
                    AND exit_code IS NOT NULL AND stdout_sha256 IS NOT NULL
                    AND stderr_sha256 IS NOT NULL
                )
                OR
                (
                    json_extract(binding_json, '$.operation') = 'CANCEL'
                    AND receipt_path = '' AND receipt_sha256 IS NULL
                    AND receipt_dev IS NULL AND receipt_ino IS NULL
                    AND receipt_ctime_ns IS NULL AND receipt_size IS NULL
                    AND exit_code IS NULL AND stdout_sha256 IS NULL
                    AND stderr_sha256 IS NULL
                )
            )
        )
    )
);

CREATE INDEX IF NOT EXISTS at_job_claims_status
ON at_job_claims(status, claimed_at, job_key);
"""


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
    reconcile_note TEXT NOT NULL DEFAULT '',
    auth_version INTEGER NOT NULL DEFAULT 1 CHECK (auth_version IN (1, 2)),
    runner_auth_tag TEXT,
    command_sha256 TEXT,
    payload_path TEXT,
    payload_sha256 TEXT,
    payload_dev INTEGER,
    payload_ino INTEGER,
    payload_ctime_ns INTEGER,
    payload_size INTEGER,
    artifact_root TEXT
);

CREATE UNIQUE INDEX IF NOT EXISTS at_jobs_queue_id
ON at_jobs(at_job_id) WHERE at_job_id IS NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS at_jobs_one_active_per_gate
ON at_jobs(gate_id) WHERE status IN ('SUBMITTING', 'SCHEDULED', 'MISSING_ALARM');

CREATE UNIQUE INDEX IF NOT EXISTS at_jobs_job_gate_identity
ON at_jobs(job_key, gate_id);

{AT_JOB_CLAIMS_SCHEMA}
"""

# Niezależny, reviewowany ratchet source DDL. Samo zbudowanie expected manifestu
# z ``SCHEMA`` jest samoreferencyjne: dopisanie nowego writera do obu stron
# wyglądałoby jak legalny expected. Każda świadoma zmiana schematu musi więc
# osobno zmienić ten digest i przejść migration review.
EXPECTED_SCHEMA_MANIFEST_SHA256 = (
    "574b4edacfad8ef4dd18c36b15247e7180eb51dc27bb6d56d23b44eab6f93e9f"
)

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
        "reconcile_note", "auth_version", "runner_auth_tag", "command_sha256",
        "payload_path", "payload_sha256", "payload_dev", "payload_ino",
        "payload_ctime_ns", "payload_size", "artifact_root",
    },
    "at_job_claims": {
        "claim_id", "job_key", "gate_id", "status", "binding_json",
        "binding_sha256", "receipt_path", "receipt_sha256", "receipt_dev",
        "receipt_ino", "receipt_ctime_ns", "receipt_size", "exit_code",
        "stdout_sha256", "stderr_sha256", "claimed_at", "updated_at",
        "finalized_at",
    },
}

PUBLIC_GATE_COLUMNS = (
    "gate_id", "title", "kind", "state", "owner", "due_at", "next_step",
    "blocker", "code_sha", "evidence_hash", "version", "alarm",
    "alarm_reason", "opened_at", "created_at", "updated_at", "closed_at",
)
PUBLIC_GATE_EVENT_COLUMNS = (
    "event_id", "gate_id", "from_state", "to_state", "expected_version",
    "result_version", "actor", "reason", "occurred_at",
)
PUBLIC_AT_JOB_COLUMNS = (
    "job_key", "gate_id", "at_job_id", "status", "scheduled_for",
    "created_at", "updated_at", "last_seen_at", "finished_at", "exit_code",
    "result_evidence_hash", "auth_version", "command_sha256", "payload_sha256",
)
PUBLIC_AT_CLAIM_COLUMNS = (
    "claim_id", "job_key", "gate_id", "status", "binding_sha256",
    "receipt_sha256", "exit_code", "stdout_sha256", "stderr_sha256",
    "claimed_at", "updated_at", "finalized_at",
)
INTERNAL_AT_JOB_COLUMNS = (
    "job_key", "gate_id", "at_job_id", "status", "scheduled_for",
    "created_at", "updated_at", "last_seen_at", "finished_at", "exit_code",
    "result_evidence_hash", "reconcile_note", "auth_version", "command_sha256",
    "payload_path", "payload_sha256", "payload_dev", "payload_ino",
    "payload_ctime_ns", "payload_size", "artifact_root",
)
INTERNAL_AT_CLAIM_COLUMNS = (
    "claim_id", "job_key", "gate_id", "status", "binding_sha256",
    "receipt_path", "receipt_sha256", "receipt_dev", "receipt_ino",
    "receipt_ctime_ns", "receipt_size", "exit_code", "stdout_sha256",
    "stderr_sha256", "claimed_at", "updated_at", "finalized_at",
)

V2_AT_JOB_COLUMNS: dict[str, str] = {
    "auth_version": "INTEGER NOT NULL DEFAULT 1 CHECK (auth_version IN (1, 2))",
    "runner_auth_tag": "TEXT",
    "command_sha256": "TEXT",
    "payload_path": "TEXT",
    "payload_sha256": "TEXT",
    "payload_dev": "INTEGER",
    "payload_ino": "INTEGER",
    "payload_ctime_ns": "INTEGER",
    "payload_size": "INTEGER",
    "artifact_root": "TEXT",
}

LEGACY_V2_CLAIM_COLUMNS = {
    "claim_id", "job_key", "gate_id", "status", "binding_json",
    "binding_sha256", "auth_tag", "receipt_path", "receipt_sha256",
    "receipt_dev", "receipt_ino", "receipt_ctime_ns", "receipt_size",
    "exit_code", "stdout_sha256", "stderr_sha256", "claimed_at",
    "updated_at", "finalized_at",
}


def _canonical_schema_sql(value: Any) -> list[str]:
    """Normalizuj wyłącznie nieistotne formatowanie DDL.

    UNIQUE/CHECK/FK/partial-index oraz kolejność kolumn pozostają częścią
    wyniku. Usuwamy tylko whitespace poza literałami; SQLite sam usuwa
    ``IF NOT EXISTS`` z ``sqlite_schema``. Literałów nie
    lowercasujemy ani nie sklejamy, bo zmieniłoby to semantykę CHECK.
    """

    if not isinstance(value, str) or not value.strip():
        return []
    result: list[str] = []
    index = 0
    while index < len(value):
        character = value[index]
        if character.isspace():
            index += 1
            continue
        if character in {"'", '"', "`", "["}:
            closing = "]" if character == "[" else character
            start = index
            index += 1
            while index < len(value):
                if value[index] == closing:
                    if index + 1 < len(value) and value[index + 1] == closing:
                        index += 2
                        continue
                    index += 1
                    break
                index += 1
            result.append("quoted:" + value[start:index])
            continue
        if character.isalpha() or character == "_":
            start = index
            index += 1
            while index < len(value) and (
                value[index].isalnum() or value[index] in {"_", "$"}
            ):
                index += 1
            result.append("word:" + value[start:index].lower())
            continue
        if character.isdigit():
            start = index
            index += 1
            while index < len(value) and (
                value[index].isdigit() or value[index] in {".", "e", "E", "+", "-"}
            ):
                index += 1
            result.append("number:" + value[start:index].lower())
            continue
        matched = next(
            (
                operator
                for operator in ("->>", "!=", "<=", ">=", "<>", "||", "->")
                if value.startswith(operator, index)
            ),
            character,
        )
        result.append("symbol:" + matched)
        index += len(matched)
    return result


def _schema_manifest(connection: sqlite3.Connection) -> dict[str, Any]:
    """Zbuduj dokładny, deterministyczny manifest DDL i właściwości PRAGMA."""

    objects = []
    for row in connection.execute(
        """
        SELECT type, name, tbl_name, sql FROM sqlite_schema
        WHERE type IN ('table', 'index', 'trigger', 'view')
          AND name NOT LIKE 'sqlite_%'
        ORDER BY type, name
        """
    ).fetchall():
        objects.append(
            {
                "type": str(row[0]),
                "name": str(row[1]),
                "table": str(row[2]),
                "sql": _canonical_schema_sql(row[3]),
            }
        )

    tables: dict[str, Any] = {}
    for table in sorted(REQUIRED_COLUMNS):
        columns = [
            {
                "cid": int(row[0]),
                "name": str(row[1]),
                "type": str(row[2]).upper(),
                "notnull": int(row[3]),
                "default": row[4],
                "pk": int(row[5]),
            }
            for row in connection.execute(f"PRAGMA table_info({table})").fetchall()
        ]
        foreign_keys = [
            [row[index] for index in range(len(row))]
            for row in connection.execute(f"PRAGMA foreign_key_list({table})").fetchall()
        ]
        indexes = []
        for row in connection.execute(f"PRAGMA index_list({table})").fetchall():
            index_name = str(row[1])
            indexes.append(
                {
                    "name": index_name,
                    "unique": int(row[2]),
                    "origin": str(row[3]),
                    "partial": int(row[4]),
                    "columns": [
                        [detail[index] for index in range(len(detail))]
                        for detail in connection.execute(
                            f"PRAGMA index_xinfo({index_name})"
                        ).fetchall()
                    ],
                }
            )
        indexes.sort(key=lambda item: item["name"])
        tables[table] = {
            "columns": columns,
            "foreign_keys": foreign_keys,
            "indexes": indexes,
        }
    return {"objects": objects, "tables": tables}


def _expected_schema_manifest() -> dict[str, Any]:
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("PRAGMA foreign_keys = ON")
        for statement in SCHEMA.split(";"):
            if statement.strip():
                connection.execute(statement)
        manifest = _schema_manifest(connection)
        digest = sha256_json(manifest)
        if digest != EXPECTED_SCHEMA_MANIFEST_SHA256:
            raise GateError(
                "SCHEMA source nie zgadza się z reviewowanym manifestem: "
                f"expected={EXPECTED_SCHEMA_MANIFEST_SHA256} actual={digest}"
            )
        return manifest
    finally:
        connection.close()


def _assert_exact_schema(connection: sqlite3.Connection) -> dict[str, Any]:
    actual = _schema_manifest(connection)
    expected = _expected_schema_manifest()
    if actual != expected:
        raise GateError(
            "niezgodny exact schema manifest SQLite: "
            f"expected={sha256_json(expected)} actual={sha256_json(actual)}"
        )
    return actual


class GateStore:
    """Jedyny interfejs zapisu i odczytu kanonicznej bazy."""

    def __init__(self, db_path: str | os.PathLike[str] = DEFAULT_DB):
        self.db_path = Path(db_path).expanduser()

    def _migration_checkpoint(self, step: str) -> None:
        """No-op seam dla fault-injection testu atomowej migracji v1→v2."""
        del step

    def _ledger_snapshot_checkpoint(self, table: str) -> None:
        """No-op seam dla deterministycznego testu współbieżnego eksportu."""
        del table

    def _migrate_legacy_v2_claims(self, connection: sqlite3.Connection) -> None:
        """Migruj wyłącznie pustą tabelę v2; cudzej authority nie interpretuj.

        Kontrakt v2 nie ma wystarczającej informacji, aby claim bezpiecznie
        wznowić albo rozliczyć jako v4. Niepusta tabela zatrzymuje CAŁĄ
        transakcję przed pierwszym DDL, dzięki czemu operator nadal ma
        niezmienioną bazę v2 i może wykonać osobną, jawną adjudykację.
        """

        count = int(connection.execute("SELECT COUNT(*) FROM at_job_claims").fetchone()[0])
        if count:
            raise GateError(
                "migracja v2→v4 zabroniona: at_job_claims nie jest puste "
                f"({count}); wymagana osobna adjudykacja bez automatycznej konwersji"
            )
        self._migration_checkpoint("legacy-claims-empty")
        connection.execute("ALTER TABLE at_job_claims RENAME TO at_job_claims_v2_legacy")
        connection.execute("DROP INDEX IF EXISTS at_job_claims_status")
        for statement in AT_JOB_CLAIMS_SCHEMA.split(";"):
            if statement.strip():
                connection.execute(statement)
        self._migration_checkpoint("claim-table-rebuild")
        connection.execute("DROP TABLE at_job_claims_v2_legacy")
        self._migration_checkpoint("legacy-claims-dropped")

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
            if version not in (0, 1, 2, DB_SCHEMA_VERSION):
                raise GateError(
                    "nieobsługiwana wersja schematu SQLite: "
                    f"{version}; oczekiwano 0, 1, 2 albo {DB_SCHEMA_VERSION}"
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
                connection.execute("BEGIN IMMEDIATE")
                # Niepusta legacy authority jest sprawdzana pod write lockiem
                # PRZED pierwszym CREATE/ALTER/DROP. Rollback samego DDL nie jest
                # wystarczającym oraclem — migrator nie może nawet próbować
                # interpretować cudzych claimów przed jawną adjudykacją.
                legacy_claim_table_exists = connection.execute(
                    "SELECT 1 FROM sqlite_master "
                    "WHERE type='table' AND name='at_job_claims'"
                ).fetchone()
                if version == 2 and legacy_claim_table_exists is not None:
                    legacy_claim_count = int(
                        connection.execute(
                            "SELECT COUNT(*) FROM at_job_claims"
                        ).fetchone()[0]
                    )
                    if legacy_claim_count:
                        raise GateError(
                            "migracja v2→v4 zabroniona: at_job_claims nie jest puste "
                            f"({legacy_claim_count}); wymagana osobna adjudykacja "
                            "bez automatycznej konwersji"
                        )
                for statement in SCHEMA.split(";"):
                    if statement.strip():
                        connection.execute(statement)
                if version < DB_SCHEMA_VERSION:
                    self._migration_checkpoint("schema-ddl")
                actual_claims = {
                    str(row[1])
                    for row in connection.execute(
                        "PRAGMA table_info(at_job_claims)"
                    ).fetchall()
                }
                expected_claims = REQUIRED_COLUMNS["at_job_claims"]
                if version == 2 and actual_claims == LEGACY_V2_CLAIM_COLUMNS:
                    self._migrate_legacy_v2_claims(connection)
                actual_at_jobs = {
                    str(row[1])
                    for row in connection.execute("PRAGMA table_info(at_jobs)").fetchall()
                }
                for column, definition in V2_AT_JOB_COLUMNS.items():
                    if column not in actual_at_jobs:
                        connection.execute(
                            f"ALTER TABLE at_jobs ADD COLUMN {column} {definition}"
                        )
                        self._migration_checkpoint(f"at_jobs.{column}")
                for table, expected in REQUIRED_COLUMNS.items():
                    actual = {
                        str(row[1])
                        for row in connection.execute(
                            f"PRAGMA table_info({table})"
                        ).fetchall()
                    }
                    missing = sorted(expected - actual)
                    if missing:
                        raise GateError(
                            f"niezgodny schemat {table}; brak kolumn: "
                            + ", ".join(missing)
                        )
                    unexpected = sorted(actual - expected)
                    if unexpected:
                        raise GateError(
                            f"niezgodny schemat {table}; nadmiarowe kolumny: "
                            + ", ".join(unexpected)
                        )
                _assert_exact_schema(connection)
                if version < DB_SCHEMA_VERSION:
                    self._migration_checkpoint("validated")
                connection.execute(f"PRAGMA user_version = {DB_SCHEMA_VERSION}")
                connection.commit()
            except Exception as exc:
                try:
                    connection.rollback()
                except sqlite3.Error as rollback_exc:
                    raise StorageError(
                        f"błąd rollback migracji SQLite: {rollback_exc}"
                    ) from exc
                if isinstance(exc, GateError):
                    raise
                if isinstance(exc, sqlite3.Error):
                    raise StorageError(f"błąd migracji SQLite: {exc}") from exc
                raise GateError(f"niezgodny schemat SQLite: {exc}") from exc
        try:
            os.chmod(self.db_path, 0o600)
        except PermissionError:
            pass

    @contextmanager
    def _write_connection(self) -> Iterator[sqlite3.Connection]:
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(
                self.db_path,
                timeout=15.0,
                isolation_level=None,
            )
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("PRAGMA journal_mode = WAL")
            connection.execute("PRAGMA synchronous = FULL")
            connection.execute("PRAGMA busy_timeout = 15000")
            try:
                yield connection
            except sqlite3.Error as exc:
                raise StorageError(f"błąd zapisu SQLite: {exc}") from exc
        except sqlite3.Error as exc:
            raise StorageError(f"błąd otwarcia SQLite do zapisu: {exc}") from exc
        finally:
            if connection is not None:
                had_pending_error = sys.exc_info()[0] is not None
                try:
                    connection.close()
                except sqlite3.Error as exc:
                    if not had_pending_error:
                        raise StorageError(f"błąd zamknięcia SQLite: {exc}") from exc

    @contextmanager
    def _read_connection(self) -> Iterator[sqlite3.Connection]:
        if not self.db_path.is_file():
            raise GateNotFound(f"baza nie istnieje: {self.db_path}")
        uri = self.db_path.resolve().as_uri() + "?mode=ro"
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(uri, uri=True, timeout=5.0)
            connection.row_factory = sqlite3.Row
            connection.execute("PRAGMA query_only = ON")
            try:
                yield connection
            except sqlite3.Error as exc:
                raise StorageError(f"błąd odczytu SQLite: {exc}") from exc
        except sqlite3.Error as exc:
            raise StorageError(f"błąd otwarcia SQLite do odczytu: {exc}") from exc
        finally:
            if connection is not None:
                had_pending_error = sys.exc_info()[0] is not None
                try:
                    connection.close()
                except sqlite3.Error as exc:
                    if not had_pending_error:
                        raise StorageError(f"błąd zamknięcia SQLite: {exc}") from exc

    @staticmethod
    def _row_to_gate(row: sqlite3.Row) -> dict[str, Any]:
        result = {column: row[column] for column in PUBLIC_GATE_COLUMNS}
        result["alarm"] = bool(result["alarm"])
        result["metadata"] = json.loads(row["metadata_json"])
        return result

    @staticmethod
    def _row_to_job(row: sqlite3.Row) -> dict[str, Any]:
        result = {column: row[column] for column in INTERNAL_AT_JOB_COLUMNS}
        result["command"] = json.loads(row["command_json"])
        return result

    @staticmethod
    def _row_to_claim(row: sqlite3.Row) -> dict[str, Any]:
        keys = set(row.keys()) if hasattr(row, "keys") else set(row)
        result = {
            column: row[column]
            for column in INTERNAL_AT_CLAIM_COLUMNS
            if column in keys
        }
        result["binding"] = json.loads(row["binding_json"])
        return result

    @staticmethod
    def _event_snapshot(row: Mapping[str, Any]) -> str:
        snapshot = dict(row)
        if "metadata_json" in snapshot:
            snapshot["metadata"] = json.loads(snapshot.pop("metadata_json"))
        snapshot["alarm"] = bool(snapshot.get("alarm", False))
        return canonical_json(snapshot)

    @staticmethod
    def _freshness(events: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
        latest_transition: Mapping[str, Any] | None = None
        latest_note: Mapping[str, Any] | None = None
        for event in events:
            if event.get("from_state") == event.get("to_state"):
                latest_note = event
            else:
                latest_transition = event
        transition_id = (
            int(latest_transition["event_id"]) if latest_transition is not None else 0
        )
        note_id = int(latest_note["event_id"]) if latest_note is not None else 0
        fresh = latest_note is not None and note_id > transition_id
        return {
            "has_fresh_note": fresh,
            "latest_event_at": (
                str(events[-1]["occurred_at"]) if events else None
            ),
            "latest_transition_at": (
                str(latest_transition["occurred_at"])
                if latest_transition is not None
                else None
            ),
            "latest_note_at": (
                str(latest_note["occurred_at"]) if latest_note is not None else None
            ),
            "latest_note_actor": (
                str(latest_note["actor"]) if latest_note is not None else None
            ),
            "latest_note_reason": (
                str(latest_note["reason"]) if latest_note is not None else None
            ),
        }

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
        except StorageError as exc:
            cause = exc.__cause__
            if isinstance(cause, sqlite3.IntegrityError):
                if "gates.gate_id" in str(cause) or (
                    "UNIQUE constraint failed: gates.gate_id" in str(cause)
                ):
                    raise GateAlreadyExists(
                        f"rekord już istnieje: {gate_id}"
                    ) from cause
                raise GateError(f"nie udało się dodać rekordu: {cause}") from cause
            raise
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
            active_run = self._active_run_claim_for_gate(connection, gate_id)
            if active_run is not None:
                connection.rollback()
                raise IllegalTransition(
                    "bramka jest zamrożona przez aktywny RUN claim "
                    f"{active_run['claim_id']} ({active_run['status']}); "
                    "najpierw rozlicz dokładnie ten claim"
                )
            if to_state not in ALLOWED_TRANSITIONS[current_state]:
                connection.rollback()
                allowed = ", ".join(sorted(ALLOWED_TRANSITIONS[current_state])) or "brak"
                raise IllegalTransition(
                    f"niedozwolone {current_state} -> {to_state}; dozwolone: {allowed}"
                )
            if bool(row["alarm"]) and to_state not in {"REJECTED", "SUPERSEDED"}:
                connection.rollback()
                raise IllegalTransition(
                    "bramka z ALARM nie może być promowana ani zamknięta; "
                    "najpierw usuń przyczynę albo zakończ REJECTED/SUPERSEDED"
                )
            if to_state in TERMINAL_STATES:
                active_job = connection.execute(
                    """
                    SELECT j.job_key, j.status, c.status AS claim_status
                    FROM at_jobs AS j
                    LEFT JOIN at_job_claims AS c ON c.job_key = j.job_key
                    WHERE j.gate_id = ?
                      AND (
                        j.status IN ('SUBMITTING', 'SCHEDULED', 'MISSING_ALARM')
                        OR c.status IN (
                            'CLAIMED', 'RECEIPT_READY',
                            'OUTCOME_UNKNOWN'
                        )
                      )
                    ORDER BY j.job_key
                    LIMIT 1
                    """,
                    (gate_id,),
                ).fetchone()
                if active_job is not None:
                    connection.rollback()
                    raise IllegalTransition(
                        "terminalne zamknięcie wymaga wcześniejszego exact "
                        "at_gate cancel/finalize; aktywny scheduler intent "
                        f"{active_job['job_key']} status={active_job['status']} "
                        f"claim={active_job['claim_status'] or 'BRAK'}"
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

    def note(
        self,
        gate_id: str,
        *,
        expected_version: int,
        actor: str,
        reason: str,
        next_step: str | None = None,
        blocker: str | None = None,
        code_sha: str | None = None,
        evidence_hash: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Dopisz audytowaną adnotację CAS bez przejścia automatu stanów."""
        gate_id = _validate_gate_id(gate_id)
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
            if int(row["version"]) != expected_version:
                connection.rollback()
                raise CASConflict(
                    f"CAS konflikt {gate_id}: oczekiwano v{expected_version}, "
                    f"jest v{row['version']}"
                )
            active_run = self._active_run_claim_for_gate(connection, gate_id)
            if active_run is not None:
                connection.rollback()
                raise IllegalTransition(
                    "bramka jest zamrożona przez aktywny RUN claim "
                    f"{active_run['claim_id']} ({active_run['status']}); "
                    "notatka operatorska może zostać zapisana po rozliczeniu claimu"
                )
            state = str(row["state"])
            updates = {
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
            }
            cursor = connection.execute(
                """
                UPDATE gates
                SET next_step = ?, blocker = ?, code_sha = ?, evidence_hash = ?,
                    version = version + 1, updated_at = ?
                WHERE gate_id = ? AND version = ?
                """,
                (
                    updates["next_step"],
                    updates["blocker"],
                    updates["code_sha"],
                    updates["evidence_hash"],
                    timestamp,
                    gate_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise CASConflict(f"CAS konflikt podczas zapisu notatki: {gate_id}")
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
                    state,
                    state,
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
        gate["freshness"] = self._freshness(gate["events"])
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
            gates = [self._row_to_gate(row) for row in rows]
            if gates:
                gate_ids = [str(gate["gate_id"]) for gate in gates]
                placeholders = ",".join("?" for _ in gate_ids)
                event_rows = connection.execute(
                    f"""
                    SELECT event_id, gate_id, from_state, to_state, actor,
                           reason, occurred_at
                    FROM gate_events
                    WHERE gate_id IN ({placeholders})
                    ORDER BY gate_id, event_id
                    """,
                    gate_ids,
                ).fetchall()
            else:
                event_rows = []
        events_by_gate: dict[str, list[dict[str, Any]]] = {
            str(gate["gate_id"]): [] for gate in gates
        }
        for event in event_rows:
            events_by_gate[str(event["gate_id"])].append(dict(event))
        for gate in gates:
            gate["freshness"] = self._freshness(
                events_by_gate[str(gate["gate_id"])]
            )
        return gates

    def register_at_job(
        self,
        *,
        gate_id: str,
        title: str,
        owner: str,
        due_at: str,
        blocker: str,
        code_sha: str,
        evidence_hash: str,
        opened_at: str | None,
        actor: str,
        job_key: str,
        runner_token_hash: str,
        scheduled_for: str,
        command: Sequence[str],
        runner_auth_hmac: str,
        payload_path: str,
        payload_identity: Mapping[str, Any],
        artifact_root: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        gate_id = _validate_gate_id(gate_id)
        title = _required_text(title, "title")
        owner = _required_text(owner, "owner")
        due_at = iso_utc(parse_timestamp(due_at, "due_at"))
        blocker = _required_text(blocker, "blocker")
        code_sha = _validate_code_sha(code_sha)
        evidence_hash = _validate_evidence_hash(evidence_hash)
        actor = _required_text(actor, "actor")
        job_key = _required_text(job_key, "job_key")
        runner_token_hash = _validate_evidence_hash(runner_token_hash)
        scheduled_for = iso_utc(parse_timestamp(scheduled_for, "scheduled_for"))
        command_sha256 = canonical_argv_hash(command)
        auth_version = SEALED_AUTH_VERSION
        runner_auth_hmac = _validate_evidence_hash(runner_auth_hmac)
        payload_path = _required_text(payload_path, "payload_path")
        if not Path(payload_path).is_absolute():
            raise ValidationError("payload_path musi być absolutna")
        identity = _validated_identity(payload_identity, "payload_identity")
        artifact_root = _required_text(artifact_root, "artifact_root")
        if not Path(artifact_root).is_absolute():
            raise ValidationError("artifact_root musi być absolutna")
        timestamp = iso_utc(now or utc_now())
        opened = (
            iso_utc(parse_timestamp(opened_at, "opened_at"))
            if opened_at
            else timestamp
        )
        gate_metadata = _metadata_json(
            {
                "scheduled_for": scheduled_for,
                "command_sha256": command_sha256,
                "runner_contract": "sealed-payload-v2-claim-receipt-fsm",
                "artifact_root": artifact_root,
            }
        )
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
                    ) VALUES (
                        ?, ?, 'AT_JOB', 'BUILT_OFF', ?, ?,
                        'Zaplanuj job wyłącznie przez at_gate.py', ?, ?, ?,
                        1, 0, '', ?, ?, ?, NULL, ?
                    )
                    """,
                    (
                        gate_id,
                        title,
                        owner,
                        due_at,
                        blocker,
                        code_sha,
                        evidence_hash,
                        opened,
                        timestamp,
                        timestamp,
                        gate_metadata,
                    ),
                )
                gate = connection.execute(
                    "SELECT * FROM gates WHERE gate_id = ?", (gate_id,)
                ).fetchone()
                assert gate is not None
                connection.execute(
                    """
                    INSERT INTO gate_events (
                        gate_id, from_state, to_state, expected_version,
                        result_version, actor, reason, occurred_at, snapshot_json
                    ) VALUES (?, NULL, 'BUILT_OFF', NULL, 1, ?, ?, ?, ?)
                    """,
                    (
                        gate_id,
                        actor,
                        "atomowe utworzenie bramki i sealed at-intent",
                        timestamp,
                        self._event_snapshot(gate),
                    ),
                )
                connection.execute(
                    """
                    INSERT INTO at_jobs (
                        job_key, gate_id, at_job_id, status, scheduled_for,
                        command_json, runner_token_hash, created_at, updated_at,
                        auth_version, runner_auth_tag, command_sha256,
                        payload_path, payload_sha256, payload_dev, payload_ino,
                        payload_ctime_ns, payload_size, artifact_root
                    ) VALUES (?, ?, NULL, 'SUBMITTING', ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_key,
                        gate_id,
                        scheduled_for,
                        canonical_json(
                            {
                                "argv_sha256": command_sha256,
                                "argc": len(command),
                            }
                        ),
                        runner_token_hash,
                        timestamp,
                        timestamp,
                        auth_version,
                        runner_auth_hmac,
                        command_sha256,
                        payload_path,
                        identity["sha256"],
                        identity["device"],
                        identity["inode"],
                        identity["ctime_ns"],
                        identity["size"],
                        artifact_root,
                    ),
                )
                connection.commit()
        except StorageError as exc:
            cause = exc.__cause__
            if isinstance(cause, sqlite3.IntegrityError):
                if "gates.gate_id" in str(cause) or (
                    "UNIQUE constraint failed: gates.gate_id" in str(cause)
                ):
                    raise GateAlreadyExists(
                        f"rekord już istnieje: {gate_id}"
                    ) from cause
                raise GateError(
                    f"nie udało się zarejestrować intencji at: {cause}"
                ) from cause
            raise
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
            if (
                job["status"] == "SCHEDULED"
                and str(job["at_job_id"] or "") == at_job_id
            ):
                connection.rollback()
                return self.show_at_job(job_key)
            if job["status"] != "SUBMITTING":
                connection.rollback()
                raise GateError(f"at job {job_key} nie jest w stanie SUBMITTING")
            if str(job["reconcile_note"] or "").startswith("EARLY_RUNNER_ABORTED:"):
                connection.rollback()
                raise ClaimConflict(
                    "runner wystartował przed confirm; SCHEDULED commit zabroniony, "
                    "wymagany logiczny submission CANCEL"
                )
            existing_claim = connection.execute(
                "SELECT claim_id FROM at_job_claims WHERE job_key = ?",
                (job_key,),
            ).fetchone()
            if existing_claim is not None:
                connection.rollback()
                raise ClaimConflict(
                    "confirm jest zabroniony po ustanowieniu authority claimu"
                )
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

    @staticmethod
    def _stored_command_hash(job: Mapping[str, Any]) -> str:
        value = str(job["command_sha256"] or "")
        if not value:
            try:
                value = str(json.loads(str(job["command_json"]))["argv_sha256"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
                raise ValidationError("at job nie ma kanonicznego argv_sha256") from exc
        return _validate_evidence_hash(value)

    @staticmethod
    def _active_run_claim_for_gate(
        connection: sqlite3.Connection,
        gate_id: str,
    ) -> sqlite3.Row | None:
        """Zwróć claim trzymający niepodzielną authority gate→child.

        CLAIMED i RECEIPT_READY są fazami jednego wykonania, w których publiczne
        ``transition``/``note`` nie mogą zmienić snapshotu gate. Wewnętrzne
        finalizery rozliczają claim i dopiero wtedy oddają authority operatorowi.
        OUTCOME_UNKNOWN nie uruchamia już childa i musi pozostać możliwy do
        jawnej adjudykacji operatorskiej pod istniejącym ALARM-em.
        """

        return connection.execute(
            """
            SELECT c.claim_id, c.status, c.job_key
            FROM at_job_claims AS c
            JOIN at_jobs AS j ON j.job_key = c.job_key AND j.gate_id = c.gate_id
            WHERE c.gate_id = ?
              AND c.status IN ('CLAIMED', 'RECEIPT_READY')
              AND json_extract(c.binding_json, '$.operation') = 'RUN'
            ORDER BY c.claimed_at, c.claim_id
            LIMIT 1
            """,
            (gate_id,),
        ).fetchone()

    @staticmethod
    def _claim_binding(claim: Mapping[str, Any]) -> dict[str, Any]:
        try:
            binding = json.loads(str(claim["binding_json"]))
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ClaimConflict("claim ma niepoprawny binding JSON") from exc
        if not isinstance(binding, dict):
            raise ClaimConflict("claim binding nie jest obiektem")
        if str(claim["binding_sha256"]) != sha256_json(binding):
            raise ClaimConflict("claim binding digest mismatch")
        return binding

    @classmethod
    def _validate_run_claim_binding(
        cls,
        claim: Mapping[str, Any],
        job: Mapping[str, Any],
        stored_command_hash: str,
    ) -> dict[str, Any]:
        binding = cls._claim_binding(claim)
        expected_keys = {
            "schema_version",
            "operation",
            "claim_id",
            "job_key",
            "gate_id",
            "at_job_id",
            "command_sha256",
            "payload_sha256",
            "artifact_root",
            "receipt_path",
            "gate_state",
            "gate_version",
            "gate_code_sha",
            "gate_evidence_hash",
            "gate_alarm",
            "gate_alarm_reason",
            "gate_blocker",
            "gate_next_step",
        }
        if set(binding) != expected_keys:
            raise ClaimConflict("RUN claim binding shape mismatch")
        try:
            expected_receipt_path = claim_receipt_path(
                artifact_root=str(job["artifact_root"] or ""),
                job_key=str(job["job_key"]),
                claim_id=str(claim["claim_id"]),
            )
        except ValidationError as exc:
            raise ClaimConflict("RUN claim ma niekanoniczne drzewo receiptu") from exc
        if (
            binding.get("schema_version") != CLAIM_BINDING_VERSION
            or binding.get("operation") != "RUN"
            or binding.get("claim_id") != claim["claim_id"]
            or binding.get("job_key") != job["job_key"]
            or binding.get("gate_id") != job["gate_id"]
            or binding.get("at_job_id") != str(job["at_job_id"] or "")
            or binding.get("command_sha256") != stored_command_hash
            or binding.get("payload_sha256") != str(job["payload_sha256"] or "")
            or binding.get("artifact_root") != str(job["artifact_root"] or "")
            or binding.get("receipt_path") != str(claim["receipt_path"] or "")
            or str(claim["receipt_path"] or "") != expected_receipt_path
            or binding.get("gate_state") not in ALL_STATES
            or not isinstance(binding.get("gate_version"), int)
            or binding.get("gate_alarm") is not False
            or not isinstance(binding.get("gate_alarm_reason"), str)
            or not isinstance(binding.get("gate_blocker"), str)
            or not isinstance(binding.get("gate_next_step"), str)
        ):
            raise ClaimConflict("RUN claim binding identity mismatch")
        try:
            _validate_code_sha(str(binding["gate_code_sha"]))
            _validate_evidence_hash(str(binding["gate_evidence_hash"]))
        except ValidationError as exc:
            raise ClaimConflict("RUN claim gate snapshot mismatch") from exc
        return binding

    def claim_at_job(
        self,
        job_key: str,
        *,
        runner_token: str,
        command: Sequence[str],
        payload_path: str | None = None,
        payload_identity: Mapping[str, Any] | None = None,
        artifact_root: str | None = None,
        require_auth_version: int | None = None,
        claim_id: str | None = None,
        receipt_path: str | None = None,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Atomowo przyznaj jedyny RUN claim przed jakimkolwiek subprocess."""

        job_key = _required_text(job_key, "job_key")
        runner_token = _required_text(runner_token, "runner_token")
        supplied_command_hash = canonical_argv_hash(command)
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
            stored_command_hash = self._stored_command_hash(job)
            if not hmac.compare_digest(supplied_command_hash, stored_command_hash):
                connection.rollback()
                raise ValidationError("command identity mismatch przed subprocess")
            auth_version = int(job["auth_version"] or 1)
            if require_auth_version is not None and auth_version != require_auth_version:
                connection.rollback()
                raise ValidationError(
                    f"wymagany auth_version={require_auth_version}, jest {auth_version}"
                )
            payload_sha = str(job["payload_sha256"] or "")
            stored_artifact_root = str(job["artifact_root"] or "")
            selected_artifact_root = str(
                Path(
                    artifact_root
                    or stored_artifact_root
                    or (self.db_path.resolve().parent / "at-results")
                ).absolute()
            )
            if artifact_root is not None and str(Path(artifact_root).absolute()) != artifact_root:
                connection.rollback()
                raise ValidationError("artifact_root musi być kanoniczną ścieżką absolutną")
            if auth_version == 2:
                if not stored_artifact_root or selected_artifact_root != stored_artifact_root:
                    connection.rollback()
                    raise ValidationError("artifact_root nie zgadza się z rejestracją")
                expected_path = str(job["payload_path"] or "")
                if not payload_path or str(Path(payload_path).absolute()) != expected_path:
                    connection.rollback()
                    raise ValidationError("payload path nie zgadza się z rejestracją")
                identity = _validated_identity(payload_identity, "payload_identity")
                expected_identity = {
                    "sha256": _validate_evidence_hash(payload_sha),
                    "device": int(job["payload_dev"]),
                    "inode": int(job["payload_ino"]),
                    "ctime_ns": int(job["payload_ctime_ns"]),
                    "size": int(job["payload_size"]),
                }
                if identity != expected_identity:
                    connection.rollback()
                    raise ValidationError("payload identity nie zgadza się z rejestracją")
                auth_binding = runner_auth_binding(
                    job_key=job_key,
                    gate_id=str(job["gate_id"]),
                    scheduled_for=str(job["scheduled_for"]),
                    command_sha256=stored_command_hash,
                    payload_sha256=payload_sha,
                    artifact_root=selected_artifact_root,
                )
                expected_tag = _validate_evidence_hash(str(job["runner_auth_tag"] or ""))
                if not hmac.compare_digest(
                    runner_auth_tag(runner_token, auth_binding), expected_tag
                ):
                    connection.rollback()
                    raise ValidationError("sealed payload HMAC nie zgadza się")
            elif payload_path is not None or payload_identity is not None:
                connection.rollback()
                raise ValidationError("auth v1 nie przyjmuje sealed payload")
            existing = connection.execute(
                "SELECT * FROM at_job_claims WHERE job_key = ?", (job_key,)
            ).fetchone()
            if existing is not None:
                connection.rollback()
                raise ClaimConflict(
                    f"at job ma już claim {existing['status']}; re-exec zabroniony"
                )
            if job["status"] == "SUBMITTING":
                early_marker = f"EARLY_RUNNER_ABORTED:{timestamp}"
                cursor = connection.execute(
                    """
                    UPDATE at_jobs SET reconcile_note = ?, updated_at = ?
                    WHERE job_key = ? AND status = 'SUBMITTING'
                    """,
                    (early_marker, timestamp, job_key),
                )
                if cursor.rowcount != 1:
                    connection.rollback()
                    raise CASConflict("SUBMITTING zmienił się podczas early-run marker")
                connection.commit()
                raise GateError(
                    "at job wystartował przed confirm; child zablokowany, "
                    "schedule musi wykonać logiczny CANCEL"
                )
            if job["status"] != "SCHEDULED":
                connection.rollback()
                raise GateError(f"at job nie jest gotowy do RUN claim: {job['status']}")
            if parse_timestamp(timestamp) < parse_timestamp(
                str(job["scheduled_for"]), "scheduled_for"
            ):
                connection.rollback()
                raise IllegalTransition("RUN claim przed scheduled_for jest zabroniony")
            gate = connection.execute(
                "SELECT * FROM gates WHERE gate_id = ?", (job["gate_id"],)
            ).fetchone()
            if gate is None:
                connection.rollback()
                raise GateNotFound(f"brak bramki dla at job: {job['gate_id']}")
            if str(gate["state"]) in TERMINAL_STATES or bool(gate["alarm"]):
                connection.rollback()
                raise IllegalTransition(
                    f"pre-exec wymaga aktywnej bramki bez ALARM; "
                    f"jest {gate['state']} alarm={int(bool(gate['alarm']))}"
                )
            selected_claim_id = (
                _required_text(claim_id, "claim_id")
                if claim_id is not None
                else f"claim-{uuid.uuid4().hex}"
            )
            if not re.fullmatch(r"claim-[0-9a-f]{32}", selected_claim_id):
                connection.rollback()
                raise ValidationError("claim_id ma niekanoniczny format")
            expected_receipt_path = claim_receipt_path(
                artifact_root=selected_artifact_root,
                job_key=job_key,
                claim_id=selected_claim_id,
            )
            if receipt_path is not None and str(Path(receipt_path).absolute()) != expected_receipt_path:
                connection.rollback()
                raise ValidationError("receipt_path nie zgadza się z kanonicznym claimem")
            selected_receipt_path = expected_receipt_path
            binding = {
                "schema_version": CLAIM_BINDING_VERSION,
                "operation": "RUN",
                "claim_id": selected_claim_id,
                "job_key": job_key,
                "gate_id": str(job["gate_id"]),
                "at_job_id": str(job["at_job_id"] or ""),
                "command_sha256": stored_command_hash,
                "payload_sha256": payload_sha,
                "artifact_root": selected_artifact_root,
                "receipt_path": selected_receipt_path,
                "gate_state": str(gate["state"]),
                "gate_version": int(gate["version"]),
                "gate_code_sha": str(gate["code_sha"]),
                "gate_evidence_hash": str(gate["evidence_hash"]),
                "gate_alarm": bool(gate["alarm"]),
                "gate_alarm_reason": str(gate["alarm_reason"] or ""),
                "gate_blocker": str(gate["blocker"]),
                "gate_next_step": str(gate["next_step"]),
            }
            connection.execute(
                """
                INSERT INTO at_job_claims (
                    claim_id, job_key, gate_id, status, binding_json,
                    binding_sha256, receipt_path, claimed_at, updated_at
                ) VALUES (?, ?, ?, 'CLAIMED', ?, ?, ?, ?, ?)
                """,
                (
                    selected_claim_id,
                    job_key,
                    job["gate_id"],
                    canonical_json(binding),
                    sha256_json(binding),
                    selected_receipt_path,
                    timestamp,
                    timestamp,
                ),
            )
            if not stored_artifact_root:
                connection.execute(
                    "UPDATE at_jobs SET artifact_root = ?, updated_at = ? WHERE job_key = ?",
                    (selected_artifact_root, timestamp, job_key),
                )
            connection.commit()
        return self.show_at_claim(job_key)

    def mark_run_outcome_unknown(
        self,
        job_key: str,
        *,
        claim_id: str,
        reason: str,
        actor: str = "at_gate/run",
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Zatrzaśnij ALARM, gdy child powstał, lecz pełny wynik nie istnieje."""

        job_key = _required_text(job_key, "job_key")
        claim_id = _required_text(claim_id, "claim_id")
        reason = _required_text(reason, "reason")
        actor = _required_text(actor, "actor")
        timestamp = iso_utc(now or utc_now())
        self.initialize()
        with self._write_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            job = connection.execute(
                "SELECT * FROM at_jobs WHERE job_key = ?", (job_key,)
            ).fetchone()
            claim = connection.execute(
                "SELECT * FROM at_job_claims WHERE job_key = ?", (job_key,)
            ).fetchone()
            if job is None or claim is None or str(claim["claim_id"]) != claim_id:
                connection.rollback()
                raise ClaimConflict("OUTCOME_UNKNOWN nie ma exact RUN claim_id")
            binding = self._validate_run_claim_binding(
                claim,
                job,
                self._stored_command_hash(job),
            )
            gate = connection.execute(
                "SELECT * FROM gates WHERE gate_id = ?", (job["gate_id"],)
            ).fetchone()
            assert gate is not None
            if gate["state"] in TERMINAL_STATES:
                connection.rollback()
                raise IllegalTransition(
                    "RUN OUTCOME_UNKNOWN nie może modyfikować terminalnej bramki"
                )
            canonical_reason = f"{outcome_unknown_reason('RUN')}; {reason}"
            if claim["status"] == "OUTCOME_UNKNOWN":
                already = bool(gate["alarm"]) and canonical_reason in str(
                    gate["alarm_reason"] or ""
                )
                connection.rollback()
                if not already:
                    raise ClaimConflict("OUTCOME_UNKNOWN claim nie ma exact ALARM")
                return self.show_at_claim(job_key)
            if claim["status"] != "CLAIMED":
                connection.rollback()
                raise ClaimConflict(
                    f"OUTCOME_UNKNOWN wymaga CLAIMED, jest {claim['status']}"
                )
            binding_still_current = (
                str(gate["state"]) == str(binding["gate_state"])
                and int(gate["version"]) == int(binding["gate_version"])
                and str(gate["code_sha"]) == str(binding["gate_code_sha"])
                and str(gate["evidence_hash"]) == str(binding["gate_evidence_hash"])
            )
            gate_version = int(gate["version"])
            existing_alarm = str(gate["alarm_reason"] or "")
            combined = (
                existing_alarm
                if canonical_reason in existing_alarm
                else (
                    f"{existing_alarm} | SCHEDULER: {canonical_reason}"
                    if existing_alarm
                    else canonical_reason
                )
            )
            claim_cursor = connection.execute(
                """
                UPDATE at_job_claims SET status = 'OUTCOME_UNKNOWN', updated_at = ?
                WHERE claim_id = ? AND status = 'CLAIMED'
                """,
                (timestamp, claim_id),
            )
            connection.execute(
                """
                UPDATE at_jobs SET status = 'MISSING_ALARM', updated_at = ?,
                    reconcile_note = ? WHERE job_key = ?
                    AND status IN ('SCHEDULED', 'MISSING_ALARM')
                """,
                (timestamp, canonical_reason, job_key),
            )
            gate_cursor = connection.execute(
                """
                UPDATE gates SET alarm = 1, alarm_reason = ?,
                    blocker = CASE WHEN ? THEN ? ELSE blocker END,
                    next_step = CASE WHEN ?
                        THEN 'Rozlicz claim bez ponownego wykonania'
                        ELSE next_step END,
                    version = version + 1, updated_at = ?
                WHERE gate_id = ? AND version = ?
                """,
                (
                    combined,
                    int(binding_still_current),
                    canonical_reason,
                    int(binding_still_current),
                    timestamp,
                    job["gate_id"],
                    gate_version,
                ),
            )
            if claim_cursor.rowcount != 1 or gate_cursor.rowcount != 1:
                connection.rollback()
                raise CASConflict("RUN OUTCOME_UNKNOWN zmienił się podczas zapisu")
            updated = connection.execute(
                "SELECT * FROM gates WHERE gate_id = ?", (job["gate_id"],)
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
                    job["gate_id"],
                    gate["state"],
                    gate["state"],
                    gate_version,
                    int(updated["version"]),
                    actor,
                    canonical_reason,
                    timestamp,
                    self._event_snapshot(updated),
                ),
            )
            connection.commit()
        return self.show_at_claim(job_key)

    def record_at_receipt(
        self,
        job_key: str,
        *,
        claim_id: str,
        receipt_path: str,
        receipt_identity: Mapping[str, Any],
        exit_code: int,
        stdout_sha256: str,
        stderr_sha256: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        job_key = _required_text(job_key, "job_key")
        claim_id = _required_text(claim_id, "claim_id")
        receipt_path = _required_text(receipt_path, "receipt_path")
        if not Path(receipt_path).is_absolute():
            raise ValidationError("receipt_path musi być absolutna")
        receipt = _validated_identity(receipt_identity, "receipt_identity")
        if not isinstance(exit_code, int) or isinstance(exit_code, bool):
            raise ValidationError("exit_code musi być liczbą całkowitą")
        stdout_sha256 = _validate_evidence_hash(stdout_sha256)
        stderr_sha256 = _validate_evidence_hash(stderr_sha256)
        timestamp = iso_utc(now or utc_now())
        self.initialize()
        with self._write_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            claim = connection.execute(
                "SELECT * FROM at_job_claims WHERE job_key = ?", (job_key,)
            ).fetchone()
            job = connection.execute(
                "SELECT * FROM at_jobs WHERE job_key = ?", (job_key,)
            ).fetchone()
            if job is None or claim is None or str(claim["claim_id"]) != claim_id:
                connection.rollback()
                raise ClaimConflict("receipt nie ma exact RUN claim_id")
            self._validate_run_claim_binding(
                claim,
                job,
                self._stored_command_hash(job),
            )
            if str(claim["receipt_path"]) != receipt_path:
                connection.rollback()
                raise ReceiptError("receipt path nie zgadza się z claimem")
            loaded_receipt, observed_identity, _stdout, _stderr = load_claim_receipt(
                receipt_path,
                claim=self._row_to_claim(claim),
            )
            if observed_identity != receipt:
                connection.rollback()
                raise ReceiptError("receipt identity zmieniło się przed zapisem DB")
            if (
                int(loaded_receipt["exit_code"]) != exit_code
                or str(loaded_receipt["stdout"]["sha256"]) != stdout_sha256
                or str(loaded_receipt["stderr"]["sha256"]) != stderr_sha256
            ):
                connection.rollback()
                raise ReceiptError("receipt wynik nie zgadza się z argumentami DB")
            if claim["status"] == "RECEIPT_READY":
                exact = (
                    str(claim["receipt_sha256"]) == receipt["sha256"]
                    and int(claim["receipt_dev"]) == receipt["device"]
                    and int(claim["receipt_ino"]) == receipt["inode"]
                    and int(claim["receipt_ctime_ns"]) == receipt["ctime_ns"]
                    and int(claim["receipt_size"]) == receipt["size"]
                    and int(claim["exit_code"]) == exit_code
                    and str(claim["stdout_sha256"]) == stdout_sha256
                    and str(claim["stderr_sha256"]) == stderr_sha256
                )
                connection.rollback()
                if not exact:
                    raise ReceiptError("drugi receipt różni się od zapisanego")
                return self.show_at_claim(job_key)
            if claim["status"] not in {"CLAIMED", "OUTCOME_UNKNOWN"}:
                connection.rollback()
                raise ClaimConflict(
                    f"claim ma stan {claim['status']}, nie CLAIMED/OUTCOME_UNKNOWN"
                )
            cursor = connection.execute(
                """
                UPDATE at_job_claims SET status = 'RECEIPT_READY',
                    receipt_sha256 = ?, receipt_dev = ?, receipt_ino = ?,
                    receipt_ctime_ns = ?, receipt_size = ?, exit_code = ?,
                    stdout_sha256 = ?, stderr_sha256 = ?, updated_at = ?
                WHERE claim_id = ? AND status IN ('CLAIMED', 'OUTCOME_UNKNOWN')
                """,
                (
                    receipt["sha256"],
                    receipt["device"],
                    receipt["inode"],
                    receipt["ctime_ns"],
                    receipt["size"],
                    exit_code,
                    stdout_sha256,
                    stderr_sha256,
                    timestamp,
                    claim_id,
                ),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise CASConflict("receipt claim zmienił się podczas zapisu")
            connection.commit()
        return self.show_at_claim(job_key)

    def finalize_at_claim(
        self,
        job_key: str,
        *,
        claim_id: str,
        receipt_identity: Mapping[str, Any],
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Atomowo rozlicz exact trwały receipt; nigdy nie uruchamiaj childa."""

        job_key = _required_text(job_key, "job_key")
        claim_id = _required_text(claim_id, "claim_id")
        receipt = _validated_identity(receipt_identity, "receipt_identity")
        timestamp = iso_utc(now or utc_now())
        self.initialize()
        with self._write_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            job = connection.execute(
                "SELECT * FROM at_jobs WHERE job_key = ?", (job_key,)
            ).fetchone()
            claim = connection.execute(
                "SELECT * FROM at_job_claims WHERE job_key = ?", (job_key,)
            ).fetchone()
            if job is None or claim is None or str(claim["claim_id"]) != claim_id:
                connection.rollback()
                raise ClaimConflict("finalize nie ma exact RUN claim_id")
            if claim["status"] not in {"RECEIPT_READY", "FINALIZED"}:
                connection.rollback()
                raise ReceiptError("finalize wymaga stanu RECEIPT_READY/FINALIZED")
            try:
                expected_receipt = {
                    "sha256": _validate_evidence_hash(str(claim["receipt_sha256"])),
                    "device": int(claim["receipt_dev"]),
                    "inode": int(claim["receipt_ino"]),
                    "ctime_ns": int(claim["receipt_ctime_ns"]),
                    "size": int(claim["receipt_size"]),
                }
            except (TypeError, ValueError, ValidationError) as exc:
                connection.rollback()
                raise ReceiptError("finalize nie ma kompletnego RUN receiptu") from exc
            if receipt != expected_receipt:
                connection.rollback()
                raise ReceiptError("receipt identity zmieniło się przed finalize")
            stored_command_hash = self._stored_command_hash(job)
            binding = self._validate_run_claim_binding(
                claim,
                job,
                stored_command_hash,
            )
            loaded_receipt, observed_identity, _stdout, _stderr = load_claim_receipt(
                str(claim["receipt_path"]),
                claim=self._row_to_claim(claim),
            )
            if observed_identity != receipt:
                connection.rollback()
                raise ReceiptError("durable receipt zniknął lub zmienił się przed finalize")
            if (
                int(loaded_receipt["exit_code"]) != int(claim["exit_code"])
                or str(loaded_receipt["stdout"]["sha256"])
                != str(claim["stdout_sha256"])
                or str(loaded_receipt["stderr"]["sha256"])
                != str(claim["stderr_sha256"])
            ):
                connection.rollback()
                raise ReceiptError("durable receipt nie zgadza się z zapisanym wynikiem")
            if claim["status"] == "FINALIZED":
                expected_status = (
                    "SUCCEEDED" if int(claim["exit_code"]) == 0 else "FAILED"
                )
                if (
                    job["status"] != expected_status
                    or int(job["exit_code"]) != int(claim["exit_code"])
                    or str(job["result_evidence_hash"] or "")
                    != str(claim["receipt_sha256"])
                ):
                    connection.rollback()
                    raise ReceiptError("FINALIZED claim i terminalny job są niespójne")
                connection.rollback()
                return self.show_at_job(job_key)
            if job["status"] not in {"SCHEDULED", "MISSING_ALARM"}:
                connection.rollback()
                raise GateError(f"at job ma stan terminalny lub niegotowy: {job['status']}")
            exit_code = int(claim["exit_code"])
            evidence_hash = _validate_evidence_hash(str(claim["receipt_sha256"]))
            new_status = "SUCCEEDED" if exit_code == 0 else "FAILED"
            connection.execute(
                """
                UPDATE at_jobs SET status = ?, exit_code = ?,
                    result_evidence_hash = ?, updated_at = ?, finished_at = ?,
                    reconcile_note = '' WHERE job_key = ?
                    AND status IN ('SCHEDULED', 'MISSING_ALARM')
                """,
                (new_status, exit_code, evidence_hash, timestamp, timestamp, job_key),
            )
            gate = connection.execute(
                "SELECT * FROM gates WHERE gate_id = ?", (job["gate_id"],)
            ).fetchone()
            assert gate is not None
            gate_version = int(gate["version"])
            run_unknown_reason = outcome_unknown_reason("RUN")
            scheduler_alarm_reason = str(gate["alarm_reason"] or "")
            recovered_scheduler_alarm = (
                gate_version == int(binding["gate_version"]) + 1
                and gate["state"] == binding["gate_state"]
                and gate["code_sha"] == binding["gate_code_sha"]
                and gate["evidence_hash"] == binding["gate_evidence_hash"]
                and gate["state"] not in TERMINAL_STATES
                and bool(gate["alarm"])
                and scheduler_alarm_reason
                in {run_unknown_reason, receipt_stalled_reason()}
                and str(gate["blocker"] or "") == scheduler_alarm_reason
                and str(gate["next_step"] or "")
                == "Rozlicz claim bez ponownego wykonania"
            )
            if exit_code == 0:
                reason = (
                    "procesowy receipt at-joba ma exit 0; semantyczna promocja "
                    "wymaga osobnego review"
                )
                result_version = gate_version
                snapshot = self._event_snapshot(gate)
                if recovered_scheduler_alarm:
                    cursor = connection.execute(
                        """
                        UPDATE gates SET alarm = ?, alarm_reason = ?,
                            blocker = ?, next_step = ?, version = version + 1,
                            updated_at = ? WHERE gate_id = ? AND version = ?
                        """,
                        (
                            int(bool(binding["gate_alarm"])),
                            str(binding["gate_alarm_reason"]),
                            str(binding["gate_blocker"]),
                            str(binding["gate_next_step"]),
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
                    result_version = int(updated["version"])
                    snapshot = self._event_snapshot(updated)
                    reason += "; exact RUN OUTCOME_UNKNOWN alarm rozliczony"
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
                        result_version,
                        f"{reason}; procesowy dowód {evidence_hash}",
                        timestamp,
                        snapshot,
                    ),
                )
            elif exit_code != 0:
                reason = f"at-job zakończył się kodem {exit_code}"
                if gate["state"] in TERMINAL_STATES:
                    connection.rollback()
                    raise IllegalTransition(
                        "nieudany RUN nie może zostać rozliczony na terminalnej bramce"
                    )
                existing_alarm = str(gate["alarm_reason"] or "")
                if recovered_scheduler_alarm:
                    combined_alarm = reason
                    event_suffix = "; exact RUN OUTCOME_UNKNOWN alarm zastąpiony wynikiem"
                elif reason in existing_alarm:
                    combined_alarm = existing_alarm
                    event_suffix = ""
                elif existing_alarm:
                    combined_alarm = f"{existing_alarm} | PROCESS: {reason}"
                    event_suffix = ""
                else:
                    combined_alarm = reason
                    event_suffix = ""
                binding_still_current = (
                    gate["state"] == binding.get("gate_state")
                    and gate_version == binding.get("gate_version")
                    and gate["code_sha"] == binding.get("gate_code_sha")
                    and gate["evidence_hash"] == binding.get("gate_evidence_hash")
                )
                replace_operator_fields = recovered_scheduler_alarm or binding_still_current
                cursor = connection.execute(
                    """
                    UPDATE gates SET alarm = 1, alarm_reason = ?,
                        blocker = CASE WHEN ? THEN ? ELSE blocker END,
                        next_step = CASE WHEN ?
                            THEN 'Rozlicz nieudany at-job' ELSE next_step END,
                        version = version + 1, updated_at = ?
                    WHERE gate_id = ? AND version = ?
                    """,
                    (
                        combined_alarm,
                        int(replace_operator_fields),
                        reason,
                        int(replace_operator_fields),
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
                reason += event_suffix
                result_version = int(updated["version"])
                snapshot = self._event_snapshot(updated)
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
                        result_version,
                        reason,
                        timestamp,
                        snapshot,
                    ),
                )
            cursor = connection.execute(
                """
                UPDATE at_job_claims SET status = 'FINALIZED', updated_at = ?,
                    finalized_at = ?
                WHERE claim_id = ? AND status = 'RECEIPT_READY'
                """,
                (timestamp, timestamp, claim_id),
            )
            if cursor.rowcount != 1:
                connection.rollback()
                raise CASConflict("claim zmienił się podczas finalize")
            connection.commit()
        return self.show_at_job(job_key)

    def finish_at_job(self, *_args: Any, **_kwargs: Any) -> dict[str, Any]:
        raise ValidationError(
            "finish_at_job jest zamknięty: wymagane durable "
            "record_at_receipt -> finalize_at_claim"
        )

    def begin_at_submission_cancellation(
        self,
        job_key: str,
        at_job_id: str,
        *,
        actor: str,
        reason: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Zapisz exact logiczny CANCEL przyjętego, niepotwierdzonego joba."""

        job_key = _required_text(job_key, "job_key")
        at_job_id = _required_text(at_job_id, "at_job_id")
        if not at_job_id.isdigit():
            raise ValidationError("at_job_id musi być liczbą")
        actor = _required_text(actor, "actor")
        reason = _required_text(reason, "reason")
        timestamp = iso_utc(now or utc_now())
        reason_sha256 = hashlib.sha256(reason.encode("utf-8")).hexdigest()
        self.initialize()
        with self._write_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            job = connection.execute(
                "SELECT * FROM at_jobs WHERE job_key = ?", (job_key,)
            ).fetchone()
            if job is None:
                connection.rollback()
                raise GateNotFound(f"brak at job: {job_key}")
            gate = connection.execute(
                "SELECT * FROM gates WHERE gate_id = ?", (job["gate_id"],)
            ).fetchone()
            assert gate is not None
            conflicting_owner = connection.execute(
                """
                SELECT job_key FROM at_jobs
                WHERE at_job_id = ? AND job_key <> ?
                  AND status IN ('SUBMITTING', 'SCHEDULED', 'MISSING_ALARM')
                LIMIT 1
                """,
                (at_job_id, job_key),
            ).fetchone()
            if conflicting_owner is not None:
                connection.rollback()
                raise ClaimConflict(
                    f"at_job_id #{at_job_id} należy do aktywnego intentu "
                    f"{conflicting_owner['job_key']}; atrm zabronione"
                )
            existing = connection.execute(
                "SELECT * FROM at_job_claims WHERE job_key = ?", (job_key,)
            ).fetchone()
            if existing is not None:
                binding = self._claim_binding(existing)
                exact_identity = (
                    binding.get("operation") == "CANCEL"
                    and binding.get("submission_rollback") is True
                    and binding.get("claim_id") == existing["claim_id"]
                    and binding.get("job_key") == job_key
                    and binding.get("gate_id") == job["gate_id"]
                    and binding.get("at_job_id") == at_job_id
                    and binding.get("actor") == actor
                    and binding.get("reason") == reason
                    and binding.get("reason_sha256") == reason_sha256
                    and isinstance(binding.get("early_runner_aborted"), bool)
                )
                active_retry = (
                    exact_identity
                    and existing["status"] == "CLAIMED"
                    and job["status"] == "SUBMITTING"
                    and gate["state"] == "BUILT_OFF"
                    and binding.get("gate_version") == int(gate["version"])
                )
                terminal_retry = (
                    exact_identity
                    and existing["status"] == "FINALIZED"
                    and job["status"] == "SUBMISSION_FAILED"
                )
                if active_retry or terminal_retry:
                    connection.rollback()
                    return self.show_at_claim(job_key)
                connection.rollback()
                raise ClaimConflict("submission rollback ma inny istniejący claim")
            if job["status"] != "SUBMITTING" or gate["state"] != "BUILT_OFF":
                connection.rollback()
                raise GateError(
                    "submission rollback wymaga SUBMITTING/BUILT_OFF; "
                    f"jest {job['status']}/{gate['state']}"
                )
            early_runner_aborted = str(job["reconcile_note"] or "").startswith(
                "EARLY_RUNNER_ABORTED:"
            )
            claim_id = f"cancel-{uuid.uuid4().hex}"
            binding = {
                "schema_version": CLAIM_BINDING_VERSION,
                "operation": "CANCEL",
                "submission_rollback": True,
                "claim_id": claim_id,
                "job_key": job_key,
                "gate_id": str(job["gate_id"]),
                "at_job_id": at_job_id,
                "gate_version": int(gate["version"]),
                "actor": actor,
                "reason": reason,
                "reason_sha256": reason_sha256,
                # Ten bit jest związany z claimem w tej samej transakcji co
                # przejęcie authority CANCEL. Jeżeli runner wygrał wcześniej
                # i już się wycofał, nie wróci później posprzątać payloadu.
                "early_runner_aborted": early_runner_aborted,
            }
            connection.execute(
                """
                INSERT INTO at_job_claims (
                    claim_id, job_key, gate_id, status, binding_json,
                    binding_sha256, claimed_at, updated_at
                ) VALUES (?, ?, ?, 'CLAIMED', ?, ?, ?, ?)
                """,
                (
                    claim_id,
                    job_key,
                    job["gate_id"],
                    canonical_json(binding),
                    sha256_json(binding),
                    timestamp,
                    timestamp,
                ),
            )
            connection.execute(
                """
                UPDATE at_jobs SET reconcile_note = ?, updated_at = ?
                WHERE job_key = ? AND status = 'SUBMITTING'
                """,
                (
                    f"SUBMISSION_CANCEL_CLAIM:{claim_id}:at#{at_job_id}",
                    timestamp,
                    job_key,
                ),
            )
            connection.commit()
        return self.show_at_claim(job_key)

    def finalize_at_submission_cancellation(
        self,
        job_key: str,
        *,
        cancel_claim_id: str,
        at_job_id: str,
        actor: str,
        reason: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Finalizuj exact logiczny submission CANCEL bez destrukcji po ID."""

        job_key = _required_text(job_key, "job_key")
        cancel_claim_id = _required_text(cancel_claim_id, "cancel_claim_id")
        at_job_id = _required_text(at_job_id, "at_job_id")
        if not at_job_id.isdigit():
            raise ValidationError("at_job_id musi być liczbą")
        actor = _required_text(actor, "actor")
        reason = _required_text(reason, "reason")
        reason_sha256 = hashlib.sha256(reason.encode("utf-8")).hexdigest()
        timestamp = iso_utc(now or utc_now())
        self.initialize()
        with self._write_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            job = connection.execute(
                "SELECT * FROM at_jobs WHERE job_key = ?", (job_key,)
            ).fetchone()
            claim = connection.execute(
                "SELECT * FROM at_job_claims WHERE job_key = ?", (job_key,)
            ).fetchone()
            if job is None or claim is None or claim["claim_id"] != cancel_claim_id:
                connection.rollback()
                raise ClaimConflict("brak exact submission CANCEL claim_id")
            gate = connection.execute(
                "SELECT * FROM gates WHERE gate_id = ?", (job["gate_id"],)
            ).fetchone()
            assert gate is not None
            binding = self._claim_binding(claim)
            exact = (
                binding.get("operation") == "CANCEL"
                and binding.get("submission_rollback") is True
                and binding.get("claim_id") == cancel_claim_id
                and binding.get("job_key") == job_key
                and binding.get("gate_id") == job["gate_id"]
                and binding.get("at_job_id") == at_job_id
                and binding.get("actor") == actor
                and binding.get("reason") == reason
                and binding.get("reason_sha256") == reason_sha256
                and isinstance(binding.get("early_runner_aborted"), bool)
            )
            if job["status"] == "SUBMISSION_FAILED" and claim["status"] == "FINALIZED":
                connection.rollback()
                if not exact:
                    raise ClaimConflict("terminalny submission CANCEL nie jest exact retry")
                return self.show_at_job(job_key)
            if (
                not exact
                or claim["status"] != "CLAIMED"
                or job["status"] != "SUBMITTING"
                or gate["state"] != "BUILT_OFF"
                or int(gate["version"]) != int(binding.get("gate_version", -1))
            ):
                connection.rollback()
                raise ClaimConflict("submission CANCEL nie zgadza się ze stanem intentu")
            gate_version = int(gate["version"])
            alarm_reason = (
                f"at-job #{at_job_id} przyjęty, lecz confirm DB nieudany; "
                f"exact CANCEL potwierdzony: {reason}"
            )
            job_cursor = connection.execute(
                """
                UPDATE at_jobs SET status = 'SUBMISSION_FAILED', updated_at = ?,
                    finished_at = ?, reconcile_note = ?
                WHERE job_key = ? AND status = 'SUBMITTING'
                """,
                (timestamp, timestamp, alarm_reason, job_key),
            )
            claim_cursor = connection.execute(
                """
                UPDATE at_job_claims SET status = 'FINALIZED', updated_at = ?,
                    finalized_at = ? WHERE claim_id = ? AND status = 'CLAIMED'
                """,
                (timestamp, timestamp, cancel_claim_id),
            )
            if job_cursor.rowcount != 1 or claim_cursor.rowcount != 1:
                connection.rollback()
                raise CASConflict("submission CANCEL zmienił się podczas finalize")
            cursor = connection.execute(
                """
                UPDATE gates SET alarm = 1, alarm_reason = ?, blocker = ?,
                    next_step = 'Napraw planowanie i utwórz nowy gate/job',
                    version = version + 1, updated_at = ?
                WHERE gate_id = ? AND version = ?
                """,
                (
                    alarm_reason,
                    alarm_reason,
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
                ) VALUES (?, 'BUILT_OFF', 'BUILT_OFF', ?, ?, ?, ?, ?, ?)
                """,
                (
                    job["gate_id"],
                    gate_version,
                    int(updated["version"]),
                    actor,
                    alarm_reason,
                    timestamp,
                    self._event_snapshot(updated),
                ),
            )
            connection.commit()
        return self.show_at_job(job_key)

    def begin_at_job_cancellation(
        self,
        job_key: str,
        at_job_id: str,
        *,
        expected_gate_version: int,
        actor: str,
        reason: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Wygraj atomowo z RUN claimem zanim caller dotknie kolejki at."""

        job_key = _required_text(job_key, "job_key")
        at_job_id = _required_text(at_job_id, "at_job_id")
        if not at_job_id.isdigit():
            raise ValidationError("at_job_id musi być liczbą")
        if not isinstance(expected_gate_version, int) or expected_gate_version < 1:
            raise ValidationError("expected_gate_version musi być dodatnią liczbą")
        actor = _required_text(actor, "actor")
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
            if str(job["at_job_id"] or "") != at_job_id:
                connection.rollback()
                raise ValidationError(
                    f"at job id drift: {job['at_job_id']!r} != {at_job_id!r}"
                )
            gate = connection.execute(
                "SELECT * FROM gates WHERE gate_id = ?", (job["gate_id"],)
            ).fetchone()
            assert gate is not None
            existing = connection.execute(
                "SELECT * FROM at_job_claims WHERE job_key = ?", (job_key,)
            ).fetchone()
            if existing is not None:
                binding = self._claim_binding(existing)
                expected_retry = {
                    "schema_version": CLAIM_BINDING_VERSION,
                    "operation": "CANCEL",
                    "claim_id": str(existing["claim_id"]),
                    "job_key": job_key,
                    "gate_id": str(job["gate_id"]),
                    "at_job_id": at_job_id,
                    "gate_version": expected_gate_version,
                    "actor": actor,
                    "reason": reason,
                    "reason_sha256": hashlib.sha256(reason.encode("utf-8")).hexdigest(),
                }
                if (
                    existing["status"] in {"CLAIMED", "OUTCOME_UNKNOWN"}
                    and job["status"] in {"SCHEDULED", "MISSING_ALARM"}
                    and existing["gate_id"] == job["gate_id"]
                    and binding == expected_retry
                ):
                    connection.rollback()
                    return self.show_at_claim(job_key)
                if (
                    existing["status"] == "FINALIZED"
                    and job["status"] == "CANCELLED"
                    and existing["gate_id"] == job["gate_id"]
                    and binding == expected_retry
                ):
                    connection.rollback()
                    return self.show_at_claim(job_key)
                connection.rollback()
                raise ClaimConflict("istniejący claim nie jest exact retry CANCEL")
            if job["status"] not in {"SCHEDULED", "MISSING_ALARM"}:
                connection.rollback()
                raise GateError(f"at job nie jest anulowalny: {job['status']}")
            if int(gate["version"]) != expected_gate_version:
                connection.rollback()
                raise CASConflict(
                    f"CAS konflikt bramki {job['gate_id']}: expected "
                    f"{expected_gate_version}, jest {gate['version']}"
                )
            claim_id = f"cancel-{uuid.uuid4().hex}"
            binding = {
                "schema_version": CLAIM_BINDING_VERSION,
                "operation": "CANCEL",
                "claim_id": claim_id,
                "job_key": job_key,
                "gate_id": str(job["gate_id"]),
                "at_job_id": at_job_id,
                "gate_version": int(gate["version"]),
                "actor": actor,
                "reason": reason,
                "reason_sha256": hashlib.sha256(reason.encode("utf-8")).hexdigest(),
            }
            connection.execute(
                """
                INSERT INTO at_job_claims (
                    claim_id, job_key, gate_id, status, binding_json,
                    binding_sha256, claimed_at, updated_at
                ) VALUES (?, ?, ?, 'CLAIMED', ?, ?, ?, ?)
                """,
                (
                    claim_id,
                    job_key,
                    job["gate_id"],
                    canonical_json(binding),
                    sha256_json(binding),
                    timestamp,
                    timestamp,
                ),
            )
            connection.commit()
        return self.show_at_claim(job_key)

    def cancel_at_job(
        self,
        job_key: str,
        at_job_id: str,
        *,
        cancel_claim_id: str,
        expected_gate_version: int,
        actor: str,
        reason: str,
        now: datetime | None = None,
    ) -> dict[str, Any]:
        """Finalizuj exact CANCEL claim dopiero po dowodzie nieobecności w atq."""

        job_key = _required_text(job_key, "job_key")
        at_job_id = _required_text(at_job_id, "at_job_id")
        cancel_claim_id = _required_text(cancel_claim_id, "cancel_claim_id")
        if not at_job_id.isdigit():
            raise ValidationError("at_job_id musi być liczbą")
        if (
            not isinstance(expected_gate_version, int)
            or expected_gate_version < 1
        ):
            raise ValidationError("expected_gate_version musi być dodatnią liczbą")
        actor = _required_text(actor, "actor")
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
            if str(job["at_job_id"] or "") != at_job_id:
                connection.rollback()
                raise ValidationError(
                    f"at job id drift: {job['at_job_id']!r} != {at_job_id!r}"
                )
            claim = connection.execute(
                "SELECT * FROM at_job_claims WHERE job_key = ?", (job_key,)
            ).fetchone()
            if claim is None or str(claim["claim_id"]) != cancel_claim_id:
                connection.rollback()
                raise ClaimConflict("brak exact CANCEL claim_id")
            expected_binding = {
                "schema_version": CLAIM_BINDING_VERSION,
                "operation": "CANCEL",
                "claim_id": cancel_claim_id,
                "job_key": job_key,
                "gate_id": str(job["gate_id"]),
                "at_job_id": at_job_id,
                "gate_version": expected_gate_version,
                "actor": actor,
                "reason": reason,
                "reason_sha256": hashlib.sha256(reason.encode("utf-8")).hexdigest(),
            }
            try:
                binding = self._claim_binding(claim)
            except ClaimConflict:
                connection.rollback()
                raise
            if (
                job["status"] == "CANCELLED"
                and claim["status"] == "FINALIZED"
                and claim["gate_id"] == job["gate_id"]
                and binding == expected_binding
            ):
                connection.rollback()
                return self.show_at_job(job_key)
            if job["status"] not in {"SCHEDULED", "MISSING_ALARM"}:
                connection.rollback()
                raise GateError(
                    f"at job ma stan terminalny lub niegotowy: {job['status']}"
                )
            if (
                claim["status"] not in {"CLAIMED", "OUTCOME_UNKNOWN"}
                or claim["gate_id"] != job["gate_id"]
                or binding != expected_binding
            ):
                connection.rollback()
                raise ClaimConflict("finalize wymaga aktywnego exact CANCEL claimu")
            gate = connection.execute(
                "SELECT * FROM gates WHERE gate_id = ?", (job["gate_id"],)
            ).fetchone()
            assert gate is not None
            cancellable_states = {
                "BUILT_OFF",
                "WAIT_DATA",
                "READY_FOR_REVIEW",
                "SUPERSEDED",
            }
            cancel_unknown_reason = outcome_unknown_reason("CANCEL")
            recovered_scheduler_alarm = (
                claim["status"] == "OUTCOME_UNKNOWN"
                and int(gate["version"]) == expected_gate_version + 1
                and gate["state"] in cancellable_states
                and bool(gate["alarm"])
                and str(gate["alarm_reason"] or "") == cancel_unknown_reason
                and str(gate["blocker"] or "") == cancel_unknown_reason
                and str(gate["next_step"] or "")
                == "Rozlicz claim bez ponownego wykonania"
            )
            gate_unchanged = (
                int(gate["version"]) == expected_gate_version
                and gate["state"] in cancellable_states
            ) or recovered_scheduler_alarm
            write_gate_version = int(gate["version"])
            from_state = str(gate["state"])
            connection.execute(
                """
                UPDATE at_jobs SET status = 'CANCELLED', updated_at = ?,
                    finished_at = ?, reconcile_note = ? WHERE job_key = ?
                """,
                (timestamp, timestamp, reason, job_key),
            )
            claim_cursor = connection.execute(
                """
                UPDATE at_job_claims SET status = 'FINALIZED', updated_at = ?,
                    finalized_at = ? WHERE claim_id = ?
                    AND status IN ('CLAIMED', 'OUTCOME_UNKNOWN')
                """,
                (timestamp, timestamp, cancel_claim_id),
            )
            if claim_cursor.rowcount != 1:
                connection.rollback()
                raise CASConflict("CANCEL claim zmienił się podczas finalize")
            if gate_unchanged:
                cursor = connection.execute(
                    """
                    UPDATE gates SET state = 'SUPERSEDED', alarm = 0,
                        alarm_reason = '', blocker = ?,
                        next_step = 'Nie wykonuj anulowanego at-joba',
                        version = version + 1, updated_at = ?,
                        closed_at = COALESCE(closed_at, ?)
                    WHERE gate_id = ? AND version = ?
                    """,
                    (
                        reason,
                        timestamp,
                        timestamp,
                        job["gate_id"],
                        write_gate_version,
                    ),
                )
                if cursor.rowcount != 1:
                    connection.rollback()
                    raise CASConflict(f"CAS konflikt bramki {job['gate_id']}")
                updated = connection.execute(
                    "SELECT * FROM gates WHERE gate_id = ?", (job["gate_id"],)
                ).fetchone()
                assert updated is not None
                to_state = "SUPERSEDED"
                result_version = int(updated["version"])
                snapshot = self._event_snapshot(updated)
                event_reason = f"at-job #{at_job_id} anulowany kanonicznie: {reason}"
            else:
                to_state = from_state
                result_version = int(gate["version"])
                snapshot = self._event_snapshot(gate)
                event_reason = (
                    f"at-job #{at_job_id} anulowany; gate zmienił się po CANCEL "
                    f"claimie i jego pola zachowano: {reason}"
                )
            connection.execute(
                """
                INSERT INTO gate_events (
                    gate_id, from_state, to_state, expected_version,
                    result_version, actor, reason, occurred_at, snapshot_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    job["gate_id"],
                    from_state,
                    to_state,
                    write_gate_version,
                    result_version,
                    actor,
                    event_reason,
                    timestamp,
                    snapshot,
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
                      AND NOT (
                        status = 'SUBMITTING'
                        AND reconcile_note LIKE 'EARLY_RUNNER_ABORTED:%'
                      )
                    """,
                    (_required_text(note or "atq UNAVAILABLE", "note"), timestamp),
                )
                count = connection.execute(
                    """SELECT COUNT(*) FROM at_jobs
                       WHERE status IN ('SUBMITTING', 'SCHEDULED', 'MISSING_ALARM')"""
                ).fetchone()[0]
                connection.commit()
            return {
                "status": "UNAVAILABLE",
                "active": count,
                "alarms": [],
                "terminal_orphans": [],
                "recovery_candidates": [],
            }

        normalized = {str(value) for value in present_job_ids if str(value).isdigit()}
        alarms: list[dict[str, str]] = []
        seen: list[str] = []
        running: list[str] = []
        launching: list[str] = []
        recovery_candidates: list[str] = []
        outcome_unknown: list[str] = []
        terminal_orphans: list[dict[str, str]] = []
        with self._write_connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            jobs = connection.execute(
                """
                SELECT j.*, c.claim_id, c.status AS claim_status,
                       c.binding_json AS claim_binding_json,
                       c.claimed_at AS claim_claimed_at,
                       c.updated_at AS claim_updated_at,
                       c.receipt_path AS claim_receipt_path,
                       g.state AS gate_state, g.version AS gate_version,
                       g.alarm AS gate_alarm, g.alarm_reason AS gate_alarm_reason
                FROM at_jobs AS j
                JOIN gates AS g ON g.gate_id = j.gate_id
                LEFT JOIN at_job_claims AS c ON c.job_key = j.job_key
                WHERE j.status IN ('SUBMITTING', 'SCHEDULED', 'MISSING_ALARM')
                ORDER BY j.job_key
                """
            ).fetchall()

            def alarm_nonterminal(
                job: sqlite3.Row,
                reason: str,
                next_step: str,
            ) -> None:
                gate = connection.execute(
                    "SELECT * FROM gates WHERE gate_id = ?", (job["gate_id"],)
                ).fetchone()
                assert gate is not None
                if str(gate["state"]) in TERMINAL_STATES:
                    raise AssertionError("terminal gate musi wejść w terminal_orphan branch")
                job_already = (
                    str(job["status"]) == "MISSING_ALARM"
                    and str(job["reconcile_note"] or "") == reason
                )
                gate_already = bool(gate["alarm"]) and reason in str(
                    gate["alarm_reason"] or ""
                )
                if job_already and gate_already:
                    return
                connection.execute(
                    """
                    UPDATE at_jobs SET status = 'MISSING_ALARM', updated_at = ?,
                        reconcile_note = ? WHERE job_key = ?
                    """,
                    (timestamp, reason, job["job_key"]),
                )
                gate_version = int(gate["version"])
                alarm_reason = str(gate["alarm_reason"] or "")
                combined = (
                    alarm_reason
                    if reason in alarm_reason
                    else (f"{alarm_reason} | SCHEDULER: {reason}" if alarm_reason else reason)
                )
                preserve_operator_fields = False
                active_claim_status = str(job["claim_status"] or "")
                if active_claim_status in {
                    "CLAIMED",
                    "RECEIPT_READY",
                    "OUTCOME_UNKNOWN",
                }:
                    try:
                        claim_binding = json.loads(
                            str(job["claim_binding_json"] or "{}")
                        )
                    except json.JSONDecodeError:
                        claim_binding = {}
                    if not isinstance(claim_binding, dict):
                        preserve_operator_fields = True
                    elif not claim_binding or claim_binding.get("operation") == "RUN":
                        bound_version = claim_binding.get("gate_version")
                        snapshot_current = (
                            claim_binding.get("operation") == "RUN"
                            and isinstance(bound_version, int)
                            and not isinstance(bound_version, bool)
                            and str(gate["state"])
                            == str(claim_binding.get("gate_state"))
                            and int(gate["version"]) == bound_version
                            and str(gate["code_sha"])
                            == str(claim_binding.get("gate_code_sha"))
                            and str(gate["evidence_hash"])
                            == str(claim_binding.get("gate_evidence_hash"))
                        )
                        preserve_operator_fields = not snapshot_current
                cursor = connection.execute(
                    """
                    UPDATE gates SET alarm = 1, alarm_reason = ?,
                        blocker = CASE WHEN ? THEN blocker ELSE ? END,
                        next_step = CASE WHEN ? THEN next_step ELSE ? END,
                        version = version + 1, updated_at = ?
                    WHERE gate_id = ? AND version = ?
                    """,
                    (
                        combined,
                        int(preserve_operator_fields),
                        reason,
                        int(preserve_operator_fields),
                        next_step,
                        timestamp,
                        job["gate_id"],
                        gate_version,
                    ),
                )
                if cursor.rowcount != 1:
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

            for job in jobs:
                queue_id = str(job["at_job_id"] or "")
                claim_status = str(job["claim_status"] or "")

                if str(job["gate_state"]) in TERMINAL_STATES:
                    present = bool(queue_id and queue_id in normalized)
                    reason = (
                        f"TERMINAL_GATE_SCHEDULER_CLEANUP: at-job #{queue_id or '?'} "
                        + ("nadal jest w atq" if present else "nie ma go w atq")
                    )
                    changed = str(job["reconcile_note"] or "") != reason
                    if not present and job["status"] != "MISSING_ALARM":
                        connection.execute(
                            """
                            UPDATE at_jobs SET status = 'MISSING_ALARM',
                                reconcile_note = ?, updated_at = ? WHERE job_key = ?
                            """,
                            (reason, timestamp, job["job_key"]),
                        )
                        changed = True
                    elif changed:
                        connection.execute(
                            "UPDATE at_jobs SET reconcile_note = ?, updated_at = ? WHERE job_key = ?",
                            (reason, timestamp, job["job_key"]),
                        )
                    if changed:
                        gate = connection.execute(
                            "SELECT * FROM gates WHERE gate_id = ?", (job["gate_id"],)
                        ).fetchone()
                        assert gate is not None
                        # Terminalna bramka jest immutable: same-state event ma
                        # dokładnie tę samą wersję i snapshot, bez alarm/field write.
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
                                int(gate["version"]),
                                int(gate["version"]),
                                reason,
                                timestamp,
                                self._event_snapshot(gate),
                            ),
                        )
                    anomaly = {
                        "job_key": str(job["job_key"]),
                        "gate_id": str(job["gate_id"]),
                        "at_job_id": queue_id,
                    }
                    terminal_orphans.append(anomaly)
                    alarms.append(anomaly)
                    if claim_status == "RECEIPT_READY" or (
                        claim_status == "CLAIMED"
                        and str(job["claim_receipt_path"] or "")
                        and os.path.lexists(str(job["claim_receipt_path"]))
                    ):
                        recovery_candidates.append(str(job["job_key"]))
                    continue

                if job["status"] == "SUBMITTING":
                    created = parse_timestamp(str(job["created_at"]), "created_at")
                    if (parse_timestamp(timestamp) - created).total_seconds() <= 300:
                        continue
                    reason = "ALARM: intencja at pozostała bez identyfikatora kolejki ponad 5 minut"
                    alarm_nonterminal(
                        job,
                        reason,
                        "Sprawdź kolejkę i rozlicz przerwane planowanie",
                    )
                    alarms.append(
                        {"job_key": job["job_key"], "gate_id": job["gate_id"], "at_job_id": ""}
                    )
                    continue

                receipt_present = bool(
                    str(job["claim_receipt_path"] or "")
                    and os.path.lexists(str(job["claim_receipt_path"]))
                )
                if claim_status == "RECEIPT_READY":
                    recovery_candidates.append(str(job["job_key"]))
                    receipt_age = (
                        parse_timestamp(timestamp)
                        - parse_timestamp(
                            str(job["claim_updated_at"]), "claim.updated_at"
                        )
                    ).total_seconds()
                    if receipt_age <= AT_RUN_CLAIM_STALE_SECONDS:
                        continue
                    reason = receipt_stalled_reason()
                    alarm_nonterminal(
                        job,
                        reason,
                        "Rozlicz claim bez ponownego wykonania",
                    )
                    outcome_unknown.append(str(job["job_key"]))
                    alarms.append(
                        {
                            "job_key": job["job_key"],
                            "gate_id": job["gate_id"],
                            "at_job_id": queue_id,
                        }
                    )
                    continue

                if claim_status in {"CLAIMED", "OUTCOME_UNKNOWN"}:
                    try:
                        binding = json.loads(str(job["claim_binding_json"] or "{}"))
                    except json.JSONDecodeError:
                        binding = {}
                    operation = str(binding.get("operation") or "")
                    if operation == "RUN" and receipt_present:
                        recovery_candidates.append(str(job["job_key"]))
                    age = (
                        parse_timestamp(timestamp)
                        - parse_timestamp(str(job["claim_claimed_at"]), "claimed_at")
                    ).total_seconds()
                    stale_after = (
                        AT_RUN_CLAIM_STALE_SECONDS
                        if operation == "RUN"
                        else AT_CANCEL_CLAIM_STALE_SECONDS
                    )
                    if claim_status == "CLAIMED" and age <= stale_after:
                        if operation == "RUN":
                            running.append(str(job["job_key"]))
                        if queue_id in normalized:
                            seen.append(queue_id)
                        continue
                    reason = outcome_unknown_reason(operation)
                    if claim_status == "CLAIMED":
                        connection.execute(
                            """
                            UPDATE at_job_claims SET status = 'OUTCOME_UNKNOWN',
                                updated_at = ? WHERE claim_id = ? AND status = 'CLAIMED'
                            """,
                            (timestamp, job["claim_id"]),
                        )
                    alarm_nonterminal(
                        job,
                        reason,
                        "Rozlicz claim bez ponownego wykonania",
                    )
                    outcome_unknown.append(str(job["job_key"]))
                    alarms.append(
                        {
                            "job_key": job["job_key"],
                            "gate_id": job["gate_id"],
                            "at_job_id": queue_id,
                        }
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
                launch_age = (
                    parse_timestamp(timestamp)
                    - parse_timestamp(str(job["scheduled_for"]), "scheduled_for")
                ).total_seconds()
                if 0 <= launch_age <= AT_LAUNCH_GRACE_SECONDS:
                    # `atd` usuwa wpis z atq zanim shell runnera zdąży wykonać
                    # atomowy RUN claim. Krótkie, deterministyczne okno nie może
                    # samo włączyć alarmu blokującego prawidłowy claim.
                    launching.append(str(job["job_key"]))
                    continue
                if job["status"] == "MISSING_ALARM":
                    alarms.append(
                        {"job_key": job["job_key"], "gate_id": job["gate_id"], "at_job_id": queue_id}
                    )
                    continue
                reason = f"ALARM: at-job #{queue_id} zniknął z atq bez statusu terminalnego"
                alarm_nonterminal(
                    job,
                    reason,
                    "Ustal wynik z logu i oznacz status terminalny",
                )
                alarms.append(
                    {"job_key": job["job_key"], "gate_id": job["gate_id"], "at_job_id": queue_id}
                )
            connection.commit()
        return {
            "status": "OK",
            "seen": sorted(set(seen), key=int),
            "running": sorted(running),
            "launching": sorted(launching),
            "recovery_candidates": sorted(set(recovery_candidates)),
            "outcome_unknown": sorted(set(outcome_unknown)),
            "terminal_orphans": terminal_orphans,
            "alarms": alarms,
        }

    def show_at_job(self, job_key: str) -> dict[str, Any]:
        job_key = _required_text(job_key, "job_key")
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM at_jobs WHERE job_key = ?", (job_key,)
            ).fetchone()
            if row is None:
                raise GateNotFound(f"brak at job: {job_key}")
        return self._row_to_job(row)

    def show_at_claim(self, job_key: str) -> dict[str, Any]:
        job_key = _required_text(job_key, "job_key")
        with self._read_connection() as connection:
            row = connection.execute(
                "SELECT * FROM at_job_claims WHERE job_key = ?", (job_key,)
            ).fetchone()
            if row is None:
                raise GateNotFound(f"brak claimu dla at job: {job_key}")
        return self._row_to_claim(row)

    def verify_active_run_claim(
        self,
        job_key: str,
        *,
        claim_id: str,
        command: Sequence[str],
    ) -> dict[str, Any]:
        """Poświadcz aktywny RUN bez ujawniania prywatnych ścieżek/bindingu.

        To jest jedyny read-only verifier dla każdego subprocessu at_gate.
        Konsument model-bearing nadal musi osobno wymagać sealed auth v2.
        Konsument przekazuje faktyczne argv własnego procesu; stored/env digest
        nie może zastąpić ponownego obliczenia hasha z tych argumentów.
        """

        job_key = _required_text(job_key, "job_key")
        claim_id = _required_text(claim_id, "claim_id")
        actual_command_sha256 = canonical_argv_hash(command)
        with self._read_connection() as connection:
            # Jeden read snapshot wiąże wersję/exact schema z jobem, claimem i
            # gate. Verifier nie może deklarować bieżącego kontraktu na DB ze
            # starym user_version ani po utracie indeksu/constraint surface.
            connection.execute("BEGIN")
            runtime_schema_version = int(
                connection.execute("PRAGMA user_version").fetchone()[0]
            )
            if runtime_schema_version != DB_SCHEMA_VERSION:
                raise ClaimConflict(
                    "active RUN verifier wymaga exact schema "
                    f"v{DB_SCHEMA_VERSION}; DB ma v{runtime_schema_version}"
                )
            try:
                _assert_exact_schema(connection)
            except GateError as exc:
                raise ClaimConflict(
                    f"active RUN verifier odrzuca schema drift: {exc}"
                ) from exc
            job = connection.execute(
                "SELECT * FROM at_jobs WHERE job_key = ?", (job_key,)
            ).fetchone()
            claim = connection.execute(
                "SELECT * FROM at_job_claims WHERE job_key = ?", (job_key,)
            ).fetchone()
            if job is None or claim is None:
                raise ClaimConflict("brak exact job/claim dla wykonania")
            if str(claim["claim_id"]) != claim_id:
                raise ClaimConflict("claim_id wykonania nie zgadza się z ledgerem")
            if job["status"] != "SCHEDULED" or claim["status"] != "CLAIMED":
                raise ClaimConflict(
                    "model-bearing execution wymaga SCHEDULED + CLAIMED; "
                    f"jest {job['status']} + {claim['status']}"
                )
            stored_command_sha256 = self._stored_command_hash(job)
            if not hmac.compare_digest(actual_command_sha256, stored_command_sha256):
                raise ClaimConflict("faktyczne argv nie zgadza się z RUN authority")
            binding = self._validate_run_claim_binding(
                claim,
                job,
                stored_command_sha256,
            )
            gate = connection.execute(
                "SELECT * FROM gates WHERE gate_id = ?", (job["gate_id"],)
            ).fetchone()
            if gate is None:
                raise GateNotFound(f"brak bramki dla at job: {job['gate_id']}")
            if (
                gate["state"] in TERMINAL_STATES
                or bool(gate["alarm"])
                or str(gate["state"]) != str(binding["gate_state"])
                or int(gate["version"]) != int(binding["gate_version"])
                or str(gate["code_sha"]) != str(binding["gate_code_sha"])
                or str(gate["evidence_hash"]) != str(binding["gate_evidence_hash"])
            ):
                raise ClaimConflict("gate zmienił się po RUN claimie albo ma ALARM")
        return {
            "schema_version": runtime_schema_version,
            "auth_version": int(job["auth_version"] or 1),
            "job_key": job_key,
            "gate_id": str(job["gate_id"]),
            "at_job_id": str(job["at_job_id"]),
            "job_status": str(job["status"]),
            "claim_id": claim_id,
            "claim_status": str(claim["status"]),
            "binding_sha256": str(claim["binding_sha256"]),
            "command_sha256": actual_command_sha256,
            "gate_state": str(gate["state"]),
            "gate_version": int(gate["version"]),
            "gate_code_sha": str(gate["code_sha"]),
            "gate_evidence_hash": str(gate["evidence_hash"]),
        }

    def list_at_claims(self, *, active_only: bool = False) -> list[dict[str, Any]]:
        if not self.db_path.is_file():
            return []
        query = "SELECT * FROM at_job_claims"
        if active_only:
            query += (
                " WHERE status IN ('CLAIMED', 'RECEIPT_READY', "
                "'OUTCOME_UNKNOWN')"
            )
        query += " ORDER BY claimed_at, job_key"
        with self._read_connection() as connection:
            rows = connection.execute(query).fetchall()
        return [self._row_to_claim(row) for row in rows]

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

    def list_gate_events(self) -> list[dict[str, Any]]:
        if not self.db_path.is_file():
            return []
        with self._read_connection() as connection:
            rows = connection.execute(
                "SELECT * FROM gate_events ORDER BY event_id"
            ).fetchall()
        return [dict(row) for row in rows]

    def ledger_attestation_material(self) -> dict[str, Any]:
        """Zwiąż wszystkie trwałe pola jednego snapshotu SQLite.

        Jawny read transaction jest częścią kontraktu: hash i publiczna projekcja
        eksportu nie mogą mieszać rekordów sprzed i po równoległym commicie.
        """

        if not self.db_path.is_file():
            raise GateNotFound(f"baza nie istnieje: {self.db_path}")
        ordering = {
            "gates": "gate_id",
            "gate_events": "event_id",
            "at_jobs": "job_key",
            "at_job_claims": "claim_id",
        }
        with self._read_connection() as connection:
            connection.execute("BEGIN")
            try:
                version = int(
                    connection.execute("PRAGMA user_version").fetchone()[0]
                )
                if version != DB_SCHEMA_VERSION:
                    raise GateError(
                        "snapshot ledgera wymaga kanonicznego schematu "
                        f"v{DB_SCHEMA_VERSION}; jest v{version}"
                    )
                schema_manifest = _assert_exact_schema(connection)
                tables: dict[str, list[dict[str, Any]]] = {}
                for table, order_by in ordering.items():
                    tables[table] = [
                        dict(row)
                        for row in connection.execute(
                            f"SELECT * FROM {table} ORDER BY {order_by}"
                        ).fetchall()
                    ]
                    self._ledger_snapshot_checkpoint(table)
                connection.commit()
            except Exception:
                connection.rollback()
                raise
        return {
            "schema_version": version,
            "schema_manifest": schema_manifest,
            "tables": tables,
        }


def _display(value: Any, limit: int) -> str:
    text = str(value).replace("|", "\\|").replace("\r", " ").replace("\n", " ").strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def render_open_gates(
    gates: Sequence[Mapping[str, Any]],
    *,
    as_of: datetime,
    source: str = "gates.sqlite3",
    ledger_hash: str | None = None,
    scheduler_anomalies: Sequence[Mapping[str, Any]] = (),
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
        f"Anomalie schedulera (także terminalne): **{len(scheduler_anomalies)}**",
        "",
        "| dni | ID | stan | owner | termin | notatka | alarm |",
        "|---:|---|---|---|---|---|---|",
    ]
    if visible:
        for days, gate in visible:
            due_date = parse_timestamp(str(gate["due_at"]), "due_at").date().isoformat()
            alarm = "ALARM" if gate.get("alarm") else "—"
            freshness = gate.get("freshness")
            note = "—"
            if isinstance(freshness, Mapping) and freshness.get("has_fresh_note"):
                note_at = parse_timestamp(
                    str(freshness["latest_note_at"]), "latest_note_at"
                ).date().isoformat()
                note = (
                    f"ŚWIEŻA {note_at} "
                    f"{_display(freshness.get('latest_note_actor') or '—', 12)}"
                )
            lines.append(
                "| "
                + " | ".join(
                    (
                        str(days),
                        _display(gate["gate_id"], 38),
                        _display(gate["state"], 22),
                        _display(gate["owner"], 20),
                        due_date,
                        note,
                        alarm,
                    )
                )
                + " |"
            )
    else:
        lines.append("| — | brak otwartych bramek | — | — | — | — | — |")
    if scheduler_anomalies:
        lines.extend(["", "## Anomalie schedulera", ""])
        for anomaly in list(scheduler_anomalies)[:10]:
            lines.append(
                "- gate=`"
                + _display(anomaly.get("gate_id", "—"), 48)
                + "` job=`"
                + _display(anomaly.get("job_key", "—"), 60)
                + "` at=`#"
                + _display(anomaly.get("at_job_id", "?"), 16)
                + "` job_status=`"
                + _display(anomaly.get("job_status", "—"), 24)
                + "` claim_status=`"
                + _display(anomaly.get("claim_status", "—"), 24)
                + "`"
            )
        omitted_anomalies = max(0, len(scheduler_anomalies) - 10)
        if omitted_anomalies:
            lines.append(f"- Pominięte anomalie schedulera: {omitted_anomalies}.")
    lines.extend(
        [
            "",
            "## Kontrola",
            "",
            f"- Najstarsza: {oldest}.",
            f"- Pominięte z tabeli: {max(0, len(open_rows) - len(visible))}.",
            "- Kolejność: dni wiszenia malejąco, potem ID rosnąco.",
            "- Terminalne: CLOSED, REJECTED i SUPERSEDED nie są pokazywane.",
            "- ŚWIEŻA = notatka audytowa nowsza niż ostatnie przejście FSM.",
            "- ALARM oznacza brak terminalnego wyniku zarejestrowanego at-joba.",
            "- Anomalia schedulera na terminalnej bramce pozostaje widoczna do exact cleanup.",
            "- Odświeżenie: `process_debt_gate.py export --format open-gates`.",
        ]
    )
    # Strażnik kompletności widoku. NIE liczymy tu całkowitych linii: sztywny
    # zakres (dawniej 20-30) pękał przy każdej zmianie ramy — np. dopisanie
    # jednej linii legendy „ŚWIEŻA" wywalało generowanie CAŁEGO widoku żywego
    # ledgera, mimo poprawnych danych. Sprawdzamy INWARIANTY, nie proxy:
    # nagłówek, sekcja kontrolna i zgodność liczby wierszy tabeli z `visible`.
    rendered_rows = sum(1 for ln in lines if ln.startswith("| ") and " | " in ln)
    # Nagłówek tabeli + N rekordów; przy pustej tabeli dochodzi placeholder.
    expected_rows = len(visible) + 1 + (0 if visible else 1)
    problems = []
    if not lines or not lines[0].startswith("# OPEN GATES"):
        problems.append("brak nagłówka '# OPEN GATES'")
    if "## Kontrola" not in lines:
        problems.append("brak sekcji '## Kontrola'")
    if rendered_rows != expected_rows:
        problems.append(
            f"tabela ma {rendered_rows} wierszy, oczekiwano {expected_rows}"
        )
    if problems:
        raise AssertionError("widok niekompletny: " + "; ".join(problems))
    return "\n".join(lines) + "\n"


def canonical_ledger_material(store: GateStore) -> dict[str, Any]:
    """Jedyny prywatny materiał hasha; publiczny eksport pozostaje zredagowany."""

    return store.ledger_attestation_material()


def public_ledger_projection(
    material: Mapping[str, Any],
) -> tuple[
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
    list[dict[str, Any]],
]:
    """Zredaguj publiczny widok z tych samych raw rows, które wiąże hash."""

    tables = material.get("tables")
    if not isinstance(tables, Mapping) or set(tables) != {
        "gates",
        "gate_events",
        "at_jobs",
        "at_job_claims",
    }:
        raise ValidationError("snapshot ledgera ma niekanoniczny zestaw tabel")
    raw_gates = list(tables["gates"])
    raw_events = list(tables["gate_events"])
    raw_jobs = list(tables["at_jobs"])
    raw_claims = list(tables["at_job_claims"])
    if any(not isinstance(row, Mapping) for rows in tables.values() for row in rows):
        raise ValidationError("snapshot ledgera zawiera niepoprawny rekord")

    gates = []
    for row in raw_gates:
        gate = {column: row[column] for column in PUBLIC_GATE_COLUMNS}
        gate["alarm"] = bool(gate["alarm"])
        gate["metadata_sha256"] = hashlib.sha256(
            str(row["metadata_json"]).encode("utf-8")
        ).hexdigest()
        gates.append(gate)
    gates.sort(key=lambda row: (str(row["opened_at"]), str(row["gate_id"])))
    gate_events = []
    for row in raw_events:
        event = {column: row[column] for column in PUBLIC_GATE_EVENT_COLUMNS}
        event["snapshot_sha256"] = hashlib.sha256(
            str(row["snapshot_json"]).encode("utf-8")
        ).hexdigest()
        gate_events.append(event)
    gate_events.sort(key=lambda row: int(row["event_id"]))
    events_by_gate: dict[str, list[dict[str, Any]]] = {
        str(gate["gate_id"]): [] for gate in gates
    }
    for event in gate_events:
        events_by_gate.setdefault(str(event["gate_id"]), []).append(event)
    for gate in gates:
        gate["freshness"] = GateStore._freshness(
            events_by_gate[str(gate["gate_id"])]
        )

    at_jobs = []
    for row in raw_jobs:
        job = {column: row[column] for column in PUBLIC_AT_JOB_COLUMNS}
        job["reconcile_note_sha256"] = hashlib.sha256(
            str(row["reconcile_note"] or "").encode("utf-8")
        ).hexdigest()
        at_jobs.append(job)
    at_jobs.sort(key=lambda row: (str(row["created_at"]), str(row["job_key"])))
    at_claims = []
    for row in raw_claims:
        claim = {column: row[column] for column in PUBLIC_AT_CLAIM_COLUMNS}
        try:
            binding = json.loads(str(row["binding_json"]))
        except (TypeError, ValueError, json.JSONDecodeError) as exc:
            raise ValidationError("snapshot ma niepoprawny claim binding") from exc
        if not isinstance(binding, dict):
            raise ValidationError("snapshot claim binding nie jest obiektem")
        claim["operation"] = str(binding.get("operation") or "")
        claim["submission_rollback"] = bool(binding.get("submission_rollback", False))
        at_claims.append(claim)
    at_claims.sort(
        key=lambda row: (str(row["claimed_at"]), str(row["job_key"]))
    )
    return gates, gate_events, at_jobs, at_claims


def scheduler_anomalies(
    gates: Sequence[Mapping[str, Any]],
    at_jobs: Sequence[Mapping[str, Any]],
    at_job_claims: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    gate_states = {str(row["gate_id"]): str(row["state"]) for row in gates}
    claims = {str(row["job_key"]): row for row in at_job_claims}
    result: list[dict[str, Any]] = []
    for job in at_jobs:
        claim = claims.get(str(job["job_key"]))
        active = str(job["status"]) in AT_ACTIVE_STATUSES
        claim_hold = claim is not None and str(claim["status"]) in CLAIM_ACTIVE_STATUSES
        if gate_states.get(str(job["gate_id"])) in TERMINAL_STATES and (
            active or claim_hold
        ):
            result.append(
                {
                    "gate_id": str(job["gate_id"]),
                    "job_key": str(job["job_key"]),
                    "at_job_id": str(job.get("at_job_id") or ""),
                    "job_status": str(job["status"]),
                    "claim_status": str(claim.get("status") if claim else ""),
                }
            )
    return sorted(result, key=lambda item: (item["gate_id"], item["job_key"]))


def export_payload(store: GateStore, *, as_of: datetime) -> dict[str, Any]:
    # Jedyna granica schematu eksportu: legalne v1/v2 są atomowo migrowane,
    # a niekanoniczny stan failuje w `initialize`. Dopiero potem powstaje jeden
    # read snapshot dla hasha i zredagowanych rekordów.
    store.initialize()
    material = canonical_ledger_material(store)
    gates, gate_events, at_jobs, at_claims = public_ledger_projection(material)
    return {
        "schema_version": material["schema_version"],
        "export_format_version": 2,
        "generated_at": iso_utc(as_of),
        "source": "process-gates-ledger",
        "ledger_hash": sha256_json(material),
        "gates": gates,
        "gate_events": gate_events,
        "at_jobs": at_jobs,
        "at_job_claims": at_claims,
        "scheduler_anomalies": scheduler_anomalies(gates, at_jobs, at_claims),
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

    note = subparsers.add_parser(
        "note", help="audytowana adnotacja CAS bez zmiany stanu"
    )
    note.add_argument("gate_id")
    note.add_argument("--expected-version", required=True, type=int)
    note.add_argument("--actor", required=True)
    note.add_argument("--reason", required=True)
    note.add_argument("--next-step")
    note.add_argument("--blocker")
    note.add_argument("--code-sha")
    note.add_argument("--evidence-hash")

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
        elif args.command == "note":
            result = store.note(
                args.gate_id,
                expected_version=args.expected_version,
                actor=args.actor,
                reason=args.reason,
                next_step=args.next_step,
                blocker=args.blocker,
                code_sha=args.code_sha,
                evidence_hash=args.evidence_hash,
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
                payload = export_payload(store, as_of=as_of)
                output = render_open_gates(
                    payload["gates"],
                    as_of=as_of,
                    source=str(payload["source"]),
                    ledger_hash=str(payload["ledger_hash"]),
                    scheduler_anomalies=payload["scheduler_anomalies"],
                )
            if args.output != "-":
                atomic_write(Path(args.output), output)
                print(json.dumps({"written": args.output}, ensure_ascii=False))
                return 0
        sys.stdout.write(output)
        return 0
    except GateError as exc:
        print(json.dumps({"error": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
