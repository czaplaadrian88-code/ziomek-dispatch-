"""plan_manager — V3.19b saved plans persistence.

Persists per-courier TSP route plans (sequence + ETAs + start_pos) across dispatch
decisions. Replaces "fresh re-TSP per propose" with incremental load/save/insert,
giving coherence between Telegram display and scoring, plus basis for V3.19c
periodic re-check.

Storage: /root/.openclaw/workspace/dispatch_state/courier_plans.json.
Concurrency: fcntl LOCK_EX (write) / LOCK_SH (read) on a companion lockfile.
Atomicity: temp file → fsync → os.replace (POSIX atomic rename).
Schema: see /tmp/v319_schema.json (JSON Schema Draft 2020-12). Top-level is a
flat dict keyed by courier_id string; each value is a CourierPlan.

Pure library — no imports from dispatch_pipeline / panel_watcher (one-way).
"""
from __future__ import annotations

import fcntl
import json
import logging
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

_log = logging.getLogger("plan_manager")

PLANS_FILE = Path("/root/.openclaw/workspace/dispatch_state/courier_plans.json")
LOCK_FILE = Path("/root/.openclaw/workspace/dispatch_state/courier_plans.lock")
SCHEMA_VERSION = 1

INVALIDATION_REASONS = frozenset({
    "ORDER_DELIVERED_ALL",
    "ORDER_CANCELLED",
    "GPS_DRIFT",
    "SHIFT_END",
    "MANUAL",
    "SCHEMA_UPGRADE",
})


def _ensure_parent() -> None:
    PLANS_FILE.parent.mkdir(parents=True, exist_ok=True)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


@contextmanager
def _locked(exclusive: bool):
    """File lock guard. Uses a dedicated lockfile to survive PLANS_FILE rename."""
    _ensure_parent()
    LOCK_FILE.touch(exist_ok=True)
    mode = "r+b"
    with open(LOCK_FILE, mode) as lockfh:
        fcntl.flock(lockfh.fileno(), fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH)
        try:
            yield
        finally:
            fcntl.flock(lockfh.fileno(), fcntl.LOCK_UN)


def _atomic_write(path: Path, data: Any) -> None:
    """Write JSON atomically: temp file in same dir → fsync → os.replace."""
    _ensure_parent()
    fd, tmp_name = tempfile.mkstemp(
        prefix=path.name + ".",
        suffix=".tmp",
        dir=str(path.parent),
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(data, fh, ensure_ascii=False, separators=(",", ":"))
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp_name, path)
    except Exception:
        try:
            os.unlink(tmp_name)
        except FileNotFoundError:
            pass
        raise


def _read_raw() -> Dict[str, Any]:
    """Load entire plans dict. Must be called under shared or exclusive lock."""
    if not PLANS_FILE.exists():
        return {}
    try:
        with open(PLANS_FILE, "r", encoding="utf-8") as fh:
            data = json.load(fh)
        if not isinstance(data, dict):
            _log.warning("courier_plans.json is not an object; treating as empty")
            return {}
        return data
    except json.JSONDecodeError as e:
        _log.error(f"courier_plans.json corrupt: {e}")
        return {}


def _write_raw(data: Dict[str, Any]) -> None:
    """Write entire plans dict. Must be called under exclusive lock."""
    _atomic_write(PLANS_FILE, data)


# ---- public API ----

def load_plans() -> Dict[str, Any]:
    """Load all plans (read-only copy). Shared lock."""
    with _locked(exclusive=False):
        return _read_raw()


def load_plan(
    courier_id: str,
    active_bag_oids: Optional[set] = None,
) -> Optional[Dict[str, Any]]:
    """Load a single plan. If active_bag_oids provided and any plan stop's
    order_id is outside that set → invalidate on read (mismatch with reality).

    Returns None if no plan or plan invalidated.
    """
    cid = str(courier_id)
    with _locked(exclusive=False):
        plans = _read_raw()
    plan = plans.get(cid)
    if plan is None:
        return None
    if plan.get("invalidated_at") is not None:
        return None
    if active_bag_oids is not None:
        plan_oids = {s["order_id"] for s in plan.get("stops", [])
                     if s.get("type") == "dropoff"}
        if plan_oids and not plan_oids.issubset(active_bag_oids):
            invalidate_plan(cid, "ORDER_DELIVERED_ALL")
            return None
    return plan


def save_plan(
    courier_id: str,
    plan_body: Dict[str, Any],
    expected_version: Optional[int] = None,
) -> Dict[str, Any]:
    """Persist a plan. plan_body must contain: start_pos, start_ts, stops,
    optimization_method. plan_version and timestamps are managed here.

    expected_version: optimistic CAS. If current version != expected, raises
    ConcurrencyError. None = accept any prior version (create-or-overwrite).

    Returns the saved plan (with final plan_version).
    """
    cid = str(courier_id)
    _validate_plan_body(plan_body)
    with _locked(exclusive=True):
        plans = _read_raw()
        current = plans.get(cid)
        prev_version = (current or {}).get("plan_version", 0)
        if expected_version is not None and prev_version != expected_version:
            raise ConcurrencyError(
                f"CAS fail for courier {cid}: "
                f"expected_version={expected_version}, current={prev_version}"
            )
        new_version = prev_version + 1
        now_iso = _now_iso()
        created_at = (current or {}).get("created_at", now_iso)
        saved = {
            "plan_version": new_version,
            "created_at": created_at,
            "last_modified_at": now_iso,
            "start_pos": plan_body["start_pos"],
            "start_ts": plan_body["start_ts"],
            "stops": plan_body["stops"],
            "optimization_method": plan_body["optimization_method"],
            "invalidated_at": None,
            "invalidation_reason": None,
        }
        plans[cid] = saved
        _write_raw(plans)
        return saved


def invalidate_plan(courier_id: str, reason: str) -> None:
    """Mark plan invalidated. Plan stays in file for debug + GC-able."""
    cid = str(courier_id)
    if reason not in INVALIDATION_REASONS:
        _log.warning(f"invalidate_plan: unknown reason {reason!r}, allowing")
    with _locked(exclusive=True):
        plans = _read_raw()
        plan = plans.get(cid)
        if plan is None:
            return
        plan["invalidated_at"] = _now_iso()
        plan["invalidation_reason"] = reason
        plan["last_modified_at"] = plan["invalidated_at"]
        _write_raw(plans)


def advance_plan(
    courier_id: str,
    delivered_order_id: str,
    delivered_at: str,
    delivery_coords: Optional[Tuple[float, float]] = None,
) -> None:
    """Remove the dropoff stop for delivered_order_id; update start_pos to the
    delivery location + start_ts to delivered_at. If no stops remain, invalidate
    with reason ORDER_DELIVERED_ALL.
    """
    cid = str(courier_id)
    doid = str(delivered_order_id)
    with _locked(exclusive=True):
        plans = _read_raw()
        plan = plans.get(cid)
        if plan is None or plan.get("invalidated_at") is not None:
            return
        # Remove both pickup and dropoff for delivered order — once delivered,
        # pickup is definitionally in the past and shouldn't linger in the plan.
        new_stops = [
            s for s in plan.get("stops", []) if s.get("order_id") != doid
        ]
        if not new_stops:
            plan["invalidated_at"] = _now_iso()
            plan["invalidation_reason"] = "ORDER_DELIVERED_ALL"
            plan["last_modified_at"] = plan["invalidated_at"]
        else:
            plan["stops"] = new_stops
            if delivery_coords is not None:
                plan["start_pos"] = {
                    "lat": float(delivery_coords[0]),
                    "lng": float(delivery_coords[1]),
                    "source": "last_delivered",
                    "source_ts": delivered_at,
                }
            plan["start_ts"] = delivered_at
            plan["plan_version"] = plan.get("plan_version", 0) + 1
            plan["last_modified_at"] = _now_iso()
        _write_raw(plans)


def remove_stops(courier_id: str, order_id: str) -> None:
    """Remove ALL stops (pickup AND dropoff) for order_id. For ORDER_CANCELLED
    / ORDER_RETURNED_TO_POOL path. If plan empty after removal, invalidate.
    """
    cid = str(courier_id)
    oid = str(order_id)
    with _locked(exclusive=True):
        plans = _read_raw()
        plan = plans.get(cid)
        if plan is None or plan.get("invalidated_at") is not None:
            return
        new_stops = [s for s in plan.get("stops", []) if s.get("order_id") != oid]
        if not new_stops:
            plan["invalidated_at"] = _now_iso()
            plan["invalidation_reason"] = "ORDER_CANCELLED"
            plan["last_modified_at"] = plan["invalidated_at"]
        else:
            plan["stops"] = new_stops
            plan["plan_version"] = plan.get("plan_version", 0) + 1
            plan["last_modified_at"] = _now_iso()
        _write_raw(plans)


def mark_stale(courier_id: str, reason: str = "GPS_DRIFT") -> None:
    """Alias of invalidate_plan for GPS-drift scenarios."""
    invalidate_plan(courier_id, reason)


def insert_stop_optimal(
    plan: Dict[str, Any],
    new_order_stops: List[Dict[str, Any]],
    now: datetime,
    leg_min_fn,
) -> Dict[str, Any]:
    """Pure function: given an existing plan + new stops for ONE order
    (pickup+dropoff, or dropoff-only if picked_up), try every legal insertion
    position for the new stops as a block, returning the plan with minimum total
    duration. No I/O.

    leg_min_fn(from_coords, to_coords) -> float minutes.

    Enforces pickup-before-dropoff for the new order when both stops present.
    Does NOT reorder existing stops (incremental, not re-TSP).
    """
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    existing = list(plan.get("stops", []))
    start_pos = plan["start_pos"]
    start_coords = (float(start_pos["lat"]), float(start_pos["lng"]))

    new_pickup = next((s for s in new_order_stops if s.get("type") == "pickup"), None)
    new_dropoff = next((s for s in new_order_stops if s.get("type") == "dropoff"), None)
    if new_dropoff is None:
        raise ValueError("insert_stop_optimal requires at least a dropoff stop")

    best_plan: Optional[Dict[str, Any]] = None
    best_total: float = float("inf")

    n = len(existing)
    for d_pos in range(n + 1):
        if new_pickup is None:
            candidate = existing[:d_pos] + [new_dropoff] + existing[d_pos:]
            total = _sequence_total_min(start_coords, candidate, leg_min_fn)
            if total < best_total:
                best_total = total
                best_plan = _build_plan_like(plan, candidate, total)
            continue
        for p_pos in range(d_pos + 1):
            # p_pos <= d_pos → after inserting pickup first then dropoff at d_pos+1
            candidate = (
                existing[:p_pos]
                + [new_pickup]
                + existing[p_pos:d_pos]
                + [new_dropoff]
                + existing[d_pos:]
            )
            total = _sequence_total_min(start_coords, candidate, leg_min_fn)
            if total < best_total:
                best_total = total
                best_plan = _build_plan_like(plan, candidate, total)

    if best_plan is None:
        raise RuntimeError("insert_stop_optimal: no valid sequence found")
    return best_plan


def _sequence_total_min(
    start_coords: Tuple[float, float],
    stops: List[Dict[str, Any]],
    leg_min_fn,
) -> float:
    total = 0.0
    current = start_coords
    for s in stops:
        c = s.get("coords", {})
        nxt = (float(c.get("lat", 0.0)), float(c.get("lng", 0.0)))
        total += leg_min_fn(current, nxt)
        total += float(s.get("dwell_min", 0.0))
        current = nxt
    return total


def _build_plan_like(base: Dict[str, Any], stops: List[Dict[str, Any]],
                     total_min: float) -> Dict[str, Any]:
    """Clone base plan (shallow) with updated stops + optimization_method=incremental."""
    return {
        "start_pos": base["start_pos"],
        "start_ts": base["start_ts"],
        "stops": stops,
        "optimization_method": "incremental",
        # plan_version, created_at, last_modified_at are handled by save_plan.
        "_total_duration_min": round(total_min, 2),
    }


def _validate_plan_body(plan_body: Dict[str, Any]) -> None:
    for key in ("start_pos", "start_ts", "stops", "optimization_method"):
        if key not in plan_body:
            raise ValueError(f"plan_body missing required key: {key}")
    sp = plan_body["start_pos"]
    for k in ("lat", "lng", "source"):
        if k not in sp:
            raise ValueError(f"start_pos missing {k}")
    if plan_body["optimization_method"] not in {"bruteforce", "greedy", "incremental"}:
        raise ValueError(
            f"invalid optimization_method: {plan_body['optimization_method']!r}"
        )
    for s in plan_body["stops"]:
        if s.get("type") not in {"pickup", "dropoff"}:
            raise ValueError(f"invalid stop type: {s.get('type')!r}")
        if "order_id" not in s or "coords" not in s:
            raise ValueError(f"stop missing order_id or coords: {s}")


class ConcurrencyError(RuntimeError):
    """Raised when save_plan's expected_version CAS fails."""
