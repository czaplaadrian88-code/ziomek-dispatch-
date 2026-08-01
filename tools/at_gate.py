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
import subprocess
import sys
import traceback
import uuid
from datetime import datetime
from pathlib import Path
from typing import Sequence

from process_debt_gate import (
    DEFAULT_DB,
    GateError,
    GateStore,
    ValidationError,
    canonical_json,
    iso_utc,
    parse_timestamp,
    utc_now,
)


_AT_JOB_RE = re.compile(r"\bjob\s+(\d+)\b", re.I)
_ATQ_RE = re.compile(r"^\s*(\d+)\s+")

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
# Log NIE zawiera runner-tokenu (leży jawnym tekstem w ciele at-joba; osobny dług).
AT_LOG_DIR = Path(os.environ.get("AT_GATE_LOG_DIR", "/root/handover/at_logs"))


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
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(command),
        input=stdin,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
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
    store.add_gate(
        gate_id=args.gate_id,
        title=args.title,
        kind="AT_JOB",
        owner=args.owner,
        due_at=args.due_at,
        next_step="Zaplanuj job wyłącznie przez at_gate.py",
        blocker=args.blocker,
        code_sha=args.code_sha,
        evidence_hash=args.evidence_hash,
        opened_at=args.opened_at,
        metadata={
            "scheduled_for": iso_utc(scheduled),
            "command_sha256": hashlib.sha256(canonical_json(command).encode("utf-8")).hexdigest(),
        },
        actor=args.actor,
        reason="utworzenie bramki przed wysłaniem do at",
    )
    store.register_at_intent(
        gate_id=args.gate_id,
        job_key=job_key,
        runner_token_hash=token_hash,
        scheduled_for=iso_utc(scheduled),
        command=command,
    )

    runner = [
        sys.executable,
        str(Path(__file__).resolve()),
        "--db",
        str(Path(args.db).resolve()),
        "run",
        "--job-key",
        job_key,
        "--token",
        token,
        "--command-b64",
        _encode_command(command),
    ]
    shell_line = " ".join(shlex.quote(part) for part in runner) + "\n"
    try:
        result = _run_process([args.at_bin, "-t", _at_time(scheduled)], stdin=shell_line)
    except (OSError, subprocess.TimeoutExpired) as exc:
        store.fail_at_submission(job_key, f"at niedostępne: {exc}")
        raise GateError(f"nie udało się uruchomić at: {exc}") from exc
    combined = "\n".join(part for part in (result.stdout, result.stderr) if part)
    match = _AT_JOB_RE.search(combined)
    if result.returncode != 0 or match is None:
        detail = combined.strip().replace("\n", " ")[:400] or "brak identyfikatora joba"
        store.fail_at_submission(
            job_key,
            f"at zwróciło kod {result.returncode}: {detail}",
        )
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
    log_path = _open_run_log(args.job_key)
    try:
        command = _decode_command(args.command_b64)
        _append_run_log(log_path, f"argv:    {shlex.join(command)}\n\n")
        return _run_registered_inner(args, command, log_path)
    except BaseException as exc:  # noqa: BLE001 — ślad MUSI zostać także przy SystemExit/KeyboardInterrupt
        _append_run_log(
            log_path,
            f"\n=== at_gate WYWROCIL SIE {iso_utc(utc_now())} ===\n"
            f"{type(exc).__name__}: {exc}\n{traceback.format_exc()}\n",
        )
        raise


def _run_registered_inner(
    args: argparse.Namespace, command: Sequence[str], log_path: Path | None
) -> int:
    try:
        result = subprocess.run(command, capture_output=True, check=False)
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
        GateStore(args.db).finish_at_job(
            args.job_key,
            runner_token=args.token,
            exit_code=exit_code,
            evidence_hash=evidence,
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
        try:
            removed = _run_process([args.atrm_bin, args.at_job_id])
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise GateError(f"atrm nie usunął at-joba: {exc}") from exc
        if removed.returncode != 0:
            detail = (
                (removed.stderr or removed.stdout)
                .strip()
                .replace("\n", " ")[:300]
            )
            raise GateError(
                f"atrm nie usunął at-joba #{args.at_job_id}: "
                f"rc={removed.returncode}: {detail}"
            )
        verify = _run_process([args.atq_bin])
        if verify.returncode != 0 or args.at_job_id in _parse_atq(verify.stdout):
            raise GateError(
                f"brak postcondition: at-job #{args.at_job_id} nadal jest w atq"
            )
    elif not args.already_removed:
        raise ValidationError(
            f"at-job #{args.at_job_id} już nie istnieje; "
            "użyj --already-removed do jawnej rekoncyliacji"
        )

    cancelled = store.cancel_at_job(
        args.job_key,
        args.at_job_id,
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
    add.add_argument("command", nargs=argparse.REMAINDER)

    runner = subparsers.add_parser("run", help=argparse.SUPPRESS)
    runner.add_argument("--job-key", required=True)
    runner.add_argument("--token", required=True)
    runner.add_argument("--command-b64", required=True)

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
