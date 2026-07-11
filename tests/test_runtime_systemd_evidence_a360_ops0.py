from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

import pytest

from dispatch_v2.tools import runtime_systemd_evidence as evidence


def _show(unit: str, pid: int, group: str, dropins: str = "") -> str:
    values = {
        "Id": unit, "LoadState": "loaded", "ActiveState": "active", "SubState": "running",
        "UnitFileState": "enabled", "MainPID": str(pid),
        "ExecMainStartTimestamp": "Sat 2026-07-11 10:27:21 UTC", "NRestarts": "0",
        "ControlGroup": group, "FragmentPath": f"/systemd/{unit}", "DropInPaths": dropins,
        "MemoryCurrent": "1048576", "MemoryPeak": "2097152", "MemorySwapCurrent": "4096",
        "MemoryHigh": "536870912", "MemoryMax": "1073741824", "OOMScoreAdjust": "-100",
        "Restart": "on-failure", "RestartUSec": "5s", "TimeoutStartUSec": "1min 30s",
        "TimeoutStopUSec": "30s",
    }
    return "\n".join(f"{key}={values[key]}" for key in evidence.SYSTEMD_PROPERTIES) + "\n"


@pytest.fixture
def fake_kernel(tmp_path: Path) -> tuple[Path, Path, Path]:
    proc, cgroup = tmp_path / "proc", tmp_path / "cgroup"
    proc.mkdir(); cgroup.mkdir()
    (proc / "locks").write_text("", encoding="utf-8")
    (proc / "loadavg").write_text("0.10 0.20 0.30 1/100 42\n", encoding="utf-8")
    (proc / "pressure").mkdir()
    (proc / "pressure" / "cpu").write_text("some avg10=0.00 avg60=0.01 avg300=0.02 total=123\n", encoding="utf-8")
    return proc, cgroup, tmp_path / "ziomek_full_regression.lock"


def _add_process(proc: Path, cgroup: Path, pid: int, group_name: str) -> None:
    process = proc / str(pid); process.mkdir()
    (process / "exe").symlink_to("/venv/bin/python")
    (process / "status").write_text("Name:\tpython\nVmRSS:\t1234 kB\nVmSwap:\t7 kB\n", encoding="utf-8")
    fields = ["R"] + ["0"] * 49; fields[7] = "111"; fields[9] = "3"
    (process / "stat").write_text(f"{pid} (python worker) " + " ".join(fields) + "\n", encoding="utf-8")
    (process / "comm").write_text("python\n", encoding="utf-8")
    (process / "cmdline").write_bytes(b"python\x00-m\x00dispatch.worker\x00")
    group = cgroup / group_name.lstrip("/"); group.mkdir(parents=True)
    (group / "memory.current").write_text("1200000\n", encoding="utf-8")
    (group / "memory.peak").write_text("2400000\n", encoding="utf-8")
    (group / "memory.swap.current").write_text("8192\n", encoding="utf-8")
    (group / "memory.pressure").write_text(
        "some avg10=0.10 avg60=0.20 avg300=0.30 total=400\nfull avg10=0.00 avg60=0.01 avg300=0.02 total=5\n",
        encoding="utf-8",
    )
    (group / "memory.stat").write_text("anon 100\npgfault 222\npgmajfault 4\n", encoding="utf-8")


def test_fixture_collects_allowlisted_systemctl_proc_and_cgroup(fake_kernel: tuple[Path, Path, Path]) -> None:
    proc, cgroup, lock = fake_kernel
    _add_process(proc, cgroup, 101, "/system.slice/dispatch-shadow.service")
    calls: list[list[str]] = []

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        calls.append(command)
        return subprocess.CompletedProcess(command, 0, _show(
            "dispatch-shadow.service", 101, "/system.slice/dispatch-shadow.service",
            "/run/systemd/system/dispatch-shadow.service.d/10-runtime.conf /systemd/dropins/90-ops.conf",
        ), "")

    result = evidence.snapshot({"dispatch": ["dispatch-shadow.service"]}, "2026-07-11T17:30:00+00:00",
        proc_root=proc, cgroup_root=cgroup, lock_path=lock, run=fake_run)
    row = result["services"][0]
    assert result["window_quality"]["status"] == "ELIGIBLE_SINGLE_SAMPLE"
    assert row["status"] == "PROVEN" and row["interpreter"]["value"] == "/venv/bin/python"
    assert row["process"]["memory"]["value"] == {"VmRSS": 1234 * 1024, "VmSwap": 7 * 1024}
    assert row["process"]["page_faults"]["value"] == (111, 3)
    assert row["cgroup"]["memory_current"]["value"] == 1200000
    assert row["cgroup"]["page_faults"]["value"] == {"pgfault": 222, "pgmajfault": 4}
    assert row["precedence"]["ordering"] == "ORDERED_BY_SYSTEMD_MANAGER"
    assert row["precedence"]["conflict"] == "UNKNOWN_WITHOUT_CONTENT_INSPECTION"
    assert calls == [evidence.systemctl_command("dispatch-shadow.service")]
    rendered = json.dumps(result, sort_keys=True)
    assert "dispatch.worker" not in rendered


def test_negative_control_never_requests_or_reads_blocked_sources(
    fake_kernel: tuple[Path, Path, Path], monkeypatch: pytest.MonkeyPatch
) -> None:
    proc, cgroup, lock = fake_kernel
    _add_process(proc, cgroup, 202, "/system.slice/courier-api.service")
    reads: list[str] = []
    original_text, original_bytes, original_link = evidence._read_text, evidence._read_bytes, evidence._readlink
    blocked = ("/e" + "tc/", "/en" + "viron", "." + "env", "dispatch_" + "state", "flags." + "json")

    def guard(path: Path) -> None:
        value = str(path); reads.append(value)
        if any(marker in value for marker in blocked):
            raise AssertionError(f"blocked read attempted: {value}")

    def guarded_text(path: Path) -> str: guard(path); return original_text(path)
    def guarded_bytes(path: Path) -> bytes: guard(path); return original_bytes(path)
    def guarded_link(path: Path) -> str: guard(path); return original_link(path)
    monkeypatch.setattr(evidence, "_read_text", guarded_text)
    monkeypatch.setattr(evidence, "_read_bytes", guarded_bytes)
    monkeypatch.setattr(evidence, "_readlink", guarded_link)

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        requested = command[-1].removeprefix("--property=").split(",")
        assert tuple(requested) == evidence.SYSTEMD_PROPERTIES
        assert not any(marker in prop for prop in requested for marker in evidence.FORBIDDEN_PROPERTY_MARKERS)
        return subprocess.CompletedProcess(command, 0, _show("courier-api.service", 202, "/system.slice/courier-api.service"), "")

    result = evidence.snapshot({"panel_api": ["courier-api.service"]}, "2026-07-11T17:31:00+00:00",
        proc_root=proc, cgroup_root=cgroup, lock_path=lock, run=fake_run)
    assert result["forbidden_sources_read"] == [] and reads
    assert all(not any(marker in path for marker in blocked) for path in reads)


def test_active_pytest_contaminates_without_emitting_command_line(fake_kernel: tuple[Path, Path, Path]) -> None:
    proc, _cgroup, lock = fake_kernel
    activity = proc / "999"; activity.mkdir()
    (activity / "comm").write_text("python\n", encoding="utf-8")
    (activity / "cmdline").write_bytes(b"python\x00-m\x00pytest\x00tests/opaque-case\x00")
    result = evidence.contamination(proc, lock)
    assert result["status"] == "CONTAMINATED" and result["reasons"] == ["active_pytest"]
    assert result["tool_activity"]["command_lines_emitted"] == 0
    assert "opaque-case" not in json.dumps(result)


def test_held_regression_lock_contaminates_window(fake_kernel: tuple[Path, Path, Path]) -> None:
    proc, _cgroup, lock = fake_kernel
    lock.write_text("", encoding="utf-8"); stat = lock.stat()
    identity = f"{os.major(stat.st_dev):02x}:{os.minor(stat.st_dev):02x}:{stat.st_ino}"
    (proc / "locks").write_text(f"1: FLOCK ADVISORY WRITE 123 {identity} 0 EOF\n", encoding="utf-8")
    result = evidence.contamination(proc, lock)
    assert result["status"] == "CONTAMINATED" and "full_regression_lock_held" in result["reasons"]


def test_unavailable_metrics_are_unknown_never_safe(fake_kernel: tuple[Path, Path, Path]) -> None:
    proc, cgroup, lock = fake_kernel
    process = proc / "303"; process.mkdir()
    (process / "comm").write_text("python\n", encoding="utf-8")
    (process / "cmdline").write_bytes(b"python\x00worker\x00")

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, _show("papu-backend.service", 303, "/system.slice/papu-backend.service"), "")

    row = evidence.snapshot({"papu": ["papu-backend.service"]}, "2026-07-11T17:32:00+00:00",
        proc_root=proc, cgroup_root=cgroup, lock_path=lock, run=fake_run)["services"][0]
    assert row["interpreter"]["status"] == "UNKNOWN"
    assert row["process"]["memory"]["status"] == "UNKNOWN"
    assert row["cgroup"]["memory_current"]["status"] == "UNKNOWN"
    assert "SAFE" not in json.dumps(row)


def test_not_loaded_and_bad_unit_fail_closed(fake_kernel: tuple[Path, Path, Path]) -> None:
    proc, cgroup, lock = fake_kernel
    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        values = {"Id": "missing.service", "LoadState": "not-found", "MainPID": "0"}
        return subprocess.CompletedProcess(command, 0, "\n".join(f"{p}={values.get(p, '')}" for p in evidence.SYSTEMD_PROPERTIES), "")
    row = evidence.snapshot({"papu": ["missing.service"]}, "2026-07-11T17:33:00+00:00",
        proc_root=proc, cgroup_root=cgroup, lock_path=lock, run=fake_run)["services"][0]
    assert row["status"] == "UNKNOWN" and row["pid"]["status"] == "UNKNOWN"
    with pytest.raises(ValueError, match="unsafe service name"):
        evidence.systemctl_command("bad;systemctl-cat.service")


def test_parser_rejects_unexpected_property() -> None:
    blocked_property = "Environ" + "ment"
    with pytest.raises(ValueError, match="unexpected systemd property"):
        evidence.parse_systemctl_show(blocked_property + "=opaque\n")


def test_infinite_effective_memory_limit_is_proven(fake_kernel: tuple[Path, Path, Path]) -> None:
    proc, cgroup, lock = fake_kernel
    _add_process(proc, cgroup, 404, "/system.slice/papu-notifications-worker.service")

    def fake_run(command: list[str], **_: object) -> subprocess.CompletedProcess[str]:
        stdout = _show("papu-notifications-worker.service", 404, "/system.slice/papu-notifications-worker.service")
        stdout = stdout.replace("MemoryHigh=536870912", "MemoryHigh=infinity").replace(
            "MemoryMax=1073741824", "MemoryMax=infinity"
        )
        return subprocess.CompletedProcess(command, 0, stdout, "")

    row = evidence.snapshot({"papu": ["papu-notifications-worker.service"]}, "2026-07-11T17:34:00+00:00",
        proc_root=proc, cgroup_root=cgroup, lock_path=lock, run=fake_run)["services"][0]
    assert row["effective_policy"]["MemoryHigh"] == {
        "status": "PROVEN", "value": "infinity", "source": "systemctl:MemoryHigh"
    }
    assert row["effective_policy"]["MemoryMax"]["value"] == "infinity"
