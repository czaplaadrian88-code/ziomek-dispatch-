"""TASK A CZASÓWKI PROACTIVE — observability hook (2026-05-05).

Reuses observability.candidate_logger (TASK 3 LIVE 2026-05-04) so that
T-50/T-40 trigger fires get logged with the same per-candidate breakdown
schema as dispatch_pipeline (V3.27) and czasowka_scheduler (V3.24-B).

Differentiated by source='czasowka_proactive' and context.proposal_type
= f'TRIGGER_T{trigger_min}'.

Defensive: NIGDY raises (caller resilient — logger swallows internally
via try/except in candidate_logger._atomic_append).
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Iterable, Optional

from dispatch_v2.observability.candidate_logger import (
    get_logger,
    serialize_candidate,
)


def log_proactive_trigger(
    oid: str,
    trigger_min: int,
    candidates: Iterable[Any],
    picked: Optional[Any],
    decision_verdict: str,
    now_utc: datetime,
    excluded_cids: Optional[set] = None,
    score_threshold: Optional[float] = None,
) -> None:
    """Log per-candidate breakdown via TASK 3 candidate_logger.

    Args:
      oid: order_id string
      trigger_min: 50 | 40 | 0 (T-0 alert) | other (extension via flag)
      candidates: iterable of Candidate-like (best + alternatives merged
                  before call). Empty allowed.
      picked: candidate object that was chosen for proposal, or None.
      decision_verdict: 'PROPOSED' | 'NO_CANDIDATE' | 'RACE_LOST' | 'ALERT_T0'
      now_utc: current UTC timestamp for log record.
      excluded_cids: set of cid strings excluded from candidate pool (NIE
                     decisions from prior triggers). Logged for audit.
      score_threshold: min proposal score floor used for filtering.

    Defensive: NIGDY raises. On any error returns silently.
    """
    try:
        logger = get_logger()
        if not logger._flag_check():
            return

        cands_serialized = []
        for c in candidates or []:
            try:
                cands_serialized.append(serialize_candidate(c))
            except Exception:
                continue

        picked_cid = None
        picked_score = None
        if picked is not None:
            try:
                picked_cid = (
                    getattr(picked, "courier_id", None)
                    or getattr(picked, "cid", None)
                )
                picked_score = getattr(picked, "score", None)
            except Exception:
                pass

        logger.log_evaluation(
            source="czasowka_proactive",
            order_id=oid,
            context={
                "proposal_type": f"TRIGGER_T{trigger_min}",
                "trigger_min_before": trigger_min,
                "now_utc": now_utc.isoformat(),
                "excluded_candidates_count": (
                    len(excluded_cids) if excluded_cids else 0
                ),
                "excluded_cids": (
                    sorted(str(x) for x in excluded_cids) if excluded_cids else []
                ),
                "score_threshold": score_threshold,
            },
            candidates_evaluated=cands_serialized,
            decision={
                "verdict": decision_verdict,
                "best_candidate_cid": (str(picked_cid) if picked_cid else None),
                "best_score": picked_score,
                "decision_threshold": f"czasowka_proactive_t{trigger_min}",
            },
        )
    except Exception:
        # Defensive — observability NIGDY crashes flow.
        pass
