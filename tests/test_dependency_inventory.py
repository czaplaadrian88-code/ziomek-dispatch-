from __future__ import annotations

import json

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


def test_inventory_deterministic_and_complete(monkeypatch, tmp_path):
    manifest = tmp_path / "requirements.txt"
    manifest.write_text("alpha==1\n", encoding="utf-8")
    monkeypatch.setattr(di, "interpreter_probe", lambda _p: {"python_version": "3.12", "packages": {"alpha": {"name": "alpha", "version": "1", "license": "MIT", "license_classifiers": []}}})
    monkeypatch.setattr(di, "pip_check", lambda _p: {"status": "PASS", "returncode": 0, "summary": "ok"})
    monkeypatch.setattr(di, "import_smoke", lambda _p, _i: {"status": "PASS", "imports": {}})
    config = {
        "environments": [{"id": "z", "interpreter": "/usr/bin/python3", "manifests": [str(manifest)], "imports": []}],
        "processes": [{"unit": "z.service", "environment": "z", "exec_start": "/usr/bin/python3 -m z"}],
    }
    first = di.inventory(config, "2026-07-11T12:00:00Z")
    second = di.inventory(config, "2026-07-11T12:00:00Z")
    assert json.dumps(first, sort_keys=True) == json.dumps(second, sort_keys=True)
    assert first["global_cve_verdict"]["status"] == "UNKNOWN"
    assert first["processes"][0]["environment"] == "z"


def test_missing_environment_fails_closed():
    config = {"environments": [], "processes": [{"unit": "x", "environment": "missing", "exec_start": "x"}]}
    with pytest.raises(ValueError, match="no environment mapping"):
        di.inventory(config, "2026-07-11T12:00:00Z")
