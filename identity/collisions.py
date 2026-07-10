"""Identity collision / gap validators (Z-P1-05 Faza A).

Pure functions over a :class:`~dispatch_v2.identity.sources.SourceBundle`. Six
checks:

  (a) normalized alias -> more than one CID (hard collision);
  (b) bare first-name keys (the "poison" set — score=1 catch-all, COMPUTED from
      single-word kurier_ids keys, not hardcoded);
  (c) full-name divergence across sources, sieving out abbreviation-vs-full
      (same first name + surname prefix == same person, not a conflict);
  (d) CIDs missing a courier_names entry / missing a tier;
  (e) duplicate PINs (one alias bound by >1 PIN, or a PIN whose alias resolves
      to no CID) — reported as pin_last2 only, never the plaintext PIN;
  (f) git-vs-live divergence of daily_accounting/kurier_full_names (two dicts).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from . import normalize
from .schema import COORDINATOR_CIDS, canon_cid, pin_last2
from .sources import SourceBundle

__all__ = ["CollisionReport", "run_collisions", "names_compatible"]


def names_compatible(n1: str, n2: str) -> bool:
    """True when two names could be the same person (abbrev-vs-full sieve).

    Different first name -> incompatible (Kuba vs Jakub). Same first name and one
    surname is a prefix of the other (under ``norm``, no diacritic folding) ->
    compatible ("Jakub Ol" ⊆ "Jakub Olchowski"). A missing surname on either
    side is treated as compatible (partial data, not a conflict).
    """
    t1 = [normalize.norm(t) for t in (n1 or "").split() if t.strip()]
    t2 = [normalize.norm(t) for t in (n2 or "").split() if t.strip()]
    if not t1 or not t2:
        return True
    if t1[0] != t2[0]:
        return False
    s1 = t1[-1] if len(t1) > 1 else ""
    s2 = t2[-1] if len(t2) > 1 else ""
    if not s1 or not s2:
        return True
    return s1.startswith(s2) or s2.startswith(s1)


@dataclass
class CollisionReport:
    alias_multi_cid: List[dict] = field(default_factory=list)
    bare_key_poison: List[dict] = field(default_factory=list)
    fullname_divergence: List[dict] = field(default_factory=list)
    missing_courier_names: List[str] = field(default_factory=list)
    missing_tier: List[str] = field(default_factory=list)
    duplicate_pins: Dict[str, list] = field(default_factory=dict)
    git_live_divergence: Optional[dict] = None
    notes: List[str] = field(default_factory=list)

    def summary(self) -> Dict[str, int]:
        gld = self.git_live_divergence or {}
        return {
            "alias_multi_cid": len(self.alias_multi_cid),
            "bare_key_poison": len(self.bare_key_poison),
            "fullname_divergence": len(self.fullname_divergence),
            "missing_courier_names": len(self.missing_courier_names),
            "missing_tier": len(self.missing_tier),
            "duplicate_pin_aliases": len(self.duplicate_pins.get("multi_pin_aliases", [])),
            "orphan_pins": len(self.duplicate_pins.get("orphan_pins", [])),
            "git_live_added": len(gld.get("added", {})),
            "git_live_removed": len(gld.get("removed", {})),
            "git_live_changed": len(gld.get("changed", {})),
        }

    def to_dict(self) -> dict:
        return {
            "summary": self.summary(),
            "alias_multi_cid": self.alias_multi_cid,
            "bare_key_poison": self.bare_key_poison,
            "fullname_divergence": self.fullname_divergence,
            "missing_courier_names": self.missing_courier_names,
            "missing_tier": self.missing_tier,
            "duplicate_pins": self.duplicate_pins,
            "git_live_divergence": self.git_live_divergence,
            "notes": self.notes,
        }


# --------------------------------------------------------------------------- #
# individual checks (each takes plain data — hermetic)
# --------------------------------------------------------------------------- #


def find_alias_multi_cid(kurier_ids: Dict[str, Any]) -> List[dict]:
    grouped: Dict[str, Dict[str, List[str]]] = {}
    for alias, raw in kurier_ids.items():
        na = normalize.norm(alias)
        cid = canon_cid(raw)
        entry = grouped.setdefault(na, {})
        entry.setdefault(cid, []).append(alias)
    out = []
    for na, by_cid in grouped.items():
        if len(by_cid) > 1:
            out.append({
                "norm_alias": na,
                "cids": sorted(by_cid.keys()),
                "raw_aliases": sorted(a for group in by_cid.values() for a in group),
            })
    return sorted(out, key=lambda d: d["norm_alias"])


def find_bare_key_poison(kurier_ids: Dict[str, Any]) -> List[dict]:
    out = []
    for alias, raw in kurier_ids.items():
        if " " not in alias.strip():
            out.append({"alias": alias, "cid": canon_cid(raw)})
    return sorted(out, key=lambda d: d["alias"].lower())


def find_fullname_divergence(bundle: SourceBundle) -> List[dict]:
    # per cid, collect candidate names from every source
    by_cid: Dict[str, Dict[str, str]] = {}

    def _add(cid: str, source: str, name: str) -> None:
        if cid and name:
            by_cid.setdefault(cid, {})[source] = name

    for full_name, cid in bundle.grafik_full_names.items():
        _add(canon_cid(cid), "grafik", full_name)
    for cid, name in bundle.courier_names.items():
        _add(cid, "panel", name)
    if bundle.courier_api_names:
        for cid, name in bundle.courier_api_names.items():
            _add(cid, "app", name)
    alias_to_cid = {a: canon_cid(v) for a, v in bundle.kurier_ids.items()}
    for alias, full_name in bundle.daily_full_names.items():
        cid = alias_to_cid.get(alias)
        if cid:
            _add(cid, "accounting", full_name)

    out = []
    for cid, per_source in by_cid.items():
        names = list(per_source.items())  # (source, name)
        conflict = False
        for i in range(len(names)):
            for j in range(i + 1, len(names)):
                if not names_compatible(names[i][1], names[j][1]):
                    conflict = True
        if conflict:
            out.append({
                "cid": cid,
                "names": {src: nm for src, nm in per_source.items()},
            })
    return sorted(out, key=lambda d: d["cid"])


def find_missing_names_and_tiers(
    bundle: SourceBundle, all_cids: List[str]
) -> Dict[str, List[str]]:
    tier_cids = {c for c in bundle.courier_tiers if c != "_meta"}
    missing_names = []
    missing_tier = []
    for cid in all_cids:
        if cid in COORDINATOR_CIDS:
            continue  # virtual — legitimately absent
        if cid not in bundle.courier_names:
            missing_names.append(cid)
        if cid not in tier_cids:
            missing_tier.append(cid)
    return {
        "missing_courier_names": sorted(missing_names, key=lambda c: int(c) if c.isdigit() else 0),
        "missing_tier": sorted(missing_tier, key=lambda c: int(c) if c.isdigit() else 0),
    }


def find_duplicate_pins(
    kurier_piny: Dict[str, str], kurier_ids: Dict[str, Any]
) -> Dict[str, list]:
    alias_to_cid = {a: canon_cid(v) for a, v in kurier_ids.items()}
    alias_to_pins: Dict[str, List[str]] = {}
    orphan = []
    for pin, alias in kurier_piny.items():
        alias_to_pins.setdefault(alias, []).append(pin)
        if alias not in alias_to_cid:
            orphan.append({"pin_last2": pin_last2(pin), "alias": alias})
    multi = [
        {"alias": alias, "pin_last2": [pin_last2(p) for p in pins], "count": len(pins)}
        for alias, pins in alias_to_pins.items()
        if len(pins) > 1
    ]
    return {
        "multi_pin_aliases": sorted(multi, key=lambda d: d["alias"].lower()),
        "orphan_pins": sorted(orphan, key=lambda d: d["alias"].lower()),
    }


def find_git_live_divergence(
    git_map: Dict[str, str], live_map: Dict[str, str]
) -> dict:
    gk, lk = set(git_map), set(live_map)
    added = {k: live_map[k] for k in sorted(lk - gk)}
    removed = {k: git_map[k] for k in sorted(gk - lk)}
    changed = {
        k: {"git": git_map[k], "live": live_map[k]}
        for k in sorted(gk & lk)
        if git_map[k] != live_map[k]
    }
    return {"added": added, "removed": removed, "changed": changed}


def run_collisions(
    bundle: SourceBundle,
    *,
    all_cids: Optional[List[str]] = None,
    git_full_names: Optional[Dict[str, str]] = None,
) -> CollisionReport:
    """Run every check. ``all_cids`` = registry CID universe (for gap checks);
    ``git_full_names`` enables the git-vs-live divergence check (f)."""
    if all_cids is None:
        all_cids = _cids_from_bundle(bundle)

    rep = CollisionReport(notes=list(bundle.notes))
    rep.alias_multi_cid = find_alias_multi_cid(bundle.kurier_ids)
    rep.bare_key_poison = find_bare_key_poison(bundle.kurier_ids)
    rep.fullname_divergence = find_fullname_divergence(bundle)
    gaps = find_missing_names_and_tiers(bundle, all_cids)
    rep.missing_courier_names = gaps["missing_courier_names"]
    rep.missing_tier = gaps["missing_tier"]
    rep.duplicate_pins = find_duplicate_pins(bundle.kurier_piny, bundle.kurier_ids)
    if git_full_names is not None:
        rep.git_live_divergence = find_git_live_divergence(
            git_full_names, bundle.daily_full_names
        )
    else:
        rep.notes.append("git-vs-live check skipped (no git_full_names supplied)")
    return rep


def _cids_from_bundle(bundle: SourceBundle) -> List[str]:
    cids: set = set()
    cids.update(canon_cid(v) for v in bundle.kurier_ids.values())
    cids.update(c for c in bundle.courier_tiers if c != "_meta")
    cids.update(bundle.courier_names)
    cids.update(canon_cid(v) for v in bundle.grafik_full_names.values())
    if bundle.courier_api_names:
        cids.update(bundle.courier_api_names)
    cids.discard("")
    return sorted(cids, key=lambda c: int(c) if c.isdigit() else 0)
