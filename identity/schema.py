"""Canonical courier record schema (Z-P1-05 Faza A).

CID is the immutable canonical key, stored as ``str`` (JSON holds int,
courier_api.db holds TEXT — we normalize to str at the boundary). Aliases and
full names are versioned per source so the same person never fragments into
several entities.

PIN is treated as a secret: a record NEVER stores the plaintext PIN, only
``pin_present`` and ``pin_last2`` (last two digits) for reports/diagnostics.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional

__all__ = [
    "SOURCE_LABELS",
    "COORDINATOR_CIDS",
    "CourierRecord",
    "canon_cid",
    "canonical_courier_id",
    "canonical_numeric_cid",
    "pin_last2",
    "validate_courier_ids_store",
    "validate_record",
]

# Alias provenance buckets (brief: panel/gps/grafik/app/accounting) + "ids" =
# the raw kurier_ids alias registry (the union that actually drives resolution).
SOURCE_LABELS = ("ids", "panel", "gps", "grafik", "app", "accounting")

# Virtual / non-courier identities. cid 26 "Koordynator" has no courier_tiers
# entry; observability/data_alerts.py excludes "26" by default.
COORDINATOR_CIDS = frozenset({"26"})


def canon_cid(cid) -> str:
    """Canonical cid as str. ``26`` / ``"26"`` / ``26.0`` -> ``"26"``."""
    if cid is None:
        return ""
    if isinstance(cid, bool):  # guard: bool is an int subclass
        return str(cid)
    if isinstance(cid, float) and cid.is_integer():
        return str(int(cid))
    return str(cid).strip()


def canonical_courier_id(value) -> Optional[str]:
    """Normalize the shared JSON CID scalar or reject a malformed value.

    ``bool`` is rejected explicitly: it is an ``int`` subclass, so ``int(True)``
    would otherwise mint CID 1 out of a flag.
    """
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    normalized = str(value).strip()
    return normalized or None


def canonical_numeric_cid(value) -> Optional[str]:
    """Canonical CID restricted to a positive decimal integer, or ``None``.

    Stricter sibling of :func:`canonical_courier_id`, for the *writer* side
    (onboarding / grafik) that mints new roster rows. ``canonical_courier_id``
    only rejects ``bool`` and non ``str``/``int`` scalars, so ``-5``, ``"017"``,
    ``"+17"``, ``"1_7"`` or ``" 17 "`` would still pass — yet an int()-based
    reader (``manual_overrides._all_name_to_cid``, ``courier_availability``)
    coerces every one of those into a *different* identity. A writer must never
    persist a form that a reader would read as a different CID, so here the
    accepted set is exactly the canonical decimal integer:

    ``str(value)`` must be all ASCII digits and round-trip through ``int``
    (``str(int(s)) == s`` rejects leading zeros). ``bool`` stays rejected (it is
    an ``int`` subclass), ``float`` / ``None`` / other types are rejected.
    Returns the canonical decimal string, or ``None`` to reject.
    """
    if isinstance(value, bool) or not isinstance(value, (str, int)):
        return None
    try:
        s = str(value)
    except (ValueError, OverflowError):  # int beyond int_max_str_digits
        return None
    if not s.isascii() or not s.isdigit():  # rejects sign/space/underscore, unicode digits
        return None
    try:
        canonical = str(int(s))
    except (ValueError, OverflowError):  # digit string beyond int_max_str_digits
        return None
    return s if canonical == s else None  # rejects leading zeros


def validate_courier_ids_store(store) -> Dict:
    """Validate the complete name->CID generation used for authorization.

    Authorization reads one *generation* of the roster, never a subset: a
    consumer that skips an unrelated malformed row keeps answering from a
    roster nobody is allowed to trust. Raises ``ValueError`` so the caller
    fails the whole generation closed.
    """
    if not isinstance(store, dict):
        raise ValueError("courier ID store is not a mapping")
    for name, cid in store.items():
        if (
            not isinstance(name, str)
            or not name.strip()
            or canonical_courier_id(cid) is None
        ):
            raise ValueError("courier ID store contains a malformed record")
    return store


def pin_last2(pin) -> Optional[str]:
    """Last two chars of a PIN — never expose the full PIN in artifacts."""
    if pin is None:
        return None
    s = str(pin).strip()
    if not s:
        return None
    return s[-2:]


@dataclass
class CourierRecord:
    """One courier keyed by canonical ``cid`` (str)."""

    cid: str
    aliases: Dict[str, List[str]] = field(default_factory=dict)   # source -> [alias]
    full_name: Dict[str, str] = field(default_factory=dict)        # source -> full name
    tier: Optional[str] = None
    pin_present: bool = False
    pin_last2: Optional[str] = None
    active: bool = True
    excluded: bool = False
    is_coordinator: bool = False
    added_at: Optional[str] = None

    def all_aliases(self) -> List[str]:
        """Deduplicated union of aliases across all sources (insertion order)."""
        seen: Dict[str, None] = {}
        for vals in self.aliases.values():
            for a in vals:
                if a not in seen:
                    seen[a] = None
        return list(seen.keys())

    def add_alias(self, source: str, alias: str) -> None:
        if not alias:
            return
        bucket = self.aliases.setdefault(source, [])
        if alias not in bucket:
            bucket.append(alias)

    def best_full_name(self) -> Optional[str]:
        """Preferred display name: grafik > accounting > panel > app > gps."""
        for src in ("grafik", "accounting", "panel", "app", "gps"):
            v = self.full_name.get(src)
            if v:
                return v
        return None

    def to_public_dict(self) -> dict:
        """PIN-redacted, JSON-safe view for reports (never the plaintext PIN)."""
        return {
            "cid": self.cid,
            "best_full_name": self.best_full_name(),
            "aliases": {k: list(v) for k, v in self.aliases.items()},
            "full_name": dict(self.full_name),
            "tier": self.tier,
            "pin_present": self.pin_present,
            "pin_last2": self.pin_last2,
            "active": self.active,
            "excluded": self.excluded,
            "is_coordinator": self.is_coordinator,
            "added_at": self.added_at,
        }


def validate_record(rec: CourierRecord) -> List[str]:
    """Return a list of schema issues (empty == valid). Never raises."""
    issues: List[str] = []
    if not rec.cid:
        issues.append("empty cid")
    if rec.cid and rec.cid != canon_cid(rec.cid):
        issues.append(f"non-canonical cid {rec.cid!r}")
    for src in rec.aliases:
        if src not in SOURCE_LABELS:
            issues.append(f"unknown alias source {src!r}")
    for src in rec.full_name:
        if src not in SOURCE_LABELS:
            issues.append(f"unknown full_name source {src!r}")
    if rec.pin_present and not rec.pin_last2:
        issues.append("pin_present but no pin_last2")
    if rec.pin_last2 and len(rec.pin_last2) > 2:
        issues.append("pin_last2 leaks more than 2 chars")
    return issues
