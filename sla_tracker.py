"""SLA Tracker - konsumer COURIER_PICKED_UP + COURIER_DELIVERED.
Liczy delivery_time_minutes, loguje do sla_log.jsonl."""
import json
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dispatch_v2.common import now_iso, setup_logger
from dispatch_v2.event_bus import get_pending, mark_processed
from dispatch_v2.state_machine import get_order, upsert_order

_log = setup_logger("sla_tracker", "/root/.openclaw/workspace/scripts/logs/sla_tracker.log")
_running = True
_stats = {"pickup": 0, "delivered": 0, "violations": 0}
LOG_PATH = Path("/root/.openclaw/workspace/scripts/logs/sla_log.jsonl")


def _handler(signum, frame):
    global _running
    _log.info(f"Signal {signum}")
    _running = False


def _parse(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        try:
            return datetime.strptime(s, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        except Exception:
            return None


def process(evt):
    etype = evt["event_type"]
    oid = evt.get("order_id")
    payload = evt.get("payload", {})

    if etype == "COURIER_PICKED_UP":
        ts = payload.get("timestamp", now_iso())
        upsert_order(oid, {"picked_up_at": ts}, event="SLA_PICKUP")
        _stats["pickup"] += 1
        _log.info(f"pickup {oid} at {ts}")
        return True

    if etype == "COURIER_DELIVERED":
        order = get_order(oid) or {}
        delivered_ts = payload.get("timestamp", now_iso())
        picked_ts = order.get("picked_up_at")

        dmin = None
        sla_ok = None
        if picked_ts:
            p, d = _parse(picked_ts), _parse(delivered_ts)
            if p and d:
                dmin = round((d - p).total_seconds() / 60, 1)
                sla_ok = dmin <= 35

        rec = {
            "order_id": oid,
            "courier_id": evt.get("courier_id") or order.get("courier_id"),
            "restaurant": order.get("restaurant"),
            "delivery_address": order.get("delivery_address"),
            "picked_up_at": picked_ts,
            "delivered_at": delivered_ts,
            "delivery_time_minutes": dmin,
            "sla_ok": sla_ok,
            "was_czasowka": order.get("order_type") == "czasowka",
            "logged_at": now_iso(),
        }
        LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_PATH, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")

        _stats["delivered"] += 1
        if sla_ok is False:
            _stats["violations"] += 1
            _log.warning(f"SLA VIOLATION {oid}: {dmin}min courier={rec['courier_id']}")
        else:
            _log.info(f"SLA OK {oid}: {dmin}min")
        return True

    return False


def run():
    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)
    _log.info("SLA tracker START")
    last_summary = time.time()

    SLA_EVENT_TYPES = ["COURIER_PICKED_UP", "COURIER_DELIVERED"]
    while _running:
        try:
            for evt in get_pending(limit=200, event_types=SLA_EVENT_TYPES):
                if process(evt):
                    mark_processed(evt["event_id"])
        except Exception as e:
            _log.error(f"loop: {e}")

        if time.time() - last_summary > 300:
            _log.info(f"SUMMARY: {_stats}")
            last_summary = time.time()
        time.sleep(10)

    _log.info("SLA tracker STOP")


if __name__ == "__main__":
    run()
