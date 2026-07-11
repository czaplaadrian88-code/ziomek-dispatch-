"""Known-answer tests for the versioned, fail-closed night-guard contract."""
from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace

from dispatch_v2.tools import night_guard as ng
from dispatch_v2.tools import night_guard_pytest_plugin as plugin


def _manifest(nodeids, contracts=None):
    nodeids = sorted(nodeids)
    return {
        "schema_version": 1,
        "manifest_version": 7,
        "base_sha": "a" * 40,
        "updated_at_utc": "2026-07-11T20:00:00+00:00",
        "owner": "A360-N0",
        "reason": "known answer",
        "nodeids": nodeids,
        "nodeids_sha256": ng._nodeids_sha256(nodeids),
        "outcome_contracts": contracts or {},
    }


def test_slow_shrink_stays_red_after_multiple_nights():
    manifest = _manifest(["tests/a.py::test_a", "tests/b.py::test_b", "tests/c.py::test_c"])
    night_1 = ng.evaluate_suite_contract(manifest["nodeids"][:2], None, manifest)
    night_2 = ng.evaluate_suite_contract(manifest["nodeids"][:1], None, manifest)
    assert "tests/c.py::test_c" in night_1[0]
    assert "tests/b.py::test_b" in night_2[0] and "tests/c.py::test_c" in night_2[0]


def test_hard_error_between_green_runs_does_not_become_baseline(tmp_path, monkeypatch):
    history = tmp_path / "history.jsonl"
    history.write_text(json.dumps({
        "ts": "green", "pytest": {"baseline_eligible": True},
        "flaky_streak": {"tests/f.py::test_f": 2},
        "entropy": {"baseline_eligible": True, "flag_div": 1, "poison_live": 2},
    }) + "\n", encoding="utf-8")
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(_manifest(["tests/a.py::test_a"])), encoding="utf-8")
    monkeypatch.setattr(ng, "HISTORY", str(history))
    monkeypatch.setattr(ng, "MANIFEST", str(manifest_path))
    monkeypatch.setattr(ng, "collect_suite", lambda: ([], "COLLECT_RC_3"))
    monkeypatch.setattr(ng, "run_entropy", lambda: ({"flag_div": 1, "poison_live": 2}, None))
    assert ng.main() == 1
    entries = [json.loads(line) for line in history.read_text().splitlines()]
    assert entries[-1]["pytest"]["baseline_eligible"] is False
    assert entries[-1]["pytest"]["hard_error"] == "COLLECT_RC_3"
    assert entries[-1]["flaky_streak"] == {"tests/f.py::test_f": 2}
    assert ng._latest(entries, lambda e: e["pytest"].get("baseline_eligible"))["ts"] == "green"


def test_skip_list_change_is_red_with_constant_collection():
    nodeids = ["tests/a.py::test_a", "tests/b.py::test_b"]
    manifest = _manifest(nodeids, {"tests/b.py::test_b": ["skipped"]})
    alerts = ng.evaluate_suite_contract(nodeids, {
        "tests/a.py::test_a": "skipped", "tests/b.py::test_b": "passed"}, manifest)
    assert alerts and "SUITE-OUTCOME-DRIFT(2)" in alerts[0]


def test_xpass_and_strict_xpass_are_red():
    nodeid = "tests/a.py::test_ratchet"
    manifest = _manifest([nodeid], {nodeid: ["xfailed"]})
    assert "xpassed" in ng.evaluate_suite_contract([nodeid], {nodeid: "xpassed"}, manifest)[0]
    assert "failed" in ng.evaluate_suite_contract([nodeid], {nodeid: "failed"}, manifest)[0]


def test_replacement_nodeid_reports_both_missing_and_unexpected():
    manifest = _manifest(["tests/a.py::test_old"])
    alerts = ng.evaluate_suite_contract(["tests/a.py::test_new"], None, manifest)
    assert len(alerts) == 2
    assert "test_old" in alerts[0] and "test_new" in alerts[1]


def test_explicit_manifest_update_is_versioned_and_auditable(tmp_path):
    payload = ng._manifest_payload(
        ["tests/a.py::test_a", "tests/b.py::test_b"],
        {"tests/a.py::test_a": "passed", "tests/b.py::test_b": "xfailed"},
        owner="A360-N0", reason="add known-answer coverage", base_sha="b" * 40, version=3)
    path = tmp_path / "manifest.json"
    ng.write_manifest(payload, str(path))
    loaded, error = ng.load_manifest(str(path))
    assert error is None
    assert loaded["manifest_version"] == 3
    assert loaded["owner"] == "A360-N0"
    assert loaded["outcome_contracts"] == {"tests/b.py::test_b": ["xfailed"]}


def test_documented_clock_skip_is_explicitly_pass_or_skip():
    nodeid = "tests/test_preshift_window_penalty_2026_06_24.py::test_clock_edge"
    payload = ng._manifest_payload(
        [nodeid], {nodeid: "passed"}, owner="A360-N0", reason="clock contract",
        base_sha="c" * 40, version=1)
    assert payload["outcome_contracts"][nodeid] == ["passed", "skipped"]


def test_manifest_hash_mutation_is_rejected(tmp_path):
    payload = _manifest(["tests/a.py::test_a"])
    payload["nodeids"].append("tests/b.py::test_b")
    path = tmp_path / "manifest.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    loaded, error = ng.load_manifest(str(path))
    assert loaded is None and "MANIFEST_INVALID" in error


def test_hard_error_summary_is_not_parseable_as_green():
    parsed = ng._parse_pytest_summary("worker died before summary")
    assert parsed["summary_line"] is None


def test_pytest_plugin_child_env_starts_with_package_parent(tmp_path, monkeypatch):
    root = tmp_path / "scripts" / "dispatch_v2"
    monkeypatch.setattr(ng, "ROOT", str(root))
    monkeypatch.setenv("PYTHONPATH", "/existing/pythonpath")

    env = ng._pytest_subprocess_env(str(tmp_path / "result.json"))

    assert env["PYTHONPATH"].split(os.pathsep) == [
        str(root.parent.resolve()),
        "/existing/pythonpath",
    ]
    assert env["NIGHT_GUARD_RESULT_PATH"] == str(tmp_path / "result.json")


def test_aggregate_plugin_classifies_xfail_xpass_without_payload(tmp_path, monkeypatch):
    plugin._OUTCOMES.clear()
    monkeypatch.setattr(plugin, "_COLLECTED", ["tests/a.py::test_xf", "tests/a.py::test_xp"])
    plugin.pytest_runtest_logreport(SimpleNamespace(
        nodeid="tests/a.py::test_xf", when="call", wasxfail="owned", skipped=True,
        passed=False, failed=False, longrepr="sensitive assertion"))
    plugin.pytest_runtest_logreport(SimpleNamespace(
        nodeid="tests/a.py::test_xp", when="call", wasxfail="owned", skipped=False,
        passed=True, failed=False, longrepr="sensitive assertion"))
    result = tmp_path / "result.json"
    monkeypatch.setenv("NIGHT_GUARD_RESULT_PATH", str(result))
    plugin.pytest_sessionfinish(None, 0)
    raw = result.read_text(encoding="utf-8")
    payload = json.loads(raw)
    assert payload["outcomes"] == {
        "tests/a.py::test_xf": "xfailed", "tests/a.py::test_xp": "xpassed"}
    assert "sensitive assertion" not in raw
