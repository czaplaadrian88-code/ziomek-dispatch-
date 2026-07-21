"""Decision-time ETA audit log (shadow-safe, default OFF).

The log captures exactly what the dispatcher predicted when a selection or a
plan write completed.  It intentionally contains no courier names, addresses
or coordinates.  All public writers are fail-safe: observability loss is
counted and logged, but can never change or abort the dispatch decision.
"""
from __future__ import annotations

import logging
import math
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from dispatch_v2 import common as C


SCHEMA = "decision_eta.v1"
FLAG = "ENABLE_DECISION_ETA_LOG"
LOG_PATH = Path(
    "/root/.openclaw/workspace/dispatch_state/decision_eta_log.jsonl"
)

_log = logging.getLogger("decision_eta_log")
_stats_lock = threading.Lock()
_stats = {"written": 0, "errors": 0, "skipped_off": 0}
_CONTEXT_ALLOWLIST = frozenset({
    "event_id", "claim_dropped", "minutes_to_pickup", "match_quality",
    "holder_cid", "score_margin", "live_armed",
})


def get_stats() -> dict[str, int]:
    """Return process-local counters for health/tests without exposing records."""
    with _stats_lock:
        return dict(_stats)


def _bump(name: str, amount: int = 1) -> int:
    with _stats_lock:
        _stats[name] = int(_stats.get(name, 0)) + amount
        return _stats[name]


def _record_error(exc: BaseException) -> None:
    count = _bump("errors")
    _log.warning(
        "DECISION_ETA_LOG_FAIL count=%s error=%s:%s",
        count,
        type(exc).__name__,
        exc,
    )


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is None:
            value = value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc).isoformat()
    text = str(value).strip()
    return text or None


def _number(value: Any) -> float | int | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(number):
        return None
    return int(number) if number.is_integer() else round(number, 4)


def _cid(candidate: Any) -> str | None:
    raw = getattr(candidate, "courier_id", None)
    if raw is None or str(raw).strip() == "":
        return None
    return str(raw).strip()


def _as_map(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def _safe_context(value: Mapping[str, Any] | None) -> dict[str, Any]:
    """Allowlist scalar operational fields; never accept free-form PII."""
    out: dict[str, Any] = {}
    for key, raw in (value or {}).items():
        key = str(key)
        if key not in _CONTEXT_ALLOWLIST:
            continue
        if raw is None or isinstance(raw, (str, bool, int)):
            out[key] = raw
        elif isinstance(raw, float):
            out[key] = _number(raw)
    return out


def _plan_legs(candidate: Any, order_id: str) -> list[dict[str, Any]]:
    metrics = _as_map(getattr(candidate, "metrics", None))
    plan = getattr(candidate, "plan", None)
    pickup_map = {
        str(key): value
        for key, value in _as_map(getattr(plan, "pickup_at", None)).items()
    }
    delivery_map = {
        str(key): value
        for key, value in _as_map(
            getattr(plan, "predicted_delivered_at", None)
        ).items()
    }
    order_ids = set(pickup_map)
    order_ids.update(delivery_map)
    if order_id:
        order_ids.add(str(order_id))

    legs: list[dict[str, Any]] = []
    for oid in sorted(order_ids):
        pickup_eta = _iso(pickup_map.get(oid))
        if oid == str(order_id) and pickup_eta is None:
            pickup_eta = _iso(metrics.get("eta_pickup_utc"))
        delivery_eta = _iso(delivery_map.get(oid))
        missing: list[str] = []
        if pickup_eta is None:
            missing.append("pickup_eta_unavailable")
        if delivery_eta is None:
            missing.append("delivery_eta_unavailable")
        legs.append({
            "order_id": oid,
            "pickup_eta_at": pickup_eta,
            "delivery_eta_at": delivery_eta,
            "missing": missing,
        })
    return legs


def _candidate_snapshot(candidate: Any, order_id: str, selected_cid: str | None) -> dict:
    metrics = _as_map(getattr(candidate, "metrics", None))
    cid = _cid(candidate)
    plan = getattr(candidate, "plan", None)
    return {
        "cid": cid,
        "selected": cid is not None and cid == selected_cid,
        "feasibility": getattr(candidate, "feasibility_verdict", None),
        "best_effort": bool(getattr(candidate, "best_effort", False)),
        "score": _number(getattr(candidate, "score", None)),
        "position_source": metrics.get("pos_source"),
        "position_from_store": bool(metrics.get("pos_from_store", False)),
        "position_age_min": _number(metrics.get("pos_age_min")),
        "eta_source": metrics.get("eta_source"),
        "pickup_travel_min": _number(metrics.get("travel_min")),
        "pickup_travel_calibrated_min": _number(metrics.get("travel_min_cal")),
        "plan_strategy": getattr(plan, "strategy", None),
        "legs": _plan_legs(candidate, order_id),
    }


def _ordered_candidates(
    candidates: Iterable[Any], selected: Any | None,
) -> list[Any]:
    out: list[Any] = []
    seen: set[str] = set()
    for candidate in ([selected] if selected is not None else []):
        cid = _cid(candidate)
        if cid is not None and cid not in seen:
            seen.add(cid)
            out.append(candidate)
    for candidate in candidates:
        cid = _cid(candidate)
        if cid is None or cid in seen:
            continue
        seen.add(cid)
        out.append(candidate)
    return out


def _model_provenance(candidates: Sequence[Any]) -> dict[str, Any]:
    lgbm_versions: set[str] = set()
    lgbm_seen = False
    twomodel_seen = False
    for candidate in candidates:
        metrics = _as_map(getattr(candidate, "metrics", None))
        lgbm = _as_map(metrics.get("lgbm_shadow"))
        if lgbm:
            lgbm_seen = True
            version = lgbm.get("model_version")
            if version not in (None, ""):
                lgbm_versions.add(str(version))
        if isinstance(metrics.get("lgbm_twomodel_shadow"), Mapping):
            twomodel_seen = True
    return {
        "primary_selector": "dispatch_pipeline/core.selection",
        "primary_version": None,
        "primary_version_status": "unversioned",
        "lgbm_shadow_seen": lgbm_seen,
        "lgbm_shadow_versions": sorted(lgbm_versions),
        "lgbm_twomodel_seen": twomodel_seen,
        "lgbm_twomodel_version": None,
    }


def _calibration_provenance() -> dict[str, Any]:
    try:
        from dispatch_v2 import calib_maps
        return calib_maps.calibration_provenance()
    except Exception as exc:  # provenance gap is data, not a dispatch failure
        return {"status": "unavailable", "error": type(exc).__name__}


def _emit(build_records: Callable[[], list[dict]]) -> bool:
    """Flag-gated append. Every exception becomes a counter + warning."""
    try:
        if not C.decision_flag(FLAG):
            _bump("skipped_off")
            return False
        records = build_records()
        if not records:
            return False
        error_count = get_stats()["errors"]
        recorded_at = datetime.now(timezone.utc).isoformat()
        for record in records:
            record["schema"] = SCHEMA
            record["recorded_at"] = recorded_at
            record["logger_error_count_before"] = error_count
        from dispatch_v2.core.jsonl_appender import append_jsonl_batch
        written = append_jsonl_batch(LOG_PATH, records)
        _bump("written", int(written))
        return True
    except Exception as exc:  # noqa: BLE001 - telemetry cannot affect decisions
        _record_error(exc)
        return False


def record_candidate_decision(
    *,
    decision_id: str,
    decision_ts: datetime | str,
    decision_kind: str,
    source: str,
    order_id: str,
    outcome: str | None,
    candidates: Iterable[Any],
    selected: Any | None = None,
    selected_cid: str | None = None,
    candidate_pool_scope: str = "top_n",
    context: Mapping[str, Any] | None = None,
) -> bool:
    """Record one final selection without names, addresses or coordinates."""
    def build() -> list[dict]:
        chosen_cid = selected_cid or _cid(selected)
        ordered = _ordered_candidates(candidates, selected)
        snapshots = [
            _candidate_snapshot(candidate, str(order_id), chosen_cid)
            for candidate in ordered
        ]
        return [{
            "decision_id": str(decision_id),
            "decision_ts": _iso(decision_ts),
            "decision_kind": str(decision_kind),
            "source": str(source),
            "order_id": str(order_id),
            "selected_cid": chosen_cid,
            "outcome": outcome,
            "candidate_pool_scope": candidate_pool_scope,
            "candidate_count": len(snapshots),
            "candidates": snapshots,
            "model": _model_provenance(ordered),
            "calibration": _calibration_provenance(),
            "context": _safe_context(context),
        }]

    return _emit(build)


def record_pipeline_decision(
    result: Any,
    *,
    decision_id: str,
    decision_ts: datetime | str,
    decision_kind: str,
    source: str,
    outcome: str | None = None,
    selected_cid: str | None = None,
    context: Mapping[str, Any] | None = None,
) -> bool:
    """Record a PipelineResult, preferring the full pre-top-N candidate pool."""
    full_pool = getattr(result, "full_pool_candidates", None)
    if full_pool is None:
        candidates = list(getattr(result, "candidates", None) or [])
        scope = "top_n_fallback"
    else:
        candidates = list(full_pool or [])
        scope = "full_pool_pre_top_n"
    selected = getattr(result, "best", None)
    return record_candidate_decision(
        decision_id=decision_id,
        decision_ts=decision_ts,
        decision_kind=decision_kind,
        source=source,
        order_id=str(getattr(result, "order_id", "") or ""),
        outcome=outcome if outcome is not None else getattr(result, "verdict", None),
        candidates=candidates,
        selected=selected,
        selected_cid=selected_cid,
        candidate_pool_scope=scope,
        context=context,
    )


def record_plan_commit(
    courier_id: str,
    saved_plan: Mapping[str, Any],
) -> bool:
    """Record every order leg after a plan CAS/write has actually committed."""
    def build() -> list[dict]:
        cid = str(courier_id)
        decision_ts = _iso(saved_plan.get("last_modified_at"))
        version = saved_plan.get("plan_version")
        start_pos = _as_map(saved_plan.get("start_pos"))
        position_source = start_pos.get("source") or "plan_start_unattributed"
        by_order: dict[str, dict[str, Any]] = {}
        for stop in saved_plan.get("stops") or []:
            if not isinstance(stop, Mapping):
                continue
            oid_raw = stop.get("order_id")
            if oid_raw in (None, ""):
                continue
            oid = str(oid_raw)
            leg = by_order.setdefault(oid, {
                "order_id": oid,
                "pickup_eta_at": None,
                "delivery_eta_at": None,
                "missing": [],
            })
            predicted_at = _iso(stop.get("predicted_at") or stop.get("scheduled_at"))
            if stop.get("type") == "pickup":
                leg["pickup_eta_at"] = predicted_at
            elif stop.get("type") in ("dropoff", "delivery"):
                leg["delivery_eta_at"] = predicted_at

        if not by_order:
            by_order[""] = {
                "order_id": "",
                "pickup_eta_at": None,
                "delivery_eta_at": None,
                "missing": ["plan_has_no_order_stops"],
            }
        records: list[dict] = []
        calibration = _calibration_provenance()
        for oid, leg in sorted(by_order.items()):
            missing = list(leg.get("missing") or [])
            if leg.get("pickup_eta_at") is None:
                missing.append("pickup_eta_unavailable")
            if leg.get("delivery_eta_at") is None:
                missing.append("delivery_eta_unavailable")
            leg["missing"] = missing
            records.append({
                "decision_id": f"plan_manager:{cid}:{version}:{oid}",
                "decision_ts": decision_ts,
                "decision_kind": "plan_commit",
                "source": "plan_manager",
                "order_id": oid,
                "selected_cid": cid,
                "outcome": "PLAN_COMMITTED",
                "candidate_pool_scope": "selected_only",
                "candidate_count": 1,
                "candidates": [{
                    "cid": cid,
                    "selected": True,
                    "feasibility": None,
                    "best_effort": False,
                    "score": None,
                    "position_source": position_source,
                    "position_from_store": position_source in {
                        "store", "last_known", "courier_last_pos",
                    },
                    "position_age_min": None,
                    "eta_source": "persisted_plan_stop",
                    "pickup_travel_min": None,
                    "pickup_travel_calibrated_min": None,
                    "plan_strategy": saved_plan.get("optimization_method"),
                    "legs": [leg],
                }],
                "model": {
                    "primary_selector": "plan_manager.save_plan",
                    "primary_version": None,
                    "primary_version_status": "unversioned",
                    "lgbm_shadow_seen": False,
                    "lgbm_shadow_versions": [],
                    "lgbm_twomodel_seen": False,
                    "lgbm_twomodel_version": None,
                },
                "calibration": calibration,
                "context": {
                    "plan_version": version,
                    "optimization_method": saved_plan.get("optimization_method"),
                },
            })
        return records

    return _emit(build)


def _reset_stats_for_tests() -> None:
    with _stats_lock:
        _stats.update({"written": 0, "errors": 0, "skipped_off": 0})
