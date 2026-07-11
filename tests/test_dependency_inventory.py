from __future__ import annotations

import json
import re

import pytest

from tools import dependency_inventory as di


def test_parse_and_classify(tmp_path):
    manifest = tmp_path / "requirements.txt"
    manifest.write_text("Direct_Pkg==1.2\nmissing>=3\n", encoding="utf-8")
    parsed = di.parse_requirements(manifest)
    assert [row["name"] for row in parsed] == ["direct-pkg", "missing"]
    rows = di.classify_packages({
        "direct-pkg": {"name": "direct-pkg", "version": "1.2", "license": "MIT", "license_classifiers": []},
        "child": {"name": "child", "version": "4", "license": "UNKNOWN", "license_classifiers": []},
    }, [{"requirements": parsed}])
    assert [(row["name"], row["relationship"]) for row in rows] == [("child", "transitive"), ("direct-pkg", "direct"), ("missing", "unmanaged")]
    assert {row["cve"]["status"] for row in rows} == {"UNKNOWN"}
    drift = di.manifest_drift({"direct-pkg": {"version": "1.2"}}, [{"path": "m", "requirements": parsed}])
    assert [(row["name"], row["status"]) for row in drift] == [("direct-pkg", "SATISFIED"), ("missing", "MISSING")]


def test_manifest_drift_marks_unpinned_and_out_of_range():
    rows = di.manifest_drift(
        {"a": {"version": "2"}, "b": {"version": "1"}},
        [{"path": "m", "requirements": [{"name": "a", "specifier": "*"}, {"name": "b", "specifier": ">=2"}]}],
    )
    assert [(row["name"], row["status"]) for row in rows] == [("a", "UNPINNED"), ("b", "OUT_OF_RANGE")]


def test_redaction_fails_closed():
    assert di.redact_path("/root/.openclaw/venvs/dispatch/bin/python") == "$VENV_ROOT/dispatch/bin/python"
    blocked = [
        "/root/.openclaw/workspace/dispatch_" + "state/file",
        "/root/.openclaw/workspace/.sec" + "rets/file",
        "/tmp/." + "env",
    ]
    for value in blocked:
        with pytest.raises(ValueError):
            di.ensure_safe_text(value)


def test_redaction_handles_embedded_root_path_in_pip_summary():
    message = "broken package at /root/private/tree/pkg.dist-info requires x"
    result = di.ensure_safe_text(message)
    assert "/root/" not in result
    assert result == "broken package at $REDACTED_ROOT/pkg.dist-info requires x"


def test_pip_check_redacts_embedded_root_path(monkeypatch):
    monkeypatch.setattr(
        di.subprocess,
        "run",
        lambda *args, **kwargs: di.subprocess.CompletedProcess(args[0], 1, "broken at /root/private/pkg", ""),
    )
    result = di.pip_check(di.Path("/fake/python"))
    assert result["status"] == "FAIL"
    assert result["summary"] == "broken at $REDACTED_ROOT/pkg"


def test_inventory_deterministic_and_complete(monkeypatch, tmp_path):
    manifest = tmp_path / "requirements.txt"
    manifest.write_text("alpha==1\n", encoding="utf-8")
    monkeypatch.setattr(di, "interpreter_probe", lambda _p: {"python_version": "3.12", "packages": {"alpha": {"name": "alpha", "version": "1", "license": "MIT", "license_classifiers": []}}})
    monkeypatch.setattr(di, "pip_check", lambda _p: {"status": "PASS", "returncode": 0, "summary": "ok"})
    monkeypatch.setattr(di, "import_smoke", lambda _p, _i: {"status": "PASS", "imports": {}})
    config = {
        "discovery": {"command": ["systemctl"], "unit_patterns": ["z.service"]},
        "provenance": {"config": "config.json", "regeneration_command": "generate"},
        "environments": [{"id": "z", "interpreter": "/usr/bin/python3", "manifests": [str(manifest)], "imports": []}],
        "processes": [{"unit": "z.service", "environment": "z", "exec_start": "/usr/bin/python3 -m z"}],
    }
    first = di.inventory(config, "2026-07-11T12:00:00Z", active_units=["z.service"])
    second = di.inventory(config, "2026-07-11T12:00:00Z", active_units=["z.service"])
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["global_cve_verdict"]["status"] == "UNKNOWN"
    assert first["processes"][0]["environment"] == "z"


def test_same_repo_relative_config_works_from_different_checkout_roots(monkeypatch, tmp_path):
    roots = [tmp_path / "checkout-a", tmp_path / "checkout-b"]
    for root in roots:
        (root / "requirements").mkdir(parents=True)
        (root / "requirements" / "dispatch.txt").write_text("alpha==1\n", encoding="utf-8")
    monkeypatch.setattr(di, "interpreter_probe", lambda _p: {"python_version": "3.12", "packages": {"alpha": {"name": "alpha", "version": "1", "license": "MIT", "license_classifiers": []}}})
    monkeypatch.setattr(di, "pip_check", lambda _p: {"status": "PASS", "returncode": 0, "summary": "ok"})
    monkeypatch.setattr(di, "import_smoke", lambda _p, _i: {"status": "PASS", "imports": {}})
    config = {
        "discovery": {"command": ["systemctl"], "unit_patterns": ["z.service"]},
        "provenance": {"config": "config.json", "regeneration_command": "generate"},
        "environments": [{"id": "z", "interpreter": "/usr/bin/python3", "manifests": ["repo:requirements/dispatch.txt"], "imports": []}],
        "processes": [{"unit": "z.service", "environment": "z", "exec_start": "/usr/bin/python3 -m z"}],
    }
    outputs = [di.inventory(config, "2026-07-11T12:00:00Z", active_units=["z.service"], repo_root=root) for root in roots]
    assert outputs[0] == outputs[1]
    assert outputs[0]["environments"][0]["manifests"][0]["path"] == "$DISPATCH_ROOT/requirements/dispatch.txt"


def test_versioned_config_uses_repo_locators_not_ephemeral_checkout():
    repo_root = di.Path(__file__).resolve().parents[1]
    path = repo_root / "eod_drafts/2026-07-11/audit360_artifacts/A360_DEP0_CONFIG.json"
    raw = path.read_text(encoding="utf-8")
    config = json.loads(raw)
    assert "a360_" + "dep0_wt" not in raw
    dispatch = next(row for row in config["environments"] if row["id"] == "dispatch")
    assert dispatch["manifests"] == ["repo:requirements-dispatch-venv.txt", "repo:tools/eta_calibration/requirements.txt"]


def test_missing_environment_fails_closed():
    config = {"discovery": {"command": ["systemctl"], "unit_patterns": ["x"]}, "provenance": {"config": "c", "regeneration_command": "g"}, "environments": [], "processes": [{"unit": "x", "environment": "missing", "exec_start": "x"}]}
    with pytest.raises(ValueError, match="no environment mapping"):
        di.inventory(config, "2026-07-11T12:00:00Z", active_units=["x"])


@pytest.mark.parametrize(
    ("active", "fragment"),
    [(["a.service"], "missing=['b.service']"), (["a.service", "b.service", "c.service"], "extra=['c.service']")],
)
def test_unit_coverage_fails_closed_for_missing_or_extra(active, fragment):
    config = {
        "discovery": {"unit_patterns": ["*.service"]},
        "processes": [{"unit": "a.service"}, {"unit": "b.service"}],
    }
    with pytest.raises(ValueError, match=re.escape(fragment)):
        di.validate_unit_coverage(config, active)
