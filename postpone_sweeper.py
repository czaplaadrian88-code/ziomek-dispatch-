"""postpone_sweeper — Tech-debt #20 POSTPONE auto-replan sweeper.

Checks postponed proposals every minute (called by cron or asyncio loop).
Re-emits proposals when postpone window expires (if order still unassigned),
escalates to KOORD after MAX_POSTPONE_COUNT postpones.

Storage: /root/.openclaw/workspace/dispatch_state/postponed_proposals.json
"""
import fcntl
import json
import logging
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from zoneinfo import ZoneInfo

from dispatch_v2 import state_machine
from dispatch_v2 import dispatch_pipeline
from dispatch_v2 import courier_resolver
from dispatch_v2 import telegram_utils
from dispatch_v2 import pending_proposals_store

log = logging.getLogger(__name__)

POSTPONED_PATH = "/root/.openclaw/workspace/dispatch_state/postponed_proposals.json"
PENDING_PROPOSALS_PATH = "/root/.openclaw/workspace/dispatch_state/pending_proposals.json"
MAX_POSTPONE_COUNT = 2
WARSAW = ZoneInfo("Europe/Warsaw")


def _atomic_write_json(path: str, data: Any) -> None:
    """Write JSON atomically: temp file in same dir → fsync → os.replace."""
    parent = Path(path).parent
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        mode="w",
        prefix=Path(path).name + ".",
        suffix=".tmp",
        dir=str(parent),
        delete=False,
    ) as tmp:
        tmp_name = tmp.name
        json.dump(data, tmp, ensure_ascii=False, indent=2)
        tmp.flush()
        os.fsync(tmp.fileno())
    os.replace(tmp_name, path)


def _load_json_safe(path: str, default: Any = None) -> Any:
    """Load JSON with shared lock. Returns default on missing/corrupt."""
    if default is None:
        default = {}
    p = Path(path)
    if not p.exists():
        return default
    try:
        with open(path, "r") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_SH)
            try:
                return json.load(f)
            finally:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except (json.JSONDecodeError, OSError) as e:
        log.warning(f"_load_json_safe fail path={path}: {e}")
        return default


def run_once(now: Optional[datetime] = None) -> Dict[str, int]:
    """Check all postponed entries, process expired ones.

    Returns stats dict with keys: checked, resolved, escalated, reemitted,
    skipped, errors.
    """
    if now is None:
        now = datetime.now(timezone.utc)

    postponed = _load_json_safe(POSTPONED_PATH, {})
    stats: Dict[str, int] = {
        "checked": 0,
        "resolved": 0,
        "escalated": 0,
        "reemitted": 0,
        "skipped": 0,
        "errors": 0,
    }

    expired_oids: list = []
    for oid, entry in list(postponed.items()):
        stats["checked"] += 1
        try:
            postponed_until = datetime.fromisoformat(entry["postponed_until"])
            if postponed_until.tzinfo is None:
                postponed_until = postponed_until.replace(tzinfo=timezone.utc)
            if now < postponed_until:
                stats["skipped"] += 1
                continue

            expired_oids.append(oid)

            # Check if order has been manually assigned
            # K02 refaktor (2026-07-06, deep-audit #1.8): _read_state() zwraca
            # PŁASKI {oid: rec}, a pole kuriera to "courier_id" — poprzedni
            # odczyt .get("orders",{}).get(oid) + .get("cid") nigdy nie trafiał
            # → POSTPONE_RESOLVED nieosiągalny → duplikat propozycji po re-enable.
            orders_state = state_machine._read_state()
            current = orders_state.get(oid) or orders_state.get(str(oid)) or {}
            current_cid = current.get("courier_id")
            if current_cid not in (None, "", "26", 26, "None"):
                log.info(f"POSTPONE_RESOLVED oid={oid} cid={current_cid}")
                stats["resolved"] += 1
                postponed.pop(oid, None)
                continue

            # Escalation threshold
            if entry.get("postpone_count", 0) >= MAX_POSTPONE_COUNT:
                telegram_utils.send_admin_alert(
                    f"⚠ POSTPONE_ESCALATED #{oid} po {entry['postpone_count']} postpone — eskalacja KOORD"
                )
                log.warning(f"POSTPONE_ESCALATED oid={oid} count={entry['postpone_count']}")
                stats["escalated"] += 1
                postponed.pop(oid, None)
                continue

            # Re-emit attempt
            order_event = (
                current.get("raw")
                or entry.get("decision_record", {}).get("order_event")
                or {}
            )
            if not order_event:
                if not current:
                    # Order zniknął ze stanu (terminalny/usunięty) i brak payloadu
                    # w decision_record → nic do re-emitu, retry nigdy nie pomoże.
                    # Drop zamiast pętlić w nieskończoność (zombie postponed entry).
                    log.warning(
                        f"POSTPONE_DROP_STALE oid={oid} — order nieobecny w stanie, brak order_event → drop"
                    )
                    stats["skipped"] += 1
                    postponed.pop(oid, None)
                    continue
                log.warning(
                    f"POSTPONE_REEMIT_NO_ORDER oid={oid} — skip, will retry next tick"
                )
                stats["errors"] += 1
                continue

            fleet_snapshot = courier_resolver.dispatchable_fleet(now)
            result = dispatch_pipeline.assess_order(
                order_event, fleet_snapshot, now=now
            )
            verdict = getattr(result, "verdict", None)
            if verdict in ("ASSIGN", "PROPOSE"):
                _entry = {
                    "decision_record": (
                        result.__dict__ if hasattr(result, "__dict__") else dict(result)
                    ),
                    "reemitted_from_postpone": True,
                    "ts": now.isoformat(),
                }
                # L7.5 (audyt O1): RMW pending POD LOCK_EX (kanon) — load→setitem→save
                # w jednym locku serializuje z pisarzem shadow (upsert_proposals) i
                # zachowuje wpisy dołożone współbieżnie (blind write nadpisywał je).
                pending_proposals_store.locked_mutate(
                    lambda p, _o=oid, _e=_entry: p.__setitem__(_o, _e),
                    PENDING_PROPOSALS_PATH,
                )
                log.info(f"POSTPONE_REEMITTED oid={oid} verdict={verdict}")
                stats["reemitted"] += 1
                postponed.pop(oid, None)
            else:
                log.info(
                    f"POSTPONE_REEMIT_VERDICT_{verdict} oid={oid} — keep postponed for next attempt"
                )
                stats["errors"] += 1
        except Exception as e:
            log.exception(f"POSTPONE_SWEEPER_ERROR oid={oid}: {e}")
            stats["errors"] += 1

    # Write back updated postponed dict
    _atomic_write_json(POSTPONED_PATH, postponed)
    return stats


if __name__ == "__main__":
    import sys
    stats = run_once()
    print(json.dumps(stats))
    sys.exit(0)
