"""dispatch_pipeline - per-order assessment: feasibility → scoring → rank → verdict.

Input:  NEW_ORDER event dict + fleet snapshot + restaurant_meta.
Output: PipelineResult with ranked candidates and final verdict.

Verdicts:
    PROPOSE — best candidate is feasible, send to Telegram for approval
    KOORD   — early-bird (>=60 min ahead) OR R28 best_effort (no feasible, SLA compromise)
    SKIP    — no candidate with any plan (fleet empty / all fast-filter rejections).
              R29 says never hang; SKIP always alerts Adrian.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Tuple, Any

from dispatch_v2.route_simulator_v2 import OrderSim, RoutePlanV2, DWELL_PICKUP_MIN  # noqa: F401 — DWELL_PICKUP_MIN: kontrakt atrybutu dla core.candidates (_dp.DWELL_PICKUP_MIN, K11)
from dispatch_v2.feasibility_v2 import check_feasibility_v2  # noqa: F401 — kontrakt atrybutu: monkeypatch tools/replay_feasibility + aliasy _dp w core.{candidates,selection} (K11/K12)
from dispatch_v2 import common as C
from dispatch_v2 import calib_maps  # SP-B2 (2026-06-11): mapy ETA-quantile + prep-bias (shadow)
from dispatch_v2 import panel_client  # V3.27.1 sesja 2: pre-proposal recheck (Blocker 2 Opcja A)
from dispatch_v2 import effects_buffer as _EB  # K08 refaktoru: efekty PO decyzji (ADR-R02)
from dispatch_v2.core import gates as _gates  # K10 refaktoru: bramki wejściowe (geokod-defense, early-bird)
from dispatch_v2.core import candidates as _candidates  # K11 refaktoru: pętla per-kurier
from dispatch_v2.core import selection as _selection  # K12 refaktoru: selekcja + werdykt
from dispatch_v2.observability import stage_timing as _ST  # Z-P1-03: observation-only
from dispatch_v2.common import (
    parse_panel_timestamp,
    WARSAW,
    HAVERSINE_ROAD_FACTOR_BIALYSTOK,  # noqa: F401 — kontrakt atrybutu _dp (core.candidates, K11)
    get_fallback_speed_kmh,
    ENABLE_CZAS_KURIERA_PROPAGATION,
)
from dispatch_v2.osrm_client import haversine
# NOGPS-NEUTRAL-SCORE (2026-07-19): jedno źródło klasy Unknown pozycji (F-3) +
# s_dystans do przeliczenia neutralnego komponentu dystansu. Bez cyklu:
# courier_resolver/scoring nie importują dispatch_pipeline na module-level.
from dispatch_v2.courier_resolver import POSITION_UNKNOWN_SOURCES
from dispatch_v2 import eta_trust as _eta_trust
from dispatch_v2 import scoring as _scoring_nn
from dispatch_v2.bag_state import build_courier_bag_state
from dispatch_v2.fleet_context import build_fleet_context, FleetContext
from dispatch_v2.pipeline_geometry import _point_to_segment_km, _min_dist_to_route_km  # noqa: F401 — B6: czysta geometria; kontrakt tożsamości (test_pipeline_geometry) + _dp._min_dist_to_route_km w core.candidates (K11)
import importlib
import json
import math
import os
import threading  # V3.27.1 sesja 2: in-memory cache lock dla pre-proposal recheck

# T1 (2026-05-01): bare getLogger zostawiał dispatch_pipeline INFO logs bez handlers
# (effective level WARNING z root inheritance) → FIX_C bundle_cap, V326_R06,
# V326_WAVE_VETO, LGBM shadow INFO logs były dropped. Match canonical pattern z innych
# modułów dispatch_v2 (geocoding, osrm_client, panel_client, panel_watcher,
# state_machine — wszystkie używają setup_logger → dispatch.log).
log = C.setup_logger("dispatch_pipeline", "/root/.openclaw/workspace/scripts/logs/dispatch.log")


# ═══════════════════════════════════════════════════════════════════
# V3.27.1 sesja 2 — Pre-proposal czas_kuriera recheck (Mechanizm 3 hybrydowy)
# ═══════════════════════════════════════════════════════════════════
# Module-level singleton in-memory cache (Blocker 1 Opcja C — clean separation,
# zero schema migration). Cache survives across dispatch calls, evicted by TTL
# co N calls lub max size (whichever first).
# Thread-safe via Lock dla parallel candidates w ThreadPoolExecutor.
_v327_pre_recheck_last_seen: Dict[str, datetime] = {}
_v327_pre_recheck_lock = threading.Lock()
_v327_pre_recheck_call_counter = 0

# V3.29: DEFAULT_CITY z env (multi-tenant)
DEFAULT_CITY = os.environ.get('ZIOMEK_DEFAULT_CITY', 'Białystok')
log.info(f"V326_DEFAULT_CITY: {DEFAULT_CITY}")


# B2 (audyt 2026-05-29): rule_weights.json — STATIC, strojone ręcznie (B1-b: brak writera).
# Wcześniej ładowane per-kandydat z hardcoded path, a load-fail → CICHY `{}` → kary R1/R5/R8
# znikały bez śladu (Z2 never-silent violation). Teraz: ścieżka z env, cache z mtime-checkiem
# (zero disk I/O na cache-hit), load-fail → GŁOŚNY log.error + ostatnie-dobre/defaults
# (fail-safe: scoring nie crashuje, ale awaria pliku jest WIDOCZNA w dispatch.log).
# Thread-safe bez locka (parallel candidates w ThreadPoolExecutor): `data` budowane w local
# i podmieniane jednym atomowym przypisaniem referencji (GIL) — reader widzi cały dict;
# reload tylko na zmianę mtime i jest idempotentny (wyścig = redundantny read tych samych danych).
_RULE_WEIGHTS_PATH = os.environ.get(
    "RULE_WEIGHTS_PATH", "/root/.openclaw/workspace/dispatch_state/rule_weights.json"
)
_RULE_WEIGHTS_DEFAULTS: Dict[str, Any] = {
    "R1_spread_per_km": -8.0,
    "R5_pickup_per_km": -6.0,
    "R8_span_per_min": -1.5,
}
_rule_weights_cache: Dict[str, Any] = {
    "mtime": None,
    "data": dict(_RULE_WEIGHTS_DEFAULTS),
    "logged_fail": False,
}


def _load_rule_weights() -> Dict[str, Any]:
    """Cached loader rule_weights.json (kary R1/R5/R8). Reload tylko gdy mtime się zmienił
    → brak per-kandydat disk I/O. Load-fail → GŁOŚNY log.error (raz na stan błędu) +
    ostatnie-dobre dane (lub defaults gdy nigdy nie wczytano). Scoring NIE crashuje, ale
    awaria pliku jest widoczna w logu — koniec cichego `{}`."""
    cache = _rule_weights_cache
    try:
        mtime = os.stat(_RULE_WEIGHTS_PATH).st_mtime
    except OSError as e:
        if not cache["logged_fail"]:
            log.error(
                "rule_weights NIEDOSTĘPNY path=%s err=%s — używam %s (kary R1/R5/R8 z fallbacku!)",
                _RULE_WEIGHTS_PATH, e,
                "ostatnich-dobrych" if cache["mtime"] is not None else "defaults",
            )
            cache["logged_fail"] = True
        return cache["data"]
    if mtime != cache["mtime"]:
        try:
            with open(_RULE_WEIGHTS_PATH, encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                raise ValueError("rule_weights.json nie jest obiektem JSON")
            cache["data"] = data            # atomic ref-swap (GIL) — reader widzi cały dict
            cache["mtime"] = mtime
            if cache["logged_fail"]:
                log.info("rule_weights ODZYSKANY path=%s keys=%d", _RULE_WEIGHTS_PATH, len(data))
            cache["logged_fail"] = False
        except Exception as e:
            if not cache["logged_fail"]:
                log.error(
                    "rule_weights LOAD FAIL path=%s err=%s — używam %s (kary R1/R5/R8 z fallbacku!)",
                    _RULE_WEIGHTS_PATH, e,
                    "ostatnich-dobrych" if cache["mtime"] is not None else "defaults",
                )
                cache["logged_fail"] = True
            return cache["data"]
    return cache["data"]


# V3.28 #28 (2026-05-11): defensive fallback dla (0,0) sentinel leak.
# courier_resolver.dispatchable_fleet substituuje BIALYSTOK_CENTER dla no_gps,
# ale (0,0) leakuje przez inne paths (stale GPS API read, missing init). Bez
# guard'a haversine raise ValueError (Lekcja #81 fail-loud) → V328_CP_SOLVER_FAIL
# spam (10.05 ~110/30min peak). Mirror Faza 7 Etap 0 wzorzec.
_BIALYSTOK_CENTER_FALLBACK = (53.1325, 23.1688)


def _r1_corridor_base_bonus(avg_cos, gradient: bool) -> float:
    """F1 (2026-05-24) — bazowa kara/bonus korytarza R1 z avg pairwise cosine
    (PRZED mnożnikiem deliv_spread). Strona dodatnia (reward) bez zmian.

    gradient=False (legacy): klif na ujemnej stronie — avg_cos ∈ (-0.5,0] → płaskie
    -35, ≤-0.5 → -40. Problem: -0.05 karane tak samo jak -0.49.
    gradient=True (F1): liniowo 0 przy cos=0 → -40 przy cos=-1 (40*cos). Po F2
    (wave-scoped cosine) sygnał jest czysty — lekka rozbieżność dostaje lekką karę,
    przeciwne kierunki mocną. Mnożnik deliv_spread (caller) nadal dokłada dla
    szerokich dropów. None → 0 (solo noga / brak sygnału).
    """
    if avg_cos is None:
        return 0.0
    if avg_cos > 0.85:
        return 20.0
    if avg_cos > 0.5:
        return 5.0
    if avg_cos > 0.0:
        return 0.0
    if gradient:
        return 40.0 * avg_cos
    if avg_cos > -0.5:
        return -35.0  # P3-D5 2026-05-11: tighten -15 → -35
    return -40.0


def _compute_r1_progressive_delta(cosine, existing_bonus):
    """Sprint 2026-05-28 — progresywna kara R1 dla skrajnych przeciwieństw drops.

    Istniejący ``_r1_corridor_base_bonus`` flat-clip'uje karę na -35/-40 dla
    cosine < 0. Niewystarczająco wobec ``bonus_l2`` (+11..+17) +
    ``v319h_bug2_continuation_bonus`` (+30). Empiryczna kalibracja (7d replay):
    cos<-0.7 (n=14) → -100, -0.7..-0.5 (n=7) → -60, -0.5..-0.3 (n=15) → -45.
    Zachowuje cos>=-0.3 (15 cases) bez zmian — fix uderza tylko gdy drops
    naprawdę się rozjeżdżają (Adrian: „inna strona miasta").

    Zwraca delta do bonus_r1_corridor (NIGDY positive — nigdy nie lightening
    istniejącej kary).
    """
    if cosine is None or not isinstance(cosine, (int, float)):
        return 0.0
    existing = existing_bonus if isinstance(existing_bonus, (int, float)) else 0.0
    if cosine < C.R1_PROGRESSIVE_CRITICAL_COS:
        new_val = C.R1_PROGRESSIVE_CRITICAL_VAL
    elif cosine < C.R1_PROGRESSIVE_HEAVY_COS:
        new_val = C.R1_PROGRESSIVE_HEAVY_VAL
    elif cosine < C.R1_PROGRESSIVE_MEDIUM_COS:
        new_val = C.R1_PROGRESSIVE_MEDIUM_VAL
    else:
        return 0.0
    return min(new_val - existing, 0.0)


def _compute_v319h_guard_delta(cosine, continuation_bonus):
    """Sprint 2026-05-28 — guard zerujący ``v319h_bug2_continuation_bonus`` (+30)
    gdy drops się rozjeżdżają (cosine < threshold, default -0.3).

    Reguła Adriana: bonus „za kontynuację fali" nie ma uzasadnienia gdy nowy
    drop jest w przeciwnym kierunku niż reszta bagu. Empirycznie: case
    #476749 Kebab Król → Mieszka I (cos=-0.425, continuation +30 maskowało
    karę kierunku, finalnie PROPOSE+ALERT zamiast KOORD).

    Zwraca delta do v319h_bug2_continuation_bonus (zawsze ≤ 0).
    """
    if cosine is None or not isinstance(cosine, (int, float)):
        return 0.0
    if not isinstance(continuation_bonus, (int, float)) or continuation_bonus <= 0:
        return 0.0
    if cosine < C.V319H_GUARD_COSINE_THRESHOLD:
        return -continuation_bonus
    return 0.0


def _append_difficult_case_log(entry: dict) -> None:
    """Sprint 2026-05-28 — zapis trudnego przypadku (KOORD redirect z powodu
    geometrii) do dedykowanego pliku do uczenia.

    Atomic append: open w trybie 'a' z domyślnym buforowaniem JSONL.
    Fail-soft: exception loguje warning ale nie wpływa na pipeline.
    """
    if _EB.divert(_append_difficult_case_log, entry):  # K08: efekt PO decyzji
        return
    try:
        import json as _json
        import os as _os
        path = getattr(C, "DIFFICULT_CASE_LOG_PATH",
                       "/root/.openclaw/workspace/scripts/logs/difficult_case_log.jsonl")
        # Ensure parent dir exists
        _os.makedirs(_os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            f.write(_json.dumps(entry, default=str, ensure_ascii=False) + "\n")
    except Exception as _e:
        try:
            log.warning(f"_append_difficult_case_log failed: {_e}")
        except Exception:
            pass


SPLIT_LAYER_GUARD_LOG_PATH = (
    "/root/.openclaw/workspace/dispatch_state/split_layer_guard.jsonl")


def _append_split_layer_guard_log(entry: dict) -> None:
    """L7.3 (2026-07-03, R2 ROOT-9) — zapis naruszenia kontraktu warstw (INV-LAYER-1/2)
    do dedykowanego JSONL. OBSERWACYJNY: nie wpływa na decyzję. Atomic append (tryb 'a').
    Fail-soft: exception loguje warning, nie wywraca pętli decyzyjnej."""
    if _EB.divert(_append_split_layer_guard_log, entry):  # K08: efekt PO decyzji
        return
    try:
        import json as _json
        import os as _os
        path = getattr(C, "SPLIT_LAYER_GUARD_LOG_PATH", SPLIT_LAYER_GUARD_LOG_PATH)
        _os.makedirs(_os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            f.write(_json.dumps(entry, default=str, ensure_ascii=False) + "\n")
    except Exception as _e:
        try:
            log.warning(f"_append_split_layer_guard_log failed: {_e}")
        except Exception:
            pass


def _split_layer_guard_on() -> bool:
    """L7.3 flaga OBSERWACYJNA (nie-decyzyjna, poza ETAP4). OFF ⇒ bajt-parytet
    (zero logu/jsonl, zero mutacji). flags.json hot → stała-fallback OFF."""
    try:
        return bool(C.flag("ENABLE_SPLIT_LAYER_GUARD",
                           getattr(C, "ENABLE_SPLIT_LAYER_GUARD", False)))
    except Exception:
        return False


def _coords_pass(legacy_ok, *coords) -> bool:
    """L2.1 sentinel-ingest (2026-07-01, K5a): wspólny guard callerów geometrii.

    Flaga ON → KAŻDY coord przez kanoniczny walidator `coords_in_bialystok_bbox`
    (None/NaN/(0,0)/poza-bbox = odpada; koniec truthy-guardów `if coords:` które
    przepuszczały [0,0] do haversine → ValueError → V328 eject kuriera).
    Flaga OFF → dokładnie legacy_ok (zachowanie sprzed L2.1, bajt-w-bajt).
    """
    if not C.decision_flag("ENABLE_COORD_SENTINEL_INGEST_GUARD"):
        return bool(legacy_ok)
    return all(C.coords_in_bialystok_bbox(c) for c in coords)


def _sanitize_courier_pos(pos):
    """Return BIALYSTOK_CENTER gdy pos to (0,0) sentinel, else pass-through."""
    if pos is None:
        return None
    try:
        if len(pos) >= 2 and float(pos[0]) == 0.0 and float(pos[1]) == 0.0:
            return _BIALYSTOK_CENTER_FALLBACK
    except (TypeError, ValueError):
        return None
    return pos


def _v327_evict_old_pre_recheck_entries(now: datetime) -> int:
    """V3.27.1 sesja 2: TTL-based eviction (default 1h).

    Trigger conditions:
    - Co N calls (V327_PRE_PROPOSAL_RECHECK_CACHE_EVICT_EVERY)
    - OR cache size > max (V327_PRE_PROPOSAL_RECHECK_CACHE_EVICT_MAX_SIZE)

    Returns count of evicted entries.
    """
    cutoff = now - timedelta(seconds=C.V327_PRE_PROPOSAL_RECHECK_CACHE_EVICT_AGE_SEC)
    with _v327_pre_recheck_lock:
        keys_to_remove = [k for k, v in _v327_pre_recheck_last_seen.items() if v < cutoff]
        for k in keys_to_remove:
            del _v327_pre_recheck_last_seen[k]
    return len(keys_to_remove)


class _V327FreshCzasKuriera(tuple):
    """2-tuple kompatybilny z V3.27.1 + nieiterowany snapshot korelacyjny."""

    def __new__(cls, iso, hhmm, fresh_time):
        obj = super().__new__(cls, (iso, hhmm))
        obj.fresh_time = fresh_time
        return obj


def _v327_safe_fetch_order_time(oid: str, timeout: float = None) -> Optional[dict]:
    """Fetch jednego, spojnego snapshotu pol czasu z gastro.

    Rozszerza V3.27.1 o istniejacy sygnal recznej korekty
    ``zmiana_czasu_odbioru`` oraz korelaty pickup/status z TEGO SAMEGO response.
    To wazne: osobny fetch otwieralby race z re-stampem statusowym.

    Pre-fix (sesja 2 broken): zwracało raw HH:MM gdy `czas_kuriera_warsaw` klucz
    nie istniał w surowym response. State_machine sanity check FAIL bo hhmm=None.

    Post-fix: call `normalize_order(raw)` żeby dostać OBA pola (ISO Warsaw + HH:MM).

    Returns dict albo None gdy:
    - Fetch fail (timeout, connection, exception)
    - normalize_order returns None (status_id ∈ {7,8,9} delivered/cancelled/declined
      — order zmienił status w trakcie cycle, skip emit)
    - Order ma czas_kuriera missing/invalid (norm fields = None, propagate up)

    Caller skip emit przy None (zachowuje cached).
    """
    if timeout is None:
        timeout = C.V327_PRE_PROPOSAL_RECHECK_FETCH_TIMEOUT_SEC
    try:
        fresh = panel_client.fetch_order_details(oid, timeout=int(timeout))
        if fresh is None:
            return None
        # KEY FIX V3.27.1 sesja 3: normalize_order konwertuje raw HH:MM → ISO Warsaw
        # plus filtruje IGNORED_STATUSES (7=delivered, 8=cancelled, 9=declined).
        norm = panel_client.normalize_order(fresh)
        if norm is None:
            # Status ignored = order delivered/cancelled w trakcie cycle, skip
            return None
        return {
            "czas_kuriera_warsaw": norm.get("czas_kuriera_warsaw"),
            "czas_kuriera_hhmm": norm.get("czas_kuriera_hhmm"),
            "pickup_at_warsaw": norm.get("pickup_at_warsaw"),
            "status_id": norm.get("status_id"),
            "prep_minutes": norm.get("prep_minutes"),
            "decision_deadline": norm.get("decision_deadline"),
            "zmiana_czasu_odbioru": norm.get("zmiana_czasu_odbioru"),
        }
    except Exception as e:
        # Zachowaj dotychczasowy marker logu (OFF parity + istniejące alerty).
        log.warning(f"V3.27.1 _v327_safe_fetch_czas_kuriera oid={oid} fail: {e}")
        return None


def _v327_safe_fetch_czas_kuriera(
    oid: str, timeout: float = None
) -> Tuple[Optional[str], Optional[str]]:
    """API V3.27.1: OFF zwraca dokladnie legacy 2-tuple.

    Dopiero nowa flaga ON dokleja nieiterowany snapshot do kompatybilnego
    tuple-subclass. Dzięki temu ciemny deploy nie zmienia nawet typu wyniku.
    """
    snapshot = _v327_safe_fetch_order_time(oid, timeout=timeout)
    if snapshot is None:
        return (None, None)
    legacy = (
        snapshot.get("czas_kuriera_warsaw"),
        snapshot.get("czas_kuriera_hhmm"),
    )
    if not C.decision_flag("ENABLE_CZASOWKA_CK_MANUAL_EDIT_PASSTHROUGH"):
        return legacy
    return _V327FreshCzasKuriera(
        legacy[0],
        legacy[1],
        snapshot,
    )


def _v327_compute_delta_min(old_iso: Optional[str], new_iso: Optional[str]) -> Optional[float]:
    """Compute delta minutes z 2 ISO timestamps. None gdy old/new missing lub parse fail."""
    if not old_iso or not new_iso:
        return None
    try:
        old_dt = datetime.fromisoformat(old_iso)
        new_dt = datetime.fromisoformat(new_iso)
        return round((new_dt - old_dt).total_seconds() / 60.0, 2)
    except Exception:
        return None


def _v327_emit_pre_recheck_event(oid: str, courier_id: Optional[str],
                                   old_ck_iso: Optional[str], new_ck_iso: str,
                                   new_ck_hhmm: Optional[str],
                                   now: datetime,
                                   fresh_time: Optional[dict] = None) -> None:
    """V3.27.1 sesja 3 fix Bug 1 — emit synth CZAS_KURIERA_UPDATED z OBIEMA polami.

    Pre-fix (sesja 2): payload `new_ck_hhmm=None` → state_machine sanity FAIL
    (`_verify_czas_kuriera_consistency` wymaga że strftime(parsed_iso, "%H:%M")==hhmm).

    Post-fix: caller (get_fresh_czas_kuriera_for_bag) przekazuje hhmm z
    `_v327_safe_fetch_czas_kuriera` tuple — sanity check OK.

    Side-effect: event_bus.emit + state_machine.update_from_event w background.
    Event_id: {oid}_CZAS_KURIERA_UPDATED_PRE_RECHECK_{epoch_ms} — unique per emit.
    """
    from dispatch_v2.event_bus import emit_audit as _eb_emit_audit
    from dispatch_v2.state_machine import (
        build_czasowka_manual_ck_pickup_event as _manual_pickup_event,
        get_order as _sm_get_order,
        update_from_event as _sm_apply,
    )

    delta_min = _v327_compute_delta_min(old_ck_iso, new_ck_iso)
    timestamp_ms = int(now.timestamp() * 1000)
    event_id = f"{oid}_CZAS_KURIERA_UPDATED_PRE_RECHECK_{timestamp_ms}"

    payload = {
        "oid": oid,
        "courier_id": courier_id,
        "old_ck_iso": old_ck_iso,
        "old_ck_hhmm": None,  # cached state — tylko ISO znamy
        "new_ck_iso": new_ck_iso,
        "new_ck_hhmm": new_ck_hhmm,  # V3.27.1 sesja 3 fix: OBA pola dla state_machine sanity
        "delta_min": delta_min,
        "source": "pre_proposal_recheck",
    }
    # OFF = payload/event identyczny jak przed zmiana. Dopiero ON dopina
    # snapshot korelacyjny z tego samego response gastro.
    manual_passthrough_enabled = C.decision_flag(
        "ENABLE_CZASOWKA_CK_MANUAL_EDIT_PASSTHROUGH"
    )
    if manual_passthrough_enabled:
        fresh_time = fresh_time or {}
        payload.update({
            "new_zmiana_czasu_odbioru": fresh_time.get(
                "zmiana_czasu_odbioru"
            ),
            "observed_pickup_at_warsaw": fresh_time.get("pickup_at_warsaw"),
            "observed_status_id": fresh_time.get("status_id"),
            "observed_prep_minutes": fresh_time.get("prep_minutes"),
            "observed_decision_deadline": fresh_time.get("decision_deadline"),
        })
    event = {
        "event_type": "CZAS_KURIERA_UPDATED",
        "order_id": oid,
        "courier_id": courier_id,
        "payload": payload,
    }
    try:
        # Ten sam classifier co panel_watcher i defense-in-depth state_machine.
        # Pozytywny sygnal nie zapisuje CK bokiem: przechodzi kanonicznym
        # PICKUP_TIME_UPDATED, ktory mirroruje czas do aplikacji.
        manual_event = None
        if manual_passthrough_enabled:
            manual_event = _manual_pickup_event(_sm_get_order(oid), payload)
        if manual_event is not None:
            event = manual_event
            event_id = f"{oid}_PICKUP_TIME_UPDATED_PRE_RECHECK_CK_MANUAL_{timestamp_ms}"
        _eb_emit_audit(event["event_type"],
                 order_id=oid, courier_id=courier_id or "",
                 payload=event["payload"], event_id=event_id)
        applied = _sm_apply(event)
        if manual_event is not None and applied is not None:
            _v327_touch_committed_view(oid, courier_id)
        delta_str = f"Δ={delta_min:+.1f}min" if delta_min is not None else "Δ=null"
        if manual_event is not None:
            log.info(
                f"V3.27.1 pre_proposal_recheck oid={oid} "
                f"{old_ck_iso or 'null'}→{new_ck_iso} ({new_ck_hhmm}) "
                f"{delta_str} event=PICKUP_TIME_UPDATED"
            )
        else:
            # OFF/niepotwierdzony sygnal: dotychczasowy log bajt-w-bajt.
            log.info(f"V3.27.1 pre_proposal_recheck oid={oid} {old_ck_iso or 'null'}→{new_ck_iso} ({new_ck_hhmm}) {delta_str}")
    except Exception as e:
        log.warning(f"V3.27.1 _v327_emit_pre_recheck_event oid={oid} fail: {e}")


def _v327_touch_committed_view(oid: str, courier_id: Optional[str]) -> None:
    """Pre-recheck twin FIX-E: bump plan_version po legalnej korekcie czasu.

    Panel watcher robi to samo przez `_invalidate_plan_on_committed_change`.
    Bez tego pre-proposal moglby zapisac korekte jako pierwszy, a watcher nie
    zobaczylby juz delty i aplikacja kuriera zachowalaby stary snapshot /orders.
    """
    if not courier_id or not C.ENABLE_SAVED_PLANS:
        return
    if not C.flag("ENABLE_COMMITTED_INVALIDATES_VIEW", True):
        return
    try:
        from dispatch_v2 import plan_manager
        if plan_manager.touch_plan(str(courier_id), "COMMITTED_TIME_CHANGED"):
            log.info(
                f"CK_MANUAL_EDIT_VIEW_REFRESH cid={courier_id} oid={oid} "
                f"— aplikacja odswiezy /orders"
            )
    except Exception as e:
        log.warning(
            f"CK_MANUAL_EDIT_VIEW_REFRESH fail cid={courier_id} oid={oid}: {e}"
        )


def get_fresh_czas_kuriera_for_bag(bag_orders: List[OrderSim],
                                     now: datetime) -> Dict[str, Optional[str]]:
    """V3.27.1 sesja 2: Pre-proposal czas_kuriera recheck dla orders w bagu kandydata.

    Mechanizm 3 hybrydowy (Adrian sesja 2 spec):
    - SKIP fetch dla orders z assigned_at <10 min temu (świeże, panel-watcher caught up)
    - SKIP fetch dla orders z last_recheck <5 min temu (in-memory cache)
    - FORCE fetch w przeciwnym wypadku, parallel via ThreadPoolExecutor
    - ZERO max bag limit (Bartek peak bag=8-11 expected, Plik wiedzy #1)
    - Defensive fallback do cached state value przy fetch failure
    - Emit synth CZAS_KURIERA_UPDATED z source=pre_proposal_recheck przy detected change

    Args:
        bag_orders: lista OrderSim w bagu kandydata kuriera
        now: timezone-aware datetime

    Returns:
        Dict[oid, czas_kuriera_warsaw_iso] — fresh OR cached values per oid.
        Caller MOŻE override bag_orders[i].czas_kuriera_warsaw values dla downstream
        scoring/TSP gdy fresh != cached.
    """
    global _v327_pre_recheck_call_counter

    if not C.ENABLE_V327_PRE_PROPOSAL_RECHECK:
        # Flag-gated short-circuit — return cached state values
        return {o.order_id: getattr(o, "czas_kuriera_warsaw", None) for o in bag_orders}

    # Counter increment + eviction trigger
    _v327_pre_recheck_call_counter += 1
    cache_size = len(_v327_pre_recheck_last_seen)
    if (_v327_pre_recheck_call_counter % C.V327_PRE_PROPOSAL_RECHECK_CACHE_EVICT_EVERY == 0
            or cache_size > C.V327_PRE_PROPOSAL_RECHECK_CACHE_EVICT_MAX_SIZE):
        evicted = _v327_evict_old_pre_recheck_entries(now)
        if evicted > 0:
            log.debug(f"V3.27.1 pre_recheck cache evicted {evicted} entries (size now={len(_v327_pre_recheck_last_seen)})")

    # Build per-oid decision (skip vs fetch)
    results: Dict[str, Optional[str]] = {}
    fetch_oids: List[str] = []
    bag_by_oid = {o.order_id: o for o in bag_orders}

    for o in bag_orders:
        oid = o.order_id
        cached_ck = getattr(o, "czas_kuriera_warsaw", None)
        results[oid] = cached_ck  # default: cached (overwritten if fetch happens)

        # Skip 1: świeży assignment (<10 min)
        assigned_at_iso = getattr(o, "assigned_at", None)
        if assigned_at_iso:
            try:
                assigned_at = datetime.fromisoformat(str(assigned_at_iso))
                age_min = (now - assigned_at).total_seconds() / 60.0
                if age_min < C.V327_PRE_PROPOSAL_RECHECK_AGE_MIN:
                    continue  # too fresh, skip fetch
            except Exception:
                pass  # parse fail → continue do cache check

        # Skip 2: świeży recheck cache (<5 min)
        with _v327_pre_recheck_lock:
            last_recheck = _v327_pre_recheck_last_seen.get(oid)
        if last_recheck is not None:
            cache_age_sec = (now - last_recheck).total_seconds()
            if cache_age_sec < C.V327_PRE_PROPOSAL_RECHECK_CACHE_TTL_SEC:
                continue  # cache still fresh, skip fetch

        # Force fetch
        fetch_oids.append(oid)

    # Parallel fetchy bez max ceiling (ZERO bag limit per Adrian)
    if fetch_oids:
        from concurrent.futures import ThreadPoolExecutor, as_completed
        with ThreadPoolExecutor(max_workers=len(fetch_oids),
                                  thread_name_prefix="v327_recheck") as executor:
            future_to_oid = {
                executor.submit(_v327_safe_fetch_czas_kuriera, oid): oid
                for oid in fetch_oids
            }
            for future in as_completed(future_to_oid):
                oid = future_to_oid[future]
                try:
                    # API nadal rozpakowuje sie jak legacy 2-tuple. Runtime helper
                    # dokleja nieiterowany snapshot; mocki zwracajace zwykly tuple
                    # zachowuja sie identycznie i po prostu nie maja evidence.
                    fresh_result = future.result()
                    fresh_iso, fresh_hhmm = fresh_result
                    fresh_time = getattr(fresh_result, "fresh_time", None)
                except Exception as e:
                    log.warning(f"V3.27.1 pre_recheck future oid={oid} exc: {e}")
                    fresh_time = None
                    fresh_iso, fresh_hhmm = (None, None)

                # Update cache timestamp regardless of result (avoid retry storms)
                with _v327_pre_recheck_lock:
                    _v327_pre_recheck_last_seen[oid] = now

                if fresh_iso is None:
                    # Defensive: helper zwrócił (None, None) — fetch fail lub
                    # status delivered/cancelled (normalize_order None) — skip emit.
                    continue

                # Compare z cached value
                bag_o = bag_by_oid.get(oid)
                cached_ck = getattr(bag_o, "czas_kuriera_warsaw", None) if bag_o else None
                if fresh_iso != cached_ck:
                    # Detected change — emit synth event z OBIEMA polami (iso + hhmm)
                    courier_id = str(getattr(bag_o, "courier_id", "") or "") if bag_o else ""
                    _v327_emit_pre_recheck_event(oid, courier_id, cached_ck,
                                                   fresh_iso, fresh_hhmm, now,
                                                   fresh_time=fresh_time)
                    results[oid] = fresh_iso

    return results


def _k07_apply_fresh_ck(bag_sim, fresh_dict) -> None:
    """K07 refaktor (2026-07-06): JEDNO źródło reguły nadpisania czas_kuriera
    w bag_sim (kontrakt ① — dotąd inline w _v327_eval_courier_inner).
    Reguła 1:1 z legacy: fresh is not None ORAZ różny od cached → override."""
    if not bag_sim or not fresh_dict:
        return
    for _bo in bag_sim:
        _fresh = fresh_dict.get(_bo.order_id)
        if _fresh is not None and _fresh != getattr(_bo, "czas_kuriera_warsaw", None):
            _bo.czas_kuriera_warsaw = _fresh


def _k07_prefetch_fresh_ck(fleet_snapshot, now):
    """K07 refaktor (2026-07-06, ADR-R02 przygotowanie): pre-proposal recheck
    czas_kuriera wykonany RAZ na decyzję, PRZED pulą kandydatów — zamiast
    żywego HTTP w środku oceny (dispatch_pipeline ~:3913, wątki puli).

    Mechanika: unia worków CAŁEJ floty (worki są rozłączne per kurier, więc
    zbiór zleceń = dokładnie ten, który dziś fetchują kandydaci) → JEDNO
    wywołanie get_fresh_czas_kuriera_for_bag (ta sama funkcja: te same skip-
    reguły age/cache, te same synth-eventy, ZERO bliźniaka). Wynik = dict
    {oid: fresh_iso} aplikowany w pętli czysto (_k07_apply_fresh_ck) — żadnego
    I/O w ocenie kandydata.

    Gate: ENABLE_PRE_RECHECK_BEFORE_POOL (ETAP4, kanon flags.json, brak
    klucza = OFF = None = ścieżka legacy 1:1). Fail-soft: KAŻDY błąd → None
    → pętla używa ścieżki legacy (zachowanie dotychczasowe)."""
    try:
        if not C.decision_flag("ENABLE_PRE_RECHECK_BEFORE_POOL"):
            return None
        if not C.ENABLE_V327_PRE_PROPOSAL_RECHECK:
            return None  # legacy short-circuit i tak zwróciłby cached — nic do prefetchu
        seen = set()
        union_sim = []
        for _cs in (fleet_snapshot or {}).values():
            for _b in (getattr(_cs, "bag", []) or []):
                _oid = str(_b.get("order_id") or "")
                if not _oid or _oid in seen:
                    continue
                seen.add(_oid)
                union_sim.append(_bag_dict_to_ordersim(_b))
        if not union_sim:
            return {}
        return get_fresh_czas_kuriera_for_bag(union_sim, now)
    except Exception as _e:
        log.warning(f"K07 prefetch_fresh_ck fail (fallback do ścieżki legacy): {_e}")
        return None


BLIND_POS_SOURCES = ("no_gps", "pre_shift", "none")
INFORMED_POS_SOURCES = (
    "gps", "last_assigned_pickup", "last_picked_up_delivery",
    "last_picked_up_recent", "last_delivered", "post_wave",
    # Fix #5 (2026-05-31): last_picked_up_pickup = „punkt realnie odwiedzony"
    # (courier_resolver tier wiarygodności 1, ten sam co last_picked_up_delivery,
    # LEPSZY niż last_assigned_pickup=tier 2 który TU już był). Wykluczenie było
    # przeoczeniem → kandydat spadał do bucketu „other" w _demote_blind_empty mimo
    # top-score (Paweł SC 477329 +12.2 zdegradowany pod gorzej-punktowanych informed).
    "last_picked_up_pickup",
)


def _is_blind_empty_cand(c) -> bool:
    """V3.16: kandydat z synthetic pos (no_gps/pre_shift/none) i pustym bagiem."""
    ps = c.metrics.get("pos_source") if hasattr(c, "metrics") and c.metrics else None
    # Hardening 2026-05-17 (#474227): r6_bag_size jest null gdy feasibility_v2
    # robi early-return przed blokiem R6 (bramka sla_violation:538). Dziś ścieżka
    # bezpieczna (wołane tylko na feasible — ci doszli do R6), ale fallback chain
    # do bag_size_before (:276 bezwarunkowe) / r7_bag_size (:304) usuwa latent
    # fragility i wyrównuje ze spójnością reszty pliku (linie ~421/500/511/766).
    m = c.metrics if (hasattr(c, "metrics") and c.metrics) else {}
    bsize = m.get("r6_bag_size") or m.get("bag_size_before") or m.get("r7_bag_size") or 0
    return ps in BLIND_POS_SOURCES and (bsize or 0) == 0


def _is_informed_cand(c) -> bool:
    """V3.16: kandydat z real pos source (fresh GPS lub recent panel activity)."""
    ps = c.metrics.get("pos_source") if hasattr(c, "metrics") and c.metrics else None
    return ps in INFORMED_POS_SOURCES


def _late_pickup_tier(c) -> int:
    """R-LATE-PICKUP tier kandydata (2026-05-31).

    2 = łamie committed czas_kuriera bag-ordera (>HARD_MAX) — OSTATECZNOŚĆ.
    1 = nowy odbiór potrzebuje przedłużenia (>HARD_MAX).
    0 = na czas (≤HARD_MAX) i nie psuje committed.
    """
    m = (c.metrics if (hasattr(c, "metrics") and c.metrics) else {}) or {}
    if m.get("late_pickup_committed_breach"):
        return 2
    if m.get("new_pickup_needs_extension"):
        return 1
    return 0


def _late_pickup_soft_penalty(c, free_min: float, coeff: float, cap: float) -> float:
    """Opcja B (2026-05-31): gradient kara ∝ max(0, new_pickup_late_min − free_min).

    Gentle (delivery zwykle wygrywa). Cap zapobiega absurdalnym przedłużeniom.
    """
    m = (c.metrics if (hasattr(c, "metrics") and c.metrics) else {}) or {}
    lm = m.get("new_pickup_late_min")
    if not isinstance(lm, (int, float)) or lm <= free_min:
        return 0.0
    return min(cap, coeff * (lm - free_min))


_V325_SCORE_BLOCKED_KEY = "v325_score_blocked"
_V325_BLOCKED_RANK_DELTA_KEY = "v325_blocked_rank_delta"


def _v325_score_blocked(c) -> bool:
    """Jawny stan selekcyjny V325; nigdy nie jest kodowany magicznym score.

    V325 zostawia kandydata w puli (ALWAYS-PROPOSE), ale dla zwykłych sortów
    score ma on pozostać za kandydatami dopuszczonymi przez profil nowego
    kuriera. Do 20.07.2026 stan ten był kodowany przez ``score=-1e9``.
    """
    m = getattr(c, "metrics", None) or {}
    return bool(m.get(_V325_SCORE_BLOCKED_KEY))


def _v325_rank_score(c, score: Optional[float] = None) -> float:
    """Liczbowa oś wewnątrz tej samej klasy blocked/unblocked.

    Dla niezablokowanego kandydata jest to zwykły score. Dla zablokowanego
    przechowujemy wyłącznie realne delty dopisane po V325 (V326/A2/GPS), aby
    zachować dawną kolejność ``-1e9 + delta`` bez sentinela w Candidate.score.
    ``score`` służy shadowowi legacy-R6 i wnosi tylko różnicę względem score.
    """
    actual = getattr(c, "score", 0.0)
    actual = float(actual) if isinstance(actual, (int, float)) else 0.0
    if not _v325_score_blocked(c):
        value = actual if score is None else score
        return float(value) if isinstance(value, (int, float)) else 0.0
    m = getattr(c, "metrics", None) or {}
    delta = m.get(_V325_BLOCKED_RANK_DELTA_KEY, 0.0)
    delta = float(delta) if isinstance(delta, (int, float)) else 0.0
    if score is not None and isinstance(score, (int, float)):
        delta += float(score) - actual
    return delta


def _v325_mark_score_blocked(c) -> None:
    """Oznacz blokadę V325 bez mutowania domenowej wartości ``c.score``."""
    m = getattr(c, "metrics", None)
    if isinstance(m, dict):
        m[_V325_SCORE_BLOCKED_KEY] = True
        m[_V325_BLOCKED_RANK_DELTA_KEY] = 0.0


def _v325_clear_score_blocked(c) -> None:
    """Zdejmij blokadę (solo-rescue / legalna ścieżka V325)."""
    m = getattr(c, "metrics", None)
    if isinstance(m, dict):
        m.pop(_V325_SCORE_BLOCKED_KEY, None)
        m.pop(_V325_BLOCKED_RANK_DELTA_KEY, None)


def _v325_add_score_delta(c, delta: float) -> None:
    """Dodaj realną deltę i zachowaj dawny rank zablokowanych kandydatów."""
    current = getattr(c, "score", 0.0)
    current = float(current) if isinstance(current, (int, float)) else 0.0
    c.score = current + float(delta)
    if _v325_score_blocked(c):
        m = getattr(c, "metrics", None)
        if isinstance(m, dict):
            old = m.get(_V325_BLOCKED_RANK_DELTA_KEY, 0.0)
            old = float(old) if isinstance(old, (int, float)) else 0.0
            m[_V325_BLOCKED_RANK_DELTA_KEY] = old + float(delta)


def _v325_score_rank_key(c):
    """Sort score-desc z jawną osią blocked-last; bez zmiany tie-breaków."""
    return (1 if _v325_score_blocked(c) else 0, -_v325_rank_score(c))


def _v325_score_corridor_key(c):
    """Wariant dotychczasowych sortów score + corridor deviation."""
    m = getattr(c, "metrics", None) or {}
    dev = m.get("bundle_level3_dev")
    return (*_v325_score_rank_key(c), dev if dev is not None else 999.0)


def _is_pre_shift_cand(c) -> bool:
    """Fix #7 477271 (2026-05-31): kurier pre_shift = zmiana jeszcze nie zaczęła →
    nie pracuje, syntetyczna pozycja (clamp do shift_start) → ZAWYŻONY score (Grzegorz
    477271 = 97). Niezależnie od bagu = niska pewność → bucket 2 (jak blind+empty), żeby
    NIE bił aktywnych kurierów mimo zawyżonego score. (Poprzednio pre_shift+bag był
    bucket „other" tylko przypadkiem — nie-w-INFORMED. Teraz EXPLICITNIE.)"""
    ps = c.metrics.get("pos_source") if hasattr(c, "metrics") and c.metrics else None
    return ps == "pre_shift"


def _late_pickup_score_first_key(c, tier: int, orig_rank: int,
                                 free_min: float, coeff: float, cap: float,
                                 score: Optional[float] = None):
    """Opcja B sort key (TESTOWALNY) — naprawia nadkorektę starego tieringu.

    Klucz: (tier-2 ostatecznie na koniec, V3.16 demote-bucket, score − kara_za_późny_odbiór,
    stabilny tie-break). Pickup-lateness KONKURUJE z jakością dowozu (R6/spread w score),
    nie DOMINUJE jak stary tier-primary sort (gdzie tier-0 bił każdy tier-1 niezależnie
    od score → 477330 Andrei −5.3 11.7km bił Michała Ro +36.4).

    `score` override (default = c.score) — używane przez r6_danger_shadow do
    przeliczenia rankingu pod legacy (liniową) karą R6 bez mutacji kandydata.
    """
    bucket = _selection_bucket(c)   # equal-treatment-aware (no_gps/pre_shift po score gdy ON)
    _s = _v325_rank_score(c, score=score)
    adj = _s - _late_pickup_soft_penalty(c, free_min, coeff, cap)
    return (
        1 if tier == 2 else 0,
        bucket,
        1 if _v325_score_blocked(c) else 0,
        -adj,
        orig_rank,
    )


def _post_shift_overrun_penalty_of(c):
    """Post-shift overrun penalty (pkt) z metrics kandydata — WIODĄCY term selekcji
    best_effort gdy ENABLE_POST_SHIFT_OVERRUN_PENALTY. 0.0 gdy flaga OFF / brak
    metryki (→ zero wpływu na sort, zachowanie sprzed zmiany). Wyższa wartość = kurier
    kończy dalej PO zmianie = GORZEJ (sort rosnący → 0 nadwyżki na górze)."""
    if not C.decision_flag("ENABLE_POST_SHIFT_OVERRUN_PENALTY"):
        return 0.0
    m = getattr(c, "metrics", None) or {}
    v = m.get("post_shift_overrun_penalty")
    return float(v) if isinstance(v, (int, float)) else 0.0


def _best_effort_sort_key(c):
    """FEAS-01 (2026-06-06): klucz sortu ścieżki best_effort (feasible=0) — spójny z
    główną selekcją. Czysta funkcja (testowalna).

    PRIMARY = R6 per-order violations + plan.sla_violations (best_effort to już
    kompromis SLA, ale NIE proponuj kandydata GORSZEGO na R6 niż inny w puli — ta
    sama prymarność co stary klucz). Dalej bucket pos_source (informed=0 / other=1 /
    blind+empty|pre_shift=2) — informed z REALNĄ pozycją bije no_gps z FIKCYJNYM
    BIALYSTOK_CENTER (mirror _demote_blind_empty + _late_pickup_score_first_key z
    głównej ścieżki). Potem -score, na końcu total_duration_min (stabilny tie-break).

    r6_pov=99 gdy brak metrics (mirror lokalnego _r6_pov_count — kandydat bez danych
    na dół, NIE na górę).
    """
    if not (hasattr(c, "metrics") and c.metrics):
        r6_pov = 99
    else:
        _pov = c.metrics.get("r6_per_order_violations")
        r6_pov = len(_pov) if _pov else 0
    bucket = _selection_bucket(c)   # equal-treatment-aware (wspólne z _late_pickup_score_first_key)
    plan = c.plan
    sla = getattr(plan, "sla_violations", 0) or 0
    dur = getattr(plan, "total_duration_min", 0.0) or 0.0
    score = getattr(c, "score", 0.0) or 0.0
    # Post-shift overrun WIODĄCY (Adrian 2026-06-24): kurier kończący PO zmianie
    # spada poniżej kończących w oknie (0 nadwyżki). 0.0 gdy flaga OFF = sort
    # identyczny jak wcześniej.
    ps_pen = _post_shift_overrun_penalty_of(c)
    return (ps_pen, r6_pov, sla, bucket, -score, dur)


def _best_effort_fastest_pickup_key(c, new_order_id):
    """SHADOW (Adrian 2026-06-15): klucz selekcji „NAJSZYBSZY ODBIÓR → potem najszybszy
    dowóz". PRIMARY = projektowany czas DOJAZDU DO ODBIORU nowego ordera (kiedy kurier
    dotrze do restauracji = plan.pickup_at[oid]). SECONDARY = projektowana dostawa
    (predicted_delivered_at). TERTIARY = bucket pos_source (informed<other<blind) —
    tie-break, by NIE ufać fikcyjnej pozycji blind (BIALYSTOK_CENTER) przy równym ETA.
    None → +inf (na dół). Czysta funkcja. LOG-ONLY do czasu walidacji shadow."""
    plan = getattr(c, "plan", None)
    BIG = float("inf")
    pu = dv = None
    if plan is not None:
        _pu = (getattr(plan, "pickup_at", {}) or {}).get(new_order_id)
        _dv = (getattr(plan, "predicted_delivered_at", {}) or {}).get(new_order_id)
        try:
            pu = _pu.timestamp() if _pu is not None else None
            dv = _dv.timestamp() if _dv is not None else None
        except Exception:
            pu = dv = None
    # Sprint3 NO-GPS-EQUAL (29.06): bucket pozycji z JEDNEGO źródła `_selection_bucket`
    # (equal-treatment-aware). Było inline-kopią informed0/blind|pre_shift2 sprzed
    # equal-treatment — ten klucz jest SHADOW/LOG-ONLY (l.~6711 → metrics _shadow, NIE
    # zmienia realnego best), ale unifikujemy by ewentualny awans nie wskrzesił dyskryminacji
    # (wzorzec #2 „klasa wraca"). Forward-ref OK (def runtime). Zero-live-impact.
    bucket = _selection_bucket(c)
    return (pu if pu is not None else BIG, dv if dv is not None else BIG, bucket)


def _new_delivered_at_dt(c, new_oid):
    """predicted_delivered_at[new] kandydata (datetime|None). Adrianowa metryka
    „najwcześniej do klienta" = min total (spóźnienie+dowóz), bo committed stały dla
    zlecenia → min delivered_at = min total. Czysta funkcja (testowalna). None gdy brak
    planu / klucza. UWAGA: tylko ODCZYT do shadow-komparatora, NIE zmienia selekcji."""
    plan = getattr(c, "plan", None)
    if plan is None:
        return None
    return (getattr(plan, "predicted_delivered_at", {}) or {}).get(new_oid)


def _pre_shift_too_late_verdict_pass(candidates, prep_remaining_min, order_id=None):
    """Legacy F1.8e pre_shift_too_late — JEDNO źródło predykatu (v3 HOIST po recenzji delty).

    Kurier pre_shift nie zdąży na pickup_ready (start zmiany > prep_remaining):
    hard exclude (verdict NO). Semantyka, komunikat i layer=L5 przeniesione 1:1
    z pętli display F1.7, skąd blok został WYNIESIONY — pętla display już NIE
    powtarza predykatu (zero duplikacji, jedno źródło).

    POWÓD HOISTA (bloker recenzji delty v2): donor filter
    `_nogps_neutral_score_pass` czyta `feasibility_verdict` w chwili passu —
    werdykty muszą być FINALNE przed passem. ZAKOTWICZONY pre_shift
    (anchor/bag-tail ⇒ road_km_from_synthetic_pos=False ⇒ donor) z przyszłym
    NO zasilałby medianę kilometrem HARD-NO. Po hoiście: między passem a
    selekcją NIE zachodzi już ŻADNA mutacja werdyktu w _assess_order_impl.

    Aktywny TYLKO przy ENABLE_V324A_SCHEDULE_INTEGRATION=False (legacy —
    stała modułowa, default ON, brak klucza w flags.json ⇒ w prodzie ścieżka
    LATENTNA; przy ON hard-reject >60 min deleguje warstwa B5 feasibility —
    PRZED budową kandydatów, więc werdykt i tak finalny przed passem).
    Idempotentny (powtórny zapis NO = ten sam stan). Zwraca liczbę odrzuconych.
    """
    if C.ENABLE_V324A_SCHEDULE_INTEGRATION:
        return 0
    rejected = 0
    for c in candidates:
        m = getattr(c, "metrics", None)
        if not m or m.get("pos_source") != "pre_shift":
            continue
        shift_min = float(m.get("shift_start_min") or 0.0)
        if shift_min > prep_remaining_min + 0.01:
            # L7.3: zapis werdyktu w L5 (feasibility) — kanonizowany przez setter
            # (layer=L5 ⇒ garda cicha; zachowanie NIEZMIENIONE).
            _set_feasibility_verdict(c, "NO", layer="L5", order_id=order_id)
            c.feasibility_reason = (
                f"pre_shift_too_late (start za {shift_min:.0f} min, "
                f"odbiór za {prep_remaining_min:.0f} min)"
            )
            rejected += 1
    return rejected


def _route_order_override_shadow_pass(candidates, now=None):
    """Dopnij would-apply route-order do metrics PO zakończonej selekcji.

    Manual-seq dotyczy AKTYWNEGO worka, podczas gdy plan kandydata zawiera też
    nowe rozważane zlecenie. Dlatego engine-seq to projekcja `plan.sequence` na
    id z `bag_context`. Wspólny walidator `operator_route_override.pin_stops`
    sprawdza wpis, TTL, pełną permutację i konstrukcję tak samo jak oba writery
    plan_recheck. Brak wpisu/ważnego planu = brak pól (zero szumu).

    Caller uruchamia pass dopiero PO `_selection.select_and_emit` i finalnym
    firewallu: funkcja może zmienić wyłącznie `Candidate.metrics`, nigdy
    score/verdict/plan/winner.
    Metryki płyną automatycznie do LOCATION A+B przez deny-list serializer.
    """
    produced = 0
    try:
        from dispatch_v2 import operator_route_override as _route_override
    except Exception:
        return produced

    for candidate in candidates or []:
        try:
            metrics = getattr(candidate, "metrics", None)
            plan = getattr(candidate, "plan", None)
            sequence = getattr(plan, "sequence", None) if plan is not None else None
            bag_context = metrics.get("bag_context") if isinstance(metrics, dict) else None
            if not isinstance(bag_context, list) or not sequence:
                continue
            active_ids = [
                str(item.get("order_id"))
                for item in bag_context
                if isinstance(item, dict) and item.get("order_id")
            ]
            if not active_ids or len(set(active_ids)) != len(active_ids):
                continue
            active_set = set(active_ids)
            engine_ids = [str(oid) for oid in sequence if str(oid) in active_set]
            shadow = _route_override.shadow_metrics_for_route(
                str(candidate.courier_id), active_ids, engine_ids, now,
            )
            if shadow:
                metrics["route_order_would_apply"] = shadow["route_order_would_apply"]
                metrics["route_order_manual_seq"] = shadow["route_order_manual_seq"]
                metrics["route_order_engine_seq"] = shadow["route_order_engine_seq"]
                metrics["route_order_divergence"] = shadow["route_order_divergence"]
                produced += 1
        except Exception as exc:
            log.warning(
                "ROUTE_ORDER_SHADOW fail-soft cid=%s: %s: %s",
                getattr(candidate, "courier_id", "?"),
                type(exc).__name__, str(exc)[:160],
            )
    return produced


_NOGPS_LEGACY_DECISION_OVERRIDE = __import__("contextvars").ContextVar(
    "nogps_legacy_decision_override", default=None)


def _nogps_neutral_score_pass(candidates, order_id=None, *, apply_on=None):
    """NOGPS-NEUTRAL-SCORE (2026-07-19, memory ziomek-nogps-center-score-bug-2026-07-19).

    BUG: kurier bez GPS planowany w BIALYSTOK_CENTER (courier_resolver
    _synthetic_pos_fallback) — ta fikcja zasilała SCORE (s_dystans=100·exp(-km/5)
    z centrum ≈ sufit przy centralnych restauracjach), a F1.7 neutralizował
    tylko DISPLAY (km=śr. floty) PO zamrożeniu score. Efekt: no-GPS 24.8% puli
    → 50.5% zwycięzców (regresja ENABLE_NO_GPS_EQUAL_TREATMENT: zdjęty demote,
    został ukryty bonus centrum). Komentarz F1.7 „score z centrum ~mediana floty,
    nie faworyzuje" był FAŁSZYWYM założeniem.

    FIX U ŹRÓDŁA: dla kandydatów, których road_km policzono z pozycji-fikcji
    (metrics.road_km_from_synthetic_pos z core.candidates — anchor/bag-tail
    ZOSTAJE realny) i pos_source ∈ POSITION_UNKNOWN_SOURCES, licz neutralny
    dystans = MEDIANA road_km kandydatów o pozycji ZNANEJ **i wykonalnych**
    (feasibility_verdict == "MAYBE" — v2 po recenzji adwersaryjnej pkt #2:
    HARD-NO nie jest konkurentem, jego km nie może zniekształcać mediany;
    mediana, nie średnia — rozkład prawoskośny) i PRZELICZ s_dystans/score.
    Jedna wartość napędza i score, i display (kasuje rozjazd score↔display).

    Kontrakt (compute-always-for-shadow, lekcja #186 / wzorzec #16):
      - metryki bonus_nogps_neutral_* liczone ZAWSZE (auto-serializacja L1.1
        LOCATION A+B przez prefix bonus_),
      - APLIKACJA do c.score + metrics["score"] WYŁĄCZNIE za
        ENABLE_NO_GPS_NEUTRAL_SCORE_DIST (decision_flag, default OFF) —
        OFF = zachowanie bajt-w-bajt jak przed zmianą.
    KOMPONUJE z ENABLE_NO_GPS_EQUAL_TREATMENT (bucket'y bez zmian — no-GPS
    dalej konkuruje w bucket 0, ale po UCZCIWYM score). NIE flipować
    equal-treatment OFF (przywraca starą karę — odwrotna nadkorekta).
    post_wave/anchor/bag-tail: road realny ⇒ nietknięte z konstrukcji.

    Zwraca (neutral_km, applied_count) — dla logu/testów. Fail-soft: wyjątek
    per-kandydat nie psuje puli (hot path, Lekcja #32 — loguj, nie milcz).
    """
    known_kms = []
    for c in candidates:
        m = getattr(c, "metrics", None)
        if not m:
            continue
        if m.get("road_km_from_synthetic_pos"):
            continue
        # v2 (recenzja adwersaryjna pkt #2): donor mediany = WYKONALNY konkurent.
        # Kanon werdyktu = pole `feasibility_verdict` ("MAYBE"|"NO", filtr selekcji
        # core/selection.py). HARD-NO (post-shift, R-35MIN, pickup_too_far…) nie
        # konkuruje o zlecenie, więc jego km nie może zniekształcać neutralnego
        # dystansu. Werdykty w chwili passu są FINALNE (v3): L5 przy budowie
        # kandydata + legacy F1.8e pre_shift_too_late WYNIESIONY przed pass
        # (_pre_shift_too_late_verdict_pass — zakotwiczony pre_shift ma road
        # realny ⇒ JEST donorem, jego NO musi zapaść przed medianą).
        if getattr(c, "feasibility_verdict", None) != "MAYBE":
            continue
        km = m.get("km_to_pickup")
        if isinstance(km, (int, float)):
            known_kms.append(float(km))
    if known_kms:
        known_kms.sort()
        _n = len(known_kms)
        _mid = _n // 2
        neutral_km = (known_kms[_mid] if _n % 2 == 1
                      else 0.5 * (known_kms[_mid - 1] + known_kms[_mid]))
    else:
        # 0 donorów (brak realnych kotwic ALBO wszystkie HARD-NO) →
        # mirror F1.7 fallback (5.0 km).
        neutral_km = 5.0
    if apply_on is None:
        apply_on = _NOGPS_LEGACY_DECISION_OVERRIDE.get()
    if apply_on is None:
        apply_on = C.decision_flag("ENABLE_NO_GPS_NEUTRAL_SCORE_DIST")
    applied = 0
    sd_new = _scoring_nn.s_dystans(float(neutral_km))
    for c in candidates:
        m = getattr(c, "metrics", None)
        if not m:
            continue
        if not (m.get("road_km_from_synthetic_pos")
                and m.get("pos_source") in POSITION_UNKNOWN_SOURCES):
            continue
        try:
            old_km = m.get("km_to_pickup")
            if not isinstance(old_km, (int, float)):
                continue
            sr = m.get("score")
            _comp = sr.get("components") if isinstance(sr, dict) else None
            sd_old = (_comp.get("dystans")
                      if isinstance(_comp, dict)
                      and isinstance(_comp.get("dystans"), (int, float))
                      else _scoring_nn.s_dystans(float(old_km)))
            delta = round(_scoring_nn.W_DYSTANS * (sd_new - sd_old), 2)
            # SHADOW ZAWSZE (flip-walidacja ETAP-5 czyta z shadow_decisions):
            m["bonus_nogps_neutral_raw_km"] = round(float(old_km), 2)
            m["bonus_nogps_neutral_km"] = round(float(neutral_km), 2)
            m["bonus_nogps_neutral_dist_delta"] = delta
            m["bonus_nogps_neutral_applied"] = bool(apply_on)
            if apply_on:
                c.score = c.score + delta
                if isinstance(sr, dict):
                    if isinstance(_comp, dict) and "dystans" in _comp:
                        _comp["dystans"] = round(sd_new, 2)
                    if isinstance(sr.get("total"), (int, float)):
                        sr["total"] = round(sr["total"] + delta, 2)
                applied += 1
        except Exception as _nn_e:
            log.warning(
                f"NOGPS_NEUTRAL_SCORE fail-soft order={order_id} "
                f"cid={getattr(c, 'courier_id', '?')}: "
                f"{type(_nn_e).__name__}: {_nn_e}")
    if applied:
        log.info(
            f"NOGPS_NEUTRAL_SCORE order={order_id} applied={applied} "
            f"neutral_km={neutral_km:.2f} (median of {len(known_kms)} known)")
    return neutral_km, applied


def _select_with_position_model_shadow(
    selection_ctx,
    candidates,
    *,
    explicit_effective: bool,
    explicit_requested: bool,
    flag_conflict: bool,
):
    """Uruchom actual i kontrfaktyk przez ten sam pełny selektor."""
    import copy as _copy

    counter_mode = "legacy" if explicit_effective else "explicit"
    counter_candidates = []
    for candidate in candidates:
        variants = getattr(candidate, "_position_model_variants", {}) or {}
        variant = variants.get(counter_mode, candidate)
        if variant is not None:
            counter_candidates.append(_copy.deepcopy(variant))
    counter_ctx = _copy.copy(selection_ctx)
    counter_ctx.position_model_mode = counter_mode
    counter_ctx.shadow_only = True
    counter_selected = _selection.select_and_emit(counter_ctx, counter_candidates)
    selected = _selection.select_and_emit(selection_ctx, candidates)
    legacy_result = counter_selected if explicit_effective else selected
    explicit_result = selected if explicit_effective else counter_selected
    selected.position_model_shadow = {
        "schema": "explicit_unknown_position.v1",
        "flag_requested": explicit_requested,
        "flag_effective": explicit_effective,
        "flag_conflict": flag_conflict,
        "selector_path": "core.selection.select_and_emit",
        "legacy_winner_cid": (
            str(legacy_result.best.courier_id) if legacy_result.best is not None else None
        ),
        "explicit_winner_cid": (
            str(explicit_result.best.courier_id) if explicit_result.best is not None else None
        ),
        "would_change_winner": (
            getattr(legacy_result.best, "courier_id", None)
            != getattr(explicit_result.best, "courier_id", None)
        ),
        "legacy_verdict": legacy_result.verdict,
        "explicit_verdict": explicit_result.verdict,
    }
    return selected


def _objm_metric_min(c, k):
    """Metryka liczbowa kandydata albo None — JEDNO ŹRÓDŁO (scalenie 2 kopii
    zagnieżdżonego `_m` z pick/shadow, dedup 2026-07-17)."""
    v = (getattr(c, "metrics", None) or {}).get(k)
    return float(v) if isinstance(v, (int, float)) else None


def _objm_newbag_min(c, new_oid):
    """Bag-time NOWEGO zlecenia: plan per-order → fallback sum_bag_time_min —
    JEDNO ŹRÓDŁO (scalenie 3 kopii zagnieżdżonego `_newbag` z pick/shadow/readmit,
    dedup 2026-07-17). Tier-3 cap dotyczy NOWEGO zlecenia."""
    pod = getattr(getattr(c, "plan", None), "per_order_delivery_times", None) or {}
    v = pod.get(new_oid)
    if isinstance(v, (int, float)):
        return float(v)
    return _objm_metric_min(c, "sum_bag_time_min")


def _feas_carry_reject_kind(c):
    """Klasyfikacja blocking odrzutu (sla / r6_new / r6_carry_delta / other) —
    JEDNO ŹRÓDŁO (scalenie 2 kopii zagnieżdżonego `_kind` z blind-shadow/readmit,
    dedup 2026-07-17)."""
    r = getattr(c, "feasibility_reason", "") or ""
    if r.startswith("sla_violation"):
        return "sla"
    if r.startswith("R6_per_order"):
        return "r6_new"
    if r.startswith("R6_picked_up_delta"):
        return "r6_carry_delta"
    return "other"


def _best_effort_objm_pick(with_plan, new_oid, cap_min=40.0):
    """Carry-aware guarded best_effort pick (case #482817). PRIMARY = objm_r6_breach_max_min
    (carry-inclusive, przez kanon objm_lexr6.lex_qual) zamiast new-pickup-only
    r6_per_order_violations. BEZPIECZNIK nowego zlecenia: carry-min TYLKO wśród kandydatów z
    new-order bag <= cap_min; gdy żaden bezpieczny → fallback pure carry-min (raw).

    JEDNO ŹRÓDŁO PRAWDY dla wyboru objm — używane przez _best_effort_objm_shadow (log) i
    live-flip ENABLE_BEST_EFFORT_OBJM_R6_KEY (selekcja). Pure: zwraca kandydata z with_plan
    lub None (puste/błąd → caller zostaje na carry-ślepym _best_effort_sort_key). Defensywny."""
    try:
        if not with_plan:
            return None

        def _newbag(c):
            return _objm_newbag_min(c, new_oid)

        # JEDNO ŹRÓDŁO PRAWDY tie-breaku = kanon objm_lexr6.lex_qual (post-shift-aware przy
        # ENABLE_POST_SHIFT_OVERRUN_PENALTY: OFF → krotka 3-elem. R6-primary; ON → prepend
        # WIODĄCY post_shift_overrun_penalty — kurier kończący PO zmianie spada, case 483144).
        # Unifikacja 2026-06-25 (objm-lexr6-unify): dawna kopia inline (_ps_pen + _lex_qual)
        # USUNIĘTA — bajt-identyczna z modułem (przy OFF wiodące 0.0 było no-opem w min()),
        # więc zero zmiany zachowania. Parytet pilnuje test_objm_lexr6_unify_2026_06_25.
        from dispatch_v2 import objm_lexr6 as _OL  # liść (common) — brak cyklu; lokalny jak _d2/_shadow

        raw = min(with_plan, key=_OL.lex_qual)
        _safe = [c for c in with_plan if (_newbag(c) is None or _newbag(c) <= cap_min)]
        return min(_safe, key=_OL.lex_qual) if _safe else raw
    except Exception:
        return None


def _best_effort_objm_shadow(
    with_plan, live_best, new_oid, cap_min=40.0, now=None,
) -> None:
    """SHADOW (2026-06-23): co BY wybrała selekcja best_effort, gdyby PRIMARY był carry-inclusive
    objm_r6_breach_max_min (mirror _objm_lexr6_shadow._lex_qual) zamiast new-pickup-only
    r6_per_order_violations (ślepego na carry-ordery — case #482817). LOG-ONLY: pisze TYLKO
    live_best.metrics['best_effort_objm_*'] oraz — wyłącznie za osobną flagą — addytywne
    ``eta_trust_*`` każdego kandydata (auto-serializacja A+B w shadow_dispatcher). NIGDY nie
    mutuje planu/score/feasibility/best/werdyktu. Faithful (sticky-aware) — liczy na DOKŁADNIE
    tych planach co realna selekcja. Defensywny (Lekcja #83: try/except, zero raise).

    BEZPIECZNIK nowego zlecenia (cap_min, 2026-06-23): rekomendacja (pola bez sufiksu raw) =
    carry-min ALE tylko wśród kandydatów z new-order bag <= cap_min (max ~5 min ponad R6=35);
    gdy żaden bezpieczny (nowy order i tak przepada) → fallback do pure carry-min. Sweep 21-23.06:
    cap=40 → regresja nowego 27%→16%, zysk carry 83% utrzymany. `raw` (bez bezpiecznika) logowany
    obok do porównania. cap_min hot przez flags.json BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN."""
    try:
        if not with_plan or live_best is None:
            return
        lm = getattr(live_best, "metrics", None)
        if not isinstance(lm, dict):
            return

        _m = _objm_metric_min

        def _newbag(c):
            return _objm_newbag_min(c, new_oid)

        from dispatch_v2 import objm_lexr6 as _OL  # kanon lex_qual (unifikacja 2026-06-25)

        _cid = lambda c: str(getattr(c, "courier_id", ""))
        live_cid = _cid(live_best)
        # raw = bez bezpiecznika new-order (pure carry-min, kanon objm_lexr6.lex_qual);
        # pick = z bezpiecznikiem (new-order cap) z _best_effort_objm_pick = to samo źródło.
        # Po unifikacji raw i pick używają TEGO SAMEGO klucza → guard_changed izoluje sam cap.
        raw = min(with_plan, key=_OL.lex_qual)
        _safe = [c for c in with_plan if (_newbag(c) is None or _newbag(c) <= cap_min)]
        pick = _best_effort_objm_pick(with_plan, new_oid, cap_min=cap_min) or raw

        flip = _cid(pick) != live_cid
        lm["best_effort_objm_cid"] = _cid(pick)
        lm["best_effort_objm_flip"] = flip
        lm["best_effort_objm_pool"] = len(with_plan)
        lm["best_effort_objm_cap_min"] = round(float(cap_min), 1)
        lm["best_effort_objm_safe_n"] = len(_safe)
        lm["best_effort_objm_raw_cid"] = _cid(raw)
        lm["best_effort_objm_guard_changed"] = _cid(pick) != _cid(raw)
        lm["best_effort_objm_live_r6"] = round(_m(live_best, "objm_r6_breach_max_min") or 0.0, 1)
        lm["best_effort_objm_pick_r6"] = round(_m(pick, "objm_r6_breach_max_min") or 0.0, 1)

        # === ESKALACJA Tier 2 (pierwszy-wolny) — reguła Adriana 3-stopniowa ===
        # Tier 2: daj nowe kurierowi z min free_at (kończy obecny worek najwcześniej); odbiera
        # nowe PO rozładowaniu → obecne nietknięte. Akceptowalny gdy zwalnia się ≤ próg, inaczej
        # Tier 3 = `pick` (carry-aware cap-stretch). LOG-ONLY (mierzy selekcję eskalacji).
        def _free_at(c):
            v = (getattr(c, "metrics", None) or {}).get("free_at_min")
            return float(v) if isinstance(v, (int, float)) else None
        _t2 = min(with_plan, key=lambda c: (_free_at(c) if _free_at(c) is not None else 9e9))
        _t2_free = _free_at(_t2)
        _trust_on = C.decision_flag("ENABLE_BEST_EFFORT_ESC_TRUSTED_ETA")
        if _trust_on:
            try:
                _trust_evidence = _eta_trust.load_eta_trust_evidence()
            except Exception as _trust_load_err:  # fail-closed: 30, nigdy 90
                _trust_evidence = _eta_trust.unavailable_evidence(
                    "loader_error:" + type(_trust_load_err).__name__
                )
            _trust_now = now or datetime.now(timezone.utc)
            for _trust_c in with_plan:
                _trust_m = getattr(_trust_c, "metrics", None)
                if not isinstance(_trust_m, dict):
                    continue
                _trust_m.update(_eta_trust.eta_trust_metrics(
                    getattr(_trust_c, "courier_id", None),
                    _trust_m,
                    _trust_evidence,
                    _trust_now,
                ))
                _trust_m["eta_trust_t2_candidate"] = _trust_c is _t2
        _esc_max = C.flag("BEST_EFFORT_ESC_TIER2_MAX_FREE_MIN",
                          getattr(C, "BEST_EFFORT_ESC_TIER2_MAX_FREE_MIN", 30.0))
        if _trust_on:
            _esc_max = _eta_trust.trusted_tier2_max_free_min(
                _esc_max,
                bool((getattr(_t2, "metrics", None) or {}).get("eta_trust_ok")),
            )
        if _t2_free is not None and _t2_free <= _esc_max:
            _esc_tier, _esc_cid = 2, _cid(_t2)
        else:
            _esc_tier, _esc_cid = 3, _cid(pick)
        lm["best_effort_objm_t2_cid"] = _cid(_t2)
        lm["best_effort_objm_t2_free_min"] = round(_t2_free, 1) if _t2_free is not None else None
        lm["best_effort_objm_t2_bag"] = (getattr(_t2, "metrics", None) or {}).get("bag_size_before")
        lm["best_effort_objm_esc_tier"] = _esc_tier
        lm["best_effort_objm_esc_cid"] = _esc_cid
        lm["best_effort_objm_esc_vs_live"] = _esc_cid != live_cid
        lm["best_effort_objm_esc_max_free"] = round(float(_esc_max), 1)
        if flip:
            _ln, _pn = _newbag(live_best), _newbag(pick)
            lm["best_effort_objm_d_r6"] = round(
                (_m(pick, "objm_r6_breach_max_min") or 0.0)
                - (_m(live_best, "objm_r6_breach_max_min") or 0.0), 1)
            lm["best_effort_objm_d_committed"] = round(
                (_m(pick, "late_pickup_committed_max") or 0.0)
                - (_m(live_best, "late_pickup_committed_max") or 0.0), 1)
            lm["best_effort_objm_live_newbag"] = round(_ln, 1) if _ln is not None else None
            lm["best_effort_objm_pick_newbag"] = round(_pn, 1) if _pn is not None else None
            lm["best_effort_objm_d_newbag"] = (round(_pn - _ln, 1)
                                               if (_ln is not None and _pn is not None) else None)
            try:
                log.info(
                    "BEST_EFFORT_OBJM_SHADOW oid=%s live=%s pick=%s(raw=%s cap=%s) dR6=%s dNewBag=%s pool=%d"
                    % (new_oid, getattr(live_best, "courier_id", None),
                       _cid(pick), _cid(raw), lm["best_effort_objm_cap_min"],
                       lm["best_effort_objm_d_r6"], lm.get("best_effort_objm_d_newbag"),
                       len(with_plan)))
            except Exception:
                pass
    except Exception as _e:
        try:
            log.warning("best_effort_objm_shadow fail oid=%s: %r" % (new_oid, _e))
        except Exception:
            pass


# _selection_veto_winner — RETIRED 2026-06-11 (ACK Adrian po digescie at#113;
# A2 soft-score dowiózł, veto nadpisywałoby legalne decyzje — werdykt 08.06).


# _r6_breach_guard_winner (R6BREACH-01/GATE-02) — RETIRED 2026-06-11 (Adrian:
# „duplikat R6 = R6BREACH, wytnij"). Nigdy nie zebrał danych (flaga OFF od
# commitu, 0/2452 rekordów non-null). Oś R6 pokrywają: late-pickup hard gate,
# OBJ_R6_SOFT_DEADLINE, best_effort_r6_breach (OBJ F3), A2 soft-score, a po
# flipie BUG-A także kara max_bag_time. Historia: commit f64ff81 + werdykt veto.


def _r6_soft_penalty(r6_max_bag_time, soft_min: float, per_min: float,
                     danger_on: bool, danger_min: float, danger_per_min: float,
                     cap_floor=None):
    """R6-soft kara (Fix #6 2026-05-31) — liniowa nad soft_min + EKSTRA stroma w danger zone.

    Strefa soft_min..danger_min (30-32): liniowa -per_min/min (normalny bufor R-BUFFER-OK).
    Strefa danger_min..35 (32-35): EKSTRA -danger_per_min/min (near-limit ryzykowne — jeden
    korek od zimnego/SLA breach >35, ryzyko nieliniowe → kara nieliniowa).

    cap_floor (E7 2026-06-17, robustness): gdy podany (np. -2000.0), kara NIE schodzi
    poniżej floor. Cel = uodpornić score/LGBM na astronomiczne wartości z zombie-pickup
    (r6_max_bag_time liczone z dni → kara ~ -240000). Próg -2000 dobrany replayem flipów
    (eod_drafts/2026-06-17/r6cap_flip_replay.py): 0 zmian selekcji na 7d (kandydat z karą
    < -2000 i tak jest zdominowany — cap to czysta higiena, nie zmiana decyzji).
    Zwraca (penalty, legacy_linear_penalty, raw_penalty) — raw = przed capem (telemetria);
    legacy = sama liniowa baza dla shadow (też przed capem).
    """
    if r6_max_bag_time is None or r6_max_bag_time <= soft_min:
        return 0.0, 0.0, 0.0
    legacy = -(r6_max_bag_time - soft_min) * per_min
    pen = legacy
    if danger_on and r6_max_bag_time > danger_min:
        pen -= (r6_max_bag_time - danger_min) * danger_per_min
    raw = pen
    if cap_floor is not None and pen < cap_floor:
        pen = float(cap_floor)
    return pen, legacy, raw


# V3.26 STEP 5 (R-06): cache restaurant_name → district lookup at module load.
# 98 entries w restaurant_coords.json — load once, build NAME → STREET map.
_V326_RESTAURANT_DISTRICT_CACHE = None


def _v326_load_restaurant_district_map():
    """Build NAME → district map z restaurant_coords.json + drop_zone_from_address.
    V3.26 R-06 Adrian corrections: overrides layer (restaurant_district_overrides.json)
    applied LAST — highest priority. Cached after first call.
    Returns dict {company_name_lower: district_name}."""
    global _V326_RESTAURANT_DISTRICT_CACHE
    if _V326_RESTAURANT_DISTRICT_CACHE is not None:
        return _V326_RESTAURANT_DISTRICT_CACHE
    out = {}
    try:
        import json as _json
        from dispatch_v2.common import drop_zone_from_address as _dza
        with open("/root/.openclaw/workspace/dispatch_state/restaurant_coords.json") as _f:
            data = _json.load(_f)
        for _, entry in data.items():
            name = (entry.get("company") or "").strip()
            street = (entry.get("street") or "").strip()
            city = (entry.get("city") or DEFAULT_CITY).strip()
            if not name:
                continue
            district = _dza(street, city) if street else "Unknown"
            out[name.lower()] = district
    except Exception as e:
        log.warning(f"V326_RESTAURANT_DISTRICT_CACHE build fail: {e}")
    # V3.26 R-06 Adrian ground truth overrides (commit post-R07-shadow).
    # File format: {restaurant_name: district_name} + "_meta" block.
    try:
        import json as _json2
        with open("/root/.openclaw/workspace/dispatch_state/restaurant_district_overrides.json") as _fo:
            overrides = _json2.load(_fo)
        _applied = 0
        for k, v in overrides.items():
            if k.startswith("_"):  # skip _meta
                continue
            if not isinstance(k, str) or not isinstance(v, str):
                continue
            out[k.lower()] = v
            _applied += 1
        log.info(f"V326_RESTAURANT_DISTRICT overrides applied: {_applied} entries")
    except FileNotFoundError:
        pass  # no overrides file — OK
    except Exception as e2:
        log.warning(f"V326_RESTAURANT_DISTRICT overrides load fail: {e2}")
    _V326_RESTAURANT_DISTRICT_CACHE = out
    log.info(f"V326_RESTAURANT_DISTRICT_CACHE built: {len(out)} entries")
    return out


def _v326_resolve_pickup_district(restaurant_name):
    """Resolve restaurant name → district name. Fallback 'Unknown'."""
    if not restaurant_name:
        return "Unknown"
    cache = _v326_load_restaurant_district_map()
    return cache.get(str(restaurant_name).strip().lower(), "Unknown")


def _v326_multistop_trajectory(feasible: list, new_order, order_id=None) -> list:
    """V3.26 STEP 5 (R-06 MULTI-STOP-TRAJECTORY).

    Per candidate z bag_size >= 1 i pos_source != 'no_gps':
    - Find last_drop_district z bag_context (use delivery_address)
    - Find new_pickup_district via restaurant lookup
    - Classify trajectory → bonus/penalty per V326_R06_* constants
    - Skip cand without bag, no_gps pos, brak coords/addresses → no adjustment

    Re-sorts feasible po score desc.
    """
    try:
        flag = bool(getattr(C, "ENABLE_V326_MULTISTOP_TRAJECTORY", False))
    except Exception:
        flag = False
    if not flag or not feasible:
        return feasible
    from dispatch_v2.common import (
        BIALYSTOK_DISTRICT_ADJACENCY,
        drop_zone_from_address,
    )
    from dispatch_v2.districts_data import classify_trajectory

    # Resolve new_pickup_district once (same dla wszystkich candidates)
    new_restaurant = getattr(new_order, "restaurant", None)
    if new_restaurant is None:
        new_restaurant = (new_order.__dict__.get("restaurant") if hasattr(new_order, "__dict__") else None)
    new_pickup_district = _v326_resolve_pickup_district(new_restaurant)

    bonus_map = {
        'SAME': float(getattr(C, "V326_R06_BONUS_SAME", 40.0)),
        'SIMILAR': float(getattr(C, "V326_R06_BONUS_SIMILAR", 15.0)),
        'SIDEWAYS': float(getattr(C, "V326_R06_PENALTY_SIDEWAYS", -10.0)),
        'OPPOSITE': float(getattr(C, "V326_R06_PENALTY_OPPOSITE", -40.0)),
        'UNKNOWN': 0.0,
    }

    for cand in feasible:
        m = getattr(cand, "metrics", {}) or {}
        bag_size = m.get("bag_size_before") or 0
        pos_source = m.get("pos_source")
        # SKIP path (per Adrian R-06 spec):
        # - bag < min_bag: bag=0 nie ma "ostatniego" dropu (bag=1 MA — fix
        #   V326-H2 flag-gated: min 2→1 gdy ENABLE_V326_R06_BAG1_FIX True;
        #   default zostaje "<2" identycznie jak pre-fix dla bag=2 PASS)
        # - pos_source=no_gps (synthetic pos, brak realnej trajektorii)
        _r06_min_bag = 1 if getattr(C, "ENABLE_V326_R06_BAG1_FIX", False) else 2
        if bag_size < _r06_min_bag or pos_source == "no_gps":
            m["v326_r06_relation"] = None
            m["v326_r06_bonus"] = 0.0
            m["v326_r06_skip_reason"] = (
                f"bag={bag_size}<{_r06_min_bag}"
                if bag_size < _r06_min_bag else "no_gps"
            )
            continue
        # Find last_drop_district from bag_context
        bc = m.get("bag_context") or []
        if not bc:
            m["v326_r06_relation"] = None
            m["v326_r06_bonus"] = 0.0
            m["v326_r06_skip_reason"] = "no_bag_context"
            continue
        # Heuristic: last entry w bag_context (najnowszy assignment).
        # TODO V3.27: użyj plan.predicted_delivered_at dla precyzyjnego "last".
        last_drop_addr = bc[-1].get("delivery_address") if bc else None
        last_drop_district = drop_zone_from_address(last_drop_addr, DEFAULT_CITY)
        relation, detail = classify_trajectory(
            last_drop_district, new_pickup_district, BIALYSTOK_DISTRICT_ADJACENCY
        )
        bonus = bonus_map.get(relation, 0.0)
        _v325_add_score_delta(cand, bonus)
        m["v326_r06_relation"] = relation
        m["v326_r06_bonus"] = bonus
        m["v326_r06_drop_district"] = last_drop_district
        m["v326_r06_pickup_district"] = new_pickup_district
        m["v326_r06_detail"] = detail
        if bonus != 0.0:
            log.info(
                f"V326_R06 order={order_id} cid={cand.courier_id} "
                f"{relation} ({detail}) → {bonus:+.0f}"
            )
    feasible.sort(key=_v325_score_corridor_key)
    return feasible


# ── A2 reliability soft-score (2026-06-07, dźwignia A2 z audytu autonomii 03.06) ──
# Kara score ∝ nadwyżka breach_rate kuriera nad medianą floty, z confidence-gatingiem.
# Metoda 1:1 z tools/a2_selection_shadow.py (zwalidowana offline na realnych wynikach).
_A2_FEED_CACHE = {"mtime": None, "data": (None, None, None)}


def _load_courier_reliability():
    """(breach_by_cid, conf_by_cid, fleet_median) z courier_reliability.json.
    Cache wg mtime; brak/zły plik → (None, None, None) + log (fail-safe = brak kary)."""
    import os as _os2
    p = getattr(C, "A2_RELIABILITY_FEED_PATH", "")
    try:
        mt = _os2.path.getmtime(p)
    except OSError:
        return (None, None, None)
    if _A2_FEED_CACHE["mtime"] == mt:
        return _A2_FEED_CACHE["data"]
    data = (None, None, None)
    try:
        import json as _json
        d = _json.load(open(p, encoding="utf-8"))
        fm = d.get("fleet_median_breach_rate")
        cr = d.get("couriers") or {}
        if not isinstance(fm, (int, float)):
            raise ValueError("brak fleet_median_breach_rate")
        breach = {
            str(k): v.get("breach_rate")
            for k, v in cr.items()
            if isinstance(v, dict) and isinstance(v.get("breach_rate"), (int, float))
        }
        conf = {
            str(k): str(v.get("confidence", "low"))
            for k, v in cr.items() if isinstance(v, dict)
        }
        data = (breach, conf, float(fm))
    except Exception as _e:
        log.error(f"A2 reliability feed load fail ({p}): {_e!r} — kara=0")
    _A2_FEED_CACHE.update(mtime=mt, data=data)
    return data


def _a2_reliability_delta(cid, breach, conf, fleet_median, coeff, min_gap):
    """Kara = -coeff*max(0, breach-median); 0 gdy nieznany cid / gap<min_gap / confidence=='low'."""
    if not breach:
        return 0.0
    br = breach.get(str(cid))
    if br is None:
        return 0.0
    gap = br - fleet_median
    if gap < min_gap:
        return 0.0
    if str((conf or {}).get(str(cid), "low")) == "low":
        return 0.0
    return -coeff * max(0.0, gap)


def _e2_ab_arm(order_id) -> str:
    """E2 20% live A/B split (deterministyczny po order_id): 'pln' (20%) | 'score'."""
    try:
        return "pln" if (int(str(order_id)) % 5 == 0) else "score"
    except (TypeError, ValueError):
        import hashlib
        h = int(hashlib.md5(str(order_id).encode()).hexdigest(), 16)
        return "pln" if (h % 5 == 0) else "score"


def _pln_pure_resort(top) -> None:
    """E2: sortuj `top` po pln_v (pay-aware). In-place.

    FIX 2026-06-17 (bug tier2): czysty sort po pln_v IGNOROWAŁ twardy demote tier2
    (łamanie committed odbioru) + buckety GPS → pay-pick łamał cudzy committed o
    5–28 min dla drobnego zysku pay (audit: 23 vs 8 wymuszonych złamań / 3 dni,
    ΣΔpln_v=117 = marny zysk, mediana +2,83/flip). pln NIE może liczyć tylko po
    wynagrodzeniu — zła jakość = utrata klienta. Gdy ENABLE_PLN_RESORT_WITHIN_TIER
    ON: sortuj W OBRĘBIE tieru/bucketu — `(tier2 na koniec, bucket informed>other>
    blind, -pln_v)` — tier2 NIGDY nie bije tier0/1; pay-aware decyduje tylko
    wewnątrz tego samego tieru (eksperyment zachowany tam, gdzie bezpieczny).
    Flaga OFF (default) = legacy czysty pln_v (porównanie A/B)."""
    if not top:
        return
    _orig = {id(c): i for i, c in enumerate(top)}
    _within = C.flag("ENABLE_PLN_RESORT_WITHIN_TIER", False)
    # C (2026-06-17): pln_v quality-aware — kara za faktyczny R6-breach planu +
    # spóźniony NOWY odbiór, by pln NIE liczył tylko po wynagrodzeniu (zła jakość =
    # utrata klienta). pln_v ma już P(breach) statystyczny + lezenie, ale NIE realny
    # R6/late tego planu. Aplikowane TYLKO z within-tier (gated). Wagi env-override.
    # OFF = czysty pln_v w obrębie tieru (polityka B).
    _quality = C.flag("ENABLE_PLN_QUALITY_AWARE", False)
    _q_r6 = float(getattr(C, "PLN_QUALITY_R6_COEFF", 0.5))
    _q_late = float(getattr(C, "PLN_QUALITY_LATE_COEFF", 0.3))
    _q_free = float(getattr(C, "PLN_QUALITY_LATE_FREE_MIN", 5.0))

    def _pln_v_of(c):
        pv = (getattr(c, "metrics", None) or {}).get("pln_v")
        return float(pv) if isinstance(pv, (int, float)) else None

    def _pln_ord(c):
        pv = _pln_v_of(c)
        return -pv if pv is not None else float("inf")

    def _pln_ord_quality(c):
        pv = _pln_v_of(c)
        if pv is None:
            return float("inf")
        m = getattr(c, "metrics", None) or {}
        r6 = m.get("objm_r6_breach_max_min") or 0.0
        late = m.get("new_pickup_late_min") or 0.0
        pv = pv - _q_r6 * max(0.0, float(r6)) - _q_late * max(0.0, float(late) - _q_free)
        return -pv

    # B2 FIX (audyt 2026-06-28): bylo inline _bucket sprzed equal-treatment (demote
    # no_gps/pre_shift do bucketu 2) -> teraz wspolny _selection_bucket (equal-treatment-
    # aware, ta sama szuflada co reszta selekcji; sterowane ENABLE_EQUAL_TREATMENT_BUCKET).
    # Replay 10d: 49/378 decyzji E2-arm stary demote zmienial pick, 100% przeciw
    # no_gps/pre_shift. Twin z _objm_lexr6_shadow (nizej) naprawiony RAZEM.
    _pln_key = _pln_ord_quality if (_within and _quality) else _pln_ord
    if _within:
        def _key(c):
            return (1 if _late_pickup_tier(c) == 2 else 0, _selection_bucket(c), _pln_key(c), _orig[id(c)])
    else:
        def _key(c):
            return (_pln_ord(c), _orig[id(c)])

    _pre = id(top[0])
    top.sort(key=_key)
    if id(top[0]) != _pre and isinstance(getattr(top[0], "metrics", None), dict):
        top[0].metrics["pln_ab_flipped"] = True


def _objm_lexr6_shadow(top, feasible, order_id=None) -> None:
    """D2 SHADOW (2026-06-17): R6-breach-primary lexicographic selektor W OBRĘBIE grupy
    (tier × bucket) zwycięzcy. OBSERWACYJNY — pisze TYLKO top[0].metrics['objm_lexr6_*']
    (prefix objm_ → auto-serializowany w shadow_dispatcher), NIGDY nie mutuje top/feasible/
    werdyktu. Replay-harness 2026-06-17 wskazał tę selekcję jako jedyny czysty zysk
    (−577 min twardych spóźnień / 7d na 54 naprawionych, +23 new-late/+41 idle). Faza 1 =
    walidacja na żywo; live-flip selekcji = OSOBNA flaga ENABLE_OBJM_LEXR6_SELECT + ACK.
    Grupa = ten sam (tier,bucket) co live top[0]: dokładnie zakres, w którym dziś rozstrzyga
    score. Hard-rejecty są już poza `feasible` (selekcja je usuwa przed top), więc tu nie ma
    wave_veto/NEG_INF. Defensywny per Lekcja #83 (try/except, fail-open, zero raise)."""
    if not top or not feasible:
        return
    try:
        _w = top[0]

        # B2 FIX (audyt 2026-06-28): stale inline _bucket -> wspolny _selection_bucket
        # (parytet z LIVE objm select, ktory uzywa bucket_fn=_selection_bucket; SHADOW
        # musi miec te sama szuflade by byl wiernym cieniem live-selekcji).
        _w_tb = (_late_pickup_tier(_w), _selection_bucket(_w))
        _grp = [c for c in feasible if (_late_pickup_tier(c), _selection_bucket(c)) == _w_tb]

        # L6.C1 (2026-07-04, dokończenie objm-lexr6-unify): kopia inline przepięta na
        # kanon. Zamrożenie pod at#152 wygasło — walidacja PASS (at-200 03.07 = GO,
        # SELECT LIVE), a POST_SHIFT_OVERRUN OFF ⇒ kanon zwraca 3-krotkę bajt-identyczną
        # z dawnym inline. Parytet cień↔kanon: test_objm_lexr6_unify (oba stany flagi).
        from dispatch_v2 import objm_lexr6 as _OLS
        _lex_qual = _OLS.lex_qual

        # E2↔D2 (2026-06-17, dyrektywa Adriana „brał pod uwagę też pln, nie w pierwszej
        # kolejności"): pln_v jako tie-breaker NAJNIŻSZEGO rzędu — jakość (R6→committed→
        # new-late) zostaje PRIMARY (peak quality). Kanon `_d2` = czysto jakościowy (NIE
        # kontaminuje walidacji at#152); mierzymy OSOBNO ile pln zmieniłby pick WŚRÓD
        # równych jakościowo i za ile zł. Z gwarancji leksykograficznej pln rusza pick
        # tylko przy remisie 3 pierwszych kluczy → pln_d_r6/pln_d_committed ~0 (sanity).
        def _pln_of(c):
            v = (getattr(c, "metrics", None) or {}).get("pln_v")
            return float(v) if isinstance(v, (int, float)) else None

        def _lex_pln(c):
            pv = _pln_of(c)
            return _lex_qual(c) + ((-pv) if pv is not None else float("inf"),)

        def _f(m, k):
            v = m.get(k)
            return float(v) if isinstance(v, (int, float)) else 0.0

        _d2 = min(_grp, key=_lex_qual) if _grp else _w
        _d2_pln = min(_grp, key=_lex_pln) if _grp else _w
        _wm = getattr(_w, "metrics", None)
        if not isinstance(_wm, dict):
            return
        _flip = str(getattr(_d2, "courier_id", "")) != str(getattr(_w, "courier_id", ""))
        _wm["objm_lexr6_best_cid"] = str(getattr(_d2, "courier_id", ""))
        _wm["objm_lexr6_flip"] = _flip
        _wm["objm_lexr6_group_size"] = len(_grp)
        # tie-breaker pln (obserwacja): kogo wybrałby pln WŚRÓD równych jakościowo
        _pln_cid = str(getattr(_d2_pln, "courier_id", ""))
        _wm["objm_lexr6_pln_cid"] = _pln_cid
        _wm["objm_lexr6_pln_coverage"] = sum(1 for c in _grp if _pln_of(c) is not None)
        _pln_changed = _pln_cid != str(getattr(_d2, "courier_id", ""))
        _wm["objm_lexr6_pln_changed"] = _pln_changed
        if _pln_changed:
            _pv_pln = _pln_of(_d2_pln)
            _pv_qual = _pln_of(_d2)
            if _pv_pln is not None and _pv_qual is not None:
                _wm["objm_lexr6_d_pln_v"] = round(_pv_pln - _pv_qual, 2)
            _dmp = getattr(_d2_pln, "metrics", None) or {}
            _dmq = getattr(_d2, "metrics", None) or {}
            _wm["objm_lexr6_pln_d_r6"] = round(_f(_dmp, "objm_r6_breach_max_min") - _f(_dmq, "objm_r6_breach_max_min"), 1)
            _wm["objm_lexr6_pln_d_committed"] = round(_f(_dmp, "late_pickup_committed_max") - _f(_dmq, "late_pickup_committed_max"), 1)
        if _flip:
            _dm = getattr(_d2, "metrics", None) or {}
            _wm["objm_lexr6_d_r6_breach"] = round(_f(_dm, "objm_r6_breach_max_min") - _f(_wm, "objm_r6_breach_max_min"), 1)
            _wm["objm_lexr6_d_committed"] = round(_f(_dm, "late_pickup_committed_max") - _f(_wm, "late_pickup_committed_max"), 1)
            _wm["objm_lexr6_d_new_late"] = round(_f(_dm, "new_pickup_late_min") - _f(_wm, "new_pickup_late_min"), 1)
            _wm["objm_lexr6_d_idle"] = round(_f(_dm, "v3273_wait_courier_max_min") - _f(_wm, "v3273_wait_courier_max_min"), 1)
            try:
                log.info(
                    f"OBJM_LEXR6_DIVERGENCE order={order_id} live={getattr(_w, 'courier_id', None)} "
                    f"d2={getattr(_d2, 'courier_id', None)} dR6={_wm['objm_lexr6_d_r6_breach']} "
                    f"dCom={_wm['objm_lexr6_d_committed']} dIdle={_wm['objm_lexr6_d_idle']}")
            except Exception:
                pass
    except Exception as _e:
        try:
            log.warning(f"OBJM_LEXR6_SHADOW failed order={order_id}: {_e!r}")
        except Exception:
            pass


# ── Warstwa B (#483000, 2026-06-24): carry-ślepota w SAMEJ BRAMCE check_feasibility_v2.
# SLA_PREEXISTING_BYPASS (feasibility_v2:1217) wybacza najgorszy realny breach gdy NIESIONY
# (sunk carry, dostarczany przed nowym odbiorem), a HARD-rejectuje mniejsze na NIEodebranych
# (blocking) → pula feasible może = GORSZY ocalały, a lepszy (carrying) kurier wycięty.
# objm_lexr6 tego NIE łapie (działa tylko na NIEpustej feasible, a bramka już wycięła lepszego).
# Pomiar 24.06: log-replay STRUKTURALNIE ślepy (feasible-path serializuje tylko survivorów MAYBE),
# ale odrzuceni-w-procesie ISTNIEJĄ (pool_total>pool_feasible w 155/155) → re-ranking BEZ re-runu.
# Ten SHADOW (OBSERWACYJNY, flaga ENABLE_FEAS_CARRY_BLIND_SHADOW default OFF) re-rankuje CHOSEN
# survivora przeciw PEŁNEJ puli `candidates` (z NO) używając lex_qual (carry-inclusive, kanon
# objm_lexr6) — faithful (zero sticky), ZERO mutacji decyzji/werdyktu. Dedyk. jsonl. Lekcja #83.
FEAS_CARRY_BLIND_SHADOW_LOG_PATH = "/root/.openclaw/workspace/dispatch_state/feas_carry_blind_shadow.jsonl"


def _emit_feas_carry_blind(event) -> None:
    """Append-only zapis dedyk. jsonl (rekord <4KB → atomowy O_APPEND, wzór _emit_r6_breach_shadow)."""
    if _EB.divert(_emit_feas_carry_blind, event):  # K08: efekt PO decyzji
        return
    try:
        with open(FEAS_CARRY_BLIND_SHADOW_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
    except Exception as e:  # noqa: BLE001
        try:
            log.warning(f"FEAS_CARRY_BLIND shadow write failed: {e!r}")
        except Exception:
            pass


def _feas_carry_blind_shadow(top, feasible, candidates, order_id, now=None) -> None:
    """SHADOW warstwy B — czy carry-ślepa bramka wycięła lepszego-na-prawdzie kandydata.
    OBSERWACYJNY: pisze feas_carry_blind_shadow.jsonl, NIGDY nie mutuje top/feasible/
    candidates/werdyktu. Fail-open (Lekcja #83). Odpala tylko gdy CHOSEN survivor niesie
    WYBACZONY breach (objm_r6_breach_max_min>0) = populacja ryzyka #483000; porównuje z
    odrzuconymi (NO) W PROCESIE używając kanonicznego lex_qual (carry-inclusive)."""
    try:
        if not top or not candidates:
            return
        from dispatch_v2 import objm_lexr6 as _OL
        import re as _re
        chosen = top[0]
        chosen_objm = _OL.objm(chosen, "objm_r6_breach_max_min")
        if chosen_objm is None or chosen_objm <= 0:
            return  # chosen czysty → brak asymetrii bypassu, poza zakresem warstwy B
        chosen_lex = _OL.lex_qual(chosen)

        _kind = _feas_carry_reject_kind

        def _overby(c):
            m = _re.search(r"over by ([0-9.]+)", getattr(c, "feasibility_reason", "") or "")
            return float(m.group(1)) if m else None

        rejected = [c for c in candidates
                    if getattr(c, "feasibility_verdict", None) == "NO"]
        # blocking SLA/R6 = ofiary asymetrii bramki (reszta = legit reject: shift_end/C2/dist)
        blocking = [c for c in rejected if _kind(c) in ("sla", "r6_new", "r6_carry_delta")]
        best_rej = min(blocking, key=_OL.lex_qual) if blocking else None
        rej_objm = _OL.objm(best_rej, "objm_r6_breach_max_min") if best_rej is not None else None
        would_redirect = bool(best_rej is not None and _OL.lex_qual(best_rej) < chosen_lex)
        ob = _overby(best_rej) if best_rej is not None else None

        event = {
            "ts": (now.isoformat() if hasattr(now, "isoformat") else None),
            "order_id": order_id,
            "pool_total": len(candidates),
            "pool_feasible": len(feasible),
            "chosen_cid": str(getattr(chosen, "courier_id", "")),
            "chosen_forgiven_breach": round(chosen_objm, 1),
            "would_redirect": would_redirect,
            "redirect_cid": (str(getattr(best_rej, "courier_id", "")) if best_rej is not None else None),
            "redirect_objm": (round(rej_objm, 1) if isinstance(rej_objm, (int, float)) else None),
            "redirect_kind": (_kind(best_rej) if best_rej is not None else None),
            "redirect_over_by": ob,
            "regret_min": (round(chosen_objm - rej_objm, 1)
                           if would_redirect and isinstance(rej_objm, (int, float)) else None),
            "marginal": bool(would_redirect and ob is not None and ob <= 5.0),
            "n_rejected": len(rejected),
            "n_blocking": len(blocking),
            "cands": [
                {"cid": str(getattr(c, "courier_id", "")),
                 "v": getattr(c, "feasibility_verdict", None),
                 "objm": (round(_OL.objm(c, "objm_r6_breach_max_min"), 1)
                          if isinstance(_OL.objm(c, "objm_r6_breach_max_min"), (int, float)) else None),
                 "r": (getattr(c, "feasibility_reason", "") or "")[:34]}
                for c in candidates[:14]
            ],
        }
        _emit_feas_carry_blind(event)
    except Exception as _e:  # noqa: BLE001
        try:
            log.warning(f"FEAS_CARRY_BLIND_SHADOW failed order={order_id}: {_e!r}")
        except Exception:
            pass


def _feas_carry_readmit_pick(top, feasible, candidates, new_oid, cap_min=40.0):
    """B2 LIVE (#483000, 2026-06-27): carry-aware re-admit na warstwie SELEKCJI feasible-path.
    Mirror _feas_carry_blind_shadow, ale ZWRACA kandydata do promocji (zamiast tylko log).

    Zwraca (cand, regret_min, orig_reason, newbag_min) gdy ISTNIEJE odrzucony (verdict NO,
    blocking sla/r6) który (a) jest lepszy carry-inclusive (lex_qual < chosen) ORAZ (b) jego
    NOWY order ≤ cap_min (40 = Tier-3 cap-stretch, ten sam guard co _best_effort_objm_pick).
    Inaczej None (caller zostaje na live zwycięzcy). Odpala TYLKO gdy chosen niesie WYBACZONY
    breach (objm_r6_breach_max_min>0) = populacja asymetrii #483000. Defensywny (fail-open)."""
    try:
        if not top or not candidates:
            return None
        from dispatch_v2 import objm_lexr6 as _OL
        chosen = top[0]
        chosen_objm = _OL.objm(chosen, "objm_r6_breach_max_min")
        if chosen_objm is None or chosen_objm <= 0:
            return None  # chosen czysty → brak asymetrii bypassu, poza zakresem B2
        chosen_lex = _OL.lex_qual(chosen)

        _kind = _feas_carry_reject_kind

        def _newbag(c):
            return _objm_newbag_min(c, new_oid)

        rejected = [c for c in candidates
                    if getattr(c, "feasibility_verdict", None) == "NO"]
        # blocking sla/r6 = ofiary asymetrii bramki (reszta = legit: shift_end/C2/dist/committed)
        blocking = [c for c in rejected if _kind(c) in ("sla", "r6_new", "r6_carry_delta")]
        # TWARDY guard Tier-3: nowy order ≤ cap_min (40). Brak danych newbag → odrzuć (bezpieczniej
        # nie re-dopuszczać niż wpuścić ślepo); chosen pozostaje. NIE fallback do pure carry-min
        # (to feasible-path, nie 0-feasible best_effort).
        capped = [c for c in blocking if (_newbag(c) is not None and _newbag(c) <= cap_min)]
        if not capped:
            return None
        best_rej = min(capped, key=_OL.lex_qual)
        if _OL.lex_qual(best_rej) >= chosen_lex:
            return None  # zwycięzca już nie gorszy carry-inclusive → bez zmiany
        rej_objm = _OL.objm(best_rej, "objm_r6_breach_max_min")
        regret = (round(chosen_objm - rej_objm, 1)
                  if isinstance(rej_objm, (int, float)) else None)
        return (best_rej, regret, (getattr(best_rej, "feasibility_reason", "") or "")[:60],
                round(_newbag(best_rej), 1))
    except Exception as _e:  # noqa: BLE001
        try:
            log.warning(f"FEAS_CARRY_READMIT pick failed new_oid={new_oid}: {_e!r}")
        except Exception:
            pass
        return None


def _objm_lexr6_d2_pick(feasible):
    """FAZA 2 (2026-06-18, flaga ENABLE_OBJM_LEXR6_SELECT): zwróć kandydata, którego
    R6-breach-primary lexicographic selektor D2 wskazuje W OBRĘBIE grupy (tier × bucket)
    zwycięzcy score (feasible[0]). Klucz: min(R6-breach → committed-late → new-pickup-late).
    Zwraca feasible[0] gdy brak lepszego / pusta grupa / brak metryk / błąd (fail-open).

    P1#5 (2026-06-19): lex-helpery + bucketowanie wydzielone do dzielonego modułu
    `dispatch_v2.objm_lexr6` (kanon). Cień `_objm_lexr6_shadow` POZOSTAJE z własnymi
    kopiami inline — ZAMROŻONY pod walidację at#152 (24.06, „walidacji NIE ruszać");
    po PASS at#152 → przepiąć też cień na ten moduł (dokończenie objm-lexr6-unify).
    Logika modułu jest bajt-identyczna z dawnym inline → zero zmiany zachowania D2."""
    if not feasible:
        return None
    try:
        from dispatch_v2 import objm_lexr6 as _olx
        return _olx.pick(
            feasible,
            late_pickup_tier=_late_pickup_tier,
            is_informed=_is_informed_cand,
            is_blind_empty=_is_blind_empty_cand,
            is_pre_shift=_is_pre_shift_cand,
            # 2026-06-24: spójny bucket z główną selekcją — equal-treatment dla no_gps/
            # pre_shift także w grupowaniu tier×bucket LEXR6 (przed flipem ENABLE_OBJM_LEXR6_SELECT).
            bucket_fn=_selection_bucket,
        )
    except Exception as _e:
        try:
            log.warning(f"OBJM_LEXR6_SELECT pick failed: {_e!r}")
        except Exception:
            pass
        return feasible[0] if feasible else None


def _a2_reliability_soft_score(feasible, order_id=None):
    """Dźwignia A2: kara score za niską niezawodność kuriera. Flag-gated, default OFF.
    Buckety pos/tier zachowuje późniejszy _demote_blind_empty + late-pickup tiering
    (semantyka 'nie-gorszy koszyk + score+delta' jak a2_selection_shadow). Re-sort desc."""
    if not C.decision_flag("ENABLE_A2_RELIABILITY_SOFT_SCORE") or not feasible:
        return feasible
    breach, conf, fm = _load_courier_reliability()
    if not breach or fm is None:
        return feasible
    coeff = float(getattr(C, "A2_RELIABILITY_COEFF", 60.0))
    min_gap = float(getattr(C, "A2_RELIABILITY_MIN_GAP", 0.05))
    for c in feasible:
        d = _a2_reliability_delta(getattr(c, "courier_id", None), breach, conf, fm, coeff, min_gap)
        if d:
            _v325_add_score_delta(c, d)
            m = getattr(c, "metrics", None)
            if isinstance(m, dict):
                m["a2_reliability_delta"] = round(d, 2)
    feasible.sort(key=_v325_score_rank_key)
    return feasible


def _gps_age_discount(feasible, order_id=None):
    """GPS-03/DATA-04 (2026-06-11): confidence-discount za wiek pozycji.

    pos_age_min (recent-fallback / store-rescue; None = żywy fix lub no_gps)
    dotąd nie kosztował nic w score — kandydat z repliką pozycji sprzed 20 min
    rywalizował jak świeży GPS. Dyskonto: -PER_MIN za minutę ponad FREE_MIN,
    cap CAP. Liczone ZAWSZE do bonus_gps_age_discount_shadow (lekcja #186);
    aplikacja + re-sort wyłącznie pod flagą ENABLE_GPS_AGE_DISCOUNT (kanon
    flags.json). Stałe nadpisywalne z flags.json (FLAGS_JSON_NUMERIC_OVERRIDES).
    Buckety pos/tier zachowuje późniejszy _demote_blind_empty (jak A2)."""
    if not feasible:
        return feasible
    _fl = C.load_flags()
    free_min = float(_fl.get("GPS_AGE_DISCOUNT_FREE_MIN", C.GPS_AGE_DISCOUNT_FREE_MIN))
    per_min = float(_fl.get("GPS_AGE_DISCOUNT_PER_MIN", C.GPS_AGE_DISCOUNT_PER_MIN))
    cap = float(_fl.get("GPS_AGE_DISCOUNT_CAP", C.GPS_AGE_DISCOUNT_CAP))
    apply_live = C.decision_flag("ENABLE_GPS_AGE_DISCOUNT")
    applied_any = False
    for c in feasible:
        m = getattr(c, "metrics", None)
        if not isinstance(m, dict):
            continue
        age = m.get("pos_age_min")
        delta = 0.0
        if isinstance(age, (int, float)) and age > free_min:
            delta = -min(cap, (float(age) - free_min) * per_min)
        m["bonus_gps_age_discount_shadow"] = round(delta, 2)
        m["bonus_gps_age_discount"] = 0.0
        if apply_live and delta:
            _v325_add_score_delta(c, delta)
            m["bonus_gps_age_discount"] = round(delta, 2)
            applied_any = True
    if applied_any:
        feasible.sort(key=_v325_score_rank_key)
    return feasible


def _v326_fleet_load_balance(feasible: list, candidates: list, order_id=None) -> list:
    """V3.26 STEP 4 (R-10 FLEET-LOAD-BALANCE).

    Compute fleet_bag_avg z metrics.bag_size_before across all candidates
    (feasible + infeasible — broader than just feasible dla representative
    fleet load picture). Apply per-candidate score adjustment:
    - delta = cand.bag_size - fleet_bag_avg
    - delta < -V326_FLEET_LOAD_THRESHOLD → bonus (underloaded, daj mu)
    - delta > +V326_FLEET_LOAD_THRESHOLD → penalty (overloaded, daj innym)
    - else → no adjustment

    Empty fleet (no bag data) → fallback no adjustment + WARNING log.
    Re-sorts feasible po score desc.
    """
    try:
        flag = bool(getattr(C, "ENABLE_V326_FLEET_LOAD_BALANCE", False))
    except Exception:
        flag = False
    if not flag or not feasible:
        return feasible
    threshold = float(getattr(C, "V326_FLEET_LOAD_THRESHOLD", 1.0))
    bonus = float(getattr(C, "V326_FLEET_LOAD_BONUS", 15.0))
    penalty = float(getattr(C, "V326_FLEET_LOAD_PENALTY", 15.0))
    bag_sizes = []
    for c in (candidates or feasible):
        m = getattr(c, "metrics", {}) or {}
        bs = m.get("bag_size_before")
        if isinstance(bs, (int, float)):
            bag_sizes.append(int(bs))
    if not bag_sizes:
        log.warning(
            f"V326_FLEET_LOAD order={order_id} brak bag_size data — fallback no adjustment"
        )
        return feasible
    fleet_bag_avg = sum(bag_sizes) / len(bag_sizes)
    for cand in feasible:
        m = getattr(cand, "metrics", {}) or {}
        cb = m.get("bag_size_before") or 0
        delta = cb - fleet_bag_avg
        if delta < -threshold:
            adj = bonus
        elif delta > threshold:
            adj = -penalty
        else:
            adj = 0.0
        if adj != 0.0:
            _v325_add_score_delta(cand, adj)
        m["v326_fleet_bag_avg"] = round(fleet_bag_avg, 2)
        m["v326_fleet_load_delta"] = round(delta, 2)
        m["v326_fleet_load_adjustment"] = round(adj, 2)
    feasible.sort(key=_v325_score_corridor_key)
    return feasible


def _v328_simple_heuristic_score(cid: str, cs: Any, order_event: dict) -> float:
    """V3.28 Fix 6 (incident 03.05.2026): simple proximity + tier scoring fallback.

    Used jako mass fail fallback gdy >=50% kurierów crash w _v327_pool
    (OR-Tools mass fail). NIE używa OR-Tools więc nie crashuje na out-of-domain
    time_windows. Pure heuristic, no constraints.

    Returns float score (higher = better):
    - Proximity: -dist_km * 10 (5km = -50 score)
    - Tier bonus: gold +5, std+ +2, std 0 (default)
    - No GPS / no pickup coords: -1000 penalty (fallback NIE wybierze takiego)

    Args:
        cid: Courier ID (str)
        cs: FleetCourier object (cs.pos = (lat,lon), cs.tier_bag = 'gold'|'std+'|'std')
        order_event: dict z 'pickup_coords' = (lat, lon)
    """
    try:
        # Fala #7 (2026-07-18): bez tworzenia sentinela (0,0) tylko po to, by go
        # zaraz odrzucić — parytet wyników: None/()/(0.0,y)/(None,y) → -1000 jak
        # dotąd ((None,y) szło przez except-haversine na ten sam -1000).
        pickup_coords = order_event.get("pickup_coords")
        if not pickup_coords or not pickup_coords[0]:
            return -1000.0
        courier_pos = getattr(cs, "pos", None)
        if not courier_pos or courier_pos[0] is None:
            return -1000.0  # no GPS penalty
        from dispatch_v2.osrm_client import haversine as _hav
        dist_km = float(_hav(tuple(pickup_coords), tuple(courier_pos)))
        proximity = -dist_km * 10.0
        tier = getattr(cs, "tier_bag", None) or "std"
        tier_bonus = {"gold": 5.0, "std+": 2.0, "std": 0.0}.get(str(tier), 0.0)
        return proximity + tier_bonus
    except Exception:
        return -1000.0


def _v328_heuristic_post_shift_skip(cs, order_event, now, fleet_speed_kmh):
    """Z-11 (audyt 2026-06-10): True gdy kuriera pominąć w heurystyce mass-fail,
    bo nie zdąży dojechać do restauracji przed końcem zmiany.

    Heurystyka omija CAŁĄ feasibility (V325 PICKUP_POST_SHIFT) — to minimalna
    bramka grafikowa: shift_end < now + naive_eta (haversine / fallback speed).
    Fail-open: brak shift_end / brak pozycji / pickup_coords zero / wyjątek →
    False (NIE skipuj — degraded mode, grafik mógł paść razem z OR-Tools).
    """
    try:
        shift_end = getattr(cs, "shift_end", None)
        if shift_end is None:
            return False
        pos = getattr(cs, "pos", None)
        pickup = (order_event or {}).get("pickup_coords")
        if not pos or not pos[0] or not pickup or not pickup[0]:
            return False
        from dispatch_v2.osrm_client import haversine as _hav
        dist_km = float(_hav(tuple(pickup), tuple(pos)))
        naive_eta_min = (dist_km / max(float(fleet_speed_kmh or 0.0), 1.0)) * 60.0
        if shift_end.tzinfo is None:
            shift_end = shift_end.replace(tzinfo=timezone.utc)
        return shift_end < now + timedelta(minutes=naive_eta_min)
    except Exception:
        return False


# DATA-DRIVEN SPEED (2026-06-14): mtime-cache loadera realnej prędkości per cid
# (tools/build_speed_tiers.py — solo-legi, OSRM). Do SHADOW re-pointu V326
# (owner-tier ≠ prędkość, test ρ−0.29). NIGDY nie mutuje stanu.
_SPEED_DATA_PATH = "/root/.openclaw/workspace/dispatch_state/courier_speed_data.json"
_speed_data_cache = {"mtime": None, "data": None, "ref_kmh": None}


def _load_speed_data():
    """Data-driven prędkość per cid (mtime-cache); None gdy brak/zły plik/brak ref.
    Zwraca cache {"data": {cid:{median_kmh,n_solo,...}}, "ref_kmh": std-median}."""
    try:
        mt = os.path.getmtime(_SPEED_DATA_PATH)
    except OSError:
        return None
    if _speed_data_cache["mtime"] != mt:
        try:
            with open(_SPEED_DATA_PATH, encoding="utf-8") as fh:
                d = json.load(fh)
            _speed_data_cache["data"] = d.get("couriers") if isinstance(d, dict) else None
            _speed_data_cache["ref_kmh"] = (d.get("_meta") or {}).get("std_tier_median_kmh")
            _speed_data_cache["mtime"] = mt
        except Exception:
            return None
    if not _speed_data_cache["data"] or not _speed_data_cache["ref_kmh"]:
        return None
    return _speed_data_cache


def _v326_speed_multiplier_adjust(feasible: list, order_id=None) -> list:
    """V3.26 STEP 2 (R-05 SPEED-MULTIPLIER).

    Apply tier-based speed adjustment do score:
      adjustment = (1.0 - multiplier) * SCORE_FACTOR
    Faster tier (multi<1.0) → positive boost, slower tier (multi>1.0) → penalty.

    Reads cs_tier_bag (z courier_tiers.json bag.tier) z metrics.
    Multiplier map per V326_SPEED_MULTIPLIER_MAP (backtest empirical).
    Unknown tier → fallback std (multi 1.0, no change) + WARNING log.

    NIE zmienia feasibility metrics (eta_pickup, drive_min). Tylko score.
    Re-sorts feasible po score desc na koniec.
    """
    try:
        flag = bool(getattr(C, "ENABLE_V326_SPEED_MULTIPLIER", False))
    except Exception:
        flag = False
    if not flag or not feasible:
        return feasible
    mult_map = getattr(C, "V326_SPEED_MULTIPLIER_MAP", {})
    factor = float(getattr(C, "V326_SPEED_SCORE_FACTOR", 50.0))
    for cand in feasible:
        m = getattr(cand, "metrics", {}) or {}
        tier = m.get("cs_tier_bag")
        if tier is None or tier not in mult_map:
            tier_used = "std"
            mult = 1.0
            if tier is not None:
                log.warning(
                    f"V326_SPEED_MULT order={order_id} cid={cand.courier_id} "
                    f"unknown tier={tier!r}, fallback std (multi=1.0)"
                )
        else:
            tier_used = tier
            mult = float(mult_map[tier])
        adjustment = (1.0 - mult) * factor
        _v325_add_score_delta(cand, adjustment)
        m["v326_speed_tier_used"] = tier_used
        m["v326_speed_multiplier"] = mult
        m["v326_speed_score_adjustment"] = round(adjustment, 2)
        # DATA-DRIVEN SPEED shadow (2026-06-14): co dałby mnożnik z REALNEJ
        # prędkości (solo-legi, n_solo≥5) zamiast owner-tieru; logujemy deltę —
        # NIE aplikujemy do score (telemetria pod replay; v326_ auto-serializuje).
        # Try/except: NIGDY nie wywróci hot-path (Lekcja #32).
        try:
            _sd = _load_speed_data()
            _ci = _sd["data"].get(str(cand.courier_id)) if _sd else None
            if _ci and (_ci.get("n_solo") or 0) >= 5 and _ci.get("median_kmh"):
                _dd_mult = min(1.25, max(0.85, float(_sd["ref_kmh"]) / float(_ci["median_kmh"])))
                _dd_adj = round((1.0 - _dd_mult) * factor, 2)
                m["v326_speed_dd_multiplier"] = round(_dd_mult, 3)
                m["v326_speed_dd_adjustment_shadow"] = _dd_adj
                m["v326_speed_dd_delta"] = round(_dd_adj - adjustment, 2)
        except Exception:
            pass
    # Re-sort feasible by score desc (tie-break corridor dev — pattern z _v325)
    feasible.sort(key=_v325_score_corridor_key)
    return feasible


def _v326_build_rationale(best: "Candidate", feasible: list) -> dict:
    """V3.26 STEP 1 (R-11 TRANSPARENCY-RATIONALE).

    Build decision rationale dla BEST candidate:
    - top_3_factors: top 3 by |contribution| z mapy known scoring components.
    - dominant_factor: name z najwyższą |contribution|.
    - advantage_vs_next: best.score - second-best.score.
    - close_call: True gdy advantage < V326_RATIONALE_CLOSE_CALL_THRESHOLD.
    - clear_winner: True gdy advantage > V326_RATIONALE_CLEAR_WIN_THRESHOLD.
    - dlaczego: PL natural-language string dla telegram render.

    Flag-gated; gdy off — zwraca None.
    """
    try:
        flag = bool(getattr(C, "ENABLE_V326_TRANSPARENCY_RATIONALE", False))
    except Exception:
        flag = False
    if not flag or not best:
        return None
    bm = (best.metrics or {}) if hasattr(best, "metrics") else {}
    # Factor map: (PL label, value, signed contribution)
    # Bonuses are positive contributions, penalties negative.
    # V3.26 Bug A complete (2026-04-25): bliskość rationale używa actual scoring
    # contribution loss vs ideal (km=0). Pre-fix `-km*5` heuristic mylił operatorów
    # — pokazywało "-79.5 pts" dla 15.91 km gdy real impact na ranking ~1.5 pts
    # (po W_DYSTANS=0.30 weight). Now: signed_contribution = (s_dystans(km) - 100) * W_DYSTANS.
    # km=0 → 0 (ideal, no penalty), km=15 → -28.5 (real cost vs ideal).
    import math as _math
    _km_for_rationale = float(bm.get("km_to_pickup") or 0)
    _decay = float(getattr(C, "_dummy_unused", 5.0))  # mirror scoring.DIST_DECAY_KM (NIE import bo cycle risk)
    try:
        from dispatch_v2.scoring import DIST_DECAY_KM as _decay, W_DYSTANS as _wd
    except Exception:
        _decay = 5.0
        _wd = 0.30
    _s_dystans = 100.0 * _math.exp(-_km_for_rationale / _decay) if _km_for_rationale > 0 else 100.0
    _bliskosc_contribution = (_s_dystans - 100.0) * _wd  # negative penalty vs ideal
    # V3.27.3: kara_wait_kuriera factor z custom value (wait_min + restaurant)
    # dla rich rendering w dlaczego ("kara_wait_kuriera -X (czeka Y min pod {rest})").
    _v3273_wait_value = None
    if bm.get("v3273_wait_courier_max_min") and float(bm.get("v3273_wait_courier_max_min") or 0) > 0:
        _v3273_wait_value = {
            "wait_min": float(bm.get("v3273_wait_courier_max_min") or 0),
            "restaurant": bm.get("v3273_wait_courier_max_restaurant") or "?",
        }
    factors = [
        ("bliskość", bm.get("km_to_pickup"), _bliskosc_contribution),  # actual scoring loss vs km=0
        ("fala", None, float(bm.get("bundle_bonus") or 0)),
        ("trajektoria", None, float(bm.get("v319h_bug2_continuation_bonus") or 0)),
        ("timing", None, float(bm.get("timing_gap_bonus") or 0)),
        ("post-wave", None, float(bm.get("wave_bonus") or 0) if "wave_bonus" in bm else 0),
        ("kara_R6", None, float(bm.get("bonus_r6_soft_pen") or 0)),
        ("kara_R8", None, float(bm.get("bonus_r8_soft_pen") or 0)),
        ("kara_R9_stop", None, float(bm.get("bonus_r9_stopover") or 0)),
        ("kara_R9_wait", None, float(bm.get("bonus_r9_wait_pen") or 0)),
        ("kara_wait_kuriera", _v3273_wait_value, float(bm.get("bonus_v3273_wait_courier") or 0)),
        ("kara_BUG4_cap", None, float(bm.get("bonus_bug4_cap_soft") or 0)),
        ("ext_kara", None, float(bm.get("v324a_extension_penalty") or 0)),
        ("V3.25_pre_shift", None, float(bm.get("v325_pre_shift_soft_penalty") or 0)),
        ("V3.25_new", None, float(bm.get("v325_new_courier_penalty") or 0)),
        ("D2_stale_grafik", None, float(bm.get("d2_soft_penalty") or 0)),
    ]
    # Filter out zero contributions, sort by |contribution| desc
    nonzero = [(label, value, contrib) for (label, value, contrib) in factors if abs(contrib) > 0.01]
    nonzero.sort(key=lambda t: -abs(t[2]))
    top_3 = nonzero[:3]
    # advantage vs next
    others = [c for c in feasible if c is not best]
    advantage = None
    next_name = None
    if others:
        next_best = max(others, key=lambda c: c.score)
        advantage = best.score - next_best.score
        next_name = next_best.name or f"K{next_best.courier_id}"
    # close call / clear winner flags
    close_call = (advantage is not None and abs(advantage) < C.V326_RATIONALE_CLOSE_CALL_THRESHOLD)
    clear_winner = (advantage is not None and advantage > C.V326_RATIONALE_CLEAR_WIN_THRESHOLD)
    # PL natural language string
    if top_3:
        parts = []
        for label, value, contrib in top_3:
            sign = "+" if contrib >= 0 else ""
            # V3.27.3: kara_wait_kuriera ma rich format "(czeka Y min pod {rest})"
            if label == "kara_wait_kuriera" and isinstance(value, dict):
                _w = value.get("wait_min", 0)
                _r = value.get("restaurant", "?")
                parts.append(f"{label} {sign}{contrib:.0f} (czeka {_w:.0f}min pod {_r})")
            else:
                parts.append(f"{label} {sign}{contrib:.0f}")
        dlaczego = ", ".join(parts)
    else:
        dlaczego = "brak wyróżniających czynników (default scoring)"
    if advantage is not None:
        sign = "+" if advantage >= 0 else ""
        dlaczego += f" · przewaga {sign}{advantage:.0f} vs {next_name}"
    if close_call:
        dlaczego += " ⚠ close call (2 kandydatów blisko siebie)"
    elif clear_winner:
        dlaczego += " · clear winner"
    return {
        "top_3_factors": [{"name": l, "value": v, "contribution": c} for l, v, c in top_3],
        "dominant_factor": top_3[0][0] if top_3 else None,
        "advantage_vs_next": round(advantage, 2) if advantage is not None else None,
        "next_best_name": next_name,
        "close_call": close_call,
        "clear_winner": clear_winner,
        "dlaczego": dlaczego,
    }


_NEW_COURIER_DELIV_CACHE = {"mtime": None, "data": {}}


def _new_courier_deliveries(cid) -> int:
    """SP-B2-RAMPA: licznik dostaw kuriera z courier_reliability.json (n_delivered).

    Cache wg mtime (ten sam plik co feed A2, osobny cache — inny kontrakt).
    Brak pliku / brak wpisu (min_history=5 wycina świeżych) / zły format → 0,
    czyli rampa AKTYWNA — konserwatywnie traktujemy nieznanego jako nowego.
    Plik regenerowany daily 04:30 — licznik rośnie raz dziennie (wystarcza:
    rampa to dziesiątki dostaw, nie minuty).
    """
    import os as _os3
    p = getattr(C, "A2_RELIABILITY_FEED_PATH", "")
    try:
        mt = _os3.path.getmtime(p)
    except OSError:
        return 0
    if _NEW_COURIER_DELIV_CACHE["mtime"] != mt:
        data = {}
        try:
            import json as _json2
            d = _json2.load(open(p, encoding="utf-8"))
            for k, v in (d.get("couriers") or {}).items():
                if isinstance(v, dict) and isinstance(v.get("n_delivered"), (int, float)):
                    data[str(k)] = int(v["n_delivered"])
        except Exception as _e:
            log.warning(f"SP-B2-RAMPA: courier_reliability load fail ({p}): {_e!r} — liczniki=0")
            data = {}
        _NEW_COURIER_DELIV_CACHE.update(mtime=mt, data=data)
    return int(_NEW_COURIER_DELIV_CACHE["data"].get(str(cid), 0))


def _v325_new_courier_penalty(feasible: list, order_id=None, now=None) -> list:
    """V3.25 STEP C (R-04 NEW-COURIER-CAP gradient) + SP-B2-RAMPA (2026-06-11).

    Post-scoring penalty layer dla kurierów z tier_label='new'. Mimicked po
    _demote_blind_empty pattern (V3.16) — read-modify candidate.score, re-sort.

    SP-B2-RAMPA (flaga ENABLE_NEW_COURIER_RAMP, hot-reload, default ON):
    przez pierwsze NEW_COURIER_RAMP_DELIVERIES (30) dostaw nowy kurier:
    - kurs "rampowy" (km_to_pickup ≤ 2,5 ∧ bag==0 ∧ slot ≠ high_risk 14-17)
      → stały malus NEW_COURIER_RAMP_MALUS (-20) zamiast gradientu — nowy
      STAJE SIĘ widzialny dla krótkich kursów (Z-18: człowiek tak robi, B6);
    - kurs poza profilem → jawny ``v325_score_blocked`` (sort na koniec,
      kandydat zostaje w puli — ALWAYS-PROPOSE; mining H13: dni 0-7 = 16,8%
      breach). Score pozostaje realną sumą komponentów, bez magicznej liczby.
    Po rampie (≥30 dostaw) lub flaga OFF → dotychczasowa logika niżej.

    Logic per candidate gdzie metrics.cs_tier_label == 'new' (post-rampa):
    - bag_size_before >= 2 → HARD SKIP (jawny blocked-state, sort to end)
    - else: compute advantage = candidate.score - max(non-new alt scores)
      - advantage >= 50 → penalty -10 (objectively significantly better)
      - advantage 20-50 → penalty -30
      - advantage < 20 → penalty -50 (default discount)

    Visual flag dodawany w metrics.v325_new_courier_flag dla telegram_approver
    LOCATION A + B render: "🆕 NOWY KURIER — advantage +X".
    """
    try:
        flag = bool(getattr(C, "ENABLE_V325_NEW_COURIER_CAP", False))
    except Exception:
        flag = False
    if not flag or not feasible:
        return feasible

    # Compute max non-new score (for advantage calc)
    non_new_scores = [
        c.score for c in feasible
        if (c.metrics.get("cs_tier_label") if hasattr(c, "metrics") and c.metrics else None) != "new"
    ]
    max_non_new = max(non_new_scores) if non_new_scores else None

    ramp_on = bool(C.flag("ENABLE_NEW_COURIER_RAMP", True))
    ramp_deliveries = int(getattr(C, "NEW_COURIER_RAMP_DELIVERIES", 30))
    ramp_max_km = float(getattr(C, "NEW_COURIER_RAMP_MAX_KM", 2.5))
    ramp_malus = float(getattr(C, "NEW_COURIER_RAMP_MALUS", -20.0))
    _ramp_blocked = []  # [(cand, pre_block_score)] — do solo-guard niżej

    for cand in feasible:
        m = getattr(cand, "metrics", {}) or {}
        if m.get("cs_tier_label") != "new":
            continue
        bag_before = m.get("bag_size_before", 0) or 0

        # ── SP-B2-RAMPA: pierwsze N dostaw = tylko kursy rampowe ──
        if ramp_on:
            _deliv = _new_courier_deliveries(cand.courier_id)
            if _deliv < ramp_deliveries:
                _km = m.get("km_to_pickup")
                _slot = calib_maps.time_slot_warsaw(now)
                _block = None
                if bag_before > 0:
                    _block = "bag_niepusty"
                elif _km is None or float(_km) > ramp_max_km:
                    _block = f"dystans_{_km if _km is not None else 'brak'}km"
                elif _slot == "high_risk":
                    _block = "slot_14_17"
                if _block is None:
                    _v325_clear_score_blocked(cand)
                    cand.score = cand.score + ramp_malus
                    m["v325_new_courier_penalty"] = ramp_malus
                    m["new_courier_ramp"] = {
                        "active": True, "eligible": True, "deliveries": _deliv,
                        "malus": ramp_malus, "km_to_pickup": _km, "slot": _slot,
                    }
                    m["v325_new_courier_flag"] = (
                        f"🆕 NOWY KURIER (rampa {_deliv}/{ramp_deliveries}) — "
                        f"krótki kurs {_km:.1f} km, pusta torba"
                    )
                    log.info(
                        f"SP-B2-RAMPA order={order_id} cid={cand.courier_id} ELIGIBLE "
                        f"deliv={_deliv} km={_km} slot={_slot} new_score={cand.score:.2f}"
                    )
                else:
                    _ramp_blocked.append((cand, float(cand.score)))
                    _v325_mark_score_blocked(cand)
                    # Powód hard-skipu i stan selekcyjny są jawne. penalty=None →
                    # reason breakdown `or 0` → 0 → odfiltrowany. Candidate.score
                    # pozostaje realną sumą komponentów i może być serializowany.
                    m["v325_new_courier_penalty"] = None
                    m["v325_skipped_reason"] = f"new_courier_ramp_off_profile:{_block}"
                    m["new_courier_ramp"] = {
                        "active": True, "eligible": False, "reason": _block,
                        "deliveries": _deliv, "km_to_pickup": _km, "slot": _slot,
                    }
                    m["v325_new_courier_flag"] = (
                        f"🆕 NOWY KURIER (rampa {_deliv}/{ramp_deliveries}) — "
                        f"kurs poza rampą ({_block})"
                    )
                    log.info(
                        f"SP-B2-RAMPA order={order_id} cid={cand.courier_id} BLOCK={_block} "
                        f"deliv={_deliv} km={_km} slot={_slot}"
                    )
                continue
            # post-rampa: licznik do telemetrii, dalej normalne reguły R-04
            m["new_courier_ramp"] = {"active": False, "deliveries": _deliv}

        if bag_before >= C.V325_NEW_COURIER_BAG_HARD_SKIP_AT:
            _v325_mark_score_blocked(cand)
            # Jak w ramp-block: jawny boolean niesie stan selekcyjny, a score
            # pozostaje liczbą domenową zamiast sentinela udającego komponent.
            m["v325_new_courier_penalty"] = None
            m["v325_skipped_reason"] = f"new_courier_bag_hard_skip:bag={bag_before}"
            m["v325_new_courier_flag"] = (
                f"🆕 NOWY KURIER — HARD SKIP (bag={bag_before} >= {C.V325_NEW_COURIER_BAG_HARD_SKIP_AT})"
            )
            log.info(
                f"V325_NEW_COURIER_HARD_SKIP order={order_id} cid={cand.courier_id} "
                f"bag={bag_before}"
            )
            continue
        if max_non_new is None:
            # Wszyscy są 'new' — fallback: standard discount, no advantage signal
            penalty = C.V325_NEW_COURIER_PENALTY_LOW_ADVANTAGE
            advantage = None
        else:
            advantage = cand.score - max_non_new
            if advantage >= C.V325_NEW_COURIER_HIGH_ADV_THRESHOLD:
                penalty = C.V325_NEW_COURIER_PENALTY_HIGH_ADVANTAGE
            elif advantage >= C.V325_NEW_COURIER_MED_ADV_THRESHOLD:
                penalty = C.V325_NEW_COURIER_PENALTY_MED_ADVANTAGE
            else:
                penalty = C.V325_NEW_COURIER_PENALTY_LOW_ADVANTAGE
        _v325_clear_score_blocked(cand)
        cand.score = cand.score + penalty
        m["v325_new_courier_penalty"] = penalty
        m["v325_new_courier_advantage"] = (
            round(advantage, 2) if advantage is not None else None
        )
        adv_str = f"advantage +{advantage:.1f}" if advantage is not None else "all-new"
        m["v325_new_courier_flag"] = f"🆕 NOWY KURIER — {adv_str}, penalty {penalty}"
        log.info(
            f"V325_NEW_COURIER order={order_id} cid={cand.courier_id} "
            f"adv={advantage} penalty={penalty} new_score={cand.score:.2f}"
        )

    # SP-B2-RAMPA SOLO-GUARD (replay 11.06, ALWAYS-PROPOSE): blokada nie może
    # wepchnąć decyzji w KOORD "wszyscy poniżej progu propozycji", gdy
    # zablokowany nowy był jedyną realną opcją (6-7 eskalacji/tydz. w replayu).
    # Gdy po blokadach ŻADEN feasible nie ma score >= MIN_PROPOSE_SCORE:
    # najlepszy zablokowany wraca na pre_block + SOLO_MALUS — mocno
    # zdemotowany, ale proposable; decyduje człowiek, nie cisza.
    if _ramp_blocked:
        _min_prop = _min_propose_score()  # SCALE-01: flags.json (hot) → common (=-100)
        _all_below = all(
            _v325_score_blocked(c)
            or (not isinstance(c.score, (int, float)))
            or c.score < _min_prop
            for c in feasible
        )
        if _all_below:
            _best_blocked, _pre = max(_ramp_blocked, key=lambda t: t[1])
            _solo = float(getattr(C, "NEW_COURIER_RAMP_SOLO_MALUS", -60.0))
            _best_blocked.score = _pre + _solo
            _v325_clear_score_blocked(_best_blocked)
            _bm = getattr(_best_blocked, "metrics", {}) or {}
            if isinstance(_bm.get("new_courier_ramp"), dict):
                _bm["new_courier_ramp"]["solo_rescue"] = True
                _bm["new_courier_ramp"]["malus"] = _solo
            _bm["v325_new_courier_penalty"] = _solo
            # Z-18: rescue → kandydat znów proposable, zdejmij etykietę skipu
            # (analityka nie powinna widzieć "skipped" na proponowanym kurierze).
            _bm.pop("v325_skipped_reason", None)
            _bm["v325_new_courier_flag"] = (
                (_bm.get("v325_new_courier_flag") or "")
                + " — jedyna opcja, proponuję mimo rampy"
            )
            log.info(
                f"SP-B2-RAMPA SOLO-RESCUE order={order_id} "
                f"cid={_best_blocked.courier_id} pre={_pre:.1f} "
                f"score={_best_blocked.score:.1f}"
            )

    # Re-sort: dopuszczeni przed blocked; wewnątrz klas dawny score/corridor.
    feasible.sort(key=_v325_score_corridor_key)
    return feasible


def _sync_spread_penalty(spread_min: float) -> float:
    """SP-B2-SYNCWORKA H1: kara gradientowa za spread gotowości worka.

    Węzły C.SYNC_SPREAD_KNOTS ((7,0),(10,-30),(15,-80),(20,-150)), liniowa
    interpolacja między nimi, płasko -150 powyżej ostatniego węzła.
    NIE hard reject — ALWAYS-PROPOSE (kandydat tylko traci w rankingu).
    """
    knots = getattr(C, "SYNC_SPREAD_KNOTS",
                    ((7.0, 0.0), (10.0, -30.0), (15.0, -80.0), (20.0, -150.0)))
    try:
        s = float(spread_min)
    except (TypeError, ValueError):
        return 0.0
    if s <= knots[0][0]:
        return 0.0
    for (x0, y0), (x1, y1) in zip(knots, knots[1:], strict=False):
        if s <= x1:
            return y0 + (y1 - y0) * (s - x0) / (x1 - x0)
    return float(knots[-1][1])


def _sync_effective_ready(ready_dt, restaurant, now):
    """effective_ready dla SYNCWORKI: deklaracja + prep-bias TYLKO gdy
    ENABLE_PREP_BIAS_TABLE flipnięty (🛑 ACK Adriana); inaczej sama deklaracja.
    Naive datetime traktowany jako UTC (konwencja pipeline'u). Fail-soft."""
    if ready_dt is None:
        return None
    if ready_dt.tzinfo is None:
        ready_dt = ready_dt.replace(tzinfo=timezone.utc)
    if C.decision_flag("ENABLE_PREP_BIAS_TABLE"):
        try:
            b = calib_maps.prep_bias_for(restaurant, now)
            if b is not None:
                return ready_dt + timedelta(minutes=float(b))
        except Exception:
            pass
    return ready_dt


def _compute_sync_spread(bag_sim, bag_raw, new_ready_at, new_restaurant, now):
    """SP-B2-SYNCWORKA H1 (2026-06-11): spread gotowości worka w minutach.

    spread = max−min po kotwicach czasowych: nowe zlecenie i bag-assigned =
    effective_ready (deklaracja + bias za flagą); bag picked_up = faktyczny
    picked_up_at (jedzenie już w torbie — liczy się od kiedy; fallback
    pickup_ready_at). Zwraca (spread_min | None, n_punktów). None gdy pusty
    bag albo <2 znanych czasów (solo / brak danych) — wtedy zero kary.

    Mining 2e: pick_spread ≤5 min → multi-rest bezpieczny jak same-rest
    (6,1% vs 6,5%); >10 min → worki niosące 50% wszystkich breachy.
    """
    if not bag_sim:
        return None, 0
    rest_by_oid = {}
    try:
        for b in (bag_raw or []):
            if isinstance(b, dict) and b.get("order_id") is not None:
                rest_by_oid[str(b.get("order_id"))] = b.get("restaurant")
    except Exception:
        pass
    times = []
    t_new = _sync_effective_ready(new_ready_at, new_restaurant, now)
    if t_new is not None:
        times.append(t_new)
    for bo in bag_sim:
        try:
            picked = (getattr(bo, "status", "assigned") == "picked_up"
                      or getattr(bo, "picked_up_at", None) is not None)
            if picked:
                anchor = getattr(bo, "picked_up_at", None) or getattr(bo, "pickup_ready_at", None)
                if anchor is not None and anchor.tzinfo is None:
                    anchor = anchor.replace(tzinfo=timezone.utc)
            else:
                anchor = _sync_effective_ready(
                    getattr(bo, "pickup_ready_at", None),
                    rest_by_oid.get(str(getattr(bo, "order_id", ""))),
                    now,
                )
            if anchor is not None:
                times.append(anchor)
        except Exception:
            continue
    if len(times) < 2:
        return None, len(times)
    spread = (max(times) - min(times)).total_seconds() / 60.0
    return round(spread, 1), len(times)


def _repo_cost_penalty(repo_km) -> float:
    """SP-B2-REPO: kara za dead-head repozycjonowania (≤0).

    -REPO_COST_MAX_PENALTY * min(1, km / REPO_KM_FULL_SCALE); km None/0 → 0.
    Waga rzędu komponentu dystansu (~30 pkt @ ≥4 km; mediana floty 3,56 km
    → ~-27), NIE 5-punktowy bonus (raport §3.1.4).
    """
    try:
        km = float(repo_km)
    except (TypeError, ValueError):
        return 0.0
    if km <= 0.0:
        return 0.0
    max_pen = float(getattr(C, "REPO_COST_MAX_PENALTY", 30.0))
    scale = float(getattr(C, "REPO_KM_FULL_SCALE", 4.0))
    if scale <= 0:
        return -max_pen
    return -max_pen * min(1.0, km / scale)


def _compute_repo_cost_km(bag_sim, plan, order_id, pickup_coords):
    """SP-B2-REPO (2026-06-11): km dead-headu do nowego odbioru wg PLANU kandydata.

    Szuka dropu poprzedzającego nowy pickup w planie: bag-zlecenia z
    predicted_delivered_at <= pickup_at[nowego]. Jest taki → km(haversine)
    od jego delivery_coords do pickup nowego (ukryta połowa kilometrów,
    raport §3.1.4). Nowy odbiór PRZED dropami (kurier jedzie od razu /
    po drodze) → None (km_to_pickup z bieżącej pozycji już to wycenia —
    zero podwójnego liczenia z BUG-2/road-to-rest: tamte są czasowe/correlate
    z bieżącą pozycją, ta kara dotyczy wyłącznie końcówki istniejącego worka).

    Zwraca (repo_km | None, last_drop_oid | None). Fail-soft.
    """
    if not bag_sim or plan is None or pickup_coords is None:
        return None, None
    try:
        pickup_at = plan.pickup_at or {}
        t_pick = pickup_at.get(order_id)
        if t_pick is None:
            return None, None
        if t_pick.tzinfo is None:
            t_pick = t_pick.replace(tzinfo=timezone.utc)
        delivered = plan.predicted_delivered_at or {}
        by_oid = {str(o.order_id): o for o in bag_sim}
        last_t = None
        last_oid = None
        for oid, t_drop in delivered.items():
            if str(oid) == str(order_id) or str(oid) not in by_oid:
                continue
            if t_drop is None:
                continue
            if t_drop.tzinfo is None:
                t_drop = t_drop.replace(tzinfo=timezone.utc)
            if t_drop <= t_pick and (last_t is None or t_drop > last_t):
                last_t = t_drop
                last_oid = str(oid)
        if last_oid is None:
            return None, None
        drop_coords = getattr(by_oid[last_oid], "delivery_coords", None)
        # L2.1: truthy-guard NIE łapał (0,0) → haversine raise połykany niżej
        # → repo_km=None → kandydat z zatrutym workiem wyglądał TAŃSZY (M-4).
        if not _coords_pass(bool(drop_coords), drop_coords, pickup_coords):
            return None, None
        return round(haversine(tuple(drop_coords), tuple(pickup_coords)), 2), last_oid
    except Exception as _rc_e:  # noqa: BLE001
        # L6.C3b fail-LOUD (2026-07-04, F_target_R2:42 „sentinel-swallow→0"): kara
        # repo-cost jest opcjonalna (None = brak kary, kandydat wygląda TAŃSZY), więc
        # cichy except systematycznie faworyzował zepsute worki. Zostaje fail-soft
        # (nie wywalamy oceny), ale KAŻDE połknięcie krzyczy — grep REPO_COST_SWALLOW.
        log.warning(f"REPO_COST_SWALLOW order={order_id}: "
                    f"{type(_rc_e).__name__}: {_rc_e}")
        return None, None


# ── SP-B2-LOADGOV (2026-06-11): load governor floty ──
# Stan procesowy: EWMA (tau 15 min) + uzbrojenie alertu trybu defensywnego.
# Shadow daemon = długo żyjący proces (EWMA ciągła); czasowka/plan-recheck =
# świeży proces per tick (EWMA startuje od próbki chwilowej — fail-soft OK,
# bo flaga decyzyjna i tak OFF, a telemetria chwilowa pozostaje poprawna).
_LOADGOV_STATE = {"ts": None, "ewma": None, "alert_armed": True}
_LOADGOV_ORDERS_CACHE = {"mtime": None, "count": None}
LOADGOV_ORDERS_STATE_PATH = "/root/.openclaw/workspace/dispatch_state/orders_state.json"
_LOADGOV_TERMINAL_STATUSES = frozenset(
    {"delivered", "cancelled", "not_picked", "nieodebrano", "anulowane"})

# Stan alertu „tryb defensywny" DZIELONY między procesami. assess_order biega w shadow
# (długo żyje) ORAZ w świeżych procesach per-tick: czasowka (CO MINUTĘ), plan-recheck,
# panel-quote subprocess. `alert_armed` w pamięci procesu nie wystarcza — świeży proces
# startuje armed=True i alarmuje od nowa → spam co minutę. Dzielimy hysteresis przez plik.
_LOADGOV_ALERT_STATE_PATH = "/root/.openclaw/workspace/dispatch_state/loadgov_alert_state.json"


def _loadgov_load_alert_state():
    """(armed, last_alert_ts) z pliku — domyślnie (True, None). Fail-soft."""
    try:
        with open(_LOADGOV_ALERT_STATE_PATH, encoding="utf-8") as fh:
            d = json.load(fh)
        ts = d.get("last_alert_ts")
        ts_dt = None
        if ts:
            try:
                ts_dt = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
                if ts_dt.tzinfo is None:
                    ts_dt = ts_dt.replace(tzinfo=timezone.utc)
            except (ValueError, TypeError):
                ts_dt = None
        return bool(d.get("armed", True)), ts_dt
    except Exception:  # brak pliku / zły JSON → uzbrojony, bez ostatniego alertu
        return True, None


def _loadgov_save_alert_state(armed, last_alert_ts):
    """Atomowy zapis stanu alertu (temp+fsync+rename). Nie może wywalić dispatchu."""
    import os as _oslg
    import tempfile as _tflg
    try:
        payload = {"armed": bool(armed),
                   "last_alert_ts": last_alert_ts.isoformat() if last_alert_ts else None}
        fd, tmp = _tflg.mkstemp(dir=_oslg.path.dirname(_LOADGOV_ALERT_STATE_PATH), suffix=".tmp")
        with _oslg.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, ensure_ascii=False)
            fh.flush()
            _oslg.fsync(fh.fileno())
        _oslg.replace(tmp, _LOADGOV_ALERT_STATE_PATH)
    except Exception:
        pass


def _loadgov_active_orders(now) -> Optional[int]:
    """Aktywne zlecenia z orders_state.json: status nie-terminalny + updated_at
    świeższe niż LOADGOV_ORDER_FRESH_H (guard na zalegające wpisy — wzorzec
    V3.14 stale-bag). mtime-cache; fail-soft → None."""
    import os as _os4
    try:
        mt = _os4.path.getmtime(LOADGOV_ORDERS_STATE_PATH)
    except OSError:
        return None
    if _LOADGOV_ORDERS_CACHE["mtime"] == mt:
        return _LOADGOV_ORDERS_CACHE["count"]
    count = None
    try:
        with open(LOADGOV_ORDERS_STATE_PATH, encoding="utf-8") as fh:
            data = json.load(fh)
        fresh_h = float(getattr(C, "LOADGOV_ORDER_FRESH_H", 3.0))
        cutoff = now - timedelta(hours=fresh_h)
        n = 0
        for v in (data or {}).values():
            if not isinstance(v, dict):
                continue
            if str(v.get("status") or "") in _LOADGOV_TERMINAL_STATUSES:
                continue
            ua = v.get("updated_at")
            if ua:
                try:
                    ua_dt = datetime.fromisoformat(str(ua).replace("Z", "+00:00"))
                    if ua_dt.tzinfo is None:
                        ua_dt = ua_dt.replace(tzinfo=timezone.utc)
                    if ua_dt < cutoff:
                        continue
                except (ValueError, TypeError):
                    pass  # brak/zły timestamp → licz (konserwatywnie aktywny)
            n += 1
        count = n
    except Exception as _e:
        log.warning(f"SP-B2-LOADGOV: orders_state load fail: {_e!r}")
        count = None
    _LOADGOV_ORDERS_CACHE.update(mtime=mt, count=count)
    return count


def _loadgov_compute(fleet_snapshot, now):
    """(load_now, load_ewma, active_orders, active_couriers) — fail-soft Nones.

    load = aktywne zlecenia / aktywni kurierzy (dispatchable fleet przekazany
    do assess_order). EWMA: alpha = 1 - exp(-dt/tau), pierwsza próbka = load.
    """
    couriers = len(fleet_snapshot or {})
    orders = _loadgov_active_orders(now)
    if orders is None or couriers <= 0:
        return None, _LOADGOV_STATE["ewma"], orders, couriers
    load_now = round(orders / couriers, 3)
    try:
        prev_ts = _LOADGOV_STATE["ts"]
        prev = _LOADGOV_STATE["ewma"]
        if prev is None or prev_ts is None:
            ewma = load_now
        else:
            dt_min = max(0.0, (now - prev_ts).total_seconds() / 60.0)
            tau = max(0.1, float(getattr(C, "LOADGOV_EWMA_TAU_MIN", 15.0)))
            alpha = 1.0 - math.exp(-dt_min / tau)
            ewma = round(alpha * load_now + (1.0 - alpha) * prev, 3)
        _LOADGOV_STATE["ts"] = now
        _LOADGOV_STATE["ewma"] = ewma
    except Exception:
        ewma = load_now
    return load_now, ewma, orders, couriers


def _loadgov_alert_transition(ewma, armed,
                              on_at=None, rearm_at=None):
    """Czysta maszynka hysteresis alertu (wzorzec _v328_should_emit_stuck_alert):
    (emit, new_armed). Uzbrojony + ewma>on → emit raz i rozbrój; rozbrojony +
    ewma<rearm → uzbrój ponownie (bez emisji)."""
    if on_at is None:
        on_at = float(getattr(C, "LOADGOV_DEFENSIVE_AT", 3.5))
    if rearm_at is None:
        rearm_at = float(getattr(C, "LOADGOV_REARM_AT", 3.0))
    if ewma is None:
        return False, armed
    if armed and ewma > on_at:
        return True, False
    if not armed and ewma < rearm_at:
        return False, True
    return False, armed


# INV-GATE-SCORE-DELTA registry (audyt 2026-06-24): JEDNO źródło prawdy — (flaga, klucz
# metrics) dla KAŻDEJ delty RANKINGOWEJ dopisywanej do final_score (dp ~5016-5035), którą
# bramka MIN_PROPOSE/KOORD MUSI wyłączyć. Strażnik `test_inv_gate_score_delta` pilnuje, że
# każda taka delta z final_score jest tu obecna (nie da się dodać rankingowej delty cicho
# wpływającej na werdykt). Klucz metrics = DOKŁADNIE wartość dodana do final_score (dp:5206-5228).
_GATE_RANKING_DELTA_EXCLUSIONS = (
    ("ENABLE_BUNDLE_SYNC_SPREAD", "bonus_sync_spread_shadow_delta"),       # -150, LIVE
    ("ENABLE_FLEET_LOAD_GOVERNOR", "bonus_loadgov_shadow_delta"),          # -40, LIVE
    ("ENABLE_R1_PROGRESSIVE_CLIP", "bonus_r1_progressive_shadow_delta"),   # -45..-100, LIVE (była luka)
    ("ENABLE_V319H_CONTINUATION_GUARD", "bonus_v319h_guard_shadow_delta"), # LIVE (była luka)
    ("ENABLE_REPO_COST_LIVE", "bonus_repo_cost_shadow_delta"),             # OFF (preemptive)
    ("ENABLE_BUNDLE_VALUE_SCORING", "bonus_bundle_fit_shadow_delta"),      # OFF (preemptive)
    ("ENABLE_FIX_C_ADDITIVE_PENALTY", "fix_c_additive_pen_shadow"),        # OFF (preemptive)
)


def _gate_score_excluding_ranking_deltas(cand):
    """INCYDENT-FIX 2026-06-12: score do bramki KOORD "wszyscy poniżej progu".

    (Literał nazwy reason celowo nieużyty w tym docstringu — test kolejności
    ścieżek KOORD szuka jego PIERWSZEGO wystąpienia w źródle.)
    Kary RANKINGOWE aplikowane flagami decyzyjnymi (SYNCWORKA -150 / LOADGOV
    -40) po flipie 11.06 wepchnęły 92 decyzje/30h w KOORD (rate 15,6%→50%) —
    próg MIN_PROPOSE_SCORE=-100 był kalibrowany na SUROWYCH score. Bramka
    ocenia score Z WYŁĄCZENIEM tych delt: kara poprawia ranking (kto wygrywa),
    NIGDY nie wpycha decyzji w ciszę (dyrektywa ALWAYS-PROPOSE). Serializowany
    score zostaje z deltami. None gdy score nie-liczbowy. Fail-soft.
    """
    sc = getattr(cand, "score", None)
    if not isinstance(sc, (int, float)):
        return None
    try:
        m = getattr(cand, "metrics", None) or {}
        # INV-GATE-SCORE-DELTA (audyt 2026-06-24, A2): bramka wyłącza WSZYSTKIE delty
        # rankingowe z `_GATE_RANKING_DELTA_EXCLUSIONS`, nie tylko SYNC/LOADGOV. Były LUKĄ:
        # r1_progressive(−45..−100) + v319h ŻYWE dopisywane do final_score, ale gate ich NIE
        # wyłączał → kara rankingowa mogła zbić gate-score <MIN_PROPOSE = best_effort-low_score
        # (z ALWAYS-PROPOSE = etykieta, nie KOORD). repo/bundle_fit/fix_c OFF (no-op, preemptive).
        for _flag, _key in _GATE_RANKING_DELTA_EXCLUSIONS:
            if C.decision_flag(_flag):
                sc = sc - float(m.get(_key) or 0.0)
    except Exception:
        pass
    return sc


def _soon_free_probe(cid, bag_raw, now):
    """SP-B2-ZARAZWOLNY (2026-06-11, B2): czy busy kurier kończy ≤12 min.

    61% busy-picków człowieka = kurier kończący ≤12 min — Ziomek karze
    zajętych nie modelując zwolnienia. Probe czyta ZAPISANY plan kuriera
    (plan_manager, walidacja active_bag_oids jak V3.19d) i zwraca:
      {eligible, free_at_min, free_at_iso, last_drop_coords (lat, lng)}
    None gdy pusty bag / brak planu / plan mismatch / błąd (fail-soft).
    free_at_min clampowane ≥0 (plan przeterminowany = „wolny zaraz").
    """
    if not bag_raw:
        return None
    try:
        from dispatch_v2 import plan_manager as _pm_sf
        _bag_oids = {str(b.get("order_id")) for b in bag_raw if b.get("order_id")}
        if not _bag_oids:
            return None
        saved = _pm_sf.load_plan(
            str(cid), active_bag_oids=_bag_oids,
            invalidate_on_mismatch=not C.flag("ENABLE_LOAD_PLAN_PURE_READ"))
        if saved is None:
            return None
        drops = [
            s for s in (saved.get("stops") or [])
            if s.get("type") == "dropoff" and s.get("predicted_at")
            and isinstance(s.get("coords"), dict)
        ]
        if not drops:
            return None
        last = max(drops, key=lambda s: s["predicted_at"])
        free_at = datetime.fromisoformat(str(last["predicted_at"]).replace("Z", "+00:00"))
        if free_at.tzinfo is None:
            free_at = free_at.replace(tzinfo=timezone.utc)
        free_at_min = max(0.0, (free_at - now).total_seconds() / 60.0)
        coords = (float(last["coords"]["lat"]), float(last["coords"]["lng"]))
        # L2.1 (K5b): plan bywa persystowany z placeholderem (0,0)
        # (_save_plan_on_assign legacy) — DETONOWAŁ w serializerze
        # (soon_free_last_drop_km haversine → ValueError → V328 eject
        # CAŁEGO kuriera; 28 ofiar 01.07). Zatruty last_drop → probe=None
        # (fail-soft, kurier ewaluowany normalnie z bieżącej pozycji).
        if C.decision_flag("ENABLE_COORD_SENTINEL_INGEST_GUARD") \
                and not C.coords_in_bialystok_bbox(coords):
            log.warning(
                f"COORD_INGEST_GUARD soon_free cid={cid}: last_drop_coords="
                f"{coords!r} zatrute (plan placeholder?) — probe pominięty"
            )
            return None
        max_min = float(getattr(C, "SOON_FREE_MAX_MIN", 12.0))
        return {
            "eligible": free_at_min <= max_min,
            "free_at_min": round(free_at_min, 1),
            "free_at_iso": free_at.isoformat(),
            "last_drop_coords": coords,
        }
    except Exception:
        return None


def _no_gps_equal_on() -> bool:
    """Adrian 2026-06-22: kurier bez GPS traktowany NA RÓWNI z GPS — żadnych kar/
    demote. no_gps konkuruje czystym score (ma już neutralne km=śr.floty + ETA=
    max(15,prep) z F1.7). flags.json hot → common (default False)."""
    try:
        return bool(C.flag("ENABLE_NO_GPS_EQUAL_TREATMENT",
                           getattr(C, "ENABLE_NO_GPS_EQUAL_TREATMENT", False)))
    except Exception:
        return False


def _equal_bucket_on() -> bool:
    """Adrian 2026-06-24: DOKOŃCZENIE równego traktowania — no_gps I pre_shift konkurują
    PO SCORE także w bucketach selekcji (tiering + best_effort) i nie są demotowane.
    Model: kurier bez GPS / przed zmianą, w grafiku, dojazd 15 min; filtrem jest off-switch
    w konsoli koordynatora, NIE demote. Pomiar przed flipem: 359 flipów/tydz (184 no_gps +
    175 pre_shift), 282 czyste, wierność 92% (tools/nogps_preshift_bucket_replay.py).
    'none' (poza grafikiem) zostaje demotowane. flags.json hot → common (default False)."""
    try:
        return bool(C.flag("ENABLE_EQUAL_TREATMENT_BUCKET",
                           getattr(C, "ENABLE_EQUAL_TREATMENT_BUCKET", False)))
    except Exception:
        return False


def _apply_pre_shift_equal_gate(bonus_pre_shift_soft, metrics):
    """Sprint 1 NO-GPS-EQUAL (Adrian 2026-06-29 „bez kary przed zmianą"): gdy flaga
    `ENABLE_PRE_SHIFT_EQUAL_NO_PENALTY` ON → zdejmuje LEKKĄ karę pre_shift (NEAR ∝m
    `PRE_SHIFT_NEAR_PEN_PER_MIN`·m, ≤~−30; LUB stała feasibility `V325_PRE_SHIFT_SOFT_PENALTY`
    −20 gdy gradient OFF). Pojedynczy autorytatywny punkt PO obu źródłach kary.

    ⚠ ZACHOWUJE FAR-veto (`PRE_SHIFT_FAR_PEN` ≈ −1000) — kurier z odległym startem zmiany
    (NEAR<m≤cap) NIE bierze now-ordera POZA przeładowaniem floty (loadgov≥unlock → gradient
    sam relaksuje do ∝m, wtedy lekka i też zdjęta). To JEST reguła Adriana „chyba że trzeba
    przedłużyć w odpowiedzi do restauracji" = load-aware, NIE ruszamy. Zdjęcie FAR-veta
    posłałoby klienta na 40-60 min czekania → harm (replay 29.06 to wykrył).

    „Kurier dotrze później" (NEAR) obsługuje LEGALNA ścieżka (clamp do shift_start +
    R-LATE-PICKUP propozycja przedłużenia DO RESTAURACJI), NIE ukryta kara w score.
    HARD-reject >30min-przed-zmianą (feasibility_v2) zostaje. Default OFF = czysty no-op.
    Czysta (testowalna); `v325_` prefix → metryka auto-serializowana."""
    try:
        on = bool(C.decision_flag("ENABLE_PRE_SHIFT_EQUAL_NO_PENALTY"))
    except Exception:
        on = False
    if not on:
        return bonus_pre_shift_soft
    pen = float(bonus_pre_shift_soft or 0.0)
    if pen >= 0.0:
        return bonus_pre_shift_soft
    # FAR-veto (load-aware ~veto) ZOSTAJE; zdejmujemy tylko lekką karę NEAR/stałą.
    try:
        _far = float(C.PRE_SHIFT_FAR_PEN)
    except Exception:
        _far = -1000.0
    if pen <= _far + 0.5:
        metrics["v325_pre_shift_far_veto_kept"] = round(pen, 2)
        return bonus_pre_shift_soft
    metrics["v325_pre_shift_penalty_suppressed"] = round(pen, 2)
    metrics["v325_pre_shift_soft_penalty"] = 0.0
    return 0.0


def _selection_bucket(c) -> int:
    """V3.16 bucket selekcji: informed 0 / other 1 / blind(+pre_shift) 2. RÓWNE
    TRAKTOWANIE (Adrian 2026-06-24, `_equal_bucket_on`): no_gps I pre_shift NIE są karane
    bucketem → 0 (konkurują po score). Wspólny dla `_late_pickup_score_first_key` +
    `_best_effort_sort_key` (jedno źródło prawdy). 'none' zawsze 2."""
    if _is_informed_cand(c):
        return 0
    ps = c.metrics.get("pos_source") if (hasattr(c, "metrics") and c.metrics) else None
    if _equal_bucket_on() and ps in ("no_gps", "pre_shift"):
        return 0
    if _is_blind_empty_cand(c) or _is_pre_shift_cand(c):
        return 2
    return 1


def _is_demotable_blind_empty(c) -> bool:
    """blind+empty kandydat KWALIFIKUJĄCY SIĘ do demote. Równe traktowanie: no_gps wyłączony
    (`_no_gps_equal_on`, 22.06); pre_shift wyłączony (`_equal_bucket_on`, 24.06 — decyzja
    Adriana 'pre_shift też'). 'none' zostaje (poza grafikiem)."""
    if not _is_blind_empty_cand(c):
        return False
    ps = c.metrics.get("pos_source") if (hasattr(c, "metrics") and c.metrics) else None
    if _no_gps_equal_on() and ps == "no_gps":
        return False
    if _equal_bucket_on() and ps == "pre_shift":
        return False
    return True


def _assert_feasibility_first(feasible: list, order_id=None) -> None:
    """INV-FEASIBILITY-FIRST (audyt 2026-06-24, spec odporności §6.A). Gwarancja P0:
    żaden kandydat z `feasibility_verdict=='NO'` NIE może być w puli selekcji — HARD bramki
    feasibility egzekwowane PRZED warstwą scoring/bonus, żaden SOFT nie obejdzie HARD.
    Filtr (`feasible=[c if MAYBE]`) zapewnia to z konstrukcji; ten strażnik łapie REGRESJĘ,
    gdyby przyszła zmiana wpuściła NO do puli albo zmutowała verdict po odsiewie.
    FAIL-LOUD (log.error + licznik metryki), NIGDY nie crashuje (fail-soft → nie psuje
    pętli decyzyjnej). Read-only, jeden przebieg po małej liście feasible."""
    try:
        bad = [str(getattr(c, "courier_id", "?")) for c in feasible
               if getattr(c, "feasibility_verdict", None) == "NO"]
        if bad:
            log.error(
                f"INV_FEASIBILITY_FIRST_VIOLATION order={order_id} "
                f"NO-verdict w puli selekcji: {bad} — SOFT mógł obejść HARD bramkę"
            )
            for c in feasible:
                if getattr(c, "feasibility_verdict", None) == "NO" and isinstance(
                        getattr(c, "metrics", None), dict):
                    c.metrics["inv_feasibility_first_violation"] = True
    except Exception:
        pass


def _set_feasibility_verdict(cand, verdict, *, layer, order_id=None) -> None:
    """L7.3 (INV-LAYER-2 „NO-VERDICT-OUTSIDE-L5"): JEDEN setter atrybutu
    `feasibility_verdict`. Werdykt feasibility należy USTAWIAĆ WYŁĄCZNIE w warstwie 5
    (check_feasibility_v2 / feasibility loop). Zapis w innej warstwie (np. L7 selekcja —
    FEAS_CARRY_READMIT) = naruszenie kontraktu warstw.

    Setter ZAWSZE wykonuje zapis (zachowanie NIEZMIENIONE — bajt-parytet). Garda jest
    OBSERWACYJNA: gdy ENABLE_SPLIT_LAYER_GUARD ON i layer != 'L5' → WARNING + wpis do
    split_layer_guard.jsonl. Fail-soft (nie wywraca pętli decyzyjnej).

    MAPA zapisów `feasibility_verdict` (2026-07-03, cały repo):
      • L5 (dozwolone): dispatch_pipeline._v327_eval_courier_inner Candidate(verdict=…) [konstruktor],
        _v328_eval_safe Candidate(verdict="MAYBE") [konstruktor], solo Candidate(verdict=sv)
        [konstruktor], legacy F1.8e pre_shift `c.feasibility_verdict="NO"` (ten setter, layer=L5;
        od v3 NOGPS-NEUTRAL w `_pre_shift_too_late_verdict_pass` — hoist PRZED
        `_nogps_neutral_score_pass`, żeby donor filter mediany widział finalne werdykty).
      • L7 (naruszenie, kanalizowane tu): FEAS_CARRY_READMIT `_fcr_cand→"MAYBE"` (layer=L7_selekcja).
      • Bliźniak best_effort↔objm_lexr6: NIE zapisuje verdiktu (tylko REORDER) — brak setterów.
    """
    try:
        cand.feasibility_verdict = verdict
    except Exception:
        return
    if layer == "L5" or not _split_layer_guard_on():
        return
    try:
        _cid = getattr(cand, "courier_id", None)
        log.warning(
            f"SPLIT_LAYER_VERDICT_WRITE order={order_id} cid={_cid} "
            f"verdict={verdict} layer={layer} — feasibility_verdict zapisany POZA L5 "
            f"(INV-LAYER-2)")
        _append_split_layer_guard_log({
            "ts": datetime.now(timezone.utc).isoformat(),
            "kind": "verdict_write_outside_l5",
            "order_id": order_id,
            "courier_id": _cid,
            "verdict": verdict,
            "layer": layer,
        })
    except Exception:
        pass


def _split_layer_emit_assert(result, order_id=None) -> None:
    """L7.3 (INV-LAYER-1 „HARD-BEFORE-SOFT, pełny"): re-assert `_assert_feasibility_first`
    na KAŻDYM EMIT (nie tylko 1× po demote). Wołane ze wspólnego lejka pre-emit
    `_classify_and_set_auto_route` (11 call-site'ów assess_order) — więc biegnie przy każdym
    zwrocie PipelineResult. Powód: pula `feasible` bywa MUTOWANA po pierwszym asercie
    (FEAS_CARRY_READMIT wstawia readmitowanego na czoło), a naruszenie warstwy materializuje
    się przy EMIT.

    OBSERWACYJNY: OFF ⇒ natychmiastowy return (bajt-parytet). ON ⇒ tylko log/jsonl,
    ZERO zmiany decyzji. best_effort/solo (0 feasible, verdict NO z KONTRAKTU R28) są
    WYŁĄCZONE bramką `pool_feasible_count > 0` — inaczej fałszywy alarm.
    """
    if not _split_layer_guard_on():
        return
    try:
        pfc = getattr(result, "pool_feasible_count", 0) or 0
        if pfc <= 0:
            return  # best_effort/solo/no_solo: brak puli feasible → kontrakt R28, wyłączone
        pool = getattr(result, "candidates", None) or []
        # Re-assert HARD-before-SOFT na emitowanej puli selekcji (top). _assert_… jest
        # fail-loud (log.error + metryka na naruszającym), fail-soft. Serializowana pula
        # = to co konsumuje downstream; po FEAS_CARRY_READMIT readmitowany jest na czole
        # top z verdict=MAYBE (promowany), więc czysto = brak alarmu.
        _bad = [str(getattr(c, "courier_id", "?")) for c in pool
                if getattr(c, "feasibility_verdict", None) == "NO"]
        _best = getattr(result, "best", None)
        _best_no = (getattr(_best, "feasibility_verdict", None) == "NO"
                    if _best is not None else False)
        if _bad or _best_no:
            _assert_feasibility_first(pool, order_id)
            log.warning(
                f"SPLIT_LAYER_EMIT_VIOLATION order={order_id} verdict={getattr(result,'verdict',None)} "
                f"best_cid={getattr(_best,'courier_id',None)} best_no={_best_no} "
                f"no_in_pool={_bad} pool_feasible={pfc} — NO-verdict w emitowanej puli feasible-path")
            _append_split_layer_guard_log({
                "ts": datetime.now(timezone.utc).isoformat(),
                "kind": "no_verdict_in_emit_pool",
                "order_id": order_id,
                "verdict": getattr(result, "verdict", None),
                "best_cid": getattr(_best, "courier_id", None),
                "best_verdict_no": _best_no,
                "no_in_pool_cids": _bad,
                "pool_feasible_count": pfc,
            })
    except Exception:
        pass


def _demote_blind_empty(feasible: list, order_id=None) -> list:
    """V3.16 demotion: jeśli top-1 jest blind+empty AND istnieje informed alt,
    reorder — informed first (stable), other middle, blind+empty last.
    Guard "all blind": jeśli żadnego informed → zostaw bez zmian.
    NO_GPS RÓWNE TRAKTOWANIE (2026-06-22): gdy ENABLE_NO_GPS_EQUAL_TREATMENT ON,
    no_gps jest wyłączony z demote (_is_demotable_blind_empty) → konkuruje jak GPS.
    """
    try:
        flag = bool(getattr(C, "ENABLE_NO_GPS_EMPTY_DEMOTE", True))
    except Exception:
        flag = True
    if not flag or not feasible:
        return feasible
    if not _is_demotable_blind_empty(feasible[0]):
        return feasible
    informed = [c for c in feasible if _is_informed_cand(c)]
    if not informed:
        return feasible  # all blind — nie degraduj (empty shift edge)
    original_top_cid = feasible[0].courier_id
    other = [c for c in feasible
             if not _is_informed_cand(c) and not _is_demotable_blind_empty(c)]
    blind_empty = [c for c in feasible if _is_demotable_blind_empty(c)]
    reordered = informed + other + blind_empty
    log.info(
        f"NO_GPS_DEMOTE order={order_id}: top cid={original_top_cid} "
        f"(no_gps+empty) demoted; informed_alts={len(informed)}; "
        f"new_top_cid={reordered[0].courier_id}"
    )
    return reordered


def _reserve_aware_tiebreak_eval(winner, feasible, wtier, lp_tier_fn, margin):
    """#3 top10 (2026-06-29): log-only ewaluacja reserve-aware tie-break. Czy zwycięzca to
    WOLNY kurier (bag 0), a w TYM SAMYM tierze late-pickup jest FEASIBLE kandydat JUŻ W
    TRASIE (bag≥1) w marginesie score (silnik ~obojętny) → tie-break dołożyłby do jadącego
    (oszczędza rezerwę). PURE, ZERO mutacji feasible/winner. Zwraca dict (would_fire+detal).
    same-tier = brak inwersji committed-odbioru; wyklucz jawnie zablokowanych
    przez V325 + R6>40 (bundle nie może psuć świeżości). Margin =
    RESERVE_TIEBREAK_MARGIN."""
    def _bag_before(c):
        m = c.metrics or {}
        b = m.get("bag_size_before")
        return (b if b is not None else m.get("r6_bag_size")) or 0
    if _bag_before(winner) != 0:
        return {"would_fire": False, "winner_free": False}
    ws = winner.score or 0.0
    carriers = []
    for c in feasible:
        if c is winner or lp_tier_fn(c) != wtier:
            continue  # tylko ten sam tier late-pickup (brak inwersji committed-odbioru)
        if _bag_before(c) < 1:
            continue  # musi już wieźć (jadący)
        cs = c.score
        if cs is None or _v325_score_blocked(c):
            continue  # jawny V325 hard-skip; score nie koduje już stanu
        m = c.metrics or {}
        mb = m.get("max_bag_time_min")
        mb = mb if mb is not None else m.get("r6_max_bag_time_min")
        if mb is not None and mb > 40.0:
            continue  # R6 tier-aware cap — bundle nie może psuć świeżości
        if (ws - cs) <= margin:
            carriers.append((c, cs, _bag_before(c), mb))
    if not carriers:
        return {"would_fire": False, "winner_free": True}
    carriers.sort(key=lambda t: -t[1])
    bc, bcs, bbag, bmb = carriers[0]
    return {
        "would_fire": True,
        "winner_free": True,
        "winner_cid": str(getattr(winner, "courier_id", "")),
        "carry_cid": str(getattr(bc, "courier_id", "")),
        "carry_bag_before": bbag,
        "carry_r6_max_bag_time_min": bmb,
        "dscore_free_minus_carry": round(ws - bcs, 1),
        "same_late_pickup_tier": wtier,
        "n_carrier_candidates": len(carriers),
    }


# Czysta geometria trasy (point→segment, min→route) → pipeline_geometry.py
# (B6 2026-06-20, zaimportowane wyżej). Wywołanie w _assess_order_impl nietknięte,
# zachowanie identyczne (test_pipeline_geometry + pełna suita = bramka).


# SCALE-01: kanon = common.EARLY_BIRD_THRESHOLD_MIN (env-default 60). Stała tu
# zostaje jako backward-compat re-export (shadow_dispatcher importuje ją),
# ale runtime threshold czytany przez _early_bird_threshold_min() (flags.json hot).
EARLY_BIRD_THRESHOLD_MIN = int(getattr(C, "EARLY_BIRD_THRESHOLD_MIN", 60))
# Sprint-1 2026-04-30 (logging extension): bumped 5→16 to capture full feasible
# pool dla counterfactual analysis (PANEL_OVERRIDE pairwise). Faza 2 baseline
# pool mean=10.24, max=17 — top-15 alternatives + best=16 covers ~100% pool.
TOP_N_CANDIDATES = 16
DEFAULT_FLEET_PREP_VARIANCE_MIN = 13.0


def _early_bird_threshold_min() -> float:
    """SCALE-01: early-bird KOORD threshold — flags.json (hot) → common (=60 min)."""
    return float(C.load_flags().get("EARLY_BIRD_THRESHOLD_MIN", C.EARLY_BIRD_THRESHOLD_MIN))


# EARLYBIRD-01 (2026-06-14): forward-shadow domykający lukę „deferowalności".
# Problem: early_bird KOORD zwiera obwód PRZED budową puli feasibility → nie wiemy
# czy w T-30 zlecenie byłoby rozwiązywalne (kandydat istnieje) czy to realna eskalacja.
# Shadow: gdy early_bird odpala, re-uruchom assess_order z _bypass_early_bird=True
# (kontrfaktyk „co gdyby przepuścić do feasibility teraz") i zaloguj wynik. LOG-ONLY —
# live verdict POZOSTAJE KOORD. Flaga OFF default. Pomiar/decyzja: VERDICT_c_redux_measurement_2026-06-14.
EARLYBIRD_T30_SHADOW_LOG_PATH = "/root/.openclaw/workspace/dispatch_state/earlybird_shadow.jsonl"


def _earlybird_t30_shadow_enabled() -> bool:
    """EARLYBIRD-01: czy zbierać forward-shadow kontrfaktyk early_bird (flags.json hot, OFF default)."""
    return bool(C.load_flags().get("ENABLE_EARLYBIRD_T30_SHADOW", False))


def _append_earlybird_t30_shadow(entry: dict) -> None:
    """EARLYBIRD-01 forward-shadow append (atomic 'a', fail-soft — wzór _append_difficult_case_log)."""
    if _EB.divert(_append_earlybird_t30_shadow, entry):  # K08: efekt PO decyzji
        return
    try:
        import json as _json
        import os as _os
        path = getattr(C, "EARLYBIRD_T30_SHADOW_LOG_PATH", EARLYBIRD_T30_SHADOW_LOG_PATH)
        _os.makedirs(_os.path.dirname(path), exist_ok=True)
        with open(path, "a") as f:
            f.write(_json.dumps(entry, default=str, ensure_ascii=False) + "\n")
    except Exception as _e:
        try:
            log.warning(f"_append_earlybird_t30_shadow failed: {_e}")
        except Exception:
            pass


def _min_propose_score() -> float:
    """SCALE-01: PROPOSE-quality floor — flags.json (hot) → common (=-100.0)."""
    return float(C.load_flags().get("MIN_PROPOSE_SCORE", C.MIN_PROPOSE_SCORE))


def _always_propose_on() -> bool:
    """ALWAYS-PROPOSE ON SATURATION (Adrian 2026-06-15): gdy ON, bramki ciszy
    (best_effort r6_breach/low_score, all_candidates_low_score) NIE zwracają KOORD —
    przepadają do istniejącego PROPOSE (best_effort=True → banner ⚠️). flags.json hot
    → konstanta common (default False). early_bird i pusta pula ZOSTAJĄ KOORD."""
    return bool(C.flag("ENABLE_ALWAYS_PROPOSE_ON_SATURATION",
                       getattr(C, "ENABLE_ALWAYS_PROPOSE_ON_SATURATION", False)))


@dataclass
class Candidate:
    courier_id: str
    name: Optional[str]
    score: float
    feasibility_verdict: str  # "MAYBE" | "NO"
    feasibility_reason: str
    plan: Optional[RoutePlanV2]
    metrics: Dict[str, Any] = field(default_factory=dict)
    best_effort: bool = False
    # BUG-D Faza 2b 2026-05-28 — per-route v2 traffic multiplier shadow data.
    # Populated by `_v327_eval_courier` via TLS leg tracking + traffic_v2_aggregator.
    # None gdy brak OSRM calls dla tego candidate (rare edge case, early return paths).
    # Spec: dispatch_v2/traffic_v2_aggregator.py docstring.
    traffic_v2_shadow_route: Optional[Dict[str, Any]] = None


def _v328_classify_fail_cause(exc: Exception) -> str:
    """L2.2 (most K5): klasa przyczyny fail-u kuriera w catch-allu _v328_eval_safe.

    'data_poison'  → wyjątek pochodzi z FAIL-LOUD strażnika danych coords
                     (sentinel (0,0) / None / poza-bbox) — trucizna danych,
                     NIE bug logiki. Sygnatury = kontrakty komunikatów strażników
                     (osrm_client.haversine Lekcja #32/#81, OSRM coord-guard,
                     COORD_GUARD L2.1). Anty-dryf: test woła realny strażnik
                     i asertuje klasę (tests/test_v328_fail_cause_l22.py).
    'real_bug'     → każdy inny nieoczekiwany wyjątek.
    Uwaga: 'infeasible' (legalny brak kandydata) NIE jest wyjątkiem — to
    result=None z _v327_eval_courier, nigdy nie trafia do tej klasyfikacji.
    """
    msg = str(exc)
    if isinstance(exc, ValueError) and (
        "haversine: None coords" in msg
        or "haversine: sentinel (0,0)" in msg
        or "coord" in msg.lower() and ("bbox" in msg.lower() or "sentinel" in msg.lower())
    ):
        return "data_poison"
    return "real_bug"


# L2.2: stan zbiorczego alertu data-poison (per-proces dispatch-shadow).
# Wzór zbiorczości = worker-stuck (shadow_dispatcher): okno + próg + realert,
# NIGDY per-zdarzenie. Emisja WYŁĄCZNIE za flagą ENABLE_V328_POISON_ALERT (OFF).
_V328_POISON_ALERT_STATE: Dict[str, Any] = {"events": [], "last_sent_ts": 0.0}


def _v328_maybe_poison_alert(order_id, poison_cids, now_ts: Optional[float] = None,
                             _state: Optional[dict] = None) -> bool:
    """Zbiorczy operator-alert na data-poison. Zwraca True gdy alert WYSŁANY.

    Za flagą ENABLE_V328_POISON_ALERT (default OFF — kod inert). Progi
    env-overridable: V328_POISON_ALERT_{WINDOW_MIN,MIN_EVENTS,REALERT_SEC}.
    Fail-soft: wysyłka nie może wywalić assess_order.
    """
    if not poison_cids:
        return False
    if not C.flag("ENABLE_V328_POISON_ALERT",
                  getattr(C, "ENABLE_V328_POISON_ALERT", False)):
        return False
    st = _state if _state is not None else _V328_POISON_ALERT_STATE
    if now_ts is None:
        import time as _pa_time
        now_ts = _pa_time.time()
    window_s = float(getattr(C, "V328_POISON_ALERT_WINDOW_MIN", 30.0)) * 60.0
    st["events"] = [e for e in st["events"] if now_ts - e[0] <= window_s]
    st["events"].append((now_ts, str(order_id), [str(c) for c in poison_cids]))
    min_events = int(getattr(C, "V328_POISON_ALERT_MIN_EVENTS", 5))
    realert_s = float(getattr(C, "V328_POISON_ALERT_REALERT_SEC", 1800.0))
    if len(st["events"]) < min_events:
        return False
    if now_ts - st["last_sent_ts"] < realert_s:
        return False
    cids = sorted({c for _, _, cl in st["events"] for c in cl})
    oids = [o for _, o, _ in st["events"]]
    msg = (
        f"🧪 DATA-POISON (L2.2): {len(st['events'])} zdarzeń trucizny coords w "
        f"{window_s/60:.0f} min (próg {min_events}). Kurierzy: {cids[:8]}; "
        f"ostatnie ordery: {oids[-5:]}. Fail-loud strażnik coords wywala ewaluację "
        f"kuriera — sprawdź źródło sentineli (K5; shadow_decisions: v328_fail_causes)."
    )
    try:
        from dispatch_v2.telegram_utils import send_admin_alert as _pa_alert
        _pa_alert(msg, priority="low")
    except Exception as _pa_e:  # fail-soft: alert nie może psuć decyzji
        log.warning(f"V328_POISON_ALERT wysyłka pominięta: {_pa_e!r}")
        return False
    st["last_sent_ts"] = now_ts
    return True


@dataclass
class PipelineResult:
    order_id: str
    verdict: str  # "PROPOSE" | "KOORD" | "SKIP"
    reason: str
    best: Optional[Candidate]
    candidates: List[Candidate]
    pickup_ready_at: Optional[datetime]
    restaurant: Optional[str]
    delivery_address: Optional[str] = None
    # CHOICE-SET (2026-07-21): pełna oceniona pula sprzed top-N, przeniesiona
    # jawnie przez granicę selection→shadow serializer. `candidates` pozostaje
    # kompatybilnym top-N używanym przez istniejące renderery/konsumentów.
    # Serializer emituje z tego wyłącznie sześć bezpiecznych pól przy fladze ON;
    # flaga jest domyślnie OFF.
    full_pool_candidates: Optional[List[Candidate]] = None
    # Sprint-1 2026-04-30 (logging extension): pool size scalars dla counterfactual
    # analysis. pool_total_count = liczba kandydatów PRZED feasibility cut (cała
    # rozważana pula), pool_feasible_count = liczba MAYBE post-feasibility.
    # Domyślnie 0 (early_bird path nie wchodzi w feasibility loop).
    pool_total_count: int = 0
    pool_feasible_count: int = 0
    # Faza 7-AUTO-PROXIMITY (2026-05-06): auto-route classification dla post-PROPOSE
    # routing. Domyślnie "ACK" — backward compat: KOORD/SKIP nie odpalają classifier.
    # Spec: eod_drafts/2026-05-06/faza_7_auto_proximity_design_spec.md
    auto_route: str = "ACK"
    auto_route_reason: str = ""
    # Classifier telemetry snapshot — populated by _classify_and_set_auto_route.
    # Zawiera: pool_feasible, score_margin, tier_best, pos_source_best, czasowka, etc.
    # Read-only consumption w shadow_dispatcher serialize.
    auto_route_context: Optional[Dict[str, Any]] = field(default_factory=dict)
    # MP-#13 (2026-05-08): L3 caller propagation. True gdy osrm_client.is_degraded()
    # przy entry do assess_order — caller (telegram_approver) może hint'ować "⚠
    # degraded mode" w propozycji. Defaults False (healthy). Read-only consumption
    # w shadow_dispatcher._serialize_result top-level field + decision_meta dict.
    degraded_osrm: bool = False
    # Snapshot diagnostic counters at assess_order time. Defaults to None (no degradation).
    osrm_cache_age_s: Optional[float] = None
    osrm_degraded_since_ts: Optional[float] = None
    # FAIL-04 (2026-06-06): shadow-first "slepa wiara w prep" sygnal. None gdy brak
    # anomalii lub flaga OFF. Dict {restaurant, declared_prep_min, empirical_median_min,
    # empirical_p90_min, gap_min, threshold_min, chronically_late}. Read-only consumption
    # w shadow_dispatcher._serialize_result. NIE wplywa na pickup_ready_at/score/verdict.
    prep_variance_anomaly: Optional[Dict[str, Any]] = None
    # AUTON-01 (2026-06-13): telemetria bramki auto-assign, compute-zawsze
    # (lekcja #186). would_auto_assign=None tylko gdy gate nie był liczony
    # (KOORD/SKIP bez classify). Egzekucja = auto_assign_executor (shadow only,
    # flaga ENABLE_AUTO_ASSIGN). Projekt: eod_drafts/2026-06-13/AUTON01_DESIGN.md.
    would_auto_assign: Optional[bool] = None
    auto_block_reasons: Optional[List[str]] = None
    # L2.2 (2026-07-02): przyczyny fail-ów per kurier z catch-alla _v328_eval_safe
    # {cid: 'data_poison'|'real_bug'}. None gdy zero fail-ów (ścieżki wczesne/czysto).
    # Read-only consumption w shadow_dispatcher._serialize_result top-level.
    v328_fail_causes: Optional[Dict[str, str]] = None
    # W0.2 advisory (2026-07-06): bezpiecznik fabrykacji ETA. eta_unreliable=None gdy
    # nie liczono (brak best/planu) lub gałąź nie doszła; True gdy pred_carry balonuje
    # względem fizycznego robust_ref (fabrykacja route-simu). Compute-ALWAYS (shadow),
    # niezależnie od flagi — aktywny routing (defer/uncertainty zamiast KOORD-z-fabrykatem)
    # tylko gdy ENABLE_ETA_FABRICATION_GUARD. Read-only consumption w serializerze (B top-level;
    # sygnały per-order w best.metrics → auto A+B). NIE wpływa na score/feasibility.
    eta_unreliable: Optional[bool] = None
    eta_unreliable_meta: Optional[Dict[str, Any]] = None
    # Podpowiedź „to jest kandydat do deferu/uncertainty, nie twardy KOORD" — ustawiana
    # TYLKO gdy flaga ON i wykryto fabrykację przy werdykcie eskalacyjnym. Konsument:
    # telegram_approver/konsola (framing uncertainty). W1 (defer-engine) skonsumuje jako trigger.
    eta_defer_hint: Optional[bool] = None
    # W1/T2.4 (2026-07-07): stempel would-be-mode (S1/S2/S3) + reason z obserwatora
    # trybów (shadow; NIE krokuje FSM, tylko odczyt stanu). None gdy flaga OFF / brak
    # stanu. Read-only w serializerze; NIE wpływa na verdict/score/feasibility.
    mode: Optional[str] = None
    mode_reason: Optional[str] = None
    # Z-P0-01 faza A: finalny, obserwacyjny werdykt R6/R27/SLA. Ustawiany
    # dokladnie raz w publicznym ogonie assess_order; nigdy konsumowany przez
    # selekcje/auto-route. Any unika cyklu typow z czystym core.
    rule_verdict: Optional[Any] = None
    # Z-P1-03 Faza A: addytywna telemetria czasu. Dolaczana dopiero w ogonie
    # assess_order (po selection i firewallu), nigdy nie jest wejsciem decyzji.
    stage_timing: Optional[Dict[str, Any]] = None


# ─── FAIL-04: prep-variance anomaly (A1 anomaly block, shadow-first) ───
# Empiryczne zrodlo: restaurant_meta.json (te same dane co daily_briefing R17/R19).
# F1.8g LANDMINE: prep_variance NIE wolno doliczac do pickup_ready_at (zawyzalo
# wyswietlany czas = bug wg Adriana). Tu uzywamy go TYLKO jako sygnal alertu/shadow.
RESTAURANT_META_PATH = "/root/.openclaw/workspace/dispatch_state/restaurant_meta.json"
_PREP_META_CACHE: Dict[str, Any] = {"mtime": None, "data": None, "index": None}


def _load_restaurant_meta_cached() -> Optional[dict]:
    """mtime-cached load restaurant_meta.json. Fail-soft -> None (zero raise)."""
    try:
        mt = os.path.getmtime(RESTAURANT_META_PATH)
        if _PREP_META_CACHE["mtime"] != mt:
            with open(RESTAURANT_META_PATH, encoding="utf-8") as fh:
                data = json.load(fh)
            rests = (data.get("restaurants") or {}) if isinstance(data, dict) else {}
            # lowercase index dla tolerancyjnego dopasowania nazwy
            _PREP_META_CACHE["index"] = {
                str(k).strip().lower(): v for k, v in rests.items()
            }
            _PREP_META_CACHE["data"] = data
            _PREP_META_CACHE["mtime"] = mt
        return _PREP_META_CACHE["data"]
    except Exception:
        return None


def restaurant_prep_variance(
    restaurant_name: Optional[str], meta: Optional[dict] = None
) -> Optional[Dict[str, Any]]:
    """Empiryczna prep-variance restauracji z restaurant_meta.json.

    Zwraca {median, p90, sample_n, high, low_confidence, chronically_late} lub
    None (brak nazwy / brak danych / median=None). Dopasowanie nazwy: exact-strip
    potem lowercase fallback. Pure read, fail-soft.
    """
    if not restaurant_name:
        return None
    name = str(restaurant_name).strip()
    r = None
    if meta is not None:
        rests = (meta.get("restaurants") or {}) if isinstance(meta, dict) else {}
        r = rests.get(name) or {
            str(k).strip().lower(): v for k, v in rests.items()
        }.get(name.lower())
    else:
        if _load_restaurant_meta_cached() is None:
            return None
        idx = _PREP_META_CACHE.get("index") or {}
        r = idx.get(name.lower())
    if not isinstance(r, dict):
        return None
    pv = r.get("prep_variance_min") or {}
    flags = r.get("flags") or {}
    if pv.get("median") is None:
        return None
    return {
        "median": pv.get("median"),
        "p90": pv.get("p90"),
        "sample_n": pv.get("sample_n"),
        "high": bool(flags.get("prep_variance_high")),
        "low_confidence": bool(flags.get("low_confidence")),
        "chronically_late": bool(flags.get("chronically_late")),
    }


def detect_prep_variance_anomaly(
    restaurant_name: Optional[str],
    declared_prep_min: Optional[float],
    meta: Optional[dict] = None,
) -> Optional[Dict[str, Any]]:
    """FAIL-04: anomalia "slepej wiary w prep".

    Fires gdy restauracja prep_variance_high (i NIE low_confidence) ma zadeklarowany
    prep nizszy od empirycznej mediany o >= RESTAURANT_PREP_VARIANCE_HARD_MIN.
    Zwraca dict anomalii albo None. NIE modyfikuje czasu (F1.8g) — czysty sygnal.
    """
    pv = restaurant_prep_variance(restaurant_name, meta=meta)
    if not pv or not pv.get("high") or pv.get("low_confidence"):
        return None
    median = pv.get("median")
    if median is None:
        return None
    declared = float(declared_prep_min) if declared_prep_min is not None else 0.0
    gap = float(median) - declared
    if gap < float(C.RESTAURANT_PREP_VARIANCE_HARD_MIN):
        return None
    return {
        "restaurant": str(restaurant_name).strip(),
        "declared_prep_min": declared,
        "empirical_median_min": median,
        "empirical_p90_min": pv.get("p90"),
        "gap_min": round(gap, 1),
        "threshold_min": float(C.RESTAURANT_PREP_VARIANCE_HARD_MIN),
        "chronically_late": pv.get("chronically_late"),
    }


def _detect_and_set_prep_variance_anomaly(
    result: "PipelineResult", order_event: Optional[Dict[str, Any]]
) -> None:
    """FAIL-04 hook (shadow-first). Ustawia result.prep_variance_anomaly.

    Gated flaga ENABLE_PREP_VARIANCE_ANOMALY_SHADOW (default OFF). NIGDY raise,
    NIE zmienia pickup_ready_at/score/verdict — czysta telemetria do shadow logu.
    """
    try:
        if not C.flag("ENABLE_PREP_VARIANCE_ANOMALY_SHADOW", False):
            return
        rest = getattr(result, "restaurant", None) or (order_event or {}).get("restaurant")
        declared = (order_event or {}).get("prep_minutes")
        anomaly = detect_prep_variance_anomaly(rest, declared)
        result.prep_variance_anomaly = anomaly
        if anomaly:
            log.info(
                f"PREP_VARIANCE_ANOMALY order={getattr(result, 'order_id', '?')} "
                f"rest={rest!r} declared={anomaly['declared_prep_min']} "
                f"median={anomaly['empirical_median_min']} gap={anomaly['gap_min']}min"
            )
    except Exception as _e:
        try:
            log.warning(
                f"prep_variance_anomaly detect exception "
                f"order={getattr(result, 'order_id', '?')}: {_e}"
            )
        except Exception:
            pass


def _classify_and_set_auto_route(
    result: "PipelineResult",
    fleet_snapshot: Optional[Dict[str, Any]],
    order_event: Optional[Dict[str, Any]],
    now: Optional[datetime] = None,
    v328_fail_causes: Optional[Dict[str, str]] = None,
) -> None:
    """Faza 7-AUTO-PROXIMITY: populate result.auto_route + auto_route_reason.

    Defensive: NIGDY raise — fallback do ACK przy any exception. Czyta flagi z
    flags.json (hot-reload). Pure side-effect (mutates result).

    L2.2 (2026-07-02): to jest WSPÓLNY LEJEK wszystkich post-eval returnów
    assess_order (11 call-site'ów) → tu doczepiamy result.v328_fail_causes
    ({cid: data_poison|real_bug} z catch-alla) do serializacji order-level.
    """
    if v328_fail_causes:
        result.v328_fail_causes = dict(v328_fail_causes)
    try:
        from dispatch_v2.auto_proximity_classifier import (
            classify_auto_route, build_context_for_logging,
        )
        flags = C.load_flags()
        route, reason = classify_auto_route(
            result=result,
            fleet_snapshot=fleet_snapshot,
            now=now,
            flags=flags,
            order_event=order_event,
        )
        result.auto_route = route
        result.auto_route_reason = reason
        result.auto_route_context = build_context_for_logging(
            result=result,
            fleet_snapshot=fleet_snapshot,
            flags=flags,
            order_event=order_event,
            now=now,  # SP-B2-PEAKWIN: spójny bucket czasowy z classify_auto_route
        )
    except Exception as _e:
        # Defense-in-depth: classifier exception NIE powinien zatrzymać dispatch.
        result.auto_route = "ACK"
        result.auto_route_reason = f"classifier_exception:{type(_e).__name__}"
        result.auto_route_context = {}
        try:
            log.warning(f"auto_proximity classifier exception order={getattr(result, 'order_id', '?')}: {_e}")
        except Exception:
            pass
    # FAIL-04 (shadow-first): wykryj slepa-wiare-w-prep dla wysoko-wariancyjnych
    # restauracji. Osobny try wewnatrz helpera — nie moze zaklocic auto_route.
    _detect_and_set_prep_variance_anomaly(result, order_event)
    # AUTON-01 (2026-06-13): bramka auto-assign — czysta telemetria liczona
    # ZAWSZE po klasyfikacji (lekcja #186). Defensywnie: wyjatek → fail-closed
    # (would=False + marker), nigdy nie zaklóca decyzji.
    try:
        from dispatch_v2.auto_assign_gate import evaluate_auto_assign
        _aa_flags = C.load_flags()
        _would, _blocks = evaluate_auto_assign(
            result, order_event, INFORMED_POS_SOURCES, flags=_aa_flags,
        )
        result.would_auto_assign = _would
        result.auto_block_reasons = _blocks
        # AUTON-02 (2026-06-30): policz plaster D i D' OBOK strict — czysta
        # telemetria (lekcja #186), NIE zmienia decyzji/egzekucji. Egzekutor
        # czyta wyłącznie strict `would_auto_assign`. Pozwala zmierzyć na żywo
        # rozmiar/jakość plastra przed flipem profilu w flags.json.
        # D = pool≥2 (luzno+srednio), D' = pool≥3 (luzno) — oba bez G2/G12.
        for _suf, _ov in (
            ("_d", {"AUTO_ASSIGN_REQUIRE_CLASSIFIER_AUTO": False,
                    "AUTO_ASSIGN_REQUIRE_MARGIN": False,
                    "AUTO_ASSIGN_MIN_POOL_FEASIBLE": 2}),
            ("_dprime", {"AUTO_ASSIGN_REQUIRE_CLASSIFIER_AUTO": False,
                         "AUTO_ASSIGN_REQUIRE_MARGIN": False,
                         "AUTO_ASSIGN_MIN_POOL_FEASIBLE": 3}),
        ):
            try:
                _fd = dict(_aa_flags or {})
                _fd.update(_ov)
                _w2, _b2 = evaluate_auto_assign(
                    result, order_event, INFORMED_POS_SOURCES, flags=_fd,
                )
            except Exception:
                _w2, _b2 = False, ["shadow_profile_exception"]
            setattr(result, f"would_auto_assign{_suf}", _w2)
            setattr(result, f"auto_block_reasons{_suf}", _b2)
    except Exception as _aa_e:
        result.would_auto_assign = False
        result.auto_block_reasons = [f"gate_exception:{type(_aa_e).__name__}"]
        result.would_auto_assign_d = False
        result.would_auto_assign_dprime = False
        try:
            log.warning(
                f"auto_assign gate exception order={getattr(result, 'order_id', '?')}: {_aa_e}"
            )
        except Exception:
            pass
    # L7.3 (2026-07-03, INV-LAYER-1): re-assert HARD-before-SOFT na EMIT. Ten helper jest
    # WSPÓLNYM lejkiem wszystkich post-eval returnów (11 call-site'ów) → tu strażnik biegnie
    # przy każdym zwrocie. OBSERWACYJNY (flaga OFF = no-op, bajt-parytet). Osobny try —
    # nie może zakłócić auto_route/gate.
    _split_layer_emit_assert(result, getattr(result, "order_id", None))
    # D6a SHADOW (2026-07-18, OWNER_CONFIRMED D1-D7): obietnice kalibratora per-kurier
    # dla ZWYCIĘZCY — wyłącznie NOWE metryki eta_calib_promise_* na best (wzorzec #8),
    # auto-serializowane do shadow_decisions (parytet stary-vs-nowy w cieniu 2 dni).
    # OBSERWACYJNE: flaga OFF = no-op; fail-soft w środku; osobny try jak wyżej.
    try:
        from dispatch_v2 import eta_calib_serving as _ECS
        _ECS.attach_shadow_promise_metrics(result, order_event)
    except Exception:
        pass


def get_pickup_ready_at(
    restaurant_name: Optional[str],
    pickup_at: Optional[datetime],
    now: datetime,
    meta: Optional[dict],
) -> Optional[datetime]:
    """Effective pickup-ready time = panel-declared pickup_at (czysto, bez bufora).

    F1.8g: usunięty historyczny bufor prep_variance_min (D16). Display w
    propozycji Telegram pokazywał czas powiększony o medianę spóźnień restauracji,
    co Adrian odbierał jako bug. restaurant_meta.prep_variance_min nadal
    dostępne dla alertów/monitoringu (R17/R19), ale NIE doliczane do pickup_ready_at.
    """
    if pickup_at is None:
        return None
    if pickup_at.tzinfo is None:
        pickup_at = pickup_at.replace(tzinfo=WARSAW)
    pickup_utc = pickup_at.astimezone(timezone.utc)
    return max(now, pickup_utc)


def _coloc_is_default_centroid(coords) -> bool:
    """#geocode-centroid (audyt 28.06): czy coords to DEFAULTOWY/nieznany punkt (Google→centrum
    miasta dla dwuznacznego adresu / firmowe fallback) → 0km coloc na nim jest FAŁSZYWY.
    122 adresów cache → BIALYSTOK_CENTER (53.1325,23.1688). Próg C.BUNDLE_COLOC_CENTROID_TOL_KM."""
    if not coords:
        return False
    try:
        c = (float(coords[0]), float(coords[1]))
    except (TypeError, ValueError, IndexError):
        return False
    tol = getattr(C, "BUNDLE_COLOC_CENTROID_TOL_KM", 0.06)
    for cen in getattr(C, "BUNDLE_COLOC_DEFAULT_CENTROIDS", ()):
        try:
            if haversine(c, (float(cen[0]), float(cen[1]))) <= tol:
                return True
        except Exception:
            continue
    return False


def compute_bundle_deliv_coloc(
        bag_raw, delivery_coords, metrics, committed_breach, *,
        flag_on, km_threshold, bonus_max, r6_hard_max, level1, level2,
        centroid_guard=False):
    """BUNDLE-DELIVERY-COLOCATION (Adrian 2026-06-26, case 509 Street Mama Thai+Raj).

    Forced-bundle z 2 TWARDYCH reguł (NIE miękka geometria pickupów): kredyt gdy
    nowa dostawa skolokowana z dostawą w bagu (różne restauracje, ten sam adres)
    ORAZ R6 czyste (≤ r6_hard_max, bez naruszeń) ORAZ committed honorowane (±5,
    `committed_breach is not True`). Zamyka pickup-centryczną ślepotę L1/L2.

    centroid_guard (#geocode-centroid audyt 28.06, flaga ENABLE_BUNDLE_COLOC_CENTROID_GUARD):
    gdy ON — wyklucz pary, gdzie któryś drop to DEFAULTOWY centroid (Google→centrum miasta dla
    nieznanego adresu) → 0km na nim FAŁSZYWY (122 adresów→BIALYSTOK_CENTER). OFF = zachowanie sprzed.

    Pure → testowalne (ON≠OFF). Zwraca (km|None, active:bool, bonus:float).
    flag OFF / L1|L2 już daje kredyt / brak skolokowania → (·, False, 0.0).
    """
    if not flag_on or level1 is not None or level2 is not None:
        return None, False, 0.0
    # L2.1: konsolidacja predykatu sentinela do kanonicznego walidatora (flaga ON).
    if not _coords_pass(
            bool(delivery_coords) and tuple(delivery_coords) != (0.0, 0.0)
            and delivery_coords[0] != 0.0,
            delivery_coords):
        return None, False, 0.0
    # #geocode-centroid: nowa dostawa na defaultowym centroidzie → WSZYSTKIE jej 0km matche fałszywe
    if centroid_guard and _coloc_is_default_centroid(delivery_coords):
        return None, False, 0.0
    best = None
    for b in (bag_raw or []):
        bd = b.get("delivery_coords")
        if not _coords_pass(
                bool(bd) and tuple(bd) != (0.0, 0.0) and bd[0] != 0.0, bd):
            continue
        # #geocode-centroid: drop w bagu na defaultowym centroidzie → pomiń (jego 0km fałszywy)
        if centroid_guard and _coloc_is_default_centroid(bd):
            continue
        try:
            dk = haversine(tuple(bd), tuple(delivery_coords))
        except Exception:
            continue
        if best is None or dk < best:
            best = dk
    if best is None:
        return None, False, 0.0
    km = round(best, 3)
    if km >= km_threshold:
        return km, False, 0.0
    r6_clean = (
        not metrics.get("r6_per_order_violations")
        and not metrics.get("r6_picked_up_violations")
        and (metrics.get("r6_max_bag_time_min") or 0.0) <= r6_hard_max)
    if r6_clean and committed_breach is not True:
        return km, True, max(0.0, bonus_max - km * 10.0)
    return km, False, 0.0


def _bag_dict_to_order_in_bag_raw(d: dict) -> dict:
    """V3.18: bag dict → orders_raw entry dla build_courier_bag_state.

    Translate string status ('assigned'/'picked_up') na int (3/5).
    Panel raw ma czas_odbioru_timestamp → pickup_time (Warsaw).
    """
    str_status = d.get("status", "assigned")
    int_status = 5 if str_status == "picked_up" else 3
    # V3.19f: czas_kuriera_warsaw first-choice pod flagą (panel commitment HH:MM
    # declared arrival). Fallback chain: pickup_at_warsaw → czas_odbioru_timestamp.
    pickup_t = None
    if ENABLE_CZAS_KURIERA_PROPAGATION:
        pickup_t = parse_panel_timestamp(d.get("czas_kuriera_warsaw"))
    if pickup_t is None:
        pickup_t = (
            parse_panel_timestamp(d.get("pickup_at_warsaw"))
            or parse_panel_timestamp(d.get("czas_odbioru_timestamp"))
        )
    added = parse_panel_timestamp(d.get("assigned_at")) or parse_panel_timestamp(d.get("created_at"))
    return {
        "order_id": str(d.get("order_id") or d.get("id") or ""),
        "restaurant_address": d.get("restaurant") or d.get("restaurant_address", ""),
        "restaurant_coords": tuple(d["pickup_coords"]) if d.get("pickup_coords") else None,
        "drop_address": d.get("delivery_address", ""),
        "drop_coords": tuple(d["delivery_coords"]) if d.get("delivery_coords") else None,
        "pickup_time": pickup_t,
        "predicted_drop_time": None,  # computed later by route_simulator
        "status": int_status,
        "added_at": added,
    }


def _build_fleet_context_from_snapshot(
    fleet_snapshot: Dict[str, Any],
    now: datetime,
) -> FleetContext:
    """V3.18: build FleetContext z fleet_snapshot dla Bug 2 (overload penalty).

    Per courier: minimal CourierBagState (tylko bag_size + pos_source matter).
    """
    bag_states = []
    for cid, cs in fleet_snapshot.items():
        bag_raw = getattr(cs, "bag", []) or []
        orders_raw = [_bag_dict_to_order_in_bag_raw(b) for b in bag_raw]
        bag_states.append(build_courier_bag_state(
            courier_id=str(cid),
            nick=getattr(cs, "name", "?") or "?",
            pos_source=getattr(cs, "pos_source", "?") or "?",
            position=getattr(cs, "pos", None),
            orders_raw=orders_raw,
            now=now,
        ))
    return build_fleet_context(bag_states, now=now)


def _bag_coord_city(d: dict, kind: str) -> str:
    """Miasto dla geokodu bag-ordera (kind='pickup'|'delivery'), fallback Białystok."""
    return (d.get(f"{kind}_city") or d.get("city") or "Białystok")


def _repair_bag_coords(d: dict, kind: str):
    """Lekcja #140: re-geokoduj brakującą/nieprawidłową współrzędną bag-ordera tą
    samą ścieżką co defense gate nowego zlecenia (NIE (0,0)). Zwraca (lat,lon) lub
    None. Best-effort — geokod cache-first, nigdy nie crashuje assess_order.
    Pickup→geocode_restaurant(nazwa), delivery→geocode(adres)."""
    if not C.ENABLE_BAG_COORD_REPAIR:
        return None
    try:
        from dispatch_v2 import geocoding as _geo
        city = _bag_coord_city(d, kind)
        if kind == "pickup":
            name = d.get("restaurant") or d.get("pickup_name")
            if not name:
                return None
            r = _geo.geocode_restaurant(str(name), d.get("pickup_address", "") or "", city=city)
        else:
            addr = d.get("delivery_address")
            if not addr:
                return None
            r = _geo.geocode(str(addr), city=city)
        if r and C.coords_in_bialystok_bbox(r):
            log.warning(
                "BAG_COORD_REPAIR oid=%s kind=%s restaurant=%r → %r (było brak/nieprawidłowe)",
                d.get("order_id"), kind, d.get("restaurant"), tuple(r))
            return (round(float(r[0]), 6), round(float(r[1]), 6))
    except Exception as e:
        log.warning("BAG_COORD_REPAIR fail oid=%s kind=%s: %r",
                    d.get("order_id"), kind, e)
    return None


# Sprint F (2026-07-08): rate-limit logu fallbacku firmowego (klasa peak-only).
_firmowe_bag_fallback_log_count = 0


def _firmowe_bag_pickup_fallback(d: dict):
    """Sprint F (2026-07-08, źródło (0,0)/COORD_GUARD): fallback ODBIORU dla
    bag-ordera FIRMOWEGO gdy `_repair_bag_coords` zawiódł (runtime re-geokod
    padł — sieć/TTL w peaku). Flaga ON + aid∈FIRMOWE_KONTO_ADDRESS_IDS →
    FIRMOWE_KONTO_FALLBACK_COORDS (centrala Nadajesz, w bbox) zamiast cichego
    (0,0). (0,0) snapował w OSRM → COORD_GUARD sentinel 9999 → holder cicho
    wykluczany (geometria-ślepy pile-on, choroba L2.1). Flaga OFF / nie-firmowe
    → (0.0, 0.0) = LEGACY bajt-w-bajt (guard OSRM zostaje backstopem). Dotyczy
    WYŁĄCZNIE odbioru firmowego (pickup w uwagach = nierozwiązywalny; delivery
    firmowe zawsze geokodowane)."""
    global _firmowe_bag_fallback_log_count
    if not C.decision_flag("ENABLE_FIRMOWE_BAG_COORD_FALLBACK"):
        return (0.0, 0.0)
    try:
        _aid = d.get("address_id")
        _is_firmowe = _aid is not None and int(_aid) in C.FIRMOWE_KONTO_ADDRESS_IDS
    except (ValueError, TypeError):
        _is_firmowe = False
    if not _is_firmowe:
        return (0.0, 0.0)
    _fc = tuple(C.FIRMOWE_KONTO_FALLBACK_COORDS)
    _firmowe_bag_fallback_log_count += 1
    if (_firmowe_bag_fallback_log_count <= 20
            or _firmowe_bag_fallback_log_count % 100 == 0):
        log.warning(
            "FIRMOWE_BAG_COORD_FALLBACK #%d oid=%s aid=%s odbiór nierozwiązywalny "
            "→ centrala %r (zamiast (0,0)/COORD_GUARD)",
            _firmowe_bag_fallback_log_count, d.get("order_id"),
            d.get("address_id"), _fc)
    return _fc


def _osrm_guard_sentinel_coords(existing=None):
    """Fala #7 sentinel-as-data (2026-07-18): JEDYNY producent JAWNEGO sentinela
    (0.0, 0.0) dla backstopu guardu OSRM (fail-loud haversine, Lekcja #81).
    Rozsiane inline `or (0.0, 0.0)` skolapsowane do tego nazwanego kanału —
    sentinel pozycji powstaje wyłącznie w funkcjach-obronach (oracle #7),
    nigdy anonimowo w środku logiki. `existing` truthy → pass-through tuple
    (parytet z dawnym `tuple(x or (0,0))` / `x or (0,0)` bajt-w-bajt)."""
    return tuple(existing) if existing else (0.0, 0.0)


def _bag_dict_to_ordersim(d: dict) -> OrderSim:
    picked = parse_panel_timestamp(d.get("picked_up_at"))
    # V3.19f: czas_kuriera_warsaw first-choice dla pickup_ready_at (F2.1c R8 T_KUR).
    # Fallback do pickup_at_warsaw (pre-V3.19f behavior) gdy flaga False albo brak.
    pra = None
    if ENABLE_CZAS_KURIERA_PROPAGATION:
        pra = parse_panel_timestamp(d.get("czas_kuriera_warsaw"))
    if pra is None:
        pra = parse_panel_timestamp(d.get("pickup_at_warsaw"))
    status = d.get("status", "assigned")
    # Lekcja #140: bag-order z brakującą/nieprawidłową współrzędną → re-geokod
    # (NIE (0,0), bo (0,0) snapuje w OSRM do krawędzi ekstraktu → phantom 148min
    # leg → false INFEASIBLE → wycięcie wolnych kurierów). (0,0) zostaje tylko gdy
    # repair zawiedzie — wtedy guard OSRM (table/route) sentineluje JAWNIE.
    pickup_c = d.get("pickup_coords")
    deliv_c = d.get("delivery_coords")
    if not C.coords_in_bialystok_bbox(pickup_c):
        # Sprint F: ostatnia deska ODBIORU = fallback firmowy (centrala) za flagą
        # ON, inaczej (0,0) legacy → guard OSRM. _firmowe_bag_pickup_fallback OFF
        # / nie-firmowe zwraca (0.0, 0.0) → bajt-w-bajt jak dawniej.
        pickup_c = _repair_bag_coords(d, "pickup") or pickup_c \
            or _firmowe_bag_pickup_fallback(d)
    if not C.coords_in_bialystok_bbox(deliv_c):
        # Delivery firmowe zawsze geokodowane; centrala jako DOSTAWA byłaby błędna
        # → zostaje (0,0) legacy (guard backstop) — od fali #7 przez JEDYNY kanał.
        deliv_c = (_repair_bag_coords(d, "delivery") or deliv_c
                   or _osrm_guard_sentinel_coords())
    # V3.27.5 Path A (2026-04-27): defense-in-depth dla state inconsistency.
    # Pre-fix: status field jedyny signal picked_up. Path B fixes state_machine
    # COURIER_ASSIGNED handler (preserve terminal status), ale picked_up_at
    # canonical signal — działa NAWET jeśli future state_machine bug pojawi się
    # downstream. Per TASK H Q3: feasibility_v2 + sla_tracker już używają
    # picked_up_at preferred — Path A replikuje best practice.
    is_picked_up = (status == "picked_up") or (picked is not None)
    sim = OrderSim(
        order_id=str(d.get("order_id") or d.get("id") or ""),
        pickup_coords=tuple(pickup_c),
        delivery_coords=tuple(deliv_c),
        picked_up_at=picked,
        status="picked_up" if is_picked_up else "assigned",
        pickup_ready_at=pra,  # F2.1c R8 T_KUR propagation
    )
    # V3.27.1 sesja 2: dynamic attrs dla pre-proposal recheck helper.
    # OrderSim dataclass NIE ma tych pól w declaration, ale Python pozwala
    # dodać atrybuty per-instance. Helper czyta z getattr() z None fallback.
    sim.czas_kuriera_warsaw = d.get("czas_kuriera_warsaw")
    sim.assigned_at = d.get("assigned_at")
    sim.courier_id = d.get("courier_id")
    # R-PACZKI-FLEX (2026-05-20): address_id (=restaurant_id w panelu gastro),
    # order_type (czasowka vs elastic), created_at_utc (pojawienie w gastro).
    sim.address_id = d.get("address_id")
    sim.order_type = d.get("order_type")
    sim.created_at_utc = d.get("created_at_utc") or d.get("created_at")
    return sim


def _r_paczki_flex_penalty(new_order: OrderSim, plan, now: datetime) -> float:
    """R-PACZKI-FLEX (2026-05-20): liniowa kara dla NIE-czasówka paczki, nad
    soft cap 2h pickup / 3h delivery liczonym od created_at (pojawienie w
    panelu gastro). Czasówka-paczka → 0 (R-DECLARED-TIME nadrzędne).
    Fail-soft: zwraca 0.0 przy braku danych / wyjątku."""
    try:
        if not (C.ENABLE_R_PACZKI_FLEX or C.flag("ENABLE_R_PACZKI_FLEX", False)):
            return 0.0
        if not C.is_paczka_flex_eligible({
            "address_id": getattr(new_order, "address_id", None),
            "order_type": getattr(new_order, "order_type", None),
        }):
            return 0.0
        if plan is None:
            return 0.0
        created = getattr(new_order, "created_at_utc", None)
        if isinstance(created, str):
            created = parse_panel_timestamp(created)
        if created is None:
            return 0.0
        if getattr(created, "tzinfo", None) is None:
            created = created.replace(tzinfo=timezone.utc)
        oid = new_order.order_id
        penalty = 0.0
        eta_pickup = plan.pickup_at.get(oid) if hasattr(plan, "pickup_at") else None
        if eta_pickup is not None:
            if eta_pickup.tzinfo is None:
                eta_pickup = eta_pickup.replace(tzinfo=timezone.utc)
            overrun = (eta_pickup - created).total_seconds() / 60.0 - C.PACZKA_PICKUP_SOFT_CAP_MIN
            if overrun > 0:
                penalty -= overrun * C.PACZKA_FLEX_PENALTY_PER_MIN
        eta_deliv = plan.predicted_delivered_at.get(oid) if hasattr(plan, "predicted_delivered_at") else None
        if eta_deliv is not None:
            if eta_deliv.tzinfo is None:
                eta_deliv = eta_deliv.replace(tzinfo=timezone.utc)
            overrun = (eta_deliv - created).total_seconds() / 60.0 - C.PACZKA_DELIVERY_SOFT_CAP_MIN
            if overrun > 0:
                penalty -= overrun * C.PACZKA_FLEX_PENALTY_PER_MIN
        return penalty
    except Exception as _ex:
        log.warning(f"_r_paczki_flex_penalty failed oid={getattr(new_order, 'order_id', '?')}: {type(_ex).__name__}: {_ex}")
        return 0.0


def _oldest_in_bag_min(bag: List[OrderSim], now: datetime) -> Optional[float]:
    ages: List[float] = []
    for o in bag:
        if o.picked_up_at is None:
            continue
        pu = o.picked_up_at
        if pu.tzinfo is None:
            pu = pu.replace(tzinfo=timezone.utc)
        ages.append((now - pu.astimezone(timezone.utc)).total_seconds() / 60.0)
    return max(ages) if ages else None


def _compute_loadaware_shadow(candidates, feasible, top):
    """Load-aware distribution counterfactual (2026-06-07) — SHADOW / log-only.

    Kogo wybrałaby dystrybucja load-aware (najmniej obłożony kurier z PEŁNEGO
    rosteru `candidates`) vs argmax-best (top[0]). Pure, testowalny, ZERO mutacji
    best/feasible/top. Walidacja offline modelem outcome + cascade harness
    (eod_drafts/2026-06-07/). Patrz memory ziomek-autonomy-cascade-verdict.
    """
    if not candidates:
        return None

    def _bag(c):
        return int((getattr(c, "metrics", {}) or {}).get("bag_size_before") or 0)

    def _key(c):
        return (_bag(c), -(float(getattr(c, "score", 0.0) or 0.0)))

    best_cid = str(getattr(top[0], "courier_id", "")) if top else None
    feas = [c for c in candidates if getattr(c, "feasibility_verdict", None) == "MAYBE"]
    la_feas = min(feas, key=_key) if feas else None
    la_all = min(candidates, key=_key)
    la_feas_cid = str(getattr(la_feas, "courier_id", "")) if la_feas else None
    la_all_cid = str(getattr(la_all, "courier_id", ""))
    return {
        "best_cid": best_cid,
        "best_bag": _bag(top[0]) if top else None,
        "la_feasible_cid": la_feas_cid,
        "la_feasible_bag": _bag(la_feas) if la_feas else None,
        "la_roster_cid": la_all_cid,
        "la_roster_bag": _bag(la_all),
        "changed_feasible": bool(la_feas_cid and la_feas_cid != best_cid),
        "changed_roster": la_all_cid != best_cid,
        "roster": [
            {
                "cid": str(getattr(c, "courier_id", "")),
                "bag": _bag(c),
                "feas": (getattr(c, "feasibility_verdict", None) == "MAYBE"),
                "score": round(float(getattr(c, "score", 0.0) or 0.0), 1),
                "pos": (getattr(c, "metrics", {}) or {}).get("pos_source"),
            }
            for c in candidates
        ],
    }


def _pre_shift_gradient_penalty(shift_start_min, loadgov_ewma):
    """Kara pre-shift gradientowa (Adrian 2026-06-24). Zwraca punkty (≤0) lub None.

    m = minuty do startu zmiany (cs.shift_start_min):
      m ≤ 0                  → None (brak kary; kurier praktycznie na zmianie)
      m ≤ PRE_SHIFT_NEAR_MIN → ∝ m (lekka — chętnie brany, restauracja nie czeka rano)
      NEAR < m ≤ cap         → PRE_SHIFT_FAR_PEN (~veto) POZA dużym przeładowaniem floty;
                               loadgov_ewma ≥ PRE_SHIFT_FAR_UNLOCK_LOAD → relaks do ∝ m
                               (lepiej kurier weźmie za chwilę niż restauracja czeka 40-60′).
    Rygor „odbiór nie przed zmianą" egzekwuje osobno departure-clamp (≥ shift_start)."""
    m = float(shift_start_min or 0)
    if m <= 0:
        return None
    if m <= C.PRE_SHIFT_NEAR_MIN:
        return C.PRE_SHIFT_NEAR_PEN_PER_MIN * m
    if loadgov_ewma is not None and loadgov_ewma >= C.PRE_SHIFT_FAR_UNLOCK_LOAD:
        return C.PRE_SHIFT_NEAR_PEN_PER_MIN * m
    return C.PRE_SHIFT_FAR_PEN


def _l4_floor_candidate_eta(c) -> Optional[float]:
    """L4 (#1) — podnieś eta_pickup kandydata do available_from (metrics
    available_from_utc, wypełniony z courier_resolver gdy flaga ON). Pure floor:
    tylko podnosi, NIGDY nie obniża (zero regresji przez zaniżenie). Mutuje
    c.metrics (eta_pickup_utc/eta_drive_utc/af_floor_applied_min/af_applied).
    Zwraca minuty podniesienia (0.0 = no-op), None gdy brak available_from/parse.
    Wydzielone z pętli assess_order → testowalne + parytet z #3/#5 (ta sama
    wartość floora = available_from)."""
    _af_iso = c.metrics.get("available_from_utc")
    if not _af_iso:
        return None
    try:
        _af_dt = datetime.fromisoformat(_af_iso)
        if _af_dt.tzinfo is None:
            _af_dt = _af_dt.replace(tzinfo=timezone.utc)
        _eta_iso = c.metrics.get("eta_pickup_utc")
        _cur = datetime.fromisoformat(_eta_iso) if _eta_iso else None
        if _cur is not None and _cur.tzinfo is None:
            _cur = _cur.replace(tzinfo=timezone.utc)
        if _cur is not None and _af_dt > _cur:
            raised = round((_af_dt - _cur).total_seconds() / 60.0, 1)
            c.metrics["af_floor_applied_min"] = raised
            c.metrics["eta_pickup_utc"] = _af_dt.isoformat()
            c.metrics["eta_drive_utc"] = _af_dt.isoformat()
            c.metrics["af_applied"] = True
            return raised
        c.metrics["af_floor_applied_min"] = 0.0
        c.metrics["af_applied"] = False
        return 0.0
    except Exception:
        c.metrics["af_applied"] = False
        return None


# ─── W0.2: bezpiecznik fabrykacji ETA (advisory Faza 6.2; werdykt E-1) ───

def _robust_eta_ref_min(pickup_coords, delivery_coords, now) -> Optional[float]:
    """Fizyczny robust_ref (minuty) dla pojedynczej nogi pickup→dostawa:
    osrm freeflow drive (już z traffic-mult, Opus: freeflow·mult) + service + slack.
    None gdy coords niewiarygodne / OSRM sentinel/fallback — bez PEWNEGO floora NIE
    osądzamy fabrykacji (fail-safe: brak flagi, nie fałszywe stłumienie KOORD)."""
    try:
        if not pickup_coords or not delivery_coords:
            return None
        from dispatch_v2 import osrm_client as _oc
        r = _oc.route(tuple(pickup_coords), tuple(delivery_coords))
        if not isinstance(r, dict):
            return None
        dur = r.get("duration_min")
        if dur is None or r.get("replay_miss") or r.get("coord_invalid"):
            return None
        # sentinel niewiarygodnej współrzędnej (duża wartość) = brak floora
        sentinel = getattr(_oc, "OSRM_INVALID_COORD_SENTINEL_MIN", 9990.0)
        if float(dur) >= float(sentinel) - 1.0:
            return None
        return float(dur) + C.ETA_ROBUST_SERVICE_MIN + C.ETA_ROBUST_SLACK_MIN
    except Exception:
        return None


def _eta_fabrication_check(result: "PipelineResult", order_event: dict,
                           now: Optional[datetime]) -> None:
    """Shadow-first bezpiecznik fabrykacji ETA. Compute-ALWAYS (niezależnie od flagi):
    ustawia `result.eta_unreliable`(+meta) i sygnały w `best.metrics` (→ auto serializer
    A+B) gdy pred_carry balonuje względem fizycznego robust_ref. Aktywny routing
    (defer/uncertainty zamiast KOORD-z-fabrykatem) TYLKO gdy ENABLE_ETA_FABRICATION_GUARD.

    pred_carry = predicted_delivered_at[new] − pickup_ready_at (CARRY, nie total —
    total zawierałby legalny WAIT early-bird → fałszywe flagowanie). Fail-soft: każdy
    wyjątek zostawia stan bez zmian. NIE dotyka score/feasibility/kanonu."""
    try:
        best = result.best
        if best is None or getattr(best, "plan", None) is None:
            return
        oid = str(result.order_id)
        pda = getattr(best.plan, "predicted_delivered_at", None) or {}
        pred_deliv = pda.get(oid)
        ref_t = result.pickup_ready_at or now
        if pred_deliv is None or ref_t is None:
            return
        if pred_deliv.tzinfo is None:
            pred_deliv = pred_deliv.replace(tzinfo=timezone.utc)
        if ref_t.tzinfo is None:
            ref_t = ref_t.replace(tzinfo=timezone.utc)
        pred_carry = (pred_deliv - ref_t).total_seconds() / 60.0
        floor = float(C.ETA_FABRICATION_FLOOR_MIN)
        if pred_carry <= floor:
            result.eta_unreliable = False  # poniżej podłogi — na pewno nie fabrykacja
            return
        # dopiero powyżej podłogi (rzadko) licz fizyczny robust_ref (1 wywołanie OSRM)
        robust_ref = _robust_eta_ref_min(
            order_event.get("pickup_coords"), order_event.get("delivery_coords"), now)
        if robust_ref is None or robust_ref <= 0:
            result.eta_unreliable = None  # brak pewnego floora → nie osądzamy
            return
        ratio = pred_carry / robust_ref
        unreliable = bool(ratio > float(C.ETA_FABRICATION_RATIO))  # ∧ pred_carry>floor (wyżej)
        result.eta_unreliable = unreliable
        result.eta_unreliable_meta = {
            "pred_carry_min": round(pred_carry, 1),
            "robust_ref_min": round(robust_ref, 1),
            "ratio": round(ratio, 2),
            "floor_min": floor,
        }
        if best.metrics is None:
            best.metrics = {}
        best.metrics["eta_unreliable"] = unreliable
        best.metrics["eta_fabrication_ratio"] = round(ratio, 2)
        best.metrics["eta_robust_ref_min"] = round(robust_ref, 1)
        if not unreliable:
            return
        # AKTYWNY routing — tylko za flagą (shadow-first: OFF = czysta obserwacja)
        if C.flag("ENABLE_ETA_FABRICATION_GUARD", False):
            # NIGDY KOORD z fabrykatem: przy eskalacji oznacz jako defer/uncertainty
            # (framing dla telegram/konsoli; W1 defer-engine skonsumuje jako trigger).
            result.eta_defer_hint = True
            if result.verdict == "KOORD":
                result.reason = (result.reason or "") + "|eta_unreliable_defer"
                best.metrics["eta_koord_fabrication_flagged"] = True
    except Exception:
        pass  # fabrication-guard NIGDY nie wywróci assess flow


def _a360_d1_od07_firewall_enabled() -> bool:
    """Fail-closed odczyt flagi obserwacyjnej; brak nośnika oznacza OFF."""
    try:
        return bool(C.flag(
            "ENABLE_A360_D1_OD07_FIREWALL_EXEMPT_TRUTH",
            getattr(C, "ENABLE_A360_D1_OD07_FIREWALL_EXEMPT_TRUTH", False),
        ))
    except Exception:
        return bool(getattr(
            C, "ENABLE_A360_D1_OD07_FIREWALL_EXEMPT_TRUTH", False))


def _final_rule_unknown_dict(result: "PipelineResult", reason_code: str) -> Dict[str, Any]:
    """Ostatni, bezmodułowy fallback v1 albo flagowany OD-07 v3.

    Używa wyłącznie prymitywów JSON i nie zależy od importu firewalla ani
    loggera; odczyt flagi sam ma fallback OFF. Dzięki temu awaria instrumentu nie usuwa
    informacji, że pokrycie reguł jest nieznane.
    """
    try:
        order_id = str(getattr(result, "order_id", "") or "")
    except Exception:
        order_id = ""
    try:
        decision_verdict = str(getattr(result, "verdict", "") or "")
    except Exception:
        decision_verdict = ""
    try:
        best = getattr(result, "best", None)
    except Exception:
        best = None
    try:
        selected_courier_id = (
            str(getattr(best, "courier_id", "") or "") if best is not None else None
        )
    except Exception:
        selected_courier_id = None
    try:
        if best is None:
            selection_mode = "none"
        elif getattr(best, "plan", None) is None:
            selection_mode = "planless"
        elif bool(getattr(best, "best_effort", False)):
            selection_mode = "best_effort"
        else:
            selection_mode = "normal"
    except Exception:
        selection_mode = "unknown"
    try:
        missing_reason = str(reason_code or "FINAL_FALLBACK:UNKNOWN")
    except Exception:
        missing_reason = "FINAL_FALLBACK:UNKNOWN"

    def _unknown_rule(rule_id: str, policy_variant: str) -> Dict[str, Any]:
        return {
            "rule_id": rule_id,
            "policy_variant": policy_variant,
            "status": "UNKNOWN",
            "limit": None,
            "evaluated_count": 0,
            "violation_count": 0,
            "exempt_count": 0,
            "unknown_count": 1,
        }

    if not _a360_d1_od07_firewall_enabled():
        return {
            "schema": "rule_verdict.v1",
            "phase": "A_SHADOW",
            "status": "UNKNOWN",
            "coverage": "NONE",
            "enforcement": "NONE",
            "decision_order_id": order_id,
            "decision_verdict": decision_verdict,
            "selected_courier_id": selected_courier_id,
            "selection_mode": selection_mode,
            "always_propose_enabled": None,
            "policy_pending": ["B-01", "B-02"],
            "rules": [
                _unknown_rule("R6_THERMAL", "physical_thermal"),
                _unknown_rule("R27_COMMITTED_PICKUP", "strict_5_candidate"),
                _unknown_rule("SLA_DELIVERY", "anchor_unknown"),
            ],
            "violations": [],
            "exceptions": [],
            "missing_reasons": [missing_reason],
        }

    r6_unknown = {
        "rule_id": "R6_THERMAL",
        "policy_variant": "in_vehicle_age_od07",
        "status": "HOLD",
        "limit": 35.0,
        "evaluated_count": 0,
        "violation_count": 0,
        "exempt_count": 0,
        "unknown_count": 1,
        "physical_status": "UNBOUND",
        "interval": "physical_possession_to_customer_handoff",
        "normal_limit_min": 35.0,
        "alarm_limit_min": 40.0,
        "alarm_count": 0,
        "prohibited_count": 0,
        "introduced_order_count": 0,
        "preexisting_order_count": 0,
        "causality_unbound_order_count": 1,
        "evidence_lineage": [],
        "food_ready_age_status": "SEPARATE_UNBOUND",
        "food_ready_age_threshold_min": None,
        "count_unit": "orders",
    }
    return {
        "schema": "rule_verdict.v3",
        "phase": "A_SHADOW",
        "evaluation_stage": "POST_SELECTION_OD07_PHYSICAL_INTERVAL",
        "status": "HOLD",
        "physical_status": "UNBOUND",
        "coverage": "NONE",
        "enforcement": "NONE",
        "decision_order_id": order_id,
        "decision_verdict": decision_verdict,
        "selected_courier_id": selected_courier_id,
        "selection_mode": selection_mode,
        "always_propose_enabled": None,
        "policy_pending": [
            "R6_PHYSICAL_POSSESSION_EVENT_SOURCE",
            "R6_CUSTOMER_HANDOFF_EVENT_SOURCE",
            "R6_AUTOMATIC_ALARM_PREDICATE",
            "R6_PREDECISION_COUNTERFACTUAL",
        ],
        "rules": [
            r6_unknown,
            _unknown_rule("R27_COMMITTED_PICKUP", "strict_5_candidate"),
            _unknown_rule("SLA_DELIVERY", "anchor_unknown"),
        ],
        "violations": [],
        "exceptions": [],
        "missing_reasons": [missing_reason],
        "introduced_order_count": 0,
        "preexisting_order_count": 0,
        "causality_unbound_order_count": 1,
        "count_unit": "orders",
        "r6_event_binding": "UNBOUND",
    }


def _attach_final_rule_verdict(result: "PipelineResult", order_event: dict,
                               fleet_snapshot: Dict[str, Any],
                               decision_now: datetime) -> None:
    """Podłącz finalny firewall; każda awaria kończy się jawnym UNKNOWN."""
    _ifw = None
    _fw_policy = None
    _fw_exc: BaseException
    try:
        # Import także jest częścią instrumentacji i musi być fail-open dla decyzji.
        _ifw = importlib.import_module("dispatch_v2.core.invariant_firewall")
        try:
            _fw_flags = dict(C.load_flags() or {})
        except Exception:
            _fw_flags = {}

        def _fw_flag(name, fallback=False):
            return bool(_fw_flags.get(name, fallback))

        _fw_policy = _ifw.FirewallPolicy(
            r6_limit_min=float(C.BAG_TIME_HARD_MAX_MIN),
            sla_limit_min=float(C.BAG_TIME_HARD_MAX_MIN),
            r27_strict_limit_min=float(C.LATE_PICKUP_HARD_MAX_MIN),
            r27_overload_limit_min=float(C.OBJ_COMMITTED_PICKUP_TOL_LOOSE_MIN),
            overload_threshold=float(C.OBJ_COMMITTED_PICKUP_LOAD_THRESHOLD),
            package_address_ids=tuple(sorted(int(x) for x in C.PACZKA_ADDRESS_IDS)),
            package_thermal_exempt=_fw_flag(
                "ENABLE_PACZKA_R6_THERMAL_EXEMPT",
                getattr(C, "ENABLE_PACZKA_R6_THERMAL_EXEMPT", False)),
            sla_anchor_kind=(
                "ready" if (
                    _fw_flag("ENABLE_SLA_ANCHOR_UNIFIED",
                             getattr(C, "ENABLE_SLA_ANCHOR_UNIFIED", False))
                    and _fw_flag("ENABLE_SLA_GATE_READY_ANCHOR",
                                 getattr(C, "ENABLE_SLA_GATE_READY_ANCHOR", False))
                ) else "now"
            ),
            always_propose_enabled=_fw_flag(
                "ENABLE_ALWAYS_PROPOSE_ON_SATURATION",
                getattr(C, "ENABLE_ALWAYS_PROPOSE_ON_SATURATION", False)),
            od07_firewall_exempt_truth_enabled=_fw_flag(
                "ENABLE_A360_D1_OD07_FIREWALL_EXEMPT_TRUTH",
                getattr(
                    C, "ENABLE_A360_D1_OD07_FIREWALL_EXEMPT_TRUTH", False)),
        )
        result.rule_verdict = _ifw.evaluate_final(
            result, order_event, fleet_snapshot, decision_now, _fw_policy)
        return
    except Exception as exc:
        _fw_exc = exc

    # Preferuj typowany UNKNOWN, jeśli moduł/polityka są dostępne. Ta ścieżka
    # również jest nieufna: error_verdict i przypisanie mogą same zawieść.
    try:
        if _ifw is not None:
            if _fw_policy is None:
                _fw_policy = _ifw.FirewallPolicy(
                    r6_limit_min=float(getattr(C, "BAG_TIME_HARD_MAX_MIN", 35.0)),
                    sla_limit_min=float(getattr(C, "BAG_TIME_HARD_MAX_MIN", 35.0)),
                    r27_strict_limit_min=float(getattr(C, "LATE_PICKUP_HARD_MAX_MIN", 5.0)),
                    r27_overload_limit_min=float(
                        getattr(C, "OBJ_COMMITTED_PICKUP_TOL_LOOSE_MIN", 10.0)),
                    overload_threshold=float(
                        getattr(C, "OBJ_COMMITTED_PICKUP_LOAD_THRESHOLD", 4.5)),
                    package_address_ids=(), package_thermal_exempt=False,
                    sla_anchor_kind="now", always_propose_enabled=False,
                    od07_firewall_exempt_truth_enabled=(
                        _a360_d1_od07_firewall_enabled()),
                )
            result.rule_verdict = _ifw.error_verdict(result, _fw_policy, _fw_exc)
            typed_fallback_ok = True
        else:
            typed_fallback_ok = False
    except Exception as fallback_exc:
        _fw_exc = fallback_exc
        typed_fallback_ok = False

    try:
        log.warning(
            "INVARIANT_FIREWALL_UNKNOWN order=%s error=%s",
            getattr(result, "order_id", ""), type(_fw_exc).__name__)
    except Exception:
        pass

    if typed_fallback_ok:
        return
    result.rule_verdict = _final_rule_unknown_dict(
        result, f"FINAL_FALLBACK:{type(_fw_exc).__name__}")


def assess_order(
    order_event: dict,
    fleet_snapshot: Dict[str, Any],
    restaurant_meta: Optional[dict] = None,
    now: Optional[datetime] = None,
    *,
    pending_queue: Optional[list] = None,
    demand_context: Optional[dict] = None,
    _bypass_early_bird: bool = False,
) -> PipelineResult:
    """Public assess_order wrapper — calls _assess_order_impl + observability hook.

    TASK 3 (2026-05-04): per-candidate logging gdy OBSERVABILITY_PER_CANDIDATE_ENABLED.
    Defensive: hook NIGDY raises (try/except). Zero overhead gdy flag false.
    """
    # Z-P0-01: jeden zegar dla calej decyzji ORAZ finalnego firewalla. Wczesniej
    # impl tworzyl wlasne `now` gdy caller podal None, a hooki po impl dostawaly
    # nadal None. Jawne zwiazanie tutaj jest semantycznie rownowazne dla impl,
    # lecz czyni pomiar deterministycznym i replayowalnym.
    decision_now = now if now is not None else datetime.now(timezone.utc)
    if decision_now.tzinfo is None:
        decision_now = decision_now.replace(tzinfo=timezone.utc)

    # Z-P1-03: snapshot raz na samodzielne assess albo dziedziczony raz z ticka.
    # Default OFF oznacza brak collectora i brak wiazania workerow/OSRM/solvera.
    _stage_timing_on = _ST.observation_enabled(
        C.flag, C.ENABLE_STAGE_TIMING_OBSERVATION)
    _timing = _ST.DecisionTrace() if _stage_timing_on else None
    _assess_started = _timing.now_ns() if _timing is not None else None

    # K08 refaktoru (ADR-R02): efekty uboczne decyzji (shadow-jsonle, zapis stanu
    # loadgov, alert Telegram) buforowane i wykonywane PO impl — flush w finally
    # (przy wyjątku zbuforowane efekty wykonują się jak w legacy, gdzie zapis
    # zdążył się wydarzyć przed crashem). Gate w begin(); OFF = bajt-parytet 1:1.
    _eb_on = _EB.begin()
    _impl_started = _timing.now_ns() if _timing is not None else None
    try:
        result = _assess_order_impl(
            order_event, fleet_snapshot, restaurant_meta, decision_now,
            pending_queue=pending_queue, demand_context=demand_context,
            _bypass_early_bird=_bypass_early_bird,
            _timing_trace=_timing,
        )
    finally:
        if _timing is not None:
            _timing.record_since("impl_wall_ms", _impl_started)
        if _eb_on:
            _effects_started = (
                _timing.now_ns() if _timing is not None else None)
            try:
                _EB.flush()
            finally:
                if _timing is not None:
                    _timing.record_since(
                        "effects_flush_wall_ms", _effects_started)
    _post_hooks_started = (
        _timing.now_ns() if _timing is not None else None)
    # MP-#13 (2026-05-08): L3 — snapshot OSRM degraded state at assess time.
    # Caller (shadow_dispatcher serializer + telegram_approver format_proposal) reads.
    # Defensive: NIGDY raise (osrm_client import-fail unlikely ale fallback safe).
    try:
        from dispatch_v2 import osrm_client as _oc
        result.degraded_osrm = bool(_oc.is_degraded())
        result.osrm_cache_age_s = _oc.cache_age_s()
        result.osrm_degraded_since_ts = _oc.degraded_since_ts()
    except Exception:
        pass  # MP-#13 defense-in-depth — leave defaults False/None
    # W0.2: bezpiecznik fabrykacji ETA (shadow-first compute-always; aktywny za flagą)
    _eta_fabrication_check(result, order_event, decision_now)
    # W1/T2.4: stempel would-be-mode na rekordzie decyzji (shadow, flaga OFF default).
    # Odczyt stanu obserwatora (mode_observer) — NIE krokuje FSM. Fail-soft.
    if C.flag("ENABLE_MODE_LAYER_SHADOW", False):
        try:
            from dispatch_v2.tools.mode_observer import read_current_mode
            result.mode, result.mode_reason = read_current_mode()
        except Exception:
            pass  # obserwowalność NIGDY nie wywróci assess flow
    try:
        from dispatch_v2.observability.candidate_logger import get_logger, serialize_candidate
        logger = get_logger()
        if logger._flag_check():
            cands_full = []
            if result.best is not None:
                cands_full.append(serialize_candidate(result.best))
            for c in (result.candidates or []):
                if result.best is not None and c is result.best:
                    continue
                cands_full.append(serialize_candidate(c))
            logger.log_evaluation(
                source="dispatch_pipeline.assess_order",
                order_id=str(result.order_id),
                context={
                    "restaurant": result.restaurant,
                    "delivery_address": result.delivery_address,
                    "pool_total_count": result.pool_total_count,
                    "pool_feasible_count": result.pool_feasible_count,
                },
                candidates_evaluated=cands_full,
                decision={
                    "verdict": result.verdict,
                    "reason": result.reason,
                    "best_candidate_cid": (getattr(result.best, "courier_id", None) if result.best else None),
                    "best_score": (getattr(result.best, "score", None) if result.best else None),
                },
                fleet_size_total=len(fleet_snapshot),
            )
    except Exception:
        pass  # Defensive — observability NIGDY nie crashes assess flow

    # Z-P0-01 FAZA A -- JEDYNY finalny hook po wszystkich bramkach, rankingu,
    # best_effort i ALWAYS_PROPOSE. Dodatkowa warstwa last-resort chroni decyzję
    # także przed regresją samego helpera/fallbacku instrumentacji.
    try:
        _attach_final_rule_verdict(result, order_event, fleet_snapshot, decision_now)
    except Exception as _fw_outer_exc:
        try:
            result.rule_verdict = _final_rule_unknown_dict(
                result, f"OUTER_FALLBACK:{type(_fw_outer_exc).__name__}")
        except Exception:
            pass
    # ROUTE-ORDER WOULD-APPLY (2026-07-20): dopiero PO finalnym firewallu —
    # telemetry-only byte-parity decyzji przy OFF. Manual sequence jest czytana
    # przez wspólny walidator plan_recheck; auto-serializer dostarcza pola A+B
    # do kanonicznego logs/shadow_decisions.jsonl.
    try:
        _route_shadow_candidates = list(result.candidates or [])
        if result.best is not None and not any(
                c is result.best for c in _route_shadow_candidates):
            _route_shadow_candidates.append(result.best)
        _route_order_override_shadow_pass(_route_shadow_candidates, decision_now)
    except Exception as _route_shadow_exc:
        log.warning(
            "ROUTE_ORDER_SHADOW outer fail-soft: %s: %s",
            type(_route_shadow_exc).__name__, str(_route_shadow_exc)[:160],
        )
    if _timing is not None:
        _timing.record_since("post_hooks_wall_ms", _post_hooks_started)
        _timing.record_since("assess_wall_ms", _assess_started)
    # Attach dopiero PO wszystkich bramkach/firewallu. candidate_timing trafia do
    # metrics po selekcji, więc nie może zmienić rankingu ani werdyktu.
    if _timing is not None:
        try:
            _timing.attach(result)
        except Exception as _timing_exc:  # obserwacja nigdy nie zmienia decyzji
            try:
                result.stage_timing = None
            except Exception:
                pass
            log.warning(
                "stage_timing attach fail-soft: %s: %s",
                type(_timing_exc).__name__, _timing_exc,
            )
    return result


def _assess_order_impl(
    order_event: dict,
    fleet_snapshot: Dict[str, Any],
    restaurant_meta: Optional[dict] = None,
    now: Optional[datetime] = None,
    *,
    # F2.2 C7 skeleton (2026-04-18): additive kwargs for wave_scoring/commitment wire-up.
    # Existing 2 callers (shadow_dispatcher, test_decision_engine_f21) pass positional
    # args only → these kwargs stay None, zero behavior change.
    # When ENABLE_PENDING_QUEUE_VIEW=True AND kwargs=None → auto-fetch providers.
    pending_queue: Optional[list] = None,
    demand_context: Optional[dict] = None,
    # EARLYBIRD-01 (2026-06-14): True → pomiń early_bird short-circuit (kontrfaktyk shadow).
    _bypass_early_bird: bool = False,
    # Z-P1-03: wewnętrzny collector observation-only; nie jest częścią WorldState.
    _timing_trace: Optional[_ST.DecisionTrace] = None,
) -> PipelineResult:
    _prepare_started = _timing_trace.now_ns() if _timing_trace is not None else None
    _prepare_closed = False

    def _timing_close_prepare() -> None:
        nonlocal _prepare_closed
        if _timing_trace is not None and not _prepare_closed:
            _timing_trace.record_since("prepare_wall_ms", _prepare_started)
            _prepare_closed = True

    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    # F2.2 C7: Auto-fetch providers when flag enabled and caller passed None
    from dispatch_v2.common import ENABLE_PENDING_QUEUE_VIEW
    if ENABLE_PENDING_QUEUE_VIEW:
        if pending_queue is None:
            try:
                from dispatch_v2.pending_queue_provider import get_pending_queue
                pending_queue = get_pending_queue()
            except Exception:
                pending_queue = []
        if demand_context is None:
            try:
                from dispatch_v2.pending_queue_provider import compute_demand_context
                demand_context = compute_demand_context(now)
            except Exception:
                demand_context = {}
    # pending_queue and demand_context are available for downstream wave_scoring
    # wire-up in future C7 iteration. Current flow below unchanged.

    order_id = str(order_event.get("order_id") or "")
    # Jedyny snapshot obu flag na decyzję. Konflikt fail-closed: nowy model nie
    # wpływa na wynik, stary tor zachowuje dotychczasową semantykę i emitujemy ALERT.
    _explicit_unknown_requested = C.decision_flag(
        "ENABLE_EXPLICIT_UNKNOWN_POSITION_MODEL")
    _legacy_nogps_requested = C.decision_flag(
        "ENABLE_NO_GPS_NEUTRAL_SCORE_DIST")
    _position_flag_conflict = bool(
        _explicit_unknown_requested and _legacy_nogps_requested)
    _explicit_unknown_effective = bool(
        _explicit_unknown_requested and not _position_flag_conflict)
    if _position_flag_conflict:
        log.error(
            "ALERT EXPLICIT_UNKNOWN_FLAG_CONFLICT order=%s; legacy retained",
            order_id,
        )
    restaurant = order_event.get("restaurant")
    delivery_address = order_event.get("delivery_address")

    # === SP-B2-LOADGOV (2026-06-11): chwilowy load floty + EWMA 15 min ===
    # Telemetria ZAWSZE (loadgov_* per kandydat, LOCATION A+B); polityka
    # (kara bag≥3 przy ewma>2,7 + alert >3,5) za 🛑 ENABLE_FLEET_LOAD_GOVERNOR.
    loadgov_now, loadgov_ewma, loadgov_orders, loadgov_couriers = (
        _loadgov_compute(fleet_snapshot, now))
    # world_record v1: nagraj obliczoną krotkę loadgov (zależy od orders_state.json
    # + in-proc EWMA — nieodtwarzalne w świeżym procesie replayu). Hook no-op poza
    # oknem capture; NIGDY nie dotyka decyzji (fail-soft w note_decision_input).
    try:
        from dispatch_v2 import world_record as _wr_note
        _wr_note.note_decision_input(
            "loadgov", [loadgov_now, loadgov_ewma, loadgov_orders, loadgov_couriers])
    except Exception:
        pass
    # N5 krok 2 (2026-06-17): tolerancja punktualności committed load-aware →
    # route_simulator (czyta ją w _ortools_plan). loadgov_ewma ≥ próg 4,5 (niedobór,
    # dni jak 16.05) → loose 10 min; inaczej strict 5. Gated; flaga OFF → bound się
    # nie buduje (no-op). loadgov_ewma None → strict (bezpiecznie).
    try:
        if C.decision_flag("ENABLE_OBJ_COMMITTED_PICKUP_PENALTY"):
            from dispatch_v2 import route_simulator_v2 as _rsim_n5
            _n5_thr = float(getattr(C, "OBJ_COMMITTED_PICKUP_LOAD_THRESHOLD", 4.5))
            _n5_tol = (
                float(getattr(C, "OBJ_COMMITTED_PICKUP_TOL_LOOSE_MIN", 10.0))
                if (loadgov_ewma is not None and loadgov_ewma >= _n5_thr)
                else float(getattr(C, "OBJ_COMMITTED_PICKUP_TOL_STRICT_MIN", 5.0)))
            _rsim_n5.set_committed_pickup_tolerance(_n5_tol)
    except Exception:
        pass  # best-effort; bound ma strict default gdy nie ustawione
    if C.decision_flag("ENABLE_FLEET_LOAD_GOVERNOR"):
        # Hysteresis DZIELONA przez plik (nie pamięć procesu) → JEDEN alert na epizod
        # przeciążenia, niezależnie ile procesów (czasowka co minutę!) liczy load.
        _armed_disk, _last_ts = _loadgov_load_alert_state()
        _lg_emit, _new_armed = _loadgov_alert_transition(loadgov_ewma, _armed_disk)
        # Cooldown: nie alarmuj częściej niż co LOADGOV_ALERT_COOLDOWN_MIN (oscylacja
        # wokół progu nie spamuje). Belt-and-suspenders ponad dzieloną hysteresis.
        _cooldown = float(getattr(C, "LOADGOV_ALERT_COOLDOWN_MIN", 30.0))
        if _lg_emit and _last_ts is not None and (now - _last_ts) < timedelta(minutes=_cooldown):
            _lg_emit = False
        if loadgov_ewma is not None and (_new_armed != _armed_disk or _lg_emit):
            # K08: zapis stanu alertu PO decyzji (divert); OFF/awaria → wprost jak dotąd
            if not _EB.divert(_loadgov_save_alert_state, _new_armed, now if _lg_emit else _last_ts):
                _loadgov_save_alert_state(_new_armed, now if _lg_emit else _last_ts)
        _LOADGOV_STATE["alert_armed"] = _new_armed  # mirror do telemetrii procesu
        if _lg_emit:
            try:
                from dispatch_v2.telegram_utils import send_admin_alert as _lg_alert
                _lg_msg = (
                    "🛑 Flota przeciążona — tryb defensywny\n"
                    f"Na każdego aktywnego kuriera przypada średnio {loadgov_ewma:.1f} "
                    f"zleceń ({loadgov_orders} aktywnych zleceń / {loadgov_couriers} kurierów).\n"
                    "Co robię: ostrożniej dokładam do pełnych toreb (kara za worki 3+), "
                    "propozycje nadal wychodzą normalnie.\n"
                    "Co Ty masz zrobić: dzwoń po posiłki — każdy dodatkowy kurier "
                    "realnie zbija opóźnienia (próg alarmu: 3,5 zlec./kuriera, "
                    "odwołanie poniżej 3,0)."
                )
                # K08: wysyłka PO decyzji (divert); OFF/awaria bufora → wprost jak dotąd
                if not _EB.divert(_lg_alert, _lg_msg):
                    _lg_alert(_lg_msg)
            except Exception:
                pass  # Telegram unreachable nie blokuje dispatchu

    # K10 refaktoru: geokod-defense (L2) wyniesiony do core.gates.geocode_defense
    # — treść, log i kształt SKIP-wyniku 1:1 (historia firmowego konta w docstringu
    # bramki). czasowka_scheduler emit'uje dedicated Telegram alert PRZED tym
    # wywołaniem (visible operator).
    _gate_res = _gates.geocode_defense(
        order_event, order_id=order_id, restaurant=restaurant,
        delivery_address=delivery_address,
    )
    if _gate_res is not None:
        _timing_close_prepare()
        return _gate_res

    # geocode_defense gwarantuje nie-None/nie-sentinel (mypy nie widzi narrowingu
    # przez granicę funkcji — stąd ignore; semantyka 1:1 z wersją inline).
    pickup_coords = tuple(order_event.get("pickup_coords"))  # type: ignore[arg-type]
    delivery_coords = _osrm_guard_sentinel_coords(order_event.get("delivery_coords"))

    # V3.19f: czas_kuriera_warsaw first-choice pod flagą (panel HH:MM commitment).
    # Fallback do pickup_at_warsaw (pre-V3.19f behavior) gdy flaga False albo brak.
    pickup_at_raw = None
    _ck_used = False
    if ENABLE_CZAS_KURIERA_PROPAGATION:
        _ck_warsaw = order_event.get("czas_kuriera_warsaw")
        if _ck_warsaw:
            pickup_at_raw = _ck_warsaw
            _ck_used = True
    if pickup_at_raw is None:
        pickup_at_raw = order_event.get("pickup_at_warsaw") or order_event.get("pickup_at")
    pickup_at = parse_panel_timestamp(pickup_at_raw) if pickup_at_raw else None
    if _ck_used and pickup_at is not None:
        log.debug(
            f"V3.19f: pickup_ready_at=czas_kuriera={pickup_at_raw} "
            f"(vs pickup_at_warsaw={order_event.get('pickup_at_warsaw')}) "
            f"oid={order_id}"
        )

    # K10 refaktoru: early-bird (+ kontrfaktyk EARLYBIRD-01, głębokość 1) wyniesiony
    # do core.gates.early_bird — próg z RAW pickup_at_warsaw (fix 2026-05-07),
    # treść/reason/kolejność efektów 1:1; helpery (_early_bird_threshold_min,
    # _earlybird_t30_shadow_enabled, _append_earlybird_t30_shadow) zostają TU
    # (konsument zewn.: shadow_dispatcher) — bramka woła je przez moduł.
    _gate_res = _gates.early_bird(
        order_event, fleet_snapshot, restaurant_meta, now,
        pickup_at=pickup_at, order_id=order_id, restaurant=restaurant,
        delivery_address=delivery_address, pending_queue=pending_queue,
        demand_context=demand_context, bypass=_bypass_early_bird,
    )
    if _gate_res is not None:
        _timing_close_prepare()
        return _gate_res

    pickup_ready_at = get_pickup_ready_at(restaurant, pickup_at, now, restaurant_meta)

    new_order = OrderSim(
        order_id=order_id,
        pickup_coords=pickup_coords,
        delivery_coords=delivery_coords,
        status="assigned",
        pickup_ready_at=pickup_ready_at,
    )
    # R-PACZKI-FLEX (2026-05-20): patrz _bag_dict_to_ordersim site dla rationale.
    new_order.address_id = order_event.get("address_id")
    new_order.order_type = order_event.get("order_type")
    new_order.created_at_utc = order_event.get("created_at_utc") or order_event.get("created_at")

    # Traffic-aware fallback speed dla estymat ETA (zgodne z P0.5 common.py)
    fleet_speed_kmh = get_fallback_speed_kmh(now)

    candidates: List[Candidate] = []
    new_rest_norm = (restaurant or "").strip().lower()

    # V3.18 (2026-04-19): FleetContext once per event dla scoring overload penalty.
    # Flag ENABLE_UNIFIED_BAG_STATE=False → fleet_context=None, scoring ignoruje kwarg.
    fleet_context: Optional[FleetContext] = None
    if C.ENABLE_UNIFIED_BAG_STATE:
        try:
            fleet_context = _build_fleet_context_from_snapshot(fleet_snapshot, now.astimezone(WARSAW))
        except Exception as e:
            log.warning(f"V3.18 fleet_context build failed ({e}), falling back to None")
            fleet_context = None

    # V3.27 latency parallel (2026-04-25 wieczór): per-courier eval extracted do
    # nested function `_v327_eval_courier` z dostępem do enclosing scope (closure
    # captures now, order_event, fleet_speed_kmh, fleet_context, etc. without
    # explicit param passing). ThreadPoolExecutor.map ewaluuje 10 candidates parallel.
    # Thread-safety:
    #   - OR-Tools per-call `RoutingModel` lokalny (zero shared state) — verified
    #   - OSRM cache pod RLock w osrm_client._module_lock — verified V3.27
    #   - urllib HTTP per-call socket (no shared CookieJar dispatch-side) — safe
    #   - Python logging built-in lock — safe
    # Wall time goal: 250-400ms (vs sequential 500-2000ms, baseline 100-150ms pre-flip).
    # K11 refaktoru: wrapper TLS + inner (~2147 l.) przeniesione 1:1 do
    # core/candidates.py (eval_courier/eval_courier_inner + EvalContext).
    # Lokalny shim _v327_eval_courier definiowany NIŻEJ (po _k07_prefetched_ck
    # — closure czytała tę nazwę late-bound, ctx musi ją dostać policzoną).

    # V3.27 latency parallel: ThreadPoolExecutor map. 10 workers (lub mniej gdy
    # fleet < 10). Lambda unpacks (cid, cs) tuple z fleet_snapshot.items().
    # Single-courier fallback do sequential dla edge case (np. fleet=1).
    #
    # V3.28 Fix 1 (incident 03.05.2026): per-courier defense-in-depth.
    # Pre-fix: pool.map raise propagates → assess_order raise → event status=failed
    # → ZERO propose dla całego order. Jeden zły kurier blokował wszystkich
    # (production 470208/209/210 — Lekcja #66 amplifier). Post-fix: try/except
    # per courier, failed kurier logged + skipped, pozostali evaluated.
    _v328_failed_couriers: List[str] = []  # cid list dla telemetrii post-pool
    # L2.2: rozróżnienie przyczyn w catch-allu (koniec "wszystko wygląda tak samo"):
    # data_poison (fail-loud strażnik coords, most K5) vs real_bug (nieoczekiwany
    # wyjątek). infeasible = legalny brak → result None, NIE wyjątek.
    _v328_fail_causes: Dict[str, str] = {}

    def _v328_eval_safe(kv):
        """Wrap _v327_eval_courier z try/except — single courier crash NIE blokuje pool."""
        cid, cs = kv
        try:
            return ('ok', cid, _v327_eval_courier(cid, cs))
        except Exception as _e:
            _cause = _v328_classify_fail_cause(_e)
            log.error(
                f"V328_CP_SOLVER_FAIL_PER_COURIER cid={cid} order={order_id} "
                f"cause={_cause} exc={type(_e).__name__}: {str(_e)[:200]}",
                exc_info=True,
            )
            _v328_fail_causes[str(cid)] = _cause
            return ('fail', cid, _e)

    # K07 refaktor (2026-07-06): pre-proposal recheck czas_kuriera RAZ na
    # decyzję, PRZED pulą (unia worków floty = ten sam zbiór, który dziś
    # fetchują kandydaci; None = flaga OFF/awaria → pętla idzie ścieżką
    # legacy 1:1). Nazwa czytana w closure _v327_eval_courier_inner.
    _timing_close_prepare()
    _pre_recheck_started = (
        _timing_trace.now_ns() if _timing_trace is not None else None)
    _k07_prefetched_ck = _k07_prefetch_fresh_ck(fleet_snapshot, now)
    if _timing_trace is not None:
        _timing_trace.record_since("pre_recheck_wall_ms", _pre_recheck_started)
    _fanout_setup_started = (
        _timing_trace.now_ns() if _timing_trace is not None else None)
    # world_record v1: nagraj wynik prefetchu czas_kuriera (żywy fetch HTTP panelu
    # — nieodtwarzalny offline). Hook no-op poza oknem capture; fail-soft.
    try:
        from dispatch_v2 import world_record as _wr_note
        _wr_note.note_decision_input("k07", _k07_prefetched_ck)
    except Exception:
        pass

    # Z-P0-04: jeden spojny snapshot wersji PRZED uruchomieniem puli kandydatow.
    # Token trafia przez Candidate.metrics do decision_record/pending proposal i
    # jest warunkiem pozniejszego event-time save w panel_watcher. Rekordy
    # invalidated tez sa istotne (save_plan moze je reaktywowac); brak wpisu=0.
    # Awaria odczytu daje None, nigdy bezwarunkowy expected_version=None u writera.
    _plan_versions_snapshot = None
    try:
        from dispatch_v2 import plan_manager as _pm_cas
        _raw_plan_snapshot = _pm_cas.load_plans()
        _plan_versions_snapshot = {}
        for _cas_cid, _cas_plan in _raw_plan_snapshot.items():
            _cas_v = (_cas_plan or {}).get("plan_version", 0) \
                if isinstance(_cas_plan, dict) else None
            _plan_versions_snapshot[str(_cas_cid)] = (
                int(_cas_v)
                if isinstance(_cas_v, int) and not isinstance(_cas_v, bool)
                else None
            )
    except Exception as _cas_e:
        log.warning(
            "PLAN_CAS_SNAPSHOT_FAIL order=%s exc=%s: %s",
            order_id, type(_cas_e).__name__, _cas_e,
        )

    # K11 refaktoru: kontekst oceny = dokładnie wartości czytane dawniej z closure;
    # budowany tu (wartości finalne, wspólne dla całej puli). Delegacja 1:1.
    _eval_ctx = _candidates.EvalContext(
        now=now, order_event=order_event, order_id=order_id, restaurant=restaurant,
        delivery_address=delivery_address, pickup_coords=pickup_coords,
        delivery_coords=delivery_coords, pickup_at=pickup_at,
        pickup_ready_at=pickup_ready_at, new_order=new_order,
        new_rest_norm=new_rest_norm, fleet_speed_kmh=fleet_speed_kmh,
        fleet_context=fleet_context, k07_prefetched_ck=_k07_prefetched_ck,
        loadgov_now=loadgov_now, loadgov_ewma=loadgov_ewma,
        loadgov_orders=loadgov_orders, loadgov_couriers=loadgov_couriers,
        plan_versions=_plan_versions_snapshot,
        timing_trace=_timing_trace,
        position_model_mode=("explicit" if _explicit_unknown_effective else "legacy"),
        position_model_shadow=True,
    )

    def _v327_eval_courier(cid, cs):
        # K11: delegacja do core.candidates (wrapper TLS + inner, treść 1:1).
        return _candidates.eval_courier(_eval_ctx, cid, cs)

    from concurrent.futures import ThreadPoolExecutor as _V327_TPE
    _v327_max_workers = max(1, min(10, len(fleet_snapshot)))
    if _timing_trace is not None:
        _timing_trace.record_since("fanout_setup_wall_ms", _fanout_setup_started)
    _fanout_started = _timing_trace.now_ns() if _timing_trace is not None else None
    if _v327_max_workers > 1:
        with _V327_TPE(max_workers=_v327_max_workers, thread_name_prefix="dispatch_v327") as _v327_pool:
            for _tag, _cid, _result in _v327_pool.map(_v328_eval_safe, list(fleet_snapshot.items())):
                if _tag == 'fail':
                    _v328_failed_couriers.append(str(_cid))
                    continue
                if _result is not None:
                    candidates.append(_result)
    else:
        for _v327_cid, _v327_cs in fleet_snapshot.items():
            _tag, _cid, _result = _v328_eval_safe((_v327_cid, _v327_cs))
            if _tag == 'fail':
                _v328_failed_couriers.append(str(_cid))
                continue
            if _result is not None:
                candidates.append(_result)

    if _timing_trace is not None:
        _timing_trace.record_since("fanout_wall_ms", _fanout_started)
    _post_pool_started = _timing_trace.now_ns() if _timing_trace is not None else None

    # V3.28 Fix 1 telemetria post-pool fail rate (warning gdy >=1 fail, dla audit trail)
    _v328_fail_ratio = 0.0
    if _v328_failed_couriers:
        _v328_fail_ratio = len(_v328_failed_couriers) / max(1, len(fleet_snapshot))
        # L2.2: rozbicie per przyczyna + zbiorczy operator-alert na data-poison
        # (za flagą ENABLE_V328_POISON_ALERT, default OFF — inert).
        _v328_poison_cids = [c for c, k in _v328_fail_causes.items() if k == "data_poison"]
        _v328_bug_cids = [c for c, k in _v328_fail_causes.items() if k == "real_bug"]
        log.warning(
            f"V328_POOL_PARTIAL_FAIL order={order_id} "
            f"failed={len(_v328_failed_couriers)}/{len(fleet_snapshot)} "
            f"({_v328_fail_ratio:.0%}) failed_cids={_v328_failed_couriers[:10]} "
            f"data_poison={len(_v328_poison_cids)} real_bug={len(_v328_bug_cids)}"
        )
        try:
            _v328_maybe_poison_alert(order_id, _v328_poison_cids)
        except Exception as _pa_exc:  # defensywnie: telemetria nie psuje decyzji
            log.warning(f"V328_POISON_ALERT agregacja pominięta: {_pa_exc!r}")

    # V3.28 Fix 6 (incident 03.05.2026): mass fail fallback heuristic.
    # Gdy >=V328_MASS_FAIL_RATIO_THRESHOLD (default 0.5) kurierów crash w pool →
    # system w degraded state. Trigger simple proximity+tier heuristic na ALL
    # couriers (heuristic NIE używa OR-Tools więc nie crashuje na out-of-domain).
    # Inject fallback Candidate (verdict=MAYBE, plan=None, fallback_strategy
    # marked) — downstream sort by score wybierze best (fallback vs partial OR-Tools).
    if (
        C.ENABLE_V328_MASS_FAIL_FALLBACK
        and _v328_failed_couriers
        and _v328_fail_ratio >= C.V328_MASS_FAIL_RATIO_THRESHOLD
    ):
        log.critical(
            f"V328_OR_TOOLS_MASS_FAIL order={order_id} "
            f"ratio={_v328_fail_ratio:.0%} ({len(_v328_failed_couriers)}/{len(fleet_snapshot)}) "
            f"threshold={C.V328_MASS_FAIL_RATIO_THRESHOLD:.0%} → trigger heuristic fallback"
        )
        try:
            _v328_heuristic_results = []
            # Safety guard (#474808-style 2026-05-20): heuristic NIE używa
            # feasibility_v2 więc nie egzekwuje D3 sanity cap (MAX_BAG_SANITY_CAP=8).
            # Bez tego filtra heuristic proponuje kuriera z bag-at-cap który normalną
            # ścieżką byłby R3 hard-reject. Diagnoza: Dariusz cid=509 bag=8 wybrany
            # jako WYBRANY mimo bag_full reject path w OR-Tools.
            # SCALE-01: bag-cap z flags.json (hot, multi-city), fallback common =8.
            _v328_bag_cap = int(C.load_flags().get("MAX_BAG_SANITY_CAP", C.MAX_BAG_SANITY_CAP))
            # Z-11 (audyt 2026-06-10): bramka grafikowa obok bag-cap. Hot-reload
            # kill-switch flags.json, env default ON (common).
            _v328_shift_guard_on = C.flag(
                "ENABLE_V328_HEURISTIC_SHIFT_END_GUARD",
                default=bool(getattr(C, "ENABLE_V328_HEURISTIC_SHIFT_END_GUARD", True)))
            for _h_cid, _h_cs in fleet_snapshot.items():
                try:
                    _h_bag = getattr(_h_cs, "bag", None) or []
                    if len(_h_bag) >= _v328_bag_cap:
                        log.warning(
                            f"V328_HEURISTIC_SKIP_BAG_AT_CAP cid={_h_cid} "
                            f"bag={len(_h_bag)}>={_v328_bag_cap}"
                        )
                        continue
                    if _v328_shift_guard_on and _v328_heuristic_post_shift_skip(
                            _h_cs, order_event, now, fleet_speed_kmh):
                        log.warning(
                            f"V328_HEURISTIC_SKIP_POST_SHIFT cid={_h_cid} "
                            f"shift_end={getattr(_h_cs, 'shift_end', None)} "
                            f"(Z-11: nie zdąży przed końcem zmiany)"
                        )
                        continue
                    _h_score = _v328_simple_heuristic_score(_h_cid, _h_cs, order_event)
                    _v328_heuristic_results.append((_h_score, _h_cid, _h_cs))
                except Exception as _h_e:
                    log.warning(
                        f"V328_HEURISTIC_FALLBACK_PER_CID_FAIL cid={_h_cid}: "
                        f"{type(_h_e).__name__}: {str(_h_e)[:120]}"
                    )
            if _v328_heuristic_results:
                _v328_heuristic_results.sort(reverse=True, key=lambda x: x[0])
                _h_top_score, _h_top_cid, _h_top_cs = _v328_heuristic_results[0]
                if _h_top_score > -1000.0:
                    log.warning(
                        f"V328_HEURISTIC_WINNER order={order_id} "
                        f"cid={_h_top_cid} score={_h_top_score:.2f} "
                        f"name={getattr(_h_top_cs, 'name', None)!r}"
                    )
                    # Propaguj realne dane z CourierState do metrics — telegram
                    # display (_candidate_line_v2) używa r6_bag_size / bag_size_before
                    # i pos_source; bez tych pól rysuje 🟢 0 / ❔? / ETA — co maskuje
                    # rzeczywisty stan kuriera (Adrian incident 2026-05-20 Dariusz
                    # cid=509 bag=8 widziany jako 🟢 0). Pos_source z CS gdy realne
                    # GPS/proxy istnieje, fallback "heuristic_fallback" jako sygnał
                    # degraded mode.
                    _h_top_bag = getattr(_h_top_cs, "bag", None) or []
                    _h_top_pos_src = (
                        getattr(_h_top_cs, "pos_source", None)
                        or "heuristic_fallback"
                    )
                    _v328_fb_cand = Candidate(
                        courier_id=str(_h_top_cid),
                        name=getattr(_h_top_cs, "name", None),
                        score=float(_h_top_score),
                        feasibility_verdict="MAYBE",
                        feasibility_reason="v328_heuristic_fallback_post_mass_fail",
                        plan=None,  # no plan — heuristic skips OR-Tools
                        metrics={
                            "fallback_strategy": "v328_simple_heuristic_post_mass_fail",
                            "fallback_score": float(_h_top_score),
                            "mass_fail_ratio": _v328_fail_ratio,
                            "mass_fail_count": len(_v328_failed_couriers),
                            "fleet_size": len(fleet_snapshot),
                            "pos_source": _h_top_pos_src,
                            "bag_size_before": len(_h_top_bag),
                            "r6_bag_size": len(_h_top_bag),
                        },
                    )
                    candidates.append(_v328_fb_cand)
                else:
                    log.warning(
                        f"V328_HEURISTIC_NO_VIABLE_WINNER order={order_id} "
                        f"top_score={_h_top_score:.2f} (all couriers no GPS or pickup coords zero)"
                    )
        except Exception as _v328_fb_outer_e:
            log.error(
                f"V328_HEURISTIC_FALLBACK_OUTER_FAIL order={order_id}: "
                f"{type(_v328_fb_outer_e).__name__}: {_v328_fb_outer_e}",
                exc_info=True,
            )

    # F1.7 no_gps fallback: kurier z syntetycznym pos (centrum) dostaje
    # neutralne km/ETA. km_to_pickup = średnia floty (tylko z realnych pos),
    # travel_min = max(15, prep_remaining_min). Score liczony z centrum został,
    # bo i tak jest blisko mediany floty — nie faworyzuje, nie wyklucza.
    real_kms = [
        c.metrics.get("km_to_pickup")
        for c in candidates
        if c.metrics.get("pos_source") not in ("no_gps", None)
        and c.metrics.get("km_to_pickup") is not None
    ]
    fleet_avg_km = (sum(real_kms) / len(real_kms)) if real_kms else 5.0
    prep_remaining_min = 0.0
    if pickup_ready_at is not None:
        ready_utc = pickup_ready_at if pickup_ready_at.tzinfo else pickup_ready_at.replace(tzinfo=timezone.utc)
        prep_remaining_min = max(0.0, (ready_utc.astimezone(timezone.utc) - now).total_seconds() / 60.0)
    # v3 HOIST (recenzja delty): legacy F1.8e pre_shift_too_late PRZED passem
    # neutralizacji — donor filter mediany musi widzieć FINALNE werdykty
    # (zakotwiczony pre_shift ma road realny ⇒ jest donorem). Predykat 1:1
    # wyniesiony z pętli display niżej (tam już NIE powtarzany).
    _pre_shift_too_late_verdict_pass(candidates, prep_remaining_min, order_id)
    # NOGPS-NEUTRAL-SCORE (2026-07-19): PRZED nadpisaniem display niżej —
    # shadow czyta surowe km_to_pickup (road z centrum) i liczy neutralizację
    # score z MEDIANY realnych kotwic. Apply tylko za flagą (docstring pass).
    # Pętla display używa per-kandydat metrics["bonus_nogps_neutral_applied"]
    # (ustawia pass) — display podąża DOKŁADNIE za tym, co dostał score.
    _nogps_override_token = _NOGPS_LEGACY_DECISION_OVERRIDE.set(
        _legacy_nogps_requested)
    _nogps_neutral_km, _nogps_neutral_applied = _nogps_neutral_score_pass(
        candidates, order_id)
    _NOGPS_LEGACY_DECISION_OVERRIDE.reset(_nogps_override_token)
    no_gps_travel_min = max(15.0, prep_remaining_min)
    no_gps_eta_utc = now + timedelta(minutes=no_gps_travel_min)

    # L4 (2026-07-02, F1): jedno źródło floor odbioru. OFF → stare ścieżki niżej
    # bajt-w-bajt; ON → po per-pos blokach podnosimy eta_pickup do available_from
    # (=max(now,shift_start) z courier_resolver): repointuje pre_shift clamp na
    # kanoniczny available_from i DOMYKA lukę no_gps (audyt :5856 — no_gps pomijał
    # floor; on-shift = no-op bo available_from=now).
    _af_single_source_on = C.decision_flag("ENABLE_AVAILABLE_FROM_SINGLE_SOURCE")

    for c in candidates:
        ps = c.metrics.get("pos_source")
        if c.metrics.get("position_kind") == "UNKNOWN" \
                and _explicit_unknown_effective:
            c.metrics["km_to_pickup"] = None
            c.metrics["estimated_road_km"] = 6.5
            c.metrics["estimated_drive_min"] = 15.0
            c.metrics["travel_min"] = round(float(c.metrics.get("travel_min") or 15.0), 1)
            c.metrics["drive_min"] = 15.0
            c.metrics["eta_source"] = "unknown_profile"
            c.metrics["position_display_text"] = "pozycja nieznana · dojazd szac. 15 min"
        elif ps == "no_gps":
            # NOGPS-NEUTRAL-SCORE: gdy score zneutralizowany medianą (apply ON),
            # display km = TA SAMA mediana (koniec rozjazdu display≈peers vs
            # score=centrum). OFF / road realny (anchor) → legacy fleet_avg.
            if c.metrics.get("bonus_nogps_neutral_applied"):
                c.metrics["km_to_pickup"] = round(_nogps_neutral_km, 2)
            else:
                c.metrics["km_to_pickup"] = round(fleet_avg_km, 2)
            c.metrics["travel_min"] = round(no_gps_travel_min, 1)
            c.metrics["drive_min"] = round(no_gps_travel_min, 1)
            c.metrics["eta_pickup_utc"] = no_gps_eta_utc.isoformat()
            c.metrics["eta_drive_utc"] = no_gps_eta_utc.isoformat()
            c.metrics["eta_source"] = "no_gps_fallback"
            # SP-B2-ETAQ: travel_min nadpisany po pętli → przelicz kalibrację
            # (inaczej travel_min_cal zostałby z wartości sprzed fallbacku).
            if C.flag("ENABLE_ETA_QUANTILE_SHADOW", True):
                c.metrics["travel_min_cal"] = calib_maps.eta_quantile_calibrate(no_gps_travel_min, now)
        elif ps == "pre_shift":
            # Kurier zaczyna zmianę za N min — travel_min = N (czas oczekiwania).
            # Bez km (nieznane gdzie będzie). eta_pickup = start zmiany.
            shift_min = float(c.metrics.get("shift_start_min") or 0.0)
            shift_eta = (now + timedelta(minutes=shift_min)).isoformat()
            c.metrics["km_to_pickup"] = None
            c.metrics["travel_min"] = round(shift_min, 1)
            c.metrics["drive_min"] = round(shift_min, 1)
            c.metrics["eta_pickup_utc"] = shift_eta
            c.metrics["eta_drive_utc"] = shift_eta
            c.metrics["eta_source"] = "pre_shift"
            # SP-B2-ETAQ: jw. — travel_min nadpisany, przelicz kalibrację.
            if C.flag("ENABLE_ETA_QUANTILE_SHADOW", True):
                c.metrics["travel_min_cal"] = calib_maps.eta_quantile_calibrate(shift_min, now)
            # V3.24-A: eta_pickup_utc dla pre_shift = shift_start (clamp aktywny).
            c.metrics["v324a_pickup_clamped_to_shift_start"] = True
            # Legacy F1.8e pre_shift_too_late (hard exclude gdy nie zdąży na
            # pickup_ready; V3.24-A ON deleguje >60 min do warstwy B5) —
            # WYNIESIONY do _pre_shift_too_late_verdict_pass (v3 hoist, wołany
            # PRZED _nogps_neutral_score_pass): donor filter mediany musi
            # widzieć FINALNE werdykty. Tu ZERO powtórzenia predykatu (jedno
            # źródło prawdy); display tego brancha bez zmian.
        elif c.metrics.get("bonus_nogps_neutral_applied"):
            # NOGPS-NEUTRAL-SCORE: pozostałe syntetyki (pin / none /
            # post_shift_start_synthetic / working_override_synthetic) z road_km
            # z centrum — przy ON display km podąża za zneutralizowanym score
            # (jedna wartość; bez brancha score=mediana a display=km-z-centrum
            # byłby NOWYM rozjazdem). pre_shift ma jawnie km=None (wyżej, bez
            # zmian); travel_min/ETA tych syntetyków NIETYKANE (osobna oś).
            # OFF → elif martwy (bonus_nogps_neutral_applied=False) = bajt-parytet.
            c.metrics["km_to_pickup"] = round(_nogps_neutral_km, 2)

        # L4 floor (2026-07-02): eta_pickup ≥ available_from. Pure floor — tylko
        # podnosi, nigdy nie obniża (zero regresji przez zaniżenie). Dla pre_shift
        # available_from≈shift_start (≈ eta powyżej → floor ~0); no_gps on-shift
        # available_from=now (no-op); GPS/edge z przyszłym startem → realny floor.
        if _af_single_source_on:
            _l4_floor_candidate_eta(c)

    # Feasible (MAYBE) → rank by score.
    # R2 Bartek Gold Standard tie-breaker: przy równym score, preferuj
    # kandydata o niższej corridor deviation (bundle_level3_dev).
    # Brak dev (pusty bag / solo) → 999 (sortuje się na koniec przy tie).
    # K12 refaktoru: selekcja + tiering + best_effort + bramki werdyktu przeniesione
    # 1:1 do core/selection.py (select_and_emit; re-assert EMIT = L7.3 w lejku
    # _classify_and_set_auto_route, LIVE — werdykt C15: wymaganie planu JUZ wykonane).
    if _timing_trace is not None:
        _timing_trace.record_since("post_pool_wall_ms", _post_pool_started)
    _selection_started = _timing_trace.now_ns() if _timing_trace is not None else None
    _selection_ctx = _selection.SelectionContext(
            now=now, order_event=order_event, order_id=order_id, restaurant=restaurant,
            delivery_address=delivery_address, pickup_coords=pickup_coords,
            delivery_coords=delivery_coords, pickup_ready_at=pickup_ready_at,
            new_order=new_order, fleet_snapshot=fleet_snapshot,
            v328_fail_causes=_v328_fail_causes,
            plan_versions=_plan_versions_snapshot,
            position_model_mode=("explicit" if _explicit_unknown_effective else "legacy"),
        )
    # Kontrfaktyk idzie przez PRAWDZIWY selektor (feasibility→score→tiering→
    # buckets→best_effort→OBJM/R29→final gates), nigdy przez max(score).
    _selected = _select_with_position_model_shadow(
        _selection_ctx,
        candidates,
        explicit_effective=_explicit_unknown_effective,
        explicit_requested=_explicit_unknown_requested,
        flag_conflict=_position_flag_conflict,
    )
    if _timing_trace is not None:
        _timing_trace.record_since("selection_wall_ms", _selection_started)
    return _selected
