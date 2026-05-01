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
import math
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
        _eb_emit("CZAS_KURIERA_UPDATED",
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
)


def _is_blind_empty_cand(c) -> bool:
    """V3.16: kandydat z synthetic pos (no_gps/pre_shift/none) i pustym bagiem."""
    ps = c.metrics.get("pos_source") if hasattr(c, "metrics") and c.metrics else None
    bsize = c.metrics.get("r6_bag_size", 0) if hasattr(c, "metrics") and c.metrics else 0
    return ps in BLIND_POS_SOURCES and (bsize or 0) == 0


def _is_informed_cand(c) -> bool:
    """V3.16: kandydat z real pos source (fresh GPS lub recent panel activity)."""
    ps = c.metrics.get("pos_source") if hasattr(c, "metrics") and c.metrics else None
    return ps in INFORMED_POS_SOURCES


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
            city = (entry.get("city") or "Białystok").strip()
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
        last_drop_district = drop_zone_from_address(last_drop_addr, "Białystok")
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


def _v325_new_courier_penalty(feasible: list, order_id=None) -> list:
    """V3.25 STEP C (R-04 NEW-COURIER-CAP gradient).

    Post-scoring penalty layer dla kurierów z tier_label='new'. Mimicked po
    _demote_blind_empty pattern (V3.16) — read-modify candidate.score, re-sort.

    Logic per candidate gdzie metrics.cs_tier_label == 'new':
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

    NEG_INF = -1e9
    for cand in feasible:
        m = getattr(cand, "metrics", {}) or {}
        if m.get("cs_tier_label") != "new":
            continue
        bag_before = m.get("bag_size_before", 0) or 0
        if bag_before >= C.V325_NEW_COURIER_BAG_HARD_SKIP_AT:
            cand.score = NEG_INF
            m["v325_new_courier_penalty"] = NEG_INF
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


def _demote_blind_empty(feasible: list, order_id=None) -> list:
    """V3.16 demotion: jeśli top-1 jest blind+empty AND istnieje informed alt,
    reorder — informed first (stable), other middle, blind+empty last.
    Guard "all blind": jeśli żadnego informed → zostaw bez zmian.
    """
    try:
        flag = bool(getattr(C, "ENABLE_NO_GPS_EMPTY_DEMOTE", True))
    except Exception:
        flag = True
    if not flag or not feasible:
        return feasible
    if not _is_blind_empty_cand(feasible[0]):
        return feasible
    informed = [c for c in feasible if _is_informed_cand(c)]
    if not informed:
        return feasible  # all blind — nie degraduj (empty shift edge)
    original_top_cid = feasible[0].courier_id
    other = [c for c in feasible
             if not _is_informed_cand(c) and not _is_blind_empty_cand(c)]
    blind_empty = [c for c in feasible if _is_blind_empty_cand(c)]
    reordered = informed + other + blind_empty
    log.info(
        f"NO_GPS_DEMOTE order={order_id}: top cid={original_top_cid} "
        f"(no_gps+empty) demoted; informed_alts={len(informed)}; "
        f"new_top_cid={reordered[0].courier_id}"
    )
    return reordered


def _point_to_segment_km(p, a, b) -> float:
    """Najkrótsza odległość punktu p od odcinka [a, b] w km.
    Equirectangular projection — wystarczająca dla skali Białegostoku (<30 km)."""
    lat0 = (a[0] + b[0] + p[0]) / 3.0
    coslat = math.cos(math.radians(lat0))
    def to_xy(pt):
        return (pt[1] * coslat * 111.32, pt[0] * 111.32)
    ax, ay = to_xy(a)
    bx, by = to_xy(b)
    px, py = to_xy(p)
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    proj_x = ax + t * dx
    proj_y = ay + t * dy
    return ((px - proj_x) ** 2 + (py - proj_y) ** 2) ** 0.5


def _min_dist_to_route_km(point, courier_pos, bag_dropoffs) -> Optional[float]:
    """Min dystans od punktu do polyline kurier→bag_dropoff_1→bag_dropoff_2...
    None gdy bag pusty lub brak coords."""
    if not bag_dropoffs:
        return None
    nodes = [courier_pos] + [d for d in bag_dropoffs if d]
    if len(nodes) < 2:
        return None
    return min(_point_to_segment_km(point, nodes[i], nodes[i+1]) for i in range(len(nodes)-1))


EARLY_BIRD_THRESHOLD_MIN = 60
# Sprint-1 2026-04-30 (logging extension): bumped 5→16 to capture full feasible
# pool dla counterfactual analysis (PANEL_OVERRIDE pairwise). Faza 2 baseline
# pool mean=10.24, max=17 — top-15 alternatives + best=16 covers ~100% pool.
TOP_N_CANDIDATES = 16
DEFAULT_FLEET_PREP_VARIANCE_MIN = 13.0


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
    pickup_c = d.get("pickup_coords") or (0.0, 0.0)
    deliv_c = d.get("delivery_coords") or (0.0, 0.0)
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
    return sim


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


def assess_order(
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
    pickup_coords = tuple(order_event.get("pickup_coords") or (0.0, 0.0))
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

    # Early bird → KOORD
    if pickup_at is not None:
        pu = pickup_at if pickup_at.tzinfo else pickup_at.replace(tzinfo=WARSAW)
        minutes_ahead = (pu.astimezone(timezone.utc) - now).total_seconds() / 60.0
        if minutes_ahead >= EARLY_BIRD_THRESHOLD_MIN:
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
        courier_pos = getattr(cs, "pos", None)
        if courier_pos is None:
            return None
        bag_raw = getattr(cs, "bag", []) or []
        bag_sim = [_bag_dict_to_ordersim(b) for b in bag_raw]

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
                log.warning(f"V3.27.1 pre_recheck oid_new={getattr(order, 'order_id', '?')} cid={cid} fail: {_e}")
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
                and pickup_coords != (0.0, 0.0)
                and pickup_coords[0] != 0.0):
            for b in bag_raw:
                if b.get("status") != "assigned":
                    continue
                bag_pc = b.get("pickup_coords")
                if not bag_pc:
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
        if (delivery_coords != (0.0, 0.0)
                and delivery_coords[0] != 0.0):
            bag_drops = [b.get("delivery_coords") for b in bag_raw if b.get("delivery_coords")]
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
                v327_drop_zones_audit = {
                    "new_zone": _v327_new_zone,
                    "bag_zones": _v327_bag_zones,
                    "min_factor": v327_min_drop_factor,
                    "score_mult": v327_bundle_score_mult,
                }
            except Exception as _v327_z_e:
                log.warning(
                    f"V3.27 Bug Z compute fail: {type(_v327_z_e).__name__}: {_v327_z_e}"
                )

        # SLA 45 min dla bundli (per dane historyczne 86%/95% w 35/45 min).
        # Solo (pusty bag) zostaje 35 min — nie poluzowujemy sytuacji bez bundlingu.
        sla_minutes = 45 if bag_sim else 35

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
                    _saved = _pm_read.load_plan(str(cid), active_bag_oids=_bag_oids)
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
        # Fallback do haversine × road_factor / fleet_speed × traffic_mult tylko przy
        # hard exception (osrm_client samo handluje circuit-breaker → haversine fallback).
        try:
            from dispatch_v2 import osrm_client as _osrm_v327
            _osrm_drive_res = _osrm_v327.route(tuple(courier_pos), pickup_coords)
            drive_min = float(_osrm_drive_res.get("duration_min") or 0.0)
            _drive_km_from_courier = float(_osrm_drive_res.get("distance_km") or 0.0)
        except Exception as _v327_e:
            log.warning(
                f"V3.27 drive_min OSRM exception, fallback to haversine + traffic_mult: "
                f"{type(_v327_e).__name__}: {_v327_e}"
            )
            _drive_km_from_courier = haversine(tuple(courier_pos), pickup_coords) * HAVERSINE_ROAD_FACTOR_BIALYSTOK
            drive_min = (_drive_km_from_courier / fleet_speed_kmh) * 60.0 if fleet_speed_kmh > 0 else 0.0
            try:
                drive_min *= float(C.get_traffic_multiplier(now))
            except Exception:
                pass  # safety — fleet_speed_kmh fallback bez mult
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
        if plan is not None:
            r6_max_bag_time = metrics.get("r6_max_bag_time_min")
            if r6_max_bag_time is None:
                log.warning(
                    f"R6 soft skip: metrics.r6_max_bag_time_min missing "
                    f"despite plan!=None (expected after krok #6 restart)"
                )
                r6_max_bag_time = 0.0
            if r6_max_bag_time > C.BAG_TIME_SOFT_MIN:
                bonus_r6_soft_pen = -(r6_max_bag_time - C.BAG_TIME_SOFT_MIN) * C.BAG_TIME_SOFT_PENALTY_PER_MIN
            else:
                bonus_r6_soft_pen = 0.0

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
                    v3273_wait_courier_per_pickup.append({
                        "oid": _str_oid_273,
                        "wait_min": round(_wait_273, 2),
                        "penalty": round(_pen_273, 2),
                        "hard_reject": _reject_273,
                    })
                    if _reject_273:
                        v3273_wait_courier_hard_reject = True
                    bonus_v3273_wait_courier += _pen_273
                    if _wait_273 > v3273_wait_courier_max_min:
                        v3273_wait_courier_max_min = _wait_273
                        v3273_wait_courier_max_oid = _str_oid_273
                        if _str_oid_273 == str(_new_oid_273):
                            v3273_wait_courier_max_restaurant = restaurant_name
                        else:
                            for _b_273 in bag_raw:
                                if str(_b_273.get("order_id") or "") == _str_oid_273:
                                    v3273_wait_courier_max_restaurant = _b_273.get("restaurant")
                                    break
                except Exception:
                    continue

        # Wczytaj rule_weights (adaptive penalties R1/R5/R8)
        try:
            import json as _json
            _rw_path = "/root/.openclaw/workspace/dispatch_state/rule_weights.json"
            with open(_rw_path) as _f:
                _rw = _json.load(_f)
        except Exception:
            _rw = {}

        # R1 soft penalty (delivery spread violation)
        _r1_viol = metrics.get("r1_violation_km") or 0.0
        bonus_r1_soft_pen = _r1_viol * _rw.get("R1_spread_per_km", -8.0) if _r1_viol > 0 else 0.0

        # R5 soft penalty (mixed pickup spread violation)
        _r5_viol = metrics.get("r5_violation_km") or 0.0
        bonus_r5_soft_pen = _r5_viol * _rw.get("R5_pickup_per_km", -6.0) if _r5_viol > 0 else 0.0

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
        if C.ENABLE_V319H_BUG2_WAVE_CONTINUATION:
            if free_at_dt is not None and pickup_at is not None:
                _pu_utc = pickup_at if pickup_at.tzinfo else pickup_at.replace(tzinfo=WARSAW)
                _pu_utc = _pu_utc.astimezone(timezone.utc)
                _fa_utc = free_at_dt if free_at_dt.tzinfo else free_at_dt.replace(tzinfo=timezone.utc)
                _gap_sec = (_pu_utc - _fa_utc).total_seconds()
                bug2_interleave_gap_min = round(_gap_sec / 60.0, 2)
                bonus_bug2_continuation = C.bug2_wave_continuation_bonus(
                    bug2_interleave_gap_min
                )
            # edge: bag empty albo pickup_at=None → gap=None, bonus=0 (default)

        # V3.26 STEP 3 (R-09 WAVE-GEOMETRIC-VETO): refinement BUG-2.
        # Veto bonus gdy geometryczna incoherence: km(last_drop → new_pickup) > threshold.
        # Bug case Adrian Q&A 22.04 Kacper Sa: gap OK ale drops na 2 końcach miasta.
        v326_wave_veto = False
        v326_wave_geometric_km = None
        if (C.ENABLE_V326_WAVE_GEOMETRIC_VETO and bonus_bug2_continuation > 0
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
                    if _last_drop and _new_pickup:
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

        # V3.28 FIX_C: Bundle deliv_spread hard cap (FILOZ-3 peak-safe gate).
        # Cross-restaurant bundle scoring (bonus_l2 cross-pickup proximity + bug2
        # continuation) currently NIE patrzy na deliv_spread. Drops w przeciwnych
        # częściach miasta dostają full bonus pomimo trasy chaotic (#469834).
        # Gate zeruje bonus_l2 + bonus_bug2_continuation gdy bag>=1 i deliv_spread
        # przekracza cap. bonus_l1 SR pozostaje (osobny mechanizm, drop_proximity
        # SR-only już guarded). Default OFF (env ENABLE_BUNDLE_DELIV_SPREAD_CAP=1).
        fix_c_applied = False
        fix_c_deliv_spread_km = metrics.get("deliv_spread_km")
        if (C.ENABLE_BUNDLE_DELIV_SPREAD_CAP
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

        # Suma penalties (BUG-4 soft penalty dodany do puli)
        # V3.25 STEP B (R-01): pre-shift soft penalty z feasibility metrics
        bonus_v325_pre_shift_soft = float(metrics.get("v325_pre_shift_soft_penalty", 0) or 0)
        bonus_penalty_sum = (bonus_r6_soft_pen or 0.0) + bonus_r1_soft_pen + bonus_r5_soft_pen + bonus_r8_soft_pen + bonus_r9_stopover + bonus_r9_wait_pen + bonus_bug4_cap_soft + bonus_v325_pre_shift_soft + bonus_v3273_wait_courier
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

        final_score = score_result["total"] + bundle_bonus + timing_gap_bonus + wave_bonus + bonus_penalty_sum + bonus_bug2_continuation + v324a_extension_penalty

        # V3.27 Bug Z Q5: SOFT bundle score multiplier dla cross-quadrant bag.
        # 0.0 (cross-quadrant) → score *= 0.1
        # 0.5 (adjacent) → score *= 0.7
        # 1.0 (same quadrant) → score *= 1.0 (unchanged)
        # Gated by flag (v327_bundle_score_mult=1.0 gdy flag=False lub empty bag).
        v327_score_pre_mult = final_score
        if v327_bundle_score_mult != 1.0:
            final_score = final_score * v327_bundle_score_mult

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
            "drive_min": round(drive_min, 1),
            "eta_pickup_utc": eta_pickup_utc.isoformat(),
            "eta_drive_utc": drive_arrival_utc.isoformat(),
            "eta_source": eta_source,
            "pos_source": getattr(cs, "pos_source", None),
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
            # V3.27 Bug Z metrics (observability)
            "v327_min_drop_factor": v327_min_drop_factor,
            "v327_bundle_score_mult": round(v327_bundle_score_mult, 3) if v327_bundle_score_mult != 1.0 else 1.0,
            "v327_corridor_mult_applied": round(v327_corridor_mult_applied, 3),
            "v327_score_pre_mult": round(v327_score_pre_mult, 2) if v327_bundle_score_mult != 1.0 else None,
            "v327_drop_zones_audit": v327_drop_zones_audit,
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
            "bonus_r1_soft_pen": round(bonus_r1_soft_pen, 2),
            "bonus_r5_soft_pen": round(bonus_r5_soft_pen, 2),
            "bonus_r8_soft_pen": round(bonus_r8_soft_pen, 2),
            "r1_violation_km": metrics.get("r1_violation_km", 0.0),
            "r5_violation_km": metrics.get("r5_violation_km", 0.0),
            "r8_violation_min": metrics.get("r8_violation_min", 0.0),
            "bonus_r9_stopover": round(bonus_r9_stopover, 2),
            "bonus_r9_wait_pen": round(bonus_r9_wait_pen, 2),
            # V3.27.1: A/B comparison fields (legacy = always computed; v327 = 0 gdy flag=False)
            "bonus_r9_wait_pen_legacy": round(bonus_r9_wait_pen_legacy, 2),
            "bonus_r9_wait_pen_v327": round(bonus_r9_wait_pen_v327, 2),
            "bonus_v3273_wait_courier": round(bonus_v3273_wait_courier, 2),
            "v3273_wait_courier_max_min": round(v3273_wait_courier_max_min, 2),
            "v3273_wait_courier_max_restaurant": v3273_wait_courier_max_restaurant,
            "v3273_wait_courier_max_oid": v3273_wait_courier_max_oid,
            "v3273_wait_courier_hard_reject": v3273_wait_courier_hard_reject,
            "v3273_wait_courier_per_pickup": v3273_wait_courier_per_pickup,
            "bonus_penalty_sum": round(bonus_penalty_sum, 2),
            # Transparency OPCJA A (2026-04-19): order_id → (restaurant, delivery_address)
            # mapping dla route section w telegram_approver. Per-courier bag snapshot.
            "bag_context": [
                {
                    "order_id": str(b.get("order_id") or ""),
                    "restaurant": b.get("restaurant"),
                    "delivery_address": b.get("delivery_address"),
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
            # V3.24-A extension metrics
            "v324a_extension_min": round(v324a_extension_min, 2) if v324a_extension_min is not None else None,
            "v324a_extension_penalty": v324a_extension_penalty,
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

        # V3.27.3 hard reject: kurier idle >20 min pod restauracją (bag>=1).
        # Same pattern jak v324a — override MAYBE → NO, nie przebijamy wcześniejszego NO.
        if v3273_wait_courier_hard_reject and verdict == "MAYBE":
            verdict = "NO"
            _rest_273 = v3273_wait_courier_max_restaurant or "?"
            reason = f"v3273_wait_courier_hard_reject ({v3273_wait_courier_max_min:.1f}min > {C.V3273_WAIT_COURIER_HARD_REJECT_MIN} pod {_rest_273})"

        return Candidate(
            courier_id=str(cid),
            name=getattr(cs, "name", None),
            score=final_score,
            feasibility_verdict=verdict,
            feasibility_reason=reason,
            plan=plan,
            metrics=enriched_metrics,
        )
    # ── end _v327_eval_courier ──

    # V3.27 latency parallel: ThreadPoolExecutor map. 10 workers (lub mniej gdy
    # fleet < 10). Lambda unpacks (cid, cs) tuple z fleet_snapshot.items().
    # Single-courier fallback do sequential dla edge case (np. fleet=1).
    from concurrent.futures import ThreadPoolExecutor as _V327_TPE
    _v327_max_workers = max(1, min(10, len(fleet_snapshot)))
    if _v327_max_workers > 1:
        with _V327_TPE(max_workers=_v327_max_workers, thread_name_prefix="dispatch_v327") as _v327_pool:
            for _v327_c in _v327_pool.map(lambda kv: _v327_eval_courier(kv[0], kv[1]), list(fleet_snapshot.items())):
                if _v327_c is not None:
                    candidates.append(_v327_c)
    else:
        for _v327_cid, _v327_cs in fleet_snapshot.items():
            _v327_c = _v327_eval_courier(_v327_cid, _v327_cs)
            if _v327_c is not None:
                candidates.append(_v327_c)

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

    # V3.16: no_gps + empty bag demotion (patrz _demote_blind_empty).
    feasible = _demote_blind_empty(feasible, order_id)

    # V3.25 STEP C (R-04): NEW-COURIER-CAP gradient (flag-gated, default False).
    feasible = _v325_new_courier_penalty(feasible, order_id)

    # V3.26 STEP 2 (R-05): speed multiplier adjustment (flag-gated, default False).
    feasible = _v326_speed_multiplier_adjust(feasible, order_id)

    # V3.26 STEP 4 (R-10): fleet load balance adjustment (flag-gated, default False).
    feasible = _v326_fleet_load_balance(feasible, candidates, order_id)

    # V3.26 STEP 5 (R-06): multi-stop trajectory district-based (flag-gated, default False).
    feasible = _v326_multistop_trajectory(feasible, new_order, order_id)

    if feasible:
        top = feasible[:TOP_N_CANDIDATES]
        # V3.26 STEP 1 (R-11): build rationale dla BEST candidate (flag-gated).
        # Inject do best.metrics["v326_rationale"] żeby shadow_dispatcher
        # serializer + telegram_approver formatter mogli renderować.
        _rationale = _v326_build_rationale(top[0], feasible)
        if _rationale and hasattr(top[0], "metrics") and isinstance(top[0].metrics, dict):
            top[0].metrics["v326_rationale"] = _rationale

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
            except Exception as _lgbm_e:
                log.error(f"LGBM shadow unexpected fail order={order_id}: {_lgbm_e}", exc_info=True)
                if hasattr(top[0], "metrics") and isinstance(top[0].metrics, dict):
                    top[0].metrics["lgbm_shadow"] = {
                        "enabled": False,
                        "fallback_reason": "exception_in_pipeline",
                    }
        return PipelineResult(
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

    # R28 best_effort: NO candidates that still produced a plan (SLA-only rejections)
    # F2.1c: verdict PROPOSE (nie KOORD) — Telegram musi to zobaczyć, Adrian decyduje
    with_plan = [c for c in candidates if c.plan is not None]
    with_plan.sort(key=lambda c: (c.plan.sla_violations, c.plan.total_duration_min))
    if with_plan:
        best = with_plan[0]
        best.best_effort = True
        return PipelineResult(
            order_id=order_id,
            verdict="PROPOSE",
            reason=f"best_effort (0 feasible, best_violations={best.plan.sla_violations})",
            best=best,
            candidates=with_plan[:TOP_N_CANDIDATES],
            pickup_ready_at=pickup_ready_at,
            restaurant=restaurant,
            delivery_address=delivery_address,
            pool_total_count=len(candidates),
            pool_feasible_count=0,
        )

    # R29 SOLO fallback: zamiast SKIP — spróbuj przydzielić SOLO (pusty bag, ignoruje R1/R5/R8)
    solo_best = None
    solo_best_score = -999
    for cid, cs in fleet_snapshot.items():
        courier_pos = getattr(cs, "pos", None)
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
        return PipelineResult(
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
