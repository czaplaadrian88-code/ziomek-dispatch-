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
import json
import os
import signal
import sys
import time
from datetime import datetime, timezone
from typing import Dict, Optional, Tuple

from dispatch_v2.common import (
    FIRMOWE_KONTO_ADDRESS_IDS,
    FIRMOWE_KONTO_FALLBACK_COORDS,
    flag,
    load_config,
    now_iso,
    setup_logger,
)
from dispatch_v2.core.broadcast_handlers import dispatch_config_reload
from dispatch_v2.core.config_reload_subscriber import BroadcastSubscriber
from dispatch_v2.event_bus import emit, emit_audit
from dispatch_v2.parser_health import get_monitor as get_parser_health_monitor
from dispatch_v2.parser_health_layer3 import install_layer3, record_tick_full
from dispatch_v2.parser_health_endpoint import start_health_endpoint
from dispatch_v2.uwagi_address_parser import parse_pickup_from_uwagi
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
# tech-debt #24: cold-start packs scan one-shot post-restart. Eliminuje
# MISSING_FROM_STATE phantoms gdy panel-watcher restart in-peak drops
# COURIER_ASSIGNED dla orderów mid-way ASSIGN→PICKUP (post-restart diff
# emit COURIER_PICKED_UP direct bez prior ASSIGNED). Scan iteruje
# parsed["courier_packs"] i emit COURIER_ASSIGNED dla każdego oid bez
# entry w orders_state lub z empty cid. Bypasses V3.15 budget (one-shot).
_cold_start_done = False
# Lookup address_id -> coords. MP-#12 (2026-05-08): mtime-based hot-reload co 15s
# eliminuje konieczność restart'u panel_watcher gdy restaurant_coords.json zmieniony
# (np. nowy add_id mapping od Adriana). META top-5 quick win, STATE_OWNERSHIP F3+F8.
_COORDS_PATH = "/root/.openclaw/workspace/dispatch_state/restaurant_coords.json"
_COORDS = {}
_COORDS_MTIME = 0.0
_COORDS_LAST_CHECK_TS = 0.0
_COORDS_CHECK_INTERVAL_S = 15.0


def _load_coords():
    global _COORDS, _COORDS_MTIME
    try:
        import json
        import os
        mtime = os.path.getmtime(_COORDS_PATH)
        with open(_COORDS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        _COORDS = {str(k): (v["lat"], v["lng"]) for k, v in data.items() if "lat" in v and "lng" in v}
        _COORDS_MTIME = mtime
    except Exception as e:
        _log.warning(f"_load_coords fail: {e}")
        _COORDS = {}
        _COORDS_MTIME = 0.0


def _maybe_reload_coords():
    """MP-#12: mtime check co _COORDS_CHECK_INTERVAL_S. Reload gdy plik zmieniony."""
    global _COORDS_LAST_CHECK_TS
    now = time.time()
    if now - _COORDS_LAST_CHECK_TS < _COORDS_CHECK_INTERVAL_S:
        return False
    _COORDS_LAST_CHECK_TS = now
    try:
        import os
        mtime = os.path.getmtime(_COORDS_PATH)
    except Exception as e:
        _log.warning(f"_maybe_reload_coords stat fail: {e}")
        return False
    if mtime > _COORDS_MTIME:
        prev_count = len(_COORDS)
        _load_coords()
        _log.info(
            f"_COORDS hot-reload: mtime {_COORDS_MTIME:.0f} → {mtime:.0f}, "
            f"entries {prev_count} → {len(_COORDS)} (MP-#12)"
        )
        return True
    return False


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
    # MP-#11 (2026-05-08): atomic JSONL append via core helper. Eliminuje race
    # między panel_watcher i telegram_approver pisanie do TEGO SAMEGO learning_log.
    try:
        from dispatch_v2.core.jsonl_appender import append_jsonl
        append_jsonl(_LEARNING_LOG_PATH, override_rec)
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


def _invalidate_plan_on_bag_change(order_id: str, courier_id: str) -> None:
    """BUG-1 (2026-06-05): gdy zlecenie zostaje przypisane/przepisane kurierowi, a NIE
    jest pokryte jego zapisanym planem (typowo PANEL_OVERRIDE / reassign — koordynator
    przypisał ręcznie, nie z propozycji Ziomka, więc _save_plan_on_assign cicho pomija
    zapis), unieważnij istniejący plan kuriera.

    invalidate_plan bumpuje invalidated_at → /api/courier/plan-version zmienia sygnał +
    SSE PLAN_UPDATED → apka natychmiast robi pełny GET /api/courier/orders (build_view
    zwraca cały aktualny worek), zamiast czekać do 5-min plan_recheck gap-fill.

    Cicho no-op gdy: flaga off, saved-plans off, kurier bez aktywnego planu (apka i tak
    na fallbacku pełnego worka), albo order już pokryty planem (świeży save_plan ruszył
    plan_version, sygnał jest). Błędy nie propagują do callera.
    """
    try:
        from dispatch_v2.common import ENABLE_SAVED_PLANS, flag
        if not ENABLE_SAVED_PLANS:
            return
        if not flag("ENABLE_INVALIDATE_PLAN_ON_BAG_CHANGE", True):
            return
    except Exception:
        return
    if not courier_id or not order_id:
        return
    try:
        from dispatch_v2 import plan_manager
        # load_plan zwraca None gdy brak planu LUB plan już invalidated → no-op w obu
        plan = plan_manager.load_plan(str(courier_id))
        if plan is None:
            return
        covered = {str(s.get("order_id")) for s in plan.get("stops", [])}
        if str(order_id) in covered:
            return
        plan_manager.invalidate_plan(str(courier_id), "BAG_CHANGED")
        _log.info(
            f"BUG-1 invalidate_plan_on_bag_change cid={courier_id} oid={order_id} "
            f"— order poza planem (reassign/override) → apka odświeży worek"
        )
    except Exception as e:
        _log.warning(
            f"BUG-1 invalidate_plan_on_bag_change fail cid={courier_id} oid={order_id}: {e}"
        )


def _save_plan_on_assign_signal(order_id: str, courier_id: str) -> None:
    """BUG-1 (2026-06-05): zapisz plan z propozycji (gdy to nasz kurier) ORAZ zasygnalizuj
    apce zmianę worka. _save_plan_on_assign cicho pomija zapis przy PANEL_OVERRIDE/reassign,
    więc plan_version stoi i apka nie odświeża worka aż do 5-min plan_recheck.
    _invalidate_plan_on_bag_change łapie ten przypadek (order poza planem) i unieważnia
    plan → SSE PLAN_UPDATED → natychmiastowy pełny GET. No-op gdy save pokrył order."""
    _save_plan_on_assign(order_id, courier_id)
    _invalidate_plan_on_bag_change(order_id, courier_id)
    # F3: po override/reassign (plan unieważniony lub brak) Ziomek decyduje trasę
    # NATYCHMIAST, nie po ≤5 min ticku plan_recheck. Samo-bramkujące (no-op gdy
    # ważny plan już pokrywa worek — nie nadpisuje propozycji). Best-effort, flaga
    # ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE (OFF) — błąd nigdy nie psuje diff loopu.
    try:
        from dispatch_v2 import plan_recheck
        plan_recheck.redecide_courier(courier_id)
    except Exception as e:
        _log.warning(f"F3 redecide signal fail cid={courier_id} oid={order_id}: {e}")


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


def _diff_pickup_time(old_state: dict, fresh_response: dict,
                      oid: str) -> Optional[dict]:
    """Detect pickup_at_warsaw change (restaurant-declared pickup time).

    Root cause oid 474577 (2026-05-19): koordynator zmienił czas odbioru
    czasówki trzymanej przez Koordynatora na życzenie restauracji.
    pickup_at_warsaw zapisywany RAZ w NEW_ORDER, nigdy nie odświeżany dla
    status=planned → czasowka_scheduler._minutes_to_pickup liczył T-minus
    od starego czasu (FORCE_ASSIGN spam ~3h za wcześnie).

    pickup_at_warsaw pochodzi z panelowego czas_odbioru_timestamp —
    OSOBNE pole niż czas_kuriera (które pokrywa V3.19g1). Oba mogą się
    rozjechać, więc potrzebna niezależna detekcja.

    Returns None gdy: brak zmiany, poniżej progu, null→null,
    value→null (panel revert — warn + skip).
    Returns event dict gdy |Δt| >= PICKUP_TIME_DELTA_THRESHOLD_MIN
    lub null→value (late-arriving panel field).
    """
    from dispatch_v2.common import PICKUP_TIME_DELTA_THRESHOLD_MIN

    old_state = old_state or {}
    fresh_response = fresh_response or {}

    old_iso = old_state.get("pickup_at_warsaw")
    new_iso = fresh_response.get("pickup_at_warsaw")

    # null→null — brak danych po obu stronach.
    if not old_iso and not new_iso:
        return None
    # value→null — panel revert; nie nadpisuj realnej wartości None'em.
    if old_iso and not new_iso:
        _log.warning(f"pickup_time oid={oid} change_to_null old={old_iso}")
        return None

    delta_min: Optional[float] = None
    event_id_suffix: Optional[str] = None
    if not old_iso and new_iso:
        # null→value — panel dostarczył pickup_at_warsaw późno (rzadkie:
        # NEW_ORDER zwykle je ma). Traktuj jak update, brak baseline delty.
        event_id_suffix = "_LATE"
    else:
        # value→value — policz signed deltę.
        try:
            old_dt = datetime.fromisoformat(old_iso)
            new_dt = datetime.fromisoformat(new_iso)
        except (ValueError, TypeError) as e:
            _log.warning(f"pickup_time oid={oid} iso parse fail: {e}")
            return None
        delta_min = (new_dt - old_dt).total_seconds() / 60.0
        if abs(delta_min) < PICKUP_TIME_DELTA_THRESHOLD_MIN:
            return None  # noise floor

    payload = {
        "oid": oid,
        "courier_id": old_state.get("courier_id"),
        "old_pickup_at_warsaw": old_iso,
        "new_pickup_at_warsaw": new_iso,
        "old_prep_minutes": old_state.get("prep_minutes"),
        "new_prep_minutes": fresh_response.get("prep_minutes"),
        "new_decision_deadline": fresh_response.get("decision_deadline"),
        "new_zmiana_czasu_odbioru": fresh_response.get("zmiana_czasu_odbioru"),
        "delta_min": round(delta_min, 2) if delta_min is not None else None,
        "source": "panel_re_check",
    }
    evt = {
        "event_type": "PICKUP_TIME_UPDATED",
        "order_id": oid,
        "courier_id": old_state.get("courier_id"),
        "payload": payload,
    }
    if event_id_suffix:
        evt["event_id_suffix"] = event_id_suffix
    return evt


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

    # PARSE-01 (audyt 2026-06-03): straż ciągłości parse PRZED emisją NOWYCH.
    # Gdy aktywne (order_ids - closed_ids) nagle spadną do 0 (a wcześniej było
    # >0) lub o >= PARSE_DROP_PCT — to wzorzec zerwanego parse (HTTP 200 + pusty
    # wynik), nie 'brak zamówień'. Shadow-first: flaga OFF => guard tylko loguje
    # 'ZABLOKOWALBYM', _freeze_new pozostaje False. Flaga ON + potwierdzone =>
    # _freeze_new=True (pomijamy emisję NEW_ORDER, ZOSTAWIAMy detekcję terminalną
    # disappeared/delivered niżej) + PARSER_DEGRADED=true. Defense: NIGDY nie
    # wywraca tick() — guard.evaluate ma własne try/except i zwraca no-trip.
    _freeze_new = False
    try:
        from dispatch_v2 import parse_continuity_guard as _pcg
        _n_state_active = sum(
            1 for _so in current_state.values()
            if _so.get("status") not in ("delivered", "returned_to_pool", "cancelled")
        )
        _guard = _pcg.evaluate(
            parsed.get("order_ids"),
            parsed.get("closed_ids"),
            n_state_active=_n_state_active,
        )
        _freeze_new = bool(_guard.get("freeze_new"))
        stats["parse_guard_suspicious"] = int(bool(_guard.get("suspicious")))
        stats["parse_guard_freeze_new"] = int(_freeze_new)
    except Exception as _pcg_e:
        _log.warning(f"PARSE-01 guard fail (non-blocking, no-freeze): {_pcg_e}")
        _freeze_new = False

    # 1. NOWE: ID widoczne w HTML ale nieznane w state.
    # PARSE-01: gdy _freeze_new => iterujemy po pustej liście (zero emisji NEW),
    # reszta _diff_and_emit (sekcja 2 — zmiany/terminalne) działa normalnie.
    _new_scan_ids = [] if _freeze_new else parsed["order_ids"]
    for zid in _new_scan_ids:
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

        # Firmowe konto path: address_id ∈ FIRMOWE_KONTO_ADDRESS_IDS znaczy
        # adres pickup'u jest w polu uwagi (free-text), nie w panel address.
        # Parser PRIMARY: parsuj uwagi → geocode → real coords (Mickiewicza 50,
        # Wyszyńskiego 2/75, etc.). FALLBACK: gdy parser zwróci None (P3 edge)
        # ALBO geocode fail → użyj FIRMOWE_KONTO_FALLBACK_COORDS (centrala
        # Nadajesz.pl, Adrian decision 2026-05-07). Defense gate L2 w
        # dispatch_pipeline + L4 czasowka_scheduler obsługują gdy nawet fallback
        # coords nie zostały wpisane (np. inne firmowe konta bez fallback config).
        _uwagi_pickup_parsed = None
        _pickup_address_override = None
        _restaurant_override = None
        _is_firmowe_konto = (
            _aid is not None
            and int(_aid) in FIRMOWE_KONTO_ADDRESS_IDS
        )
        if (_pcoords is None
                and _is_firmowe_konto
                and flag("ENABLE_UWAGI_ADDRESS_PARSER", True)):
            _uwagi_text = norm.get("uwagi")
            _parsed = parse_pickup_from_uwagi(_uwagi_text)
            if _parsed is not None:
                _pickup_address_override = f"{_parsed.street} {_parsed.number}"
                _pcoords = geocode(_pickup_address_override, city="Białystok", timeout=2.0)
                if _pcoords is None:
                    _log.warning(
                        f"NEW_ORDER {zid} firmowe-konto aid={_aid}: parser "
                        f"OK ({_pickup_address_override!r} conf={_parsed.confidence}) "
                        f"ALE geocode fail — fallback do FIRMOWE_KONTO_FALLBACK_COORDS"
                    )
                    _pcoords = tuple(FIRMOWE_KONTO_FALLBACK_COORDS)
                else:
                    _log.info(
                        f"NEW_ORDER {zid} firmowe-konto aid={_aid}: uwagi-parser "
                        f"resolved pickup {_pickup_address_override!r} conf={_parsed.confidence} "
                        f"→ coords={_pcoords}"
                    )
                if _parsed.company:
                    _restaurant_override = _parsed.company
                _uwagi_pickup_parsed = {
                    "street": _parsed.street,
                    "number": _parsed.number,
                    "company": _parsed.company,
                    "confidence": _parsed.confidence,
                    "raw_pickup_line": _parsed.raw_pickup_line,
                }
            else:
                # P3 edge: parser nie wyciągnął adresu (np. uwagi=company-only
                # "MALI WOJOWNICY"). Fallback do hardcoded coords centrali —
                # kurier dostaje real feasibility loop zamiast manual KOORD.
                _log.info(
                    f"NEW_ORDER {zid} firmowe-konto aid={_aid}: parser zwrócił "
                    f"None (P3 edge), fallback do FIRMOWE_KONTO_FALLBACK_COORDS. "
                    f"Uwagi: {_uwagi_text!r}"
                )
                _pcoords = tuple(FIRMOWE_KONTO_FALLBACK_COORDS)
                _uwagi_pickup_parsed = {
                    "street": None,
                    "number": None,
                    "company": None,
                    "confidence": 0.0,
                    "raw_pickup_line": _uwagi_text or "",
                    "fallback_coords_used": True,
                }

        # Geocode delivery address (cache hit ~90% = 0ms, miss = Google API max 2s)
        _del_addr = norm.get("delivery_address")
        _del_city = norm.get("delivery_city")
        _dcoords = None
        if _del_addr:
            _dcoords = geocode(_del_addr, city=_del_city, timeout=2.0)
            if _dcoords is None:
                _log.warning(f"NEW_ORDER {zid}: geocode fail for '{_del_addr}' city={_del_city!r}")

        ev_payload = {
            "restaurant": _restaurant_override or norm["restaurant"],
            "pickup_address": _pickup_address_override or norm["pickup_address"],
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
            # Audit trail dla firmowego konto path (zwykle None).
            "uwagi": norm.get("uwagi"),
            "uwagi_pickup_parsed": _uwagi_pickup_parsed,
            # Tech debt #19a/b/c (2026-05-07) — fields tracone od V3.x:
            # - decision_deadline: SLA visibility (panel deadline na decyzję koord)
            # - zmiana_czasu_odbioru: audit flag czy panel zmienił pickup time
            # - created_at_utc: single anchor dla downstream age_minutes consumers
            "decision_deadline": norm.get("decision_deadline"),
            "zmiana_czasu_odbioru": norm.get("zmiana_czasu_odbioru"),
            "created_at_utc": norm.get("created_at_utc"),
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

            # TASK 4 (2026-05-04): Auto-KOORD on NEW_ORDER dla czasówek.
            # Defensive: try/except, NIGDY crash panel_watcher. Flag-gated default False.
            try:
                from dispatch_v2 import auto_koord, common as _C
                if _C.flag("AUTO_KOORD_ON_NEW_ORDER_ENABLED", default=False):
                    decision, reason = auto_koord.needs_auto_koord(raw, flag_enabled=True)
                    if decision:
                        _log.info(f"AUTO_KOORD trigger oid={zid} reason={reason}")
                        ak_result = auto_koord.perform_auto_koord(
                            order_id=zid,
                            fetch_details_fn=lambda z: fetch_order_details(z, csrf),
                        )
                        auto_koord.emit_event_log(zid, norm, ak_result)
                        if _C.flag("AUTO_KOORD_TELEGRAM_INFO_ENABLED", default=False):
                            msg = auto_koord.make_telegram_info_message(norm, ak_result)
                            auto_koord.send_telegram_info(msg)
                        if ak_result.get("success") or ak_result.get("skipped"):
                            stats["auto_koord_handled"] = stats.get("auto_koord_handled", 0) + 1
                        else:
                            stats["auto_koord_failed"] = stats.get("auto_koord_failed", 0) + 1
                    else:
                        _log.debug(f"AUTO_KOORD skip oid={zid} reason={reason}")
            except Exception as _ake:
                _log.warning(f"AUTO_KOORD hook fail oid={zid} (non-blocking): {type(_ake).__name__}: {_ake}")

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
            assigned_event = emit_audit(
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
                _save_plan_on_assign_signal(zid, courier_id)

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
                        # Doreczone (F10 2026-05-09: canonical event_id eliminuje
                        # duplicate audit_log entries vs ghost_detect/reconcile path).
                        ev = emit(
                            "COURIER_DELIVERED",
                            order_id=zid,
                            courier_id=str(raw.get("id_kurier") or ""),
                            payload={
                                "timestamp": raw.get("czas_doreczenia") or now_iso(),
                                "final_location": state_order.get("delivery_address"),
                                "deliv_source": "panel",
                            },
                            event_id=f"{zid}_COURIER_DELIVERED_canonical",
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
                        ev = emit_audit(
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
                    ev = emit_audit(
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
                        _save_plan_on_assign_signal(zid, courier_id)
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
                    ev = emit_audit(
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
                        _save_plan_on_assign_signal(zid, panel_courier)
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
                    _ev = emit_audit(
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
                        _save_plan_on_assign_signal(_oid_str, _target_cid)
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
                        "deliv_source": "packs_ghost_detect",
                    },
                    event_id=f"{_oid}_COURIER_DELIVERED_canonical",
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
                    "deliv_source": "reconcile",
                },
                event_id=f"{zid}_COURIER_DELIVERED_canonical",
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
            ev = emit_audit(
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

    # ============ ORDER-TIME RE-CHECK (czas_kuriera + pickup) ============
    # Re-czytaj panelowe pola czasu dla NIE-terminalnych zleceń i emituj
    # update gdy się zmieniły. Dwie niezależne detekcje na jednym fetchu:
    #
    #  V3.19g1 CZAS_KURIERA_UPDATED — panelowe czas_kuriera (HH:MM, declared
    #    courier arrival). Pokrywało historycznie tylko assigned/picked_up.
    #
    #  PICKUP_TIME_UPDATED (oid 474577 root cause, 2026-05-19) — panelowe
    #    czas_odbioru_timestamp → pickup_at_warsaw (czas odbioru z restauracji).
    #    pickup_at_warsaw zapisywany RAZ w NEW_ORDER, nigdy nie odświeżany dla
    #    status=planned. Czasówka żyje większość czasu jako planned w buckecie
    #    Koordynatora — koordynator zmieniał czas na życzenie restauracji,
    #    czasowka_scheduler czytał stary pickup_at_warsaw.
    #
    # SCOPE: assigned/picked_up (dowolny typ) + planned CZASÓWKI. Czasówki
    # planned są nieliczne i długowieczne (godziny w Koordynatorze) — koszt
    # fetchu ograniczony. Elastyki planned rozwiązują się w minuty (krótkie
    # okno ryzyka) i są liczne → pominięte dla kosztu; po assign trafiają w
    # scope assigned. Każda detekcja osobno flag-gated — zero kosztu gdy obie
    # False.
    try:
        from dispatch_v2.common import (
            ENABLE_V319G_CK_DETECTION, ENABLE_PICKUP_TIME_DETECTION)
    except Exception:
        ENABLE_V319G_CK_DETECTION = False
        ENABLE_PICKUP_TIME_DETECTION = False
    if ENABLE_V319G_CK_DETECTION or ENABLE_PICKUP_TIME_DETECTION:
        for zid, state_order in list(current_state.items()):
            _status = state_order.get("status")
            _is_czasowka = (
                state_order.get("order_type") == "czasowka"
                or (state_order.get("prep_minutes") or 0) >= 60
            )
            in_scope = (
                _status in ("assigned", "picked_up")
                or (_status == "planned" and _is_czasowka)
            )
            if not in_scope:
                continue
            if zid not in html_order_ids:
                continue  # terminal or vanished — skip
            try:
                raw_ck = fetch_order_details(zid, csrf)
                stats["fetched_details"] = stats.get("fetched_details", 0) + 1
            except Exception as e:
                _log.debug(f"order-time fetch fail zid={zid}: {e}")
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
                _log.debug(f"order-time normalize fail zid={zid}: {e}")
                continue

            # ---- Detekcja A: czas_kuriera (V3.19g1) ----
            if ENABLE_V319G_CK_DETECTION:
                fresh_snippet = {
                    "czas_kuriera_warsaw": norm_ck.get("czas_kuriera_warsaw"),
                    "czas_kuriera_hhmm": norm_ck.get("czas_kuriera_hhmm"),
                    "id_kurier": raw_ck.get("id_kurier"),
                }
                evt = _diff_czas_kuriera(state_order, fresh_snippet, oid=zid)
                if evt is not None:
                    # V3.27.1 BUG-1: event_id suffix dispatch — first_acceptance
                    # używa _FIRST_ACK dla łatwego grep, value→value zachowuje
                    # delta-based suffix.
                    suffix = evt.get("event_id_suffix")
                    if suffix:
                        event_id_str = f"{zid}_CZAS_KURIERA_UPDATED{suffix}"
                    else:
                        event_id_str = f"{zid}_CZAS_KURIERA_UPDATED_{int(evt['payload'].get('delta_min',0)*10)}"
                    emit_audit(
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
                        f"{delta_str} status={_status}"
                    )

            # ---- Detekcja B: pickup_at_warsaw (PICKUP_TIME_UPDATED) ----
            if ENABLE_PICKUP_TIME_DETECTION:
                pickup_snippet = {
                    "pickup_at_warsaw": norm_ck.get("pickup_at_warsaw"),
                    "prep_minutes": norm_ck.get("prep_minutes"),
                    "decision_deadline": norm_ck.get("decision_deadline"),
                    "zmiana_czasu_odbioru": norm_ck.get("zmiana_czasu_odbioru"),
                }
                evt_p = _diff_pickup_time(state_order, pickup_snippet, oid=zid)
                if evt_p is not None:
                    p_suffix = evt_p.get("event_id_suffix")
                    if p_suffix:
                        p_event_id = f"{zid}_PICKUP_TIME_UPDATED{p_suffix}"
                    else:
                        p_event_id = f"{zid}_PICKUP_TIME_UPDATED_{int(evt_p['payload'].get('delta_min',0)*10)}"
                    emit_audit(
                        "PICKUP_TIME_UPDATED",
                        order_id=zid,
                        courier_id=str(state_order.get("courier_id") or ""),
                        payload=evt_p["payload"],
                        event_id=p_event_id,
                    )
                    update_from_event(evt_p)
                    p_delta = evt_p["payload"].get("delta_min")
                    p_delta_str = f"Δ={p_delta:+.1f}min" if p_delta is not None else "Δ=null(late)"
                    _log.info(
                        f"PICKUP_TIME_UPDATED oid={zid} pickup "
                        f"{evt_p['payload'].get('old_pickup_at_warsaw')}→"
                        f"{evt_p['payload'].get('new_pickup_at_warsaw')} "
                        f"{p_delta_str} status={_status}"
                    )
    # ================== END ORDER-TIME RE-CHECK ==================

    return stats


def _post_restart_cold_start_scan(parsed: dict, csrf: str) -> dict:
    """tech-debt #24: one-shot scan post-restart żeby naprawić missing
    COURIER_ASSIGNED dla orderów mid-way ASSIGN→PICKUP w restart window.

    Iteruje parsed["courier_packs"][nick] → oids. Dla każdego oid bez
    entry w orders_state (state_cid=="") emit COURIER_ASSIGNED z
    source="cold_start_scan". Bypass V3.15 budget (one-shot, expected
    5-30 mismatches po panel-watcher restart in-peak).

    Idempotent: emit_audit z deterministic event_id, drugi call no-op.
    Defense-in-depth: kurier_ids load fail → skip (warn), per-oid
    fetch fail → skip+counter, ambiguous nick → skip+warn.
    """
    stats = {"cold_start_scanned": 0, "cold_start_emitted": 0, "cold_start_errors": 0}
    packs = parsed.get("courier_packs") or {}
    if not packs:
        return stats

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
        _log.warning(f"cold_start_scan: kurier_ids load fail: {_e}")
        return stats

    current_state = state_get_all()

    for _nick, _oids in packs.items():
        _nick_key = (_nick or "").strip()
        if not _nick_key or not _oids:
            continue
        if _nick_key in _ambiguous_names:
            _log.warning(f"cold_start_scan: skip ambiguous nick {_nick_key!r}")
            continue
        _target_cid = _name_to_cid.get(_nick_key)
        if not _target_cid:
            continue
        for _oid in _oids:
            _oid_str = str(_oid)
            _sorder = current_state.get(_oid_str) or {}
            _state_cid = str(_sorder.get("courier_id") or "")
            # Cold-start fires ONLY gdy state nie ma cid (missing entry
            # lub courier_id=None). Jeśli state ma cid (nawet stary)
            # → V3.15 packs_fallback handle mismatch w normalnym diff.
            if _state_cid:
                continue
            _state_status = _sorder.get("status")
            if _state_status in ("delivered", "returned_to_pool", "cancelled"):
                continue
            try:
                _raw = fetch_order_details(_oid_str, csrf)
                stats["cold_start_scanned"] += 1
            except Exception as _fe:
                _log.warning(f"cold_start_scan fetch({_oid_str}): {_fe}")
                stats["cold_start_errors"] += 1
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
                    f"cold_start_scan: nick={_nick_key!r} map→{_target_cid} "
                    f"but raw id_kurier={_panel_cid} for oid={_oid_str} — trust raw"
                )
                _target_cid = _panel_cid
            _ev = emit_audit(
                "COURIER_ASSIGNED",
                order_id=_oid_str,
                courier_id=_target_cid,
                payload={
                    "source": "cold_start_scan",
                    "nick": _nick_key,
                },
                event_id=f"{_oid_str}_COURIER_ASSIGNED_{_target_cid}_coldstart",
            )
            if _ev:
                update_from_event({
                    "event_type": "COURIER_ASSIGNED",
                    "order_id": _oid_str,
                    "courier_id": _target_cid,
                    "payload": {"source": "cold_start_scan"},
                })
                stats["cold_start_emitted"] += 1
                _log.info(
                    f"COLD_START_CATCHUP {_oid_str} → cid={_target_cid} "
                    f"nick={_nick_key!r} sid={_sid}"
                )
                _save_plan_on_assign_signal(_oid_str, _target_cid)
    return stats


def _should_skip_empty_packs_write(
    new_packs: dict,
    prev_cache: Optional[dict],
    max_age_s: float,
    now_utc: datetime,
) -> Tuple[bool, Optional[float], int]:
    """FAIL-09 / PACKS-01 (2026-06-06) — czysta decyzja: czy POMINĄĆ zapis pustego
    panel_packs_cache, bo poprzedni był świeży i niepusty (prawdopodobnie zdegradowany
    parse, np. HTTP 200 login-page).

    Zwraca (skip, prev_age_s, n_prev_packs).
    Zasady (konserwatywne — pomiń tylko gdy pewne, że to regresja, nie realne zero):
      • new_packs niepuste → NIGDY nie pomijaj (zapis realnych danych).
      • brak/uszkodzony poprzedni cache → nie pomijaj (write-through; reader i tak ma TTL).
      • poprzedni cache pusty → nie pomijaj (zero→zero, nic nie tracimy).
      • poprzedni nieczytelny ts lub starszy niż max_age_s → nie pomijaj (i tak by się
        zestarzał u czytnika — write-through prościej).
      • poprzedni niepusty ORAZ świeży (age ≤ max_age_s) → POMIŃ (zachowaj last-good).
    """
    if new_packs:
        return False, None, 0
    if not isinstance(prev_cache, dict):
        return False, None, 0
    prev_packs = prev_cache.get("packs") or {}
    if not prev_packs:
        return False, None, len(prev_packs)
    prev_age: Optional[float] = None
    prev_ts = prev_cache.get("ts")
    if prev_ts:
        try:
            prev_age = (
                now_utc - datetime.fromisoformat(str(prev_ts).replace("Z", "+00:00"))
            ).total_seconds()
        except Exception:
            prev_age = None
    if prev_age is None or prev_age > max_age_s:
        return False, prev_age, len(prev_packs)
    return True, prev_age, len(prev_packs)


def tick(cycle_num: int) -> Tuple[dict, Optional[dict]]:
    """Jeden cykl watchera. Zwraca (statystyki, parsed_dict_or_None).

    V3.28 Layer 2+3: parsed zachowane dla parser_health.record_tick_full().
    """
    global _fail_count, _last_panel_unreachable_emit, _cold_start_done

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

        # V3.28 P3 (B) — atomic write panel_packs_cache.json dla shadow_dispatcher
        # per-proposal lookup. Pozwala wykryć state-vs-panel divergence (state ma
        # cid=None / bag=[], ale panel widzi nick→[oids] = kurier faktycznie wozi).
        # Kontekst: 472242 Baanko 17:41 — Mateusz O proposed jako bag=0 mimo 7 queued
        # w panelu (PACKS_CATCHUP lag 11s). Cache pozwala shadow_dispatcher zobaczyć
        # ground truth panel niezależnie od panel_watcher tick rate.
        try:
            import tempfile as _tempfile
            from dispatch_v2 import common as _C
            _packs_cache_path = "/root/.openclaw/workspace/dispatch_state/panel_packs_cache.json"
            _new_packs = parsed.get("courier_packs") or {}
            # FAIL-09 / PACKS-01 guard (2026-06-06): nie nadpisuj ŚWIEŻEGO niepustego
            # cache pustką. Pusty parse (HTTP 200 login-page / zmiana layoutu) zwraca
            # courier_packs={} mimo że chwilę temu panel widział kurierów z workami →
            # konsumenci packs (courier_resolver._load_panel_packs_cache,
            # state_panel_monitor) straciliby ground-truth na okno degradacji → kurier
            # z workiem widziany jako wolny (wzorzec V3.13-15). Zachowaj poprzedni cache;
            # jeśli degradacja trwa, zestarzeje się wg TTL czytnika
            # (PANEL_PACKS_CACHE_MAX_AGE_S=120s) → naturalny fail-safe, brak stale-forever.
            # Tylko EMPTY (wysoka precyzja); partial-drop = domena PARSE-01.
            # Kill-switch: ENABLE_PANEL_PACKS_EMPTY_WRITE_GUARD=false (flags.json hot-reload).
            _skip_packs_write = False
            if (not _new_packs) and _C.flag(
                    "ENABLE_PANEL_PACKS_EMPTY_WRITE_GUARD", default=True):
                try:
                    with open(_packs_cache_path, encoding="utf-8") as _pf:
                        _prev = json.load(_pf)
                    _guard_max_age = float(
                        _C.flag("PANEL_PACKS_EMPTY_GUARD_MAX_PREV_AGE_S", default=180.0))
                    _skip_packs_write, _prev_age, _n_prev = _should_skip_empty_packs_write(
                        _new_packs, _prev, _guard_max_age, datetime.now(timezone.utc))
                    if _skip_packs_write:
                        cycle_stats["panel_packs_empty_write_skipped"] = True
                        _log.warning(
                            f"PANEL_PACKS_EMPTY_WRITE_GUARD skip: parse packs=0 a poprzedni "
                            f"cache miał {_n_prev} packs (age={_prev_age:.0f}s "
                            f"<= {_guard_max_age:.0f}s) — prawdopodobnie zdegradowany parse; "
                            f"zachowuję poprzedni cache "
                            f"(orders_in_panel={cycle_stats.get('orders_in_panel')})"
                        )
                except FileNotFoundError:
                    pass
                except Exception as _ge:
                    _log.warning(
                        f"PANEL_PACKS_EMPTY_WRITE_GUARD read prev fail (write through): {_ge}")
            if not _skip_packs_write:
                _packs_cache_data = {
                    "ts": now_iso(),
                    "packs": _new_packs,
                    "tick": cycle_num,
                    "orders_in_panel": cycle_stats.get("orders_in_panel"),
                }
                _fd, _tmp = _tempfile.mkstemp(
                    prefix="panel_packs_cache.",
                    suffix=".tmp",
                    dir="/root/.openclaw/workspace/dispatch_state/",
                )
                try:
                    with os.fdopen(_fd, "w", encoding="utf-8") as _fh:
                        json.dump(_packs_cache_data, _fh, ensure_ascii=False, separators=(",", ":"))
                        _fh.flush()
                        os.fsync(_fh.fileno())
                    os.replace(_tmp, _packs_cache_path)
                except Exception:
                    try: os.unlink(_tmp)
                    except Exception: pass
                    raise
        except Exception as _e:
            _log.warning(f"panel_packs_cache write fail: {_e}")

        from dispatch_v2.panel_client import _session
        csrf = _session.get("csrf") or ""

        # tech-debt #24: cold-start packs scan one-shot post-restart.
        # Wykonaj PRZED _diff_and_emit żeby state_get_all() w diff
        # widział COURIER_ASSIGNED emitowane przez scan (uniknij race
        # gdy diff emit COURIER_PICKED_UP bez prior ASSIGNED → reconcile
        # phantom MISSING_FROM_STATE 4h+ później).
        if not _cold_start_done:
            try:
                cs_stats = _post_restart_cold_start_scan(parsed, csrf)
                cycle_stats.update(cs_stats)
                _log.info(
                    f"COLD_START_SCAN done cycle={cycle_num}: "
                    f"scanned={cs_stats.get('cold_start_scanned', 0)} "
                    f"emitted={cs_stats.get('cold_start_emitted', 0)} "
                    f"errors={cs_stats.get('cold_start_errors', 0)}"
                )
            except Exception as _cs_e:
                _log.warning(f"cold_start_scan fail (non-blocking): {_cs_e}")
            _cold_start_done = True

        diff_stats = _diff_and_emit(parsed, csrf)
        cycle_stats.update(diff_stats)

    except Exception as e:
        _fail_count += 1
        cycle_stats["error"] = f"{type(e).__name__}: {e}"
        _log.error(f"tick fail #{_fail_count}: {e}")

        # Po 3 failach emit PANEL_UNREACHABLE (throttled: max 1/min)
        if _fail_count >= 3 and time.time() - _last_panel_unreachable_emit > 60:
            emit_audit(
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

    # A4.1 (2026-05-09): BroadcastSubscriber dla CONFIG_RELOAD events.
    _broadcast_sub = None
    try:
        from pathlib import Path as _Path
        _broadcast_sub = BroadcastSubscriber(
            consumer_id="panel_watcher",
            state_path=_Path(
                "/root/.openclaw/workspace/dispatch_state/event_subscribers/panel_watcher.json"
            ),
        )
        _log.info("A4.1 BroadcastSubscriber init OK consumer=panel_watcher")
    except Exception as _bs_e:
        _log.warning(
            f"A4.1 BroadcastSubscriber init fail "
            f"({type(_bs_e).__name__}: {_bs_e}) — broadcast disabled"
        )

    cycle = 0
    last_log_summary = time.time()
    last_broadcast_poll = 0.0
    BROADCAST_POLL_INTERVAL_S = 30.0
    totals = {"new": 0, "assigned": 0, "picked_up": 0, "delivered": 0, "ignored": 0, "errors": 0}

    while _running:
        cycle += 1

        # Kill switch
        if flag("kill_switch_to_v1", False):
            _log.warning("kill_switch_to_v1=TRUE, sleeping 30s")
            time.sleep(30)
            continue

        t0 = time.time()
        _maybe_reload_coords()
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

        # A4.1: poll CONFIG_RELOAD broadcast events co 30s rate-limited.
        # Belt-and-suspenders obok _COORDS mtime hot-reload (MP-#12).
        if _broadcast_sub is not None and time.time() - last_broadcast_poll >= BROADCAST_POLL_INTERVAL_S:
            try:
                _new_events = _broadcast_sub.poll(["CONFIG_RELOAD"], limit=50)
                if _new_events:
                    dispatch_config_reload(_new_events, "panel_watcher")
            except Exception as _bp_e:
                _log.warning(
                    f"A4.1 broadcast poll fail "
                    f"({type(_bp_e).__name__}: {_bp_e}) — skip, retry next interval"
                )
            last_broadcast_poll = time.time()

        # Sleep do nastepnego cyklu
        sleep_for = max(0.5, interval - elapsed)
        time.sleep(sleep_for)

    _log.info("Panel watcher STOP")


if __name__ == "__main__":
    run()
