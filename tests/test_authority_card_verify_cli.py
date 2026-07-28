"""Hermetyczne writery operatorskie CLI karty authority."""
from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from dispatch_v2 import authority_card as AC
from dispatch_v2.tools import authority_card_verify as CLI


NOW = datetime(2026, 7, 29, 10, 0, tzinfo=timezone.utc)
OWNER_ACK_PHRASE = "ODBLOKOWUJE AUTO-CANARY 2026-07-29"


class _FixedDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is not None else NOW.replace(tzinfo=None)


def _read_rows(path):
    return [
        json.loads(line)
        for line in path.read_text(encoding="utf-8").splitlines()
    ]


def test_cli_latch_clear_refuses_budget_with_pending_verification(
    tmp_path,
    capsys,
    monkeypatch,
):
    state_path = tmp_path / "state.json"
    audit_path = tmp_path / "audit.jsonl"
    before = {
        **AC.empty_state(),
        "executed_total": 1,
        "executed_ts": [NOW.timestamp()],
        "in_flight": "OID-1",
        "pending_verification": ["OID-1"],
        "auto_off_latch": True,
        "auto_off_reason": "runner_outcome_unknown",
        "auto_off_ts": NOW.isoformat(),
    }
    AC.save_state(str(state_path), before)
    monkeypatch.setattr(CLI, "datetime", _FixedDatetime)

    rc = CLI.main([
        "--state", str(state_path),
        "--audit", str(audit_path),
        "latch-clear",
        "--reason", "owner ACK po reconcile 5b",
        "--operator", "operator-test",
        "--owner-ack-phrase", OWNER_ACK_PHRASE,
    ])

    assert rc == 1
    assert AC.load_state(str(state_path)) == before
    assert not audit_path.exists()
    response = json.loads(capsys.readouterr().out)
    assert response["cleared"] is False
    assert "verify-execution" in response["reason"]


def test_cli_latch_clear_requires_owner_ack_phrase_without_writes(
    tmp_path,
    monkeypatch,
):
    """RED J1: brak wymaganego parametru CLI kończy się przed writerem."""
    state_path = tmp_path / "state.json"
    audit_path = tmp_path / "audit.jsonl"
    before = {
        **AC.empty_state(),
        "auto_off_latch": True,
        "auto_off_reason": "runner_outcome_unknown",
        "auto_off_ts": NOW.isoformat(),
    }
    AC.save_state(str(state_path), before)
    monkeypatch.setattr(CLI, "datetime", _FixedDatetime)

    with pytest.raises(SystemExit):
        CLI.main([
            "--state", str(state_path),
            "--audit", str(audit_path),
            "latch-clear",
            "--reason", "owner ACK po verify-execution",
            "--operator", "operator-test",
        ])

    assert AC.load_state(str(state_path)) == before
    assert not audit_path.exists()


@pytest.mark.parametrize(
    "owner_ack_phrase",
    [
        "ODBLOKOWUJE AUTO-CANARY 2026-07-29.",
        "ODBLOKOWUJE AUTO-CANARY 2026-07-28",
    ],
)
def test_cli_latch_clear_refuses_wrong_or_stale_owner_ack_phrase_without_writes(
    tmp_path,
    capsys,
    monkeypatch,
    owner_ack_phrase,
):
    state_path = tmp_path / "state.json"
    audit_path = tmp_path / "audit.jsonl"
    before = {
        **AC.empty_state(),
        "auto_off_latch": True,
        "auto_off_reason": "runner_outcome_unknown",
        "auto_off_ts": NOW.isoformat(),
    }
    AC.save_state(str(state_path), before)
    monkeypatch.setattr(CLI, "datetime", _FixedDatetime)

    rc = CLI.main([
        "--state", str(state_path),
        "--audit", str(audit_path),
        "latch-clear",
        "--reason", "owner ACK po verify-execution",
        "--operator", "operator-test",
        "--owner-ack-phrase", owner_ack_phrase,
    ])

    assert rc == 1
    assert AC.load_state(str(state_path)) == before
    assert not audit_path.exists()
    response = json.loads(capsys.readouterr().out)
    assert response["cleared"] is False
    assert "owner ACK phrase mismatch" in response["reason"]


def test_cli_latch_clear_accepts_clean_reconciled_state(
    tmp_path,
    capsys,
    monkeypatch,
):
    state_path = tmp_path / "state.json"
    audit_path = tmp_path / "audit.jsonl"
    before = {
        **AC.empty_state(),
        "executed_total": 1,
        "executed_ts": [NOW.timestamp()],
        "auto_off_latch": True,
        "auto_off_reason": "runner_outcome_unknown",
        "auto_off_ts": NOW.isoformat(),
    }
    AC.save_state(str(state_path), before)
    monkeypatch.setattr(CLI, "datetime", _FixedDatetime)

    rc = CLI.main([
        "--state", str(state_path),
        "--audit", str(audit_path),
        "latch-clear",
        "--reason", "owner ACK po verify-execution",
        "--operator", "operator-test",
        "--owner-ack-phrase", OWNER_ACK_PHRASE,
    ])

    assert rc == 0
    assert AC.load_state(str(state_path)) == {
        **before,
        "auto_off_latch": False,
    }
    row = _read_rows(audit_path)[0]
    assert row["kind"] == "authority_latch_cleared"
    assert row["owner_ack_phrase"] == OWNER_ACK_PHRASE
    receipt = json.loads(capsys.readouterr().out)
    assert receipt["cleared"] is True
    assert receipt["owner_ack_phrase"] == OWNER_ACK_PHRASE


def test_cli_latch_clear_refuses_corrupt_synthetic_state_without_writes(
    tmp_path,
    capsys,
    monkeypatch,
):
    """G3: CLI nie może zamienić syntetycznego latcha w pusty stan."""
    state_path = tmp_path / "state.json"
    audit_path = tmp_path / "audit.jsonl"
    corrupt_bytes = b"{broken"
    state_path.write_bytes(corrupt_bytes)
    monkeypatch.setattr(CLI, "datetime", _FixedDatetime)

    rc = CLI.main([
        "--state", str(state_path),
        "--audit", str(audit_path),
        "latch-clear",
        "--reason", "owner ACK bez reconcile",
        "--operator", "operator-test",
        "--owner-ack-phrase", OWNER_ACK_PHRASE,
    ])

    assert rc == 1
    assert state_path.read_bytes() == corrupt_bytes
    assert not audit_path.exists()
    response = json.loads(capsys.readouterr().out)
    assert response["cleared"] is False
    assert "stan uszkodzony" in response["reason"]
    assert "reconcile" in response["reason"]


def test_cli_verify_execution_only_releases_requested_oid(tmp_path, capsys):
    state_path = tmp_path / "state.json"
    audit_path = tmp_path / "audit.jsonl"
    AC.save_state(
        str(state_path),
        {
            **AC.empty_state(),
            "executed_total": 2,
            "executed_ts": [NOW.timestamp() - 60, NOW.timestamp()],
            "in_flight": "OID-2",
            "pending_verification": ["OID-1", "OID-2"],
        },
    )

    rc = CLI.main([
        "--state", str(state_path),
        "--audit", str(audit_path),
        "verify-execution",
        "--oid", "OID-2",
        "--operator", "operator-test",
    ])

    assert rc == 0
    state = AC.load_state(str(state_path))
    assert state["executed_total"] == 2
    assert state["executed_ts"] == [NOW.timestamp() - 60, NOW.timestamp()]
    assert state["pending_verification"] == ["OID-1"]
    assert state["in_flight"] is None
    assert _read_rows(audit_path)[0]["kind"] == (
        "authority_execution_verified"
    )
    assert json.loads(capsys.readouterr().out)["verified"] is True


def test_cli_initialize_state_requires_verified_receipt_and_binds_sha(
    tmp_path,
    monkeypatch,
    capsys,
):
    state_path = tmp_path / "state.json"
    digest = "c" * 64
    monkeypatch.setattr(
        CLI,
        "_verdict",
        lambda _args: AC.CardVerdict(True, "ok", digest, {}),
    )

    rc = CLI.main([
        "--state",
        str(state_path),
        "initialize-state",
    ])

    assert rc == 0
    state = AC.load_state(str(state_path))
    assert state["initialized_for_card"] == digest
    assert state["executed_total"] == 0
    assert json.loads(capsys.readouterr().out)["initialized"] is True


def test_cli_template_prints_canonical_stop_contract(capsys):
    assert CLI.main(["template"]) == 0
    body = json.loads(capsys.readouterr().out)
    assert body["stop_contract_sha256"] == AC.EXPECTED_STOP_CONTRACT_SHA256
