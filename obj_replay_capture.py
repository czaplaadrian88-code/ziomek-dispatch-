"""obj_replay_capture — zapis wejść solvera do offline replay (sprint OBJ F0.3).

Flag-gated (common.ENABLE_OBJ_REPLAY_CAPTURE, default OFF). Serializuje DOKŁADNE
wejścia simulate_bag_route_v2 do jsonl — obj_harness ładuje to jako zestaw masowy
(regresja/breadth) z 100% wiernością, bez kruchej rekonstrukcji z logów.

Fail-safe: capture NIGDY nie może przerwać dispatchu — całość w try/except,
caller dostaje ciche None. Append pod lockiem (route_simulator wywoływany z
ThreadPoolExecutor — wiele wątków).
"""
import json
import logging
import threading
from datetime import datetime, timezone

log = logging.getLogger(__name__)

CAPTURE_PATH = "/root/.openclaw/workspace/dispatch_state/obj_replay_capture.jsonl"
_lock = threading.Lock()


def _iso(dt):
    """datetime → ISO UTC; None gdy brak / nie-datetime."""
    if dt is None:
        return None
    try:
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc).isoformat()
    except Exception:
        return None


def _ser_order(o):
    """OrderSim → dict (komplet pól potrzebnych do wiernego replay)."""
    return {
        "order_id": getattr(o, "order_id", None),
        "pickup_coords": list(getattr(o, "pickup_coords", None) or []),
        "delivery_coords": list(getattr(o, "delivery_coords", None) or []),
        "picked_up_at": _iso(getattr(o, "picked_up_at", None)),
        "status": getattr(o, "status", None),
        "pickup_ready_at": _iso(getattr(o, "pickup_ready_at", None)),
        "czas_kuriera_warsaw": getattr(o, "czas_kuriera_warsaw", None),
    }


def capture(courier_pos, bag, new_order, now, dwell_pickup, dwell_dropoff,
            tier, order_id, path=CAPTURE_PATH):
    """Zapisz jeden rekord wejść solvera (jsonl append). Fail-safe.

    No-op gdy ENABLE_OBJ_REPLAY_CAPTURE wyłączone. Wyjątki tłumione (warning) —
    instrumentacja nie może wpłynąć na dispatch.
    """
    try:
        from dispatch_v2 import common as C
        if not getattr(C, "ENABLE_OBJ_REPLAY_CAPTURE", False):
            return
        rec = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "order_id": order_id,
            "tier": tier,
            "now": _iso(now),
            "courier_pos": list(courier_pos or []),
            "dwell_pickup": dwell_pickup,
            "dwell_dropoff": dwell_dropoff,
            "bag": [_ser_order(o) for o in (bag or [])],
            "new_order": _ser_order(new_order),
        }
        line = json.dumps(rec, ensure_ascii=False)
        with _lock:
            with open(path, "a") as f:
                f.write(line + "\n")
    except Exception as e:
        log.warning(f"OBJ_REPLAY_CAPTURE_FAIL {type(e).__name__}: {e}")
