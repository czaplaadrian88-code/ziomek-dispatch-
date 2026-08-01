from __future__ import annotations

import hashlib
import inspect
import json
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
    payload_path = str((tmp_path / f"payload-{suffix}.json").absolute())
    identity = {
        "sha256": ("a" if suffix == "running" else "b") * 64,
        "device": 1,
        "inode": 2,
        "ctime_ns": 3,
        "size": 4,
    }
    binding = runner_auth_binding(
        job_key=job_key,
        gate_id=gate_id,
        scheduled_for=scheduled_for,
        command_sha256=canonical_argv_hash(command),
        payload_sha256=str(identity["sha256"]),
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
