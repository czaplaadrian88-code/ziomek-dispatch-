"""A4 (audit META RC2 2026-05-07) — BroadcastSubscriber dla CONFIG_RELOAD events.

Reusable per-process subscriber dla long-running services (shadow_dispatcher,
panel_watcher, telegram_approver, sla_tracker). Każdy subscriber persistuje
swój cursor (last_seen_event_id_per_type) atomic w state file.

Usage example:
    sub = BroadcastSubscriber(
        consumer_id="shadow_dispatcher",
        state_path=Path("/root/.openclaw/workspace/dispatch_state/event_subscribers/shadow.json"),
    )
    # In tick loop:
    new_events = sub.poll(["CONFIG_RELOAD"])
    for evt in new_events:
        scope = evt["payload"].get("scope")
        if scope == "courier_tiers":
            invalidate_tier_cache()
        elif scope == "flags":
            # flags.json już ma mtime hot-reload; broadcast = belt-and-suspenders
            pass
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import List

from dispatch_v2 import event_bus
from dispatch_v2.common import setup_logger

_log = setup_logger("config_reload_sub", "/root/.openclaw/workspace/scripts/logs/dispatch.log")


class BroadcastSubscriber:
    """Per-process broadcast event subscriber z persistent cursor.

    State file format:
        {"cursor_per_type": {"CONFIG_RELOAD": "<last_seen_event_id>"}}

    State persistowany atomic (tempfile + os.replace). Corrupt/missing →
    fresh start (cursor=None → poll returns wszystko od początku, cap'd by limit).
    """

    def __init__(self, consumer_id: str, state_path: Path):
        self.consumer_id = consumer_id
        self.state_path = Path(state_path)

    def _load_state(self) -> dict:
        try:
            return json.loads(self.state_path.read_text())
        except (FileNotFoundError, json.JSONDecodeError) as _e:
            if isinstance(_e, json.JSONDecodeError):
                _log.warning(f"subscriber={self.consumer_id} corrupt state, fresh start: {_e}")
            return {"cursor_per_type": {}}
        except Exception as _e:
            _log.error(f"subscriber={self.consumer_id} state load fail ({type(_e).__name__}: {_e}) — fresh start")
            return {"cursor_per_type": {}}

    def _save_state(self, state: dict) -> None:
        """Atomic write: tempfile + fsync + os.replace (Lekcja #14 pattern)."""
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(self.state_path.parent),
            prefix=f".{self.state_path.name}.tmp-",
            suffix=".json",
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.state_path)
        except Exception:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise

    def poll(self, event_types: List[str], limit: int = 100) -> List[dict]:
        """Returns nowych eventów od ostatniego poll. Updates cursor atomic.

        Defensywnie: poll_broadcast() raises na bad event_types — propaguje
        (caller dostaje sygnał o błędnym wire'owaniu). DB locked → propaguje
        (subscriber retry next tick).
        """
        state = self._load_state()
        cursor_per_type = state.setdefault("cursor_per_type", {})

        # Use min cursor across all event_types as conservative since (will fetch
        # any event newer than oldest cursor; per-type filter applied client-side
        # below). Simple semantics gdy 1 type w event_types — typical case.
        if len(event_types) == 1:
            since = cursor_per_type.get(event_types[0])
            new = event_bus.poll_broadcast(event_types, since_event_id=since, limit=limit)
        else:
            since_min = min(
                (cursor_per_type.get(t) or "") for t in event_types
            ) or None
            all_new = event_bus.poll_broadcast(event_types, since_event_id=since_min, limit=limit)
            # Per-type cursor filter
            new = [
                e for e in all_new
                if not cursor_per_type.get(e["event_type"])
                or e["event_id"] > cursor_per_type[e["event_type"]]
            ]

        if not new:
            return []

        # Advance cursor per-type (max event_id per type seen w tym poll)
        for evt in new:
            t = evt["event_type"]
            cur = cursor_per_type.get(t)
            if cur is None or evt["event_id"] > cur:
                cursor_per_type[t] = evt["event_id"]

        try:
            self._save_state(state)
        except Exception as _e:
            _log.error(
                f"subscriber={self.consumer_id} state save FAIL "
                f"({type(_e).__name__}: {_e}) — events delivered ALE cursor lost, "
                f"next poll redelivers (consumer MUSI być idempotent)"
            )
        return new
