"""CandidateLogger — centralized per-candidate decision logging.

Single source of truth dla dispatch decision observability. Reusable z:
  - czasowka_scheduler eval_czasowka
  - dispatch_pipeline assess_order
  - courier_resolver dispatchable_fleet

Z3 design:
  - Flag-gated: OBSERVABILITY_PER_CANDIDATE_ENABLED default False
  - Defensive: każdy log call try/except — NIGDY nie crashes caller
  - Performance: <5ms per decision (JSONL append + fsync, no async)
  - Atomic: fcntl.flock LOCK_EX podczas append (multi-proc safe)
  - Lazy serialization: dict construction tylko gdy flag enabled
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional


DEFAULT_LOG_DIR = Path("/root/.openclaw/workspace/dispatch_state/observability")
CANDIDATE_LOG_PREFIX = "candidate_decisions"  # rotated: candidate_decisions_YYYYMMDD.jsonl
FLEET_FILTER_LOG_PREFIX = "fleet_filter"      # rotated: fleet_filter_YYYYMMDD.jsonl

_log = logging.getLogger("observability.candidate_logger")


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _today_yyyymmdd() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d")


def _atomic_append(path: Path, line: str) -> None:
    """fcntl.flock-protected single-line append + fsync.

    Z3: never raises — caller resilient to logger failure.
    """
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            try:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
                f.write(line + "\n")
                f.flush()
                os.fsync(f.fileno())
            finally:
                try:
                    fcntl.flock(f.fileno(), fcntl.LOCK_UN)
                except Exception:
                    pass
    except Exception as e:
        # Defensive: NIGDY nie raise w caller. Log do stderr/syslog only.
        _log.warning(f"observability append fail (non-blocking): {type(e).__name__}: {e}")


class CandidateLogger:
    """Per-candidate decision logger.

    Usage:
      logger = CandidateLogger(flag_check_fn=lambda: flag("OBSERVABILITY_PER_CANDIDATE_ENABLED"))
      logger.log_evaluation(
          source="czasowka_scheduler",
          order_id="470559",
          context={"trigger_min_before": 40, ...},
          best=best_candidate_dict,
          alts=alts_list,
          rejected=rejected_list,
          decision={"verdict": "KOORD", "reason": "no MAYBE candidate"},
      )

    Flag check happens FIRST — gdy disabled, zero serialization overhead.
    """

    def __init__(
        self,
        flag_check_fn: Optional[Callable[[], bool]] = None,
        fleet_filter_flag_fn: Optional[Callable[[], bool]] = None,
        log_dir: Optional[Path] = None,
    ):
        """flag_check_fn: zwraca True gdy logging enabled.
        fleet_filter_flag_fn: osobna flaga dla fleet filter logging (more verbose).
        log_dir: override default location."""
        self._flag_check = flag_check_fn or (lambda: False)
        self._fleet_flag_check = fleet_filter_flag_fn or (lambda: False)
        self._log_dir = Path(log_dir) if log_dir else DEFAULT_LOG_DIR

    def _candidate_log_path(self) -> Path:
        return self._log_dir / f"{CANDIDATE_LOG_PREFIX}_{_today_yyyymmdd()}.jsonl"

    def _fleet_log_path(self) -> Path:
        return self._log_dir / f"{FLEET_FILTER_LOG_PREFIX}_{_today_yyyymmdd()}.jsonl"

    def log_evaluation(
        self,
        source: str,
        order_id: str,
        context: Dict[str, Any],
        candidates_evaluated: List[Dict[str, Any]],
        decision: Dict[str, Any],
        fleet_size_total: Optional[int] = None,
        fleet_size_on_shift: Optional[int] = None,
    ) -> bool:
        """Zwraca True gdy zapisano (flag enabled), False gdy skip.

        candidates_evaluated: lista dict per kandydat z polami:
          cid, panel_name, tier, feasibility_verdict, rejection_gate,
          rejection_params, scoring_attempted, score_total, score_breakdown,
          verdict_tier
        decision: {"verdict", "reason", "best_candidate_cid", "decision_threshold"}
        """
        if not self._flag_check():
            return False
        try:
            record = {
                "ts": _now_iso(),
                "source": source,  # czasowka_scheduler | dispatch_pipeline | ...
                "order_id": str(order_id),
                "context": context,
                "fleet_size_total": fleet_size_total,
                "fleet_size_on_shift": fleet_size_on_shift,
                "candidates_evaluated_count": len(candidates_evaluated),
                "candidates_evaluated": candidates_evaluated,
                "decision": decision,
            }
            line = json.dumps(record, ensure_ascii=False, default=str)
            _atomic_append(self._candidate_log_path(), line)
            return True
        except Exception as e:
            _log.warning(f"log_evaluation fail oid={order_id}: {type(e).__name__}: {e}")
            return False

    def log_fleet_filter(
        self,
        source: str,
        passed: List[Dict[str, Any]],
        rejected: List[Dict[str, Any]],
        context: Optional[Dict[str, Any]] = None,
    ) -> bool:
        """Fleet filter logging. Used przez courier_resolver.dispatchable_fleet.

        passed/rejected: list[dict] z polami: cid, panel_name, reason (rejected only)
        """
        if not self._fleet_flag_check():
            return False
        try:
            record = {
                "ts": _now_iso(),
                "source": source,
                "context": context or {},
                "passed_count": len(passed),
                "rejected_count": len(rejected),
                "passed": passed,
                "rejected": rejected,
            }
            line = json.dumps(record, ensure_ascii=False, default=str)
            _atomic_append(self._fleet_log_path(), line)
            return True
        except Exception as e:
            _log.warning(f"log_fleet_filter fail: {type(e).__name__}: {e}")
            return False


# ---------- Helper: serialize candidate object dla loggera ----------

def serialize_candidate(c: Any, include_score_breakdown: bool = True) -> Dict[str, Any]:
    """Convert candidate-like object (dataclass / dict / namedtuple) → log dict.

    Tolerant na różne shape — używa getattr z defaults.
    """
    def _g(name, default=None):
        if isinstance(c, dict):
            return c.get(name, default)
        return getattr(c, name, default)

    out = {
        "cid": str(_g("courier_id") or _g("cid") or ""),
        "panel_name": _g("name") or _g("panel_name"),
        "tier": _g("tier") or _g("speed_tier"),
        "feasibility_verdict": _g("feasibility_verdict") or _g("feasibility"),
        "feasibility_reason": _g("feasibility_reason") or _g("reason"),
        "score_total": _g("score"),
        "scoring_attempted": _g("score") is not None,
    }
    if include_score_breakdown:
        breakdown = {}
        # Standard score component fields w dispatch_v2
        for k in ("bonus_l1", "bonus_l2", "bonus_r4", "bundle_bonus",
                  "timing_gap_bonus", "bonus_r1_soft_pen", "bonus_r5_soft_pen",
                  "bonus_r6_soft_pen", "bonus_r8_soft_pen", "bonus_r9_stopover",
                  "bonus_r9_wait_pen", "bonus_v3273_wait_courier",
                  "bonus_penalty_sum", "v326_speed_score_adjustment",
                  "v325_pre_shift_soft_penalty"):
            v = _g(k)
            if v is not None:
                breakdown[k] = v
        if breakdown:
            out["score_breakdown"] = breakdown
    return out


# ---------- Module-level singleton (lazy-init przez common.flag) ----------

_singleton: Optional[CandidateLogger] = None


def get_logger() -> CandidateLogger:
    """Singleton z lazy flag-binding. Reused across modules."""
    global _singleton
    if _singleton is None:
        try:
            from dispatch_v2.common import flag
            _singleton = CandidateLogger(
                flag_check_fn=lambda: flag("OBSERVABILITY_PER_CANDIDATE_ENABLED", default=False),
                fleet_filter_flag_fn=lambda: flag("OBSERVABILITY_FLEET_FILTER_LOGGING", default=False),
            )
        except Exception:
            # If common.flag unavailable, return disabled logger
            _singleton = CandidateLogger()
    return _singleton
