from __future__ import annotations

import json
import sys
from pathlib import Path


TOOLS = Path(__file__).resolve().parents[1] / "tools"
FIXTURES = Path(__file__).resolve().parent / "fixtures" / "process_debt"
sys.path.insert(0, str(TOOLS))

import process_debt_collect as collector  # noqa: E402
from process_debt_gate import GateStore  # noqa: E402


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
