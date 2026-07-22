from __future__ import annotations

import hashlib
import json
import stat
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path

import pytest


TOOLS = Path(__file__).resolve().parents[1] / "tools"
sys.path.insert(0, str(TOOLS))

from process_debt_gate import (  # noqa: E402
    CASConflict,
    GateStore,
    IllegalTransition,
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
    store.register_at_intent(
        gate_id="at.test",
        job_key="job-key-1",
        runner_token_hash=hashlib.sha256(token.encode()).hexdigest(),
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


def test_successful_at_result_advances_to_review_and_records_hash(tmp_path: Path) -> None:
    store = GateStore(tmp_path / "gates.sqlite3")
    add_gate(store, "at.success")
    token = "one-time-runner-token"
    store.register_at_intent(
        gate_id="at.success",
        job_key="job-key-success",
        runner_token_hash=hashlib.sha256(token.encode()).hexdigest(),
        scheduled_for="2026-07-22T10:00:00Z",
        command=["/bin/true", "sensitive-argument"],
    )
    store.confirm_at_job("job-key-success", "456")
    result = store.finish_at_job(
        "job-key-success",
        runner_token=token,
        exit_code=0,
        evidence_hash="f" * 64,
    )
    assert result["status"] == "SUCCEEDED"
    assert result["result_evidence_hash"] == "f" * 64
    gate = store.show_gate("at.success")
    assert gate["state"] == "READY_FOR_REVIEW"
    assert gate["evidence_hash"] == "f" * 64
    assert gate["alarm"] is False


def test_stale_at_intent_becomes_alarm(tmp_path: Path) -> None:
    store = GateStore(tmp_path / "gates.sqlite3")
    add_gate(store, "at.stale")
    token = "stale-token"
    store.register_at_intent(
        gate_id="at.stale",
        job_key="job-key-stale",
        runner_token_hash=hashlib.sha256(token.encode()).hexdigest(),
        scheduled_for="2026-07-22T10:00:00Z",
        command=["/bin/true"],
        now=datetime(2026, 7, 21, 10, 0, tzinfo=timezone.utc),
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
    committed = (TOOLS.parent / "OPEN_GATES.md").read_text(encoding="utf-8")
    lines = committed.splitlines()
    assert lines[0] == "# OPEN GATES"
    assert "GENERATED — edycja bezcelowa" in committed
    assert "Ledger SHA-256" in committed
    assert "Otwarte: **" in committed
    assert "## Kontrola" in committed
    assert 20 <= len(lines) <= 30


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
