"""Log-only dual-selector instrument for the C7 normal path.

The production result is an immutable oracle from this module's perspective.
Two isolated copies of the complete pre-top-N candidate pool are selected by
the canonical selector with C7 forced OFF and ON in the current thread.
Failures only attach a small PII-free error marker.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass
import hashlib
import logging
import os
from pathlib import Path
import re
import subprocess
import time
from typing import Any, Callable, Optional

from dispatch_v2 import common as C


SCHEMA = "c7_normal_path.v1"
_C7_FLAG = "ENABLE_POST_SHIFT_OVERRUN_PENALTY"
_log = logging.getLogger("dispatch.c7_normal_path")
_CODE_SHA: Optional[str] = None
_SHA_RE = re.compile(r"^[0-9a-fA-F]{40}(?:[0-9a-fA-F]{24})?$")


class InstrumentMismatch(AssertionError):
    """OFF arm differs from the production selection oracle."""

    def __init__(self, fields):
        self.fields = sorted(fields)
        super().__init__("OFF parity mismatch: " + ",".join(self.fields))


@dataclass
class Prepared:
    """Two independent arms captured before production selection/top-N."""

    off_candidates: list
    on_candidates: list
    full_pool_size: int
    full_feasible_size: int
    prepare_ms: float


def _number(value, default=0.0):
    return float(value) if isinstance(value, (int, float)) else float(default)


def _configure_arm(candidate, enabled: bool) -> None:
    """Normalize an already evaluated candidate and apply only the C7 score arm."""
    metrics = getattr(candidate, "metrics", None)
    if not isinstance(metrics, dict):
        metrics = {}
        candidate.metrics = metrics
    current_delta = _number(metrics.get("post_shift_overrun_score_delta"))
    base_score = _number(getattr(candidate, "score", 0.0)) - current_delta
    penalty = max(0.0, _number(metrics.get("post_shift_overrun_penalty")))
    arm_delta = -penalty if enabled else 0.0
    candidate.score = base_score + arm_delta
    metrics["post_shift_overrun_score_delta"] = arm_delta


def prepare(candidates: list) -> Prepared:
    """Capture full arm pools before the production selector can mutate them."""
    started = time.perf_counter_ns()
    off_candidates = copy.deepcopy(list(candidates))
    on_candidates = copy.deepcopy(list(candidates))
    for candidate in off_candidates:
        _configure_arm(candidate, False)
    for candidate in on_candidates:
        _configure_arm(candidate, True)
    elapsed_ms = (time.perf_counter_ns() - started) / 1_000_000.0
    return Prepared(
        off_candidates=off_candidates,
        on_candidates=on_candidates,
        full_pool_size=len(candidates),
        full_feasible_size=sum(
            getattr(c, "feasibility_verdict", None) == "MAYBE"
            for c in candidates
        ),
        prepare_ms=elapsed_ms,
    )


def _winner_cid(result) -> Optional[str]:
    best = getattr(result, "best", None)
    cid = getattr(best, "courier_id", None) if best is not None else None
    return str(cid) if cid is not None else None


def _parity_fields(actual, off) -> list[str]:
    fields = []
    if _winner_cid(actual) != _winner_cid(off):
        fields.append("winner_cid")
    if getattr(actual, "verdict", None) != getattr(off, "verdict", None):
        fields.append("verdict")
    if getattr(actual, "auto_route", "ACK") != getattr(off, "auto_route", "ACK"):
        fields.append("routing")
    return fields


def assert_off_parity(actual, off) -> None:
    fields = _parity_fields(actual, off)
    if fields:
        raise InstrumentMismatch(fields)


def _iso(value):
    try:
        return value.isoformat() if value is not None else None
    except Exception:
        return None


def _plan_value(mapping, order_id):
    if not isinstance(mapping, dict):
        return None
    if order_id in mapping:
        return mapping.get(order_id)
    return mapping.get(str(order_id))


def _arm_summary(result, order_id) -> dict:
    best = getattr(result, "best", None)
    metrics = (getattr(best, "metrics", None) or {}) if best is not None else {}
    plan = getattr(best, "plan", None) if best is not None else None
    predicted = _plan_value(
        getattr(plan, "predicted_delivered_at", None), order_id
    )
    margin = (getattr(result, "auto_route_context", None) or {}).get(
        "auto_route_score_margin"
    )
    return {
        "winner_cid": _winner_cid(result),
        "verdict": getattr(result, "verdict", None),
        "routing": getattr(result, "auto_route", "ACK"),
        "score": (
            round(_number(getattr(best, "score", 0.0)), 3)
            if best is not None else None
        ),
        "score_margin": round(_number(margin), 3) if margin is not None else None,
        "c7_overrun_min": metrics.get("post_shift_overrun_min"),
        "c7_penalty": metrics.get("post_shift_overrun_penalty"),
        "c7_score_delta": metrics.get("post_shift_overrun_score_delta"),
        "predicted_delivery_iso": _iso(predicted),
        "r35_max_bag_time_min": metrics.get("r6_max_bag_time_min"),
        "r35_breach_max_min": metrics.get("objm_r6_breach_max_min"),
        "committed_time_iso": metrics.get("czas_kuriera_warsaw"),
        "committed_breach_min": metrics.get("late_pickup_committed_max"),
        "new_pickup_late_min": metrics.get("new_pickup_late_min"),
        "load": metrics.get("loadgov_load_ewma"),
        "bag_size": metrics.get(
            "r6_bag_size", metrics.get("bag_size_before")
        ),
    }


def _margin_changed(off: dict, on: dict) -> bool:
    left, right = off.get("score_margin"), on.get("score_margin")
    if left is None or right is None:
        return left != right
    return abs(float(left) - float(right)) > 1e-6


def _last_changed_stage(off_trace, on_trace, *, off, on) -> Optional[str]:
    last = None
    previous_pair = (None, None)
    for stage in ("score", "OBJM", "E2"):
        pair = (off_trace.get(stage), on_trace.get(stage))
        if stage == "score":
            if pair[0] != pair[1]:
                last = stage
        elif (
            pair != previous_pair
            and (pair[0] != pair[1] or previous_pair[0] != previous_pair[1])
        ):
            last = stage
        previous_pair = pair
    winner_changed = off["winner_cid"] != on["winner_cid"]
    if _margin_changed(off, on) and not winner_changed and last is None:
        last = "score"
    final_pair = (off["winner_cid"], on["winner_cid"])
    if (
        off["verdict"] != on["verdict"]
        or off["routing"] != on["routing"]
        or (winner_changed and final_pair != previous_pair)
    ):
        last = "gate"
    return last


def _read_git_head(repo_root: Path) -> Optional[str]:
    """Read a worktree HEAD/ref without spawning git or reading git config."""
    dotgit = repo_root / ".git"
    gitdir = dotgit
    try:
        if dotgit.is_file():
            marker = dotgit.read_text(encoding="utf-8").strip()
            if not marker.startswith("gitdir:"):
                return None
            raw = marker.split(":", 1)[1].strip()
            gitdir = Path(raw)
            if not gitdir.is_absolute():
                gitdir = (repo_root / gitdir).resolve()
        head = (gitdir / "HEAD").read_text(encoding="utf-8").strip()
        if _SHA_RE.fullmatch(head):
            return head.lower()
        if not head.startswith("ref:"):
            return None
        ref = head.split(":", 1)[1].strip()
        common_dir = gitdir
        commondir_file = gitdir / "commondir"
        if commondir_file.is_file():
            common_raw = commondir_file.read_text(encoding="utf-8").strip()
            common_dir = (gitdir / common_raw).resolve()
        for candidate in (gitdir / ref, common_dir / ref):
            if candidate.is_file():
                value = candidate.read_text(encoding="utf-8").strip()
                if _SHA_RE.fullmatch(value):
                    return value.lower()
        packed = common_dir / "packed-refs"
        if packed.is_file():
            for line in packed.read_text(encoding="utf-8").splitlines():
                if line.startswith(("#", "^")):
                    continue
                parts = line.split(" ", 1)
                if len(parts) == 2 and parts[1] == ref and _SHA_RE.fullmatch(parts[0]):
                    return parts[0].lower()
    except (OSError, UnicodeError):
        return None
    return None


def _default_code_sha() -> str:
    global _CODE_SHA
    if _CODE_SHA is not None:
        return _CODE_SHA
    supplied = os.environ.get("ZIOMEK_CODE_SHA")
    if supplied:
        _CODE_SHA = supplied[:64]
        return _CODE_SHA
    direct = _read_git_head(Path(__file__).resolve().parent)
    if direct:
        _CODE_SHA = direct
        return _CODE_SHA
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parent,
            check=True,
            capture_output=True,
            text=True,
            timeout=0.1,
        )
        value = proc.stdout.strip()
        _CODE_SHA = value if value else "UNAVAILABLE"
    except Exception:
        _CODE_SHA = "UNAVAILABLE"
    return _CODE_SHA


def _select_arm(ctx, candidates, enabled, select_fn):
    arm_ctx = copy.copy(ctx)
    arm_ctx.shadow_only = True
    arm_ctx.selection_trace = {}
    with C.post_shift_overrun_override(enabled):
        result = select_fn(arm_ctx, candidates)
    return result, arm_ctx.selection_trace


def _route_arm(result, ctx, route_fn, context_fn, flags):
    context = context_fn(
        result=result,
        fleet_snapshot=getattr(ctx, "fleet_snapshot", None),
        flags=flags,
        order_event=getattr(ctx, "order_event", None),
        now=getattr(ctx, "now", None),
    )
    result.auto_route_context = context
    route, reason = route_fn(
        result=result,
        fleet_snapshot=getattr(ctx, "fleet_snapshot", None),
        flags=flags,
        order_event=getattr(ctx, "order_event", None),
        now=getattr(ctx, "now", None),
        emit_calibration_shadow=False,
    )
    result.auto_route = route
    result.auto_route_reason = reason


def measure_prepared(
    ctx,
    prepared: Prepared,
    actual_result,
    *,
    select_fn: Optional[Callable] = None,
    route_fn: Optional[Callable] = None,
    context_fn: Optional[Callable] = None,
    code_sha_fn: Callable[[], str] = _default_code_sha,
    fingerprint_fn: Callable[[], str] = C.flag_fingerprint,
) -> dict:
    """Run exactly two canonical arms and return the PII-free v1 payload."""
    if select_fn is None:
        from dispatch_v2.core.selection import select_and_emit
        select_fn = select_and_emit
    if route_fn is None or context_fn is None:
        from dispatch_v2.auto_proximity_classifier import (
            build_context_for_logging,
            classify_auto_route,
        )
        route_fn = route_fn or classify_auto_route
        context_fn = context_fn or build_context_for_logging

    started = time.perf_counter_ns()
    flags = C.load_flags()
    actual_c7_enabled = C.decision_flag(_C7_FLAG)
    off_result, off_trace = _select_arm(
        ctx, prepared.off_candidates, False, select_fn
    )
    on_result, on_trace = _select_arm(
        ctx, prepared.on_candidates, True, select_fn
    )
    _route_arm(off_result, ctx, route_fn, context_fn, flags)
    _route_arm(on_result, ctx, route_fn, context_fn, flags)

    off = _arm_summary(off_result, getattr(ctx, "order_id", None))
    on = _arm_summary(on_result, getattr(ctx, "order_id", None))
    fingerprint = fingerprint_fn()
    fingerprint_hash = hashlib.sha256(
        fingerprint.encode("utf-8", errors="replace")
    ).hexdigest()
    measurement_ms = (time.perf_counter_ns() - started) / 1_000_000.0
    winner_changed = off["winner_cid"] != on["winner_cid"]
    margin_changed = _margin_changed(off, on)
    routing_changed = off["routing"] != on["routing"]
    verdict_changed = off["verdict"] != on["verdict"]
    mismatch_fields = _parity_fields(actual_result, off_result)
    # Kontrakt pomiaru flipowego: realny proces ma być C7 OFF. Nawet jeśli ON
    # przypadkiem wybierze ten sam wynik, nie wolno uznać OFF parity za dowiedzione.
    if actual_c7_enabled:
        mismatch_fields.append("actual_c7_enabled")
    payload = {
        "schema": SCHEMA,
        "status": "INSTRUMENT_MISMATCH" if mismatch_fields else "OK",
        "actual_c7_enabled": actual_c7_enabled,
        "full_pool_size": prepared.full_pool_size,
        "full_feasible_size": prepared.full_feasible_size,
        "last_changed_stage": _last_changed_stage(
            off_trace, on_trace, off=off, on=on
        ),
        "winner_changed": winner_changed,
        "margin_changed": margin_changed,
        "routing_changed": routing_changed,
        "verdict_changed": verdict_changed,
        "code_sha": code_sha_fn(),
        "flag_fingerprint": fingerprint,
        "flag_fingerprint_sha256": f"sha256:{fingerprint_hash}",
        "prepare_ms": round(prepared.prepare_ms, 3),
        "measurement_ms": round(measurement_ms, 3),
        "overhead_ms": round(prepared.prepare_ms + measurement_ms, 3),
        "off": off,
        "on": on,
    }
    if mismatch_fields:
        payload["mismatch_fields"] = sorted(mismatch_fields)
    return payload


def attach_fail_safe(
    ctx,
    prepared: Prepared,
    actual_result,
    *,
    select_fn: Optional[Callable] = None,
) -> Any:
    """Attach telemetry and always return the exact production result object."""
    try:
        actual_result.c7_normal_path = measure_prepared(
            ctx, prepared, actual_result, select_fn=select_fn
        )
        if actual_result.c7_normal_path["status"] == "INSTRUMENT_MISMATCH":
            _log.error(
                "INSTRUMENT_MISMATCH c7_normal_path order=%s fields=%s",
                getattr(ctx, "order_id", "?"),
                actual_result.c7_normal_path.get("mismatch_fields"),
            )
    except Exception as exc:  # fail-safe is the central contract of the instrument
        actual_result.c7_normal_path = {
            "schema": SCHEMA,
            "status": "INSTRUMENT_ERROR",
            "error_type": type(exc).__name__,
        }
        _log.warning(
            "c7_normal_path fail-safe order=%s error_type=%s",
            getattr(ctx, "order_id", "?"),
            type(exc).__name__,
        )
    return actual_result
