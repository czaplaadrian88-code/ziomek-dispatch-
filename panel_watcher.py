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
import hashlib
import json
import os
import signal
import sys
import time
from contextvars import ContextVar
from datetime import datetime, timezone
from typing import Optional, Tuple

from dispatch_v2 import common as C
from dispatch_v2 import durable_event_apply
from dispatch_v2 import lifecycle_downstream
from dispatch_v2 import plan_manager
from dispatch_v2.common import (
    FIRMOWE_KONTO_ADDRESS_IDS,
    FIRMOWE_KONTO_FALLBACK_COORDS,
    decision_flag,
    flag,
    load_config,
    load_flags,
    now_iso,
    setup_logger,
)
from dispatch_v2.osrm_client import haversine as _haversine_km
from dispatch_v2.core.broadcast_handlers import dispatch_config_reload
from dispatch_v2.core.config_reload_subscriber import BroadcastSubscriber
from dispatch_v2.event_bus import emit, emit_audit
from dispatch_v2.parser_health import get_monitor as get_parser_health_monitor
from dispatch_v2.parser_health_layer3 import install_layer3, record_tick_full
from dispatch_v2.parser_health_endpoint import start_health_endpoint
from dispatch_v2.uwagi_address_parser import (
    inspect_bridge_nadawca,
    parse_pickup_from_uwagi,
)
from dispatch_v2.uwagi_bridge_envelope import (
    BridgeCredentialError,
    load_bridge_hmac,
)
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
    build_czasowka_manual_ck_pickup_event,
    event_effect_status,
    get_all as state_get_all,
    get_order as state_get_order,
    get_order_strict as state_get_order_strict,
    update_from_event,
    upsert_order,
    touch_check_cursor,
)
from dispatch_v2.geocoding import geocode

_log = setup_logger("panel_watcher", "/root/.openclaw/workspace/scripts/logs/dispatch.log")
_UWAGI_BRIDGE_SHADOW_LOG_PATH = os.environ.get(
    "UWAGI_BRIDGE_SHADOW_LOG",
    "/root/.openclaw/workspace/dispatch_state/uwagi_bridge_envelope.jsonl",
)


def _write_uwagi_bridge_shadow_metric(
    order_id,
    *,
    envelope_seen: bool,
    version,
    reason: str,
    parsed: bool,
    geocode_ok: bool,
    central_fallback: bool,
) -> None:
    """Append one bounded PII-free envelope outcome; ingestion stays fail-soft."""
    record = {
        "order_id_hash": hashlib.sha256(
            f"uwagi-bridge:{order_id}".encode("utf-8")
        ).hexdigest()[:16],
        "envelope_seen": bool(envelope_seen),
        "version": version,
        "reason": str(reason),
        "parsed": bool(parsed),
        "geocode_ok": bool(geocode_ok),
        "central_fallback": bool(central_fallback),
    }
    try:
        from dispatch_v2.core.jsonl_appender import append_jsonl
        append_jsonl(_UWAGI_BRIDGE_SHADOW_LOG_PATH, record)
    except Exception as exc:
        _log.error(
            "UWAGI_BRIDGE_METRIC_WRITE_FAIL order_hash=%s error=%s",
            record["order_id_hash"],
            type(exc).__name__,
        )

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
_COORDS_META = {}   # FRONT-B: aid → source (guard manual*/adrian_manual*, GEO-02)
_COORDS_MTIME = 0.0
_COORDS_LAST_CHECK_TS = 0.0
_COORDS_CHECK_INTERVAL_S = 15.0


_EventApplyOutcome = durable_event_apply.DurableApplyOutcome
_STATE_OUTBOX_SWEEPER_TICK_SNAPSHOT: ContextVar[Optional[bool]] = ContextVar(
    "state_outbox_sweeper_tick_snapshot", default=None
)


def _emit_and_apply_state(
    event_type: str,
    *,
    order_id: str,
    courier_id: Optional[str] = None,
    payload: Optional[dict] = None,
    state_payload: Optional[dict] = None,
    event_id: str,
    audit: bool = False,
    old_plan_release_authorized: Optional[bool] = None,
) -> _EventApplyOutcome:
    """Atomowy event+outbox, exact-payload state apply i trwały downstream."""
    emitter = emit_audit if audit else emit
    state_body = (payload or {}) if state_payload is None else (state_payload or {})
    source = str(state_body.get("source") or "")
    state_event_metadata = {}
    if event_type == "COURIER_ASSIGNED" and source in _PANEL_LEARNING_SOURCES:
        # pending_proposals/learning_log sa zmienne i nie naleza do orders_state.
        # Ich causalny snapshot musi jednak wejsc do TEJ SAMEJ trwalej intencji
        # co assignment; inaczej crash przed pierwsza projekcja learningu
        # pozwala retry sklasyfikowac zdarzenie wedlug pozniejszej propozycji.
        state_event_metadata["panel_learning_context"] = (
            _capture_panel_learning_context(order_id)
        )
    if (
        old_plan_release_authorized is not None
        and event_type in {"COURIER_ASSIGNED", "ORDER_RETURNED_TO_POOL"}
    ):
        marker = (
            "reassign_old_plan_release_authorized"
            if event_type == "COURIER_ASSIGNED"
            else "return_previous_cleanup_authorized"
        )
        state_event_metadata[marker] = bool(old_plan_release_authorized)
    return durable_event_apply.emit_and_apply(
        event_type,
        order_id=str(order_id),
        courier_id=courier_id,
        payload=payload,
        state_payload=state_payload,
        event_key=event_id,
        emit_fn=emitter,
        state_update_fn=update_from_event,
        effect_status_fn=event_effect_status,
        get_order_fn=state_get_order_strict,
        downstream_fn=lifecycle_downstream.apply,
        state_event_metadata=state_event_metadata or None,
        sweeper_enabled=_STATE_OUTBOX_SWEEPER_TICK_SNAPSHOT.get(),
    )


def _load_coords():
    global _COORDS, _COORDS_META, _COORDS_MTIME
    try:
        import json
        import os
        mtime = os.path.getmtime(_COORDS_PATH)
        with open(_COORDS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        _COORDS = {str(k): (v["lat"], v["lng"]) for k, v in data.items() if "lat" in v and "lng" in v}
        _COORDS_META = {str(k): str(v.get("source") or "")
                        for k, v in data.items() if isinstance(v, dict)}
        _COORDS_MTIME = mtime
    except Exception as e:
        _log.warning(f"_load_coords fail: {e}")
        _COORDS = {}
        _COORDS_META = {}
        _COORDS_MTIME = 0.0


_FRONTB_DRIFT_WARNED = set()  # aid → WARNING raz na proces (anti-spam Z3)


def _resolve_pickup_coords(aid_str, street, city, zid=None):
    """FRONT-B (2026-06-11): pickup_coords na żywo z adresu panelu.

    Cache restaurant_coords.json (bootstrap 11.04, bez timera) nie podąża za
    zmianami adresu w panelu — incydent Raj/Grill Kebab 05.06 (2 restauracje,
    bajt-w-bajt identyczne koordy). Geokod aktualnego address.street liczony
    ZAWSZE (lekcja #186; cache-first przez geocode_cache → ~0 ms dla znanych
    ulic; tylko NOWE zlecenia) + drift vs cache do logu FRONT_B (korpus pod
    werdykt flipu). Selekcję przełącza ENABLE_PICKUP_COORDS_FROM_PANEL (kanon
    flags.json, default OFF = zachowanie bez zmian: cache albo None).

    GUARD GEO-02: wpis cache source=manual*/adrian_manual* jest autorytatywny
    (ręczne koordy Adriana, często PUSTY street — geokod dałby centrum miasta):
    zero geokodu, zero driftu. Firmowe konta filtruje CALL-SITE (ich pickup
    jest w uwagach, nie w address.street).

    Zwraca (coords|None, source_label, drift_m|None).
    """
    _maybe_reload_coords()
    cached = _COORDS.get(aid_str) if aid_str else None
    src = _COORDS_META.get(aid_str, "") if aid_str else ""
    if cached and (src.startswith("adrian_manual") or src.startswith("manual")):
        return tuple(cached), "cache_manual", None
    live = None
    if street:
        try:
            live = geocode(street, city=city, timeout=2.0)
        except Exception as e:
            _log.warning(f"FRONT_B geocode fail zid={zid} '{street}': {e}")
    drift_m = None
    if cached and live:
        try:
            drift_m = round(_haversine_km(tuple(cached), tuple(live)) * 1000.0, 1)
        except Exception:
            drift_m = None
        warn_m = float(load_flags().get(
            "PICKUP_COORDS_DRIFT_WARN_M", C.PICKUP_COORDS_DRIFT_WARN_M))
        if (drift_m is not None and drift_m > warn_m
                and aid_str not in _FRONTB_DRIFT_WARNED):
            _FRONTB_DRIFT_WARNED.add(aid_str)
            _log.warning(
                f"FRONT_B drift aid={aid_str} zid={zid} drift_m={drift_m} "
                f"cache={cached} live={live} street='{street}' — cache nie "
                f"podąża za panelem (warn raz/proces)")
    _log.info(
        f"FRONT_B resolve zid={zid} aid={aid_str} cache={bool(cached)} "
        f"live={bool(live)} drift_m={drift_m}")
    if decision_flag("ENABLE_PICKUP_COORDS_FROM_PANEL") and live:
        return tuple(live), "panel_live", drift_m
    if cached:
        return tuple(cached), "cache", drift_m
    return None, "miss", drift_m


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
_PANEL_LEARNING_SOURCES = frozenset({
    "panel_initial",
    "panel_diff",
    "panel_reassign",
})


def _durable_downstream_attempt(
    lifecycle_event_id: Optional[str],
    *,
    _raise_on_error: bool = False,
) -> int:
    """Numer trwalej próby callbacku; zero oznacza legacy caller."""
    if not lifecycle_event_id:
        return 0
    try:
        from dispatch_v2 import event_bus

        row = event_bus.get_state_apply_outbox(str(lifecycle_event_id))
        if row is None:
            if _raise_on_error:
                raise RuntimeError(
                    f"missing durable receipt for {lifecycle_event_id}"
                )
            return 0
        return int(row.get("downstream_attempts") or 0)
    except Exception:
        if _raise_on_error:
            raise
        # Brak outboxa oznacza legacy caller. Jego semantyki nie rozszerzamy o
        # kosztowny skan 30 rotacji; durable path ma zawsze prawdziwy receipt.
        return 0


def _append_learning_record(
    record: dict,
    lifecycle_event_id: Optional[str],
    *,
    _raise_on_error: bool = False,
) -> None:
    """Materialize one SQLite-canonical learning effect into JSONL.

    The per-effect receipt is separate from the composite lifecycle callback:
    a later plan/recanon failure therefore retries only unfinished effects and
    cannot reclassify the assignment.  Physical JSONL delivery remains
    at-least-once in the extreme append-before-receipt crash window; retries
    dedupe across the active file and every retained numeric rotation. The
    callback ``lifecycle_event_id`` remains the idempotency identity of the
    assignment.  With JOIN-HARDENING ON the public record uses
    ``lifecycle_event_id`` for the source shadow decision and keeps the callback
    identity separately as ``assignment_lifecycle_event_id``.
    """
    from dispatch_v2 import event_bus
    from dispatch_v2.core.jsonl_appender import (
        append_jsonl,
        append_jsonl_durable,
        append_jsonl_once,
    )

    if not lifecycle_event_id:
        append_jsonl(_LEARNING_LOG_PATH, record)
        return
    # PANEL_AGREE and PANEL_OVERRIDE are mutually exclusive classifications of
    # one assignment.  Use one event-level effect identity so a crash between
    # preparing and projecting the record cannot let changed proposal files
    # choose a different classification on retry; first committed record wins.
    effect_name = "panel_assignment_learning"
    projection, created = event_bus.prepare_durable_learning_projection(
        str(lifecycle_event_id), effect_name, record
    )
    if projection.get("projected_at"):
        return
    canonical_record = projection.get("record")
    if not isinstance(canonical_record, dict):
        raise RuntimeError(
            f"invalid canonical learning projection {projection.get('effect_id')}"
        )
    if canonical_record.get("action") == "PANEL_LEARNING_NONE":
        # Trwaly negatywny wynik klasyfikacji jest receiptem, nie rekordem
        # treningowym. Crash miedzy prepare i mark wznawia samo markowanie.
        if not event_bus.mark_durable_learning_projected(
            str(projection.get("effect_id") or "")
        ):
            raise RuntimeError(
                "cannot persist negative learning projection receipt "
                f"{projection.get('effect_id')}"
            )
        return
    attempt = _durable_downstream_attempt(
        lifecycle_event_id, _raise_on_error=_raise_on_error
    )
    # Backward compatibility with E1 projections: older canonical records use
    # lifecycle_event_id for the COURIER_ASSIGNED callback. New join records
    # reserve that key for shadow.event_id and dedupe on the explicit assignment
    # key. The SQLite projection is still the first-wins source of truth.
    assignment_id = str(lifecycle_event_id)
    dedupe_key = (
        "assignment_lifecycle_event_id"
        if str(canonical_record.get("assignment_lifecycle_event_id") or "")
        == assignment_id
        else "lifecycle_event_id"
    )
    if not created or attempt > 1:
        append_jsonl_once(
            _LEARNING_LOG_PATH,
            canonical_record,
            dedupe_key=dedupe_key,
            dedupe_value=assignment_id,
            scan_rotated=True,
        )
    elif attempt == 1:
        append_jsonl_durable(_LEARNING_LOG_PATH, canonical_record)
    elif _raise_on_error:
        raise RuntimeError(
            f"durable callback has invalid attempt={attempt} "
            f"for {lifecycle_event_id}"
        )
    else:
        append_jsonl_once(
            _LEARNING_LOG_PATH,
            canonical_record,
            dedupe_key=dedupe_key,
            dedupe_value=assignment_id,
            scan_rotated=True,
        )
    if not event_bus.mark_durable_learning_projected(
        str(projection.get("effect_id") or "")
    ):
        raise RuntimeError(
            f"cannot persist learning projection receipt {projection.get('effect_id')}"
        )


def _resume_durable_learning_projection(
    lifecycle_event_id: Optional[str],
    *,
    _raise_on_error: bool = False,
) -> bool:
    """Domknij raz wybrana klasyfikacje przed odczytem zmiennych plikow/flag.

    ``True`` oznacza, ze SQLite zawieral juz kanoniczny efekt (takze juz
    projected). Caller nie moze wtedy ponownie klasyfikowac ASSIGN na podstawie
    pozniejszego pending_proposals, wieku ani live flagi.
    """
    if not lifecycle_event_id:
        return False
    from dispatch_v2 import event_bus

    effect_id = f"{lifecycle_event_id}:panel_assignment_learning"
    try:
        projection = event_bus.get_durable_learning_projection(effect_id)
    except Exception:
        if _raise_on_error:
            raise
        return False
    if projection is None:
        return False
    try:
        canonical_record = projection.get("record")
        if not isinstance(canonical_record, dict):
            raise RuntimeError(f"invalid canonical learning projection {effect_id}")
        _append_learning_record(
            canonical_record,
            lifecycle_event_id,
            _raise_on_error=_raise_on_error,
        )
        return True
    except Exception:
        if _raise_on_error:
            raise
        # Istniejacy durable wybor nadal blokuje ponowna klasyfikacje, nawet
        # gdy legacy caller nie chce propagowac chwilowego bledu projekcji.
        return True


def _seal_no_panel_learning(
    lifecycle_event_id: Optional[str],
    order_id: str,
    *,
    _raise_on_error: bool = False,
) -> None:
    """Utrwal negatywna klasyfikacje, aby retry nie czytal nowszych proposal."""
    if not lifecycle_event_id:
        return
    try:
        _append_learning_record(
            {
                "ts": now_iso(),
                "order_id": str(order_id),
                "action": "PANEL_LEARNING_NONE",
                "lifecycle_event_id": str(lifecycle_event_id),
            },
            lifecycle_event_id,
            _raise_on_error=_raise_on_error,
        )
    except Exception:
        if _raise_on_error:
            raise


def _learning_record_identity_fields(
    decision_record: dict,
    assignment_lifecycle_event_id: Optional[str],
    *,
    decision_join_enabled: bool,
) -> dict:
    """Rozdziel ID decyzji shadow od ID późniejszego assignmentu.

    OFF zachowuje dokładnie schemat E1. ON daje żądany twardy join
    ``learning.lifecycle_event_id == shadow.event_id``; brak event_id jest
    jawny jako ``null`` zamiast sfabrykowania innego zdarzenia.
    """
    assignment_id = (
        str(assignment_lifecycle_event_id)
        if assignment_lifecycle_event_id
        else None
    )
    if not decision_join_enabled:
        return {"lifecycle_event_id": assignment_id} if assignment_id else {}

    shadow_event_id = str((decision_record or {}).get("event_id") or "") or None
    fields = {"lifecycle_event_id": shadow_event_id}
    if assignment_id:
        fields["assignment_lifecycle_event_id"] = assignment_id
    return fields


def _check_panel_override(
    order_id: str,
    panel_courier_id: str,
    source: str,
    *,
    lifecycle_event_id: Optional[str] = None,
    _raise_on_error: bool = False,
    _context_by_receipt: Optional[dict] = None,
) -> None:
    """Jeśli order_id był w pending_proposals i kurier panelu różny od propozycji
    Ziomka — zapisz PANEL_OVERRIDE do learning_log.jsonl.

    source: 'panel_initial' | 'panel_diff' | 'panel_reassign' (telemetria).
    Wywoływane TYLKO po skutecznym przejściu stanu (nowy event albo recovery
    wcześniejszego lost-apply); zwykły duplikat już zastosowanego eventu jest
    pomijany. Domyślnie best-effort; trwały downstream ustawia
    ``_raise_on_error``, aby błąd pozostawił receipt do retry.
    """
    import json
    if _resume_durable_learning_projection(
        lifecycle_event_id, _raise_on_error=_raise_on_error
    ):
        return
    if _context_by_receipt is not None:
        if not isinstance(_context_by_receipt, dict):
            raise ValueError("panel learning receipt context is not an object")
        rec = _context_by_receipt.get("pending_record")
    else:
        try:
            with open(_PENDING_PROPOSALS_PATH, "r", encoding="utf-8") as f:
                pending = json.load(f)
        except FileNotFoundError:
            _seal_no_panel_learning(
                lifecycle_event_id, order_id, _raise_on_error=_raise_on_error
            )
            return
        except Exception as e:
            _log.warning(f"PANEL_OVERRIDE read pending fail: {e}")
            if _raise_on_error:
                raise
            return
        rec = pending.get(str(order_id)) if isinstance(pending, dict) else None
    if not rec:
        _seal_no_panel_learning(
            lifecycle_event_id, order_id, _raise_on_error=_raise_on_error
        )
        return

    dr = rec.get("decision_record") or {}
    best = dr.get("best") or {}
    proposed_courier_id = str(best.get("courier_id") or "")
    proposed_score = best.get("score")

    if not proposed_courier_id or proposed_courier_id == str(panel_courier_id):
        _seal_no_panel_learning(
            lifecycle_event_id, order_id, _raise_on_error=_raise_on_error
        )
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
        **_learning_record_identity_fields(
            dr,
            lifecycle_event_id,
            decision_join_enabled=(
                bool(_context_by_receipt.get("decision_join_enabled", False))
                if _context_by_receipt is not None
                else decision_flag("ENABLE_LEARNING_LOG_DECISION_JOIN")
            ),
        ),
    }
    # MP-#11 (2026-05-08): atomic JSONL append via core helper. Eliminuje race
    # między panel_watcher i telegram_approver pisanie do TEGO SAMEGO learning_log.
    try:
        _append_learning_record(
            override_rec,
            lifecycle_event_id,
            _raise_on_error=_raise_on_error,
        )
    except Exception as e:
        _log.warning(f"PANEL_OVERRIDE write learning_log fail oid={order_id}: {e}")
        if _raise_on_error:
            raise
        return

    _log.info(
        f"PANEL_OVERRIDE oid={order_id} proposed={proposed_courier_id} "
        f"(score={proposed_score}) actual={panel_courier_id} src={source}"
    )


# ---- PANEL_AGREE reconciliation (ETAP 3 audytu 2026-06-10, finding Z-03) ----
# Lustrzane do PANEL_OVERRIDE: zgodne przypisanie panelem (koordynator daje TEGO
# SAMEGO kuriera co best propozycji) nie zostawiało żadnego śladu w learning_log
# (_check_panel_override robi return przy zgodności) → acceptance-rate propozycji
# nie istniał. Czysta telemetria — zero wpływu na scoring/feasibility/emit.
# Kill-switch: env ENABLE_PANEL_AGREE=0 (default ON) albo flags.json (hot-reload).
_PANEL_AGREE_MAX_AGE_MIN = float(os.environ.get("PANEL_AGREE_MAX_PROPOSAL_AGE_MIN", "15"))
_PANEL_AGREE_TAIL_BYTES = 262144  # tail-scan learning_log za ASSIGN_DIRECT (edge c)


def _panel_agree_enabled() -> bool:
    return flag("ENABLE_PANEL_AGREE",
                default=os.environ.get("ENABLE_PANEL_AGREE", "1") != "0")


def _parse_iso_utc(ts_str) -> Optional[datetime]:
    if not ts_str:
        return None
    try:
        dt = datetime.fromisoformat(str(ts_str).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    except Exception:
        return None


def _find_recent_assign_direct(
    order_id: str,
    *,
    _raise_on_error: bool = False,
) -> Optional[dict]:
    """ASSIGN z przycisku w Telegramie popuje pending_proposals PRZED tym, jak
    panel pokaże przypisanie — propozycji już nie ma, ale telegram_approver
    zostawił wpis ASSIGN_DIRECT (chosen_courier_id + proposed_courier_id +
    decision). Tail-scan ostatnich _PANEL_AGREE_TAIL_BYTES learning_log,
    najnowszy wpis dla oid świeższy niż _PANEL_AGREE_MAX_AGE_MIN."""
    import json
    oid = str(order_id)
    try:
        with open(_LEARNING_LOG_PATH, "rb") as f:
            f.seek(0, os.SEEK_END)
            size = f.tell()
            f.seek(max(0, size - _PANEL_AGREE_TAIL_BYTES))
            tail = f.read().decode("utf-8", errors="ignore")
    except FileNotFoundError:
        return None
    except Exception as e:
        _log.warning(f"PANEL_AGREE tail-scan fail oid={oid}: {e}")
        if _raise_on_error:
            raise
        return None
    now = datetime.now(timezone.utc)
    for ln in reversed(tail.splitlines()):
        if "ASSIGN_DIRECT" not in ln or oid not in ln:
            continue
        try:
            rec = json.loads(ln)
        except Exception:
            continue
        if rec.get("action") != "ASSIGN_DIRECT" or str(rec.get("order_id")) != oid:
            continue
        ts = _parse_iso_utc(rec.get("ts"))
        if ts is None or (now - ts).total_seconds() > _PANEL_AGREE_MAX_AGE_MIN * 60.0:
            return None  # najnowszy wpis dla oid za stary → brak związku
        return rec
    return None


def _capture_panel_learning_context(order_id: str) -> dict:
    """Snapshot mutable learning inputs before the durable ASSIGNED receipt.

    ``capture_status`` is explicit, including read failures.  Retry therefore
    never upgrades a temporary absence/corruption into a later proposal and
    never changes AGREE into OVERRIDE (or the reverse).
    """
    import json

    context = {
        "schema_version": 3,
        "captured_at": now_iso(),
        # Threshold jest czescia klasyfikacji tego exact ASSIGNED. Retry po
        # restarcie nie moze zmienic AGREE/NONE tylko dlatego, ze config procesu
        # zostal zmieniony po utrwaleniu receiptu.
        "panel_agree_max_age_min": float(_PANEL_AGREE_MAX_AGE_MIN),
        # Semantyka klucza joinu musi być związana z exact assignment receipt;
        # retry po flipie nie może zmienić znaczenia już utrwalanej lekcji.
        "decision_join_enabled": decision_flag(
            "ENABLE_LEARNING_LOG_DECISION_JOIN"
        ),
        "capture_status": "none",
        "pending_record": None,
        "assign_direct": None,
    }
    try:
        with open(_PENDING_PROPOSALS_PATH, "r", encoding="utf-8") as f:
            pending = json.load(f)
    except FileNotFoundError:
        pending = {}
    except Exception as exc:
        context["capture_status"] = "unavailable"
        _log.warning(
            "PANEL_LEARNING snapshot pending fail oid=%s: %s: %s",
            order_id,
            type(exc).__name__,
            exc,
        )
        return context

    rec = pending.get(str(order_id)) if isinstance(pending, dict) else None
    if isinstance(rec, dict):
        context["capture_status"] = "pending"
        context["pending_record"] = rec
        return context

    assign_direct = _find_recent_assign_direct(order_id)
    if isinstance(assign_direct, dict):
        context["capture_status"] = "assign_direct"
        context["assign_direct"] = assign_direct
    return context


def _write_panel_agree(
    order_id: str,
    proposed_cid: str,
    panel_courier_id: str,
    latency_s,
    dr: dict,
    source_kind: str,
    panel_source: str,
    *,
    lifecycle_event_id: Optional[str] = None,
    decision_join_enabled: Optional[bool] = None,
    _raise_on_error: bool = False,
) -> None:
    """Schemat zgodny z PANEL_OVERRIDE (proposed_courier_id/actual_courier_id —
    sequential_replay.build_roster łapie cidy bez zmian) + pola pod raport
    acceptance (tier/prep/verdict). Celowo BEZ pełnego `decision` — komponenty
    score są w shadow_decisions po order_id (nie bloatujemy learning_log)."""
    best = dr.get("best") or {}
    tier = best.get("dwell_tier")
    if not tier:
        cap_used = str(best.get("v319h_bug4_tier_cap_used") or "")
        tier = cap_used.split("/")[0] or None
    agree_rec = {
        "ts": now_iso(),
        "order_id": str(order_id),
        "action": "PANEL_AGREE",
        "proposed_courier_id": str(proposed_cid),
        "actual_courier_id": str(panel_courier_id),
        "latency_s": latency_s,
        "proposed_score": best.get("score"),
        "proposal_verdict": dr.get("verdict"),
        "restaurant": dr.get("restaurant"),
        "proposed_tier": tier,
        "pickup_ready_at": dr.get("pickup_ready_at"),
        "order_created_at": dr.get("order_created_at"),
        "source": source_kind,        # "panel" | "telegram" (edge c — bez dublu w raporcie)
        "panel_source": panel_source,  # panel_initial | panel_diff | panel_reassign
        **_learning_record_identity_fields(
            dr,
            lifecycle_event_id,
            decision_join_enabled=(
                decision_flag("ENABLE_LEARNING_LOG_DECISION_JOIN")
                if decision_join_enabled is None
                else bool(decision_join_enabled)
            ),
        ),
    }
    # MP-#11: atomic JSONL append (flock) — ta sama dyscyplina co PANEL_OVERRIDE;
    # panel_watcher i telegram_approver piszą do TEGO SAMEGO learning_log.
    try:
        _append_learning_record(
            agree_rec,
            lifecycle_event_id,
            _raise_on_error=_raise_on_error,
        )
    except Exception as e:
        _log.warning(f"PANEL_AGREE write learning_log fail oid={order_id}: {e}")
        if _raise_on_error:
            raise
        return
    _log.info(
        f"PANEL_AGREE oid={order_id} cid={panel_courier_id} "
        f"(score={best.get('score')}) latency_s={latency_s} "
        f"source={source_kind} src={panel_source}"
    )


def _check_panel_agree(
    order_id: str,
    panel_courier_id: str,
    source: str,
    *,
    lifecycle_event_id: Optional[str] = None,
    _raise_on_error: bool = False,
    _enabled_by_receipt: Optional[bool] = None,
    _context_by_receipt: Optional[dict] = None,
) -> None:
    """Jeśli order_id ma świeżą (≤15 min) propozycję i kurier panelu ZGODNY z
    best — zapisz PANEL_AGREE do learning_log. Rozjazd obsługuje istniejący
    _check_panel_override (nietknięty). Wywoływane TYLKO po skutecznym przejściu
    stanu: nowy event albo recovery lost-apply (te same 3 call-sites co OVERRIDE;
    packs_fallback i coldstart celowo poza oboma). Zwykły duplikat już
    zastosowanego eventu jest pomijany. Domyślnie best-effort; trwały
    downstream może zażądać propagacji błędu do outboxa."""
    import json
    try:
        if _resume_durable_learning_projection(
            lifecycle_event_id, _raise_on_error=_raise_on_error
        ):
            return
        agree_enabled = (
            _panel_agree_enabled()
            if _enabled_by_receipt is None
            else bool(_enabled_by_receipt)
        )
        if not agree_enabled:
            return
        if not panel_courier_id or str(panel_courier_id) == str(KOORDYNATOR_ID):
            return  # hold na Koordynatora = nie-decyzja (edge a, belt-and-suspenders)

        if _context_by_receipt is not None:
            if not isinstance(_context_by_receipt, dict):
                raise ValueError("panel learning receipt context is not an object")
            rec = _context_by_receipt.get("pending_record")
        else:
            try:
                with open(_PENDING_PROPOSALS_PATH, "r", encoding="utf-8") as f:
                    pending = json.load(f)
            except FileNotFoundError:
                pending = {}
            except Exception as e:
                _log.warning(f"PANEL_AGREE read pending fail: {e}")
                if _raise_on_error:
                    raise
                return
            rec = pending.get(str(order_id)) if isinstance(pending, dict) else None
        if rec:
            # pending_proposals[oid] = zawsze OSTATNIA propozycja (proposal_sender
            # nadpisuje; starsze kończą jako TIMEOUT_SUPERSEDED) — edge (b).
            dr = rec.get("decision_record") or {}
            best = dr.get("best") or {}
            proposed = str(best.get("courier_id") or "")
            if not proposed or proposed != str(panel_courier_id):
                return  # rozjazd → PANEL_OVERRIDE path
            sent_at = _parse_iso_utc(rec.get("sent_at") or dr.get("ts"))
            latency_s = None
            if sent_at is not None:
                if _context_by_receipt is not None:
                    observed_at = _parse_iso_utc(
                        _context_by_receipt.get("captured_at")
                    )
                    if observed_at is None:
                        raise ValueError(
                            "panel learning receipt has invalid captured_at"
                        )
                    raw_max_age = _context_by_receipt.get(
                        "panel_agree_max_age_min",
                        # Kompatybilnosc z receiptami schema v1 utrwalonymi
                        # przed dodaniem progu. Nowe v2 zawsze maja snapshot.
                        _PANEL_AGREE_MAX_AGE_MIN,
                    )
                    try:
                        max_age_min = float(raw_max_age)
                    except (TypeError, ValueError) as exc:
                        raise ValueError(
                            "panel learning receipt has invalid age threshold"
                        ) from exc
                    if max_age_min < 0:
                        raise ValueError(
                            "panel learning receipt has negative age threshold"
                        )
                else:
                    observed_at = datetime.now(timezone.utc)
                    max_age_min = _PANEL_AGREE_MAX_AGE_MIN
                age_s = (observed_at - sent_at).total_seconds()
                if age_s > max_age_min * 60.0:
                    return  # propozycja za stara — brak związku przyczynowego
                latency_s = round(age_s, 1)
            _write_panel_agree(order_id, proposed, panel_courier_id,
                               latency_s, dr, "panel", source,
                               lifecycle_event_id=lifecycle_event_id,
                               decision_join_enabled=(
                                   bool(_context_by_receipt.get(
                                       "decision_join_enabled", False
                                   ))
                                   if _context_by_receipt is not None
                                   else None
                               ),
                               _raise_on_error=_raise_on_error)
            return

        # Brak pending → możliwy ASSIGN z Telegrama (edge c).
        ad = (
            _context_by_receipt.get("assign_direct")
            if _context_by_receipt is not None
            else _find_recent_assign_direct(
                order_id, _raise_on_error=_raise_on_error
            )
        )
        if not ad:
            return
        chosen = str(ad.get("chosen_courier_id") or "")
        proposed = str(ad.get("proposed_courier_id") or "")
        if not chosen or chosen != str(panel_courier_id):
            return
        if not proposed or chosen != proposed:
            return  # ASSIGN w alternatywę ≠ zgoda z best — zostaje sam ASSIGN_DIRECT
        dr = ad.get("decision") or {}
        latency_s = None
        t_dec = _parse_iso_utc(dr.get("ts"))
        t_assign = _parse_iso_utc(ad.get("ts"))
        if t_dec is not None and t_assign is not None:
            latency_s = round((t_assign - t_dec).total_seconds(), 1)
        _write_panel_agree(order_id, proposed, panel_courier_id,
                           latency_s, dr, "telegram", source,
                           lifecycle_event_id=lifecycle_event_id,
                           decision_join_enabled=(
                               bool(_context_by_receipt.get(
                                   "decision_join_enabled", False
                               ))
                               if _context_by_receipt is not None
                               else None
                           ),
                           _raise_on_error=_raise_on_error)
    except Exception as e:
        _log.warning(f"PANEL_AGREE check fail oid={order_id}: {type(e).__name__}: {e}")
        if _raise_on_error:
            raise


def _save_plan_on_assign(
    order_id: str,
    courier_id: str,
    *,
    _raise_on_error: bool = False,
    _saved_plans_authorized_by_receipt: Optional[bool] = None,
) -> None:
    """V3.19b: zapisz plan z pending_proposals po emit COURIER_ASSIGNED.

    Odczytuje pending_proposals[oid].decision_record.best.plan i mapuje na
    plan_manager schema. Skip cicho gdy: flag off, pending brak, best courier
    ≠ assigned courier (PANEL_OVERRIDE — kurier koordynatora, nie nasz), brak
    plan.sequence. Domyślnie best-effort; tryb outboxa propaguje błędy.
    """
    try:
        from dispatch_v2.common import ENABLE_SAVED_PLANS
        saved_plans_enabled = (
            bool(ENABLE_SAVED_PLANS)
            if _saved_plans_authorized_by_receipt is None
            else bool(_saved_plans_authorized_by_receipt)
        )
        if not saved_plans_enabled:
            return
    except Exception:
        if _raise_on_error:
            raise
        return
    try:
        import json
        with open(_PENDING_PROPOSALS_PATH, "r", encoding="utf-8") as f:
            pending = json.load(f)
    except FileNotFoundError:
        return
    except Exception:
        if _raise_on_error:
            raise
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
    # Z-P0-04: token pochodzi ze snapshotu sprzed puli kandydatow i przeszedl
    # przez metrics -> serializer -> pending proposal. Brak tokenu (np. proposal
    # utworzony przez starszy proces przed deployem) = bezpieczny skip, nigdy
    # create-or-overwrite przez expected_version=None.
    expected_version = best.get("plan_expected_version")
    if (not isinstance(expected_version, int)
            or isinstance(expected_version, bool)):
        _log.warning(
            f"PLAN_CAS_TOKEN_MISSING writer=assign cid={courier_id} "
            f"oid={order_id} value={expected_version!r} policy=keep_current"
        )
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

    # L2.1 sentinel-ingest (2026-07-01, K5b): stop.coords z REALNYCH coords zleceń
    # w orders_state zamiast hardkodu (0,0). Placeholder (0,0) persystował w
    # courier_plans.json (LIVE 11/79 stopów) i DETONOWAŁ w konsumentach planu
    # (_soon_free_probe → haversine ValueError → V328 eject całego kuriera —
    # 28 ofiar 01.07). Lookup fail → placeholder jak dotąd (konsumenci mają
    # guardy), ale GŁOŚNO. Flaga OFF = legacy hardkod.
    _real_coords = {}
    if C.decision_flag("ENABLE_COORD_SENTINEL_INGEST_GUARD"):
        try:
            from dispatch_v2 import state_machine as _sm_plan
            for _oid in sequence:
                _so = _sm_plan.get_order(str(_oid)) or {}
                _real_coords[str(_oid)] = {
                    "pickup": _so.get("pickup_coords"),
                    "dropoff": _so.get("delivery_coords"),
                }
        except Exception as _rc_e:
            _log.warning(f"V3.19b real-coords lookup fail oid={order_id}: {_rc_e}")

    def _stop_coords(oid_s: str, stop_type: str) -> dict:
        c = (_real_coords.get(oid_s) or {}).get(stop_type)
        try:
            from dispatch_v2.common import coords_in_bialystok_bbox as _cib
            if c is not None and _cib(c):
                return {"lat": round(float(c[0]), 6), "lng": round(float(c[1]), 6)}
        except Exception:
            pass
        if _real_coords:  # flaga ON, a coords brak/niepoprawne → głośno
            _log.warning(
                f"COORD_INGEST_GUARD plan-stop {oid_s}/{stop_type}: brak realnych "
                f"coords ({c!r}) — placeholder (0,0) w planie cid={courier_id}"
            )
        return {"lat": 0.0, "lng": 0.0}

    stops = []
    for oid in sequence:
        oid_s = str(oid)
        # pickup first (jeśli w pickup_at — oznacza że nowy order miał pickup w planie)
        if oid_s in pickup_at:
            stops.append({
                "order_id": oid_s,
                "type": "pickup",
                "coords": _stop_coords(oid_s, "pickup"),
                "scheduled_at": None,
                "predicted_at": pickup_at[oid_s],
                "dwell_min": 2.0,
                "status_at_plan_time": "assigned",
            })
        pred = predicted.get(oid_s)
        stops.append({
            "order_id": oid_s,
            "type": "dropoff",
            "coords": _stop_coords(oid_s, "dropoff"),
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
        if _raise_on_error:
            plan_manager.save_plan(
                str(courier_id),
                body,
                expected_version=expected_version,
                _raise_on_corrupt=True,
            )
        else:
            plan_manager.save_plan(
                str(courier_id), body, expected_version=expected_version
            )
        _log.info(f"V3.19b plan saved cid={courier_id} oid={order_id} stops={len(stops)}")
    except plan_manager.ConcurrencyError as e:
        _log.warning(
            f"PLAN_CAS_SKIP writer=assign cid={courier_id} oid={order_id} "
            f"expected={e.expected_version} current={e.current_version} "
            f"policy=keep_current"
        )
        if _raise_on_error:
            # Pierwsza proba mogla wykonac rename, po czym polec na fsync dir.
            # Retry widzi wtedy CAS mismatch; ponowny fsync domyka ten zapis.
            plan_manager.ensure_storage_durable()
    except Exception as e:
        _log.warning(f"V3.19b save_plan fail cid={courier_id} oid={order_id}: {e}")
        if _raise_on_error:
            raise


def _invalidate_plan_on_bag_change(
    order_id: str,
    courier_id: str,
    *,
    _raise_on_error: bool = False,
    _saved_plans_authorized_by_receipt: Optional[bool] = None,
    _enabled_by_receipt: Optional[bool] = None,
) -> bool:
    """BUG-1 (2026-06-05): gdy zlecenie zostaje przypisane/przepisane kurierowi, a NIE
    jest pokryte jego zapisanym planem (typowo PANEL_OVERRIDE / reassign — koordynator
    przypisał ręcznie, nie z propozycji Ziomka, więc _save_plan_on_assign cicho pomija
    zapis), unieważnij istniejący plan kuriera.

    invalidate_plan bumpuje invalidated_at → /api/courier/plan-version zmienia sygnał +
    SSE PLAN_UPDATED → apka natychmiast robi pełny GET /api/courier/orders (build_view
    zwraca cały aktualny worek), zamiast czekać do 5-min plan_recheck gap-fill.

    Zwraca True, gdy plan jest bezpieczny (pokrywa order albo już używa
    fallbacku po braku/inwalidacji). False oznacza, że flaga blokuje potrzebną
    inwalidację. Domyślnie błędy nie propagują; outbox włącza strict.
    """
    try:
        from dispatch_v2.common import ENABLE_SAVED_PLANS, flag
        saved_plans_enabled = (
            bool(ENABLE_SAVED_PLANS)
            if _saved_plans_authorized_by_receipt is None
            else bool(_saved_plans_authorized_by_receipt)
        )
        if not saved_plans_enabled:
            return True
        invalidate_enabled = (
            flag("ENABLE_INVALIDATE_PLAN_ON_BAG_CHANGE", True)
            if _enabled_by_receipt is None
            else bool(_enabled_by_receipt)
        )
    except Exception:
        if _raise_on_error:
            raise
        return False
    if not courier_id or not order_id:
        return True
    try:
        from dispatch_v2 import plan_manager
        # load_plan zwraca None gdy brak planu LUB plan już invalidated → no-op w obu
        if _raise_on_error:
            plan = plan_manager.load_plan(
                str(courier_id), _raise_on_corrupt=True
            )
        else:
            plan = plan_manager.load_plan(str(courier_id))
        if plan is None:
            if _raise_on_error:
                plan_manager.ensure_storage_durable()
            return True
        covered = {str(s.get("order_id")) for s in plan.get("stops", [])}
        if str(order_id) in covered:
            if _raise_on_error:
                plan_manager.ensure_storage_durable()
            return True
        if not invalidate_enabled:
            # Brak naprawy NIE jest stanem bezpiecznym. Caller zwykle może
            # potraktować receipt-False jako zamierzony no-op feature flagi,
            # lecz generation guard po remove_stops musi fail-closed i zostawić
            # receipt pending zamiast zamknąć go z aktywnym OID poza planem.
            return False
        try:
            if _raise_on_error:
                plan_manager.invalidate_plan(
                    str(courier_id),
                    "BAG_CHANGED",
                    expected_version=plan.get("plan_version", 0),
                    _raise_on_corrupt=True,
                )
            else:
                plan_manager.invalidate_plan(
                    str(courier_id),
                    "BAG_CHANGED",
                    expected_version=plan.get("plan_version", 0),
                )
        except plan_manager.ConcurrencyError as e:
            _log.warning(
                f"PLAN_CAS_SKIP writer=bag_change cid={courier_id} oid={order_id} "
                f"expected={e.expected_version} current={e.current_version} "
                f"policy=keep_current"
            )
            if _raise_on_error:
                raise
            return False
        _log.info(
            f"BUG-1 invalidate_plan_on_bag_change cid={courier_id} oid={order_id} "
            f"— order poza planem (reassign/override) → apka odświeży worek"
        )
        return True
    except Exception as e:
        _log.warning(
            f"BUG-1 invalidate_plan_on_bag_change fail cid={courier_id} oid={order_id}: {e}"
        )
        if _raise_on_error:
            raise
        return False


def _invalidate_plan_on_committed_change(
    order_id: str,
    courier_id: str,
    *,
    _raise_on_error: bool = False,
    _saved_plans_authorized_by_receipt: Optional[bool] = None,
    _enabled_by_receipt: Optional[bool] = None,
) -> None:
    """FIX-E (2026-06-13, B1): gdy zmienia się czas_kuriera/pickup (committed/ready)
    zlecenia PRZYPISANEGO kurierowi, zasygnalizuj zmianę JEGO planu (touch_plan: bump
    plan_version BEZ invalidacji) → /api/courier/plan-version się zmienia → apka robi
    pełny GET /orders i pobiera świeże eta_committed.

    Root B1 (wyścig odświeżania): czas_kuriera mutował orders_state, ale NIE bumpował
    plan_version, a apka odświeża /orders TYLKO po zmianie plan_version/invalidated_at.
    Po PANEL_OVERRIDE (bag-change → 1 refresh) czas_kuriera wchodził sekundy później,
    apka trzymała stale snapshot z eta_committed=null → nagłówek odbioru spadał do
    surowego predicted_at (np. 12:44 zamiast committed 13:20).

    W PRZECIWIEŃSTWIE do _invalidate_plan_on_bag_change NIE pomija gdy order jest
    POKRYTY planem — committed zmienia się właśnie dla pokrytych zleceń, a to ICH
    eta_committed w widoku jest nieaktualne. Per-cid (bumpuje tylko plan tego kuriera).
    Domyślnie best-effort; outbox włącza propagację do retry. Plan_recheck re-czasuje plan z nowym committed
    (refloor) na następnym ticku. Kill-switch: flaga ENABLE_COMMITTED_INVALIDATES_VIEW.
    """
    try:
        from dispatch_v2.common import ENABLE_SAVED_PLANS, flag
        saved_plans_enabled = (
            bool(ENABLE_SAVED_PLANS)
            if _saved_plans_authorized_by_receipt is None
            else bool(_saved_plans_authorized_by_receipt)
        )
        if not saved_plans_enabled:
            return
        committed_signal_enabled = (
            flag("ENABLE_COMMITTED_INVALIDATES_VIEW", True)
            if _enabled_by_receipt is None
            else bool(_enabled_by_receipt)
        )
        if not committed_signal_enabled:
            return
    except Exception:
        if _raise_on_error:
            raise
        return
    if not courier_id or not order_id:
        return
    try:
        from dispatch_v2 import plan_manager
        # touch_plan bumpuje plan_version (per-cid) BEZ invalidacji → /plan-version
        # się zmienia → apka odświeża /orders i czyta świeże eta_committed. Działa TEŻ
        # na planie JUŻ invalidated (scenariusz B1: PANEL_OVERRIDE unieważnił plan, a
        # czas_kuriera wchodzi sekundy później — load_plan zwracałby None i no-opował).
        # No-op (False) gdy brak planu — apka i tak na pełnym worku z fresh czas_kuriera.
        touched = (
            plan_manager.touch_plan(
                str(courier_id),
                "COMMITTED_TIME_CHANGED",
                _raise_on_corrupt=True,
            )
            if _raise_on_error
            else plan_manager.touch_plan(
                str(courier_id), "COMMITTED_TIME_CHANGED"
            )
        )
        if touched:
            _log.info(
                f"FIX-E committed_change cid={courier_id} oid={order_id} "
                f"— apka odświeży widok (eta_committed)"
            )
    except Exception as e:
        _log.warning(
            f"FIX-E committed_change fail cid={courier_id} oid={order_id}: {e}"
        )
        if _raise_on_error:
            raise


def _save_plan_on_assign_signal(
    order_id: str,
    courier_id: str,
    *,
    _raise_on_error: bool = False,
    _saved_plans_authorized_by_receipt: Optional[bool] = None,
    _recanon_authorized_by_receipt: Optional[bool] = None,
    _redecide_authorized_by_receipt: Optional[bool] = None,
    _invalidate_authorized_by_receipt: Optional[bool] = None,
) -> None:
    """BUG-1 (2026-06-05): zapisz plan z propozycji (gdy to nasz kurier) ORAZ zasygnalizuj
    apce zmianę worka. _save_plan_on_assign cicho pomija zapis przy PANEL_OVERRIDE/reassign,
    więc plan_version stoi i apka nie odświeża worka aż do 5-min plan_recheck.
    _invalidate_plan_on_bag_change łapie ten przypadek (order poza planem) i unieważnia
    plan → SSE PLAN_UPDATED → natychmiastowy pełny GET. No-op gdy save pokrył order."""
    _save_plan_on_assign(
        order_id,
        courier_id,
        _raise_on_error=_raise_on_error,
        _saved_plans_authorized_by_receipt=(
            _saved_plans_authorized_by_receipt
        ),
    )
    bag_safe = _invalidate_plan_on_bag_change(
        order_id,
        courier_id,
        _raise_on_error=_raise_on_error,
        _saved_plans_authorized_by_receipt=(
            _saved_plans_authorized_by_receipt
        ),
        _enabled_by_receipt=_invalidate_authorized_by_receipt,
    )
    if (
        _raise_on_error
        and bag_safe is False
        and _invalidate_authorized_by_receipt is True
    ):
        raise RuntimeError(
            f"assigned plan does not cover oid={order_id} and invalidation is disabled"
        )
    # RECANON: po zapisie planu z PROPOZYCJI (surowa sekwencja OR-Tools) egzekwuj
    # od razu niezmienniki kanonu (carried-first + committed + relax). No-op gdy
    # plan invalidated (override) — tym zajmuje się redecide niżej.
    try:
        from dispatch_v2 import plan_recheck
        if _raise_on_error:
            plan_recheck.recanon_courier(
                str(courier_id),
                reason="assign",
                _raise_on_error=True,
                _enabled_by_receipt=_recanon_authorized_by_receipt,
            )
        else:
            plan_recheck.recanon_courier(str(courier_id), reason="assign")
    except Exception as e:
        _log.warning(f"recanon-on-assign fail cid={courier_id} oid={order_id}: {e}")
        if _raise_on_error:
            raise
    # F3: po override/reassign (plan unieważniony lub brak) Ziomek decyduje trasę
    # NATYCHMIAST, nie po ≤5 min ticku plan_recheck. Samo-bramkujące (no-op gdy
    # ważny plan już pokrywa worek — nie nadpisuje propozycji). Best-effort, flaga
    # ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE (OFF) — błąd nigdy nie psuje diff loopu.
    try:
        from dispatch_v2 import plan_recheck
        if _raise_on_error:
            plan_recheck.redecide_courier(
                courier_id,
                _raise_on_error=True,
                _enabled_by_receipt=_redecide_authorized_by_receipt,
            )
        else:
            plan_recheck.redecide_courier(courier_id)
    except Exception as e:
        _log.warning(f"F3 redecide signal fail cid={courier_id} oid={order_id}: {e}")
        if _raise_on_error:
            raise


def _advance_plan_on_deliver(
    courier_id: str,
    order_id: str,
    delivered_at_raw: Optional[str],
    delivery_coords: Optional[list],
    *,
    _raise_on_error: bool = False,
    _saved_plans_authorized_by_receipt: Optional[bool] = None,
    _recanon_authorized_by_receipt: Optional[bool] = None,
) -> None:
    """V3.19b: advance plan po emit COURIER_DELIVERED sukces."""
    try:
        from dispatch_v2.common import ENABLE_SAVED_PLANS
        saved_plans_enabled = (
            bool(ENABLE_SAVED_PLANS)
            if _saved_plans_authorized_by_receipt is None
            else bool(_saved_plans_authorized_by_receipt)
        )
        if not saved_plans_enabled:
            return
    except Exception:
        if _raise_on_error:
            raise
        return
    if not courier_id:
        return
    try:
        from dispatch_v2 import plan_manager
        coords_tuple = None
        if delivery_coords and isinstance(delivery_coords, (list, tuple)) \
                and len(delivery_coords) == 2:
            coords_tuple = (float(delivery_coords[0]), float(delivery_coords[1]))
        if _raise_on_error:
            plan_manager.advance_plan(
                str(courier_id),
                str(order_id),
                delivered_at_raw or now_iso(),
                coords_tuple,
                _raise_on_corrupt=True,
            )
            # Retry po rename+fsync-dir failure moze byc store no-op. Jawny
            # ponowny fsync jest warunkiem zamkniecia durable receiptu.
            plan_manager.ensure_storage_durable()
        else:
            plan_manager.advance_plan(
                str(courier_id),
                str(order_id),
                delivered_at_raw or now_iso(),
                coords_tuple,
            )
    except Exception as e:
        _log.warning(f"V3.19b advance_plan fail cid={courier_id} oid={order_id}: {e}")
        if _raise_on_error:
            raise
    # RECANON: po dostawie (advance_plan usunął stop) re-egzekwuj kanon na RESZCIE
    # worka natychmiast — bez czekania na 5-min tick. No-op gdy worek pusty.
    try:
        from dispatch_v2 import plan_recheck
        if _raise_on_error:
            plan_recheck.recanon_courier(
                str(courier_id),
                reason="deliver",
                _raise_on_error=True,
                _enabled_by_receipt=_recanon_authorized_by_receipt,
            )
        else:
            plan_recheck.recanon_courier(str(courier_id), reason="deliver")
    except Exception as e:
        _log.warning(f"recanon-on-deliver fail cid={courier_id} oid={order_id}: {e}")
        if _raise_on_error:
            raise


def _recanon_after_plan_cleanup(
    courier_id: str,
    order_id: str,
    *,
    reason: str,
    _raise_on_error: bool = False,
    _recanon_authorized_by_receipt: Optional[bool] = None,
    _expected_order_generation: Optional[tuple[str, Optional[dict]]] = None,
) -> bool:
    """Druga, retryable faza cleanupu wykonywana poza lifecycle state lockiem."""
    try:
        from dispatch_v2 import plan_recheck

        if _raise_on_error:
            plan_recheck.recanon_courier(
                str(courier_id),
                reason=reason,
                _raise_on_error=True,
                _enabled_by_receipt=_recanon_authorized_by_receipt,
                _expected_order_generation=_expected_order_generation,
            )
        else:
            plan_recheck.recanon_courier(str(courier_id), reason=reason)
        return True
    except Exception as exc:
        _log.warning(
            f"recanon-on-{reason} fail cid={courier_id} oid={order_id}: {exc}"
        )
        if _raise_on_error:
            raise
        return False


def _remove_stops_on_return(
    courier_id: str,
    order_id: str,
    *,
    _raise_on_error: bool = False,
    _saved_plans_authorized_by_receipt: Optional[bool] = None,
    _recanon_authorized_by_receipt: Optional[bool] = None,
    _expected_plan_version: Optional[int] = None,
    _skip_recanon: bool = False,
) -> bool:
    """V3.19b cleanup; bool pozwala receiptowi wykryc best-effort failure."""
    try:
        from dispatch_v2.common import ENABLE_SAVED_PLANS
        saved_plans_enabled = (
            bool(ENABLE_SAVED_PLANS)
            if _saved_plans_authorized_by_receipt is None
            else bool(_saved_plans_authorized_by_receipt)
        )
        if not saved_plans_enabled:
            return True
    except Exception:
        if _raise_on_error:
            raise
        return False
    if not courier_id:
        return True
    succeeded = True
    try:
        from dispatch_v2 import plan_manager
        if _raise_on_error:
            try:
                remove_kwargs = {"_raise_on_corrupt": True}
                if _expected_plan_version is not None:
                    remove_kwargs["expected_version"] = _expected_plan_version
                plan_manager.remove_stops(
                    str(courier_id), str(order_id), **remove_kwargs
                )
            except plan_manager.ConcurrencyError:
                # Rename+fsync-dir crash może zostawić już wykonany efekt przy
                # starej wersji. Uznaj CAS conflict wyłącznie gdy targetu już
                # nie ma; nowszy plan nadal zawierający OID jest inną generacją.
                current_plan = plan_manager.load_plans(
                    _raise_on_corrupt=True
                ).get(str(courier_id))
                still_has_target = bool(
                    current_plan
                    and current_plan.get("invalidated_at") is None
                    and any(
                        str(stop.get("order_id")) == str(order_id)
                        for stop in current_plan.get("stops", [])
                    )
                )
                if still_has_target:
                    raise
        else:
            plan_manager.remove_stops(str(courier_id), str(order_id))
        # Retry po bledzie fsync katalogu moze zobaczyc juz podmieniony plik i
        # wykonac store no-op. Ponowny fsync dir jest wtedy warunkiem zamkniecia
        # durable receiptu, nie opcjonalna optymalizacja.
        plan_manager.ensure_storage_durable()
    except Exception as e:
        succeeded = False
        _log.warning(f"V3.19b remove_stops fail cid={courier_id} oid={order_id}: {e}")
        if _raise_on_error:
            raise
    if _skip_recanon:
        return succeeded
    # RECANON pozostaje osobna faza: durable lifecycle wywołuje ją po
    # zwolnieniu krótkiego state-generation locka.
    if not _recanon_after_plan_cleanup(
        str(courier_id),
        str(order_id),
        reason="return",
        _raise_on_error=_raise_on_error,
        _recanon_authorized_by_receipt=_recanon_authorized_by_receipt,
    ):
        succeeded = False
    return succeeded


def _release_plan_on_reassign(
    old_courier_id: str,
    order_id: str,
    *,
    _raise_on_error: bool = False,
    _authorized_by_receipt: bool = False,
    _saved_plans_authorized_by_receipt: Optional[bool] = None,
    _recanon_authorized_by_receipt: Optional[bool] = None,
    _expected_plan_version: Optional[int] = None,
    _skip_recanon: bool = False,
) -> bool:
    """REASSIGN-RELEASE (2026-07-20): po przerzuceniu zlecenia na INNEGO kuriera
    zwolnij plan STAREGO — lustrzane do _remove_stops_on_return (ta sama klasa:
    tranzycja KURCZĄCA worek → remove_stops PRZED recanon, protokół #0 „Recanon").

    Bug: branch reassign (panel_reassign) i PANEL_PACKS FALLBACK sygnalizowały
    TYLKO NOWEMU kurierowi (_save_plan_on_assign_signal) — courier_plans STAREGO
    dalej zawierał stop, plan_version stał → apka starego pokazywała zabrane
    zlecenie do fallbacku 180 s (PlanPoller.FULL_REFRESH_FALLBACK_MS) / 5-min
    plan_recheck. remove_stops bumpuje plan_version → /plan-version + SSE →
    apka starego robi pełny GET /orders natychmiast.

    Zwraca True, gdy feature AKTYWNY dla tej pary (flaga ON + SAVED_PLANS +
    niepusty cid). ``_raise_on_error`` jest prywatnym trybem durable-outboxa:
    błąd zostawia receipt do retry zamiast fałszywie zamknąć downstream.

    v3 (Sol flip-gate): idempotencja NIE tu, tylko U ŹRÓDŁA — remove_stops
    robi no-op (zero zapisu/bumpu) WEWNĄTRZ swojego exclusive locka, gdy plan
    nie zawiera stopa. Pre-check read-only w helperze (v2) usunięty: dwa osobne
    locki = TOCTOU (nowszy plan mógł wejść między odczyt a zapis).

    Za flagą ENABLE_REASSIGN_OLD_PLAN_RELEASE (default OFF, flip za ACK).
    Zwykły caller pozostaje best-effort; durable callback używa trybu strict
    i trwalej autoryzacji zapisanej w exact state_event. Dzieki temu OFF blokuje
    nowe intencje, ale nie ucina retry po czesciowo wykonanym efekcie.
    """
    try:
        from dispatch_v2.common import ENABLE_SAVED_PLANS, decision_flag
        saved_plans_enabled = (
            bool(ENABLE_SAVED_PLANS)
            if _saved_plans_authorized_by_receipt is None
            else bool(_saved_plans_authorized_by_receipt)
        )
        if not saved_plans_enabled:
            return False
        if (
            not _authorized_by_receipt
            and not decision_flag("ENABLE_REASSIGN_OLD_PLAN_RELEASE")
        ):
            return False
    except Exception:
        if _raise_on_error:
            raise
        return False
    if not old_courier_id:
        return False
    cid = str(old_courier_id)
    oid = str(order_id)
    try:
        from dispatch_v2 import plan_manager
        if _raise_on_error:
            try:
                remove_kwargs = {"_raise_on_corrupt": True}
                if _expected_plan_version is not None:
                    remove_kwargs["expected_version"] = _expected_plan_version
                plan_manager.remove_stops(cid, oid, **remove_kwargs)
            except plan_manager.ConcurrencyError:
                current_plan = plan_manager.load_plans(
                    _raise_on_corrupt=True
                ).get(cid)
                still_has_target = bool(
                    current_plan
                    and current_plan.get("invalidated_at") is None
                    and any(
                        str(stop.get("order_id")) == oid
                        for stop in current_plan.get("stops", [])
                    )
                )
                if still_has_target:
                    raise
        else:
            plan_manager.remove_stops(cid, oid)
        plan_manager.ensure_storage_durable()
        _log.info(
            f"REASSIGN-RELEASE cid_old={cid} oid={oid} "
            f"— plan starego zwolniony (remove_stops → bump plan_version)"
        )
    except Exception as e:
        _log.warning(
            f"REASSIGN-RELEASE remove_stops fail cid_old={cid} oid={oid}: {e}"
        )
        if _raise_on_error:
            raise
    if _skip_recanon:
        return True
    _recanon_after_plan_cleanup(
        cid,
        oid,
        reason="reassign_out",
        _raise_on_error=_raise_on_error,
        _recanon_authorized_by_receipt=_recanon_authorized_by_receipt,
    )
    return True


def _update_plan_on_picked_up(
    courier_id: str,
    order_id: str,
    picked_up_at: Optional[str] = None,
    *,
    _raise_on_error: bool = False,
    _saved_plans_authorized_by_receipt: Optional[bool] = None,
    _recanon_authorized_by_receipt: Optional[bool] = None,
    _redecide_authorized_by_receipt: Optional[bool] = None,
) -> None:
    """V3.19c sub A: po emit COURIER_PICKED_UP sukces. Update
    stop.status_at_plan_time + prune pickup stop (jeśli był).
    """
    try:
        from dispatch_v2.common import ENABLE_SAVED_PLANS
        saved_plans_enabled = (
            bool(ENABLE_SAVED_PLANS)
            if _saved_plans_authorized_by_receipt is None
            else bool(_saved_plans_authorized_by_receipt)
        )
        if not saved_plans_enabled:
            return
    except Exception:
        if _raise_on_error:
            raise
        return
    if not courier_id:
        return
    try:
        from dispatch_v2 import plan_manager
        if _raise_on_error:
            plan_manager.mark_picked_up(
                str(courier_id),
                str(order_id),
                picked_up_at,
                _raise_on_corrupt=True,
            )
            plan_manager.ensure_storage_durable()
        else:
            plan_manager.mark_picked_up(
                str(courier_id), str(order_id), picked_up_at
            )
    except Exception as e:
        _log.warning(f"V3.19c mark_picked_up fail cid={courier_id} oid={order_id}: {e}")
        if _raise_on_error:
            raise
    # Redecide natychmiast po ODEBRANE (zmiana stanu worka = zmiana bag_signature
    # F2): kanon zdecydowany tuż PRZED wpisem statusu (reconcile lag ~1 min)
    # zostawał z odbiorami przed niesionym aż do następnego 5-min ticku — case
    # Gabriel cid=179 11.06 (Mama Thai/Sushi przed dostawą 42PP, okno 17:03→17:08).
    # Samo-bramkujące w plan_recheck (no-op gdy sygnatura planu aktualna), flaga
    # ENABLE_IMMEDIATE_REDECIDE_ON_PICKUP (OFF). Best-effort — nie psuje pętli.
    try:
        from dispatch_v2 import plan_recheck
        # RECANON: po ODEBRANE floor-uj świeżo-niesioną dostawę na front (+ relax)
        # natychmiast — egzekwuje carried-first zanim apka/konsola pobiorą plan.
        if _raise_on_error:
            plan_recheck.recanon_courier(
                str(courier_id),
                reason="pickup",
                _raise_on_error=True,
                _enabled_by_receipt=_recanon_authorized_by_receipt,
            )
            plan_recheck.redecide_courier(
                str(courier_id),
                reason="pickup",
                _raise_on_error=True,
                _enabled_by_receipt=_redecide_authorized_by_receipt,
            )
        else:
            plan_recheck.recanon_courier(str(courier_id), reason="pickup")
            plan_recheck.redecide_courier(str(courier_id), reason="pickup")
    except Exception as e:
        _log.warning(f"redecide-on-pickup fail cid={courier_id} oid={order_id}: {e}")
        if _raise_on_error:
            raise


def _diff_czas_kuriera(old_state: dict, fresh_response: dict,
                      oid: str, deliberate: bool = False) -> Optional[dict]:
    """V3.19g1: detect czas_kuriera change for already-assigned order.

    deliberate=True (force-recheck na żądanie koordynatora, kolejka
    coordinator_time_recheck): pasywne strażniki (czasówka-passive, elastyk
    forward-only) są POMIJANE i source="coordinator_force" — klik człowieka to
    świadoma zmiana, ściągamy nowy czas w OBIE strony (state_machine też przepuści,
    bo coordinator_force ∉ _CK_PASSIVE_SOURCES). Próg szumu ±3min zostaje.

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

    # Source-block L1 (Adrian 2026-06-24, root #483023): dla CZASÓWKI umówiony
    # czas = pickup_at_warsaw. Gastro przestempluje `czas_kuriera` przy zmianie
    # statusu → ten pasywny re-odczyt to śmieć (16:22→15:04 5 s po assignie).
    # NIE emituj (żeby nie odpalić FIX-E „apka odświeży widok" + audit na bzdurze).
    # Autorytatywny bliźniak: state_machine CZAS_KURIERA_UPDATED (_CK_PASSIVE_SOURCES).
    # Zmiana umówionego czasu czasówki idzie kanałem pickup_at (PICKUP_TIME_UPDATED).
    _is_czas = C.is_czasowka_order(old_state)
    try:
        from dispatch_v2.common import flag as _flag
    except Exception:
        _flag = None
    if _is_czas:
        # Czasówka: committed = pickup_at, a czas_kuriera to przeklepywany przez gastro
        # ŚMIEĆ (re-stamp na zmianie statusu) — guard suppress ZAWSZE, także przy deliberate
        # (force koordynatora ściąga czasówkę kanałem pickup_at → PICKUP_TIME_UPDATED, który
        # mirroruje na czas_kuriera). Bypass tu pociągnąłby śmieciowy czas_kuriera.
        _guard = _flag("ENABLE_CZASOWKA_CK_PASSIVE_GUARD", True) if _flag else True
        if _guard:
            # Incydent #489052: gastro wystawia marker recznej zmiany czasu.
            # Wspolny, fail-closed classifier ze state_machine dopuszcza tylko
            # krawedz False->True przy stabilnym pickup/statusie i zwraca
            # kanoniczny PICKUP_TIME_UPDATED (nie bezposredni writer CK).
            _manual_payload = {
                "oid": oid,
                "courier_id": old_state.get("courier_id"),
                "old_ck_iso": old_ck_iso,
                "old_ck_hhmm": old_ck_hhmm,
                "new_ck_iso": new_ck_iso,
                "new_ck_hhmm": new_ck_hhmm,
                "delta_min": round(delta_min, 2),
                "source": "coordinator_force" if deliberate else "panel_re_check",
                "new_zmiana_czasu_odbioru": fresh_response.get(
                    "zmiana_czasu_odbioru"
                ),
                "observed_pickup_at_warsaw": fresh_response.get(
                    "pickup_at_warsaw"
                ),
                "observed_status_id": fresh_response.get("status_id"),
                "observed_prep_minutes": fresh_response.get("prep_minutes"),
                "observed_decision_deadline": fresh_response.get(
                    "decision_deadline"
                ),
                "assignment_event_id_at_observation": old_state.get(
                    "assignment_event_id"
                ),
                "courier_id_at_observation": old_state.get("courier_id"),
            }
            _manual_evt = build_czasowka_manual_ck_pickup_event(
                old_state, _manual_payload
            )
            if _manual_evt is not None:
                return _manual_evt
            _log.info(
                f"CK_PASSIVE_SUPPRESSED oid={oid} czasówka ck "
                f"{old_ck_hhmm}→{new_ck_hhmm} Δ={delta_min:+.1f}min "
                f"src={'coordinator_force' if deliberate else 'panel_re_check'} "
                f"— committed=pickup_at, gastro re-stamp ignorowany (no emit)"
            )
            return None
    else:
        # Elastyk forward-only (Adrian 2026-06-24, opcja B): pasywny re-odczyt
        # NIE cofa committed czas_kuriera („przyjazd wcześniej niż umówiono" =
        # wobble ETA). Forward (koordynatorski +15 / spóźnienie) przechodzi.
        # deliberate (klik koordynatora) omija — to świadoma zmiana, nie szum.
        _eguard = _flag("ENABLE_ELASTYK_CK_NO_BACKWARD", True) if _flag else True
        if _eguard and delta_min < 0 and not deliberate:
            _log.info(
                f"CK_ELASTYK_BACKWARD_BLOCKED oid={oid} ck {old_ck_hhmm}→{new_ck_hhmm} "
                f"Δ={delta_min:+.1f}min src=panel_re_check — elastyk forward-only, "
                f"nie cofamy (no emit)"
            )
            return None

    payload = {
        "oid": oid,
        "courier_id": old_state.get("courier_id"),
        "old_ck_iso": old_ck_iso,
        "old_ck_hhmm": old_ck_hhmm,
        "new_ck_iso": new_ck_iso,
        "new_ck_hhmm": new_ck_hhmm,
        "delta_min": round(delta_min, 2),
        "source": "coordinator_force" if deliberate else "panel_re_check",
    }
    return {
        "event_type": "CZAS_KURIERA_UPDATED",
        "order_id": oid,
        "courier_id": old_state.get("courier_id"),
        "payload": payload,
    }


def _diff_pickup_time(old_state: dict, fresh_response: dict,
                      oid: str, deliberate: bool = False) -> Optional[dict]:
    """Detect pickup_at_warsaw change (restaurant-declared pickup time).

    deliberate=True (force-recheck koordynatora): source="coordinator_force"
    (audyt). Kanał pickup_at i tak nie ma strażnika kierunku — zmienia w obie
    strony; deliberate jedynie znakuje źródło i pełni rolę dla czasówek (mirror
    pickup→czas_kuriera w state_machine).

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
        "source": "coordinator_force" if deliberate else "panel_re_check",
        # Causal snapshot: durable payload dowodzi, ze ten time-event powstal
        # w tej samej generacji assignmentu i przy tym samym kurierze.
        "assignment_event_id_at_observation": old_state.get(
            "assignment_event_id"
        ),
        "courier_id_at_observation": old_state.get("courier_id"),
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


def _time_update_event_key(order_id: str, event: dict) -> str:
    """Stabilny klucz intencji czasu, zwiazany z calym przejsciem.

    Sama delta nie identyfikuje przejscia: 12:00->12:05 i 12:05->12:10 maja
    identyczne ``delta_min``. Klucz zachowuje historyczny, czytelny prefiks,
    ale jego digest obejmuje stare i docelowe pola semantyczne zapisywane przez
    dany event. Retry tego samego przejscia pozostaje idempotentny, kolejny cel
    lub powrot do wczesniejszego celu jest osobna trwala intencja.
    """
    event_type = str(event.get("event_type") or "")
    payload = event.get("payload") or {}
    transition_fields = {
        "CZAS_KURIERA_UPDATED": (
            "old_ck_iso",
            "old_ck_hhmm",
            "new_ck_iso",
            "new_ck_hhmm",
            "source",
        ),
        "PICKUP_TIME_UPDATED": (
            "old_pickup_at_warsaw",
            "new_pickup_at_warsaw",
            "old_prep_minutes",
            "new_prep_minutes",
            "new_decision_deadline",
            "new_zmiana_czasu_odbioru",
            "source",
            "assignment_event_id_at_observation",
            "courier_id_at_observation",
        ),
    }
    if event_type not in transition_fields:
        raise ValueError(f"unsupported time update event_type: {event_type!r}")

    transition = {
        field: payload.get(field) for field in transition_fields[event_type]
    }
    transition_json = json.dumps(
        transition,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    transition_digest = hashlib.sha256(
        transition_json.encode("utf-8")
    ).hexdigest()

    suffix = event.get("event_id_suffix")
    if suffix:
        legacy_discriminator = str(suffix)
    else:
        legacy_discriminator = f"_{int(float(payload.get('delta_min', 0)) * 10)}"
    return (
        f"{order_id}_{event_type}{legacy_discriminator}"
        f"_to_{transition_digest}"
    )


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


class _TickOverlapTracker:
    """TICK-OVERLAP-05 (Front C audytu 03.06, 2026-06-12): metryka headroomu
    ticka elapsed/interval. Sygnał WYPRZEDZAJĄCY (log WARNING, ZERO Telegrama):
    ratio>0.8 znaczy że tick zjada interwał i przy wzroście ruchu ticki polecą
    back-to-back (sleep 0.5 s) → opóźniona detekcja nowych zleceń + więcej
    requestów/min do panelu (ryzyko 419). Baseline 2026-06-12: p50=7.4 s,
    p95=23.7 s przy interval=20 s (p95 JUŻ nad interwałem w peak).

    Czysta logika (testowalna): note() per tick, summary_fragment() per SUMMARY
    (resetuje okno). Warning rate-limited (1/5 min) żeby peak nie zalał loga.
    """

    WARN_RATIO = 0.8
    WARN_COOLDOWN_S = 300.0

    def __init__(self):
        self.window_max = 0.0
        self.over_count = 0
        self.tick_count = 0
        self.last_ratio = 0.0
        self._last_warn_at = 0.0

    def note(self, elapsed: float, interval: float, now: float = None) -> str:
        """Rejestruje tick. Zwraca tekst WARNING do zalogowania albo ''."""
        now = time.time() if now is None else now
        ratio = (elapsed / interval) if interval > 0 else 0.0
        self.last_ratio = ratio
        self.tick_count += 1
        if ratio > self.window_max:
            self.window_max = ratio
        if ratio <= self.WARN_RATIO:
            return ""
        self.over_count += 1
        if now - self._last_warn_at < self.WARN_COOLDOWN_S:
            return ""
        self._last_warn_at = now
        return (f"TICK_OVERLAP ratio={ratio:.2f} (elapsed={elapsed:.1f}s / "
                f"interval={interval:.0f}s) > {self.WARN_RATIO} — tick zjada "
                f"headroom, przy 2x ruchu ticki polecą back-to-back "
                f"(TICK-OVERLAP-05; sygnał wyprzedzający, bez Telegrama)")

    def summary_fragment(self) -> str:
        """Fragment do SUMMARY; resetuje okno (wołać raz na SUMMARY)."""
        frag = (f"ratio_last={self.last_ratio:.2f}, "
                f"ratio_max={self.window_max:.2f}, "
                f"over{self.WARN_RATIO}={self.over_count}/{self.tick_count}")
        self.window_max = 0.0
        self.over_count = 0
        self.tick_count = 0
        return frag


def _build_prefetch_candidates(parsed: dict, current_state: dict, ignored_ids,
                               freeze_new: bool, ck_detection_on: bool,
                               pickup_time_detection_on: bool) -> list:
    """PANEL-SCRAPE-01 (2026-06-12): lista zid, które bieżący tick i tak
    fetchowałby sekwencyjnie — kandydaci do równoległego pre-fetchu.

    Czysta funkcja (testowalna). Lustrzane predykaty pętli _diff_and_emit:
      1. NOWE: w HTML, nieznane w state, nie-ignorowane (chyba że freeze_new).
      2. ZNIKNIĘTE: aktywne w state, nieobecne w HTML (details → status 7/8/9?).
      3. planned→assigned: w HTML, state nie-assigned, panel pokazuje przypisanie.
      4. Scope re-checku czasów (V319G/PICKUP_TIME): assigned/picked_up
         + planned czasówki obecne w HTML — to jest WIĘKSZOŚĆ fetchy/tick
         (mediana 42, zmierzone 2026-06-12).
    Pętle budżetowane (reassign≤5, packs, ghost, pu_reconcile≤10, closed
    reconcile) celowo POZA prefetchem — mały wolumen, zostają sekwencyjne.
    """
    html_order_ids = set(parsed.get("order_ids") or [])
    assigned_in_panel = parsed.get("assigned_ids") or set()
    out = []
    if not freeze_new:
        for zid in parsed.get("order_ids") or []:
            if zid not in current_state and zid not in ignored_ids:
                out.append(zid)
    for zid, so in current_state.items():
        status = so.get("status")
        if status in ("delivered", "returned_to_pool", "cancelled"):
            continue
        if so.get("source") == "parcel":
            continue  # PACZKI (Etap 3): własny tor (parcel_lane), NIGDY prefetch gastro
        if zid not in html_order_ids:
            out.append(zid)
            continue
        if status != "assigned" and zid in assigned_in_panel:
            out.append(zid)
        if ck_detection_on or pickup_time_detection_on:
            is_czasowka = C.is_czasowka_order(so)
            if status in ("assigned", "picked_up") or (status == "planned" and is_czasowka):
                out.append(zid)
    return list(dict.fromkeys(out))


def _sweep_state_apply_outbox(*, enabled: Optional[bool] = None) -> dict:
    """Lekki recovery step; OFF nie dodaje I/O ponad legacy drain po fetchu."""
    try:
        if enabled is None:
            enabled = decision_flag("ENABLE_STATE_OUTBOX_SWEEPER")
        if not enabled:
            return {}
        min_age_s = max(0.0, float(C.STATE_OUTBOX_SWEEPER_MIN_AGE_S))
        recovered = durable_event_apply.drain_pending(
            state_update_fn=update_from_event,
            effect_status_fn=event_effect_status,
            get_order_fn=state_get_order_strict,
            downstream_fn=lifecycle_downstream.apply,
            limit=100,
            min_age_seconds=min_age_s,
        )
        stats = {
            "durable_apply_seen": recovered.get("seen", 0),
            "durable_apply_recovered": recovered.get("state_ready", 0),
            "durable_downstream_recovered": recovered.get("downstream", 0),
            "durable_apply_superseded": recovered.get("superseded", 0),
            "durable_apply_failed": recovered.get("failed", 0),
            "state_outbox_sweeper_completed": recovered.get("completed", 0),
        }
        stats["errors"] = stats["durable_apply_failed"]
        if any(
            stats[key]
            for key in (
                "durable_apply_seen",
                "durable_downstream_recovered",
                "durable_apply_superseded",
                "durable_apply_failed",
                "state_outbox_sweeper_completed",
            )
        ):
            _log.info(
                "STATE_OUTBOX_SWEEPER completed=%d seen=%d state_ready=%d "
                "downstream=%d superseded=%d failed=%d min_age_s=%.1f",
                stats["state_outbox_sweeper_completed"],
                stats["durable_apply_seen"],
                stats["durable_apply_recovered"],
                stats["durable_downstream_recovered"],
                stats["durable_apply_superseded"],
                stats["durable_apply_failed"],
                min_age_s,
            )
        return stats
    except Exception as exc:
        _log.error(
            f"STATE_OUTBOX_SWEEPER failed: {type(exc).__name__}: {exc}"
        )
        return {
            "durable_apply_failed": 1,
            "state_outbox_sweeper_completed": 0,
            "errors": 1,
        }


def _diff_and_emit(
    parsed: dict,
    csrf: str,
    *,
    _state_outbox_sweeper_on: Optional[bool] = None,
) -> dict:
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

    # Zachowaj baseline OFF: dotychczasowy opportunistyczny drain biegl po
    # udanym fetchu/parse. Flaga ON przenosi recovery na poczatek ticku, dodaje
    # age gate i dziala takze przy awarii panelu; nie wolno wtedy wykonywac
    # drugiego, nieograniczonego wiekiem drainu w tym samym cyklu.
    if _state_outbox_sweeper_on is None:
        try:
            _state_outbox_sweeper_on = decision_flag(
                "ENABLE_STATE_OUTBOX_SWEEPER"
            )
        except Exception:
            _state_outbox_sweeper_on = False
    if not _state_outbox_sweeper_on:
        try:
            recovered = durable_event_apply.drain_pending(
                state_update_fn=update_from_event,
                effect_status_fn=event_effect_status,
                get_order_fn=state_get_order_strict,
                downstream_fn=lifecycle_downstream.apply,
                limit=100,
            )
            stats["durable_apply_seen"] = recovered["seen"]
            stats["durable_apply_recovered"] = recovered["state_ready"]
            stats["durable_downstream_recovered"] = recovered["downstream"]
            stats["durable_apply_superseded"] = recovered["superseded"]
            stats["durable_apply_failed"] = recovered["failed"]
            stats["errors"] += recovered["failed"]
        except Exception as exc:
            stats["durable_apply_failed"] = 1
            stats["errors"] += 1
            _log.error(
                f"DURABLE_APPLY drain failed: {type(exc).__name__}: {exc}"
            )

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

    # PANEL-SCRAPE-01 (2026-06-12): równoległy pre-fetch detali, które ten tick
    # i tak by fetchował sekwencyjnie (~0.3 s/szt na głównej sesji, mediana
    # 42/tick → ~12.6 s z 20 s interwału). Osobne sesje per wątek — główna
    # sesja NIETKNIĘTA (NIGDY: edit-zamowienie na głównej sesji sekwencyjnie).
    # Miss → sekwencyjny fallback w _details() = zachowanie sprzed zmiany.
    # Kill-switch hot-reload: ENABLE_PANEL_DETAIL_PREFETCH w flags.json.
    _prefetch_map = {}
    try:
        from dispatch_v2 import panel_detail_prefetch as _pdp
        try:
            from dispatch_v2.common import (
                ENABLE_V319G_CK_DETECTION as _pf_ck,
                ENABLE_PICKUP_TIME_DETECTION as _pf_pt)
        except Exception:
            _pf_ck = _pf_pt = False
        _pf_zids = _build_prefetch_candidates(
            parsed, current_state, _ignored_ids, _freeze_new, _pf_ck, _pf_pt)
        _prefetch_map, _pf_stats = _pdp.prefetch_details(_pf_zids)
        if _pf_stats.get("prefetch_enabled"):
            for _pk in ("prefetch_requested", "prefetch_fetched",
                        "prefetch_errors", "prefetch_s"):
                stats[_pk] = _pf_stats[_pk]
    except Exception as _pfe:
        _log.warning(f"PANEL-SCRAPE-01 prefetch fail (non-blocking, full "
                     f"fallback): {type(_pfe).__name__}: {_pfe}")
        _prefetch_map = {}

    def _details(zid):
        """Detal zlecenia: hit z prefetchu (już pobrany równolegle) albo
        sekwencyjny fetch na głównej sesji (miss — identyczna semantyka co
        przed PANEL-SCRAPE-01). Wartość None W mapie = legit odpowiedź panelu
        bez 'zlecenie' (nie ponawiamy)."""
        if zid in _prefetch_map:
            stats["prefetch_hits"] = stats.get("prefetch_hits", 0) + 1
            return _prefetch_map[zid]
        return fetch_order_details(zid, csrf)

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

        # Nowe ID - fetch details i normalize (PANEL-SCRAPE-01: prefetch-first)
        try:
            raw = _details(zid)
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
        _is_firmowe_konto = (
            _aid is not None
            and int(_aid) in FIRMOWE_KONTO_ADDRESS_IDS
        )
        # FRONT-B (2026-06-11): zwykłe restauracje → helper (geokod adresu
        # panelu liczony zawsze + drift; selekcja live-first za flagą OFF).
        # Firmowe konto ZOSTAJE na starym lookupie — pickup w uwagach (niżej).
        if _is_firmowe_konto:
            _pcoords = _COORDS.get(_aid_str) if _aid_str else None
        else:
            _pcoords, _pc_source, _pc_drift_m = _resolve_pickup_coords(
                _aid_str, norm.get("pickup_address"), norm.get("pickup_city"),
                zid=zid)

        # Firmowe konto path: address_id ∈ FIRMOWE_KONTO_ADDRESS_IDS znaczy
        # adres pickup'u jest w polu uwagi (free-text), nie w panel address.
        # Parser PRIMARY: parsuj uwagi → geocode → real coords (Mickiewicza 50,
        # Wyszyńskiego 2/75, etc.). Legacy fallback do centrali obowiązuje tylko
        # przy efektywnym ENABLE_FIRMOWE_REJECT_ON_GEOCODE_FAIL=OFF. P0 bridge
        # wolno uruchomić wyłącznie razem z reject=ON, więc bridge-geocode FAIL
        # zawsze zostawia None → jawny KOORD, nigdy wiarygodnie-złą centralę.
        _uwagi_pickup_parsed = None
        _pickup_address_override = None
        _restaurant_override = None
        _bridge_metric_enabled = False
        _bridge_attempt = None
        _bridge_metric_reason = "not_evaluated"
        _bridge_parsed = False
        _bridge_geocode_ok = False
        if (_pcoords is None
                and _is_firmowe_konto
                and flag("ENABLE_UWAGI_ADDRESS_PARSER", True)):
            _uwagi_text = norm.get("uwagi")
            _bridge_requested = flag("ENABLE_UWAGI_BRIDGE_NADAWCA", False)
            _reject_on_geocode_fail = flag(
                "ENABLE_FIRMOWE_REJECT_ON_GEOCODE_FAIL", True
            )
            _bridge_format = C.uwagi_bridge_flags_coherent(
                bridge_enabled=_bridge_requested,
                reject_enabled=_reject_on_geocode_fail,
            )
            _bridge_metric_enabled = bool(_bridge_requested)
            if _bridge_requested and not _reject_on_geocode_fail:
                _log.warning(
                    f"NEW_ORDER {zid} firmowe-konto aid={_aid}: "
                    "ENABLE_UWAGI_BRIDGE_NADAWCA=ON, ale efektywne "
                    "ENABLE_FIRMOWE_REJECT_ON_GEOCODE_FAIL=OFF; "
                    "bridge_format=False (sprzężenie fail-closed)"
                )
            _bridge_hmac_material = None
            if _bridge_format:
                try:
                    _bridge_hmac_material = load_bridge_hmac()
                except BridgeCredentialError as exc:
                    _log.error(
                        "NEW_ORDER %s firmowe-konto aid=%s: bridge HMAC "
                        "niedostępny (%s); fail-closed do legacy/KOORD",
                        zid,
                        _aid,
                        type(exc).__name__,
                    )
                # Anti-replay: no independent expected_order_id here — the
                # panel `zid` lives in a different namespace than the bridge's
                # source `#order_id`, so binding is enforced by the envelope
                # itself (signed-oid must match the content `#oid` = internal
                # consistency, plus the freshness window). A captured envelope
                # is thus non-eternal and any tamper breaks the signature.
                _bridge_attempt = inspect_bridge_nadawca(
                    _uwagi_text,
                    hmac_material=_bridge_hmac_material,
                )
                _bridge_metric_reason = _bridge_attempt.reason
                if _bridge_attempt.pickup is not None:
                    _parsed = _bridge_attempt.pickup
                elif _bridge_attempt.envelope_seen:
                    _parsed = None
                else:
                    _parsed = parse_pickup_from_uwagi(
                        _uwagi_text,
                        bridge_format=False,
                    )
            else:
                if _bridge_requested:
                    _bridge_attempt = inspect_bridge_nadawca(_uwagi_text)
                    _bridge_metric_reason = "binding_reject_flag_off"
                if _bridge_attempt is not None and _bridge_attempt.envelope_seen:
                    # Incoherent ON/OFF configuration must not silently parse a
                    # signed bridge payload through the unauthenticated legacy path.
                    _parsed = None
                else:
                    _parsed = parse_pickup_from_uwagi(
                        _uwagi_text,
                        bridge_format=False,
                    )
            _bridge_rejection_reason = None
            if (_parsed is None and _bridge_attempt is not None
                    and _bridge_attempt.envelope_seen):
                _bridge_rejection_reason = _bridge_attempt.reason
            _bridge_envelope_rejected = bool(
                _bridge_attempt is not None
                and _bridge_attempt.envelope_seen
                and _bridge_attempt.pickup is None
            )
            if _parsed is not None:
                _bridge_parsed = bool(
                    _bridge_attempt is not None
                    and _bridge_attempt.pickup is not None
                )
                _pickup_address_override = f"{_parsed.street} {_parsed.number}"
                _pcoords = geocode(_pickup_address_override,
                                   city=(getattr(_parsed, "city", None) or "Białystok"),
                                   timeout=2.0)
                if (_pcoords is not None
                        and not C.coords_in_bialystok_bbox(_pcoords)):
                    _log.error(
                        "NEW_ORDER %s firmowe-konto aid=%s: geocode poza bbox; "
                        "REJECT+FLAG (→ no_pickup_geocode/KOORD)",
                        zid,
                        _aid,
                    )
                    _pcoords = None
                    if _bridge_parsed:
                        _bridge_metric_reason = "geocode_out_of_bbox"
                if _pcoords is None:
                    if _bridge_parsed and _bridge_metric_reason == "parsed_v2":
                        _bridge_metric_reason = "geocode_failed"
                    if _reject_on_geocode_fail:
                        # FAZA 2 #1: reject+flag — znamy adres, geocode padł →
                        # NIE udawaj że to centrala. None → no_pickup_geocode → KOORD.
                        _log.error(
                            f"NEW_ORDER {zid} firmowe-konto aid={_aid}: parser OK "
                            f"({_pickup_address_override!r} conf={_parsed.confidence}) "
                            f"ALE geocode FAIL — REJECT+FLAG (→ no_pickup_geocode/KOORD), "
                            f"NIE podstawiam centrali"
                        )
                        # _pcoords zostaje None → downstream defense gate → KOORD
                    else:
                        _log.warning(
                            f"NEW_ORDER {zid} firmowe-konto aid={_aid}: parser "
                            f"OK ({_pickup_address_override!r} conf={_parsed.confidence}) "
                            f"ALE geocode fail — fallback do FIRMOWE_KONTO_FALLBACK_COORDS"
                        )
                        _pcoords = tuple(FIRMOWE_KONTO_FALLBACK_COORDS)
                else:
                    if _bridge_parsed:
                        _bridge_geocode_ok = True
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
                if _bridge_parsed and _pcoords is None:
                    # Persist the provenance-aware reject so downstream twins
                    # cannot re-geocode or revive the central fallback.
                    _uwagi_pickup_parsed["bridge_envelope_rejected"] = True
            else:
                # P3 edge: parser nie wyciągnął adresu (np. uwagi=company-only
                # "MALI WOJOWNICY"). FAZA 2 #1: reject+flag — bez adresu NIE
                # zgadujemy centrali; koordynator ustala adres (None → KOORD).
                if _reject_on_geocode_fail or _bridge_envelope_rejected:
                    _log.error(
                        f"NEW_ORDER {zid} firmowe-konto aid={_aid}: parser zwrócił "
                        f"None (P3 edge) — REJECT+FLAG (→ no_pickup_geocode/KOORD), "
                        f"NIE podstawiam centrali; "
                        f"bridge_reason={_bridge_rejection_reason!r}. "
                        f"Uwagi: {'<bridge-envelope-redacted>' if _bridge_envelope_rejected else repr(_uwagi_text)}"
                    )
                    _pcoords = None
                    _uwagi_pickup_parsed = {
                        "street": None, "number": None, "company": None,
                        "confidence": 0.0,
                        "raw_pickup_line": (
                            "<bridge-envelope-redacted>"
                            if _bridge_envelope_rejected else (_uwagi_text or "")
                        ),
                        "geocode_rejected": True,
                    }
                    if _bridge_envelope_rejected:
                        _uwagi_pickup_parsed["bridge_envelope_rejected"] = True
                else:
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

        if _bridge_metric_enabled:
            _write_uwagi_bridge_shadow_metric(
                zid,
                envelope_seen=bool(
                    _bridge_attempt and _bridge_attempt.envelope_seen
                ),
                version=(
                    _bridge_attempt.version if _bridge_attempt is not None else None
                ),
                reason=_bridge_metric_reason,
                parsed=_bridge_parsed,
                geocode_ok=_bridge_geocode_ok,
                central_fallback=(
                    _pcoords is not None
                    and tuple(_pcoords) == tuple(FIRMOWE_KONTO_FALLBACK_COORDS)
                ),
            )

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
        result = _emit_and_apply_state(
            "NEW_ORDER",
            order_id=zid,
            payload=ev_payload,
            event_id=event_id,
        )
        if not result.state_ready:
            stats["errors"] += 1
        if result.event_created:
            stats["new"] += 1
        if result.event_created and result.state_ready:
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
            assigned_event = _emit_and_apply_state(
                "COURIER_ASSIGNED",
                order_id=zid,
                courier_id=courier_id,
                payload=_assigned_payload,
                # Jedna semantyczna tozsamosc dla wszystkich writerow
                # assignment. Source zostaje w payloadzie; powrot 100->200->100
                # dostaje nowa generacje durable outbox pod tym samym kluczem.
                event_id=f"{zid}_COURIER_ASSIGNED_{courier_id}_canonical",
                audit=True,
            )
            if not assigned_event.state_ready:
                stats["errors"] += 1
            if assigned_event.event_created:
                stats["assigned"] += 1

    # 2. ZMIANY: ID znane w state, sprawdz czy cos sie zmienilo
    # V3.15 pre-req fix: reassign_checked/MAX_REASSIGN_PER_CYCLE musi być
    # zainicjalizowane PRZED pętlą (używane w L330-335). Wcześniej init był
    # po pętli (L364-365) → UnboundLocalError przy każdym tick → całe
    # _diff_and_emit failowało, blokując m.in. V3.15 packs fallback.
    MAX_REASSIGN_PER_CYCLE = 5
    reassign_checked = 0
    # REASSIGN-RELEASE v2 (Sol review 50f5946): oba tory wykrycia przerzutu
    # (branch reassign niżej + PANEL_PACKS FALLBACK) czytają TEN SAM snapshot
    # current_state (stale po update_from_event) i mają RÓŻNE event_id → jeden
    # realny przerzut = podwójna obsługa w jednym ticku. Zbiór (oid, stary_cid)
    # zasilany TYLKO gdy helper realnie aktywny (flaga ON) → przy OFF pusty =
    # packs zachowuje się bajt-w-bajt jak dziś.
    released_this_tick = set()
    try:
        _old_plan_release_on = decision_flag(
            "ENABLE_REASSIGN_OLD_PLAN_RELEASE"
        )
    except Exception:
        _old_plan_release_on = False
    for zid, state_order in list(current_state.items()):
        # Pomijamy terminalne (delivered, cancelled) - nie obserwujemy ich dalej
        if state_order.get("status") in ("delivered", "returned_to_pool", "cancelled"):
            continue

        # PACZKI (Etap 3): mają własny tor (parcel_lane), NIE ma ich w gastro HTML — watcher
        # ich NIE dotyka (bez fetch gastro „disappeared", bez fałszywej detekcji terminalnej).
        if state_order.get("source") == "parcel":
            continue

        # Czy zlecenie nadal widoczne w panelu?
        if zid not in html_order_ids:
            # Zniknelo - moze zostalo zakonczone lub anulowane
            # Sprawdzmy details zeby wiedziec (PANEL-SCRAPE-01: prefetch-first)
            try:
                raw = _details(zid)
                stats["fetched_details"] += 1
                if raw:
                    status_id = raw.get("id_status_zamowienia")
                    if status_id == 7:
                        # Doreczone (F10 2026-05-09: canonical event_id eliminuje
                        # duplicate audit_log entries vs ghost_detect/reconcile path).
                        _delivered_ts = raw.get("czas_doreczenia") or now_iso()
                        _delivered_payload = {
                            "timestamp": _delivered_ts,
                            "final_location": state_order.get("delivery_address"),
                            "delivery_address": state_order.get("delivery_address"),
                            "deliv_source": "panel",
                        }
                        ev = _emit_and_apply_state(
                            "COURIER_DELIVERED",
                            order_id=zid,
                            courier_id=str(raw.get("id_kurier") or ""),
                            payload=_delivered_payload,
                            event_id=f"{zid}_COURIER_DELIVERED_canonical",
                        )
                        if not ev.state_ready:
                            stats["errors"] += 1
                        if ev.event_created:
                            stats["delivered"] += 1
                        if ev.downstream_executed:
                            _log.info(f"DELIVERED {zid}")
                    elif status_id in (8, 9):
                        # TASK 2 Część A (2026-05-04): mirror reconcile path L960.
                        # Pre-fix: upsert_order(status='cancelled') aktualizował state
                        # ale NIE emitował do events.db → akumulacja phantom orders.
                        reason = "undelivered" if status_id == 8 else "cancelled"
                        _adv_cid = str(raw.get("id_kurier") or "")
                        _returned_payload = {
                            "reason": reason,
                            "source": "panel_diff",
                        }
                        _returned_state_payload = None
                        if _old_plan_release_on:
                            _returned_state_payload = dict(_returned_payload)
                            _snapshot_cid = str(
                                state_order.get("courier_id") or ""
                            )
                            if _snapshot_cid:
                                _returned_state_payload[
                                    "return_snapshot_cleanup_courier_id"
                                ] = _snapshot_cid
                        ev = _emit_and_apply_state(
                            "ORDER_RETURNED_TO_POOL",
                            order_id=zid,
                            courier_id=_adv_cid,
                            payload=_returned_payload,
                            state_payload=_returned_state_payload,
                            event_id=f"{zid}_ORDER_RETURNED_{reason}_canonical",
                            audit=True,
                            old_plan_release_authorized=_old_plan_release_on,
                        )
                        if not ev.state_ready:
                            stats["errors"] += 1
                        if ev.downstream_executed:
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
            # Fetch details zeby wiedziec ktory kurier (PANEL-SCRAPE-01)
            try:
                raw = _details(zid)
                stats["fetched_details"] += 1
                if raw and raw.get("id_kurier") and raw["id_kurier"] != KOORDYNATOR_ID:
                    courier_id = str(raw["id_kurier"])
                    ev = _emit_and_apply_state(
                        "COURIER_ASSIGNED",
                        order_id=zid,
                        courier_id=courier_id,
                        payload={"source": "panel_diff"},
                        event_id=f"{zid}_COURIER_ASSIGNED_{courier_id}_canonical",
                        audit=True,
                    )
                    if not ev.state_ready:
                        stats["errors"] += 1
                    if ev.event_created:
                        stats["assigned"] += 1
                    if ev.downstream_executed:
                        _log.info(f"ASSIGNED {zid} -> {courier_id}")
            except Exception as e:
                _log.warning(f"fetch for assigned {zid}: {e}")
                stats["errors"] += 1

        # Reassignment: kurier zmieniony na already-assigned order (F2.1c)
        elif was_assigned and is_assigned_now and reassign_checked < MAX_REASSIGN_PER_CYCLE:
            # v2 (Sol review): normalizacja do str jak w twin-torze packs —
            # int 207 w state vs "207" z panelu robił FAŁSZYWY reassign
            # (duplikat emit; z release'em zdarłby stop AKTUALNEMU kurierowi).
            state_courier = str(state_order.get("courier_id") or "")
            try:
                raw = fetch_order_details(zid, csrf)
                stats["fetched_details"] += 1
                reassign_checked += 1
                panel_courier = str(raw.get("id_kurier") or "") if raw else ""
                if panel_courier and panel_courier != state_courier and raw.get("id_kurier") != KOORDYNATOR_ID:
                    ev = _emit_and_apply_state(
                        "COURIER_ASSIGNED",
                        order_id=zid,
                        courier_id=panel_courier,
                        payload={"source": "panel_reassign"},
                        event_id=f"{zid}_COURIER_ASSIGNED_{panel_courier}_canonical",
                        audit=True,
                        old_plan_release_authorized=_old_plan_release_on,
                    )
                    if not ev.state_ready:
                        stats["errors"] += 1
                    if ev.event_created:
                        stats["assigned"] += 1
                    if ev.downstream_executed:
                        _log.info(f"REASSIGNED {zid} {state_courier} -> {panel_courier}")
                        if (
                            bool(
                                (getattr(ev, "state_event", None) or {}).get(
                                    "reassign_old_plan_release_authorized"
                                )
                            )
                            and state_courier
                            and state_courier != panel_courier
                        ):
                            # Durable callback wykonał już event-local cleanup
                            # starego planu (za istniejącą flagą) i sygnał nowego.
                            # Snapshot current_state jest stale do końca ticku;
                            # nie pozwól PANEL_PACKS ponownie fetchować tej samej
                            # kanonicznej intencji.
                            released_this_tick.add((zid, state_courier))
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
                    if (_oid_str, _state_cid) in released_this_tick:
                        # v2 (Sol review): branch reassign obsłużył TEN przerzut
                        # w TYM ticku (release+signal już poszły; snapshot state
                        # jest stale) — nie dubluj fetch/emit/release/signal.
                        # Zbiór niepusty TYLKO przy fladze ON → OFF bajt-w-bajt.
                        continue
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
                    _ev = _emit_and_apply_state(
                        "COURIER_ASSIGNED",
                        order_id=_oid_str,
                        courier_id=_target_cid,
                        payload={
                            "source": "packs_fallback",
                            "previous_cid": _state_cid or None,
                            "nick": _nick_key,
                        },
                        state_payload={"source": "packs_fallback"},
                        event_id=f"{_oid_str}_COURIER_ASSIGNED_{_target_cid}_canonical",
                        audit=True,
                        old_plan_release_authorized=_old_plan_release_on,
                    )
                    if not _ev.state_ready:
                        stats["errors"] += 1
                    if _ev.event_created:
                        stats["assigned"] += 1
                        _packs_catchup += 1
                    if _ev.downstream_executed:
                        _log.info(
                            f"PACKS_CATCHUP {_oid_str} → cid={_target_cid} nick={_nick_key!r} "
                            f"(was cid={_state_cid or 'None'})"
                        )
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
                _delivered_payload_gd = {
                    "timestamp": _raw_gd.get("czas_doreczenia") or now_iso(),
                    "final_location": _deliv_addr_gd,
                    "delivery_address": _deliv_addr_gd,
                    "source": "packs_ghost_detect",
                    "deliv_source": "packs_ghost_detect",
                }
                _ev_gd = _emit_and_apply_state(
                    "COURIER_DELIVERED",
                    order_id=str(_oid),
                    courier_id=_state_cid,
                    payload=_delivered_payload_gd,
                    event_id=f"{_oid}_COURIER_DELIVERED_canonical",
                )
                if not _ev_gd.state_ready:
                    stats["errors"] += 1
                if _ev_gd.event_created:
                    stats["delivered"] += 1
                    _ghost_confirmed += 1
                if _ev_gd.downstream_executed:
                    _log.info(
                        f"V3.20 PACKS_GHOST oid={_oid} cid={_state_cid} "
                        f"nick={_nick_gd!r} (zniknął z packs, panel status=7)"
                    )
            if _ghost_confirmed:
                stats["packs_ghost_detect"] = _ghost_confirmed
    # ================== END V3.20 PACKS GHOST DETECT ==================

    # ================== DELIVERED RESURRECTION ==================
    # Symetria do PACKS GHOST: zlecenie 'delivered' w state, ale WRÓCIŁO do packs kuriera
    # (koordynator RĘCZNIE cofnął status w gastro — case Pizzeria 105 29.06: apka wysłała
    # błędne 'doręczone' tuż po odbiorze, Adrian cofnął). Bez tego Ziomek ignoruje je NA
    # ZAWSZE (terminal + _ignored_ids) → lista kuriera i czasy się nie aktualizują.
    # fetch_details POTWIERDZA aktywny status gastro (3-6) ZANIM wskrzesi. Wąskie: tylko
    # świeżo dostarczone (okno cofnięcia ~60 min) + budżet 5 fetchów/cykl.
    try:
        from dispatch_v2 import common as _Cres
        _res_flag = _Cres.flag("ENABLE_DELIVERED_RESURRECTION",
                               getattr(_Cres, "ENABLE_DELIVERED_RESURRECTION", False))
    except Exception:
        _res_flag = False
    if _res_flag:
        _packs_res = parsed.get("courier_packs") or {}
        if _packs_res:
            try:
                import json as _json_res
                with open("/root/.openclaw/workspace/dispatch_state/kurier_ids.json") as _f_res:
                    _kids_res = _json_res.load(_f_res)
                _cid2nick_res, _namecnt_res = {}, {}
                for _nm, _cid in _kids_res.items():
                    _nmk = (_nm or "").strip()
                    if not _nmk:
                        continue
                    _namecnt_res[_nmk] = _namecnt_res.get(_nmk, 0) + 1
                    _cid2nick_res[str(_cid)] = _nmk
                _ambig_res = {n for n, c in _namecnt_res.items() if c > 1}
            except Exception as _e_res:
                _log.warning(f"resurrection: kurier_ids load fail: {_e_res}")
                _cid2nick_res, _ambig_res = {}, set()
            _packs_by_nick_res = {(n or "").strip(): {str(x) for x in (v or [])}
                                  for n, v in _packs_res.items()}
            _now_res = datetime.now(timezone.utc)
            _res_checked = _res_done = 0
            for _oid_r, _so_r in list(current_state.items()):
                if _res_checked >= 5:
                    break
                if _so_r.get("status") != "delivered":
                    continue
                _da = _so_r.get("delivered_at")
                if _da:                                   # tylko świeżo dostarczone (okno cofnięcia)
                    try:
                        _dadt = datetime.fromisoformat(str(_da).replace("Z", "+00:00"))
                        if _dadt.tzinfo is None:
                            _dadt = _dadt.replace(tzinfo=timezone.utc)
                        if (_now_res - _dadt).total_seconds() / 60.0 > 60.0:
                            continue
                    except Exception:
                        pass
                _cid_r = str(_so_r.get("courier_id") or "")
                _nick_r = _cid2nick_res.get(_cid_r)
                if not _nick_r or _nick_r in _ambig_res:
                    continue
                if str(_oid_r) not in _packs_by_nick_res.get(_nick_r, set()):
                    continue                              # NIE wróciło do packs — zostaje delivered
                try:
                    _raw_r = fetch_order_details(str(_oid_r), csrf)
                    stats["fetched_details"] += 1
                    _res_checked += 1
                except Exception as _fe_r:
                    _log.warning(f"resurrection fetch({_oid_r}): {_fe_r}")
                    stats["errors"] += 1
                    continue
                if not _raw_r or _raw_r.get("id_status_zamowienia") not in (3, 4, 5, 6):
                    continue                              # gastro nadal terminalny → nie wskrzeszaj
                _sid_r = _raw_r.get("id_status_zamowienia")
                _new_st = "picked_up" if _sid_r in (5, 6) else "assigned"
                _ignored_ids.discard(str(_oid_r))
                try:
                    _res_outcome = _emit_and_apply_state(
                        "ORDER_RESURRECTED",
                        order_id=str(_oid_r),
                        courier_id=_cid_r,
                        payload={
                            "new_status": _new_st,
                            "reason": "panel_status_restored",
                            "source": "panel_status_restored",
                        },
                        event_id=(
                            f"{_oid_r}_ORDER_RESURRECTED_{_new_st}_canonical"
                        ),
                        audit=True,
                    )
                    if not _res_outcome.state_ready:
                        stats["errors"] += 1
                        continue
                    if _res_outcome.failure_stage is not None:
                        # State correction is already durable, but its exact
                        # plan repair remains visible/retryable in the outbox.
                        stats["errors"] += 1
                    if _res_outcome.state_transitioned:
                        _res_done += 1
                        _log.info(
                            f"RESURRECT {_oid_r} cid={_cid_r} "
                            f"sid={_sid_r}→{_new_st} "
                            "(wrócił do packs po ręcznym cofnięciu w gastro)"
                        )
                except Exception as _re_r:
                    _log.warning(f"resurrect fail {_oid_r}: {type(_re_r).__name__}: {_re_r}")
            if _res_done:
                stats["resurrected"] = _res_done
    # ================== END DELIVERED RESURRECTION ==================

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
            _reconcile_delivered_payload = {
                "timestamp": raw.get("czas_doreczenia") or now_iso(),
                "final_location": deliv_addr,
                "delivery_address": deliv_addr,
                "source": "reconcile",
                "deliv_source": "reconcile",
            }
            ev = _emit_and_apply_state(
                "COURIER_DELIVERED",
                order_id=zid,
                courier_id=kid,
                payload=_reconcile_delivered_payload,
                event_id=f"{zid}_COURIER_DELIVERED_canonical",
            )
            if not ev.state_ready:
                stats["errors"] += 1
            if ev.event_created:
                stats["delivered"] += 1
            if ev.downstream_executed:
                _log.info(f"DELIVERED {zid} (reconcile) kurier={kid}")
        elif sid in (8, 9):
            reason = "undelivered" if sid == 8 else "cancelled"
            _reconcile_returned_payload = {
                "reason": reason,
                "source": "reconcile",
            }
            ev = _emit_and_apply_state(
                "ORDER_RETURNED_TO_POOL",
                order_id=zid,
                courier_id=str(sorder.get("courier_id") or ""),
                payload=_reconcile_returned_payload,
                event_id=f"{zid}_ORDER_RETURNED_{reason}_canonical",
                audit=True,
                old_plan_release_authorized=_old_plan_release_on,
            )
            if not ev.state_ready:
                stats["errors"] += 1
            if ev.downstream_executed:
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
        # ETAP 5 KROK 5 (2026-06-10, upgrade E6/Z-19): persystencja wejścia w
        # id_status=4 (oczekiwanie pod restauracją). Pierwszy raz widziany sid=4
        # → waiting_at=now_iso (idempotent — NIE nadpisujemy; granulacja = cykl
        # pu_reconcile). Konsument: sla_tracker._check_restaurant_violations
        # (arrival_source=status4 zamiast commit_fallback — czysta atrybucja
        # kurier-vs-restauracja). Flaga hot-reload w flags.json.
        if (sid == 4 and not sorder.get("waiting_at")
                and flag("ENABLE_WAITING_AT_PERSIST", True)):
            try:
                upsert_order(zid, {"waiting_at": now_iso()},
                             event="WAITING_AT_RESTAURANT_OBSERVED")
                _log.info(f"WAITING_AT set {zid} (pu_reconcile sid=4)")
            except Exception as e:
                _log.warning(f"waiting_at persist fail {zid}: {e}")
        if sid == 5 and dzien_odbioru:
            kid = str(raw.get("id_kurier") or "")
            # pickup_coords z lookup - order moze miec address_id w state (po patch enrichment)
            # lub fallback z raw.address.id
            aid = sorder.get("address_id") or (raw.get("address", {}) or {}).get("id")
            aid_str = str(aid) if aid is not None else None
            # FRONT-B: pod flagą preferuj koordy persystowane na orderze
            # (NEW_ORDER już je rozwiązał — spójne z tym, co widział scoring);
            # OFF = stary lookup z cache (zero zmiany zachowania).
            pu_coords = None
            if decision_flag("ENABLE_PICKUP_COORDS_FROM_PANEL"):
                _st_pc = sorder.get("pickup_coords")
                if _st_pc and len(_st_pc) == 2:
                    pu_coords = tuple(_st_pc)
            if pu_coords is None:
                pu_coords = _COORDS.get(aid_str) if aid_str else None
            _pickup_payload = {
                "timestamp": dzien_odbioru,
                "pickup_coords": list(pu_coords) if pu_coords else None,
                "source": "reconcile",
            }
            ev = _emit_and_apply_state(
                "COURIER_PICKED_UP",
                order_id=zid,
                courier_id=kid,
                payload=_pickup_payload,
                event_id=f"{zid}_COURIER_PICKED_UP_canonical",
            )
            if not ev.state_ready:
                stats["errors"] += 1
            if ev.event_created:
                stats["picked_up"] += 1
            if ev.downstream_executed:
                _log.info(f"PICKED_UP {zid} (reconcile) kurier={kid} at {dzien_odbioru}")
    # ================== END PICKED_UP RECONCILE ==================

    # ============ GROUND-TRUTH PICKUP FALLBACK (R1, 2026-06-16) ============
    # Kurierzy BEZ konta panel-sync (wszyscy poza cid 123/21): odbiór melduje apka
    # → courier_ground_truth.picked_up_at + courier_status_events status 5, ALE panel
    # nie pokazuje OTWARTEGO sid==5 → PICKED_UP RECONCILE wyżej go gubi → zlecenie
    # domyka DELIVERED-reconcile (ustawia delivered_at, NIGDY picked_up_at) → plan_recheck
    # planuje FANTOMOWY odbiór już-niesionego (zawyżone ETA, dostawa-przed-odbiorem).
    # Domykamy lukę u ŹRÓDŁA: gdy ground_truth zna odbiór dla (kurier ZGODNY, oid) a
    # orders_state wciąż assigned/picked_up_at=null → emit COURIER_PICKED_UP z czasem GT.
    # = zaplanowana „Faza 2b-LIVE" z courier_ground_truth.py. Idempotentne (event_id +
    # guard picked_up_at). Default OFF (decision_flag absent → False). Naprawia WSZYSTKICH
    # konsumentów orders_state (plan + sla_tracker + gate_audit + apka).
    if decision_flag("ENABLE_PICKUP_FROM_GROUND_TRUTH"):
        try:
            from dispatch_v2 import courier_ground_truth as _gtmod
            from zoneinfo import ZoneInfo as _ZI
            _WARSAW_GT = _ZI("Europe/Warsaw")
            _gt = _gtmod.load_ground_truth()
            for _zid, _sorder in current_state.items():
                if _sorder.get("status") != "assigned" or _sorder.get("picked_up_at"):
                    continue
                _kid = str(_sorder.get("courier_id") or "")
                if not _kid:
                    continue
                _e = _gtmod.get_entry(_gt, _zid)
                if not _e:
                    continue
                _pu_epoch = _e.get("picked_up_at")
                # tylko gdy GT zna odbiór, NIE zna jeszcze doręczenia (delivered idzie
                # własną ścieżką), i kurier w GT zgadza się z orders_state (B6: nie ufaj
                # wpisom reassign o niezgodnym kurierze).
                if not _pu_epoch or _e.get("delivered_at"):
                    continue
                if str(_e.get("courier_id") or "") != _kid:
                    continue
                try:
                    _pu_ts = datetime.fromtimestamp(int(_pu_epoch), _WARSAW_GT).strftime(
                        "%Y-%m-%d %H:%M:%S")
                except (ValueError, OSError, OverflowError, TypeError):
                    continue
                _st_pc = _sorder.get("pickup_coords")
                _pc = list(_st_pc) if (_st_pc and len(_st_pc) == 2) else None
                _gt_pickup_payload = {
                    "timestamp": _pu_ts,
                    "pickup_coords": _pc,
                    "source": "ground_truth_fallback",
                }
                _ev = _emit_and_apply_state(
                    "COURIER_PICKED_UP",
                    order_id=_zid,
                    courier_id=_kid,
                    payload=_gt_pickup_payload,
                    event_id=f"{_zid}_COURIER_PICKED_UP_canonical",
                )
                if not _ev.state_ready:
                    stats["errors"] += 1
                if _ev.event_created:
                    stats["picked_up"] += 1
                if _ev.downstream_executed:
                    _log.info(f"PICKED_UP {_zid} (ground_truth_fallback) kurier={_kid} at {_pu_ts}")
        except Exception as _e:
            _log.warning(f"gt_pickup_fallback fail: {type(_e).__name__}: {_e}")
    # ============ END GROUND-TRUTH PICKUP FALLBACK ============

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
    # FORCE-RECHECK na żądanie koordynatora (przycisk „Odśwież czas" w konsoli):
    # drenuj kolejkę coordinator_time_recheck (panel dopisał oid). Te oid wymuszamy
    # BEZWARUNKOWO — także planned-elastyki (poza zwykłym scope) i w OBIE strony
    # (deliberate=True omija forward-only/czasówka-passive). Flaga = kill-switch.
    _force_ids: set = set()
    try:
        if C.flag("ENABLE_COORDINATOR_FORCE_TIME_RECHECK", True):
            from dispatch_v2 import coordinator_time_recheck as _ctr
            _force_ids = _ctr.drain()
            if _force_ids:
                _log.info(
                    f"COORDINATOR_FORCE_TIME_RECHECK drained {len(_force_ids)} oid(s): "
                    f"{sorted(_force_ids)}"
                )
    except Exception as _e:  # noqa: BLE001 — fail-soft, automat leci dalej
        _log.warning(f"force-recheck drain fail: {_e}")

    if ENABLE_V319G_CK_DETECTION or ENABLE_PICKUP_TIME_DETECTION or _force_ids:
        for zid, state_order in list(current_state.items()):
            _force = zid in _force_ids
            _status = state_order.get("status")
            _is_czasowka = C.is_czasowka_order(state_order)
            in_scope = (
                _status in ("assigned", "picked_up")
                or (_status == "planned" and _is_czasowka)
                or _force  # klik koordynatora wymusza re-check dowolnego statusu
            )
            if not in_scope:
                continue
            if zid not in html_order_ids:
                if _force:
                    _log.info(f"force-recheck oid={zid} nie ma na boardzie — pomijam")
                continue  # terminal or vanished — skip
            try:
                # PANEL-SCRAPE-01: ten scope to WIĘKSZOŚĆ fetchy/tick — prefetch-first
                raw_ck = _details(zid)
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
            if ENABLE_V319G_CK_DETECTION or _force:
                fresh_snippet = {
                    "czas_kuriera_warsaw": norm_ck.get("czas_kuriera_warsaw"),
                    "czas_kuriera_hhmm": norm_ck.get("czas_kuriera_hhmm"),
                    "id_kurier": raw_ck.get("id_kurier"),
                    # CK-MANUAL-EDIT: pola juz obecne w tym samym response gastro.
                    # OFF sa tylko nieuzywanym snippetem; emit/persist pozostaje
                    # identyczny. Marker ma jawny bool z normalize_order.
                    "zmiana_czasu_odbioru": norm_ck.get("zmiana_czasu_odbioru"),
                    "pickup_at_warsaw": norm_ck.get("pickup_at_warsaw"),
                    "status_id": norm_ck.get("status_id"),
                    "prep_minutes": norm_ck.get("prep_minutes"),
                    "decision_deadline": norm_ck.get("decision_deadline"),
                }
                evt = _diff_czas_kuriera(state_order, fresh_snippet, oid=zid,
                                         deliberate=_force)
                if evt is not None:
                    if evt.get("event_type") == "PICKUP_TIME_UPDATED":
                        p = evt["payload"]
                        p_event_id = _time_update_event_key(zid, evt)
                        pickup_outcome = _emit_and_apply_state(
                            "PICKUP_TIME_UPDATED",
                            order_id=zid,
                            courier_id=str(state_order.get("courier_id") or ""),
                            payload=p,
                            event_id=p_event_id,
                            audit=True,
                        )
                        if not pickup_outcome.state_ready:
                            stats["errors"] += 1
                        if pickup_outcome.downstream_executed:
                            _log.info(
                                f"CK_MANUAL_EDIT_PASSTHROUGH oid={zid} pickup "
                                f"{p.get('old_pickup_at_warsaw')}→"
                                f"{p.get('new_pickup_at_warsaw')} status={_status}"
                            )
                        continue
                    event_id_str = _time_update_event_key(zid, evt)
                    ck_outcome = _emit_and_apply_state(
                        "CZAS_KURIERA_UPDATED",
                        order_id=zid,
                        courier_id=str(state_order.get("courier_id") or ""),
                        payload=evt["payload"],
                        event_id=event_id_str,
                        audit=True,
                    )
                    if not ck_outcome.state_ready:
                        stats["errors"] += 1
                    if ck_outcome.downstream_executed:
                        delta_val = evt["payload"].get("delta_min")
                        delta_str = (
                            f"Δ={delta_val:+.1f}min"
                            if delta_val is not None else "Δ=null(first_ack)"
                        )
                        _log.info(
                            f"V3.19g1 oid={zid} ck "
                            f"{evt['payload'].get('old_ck_hhmm')}→"
                            f"{evt['payload'].get('new_ck_hhmm')} "
                            f"{delta_str} status={_status}"
                        )

            # ---- Detekcja B: pickup_at_warsaw (PICKUP_TIME_UPDATED) ----
            if ENABLE_PICKUP_TIME_DETECTION or _force:
                pickup_snippet = {
                    "pickup_at_warsaw": norm_ck.get("pickup_at_warsaw"),
                    "prep_minutes": norm_ck.get("prep_minutes"),
                    "decision_deadline": norm_ck.get("decision_deadline"),
                    "zmiana_czasu_odbioru": norm_ck.get("zmiana_czasu_odbioru"),
                }
                evt_p = _diff_pickup_time(state_order, pickup_snippet, oid=zid,
                                          deliberate=_force)
                if evt_p is not None:
                    p_event_id = _time_update_event_key(zid, evt_p)
                    pickup_outcome = _emit_and_apply_state(
                        "PICKUP_TIME_UPDATED",
                        order_id=zid,
                        courier_id=str(state_order.get("courier_id") or ""),
                        payload=evt_p["payload"],
                        event_id=p_event_id,
                        audit=True,
                    )
                    if not pickup_outcome.state_ready:
                        stats["errors"] += 1
                    if pickup_outcome.downstream_executed:
                        p_delta = evt_p["payload"].get("delta_min")
                        p_delta_str = (
                            f"Δ={p_delta:+.1f}min"
                            if p_delta is not None else "Δ=null(late)"
                        )
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
            _ev = _emit_and_apply_state(
                "COURIER_ASSIGNED",
                order_id=_oid_str,
                courier_id=_target_cid,
                payload={
                    "source": "cold_start_scan",
                    "nick": _nick_key,
                },
                state_payload={"source": "cold_start_scan"},
                event_id=f"{_oid_str}_COURIER_ASSIGNED_{_target_cid}_canonical",
                audit=True,
            )
            if not _ev.state_ready:
                stats["cold_start_errors"] += 1
            if _ev.event_created:
                stats["cold_start_emitted"] += 1
            if _ev.downstream_executed:
                _log.info(
                    f"COLD_START_CATCHUP {_oid_str} → cid={_target_cid} "
                    f"nick={_nick_key!r} sid={_sid}"
                )
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

    _cas_conflicts_before = plan_manager.cas_conflicts_total()
    cycle_stats = {"cycle": cycle_num, "at": now_iso()}
    cycle_parsed: Optional[dict] = None

    # E1: niezalezny od fetchu panelu i nowych eventow recovery tick. Helper
    # jest fail-soft; jeden snapshot flagi steruje OBU miejscami, więc hot-flip
    # w połowie ticku nie uruchomi dwóch drainów ani nie ominie obu.
    try:
        _state_outbox_sweeper_on = decision_flag(
            "ENABLE_STATE_OUTBOX_SWEEPER"
        )
    except Exception as exc:
        _state_outbox_sweeper_on = False
        _log.error(
            "STATE_OUTBOX_SWEEPER flag read failed: "
            f"{type(exc).__name__}: {exc}; using legacy drain"
        )
    cycle_stats.update(
        _sweep_state_apply_outbox(enabled=_state_outbox_sweeper_on)
    )
    _sweeper_errors = int(cycle_stats.get("errors", 0) or 0)

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
        _sweeper_snapshot_token = _STATE_OUTBOX_SWEEPER_TICK_SNAPSHOT.set(
            _state_outbox_sweeper_on
        )
        try:
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

            diff_stats = _diff_and_emit(
                parsed,
                csrf,
                _state_outbox_sweeper_on=_state_outbox_sweeper_on,
            )
        finally:
            _STATE_OUTBOX_SWEEPER_TICK_SNAPSHOT.reset(
                _sweeper_snapshot_token
            )
        if _sweeper_errors:
            diff_stats["errors"] = int(diff_stats.get("errors", 0) or 0) + (
                _sweeper_errors
            )
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

    cycle_stats["plan_cas_conflicts"] = (
        plan_manager.cas_conflicts_total() - _cas_conflicts_before
    )
    return cycle_stats, cycle_parsed


def run():
    """Glowna petla watchera."""
    signal.signal(signal.SIGTERM, _signal_handler)
    signal.signal(signal.SIGINT, _signal_handler)

    cfg = load_config()
    interval = cfg["polling"]["panel_interval_seconds"]
    _log.info(f"Panel watcher START interval={interval}s")
    # ETAP 4 (2026-06-10, Z-04): fingerprint flag decyzyjnych (watcher nie ma
    # silnika scoringu — linia czysto diagnostyczna, strażnik na przyszłość).
    try:
        from dispatch_v2 import common as _C4
        _log.info("FLAG_FINGERPRINT proc=panel-watcher %s", _C4.flag_fingerprint())
    except Exception:
        pass

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
    _overlap = _TickOverlapTracker()  # TICK-OVERLAP-05

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

        # TICK-OVERLAP-05: headroom ticka (WARNING rate-limited, bez Telegrama)
        _ov_warn = _overlap.note(elapsed, interval)
        if _ov_warn:
            _log.warning(_ov_warn)

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
                f"{_overlap.summary_fragment()}, "
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
