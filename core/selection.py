"""core.selection — selekcja + tiering + best_effort + bramki werdyktu (K12, PRZENOSINY 1:1).

`select_and_emit` = dosłowna treść końcowego segmentu `_assess_order_impl`
(filtr feasible → kary selekcyjne → sort/tiering → bramki KOORD jakości i
operacyjne → best_effort/always-propose → konstrukcja PipelineResult; każda
ścieżka wyjścia zwraca PipelineResult, EMIT przechodzi przez wspólny lejek
`_classify_and_set_auto_route` → w nim re-assert L7.3 `_split_layer_emit_assert`
= INV-LAYER-HARD-BEFORE-SOFT na KAŻDYM EMIT, LIVE za ENABLE_SPLIT_LAYER_GUARD).

Mechanika przenosin jak K11 (dowód 1:1): 11 odczytów z impl → SelectionContext
(prolog odpakowuje do lokalnych nazw 1:1), symbole module-level dispatch_pipeline
związane aliasami `X = _dp.X` (kontrakty monkeypatch + wspólny logger), ciało
bajt-w-bajt, lazy import `_dp` w funkcji (bez cyklu).
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Dict, Optional

from dispatch_v2 import common as C
from dispatch_v2 import pln_objective


@dataclass
class SelectionContext:
    """Wejścia selekcji — dokładnie to, co segment czytał z lokali impl."""
    now: datetime
    order_event: dict
    order_id: str
    restaurant: Any
    delivery_address: Any
    pickup_coords: Any
    delivery_coords: Any
    pickup_ready_at: Optional[datetime]
    new_order: Any
    fleet_snapshot: Dict[str, Any]
    v328_fail_causes: Dict[str, str]


def select_and_emit(ctx: SelectionContext, candidates: list):
    """Selekcja i emisja werdyktu — treść 1:1 z _assess_order_impl (pre-K12 4165-5292)."""
    from dispatch_v2 import dispatch_pipeline as _dp
    # ── prolog K12: odpakowanie kontekstu do lokalnych nazw 1:1 ──
    now = ctx.now
    order_event = ctx.order_event
    order_id = ctx.order_id
    restaurant = ctx.restaurant
    delivery_address = ctx.delivery_address
    pickup_coords = ctx.pickup_coords
    delivery_coords = ctx.delivery_coords
    pickup_ready_at = ctx.pickup_ready_at
    new_order = ctx.new_order
    fleet_snapshot = ctx.fleet_snapshot
    _v328_fail_causes = ctx.v328_fail_causes
    # ── aliasy symboli module-level dispatch_pipeline (kontrakty monkeypatch + logi 1:1) ──
    Candidate = _dp.Candidate
    HAVERSINE_ROAD_FACTOR_BIALYSTOK = _dp.HAVERSINE_ROAD_FACTOR_BIALYSTOK
    PipelineResult = _dp.PipelineResult
    TOP_N_CANDIDATES = _dp.TOP_N_CANDIDATES
    _a2_reliability_soft_score = _dp._a2_reliability_soft_score
    _always_propose_on = _dp._always_propose_on
    _append_difficult_case_log = _dp._append_difficult_case_log
    _assert_feasibility_first = _dp._assert_feasibility_first
    _best_effort_fastest_pickup_key = _dp._best_effort_fastest_pickup_key
    _best_effort_objm_pick = _dp._best_effort_objm_pick
    _best_effort_objm_shadow = _dp._best_effort_objm_shadow
    _best_effort_sort_key = _dp._best_effort_sort_key
    _classify_and_set_auto_route = _dp._classify_and_set_auto_route
    _compute_loadaware_shadow = _dp._compute_loadaware_shadow
    _demote_blind_empty = _dp._demote_blind_empty
    _e2_ab_arm = _dp._e2_ab_arm
    _feas_carry_blind_shadow = _dp._feas_carry_blind_shadow
    _feas_carry_readmit_pick = _dp._feas_carry_readmit_pick
    _gate_score_excluding_ranking_deltas = _dp._gate_score_excluding_ranking_deltas
    _gps_age_discount = _dp._gps_age_discount
    _late_pickup_score_first_key = _dp._late_pickup_score_first_key
    _late_pickup_tier = _dp._late_pickup_tier
    _min_propose_score = _dp._min_propose_score
    _new_delivered_at_dt = _dp._new_delivered_at_dt
    _objm_lexr6_d2_pick = _dp._objm_lexr6_d2_pick
    _objm_lexr6_shadow = _dp._objm_lexr6_shadow
    _pln_pure_resort = _dp._pln_pure_resort
    _reserve_aware_tiebreak_eval = _dp._reserve_aware_tiebreak_eval
    _sanitize_courier_pos = _dp._sanitize_courier_pos
    _set_feasibility_verdict = _dp._set_feasibility_verdict
    _v325_new_courier_penalty = _dp._v325_new_courier_penalty
    _v326_build_rationale = _dp._v326_build_rationale
    _v326_fleet_load_balance = _dp._v326_fleet_load_balance
    _v326_multistop_trajectory = _dp._v326_multistop_trajectory
    _v326_speed_multiplier_adjust = _dp._v326_speed_multiplier_adjust
    check_feasibility_v2 = _dp.check_feasibility_v2
    haversine = _dp.haversine
    log = _dp.log
    # ── koniec prologu; poniżej ciało bajt-w-bajt z impl ──
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
                        # L7.3 (INV-LAYER-2): promocja werdyktu odbywa się w L7 (selekcja) —
                        # zapis POZA L5. Kanonizowany przez setter (layer=L7_selekcja): garda
                        # loguje naruszenie warstwy gdy ENABLE_SPLIT_LAYER_GUARD ON. Zapis sam
                        # NIEZMIENIONY (verdict→MAYBE dla spójności serializacji/inwariantu).
                        _set_feasibility_verdict(
                            _fcr_cand, "MAYBE", layer="L7_selekcja",
                            order_id=new_order.order_id)
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
                available_from=getattr(cs, "available_from", None),  # L4 — jedno źródło max(now,shift_start)
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
