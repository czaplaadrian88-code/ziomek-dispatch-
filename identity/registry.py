"""Canonical courier registry — merges the 10 sources into one record per CID.

Read-only, fail-open. ``build_registry(bundle)`` folds every source into
``{cid(str) -> CourierRecord}``; the :class:`Registry` exposes ``resolve`` (with
the two 1:1 legacy strategies), ``by_cid`` and ``all_records``.

Alias provenance is by AUTHORITATIVE source file (no heuristic guessing):
``ids`` = the union kurier_ids registry, ``panel`` = courier_names display,
``grafik`` = grafik_full_names, ``app`` = courier_api.db, ``accounting`` =
daily_accounting. ``gps`` is reserved (GPS keys by cid, carries no distinct name)
and stays empty in Faza A.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from . import normalize
from .schema import (
    COORDINATOR_CIDS,
    CourierRecord,
    canon_cid,
    pin_last2,
)
from .sources import SourceBundle, load_all

__all__ = ["Registry", "build_registry"]


def _tier_label(entry: Any) -> Optional[str]:
    if not isinstance(entry, dict):
        return None
    if entry.get("tier_label"):
        return str(entry["tier_label"])
    bag = entry.get("bag")
    if isinstance(bag, dict) and bag.get("tier"):
        return str(bag["tier"])
    return None


class Registry:
    """Immutable-ish read view over merged courier identity."""

    def __init__(
        self,
        records: Dict[str, CourierRecord],
        kurier_ids: Dict[str, Any],
        roster: Dict[str, str],
    ) -> None:
        self.records = records
        self._kurier_ids = kurier_ids
        self._roster = roster

    # -- lookups ----------------------------------------------------------- #

    def by_cid(self, cid) -> Optional[CourierRecord]:
        return self.records.get(canon_cid(cid))

    def accounting_name(self, cid) -> Optional[str]:
        """Canonical full name for a settlement row.

        Grafik is the approved full-name source. The remaining sources are
        explicit fallbacks for a courier not yet present in the grafik; this
        prevents accounting from maintaining another alias->name resolver.
        """
        names = self.accounting_names(cid)
        return names[0] if names else None

    def accounting_names(self, cid) -> List[str]:
        """All authoritative full-name variants for legacy settlement matching."""
        rec = self.by_cid(cid)
        if rec is None:
            return []
        names: List[str] = []
        for source in ("grafik", "panel", "app", "accounting"):
            name = rec.full_name.get(source)
            clean = name.strip() if name else ""
            if clean and clean not in names:
                names.append(clean)
        return names

    def all_records(self) -> List[CourierRecord]:
        return list(self.records.values())

    def resolve(
        self,
        name: str,
        profile: str = "worker",
        *,
        bare_key_strict: bool = False,
    ) -> Optional[str]:
        """Resolve a name to a canonical cid (str) or ``None``.

        ``profile="worker"`` uses the kurier_ids ×10/×5 strategy (shift/dispatch
        canon). ``profile="panel_roster"`` uses the ×10/×10 roster strategy.
        Ambiguous ties resolve to ``None`` in both (mirrors legacy).
        """
        if profile == "worker":
            raw = normalize.resolve_worker(
                name, self._kurier_ids, bare_key_strict=bare_key_strict
            )
            return canon_cid(raw) if raw is not None else None
        if profile == "panel_roster":
            m = normalize.resolve_panel_roster(name, self._roster)
            return canon_cid(m.cid) if m.status == "matched" else None
        raise ValueError(f"unknown resolve profile {profile!r}")


def _build_roster(bundle: SourceBundle) -> Dict[str, str]:
    """``{cid -> display name}`` for the panel_roster strategy.

    Prefers courier_names; falls back to courier_tiers' ``name`` field so the
    roster strategy stays usable even where courier_names is stale/missing.
    """
    roster: Dict[str, str] = dict(bundle.courier_names)
    for cid, entry in bundle.courier_tiers.items():
        if cid == "_meta":
            continue
        if cid not in roster and isinstance(entry, dict) and entry.get("name"):
            roster[cid] = str(entry["name"])
    return roster


def build_registry(bundle: Optional[SourceBundle] = None) -> Registry:
    """Fold all sources into a per-CID registry (fail-open)."""
    if bundle is None:
        bundle = load_all()

    # --- reverse indexes ------------------------------------------------- #
    cid_to_ids_aliases: Dict[str, List[str]] = {}
    alias_to_cid: Dict[str, str] = {}
    for alias, raw in bundle.kurier_ids.items():
        cid = canon_cid(raw)
        alias_to_cid[alias] = cid
        cid_to_ids_aliases.setdefault(cid, []).append(alias)

    cid_to_grafik_names: Dict[str, List[str]] = {}
    for full_name, cid in bundle.grafik_full_names.items():
        cid_to_grafik_names.setdefault(canon_cid(cid), []).append(full_name)

    cid_to_pins: Dict[str, List[str]] = {}
    for pin, alias in bundle.kurier_piny.items():
        cid = alias_to_cid.get(alias)
        if cid:
            cid_to_pins.setdefault(cid, []).append(pin)

    api_names = bundle.courier_api_names or {}

    # --- universe of CIDs ------------------------------------------------- #
    cids: set = set()
    cids.update(cid_to_ids_aliases)
    cids.update(cid_to_grafik_names)
    cids.update(c for c in bundle.courier_tiers if c != "_meta")
    cids.update(bundle.courier_names)
    cids.update(api_names)
    cids.update(bundle.whitelist)
    cids.discard("")

    ignored_norm = {normalize.norm(n) for n in bundle.shift_ignored}

    records: Dict[str, CourierRecord] = {}
    # sort the universe so records / all_records() are deterministic (stable JSON)
    for cid in sorted(cids, key=lambda c: (0, int(c)) if c.isdigit() else (1, c)):
        rec = CourierRecord(cid=cid)

        # aliases by authoritative source
        for a in cid_to_ids_aliases.get(cid, []):
            rec.add_alias("ids", a)
        for a in cid_to_grafik_names.get(cid, []):
            rec.add_alias("grafik", a)
        panel_name = bundle.courier_names.get(cid)
        if panel_name:
            rec.add_alias("panel", panel_name)
        app_name = api_names.get(cid)
        if app_name:
            rec.add_alias("app", app_name)

        # full names by source
        if cid_to_grafik_names.get(cid):
            rec.full_name["grafik"] = cid_to_grafik_names[cid][0]
        if panel_name:
            rec.full_name["panel"] = panel_name
        if app_name:
            rec.full_name["app"] = app_name
        for a in cid_to_ids_aliases.get(cid, []):
            if a in bundle.daily_full_names:
                rec.full_name["accounting"] = bundle.daily_full_names[a]
                break

        # tier / flags
        tier_entry = bundle.courier_tiers.get(cid)
        rec.tier = _tier_label(tier_entry)
        if isinstance(tier_entry, dict):
            rec.added_at = tier_entry.get("added_at")
            if tier_entry.get("coordinator"):
                rec.is_coordinator = True
        if cid in COORDINATOR_CIDS:
            rec.is_coordinator = True
        rec.excluded = cid in bundle.excluded_cids

        # pin (secret — last2 only)
        pins = cid_to_pins.get(cid, [])
        if pins:
            rec.pin_present = True
            rec.pin_last2 = pin_last2(pins[0])

        # active: only demoted when a known name is on the shift-ignore list
        names_norm = {normalize.norm(n) for n in rec.full_name.values()}
        names_norm.update(normalize.norm(a) for a in rec.all_aliases())
        rec.active = not (names_norm & ignored_norm)

        records[cid] = rec

    return Registry(records, dict(bundle.kurier_ids), _build_roster(bundle))
