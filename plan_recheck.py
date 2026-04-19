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
GPS_PWA_PATH = Path(
    "/root/.openclaw/workspace/dispatch_state/gps_positions_pwa.json"
)

AUTO_INVALIDATE_STALE = os.environ.get("AUTO_INVALIDATE_STALE", "0") == "1"

# V3.19c sub D: GPS drift check.
# True → gdy kurier GPS > GPS_DRIFT_THRESHOLD_M od plan.start_pos i flag
# ENABLE_GPS_DRIFT_INVALIDATION → plan_manager.mark_stale(cid, "GPS_DRIFT").
# Default OFF — shadow observation tylko.
ENABLE_GPS_DRIFT_INVALIDATION = os.environ.get(
    "ENABLE_GPS_DRIFT_INVALIDATION", "0"
) == "1"
GPS_DRIFT_THRESHOLD_M = int(os.environ.get("GPS_DRIFT_THRESHOLD_M", "500"))
GPS_DRIFT_FRESHNESS_MIN = int(os.environ.get("GPS_DRIFT_FRESHNESS_MIN", "5"))

MAX_PLAN_AGE_MIN = int(os.environ.get("MAX_PLAN_AGE_MIN", "120"))

TERMINAL_STATUSES = frozenset({"delivered", "cancelled", "returned_to_pool"})


def _haversine_m(p1: tuple, p2: tuple) -> float:
    """Distance in meters between 2 (lat, lng) pairs."""
    import math
    lat1, lng1 = p1
    lat2, lng2 = p2
    R = 6371008.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _load_gps_positions() -> Dict[str, Any]:
    if not GPS_PWA_PATH.exists():
        return {}
    try:
        with open(GPS_PWA_PATH) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception as e:
        _log.warning(f"gps_positions load fail: {e}")
        return {}


def _gps_drift_check(cid: str, plan: Dict[str, Any],
                     gps_positions: Dict[str, Any],
                     now: datetime) -> Optional[Dict[str, Any]]:
    """Return finding dict {drift_m, age_min, gps_pos, start_pos} if GPS fresh
    AND drift > threshold, else None.
    """
    gps = gps_positions.get(cid)
    if not gps:
        return None
    try:
        ts_str = gps.get("timestamp")
        if not ts_str:
            return None
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_min = (now - ts).total_seconds() / 60.0
    except Exception:
        return None
    if age_min < 0 or age_min > GPS_DRIFT_FRESHNESS_MIN:
        return None  # stale GPS not used for drift detection
    gps_lat = gps.get("lat")
    gps_lon = gps.get("lon")
    if gps_lat is None or gps_lon is None:
        return None
    sp = plan.get("start_pos") or {}
    sp_lat = sp.get("lat")
    sp_lng = sp.get("lng")
    if sp_lat is None or sp_lng is None:
        return None
    # Placeholder start_pos (0,0) — saved from V3.19b hook without coords
    if (sp_lat, sp_lng) == (0.0, 0.0):
        return None
    drift = _haversine_m((gps_lat, gps_lon), (sp_lat, sp_lng))
    if drift <= GPS_DRIFT_THRESHOLD_M:
        return None
    return {
        "drift_m": round(drift, 1),
        "gps_age_min": round(age_min, 1),
        "gps_pos": [gps_lat, gps_lon],
        "start_pos": [sp_lat, sp_lng],
    }


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
                gps_positions: Dict[str, Any],
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

    # V3.19c sub D: GPS drift check
    gps_drift = _gps_drift_check(cid, plan, gps_positions, now)
    if gps_drift:
        issues.append(f"gps_drift:{gps_drift['drift_m']}m")

    return {
        "ts": now.isoformat(),
        "cid": cid,
        "plan_version": plan.get("plan_version"),
        "age_min": round(age_min, 1) if age_min is not None else None,
        "stops_count": len(stops),
        "missing_orders": missing,
        "terminal_orders": [{"oid": o, "status": s} for o, s in terminal],
        "gps_drift": gps_drift,
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

    gps_positions = _load_gps_positions()
    summary["gps_drift_detected"] = 0
    summary["gps_drift_invalidated"] = 0

    findings: List[Dict[str, Any]] = []
    for cid, plan in plans.items():
        summary["total_plans"] += 1
        if plan.get("invalidated_at") is not None:
            continue
        summary["active_plans"] += 1
        finding = _check_plan(cid, plan, orders_state, gps_positions, now)
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
            if finding.get("gps_drift"):
                summary["gps_drift_detected"] += 1
                if ENABLE_GPS_DRIFT_INVALIDATION:
                    plan_manager.mark_stale(cid, "GPS_DRIFT")
                    summary["gps_drift_invalidated"] += 1
                    _log.info(
                        f"GPS_DRIFT_INVALIDATE cid={cid} drift={finding['gps_drift']['drift_m']}m"
                    )
        else:
            summary["healthy"] += 1

    _log.info(f"PLAN_RECHECK summary={summary}")
    return summary


if __name__ == "__main__":
    sys.exit(0 if run_recheck()["auto_invalidated"] == 0 or AUTO_INVALIDATE_STALE else 1)
