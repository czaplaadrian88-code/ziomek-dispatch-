"""CLI tool dla atomic edycji flags.json (Master plan TOP-15 #6).

Single source of truth dla mutacji flags.json — używaj zamiast ad-hoc
`python3 -c "import json; ..."` one-liners w rollback procedurach.

Usage:
    python -m dispatch_v2.flags_admin set FLAG_NAME true
    python -m dispatch_v2.flags_admin set FLAG_NAME 30
    python -m dispatch_v2.flags_admin set FLAG_NAME '"string_value"'
    python -m dispatch_v2.flags_admin set FLAG_NAME '[60,50,40]'
    python -m dispatch_v2.flags_admin del FLAG_NAME
    python -m dispatch_v2.flags_admin get FLAG_NAME
    python -m dispatch_v2.flags_admin list

Wartość dla `set` parsowana jako JSON (true/false/int/float/list/dict).
String literals require explicit JSON quoting (np. '"text"').
Bare identifier fallback ("foo") = string "foo".

Exit codes:
    0 = ok
    1 = not_found (get)
    2 = bad input (parse error, missing arg)
"""
from __future__ import annotations

import argparse
import json
import sys

from dispatch_v2.core.flags_io import (
    delete_flag,
    load_flags,
    update_flag,
)


def _broadcast_flags_reload(name: str, action: str, value=None) -> None:
    """A4 (audit META RC2): emit CONFIG_RELOAD scope='flags' po mutacji.

    Defensive: emit fail NIE blokuje CLI exit (reload pozostaje opcjonalny —
    flags.json mtime hot-reload nadal działa via common.flag()). Logowane gdy
    fail żeby debug był możliwy. Best-effort signal dla future per-process
    cache invalidation (courier_tiers, etc.) gdy podobne broadcast dodane.
    """
    try:
        from dispatch_v2 import event_bus
        payload = {"name": name, "action": action}
        if value is not None and action == "set":
            payload["value"] = value
        event_bus.emit_config_reload(scope="flags", payload=payload)
    except Exception as _e:
        # NIE re-raise — CLI musi exit OK gdy mutation się powiodło
        sys.stderr.write(f"# warning: emit_config_reload FAIL ({type(_e).__name__}: {_e})\n")


def _parse_value(raw: str):
    """Parse CLI value as JSON; fallback to bare string."""
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return raw


def cmd_set(args: argparse.Namespace) -> int:
    value = _parse_value(args.value)
    flags = update_flag(args.name, value)
    _broadcast_flags_reload(args.name, "set", flags[args.name])
    print(json.dumps(
        {"ok": True, "name": args.name, "value": flags[args.name]},
        ensure_ascii=False,
    ))
    return 0


def cmd_del(args: argparse.Namespace) -> int:
    flags_pre = load_flags()
    existed = args.name in flags_pre
    delete_flag(args.name)
    if existed:
        _broadcast_flags_reload(args.name, "del")
    print(json.dumps(
        {"ok": True, "name": args.name, "existed": existed},
        ensure_ascii=False,
    ))
    return 0


def cmd_get(args: argparse.Namespace) -> int:
    flags = load_flags()
    if args.name not in flags:
        print(json.dumps(
            {"ok": False, "error": "not_found", "name": args.name},
            ensure_ascii=False,
        ))
        return 1
    print(json.dumps(
        {"ok": True, "name": args.name, "value": flags[args.name]},
        ensure_ascii=False,
    ))
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    print(json.dumps(load_flags(), indent=2, ensure_ascii=False))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="flags_admin",
        description="Atomic flags.json admin CLI",
    )
    sub = p.add_subparsers(dest="cmd", required=True)

    p_set = sub.add_parser("set", help="Set flag value (JSON-parsed)")
    p_set.add_argument("name")
    p_set.add_argument("value")
    p_set.set_defaults(func=cmd_set)

    p_del = sub.add_parser("del", help="Delete flag")
    p_del.add_argument("name")
    p_del.set_defaults(func=cmd_del)

    p_get = sub.add_parser("get", help="Get flag value")
    p_get.add_argument("name")
    p_get.set_defaults(func=cmd_get)

    p_list = sub.add_parser("list", help="List all flags")
    p_list.set_defaults(func=cmd_list)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
