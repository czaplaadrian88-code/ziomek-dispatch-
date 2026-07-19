"""Canonical courier identity (Z-P1-05 Faza A).

Additive, READ-ONLY package: one record per CID (the immutable canonical key),
with grafik / panel / GPS / app / accounting names as versioned aliases, plus a
collision validator, a report/parity CLI and dry-run onboarding tooling.

The registry/collisions/onboarding machinery is inert until Faza B wires it
in — but ``canon_cid`` (schema) and ``candidate_identity_key`` /
``alternative_candidates`` (candidate_pool) are pure identity-comparison
helpers with no such gate: ``shadow_dispatcher`` and ``czasowka_scheduler``
already import them at runtime (A8-2, 2026-07-19) to dedup a decision
candidate pool by courier identity instead of list position. Rollback =
revert the commit.

Public API:
    norm, resolve_worker, resolve_panel_roster, RosterMatch     (normalize)
    CourierRecord, canon_cid, SOURCE_LABELS                      (schema)
    candidate_identity_key, alternative_candidates               (candidate_pool)
    default_paths, load_all, SourceBundle                        (sources)
    build_registry, Registry                                     (registry)
    run_collisions, CollisionReport                              (collisions)
"""
from __future__ import annotations

from .candidate_pool import alternative_candidates, candidate_identity_key
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
    "candidate_identity_key",
    "alternative_candidates",
    "SOURCE_LABELS",
    "default_paths",
    "load_all",
    "SourceBundle",
    "build_registry",
    "Registry",
    "run_collisions",
    "CollisionReport",
]
