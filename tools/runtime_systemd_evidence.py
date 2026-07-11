#!/usr/bin/env python3
"""Collect read-only, non-sensitive systemd/process/cgroup evidence.

The collector deliberately does not inspect unit contents, Environment,
EnvironmentFiles, /proc/*/environ, runtime data, or full process command lines.
Process cmdline is read only by the contamination detector and is reduced in
memory to a boolean tool class; neither arguments nor tokens enter the result.
"""

from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any, Callable, Iterable


SCHEMA = "a360-runtime-systemd-evidence/v1"
UNKNOWN: dict[str, str] = {"status": "UNKNOWN"}

SYSTEMD_PROPERTIES = (
    "Id",
    "LoadState",
    "ActiveState",
    "SubState",
    "UnitFileState",
    "MainPID",
    "ExecMainStartTimestamp",
    "NRestarts",
    "ControlGroup",
    "FragmentPath",
    "DropInPaths",
    "MemoryCurrent",
    "MemoryPeak",
    "MemorySwapCurrent",
    "MemoryHigh",
    "MemoryMax",
    "OOMScoreAdjust",
    "Restart",
    "RestartUSec",
    "TimeoutStartUSec",
    "TimeoutStopUSec",
)

FORBIDDEN_PROPERTY_MARKERS = ("Environment", "ExecStart", "ExecStop")
CGROUP_FILES = frozenset(
    {"memory.current", "memory.peak", "memory.swap.current", "memory.pressure", "memory.stat"}
)
PROC_SERVICE_FILES = frozenset({"exe", "status", "stat"})
PROC_CONTAMINATION_FILES = frozenset({"comm", "cmdline"})

DEFAULT_SERVICES = {
    "dispatch": (
        "dispatch-shadow.service",
        "dispatch-panel-watcher.service",
        "dispatch-gps.service",
        "dispatch-sla-tracker.service",
        "dispatch-monitor-419.service",
    ),
    "panel_api": ("courier-api.service", "nadajesz-panel.service"),
    "papu": (
        "papu-backend.service",
        "papu-backend-2.service",
        "papu-notifications-worker.service",
    ),
}


def unknown(reason: str) -> dict[str, str]:
    return {"status": "UNKNOWN", "reason": reason}


def proven(value: Any, source: str) -> dict[str, Any]:
    return {"status": "PROVEN", "value": value, "source": source}


def _safe_unit(unit: str) -> str:
    if not re.fullmatch(r"[A-Za-z0-9_.@:-]+\.service", unit):
        raise ValueError(f"unsafe service name: {unit!r}")
    return unit


def systemctl_command(unit: str, binary: str = "systemctl") -> list[str]:
    _safe_unit(unit)
    if any(any(marker in prop for marker in FORBIDDEN_PROPERTY_MARKERS) for prop in SYSTEMD_PROPERTIES):
        raise AssertionError("forbidden systemd property in allowlist")
    return [binary, "show", unit, "--no-pager", "--property=" + ",".join(SYSTEMD_PROPERTIES)]


def parse_systemctl_show(text: str) -> dict[str, str]:
    result: dict[str, str] = {}
    for raw in text.splitlines():
        if "=" not in raw:
            continue
        key, value = raw.split("=", 1)
        if key not in SYSTEMD_PROPERTIES:
            raise ValueError(f"unexpected systemd property: {key}")
        result[key] = value
    return result


def _int_value(value: str | None) -> int | None:
    if value is None or value in {"", "[not set]", "infinity"}:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def _property(props: dict[str, str], key: str) -> dict[str, Any]:
    if key not in props or props[key] == "":
        return unknown(f"systemd_property_{key}_missing")
    return proven(props[key], f"systemctl:{key}")


def _numeric_property(props: dict[str, str], key: str) -> dict[str, Any]:
    value = _int_value(props.get(key))
    if value is None:
        return unknown(f"systemd_property_{key}_not_numeric_or_unset")
    return proven(value, f"systemctl:{key}")


def _limit_property(props: dict[str, str], key: str) -> dict[str, Any]:
    raw = props.get(key)
    if raw == "infinity":
        return proven("infinity", f"systemctl:{key}")
    return _numeric_property(props, key)


def _read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _read_bytes(path: Path) -> bytes:
    return path.read_bytes()


def _readlink(path: Path) -> str:
    return os.readlink(path)


def _proc_path(proc_root: Path, pid: int, name: str, *, contamination: bool = False) -> Path:
    allowed = PROC_CONTAMINATION_FILES if contamination else PROC_SERVICE_FILES
    if name not in allowed:
        raise ValueError(f"forbidden proc field: {name}")
    return proc_root / str(pid) / name


def _cgroup_path(cgroup_root: Path, control_group: str, name: str) -> Path:
    if name not in CGROUP_FILES:
        raise ValueError(f"forbidden cgroup field: {name}")
    relative = Path(control_group.lstrip("/"))
    if not control_group.startswith("/") or ".." in relative.parts:
        raise ValueError("unsafe ControlGroup")
    return cgroup_root / relative / name


def _parse_status(text: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in text.splitlines():
        if line.startswith(("VmRSS:", "VmSwap:")):
            key, raw = line.split(":", 1)
            match = re.search(r"(\d+)\s+kB", raw)
            if match:
                values[key] = int(match.group(1)) * 1024
    return values


def _parse_proc_stat(text: str) -> tuple[int, int]:
    closing = text.rfind(")")
    if closing < 0:
        raise ValueError("malformed /proc/PID/stat")
    fields = text[closing + 2 :].split()
    # fields[0] is canonical stat field 3 (state); minflt=10, majflt=12.
    return int(fields[7]), int(fields[9])


def _parse_memory_stat(text: str) -> dict[str, int]:
    values: dict[str, int] = {}
    for line in text.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0] in {"pgfault", "pgmajfault"}:
            try:
                values[parts[0]] = int(parts[1])
            except ValueError:
                pass
    return values


def _parse_psi(text: str) -> dict[str, dict[str, float | int]]:
    result: dict[str, dict[str, float | int]] = {}
    for line in text.splitlines():
        parts = line.split()
        if not parts or parts[0] not in {"some", "full"}:
            continue
        row: dict[str, float | int] = {}
        for item in parts[1:]:
            if "=" not in item:
                continue
            key, raw = item.split("=", 1)
            try:
                row[key] = int(raw) if key == "total" else float(raw)
            except ValueError:
                continue
        result[parts[0]] = row
    return result


def _read_metric(path: Path, parser: Callable[[str], Any] | None = None) -> dict[str, Any]:
    try:
        raw = _read_text(path).strip()
    except (OSError, UnicodeError) as exc:
        return unknown(f"unavailable:{type(exc).__name__}")
    try:
        value = parser(raw) if parser else int(raw)
    except (TypeError, ValueError) as exc:
        return unknown(f"unparseable:{type(exc).__name__}")
    return proven(value, str(path))


def _precedence(props: dict[str, str]) -> dict[str, Any]:
    fragment = props.get("FragmentPath", "")
    dropins = tuple(x for x in props.get("DropInPaths", "").split() if x)
    if not fragment:
        return {
            "effective_fragment": unknown("FragmentPath_missing"),
            "effective_dropins": unknown("DropInPaths_unavailable"),
            "ordering": "UNKNOWN",
            "conflict": "UNKNOWN",
            "dead_configuration": "UNKNOWN",
        }
    ordering = "SINGLE_FRAGMENT" if not dropins else "ORDERED_BY_SYSTEMD_MANAGER"
    duplicate_names = sorted({Path(p).name for p in dropins if sum(Path(q).name == Path(p).name for q in dropins) > 1})
    policy_named = sorted(
        p for p in dropins if re.search(r"(?:memory|resource|oom)", Path(p).name, re.IGNORECASE)
    )
    return {
        "effective_fragment": proven(fragment, "systemctl:FragmentPath"),
        "effective_dropins": proven(list(dropins), "systemctl:DropInPaths"),
        "ordering": ordering,
        "duplicate_dropin_basenames": duplicate_names,
        "policy_named_dropins": policy_named,
        "precedence_risk": "POTENTIAL_OVERLAP_BY_FILENAME_ONLY" if len(policy_named) > 1 else "NO_MULTI_POLICY_FILENAME_SIGNAL",
        "conflict": "UNKNOWN_WITHOUT_CONTENT_INSPECTION" if dropins else "NOT_OBSERVED_IN_EFFECTIVE_LIST",
        "dead_configuration": "UNKNOWN_WITHOUT_FORBIDDEN_FILESYSTEM_SCAN",
    }


def collect_service(
    unit: str,
    group: str,
    *,
    proc_root: Path,
    cgroup_root: Path,
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    systemctl_binary: str = "systemctl",
) -> dict[str, Any]:
    command = systemctl_command(unit, systemctl_binary)
    try:
        completed = run(command, check=False, text=True, capture_output=True, timeout=15)
    except (OSError, subprocess.SubprocessError) as exc:
        return {"unit": unit, "group": group, "status": "UNKNOWN", "reason": f"systemctl:{type(exc).__name__}"}
    if completed.returncode != 0:
        return {"unit": unit, "group": group, "status": "UNKNOWN", "reason": "systemctl_nonzero"}
    try:
        props = parse_systemctl_show(completed.stdout)
    except ValueError as exc:
        return {"unit": unit, "group": group, "status": "UNKNOWN", "reason": f"systemctl_parse:{exc}"}

    pid = _int_value(props.get("MainPID")) or 0
    row: dict[str, Any] = {
        "unit": unit,
        "group": group,
        "status": "PROVEN" if props.get("LoadState") == "loaded" else "UNKNOWN",
        "service_state": {key: _property(props, key) for key in ("LoadState", "ActiveState", "SubState", "UnitFileState")},
        "pid": proven(pid, "systemctl:MainPID") if pid > 0 else unknown("no_running_MainPID"),
        "start": _property(props, "ExecMainStartTimestamp"),
        "nrestarts": _numeric_property(props, "NRestarts"),
        "effective_policy": {
            key: (
                _limit_property(props, key)
                if key in {"MemoryHigh", "MemoryMax"}
                else _numeric_property(props, key)
                if key in {"MemoryCurrent", "MemoryPeak", "MemorySwapCurrent", "OOMScoreAdjust"}
                else _property(props, key)
            )
            for key in ("MemoryCurrent", "MemoryPeak", "MemorySwapCurrent", "MemoryHigh", "MemoryMax", "OOMScoreAdjust", "Restart", "RestartUSec", "TimeoutStartUSec", "TimeoutStopUSec")
        },
        "precedence": _precedence(props),
    }
    if pid <= 0:
        row.update(interpreter=unknown("no_running_MainPID"), process=unknown("no_running_MainPID"), cgroup=unknown("no_running_MainPID"))
        return row

    try:
        interpreter = _readlink(_proc_path(proc_root, pid, "exe"))
        row["interpreter"] = proven(interpreter, "/proc/PID/exe")
    except OSError as exc:
        row["interpreter"] = unknown(f"proc_exe_unavailable:{type(exc).__name__}")

    status_metric = _read_metric(_proc_path(proc_root, pid, "status"), _parse_status)
    stat_metric = _read_metric(_proc_path(proc_root, pid, "stat"), _parse_proc_stat)
    row["process"] = {"memory": status_metric, "page_faults": stat_metric}

    control_group = props.get("ControlGroup", "")
    if not control_group:
        row["cgroup"] = unknown("ControlGroup_missing")
        return row
    row["cgroup"] = {
        "path": proven(control_group, "systemctl:ControlGroup"),
        "memory_current": _read_metric(_cgroup_path(cgroup_root, control_group, "memory.current")),
        "memory_peak": _read_metric(_cgroup_path(cgroup_root, control_group, "memory.peak")),
        "memory_swap_current": _read_metric(_cgroup_path(cgroup_root, control_group, "memory.swap.current")),
        "memory_pressure": _read_metric(_cgroup_path(cgroup_root, control_group, "memory.pressure"), _parse_psi),
        "page_faults": _read_metric(_cgroup_path(cgroup_root, control_group, "memory.stat"), _parse_memory_stat),
    }
    return row


def _lock_is_held(lock_path: Path, proc_locks: str) -> bool | None:
    try:
        stat = lock_path.stat()
    except FileNotFoundError:
        return False
    except OSError:
        return None
    needle = f"{os.major(stat.st_dev):02x}:{os.minor(stat.st_dev):02x}:{stat.st_ino}"
    return any(needle in line and "FLOCK" in line for line in proc_locks.splitlines())


def _scan_tool_activity(proc_root: Path) -> dict[str, Any]:
    detected: set[str] = set()
    inspected = 0
    try:
        entries: Iterable[Path] = proc_root.iterdir()
    except OSError:
        return {"status": "UNKNOWN", "reason": "proc_scan_unavailable"}
    for entry in entries:
        if not entry.name.isdigit() or int(entry.name) == os.getpid():
            continue
        try:
            comm = _read_text(_proc_path(proc_root, int(entry.name), "comm", contamination=True)).strip().lower()
            raw = _read_bytes(_proc_path(proc_root, int(entry.name), "cmdline", contamination=True))
        except OSError:
            continue
        inspected += 1
        # Never decode or emit the full command; only classify known tokens.
        lowered = raw.lower()
        if comm in {"pytest", "py.test"} or b"pytest" in lowered:
            detected.add("pytest")
        if comm in {"mutmut", "cosmic-ray"} or b"mutmut" in lowered or b"cosmic-ray" in lowered or b"mutation" in lowered:
            detected.add("mutation")
    return {"status": "PROVEN", "detected_classes": sorted(detected), "processes_inspected": inspected, "command_lines_emitted": 0}


def contamination(proc_root: Path, lock_path: Path) -> dict[str, Any]:
    reasons: list[str] = []
    unknowns: list[str] = []
    try:
        locks = _read_text(proc_root / "locks")
        held = _lock_is_held(lock_path, locks)
    except OSError:
        held = None
    if held is True:
        reasons.append("full_regression_lock_held")
    elif held is None:
        unknowns.append("full_regression_lock_state")

    tools = _scan_tool_activity(proc_root)
    if tools.get("status") != "PROVEN":
        unknowns.append("pytest_mutation_activity")
    else:
        reasons.extend(f"active_{kind}" for kind in tools["detected_classes"])

    load_evidence: dict[str, Any]
    try:
        load1 = float(_read_text(proc_root / "loadavg").split()[0])
        cpus = os.cpu_count()
        load_evidence = {"status": "PROVEN", "load1": load1, "cpu_count": cpus}
        if cpus is None:
            unknowns.append("cpu_count")
        elif load1 > cpus * 1.5:
            reasons.append("host_load_above_conservative_threshold")
    except (OSError, ValueError, IndexError):
        load_evidence = unknown("host_load_unavailable")
        unknowns.append("host_load")

    try:
        host_cpu_psi = proven(_parse_psi(_read_text(proc_root / "pressure" / "cpu")), "/proc/pressure/cpu")
    except OSError as exc:
        host_cpu_psi = unknown(f"host_cpu_psi_unavailable:{type(exc).__name__}")
        unknowns.append("host_cpu_psi")

    if reasons:
        status = "CONTAMINATED"
    elif unknowns:
        status = "UNKNOWN"
    else:
        status = "ELIGIBLE_SINGLE_SAMPLE"
    return {
        "status": status,
        "reasons": sorted(reasons),
        "unknown_checks": sorted(unknowns),
        "regression_lock_held": held if held is not None else "UNKNOWN",
        "tool_activity": tools,
        "host_load": load_evidence,
        "host_cpu_psi": host_cpu_psi,
        "representative_window": False,
    }


def snapshot(
    services: dict[str, Iterable[str]],
    timestamp: str,
    *,
    proc_root: Path = Path("/proc"),
    cgroup_root: Path = Path("/sys/fs/cgroup"),
    lock_path: Path = Path("/tmp/ziomek_full_regression.lock"),
    run: Callable[..., subprocess.CompletedProcess[str]] = subprocess.run,
    systemctl_binary: str = "systemctl",
) -> dict[str, Any]:
    rows = [
        collect_service(unit, group, proc_root=proc_root, cgroup_root=cgroup_root, run=run, systemctl_binary=systemctl_binary)
        for group in sorted(services)
        for unit in services[group]
    ]
    return {
        "schema": SCHEMA,
        "timestamp": timestamp,
        "read_only": True,
        "services": rows,
        "window_quality": contamination(proc_root, lock_path),
        "forbidden_sources_read": [],
        "limitations": [
            "DropInPaths proves manager-applied paths, not content consistency.",
            "Dead or contradictory configuration remains UNKNOWN without forbidden content/filesystem inspection.",
            "A single snapshot is never a representative window.",
        ],
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--timestamp", default=None)
    parser.add_argument("--proc-root", type=Path, default=Path("/proc"), help=argparse.SUPPRESS)
    parser.add_argument("--cgroup-root", type=Path, default=Path("/sys/fs/cgroup"), help=argparse.SUPPRESS)
    parser.add_argument("--lock-path", type=Path, default=Path("/tmp/ziomek_full_regression.lock"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    timestamp = args.timestamp or dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
    result = snapshot(DEFAULT_SERVICES, timestamp, proc_root=args.proc_root, cgroup_root=args.cgroup_root, lock_path=args.lock_path)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
