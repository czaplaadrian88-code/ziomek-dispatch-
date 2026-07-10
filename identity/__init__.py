"""Canonical courier identity (Z-P1-05 Faza A).

Additive, READ-ONLY package: one record per CID (the immutable canonical key),
with grafik / panel / GPS / app / accounting names as versioned aliases, plus a
collision validator, a report/parity CLI and dry-run onboarding tooling.

Nothing here is imported by the runtime engine — the package is inert until
Faza B wires it in. Rollback = revert the commit.

Public API:
    norm, resolve_worker, resolve_panel_roster, RosterMatch     (normalize)
    CourierRecord, canon_cid, SOURCE_LABELS                      (schema)
    default_paths, load_all, SourceBundle                        (sources)
    build_registry, Registry                                     (registry)
    run_collisions, CollisionReport                              (collisions)
"""
from __future__ import annotations

from .collisions import CollisionReport, run_collisions
from .normalize import (
    RosterMatch,
    norm,
    resolve_panel_roster,
    resolve_worker,
)
from .registry import Registry, build_registry
from .schema import SOURCE_LABELS, CourierRecord, canon_cid
from .sources import SourceBundle, default_paths, load_all

__all__ = [
    "norm",
    "resolve_worker",
    "resolve_panel_roster",
    "RosterMatch",
    "CourierRecord",
    "canon_cid",
    "SOURCE_LABELS",
    "default_paths",
    "load_all",
    "SourceBundle",
    "build_registry",
    "Registry",
    "run_collisions",
    "CollisionReport",
]
