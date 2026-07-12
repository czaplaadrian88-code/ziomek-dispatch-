#!/usr/bin/env python3
"""Dry-run rotation plan; mutation is restricted to an explicit sandbox."""
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from dispatch_v2.privacy.private_ledger import rotate_secure_jsonl


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


def default_archive_name(path: str | Path, now: datetime | None = None) -> str:
    stamp = (now or datetime.now(timezone.utc)).strftime("%Y%m%dT%H%M%SZ")
    p = Path(path)
    stem = p.name[:-6] if p.name.endswith(".jsonl") else p.name
    return f"{stem}-{stamp}.jsonl"


def plan_rotation(path: str | Path, archive_name: str | None = None) -> dict:
    current = Path(path)
    return {
        "schema": "private_ledger_rotation.v1",
        "mode": "would-rotate",
        "path_name": current.name,
        "archive_name": archive_name or default_archive_name(current),
        "mutated": False,
    }


def rotate_sandbox(path: str | Path, archive_name: str, *,
                   sandbox_root: str | Path) -> Path:
    root = Path(sandbox_root).resolve()
    current = Path(path).resolve()
    if any(_inside(current, live) for live in _LIVE_PREFIXES):
        raise ValueError("known-live rotation is forbidden")
    if not _inside(current, root):
        raise ValueError("rotation apply path must be inside sandbox")
    return rotate_secure_jsonl(current, archive_name)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--path", required=True)
    parser.add_argument("--archive-name")
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--sandbox-root")
    args = parser.parse_args(argv)
    archive_name = args.archive_name or default_archive_name(args.path)
    if not args.apply:
        print(json.dumps(plan_rotation(args.path, archive_name), sort_keys=True))
        return 0
    if not args.sandbox_root:
        print(json.dumps({"schema": "private_ledger_rotation.v1",
                          "error": "SandboxRequired"}))
        return 2
    try:
        archive = rotate_sandbox(
            args.path, archive_name, sandbox_root=args.sandbox_root,
        )
    except Exception as exc:
        print(json.dumps({"schema": "private_ledger_rotation.v1",
                          "error": type(exc).__name__}))
        return 2
    print(json.dumps({"schema": "private_ledger_rotation.v1", "status": "rotated",
                      "archive_name": archive.name}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
