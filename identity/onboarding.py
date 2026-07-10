"""Onboard / offboard tooling (Z-P1-05 Faza A) — COLLISION-CHECKED, dry-run first.

  python -m dispatch_v2.identity.onboarding onboard  --cid 601 --name "Jan Kowalski"
  python -m dispatch_v2.identity.onboarding offboard --cid 601

Onboard validates collisions against the registry BEFORE any write (new alias vs
existing, bare-key poison, cross-source), then — only for a real apply —
COMPOSES the proven atomic writer ``courier_admin.add_new_courier`` (it never
reimplements the write). Default is ``--dry-run``: it prints the diff of the 5
files onboarding touches. A real write requires BOTH ``--apply`` and env
``IDENTITY_ONBOARD_ALLOW=1``; in Faza A this is never exercised.

Offboard (Faza A) emits a PLAN only (shift_ignored_names + EXCLUDED_CIDS +
deactivation) — no writes; CID and historical settlements are never changed.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from typing import Any, Dict, List, Optional

from . import normalize
from .registry import Registry, build_registry
from .schema import canon_cid
from .sources import SourceBundle, load_all

# 5 files touched by onboarding today (4 via add_new_courier + grafik self-heal)
ONBOARD_FILES = (
    "dispatch_state/kurier_ids.json",
    "dispatch_state/kurier_piny.json",
    "dispatch_state/courier_tiers.json",
    "dispatch_v2/daily_accounting/kurier_full_names.json",
    "dispatch_state/grafik_full_names.json",
)


def _derive_alias(full_name: str) -> str:
    from dispatch_v2.courier_admin import derive_alias
    return derive_alias(full_name)


def validate_onboard(
    reg: Registry, bundle: SourceBundle, cid: str, full_name: str
) -> Dict[str, List[str]]:
    """Return ``{"blocking": [...], "warnings": [...]}`` (never writes)."""
    cid = canon_cid(cid)
    blocking: List[str] = []
    warnings: List[str] = []
    try:
        alias = _derive_alias(full_name)
    except Exception as e:
        return {"blocking": [f"cannot derive alias from {full_name!r}: {e}"], "warnings": []}

    # cid already known
    if reg.by_cid(cid) is not None or cid in {c for c in bundle.courier_tiers if c != "_meta"}:
        blocking.append(f"cid {cid} already exists in registry/courier_tiers")

    # alias exact collision with a different cid
    existing = bundle.kurier_ids.get(alias)
    if existing is not None and canon_cid(existing) != cid:
        blocking.append(f"alias {alias!r} already maps to cid {canon_cid(existing)} (not {cid})")

    # normalized-alias collision with a different cid
    na = normalize.norm(alias)
    for a, raw in bundle.kurier_ids.items():
        if normalize.norm(a) == na and canon_cid(raw) != cid:
            blocking.append(f"normalized alias {na!r} already used by cid {canon_cid(raw)} ({a!r})")
            break

    # bare-key poison risks
    if len((full_name or "").split()) <= 1:
        warnings.append(f"full name {full_name!r} is single-word -> creates a bare-key poison key")
    if " " not in alias.strip():
        warnings.append(f"derived alias {alias!r} is a bare key (single token)")
    swallow = reg.resolve(full_name, "worker")  # non-strict
    if swallow is not None and swallow != cid:
        warnings.append(
            f"non-strict resolve({full_name!r}) already returns cid {swallow} "
            f"(would be swallowed / ambiguous under legacy worker)"
        )

    return {"blocking": blocking, "warnings": warnings}


def plan_onboard(
    reg: Registry, bundle: SourceBundle, cid: str, full_name: str
) -> Dict[str, Any]:
    """Pure dry-run: validation + the diff of the 5 onboarding files. No writes."""
    cid = canon_cid(cid)
    checks = validate_onboard(reg, bundle, cid, full_name)
    alias = _derive_alias(full_name) if not any(
        "cannot derive alias" in b for b in checks["blocking"]
    ) else None

    diff: Dict[str, Any] = {}
    if alias is not None:
        diff = {
            "dispatch_state/kurier_ids.json": {f"+{alias}": cid, f"+{full_name}": cid},
            "dispatch_state/kurier_piny.json": {"+<new-unique-PIN>": alias},
            "dispatch_state/courier_tiers.json": {
                f"+{cid}": {"tier": "new", "cap_override": {"off_peak": 1, "normal": 2, "peak": 2}}
            },
            "dispatch_v2/daily_accounting/kurier_full_names.json": {f"+{alias}": full_name},
            "dispatch_state/grafik_full_names.json": {f"+{full_name}": cid},
        }
    return {
        "cid": cid,
        "full_name": full_name,
        "alias": alias,
        "blocking": checks["blocking"],
        "warnings": checks["warnings"],
        "diff": diff,
        "files": list(ONBOARD_FILES),
    }


def plan_offboard(reg: Registry, bundle: SourceBundle, cid: str) -> Dict[str, Any]:
    """Pure PLAN for offboarding (no writes)."""
    cid = canon_cid(cid)
    rec = reg.by_cid(cid)
    names: List[str] = []
    if rec is not None:
        names = [n for n in ({rec.best_full_name()} | set(rec.full_name.values())) if n]
    already_excluded = cid in bundle.excluded_cids
    return {
        "cid": cid,
        "known": rec is not None,
        "plan": {
            "shift_ignored_names.json += names": sorted(names),
            "daily_accounting/config.py EXCLUDED_CIDS += cid": (
                "already present" if already_excluded else int(cid) if cid.isdigit() else cid
            ),
            "registry: mark active=False": True,
        },
        "note": "Faza A emits a plan only — no file is written; CID and historical "
                "settlements are never changed.",
    }


# --------------------------------------------------------------------------- #


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m dispatch_v2.identity.onboarding")
    sub = ap.add_subparsers(dest="cmd", required=True)

    on = sub.add_parser("onboard")
    on.add_argument("--cid", required=True)
    on.add_argument("--name", required=True)
    on.add_argument("--apply", action="store_true", help="real write (needs IDENTITY_ONBOARD_ALLOW=1)")
    on.add_argument("--json", action="store_true")
    on.add_argument("--state-root", default=None)
    on.add_argument("--repo-root", default=None)

    off = sub.add_parser("offboard")
    off.add_argument("--cid", required=True)
    off.add_argument("--json", action="store_true")
    off.add_argument("--state-root", default=None)
    off.add_argument("--repo-root", default=None)

    args = ap.parse_args(argv)
    from .sources import default_paths
    paths = default_paths(state_root=args.state_root, repo_root=args.repo_root)
    bundle = load_all(paths)
    reg = build_registry(bundle)

    if args.cmd == "offboard":
        plan = plan_offboard(reg, bundle, args.cid)
        print(json.dumps(plan, ensure_ascii=False, indent=2) if args.json else _fmt_offboard(plan))
        return 0

    # onboard
    plan = plan_onboard(reg, bundle, args.cid, args.name)
    apply_allowed = args.apply and os.environ.get("IDENTITY_ONBOARD_ALLOW") == "1"

    if args.apply and not apply_allowed:
        print("REFUSED: --apply requires env IDENTITY_ONBOARD_ALLOW=1 (not set).")
        return 2
    if apply_allowed:
        if plan["blocking"]:
            print("REFUSED: blocking collisions:")
            for b in plan["blocking"]:
                print(f"  - {b}")
            return 3
        from dispatch_v2.courier_admin import add_new_courier
        result = add_new_courier(int(args.cid), args.name)
        # never expose the PIN — redact to last2
        result_safe = dict(result)
        if "pin" in result_safe:
            result_safe["pin_last2"] = str(result_safe.pop("pin"))[-2:]
        print("APPLIED: " + json.dumps(result_safe, ensure_ascii=False))
        return 0

    # dry-run (default)
    print(json.dumps(plan, ensure_ascii=False, indent=2) if args.json else _fmt_onboard(plan))
    return 0


def _fmt_onboard(plan: dict) -> str:
    lines = [f"ONBOARD (dry-run) cid={plan['cid']} name={plan['full_name']!r} alias={plan['alias']!r}"]
    if plan["blocking"]:
        lines.append("  BLOCKING:")
        lines += [f"    - {b}" for b in plan["blocking"]]
    if plan["warnings"]:
        lines.append("  warnings:")
        lines += [f"    - {w}" for w in plan["warnings"]]
    lines.append("  diff (5 files):")
    for f, d in plan["diff"].items():
        lines.append(f"    {f}: {d}")
    lines.append("  (no write — pass --apply with IDENTITY_ONBOARD_ALLOW=1 for a real onboard)")
    return "\n".join(lines)


def _fmt_offboard(plan: dict) -> str:
    lines = [f"OFFBOARD (plan) cid={plan['cid']} known={plan['known']}"]
    for k, v in plan["plan"].items():
        lines.append(f"    {k}: {v}")
    lines.append(f"  {plan['note']}")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.exit(main())
