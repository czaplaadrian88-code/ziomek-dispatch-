#!/usr/bin/env python3
"""state_panel_monitor.py — watchdog rozjazdu orders_state ↔ panel (Faza 5).

Oneshot, uruchamiany przez dispatch-state-panel-monitor.timer co 10 min.
Porównuje liczbę AKTYWNYCH zleceń w orders_state.json z liczbą zleceń, które
panel widzi w bagach kurierów (panel_packs_cache.json — ground-truth). Duży,
UTRZYMUJĄCY SIĘ deficyt = orders_state zgubił/utracił stan → alert Telegram.

Sieć bezpieczeństwa P2 — Fazy 1-4 strukturalnie zapobiegają utracie stanu
(D1 clobber, D2 test kasujący produkcję, D5 rekonstrukcja bagu); ten monitor
łapie unknown-unknowns: przyszły bug, awaria dysku, błąd manualny.

Anty-fałszywy-alarm: alarmuje dopiero gdy rozjazd utrzymuje się przez 2 kolejne
sprawdzenia (~20 min) — transient lag reconcile (V3.15) klaruje się w 1 tick.
"""
import json
import os
import sys
import time
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from dispatch_v2.common import setup_logger, flag

_log = setup_logger(
    "state_panel_monitor",
    "/root/.openclaw/workspace/scripts/logs/state_panel_monitor.log",
)

ORDERS_STATE = "/root/.openclaw/workspace/dispatch_state/orders_state.json"
PANEL_PACKS = "/root/.openclaw/workspace/dispatch_state/panel_packs_cache.json"
MONITOR_STATE = "/root/.openclaw/workspace/dispatch_state/state_panel_monitor.json"

# Progi (env-overridable):
PACKS_MAX_AGE_S = float(os.environ.get("STATE_PANEL_PACKS_MAX_AGE_S", "180"))
MIN_PANEL = int(os.environ.get("STATE_PANEL_MIN_PANEL", "6"))      # niżej = za mały wolumen, skip
RATIO = float(os.environ.get("STATE_PANEL_RATIO", "0.6"))          # state ≤ 60% panel = rozjazd
COOLDOWN_S = float(os.environ.get("STATE_PANEL_ALERT_COOLDOWN_S", "1800"))  # 30 min
CONSEC_REQUIRED = int(os.environ.get("STATE_PANEL_CONSEC_REQUIRED", "2"))   # 2 sprawdzenia z rzędu
ACTIVE_STATUSES = ("assigned", "picked_up")


def _load_json(path):
    with open(path) as f:
        return json.load(f)


def _packs_age_s(packs_data):
    ts = packs_data.get("ts")
    if not ts:
        return None
    try:
        dt = datetime.fromisoformat(ts)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return (datetime.now(timezone.utc) - dt).total_seconds()
    except (ValueError, TypeError):
        return None


def _panel_active_count(packs_data):
    """Suma oids w bagach realnych kurierów. Koordynator (cid=26) = wirtualny
    holding czasówek, nie kurier — wykluczamy."""
    total = 0
    for nick, oids in (packs_data.get("packs") or {}).items():
        if str(nick).strip().lower() == "koordynator":
            continue
        if isinstance(oids, list):
            total += len(oids)
    return total


def _state_active_count(state):
    return sum(1 for o in state.values()
               if isinstance(o, dict) and o.get("status") in ACTIVE_STATUSES)


def _load_monitor_state():
    try:
        d = _load_json(MONITOR_STATE)
        return d if isinstance(d, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def _save_monitor_state(d):
    tmp = MONITOR_STATE + ".tmp"
    try:
        with open(tmp, "w") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, MONITOR_STATE)
    except OSError as e:
        _log.warning(f"_save_monitor_state fail: {e}")
        if os.path.exists(tmp):
            try:
                os.unlink(tmp)
            except OSError:
                pass


def main():
    # 1. panel_packs_cache — ground-truth, z bramką świeżości
    try:
        packs_data = _load_json(PANEL_PACKS)
    except (OSError, json.JSONDecodeError) as e:
        _log.warning(f"panel_packs_cache nieczytelny: {e} — skip")
        return
    age = _packs_age_s(packs_data)
    if age is None or age > PACKS_MAX_AGE_S:
        _log.info(f"panel_packs_cache stale (age={age}) — skip "
                  f"(panel_watcher down → pokrywa parser_health)")
        return
    panel_active = _panel_active_count(packs_data)

    # 2. orders_state
    try:
        state = _load_json(ORDERS_STATE)
    except (OSError, json.JSONDecodeError) as e:
        _log.warning(f"orders_state nieczytelny: {e} — skip")
        return
    state_active = _state_active_count(state)

    gap = panel_active - state_active
    diverged = (panel_active >= MIN_PANEL
                and state_active <= panel_active * RATIO)

    st = _load_monitor_state()
    consec = int(st.get("consecutive_diverged", 0)) + 1 if diverged else 0
    st["consecutive_diverged"] = consec
    st["last_check_iso"] = datetime.now(timezone.utc).isoformat()

    _log.info(f"check: panel_active={panel_active} state_active={state_active} "
              f"gap={gap} diverged={diverged} consec={consec} packs_age={age:.0f}s")

    should_alert = (
        consec >= CONSEC_REQUIRED
        and (time.time() - float(st.get("last_alert_ts", 0))) >= COOLDOWN_S
        and flag("ENABLE_STATE_PANEL_DIVERGENCE_ALERT", True)
    )
    if should_alert:
        msg = (
            f"⚠️ ROZJAZD orders_state ↔ panel (utrzymuje się {consec} sprawdzenia)\n\n"
            f"Panel widzi {panel_active} zleceń w bagach kurierów, orders_state "
            f"ma tylko {state_active} aktywnych (deficyt {gap}).\n\n"
            f"Możliwa utrata/uszkodzenie orders_state.json. Sprawdź dispatch_state/ "
            f"+ logi state_machine. Recovery: "
            f"python3 -m dispatch_v2.tools.rebuild_state_from_events"
        )
        try:
            from dispatch_v2.telegram_utils import send_admin_alert
            send_admin_alert(msg)
            st["last_alert_ts"] = time.time()
            _log.warning(f"ALERT wysłany: panel={panel_active} state={state_active} "
                         f"gap={gap} consec={consec}")
        except Exception as e:
            _log.error(f"alert send fail: {type(e).__name__}: {e}")
    elif diverged:
        _log.info(f"rozjazd wykryty (consec={consec}) — alert wstrzymany "
                  f"(próg consec={CONSEC_REQUIRED} / cooldown / flaga)")

    _save_monitor_state(st)


if __name__ == "__main__":
    try:
        main()
    except Exception as e:
        _log.error(f"state_panel_monitor crash: {type(e).__name__}: {e}")
        raise
