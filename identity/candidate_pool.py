"""Decision-candidate pool identity dedup (A8-2 twin sweep, 2026-07-19).

Pure functions over decision-pipeline candidates. Duck-typed on
``.courier_id`` — works for ``core.candidates.Candidate`` (shadow_dispatcher
ticks) and czasówka's evaluation candidates (same object, different caller),
without either module importing the other.

Selection producers (``core/selection.py``: best-effort OBJM, ``solo_fallback``,
``no_solo_candidates``) do not guarantee that the selected/"best" candidate is
``candidates[0]``. Slicing ``candidates[1:]`` to build an "alternatives" list
silently drops or duplicates couriers whenever that assumption breaks:

- OBJM may pick a candidate from the middle of the list without moving it
  to the front;
- ``solo_fallback`` builds a *new* best object while a rejected variant of
  the same courier can remain in ``candidates``;
- ``no_solo_candidates`` returns ``best=None``, so nothing may be sliced off.

These two functions replace position with IDENTITY: exclude whichever
candidate canonically represents the same courier as the selected one,
deduping the remaining pool, regardless of where either sits in the list.
Origin: ``shadow_dispatcher._serialize_result`` (commit 863d11c); extracted
here so the same fix covers every producer of a ``best`` + "alternatives"
pair, not only the one that happened to be audited first (Przykazanie #0 —
bliźniacze ścieżki razem).
"""
from __future__ import annotations

from typing import Any, Hashable, List, Optional, Tuple

from .schema import canon_cid

__all__ = ["candidate_identity_key", "alternative_candidates"]


def candidate_identity_key(candidate: Any) -> Tuple[str, Hashable]:
    """Stable per-courier key; preserve distinct malformed candidates safely."""
    cid = canon_cid(getattr(candidate, "courier_id", None))
    if cid:
        return ("cid", cid)
    # A missing CID is invalid at the identity boundary.  Do not silently merge
    # unrelated malformed candidates; only recognise the exact same object.
    return ("object", id(candidate))


def alternative_candidates(candidates: List[Any], best: Optional[Any]) -> List[Any]:
    """Return each non-selected courier once, preserving candidate order."""
    best_key = candidate_identity_key(best) if best is not None else None
    seen = set()
    alternatives: List[Any] = []
    for candidate in candidates:
        key = candidate_identity_key(candidate)
        if best_key is not None and key == best_key:
            continue
        if key in seen:
            continue
        seen.add(key)
        alternatives.append(candidate)
    return alternatives
