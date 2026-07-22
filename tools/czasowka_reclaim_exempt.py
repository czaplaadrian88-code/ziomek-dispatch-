#!/usr/bin/env python3
"""Manage PII-free exemptions for the czasowka reclaim observer."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from dispatch_v2 import reclaim_exemptions as exemptions


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--state-path", type=Path, default=None)
    commands = parser.add_subparsers(dest="command", required=True)

    add = commands.add_parser("add")
    add.add_argument("order_id")
    add.add_argument("--reason-code", required=True)

    remove = commands.add_parser("remove")
    remove.add_argument("order_id")
    remove.add_argument("--reason-code", required=True)

    commands.add_parser("list")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "add":
            entry = exemptions.set_exemption(
                args.order_id, args.reason_code, path=args.state_path
            )
            payload = {"action": "add", "order_id": args.order_id, "entry": entry}
        elif args.command == "remove":
            entry = exemptions.remove_exemption(
                args.order_id, args.reason_code, path=args.state_path
            )
            payload = {
                "action": "remove",
                "order_id": args.order_id,
                "removed": entry,
            }
        else:
            payload = {
                "action": "list",
                "entries": exemptions.list_exemptions(args.state_path),
            }
    except (KeyError, OSError, ValueError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(payload, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
