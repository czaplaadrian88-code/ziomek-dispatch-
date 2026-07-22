"""core.candidates — pętla oceny per-kurier (K11, PRZENOSINY 1:1 z `_assess_order_impl`).

`eval_courier_inner` = dosłowna treść dawnej closure `_v327_eval_courier_inner`
(~2145 linii): sanity pozycji → worek → feasibility → scoring (19 kar) → Candidate.
`eval_courier` = dawny wrapper TLS (tracking legów OSRM per wątek puli).

Mechanika przenosin (dowód 1:1):
- 18 odczytów z closure → jawny `EvalContext` (pola nazwane identycznie; prolog
  odpakowuje do lokalnych NAZW 1:1, więc ciało pozostaje niezmienione);
- symbole module-level dispatch_pipeline (helpery kar, Candidate,
  check_feasibility_v2, get_fresh_czas_kuriera_for_bag, log, haversine, stałe)
  związane w PROLOGU aliasami `X = _dp.X` (odczyt atrybutu modułu przy KAŻDYM
  wywołaniu inner — ciało pozostaje bajt-w-bajt, a monkeypatch na atrybutach
  dispatch_pipeline obowiązuje) — zachowuje KAŻDY kontrakt monkeypatch na atrybutach
  dispatch_pipeline (tools/replay_feasibility: check_feasibility_v2; testy K07:
  get_fresh_czas_kuriera_for_bag) i wspólny logger (treść logów 1:1);
- lazy `import dispatch_pipeline as _dp` w funkcji (moduł-macierz w pełni
  zainicjalizowany w call-time; top-level w obie strony = cykl);
- pula wątków / _v328_eval_safe / mass-fail heuristic ZOSTAJĄ w impl (K12).
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from dispatch_v2 import common as C
from dispatch_v2 import scoring
from dispatch_v2 import calib_maps
from dispatch_v2 import eta_load_aware
from dispatch_v2 import pln_objective
# NOGPS-NEUTRAL-SCORE (2026-07-19): JEDNO źródło klasy Known|Unknown pozycji
# (F-3, courier_resolver) — konsumowane przy klasyfikacji road_km (czy dystans
# do odbioru policzono z pozycji-fikcji BIALYSTOK_CENTER, czy z realnej kotwicy).
# Bez cyklu importu: courier_resolver nie importuje core.* ani dispatch_pipeline.
from dispatch_v2.courier_resolver import is_position_known
from dispatch_v2.position_model import (
    PositionKind, origin_estimate_for, resolve_courier_position, shadow_position,
)
from dispatch_v2.observability import stage_timing as _ST


@dataclass
class EvalContext:
    """Wejścia oceny kandydata — dokładnie to, co closure czytała z _assess_order_impl.

    Budowany w impl PO `_k07_prefetched_ck` (closure czytała tę nazwę late-bound),
    tuż przed pulą — wartości są wtedy finalne i wspólne dla wszystkich kandydatów.
    """
    now: datetime
    order_event: dict
    order_id: str
    restaurant: Any
    delivery_address: Any
    pickup_coords: Any
    delivery_coords: Any
    pickup_at: Optional[datetime]
    pickup_ready_at: Optional[datetime]
    new_order: Any
    new_rest_norm: Any
    fleet_speed_kmh: Any
    fleet_context: Any
    k07_prefetched_ck: Optional[dict]
    loadgov_now: Any
    loadgov_ewma: Any
    loadgov_orders: Any
    loadgov_couriers: Any
    # Snapshot plan_version z poczatku calej decyzji (przed pula kandydatow).
    # None = snapshot niedostepny; panel-watcher wtedy bezpiecznie pomija zapis.
    plan_versions: Optional[Dict[str, Optional[int]]] = None
    # Z-P1-03: jawna propagacja do workera ThreadPool (ContextVar sam nie
    # przechodzi przez executor). Pole jest wyłącznie obserwacyjne.
    timing_trace: Any = None
    position_model_mode: str = "legacy"
    position_model_shadow: bool = False
    # Side-channel wyłącznie dla kontrfaktu shadow. Pozwala zachować wariant
    # explicit odrzucony przez legacy BEZ wkładania go do głównej puli.
    position_model_variants: Optional[Dict[str, Dict[str, Any]]] = None
    position_model_variants_lock: Any = None


def _origin_shadow(candidate, position, *, explicit: bool) -> dict:
    metrics = (getattr(candidate, "metrics", None) or {}) if candidate is not None else {}
    payload = {
        **shadow_position(position),
        "road_km": metrics.get("estimated_road_km", metrics.get("km_to_pickup")),
        "drive_min": metrics.get("estimated_drive_min", metrics.get("drive_min")),
        "feasibility": getattr(candidate, "feasibility_verdict", None),
        "score": getattr(candidate, "score", None),
    }
    if explicit:
        payload.update({
            "r1_origin_geometry_evaluable": metrics.get("r1_origin_geometry_evaluable"),
            "r5_origin_geometry_evaluable": metrics.get("r5_origin_geometry_evaluable"),
            "chain_eta": metrics.get("r07_chain_eta_min"),
            "r29_solo_score": metrics.get("r29_solo_score"),
        })
    return payload


def eval_courier(ctx: EvalContext, cid, cs):
    # BUG-D Faza 2b: opt-in TLS leg tracking dla per-route v2 aggregate.
    # Każdy thread w ThreadPoolExecutor ma własny TLS context — parallel safe.
    # Inner stop'nie tracking + aggregate przed return Candidate. Outer
    # try/finally jest safety net dla early return None paths (cleanup TLS
    # idempotent — stop_v2_request_tracking w obu miejscach OK).
    from dispatch_v2 import osrm_client as _osrm_client
    with _ST.candidate_scope(ctx.timing_trace, str(cid)):
        _osrm_client.start_v2_request_tracking()
        try:
            # OFF-parytet/perf: bez shadow wrapper jest dokładnie jednym wywołaniem
            # aktywnej polityki. Nie klasyfikuje pozycji i nie buduje telemetrii.
            if not ctx.position_model_shadow:
                return eval_courier_inner(ctx, cid, cs)

            position = resolve_courier_position(cs)
            if ctx.position_model_shadow and position.position_kind is PositionKind.UNKNOWN:
                legacy = eval_courier_inner(replace(ctx, position_model_mode="legacy"), cid, cs)
                explicit = eval_courier_inner(replace(ctx, position_model_mode="explicit"), cid, cs)
                variants = {"legacy": legacy, "explicit": explicit}
                if ctx.position_model_variants is not None:
                    if ctx.position_model_variants_lock is None:
                        ctx.position_model_variants[str(cid)] = variants
                    else:
                        with ctx.position_model_variants_lock:
                            ctx.position_model_variants[str(cid)] = variants
                primary = explicit if ctx.position_model_mode == "explicit" else legacy
                if primary is not None:
                    primary._position_model_variants = variants
                    primary.metrics["position_model_shadow"] = {
                        **shadow_position(position),
                        "legacy_origin": _origin_shadow(legacy, position, explicit=False),
                        "explicit_unknown_origin": _origin_shadow(explicit, position, explicit=True),
                    }
                return primary
            candidate = eval_courier_inner(ctx, cid, cs)
            variants = {"legacy": candidate, "explicit": candidate}
            if ctx.position_model_variants is not None:
                if ctx.position_model_variants_lock is None:
                    ctx.position_model_variants[str(cid)] = variants
                else:
                    with ctx.position_model_variants_lock:
                        ctx.position_model_variants[str(cid)] = variants
            if candidate is not None:
                candidate._position_model_variants = variants
                candidate.metrics["position_model_shadow"] = {
                    **shadow_position(position),
                    "legacy_origin": _origin_shadow(candidate, position, explicit=False),
                    "explicit_unknown_origin": _origin_shadow(candidate, position, explicit=True),
                }
            return candidate
        finally:
            # Idempotent cleanup — inner mogło już stop'nąć przed Candidate construction;
            # ten call wtedy zwraca None (TLS już wyczyszczony). Defense-in-depth dla raise.
            _osrm_client.stop_v2_request_tracking()


def eval_courier_inner(ctx: EvalContext, cid, cs):
    from dispatch_v2 import dispatch_pipeline as _dp
    # ── prolog K11: odpakowanie kontekstu do lokalnych nazw 1:1 z closure ──
    now = ctx.now
    order_event = ctx.order_event
    order_id = ctx.order_id
    restaurant = ctx.restaurant
    delivery_address = ctx.delivery_address
    pickup_coords = ctx.pickup_coords
    delivery_coords = ctx.delivery_coords
    pickup_at = ctx.pickup_at
    pickup_ready_at = ctx.pickup_ready_at
    new_order = ctx.new_order
    new_rest_norm = ctx.new_rest_norm
    fleet_speed_kmh = ctx.fleet_speed_kmh
    fleet_context = ctx.fleet_context
    _k07_prefetched_ck = ctx.k07_prefetched_ck
    loadgov_now = ctx.loadgov_now
    loadgov_ewma = ctx.loadgov_ewma
    loadgov_orders = ctx.loadgov_orders
    loadgov_couriers = ctx.loadgov_couriers
    _plan_versions = ctx.plan_versions
    _plan_expected_version = (
        _plan_versions.get(str(cid), 0) if _plan_versions is not None else None
    )
    # ── aliasy symboli module-level dispatch_pipeline (kontrakty monkeypatch + logi 1:1) ──
    Candidate = _dp.Candidate
    DWELL_PICKUP_MIN = _dp.DWELL_PICKUP_MIN
    HAVERSINE_ROAD_FACTOR_BIALYSTOK = _dp.HAVERSINE_ROAD_FACTOR_BIALYSTOK
    WARSAW = _dp.WARSAW
    haversine = _dp.haversine
    log = _dp.log
    check_feasibility_v2 = _dp.check_feasibility_v2
    get_fresh_czas_kuriera_for_bag = _dp.get_fresh_czas_kuriera_for_bag
    compute_bundle_deliv_coloc = _dp.compute_bundle_deliv_coloc
    _min_dist_to_route_km = _dp._min_dist_to_route_km
    _apply_pre_shift_equal_gate = _dp._apply_pre_shift_equal_gate
    _bag_dict_to_ordersim = _dp._bag_dict_to_ordersim
    _compute_r1_progressive_delta = _dp._compute_r1_progressive_delta
    _compute_repo_cost_km = _dp._compute_repo_cost_km
    _compute_sync_spread = _dp._compute_sync_spread
    _compute_v319h_guard_delta = _dp._compute_v319h_guard_delta
    _coords_pass = _dp._coords_pass
    _k07_apply_fresh_ck = _dp._k07_apply_fresh_ck
    _load_rule_weights = _dp._load_rule_weights
    _oldest_in_bag_min = _dp._oldest_in_bag_min
    _pre_shift_gradient_penalty = _dp._pre_shift_gradient_penalty
    _r1_corridor_base_bonus = _dp._r1_corridor_base_bonus
    _r6_soft_penalty = _dp._r6_soft_penalty
    _r_paczki_flex_penalty = _dp._r_paczki_flex_penalty
    _repo_cost_penalty = _dp._repo_cost_penalty
    _sanitize_courier_pos = _dp._sanitize_courier_pos
    _soon_free_probe = _dp._soon_free_probe
    _sync_spread_penalty = _dp._sync_spread_penalty
    # ── koniec prologu; poniżej ciało bajt-w-bajt z dawnej closure ──
    resolved_position = None
    explicit_unknown = False
    origin_travel = None
    if ctx.position_model_mode == "explicit":
        resolved_position = resolve_courier_position(cs)
        explicit_unknown = resolved_position.position_kind is PositionKind.UNKNOWN
        origin_travel = origin_estimate_for(resolved_position) if explicit_unknown else None
        courier_pos = resolved_position.coords
    else:
        # Legacy OFF-parity: nie uruchamiaj nawet resolvera nowego modelu.
        courier_pos = _sanitize_courier_pos(getattr(cs, "pos", None))
    if courier_pos is None and origin_travel is None:
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
    if bag_sim and _k07_prefetched_ck is not None:
        # K07 refaktor (2026-07-06): dane odświeżone RAZ przed pulą
        # (_k07_prefetch_fresh_ck) — tu wyłącznie CZYSTA aplikacja, zero
        # HTTP w ocenie kandydata; wszyscy kandydaci widzą TEN SAM stan.
        _k07_apply_fresh_ck(bag_sim, _k07_prefetched_ck)
    elif bag_sim and C.ENABLE_V327_PRE_PROPOSAL_RECHECK:
        try:
            with _ST.work_span("pre_recheck"):
                _fresh_ck_dict = get_fresh_czas_kuriera_for_bag(bag_sim, now)
                # Override OrderSim.czas_kuriera_warsaw dla orders gdzie fresh != cached.
                # Downstream scoring/TSP/feasibility używa updated values w tym candidate run.
                # (reguła nadpisania = wspólny helper _k07_apply_fresh_ck, kontrakt ①)
                _k07_apply_fresh_ck(bag_sim, _fresh_ck_dict)
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
    if not explicit_unknown and _coords_pass(
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
            courier_pos=(resolved_position.coords if explicit_unknown else getattr(cs, "pos", None)),
            pos_source=getattr(cs, "pos_source", None),
            pos_age_min=getattr(cs, "pos_age_min", None),
            bag_orders=bag_sim,
            proposal_pickup_coords=tuple(pickup_coords),
            proposal_scheduled_utc=pickup_ready_at,
            now_utc=now,
            osrm_drive_min=_drive_min_fn,
            haversine_km=_hav,
            speed_multiplier=_speed_mult,
            origin_travel=origin_travel,
            origin_available_from=getattr(cs, "available_from", None),
        )
        _r07_latency_ms = (_time.perf_counter() - _r07_t0) * 1000.0
        if r07_chain_result is not None:
            r07_chain_eta_utc = r07_chain_result.effective_eta_utc
    except Exception as _r07_e:
        log.warning(f"R-07 chain_eta compute fail: {type(_r07_e).__name__}: {_r07_e}")

    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(tuple(courier_pos) if courier_pos is not None else None),
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
        available_from=getattr(cs, "available_from", None),  # L4 — jedno źródło max(now,shift_start)
        courier_tier=getattr(cs, "tier_bag", None),  # 2026-05-17 tier-aware DWELL
        schedule_source_stale=getattr(cs, "schedule_source_stale", False),  # D2 (audyt 2026-05-28)
        pos_from_store=getattr(cs, "pos_from_store", False),  # Z-06 (audyt 2026-06-10) — store-rescue to nie świeży fix
        origin_travel=origin_travel,
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
    effective_start_pos = tuple(courier_pos) if courier_pos is not None else None
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
    # NOGPS-NEUTRAL-SCORE (2026-07-19): śledzimy, czy start-pos dla road_km
    # została nadpisana REALNĄ kotwicą (anchor / bag-tail delivery_coords) —
    # wtedy road_km NIE pochodzi z syntetycznej pozycji kuriera.
    _v326_bag_tail_used = False
    if not v326_anchor_used and bag_sim and plan is not None and plan.sequence:
        # Legacy fallback: chronological last drop in sequence
        _bag_by_oid = {o.order_id: o for o in bag_sim}
        _bag_in_seq = [oid for oid in plan.sequence if oid in _bag_by_oid]
        if _bag_in_seq:
            effective_start_pos = tuple(_bag_by_oid[_bag_in_seq[-1]].delivery_coords)
            _v326_bag_tail_used = True

    # F1.7 fix: travel_min = plan-based (uwzględnia bag + waiting na pickup_ready),
    # używane przez compute_assign_time. Display ETA jest osobne (drive_min).
    # Fix 2: km_to_pickup liczone od effective_start_pos (end-of-wave dla bag).
    # V3.28 #28 ext (2026-05-11): sanitize effective_start_pos — pochodne pozycje
    # z _anchor.location (linia 1564) lub bag tail delivery_coords (linia 1595)
    # mogą być (0,0) gdy bag zawiera order z P0.4 data quality issue (delivery_coords
    # missing) — courier_resolver loguje "courier X picked_up order Y bez delivery_coords".
    # Bez sanitize → haversine raise → V328_CP_SOLVER_FAIL_PER_COURIER spam (residual 9/7h
    # post #28 cz.1; cid=508/523 z bag=469087). Mirror _sanitize_courier_pos pattern.
    if origin_travel is not None:
        km_to_pickup_haversine = origin_travel.road_km
    else:
        effective_start_pos = _sanitize_courier_pos(effective_start_pos) or effective_start_pos
        km_to_pickup_haversine = haversine(effective_start_pos, pickup_coords) * HAVERSINE_ROAD_FACTOR_BIALYSTOK

    # NOGPS-NEUTRAL-SCORE (2026-07-19, bug ziomek-nogps-center-score-bug):
    # road_km policzony z SYNTETYCZNEJ pozycji kuriera (BIALYSTOK_CENTER fiction
    # z courier_resolver._synthetic_pos_fallback), a nie z realnej kotwicy?
    # True ⇔ pozycja RAW kuriera jest Unknown (F-3 is_position_known — jedno
    # źródło klasyfikacji) ORAZ start-pos NIE została nadpisana anchor/bag-tail.
    # Konsument: dispatch_pipeline._nogps_neutral_score_pass (post-loop) —
    # shadow zawsze, neutralizacja s_dystans/score za ENABLE_NO_GPS_NEUTRAL_SCORE_DIST.
    road_km_from_synthetic_pos = (
        not is_position_known(getattr(cs, "pos_source", None))
        and not (v326_anchor_used or _v326_bag_tail_used)
    )

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
        courier_pos=(tuple(courier_pos) if courier_pos is not None else None),
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
        if origin_travel is not None:
            drive_min = origin_travel.drive_min_soft
            _drive_km_from_courier = origin_travel.road_km
        else:
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
    if (C.ENABLE_V326_R07_CHAIN_ETA or explicit_unknown) and r07_chain_result is not None:
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

    # L5.1 ETA LOAD-AWARE (2026-07-05, K3): silnik systematycznie OPTYMISTYCZNY
    # na nodze ODBIORU (eta_truth_map 28.06-04.07 n=925: med −4.0, solo −6.0,
    # std −5.4). Bufor z tabeli kalibracji (tier×solo/bundle, generator =
    # tools/eta_load_aware_calibrate.py) dokładany do OBIETNICY odbioru.
    # SHADOW zawsze (metryki eta_la_* → auto-serializacja L1.1); DECYZJA tylko
    # za flagą ENABLE_ETA_LOAD_AWARE (default OFF) — przesuwa eta_pickup_utc/
    # travel_min (wait-penalty/extension/target_pickup). NIE dotyka
    # feasibility_v2 (HARD; GATE-STRICTER + Q2 = osobny pas za ACK). Znana
    # granica: no_gps/pre_shift nadpisywane post-loop polityką (~6202-6219)
    # — bufor ich nie dotyczy. Fail-soft: brak tabeli → 0.0 (zachowanie
    # bajt-identyczne z przed-L5). Try/except: hot-path (Lekcja #32 — loguj).
    eta_la_buffer_min = 0.0
    try:
        eta_la_buffer_min = eta_load_aware.pickup_buffer_min(
            getattr(cs, "tier_bag", None), len(bag_sim))
    except Exception as _la_e:
        log.warning(f"L5.1 eta_load_aware fail cid={cid}: "
                    f"{type(_la_e).__name__}: {_la_e}")
        eta_la_buffer_min = 0.0
    eta_pickup_load_aware_utc = (
        eta_pickup_utc + timedelta(minutes=eta_la_buffer_min)
        if eta_la_buffer_min > 0 else eta_pickup_utc)
    if eta_la_buffer_min > 0 and C.decision_flag("ENABLE_ETA_LOAD_AWARE"):
        eta_pickup_utc = eta_pickup_load_aware_utc
        travel_min = round(travel_min + eta_la_buffer_min, 1)
        eta_source = f"{eta_source}+load_aware"

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
        # Z-P0-04: optimistic-CAS token dla event-time save w panel_watcher.
        # Powstal PRZED pula kandydatow, nie tuz przed pozniejszym zapisem.
        "plan_expected_version": _plan_expected_version,
        "score": score_result,
        "km_to_pickup": (None if explicit_unknown else round(km_to_pickup_haversine, 2)),
        # NOGPS-NEUTRAL-SCORE (2026-07-19): road_km z pozycji-fikcji (centrum)?
        # Konsument: _nogps_neutral_score_pass (neutralizacja score) + display.
        "road_km_from_synthetic_pos": road_km_from_synthetic_pos,
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
        # L5.1 (2026-07-05): shadow load-aware — bufor [min] + skorygowana
        # obietnica ODBIORU. Zawsze liczone (replay czyta stary vs nowy per
        # decyzja); decyzję zmienia TYLKO ENABLE_ETA_LOAD_AWARE.
        "eta_la_buffer_min": round(eta_la_buffer_min, 1),
        "eta_pickup_load_aware_utc": eta_pickup_load_aware_utc.isoformat(),
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
        # L4 (2026-07-02, F1): available_from = max(now, shift_start) policzone RAZ
        # w courier_resolver (None + "unset" gdy flaga OFF). Post-loop #1 clamp czyta
        # to zamiast re-derywacji shift_start_min. Auto-serializuje do ledgera (L1.1).
        "available_from_utc": (getattr(cs, "available_from", None).isoformat()
                               if getattr(cs, "available_from", None) is not None else None),
        "af_source": getattr(cs, "available_from_source", "unset"),
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
    if explicit_unknown:
        enriched_metrics.update({
            "estimated_road_km": origin_travel.road_km,
            "estimated_drive_min": origin_travel.drive_min_soft,
            "position_kind": resolved_position.position_kind.value,
            "position_provenance": resolved_position.provenance.value,
            "position_display_text": "pozycja nieznana · dojazd szac. 15 min",
            "r29_solo_score": round(100.0 - origin_travel.road_km * 10.0, 2),
        })

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

    # L4 OFF = ledger bajt-w-bajt: klucze L4 wchodzą do metrics TYLKO gdy źródło
    # policzyło available_from (⟺ ENABLE_AVAILABLE_FROM_SINGLE_SOURCE ON w
    # dispatchable_fleet). OFF → cs.available_from None → usuń → serializer ich
    # nie widzi (jak sprzed L4). Post-loop #1 floor i tak nie odpali (flaga OFF).
    if getattr(cs, "available_from", None) is None:
        enriched_metrics.pop("available_from_utc", None)
        enriched_metrics.pop("af_source", None)

    # K13 (ADR-R06): interfejs Scorer za flagą ENABLE_SCORER_INTERFACE (ETAP4,
    # OFF default = zero odczytu, bajt-parytet 1:1). ON + SCORER_IMPL=heuristic
    # (default) = tożsamość score + metryki obserwacyjne scorer_impl/scorer_fallback
    # (auto-serializacja A+B). Wybór 'lgbm' (primary) = flip POZA zakresem programu,
    # wyłącznie jawna decyzja Adriana.
    if C.decision_flag("ENABLE_SCORER_INTERFACE"):
        try:
            from dispatch_v2.core import scorer as _scorer_mod
            _sv = _scorer_mod.get_scorer().score_candidate(
                final_score, candidate=None,
                decision_ctx={"order_id": order_id, "restaurant": restaurant})
            enriched_metrics["scorer_impl"] = _sv.source
            enriched_metrics["scorer_fallback"] = bool(_sv.fallback)
            final_score = _sv.score
        except Exception:
            pass  # fail-soft: scorer NIGDY nie psuje oceny kandydata

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
