"""plan_recheck — V3.19c sub C periodic consistency checker.

Standalone script. Reads courier_plans.json + orders_state.json. For each
non-invalidated plan, verifies invariants:
  1. Every stop.order_id exists in orders_state.
  2. Status of each order is 'assigned' or 'picked_up' (not delivered/
     cancelled/returned).
  3. Plan age (now - last_modified_at) under threshold.

Rozbieżności → structured log to plan_recheck_log.jsonl. Auto-invalidate
(AUTO_INVALIDATE_STALE=True env) gdy znaleziony delivered/cancelled order
w plan.

NIE re-optymalizuje TSP (deferred V3.19d — wymaga read integration).
NIE modyfikuje scoring path — read-only + optional invalidate.

Invocation: python3 -m dispatch_v2.plan_recheck (stdlib only, no deps).
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

from dispatch_v2 import plan_manager

_log = logging.getLogger("plan_recheck")
if not _log.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    _log.addHandler(handler)
    _log.setLevel(logging.INFO)

RECHECK_LOG_PATH = Path(
    "/root/.openclaw/workspace/dispatch_state/plan_recheck_log.jsonl"
)
ORDERS_STATE_PATH = Path(
    "/root/.openclaw/workspace/dispatch_state/orders_state.json"
)

AUTO_INVALIDATE_STALE = os.environ.get("AUTO_INVALIDATE_STALE", "0") == "1"

MAX_PLAN_AGE_MIN = int(os.environ.get("MAX_PLAN_AGE_MIN", "120"))

TERMINAL_STATUSES = frozenset({"delivered", "cancelled", "returned_to_pool"})


def _load_orders_state() -> Dict[str, Any]:
    if not ORDERS_STATE_PATH.exists():
        return {}
    try:
        with open(ORDERS_STATE_PATH) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception as e:
        _log.warning(f"orders_state load fail: {e}")
        return {}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _log_recheck_entry(entry: Dict[str, Any]) -> None:
    try:
        RECHECK_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(RECHECK_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        _log.warning(f"recheck log write fail: {e}")


def _check_plan(cid: str, plan: Dict[str, Any],
                orders_state: Dict[str, Any],
                now: datetime) -> Dict[str, Any]:
    """Return structured finding dict. issues list is empty when plan healthy."""
    issues: List[str] = []
    auto_invalidate_reason: Optional[str] = None

    stops = plan.get("stops") or []
    stop_oids = {str(s.get("order_id")) for s in stops}

    missing = []
    terminal = []
    for oid in stop_oids:
        rec = orders_state.get(oid)
        if not rec:
            missing.append(oid)
            continue
        st = rec.get("status")
        if st in TERMINAL_STATUSES:
            terminal.append((oid, st))

    if missing:
        issues.append(f"missing_in_orders_state:{','.join(missing)}")
    if terminal:
        issues.append(f"terminal_status:{','.join(f'{o}={s}' for o,s in terminal)}")
        auto_invalidate_reason = "ORDER_DELIVERED_ALL" if all(
            s == "delivered" for _, s in terminal
        ) else "ORDER_CANCELLED"

    # age check
    age_min = None
    try:
        lm = plan.get("last_modified_at")
        if lm:
            lm_dt = datetime.fromisoformat(lm.replace("Z", "+00:00"))
            if lm_dt.tzinfo is None:
                lm_dt = lm_dt.replace(tzinfo=timezone.utc)
            age_min = (now - lm_dt).total_seconds() / 60.0
            if age_min > MAX_PLAN_AGE_MIN:
                issues.append(f"stale_age:{age_min:.1f}min")
    except Exception:
        pass

    return {
        "ts": now.isoformat(),
        "cid": cid,
        "plan_version": plan.get("plan_version"),
        "age_min": round(age_min, 1) if age_min is not None else None,
        "stops_count": len(stops),
        "missing_orders": missing,
        "terminal_orders": [{"oid": o, "status": s} for o, s in terminal],
        "issues": issues,
        "auto_invalidate_reason": auto_invalidate_reason,
    }


def run_recheck() -> Dict[str, Any]:
    """Main entry point. Returns summary dict."""
    now = _now_utc()
    orders_state = _load_orders_state()
    plans = plan_manager.load_plans()

    summary = {
        "ts": now.isoformat(),
        "total_plans": 0,
        "active_plans": 0,
        "healthy": 0,
        "with_issues": 0,
        "auto_invalidated": 0,
    }

    findings: List[Dict[str, Any]] = []
    for cid, plan in plans.items():
        summary["total_plans"] += 1
        if plan.get("invalidated_at") is not None:
            continue
        summary["active_plans"] += 1
        finding = _check_plan(cid, plan, orders_state, now)
        if finding["issues"]:
            summary["with_issues"] += 1
            findings.append(finding)
            _log_recheck_entry(finding)
            if AUTO_INVALIDATE_STALE and finding.get("auto_invalidate_reason"):
                plan_manager.invalidate_plan(cid, finding["auto_invalidate_reason"])
                summary["auto_invalidated"] += 1
                _log.info(
                    f"AUTO_INVALIDATE cid={cid} reason={finding['auto_invalidate_reason']}"
                )
        else:
            summary["healthy"] += 1

    _log.info(f"PLAN_RECHECK summary={summary}")
    return summary


if __name__ == "__main__":
    sys.exit(0 if run_recheck()["auto_invalidated"] == 0 or AUTO_INVALIDATE_STALE else 1)
