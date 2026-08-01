#!/usr/bin/env python3
"""Bezpieczny wrapper ``at`` powiązany z kanoniczną bramką procesową.

``schedule`` najpierw zapisuje intencję, potem planuje samorozliczający runner.
``reconcile`` nie zmienia kolejki: porównuje ją z bazą i podnosi ALARM, gdy
zarejestrowany job zniknął bez terminalnego statusu.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import selectors
import secrets
import shlex
import stat
import subprocess
import sys
import time
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from process_debt_gate import (
    DEFAULT_DB,
    ClaimConflict,
    GateError,
    GateStore,
    ReceiptError,
    SEALED_AUTH_VERSION,
    ValidationError,
    canonical_argv_hash,
    canonical_json,
    claim_receipt_path,
    ensure_private_directory,
    iso_utc,
    load_claim_receipt,
    parse_timestamp,
    read_private_bytes,
    runner_auth_binding,
    runner_auth_tag,
    utc_now,
)


_AT_JOB_RE = re.compile(r"\bjob\s+(\d+)\b", re.I)
_ATQ_RE = re.compile(r"^\s*(\d+)\s+")
_PAYLOAD_KEYS = {
    "schema_version",
    "db_path",
    "job_key",
    "gate_id",
    "runner_token",
    "command",
    "command_sha256",
    "scheduled_for",
    "artifact_root",
}
_MAX_PRIVATE_FILE_BYTES = 4 * 1024 * 1024
_PIPE_EOF_GRACE_SECONDS = 5.0


class StreamCaptureUnknown(RuntimeError):
    """Child powstał, lecz wrapper nie może poświadczyć kompletnego outputu."""


class ExecutionCapture:
    """Minimalna prawda, którą wolno później poświadczyć w receipcie."""

    __slots__ = (
        "exit_code",
        "child_started",
        "direct_child_exit_observed",
        "stdio_eof_observed",
    )

    def __init__(
        self,
        exit_code: int,
        child_started: bool,
        direct_child_exit_observed: bool,
        stdio_eof_observed: bool,
    ) -> None:
        self.exit_code = int(exit_code)
        self.child_started = bool(child_started)
        self.direct_child_exit_observed = bool(direct_child_exit_observed)
        self.stdio_eof_observed = bool(stdio_eof_observed)

# ── Trwały log przebiegu at-joba ────────────────────────────────────────────────
# LUKA SYSTEMOWA (bramka eta.gps-remeasure-checkpoint, ALARM 2026-07-25): ``at``
# oddaje wyjście zadania przez ``mail``, którego na tym hoście NIE MA. Skutek:
# stdout/stderr runnera szedł donikąd, więc KAŻDA porażka była cicha — job #225
# zniknął z kolejki bez terminalnego statusu i bez jednego bajtu diagnostyki.
#
# Naprawa u źródła: runner zapisuje własny log NA WEJŚCIU, zanim odpali komendę.
# Dzięki temu ślad zostaje nawet wtedy, gdy runner wywróci się przed zapisem do DB —
# a to był dokładnie przypadek #225 (brak wpisu terminalnego = ``finish_at_job``
# nigdy się nie wykonał). Logowanie wyłącznie po fakcie by tego NIE wykryło.
#
# Log NIE zawiera runner-tokenu; nowe joby niosą go wyłącznie w pliku 0600,
# którego identity jest związane z ledgerem i który znika zaraz po RUN claimie.
AT_LOG_DIR = Path(os.environ.get("AT_GATE_LOG_DIR", "/root/handover/at_logs"))


def _identity_from_stat(file_stat: os.stat_result, data: bytes) -> dict[str, int | str]:
    return {
        "sha256": hashlib.sha256(data).hexdigest(),
        "device": int(file_stat.st_dev),
        "inode": int(file_stat.st_ino),
        "ctime_ns": int(file_stat.st_ctime_ns),
        "size": len(data),
    }


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0))
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _ensure_private_directory(path: Path) -> Path:
    return ensure_private_directory(path, create=True)


def _exclusive_private_write(path: Path, data: bytes) -> dict[str, int | str]:
    if len(data) > _MAX_PRIVATE_FILE_BYTES:
        raise ValidationError("sealed payload przekracza limit")
    parent = _ensure_private_directory(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    try:
        file_stat = os.fstat(descriptor)
        if not stat.S_ISREG(file_stat.st_mode):
            raise ValidationError("sealed payload nie jest zwykłym plikiem")
        with os.fdopen(descriptor, "wb", closefd=False) as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        final_stat = os.fstat(descriptor)
    finally:
        os.close(descriptor)
    _fsync_directory(parent)
    return _identity_from_stat(final_stat, data)


def _create_private_child(parent: Path, component: str) -> Path:
    parent = _ensure_private_directory(parent)
    if not component or component in {".", ".."} or "/" in component or "\x00" in component:
        raise ValidationError("niepoprawny komponent prywatnego katalogu")
    child = parent / component
    try:
        child.mkdir(mode=0o700)
        _fsync_directory(parent)
    except FileExistsError:
        pass
    return _ensure_private_directory(child)


def _atomic_private_publish(path: Path, data: bytes) -> dict[str, int | str]:
    """Opublikuj immutable plik bez okna częściowego odczytu i bez overwrite."""

    parent = _ensure_private_directory(path.parent)
    temporary = parent / f".{path.name}.{uuid.uuid4().hex}.tmp"
    _exclusive_private_write(temporary, data)
    try:
        os.link(temporary, path, follow_symlinks=False)
        _fsync_directory(parent)
    except FileExistsError as exc:
        raise ReceiptError(f"durable receipt już istnieje: {path}") from exc
    finally:
        try:
            temporary.unlink()
            _fsync_directory(parent)
        except FileNotFoundError:
            pass
    published, identity = _read_private_bytes(path)
    if published != data:
        raise ReceiptError("durable receipt zmienił się podczas publikacji")
    return identity


def _open_private_output(path: Path):
    parent = _ensure_private_directory(path.parent)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(path, flags, 0o600)
    file_stat = os.fstat(descriptor)
    if (
        not stat.S_ISREG(file_stat.st_mode)
        or file_stat.st_uid != os.geteuid()
        or stat.S_IMODE(file_stat.st_mode) != 0o600
    ):
        os.close(descriptor)
        raise ValidationError("prywatny output nie spełnia owner/mode")
    _fsync_directory(parent)
    return os.fdopen(descriptor, "wb")


def _read_private_bytes(path: Path) -> tuple[bytes, dict[str, int | str]]:
    _ensure_private_directory(path.parent)
    data, identity = read_private_bytes(path)
    return data, identity


def _unlink_exact_private_file(
    path: Path,
    expected_identity: Mapping[str, Any],
) -> None:
    """Usuń tylko ten sam sealed payload; nigdy podstawiony path/inode."""

    parent = _ensure_private_directory(path.parent)
    directory_fd = os.open(
        parent,
        os.O_RDONLY | getattr(os, "O_DIRECTORY", 0),
    )
    try:
        try:
            current = os.stat(path.name, dir_fd=directory_fd, follow_symlinks=False)
        except OSError as exc:
            raise ValidationError(f"sealed payload cleanup lookup failed: {exc}") from exc
        actual = {
            "device": int(current.st_dev),
            "inode": int(current.st_ino),
            "ctime_ns": int(current.st_ctime_ns),
            "size": int(current.st_size),
        }
        expected = {
            key: int(expected_identity[key])
            for key in ("device", "inode", "ctime_ns", "size")
        }
        if not stat.S_ISREG(current.st_mode) or actual != expected:
            raise ValidationError("sealed payload cleanup identity mismatch")
        os.unlink(path.name, dir_fd=directory_fd)
        os.fsync(directory_fd)
    finally:
        os.close(directory_fd)


def _load_sealed_payload(path: Path) -> tuple[dict[str, Any], dict[str, int | str]]:
    data, identity = _read_private_bytes(path)
    try:
        payload = json.loads(data.decode("utf-8"))
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError("sealed payload: niepoprawny JSON") from exc
    if not isinstance(payload, dict) or set(payload) != _PAYLOAD_KEYS:
        raise ValidationError("sealed payload: exact shape mismatch")
    if payload["schema_version"] != SEALED_AUTH_VERSION:
        raise ValidationError(
            f"sealed payload: schema_version musi wynosić {SEALED_AUTH_VERSION}"
        )
    command = payload["command"]
    if not isinstance(command, list) or canonical_argv_hash(command) != payload["command_sha256"]:
        raise ValidationError("sealed payload: command/hash mismatch")
    if not isinstance(payload["db_path"], str) or not Path(payload["db_path"]).is_absolute():
        raise ValidationError("sealed payload.db_path: wymagana ścieżka absolutna")
    if not isinstance(payload["artifact_root"], str) or not Path(
        payload["artifact_root"]
    ).is_absolute():
        raise ValidationError("sealed payload.artifact_root: wymagana ścieżka absolutna")
    for key in ("job_key", "gate_id", "runner_token", "scheduled_for"):
        if not isinstance(payload[key], str) or not payload[key]:
            raise ValidationError(f"sealed payload.{key}: wymagana wartość")
    return payload, identity


def _run_log_path(job_key: str) -> Path:
    safe = re.sub(r"[^A-Za-z0-9_.-]", "_", job_key)[:80] or "unknown"
    return AT_LOG_DIR / f"{utc_now().strftime('%Y%m%dT%H%M%SZ')}-{safe}.log"


def _append_run_log(path: Path | None, text: str) -> None:
    """Zapis best-effort: awaria logowania NIGDY nie może wywrócić runnera."""
    if path is None:
        return
    try:
        with path.open("a", encoding="utf-8", errors="replace") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
    except OSError:
        pass


def _open_run_log(job_key: str) -> Path | None:
    """Tworzy log i od razu wypisuje nagłówek, żeby crash też zostawił ślad.

    Otwierany PRZED dekodowaniem ``--command-b64`` — inaczej wywrotka na samym
    dekodowaniu argumentu też byłaby cicha.
    """
    try:
        AT_LOG_DIR.mkdir(parents=True, exist_ok=True)
        path = _run_log_path(job_key)
    except OSError:
        return None
    _append_run_log(
        path,
        f"=== at_gate run START {iso_utc(utc_now())} ===\n"
        f"job_key: {job_key}\n"
        f"pid:     {os.getpid()}\n",
    )
    return path


def _run_process(
    command: Sequence[str],
    *,
    stdin: str | None = None,
    timeout: float = 30.0,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
        env=dict(env) if env is not None else None,
    )


def _at_time(value: datetime) -> str:
    """Format -t w lokalnej strefie hosta; w bazie pozostaje UTC."""
    local = value.astimezone()
    return local.strftime("%Y%m%d%H%M.%S")


def _encode_command(command: Sequence[str]) -> str:
    payload = canonical_json(list(command)).encode("utf-8")
    return base64.urlsafe_b64encode(payload).decode("ascii")


def _decode_command(value: str) -> list[str]:
    try:
        decoded = base64.urlsafe_b64decode(value.encode("ascii"))
        command = json.loads(decoded.decode("utf-8"))
    except (ValueError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValidationError(f"niepoprawny command-b64: {exc}") from exc
    if not isinstance(command, list) or not command or any(
        not isinstance(item, str) or not item for item in command
    ):
        raise ValidationError("zakodowana komenda musi być niepustą listą argumentów")
    return command


def _parse_atq(text: str) -> set[str]:
    result: set[str] = set()
    for line in text.splitlines():
        match = _ATQ_RE.match(line)
        if match:
            result.add(match.group(1))
    return result


def _rollback_accepted_submission(
    store: GateStore,
    *,
    job_key: str,
    at_job_id: str,
    actor: str,
    reason: str,
) -> Mapping[str, Any]:
    """Zapisz logiczny tombstone; numeryczne ID nigdy nie autoryzuje ``atrm``."""

    try:
        cancel_claim = store.begin_at_submission_cancellation(
            job_key,
            at_job_id,
            actor=actor,
            reason=reason,
        )
    except GateError as exc:
        raise GateError(
            f"job #{at_job_id} nie został potwierdzony w DB; "
            f"logiczny CANCEL claim nie powstał: {exc}"
        ) from exc
    early_runner_aborted = cancel_claim["binding"].get("early_runner_aborted") is True
    finalized = store.finalize_at_submission_cancellation(
        job_key,
        cancel_claim_id=str(cancel_claim["claim_id"]),
        at_job_id=at_job_id,
        actor=actor,
        reason=reason,
    )
    # Dwa atomowe porządki są bezpieczne:
    # 1. CANCEL wygrał pierwszy -> późniejszy wrapper widzi tombstone i sprząta;
    # 2. runner zdążył zapisać EARLY_RUNNER_ABORTED -> już nie wróci, więc
    #    finalizer sprząta dokładnie payload związany z rekordem joba.
    if early_runner_aborted:
        _cleanup_finalized_payload(finalized)
    return finalized


def schedule(args: argparse.Namespace) -> int:
    command = list(args.command)
    if command and command[0] == "--":
        command = command[1:]
    if not command:
        raise ValidationError("po `--` wymagana jest komenda do wykonania")
    scheduled = parse_timestamp(args.when, "when")
    now = utc_now()
    if scheduled <= now:
        raise ValidationError("when musi wskazywać przyszłość")

    store = GateStore(args.db)
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    job_key = f"at-{uuid.uuid4().hex}"
    command_sha256 = canonical_argv_hash(command)
    database_path = str(Path(args.db).expanduser().absolute())
    payload_root = _ensure_private_directory(
        Path(
            getattr(args, "payload_dir", "")
            or Path(database_path).parent / "at-payloads"
        )
    )
    artifact_root = _ensure_private_directory(
        Path(
            getattr(args, "artifact_dir", "")
            or Path(database_path).parent / "at-results"
        )
    )
    payload_path = payload_root / f"{job_key}.json"
    payload = {
        "schema_version": SEALED_AUTH_VERSION,
        "db_path": database_path,
        "job_key": job_key,
        "gate_id": args.gate_id,
        "runner_token": token,
        "command": command,
        "command_sha256": command_sha256,
        "scheduled_for": iso_utc(scheduled),
        "artifact_root": str(artifact_root),
    }
    payload_identity = _exclusive_private_write(
        payload_path,
        (canonical_json(payload) + "\n").encode("utf-8"),
    )
    binding = runner_auth_binding(
        job_key=job_key,
        gate_id=args.gate_id,
        scheduled_for=iso_utc(scheduled),
        command_sha256=command_sha256,
        payload_sha256=str(payload_identity["sha256"]),
        artifact_root=str(artifact_root),
    )
    try:
        store.register_at_job(
            gate_id=args.gate_id,
            title=args.title,
            owner=args.owner,
            due_at=args.due_at,
            blocker=args.blocker,
            code_sha=args.code_sha,
            evidence_hash=args.evidence_hash,
            opened_at=args.opened_at,
            actor=args.actor,
            job_key=job_key,
            runner_token_hash=token_hash,
            scheduled_for=iso_utc(scheduled),
            command=command,
            runner_auth_hmac=runner_auth_tag(token, binding),
            payload_path=str(payload_path),
            payload_identity=payload_identity,
            artifact_root=str(artifact_root),
        )
    except Exception:
        _unlink_exact_private_file(payload_path, payload_identity)
        raise

    runner = [
        sys.executable,
        str(Path(__file__).resolve()),
        "run",
        "--payload-file",
        str(payload_path),
    ]
    shell_line = " ".join(shlex.quote(part) for part in runner) + "\n"
    try:
        result = _run_process(
            [args.at_bin, "-t", _at_time(scheduled)],
            stdin=shell_line,
            env={
                "PATH": "/usr/bin:/bin",
                "LANG": "C.UTF-8",
                "LC_ALL": "C.UTF-8",
                "TZ": "UTC",
                "SHELL": "/bin/sh",
            },
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        try:
            store.fail_at_submission(job_key, f"at niedostępne: {exc}")
        finally:
            _unlink_exact_private_file(payload_path, payload_identity)
        raise GateError(f"nie udało się uruchomić at: {exc}") from exc
    combined = "\n".join(part for part in (result.stdout, result.stderr) if part)
    match = _AT_JOB_RE.search(combined)
    if match is None:
        detail = combined.strip().replace("\n", " ")[:400] or "brak identyfikatora joba"
        try:
            store.fail_at_submission(
                job_key,
                f"at zwróciło kod {result.returncode}: {detail}",
            )
        finally:
            _unlink_exact_private_file(payload_path, payload_identity)
        raise GateError(f"at nie potwierdziło joba: {detail}")

    at_job_id = match.group(1)
    if result.returncode != 0:
        detail = combined.strip().replace("\n", " ")[:400]
        cancel_reason = (
            f"at zwróciło kod {result.returncode} po ID #{at_job_id}: {detail}"
        )
        try:
            _rollback_accepted_submission(
                store,
                job_key=job_key,
                at_job_id=at_job_id,
                actor=args.actor,
                reason=cancel_reason,
            )
        except GateError as rollback_exc:
            raise GateError(
                f"at zwróciło rc={result.returncode} z ID #{at_job_id}; "
                f"rollback pozostaje fail-closed: {rollback_exc}"
            ) from rollback_exc
        raise GateError(
            f"at zwróciło rc={result.returncode} z ID #{at_job_id}; "
            "logiczny CANCEL zapisany i sfinalizowany bez destrukcji po ID"
        )
    try:
        job = store.confirm_at_job(job_key, at_job_id, actor=args.actor)
    except Exception as exc:
        # Commit mógł się udać, a caller dostać błąd po commicie. Dokładny
        # SCHEDULED/ID jest wtedy bezpiecznym idempotentnym sukcesem; każdy
        # inny stan wymaga jedynej DB-first ścieżki anulowania.
        try:
            observed = store.show_at_job(job_key)
        except GateError:
            observed = None
        if (
            observed is not None
            and observed["status"] == "SCHEDULED"
            and str(observed.get("at_job_id") or "") == at_job_id
        ):
            job = observed
        else:
            cancel_reason = f"confirm DB nieudany: {type(exc).__name__}: {exc}"
            try:
                _rollback_accepted_submission(
                    store,
                    job_key=job_key,
                    at_job_id=at_job_id,
                    actor=args.actor,
                    reason=cancel_reason,
                )
            except GateError as rollback_exc:
                raise GateError(str(rollback_exc)) from exc
            raise GateError(
                f"job #{at_job_id} nie został potwierdzony w DB; "
                "logiczny CANCEL zapisany i sfinalizowany bez destrukcji po ID"
            ) from exc
    print(
        json.dumps(
            {
                "status": "SCHEDULED",
                "gate_id": args.gate_id,
                "job_key": job_key,
                "at_job_id": at_job_id,
                "scheduled_for": job["scheduled_for"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def _prepare_claim_artifact_paths(
    artifact_root: Path,
    job_key: str,
    claim_id: str,
) -> tuple[Path, Path, Path]:
    artifact_root = _ensure_private_directory(artifact_root)
    expected_receipt = Path(
        claim_receipt_path(
            artifact_root=str(artifact_root),
            job_key=job_key,
            claim_id=claim_id,
        )
    )
    job_component = expected_receipt.parent.parent.name
    job_dir = _create_private_child(artifact_root, job_component)
    claim_dir = _create_private_child(job_dir, claim_id)
    receipt_path = claim_dir / "receipt.json"
    if receipt_path != expected_receipt:
        raise ReceiptError("claim receipt path nie pochodzi z kanonicznego ownera")
    return receipt_path, claim_dir / "stdout.bin", claim_dir / "stderr.bin"


def _discard_unclaimed_artifacts(paths: Sequence[Path]) -> None:
    """Usuń tylko prywatne artefakty przygotowane przed przegranym claimem."""

    parents: list[Path] = []
    for path in paths:
        parents.extend((path.parent, path.parent.parent))
        try:
            info = path.lstat()
        except FileNotFoundError:
            continue
        if (
            stat.S_ISREG(info.st_mode)
            and info.st_uid == os.geteuid()
            and stat.S_IMODE(info.st_mode) == 0o600
        ):
            path.unlink()
            _fsync_directory(path.parent)
    for parent in sorted(set(parents), key=lambda value: len(value.parts), reverse=True):
        try:
            parent.rmdir()
            _fsync_directory(parent.parent)
        except OSError:
            pass


def _claim_artifact_paths(claim: Mapping[str, Any]) -> tuple[Path, Path, Path]:
    binding = claim.get("binding")
    if not isinstance(binding, Mapping):
        raise ReceiptError("claim nie zawiera kanonicznego binding")
    artifact_root = Path(str(binding.get("artifact_root") or ""))
    job_key = str(claim.get("job_key") or "")
    claim_id = str(claim.get("claim_id") or "")
    receipt_path, stdout_path, stderr_path = _prepare_claim_artifact_paths(
        artifact_root,
        job_key,
        claim_id,
    )
    if str(receipt_path) != str(claim.get("receipt_path") or ""):
        raise ReceiptError("claim receipt path nie zgadza się z prywatnym drzewem")
    if binding.get("receipt_path") != str(receipt_path):
        raise ReceiptError("binding receipt path nie zgadza się z claimem")
    return receipt_path, stdout_path, stderr_path


def _stream_record(path: Path) -> tuple[bytes, dict[str, int | str], dict[str, Any]]:
    data, identity = _read_private_bytes(path)
    return data, identity, {
        "path": str(path),
        "sha256": identity["sha256"],
        "device": identity["device"],
        "inode": identity["inode"],
        "ctime_ns": identity["ctime_ns"],
        "size": identity["size"],
    }


def _write_result_receipt(
    *,
    claim: Mapping[str, Any],
    command: Sequence[str],
    execution: ExecutionCapture,
    stdout_path: Path,
    stderr_path: Path,
) -> tuple[dict[str, Any], dict[str, int | str], bytes, bytes]:
    receipt_path = Path(str(claim["receipt_path"]))
    stdout, _stdout_identity, stdout_record = _stream_record(stdout_path)
    stderr, _stderr_identity, stderr_record = _stream_record(stderr_path)
    receipt = {
        "schema_version": 3,
        "job_key": claim["job_key"],
        "gate_id": claim["gate_id"],
        "claim_id": claim["claim_id"],
        "binding_sha256": claim["binding_sha256"],
        "command_sha256": canonical_argv_hash(command),
        "exit_code": int(execution.exit_code),
        "created_at": iso_utc(utc_now()),
        "execution": {
            "child_started": execution.child_started,
            "direct_child_exit_observed": execution.direct_child_exit_observed,
            "stdio_eof_observed": execution.stdio_eof_observed,
        },
        "stdout": stdout_record,
        "stderr": stderr_record,
    }
    identity = _atomic_private_publish(
        receipt_path,
        (canonical_json(receipt) + "\n").encode("utf-8"),
    )
    return receipt, identity, stdout, stderr


def _load_result_receipt(
    path: Path,
    *,
    claim: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, int | str], bytes, bytes]:
    receipt, identity, stdout, stderr = load_claim_receipt(path, claim=claim)
    return receipt, identity, stdout, stderr


def _cleanup_finalized_payload(job: Mapping[str, Any]) -> None:
    if int(job.get("auth_version") or 1) != SEALED_AUTH_VERSION:
        return
    path = Path(str(job.get("payload_path") or ""))
    if not path.exists() and not path.is_symlink():
        return
    identity = {
        "sha256": job.get("payload_sha256"),
        "device": job.get("payload_dev"),
        "inode": job.get("payload_ino"),
        "ctime_ns": job.get("payload_ctime_ns"),
        "size": job.get("payload_size"),
    }
    _unlink_exact_private_file(path, identity)


def _recover_existing(
    store: GateStore,
    job_key: str,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], bytes, bytes]:
    """Odtwórz DB wyłącznie z istniejącego receiptu; nigdy nie re-exec."""

    job = store.show_at_job(job_key)
    claim = store.show_at_claim(job_key)
    receipt_path = Path(str(claim.get("receipt_path") or ""))
    if not receipt_path.is_absolute() or not receipt_path.exists():
        raise ReceiptError("OUTCOME_UNKNOWN: brak durable receipt; re-exec zabroniony")
    receipt, identity, stdout, stderr = _load_result_receipt(
        receipt_path,
        claim=claim,
    )
    if claim["status"] in {"CLAIMED", "OUTCOME_UNKNOWN"}:
        store.record_at_receipt(
            job_key,
            claim_id=str(claim["claim_id"]),
            receipt_path=str(receipt_path),
            receipt_identity=identity,
            exit_code=int(receipt["exit_code"]),
            stdout_sha256=str(receipt["stdout"]["sha256"]),
            stderr_sha256=str(receipt["stderr"]["sha256"]),
        )
        claim = store.show_at_claim(job_key)
    if claim["status"] == "RECEIPT_READY":
        store.finalize_at_claim(
            job_key,
            claim_id=str(claim["claim_id"]),
            receipt_identity=identity,
        )
    job = store.show_at_job(job_key)
    claim = store.show_at_claim(job_key)
    if claim["status"] != "FINALIZED":
        raise ReceiptError(f"recovery nie sfinalizował claimu: {claim['status']}")
    _cleanup_finalized_payload(job)
    return job, claim, receipt, stdout, stderr


def run_registered(args: argparse.Namespace) -> int:
    payload_path: Path | None = None
    payload_identity: Mapping[str, Any] | None = None
    require_auth_version: int | None = None
    if getattr(args, "payload_file", None):
        payload_path = Path(args.payload_file).absolute()
        log_path = _open_run_log(f"sealed-{payload_path.name}")
    else:
        log_path = _open_run_log(getattr(args, "job_key", None) or "legacy-unknown")
    try:
        if payload_path is not None:
            payload, payload_identity = _load_sealed_payload(payload_path)
            args.db = str(payload["db_path"])
            args.job_key = str(payload["job_key"])
            args.token = str(payload["runner_token"])
            args.artifact_root = str(payload["artifact_root"])
            command = list(payload["command"])
            require_auth_version = SEALED_AUTH_VERSION
        else:
            if not args.job_key or not args.token or not args.command_b64:
                raise ValidationError(
                    "legacy run wymaga --job-key, --token i --command-b64"
                )
            command = _decode_command(args.command_b64)
        _append_run_log(
            log_path,
            f"argv_sha256: {canonical_argv_hash(command)}\n"
            f"argc:        {len(command)}\n\n",
        )
        return _run_registered_inner(
            args,
            command,
            log_path,
            payload_path=payload_path,
            payload_identity=payload_identity,
            require_auth_version=require_auth_version,
        )
    except BaseException as exc:  # noqa: BLE001 — ślad MUSI zostać także przy SystemExit/KeyboardInterrupt
        _append_run_log(
            log_path,
            f"\n=== at_gate WYWROCIL SIE {iso_utc(utc_now())} ===\n"
            f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}\n",
        )
        raise


def _execute_with_owned_streams(
    command: Sequence[str],
    *,
    child_env: Mapping[str, str],
    stdout_handle: Any,
    stderr_handle: Any,
) -> ExecutionCapture:
    """Uruchom childa, lecz zachowaj jedyne prawo zapisu plików w wrapperze.

    Child i jego potomkowie dostają pipe'y, nie deskryptory plików. Receipt
    może powstać dopiero po exit bezpośredniego procesu i EOF obu pipe'ów, więc
    żaden odziedziczony writer nie dopisze po ``FINALIZED``.
    """

    try:
        process = subprocess.Popen(
            list(command),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=dict(child_env),
        )
    except (OSError, ValueError) as exc:
        stderr_handle.write(
            (
                "at_gate: nie można uruchomić komendy: "
                f"{type(exc).__name__}: {exc}\n"
            ).encode("utf-8", "replace")
        )
        return ExecutionCapture(
            exit_code=127,
            child_started=False,
            direct_child_exit_observed=False,
            stdio_eof_observed=True,
        )

    assert process.stdout is not None and process.stderr is not None
    selector = selectors.DefaultSelector()
    targets = {
        process.stdout: (stdout_handle, "stdout"),
        process.stderr: (stderr_handle, "stderr"),
    }
    written = {"stdout": 0, "stderr": 0}
    failure = ""
    direct_exit_at: float | None = None
    try:
        for pipe in targets:
            os.set_blocking(pipe.fileno(), False)
            selector.register(pipe, selectors.EVENT_READ)
        while selector.get_map():
            events = selector.select(timeout=0.2)
            if process.poll() is not None and direct_exit_at is None:
                direct_exit_at = time.monotonic()
            for key, _mask in events:
                pipe = key.fileobj
                target, name = targets[pipe]
                try:
                    chunk = os.read(pipe.fileno(), 64 * 1024)
                except (OSError, ValueError):
                    failure = f"błąd odczytu pipe {name}"
                    selector.unregister(pipe)
                    pipe.close()
                    break
                if not chunk:
                    selector.unregister(pipe)
                    pipe.close()
                    continue
                remaining = max(0, _MAX_PRIVATE_FILE_BYTES - written[name])
                if remaining:
                    accepted = chunk[:remaining]
                    target.write(accepted)
                    written[name] += len(accepted)
                if len(chunk) > remaining:
                    failure = f"{name} przekroczył limit {_MAX_PRIVATE_FILE_BYTES} B"
                    break
            if failure:
                break
            if (
                direct_exit_at is not None
                and selector.get_map()
                and time.monotonic() - direct_exit_at >= _PIPE_EOF_GRACE_SECONDS
            ):
                failure = (
                    "brak EOF stdout/stderr po wyjściu bezpośredniego childa "
                    f"przez {_PIPE_EOF_GRACE_SECONDS:.1f}s"
                )
                break
    finally:
        selector.close()
        for pipe in targets:
            if not pipe.closed:
                pipe.close()

    if failure:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=_PIPE_EOF_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
        raise StreamCaptureUnknown(failure)
    return ExecutionCapture(
        exit_code=int(process.wait()),
        child_started=True,
        direct_child_exit_observed=True,
        stdio_eof_observed=True,
    )


def _run_registered_inner(
    args: argparse.Namespace,
    command: Sequence[str],
    log_path: Path | None,
    *,
    payload_path: Path | None = None,
    payload_identity: Mapping[str, Any] | None = None,
    require_auth_version: int | None = None,
) -> int:
    command_sha256 = canonical_argv_hash(command)
    store = GateStore(args.db)
    artifact_root = _ensure_private_directory(
        Path(
            getattr(args, "artifact_root", "")
            or Path(args.db).expanduser().absolute().parent / "at-results"
        )
    )
    proposed_claim_id = f"claim-{uuid.uuid4().hex}"
    prepared_paths = _prepare_claim_artifact_paths(
        artifact_root,
        str(args.job_key),
        proposed_claim_id,
    )
    receipt_path, stdout_path, stderr_path = prepared_paths
    child_env = dict(os.environ)
    child_env.update(
        {
            "AT_GATE_DB": str(Path(args.db).expanduser().absolute()),
            "AT_GATE_JOB_KEY": str(args.job_key),
            "AT_GATE_CLAIM_ID": proposed_claim_id,
            "AT_GATE_COMMAND_SHA256": command_sha256,
            "HOME": child_env.get("HOME", "/root"),
            "USER": child_env.get("USER", "root"),
            "LOGNAME": child_env.get("LOGNAME", "root"),
        }
    )
    claim: Mapping[str, Any] | None = None
    try:
        with _open_private_output(stdout_path) as stdout_handle, _open_private_output(
            stderr_path
        ) as stderr_handle:
            claim = store.claim_at_job(
                args.job_key,
                runner_token=args.token,
                command=command,
                payload_path=str(payload_path) if payload_path is not None else None,
                payload_identity=payload_identity,
                artifact_root=str(artifact_root),
                require_auth_version=require_auth_version,
                claim_id=proposed_claim_id,
                receipt_path=str(receipt_path),
            )
            if _claim_artifact_paths(claim) != prepared_paths:
                raise ReceiptError("claim zmienił przygotowane exact ścieżki outputu")
            try:
                attestation = store.verify_active_run_claim(
                    str(args.job_key),
                    claim_id=str(claim["claim_id"]),
                    command=command,
                )
            except GateError as exc:
                raise ClaimConflict(
                    f"pre-exec active RUN attestation failed: {exc}"
                ) from exc
            if not secrets.compare_digest(
                str(attestation["binding_sha256"]),
                str(claim["binding_sha256"]),
            ):
                raise ClaimConflict("pre-exec attestation binding digest drift")
            child_env["AT_GATE_GATE_ID"] = str(claim["gate_id"])
            execution = _execute_with_owned_streams(
                command,
                child_env=child_env,
                stdout_handle=stdout_handle,
                stderr_handle=stderr_handle,
            )
            exit_code = execution.exit_code
            stdout_handle.flush()
            stderr_handle.flush()
            os.fsync(stdout_handle.fileno())
            os.fsync(stderr_handle.fileno())
    except ClaimConflict as exc:
        if claim is not None:
            try:
                store.mark_run_outcome_unknown(
                    str(args.job_key),
                    claim_id=str(claim["claim_id"]),
                    reason=(
                        "pre-exec attestation failed before child start: "
                        f"{type(exc).__name__}: {exc}"
                    ),
                )
                db_note = (
                    "OUTCOME_UNKNOWN + ALARM zapisane; child nie został uruchomiony"
                )
            except GateError as db_exc:
                db_note = (
                    "ALARM: nie udało się zatrzasnąć pre-exec failure; "
                    "reconcile wymagany: "
                    f"{type(db_exc).__name__}: {db_exc}"
                )
            _discard_unclaimed_artifacts(prepared_paths)
            _append_run_log(
                log_path,
                "exit_code: 125\n"
                f"pre_exec:  {exc}\n"
                "subprocess: NIE URUCHOMIONO\n"
                f"db:        {db_note}\n"
                "receipt:   NIE OPUBLIKOWANO\n",
            )
            return 125
        _discard_unclaimed_artifacts(prepared_paths)
        existing_claim = store.show_at_claim(str(args.job_key))
        binding = existing_claim.get("binding")
        if isinstance(binding, Mapping) and binding.get("operation") == "CANCEL":
            job = store.show_at_job(str(args.job_key))
            _append_run_log(
                log_path,
                f"cancelled:  logiczny tombstone {existing_claim['claim_id']}\n"
                "subprocess:  NIE URUCHOMIONO\n",
            )
            _cleanup_finalized_payload(job)
            return 0
        job, existing_claim, receipt, stdout, stderr = _recover_existing(
            store,
            str(args.job_key),
        )
        _append_run_log(
            log_path,
            f"recovery:   existing durable receipt\n"
            f"claim:      {existing_claim['claim_id']}\n"
            f"evidence:   {existing_claim['receipt_sha256']}\n",
        )
        sys.stdout.buffer.write(stdout)
        sys.stderr.buffer.write(stderr)
        return int(receipt["exit_code"])
    except StreamCaptureUnknown as exc:
        assert claim is not None
        try:
            store.mark_run_outcome_unknown(
                str(args.job_key),
                claim_id=str(claim["claim_id"]),
                reason=str(exc),
            )
            db_note = "OUTCOME_UNKNOWN + ALARM zapisane; receipt nie powstał"
        except GateError as db_exc:
            db_note = (
                "ALARM: nie udało się zapisać OUTCOME_UNKNOWN; reconcile wymagany: "
                f"{type(db_exc).__name__}: {db_exc}"
            )
        _append_run_log(
            log_path,
            "exit_code: 125\n"
            f"capture:   {exc}\n"
            f"db:        {db_note}\n"
            "receipt:   NIE OPUBLIKOWANO\n",
        )
        return 125
    except BaseException:
        if claim is None:
            _discard_unclaimed_artifacts(prepared_paths)
        raise

    assert claim is not None

    receipt, receipt_identity, stdout, stderr = _write_result_receipt(
        claim=claim,
        command=command,
        execution=execution,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
    )
    verified, verified_identity, _verified_stdout, _verified_stderr = _load_result_receipt(
        receipt_path,
        claim=claim,
    )
    if verified != receipt or verified_identity != receipt_identity:
        raise ReceiptError("durable receipt nie jest stabilny po publikacji")
    try:
        store.record_at_receipt(
            args.job_key,
            claim_id=str(claim["claim_id"]),
            exit_code=exit_code,
            receipt_path=str(receipt_path),
            receipt_identity=receipt_identity,
            stdout_sha256=str(receipt["stdout"]["sha256"]),
            stderr_sha256=str(receipt["stderr"]["sha256"]),
        )
        store.finalize_at_claim(
            args.job_key,
            claim_id=str(claim["claim_id"]),
            receipt_identity=receipt_identity,
        )
    except GateError as exc:
        try:
            recovered_job, _recovered_claim, recovered_receipt, _out, _err = (
                _recover_existing(store, str(args.job_key))
            )
        except (GateError, OSError) as recovery_exc:
            returned_exit_code = 125
            db_note = (
                "ALARM: durable receipt istnieje, DB wymaga recovery: "
                f"{exc}; recovery={recovery_exc}"
            )
        else:
            returned_exit_code = int(recovered_receipt["exit_code"])
            db_note = f"OK — równoległe recovery sfinalizowało {recovered_job['status']}"
    else:
        returned_exit_code = exit_code
        db_note = "OK — durable receipt zapisany i sfinalizowany bez re-exec"
        _cleanup_finalized_payload(store.show_at_job(str(args.job_key)))
    _append_run_log(
        log_path,
        f"exit_code: {returned_exit_code}\n"
        f"db:        {db_note}\n"
        f"evidence:  {receipt_identity['sha256']}\n"
        f"--- stdout ({len(stdout)} B) ---\n{stdout.decode('utf-8', 'replace')}\n"
        f"--- stderr ({len(stderr)} B) ---\n{stderr.decode('utf-8', 'replace')}\n"
        f"=== at_gate run KONIEC {iso_utc(utc_now())} ===\n",
    )
    sys.stdout.buffer.write(stdout)
    sys.stderr.buffer.write(stderr)
    return returned_exit_code


def cancel(args: argparse.Namespace) -> int:
    """Cancel one exact registered job and close its intent atomically."""

    store = GateStore(args.db)
    job = store.show_at_job(args.job_key)
    if str(job.get("at_job_id") or "") != args.at_job_id:
        raise ValidationError(
            f"at job id drift: {job.get('at_job_id')!r} != {args.at_job_id!r}"
        )

    # Jedynym efektem jest atomowy tombstone w DB. Numericzne ID schedulera nie
    # identyfikuje generacji, dlatego NIGDY nie jest authority do destrukcji.
    cancel_claim = store.begin_at_job_cancellation(
        args.job_key,
        args.at_job_id,
        expected_gate_version=args.expected_gate_version,
        actor=args.actor,
        reason=args.reason,
    )
    if cancel_claim["status"] == "FINALIZED":
        cancelled = store.show_at_job(args.job_key)
        if cancelled["status"] != "CANCELLED":
            raise ClaimConflict("FINALIZED CANCEL nie ma terminalnego joba")
    else:
        cancelled = store.cancel_at_job(
            args.job_key,
            args.at_job_id,
            cancel_claim_id=str(cancel_claim["claim_id"]),
            expected_gate_version=args.expected_gate_version,
            actor=args.actor,
            reason=args.reason,
        )
    # Payload zostaje do chwili, gdy naturalnie dequeued wrapper odczyta auth2,
    # zobaczy FINALIZED CANCEL i potwierdzi no-op bez Popen. Dopiero wrapper robi
    # exact-GC tego własnego pliku.
    print(
        json.dumps(
            {
                "status": cancelled["status"],
                "gate_id": cancelled["gate_id"],
                "job_key": cancelled["job_key"],
                "at_job_id": cancelled["at_job_id"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


def recover(args: argparse.Namespace) -> int:
    job, claim, receipt, _stdout, _stderr = _recover_existing(
        GateStore(args.db),
        args.job_key,
    )
    print(
        json.dumps(
            {
                "status": job["status"],
                "job_key": job["job_key"],
                "claim_id": claim["claim_id"],
                "claim_status": claim["status"],
                "exit_code": receipt["exit_code"],
                "receipt_sha256": claim["receipt_sha256"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return int(receipt["exit_code"])


def reconcile(args: argparse.Namespace) -> int:
    present: set[str] | None
    unavailable_note = ""
    if args.atq_unavailable:
        present = None
        unavailable_note = "atq UNAVAILABLE wymuszone opcją"
    elif args.atq_file:
        try:
            present = _parse_atq(Path(args.atq_file).read_text(encoding="utf-8"))
        except OSError as exc:
            present = None
            unavailable_note = f"atq UNAVAILABLE: {exc}"
    else:
        try:
            result = _run_process([args.atq_bin])
        except (OSError, subprocess.TimeoutExpired) as exc:
            present = None
            unavailable_note = f"atq UNAVAILABLE: {exc}"
        else:
            if result.returncode == 0:
                present = _parse_atq(result.stdout)
            else:
                present = None
                detail = (result.stderr or result.stdout).strip().replace("\n", " ")[:300]
                unavailable_note = f"atq UNAVAILABLE ({result.returncode}): {detail}"
    store = GateStore(args.db)
    outcome = store.reconcile_at_jobs(present, note=unavailable_note)
    recovered: list[str] = []
    recovery_errors: list[dict[str, str]] = []
    recovered_failures: list[dict[str, Any]] = []
    resolved_during_recovery: set[str] = set()
    for job_key in outcome.get("recovery_candidates", []):
        try:
            job, claim, receipt, _stdout, _stderr = _recover_existing(
                store, str(job_key)
            )
        except (GateError, OSError) as exc:
            recovery_errors.append(
                {"job_key": str(job_key), "error": type(exc).__name__}
            )
            continue
        recovered.append(str(job_key))
        gate = store.show_gate(str(job["gate_id"]))
        if (
            int(receipt["exit_code"]) != 0
            or str(job["status"]) != "SUCCEEDED"
            or bool(gate["alarm"])
        ):
            recovered_failures.append(
                {
                    "job_key": str(job_key),
                    "job_status": str(job["status"]),
                    "claim_status": str(claim["status"]),
                    "exit_code": int(receipt["exit_code"]),
                    "gate_alarm": bool(gate["alarm"]),
                }
            )
            if not any(
                str(alarm.get("job_key")) == str(job_key)
                for alarm in outcome.get("alarms", [])
            ):
                outcome.setdefault("alarms", []).append(
                    {
                        "job_key": str(job_key),
                        "gate_id": str(job["gate_id"]),
                        "at_job_id": str(job.get("at_job_id") or ""),
                    }
                )
        else:
            resolved_during_recovery.add(str(job_key))
    finalized_recovered = set(recovered)
    if finalized_recovered:
        outcome["running"] = [
            job_key
            for job_key in outcome.get("running", [])
            if str(job_key) not in finalized_recovered
        ]
        outcome["recovery_candidates"] = [
            job_key
            for job_key in outcome.get("recovery_candidates", [])
            if str(job_key) not in finalized_recovered
        ]
        outcome["outcome_unknown"] = [
            job_key
            for job_key in outcome.get("outcome_unknown", [])
            if str(job_key) not in finalized_recovered
        ]
    if resolved_during_recovery:
        outcome["alarms"] = [
            alarm
            for alarm in outcome.get("alarms", [])
            if str(alarm.get("job_key")) not in resolved_during_recovery
        ]
    outcome["recovered"] = sorted(recovered)
    outcome["recovery_errors"] = recovery_errors
    outcome["recovered_failures"] = recovered_failures
    outcome["resolved_during_recovery"] = sorted(resolved_during_recovery)
    print(json.dumps(outcome, ensure_ascii=False, indent=2, sort_keys=True))
    return (
        2
        if outcome["status"] == "UNAVAILABLE"
        else (
            1
            if outcome["alarms"] or recovery_errors or recovered_failures
            else 0
        )
    )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", default=str(DEFAULT_DB))
    subparsers = parser.add_subparsers(dest="command_name", required=True)

    add = subparsers.add_parser("schedule", help="zarejestruj i zaplanuj at-job")
    add.add_argument("--id", required=True, dest="gate_id")
    add.add_argument("--title", required=True)
    add.add_argument("--owner", required=True)
    add.add_argument("--due", required=True, dest="due_at")
    add.add_argument("--when", required=True, help="ISO-8601 ze strefą")
    add.add_argument("--blocker", default="Oczekiwanie na termin at-joba")
    add.add_argument("--code-sha", required=True)
    add.add_argument("--evidence-hash", required=True)
    add.add_argument("--opened-at")
    add.add_argument("--actor", default="at_gate/schedule")
    add.add_argument("--at-bin", default="at", help=argparse.SUPPRESS)
    add.add_argument("--payload-dir", help=argparse.SUPPRESS)
    add.add_argument("--artifact-dir", help=argparse.SUPPRESS)
    add.add_argument("command", nargs=argparse.REMAINDER)

    runner = subparsers.add_parser("run", help=argparse.SUPPRESS)
    runner.add_argument("--payload-file")
    runner.add_argument("--job-key")
    runner.add_argument("--token")
    runner.add_argument("--command-b64")

    recover_parser = subparsers.add_parser(
        "recover", help="sfinalizuj istniejący durable receipt bez re-exec"
    )
    recover_parser.add_argument("--job-key", required=True)

    cancel_parser = subparsers.add_parser(
        "cancel",
        help="anuluj exact zarejestrowany at-job i zamknij jego intencję",
    )
    cancel_parser.add_argument("--job-key", required=True)
    cancel_parser.add_argument("--at-job-id", required=True)
    cancel_parser.add_argument(
        "--expected-gate-version",
        required=True,
        type=int,
    )
    cancel_parser.add_argument("--actor", required=True)
    cancel_parser.add_argument("--reason", required=True)

    reconcile_parser = subparsers.add_parser(
        "reconcile", help="porównaj aktywne wpisy DB z atq"
    )
    reconcile_parser.add_argument("--atq-file", help="fixture/snapshot zamiast atq")
    reconcile_parser.add_argument("--atq-unavailable", action="store_true")
    reconcile_parser.add_argument("--atq-bin", default="atq", help=argparse.SUPPRESS)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command_name == "schedule":
            return schedule(args)
        if args.command_name == "run":
            return run_registered(args)
        if args.command_name == "recover":
            return recover(args)
        if args.command_name == "cancel":
            return cancel(args)
        return reconcile(args)
    except (GateError, ValidationError) as exc:
        print(
            json.dumps({"error": type(exc).__name__, "detail": str(exc)}, ensure_ascii=False),
            file=sys.stderr,
        )
        return 3


if __name__ == "__main__":
    raise SystemExit(main())
