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
    "/root/.openclaw/workspace/dispatch_state/live_eta_history.jsonl",
    "/root/.openclaw/workspace/dispatch_state/czasowka_reclaim_shadow.jsonl",
    "/root/.openclaw/workspace/dispatch_state/uwagi_bridge_envelope.jsonl",
    "/root/.openclaw/workspace/scripts/logs/geocoding_log.jsonl",
)


class OpenJsonlInodeError(RuntimeError):
    """Rotation cannot prove that every legacy data-file descriptor is closed."""


class RawRotationWouldBeClobberedError(RuntimeError):
    """A raw rotated slot exists that logrotate would silently overwrite.

    Incydent 2026-08-04 05:22Z: ``decision_eta_log.jsonl.1`` (407,3 MB, surowe)
    przepadło w jednym biegu, bez ani jednej linii ``removing`` w logu — bo NIE
    zostało skasowane, tylko NADPISANE przez ``rename(live, <log>.1)``.

    Mechanizm (zreprodukowany, 3 warianty): przy ``compress`` BEZ ``delaycompress``
    logrotate zakłada, że każda już zrotowana generacja jest skompresowana, więc
    w pętli przesuwającej szuka wyłącznie ``<log>.<N>.gz``. Surowe ``<log>.<N>``
    jest dla tej pętli NIEWIDOCZNE — nie zostaje przesunięte do ``.<N+1>``, a
    zaraz potem slot ``.1`` jest nadpisywany świeżą rotacją.

    Surowe ``<log>.1`` to normalny stan pod ``delaycompress``. Dlatego samo
    ZDJĘCIE ``delaycompress`` z configu, przy zaległym surowym ``.1`` na dysku,
    zamienia następny bieg w cichą utratę danych. Ten guard blokuje dokładnie
    ten układ; naprawa to ``gzip <log>.<N>`` PRZED biegiem (wariant C repro:
    po kompresji przesunięcie ``.1.gz -> .2.gz`` działa poprawnie).
    """


def _config_blocks(config_text: str) -> list[tuple[list[str], set[str]]]:
    """(ścieżki, dyrektywy) dla każdego bloku configu logrotate.

    Parser minimalny i celowo konserwatywny: interesują nas wyłącznie ścieżki
    przed ``{`` oraz obecność ``compress``/``delaycompress`` wewnątrz bloku.
    """
    blocks: list[tuple[list[str], set[str]]] = []
    pending: list[str] = []
    directives: set[str] = set()
    inside = False
    for raw_line in config_text.splitlines():
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        if inside:
            if line.startswith("}"):
                blocks.append((pending, directives))
                pending, directives, inside = [], set(), False
                continue
            directives.add(line.split()[0].lower())
            continue
        if line.startswith("{"):
            inside = True
            continue
        if line.endswith("{"):
            head = line[:-1].strip()
            if head:
                pending.extend(head.split())
            inside = True
            continue
        pending.extend(line.split())
    return blocks


def raw_rotations_at_risk(config_path: str | os.PathLike) -> list[Path]:
    """Surowe rotacje, które ten config nadpisze zamiast przesunąć.

    Pusta lista = brak zagrożenia. Config nieczytelny = pusta lista; brakującym
    configiem zajmuje się sam logrotate linijkę dalej (kończy błędem), więc nie
    dublujemy tu jego walidacji.
    """
    config = Path(config_path)
    try:
        text = config.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return []
    at_risk: list[Path] = []
    for paths, directives in _config_blocks(text):
        if "compress" not in directives or "delaycompress" in directives:
            continue  # delaycompress ZNA surowe .1 i obsługuje je poprawnie
        for pattern in paths:
            pattern = pattern.strip("\"'")
            if not pattern.startswith("/"):
                continue
            base = Path(pattern)
            matches = sorted(base.parent.glob(base.name)) if any(
                ch in base.name for ch in "*?["
            ) else [base]
            for logfile in matches:
                for candidate in sorted(logfile.parent.glob(logfile.name + ".[0-9]*")):
                    if candidate.suffix in (".gz", ".bz2", ".xz", ".zst"):
                        continue
                    if not candidate.is_file():
                        continue
                    at_risk.append(candidate)
    return at_risk


def assert_no_clobberable_raw_rotations(config_path: str | os.PathLike) -> None:
    """Fail closed, gdy bieg nadpisałby surową rotację zamiast ją przesunąć."""
    at_risk = raw_rotations_at_risk(config_path)
    if not at_risk:
        return
    opis = ", ".join(
        f"{p} ({p.stat().st_size / 1048576:.1f} MB)" for p in at_risk
    )
    naprawa = " && ".join(f"gzip {p}" for p in at_risk)
    raise RawRotationWouldBeClobberedError(
        "rotation refused: config compresses immediately (compress bez delaycompress), "
        f"a na dysku leza SUROWE rotacje, ktore logrotate nadpisze zamiast przesunac: {opis}. "
        f"Napraw je NAJPIERW kompresja, potem powtorz bieg: {naprawa}"
    )


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
        assert_no_clobberable_raw_rotations(config_path)
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
