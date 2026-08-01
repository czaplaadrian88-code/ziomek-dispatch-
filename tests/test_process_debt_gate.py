from __future__ import annotations

import hashlib
import importlib.util
import inspect
import json
import sqlite3
import stat
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from process_debt_gate import (  # noqa: E402
    CASConflict,
    ClaimConflict,
    GateError,
    GateStore,
    IllegalTransition,
    ValidationError,
    render_open_gates,
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
    gate_before_finish = store.show_gate("at.success")
    result = store.finish_at_job(
        "job-key-success",
        claim_id=claim["claim_id"],
        runner_token=token,
        exit_code=0,
        evidence_hash="f" * 64,
        command=command,
    )
    assert result["status"] == "SUCCEEDED"
    assert result["result_evidence_hash"] == "f" * 64
    gate = store.show_gate("at.success")
    assert gate["state"] == "WAIT_DATA"
    assert gate["evidence_hash"] == EVIDENCE
    assert gate["alarm"] is False
    for field in ("state", "version", "evidence_hash", "alarm", "blocker", "next_step"):
        assert gate[field] == gate_before_finish[field]
    assert gate["events"][-1]["from_state"] == gate["events"][-1]["to_state"]

    # Mutation ratchet: exact claim MUSI poprzedzać subprocess, a cancel claim
    # MUSI poprzedzać atrm. Przywrócenie starej kolejności czerwieni ten oracle.
    spec = importlib.util.spec_from_file_location("at_gate_claim_test", TOOLS / "at_gate.py")
    assert spec and spec.loader
    at_gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(at_gate)
    runner_source = inspect.getsource(at_gate._run_registered_inner)
    cancel_source = inspect.getsource(at_gate.cancel)
    assert runner_source.index("claim_at_job(") < runner_source.index("subprocess.run(")
    assert cancel_source.index("begin_at_job_cancellation(") < cancel_source.index("args.atrm_bin")

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

    def subprocess_spy(argv, *, capture_output, check, env):
        calls.append(list(argv))
        return subprocess.CompletedProcess(argv, 0, b"", b"")

    monkeypatch.setattr(at_gate.subprocess, "run", subprocess_spy)
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
        except ClaimConflict:
            return "CONFLICT"

    with ThreadPoolExecutor(max_workers=2) as pool:
        twin_results = list(pool.map(lambda _: execute_twin(), range(2)))
    assert sorted(twin_results) == ["CONFLICT", "RC=0"]
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
    terminal.transition(
        "at.terminal",
        "SUPERSEDED",
        expected_version=2,
        actor="pytest",
        reason="terminal oracle",
    )
    with pytest.raises(IllegalTransition):
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

    # Process failure po zmianie bramki nie przejmuje jej pól ani alarmu.
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
    moved.transition(
        "at.moved",
        "READY_FOR_REVIEW",
        expected_version=2,
        actor="pytest",
        reason="independent semantic move",
        next_step="owner-specific next",
        blocker="owner-specific blocker",
    )
    before_failure = moved.show_gate("at.moved")
    moved.finish_at_job(
        "job-moved",
        claim_id=moved_claim["claim_id"],
        runner_token=token,
        exit_code=7,
        evidence_hash="7" * 64,
        command=["/bin/false"],
    )
    after_failure = moved.show_gate("at.moved")
    for field in (
        "state",
        "version",
        "alarm",
        "alarm_reason",
        "evidence_hash",
        "blocker",
        "next_step",
    ):
        assert after_failure[field] == before_failure[field]
    assert "gate zmienił się po claimie" in after_failure["events"][-1]["reason"]

    # CLI cancel: DB-first claim blokuje runner także po awarii atrm; jawny
    # retry --already-removed finalizuje ten sam exact claim.
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

    def atrm_unavailable(command_line, **_kwargs):
        if command_line[0] == "atq":
            return subprocess.CompletedProcess(command_line, 0, "463 fixture\n", "")
        raise OSError("synthetic atrm unavailable")

    monkeypatch.setattr(at_gate, "_run_process", atrm_unavailable)
    with pytest.raises(GateError, match="CANCEL claim"):
        at_gate.cancel(cancel_args)
    cancel_claim = cancel_store.show_at_claim("job-cancel-cli")
    assert cancel_claim["binding"]["operation"] == "CANCEL"
    with pytest.raises(ClaimConflict):
        at_gate._run_registered_inner(
            SimpleNamespace(
                db=str(cancel_store.db_path),
                job_key="job-cancel-cli",
                token=token,
            ),
            ["/bin/true"],
            None,
        )
    assert calls == []
    retry_args = at_gate.build_parser().parse_args(
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
            "--already-removed",
        ]
    )
    monkeypatch.setattr(
        at_gate,
        "_run_process",
        lambda command_line, **_kwargs: subprocess.CompletedProcess(
            command_line, 0, "", ""
        ),
    )
    assert at_gate.cancel(retry_args) == 0
    assert cancel_store.show_at_job("job-cancel-cli")["status"] == "CANCELLED"

    def cancel_case(label: str, at_job_id: str):
        case_store = GateStore(tmp_path / f"cancel-{label}.sqlite3")
        gate_id = f"at.cancel-{label}"
        job_key = f"job-cancel-{label}"
        add_gate(case_store, gate_id)
        register_legacy_fixture(
            case_store,
            gate_id=gate_id,
            job_key=job_key,
            token=token,
            scheduled_for="2026-07-22T10:00:00Z",
            command=["/bin/true"],
        )
        case_store.confirm_at_job(job_key, at_job_id)
        case_args = at_gate.build_parser().parse_args(
            [
                "--db",
                str(case_store.db_path),
                "cancel",
                "--job-key",
                job_key,
                "--at-job-id",
                at_job_id,
                "--expected-gate-version",
                "2",
                "--actor",
                "pytest",
                "--reason",
                f"cancel {label} oracle",
            ]
        )
        return case_store, case_args, job_key, gate_id

    rc_store, rc_args, rc_job, _ = cancel_case("atrm-rc", "464")

    def atrm_rc_failure(command_line, **_kwargs):
        if command_line[0] == "atq":
            return subprocess.CompletedProcess(command_line, 0, "464 fixture\n", "")
        return subprocess.CompletedProcess(command_line, 1, "", "synthetic rc")

    monkeypatch.setattr(at_gate, "_run_process", atrm_rc_failure)
    with pytest.raises(GateError, match="atrm #464 rc=1"):
        at_gate.cancel(rc_args)
    assert rc_store.show_at_claim(rc_job)["binding"]["operation"] == "CANCEL"

    post_store, post_args, post_job, _ = cancel_case("postcondition", "465")
    post_calls = 0

    def postcondition_failure(command_line, **_kwargs):
        nonlocal post_calls
        post_calls += 1
        if command_line[0] == "atrm":
            return subprocess.CompletedProcess(command_line, 0, "", "")
        return subprocess.CompletedProcess(command_line, 0, "465 fixture\n", "")

    monkeypatch.setattr(at_gate, "_run_process", postcondition_failure)
    with pytest.raises(GateError, match="brak postcondition"):
        at_gate.cancel(post_args)
    assert post_calls == 3
    assert post_store.show_at_claim(post_job)["binding"]["operation"] == "CANCEL"

    cas_store, cas_args, cas_job, cas_gate = cancel_case("cas-after-atrm", "466")
    cas_calls = 0

    def cas_after_atrm(command_line, **_kwargs):
        nonlocal cas_calls
        cas_calls += 1
        if command_line[0] == "atrm":
            cas_store.note(
                cas_gate,
                expected_version=2,
                actor="independent-writer",
                reason="synthetic CAS after atrm",
            )
            return subprocess.CompletedProcess(command_line, 0, "", "")
        output = "466 fixture\n" if cas_calls == 1 else ""
        return subprocess.CompletedProcess(command_line, 0, output, "")

    monkeypatch.setattr(at_gate, "_run_process", cas_after_atrm)
    assert at_gate.cancel(cas_args) == 0
    assert cas_store.show_at_job(cas_job)["status"] == "CANCELLED"
    assert cas_store.show_at_claim(cas_job)["status"] == "FINALIZED"
    preserved_gate = cas_store.show_gate(cas_gate)
    assert preserved_gate["state"] == "WAIT_DATA"
    assert preserved_gate["version"] == 3
    assert preserved_gate["next_step"] == "Poczekaj na wykonanie zarejestrowanego at-joba"
    assert "gate zmienił się po CANCEL claimie" in preserved_gate["events"][-1]["reason"]
    assert calls == []

    # Auth2 cancel usuwa exact plik 0600 z tokenem po zdobyciu CANCEL claimu.
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
    auth2_cancel_calls = 0

    def cancel_auth2(command_line, **_kwargs):
        nonlocal auth2_cancel_calls
        auth2_cancel_calls += 1
        if command_line[0] == "atrm":
            return subprocess.CompletedProcess(command_line, 0, "", "")
        output = "467 fixture\n" if auth2_cancel_calls == 1 else ""
        return subprocess.CompletedProcess(command_line, 0, output, "")

    monkeypatch.setattr(at_gate, "_run_process", cancel_auth2)
    assert at_gate.cancel(auth2_cancel) == 0
    assert not auth2_payload.exists()
    assert auth2_store.show_at_job(auth2_job["job_key"])["status"] == "CANCELLED"


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


def test_database_is_0600_without_changing_existing_parent_mode(tmp_path: Path) -> None:
    parent = tmp_path / "shared"
    parent.mkdir(mode=0o755)
    parent.chmod(0o755)
    database = parent / "gates.sqlite3"
    add_gate(GateStore(database))
    assert stat.S_IMODE(parent.stat().st_mode) == 0o755
    assert stat.S_IMODE(database.stat().st_mode) == 0o600


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
