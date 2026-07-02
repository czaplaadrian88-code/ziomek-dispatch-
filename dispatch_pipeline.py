"""dispatch_pipeline - per-order assessment: feasibility → scoring → rank → verdict.

Input:  NEW_ORDER event dict + fleet snapshot + restaurant_meta.
Output: PipelineResult with ranked candidates and final verdict.

Verdicts:
    PROPOSE — best candidate is feasible, send to Telegram for approval
    KOORD   — early-bird (>=60 min ahead) OR R28 best_effort (no feasible, SLA compromise)
    SKIP    — no candidate with any plan (fleet empty / all fast-filter rejections).
              R29 says never hang; SKIP always alerts Adrian.
"""
import logging
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Tuple, Any

from dispatch_v2.route_simulator_v2 import OrderSim, RoutePlanV2, DWELL_PICKUP_MIN
from dispatch_v2.feasibility_v2 import check_feasibility_v2
from dispatch_v2 import scoring
from dispatch_v2 import common as C
from dispatch_v2 import calib_maps  # SP-B2 (2026-06-11): mapy ETA-quantile + prep-bias (shadow)
from dispatch_v2 import pln_objective  # SP-B2-PLN (2026-06-11): funkcja celu PLN (shadow)
from dispatch_v2 import panel_client  # V3.27.1 sesja 2: pre-proposal recheck (Blocker 2 Opcja A)
from dispatch_v2.common import (
    parse_panel_timestamp,
    WARSAW,
    HAVERSINE_ROAD_FACTOR_BIALYSTOK,
    get_fallback_speed_kmh,
    ENABLE_CZAS_KURIERA_PROPAGATION,
)
from dispatch_v2.osrm_client import haversine
from dispatch_v2.bag_state import build_courier_bag_state, CourierBagState
from dispatch_v2.fleet_context import build_fleet_context, FleetContext
from dispatch_v2.pipeline_geometry import _point_to_segment_km, _min_dist_to_route_km  # B6: czysta geometria wydzielona
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


def _v327_safe_fetch_czas_kuriera(oid: str, timeout: float = None) -> Tuple[Optional[str], Optional[str]]:
    """V3.27.1 sesja 3 fix Bug 1 — schema-correct via panel_client.normalize_order.

    Pre-fix (sesja 2 broken): zwracało raw HH:MM gdy `czas_kuriera_warsaw` klucz
    nie istniał w surowym response. State_machine sanity check FAIL bo hhmm=None.

    Post-fix: call `normalize_order(raw)` żeby dostać OBA pola (ISO Warsaw + HH:MM).

    Returns (czas_kuriera_warsaw_iso, czas_kuriera_hhmm) tuple. (None, None) gdy:
    - Fetch fail (timeout, connection, exception)
    - normalize_order returns None (status_id ∈ {7,8,9} delivered/cancelled/declined
      — order zmienił status w trakcie cycle, skip emit)
    - Order ma czas_kuriera missing/invalid (norm fields = None, propagate up)

    Caller (get_fresh_czas_kuriera_for_bag) skip emit gdy iso=None (zachowuje cached).
    """
    if timeout is None:
        timeout = C.V327_PRE_PROPOSAL_RECHECK_FETCH_TIMEOUT_SEC
    try:
        fresh = panel_client.fetch_order_details(oid, timeout=int(timeout))
        if fresh is None:
            return (None, None)
        # KEY FIX V3.27.1 sesja 3: normalize_order konwertuje raw HH:MM → ISO Warsaw
        # plus filtruje IGNORED_STATUSES (7=delivered, 8=cancelled, 9=declined).
        norm = panel_client.normalize_order(fresh)
        if norm is None:
            # Status ignored = order delivered/cancelled w trakcie cycle, skip
            return (None, None)
        return (norm.get("czas_kuriera_warsaw"), norm.get("czas_kuriera_hhmm"))
    except Exception as e:
        log.warning(f"V3.27.1 _v327_safe_fetch_czas_kuriera oid={oid} fail: {e}")
        return (None, None)


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
                                   now: datetime) -> None:
    """V3.27.1 sesja 3 fix Bug 1 — emit synth CZAS_KURIERA_UPDATED z OBIEMA polami.

    Pre-fix (sesja 2): payload `new_ck_hhmm=None` → state_machine sanity FAIL
    (`_verify_czas_kuriera_consistency` wymaga że strftime(parsed_iso, "%H:%M")==hhmm).

    Post-fix: caller (get_fresh_czas_kuriera_for_bag) przekazuje hhmm z
    `_v327_safe_fetch_czas_kuriera` tuple — sanity check OK.

    Side-effect: event_bus.emit + state_machine.update_from_event w background.
    Event_id: {oid}_CZAS_KURIERA_UPDATED_PRE_RECHECK_{epoch_ms} — unique per emit.
    """
    from dispatch_v2.event_bus import emit as _eb_emit
    from dispatch_v2.event_bus import emit_audit as _eb_emit_audit
    from dispatch_v2.state_machine import update_from_event as _sm_apply

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
    event = {
        "event_type": "CZAS_KURIERA_UPDATED",
        "order_id": oid,
        "courier_id": courier_id,
        "payload": payload,
    }
    try:
        _eb_emit_audit("CZAS_KURIERA_UPDATED",
                 order_id=oid, courier_id=courier_id or "",
                 payload=payload, event_id=event_id)
        _sm_apply(event)
        delta_str = f"Δ={delta_min:+.1f}min" if delta_min is not None else "Δ=null"
        log.info(f"V3.27.1 pre_proposal_recheck oid={oid} {old_ck_iso or 'null'}→{new_ck_iso} ({new_ck_hhmm}) {delta_str}")
    except Exception as e:
        log.warning(f"V3.27.1 _v327_emit_pre_recheck_event oid={oid} fail: {e}")


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
                    # V3.27.1 sesja 3 fix Bug 1: helper teraz returns Tuple (iso, hhmm)
                    fresh_iso, fresh_hhmm = future.result()
                except Exception as e:
                    log.warning(f"V3.27.1 pre_recheck future oid={oid} exc: {e}")
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
                                                   fresh_iso, fresh_hhmm, now)
                    results[oid] = fresh_iso

    return results


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
    _s = (getattr(c, "score", 0.0) or 0.0) if score is None else score
    adj = _s - _late_pickup_soft_penalty(c, free_min, coeff, cap)
    return (1 if tier == 2 else 0, bucket, -adj, orig_rank)


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

        def _m(c, k):
            v = (getattr(c, "metrics", None) or {}).get(k)
            return float(v) if isinstance(v, (int, float)) else None

        def _newbag(c):
            pod = getattr(getattr(c, "plan", None), "per_order_delivery_times", None) or {}
            v = pod.get(new_oid)
            if isinstance(v, (int, float)):
                return float(v)
            return _m(c, "sum_bag_time_min")

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


def _best_effort_objm_shadow(with_plan, live_best, new_oid, cap_min=40.0) -> None:
    """SHADOW (2026-06-23): co BY wybrała selekcja best_effort, gdyby PRIMARY był carry-inclusive
    objm_r6_breach_max_min (mirror _objm_lexr6_shadow._lex_qual) zamiast new-pickup-only
    r6_per_order_violations (ślepego na carry-ordery — case #482817). LOG-ONLY: pisze TYLKO
    live_best.metrics['best_effort_objm_*'] (prefix auto-serializowany w shadow_dispatcher),
    NIGDY nie mutuje with_plan/best/werdyktu. Faithful (sticky-aware) — liczy na DOKŁADNIE tych
    planach co realna selekcja. Defensywny (Lekcja #83: try/except, fail-open, zero raise).

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

        def _m(c, k):
            v = (getattr(c, "metrics", None) or {}).get(k)
            return float(v) if isinstance(v, (int, float)) else None

        def _newbag(c):
            pod = getattr(getattr(c, "plan", None), "per_order_delivery_times", None) or {}
            v = pod.get(new_oid)
            if isinstance(v, (int, float)):
                return float(v)
            return _m(c, "sum_bag_time_min")

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
        _esc_max = C.flag("BEST_EFFORT_ESC_TIER2_MAX_FREE_MIN",
                          getattr(C, "BEST_EFFORT_ESC_TIER2_MAX_FREE_MIN", 30.0))
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
        cand.score = cand.score + bonus
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
    feasible.sort(
        key=lambda c: (
            -c.score,
            c.metrics.get("bundle_level3_dev")
            if c.metrics.get("bundle_level3_dev") is not None
            else 999.0,
        )
    )
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
        def _objm(c, k):
            v = (getattr(c, "metrics", None) or {}).get(k)
            return float(v) if isinstance(v, (int, float)) else None

        _w_tb = (_late_pickup_tier(_w), _selection_bucket(_w))
        _grp = [c for c in feasible if (_late_pickup_tier(c), _selection_bucket(c)) == _w_tb]

        def _lex_qual(c):
            r6 = _objm(c, "objm_r6_breach_max_min")
            return (r6 if r6 is not None else 9e9,
                    _objm(c, "late_pickup_committed_max") or 0.0,
                    _objm(c, "new_pickup_late_min") or 0.0)

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

        def _kind(c):
            r = getattr(c, "feasibility_reason", "") or ""
            if r.startswith("sla_violation"):
                return "sla"
            if r.startswith("R6_per_order"):
                return "r6_new"
            if r.startswith("R6_picked_up_delta"):
                return "r6_carry_delta"
            return "other"

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
        import re as _re
        chosen = top[0]
        chosen_objm = _OL.objm(chosen, "objm_r6_breach_max_min")
        if chosen_objm is None or chosen_objm <= 0:
            return None  # chosen czysty → brak asymetrii bypassu, poza zakresem B2
        chosen_lex = _OL.lex_qual(chosen)

        def _kind(c):
            r = getattr(c, "feasibility_reason", "") or ""
            if r.startswith("sla_violation"):
                return "sla"
            if r.startswith("R6_per_order"):
                return "r6_new"
            if r.startswith("R6_picked_up_delta"):
                return "r6_carry_delta"
            return "other"

        def _newbag(c):
            # Nowy order bag-time (mirror _best_effort_objm_pick._newbag): plan per-order
            # → fallback sum_bag_time_min. Tier-3 cap dotyczy NOWEGO zlecenia.
            pod = getattr(getattr(c, "plan", None), "per_order_delivery_times", None) or {}
            v = pod.get(new_oid)
            if isinstance(v, (int, float)):
                return float(v)
            m = (getattr(c, "metrics", None) or {}).get("sum_bag_time_min")
            return float(m) if isinstance(m, (int, float)) else None

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
            c.score = (c.score or 0.0) + d
            m = getattr(c, "metrics", None)
            if isinstance(m, dict):
                m["a2_reliability_delta"] = round(d, 2)
    feasible.sort(key=lambda c: -(c.score or 0.0))
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
            c.score = (c.score or 0.0) + delta
            m["bonus_gps_age_discount"] = round(delta, 2)
            applied_any = True
    if applied_any:
        feasible.sort(key=lambda c: -(c.score or 0.0))
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
            cand.score = cand.score + adj
        m["v326_fleet_bag_avg"] = round(fleet_bag_avg, 2)
        m["v326_fleet_load_delta"] = round(delta, 2)
        m["v326_fleet_load_adjustment"] = round(adj, 2)
    feasible.sort(
        key=lambda c: (
            -c.score,
            c.metrics.get("bundle_level3_dev")
            if c.metrics.get("bundle_level3_dev") is not None
            else 999.0,
        )
    )
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
        pickup_coords = order_event.get("pickup_coords") or (0.0, 0.0)
        if not pickup_coords or pickup_coords[0] == 0.0:
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
        cand.score = cand.score + adjustment
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
    feasible.sort(
        key=lambda c: (
            -c.score,
            c.metrics.get("bundle_level3_dev")
            if c.metrics.get("bundle_level3_dev") is not None
            else 999.0,
        )
    )
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
    - kurs poza profilem → sentinel -1e9 (sort na koniec, kandydat zostaje
      w puli — ALWAYS-PROPOSE; mining H13: dni 0-7 = 16,8% breach).
    Po rampie (≥30 dostaw) lub flaga OFF → dotychczasowa logika niżej.

    Logic per candidate gdzie metrics.cs_tier_label == 'new' (post-rampa):
    - bag_size_before >= 2 → HARD SKIP (effective -inf score, sort to end)
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

    NEG_INF = -1e9
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
                    cand.score = NEG_INF
                    # Z-18 (higiena 2026-06-13): score=NEG_INF zostaje (sort/decyzja
                    # bez zmian), ale NIE wpisujemy magic-number do pola analitycznego
                    # v325_new_courier_penalty — sentinel -1e9 przeciekał do shadow
                    # (analityka) + reason breakdown (l.1119 "V3.25_new -1000000000").
                    # Powód hard-skipu = jawna etykieta v325_skipped_reason (auto-
                    # serializowana przez prefix "v325_"). penalty=None → reason
                    # breakdown `or 0` → 0 → odfiltrowany; decyzja identyczna.
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
            cand.score = NEG_INF
            # Z-18 (higiena 2026-06-13): patrz blok ramp-block wyżej — score=NEG_INF
            # decyduje, ale powód idzie jako jawna etykieta, nie -1e9 w polu penalty.
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

    # SP-B2-RAMPA SOLO-GUARD (replay 11.06, ALWAYS-PROPOSE): sentinel -1e9 nie
    # może wepchnąć decyzji w KOORD "wszyscy poniżej progu propozycji", gdy
    # zablokowany nowy był jedyną realną opcją (6-7 eskalacji/tydz. w replayu).
    # Gdy po blokadach ŻADEN feasible nie ma score >= MIN_PROPOSE_SCORE:
    # najlepszy zablokowany wraca na pre_block + SOLO_MALUS — mocno
    # zdemotowany, ale proposable; decyduje człowiek, nie cisza.
    if _ramp_blocked:
        _min_prop = _min_propose_score()  # SCALE-01: flags.json (hot) → common (=-100)
        _all_below = all(
            (not isinstance(c.score, (int, float))) or c.score < _min_prop
            for c in feasible
        )
        if _all_below:
            _best_blocked, _pre = max(_ramp_blocked, key=lambda t: t[1])
            _solo = float(getattr(C, "NEW_COURIER_RAMP_SOLO_MALUS", -60.0))
            _best_blocked.score = _pre + _solo
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

    # Re-sort feasible po score (descending) + tie-break corridor deviation
    feasible.sort(
        key=lambda c: (
            -c.score,
            c.metrics.get("bundle_level3_dev")
            if c.metrics.get("bundle_level3_dev") is not None
            else 999.0,
        )
    )
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
    for (x0, y0), (x1, y1) in zip(knots, knots[1:]):
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
    except Exception:
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
    same-tier = brak inwersji committed-odbioru; wyklucz sentinel/best_effort (|score|≥1e6)
    + R6>40 (bundle nie może psuć świeżości). Margin = RESERVE_TIEBREAK_MARGIN."""
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
        if cs is None or abs(cs) >= 1e6:
            continue  # sentinel/best_effort
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
        pickup_c = _repair_bag_coords(d, "pickup") or pickup_c or (0.0, 0.0)
    if not C.coords_in_bialystok_bbox(deliv_c):
        deliv_c = _repair_bag_coords(d, "delivery") or deliv_c or (0.0, 0.0)
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
    result = _assess_order_impl(
        order_event, fleet_snapshot, restaurant_meta, now,
        pending_queue=pending_queue, demand_context=demand_context,
        _bypass_early_bird=_bypass_early_bird,
    )
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
) -> PipelineResult:
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
    restaurant = order_event.get("restaurant")
    delivery_address = order_event.get("delivery_address")

    # === SP-B2-LOADGOV (2026-06-11): chwilowy load floty + EWMA 15 min ===
    # Telemetria ZAWSZE (loadgov_* per kandydat, LOCATION A+B); polityka
    # (kara bag≥3 przy ewma>2,7 + alert >3,5) za 🛑 ENABLE_FLEET_LOAD_GOVERNOR.
    loadgov_now, loadgov_ewma, loadgov_orders, loadgov_couriers = (
        _loadgov_compute(fleet_snapshot, now))
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
            _loadgov_save_alert_state(_new_armed, now if _lg_emit else _last_ts)
        _LOADGOV_STATE["alert_armed"] = _new_armed  # mirror do telemetrii procesu
        if _lg_emit:
            try:
                from dispatch_v2.telegram_utils import send_admin_alert as _lg_alert
                _lg_alert(
                    "🛑 Flota przeciążona — tryb defensywny\n"
                    f"Na każdego aktywnego kuriera przypada średnio {loadgov_ewma:.1f} "
                    f"zleceń ({loadgov_orders} aktywnych zleceń / {loadgov_couriers} kurierów).\n"
                    "Co robię: ostrożniej dokładam do pełnych toreb (kara za worki 3+), "
                    "propozycje nadal wychodzą normalnie.\n"
                    "Co Ty masz zrobić: dzwoń po posiłki — każdy dodatkowy kurier "
                    "realnie zbija opóźnienia (próg alarmu: 3,5 zlec./kuriera, "
                    "odwołanie poniżej 3,0)."
                )
            except Exception:
                pass  # Telegram unreachable nie blokuje dispatchu

    # Defense gate (L2): brak pickup_coords = order bez geokodacji.
    # Pre-fix scenariusz: panel_watcher dla firmowego konta (address_id=161)
    # zostawiał pickup_coords=None → tuple() fallback (0,0) → haversine sentinel
    # 6285km → wszyscy kurierzy pickup_too_far → BRAK KANDYDATÓW. Post-fix:
    # parser uwag (L1) wypełnia coords gdy uwagi mają adres; gdy NIE (P3 edge,
    # parser regression, malformed uwagi) — fail-loud SKIP zamiast śmieciowy
    # feasibility loop. czasowka_scheduler emit'uje dedicated Telegram alert
    # PRZED tym wywołaniem (visible operator).
    _raw_pickup_coords = order_event.get("pickup_coords")
    if _raw_pickup_coords is None or _raw_pickup_coords == [0.0, 0.0] or _raw_pickup_coords == (0.0, 0.0):
        log.warning(
            f"assess_order SKIP {order_id} aid={order_event.get('address_id')!r}: "
            f"pickup_coords missing — defense gate (no geocode); "
            f"uwagi_parsed={order_event.get('uwagi_pickup_parsed')!r}"
        )
        return PipelineResult(
            order_id=order_id,
            verdict="SKIP",
            reason="no_pickup_geocode",
            best=None,
            candidates=[],
            pickup_ready_at=None,
            restaurant=restaurant,
            delivery_address=delivery_address,
            pool_total_count=0,
            pool_feasible_count=0,
        )

    pickup_coords = tuple(_raw_pickup_coords)
    delivery_coords = tuple(order_event.get("delivery_coords") or (0.0, 0.0))

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

    # Fix 2026-05-07: early_bird threshold patrzy na RAW pickup_at_warsaw (deklaracja
    # restauracji), NIE extended czas_kuriera_warsaw. Bug strukturalny od V3.19f deploy:
    # czasowka_scheduler liczy mtp z raw, assess_order early_bird patrzył na extended →
    # czasówki przedłużone Ziomkiem o 20-30min były KOORD'owane jako pool=0 mimo że
    # czasowka_scheduler był w T-40 trigger window. Eliminuje 49% KOORD czasówek
    # (`zero MAYBE` 19× / 39 całych w 5-day eval_log obs).
    pickup_at_for_early_bird_raw = order_event.get("pickup_at_warsaw") or order_event.get("pickup_at")
    pickup_at_for_early_bird = (
        parse_panel_timestamp(pickup_at_for_early_bird_raw)
        if pickup_at_for_early_bird_raw else pickup_at
    )

    # Early bird → KOORD
    if pickup_at_for_early_bird is not None and not _bypass_early_bird:
        pu = pickup_at_for_early_bird if pickup_at_for_early_bird.tzinfo else pickup_at_for_early_bird.replace(tzinfo=WARSAW)
        minutes_ahead = (pu.astimezone(timezone.utc) - now).total_seconds() / 60.0
        if minutes_ahead >= _early_bird_threshold_min():  # SCALE-01: flags.json hot
            # EARLYBIRD-01 forward-shadow: kontrfaktyk „co gdyby przepuścić do feasibility
            # teraz" (bez early_bird short-circuit). LOG-ONLY, flaga OFF default, fail-soft
            # (defense-in-depth — błąd shadow NIGDY nie psuje live KOORD). _bypass_early_bird=True
            # zapobiega rekurencji (max głębokość 1).
            if _earlybird_t30_shadow_enabled():
                try:
                    # Kontrfaktyk woła _assess_order_impl BEZPOŚREDNIO (nie wrapper) —
                    # inaczej observability hook podwójnie zalogowałby zlecenie do
                    # candidate_decisions.jsonl (= strumień, który czyta pomiar EARLYBIRD).
                    _cf = _assess_order_impl(
                        order_event, fleet_snapshot, restaurant_meta, now,
                        pending_queue=pending_queue, demand_context=demand_context,
                        _bypass_early_bird=True,
                    )
                    _append_earlybird_t30_shadow({
                        "ts": now.isoformat(),
                        "order_id": order_id,
                        "restaurant": restaurant,
                        "minutes_ahead": round(minutes_ahead, 1),
                        "cf_verdict": _cf.verdict,
                        "cf_reason": _cf.reason,
                        "cf_pool_total": _cf.pool_total_count,
                        "cf_pool_feasible": _cf.pool_feasible_count,
                        "cf_best_cid": (_cf.best.courier_id if _cf.best else None),
                        "cf_best_score": (round(_cf.best.score, 2) if _cf.best else None),
                        # would_resolve = przepuszczenie dałoby realną PROPOZYCJĘ (nie kolejny
                        # KOORD/SKIP/NO) → kandydat do auto-resolve w T-30 zamiast eskalacji.
                        "would_resolve": (_cf.verdict == "PROPOSE"),
                    })
                except Exception as _eb_e:
                    log.warning(f"earlybird_t30_shadow failed oid={order_id}: {_eb_e}")
            return PipelineResult(
                order_id=order_id,
                verdict="KOORD",
                reason=f"early_bird ({minutes_ahead:.0f} min ahead)",
                best=None,
                candidates=[],
                pickup_ready_at=None,
                restaurant=restaurant,
                delivery_address=delivery_address,
            )

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
    def _v327_eval_courier(cid, cs):
        # BUG-D Faza 2b: opt-in TLS leg tracking dla per-route v2 aggregate.
        # Każdy thread w ThreadPoolExecutor ma własny TLS context — parallel safe.
        # Inner stop'nie tracking + aggregate przed return Candidate. Outer
        # try/finally jest safety net dla early return None paths (cleanup TLS
        # idempotent — stop_v2_request_tracking w obu miejscach OK).
        from dispatch_v2 import osrm_client as _osrm_client
        _osrm_client.start_v2_request_tracking()
        try:
            return _v327_eval_courier_inner(cid, cs)
        finally:
            # Idempotent cleanup — inner mogło już stop'nąć przed Candidate construction;
            # ten call wtedy zwraca None (TLS już wyczyszczony). Defense-in-depth dla raise.
            _osrm_client.stop_v2_request_tracking()

    def _v327_eval_courier_inner(cid, cs):
        courier_pos = _sanitize_courier_pos(getattr(cs, "pos", None))
        if courier_pos is None:
            return None
        bag_raw = getattr(cs, "bag", []) or []
        bag_sim = [_bag_dict_to_ordersim(b) for b in bag_raw]

        # === SP-B2-ZARAZWOLNY (2026-06-11): kurier "zaraz-wolny" ===
        # Probe ZAWSZE (telemetria soon_free_* do shadow); substytucja wejść
        # TYLKO za 🛑 flagą ENABLE_SOON_FREE_CANDIDATE (OFF): busy kończący
        # ≤12 min ewaluowany jako pusty-przy-ostatnim-dropie, dostępny od
        # free_at (pozycja/travel/gap niżej). Upraszczenie vs "dwa warianty":
        # przy ≤12 min do końca worka wartość interleave jest marginalna —
        # substytucja zamiast drugiego kandydata (ten sam cid w mapach
        # downstream nie może wystąpić 2×).
        soon_free_probe = _soon_free_probe(cid, bag_raw, now)
        soon_free_applied = False
        if (soon_free_probe is not None and soon_free_probe.get("eligible")
                and C.decision_flag("ENABLE_SOON_FREE_CANDIDATE")):
            courier_pos = tuple(soon_free_probe["last_drop_coords"])
            bag_raw = []
            bag_sim = []
            soon_free_applied = True

        # V3.27.1 sesja 2: Pre-proposal czas_kuriera recheck dla bagu kandydata.
        # Flag-gated (default False). Mechanizm 3 hybrydowy: 10min age + 5min cache,
        # ZERO max bag limit (Bartek peak bag=8-11 expected). Parallel fetchy via
        # ThreadPoolExecutor, defensive fallback do cached state przy fail.
        # Side-effect: emit synth CZAS_KURIERA_UPDATED z source=pre_proposal_recheck.
        if bag_sim and C.ENABLE_V327_PRE_PROPOSAL_RECHECK:
            try:
                _fresh_ck_dict = get_fresh_czas_kuriera_for_bag(bag_sim, now)
                # Override OrderSim.czas_kuriera_warsaw dla orders gdzie fresh != cached.
                # Downstream scoring/TSP/feasibility używa updated values w tym candidate run.
                for _bo in bag_sim:
                    _fresh = _fresh_ck_dict.get(_bo.order_id)
                    if _fresh is not None and _fresh != getattr(_bo, "czas_kuriera_warsaw", None):
                        _bo.czas_kuriera_warsaw = _fresh
            except Exception as _e:
                log.warning(f"V3.27.1 pre_recheck oid_new={new_order.order_id} cid={cid} fail: {_e}")
                # Defensive: continue z cached bag_sim values (zero behavior change)

        # POZIOM 1 same-restaurant: order w bagu ze statusem "assigned" (kurier
        # jeszcze JEDZIE do pickupu) z tej samej restauracji co nowy order.
        # Picked_up SKIP: kurier już odjechał od restauracji, nie wraca po więcej.
        bundle_level1 = None
        if new_rest_norm:
            for b in bag_raw:
                if b.get("status") != "assigned":
                    continue
                br = (b.get("restaurant") or "").strip().lower()
                if br and br == new_rest_norm:
                    bundle_level1 = b.get("restaurant")
                    break

        # POZIOM 2 nearby pickup (<1.5 km): tylko w restauracjach gdzie kurier
        # jeszcze ma jechać po pickup (status="assigned"). Skip jeśli L1 lub
        # pickup_coords sentinel (0, 0).
        bundle_level2 = None
        bundle_level2_dist = None
        if (bundle_level1 is None
                and _coords_pass(
                    pickup_coords != (0.0, 0.0) and pickup_coords[0] != 0.0,
                    pickup_coords)):
            for b in bag_raw:
                if b.get("status") != "assigned":
                    continue
                bag_pc = b.get("pickup_coords")
                if not _coords_pass(bool(bag_pc), bag_pc):
                    continue
                try:
                    dist = haversine(tuple(bag_pc), pickup_coords)
                except Exception:
                    continue
                if dist < 1.5:
                    bundle_level2 = b.get("restaurant")
                    bundle_level2_dist = round(dist, 2)
                    break

        # POZIOM 3 corridor delivery (<2.0 km): nowa dostawa leży w korytarzu
        # trasy kurier → bag deliveries. Niezależny od L1/L2.
        bundle_level3 = False
        bundle_level3_dev = None
        if _coords_pass(
                delivery_coords != (0.0, 0.0) and delivery_coords[0] != 0.0,
                delivery_coords):
            bag_drops = [
                b.get("delivery_coords") for b in bag_raw
                if _coords_pass(bool(b.get("delivery_coords")), b.get("delivery_coords"))
            ]
            dev = _min_dist_to_route_km(delivery_coords, tuple(courier_pos), bag_drops)
            # V3.26 Bug C (2026-04-25): configurable threshold (was hardcoded 2.0).
            _po_drodze_dist_km = float(getattr(C, "PO_DRODZE_DIST_KM", 2.0))
            if dev is not None and dev < _po_drodze_dist_km:
                bundle_level3 = True
                bundle_level3_dev = round(dev, 2)

        # V3.27 Bug Z fix (2026-04-25 wieczór): compute min drop_proximity_factor
        # across (new_drop + bag_drops) dla SOFT penalty (Q5) + Z-OWN-1 corridor
        # mult (Q5a). Gated by ENABLE_V327_BUG_FIXES_BUNDLE.
        # 'Unknown' zone treated as 0.0 (defensive — coverage gap akceptowany per Q4).
        # Empty bag (len < 1) → score_mult=1.0, corridor_mult=1.0 (no-op).
        v327_min_drop_factor = None
        v327_bundle_score_mult = 1.0
        v327_drop_zones_audit = None
        v327_min_drop_factor_known = None
        v327_unknown_zone_present = False
        # Z-02 (audyt 2026-06-10): sign-guard + Unknown-split. Hot-reload kill-switch
        # w flags.json, env default ON (common.ENABLE_V327_MULT_SIGN_GUARD).
        _v327_sign_guard_on = C.flag(
            "ENABLE_V327_MULT_SIGN_GUARD",
            default=bool(getattr(C, "ENABLE_V327_MULT_SIGN_GUARD", True)))
        if C.ENABLE_V327_BUG_FIXES_BUNDLE and bag_raw:
            try:
                _v327_new_zone = C.drop_zone_from_address(
                    delivery_address,
                    order_event.get('delivery_city'),
                )
                _v327_bag_zones = [
                    C.drop_zone_from_address(
                        _b.get('delivery_address'),
                        _b.get('delivery_city'),
                    )
                    for _b in bag_raw
                ]
                _v327_all_zones = [_v327_new_zone] + _v327_bag_zones
                v327_min_drop_factor = C.min_drop_proximity_factor(_v327_all_zones)
                if v327_min_drop_factor is not None:
                    v327_bundle_score_mult = C.bundle_score_multiplier(v327_min_drop_factor)
                # Z-02: 'Unknown' (luka pokrycia districts) nie jest dowodem
                # cross-quadrant → mult łagodny 0.7; realny cross-quadrant wśród
                # ZNANYCH stref zostaje 0.1 (min z obu sygnałów).
                if _v327_sign_guard_on and v327_min_drop_factor is not None:
                    v327_min_drop_factor_known, v327_unknown_zone_present = (
                        C.min_drop_proximity_factor_split(_v327_all_zones))
                    _v327_mult = C.bundle_score_multiplier(v327_min_drop_factor_known)
                    if v327_unknown_zone_present:
                        _v327_mult = min(_v327_mult, C.V327_BUNDLE_UNKNOWN_SCORE_MULT)
                    v327_bundle_score_mult = _v327_mult
                v327_drop_zones_audit = {
                    "new_zone": _v327_new_zone,
                    "bag_zones": _v327_bag_zones,
                    "min_factor": v327_min_drop_factor,
                    "min_factor_known": v327_min_drop_factor_known,
                    "has_unknown": v327_unknown_zone_present,
                    "score_mult": v327_bundle_score_mult,
                }
            except Exception as _v327_z_e:
                log.warning(
                    f"V3.27 Bug Z compute fail: {type(_v327_z_e).__name__}: {_v327_z_e}"
                )

        # P3-D3 2026-05-11: unify sla_minutes=35 (Adrian doktryna V3.28 P0 anchor
        # 10.05: 35 min jest JEDYNĄ hard rule, per-zlecenie, anchor=pickup_ready_at).
        # Pre-fix: 45 if bag_sim (F2.1c heurystyka 17.04) maskował thermal violations
        # → best_effort z plan.sla_violations=0 dla 35-44 min carry (Bartek 187 min case).
        sla_minutes = 35

        # V3.19d: read integration — extract base_sequence z saved plan dla
        # bag ordering. Triple guard: flag True + bag non-empty + saved match.
        # Mismatch / exception → base_sequence=None (fresh TSP fallback).
        _base_sequence = None
        if bag_sim:
            try:
                from dispatch_v2.common import ENABLE_SAVED_PLANS_READ
                if ENABLE_SAVED_PLANS_READ:
                    from dispatch_v2 import plan_manager as _pm_read
                    _bag_oids = {str(o.order_id) for o in bag_sim}
                    _saved = _pm_read.load_plan(
                        str(cid), active_bag_oids=_bag_oids,
                        invalidate_on_mismatch=not C.flag("ENABLE_LOAD_PLAN_PURE_READ"))
                    if _saved is not None:
                        _seq = [
                            str(s["order_id"]) for s in _saved.get("stops", [])
                            if s.get("type") == "dropoff"
                            and str(s.get("order_id")) in _bag_oids
                        ]
                        if set(_seq) == _bag_oids and len(_seq) == len(_bag_oids):
                            _base_sequence = _seq
            except Exception:
                _base_sequence = None

        # V3.26 STEP 6 (R-07 v2): compute chain_eta BEFORE feasibility — R-01 MANDATORY
        # integration gdy flag True (chain_eta = pickup_ref source of truth).
        # Zawsze compute (shadow), flag-gated use dla decision path.
        r07_chain_result = None
        r07_chain_eta_utc = None
        _r07_latency_ms = None
        try:
            from dispatch_v2.chain_eta import compute_chain_eta as _cce
            from dispatch_v2.osrm_client import route as _osrm_route, haversine as _hav
            def _drive_min_fn(a, b):
                try:
                    r = _osrm_route(a, b)
                    return float(r.get("duration_min") or 0) if r else None
                except Exception:
                    return None
            _speed_mult = 1.0
            try:
                if C.ENABLE_V326_SPEED_MULTIPLIER:
                    _tb = getattr(cs, "tier_bag", None) or "std"
                    _speed_mult = float(C.V326_SPEED_MULTIPLIER_MAP.get(_tb, 1.0))
            except Exception:
                pass
            import time as _time
            _r07_t0 = _time.perf_counter()
            r07_chain_result = _cce(
                courier_pos=getattr(cs, "pos", None),
                pos_source=getattr(cs, "pos_source", None),
                pos_age_min=getattr(cs, "pos_age_min", None),
                bag_orders=bag_sim,
                proposal_pickup_coords=tuple(pickup_coords),
                proposal_scheduled_utc=pickup_ready_at,
                now_utc=now,
                osrm_drive_min=_drive_min_fn,
                haversine_km=_hav,
                speed_multiplier=_speed_mult,
            )
            _r07_latency_ms = (_time.perf_counter() - _r07_t0) * 1000.0
            if r07_chain_result is not None:
                r07_chain_eta_utc = r07_chain_result.effective_eta_utc
        except Exception as _r07_e:
            log.warning(f"R-07 chain_eta compute fail: {type(_r07_e).__name__}: {_r07_e}")

        verdict, reason, metrics, plan = check_feasibility_v2(
            courier_pos=tuple(courier_pos),
            bag=bag_sim,
            new_order=new_order,
            shift_end=getattr(cs, "shift_end", None),
            shift_start=getattr(cs, "shift_start", None),  # V3.25 STEP B (R-01)
            now=now,
            pickup_ready_at=pickup_ready_at,
            sla_minutes=sla_minutes,
            base_sequence=_base_sequence,
            r07_chain_eta_utc=r07_chain_eta_utc,  # V3.26 STEP 6 R-07 MANDATORY when flag=True
            pos_source=getattr(cs, "pos_source", None),  # V3.28 ETAP 2 — clamp gate
            courier_tier=getattr(cs, "tier_bag", None),  # 2026-05-17 tier-aware DWELL
            schedule_source_stale=getattr(cs, "schedule_source_stale", False),  # D2 (audyt 2026-05-28)
            pos_from_store=getattr(cs, "pos_from_store", False),  # Z-06 (audyt 2026-06-10) — store-rescue to nie świeży fix
        )

        # F1.8f hard guard: kurier którego zmiana kończy się PRZED pickup_ready_at
        # nie może wziąć tego zlecenia (nawet jeśli SHIFT_END_BUFFER_MIN przeszło).
        cs_shift_end = getattr(cs, "shift_end", None)
        if cs_shift_end is not None and pickup_ready_at is not None:
            if cs_shift_end.tzinfo is None:
                cs_shift_end_utc = cs_shift_end.replace(tzinfo=timezone.utc)
            else:
                cs_shift_end_utc = cs_shift_end.astimezone(timezone.utc)
            if pickup_ready_at > cs_shift_end_utc:
                verdict = "NO"
                end_hhmm = cs_shift_end.strftime("%H:%M") if hasattr(cs_shift_end, "strftime") else "?"
                reason = f"shift_end_before_pickup (zmiana do {end_hhmm}, odbiór później)"
                plan = None

        # V3.19c sub B: observational read-shadow diff log. Zero wpływu na
        # scoring path — tylko zapisuje różnicę saved vs fresh plan sequence
        # dla orderów w bagu. Flag ENABLE_SAVED_PLANS_READ_SHADOW default True.
        if plan is not None and plan.sequence and bag_sim:
            try:
                from dispatch_v2 import plan_manager as _pm_shadow
                _active_bag = {str(o.order_id) for o in bag_sim}
                _pm_shadow.log_read_shadow_diff(
                    courier_id=str(cid),
                    fresh_sequence=list(plan.sequence),
                    active_bag_oids=_active_bag,
                    now=now,
                    extra={"new_order_id": str(new_order.order_id)},
                )
            except Exception:
                pass  # shadow log never breaks hot path

        bag_drop_coords = [b.delivery_coords for b in bag_sim]
        oldest = _oldest_in_bag_min(bag_sim, now)

        # Fix 2: last_wave_pos — efektywna pozycja startowa do liczenia dystansu
        # do NOWEGO pickupu. Po dostarczeniu bagu kurier będzie w delivery_coords
        # ostatniego orderu z plan.sequence. Używane TYLKO dla km_to_pickup i
        # S_dystans (scoring.road_km). R4/R9 route-deviation i R9 wait zostają
        # z oryginalnym courier_pos (liczą trasę bagu, nie nowego punktu startu).
        # Kurier bez baga → effective_start_pos == courier_pos (no-op).
        #
        # V3.26 Bug A complete (2026-04-25): flag-gated insertion anchor.
        # Z ENABLE_V326_ANCHOR_BASED_SCORING=True: effective_start_pos =
        # chronologically previous stop in plan PRZED new pickup (anchor).
        # Bez flag: legacy chronological-last-drop (semantycznie mylące dla
        # mid-chain insertion — kurier rzeczywiście jest przy anchor location,
        # NIE na end-of-bag location).
        effective_start_pos = tuple(courier_pos)
        v326_anchor_restaurant = None
        v326_anchor_used = False
        v326_anchor_obj = None  # Bug D fix: keep full anchor object for bundle_level2 override
        if getattr(C, "ENABLE_V326_ANCHOR_BASED_SCORING", False) and bag_sim and plan is not None:
            from dispatch_v2.insertion_anchor import compute_insertion_anchor as _cia
            try:
                _anchor = _cia(plan, str(order_id), bag_sim)
            except Exception:
                _anchor = None
            if _anchor is not None:
                effective_start_pos = _anchor.location
                v326_anchor_restaurant = _anchor.restaurant_name
                v326_anchor_used = True
                v326_anchor_obj = _anchor

                # V3.26 Bug D fix (2026-04-25): anchor-based "po odbiorze z X"
                # override legacy bundle_level2 (first geographic match w bag_raw
                # iteration order). Anchor = chronologically previous stop w plan;
                # gdy is_pickup AND <1.5km od new pickup → X = anchor.restaurant_name.
                # Inaczej (anchor is drop OR far): clear bundle_level2 (NIE pokazujemy
                # mylącego "po odbiorze" gdy nie ma chronological pickup before new).
                if _anchor.is_pickup:
                    try:
                        _l2_anchor_dist = haversine(_anchor.location, pickup_coords)
                    except Exception:
                        _l2_anchor_dist = None
                    if _l2_anchor_dist is not None and _l2_anchor_dist < 1.5:
                        bundle_level2 = _anchor.restaurant_name
                        bundle_level2_dist = round(_l2_anchor_dist, 2)
                    else:
                        bundle_level2 = None
                        bundle_level2_dist = None
                else:
                    # Anchor is drop → no clear "po odbiorze" semantyka
                    bundle_level2 = None
                    bundle_level2_dist = None
        if not v326_anchor_used and bag_sim and plan is not None and plan.sequence:
            # Legacy fallback: chronological last drop in sequence
            _bag_by_oid = {o.order_id: o for o in bag_sim}
            _bag_in_seq = [oid for oid in plan.sequence if oid in _bag_by_oid]
            if _bag_in_seq:
                effective_start_pos = tuple(_bag_by_oid[_bag_in_seq[-1]].delivery_coords)

        # F1.7 fix: travel_min = plan-based (uwzględnia bag + waiting na pickup_ready),
        # używane przez compute_assign_time. Display ETA jest osobne (drive_min).
        # Fix 2: km_to_pickup liczone od effective_start_pos (end-of-wave dla bag).
        # V3.28 #28 ext (2026-05-11): sanitize effective_start_pos — pochodne pozycje
        # z _anchor.location (linia 1564) lub bag tail delivery_coords (linia 1595)
        # mogą być (0,0) gdy bag zawiera order z P0.4 data quality issue (delivery_coords
        # missing) — courier_resolver loguje "courier X picked_up order Y bez delivery_coords".
        # Bez sanitize → haversine raise → V328_CP_SOLVER_FAIL_PER_COURIER spam (residual 9/7h
        # post #28 cz.1; cid=508/523 z bag=469087). Mirror _sanitize_courier_pos pattern.
        effective_start_pos = _sanitize_courier_pos(effective_start_pos) or effective_start_pos
        km_to_pickup_haversine = haversine(effective_start_pos, pickup_coords) * HAVERSINE_ROAD_FACTOR_BIALYSTOK

        # V3.26 Bug C strict mode (2026-04-25): "po drodze" semantyka.
        # Pre-fix bundle_level3 fires na pure geometric (dev<2km) — Adrian's
        # case #468404 Maison 1.02km od Sweet Fit fires "po drodze" mimo że
        # pickup Maison @ 10:04 vs new pickup @ 10:37 = 33 min apart, 2 intervening
        # stops (drop Łąkowa, pickup Doner) → mylące UX.
        # Strict checks (gdy ENABLE_V326_PO_DRODZE_STRICT=True i bundle_level3 fired):
        # 1. Time proximity: |new_pickup_ready_at - bag_pickup_ready_at| <= TIME_DIFF (10 min)
        # 2. Intervening stops: count events między anchor i new pickup w plan.events
        #    <= MAX_INTERVENING (0)
        # Fail któregoś check → bundle_level3 cleared.
        if bundle_level3 and getattr(C, "ENABLE_V326_PO_DRODZE_STRICT", False):
            _time_diff_max = float(getattr(C, "PO_DRODZE_TIME_DIFF_MIN", 10))
            _max_intervening = int(getattr(C, "PO_DRODZE_MAX_INTERVENING", 0))

            # Time proximity: dowolny bag pickup w ±_time_diff_max?
            _time_proximate = False
            if pickup_ready_at is not None and bag_sim:
                _new_pra = pickup_ready_at
                if _new_pra.tzinfo is None:
                    _new_pra = _new_pra.replace(tzinfo=timezone.utc)
                for _b in bag_sim:
                    _bp = getattr(_b, 'pickup_ready_at', None)
                    if _bp is None:
                        continue
                    if _bp.tzinfo is None:
                        _bp = _bp.replace(tzinfo=timezone.utc)
                    _delta_min = abs((_bp - _new_pra).total_seconds()) / 60.0
                    if _delta_min <= _time_diff_max:
                        _time_proximate = True
                        break

            # Intervening stops count (gdy plan + anchor available)
            _intervening_count = None
            if v326_anchor_obj is not None and plan is not None:
                _events_for_count = []
                _pa = plan.pickup_at or {}
                _da = plan.predicted_delivered_at or {}
                for _oid, _ts in _pa.items():
                    if isinstance(_ts, str):
                        try:
                            _ts = datetime.fromisoformat(_ts.replace('Z', '+00:00'))
                        except Exception:
                            continue
                    if _ts.tzinfo is None:
                        _ts = _ts.replace(tzinfo=timezone.utc)
                    _events_for_count.append((_ts, 'pickup', str(_oid)))
                for _oid, _ts in _da.items():
                    if isinstance(_ts, str):
                        try:
                            _ts = datetime.fromisoformat(_ts.replace('Z', '+00:00'))
                        except Exception:
                            continue
                    if _ts.tzinfo is None:
                        _ts = _ts.replace(tzinfo=timezone.utc)
                    _events_for_count.append((_ts, 'drop', str(_oid)))
                _events_for_count.sort(key=lambda e: (e[0], 0 if e[1] == 'pickup' else 1))
                _new_idx = next((i for i, e in enumerate(_events_for_count)
                                 if e[2] == str(order_id) and e[1] == 'pickup'), None)
                _anchor_kind = 'pickup' if v326_anchor_obj.is_pickup else 'drop'
                _anchor_idx = next((i for i, e in enumerate(_events_for_count)
                                    if e[2] == v326_anchor_obj.order_id and e[1] == _anchor_kind), None)
                if _new_idx is not None and _anchor_idx is not None and _new_idx > _anchor_idx:
                    _intervening_count = _new_idx - _anchor_idx - 1

            # Decide: clear bundle_level3 jeśli któryś check fail
            _strict_fail = (not _time_proximate) or (
                _intervening_count is not None and _intervening_count > _max_intervening
            )
            if _strict_fail:
                bundle_level3 = False
                bundle_level3_dev = None

        # scoring.score_candidate: road_km przekazujemy jawnie (S_dystans użyje
        # effective_start_pos → pickup), a bearing (S_kierunek) nadal z courier_pos.
        score_result = scoring.score_candidate(
            courier_pos=tuple(courier_pos),
            restaurant_pos=pickup_coords,
            bag_drop_coords=bag_drop_coords or None,
            bag_size=len(bag_sim),
            oldest_in_bag_min=oldest,
            road_km=km_to_pickup_haversine,
            fleet_context=fleet_context,
        )

        # drive_min: pure drive od COURIER_POS (nie effective_start_pos) do restauracji.
        # R9 wait invariant + eta_drive display — trzyma oryginalną semantykę.
        # V3.27 Bug X fix: OSRM-first (z traffic_mult applied via osrm_client._apply_traffic_multiplier)
        # zamiast haversine/fleet_speed_kmh fallback. Single source of truth dla ETA.
        # Fallback do haversine × road_factor / fleet_speed (JUŻ korkowy bucket) tylko przy
        # hard exception (osrm_client samo handluje circuit-breaker → haversine fallback).
        try:
            from dispatch_v2 import osrm_client as _osrm_v327
            _osrm_drive_res = _osrm_v327.route(tuple(courier_pos), pickup_coords)
            drive_min = float(_osrm_drive_res.get("duration_min") or 0.0)
            _drive_km_from_courier = float(_osrm_drive_res.get("distance_km") or 0.0)
        except Exception as _v327_e:
            log.warning(
                f"V3.27 drive_min OSRM exception, fallback to haversine (korkowy fleet_speed): "
                f"{type(_v327_e).__name__}: {_v327_e}"
            )
            _drive_km_from_courier = haversine(tuple(courier_pos), pickup_coords) * HAVERSINE_ROAD_FACTOR_BIALYSTOK
            # #12 audyt 28.06: fleet_speed_kmh = get_fallback_speed_kmh = bucket KORKOWY (20-32 km/h,
            # traffic w środku) → NIE mnóż dodatkowo get_traffic_multiplier (podwójne liczenie ruchu
            # ~+25..49% peak). Bliźniak osrm_client._apply_traffic_multiplier (osrm_fallback guard).
            drive_min = (_drive_km_from_courier / fleet_speed_kmh) * 60.0 if fleet_speed_kmh > 0 else 0.0
        drive_arrival_utc = now + timedelta(minutes=drive_min)

        eta_source = "haversine"
        if plan is not None and order_id in (plan.pickup_at or {}):
            arrive_pickup_utc = plan.pickup_at[order_id] - timedelta(minutes=DWELL_PICKUP_MIN)
            if arrive_pickup_utc.tzinfo is None:
                arrive_pickup_utc = arrive_pickup_utc.replace(tzinfo=timezone.utc)
            travel_min = max(0.0, (arrive_pickup_utc - now).total_seconds() / 60.0)
            eta_pickup_utc = arrive_pickup_utc
            eta_source = "plan"
        else:
            travel_min = drive_min
            eta_pickup_utc = drive_arrival_utc

        # V3.26 STEP 6 (R-07 v2 CHAIN-ETA) — flag-gated override eta_pickup_utc.
        # Chain_eta already computed przed feasibility (line ~845). Tu tylko override
        # decision path gdy flag=True.
        if C.ENABLE_V326_R07_CHAIN_ETA and r07_chain_result is not None:
            eta_pickup_utc = r07_chain_eta_utc
            drive_min = r07_chain_result.total_chain_min
            travel_min = r07_chain_result.total_chain_min
            eta_source = "r07_chain_eta"

        # SP-B2-ZARAZWOLNY: dostępność od free_at — kurier rusza po ostatnim
        # dropie (wzór pre_shift: czas oczekiwania + dojazd z pozycji dropa).
        if soon_free_applied:
            _sf_wait = max(0.0, float(soon_free_probe.get("free_at_min") or 0.0))
            travel_min = round(_sf_wait + drive_min, 1)
            eta_pickup_utc = now + timedelta(minutes=travel_min)
            drive_arrival_utc = eta_pickup_utc
            eta_source = "soon_free"

        # Bundle bonus — sumowanie L1 + L2 + R4 (Bartek Gold Standard).
        # L1 = +25 (same restaurant), L2 = max(0, 20 - dist*10).
        # R4 (zastępuje L3): tier-based free-stop curve × weight 1.5.
        #   dev ≤ 0.5 km  → raw 100      (full free stop)
        #   0.5 < dev ≤ 1.5 → raw 50*(1.5-d)/1.0 linear
        #   1.5 < dev ≤ 2.5 → raw 20*(2.5-d)/1.0 linear
        #   > 2.5 km       → raw 0
        bonus_l1 = 25.0 if bundle_level1 else 0.0
        # V3.19h BUG-1: drop_proximity_factor mnożnik na bonus_l1.
        # Gold tier pattern: SR bundle TYLKO gdy dropy blisko. Std bierze SR ślepo
        # (Kacper S avg drop_spread 10km dla SR bundles — anti-pattern).
        # Factor:
        #   1.0 — dropy w tej samej strefie (osiedlu)
        #   0.5 — adjacent strefach (sąsiadujące per ACK właściciela)
        #   0.0 — odległe albo Unknown (defensive)
        # min per-pair factor użyty (konserwatywnie najgorsza para).
        v319h_bug1_drop_proximity_factor = 1.0
        v319h_bug1_sr_bundle_adjusted = bonus_l1
        if C.ENABLE_V319H_BUG1_DROP_PROXIMITY_FACTOR and bundle_level1:
            # Zbierz dropy: new_order + wszystkie bag items z SR match
            _new_zone = C.drop_zone_from_address(
                order_event.get('delivery_address'),
                order_event.get('delivery_city'),
            )
            _zones = [_new_zone]
            for _b in bag_raw:
                if _b.get('status') != 'assigned':
                    continue
                if (_b.get('restaurant') or '').strip().lower() != new_rest_norm:
                    continue
                _bz = C.drop_zone_from_address(
                    _b.get('delivery_address'), _b.get('delivery_city')
                )
                _zones.append(_bz)
            # min factor across pairs (konserwatywnie)
            if len(_zones) >= 2:
                _factor_min = 1.0
                for _i in range(len(_zones)):
                    for _j in range(_i + 1, len(_zones)):
                        _f = C.drop_proximity_factor(_zones[_i], _zones[_j])
                        if _f < _factor_min:
                            _factor_min = _f
                v319h_bug1_drop_proximity_factor = _factor_min
            # Zastosuj mnożnik
            bonus_l1 = bonus_l1 * v319h_bug1_drop_proximity_factor
            v319h_bug1_sr_bundle_adjusted = bonus_l1
        bonus_l2 = max(0.0, 20.0 - bundle_level2_dist * 10.0) if bundle_level2_dist is not None else 0.0
        if bundle_level3_dev is None:
            bonus_r4_raw = 0.0
        else:
            d = bundle_level3_dev
            if d <= 0.5:
                bonus_r4_raw = 100.0
            elif d <= 1.5:
                bonus_r4_raw = 50.0 * (1.5 - d)
            elif d <= 2.5:
                bonus_r4_raw = 20.0 * (2.5 - d)
            else:
                bonus_r4_raw = 0.0
        bonus_r4 = bonus_r4_raw * 1.5  # R4 weight per Bartek Gold Standard
        # V3.27 Bug Z Z-OWN-1 (Q5a): corridor bonus *= min(drop_proximity_factor)
        # across drops. Cross-quadrant bag → factor=0.0 → bonus_r4=0 (zeroed razem
        # z Q5 bundle penalty). Same-quadrant → 1.0 (unchanged). Adjacent → 0.5×.
        # Gated by flag (v327_min_drop_factor=None gdy flag=False lub empty bag).
        v327_corridor_mult_applied = 1.0
        if v327_min_drop_factor is not None:
            v327_corridor_mult_applied = float(v327_min_drop_factor)
            bonus_r4 = bonus_r4 * v327_corridor_mult_applied
        bundle_bonus = bonus_l1 + bonus_l2 + bonus_r4
        # V3.19h BUG-2 wave continuation bonus dodany do final_score niżej
        # (wymaga free_at_dt computed after bag sim — order-of-execution).

        # Timing gap bonus: dopasowanie free_at (kurier wolny) do pickup_ready
        # (jedzenie gotowe). Zastępuje availability_bonus.
        #   gap = free_at_min - time_to_pickup_ready
        #   |gap| ≤  5  → +25  (idealne dopasowanie)
        #   |gap| ≤ 10  → +15  (dobre)
        #   |gap| ≤ 15  → +5   (akceptowalne)
        #   gap  >  15  → -3/min za każdą minutę >15 (kurier się spóźni)
        #   gap  < -15  → -2/min za każdą minutę <-15 (restauracja czeka)
        # pickup_ready_at=None → time_to_pickup_ready = travel_min (zakładamy
        # gotowość gdy kurier dotrze) → gap neutralny.
        # Bag pusty → free_at_min = 0 (już wolny).
        free_at_min = 0.0
        free_at_dt: Optional[datetime] = None
        if bag_sim and plan is not None and plan.predicted_delivered_at:
            bag_oids_set = {o.order_id for o in bag_sim}
            bag_in_seq = [oid for oid in (plan.sequence or []) if oid in bag_oids_set]
            if bag_in_seq:
                last_bag_oid = bag_in_seq[-1]
                _free_at_dt = plan.predicted_delivered_at.get(last_bag_oid)
                if _free_at_dt is not None:
                    if _free_at_dt.tzinfo is None:
                        _free_at_dt = _free_at_dt.replace(tzinfo=timezone.utc)
                    free_at_dt = _free_at_dt
                    free_at_min = max(0.0, (_free_at_dt - now).total_seconds() / 60.0)

        # SP-B2-ZARAZWOLNY: po substytucji bag jest pusty → free_at_min=0
        # zafałszowałby timing gap; przywróć realne zwolnienie z probe
        # (gap = free_at vs gotowość nowego — dokładnie semantyka B2).
        if soon_free_applied:
            free_at_min = max(0.0, float(soon_free_probe.get("free_at_min") or 0.0))
            try:
                free_at_dt = datetime.fromisoformat(soon_free_probe["free_at_iso"])
            except Exception:
                free_at_dt = None

        if pickup_ready_at is not None:
            _pra_utc = pickup_ready_at if pickup_ready_at.tzinfo else pickup_ready_at.replace(tzinfo=timezone.utc)
            time_to_pickup_ready = max(0.0, (_pra_utc - now).total_seconds() / 60.0)
        else:
            time_to_pickup_ready = travel_min

        gap_min = free_at_min - time_to_pickup_ready
        _abs_gap = abs(gap_min)
        if _abs_gap <= 5:
            timing_gap_bonus = 25.0
        elif _abs_gap <= 10:
            timing_gap_bonus = 15.0
        elif _abs_gap <= 15:
            timing_gap_bonus = 5.0
        elif gap_min > 15:
            timing_gap_bonus = -3.0 * (gap_min - 15)
        else:  # gap_min < -15
            timing_gap_bonus = -2.0 * (-gap_min - 15)

        # F2.1b penalties — R6 soft BAG_TIME + R9 stopover + R9 wait.
        # R8 soft pozostaje None (placeholder do F2.1c — brak T_KUR propagation).
        # Wszystkie penalties ≤ 0 (ujemne albo zero), dodawane do final_score.

        # R6 soft: zone 30-35 min BAG_TIME. Hard cap 35 min jest w feasibility_v2
        # (F2.1b step 3), tu widzimy tylko przypadki 30-35 min które przeszły hard.
        # Reuse metrics.r6_max_bag_time_min (step 3) — zero duplicate computation.
        bonus_r6_soft_pen: Optional[float] = None
        bonus_r6_soft_pen_legacy: Optional[float] = None  # Fix #6: liniowa (pre-danger) dla shadow
        bonus_r6_soft_pen_raw: Optional[float] = None  # E7 2026-06-17: kara przed capem (telemetria)
        if plan is not None:
            r6_max_bag_time = metrics.get("r6_max_bag_time_min")
            if r6_max_bag_time is None:
                log.warning(
                    f"R6 soft skip: metrics.r6_max_bag_time_min missing "
                    f"despite plan!=None (expected after krok #6 restart)"
                )
                r6_max_bag_time = 0.0
            # Fix #6 477285 (2026-05-31): liniowa baza (legacy) + EKSTRA stroma kara
            # w danger zone (32-35) — patrz _r6_soft_penalty. 30-32 (R-BUFFER-OK) bez zmian.
            # E7 2026-06-17: cap_floor (flag-gated) uodparnia na zombie-pickup (-240k).
            _r6_cap_floor = (
                float(getattr(C, "R6_SOFT_PEN_CAP_FLOOR", -2000.0))
                if C.flag("ENABLE_R6_SOFT_PEN_CAP", False) else None
            )
            bonus_r6_soft_pen, bonus_r6_soft_pen_legacy, bonus_r6_soft_pen_raw = _r6_soft_penalty(
                r6_max_bag_time, C.BAG_TIME_SOFT_MIN, C.BAG_TIME_SOFT_PENALTY_PER_MIN,
                getattr(C, "ENABLE_R6_DANGER_ZONE_PENALTY", False),
                getattr(C, "BAG_TIME_DANGER_MIN", 32.0),
                getattr(C, "BAG_TIME_DANGER_PENALTY_PER_MIN", 16.0),
                cap_floor=_r6_cap_floor,
            )

        # R-PACZKI-FLEX (2026-05-20): gradient -1pt/min nad soft cap 2h pickup
        # / 3h delivery dla NIE-czasówka paczki. Fail-soft 0.0 dla jedzeniówek.
        bonus_r_paczki_flex = _r_paczki_flex_penalty(new_order, plan, now)

        # === BUG A shadow (2026-05-26): Σ bag_time + max + FIFO ===
        # Mierzymy bag_time per order z plan.pickup_at / predicted_delivered_at.
        # Sum + max + FIFO violations zbierane ZAWSZE (observability), bonus
        # tylko gdy flag ON. Default OFF — shadow-first, kalibracja po replay.
        # Reguła Adriana: „Suma czasów wszystkich dowozów w bagu jak najmniejsza,
        # lepiej oba po 15 min niż 25+8, FIFO tie-break".
        bag_times_per_order: Dict[str, float] = {}
        sum_bag_time_min_v = 0.0
        max_bag_time_min_v = 0.0
        fifo_violations = 0
        bonus_bag_time_sum = 0.0
        bonus_bag_time_max = 0.0
        bonus_fifo_violation = 0.0
        shadow_bag_time_sum = 0.0
        shadow_bag_time_max = 0.0
        shadow_fifo_violation = 0.0
        if plan is not None:
            _pu_map = getattr(plan, "pickup_at", None) or {}
            _do_map = getattr(plan, "predicted_delivered_at", None) or {}
            _pickup_order: List = []
            for _oid, _pu in _pu_map.items():
                _do = _do_map.get(_oid)
                if _pu is not None and _do is not None:
                    try:
                        bag_times_per_order[_oid] = (_do - _pu).total_seconds() / 60.0
                        _pickup_order.append((_pu, _oid))
                    except (TypeError, AttributeError):
                        pass
            _pickup_order.sort()
            sum_bag_time_min_v = sum(bag_times_per_order.values())
            max_bag_time_min_v = (
                max(bag_times_per_order.values()) if bag_times_per_order else 0.0
            )
            # FIFO violations: ile par (i<j by pickup) gdzie i delivered LATER niż j.
            for _i, (_pu_i, _oid_i) in enumerate(_pickup_order):
                for _pu_j, _oid_j in _pickup_order[_i + 1:]:
                    _do_i = _do_map.get(_oid_i)
                    _do_j = _do_map.get(_oid_j)
                    if _do_i is not None and _do_j is not None and _do_i > _do_j:
                        fifo_violations += 1
            # E7-doklejki 3+4 (2026-06-11): kary liczone ZAWSZE (lekcja #186 —
            # pola shadow przy OFF były zerowe, werdykt A/B wymagał rekonstrukcji
            # ze surowców); flaga gate'uje WYŁĄCZNIE aplikację do score. Stałe:
            # flags.json → stała modułu/env (flip A werdyktu = max+FIFO,
            # BAG_TIME_SUM_PENALTY_PER_MIN=0.0 ustawiane w flags.json).
            _fl_a = C.load_flags()
            shadow_bag_time_sum = -float(_fl_a.get(
                "BAG_TIME_SUM_PENALTY_PER_MIN",
                C.BAG_TIME_SUM_PENALTY_PER_MIN)) * sum_bag_time_min_v
            shadow_bag_time_max = -float(_fl_a.get(
                "BAG_TIME_MAX_PENALTY_PER_MIN",
                C.BAG_TIME_MAX_PENALTY_PER_MIN)) * max_bag_time_min_v
            shadow_fifo_violation = -float(_fl_a.get(
                "BAG_TIME_FIFO_TIE_PENALTY",
                C.BAG_TIME_FIFO_TIE_PENALTY)) * fifo_violations
            if C.decision_flag("ENABLE_BAG_TIME_FAIRNESS_SCORING"):
                bonus_bag_time_sum = shadow_bag_time_sum
                bonus_bag_time_max = shadow_bag_time_max
                bonus_fifo_violation = shadow_fifo_violation

        # === BUG B shadow (2026-05-26): kara za detour pickup-not-on-route ===
        # r5_pickup_detour_total_km już zbierane przez route_metrics jako metryka
        # obserwacyjna — dodajemy negative weight (free threshold + penalty/km).
        # Default OFF.
        bonus_r5_pickup_detour_penalty = 0.0
        _r5_detour_km_raw = metrics.get("r5_pickup_detour_total_km")
        _r5_detour_km = float(_r5_detour_km_raw) if isinstance(_r5_detour_km_raw, (int, float)) else 0.0
        # E7-doklejki 3+4: kara liczona ZAWSZE (lekcja #186), flaga gate'uje
        # tylko score; stałe flags.json → moduł/env (flip B werdyktu 11.06 =
        # R5_DETOUR_PENALTY_PER_KM=4.0 w flags.json, eskalacja 8.0 po 7 dniach).
        _fl_b = C.load_flags()
        _excess_km = max(0.0, _r5_detour_km - float(_fl_b.get(
            "R5_DETOUR_FREE_THRESHOLD_KM", C.R5_DETOUR_FREE_THRESHOLD_KM)))
        shadow_r5_pickup_detour_penalty = -float(_fl_b.get(
            "R5_DETOUR_PENALTY_PER_KM", C.R5_DETOUR_PENALTY_PER_KM)) * _excess_km
        # DETOUR-01 (audyt 03.06, case oid=477347 detour 9.1 km z dodatnim
        # score): marker ekstremalnego detouru przy worku ≥2 — obserwowalność
        # pod decyzję o vecie PO danych z flipu B, bez wpływu na score.
        r5_detour_extreme = bool(
            _r5_detour_km > C.R5_DETOUR_EXTREME_KM and len(bag_sim) >= 2)
        if C.decision_flag("ENABLE_R5_PICKUP_DETOUR_PENALTY"):
            bonus_r5_pickup_detour_penalty = shadow_r5_pickup_detour_penalty

        # R9 stopover — differential tax (bag=0 → 0, bag=1 → -8, bag=2 → -16, ...).
        # Rationale: scoring porównuje kandydatów względem kosztu DODANIA stopu,
        # nie absolutnego. Zgodny z op.1 "podatek przystankowy".
        bonus_r9_stopover = -len(bag_sim) * C.STOPOVER_SCORE_PER_STOP

        # R9 wait — penalty za przewidywane oczekiwanie pod restauracją > 5 min.
        # Wait = max(0, T_KUR_from_now - effective_drive_min).
        #
        # F2.1b step 4.1 fix: dla no_gps/pre_shift courierów drive_min z linii 285
        # jest liczony z SYNTHETIC courier_pos (fallback do BIALYSTOK_CENTER lub
        # last-known), co dla restauracji w centrum daje sztucznie niski drive_min
        # (~2-3 min) → wait_pred zawyżony → nierealny penalty.
        # Historyczny bug: order #466290 Chicago Pizza @ 2026-04-15T19:16:45 UTC,
        # Patryk 5506 (no_gps), bonus_r9_wait_pen = -101.76.
        #
        # Fix: effective_drive_min replikuje post-loop normalization (linie 453-469):
        #   no_gps     → max(15, prep_remaining_min)   (zgodne z linią 450)
        #   pre_shift  → shift_start_min                (zgodne z linią 465)
        #   inne       → drive_min                       (bez zmian dla GPS)
        # Legacy R9 wait penalty (linear, single new-pickup) — ZAWSZE compute,
        # niezależnie od flag, dla shadow log A/B comparison V3.27.1 vs legacy.
        # Lekcja #11: Replay/audit ≠ production validation; side-by-side w shadow
        # przed flip = najlepsze pre-flip validation.
        bonus_r9_wait_pen_legacy = 0.0
        if pickup_ready_at is not None:
            _pos_src = getattr(cs, "pos_source", None)
            if _pos_src == "no_gps":
                _prep_rem = max(0.0, (pickup_ready_at - now).total_seconds() / 60.0)
                effective_drive_min = max(15.0, _prep_rem)
            elif _pos_src == "pre_shift":
                effective_drive_min = float(getattr(cs, "shift_start_min", 0) or 0)
            else:
                effective_drive_min = drive_min
            tkur_from_now_min = (pickup_ready_at - now).total_seconds() / 60.0
            wait_pred_min = max(0.0, tkur_from_now_min - effective_drive_min)
            if wait_pred_min > C.RESTAURANT_WAIT_SOFT_MIN:
                bonus_r9_wait_pen_legacy = -(wait_pred_min - C.RESTAURANT_WAIT_SOFT_MIN) * C.RESTAURANT_WAIT_PENALTY_PER_MIN

        # V3.27.1 Wait penalty (Adrian's quadratic table) — flag-gated.
        # SUMMED per pickup w plan.sequence (new pickup + bag pickups not-yet-picked-up).
        # Helper compute_wait_penalty(wait_min) z scoring.py — linear interpolacja
        # między punktami tabeli, hard fallback -1000 dla wait > 60min.
        # Computed osobno (additive z legacy w serializacji), score używa v327 gdy
        # flag=True, legacy gdy False. Legacy ZAWSZE w serialize dla A/B compare.
        bonus_r9_wait_pen_v327 = 0.0
        if getattr(C, "ENABLE_V327_WAIT_PENALTY", False) and plan is not None:
            from datetime import datetime as _dt327
            from dispatch_v2.scoring import compute_wait_penalty as _v327_wp
            _new_oid = getattr(new_order, "order_id", None)
            _bag_by_oid_v327 = {b.order_id: b for b in bag_sim} if bag_sim else {}
            _plan_pickup_at = getattr(plan, "pickup_at", None) or {}
            _plan_seq = getattr(plan, "sequence", None) or []
            _v327_wait_pen_sum = 0.0
            for _oid in _plan_seq:
                _str_oid = str(_oid)
                # Find ready_at: new_order or bag order (skip already-picked-up)
                _order_ready = None
                if _str_oid == str(_new_oid):
                    _order_ready = pickup_ready_at
                elif _str_oid in _bag_by_oid_v327:
                    _bo = _bag_by_oid_v327[_str_oid]
                    if getattr(_bo, "picked_up_at", None) is None:
                        _order_ready = getattr(_bo, "pickup_ready_at", None)
                if _order_ready is None:
                    continue
                _pat_iso = _plan_pickup_at.get(_str_oid)
                if not _pat_iso:
                    continue
                try:
                    _pat_dt = _dt327.fromisoformat(str(_pat_iso))
                    _wait_min = max(0.0, (_pat_dt - _order_ready).total_seconds() / 60.0)
                    _v327_wait_pen_sum += _v327_wp(_wait_min)
                except Exception:
                    continue
            bonus_r9_wait_pen_v327 = _v327_wait_pen_sum

        # Score używa v327 gdy flag=True, legacy gdy False. Mutex (nie additive
        # do score), ale OBA serializowane w shadow log dla A/B comparison.
        if getattr(C, "ENABLE_V327_WAIT_PENALTY", False):
            bonus_r9_wait_pen = bonus_r9_wait_pen_v327
        else:
            bonus_r9_wait_pen = bonus_r9_wait_pen_legacy

        # V3.27.3 Wait kuriera penalty (Task 1 hypothesis B+C fix, 2026-04-27).
        # Mierzy idle kuriera pod restauracją (max(0, ready - chain_arrival)),
        # vs V327 mierzy restaurant wait (pickup_at - ready). Conditional
        # bag_size>=1 (jedzenie w aucie stygnie). HARD REJECT >20 min.
        # Per-pickup w plan.sequence; uses plan.arrival_at (V3.27.3 NEW field).
        bonus_v3273_wait_courier = 0.0
        bonus_v3273_wait_courier_legacy = 0.0  # Fix #7: per_min=-5 (pre-steepen) dla shadow
        v3273_wait_courier_max_min = 0.0
        v3273_wait_courier_max_oid = None
        v3273_wait_courier_max_restaurant = None
        v3273_wait_courier_hard_reject = False
        v3273_wait_courier_per_pickup = []
        if getattr(C, "ENABLE_V3273_WAIT_COURIER_PENALTY", False) and plan is not None:
            from datetime import datetime as _dt3273
            from dispatch_v2.scoring import compute_wait_courier_penalty as _v3273_wcp
            _new_oid_273 = getattr(new_order, "order_id", None)
            _bag_by_oid_273 = {b.order_id: b for b in bag_sim} if bag_sim else {}
            _plan_arrival_273 = getattr(plan, "arrival_at", None) or {}
            _plan_seq_273 = getattr(plan, "sequence", None) or []
            # N2 (2026-06-17, flaga ENABLE_V3273_WAIT_REJECT_PICKED_UP_ONLY):
            # reżim hard-reject "stygnące jedzenie" liczony po ODEBRANYCH (gorące
            # realnie w aucie), nie po PRZYPISANYCH. Kurier z workiem samych
            # przypisanych-nieodebranych (np. 413 12:39: 1 przypisane/0 odebrane)
            # nic nie wiezie → bag_size 0 → compute_wait_courier_penalty zwraca
            # (0,False) zanim sprawdzi wait → brak fałszywego hard-reject.
            _picked_up_count_273 = (
                sum(1 for _b273s in bag_sim if getattr(_b273s, "picked_up_at", None))
                if bag_sim else 0
            )
            if C.flag("ENABLE_V3273_WAIT_REJECT_PICKED_UP_ONLY", False):
                _bag_size_at_insertion_273 = _picked_up_count_273
            else:
                _bag_size_at_insertion_273 = len(bag_sim) if bag_sim else 0
            for _oid_273 in _plan_seq_273:
                _str_oid_273 = str(_oid_273)
                _order_ready_273 = None
                if _str_oid_273 == str(_new_oid_273):
                    _order_ready_273 = pickup_ready_at
                elif _str_oid_273 in _bag_by_oid_273:
                    _bo_273 = _bag_by_oid_273[_str_oid_273]
                    if getattr(_bo_273, "picked_up_at", None) is None:
                        _order_ready_273 = getattr(_bo_273, "pickup_ready_at", None)
                if _order_ready_273 is None:
                    continue
                _arr_273 = _plan_arrival_273.get(_str_oid_273)
                if _arr_273 is None:
                    continue
                try:
                    if isinstance(_arr_273, str):
                        _arr_dt_273 = _dt3273.fromisoformat(_arr_273)
                    else:
                        _arr_dt_273 = _arr_273
                    if _arr_dt_273.tzinfo is None:
                        _arr_dt_273 = _arr_dt_273.replace(tzinfo=timezone.utc)
                    _ready_273 = _order_ready_273
                    if _ready_273.tzinfo is None:
                        _ready_273 = _ready_273.replace(tzinfo=timezone.utc)
                    _wait_273 = max(0.0, (_ready_273 - _arr_dt_273).total_seconds() / 60.0)
                    _pen_273, _reject_273 = _v3273_wcp(_wait_273, _bag_size_at_insertion_273)
                    # Fix #7: legacy (per_min=-5) liczone równolegle dla shadow-porównania.
                    _pen_273_legacy, _ = _v3273_wcp(
                        _wait_273, _bag_size_at_insertion_273,
                        per_min=getattr(C, "V3273_WAIT_COURIER_PER_MIN_PENALTY_LEGACY", -5.0))
                    v3273_wait_courier_per_pickup.append({
                        "oid": _str_oid_273,
                        "wait_min": round(_wait_273, 2),
                        "penalty": round(_pen_273, 2),
                        "hard_reject": _reject_273,
                    })
                    if _reject_273:
                        v3273_wait_courier_hard_reject = True
                    bonus_v3273_wait_courier += _pen_273
                    bonus_v3273_wait_courier_legacy += _pen_273_legacy
                    if _wait_273 > v3273_wait_courier_max_min:
                        v3273_wait_courier_max_min = _wait_273
                        v3273_wait_courier_max_oid = _str_oid_273
                        if _str_oid_273 == str(_new_oid_273):
                            v3273_wait_courier_max_restaurant = restaurant
                        else:
                            for _b_273 in bag_raw:
                                if str(_b_273.get("order_id") or "") == _str_oid_273:
                                    v3273_wait_courier_max_restaurant = _b_273.get("restaurant")
                                    break
                except Exception:
                    continue

            # N2 (2026-06-17): kurier BEZ odebranego jedzenia (0 picked_up) nie
            # dostaje hard-reject (nic nie stygnie), ale idle pod restauracją
            # karany ROSNĄCO powyżej progu — Adrian: "zostaw soft, ale z rosnącą
            # karą powyżej 5 min czekania pod restauracją". Bazujemy na MAX wait
            # (najdłuższy postój), bez sumowania per-pickup żeby nie stackować.
            if (C.flag("ENABLE_V3273_WAIT_REJECT_PICKED_UP_ONLY", False)
                    and _picked_up_count_273 == 0
                    and v3273_wait_courier_max_min > 0):
                from dispatch_v2.scoring import compute_idle_wait_soft_penalty as _v3273_idle
                _idle_pen_273 = _v3273_idle(v3273_wait_courier_max_min)
                bonus_v3273_wait_courier += _idle_pen_273
                bonus_v3273_wait_courier_legacy += _idle_pen_273

        # R-INTRA-RESTAURANT-GAP (HARD, 2026-05-14): max gap między dwoma
        # kolejnymi pickupami tej samej restauracji w plan.pickup_at.
        # Łapie scenariusz gdy wait_courier formuła ślepa (arrival_at[new]
        # ≈ ready[new] dla mid-trip same-restaurant insert), a kurier
        # realnie sterczy N min przy stoliku między pickup#1 a pickup#2.
        intra_rest_gap_max_min = 0.0
        intra_rest_gap_max_pair = None
        intra_rest_gap_max_restaurant = None
        intra_rest_gap_hard_reject = False
        if getattr(C, "ENABLE_INTRA_RESTAURANT_GAP_LIMIT", False) and plan is not None:
            from datetime import datetime as _dt_irg
            _new_oid_irg = str(getattr(new_order, "order_id", "") or "")
            _rest_by_oid_irg = {}
            if _new_oid_irg:
                _rest_by_oid_irg[_new_oid_irg] = restaurant
            for _b_irg in bag_raw or []:
                _boid = str(_b_irg.get("order_id") or "")
                if _boid:
                    _rest_by_oid_irg[_boid] = _b_irg.get("restaurant")
            _plan_pickup_at_irg = getattr(plan, "pickup_at", None) or {}
            _pickups_irg = []
            for _oid_irg, _pat_raw in _plan_pickup_at_irg.items():
                try:
                    _pat_dt_irg = (
                        _dt_irg.fromisoformat(str(_pat_raw))
                        if isinstance(_pat_raw, str) else _pat_raw
                    )
                    if _pat_dt_irg.tzinfo is None:
                        _pat_dt_irg = _pat_dt_irg.replace(tzinfo=timezone.utc)
                    _pickups_irg.append((_pat_dt_irg, str(_oid_irg)))
                except Exception:
                    continue
            _pickups_irg.sort(key=lambda x: x[0])
            for _i_irg in range(len(_pickups_irg) - 1):
                _t1, _o1 = _pickups_irg[_i_irg]
                _t2, _o2 = _pickups_irg[_i_irg + 1]
                _r1 = _rest_by_oid_irg.get(_o1)
                _r2 = _rest_by_oid_irg.get(_o2)
                if _r1 is None or _r2 is None or _r1 != _r2:
                    continue
                _gap_irg = (_t2 - _t1).total_seconds() / 60.0
                if _gap_irg > intra_rest_gap_max_min:
                    intra_rest_gap_max_min = _gap_irg
                    intra_rest_gap_max_pair = (_o1, _o2)
                    intra_rest_gap_max_restaurant = _r1
                if _gap_irg > C.MAX_INTRA_RESTAURANT_GAP_MIN:
                    intra_rest_gap_hard_reject = True

        # R-LATE-PICKUP (2026-05-31, Adrian): max 5 min spóźnienia na ODBIÓR.
        # Dwie nienaruszalne reguły: (1) 5 min spóźnienie odbioru [tu], (2) 35 min
        # doręczenie [R6 BAG_TIME_HARD_MAX_MIN]. Liczone z plan.pickup_at vs ref.
        # DWA osobne pomiary (Adrian 2026-05-31 — patrz feedback memory):
        #   • COMMITTED bag-order (już zadeklarowany czas_kuriera): spóźnienie >5 =
        #     „złamana obietnica" → kandydat demotowany do najniższego tieru (NIE bierze
        #     tego zlecenia jeśli jest ktokolwiek lepszy; przypadek 477237 Rukola).
        #   • NOWY order (vs pickup_ready / firm-commit): spóźnienie >5 NIE wyklucza —
        #     sygnalizuje „trzeba przedłużyć czas odbioru". Selekcja (niżej) preferuje
        #     kandydatów na czas; gdy brak → najszybszy + propozycja przedłużonego czasu.
        # Selekcja = tiering (NIE hard-reject) → ZAWSZE jest propozycja (reguła Adriana
        # „zawsze daje propozycje"). Post-solve (NIE okno TSP — lekcja E3). Metryki
        # liczone ZAWSZE; tiering aktywny tylko gdy ENABLE_LATE_PICKUP_HARD_GATE.
        late_pickup_max_min = 0.0            # max(committed, new) — ciągłość shadow
        late_pickup_committed_max = 0.0      # tylko bag-committed (czas_kuriera)
        late_pickup_committed_worst_oid = None
        late_pickup_committed_worst_restaurant = None
        new_pickup_late_min = 0.0            # nowy order vs jego ref
        new_pickup_eta_iso = None            # ETA odbioru nowego (render + „najszybszy")
        new_pickup_needs_extension = False   # nowy >5 → propozycja przedłużonego czasu
        late_pickup_committed_breach = False  # committed >5 → tier ostateczny
        if plan is not None:
            from datetime import datetime as _dt_lp
            _LP_LIMIT = getattr(C, "LATE_PICKUP_HARD_MAX_MIN", 5.0)
            _new_oid_lp = str(getattr(new_order, "order_id", "") or "")
            _plan_pickup_at_lp = getattr(plan, "pickup_at", None) or {}

            def _parse_lp(_raw):
                try:
                    _d = (_dt_lp.fromisoformat(str(_raw).replace("Z", "+00:00"))
                          if isinstance(_raw, str) else _raw)
                    return _d if _d.tzinfo else _d.replace(tzinfo=timezone.utc)
                except (TypeError, ValueError, AttributeError):
                    return None

            # COMMITTED bag-orders: spóźnienie vs zadeklarowany czas_kuriera.
            for _b_lp in bag_raw or []:
                _boid_lp = str(_b_lp.get("order_id") or "")
                _ref_dt_lp = _parse_lp(_b_lp.get("czas_kuriera_warsaw"))
                _pat_dt_lp = _parse_lp(_plan_pickup_at_lp.get(_boid_lp))
                if not _boid_lp or _ref_dt_lp is None or _pat_dt_lp is None:
                    continue
                _late_lp = (_pat_dt_lp - _ref_dt_lp).total_seconds() / 60.0
                if _late_lp > late_pickup_committed_max:
                    late_pickup_committed_max = _late_lp
                    late_pickup_committed_worst_oid = _boid_lp
                    late_pickup_committed_worst_restaurant = _b_lp.get("restaurant")

            # NOWY order: ETA odbioru + spóźnienie vs ref (firm-commit | pickup_ready).
            if _new_oid_lp:
                _new_ref_lp = _parse_lp(order_event.get("czas_kuriera_warsaw")) or _parse_lp(pickup_ready_at)
                _new_pat_dt = _parse_lp(_plan_pickup_at_lp.get(_new_oid_lp))
                if _new_pat_dt is not None:
                    new_pickup_eta_iso = _new_pat_dt.astimezone(timezone.utc).isoformat()
                if _new_ref_lp is not None and _new_pat_dt is not None:
                    new_pickup_late_min = (_new_pat_dt - _new_ref_lp).total_seconds() / 60.0

            late_pickup_max_min = max(late_pickup_committed_max, new_pickup_late_min)
            if getattr(C, "ENABLE_LATE_PICKUP_HARD_GATE", False):
                late_pickup_committed_breach = late_pickup_committed_max > _LP_LIMIT
                new_pickup_needs_extension = new_pickup_late_min > _LP_LIMIT

        # Wczytaj rule_weights (adaptive penalties R1/R5/R8) — B2: cached + głośny log na fail.
        _rw = _load_rule_weights()

        # R1 soft penalty (delivery spread violation)
        _r1_viol = metrics.get("r1_violation_km") or 0.0
        bonus_r1_soft_pen = _r1_viol * _rw.get("R1_spread_per_km", -8.0) if _r1_viol > 0 else 0.0

        # R5 soft penalty (mixed pickup spread violation)
        _r5_viol = metrics.get("r5_violation_km") or 0.0
        bonus_r5_soft_pen = _r5_viol * _rw.get("R5_pickup_per_km", -6.0) if _r5_viol > 0 else 0.0

        # V3.28 P1 — R1 directionality (corridor) bonus/penalty — Adrian doktryna 2026-05-10.
        # avg_pairwise_cosine wektorów courier→drop:
        #  >0.85 = tight corridor (wszystkie w jednym kierunku)
        #   0..0.5 = neutralne / lekko rozbieżne
        #  -0.5..0 = orthogonal (drops w bok)
        #  <-0.5 = opposite (split bag w przeciwne strony) — to chcemy karać mocno
        #
        # P3-D5 2026-05-11: bucket -0.5..0 tighten -15 → -35 (case 472338 Ogniomistrz
        # cos=-0.326 deliv_spread=12.63km — wcześniej za łagodna penalty pozwalała
        # przejść geometric anti-pattern). Plus deliv_spread_km multiplier dla wide
        # drops (>8 km): linear scale 8→1.0x, 16+→2.0x. Tylko negative bucket — bonus
        # pozostaje bez zmiany.
        _r1_avg_cos = metrics.get("r1_avg_pairwise_cosine")
        bonus_r1_corridor = _r1_corridor_base_bonus(
            _r1_avg_cos,
            getattr(C, "ENABLE_R1_CORRIDOR_GRADIENT", False)
            or C.flag("ENABLE_R1_CORRIDOR_GRADIENT", False),
        )

        # P3-D5 2026-05-11: deliv_spread mnożnik dla wide drops (negative bucket only).
        # Case 472338 deliv_spread=12.63km → 1.578x mnożnik → -35 × 1.578 = -55.2.
        # Bonus (positive) NIE multiplied — tight corridor reward niezależny od spread.
        r1_corridor_spread_mult = 1.0
        if bonus_r1_corridor < 0:
            _r1_deliv_spread = metrics.get("deliv_spread_km")
            if _r1_deliv_spread is not None and _r1_deliv_spread > 8.0:
                r1_corridor_spread_mult = min(2.0, 1.0 + (_r1_deliv_spread - 8.0) * 0.125)
                bonus_r1_corridor = bonus_r1_corridor * r1_corridor_spread_mult

        # F5 RETURN-TO-RESTAURANT (2026-05-24) — zakazany powrót do tej samej
        # restauracji niosąc jej dowóz (Case B korpusu). Detekcja w feasibility_v2
        # (commit-aware), tu silna kara dominująca (deprioryzuje kuriera; NIE hard
        # veto — gdy jedyny kandydat, dostawa > brak dostawy, R-FLEET-LEVEL).
        bonus_r_return_rest = 0.0
        if metrics.get("return_to_restaurant"):
            bonus_r_return_rest = -float(getattr(C, "RETURN_TO_RESTAURANT_PENALTY", 100.0))

        # V3.28 P1 — R5 pickup detour per order — Adrian doktryna 2026-05-10.
        # detour_per_pickup_km = ile dodatkowego km każdy pickup płaci za udział w bagu
        # (vs solo pickup). Galeria Biała "po drodze" do Wasilkowa → ~0 detour → 0 penalty.
        # Pickupy na przeciwnych końcach miasta → detour 5+ km → -40 penalty.
        _r5_detour = metrics.get("r5_pickup_detour_per_order_km")
        bonus_r5_detour = 0.0
        if _r5_detour is not None:
            if _r5_detour < 0.5:
                bonus_r5_detour = 0.0
            elif _r5_detour < 1.5:
                bonus_r5_detour = -5.0
            elif _r5_detour < 3.0:
                bonus_r5_detour = -15.0
            else:
                bonus_r5_detour = -40.0

        # V3.28 P2 — wave clean bonus + inter-wave deadhead penalty (Adrian doktryna 2026-05-10).
        # Wave = burst pickupów (12 min + 1.5 km) → burst dropów. Bag z 1 falą = "linia"
        # (Adrian's idealny model). 2+ fale = OK gdy deadhead między falami sensowny.
        # Filozofia: nie blokujemy multi-wave bagów (peak day rescue), karzemy tylko
        # nadmiarowy deadhead (>4 km) między falami.
        _n_waves = metrics.get("n_waves") or 0
        _inter_wave_max = metrics.get("inter_wave_deadhead_max_km") or 0.0
        bonus_wave_clean = 0.0
        bonus_inter_wave_deadhead = 0.0
        if _n_waves == 1 and (len(_bag_dicts) >= 1 if "_bag_dicts" in dir() else True):
            bonus_wave_clean = 10.0  # 1 fala = atomic burst, idealnie
        elif _n_waves >= 2 and _inter_wave_max > 4.0:
            # Penalty -3/km nadmiar nad 4 km deadhead (najgorszy segment)
            bonus_inter_wave_deadhead = -3.0 * (_inter_wave_max - 4.0)

        # V3.28 P4 — coordinator hybrid duty penalty (Adrian doktryna 2026-05-10 wieczór).
        # Coordinator (Bartek O. cid=123) jeździ tylko aktywnie. Off-peak / brak fali
        # = NIE jeździ. Pipeline default proponował go zawsze (gold tier +100).
        # Activation: auto na pierwszym COURIER_ASSIGNED dnia (state_machine hook) LUB
        # manual TG `<nick> start/stop`. Reset 06:00 daily.
        bonus_coordinator_idle = 0.0
        _is_coord = bool(getattr(cs, "is_coordinator", False))
        _coord_active = bool(getattr(cs, "coordinator_active", False))
        if _is_coord and not _coord_active:
            bonus_coordinator_idle = -100.0  # Strong demote — koord nie jeździ aktywnie

        # V3.28 P3 (B) — state-vs-panel mismatch penalty (Adrian doktryna 2026-05-10).
        # Gdy panel widzi kuriera z bag (nick→[oids] w panel_packs_cache) ALE
        # orders_state ma jego bag pusty (cid=None lag) — silna kara, kurier
        # faktycznie wozi mimo że pipeline myśli że jest wolny. Diagnoza 472242
        # Baanko 17:41: Mateusz O bag=0 w state, mimo 7 queued w panelu (PACKS_CATCHUP
        # lag 11s). Selektywna kara (tylko gdy konkretny dowód state-stale)
        # vs uniwersalny penalty no_gps (Adrian rejected — czasem no_gps może być legit).
        _panel_packs_signal = getattr(cs, "panel_packs_oids_signal", []) or []
        _state_bag_size = len(_bag_dicts) if "_bag_dicts" in dir() else 0
        _panel_packs_age_s = getattr(cs, "panel_packs_cache_age_s", None)
        bonus_state_panel_mismatch = 0.0
        if (
            _panel_packs_age_s is not None
            and _panel_packs_age_s <= 120.0
            and len(_panel_packs_signal) > 0
            and _state_bag_size == 0
        ):
            # Mocna kara — kurier ma realny bag, state stale. -50 per phantom oid
            # (max -200 dla 4+ orderów = bardzo gorzej niż score=82 baseline).
            bonus_state_panel_mismatch = -50.0 * min(len(_panel_packs_signal), 4)

        # R8 soft penalty (pickup span — oryginalna + violation)
        _r8_span = metrics.get("r8_pickup_span_min") or 0
        bonus_r8_soft_pen = (
            -(_r8_span - C.PICKUP_SPAN_SOFT_START_MIN) * C.PICKUP_SPAN_SOFT_PENALTY_PER_MIN
            if _r8_span > C.PICKUP_SPAN_SOFT_START_MIN else 0.0
        )
        _r8_viol = metrics.get("r8_violation_min") or 0.0
        bonus_r8_soft_pen += _r8_viol * _rw.get("R8_span_per_min", -1.5) if _r8_viol > 0 else 0.0

        # V3.19h BUG-2: wave continuation bonus.
        # Gold tier pattern: interleave pickup wave #2 przed ukończeniem wave #1.
        # Bonus gdy pickup_new pasuje do projected free_at (last bag drop).
        # Source of truth dla free_at_dt: plan.predicted_delivered_at[last_bag_oid]
        # (spójny sticky V3.19d / V3.19e pre_pickup / fresh TSP).
        # pickup_at: V3.19f first-choice czas_kuriera_warsaw → pickup_at_warsaw.
        bug2_interleave_gap_min = None
        bonus_bug2_continuation = 0.0
        bug2_pickup_src = "ready_time"
        if C.ENABLE_V319H_BUG2_WAVE_CONTINUATION:
            # FIX 1 (2026-05-22): gap z REALNEGO zaplanowanego odbioru plan.pickup_at[new],
            # nie z gotowości jedzenia. Elastyk gotowy wcześnie → ready-time daje gap
            # ~zawsze ujemny → phantom +30 dla DRUGIEJ FALI (475235: Michał K real odbiór
            # 12:56 vs free 12:46 = +10 nowa fala; ready-time dawał -6.5 → +30). Default OFF.
            _bug2_pu = pickup_at
            if getattr(C, "ENABLE_BUG2_GAP_FROM_PLAN", False) and plan is not None:
                _pp_iso = (getattr(plan, "pickup_at", None) or {}).get(str(order_id))
                if _pp_iso:
                    try:
                        _bug2_pu = datetime.fromisoformat(str(_pp_iso).replace("Z", "+00:00"))
                        bug2_pickup_src = "plan_pickup_at"
                    except Exception as _b2e:
                        log.warning(
                            f"BUG2_GAP_FROM_PLAN parse fail order={order_id} "
                            f"cid={cid} val={_pp_iso!r}: {_b2e}"
                        )
            if free_at_dt is not None and _bug2_pu is not None:
                _pu_utc = _bug2_pu if _bug2_pu.tzinfo else _bug2_pu.replace(tzinfo=WARSAW)
                _pu_utc = _pu_utc.astimezone(timezone.utc)
                _fa_utc = free_at_dt if free_at_dt.tzinfo else free_at_dt.replace(tzinfo=timezone.utc)
                _gap_sec = (_pu_utc - _fa_utc).total_seconds()
                bug2_interleave_gap_min = round(_gap_sec / 60.0, 2)
                bonus_bug2_continuation = C.bug2_wave_continuation_bonus(
                    bug2_interleave_gap_min
                )
            # edge: bag empty albo pickup=None → gap=None, bonus=0 (default)

        # BUNDLE-DELIVERY-COLOCATION (Adrian 2026-06-26, case 509 Street Mama Thai+Raj):
        # forced-bundle wynika z 2 TWARDYCH reguł, nie z miękkiej geometrii pickupów.
        # Gdy nowa dostawa skolokowana z dostawą w bagu (różne restauracje, ten sam
        # adres) ORAZ R6 czyste (≤35) ORAZ committed honorowane (±5) → kredyt + gate
        # veta/FIX_C (to co-pickup wymuszony regułami, nie nawrót). L1/L2 (pickup-
        # centryczne) dają tu 0 → ta luka. Default OFF (decision_flag).
        bundle_deliv_coloc_km, bundle_deliv_coloc_active, bonus_deliv_coloc = (
            compute_bundle_deliv_coloc(
                bag_raw, delivery_coords, metrics, late_pickup_committed_breach,
                flag_on=C.decision_flag("ENABLE_BUNDLE_DELIVERY_COLOCATION"),
                km_threshold=C.BUNDLE_DELIV_COLOC_KM,
                bonus_max=C.BUNDLE_DELIV_COLOC_BONUS_MAX,
                r6_hard_max=C.BAG_TIME_HARD_MAX_MIN,
                level1=bundle_level1, level2=bundle_level2,
                centroid_guard=C.decision_flag("ENABLE_BUNDLE_COLOC_CENTROID_GUARD")))
        if bundle_deliv_coloc_active:
            bundle_bonus = bundle_bonus + bonus_deliv_coloc
            log.info(
                f"BUNDLE_DELIV_COLOC order={order_id} cid={cid} "
                f"drop_dist={bundle_deliv_coloc_km:.3f}km "
                f"r6={metrics.get('r6_max_bag_time_min')} "
                f"commit_breach={late_pickup_committed_breach} "
                f"+{bonus_deliv_coloc:.1f}")

        # V3.26 STEP 3 (R-09 WAVE-GEOMETRIC-VETO): refinement BUG-2.
        # Veto bonus gdy geometryczna incoherence: km(last_drop → new_pickup) > threshold.
        # Bug case Adrian Q&A 22.04 Kacper Sa: gap OK ale drops na 2 końcach miasta.
        # BUNDLE-DELIVERY-COLOCATION: nie wetuj gdy dostawa skolokowana (co-pickup
        # wymuszony committed+R6, nie nawrót).
        v326_wave_veto = False
        v326_wave_geometric_km = None
        if (C.ENABLE_V326_WAVE_GEOMETRIC_VETO and bonus_bug2_continuation > 0
                and not bundle_deliv_coloc_active
                and plan is not None and bag_raw):
            try:
                pda = plan.predicted_delivered_at or {}
                bag_oids_set = {str(b.get("order_id")) for b in bag_raw if b.get("order_id")}
                bag_pda = [(oid, ts) for oid, ts in pda.items() if str(oid) in bag_oids_set]
                if bag_pda:
                    _last_oid = max(bag_pda, key=lambda x: x[1])[0]
                    _last_drop = None
                    for _b in bag_raw:
                        if str(_b.get("order_id")) == str(_last_oid):
                            _last_drop = _b.get("delivery_coords")
                            break
                    _new_pickup = getattr(new_order, "pickup_coords", None)
                    # L2.1: truthy-guard NIE łapał [0,0] → haversine raise.
                    if _coords_pass(bool(_last_drop and _new_pickup),
                                    _last_drop, _new_pickup):
                        v326_wave_geometric_km = haversine(
                            tuple(_last_drop), tuple(_new_pickup)
                        )
                        if v326_wave_geometric_km > C.V326_WAVE_VETO_KM_THRESHOLD:
                            v326_wave_veto = True
                            log.info(
                                f"V326_WAVE_VETO order={order_id} cid={cid} "
                                f"km_from_last_drop={v326_wave_geometric_km:.2f} > "
                                f"{C.V326_WAVE_VETO_KM_THRESHOLD} — bonus "
                                f"+{bonus_bug2_continuation:.1f} VETOED"
                            )
                            bonus_bug2_continuation = 0.0
            except Exception as _ve:
                log.warning(f"V326_WAVE_VETO compute fail order={order_id} cid={cid}: {_ve}")

        # FIX 2 (2026-05-22, R-09 oś nowej DOSTAWY): veto bonusu kontynuacji gdy nowa
        # dostawa opuszcza korytarz bagu — daleko od centroidu dostaw I rozbieżna
        # kierunkowo. Domyka ślepą plamę: R-09 mierzy odbiór (475235 last_drop→Raj 0.98km
        # OK), FIX_C cały spread (5.01km<8 OK), a pojedyncza daleka rozbieżna dostawa
        # (Hallera 3.25km NW, cos≈-0.39) wpada między progi i utrzymuje phantom +30.
        v326_wave_veto_newdrop = False
        if (getattr(C, "ENABLE_V326_WAVE_VETO_NEW_DROP", False)
                and bonus_bug2_continuation > 0
                and not bundle_deliv_coloc_active):
            _nd_km = metrics.get("r1_new_drop_dist_km")
            _nd_cos = metrics.get("r1_new_drop_cosine")
            if (_nd_km is not None and _nd_cos is not None
                    and _nd_km > C.V326_WAVE_VETO_NEW_DROP_KM
                    and _nd_cos < C.V326_WAVE_VETO_NEW_DROP_COS):
                v326_wave_veto_newdrop = True
                log.info(
                    f"V326_WAVE_VETO_NEWDROP order={order_id} cid={cid} "
                    f"new_drop_km={_nd_km:.2f}>{C.V326_WAVE_VETO_NEW_DROP_KM} "
                    f"cos={_nd_cos:.2f}<{C.V326_WAVE_VETO_NEW_DROP_COS} — bonus "
                    f"+{bonus_bug2_continuation:.1f} VETOED"
                )
                bonus_bug2_continuation = 0.0

        # V3.28 FIX_C: Bundle deliv_spread hard cap (FILOZ-3 peak-safe gate).
        # Cross-restaurant bundle scoring (bonus_l2 cross-pickup proximity + bug2
        # continuation) currently NIE patrzy na deliv_spread. Drops w przeciwnych
        # częściach miasta dostają full bonus pomimo trasy chaotic (#469834).
        # Gate zeruje bonus_l2 + bonus_bug2_continuation gdy bag>=1 i deliv_spread
        # przekracza cap. bonus_l1 SR pozostaje (osobny mechanizm, drop_proximity
        # SR-only już guarded). Default OFF (env ENABLE_BUNDLE_DELIV_SPREAD_CAP=1).
        fix_c_applied = False
        fix_c_deliv_spread_km = metrics.get("deliv_spread_km")
        if (C.decision_flag("ENABLE_BUNDLE_DELIV_SPREAD_CAP")
                and not bundle_deliv_coloc_active
                and len(bag_raw) >= 1
                and fix_c_deliv_spread_km is not None
                and fix_c_deliv_spread_km > C.BUNDLE_MAX_DELIV_SPREAD_KM):
            if bonus_l2 != 0.0 or bonus_bug2_continuation != 0.0:
                log.info(
                    f"FIX_C bundle_cap order={order_id} cid={cid} "
                    f"deliv_spread={fix_c_deliv_spread_km:.2f}km > "
                    f"cap={C.BUNDLE_MAX_DELIV_SPREAD_KM}km → "
                    f"zero bonus_l2={bonus_l2:.1f} continuation={bonus_bug2_continuation:.1f}"
                )
                fix_c_applied = True
            bonus_l2 = 0.0
            bonus_bug2_continuation = 0.0
            # Recompute bundle_bonus po zero bonus_l2 (bonus_l1, bonus_r4 unchanged).
            bundle_bonus = bonus_l1 + bonus_l2 + bonus_r4

        # === BUNDLE-03 (Front D audytu 03.06, 2026-06-12): FIX_C addytywnie ===
        # Zerowanie bonusów = no-op dla najgorszych worków (przeciw-kierunkowe,
        # różne restauracje — NIE MAJĄ bonus_l2/continuation do wyzerowania;
        # case #469834, do którego FIX_C był pisany). Shadow: addytywna kara
        # liczona ZAWSZE — (a) spread>cap: −PEN·(spread−cap); (b) cos<TRIGGER
        # (przeciwny kierunek nowego dropu): −PEN·spread PEŁNY (zły kierunek
        # czyni każdy rozrzut kosztownym). Aplikacja za 🛑
        # ENABLE_FIX_C_ADDITIVE_PENALTY (decision_flag, flags.json=false; E7).
        fix_c_additive_pen_shadow = 0.0
        if len(bag_raw) >= 1 and fix_c_deliv_spread_km is not None:
            _fl_fc = C.load_flags()
            _fc_pen = float(_fl_fc.get(
                "FIX_C_ADDITIVE_PEN_PER_KM", C.FIX_C_ADDITIVE_PEN_PER_KM))
            _fc_cos_trig = float(_fl_fc.get(
                "FIX_C_ADDITIVE_COS_TRIGGER", C.FIX_C_ADDITIVE_COS_TRIGGER))
            _fc_cos = metrics.get("r1_new_drop_cosine")
            _fc_over = max(0.0, fix_c_deliv_spread_km - C.BUNDLE_MAX_DELIV_SPREAD_KM)
            if isinstance(_fc_cos, (int, float)) and _fc_cos < _fc_cos_trig:
                fix_c_additive_pen_shadow = round(
                    -_fc_pen * fix_c_deliv_spread_km, 2)
            elif _fc_over > 0.0:
                fix_c_additive_pen_shadow = round(-_fc_pen * _fc_over, 2)

        # === BUNDLE-06 Faza 1 / BUNDLE-02 (Front D, 2026-06-12): bundle_fit ===
        # 80,2% proponowanych worków ma zerowy bundle bonus — brak bonusu ≠
        # kara, worek wygrywa „za darmo" bazowym score bliskości. Faza 1 per
        # REKO audytu: scal ISTNIEJĄCE sygnały (zero nowych OSRM) w jedną deltę:
        #   + W_COS·r1_new_drop_cosine                      [kierunek; None→0]
        #   − THERMAL_PER_MIN·max(0, objm_max_thermal − FREE)  [koszt świeżości]
        #   − SPAN_PER_MIN·max(0, r8_pickup_span − FREE)    [rozstrzał odbiorów]
        # Delta ZAWSZE (lekcja #186); do score TYLKO za 🛑
        # ENABLE_BUNDLE_VALUE_SCORING (reaktywacja flagi V3.18 per BUNDLE-08 —
        # tym razem z konsumentem; decision_flag, flags.json=false; wagi = E7).
        # Osobno bundle_fit_marginal_min = plan_total − free_at (ile minut
        # NAPRAWDĘ dokłada ten order TEMU kurierowi) — czysta telemetria dla
        # E7, świadomie POZA deltą (nakłada się z S_dystans, wymaga studium).
        bundle_fit_shadow = None
        bonus_bundle_fit_shadow_delta = 0.0
        bundle_fit_marginal_min = None
        _bf_plan = metrics.get("plan")
        _bf_total = (_bf_plan.get("total_duration_min")
                     if isinstance(_bf_plan, dict)
                     else getattr(_bf_plan, "total_duration_min", None))
        if isinstance(_bf_total, (int, float)):
            bundle_fit_marginal_min = round(
                max(0.0, float(_bf_total) - float(free_at_min or 0.0)), 1)
        if len(bag_raw) >= 1:
            _fl_bf = C.load_flags()
            _bf_cos = metrics.get("r1_new_drop_cosine")
            _bf_thermal = metrics.get("objm_max_thermal_age_min")
            _bf_span = metrics.get("r8_pickup_span_min")
            _bf = 0.0
            if isinstance(_bf_cos, (int, float)):
                _bf += float(_fl_bf.get(
                    "BUNDLE_FIT_W_COS", C.BUNDLE_FIT_W_COS)) * float(_bf_cos)
            if isinstance(_bf_thermal, (int, float)):
                _bf -= float(_fl_bf.get(
                    "BUNDLE_FIT_THERMAL_PER_MIN", C.BUNDLE_FIT_THERMAL_PER_MIN)) * max(
                    0.0, float(_bf_thermal) - float(_fl_bf.get(
                        "BUNDLE_FIT_THERMAL_FREE_MIN", C.BUNDLE_FIT_THERMAL_FREE_MIN)))
            if isinstance(_bf_span, (int, float)):
                _bf -= float(_fl_bf.get(
                    "BUNDLE_FIT_SPAN_PER_MIN", C.BUNDLE_FIT_SPAN_PER_MIN)) * max(
                    0.0, float(_bf_span) - float(_fl_bf.get(
                        "BUNDLE_FIT_SPAN_FREE_MIN", C.BUNDLE_FIT_SPAN_FREE_MIN)))
            bundle_fit_shadow = round(_bf, 2)
            bonus_bundle_fit_shadow_delta = bundle_fit_shadow

        # === SP-B2-SYNCWORKA H1 (2026-06-11): spread gotowości worka ===
        # Metryki ZAWSZE liczone (observability/replay); wpływ na score TYLKO
        # gdy ENABLE_BUNDLE_SYNC_SPREAD (decision_flag, default OFF, 🛑 ACK).
        # Delta = kara gradientowa + (przy spreadzie >10 min) zerowanie
        # dodatnich bonusów bundlowych (bundle_bonus + continuation) wzorem
        # Fix C — liczona PO Fix C, żeby nie zerować podwójnie.
        sync_ready_spread_min, sync_spread_n = _compute_sync_spread(
            bag_sim, bag_raw, pickup_ready_at, order_event.get("restaurant"), now)
        bonus_sync_spread = 0.0
        sync_spread_bundle_zeroed = False
        bonus_sync_spread_shadow_delta = 0.0
        if sync_ready_spread_min is not None:
            bonus_sync_spread = round(_sync_spread_penalty(sync_ready_spread_min), 2)
            bonus_sync_spread_shadow_delta = bonus_sync_spread
            if sync_ready_spread_min > float(getattr(C, "SYNC_SPREAD_BUNDLE_ZERO_MIN", 10.0)):
                _sync_zero_part = max(0.0, bundle_bonus) + max(0.0, bonus_bug2_continuation)
                if _sync_zero_part > 0.0:
                    sync_spread_bundle_zeroed = True
                    bonus_sync_spread_shadow_delta = round(
                        bonus_sync_spread - _sync_zero_part, 2)

        # === SP-B2-REPO (2026-06-11): koszt repozycjonowania (dead-head) ===
        # km(drop poprzedzający nowy odbiór w planie → nowy pickup) — ukryta
        # połowa kilometrów (raport §3.1.4, mediana 3,56 km). Telemetria za
        # ENABLE_REPO_COST_SHADOW (ON); aplikacja do score za 🛑
        # ENABLE_REPO_COST_LIVE (decision_flag, OFF). Odbiór przed dropami /
        # pusty bag → None (km_to_pickup już wycenia — bez podwójnego liczenia).
        repo_km = None
        repo_last_drop_oid = None
        bonus_repo_cost_shadow_delta = 0.0
        if C.flag("ENABLE_REPO_COST_SHADOW", True):
            repo_km, repo_last_drop_oid = _compute_repo_cost_km(
                bag_sim, plan, order_id, pickup_coords)
            if repo_km is not None:
                bonus_repo_cost_shadow_delta = round(_repo_cost_penalty(repo_km), 2)

        # === SP-B2-LOADGOV (2026-06-11): kara za dokładanie do pełnych toreb
        # przy przeciążonej flocie. Delta zawsze liczona (shadow); aplikacja
        # za 🛑 flagą niżej. Miękki odpowiednik "tighten capów o 1".
        bonus_loadgov_shadow_delta = 0.0
        if (loadgov_ewma is not None
                and loadgov_ewma > float(getattr(C, "LOADGOV_TIGHTEN_AT", 2.7))
                and len(bag_raw) >= int(getattr(C, "LOADGOV_BAG_MIN", 3))):
            bonus_loadgov_shadow_delta = float(getattr(C, "LOADGOV_BAG_PENALTY", -40.0))

        # === P(breach)-GOVERNANCE shadow (2026-06-14): kandydat na zastąpienie
        # binarnego progu load (test 06-14: knee NIE istnieje, mean ewma breach≈
        # on-time) ciągłym P(breach) z pln_objective (km+worek dominują, load
        # najsłabszy 0.090). Compute+log ZAWSZE; NIE dodawane do final_score =
        # czysta telemetria pod replay-kalibrację (aplikacja = osobny flip + ACK).
        # Defensive try/except → 0.0 (NIGDY nie wywróci hot-path, Lekcja #32).
        pbreach_gov = None
        bonus_pbreach_gov_shadow_delta = 0.0
        try:
            _km_pb = repo_km if repo_km is not None else km_to_pickup_haversine
            if _km_pb is not None and loadgov_ewma is not None:
                pbreach_gov = pln_objective.p_breach(
                    float(_km_pb), len(bag_raw) + 1, float(loadgov_ewma))
                bonus_pbreach_gov_shadow_delta = round(
                    -float(getattr(C, "PBREACH_GOV_COEFF", 40.0)) * pbreach_gov, 2)
        except Exception as _pbg_e:
            log.warning(f"pbreach_gov shadow fail cid={cid} order={order_id}: {_pbg_e!r}")

        # === R1 progresywny + V319H guard SHADOW (2026-05-28) ===
        # Cele:
        #   R1: cosine < -0.3 dostaje progresywnie mocniejszą karę niż flat
        #       clip (-35/-40) by łapać Z-route'y (#476749 Mieszka I,
        #       #476777 Sikorskiego).
        #   V319H: continuation_bonus (+30) nie ma sensu gdy drops się
        #       rozjeżdżają — zerujemy.
        # Wartości zawsze policzone (shadow logging); aplikacja do final_score
        # tylko gdy flagi ON. Empirycznie: 19 historycznych improvements vs
        # 2 maybe-regresje (KOORD-redirect mitigation niżej).
        try:
            bonus_r1_progressive_shadow_delta = _compute_r1_progressive_delta(
                _r1_avg_cos, bonus_r1_corridor)
        except Exception as _e:
            log.warning(f"_compute_r1_progressive_delta exception cid={cid} order={order_id}: {_e!r}")
            bonus_r1_progressive_shadow_delta = 0.0
        try:
            bonus_v319h_guard_shadow_delta = _compute_v319h_guard_delta(
                _r1_avg_cos, bonus_bug2_continuation)
        except Exception as _e:
            log.warning(f"_compute_v319h_guard_delta exception cid={cid} order={order_id}: {_e!r}")
            bonus_v319h_guard_shadow_delta = 0.0

        # V3.19h BUG-4: tier × pora bag cap soft penalty (progressive scaling).
        # Orthogonal do R6 hard bag_time. Flag gated (default False).
        bug4_tier_cap_used = None
        bug4_cap_violation = None
        bonus_bug4_cap_soft = 0.0
        if C.ENABLE_V319H_BUG4_TIER_CAP_MATRIX:
            _tier = getattr(cs, "tier_bag", None) or "std"
            _cap_override = getattr(cs, "tier_cap_override", None)
            _pora = C.bug4_pora_now(now)
            if isinstance(_cap_override, dict) and _pora in _cap_override:
                _cap = _cap_override[_pora]
            else:
                _cap = C.BUG4_TIER_CAP_MATRIX.get(_tier, C.BUG4_TIER_CAP_MATRIX["std"])[_pora]
            _bag_after = len(bag_sim) + 1
            bug4_cap_violation = max(0, _bag_after - _cap)
            bug4_tier_cap_used = f"{_tier}/{_pora}/{_cap}"
            bonus_bug4_cap_soft = C.bug4_soft_penalty(bug4_cap_violation)

        # Sprint 2 Etap 2.2 (2026-05-27): carry / bag-stack visibility penalty.
        # Forensic Agent D — KK dinner R6 breach 22.5% root cause = carry chain
        # (kurier z bag innej restauracji + długi ETA do nowego pickup → carry
        # 15-30 min). Pure helper z common.py — penalty proporcjonalny do drive_min;
        # hard reject feasibility-side gated przez flag + dinner + KK + chain>=2.
        # Default flag OFF — wymaga 14d shadow.
        bonus_carry_chain_penalty = 0.0
        carry_chain_stops = 0
        carry_chain_applied = False
        carry_chain_hard_rejected = False
        if C.ENABLE_CARRY_CHAIN_PENALTY:
            try:
                _bag_rests = [b.get("restaurant") for b in (bag_raw or [])]
                _new_rest = restaurant  # closure: order_event.get("restaurant") line 1319
                _eta_for_carry = float(drive_min or 0.0)
                _pen, _stops, _appl = C.carry_chain_penalty(
                    _bag_rests, _new_rest, _eta_for_carry,
                )
                bonus_carry_chain_penalty = _pen
                carry_chain_stops = _stops
                carry_chain_applied = _appl
                carry_chain_hard_rejected = C.carry_chain_hard_reject(
                    _stops, _new_rest, now_utc=now,
                )
            except Exception as _carry_e:
                # Defense-in-depth: helper exception NIE psuje score loop.
                try:
                    log.warning(
                        f"carry_chain_penalty exception cid={cid} order={order_id}: {_carry_e}"
                    )
                except Exception:
                    pass

        # Suma penalties (BUG-4 soft penalty dodany do puli)
        # V3.25 STEP B (R-01): pre-shift soft penalty z feasibility metrics
        bonus_v325_pre_shift_soft = float(metrics.get("v325_pre_shift_soft_penalty", 0) or 0)
        # Pre-shift kara GRADIENTOWA (Adrian 2026-06-24) — zastępuje stałą feasibility
        # dla kuriera pre_shift (logika: _pre_shift_gradient_penalty). Rygor „odbiór
        # nie przed zmianą" = osobno departure-clamp (≥ shift_start).
        if (C.decision_flag("ENABLE_PRE_SHIFT_GRADIENT_PENALTY")
                and getattr(cs, "pos_source", None) == "pre_shift"):
            _psp = _pre_shift_gradient_penalty(getattr(cs, "shift_start_min", 0), loadgov_ewma)
            if _psp is not None:
                bonus_v325_pre_shift_soft = _psp
                metrics["v325_pre_shift_soft_penalty"] = _psp   # spójność breakdown/serializacji
        # Sprint 1 NO-GPS-EQUAL (Adrian 2026-06-29 „bez kary przed zmianą"): kurier przed
        # zmianą = liczony RÓWNO. Gate PO obu źródłach kary (stała V325 + gradient) =
        # jeden autorytatywny punkt; default OFF = no-op. Szczegóły w _apply_pre_shift_equal_gate.
        bonus_v325_pre_shift_soft = _apply_pre_shift_equal_gate(bonus_v325_pre_shift_soft, metrics)
        # D2 (audyt 2026-05-28): soft penalty gdy grafik STALE (shift_end None z awarii pliku,
        # nie realnego braku shiftu). 0 gdy flag OFF lub grafik świeży. Default OFF → shadow.
        bonus_d2_stale_soft = float(metrics.get("d2_soft_penalty", 0) or 0)
        # P-7 higiena (audyt 2026-06-24): 19 termów kary w JEDNYM nazwanym słowniku zamiast
        # rozproszonej sumy — auditowalność „jaka kara zapaliła dla kandydata" w jednym miejscu
        # + łatwy log/breakdown. Zachowanie 1:1: ta sama kolejność (dict zachowuje insertion),
        # sum() startuje od 0 (0+x==x dokładnie dla float) → wynik bit-identyczny.
        bonus_penalty_terms = {
            "r6_soft_pen": (bonus_r6_soft_pen or 0.0),
            "r1_soft_pen": bonus_r1_soft_pen,
            "r5_soft_pen": bonus_r5_soft_pen,
            "r8_soft_pen": bonus_r8_soft_pen,
            "r9_stopover": bonus_r9_stopover,
            "r9_wait_pen": bonus_r9_wait_pen,
            "bug4_cap_soft": bonus_bug4_cap_soft,
            "v325_pre_shift_soft": bonus_v325_pre_shift_soft,
            "d2_stale_soft": bonus_d2_stale_soft,
            "v3273_wait_courier": bonus_v3273_wait_courier,
            "r1_corridor": bonus_r1_corridor,
            "r5_detour": bonus_r5_detour,
            "wave_clean": bonus_wave_clean,
            "inter_wave_deadhead": bonus_inter_wave_deadhead,
            "state_panel_mismatch": bonus_state_panel_mismatch,
            "coordinator_idle": bonus_coordinator_idle,
            "r_paczki_flex": bonus_r_paczki_flex,
            "r_return_rest": bonus_r_return_rest,
            "carry_chain_penalty": bonus_carry_chain_penalty,
        }
        bonus_penalty_sum = sum(bonus_penalty_terms.values())
        # V3.19h BUG-2: wave continuation to BONUS (positive). Dodajemy do bundle_bonus
        # (nie penalty_sum) żeby zachować czysty semantyczny split penalty vs bonus.
        # Integracja z final_score — patrz niżej.

        # Post-wave override (F2.1c): brak GPS + wszystkie picked_up + kończy ≤15 min
        # Kurier zaraz wraca do centrum → bonus scoring
        pos_source_effective = getattr(cs, "pos_source", "no_gps")
        all_picked_up = (
            len(bag_sim) > 0 and
            all(getattr(o, "status", "") == "picked_up" for o in bag_sim)
        )
        wave_bonus = 0.0
        if (all_picked_up and
                pos_source_effective != "gps" and
                free_at_min <= C.POST_WAVE_FREE_MAX_MIN):
            pos_source_effective = "post_wave"
            wave_bonus = C.POST_WAVE_BONUS_FAST
        elif (all_picked_up and
                pos_source_effective != "gps" and
                free_at_min <= 30):
            pos_source_effective = "post_wave"
            wave_bonus = C.POST_WAVE_BONUS_SLOW

        # V3.24-A: extension penalty + hard reject gdy extension > 60 min.
        # extension = eta_pickup_utc - pickup_ready_at (restaurant requested).
        # Dla pre_shift kurier eta_pickup_utc = shift_start (clamp aktywny w post-loop
        # override L920+); dla in-shift naive_eta. extension_penalty() w common.py:
        #   None → hard reject (> 60 min)
        #   0 / -10 / -50 / -100 / -200 → gradient.
        v324a_extension_min = None
        v324a_extension_penalty = 0
        v324a_extension_hard_reject = False
        if C.ENABLE_V324A_SCHEDULE_INTEGRATION and pickup_ready_at is not None:
            _pra_v324 = pickup_ready_at if pickup_ready_at.tzinfo else pickup_ready_at.replace(tzinfo=timezone.utc)
            _eta_v324 = eta_pickup_utc if eta_pickup_utc.tzinfo else eta_pickup_utc.replace(tzinfo=timezone.utc)
            v324a_extension_min = (_eta_v324 - _pra_v324).total_seconds() / 60.0
            _pen_v324 = C.extension_penalty(_eta_v324, _pra_v324)
            if _pen_v324 is None:
                v324a_extension_hard_reject = True
            else:
                v324a_extension_penalty = _pen_v324

        # Post-shift overrun (Adrian 2026-06-24): rosnąca kara za minuty, o jakie
        # DOWÓZ nowego ordera wypada PO końcu zmiany kuriera. Liczone NIEZALEŻNIE
        # od v324a_dropoff_excess_min (to ostatnie bywa None bo feasibility ucina
        # się na wcześniejszej bramce — case 483144 Kuba/Patryk). Metryka liczona
        # ZAWSZE (widoczność w shadow); wpływ na score/selekcję best_effort tylko
        # gdy ENABLE_POST_SHIFT_OVERRUN_PENALTY. Fail-open: brak shift_end / dropoff
        # → 0 (grafik mógł paść — nie karać na ślepo).
        post_shift_overrun_min = 0.0
        post_shift_overrun_penalty = 0.0
        _cs_shift_end = getattr(cs, "shift_end", None)
        if _cs_shift_end is not None and plan is not None:
            _pred_new = (getattr(plan, "predicted_delivered_at", None) or {}).get(
                getattr(new_order, "order_id", None))
            if _pred_new is not None:
                _se = _cs_shift_end if _cs_shift_end.tzinfo else _cs_shift_end.replace(tzinfo=timezone.utc)
                _pn = _pred_new if _pred_new.tzinfo else _pred_new.replace(tzinfo=timezone.utc)
                post_shift_overrun_min = round((_pn - _se).total_seconds() / 60.0, 2)
                post_shift_overrun_penalty = C.post_shift_overrun_penalty(post_shift_overrun_min)

        final_score = score_result["total"] + bundle_bonus + timing_gap_bonus + wave_bonus + bonus_penalty_sum + bonus_bug2_continuation + v324a_extension_penalty
        # Post-shift overrun: odjęcie kary od score TYLKO gdy flaga ON (shadow-first).
        if C.decision_flag("ENABLE_POST_SHIFT_OVERRUN_PENALTY") and post_shift_overrun_penalty:
            final_score = final_score - post_shift_overrun_penalty
        # BUG A+B shadow (2026-05-26): bag_time fairness + r5 detour. Wszystkie
        # cztery bonus_* są 0.0 gdy flagi OFF (default) → zero behavior change
        # dopóki flagi nie zostaną włączone (env override / hot-reload).
        final_score = (
            final_score
            + bonus_bag_time_sum
            + bonus_bag_time_max
            + bonus_fifo_violation
            + bonus_r5_pickup_detour_penalty
        )

        # === R1 progresywny + V319H guard apply (2026-05-28) ===
        # Defaults OFF — shadow-first. Delty zawsze policzone (linie ~2596),
        # tu dodajemy do final_score tylko gdy flagi ON.
        if C.decision_flag("ENABLE_R1_PROGRESSIVE_CLIP"):
            final_score = final_score + bonus_r1_progressive_shadow_delta
        if C.decision_flag("ENABLE_V319H_CONTINUATION_GUARD"):
            final_score = final_score + bonus_v319h_guard_shadow_delta
        # SP-B2-SYNCWORKA H1 (2026-06-11): delta liczona zawsze (wyżej, po Fix C),
        # aplikacja za flagą decyzyjną — shadow-first, flip 🛑 ACK Adriana.
        if C.decision_flag("ENABLE_BUNDLE_SYNC_SPREAD"):
            final_score = final_score + bonus_sync_spread_shadow_delta
        # SP-B2-REPO (2026-06-11): kara repozycjonowania — aplikacja za 🛑 flagą.
        if C.decision_flag("ENABLE_REPO_COST_LIVE"):
            final_score = final_score + bonus_repo_cost_shadow_delta
        # SP-B2-LOADGOV (2026-06-11): governor load floty — aplikacja za 🛑 flagą.
        if C.decision_flag("ENABLE_FLEET_LOAD_GOVERNOR"):
            final_score = final_score + bonus_loadgov_shadow_delta
        # BUNDLE-06 Faza 1 (2026-06-12): wartość worka — aplikacja za 🛑 flagą
        # (wagi kalibruje E7 at#131; delta zawsze policzona wyżej).
        if C.decision_flag("ENABLE_BUNDLE_VALUE_SCORING"):
            final_score = final_score + bonus_bundle_fit_shadow_delta
        # BUNDLE-03 (2026-06-12): FIX_C addytywna kara — aplikacja za 🛑 flagą.
        if C.decision_flag("ENABLE_FIX_C_ADDITIVE_PENALTY"):
            final_score = final_score + fix_c_additive_pen_shadow

        # V3.27 Bug Z Q5: SOFT bundle score multiplier dla cross-quadrant bag.
        # 0.0 (cross-quadrant) → score *= 0.1
        # 0.5 (adjacent) → score *= 0.7
        # 1.0 (same quadrant) → score *= 1.0 (unchanged)
        # Gated by flag (v327_bundle_score_mult=1.0 gdy flag=False lub empty bag).
        # Z-02 (audyt 2026-06-10, _v327_sign_guard_on): mnożnik <1.0 na UJEMNYM
        # score ODWRACA karę (−80×0.1=−8 bije −50 same-quadrant) → aplikuj
        # wyłącznie na dodatnim score; ujemny zostaje bez zmian (kary już działają).
        v327_score_pre_mult = final_score
        final_score, v327_mult_sign_guarded = C.apply_bundle_score_mult(
            final_score, v327_bundle_score_mult, _v327_sign_guard_on)

        # V3.19e Opcja B — R1' observability only, zero behavior change.
        # Dla propozycji z synthetic pos=last_assigned_pickup (kurier w drodze
        # do restauracji X) loguj hypothetical metric: czy floor drive_min >=
        # pickup_ready_delta_min by zmienił scoring? Raw pos_source (przed
        # post_wave override L654-663), bo post_wave zaciera sygnał.
        _pos_raw = getattr(cs, "pos_source", None)
        v319e_r1_prime_hypothetical = None
        if _pos_raw == "last_assigned_pickup":
            _drive_m = round(drive_min, 1)
            _ready_delta = round(time_to_pickup_ready, 1) if time_to_pickup_ready is not None else 0.0
            v319e_r1_prime_hypothetical = {
                "pos_source_raw": _pos_raw,
                "drive_min": _drive_m,
                "pickup_ready_delta_min": _ready_delta,
                "would_trigger_floor": _drive_m < _ready_delta,
                "hypothetical_min_eta_min": max(_drive_m, _ready_delta),
            }

        enriched_metrics = {
            **metrics,
            "score": score_result,
            "km_to_pickup": round(km_to_pickup_haversine, 2),
            # V3.26 Bug A complete: anchor restaurant for Telegram label clarification.
            "v326_anchor_restaurant": v326_anchor_restaurant,
            "v326_anchor_used": v326_anchor_used,
            "travel_min": round(travel_min, 1),
            # SP-B2-ETAQ shadow (2026-06-11): travel_min po kalibracji kwantylowej
            # pred→real (dispatch_state/eta_quantile_map.json, generator = tor
            # narzędziowy). None gdy mapy brak / flaga OFF. Czysta telemetria —
            # NIE wpływa na score/feasibility/verdict (flip = ENABLE_ETA_QUANTILE_LIVE,
            # osobny sprint za ACK). Serializer LOCATION A+B.
            "travel_min_cal": (
                calib_maps.eta_quantile_calibrate(travel_min, now)
                if C.flag("ENABLE_ETA_QUANTILE_SHADOW", True) else None
            ),
            "drive_min": round(drive_min, 1),
            "eta_pickup_utc": eta_pickup_utc.isoformat(),
            "eta_drive_utc": drive_arrival_utc.isoformat(),
            "eta_source": eta_source,
            "pos_source": getattr(cs, "pos_source", None),
            # FIX 2026-06-08: True gdy pozycja odtworzona z last-known-pos store
            # (kurier bez GPS uratowany z BIALYSTOK_CENTER fiction). Obserwowalność
            # dla harnessu — odróżnia rescue od żywego pos_source tego samego enum.
            "pos_from_store": getattr(cs, "pos_from_store", False),
            # Z-09 (audyt 2026-06-10): wiek pozycji w minutach (recent-fallback /
            # store-rescue); None dla żywego GPS/no_gps. Razem z pos_from_store
            # pozwala odróżnić świeży fix od repliki ze store w shadow_decisions.
            "pos_age_min": (
                round(getattr(cs, "pos_age_min"), 1)
                if getattr(cs, "pos_age_min", None) is not None else None),
            "shift_start_min": getattr(cs, "shift_start_min", None),
            # V3.24-A: default False (in-shift kurier — naive_eta > shift_start zawsze).
            # Post-loop override ustawia True dla pos_source=pre_shift (linie ~925).
            "v324a_pickup_clamped_to_shift_start": False,
            "bundle_level1": bundle_level1,
            "bundle_level2": bundle_level2,
            "bundle_level2_dist": bundle_level2_dist,
            "bundle_level3": bundle_level3,
            "bundle_level3_dev": bundle_level3_dev,
            "bonus_l1": round(bonus_l1, 2),
            "bonus_l2": round(bonus_l2, 2),
            "bonus_r4_raw": round(bonus_r4_raw, 2),
            "bonus_r4": round(bonus_r4, 2),
            "bundle_bonus": round(bundle_bonus, 2),
            # BUNDLE-DELIVERY-COLOCATION (Adrian 2026-06-26) obs
            "bundle_deliv_coloc_km": bundle_deliv_coloc_km,
            "bundle_deliv_coloc_active": bundle_deliv_coloc_active,
            "bonus_deliv_coloc": round(bonus_deliv_coloc, 2),
            # V3.27 Bug Z metrics (observability)
            "v327_min_drop_factor": v327_min_drop_factor,
            "v327_bundle_score_mult": round(v327_bundle_score_mult, 3) if v327_bundle_score_mult != 1.0 else 1.0,
            "v327_corridor_mult_applied": round(v327_corridor_mult_applied, 3),
            "v327_score_pre_mult": round(v327_score_pre_mult, 2) if v327_bundle_score_mult != 1.0 else None,
            "v327_drop_zones_audit": v327_drop_zones_audit,
            # Z-02 (audyt 2026-06-10): sign-guard + Unknown-split observability.
            "v327_min_drop_factor_known": v327_min_drop_factor_known,
            "v327_unknown_zone_present": v327_unknown_zone_present,
            "v327_mult_sign_guarded": v327_mult_sign_guarded,
            "timing_gap_bonus": round(timing_gap_bonus, 2),
            "timing_gap_min": round(gap_min, 1),
            "time_to_pickup_ready_min": round(time_to_pickup_ready, 1),
            "free_at_utc": free_at_dt.isoformat() if free_at_dt is not None else None,
            "wave_bonus": round(wave_bonus, 2),
            "pos_source": pos_source_effective,
            "free_at_min": round(free_at_min, 1),
            "sla_minutes_used": sla_minutes,
            # F2.1b/F2.1c penalties. R8 aktywne od F2.1c (T_KUR propagation step 1-4).
            "bonus_r6_soft_pen": (
                round(bonus_r6_soft_pen, 2)
                if bonus_r6_soft_pen is not None else None
            ),
            # Fix #6 (2026-05-31): liniowa (pre-danger) kara R6 dla shadow-porównania.
            "bonus_r6_soft_pen_legacy": (
                round(bonus_r6_soft_pen_legacy, 2)
                if bonus_r6_soft_pen_legacy is not None else None
            ),
            # E7 (2026-06-17): kara R6 PRZED capem — telemetria zombie-pickup (gdy != pen → ucapowane).
            "bonus_r6_soft_pen_raw": (
                round(bonus_r6_soft_pen_raw, 2)
                if bonus_r6_soft_pen_raw is not None else None
            ),
            "bonus_r1_soft_pen": round(bonus_r1_soft_pen, 2),
            "bonus_r5_soft_pen": round(bonus_r5_soft_pen, 2),
            "bonus_r8_soft_pen": round(bonus_r8_soft_pen, 2),
            "r1_violation_km": metrics.get("r1_violation_km", 0.0),
            "r5_violation_km": metrics.get("r5_violation_km", 0.0),
            # V3.28 P1 — R1 directionality + R5 pickup detour (Adrian doktryna 2026-05-10)
            "r1_avg_pairwise_cosine": metrics.get("r1_avg_pairwise_cosine"),
            # FIX 2 observability — izolowany kierunek + dystans nowej dostawy
            "r1_new_drop_dist_km": metrics.get("r1_new_drop_dist_km"),
            "r1_new_drop_cosine": metrics.get("r1_new_drop_cosine"),
            # F2 R1-WAVE-SCOPED (2026-05-24) — wholebag (przed) vs wave-scoped
            # (po). Gdy flaga ON: r1_avg_pairwise_cosine/r1_new_drop_cosine wyżej
            # = wave-scoped; r1_wholebag_* = stara wartość do porównania.
            "r1_wholebag_avg_pairwise_cosine": metrics.get("r1_wholebag_avg_pairwise_cosine"),
            "r1_wholebag_new_drop_cosine": metrics.get("r1_wholebag_new_drop_cosine"),
            "r1ws_open_drop_count": metrics.get("r1ws_open_drop_count"),
            "r5_pickup_detour_total_km": metrics.get("r5_pickup_detour_total_km"),
            "r5_pickup_detour_per_order_km": metrics.get("r5_pickup_detour_per_order_km"),
            "bonus_r1_corridor": round(bonus_r1_corridor, 2),
            "r1_corridor_spread_mult": round(r1_corridor_spread_mult, 3),  # P3-D5 observability
            "bonus_r5_detour": round(bonus_r5_detour, 2),
            # V3.28 P2 — wave detection (Adrian doktryna 2026-05-10)
            "n_waves": metrics.get("n_waves"),
            "inter_wave_deadhead_total_km": metrics.get("inter_wave_deadhead_total_km"),
            "inter_wave_deadhead_max_km": metrics.get("inter_wave_deadhead_max_km"),
            "inter_wave_n_segments": metrics.get("inter_wave_n_segments"),
            "bonus_wave_clean": round(bonus_wave_clean, 2),
            "bonus_inter_wave_deadhead": round(bonus_inter_wave_deadhead, 2),
            # V3.28 P3 (B) — state-vs-panel mismatch (Adrian doktryna 2026-05-10)
            "panel_packs_signal_size": len(_panel_packs_signal),
            "panel_packs_oids_signal": list(_panel_packs_signal[:8]),  # cap dla logu
            "panel_packs_cache_age_s": _panel_packs_age_s,
            "bonus_state_panel_mismatch": round(bonus_state_panel_mismatch, 2),
            # R-PACZKI-FLEX (2026-05-20): gradient penalty + paczka_is dla shadow obs.
            # Auto-propagated do shadow log przez prefix bonus_ + paczka_.
            "bonus_r_paczki_flex": round(bonus_r_paczki_flex, 2),
            # BUG A shadow (2026-05-26): bag_time fairness — Σ + max + FIFO.
            # Metryki ZAWSZE zbierane (observability), bonus_* tylko gdy flag ON.
            # Auto-propagated via prefix bonus_ w shadow_dispatcher.
            "sum_bag_time_min": round(sum_bag_time_min_v, 2),
            "max_bag_time_min": round(max_bag_time_min_v, 2),
            "fifo_violations": fifo_violations,
            "bonus_bag_time_sum": round(bonus_bag_time_sum, 2),
            "bonus_bag_time_max": round(bonus_bag_time_max, 2),
            "bonus_fifo_violation": round(bonus_fifo_violation, 2),
            # E7-doklejki 3+4 (2026-06-11): wersje _shadow liczone ZAWSZE
            # (lekcja #186) — bonus_* powyżej = zaaplikowane (0 przy OFF).
            "bonus_bag_time_sum_shadow": round(shadow_bag_time_sum, 2),
            "bonus_bag_time_max_shadow": round(shadow_bag_time_max, 2),
            "bonus_fifo_violation_shadow": round(shadow_fifo_violation, 2),
            # BUG B shadow (2026-05-26): pickup-not-on-route penalty.
            # r5_pickup_detour_total_km już wyżej w enriched_metrics.
            "bonus_r5_pickup_detour_penalty": round(bonus_r5_pickup_detour_penalty, 2),
            "bonus_r5_pickup_detour_penalty_shadow": round(shadow_r5_pickup_detour_penalty, 2),
            # DETOUR-01: marker ekstremalny (detour > R5_DETOUR_EXTREME_KM ∧
            # bag≥2) — explicit w shadow_dispatcher LOC A+B (bez prefiksu auto).
            "r5_detour_extreme": r5_detour_extreme,
            # R1 progresywny + V319H guard shadow (2026-05-28): delty
            # zawsze policzone (observability), score-application gated flagą.
            # Auto-propagated via prefix bonus_ w shadow_dispatcher.
            "bonus_r1_progressive_shadow_delta": round(bonus_r1_progressive_shadow_delta, 2),
            "bonus_v319h_guard_shadow_delta": round(bonus_v319h_guard_shadow_delta, 2),
            # SP-B2-SYNCWORKA H1 (2026-06-11): spread gotowości worka + kara
            # gradientowa + delta shadow (kara + zerowanie bonusów bundlowych
            # przy >10 min). Serializacja: L1.1 deny-list — każdy klucz metrics
            # trafia do shadow_decisions (LOCATION A+B), chyba że w
            # shadow_dispatcher._METRICS_EXCLUDE.
            "sync_ready_spread_min": sync_ready_spread_min,
            "sync_spread_n": sync_spread_n,
            "sync_spread_bundle_zeroed": sync_spread_bundle_zeroed,
            "bonus_sync_spread": bonus_sync_spread,
            "bonus_sync_spread_shadow_delta": bonus_sync_spread_shadow_delta,
            # BUNDLE-06 Faza 1 + BUNDLE-03 (Front D, 2026-06-12): wartość worka
            # + addytywna kara FIX_C. bundle_fit_*/fix_c_* prefixy w
            # shadow_dispatcher (LOCATION A+B); bonus_ auto przez prefix.
            "bundle_fit_shadow": bundle_fit_shadow,
            "bundle_fit_marginal_min": bundle_fit_marginal_min,
            "bonus_bundle_fit_shadow_delta": bonus_bundle_fit_shadow_delta,
            "fix_c_additive_pen_shadow": fix_c_additive_pen_shadow,
            # SP-B2-REPO (2026-06-11): dead-head do nowego odbioru wg planu.
            # repo_* prefix w shadow_dispatcher (LOCATION A+B); bonus_ auto.
            "repo_km": repo_km,
            "repo_last_drop_oid": repo_last_drop_oid,
            "bonus_repo_cost_shadow_delta": bonus_repo_cost_shadow_delta,
            # SP-B2-LOADGOV (2026-06-11): load floty (chwilowy + EWMA) per
            # decyzja — identyczne dla kandydatów jednego zlecenia; loadgov_*
            # prefix LOCATION A+B (bonus_ auto).
            "loadgov_load_now": loadgov_now,
            "loadgov_load_ewma": loadgov_ewma,
            "loadgov_active_orders": loadgov_orders,
            "loadgov_active_couriers": loadgov_couriers,
            "bonus_loadgov_shadow_delta": round(bonus_loadgov_shadow_delta, 2),
            # P(breach)-GOVERNANCE shadow (2026-06-14): ciągły P(breach) jako
            # kandydat-zamiennik binarnego governora. loadgov_/bonus_ auto-prefix.
            "loadgov_pbreach": round(pbreach_gov, 4) if pbreach_gov is not None else None,
            "bonus_pbreach_gov_shadow_delta": bonus_pbreach_gov_shadow_delta,
            # SP-B2-ZARAZWOLNY (2026-06-11): telemetria B2 — busy kończący
            # ≤12 min (z zapisanego planu). soon_free_* prefix LOCATION A+B.
            "soon_free_eligible": bool(soon_free_probe and soon_free_probe.get("eligible")),
            "soon_free_applied": soon_free_applied,
            "soon_free_free_at_min": (
                soon_free_probe.get("free_at_min") if soon_free_probe else None),
            # L2.1: guard obu stron — haversine na zatrutym last_drop_coords
            # wywalał CAŁĄ ewaluację kuriera z tego dict-a telemetrii (V328).
            "soon_free_last_drop_km": (
                round(haversine(tuple(soon_free_probe["last_drop_coords"]), pickup_coords), 2)
                if (soon_free_probe and pickup_coords and pickup_coords[0] != 0.0
                    and _coords_pass(True, soon_free_probe["last_drop_coords"],
                                     pickup_coords))
                else None),
            # L2.1 sentinel-ingest (2026-07-01): obserwowalność trucizny coords —
            # które zlecenia w worku kandydata mają sentinel/poza-bbox coords
            # (źródło V328-eject/COORD_GUARD). Unconditional (czysta telemetria,
            # bez I/O); auto-serializacja deny-listą L1.1. None gdy czysto.
            "coord_poison_bag_oids": ([
                str(b.get("order_id")) for b in bag_raw
                if (b.get("pickup_coords") is not None
                    and not C.coords_in_bialystok_bbox(b.get("pickup_coords")))
                or (b.get("delivery_coords") is not None
                    and not C.coords_in_bialystok_bbox(b.get("delivery_coords")))
            ] or None),
            "coord_poison_new_delivery": (
                delivery_coords is not None
                and not C.coords_in_bialystok_bbox(delivery_coords)),
            # F5 RETURN-TO-RESTAURANT (2026-05-24)
            "bonus_r_return_rest": round(bonus_r_return_rest, 2),
            "return_to_restaurant": metrics.get("return_to_restaurant"),
            "return_to_restaurant_oid": metrics.get("return_to_restaurant_oid"),
            # Sprint 2 Etap 2.2 (2026-05-27): carry / bag-stack visibility.
            # Penalty proporcjonalny do drive_min gdy bag ma items z innej
            # restauracji niż nowy pickup. Hard reject = flag-gated KK + dinner.
            "carry_chain_penalty": round(bonus_carry_chain_penalty, 2),
            "carry_chain_stops": int(carry_chain_stops),
            "carry_chain_applied": bool(carry_chain_applied),
            "carry_chain_hard_reject": bool(carry_chain_hard_rejected),
            "carry_chain_drive_min_used": round(float(drive_min or 0.0), 2),
            "paczka_is": C.is_paczka_order({
                "address_id": getattr(new_order, "address_id", None),
                "order_type": getattr(new_order, "order_type", None),
            }),
            "paczka_flex_eligible": C.is_paczka_flex_eligible({
                "address_id": getattr(new_order, "address_id", None),
                "order_type": getattr(new_order, "order_type", None),
            }),
            # V3.28 P4 — coordinator hybrid duty (Adrian doktryna 2026-05-10 wieczór)
            "is_coordinator": _is_coord,
            "coordinator_active": _coord_active,
            "bonus_coordinator_idle": round(bonus_coordinator_idle, 2),
            "r8_violation_min": metrics.get("r8_violation_min", 0.0),
            "bonus_r9_stopover": round(bonus_r9_stopover, 2),
            "bonus_r9_wait_pen": round(bonus_r9_wait_pen, 2),
            # V3.27.1: A/B comparison fields (legacy = always computed; v327 = 0 gdy flag=False)
            "bonus_r9_wait_pen_legacy": round(bonus_r9_wait_pen_legacy, 2),
            "bonus_r9_wait_pen_v327": round(bonus_r9_wait_pen_v327, 2),
            "bonus_v3273_wait_courier": round(bonus_v3273_wait_courier, 2),
            "bonus_v3273_wait_courier_legacy": round(bonus_v3273_wait_courier_legacy, 2),  # Fix #7 shadow
            "v3273_wait_courier_max_min": round(v3273_wait_courier_max_min, 2),
            "v3273_wait_courier_max_restaurant": v3273_wait_courier_max_restaurant,
            "v3273_wait_courier_max_oid": v3273_wait_courier_max_oid,
            "v3273_wait_courier_hard_reject": v3273_wait_courier_hard_reject,
            "v3273_wait_courier_per_pickup": v3273_wait_courier_per_pickup,
            # R-INTRA-RESTAURANT-GAP (2026-05-14)
            "intra_rest_gap_max_min": round(intra_rest_gap_max_min, 2),
            "intra_rest_gap_max_pair": intra_rest_gap_max_pair,
            "intra_rest_gap_max_restaurant": intra_rest_gap_max_restaurant,
            "intra_rest_gap_hard_reject": intra_rest_gap_hard_reject,
            # R-LATE-PICKUP (2026-05-31): committed vs nowy odbiór (patrz tiering selekcji).
            "late_pickup_max_min": round(late_pickup_max_min, 2),
            "late_pickup_committed_max": round(late_pickup_committed_max, 2),
            "late_pickup_committed_worst_oid": late_pickup_committed_worst_oid,
            "late_pickup_committed_worst_restaurant": late_pickup_committed_worst_restaurant,
            "late_pickup_committed_breach": late_pickup_committed_breach,
            "new_pickup_late_min": round(new_pickup_late_min, 2),
            "new_pickup_eta_iso": new_pickup_eta_iso,
            "new_pickup_needs_extension": new_pickup_needs_extension,
            "bonus_penalty_sum": round(bonus_penalty_sum, 2),
            # Transparency OPCJA A (2026-04-19): order_id → (restaurant, delivery_address)
            # mapping dla route section w telegram_approver. Per-courier bag snapshot.
            "bag_context": [
                {
                    "order_id": str(b.get("order_id") or ""),
                    "restaurant": b.get("restaurant"),
                    "delivery_address": b.get("delivery_address"),
                    # V3.28 (2026-05-09) — czas_kuriera per bag-order propagowany do
                    # bag_context payload, żeby telegram_approver render mógł
                    # preferować commit zamiast computed ETA z plan.pickup_at.
                    # Backward compat: nowe pola optional, downstream ignore gdy None.
                    "czas_kuriera_warsaw": b.get("czas_kuriera_warsaw"),
                    "czas_kuriera_hhmm": b.get("czas_kuriera_hhmm"),
                }
                for b in bag_raw
                if b.get("order_id")
            ],
            # V3.19e Opcja B: R1' observability (None gdy pos!=last_assigned_pickup).
            # Post 5 dni shadow: jeśli would_trigger_floor rate >5% → V3.19f floor impl.
            "v319e_r1_prime_hypothetical": v319e_r1_prime_hypothetical,
            # V3.19f: czas_kuriera 2-field passthrough z order_event do enriched_metrics.
            # Shadow serializer (Step 5) propaguje do shadow_decisions.jsonl dla offline
            # diagnostyki rozjazdu HH:MM vs ISO (sanity check w state layer).
            "czas_kuriera_warsaw": order_event.get("czas_kuriera_warsaw"),
            "czas_kuriera_hhmm": order_event.get("czas_kuriera_hhmm"),
            # V3.19h BUG-4: tier × pora cap soft penalty tracking.
            # tier_cap_used = "tier/pora/cap" string. violation = bag_after - cap (int).
            # bonus_bug4_cap_soft = progressive penalty applied do bonus_penalty_sum.
            "v319h_bug4_tier_cap_used": bug4_tier_cap_used,
            "v319h_bug4_cap_violation": bug4_cap_violation,
            "bonus_bug4_cap_soft": round(bonus_bug4_cap_soft, 2),
            # V3.19h BUG-1: SR bundle × drop_proximity_factor.
            # factor (1.0 same zone / 0.5 adjacent / 0.0 distant/Unknown).
            # sr_bundle_adjusted = bonus_l1 po mnożnik (oryginalny bonus_l1 w enriched).
            "v319h_bug1_drop_proximity_factor": v319h_bug1_drop_proximity_factor,
            "v319h_bug1_sr_bundle_adjusted": round(v319h_bug1_sr_bundle_adjusted, 2),
            # V3.19h BUG-2: wave continuation bonus tracking.
            # gap_min = pickup_new - free_at_dt (minutes). None gdy edge (no bag/pickup).
            # continuation_bonus = helper bug2_wave_continuation_bonus(gap_min).
            "v319h_bug2_interleave_gap_min": bug2_interleave_gap_min,
            "v319h_bug2_continuation_bonus": round(bonus_bug2_continuation, 2),
            # V3.28 FIX_C: bundle deliv_spread cap observability.
            # fix_c_applied=True gdy gate zerował bonus_l2/continuation (i któryś był >0).
            # fix_c_deliv_spread_km = max pair-wise drops road km z feasibility.
            "fix_c_applied": fix_c_applied,
            "fix_c_deliv_spread_km": (
                round(fix_c_deliv_spread_km, 2)
                if fix_c_deliv_spread_km is not None else None
            ),
            "fix_c_cap_km": float(C.BUNDLE_MAX_DELIV_SPREAD_KM),
            # V3.26 STEP 3 (R-09): wave geometric veto tracking.
            "v326_wave_veto": v326_wave_veto,
            "v326_wave_geometric_km": (
                round(v326_wave_geometric_km, 2)
                if v326_wave_geometric_km is not None else None
            ),
            # FIX 2 (R-09 oś nowej dostawy) + FIX 1 (źródło czasu odbioru) observability
            "v326_wave_veto_newdrop": v326_wave_veto_newdrop,
            "bug2_pickup_src": bug2_pickup_src,
            # V3.24-A extension metrics
            "v324a_extension_min": round(v324a_extension_min, 2) if v324a_extension_min is not None else None,
            "v324a_extension_penalty": v324a_extension_penalty,
            # Post-shift overrun (Adrian 2026-06-24): minuty dowozu nowego ordera PO
            # końcu zmiany + rosnąca kara (pkt). Liczone ZAWSZE (shadow); wiodący term
            # selekcji best_effort + odjęcie od score gdy ENABLE_POST_SHIFT_OVERRUN_PENALTY.
            "post_shift_overrun_min": post_shift_overrun_min,
            "post_shift_overrun_penalty": post_shift_overrun_penalty,
            # V3.25 STEP C: tier propagation dla R-04 NEW-COURIER-CAP gradient.
            # cs_tier_label = 'new' dla świeżo dodanych (Szymon Sa, Grzegorz R).
            # cs_tier_bag = bag.tier (gold|std+|std|slow|new) dla cross-ref.
            # Penalty applied post-scoring w _v325_new_courier_penalty.
            "cs_tier_label": getattr(cs, "tier_label", None),
            "cs_tier_bag": getattr(cs, "tier_bag", None),
            # V3.26 STEP 6 (R-07 v2 chain-ETA) — ALWAYS recorded (shadow), flag-gated decision.
            "r07_chain_eta_min": (
                round(r07_chain_result.total_chain_min, 2)
                if r07_chain_result is not None else None
            ),
            "r07_starting_point": (
                r07_chain_result.starting_point if r07_chain_result is not None else "error"
            ),
            "r07_chain_details": (
                r07_chain_result.chain_details if r07_chain_result is not None else None
            ),
            "r07_delta_vs_naive_min": (
                round(r07_chain_result.delta_vs_naive_min, 2)
                if r07_chain_result is not None else None
            ),
            "r07_chain_truncated_count": (
                r07_chain_result.truncated_count if r07_chain_result is not None else 0
            ),
            "r07_chain_warnings": (
                (r07_chain_result.warnings or [])[:5] if r07_chain_result is not None else []
            ),
            "r07_compute_latency_ms": (
                round(_r07_latency_ms, 2) if _r07_latency_ms is not None else None
            ),
        }

        # V3.24-A: hard reject gdy extension_penalty() returned None (>60 min).
        # Override verdict na NO tylko jeśli obecny MAYBE (nie przebijaj wcześniejszego NO).
        if v324a_extension_hard_reject and verdict == "MAYBE":
            verdict = "NO"
            reason = f"v324a_extension_too_large ({v324a_extension_min:.1f}min > {C.V324_HARD_REJECT_EXTENSION_OVER_MIN})"

        # Sprint 2 Etap 2.2 (2026-05-27): carry chain hard reject.
        # bag_chain_stops >= 2 AND dinner peak Warsaw AND restaurant w CARRY_RISK_LIST
        # → hard reject (KK dinner R6 breach forensic). Flag-gated (ENABLE_CARRY_CHAIN_PENALTY
        # default OFF), same flaga co soft penalty — gdy flag OFF, carry_chain_hard_rejected
        # zawsze False (helper carry_chain_hard_reject nie dzieje sie bo branch flagowy nie odpala).
        if carry_chain_hard_rejected and verdict == "MAYBE":
            verdict = "NO"
            reason = (
                f"carry_chain_hard_reject (stops={carry_chain_stops}>=2, "
                f"restaurant_in_CARRY_RISK_LIST, dinner_peak Warsaw)"
            )

        # V3.27.3 hard reject: kurier idle >20 min pod restauracją (bag>=1).
        # Same pattern jak v324a — override MAYBE → NO, nie przebijamy wcześniejszego NO.
        #
        # tech-debt #38 re-scope 2026-05-18 (Adrian + replay 472791): hard-reject
        # TYLKO gdy kurier ma realny pending pickup (order `assigned`, picked_up_at
        # is None) — wait pod nowym pickupem zaburza jego niezrealizowany odbiór.
        # Wolny kurier (bag pusty / wszystkie picked_up) — wait BIJE bezczynność
        # ("lepiej czekać 20 min niż stać godzinę"); skip reject, verdict zostaje
        # MAYBE, penalty bonus_v3273_wait_courier zostaje jako SOFT (lepszy kurier
        # i tak wygrywa na score). R6 BAG_TIME 35min nadal niezależnie chroni przed
        # zimnym jedzeniem. Kill-switch: ENABLE_V3273_WAIT_REJECT_FREE_COURIER_SKIP=0.
        if v3273_wait_courier_hard_reject and verdict == "MAYBE":
            _v3273_has_pending_pickup = any(
                getattr(_b273, "picked_up_at", None) is None for _b273 in bag_sim
            )
            _v3273_skip_free = getattr(
                C, "ENABLE_V3273_WAIT_REJECT_FREE_COURIER_SKIP", True)
            if _v3273_has_pending_pickup or not _v3273_skip_free:
                verdict = "NO"
                _rest_273 = v3273_wait_courier_max_restaurant or "?"
                reason = f"v3273_wait_courier_hard_reject ({v3273_wait_courier_max_min:.1f}min > {C.V3273_WAIT_COURIER_HARD_REJECT_MIN} pod {_rest_273})"

        # R-INTRA-RESTAURANT-GAP hard reject (2026-05-14): same-restaurant
        # pickup gap > MAX_INTRA_RESTAURANT_GAP_MIN. Override MAYBE → NO.
        if intra_rest_gap_hard_reject and verdict == "MAYBE":
            verdict = "NO"
            _rest_irg = intra_rest_gap_max_restaurant or "?"
            reason = f"intra_restaurant_gap_exceeded ({intra_rest_gap_max_min:.1f}min > {C.MAX_INTRA_RESTAURANT_GAP_MIN} pod {_rest_irg})"

        # R-LATE-PICKUP (2026-05-31): NIE hard-reject — kandydat zostaje feasible,
        # a spóźnienie odbioru rozstrzyga TIERING selekcji niżej (Adrian: „zawsze daje
        # propozycje"). late_pickup_committed_breach → najniższy tier; new_pickup_needs_extension
        # → propozycja przedłużonego czasu. Patrz: late-pickup tiering reorder po _demote_blind_empty.

        # BUG-D Faza 2b: stop TLS leg tracking + aggregate przed Candidate construction.
        # stop_v2_request_tracking jest idempotent — outer finally zrobi second no-op call.
        from dispatch_v2 import osrm_client as _osrm_client_inner
        from dispatch_v2.traffic_v2_aggregator import aggregate_legs as _aggregate_legs
        _v2_legs = _osrm_client_inner.stop_v2_request_tracking()
        _v2_route = _aggregate_legs(_v2_legs) if _v2_legs else None

        return Candidate(
            courier_id=str(cid),
            name=getattr(cs, "name", None),
            score=final_score,
            feasibility_verdict=verdict,
            feasibility_reason=reason,
            plan=plan,
            metrics=enriched_metrics,
            traffic_v2_shadow_route=_v2_route,
        )
    # ── end _v327_eval_courier (inner) ──

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

    from concurrent.futures import ThreadPoolExecutor as _V327_TPE
    _v327_max_workers = max(1, min(10, len(fleet_snapshot)))
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
    no_gps_travel_min = max(15.0, prep_remaining_min)
    no_gps_eta_utc = now + timedelta(minutes=no_gps_travel_min)

    for c in candidates:
        ps = c.metrics.get("pos_source")
        if ps == "no_gps":
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
            if C.ENABLE_V324A_SCHEDULE_INTEGRATION:
                # V3.24-A zastępuje legacy F1.8e hard reject gradient extension_penalty
                # (applied w scoring layer B5). Hard reject tylko gdy extension > 60 min
                # — delegowane do feasibility layer B5. Tu pozostawiamy MAYBE.
                pass
            else:
                # Legacy F1.8e: hard exclude jeśli pre_shift kurier nie zdąży na pickup_ready.
                # Bez tego scoring promuje go pomimo niedostępności (np. odbiór za 26
                # min, kurier startuje za 46 min → nie zdąży).
                if shift_min > prep_remaining_min + 0.01:
                    c.feasibility_verdict = "NO"
                    c.feasibility_reason = (
                        f"pre_shift_too_late (start za {shift_min:.0f} min, "
                        f"odbiór za {prep_remaining_min:.0f} min)"
                    )

    # Feasible (MAYBE) → rank by score.
    # R2 Bartek Gold Standard tie-breaker: przy równym score, preferuj
    # kandydata o niższej corridor deviation (bundle_level3_dev).
    # Brak dev (pusty bag / solo) → 999 (sortuje się na koniec przy tie).
    feasible = [c for c in candidates if c.feasibility_verdict == "MAYBE"]
    feasible.sort(key=lambda c: (-c.score, c.metrics.get("bundle_level3_dev") if c.metrics.get("bundle_level3_dev") is not None else 999.0))

    # V3.25 STEP C (R-04): NEW-COURIER-CAP gradient (flag-gated, default False).
    # SP-B2-RAMPA: now dla slotu rampy (high_risk 14-17 wyłączony z rampy).
    feasible = _v325_new_courier_penalty(feasible, order_id, now=now)

    # V3.26 STEP 2 (R-05): speed multiplier adjustment (flag-gated, default False).
    feasible = _v326_speed_multiplier_adjust(feasible, order_id)

    # V3.26 STEP 4 (R-10): fleet load balance adjustment (flag-gated, default False).
    feasible = _v326_fleet_load_balance(feasible, candidates, order_id)

    # A2 reliability soft-score (2026-06-07, dźwignia A2) — flag-gated OFF. PRZED
    # demote/tiering, by buckety pos/tier zostały re-narzucone (semantyka A2).
    feasible = _a2_reliability_soft_score(feasible, order_id)
    # GPS-03/DATA-04: shadow liczy się zawsze, aplikacja za flagą (OFF).
    feasible = _gps_age_discount(feasible, order_id)

    # V3.26 STEP 5 (R-06): multi-stop trajectory district-based (flag-gated, default False).
    feasible = _v326_multistop_trajectory(feasible, new_order, order_id)

    # V3.16 demote — FINAL reorder pass, AFTER V325/V326 score adjustments.
    # Sprint 5 (2026-05-27): moved here from pre-V325 position. Powód: V325/V326
    # wywołują feasible.sort() po score, co restoreował blind+empty na top mimo
    # demote (oid=474624 verified — Mateusz O cid=413 score 112 vs Adrian R cid=400
    # score 4.1, mimo NO_GPS_DEMOTE log). Demote musi być LAST żeby V3.16
    # invariant przeżył (informed first, blind+empty last) do final top[:16].
    # Patrz: eod_drafts/2026-05-27/sprint_diag_27may/operator_favorites_root_cause_2026-05-27.md
    feasible = _demote_blind_empty(feasible, order_id)
    # INV-FEASIBILITY-FIRST (spec odporności §6.A): po całym łańcuchu rescore/reorder
    # (v325/v326/a2/gps_age/multistop/demote) pula selekcji MUSI być wyłącznie MAYBE.
    # Tiering/LEXR6 niżej tylko PERMUTUJĄ ten sam zbiór (nie dodają NO). Fail-loud guard.
    _assert_feasibility_first(feasible, order_id)

    # R-LATE-PICKUP tiering (2026-05-31, Adrian) — FINAL reorder pass, AFTER demote.
    # NIE usuwa kandydatów (→ „zawsze daje propozycje"), tylko ustawia priorytet:
    #   tier 0: nie psuje umówionego odbioru ORAZ zdąży na nowy ≤5 min (na czas)
    #   tier 1: nie psuje umówionego, ale nowy odbiór potrzebuje przedłużenia (>5 min)
    #   tier 2: psuje umówiony odbiór committed (>5 min) — OSTATECZNOŚĆ (jak 477237)
    # Stabilny sort po (tier, dotychczasowa kolejność) — demote/score zachowane w tierze.
    # Gdy zwycięzca tier>0 → pickup_extension_redirect niesie propozycję czasu + powód.
    # Aktywne tylko gdy ENABLE_LATE_PICKUP_HARD_GATE (metryki w candidate.metrics).
    pickup_extension_redirect = None
    late_pickup_shadow = None
    r6_danger_shadow = None  # Fix #6: rozjazd zwycięzcy legacy-liniowa-R6 vs danger-R6
    min_delivered_at_shadow = None  # Adrian 2026-06-25: log-only min-total komparator
    reserve_tiebreak_shadow = None  # #3 top10 2026-06-29: log-only reserve-aware tie-break (wolny vs jadący)
    if getattr(C, "ENABLE_LATE_PICKUP_HARD_GATE", False) and feasible:
        _lp_tier = _late_pickup_tier  # module-level (testowalny)
        _orig_order = {id(c): i for i, c in enumerate(feasible)}
        _free = float(getattr(C, "LATE_PICKUP_SOFT_FREE_MIN", 5.0))
        _coeff = float(getattr(C, "LATE_PICKUP_SOFT_COEFF", 1.5))
        _cap = float(getattr(C, "LATE_PICKUP_SOFT_CAP", 60.0))
        def _new_eta_key(c):
            _iso = (c.metrics or {}).get("new_pickup_eta_iso")
            return _iso or "9999"

        # --- STARY tiering (SHADOW counterfactual — „co by było bez Opcji B") ---
        # Stary klucz: tier PIERWSZY → tier-0 (odbiór ≤5 min na czas) bił każdy tier-1
        # NIEZALEŻNIE od score → krzyżowo-miejskie bundle wygrywały mimo R1/R6 w score
        # (477330 Andrei −5.3 bił Michała Ro +36.4). Liczone bez mutacji `feasible`.
        _has_lower = any(_lp_tier(c) == 0 for c in feasible)
        if _has_lower:
            _old_sorted = sorted(feasible, key=lambda c: (_lp_tier(c), _orig_order[id(c)]))
        else:
            _old_sorted = sorted(feasible, key=lambda c: (_lp_tier(c), _new_eta_key(c), _orig_order[id(c)]))
        _old_winner = _old_sorted[0] if _old_sorted else None

        # --- Opcja B (LIVE gdy flaga ON) — score-first z miękką karą za późny odbiór ---
        # Tier-2 (łamanie committed czas_kuriera) = twardy demote (ostateczność, 477237).
        # Reszta: ranking po score (z zachowanymi V3.16 demote-bucketami informed>other>
        # blind) MINUS gradient kara ∝ max(0, new_pickup_late_min − FREE_MIN). Pickup-
        # lateness KONKURUJE z jakością dowozu (R6/spread w score), nie DOMINUJE.
        if getattr(C, "ENABLE_LATE_PICKUP_TIERING_SCORE_FIRST", False):
            feasible.sort(key=lambda c: _late_pickup_score_first_key(
                c, _lp_tier(c), _orig_order[id(c)], _free, _coeff, _cap))
        else:
            # flaga OFF → identyczne zachowanie ze starym tieringiem (in-place)
            if _has_lower:
                feasible.sort(key=lambda c: (_lp_tier(c), _orig_order[id(c)]))
            else:
                feasible.sort(key=lambda c: (_lp_tier(c), _new_eta_key(c), _orig_order[id(c)]))

        # FAZA 2 OBJM-LEXR6 (2026-06-18, flaga ENABLE_OBJM_LEXR6_SELECT default OFF): live-flip.
        # PO tier-gate sorcie, PRZED wyborem feasible[0]: przesuń R6-primary-lex pick na czoło
        # JEGO grupy (tier×bucket). Zachowuje bramkę tierów/committed (grupa = ten sam tier),
        # bucket V3.16 demote (informed>other>blind), MIN_PROPOSE/KOORD gate (na feasible[0].score
        # liczonym niżej). Reorder identity-safe (pop po id, nie .remove==). Rollback = flaga OFF
        # (hot-reload). NIE wpinać przed tier-gate. Zwalidowane Fazą 1 (n=352, G1 −72min, G2 0%).
        if C.flag("ENABLE_OBJM_LEXR6_SELECT", False) and feasible:
            _d2 = _objm_lexr6_d2_pick(feasible)
            if _d2 is not None and _d2 is not feasible[0]:
                _d2_idx = next((i for i, c in enumerate(feasible) if c is _d2), None)
                if _d2_idx is not None:
                    feasible.pop(_d2_idx)
                    feasible.insert(0, _d2)
                    try:
                        log.info(f"OBJM_LEXR6_SELECT order={order_id} reorder→cid="
                                 f"{getattr(_d2, 'courier_id', None)}")
                    except Exception:
                        pass

        _winner = feasible[0]
        _wm = _winner.metrics or {}
        _wtier = _lp_tier(_winner)

        # MIN-DELIVERED-AT SHADOW (Adrian 2026-06-25): log-only komparator — kto by wygrał
        # gdyby selekcja minimalizowała `predicted_delivered_at[new]` (= min total
        # spóźnienie+dowóz, committed stały → najwcześniej do klienta) vs dzisiejszy live
        # `_winner`. Loguje też regresję floty (R6/spread/late) OBU w TEJ SAMEJ decyzji
        # (Pareto), by rozstrzygnąć: „min-total" netto wygrywa czy psuje flotę. ZERO zmiany
        # decyzji — `feasible`/`_winner` nietknięte. Defense-in-depth try/except (nie krasz
        # propozycji). Gated ENABLE_MIN_DELIVERED_AT_SHADOW (default OFF).
        if C.flag("ENABLE_MIN_DELIVERED_AT_SHADOW",
                  getattr(C, "ENABLE_MIN_DELIVERED_AT_SHADOW", False)):
            try:
                _mda = min(feasible, key=lambda c: (
                    _d.timestamp() if (_d := _new_delivered_at_dt(c, order_id)) is not None
                    else float("inf")))
                _live_d = _new_delivered_at_dt(_winner, order_id)
                _mda_d = _new_delivered_at_dt(_mda, order_id)
                _mm = _mda.metrics or {}
                _sooner = (round((_live_d - _mda_d).total_seconds() / 60.0, 1)
                           if (_live_d is not None and _mda_d is not None) else None)
                min_delivered_at_shadow = {
                    "changed": (str(getattr(_mda, "courier_id", ""))
                                != str(getattr(_winner, "courier_id", ""))),
                    "live_cid": str(getattr(_winner, "courier_id", "")),
                    "live_delivered_at": (_live_d.isoformat() if _live_d is not None else None),
                    "live_r6_max_bag_time_min": _wm.get("r6_max_bag_time_min"),
                    "live_deliv_spread_km": _wm.get("deliv_spread_km"),
                    "live_new_pickup_late_min": _wm.get("new_pickup_late_min"),
                    "mda_cid": str(getattr(_mda, "courier_id", "")),
                    "mda_delivered_at": (_mda_d.isoformat() if _mda_d is not None else None),
                    "mda_r6_max_bag_time_min": _mm.get("r6_max_bag_time_min"),
                    "mda_deliv_spread_km": _mm.get("deliv_spread_km"),
                    "mda_new_pickup_late_min": _mm.get("new_pickup_late_min"),
                    # >0 = „min-total" dowozi WCZEŚNIEJ do klienta niż live (o tyle minut)
                    "mda_delivers_sooner_min": _sooner,
                }
            except Exception as _mda_e:
                log.warning(f"min_delivered_at_shadow fail order={order_id}: {_mda_e!r}")

        # RESERVE-AWARE TIEBREAK SHADOW (#3 top10, 2026-06-29): log-only — gdy zwycięzca to
        # WOLNY kurier (bag 0), a w TYM SAMYM tierze late-pickup jest FEASIBLE kandydat JUŻ
        # W TRASIE (bag>=1) w wąskim marginesie score → tie-break dołożyłby do jadącego
        # (oszczędza rezerwę). ZERO zmiany decyzji (feasible/_winner NIETKNIĘTE). same-tier =
        # brak inwersji committed-odbioru; wyklucz sentinel/best_effort + R6>40 (świeżość).
        # Pomiar dokładny 29.06: ~3-9/d czystych. Flip AKTYWNY = osobna flaga + ACK (po
        # walidacji fizycznej #1, że bundle nie psuje świeżości). Gated OFF, try/except.
        if C.flag("ENABLE_RESERVE_AWARE_TIEBREAK_SHADOW",
                  getattr(C, "ENABLE_RESERVE_AWARE_TIEBREAK_SHADOW", False)):
            try:
                reserve_tiebreak_shadow = _reserve_aware_tiebreak_eval(
                    _winner, feasible, _wtier, _lp_tier,
                    float(getattr(C, "RESERVE_TIEBREAK_MARGIN", 30.0)))
            except Exception as _rt_e:
                log.warning(f"reserve_tiebreak_shadow fail order={order_id}: {_rt_e!r}")

        # SHADOW: rozjazd stary-vs-nowy zwycięzca (Adrian chce widzieć efekt natychmiast).
        # Serializowany top-level w shadow_dispatcher → grep LATE_PICKUP_SCORE_FIRST.
        if (_old_winner is not None
                and str(getattr(_old_winner, "courier_id", "")) != str(getattr(_winner, "courier_id", ""))):
            _ow_m = _old_winner.metrics or {}
            late_pickup_shadow = {
                "changed": True,
                "old_winner_cid": str(getattr(_old_winner, "courier_id", "")),
                "old_winner_name": getattr(_old_winner, "name", None),
                "old_winner_score": round(float(getattr(_old_winner, "score", 0.0) or 0.0), 2),
                "old_winner_tier": _lp_tier(_old_winner),
                "old_winner_deliv_spread_km": _ow_m.get("deliv_spread_km"),
                "old_winner_r6_max_bag_time_min": _ow_m.get("r6_max_bag_time_min"),
                "old_winner_new_pickup_late_min": _ow_m.get("new_pickup_late_min"),
                "new_winner_cid": str(getattr(_winner, "courier_id", "")),
                "new_winner_name": getattr(_winner, "name", None),
                "new_winner_score": round(float(getattr(_winner, "score", 0.0) or 0.0), 2),
                "new_winner_tier": _wtier,
                "new_winner_deliv_spread_km": _wm.get("deliv_spread_km"),
                "new_winner_r6_max_bag_time_min": _wm.get("r6_max_bag_time_min"),
                "new_winner_new_pickup_late_min": _wm.get("new_pickup_late_min"),
            }
            log.info(
                f"LATE_PICKUP_SCORE_FIRST_DIVERGENCE order={order_id} "
                f"old={_old_winner.courier_id}(score={getattr(_old_winner,'score',0.0):.1f},"
                f"tier={_lp_tier(_old_winner)},spread={_ow_m.get('deliv_spread_km')},"
                f"r6={_ow_m.get('r6_max_bag_time_min')}) "
                f"new={_winner.courier_id}(score={getattr(_winner,'score',0.0):.1f},"
                f"tier={_wtier},spread={_wm.get('deliv_spread_km')},"
                f"r6={_wm.get('r6_max_bag_time_min')})"
            )
        else:
            late_pickup_shadow = {"changed": False}

        # Fix #6 SHADOW: czy stroma kara R6 (danger zone) zmieniła zwycięzcę vs legacy
        # liniowa. Tylko gdy obie flagi ON (live config) — score-override w kluczu Opcji B
        # cofa ekstra danger-penalty: legacy_score = score + (legacy_r6 − new_r6).
        if (getattr(C, "ENABLE_R6_DANGER_ZONE_PENALTY", False)
                and getattr(C, "ENABLE_LATE_PICKUP_TIERING_SCORE_FIRST", False)):
            def _legacy_r6_score(c):
                m = c.metrics or {}
                _new = m.get("bonus_r6_soft_pen") or 0.0
                _leg = m.get("bonus_r6_soft_pen_legacy")
                if _leg is None:
                    _leg = _new
                return (getattr(c, "score", 0.0) or 0.0) + (_leg - _new)
            _r6_legacy_sorted = sorted(feasible, key=lambda c: _late_pickup_score_first_key(
                c, _lp_tier(c), _orig_order[id(c)], _free, _coeff, _cap, score=_legacy_r6_score(c)))
            _r6_old = _r6_legacy_sorted[0] if _r6_legacy_sorted else None
            if (_r6_old is not None
                    and str(getattr(_r6_old, "courier_id", "")) != str(getattr(_winner, "courier_id", ""))):
                _r6om = _r6_old.metrics or {}
                r6_danger_shadow = {
                    "changed": True,
                    "old_winner_cid": str(getattr(_r6_old, "courier_id", "")),
                    "old_winner_name": getattr(_r6_old, "name", None),
                    "old_winner_r6_max_bag_time_min": _r6om.get("r6_max_bag_time_min"),
                    "old_winner_r6_pen_legacy": _r6om.get("bonus_r6_soft_pen_legacy"),
                    "new_winner_cid": str(getattr(_winner, "courier_id", "")),
                    "new_winner_name": getattr(_winner, "name", None),
                    "new_winner_r6_max_bag_time_min": _wm.get("r6_max_bag_time_min"),
                    "new_winner_r6_pen": _wm.get("bonus_r6_soft_pen"),
                }
                log.info(
                    f"R6_DANGER_DIVERGENCE order={order_id} "
                    f"legacy_lin={_r6_old.courier_id}(r6={_r6om.get('r6_max_bag_time_min')}min) "
                    f"danger={_winner.courier_id}(r6={_wm.get('r6_max_bag_time_min')}min)"
                )
            else:
                r6_danger_shadow = {"changed": False}

        if _wtier >= 1:
            pickup_extension_redirect = {
                "tier": _wtier,
                "courier_id": str(getattr(_winner, "courier_id", "")),
                "suggested_pickup_iso": _wm.get("new_pickup_eta_iso"),
                "new_pickup_late_min": _wm.get("new_pickup_late_min"),
                "committed_breach_min": (round(_wm.get("late_pickup_committed_max", 0.0), 1)
                                         if _wtier == 2 else None),
                "committed_worst_restaurant": (_wm.get("late_pickup_committed_worst_restaurant")
                                               if _wtier == 2 else None),
            }
            log.info(
                f"LATE_PICKUP_TIER order={order_id} winner={_winner.courier_id} tier={_wtier} "
                f"new_late={_wm.get('new_pickup_late_min')}min "
                f"committed_breach={_wm.get('late_pickup_committed_max')}min "
                f"suggested_pickup={_wm.get('new_pickup_eta_iso')}"
            )

    # SELECTION VETO SHADOW — RETIRED 2026-06-11 (ACK po at#113): A2 dowiózł,
    # werdykt 08.06 = veto nadpisywałoby legalne decyzje. Kod usunięty w całości.

    # R6BREACH-01/GATE-02 SHADOW — RETIRED 2026-06-11 (Adrian: „duplikat R6 =
    # R6BREACH, wytnij"). Zero danych zebranych (flaga OFF od commitu f64ff81).

    if feasible:
        top = feasible[:TOP_N_CANDIDATES]
        # V3.26 STEP 1 (R-11): build rationale dla BEST candidate (flag-gated).
        # Inject do best.metrics["v326_rationale"] żeby shadow_dispatcher
        # serializer + telegram_approver formatter mogli renderować.
        _rationale = _v326_build_rationale(top[0], feasible)
        if _rationale and hasattr(top[0], "metrics") and isinstance(top[0].metrics, dict):
            top[0].metrics["v326_rationale"] = _rationale

        # === SP-B2-PLN (2026-06-11): funkcja celu PLN w shadow ===
        # V = 6,33 − koszt_km·Δkm − 14·P(breach) − 0,20·leżenie − opp·(blokada
        # + czekanie) dla top-5 kandydatów; pln_* per kandydat + pln_best_cid /
        # pln_best_v / pln_vs_score_flip na zwycięzcy (LOCATION A+B przez
        # prefix pln_). Czysta telemetria za ENABLE_PLN_OBJECTIVE_SHADOW (ON);
        # jakiekolwiek użycie w decyzji = 🛑 ACK. Δkm = (repo dead-head albo
        # dojazd z pozycji) + noga pickup→drop (haversine×1,37 jak agent_econ);
        # blokada ≈ dojazd + noga/24 km/h + 2×DWELL (przybliżenie, opisane
        # w pln_objective docstring).
        if C.flag("ENABLE_PLN_OBJECTIVE_SHADOW", True):
            try:
                _pln_leg_km = None
                if (delivery_coords and delivery_coords != (0.0, 0.0)
                        and pickup_coords and pickup_coords[0] != 0.0):
                    _pln_leg_km = round(
                        haversine(pickup_coords, delivery_coords)
                        * HAVERSINE_ROAD_FACTOR_BIALYSTOK, 2)
                _pln_best_cid = None
                _pln_best_v = None
                # 2026-06-17 (rozszerzenie grupy, ACK Adrian): pln_v liczone dla CAŁEJ puli
                # feasible (nie tylko top[:5]) → tie-breaker pln w _objm_lexr6_shadow ma
                # pełne pokrycie grupy (tier×bucket). compute_pln_value = czysta arytmetyka
                # + mtime-cache (tylko getmtime/kandydat — tani stat). pln_best_cid/_v dalej
                # WYŁĄCZNIE z top[:5] (zachowana semantyka + izolacja walidacji at#152).
                _pln_top5_ids = {id(_pc) for _pc in top[:5]}
                for _pc in feasible:
                    _pm = getattr(_pc, "metrics", None)
                    if not isinstance(_pm, dict):
                        continue
                    _base_km = _pm.get("repo_km")
                    if _base_km is None:
                        _base_km = _pm.get("km_to_pickup")
                    if _base_km is None or _pln_leg_km is None:
                        continue
                    _dkm = float(_base_km) + _pln_leg_km
                    _trav = _pm.get("travel_min")
                    _leg_min = _pln_leg_km * 2.5 + 4.0  # 24 km/h + 2×DWELL
                    _pln = pln_objective.compute_pln_value(
                        cid=_pc.courier_id,
                        delta_km=_dkm,
                        bag_before=_pm.get("bag_size_before") or 0,
                        load=_pm.get("loadgov_load_ewma"),
                        travel_min=_trav,
                        time_to_ready_min=_pm.get("time_to_pickup_ready_min"),
                        blokada_min=(float(_trav) + _leg_min) if _trav is not None else None,
                        now=now,
                        apply_courier_pay=C.flag("ENABLE_PLN_COURIER_PAY", False),
                    )
                    if _pln:
                        _pm.update(_pln)
                        if id(_pc) in _pln_top5_ids and (
                                _pln_best_v is None or _pln["pln_v"] > _pln_best_v):
                            _pln_best_v = _pln["pln_v"]
                            _pln_best_cid = str(_pc.courier_id)
                if _pln_best_cid is not None and isinstance(top[0].metrics, dict):
                    top[0].metrics["pln_best_cid"] = _pln_best_cid
                    top[0].metrics["pln_best_v"] = _pln_best_v
                    top[0].metrics["pln_vs_score_flip"] = (
                        _pln_best_cid != str(top[0].courier_id))
            except Exception as _pln_e:
                log.warning(f"SP-B2-PLN shadow fail order={order_id}: {_pln_e!r}")

        # ── E2 (2026-06-14) 20% LIVE A/B: PLN-sort dla 20% zlecen (split int(order_id)%5),
        # reszta=kontrola. Tylko ENABLE_E2_PLN_AB ON (default OFF = inert). Tag pln_ab_arm
        # do shadow → porownanie realnego breachu PLN vs score (join order_id->sla_log).
        # Re-sort `top` po pln_v (top[:5] ma pln_v); selekcja nizej bierze nowego top[0].
        # MIN_PROPOSE gate dalej na top[0].score (low-score PLN-pick -> KOORD, human-gated).
        if C.flag("ENABLE_E2_PLN_AB", False) and top:
            _e2_arm = _e2_ab_arm(order_id)
            if _e2_arm == "pln":
                _pln_pure_resort(top)
            try:
                if isinstance(getattr(top[0], "metrics", None), dict):
                    top[0].metrics["pln_ab_arm"] = _e2_arm
            except Exception:
                pass

        # D2 SHADOW (2026-06-17): objm R6-primary lexicographic selektor — OBSERWACYJNY,
        # flaga default OFF (zero wpływu na selekcję/werdykt). Po E2 hooku → top[0] = finalny
        # serializowany best. Pisze top[0].metrics['objm_lexr6_*']. Patrz _objm_lexr6_shadow.
        if C.flag("ENABLE_OBJM_LEXR6_SELECT_SHADOW", False):
            _objm_lexr6_shadow(top, feasible, order_id)

        # WARSTWA B SHADOW (#483000, 2026-06-24): carry-ślepota bramki feasibility —
        # czy odrzucony (NO) kandydat W PROCESIE jest lepszy-na-prawdzie niż bypassowany
        # survivor. OBSERWACYJNY, flaga default OFF, pełne `candidates` (z NO) w zasięgu.
        if C.flag("ENABLE_FEAS_CARRY_BLIND_SHADOW", False):
            _feas_carry_blind_shadow(top, feasible, candidates, order_id, now)

        # WARSTWA B LIVE (#483000, 2026-06-27, flaga ENABLE_FEAS_CARRY_READMIT default OFF):
        # carry-aware re-admit — promuj odrzuconego (verdict NO, blocking sla/r6) na top[0]
        # gdy lepszy carry-inclusive (lex_qual) ORAZ nowy order ≤ cap-40 (Tier-3 cap-stretch,
        # ta sama stała co best_effort). OSTATNIA mutacja selekcji (po E2/OBJM/shadow): mutuje
        # `top`/`feasible` in-place (jak E2 _pln_pure_resort); downstream MIN_PROPOSE +
        # commit_divergence_gate dalej gate'ują nowy top[0] (HARD nietknięte u źródła — bramka
        # candidata dalej zwraca NO; tu selekcja przenosi go, promote verdict→MAYBE dla spójności
        # serializacji/inwariantu). Rollback = flaga OFF (hot). Fail-open (nie krasz propozycji).
        if C.decision_flag("ENABLE_FEAS_CARRY_READMIT"):
            try:
                _fcr_cap = float(C.flag(
                    "BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN",
                    getattr(C, "BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN", 40.0)))
                _fcr = _feas_carry_readmit_pick(
                    top, feasible, candidates, new_order.order_id, cap_min=_fcr_cap)
                if _fcr is not None:
                    _fcr_cand, _fcr_regret, _fcr_reason, _fcr_newbag = _fcr
                    if _fcr_cand is not None and _fcr_cand is not top[0]:
                        _prev_cid = getattr(top[0], "courier_id", None)
                        try:
                            _fcr_cand.feasibility_verdict = "MAYBE"
                        except Exception:
                            pass
                        if isinstance(getattr(_fcr_cand, "metrics", None), dict):
                            _fcr_cand.metrics["feas_carry_readmit"] = True
                            _fcr_cand.metrics["feas_carry_regret_min"] = _fcr_regret
                            _fcr_cand.metrics["feas_carry_orig_reason"] = _fcr_reason
                            _fcr_cand.metrics["feas_carry_newbag_min"] = _fcr_newbag
                            _fcr_cand.metrics["feas_carry_redirect_from_cid"] = str(_prev_cid)
                            _fcr_cand.metrics["feas_carry_cap_min"] = _fcr_cap
                        # przenieś na czoło top (identity-safe pop jak OBJM_LEXR6) + do feasible
                        _fcr_idx = next((i for i, c in enumerate(top) if c is _fcr_cand), None)
                        if _fcr_idx is not None:
                            top.pop(_fcr_idx)
                        top.insert(0, _fcr_cand)
                        del top[TOP_N_CANDIDATES:]
                        if _fcr_cand not in feasible:
                            feasible.insert(0, _fcr_cand)
                        log.info(
                            f"FEAS_CARRY_READMIT order={order_id} redirect "
                            f"{_prev_cid}→{getattr(_fcr_cand, 'courier_id', None)} "
                            f"regret={_fcr_regret}min newbag={_fcr_newbag}min cap={_fcr_cap}")
            except Exception as _fcr_e:  # noqa: BLE001
                log.warning(f"FEAS_CARRY_READMIT live fail order={order_id}: {_fcr_e!r}")

        # V3.28 Faza 6 — LGBM shadow inference (parallel, ZERO behavior change).
        # Pure BC model trained na 399K pairs CSV history (Faza 5 v1.0). Result
        # attached to top[0].metrics["lgbm_shadow"] for shadow_dispatcher LOCATION B
        # serialization. NIGDY nie raise — defense-in-depth fallback w ml_inference.
        if getattr(C, "ENABLE_LGBM_SHADOW", False):
            try:
                from dispatch_v2.ml_inference import get_lgbm_inferer
                _inferer = get_lgbm_inferer()
                _decision_ctx = {
                    "decision_ts": now,
                    "order_id": order_id,
                    "pickup_lat": pickup_coords[0] if pickup_coords and pickup_coords != (0.0, 0.0) else None,
                    "pickup_lon": pickup_coords[1] if pickup_coords and pickup_coords != (0.0, 0.0) else None,
                    "pickup_district": None,  # Optional: derive z pickup_coords via district_lookup
                    "drop_district": None,
                }
                _shadow_result = _inferer.predict_for_decision(_decision_ctx, feasible)
                # Compute agreement (winner_cid == primary best courier_id)
                _shadow_result.agreement_with_primary = (
                    str(_shadow_result.winner_cid) == str(top[0].courier_id)
                    if _shadow_result.winner_cid else None
                )
                if hasattr(top[0], "metrics") and isinstance(top[0].metrics, dict):
                    top[0].metrics["lgbm_shadow"] = _shadow_result.to_dict()
                # V3.28-TICKET2: explicit LGBM_SHADOW log line dla validation gate pipeline.
                # C4 (2026-06-11): "pool_size" było mylące (to LICZBA KANDYDATÓW
                # SCOROWANYCH przez LGBM, nie pula kurierów) → candidates_scored.
                try:
                    _lgbm_winner = _shadow_result.winner_cid
                    _current_winner = str(top[0].courier_id) if top else None
                    _agreement = (str(_lgbm_winner) == _current_winner) if (_lgbm_winner and _current_winner) else None
                    log.info(
                        f"LGBM_SHADOW oid={order_id} "
                        f"winner_lgbm={_lgbm_winner} winner_current={_current_winner} "
                        f"agreement={_agreement} fallback={_shadow_result.fallback_reason or 'NONE'} "
                        f"latency_ms={_shadow_result.latency_ms} "
                        f"candidates_scored={_shadow_result.n_candidates_scored} "
                        f"model_version={_shadow_result.model_version}"
                    )
                except Exception as _log_e:
                    log.warning(f"LGBM_SHADOW log line emit fail (non-blocking) order={order_id}: {_log_e}")
            except Exception as _lgbm_e:
                log.error(f"LGBM shadow unexpected fail order={order_id}: {_lgbm_e}", exc_info=True)
                if hasattr(top[0], "metrics") and isinstance(top[0].metrics, dict):
                    top[0].metrics["lgbm_shadow"] = {
                        "enabled": False,
                        "fallback_reason": "exception_in_pipeline",
                    }
                log.info(
                    f"LGBM_SHADOW oid={order_id} winner_lgbm=None winner_current={top[0].courier_id if top else None} "
                    f"agreement=None fallback=exception_in_pipeline latency_ms=0.0 pool_size=0 model_version=unknown"
                )

        # A2 DWUMODEL SHADOW (2026-06-20): solo/bundle ranking OBOK selekcji reguł — OBSERWACYJNY.
        # Liczone TU (pozycje kandydatów REALNE z feasible/pickup_coords, nie z logu) → rozwiązuje
        # blocker lat/lon online-parytetu. Router per-kandydat po STANIE WORKA (3 skew naprawione,
        # parity 0/58385). ZERO wpływu na werdykt/selekcję — wynik tylko do top[0].metrics →
        # shadow log. Flaga hot-reload default OFF. NIGDY raise (predict_two_model_for_decision
        # fail-soft + ten wrapper try/except). Self-contained _decision_ctx (niezależny od bloku
        # ENABLE_LGBM_SHADOW powyżej).
        if C.flag("ENABLE_LGBM_TWOMODEL_SHADOW", False) and top:
            try:
                from dispatch_v2.ml_inference import predict_two_model_for_decision
                _tm_ctx = {
                    "decision_ts": now,
                    "order_id": order_id,
                    "pickup_lat": pickup_coords[0] if pickup_coords and pickup_coords != (0.0, 0.0) else None,
                    "pickup_lon": pickup_coords[1] if pickup_coords and pickup_coords != (0.0, 0.0) else None,
                    "pickup_district": None,
                    "drop_district": None,
                }
                _tm_result = predict_two_model_for_decision(_tm_ctx, feasible)
                if _tm_result is not None:
                    _tm_dict = _tm_result.to_dict()
                    _tm_dict["agreement_with_primary"] = (
                        str(_tm_result.winner_cid) == str(top[0].courier_id)
                        if _tm_result.winner_cid else None
                    )
                    if hasattr(top[0], "metrics") and isinstance(top[0].metrics, dict):
                        top[0].metrics["lgbm_twomodel_shadow"] = _tm_dict
                    log.info(
                        f"LGBM_TWOMODEL_SHADOW oid={order_id} "
                        f"winner_tm={_tm_result.winner_cid} winner_current={top[0].courier_id} "
                        f"agreement={_tm_dict['agreement_with_primary']} "
                        f"regimes={_tm_result.regime_counts} "
                        f"fallback={_tm_result.fallback_reason or 'NONE'} "
                        f"latency_ms={_tm_result.latency_ms} scored={_tm_result.n_candidates_scored}"
                    )
            except Exception as _tm_e:
                log.error(f"LGBM twomodel shadow fail order={order_id}: {_tm_e}", exc_info=True)

        # V3.28 P3 (C) — min latency gate KOORD escalate (Adrian doktryna 2026-05-10).
        # Gdy panel_packs_cache jest stale (>60s) AND >=2 candidates mają state-vs-panel
        # divergence (bag=0 w state ale signal>0 w panel) → state_likely_stale escalate
        # do KOORD. Operator decyduje na podstawie panel-em zamiast nieaktualnego state.
        # Filozofia: pojedyncze divergence = OK (B penalty wystarczy), masowe = signal że
        # panel_watcher ma lag i pipeline nie powinien proponować bo wszyscy mogą być stale.
        _stale_signal_count = 0
        _max_packs_age = 0.0
        for _c in feasible[:5]:
            _csi = fleet_snapshot.get(_c.courier_id)
            if _csi is None:
                continue
            _signal = getattr(_csi, "panel_packs_oids_signal", []) or []
            _bag = getattr(_csi, "bag", []) or []
            _age = getattr(_csi, "panel_packs_cache_age_s", None)
            if _age is not None and _age > _max_packs_age:
                _max_packs_age = _age
            if len(_signal) > 0 and len(_bag) == 0:
                _stale_signal_count += 1
        if _max_packs_age > 60.0 and _stale_signal_count >= 2:
            _result_stale = PipelineResult(
                order_id=order_id,
                verdict="KOORD",
                reason=(
                    f"state_likely_stale (panel_packs_age={_max_packs_age:.1f}s, "
                    f"n_stale_signal={_stale_signal_count}; pool={len(feasible)})"
                ),
                best=top[0],
                candidates=top,
                pickup_ready_at=pickup_ready_at,
                restaurant=restaurant,
                delivery_address=delivery_address,
                pool_total_count=len(candidates),
                pool_feasible_count=len(feasible),
            )
            _classify_and_set_auto_route(_result_stale, fleet_snapshot, order_event, now=now, v328_fail_causes=_v328_fail_causes)
            return _result_stale

        # P3-D6 path B 2026-05-11 — geometry-blind fallback KOORD escalation.
        # Tech debt #29 + Lekcja #108: gdy wszyscy kandydaci spadli na
        # `_greedy_plan` (strategy=greedy_fallback — OR-Tools INFEASIBLE) AND
        # wszyscy mają negative R1 corridor cosine (drops w przeciwnych
        # kierunkach), greedy geometry-blind nie ma żadnej ścieżki do dobrej
        # trasy. Eskaluj człowiekowi (Adrian) zamiast auto-proponować low-quality
        # bundle. Case 472338 Ogniomistrz 10.05 archetype: zigzag plan przeszedł.
        # E2 sprint 2026-05-17: warunek był `ortools_rejected_v3274` (V3.27.4
        # reject→greedy). Po wycofaniu tej ścieżki OR-Tools nie jest już
        # odrzucany; jedyny pozostały geometrycznie ślepy fallback to
        # `greedy_fallback` (realny INFEASIBLE) — na ten enum przepinamy.
        if len(feasible) >= 2:
            _all_greedy_fallback = all(
                getattr(getattr(_c, "plan", None), "strategy", "") == "greedy_fallback"
                for _c in feasible
            )
            _all_negative_cos = all(
                (_c.metrics.get("r1_avg_pairwise_cosine") if _c.metrics else None) is not None
                and _c.metrics.get("r1_avg_pairwise_cosine") < 0
                for _c in feasible
            )
            if _all_greedy_fallback and _all_negative_cos:
                _result_geo_blind = PipelineResult(
                    order_id=order_id,
                    verdict="KOORD",
                    reason=(
                        f"geometry_blind_fallback (all {len(feasible)} kandydaci "
                        f"strategy=greedy_fallback + cos<0; escalate)"
                    ),
                    best=top[0],
                    candidates=top,
                    pickup_ready_at=pickup_ready_at,
                    restaurant=restaurant,
                    delivery_address=delivery_address,
                    pool_total_count=len(candidates),
                    pool_feasible_count=len(feasible),
                )
                _classify_and_set_auto_route(_result_geo_blind, fleet_snapshot, order_event, now=now, v328_fail_causes=_v328_fail_causes)
                return _result_geo_blind

        # V3.28 ANCHOR FIX 2026-05-10 — Adrian doktryna: min_score_threshold dla PROPOSE.
        # Gdy best.score < MIN_PROPOSE_SCORE → KOORD zamiast PROPOSE (all_candidates_low_score).
        # Diagnoza 2026-05-10 472189: PROPOSE Andrei score=-50 + Mateusz Bro alt -1047 =
        # both bad, operator i tak nadpisał (89% override rate). MIN_PROPOSE_SCORE=-100
        # = tylko ekstremalnie złe (jak -1047) lecą do KOORD; lekko ujemne (peak rescue) zostają.
        #
        # INCYDENT-FIX 2026-06-12 (post-flip SYNCWORKA/LOADGOV, ALWAYS-PROPOSE):
        # kary RANKINGOWE (sync_spread -150, loadgov -40) po flipie 11.06 14:28
        # wepchnęły 92 decyzje/30h w KOORD all_candidates_low_score (KOORD-rate
        # 15,6%→50%) — próg był kalibrowany na SUROWYCH score (sprzed delt).
        # Bramka ocenia więc score Z WYŁĄCZENIEM delt aplikowanych flagami
        # decyzyjnymi: kara ma poprawiać ranking (kto wygrywa), NIGDY nie
        # wpychać decyzji w ciszę. Serializowany score zostaje z deltami
        # (uczciwa wartość rankingowa).
        _best_score = getattr(top[0], "score", None)
        _best_score_gate = _gate_score_excluding_ranking_deltas(top[0])
        _min_prop_gate = _min_propose_score()  # SCALE-01: flags.json hot → common
        if isinstance(_best_score, (int, float)) and _best_score_gate is not None \
                and _best_score_gate < _min_prop_gate \
                and not _always_propose_on():  # ALWAYS-PROPOSE: nie milcz, proponuj feasible best
            _result_low = PipelineResult(
                order_id=order_id,
                verdict="KOORD",
                reason=(
                    f"all_candidates_low_score (best={top[0].courier_id} "
                    f"score={_best_score:.1f}<{_min_prop_gate:.0f}; "
                    f"feasible={len(feasible)})"
                ),
                best=top[0],
                candidates=top,
                pickup_ready_at=pickup_ready_at,
                restaurant=restaurant,
                delivery_address=delivery_address,
                pool_total_count=len(candidates),
                pool_feasible_count=len(feasible),
            )
            _classify_and_set_auto_route(_result_low, fleet_snapshot, order_event, now=now, v328_fail_causes=_v328_fail_causes)
            return _result_low

        # BUG C verdict-gate (2026-05-27): jeśli plan.pickup_at[oid] (ETA z
        # route_simulator) odjeżdża od commit czas_kuriera_warsaw (z bag_context
        # bag-orderów lub z decision dla nowego ordera) o > próg → KOORD. Marker
        # `⚠plan~HH:MM` w renderze (telegram_approver._route_lines_v2) tylko
        # surface'uje rozjazd, ale verdict pozostaje PROPOSE/AUTO — operator
        # może zatwierdzić fikcję. Case #12 27.05: Retrospekcja commit 14:16,
        # plan 14:32, divergence 16 min — system PROPOSE'ował zamiast eskalować.
        # Gate: per-oid one-sided (plan_eta - commit > próg, plan PÓŹNIEJ niż
        # commit), bo to oznacza zimną potrawę. Reverse (plan wcześniej) =
        # wait_courier penalty już to łapie.
        _cd_top = top[0] if top else None
        _cd_plan = getattr(_cd_top, "plan", None) if _cd_top is not None else None
        if (C.decision_flag("ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE")
                and _cd_plan is not None):
            _cd_threshold = float(getattr(
                C, "COMMIT_DIVERGENCE_VERDICT_KOORD_MIN_MIN", 10.0))
            _cd_plan_pickup_at = getattr(_cd_plan, "pickup_at", None) or {}
            # Build commit map: oid → czas_kuriera_warsaw ISO (bag-orders + new).
            _cd_bag_context = (_cd_top.metrics or {}).get("bag_context", []) or []
            _cd_commit_iso: Dict[str, Optional[str]] = {}
            for _bc in _cd_bag_context:
                _bc_oid = str(_bc.get("order_id") or "")
                if _bc_oid:
                    _cd_commit_iso[_bc_oid] = _bc.get("czas_kuriera_warsaw")
            # Nowy order: czas_kuriera_warsaw może być w order_event (jeśli
            # firma deklaruje hard commit z góry — F2.1c R8 T_KUR).
            _cd_new_ck = order_event.get("czas_kuriera_warsaw")
            if _cd_new_ck:
                _cd_commit_iso[str(order_id)] = _cd_new_ck
            # Compute max divergence (one-sided: plan_eta - commit, only positive).
            _cd_max_div_min = 0.0
            _cd_worst_oid: Optional[str] = None
            for _oid, _plan_dt in _cd_plan_pickup_at.items():
                _commit_iso = _cd_commit_iso.get(str(_oid))
                if not _commit_iso or _plan_dt is None:
                    continue
                try:
                    _commit_dt = datetime.fromisoformat(
                        str(_commit_iso).replace("Z", "+00:00"))
                    if _commit_dt.tzinfo is None:
                        _commit_dt = _commit_dt.replace(tzinfo=timezone.utc)
                    _plan_dt_norm = _plan_dt
                    if isinstance(_plan_dt, str):
                        _plan_dt_norm = datetime.fromisoformat(
                            _plan_dt.replace("Z", "+00:00"))
                    if _plan_dt_norm.tzinfo is None:
                        _plan_dt_norm = _plan_dt_norm.replace(tzinfo=timezone.utc)
                    _diff_min = (_plan_dt_norm - _commit_dt).total_seconds() / 60.0
                    if _diff_min > _cd_max_div_min:
                        _cd_max_div_min = _diff_min
                        _cd_worst_oid = str(_oid)
                except (TypeError, ValueError, AttributeError):
                    continue  # Skip oid z nieparseowalnym timestampem (fail-soft).
            if _cd_max_div_min > _cd_threshold:
                _result_cd = PipelineResult(
                    order_id=order_id,
                    verdict="KOORD",
                    reason=(
                        f"commit_divergence_gate (best={_cd_top.courier_id} "
                        f"worst_oid={_cd_worst_oid} divergence={_cd_max_div_min:.1f}min > "
                        f"{_cd_threshold:.0f}min threshold; plan_eta later than commit, "
                        f"zimna potrawa ryzyko)"
                    ),
                    best=_cd_top,
                    candidates=top,
                    pickup_ready_at=pickup_ready_at,
                    restaurant=restaurant,
                    delivery_address=delivery_address,
                    pool_total_count=len(candidates),
                    pool_feasible_count=len(feasible),
                )
                # Surface dla render Telegram (banner KOORD z worst oid + divergence).
                _result_cd.commit_divergence_redirect = {
                    "max_divergence_min": round(_cd_max_div_min, 1),
                    "worst_oid": _cd_worst_oid,
                    "threshold_min": _cd_threshold,
                }
                _classify_and_set_auto_route(
                    _result_cd, fleet_snapshot, order_event, now=now, v328_fail_causes=_v328_fail_causes)
                return _result_cd

        # === Difficult-case KOORD redirect (2026-05-28) ===
        # Gdy R1+CB obniżyło wszystkich kandydatów poniżej DIFFICULT_CASE_SCORE_FLOOR
        # (default -30), system uznaje że "geometria jest trudna" — żadna
        # propozycja nie jest dobra. Zamiast forsować najmniej-zła propozycję,
        # eskaluje do KOORD i loguje case do difficult_case_log.jsonl jako
        # materiał uczący (sprint plan: korpus do FIX-B kalibracji / Faza 6
        # klastry osiedli). Reguła Adriana: "system mówi: zapytaj koordynatora".
        # Default OFF — shadow-first. Aktywacja po ACK Etap 3.
        try:
            _diff_floor = float(getattr(C, "DIFFICULT_CASE_SCORE_FLOOR", -30.0))
            _diff_top_score = float(getattr(top[0], "score", 0.0) or 0.0)
            _diff_above = sum(
                1 for _c in top if float(getattr(_c, "score", 0.0) or 0.0) >= _diff_floor
            )
            # Detect — zawsze (shadow); apply — tylko gdy flag ON.
            _diff_should_redirect = (top and _diff_top_score < _diff_floor)
            if _diff_should_redirect and C.decision_flag(
                    "ENABLE_DIFFICULT_CASE_KOORD_REDIRECT"):
                _diff_best_metrics = getattr(top[0], "metrics", {}) or {}
                _diff_payload = {
                    "max_score": round(_diff_top_score, 2),
                    "floor": _diff_floor,
                    "n_candidates_above_floor": _diff_above,
                    "best_candidate_id": getattr(top[0], "courier_id", None),
                    "best_cosine": _diff_best_metrics.get("r1_avg_pairwise_cosine"),
                    "best_max_bag_min": _diff_best_metrics.get("max_bag_time_min"),
                    "best_r5_detour_km": _diff_best_metrics.get("r5_pickup_detour_total_km"),
                }
                _result_diff = PipelineResult(
                    order_id=order_id,
                    verdict="KOORD",
                    reason=(
                        f"difficult_geometry_redirect (best={top[0].courier_id} "
                        f"max_score={_diff_top_score:.1f} < floor={_diff_floor:.0f}; "
                        f"n_above_floor={_diff_above}; geometryczny eskalator KOORD)"
                    ),
                    best=top[0],
                    candidates=top,
                    pickup_ready_at=pickup_ready_at,
                    restaurant=restaurant,
                    delivery_address=delivery_address,
                    pool_total_count=len(candidates),
                    pool_feasible_count=len(feasible),
                )
                _result_diff.difficult_case_redirect = _diff_payload
                # Append do dedykowanego logu (materiał uczący)
                _append_difficult_case_log({
                    "ts": now.isoformat(),
                    "order_id": order_id,
                    "restaurant": restaurant,
                    "delivery_address": delivery_address,
                    "verdict_redirected": "KOORD",
                    "verdict_legacy": "PROPOSE",
                    "payload": _diff_payload,
                    "top_candidates": [
                        {
                            "courier_id": getattr(_c, "courier_id", None),
                            "name": getattr(_c, "name", None),
                            "score": round(float(getattr(_c, "score", 0.0) or 0.0), 2),
                            "cosine": (getattr(_c, "metrics", {}) or {}).get("r1_avg_pairwise_cosine"),
                            "r5_detour_km": (getattr(_c, "metrics", {}) or {}).get("r5_pickup_detour_total_km"),
                            "max_bag_min": (getattr(_c, "metrics", {}) or {}).get("max_bag_time_min"),
                            "bag_size": (getattr(_c, "metrics", {}) or {}).get("r6_bag_size"),
                            "pos_source": getattr(getattr(_c, "courier_state", None), "pos_source", None),
                        }
                        for _c in top[:5]
                    ],
                    "operator_decision": None,  # async fill via reconciliation
                })
                _classify_and_set_auto_route(
                    _result_diff, fleet_snapshot, order_event, now=now, v328_fail_causes=_v328_fail_causes)
                return _result_diff
            elif _diff_should_redirect:
                # Flag OFF — zapisuj do shadow logu (best dict) by symulacja
                # mogła sprawdzić ile case'ów BYŁOBY redirectowanych. Pole
                # difficult_case_redirect_shadow w serializer.
                top[0].metrics["difficult_case_redirect_shadow"] = {
                    "max_score": round(_diff_top_score, 2),
                    "floor": _diff_floor,
                    "n_candidates_above_floor": _diff_above,
                }
        except Exception as _diff_e:
            log.warning(
                f"difficult_case_redirect exception order={order_id}: {_diff_e!r}"
            )

        # === Load-aware selection SHADOW (2026-06-07) — log-only, PEŁNY roster ===
        # Counterfactual dystrybucji load-aware vs argmax-best. ZERO zmiany
        # zachowania (nie dotyka best/feasible/top/verdiktu). Walidacja offline.
        loadaware_shadow = None
        if getattr(C, "ENABLE_LOADAWARE_SELECTION_SHADOW", False):
            try:
                loadaware_shadow = _compute_loadaware_shadow(candidates, feasible, top)
            except Exception as _la_e:
                log.warning(f"loadaware_shadow fail order={order_id}: {_la_e!r}")

        _result_pf = PipelineResult(
            order_id=order_id,
            verdict="PROPOSE",
            reason=f"feasible={len(feasible)} best={top[0].courier_id}",
            best=top[0],
            candidates=top,
            pickup_ready_at=pickup_ready_at,
            restaurant=restaurant,
            delivery_address=delivery_address,
            pool_total_count=len(candidates),
            pool_feasible_count=len(feasible),
        )
        # R-LATE-PICKUP: propozycja przedłużonego czasu odbioru (tier 1/2) dla renderu.
        _result_pf.pickup_extension_redirect = pickup_extension_redirect
        # R-LATE-PICKUP Opcja B (2026-05-31): stary-vs-nowy zwycięzca tieringu (shadow).
        _result_pf.late_pickup_shadow = late_pickup_shadow
        # MIN-DELIVERED-AT (Adrian 2026-06-25): min-total vs live winner (shadow, log-only).
        _result_pf.min_delivered_at_shadow = min_delivered_at_shadow
        # RESERVE-AWARE TIEBREAK (#3 top10 2026-06-29): wolny-vs-jadący tie-break (shadow, log-only).
        _result_pf.reserve_tiebreak_shadow = reserve_tiebreak_shadow
        # Fix #6 (2026-05-31): rozjazd zwycięzcy legacy-liniowa-R6 vs danger-R6 (shadow).
        _result_pf.r6_danger_shadow = r6_danger_shadow
        # Load-aware distribution counterfactual (2026-06-07) — shadow only.
        _result_pf.loadaware_shadow = loadaware_shadow
        _classify_and_set_auto_route(_result_pf, fleet_snapshot, order_event, now=now, v328_fail_causes=_v328_fail_causes)
        return _result_pf

    # R28 best_effort: NO candidates that still produced a plan (SLA-only rejections)
    # F2.1c: verdict PROPOSE (nie KOORD) — Telegram musi to zobaczyć, Adrian decyduje
    #
    # P3-D3 2026-05-11 (root cause 2): sort key primary = r6_per_order_violations count
    # (V3.28 P0 anchor=pickup_ready_at), nie legacy plan.sla_violations (anchor=TSP
    # pickup_at). Pre-fix: Jelenia 43 min carry przeszedł bo plan.sla_violations=0
    # (TSP pickup misaligned z real ready_at).
    def _r6_pov_count(c):
        if not hasattr(c, "metrics") or not c.metrics:
            return 99
        pov = c.metrics.get("r6_per_order_violations")
        return len(pov) if pov else 0

    # Sprint OBJ F3 / BUG-4: największe przekroczenie R6 (min) kandydata wg
    # objm_ (route_metrics.compute_plan_metrics, anchor=gotowość/picked_up).
    # 0.0 gdy brak metryki — conservative (brak danych → brak eskalacji).
    def _r6_breach_max(c):
        m = getattr(c, "metrics", None) or {}
        v = m.get("objm_r6_breach_max_min")
        return float(v) if isinstance(v, (int, float)) else 0.0

    # R-INTRA-RESTAURANT-GAP filter (2026-05-14, Opcja A): eliminuje kandydatów
    # z hard_reject z best_effort poolu. Bez tego best_effort PROPOSE wybierał
    # cid z gap 26 min Chicago Pizza (case 473251 19:35 UTC) bo MAYBE→NO override
    # w _v327_eval_courier nie zmieniał verdict gdy poprzedni był już NO. Po
    # filtrze: jeśli all candidates intra-gap-violating → spadamy do R29 SOLO
    # fallback (pusty bag, naturalnie eliminuje pair).
    def _intra_gap_reject(c):
        return bool((c.metrics or {}).get("intra_rest_gap_hard_reject"))
    with_plan = [c for c in candidates if c.plan is not None and not _intra_gap_reject(c)]
    # FEAS-01 / SEL-01 (2026-06-06): best_effort sortuje z bucketem pos_source + score
    # (mirror głównej selekcji) — bez tego no_gps z fikcyjnym BIALYSTOK_CENTER bił
    # informed kuriera z obrzeży. R6/SLA zostają PRIMARY (identycznie jak stary klucz),
    # bucket+score rozstrzygają WŚRÓD równych na R6/SLA. Kill-switch
    # ENABLE_BEST_EFFORT_POS_SOURCE_KEY=false (flags.json) → stary klucz.
    if C.flag("ENABLE_BEST_EFFORT_POS_SOURCE_KEY", default=True):
        with_plan.sort(key=_best_effort_sort_key)
    else:
        with_plan.sort(key=lambda c: (_r6_pov_count(c), c.plan.sla_violations, c.plan.total_duration_min))
    if with_plan:
        best = with_plan[0]
        best.best_effort = True
        # OBJM CARRY-INCLUSIVE SHADOW (2026-06-23): co BY wybrała selekcja gdyby PRIMARY był
        # objm_r6_breach_max (carry-aware) zamiast r6_per_order_violations (new-pickup-only,
        # ślepego na carry — case #482817). LOG-ONLY. flags.json hot. Walidacja PRZED
        # ENABLE_BEST_EFFORT_OBJM_R6_KEY (live-flip = osobna flaga + ACK).
        if C.flag("ENABLE_BEST_EFFORT_OBJM_SHADOW",
                  getattr(C, "ENABLE_BEST_EFFORT_OBJM_SHADOW", False)):
            _be_objm_cap = C.flag("BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN",
                                  getattr(C, "BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN", 40.0))
            _best_effort_objm_shadow(with_plan, best, new_order.order_id, cap_min=_be_objm_cap)
        # OBJM CARRY-INCLUSIVE LIVE-FLIP (2026-06-24, ENABLE_BEST_EFFORT_OBJM_R6_KEY, ACK Adrian):
        # gdy ON, REALNIE wybierz carry-aware guarded pick (_best_effort_objm_pick — JEDNO ŹRÓDŁO
        # PRAWDY z shadow) zamiast carry-ślepego _best_effort_sort_key (case #482817). flags.json
        # hot → rollback bez restartu. Defensywny: pick None → zostań na starym best (fail-open).
        # Telemetria best_effort_objm_* przeniesiona na realnie wybranego + marker live_*.
        if C.flag("ENABLE_BEST_EFFORT_OBJM_R6_KEY",
                  getattr(C, "ENABLE_BEST_EFFORT_OBJM_R6_KEY", False)):
            _be_live_cap = C.flag("BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN",
                                  getattr(C, "BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN", 40.0))
            _objm_pick = _best_effort_objm_pick(with_plan, new_order.order_id, cap_min=_be_live_cap)
            _carry_blind_cid = str(getattr(best, "courier_id", None))
            if _objm_pick is not None and _objm_pick is not best:
                try:
                    _src = getattr(best, "metrics", None) or {}
                    _dst = getattr(_objm_pick, "metrics", None)
                    if isinstance(_dst, dict):
                        for _k, _v in list(_src.items()):
                            if _k.startswith("best_effort_objm_"):
                                _dst[_k] = _v
                except Exception:
                    pass
                best = _objm_pick
                best.best_effort = True
            try:
                if isinstance(getattr(best, "metrics", None), dict):
                    best.metrics["best_effort_objm_live_key_on"] = True
                    best.metrics["best_effort_objm_live_flip"] = (
                        str(getattr(best, "courier_id", None)) != _carry_blind_cid)
                    best.metrics["best_effort_objm_live_from_cid"] = _carry_blind_cid
            except Exception:
                pass
        # FASTEST-PICKUP SHADOW (Adrian 2026-06-15): co BY wybrała selekcja „najszybszy
        # odbiór → potem najszybszy dowóz". LOG-ONLY — NIE zmienia `best` (live = stary
        # klucz). Walidacja w shadow_decisions przed ewentualnym flipem live. flags.json hot.
        if C.flag("ENABLE_BEST_EFFORT_FASTEST_PICKUP_SHADOW",
                  getattr(C, "ENABLE_BEST_EFFORT_FASTEST_PICKUP_SHADOW", False)):
            try:
                _fp_best = min(with_plan, key=lambda c: _best_effort_fastest_pickup_key(c, new_order.order_id))
                _live_pu = (getattr(best.plan, "pickup_at", {}) or {}).get(new_order.order_id)
                _fp_pu = (getattr(_fp_best.plan, "pickup_at", {}) or {}).get(new_order.order_id)
                _earlier = None
                if _live_pu is not None and _fp_pu is not None:
                    _earlier = round((_live_pu - _fp_pu).total_seconds() / 60.0, 1)  # >0 = shadow odbiera wcześniej
                best.metrics["best_effort_fastest_pickup_shadow"] = {
                    "live_cid": best.courier_id,
                    "live_pickup_eta": _live_pu.isoformat() if _live_pu is not None else None,
                    "live_pos_source": getattr(best, "pos_source", None),
                    "shadow_cid": _fp_best.courier_id,
                    "shadow_pickup_eta": _fp_pu.isoformat() if _fp_pu is not None else None,
                    "shadow_pos_source": getattr(_fp_best, "pos_source", None),  # blind-check: fikcyjny ETA?
                    "would_differ": _fp_best.courier_id != best.courier_id,
                    "shadow_pickup_earlier_min": _earlier,
                    "pool_size": len(with_plan),
                }
                if _fp_best.courier_id != best.courier_id:
                    log.info(
                        "BEST_EFFORT_FASTEST_PICKUP_SHADOW oid=%s live=%s shadow=%s earlier=%smin pool=%d"
                        % (new_order.order_id, best.courier_id, _fp_best.courier_id, _earlier, len(with_plan)))
            except Exception as _fp_e:
                log.warning(f"fastest_pickup_shadow fail oid={new_order.order_id}: {_fp_e!r}")
        # BUG E hotfix (2026-05-26, naprawiony 2026-05-27): best_effort z >=1
        # orderem łamiącym hard R6 (35 min thermal bag_time) → KOORD. Stricter
        # superset OBJ F3 — bez progu min-breach, ANY breach. Default ON. Reguła
        # Adriana: „już lepiej dać 10 min później i wrócić po to". Case D/E/F/G
        # 26.05 — 4 propozycje z carry 43-90 min uciekły jako best_effort
        # PROPOSE bo OBJ F3 próg=20 łapie tylko bag_time > 55. Nowy check łapie
        # bag_time > 35.
        #
        # Hotfix 2026-05-27 (case Mama Thai Bistro Michał K. K-393): poprzednia
        # implementacja iterowała tylko plan.pickup_at — z definicji „only for
        # orders picked up during this plan" (route_simulator_v2:194), czyli
        # NOWE pickupy. Picked_up carry (np. Sweet Fit z 10:05 jadące do
        # Mickiewicza w bagu, drop 10:55 = 50 min thermal) byli pomijani →
        # _be_max_bt liczone tylko z nowego pickupu (~16 min) → gate
        # NIE odpalał → propozycja wychodziła jako best_effort PROPOSE.
        #
        # Fix: czytamy plan.per_order_delivery_times (POD) — pole populowane
        # przez _compute_per_order_delivery_minutes (anchor=picked_up_at dla
        # carry, pickup_ready_at dla in-bag/new). Ten sam horizon co
        # route_metrics.compute_plan_metrics (objm_r6_breach_*) i feasibility
        # check_per_order_35min_rule — jedna kanoniczna definicja thermal
        # bag_time per order. Fallback (POD None / pusty) → conservative skip
        # gate, nie blokujemy decyzji bez danych.
        _be_plan = getattr(best, "plan", None)
        _be_bag_times: Dict[str, float] = {}
        if _be_plan is not None:
            _pod = getattr(_be_plan, "per_order_delivery_times", None) or {}
            for _oid, _elapsed in _pod.items():
                if isinstance(_elapsed, (int, float)):
                    _be_bag_times[str(_oid)] = float(_elapsed)
        _be_max_bt = max(_be_bag_times.values()) if _be_bag_times else 0.0
        _be_breach_orders = [
            _oid for _oid, _bt in _be_bag_times.items()
            if _bt > C.BAG_TIME_HARD_MAX_MIN
        ]
        if (getattr(C, "ENABLE_BEST_EFFORT_R6_KOORD_REDIRECT", True)
                and _be_max_bt > C.BAG_TIME_HARD_MAX_MIN
                and len(_be_breach_orders) >= 1
                and not _always_propose_on()):  # ALWAYS-PROPOSE: proponuj best_effort z bannerem ⚠️
            _result_be_e = PipelineResult(
                order_id=order_id,
                verdict="KOORD",
                reason=(
                    f"best_effort_r6_breach_v2 (best={best.courier_id} "
                    f"breach_orders={len(_be_breach_orders)} "
                    f"max_bag_time={_be_max_bt:.1f}min > "
                    f"{C.BAG_TIME_HARD_MAX_MIN}min; 0 feasible)"
                ),
                best=best,
                candidates=with_plan[:TOP_N_CANDIDATES],
                pickup_ready_at=pickup_ready_at,
                restaurant=restaurant,
                delivery_address=delivery_address,
                pool_total_count=len(candidates),
                pool_feasible_count=0,
            )
            # Surface dla render Telegram (banner KOORD z listą orderów w breach)
            _result_be_e.best_effort_r6_redirect = {
                "breach_count": len(_be_breach_orders),
                "max_bag_time_min": round(_be_max_bt, 1),
                "orders_in_breach": _be_breach_orders,
            }
            _classify_and_set_auto_route(
                _result_be_e, fleet_snapshot, order_event, now=now, v328_fail_causes=_v328_fail_causes)
            return _result_be_e
        # Sprint OBJ F3 / BUG-4 (2026-05-18): best_effort z najlepszym kandydatem
        # łamiącym hard R6 o > próg → KOORD, nie auto-PROPOSE. Diagnoza 474297:
        # kurier R6-doomed (carry 47-82 min), Ziomek proponował trasę-potworka.
        # Trasa przekraczająca R6 o 20+ min = decyzja koordynatora. Próg wysoki —
        # nie rusza buforów R-BUFFER-OK (soft zone 30-35). objm_r6_breach_max_min
        # liczony przez compute_plan_metrics — wiarygodny dla kandydatów z planem.
        _be_r6_breach = _r6_breach_max(best)
        if (C.decision_flag("ENABLE_OBJ_F3_BEST_EFFORT_R6_KOORD")
                and _be_r6_breach > C.OBJ_F3_R6_BREACH_KOORD_MIN
                and not _always_propose_on()):  # ALWAYS-PROPOSE: proponuj best_effort z bannerem ⚠️
            _result_be_r6 = PipelineResult(
                order_id=order_id,
                verdict="KOORD",
                reason=(
                    f"best_effort_r6_breach (best={best.courier_id} "
                    f"r6_breach={_be_r6_breach:.0f}min > "
                    f"{C.OBJ_F3_R6_BREACH_KOORD_MIN:.0f}; 0 feasible)"
                ),
                best=best,
                candidates=with_plan[:TOP_N_CANDIDATES],
                pickup_ready_at=pickup_ready_at,
                restaurant=restaurant,
                delivery_address=delivery_address,
                pool_total_count=len(candidates),
                pool_feasible_count=0,
            )
            _classify_and_set_auto_route(
                _result_be_r6, fleet_snapshot, order_event, now=now, v328_fail_causes=_v328_fail_causes)
            return _result_be_r6
        # P3-D3 2026-05-11 (root cause 3): MIN_PROPOSE_SCORE gate aligned z feasible
        # branch (line ~2800). Pre-fix: best_effort skip gate → score=-390 carry
        # przeszedł jako PROPOSE (Bartek O. 187/196 min case 10.05).
        _be_best_score = getattr(best, "score", None)
        _min_prop_be = _min_propose_score()  # SCALE-01: flags.json hot → common
        if (isinstance(_be_best_score, (int, float)) and _be_best_score < _min_prop_be
                and not _always_propose_on()):  # ALWAYS-PROPOSE: proponuj best_effort z bannerem ⚠️
            _be_r6_count = _r6_pov_count(best)
            _result_be_low = PipelineResult(
                order_id=order_id,
                verdict="KOORD",
                reason=(
                    f"best_effort_low_score (best={best.courier_id} "
                    f"score={_be_best_score:.1f}<{_min_prop_be:.0f}; "
                    f"r6_violations={_be_r6_count})"
                ),
                best=best,
                candidates=with_plan[:TOP_N_CANDIDATES],
                pickup_ready_at=pickup_ready_at,
                restaurant=restaurant,
                delivery_address=delivery_address,
                pool_total_count=len(candidates),
                pool_feasible_count=0,
            )
            _classify_and_set_auto_route(_result_be_low, fleet_snapshot, order_event, now=now, v328_fail_causes=_v328_fail_causes)
            return _result_be_low
        _result_be = PipelineResult(
            order_id=order_id,
            verdict="PROPOSE",
            reason=f"best_effort (0 feasible, r6_violations={_r6_pov_count(best)}, legacy_sla_v={best.plan.sla_violations})",
            best=best,
            candidates=with_plan[:TOP_N_CANDIDATES],
            pickup_ready_at=pickup_ready_at,
            restaurant=restaurant,
            delivery_address=delivery_address,
            pool_total_count=len(candidates),
            pool_feasible_count=0,
        )
        _classify_and_set_auto_route(_result_be, fleet_snapshot, order_event, now=now, v328_fail_causes=_v328_fail_causes)
        return _result_be

    # R29 SOLO fallback: zamiast SKIP — spróbuj przydzielić SOLO (pusty bag, ignoruje R1/R5/R8)
    solo_best = None
    solo_best_score = -999
    for cid, cs in fleet_snapshot.items():
        courier_pos = _sanitize_courier_pos(getattr(cs, "pos", None))
        if courier_pos is None:
            continue
        try:
            sv, sr, sm, sp = check_feasibility_v2(
                courier_pos=tuple(courier_pos),
                bag=[],  # pusty bag = solo
                new_order=new_order,
                shift_end=getattr(cs, "shift_end", None),
                shift_start=getattr(cs, "shift_start", None),
                now=now,
                sla_minutes=35,
                pos_source=getattr(cs, "pos_source", None),  # V3.28 ETAP 2 — clamp gate
                courier_tier=getattr(cs, "tier_bag", None),  # 2026-05-17 tier-aware DWELL
                schedule_source_stale=getattr(cs, "schedule_source_stale", False),  # D2 (audyt 2026-05-28)
                pos_from_store=getattr(cs, "pos_from_store", False),  # Z-06 (audyt 2026-06-10)
            )
            if sv in ("YES", "MAYBE") and sp is not None:
                sc = sm.get("pickup_dist_km", 999)
                # Prostszy scoring: bliższy = lepszy
                solo_score = 100 - sc * 10
                if solo_score > solo_best_score:
                    solo_best_score = solo_score
                    solo_best = Candidate(
                        courier_id=cid,
                        name=getattr(cs, "name", cid),
                        score=round(solo_score, 2),
                        feasibility_verdict=sv,
                        feasibility_reason=f"solo_fallback ({sr})",
                        plan=sp,
                        metrics={**sm, "solo_fallback": True, "pos_source": getattr(cs, "pos_source", "no_gps")},
                    )
        except Exception:
            pass

    if solo_best is not None:
        _result_solo = PipelineResult(
            order_id=order_id,
            verdict="PROPOSE",
            reason=f"solo_fallback (R1/R5/R8 ignored, fleet_n={len(candidates)})",
            best=solo_best,
            candidates=candidates,
            pickup_ready_at=pickup_ready_at,
            restaurant=restaurant,
            delivery_address=delivery_address,
            pool_total_count=len(candidates),
            pool_feasible_count=0,
        )
        _classify_and_set_auto_route(_result_solo, fleet_snapshot, order_event, now=now, v328_fail_causes=_v328_fail_causes)
        return _result_solo

    # R29 absolutny fallback: nikt nie przechodzi nawet solo — KOORD
    return PipelineResult(
        order_id=order_id,
        verdict="KOORD",
        reason=f"no_solo_candidates (fleet_n={len(candidates)}) — wszyscy odrzuceni nawet solo",
        best=None,
        candidates=candidates,
        pickup_ready_at=pickup_ready_at,
        restaurant=restaurant,
        delivery_address=delivery_address,
        pool_total_count=len(candidates),
        pool_feasible_count=0,
    )
