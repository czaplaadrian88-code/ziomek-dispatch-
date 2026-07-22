"""Canonical rename-based JSONL rotation with legacy-inode fencing.

The wrapper holds every cooperative ``<name>.append.lock`` and then proves
that no process still has an active or rotated JSONL inode open.  Logrotate
uses rename+create, never copytruncate: a legacy writer racing after the scan
either keeps the renamed inode (which remains linked) or opens the newly
created active path.  On the next run an inherited old fd blocks compression,
renumbering and retention until it closes.  This makes rollout and rollback
safe without trusting a process marker or an operator-created ACK file.
"""
from __future__ import annotations

import argparse
import fcntl
import os
import subprocess
from contextlib import contextmanager
from pathlib import Path
from typing import Iterable, Iterator, Sequence


JSONL_PATHS: tuple[str, ...] = (
    "/root/.openclaw/workspace/dispatch_state/learning_log.jsonl",
    "/root/.openclaw/workspace/dispatch_state/v319c_read_shadow_log.jsonl",
    "/root/.openclaw/workspace/scripts/logs/shadow_decisions.jsonl",
    "/root/.openclaw/workspace/scripts/logs/sla_log.jsonl",
    "/root/.openclaw/workspace/dispatch_state/consumer_stuck_alert_evaluations.jsonl",
    "/root/.openclaw/workspace/dispatch_state/obj_replay_capture.jsonl",
    "/root/.openclaw/workspace/dispatch_state/eta_calibration_log.jsonl",
    "/root/.openclaw/workspace/dispatch_state/drive_min_enriched.jsonl",
    "/root/.openclaw/workspace/dispatch_state/drive_min_calibration_log_v2.jsonl",
    "/root/.openclaw/workspace/dispatch_state/plan_recheck_log.jsonl",
    "/root/.openclaw/workspace/dispatch_state/czasowka_eval_log.jsonl",
    "/root/.openclaw/workspace/dispatch_state/decision_eta_log.jsonl",
    "/root/.openclaw/workspace/dispatch_state/czasowka_reclaim_shadow.jsonl",
    "/root/.openclaw/workspace/scripts/logs/geocoding_log.jsonl",
)


class OpenJsonlInodeError(RuntimeError):
    """Rotation cannot prove that every legacy data-file descriptor is closed."""


def _rotation_candidates(paths: Iterable[str | os.PathLike]) -> list[Path]:
    candidates: set[Path] = set()
    for raw_path in paths:
        path = Path(raw_path)
        if path.exists():
            candidates.add(path)
        try:
            siblings = path.parent.glob(path.name + ".[0-9]*")
            candidates.update(
                candidate for candidate in siblings if candidate.is_file()
            )
        except OSError as exc:
            raise OpenJsonlInodeError(
                f"cannot enumerate JSONL rotations for {path}: {exc}"
            ) from exc
    return sorted(candidates)


def assert_no_open_jsonl_inodes(
    paths: Iterable[str | os.PathLike],
    *,
    proc_root: str | os.PathLike = "/proc",
) -> None:
    """Fail closed if any process references an inode logrotate may move/remove."""
    identities: dict[tuple[int, int], Path] = {}
    for candidate in _rotation_candidates(paths):
        try:
            stat_result = candidate.stat()
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise OpenJsonlInodeError(
                f"cannot stat JSONL candidate {candidate}: {exc}"
            ) from exc
        identities[(stat_result.st_dev, stat_result.st_ino)] = candidate
    if not identities:
        return

    try:
        processes = list(Path(proc_root).iterdir())
    except OSError as exc:
        raise OpenJsonlInodeError(f"cannot enumerate process fds: {exc}") from exc
    for process in processes:
        if not process.name.isdigit():
            continue
        try:
            descriptors = list((process / "fd").iterdir())
        except FileNotFoundError:
            continue
        except OSError as exc:
            raise OpenJsonlInodeError(
                f"cannot inspect process {process.name} fds: {exc}"
            ) from exc
        for descriptor in descriptors:
            try:
                opened = descriptor.stat()
            except FileNotFoundError:
                continue
            except OSError as exc:
                raise OpenJsonlInodeError(
                    f"cannot inspect process fd {descriptor}: {exc}"
                ) from exc
            identity = (opened.st_dev, opened.st_ino)
            if identity in identities:
                raise OpenJsonlInodeError(
                    "JSONL inode still open; rotation deferred: "
                    f"pid={process.name} path={identities[identity]}"
                )


@contextmanager
def hold_jsonl_rotation_locks(
    paths: Iterable[str | os.PathLike],
) -> Iterator[None]:
    """Hold all writer namespace locks in deterministic pathname order."""
    lock_fds: list[int] = []
    try:
        normalized = sorted({str(Path(path)) for path in paths})
        for raw_path in normalized:
            path = Path(raw_path)
            path.parent.mkdir(parents=True, exist_ok=True)
            lock_path = path.with_name(path.name + ".append.lock")
            fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX)
            except Exception:
                os.close(fd)
                raise
            lock_fds.append(fd)
        yield
    finally:
        for fd in reversed(lock_fds):
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            finally:
                os.close(fd)


def run_logrotate(
    config_path: str,
    *,
    logrotate_bin: str = "/usr/sbin/logrotate",
    extra_args: Sequence[str] = (),
    paths: Iterable[str | os.PathLike] = JSONL_PATHS,
    proc_root: str | os.PathLike = "/proc",
) -> int:
    """Run canonical logrotate after lock + direct open-inode attestation."""
    command = [str(logrotate_bin), *map(str, extra_args), str(config_path)]
    normalized_paths = tuple(paths)
    with hold_jsonl_rotation_locks(normalized_paths):
        assert_no_open_jsonl_inodes(normalized_paths, proc_root=proc_root)
        completed = subprocess.run(command, check=False)
    return int(completed.returncode)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", nargs="?", default="/etc/logrotate.conf")
    parser.add_argument("--logrotate-bin", default="/usr/sbin/logrotate")
    args, extra = parser.parse_known_args(argv)
    return run_logrotate(
        args.config,
        logrotate_bin=args.logrotate_bin,
        extra_args=extra,
    )


if __name__ == "__main__":
    raise SystemExit(main())
