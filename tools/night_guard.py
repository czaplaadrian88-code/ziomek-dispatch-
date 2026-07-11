#!/usr/bin/env python3
"""night_guard — P2 NOCNY STRAŻNIK REGRESJI + ENTROPII (dyrektywa Adriana 03.07.2026).

Read-only wobec silnika. Co noc:
  1. PEŁNA regresja kanonu: `<venv> -m pytest tests/ -q` (identyczna inwokacja jak baseline).
  2. Entropia: `tools/entropy_dashboard.py` — trendowane TYLKO metryki [AUTO]
     (#4 flag-rozjazdy, #7 sentinel-trucizna); [AUDIT-BASELINE] to stałe stringi, nie trend.
  3. Werdykt + append do dispatch_state/night_guard_history.jsonl (tick pisany ZAWSZE, też OK).

ALERT (exit 1 → systemd OnFailure → dispatch-onfailure-alert@ → Telegram) gdy:
  • ≥1 test CONFIRMED-FAIL (pada w pełnym biegu ORAZ w re-runie w izolacji), lub
  • pytest/collect sam się wywalił, timeoutował lub nie dał parsowalnego raportu, lub
  • dokładny zbiór nodeidów albo outcome skip/xfail/xpass odbiega od wersjonowanego manifestu, lub
  • entropia [AUTO] WZROSŁA vs poprzedni nocny run, lub
  • ten sam test FLAKY (pada w pełnym biegu, przechodzi w izolacji) ≥FLAKY_ALERT_NIGHTS nocy z rzędu.

Anty-szum (feedback_alert_signal_not_noise): pojedynczy flaky NIE alertuje — jest logowany
i liczony; znany przypadek = test_flag_doc_coverage (state-leak tylko w pełnym biegu).
Anty-kłamstwo (C9): werdykt liczony z realnego exit-code + parsowanej linii summary pytest;
rozjazd między nimi = ALERT (przyrząd nie zgaduje).
"""
from __future__ import annotations

import json
import argparse
import hashlib
import os
import re
import subprocess
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

WARSAW = ZoneInfo("Europe/Warsaw")
ROOT = os.environ.get("NIGHT_GUARD_ROOT", "/root/.openclaw/workspace/scripts/dispatch_v2")
VENV_PY = "/root/.openclaw/venvs/dispatch/bin/python"
HISTORY = os.environ.get(
    "NIGHT_GUARD_HISTORY",
    "/root/.openclaw/workspace/dispatch_state/night_guard_history.jsonl")
MANIFEST = os.environ.get(
    "NIGHT_GUARD_MANIFEST",
    os.path.join(os.path.dirname(__file__), "night_guard_suite_manifest.json"))
PYTEST_TIMEOUT_S = int(os.environ.get("NIGHT_GUARD_PYTEST_TIMEOUT_S", "3600"))
ISOLATION_RERUN_CAP = int(os.environ.get("NIGHT_GUARD_ISOLATION_CAP", "10"))
FLAKY_ALERT_NIGHTS = int(os.environ.get("NIGHT_GUARD_FLAKY_ALERT_NIGHTS", "3"))
_OUTCOMES = frozenset({"passed", "failed", "error", "skipped", "xfailed", "xpassed"})
_PASS_SKIP_PREFIXES = ("tests/test_preshift_window_penalty_2026_06_24.py::",)


def _now_iso() -> str:
    return datetime.now(WARSAW).isoformat(timespec="seconds")


def _parse_pytest_summary(text: str) -> dict:
    """Ostatnia linia '=== N failed, M passed, ... in Xs ===' → dict liczników."""
    out = {"failed": 0, "passed": 0, "skipped": 0, "xfailed": 0, "xpassed": 0,
           "errors": 0, "duration_s": None, "summary_line": None}
    for line in reversed(text.splitlines()):
        if " in " not in line or ("passed" not in line and "failed" not in line
                                  and "error" not in line):
            continue
        out["summary_line"] = line.strip().strip("=").strip()
        for count, key in re.findall(r"(\d+) (failed|passed|skipped|xfailed|xpassed|error)s?",
                                     line):
            out[{"error": "errors"}.get(key, key)] = int(count)
        m = re.search(r"in ([0-9.]+)s", line)
        if m:
            out["duration_s"] = float(m.group(1))
        break
    return out


def _failed_test_ids(text: str) -> list[str]:
    ids = []
    for line in text.splitlines():
        m = re.match(r"(?:FAILED|ERROR) (\S+?)(?:\s+-.*)?$", line.strip())
        if m:
            ids.append(m.group(1))
    return sorted(set(ids))


def _nodeids_sha256(nodeids: list[str]) -> str:
    return hashlib.sha256("\n".join(nodeids).encode("utf-8")).hexdigest()


def collect_suite() -> tuple[list[str], str | None]:
    """Collect the exact suite before execution; partial collection is RED."""
    try:
        p = subprocess.run(
            [VENV_PY, "-m", "pytest", "tests/", "--collect-only", "-q"],
            cwd=ROOT, capture_output=True, text=True, timeout=600)
    except subprocess.TimeoutExpired:
        return [], "COLLECT_TIMEOUT"
    nodeids = sorted({line.strip() for line in p.stdout.splitlines()
                      if line.strip().startswith("tests/") and "::" in line})
    if p.returncode != 0:
        return nodeids, f"COLLECT_RC_{p.returncode}"
    if not nodeids:
        return [], "COLLECT_EMPTY"
    return nodeids, None


def _load_json(path: str) -> dict:
    with open(path, encoding="utf-8") as f:
        value = json.load(f)
    if not isinstance(value, dict):
        raise ValueError("root is not an object")
    return value


def load_manifest(path: str | None = None) -> tuple[dict | None, str | None]:
    """Load and strictly validate the versioned suite contract."""
    path = path or MANIFEST
    try:
        manifest = _load_json(path)
        if manifest.get("schema_version") != 1:
            raise ValueError("unsupported schema_version")
        if not isinstance(manifest.get("manifest_version"), int) or manifest["manifest_version"] < 1:
            raise ValueError("invalid manifest_version")
        nodeids = manifest.get("nodeids")
        if not isinstance(nodeids, list) or not nodeids or nodeids != sorted(set(nodeids)):
            raise ValueError("nodeids must be non-empty, sorted and unique")
        if manifest.get("nodeids_sha256") != _nodeids_sha256(nodeids):
            raise ValueError("nodeids_sha256 mismatch")
        contracts = manifest.get("outcome_contracts")
        if not isinstance(contracts, dict):
            raise ValueError("outcome_contracts is not an object")
        for nodeid, allowed in contracts.items():
            if nodeid not in nodeids or not isinstance(allowed, list) or not allowed:
                raise ValueError(f"invalid outcome contract for {nodeid}")
            if len(allowed) != len(set(allowed)) or not set(allowed) <= _OUTCOMES:
                raise ValueError(f"invalid allowed outcomes for {nodeid}")
        for key in ("owner", "reason", "updated_at_utc", "base_sha"):
            if not isinstance(manifest.get(key), str) or not manifest[key].strip():
                raise ValueError(f"missing audit field {key}")
        return manifest, None
    except FileNotFoundError:
        return None, "MANIFEST_MISSING"
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return None, f"MANIFEST_INVALID:{type(exc).__name__}:{exc}"


def evaluate_suite_contract(
    collected: list[str], outcomes: dict[str, str] | None, manifest: dict,
) -> list[str]:
    """Return fail-closed contract violations, always named by exact nodeid."""
    alerts: list[str] = []
    expected = set(manifest["nodeids"])
    actual = set(collected)
    missing = sorted(expected - actual)
    unexpected = sorted(actual - expected)
    if missing:
        alerts.append(f"SUITE-CONTRACT-MISSING({len(missing)}): {missing}")
    if unexpected:
        alerts.append(f"SUITE-CONTRACT-UNEXPECTED({len(unexpected)}): {unexpected}")
    if outcomes is not None and not missing and not unexpected:
        contracts = manifest["outcome_contracts"]
        mismatches = []
        for nodeid in manifest["nodeids"]:
            actual_outcome = outcomes.get(nodeid, "not_run")
            allowed = contracts.get(nodeid, ["passed"])
            if actual_outcome not in allowed:
                mismatches.append({"nodeid": nodeid, "actual": actual_outcome,
                                   "allowed": allowed})
        if mismatches:
            alerts.append(f"SUITE-OUTCOME-DRIFT({len(mismatches)}): {mismatches}")
    return alerts


def run_pytest() -> tuple[dict, list[str], str | None, dict | None]:
    """Full run with an aggregate-only per-nodeid outcome report."""
    fd, result_path = tempfile.mkstemp(prefix="night-guard-", suffix=".json")
    os.close(fd)
    os.unlink(result_path)
    env = dict(os.environ)
    env["NIGHT_GUARD_RESULT_PATH"] = result_path
    try:
        p = subprocess.run([VENV_PY, "-m", "pytest", "tests/", "-q", "-p",
                            "dispatch_v2.tools.night_guard_pytest_plugin"],
                           cwd=ROOT, capture_output=True, text=True,
                           timeout=PYTEST_TIMEOUT_S, env=env)
    except subprocess.TimeoutExpired:
        try:
            os.unlink(result_path)
        except FileNotFoundError:
            pass
        return {}, [], f"PYTEST_TIMEOUT_{PYTEST_TIMEOUT_S}s", None
    text = p.stdout + "\n" + p.stderr
    summary = _parse_pytest_summary(text)
    failed = _failed_test_ids(text)
    if summary["summary_line"] is None:
        try:
            os.unlink(result_path)
        except FileNotFoundError:
            pass
        return summary, failed, f"PYTEST_SUMMARY_MISSING_RC_{p.returncode}", None
    # anty-kłamstwo: exit-code i summary muszą się zgadzać
    saw_fail = bool(failed) or summary["failed"] > 0 or summary["errors"] > 0
    if (p.returncode == 0) == saw_fail:
        try:
            os.unlink(result_path)
        except FileNotFoundError:
            pass
        return summary, failed, f"PYTEST_RC_SUMMARY_MISMATCH_RC_{p.returncode}", None
    try:
        report = _load_json(result_path)
        if report.get("schema_version") != 1:
            raise ValueError("bad result schema")
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return summary, failed, f"PYTEST_RESULT_INVALID:{type(exc).__name__}", None
    finally:
        try:
            os.unlink(result_path)
        except FileNotFoundError:
            pass
    return summary, failed, None, report


def rerun_isolated(test_ids: list[str]) -> tuple[list[str], list[str]]:
    """Każdy failed test w izolacji → (confirmed, flaky)."""
    confirmed, flaky = [], []
    for tid in test_ids[:ISOLATION_RERUN_CAP]:
        try:
            p = subprocess.run([VENV_PY, "-m", "pytest", tid, "-q"],
                               cwd=ROOT, capture_output=True, text=True, timeout=600)
            (flaky if p.returncode == 0 else confirmed).append(tid)
        except subprocess.TimeoutExpired:
            confirmed.append(tid)
    # ponad cap = nie weryfikowane w izolacji → traktuj jako confirmed (fail-loud)
    confirmed.extend(test_ids[ISOLATION_RERUN_CAP:])
    return confirmed, flaky


def run_entropy() -> tuple[dict, str | None]:
    """entropy_dashboard → metryki AUTO. AUDIT-BASELINE świadomie NIE trendowane."""
    try:
        p = subprocess.run([VENV_PY, os.path.join(ROOT, "tools", "entropy_dashboard.py")],
                           cwd=os.path.dirname(ROOT), capture_output=True, text=True,
                           timeout=300)
    except subprocess.TimeoutExpired:
        return {}, "entropy_dashboard TIMEOUT"
    if p.returncode != 0:
        return {}, f"entropy_dashboard rc={p.returncode}: {p.stderr[-300:]}"
    text = p.stdout
    out: dict = {}
    m = re.search(r"#4 flag-rozjazdy \[AUTO\]: (\d+)", text)
    out["flag_div"] = int(m.group(1)) if m else None
    m = re.search(r"#7 sentinel-trucizna żywy silnik \[AUTO-oracle\]: (\d+)", text)
    out["poison_live"] = int(m.group(1)) if m else None
    m = re.search(r"#7 instrument/harness \(osobno\): (\d+)", text)
    out["poison_instr"] = int(m.group(1)) if m else None
    if out["poison_live"] is None:
        return out, "entropy_dashboard: nie sparsowano #7 (format się zmienił?)"
    return out, None


def load_history() -> tuple[list[dict], str | None]:
    try:
        with open(HISTORY, encoding="utf-8") as f:
            lines = [ln for ln in f if ln.strip()]
        entries = [json.loads(line) for line in lines]
        if not all(isinstance(entry, dict) for entry in entries):
            raise ValueError("history entry is not an object")
        return entries, None
    except FileNotFoundError:
        return [], None
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        return [], f"HISTORY_INVALID:{type(exc).__name__}:{exc}"


def _latest(entries: list[dict], predicate) -> dict | None:
    return next((entry for entry in reversed(entries) if predicate(entry)), None)


def append_history(entry: dict) -> None:
    os.makedirs(os.path.dirname(HISTORY), exist_ok=True)
    tmp = HISTORY + ".tmp-ng"
    prev = ""
    if os.path.exists(HISTORY):
        with open(HISTORY, encoding="utf-8") as f:
            prev = f.read()
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(prev + json.dumps(entry, ensure_ascii=False) + "\n")
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, HISTORY)


def _manifest_payload(nodeids: list[str], outcomes: dict[str, str], *, owner: str,
                      reason: str, base_sha: str, version: int) -> dict:
    contracts = {nodeid: [outcome] for nodeid, outcome in sorted(outcomes.items())
                 if outcome != "passed"}
    for nodeid in nodeids:
        if nodeid.startswith(_PASS_SKIP_PREFIXES):
            contracts[nodeid] = ["passed", "skipped"]
    return {
        "schema_version": 1,
        "manifest_version": version,
        "base_sha": base_sha,
        "updated_at_utc": datetime.now(ZoneInfo("UTC")).isoformat(timespec="seconds"),
        "owner": owner,
        "reason": reason,
        "nodeids_sha256": _nodeids_sha256(nodeids),
        "nodeids": nodeids,
        "outcome_contracts": contracts,
    }


def write_manifest(payload: dict, path: str | None = None) -> None:
    path = path or MANIFEST
    target = Path(path)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    os.replace(tmp, target)


def update_manifest(owner: str, reason: str, base_sha: str) -> int:
    if not owner.strip() or not reason.strip() or not re.fullmatch(r"[0-9a-f]{40}", base_sha):
        print("owner, reason and full 40-char base SHA are required", file=sys.stderr)
        return 2
    head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True,
                          text=True, timeout=30)
    if head.returncode != 0 or head.stdout.strip() != base_sha:
        print("refusing manifest update: base SHA does not equal current HEAD", file=sys.stderr)
        return 1
    collected, collect_err = collect_suite()
    if collect_err:
        print(f"refusing manifest update: {collect_err}", file=sys.stderr)
        return 1
    summary, failed, hard_err, report = run_pytest()
    if hard_err or failed or report is None:
        print(f"refusing manifest update: hard={hard_err} failed={failed}", file=sys.stderr)
        return 1
    outcomes = report.get("outcomes")
    if report.get("nodeids") != collected or not isinstance(outcomes, dict):
        print("refusing manifest update: collect/run nodeids differ", file=sys.stderr)
        return 1
    if any(outcome in {"failed", "error", "xpassed", "not_run"}
           for outcome in outcomes.values()):
        print("refusing manifest update: suite has failed/error/xpassed/not_run", file=sys.stderr)
        return 1
    previous, previous_err = load_manifest()
    if previous_err and previous_err != "MANIFEST_MISSING":
        print(f"refusing manifest update: existing {previous_err}", file=sys.stderr)
        return 1
    version = (previous or {}).get("manifest_version", 0) + 1
    write_manifest(_manifest_payload(collected, outcomes, owner=owner, reason=reason,
                                     base_sha=base_sha, version=version))
    print(f"manifest updated: version={version} nodeids={len(collected)} "
          f"sha256={_nodeids_sha256(collected)} summary={summary['summary_line']}")
    return 0


def main() -> int:
    alerts: list[str] = []
    notes: list[str] = []
    history, history_err = load_history()
    if history_err:
        alerts.append(f"HISTORY-HARD: {history_err}")
    prev = history[-1] if history else None
    prev_valid_pytest = _latest(
        history, lambda e: bool((e.get("pytest") or {}).get("baseline_eligible")))
    prev_valid_entropy = _latest(
        history, lambda e: bool((e.get("entropy") or {}).get("baseline_eligible")))

    manifest, manifest_err = load_manifest()
    if manifest_err:
        alerts.append(f"SUITE-MANIFEST-HARD: {manifest_err}")
    collected, collect_err = collect_suite()
    if collect_err:
        alerts.append(f"PYTEST-HARD: {collect_err}")
    elif manifest is not None:
        alerts.extend(evaluate_suite_contract(collected, None, manifest))

    summary: dict = {}
    failed_ids: list[str] = []
    report: dict | None = None
    hard_err: str | None = collect_err
    if not collect_err:
        summary, failed_ids, hard_err, report = run_pytest()
        if hard_err:
            alerts.append(f"PYTEST-HARD: {hard_err}")
        elif manifest is not None and report is not None:
            alerts.extend(evaluate_suite_contract(report["nodeids"], report["outcomes"], manifest))
    confirmed, flaky = ([], [])
    if failed_ids:
        confirmed, flaky = rerun_isolated(failed_ids)
    if confirmed:
        alerts.append(f"REGRESJA: {len(confirmed)} confirmed-fail: {confirmed}")

    # Count is diagnostic only. Exact nodeids are the fail-closed denominator.
    total = sum(summary.get(k, 0) for k in
                ("failed", "passed", "skipped", "xfailed", "xpassed", "errors"))

    # flaky N nocy z rzędu
    prev_flaky_streak = (prev or prev_valid_pytest or {}).get("flaky_streak", {})
    flaky_streak = (dict(prev_flaky_streak) if hard_err else
                    {t: prev_flaky_streak.get(t, 0) + 1 for t in flaky})
    persistent = [t for t, n in flaky_streak.items() if n >= FLAKY_ALERT_NIGHTS]
    if persistent:
        alerts.append(f"FLAKY≥{FLAKY_ALERT_NIGHTS}nocy: {persistent}")
    elif flaky:
        notes.append(f"flaky (pass w izolacji, bez alertu): {flaky}")

    entropy, ent_err = run_entropy()
    if ent_err:
        alerts.append(f"ENTROPY-TOOL: {ent_err}")
    if prev_valid_entropy:
        for key, label in (("flag_div", "#4 flag-rozjazdy"),
                           ("poison_live", "#7 sentinel-trucizna(silnik)")):
            cur, old = entropy.get(key), (prev_valid_entropy.get("entropy") or {}).get(key)
            if cur is not None and old is not None and cur > old:
                alerts.append(f"ENTROPIA ROŚNIE: {label} {old}→{cur}")

    entry = {
        "ts": _now_iso(),
        "history_schema_version": 2,
        "suite_contract": {
            "manifest_version": (manifest or {}).get("manifest_version"),
            "nodeids_sha256": (manifest or {}).get("nodeids_sha256"),
            "collected_sha256": _nodeids_sha256(collected) if collected else None,
            "contract_ok": not any(a.startswith("SUITE-") for a in alerts),
        },
        "pytest": {**summary, "total_collected": total or None,
                   "failed_ids": failed_ids, "confirmed_failed": confirmed,
                   "flaky": flaky, "hard_error": hard_err,
                   "baseline_eligible": hard_err is None and report is not None},
        "flaky_streak": flaky_streak,
        "entropy": {**entropy, "baseline_eligible": ent_err is None},
        "alerts": alerts,
        "notes": notes,
        "verdict": "ALERT" if alerts else "OK",
    }
    append_history(entry)

    print(f"[night_guard {entry['ts']}] verdict={entry['verdict']} "
          f"pytest={summary.get('summary_line')} entropy={entropy}")
    for a in alerts:
        print(f"  ALERT: {a}")
    for n in notes:
        print(f"  note: {n}")
    return 1 if alerts else 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--update-manifest", action="store_true")
    parser.add_argument("--owner")
    parser.add_argument("--reason")
    parser.add_argument("--base-sha")
    args = parser.parse_args()
    if args.update_manifest:
        sys.exit(update_manifest(args.owner or "", args.reason or "", args.base_sha or ""))
    sys.exit(main())
