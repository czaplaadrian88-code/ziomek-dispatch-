"""Canonical alias normalization + score-based courier resolvers (Z-P1-05 Faza A).

READ-ONLY, additive. This module is the single home for the alias-matching
contract that today lives inline in six copies across the codebase. It
reproduces the two DIVERGENT legacy resolvers **1:1** as parameterized, pure
strategies — it deliberately does NOT unify their semantics (that is Faza B):

  * ``resolve_worker``  — port of ``shift_notifications.worker.resolve_cid``.
    Surname prefix asymmetric: schedule⊇alias -> len*10, alias⊇schedule -> len*5.
    Tokens compared with bare ``.lower()`` (NOT the punctuation-stripping
    ``norm`` — this matches the actual legacy code).

  * ``resolve_panel_roster`` — port of ``panel_roster.match_name_to_cid`` /
    ``_score``. Surname prefix symmetric ×10 in BOTH directions. Tokens compared
    with ``norm`` (== legacy ``_norm_token``).

Both keep the exact same order (exact -> case-insensitive exact -> score) and
the same tie=ambiguous behaviour. No I/O, no logging side effects.

The ``norm`` contract intentionally does NOT fold diacritics: ``Ś`` stays ``ś``
(reproducing the cid 376 "Paweł Ściepko" vs "Paweł Sc" landmine — an ascii
abbreviation must not silently match a diacritic surname).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "norm",
    "resolve_worker",
    "resolve_panel_roster",
    "RosterMatch",
    "score_worker_alias",
    "score_panel_roster",
]


def norm(s: Optional[str]) -> str:
    """Canonical alias normalization contract (6 inline copies today).

    ``(s or "").strip().rstrip(".,;:").lower()`` — strips whitespace, trailing
    abbreviation punctuation ("Ch." -> "ch"), lowercases. Does NOT fold
    diacritics (Ś -> ś, not s). "No dots" since 2026-04-24.
    """
    return (s or "").strip().rstrip(".,;:").lower()


# --------------------------------------------------------------------------- #
# Strategy A: worker.resolve_cid  (kurier_ids {alias -> cid}, ×10 / ×5)
# --------------------------------------------------------------------------- #


def score_worker_alias(full_name: str, alias: str) -> int:
    """Score one kurier_ids alias against a schedule full name (worker rules).

    First name must match (bare ``.lower()``). Bare first-name alias -> 1.
    schedule_surname startswith alias_surname -> len(alias_surname)*10.
    alias_surname startswith schedule_surname -> len(schedule_surname)*5.
    Reproduces ``shift_notifications.worker.resolve_cid`` step 3 exactly.
    """
    parts = (full_name or "").strip().split()
    if not parts:
        return 0
    first_lc = parts[0].lower()
    s_last_lc = parts[-1].lower() if len(parts) > 1 else ""

    atokens = (alias or "").strip().split()
    if not atokens:
        return 0
    if atokens[0].lower() != first_lc:
        return 0
    if len(atokens) == 1:
        return 1  # bare first-name alias (e.g. "Adrian")
    a_last_lc = atokens[-1].lower()
    if not s_last_lc:
        return 0
    if s_last_lc.startswith(a_last_lc):
        return len(a_last_lc) * 10
    if a_last_lc.startswith(s_last_lc):
        return len(s_last_lc) * 5
    return 0


def resolve_worker(
    full_name: str,
    mapping: Optional[Dict[str, Any]],
    *,
    bare_key_strict: bool = False,
) -> Optional[Any]:
    """Port of ``shift_notifications.worker.resolve_cid`` (score v2).

    Returns the raw cid value from ``mapping`` (type preserved — int or str, to
    stay 1:1 with the legacy return) or ``None`` (no match / ambiguous tie).

    ``bare_key_strict`` reproduces ``new_courier_pairing._resolve_cid_trusted``:
    for a multi-word name, single-word (bare first-name) keys are filtered out of
    the mapping before scoring, so a bare key ("Gabriel" -> 179) cannot silently
    swallow a new courier. Single-word input names are resolved normally.
    """
    if not full_name:
        return None
    if mapping is None:
        mapping = {}
    if not mapping:
        return None

    if bare_key_strict and " " in full_name.strip():
        mapping = {k: v for k, v in mapping.items() if " " in (k or "").strip()}
        if not mapping:
            return None

    # 1. exact (case-sensitive)
    if full_name in mapping:
        return mapping[full_name]

    # 2. case-insensitive exact (first winner, insertion order)
    fn_lc = full_name.lower()
    for key, cid in mapping.items():
        if key.lower() == fn_lc:
            return cid

    # 3. score-based fallback
    scored: List[Tuple[int, Any, str]] = []
    for alias, cid in mapping.items():
        sc = score_worker_alias(full_name, alias)
        if sc > 0:
            scored.append((sc, cid, alias))
    if not scored:
        return None
    scored.sort(key=lambda x: -x[0])
    best_score = scored[0][0]
    if len(scored) > 1 and scored[1][0] == best_score:
        return None  # ambiguous tie
    return scored[0][1]


# --------------------------------------------------------------------------- #
# Strategy B: panel_roster.match_name_to_cid  ({cid -> name}, ×10 / ×10)
# --------------------------------------------------------------------------- #


@dataclass
class RosterMatch:
    """Result of the panel_roster strategy (mirrors panel_roster.MatchResult)."""

    status: str  # "matched" | "none" | "ambiguous"
    cid: Optional[Any] = None
    name: Optional[str] = None
    score: int = 0
    candidates: List[Tuple[Any, str, int]] = field(default_factory=list)


def score_panel_roster(full_name: str, roster_name: str) -> int:
    """Port of ``panel_roster._score`` — symmetric ×10 surname prefix.

    First name must match under ``norm`` (== legacy ``_norm_token``). Bare
    first-name roster entry -> 1. Both prefix directions score ``len(prefix)*10``
    (unlike the worker strategy's ×5 reverse direction).
    """
    sp = [t for t in (full_name or "").strip().split() if t]
    rp = [t for t in (roster_name or "").strip().split() if t]
    if not sp or not rp:
        return 0
    if norm(sp[0]) != norm(rp[0]):
        return 0
    s_last = norm(sp[-1]) if len(sp) > 1 else ""
    r_last = norm(rp[-1]) if len(rp) > 1 else ""
    if not r_last:
        return 1  # roster has bare first name only
    if not s_last:
        return 0  # grafik has only first name, roster has a surname -> mismatch
    if s_last.startswith(r_last):
        return len(r_last) * 10
    if r_last.startswith(s_last):
        return len(s_last) * 10
    return 0


def resolve_panel_roster(
    full_name: str,
    roster: Optional[Dict[Any, str]],
) -> RosterMatch:
    """Port of ``panel_roster.match_name_to_cid`` — conservative tie=ambiguous.

    ``roster`` is ``{cid -> display_name}``. Returns a :class:`RosterMatch`.
    """
    if not roster:
        return RosterMatch(status="none")
    scored = [
        (cid, name, score_panel_roster(full_name, name))
        for cid, name in roster.items()
    ]
    scored = [t for t in scored if t[2] > 0]
    scored.sort(key=lambda t: -t[2])
    if not scored:
        return RosterMatch(status="none", candidates=[])
    best = scored[0]
    if len(scored) > 1 and scored[1][2] == best[2]:
        return RosterMatch(status="ambiguous", candidates=scored[:5])
    return RosterMatch(
        status="matched",
        cid=best[0],
        name=best[1],
        score=best[2],
        candidates=scored[:5],
    )
