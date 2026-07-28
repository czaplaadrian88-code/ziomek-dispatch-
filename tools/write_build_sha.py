#!/usr/bin/env python3
"""Ręczny krok deployu T5: atomowo zapisz SHA uruchomionego repo.

Ten moduł nie jest importowany przez silnik i nie ma timera. Hot-path czyta
wyłącznie gotowy plik przez ``authority_card.read_code_git_sha``.
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Sequence

# Uruchomienie bezpośrednie ustawia sys.path na ``dispatch_v2/tools``.
# Dodajemy wyłącznie root pakietów; nie dotykamy środowiska procesu silnika.
if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

try:
    from dispatch_v2 import authority_card
except ModuleNotFoundError as exc:
    if exc.name != "dispatch_v2":
        raise
    # Izolowany worktree może nie nazywać się ``dispatch_v2``. Moduł karty
    # nie ma importów pakietowych, więc ten fallback służy wyłącznie CLI.
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    import authority_card  # type: ignore[no-redef]


def _validated_sha(value: object) -> str:
    sha = str(value).strip().lower()
    if len(sha) not in (40, 64) or any(
        char not in "0123456789abcdef" for char in sha
    ):
        raise ValueError("git SHA must be 40 or 64 lowercase hex characters")
    return sha


def git_head(repo: Path) -> str:
    """Odczytaj HEAD bez shella; błąd gita jest błędem kroku deployu."""
    completed = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=str(repo),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return _validated_sha(completed.stdout)


def tracked_worktree_clean(repo: Path) -> bool:
    """True tylko gdy wszystkie śledzone bajty odpowiadają commitowi HEAD.

    Untracked są świadomie pomijane (np. ``eod_drafts``); BUILD_SHA nie jest
    pełną atestacją drzewa ani środowiska wykonawczego.
    """
    completed = subprocess.run(
        [
            "git",
            "status",
            "--porcelain",
            "--untracked-files=no",
        ],
        cwd=str(repo),
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    return completed.stdout == ""


def verify_sha(path: Path, expected_sha: str) -> bool:
    expected = _validated_sha(expected_sha)
    try:
        actual = path.read_text(encoding="ascii").strip().lower()
    except (OSError, UnicodeError):
        return False
    try:
        return _validated_sha(actual) == expected
    except ValueError:
        return False


def write_sha(path: Path, sha: str) -> bool:
    """Zapisz ``sha`` przez temp→fsync→rename→fsync katalogu.

    Zwraca ``True`` po zmianie pliku i ``False`` dla idempotentnego no-opu.
    """
    normalized = _validated_sha(sha)
    path = Path(path)
    if verify_sha(path, normalized):
        return False
    parent = path.parent
    if not parent.is_dir():
        raise FileNotFoundError(f"BUILD_SHA parent does not exist: {parent}")

    fd, temp_name = tempfile.mkstemp(
        dir=str(parent),
        prefix=f".{path.name}.",
        suffix=".tmp",
    )
    try:
        with os.fdopen(fd, "w", encoding="ascii", newline="\n") as stream:
            stream.write(f"{normalized}\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
        dir_fd = os.open(str(parent), os.O_RDONLY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    except Exception:
        try:
            os.unlink(temp_name)
        except OSError:
            pass
        raise
    return True


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Write or verify deploy BUILD_SHA for the authority-card gate"
    )
    parser.add_argument(
        "--verify",
        action="store_true",
        help="compare BUILD_SHA with git rev-parse HEAD; do not write",
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository whose HEAD is deployed (default: dispatch_v2 repo)",
    )
    args = parser.parse_args(argv)
    path = Path(authority_card.BUILD_SHA_PATH)
    try:
        repo = args.repo.resolve()
        if not tracked_worktree_clean(repo):
            print(
                "BUILD_SHA DIRTY: poświadcza commit, nie brudne śledzone bajty",
                file=sys.stderr,
            )
            return 1
        expected = git_head(repo)
        if args.verify:
            ok = verify_sha(path, expected)
            print(
                f"BUILD_SHA {'OK' if ok else 'MISMATCH'} "
                f"path={path} expected={expected}"
            )
            return 0 if ok else 1
        changed = write_sha(path, expected)
        print(
            f"BUILD_SHA {'WRITTEN' if changed else 'UNCHANGED'} "
            f"path={path} sha={expected}"
        )
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(
            f"BUILD_SHA ERROR {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
