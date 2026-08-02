from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import os
import shlex
import sqlite3
import stat
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from process_debt_gate import (  # noqa: E402
    AT_CANCEL_CLAIM_STALE_SECONDS,
    AT_RUN_CLAIM_STALE_SECONDS,
    MAX_PRIVATE_FILE_BYTES,
    CASConflict,
    ClaimConflict,
    GateError,
    GateStore,
    IllegalTransition,
    ReceiptError,
    StorageError,
    ValidationError,
    canonical_argv_hash,
    export_payload,
    load_claim_receipt,
    read_private_bytes,
    render_open_gates,
    runner_auth_binding,
    runner_auth_tag,
)


CODE_SHA = "323034299fbba20a2fb33a45819e26c91f10a27a"
EVIDENCE = "20357879f33374b4ba3955ae77dd81f05bd686eaade2ce25d411a5373835630b"


def add_gate(store: GateStore, gate_id: str = "test.gate", *, opened_at: str = "2026-07-01T00:00:00Z"):
    return store.add_gate(
        gate_id=gate_id,
        title=f"Test {gate_id}",
        kind="TEST",
        owner="CTO",
        due_at="2026-07-30T00:00:00Z",
        next_step="Review",
        blocker="BRAK",
        code_sha=CODE_SHA,
        evidence_hash=EVIDENCE,
        opened_at=opened_at,
        now=datetime(2026, 7, 21, 12, tzinfo=timezone.utc),
    )


def register_legacy_fixture(
    store: GateStore,
    *,
    gate_id: str,
    job_key: str,
    token: str,
    scheduled_for: str,
    command: list[str],
    now: str = "2026-07-21T12:00:00Z",
) -> dict:
    """Test-only predecessor row; produkcyjny writer v1 został usunięty."""

    store.initialize()
    argv_sha = hashlib.sha256(
        json.dumps(
            command,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    command_json = json.dumps(
        {"argv_sha256": argv_sha, "argc": len(command)},
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            """
            INSERT INTO at_jobs (
                job_key, gate_id, status, scheduled_for, command_json,
                runner_token_hash, created_at, updated_at, auth_version
            ) VALUES (?, ?, 'SUBMITTING', ?, ?, ?, ?, ?, 1)
            """,
            (
                job_key,
                gate_id,
                scheduled_for,
                command_json,
                hashlib.sha256(token.encode("utf-8")).hexdigest(),
                now,
                now,
            ),
        )
        connection.commit()
    return store.show_at_job(job_key)


def register_sealed_fixture(
    store: GateStore,
    tmp_path: Path,
    *,
    suffix: str,
    queue_id: str,
    scheduled_for: str = "2026-07-22T10:00:00Z",
    command: list[str] | None = None,
    tag_token: str | None = None,
    tag_binding_overrides: dict | None = None,
) -> dict:
    """Sealed auth v2 job w stanie SCHEDULED, bez uruchamiania `at`.

    `tag_token` / `tag_binding_overrides` służą wyłącznie do podrobienia HMAC:
    tag liczony jest wtedy innym tokenem albo nad zmienionym bindingiem, przy
    rejestracji reszty pól bez zmian.
    """

    token = f"sealed-token-{suffix}"
    command = command or ["/bin/true", suffix]
    job_key = f"job-{suffix}"
    gate_id = f"at.sealed.{suffix}"
    payload = (tmp_path / f"payload-{suffix}.json").absolute()
    payload.write_bytes(f"sealed payload {suffix}".encode("utf-8"))
    payload.chmod(0o600)
    info = os.stat(payload, follow_symlinks=False)
    identity = {
        "sha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
        "device": info.st_dev,
        "inode": info.st_ino,
        "ctime_ns": info.st_ctime_ns,
        "size": info.st_size,
    }
    artifact_root = str((tmp_path / f"artifacts-{suffix}").absolute())
    binding = runner_auth_binding(
        job_key=job_key,
        gate_id=gate_id,
        scheduled_for=scheduled_for,
        command_sha256=canonical_argv_hash(command),
        payload_sha256=str(identity["sha256"]),
        artifact_root=artifact_root,
    )
    tagged_binding = dict(binding, **(tag_binding_overrides or {}))
    store.register_at_job(
        gate_id=gate_id,
        title=f"Sealed fixture {suffix}",
        owner="pytest",
        due_at="2026-07-30T00:00:00Z",
        blocker="Oczekiwanie na fixture",
        code_sha=CODE_SHA,
        evidence_hash=EVIDENCE,
        opened_at="2026-07-21T09:00:00Z",
        actor="pytest",
        job_key=job_key,
        runner_token_hash=hashlib.sha256(token.encode("utf-8")).hexdigest(),
        scheduled_for=scheduled_for,
        command=command,
        runner_auth_hmac=runner_auth_tag(tag_token or token, tagged_binding),
        payload_path=str(payload),
        payload_identity=identity,
        artifact_root=artifact_root,
        now=datetime(2026, 7, 21, 9, 0, tzinfo=timezone.utc),
    )
    store.confirm_at_job(
        job_key,
        queue_id,
        now=datetime(2026, 7, 21, 9, 1, tzinfo=timezone.utc),
    )
    return {
        "job_key": job_key,
        "gate_id": gate_id,
        "token": token,
        "command": command,
        "payload_path": str(payload),
        "identity": identity,
        "artifact_root": artifact_root,
        "binding": binding,
        "scheduled_for": scheduled_for,
    }


def sealed_claim_kwargs(record: dict) -> dict:
    """Dokładnie te argumenty `claim_at_job`, które MUSZĄ przejść."""

    return {
        "runner_token": str(record["token"]),
        "command": list(record["command"]),
        "payload_path": str(record["payload_path"]),
        "payload_identity": dict(record["identity"]),
        "artifact_root": str(record["artifact_root"]),
        "require_auth_version": 2,
    }


def assert_no_claim_and_job_unchanged(store: GateStore, job_key: str) -> None:
    """Fail-closed: odrzucony pre-exec nie zostawia claimu ani nie rusza joba."""

    with sqlite3.connect(store.db_path) as connection:
        claims = connection.execute(
            "SELECT COUNT(*) FROM at_job_claims WHERE job_key = ?", (job_key,)
        ).fetchone()[0]
    assert claims == 0
    job = store.show_at_job(job_key)
    assert job["status"] == "SCHEDULED"
    assert str(job["reconcile_note"] or "") == ""


def write_fixture_receipt(
    store: GateStore,
    job_key: str,
    claim: dict,
    *,
    exit_code: int,
) -> tuple[dict, dict]:
    path = Path(claim["receipt_path"])
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    private = path.parent
    while private != store.db_path.parent:
        private.chmod(0o700)
        private = private.parent
    stream_records = {}
    for name in ("stdout", "stderr"):
        stream_path = path.parent / f"{name}.bin"
        stream_data = b""
        stream_path.write_bytes(stream_data)
        stream_path.chmod(0o600)
        stream_info = stream_path.stat()
        stream_records[name] = {
            "path": str(stream_path),
            "sha256": hashlib.sha256(stream_data).hexdigest(),
            "device": stream_info.st_dev,
            "inode": stream_info.st_ino,
            "ctime_ns": stream_info.st_ctime_ns,
            "size": stream_info.st_size,
        }
    receipt = {
        "schema_version": 3,
        "job_key": job_key,
        "gate_id": claim["gate_id"],
        "claim_id": claim["claim_id"],
        "binding_sha256": claim["binding_sha256"],
        "command_sha256": claim["binding"]["command_sha256"],
        "exit_code": exit_code,
        "created_at": "2026-07-22T10:01:00Z",
        "execution": {
            "child_started": True,
            "direct_child_exit_observed": True,
            "stdio_eof_observed": True,
        },
        "stdout": stream_records["stdout"],
        "stderr": stream_records["stderr"],
    }
    payload = (
        json.dumps(
            receipt,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        + "\n"
    ).encode("utf-8")
    path.write_bytes(payload)
    path.chmod(0o600)
    info = path.stat()
    identity = {
        "sha256": hashlib.sha256(payload).hexdigest(),
        "device": info.st_dev,
        "inode": info.st_ino,
        "ctime_ns": info.st_ctime_ns,
        "size": info.st_size,
    }
    return identity, receipt


def finalize_fixture_receipt(
    store: GateStore,
    job_key: str,
    claim: dict,
    *,
    exit_code: int,
) -> tuple[dict, str]:
    identity, receipt = write_fixture_receipt(
        store,
        job_key,
        claim,
        exit_code=exit_code,
    )
    store.record_at_receipt(
        job_key,
        claim_id=claim["claim_id"],
        receipt_path=claim["receipt_path"],
        receipt_identity=identity,
        exit_code=exit_code,
        stdout_sha256=receipt["stdout"]["sha256"],
        stderr_sha256=receipt["stderr"]["sha256"],
    )
    return (
        store.finalize_at_claim(
            job_key,
            claim_id=claim["claim_id"],
            receipt_identity=identity,
        ),
        identity["sha256"],
    )


def test_full_transition_chain_is_atomic_and_audited(tmp_path: Path) -> None:
    store = GateStore(tmp_path / "gates.sqlite3")
    gate = add_gate(store)
    chain = [
        "WAIT_DATA",
        "READY_FOR_REVIEW",
        "READY_FOR_OWNER",
        "OWNER_ACKED",
        "APPLIED",
        "VERIFIED",
        "CLOSED",
    ]
    for version, state in enumerate(chain, start=1):
        gate = store.transition(
            "test.gate",
            state,
            expected_version=version,
            actor="pytest",
            reason=f"oracle {state}",
            now=datetime(2026, 7, 21, 12, version, tzinfo=timezone.utc),
        )
    assert gate["state"] == "CLOSED"
    assert gate["version"] == 8
    assert gate["closed_at"] == "2026-07-21T12:07:00Z"
    assert len(gate["events"]) == 8


def test_cas_rejects_stale_writer_without_partial_change(tmp_path: Path) -> None:
    store = GateStore(tmp_path / "gates.sqlite3")
    add_gate(store)
    store.transition(
        "test.gate",
        "WAIT_DATA",
        expected_version=1,
        actor="writer-a",
        reason="pierwszy zapis",
    )
    with pytest.raises(CASConflict):
        store.transition(
            "test.gate",
            "READY_FOR_REVIEW",
            expected_version=1,
            actor="writer-b",
            reason="stary odczyt",
            owner="NIE-MOŻE-WEJŚĆ",
        )
    gate = store.show_gate("test.gate")
    assert gate["state"] == "WAIT_DATA"
    assert gate["owner"] == "CTO"
    assert gate["version"] == 2


def test_note_records_decision_without_state_change_and_refreshes_views(
    tmp_path: Path,
) -> None:
    store = GateStore(tmp_path / "gates.sqlite3")
    add_gate(store)
    store.transition(
        "test.gate",
        "WAIT_DATA",
        expected_version=1,
        actor="pytest/transition",
        reason="oczekiwanie na decyzję",
        now=datetime(2026, 7, 21, 12, 1, tzinfo=timezone.utc),
    )

    gate = store.note(
        "test.gate",
        expected_version=2,
        actor="OWNER",
        reason="decyzja 2 zatwierdzona",
        next_step="Wykonać decyzję przy najbliższym przejściu",
        blocker="BRAK — owner zdecydował",
        evidence_hash="a" * 64,
        code_sha="b" * 40,
        now=datetime(2026, 7, 22, 9, 30, tzinfo=timezone.utc),
    )

    assert gate["state"] == "WAIT_DATA"
    assert gate["version"] == 3
    assert gate["next_step"] == "Wykonać decyzję przy najbliższym przejściu"
    assert gate["blocker"] == "BRAK — owner zdecydował"
    assert gate["evidence_hash"] == "a" * 64
    assert gate["code_sha"] == "b" * 40
    assert gate["events"][-1]["from_state"] == "WAIT_DATA"
    assert gate["events"][-1]["to_state"] == "WAIT_DATA"
    assert gate["freshness"]["has_fresh_note"] is True
    assert gate["freshness"]["latest_note_at"] == "2026-07-22T09:30:00Z"
    assert gate["freshness"]["latest_transition_at"] == "2026-07-21T12:01:00Z"
    assert gate["freshness"]["latest_note_actor"] == "OWNER"
    assert gate["freshness"]["latest_note_reason"] == "decyzja 2 zatwierdzona"

    listed = store.list_gates()
    assert listed[0]["freshness"] == gate["freshness"]
    view = render_open_gates(
        listed,
        as_of=datetime(2026, 7, 24, 12, tzinfo=timezone.utc),
        source="fixture.sqlite3",
    )
    assert "| ŚWIEŻA 2026-07-22 OWNER |" in view
    transitioned = store.transition(
        "test.gate",
        "READY_FOR_REVIEW",
        expected_version=3,
        actor="pytest/transition",
        reason="konsumpcja decyzji",
        now=datetime(2026, 7, 22, 10, 0, tzinfo=timezone.utc),
    )
    assert transitioned["freshness"]["has_fresh_note"] is False
    assert transitioned["freshness"]["latest_note_at"] == "2026-07-22T09:30:00Z"
    assert transitioned["freshness"]["latest_transition_at"] == "2026-07-22T10:00:00Z"


def test_note_cas_rejects_stale_writer_without_partial_change(tmp_path: Path) -> None:
    store = GateStore(tmp_path / "gates.sqlite3")
    add_gate(store)
    store.note(
        "test.gate",
        expected_version=1,
        actor="writer-a",
        reason="pierwsza notatka",
        next_step="Nowy krok",
    )
    with pytest.raises(CASConflict):
        store.note(
            "test.gate",
            expected_version=1,
            actor="writer-b",
            reason="stary odczyt",
            blocker="NIE-MOŻE-WEJŚĆ",
        )
    gate = store.show_gate("test.gate")
    assert gate["state"] == "BUILT_OFF"
    assert gate["version"] == 2
    assert gate["next_step"] == "Nowy krok"
    assert gate["blocker"] == "BRAK"
    assert len(gate["events"]) == 2


def test_note_cli_requires_audited_fields_and_uses_explicit_database(
    tmp_path: Path,
) -> None:
    database = tmp_path / "gates.sqlite3"
    store = GateStore(database)
    add_gate(store)
    tool = TOOLS / "process_debt_gate.py"
    result = subprocess.run(
        [
            sys.executable,
            str(tool),
            "--db",
            str(database),
            "note",
            "test.gate",
            "--expected-version",
            "1",
            "--actor",
            "OWNER",
            "--reason",
            "decyzja bez zmiany stanu",
            "--next-step",
            "Review dowodu",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["state"] == "BUILT_OFF"
    assert payload["version"] == 2
    assert payload["freshness"]["has_fresh_note"] is True


def test_two_concurrent_cas_writers_have_exactly_one_winner(tmp_path: Path) -> None:
    database = tmp_path / "gates.sqlite3"
    add_gate(GateStore(database))
    barrier = threading.Barrier(2)

    def write(actor: str) -> str:
        barrier.wait()
        try:
            GateStore(database).transition(
                "test.gate",
                "WAIT_DATA",
                expected_version=1,
                actor=actor,
                reason="równoległy CAS",
            )
            return "WIN"
        except CASConflict:
            return "CAS"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(write, ("writer-a", "writer-b")))
    assert sorted(results) == ["CAS", "WIN"]
    gate = GateStore(database).show_gate("test.gate")
    assert gate["state"] == "WAIT_DATA"
    assert gate["version"] == 2
    assert len(gate["events"]) == 2


def test_illegal_transition_is_rejected(tmp_path: Path) -> None:
    store = GateStore(tmp_path / "gates.sqlite3")
    add_gate(store)
    with pytest.raises(IllegalTransition):
        store.transition(
            "test.gate",
            "READY_FOR_OWNER",
            expected_version=1,
            actor="pytest",
            reason="próba przeskoku",
        )
    gate = store.show_gate("test.gate")
    assert gate["state"] == "BUILT_OFF"
    assert gate["version"] == 1
    assert len(gate["events"]) == 1


@pytest.mark.parametrize("terminal", ["REJECTED", "SUPERSEDED"])
def test_alternative_terminal_states_cannot_be_reopened(tmp_path: Path, terminal: str) -> None:
    store = GateStore(tmp_path / f"{terminal}.sqlite3")
    add_gate(store)
    store.transition(
        "test.gate",
        terminal,
        expected_version=1,
        actor="pytest",
        reason="jawny werdykt",
    )
    with pytest.raises(IllegalTransition):
        store.transition(
            "test.gate",
            "WAIT_DATA",
            expected_version=2,
            actor="pytest",
            reason="próba reopen",
        )


def test_reconcile_missing_at_job_sets_alarm_visible_in_view(tmp_path: Path) -> None:
    store = GateStore(tmp_path / "gates.sqlite3")
    add_gate(store, "at.test")
    token = "runner-secret-for-test"
    register_legacy_fixture(
        store,
        gate_id="at.test",
        job_key="job-key-1",
        token=token,
        scheduled_for="2026-07-22T10:00:00Z",
        command=["/bin/true"],
    )
    registered = store.show_at_job("job-key-1")
    assert registered["command"]["argc"] == 1
    assert registered["command"]["argv_sha256"]
    assert "/bin/true" not in json.dumps(registered)
    store.confirm_at_job(
        "job-key-1",
        "123",
        now=datetime(2026, 7, 21, 12, 1, tzinfo=timezone.utc),
    )
    outcome = store.reconcile_at_jobs(
        set(), now=datetime(2026, 7, 21, 12, 2, tzinfo=timezone.utc)
    )
    assert outcome["status"] == "OK"
    assert outcome["alarms"][0]["at_job_id"] == "123"
    gate = store.show_gate("at.test")
    assert gate["alarm"] is True
    assert "zniknął" in gate["alarm_reason"]
    view = render_open_gates(
        store.list_gates(),
        as_of=datetime(2026, 7, 21, 12, 3, tzinfo=timezone.utc),
        source="fixture.sqlite3",
    )
    assert "| ALARM |" in view

    with pytest.raises(ValidationError):
        store.begin_at_job_cancellation(
            "job-key-1",
            "999",
            expected_gate_version=3,
            actor="pytest",
            reason="wrong scheduler receipt must fail",
        )
    with pytest.raises(CASConflict):
        store.begin_at_job_cancellation(
            "job-key-1",
            "123",
            expected_gate_version=2,
            actor="pytest",
            reason="stale cancellation must fail",
        )
    gate = store.show_gate("at.test")
    claim = store.begin_at_job_cancellation(
        "job-key-1",
        "123",
        expected_gate_version=gate["version"],
        actor="pytest",
        reason="exact successor is ready",
    )
    with pytest.raises(ClaimConflict):
        store.claim_at_job(
            "job-key-1", runner_token=token, command=["/bin/true"]
        )
    cancelled = store.cancel_at_job(
        "job-key-1",
        "123",
        cancel_claim_id=claim["claim_id"],
        expected_gate_version=gate["version"],
        actor="pytest",
        reason="exact successor is ready",
        now=datetime(2026, 7, 21, 12, 4, tzinfo=timezone.utc),
    )
    assert cancelled["status"] == "CANCELLED"
    closed = store.show_gate("at.test")
    assert closed["state"] == "SUPERSEDED"
    assert closed["alarm"] is False
    assert closed["events"][-1]["actor"] == "pytest"
    assert store.reconcile_at_jobs(set())["alarms"] == []


def test_successful_at_result_advances_to_review_and_records_hash(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = GateStore(tmp_path / "gates.sqlite3")
    add_gate(store, "at.success")
    token = "one-time-runner-token"
    register_legacy_fixture(
        store,
        gate_id="at.success",
        job_key="job-key-success",
        token=token,
        scheduled_for="2026-07-22T10:00:00Z",
        command=["/bin/true", "sensitive-argument"],
    )
    store.confirm_at_job("job-key-success", "456")
    command = ["/bin/true", "sensitive-argument"]
    with pytest.raises(IllegalTransition, match="przed scheduled_for"):
        store.claim_at_job(
            "job-key-success",
            runner_token=token,
            command=command,
            now=datetime(2026, 7, 22, 9, 59, tzinfo=timezone.utc),
        )
    with pytest.raises((ClaimConflict, GateError)):
        store.finish_at_job(
            "job-key-success",
            claim_id="claim-does-not-exist",
            runner_token=token,
            exit_code=0,
            evidence_hash="f" * 64,
            command=command,
        )
    with pytest.raises(ValidationError, match="command identity mismatch"):
        store.claim_at_job(
            "job-key-success",
            runner_token=token,
            command=["/bin/false"],
        )
    launch_gap = store.reconcile_at_jobs(
        set(),
        now=datetime(2026, 7, 22, 10, 0, 1, tzinfo=timezone.utc),
    )
    assert launch_gap["alarms"] == []
    assert launch_gap["launching"] == ["job-key-success"]
    claim = store.claim_at_job(
        "job-key-success",
        runner_token=token,
        command=command,
        now=datetime(2026, 7, 22, 10, 0, 2, tzinfo=timezone.utc),
    )
    legacy_attestation = store.verify_active_run_claim(
        "job-key-success",
        claim_id=claim["claim_id"],
        command=command,
    )
    assert legacy_attestation["auth_version"] == 1
    gate_before_finish = store.show_gate("at.success")
    result, receipt_sha = finalize_fixture_receipt(
        store,
        "job-key-success",
        claim,
        exit_code=0,
    )
    assert result["status"] == "SUCCEEDED"
    assert result["result_evidence_hash"] == receipt_sha
    gate = store.show_gate("at.success")
    assert gate["state"] == "WAIT_DATA"
    assert gate["evidence_hash"] == EVIDENCE
    assert gate["alarm"] is False
    for field in ("state", "version", "evidence_hash", "alarm", "blocker", "next_step"):
        assert gate[field] == gate_before_finish[field]
    assert gate["events"][-1]["from_state"] == gate["events"][-1]["to_state"]

    # Mutation ratchet: output jest otwarty przed claimem, child dostaje PIPE,
    # a logical CANCEL nie ma żadnego zewnętrznego delete-writera.
    spec = importlib.util.spec_from_file_location("at_gate_claim_test", TOOLS / "at_gate.py")
    assert spec and spec.loader
    at_gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(at_gate)
    runner_source = inspect.getsource(at_gate._run_registered_inner)
    capture_source = inspect.getsource(at_gate._execute_with_owned_streams)
    cancel_source = inspect.getsource(at_gate.cancel)
    assert runner_source.index("_open_private_output(") < runner_source.index("claim_at_job(")
    assert runner_source.index("claim_at_job(") < runner_source.index("_execute_with_owned_streams(")
    assert runner_source.index("verify_active_run_claim(") < runner_source.index(
        "_execute_with_owned_streams("
    )
    assert runner_source.index("_execute_with_owned_streams(") < runner_source.index("_write_result_receipt(")
    assert "stdout=subprocess.PIPE" in capture_source
    assert "stderr=subprocess.PIPE" in capture_source
    assert "_run_process(" not in cancel_source
    assert "atrm" not in cancel_source

    # Nowe schedule zawsze zapisuje sealed auth v2, a spool nie zawiera tokenu
    # ani argv. Proces `at` dostaje wyłącznie minimalne, jawne środowisko.
    scheduled_db = tmp_path / "sealed.sqlite3"
    captured_schedule: dict[str, object] = {}

    def fake_at(command_line, *, stdin=None, timeout=30.0, env=None):
        captured_schedule.update(
            {"command": list(command_line), "stdin": stdin, "env": dict(env or {})}
        )
        return subprocess.CompletedProcess(command_line, 0, "job 999 at fixture\n", "")

    monkeypatch.setattr(at_gate, "_run_process", fake_at)
    schedule_args = at_gate.build_parser().parse_args(
        [
            "--db",
            str(scheduled_db),
            "schedule",
            "--id",
            "at.sealed",
            "--title",
            "sealed fixture",
            "--owner",
            "pytest",
            "--due",
            "2099-01-03T00:00:00Z",
            "--when",
            "2099-01-02T10:00:00Z",
            "--code-sha",
            CODE_SHA,
            "--evidence-hash",
            EVIDENCE,
            "--payload-dir",
            str(tmp_path / "payloads"),
            "--",
            "/bin/true",
            "sealed-sensitive-argument",
        ]
    )
    assert at_gate.schedule(schedule_args) == 0
    sealed_store = GateStore(scheduled_db)
    sealed_job = sealed_store.list_at_jobs(active_only=True)[0]
    payloads_before_duplicate = set((tmp_path / "payloads").iterdir())
    with pytest.raises(GateError):
        at_gate.schedule(schedule_args)
    assert set((tmp_path / "payloads").iterdir()) == payloads_before_duplicate
    assert len(sealed_store.list_at_jobs(active_only=True)) == 1
    spool = str(captured_schedule["stdin"])
    assert "--payload-file" in spool
    assert "--token" not in spool and "--command-b64" not in spool
    assert "sealed-sensitive-argument" not in spool
    assert sealed_job["auth_version"] == 2
    assert sealed_job["command_sha256"] == sealed_job["command"]["argv_sha256"]
    payload_file = Path(str(sealed_job["payload_path"]))
    assert stat.S_IMODE(payload_file.stat().st_mode) == 0o600
    payload, identity = at_gate._load_sealed_payload(payload_file)
    with pytest.raises(ValidationError, match="payload identity"):
        sealed_store.claim_at_job(
            sealed_job["job_key"],
            runner_token=payload["runner_token"],
            command=payload["command"],
            payload_path=str(payload_file),
            payload_identity=dict(identity, inode=int(identity["inode"]) + 1),
            require_auth_version=2,
        )
    assert set(captured_schedule["env"]) == {
        "PATH",
        "LANG",
        "LC_ALL",
        "TZ",
        "SHELL",
    }

    # Negatywny oracle na realnym callerze: zmienione argv nie dochodzi do child.
    isolated = GateStore(tmp_path / "caller.sqlite3")
    add_gate(isolated, "at.caller")
    register_legacy_fixture(
        isolated,
        gate_id="at.caller",
        job_key="job-caller",
        token=token,
        scheduled_for="2026-07-22T10:00:00Z",
        command=command,
    )
    isolated.confirm_at_job("job-caller", "457")
    calls: list[list[str]] = []

    def capture_spy(command, *, child_env, stdout_handle, stderr_handle):
        del child_env, stdout_handle, stderr_handle
        calls.append(list(command))
        return at_gate.ExecutionCapture(0, True, True, True)

    monkeypatch.setattr(at_gate, "_execute_with_owned_streams", capture_spy)
    monkeypatch.setattr(
        sys.modules["process_debt_gate"],
        "utc_now",
        lambda: datetime(2099, 1, 2, 10, 1, tzinfo=timezone.utc),
    )
    assert (
        at_gate._run_registered_inner(
            SimpleNamespace(
                db=str(scheduled_db),
                job_key=sealed_job["job_key"],
                token=payload["runner_token"],
            ),
            payload["command"],
            None,
            payload_path=payload_file,
            payload_identity=identity,
            require_auth_version=2,
        )
        == 0
    )
    assert not payload_file.exists()
    assert calls == [payload["command"]]
    calls.clear()

    with pytest.raises(ValidationError, match="command identity mismatch"):
        at_gate._run_registered_inner(
            SimpleNamespace(db=str(isolated.db_path), job_key="job-caller", token=token),
            ["/bin/false"],
            None,
        )
    assert calls == []

    # Wspólny BEGIN IMMEDIATE daje dokładnie jednego zwycięzcę RUN↔CANCEL.
    race = GateStore(tmp_path / "race.sqlite3")
    add_gate(race, "at.race")
    register_legacy_fixture(
        race,
        gate_id="at.race",
        job_key="job-race",
        token=token,
        scheduled_for="2026-07-22T10:00:00Z",
        command=["/bin/true"],
    )
    race.confirm_at_job("job-race", "458")
    race_gate_version = race.show_gate("at.race")["version"]
    barrier = threading.Barrier(2)

    def win_run_or_cancel(operation: str) -> str:
        barrier.wait()
        try:
            if operation == "RUN":
                GateStore(race.db_path).claim_at_job(
                    "job-race", runner_token=token, command=["/bin/true"]
                )
            else:
                GateStore(race.db_path).begin_at_job_cancellation(
                    "job-race",
                    "458",
                    expected_gate_version=race_gate_version,
                    actor="pytest",
                    reason="race oracle",
                )
            return operation
        except ClaimConflict:
            return "CONFLICT"

    with ThreadPoolExecutor(max_workers=2) as pool:
        race_results = sorted(pool.map(win_run_or_cancel, ("RUN", "CANCEL")))
    assert "CONFLICT" in race_results
    assert len(set(race_results) - {"CONFLICT"}) == 1

    # Terminal transition i RUN claim współdzielą ten sam lock. Aktywny job
    # oznacza, że terminalny writer zawsze przegrywa przed zmianą gate'a.
    terminal_race = GateStore(tmp_path / "terminal-race.sqlite3")
    add_gate(terminal_race, "at.terminal-race")
    register_legacy_fixture(
        terminal_race,
        gate_id="at.terminal-race",
        job_key="job-terminal-race",
        token=token,
        scheduled_for="2026-07-22T10:00:00Z",
        command=["/bin/true"],
    )
    terminal_race.confirm_at_job("job-terminal-race", "4580")
    terminal_barrier = threading.Barrier(2)

    def win_run_or_terminal(operation: str) -> str:
        terminal_barrier.wait()
        try:
            if operation == "RUN":
                GateStore(terminal_race.db_path).claim_at_job(
                    "job-terminal-race",
                    runner_token=token,
                    command=["/bin/true"],
                )
            else:
                GateStore(terminal_race.db_path).transition(
                    "at.terminal-race",
                    "SUPERSEDED",
                    expected_version=2,
                    actor="pytest",
                    reason="terminal vs RUN oracle",
                )
            return operation
        except IllegalTransition:
            return "HOLD"

    with ThreadPoolExecutor(max_workers=2) as pool:
        terminal_results = sorted(
            pool.map(win_run_or_terminal, ("RUN", "TERMINAL"))
        )
    assert terminal_results == ["HOLD", "RUN"]
    assert terminal_race.show_gate("at.terminal-race")["state"] == "WAIT_DATA"
    assert terminal_race.show_at_claim("job-terminal-race")["status"] == "CLAIMED"

    # Dwa realne runnery: dokładnie jeden dochodzi do child subprocess.
    calls.clear()
    twin = GateStore(tmp_path / "twin.sqlite3")
    add_gate(twin, "at.twin")
    register_legacy_fixture(
        twin,
        gate_id="at.twin",
        job_key="job-twin",
        token=token,
        scheduled_for="2026-07-22T10:00:00Z",
        command=["/bin/true"],
    )
    twin.confirm_at_job("job-twin", "459")
    twin_barrier = threading.Barrier(2)

    def execute_twin() -> str:
        twin_barrier.wait()
        try:
            result = at_gate._run_registered_inner(
                SimpleNamespace(
                    db=str(twin.db_path),
                    job_key="job-twin",
                    token=token,
                ),
                ["/bin/true"],
                None,
            )
            return f"RC={result}"
        except (ClaimConflict, ReceiptError):
            return "RECOVERY_HOLD"

    with ThreadPoolExecutor(max_workers=2) as pool:
        twin_results = list(pool.map(lambda _: execute_twin(), range(2)))
    assert twin_results.count("RC=0") >= 1
    assert set(twin_results) <= {"RC=0", "RECOVERY_HOLD"}
    assert calls == [["/bin/true"]]
    calls.clear()

    # Terminalna lub zaalarmowana bramka nigdy nie dopuszcza child.
    terminal = GateStore(tmp_path / "terminal.sqlite3")
    add_gate(terminal, "at.terminal")
    register_legacy_fixture(
        terminal,
        gate_id="at.terminal",
        job_key="job-terminal",
        token=token,
        scheduled_for="2026-07-22T10:00:00Z",
        command=["/bin/true"],
    )
    terminal.confirm_at_job("job-terminal", "460")
    before_terminal = terminal.show_gate("at.terminal")
    with pytest.raises(IllegalTransition, match="at_gate cancel/finalize"):
        terminal.transition(
            "at.terminal",
            "SUPERSEDED",
            expected_version=2,
            actor="pytest",
            reason="terminal oracle",
        )
    assert terminal.show_gate("at.terminal") == before_terminal
    # Predecessor/corrupt fixture: reconcile musi zachować terminalną bramkę
    # bit-for-bit i głośno pokazać scheduler orphan.
    with sqlite3.connect(terminal.db_path) as connection:
        connection.execute(
            "UPDATE gates SET state='SUPERSEDED', version=3, "
            "closed_at='2026-07-22T10:00:00Z' WHERE gate_id='at.terminal'"
        )
        connection.commit()
    terminal_snapshot = terminal.show_gate("at.terminal")
    terminal_reconcile = terminal.reconcile_at_jobs(
        set(), now=datetime(2026, 7, 22, 10, 3, tzinfo=timezone.utc)
    )
    assert terminal_reconcile["terminal_orphans"][0]["job_key"] == "job-terminal"
    assert terminal.show_gate("at.terminal")["version"] == terminal_snapshot["version"]
    for field in ("state", "alarm", "alarm_reason", "blocker", "next_step", "closed_at"):
        assert terminal.show_gate("at.terminal")[field] == terminal_snapshot[field]
    after_terminal_reconcile = terminal.show_gate("at.terminal")
    second_terminal_reconcile = terminal.reconcile_at_jobs(
        set(), now=datetime(2026, 7, 22, 10, 4, tzinfo=timezone.utc)
    )
    assert second_terminal_reconcile["terminal_orphans"] == terminal_reconcile[
        "terminal_orphans"
    ]
    assert terminal.show_gate("at.terminal") == after_terminal_reconcile
    with pytest.raises((IllegalTransition, GateError)):
        at_gate._run_registered_inner(
            SimpleNamespace(
                db=str(terminal.db_path),
                job_key="job-terminal",
                token=token,
            ),
            ["/bin/true"],
            None,
        )
    alarmed = GateStore(tmp_path / "alarmed.sqlite3")
    add_gate(alarmed, "at.alarmed")
    register_legacy_fixture(
        alarmed,
        gate_id="at.alarmed",
        job_key="job-alarmed",
        token=token,
        scheduled_for="2026-07-22T10:00:00Z",
        command=["/bin/true"],
    )
    alarmed.confirm_at_job("job-alarmed", "461")
    with sqlite3.connect(alarmed.db_path) as connection:
        connection.execute(
            "UPDATE gates SET alarm=1, alarm_reason='independent alarm' "
            "WHERE gate_id='at.alarmed'"
        )
        connection.commit()
    with pytest.raises(IllegalTransition):
        at_gate._run_registered_inner(
            SimpleNamespace(
                db=str(alarmed.db_path),
                job_key="job-alarmed",
                token=token,
            ),
            ["/bin/true"],
            None,
        )
    assert calls == []

    # Process failure po zmianie bramki zawsze latchuje ALARM, lecz nie przejmuje
    # nowszych pól operatorskich. ALARM blokuje dalszą promocję/CLOSED.
    moved = GateStore(tmp_path / "moved.sqlite3")
    add_gate(moved, "at.moved")
    register_legacy_fixture(
        moved,
        gate_id="at.moved",
        job_key="job-moved",
        token=token,
        scheduled_for="2026-07-22T10:00:00Z",
        command=["/bin/false"],
    )
    moved.confirm_at_job("job-moved", "462")
    moved_claim = moved.claim_at_job(
        "job-moved", runner_token=token, command=["/bin/false"]
    )
    with pytest.raises(IllegalTransition, match="zamrożona przez aktywny RUN"):
        moved.transition(
            "at.moved",
            "READY_FOR_REVIEW",
            expected_version=2,
            actor="pytest",
            reason="canonical writer must freeze",
            next_step="owner-specific next",
            blocker="owner-specific blocker",
        )
    with pytest.raises(IllegalTransition, match="zamrożona przez aktywny RUN"):
        moved.note(
            "at.moved",
            expected_version=2,
            actor="pytest",
            reason="canonical note must freeze",
            next_step="owner-specific next",
        )
    # Symulacja starego/obcego writera omijającego jedyny interfejs. Finalizer
    # nadal musi latchować ALARM bez przejęcia nowszych pól operatorskich.
    with sqlite3.connect(moved.db_path) as connection:
        connection.execute(
            "UPDATE gates SET state='READY_FOR_REVIEW', version=3, "
            "next_step='owner-specific next', blocker='owner-specific blocker' "
            "WHERE gate_id='at.moved'"
        )
        connection.commit()
    before_failure = moved.show_gate("at.moved")
    finalize_fixture_receipt(
        moved,
        "job-moved",
        moved_claim,
        exit_code=7,
    )
    after_failure = moved.show_gate("at.moved")
    for field in ("state", "evidence_hash", "blocker", "next_step"):
        assert after_failure[field] == before_failure[field]
    assert after_failure["version"] == before_failure["version"] + 1
    assert after_failure["alarm"] is True
    assert "kodem 7" in after_failure["alarm_reason"]
    with pytest.raises(IllegalTransition, match="ALARM"):
        moved.transition(
            "at.moved",
            "READY_FOR_OWNER",
            expected_version=after_failure["version"],
            actor="pytest",
            reason="mutation: alarm nie może być pominięty",
        )

    # CLI cancel: jedynym writerem jest logiczny tombstone. Ani pierwszy call,
    # ani retry nie czyta atq i nie wykonuje destrukcji po numerycznym ID.
    cancel_store = GateStore(tmp_path / "cancel-cli.sqlite3")
    add_gate(cancel_store, "at.cancel-cli")
    register_legacy_fixture(
        cancel_store,
        gate_id="at.cancel-cli",
        job_key="job-cancel-cli",
        token=token,
        scheduled_for="2026-07-22T10:00:00Z",
        command=["/bin/true"],
    )
    cancel_store.confirm_at_job("job-cancel-cli", "463")
    cancel_args = at_gate.build_parser().parse_args(
        [
            "--db",
            str(cancel_store.db_path),
            "cancel",
            "--job-key",
            "job-cancel-cli",
            "--at-job-id",
            "463",
            "--expected-gate-version",
            "2",
            "--actor",
            "pytest",
            "--reason",
            "cancel retry oracle",
        ]
    )

    external_cancel_calls: list[list[str]] = []

    def forbidden_cancel_process(command_line, **_kwargs):
        external_cancel_calls.append(list(command_line))
        raise AssertionError("logiczny CANCEL nie może wołać atq/atrm")

    monkeypatch.setattr(at_gate, "_run_process", forbidden_cancel_process)
    assert at_gate.cancel(cancel_args) == 0
    cancel_claim = cancel_store.show_at_claim("job-cancel-cli")
    assert cancel_claim["binding"]["operation"] == "CANCEL"
    assert cancel_claim["status"] == "FINALIZED"
    assert cancel_store.show_at_job("job-cancel-cli")["status"] == "CANCELLED"
    recovered_cancel_gate = cancel_store.show_gate("at.cancel-cli")
    assert recovered_cancel_gate["state"] == "SUPERSEDED"
    assert recovered_cancel_gate["alarm"] is False
    assert at_gate.cancel(cancel_args) == 0
    assert external_cancel_calls == []
    # Auth2 CANCEL zachowuje payload aż naturalnie dequeued wrapper zweryfikuje
    # sealed auth, rozpozna tombstone i wykona exact-GC bez jednego Popen.
    auth2_db = tmp_path / "cancel-auth2.sqlite3"

    def schedule_auth2(command_line, **_kwargs):
        return subprocess.CompletedProcess(command_line, 0, "job 467 fixture\n", "")

    monkeypatch.setattr(at_gate, "_run_process", schedule_auth2)
    auth2_schedule = at_gate.build_parser().parse_args(
        [
            "--db",
            str(auth2_db),
            "schedule",
            "--id",
            "at.cancel-auth2",
            "--title",
            "auth2 cancel fixture",
            "--owner",
            "pytest",
            "--due",
            "2099-01-03T00:00:00Z",
            "--when",
            "2099-01-02T10:00:00Z",
            "--code-sha",
            CODE_SHA,
            "--evidence-hash",
            EVIDENCE,
            "--payload-dir",
            str(tmp_path / "cancel-auth2-payloads"),
            "--",
            "/bin/true",
        ]
    )
    assert at_gate.schedule(auth2_schedule) == 0
    auth2_store = GateStore(auth2_db)
    auth2_job = auth2_store.list_at_jobs(active_only=True)[0]
    auth2_payload = Path(auth2_job["payload_path"])
    assert auth2_payload.exists()
    auth2_cancel = at_gate.build_parser().parse_args(
        [
            "--db",
            str(auth2_db),
            "cancel",
            "--job-key",
            auth2_job["job_key"],
            "--at-job-id",
            "467",
            "--expected-gate-version",
            "2",
            "--actor",
            "pytest",
            "--reason",
            "auth2 cleanup oracle",
        ]
    )
    monkeypatch.setattr(at_gate, "_run_process", forbidden_cancel_process)
    assert at_gate.cancel(auth2_cancel) == 0
    assert auth2_payload.exists()
    assert auth2_store.show_at_job(auth2_job["job_key"])["status"] == "CANCELLED"
    finalized_cancel_claim = auth2_store.show_at_claim(auth2_job["job_key"])
    assert finalized_cancel_claim["status"] == "FINALIZED"
    assert finalized_cancel_claim["binding"]["claim_id"] == finalized_cancel_claim[
        "claim_id"
    ]
    assert finalized_cancel_claim["binding"]["reason"] == "auth2 cleanup oracle"
    with sqlite3.connect(auth2_db) as connection:
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                "UPDATE at_job_claims SET claim_id='cancel-mutated' WHERE job_key=?",
                (auth2_job["job_key"],),
            )
        connection.rollback()

    auth2_payload_data, auth2_identity = at_gate._load_sealed_payload(auth2_payload)
    monkeypatch.setattr(
        at_gate.subprocess,
        "Popen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            AssertionError("CANCEL tombstone uruchomił subprocess")
        ),
    )
    assert at_gate._run_registered_inner(
        SimpleNamespace(
            db=str(auth2_db),
            job_key=auth2_job["job_key"],
            token=auth2_payload_data["runner_token"],
            artifact_root=auth2_payload_data["artifact_root"],
        ),
        auth2_payload_data["command"],
        None,
        payload_path=auth2_payload,
        payload_identity=auth2_identity,
        require_auth_version=2,
    ) == 0
    assert not auth2_payload.exists()
    assert external_cancel_calls == []


def test_preexec_attestation_blocks_child_and_preserves_owner_fields_after_drift(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = importlib.util.spec_from_file_location("at_gate_preexec_test", TOOLS / "at_gate.py")
    assert spec and spec.loader
    at_gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(at_gate)

    store = GateStore(tmp_path / "preexec-drift.sqlite3")
    add_gate(store, "at.preexec-drift")
    register_legacy_fixture(
        store,
        gate_id="at.preexec-drift",
        job_key="job-preexec-drift",
        token="preexec-token",
        scheduled_for="2026-07-22T10:00:00Z",
        command=["/bin/true"],
    )
    store.confirm_at_job("job-preexec-drift", "799")

    original_claim = at_gate.GateStore.claim_at_job

    def claim_then_inject_legacy_writer(self, *args, **kwargs):
        claim = original_claim(self, *args, **kwargs)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute(
                "UPDATE gates SET code_sha=?, version=version+1, blocker=?, "
                "next_step=? WHERE gate_id=?",
                (
                    "9" * 40,
                    "OWNER BLOCKER",
                    "OWNER NEXT",
                    "at.preexec-drift",
                ),
            )
            connection.commit()
        return claim

    child_calls: list[list[str]] = []
    monkeypatch.setattr(at_gate.GateStore, "claim_at_job", claim_then_inject_legacy_writer)
    monkeypatch.setattr(
        at_gate,
        "_execute_with_owned_streams",
        lambda command, **_kwargs: child_calls.append(list(command)),
    )

    rc = at_gate._run_registered_inner(
        SimpleNamespace(
            db=str(store.db_path),
            job_key="job-preexec-drift",
            token="preexec-token",
        ),
        ["/bin/true"],
        None,
    )
    assert rc == 125
    assert child_calls == []
    claim = store.show_at_claim("job-preexec-drift")
    assert claim["status"] == "OUTCOME_UNKNOWN"
    assert claim["receipt_sha256"] is None
    gate = store.show_gate("at.preexec-drift")
    assert gate["alarm"] is True
    assert gate["blocker"] == "OWNER BLOCKER"
    assert gate["next_step"] == "OWNER NEXT"
    assert "pre-exec attestation failed before child start" in gate["alarm_reason"]


def test_stale_at_intent_becomes_alarm(tmp_path: Path) -> None:
    store = GateStore(tmp_path / "gates.sqlite3")
    add_gate(store, "at.stale")
    token = "stale-token"
    register_legacy_fixture(
        store,
        gate_id="at.stale",
        job_key="job-key-stale",
        token=token,
        scheduled_for="2026-07-22T10:00:00Z",
        command=["/bin/true"],
        now="2026-07-21T10:00:00Z",
    )
    outcome = store.reconcile_at_jobs(
        set(), now=datetime(2026, 7, 21, 10, 6, tzinfo=timezone.utc)
    )
    assert outcome["alarms"][0]["job_key"] == "job-key-stale"
    assert store.show_at_job("job-key-stale")["status"] == "MISSING_ALARM"
    assert store.show_gate("at.stale")["alarm"] is True


def test_reused_historical_queue_id_is_a_domain_storage_error(tmp_path: Path) -> None:
    store = GateStore(tmp_path / "reused-id.sqlite3")
    for suffix in ("old", "new"):
        gate_id = f"at.reused-{suffix}"
        add_gate(store, gate_id)
        register_legacy_fixture(
            store,
            gate_id=gate_id,
            job_key=f"job-reused-{suffix}",
            token=f"token-{suffix}",
            scheduled_for="2026-07-22T10:00:00Z",
            command=["/bin/true"],
        )
    store.confirm_at_job("job-reused-old", "845")
    with pytest.raises(StorageError, match="UNIQUE constraint failed"):
        store.confirm_at_job("job-reused-new", "845")
    assert store.show_at_job("job-reused-new")["status"] == "SUBMITTING"


def test_stale_run_claim_alarm_is_idempotent_and_never_reexecutes(tmp_path: Path) -> None:
    store = GateStore(tmp_path / "stale-claim.sqlite3")
    add_gate(store, "at.stale-claim")
    token = "stale-claim-token"
    register_legacy_fixture(
        store,
        gate_id="at.stale-claim",
        job_key="job-stale-claim",
        token=token,
        scheduled_for="2026-07-22T10:00:00Z",
        command=["/bin/true"],
    )
    store.confirm_at_job("job-stale-claim", "468")
    store.claim_at_job(
        "job-stale-claim",
        runner_token=token,
        command=["/bin/true"],
        now=datetime(2026, 7, 22, 10, 0, 1, tzinfo=timezone.utc),
    )
    first = store.reconcile_at_jobs(
        set(), now=datetime(2026, 7, 22, 23, 0, 2, tzinfo=timezone.utc)
    )
    assert first["outcome_unknown"] == ["job-stale-claim"]
    assert store.show_at_claim("job-stale-claim")["status"] == "OUTCOME_UNKNOWN"
    gate_after_first = store.show_gate("at.stale-claim")
    second = store.reconcile_at_jobs(
        set(), now=datetime(2026, 7, 23, 0, 0, 2, tzinfo=timezone.utc)
    )
    assert second["outcome_unknown"] == ["job-stale-claim"]
    gate_after_second = store.show_gate("at.stale-claim")
    assert gate_after_second["version"] == gate_after_first["version"]
    assert len(gate_after_second["events"]) == len(gate_after_first["events"])
    assert gate_after_second["alarm_reason"] == gate_after_first["alarm_reason"]


@pytest.mark.parametrize("claim_phase", ["CLAIMED", "RECEIPT_READY"])
def test_stale_run_reconcile_preserves_fields_from_newer_legacy_writer(
    tmp_path: Path,
    claim_phase: str,
) -> None:
    store = GateStore(tmp_path / f"stale-drift-{claim_phase.lower()}.sqlite3")
    gate_id = f"at.stale-drift-{claim_phase.lower()}"
    job_key = f"job-stale-drift-{claim_phase.lower()}"
    token = f"token-{claim_phase.lower()}"
    add_gate(store, gate_id)
    register_legacy_fixture(
        store,
        gate_id=gate_id,
        job_key=job_key,
        token=token,
        scheduled_for="2026-07-22T10:00:00Z",
        command=["/bin/true"],
    )
    store.confirm_at_job(job_key, "846" if claim_phase == "CLAIMED" else "847")
    claim = store.claim_at_job(
        job_key,
        runner_token=token,
        command=["/bin/true"],
        now=datetime(2026, 7, 22, 10, 0, 1, tzinfo=timezone.utc),
    )
    if claim_phase == "RECEIPT_READY":
        identity, receipt = write_fixture_receipt(
            store,
            job_key,
            claim,
            exit_code=0,
        )
        store.record_at_receipt(
            job_key,
            claim_id=claim["claim_id"],
            receipt_path=claim["receipt_path"],
            receipt_identity=identity,
            exit_code=0,
            stdout_sha256=receipt["stdout"]["sha256"],
            stderr_sha256=receipt["stderr"]["sha256"],
            now=datetime(2026, 7, 22, 10, 0, 2, tzinfo=timezone.utc),
        )
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE gates SET version=version+1, code_sha=?, blocker=?, next_step=? "
            "WHERE gate_id=?",
            ("8" * 40, "OWNER BLOCKER", "OWNER NEXT", gate_id),
        )
        connection.commit()

    result = store.reconcile_at_jobs(
        set(),
        now=datetime(2026, 7, 22, 23, 0, 3, tzinfo=timezone.utc),
    )
    assert result["outcome_unknown"] == [job_key]
    gate = store.show_gate(gate_id)
    assert gate["alarm"] is True
    assert gate["blocker"] == "OWNER BLOCKER"
    assert gate["next_step"] == "OWNER NEXT"


def test_full_ledger_hash_binds_jobs_claims_receipts_and_events(tmp_path: Path) -> None:
    store = GateStore(tmp_path / "ledger-hash.sqlite3")
    add_gate(store, "at.hash")
    token = "ledger-hash-token"
    register_legacy_fixture(
        store,
        gate_id="at.hash",
        job_key="job-hash",
        token=token,
        scheduled_for="2026-07-22T10:00:00Z",
        command=["/bin/true"],
    )
    store.confirm_at_job("job-hash", "469")
    before_claim = export_payload(
        store, as_of=datetime(2026, 7, 22, 10, 0, tzinfo=timezone.utc)
    )
    with sqlite3.connect(store.db_path) as connection:
        original_auth = connection.execute(
            "SELECT runner_token_hash, runner_auth_tag FROM at_jobs WHERE job_key=?",
            ("job-hash",),
        ).fetchone()
        assert original_auth is not None
        connection.execute(
            "UPDATE at_jobs SET runner_token_hash=?, runner_auth_tag=? WHERE job_key=?",
            ("a" * 64, "b" * 64, "job-hash"),
        )
        connection.commit()
    auth_mutated = export_payload(
        store, as_of=datetime(2026, 7, 22, 10, 0, 30, tzinfo=timezone.utc)
    )
    assert auth_mutated["at_jobs"] == before_claim["at_jobs"]
    assert "runner_token_hash" not in auth_mutated["at_jobs"][0]
    assert "runner_auth_tag" not in auth_mutated["at_jobs"][0]
    assert auth_mutated["ledger_hash"] != before_claim["ledger_hash"]
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE at_jobs SET runner_token_hash=?, runner_auth_tag=? WHERE job_key=?",
            (original_auth[0], original_auth[1], "job-hash"),
        )
        connection.commit()
    restored_auth = export_payload(
        store, as_of=datetime(2026, 7, 22, 10, 0, 45, tzinfo=timezone.utc)
    )
    assert restored_auth["ledger_hash"] == before_claim["ledger_hash"]
    claim = store.claim_at_job(
        "job-hash",
        runner_token=token,
        command=["/bin/true"],
        now=datetime(2026, 7, 22, 10, 0, 1, tzinfo=timezone.utc),
    )
    after_claim = export_payload(
        store, as_of=datetime(2026, 7, 22, 10, 1, tzinfo=timezone.utc)
    )
    assert after_claim["ledger_hash"] != before_claim["ledger_hash"]

    identity, receipt = write_fixture_receipt(
        store,
        "job-hash",
        claim,
        exit_code=0,
    )
    store.record_at_receipt(
        "job-hash",
        claim_id=claim["claim_id"],
        receipt_path=claim["receipt_path"],
        receipt_identity=identity,
        exit_code=0,
        stdout_sha256=receipt["stdout"]["sha256"],
        stderr_sha256=receipt["stderr"]["sha256"],
    )
    after_receipt = export_payload(
        store, as_of=datetime(2026, 7, 22, 10, 2, tzinfo=timezone.utc)
    )
    same_state_later = export_payload(
        store, as_of=datetime(2026, 7, 22, 11, 2, tzinfo=timezone.utc)
    )
    assert after_receipt["gates"] == after_claim["gates"]
    assert after_receipt["gate_events"] == after_claim["gate_events"]
    assert after_receipt["at_jobs"] == after_claim["at_jobs"]
    assert after_receipt["ledger_hash"] != after_claim["ledger_hash"]
    assert same_state_later["ledger_hash"] == after_receipt["ledger_hash"]
    view = render_open_gates(
        after_receipt["gates"],
        as_of=datetime(2026, 7, 22, 10, 3, tzinfo=timezone.utc),
        ledger_hash=after_receipt["ledger_hash"],
        scheduler_anomalies=after_receipt["scheduler_anomalies"],
    )
    assert after_receipt["ledger_hash"] in view

    store.finalize_at_claim(
        "job-hash",
        claim_id=claim["claim_id"],
        receipt_identity=identity,
    )
    after_finalize = export_payload(
        store, as_of=datetime(2026, 7, 22, 10, 4, tzinfo=timezone.utc)
    )
    assert after_finalize["ledger_hash"] != after_receipt["ledger_hash"]


def test_export_hash_and_public_rows_share_one_sqlite_snapshot(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = GateStore(tmp_path / "ledger-snapshot.sqlite3")
    add_gate(store, "at.snapshot")
    at = datetime(2026, 7, 22, 10, 0, tzinfo=timezone.utc)
    baseline = export_payload(store, as_of=at)
    fired = False

    def commit_after_gate_read(table: str) -> None:
        nonlocal fired
        if table != "gates" or fired:
            return
        fired = True
        store.transition(
            "at.snapshot",
            "WAIT_DATA",
            expected_version=1,
            actor="pytest/concurrent-writer",
            reason="commit pomiędzy SELECT-ami eksportu",
            now=datetime(2026, 7, 22, 10, 0, 1, tzinfo=timezone.utc),
        )

    monkeypatch.setattr(
        store,
        "_ledger_snapshot_checkpoint",
        commit_after_gate_read,
    )
    raced = export_payload(store, as_of=at)
    assert fired is True
    assert raced == baseline

    after_commit = export_payload(store, as_of=at)
    assert after_commit["gates"][0]["version"] == 2
    assert after_commit["gate_events"][-1]["result_version"] == 2
    assert after_commit["ledger_hash"] != baseline["ledger_hash"]


def test_claim_schema_rejects_cross_gate_and_incomplete_terminal_receipt(
    tmp_path: Path,
) -> None:
    store = GateStore(tmp_path / "claim-integrity.sqlite3")
    add_gate(store, "at.integrity-a")
    add_gate(store, "at.integrity-b")
    register_legacy_fixture(
        store,
        gate_id="at.integrity-a",
        job_key="job-integrity-a",
        token="integrity-token",
        scheduled_for="2026-07-22T10:00:00Z",
        command=["/bin/true"],
    )
    binding = json.dumps(
        {
            "schema_version": 1,
            "operation": "RUN",
            "job_key": "job-integrity-a",
            "gate_id": "at.integrity-a",
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    digest = hashlib.sha256(binding.encode("utf-8")).hexdigest()
    with sqlite3.connect(store.db_path) as connection:
        connection.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO at_job_claims (
                    claim_id, job_key, gate_id, status, binding_json,
                    binding_sha256, receipt_path, claimed_at, updated_at
                ) VALUES (?, ?, ?, 'CLAIMED', ?, ?, ?, ?, ?)
                """,
                (
                    "claim-cross-gate",
                    "job-integrity-a",
                    "at.integrity-b",
                    binding,
                    digest,
                    "/fixture/receipt.json",
                    "2026-07-22T10:00:00Z",
                    "2026-07-22T10:00:00Z",
                ),
            )
        connection.rollback()
        with pytest.raises(sqlite3.IntegrityError):
            connection.execute(
                """
                INSERT INTO at_job_claims (
                    claim_id, job_key, gate_id, status, binding_json,
                    binding_sha256, receipt_path, claimed_at, updated_at
                ) VALUES (?, ?, ?, 'FINALIZED', ?, ?, ?, ?, ?)
                """,
                (
                    "claim-incomplete-final",
                    "job-integrity-a",
                    "at.integrity-a",
                    binding,
                    digest,
                    "/fixture/receipt.json",
                    "2026-07-22T10:00:00Z",
                    "2026-07-22T10:00:00Z",
                ),
            )
        connection.rollback()
    assert store.list_at_claims() == []
    store.confirm_at_job("job-integrity-a", "470")
    claim = store.claim_at_job(
        "job-integrity-a",
        runner_token="integrity-token",
        command=["/bin/true"],
    )
    mutated_binding = dict(claim["binding"])
    mutated_binding["receipt_path"] = "/fixture/attacker-controlled/receipt.json"
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            """
            UPDATE at_job_claims SET receipt_path=?, binding_json=?,
                binding_sha256=? WHERE claim_id=?
            """,
            (
                mutated_binding["receipt_path"],
                json.dumps(mutated_binding, sort_keys=True, separators=(",", ":")),
                hashlib.sha256(
                    json.dumps(
                        mutated_binding,
                        sort_keys=True,
                        separators=(",", ":"),
                    ).encode("utf-8")
                ).hexdigest(),
                claim["claim_id"],
            ),
        )
        connection.commit()
    with pytest.raises(ClaimConflict, match="binding identity mismatch"):
        store.record_at_receipt(
            "job-integrity-a",
            claim_id=claim["claim_id"],
            receipt_path=mutated_binding["receipt_path"],
            receipt_identity={
                "sha256": "c" * 64,
                "device": 1,
                "inode": 2,
                "ctime_ns": 3,
                "size": 4,
            },
            exit_code=0,
            stdout_sha256="d" * 64,
            stderr_sha256="e" * 64,
        )
    assert store.show_at_claim("job-integrity-a")["status"] == "CLAIMED"


def test_open_gates_empty_ledger_renders_placeholder() -> None:
    rendered = render_open_gates(
        [],
        as_of=datetime(2026, 7, 22, 10, tzinfo=timezone.utc),
        scheduler_anomalies=[
            {
                "gate_id": "g.term",
                "job_key": "j1",
                "at_job_id": "228",
                "job_status": "SCHEDULED",
                "claim_status": "CLAIMED",
            }
        ],
    )
    assert "brak otwartych bramek" in rendered
    assert "Otwarte: **0**" in rendered
    for detail in ("g.term", "j1", "#228", "SCHEDULED", "CLAIMED"):
        assert detail in rendered


def test_durable_receipt_recovery_and_unknown_window_never_reexecutes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    spec = importlib.util.spec_from_file_location("at_gate_receipt_test", TOOLS / "at_gate.py")
    assert spec and spec.loader
    at_gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(at_gate)
    queue_id = 700

    def fake_at(command_line, *, stdin=None, timeout=30.0, env=None):
        nonlocal queue_id
        del stdin, timeout, env
        queue_id += 1
        return subprocess.CompletedProcess(
            command_line, 0, f"job {queue_id} fixture\n", ""
        )

    monkeypatch.setattr(at_gate, "_run_process", fake_at)

    def schedule_case(label: str):
        db = tmp_path / f"{label}.sqlite3"
        args = at_gate.build_parser().parse_args(
            [
                "--db",
                str(db),
                "schedule",
                "--id",
                f"at.receipt-{label}",
                "--title",
                label,
                "--owner",
                "pytest",
                "--due",
                "2099-01-03T00:00:00Z",
                "--when",
                "2099-01-02T10:00:00Z",
                "--code-sha",
                CODE_SHA,
                "--evidence-hash",
                EVIDENCE,
                "--payload-dir",
                str(tmp_path / f"payload-{label}"),
                "--artifact-dir",
                str(tmp_path / f"artifact-{label}"),
                "--",
                "/bin/true",
            ]
        )
        assert at_gate.schedule(args) == 0
        store = GateStore(db)
        job = store.list_at_jobs(active_only=True)[0]
        payload_path = Path(job["payload_path"])
        payload, identity = at_gate._load_sealed_payload(payload_path)
        namespace = SimpleNamespace(
            db=str(db),
            job_key=job["job_key"],
            token=payload["runner_token"],
            artifact_root=payload["artifact_root"],
        )
        return store, job, payload_path, payload, identity, namespace

    child_starts: list[list[str]] = []

    def durable_capture_spy(
        command,
        *,
        child_env,
        stdout_handle,
        stderr_handle,
    ):
        del child_env, stderr_handle
        child_starts.append(list(command))
        stdout_handle.write(b"durable child result\n")
        return at_gate.ExecutionCapture(0, True, True, True)

    monkeypatch.setattr(at_gate, "_execute_with_owned_streams", durable_capture_spy)
    monkeypatch.setattr(
        sys.modules["process_debt_gate"],
        "utc_now",
        lambda: datetime(2099, 1, 2, 10, 1, tzinfo=timezone.utc),
    )

    store, job, payload_path, payload, identity, namespace = schedule_case("db-crash")
    original_record = GateStore.record_at_receipt

    def fail_db_record(self, *args, **kwargs):
        del self, args, kwargs
        raise GateError("synthetic crash after receipt publish")

    monkeypatch.setattr(GateStore, "record_at_receipt", fail_db_record)
    first_rc = at_gate._run_registered_inner(
        namespace,
        payload["command"],
        None,
        payload_path=payload_path,
        payload_identity=identity,
        require_auth_version=2,
    )
    assert first_rc == 125
    claim = store.show_at_claim(job["job_key"])
    assert claim["status"] == "CLAIMED"
    assert Path(claim["receipt_path"]).exists()
    assert payload_path.exists()
    assert len(child_starts) == 1

    monkeypatch.setattr(GateStore, "record_at_receipt", original_record)
    recovered_rc = at_gate._run_registered_inner(
        namespace,
        payload["command"],
        None,
        payload_path=payload_path,
        payload_identity=identity,
        require_auth_version=2,
    )
    assert recovered_rc == 0
    assert len(child_starts) == 1
    assert store.show_at_claim(job["job_key"])["status"] == "FINALIZED"
    assert store.show_at_job(job["job_key"])["status"] == "SUCCEEDED"
    assert not payload_path.exists()
    with pytest.raises(ReceiptError, match="identity zmieniło się"):
        store.finalize_at_claim(
            job["job_key"],
            claim_id=claim["claim_id"],
            receipt_identity={
                "sha256": "f" * 64,
                "device": 1,
                "inode": 2,
                "ctime_ns": 3,
                "size": 4,
            },
        )

    (
        unknown_store,
        unknown_job,
        unknown_payload_path,
        unknown_payload,
        unknown_identity,
        unknown_namespace,
    ) = schedule_case("unknown")
    original_write_receipt = at_gate._write_result_receipt

    def crash_before_receipt(**_kwargs):
        raise RuntimeError("synthetic crash after child, before receipt")

    monkeypatch.setattr(at_gate, "_write_result_receipt", crash_before_receipt)
    with pytest.raises(RuntimeError, match="before receipt"):
        at_gate._run_registered_inner(
            unknown_namespace,
            unknown_payload["command"],
            None,
            payload_path=unknown_payload_path,
            payload_identity=unknown_identity,
            require_auth_version=2,
        )
    assert len(child_starts) == 2
    assert unknown_payload_path.exists()
    monkeypatch.setattr(at_gate, "_write_result_receipt", original_write_receipt)
    with pytest.raises(ReceiptError, match="OUTCOME_UNKNOWN"):
        at_gate._run_registered_inner(
            unknown_namespace,
            unknown_payload["command"],
            None,
            payload_path=unknown_payload_path,
            payload_identity=unknown_identity,
            require_auth_version=2,
        )
    assert len(child_starts) == 2

    first_alarm = unknown_store.reconcile_at_jobs(
        set(), now=datetime(2099, 1, 2, 23, 2, tzinfo=timezone.utc)
    )
    assert first_alarm["outcome_unknown"] == [unknown_job["job_key"]]
    first_gate = unknown_store.show_gate(unknown_job["gate_id"])
    second_alarm = unknown_store.reconcile_at_jobs(
        set(), now=datetime(2099, 1, 3, 0, 2, tzinfo=timezone.utc)
    )
    assert second_alarm["outcome_unknown"] == [unknown_job["job_key"]]
    second_gate = unknown_store.show_gate(unknown_job["gate_id"])
    assert second_gate["version"] == first_gate["version"]
    assert len(second_gate["events"]) == len(first_gate["events"])

    late_claim = unknown_store.show_at_claim(unknown_job["job_key"])
    write_fixture_receipt(
        unknown_store,
        unknown_job["job_key"],
        late_claim,
        exit_code=0,
    )
    recovered_job, recovered_claim, _receipt, _stdout, _stderr = (
        at_gate._recover_existing(unknown_store, unknown_job["job_key"])
    )
    assert recovered_job["status"] == "SUCCEEDED"
    assert recovered_claim["status"] == "FINALIZED"
    recovered_gate = unknown_store.show_gate(unknown_job["gate_id"])
    assert recovered_gate["alarm"] is False
    assert recovered_gate["blocker"] == late_claim["binding"]["gate_blocker"]

    # Mutation/TOCTOU: zniknięcie receiptu tuż przed granicą DB nie może
    # sfinalizować joba ani usunąć sealed payloadu.
    (
        vanished_store,
        vanished_job,
        vanished_payload_path,
        vanished_payload,
        vanished_identity,
        vanished_namespace,
    ) = schedule_case("receipt-vanished")
    starts_before_vanished = len(child_starts)

    def unlink_before_db_boundary(self, *args, **kwargs):
        Path(kwargs["receipt_path"]).unlink()
        return original_record(self, *args, **kwargs)

    monkeypatch.setattr(GateStore, "record_at_receipt", unlink_before_db_boundary)
    assert (
        at_gate._run_registered_inner(
            vanished_namespace,
            vanished_payload["command"],
            None,
            payload_path=vanished_payload_path,
            payload_identity=vanished_identity,
            require_auth_version=2,
        )
        == 125
    )
    monkeypatch.setattr(GateStore, "record_at_receipt", original_record)
    assert len(child_starts) == starts_before_vanished + 1
    assert vanished_store.show_at_job(vanished_job["job_key"])["status"] == "SCHEDULED"
    assert vanished_store.show_at_claim(vanished_job["job_key"])["status"] == "CLAIMED"
    assert vanished_payload_path.exists()

    # Exit != 0 odzyskany z trwałego receiptu musi być widoczny jako failure
    # całego reconcile, a nie jako fałszywe rc=0.
    (
        failed_store,
        failed_job,
        failed_payload_path,
        failed_payload,
        failed_identity,
        _failed_namespace,
    ) = schedule_case("recovered-failure")
    failed_claim = failed_store.claim_at_job(
        failed_job["job_key"],
        runner_token=failed_payload["runner_token"],
        command=failed_payload["command"],
        payload_path=str(failed_payload_path),
        payload_identity=failed_identity,
        artifact_root=failed_payload["artifact_root"],
        require_auth_version=2,
        now=datetime(2099, 1, 2, 10, 1, tzinfo=timezone.utc),
    )
    write_fixture_receipt(
        failed_store,
        failed_job["job_key"],
        failed_claim,
        exit_code=7,
    )
    empty_atq = tmp_path / "empty-atq.txt"
    empty_atq.write_text("", encoding="utf-8")
    reconcile_args = at_gate.build_parser().parse_args(
        [
            "--db",
            str(failed_store.db_path),
            "reconcile",
            "--atq-file",
            str(empty_atq),
        ]
    )
    capsys.readouterr()
    assert at_gate.reconcile(reconcile_args) == 1
    reconcile_output = json.loads(capsys.readouterr().out)
    assert reconcile_output["recovered"] == [failed_job["job_key"]]
    assert reconcile_output["recovered_failures"][0]["exit_code"] == 7
    assert failed_store.show_at_job(failed_job["job_key"])["status"] == "FAILED"
    assert failed_store.show_gate(failed_job["gate_id"])["alarm"] is True

    # Samo istnienie niepoprawnego receipt.json nie może wiecznie omijać
    # OUTCOME_UNKNOWN ani trwałego ALARM-u.
    (
        invalid_store,
        invalid_job,
        invalid_payload_path,
        invalid_payload,
        invalid_identity,
        _invalid_namespace,
    ) = schedule_case("invalid-receipt")
    invalid_claim = invalid_store.claim_at_job(
        invalid_job["job_key"],
        runner_token=invalid_payload["runner_token"],
        command=invalid_payload["command"],
        payload_path=str(invalid_payload_path),
        payload_identity=invalid_identity,
        artifact_root=invalid_payload["artifact_root"],
        require_auth_version=2,
        now=datetime(2099, 1, 2, 10, 1, tzinfo=timezone.utc),
    )
    invalid_receipt = Path(invalid_claim["receipt_path"])
    invalid_receipt.parent.mkdir(mode=0o700, parents=True)
    invalid_receipt.write_bytes(b"{}\n")
    invalid_receipt.chmod(0o600)
    invalid_outcome = invalid_store.reconcile_at_jobs(
        set(),
        now=datetime(2099, 1, 2, 23, 2, tzinfo=timezone.utc),
    )
    assert invalid_outcome["recovery_candidates"] == [invalid_job["job_key"]]
    assert invalid_outcome["outcome_unknown"] == [invalid_job["job_key"]]
    assert invalid_outcome["alarms"][0]["job_key"] == invalid_job["job_key"]
    assert invalid_store.show_at_claim(invalid_job["job_key"])["status"] == "OUTCOME_UNKNOWN"
    assert invalid_store.show_gate(invalid_job["gate_id"])["alarm"] is True


def test_database_is_0600_without_changing_existing_parent_mode(tmp_path: Path) -> None:
    parent = tmp_path / "shared"
    parent.mkdir(mode=0o755)
    parent.chmod(0o755)
    database = parent / "gates.sqlite3"
    add_gate(GateStore(database))
    assert stat.S_IMODE(parent.stat().st_mode) == 0o755
    assert stat.S_IMODE(database.stat().st_mode) == 0o600


def test_private_receipt_reader_rejects_intermediate_symlink(tmp_path: Path) -> None:
    private = tmp_path / "private-real"
    private.mkdir(mode=0o700)
    private.chmod(0o700)
    receipt = private / "receipt.json"
    receipt.write_bytes(b"{}\n")
    receipt.chmod(0o600)
    alias = tmp_path / "private-alias"
    alias.symlink_to(private, target_is_directory=True)
    with pytest.raises(ValidationError, match="prywatny katalog"):
        read_private_bytes(alias / "receipt.json")


def test_open_view_is_deterministic_and_sorted_by_days(tmp_path: Path) -> None:
    store = GateStore(tmp_path / "gates.sqlite3")
    add_gate(store, "newer.gate", opened_at="2026-07-20T00:00:00Z")
    add_gate(store, "older.gate", opened_at="2026-06-20T00:00:00Z")
    as_of = datetime(2026, 7, 21, 12, tzinfo=timezone.utc)
    first = render_open_gates(store.list_gates(), as_of=as_of, source="fixed.sqlite3")
    second = render_open_gates(store.list_gates(), as_of=as_of, source="fixed.sqlite3")
    assert first.encode() == second.encode()
    assert first.index("older.gate") < first.index("newer.gate")
    assert 20 <= len(first.splitlines()) <= 30
    assert "GENERATED — edycja bezcelowa" in first


def test_committed_open_view_is_generator_shaped() -> None:
    # Do 21.07 (pre-deploy) widok byl placeholderem "NOT_DEPLOYED" i test wymagal
    # rownosci bajtowej. Od wdrozenia ledgera (seed zaimportowany 21.07 wieczor)
    # OPEN_GATES.md jest generowany z ZYWEJ bazy — rownosc z placeholderem klamie,
    # a rownosc z zywa baza zlamalaby hermetycznosc testow. Kontrakt sprawdzany
    # odtad: plik w worktree ma ksztalt wyjscia generatora (naglowek GENERATED,
    # zrodlo, hash ledgera, licznik otwartych, sekcja Kontrola, 20-30 linii).
    committed = (
        TOOLS.parent
        / "tests"
        / "fixtures"
        / "process_debt"
        / "OPEN_GATES_2026-07-24.md"
    ).read_text(encoding="utf-8")
    lines = committed.splitlines()
    assert lines[0] == "# OPEN GATES"
    assert "GENERATED — edycja bezcelowa" in committed
    assert "Ledger SHA-256" in committed
    assert "Otwarte: **" in committed
    assert "## Kontrola" in committed
    assert 20 <= len(lines) <= 30


def test_open_gates_snapshot_is_copied_as_immutable_fixture() -> None:
    fixture = (
        TOOLS.parent
        / "tests"
        / "fixtures"
        / "process_debt"
        / "OPEN_GATES_2026-07-24.md"
    ).read_text(encoding="utf-8")
    assert "Ledger SHA-256: `4b9c557f8750be208bb20267ab46082c83c96c09b89f3bbf2f130fcc66e175b6`" in fixture
    assert "| 49 | audit.fail03-k2 | WAIT_DATA | CTO | 2026-07-25 | — |" in fixture


def test_audit_seed_is_not_auto_imported_and_all_records_validate(tmp_path: Path) -> None:
    seed_path = TOOLS / "process_debt_seed_2026-07-21.json"
    seed = json.loads(seed_path.read_text(encoding="utf-8"))
    assert seed["auto_import"] is False
    assert len(seed["records"]) == 17
    store = GateStore(tmp_path / "seed.sqlite3")
    for record in seed["records"]:
        assert record["state"] == "BUILT_OFF"
        store.add_gate(
            gate_id=record["gate_id"],
            title=record["title"],
            kind=record["kind"],
            owner=record["owner"],
            due_at=record["due_at"],
            next_step=record["next_step"],
            blocker=record["blocker"],
            code_sha=record["code_sha"],
            evidence_hash=record["evidence_hash"],
            opened_at=record["opened_at"],
            metadata=record["metadata"],
        )
    assert len(store.list_gates()) == 17


def test_open_gates_view_renders_with_full_table(tmp_path: Path) -> None:
    """RED-first: pełna tabela (10 widocznych bramek) MUSI się wyrenderować.

    Sztywna asercja `20 <= len(lines) <= 30` pękała, gdy sekcja „Kontrola"
    urosła o legendę ŚWIEŻA — czyli widok żywego ledgera przestawał się
    generować w ogóle, mimo poprawnych danych. Rama widoku ma zależeć od
    liczby wierszy, a nie od magicznej liczby.
    """
    store = GateStore(tmp_path / "gates.sqlite3")
    for i in range(12):
        add_gate(store, f"test.gate{i:02d}", opened_at="2026-06-01T00:00:00Z")
    view = render_open_gates(
        store.list_gates(),
        as_of=datetime(2026, 7, 24, 12, tzinfo=timezone.utc),
        source="fixture.sqlite3",
    )
    assert view.startswith("# OPEN GATES")
    assert "## Kontrola" in view
    rows = [ln for ln in view.splitlines() if ln.startswith("| test.gate")
            or (ln.startswith("|") and "test.gate" in ln)]
    assert len(rows) == 10, f"tabela ma pokazac 10 wierszy, ma {len(rows)}"
    assert "Pominięte z tabeli: 2." in view


def test_runner_waits_for_pipe_eof_and_never_finalizes_incomplete_capture(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = importlib.util.spec_from_file_location("at_gate_pipe_test", TOOLS / "at_gate.py")
    assert spec and spec.loader
    at_gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(at_gate)

    def prepared_case(label: str, command: list[str]) -> tuple[GateStore, SimpleNamespace]:
        store = GateStore(tmp_path / f"pipe-{label}.sqlite3")
        gate_id = f"at.pipe-{label}"
        job_key = f"job-pipe-{label}"
        token = f"token-{label}"
        add_gate(store, gate_id)
        register_legacy_fixture(
            store,
            gate_id=gate_id,
            job_key=job_key,
            token=token,
            scheduled_for="2026-07-22T10:00:00Z",
            command=command,
        )
        store.confirm_at_job(job_key, "801" if label == "late" else "802")
        return store, SimpleNamespace(db=str(store.db_path), job_key=job_key, token=token)

    late_command = [
        "/bin/sh",
        "-c",
        "(sleep 0.25; printf late-auth2-output) & exit 0",
    ]
    late_store, late_args = prepared_case("late", late_command)
    started = time.monotonic()
    assert at_gate._run_registered_inner(late_args, late_command, None) == 0
    elapsed = time.monotonic() - started
    assert elapsed >= 0.20
    late_claim = late_store.show_at_claim("job-pipe-late")
    assert late_claim["status"] == "FINALIZED"
    receipt, identity, stdout, _stderr = load_claim_receipt(
        late_claim["receipt_path"],
        claim=late_claim,
    )
    assert stdout == b"late-auth2-output"
    assert receipt["execution"] == {
        "child_started": True,
        "direct_child_exit_observed": True,
        "stdio_eof_observed": True,
    }
    time.sleep(0.15)
    repeated, repeated_identity, repeated_stdout, _ = load_claim_receipt(
        late_claim["receipt_path"],
        claim=late_claim,
    )
    assert repeated == receipt
    assert repeated_identity == identity
    assert repeated_stdout == stdout
    tampered = dict(receipt)
    tampered["execution"] = dict(receipt["execution"])
    tampered["execution"]["stdio_eof_observed"] = False
    Path(late_claim["receipt_path"]).write_text(
        json.dumps(tampered, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    Path(late_claim["receipt_path"]).chmod(0o600)
    with pytest.raises(ReceiptError, match="bez poświadczonego EOF"):
        load_claim_receipt(late_claim["receipt_path"], claim=late_claim)

    lingering_command = ["/bin/sh", "-c", "(sleep 2) & exit 0"]
    lingering_store, lingering_args = prepared_case("lingering", lingering_command)
    monkeypatch.setattr(at_gate, "_PIPE_EOF_GRACE_SECONDS", 0.10)
    started = time.monotonic()
    assert at_gate._run_registered_inner(lingering_args, lingering_command, None) == 125
    assert time.monotonic() - started < 1.0
    lingering_claim = lingering_store.show_at_claim("job-pipe-lingering")
    assert lingering_claim["status"] == "OUTCOME_UNKNOWN"
    assert not Path(lingering_claim["receipt_path"]).exists()
    assert lingering_store.show_at_job("job-pipe-lingering")["status"] == "MISSING_ALARM"
    assert lingering_store.show_gate("at.pipe-lingering")["alarm"] is True

    overflow_command = [
        sys.executable,
        "-c",
        "import sys; sys.stdout.buffer.write(b'x' * 256)",
    ]
    overflow_store, overflow_args = prepared_case("overflow", overflow_command)
    monkeypatch.setattr(at_gate, "_MAX_PRIVATE_FILE_BYTES", 64)
    assert at_gate._run_registered_inner(overflow_args, overflow_command, None) == 125
    overflow_claim = overflow_store.show_at_claim("job-pipe-overflow")
    assert overflow_claim["status"] == "OUTCOME_UNKNOWN"
    assert not Path(overflow_claim["receipt_path"]).exists()
    assert overflow_store.show_at_job("job-pipe-overflow")["status"] == "MISSING_ALARM"
    assert overflow_store.show_gate("at.pipe-overflow")["alarm"] is True

    source = inspect.getsource(at_gate._execute_with_owned_streams)
    assert "process.poll()" in source
    assert "selector.get_map()" in source
    assert "stdout=subprocess.PIPE" in source
    assert "stderr=subprocess.PIPE" in source
    assert "raise StreamCaptureUnknown" in source


def test_preopen_and_early_runner_fail_before_child_or_authority(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = importlib.util.spec_from_file_location("at_gate_preopen_test", TOOLS / "at_gate.py")
    assert spec and spec.loader
    at_gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(at_gate)
    child_calls: list[list[str]] = []
    monkeypatch.setattr(
        at_gate.subprocess,
        "Popen",
        lambda argv, **_kwargs: child_calls.append(list(argv)),
    )

    store = GateStore(tmp_path / "preopen.sqlite3")
    add_gate(store, "at.preopen")
    register_legacy_fixture(
        store,
        gate_id="at.preopen",
        job_key="job-preopen",
        token="preopen-token",
        scheduled_for="2026-07-22T10:00:00Z",
        command=["/bin/true"],
    )
    store.confirm_at_job("job-preopen", "803")
    monkeypatch.setattr(
        at_gate,
        "_open_private_output",
        lambda _path: (_ for _ in ()).throw(OSError("synthetic preopen failure")),
    )
    with pytest.raises(OSError, match="preopen failure"):
        at_gate._run_registered_inner(
            SimpleNamespace(
                db=str(store.db_path),
                job_key="job-preopen",
                token="preopen-token",
            ),
            ["/bin/true"],
            None,
        )
    with pytest.raises(GateError, match="brak claimu"):
        store.show_at_claim("job-preopen")
    assert child_calls == []

    early = GateStore(tmp_path / "early.sqlite3")
    add_gate(early, "at.early")
    register_legacy_fixture(
        early,
        gate_id="at.early",
        job_key="job-early",
        token="early-token",
        scheduled_for="2026-07-22T10:00:00Z",
        command=["/bin/true"],
    )
    # Przywróć realny opener; runner trafia w SUBMITTING i zapisuje durable marker.
    monkeypatch.undo()
    with pytest.raises(GateError, match="przed confirm"):
        at_gate._run_registered_inner(
            SimpleNamespace(
                db=str(early.db_path),
                job_key="job-early",
                token="early-token",
            ),
            ["/bin/true"],
            None,
        )
    assert early.show_at_job("job-early")["reconcile_note"].startswith(
        "EARLY_RUNNER_ABORTED:"
    )
    early.reconcile_at_jobs(None, note="atq UNAVAILABLE synthetic")
    assert early.show_at_job("job-early")["reconcile_note"].startswith(
        "EARLY_RUNNER_ABORTED:"
    )
    with pytest.raises(ClaimConflict, match="przed confirm"):
        early.confirm_at_job("job-early", "804")
    with pytest.raises(GateError, match="brak claimu"):
        early.show_at_claim("job-early")


def test_accepted_submission_uses_logical_cancel_and_commit_error_is_idempotent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = importlib.util.spec_from_file_location("at_gate_submit_test", TOOLS / "at_gate.py")
    assert spec and spec.loader
    at_gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(at_gate)

    def schedule_args(db: Path, gate_id: str):
        return at_gate.build_parser().parse_args(
            [
                "--db", str(db), "schedule",
                "--id", gate_id,
                "--title", gate_id,
                "--owner", "pytest",
                "--due", "2099-01-03T00:00:00Z",
                "--when", "2099-01-02T10:00:00Z",
                "--code-sha", CODE_SHA,
                "--evidence-hash", EVIDENCE,
                "--payload-dir", str(tmp_path / f"payload-{gate_id}"),
                "--artifact-dir", str(tmp_path / f"artifact-{gate_id}"),
                "--", "/bin/true",
            ]
        )

    calls: list[list[str]] = []

    def accepted_with_nonzero(command_line, **_kwargs):
        calls.append(list(command_line))
        return subprocess.CompletedProcess(
            command_line,
            1,
            "job 811 accepted despite rc\n",
            "synthetic warning",
        )

    monkeypatch.setattr(at_gate, "_run_process", accepted_with_nonzero)
    failed_db = tmp_path / "accepted-nonzero.sqlite3"
    with pytest.raises(GateError, match="logiczny CANCEL"):
        at_gate.schedule(schedule_args(failed_db, "at.accepted-nonzero"))
    failed_store = GateStore(failed_db)
    failed_job = failed_store.list_at_jobs()[0]
    failed_claim = failed_store.show_at_claim(failed_job["job_key"])
    assert failed_job["status"] == "SUBMISSION_FAILED"
    assert failed_claim["status"] == "FINALIZED"
    assert failed_claim["binding"]["submission_rollback"] is True
    assert Path(failed_job["payload_path"]).exists()
    assert calls == [["at", "-t", "209901021000.00"]]

    # Commit mógł się udać, a sqlite/caller zgłosił błąd już po nim. Exact
    # SCHEDULED + ten sam ID jest idempotentnym sukcesem, bez CANCEL.
    original_confirm = GateStore.confirm_at_job

    def committed_then_failed(self, *args, **kwargs):
        original_confirm(self, *args, **kwargs)
        raise GateError("synthetic error after confirm commit")

    monkeypatch.setattr(GateStore, "confirm_at_job", committed_then_failed)
    monkeypatch.setattr(
        at_gate,
        "_run_process",
        lambda command_line, **_kwargs: subprocess.CompletedProcess(
            command_line, 0, "job 812 fixture\n", ""
        ),
    )
    committed_db = tmp_path / "confirm-committed.sqlite3"
    assert at_gate.schedule(schedule_args(committed_db, "at.confirm-committed")) == 0
    committed_store = GateStore(committed_db)
    committed_job = committed_store.list_at_jobs()[0]
    assert committed_job["status"] == "SCHEDULED"
    assert committed_job["at_job_id"] == "812"
    with pytest.raises(GateError, match="brak claimu"):
        committed_store.show_at_claim(committed_job["job_key"])


def test_early_runner_then_submission_cancel_removes_exact_sealed_payload(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    spec = importlib.util.spec_from_file_location(
        "at_gate_early_submit_test", TOOLS / "at_gate.py"
    )
    assert spec and spec.loader
    at_gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(at_gate)

    db = tmp_path / "early-submission.sqlite3"
    args = at_gate.build_parser().parse_args(
        [
            "--db", str(db), "schedule",
            "--id", "at.early-submission",
            "--title", "early submission",
            "--owner", "pytest",
            "--due", "2099-01-03T00:00:00Z",
            "--when", "2099-01-02T10:00:00Z",
            "--code-sha", CODE_SHA,
            "--evidence-hash", EVIDENCE,
            "--payload-dir", str(tmp_path / "early-payload"),
            "--artifact-dir", str(tmp_path / "early-artifact"),
            "--", "/bin/true",
        ]
    )
    popen_calls: list[list[str]] = []

    def forbidden_popen(command, **_kwargs):
        popen_calls.append(list(command))
        raise AssertionError("child nie może wystartować przed confirm")

    def accepted_after_early_runner(command_line, **kwargs):
        payload_path = Path(shlex.split(str(kwargs["stdin"]).strip())[-1])
        payload, payload_identity = at_gate._load_sealed_payload(payload_path)
        with pytest.raises(GateError, match="przed confirm"):
            at_gate._run_registered_inner(
                SimpleNamespace(
                    db=str(payload["db_path"]),
                    job_key=str(payload["job_key"]),
                    token=str(payload["runner_token"]),
                    artifact_root=str(payload["artifact_root"]),
                ),
                list(payload["command"]),
                None,
                payload_path=payload_path,
                payload_identity=payload_identity,
                require_auth_version=at_gate.SEALED_AUTH_VERSION,
            )
        assert payload_path.exists()
        return subprocess.CompletedProcess(
            command_line, 0, "job 813 accepted before confirm\n", ""
        )

    monkeypatch.setattr(at_gate.subprocess, "Popen", forbidden_popen)
    monkeypatch.setattr(at_gate, "_run_process", accepted_after_early_runner)
    with pytest.raises(GateError, match="logiczny CANCEL"):
        at_gate.schedule(args)

    store = GateStore(db)
    job = store.list_at_jobs()[0]
    claim = store.show_at_claim(job["job_key"])
    assert job["status"] == "SUBMISSION_FAILED"
    assert claim["status"] == "FINALIZED"
    assert claim["binding"]["submission_rollback"] is True
    assert claim["binding"]["early_runner_aborted"] is True
    assert not Path(job["payload_path"]).exists()
    assert popen_calls == []


def test_sealed_auth_v2_forged_hmac_is_rejected_before_any_claim(
    tmp_path: Path,
) -> None:
    """A-D1: podrobiony sealed HMAC nigdy nie przyznaje RUN claimu.

    Chroni `claim_at_job` -> `hmac.compare_digest(runner_auth_tag(...), expected_tag)`.
    Trzy niezależne drogi podrobienia (cudzy token, przetagowany binding,
    uszkodzony digest w DB) plus kontrola pozytywna, żeby test nie mógł
    przejść samym erroringiem.
    """

    now = datetime(2026, 7, 22, 10, 1, tzinfo=timezone.utc)

    # (a) Napastnik zna binding, ale nie zna tokenu wykonawcy.
    foreign = GateStore(tmp_path / "hmac-foreign-token.sqlite3")
    forged_token = register_sealed_fixture(
        foreign,
        tmp_path,
        suffix="hmac-token",
        queue_id="901",
        tag_token="token-napastnika",
    )
    with pytest.raises(ValidationError, match="sealed payload HMAC"):
        foreign.claim_at_job(
            forged_token["job_key"], now=now, **sealed_claim_kwargs(forged_token)
        )
    assert_no_claim_and_job_unchanged(foreign, forged_token["job_key"])

    # (b) Tag policzony nad INNYM argv — zapieczętowanie nie przenosi się na
    #     inne polecenie, choć wszystkie kolumny joba są poprawne.
    retagged_argv = GateStore(tmp_path / "hmac-retagged-argv.sqlite3")
    forged_argv = register_sealed_fixture(
        retagged_argv,
        tmp_path,
        suffix="hmac-argv",
        queue_id="902",
        tag_binding_overrides={"command_sha256": canonical_argv_hash(["/bin/false"])},
    )
    with pytest.raises(ValidationError, match="sealed payload HMAC"):
        retagged_argv.claim_at_job(
            forged_argv["job_key"], now=now, **sealed_claim_kwargs(forged_argv)
        )
    assert_no_claim_and_job_unchanged(retagged_argv, forged_argv["job_key"])

    # (c) Tag policzony nad INNYM artifact_root — przekierowanie drzewa
    #     artefaktów nie jest objęte pieczęcią.
    retagged_root = GateStore(tmp_path / "hmac-retagged-root.sqlite3")
    forged_root = register_sealed_fixture(
        retagged_root,
        tmp_path,
        suffix="hmac-root",
        queue_id="903",
        tag_binding_overrides={"artifact_root": str((tmp_path / "obcy-root").absolute())},
    )
    with pytest.raises(ValidationError, match="sealed payload HMAC"):
        retagged_root.claim_at_job(
            forged_root["job_key"], now=now, **sealed_claim_kwargs(forged_root)
        )
    assert_no_claim_and_job_unchanged(retagged_root, forged_root["job_key"])

    # (d) Uszkodzony digest w DB (obcięty / pusty) NIE degeneruje się do
    #     porównania prefiksu — leci ValidationError na kształcie hasha.
    for index, (label, tampered_tag) in enumerate(
        (
            ("truncated", "a" * 32),
            ("empty", ""),
            ("nonhex", "z" * 64),
        )
    ):
        corrupt = GateStore(tmp_path / f"hmac-db-{label}.sqlite3")
        record = register_sealed_fixture(
            corrupt,
            tmp_path,
            suffix=f"hmac-db-{label}",
            queue_id=f"91{index}",
        )
        with sqlite3.connect(corrupt.db_path) as connection:
            connection.execute(
                "UPDATE at_jobs SET runner_auth_tag = ? WHERE job_key = ?",
                (tampered_tag, record["job_key"]),
            )
            connection.commit()
        with pytest.raises(ValidationError, match="evidence_hash"):
            corrupt.claim_at_job(
                record["job_key"], now=now, **sealed_claim_kwargs(record)
            )
        assert_no_claim_and_job_unchanged(corrupt, record["job_key"])

    # Kontrola pozytywna: nietknięta pieczęć nadal przyznaje dokładnie
    # jeden RUN claim. Bez tego cały test przechodziłby po zaślepieniu
    # `claim_at_job` dowolnym wyjątkiem.
    healthy = GateStore(tmp_path / "hmac-healthy.sqlite3")
    good = register_sealed_fixture(
        healthy, tmp_path, suffix="hmac-ok", queue_id="904"
    )
    claim = healthy.claim_at_job(
        good["job_key"], now=now, **sealed_claim_kwargs(good)
    )
    assert claim["status"] == "CLAIMED"
    assert claim["binding"]["command_sha256"] == canonical_argv_hash(good["command"])


def test_sealed_auth_v2_rejects_every_unsealed_argument_mismatch(
    tmp_path: Path,
) -> None:
    """A-D1: token, auth_version, artifact_root, payload_path i payload identity.

    Każdy z tych checków w `claim_at_job` był bez oracle — wycięcie go
    przechodziło suitę na zielono.
    """

    now = datetime(2026, 7, 22, 10, 1, tzinfo=timezone.utc)
    store = GateStore(tmp_path / "sealed-mismatch.sqlite3")
    record = register_sealed_fixture(
        store, tmp_path, suffix="mismatch", queue_id="905"
    )
    job_key = str(record["job_key"])

    def reject(message: str, **overrides) -> None:
        kwargs = dict(sealed_claim_kwargs(record))
        kwargs.update(overrides)
        with pytest.raises(ValidationError, match=message):
            store.claim_at_job(job_key, now=now, **kwargs)
        assert_no_claim_and_job_unchanged(store, job_key)

    reject("niepoprawny token wykonawcy", runner_token="nie-ten-token")
    reject("command identity mismatch", command=["/bin/false"])
    reject("wymagany auth_version=1", require_auth_version=1)
    reject(
        "artifact_root nie zgadza się",
        artifact_root=str((tmp_path / "inny-root").absolute()),
    )
    reject(
        "artifact_root musi być kanoniczną ścieżką absolutną",
        artifact_root=f"{tmp_path}/./niekanoniczny",
    )
    reject("payload path nie zgadza się", payload_path=None)
    reject(
        "payload path nie zgadza się",
        payload_path=str((tmp_path / "obcy-payload.json").absolute()),
    )
    for field, value in (
        ("sha256", "b" * 64),
        ("device", int(record["identity"]["device"]) + 1),
        ("ctime_ns", int(record["identity"]["ctime_ns"]) + 1),
        ("size", int(record["identity"]["size"]) + 1),
    ):
        reject(
            "payload identity nie zgadza się",
            payload_identity=dict(record["identity"], **{field: value}),
        )

    # Bliźniacza ścieżka: job auth v1 nie może przyjąć sealed argumentów.
    legacy = GateStore(tmp_path / "sealed-legacy.sqlite3")
    add_gate(legacy, "at.legacy-sealed")
    register_legacy_fixture(
        legacy,
        gate_id="at.legacy-sealed",
        job_key="job-legacy-sealed",
        token="legacy-token",
        scheduled_for="2026-07-22T10:00:00Z",
        command=["/bin/true"],
    )
    legacy.confirm_at_job("job-legacy-sealed", "906")
    with pytest.raises(ValidationError, match="auth v1 nie przyjmuje sealed payload"):
        legacy.claim_at_job(
            "job-legacy-sealed",
            runner_token="legacy-token",
            command=["/bin/true"],
            payload_path=str((tmp_path / "payload-mismatch.json").absolute()),
            now=now,
        )
    assert_no_claim_and_job_unchanged(legacy, "job-legacy-sealed")

    # Kontrola pozytywna na TYM SAMYM jobie: komplet poprawnych argumentów
    # nadal przechodzi, więc powyższe odrzucenia nie są skutkiem złego fixture.
    assert (
        store.claim_at_job(job_key, now=now, **sealed_claim_kwargs(record))["status"]
        == "CLAIMED"
    )


def read_claim_rows(store: GateStore, job_key: str) -> list[dict]:
    """Surowe wiersze claimu — łapią też pola, których `show_at_claim` nie pokazuje."""

    connection = sqlite3.connect(store.db_path)
    try:
        connection.row_factory = sqlite3.Row
        return [
            dict(row)
            for row in connection.execute(
                "SELECT * FROM at_job_claims WHERE job_key = ? ORDER BY claim_id",
                (job_key,),
            )
        ]
    finally:
        connection.close()


def count_gate_events(store: GateStore, gate_id: str, actor: str) -> int:
    connection = sqlite3.connect(store.db_path)
    try:
        return int(
            connection.execute(
                "SELECT COUNT(*) FROM gate_events WHERE gate_id = ? AND actor = ?",
                (gate_id, actor),
            ).fetchone()[0]
        )
    finally:
        connection.close()


def claimed_sealed_job(
    store: GateStore,
    tmp_path: Path,
    *,
    suffix: str,
    queue_id: str,
) -> tuple[dict, dict]:
    record = register_sealed_fixture(
        store, tmp_path, suffix=suffix, queue_id=queue_id
    )
    claim = store.claim_at_job(
        str(record["job_key"]),
        now=datetime(2026, 7, 22, 10, 1, tzinfo=timezone.utc),
        **sealed_claim_kwargs(record),
    )
    return record, claim


def test_record_at_receipt_retry_is_idempotent_and_blocks_divergent_second(
    tmp_path: Path,
) -> None:
    """A-D2: powtórka runnera po timeoucie nie duplikuje i nie mutuje receiptu.

    Retry z INNYM `now` musi wrócić przez early-return (`updated_at` bez zmian),
    a rozbieżny drugi receipt musi zostać odrzucony bez tknięcia wiersza.
    """

    store = GateStore(tmp_path / "receipt-idempotent.sqlite3")
    record, claim = claimed_sealed_job(
        store, tmp_path, suffix="receipt-retry", queue_id="920"
    )
    job_key = str(record["job_key"])
    identity, receipt = write_fixture_receipt(store, job_key, claim, exit_code=0)
    record_kwargs = {
        "claim_id": str(claim["claim_id"]),
        "receipt_path": str(claim["receipt_path"]),
        "receipt_identity": identity,
        "exit_code": 0,
        "stdout_sha256": str(receipt["stdout"]["sha256"]),
        "stderr_sha256": str(receipt["stderr"]["sha256"]),
    }

    first = store.record_at_receipt(
        job_key,
        now=datetime(2026, 7, 22, 10, 2, tzinfo=timezone.utc),
        **record_kwargs,
    )
    assert first["status"] == "RECEIPT_READY"
    after_first = read_claim_rows(store, job_key)
    assert len(after_first) == 1

    # Retry z późniejszym zegarem: exact-match musi wrócić BEZ zapisu, więc
    # `updated_at` zostaje przy pierwszym timestampie.
    second = store.record_at_receipt(
        job_key,
        now=datetime(2026, 7, 22, 11, 30, tzinfo=timezone.utc),
        **record_kwargs,
    )
    assert second == first
    assert read_claim_rows(store, job_key) == after_first
    assert after_first[0]["updated_at"] == "2026-07-22T10:02:00Z"

    # Rozbieżny drugi receipt (inny exit_code, spójny z plikiem na dysku)
    # musi zostać odrzucony i nie może nadpisać zapisanego wyniku.
    divergent_identity, divergent_receipt = write_fixture_receipt(
        store, job_key, claim, exit_code=1
    )
    assert divergent_identity["sha256"] != identity["sha256"]
    with pytest.raises(ReceiptError, match="drugi receipt różni się od zapisanego"):
        store.record_at_receipt(
            job_key,
            claim_id=str(claim["claim_id"]),
            receipt_path=str(claim["receipt_path"]),
            receipt_identity=divergent_identity,
            exit_code=1,
            stdout_sha256=str(divergent_receipt["stdout"]["sha256"]),
            stderr_sha256=str(divergent_receipt["stderr"]["sha256"]),
            now=datetime(2026, 7, 22, 11, 31, tzinfo=timezone.utc),
        )
    assert read_claim_rows(store, job_key) == after_first


def test_finalize_at_claim_retry_is_idempotent_and_detects_inconsistent_job(
    tmp_path: Path,
) -> None:
    """A-D2: powtórzony finalize nie rozlicza joba drugi raz.

    Druga próba musi wrócić przez gałąź FINALIZED (bez nowego `gate_events`),
    a niespójny terminalny job musi ją zablokować.
    """

    store = GateStore(tmp_path / "finalize-idempotent.sqlite3")
    record, claim = claimed_sealed_job(
        store, tmp_path, suffix="finalize-retry", queue_id="921"
    )
    job_key = str(record["job_key"])
    gate_id = str(record["gate_id"])
    identity, receipt = write_fixture_receipt(store, job_key, claim, exit_code=0)
    store.record_at_receipt(
        job_key,
        claim_id=str(claim["claim_id"]),
        receipt_path=str(claim["receipt_path"]),
        receipt_identity=identity,
        exit_code=0,
        stdout_sha256=str(receipt["stdout"]["sha256"]),
        stderr_sha256=str(receipt["stderr"]["sha256"]),
        now=datetime(2026, 7, 22, 10, 2, tzinfo=timezone.utc),
    )

    first = store.finalize_at_claim(
        job_key,
        claim_id=str(claim["claim_id"]),
        receipt_identity=identity,
        now=datetime(2026, 7, 22, 10, 3, tzinfo=timezone.utc),
    )
    assert first["status"] == "SUCCEEDED"
    after_first = read_claim_rows(store, job_key)
    events_after_first = count_gate_events(store, gate_id, "at_gate/run")
    assert events_after_first == 1
    gate_after_first = store.show_gate(gate_id)

    second = store.finalize_at_claim(
        job_key,
        claim_id=str(claim["claim_id"]),
        receipt_identity=identity,
        now=datetime(2026, 7, 22, 12, 0, tzinfo=timezone.utc),
    )
    assert second == first
    assert read_claim_rows(store, job_key) == after_first
    assert count_gate_events(store, gate_id, "at_gate/run") == events_after_first
    assert store.show_gate(gate_id) == gate_after_first
    assert after_first[0]["finalized_at"] == "2026-07-22T10:03:00Z"

    # Rozjazd claim↔job (cudzy writer ruszył terminalny job) musi być błędem,
    # nie cichym „już zrobione".
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE at_jobs SET exit_code = 7 WHERE job_key = ?", (job_key,)
        )
        connection.commit()
    with pytest.raises(ReceiptError, match="FINALIZED claim i terminalny job"):
        store.finalize_at_claim(
            job_key,
            claim_id=str(claim["claim_id"]),
            receipt_identity=identity,
            now=datetime(2026, 7, 22, 12, 1, tzinfo=timezone.utc),
        )
    assert read_claim_rows(store, job_key) == after_first


def test_concurrent_receipt_and_finalize_have_exactly_one_effect(
    tmp_path: Path,
) -> None:
    """A-D2: dwa równoległe runnery dają dokładnie jeden skutek, nie dwa."""

    store = GateStore(tmp_path / "receipt-concurrent.sqlite3")
    record, claim = claimed_sealed_job(
        store, tmp_path, suffix="concurrent", queue_id="922"
    )
    job_key = str(record["job_key"])
    gate_id = str(record["gate_id"])
    identity, receipt = write_fixture_receipt(store, job_key, claim, exit_code=0)

    def record_receipt(offset: int) -> str:
        barrier.wait()
        GateStore(store.db_path).record_at_receipt(
            job_key,
            claim_id=str(claim["claim_id"]),
            receipt_path=str(claim["receipt_path"]),
            receipt_identity=identity,
            exit_code=0,
            stdout_sha256=str(receipt["stdout"]["sha256"]),
            stderr_sha256=str(receipt["stderr"]["sha256"]),
            now=datetime(2026, 7, 22, 10, 2, offset, tzinfo=timezone.utc),
        )
        return "OK"

    barrier = threading.Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(record_receipt, (0, 1))) == ["OK", "OK"]
    rows = read_claim_rows(store, job_key)
    assert len(rows) == 1 and rows[0]["status"] == "RECEIPT_READY"
    # Dokładnie jeden zapis: `updated_at` pochodzi od zwycięzcy, przegrany
    # wrócił przez exact-match i nie nadpisał wiersza drugą sekundą.
    written_at = str(rows[0]["updated_at"])
    assert written_at in {"2026-07-22T10:02:00Z", "2026-07-22T10:02:01Z"}

    def finalize(offset: int) -> str:
        finalize_barrier.wait()
        GateStore(store.db_path).finalize_at_claim(
            job_key,
            claim_id=str(claim["claim_id"]),
            receipt_identity=identity,
            now=datetime(2026, 7, 22, 10, 3, offset, tzinfo=timezone.utc),
        )
        return "OK"

    finalize_barrier = threading.Barrier(2)
    with ThreadPoolExecutor(max_workers=2) as pool:
        assert list(pool.map(finalize, (0, 1))) == ["OK", "OK"]
    final_rows = read_claim_rows(store, job_key)
    assert len(final_rows) == 1 and final_rows[0]["status"] == "FINALIZED"
    assert count_gate_events(store, gate_id, "at_gate/run") == 1
    job = store.show_at_job(job_key)
    assert job["status"] == "SUCCEEDED" and int(job["exit_code"]) == 0


@pytest.mark.parametrize(
    "age_seconds, expect_alarm",
    [
        (AT_CANCEL_CLAIM_STALE_SECONDS - 1, False),
        (AT_CANCEL_CLAIM_STALE_SECONDS, False),
        (AT_CANCEL_CLAIM_STALE_SECONDS + 1, True),
    ],
)
def test_cancel_claim_stale_threshold_alarms_exactly_after_five_minutes(
    tmp_path: Path,
    age_seconds: int,
    expect_alarm: bool,
) -> None:
    """A-D4: CANCEL claim ma WŁASNY, krótki próg — nie dwunastogodzinny RUN.

    Bez tego oracle odwrócenie ternary wybierającego próg (albo podniesienie
    stałej) zostawia zawieszony CANCEL bez alarmu przez pół doby.
    """

    store = GateStore(tmp_path / f"cancel-stale-{age_seconds}.sqlite3")
    record = register_sealed_fixture(
        store, tmp_path, suffix=f"cancel-stale-{age_seconds}", queue_id="930"
    )
    job_key = str(record["job_key"])
    gate = store.show_gate(str(record["gate_id"]))
    claimed_at = datetime(2026, 7, 22, 10, 0, tzinfo=timezone.utc)
    store.begin_at_job_cancellation(
        job_key,
        "930",
        expected_gate_version=int(gate["version"]),
        actor="pytest",
        reason="logiczny cancel",
        now=claimed_at,
    )
    assert store.show_at_claim(job_key)["binding"]["operation"] == "CANCEL"

    result = store.reconcile_at_jobs(
        {"930"},
        now=claimed_at + timedelta(seconds=age_seconds),
    )
    claim_status = store.show_at_claim(job_key)["status"]
    if not expect_alarm:
        assert result["outcome_unknown"] == []
        assert claim_status == "CLAIMED"
        return
    assert result["outcome_unknown"] == [job_key]
    assert claim_status == "OUTCOME_UNKNOWN"
    alarm_reason = str(store.show_gate(str(record["gate_id"]))["alarm_reason"])
    # Literalna liczba, nie stała z modułu: podniesienie progu zaczerwieni test.
    assert "CANCEL claim ma OUTCOME_UNKNOWN" in alarm_reason
    assert "ponad 300 sekund" in alarm_reason


@pytest.mark.parametrize(
    "age_seconds, expect_alarm",
    [
        (AT_RUN_CLAIM_STALE_SECONDS, False),
        (AT_RUN_CLAIM_STALE_SECONDS + 1, True),
    ],
)
def test_run_claim_stale_threshold_alarms_exactly_after_twelve_hours(
    tmp_path: Path,
    age_seconds: int,
    expect_alarm: bool,
) -> None:
    """A-D4: granica progu RUN, dotąd testowana tylko 1 s i 13 h od progu."""

    store = GateStore(tmp_path / f"run-stale-{age_seconds}.sqlite3")
    record = register_sealed_fixture(
        store, tmp_path, suffix=f"run-stale-{age_seconds}", queue_id="931"
    )
    job_key = str(record["job_key"])
    claimed_at = datetime(2026, 7, 22, 10, 1, tzinfo=timezone.utc)
    store.claim_at_job(job_key, now=claimed_at, **sealed_claim_kwargs(record))

    result = store.reconcile_at_jobs(
        {"931"},
        now=claimed_at + timedelta(seconds=age_seconds),
    )
    if not expect_alarm:
        assert result["outcome_unknown"] == []
        assert result["running"] == [job_key]
        assert store.show_at_claim(job_key)["status"] == "CLAIMED"
        return
    assert result["outcome_unknown"] == [job_key]
    assert result["running"] == []
    assert store.show_at_claim(job_key)["status"] == "OUTCOME_UNKNOWN"
    alarm_reason = str(store.show_gate(str(record["gate_id"]))["alarm_reason"])
    assert "RUN claim ma OUTCOME_UNKNOWN" in alarm_reason
    assert "ponad 43200 sekund" in alarm_reason


def test_private_reader_enforces_owner_mode_size_and_regular_file(
    tmp_path: Path,
) -> None:
    """A-D4: guardy `read_private_bytes` poza symlinkiem katalogu pośredniego.

    Dotąd żaden test nie czerwieniał po wycięciu warunku owner/mode/size ani
    limitu bajtów; receipt z prawami 0644 przechodził bez śladu.
    """

    private = tmp_path / "private"
    private.mkdir(mode=0o700)

    good = private / "ok.bin"
    good.write_bytes(b"kanoniczny artefakt")
    good.chmod(0o600)
    data, identity = read_private_bytes(good)
    assert data == b"kanoniczny artefakt"
    assert identity["size"] == len(data)

    loose = private / "loose.bin"
    loose.write_bytes(b"za szerokie prawa")
    loose.chmod(0o644)
    with pytest.raises(ReceiptError, match="owner/mode/size"):
        read_private_bytes(loose)

    group_readable = private / "group.bin"
    group_readable.write_bytes(b"grupa czyta")
    group_readable.chmod(0o640)
    with pytest.raises(ReceiptError, match="owner/mode/size"):
        read_private_bytes(group_readable)

    # Nie-zwykły plik: katalog. (FIFO celowo pominięte — `read_private_bytes`
    # otwiera bez O_NONBLOCK, więc test na FIFO wieszałby suitę zamiast
    # udowodnić guard S_ISREG.)
    not_regular = private / "podkatalog"
    not_regular.mkdir(mode=0o700)
    with pytest.raises(ReceiptError, match="owner/mode/size"):
        read_private_bytes(not_regular)

    oversize = private / "oversize.bin"
    oversize.write_bytes(b"x" * (MAX_PRIVATE_FILE_BYTES + 1))
    oversize.chmod(0o600)
    with pytest.raises(ReceiptError, match="owner/mode/size"):
        read_private_bytes(oversize)

    absent = private / "nie-ma.bin"
    with pytest.raises(ReceiptError, match="prywatny artefakt jest niedostępny"):
        read_private_bytes(absent)

    final_symlink = private / "link.bin"
    final_symlink.symlink_to(good)
    with pytest.raises(ReceiptError, match="prywatny artefakt jest niedostępny"):
        read_private_bytes(final_symlink)

    with pytest.raises(ValidationError, match="ścieżkę absolutną"):
        read_private_bytes(Path("wzgledna.bin"))

    loose_dir = tmp_path / "loose-dir"
    loose_dir.mkdir(mode=0o755)
    inside = loose_dir / "receipt.bin"
    inside.write_bytes(b"katalog za szeroki")
    inside.chmod(0o600)
    with pytest.raises(ValidationError, match="mode 0700"):
        read_private_bytes(inside)
