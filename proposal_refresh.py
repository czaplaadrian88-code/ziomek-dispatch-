"""R2 shadow-only proposal refresh on dispatchable-fleet membership changes."""
from __future__ import annotations

import fcntl
import hashlib
import json
import logging
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

from dispatch_v2 import common as C
from dispatch_v2.c7_normal_path import _default_code_sha
from dispatch_v2.core.jsonl_appender import append_jsonl_once

_log = logging.getLogger("proposal_refresh")

FLAG = "ENABLE_PROPOSAL_REFRESH"
SCHEMA = "proposal_refresh.v1"
MIN_INTERVAL_SECONDS = 120.0
REFRESH_STATE_PATH = C.STATE_DIR / "proposal_refresh_state.json"
SHADOW_DECISIONS_PATH = C.LOGS_DIR / "shadow_decisions.jsonl"


def _fleet_membership(fleet: Dict[str, Any]) -> tuple[list[str], str]:
    cids = sorted(str(cid) for cid in fleet)
    packed = json.dumps(cids, separators=(",", ":"), ensure_ascii=True).encode()
    return cids, "sha256:" + hashlib.sha256(packed).hexdigest()


def _winner(result: Any) -> Optional[str]:
    best = getattr(result, "best", None)
    cid = getattr(best, "courier_id", None) if best is not None else None
    return str(cid) if cid not in (None, "") else None


def _margin(result: Any) -> Optional[float]:
    winner = _winner(result)
    best = getattr(result, "best", None)
    best_score = getattr(best, "score", None) if best is not None else None
    for candidate in getattr(result, "candidates", None) or []:
        cid = getattr(candidate, "courier_id", None)
        score = getattr(candidate, "score", None)
        if cid is None or str(cid) == str(winner) or score is None:
            continue
        if best_score is None:
            return None
        return round(float(best_score) - float(score), 3)
    return None


def _parse_time(value: Any) -> Optional[datetime]:
    try:
        parsed = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _event_id(
    order_id: str, old_generation: str, new_generation: str, winner_cid: str
) -> str:
    raw = "\x1f".join(
        (str(order_id), old_generation, new_generation, str(winner_cid))
    ).encode()
    return "proposal-refresh-" + hashlib.sha256(raw).hexdigest()


def _build_refresh_record(
    order_id: str,
    result: Any,
    previous_winner_cid: Optional[str],
    old_cids: list[str],
    old_generation: str,
    new_cids: list[str],
    new_generation: str,
    now: datetime,
) -> dict:
    winner_cid = _winner(result)
    return {
        "ts": now.isoformat(),
        "event_id": _event_id(
            order_id, old_generation, new_generation, str(winner_cid)
        ),
        "order_id": str(order_id),
        "record_type": "proposal_refresh",
        "verdict": "SHADOW_ONLY",
        "proposal_refresh": {
            "schema": SCHEMA,
            "shadow_only": True,
            "reason": "available_fleet_changed",
            "previous_winner_cid": previous_winner_cid,
            "winner_cid": winner_cid,
            "engine_verdict": str(getattr(result, "verdict", "UNKNOWN")),
            "engine_routing": str(getattr(result, "auto_route", "ACK")),
            "score_margin": _margin(result),
            "fleet_generation_before": old_generation,
            "fleet_generation_after": new_generation,
            "available_cids_before": old_cids,
            "available_cids_after": new_cids,
            "min_interval_seconds": MIN_INTERVAL_SECONDS,
        },
        "code_sha": _default_code_sha(),
        "flag_fingerprint": C.flag_fingerprint(),
    }


def _load_state(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8") as source:
            value = json.load(source)
    except FileNotFoundError:
        return {}
    except (OSError, ValueError, TypeError) as exc:
        _log.warning("R2 refresh state unreadable; rebuilding: %s", exc)
        return {}
    return value if isinstance(value, dict) else {}


def _atomic_write(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=".proposal-refresh-", suffix=".tmp", dir=str(path.parent)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as target:
            json.dump(
                value,
                target,
                ensure_ascii=False,
                separators=(",", ":"),
                sort_keys=True,
            )
            target.flush()
            os.fsync(target.fileno())
        os.replace(tmp_path, path)
        dir_fd = os.open(
            str(path.parent), os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
        )
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)


def record_refreshes(
    results: Dict[str, Any],
    proposed: Dict[str, dict],
    fleet: Dict[str, Any],
    now: Optional[datetime] = None,
) -> int:
    """Append winner-changing refreshes, never a console/Telegram proposal."""
    now = now or datetime.now(timezone.utc)
    state_path = Path(REFRESH_STATE_PATH)
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_path.with_name(state_path.name + ".lock")
    lock_fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(lock_fd, fcntl.LOCK_EX)
        state = _load_state(state_path)
        orders = state.get("orders") if isinstance(state.get("orders"), dict) else {}
        old_cids = [
            str(cid) for cid in (state.get("available_cids") or [])
        ]
        old_generation = str(state.get("fleet_generation") or "")
        new_cids, new_generation = _fleet_membership(fleet)

        active = {str(oid) for oid in results}
        orders = {
            str(oid): value
            for oid, value in orders.items()
            if str(oid) in active and isinstance(value, dict)
        }
        for oid, result in results.items():
            oid = str(oid)
            if oid not in orders:
                proposed_cid = (proposed.get(oid) or {}).get("cid")
                orders[oid] = {
                    "winner_cid": (
                        str(proposed_cid)
                        if proposed_cid not in (None, "")
                        else _winner(result)
                    ),
                    "last_logged_at": None,
                }

        appended = 0
        fleet_changed = bool(old_generation and old_generation != new_generation)
        if fleet_changed:
            for oid, result in results.items():
                oid = str(oid)
                item = orders[oid]
                previous = item.get("winner_cid")
                winner = _winner(result)
                if winner is None or str(winner) == str(previous):
                    item["winner_cid"] = winner
                    continue
                last_logged = _parse_time(item.get("last_logged_at"))
                if (
                    last_logged is not None
                    and (now - last_logged).total_seconds() < MIN_INTERVAL_SECONDS
                ):
                    continue
                record = _build_refresh_record(
                    oid,
                    result,
                    str(previous) if previous not in (None, "") else None,
                    old_cids,
                    old_generation,
                    new_cids,
                    new_generation,
                    now,
                )
                was_appended = append_jsonl_once(
                    Path(SHADOW_DECISIONS_PATH),
                    record,
                    dedupe_key="event_id",
                    dedupe_value=record["event_id"],
                    scan_rotated=True,
                )
                item["winner_cid"] = winner
                item["last_logged_at"] = now.isoformat()
                appended += int(was_appended)

        _atomic_write(
            state_path,
            {
                "schema": SCHEMA,
                "updated_at": now.isoformat(),
                "fleet_generation": new_generation,
                "available_cids": new_cids,
                "orders": orders,
            },
        )
        return appended
    finally:
        fcntl.flock(lock_fd, fcntl.LOCK_UN)
        os.close(lock_fd)

