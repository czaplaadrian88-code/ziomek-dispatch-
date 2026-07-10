"""Identity report CLI (Z-P1-05 Faza A) — READ-ONLY.

  python -m dispatch_v2.identity.report [--json] [--state-root ...] [--repo-root ...]
  python -m dispatch_v2.identity.report --parity

Default: gaps (missing names/tiers) + collisions (a-f) report. ``--parity``
compares registry ``resolve()`` against the real legacy functions
(``shift_notifications.worker.resolve_cid`` and ``panel_roster.match_name_to_cid``)
on all kurier_ids aliases + grafik full names — the shadow-parity proof for
handoff. Legacy side effects (debug-log append, admin alert, log-file creation)
are neutralized before import so ``--parity`` performs no writes/network/Telegram.
"""
from __future__ import annotations

import argparse
import json
import logging
import subprocess
import sys
from typing import Any, Dict, List, Optional

from .collisions import run_collisions
from .registry import build_registry
from .schema import canon_cid
from .sources import default_paths, load_all


# --------------------------------------------------------------------------- #
# git-vs-live helper (read-only subprocess, fail-open)
# --------------------------------------------------------------------------- #

_DAILY_REL = "daily_accounting/kurier_full_names.json"


def _git_daily_full_names(repo_root: str) -> Optional[Dict[str, str]]:
    try:
        out = subprocess.run(
            ["git", "-C", repo_root, "show", f"HEAD:{_DAILY_REL}"],
            capture_output=True, text=True, timeout=10, check=True,
        )
        data = json.loads(out.stdout)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except Exception:
        return None
    return None


# --------------------------------------------------------------------------- #
# default report
# --------------------------------------------------------------------------- #


def build_report(paths: Dict[str, str], *, git_compare: bool = True) -> dict:
    bundle = load_all(paths)
    reg = build_registry(bundle)
    all_cids = [r.cid for r in reg.all_records()]
    git_map = _git_daily_full_names(paths["repo_root"]) if git_compare else None
    col = run_collisions(bundle, all_cids=all_cids, git_full_names=git_map)
    return {
        "registry": {
            "records": len(reg.records),
            "aliases_total": len(bundle.kurier_ids),
            "cids_with_multi_alias": sum(
                1 for r in reg.all_records() if len(r.aliases.get("ids", [])) > 1
            ),
            "coordinator_cids": sorted(
                (r.cid for r in reg.all_records() if r.is_coordinator),
                key=lambda c: int(c) if c.isdigit() else 0,
            ),
            "excluded_cids": sorted(
                (r.cid for r in reg.all_records() if r.excluded),
                key=lambda c: int(c) if c.isdigit() else 0,
            ),
            "inactive_cids": sorted(
                (r.cid for r in reg.all_records() if not r.active),
                key=lambda c: int(c) if c.isdigit() else 0,
            ),
        },
        "collisions": col.to_dict(),
    }


def _print_report(rep: dict) -> None:
    r = rep["registry"]
    c = rep["collisions"]
    s = c["summary"]
    print("=== IDENTITY REGISTRY ===")
    print(f"  records (unique CID)      : {r['records']}")
    print(f"  aliases total (kurier_ids): {r['aliases_total']}")
    print(f"  CIDs with >1 ids alias    : {r['cids_with_multi_alias']}")
    print(f"  coordinator CIDs          : {r['coordinator_cids']}")
    print(f"  excluded CIDs             : {r['excluded_cids']}")
    print(f"  inactive CIDs             : {r['inactive_cids']}")
    print("\n=== COLLISIONS / GAPS ===")
    for k, v in s.items():
        print(f"  {k:24s}: {v}")
    if c["alias_multi_cid"]:
        print("\n  [a] alias -> >1 CID:")
        for row in c["alias_multi_cid"]:
            print(f"    {row['norm_alias']!r} -> {row['cids']} ({row['raw_aliases']})")
    if c["bare_key_poison"]:
        print(f"\n  [b] bare-key poison ({len(c['bare_key_poison'])}):")
        print("    " + ", ".join(f"{d['alias']}->{d['cid']}" for d in c["bare_key_poison"]))
    if c["fullname_divergence"]:
        print(f"\n  [c] full-name divergence ({len(c['fullname_divergence'])}):")
        for row in c["fullname_divergence"]:
            print(f"    cid {row['cid']}: {row['names']}")
    if c["missing_courier_names"]:
        print(f"\n  [d] missing courier_names ({len(c['missing_courier_names'])}): {c['missing_courier_names']}")
    if c["missing_tier"]:
        print(f"  [d] missing tier ({len(c['missing_tier'])}): {c['missing_tier']}")
    dp = c["duplicate_pins"]
    if dp.get("multi_pin_aliases") or dp.get("orphan_pins"):
        print(f"\n  [e] duplicate/orphan PINs: {dp}")
    gld = c["git_live_divergence"]
    if gld:
        print(f"\n  [f] git-vs-live daily kurier_full_names: "
              f"added={list(gld['added'])}, removed={list(gld['removed'])}, "
              f"changed={list(gld['changed'])}")
    if c["notes"]:
        print("\n  notes:")
        for n in c["notes"]:
            print(f"    - {n}")


# --------------------------------------------------------------------------- #
# --parity (registry vs legacy)
# --------------------------------------------------------------------------- #


def _neutralize_legacy_side_effects() -> None:
    """Patch legacy write/network/log side effects to no-ops BEFORE import."""
    # 1. worker's module-level logger opens a real log file at import — give it
    #    a null logger so importing worker touches nothing.
    try:
        import dispatch_v2.common as common
        common.setup_logger = lambda *a, **k: logging.getLogger("identity.parity.noop")  # type: ignore
    except Exception:
        pass
    # 2. ambiguous resolve appends to a state debug log.
    try:
        import dispatch_v2.shift_notifications.state as st
        st.append_match_debug_log = lambda *a, **k: None  # type: ignore
    except Exception:
        pass
    # 3. any admin alert path.
    try:
        import dispatch_v2.telegram_utils as tu
        tu.send_admin_alert = lambda *a, **k: None  # type: ignore
    except Exception:
        pass


def _n(x: Any) -> Optional[str]:
    return None if x is None else canon_cid(x)


def run_parity(paths: Dict[str, str]) -> dict:
    bundle = load_all(paths)
    reg = build_registry(bundle)

    _neutralize_legacy_side_effects()
    from dispatch_v2.shift_notifications.worker import resolve_cid as legacy_worker

    panel_ok = True
    try:
        from dispatch_v2 import panel_roster as pr
    except Exception:
        panel_ok = False
        pr = None  # type: ignore

    names: List[str] = list(bundle.kurier_ids.keys()) + list(bundle.grafik_full_names.keys())
    roster = reg._roster

    worker_mismatch = []
    panel_mismatch = []
    for name in names:
        legacy = legacy_worker(name, bundle.kurier_ids)
        mine = reg.resolve(name, "worker")
        if _n(legacy) != _n(mine):
            worker_mismatch.append({"name": name, "legacy": _n(legacy), "registry": _n(mine)})
        if panel_ok:
            m = pr.match_name_to_cid(name, roster)
            legacy_p = _n(m.cid) if m.status == "matched" else None
            mine_p = reg.resolve(name, "panel_roster")
            if legacy_p != mine_p:
                panel_mismatch.append({"name": name, "legacy": legacy_p, "registry": mine_p})

    return {
        "names_tested": len(names),
        "worker": {
            "matches": len(names) - len(worker_mismatch),
            "mismatches": worker_mismatch,
        },
        "panel_roster": {
            "available": panel_ok,
            "matches": (len(names) - len(panel_mismatch)) if panel_ok else 0,
            "mismatches": panel_mismatch,
        },
    }


def _print_parity(res: dict) -> None:
    n = res["names_tested"]
    w = res["worker"]
    p = res["panel_roster"]
    print(f"=== PARITY (registry vs legacy) — {n} names tested ===")
    print(f"  worker      : {w['matches']}/{n} match, {len(w['mismatches'])} mismatch")
    for m in w["mismatches"][:20]:
        print(f"    MISMATCH {m['name']!r}: legacy={m['legacy']} registry={m['registry']}")
    if p["available"]:
        print(f"  panel_roster: {p['matches']}/{n} match, {len(p['mismatches'])} mismatch")
        for m in p["mismatches"][:20]:
            print(f"    MISMATCH {m['name']!r}: legacy={m['legacy']} registry={m['registry']}")
    else:
        print("  panel_roster: legacy import failed — skipped")


# --------------------------------------------------------------------------- #


def main(argv: Optional[List[str]] = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m dispatch_v2.identity.report")
    ap.add_argument("--json", action="store_true", help="emit JSON")
    ap.add_argument("--parity", action="store_true", help="compare registry vs legacy resolvers")
    ap.add_argument("--state-root", default=None)
    ap.add_argument("--repo-root", default=None)
    ap.add_argument("--no-git-compare", action="store_true")
    args = ap.parse_args(argv)

    paths = default_paths(state_root=args.state_root, repo_root=args.repo_root)

    if args.parity:
        res = run_parity(paths)
        if args.json:
            print(json.dumps(res, ensure_ascii=False, indent=2))
        else:
            _print_parity(res)
        ok = not res["worker"]["mismatches"] and not res["panel_roster"]["mismatches"]
        return 0 if ok else 1

    rep = build_report(paths, git_compare=not args.no_git_compare)
    if args.json:
        print(json.dumps(rep, ensure_ascii=False, indent=2))
    else:
        _print_report(rep)
    return 0


if __name__ == "__main__":
    sys.exit(main())
