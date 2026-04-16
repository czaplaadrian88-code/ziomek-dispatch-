"""Panel Watcher - event-driven polling panelu NadajeSz.

Co robi:
- Co N sekund fetchuje HTML panelu (domyslnie 10s z config.json)
- Porownuje stan z orders_state.json
- Emituje eventy przez event_bus dla kazdej zmiany
- Health tracking - detekcja PANEL_UNREACHABLE po 3 failach
- Throttling fetchu detali - tylko dla zmienionych ID (nie dla wszystkich 335)
- Respektuje kill_switch_to_v1 (wtedy spi)

Eventy emitowane:
- NEW_ORDER       - nowe ID pojawilo sie w panelu
- COURIER_ASSIGNED - nieprzypisane -> przypisane do kuriera
- COURIER_PICKED_UP - status 3/4 -> 5
- COURIER_DELIVERED - status -> 7 (wtedy tez lokalnie usuwamy z trackingu)
- PANEL_UNREACHABLE - 3+ failed fetche pod rzad

Uzywanie:
    python3 -m dispatch_v2.panel_watcher
    # lub:
    python3 /root/.openclaw/workspace/scripts/dispatch_v2/panel_watcher.py
"""
import signal
import sys
import time
from typing import Dict, Optional

from dispatch_v2.common import flag, load_config, now_iso, setup_logger
from dispatch_v2.event_bus import emit
from dispatch_v2.panel_client import (
    fetch_panel_html,
    parse_panel_html,
    fetch_order_details,
    normalize_order,
    health_check,
    IGNORED_STATUSES,
    KOORDYNATOR_ID,
)
from dispatch_v2.state_machine import (
    get_all as state_get_all,
    get_order as state_get_order,
    update_from_event,
    upsert_order,
    touch_check_cursor,
)
from dispatch_v2.geocoding import geocode

_log = setup_logger("panel_watcher", "/root/.openclaw/workspace/scripts/logs/dispatch.log")

_running = True
_fail_count = 0
_last_panel_unreachable_emit = 0.0
# Lookup address_id -> coords, zaladowany raz przy starcie (hot-reload w razie potrzeby przez restart)
_COORDS_PATH = "/root/.openclaw/workspace/dispatch_state/restaurant_coords.json"
_COORDS = {}
def _load_coords():
    global _COORDS
    try:
        import json
        with open(_COORDS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        _COORDS = {str(k): (v["lat"], v["lng"]) for k, v in data.items() if "lat" in v and "lng" in v}
    except Exception as e:
        _log.warning(f"_load_coords fail: {e}")
        _COORDS = {}
_load_coords()

_ignored_ids = set()  # ID znanych jako status 7/8/9 — nie fetchuj ponownie


def _signal_handler(signum, frame):
    global _running
    _log.info(f"Signal {signum} received, graceful shutdown")
    _running = False


# ---- PANEL_OVERRIDE detection (F2.3) ----
# Gdy panel przypisuje kuriera do orderu który był w pending_proposals (Ziomek
# wysłał propozycję), ale wybrany panel_courier_id ≠ proposed_courier_id →
# rejestrujemy jako PANEL_OVERRIDE (sygnał "koordynator ma inne zdanie").
_PENDING_PROPOSALS_PATH = "/root/.openclaw/workspace/dispatch_state/pending_proposals.json"
_LEARNING_LOG_PATH = "/root/.openclaw/workspace/dispatch_state/learning_log.jsonl"


def _check_panel_override(order_id: str, panel_courier_id: str, source: str) -> None:
    """Jeśli order_id był w pending_proposals i kurier panelu różny od propozycji
    Ziomka — zapisz PANEL_OVERRIDE do learning_log.jsonl.

    source: 'panel_initial' | 'panel_diff' | 'panel_reassign' (telemetria).
    Wywoływane TYLKO gdy emit COURIER_ASSIGNED faktycznie wyemitowało event
    (non-duplicate) — per-cycle idempotent. Żadne błędy I/O nie propagują do
    callera (panel_watcher zdrowie ma priorytet nad telemetrią).
    """
    import json
    try:
        with open(_PENDING_PROPOSALS_PATH, "r", encoding="utf-8") as f:
            pending = json.load(f)
    except FileNotFoundError:
        return
    except Exception as e:
        _log.warning(f"PANEL_OVERRIDE read pending fail: {e}")
        return

    rec = pending.get(str(order_id)) if isinstance(pending, dict) else None
    if not rec:
        return

    dr = rec.get("decision_record") or {}
    best = dr.get("best") or {}
    proposed_courier_id = str(best.get("courier_id") or "")
    proposed_score = best.get("score")

    if not proposed_courier_id or proposed_courier_id == str(panel_courier_id):
        return

    override_rec = {
        "ts": now_iso(),
        "order_id": str(order_id),
        "action": "PANEL_OVERRIDE",
        "proposed_courier_id": proposed_courier_id,
        "proposed_score": proposed_score,
        "actual_courier_id": str(panel_courier_id),
        "panel_source": source,
        "decision": dr,
    }
    try:
        with open(_LEARNING_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(override_rec, ensure_ascii=False) + "\n")
    except Exception as e:
        _log.warning(f"PANEL_OVERRIDE write learning_log fail oid={order_id}: {e}")
        return

    _log.info(
        f"PANEL_OVERRIDE oid={order_id} proposed={proposed_courier_id} "
        f"(score={proposed_score}) actual={panel_courier_id} src={source}"
    )


def _diff_and_emit(parsed: dict, csrf: str) -> dict:
    """Porownuje stan panel vs orders_state, emituje eventy.
    Zwraca statystyki tego cyklu."""
    stats = {
        "new": 0,
        "assigned": 0,
        "picked_up": 0,
        "delivered": 0,
        "ignored": 0,
        "fetched_details": 0,
        "errors": 0,
    }

    current_state = state_get_all()
    html_order_ids = set(parsed["order_ids"])
    assigned_in_panel = parsed["assigned_ids"]
    rest_names = parsed["rest_names"]

    # 1. NOWE: ID widoczne w HTML ale nieznane w state
    for zid in parsed["order_ids"]:
        if zid in current_state:
            continue
        if zid in _ignored_ids:
            stats["ignored"] += 1
            continue

        # Nowe ID - fetch details i normalize
        try:
            raw = fetch_order_details(zid, csrf)
            stats["fetched_details"] += 1
        except Exception as e:
            _log.warning(f"fetch_details({zid}) fail: {e}")
            stats["errors"] += 1
            continue

        if not raw:
            continue

        norm = normalize_order(raw, rest_names.get(zid))
        if norm is None:
            stats["ignored"] += 1
            _ignored_ids.add(zid)
            continue

        # Emit NEW_ORDER (idempotent per zid + first_seen)
        _aid = norm.get("address_id")
        _aid_str = str(_aid) if _aid is not None else None
        _pcoords = _COORDS.get(_aid_str) if _aid_str else None

        # Geocode delivery address (cache hit ~90% = 0ms, miss = Google API max 2s)
        _del_addr = norm.get("delivery_address")
        _dcoords = None
        if _del_addr:
            _dcoords = geocode(_del_addr, timeout=2.0)
            if _dcoords is None:
                _log.warning(f"NEW_ORDER {zid}: geocode fail for '{_del_addr}'")

        ev_payload = {
            "restaurant": norm["restaurant"],
            "pickup_address": norm["pickup_address"],
            "delivery_address": norm["delivery_address"],
            "pickup_at_warsaw": norm["pickup_at_warsaw"],
            "prep_minutes": norm["prep_minutes"],
            "order_type": norm["order_type"],
            "status_id": norm["status_id"],
            "first_seen": now_iso(),
            "address_id": _aid_str,
            "pickup_coords": list(_pcoords) if _pcoords else None,
            "delivery_coords": list(_dcoords) if _dcoords else None,
        }

        # Deterministyczny event_id: {order_id}_NEW_ORDER_first_seen (bez timestamp - raz na zycie)
        event_id = f"{zid}_NEW_ORDER_first"
        result = emit(
            "NEW_ORDER",
            order_id=zid,
            payload=ev_payload,
            event_id=event_id,
        )
        if result:
            stats["new"] += 1
            # Aktualizuj state
            update_from_event({
                "event_type": "NEW_ORDER",
                "order_id": zid,
                "payload": ev_payload,
            })
            _log.info(f"NEW {zid} {norm['order_type']} {norm['restaurant']} pickup={norm['pickup_at_warsaw']}")

        # Jesli nowe i juz przypisane do kuriera od razu - emit ASSIGNED
        if norm["id_kurier"] and not norm["is_koordynator"]:
            courier_id = str(norm["id_kurier"])
            assigned_event = emit(
                "COURIER_ASSIGNED",
                order_id=zid,
                courier_id=courier_id,
                payload={"assigned_at": now_iso(), "source": "panel_initial"},
                event_id=f"{zid}_COURIER_ASSIGNED_{courier_id}_initial",
            )
            if assigned_event:
                stats["assigned"] += 1
                update_from_event({
                    "event_type": "COURIER_ASSIGNED",
                    "order_id": zid,
                    "courier_id": courier_id,
                    "payload": {"source": "panel_initial"},
                })
                _check_panel_override(zid, courier_id, "panel_initial")

    # 2. ZMIANY: ID znane w state, sprawdz czy cos sie zmienilo
    for zid, state_order in list(current_state.items()):
        # Pomijamy terminalne (delivered, cancelled) - nie obserwujemy ich dalej
        if state_order.get("status") in ("delivered", "returned_to_pool", "cancelled"):
            continue

        # Czy zlecenie nadal widoczne w panelu?
        if zid not in html_order_ids:
            # Zniknelo - moze zostalo zakonczone lub anulowane
            # Sprawdzmy details zeby wiedziec
            try:
                raw = fetch_order_details(zid, csrf)
                stats["fetched_details"] += 1
                if raw:
                    status_id = raw.get("id_status_zamowienia")
                    if status_id == 7:
                        # Doreczone
                        ev = emit(
                            "COURIER_DELIVERED",
                            order_id=zid,
                            courier_id=str(raw.get("id_kurier") or ""),
                            payload={
                                "timestamp": raw.get("czas_doreczenia") or now_iso(),
                                "final_location": state_order.get("delivery_address"),
                            },
                            event_id=f"{zid}_COURIER_DELIVERED_panel",
                        )
                        if ev:
                            stats["delivered"] += 1
                            update_from_event({
                                "event_type": "COURIER_DELIVERED",
                                "order_id": zid,
                                "courier_id": str(raw.get("id_kurier") or ""),
                                "payload": {"timestamp": raw.get("czas_doreczenia")},
                            })
                            _log.info(f"DELIVERED {zid}")
                    elif status_id in (8, 9):
                        # Cancelled / nieodebrano
                        upsert_order(zid, {"status": "cancelled"}, event="PANEL_CANCELLED")
                        _log.info(f"CANCELLED {zid} status={status_id}")
            except Exception as e:
                _log.warning(f"details for disappeared {zid}: {e}")
                stats["errors"] += 1
            continue

        # Nadal w panelu - sprawdz zmiany na podstawie HTML (tanie, bez fetch details)
        was_assigned = state_order.get("status") == "assigned"
        is_assigned_now = zid in assigned_in_panel

        # Transition: planned -> assigned
        if not was_assigned and is_assigned_now:
            # Fetch details zeby wiedziec ktory kurier
            try:
                raw = fetch_order_details(zid, csrf)
                stats["fetched_details"] += 1
                if raw and raw.get("id_kurier") and raw["id_kurier"] != KOORDYNATOR_ID:
                    courier_id = str(raw["id_kurier"])
                    ev = emit(
                        "COURIER_ASSIGNED",
                        order_id=zid,
                        courier_id=courier_id,
                        payload={"source": "panel_diff"},
                        event_id=f"{zid}_COURIER_ASSIGNED_{courier_id}_diff",
                    )
                    if ev:
                        stats["assigned"] += 1
                        update_from_event({
                            "event_type": "COURIER_ASSIGNED",
                            "order_id": zid,
                            "courier_id": courier_id,
                            "payload": {"source": "panel_diff"},
                        })
                        _log.info(f"ASSIGNED {zid} -> {courier_id}")
                        _check_panel_override(zid, courier_id, "panel_diff")
            except Exception as e:
                _log.warning(f"fetch for assigned {zid}: {e}")
                stats["errors"] += 1

        # Reassignment: kurier zmieniony na already-assigned order (F2.1c)
        elif was_assigned and is_assigned_now and reassign_checked < MAX_REASSIGN_PER_CYCLE:
            state_courier = state_order.get("courier_id", "")
            try:
                raw = fetch_order_details(zid, csrf)
                stats["fetched_details"] += 1
                reassign_checked += 1
                panel_courier = str(raw.get("id_kurier") or "") if raw else ""
                if panel_courier and panel_courier != state_courier and raw.get("id_kurier") != KOORDYNATOR_ID:
                    ev = emit(
                        "COURIER_ASSIGNED",
                        order_id=zid,
                        courier_id=panel_courier,
                        payload={"source": "panel_reassign"},
                        event_id=f"{zid}_COURIER_ASSIGNED_{panel_courier}_reassign",
                    )
                    if ev:
                        stats["assigned"] += 1
                        update_from_event({
                            "event_type": "COURIER_ASSIGNED",
                            "order_id": zid,
                            "courier_id": panel_courier,
                            "payload": {"source": "panel_reassign"},
                        })
                        _log.info(f"REASSIGNED {zid} {state_courier} -> {panel_courier}")
                        _check_panel_override(zid, panel_courier, "panel_reassign")
            except Exception as e:
                _log.warning(f"fetch for reassign {zid}: {e}")
                stats["errors"] += 1

    # ================== RECONCILE STATUS ==================
    # Dla orderow ktore state widzi jako assigned/picked_up, a panel widzi jako closed
    # (bez data-idkurier w bloku HTML = status 7/8/9) - fetch details i emit event.
    # Budzet 10 fetchow na cykl (10 * 200ms = 2s) zeby nie wysycic panelu.
    closed = parsed.get("closed_ids", set())
    MAX_REASSIGN_PER_CYCLE = 5
    reassign_checked = 0
    MAX_RECONCILE_PER_CYCLE = 25  # F2.1c: zwiększone z 10 (zombie backlog)
    reconciled = 0
    for zid, sorder in list(current_state.items()):
        if reconciled >= MAX_RECONCILE_PER_CYCLE:
            break
        if sorder.get("status") not in ("assigned", "picked_up"):
            continue
        if zid not in closed:
            continue
        try:
            raw = fetch_order_details(zid, csrf)
            stats["fetched_details"] += 1
            reconciled += 1
        except Exception as e:
            _log.warning(f"reconcile fetch({zid}): {e}")
            stats["errors"] += 1
            continue
        if not raw:
            continue
        sid = raw.get("id_status_zamowienia")
        kid = str(raw.get("id_kurier") or "")
        deliv_addr = parsed.get("delivery_addresses", {}).get(zid) or sorder.get("delivery_address")
        if sid == 7:
            ev = emit(
                "COURIER_DELIVERED",
                order_id=zid,
                courier_id=kid,
                payload={
                    "timestamp": raw.get("czas_doreczenia") or now_iso(),
                    "final_location": deliv_addr,
                    "delivery_address": deliv_addr,
                    "source": "reconcile",
                },
                event_id=f"{zid}_COURIER_DELIVERED_reconcile",
            )
            if ev:
                stats["delivered"] += 1
                update_from_event({
                    "event_type": "COURIER_DELIVERED",
                    "order_id": zid,
                    "courier_id": kid,
                    "payload": {
                        "timestamp": raw.get("czas_doreczenia"),
                        "final_location": deliv_addr,
                        "delivery_address": deliv_addr,
                    },
                })
                _log.info(f"DELIVERED {zid} (reconcile) kurier={kid}")
        elif sid in (8, 9):
            reason = "undelivered" if sid == 8 else "cancelled"
            ev = emit(
                "ORDER_RETURNED_TO_POOL",
                order_id=zid,
                payload={"reason": reason, "source": "reconcile"},
                event_id=f"{zid}_ORDER_RETURNED_{reason}_reconcile",
            )
            if ev:
                update_from_event({
                    "event_type": "ORDER_RETURNED_TO_POOL",
                    "order_id": zid,
                    "payload": {"reason": reason},
                })
                _log.info(f"{reason.upper()} {zid} (reconcile)")
    # ================== END RECONCILE ==================

    # ================== PICKED_UP RECONCILE ==================
    # Panel HTML nie rozroznia status 3 (assigned) od 5 (picked_up).
    # Robimy round-robin: fetch max N orderow z najstarszym assigned_check_ts.
    # Jesli dzien_odbioru is not None -> emit COURIER_PICKED_UP z pickup_coords.
    # Cursor touch_check_cursor dla KAZDEGO sprawdzonego, zeby sie przesuwal.
    PICKED_UP_RECONCILE_BUDGET = 10
    # Kandydaci: status=assigned w state, NIE w closed (bo tamte lapie reconcile delivered)
    candidates = []
    for zid, sorder in current_state.items():
        if sorder.get("status") != "assigned":
            continue
        if zid in closed:
            continue
        # Round-robin key: brak cursora = "nigdy nie sprawdzany" = najwyzszy priorytet (None < str)
        candidates.append((sorder.get("assigned_check_ts") or "", zid, sorder))
    candidates.sort(key=lambda x: x[0])
    pu_checked = 0
    for _, zid, sorder in candidates[:PICKED_UP_RECONCILE_BUDGET]:
        try:
            raw = fetch_order_details(zid, csrf)
            stats["fetched_details"] += 1
            pu_checked += 1
        except Exception as e:
            _log.warning(f"pu_reconcile fetch({zid}): {e}")
            stats["errors"] += 1
            touch_check_cursor(zid)  # cursor przesuwa sie nawet gdy fetch fail
            continue
        touch_check_cursor(zid)
        if not raw:
            continue
        sid = raw.get("id_status_zamowienia")
        dzien_odbioru = raw.get("dzien_odbioru")
        if sid == 5 and dzien_odbioru:
            kid = str(raw.get("id_kurier") or "")
            # pickup_coords z lookup - order moze miec address_id w state (po patch enrichment)
            # lub fallback z raw.address.id
            aid = sorder.get("address_id") or (raw.get("address", {}) or {}).get("id")
            aid_str = str(aid) if aid is not None else None
            pu_coords = _COORDS.get(aid_str) if aid_str else None
            ev = emit(
                "COURIER_PICKED_UP",
                order_id=zid,
                courier_id=kid,
                payload={
                    "timestamp": dzien_odbioru,
                    "pickup_coords": list(pu_coords) if pu_coords else None,
                    "source": "reconcile",
                },
                event_id=f"{zid}_COURIER_PICKED_UP_reconcile",
            )
            if ev:
                stats["picked_up"] += 1
                update_from_event({
                    "event_type": "COURIER_PICKED_UP",
                    "order_id": zid,
                    "courier_id": kid,
                    "payload": {
                        "timestamp": dzien_odbioru,
                        "pickup_coords": list(pu_coords) if pu_coords else None,
                    },
                })
                _log.info(f"PICKED_UP {zid} (reconcile) kurier={kid} at {dzien_odbioru}")
    # ================== END PICKED_UP RECONCILE ==================

    return stats


def tick(cycle_num: int) -> dict:
    """Jeden cykl watchera. Zwraca statystyki."""
    global _fail_count, _last_panel_unreachable_emit

    cycle_stats = {"cycle": cycle_num, "at": now_iso()}

    try:
        html = fetch_panel_html()
        parsed = parse_panel_html(html)
        cycle_stats["orders_in_panel"] = len(parsed["order_ids"])

        # Udany fetch - reset fail counter
        if _fail_count > 0:
            _log.info(f"Panel recovered po {_fail_count} failach")
            _fail_count = 0

        from dispatch_v2.panel_client import _session
        csrf = _session.get("csrf") or ""

        diff_stats = _diff_and_emit(parsed, csrf)
        cycle_stats.update(diff_stats)

    except Exception as e:
        _fail_count += 1
        cycle_stats["error"] = f"{type(e).__name__}: {e}"
        _log.error(f"tick fail #{_fail_count}: {e}")

        # Po 3 failach emit PANEL_UNREACHABLE (throttled: max 1/min)
        if _fail_count >= 3 and time.time() - _last_panel_unreachable_emit > 60:
            emit(
                "PANEL_UNREACHABLE",
                payload={"fail_count": _fail_count, "last_error": str(e)},
                event_id=f"PANEL_UNREACHABLE_{int(time.time() / 60)}",
            )
            _last_panel_unreachable_emit = time.time()

    return cycle_stats


def run():
    """Glowna petla watchera."""
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    cfg = load_config()
    interval = cfg["polling"]["panel_interval_seconds"]
    _log.info(f"Panel watcher START interval={interval}s")

    # Health check na start
    h = health_check()
    if not h["login_ok"]:
        _log.critical(f"HEALTH FAIL: {h}")
        sys.exit(1)
    _log.info(f"Health OK: {h.get('stats')}")

    cycle = 0
    last_log_summary = time.time()
    totals = {"new": 0, "assigned": 0, "picked_up": 0, "delivered": 0, "ignored": 0, "errors": 0}

    while _running:
        cycle += 1

        # Kill switch
        if flag("kill_switch_to_v1", False):
            _log.warning("kill_switch_to_v1=TRUE, sleeping 30s")
            time.sleep(30)
            continue

        t0 = time.time()
        stats = tick(cycle)
        elapsed = time.time() - t0

        # Zbieramy totals
        for k in totals:
            totals[k] += stats.get(k, 0)

        # Summary co 60s
        if time.time() - last_log_summary >= 60:
            _log.info(
                f"SUMMARY {cycle} cykli, elapsed_last={elapsed:.1f}s, "
                f"panel={stats.get('orders_in_panel','?')}, totals={totals}"
            )
            totals = {k: 0 for k in totals}
            last_log_summary = time.time()

        # Detail log tylko gdy cos sie wydarzylo
        if any(stats.get(k, 0) > 0 for k in ("new", "assigned", "picked_up", "delivered")):
            _log.info(f"TICK {cycle}: {stats}")

        # Sleep do nastepnego cyklu
        sleep_for = max(0.5, interval - elapsed)
        time.sleep(sleep_for)

    _log.info("Panel watcher STOP")


if __name__ == "__main__":
    run()
