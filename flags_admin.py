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
import glob
import json
import os
import subprocess
import sys

from dispatch_v2.core.flags_io import (
    delete_flag,
    load_flags,
    update_flag,
)


# ── CONFIG-DUAL-01 (audyt 2026-06-03): `effective` ──────────────────────────
# Problem: `flags_admin list` pokazuje TYLKO flags.json. Ale dispatch-shadow ma
# nakladke env (drop-in override.conf), ktora dla flag czytanych wzorem
# `getattr(C, FLAG) or C.flag(FLAG)` WYGRYWA — wiec operator widzi OFF tam,
# gdzie produkcja ma ON. `effective` uwidacznia warstwe env + drift (override.conf
# zmieniony ale serwis nie zrestartowany → wartosc deklarowana != live w procesie).

def _parse_environment_lines(text: str) -> dict:
    """Wyciaga KEY=VALUE z linii systemd `Environment=...` (obsluga cudzyslowow)."""
    out: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("Environment="):
            continue
        rhs = line[len("Environment="):].strip()
        if len(rhs) >= 2 and rhs[0] == '"' and rhs[-1] == '"':
            rhs = rhs[1:-1]
        if "=" in rhs:
            k, v = rhs.split("=", 1)
            out[k.strip()] = v.strip()
    return out


def _read_override_env(service: str) -> dict:
    """Merge Environment= z base service + drop-inow *.conf (last-wins)."""
    merged: dict = {}
    base = f"/etc/systemd/system/{service}"
    paths = []
    if os.path.exists(base):
        paths.append(base)
    dropin_dir = f"/etc/systemd/system/{service}.d"
    # systemd laduje drop-iny alfabetycznie; .bak/.bak-* pomijamy
    for p in sorted(glob.glob(os.path.join(dropin_dir, "*.conf"))):
        paths.append(p)
    for p in paths:
        try:
            with open(p, encoding="utf-8") as f:
                merged.update(_parse_environment_lines(f.read()))
        except OSError:
            continue
    return merged


def _read_live_env(service: str, keys) -> dict | None:
    """Czyta /proc/<MainPID>/environ dzialajacego serwisu, filtruje do `keys`.
    None gdy MainPID niedostepny (serwis down / brak uprawnien)."""
    try:
        pid = subprocess.run(
            ["systemctl", "show", service, "-p", "MainPID", "--value"],
            capture_output=True, text=True, timeout=5,
        ).stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return None
    if not pid or pid == "0":
        return None
    try:
        with open(f"/proc/{pid}/environ", "rb") as f:
            raw = f.read()
    except OSError:
        return None
    env: dict = {}
    for chunk in raw.split(b"\0"):
        if b"=" in chunk:
            k, v = chunk.split(b"=", 1)
            ks = k.decode("utf-8", "replace")
            if ks in keys:
                env[ks] = v.decode("utf-8", "replace")
    return env


def compute_effective(flags: dict, override_env: dict, live_env: dict | None) -> dict:
    """Czysta funkcja (testowalna): laczy 3 zrodla w raport efektywnego stanu flag."""
    rows = []
    for k in sorted(override_env):
        decl = override_env[k]
        live = None if live_env is None else live_env.get(k)
        drift = None
        if live_env is not None:
            drift = (live != decl)          # rozjazd deklaracji vs proces (brak restartu / brak w procesie)
        rows.append({
            "key": k,
            "override_conf": decl,
            "live_process": live,
            "drift": drift,
            "also_in_flags_json": k in flags,
        })
    dual = [r["key"] for r in rows if r["also_in_flags_json"]]
    return {
        "flags_json_count": len(flags),
        "env_override_count": len(override_env),
        "live_readable": live_env is not None,
        "dual_source": dual,        # env WYGRYWA dla tych przy wzorcu getattr-or-flag
        "rows": rows,
    }


def cmd_effective(args: argparse.Namespace) -> int:
    flags = load_flags()
    override_env = _read_override_env(args.service)
    live_env = _read_live_env(args.service, set(override_env.keys()))
    res = compute_effective(flags, override_env, live_env)

    print(f"# EFFECTIVE FLAGS — service={args.service}")
    print(f"# flags.json: {res['flags_json_count']} kluczy | env override: "
          f"{res['env_override_count']} | live_proc_readable: {res['live_readable']}")
    if res["dual_source"]:
        print(f"# DUAL-SOURCE (env override.conf WYGRYWA nad flags.json dla wzorca "
              f"getattr-or-flag): {', '.join(res['dual_source'])}")
    print(f"# {'KEY':<48} {'override.conf':<14} {'live_proc':<14} drift in_flags.json")
    for r in res["rows"]:
        drift = "" if r["drift"] is None else ("DRIFT" if r["drift"] else "ok")
        live = "n/a" if r["live_process"] is None else r["live_process"]
        print(f"  {r['key']:<48} {str(r['override_conf']):<14} {str(live):<14} "
              f"{drift:<5} {'YES' if r['also_in_flags_json'] else ''}")
    if args.json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


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

    p_eff = sub.add_parser(
        "effective",
        help="Pokaz efektywny stan flag (flags.json + env override.conf + live proc) — fix CONFIG-DUAL-01",
    )
    p_eff.add_argument("--service", default="dispatch-shadow.service",
                       help="Service systemd do inspekcji env (default dispatch-shadow.service)")
    p_eff.add_argument("--json", action="store_true", help="Dolacz surowy JSON")
    p_eff.set_defaults(func=cmd_effective)

    return p


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
