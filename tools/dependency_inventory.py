#!/usr/bin/env python3
"""Deterministic dependency inventory built only from local metadata."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from pathlib import Path
from typing import Any, Iterable

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version


SCHEMA = "a360-dependency-inventory/v1"
UNKNOWN = {"status": "UNKNOWN", "source": "no_local_validated_feed", "confidence": "none"}
BLOCKED_MARKERS = (
    "/root/.openclaw/workspace/dispatch_" + "state",
    "/root/.openclaw/workspace/.sec" + "rets",
    "." + "env",
)
PATH_ALIASES = (
    ("/root/.openclaw/workspace/scripts/courier_api", "$COURIER_API_ROOT"),
    ("/root/.openclaw/workspace/scripts/dispatch_v2", "$DISPATCH_ROOT"),
    ("/root/a360_dep0_wt/dispatch_v2", "$DISPATCH_ROOT"),
    ("/root/.openclaw/venvs", "$VENV_ROOT"),
)


def canonical_name(value: str) -> str:
    return re.sub(r"[-_.]+", "-", value).lower()


def redact_path(value: str) -> str:
    for prefix, alias in PATH_ALIASES:
        if value == prefix or value.startswith(prefix + "/"):
            return alias + value[len(prefix) :]
    if value.startswith("/root/"):
        return "$REDACTED_ROOT/" + Path(value).name
    return value


def ensure_safe_text(value: str) -> str:
    lowered = value.lower()
    if any(marker.lower() in lowered for marker in BLOCKED_MARKERS):
        raise ValueError("sensitive or runtime value rejected")
    return redact_path(value)


def parse_requirements(path: Path) -> list[dict[str, str]]:
    items: list[dict[str, str]] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not line or line.startswith(("-r", "--requirement", "-c", "--constraint")):
            continue
        match = re.match(r"^([A-Za-z0-9_.-]+)(?:\[([^]]+)\])?\s*(.*)$", line)
        if not match:
            raise ValueError(f"unsupported requirement syntax in {path.name}")
        name, extras, specifier = match.groups()
        items.append({"name": canonical_name(name), "specifier": specifier.strip() or "*", "extras": extras or ""})
    return sorted(items, key=lambda item: item["name"])


def interpreter_probe(interpreter: Path) -> dict[str, Any]:
    code = (
        "import importlib.metadata as m,json,platform\n"
        "r=[]\n"
        "for d in m.distributions():\n"
        " n=d.metadata.get('Name')\n"
        " if n:r.append({'name':n,'version':d.version,'license':d.metadata.get('License') or 'UNKNOWN',"
        "'license_classifiers':sorted(x for x in (d.metadata.get_all('Classifier') or []) if x.startswith('License ::'))})\n"
        "print(json.dumps({'python_version':platform.python_version(),'packages':r},sort_keys=True))\n"
    )
    proc = subprocess.run([str(interpreter), "-I", "-c", code], check=True, text=True, capture_output=True, timeout=60)
    payload = json.loads(proc.stdout)
    packages = {}
    for item in payload["packages"]:
        name = canonical_name(item["name"])
        packages[name] = {
            "name": name,
            "version": str(item["version"]),
            "license": str(item.get("license") or "UNKNOWN").strip() or "UNKNOWN",
            "license_classifiers": item.get("license_classifiers", []),
        }
    return {"python_version": payload["python_version"], "packages": packages}


def pip_check(interpreter: Path) -> dict[str, Any]:
    proc = subprocess.run([str(interpreter), "-m", "pip", "check"], check=False, text=True, capture_output=True, timeout=120)
    output = "\n".join(x.strip() for x in (proc.stdout, proc.stderr) if x.strip())
    return {"status": "PASS" if proc.returncode == 0 else "FAIL", "returncode": proc.returncode, "summary": ensure_safe_text(output or "no output")}


def import_smoke(interpreter: Path, imports: Iterable[str]) -> dict[str, Any]:
    names = sorted(set(imports))
    code = (
        "import importlib,json\nr={}\nfor n in " + repr(names) + ":\n"
        " try:importlib.import_module(n);r[n]='PASS'\n"
        " except Exception as e:r[n]='FAIL:'+type(e).__name__\n"
        "print(json.dumps(r,sort_keys=True))\n"
    )
    proc = subprocess.run([str(interpreter), "-I", "-c", code], check=True, text=True, capture_output=True, timeout=120)
    results = json.loads(proc.stdout)
    return {"status": "PASS" if all(v == "PASS" for v in results.values()) else "FAIL", "imports": results}


def classify_packages(runtime: dict[str, dict[str, Any]], manifests: list[dict[str, Any]]) -> list[dict[str, Any]]:
    direct = {item["name"] for manifest in manifests for item in manifest["requirements"]}
    rows = []
    for name in sorted(runtime):
        item = dict(runtime[name])
        item.update(relationship="direct" if name in direct else "transitive", cve=dict(UNKNOWN), eol=dict(UNKNOWN))
        rows.append(item)
    for name in sorted(direct - set(runtime)):
        rows.append({"name": name, "version": "NOT_INSTALLED", "license": "UNKNOWN", "license_classifiers": [], "relationship": "unmanaged", "cve": dict(UNKNOWN), "eol": dict(UNKNOWN)})
    return rows


def manifest_drift(runtime: dict[str, dict[str, Any]], manifests: list[dict[str, Any]]) -> list[dict[str, str]]:
    findings = []
    for manifest in manifests:
        for requirement in manifest["requirements"]:
            name, specifier = requirement["name"], requirement["specifier"]
            installed = runtime.get(name, {}).get("version", "NOT_INSTALLED")
            if installed == "NOT_INSTALLED":
                status = "MISSING"
            elif specifier == "*":
                status = "UNPINNED"
            else:
                try:
                    status = "SATISFIED" if Version(installed) in SpecifierSet(specifier) else "OUT_OF_RANGE"
                except (InvalidSpecifier, InvalidVersion):
                    status = "UNKNOWN"
            findings.append({"manifest": manifest["path"], "name": name, "specifier": specifier, "runtime_version": installed, "status": status})
    return sorted(findings, key=lambda row: (row["manifest"], row["name"]))


def inventory(config: dict[str, Any], timestamp: str) -> dict[str, Any]:
    environments = []
    for source in sorted(config["environments"], key=lambda row: row["id"]):
        interpreter = Path(source["interpreter"])
        manifests = []
        for manifest_path in sorted(source["manifests"]):
            path = Path(manifest_path)
            manifests.append({"path": ensure_safe_text(str(path)), "sha256": hashlib.sha256(path.read_bytes()).hexdigest(), "requirements": parse_requirements(path)})
        probe = interpreter_probe(interpreter)
        environments.append({
            "id": source["id"], "interpreter": ensure_safe_text(str(interpreter)), "python_version": probe["python_version"],
            "manifests": manifests, "pip_check": pip_check(interpreter), "import_smoke": import_smoke(interpreter, source.get("imports", [])),
            "manifest_drift": manifest_drift(probe["packages"], manifests), "packages": classify_packages(probe["packages"], manifests),
        })
    known = {row["id"] for row in environments}
    processes = []
    for row in sorted(config["processes"], key=lambda item: item["unit"]):
        if row["environment"] not in known:
            raise ValueError(f"process {row['unit']} has no environment mapping")
        processes.append({"unit": row["unit"], "environment": row["environment"], "exec_start": ensure_safe_text(row["exec_start"])})
    result = {
        "schema": SCHEMA, "generated_at": timestamp, "generator": "tools/dependency_inventory.py", "network_cve_feed_used": False,
        "global_cve_verdict": dict(UNKNOWN), "global_eol_verdict": dict(UNKNOWN), "environments": environments, "processes": processes,
    }
    ensure_safe_text(json.dumps(result, sort_keys=True))
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timestamp", required=True)
    args = parser.parse_args()
    result = inventory(json.loads(args.config.read_text(encoding="utf-8")), args.timestamp)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
