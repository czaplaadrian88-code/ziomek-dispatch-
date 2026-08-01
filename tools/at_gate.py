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
import secrets
import shlex
import stat
import subprocess
import sys
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Mapping, Sequence

from process_debt_gate import (
    DEFAULT_DB,
    GateError,
    GateStore,
    SEALED_AUTH_VERSION,
    ValidationError,
    canonical_argv_hash,
    canonical_json,
    iso_utc,
    parse_timestamp,
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
}
_MAX_PRIVATE_FILE_BYTES = 4 * 1024 * 1024

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
    path = path.absolute()
    if path.exists() or path.is_symlink():
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or not stat.S_ISDIR(info.st_mode):
            raise ValidationError(f"prywatny katalog nie jest zwykłym katalogiem: {path}")
    else:
        path.mkdir(mode=0o700, parents=True, exist_ok=False)
        _fsync_directory(path.parent)
        info = path.lstat()
    if path.resolve() != path:
        raise ValidationError(f"prywatny katalog ma symlink w ścieżce: {path}")
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700:
        raise ValidationError(f"prywatny katalog wymaga ownera procesu i mode 0700: {path}")
    return path


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


def _read_private_bytes(path: Path) -> tuple[bytes, dict[str, int | str]]:
    _ensure_private_directory(path.parent)
    descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    try:
        before = os.fstat(descriptor)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.geteuid()
            or stat.S_IMODE(before.st_mode) != 0o600
            or before.st_size > _MAX_PRIVATE_FILE_BYTES
        ):
            raise ValidationError("sealed payload nie spełnia owner/mode/size")
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = os.read(
                descriptor,
                min(1024 * 1024, _MAX_PRIVATE_FILE_BYTES + 1 - total),
            )
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > _MAX_PRIVATE_FILE_BYTES:
                raise ValidationError("sealed payload przekracza limit")
        data = b"".join(chunks)
        after = os.fstat(descriptor)
        if (
            before.st_dev,
            before.st_ino,
            before.st_ctime_ns,
            before.st_size,
        ) != (after.st_dev, after.st_ino, after.st_ctime_ns, after.st_size):
            raise ValidationError("sealed payload zmienił identity podczas odczytu")
    finally:
        os.close(descriptor)
    return data, _identity_from_stat(after, data)


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
    for key in ("job_key", "gate_id", "runner_token", "scheduled_for"):
        if not isinstance(payload[key], str) or not payload[key]:
            raise ValidationError(f"sealed payload.{key}: wymagana wartość")
    return payload, identity


def _discard_cancelled_sealed_payload(job: Mapping[str, Any]) -> None:
    if int(job.get("auth_version") or 1) != SEALED_AUTH_VERSION:
        return
    path_text = job.get("payload_path")
    if not isinstance(path_text, str) or not path_text:
        raise ValidationError("auth2 cancel: brak payload_path")
    identity: dict[str, Any] = {
        "sha256": job.get("payload_sha256"),
        "device": job.get("payload_dev"),
        "inode": job.get("payload_ino"),
        "ctime_ns": job.get("payload_ctime_ns"),
        "size": job.get("payload_size"),
    }
    if any(value is None for value in identity.values()):
        raise ValidationError("auth2 cancel: niepełne payload identity")
    path = Path(path_text)
    if path.exists() or path.is_symlink():
        _unlink_exact_private_file(path, identity)


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
    if result.returncode != 0 or match is None:
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
    try:
        job = store.confirm_at_job(job_key, at_job_id, actor=args.actor)
    except GateError as exc:
        try:
            rollback = _run_process([args.atrm_bin, at_job_id])
            detail = (
                "rollback atrm OK"
                if rollback.returncode == 0
                else "ALARM: rollback atrm FAILED"
            )
        except (OSError, subprocess.TimeoutExpired) as rollback_exc:
            detail = f"ALARM: rollback atrm UNAVAILABLE ({rollback_exc})"
        try:
            store.fail_at_submission(job_key, f"potwierdzenie DB nieudane; {detail}: {exc}")
        except GateError:
            pass
        _unlink_exact_private_file(payload_path, payload_identity)
        raise GateError(f"job #{at_job_id} nie został potwierdzony w DB; {detail}: {exc}") from exc
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


def _run_registered_inner(
    args: argparse.Namespace,
    command: Sequence[str],
    log_path: Path | None,
    *,
    payload_path: Path | None = None,
    payload_identity: Mapping[str, Any] | None = None,
    require_auth_version: int | None = None,
) -> int:
    store = GateStore(args.db)
    claim = store.claim_at_job(
        args.job_key,
        runner_token=args.token,
        command=command,
        payload_path=str(payload_path) if payload_path is not None else None,
        payload_identity=payload_identity,
        require_auth_version=require_auth_version,
    )
    if payload_path is not None:
        assert payload_identity is not None
        _unlink_exact_private_file(payload_path, payload_identity)
    child_env = dict(os.environ)
    child_env.update(
        {
            "AT_GATE_DB": str(Path(args.db).expanduser().absolute()),
            "AT_GATE_JOB_KEY": str(args.job_key),
            "AT_GATE_CLAIM_ID": str(claim["claim_id"]),
            "AT_GATE_GATE_ID": str(claim["gate_id"]),
            "AT_GATE_COMMAND_SHA256": canonical_argv_hash(command),
            "HOME": child_env.get("HOME", "/root"),
            "USER": child_env.get("USER", "root"),
            "LOGNAME": child_env.get("LOGNAME", "root"),
        }
    )
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            check=False,
            env=child_env,
        )
        exit_code = int(result.returncode)
        stdout = result.stdout
        stderr = result.stderr
    except OSError as exc:
        exit_code = 127
        stdout = b""
        stderr = f"at_gate: nie można uruchomić komendy: {exc}\n".encode("utf-8")
    evidence = hashlib.sha256(
        canonical_json({"argv": command, "exit_code": exit_code}).encode("utf-8")
        + b"\x00stdout\x00"
        + stdout
        + b"\x00stderr\x00"
        + stderr
    ).hexdigest()
    try:
        store.finish_at_job(
            args.job_key,
            claim_id=str(claim["claim_id"]),
            runner_token=args.token,
            exit_code=exit_code,
            evidence_hash=evidence,
            command=command,
        )
    except GateError as exc:
        stderr += f"\nat_gate: ALARM: wynik nie zapisany w DB: {exc}\n".encode("utf-8")
        exit_code = 125
        db_note = f"ALARM: wynik NIE zapisany w DB: {exc}"
    else:
        db_note = "OK — wynik zapisany w DB (finish_at_job)"
    _append_run_log(
        log_path,
        f"exit_code: {exit_code}\n"
        f"db:        {db_note}\n"
        f"evidence:  {evidence}\n"
        f"--- stdout ({len(stdout)} B) ---\n{stdout.decode('utf-8', 'replace')}\n"
        f"--- stderr ({len(stderr)} B) ---\n{stderr.decode('utf-8', 'replace')}\n"
        f"=== at_gate run KONIEC {iso_utc(utc_now())} ===\n",
    )
    sys.stdout.buffer.write(stdout)
    sys.stderr.buffer.write(stderr)
    return exit_code


def cancel(args: argparse.Namespace) -> int:
    """Cancel one exact registered job and close its intent atomically."""

    store = GateStore(args.db)
    job = store.show_at_job(args.job_key)
    if str(job.get("at_job_id") or "") != args.at_job_id:
        raise ValidationError(
            f"at job id drift: {job.get('at_job_id')!r} != {args.at_job_id!r}"
        )
    try:
        queue = _run_process([args.atq_bin])
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise GateError(
            f"nie można potwierdzić atq przed anulowaniem: {exc}"
        ) from exc
    if queue.returncode != 0:
        detail = (queue.stderr or queue.stdout).strip().replace("\n", " ")[:300]
        raise GateError(
            f"atq nie potwierdziło stanu przed anulowaniem: "
            f"rc={queue.returncode}: {detail}"
        )
    present = _parse_atq(queue.stdout)
    if args.at_job_id in present:
        if args.already_removed:
            raise ValidationError(
                f"at-job #{args.at_job_id} nadal istnieje; "
                "--already-removed jest fałszywe"
            )
    elif not args.already_removed:
        raise ValidationError(
            f"at-job #{args.at_job_id} już nie istnieje; "
            "użyj --already-removed do jawnej rekoncyliacji"
        )

    # DB-first interlock: RUN i CANCEL rywalizują pod tym samym BEGIN IMMEDIATE.
    # Po tym punkcie nawet awaria atrm nie może dopuścić child subprocess.
    cancel_claim = store.begin_at_job_cancellation(
        args.job_key,
        args.at_job_id,
        expected_gate_version=args.expected_gate_version,
        actor=args.actor,
        reason=args.reason,
    )
    _discard_cancelled_sealed_payload(job)
    if args.at_job_id in present:
        try:
            removed = _run_process([args.atrm_bin, args.at_job_id])
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GateError(
                "CANCEL claim zapisany fail-closed, ale atrm jest niedostępne: "
                f"{exc}"
            ) from exc
        if removed.returncode != 0:
            detail = (
                (removed.stderr or removed.stdout)
                .strip()
                .replace("\n", " ")[:300]
            )
            raise GateError(
                f"CANCEL claim zapisany fail-closed; atrm #{args.at_job_id} "
                f"rc={removed.returncode}: {detail}"
            )
        verify = _run_process([args.atq_bin])
        if verify.returncode != 0 or args.at_job_id in _parse_atq(verify.stdout):
            raise GateError(
                f"CANCEL claim zapisany fail-closed, ale brak postcondition: "
                f"at-job #{args.at_job_id} nadal jest w atq"
            )

    cancelled = store.cancel_at_job(
        args.job_key,
        args.at_job_id,
        cancel_claim_id=str(cancel_claim["claim_id"]),
        expected_gate_version=args.expected_gate_version,
        actor=args.actor,
        reason=args.reason,
    )
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
    outcome = GateStore(args.db).reconcile_at_jobs(present, note=unavailable_note)
    print(json.dumps(outcome, ensure_ascii=False, indent=2, sort_keys=True))
    return 2 if outcome["status"] == "UNAVAILABLE" else (1 if outcome["alarms"] else 0)


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
    add.add_argument("--atrm-bin", default="atrm", help=argparse.SUPPRESS)
    add.add_argument("--payload-dir", help=argparse.SUPPRESS)
    add.add_argument("command", nargs=argparse.REMAINDER)

    runner = subparsers.add_parser("run", help=argparse.SUPPRESS)
    runner.add_argument("--payload-file")
    runner.add_argument("--job-key")
    runner.add_argument("--token")
    runner.add_argument("--command-b64")

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
    cancel_parser.add_argument("--already-removed", action="store_true")
    cancel_parser.add_argument(
        "--atq-bin", default="atq", help=argparse.SUPPRESS
    )
    cancel_parser.add_argument(
        "--atrm-bin", default="atrm", help=argparse.SUPPRESS
    )

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
