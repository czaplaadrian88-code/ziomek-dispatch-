"""SLA Tracker - konsumer COURIER_PICKED_UP + COURIER_DELIVERED.
Liczy delivery_time_minutes, loguje do sla_log.jsonl.

F2.1b step 6: R6 BAG_TIME pre-warning — scan picked_up orderów co 10s,
alert Telegram gdy bag_time > 30 min, one-shot per order."""
import json
import signal
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict

from dispatch_v2 import common as C
from dispatch_v2.common import now_iso, setup_logger
from dispatch_v2.event_bus import get_pending, mark_processed
from dispatch_v2.state_machine import get_order, upsert_order, get_by_status
from dispatch_v2.telegram_utils import send_admin_alert

_log = setup_logger("sla_tracker", "/root/.openclaw/workspace/scripts/logs/sla_tracker.log")
_running = True
_stats = {"pickup": 0, "delivered": 0, "violations": 0, "r6_alerts": 0}
LOG_PATH = Path("/root/.openclaw/workspace/scripts/logs/sla_log.jsonl")
COURIER_NAMES_PATH = Path("/root/.openclaw/workspace/dispatch_state/courier_names.json")
_courier_names: Dict[str, str] = {}


def _load_courier_names() -> Dict[str, str]:
    try:
        return json.loads(COURIER_NAMES_PATH.read_text())
    except Exception as e:
        _log.warning(f"courier_names load fail: {e}")
        return {}


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


def _parse_aware_utc(s):
    """Parser zwracający ZAWSZE aware datetime w UTC (lub None).

    Różnica vs _parse():
      _parse()         — legacy parser używany przez SLA check ścieżkę
                         (process(COURIER_DELIVERED)). Dla naive stringów
                         `fromisoformat` sukces zwraca NAIVE datetime (bez tzinfo).
                         SLA check działa z tym "niechcący poprawnie" bo robi
                         `(d - p)` gdzie OBIE daty są naive — timedelta jest
                         numerycznie correct, ale oba są w tej samej (nieznanej)
                         tzinfo=None strefie.
      _parse_aware_utc() — NOWY parser dla R6 krok #6.1. Naive string traktuje
                         jako Warsaw-local (bo panel Rutcom emituje naive Warsaw)
                         i konwertuje do UTC. Zawsze zwraca aware UTC datetime.

    Dlaczego dwa parsery:
      R6 `_check_bag_time_alerts` porównuje `now_utc` (aware UTC z datetime.now)
      z `picked_up_at` z state. Mieszanka aware vs naive → TypeError. SLA check
      ma **obie** naive więc nie crashuje. Nie możemy zastąpić _parse() przez
      _parse_aware_utc() bez pełnej weryfikacji SLA path — istniejące
      delivery_time_minutes liczone z (d - p) gdzie oba są naive Warsaw dają
      POPRAWNY wynik numerycznie (różnica nie zależy od tzinfo gdy oba są w
      tej samej strefie). Zmiana _parse → aware Warsaw → aware UTC zmienia
      typ, co wymaga retestu SLA logowania + downstream consumerów sla_log.jsonl.

    Pre-existing bug:
      _parse() fallback `strptime.replace(tzinfo=timezone.utc)` ZAKŁADA że naive
      jest UTC — to jest błędne, panel wysyła naive Warsaw. W praktyce ścieżka
      strptime nie jest dotykana (fromisoformat sukces dla naive), więc bug
      jest uśpiony. Udokumentowany w docs/TECH_DEBT.md sekcja "F2.1b step 6 —
      sla_tracker._parse() naive→UTC timestamp bug". Fix → F2.1c lub krok #8
      z pełnym retestem SLA delivery_time_minutes.

    Returns:
        datetime with tzinfo=timezone.utc, albo None dla corrupt/empty input.
    """
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except Exception:
        try:
            dt = datetime.strptime(s, "%Y-%m-%d %H:%M:%S")
        except Exception:
            return None
    if dt.tzinfo is None:
        from zoneinfo import ZoneInfo
        dt = dt.replace(tzinfo=ZoneInfo("Europe/Warsaw"))
    return dt.astimezone(timezone.utc)


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


def _format_picked_up_hhmm(picked_dt: datetime) -> str:
    """ISO dt → 'HH:MM' Warsaw local for R6 alert message."""
    try:
        from zoneinfo import ZoneInfo
        if picked_dt.tzinfo is None:
            picked_dt = picked_dt.replace(tzinfo=timezone.utc)
        return picked_dt.astimezone(ZoneInfo("Europe/Warsaw")).strftime("%H:%M")
    except Exception:
        return "??:??"


def _check_bag_time_alerts(now_utc: datetime) -> None:
    """F2.1b step 6: R6 BAG_TIME pre-warning scan.

    Iteruje picked_up ordery, liczy bag_time_min = now - picked_up_at.
    Dla orderów z bag_time > C.BAG_TIME_PRE_WARNING_MIN AND bag_time_alerted=False:
      1. Upsert bag_time_alerted=True (PRZED send — one-shot guarantee)
      2. Wysyła Telegram alert do admina
      3. Loguje warning z detail, error przy send fail (alert lost)

    Per-order try/except — jeden bad order nie ubija całego skanu.
    Set-then-send (Opcja X): duplicate-safe, Telegram fail logowany bez retry.
    """
    try:
        picked_up_orders = get_by_status("picked_up")
    except Exception as e:
        _log.error(f"R6 scan: get_by_status fail: {e}")
        return

    for order in picked_up_orders:
        oid = order.get("order_id") or "unknown"
        try:
            if order.get("bag_time_alerted", False):
                continue  # one-shot gate — already alerted

            picked_ts = order.get("picked_up_at")
            if not picked_ts:
                _log.warning(f"R6 skip {oid}: picked_up_at missing")
                continue

            picked_dt = _parse_aware_utc(picked_ts)
            if picked_dt is None:
                _log.warning(f"R6 skip {oid}: picked_up_at unparseable: {picked_ts!r}")
                continue

            bag_time_min = (now_utc - picked_dt).total_seconds() / 60.0
            if bag_time_min <= C.BAG_TIME_PRE_WARNING_MIN:
                continue

            # Gate met. Set flag PRZED send (set-then-send, Opcja X).
            upsert_order(
                oid, {"bag_time_alerted": True}, event="R6_PRE_WARNING_ALERT"
            )

            cid = str(order.get("courier_id") or "?")
            cname = _courier_names.get(cid, cid)
            restaurant = order.get("restaurant") or "?"
            delivery = order.get("delivery_address") or "?"
            picked_hhmm = _format_picked_up_hhmm(picked_dt)

            msg = (
                f"⚠️ BAG_TIME {bag_time_min:.0f} min (limit {C.BAG_TIME_PRE_WARNING_MIN})\n"
                f"#{oid} {restaurant} → {delivery}\n"
                f"Kurier: {cname} ({cid}) • picked up {picked_hhmm}"
            )
            ok = send_admin_alert(msg)
            _stats["r6_alerts"] += 1
            if ok:
                _log.warning(
                    f"R6 ALERT sent {oid} courier={cid} bag_time={bag_time_min:.1f}min"
                )
            else:
                _log.error(
                    f"R6 alert send FAILED for order {oid} — "
                    f"flag already set, alert LOST (bag_time={bag_time_min:.1f}min)"
                )
        except Exception as e:
            _log.error(
                f"R6 check failed for order {order.get('order_id','unknown')}: {e}"
            )
            continue  # next order, nie crashuj całego ticku


def run():
    signal.signal(signal.SIGTERM, _handler)
    signal.signal(signal.SIGINT, _handler)
    _log.info("SLA tracker START")
    last_summary = time.time()

    # F2.1b step 6: load courier_names cache once on start (zero IO per tick).
    global _courier_names
    _courier_names = _load_courier_names()
    _log.info(
        f"R6 bag_time alerts enabled — courier_names loaded: {len(_courier_names)}, "
        f"threshold={C.BAG_TIME_PRE_WARNING_MIN}min"
    )

    SLA_EVENT_TYPES = ["COURIER_PICKED_UP", "COURIER_DELIVERED"]
    while _running:
        try:
            for evt in get_pending(limit=200, event_types=SLA_EVENT_TYPES):
                if process(evt):
                    mark_processed(evt["event_id"])
        except Exception as e:
            _log.error(f"loop: {e}")

        # F2.1b step 6: R6 BAG_TIME scan per tick (outer safety net).
        try:
            _check_bag_time_alerts(datetime.now(timezone.utc))
        except Exception as e:
            _log.error(f"R6 scan wrapper fail: {e}")

        if time.time() - last_summary > 300:
            _log.info(f"SUMMARY: {_stats}")
            last_summary = time.time()
        time.sleep(10)

    _log.info("SLA tracker STOP")


if __name__ == "__main__":
    run()
