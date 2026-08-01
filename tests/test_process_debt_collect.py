from __future__ import annotations

import hashlib
import inspect
import json
import os
import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1] / "tools"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "process_debt"
sys.path.insert(0, str(TOOLS))

import process_debt_collect as collector  # noqa: E402
from process_debt_gate import (  # noqa: E402
    GateStore,
    canonical_argv_hash,
    runner_auth_binding,
    runner_auth_tag,
)


CODE_SHA = "323034299fbba20a2fb33a45819e26c91f10a27a"
EVIDENCE = "20357879f33374b4ba3955ae77dd81f05bd686eaade2ce25d411a5373835630b"


def register_sealed_at_job(
    store: GateStore,
    tmp_path: Path,
    *,
    suffix: str,
    queue_id: str,
    scheduled_for: str,
) -> dict[str, object]:
    token = f"sealed-token-{suffix}"
    command = ["/bin/true", suffix]
    job_key = f"job-{suffix}"
    gate_id = f"at.collect.{suffix}"
    payload = (tmp_path / f"payload-{suffix}.json").absolute()
    payload.write_bytes(f"sealed payload {suffix}".encode("utf-8"))
    payload.chmod(0o600)
    metadata = os.stat(payload, follow_symlinks=False)
    payload_path = str(payload)
    identity = {
        "sha256": hashlib.sha256(payload.read_bytes()).hexdigest(),
        "device": metadata.st_dev,
        "inode": metadata.st_ino,
        "ctime_ns": metadata.st_ctime_ns,
        "size": metadata.st_size,
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
    store.register_at_job(
        gate_id=gate_id,
        title=f"Collector fixture {suffix}",
        owner="pytest",
        due_at="2026-07-23T00:00:00Z",
        blocker="Oczekiwanie na fixture",
        code_sha=CODE_SHA,
        evidence_hash=EVIDENCE,
        opened_at="2026-07-21T09:00:00Z",
        actor="pytest",
        job_key=job_key,
        runner_token_hash=hashlib.sha256(token.encode()).hexdigest(),
        scheduled_for=scheduled_for,
        command=command,
        runner_auth_hmac=runner_auth_tag(token, binding),
        payload_path=payload_path,
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
        "token": token,
        "command": command,
        "payload_path": payload_path,
        "identity": identity,
    }


def collector_args(tmp_path: Path) -> list[str]:
    return [
        "--repo",
        str(tmp_path),
        "--db",
        str(tmp_path / "gates.sqlite3"),
        "--flags-json",
        str(FIXTURES / "flags.json"),
        "--effective-flags",
        str(FIXTURES / "effective_flags.json"),
        "--flag-evidence",
        str(FIXTURES / "flag_evidence.json"),
        "--branches-fixture",
        str(FIXTURES / "branches.json"),
        "--bundles-fixture",
        str(FIXTURES / "bundles.json"),
        "--atq-file",
        str(FIXTURES / "atq.txt"),
        "--as-of",
        "2026-07-21T12:00:00Z",
    ]


def test_fixture_collector_proposes_without_inserting(tmp_path: Path, capsys) -> None:
    rc = collector.main(collector_args(tmp_path))
    assert rc == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["mutation"]["mode"] == "PROPOSALS_ONLY"
    assert not (tmp_path / "gates.sqlite3").exists()
    assert all(component["status"] == "OK" for component in payload["components"].values())
    kinds = [proposal["kind"] for proposal in payload["proposals"]]
    assert kinds == sorted(kinds)
    assert set(kinds) == {
        "AT_JOB_UNREGISTERED",
        "BRANCH_PATCH_EQUIVALENT",
        "BRANCH_UNMERGED",
        "BUILT_FLAG_OFF",
        "BUNDLE_PATCH_EQUIVALENT",
        "BUNDLE_TARGET_MISSING",
    }
    assert "NO_EVIDENCE" not in json.dumps(payload)


def test_apply_is_explicit_and_idempotent(tmp_path: Path, capsys) -> None:
    arguments = collector_args(tmp_path) + ["--apply"]
    assert collector.main(arguments) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["mutation"]["mode"] == "APPLY"
    assert len(first["mutation"]["added"]) == first["proposal_count"]
    records = GateStore(tmp_path / "gates.sqlite3").list_gates()
    assert len(records) == first["proposal_count"]
    assert all(record["state"] == "BUILT_OFF" for record in records)

    assert collector.main(arguments) == 0
    second = json.loads(capsys.readouterr().out)
    assert second["mutation"]["added"] == []
    assert len(second["mutation"]["skipped_existing"]) == second["proposal_count"]


def test_atq_unavailable_is_explicit_not_an_empty_queue(tmp_path: Path, capsys) -> None:
    arguments = collector_args(tmp_path)
    atq_index = arguments.index("--atq-file")
    del arguments[atq_index : atq_index + 2]
    arguments.append("--atq-unavailable")
    assert collector.main(arguments) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["components"]["atq"]["status"] == "UNAVAILABLE"
    assert "UNAVAILABLE" in payload["components"]["atq"]["detail"]


def test_atq_collector_consumes_reconcile_status_instead_of_guessing_missing(
    tmp_path: Path,
) -> None:
    store = GateStore(tmp_path / "gates.sqlite3")
    snapshot = tmp_path / "empty-atq.txt"
    snapshot.write_text("", encoding="utf-8")
    running = register_sealed_at_job(
        store,
        tmp_path,
        suffix="running",
        queue_id="731",
        scheduled_for="2026-07-21T10:00:00Z",
    )

    def collect() -> tuple[list[dict], dict]:
        return collector.collect_atq(
            store=store,
            snapshot_path=snapshot,
            force_unavailable=False,
            owner="pytest",
            default_due_at="2026-07-23T00:00:00Z",
            default_opened_at="2026-07-21T09:00:00Z",
            master_sha=CODE_SHA,
        )

    # Sam dequeue z `atq` nie ustanawia alarmu po stronie collectora.
    proposals, status = collect()
    assert proposals == []
    assert status["canonical_missing_alarm"] == 0

    launch = store.reconcile_at_jobs(
        set(), now=datetime(2026, 7, 21, 10, 0, 1, tzinfo=timezone.utc)
    )
    assert launch["launching"] == [running["job_key"]]
    assert collect()[0] == []

    store.claim_at_job(
        str(running["job_key"]),
        runner_token=str(running["token"]),
        command=running["command"],
        payload_path=str(running["payload_path"]),
        payload_identity=running["identity"],
        require_auth_version=2,
        now=datetime(2026, 7, 21, 10, 0, 2, tzinfo=timezone.utc),
    )
    claimed = store.reconcile_at_jobs(
        set(), now=datetime(2026, 7, 21, 10, 0, 3, tzinfo=timezone.utc)
    )
    assert claimed["running"] == [running["job_key"]]
    assert collect()[0] == []

    missing = register_sealed_at_job(
        store,
        tmp_path,
        suffix="missing",
        queue_id="732",
        scheduled_for="2026-07-21T09:30:00Z",
    )
    alarm = store.reconcile_at_jobs(
        set(), now=datetime(2026, 7, 21, 10, 1, tzinfo=timezone.utc)
    )
    assert alarm["alarms"] == [
        {
            "job_key": missing["job_key"],
            "gate_id": "at.collect.missing",
            "at_job_id": "732",
        }
    ]
    proposals, status = collect()
    assert proposals == []
    assert status["canonical_missing_alarm"] == 1

    # Nawet MISSING_ALARM z aktywnym CANCEL claimem nie może tworzyć drugiego
    # proposal writera; source of truth i widok istnieją już w ledgerze.
    missing_gate = store.show_gate("at.collect.missing")
    store.begin_at_job_cancellation(
        str(missing["job_key"]),
        "732",
        expected_gate_version=int(missing_gate["version"]),
        actor="pytest",
        reason="cancel in progress",
        now=datetime(2026, 7, 21, 10, 1, 1, tzinfo=timezone.utc),
    )
    canceling = store.reconcile_at_jobs(
        set(), now=datetime(2026, 7, 21, 10, 1, 2, tzinfo=timezone.utc)
    )
    assert canceling["alarms"] == []
    assert collect()[0] == []

    # Licznik jest widokiem całego kanonicznego ledgera, nie ponowną
    # klasyfikacją na podstawie obecności w chwilowym snapshotcie `atq`.
    snapshot.write_text(
        "732\tTue Jul 21 10:01:00 2026 a pytest\n",
        encoding="utf-8",
    )
    proposals, status = collect()
    assert proposals == []
    assert status["canonical_missing_alarm"] == 1

    source = inspect.getsource(collector.collect_atq)
    assert "AT_JOB_MISSING" not in source
    assert 'category="at-missing"' not in source


def test_collector_treats_cancelled_payload_as_pending_tombstone_only(
    tmp_path: Path,
    monkeypatch,
) -> None:
    store = GateStore(tmp_path / "cancel-tombstone.sqlite3")
    record = register_sealed_at_job(
        store,
        tmp_path,
        suffix="cancel-tombstone",
        queue_id="733",
        scheduled_for="2026-07-21T10:00:00Z",
    )
    payload_path = Path(str(record["payload_path"]))
    gate = store.show_gate("at.collect.cancel-tombstone")
    claim = store.begin_at_job_cancellation(
        str(record["job_key"]),
        "733",
        expected_gate_version=int(gate["version"]),
        actor="pytest",
        reason="logical tombstone",
    )
    store.cancel_at_job(
        str(record["job_key"]),
        "733",
        cancel_claim_id=str(claim["claim_id"]),
        expected_gate_version=int(gate["version"]),
        actor="pytest",
        reason="logical tombstone",
    )
    snapshot = tmp_path / "tombstone-atq.txt"
    snapshot.write_text("733\tfixture\n", encoding="utf-8")

    proposals, status = collector.collect_atq(
        store=store,
        snapshot_path=snapshot,
        force_unavailable=False,
        owner="pytest",
        default_due_at="2026-07-23T00:00:00Z",
        default_opened_at="2026-07-21T09:00:00Z",
        master_sha=CODE_SHA,
    )
    assert proposals == []
    assert status["registered_cancel_tombstones"] == 1

    # Behawioralny SHA oracle: staty i dokładny FD są nadal zgodne, zmienia się
    # wyłącznie digest zapisany w ledgerze. Tombstone musi przestać maskować ID.
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE at_jobs SET payload_sha256=? WHERE job_key=?",
            ("0" * 64, record["job_key"]),
        )
        connection.commit()
    proposals, status = collector.collect_atq(
        store=store,
        snapshot_path=snapshot,
        force_unavailable=False,
        owner="pytest",
        default_due_at="2026-07-23T00:00:00Z",
        default_opened_at="2026-07-21T09:00:00Z",
        master_sha=CODE_SHA,
    )
    assert [proposal["kind"] for proposal in proposals] == ["AT_JOB_UNREGISTERED"]
    assert status["registered_cancel_tombstones"] == 0
    with sqlite3.connect(store.db_path) as connection:
        connection.execute(
            "UPDATE at_jobs SET payload_sha256=? WHERE job_key=?",
            (record["identity"]["sha256"], record["job_key"]),
        )
        connection.commit()

    # fstat, wszystkie read i close muszą dotyczyć descriptoru zwróconego przez
    # jedno O_NOFOLLOW open — ścieżka nie może zostać ponownie otwarta do hash.
    exact_job = store.show_at_job(str(record["job_key"]))
    real_open = collector.os.open
    real_fstat = collector.os.fstat
    real_read = collector.os.read
    real_close = collector.os.close
    opened: list[int] = []
    consumed: list[tuple[str, int]] = []

    def spy_open(*args, **kwargs):
        descriptor = real_open(*args, **kwargs)
        opened.append(descriptor)
        return descriptor

    def spy_fstat(descriptor):
        consumed.append(("fstat", descriptor))
        return real_fstat(descriptor)

    def spy_read(descriptor, size):
        consumed.append(("read", descriptor))
        return real_read(descriptor, size)

    def spy_close(descriptor):
        consumed.append(("close", descriptor))
        return real_close(descriptor)

    monkeypatch.setattr(collector.os, "open", spy_open)
    monkeypatch.setattr(collector.os, "fstat", spy_fstat)
    monkeypatch.setattr(collector.os, "read", spy_read)
    monkeypatch.setattr(collector.os, "close", spy_close)
    assert collector._registered_payload_identity_matches(exact_job) is True
    assert len(opened) == 1
    assert consumed
    assert {descriptor for _operation, descriptor in consumed} == {opened[0]}
    monkeypatch.undo()

    # Podmieniony inode nie może odziedziczyć numeric ID starego tombstone.
    payload_path.unlink()
    payload_path.write_bytes(b"foreign reused queue id")
    payload_path.chmod(0o600)
    proposals, status = collector.collect_atq(
        store=store,
        snapshot_path=snapshot,
        force_unavailable=False,
        owner="pytest",
        default_due_at="2026-07-23T00:00:00Z",
        default_opened_at="2026-07-21T09:00:00Z",
        master_sha=CODE_SHA,
    )
    assert [proposal["kind"] for proposal in proposals] == ["AT_JOB_UNREGISTERED"]
    assert status["registered_cancel_tombstones"] == 0

    # Symlink i katalog pod tą samą ścieżką również nigdy nie są authority.
    payload_path.unlink()
    target = tmp_path / "foreign-target"
    target.write_bytes(b"foreign")
    payload_path.symlink_to(target)
    proposals, status = collector.collect_atq(
        store=store,
        snapshot_path=snapshot,
        force_unavailable=False,
        owner="pytest",
        default_due_at="2026-07-23T00:00:00Z",
        default_opened_at="2026-07-21T09:00:00Z",
        master_sha=CODE_SHA,
    )
    assert [proposal["kind"] for proposal in proposals] == ["AT_JOB_UNREGISTERED"]
    assert status["registered_cancel_tombstones"] == 0

    payload_path.unlink()
    payload_path.mkdir()
    proposals, status = collector.collect_atq(
        store=store,
        snapshot_path=snapshot,
        force_unavailable=False,
        owner="pytest",
        default_due_at="2026-07-23T00:00:00Z",
        default_opened_at="2026-07-21T09:00:00Z",
        master_sha=CODE_SHA,
    )
    assert [proposal["kind"] for proposal in proposals] == ["AT_JOB_UNREGISTERED"]
    assert status["registered_cancel_tombstones"] == 0

    source = inspect.getsource(collector._registered_payload_identity_matches)
    assert "O_NOFOLLOW" in source
    assert "payload_sha256" in source
    assert "os.fstat" in source
    assert "lexists" not in inspect.getsource(collector.collect_atq)
