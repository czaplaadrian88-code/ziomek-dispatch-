"""A4.1 (2026-05-09) — broadcast event handlers dla per-process workers.

A4 (2026-05-08) dostarczył infrastructure: event_bus.emit_config_reload() +
BroadcastSubscriber.poll() + cleanup_broadcast(). A4.1 wire'uje subscribers
w 4 workers (shadow_dispatcher, panel_watcher, telegram_approver, sla_tracker).

Design:
- Lekkie wire — handler default = log INFO (proof-of-wire, confirms end-to-end).
- Per-scope extension point — dodaj cache invalidation per use-case gdy
  zaistnieje (np. tier cache invalidation, kurier_ids reload).
- Defense-in-depth — handler exception NIE propaguje do worker tick.

Use cases (current + future):
- scope="flags" — LIVE od flags_admin (MP-#6). flag() reader ma już mtime
  hot-reload (common.py), więc broadcast = belt-and-suspenders dla rzadkich
  edge cases (mtime stat fail, cross-host sync gdy multi-tenant).
- scope="kurier_ids" — przyszły: gdy Adrian onboard'uje kuriera, kurier_ids.json
  modyfikacja → broadcast → workers reload reverse map cache.
- scope="restaurant_coords" — przyszły: panel_watcher ma już mtime hot-reload
  (MP-#12), broadcast = backup signal.
- scope="courier_tiers" — przyszły: post r04_evaluator overnight refresh.
"""
from __future__ import annotations

from typing import List

from dispatch_v2.common import setup_logger

_log = setup_logger("broadcast_handlers", "/root/.openclaw/workspace/scripts/logs/dispatch.log")


def dispatch_config_reload(events: List[dict], consumer_id: str) -> int:
    """Process CONFIG_RELOAD events dla danego consumer.

    Switch po scope: known scopes get info-log per event (extension point dla
    cache invalidation), unknown scopes get warning. Defense-in-depth try/except
    per event — single corrupt event NIE blocks pozostałych.

    Returns: liczba processed events (incl. unknown scope counted).
    """
    if not events:
        return 0

    processed = 0
    for evt in events:
        try:
            scope = (evt.get("payload") or {}).get("scope") or "<missing>"
            event_id = evt.get("event_id", "<no-id>")
            payload_meta = {
                k: v for k, v in (evt.get("payload") or {}).items()
                if k != "scope"
            }
            if scope == "flags":
                _log.info(
                    f"[{consumer_id}] CONFIG_RELOAD scope=flags id={event_id} "
                    f"meta={payload_meta} — flag() reader has mtime hot-reload, "
                    f"broadcast = belt-and-suspenders"
                )
            elif scope == "kurier_ids":
                _log.info(
                    f"[{consumer_id}] CONFIG_RELOAD scope=kurier_ids id={event_id} "
                    f"meta={payload_meta} — extension point: reload reverse map cache "
                    f"gdy worker ma per-process kurier_ids cache"
                )
            elif scope == "restaurant_coords":
                _log.info(
                    f"[{consumer_id}] CONFIG_RELOAD scope=restaurant_coords id={event_id} "
                    f"meta={payload_meta} — panel_watcher MP-#12 mtime hot-reload primary"
                )
            elif scope == "courier_tiers":
                _log.info(
                    f"[{consumer_id}] CONFIG_RELOAD scope=courier_tiers id={event_id} "
                    f"meta={payload_meta} — extension point: invalidate tier cache w scoring"
                )
            else:
                _log.warning(
                    f"[{consumer_id}] CONFIG_RELOAD unknown scope={scope!r} id={event_id} "
                    f"meta={payload_meta} — handler not implemented, log-only"
                )
            processed += 1
        except Exception as _e:
            _log.error(
                f"[{consumer_id}] CONFIG_RELOAD handler FAIL "
                f"({type(_e).__name__}: {_e}) — skip event, continue"
            )
    return processed
