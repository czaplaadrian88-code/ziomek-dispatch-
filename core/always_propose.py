"""Czysta polityka A-3 ``best-of-worst`` dla fallbacku ``no_solo``.

Moduł nie zmienia ``PipelineResult`` i nie wykonuje I/O.  Dostaje zapisane
wyniki kanonicznej próby R29 solo, najpierw zachowuje filtr HARD, a dopiero
potem rozstrzyga najmniejsze naruszenie SOFT.  Gdy cała flota jest HARD-NO,
zwraca nadal deterministyczny *diagnostyczny* CID, ale nigdy nie nadaje mu
statusu feasible ani prawa wykonania.
"""
from __future__ import annotations

import math
from typing import Any, Iterable

from dispatch_v2.core import proposal_output
from dispatch_v2.identity.schema import canon_cid

SCHEMA = "always_propose.best_of_worst.v1"
HARD_SAFE_VERDICTS = frozenset({"MAYBE", "YES"})
SELECTION_RULE = (
    "hard_safe_then_complete_pickup_distance_then_score_then_canonical_cid;"
    "incomplete_soft_evidence_uses_canonical_cid"
)


def _finite_number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _is_hard_safe(candidate: Any) -> bool:
    """Jedyna granica HARD A-3: legalny verdict *i* materialny plan."""
    verdict = str(getattr(candidate, "feasibility_verdict", "") or "").upper()
    return (
        verdict in HARD_SAFE_VERDICTS
        and getattr(candidate, "plan", None) is not None
    )


def _pickup_distance(candidate: Any) -> float | None:
    metrics = getattr(candidate, "metrics", None)
    if not isinstance(metrics, dict):
        return None
    return _finite_number(metrics.get("pickup_dist_km"))


def _score(candidate: Any) -> float | None:
    return _finite_number(getattr(candidate, "score", None))


def _rank_key(candidate: Any) -> tuple:
    """Deterministyczny HARD-before-SOFT klucz, niezależny od kolejności puli."""
    distance = _pickup_distance(candidate)
    score = _score(candidate)
    cid = canon_cid(getattr(candidate, "courier_id", None))
    return (
        0 if _is_hard_safe(candidate) else 1,
        distance if distance is not None else math.inf,
        -(score if score is not None else -math.inf),
        cid,
        str(getattr(candidate, "feasibility_verdict", "") or ""),
        str(getattr(candidate, "feasibility_reason", "") or ""),
    )


def _soft_rank_key(candidate: Any) -> tuple:
    distance = _pickup_distance(candidate)
    score = _score(candidate)
    return (
        distance if distance is not None else math.inf,
        -(score if score is not None else -math.inf),
        canon_cid(getattr(candidate, "courier_id", None)),
    )


def _deduplicate(candidates: Iterable[Any]) -> list[Any]:
    """Jeden deterministyczny zapis na kanoniczny CID; malformed CID odpada."""
    by_cid: dict[str, list[Any]] = {}
    for candidate in candidates:
        cid = canon_cid(getattr(candidate, "courier_id", None))
        if not cid:
            continue
        by_cid.setdefault(cid, []).append(candidate)
    return [min(by_cid[cid], key=_rank_key) for cid in sorted(by_cid)]


def _public_candidate(candidate: Any) -> dict:
    """PII-free, JSON-safe wycinek potrzebny do analizy i późniejszej nauki."""
    pickup_distance = _pickup_distance(candidate)
    return {
        "courier_id": canon_cid(getattr(candidate, "courier_id", None)),
        "hard_safe": _is_hard_safe(candidate),
        "feasibility_verdict": str(
            getattr(candidate, "feasibility_verdict", "") or ""
        ),
        "feasibility_reason": str(
            getattr(candidate, "feasibility_reason", "") or ""
        ),
        "pickup_dist_km": pickup_distance,
        # Bez porównywalnej odległości wewnętrzny sentinel nie jest score'em
        # biznesowym i nie może wyciec jako pozorna kara do learningu.
        "score": _score(candidate) if pickup_distance is not None else None,
        "has_plan": getattr(candidate, "plan", None) is not None,
    }


def build_best_of_worst(candidates: Iterable[Any], *, fleet_count: int) -> dict:
    """Zwróć stabilną podpowiedź A-3 bez zmiany decyzji wykonawczej.

    HARD-safe kandydat zawsze wygrywa z HARD-NO, niezależnie od odległości i
    score.  Jeśli HARD-safe nie istnieje, najbliższy zapis jest tylko
    diagnostycznym kandydatem ``COORDINATOR_ESCALATION``.  ``candidate=None``
    jest legalne wyłącznie, gdy caller naprawdę nie przekazał żadnego członka
    floty; selection ma ratchet zapewniający obserwację każdego fizycznego CID.
    """
    try:
        physical_fleet_count = max(0, int(fleet_count))
    except (TypeError, ValueError):
        physical_fleet_count = 0

    unique = _deduplicate(candidates)
    if physical_fleet_count > 0 and not unique:
        raise ValueError(
            "A3 best-of-worst invariant: non-empty fleet without observable CID"
        )
    hard_safe = [candidate for candidate in unique if _is_hard_safe(candidate)]
    ranked_pool = hard_safe if hard_safe else unique
    soft_evidence_complete = bool(ranked_pool) and all(
        _pickup_distance(candidate) is not None for candidate in ranked_pool
    )
    if not ranked_pool:
        selected = None
    elif soft_evidence_complete:
        selected = min(ranked_pool, key=_soft_rank_key)
    else:
        # Brak GPS/porównywalnej odległości nie może być ukrytą karą ani
        # bonusem. Gdy dowód SOFT jest niepełny, ignorujemy go dla CAŁEJ klasy
        # i używamy wyłącznie neutralnego, deterministycznego CID.
        selected = min(
            ranked_pool,
            key=lambda candidate: canon_cid(
                getattr(candidate, "courier_id", None)
            ),
        )
    output_type = (
        proposal_output.LEAST_DAMAGE_ALERT
        if selected is not None and _is_hard_safe(selected)
        else proposal_output.COORDINATOR_ESCALATION
    )
    return {
        "schema": SCHEMA,
        "selection_rule": SELECTION_RULE,
        "fleet_count": physical_fleet_count,
        "observed_candidate_count": len(unique),
        "hard_safe_count": len(hard_safe),
        "soft_evidence_complete": soft_evidence_complete,
        "proposal_output_type": output_type,
        "recommend_only": True,
        "candidate": _public_candidate(selected) if selected is not None else None,
    }
