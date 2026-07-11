from __future__ import annotations

import base64
import gzip
import hashlib
import json
import os
import shutil
import sqlite3
import stat
import subprocess
import textwrap
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "docs/deploy/ha-lite/restore_from_restic.sh"
README = REPO_ROOT / "docs/deploy/ha-lite/README.md"
RUNBOOK = REPO_ROOT / "docs/deploy/ha-lite/HA_LITE_RUNBOOK_2026-06-21.md"
PINNED_TEST_IMAGE = "synthetic-postgres@sha256:" + ("a" * 64)
RUN_ID = "synthetic_0700"
SENSITIVE_CANARY = "SENSITIVE_CANARY_MUST_NEVER_LEAK"
PRIVATE_CONFIG_NAME = ".en" + "v"
REQUIRED_CORE_PATHS = (
    "root/.openclaw/workspace/dispatch_state/orders_state.json",
    "root/.openclaw/workspace/dispatch_state/courier_plans.json",
    "root/.openclaw/workspace/dispatch_state/events.db",
    "root/.openclaw/workspace/scripts/flags.json",
)
REQUIRED_PRIVATE_METADATA_PATHS = (
    "root/.openclaw/workspace/dispatch_state/kurier_ids.json",
    "root/.openclaw/workspace/dispatch_state/kurier_piny.json",
    "root/.openclaw/workspace/dispatch_state/courier_names.json",
    "root/.openclaw/workspace/dispatch_state/courier_tiers.json",
    "root/.openclaw/workspace/dispatch_state/grafik_full_names.json",
    f"root/.openclaw/workspace/{PRIVATE_CONFIG_NAME}",
    f"root/.openclaw/workspace/ordering_app/{PRIVATE_CONFIG_NAME}",
    f"root/.openclaw/workspace/nadajesz_clone/panel/backend/{PRIVATE_CONFIG_NAME}",
)
REQUIRED_SYSTEMD_PATHS = tuple(
    f"etc/systemd/system/{name}"
    for name in (
        "dispatch-shadow.service",
        "dispatch-panel-watcher.service",
        "dispatch-sla-tracker.service",
        "dispatch-gps.service",
        "dispatch-telegram.service",
        "nadajesz-panel.service",
        "nadajesz-ordering.service",
        "courier-api.service",
        "papu-backend.service",
        "papu-backend-2.service",
        "papu-notifications-worker.service",
        "dispatch-restic-backup.service",
        "dispatch-restic-backup.timer",
        "nadajesz-panel-backup.service",
        "nadajesz-panel-backup.timer",
        "papu-db-backup.service",
        "papu-db-backup.timer",
        "backup-sentinel.service",
        "backup-sentinel.timer",
    )
)
REQUIRED_NGINX_PATHS = tuple(
    f"etc/nginx/sites-available/{name}"
    for name in ("gps-nadajesz", "lokalka", "bialystok-nadajesz")
)
REQUIRED_MANIFEST_PATHS = (
    REQUIRED_CORE_PATHS
    + REQUIRED_PRIVATE_METADATA_PATHS
    + REQUIRED_SYSTEMD_PATHS
    + REQUIRED_NGINX_PATHS
)
# Staly syntetyczny wektor kontraktu producenta Papu:
# gzip -n | openssl enc -e -aes-256-cbc -pbkdf2, haslo "synthetic-only".
# Test tylko odszyfrowuje ten niezmienny ciphertext; nie generuje go tym samym
# procesem w runtime, wiec literowka algorytmu/KDF przestaje byc self-consistent.
PAPU_ENCRYPTED_KNOWN_ANSWER_B64 = (
    "U2FsdGVkX19U7AT76UWlRR6ZmcjGm8KFN7vgc4ZbJNHxleCgMrXy7QNX050LGSQm"
    "fENNMP/+A9Gqy0ZqoUjc90L8o+s4/W/PPf+VG8Q/NVE="
)


def _write_executable(path: Path, body: str) -> None:
    path.write_text(textwrap.dedent(body).lstrip(), encoding="utf-8")
    path.chmod(0o700)


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _set_private_file(path: Path) -> None:
    path.chmod(0o600)


@pytest.fixture
def restore_harness(tmp_path: Path) -> dict[str, object]:
    fixture_root = tmp_path / "synthetic_snapshot"
    state = fixture_root / "root/.openclaw/workspace/dispatch_state"
    scripts = fixture_root / "root/.openclaw/workspace/scripts"
    panel_dir = fixture_root / "root/backups/nadajesz_panel"
    papu_dir = fixture_root / "root/backups/papu"
    systemd_dir = fixture_root / "etc/systemd/system"
    nginx_dir = fixture_root / "etc/nginx/sites-available"
    for directory in (state, scripts, panel_dir, papu_dir, systemd_dir, nginx_dir):
        directory.mkdir(parents=True, exist_ok=True)

    _write_json(state / "orders_state.json", {"synthetic": {"marker": SENSITIVE_CANARY}})
    _write_json(state / "courier_plans.json", {})
    _write_json(scripts / "flags.json", {"SYNTHETIC_FLAG": False})

    with sqlite3.connect(state / "events.db") as connection:
        connection.executescript(
            "CREATE TABLE events("
            "event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, order_id TEXT, "
            "courier_id TEXT, payload TEXT, created_at TEXT NOT NULL, "
            "processed_at TEXT, status TEXT DEFAULT 'pending');"
            "CREATE TABLE processed_events("
            "event_id TEXT PRIMARY KEY, processed_at TEXT NOT NULL);"
            "CREATE TABLE audit_log("
            "event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, order_id TEXT, "
            "courier_id TEXT, payload TEXT, created_at TEXT NOT NULL);"
        )

    panel_dump = panel_dir / "nadajesz_panel_20260711.sql.gz"
    panel_dump.write_bytes(
        gzip.compress(
            f"CREATE TABLE synthetic_panel(id integer); -- {SENSITIVE_CANARY}\n".encode()
        )
    )
    papu_dump = papu_dir / "papu_20260711.sql.gz"
    papu_dump.write_bytes(
        gzip.compress(
            f"CREATE TABLE synthetic_papu(id integer); -- {SENSITIVE_CANARY}\n".encode()
        )
    )
    for relative_path in REQUIRED_PRIVATE_METADATA_PATHS:
        path = fixture_root / relative_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(SENSITIVE_CANARY + "\n", encoding="utf-8")
    for relative_path in REQUIRED_SYSTEMD_PATHS:
        path = fixture_root / relative_path
        path.write_text("[Unit]\nDescription=synthetic\n", encoding="utf-8")
    for relative_path in REQUIRED_NGINX_PATHS:
        path = fixture_root / relative_path
        path.write_text("# synthetic vhost metadata\n", encoding="utf-8")

    artifact_epoch = int((datetime.now(timezone.utc) - timedelta(minutes=30)).timestamp())
    for path in fixture_root.rglob("*"):
        if path.is_file():
            os.utime(path, (artifact_epoch, artifact_epoch))

    fake_dir = tmp_path / "fake_bin"
    fake_dir.mkdir()
    restic_log = tmp_path / "fake_restic.jsonl"
    docker_log = tmp_path / "fake_docker.jsonl"
    docker_state = tmp_path / "fake_docker_state.json"
    openssl_log = tmp_path / "fake_openssl.jsonl"

    fake_restic = fake_dir / "restic"
    _write_executable(
        fake_restic,
        r'''
        #!/usr/bin/env python3
        import json
        import os
        import shutil
        import sys

        args = sys.argv[1:]
        cache_home = os.environ["XDG_CACHE_HOME"]
        os.makedirs(cache_home, mode=0o700, exist_ok=True)
        cache_probe = os.path.join(cache_home, "synthetic_cache_probe")
        fd = os.open(cache_probe, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        os.close(fd)
        log_path = os.environ["FAKE_RESTIC_LOG"]
        fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(args) + "\n")
        command = args[0] if args else ""
        if os.environ.get("FAKE_RESTIC_FAIL") == command:
            raise SystemExit(41)
        if command == "snapshots":
            current = {
                "id": "b" * 64,
                "time": os.environ["FAKE_SNAPSHOT_TIME"],
                "paths": ["/synthetic/redacted"],
            }
            rows = [current]
            if os.environ.get("FAKE_MULTIPLE_SNAPSHOTS") == "1":
                rows.insert(0, {
                    "id": "a" * 64,
                    "time": os.environ["FAKE_OLDER_SNAPSHOT_TIME"],
                    "paths": ["/synthetic/older-redacted"],
                })
            if os.environ.get("FAKE_TIED_SNAPSHOTS") == "1":
                rows.insert(0, {
                    "id": "a" * 64,
                    "time": os.environ["FAKE_SNAPSHOT_TIME"],
                    "paths": ["/synthetic/tied-redacted"],
                })
            print(json.dumps(rows))
        elif command == "check":
            raise SystemExit(0)
        elif command == "stats":
            total_size = 0
            total_files = 0
            for root, _dirs, files in os.walk(os.environ["FAKE_SNAPSHOT_ROOT"]):
                for name in files:
                    total_size += os.path.getsize(os.path.join(root, name))
                    total_files += 1
            if os.environ.get("FAKE_SNAPSHOT_TOTAL_SIZE"):
                total_size = int(os.environ["FAKE_SNAPSHOT_TOTAL_SIZE"])
            print(json.dumps({"total_size": total_size, "total_file_count": total_files}))
        elif command == "restore":
            target = args[args.index("--target") + 1]
            shutil.copytree(
                os.environ["FAKE_SNAPSHOT_ROOT"],
                target,
                dirs_exist_ok=True,
                copy_function=shutil.copy2,
                symlinks=True,
            )
        else:
            raise SystemExit(42)
        ''',
    )

    fake_openssl = fake_dir / "openssl"
    _write_executable(
        fake_openssl,
        r'''
        #!/usr/bin/env python3
        import json
        import os
        import sys

        args = sys.argv[1:]
        fd = os.open(os.environ["FAKE_OPENSSL_LOG"],
                     os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a", encoding="utf-8") as handle:
            handle.write(json.dumps(args) + "\n")
        if os.environ.get("FAKE_OPENSSL_FAIL") == "1":
            raise SystemExit(51)
        source = args[args.index("-in") + 1]
        with open(source, "rb") as handle:
            sys.stdout.buffer.write(handle.read())
        ''',
    )

    fake_docker = fake_dir / "docker"
    _write_executable(
        fake_docker,
        r'''
        #!/usr/bin/env python3
        import hashlib
        import json
        import os
        import sys

        args = sys.argv[1:]
        state_path = os.environ["FAKE_DOCKER_STATE"]
        log_path = os.environ["FAKE_DOCKER_LOG"]

        def load_state():
            try:
                with open(state_path, "r", encoding="utf-8") as handle:
                    return json.load(handle)
            except FileNotFoundError:
                return {
                    "container": None,
                    "volume": None,
                    "databases": [],
                    "restores": {},
                    "table_counts": {},
                    "restore_roles": {},
                }

        def save_state(state):
            tmp = state_path + ".tmp"
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(state, handle)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(tmp, state_path)

        def append_log(payload):
            fd = os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, sort_keys=True) + "\n")

        append_log({"argv": args})
        state = load_state()
        if not args:
            raise SystemExit(61)

        if args[:2] == ["image", "inspect"]:
            if os.environ.get("FAKE_DOCKER_FAIL") == "image":
                raise SystemExit(62)
            raise SystemExit(0)

        if args[0] == "volume":
            action = args[1]
            if action == "inspect":
                formatted = len(args) > 2 and args[2] == "-f"
                name = args[-1]
                volume = state.get("volume")
                if not volume or volume.get("name") != name:
                    raise SystemExit(1)
                if formatted:
                    template = args[3]
                    if '"a360.dr0.scratch"' in template:
                        print(volume["labels"].get("a360.dr0.scratch", ""))
                    elif '"a360.dr0.run_id"' in template:
                        if os.environ.get("FAKE_DOCKER_FOREIGN_VOLUME_LABEL") == "1":
                            print("foreign_run")
                        else:
                            print(volume["labels"].get("a360.dr0.run_id", ""))
                    else:
                        raise SystemExit(64)
                raise SystemExit(0)
            if action == "create":
                if os.environ.get("FAKE_DOCKER_FAIL") == "volume_create":
                    raise SystemExit(63)
                labels = {}
                for index, value in enumerate(args):
                    if value == "--label":
                        key, label_value = args[index + 1].split("=", 1)
                        labels[key] = label_value
                state["volume"] = {"name": args[-1], "labels": labels}
                save_state(state)
                print(args[-1])
                raise SystemExit(0)
            if action == "rm":
                volume = state.get("volume")
                if not volume or volume.get("name") != args[-1]:
                    raise SystemExit(64)
                state["volume"] = None
                save_state(state)
                raise SystemExit(0)

        if args[0] == "run":
            if os.environ.get("FAKE_DOCKER_FAIL") == "run":
                raise SystemExit(65)
            name = args[args.index("--name") + 1]
            labels = {}
            for index, value in enumerate(args):
                if value == "--label":
                    key, label_value = args[index + 1].split("=", 1)
                    labels[key] = label_value
            mounts = [args[index + 1] for index, value in enumerate(args) if value == "-v"]
            published = [value for value in args if value in {"-p", "--publish", "-P", "--publish-all"}]
            state["container"] = {
                "name": name,
                "labels": labels,
                "network": args[args.index("--network") + 1] if "--network" in args else "bridge",
                "mounts": mounts,
                "published": published,
                "pull": args[args.index("--pull") + 1] if "--pull" in args else "missing",
                "image": args[-1],
            }
            save_state(state)
            print("c" * 64)
            raise SystemExit(0)

        if args[0] == "inspect":
            formatted = len(args) > 1 and args[1] == "-f"
            name = args[-1]
            container = state.get("container")
            if not container or container.get("name") != name:
                raise SystemExit(1)
            if not formatted:
                raise SystemExit(0)
            template = args[2]
            if ".State.Running" in template:
                print("true")
            elif '"a360.dr0.scratch"' in template:
                print(container["labels"].get("a360.dr0.scratch", ""))
            elif '"a360.dr0.run_id"' in template:
                print(container["labels"].get("a360.dr0.run_id", ""))
            elif ".HostConfig.NetworkMode" in template:
                print(container["network"])
            elif "PortBindings" in template:
                print(len(container["published"]))
            elif "len .Mounts" in template:
                print(len(container["mounts"]))
            elif "range .Mounts" in template:
                matches = [
                    mount.split(":", 1)[0]
                    for mount in container["mounts"]
                    if mount.split(":", 1)[1] == "/var/lib/postgresql/data"
                ]
                print("".join(matches))
            else:
                raise SystemExit(66)
            raise SystemExit(0)

        if args[0] == "rm":
            name = args[-1]
            container = state.get("container")
            if not container or container.get("name") != name:
                raise SystemExit(67)
            if os.environ.get("FAKE_DOCKER_FAIL") == "rm_container":
                raise SystemExit(75)
            state["container"] = None
            save_state(state)
            raise SystemExit(0)

        if args[0] == "exec":
            interactive = len(args) > 1 and args[1] == "-i"
            offset = 2 if interactive else 1
            container = args[offset]
            command = args[offset + 1:]
            container_state = state.get("container")
            if not container_state or container_state.get("name") != container:
                raise SystemExit(68)
            if command[0] == "pg_isready":
                raise SystemExit(0)
            if command[0] == "createdb":
                if os.environ.get("FAKE_DOCKER_FAIL") == "createdb":
                    raise SystemExit(69)
                database = command[-1]
                if database in state["databases"]:
                    raise SystemExit(70)
                state["databases"].append(database)
                save_state(state)
                raise SystemExit(0)
            if command[0] == "psql":
                database = command[command.index("-d") + 1]
                if interactive:
                    body = sys.stdin.buffer.read()
                    strict = (
                        "-X" in command
                        and "ON_ERROR_STOP=1" in command
                        and "--single-transaction" in command
                    )
                    append_log({
                        "database": database,
                        "stdin_bytes": len(body),
                        "stdin_sha256": hashlib.sha256(body).hexdigest(),
                        "strict": strict,
                    })
                    fail_role = os.environ.get("FAKE_SQL_FAIL", "")
                    # Real psql moze zwrocic 0 po bledzie statementu, gdy
                    # ON_ERROR_STOP nie jest aktywne. Fake odtwarza te semantyke:
                    # ten sam syntetyczny blad jest non-zero tylko w strict mode.
                    if fail_role and fail_role in database and strict:
                        raise SystemExit(72)
                    state["restores"][database] = hashlib.sha256(body).hexdigest()
                    if b"synthetic_panel" in body:
                        state["table_counts"][database] = 60
                        state["restore_roles"][database] = "panel"
                    elif b"synthetic_papu" in body:
                        state["table_counts"][database] = 65
                        state["restore_roles"][database] = "papu"
                    else:
                        state["table_counts"][database] = 0
                        state["restore_roles"][database] = "unknown"
                    save_state(state)
                    raise SystemExit(0)
                query = command[command.index("-Atqc") + 1]
                if "SELECT 1" in query:
                    print("1")
                elif "information_schema.tables" in query:
                    print(state["table_counts"].get(database, 0))
                elif "pg_index" in query:
                    print("0")
                elif "to_regclass" in query:
                    restored_role = state["restore_roles"].get(database, "unknown")
                    expected_role = "panel" if "public.delivery" in query else "papu"
                    print("2" if restored_role == expected_role else "0")
                else:
                    raise SystemExit(73)
                raise SystemExit(0)
        raise SystemExit(74)
        ''',
    )

    password_file = tmp_path / "synthetic_restic_credential"
    password_file.write_text("synthetic-only\n", encoding="utf-8")
    _set_private_file(password_file)
    key_file = tmp_path / "synthetic_decrypt_credential"
    key_file.write_text("synthetic-only\n", encoding="utf-8")
    _set_private_file(key_file)

    scratch_root = tmp_path / "scratch_0700"
    docker_root = tmp_path / "docker_root"
    docker_root.mkdir(mode=0o700)
    home = tmp_path / "home"
    home.mkdir(mode=0o700)
    snapshot_time = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    env = {
        # Kazde przypadkowe bezposrednie wywolanie restic/docker/openssl nadal
        # trafia do fake; systemowy PATH zostaje tylko dla neutralnych coreutils.
        "PATH": f"{fake_dir}:/usr/bin:/bin",
        "HOME": str(home),
        "XDG_CACHE_HOME": str(tmp_path / "cache"),
        "TZ": "UTC",
        "DISPATCH_UNDER_PYTEST": "1",
        "A360_TEST_MODE": "1",
        "A360_TEST_RUN_ID": RUN_ID,
        "A360_RESTIC_BIN": str(fake_restic),
        "A360_DOCKER_BIN": str(fake_docker),
        "A360_OPENSSL_BIN": str(fake_openssl),
        "A360_GZIP_BIN": shutil.which("gzip") or "/usr/bin/gzip",
        "A360_SQLITE_BIN": shutil.which("sqlite3") or "/usr/bin/sqlite3",
        "A360_PYTHON_BIN": shutil.which("python3") or "/usr/bin/python3",
        "A360_DR0_SCRATCH_ROOT": str(scratch_root),
        "A360_DR0_SCRATCH_BUDGET_BYTES": "107374182400",
        "A360_DR0_DOCKER_BUDGET_BYTES": "107374182400",
        "A360_DR0_MAX_SNAPSHOT_AGE_SECONDS": "86400",
        "A360_DR0_PG_READY_TIMEOUT_SECONDS": "2",
        "A360_TEST_LOAD1": "0.1",
        "A360_TEST_CPU_COUNT": "4",
        "A360_TEST_MEM_AVAILABLE_BYTES": "8589934592",
        "A360_TEST_FREE_BYTES": "107374182400",
        "A360_TEST_DOCKER_FREE_BYTES": "107374182400",
        "A360_TEST_DOCKER_ROOT": str(docker_root),
        "A360_TEST_SAME_DEVICE": "0",
        "RESTIC_PASSWORD_FILE": str(password_file),
        "RESTIC_REPOSITORY": "synthetic:repository",
        "PAPU_BACKUP_KEY_FILE": str(key_file),
        "FAKE_SNAPSHOT_ROOT": str(fixture_root),
        "FAKE_SNAPSHOT_TIME": snapshot_time,
        "FAKE_OLDER_SNAPSHOT_TIME": (
            datetime.now(timezone.utc) - timedelta(hours=2)
        ).isoformat(),
        "FAKE_RESTIC_LOG": str(restic_log),
        "FAKE_DOCKER_LOG": str(docker_log),
        "FAKE_DOCKER_STATE": str(docker_state),
        "FAKE_OPENSSL_LOG": str(openssl_log),
        "DOCKER_HOST": "unix:///definitely-not-real-a360-dr0.sock",
    }
    for name in ("PYTHONPATH", "HERMETIC_STRICT", "DISPATCH_STATE_DIR"):
        if name in os.environ:
            env[name] = os.environ[name]

    return {
        "tmp": tmp_path,
        "fixture": fixture_root,
        "state": state,
        "scripts": scripts,
        "panel_dir": panel_dir,
        "papu_dir": papu_dir,
        "scratch": scratch_root,
        "docker_root": docker_root,
        "target": scratch_root / f"restore_{RUN_ID}",
        "env": env,
        "restic_log": restic_log,
        "docker_log": docker_log,
        "docker_state": docker_state,
        "openssl_log": openssl_log,
        "key_file": key_file,
        "password_file": password_file,
        "panel_sql_sha256": hashlib.sha256(gzip.decompress(panel_dump.read_bytes())).hexdigest(),
        "papu_sql_sha256": hashlib.sha256(gzip.decompress(papu_dump.read_bytes())).hexdigest(),
    }


def _run(
    harness: dict[str, object],
    *,
    mode: str = "drill",
    extra_args: tuple[str, ...] = (),
    env_update: dict[str, str] | None = None,
    script: Path = SCRIPT,
) -> subprocess.CompletedProcess[str]:
    env = dict(harness["env"])
    if env_update:
        env.update(env_update)
    command = [str(script), "--mode", mode]
    if mode == "drill":
        command += ["--pg-image", PINNED_TEST_IMAGE]
    command += list(extra_args)
    return subprocess.run(
        command,
        env=env,
        text=True,
        capture_output=True,
        timeout=30,
        check=False,
    )


def _jsonl(path: Path) -> list[object]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def _report(harness: dict[str, object]) -> dict[str, object]:
    path = Path(harness["target"]) / "a360_dr0_restore_report.json"
    return json.loads(path.read_text(encoding="utf-8"))


def test_known_answer_fake_postgres_schema_drill_is_isolated_and_redacted(
    restore_harness: dict[str, object],
) -> None:
    result = _run(restore_harness)

    assert result.returncode == 0, result.stderr
    assert "PASS scope=artifact_and_postgres_schema_only" in result.stdout
    assert SENSITIVE_CANARY not in result.stdout + result.stderr
    target = Path(restore_harness["target"])
    report_path = target / "a360_dr0_restore_report.json"
    assert stat.S_IMODE(target.stat().st_mode) == 0o700
    assert stat.S_IMODE((target / ".cache").stat().st_mode) == 0o700
    assert (target / ".cache/synthetic_cache_probe").is_file()
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600

    report_text = report_path.read_text(encoding="utf-8")
    report = json.loads(report_text)
    assert SENSITIVE_CANARY not in report_text
    assert report["status"] == "PASS"
    assert report["evidence_scope"] == "artifact_and_postgres_schema_only"
    assert report["full_service_recovery_proven"] is False
    assert report["service_rto"] == {
        "status": "HOLD",
        "proven": False,
        "seconds": None,
        "missing_evidence": [
            "application_import",
            "application_health",
            "service_start_order",
            "systemd_activation",
            "nginx_activation",
            "traffic_switch",
        ],
    }
    assert report["recovery_timing"]["scope"] == "artifact_and_postgres_schema_only"
    assert report["recovery_timing"]["to_postgres_schema_smoke_seconds"] >= 0
    assert report["rpo"]["proven"] is False
    assert report["rpo"]["pitr_proven"] is False
    assert report["artifacts"]["required_json_files"] == 3
    assert report["artifacts"]["sqlite_table_count"] == 3
    contract = report["required_artifact_contract"]
    assert contract == {
        "version": "a360-dr0-required-artifacts-v1-20260711",
        "core_required": len(REQUIRED_CORE_PATHS),
        "core_satisfied": len(REQUIRED_CORE_PATHS),
        "private_metadata_required": len(REQUIRED_PRIVATE_METADATA_PATHS),
        "private_metadata_satisfied": len(REQUIRED_PRIVATE_METADATA_PATHS),
        "systemd_required": len(REQUIRED_SYSTEMD_PATHS),
        "systemd_satisfied": len(REQUIRED_SYSTEMD_PATHS),
        "nginx_required": len(REQUIRED_NGINX_PATHS),
        "nginx_satisfied": len(REQUIRED_NGINX_PATHS),
        "private_file_contents_read": False,
    }
    capacity = report["capacity_preflight"]
    assert capacity["run_budget_enforced"] is True
    assert capacity["filesystem_quota_enforced"] is False
    assert capacity["free_space_checked"] is True
    assert report["postgres"]["panel_table_count"] == 60
    assert report["postgres"]["papu_table_count"] == 65
    assert report["postgres"]["panel_schema_sentinel_count"] == 2
    assert report["postgres"]["papu_schema_sentinel_count"] == 2
    assert report["isolation"]["separate_container"] is True
    assert report["isolation"]["separate_volume"] is True
    assert report["isolation"]["network_none"] is True
    assert report["isolation"]["scratch_resources_cleanup_verified"] is True

    docker_log = _jsonl(Path(restore_harness["docker_log"]))
    strict_restores = [row for row in docker_log if isinstance(row, dict) and row.get("strict")]
    assert len(strict_restores) == 2
    assert strict_restores[0]["database"] != strict_restores[1]["database"]
    by_role = {
        "panel" if "panel" in row["database"] else "papu": row["stdin_sha256"]
        for row in strict_restores
    }
    assert by_role == {
        "panel": restore_harness["panel_sql_sha256"],
        "papu": restore_harness["papu_sql_sha256"],
    }
    argv_rows = [row["argv"] for row in docker_log if isinstance(row, dict) and "argv" in row]
    assert any(row[:2] == ["volume", "create"] for row in argv_rows)
    assert any(row[:2] == ["volume", "rm"] for row in argv_rows)
    assert any(row[0] == "rm" for row in argv_rows)
    run_argv = next(row for row in argv_rows if row[0] == "run")
    assert run_argv[run_argv.index("--network") + 1] == "none"
    assert run_argv[run_argv.index("--pull") + 1] == "never"
    assert "-p" not in run_argv and "--publish" not in run_argv
    labels = [run_argv[index + 1] for index, value in enumerate(run_argv) if value == "--label"]
    assert "a360.dr0.scratch=true" in labels
    assert f"a360.dr0.run_id={RUN_ID}" in labels
    mounts = [run_argv[index + 1] for index, value in enumerate(run_argv) if value == "-v"]
    assert len(mounts) == 1
    assert not mounts[0].startswith("/")
    assert mounts[0].endswith(":/var/lib/postgresql/data")

    restic_calls = _jsonl(Path(restore_harness["restic_log"]))
    restore_call = next(row for row in restic_calls if row[0] == "restore")
    assert restore_call[1] == "b" * 64
    assert restore_call[1] != "latest"

    final_state = json.loads(Path(restore_harness["docker_state"]).read_text(encoding="utf-8"))
    assert final_state["container"] is None
    assert final_state["volume"] is None


def test_encrypted_papu_uses_decrypt_path_without_plain_fallback(
    restore_harness: dict[str, object],
) -> None:
    papu_dir = Path(restore_harness["papu_dir"])
    plain = next(papu_dir.glob("papu_*.sql.gz"))
    encrypted = papu_dir / "papu_20260711.sql.gz.enc"
    encrypted.write_bytes(plain.read_bytes())
    newer = int(datetime.now(timezone.utc).timestamp()) - 60
    older = newer - 60
    os.utime(plain, (older, older))
    os.utime(encrypted, (newer, newer))

    result = _run(restore_harness)

    assert result.returncode == 0, result.stderr
    assert _report(restore_harness)["artifacts"]["papu_dump_format"] == "encrypted"
    openssl_calls = _jsonl(Path(restore_harness["openssl_log"]))
    assert len(openssl_calls) == 2


def test_encrypted_known_answer_uses_pinned_openssl_contract(
    restore_harness: dict[str, object],
) -> None:
    real_openssl = shutil.which("openssl")
    assert real_openssl is not None, "openssl is required by the restore contract"
    papu_dir = Path(restore_harness["papu_dir"])
    plain = next(papu_dir.glob("papu_*.sql.gz"))
    encrypted = papu_dir / "papu_known_answer.sql.gz.enc"
    encrypted.write_bytes(base64.b64decode(PAPU_ENCRYPTED_KNOWN_ANSWER_B64))
    plain.unlink()

    result = _run(
        restore_harness,
        env_update={"A360_OPENSSL_BIN": real_openssl},
    )

    assert result.returncode == 0, result.stderr
    assert _report(restore_harness)["artifacts"]["papu_dump_format"] == "encrypted"


def test_newest_encrypted_failure_is_red_and_never_falls_back(
    restore_harness: dict[str, object],
) -> None:
    papu_dir = Path(restore_harness["papu_dir"])
    plain = next(papu_dir.glob("papu_*.sql.gz"))
    encrypted = papu_dir / "papu_20260711.sql.gz.enc"
    encrypted.write_bytes(plain.read_bytes())
    newer = int(datetime.now(timezone.utc).timestamp()) - 60
    os.utime(plain, (newer - 60, newer - 60))
    os.utime(encrypted, (newer, newer))

    result = _run(restore_harness, env_update={"FAKE_OPENSSL_FAIL": "1"})

    assert result.returncode != 0
    assert "papu_decrypt_or_integrity_failed" in result.stderr
    assert "PASS" not in result.stdout
    assert not Path(restore_harness["target"]).exists()
    assert not Path(restore_harness["docker_log"]).exists()


def test_equal_newest_papu_formats_are_ambiguous_and_red(
    restore_harness: dict[str, object],
) -> None:
    papu_dir = Path(restore_harness["papu_dir"])
    plain = next(papu_dir.glob("papu_*.sql.gz"))
    encrypted = papu_dir / "papu_same_second.sql.gz.enc"
    encrypted.write_bytes(plain.read_bytes())
    same = int(datetime.now(timezone.utc).timestamp()) - 60
    os.utime(plain, (same, same))
    os.utime(encrypted, (same, same))

    result = _run(restore_harness)

    assert result.returncode != 0
    assert "papu_dump_missing_or_ambiguous" in result.stderr
    assert not Path(restore_harness["target"]).exists()


def test_sql_error_is_nonzero_and_cleanup_is_exact(
    restore_harness: dict[str, object],
) -> None:
    result = _run(restore_harness, env_update={"FAKE_SQL_FAIL": "panel"})

    assert result.returncode != 0
    assert "panel_strict_sql_restore_failed" in result.stderr
    assert "PASS" not in result.stdout
    assert SENSITIVE_CANARY not in result.stdout + result.stderr
    assert not Path(restore_harness["target"]).exists()
    docker_log = _jsonl(Path(restore_harness["docker_log"]))
    strict = [row for row in docker_log if isinstance(row, dict) and row.get("strict")]
    assert len(strict) == 1 and strict[0]["strict"] is True
    argv_rows = [row["argv"] for row in docker_log if isinstance(row, dict) and "argv" in row]
    assert any(row[0] == "rm" for row in argv_rows)
    assert any(row[:2] == ["volume", "rm"] for row in argv_rows)
    assert all("prune" not in row for row in argv_rows)


def test_on_error_stop_mutation_proves_false_success_without_tripwire(
    restore_harness: dict[str, object],
) -> None:
    mutant = Path(restore_harness["tmp"]) / "restore_mutant.sh"
    original = SCRIPT.read_text(encoding="utf-8")
    mutated = original.replace("ON_ERROR_STOP=1", "ON_ERROR_STOP=0")
    assert mutated != original
    mutant.write_text(mutated, encoding="utf-8")
    mutant.chmod(0o700)

    result = _run(
        restore_harness,
        script=mutant,
        env_update={"FAKE_SQL_FAIL": "panel"},
    )

    # Causal mutation probe: bez tripwire realna semantyka psql moze ukryc blad
    # statementu i caly drill daje falszywy PASS. Known-answer dla prawdziwego
    # skryptu wymaga dwoch rekordow strict=True, wiec taka mutacja lamie suite.
    assert result.returncode == 0, result.stderr
    assert "PASS scope=artifact_and_postgres_schema_only" in result.stdout
    docker_log = _jsonl(Path(restore_harness["docker_log"]))
    restore_rows = [row for row in docker_log if isinstance(row, dict) and "strict" in row]
    assert len(restore_rows) == 2
    assert all(row["strict"] is False for row in restore_rows)


@pytest.mark.parametrize(
    "relative_path",
    REQUIRED_MANIFEST_PATHS,
)
def test_missing_required_artifact_is_red(
    restore_harness: dict[str, object], relative_path: str
) -> None:
    (Path(restore_harness["fixture"]) / relative_path).unlink()

    result = _run(restore_harness)

    assert result.returncode != 0
    assert "required_artifact_missing_or_unsafe" in result.stderr
    assert not Path(restore_harness["docker_log"]).exists()


@pytest.mark.parametrize(
    "relative_path",
    (
        "root/.openclaw/workspace/dispatch_state/kurier_ids.json",
        f"root/.openclaw/workspace/{PRIVATE_CONFIG_NAME}",
        "etc/systemd/system/dispatch-shadow.service",
        "etc/nginx/sites-available/gps-nadajesz",
    ),
)
def test_empty_required_metadata_artifact_is_red(
    restore_harness: dict[str, object], relative_path: str
) -> None:
    (Path(restore_harness["fixture"]) / relative_path).write_bytes(b"")

    result = _run(restore_harness)

    assert result.returncode != 0
    assert "required_artifact_empty" in result.stderr
    assert not Path(restore_harness["docker_log"]).exists()


@pytest.mark.parametrize("role", ["systemd", "nginx"])
def test_nonempty_directory_with_only_synthetic_decoy_is_red(
    restore_harness: dict[str, object], role: str
) -> None:
    fixture = Path(restore_harness["fixture"])
    if role == "systemd":
        directory = fixture / "etc/systemd/system"
        for relative in REQUIRED_SYSTEMD_PATHS:
            (fixture / relative).unlink()
        (directory / "synthetic.service").write_text("[Unit]\n", encoding="utf-8")
    else:
        directory = fixture / "etc/nginx/sites-available"
        for relative in REQUIRED_NGINX_PATHS:
            (fixture / relative).unlink()
        (directory / "synthetic.conf").write_text("# decoy\n", encoding="utf-8")

    result = _run(restore_harness)

    assert result.returncode != 0
    assert "required_artifact_missing_or_unsafe" in result.stderr
    assert not Path(restore_harness["docker_log"]).exists()


def test_manifest_entry_removal_mutation_is_definition_invalid(
    restore_harness: dict[str, object],
) -> None:
    mutant = Path(restore_harness["tmp"]) / "restore_manifest_removal_mutant.sh"
    original = SCRIPT.read_text(encoding="utf-8")
    mutated = original.replace(
        '  "etc/systemd/system/dispatch-shadow.service"\n', "", 1
    )
    assert mutated != original
    mutant.write_text(mutated, encoding="utf-8")
    mutant.chmod(0o700)

    result = _run(restore_harness, script=mutant)

    assert result.returncode == 2
    assert "required_contract_definition_invalid" in result.stderr
    assert not Path(restore_harness["restic_log"]).exists()


def test_manifest_duplicate_mutation_is_definition_invalid(
    restore_harness: dict[str, object],
) -> None:
    mutant = Path(restore_harness["tmp"]) / "restore_manifest_duplicate_mutant.sh"
    original = SCRIPT.read_text(encoding="utf-8")
    mutated = original.replace(
        '  "etc/systemd/system/dispatch-shadow.service"\n',
        '  "etc/systemd/system/dispatch-gps.service"\n',
        1,
    )
    assert mutated != original
    mutant.write_text(mutated, encoding="utf-8")
    mutant.chmod(0o700)

    result = _run(restore_harness, script=mutant)

    assert result.returncode == 2
    assert "required_contract_definition_invalid" in result.stderr
    assert not Path(restore_harness["restic_log"]).exists()


def test_required_artifact_symlink_escape_is_red(
    restore_harness: dict[str, object],
) -> None:
    orders = Path(restore_harness["state"]) / "orders_state.json"
    external = Path(restore_harness["tmp"]) / "external.json"
    external.write_text('{"synthetic": true}', encoding="utf-8")
    orders.unlink()
    orders.symlink_to(external)

    result = _run(restore_harness)

    assert result.returncode != 0
    assert "artifact_ancestor_symlink_rejected" in result.stderr
    assert SENSITIVE_CANARY not in result.stdout + result.stderr


def test_required_artifact_ancestor_symlink_escape_is_red(
    restore_harness: dict[str, object],
) -> None:
    fixture = Path(restore_harness["fixture"])
    openclaw = fixture / "root/.openclaw"
    external = Path(restore_harness["tmp"]) / "external_openclaw"
    shutil.move(str(openclaw), str(external))
    openclaw.symlink_to(external, target_is_directory=True)

    result = _run(restore_harness)

    assert result.returncode != 0
    assert "artifact_ancestor_symlink_rejected" in result.stderr
    assert not Path(restore_harness["docker_log"]).exists()


@pytest.mark.parametrize(
    ("role", "reason"),
    [
        ("panel", "panel_dump_missing_or_ambiguous"),
        ("papu", "papu_dump_missing_or_ambiguous"),
    ],
)
def test_missing_role_artifact_is_red(
    restore_harness: dict[str, object], role: str, reason: str
) -> None:
    if role == "panel":
        next(Path(restore_harness["panel_dir"]).glob("*.sql.gz")).unlink()
    else:
        next(Path(restore_harness["papu_dir"]).glob("*.sql.gz")).unlink()

    result = _run(restore_harness)

    assert result.returncode != 0
    assert reason in result.stderr
    assert not Path(restore_harness["docker_log"]).exists()


def test_sqlite_requires_canonical_event_bus_schema(
    restore_harness: dict[str, object],
) -> None:
    database = Path(restore_harness["state"]) / "events.db"
    database.unlink()
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE unrelated(id INTEGER PRIMARY KEY)")

    result = _run(restore_harness)

    assert result.returncode != 0
    assert "sqlite_required_schema_missing" in result.stderr
    assert not Path(restore_harness["docker_log"]).exists()


def test_sqlite_missing_required_column_is_red(
    restore_harness: dict[str, object],
) -> None:
    database = Path(restore_harness["state"]) / "events.db"
    database.unlink()
    with sqlite3.connect(database) as connection:
        connection.executescript(
            "CREATE TABLE events("
            "event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, order_id TEXT, "
            "courier_id TEXT, payload TEXT, created_at TEXT NOT NULL, processed_at TEXT);"
            "CREATE TABLE processed_events("
            "event_id TEXT PRIMARY KEY, processed_at TEXT NOT NULL);"
            "CREATE TABLE audit_log("
            "event_id TEXT PRIMARY KEY, event_type TEXT NOT NULL, order_id TEXT, "
            "courier_id TEXT, payload TEXT, created_at TEXT NOT NULL);"
        )

    result = _run(restore_harness)

    assert result.returncode != 0
    assert "sqlite_required_columns_missing" in result.stderr
    assert not Path(restore_harness["docker_log"]).exists()


def test_valid_but_empty_sql_dump_is_red(
    restore_harness: dict[str, object],
) -> None:
    panel = next(Path(restore_harness["panel_dir"]).glob("*.sql.gz"))
    panel.write_bytes(gzip.compress(b""))

    result = _run(restore_harness)

    assert result.returncode != 0
    assert "sql_dump_empty" in result.stderr
    assert not Path(restore_harness["docker_log"]).exists()


@pytest.mark.parametrize("kind", ["json", "sqlite", "gzip"])
def test_corrupt_artifact_is_red(
    restore_harness: dict[str, object], kind: str
) -> None:
    if kind == "json":
        (Path(restore_harness["state"]) / "orders_state.json").write_text(
            '{"duplicate": 1, "duplicate": 2}', encoding="utf-8"
        )
    elif kind == "sqlite":
        (Path(restore_harness["state"]) / "events.db").write_bytes(b"not sqlite")
    else:
        next(Path(restore_harness["panel_dir"]).glob("*.sql.gz")).write_bytes(b"bad gzip")

    result = _run(restore_harness)

    assert result.returncode != 0
    assert "PASS" not in result.stdout
    assert not Path(restore_harness["docker_log"]).exists()


@pytest.mark.parametrize(
    "extra_args",
    [
        ("--force",),
        ("--load-db", "nadajesz_panel"),
        ("--panel-db", "nadajesz_panel"),
        ("--pg-container", "papu-postgres"),
    ],
)
def test_legacy_force_and_production_targets_are_rejected_before_restic(
    restore_harness: dict[str, object], extra_args: tuple[str, ...]
) -> None:
    result = _run(restore_harness, extra_args=extra_args)

    assert result.returncode == 2
    assert "unsafe_legacy_option_rejected" in result.stderr
    assert not Path(restore_harness["restic_log"]).exists()
    assert not Path(restore_harness["docker_log"]).exists()


def test_target_outside_scratch_is_rejected_before_restic(
    restore_harness: dict[str, object],
) -> None:
    outside = Path(restore_harness["tmp"]) / "restore_outside"

    result = _run(restore_harness, extra_args=("--target", str(outside)))

    assert result.returncode != 0
    assert "target_outside_scratch" in result.stderr
    assert not outside.exists()
    assert not Path(restore_harness["restic_log"]).exists()


def test_existing_target_is_rejected_before_restic(
    restore_harness: dict[str, object],
) -> None:
    target = Path(restore_harness["target"])
    target.mkdir(parents=True)

    result = _run(restore_harness)

    assert result.returncode != 0
    assert "target_must_be_new" in result.stderr
    assert not Path(restore_harness["restic_log"]).exists()


@pytest.mark.parametrize(
    ("env_update", "reason"),
    [
        ({"A360_TEST_CONFLICT_PROCESS": "1"}, "concurrent_heavy_job_detected"),
        ({"A360_TEST_LOAD1": "2.1", "A360_TEST_CPU_COUNT": "4"}, "host_load_too_high"),
        ({"A360_TEST_MEM_AVAILABLE_BYTES": "1024"}, "host_memory_too_low"),
    ],
)
def test_host_preflight_guards_stop_before_restic(
    restore_harness: dict[str, object], env_update: dict[str, str], reason: str
) -> None:
    result = _run(restore_harness, env_update=env_update)

    assert result.returncode != 0
    assert reason in result.stderr
    assert not Path(restore_harness["restic_log"]).exists()
    assert not Path(restore_harness["docker_log"]).exists()


def test_scratch_capacity_guard_stops_before_restore(
    restore_harness: dict[str, object],
) -> None:
    result = _run(restore_harness, env_update={"A360_TEST_FREE_BYTES": "1"})

    assert result.returncode != 0
    assert "scratch_disk_capacity_too_low" in result.stderr
    calls = _jsonl(Path(restore_harness["restic_log"]))
    assert [row[0] for row in calls] == ["snapshots", "check", "stats"]
    assert not Path(restore_harness["docker_log"]).exists()


def test_snapshot_stats_underreport_cannot_bypass_post_unpack_budget(
    restore_harness: dict[str, object],
) -> None:
    result = _run(
        restore_harness,
        mode="artifact",
        env_update={
            "FAKE_SNAPSHOT_TOTAL_SIZE": "1000",
            "A360_DR0_MIN_FREE_RESERVE_BYTES": "1",
            "A360_DR0_SCRATCH_BUDGET_BYTES": "4096",
        },
    )

    assert result.returncode != 0
    assert "scratch_budget_exceeded_after_unpack" in result.stderr
    calls = _jsonl(Path(restore_harness["restic_log"]))
    assert [row[0] for row in calls] == ["snapshots", "check", "stats", "restore"]
    assert not Path(restore_harness["target"]).exists()
    assert not Path(restore_harness["docker_log"]).exists()


def test_docker_free_space_guard_stops_before_unpack(
    restore_harness: dict[str, object],
) -> None:
    result = _run(
        restore_harness,
        env_update={"A360_TEST_DOCKER_FREE_BYTES": "1"},
    )

    assert result.returncode == 20
    assert "docker_disk_capacity_too_low_before_unpack" in result.stderr
    assert not Path(restore_harness["docker_log"]).exists()
    assert not Path(restore_harness["target"]).exists()


@pytest.mark.parametrize(
    ("mode", "env_update", "reason"),
    [
        ("artifact", {"A360_DR0_SCRATCH_BUDGET_BYTES": ""}, "scratch_budget_required_or_invalid"),
        ("artifact", {"A360_DR0_SCRATCH_BUDGET_BYTES": "0"}, "scratch_budget_required_or_invalid"),
        ("drill", {"A360_DR0_DOCKER_BUDGET_BYTES": ""}, "docker_budget_required_or_invalid"),
        ("drill", {"A360_DR0_DOCKER_BUDGET_BYTES": "not-a-number"}, "docker_budget_required_or_invalid"),
    ],
)
def test_capacity_budget_is_required_and_strictly_validated(
    restore_harness: dict[str, object],
    mode: str,
    env_update: dict[str, str],
    reason: str,
) -> None:
    result = _run(restore_harness, mode=mode, env_update=env_update)

    assert result.returncode == 2
    assert reason in result.stderr
    assert not Path(restore_harness["restic_log"]).exists()
    assert not Path(restore_harness["docker_log"]).exists()


@pytest.mark.parametrize(
    ("env_update", "reason"),
    [
        ({"A360_DR0_SCRATCH_BUDGET_BYTES": "1"}, "scratch_budget_exceeded"),
        ({"A360_DR0_DOCKER_BUDGET_BYTES": "1"}, "docker_budget_exceeded_before_unpack"),
        ({"FAKE_SNAPSHOT_TOTAL_SIZE": str(2**63)}, "capacity_arithmetic_unsafe"),
    ],
)
def test_pre_unpack_capacity_negative_controls_are_red(
    restore_harness: dict[str, object], env_update: dict[str, str], reason: str
) -> None:
    result = _run(restore_harness, env_update=env_update)

    assert result.returncode != 0
    assert reason in result.stderr
    calls = _jsonl(Path(restore_harness["restic_log"]))
    assert [row[0] for row in calls] == ["snapshots", "check", "stats"]
    assert not Path(restore_harness["docker_log"]).exists()


def test_shared_device_combined_capacity_is_checked_before_unpack(
    restore_harness: dict[str, object],
) -> None:
    result = _run(
        restore_harness,
        env_update={
            "FAKE_SNAPSHOT_TOTAL_SIZE": "1000",
            "A360_DR0_MIN_FREE_RESERVE_BYTES": "1000",
            "A360_DR0_SCRATCH_BUDGET_BYTES": "100000",
            "A360_DR0_DOCKER_BUDGET_BYTES": "100000",
            "A360_TEST_FREE_BYTES": "6000",
            "A360_TEST_DOCKER_FREE_BYTES": "6000",
            "A360_TEST_SAME_DEVICE": "1",
        },
    )

    assert result.returncode == 20
    assert "shared_device_capacity_too_low_before_unpack" in result.stderr
    calls = _jsonl(Path(restore_harness["restic_log"]))
    assert [row[0] for row in calls] == ["snapshots", "check", "stats"]
    assert not Path(restore_harness["docker_log"]).exists()


def test_decompression_expansion_must_fit_docker_budget_before_volume(
    restore_harness: dict[str, object],
) -> None:
    large_comment = b"x" * 1_000_000
    panel = next(Path(restore_harness["panel_dir"]).glob("*.sql.gz"))
    papu = next(Path(restore_harness["papu_dir"]).glob("*.sql.gz"))
    panel.write_bytes(gzip.compress(b"CREATE TABLE synthetic_panel(id integer); -- " + large_comment))
    papu.write_bytes(gzip.compress(b"CREATE TABLE synthetic_papu(id integer); -- " + large_comment))

    result = _run(
        restore_harness,
        env_update={
            "FAKE_SNAPSHOT_TOTAL_SIZE": "1000",
            "A360_DR0_MIN_FREE_RESERVE_BYTES": "1",
            "A360_DR0_SCRATCH_BUDGET_BYTES": "10000000",
            "A360_DR0_DOCKER_BUDGET_BYTES": "100000",
        },
    )

    assert result.returncode == 20
    assert "docker_budget_exceeded_after_decompress" in result.stderr
    assert not Path(restore_harness["docker_log"]).exists()
    assert not Path(restore_harness["target"]).exists()


def test_docker_free_space_is_rechecked_after_decompression_before_volume(
    restore_harness: dict[str, object],
) -> None:
    result = _run(
        restore_harness,
        env_update={"A360_TEST_DOCKER_FREE_BYTES_AFTER_DECOMPRESS": "1"},
    )

    assert result.returncode == 20
    assert "docker_disk_capacity_too_low_after_decompress" in result.stderr
    assert not Path(restore_harness["docker_log"]).exists()
    assert not Path(restore_harness["target"]).exists()


def test_scratch_budget_enforcement_mutation_proves_false_success(
    restore_harness: dict[str, object],
) -> None:
    mutant = Path(restore_harness["tmp"]) / "restore_budget_mutant.sh"
    original = SCRIPT.read_text(encoding="utf-8")
    mutated = original.replace(
        '  || fail "scratch_budget_exceeded"\n', '  || true # mutation probe\n', 1
    ).replace(
        '  || fail "scratch_budget_exceeded_after_unpack"\n',
        '  || true # mutation probe\n',
        1,
    )
    assert mutated != original
    mutant.write_text(mutated, encoding="utf-8")
    mutant.chmod(0o700)

    result = _run(
        restore_harness,
        mode="artifact",
        script=mutant,
        env_update={"A360_DR0_SCRATCH_BUDGET_BYTES": "1"},
    )

    assert result.returncode == 0, result.stderr
    assert "PASS scope=artifact_integrity_only" in result.stdout


def test_network_none_mutation_is_rejected_by_stateful_fake_inspect(
    restore_harness: dict[str, object],
) -> None:
    mutant = Path(restore_harness["tmp"]) / "restore_network_mutant.sh"
    original = SCRIPT.read_text(encoding="utf-8")
    mutated = original.replace("    --network none \\\n", "", 1)
    assert mutated != original
    mutant.write_text(mutated, encoding="utf-8")
    mutant.chmod(0o700)

    result = _run(restore_harness, script=mutant)

    assert result.returncode != 0
    assert "scratch_container_networked" in result.stderr
    assert not Path(restore_harness["target"]).exists()


def test_panel_papu_swap_mutation_is_rejected_by_schema_identity(
    restore_harness: dict[str, object],
) -> None:
    mutant = Path(restore_harness["tmp"]) / "restore_role_swap_mutant.sh"
    original = SCRIPT.read_text(encoding="utf-8")
    mutated = original.replace(
        'restore_postgres_role "panel" "plain" "$PANEL_DUMP" "$PANEL_DB"',
        'restore_postgres_role "panel" "plain" "$PAPU_DUMP" "$PANEL_DB"',
        1,
    )
    assert mutated != original
    mutant.write_text(mutated, encoding="utf-8")
    mutant.chmod(0o700)

    result = _run(restore_harness, script=mutant)

    assert result.returncode != 0
    assert "panel_schema_identity_failed" in result.stderr
    assert not Path(restore_harness["target"]).exists()


def test_second_host_guard_closes_race_before_docker(
    restore_harness: dict[str, object],
) -> None:
    result = _run(
        restore_harness,
        env_update={"A360_TEST_SECOND_CONFLICT_PROCESS": "1"},
    )

    assert result.returncode != 0
    assert "concurrent_heavy_job_detected" in result.stderr
    calls = _jsonl(Path(restore_harness["docker_log"]))
    assert calls == []
    assert not Path(restore_harness["target"]).exists()


def test_artifact_mode_also_honors_heavy_job_guard(
    restore_harness: dict[str, object],
) -> None:
    result = _run(
        restore_harness,
        mode="artifact",
        env_update={"A360_TEST_CONFLICT_PROCESS": "1"},
    )

    assert result.returncode != 0
    assert "concurrent_heavy_job_detected" in result.stderr
    assert not Path(restore_harness["restic_log"]).exists()


def test_foreign_volume_label_is_never_mounted_or_deleted(
    restore_harness: dict[str, object],
) -> None:
    result = _run(
        restore_harness,
        env_update={"FAKE_DOCKER_FOREIGN_VOLUME_LABEL": "1"},
    )

    assert result.returncode == 90
    assert "scratch_volume_run_id_invalid" in result.stderr
    assert "scratch_rollback_incomplete" in result.stderr
    calls = _jsonl(Path(restore_harness["docker_log"]))
    argv_rows = [row["argv"] for row in calls if isinstance(row, dict) and "argv" in row]
    assert not any(row[0] == "run" for row in argv_rows)
    assert not any(row[:2] == ["volume", "rm"] for row in argv_rows)


def test_cleanup_failure_is_reported_as_rollback_incomplete(
    restore_harness: dict[str, object],
) -> None:
    result = _run(
        restore_harness,
        env_update={"FAKE_SQL_FAIL": "panel", "FAKE_DOCKER_FAIL": "rm_container"},
    )

    assert result.returncode == 90
    assert "panel_strict_sql_restore_failed" in result.stderr
    assert "scratch_rollback_incomplete" in result.stderr


@pytest.mark.parametrize(
    ("delta", "reason"),
    [
        (timedelta(hours=-27), "snapshot_stale"),
        (timedelta(minutes=10), "snapshot_from_future"),
    ],
)
def test_snapshot_time_guards_are_red(
    restore_harness: dict[str, object], delta: timedelta, reason: str
) -> None:
    snapshot_time = (datetime.now(timezone.utc) + delta).isoformat()

    result = _run(
        restore_harness,
        env_update={"FAKE_SNAPSHOT_TIME": snapshot_time},
    )

    assert result.returncode != 0
    assert reason in result.stderr
    assert not Path(restore_harness["docker_log"]).exists()


def test_latest_snapshot_resolves_unique_global_newest_across_groups(
    restore_harness: dict[str, object],
) -> None:
    result = _run(
        restore_harness,
        mode="artifact",
        env_update={"FAKE_MULTIPLE_SNAPSHOTS": "1"},
    )

    assert result.returncode == 0, result.stderr
    calls = _jsonl(Path(restore_harness["restic_log"]))
    restore_call = next(row for row in calls if row[0] == "restore")
    assert restore_call[1] == "b" * 64


def test_tied_newest_snapshots_are_ambiguous_and_red(
    restore_harness: dict[str, object],
) -> None:
    result = _run(
        restore_harness,
        env_update={"FAKE_TIED_SNAPSHOTS": "1"},
    )

    assert result.returncode != 0
    assert "invalid_snapshot_metadata" in result.stderr
    assert not Path(restore_harness["docker_log"]).exists()


def test_fresh_snapshot_with_stale_database_dump_is_red(
    restore_harness: dict[str, object],
) -> None:
    stale = int((datetime.now(timezone.utc) - timedelta(hours=27)).timestamp())
    panel = next(Path(restore_harness["panel_dir"]).glob("*.sql.gz"))
    os.utime(panel, (stale, stale))

    result = _run(restore_harness)

    assert result.returncode != 0
    assert "panel_dump_stale" in result.stderr
    assert not Path(restore_harness["docker_log"]).exists()


def test_private_credential_permissions_are_fail_closed(
    restore_harness: dict[str, object],
) -> None:
    Path(restore_harness["password_file"]).chmod(0o644)

    result = _run(restore_harness)

    assert result.returncode != 0
    assert "restic_credential_unavailable_or_unsafe" in result.stderr
    assert not Path(restore_harness["restic_log"]).exists()


@pytest.mark.parametrize("failure", ["snapshots", "check", "stats", "restore"])
def test_restic_failures_are_red(
    restore_harness: dict[str, object], failure: str
) -> None:
    result = _run(restore_harness, env_update={"FAKE_RESTIC_FAIL": failure})

    assert result.returncode != 0
    assert "PASS" not in result.stdout
    assert not Path(restore_harness["docker_log"]).exists()
    assert not Path(restore_harness["target"]).exists()


def test_artifact_mode_reports_limited_scope_and_never_calls_docker(
    restore_harness: dict[str, object],
) -> None:
    result = _run(restore_harness, mode="artifact")

    assert result.returncode == 0, result.stderr
    report = _report(restore_harness)
    assert report["evidence_scope"] == "artifact_integrity_only"
    assert report["full_service_recovery_proven"] is False
    assert report["service_rto"]["status"] == "HOLD"
    assert report["recovery_timing"]["to_postgres_schema_smoke_seconds"] is None
    assert report["isolation"]["separate_container"] is False
    assert not Path(restore_harness["docker_log"]).exists()


def test_service_rto_claim_mutation_is_exposed_by_known_answer(
    restore_harness: dict[str, object],
) -> None:
    mutant = Path(restore_harness["tmp"]) / "restore_rto_claim_mutant.sh"
    original = SCRIPT.read_text(encoding="utf-8")
    mutated = original.replace(
        '    "full_service_recovery_proven": False,\n',
        '    "full_service_recovery_proven": True,\n',
        1,
    )
    assert mutated != original
    mutant.write_text(mutated, encoding="utf-8")
    mutant.chmod(0o700)

    result = _run(restore_harness, mode="artifact", script=mutant)

    assert result.returncode == 0, result.stderr
    # Causal mutation probe: taki raport jest technicznie zapisywalny, ale
    # lamie known-answer prawdziwego skryptu, ktory wymaga jawnego False/HOLD.
    assert _report(restore_harness)["full_service_recovery_proven"] is True


def test_dr_docs_use_current_cli_and_mark_source_as_not_installed() -> None:
    obsolete_tokens = ("--verify" + "-only", "--load" + "-db", "--for" + "ce")
    for path in (README, RUNBOOK):
        text = path.read_text(encoding="utf-8")
        assert all(token not in text for token in obsolete_tokens)
        assert "--mode verify" in text
        assert "--mode artifact" in text
        assert "--mode drill" in text
        assert "NIEWDRO" in text or "nie jest zainstalowana live" in text
        assert "service RTO" in text


def test_source_and_report_never_claim_full_isolated_rto() -> None:
    for path in (SCRIPT, README, RUNBOOK, REPO_ROOT / "eod_drafts/2026-07-11/A360_DR0_RESTORE.md"):
        text = path.read_text(encoding="utf-8")
        assert "full_isolated_drill" not in text


def test_verify_mode_checks_repository_without_restore_or_docker(
    restore_harness: dict[str, object],
) -> None:
    result = _run(restore_harness, mode="verify")

    assert result.returncode == 0, result.stderr
    assert "PASS scope=repository_check" in result.stdout
    restic_calls = _jsonl(Path(restore_harness["restic_log"]))
    assert [row[0] for row in restic_calls] == ["snapshots", "check"]
    assert not Path(restore_harness["target"]).exists()
    assert not list(Path(restore_harness["scratch"]).glob(".verify_cache.*"))
    assert not Path(restore_harness["docker_log"]).exists()


def test_unavailable_isolated_docker_stops_after_artifact_integrity(
    restore_harness: dict[str, object],
) -> None:
    result = _run(restore_harness, env_update={"FAKE_DOCKER_FAIL": "image"})

    assert result.returncode == 20
    assert "pinned_pg_image_unavailable" in result.stderr
    assert "PASS" not in result.stdout
    restic_calls = _jsonl(Path(restore_harness["restic_log"]))
    assert [row[0] for row in restic_calls] == ["snapshots", "check", "stats", "restore"]
    assert not Path(restore_harness["target"]).exists()
