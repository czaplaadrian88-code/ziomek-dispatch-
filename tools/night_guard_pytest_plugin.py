"""Aggregate-only pytest result recorder used by :mod:`tools.night_guard`.

The plugin deliberately records nodeids and outcome classes only.  It never
serializes assertion text, fixture values, stdout/stderr, or test longreprs.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path


_OUTCOMES: dict[str, str] = {}
_COLLECTED: list[str] = []


def pytest_collection_finish(session) -> None:
    global _COLLECTED
    _COLLECTED = sorted(item.nodeid for item in session.items)


def pytest_runtest_logreport(report) -> None:
    nodeid = report.nodeid
    was_xfail = getattr(report, "wasxfail", None)
    if report.when == "call":
        if was_xfail and report.skipped:
            outcome = "xfailed"
        elif was_xfail and report.passed:
            outcome = "xpassed"
        elif report.failed:
            outcome = "failed"
        elif report.skipped:
            outcome = "skipped"
        else:
            outcome = "passed"
        _OUTCOMES[nodeid] = outcome
    elif report.when == "setup" and report.skipped:
        _OUTCOMES[nodeid] = "xfailed" if was_xfail else "skipped"
    elif report.failed:
        _OUTCOMES[nodeid] = "error"


def pytest_sessionfinish(session, exitstatus) -> None:
    path = os.environ.get("NIGHT_GUARD_RESULT_PATH")
    if not path:
        return
    outcomes = {nodeid: _OUTCOMES.get(nodeid, "not_run") for nodeid in _COLLECTED}
    joined = "\n".join(_COLLECTED).encode("utf-8")
    payload = {
        "schema_version": 1,
        "exitstatus": int(exitstatus),
        "nodeids": _COLLECTED,
        "nodeids_sha256": hashlib.sha256(joined).hexdigest(),
        "outcomes": outcomes,
    }
    target = Path(path)
    tmp = target.with_name(target.name + ".tmp")
    tmp.write_text(json.dumps(payload, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, target)
