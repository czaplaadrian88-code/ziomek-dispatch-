#!/usr/bin/env python3
"""Dry-run old→private migration plan restricted to an explicit sandbox.

The CLI cannot accept a production path: source and destination must both be
inside ``--sandbox-root`` and known live roots are rejected before any open.
Mutation remains an explicit HOLD until an atomic no-replace, verified publish
protocol is approved.  Repeated dry-run/HOLD calls never create partial files.
"""
from __future__ import annotations

import argparse
import json
import os
import stat
from pathlib import Path

from dispatch_v2.privacy.private_ledger import PrivateLedgerError


_LIVE_PREFIXES = (
    Path("/root/.openclaw/workspace/dispatch_state"),
    Path("/root/.openclaw/workspace/scripts/logs"),
)


def _inside(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
        return True
    except ValueError:
        return False


def migrate_sandbox(source: str | Path, destination: str | Path, *,
                    sandbox_root: str | Path, ledger: str, scope: str,
                    key_file: str | Path, synthetic_fixture: bool = False,
                    apply: bool = False) -> dict:
    root = Path(sandbox_root).resolve()
    src = Path(source).resolve()
    dst = Path(destination).resolve()
    key_path = Path(key_file).resolve()
    if any(_inside(src, live) or _inside(dst, live) for live in _LIVE_PREFIXES):
        raise ValueError("live migration is forbidden")
    if (not _inside(src, root) or not _inside(dst, root)
            or not _inside(key_path, root) or src == dst):
        raise ValueError(
            "source, destination and synthetic carrier must be distinct/inside sandbox"
        )
    if not synthetic_fixture:
        raise ValueError("source-only migration requires explicit synthetic fixture")
    if os.path.lexists(dst):
        raise ValueError("destination must be absent")
    source_st = src.lstat()
    key_st = key_path.lstat()
    if not stat.S_ISREG(source_st.st_mode) or not stat.S_ISREG(key_st.st_mode):
        raise ValueError("source and synthetic carrier must be regular files")
    if key_st.st_nlink != 1 or stat.S_IMODE(key_st.st_mode) != 0o600:
        raise ValueError("synthetic carrier must be single-link mode 0600")
    plan = {
        "schema": "private_ledger_migration.v1",
        "status": "WOULD_MIGRATE",
        "ledger": ledger,
        "scope": scope,
        "live_paths": False,
        "destination_absent": True,
        "mutated": False,
    }
    if apply:
        raise PrivateLedgerError(
            "migration apply is HOLD pending atomic verified no-replace publish"
        )
    return plan


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--sandbox-root", required=True)
    parser.add_argument("--source", required=True)
    parser.add_argument("--destination", required=True)
    parser.add_argument("--ledger", required=True)
    parser.add_argument("--scope", required=True)
    parser.add_argument("--key-file", required=True)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--synthetic-fixture", action="store_true")
    args = parser.parse_args(argv)
    try:
        report = migrate_sandbox(
            args.source, args.destination, sandbox_root=args.sandbox_root,
            ledger=args.ledger, scope=args.scope, key_file=args.key_file,
            synthetic_fixture=args.synthetic_fixture, apply=args.apply,
        )
    except Exception as exc:
        print(json.dumps({"schema": "private_ledger_migration.v1",
                          "error": type(exc).__name__}))
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
