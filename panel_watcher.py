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
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from dispatch_v2.common import flag, load_config, now_iso, setup_logger
from dispatch_v2.event_bus import emit
from dispatch_v2.parser_health import get_monitor as get_parser_health_monitor
from dispatch_v2.parser_health_layer3 import install_layer3, record_tick_full
from dispatch_v2.parser_health_endpoint import start_health_endpoint
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


def _save_plan_on_assign(order_id: str, courier_id: str) -> None:
    """V3.19b: zapisz plan z pending_proposals po emit COURIER_ASSIGNED.

    Odczytuje pending_proposals[oid].decision_record.best.plan i mapuje na
    plan_manager schema. Skip cicho gdy: flag off, pending brak, best courier
    ≠ assigned courier (PANEL_OVERRIDE — kurier koordynatora, nie nasz), brak
    plan.sequence. Żadne błędy nie propagują do callera.
    """
    try:
        from dispatch_v2.common import ENABLE_SAVED_PLANS
        if not ENABLE_SAVED_PLANS:
            return
    except Exception:
        return
    try:
        import json
        with open(_PENDING_PROPOSALS_PATH, "r", encoding="utf-8") as f:
            pending = json.load(f)
    except (FileNotFoundError, Exception):
        return
    rec = pending.get(str(order_id)) if isinstance(pending, dict) else None
    if not rec:
        return
    dr = rec.get("decision_record") or {}
    best = dr.get("best") or {}
    proposed_cid = str(best.get("courier_id") or "")
    if not proposed_cid or proposed_cid != str(courier_id):
        return  # PANEL_OVERRIDE — plan kuriera A, koordynator przypisał B
    plan = best.get("plan") or {}
    sequence = plan.get("sequence") or []
    if not sequence:
        return
    predicted = plan.get("predicted_delivered_at") or {}
    pickup_at = plan.get("pickup_at") or {}
    bag_ctx = {str(b.get("order_id")): b for b in (best.get("bag_context") or [])}
    # start_pos z best.pos_source; lat/lng niestety nie w decision_record,
    # użyj fallback (courier_resolver się dopisze przy next propose).
    start_pos = {
        "lat": 0.0, "lng": 0.0,
        "source": best.get("pos_source") or "unknown",
        "source_ts": rec.get("ts"),
    }
    stops = []
    for oid in sequence:
        oid_s = str(oid)
        # pickup first (jeśli w pickup_at — oznacza że nowy order miał pickup w planie)
        if oid_s in pickup_at:
            stops.append({
                "order_id": oid_s,
                "type": "pickup",
                "coords": {"lat": 0.0, "lng": 0.0},
                "scheduled_at": None,
                "predicted_at": pickup_at[oid_s],
                "dwell_min": 2.0,
                "status_at_plan_time": "assigned",
            })
        pred = predicted.get(oid_s)
        stops.append({
            "order_id": oid_s,
            "type": "dropoff",
            "coords": {"lat": 0.0, "lng": 0.0},
            "scheduled_at": None,
            "predicted_at": pred,
            "dwell_min": 1.0,
            "status_at_plan_time": "picked_up" if oid_s in bag_ctx else "assigned",
        })
    body = {
        "start_pos": start_pos,
        "start_ts": dr.get("ts") or now_iso(),
        "stops": stops,
        "optimization_method": plan.get("strategy") or "bruteforce",
    }
    try:
        from dispatch_v2 import plan_manager
        plan_manager.save_plan(str(courier_id), body)
        _log.info(f"V3.19b plan saved cid={courier_id} oid={order_id} stops={len(stops)}")
    except Exception as e:
        _log.warning(f"V3.19b save_plan fail cid={courier_id} oid={order_id}: {e}")


def _advance_plan_on_deliver(courier_id: str, order_id: str,
                             delivered_at_raw: Optional[str],
                             delivery_coords: Optional[list]) -> None:
    """V3.19b: advance plan po emit COURIER_DELIVERED sukces."""
    try:
        from dispatch_v2.common import ENABLE_SAVED_PLANS
        if not ENABLE_SAVED_PLANS:
            return
    except Exception:
        return
    if not courier_id:
        return
    try:
        from dispatch_v2 import plan_manager
        coords_tuple = None
        if delivery_coords and isinstance(delivery_coords, (list, tuple)) \
                and len(delivery_coords) == 2:
            coords_tuple = (float(delivery_coords[0]), float(delivery_coords[1]))
        plan_manager.advance_plan(
            str(courier_id),
            str(order_id),
            delivered_at_raw or now_iso(),
            coords_tuple,
        )
    except Exception as e:
        _log.warning(f"V3.19b advance_plan fail cid={courier_id} oid={order_id}: {e}")


def _remove_stops_on_return(courier_id: str, order_id: str) -> None:
    """V3.19b: remove_stops po emit ORDER_RETURNED_TO_POOL sukces."""
    try:
        from dispatch_v2.common import ENABLE_SAVED_PLANS
        if not ENABLE_SAVED_PLANS:
            return
    except Exception:
        return
    if not courier_id:
        return
    try:
        from dispatch_v2 import plan_manager
        plan_manager.remove_stops(str(courier_id), str(order_id))
    except Exception as e:
        _log.warning(f"V3.19b remove_stops fail cid={courier_id} oid={order_id}: {e}")


def _update_plan_on_picked_up(courier_id: str, order_id: str,
                              picked_up_at: Optional[str] = None) -> None:
    """V3.19c sub A: po emit COURIER_PICKED_UP sukces. Update
    stop.status_at_plan_time + prune pickup stop (jeśli był).
    """
    try:
        from dispatch_v2.common import ENABLE_SAVED_PLANS
        if not ENABLE_SAVED_PLANS:
            return
    except Exception:
        return
    if not courier_id:
        return
    try:
        from dispatch_v2 import plan_manager
        plan_manager.mark_picked_up(str(courier_id), str(order_id), picked_up_at)
    except Exception as e:
        _log.warning(f"V3.19c mark_picked_up fail cid={courier_id} oid={order_id}: {e}")


def _diff_czas_kuriera(old_state: dict, fresh_response: dict,
                      oid: str) -> Optional[dict]:
    """V3.19g1: detect czas_kuriera change for already-assigned order.

    Returns None (no-op) when:
      - no change, below threshold, first acceptance (null→val), val→null revert
    Returns event dict ({event_type, order_id, courier_id, payload}) when
    |Δt| >= V319G_CK_DELTA_THRESHOLD_MIN (default 3 min).

    Caller should pass event dict to state_machine.update_from_event.
    """
    from dispatch_v2.common import V319G_CK_DELTA_THRESHOLD_MIN

    old_state = old_state or {}
    fresh_response = fresh_response or {}

    old_ck_iso = old_state.get("czas_kuriera_warsaw")
    old_ck_hhmm = old_state.get("czas_kuriera_hhmm")
    new_ck_iso = fresh_response.get("czas_kuriera_warsaw")
    new_ck_hhmm = fresh_response.get("czas_kuriera_hhmm") or fresh_response.get("czas_kuriera")

    # null→null
    if not old_ck_iso and not new_ck_iso:
        return None
    # V3.27.1 BUG-1: null→value (first acceptance) — emit synth event z source=first_acceptance.
    # Pre-V3.27.1 zwracało None tutaj — efekt: 100% (47/47) assigned/picked_up orderów
    # miało czas_kuriera_warsaw=None w orders_state.json. delta_min=None (brak baseline).
    if not old_ck_iso and new_ck_iso:
        payload = {
            "oid": oid,
            "courier_id": old_state.get("courier_id"),
            "old_ck_iso": None,
            "old_ck_hhmm": None,
            "new_ck_iso": new_ck_iso,
            "new_ck_hhmm": new_ck_hhmm,
            "delta_min": None,
            "source": "first_acceptance",
        }
        return {
            "event_type": "CZAS_KURIERA_UPDATED",
            "order_id": oid,
            "courier_id": old_state.get("courier_id"),
            "payload": payload,
            "event_id_suffix": "_FIRST_ACK",
        }
    # value→null (panel revert — warn, skip)
    if old_ck_iso and not new_ck_iso:
        _log.warning(f"v319g1 oid={oid} ck_change_to_null old={old_ck_hhmm}")
        return None

    # value→value — compute signed delta
    try:
        old_dt = datetime.fromisoformat(old_ck_iso)
        new_dt = datetime.fromisoformat(new_ck_iso)
    except (ValueError, TypeError) as e:
        _log.warning(f"v319g1 oid={oid} ck iso parse fail: {e}")
        return None

    delta_min = (new_dt - old_dt).total_seconds() / 60.0
    if abs(delta_min) < V319G_CK_DELTA_THRESHOLD_MIN:
        return None  # noise floor

    payload = {
        "oid": oid,
        "courier_id": old_state.get("courier_id"),
        "old_ck_iso": old_ck_iso,
        "old_ck_hhmm": old_ck_hhmm,
        "new_ck_iso": new_ck_iso,
        "new_ck_hhmm": new_ck_hhmm,
        "delta_min": round(delta_min, 2),
        "source": "panel_re_check",
    }
    return {
        "event_type": "CZAS_KURIERA_UPDATED",
        "order_id": oid,
        "courier_id": old_state.get("courier_id"),
        "payload": payload,
    }


def _compute_kid_diagnostic(state_order: dict, fresh_order: dict) -> dict:
    """V3.19g1 diagnostic: kid_state / kid_panel / kid_mismatch.

    Case 2 observability only (no event emitted — Case 2 full detection
    deferred per V3.19g1 design sec K). Used to diagnose HTML-lag scenarios
    where panel API shows id_kurier!=state.courier_id.
    """
    state_order = state_order or {}
    fresh_order = fresh_order or {}

    def _coerce_int(v):
        if v is None or v == "":
            return None
        try:
            return int(v)
        except (ValueError, TypeError):
            return None

    kid_state = _coerce_int(state_order.get("courier_id"))
    kid_panel = _coerce_int(fresh_order.get("id_kurier"))

    # mismatch: state ≠ panel (both sides present). Flags both directions:
    #  - state=400, panel=26   → HTML lag (panel stale, state fresh)
    #  - state=26,  panel=400  → state lag (state stale, panel fresh via API)
    # null on either side → no mismatch (no data to compare).
    if kid_state is None or kid_panel is None:
        mismatch = False
    else:
        mismatch = (kid_state != kid_panel)

    return {
        "v319g_kid_state": kid_state,
        "v319g_kid_panel": kid_panel,
        "v319g_kid_mismatch": mismatch,
        "_event": None,  # diagnostic only — Case 2 emission deferred
    }


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
        _del_city = norm.get("delivery_city")
        _dcoords = None
        if _del_addr:
            _dcoords = geocode(_del_addr, city=_del_city, timeout=2.0)
            if _dcoords is None:
                _log.warning(f"NEW_ORDER {zid}: geocode fail for '{_del_addr}' city={_del_city!r}")

        ev_payload = {
            "restaurant": norm["restaurant"],
            "pickup_address": norm["pickup_address"],
            "pickup_city": norm.get("pickup_city"),
            "delivery_address": norm["delivery_address"],
            "delivery_city": _del_city,
            "pickup_at_warsaw": norm["pickup_at_warsaw"],
            "prep_minutes": norm["prep_minutes"],
            "order_type": norm["order_type"],
            "status_id": norm["status_id"],
            "first_seen": now_iso(),
            "address_id": _aid_str,
            "pickup_coords": list(_pcoords) if _pcoords else None,
            "delivery_coords": list(_dcoords) if _dcoords else None,
            # V3.19f: czas_kuriera 2-field propagation (Step 5 emit layer).
            # Parse+persist zawsze (niezależnie od flagi). Pipeline consume pod flagą.
            "czas_kuriera_warsaw": norm.get("czas_kuriera_warsaw"),
            "czas_kuriera_hhmm": norm.get("czas_kuriera_hhmm"),
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
            # V3.19f: initial-assign payload z czas_kuriera (norm świeży).
            _assigned_payload = {
                "assigned_at": now_iso(),
                "source": "panel_initial",
                "czas_kuriera_warsaw": norm.get("czas_kuriera_warsaw"),
                "czas_kuriera_hhmm": norm.get("czas_kuriera_hhmm"),
            }
            assigned_event = emit(
                "COURIER_ASSIGNED",
                order_id=zid,
                courier_id=courier_id,
                payload=_assigned_payload,
                event_id=f"{zid}_COURIER_ASSIGNED_{courier_id}_initial",
            )
            if assigned_event:
                stats["assigned"] += 1
                update_from_event({
                    "event_type": "COURIER_ASSIGNED",
                    "order_id": zid,
                    "courier_id": courier_id,
                    "payload": _assigned_payload,
                })
                _check_panel_override(zid, courier_id, "panel_initial")
                _save_plan_on_assign(zid, courier_id)

    # 2. ZMIANY: ID znane w state, sprawdz czy cos sie zmienilo
    # V3.15 pre-req fix: reassign_checked/MAX_REASSIGN_PER_CYCLE musi być
    # zainicjalizowane PRZED pętlą (używane w L330-335). Wcześniej init był
    # po pętli (L364-365) → UnboundLocalError przy każdym tick → całe
    # _diff_and_emit failowało, blokując m.in. V3.15 packs fallback.
    MAX_REASSIGN_PER_CYCLE = 5
    reassign_checked = 0
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
                            _adv_cid = str(raw.get("id_kurier") or "")
                            update_from_event({
                                "event_type": "COURIER_DELIVERED",
                                "order_id": zid,
                                "courier_id": _adv_cid,
                                "payload": {"timestamp": raw.get("czas_doreczenia")},
                            })
                            _log.info(f"DELIVERED {zid}")
                            _advance_plan_on_deliver(
                                _adv_cid, zid,
                                raw.get("czas_doreczenia"),
                                state_order.get("delivery_coords"),
                            )
                    elif status_id in (8, 9):
                        # TASK 2 Część A (2026-05-04): mirror reconcile path L960.
                        # Pre-fix: upsert_order(status='cancelled') aktualizował state
                        # ale NIE emitował do events.db → akumulacja phantom orders.
                        reason = "undelivered" if status_id == 8 else "cancelled"
                        _adv_cid = str(raw.get("id_kurier") or "")
                        ev = emit(
                            "ORDER_RETURNED_TO_POOL",
                            order_id=zid,
                            courier_id=_adv_cid,
                            payload={"reason": reason, "source": "panel_diff"},
                            event_id=f"{zid}_ORDER_RETURNED_{reason}_panel_diff",
                        )
                        if ev:
                            update_from_event({
                                "event_type": "ORDER_RETURNED_TO_POOL",
                                "order_id": zid,
                                "courier_id": _adv_cid,
                                "payload": {"reason": reason},
                            })
                            _log.info(f"{reason.upper()} {zid} status={status_id} (panel_diff)")
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
                        _save_plan_on_assign(zid, courier_id)
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
                        _save_plan_on_assign(zid, panel_courier)
            except Exception as e:
                _log.warning(f"fetch for reassign {zid}: {e}")
                stats["errors"] += 1

    # ================== PANEL_PACKS FALLBACK (V3.15) ==================
    # parse_panel_html zwraca courier_packs {nick: [order_ids]} — ground
    # truth z HTML panelu (każdy tick). Do V3.14 dead data. V3.15: fallback
    # trigger gdy orders_state.cid != panel_packs mapping → wymuś fetch +
    # emit COURIER_ASSIGNED. Rozwiązuje lag 15-90s dla świeżych assignments
    # (bug #467164 Michał Li: bag=0 w pipeline mimo 4 orderów w panelu).
    try:
        from dispatch_v2.common import (
            ENABLE_PANEL_PACKS_FALLBACK as _packs_flag,
            PACKS_FALLBACK_MAX_PER_CYCLE as _packs_budget,
        )
    except Exception:
        _packs_flag, _packs_budget = True, 10

    if _packs_flag:
        packs = parsed.get("courier_packs") or {}
        if packs:
            # Lazy load kurier_ids.json reverse {name: cid} z ambiguity detection
            try:
                import json as _json
                with open("/root/.openclaw/workspace/dispatch_state/kurier_ids.json") as _f:
                    _kurier_ids = _json.load(_f)
                _name_to_cid = {}
                _ambiguous_names = set()
                for _nm, _cid in _kurier_ids.items():
                    _nm_key = _nm.strip()
                    if _nm_key in _name_to_cid and _name_to_cid[_nm_key] != str(_cid):
                        _ambiguous_names.add(_nm_key)
                    _name_to_cid[_nm_key] = str(_cid)
            except Exception as _e:
                _log.warning(f"packs fallback: kurier_ids load fail: {_e}")
                _name_to_cid = {}
                _ambiguous_names = set()

            _packs_checked = 0
            _packs_catchup = 0
            for _nick, _oids in packs.items():
                if _packs_checked >= _packs_budget:
                    break
                _nick_key = (_nick or "").strip()
                if not _nick_key or not _oids:
                    continue
                if _nick_key in _ambiguous_names:
                    _log.warning(f"packs fallback: skip ambiguous nick {_nick_key!r}")
                    continue
                _target_cid = _name_to_cid.get(_nick_key)
                if not _target_cid:
                    # Nick spoza kurier_ids.json (np. PIN-only courier w Courier App) — skip
                    continue
                for _oid in _oids:
                    if _packs_checked >= _packs_budget:
                        break
                    _oid_str = str(_oid)
                    _sorder = current_state.get(_oid_str) or {}
                    _state_cid = str(_sorder.get("courier_id") or "")
                    if _state_cid == _target_cid:
                        continue  # already in sync
                    _state_status = _sorder.get("status")
                    if _state_status in ("delivered", "returned_to_pool", "cancelled"):
                        continue  # terminal — nie wzbogacaj V3.14-filtered
                    # Mismatch — fetch_details do weryfikacji raw id_kurier
                    try:
                        _raw = fetch_order_details(_oid_str, csrf)
                        stats["fetched_details"] += 1
                        _packs_checked += 1
                    except Exception as _fe:
                        _log.warning(f"packs fallback fetch({_oid_str}): {_fe}")
                        stats["errors"] += 1
                        continue
                    if not _raw:
                        continue
                    _panel_cid = str(_raw.get("id_kurier") or "")
                    _sid = _raw.get("id_status_zamowienia")
                    if _sid in IGNORED_STATUSES:
                        continue
                    if not _panel_cid or _panel_cid == str(KOORDYNATOR_ID):
                        continue
                    if _panel_cid != _target_cid:
                        _log.warning(
                            f"packs fallback: nick={_nick_key!r} map→{_target_cid} "
                            f"but raw id_kurier={_panel_cid} for oid={_oid_str} — trust raw"
                        )
                        _target_cid = _panel_cid
                    _ev = emit(
                        "COURIER_ASSIGNED",
                        order_id=_oid_str,
                        courier_id=_target_cid,
                        payload={
                            "source": "packs_fallback",
                            "previous_cid": _state_cid or None,
                            "nick": _nick_key,
                        },
                        event_id=f"{_oid_str}_COURIER_ASSIGNED_{_target_cid}_packs",
                    )
                    if _ev:
                        stats["assigned"] += 1
                        _packs_catchup += 1
                        update_from_event({
                            "event_type": "COURIER_ASSIGNED",
                            "order_id": _oid_str,
                            "courier_id": _target_cid,
                            "payload": {"source": "packs_fallback"},
                        })
                        _log.info(
                            f"PACKS_CATCHUP {_oid_str} → cid={_target_cid} nick={_nick_key!r} "
                            f"(was cid={_state_cid or 'None'})"
                        )
                        _save_plan_on_assign(_oid_str, _target_cid)
            if _packs_catchup:
                stats["packs_catchup"] = _packs_catchup
    # ================== END PANEL_PACKS FALLBACK ==================

    # ================== V3.20 PACKS GHOST DETECT ==================
    # Odwrotność V3.15: oid w orders_state z cid+status=active, ale nick
    # tego kuriera jest w packs i oid NIE w packs[nick] → order zniknął z
    # kuriera bag w panelu (delivered/returned). fetch_details potwierdza
    # status=7 zanim emit COURIER_DELIVERED. Rozwiązuje 6min reconcile lag.
    try:
        from dispatch_v2.common import (
            ENABLE_V320_PACKS_GHOST_DETECT as _ghost_flag,
            GHOST_DETECT_AGE_MIN as _ghost_age_min,
            GHOST_DETECT_MAX_PER_CYCLE as _ghost_budget,
        )
    except Exception:
        _ghost_flag, _ghost_age_min, _ghost_budget = True, 5, 5

    if _ghost_flag:
        packs_gd = parsed.get("courier_packs") or {}
        if packs_gd:
            # Reverse {cid: nick} dla lookup; reuse ambiguity detect z V3.15
            try:
                import json as _json_gd
                with open("/root/.openclaw/workspace/dispatch_state/kurier_ids.json") as _f_gd:
                    _kids_gd = _json_gd.load(_f_gd)
                _cid_to_nick = {}
                _name_counts = {}
                for _nm, _cid in _kids_gd.items():
                    _nm_key = (_nm or "").strip()
                    if not _nm_key:
                        continue
                    _name_counts[_nm_key] = _name_counts.get(_nm_key, 0) + 1
                    _cid_to_nick[str(_cid)] = _nm_key
                _ambiguous_gd = {n for n, c in _name_counts.items() if c > 1}
            except Exception as _e_gd:
                _log.warning(f"V3.20 ghost detect: kurier_ids load fail: {_e_gd}")
                _cid_to_nick = {}
                _ambiguous_gd = set()

            # Sety orderów per-nick dla O(1) membership check
            _packs_oids_by_nick = {
                (n or "").strip(): {str(x) for x in (v or [])}
                for n, v in packs_gd.items()
            }

            _ghost_checked = 0
            _ghost_confirmed = 0
            _now_utc_gd = datetime.fromisoformat(now_iso().replace("Z", "+00:00"))
            if _now_utc_gd.tzinfo is None:
                from datetime import timezone as _tz_gd
                _now_utc_gd = _now_utc_gd.replace(tzinfo=_tz_gd.utc)

            for _oid, _sorder in list(current_state.items()):
                if _ghost_checked >= _ghost_budget:
                    break
                _state_status = _sorder.get("status")
                if _state_status not in ("assigned", "picked_up"):
                    continue
                _state_cid = str(_sorder.get("courier_id") or "")
                if not _state_cid or _state_cid == str(KOORDYNATOR_ID):
                    continue
                # age guard — avoid race z freshly-assigned
                _assigned_at_raw = _sorder.get("assigned_at") or _sorder.get("updated_at")
                if _assigned_at_raw:
                    try:
                        _assigned_dt = datetime.fromisoformat(
                            str(_assigned_at_raw).replace("Z", "+00:00"))
                        if _assigned_dt.tzinfo is None:
                            from datetime import timezone as _tz_gd2
                            _assigned_dt = _assigned_dt.replace(tzinfo=_tz_gd2.utc)
                        _age_min = (_now_utc_gd - _assigned_dt).total_seconds() / 60.0
                        if _age_min < _ghost_age_min:
                            continue
                    except Exception:
                        pass  # defensive — if parse fail, proceed (conservative)
                _nick_gd = _cid_to_nick.get(_state_cid)
                if not _nick_gd or _nick_gd in _ambiguous_gd:
                    continue  # unknown cid or ambiguous nick
                _nick_packs = _packs_oids_by_nick.get(_nick_gd)
                if _nick_packs is None:
                    continue  # kurier off-shift / brak w panelu — nie ghost
                if str(_oid) in _nick_packs:
                    continue  # order wciąż widoczny w bag panelu — NOT ghost
                # Kandydat na ghost: state says active, packs says gone
                try:
                    _raw_gd = fetch_order_details(str(_oid), csrf)
                    stats["fetched_details"] += 1
                    _ghost_checked += 1
                except Exception as _fe_gd:
                    _log.warning(f"V3.20 ghost fetch({_oid}): {_fe_gd}")
                    stats["errors"] += 1
                    continue
                if not _raw_gd:
                    continue
                _sid_gd = _raw_gd.get("id_status_zamowienia")
                if _sid_gd != 7:
                    continue  # not delivered — maybe returned/cancelled, let reconcile handle
                _deliv_addr_gd = parsed.get("delivery_addresses", {}).get(str(_oid)) \
                    or _sorder.get("delivery_address")
                _ev_gd = emit(
                    "COURIER_DELIVERED",
                    order_id=str(_oid),
                    courier_id=_state_cid,
                    payload={
                        "timestamp": _raw_gd.get("czas_doreczenia") or now_iso(),
                        "final_location": _deliv_addr_gd,
                        "delivery_address": _deliv_addr_gd,
                        "source": "packs_ghost_detect",
                    },
                    event_id=f"{_oid}_COURIER_DELIVERED_packs_ghost",
                )
                if _ev_gd:
                    stats["delivered"] += 1
                    _ghost_confirmed += 1
                    update_from_event({
                        "event_type": "COURIER_DELIVERED",
                        "order_id": str(_oid),
                        "courier_id": _state_cid,
                        "payload": {
                            "timestamp": _raw_gd.get("czas_doreczenia"),
                            "final_location": _deliv_addr_gd,
                            "delivery_address": _deliv_addr_gd,
                        },
                    })
                    _log.info(
                        f"V3.20 PACKS_GHOST oid={_oid} cid={_state_cid} "
                        f"nick={_nick_gd!r} (zniknął z packs, panel status=7)"
                    )
                    _advance_plan_on_deliver(
                        _state_cid, str(_oid),
                        _raw_gd.get("czas_doreczenia"),
                        _sorder.get("delivery_coords"),
                    )
            if _ghost_confirmed:
                stats["packs_ghost_detect"] = _ghost_confirmed
    # ================== END V3.20 PACKS GHOST DETECT ==================

    # ================== RECONCILE STATUS ==================
    # Dla orderow ktore state widzi jako assigned/picked_up, a panel widzi jako closed
    # (bez data-idkurier w bloku HTML = status 7/8/9) - fetch details i emit event.
    # Budzet 10 fetchow na cykl (10 * 200ms = 2s) zeby nie wysycic panelu.
    closed = parsed.get("closed_ids", set())
    # MAX_REASSIGN_PER_CYCLE i reassign_checked przeniesione na początek
    # pętli (V3.15 pre-req). Dead code usunięty tutaj.
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
                _advance_plan_on_deliver(
                    kid, zid,
                    raw.get("czas_doreczenia"),
                    sorder.get("delivery_coords"),
                )
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
                _remove_stops_on_return(
                    str(sorder.get("courier_id") or ""),
                    zid,
                )
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
                _update_plan_on_picked_up(kid, zid, dzien_odbioru)
    # ================== END PICKED_UP RECONCILE ==================

    # ================== V3.19g1 czas_kuriera DETECTION ==================
    # Detect czas_kuriera changes for already-assigned orders. Emits
    # CZAS_KURIERA_UPDATED events so state_machine re-persists fresh ck and
    # subsequent scoring reads fresh pickup_ready_at via bag_raw rebuild.
    # Flag-gated — no cost when False.
    try:
        from dispatch_v2.common import ENABLE_V319G_CK_DETECTION
    except Exception:
        ENABLE_V319G_CK_DETECTION = False
    if ENABLE_V319G_CK_DETECTION:
        for zid, state_order in list(current_state.items()):
            if state_order.get("status") not in ("assigned", "picked_up"):
                continue
            if zid not in html_order_ids:
                continue  # terminal or vanished — skip
            try:
                raw_ck = fetch_order_details(zid, csrf)
                stats["fetched_details"] = stats.get("fetched_details", 0) + 1
            except Exception as e:
                _log.debug(f"v319g1 fetch fail zid={zid}: {e}")
                continue
            if not raw_ck:
                continue
            try:
                # V3.19g1 hotfix: uses GLOBAL normalize_order (line 35).
                # Previously had `from dispatch_v2.panel_client import normalize_order`
                # here → Python marked normalize_order as LOCAL for whole _diff_and_emit
                # function, shadowing global used earlier (line 423) → UnboundLocalError
                # on every tick → 25-min crash loop 2026-04-21.
                norm_ck = normalize_order(raw_ck) or {}
            except Exception as e:
                _log.debug(f"v319g1 normalize fail zid={zid}: {e}")
                continue
            fresh_snippet = {
                "czas_kuriera_warsaw": norm_ck.get("czas_kuriera_warsaw"),
                "czas_kuriera_hhmm": norm_ck.get("czas_kuriera_hhmm"),
                "id_kurier": raw_ck.get("id_kurier"),
            }
            evt = _diff_czas_kuriera(state_order, fresh_snippet, oid=zid)
            if evt is None:
                continue
            # V3.27.1 BUG-1: event_id suffix dispatch — first_acceptance używa _FIRST_ACK
            # dla łatwego grep, value→value zachowuje delta-based suffix.
            suffix = evt.get("event_id_suffix")
            if suffix:
                event_id_str = f"{zid}_CZAS_KURIERA_UPDATED{suffix}"
            else:
                event_id_str = f"{zid}_CZAS_KURIERA_UPDATED_{int(evt['payload'].get('delta_min',0)*10)}"
            emit(
                "CZAS_KURIERA_UPDATED",
                order_id=zid,
                courier_id=str(state_order.get("courier_id") or ""),
                payload=evt["payload"],
                event_id=event_id_str,
            )
            update_from_event(evt)
            delta_val = evt["payload"].get("delta_min")
            delta_str = f"Δ={delta_val:+.1f}min" if delta_val is not None else "Δ=null(first_ack)"
            _log.info(
                f"V3.19g1 oid={zid} ck "
                f"{evt['payload'].get('old_ck_hhmm')}→{evt['payload'].get('new_ck_hhmm')} "
                f"{delta_str}"
            )
    # ================== END V3.19g1 ==================

    return stats


def tick(cycle_num: int) -> Tuple[dict, Optional[dict]]:
    """Jeden cykl watchera. Zwraca (statystyki, parsed_dict_or_None).

    V3.28 Layer 2+3: parsed zachowane dla parser_health.record_tick_full().
    """
    global _fail_count, _last_panel_unreachable_emit

    cycle_stats = {"cycle": cycle_num, "at": now_iso()}
    cycle_parsed: Optional[dict] = None

    try:
        html = fetch_panel_html()
        parsed = parse_panel_html(html)
        cycle_stats["orders_in_panel"] = len(parsed["order_ids"])
        cycle_parsed = parsed

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

    return cycle_stats, cycle_parsed


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

    # V3.28 PARSER-RESILIENCE Layer 2+3: lazy-init parser health monitor.
    # Default enabled (ENABLE_PARSER_HEALTH_MONITOR=1).
    # NIE crash panel_watcher gdy init fail — defense-in-depth.
    try:
        _parser_health = get_parser_health_monitor()
        try:
            install_layer3(_parser_health)
            _log.info("V3.28 Layer 3 cross-validation installed")
        except Exception as _l3e:
            _log.warning(f"V3.28 Layer 3 install failed (non-blocking): {_l3e}")
        _log.info(f"V3.28 parser_health monitor active (enabled={_parser_health.enabled})")
    except Exception as _ph_e:
        _log.warning(f"V3.28 parser_health init failed (non-blocking): {_ph_e}")
        _parser_health = None

    # V3.28 Layer 4: spawn health endpoint daemon thread (default ON).
    try:
        endpoint_started = start_health_endpoint()
        if endpoint_started:
            _log.info("V3.28 Layer 4 health endpoint started (http://127.0.0.1:8888/health/parser)")
    except Exception as _he_e:
        _log.warning(f"V3.28 Layer 4 health endpoint start failed (non-blocking): {_he_e}")

    # V3.27.7 TECH_DEBT #20: spawn bg refresh thread post health check
    try:
        from dispatch_v2 import panel_client as _pc
        _pc.start_bg_refresh()
        _log.info("V3.27.7 panel_bg_refresh thread started post health check")
    except Exception as _bg_e:
        _log.warning(f"V3.27.7 panel_bg_refresh start failed: {type(_bg_e).__name__}: {_bg_e}")

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
        stats, parsed = tick(cycle)
        elapsed = time.time() - t0

        # Zbieramy totals
        for k in totals:
            totals[k] += stats.get(k, 0)

        # V3.28 Layer 2+3: parser anomaly detection per tick.
        # record_tick_full łączy Layer 2 (quantity-based) + Layer 3 (set-based cross-validation).
        # NIGDY raise — wewnątrz wrapped try/except, NIE crash panel_watcher.
        if _parser_health is not None:
            try:
                record_tick_full(_parser_health, stats, parsed)
            except Exception as _ph_re:
                _log.warning(f"V3.28 parser_health.record_tick fail (non-blocking): {_ph_re}")

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
