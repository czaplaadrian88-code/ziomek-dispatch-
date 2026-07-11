from __future__ import annotations

import fcntl
import hashlib
import json
import os
from pathlib import Path
import subprocess

import pytest

from test_restore_from_restic_a360_dr0 import (
    CARRIER_CANARY,
    RUN_ID,
    SCRIPT,
    _jsonl,
    _report,
    _run,
    restore_harness,
)


EXPECTED_APP_STAGES = [
    "panel_import",
    "papu_import",
    "dispatch_import",
    "panel_health",
    "papu_health",
    "dispatch_health",
    "service_start_order",
]
BACKUP_SCRIPT = SCRIPT.with_name("backup_restic.sh")
PROCESS_CARRIER_CANARY = "PROCESS_ARGUMENT_SECRET_MUST_NOT_BE_READ_OR_EMITTED"


def _make_encrypted(harness: dict[str, object]) -> None:
    papu_dir = Path(harness["papu_dir"])
    plain = next(papu_dir.glob("papu_*.sql.gz"))
    plain.rename(plain.with_suffix(plain.suffix + ".enc"))


def _app_stages(harness: dict[str, object]) -> list[str]:
    rows = _jsonl(Path(harness["app_log"]))
    return [row[row.index("--stage") + 1] for row in rows]


def _assert_no_owned_resources(harness: dict[str, object]) -> None:
    state_path = Path(harness["docker_state"])
    if not state_path.exists():
        return
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state.get("container") is None
    assert state.get("volume") is None


def _fake_proc_root(
    tmp_path: Path,
    *,
    comm: str,
    cgroup: str,
    carrier_canary: str = PROCESS_CARRIER_CANARY,
) -> Path:
    proc_root = tmp_path / "synthetic_proc"
    process = proc_root / "4242"
    process.mkdir(parents=True)
    (process / "comm").write_text(comm + "\n", encoding="utf-8")
    (process / "cgroup").write_text(cgroup + "\n", encoding="utf-8")
    (process / "cmdline").write_bytes(
        b"pytest\0tests/" + carrier_canary.encode("utf-8") + b"\0"
    )
    (process / "environ").write_bytes(
        b"SYNTHETIC_SECRET=" + carrier_canary.encode("utf-8") + b"\0"
    )
    return proc_root


def test_dr1a_known_answer_is_still_hold_and_has_exact_fake_app_order(
    restore_harness: dict[str, object],
) -> None:
    result = _run(restore_harness)

    assert result.returncode == 0, result.stderr
    report = _report(restore_harness)
    assert report["schema"] == "a360-dr1a-restore-prep-report-v1"
    assert report["dr1b_execution_gate"]["status"] == "HOLD"
    assert report["dr1b_execution_gate"]["go_authorized"] is False
    contracts = report["dr1a_contracts"]
    assert contracts["carrier"] == {
        "contract_version": "a360-dr1a-one-shot-carrier-v1-20260711",
        "issued_once": False,
        "value_logged": False,
        "value_serialized": False,
    }
    assert contracts["scratch_quota"]["enforced"] is True
    assert contracts["scratch_quota"]["last_probe"] == "pre_mutation"
    assert contracts["application_smoke"] == {
        "contract_version": "a360-dr1a-app-smoke-v1-20260711",
        "completed": True,
        "evidence": "synthetic_only",
        "expected_start_order": ["postgres", "panel", "papu", "dispatch"],
    }
    assert report["process_guard"] == {
        "strategy": "cooperative_shared_lock_plus_exact_comm_cgroup",
        "command_lines_read": 0,
        "command_lines_emitted": 0,
        "process_environments_read": 0,
    }
    assert _app_stages(restore_harness) == EXPECTED_APP_STAGES
    _assert_no_owned_resources(restore_harness)


def test_one_shot_carrier_accepts_only_canary_and_never_leaks(
    restore_harness: dict[str, object],
) -> None:
    _make_encrypted(restore_harness)
    result = _run(restore_harness, extra_args=("--papu-format", "encrypted"))

    assert result.returncode == 0, result.stderr
    carrier_rows = _jsonl(Path(restore_harness["carrier_log"]))
    assert carrier_rows == [[
        "issue",
        "--contract", "a360-dr1a-one-shot-carrier-v1-20260711",
        "--run-id", RUN_ID,
        "--purpose", "papu_backup_decrypt",
    ]]
    report_path = Path(restore_harness["target"]) / "a360_dr0_restore_report.json"
    visible = result.stdout + result.stderr + report_path.read_text(encoding="utf-8")
    for log_name in (
        "restic_log",
        "docker_log",
        "openssl_log",
        "carrier_log",
        "app_log",
        "quota_log",
    ):
        path = Path(restore_harness[log_name])
        if path.exists():
            visible += path.read_text(encoding="utf-8")
    assert CARRIER_CANARY not in visible
    openssl_rows = _jsonl(Path(restore_harness["openssl_log"]))
    assert len(openssl_rows) == 2
    assert all("stdin" in row for row in openssl_rows)
    assert all(not any(CARRIER_CANARY in arg for arg in row) for row in openssl_rows)
    assert _report(restore_harness)["dr1a_contracts"]["carrier"]["issued_once"] is True
    _assert_no_owned_resources(restore_harness)


@pytest.mark.parametrize(
    ("env_update", "reason"),
    [
        ({"FAKE_QUOTA_ENFORCED": "0"}, "scratch_quota_attestation_invalid"),
        ({"FAKE_QUOTA_RUN_ID": "foreign_run"}, "scratch_quota_attestation_invalid"),
        ({"FAKE_QUOTA_CONTRACT": "foreign-contract"}, "scratch_quota_attestation_invalid"),
        ({"FAKE_QUOTA_LIMIT_BYTES": "5368709120", "FAKE_QUOTA_USED_BYTES": "1"}, "scratch_quota_reserve_too_low"),
    ],
)
def test_quota_attestation_is_fail_closed_before_restic(
    restore_harness: dict[str, object],
    env_update: dict[str, str],
    reason: str,
) -> None:
    result = _run(restore_harness, env_update=env_update)

    assert result.returncode != 0
    assert reason in result.stderr
    assert _jsonl(Path(restore_harness["restic_log"])) == []
    assert _jsonl(Path(restore_harness["docker_log"])) == []
    _assert_no_owned_resources(restore_harness)


def test_wrong_carrier_canary_is_red_before_decrypt_or_docker(
    restore_harness: dict[str, object],
) -> None:
    _make_encrypted(restore_harness)
    result = _run(
        restore_harness,
        extra_args=("--papu-format", "encrypted"),
        env_update={"FAKE_CARRIER_VALUE": "WRONG_SYNTHETIC_VALUE"},
    )

    assert result.returncode != 0
    assert "carrier_test_canary_invalid" in result.stderr
    assert _jsonl(Path(restore_harness["openssl_log"])) == []
    assert _jsonl(Path(restore_harness["docker_log"])) == []
    _assert_no_owned_resources(restore_harness)


@pytest.mark.parametrize("stage", EXPECTED_APP_STAGES)
def test_each_fake_app_failure_is_red_and_exact_run_cleanup_is_complete(
    restore_harness: dict[str, object], stage: str
) -> None:
    result = _run(restore_harness, env_update={"FAKE_APP_FAIL_STAGE": stage})

    assert result.returncode != 0
    assert f"app_probe_{stage}_failed" in result.stderr
    assert _app_stages(restore_harness) == EXPECTED_APP_STAGES[: EXPECTED_APP_STAGES.index(stage) + 1]
    assert not Path(restore_harness["target"]).exists()
    _assert_no_owned_resources(restore_harness)


def test_foreign_run_id_created_during_partial_app_failure_is_never_deleted(
    restore_harness: dict[str, object],
) -> None:
    result = _run(
        restore_harness,
        env_update={
            "FAKE_APP_FAIL_STAGE": "panel_import",
            "FAKE_APP_FOREIGN_CONTAINER_RUN_ID": "1",
        },
    )

    assert result.returncode == 90
    assert "scratch_rollback_incomplete" in result.stderr
    state = json.loads(Path(restore_harness["docker_state"]).read_text(encoding="utf-8"))
    assert state["container"]["labels"]["a360.dr0.run_id"] == "foreign_run"
    assert state["volume"]["labels"]["a360.dr0.run_id"] == RUN_ID
    docker_rows = _jsonl(Path(restore_harness["docker_log"]))
    assert not any(row.get("argv", [None])[0] == "rm" for row in docker_rows if isinstance(row, dict))


def test_active_backup_race_before_mutation_stops_before_any_create(
    restore_harness: dict[str, object],
) -> None:
    result = _run(
        restore_harness,
        env_update={"A360_TEST_THIRD_CONFLICT_PROCESS": "1"},
    )

    assert result.returncode != 0
    assert "concurrent_heavy_job_detected" in result.stderr
    docker_rows = _jsonl(Path(restore_harness["docker_log"]))
    assert not any(row.get("argv", [])[:2] == ["volume", "create"] for row in docker_rows if isinstance(row, dict))
    _assert_no_owned_resources(restore_harness)


@pytest.mark.parametrize(
    ("comm", "cgroup"),
    [
        ("restic", "0::/user.slice/session.scope"),
        ("pg_dump", "0::/user.slice/session.scope"),
        ("pg_basebackup", "0::/user.slice/session.scope"),
        ("bash", "0::/system.slice/dispatch-restic-backup.service"),
        ("python", "0::/system.slice/nadajesz-panel-backup.service"),
        ("bash", "0::/system.slice/papu-db-backup.service"),
        ("bash", "0::/system.slice/papu-offsite-restic.service"),
    ],
)
def test_safe_process_probe_rejects_exact_comm_or_backup_unit_without_argv(
    restore_harness: dict[str, object],
    tmp_path: Path,
    comm: str,
    cgroup: str,
) -> None:
    proc_root = _fake_proc_root(tmp_path, comm=comm, cgroup=cgroup)
    result = _run(
        restore_harness,
        mode="verify",
        env_update={"A360_TEST_PROC_ROOT": str(proc_root)},
    )

    assert result.returncode != 0
    assert "concurrent_heavy_job_detected" in result.stderr
    assert PROCESS_CARRIER_CANARY not in result.stdout + result.stderr
    assert _jsonl(Path(restore_harness["restic_log"])) == []


def test_safe_process_probe_ignores_argument_carriers_and_similar_names(
    restore_harness: dict[str, object], tmp_path: Path
) -> None:
    proc_root = _fake_proc_root(
        tmp_path,
        comm="myrestic",
        cgroup="0::/system.slice/not-dispatch-restic-backup.service-extra",
    )
    result = _run(
        restore_harness,
        env_update={"A360_TEST_PROC_ROOT": str(proc_root)},
    )

    assert result.returncode == 0, result.stderr
    report_path = Path(restore_harness["target"]) / "a360_dr0_restore_report.json"
    visible = result.stdout + result.stderr + report_path.read_text(encoding="utf-8")
    assert PROCESS_CARRIER_CANARY not in visible
    assert _report(restore_harness)["process_guard"]["command_lines_read"] == 0


def test_process_guard_source_has_c32_ratchet_and_backup_uses_shared_lock() -> None:
    restore_source = SCRIPT.read_text(encoding="utf-8")
    backup_source = BACKUP_SCRIPT.read_text(encoding="utf-8")

    for forbidden in ("/cmdline", "/environ"):
        assert forbidden not in restore_source
    expected_lock = "/run/lock/ziomek/heavy-operation.lock"
    assert f'HOST_ACTIVITY_LOCK="{expected_lock}"' in restore_source
    assert f"HOST_ACTIVITY_LOCK={expected_lock}" in backup_source
    assert "flock -n 8" in restore_source
    assert "flock -n 8" in backup_source
    assert '"command_lines_emitted": 0' in restore_source


def test_shared_host_activity_lock_fails_closed_before_restic(
    restore_harness: dict[str, object],
) -> None:
    lock_path = Path(restore_harness["env"]["A360_TEST_HOST_ACTIVITY_LOCK"])
    with lock_path.open("a+b") as lock_handle:
        fcntl.flock(lock_handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        result = _run(restore_harness, mode="verify")

    assert result.returncode != 0
    assert "concurrent_heavy_job_detected" in result.stderr
    assert _jsonl(Path(restore_harness["restic_log"])) == []


def test_wrong_inherited_attestation_fd_cannot_enable_test_mode(
    restore_harness: dict[str, object],
) -> None:
    env = dict(restore_harness["env"])
    attest_read_fd, attest_write_fd = os.pipe()
    try:
        os.write(attest_write_fd, b"wrong-parent-attestation\n")
    finally:
        os.close(attest_write_fd)
    env["A360_TEST_ATTEST_FD"] = str(attest_read_fd)
    try:
        result = subprocess.run(
            [str(SCRIPT), "--mode", "verify"],
            env=env,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
            pass_fds=(attest_read_fd,),
        )
    finally:
        os.close(attest_read_fd)

    assert result.returncode == 2
    assert "scratch_root_override_requires_test_mode" in result.stderr
    assert _jsonl(Path(restore_harness["restic_log"])) == []


def test_backup_unit_guard_mutation_is_observable(
    restore_harness: dict[str, object], tmp_path: Path
) -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    needle = '    b"dispatch-restic-backup.service",\n'
    assert source.count(needle) == 1
    mutant = tmp_path / "restore_without_dispatch_backup_unit.sh"
    mutant.write_text(source.replace(needle, "", 1), encoding="utf-8")
    mutant.chmod(0o700)
    proc_root = _fake_proc_root(
        tmp_path,
        comm="bash",
        cgroup="0::/system.slice/dispatch-restic-backup.service",
    )

    result = _run(
        restore_harness,
        mode="verify",
        env_update={"A360_TEST_PROC_ROOT": str(proc_root)},
        script=mutant,
    )

    assert result.returncode == 0, result.stderr


def test_carrier_fail_open_mutation_is_detected(
    restore_harness: dict[str, object], tmp_path: Path
) -> None:
    _make_encrypted(restore_harness)
    source = SCRIPT.read_text(encoding="utf-8")
    needle = '[ "$digest" = "$A360_TEST_CARRIER_CANARY_SHA256" ]'
    assert source.count(needle) == 1
    mutated = tmp_path / "restore_carrier_fail_open.sh"
    mutated.write_text(
        source.replace(needle, '[ -n "$digest" ] || fail "carrier_test_canary_invalid"'),
        encoding="utf-8",
    )
    mutated.chmod(0o700)
    wrong = "WRONG_SYNTHETIC_VALUE"
    result = _run(
        restore_harness,
        extra_args=("--papu-format", "encrypted"),
        env_update={
            "FAKE_CARRIER_VALUE": wrong,
            "FAKE_CARRIER_CANARY_SHA256": hashlib.sha256(wrong.encode()).hexdigest(),
        },
        script=mutated,
    )

    assert result.returncode == 0, result.stderr
    assert _report(restore_harness)["dr1a_contracts"]["carrier"]["issued_once"] is True


def test_quota_fail_open_mutation_is_detected(
    restore_harness: dict[str, object], tmp_path: Path
) -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    needle = 'if payload["scratch_root"] != sys.argv[4] or payload["enforced"] is not True:'
    assert source.count(needle) == 1
    mutated = tmp_path / "restore_quota_fail_open.sh"
    mutated.write_text(
        source.replace(needle, 'if payload["scratch_root"] != sys.argv[4]:'),
        encoding="utf-8",
    )
    mutated.chmod(0o700)
    result = _run(
        restore_harness,
        env_update={"FAKE_QUOTA_ENFORCED": "0"},
        script=mutated,
    )

    assert result.returncode == 0, result.stderr
    assert _report(restore_harness)["capacity_preflight"]["filesystem_quota_enforced"] is True


def test_app_smoke_omission_mutation_is_detected(
    restore_harness: dict[str, object], tmp_path: Path
) -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    needle = "  run_app_probe_stage dispatch_health\n"
    assert source.count(needle) == 1
    mutated = tmp_path / "restore_app_stage_omitted.sh"
    mutated.write_text(source.replace(needle, ""), encoding="utf-8")
    mutated.chmod(0o700)
    result = _run(restore_harness, script=mutated)

    assert result.returncode == 0, result.stderr
    assert _app_stages(restore_harness) == [
        stage for stage in EXPECTED_APP_STAGES if stage != "dispatch_health"
    ]
