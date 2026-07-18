"""Wspólne narzędzia: config loader, logger, paths."""
import json
import logging
import os
import threading
import time
import contextlib
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

SCRIPTS_DIR = Path("/root/.openclaw/workspace/scripts")
CONFIG_PATH = SCRIPTS_DIR / "config.json"
# ETAP 4 (2026-06-10): env override TYLKO dla izolacji testów script-runner
# (conftest ScriptRunItem podaje subprocesowi kopię bez flag decyzyjnych).
# W produkcji env nieustawiony → ścieżka kanoniczna.
FLAGS_PATH = Path(os.environ.get("DISPATCH_FLAGS_PATH") or (SCRIPTS_DIR / "flags.json"))

_config_cache = None
_config_mtime = 0
_flags_cache = None
_flags_mtime = 0

# ─── FALA perf-lazy (2026-07-02, finding E audytu 2.0): flag-load fast path ───
# Problem: `flag()`/`decision_flag()` wołają `load_flags()` ~700×/decyzję, a KAŻDE
# wywołanie robiło `FLAGS_PATH.stat()` (syscall). Przy 10 kandydatach w
# ThreadPoolExecutor = ~740 stat/decyzję (zmierzone cProfile). Fast path (gdy
# ENABLE_PERF_LAZY_MEMBERS ON): re-stat NAJWYŻEJ co _PERF_FLAGS_STAT_TTL_S; między
# — zwraca cache bez stat. Bajt-parytet: w oknie TTL flagi są STAŁE (zmiana
# flags.json podchwycona ≤TTL, mieści się w tolerancji hot-reload jak dziś).
# OFF (default) = zachowanie 1:1 sprzed fali (stat co wywołanie). Gate po
# EFEKTYWNEJ wartości procesu (odświeżanej przy reloadzie JSON — bez rekurencji).
_PERF_FLAGS_STAT_TTL_S = float(os.environ.get("PERF_FLAGS_STAT_TTL_S", "0.25"))
_flags_last_stat_mono = 0.0
_perf_lazy_members = False  # odświeżane w load_flags przy reloadzie JSON

# Stała-fallback (kanon = flags.json, hot-reload). NIE flaga decyzyjna (zmienia
# tylko KIEDY compute, nie TREŚĆ decyzji → poza ETAP4_DECISION_FLAGS). Env override
# wyłącznie dla wygody testów/harnessu; produkcja steruje przez flags.json.
ENABLE_PERF_LAZY_MEMBERS = os.environ.get("ENABLE_PERF_LAZY_MEMBERS", "0") == "1"

# Z-P1-03 (2026-07-10): niedecyzyjny kill-switch pelnego stage timing.
# Kanon po aktywacji = flags.json (hot-reload); brak klucza/default = OFF.
# OFF nie tworzy DecisionTrace, nie odpytuje dodatkowo depth kolejki i nie
# zapisuje sidecara. Nie nalezy do ETAP4_DECISION_FLAGS, bo nie zmienia wyniku.
ENABLE_STAGE_TIMING_OBSERVATION = False

# ─── K05 refaktor (2026-07-06, ADR-R01): FlagSnapshot per tick ───
# Problem: flagi czytane z dysku w TRAKCIE decyzji (nawet z perf-lazy TTL 0,25 s
# odświeżenie może wypaść W ŚRODKU ticku) → zmiana flags.json mid-tick daje
# NIESPÓJNĄ decyzję między kandydatami i nieodtwarzalny replay (K04 nagrywa
# snapshot, a silnik mógł liczyć na innym zestawie). Fix: pętla silnika
# (shadow_dispatcher.run) woła flags_snapshot_begin() na starcie ticku i
# flags_snapshot_end() w finally — w oknie ticku load_flags() zwraca ZAMROŻONY
# dict. Hot-reload działa MIĘDZY tickami (własność operacyjna zachowana).
# Inne procesy (panel-watcher/plan-recheck/czasówka) nigdy nie wołają begin()
# → ich zachowanie bez zmian. Gate = ENABLE_FLAG_SNAPSHOT (kanon flags.json,
# czytany ŻYWO w begin(); brak klucza/OFF = no-op = zachowanie 1:1). NIE flaga
# decyzyjna (zmienia ŚWIEŻOŚĆ odczytu, nie treść reguł) — wzorzec jak
# ENABLE_PERF_LAZY_MEMBERS wyżej.
ENABLE_FLAG_SNAPSHOT = False  # stała-fallback; kanon = flags.json
_FLAGS_SNAPSHOT_OVERRIDE = None  # aktywny snapshot ticku (None = ścieżka żywa)


def load_config():
    """Hot-reload config.json jesli sie zmienil."""
    global _config_cache, _config_mtime
    mtime = CONFIG_PATH.stat().st_mtime
    if _config_cache is None or mtime > _config_mtime:
        with open(CONFIG_PATH) as f:
            _config_cache = json.load(f)
        _config_mtime = mtime
    return _config_cache


def load_flags():
    """Hot-reload flags.json jesli sie zmienil.

    perf-lazy (ENABLE_PERF_LAZY_MEMBERS ON): pomija `stat()` w oknie TTL
    (_PERF_FLAGS_STAT_TTL_S) gdy cache ciepły — eliminuje ~740 stat/decyzję.
    Zwracany dict jest READ-ONLY dla callerów (`.get()`); zero mutacji = brak
    ryzyka parytetu. OFF = stat co wywołanie (zachowanie sprzed fali)."""
    global _flags_cache, _flags_mtime, _flags_last_stat_mono, _perf_lazy_members
    # K05 (ADR-R01): aktywny snapshot ticku wygrywa nad dyskiem — decyzja widzi
    # JEDEN spójny zestaw flag. Ustawiany wyłącznie przez flags_snapshot_begin()
    # w pętli silnika; wszędzie indziej None → ścieżka żywa jak dotąd.
    _snap = _FLAGS_SNAPSHOT_OVERRIDE
    if _snap is not None:
        return _snap
    if _perf_lazy_members and _flags_cache is not None:
        # Fast path: w oknie TTL nie stat'ujemy — flagi stałe w tak krótkim oknie.
        if (time.monotonic() - _flags_last_stat_mono) < _PERF_FLAGS_STAT_TTL_S:
            return _flags_cache
    mtime = FLAGS_PATH.stat().st_mtime
    _flags_last_stat_mono = time.monotonic()
    if _flags_cache is None or mtime > _flags_mtime:
        with open(FLAGS_PATH) as f:
            _flags_cache = json.load(f)
        _flags_mtime = mtime
        # Odśwież EFEKTYWNY stan perf-lazy z właśnie wczytanego JSON (bez rekurencji
        # przez flag()). Fallback do stałej modułu gdy klucza brak w flags.json.
        _perf_lazy_members = bool(
            _flags_cache.get("ENABLE_PERF_LAZY_MEMBERS",
                             globals().get("ENABLE_PERF_LAZY_MEMBERS", False)))
    return _flags_cache


def flag(name: str, default=False) -> bool:
    """Szybki odczyt flagi z hot-reload."""
    return load_flags().get(name, default)


def flags_snapshot_begin():
    """K05 (ADR-R01): zamroź flagi na czas TICKU silnika.

    Gate ENABLE_FLAG_SNAPSHOT czytany ŻYWO (przed zamrożeniem). OFF/brak klucza
    → None i ZERO zmiany zachowania. ON → od tego momentu load_flags() (a więc
    flag()/decision_flag() we WSZYSTKICH modułach i wątkach puli kandydatów
    tego procesu) zwraca zamrożony dict aż do flags_snapshot_end().
    Wołać wyłącznie z pętli ticku; end() ZAWSZE w finally."""
    global _FLAGS_SNAPSHOT_OVERRIDE
    _FLAGS_SNAPSHOT_OVERRIDE = None  # gate + treść czytane z żywej ścieżki
    try:
        live = load_flags()
        if not bool(live.get("ENABLE_FLAG_SNAPSHOT", ENABLE_FLAG_SNAPSHOT)):
            return None
        snap = dict(live)
    except Exception:
        return None  # fail-soft: brak snapshotu = zachowanie dotychczasowe
    _FLAGS_SNAPSHOT_OVERRIDE = snap
    return snap


def flags_snapshot_end() -> None:
    """K05: zdejmij snapshot ticku (hot-reload wraca). Idempotentne."""
    global _FLAGS_SNAPSHOT_OVERRIDE
    _FLAGS_SNAPSHOT_OVERRIDE = None


# ─── ETAP 4 (2026-06-10, audyt Z-04): flagi DECYZYJNE wspólne cross-proces ───
# Problem: te flagi były module-level env (wartość zamrożona przy imporcie),
# a env różnił się per unit systemd → dispatch-czasowka (assess_order) i
# dispatch-plan-recheck (simulate_bag_route_v2) liczyły INNYM silnikiem niż
# dispatch-shadow (override.conf ~15 flag). Kanon wartości = flags.json
# (hot-reload, wspólny dla wszystkich procesów); stała modułu (env-default)
# zostaje WYŁĄCZNIE jako fallback gdy klucza brak w flags.json.
# Inwentaryzacja + ACK Adriana: eod_drafts/2026-06-10/flag_inventory_etap4.md.
# UWAGA testy: conftest._isolate_flags_json wycina te klucze z tmp-kopii
# flags.json, żeby testy dalej sterowały zachowaniem przez patch stałej modułu.
ETAP4_DECISION_FLAGS = (
    "ENABLE_BUNDLE_DELIV_SPREAD_CAP",
    "ENABLE_R1_PROGRESSIVE_CLIP",
    "ENABLE_V319H_CONTINUATION_GUARD",
    "ENABLE_A2_RELIABILITY_SOFT_SCORE",
    "ENABLE_FAIL12_SCHEDULE_FAILOPEN",
    "ENABLE_F4_COURIER_POS_PICKUP_PROXY",
    "ENABLE_F4_COURIER_POS_INTERP",
    "ENABLE_CHECKPOINT_TS_WARSAW_PARSE",
    "ENABLE_C2_NEG_GAP_DECAY",
    "ENABLE_PRE_SHIFT_DEPARTURE_CLAMP",
    "ENABLE_PRE_SHIFT_EQUAL_NO_PENALTY",
    "ENABLE_OBJ_SPAN_COST",
    "ENABLE_OBJ_R6_SOFT_DEADLINE",
    "ENABLE_OBJ_F3_BEST_EFFORT_R6_KOORD",
    "ENABLE_OBJ_PICKUP_FRESHNESS",
    "ENABLE_OBJ_DELIVERY_FOOD_AGE",
    "ENABLE_OBJ_DELIVERY_FOOD_AGE_SHADOW",
    "ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE",
    "ENABLE_DIFFICULT_CASE_KOORD_REDIRECT",
    # SP-B2-SYNCWORKA + PREPBIAS-konsumpcja (2026-06-11): flagi decyzyjne
    # cross-proces (shadow/czasowka/plan-recheck liczą tym samym silnikiem).
    "ENABLE_BUNDLE_SYNC_SPREAD",
    "ENABLE_PREP_BIAS_TABLE",
    # SP-B2-REPO (2026-06-11): aplikacja kary repozycjonowania do score
    # (telemetria zawsze; LIVE flip = 🛑 ACK).
    "ENABLE_REPO_COST_LIVE",
    # SP-B2-ZARAZWOLNY (2026-06-11): substytucja soon_free (🛑 ACK).
    "ENABLE_SOON_FREE_CANDIDATE",
    # Equal-treatment dokończony (2026-06-24): no_gps+pre_shift po score w bucketach
    # selekcji + demote (tiering/best_effort/LEXR6). Decyzyjna, cross-proces.
    "ENABLE_EQUAL_TREATMENT_BUCKET",
    # SP-B2-LOADGOV (2026-06-11): governor load floty (🛑 ACK).
    "ENABLE_FLEET_LOAD_GOVERNOR",
    # E7-doklejka 3 (2026-06-11): BUG A/B geometry/fairness z env-only do
    # kanonu flags.json PRZED flipem (werdykty eod_drafts/2026-06-11/
    # VERDICT_bug_a_b.md; sekwencja: B 4.0/km → ≥7 dni → A max+FIFO bez SUM).
    "ENABLE_BAG_TIME_FAIRNESS_SCORING",
    "ENABLE_R5_PICKUP_DETOUR_PENALTY",
    # GPS-03/DATA-04 (2026-06-11): dyskonto pewności za wiek pozycji (shadow-first).
    "ENABLE_GPS_AGE_DISCOUNT",
    # FRONT-B (2026-06-11): pickup_coords na żywo z adresu panelu (shadow-first).
    "ENABLE_PICKUP_COORDS_FROM_PANEL",
    # BUNDLE-06 Faza 1 + BUNDLE-03 (Front D, 2026-06-12): wartość worka +
    # addytywna kara FIX_C (shadow-first, delty zawsze, flip po E7 za ACK).
    "ENABLE_BUNDLE_VALUE_SCORING",
    "ENABLE_FIX_C_ADDITIVE_PENALTY",
    # AUTON-01 (Front E, 2026-06-13): egzekutor auto-assign. Telemetria
    # would_auto_assign liczona ZAWSZE (lekcja #186); flaga gate'uje WYŁĄCZNIE
    # wykonanie w auto_assign_executor. Flip po E7 za ACK + osobne E2E.
    # Projekt: eod_drafts/2026-06-13/AUTON01_DESIGN.md.
    "ENABLE_AUTO_ASSIGN",
    # AUTON-02 (2026-06-30): profil bramki „plaster D". Default strict (oba True)
    # = zachowanie AUTON-01. False zdejmuje wymóg klasyfikator=AUTO (G2) / margin
    # (G12) → szerszy plaster bramkowany fizyką (pool_feasible≥2, non-scarcity).
    # Twarde bramki (PROPOSE/czasówka/paczka/informed-pos/late-pickup/R6/shift-end/
    # parser-degraded) ZAWSZE. Projekt: eod_drafts/2026-06-30/AUTON02_PLASTER_D_DESIGN.md.
    "AUTO_ASSIGN_REQUIRE_CLASSIFIER_AUTO",
    "AUTO_ASSIGN_REQUIRE_MARGIN",
    # D6a SHADOW (2026-07-18, OWNER_CONFIRMED D1-D7 — memory owner-decision-eta-calib):
    # serving obietnic kalibratora per-kurier NA ZWYCIĘZCY — wyłącznie nowe metryki
    # eta_calib_promise_* w best.metrics (auto-serializacja L1.1) do parytetu w cieniu
    # 2 dni. Zero wpływu na feasibility/scoring/wyświetlane czasy (warstwa APPLY =
    # osobna flaga przy flipie za końcowym ACK). Konsument: eta_calib_serving przez
    # lejek _classify_and_set_auto_route.
    "ENABLE_ETA_CALIB_PROMISE_SHADOW",
    # R6-ETA-GATE (2026-06-14): kalibracja p80 R6 dla gold worek<=4 (LIVE) +
    # shadow-log falszywych odrzutow. Test-izolacja: conftest scina z tmp flags.json,
    # zeby gate gold->4 NIE przeciekal do testow R6/SLA (determinizm suity).
    # Koordynacja: HANDOFF_eta_calib_bag3_gate.md (sesja 126 = wariant bag<=3 na tej
    # samej fladze; gold->4 juz wpiete commit 0073486 — union, nie konflikt).
    "ENABLE_ETA_QUANTILE_R6_BAGCAP",
    # W0.2 advisory (2026-07-06): bezpiecznik fabrykacji ETA (hybryda 60′∧2,5×robust_ref).
    "ENABLE_ETA_FABRICATION_GUARD",
    # W0.5 advisory (2026-07-06): korekta ETA per-komórka floty (slot×solo/worek) na obietnicę.
    "ENABLE_ETA_CELL_RESIDUAL_CORRECTION",
    # W1/T2.4 advisory (2026-07-07): stempel would-be-mode (S1/S2/S3) na rekordzie decyzji (shadow).
    "ENABLE_MODE_LAYER_SHADOW",
    "ENABLE_R6_BREACH_SHADOW_LOG",
    # E2 (2026-06-14): 20% live A/B PLN-sort selekcji kandydatow (dispatch_pipeline).
    "ENABLE_E2_PLN_AB",
    # PLN-PAY (2026-06-14): term realnej placy kuriera per-osoba w pln_v (mirror
    # panel finance.courier_cost_components). Telemetria pln_v_payaware ZAWSZE;
    # aplikacja do pln_v (i tym samym arm PLN E2) za flaga. courier_pay.json z syncu.
    "ENABLE_PLN_COURIER_PAY",
    # #9 conftest-leak fix (audyt 28.06): 3 flagi decyzyjne flags.json=True ale POZA ETAP4 →
    # conftest nie strippował → C.flag() w testach zwracał prod-True (regresja cicho biegła ON
    # myśląc że OFF). Rejestracja = conftest strip + widoczne w flag_fingerprint. Fallback OFF:
    # ALWAYS_PROPOSE_ON_SATURATION/R_PACZKI_FLEX (env-default), PLN_QUALITY_AWARE (callsite literal False).
    "ENABLE_ALWAYS_PROPOSE_ON_SATURATION",
    "ENABLE_R_PACZKI_FLEX",
    "ENABLE_PLN_QUALITY_AWARE",
    # FOOD-AGE HARD-SLA (2026-06-17 Faza 2): twardy span pickup→delivery≤sla w
    # solverze + warm-start sekwencją bazową + fallback OFF (gwarancja ON≤OFF).
    # Komponuje się z ENABLE_OBJ_DELIVERY_FOOD_AGE. Faza 0 root-cause:
    # eod_drafts/2026-06-17/PHASE0_ROOTCAUSE_VERDICT.md (62% regresji=budżet 200ms,
    # 38% strukturalne). Design: PHASE1_DESIGN_LOCK.md.
    "ENABLE_OBJ_FOOD_AGE_HARD_SLA",
    # END-OF-DAY SALVAGE (2026-06-18): w ostatniej godzinie pracy firmy (23:00, pt/sb
    # 24:00) zluzuj twarde reguły końca zmiany dla (zwykle jedynego) kuriera — twardy
    # warunek: ODBIÓR ≤ koniec pracy firmy, dostawa może wyjść po jego shift_end.
    # Default OFF; flip po replay-walidacji + ACK. feasibility_v2._end_of_day_salvage.
    "ENABLE_END_OF_DAY_SALVAGE",
    # POST-SHIFT OVERRUN PENALTY (Adrian 2026-06-24): rosnąca kara za minuty dowozu
    # po końcu zmiany — wiodący term selekcji best_effort + score. Decyzyjna,
    # cross-proces (shadow/czasowka/plan-recheck liczą tym samym silnikiem).
    # Default OFF; flip po replay + ACK poza peakiem. common.post_shift_overrun_penalty.
    "ENABLE_POST_SHIFT_OVERRUN_PENALTY",
    # ETAP4-GAP DOMKNIĘTY (2026-06-25): live-decyzyjna flaga selekcji best_effort
    # (carry-aware objm pick) była POZA rejestrem → poza zasięgiem flag_registry/
    # parytetu cross-proces/izolacji conftest (testy dziedziczyły żywy flags.json=ON).
    # Stała-fallback OFF istnieje (common.py ~2504). Produkcja czyta flags.json (ON);
    # testy teraz izolowane (strip→OFF, chyba że test jawnie ustawi). Decyzyjna,
    # cross-proces (shadow/plan-recheck liczą tym samym silnikiem).
    "ENABLE_BEST_EFFORT_OBJM_R6_KEY",
    # BUNDLE-DELIVERY-COLOCATION (Adrian 2026-06-26, case Dariusz Maruszak 509
    # Street Mama Thai + Raj): kredyt bundla gdy nowa dostawa skolokowana z dostawą
    # w bagu (różne restauracje, ten sam adres), a obie TWARDE reguły spełnione
    # (R6 ≤35 czyste + committed honorowane). Zamyka pickup-centryczną ślepotę L1/L2.
    "ENABLE_BUNDLE_DELIVERY_COLOCATION",
    # geocode-centroid guard (audyt 28.06): wyklucz fałszywy 0km coloc na defaultowym centroidzie
    # (122 adresów cache→BIALYSTOK_CENTER, Google zwraca centrum dla dwuznacznych adresów).
    "ENABLE_BUNDLE_COLOC_CENTROID_GUARD",
    # FEAS-CARRY-READMIT / #483000 (Adrian 2026-06-27, at-167 sygnał TRZYMA 55.5%):
    # bramka check_feasibility_v2 wybacza najgorszy breach NIESIONEMU (SLA_PREEXISTING_
    # BYPASS), a HARD-rejectuje blocking SLA/R6 — pula feasible bywa GORSZY ocalały,
    # lepszy carry-inclusive wycięty. Ta flaga re-dopuszcza odrzuconego (NO, blocking
    # sla/r6) na warstwie SELEKCJI gdy carry-inclusive lex_qual lepszy od zwycięzcy ORAZ
    # nowy order ≤ BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN (40 = Tier-3 cap-stretch, ten sam
    # co best_effort). Werdykt HARD bramki nietknięty (downstream MIN_PROPOSE/commit-
    # divergence dalej gate'ują). Mirror _best_effort_objm_pick na feasible-path. Default
    # OFF; flip po replay ON↔OFF za ACK. Decyzyjna, cross-proces.
    "ENABLE_FEAS_CARRY_READMIT",
    # O2 RE-SEQ (2026-06-27, review 02.07): master-flaga TRÓJKI — ready-anchor w
    # _count_sla_violations + objektyw O2 (overage + λ·czas_late) w sweep/select +
    # 2. anchor feasibility:1135. Czyni objektyw worka uczciwym o świeżości niesionego
    # (dziś pickup-anchor ślepy → nagradza opóźnianie odbioru, 14,2% worków fałszywie
    # czystych). Default OFF; flip po GO review 02.07 + dowód netto + cap-Z z danych.
    # Decyzyjna, cross-proces. Składa się z B2 (carry-readmit selekcji) ku correctness.
    "ENABLE_O2_READY_ANCHOR_SWEEP",
    # SLA-ANCHOR-UNIFIED (S1, 2026-07-02, guard-teatr §4 / finding feas-r6-sla-anchor-gap):
    # konsolidacja 35-min HARD (R6 ready-anchor ↔ SLA now-anchor) do JEDNEGO źródła
    # (sla_anchor.py) z JAWNĄ kotwicą, w 3 bliźniakach RAZEM (_count_sla_violations +
    # feasibility SLA-loop + R6 per-order; plan_recheck dziedziczy przez plan.sla_violations).
    # OFF (default) = inline bez zmian, BAJT-W-BAJT (werdykt+reason+sla_violations identyczne);
    # ON = te same DECYZJE + metryka obs `sla_anchor_source` (de-maskowanie: naruszenie
    # każdej kotwicy niezależnie WIDOCZNE). Decyzyjna, cross-proces (route_simulator biegnie
    # pod shadow/plan-recheck/czasowka). Flip = osobny ACK. NIE zmienia semantyki O2/at-202/203.
    "ENABLE_SLA_ANCHOR_UNIFIED",
    # O2 cap-Z RESEQ (2026-07-02, sprint O2 cap-Z, review 02.07 GO): WĄSKA reguła Opcji 3
    # Adriana OBOK surowego ENABLE_O2_READY_ANCHOR_SWEEP (nietknięta). Silnik preferuje
    # przeplot zmniejszający overage świeżości (Σ max(0,age_ready−35)) TYLKO gdy JEDNOCZEŚNIE:
    # detour≤O2_CAPZ_DETOUR_MAX_MIN ∧ max wiek NIESIONEJ jedzeniówki≤O2_CAPZ_Z_MIN(=20) ∧
    # overage niższy o ≥O2_CAPZ_MIN_GAIN_MIN (argmin) ∧ sla_violations nie większe. Brak
    # kandydata → kolejność BEZ ZMIAN. Trójka RAZEM: route_simulator_v2 (źródło, ogon
    # simulate_bag_route_v2 = greedy+ortools) + feasibility_v2 (metryka obs) + plan_recheck
    # (dziedziczy przez _sweep). Paczki wyłączone (ENABLE_PACZKA_R6_THERMAL_EXEMPT). Default
    # OFF; OFF=bajt-w-bajt; flip=osobny ACK po replay. Decyzyjna, cross-proces.
    "ENABLE_O2_CAPZ_RESEQ",
    # SLA-GATE READY-ANCHOR (2026-07-02, finding feas-r6-sla-anchor-gap): bramka 35-min SLA
    # (dostawy, `_count_sla_violations` + feasibility SLA-loop) przestawiona z kotwicy NOW
    # (pickup_at) na READY (od gotowości jedzenia) — WYŁĄCZNIE przez źródło sla_anchor.py
    # (kind='ready'), działa tylko gdy ENABLE_SLA_ANCHOR_UNIFIED ON (ścieżka unified). REALNA
    # zmiana decyzji (inne violations/reason) → replay ON↔OFF + ACK. Co-design z QUANTILE_R6_
    # BAGCAP + PACZKA_R6_THERMAL_EXEMPT. Default OFF; OFF=NOW-anchor (bez zmian). Cross-proces.
    "ENABLE_SLA_GATE_READY_ANCHOR",
    # #3 top10 (2026-06-29): reserve-aware tie-break SHADOW (wolny-vs-jadący) — log-only,
    # zero zmiany decyzji; obserwuje ile razy tie-break by dołożył do jadącego (oszczędność
    # rezerwy) w tym samym tierze late-pickup. Flip AKTYWNY = osobna flaga + ACK po walidacji #1.
    "ENABLE_RESERVE_AWARE_TIEBREAK_SHADOW",
    # F3-HARD-RULE FLAGS (2026-06-28, audyt Ziomka — domknięcie ETAP4-gap): flagi
    # rządzące TWARDYMI regułami feasibility/selekcji, które były POZA rejestrem →
    # poza zasięgiem fingerprint-parytetu cross-proces / izolacji conftest / flag_registry.
    # Wszystkie LIVE w flags.json=True; rejestracja runtime-NEUTRALNA (decision_flag czyta
    # flags.json PRZED stałą). Każda z testem efektu ON≠OFF. Stałe-fallback: HARD_TIER_BAG_CAP
    # l.~1237, PACZKA_R6 l.~3330, RETURN_VETO l.~3349, NO_GPS_EQUAL l.~1017, OBJM_LEXR6
    # l.~2526; PLAN_RECHECK_TIER_DWELL dodana w bloku fallback niżej (brakowała).
    "ENABLE_HARD_TIER_BAG_CAP",
    "ENABLE_PACZKA_R6_THERMAL_EXEMPT",
    "ENABLE_R_RETURN_TO_RESTAURANT_VETO",
    "ENABLE_PLAN_RECHECK_TIER_DWELL",
    "ENABLE_NO_GPS_EQUAL_TREATMENT",
    "ENABLE_OBJM_LEXR6_SELECT",
    # CZASÓWKA-W-UWAGACH SHADOW (2026-06-28, sesja 20, zlec. 484034 Sikorskiego):
    # parsuje deklarowany deadline DOSTAWY z free-text `uwagi` ("Czasówka na 17:10")
    # → nowe pole `delivery_deadline_uwagi` (panel_client → state_machine persist).
    # ADDITYWNE, observability-only: ŻADEN konsument decyzyjny go jeszcze nie czyta
    # (wpięcie w 3 bliźniaki SLA + serializer = osobny etap za ACK po dowodzie z oracle).
    # OFF → pole nie powstaje (bajt-identyczny ingest). W rejestrze: izolacja conftest
    # (test mający OFF nie dziedziczy żywego flags.json) + parytet fingerprint cross-proces.
    "ENABLE_CZASOWKA_UWAGI_DEADLINE_SHADOW",
    # CARRIED-FIRST RELAX READY-ANCHOR (2026-06-29, case Rećki cid 492): 3 bramki
    # carried-first w plan_recheck (_relax_carried_first / _reorder_noncarried_min_drive /
    # _lex_committed_window_reorder) liczyły R6 czas-w-torbie PŁASKO od TSP-pickup → carried-first
    # oszukiwał R6 odraczając odbiór (mały in-bag, 0 breachy) i odrzucał poprawny pickup-first.
    # ON → kotwica od GOTOWOŚCI (czas_kuriera, spójnie z r6_thermal_anchor) → odroczenie nie chowa
    # wieku termicznego → pickup-first przechodzi gdy skraca jazdę. Default OFF; flip po replay ON↔OFF
    # + ACK. Decyzyjna, cross-proces. Komplementarna z ENABLE_O2_READY_ANCHOR_SWEEP (tamta=SLA/objektyw).
    "ENABLE_CARRIED_FIRST_RELAX_READY_ANCHOR",
    # DELIVERED RESURRECTION (2026-06-29, case Pizzeria 105 cid 492): zlecenie błędnie oznaczone
    # 'doręczone' (apka skok 6→7), koordynator cofnął status w gastro → bez tego Ziomek ignorował
    # je na zawsze (terminal + _ignored_ids), lista/czasy się nie aktualizowały. ON → panel_watcher
    # wykrywa delivered-które-wróciło-do-packs + potwierdza aktywny status gastro (3-6) → wskrzesza
    # (status back, delivered_at=None). Wąskie: świeże <60min + budżet 5 fetchów/cykl. Default OFF.
    "ENABLE_DELIVERED_RESURRECTION",
    # L2.1 sentinel-ingest (2026-07-01, Faza 3 audytu, most K5): JEDEN walidator
    # coords_in_bialystok_bbox u KAŻDEGO ingest ((0,0)/NaN/poza-bbox NIE wchodzi
    # jako „dana"): gps_server POST, state_machine.upsert_order, shadow tick
    # geocode-or-skip, _save_plan_on_assign realne coords, guardy konsumentów
    # geometrii (soon_free/wave-veto/repo-cost/bundle) + feasibility._valid.
    # OFF = zachowanie legacy (truthy-guardy, placeholdery (0,0), V328-eject).
    # Projekt: eod_drafts/2026-06-30/backing/B15/B16 + FAZA1 L2.1.
    "ENABLE_COORD_SENTINEL_INGEST_GUARD",
    # === L0.1 conftest-leak closure (2026-07-01, Faza 3 audytu, oracle C19) ===
    # 14 flag „truly-decision" żyło w flags.json=True POZA rejestrem → testy
    # biegły cicho prod-ON (conftest wycina TYLKO członków ETAP4), a fingerprint
    # ich nie widział. Dopisanie TUTAJ auto-domyka przeciek (conftest iteruje
    # krotkę dynamicznie) i wciąga flagi do flag_fingerprint (64→78). Stałe-
    # fallback = intencja STEADY-STATE (json=True), nie ślepe OFF — const o
    # przeciwnej intencji niż json to mina klasy COMMIT_DIVERGENCE (utrata
    # klucza json = cichy flip zachowania). Blok stałych: „L0.1 fallbacki" niżej.
    "ENABLE_R6_SOFT_PEN_CAP",
    "ENABLE_OBJ_COMMITTED_PICKUP_PENALTY",
    "ENABLE_EXCLUDE_BY_CID",
    "ENABLE_INACTIVE_COURIER_GUARD",
    "ENABLE_ZOMBIE_PICKUP_AT_GUARD",
    "ENABLE_GPS_BBOX_GUARD",
    "ENABLE_V3273_WAIT_REJECT_PICKED_UP_ONLY",
    "ENABLE_R1_WAVE_SCOPED_DIRECTIONALITY",
    "ENABLE_NEW_COURIER_RAMP",
    "ENABLE_PLN_RESORT_WITHIN_TIER",
    "ENABLE_BEST_EFFORT_POS_SOURCE_KEY",
    "ENABLE_COURIER_LAST_KNOWN_POS",
    "ENABLE_LOAD_PLAN_PURE_READ",
    "ENABLE_PANEL_PACKS_BAG_RECONSTRUCTION",
    # L2.2 (2026-07-02): zbiorczy operator-alert na data-poison z klasyfikacji
    # catch-alla _v328_eval_safe (klasyfikacja/telemetria unconditional; flaga
    # gate'uje TYLKO wysyłkę alertu). Default OFF, flip za ACK.
    "ENABLE_V328_POISON_ALERT",
    # L4 (2026-07-02, Faza 3 audytu, F1/INV-SRC-AVAILABLE-FROM): jedno źródło
    # dostępności kuriera available_from=max(now,shift_start) w courier_resolver,
    # dziedziczone przez konsumentów (#1 candidate-eta, #3 plan departure-clamp,
    # #5 plan_recheck floor, chokepoint effective_pickup_at). OFF = stare ścieżki
    # bajt-w-bajt (własna re-derywacja shift_start per powierzchnia). Flip za ACK+replay.
    "ENABLE_AVAILABLE_FROM_SINGLE_SOURCE",
    # L3 (2026-07-02, Faza 3 audytu, F2/K2): plan_recheck przestaje cofać.
    # GATES = bramka ZAPISU regenu (compare-and-keep R6): świeży regen łamiący
    # R6 (carried>35) którego istniejący plan NIE łamie → NIE nadpisuj (keep
    # existing). GC = garbage-collect courier_plans (terminal-stop prune +
    # zombie by age/no-active) przez istniejące plan_manager API pod lockiem.
    # OBA OFF = bajt-w-bajt (zapis regenu i brak GC jak dziś). Flip za ACK+dry-run.
    "ENABLE_PLAN_RECHECK_GATES",
    "ENABLE_COURIER_PLANS_GC",
    # L5.1 (2026-07-05): ETA load-aware — bufor optymizmu nogi ODBIORU (K3).
    # OFF = shadow-only (metryki eta_la_* liczone zawsze, decyzja nietknięta).
    # ON = bufor przesuwa eta_pickup_utc/travel_min (oś obietnicy). Flip za ACK
    # po replay-dowodzie (eod_drafts/2026-07-05, Sprint 1 Z3).
    "ENABLE_ETA_LOAD_AWARE",
    # === D.3 fala A (2026-07-02): migracja 15 flag route/kanon z env-frozen
    # (plan_recheck.py module-consty) do flags.json = KANON. Były LIVE ON przez
    # drop-iny systemd (Environment=…=1) — martwe po tej migracji (decision_flag
    # NIE czyta env). Stałe-fallback common.py=True (blok „D.3 fala A fallbacki"
    # niżej) = intencja STEADY-STATE: utrata klucza json NIE flipuje po cichu
    # (mina COMMIT_DIVERGENCE). Rejestracja = conftest strip + flag_fingerprint +
    # parytet cross-proces (plan-recheck/panel-watcher/carried-first-guard). Dowód
    # reachability: pw woła tylko recanon_courier/redecide_courier; SEQUENCE_LOCK
    # (jedyny read w _gap_fill_plans←run_recheck) NIEOSIĄGALNY z pw → migracja
    # behawioralnie neutralna. Projekt: eod_drafts/2026-07-02/D3_RECON_migracja_env_frozen_flags.md.
    "ENABLE_GPS_FREE_ANCHOR",
    "ENABLE_GPS_FREE_ANCHOR_LAST_POS",
    "ENABLE_PLAN_REAL_PICKED_UP_AT",
    "ENABLE_PLAN_SEQUENCE_LOCK",
    "ENABLE_PLAN_CANON_ORDER_INVARIANTS",
    "ENABLE_NO_RETURN_TO_DEPARTED_PICKUP",
    "ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE",
    "ENABLE_IMMEDIATE_REDECIDE_ON_PICKUP",
    # MIGRACJA B2 (2026-07-18, audyt parytetu): committed tie-break w
    # _gen_one_bag_plan (plan_recheck :839) — było env-frozen z drop-inem tylko
    # w plan-recheck → pw (redecide_courier) liczył OFF, tick ON = kolejność
    # odbiorów MRUGAŁA (KANON §9 B2). Teraz KANON=flags.json, hot-reload w pw
    # przez _refresh_d3_fala_a_flags.
    "ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION",
    "ENABLE_RECANON_ON_WRITE",
    "ENABLE_CARRIED_FIRST_RELAX",
    "ENABLE_CARRIED_AGE_TZ_FIX",
    "ENABLE_LEX_COMMITTED_WINDOW_SHADOW",
    "ENABLE_LEX_COMMITTED_WINDOW",
    "ENABLE_RELAX_COLOC_PICKUP",
    "ENABLE_NONCARRIED_DROPOFF_REORDER",
    # === D.3 fala B (2026-07-02): para atomowa V326 (common.py, oba env-default
    # "1" jednolicie ON, żaden drop-in nie nadpisuje → migracja neutralna).
    # Konsument route_simulator_v2:299/438 czyta atrybut modułu (NIE zmieniany).
    # Sprzężenie #13: GROUPING=ON przy OR_TOOLS=OFF = double-insert super-pickupa →
    # check_v326_pair_coherence loguje ostrzeżenie (bez zmiany zachowania).
    "ENABLE_V326_OR_TOOLS_TSP",
    "ENABLE_V326_SAME_RESTAURANT_GROUPING",
    # === L6.C (2026-07-04): geometria w selekcji + claim ledger — REALNA zmiana
    # decyzji (tie-break lex_qual / obraz floty między eventami ticku). Default OFF;
    # flip za ACK po replayu ON↔OFF. Stałe-fallback: LEXQUAL ~2910, CLAIM w claim_ledger.
    "ENABLE_LEXQUAL_GEOMETRY_TIEBREAK",
    "ENABLE_ENGINE_CLAIM_LEDGER",
    # === Sprint B inwarianty (2026-07-08, INV-FEAS-NO-DOUBLE-BOOK): tripwier
    # spójności claim-ledger. _CHECK = log-loud obserwacja (NIE zmienia decyzji,
    # strażnik nie reguła); _HARD = twarda blokada (raise) — odłożona za ACK po
    # dowodzie ZERO fałszywek. Oba default OFF; NIE w flags.json (module-const only).
    "ENABLE_CLAIM_LEDGER_INVARIANT_CHECK",
    "ENABLE_CLAIM_LEDGER_INVARIANT_HARD",
    # === P-FLAGREG partia A' (2026-07-05, Sprint 2.5-prep tmux18): flagi
    # decyzyjne czytane w silniku BEZ klucza w flags.json → strip conftest =
    # no-op (klucza nie ma), baseline survivors ratchetu NIETKNIĘTY, produkcja
    # bez zmian (decision_flag: flags.json→stała). Zysk: fingerprint je widzi.
    # Czytelnicy `flag(name, default)` ignorują stałą modułu — dla 3 flag bez
    # stałej dodano konsty-lustra defaultów czytelników (sekcja ~l.4xx), żeby
    # fingerprint mówił prawdę. Inwentarz: eod_drafts/2026-07-05/FLAGREG_*.md.
    "ENABLE_FIRMOWE_REJECT_ON_GEOCODE_FAIL",
    "ENABLE_GEOCODE_NEGATIVE_CACHE",
    "ENABLE_PRE_SHIFT_GRADIENT_PENALTY",
    "ENABLE_LGBM_PRIMARY",
    "ENABLE_GPS_ACCURACY_TELEPORT_FILTER",
    "ENABLE_GRAFIK_FULL_NAMES_SOURCE",
    "ENABLE_PANEL_PACKS_CID_MATCH",
    # PICKUP-BUFFER (2026-07-06, decyzja Adriana: powierzchnia = OBIETNICA
    # DECYZYJNA). Load-aware bufor obiecywanego ODBIORU per kubełek obciążenia
    # × solo/worek — ADDYTYWNE pola eta_pickup_promised_* w serializerze best;
    # wewnętrzne eta_pickup_utc (scoring/feasibility/R-LATE) NIETKNIĘTE.
    "ENABLE_LOAD_AWARE_PICKUP_BUFFER",
    # REJESTRACJA 2026-07-06 (sesja refaktor, strażnik test_no_new_unstripped_flags
    # _ratchet): klucz dopisany do flags.json przez at-205 scheduled_flip_gate
    # (L3 GC-real, flip FLIPMASTERA 12:40) — flaga DECYZYJNA (steruje czy GC
    # realnie kasuje plany), czytelnik plan_recheck:2444 flag(name, True).
    # Rejestracja = tylko strip+fingerprint; kanon wartości = flags.json (false).
    "PLAN_GC_DRY_RUN",
    # === PROGRAM REFAKTORU (2026-07-06, docs/refaktor/): rejestracja flag K04/K05/K07
    # POD PRZYSZŁY FLIP (lekcja PLAN_GC_DRY_RUN 06.07: klucz dopisany do flags.json
    # bez wpisu tutaj = czerwona zapadka test_no_new_unstripped_flags_ratchet dla
    # wszystkich). Semantycznie NIE-decyzyjne (telemetria / świeżość odczytu), ale
    # rejestracja daje strip w testach (deterministyczny OFF) + prawdomówny fingerprint.
    "ENABLE_WORLD_RECORD",           # K04: nagrywanie wejść decyzji (telemetria)
    "ENABLE_FLAG_SNAPSHOT",          # K05: flagi zamrożone na czas ticku (świeżość)
    "ENABLE_PRE_RECHECK_BEFORE_POOL",  # K07: prefetch czas_kuriera przed pulą (świeżość)
    "ENABLE_EFFECTS_AFTER_DECISION",   # K08: efekty (shadow-jsonle/loadgov/alert) PO decyzji
    "ENABLE_POS_SOURCE_HIERARCHY",     # K16 (sesja B): adnotacja pos_resolution w resolverze pozycji
    "ENABLE_SCORER_INTERFACE",         # K13: interfejs Scorer (heuristic/lgbm) w core.candidates
    "ENABLE_PLANNER_UNIFIED",          # K15: plan_recheck parametry+simulate przez core.planner
    "ENABLE_PLANNER_UNIFIED_SHADOW",   # K15: porównanie parametrów inline↔planner (log-only)
    # A2 PERF (2026-07-08, sprint p95 pod skalę): deterministyczny budżet solvera
    # OR-Tools (solution_limit) zamiast wall-clock time_limit. Motyw tmux 31: cutoff
    # „na zegarek" wnosi ~1,7% niedeterminizmu replayu (ta sama sytuacja → inna trasa
    # zależnie od obciążenia CPU). ON → stała liczba rozwiązań GLS = powtarzalna
    # trasa; sufit wall-clock (ORTOOLS_DET_WALL_CEILING_MS) zostaje jako bezpiecznik
    # anty-zawis. OFF (default) = BAJT-W-BAJT z dziś (goły time_limit). Czytana w
    # tsp_solver.solve_tsp_with_constraints (decision_flag, cross-proces: shadow/
    # plan-recheck/czasowka liczą tym samym silnikiem). Flip = FLIPMASTER po dowodzie
    # parytetu ON↔OFF na replayu. Stała-fallback OFF + config solution_limit/ceiling niżej.
    "ENABLE_ORTOOLS_DET_TIME_LIMIT",
    # Sprint F (2026-07-08, źródło (0,0)/COORD_GUARD): gdy runtime re-geokod
    # (_repair_bag_coords) ODBIORU bag-ordera FIRMOWEGO (aid∈FIRMOWE_KONTO_ADDRESS_IDS)
    # zawiedzie, użyj FIRMOWE_KONTO_FALLBACK_COORDS (centrala Nadajesz, w bbox)
    # zamiast cichego (0,0). (0,0) snapowało w OSRM → COORD_GUARD sentinel 9999 →
    # kandydat-holder cicho wykluczany (geometria-ślepy pile-on, choroba L2.1).
    # Dotyczy WYŁĄCZNIE odbioru firmowego (pickup w uwagach = nierozwiązywalny;
    # delivery firmowe zawsze geokodowane). OFF = legacy (0,0)→guard bajt-w-bajt.
    # Flip za ACK+replay (peak-only klasa; guard zostaje backstopem). Konsument:
    # dispatch_pipeline._bag_dict_to_ordersim._firmowe_bag_pickup_fallback.
    "ENABLE_FIRMOWE_BAG_COORD_FALLBACK",
    # Pin-memory geocode fallback (2026-07-09): rejestracja jest runtime-neutralna
    # (konsument nadal czyta kanon z flags.json przez C.flag), a zapewnia parytet
    # fingerprintu i izolację żywej wartości w testach.
    "ENABLE_GEOCODE_PIN_MEMORY_FALLBACK",
    # Migracja 1b (ACK Adrian 2026-07-10): wybór parsera panelu v1/v2. Kanon od flipu
    # = flags.json (read-site panel_client czyta flag("USE_V2_PARSER", <env-const>);
    # env drop-in watchera zostaje martwym fallbackiem). Rejestracja: fingerprint
    # parytet per-serwis + strip w testach (izolacja żywej wartości).
    "USE_V2_PARSER",
)

# Stałe-fallback (module-level OFF) dla flag dodanych do ETAP4_DECISION_FLAGS
# 2026-06-14 (gold-gate ETA + r6-breach shadow + PLN A/B). Konsumpcja runtime
# idzie przez C.flag()/decision_flag() z flags.json (KANON live), więc te stałe
# NIE zmieniają zachowania produkcji — pełnią rolę: (1) bezpieczny fallback OFF
# gdy flags.json nie ma klucza, (2) inwariant ETAP4 (test_all_etap4_flags_have_
# module_const) + test-izolacja (conftest wycina klucze z tmp flags.json →
# determinizm suity). Wzorzec jak ENABLE_AUTO_ASSIGN = False (l.691, ta sama era).
ENABLE_ETA_QUANTILE_R6_BAGCAP = False
# Migracja 1b USE_V2_PARSER (ACK Adrian 2026-07-10): stała-fallback dla inwariantu
# ETAP4 + decision_flag/fingerprint. REALNY konsument (panel_client.parse_panel_html)
# czyta flag("USE_V2_PARSER", <panel_client-env-const>) — ta stała NIE steruje
# parserem; kanon po flipie = flags.json (true), rollback hot = klucz false.
USE_V2_PARSER = False
# W0.2 advisory (roadmapa 08, werdykt E-1 „GO hybryda"): bezpiecznik fabrykacji ETA.
# Wykrycie: pred_carry > ETA_FABRICATION_FLOOR_MIN ∧ pred_carry > RATIO×robust_ref,
# gdzie robust_ref = osrm_freeflow(pickup→deliv)·traffic_mult + service + slack
# (fizyczny floor odporny na balon route-simu; Opus formuła). UNRELIABLE → nigdy
# KOORD z fabrykatem (defer/uncertainty). Default OFF; shadow-first (compute-always
# obserwacja `eta_unreliable` niezależnie od flagi, aktywny routing tylko ON).
ENABLE_ETA_FABRICATION_GUARD = False
# W0.5 (werdykt E-7-GO): korekta ETA per-komórka floty (slot×solo/worek) na predykcji
# → OBIETNICA (uczciwość; NIE bramka R6). OOS: MAE 10,39→10,04 (+3,4%), underest −0,8pp.
# Mapa `calib_maps.eta_cell_residual_correct` (generator tools/eta_cell_residual_build).
# Default OFF; shadow-first (skorygowana ETA logowana jako obserwacja niezależnie od flagi).
ENABLE_ETA_CELL_RESIDUAL_CORRECTION = False
# W1/T2.4 (advisory Tura 2): stempel would-be-mode na rekordzie decyzji (czyta stan
# obserwatora mode_observer — NIE krokuje FSM). Default OFF, czysta obserwowalność
# (mode+mode_reason w serializerze); zero wpływu na verdict/score/feasibility.
ENABLE_MODE_LAYER_SHADOW = False
ETA_FABRICATION_FLOOR_MIN = 60.0     # T=60: E-1 łapie 100% fabrykacji (>90 gubi połowę)
ETA_FABRICATION_RATIO = 2.5          # pred>2,5×robust_ref (komponent ratio Opusa vs FP kryzysu)
ETA_ROBUST_SERVICE_MIN = 12.0        # service_time (odbiór+wydanie) w robust_ref
ETA_ROBUST_SLACK_MIN = 5.0           # committed_slack w robust_ref
ETA_ROBUST_URBAN_KMH = 22.0          # fallback freeflow speed gdy OSRM niedostępny (haversine)
ENABLE_R6_BREACH_SHADOW_LOG = False
ENABLE_E2_PLN_AB = False
ENABLE_PLN_COURIER_PAY = False
# #9 conftest-leak DOKOŃCZENIE (audyt 28.06, sesja 29.06): callsite czyta C.flag(
# "ENABLE_PLN_QUALITY_AWARE", False) [dispatch_pipeline ~1055]; flaga jest w
# ETAP4_DECISION_FLAGS (conftest strip), ale brakowało stałej-fallback → test
# test_all_etap4_flags_have_module_const padał. Fallback OFF (literał, NIE env-read
# — unika anty-wzorca env-frozen). Decyzja czytana z flags.json przez C.flag.
ENABLE_PLN_QUALITY_AWARE = False
ENABLE_OBJ_FOOD_AGE_HARD_SLA = False  # Faza 2 2026-06-17 (food-age hard-SLA + warm-start)
ENABLE_END_OF_DAY_SALVAGE = False  # 2026-06-18 (ostatnia godzina pracy firmy — bend reguł końca zmiany)
ENABLE_FEAS_CARRY_READMIT = False  # #483000 2026-06-27 (carry-aware re-admit feasible-path, cap-40 Tier-3)
ENABLE_O2_READY_ANCHOR_SWEEP = False  # O2 re-seq 2026-06-27 (ready-anchor + overage+λ·czas_late objektyw worka, review 02.07)
ENABLE_SLA_ANCHOR_UNIFIED = False  # S1 2026-07-02 (35-min HARD → jedno źródło sla_anchor.py z jawną kotwicą; 3 bliźniaki RAZEM; OFF=inline bajt-w-bajt, ON=te same decyzje + metryka obs sla_anchor_source; KANON=flags.json)
ENABLE_O2_CAPZ_RESEQ = False  # O2 cap-Z reseq 2026-07-02 (wąska reguła Opcji 3 OBOK O2_READY_ANCHOR_SWEEP: detour≤X ∧ carried≤Z=20 ∧ argmin overage ∧ sla nie gorsze; brak→bez zmian; OFF=bajt-w-bajt; flip=ACK po replay; KANON=flags.json)
ENABLE_SLA_GATE_READY_ANCHOR = False  # SLA-gate ready-anchor 2026-07-02 (bramka 35-min SLA pickup_at→READY via sla_anchor kind='ready'; działa gdy SLA_ANCHOR_UNIFIED ON; REALNA zmiana decyzji, replay+ACK; OFF=NOW-anchor bez zmian; KANON=flags.json)
ENABLE_RESERVE_AWARE_TIEBREAK_SHADOW = False  # #3 top10 2026-06-29: log-only tie-break wolny-vs-jadący (shadow); flip=osobna flaga+ACK
RESERVE_TIEBREAK_MARGIN = 30.0  # #3: max Δscore (wolny−jadący) by tie-break dołożył do jadącego (silnik ~obojętny = łatwy zysk)
ENABLE_GPS_DELIVERY_VALIDATION = False  # #5 2026-06-28 (sla_tracker: telemetria physical_verified delivered_at panel-vs-GPS courier_ground_truth; SHADOW, zero wpływu na decyzje/SLA; kanon=flags.json hot)
ENABLE_PLAN_RECHECK_TIER_DWELL = False  # F3 2026-06-28 (dwell tier-aware w plan_recheck; stała-fallback brakowała — dodana przy rejestracji ETAP4. KANON=flags.json (LIVE True))
ENABLE_CZASOWKA_UWAGI_DEADLINE_SHADOW = False  # 2026-06-28 sesja 20 (parse deadline DOSTAWY z `uwagi`→delivery_deadline_uwagi; observability-only, additywne, brak konsumenta decyzyjnego; KANON=flags.json default OFF)
# A2 PERF (2026-07-08, sprint p95): budżet solvera OR-Tools deterministyczny.
# Stała-fallback OFF (literał, NIE env-read — unika anty-wzorca env-frozen #9);
# decyzja czytana z flags.json przez decision_flag (KANON). OFF=bajt-w-bajt goły
# time_limit; ON=solution_limit (powtarzalna trasa) + sufit wall-clock anty-zawis.
ENABLE_ORTOOLS_DET_TIME_LIMIT = False  # A2 2026-07-08 (solution_limit zamiast wall-clock; usuwa ~1,7% niedeterminizmu replayu tmux31; flip=FLIPMASTER po parytecie ON↔OFF; KANON=flags.json/const)
ORTOOLS_DET_SOLUTION_LIMIT = 120       # A2: liczba rozwiązań GLS wiążąca budżet gdy flaga ON (wzór zwalidowany tools/sequential_replay: powtarzalny strop optymalizacji); parytet z produkcyjnym 200ms zmierzony 100% (perf_ortools_det_parity)
# A2 sufit wall-clock = OVERRIDE budżetu callera TYLKO gdy >0. 0 (default) = ZOSTAW
# budżet time_limit_ms callera jako sufit → ON ≤ OFF latencja na KAŻDEJ ścieżce (też
# krótki warm-start food-age 100ms) = ZERO regresji + bajt-w-bajt dla worków gdzie
# solution_limit nie zdąży (wall-clock tnie identycznie jak OFF). >0 (np. 30000) =
# tryb OFFLINE-REPLAY determinism-first (pełne wiązanie solution_limit; NIE dla produkcji).
ORTOOLS_DET_WALL_CEILING_MS = 0        # A2: 0=sufit=budżet callera (produkcja, zero-regresji); >0=override offline-replay

# === P-FLAGREG partia A'/B (2026-07-05): konsty-LUSTRA dla flag rejestrowanych
# w ETAP4/_FINGERPRINT_EXTRA, których czytelnicy używają `flag(name, default)`
# z defaultem INLINE (stała modułu NIE jest konsumowana przez czytelnika —
# służy WYŁĄCZNIE prawdomówności fingerprinta i inwariantowi
# test_all_etap4_flags_have_module_const). Wartość = dosłownie default
# czytelnika (courier_resolver:813/466/1190, geocoding_audit:39). Gdy klucz
# pojawi się w flags.json — i czytelnik, i fingerprint przejdą na json spójnie.
ENABLE_GPS_ACCURACY_TELEPORT_FILTER = False  # courier_resolver:813 default=False
PLAN_GC_DRY_RUN = True  # plan_recheck:2444 default=True (dry-run bezpieczny); kanon=flags.json (at-205 06.07 flip→false); rejestracja: sesja refaktor 06.07
ENABLE_WORLD_RECORD = False  # K04 refaktoru: world_record.enabled() default=False; kanon=flags.json (flip za ACK Adriana)
ENABLE_PRE_RECHECK_BEFORE_POOL = False  # K07 refaktoru: _k07_prefetch_fresh_ck default=False; kanon=flags.json (flip za ACK)
ENABLE_EFFECTS_AFTER_DECISION = False  # K08 refaktoru: effects_buffer.begin default=False; kanon=flags.json (flip za ACK)
ENABLE_POS_SOURCE_HIERARCHY = False  # K16 (sesja B): courier_resolver._resolve_position adnotacja default=False; kanon=flags.json (flip za ACK); rejestracja: sesja A na prośbę B 06.07
ENABLE_SCORER_INTERFACE = False  # K13 refaktoru (ADR-R06): core.candidates → core.scorer default=False; kanon=flags.json (flip za ACK Adriana; SCORER_IMPL='lgbm' primary = POZA zakresem programu)
ENABLE_PLANNER_UNIFIED = False  # K15 refaktoru (ADR-R03): plan_recheck._gen_one_bag_plan parametry tier+simulate przez core.planner default=False; kanon=flags.json (flip za ACK Adriana)
ENABLE_PLANNER_UNIFIED_SHADOW = False  # K15 refaktoru: przy głównej OFF porównaj parametry inline↔core.planner, rozjazd→WARNING PLANNER_PARAM_MISMATCH (log-only, zero wpływu); kanon=flags.json (flip za ACK)
# (ENABLE_FLAG_SNAPSHOT = False — zdefiniowana wyżej w bloku K05)
ENABLE_GRAFIK_FULL_NAMES_SOURCE = True       # courier_resolver:466 default=True
ENABLE_PANEL_PACKS_CID_MATCH = True          # courier_resolver:1190 default=True
ENABLE_GPS_QUALITY_SHADOW = True             # courier_resolver:812 default=True (obs)
ENABLE_GEOCODING_AUDIT_LOG = True            # geocoding_audit:39 default=True (env-first, potem flag)

# === L0.1 fallbacki (2026-07-01): stałe dla 14 flag dopisanych do ETAP4 =====
# W ODRÓŻNIENIU od bloku wyżej (era 2026-06-14, featury shipowane ciemne → OFF)
# te flagi są USTALONYM stanem produkcji (flags.json=True od tygodni). Stała =
# intencja steady-state: utrata klucza json NIE flipuje zachowania po cichu
# (mina klasy COMMIT_DIVERGENCE — const/json o przeciwnych intencjach).
# KANON pozostaje flags.json (decision_flag: json wygrywa); literały, NIE
# env-read (anty-wzorzec env-frozen). 3 flagi z 14 mają stałe gdzie indziej:
# ENABLE_R6_SOFT_PEN_CAP (niżej, wyrównana), ENABLE_OBJ_COMMITTED_PICKUP_PENALTY
# + ENABLE_R1_WAVE_SCOPED_DIRECTIONALITY (env-defaulty wyrównane do "1").
ENABLE_EXCLUDE_BY_CID = True
ENABLE_INACTIVE_COURIER_GUARD = True
ENABLE_ZOMBIE_PICKUP_AT_GUARD = True
ENABLE_GPS_BBOX_GUARD = True
ENABLE_V3273_WAIT_REJECT_PICKED_UP_ONLY = True
ENABLE_NEW_COURIER_RAMP = True
ENABLE_PLN_RESORT_WITHIN_TIER = True
ENABLE_BEST_EFFORT_POS_SOURCE_KEY = True
ENABLE_COURIER_LAST_KNOWN_POS = True
ENABLE_LOAD_PLAN_PURE_READ = True
ENABLE_PANEL_PACKS_BAG_RECONSTRUCTION = True
ENABLE_CARRIED_FIRST_RELAX_READY_ANCHOR = False  # 2026-06-29 case Rećki (ready-anchor R6 w 3 bramkach carried-first plan_recheck; OFF=legacy in-bag/byte-identyczne; KANON=flags.json)
ENABLE_DELIVERED_RESURRECTION = False  # 2026-06-29 case Pizzeria 105 (panel_watcher wskrzesza delivered-które-wróciło-do-packs po ręcznym cofnięciu w gastro; OFF=stare ignorowanie na zawsze; KANON=flags.json)
ENABLE_COORD_SENTINEL_INGEST_GUARD = False  # L2.1 2026-07-01 (walidator coords u ingest + guardy konsumentów geometrii; OFF=legacy (0,0)-as-data/V328-eject; KANON=flags.json)
ENABLE_FIRMOWE_BAG_COORD_FALLBACK = False  # Sprint F 2026-07-08 (bag-order firmowy z nierozwiązywalnym ODBIOREM → FIRMOWE_KONTO_FALLBACK_COORDS zamiast cichego (0,0)→COORD_GUARD; OFF=legacy (0,0) bajt-w-bajt; KANON=flags.json)
ENABLE_AVAILABLE_FROM_SINGLE_SOURCE = False  # L4 2026-07-02 (jedno źródło available_from=max(now,shift_start) w courier_resolver; konsumenci #1/#3/#5/chokepoint dziedziczą; OFF=stare ścieżki bajt-w-bajt; KANON=flags.json)
ENABLE_ETA_LOAD_AWARE = False  # L5.1 2026-07-05 (bufor optymizmu nogi ODBIORU z tabeli kalibracji eta_load_aware_calib.json; OFF=shadow-only metryki eta_la_*; ON=przesuwa eta_pickup_utc/travel_min — oś OBIETNICY, nie feasibility; KANON=flags.json)
ENABLE_PLAN_RECHECK_GATES = False  # L3 2026-07-02 (bramka ZAPISU regenu plan_recheck: compare-and-keep R6 carried>35 — nie nadpisuj dobrego planu gorszym-sekwencyjnie; OFF=zapis regenu bajt-w-bajt; KANON=flags.json)
ENABLE_COURIER_PLANS_GC = False  # L3 2026-07-02 (GC courier_plans: terminal-stop prune + zombie by age/no-active przez plan_manager API pod lockiem; PLAN_GC_DRY_RUN default True; OFF=brak GC jak dziś; KANON=flags.json)
ENABLE_SPLIT_LAYER_GUARD = False  # L7.3 2026-07-03 (R2 ROOT-9, INV-LAYER-1/2): OBSERWACYJNY strażnik warstw — re-assert _assert_feasibility_first na KAŻDYM EMIT (feasible-path) + garda zapisu feasibility_verdict poza L5 (setter). OFF=bajt-parytet (zero logu/jsonl, decyzja nietknięta); ON=tylko log WARNING + dispatch_state/split_layer_guard.jsonl. NIE-decyzyjna (poza ETAP4). KANON=flags.json

# === D.3 fala A fallbacki (2026-07-02): stałe dla 15 flag route/kanon =========
# ZMIGROWANE z env-frozen (plan_recheck.py `os.environ.get(...,"0")=="1"`) do
# flags.json (KANON). Były LIVE ON przez drop-iny systemd; po migracji env jest
# MARTWY (decision_flag nie czyta env). Stała = intencja STEADY-STATE (ON):
# utrata klucza json NIE flipuje zachowania po cichu (mina COMMIT_DIVERGENCE —
# const/json o przeciwnych intencjach). KANON = flags.json (decision_flag: json
# wygrywa nad stałą). plan_recheck.py czyta te flagi przez common.decision_flag
# na starcie procesu (oneshot=fresh/tick) + refresh per wywołanie w długobieżnym
# panel-watcherze (recanon/redecide) — hot-reload. Zero odczytu env.
ENABLE_GPS_FREE_ANCHOR = True
ENABLE_GPS_FREE_ANCHOR_LAST_POS = True
ENABLE_PLAN_REAL_PICKED_UP_AT = True
ENABLE_PLAN_SEQUENCE_LOCK = True
ENABLE_PLAN_CANON_ORDER_INVARIANTS = True
ENABLE_NO_RETURN_TO_DEPARTED_PICKUP = True
ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE = True
ENABLE_IMMEDIATE_REDECIDE_ON_PICKUP = True
ENABLE_RECANON_ON_WRITE = True
# migracja B2 2026-07-18: const = steady-state flags.json (True) — const≠json to mina klasy L6
ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION = True
# D6a SHADOW 2026-07-18: const-fallback OFF (testy/strip deterministycznie bez servingu);
# produkcja czyta flags.json=true (cień od restartu shadow po peaku 18.07)
ENABLE_ETA_CALIB_PROMISE_SHADOW = False
ENABLE_CARRIED_FIRST_RELAX = True
ENABLE_CARRIED_AGE_TZ_FIX = True
ENABLE_LEX_COMMITTED_WINDOW_SHADOW = True
ENABLE_LEX_COMMITTED_WINDOW = True
ENABLE_RELAX_COLOC_PICKUP = True
ENABLE_NONCARRIED_DROPOFF_REORDER = True
# Sprint 1 NO-GPS-EQUAL (Adrian 2026-06-29 „bez kary przed zmianą"): gdy ON → zeruje
# karę score pre_shift (`bonus_v325_pre_shift_soft`, oba źródła: stała V325 + gradient
# _pre_shift_gradient_penalty). „Kurier dotrze później" obsługuje LEGALNA ścieżka:
# clamp do shift_start (ENABLE_PRE_SHIFT_DEPARTURE_CLAMP) + R-LATE-PICKUP propozycja
# przedłużenia DO RESTAURACJI (ENABLE_LATE_PICKUP_HARD_GATE) — NIE ukryta kara w score.
# HARD-reject >30min-przed-zmianą (feasibility_v2 V325_PRE_SHIFT_HARD_REJECT_MIN) ZOSTAJE
# (realna niewykonalność). Default OFF=stała kara zachowana; flip flags.json=True po replayu+ACK.
ENABLE_PRE_SHIFT_EQUAL_NO_PENALTY = False

# E7-doklejka 3: stałe kar BUG A/B nadpisywalne z flags.json (flip wartości
# startowych werdyktu razem z flagą, hot-reload bez restartu; fallback = stała
# modułu/env). Test-izolacja: conftest wycina te klucze z tmp-kopii flags.json
# (jak ETAP4_DECISION_FLAGS), żeby testy sterowały przez patch stałej.
FLAGS_JSON_NUMERIC_OVERRIDES = (
    "BAG_TIME_SUM_PENALTY_PER_MIN",
    "BAG_TIME_MAX_PENALTY_PER_MIN",
    "BAG_TIME_FIFO_TIE_PENALTY",
    "R5_DETOUR_PENALTY_PER_KM",
    "R5_DETOUR_FREE_THRESHOLD_KM",
    # GPS-03/DATA-04 (2026-06-11):
    "GPS_AGE_DISCOUNT_FREE_MIN",
    "GPS_AGE_DISCOUNT_PER_MIN",
    "GPS_AGE_DISCOUNT_CAP",
    # FRONT-B (2026-06-11):
    "PICKUP_COORDS_DRIFT_WARN_M",
    # L6.C2 (2026-07-04): kwantyzacja termów czasowych lex_qual (patrz stała ~2910)
    "LEXQUAL_TIME_QUANT_MIN",
    # BUNDLE-06 Faza 1 + BUNDLE-03 (2026-06-12):
    "BUNDLE_FIT_W_COS",
    "BUNDLE_FIT_THERMAL_FREE_MIN",
    "BUNDLE_FIT_THERMAL_PER_MIN",
    "BUNDLE_FIT_SPAN_FREE_MIN",
    "BUNDLE_FIT_SPAN_PER_MIN",
    "FIX_C_ADDITIVE_PEN_PER_KM",
    "FIX_C_ADDITIVE_COS_TRIGGER",
    # AUTON-01 (2026-06-13): progi bramki + bezpieczniki egzekutora.
    "AUTO_ASSIGN_MIN_POOL_FEASIBLE",
    "AUTO_ASSIGN_SCORE_DISTRUST_CEILING",
    "AUTO_ASSIGN_MAX_PER_HOUR",
    "AUTO_ASSIGN_OVERRIDE_COOLDOWN_MIN",
    # SCALE-01 (2026-06-13): zahardkodowane "capy" do flags.json (multi-city
    # prep). Refaktor BEHAVIOR-PRESERVING — defaulty stałych modułu = obecne
    # wartości produkcyjne (EARLY_BIRD=60 / MIN_PROPOSE=-100 / bag=8 / 15 km).
    # Override z flags.json (hot-reload) podmieni per-miasto; conftest wycina
    # te klucze z tmp-kopii → testy sterują przez stałą modułu (jak BUG A/B).
    "EARLY_BIRD_THRESHOLD_MIN",
    "MIN_PROPOSE_SCORE",
    "MAX_BAG_SANITY_CAP",
    "MAX_PICKUP_REACH_KM",
    # FOOD-AGE HARD-SLA (2026-06-18 lewar latencji): krótszy limit czasu solvera
    # dla warm-startowanego ON-solve (startuje z dobrego planu base). Twarde
    # ograniczenie gwarantuje SLA niezależnie od jakości solve → cięcie czasu
    # ryzykuje tylko nieco mniej optymalizacji food-age, NIE regresję SLA.
    "OBJ_FOOD_AGE_HARD_SLA_ON_SOLVE_MS",
)

# Front C (2026-06-12): killswitche INFRA (nie-decyzyjne) sterowane z flags.json
# hot-reload — conftest wycina je z tmp-kopii jak ETAP4 (testy deterministyczne
# niezależnie od żywych killswitchy; klasa lekcji #180/#183).
TEST_ISOLATED_INFRA_FLAGS = (
    "ENABLE_OSRM_TABLE_CELL_CACHE",
    "ENABLE_PANEL_DETAIL_PREFETCH",
    "PANEL_DETAIL_PREFETCH_WORKERS",
    "ENABLE_STAGE_TIMING_OBSERVATION",
    # perf-lazy (03.07): żywy flip 00:25 zmienił zachowanie script-runnerów
    # (flake test_v319c_sub_a: 4/30 FAIL przy ON / 0/30 OFF — mtime-cache
    # planów serwował stan sprzed zapisu przy zapisach w tym samym ticku
    # zegara). Testy sterują flagą JAWNIE (monkeypatch stałej), nie żywym
    # flags.json.
    "ENABLE_PERF_LAZY_MEMBERS",
)

# Flagi zunifikowane już wcześniej wzorcem runtime (E2 audytu 10.06) — wchodzą
# do fingerprinta, ich call-sites pozostają bez zmian.
_FINGERPRINT_EXTRA_FLAGS = (
    "ENABLE_V327_MULT_SIGN_GUARD",
    "ENABLE_V328_HEURISTIC_SHIFT_END_GUARD",
    "ENABLE_FAIL12_STOREPOS_STRICT",
    "ENABLE_WORKING_OVERRIDE_GRAFIK_CAP",
    # === P-FLAGREG partia B (2026-07-05, Sprint 2.5-prep tmux18): flagi
    # obserwacyjne (shadow-metryki) + alertowe — NIE sterują decyzją, ale ich
    # rozjazd per-proces = ślepota metryk/alertów. FP_EXTRA NIE jest stripowane
    # w conftest (semantyka testów bez zmian); wartości w fingerprint =
    # decision_flag (flags.json → stała modułu). Dla 2 flag bez klucza json
    # dodano konsty-lustra defaultów czytelników (ENABLE_GPS_QUALITY_SHADOW,
    # ENABLE_GEOCODING_AUDIT_LOG). Inwentarz: eod_drafts/2026-07-05/FLAGREG_*.md.
    "ENABLE_ADDRESS_COORDS_MISMATCH_SHADOW",
    "ENABLE_ADDRESS_TOWN_MISMATCH_SHADOW",
    "ENABLE_BEST_EFFORT_FASTEST_PICKUP_SHADOW",
    "ENABLE_BEST_EFFORT_OBJM_SHADOW",
    "ENABLE_BUG4_RESEQ_SHADOW",
    "ENABLE_ETA_QUANTILE_SHADOW",
    "ENABLE_FAIL03_K2_SHADOW",
    "ENABLE_FEAS_CARRY_BLIND_SHADOW",
    "ENABLE_GPS_QUALITY_SHADOW",
    "ENABLE_GEOCODING_AUDIT_LOG",
    "ENABLE_LGBM_TWOMODEL_SHADOW",
    "ENABLE_MIN_DELIVERED_AT_SHADOW",
    "ENABLE_OBJM_LEXR6_SELECT_SHADOW",
    "ENABLE_PICKUP_DEBIAS_SHADOW",
    "ENABLE_PLN_OBJECTIVE_SHADOW",
    "ENABLE_PREP_BIAS_SHADOW",
    "ENABLE_PREP_VARIANCE_ANOMALY_SHADOW",
    "ENABLE_READY_AT_INSTRUMENTATION",
    "ENABLE_REPO_COST_SHADOW",
    "OBSERVABILITY_PER_CANDIDATE_ENABLED",
    "ENABLE_STAGE_TIMING_OBSERVATION",
    "AUTO_KOORD_TELEGRAM_INFO_ENABLED",
    "CZASOWKA_T0_ALERT_ENABLED",
    "ENABLE_BAG_TIME_ALERTS",
    "ENABLE_DATA_ALERTS",
    "ENABLE_FIRMOWE_KONTO_KOORD_ALERTS",
    "ENABLE_FIRMOWE_KONTO_TELEGRAM_PROPOSALS",
    "ENABLE_NOTIFY_PRIORITY_ROUTING",
    "ENABLE_STATE_PANEL_DIVERGENCE_ALERT",
    "SHIFT_NOTIFY_ENABLED",
)


# OBJ FOOD-AGE SHADOW (2026-06-14): thread-local override flagi food-age dla
# forward comparatora. Pipeline liczy kandydatów w ThreadPoolExecutor → globalny
# toggle byłby race-unsafe; thread-local izoluje per-wątek. food_age_override(True)
# wymusza ON tylko wokół re-computu shadow, NIE ruszając decyzji produkcyjnej.
_FOOD_AGE_TL = threading.local()


@contextlib.contextmanager
def food_age_override(value):
    """Wymuś ENABLE_OBJ_DELIVERY_FOOD_AGE=value w tym wątku na czas bloku."""
    _prev = getattr(_FOOD_AGE_TL, "override", None)
    _FOOD_AGE_TL.override = value
    try:
        yield
    finally:
        _FOOD_AGE_TL.override = _prev


def decision_flag(name: str) -> bool:
    """Flaga decyzyjna wspólna cross-proces: flags.json → stała modułu → False.

    Stała modułu czytana przez globals() W CZASIE WYWOŁANIA (nie importu) —
    testy patchujące common.ENABLE_X działają, o ile klucza nie ma w flags.json.

    Wyjątek: ENABLE_OBJ_DELIVERY_FOOD_AGE respektuje thread-local override
    (food_age_override) — forward shadow comparator wymusza ON per-wątek.
    """
    if name == "ENABLE_OBJ_DELIVERY_FOOD_AGE":
        _ov = getattr(_FOOD_AGE_TL, "override", None)
        if _ov is not None:
            return bool(_ov)
    return bool(load_flags().get(name, globals().get(name, False)))


def flag_fingerprint() -> str:
    """Jedna linia z wartościami wszystkich flag decyzyjnych (ETAP 4 KROK 3).

    Logowana przy starcie każdego procesu silnika; po unifikacji fingerprinty
    shadow / czasowka / plan-recheck MUSZĄ być identyczne.
    """
    names = ETAP4_DECISION_FLAGS + _FINGERPRINT_EXTRA_FLAGS
    return " ".join(f"{n}={int(decision_flag(n))}" for n in names)


_V326_PAIR_LOG = logging.getLogger("dispatch.v326_pair")


def check_v326_pair_coherence(or_tools=None, grouping=None) -> bool:
    """D.3 fala B: strażnik sprzężenia pary V326 (#13). GROUPING buduje super-pickup
    (route_simulator_v2:299), a jego dedupe robi gałąź OR-Tools (:438) — GROUPING=ON
    przy OR_TOOLS=OFF = double-insert super-pickupa. Zwraca True gdy para NIESPÓJNA
    (grouping AND NOT or_tools) i loguje ostrzeżenie; inaczej False. BEZ zmiany
    zachowania (tylko log — konsument route_simulator_v2 nietknięty). Argumenty None →
    efektywne wartości z decision_flag (flags.json→stała). Wołane raz przy imporcie
    (startup sanity) + z testu pary."""
    if or_tools is None:
        or_tools = decision_flag("ENABLE_V326_OR_TOOLS_TSP")
    if grouping is None:
        grouping = decision_flag("ENABLE_V326_SAME_RESTAURANT_GROUPING")
    incoherent = bool(grouping) and not bool(or_tools)
    if incoherent:
        _V326_PAIR_LOG.warning(
            "V326_PAIR_INCOHERENT ENABLE_V326_SAME_RESTAURANT_GROUPING=ON przy "
            "ENABLE_V326_OR_TOOLS_TSP=OFF → double-insert super-pickupa (#13). "
            "Para atomowa D.3 fala B: ustaw obie flagi jednakowo w flags.json.")
    return incoherent


def now_utc():
    return datetime.now(timezone.utc)


def now_iso():
    return now_utc().isoformat()


def _file_log_blocked_under_test() -> bool:
    """Test-hygiene (2026-07-03): czy FileHandlery do logów mają milczeć.

    ~34 moduły silnika robią module-level `setup_logger(..., PROD path)` —
    KAŻDY proces importujący moduł (pytest in-process i script-runner) pisał
    do żywych logów. Skutek: testowe FLAG_FINGERPRINT z conftest-owo odartym
    flags.json (defaulty) w logs/czasowka.log → log-based instrumenty kłamały
    (fałszywy INTERMITTENT-COLD 22-40% w flag_fingerprint_check; korelacja z
    journalem 03.07: 334/334 ticków serwisu warm, 0/9 klastrów cold od serwisu).
    Markery: PYTEST_CURRENT_TEST (pytest per-test + jawnie w ScriptRunItem) lub
    DISPATCH_UNDER_PYTEST (conftest, cała sesja wraz z import-time).
    Opt-out dla testów weryfikujących file-log: ALLOW_FILE_LOG_IN_TEST=1
    (wzorzec identyczny z guardem telegram_utils L1 / ALLOW_TELEGRAM_IN_TEST)."""
    if os.environ.get("ALLOW_FILE_LOG_IN_TEST") == "1":
        return False
    return bool(os.environ.get("PYTEST_CURRENT_TEST")
                or os.environ.get("DISPATCH_UNDER_PYTEST"))


class _ProdFileLogTestFilter(logging.Filter):
    """Filtr per-rekord (nie per-attach): pytest ustawia PYTEST_CURRENT_TEST
    dopiero w fazie testu, PO imporcie modułu — decyzja musi zapadać przy
    emisji, nie przy podpinaniu handlera."""

    def filter(self, record):  # noqa: A003 - API logging
        return not _file_log_blocked_under_test()


def setup_logger(name: str, log_file: str = None):
    """Prosty logger z file handlerem (pod pytestem file-handler wyciszony)."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
    logger.setLevel(logging.INFO)
    fmt = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"
    )

    ch = logging.StreamHandler()
    ch.setFormatter(fmt)
    logger.addHandler(ch)

    if log_file:
        Path(log_file).parent.mkdir(parents=True, exist_ok=True)
        # delay=True: plik otwierany dopiero przy 1. przepuszczonym rekordzie —
        # test nie tyka (nawet nie tworzy) żywego pliku loga.
        fh = logging.FileHandler(log_file, delay=True)
        fh.setFormatter(fmt)
        fh.addFilter(_ProdFileLogTestFilter())
        logger.addHandler(fh)

    return logger


# === BAG CAPS (V3.1 reformulated 12.04) ===
# Wave size NIE jest biznesowa regula per tier. Wave size wynika z:
#   - SLA 35 min per order (feasibility + TSP simulation)
#   - Traffic multiplier (traffic.py)
#   - Kurier + mapa aktualna
# Nizsze capy ponizej to TECHNICZNE guardy, nie biznesowe reguly:

# Performance guard dla PDP-TSP brute-force.
# Bag 5 = 120 permutacji TSP, PDP ~<200ms. Bag 6 = 720, ~500ms+ ryzyko.
# Faza 9 (OR-Tools VRPTW) podniesie do 8-10.
MAX_BAG_TSP_BRUTEFORCE = 5

# Anomaly guard: bag >8 = blad stanu albo koordynatora.
# Feasibility zwraca NO + alert krytyczny.
# SCALE-01: env-default = 8 (bez zmiany); kanon override = flags.json
# (FLAGS_JSON_NUMERIC_OVERRIDES). Konsumenci czytają przez load_flags().get(...).
MAX_BAG_SANITY_CAP = int(os.environ.get("MAX_BAG_SANITY_CAP", "8"))

# SCALE-01: pickup-reach cap (feasibility fast-filter "pickup_too_far") i
# early-bird KOORD threshold — wyciągnięte z feasibility_v2 / dispatch_pipeline
# do kanonu common.py (multi-city prep). Defaulty = obecne produkcyjne wartości
# (15 km / 60 min); override per-miasto z flags.json (hot-reload). Konsumenci
# czytają przez load_flags().get("KEY", C.KEY).
MAX_PICKUP_REACH_KM = float(os.environ.get("MAX_PICKUP_REACH_KM", "15.0"))
EARLY_BIRD_THRESHOLD_MIN = int(os.environ.get("EARLY_BIRD_THRESHOLD_MIN", "60"))


# === TIMEZONE + TIMESTAMP PARSING (V3.1 P0.3) ===

WARSAW = ZoneInfo("Europe/Warsaw")

# Sentinel: gwarantuje determinizm sortowania przy None timestamps
DT_MIN_UTC = datetime(1, 1, 1, tzinfo=timezone.utc)


def parse_panel_timestamp(value) -> "datetime | None":
    """Parsuje timestamp z panelu/state do aware UTC datetime.

    Akceptuje:
      - datetime z tzinfo (znormalizowany do UTC)
      - datetime naive (interpretowany jako Warsaw)
      - str ISO z 'T' i offsetem/Z: "2026-04-12T10:50:21.736800+00:00"
      - str naive Warsaw panel: "2026-04-12 13:08:07"
    Zwraca None dla None/garbage (caller decyduje o fallback).
    """
    if value is None:
        return None
    try:
        if isinstance(value, datetime):
            dt = value
        elif isinstance(value, str):
            v = value.strip()
            if not v:
                return None
            if "T" in v:
                dt = datetime.fromisoformat(v.replace("Z", "+00:00"))
            else:
                dt = datetime.strptime(v, "%Y-%m-%d %H:%M:%S").replace(tzinfo=WARSAW)
        else:
            return None

        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=WARSAW)

        return dt.astimezone(timezone.utc)
    except (ValueError, TypeError):
        return None


# === OSRM FALLBACK CONFIG (V3.1 P0.5) ===
# Kalibracja 12.04.2026: 206 delivered orders, median=1.371, std=0.354
# Raw data: dispatch_state/calibration_20260412_baseline.json
# REKALIBRACJA 2026-06-13 (GEO-04 audyt): 482 świeże delivered orders (events.db
#   NEW_ORDER coords vs OSRM :5001) → median=1.390, mean=1.489, std=0.329,
#   p10=1.207 p90=1.916. Różnica vs 1.37 = 1.4% = w granicy szumu (~0.06σ) →
#   BEZ ZMIANY. Brak trendu „zaniżania" (baseline median 1.371 → 1.390 stabilne).
#   Werdykt+dane: eod_drafts/2026-06-13/geo04_road_factor.md.
HAVERSINE_ROAD_FACTOR_BIALYSTOK = 1.37

# === COORD SANITY GUARD (Lekcja #140, 2026-05-21) ===
# Bug 2026-05-21: bag-order pickup_coords=None → (0,0) → OSRM SNAPUJE (0,0) do
# krawędzi ekstraktu (~113 km) i zwraca code:Ok z trasą ~117-148 min → phantom
# leg → false INFEASIBLE → wycięcie wolnych kurierów. Fail-loud #81 (haversine)
# NIE odpalał, bo OSRM "succeeded". Guard: KAŻDA współrzędna wchodząca do OSRM
# musi być w bbox metropolii Białystok; (0,0)/None/cross-country → sentinel+log,
# NIGDY cicha realistyczna trasa. Bbox HOJNY (≈±55 km) — pokrywa wszystkie
# realne adresy dispatchu (Wasilków/Choroszcz/Supraśl/Zabłudów/Łapy), odrzuca
# (0,0) [lat 0] i geokody cross-country. R6=35min ⇒ realny zasięg ~25-30 km, więc
# >±55 km = na pewno błąd danych, nie legit zlecenie.
#
# === GEO-06/07 (audyt 2026-06-13): DWA bboxy — celowo różne, NIE niespójność ===
# W kodzie żyją DWA bboxy o ROZŁĄCZNYCH zadaniach (nie błąd, nie do „unifikacji"):
#   (1) BIALYSTOK_BBOX_LAT/LON (niżej) = METROPOLIA ±55 km — filtr trucizny dla
#       OSRM/GPS (coords_in_bialystok_bbox); MUSI być szeroki, by przyjąć
#       Wasilków/Zabłudów/Łapy do routingu.
#   (2) GEOCODE_BBOX_* (sekcja „GEOCODE BBOX GUARD" niżej) = OBSZAR OBSŁUGI
#       +~28 km — filtr akceptacji geokodu (_in_service_bbox w geocoding.py).
# INWARIANT KANONICZNY (test go pilnuje, test_geo_bbox_consistency):
#   GEOCODE_BBOX ⊂ BIALYSTOK_BBOX (ścisłe podzbiór). Inaczej geocoding mógłby
#   zaakceptować punkt, który OSRM zaraz odrzuci jako truciznę → niespójność.
# Zweryfikowane 2026-06-13: wszystkie realne miejscowości dispatchu (Wasilków/
#   Choroszcz/Zabłudów/Łapy/Kleosin/Supraśl/Ignatki) mieszczą się w OBU. Werdykt:
#   eod_drafts/2026-06-13/geo0607_bbox.md.
BIALYSTOK_BBOX_LAT = (52.6, 53.7)
BIALYSTOK_BBOX_LON = (22.3, 24.1)


def coords_in_bialystok_bbox(ll) -> bool:
    """True gdy ll=(lat,lon) jest realną współrzędną w zasięgu dispatchu.
    False dla None / nie-2-tuple / NaN / (0,0) / poza bbox metropolii."""
    try:
        if ll is None:
            return False
        lat, lon = float(ll[0]), float(ll[1])
    except (TypeError, ValueError, IndexError):
        return False
    if lat != lat or lon != lon:  # NaN
        return False
    if lat == 0.0 and lon == 0.0:
        return False
    lo_lat, hi_lat = BIALYSTOK_BBOX_LAT
    lo_lon, hi_lon = BIALYSTOK_BBOX_LON
    return (lo_lat <= lat <= hi_lat) and (lo_lon <= lon <= hi_lon)

# Buckety prędkości oparte na KORKACH (nie na popycie).
# Peak operacyjny (Nd 15:00 = 45 orders/h) ma PUSTE ulice.
# Peak korkowy (Pt 17-19) ma SZCZYT ruchu.
FALLBACK_BASE_SPEEDS_KMH = {
    "weekday_rush": 20,       # Pn-Pt 15-19 — peak korkowy Białegostoku
    "weekday_evening": 24,    # Pn-Pt 19-22 — po rushu, jeszcze spory ruch
    "weekend_evening": 26,    # Sb-Nd 17-22 — popyt wysoki, ruch umiarkowany
    "lunch_midday": 28,       # Pn-Pt 11-15 — średni ruch
    "off_peak": 32,           # reszta (noc, poranek, Nd popołudnie) — luźno
}


def get_time_bucket(dt_utc: datetime) -> str:
    """Mapuje aware UTC datetime na bucket korkowy (Warsaw local time).

    Raises TypeError jeśli dt_utc nie ma tzinfo (fail fast, nie zgadujemy TZ).
    """
    if dt_utc.tzinfo is None:
        raise TypeError("get_time_bucket requires aware datetime (got naive)")
    local = dt_utc.astimezone(WARSAW)
    hour = local.hour
    wd = local.weekday()  # 0=Pn, 6=Nd

    if wd < 5:  # Pn-Pt
        if 15 <= hour < 19:
            return "weekday_rush"
        if 19 <= hour < 22:
            return "weekday_evening"
        if 11 <= hour < 15:
            return "lunch_midday"
        return "off_peak"
    else:  # Sb-Nd
        if 17 <= hour < 22:
            return "weekend_evening"
        return "off_peak"


def get_fallback_speed_kmh(dt_utc: datetime) -> float:
    """Zwraca prędkość fallback [km/h] dla danego momentu."""
    bucket = get_time_bucket(dt_utc)
    return FALLBACK_BASE_SPEEDS_KMH[bucket]


# === V3.26 BUG-3 STEP 1 — OSRM TRAFFIC MULTIPLIER (Adrian's table) ===
# Self-hosted OSRM Docker (:5001) returns FREE-FLOW road durations (zero
# traffic data). Białystok delivery shadow shows OSRM under-estimates by
# 20-60% during weekday rush. Adrian operator gut + empirical bucket SHAPE
# (anchor-A method, n=42,494 deliveries Nov2025-Apr2026) -> ship Adrian's
# conservative table.
#
# 2026-04-25 EMPIRICAL VALIDATION (Wariant B reconstruction, n=767 samples,
# 14-day window 04-11→04-25, events.log + orders_state + OSRM batch):
# After 5min delivery_overhead adjustment (parking+walk+ring+handover),
# 6/9 buckets z n>=50 PASS Adrian's table ±15%:
#   wd_13-15 adj=1.20 vs 1.30 (-7.5%) KEEP
#   wd_15-17 adj=1.41 vs 1.60 (-11.6%) KEEP
#   wd_17-19 adj=1.03 vs 1.20 (-14.2%) KEEP
#   wd_19-21 adj=1.11 vs 1.10 (+0.8%) KEEP
#   wd_21-24 adj=0.98 vs 1.00 (-2.2%) KEEP
#   weekend  adj=1.02 vs 1.00 (+1.6%) KEEP
# 3 buckets INSUFFICIENT (n<50): wd_08-10/wd_10-12/wd_12-13 — extrapolation OK.
# Report: /tmp/v326_osrm_empirical_aggregation_2026-04-25.md
#
# Convention: bucket = [hour_lo, hour_hi) — lower inclusive, upper exclusive.
V326_OSRM_TRAFFIC_TABLE = {
    "weekday": [   # MON-FRI (weekday()==0..4)
        # RECALIB 2026-06-05 (wariant B) — krzywa godzinowa median-based zastąpiła
        # statyczną tabelę V3.27.3 TASK G. Wyliczona z 595 weekday tropów GATE B
        # (eod_drafts/2026-06-03/hourly_multiplier_curve.md), zweryfikowana na 688
        # tropach (eod_drafts/2026-05-14/tomtom_poc/recalib_verdict_B_2026-06-05.txt):
        # bias RAZEM −2.23→−1.37 min, MAE 3.80→3.72, tier-1 GPS bias −1.37→−0.39.
        # Zeruje medianowe niedoszacowanie popołudnia (godz 12-16,19: −1..−2 → ~0).
        # Resztkowy bias = ogon breachy (wariancja) → zadanie dla live-traffic A/B.
        # Wariant B: 17-18 = 1.25 (doc-curve 1.30/1.35 przestrzeliwała +0.5/+0.36).
        # Poprzednie wartości V3.27.3 TASK G zachowane w git (tag pre-recalib).
        (0, 9, 1.0),
        (9, 10, 1.15),
        (10, 12, 1.25),
        (12, 13, 1.40),
        (13, 14, 1.50),
        (14, 15, 1.35),
        (15, 17, 1.55),    # 15-16 i 16-17 (tier-1 GPS blended w krzywej)
        (17, 18, 1.25),    # wariant B (doc-curve 1.30 → ściągnięte)
        (18, 19, 1.25),    # wariant B (doc-curve 1.35 → ściągnięte)
        (19, 20, 1.25),
        (20, 21, 1.10),
        (21, 24, 1.05),
    ],
    # RECALIB WEEKEND 2026-06-12 (smoothed, analog weekday wariant B) — median-based
    # z GATE B (eod_drafts/2026-05-14/tomtom_poc/recalib_weekend_verdict_2026-06-05.txt,
    # smoothed: validate_weekend_smoothed.py). Walidacja na danych do 11.06
    # (sob n=186: bias −1.75→−1.03, RMSE 5.32→5.18; ndz n=215: bias −3.06→−1.08,
    # MAE 3.84→3.51, win 63%). Stara tabela (sob max 1.2 / ndz flat 1.0, V3.27 Bug X)
    # systematycznie zaniżała niedzielę (bias do −3.96 OOS). Poprzednie wartości w git
    # (tag pre-weekend-recalib). Caveat: per-godz n cieńsze niż weekday — walidacja
    # OOS przez monitor_recalib_oos.py --day-kind weekend do ~22.06.
    "saturday": [   # SAT (weekday()==5)
        (0, 12, 1.0),
        (12, 13, 1.30),
        (13, 16, 1.20),
        (16, 17, 1.55),
        (17, 18, 1.45),
        (18, 21, 1.25),
        (21, 22, 1.10),
        (22, 24, 1.0),
    ],
    "sunday": [     # SUN (weekday()==6) — lunch/popołudnie realnie NIE-płaskie
        (0, 11, 1.0),
        (11, 12, 1.50),
        (12, 13, 1.40),
        (13, 15, 1.35),
        (15, 16, 1.45),
        (16, 19, 1.30),
        (19, 20, 1.15),
        (20, 24, 1.0),
    ],
}


def get_traffic_multiplier(dt_utc: datetime) -> float:
    """Zwraca traffic multiplier dla aware UTC datetime (Warsaw local).

    V3.27 Bug X fix: jednolite traktowanie weekday/saturday/sunday — list-based
    buckets per dzień. Sobota peak 12-21 (max 1.2), niedziela płaska 1.0.

    Convention: bucket = [hour_lo, hour_hi) — lower inclusive, upper exclusive.
    e.g. 17:00 sharp -> 1.2 (z 17-19), nie 1.6 (z 15-17).
    Raises TypeError jesli dt_utc nie ma tzinfo (fail fast, parytet z get_time_bucket).
    """
    if dt_utc.tzinfo is None:
        raise TypeError("get_traffic_multiplier requires aware datetime (got naive)")
    local = dt_utc.astimezone(WARSAW)
    wd = local.weekday()
    if wd <= 4:
        table = V326_OSRM_TRAFFIC_TABLE["weekday"]
    elif wd == 5:
        table = V326_OSRM_TRAFFIC_TABLE["saturday"]
    else:
        table = V326_OSRM_TRAFFIC_TABLE["sunday"]
    h = local.hour
    for lo, hi, mult in table:
        if lo <= h < hi:
            return mult
    return 1.0  # safety net (np. h=24 nie powinno wystapic)


# ─── BUG-D Distance-bin traffic boost (V3.28+) ──────────────────────────
# TomTom sample 2026-05-26 (n=8 segmentów peak weekday Wt 16-20) ujawnił że
# `V326_OSRM_TRAFFIC_TABLE` flat per-hour znacznie zaniża krótkie segmenty
# centrum (lots of lights/intersections) i lekko zawyża długie międzydzielnicowe.
#
# Empirical TomTom/OSRM_ff ratio per distance bin:
#   <2 km centrum: avg 2.3× (range 2.1-2.5×, n=4 short urban)
#   2-5 km mixed:  avg 1.5× (range 1.02-2.35×, n=4 — dominują 1.0-1.3× spoza centrum)
#   >5 km long:    avg 1.15× (range 1.02-1.33×, n=3 long inter-district)
#
# Strategy: ADDITIVE boost relative to base hour multiplier, applied ONLY in
# peak hours (base > 1.0). Off-peak (base=1.0) zostaje 1.0 niezależnie od
# distance. Floor at 1.0 (nigdy NIE zmniejszamy poniżej OSRM ff).
#
# Sample run validation (Pn-Pt 16-17, base=1.3):
#   short 1.5km: 1.3 + 1.0 = 2.3 ✓ (sample avg 2.3)
#   medium 4 km: 1.3 + 0.4 = 1.7 ✓ (sample range 1.5-2.5, midpoint OK)
#   long 6 km:   max(1.0, 1.3 - 0.15) = 1.15 ✓ (sample 1.15 long-haul)
#
# Doc: eod_drafts/2026-05-26/measurements.md sekcja "BUG D"
#
# Format: (distance_max_km_exclusive, additive_boost)
V326_OSRM_DISTANCE_BIN_BOOST_PEAK = (
    (2.0, 1.0),        # <2 km: +1.0 (urban centrum, lots of stops/lights)
    (5.0, 0.4),        # 2-5 km: +0.4 (mixed)
    (float("inf"), -0.15),  # >=5 km: -0.15 (long inter-district, OSRM ff bliski real)
)

# Default OFF — shadow-first walidacja, Adrian ACK przed LIVE flip
ENABLE_V326_DISTANCE_BIN_TRAFFIC_BOOST = os.environ.get(
    "ENABLE_V326_DISTANCE_BIN_TRAFFIC_BOOST", "0") == "1"


def get_distance_bin_v2(distance_km: float) -> str:
    """V3.28+ BUG-D: klasyfikacja distance bin dla per-distance multiplier.

    Returns 'short' (<2km), 'medium' (2-5km), 'long' (>=5km), albo 'none' gdy
    distance_km is None (legacy path, no distance correction available).
    """
    if distance_km is None:
        return "none"
    if distance_km < 2.0:
        return "short"
    if distance_km < 5.0:
        return "medium"
    return "long"


def get_traffic_multiplier_v2(dt_utc: datetime, distance_km: float = None) -> float:
    """V3.28+ BUG-D: per-distance-bin traffic multiplier z hour base.

    Backward compatible: jeśli `distance_km is None` lub off-peak (base=1.0)
    zwraca dokładnie `get_traffic_multiplier(dt_utc)` — identyczne zachowanie.

    W peak hours (base > 1.0) dodaje additive boost z V326_OSRM_DISTANCE_BIN_BOOST_PEAK
    według distance bucket. Floor at 1.0 (boost ujemny NIE zmniejsza poniżej free-flow).

    NIE zmienia get_traffic_multiplier() — to nowa funkcja dla shadow recording
    i (po Adrian ACK) live integration w osrm_client._apply_traffic_multiplier.

    Args:
        dt_utc: aware UTC datetime
        distance_km: OSRM result distance (None = no distance correction, legacy path)

    Returns:
        float multiplier, floored at 1.0
    """
    base = get_traffic_multiplier(dt_utc)
    if distance_km is None or base <= 1.0:
        return base
    for max_km, boost in V326_OSRM_DISTANCE_BIN_BOOST_PEAK:
        if distance_km < max_km:
            return max(1.0, base + boost)
    return base


# ═══════════════════════════════════════════════════════════════════
# F2.1 Decision Engine 3.0 — EXTENSIONS to Bartek Gold Standard
# Dodane 2026-04-15. R1-R5 (F1.9) pozostają bez zmian.
# R6-R9 to nowe reguły — hard rejects + soft penalties.
# ═══════════════════════════════════════════════════════════════════

# ─── R6 (H1): BAG_TIME termiczny — czas od T_KUR (gotowość w kuchni) do T_DOR ───
# Kalibracja empiryczna z 743 delivered orderów (11-15.04.2026):
# p50=15.1, p75=23.0, p90=30.9, p95=35.6, p99=44.3, max=80.5 min.
# 35 min = p95 → hard cap obcina ogon 5.7% bez wpływu na mediana/p75.
# 30 min = p90 → soft zone 30-35 łapie dodatkowe 5.9% orderów penalty.
BAG_TIME_HARD_MAX_MIN = 35
BAG_TIME_SOFT_MIN = 30
BAG_TIME_PRE_WARNING_MIN = 30    # sla_tracker alert Telegramu (krok #6)
BAG_TIME_SOFT_PENALTY_PER_MIN = 8
# Fix #6 477285 (2026-05-31): danger zone — progresywna kara near-limit R6.
# Strefa 30-32 = normalny bufor (R-BUFFER-OK) → liniowa -8/min bez zmian. Strefa
# 32-35 = near-limit ryzykowna → EKSTRA -16/min (łącznie -24/min). Powód: 33-35 min
# dostawa to jeden korek od zimnego jedzenia / SLA breach >35; ryzyko nieliniowe →
# kara nieliniowa. Diagnoza 477285 (Kołłątaja 33.9/35 wciśnięte): -31.2 (liniowa) za
# słabe by Aleksander przegrał z Andreiem (29.1 min <30, 0 kary). Z fix #6: 33.9 →
# ~-61.6 → Andrei (lepszy dowóz) wygrywa. env-tunable, default ON, legacy w cieniu.
ENABLE_R6_DANGER_ZONE_PENALTY = os.environ.get(
    "ENABLE_R6_DANGER_ZONE_PENALTY", "1") == "1"   # ON od 2026-05-31 (Adrian: live)
BAG_TIME_DANGER_MIN = float(os.environ.get("BAG_TIME_DANGER_MIN", "32.0"))
BAG_TIME_DANGER_PENALTY_PER_MIN = float(os.environ.get("BAG_TIME_DANGER_PENALTY_PER_MIN", "16.0"))

# E7 2026-06-17 (robustness/higiena) — cap kary R6-soft, by astronomiczne wartości z
# zombie-pickup (r6_max_bag_time liczone z dni → kara ~ -240000) nie zatruwały score/LGBM.
# Próg -2000 dobrany replayem flipów (eod_drafts/2026-06-17/r6cap_flip_replay.py): 0 zmian
# selekcji na 7d (kandydat z karą < -2000 i tak jest zdominowany). UWAGA: -300/-500 z notatki
# kalibracyjnej dałyby 20/8 flipów — odrzucone pomiarem. Flaga kanon = flags.json (default OFF).
ENABLE_R6_SOFT_PEN_CAP = True  # L0.1 2026-07-01: wyrównana do steady-state (json=True od tygodni); była False = mina const≠json. flags.json = kanon (C.flag)
R6_SOFT_PEN_CAP_FLOOR = float(os.environ.get("R6_SOFT_PEN_CAP_FLOOR", "-2000.0"))

# V3.28 ANCHOR FIX 2026-05-10 — Adrian doktryna: PROPOSE quality threshold.
# Gdy best.score < MIN_PROPOSE_SCORE → verdict=KOORD reason=all_candidates_low_score.
# Background: 2026-05-10 472189 PROPOSE Andrei score=-50 mimo Mateusz Bro alt -1047
# (best of bad). Operator override 89% — system proponuje gdy realnie wszyscy źli.
# Próg -100 = "tylko ekstremalne sub-optymalne (jak -1047) lecą do KOORD".
# Lekko ujemne propozycje (peak day rescue) zostają PROPOSE.
# SCALE-01: env-default = -100.0 (bez zmiany); kanon override = flags.json
# (FLAGS_JSON_NUMERIC_OVERRIDES). Konsumenci czytają przez load_flags().get(...).
MIN_PROPOSE_SCORE = float(os.environ.get("MIN_PROPOSE_SCORE", "-100.0"))

# ─── (dawna R7 long-haul: reguła USUNIĘTA L6.C 2026-07-04 — martwy REJECT za
# sentinelem LONG_HAUL_DISTANCE_KM=99 od F2.1c; stała skasowana. PEAK_HOURS zostają:
# karmią żywą telemetrię r7_in_peak/r7_warsaw_hour w feasibility_v2.) ───
LONG_HAUL_PEAK_HOURS_START = 14   # inclusive
LONG_HAUL_PEAK_HOURS_END = 17     # inclusive

# ─── R8 (S2): Pickup span czasowy (uzupełnia R5 przestrzenny 1.8km) ───
# Placeholder — shadow_decisions nie loguje T_KUR per zlecenie w bagu.
# Kalibracja post-deploy po 5-7 dniach obserwacji reject rate.
PICKUP_SPAN_HARD_BUNDLE2_MIN = 15
PICKUP_SPAN_HARD_BUNDLE3_MIN = 30
PICKUP_SPAN_SOFT_START_MIN = 7
PICKUP_SPAN_SOFT_PENALTY_PER_MIN = 3

# ─── R9 (S1 + S3): Stopover tax + restaurant wait penalty ───
# Soft-only (scoring penalties), zero hard reject.
STOPOVER_PENALTY_MIN = 4          # realny overhead parkowanie + domofon
STOPOVER_SCORE_PER_STOP = 8       # 4 min × 2 pts/min
RESTAURANT_WAIT_SOFT_MIN = 5      # tolerancja czekania pod restauracją
RESTAURANT_WAIT_PENALTY_PER_MIN = 6

# === WAVE ROUTING (F2.1c) ===
# Rynek Kościuszki — punkt referencyjny powrotu kuriera po fali dostawczej
RYNEK_KOSCUSZKI = (53.1324, 23.1489)
POST_WAVE_RETURN_BUFFER_MIN = 5   # bufor min po ostatniej dostawie → kurier na Rynku
POST_WAVE_FREE_MAX_MIN = 15       # max free_at_min dla post_wave fast bonus
POST_WAVE_BONUS_FAST = 15.0       # free_at_min ≤ 20 min
POST_WAVE_BONUS_SLOW = 8.0        # free_at_min ≤ 30 min

# ─── Auto-approve (feature-flagged, betonowo OFF do F2.1c) ───
# AUTO_APPROVE_MIN_GAP — minimalna przewaga score best vs second_best_feasible
# wymagana do auto-approve. Placeholder 10, kalibracja w F2.1c
# po 2-3 tyg danych (n_shadow ≥ 1500 dla stabilnej dystrybucji gap).
# Gdy tylko 1 feasible kandydat → gap = inf (auto-approve OK).
# Score distribution z 578 shadow: p90=106.7, p95=111.9, p99=135.3.
# Threshold 130 ≈ p98-p99 — top ~1-2% decyzji, conservative.
AUTO_APPROVE_THRESHOLD = 130
AUTO_APPROVE_MIN_GAP = 10
AUTO_APPROVE_ENABLED = False
# ⚠ DEPRECATED (2026-06-13, AUTON-01): powyższe AUTO_APPROVE_* to martwe
# placeholdery F2.1c (zero call-site od zawsze) — zostawione tylko dla
# legacy testu test_decision_engine_f21. Realna ścieżka auto-assign =
# auto_assign_gate (telemetria) + auto_assign_executor (egzekucja za
# ENABLE_AUTO_ASSIGN). Projekt: eod_drafts/2026-06-13/AUTON01_DESIGN.md.

# ─── AUTON-01 (2026-06-13): bramka + bezpieczniki auto-assign ───
# Flaga decyzyjna: kanon flags.json (ETAP4), fallback module-level OFF.
# Telemetria would_auto_assign/auto_block_reasons liczona ZAWSZE niezależnie
# od flagi (lekcja #186) — flaga gate'uje wyłącznie egzekutor.
ENABLE_AUTO_ASSIGN = False
# AUTON-02 (2026-06-30): profil bramki. True = strict AUTON-01 (wymaga
# klasyfikator=AUTO i margin≥próg). False = plaster D (szerszy, bramkowany
# fizyką: pool_feasible≥2 + informed pos + twarde reguły; bez wymogu AUTO/margin).
# Czytane przez auto_assign_gate z przekazanego dict `flags` (fallback ta stała).
# Flip = 3 flagi RAZEM (te 2 + AUTO_ASSIGN_MIN_POOL_FEASIBLE→2), sprzężone (C3).
AUTO_ASSIGN_REQUIRE_CLASSIFIER_AUTO = True
AUTO_ASSIGN_REQUIRE_MARGIN = True
# ─── Paczki Faza 2 Etap 3 (2026-06-29): natywny tor paczek w ŻYWYM orders_state ───
# Merger `parcel_lane_merge` wpisuje aktywne paczki z bialystok.nadajesz.pl do orders_state
# przez state_machine.upsert_order (LOCK_EX, bez korupcji). Watcher pomija source=parcel
# BEZWARUNKOWO (nie zależy od flagi). Flaga gate'uje TYLKO merger: OFF = zero paczek w
# orders_state (guard wtedy nigdy nie odpala). Hot-flip z flags.json. Twin GASTRO nietknięty.
ENABLE_PARCEL_LANE_LIVE = False
# Bramka jakościowa (auto_assign_gate; nadpisywalne hot z flags.json):
AUTO_ASSIGN_MIN_POOL_FEASIBLE = 3        # mniej feasible = scarcity → człowiek
# Bartek 2.0 §4.1: breach 13,5-18% przy score>90 (inflacja R4) — korelacja
# score↔wynik się odwraca w górnym zakresie. Sufit do re-oceny w E7 po capie R4.
AUTO_ASSIGN_SCORE_DISTRUST_CEILING = 90.0
# Bezpieczniki egzekutora (auto_assign_executor, stanowe — NIE wchodzą do
# would_auto_assign, patrz AUTON01_DESIGN.md §3/§5):
AUTO_ASSIGN_MAX_PER_HOUR = 6             # rate-cap wykonań
AUTO_ASSIGN_OVERRIDE_COOLDOWN_MIN = 60.0 # cisza po PANEL_OVERRIDE na kurierze

# ─── Anomaly detection: prep-variance ZREALIZOWANE jako FAIL-04 (2026-06-06,
# shadow LIVE, konsument dispatch_pipeline._detect_prep_variance_anomaly).
# Odnoga kurierska (courier_recent_delay/CIRCUIT_BREAK/ANOMALY_DETECTION) —
# USUNIĘTA 2026-06-11 (higiena, werdykt CB-01 06.08: A2 reliability soft-score
# JEST tym mechanizmem; martwe definicje bez call-site od F2.1c).
RESTAURANT_PREP_VARIANCE_HARD_MIN = 15

# FAIL-04 (2026-06-06): shadow-first detekcja "slepej wiary w prep panelu".
# Gdy wysoko-wariancyjna restauracja (restaurant_meta prep_variance_high) ma
# zadeklarowany czas_odbioru znaczaco nizszy od empirycznej mediany variancji
# (gap >= RESTAURANT_PREP_VARIANCE_HARD_MIN) -> zapis sygnalu na result.
# SHADOW: czysta telemetria, NIE zmienia pickup_ready_at (landmine F1.8g!),
# NIE kara/reject/verdict. Default OFF; flip live = osobna decyzja po danych.
# Konsument flagi: dispatch_pipeline._detect_and_set_prep_variance_anomaly.
ENABLE_PREP_VARIANCE_ANOMALY_SHADOW = False

# ============================================================
# F2.2 Sprint C Feature Flags (2026-04-18)
# Per F2.2_SECTION_4_ARCHITECTURE_SPEC sekcja 6 (Rollback Plan).
# All default False at deploy. Production flip sequential C2 → C3 → C5 → C6 → C7.
# Rollback: set flag False + restart (trivial).
# ============================================================

# C2: per-order delivery_time <= 35 min hard gate
# Currently False → existing hard gates (R6 BAG_TIME_HARD_MAX etc.) remain primary.
# When True → check_per_order_35min_rule rejects bundle if any order predicted > 35 min.
USE_PER_ORDER_GATE = False

# C2 shadow mode: log diff between current vs new-gate behavior even when flag False.
# Provides data for flip decision ("ile bundli C2 would reject gdyby flag=True").
# Zero impact na current flow — observational logging only.
ENABLE_C2_SHADOW_LOG = True

# Future flags (C3, C5-C7), default False at deploy:
DEPRECATE_LEGACY_HARD_GATES = False  # C3: R1/R5/R6/R7/R8 → soft penalties
ENABLE_WAVE_SCORING = False           # C5: wave_scoring.py module

# C5 shadow mode: observational diff logging regardless of ENABLE_WAVE_SCORING.
# When True, wave_scoring computes adjustment and emits C5_SHADOW_DIFF event
# to dispatch_state/c5_shadow_log.jsonl when adjustment magnitude > threshold.
ENABLE_C5_SHADOW_LOG = True

ENABLE_MID_TRIP_PICKUP = False        # C6: state_machine rewake for overlap
ENABLE_PENDING_QUEUE_VIEW = False     # C7: dispatch_pipeline signature change

# Rolling late-binding Faza 0 (2026-05-18): pula pending — obserwacja.
# True → shadow_dispatcher zasila pending_pool, dispatch-pending-pool.timer
# robi reconciliation. Faza 0 = czysta obserwacja, zero wpływu na dispatch.
ENABLE_PENDING_POOL = os.environ.get("ENABLE_PENDING_POOL", "0") == "1"
FREEZE_LEAD_MIN = 15                  # zlecenie zamrażane FREEZE_LEAD_MIN przed odbiorem

# ============================================================
# Telegram Transparency OPCJA A flags (2026-04-19)
# Redesign propozycji — Adrian chce rozumieć CZEMU ten kurier i
# JAKĄ TRASĘ wykona. L2 label "blisko: X" był mylący (sugeruje
# że kurier odbiera z X, a to bundling do istniejącej fali).
# ============================================================
ENABLE_TRANSPARENCY_ROUTE = True       # Route section (pickupy then drops) w propozycji
ENABLE_TRANSPARENCY_REASON = True      # Natural-language reason line (czemu ten kurier)
# (L0.1 D.4 2026-07-01: ENABLE_TRANSPARENCY_SCORING usunięta — martwa-True, 0 konsumentów w kodzie od dawna; historia w CLAUDE.md/TECH_DEBT snapshotach)

# V3.17 (2026-04-19): per-stop timeline w Telegram proposal.
# Replaces "pickups | drops" 2-line format with chronologically sorted events:
#   HH:MM {emoji} {action} {restaurant|address}
# New order highlighted via 👉 emoji + [NOWY] prefix.
# Fallback: plan.pickup_at + predicted_delivered_at empty → old format.
# Env kill-switch: ENABLE_TIMELINE_FORMAT=0 → revert to old format without restart.
import os as _os_v317
ENABLE_TIMELINE_FORMAT = _os_v317.environ.get("ENABLE_TIMELINE_FORMAT", "1") == "1"

# ============================================================
# City-aware geocoding flag (2026-04-19)
# Bugfix: wcześniej geocoder hardcodował hint_city='Białystok' i cachował
# adresy Kleosin/Ignatki/Wasilków pod fałszywymi coords Białegostoku.
# True (default) = geocoder wymaga city explicit, fail loud gdy brak.
# False = legacy kill-switch (fallback do Białystok default) — rollback on regression.
# ============================================================
CITY_AWARE_GEOCODING = True

# ============================================================
# A3 — Geocode cache TTL + drift detection (audit STATE_OWNERSHIP F6 2026-05-07)
# Cache (geocode_cache.json + restaurant_coords.json) ma `cached_at` od 2026-04
# ALE TTL nigdy nie był enforce'owany — entries żyją wiecznie. Po remoncie ulicy
# / zmianie numeracji / reorganizacji dzielnicy stale coords pozostają w cache
# bez sygnału. Plus combo z MP-#13 OSRM degraded mode: stale geocode + cache hit
# = silent stale propozycja.
# ENABLE_GEOCODE_CACHE_TTL=True (default) → entries >30d trigger re-geocode.
# ENABLE_GEOCODE_CACHE_DRIFT_ALERT=False (default OFF, opt-in) → gdy re-geocode
# zwraca coords różniące się o >200m od cache, log WARN (Telegram alert opt-in
# w przyszłości via flags.json runtime check).
# ============================================================
GEOCODE_CACHE_TTL_DAYS = float(os.environ.get("GEOCODE_CACHE_TTL_DAYS", "30"))
GEOCODE_CACHE_DRIFT_ALERT_M = float(os.environ.get("GEOCODE_CACHE_DRIFT_ALERT_M", "200"))
ENABLE_GEOCODE_CACHE_TTL = os.environ.get("ENABLE_GEOCODE_CACHE_TTL", "1") == "1"
ENABLE_GEOCODE_CACHE_DRIFT_ALERT = os.environ.get("ENABLE_GEOCODE_CACHE_DRIFT_ALERT", "0") == "1"
# NEGATYWNY cache geokodowania (P1-latencja, 2026-06-26): adres odrzucony przez weryfikację
# (verify_reject = zła dzielnica/APPROXIMATE/partial) NIE trafiał do cache → przy KAŻDYM
# kolejnym wystąpieniu świeże zapytanie do Google + weryfikacja → znów reject (zmierzone ~460
# jałowych odrzuceń/3h, +~200ms median na dotkniętych decyzjach). Neg-cache zapamiętuje „ten
# adres się nie geokoduje" z krótkim TTL → kolejne lookupy zwracają None bez sieci. TYLKO
# deterministyczny verify_reject — NIE bbox_reject (bywa transient poison Google, Nominatim go
# odzyskuje) ani transient google/osrm fail. Kill-switch + TTL hot przez flags.json. TTL krótki
# bo po fixie dzielnicy (P3b) adres może stać się ważny → wpis sam wygaśnie.
ENABLE_GEOCODE_NEGATIVE_CACHE = os.environ.get("ENABLE_GEOCODE_NEGATIVE_CACHE", "1") == "1"
GEOCODE_NEG_CACHE_TTL_SEC = float(os.environ.get("GEOCODE_NEG_CACHE_TTL_SEC", "21600"))  # 6h

# ============================================================
# Geocode bbox guard (2026-05-30) — odrzuca out-of-bbox wyniki Google PRZED
# zapisem do cache. Diagnoza (zadanie #4 geo-poison): "Witosa 26/16" rozwiązało
# się na "Witosa 26, Klepacze" (52.505,22.694 ~70km) zamiast Białystok →
# max_bag_time=10003min → KOORD. Cache spuchł do 33/6197 out-of-bbox (12 jawnie
# z "białystok"), w tym sentinel Google [51.9194,19.1451] (środek Polski) dla
# zbyt ogólnych/parser-artefakt zapytań. Brak guardu w momencie geokodu → zła
# trafia do cache i zostaje. Guard: result poza bbox → return None (NIE cache),
# log WARN GEOCODE_BBOX_REJECT. Caller dostaje None → istniejące defense gates
# (no_pickup_geocode / KOORD). Bbox = Białystok + ~28km (Kleosin, Wasilków,
# Supraśl, Choroszcz, Łapy). Multi-tenant Warsaw: bbox env-overridable per deploy.
# Kill-switch: ENABLE_GEOCODE_BBOX_GUARD=0.
# ============================================================
ENABLE_GEOCODE_BBOX_GUARD = os.environ.get("ENABLE_GEOCODE_BBOX_GUARD", "1") == "1"
GEOCODE_BBOX_LAT_MIN = float(os.environ.get("GEOCODE_BBOX_LAT_MIN", "52.85"))
GEOCODE_BBOX_LAT_MAX = float(os.environ.get("GEOCODE_BBOX_LAT_MAX", "53.35"))
GEOCODE_BBOX_LON_MIN = float(os.environ.get("GEOCODE_BBOX_LON_MIN", "22.85"))
GEOCODE_BBOX_LON_MAX = float(os.environ.get("GEOCODE_BBOX_LON_MAX", "23.45"))

# ============================================================
# FAZA 2 — Geocode verification layer ("nie ma prawa się pomylić", 2026-06-08).
# Bbox to filtr trucizny (czy w mieście), NIE check poprawności wewnątrz miasta.
# Warstwa weryfikacji łączy 3 sygnały: (2) Google location_type/partial_match,
# (3) zgodność dzielnicy wyniku z dzielnicą adresu, (4) cross-check z drugim
# źródłem (Nominatim). ENFORCE domyślnie OFF (shadow: liczy+loguje co BY odrzucił,
# zero zmiany zachowania) — po dniu obserwacji shadow flip ENFORCE=1.
# ============================================================
ENABLE_GEOCODE_VERIFICATION = os.environ.get("ENABLE_GEOCODE_VERIFICATION", "1") == "1"          # compute + log
ENABLE_GEOCODE_VERIFICATION_ENFORCE = os.environ.get("ENABLE_GEOCODE_VERIFICATION_ENFORCE", "0") == "1"  # reject low-conf
# (2) location_type, które uznajemy za niepewne (środek geometryczny / przybliżenie)
GEOCODE_LOW_CONFIDENCE_LOCATION_TYPES = frozenset({"APPROXIMATE", "GEOMETRIC_CENTER"})
# (3) próg niezgodności dzielnicy: wynik w innej, NIE-sąsiedniej dzielnicy = mismatch
ENABLE_GEOCODE_DISTRICT_CHECK = os.environ.get("ENABLE_GEOCODE_DISTRICT_CHECK", "1") == "1"
# (4) cross-source Nominatim
ENABLE_GEOCODE_CROSS_SOURCE = os.environ.get("ENABLE_GEOCODE_CROSS_SOURCE", "1") == "1"
GEOCODE_CROSS_SOURCE_MAX_DISAGREE_M = float(os.environ.get("GEOCODE_CROSS_SOURCE_MAX_DISAGREE_M", "400"))
GEOCODE_NOMINATIM_TIMEOUT_S = float(os.environ.get("GEOCODE_NOMINATIM_TIMEOUT_S", "3.0"))
GEOCODE_NOMINATIM_USER_AGENT = os.environ.get(
    "GEOCODE_NOMINATIM_USER_AGENT", "ziomek-dispatch/1.0 (ac@nadajesz.pl)")
# Fallback OSM/Nominatim gdy Google zawiódł (None) LUB zwrócił out-of-bbox poison.
# Google nie ma w indeksie części białostockich ulic (np. „Proroka Eliasza",
# „Poniatowskiego" w Pieczurkach) → dopasowuje miejscowość „Białystok" 22-540 na
# południu z pewnością ROOFTOP. Nominatim (bounded do bboxu obsługi) trafia je 100%.
# Ściśle addytywne: odpala się tylko gdy Google już zawiódł → najgorszy przypadek =
# dzisiejszy reject. Hot-reload via flags.json. Default OFF (replay-walidacja przed flip).
ENABLE_GEOCODE_NOMINATIM_FALLBACK = os.environ.get("ENABLE_GEOCODE_NOMINATIM_FALLBACK", "0") == "1"

# Pin-memory fallback (2026-07-09, Adrian: "zrób mechanizm który to wyłapuje i
# naprawia" — case Składowa 12/kurier Adrian Cit, geokoder verify_reject na dobry
# adres, mapa koordynatora "nie widziała" dostawy). `address_pin_aggregator`
# (timer co 5 min) od dawna uczy się realnych pinezek z GPS kurierów
# (address_pins.json/restaurant_pins.json) ale NIKT ich nie konsumował decyzyjnie.
# Gdy oficjalny geocode() nie da rady (neg_cache/verify_reject/bbox_reject/total
# fail) — ZANIM odda None, sprawdź czy adres ma już nauczoną pinezkę z realnych
# dostaw i użyj jej. SHADOW_ONLY: liczy+loguje co by zwrócił (audit_log +
# shadow_decisions-style), ale realnie oddaje coords dopiero gdy MAIN=True.
# Ściśle addytywne: odpala się TYLKO gdy oficjalna ścieżka już zwróciłaby None →
# najgorszy przypadek = dzisiejszy brak coords (nie może pogorszyć działającego
# geokodu). Domyślnie OFF/shadow — flip po replay-walidacji (protokół Ziomka).
ENABLE_GEOCODE_PIN_MEMORY_FALLBACK = os.environ.get("ENABLE_GEOCODE_PIN_MEMORY_FALLBACK", "0") == "1"
GEOCODE_PIN_MEMORY_MIN_INLIERS = int(os.environ.get("GEOCODE_PIN_MEMORY_MIN_INLIERS", "1"))

# FAZA 2 #1 — firmowe konto: reject+flag zamiast podstawiania centrali gdy
# parser/geocode padnie (zła-ale-wiarygodna pozycja gorsza niż głośna porażka).
# Domyślnie ON (dyrektywa Adriana 2026-06-08). ⚠ ODWRACA decyzję 07.05 (fallback
# do centrali) — firmowe ordery z nieudanym geocode idą do KOORD zamiast centrum.
# Rollback: ENABLE_FIRMOWE_REJECT_ON_GEOCODE_FAIL=0.
ENABLE_FIRMOWE_REJECT_ON_GEOCODE_FAIL = os.environ.get("ENABLE_FIRMOWE_REJECT_ON_GEOCODE_FAIL", "1") == "1"

# ============================================================
# Strict courier ID space flag (2026-04-19)
# Bugfix: build_fleet_snapshot dodawał keys z kurier_piny.json (4-digit PIN-y)
# jako osobnych kurierów obok prawdziwych courier_id z kurier_ids.json.
# Duplikaty (np. Michał Ro jako cid=518 AND cid=5333-PIN) → phantom z pustym
# bagiem → no_gps fallback → fałszywa propozycja "wolnego" kuriera.
# True (default) = PIN służy TYLKO jako name-lookup fallback, nie źródło cid.
# False = legacy kill-switch (PIN jako cid). env override: STRICT_COURIER_ID_SPACE=0.
# ============================================================
import os as _os
STRICT_COURIER_ID_SPACE = _os.environ.get("STRICT_COURIER_ID_SPACE", "1") == "1"

# ============================================================
# Strict bag reconciliation flag (2026-04-19 V3.14)
# Bugfix: panel_watcher ma lag 15-90 min w detect delivered orders
# (MAX_RECONCILE_PER_CYCLE=25/tick + FIFO closed_ids queue). W tym oknie
# pipeline ufa orders_state.json ze status=assigned dla orderów już delivered
# w panelu → scoring z phantom bagiem (propozycja #467117 @ 13:26:28 miała
# bag_context={467015,467053,467070}, wszystkie delivered 15-30 min po).
# True (default) = active_bag filter z TTL — assigned >90min bez picked_up
# wykluczony z bagu. False = legacy bez TTL. env: STRICT_BAG_RECONCILIATION=0.
# BAG_STALE_THRESHOLD_MIN tunable (env: BAG_STALE_THRESHOLD_MIN=60 etc.).
# ============================================================
STRICT_BAG_RECONCILIATION = _os.environ.get("STRICT_BAG_RECONCILIATION", "1") == "1"
try:
    BAG_STALE_THRESHOLD_MIN = int(_os.environ.get("BAG_STALE_THRESHOLD_MIN", "90"))
except (ValueError, TypeError):
    BAG_STALE_THRESHOLD_MIN = 90

# ============================================================
# Panel packs fallback flag (2026-04-19 V3.15)
# Bugfix: panel_client.parse_panel_html zwraca courier_packs {nick:[oid]}
# jako ground-truth mapping z panelu HTML (każdy tick, 20s). Było to
# DEAD DATA (zwracane ale nigdzie nie konsumowane). panel_watcher.reconcile
# ma lag 15-90s+ dla emit COURIER_ASSIGNED w burst scenarios — pipeline
# widzi kurierów z aktywnymi bagami jako wolnych (propozycja #467164
# Michał Li @ 14:30 UTC: bag=0 w pipeline mimo 4 orderów w panelu).
# True (default) = panel_watcher konsumuje courier_packs jako fallback
# trigger fetch_details + emit COURIER_ASSIGNED dla missing assignments.
# False = legacy (courier_packs dead data). env: ENABLE_PANEL_PACKS_FALLBACK=0.
# PACKS_FALLBACK_MAX_PER_CYCLE tunable żeby nie przeciążyć panel API.
# ============================================================
ENABLE_PANEL_PACKS_FALLBACK = _os.environ.get("ENABLE_PANEL_PACKS_FALLBACK", "1") == "1"
try:
    PACKS_FALLBACK_MAX_PER_CYCLE = int(_os.environ.get("PACKS_FALLBACK_MAX_PER_CYCLE", "10"))
except (ValueError, TypeError):
    PACKS_FALLBACK_MAX_PER_CYCLE = 10

# ============================================================
# No-GPS empty bag demotion flag (2026-04-19 V3.16)
# Bugfix: Mateusz O (cid=413, no_gps, bag=0) często jest BEST w pipeline
# (score ~53, bez żadnych penalty), podczas gdy bag-kurierzy z aktywnym
# bagiem dostają -100 do -300 przez r8_soft_pen + r9_wait_pen + r9_stopover.
# Koordynator override'uje 19.6% (18/92 propozycji w 1h45min) — konsekwentnie
# wybierając kurierów z aktywnymi bagami (po drodze / bundling).
# scoring.py nie ma penalty dla pos_source=no_gps → synthetic BIALYSTOK_CENTER
# + max(15,prep) travel dają no_gps kurierowi baseline ~80 punktów.
# True (default) = demote no_gps+empty poniżej GPS/bag kandydatów (post-scoring,
# przed final pick). Guard: jeśli wszyscy są no_gps empty → nie demote.
# False = legacy behavior. env: ENABLE_NO_GPS_EMPTY_DEMOTE=0.
# ============================================================
ENABLE_NO_GPS_EMPTY_DEMOTE = _os.environ.get("ENABLE_NO_GPS_EMPTY_DEMOTE", "1") == "1"

# NO_GPS RÓWNE TRAKTOWANIE (Adrian 2026-06-22): "bez GPS musi być traktowany
# na równi z GPS, żadnych kar". Kurier bezczynny bez GPS jest najpewniej WOLNY
# i w zwartym Białymstoku dojedzie pod każdą restaurację ~15 min (już ma neutralne
# km=śr.floty + ETA=max(15,prep) z F1.7). Flaga ON → no_gps NIE jest demote'owany
# (_demote_blind_empty go pomija) → konkuruje czystym score jak GPS. pre_shift/none
# bez zmian (genuinie nie-na-zmianie/nieznane). Default OFF (gated; flip po cieniu).
# flags.json hot-reload. env: ENABLE_NO_GPS_EQUAL_TREATMENT=1.
ENABLE_NO_GPS_EQUAL_TREATMENT = _os.environ.get("ENABLE_NO_GPS_EQUAL_TREATMENT", "0") == "1"
# DOKOŃCZENIE równego traktowania (Adrian 2026-06-24): no_gps I pre_shift konkurują po score
# także w bucketach selekcji (tiering + best_effort) i nie są demotowane. Kanon = flags.json
# (hot-reload), stała = fallback. Pomiar przed flipem: 359 flipów/tydz (tools/nogps_preshift_bucket_replay.py).
ENABLE_EQUAL_TREATMENT_BUCKET = _os.environ.get("ENABLE_EQUAL_TREATMENT_BUCKET", "0") == "1"

# R-DECLARED TRIPWIRE (L7.1, audyt 2026-06-30 root R7-I-E): reguła biznesowa
# R-DECLARED-TIME (HARD) — `czas_kuriera >= czas_odbioru_timestamp` (deklarowany
# przyjazd kuriera NIE wcześniej niż deklarowany czas odbioru z restauracji) —
# dziś NIE ma żadnego runtime-inwariantu (egzekucja tylko pośrednio przez SOFT
# R27; zmiana R27 cicho ją złamie). Ta flaga włącza JEDEN obserwacyjny tripwire
# w chokepoincie zapisu (state_machine.upsert_order) — fail-loud LOG + append
# JSONL, NIGDY reject/zmiana decyzji (zgodne z doktryną always-propose). OFF =
# zero kodu ścieżki (bajt-parytet decyzji). Kanon = flags.json (hot-reload);
# stała = env-default fallback. Flip = lekki ACK Adriana. Detal: state_machine
# ._r_declared_tripwire + tools/entropy INV-COH-R-DECLARED.
ENABLE_R_DECLARED_TRIPWIRE = _os.environ.get("ENABLE_R_DECLARED_TRIPWIRE", "0") == "1"
# Tolerancja tripwire (min): |czas_kuriera - czas_odbioru| < próg → NIE naruszenie
# (HH:MM czas_kuriera jest truncowany do minuty; gastro re-stemplowany
# czas_odbioru_timestamp ląduje kilka-sekund/minut po deklarowanej minucie →
# sub-progowy szum, nie realny breach reguły). Default 0.0 = ścisła nierówność
# reguły `czas_kuriera >= czas_odbioru_timestamp`. Env-tunable bez zmiany kodu.
R_DECLARED_TRIPWIRE_TOLERANCE_MIN = float(
    _os.environ.get("R_DECLARED_TRIPWIRE_TOLERANCE_MIN", "0.0"))

# ============================================================
# V3.18 unified bag reality check flags (2026-04-19)
# Master switch dla CourierBagState + FleetContext projection.
# Adresuje 3 klasy bugów poprzez pojedynczą spójną reprezentację stanu bagu:
#   Bug 1 (drop<pickup) — route_simulator respektuje pickup time per bag order
#   Bug 2 (overload)    — scoring penalty gdy bag > fleet_avg + threshold
#   Bug 3 (false wolny) — telegram czyta CourierBagState.is_free (single source)
# Bug 4 (empty no_gps top-1) reserved do osobnej sesji z plan-replay audit.
# Kill-switch: ENABLE_UNIFIED_BAG_STATE=0 ENV disable wszystkich 4 na raz.
# ============================================================
ENABLE_UNIFIED_BAG_STATE = _os.environ.get("ENABLE_UNIFIED_BAG_STATE", "1") == "1"
ENABLE_DROP_TIME_CONSTRAINT = _os.environ.get("ENABLE_DROP_TIME_CONSTRAINT", "1") == "1"
ENABLE_FLEET_OVERLOAD_PENALTY = _os.environ.get("ENABLE_FLEET_OVERLOAD_PENALTY", "1") == "1"

# V3.28 Fix 6 (incident 03.05.2026): mass fail fallback heuristic.
# Gdy >=50% kurierów crash w _v327_pool (OR-Tools mass fail) → trigger
# simple proximity+tier heuristic. NIE używa OR-Tools więc nie crashuje.
# Default True (safety net). Env override: ENABLE_V328_MASS_FAIL_FALLBACK=0
# disable (mass fail wraca do silent NO_PROPOSE).
ENABLE_V328_MASS_FAIL_FALLBACK = _os.environ.get("ENABLE_V328_MASS_FAIL_FALLBACK", "1") == "1"
V328_MASS_FAIL_RATIO_THRESHOLD = float(_os.environ.get("V328_MASS_FAIL_RATIO_THRESHOLD", "0.5"))
# L2.2 (2026-07-02, most K5): catch-all _v328_eval_safe ROZRÓŻNIA przyczynę
# fail-u kuriera (data_poison = fail-loud strażnika coords [sentinel (0,0)/None/
# bbox] vs real_bug = nieoczekiwany wyjątek; infeasible NIE jest wyjątkiem —
# legalny brak kandydata = result None). Klasyfikacja+telemetria = unconditional
# (czysta obserwowalność, wzór coord_poison_* L2.1). Flaga gate'uje WYŁĄCZNIE
# zbiorczy operator-alert Telegram (nie spam per-zdarzenie: okno+próg+realert,
# wzór worker-stuck shadow_dispatcher). Default OFF = intencja steady-state do
# czasu flipa za ACK; KANON stanu = flags.json.
ENABLE_V328_POISON_ALERT = False
V328_POISON_ALERT_WINDOW_MIN = float(_os.environ.get("V328_POISON_ALERT_WINDOW_MIN", "30"))
V328_POISON_ALERT_MIN_EVENTS = int(_os.environ.get("V328_POISON_ALERT_MIN_EVENTS", "5"))
V328_POISON_ALERT_REALERT_SEC = float(_os.environ.get("V328_POISON_ALERT_REALERT_SEC", "1800"))
# Z-11 (audyt 2026-06-10): heurystyka mass-fail omija CAŁĄ feasibility (jedyny
# guard = bag-cap) — kurier PO KOŃCU ZMIANY mógł wygrać w degraded mode (łamie
# R-SCHEDULE-AWARE / V325 PICKUP_POST_SHIFT). Guard: skip gdy
# shift_end < now + naive_eta (haversine / fallback speed). Brak shift_end →
# NIE skipuj (degraded mode, fail-open spójny z duchem FAIL-12; grafik mógł paść
# razem z OR-Tools). Env default ON (lustrzane do bezwarunkowego bag-cap guard);
# hot-reload kill-switch: flags.json ENABLE_V328_HEURISTIC_SHIFT_END_GUARD=false.
ENABLE_V328_HEURISTIC_SHIFT_END_GUARD = _os.environ.get(
    "ENABLE_V328_HEURISTIC_SHIFT_END_GUARD", "1") == "1"
# (L0.1 D.4 2026-07-01: ENABLE_PANEL_IS_FREE_AUTHORITATIVE usunięta — martwa-ON, 0 konsumentów; klucz nie istnieje w flags.json)
# === BUNDLE-06 Faza 1 / BUNDLE-02 (Front D audytu 03.06, 2026-06-12) ===
# bundle_fit: scalony sygnał wartości worka (kierunek nowego dropu / marginalny
# koszt świeżości / rozstrzał gotowości odbiorów) — liczony ZAWSZE w
# dispatch_pipeline (lekcja #186), do score TYLKO za flagą (ETAP4 kanon,
# flags.json=false; kalibracja wag = E7 at#131 17.06). Reaktywacja flagi
# V3.18 usuniętej 2026-06-11 w BUNDLE-08 („zero call-site") — tym razem
# Z konsumentem. Wagi nadpisywalne hot z flags.json (NUMERIC_OVERRIDES).
ENABLE_BUNDLE_VALUE_SCORING = _os.environ.get(
    "ENABLE_BUNDLE_VALUE_SCORING", "0") == "1"
BUNDLE_FIT_W_COS = float(_os.environ.get("BUNDLE_FIT_W_COS", "12.0"))
BUNDLE_FIT_THERMAL_FREE_MIN = float(_os.environ.get(
    "BUNDLE_FIT_THERMAL_FREE_MIN", "25.0"))
BUNDLE_FIT_THERMAL_PER_MIN = float(_os.environ.get(
    "BUNDLE_FIT_THERMAL_PER_MIN", "1.5"))
BUNDLE_FIT_SPAN_FREE_MIN = float(_os.environ.get(
    "BUNDLE_FIT_SPAN_FREE_MIN", "8.0"))
BUNDLE_FIT_SPAN_PER_MIN = float(_os.environ.get(
    "BUNDLE_FIT_SPAN_PER_MIN", "1.0"))
# BUNDLE-03 (2026-06-12): FIX_C zeruje bonusy, których najgorsze worki
# (przeciw-kierunkowe, różne restauracje) i tak NIE MAJĄ → no-op dla case'u,
# do którego był pisany (#469834). Addytywna kara: −PEN_PER_KM·(spread−cap),
# a przy cos<COS_TRIGGER (przeciwny kierunek) −PEN_PER_KM·spread (pełny —
# zły kierunek czyni KAŻDY rozrzut kosztownym). Liczona zawsze; aplikacja
# za flagą (E7 kalibruje/decyduje).
ENABLE_FIX_C_ADDITIVE_PENALTY = _os.environ.get(
    "ENABLE_FIX_C_ADDITIVE_PENALTY", "0") == "1"
FIX_C_ADDITIVE_PEN_PER_KM = float(_os.environ.get(
    "FIX_C_ADDITIVE_PEN_PER_KM", "3.0"))
FIX_C_ADDITIVE_COS_TRIGGER = float(_os.environ.get(
    "FIX_C_ADDITIVE_COS_TRIGGER", "-0.3"))

# ============================================================
# V3.19a picked_up drop floor (2026-04-19)
# Symetryczne rozszerzenie V3.18 ENABLE_DROP_TIME_CONSTRAINT na case gdy
# order.status == "picked_up". Adresuje R1 (29.1% propozycji post-V3.18):
# courier_resolver ustawia cs.pos = order.delivery_coords dla picked_up bag
# ("last_picked_up_delivery") → _simulate_sequence liczy leg_min ≈ 0 →
# predicted_drop ≈ now+1s → free_at_min ≈ 1 (structurally absurd).
# Floor: predicted_drop >= picked_up_at + osrm(pickup→drop) + DWELL_DROPOFF_MIN.
# True (default) = apply floor. env: ENABLE_PICKED_UP_DROP_FLOOR=0.
# ============================================================
ENABLE_PICKED_UP_DROP_FLOOR = _os.environ.get("ENABLE_PICKED_UP_DROP_FLOOR", "1") == "1"

# ============================================================
# V3.19b saved plans persistence (2026-04-19)
# plan_manager.py persists per-courier TSP plan w courier_plans.json po każdym
# COURIER_ASSIGNED. Advance/remove_stops na DELIVERED/RETURNED_TO_POOL.
# Read integration w scoring path → V3.19c (risk-deferred). Zero wpływu na
# ścieżkę scoring tej sesji — persistence to sidecar + fundament V3.19c.
# True (default) = panel_watcher konsumuje plan_manager save/advance/remove.
# False = legacy (plan_manager dead code). env: ENABLE_SAVED_PLANS=0.
# ============================================================
ENABLE_SAVED_PLANS = _os.environ.get("ENABLE_SAVED_PLANS", "1") == "1"

# ============================================================
# V3.19c sub B — read integration shadow-log (2026-04-19)
# Obserwacyjne: dispatch_pipeline po każdym feasibility_v2 plan-compute loguje
# diff między fresh TSP sequence vs saved_plan sequence (dla bag orderów).
# Read integration sam w sobie (use saved jako base) → V3.19d flip po N dni
# shadow. Tutaj tylko observation log do /dispatch_state/v319c_read_shadow_log.jsonl.
# True (default) = log shadow diffs. False = no write.
# env: ENABLE_SAVED_PLANS_READ_SHADOW=0.
# ============================================================
ENABLE_SAVED_PLANS_READ_SHADOW = _os.environ.get(
    "ENABLE_SAVED_PLANS_READ_SHADOW", "1") == "1"

# V3.19d (2026-04-19): read integration — flipped to True after impl Commits A+B.
# dispatch_pipeline.assess_order extract bag base_sequence z plan_manager.load_plan
# i przekazuje do simulate_bag_route_v2 jako base_sequence → sticky sequence path.
# Triple guard w caller (flag+bag+match). Env kill-switch =0 = no-op fresh TSP.
ENABLE_SAVED_PLANS_READ = _os.environ.get("ENABLE_SAVED_PLANS_READ", "1") == "1"

# ============================================================
# V3.20 — R2 ghost detection via panel_packs reverse lookup (2026-04-19)
# Rozszerzenie V3.15 packs_fallback: V3.15 wykrywa MISSING COURIER_ASSIGNED,
# V3.20 wykrywa MISSING COURIER_DELIVERED. orders_state.status=picked_up/assigned
# ale oid NIE w packs[nick] z tego samego panel tick → kurier go oddał/delivered.
# fetch_details potwierdza status=7 zanim emit COURIER_DELIVERED.
# Adresuje R2 (12.7% propozycji) ghost delivered orders z 6min panel_watcher lag.
# True (default) = ghost detect live; False = legacy (ghost widoczny 6min).
# env: ENABLE_V320_PACKS_GHOST_DETECT=0.
# Guards:
#  - GHOST_DETECT_AGE_MIN: minimalny wiek assignment żeby uniknąć race
#    z świeżym COURIER_ASSIGNED przed pierwszym HTML parse.
#  - GHOST_DETECT_MAX_PER_CYCLE: cap fetch_details calls per tick.
# ============================================================
ENABLE_V320_PACKS_GHOST_DETECT = _os.environ.get(
    "ENABLE_V320_PACKS_GHOST_DETECT", "1") == "1"
try:
    GHOST_DETECT_AGE_MIN = int(_os.environ.get("GHOST_DETECT_AGE_MIN", "5"))
except (ValueError, TypeError):
    GHOST_DETECT_AGE_MIN = 5
try:
    GHOST_DETECT_MAX_PER_CYCLE = int(
        _os.environ.get("GHOST_DETECT_MAX_PER_CYCLE", "5"))
except (ValueError, TypeError):
    GHOST_DETECT_MAX_PER_CYCLE = 5

# ============================================================
# V3.19e — pre-pickup bag semantics (2026-04-20)
# ============================================================
# Bag items z status="assigned" (pickup jeszcze nie nastąpił, kurier w drodze
# do restauracji lub czeka pod nią) były traktowane przez route_simulator_v2
# jako już picked_up → tylko drop-node. Efekt: fantazja plan-u dla wave #2
# assigned orderów (pickup_at brak w planie, fantasy predicted_delivered_at).
#
# V3.19e: dla bag items z status="assigned", simulator dodaje pickup-node
# przed delivery-node. Pickup-before-delivery jako hard constraint (analog
# new_order need_pickup).
#
# Default False → observational shadow mode. Flip na True po ≥5 dniach
# stable shadow + weryfikacji match rate + PANEL_OVERRIDE trend maleje.
# Env kill-switch: ENABLE_V319E_PRE_PICKUP_BAG=1.
# ============================================================
ENABLE_V319E_PRE_PICKUP_BAG = _os.environ.get(
    "ENABLE_V319E_PRE_PICKUP_BAG", "1") == "1"

# Overload threshold: bag > fleet_avg + this → score penalty
try:
    OVERLOAD_THRESHOLD_BAGS = int(_os.environ.get("OVERLOAD_THRESHOLD_BAGS", "2"))
except (ValueError, TypeError):
    OVERLOAD_THRESHOLD_BAGS = 2
try:
    OVERLOAD_PENALTY = float(_os.environ.get("OVERLOAD_PENALTY", "-20.0"))
except (ValueError, TypeError):
    OVERLOAD_PENALTY = -20.0

# ============================================================
# V3.19f — czas_kuriera propagation (2026-04-20)
# ============================================================
# Panel HTML kolumna "Kurier czas" (raw top-level czas_kuriera, HH:MM)
# deklaruje commitment pickup time kuriera. Przed V3.19f panel_client
# odrzucał to pole (fetch_order_details zwracał tylko raw.zlecenie).
# Pipeline używał pickup_at_warsaw (=created+prep) jako surogatu — różnice
# 20-30 min dla czasówek z "przedłużeniem" (panel +15min button).
#
# V3.19f Step 2+3: parse + persist ZAWSZE (niezależnie od flagi) — dane
# w orders_state.czas_kuriera_warsaw + czas_kuriera_hhmm dla shadow
# observability. Pipeline consumer pod flagą (dark launch pattern).
#
# Default False → parse+persist aktywne, dispatch używa pickup_at_warsaw
# jak pre-V3.19f. Flip na True po ≥5 dniach stable shadow + walidacji
# offline że czas_kuriera_warsaw dane są sensowne.
# Env kill-switch: ENABLE_CZAS_KURIERA_PROPAGATION=1.
# ============================================================
ENABLE_CZAS_KURIERA_PROPAGATION = _os.environ.get(
    "ENABLE_CZAS_KURIERA_PROPAGATION", "1") == "1"

# ============================================================
# V3.19h BUG-4 — tier × pora bag cap matrix (2026-04-20)
# ============================================================
# Ground truth od właściciela: tier-specific orders-per-wave caps zależne
# od pory (peak/normal/off_peak). Obecny code używa stałego BAG_TIME_HARD_MAX
# + bag_size bez tier awareness. V3.19g dataset 40k waves 6-mo potwierdził
# matrix (10/12 cells match actual p90).
#
# SOFT penalty (nie hard reject) — progressive scaling:
#   1 order over cap → -20
#   2 orders over cap → -60 (3x)
#   3 orders over cap → -120 (6x)
#   ≥4 orders over cap → -9999 (effective hard reject przez penalty size)
#
# Per-cid override (Gabriel cap=4) loaded z courier_tiers.json.
# HARD BAG_TIME > 35 min (R6) pozostaje — to jest SINGLE hard constraint.
# ============================================================
BUG4_TIER_CAP_MATRIX = {
    'gold':  {'off_peak': 4, 'normal': 4, 'peak': 6},
    'std+':  {'off_peak': 3, 'normal': 4, 'peak': 5},
    'std':   {'off_peak': 2, 'normal': 3, 'peak': 4},
    'slow':  {'off_peak': 2, 'normal': 2, 'peak': 3},
}

ENABLE_V319H_BUG4_TIER_CAP_MATRIX = _os.environ.get(
    "ENABLE_V319H_BUG4_TIER_CAP_MATRIX", "1") == "1"

# === TWARDY cap worka per tier (Adrian 2026-06-18) ===
# Powyżej = patologia (B_load). re-solve 16.05: breachy 53->17 (36 usuniętych u przeładowanego).
# To HARD reject w feasibility (NIE soft jak BUG4 wyżej). flag-gated hot-reload (flags.json), default OFF.
# Empiria (breach<20%, 8931 dowozów): gold/std+ ~6, std ~5, slow/new ~4. Tier nieznany -> default 6
# (łapie tylko patologię 7+, brak fałszywych odrzutów na braku danych). Egzekwowane parami z
# przelewem-na-falę + auto-przedłużeniem (zamiast KOORD) — patrz HANDOFF_hardcap_eta_2026-06-18.
HARD_TIER_BAG_CAP = {"gold": 6, "std+": 6, "std": 5, "slow": 4, "new": 4}
HARD_TIER_BAG_CAP_DEFAULT = 6
ENABLE_HARD_TIER_BAG_CAP = _os.environ.get("ENABLE_HARD_TIER_BAG_CAP", "0") == "1"


def bug4_pora_now(now_utc):
    """V3.19h: Warsaw-TZ peak detection. Returns 'peak'|'normal'|'off_peak'."""
    from datetime import timezone as _tz
    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=_tz.utc)
    w = now_utc.astimezone(WARSAW)
    h = w.hour
    if 11 <= h < 14 or 17 <= h < 20:
        return 'peak'
    if h < 10 or h >= 22:
        return 'off_peak'
    return 'normal'


def bug4_soft_penalty(violation):
    """V3.19h: progressive scaling per Q1 owner 2026-04-20.
      violation 0 → 0
      violation 1 → -20
      violation 2 → -60 (x3)
      violation 3 → -120 (x6)
      violation ≥4 → -9999 (effective hard reject)
    """
    if violation is None or violation <= 0:
        return 0.0
    if violation == 1:
        return -20.0
    if violation == 2:
        return -60.0
    if violation == 3:
        return -120.0
    return -9999.0


# ============================================================
# V3.19h BUG-1 — SR bundle × drop_proximity_factor (2026-04-21)
# ============================================================
# Gold tier pattern: SR (same-restaurant) bundle TYLKO gdy drops blisko siebie.
# Standard tier bierze SR ślepo (Kacper S avg drop_spread 10km). Fix: mnożnik
# na existing bonus_l1 (same-rest bundle bonus) × drop_proximity_factor.
#
# Drop zone = osiedle Białegostoku (28 official z info.bialystok.pl) albo
# outside-city zone (Choroszcz/Wasilków/Kleosin/Ignatki).
# Adjacency ground truth z ACK właściciela 2026-04-21.
#
# Factor:
#   1.0 gdy obydwa drops w tej samej strefie
#   0.5 gdy w sąsiadujących strefach (adjacency map)
#   0.0 gdy odległe albo Unknown (defensive)
#
# Default False — shadow observational.
# Env kill-switch: ENABLE_V319H_BUG1_DROP_PROXIMITY_FACTOR=1
# ============================================================
from dispatch_v2.districts_data import (
    BIALYSTOK_DISTRICTS,
    BIALYSTOK_OUTSIDE_CITY_ZONES,
)

# Final adjacency per ACK właściciela 2026-04-21 (post-review).
BIALYSTOK_DISTRICT_ADJACENCY = {
    # Śródmieście
    'Centrum':        {'Przydworcowe', 'Piaski', 'Bojary', 'Mickiewicza',
                       'Piasta II', 'Sienkiewicza', 'Dojlidy'},
    'Bojary':         {'Centrum', 'Piasta I', 'Piasta II', 'Sienkiewicza',
                       'Mickiewicza', 'Skorupy'},
    'Piaski':         {'Centrum', 'Mickiewicza', 'Przydworcowe'},
    # GEO-05 fix 2026-06-13: usunięto 'Dojlidy Górne' (centroidy 4.38 km, Dojlidy
    # leży MIĘDZY nimi — Mick→Dojlidy 1.71 km, Dojlidy→Dojlidy Górne 2.75 km;
    # link omijał Dojlidy = fałszywe sąsiedztwo. Symetrycznie zdjęte z 'Dojlidy
    # Górne'. Dowód: eod_drafts/2026-06-13/geo05_adjacency.md).
    'Mickiewicza':    {'Centrum', 'Dojlidy', 'Kawaleryjskie', 'Piaski',
                       'Piasta II', 'Skorupy', 'Bojary'},
    'Sienkiewicza':   {'Wygoda', 'Bojary', 'Centrum', 'Białostoczek',
                       'Wasilków', 'Jaroszówka'},
    # E/SE Dojlidy kierunek
    'Dojlidy':        {'Skorupy', 'Mickiewicza', 'Dojlidy Górne', 'Centrum'},
    'Dojlidy Górne':  {'Dojlidy'},  # GEO-05 fix 2026-06-13: usunięto 'Mickiewicza' (patrz Mickiewicza wyżej)
    'Skorupy':        {'Dojlidy', 'Mickiewicza', 'Piasta I', 'Piasta II', 'Bojary'},
    'Piasta I':       {'Bojary', 'Piasta II', 'Skorupy', 'Wygoda', 'Jaroszówka'},
    'Piasta II':      {'Bojary', 'Mickiewicza', 'Centrum', 'Piasta I', 'Skorupy',
                       'Wygoda', 'Jaroszówka'},
    # S/SW Kawaleryjskie kierunek
    'Kawaleryjskie':  {'Nowe Miasto', 'Mickiewicza', 'Bema',
                       'Kleosin', 'Ignatki-osiedle'},
    'Nowe Miasto':    {'Kawaleryjskie', 'Bema', 'Kleosin', 'Ignatki-osiedle'},
    'Przydworcowe':   {'Centrum', 'Bema', 'Piaski'},
    'Bema':           {'Przydworcowe', 'Kawaleryjskie', 'Nowe Miasto',
                       'Starosielce', 'Leśna Dolina', 'Zielone Wzgórza',
                       'Słoneczny Stok'},
    # N/NE Jaroszówka/Wygoda/Białostoczek
    'Wygoda':         {'Jaroszówka', 'Sienkiewicza', 'Piasta I', 'Piasta II'},
    'Jaroszówka':     {'Wygoda', 'Wasilków', 'Sienkiewicza',
                       'Piasta I', 'Piasta II'},
    'Białostoczek':   {'Sienkiewicza', 'Antoniuk', 'Zawady',
                       'Dziesięciny I', 'Dziesięciny II'},
    # N/NW Antoniuk/Bacieczki cluster
    'Antoniuk':       {'Młodych', 'Bacieczki', 'Wysoki Stoczek',
                       'Białostoczek', 'Leśna Dolina', 'Zielone Wzgórza'},
    'Młodych':        {'Antoniuk', 'Słoneczny Stok', 'Wysoki Stoczek',
                       'Leśna Dolina', 'Bacieczki', 'Zielone Wzgórza'},
    'Bacieczki':      {'Zawady', 'Antoniuk', 'Leśna Dolina', 'Wysoki Stoczek',
                       'Choroszcz', 'Młodych', 'Zielone Wzgórza', 'Słoneczny Stok'},
    'Wysoki Stoczek': {'Antoniuk', 'Młodych', 'Bacieczki',
                       'Dziesięciny I', 'Dziesięciny II', 'Zawady'},
    'Zawady':         {'Bacieczki', 'Białostoczek', 'Wysoki Stoczek',
                       'Dziesięciny I', 'Dziesięciny II'},
    'Dziesięciny I':  {'Dziesięciny II', 'Białostoczek', 'Wysoki Stoczek', 'Zawady'},
    'Dziesięciny II': {'Dziesięciny I', 'Białostoczek', 'Wysoki Stoczek', 'Zawady'},
    # W Starosielce/Zielone Wzgórza cluster
    'Starosielce':    {'Zielone Wzgórza', 'Leśna Dolina', 'Słoneczny Stok', 'Bema'},
    'Leśna Dolina':   {'Starosielce', 'Bacieczki', 'Słoneczny Stok',
                       'Młodych', 'Antoniuk', 'Zielone Wzgórza', 'Bema'},
    'Słoneczny Stok': {'Leśna Dolina', 'Młodych', 'Starosielce',
                       'Zielone Wzgórza', 'Bacieczki', 'Bema'},
    'Zielone Wzgórza': {'Starosielce', 'Leśna Dolina', 'Bacieczki',
                        'Słoneczny Stok', 'Młodych', 'Antoniuk', 'Bema'},
    # Outside-city operational zones
    'Choroszcz':        {'Bacieczki'},
    'Wasilków':         {'Jaroszówka', 'Sienkiewicza'},
    'Kleosin':          {'Ignatki-osiedle', 'Nowe Miasto', 'Kawaleryjskie'},
    'Ignatki-osiedle':  {'Kleosin', 'Nowe Miasto', 'Kawaleryjskie'},
}

ENABLE_V319H_BUG1_DROP_PROXIMITY_FACTOR = _os.environ.get(
    "ENABLE_V319H_BUG1_DROP_PROXIMITY_FACTOR", "1") == "1"


# V3.27 Bug Z Step D (2026-04-25 wieczór): street name aliases (canonicalization).
# Real-world adresy mają różne formy tej samej ulicy:
#   "M. Curie-Skłodowskiej", "Marii Curie-Skłodowskiej", "Skłodowskiej",
#   "Curie-Skłodowskiej" — wszystkie → canonical "skłodowskiej-curie marii"
# (canonical form matches BIALYSTOK_DISTRICTS street keys).
# Aliases applied AFTER prefix stripping (ul./al./gen.) + lower-cased.
# Format: {input_lc (street_part_only, no number) → canonical_lc}.
# Extend incrementally w V3.28 ticket per discovery z shadow log.
V327_STREET_ALIASES = {
    # Marii Skłodowskiej-Curie variants
    "skłodowskiej": "skłodowskiej-curie marii",
    "skłodowskiej-curie": "skłodowskiej-curie marii",
    "curie-skłodowskiej": "skłodowskiej-curie marii",
    "marii curie-skłodowskiej": "skłodowskiej-curie marii",
    "marii skłodowskiej-curie": "skłodowskiej-curie marii",
    "m. skłodowskiej-curie": "skłodowskiej-curie marii",
    "m. curie-skłodowskiej": "skłodowskiej-curie marii",
    # Władysława Bełzy variants
    "bełzy": "władysława bełzy",
    "wł. bełzy": "władysława bełzy",
    "władysława bełzy": "władysława bełzy",  # identity (gdy already canonical)
    # Feliksa Filipowicza variants (Białystok-side; Kleosin handled przez city-aware)
    "filipowicza": "feliksa filipowicza",
    "f. filipowicza": "feliksa filipowicza",
    "feliksa filipowicza": "feliksa filipowicza",  # identity
}


def _v327_normalize_street_for_matching(addr_lc):
    """V3.27 Bug Z Step D: apply street aliases pre-matching.

    Args:
        addr_lc: lowercased address (post prefix-strip), may include number suffix
                 (e.g. "skłodowskiej 13/15", "m. curie-skłodowskiej 5").

    Returns:
        addr_lc z canonical street name jeśli match w V327_STREET_ALIASES,
        else addr_lc unchanged.

    Logic:
        1. Identify pure street part (everything before first digit-led token).
        2. Strip trailing whitespace/punctuation z pure street.
        3. Lookup w V327_STREET_ALIASES → canonical.
        4. Concat canonical + numeric suffix.
    """
    if not addr_lc:
        return addr_lc
    # Find first digit position
    digit_idx = None
    for i, ch in enumerate(addr_lc):
        if ch.isdigit():
            digit_idx = i
            break
    if digit_idx is None:
        addr_pure = addr_lc.strip().rstrip(",.")
        suffix = ""
    else:
        # Find last whitespace before digit
        space_before_digit = addr_lc.rfind(" ", 0, digit_idx)
        if space_before_digit < 0:
            addr_pure = addr_lc[:digit_idx].strip().rstrip(",.")
            suffix = addr_lc[digit_idx:]
        else:
            addr_pure = addr_lc[:space_before_digit].strip().rstrip(",.")
            suffix = addr_lc[space_before_digit:]
    if addr_pure in V327_STREET_ALIASES:
        canonical = V327_STREET_ALIASES[addr_pure]
        return canonical + suffix
    return addr_lc


def drop_zone_from_address(addr, city=None):
    """V3.19h BUG-1: address + city → district name.

    Outside-city wykrywane z `city` field (miejscowość_docelowa z CSV).
    Białystok: match po ulicy w BIALYSTOK_DISTRICTS (prefix/substring match).
    Fallback: 'Unknown' gdy brak confident match.

    V3.27 Bug Z Step D: street aliases applied post prefix-strip.
    """
    if city and isinstance(city, str):
        city_norm = city.strip()
        city_lc = city_norm.lower()
        if city_norm and city_lc != 'białystok':
            # Outside-city — detect explicit zones
            for zone in BIALYSTOK_OUTSIDE_CITY_ZONES:
                if zone.lower() in city_lc:
                    return zone
            return 'Unknown'  # inna nieznana miejscowość
    # Białystok (or empty city) — match po ulicy
    if not addr or not isinstance(addr, str):
        return 'Unknown'
    addr_lc = addr.lower().strip()
    # Strip leading prefix (ul./al./pl./gen./św./ks./ulica/aleja).
    # V3.26 R-06 completion: extended list dla Polish name convention variants.
    for prefix in (
        'ul. ', 'ulica ', 'al. ', 'aleja ', 'plac ', 'pl. ',
        'gen. ', 'generała ', 'św. ', 'świętej ', 'świętego ',
        'ks. ', 'księdza ', 'prof. ', 'dr. ',
    ):
        if addr_lc.startswith(prefix):
            addr_lc = addr_lc[len(prefix):]
            break
    # V3.27 Bug Z Step D: apply street aliases post prefix-strip.
    # Real-world variants ("M. Curie-Skłodowskiej", "Skłodowskiej") → canonical
    # ("skłodowskiej-curie marii") matching BIALYSTOK_DISTRICTS street keys.
    addr_lc = _v327_normalize_street_for_matching(addr_lc)
    # Token-based matching: districts mają street jako "imię nazwisko" albo
    # "nazwisko" (np. "waszyngtona jerzego", "sienkiewicza henryka", "lipowa").
    # Dataset adresy mają "nazwisko number" albo "ulica number" (np. "Waszyngtona 24",
    # "Sienkiewicza 12", "Lipowa 14/13"). Strategia:
    #  1. Exact prefix match (street matches pełna fraza albo prefix).
    #  2. Token prefix: pierwszy token z street (np. "waszyngtona") jako prefix dla addr.
    #  3. Substring match jako fallback.
    # Dodatkowo: longer street match wygrywa (preferred specificity, np.
    # "branickiego jana klemensa" wygrywa nad "branickich" dla "Branickiego J.K. 5").
    addr_first_token = addr_lc.split(None, 1)[0] if addr_lc else ''

    # V3.26 R-06 completion: extract meaningful content tokens (alphabetic or hyphenated
    # Polish names, len >=3, NIE zawierające cyfr). Used for bidirectional multi-token match.
    def _is_content_token(t):
        # Remove trailing punctuation
        t = t.rstrip(',.')
        if len(t) < 3:
            return False
        # Must be alpha or hyphenated (no digits)
        if any(c.isdigit() for c in t):
            return False
        return True
    addr_content_tokens = [t.rstrip(',.') for t in addr_lc.split() if _is_content_token(t)][:3]

    best_match_zone = None
    best_match_len = 0

    for zone_name, zone_data in BIALYSTOK_DISTRICTS.items():
        streets = zone_data['streets']
        for street in streets:
            slen = len(street)
            if slen < 3:
                continue
            # 1) Exact or prefix match (full street w adresie)
            matched = False
            if addr_lc == street:
                matched = True
            elif addr_lc.startswith(street + ' ') or addr_lc.startswith(street + ','):
                matched = True
            # 2) Token-prefix: district street zaczyna się od addr_first_token
            #    (np. street "waszyngtona jerzego", addr token "waszyngtona")
            elif addr_first_token and street.startswith(addr_first_token + ' '):
                # Only accept gdy addr_first_token jest sensowny (≥4 znaki)
                if len(addr_first_token) >= 4:
                    matched = True
            # 3) Substring match (defensive, zeby np. ul. długa z dodatkami łapała)
            elif slen >= 6 and street in addr_lc:
                matched = True

            # 4) V3.26 R-06 completion: BIDIRECTIONAL multi-token match (FALLBACK).
            # Applied AFTER (1)/(2)/(3) — catches Polish name order inversion:
            # streets store "Nazwisko Imię" ("sienkiewicza henryka") but addresses
            # often "Imię Nazwisko" ("henryka sienkiewicza 5").
            # Rules:
            #   - addr has 1 content token: match gdy token ∈ street_tokens AND len ≥ 5
            #     (Kaczorowskiego alone → 'prezydenta ryszarda kaczorowskiego')
            #   - addr has ≥2 content tokens: FIRST TWO both must be in street_tokens
            #     (Marii Skłodowskiej-Curie → 'skłodowskiej-curie marii' — both match;
            #      'marii' alone NOT matches 'św. maksymiliana marii kolbego' w innym district)
            if not matched and len(addr_content_tokens) >= 1:
                _street_tokens = set(street.split())
                if len(addr_content_tokens) == 1:
                    tk = addr_content_tokens[0]
                    if len(tk) >= 5 and tk in _street_tokens:
                        matched = True
                else:
                    t0, t1 = addr_content_tokens[0], addr_content_tokens[1]
                    if t0 in _street_tokens and t1 in _street_tokens:
                        matched = True

            if matched and slen > best_match_len:
                best_match_zone = zone_name
                best_match_len = slen

    return best_match_zone if best_match_zone else 'Unknown'


def drop_proximity_factor(zone1, zone2):
    """V3.19h BUG-1: factor (0.0/0.5/1.0) między 2 zones.

      1.0 — same zone (drops w tym samym osiedlu)
      0.5 — adjacent zones (sąsiadujące per ACK właściciela)
      0.0 — distant albo Unknown (defensive)
    """
    if not zone1 or not zone2:
        return 0.0
    if zone1 == 'Unknown' or zone2 == 'Unknown':
        return 0.0
    if zone1 == zone2:
        return 1.0
    neighbors = BIALYSTOK_DISTRICT_ADJACENCY.get(zone1, set())
    if zone2 in neighbors:
        return 0.5
    return 0.0


# ============================================================
# V3.19h BUG-2 — wave continuation bonus (2026-04-21)
# ============================================================
# Gold tier pattern (confirmed V3.19h): interleave 33% within-wave vs Std 20.5%.
# Gold kurierzy pickupują wave #2 PRZED ukończeniem wave #1 (interleave
# pickup after drop). Bartek z bag=5 planuje falę #2 zanim skończy falę #1.
#
# Scoring bonus gdy nowy order pickup_at pasuje do projected free_at
# (last bag drop predicted_at):
#   gap_min = (pickup_new - free_at_dt).total_seconds() / 60
#   gap < 0    → +30 (anticipation, Bartek pattern)
#   0 ≤ gap ≤ 10 → linear decay 30 → 0
#   gap > 10 min → 0 (normal cadence, nie wave continuation)
#
# Source of truth dla free_at_dt: plan.predicted_delivered_at[last_bag_oid]
# (spójny dla sticky V3.19d / V3.19e pre_pickup_bag / fresh TSP — potwierdzone
# w grep survey Step 4.1).
#
# Default False — shadow observational.
# Env kill-switch: ENABLE_V319H_BUG2_WAVE_CONTINUATION=1
# ============================================================
BUG2_WAVE_CONTINUATION_BONUS = 30.0
BUG2_INTERLEAVE_GATE_MIN = 10.0

# ============================================================
# C2 (audyt 2026-05-28) — decay/cap dla SILNIE ujemnego gap (stara fala).
# bug2_wave_continuation_bonus dawał FLAT +30 dla KAŻDEGO gap<0 — kurier którego
# free_at jest 2 min po pickup_new (mild anticipation = realna kontynuacja fali) i
# 40 min po (stara fala: jedzenie gotowe dawno, stale pickup) dostawali identyczne
# +30. Fix: plateau pełnego bonusu dla |gap| ≤ FULL_BONUS_MIN (mild anticipation =
# tight wave chaining, Bartek pattern), potem liniowy decay do FLOOR_FRAC*BONUS przez
# DECAY_SPAN_MIN. Strona DODATNIA (gap≥0, kurier czeka) NIETKNIĘTA. Default OFF — shadow.
# Env: ENABLE_C2_NEG_GAP_DECAY=1 / C2_NEG_GAP_FULL_BONUS_MIN / C2_NEG_GAP_DECAY_SPAN_MIN
#      / C2_NEG_GAP_FLOOR_FRAC
# ============================================================
ENABLE_C2_NEG_GAP_DECAY = _os.environ.get("ENABLE_C2_NEG_GAP_DECAY", "0") == "1"
C2_NEG_GAP_FULL_BONUS_MIN = float(
    _os.environ.get("C2_NEG_GAP_FULL_BONUS_MIN", "10.0"))
C2_NEG_GAP_DECAY_SPAN_MIN = float(
    _os.environ.get("C2_NEG_GAP_DECAY_SPAN_MIN", "20.0"))
C2_NEG_GAP_FLOOR_FRAC = float(
    _os.environ.get("C2_NEG_GAP_FLOOR_FRAC", "0.0"))

# FIX 1 (2026-05-22): licz interleave gap z REALNEGO zaplanowanego odbioru TSP
# (plan.pickup_at[new]) zamiast z gotowości jedzenia. Elastyk gotowy wcześnie →
# ready-time daje gap ~zawsze ujemny → phantom +30 dla DRUGIEJ FALI (kurier fizycznie
# odbiera dużo później). Diagnoza 475235 Raj→Hallera: Michał K real odbiór 12:56 vs
# free 12:46 = +10 (nowa fala), a ready-time dawał -6.5 → +30. Default OFF (shadow-first).
# Env kill-switch: ENABLE_BUG2_GAP_FROM_PLAN=1
ENABLE_BUG2_GAP_FROM_PLAN = _os.environ.get(
    "ENABLE_BUG2_GAP_FROM_PLAN", "0") == "1"

ENABLE_V319H_BUG2_WAVE_CONTINUATION = _os.environ.get(
    "ENABLE_V319H_BUG2_WAVE_CONTINUATION", "1") == "1"


# ============================================================
# V3.19g1 — czas_kuriera change detection via panel_watcher.
# Detects |Δt| ≥ 3 min in czas_kuriera_warsaw for already-assigned
# orders; emits CZAS_KURIERA_UPDATED event to state_machine.
# Default False — shadow observational.
# Env kill-switch: ENABLE_V319G_CK_DETECTION=1
# ============================================================
ENABLE_V319G_CK_DETECTION = _os.environ.get(
    "ENABLE_V319G_CK_DETECTION", "1") == "1"
V319G_CK_DELTA_THRESHOLD_MIN = 3.0

# ============================================================
# PICKUP_TIME_UPDATED — detekcja zmiany pickup_at_warsaw (czas odbioru).
# Root cause oid 474577 (2026-05-19): pickup_at_warsaw zapisywany RAZ w
# NEW_ORDER (event_id deterministyczny _NEW_ORDER_first), nigdy nie
# odświeżany dla zleceń status=planned. Czasówka spędza większość życia
# jako planned w buckecie Koordynatora — gdy koordynator zmieni czas
# odbioru na życzenie restauracji, Ziomek czyta stary pickup_at_warsaw
# (czasowka_scheduler._minutes_to_pickup → błędny FORCE_ASSIGN spam).
# V3.19g1 czas_kuriera detection pokrywała tylko assigned/picked_up i
# tylko pole czas_kuriera (osobne pole panelu niż czas_odbioru_timestamp).
# Ta detekcja diffuje pickup_at_warsaw świeżo z panelu co tick dla
# czasówek planned + wszystkich assigned/picked_up.
# Env kill-switch: ENABLE_PICKUP_TIME_DETECTION=0
# ============================================================
ENABLE_PICKUP_TIME_DETECTION = _os.environ.get(
    "ENABLE_PICKUP_TIME_DETECTION", "1") == "1"
PICKUP_TIME_DELTA_THRESHOLD_MIN = 3.0


# ============================================================
# Telegram free-text assign control — Adrian 2026-04-21 disabled per
# lunch-peak incident: Bartek commentary "K414 będzie wolny za 14min,
# ale później..." was parsed as assign command → gastro_assign error
# "Nie znaleziono kuriera K414". Free-text remains LOGGED as learning
# signal (action=OPERATOR_COMMENT), but no real assign call triggered.
# Inline buttons (ASSIGN / INNY / KOORD callbacks) unaffected.
# Default False per Adrian — flip to True to restore old behavior.
# Env kill-switch: ENABLE_TELEGRAM_FREETEXT_ASSIGN=1
# ============================================================
ENABLE_TELEGRAM_FREETEXT_ASSIGN = _os.environ.get(
    "ENABLE_TELEGRAM_FREETEXT_ASSIGN", "0") == "1"


def bug2_wave_continuation_bonus(gap_min):
    """V3.19h BUG-2: compute bonus from interleave gap_min.

    gap_min: float (pickup_new - free_at_dt) w minutach. None → 0.
      < 0 → anticipation (pickup przed last drop):
            C2 OFF (default) → full bonus FLAT (legacy)
            C2 ON → plateau full bonus dla |gap|≤FULL_BONUS_MIN, potem decay
                    (stara fala / stale pickup nie dostaje pełnego +30)
      0-10 inclusive → linear decay (0 → 30, 10 → 0)
      > 10 → 0
    """
    if gap_min is None:
        return 0.0
    if gap_min < 0:
        if decision_flag("ENABLE_C2_NEG_GAP_DECAY"):  # ETAP 4: flags.json → const
            over = -gap_min  # magnituda antycypacji (jak bardzo pickup wyprzedza free_at)
            if over <= C2_NEG_GAP_FULL_BONUS_MIN:
                return BUG2_WAVE_CONTINUATION_BONUS  # mild anticipation = realna fala
            frac = min(
                (over - C2_NEG_GAP_FULL_BONUS_MIN) / C2_NEG_GAP_DECAY_SPAN_MIN, 1.0)
            return BUG2_WAVE_CONTINUATION_BONUS * (
                1.0 - frac * (1.0 - C2_NEG_GAP_FLOOR_FRAC))
        return BUG2_WAVE_CONTINUATION_BONUS  # legacy flat (flag OFF)
    if gap_min <= BUG2_INTERLEAVE_GATE_MIN:
        return BUG2_WAVE_CONTINUATION_BONUS * (
            1.0 - gap_min / BUG2_INTERLEAVE_GATE_MIN
        )
    return 0.0


# ============================================================
# V3.24 SCHEDULE INTEGRATION (2026-04-22) — Adrian decision
#
# Ziomek respektuje grafik kurierów + hard cutoff na early morning
# emit. Dwie części:
#   A) extension-based penalty — kara za pickup delay kuriera vs
#      restaurant-requested pickup time (+ hard reject > 60 min)
#   B) czasówka progressive emit scheduler — ordery z
#      czas_odbioru ≥ 60 min trzymane w id_kurier=26 (Koordynator)
#      do minutes_to_pickup ≤ 60 min, potem gradient selectivity
#      60→50→40 (ideal/good/force-assign).
#
# Pre-shift kurier wchodzi do pool bez time-gate (stary
# PRE_SHIFT_WINDOW_MIN=50 removed w B3) — gate replaced przez
# dropoff-after-shift hard reject + extension penalty.
#
# Defaults False; flip post B7 tests + shadow validation.
# Env kill-switches:
#   ENABLE_V324A_SCHEDULE_INTEGRATION=0|1
#   ENABLE_V324B_CZASOWKA_SCHEDULER=0|1
# ============================================================

# Ziomek nie emituje propozycji przed 9:10 Warsaw (operation window)
OPERATION_EMIT_NOT_BEFORE_HOUR_WARSAW = 9
OPERATION_EMIT_NOT_BEFORE_MIN_WARSAW = 10

# V3.24-A: planowany pickup pre-shift kuriera clamp do shift_start
V324_PICKUP_CLAMP_TO_SHIFT_START = True

# V3.24-A: tolerancja dropoff po shift_end (minuty) — hard reject if exceeded
V324_HARD_REJECT_DROPOFF_AFTER_SHIFT_MIN = 5

# V3.24-A: extension > X min = hard reject (kurier przesuwa pickup za bardzo)
V324_HARD_REJECT_EXTENSION_OVER_MIN = 60

# V3.24-A: gradient penalty; pair = (threshold_min_inclusive, penalty_pts)
# Pozytywna extension = kurier opóźnia restaurację vs requested pickup.
V324_EXTENSION_PENALTY_TIERS = [
    (5, 0),         # 0-5 min: ideal match, no penalty
    (15, -10),      # 5-15 min: small delay
    (30, -50),      # 15-30 min: moderate
    (45, -100),     # 30-45 min: significant
    (60, -200),     # 45-60 min: large (edge przed hard reject)
]

# ── Post-shift overrun penalty (Adrian 2026-06-24) ───────────────────────────
# Best_effort (feasible=0): rosnąca kara za KAŻDĄ minutę, o jaką dowóz nowego
# zlecenia wypada PO końcu zmiany kuriera. Powód: gdy nikt nie jest feasible,
# selekcja best_effort (objm R6-breach) jest ŚLEPA na koniec zmiany — kurier z
# czystym workiem (R6=0) wygrywa, mimo że kończy 30 min po zmianie (case 483144:
# Piotr +27, Kuba +38, Patryk 0 → ma trafić Patryk).
#
# Stawka ROŚNIE z progiem (krzywa wypukła = „rosnąca za każdą minutę"). 0-5 min
# = grace (skończyć ≤5 min po zmianie jest OK → 0 kary → kurier dalej konkuruje
# normalnie na R6). Powyżej: gradient (LESSON-QA-10 gradient nie próg). Wartości
# w PUNKTACH (te same jednostki co reszta penalty puli / -score). Kara liczona
# kumulacyjnie po progach przez post_shift_overrun_penalty().
#
# excess_min ≤ 0 (dowóz przed końcem zmiany) → 0. Brak shift_end → caller liczy 0
# (fail-open: grafik mógł paść — NIE karać na ślepo; mirror FAIL-12 / v325).
# pair = (threshold_min_inclusive, penalty_pts_per_min_w_tym_progu)
POST_SHIFT_OVERRUN_GRACE_MIN = 5.0
POST_SHIFT_OVERRUN_PENALTY_TIERS = [
    (5, 0.0),       # 0-5 min po zmianie: grace, brak kary
    (10, 8.0),      # 5-10 min:   8 pkt/min
    (20, 16.0),     # 10-20 min: 16 pkt/min
    (30, 28.0),     # 20-30 min: 28 pkt/min
    (10_000, 45.0),  # 30+ min:   45 pkt/min (≈ weto)
]


def post_shift_overrun_penalty(excess_min):
    """Rosnąca (wypukła) kara w PUNKTACH za nadwyżkę minut po końcu zmiany.

    excess_min: float — (planowany dowóz nowego ordera − shift_end) w minutach.
        ≤ POST_SHIFT_OVERRUN_GRACE_MIN → 0.0 (grace). None / nie-liczba → 0.0.
    Kumulacja po POST_SHIFT_OVERRUN_PENALTY_TIERS: w każdym progu nadwyżka mnożona
    przez stawkę/min tego progu. Zwraca wartość ≥ 0 (im więcej, tym GORZEJ —
    caller traktuje jako penalty / pierwszy term sortu best_effort).
    """
    if not isinstance(excess_min, (int, float)):
        return 0.0
    over = float(excess_min)
    if over <= POST_SHIFT_OVERRUN_GRACE_MIN:
        return 0.0
    penalty = 0.0
    prev = 0.0
    for threshold_min, rate_per_min in POST_SHIFT_OVERRUN_PENALTY_TIERS:
        if over <= prev:
            break
        span = min(over, float(threshold_min)) - prev
        if span > 0:
            penalty += span * float(rate_per_min)
        prev = float(threshold_min)
    return round(penalty, 2)


# Best_effort: użyj post_shift_overrun_penalty jako WIODĄCEGO termu selekcji
# (objm pick + sort_key fallback) + dodaj do score. Default OFF (shadow-first):
# metryka post_shift_overrun_min/_penalty liczona ZAWSZE (widoczność w shadow),
# ale wpływ na pick/score TYLKO gdy flaga ON. Flip = osobny ACK + poza peakiem.
ENABLE_POST_SHIFT_OVERRUN_PENALTY = _os.environ.get(
    "ENABLE_POST_SHIFT_OVERRUN_PENALTY", "0") == "1"

# V3.24-B: start eval czasówki gdy minutes_to_pickup ≤ X
V324B_CZASOWKA_EVAL_START_MIN = 60

# V3.24-B: min interval między re-score tego samego order (timer tick = 1min,
# per-order re-score gated do ≥ 5 min od ostatniej eval)
V324B_CZASOWKA_EVAL_INTERVAL_MIN = 5

# V3.24-B: force assign top candidate gdy minutes_to_pickup ≤ X
V324B_CZASOWKA_FORCE_ASSIGN_MIN = 40

# V3.24-B: "idealny match" thresholds (60 ≥ minutes > 50 window)
V324B_CZASOWKA_IDEAL_KM_MAX = 1.0
V324B_CZASOWKA_IDEAL_DROP_PROX_MIN = 0.5

# V3.24-B: "dobry match" thresholds (50 ≥ minutes > 40 window)
V324B_CZASOWKA_GOOD_KM_MAX = 2.0
V324B_CZASOWKA_GOOD_DROP_PROX_MIN = 0.5

# Feature flags — V3.24-A flipped True 2026-04-22 B14b (post dinner peak).
# V3.24-B flipped True 2026-04-22 B14c + systemd timer enabled.
ENABLE_V324A_SCHEDULE_INTEGRATION = _os.environ.get(
    "ENABLE_V324A_SCHEDULE_INTEGRATION", "1") == "1"
ENABLE_V324B_CZASOWKA_SCHEDULER = _os.environ.get(
    "ENABLE_V324B_CZASOWKA_SCHEDULER", "1") == "1"

# F3 (2026-05-06): czasowka_scheduler WAIT branch structural data loss fix.
# When True, czasowka_proactive.evaluator._filter_candidates uses
# eval_result['all_candidates_for_proactive'] instead of best+alternatives.
CZASOWKA_PROACTIVE_USE_ALL_CANDIDATES = _os.environ.get(
    "CZASOWKA_PROACTIVE_USE_ALL_CANDIDATES", "0") == "1"

# Faza 7-AUTO-PROXIMITY (2026-05-06, post-pivot 03.05 rule-based autonomy).
# Spec: eod_drafts/2026-05-06/faza_7_auto_proximity_design_spec.md
#
# AUTO_PROXIMITY_POST_SHIFT_5MIN: Adrian decyzja A1 — kurier 5+ min po shift_start
# z pos=None (brak GPS) → synthetic position (BIALYSTOK_CENTER) + pos_source
# "post_shift_start_synthetic". Pozwala AUTO klasyfikatorowi rozważyć kuriera
# który operacyjnie pracuje ale ma offline GPS. Default False — shadow tydzień
# włącza calibration mode.
ENABLE_AUTO_PROXIMITY_POST_SHIFT_5MIN = _os.environ.get(
    "ENABLE_AUTO_PROXIMITY_POST_SHIFT_5MIN", "0") == "1"

# Working-override (Adrian 2026-06-01): komenda Telegram "X pracuje" ma działać dla
# DWÓCH przypadków — (1) powracający po /stop (zdjęcie z excluded, jak dotąd),
# (2) kurier SPOZA grafiku który właśnie zaczyna → syntetyczny wpis grafiku na dziś.
# Override jest cid-keyed (manual_overrides.json["working"] = {cid: {start,end}}),
# AUTORYTATYWNY (wygrywa z realnym grafikiem: pokrywa "brak w grafiku", "zmiana
# skończona", "nie pracuje dziś"), lifecycle "do końca dnia" (reset 06:00 razem z
# manual_overrides). Default ON — feature jawnie zamówiony; env ENABLE_WORKING_OVERRIDE=0
# wyłącza (courier_resolver ignoruje sekcję "working", zero wpływu). Default end "24:00"
# = do północy; operator może zawęzić wpisując "X pracuje do HH:MM".
ENABLE_WORKING_OVERRIDE = _os.environ.get("ENABLE_WORKING_OVERRIDE", "1") == "1"
WORKING_OVERRIDE_DEFAULT_END = _os.environ.get("WORKING_OVERRIDE_DEFAULT_END", "24:00")

# Working-override GRAFIK-CAP (Adrian 2026-06-07, fix "Ziomek proponuje kuriera po zmianie").
# Komenda "X pracuje" z DOMYŚLNYM końcem (24:00), wpisana w trakcie/przed realną zmianą
# kuriera (added_at <= grafik_end), NIE może wskrzeszać go po realnym końcu grafiku —
# courier_resolver przycina efektywny shift_end do min(override_end, grafik_end). Pomijane gdy:
#   - operator podał JAWNY koniec ("pracuje do HH:MM" → entry["end_explicit"]=True),
#   - kurier NIE ma wpisu w grafiku dziś (spoza grafiku — 24:00 słuszne, brak innego źródła),
#   - override dodany PO końcu grafiku (added_at > grafik_end = realna druga/wieczorna zmiana).
# Default ON — korektność aktywnego buga; hot-reload kill-switch przez flags.json,
# env ENABLE_WORKING_OVERRIDE_GRAFIK_CAP=0 wyłącza (legacy: override_end bez przycięcia).
ENABLE_WORKING_OVERRIDE_GRAFIK_CAP = _os.environ.get(
    "ENABLE_WORKING_OVERRIDE_GRAFIK_CAP", "1") == "1"


def get_flag_czasowka_proactive_use_all_candidates() -> bool:
    return flag("CZASOWKA_PROACTIVE_USE_ALL_CANDIDATES", default=False)

# V3.25 STEP B (R-01 SCHEDULE-HARDENING) — unconditional PRE-CHECK w
# feasibility_v2 przed scoring path. Fail-CLOSED policy: cs.shift_end=None
# lub pickup poza shift window → HARD REJECT (vs V3.24-A soft penalty).
# Default False — flip po shadow ~30 min observation + Adrian ACK.
ENABLE_V325_SCHEDULE_HARDENING = _os.environ.get(
    "ENABLE_V325_SCHEDULE_HARDENING", "1") == "1"
# Pre-shift hard reject: pickup_ready < shift_start - V325_PRE_SHIFT_HARD_REJECT_MIN
# → kurier zbyt wcześnie do realnego startu. 30 min default.
V325_PRE_SHIFT_HARD_REJECT_MIN = 30
# Pre-shift soft penalty: pickup_ready ∈ [shift_start - 30, shift_start)
# → soft penalty -20 (gradient zone, kurier "warm-up" minutes).
V325_PRE_SHIFT_SOFT_PENALTY = -20

# --- Pre-shift okno + kara gradientowa (Adrian 2026-06-24) ---------------------
# Pula pre-shift ograniczona do PRE_SHIFT_WINDOW_MAX_MIN (cap przywrócony — V3.24-A
# go zniósł). Kara rośnie z liczbą minut do startu zmiany (shift_start_min):
#   m ≤ PRE_SHIFT_NEAR_MIN        → lekka (∝ m): chętnie brany, restauracja nie czeka rano
#   PRE_SHIFT_NEAR_MIN < m ≤ cap  → POTĘŻNA (~veto) POZA dużym przeładowaniem floty;
#                                   przy loadgov_ewma ≥ PRE_SHIFT_FAR_UNLOCK_LOAD relaks
#                                   do umiarkowanej kary (∝ m), by uniknąć długiego
#                                   czekania restauracji (lepiej +20-25 min w bagu).
# Rygor „odbiór nie przed zmianą" zapewnia osobno departure-clamp (≥ shift_start).
# Kill-switch: ENABLE_PRE_SHIFT_GRADIENT_PENALTY=0; cap-off: PRE_SHIFT_WINDOW_MAX_MIN=99999.
ENABLE_PRE_SHIFT_GRADIENT_PENALTY = _os.environ.get(
    "ENABLE_PRE_SHIFT_GRADIENT_PENALTY", "1") == "1"
PRE_SHIFT_WINDOW_MAX_MIN = float(_os.environ.get("PRE_SHIFT_WINDOW_MAX_MIN", "60"))
PRE_SHIFT_NEAR_MIN = float(_os.environ.get("PRE_SHIFT_NEAR_MIN", "30"))
PRE_SHIFT_NEAR_PEN_PER_MIN = float(_os.environ.get("PRE_SHIFT_NEAR_PEN_PER_MIN", "-1.0"))
PRE_SHIFT_FAR_PEN = float(_os.environ.get("PRE_SHIFT_FAR_PEN", "-1000.0"))
PRE_SHIFT_FAR_UNLOCK_LOAD = float(_os.environ.get("PRE_SHIFT_FAR_UNLOCK_LOAD", "3.5"))
# Dropoff hard reject: planned_dropoff > shift_end + 5 min
# (parallel do V3.24-A V324_HARD_REJECT_DROPOFF_AFTER_SHIFT_MIN, V3.25
# zachowuje to ale flag-gated osobno dla rollout independence).
V325_DROPOFF_AFTER_SHIFT_HARD_MIN = 5

# V3.28 ETAP 2 (2026-05-08) — pre_shift departure clamp.
# Gdy True: dla kandydata z pos_source in {"pre_shift", "no_gps"} i
# shift_start > now, simulate_bag_route_v2 dostaje earliest_departure=shift_start
# zamiast bazować plan na real now. Skutek: plan timestamps (pickup_at,
# predicted_delivered_at) liczone od shift_start → telegram trasa pokazuje
# realny "11:00 start, 11:05 odbiór" zamiast fikcyjnego "10:31 start" dla
# kuriera który jeszcze nie pracuje. Default False — flip po shadow obs.
ENABLE_PRE_SHIFT_DEPARTURE_CLAMP = _os.environ.get(
    "ENABLE_PRE_SHIFT_DEPARTURE_CLAMP", "0") == "1"

# V3.25 STEP C (R-04 NEW-COURIER-CAP gradient) — post-scoring penalty layer
# dla kurierów z tier_label='new' (Szymon Sa cid=522, Grzegorz Rogowski cid=500).
# Adrian's heurystyka: nowi mają +30% delivery time uncertainty + brak orientacji
# w terenie → penalize unless objectively significantly better (advantage > 50).
# Default False — flip po shadow ~30 min observation + Adrian ACK.
ENABLE_V325_NEW_COURIER_CAP = _os.environ.get(
    "ENABLE_V325_NEW_COURIER_CAP", "1") == "1"
# Bag cap: nowy + bag >= V325_NEW_COURIER_BAG_HARD_SKIP_AT → HARD SKIP (efektywny -inf score)
V325_NEW_COURIER_BAG_HARD_SKIP_AT = 2
# Gradient bins (advantage = candidate.score - max(non-new alt scores))
V325_NEW_COURIER_PENALTY_HIGH_ADVANTAGE = -10  # advantage >= 50 (objectively much better)
V325_NEW_COURIER_PENALTY_MED_ADVANTAGE = -30   # advantage 20-50
V325_NEW_COURIER_PENALTY_LOW_ADVANTAGE = -50   # advantage < 20 (default discount)
V325_NEW_COURIER_HIGH_ADV_THRESHOLD = 50.0
V325_NEW_COURIER_MED_ADV_THRESHOLD = 20.0

# SP-B2-RAMPA (2026-06-11, roadmapa BARTEK 2.0; Z-18 + mining H13/B6).
# Rampa nowych kurierów zamiast niewidzialności: przez pierwsze
# NEW_COURIER_RAMP_DELIVERIES dostaw kandydat tier='new' wchodzi do selekcji
# TYLKO na krótkie, proste kursy (dist ≤ MAX_KM, pusta torba, poza strefą
# śmierci 14-17) ze stałym malusem RAMP_MALUS zamiast gradientu/-1e9; kursy
# poza profilem → sentinel -1e9 (sort na koniec — kandydat ZOSTAJE w puli,
# ALWAYS-PROPOSE zachowane). Po rampie → normalne reguły R-04 (gradient wyżej).
# Licznik dostaw: courier_reliability.json (regen daily 04:30; brak wpisu = 0).
# Flaga: ENABLE_NEW_COURIER_RAMP w flags.json (hot-reload, default ON).
NEW_COURIER_RAMP_DELIVERIES = int(_os.environ.get("NEW_COURIER_RAMP_DELIVERIES", "30"))
NEW_COURIER_RAMP_MAX_KM = float(_os.environ.get("NEW_COURIER_RAMP_MAX_KM", "2.5"))
NEW_COURIER_RAMP_MALUS = float(_os.environ.get("NEW_COURIER_RAMP_MALUS", "-20.0"))
# Solo-guard (replay 11.06: rozszerzony sentinel dawał 6-7 NOWYCH eskalacji
# PROPOSE→KOORD/tydz. gdy zablokowany nowy był jedyną opcją — łamało
# ALWAYS-PROPOSE). Gdy po blokadach CAŁA pula < MIN_PROPOSE_SCORE: najlepszy
# zablokowany wraca na pre_block + SOLO_MALUS (mocno zdemotowany, proposable).
NEW_COURIER_RAMP_SOLO_MALUS = float(_os.environ.get("NEW_COURIER_RAMP_SOLO_MALUS", "-60.0"))

# SP-B2-SYNCWORKA (2026-06-11, H1 — największa dźwignia bundlingu).
# Worki o spreadzie gotowości >10 min niosą 50% WSZYSTKICH breachy
# (mining 2e: pick_spread ≤2:6,4% → 5-10:10,6% → 10-20:21,9% → >20:52,5%);
# multi-rest przy sync ≤5 min jest bezpieczny jak same-rest. Kara gradientowa
# za ready_spread worka (max−min effective_ready niedoręczonych + nowego):
# 0 przy ≤7 min, -30 przy 10, -80 przy 15, -150 przy ≥20 (liniowo między
# węzłami; NIE hard reject — ALWAYS-PROPOSE) + zerowanie bonusów bundlowych
# przy spreadzie >10 (wzór Fix C). Flaga decyzyjna ENABLE_BUNDLE_SYNC_SPREAD
# default OFF — shadow-delta zawsze serializowana; flip = 🛑 ACK Adriana.
ENABLE_BUNDLE_SYNC_SPREAD = _os.environ.get(
    "ENABLE_BUNDLE_SYNC_SPREAD", "0") == "1"
SYNC_SPREAD_KNOTS = ((7.0, 0.0), (10.0, -30.0), (15.0, -80.0), (20.0, -150.0))
SYNC_SPREAD_BUNDLE_ZERO_MIN = float(_os.environ.get("SYNC_SPREAD_BUNDLE_ZERO_MIN", "10.0"))

# SP-B2-PREPBIAS konsumpcja w DECYZJACH (effective_ready = deklaracja + bias
# z restaurant_prep_bias.json) — default OFF, flip = 🛑 ACK Adriana (osobny
# punkt roadmapy). SYNCWORKA liczy spread z biasem gdy ta flaga ON, inaczej
# z samych deklaracji.
ENABLE_PREP_BIAS_TABLE = _os.environ.get(
    "ENABLE_PREP_BIAS_TABLE", "0") == "1"

# SP-B2-REPO (2026-06-11, raport §3.1.4): koszt repozycjonowania w selekcji.
# Mediana 3,56 km z ostatniego dropa do następnego odbioru = ukryta połowa
# kilometrów (najlepsi 2,4, ogon 4,3); score widzi tylko km_to_pickup
# z BIEŻĄCEJ pozycji. s_repo = kara za dead-head: km(drop poprzedzający
# nowy odbiór w PLANIE kandydata → nowy pickup); odbiór PRZED dropami
# (po drodze / pusty bag) → 0 (km_to_pickup już to wycenia — bez podwójnego
# liczenia). Kara = -REPO_COST_MAX_PENALTY * min(1, km/REPO_KM_FULL_SCALE)
# — waga rzędu komponentu dystansu (~30 pkt), nie 5-pkt bonus.
# Flagi (wzorzec ETAQ): _SHADOW = telemetria (ON), _LIVE = aplikacja do score
# (OFF, flip = 🛑 ACK Adriana po replay).
REPO_COST_MAX_PENALTY = float(_os.environ.get("REPO_COST_MAX_PENALTY", "30.0"))
REPO_KM_FULL_SCALE = float(_os.environ.get("REPO_KM_FULL_SCALE", "4.0"))
ENABLE_REPO_COST_LIVE = _os.environ.get("ENABLE_REPO_COST_LIVE", "0") == "1"

# SP-B2-ZARAZWOLNY (2026-06-11, B2): kurier busy kończący ≤ SOON_FREE_MAX_MIN
# (wg zapisanego planu) jako pełnoprawny kandydat — pozycja = ostatni drop,
# dostępność = free_at, bez kary obciążenia bieżącym bagiem (nowy odbiór i tak
# PO zwolnieniu). 61% busy-picków człowieka to kurier kończący ≤12 min.
# Telemetria soon_free_* zawsze; substytucja za 🛑 flagą (OFF, flip = ACK).
SOON_FREE_MAX_MIN = float(_os.environ.get("SOON_FREE_MAX_MIN", "12.0"))
ENABLE_SOON_FREE_CANDIDATE = _os.environ.get("ENABLE_SOON_FREE_CANDIDATE", "0") == "1"

# SP-B2-LOADGOV (2026-06-11, M1 + werdykt CASCADE): load governor floty.
# Argmax bez hamulca floty = 17% breach/worek 33 w kaskadzie; load-aware =
# 8-10%. Load = aktywne zlecenia (orders_state, nie-terminalne, świeże) /
# aktywni kurierzy (dispatchable_fleet), wygładzone EWMA tau=15 min.
# Polityka (za 🛑 flagą ENABLE_FLEET_LOAD_GOVERNOR, OFF):
#   ewma > TIGHTEN_AT (2,7) → kara score za dokładanie do bagów ≥ BAG_MIN (3)
#     (miękki odpowiednik "tighten capów o 1"; ALWAYS-PROPOSE — zero rejectów);
#   ewma > DEFENSIVE_AT (3,5) → JEDEN alert Telegram "tryb defensywny"
#     (hysteresis: re-arm dopiero < REARM_AT 3,0 — nie spam).
# Telemetria loadgov_* serializowana ZAWSZE (kalibracja przed flipem).
LOADGOV_TIGHTEN_AT = float(_os.environ.get("LOADGOV_TIGHTEN_AT", "2.7"))
LOADGOV_DEFENSIVE_AT = float(_os.environ.get("LOADGOV_DEFENSIVE_AT", "3.5"))
LOADGOV_REARM_AT = float(_os.environ.get("LOADGOV_REARM_AT", "3.0"))
LOADGOV_BAG_MIN = int(_os.environ.get("LOADGOV_BAG_MIN", "3"))
LOADGOV_BAG_PENALTY = float(_os.environ.get("LOADGOV_BAG_PENALTY", "-40.0"))
LOADGOV_EWMA_TAU_MIN = float(_os.environ.get("LOADGOV_EWMA_TAU_MIN", "15.0"))
LOADGOV_ORDER_FRESH_H = float(_os.environ.get("LOADGOV_ORDER_FRESH_H", "3.0"))
ENABLE_FLEET_LOAD_GOVERNOR = _os.environ.get("ENABLE_FLEET_LOAD_GOVERNOR", "0") == "1"

# V3.26 STEP 1 (R-11 TRANSPARENCY-RATIONALE) — decision rationale dla każdej
# propozycji: top 3 factors + advantage vs next-best. Visible w Telegram
# proposal text + serialized in shadow_decisions/learning_log dla audit.
ENABLE_V326_TRANSPARENCY_RATIONALE = _os.environ.get(
    "ENABLE_V326_TRANSPARENCY_RATIONALE", "1") == "1"
# Threshold poniżej którego "close call" warning fires (BEST i 2nd-best
# blisko siebie, Adrian może chcieć zweryfikować ręcznie).
V326_RATIONALE_CLOSE_CALL_THRESHOLD = 5.0
# Threshold powyżej którego "clear winner" wskazany (BEST znacząco lepszy).
V326_RATIONALE_CLEAR_WIN_THRESHOLD = 50.0

# V3.26 STEP 2 (R-05 SPEED-MULTIPLIER) — backtest empirical (40,790 deliveries
# Nov2025-Apr2026, n=22,482 std baseline median=18min). Adrian Q&A 22.04
# heurystyka + V3.26 backtest 24.04 sanity. Multiplier > 1.0 = wolniejszy,
# < 1.0 = szybszy. Score adjustment = (1.0 - multiplier) * SCORE_FACTOR.
ENABLE_V326_SPEED_MULTIPLIER = _os.environ.get(
    "ENABLE_V326_SPEED_MULTIPLIER", "1") == "1"
V326_SPEED_MULTIPLIER_MAP = {
    # REKALIBRACJA 2026-06-10 z 3056 realnych dostaw (backfill_decisions_outcomes_v1),
    # atrybucja outcome.courier_id_final → AKTUALNY tier (przypisania z panelu). Mediana
    # realnego czasu dostawy / std: gold 14.8/17.4=0.83, std+ 16.3/17.4=0.94,
    # slow 22.6/17.4=1.30, new 20.4/17.4=1.17. Stare wartości (backtest XI.2025-IV.2026,
    # inne przypisania tierów): gold 0.889 / std+ 1.056 / std 1.0 / slow 1.111 / new 1.300.
    'gold':  0.850,  # real 0.83× std (Bartek O/Mateusz O/Gabriel med ~14-15 min, resid +6)
    'std+':  0.940,  # FIX INWERSJI: std+ realnie SZYBSZY niż std (16.3<17.4 min); było 1.056 (>1.0)
    'std':   1.000,  # baseline (zawsze 1.0)
    'slow':  1.250,  # real 1.30× std (Adrian R/Michał Li med ~22 min, breach 18%); było 1.111
    'new':   1.200,  # real 1.17× std — dane (n=357) zastępują policy 1.30; tail-risk → DWELL/ETA
}
# Score adjustment = (1.0 - multi) * SCORE_FACTOR.
# gold (0.889) → +5.55 score boost, slow (1.111) → -5.55 penalty, new (1.30) → -15.
V326_SPEED_SCORE_FACTOR = 50.0

# Tier-aware DWELL (2026-05-17). Postój kuriera = OBSŁUGA stopu.
# E1 sprint 2026-05-17 (Adrian): postój pod restauracją to czysta obsługa
# (chwyć torbę) ~1 min — NIE czekanie na jedzenie (to liczy pickup_ready_at
# osobno). Stąd pickup = flat DWELL_PICKUP_FLAT_MIN dla WSZYSTKICH tierów.
# Dropoff (handoff u klienta) zostaje tier-aware: szybszy tier = krótszy postój.
# Klucze DWELL_BY_TIER = tier_bag (jak V326_SPEED_MULTIPLIER_MAP); wartości =
# DROPOFF min. Nieznany/None tier → DWELL_DEFAULT_MIN dropoff fallback. Pętla
# ucząca (eta_calibration_log.jsonl) dopreciezuje dropoff per tier.
# --- PICKUP-BUFFER: load-aware bufor OBIETNICY odbioru (2026-07-06, v2) -------
# Decyzja Adriana 06.07: powierzchnia = OBIETNICA DECYZYJNA (bufor doliczany do
# obiecywanego czasu odbioru w polach eta_pickup_promised_*; serializer best →
# konsola/1-klik time_arg). KALIBRACJA v2 — 2 korekty Adriana z tego samego dnia:
#  (1) „lepiej żeby się spóźnił do 5 min, niż za ostrożnie i żeby czekał —
#      każda minuta ważna" → efektywny bufor = mediana − tolerancja
#      PICKUP_BUFFER_LATE_TOLERANCE_MIN (obietnica celuje w ~5 min spóźnienia;
#      kurier prawie nigdy nie czeka pod restauracją).
#  (2) „to od wielu rzeczy zależy — punktualnemu nie doliczaj 25 min" → tabela
#      liczona TYLKO na populacji matched_courier (jechał TEN kurier, którego
#      dotyczyła predykcja — obietnica z buforem jedzie w 1-klik akcept TEGO
#      kandydata) i BEZ czasówek. Stara tabela v1 (mediany all: 25/24/17/13/12/7)
#      była zawyżona rekordami, gdzie koordynator przydzielił INNEGO kuriera
#      (med poślizgu 17-21 min vs 8.6-11 dla matched).
# Kubełki 1:1 z tools/pickup_slip_monitor: luzno pool_feasible>=5 / srednio 2-4
# / ciasno <=1; solo = bag_after 1 (r6_bag_size+1). Wartości = SUROWE mediany
# poślizgu (matched, bez czasówek, okno 6d do 06.07, n=823); ciasno-solo n<15 →
# pożyczka od srednio-solo. Brak danych (pf/bag None) → 0.0 = stara obietnica
# (fail-open). Flaga ENABLE_LOAD_AWARE_PICKUP_BUFFER (ETAP4, OFF) — flip hot za ACK.
ENABLE_LOAD_AWARE_PICKUP_BUFFER = False
# v4 (GO Adriana 06.07 "ab"): bufor = BAZA + korekta per-restauracja.
# Kubelki obciazenia/worka (v2) ODRZUCONE danymi (0 zysku OOS na 51d).
# BAZA = mediana poslizgu jedzeniowki 8.2 (matched-only, BEZ paczek/czasowek)
# - tolerancja Adriana 5 ("lepiej spoznic sie do 5 min niz kurier czeka").
# TABELA per-restauracja = stabilni odchylency (n>=30, |bias|>5 od globalnej):
# notorycznie "poslizgowa" restauracja dostaje realny czas (med-5), punktualna
# JAWNE 0.0 (zero sztucznego zapasu — kucharz nie przedluza gotowania).
# PACZKI: guard w shadow_dispatcher (is_paczka_order) zdejmuje pola promised.
# Klucze = nazwa restauracji jak w decision record (result.restaurant).
PICKUP_BUFFER_BASE_MIN = 3.0
PICKUP_BUFFER_RESTAURANT_TABLE = {
    "Baanko": 9.0,                        # med +14.0 (n>=30)
    "Pizzeria 105 Galeria Bia\u0142a": 8.5,  # med +13.5
    "Hacienda Pizza": 0.0,                # med -0.1 — punktualna, bez zapasu
    "Restauracja Kumar&#039;s": 0.0,      # med +2.4
    "Street Mama Thai": 0.0,              # med +3.3
}
PICKUP_BUFFER_MAX_MIN = 30.0


def pickup_buffer_min(restaurant):
    """Bufor obietnicy odbioru (min): korekta per-restauracja albo BAZA.

    Nieznana/nowa restauracja => BAZA (globalna mediana-5). Odchylency z
    tabeli => ich wlasna wartosc (0.0 = jawnie bez zapasu). Cap 30.
    """
    if restaurant:
        v = PICKUP_BUFFER_RESTAURANT_TABLE.get(str(restaurant))
        if v is not None:
            return min(max(float(v), 0.0), float(PICKUP_BUFFER_MAX_MIN))
    return min(float(PICKUP_BUFFER_BASE_MIN), float(PICKUP_BUFFER_MAX_MIN))


DWELL_PICKUP_FLAT_MIN = 1.0  # E1 2026-05-17 — postój pod restauracją (obsługa)
DWELL_DEFAULT_MIN = 3.5  # dropoff fallback dla nieznanego tieru
DWELL_BY_TIER = {  # wartości = DROPOFF per tier (rezyduum ETA uczony z eta_calibration_log)
    # REKALIBRACJA 2026-06-10: korekta = bieżący DWELL + mediana błędu predykcji ETA per
    # tier (real_delivery − predicted_delivery, 7496 dopasowanych rekordów eta_calibration_log).
    # Błąd predykcji: gold −1.2 / std+ −0.6 / std +1.2 / slow +2.3 / new +2.3 min. Zeruje
    # systematyczny bias — Ziomek kompresował spread tierów (real 15.2→20.6, pred 14.9→17.3).
    # Stare wartości: gold 2.5 / std+ 3.0 / std 3.5 / slow 4.0 / new 4.0.
    'gold': 1.5,   # 2.5 − 1.0 (Ziomek przeszacowywał gold ETA o ~1.2 min)
    'std+': 2.5,   # 3.0 − 0.5
    'std':  4.5,   # 3.5 + 1.0
    'slow': 6.5,   # 4.0 + 2.5 (niedoszacowanie ~2.3 min, breach 18%)
    'new':  6.5,   # 4.0 + 2.5 (niedoszacowanie + ogon p90 +23 min)
}


def dwell_for_tier(tier):
    """Zwraca (dwell_pickup_min, dwell_dropoff_min) dla tieru kuriera (tier_bag).

    E1 2026-05-17: pickup = flat DWELL_PICKUP_FLAT_MIN (czysta obsługa pod
    restauracją; czekanie na jedzenie liczy pickup_ready_at osobno). Dropoff =
    tier-aware. Nieznany/None tier → DWELL_DEFAULT_MIN dropoff fallback.
    """
    d = DWELL_BY_TIER.get(tier, DWELL_DEFAULT_MIN)
    return (DWELL_PICKUP_FLAT_MIN, d)


# Tier-aware czas JAZDY (Sprint 3, 2026-05-17). Mnożnik tempa kuriera na nogach
# trasy w route_simulator (leg_min). >1.0 = kurier wolniejszy. Domyślnie 1.0 =
# inert (zero zmiany) — wartości kalibrowane z eta_calibration_log po Sprincie 1
# (composition-clean rezyduum per tier). NIE używać surowej V326_SPEED_MULTIPLIER_MAP:
# była kalibrowana na całkowitym czasie dostawy przy płaskim DWELL — po tier-aware
# DWELL zastosowanie jej do jazdy = podwójne liczenie. Patrz
# eod_drafts/2026-05-17/sprint3_tier_aware_drive_design.md.
DRIVE_SPEED_MULT_DEFAULT = 1.0
# 2026-07-18 (sprint D3-gold, TODO werdyktu): POMIAR composition-clean na
# eta_calib.db (2953 CZYSTE bezpośrednie nogi = zero pośrednich stopów worka
# między pickup a deliver; 2 okna 14.06-03.07 i 04.07-17.07 SPÓJNE) OBALIŁ
# starą tabelę 0.78/0.82 z 26.06 („Krok 1 agresywny" liczony na czasie
# SKAŻONYM stopami worka): realne mediany ratio jazdy = gold 0.96, std+ 1.06,
# std 0.86, new 0.95. Wartości niżej = ZMIERZONE (kod nie kłamie), ALE FLIP
# ŚWIADOMIE ZANIECHANY: zysk MAE ETA dostawy 3.01→2.92 min (~3%, gold −2%)
# = o rząd wielkości słabszy niż czekający kalibrator per-leg/per-KURIER
# z 07.07 (ta sama baza; dostawa −20%, odbiór −52%) — to ON jest właściwą
# realizacją „skalibruj ETA gold dobrze" (decyzja flipu = Adrian, todo).
# NIE WSKRZESZAĆ 0.78 (klasa: obalone pomiarem). Pomiar+MAE:
# eod_drafts/2026-07-18/gold_speed_mult_measure.py (+ EVIDENCE md).
DRIVE_SPEED_MULT_BY_TIER = {
    'gold': 0.96,
    'std+': 1.06,
    'std':  0.86,
    'slow': 1.0,   # zero aktywnych danych (0 czystych nóg w obu oknach)
    'new':  0.95,  # n małe/niestabilne między oknami (0.95 vs 1.12) — traktuj ostrożnie
}


def speed_mult_for_tier(tier):
    """Mnożnik tempa jazdy kuriera dla route_simulator leg_min.

    <1.0 = szybciej (krótsze nogi trasy). Nieznany/None tier →
    DRIVE_SPEED_MULT_DEFAULT (1.0).

    Bramka flagą ENABLE_DRIVE_SPEED_TIER_CORRECTION (hot-reload):
    OFF (default) → 1.0 dla każdego tieru = legacy/inert (zero zmiany decyzji);
    ON → wartości kalibrowane per tier. Rollback = flaga OFF (bez restartu).
    """
    if not flag("ENABLE_DRIVE_SPEED_TIER_CORRECTION", False):
        return DRIVE_SPEED_MULT_DEFAULT
    return DRIVE_SPEED_MULT_BY_TIER.get(tier, DRIVE_SPEED_MULT_DEFAULT)

# V3.26 STEP 3 (R-09 WAVE-GEOMETRIC-VETO) — refinement V3.19h BUG-2.
# Bug case (Adrian Q&A 22.04 Kacper Sa): wave_continuation +30 fire'uje gdy
# gap OK (free_at 5min after pickup wave#2) ALE drops rozrzucone na 2 końce
# miasta (>5km haversine). Veto bonus jeśli geographical incoherence.
ENABLE_V326_WAVE_GEOMETRIC_VETO = _os.environ.get(
    "ENABLE_V326_WAVE_GEOMETRIC_VETO", "1") == "1"
# Threshold km od last_drop do new_pickup powyżej którego BUG-2 bonus zostaje
# zveto'wany. 3.0 km = ~5 min ride w Bialymstoku — krzyżowanie ½ miasta.
V326_WAVE_VETO_KM_THRESHOLD = 3.0

# FIX 2 (2026-05-22): R-09 oś nowej DOSTAWY. R-09 powyżej mierzy tylko odbiór
# (last_drop→new_pickup), FIX_C tylko cały spread bagu — pojedyncza daleka rozbieżna
# DOSTAWA (Hallera 3.25km NW w 475235) wpada w lukę między progi i utrzymuje +30.
# Veto bonusu kontynuacji gdy nowa dostawa JEDNOCZEŚNIE: daleko od centroidu dostaw bagu
# (km) ORAZ rozbieżna kierunkowo (izolowany cosinus < próg). AND chroni legalną
# kontynuację "dalej tym samym korytarzem" (daleko, ale wysoki cosinus → bonus zostaje).
# Default OFF (shadow-first). Env: ENABLE_V326_WAVE_VETO_NEW_DROP=1
ENABLE_V326_WAVE_VETO_NEW_DROP = _os.environ.get(
    "ENABLE_V326_WAVE_VETO_NEW_DROP", "0") == "1"
V326_WAVE_VETO_NEW_DROP_KM = float(_os.environ.get(
    "V326_WAVE_VETO_NEW_DROP_KM", "2.5"))
V326_WAVE_VETO_NEW_DROP_COS = float(_os.environ.get(
    "V326_WAVE_VETO_NEW_DROP_COS", "0.5"))

# V3.26 STEP 4 (R-10 FLEET-LOAD-BALANCE) — score adjustment dla równomiernego
# rozkładu obciążenia floty. Adrian Q&A: nie chcemy 1 kurier z 5 bagami gdy
# inni mają 0-1. Penalty dla overloaded, bonus dla underloaded.
ENABLE_V326_FLEET_LOAD_BALANCE = _os.environ.get(
    "ENABLE_V326_FLEET_LOAD_BALANCE", "1") == "1"
# Delta from fleet avg → adjustment:
#   delta < -1.0 → bonus +V326_FLEET_LOAD_BONUS (low load courier)
#   delta > +1.0 → penalty -V326_FLEET_LOAD_PENALTY (overloaded courier)
#   -1.0 <= delta <= +1.0 → no adjustment (around mean)
V326_FLEET_LOAD_THRESHOLD = 1.0
V326_FLEET_LOAD_BONUS = 15.0
V326_FLEET_LOAD_PENALTY = 15.0

# V3.26 STEP 5 (R-06 MULTI-STOP-TRAJECTORY) — district-based trajectory bonus.
# Adrian Q&A 22.04 case Kacper Sa multi-drop: scoring nie liczył czy nowy
# pickup PODĄŻA z trajektorii ostatniego dropu.
# Mechanism: classify_trajectory(last_drop_district, new_pickup_district) →
# relation → bonus/penalty.
ENABLE_V326_MULTISTOP_TRAJECTORY = _os.environ.get(
    "ENABLE_V326_MULTISTOP_TRAJECTORY", "1") == "1"
V326_R06_BONUS_SAME       = 40.0   # same district
V326_R06_BONUS_SIMILAR    = 15.0   # adjacency hit
V326_R06_PENALTY_SIDEWAYS = -10.0  # cross-quadrant, nie opposite
V326_R06_PENALTY_OPPOSITE = -40.0  # N↔SE/SW lub E↔W

# V3.26 STEP H2 (2026-04-25) — R-06 bag1 fix flag-gated.
# Cross-review A#2.1: hardcoded `if bag_size < 2` blokował R-06 trajectory dla
# 30-50% candidates z bag=1. Komentarz "bag=1 nie ma 'ostatniego' dropu" błędny:
# bag=1 MA last drop — to bag=0 nie ma. Flag default False (shadow): threshold
# pozostaje 2 dla obs window. Po flip: threshold 0 → bag>=1 wchodzi w R-06.
ENABLE_V326_R06_BAG1_FIX = _os.environ.get(
    "ENABLE_V326_R06_BAG1_FIX", "0") == "1"

# V3.28 FIX_C (2026-05-01) — Bundle deliv_spread hard cap (FILOZ-3 peak-safe gate).
# Bug #469834: cross-restaurant bundle (Raj + Grill Kebab pickup 10m apart) z drops
# w przeciwnych częściach miasta (Wasilkowska NE Bojary + Magazynowa S Nowe Miasto,
# 8.49km road). Andrei K wygrał (score 6.80) przez bonus_l2 (+20) + bug2_continuation
# (+30), Kuba OL przegrał (2.38). Bundle scoring obecnie liczy tylko pickup_spread,
# IGNORUJE deliv_spread dla cross-restaurant bundles. Bug Z (V3.27) penalizuje tylko
# bonus_r4 corridor, NIE bonus_l2/continuation. Gate zeruje obie nagrody gdy bag>=1
# i deliv_spread > cap. bonus_l1 SR pozostaje (osobny mechanizm, drop_proximity_factor
# SR-only). Threshold 8.0 km na podstawie analizy 958 bundles since 2026-04-23:
# >=8km bucket = 18.1% propozycji, większość PANEL_OVERRIDE. Default OFF.
ENABLE_BUNDLE_DELIV_SPREAD_CAP = _os.environ.get(
    "ENABLE_BUNDLE_DELIV_SPREAD_CAP", "0") == "1"
# L6.C2 (2026-07-04): JEDEN kanon progu spread dostaw (scala dawne R1_MAX_DELIV_SPREAD_KM
# hardcode 8.0 w feasibility_v2 + BUNDLE_MAX_DELIV_SPREAD_KM env 8.0 tutaj — ta sama
# semantyka „rozrzut dostaw worka > X km = geometrycznie zły", 2 literały → 1 źródło;
# wartość NIEZMIENIONA = bajt-parytet). Env: MAX_DELIV_SPREAD_KM, back-compat fallback
# na stary klucz BUNDLE_MAX_DELIV_SPREAD_KM. Aliasy niżej trzymają starych konsumentów.
MAX_DELIV_SPREAD_KM = float(_os.environ.get(
    "MAX_DELIV_SPREAD_KM",
    _os.environ.get("BUNDLE_MAX_DELIV_SPREAD_KM", "8.0")))
BUNDLE_MAX_DELIV_SPREAD_KM = MAX_DELIV_SPREAD_KM  # alias (konsumenci FIX_C/serializer)

# BUNDLE-DELIVERY-COLOCATION (Adrian 2026-06-26) — forced-bundle z 2 twardych reguł.
# Stała-fallback OFF (decision_flag: flags.json → ta stała → False). Próg = „ten sam
# blok" (case 509 Raj↔Street Mama Thai = 0,037 km). Bonus jak L2 (max - dist*10).
ENABLE_BUNDLE_DELIVERY_COLOCATION = False
BUNDLE_DELIV_COLOC_KM = float(_os.environ.get(
    "BUNDLE_DELIV_COLOC_KM", "0.3"))
BUNDLE_DELIV_COLOC_BONUS_MAX = float(_os.environ.get(
    "BUNDLE_DELIV_COLOC_BONUS_MAX", "20.0"))
# geocode-centroid guard (audyt 28.06): gdy Google nie zna adresu → zwraca CENTRUM miasta
# (122 adresów cache → BIALYSTOK_CENTER 53.1325,23.1688). Dwa takie drops widzą się 0km →
# FAŁSZYWY deliv-coloc bundle. Guard wyklucza pary, gdzie któryś drop jest „defaultowym"
# centroidem (BIALYSTOK_CENTER + FIRMOWE_KONTO_FALLBACK). Flaga OFF→shadow→ON za ACK.
ENABLE_BUNDLE_COLOC_CENTROID_GUARD = False
BUNDLE_COLOC_DEFAULT_CENTROIDS = ((53.1325, 23.1688), (53.13222, 23.16844))
BUNDLE_COLOC_CENTROID_TOL_KM = float(_os.environ.get(
    "BUNDLE_COLOC_CENTROID_TOL_KM", "0.06"))

# V3.28 R-04 v2.0 GRADUATION SCHEMA (2026-05-01) — peak-quality based tier suggestions.
# Phase 1 SHADOW: r04_evaluator generates tier_suggestions.json (cron 03:00 daily,
# manual trigger Phase 1). shadow_dispatcher attaches r04 field do decision_record
# (current_tier, suggested_tier, tier_match, gold_candidate). ZERO scoring impact —
# courier_tiers.json nadal source of truth. Phase 2 ENFORCE pending Adrian ACK
# post obs window (auto-update tiers w cooldown 7d, gold remains manual-only).
ENABLE_R04_SHADOW = _os.environ.get("ENABLE_R04_SHADOW", "1") == "1"
ENABLE_R04_ENFORCE = _os.environ.get("ENABLE_R04_ENFORCE", "0") == "1"

# V3.28 Faza 6 — LGBM Pairwise Ranker shadow inference (2026-05-01).
# Pure Behavioral Cloning model trained na 399K pairs CSV history (Faza 5 v1.0).
# Phase 1 SHADOW: parallel computation, log do decision_record. ZERO behavior change.
# Architecture: feasibility_v2 hard rules pre-filter → LGBM ranks feasible candidates.
# Default OFF — flip ON jutro post-restart obs window.
# Hard latency cap 500ms (fallback "latency_timeout"), soft 200ms (warning log).
ENABLE_LGBM_SHADOW = _os.environ.get("ENABLE_LGBM_SHADOW", "0") == "1"
ENABLE_LGBM_PRIMARY = _os.environ.get("ENABLE_LGBM_PRIMARY", "0") == "1"  # Faza 7+ flip
LGBM_SHADOW_LATENCY_HARD_CAP_MS = float(_os.environ.get("LGBM_SHADOW_LATENCY_HARD_CAP_MS", "500"))
LGBM_SHADOW_LATENCY_SOFT_CAP_MS = float(_os.environ.get("LGBM_SHADOW_LATENCY_SOFT_CAP_MS", "200"))

# F4 — LGBM Candidate signature mismatch fix (Opt 3 hack, NIE Opt 1).
# When True, ml_inference reads bag_size etc. from c.metrics dict instead of
# getattr(c, ...) which always returns default 0 for dispatch_pipeline.Candidate.
# Default False — legacy getattr behavior (preserve fallback path).
ENABLE_LGBM_METRICS_READ = _os.environ.get("ENABLE_LGBM_METRICS_READ", "0") == "1"

# V3.26 Bug A complete (2026-04-25 sobota) — anchor-based distance scoring.
# Replace chronological-last-drop effective_start_pos z chronologically-previous
# stop w plan (insertion anchor). Distance kuriera do new pickup liczone od
# anchor location, NIE od fictional far end-of-bag stop. Plus rationale display
# recalibration (actual contribution zamiast misleading -km*5 heuristic) +
# Telegram label "X km do {anchor_restaurant}". Default False — shadow path.
ENABLE_V326_ANCHOR_BASED_SCORING = _os.environ.get(
    "ENABLE_V326_ANCHOR_BASED_SCORING", "1") == "1"

# V3.26 Bug C strict mode (2026-04-25 sobota) — "po drodze" semantyka.
# Pre-fix: dispatch_pipeline.py:850 bundle_level3 fires gdy dev<2.0km (geometric
# only). Adrian's case #468404: Maison 1.02 km od Sweet Fit fires "po drodze"
# ALE pickup Maison @ 10:04 vs pickup Sweet Fit @ 10:37 = 33 min apart, 2 intervening
# stops (drop Łąkowa, pickup Doner) → mylące UX.
# Strict mode dodaje:
# - Time proximity: bag_pickup_ready_at w ±PO_DRODZE_TIME_DIFF_MIN od new pickup_ready
# - Intervening stops (gdy plan + anchor available): count stops między anchor i
#   new pickup w plan.events <= PO_DRODZE_MAX_INTERVENING
# Default flag False — zero behavior change. Adrian flips po shadow validation.
PO_DRODZE_DIST_KM = 2.0
PO_DRODZE_TIME_DIFF_MIN = 10
PO_DRODZE_MAX_INTERVENING = 0
ENABLE_V326_PO_DRODZE_STRICT = _os.environ.get(
    "ENABLE_V326_PO_DRODZE_STRICT", "1") == "1"

# V3.26 Fix 6 (2026-04-25 sobota) — OR-Tools TSP solver replaces bruteforce/greedy.
# Adrian's strategic decision (Opcja 1 czysty OR-Tools): industry-standard
# constraint programming dla wszystkich bag sizes. Time-bounded search 200ms.
# Eliminates greedy zigzag pattern dla bag>3 (#468404 case study).
# Default False — shadow validation period przed flip True.
# D.3 fala B (2026-07-02): KANON=flags.json. Było env-frozen default "1" (jednolicie
# ON, żaden drop-in nie nadpisuje) → literał True = stała-fallback steady-state +
# źródło odczytu dla konsumenta (route_simulator_v2:438 czyta ten atrybut modułu —
# NIE zmieniany). decision_flag("ENABLE_V326_OR_TOOLS_TSP") = flags.json→ta stała.
# PARA ATOMOWA z ENABLE_V326_SAME_RESTAURANT_GROUPING (#13, check_v326_pair_coherence).
ENABLE_V326_OR_TOOLS_TSP = True  # V3.27 flip 2026-04-25 wieczór: re-enabled post Bug X+Y+Z+latency fixes
V326_OR_TOOLS_TIME_LIMIT_MS = 200  # V3.27 (2026-04-25 wieczór): RESTORED 50→200ms post parallel ThreadPoolExecutor implementation. 10 workers × 200ms = ~250-400ms wall (vs sequential 2000ms). Adrian's spec 6.5 budget. Strategic: jakość bag>=4 zamiast skróconych 50ms.

# V3.27 Phase 1A+G (Adrian Option B 2026-04-25 wieczór): skip OR-Tools dla
# trivial cases (bag<=1, bag_after_add<=2). OR-Tools time_limit=200ms hits
# ceiling EVERY call (D2 verified solve=200-232ms regardless of N). Dla N=3-4
# (bag=0/1) bruteforce z 1-24 permutacjami rozwiązuje natychmiast (<5ms).
# OR-Tools wartościowe TYLKO dla bag>=2 (5-6 nodes, 120-720 permutacji)
# gdzie meta-heuristic GUIDED_LOCAL_SEARCH eksploruje przestrzeń lepiej
# niż naive bruteforce.
# Threshold: bag_after_add >= 2 (bag>=1 + new=1 → OR-Tools; bag=0 → bruteforce).
# Empirically expected -150 to -300ms p95 wall time (D2 ground truth #468613).
V327_MIN_OR_TOOLS_BAG_AFTER = 2  # bag>=1 → OR-Tools; bag=0 → bruteforce fast path

# V3.27.1 BUG-2 — TSP time windows (sprint sesja 1, 2026-04-26).
# Pre-V3.27.1 _ortools_plan przekazywał time_windows=None — TSP minimalizował
# czysty distance ignorując pickup_ready_at, sequencer dawał patologie typu
# 53min wait (case #468733 Chicago Pizza). Adrian's spec: +35min hard close
# zbyt restrictive (częste INFEASIBLE → fallback do bug), +60min blokuje
# patologie i daje solverowi przestrzeń. Wait penalty (ENABLE_V327_WAIT_PENALTY,
# osobny flag) działa SOFT w środku okna; time window działa HARD na +60.
ENABLE_V327_TSP_TIME_WINDOWS = _os.environ.get(
    "ENABLE_V327_TSP_TIME_WINDOWS", "1") == "1"  # V3.27.1 sesja 3 atomic flip 2026-04-26 ~20:35 Warsaw (post Bug 1 fix)
V327_PICKUP_TIME_WINDOW_CLOSE_MIN = 60.0  # +60min od pickup_ready_at hard close
V327_DROP_TIME_WINDOW_MAX_MIN = 120.0  # delivery/courier nodes: luźne okno (effectively no constraint)

# V3.27.1 Wait penalty — Adrian's quadratic table (sprint sesja 1, 2026-04-26).
# W środku okna time_window (60min hard close) działa SOFT scoring penalty
# rosnący quadratically. Decyzja Adriana: sweet spot ≤20 min, +10 pkt/5min do 30,
# +20 do 35, +60 do 40, +100 do 50, +300 do 60 (extrapolacja). Zaplikowane
# per pickup w plan.sequence — sumarycznie do score kandydata. Quadratic
# dyskredytuje sequence z duzym wait, push solver ku tighter scheduling.
ENABLE_V327_WAIT_PENALTY = _os.environ.get(
    "ENABLE_V327_WAIT_PENALTY", "1") == "1"  # V3.27.1 sesja 3 atomic flip 2026-04-26 ~20:35 Warsaw (post Bug 1 fix)
V327_WAIT_PENALTY_TABLE = [
    (20.0, 0.0),       # sweet spot
    (25.0, -10.0),
    (30.0, -30.0),
    (35.0, -90.0),
    (40.0, -150.0),
    (50.0, -400.0),    # ekstrapolacja
    (60.0, -700.0),    # near hard limit (time_window close +60min)
]
V327_WAIT_PENALTY_HARD_FALLBACK = -1000.0  # safety net dla wait > 60min (poza tabelą)

# ============================================================
# B3 (audyt 2026-05-28) — ciągły gradient zamiast sentinela -1000 dla wait>60min.
# Root-cause: nieciągłość -700 (tabela @60) → -1000 (sentinel @60.001) destabilizuje
# ranking blisko progu; flat -1000 dla CAŁEGO wait>60 gubi dyskryminację (61min ==
# 200min ten sam score). Fix: kontynuuj gradient z ostatniego punktu tabeli (-700 @60)
# stromym, CIĄGŁYM nachyleniem do twardego floora. Continuity @60 = -700 (zero klifu).
# slope -40/min: stromiej niż finalny segment tabeli (-30/min @50→60) → zachowuje
# wypukłość (akceleracja kary). floor -2000: decydowanie gorszy niż każda wartość w
# tabeli, ale skończony (nie -inf — to wciąż SOFT scoring signal, nie hard reject).
# HARD safety NIETKNIĘTE: compute_wait_courier_penalty 20min reject + s_obciazenie
# bag-cap→0 zostają (Lekcja-QA-10: binary tylko dla HARD safety). Default OFF — shadow.
# Env: ENABLE_B3_WAIT_GRADIENT=1 / B3_WAIT_GRADIENT_SLOPE_PER_MIN / B3_WAIT_GRADIENT_FLOOR
# ============================================================
ENABLE_B3_WAIT_GRADIENT = _os.environ.get("ENABLE_B3_WAIT_GRADIENT", "0") == "1"
B3_WAIT_GRADIENT_SLOPE_PER_MIN = float(
    _os.environ.get("B3_WAIT_GRADIENT_SLOPE_PER_MIN", "-40.0"))
B3_WAIT_GRADIENT_FLOOR = float(
    _os.environ.get("B3_WAIT_GRADIENT_FLOOR", "-2000.0"))

# ============================================================
# D2 (audyt 2026-05-28) — soft-degrade zamiast BRAK KANDYDATÓW gdy grafik STALE.
# Root-cause: gdy load_schedule() zwróci pusty {} (plik zniknął + fetch fail, albo
# JSON parse fail bez cache), dispatchable_fleet pomija mapowanie shift → cs.shift_end
# zostaje None → feasibility Gate 1 hard-rejectuje WSZYSTKICH (NO_ACTIVE_SHIFT) →
# BRAK KANDYDATÓW na CAŁĄ flotę z powodu awarii pliku, nie realnej niedostępności.
# Fix: gdy grafik wykryty jako STALE (is_schedule_stale() — ten sam 30min próg co
# shift_notifications.worker STALE_SCHEDULE_AGE alert), zamiast hard-reject NO_ACTIVE_SHIFT
# nakładamy SOFT penalty (-75, umiarkowany) i pozwalamy kurierowi przejść feasibility —
# degradacja zamiast total blackout. Soft signal: ranking nadal preferuje kurierów z
# realnym shift mapping, ale awaria grafiku nie blokuje dispatchu w 100%.
# Brak osobnego alertu dispatch — polegamy na istniejącym shift_notifications.worker
# STALE_SCHEDULE_AGE (ten sam sygnał źródłowy). D2 tylko soft-degraduje + loguje metrykę.
# HARD safety NIETKNIĘTE: gdy grafik ŚWIEŻY a shift_end None (realnie brak shiftu) →
# nadal hard reject NO_ACTIVE_SHIFT (Lekcja-QA-10). Default OFF — shadow.
# Env: ENABLE_D2_STALE_SCHEDULE_SOFT=1 / D2_STALE_SCHEDULE_SOFT_PENALTY
# ============================================================
ENABLE_D2_STALE_SCHEDULE_SOFT = _os.environ.get(
    "ENABLE_D2_STALE_SCHEDULE_SOFT", "0") == "1"
D2_STALE_SCHEDULE_SOFT_PENALTY = float(
    _os.environ.get("D2_STALE_SCHEDULE_SOFT_PENALTY", "-75.0"))

# ============================================================
# FAIL-12 (audyt Ziomka 2026-06-03) — grafik (Google Sheet) padł / niepełny →
# fail-OPEN dla kuriera FIZYCZNIE pracującego (aktywny bag LUB świeży GPS).
# ------------------------------------------------------------
# Root-cause: D2 (wyżej) ratuje TYLKO gdy CAŁY arkusz wykryty jako stale (>30min,
# per-flota) ORAZ jest ON. Gdy arkusz jest niepełny / pojedynczy kurier nie ma
# mapowania shift (cs.shift_end=None) przy zdrowym-ale-dziurawym grafiku — albo gdy
# D2 jest OFF (default) — feasibility Gate 1 hard-rejectuje go (NO_ACTIVE_SHIFT,
# fail-CLOSED). Precedens: incident #471036 — 3 aktywnych kurierów odrzuconych.
# Fix: gdy shift_end=None ALE kurier ma TWARDY dowód aktywnej pracy NIEZALEŻNY od
# grafiku (len(bag)>0 — wiezie zlecenia; LUB pos_source=="gps" — realny świeży fix
# ten tick) → fail-OPEN: przepuść przez Gate 1 (degradacja zamiast blackout).
# Świadomy dyskryminator vs FAIL-07: sam BRAK GPS NIE wystarcza (GPS bywa celowo
# nieobecny → odcięcie odcięłoby legalnie pracujących bez apki). Bag/świeży GPS to
# pozytywny sygnał pracy, nie jego brak.
# HARD safety NIETKNIĘTE: R6 35min / SLA / post-shift dalej egzekwowane w dalszej
# części feasibility (fail-OPEN dotyczy WYŁĄCZNIE bramki obecności w grafiku).
# Krok 1 (2026-06-06): pure pass-through + obserwowalność (metryka + GŁOŚNY log).
# BEZ kary scoringowej — w pełnej awarii arkusza wszyscy mają shift_end=None →
# wspólna kara zbiłaby flotę poniżej MIN_PROPOSE_SCORE i odtworzyła blackout miękką
# drogą. Demota w trybie mieszanym = osobna decyzja po danych z cienia.
# Z2 anti-silent-failure: fail-OPEN MASKUJE realną awarię grafiku → log.warning.
# Default OFF — shadow-first. Env: ENABLE_FAIL12_SCHEDULE_FAILOPEN=1
# ============================================================
ENABLE_FAIL12_SCHEDULE_FAILOPEN = _os.environ.get(
    "ENABLE_FAIL12_SCHEDULE_FAILOPEN", "0") == "1"
# Z-06 (audyt 2026-06-10): rescue z last-known-pos store (TTL 25 min) replay'uje
# pierwotny label pos_source="gps" → przechodził gate świeżego GPS w FAIL-12
# ("kurier FIZYCZNIE pracuje — świeży GPS TEN TICK"). Pozycja sprzed ≤25 min to
# NIE jest dowód pracy w tym ticku → strict: pos_source=="gps" and not
# pos_from_store. Bag nadal wystarcza. Env default ON; hot-reload kill-switch:
# flags.json ENABLE_FAIL12_STOREPOS_STRICT=false.
ENABLE_FAIL12_STOREPOS_STRICT = _os.environ.get(
    "ENABLE_FAIL12_STOREPOS_STRICT", "1") == "1"

# V3.27.1 sesja 2 — Pre-proposal czas_kuriera recheck (Mechanizm 3 hybrydowy).
# Per Adrian sesja 2 spec: dla bagu kandydata kuriera, PRZED scoring force fetch
# fresh czas_kuriera z panel jeśli (assignment age >10 min AND last recheck >5 min).
# In-memory cache `_v327_pre_recheck_last_seen` w dispatch_pipeline (Blocker 1 Opcja C
# — clean separation, zero schema migration).
# ZERO max bag limit per Plik wiedzy #1: "BAG caps zawsze per-courier policy, never
# single threshold — hard limits systemically block top performers (Bartek peak bag=8-11)".
# Parallel fetchy via ThreadPoolExecutor(max_workers=len(fetch_oids)) — bez ceiling.
ENABLE_V327_PRE_PROPOSAL_RECHECK = _os.environ.get(
    "ENABLE_V327_PRE_PROPOSAL_RECHECK", "1") == "1"  # V3.27.1 sesja 3 atomic flip 2026-04-26 ~20:35 Warsaw (post Bug 1 fix)
V327_PRE_PROPOSAL_RECHECK_AGE_MIN = 10.0  # skip jeśli order assigned <10 min ago (świeży)
V327_PRE_PROPOSAL_RECHECK_CACHE_TTL_SEC = 300.0  # skip jeśli last recheck <5 min ago
V327_PRE_PROPOSAL_RECHECK_FETCH_TIMEOUT_SEC = 2.0  # 2s budget per fetch (vs default 10s)
V327_PRE_PROPOSAL_RECHECK_CACHE_EVICT_AGE_SEC = 3600.0  # TTL 1h dla in-memory cache eviction
V327_PRE_PROPOSAL_RECHECK_CACHE_EVICT_EVERY = 100  # trigger eviction co 100 calls
V327_PRE_PROPOSAL_RECHECK_CACHE_EVICT_MAX_SIZE = 1000  # OR jeśli cache size > 1000

# ============================================================
# V3.27.3 Wait kuriera penalty (2026-04-27) — kara za idle pod restauracją
# ============================================================
# Hypothesis B + C fix z Task 1 diagnozy #468945. V327 wait_pen używa
# `plan.pickup_at - pickup_ready_at` = ile RESTAURACJA czeka na kuriera (= 0
# dla early arrival po max+dwell logic). NIE wykrywa kuriera idle przed
# restauracją (bag bundling case). Andrei #468945: chain arrival 12:32, ready
# 12:44:57 → real wait kuriera 12.6 min, system widział 0 (sweet-spot ≤20).
#
# Mechanizm V3.27.3:
#   wait_courier_min[oid] = max(0, pickup_ready_at - plan.arrival_at[oid])
#   gdzie plan.arrival_at = chain-aware drive arrival PRZED wait + dwell.
# Linear gradient -10 dla 6 min, -5 per dodatkową minutę aż do 20 min.
# >20 min = HARD REJECT (infeasibility signal).
# Conditional: bag_size_at_insertion >= 1 (kurier ma dowóz w aucie, jedzenie
# stygnie podczas idle). bag=0 skip — kurier wolny i tak czeka na zlecenie.
# Default False — shadow validation period przed flip True.
ENABLE_V3273_WAIT_COURIER_PENALTY = _os.environ.get(
    "ENABLE_V3273_WAIT_COURIER_PENALTY", "1") == "1"  # V3.27.3 flag flip 2026-04-27 wieczór (Adrian ACK post-Task B shadow validation)
V3273_WAIT_COURIER_THRESHOLD_MIN = 3.0   # P3-D2 2026-05-11: tighten 5→3 (Adrian doktryna "kurierzy wolą jeździć niż czekać")
V3273_WAIT_COURIER_FIRST_STEP_PENALTY = -10.0  # at wait=6 (first min above threshold)
# Fix #7 477271 (2026-05-31): steepen -5 → -8 (Adrian „kurier ma jak najmniej czekać
# pod restauracją"). env-tunable; legacy -5 zachowane do shadow-porównania.
V3273_WAIT_COURIER_PER_MIN_PENALTY = float(_os.environ.get(
    "V3273_WAIT_COURIER_PER_MIN_PENALTY", "-8.0"))   # /min powyżej wait=6 (było -5.0)
V3273_WAIT_COURIER_PER_MIN_PENALTY_LEGACY = -5.0     # pre-fix #7 baseline (shadow)
V3273_WAIT_COURIER_HARD_REJECT_MIN = 15.0      # P3-D2 2026-05-11: tighten 20→15 (idle >15 min = unacceptable)
# tech-debt #38 re-scope 2026-05-18 (Adrian): hard-reject wait_courier NIE dla
# wolnego kuriera. Decyzja: "jeżeli kurier jest wolny i nie ma lepszych opcji —
# niech bierze; jeżeli ma 0 w bagu, lepiej czekać 20 min niż stać godzinę".
# Gate: hard-reject (verdict→NO) tylko gdy bag ma order `assigned` (pending pickup,
# picked_up_at is None). Bag pusty / wszystkie picked_up → skip reject, penalty
# bonus_v3273_wait_courier zostaje jako SOFT. True=skip aktywny (default), False=
# kill-switch przywraca stary hard-reject niezależny od bagu.
ENABLE_V3273_WAIT_REJECT_FREE_COURIER_SKIP = _os.environ.get(
    "ENABLE_V3273_WAIT_REJECT_FREE_COURIER_SKIP", "1") == "1"

# N2 (2026-06-17, Adrian ACK + replay dowodowy): hard-reject "stygnące jedzenie"
# ma zależeć od jedzenia REALNIE ODEBRANEGO (picked_up_at != None), nie od liczby
# orderów PRZYPISANYCH. Kurier z workiem samych przypisanych-nieodebranych (jak
# 413 o 12:39: 1 przypisane, 0 odebrane) nie wiezie nic gorącego → fałszywy odrzut.
# Flaga sterowana przez flags.json (C.flag, hot-reload). Gdy ON: licznik reżimu
# hard-reject = liczba ODEBRANYCH w worku; gdy 0 odebranych → brak hard-reject,
# a idle pod restauracją karany ROSNĄCO powyżej progu (soft, bez reject).
# Replay 2026-06-17: ~276 fałszywych odrzutów naprawione, 0 regresji.
V3273_WAIT_IDLE_SOFT_THRESHOLD_MIN = 5.0   # Adrian 17.06: kara idle rośnie powyżej 5 min
V3273_WAIT_IDLE_SOFT_PER_MIN = float(_os.environ.get(
    "V3273_WAIT_IDLE_SOFT_PER_MIN", "-4.0"))   # /min idle powyżej progu (empty-handed, bez reject)

# N5 krok 2 (2026-06-17, Adrian ACK): KARA PUNKTUALNOŚCI COMMITTED w celu solvera.
# Soft upper bound na pickupach z czas_kuriera (obietnica dla restauracji), anchor =
# czas_kuriera + tolerancja. Solver przestaje ślizgać committed dla skrótu jazdy.
# SOFT (SetCumulVarSoftUpperBound) — NIGDY nie INFEASIBLE (lekcja: sztywne ±5 = 7500 INFEASIBLE/d).
# Tolerancja load-aware: strict 5 min / loose 10 min gdy loadgov_ewma ≥ próg (awaryjne, dni jak 16.05).
# Flaga przez decision_flag (flags.json), default OFF. Coeff env-tunable (kalibracja w replayu).
ENABLE_OBJ_COMMITTED_PICKUP_PENALTY = _os.environ.get(
    "ENABLE_OBJ_COMMITTED_PICKUP_PENALTY", "1") == "1"  # L0.1 2026-07-01: default wyrównany do steady-state (json=True, konsumenci przez decision_flag); "0" = mina const≠json
OBJ_COMMITTED_PICKUP_PENALTY_COEFF = float(_os.environ.get(
    "OBJ_COMMITTED_PICKUP_PENALTY_COEFF", "100.0"))
OBJ_COMMITTED_PICKUP_TOL_STRICT_MIN = 5.0
OBJ_COMMITTED_PICKUP_TOL_LOOSE_MIN = 10.0
OBJ_COMMITTED_PICKUP_LOAD_THRESHOLD = 4.5   # loadgov_ewma ≥ to → loosening (Adrian: 50 zleceń/11 std ≈ 4,5)

# ESKALACJA kary committed (Adrian 2026-06-22 D1): "±5 za darmo, od +6 kara MOCNO
# ROSNĄCA o każdą minutę". Pojedynczy SetCumulVarSoftUpperBound jest LINIOWY → drugi
# próg przez OSOBNY WYMIAR (wzorzec food-age: CumulVar==Time) daje karę WYPUKŁĄ
# (eskalującą): tier-1 (+tol, coeff bazowy) + tier-2 (+T2, coeff ostry) → slope rośnie
# za drugim progiem. Flaga env, default OFF — flip po replayu. Łańcuch progów:
#   ≤ ck+tol         : 0
#   ck+tol .. ck+T2  : COEFF / min
#   > ck+T2          : (COEFF + COEFF_T2) / min  ← „mocno rosnąca"
ENABLE_OBJ_COMMITTED_PICKUP_ESCALATION = _os.environ.get(
    "ENABLE_OBJ_COMMITTED_PICKUP_ESCALATION", "0") == "1"
OBJ_COMMITTED_PICKUP_ESCALATION_T2_MIN = float(_os.environ.get(
    "OBJ_COMMITTED_PICKUP_ESCALATION_T2_MIN", "10.0"))   # 2. próg: ck + tol + (T2 - tol) → bound = ck + T2
OBJ_COMMITTED_PICKUP_PENALTY_COEFF_T2 = float(_os.environ.get(
    "OBJ_COMMITTED_PICKUP_PENALTY_COEFF_T2", "400.0"))   # dodatkowy slope za 2. progiem (ostry)

# R-INTRA-RESTAURANT-GAP (HARD, 2026-05-14): max gap między dwoma kolejnymi
# pickupami w tej samej restauracji. Adrian doktryna: kurier nie będzie czekał
# >5 min w tej samej restauracji żeby razem odebrać. Diagnoza propozycji
# K-523 Marcin By Raj→Raj (gap 13 min, wait_courier formuła nie złapała bo
# arrival_at[new]≈ready[new] dla mid-trip same-restaurant insert). Hard reject
# verdict NO gdy gap > MAX_INTRA_RESTAURANT_GAP_MIN dla par (oid_i, oid_j)
# z plan.pickup_at gdzie restaurant(oid_i) == restaurant(oid_j).
ENABLE_INTRA_RESTAURANT_GAP_LIMIT = _os.environ.get(
    "ENABLE_INTRA_RESTAURANT_GAP_LIMIT", "1") == "1"
MAX_INTRA_RESTAURANT_GAP_MIN = 5.0

# ============================================================
# Sprint OBJ F2 — koszt SPAN trasy (idle) w objective solvera TSP (2026-05-18)
# ============================================================
# Naprawia 474253: objective OR-Tools minimalizował SAMĄ jazdę. Czekanie kuriera
# na gotowość pickupu (slack w Time dimension) było w objective DARMOWE → solver
# obojętny między "dojedź i stój 15 min" a "doręcz coś po drodze, dojedź na czas".
#
# Mechanizm: SetSpanCostCoefficientForAllVehicles na Time dimension. Span =
# makespan trasy (cumul end), zawiera slack (idle). coeff×span wchodzi do
# objective → solver unika dead-stopów i konwertuje idle na produktywną jazdę
# (= "throughput per shift", feedback_dispatch_idle_vs_drive).
#
# Zastępuje strukturalnie zepsute P3-D1 (per-edge idle estimate: time_matrix[i][j]
# = pojedyncza krawędź nie skumulowany przyjazd; karał KAŻDĄ krawędź jednakowo;
# perwersyjny incentyw "dłuższy dojazd = mniejsza kara"; magnitudy dominowały
# objective ~6:1 — diagnoza 474253). P3-D1 retired sprintem OBJ F2.
#
# OBJ_SPAN_COST_COEFF = waga 1 min span względem 1 min jazdy w arc-cost.
# coeff=1.0 → 1 min idle kosztuje tyle co 1 min jazdy. Default OFF (env override
# w dispatch-shadow.service). Coeff SKALIBROWANY 2026-05-18 sweepem obj_harness
# (1091 bundli, 797 ortools): span cost tnie idle/span/thermal monotonicznie,
# R6 bez regresji (nie tradeoff). coeff=1.0 = −9,9% idle floty przy umiarkowanej
# dyspersji (14/797 sekwencji); powyżej 1.0 diminishing returns. Default
# zrównany do unit-override. Raport: /tmp/obj_f2_cal/REPORT.md.
ENABLE_OBJ_SPAN_COST = _os.environ.get("ENABLE_OBJ_SPAN_COST", "0") == "1"
OBJ_SPAN_COST_COEFF = float(_os.environ.get("OBJ_SPAN_COST_COEFF", "1.0"))

# OBJM-LEXR6 SHADOW (2026-06-17): obserwacyjny R6-breach-primary lexicographic selektor.
# Pisze top[0].metrics['objm_lexr6_*'] (prefix objm_ auto-serializowany), ZERO wpływu na
# selekcję/werdykt. Faza 1 walidacji selekcji D2 z replay-harness (−577 min twardych spóźnień
# /7d na 54 napr.). Konsumpcja runtime: C.flag("ENABLE_OBJM_LEXR6_SELECT_SHADOW", False)
# (flags.json = KANON live/hot-reload; ta stała = fallback OFF gdy brak klucza). NIE jest
# flagą decyzyjną (ETAP4) — czysta telemetria. Live-flip selekcji = OSOBNA flaga + ACK.
ENABLE_OBJM_LEXR6_SELECT_SHADOW = _os.environ.get("ENABLE_OBJM_LEXR6_SELECT_SHADOW", "0") == "1"

# OBJM-LEXR6 SELECT — FAZA 2 (2026-06-18, live-flip): ZMIENIA faktyczny wybór. Po tier-gate
# sorcie przesuwa R6-breach-primary-lex pick na czoło JEGO grupy (tier×bucket) w `feasible`,
# zanim wybrany zostanie feasible[0]. Zwalidowane Fazą 1 na żywej telemetrii (n=352, G1 Σ=−72min
# R6+committed, G2 regresje 0%, outcome 12/12 delivered). OSOBNA flaga od shadow — domyślnie OFF;
# flip = osobny ACK po re-walidacji live. flags.json = KANON hot-reload (rollback bez restartu);
# ta stała = fallback OFF gdy brak klucza. Konsumpcja: C.flag("ENABLE_OBJM_LEXR6_SELECT", False).
ENABLE_OBJM_LEXR6_SELECT = _os.environ.get("ENABLE_OBJM_LEXR6_SELECT", "0") == "1"

# L6.C2 GEOMETRIA W SELEKCJI (2026-07-04, R2 ROOT-7): człon `deliv_spread_km` jako OSTATNI
# term kanonu objm_lexr6.lex_qual — SOFT tie-break podrzędny wobec całej osi czasowej
# R6→committed→new-late (INV-LAYER-5: geometria NIE osłabia HARD; działa wewnątrz puli,
# którą feasibility już przepuściło). Leczy klasę „279 propozycji spread>8km" (C10-oracle
# 30.06) i jest BRAMKĄ flipu PENDING_RESWEEP_LIVE (de-pile bez geometrii = szkoda).
# Flaga DECYZYJNA (ETAP4, cross-proces przez flags.json); default OFF = krotka bajt-
# identyczna. Konsumenci: WSZYSTKIE ścieżki importujące kanon lex_qual (d2_pick LIVE,
# best_effort_objm, feas_carry_readmit, cień, global_allocate przez assess_order).
ENABLE_LEXQUAL_GEOMETRY_TIEBREAK = _os.environ.get(
    "ENABLE_LEXQUAL_GEOMETRY_TIEBREAK", "0") == "1"
# Kwantyzacja termów czasowych lex_qual do kubełków N-min (0.0=OFF; aktywna TYLKO gdy
# geometria ON). Czysty append rozstrzyga wyłącznie idealne remisy floatów — pod scarcity
# geometria mogłaby nie odpalić nigdy; kubełkowanie zlewa bliskie remisy. Wartość = decyzja
# z pomiaru (replay quant=0 vs 1.0), nie zgadywana. Konsumpcja: C.flag(..., fallback stała).
LEXQUAL_TIME_QUANT_MIN = float(_os.environ.get("LEXQUAL_TIME_QUANT_MIN", "0.0"))

# L6.C3 ENGINE CLAIM LEDGER (2026-07-04, R2 ROOT-8): shadow_dispatcher._tick po PROPOSE
# dla NEW_ORDER dokłada zwycięzcy zlecenie do JEGO worka w snapshocie floty (wspólny
# claim_ledger.tentative_assign — TEN SAM mechanizm co global_allocate resweep/przerzut,
# zero 2. kopii) → kolejne eventy TEGO SAMEGO ticku widzą obciążenie zamiast proponować
# temu samemu kurierowi w nieskończoność (INV-LAYER-4; pomiar: 447 proponowany 127×/32
# zlecenia, g_maxpile=7). Flaga DECYZYJNA (ETAP4); default OFF = flota niemutowana
# (zachowanie sprzed L6.C3, bajt-parytet).
ENABLE_ENGINE_CLAIM_LEDGER = _os.environ.get(
    "ENABLE_ENGINE_CLAIM_LEDGER", "0") == "1"

# INV-FEAS-NO-DOUBLE-BOOK tripwier (Sprint B, 2026-07-08): strażnik spójności
# claim-ledger. _CHECK = log-loud obserwacja (weryfikuje ślad sweepu/ticku, LOGUJE
# naruszenia — NIE zmienia allocation, strażnik nie reguła); _HARD = twarda blokada
# (raise przy naruszeniu) — odłożona za ACK po dowodzie ZERO fałszywek na kanonie +
# realnym korpusie (protokół #0 §5). Oba default OFF; ŚWIADOMIE nie w flags.json
# (wersja live-obserwacji = flip po ACK). Konsument: pending_global_resweep.global_allocate
# + shadow_dispatcher._tick (bliźniaki claim-ledger RAZEM).
ENABLE_CLAIM_LEDGER_INVARIANT_CHECK = _os.environ.get(
    "ENABLE_CLAIM_LEDGER_INVARIANT_CHECK", "0") == "1"
ENABLE_CLAIM_LEDGER_INVARIANT_HARD = _os.environ.get(
    "ENABLE_CLAIM_LEDGER_INVARIANT_HARD", "0") == "1"

# BEST-EFFORT OBJM SHADOW (2026-06-23): ścieżka best_effort (0 feasible) sortuje
# `_best_effort_sort_key` z PRIMARY = r6_per_order_violations (new-pickup-only) — ŚLEPYM na
# carry-ordery (case #482817: 370 wybrany r6_pov=0 mimo objm_breach 58min na 482800). Ten shadow
# loguje co BY wybrała selekcja gdyby PRIMARY był carry-inclusive objm_r6_breach_max (mirror
# _objm_lexr6_shadow), ZERO wpływu na werdykt. Faithful (sticky-aware) — offline capture replay
# daje tylko 36% (sticky nieodtwarzalny). Konsumpcja: C.flag("ENABLE_BEST_EFFORT_OBJM_SHADOW",
# False). flags.json = KANON hot-reload; ta stała = fallback OFF. Telemetria, NIE flaga decyzyjna.
# Live-flip selekcji = OSOBNA flaga ENABLE_BEST_EFFORT_OBJM_R6_KEY + ACK po walidacji.
ENABLE_BEST_EFFORT_OBJM_SHADOW = _os.environ.get("ENABLE_BEST_EFFORT_OBJM_SHADOW", "0") == "1"

# BEST-EFFORT OBJM LIVE-KEY (2026-06-24, ACK Adrian po walidacji shadow): gdy ON, ścieżka
# best_effort (0 feasible) wybiera `best` carry-aware guarded pickiem (_best_effort_objm_pick:
# PRIMARY=objm_r6_breach_max_min, guard new-order bag <= BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN)
# ZAMIAST carry-ślepego _best_effort_sort_key (r6_per_order_violations new-pickup-only, case
# #482817). Walidacja: shadow flip 50% (21-24.06), guard cap35 = 0% regresji nowego / 68% zysku
# carry. NAPRAWIA warstwę best_effort; NIE dotyka feasible-path gate (#483000 = osobny ticket).
# KANON hot = flags.json (rollback bez restartu); ta stała = fallback OFF. Konsumpcja:
# C.flag("ENABLE_BEST_EFFORT_OBJM_R6_KEY", False).
ENABLE_BEST_EFFORT_OBJM_R6_KEY = _os.environ.get("ENABLE_BEST_EFFORT_OBJM_R6_KEY", "0") == "1"

# BEZPIECZNIK nowego zlecenia dla best_effort objm shadow (2026-06-23): carry-aware pick TYLKO
# wśród kandydatów z new-order bag <= cap (max ~5 min ponad R6=35); fallback do pure carry-min gdy
# żaden bezpieczny. Sweep 21-23.06: cap=40 → regresja nowego 27%→16%, zysk carry 83% utrzymany.
# KANON hot = flags.json; ta stała = fallback. Konsumpcja: C.flag("BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN", 40.0).
BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN = float(_os.environ.get("BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN", "40"))

# O2 RE-SEQ tuning (2026-06-27, review 02.07; konsumpcja hot przez C.flag(name, C.NAME)).
# Objektyw O2 = overage(carry ponad cap, READY-anchor) + O2_LAMBDA_CZAS·czas_late(czasówki).
# λ=1.5 = sweet-spot ZATWIERDZONY przez Adriana (bundle_calib, 42 worki — zeruje spóźnienia
# czasówek przy +5min overage). cap=35 walidowany default; 40 na RZADKIE dni niedoboru
# (tier-3, jak 16.05) = strojone z danych 02.07 (jak cap-Z, decyzja Adriana „wybierzemy
# na danych"). cap-Z = TWARDY sufit świeżości niesionego w sweepie (O2 ślepy na pasmo
# 20-35 → bez capa wozi do 90min); wartość z under_z review 02.07 (kandydaci 20/32/35).
O2_LAMBDA_CZAS = float(_os.environ.get("O2_LAMBDA_CZAS", "1.5"))
O2_OVERAGE_CAP_MIN = float(_os.environ.get("O2_OVERAGE_CAP_MIN", "35"))
O2_CAP_Z_MIN = float(_os.environ.get("O2_CAP_Z_MIN", "35"))

# O2 cap-Z RESEQ (2026-07-02, ENABLE_O2_CAPZ_RESEQ, review 02.07 Opcja 3) — progi Z/X/Y
# WYPROWADZONE Z DANYCH `bundle_calib_review_verdict_2026-07-02.txt` (NIE z głowy):
#  • O2_CAPZ_Z_MIN=20 = REKOMENDACJA review (najmniejszy cap dający ≥2% policy-improved =
#    max ochrona niesionego jedzenia; Z≤20: policy-improved 7.9%, med ΔO2 10.4). Semantyka Z:
#    max wiek NIESIONEGO (picked_up) jedzenia = delivered − ready ≤ Z (bundle_calib._max_carried_age).
#  • O2_CAPZ_DETOUR_MAX_MIN=8 = twardy sufit detouru drive-only; review p90 detour dla Z=20 = 7.93
#    (med 0.04) → 8.0 zaokrąglone w górę = utrzymuje ~90% improved, tnie patologiczny ogon.
#  • O2_CAPZ_MIN_GAIN_MIN=2 = materialna redukcja overage by ADOPTOWAĆ (=review MATERIAL_O2_MIN,
#    próg „improved"; unika churnu na trywialnych zyskach).
#  • O2_CAPZ_MAX_STOPS=8 = sufit enumeracji permutacji (koszt wykładniczy); kolektor brute do
#    5 zleceń — powyżej silnik konserwatywnie KEEP (→ engine improved ≤ review, kierunek bezpieczny).
O2_CAPZ_Z_MIN = float(_os.environ.get("O2_CAPZ_Z_MIN", "20"))
O2_CAPZ_DETOUR_MAX_MIN = float(_os.environ.get("O2_CAPZ_DETOUR_MAX_MIN", "8"))
O2_CAPZ_MIN_GAIN_MIN = float(_os.environ.get("O2_CAPZ_MIN_GAIN_MIN", "2"))
O2_CAPZ_MAX_STOPS = int(_os.environ.get("O2_CAPZ_MAX_STOPS", "8"))

# ESKALACJA best_effort (2026-06-23, reguła Adriana 3-stopniowa): gdy 0 feasible (Tier 1
# zawodzi), PRZED rozciąganiem worka (Tier 3) sprawdź Tier 2 = „daj pierwszemu wolnemu"
# (min free_at — kurier kończący obecny worek; nowe odbiera PO rozładowaniu, obecne nietknięte).
# Tier 2 akceptowalny gdy pierwszy-wolny zwalnia się ≤ ten próg (inaczej Tier 3 = cap-stretch).
# SHADOW: log-only pod ENABLE_BEST_EFFORT_OBJM_SHADOW. KANON hot = flags.json.
BEST_EFFORT_ESC_TIER2_MAX_FREE_MIN = float(_os.environ.get("BEST_EFFORT_ESC_TIER2_MAX_FREE_MIN", "30"))

# ETA R3 SHADOW (2026-06-18): residualny model LightGBM koryguje ETA bazową OSRM
# (held-out MAE 9,97→8,63 = −13,4%). FAZA SHADOW: eta_calibration_logger liczy `corrected`
# obok `predicted` i loguje OBA → pomiar MAE(base) vs MAE(corrected) na ŻYWYCH held-out danych.
# ZERO wpływu na feasibility/chain_eta/selekcję (logger off-hot-path, post-hoc). Wpięcie live =
# OSOBNA flaga + ACK PO potwierdzeniu MAE↓ na świeżych danych (uwaga: fałszywy optymizm +1,2%).
ENABLE_ETA_R3_SHADOW = _os.environ.get("ENABLE_ETA_R3_SHADOW", "0") == "1"

# === COORD POISON GUARD flagi (Lekcja #140, 2026-05-21) — default ON ===
# Defense-in-depth, by ten bug NIGDY nie wrócił cicho:
#  - ENABLE_OSRM_COORD_GUARD: osrm_client.route()/table() walidują bbox KAŻDEJ
#    współrzędnej + snap-distance route(); zła współrzędna → sentinel+loud log
#    (NIE realistyczna phantom-trasa). Kill-switch: env=0.
#  - ENABLE_BAG_COORD_REPAIR: dispatch_pipeline._bag_dict_to_ordersim re-geokoduje
#    brakujące/nieprawidłowe współrzędne bag-orderów (ta sama ścieżka co defense
#    gate nowego zlecenia) zamiast (0,0). Kill-switch: env=0.
#  - OSRM_MAX_SNAP_KM: max dystans snapu waypointa OSRM; >próg = punkt nie leży na
#    mapie (np. (0,0)→6225 km) → traktuj jak no-route.
#  - OSRM_INVALID_COORD_SENTINEL_MIN: czas legu dla nieprawidłowej współrzędnej
#    (duży = jawnie infeasible, NIE mylony z realną trasą).
ENABLE_OSRM_COORD_GUARD = _os.environ.get("ENABLE_OSRM_COORD_GUARD", "1") == "1"
ENABLE_BAG_COORD_REPAIR = _os.environ.get("ENABLE_BAG_COORD_REPAIR", "1") == "1"
OSRM_MAX_SNAP_KM = float(_os.environ.get("OSRM_MAX_SNAP_KM", "5.0"))
OSRM_INVALID_COORD_SENTINEL_MIN = float(
    _os.environ.get("OSRM_INVALID_COORD_SENTINEL_MIN", "9999"))

# Sprint OBJ F3 / BUG-4 (2026-05-18): best_effort z najlepszym kandydatem
# łamiącym hard R6 o > próg → verdict KOORD zamiast auto-PROPOSE. Diagnoza
# 474297: kurier R6-doomed (carry 47-82 min), Ziomek proponował trasę-potworka
# zamiast eskalować do koordynatora. Trasa przekraczająca R6 (35 min) o 20+ min
# = dostawa 55+ min = decyzja człowieka, nie propozycja. Próg WYSOKI — nie
# rusza normalnych buforów R-BUFFER-OK (soft zone 30-35). Mierzone
# objm_r6_breach_max_min (route_metrics, anchor=gotowość/picked_up). Default OFF.
ENABLE_OBJ_F3_BEST_EFFORT_R6_KOORD = _os.environ.get(
    "ENABLE_OBJ_F3_BEST_EFFORT_R6_KOORD", "0") == "1"
OBJ_F3_R6_BREACH_KOORD_MIN = float(_os.environ.get(
    "OBJ_F3_R6_BREACH_KOORD_MIN", "20.0"))

# BUG E hotfix (2026-05-26): best_effort fallback gdy >=1 order łamie hard R6
# (35 min) → verdict KOORD, bez progu min-breach jak OBJ_F3 (czyli ANY breach,
# nie tylko 20+ ponad próg). Diagnoza 26.05: 4 z 9 case'ów (D/E/F/G) odjeżdżały
# jako best_effort PROPOSE z bag_times 43-90 min — Adrian akceptował myśląc że
# to sensowny wybór, generując R6 violations dla istniejących orderów. Reguła
# Adriana: „przecież to psuje na 100% dowóz, już lepiej dać 10 min później".
# Liczone z plan.pickup_at/predicted_delivered_at per order (NIE objm_…), bo
# anchor solver'a — dokładnie ten sam horizon co operator widzi. Default ON.
ENABLE_BEST_EFFORT_R6_KOORD_REDIRECT = _os.environ.get(
    "ENABLE_BEST_EFFORT_R6_KOORD_REDIRECT", "1") == "1"

# ALWAYS-PROPOSE ON SATURATION (Adrian 2026-06-15): gdy nie da się dotrzymać 35min
# (best_effort r6_breach/low_score, all_candidates_low_score), Ziomek NIE milczy —
# proponuje najlepszego dostępnego (best_effort, już posortowany) z bannerem
# "⚠️ Best effort", choćby dostawa była >35min. Koordynator nadpisze. Cel: pełna
# autonomia (Z1) — Ziomek radzi sobie nie gorzej od człowieka. KOORD ZOSTAJE tylko
# gdy: early_bird (za wcześnie, wraca do puli) lub PUSTA pula (brak kandydata).
# Hot-reload flags.json; konstanta-default False (kod inertny do flipu).
ENABLE_ALWAYS_PROPOSE_ON_SATURATION = _os.environ.get(
    "ENABLE_ALWAYS_PROPOSE_ON_SATURATION", "0") == "1"

# BEST-EFFORT FASTEST-PICKUP SHADOW (Adrian 2026-06-15): log-only — co BY wybrała
# selekcja „najszybszy odbiór → potem najszybszy dowóz" (PRIMARY = plan.pickup_at[oid]).
# ZERO zmiany live (best dalej _best_effort_sort_key); waliduje w shadow_decisions przed
# ewentualnym flipem. Hot-reload flags.json; konstanta-default False.
ENABLE_BEST_EFFORT_FASTEST_PICKUP_SHADOW = _os.environ.get(
    "ENABLE_BEST_EFFORT_FASTEST_PICKUP_SHADOW", "0") == "1"

# MIN-DELIVERED-AT SHADOW (Adrian 2026-06-25): log-only komparator selekcji na GŁÓWNEJ
# ścieżce feasible — „min predicted_delivered_at[new]" (= min total spóźnienie+dowóz,
# committed stały → najwcześniej do klienta) vs dzisiejszy live winner + regresja floty
# (R6/spread/late) w TEJ SAMEJ decyzji (Pareto). ZERO zmiany decyzji (feasible[0] nietknięte).
# Czytane przez C.flag(); flip obserwacji = flags.json. Hot-reload; konstanta-default False.
ENABLE_MIN_DELIVERED_AT_SHADOW = _os.environ.get(
    "ENABLE_MIN_DELIVERED_AT_SHADOW", "0") == "1"

# BUG A shadow (2026-05-26): Σ bag_time + max bag_time + FIFO penalty w scoring.
# Reguła Adriana: „Suma czasów wszystkich dowozów w bagu jak najmniejsza. Lepiej
# żeby OBA jechały po 15 min, niż jedno 25 a drugie 8. Jeśli podobnie, najpierw
# to co zostało wcześniej odebrane." Solver minimalizuje total_drive_min (geo
# efficiency), nie bag-time fairness — Case #2 (Andersa) TomTom potwierdza
# Adrian wygrywa 15.7 vs 17.2 min mimo wyższego total_drive. Default OFF —
# shadow-first, kalibracja wag po 7-14 dni replay corpus. Wagi startowe per
# SPRINT_PLAN (eod_drafts/2026-05-26/...).
ENABLE_BAG_TIME_FAIRNESS_SCORING = _os.environ.get(
    "ENABLE_BAG_TIME_FAIRNESS_SCORING", "0") == "1"
BAG_TIME_SUM_PENALTY_PER_MIN = float(_os.environ.get(
    "BAG_TIME_SUM_PENALTY_PER_MIN", "1.0"))
BAG_TIME_MAX_PENALTY_PER_MIN = float(_os.environ.get(
    "BAG_TIME_MAX_PENALTY_PER_MIN", "0.7"))
BAG_TIME_FIFO_TIE_PENALTY = float(_os.environ.get(
    "BAG_TIME_FIFO_TIE_PENALTY", "5.0"))

# BUG B shadow (2026-05-26): kara za detour pickup-not-on-route. Reguła Adriana
# „dowóz w żaden sposób nie jest po drodze" (Case C). r5_pickup_detour_total_km
# już zbierane (linia ~2608 dispatch_pipeline) jako metryka obserwacyjna — brak
# negative weight w bonus aggregation. Default OFF. Wagi startowe: penalty 8.0
# pkt/km (~ R4 clip), free threshold 0.5 km (naturalnie po drodze, bez kary).
ENABLE_R5_PICKUP_DETOUR_PENALTY = _os.environ.get(
    "ENABLE_R5_PICKUP_DETOUR_PENALTY", "0") == "1"
R5_DETOUR_PENALTY_PER_KM = float(_os.environ.get(
    "R5_DETOUR_PENALTY_PER_KM", "8.0"))
R5_DETOUR_FREE_THRESHOLD_KM = float(_os.environ.get(
    "R5_DETOUR_FREE_THRESHOLD_KM", "0.5"))
# DETOUR-01 (audyt 03.06): marker ekstremalnego detouru przy worku ≥2 — sama
# obserwowalność (case oid=477347: 9.1 km z dodatnim score); decyzja o vecie
# dopiero po danych z flipu B (werdykt 11.06: 8.0/km bywał już za mocny).
R5_DETOUR_EXTREME_KM = float(_os.environ.get(
    "R5_DETOUR_EXTREME_KM", "7.5"))

# BUG F long-term (2026-05-26): klastry geograficzne (osiedla) — flaga
# ENABLE_CLUSTER_DROP_GROUPING_METRIC USUNIĘTA 2026-07-05 (P-FLAGREG GC za ACK:
# dead od zadeklarowania, 0 konsumentów — sweep B06 D1-1 + re-grep 05.07).
# Sam pomysł (districts_data.py → planowanie po osiedlach, case Kraszewskiego/
# Wąska vs Jaroszówka) zostaje w backlogu jako sprint długoterminowy.

# BUG C (2026-05-26): renderer commit-priority maskuje plan-divergence. Solver
# OR-Tools respektuje [ck-5, ck+5] per pickup independently — może wcisnąć
# pickup na ck+5 mimo że drive Tor→GK = 6 min realnie (Case #3 commit 13:08 +
# Toriko 13:06 = niemożliwe 2 min). Renderer (`_route_lines_v2`) priorytetyzuje
# commit nad plan ETA → pokazuje fikcję bez tyldy. V3274_RENDER_DIVERGENCE_WARN
# (5min) już loguje warning, ale NIE pokazuje operatorowi. Faza 3 marker: gdy
# commit i plan_eta różnią się > próg 3 min (niższy niż 5 min warn — pokazuje
# rosnące napięcie zanim trafi do warning'a) → render `{hhmm}⚠plan~{plan_hhmm}`.
COMMIT_RENDER_DIVERGENCE_TILDE_MIN = float(_os.environ.get(
    "COMMIT_RENDER_DIVERGENCE_TILDE_MIN", "3.0"))

# BUG C verdict-gate eskalacja (2026-05-27): marker `⚠plan~HH:MM` w renderze
# pokazuje operatorowi rozjazd commit-vs-plan, ale verdict nadal PROPOSE/AUTO —
# operator może zatwierdzić "fikcję" jednym kliknięciem. Przy dużym rozjeździe
# (Case #12 27.05: Retrospekcja commit 14:16, plan 14:32, divergence 16 min) =
# realne ryzyko zimnej potrawy / dispatch failure. Gate: gdy max(plan_eta -
# commit) > próg dla dowolnego bag-pickupa → verdict=KOORD (operator decyduje,
# nie auto-PROPOSE z markerem). Próg 10 min = midpoint sprint planu (10/15/20).
# Default ON — strict safety. Env override dla replay/calibration.
ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE = _os.environ.get(
    "ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE", "0") == "1"  # L0.1 D.5 2026-07-01: default "1"→"0" — const kodował PRZECIWNĄ intencję niż flags.json=False (ALWAYS-PROPOSE, werdykt Adriana); utrata klucza json = cichy flip gate'u ON. Efektywnie OFF było i jest; env override dla replay zostaje.
COMMIT_DIVERGENCE_VERDICT_KOORD_MIN_MIN = float(_os.environ.get(
    "COMMIT_DIVERGENCE_VERDICT_KOORD_MIN_MIN", "10.0"))

# R-LATE-PICKUP (2026-05-31, Adrian): twarda reguła — max 5 min spóźnienia na
# ODBIÓR względem zadeklarowanego czasu odbioru. Referencja = committed
# czas_kuriera_warsaw (bag-order lub nowy z firm-commit) | pickup_ready_at (nowy
# bez commitu). Per-pickup hard gate na plan.pickup_at (post-solve, NIE okno TSP
# — lekcja E3 17.05: zaciśnięcie okien TSP → 7.5k INFEASIBLE/dzień → ślepy
# greedy; tu OR-Tools dalej optymalizuje z luźnym oknem, a bramka filtruje
# FINALNĄ pulę po realnym ETA). Komplementarna do R6 (35 min doręczenie,
# BAG_TIME_HARD_MAX_MIN) — DWIE nienaruszalne reguły. Gdy plan_pickup_eta - ref
# > próg → kandydat infeasible (verdict NO, wypada z feasible + z best_effort).
# Reguła Adriana: „lepiej wydłużyć/odroczyć czas odbioru niż złamać te dwie
# reguły"; eliminuje stare propozycje +1h (V327_PICKUP_TIME_WINDOW_CLOSE_MIN=60).
# Metryka late_pickup_max_min liczona ZAWSZE (shadow); reject tylko gdy flag ON.
ENABLE_LATE_PICKUP_HARD_GATE = _os.environ.get(
    "ENABLE_LATE_PICKUP_HARD_GATE", "1") == "1"  # ON od 2026-05-31 (Adrian: widzieć efekt w propozycjach + pomiar shadow)
LATE_PICKUP_HARD_MAX_MIN = float(_os.environ.get(
    "LATE_PICKUP_HARD_MAX_MIN", "5.0"))

# R-LATE-PICKUP Opcja B (2026-05-31) — score-first tiering z miękką karą za późny
# odbiór nowego zlecenia. Naprawia nadkorektę starego tieringu (tier-0 odbiór-na-czas
# bił każdy tier-1 NIEZALEŻNIE od score → krzyżowo-miejskie bundle wygrywały mimo
# −58 R1 korytarz; diagnoza eod_drafts/2026-05-31/SPEC_late_pickup_tiering_fix.md).
# Mechanizm: tier-2 (łamanie committed czas_kuriera) = twardy demote (ostateczność);
# reszta ranking po score (z demote-bucketami V3.16) MINUS gradient kara
# ∝ max(0, new_pickup_late_min − FREE_MIN). Pickup-lateness KONKURUJE z jakością
# dowozu (R6/spread w score), nie DOMINUJE. Adrian (31.05): „lepiej przedłużyć
# 15-20 min i zawieźć w 20 min niż odebrać na czas i wozić 35 min" → kara GENTLE
# (delivery zwykle wygrywa). LIVE default ON; stary tiering liczony równolegle w
# cieniu (late_pickup_shadow) dla porównania efektu. Kalibracja COEFF replay 7-14d.
ENABLE_LATE_PICKUP_TIERING_SCORE_FIRST = _os.environ.get(
    "ENABLE_LATE_PICKUP_TIERING_SCORE_FIRST", "1") == "1"  # ON od 2026-05-31 (Adrian: live + shadow-compare)
LATE_PICKUP_SOFT_FREE_MIN = float(_os.environ.get(
    "LATE_PICKUP_SOFT_FREE_MIN", "5.0"))   # spóźnienie ≤ FREE_MIN → kara 0 (spójne z HARD_MAX)
LATE_PICKUP_SOFT_COEFF = float(_os.environ.get(
    "LATE_PICKUP_SOFT_COEFF", "1.5"))      # pkt kary / min ponad FREE_MIN (gentle: delivery zwykle wygrywa)
LATE_PICKUP_SOFT_CAP = float(_os.environ.get(
    "LATE_PICKUP_SOFT_CAP", "60.0"))       # górny limit kary (zapobiega absurdalnym przedłużeniom)

# Sprint OBJ F0.3 (2026-05-17): replay-capture wejść solvera do offline
# harnessu (zestaw masowy / regresja). Default OFF — włączane env na czas sprintu.
ENABLE_OBJ_REPLAY_CAPTURE = _os.environ.get(
    "ENABLE_OBJ_REPLAY_CAPTURE", "0") == "1"

# Sprint R1+CB+KOORD redirect (2026-05-28): naprawa dwóch tragedii z 28.05
# #476749 Kebab Król → Mieszka I (Adrian Cit, Kaczor→Mieszka→Antoniuk = "Z")
# #476777 Rukola Sienkiewicza → Kraszewskiego 45b (cosine -0.991)
#
# Replay 7d (1170 decyzji, 21-28.05) — R1 progresywny + V319H guard łapie
# 19 historycznych improvements (w tym oba dzisiejsze case'y) przy 2 maybe-
# regresjach (cos<-0.85 + biedny pre_shift pool — adresowane przez KOORD redirect).
#
# R1_PROGRESSIVE_CLIP — istniejący bonus_r1_corridor ma flat clip:
#   cosine <-0.5 → -40, cosine -0.5..0 → -35 (niewystarczająco wobec bonus_l2
#   +11..17 + v319h_bug2_continuation +30). Progresywny:
#   cos<-0.7 → -100, -0.7..-0.5 → -60, -0.5..-0.3 → -45, >=-0.3 → keep.
#
# V319H_CONTINUATION_GUARD — v319h_bug2_continuation_bonus=+30 za "kontynuacja
# fali" maskuje karę kierunku. Guard: gdy cos<-0.3 (drops rozjeżdżają się),
# continuation_bonus nie ma uzasadnienia → zeruj.
#
# DIFFICULT_CASE_KOORD_REDIRECT — gdy R1+CB obniży max score < floor (-30 init),
# wszystkie kandydaty są "trudne geometrycznie", forsowanie złej propozycji =
# operator override / fail. Lepiej redirect KOORD + log do
# difficult_case_log.jsonl (korpus uczenia dla FIX-B / Faza 6 klastry osiedli).
#
# Default OFF (shadow-first). Plan: SHADOW 28.05 wieczór → 29-30.05 verify →
# flip 31.05 → A/B 07.06 → decyzja o FIX-B (cosine-gate, osobny sprint).
# Spec: eod_drafts/2026-05-28/SPRINT_PLAN_r1cb_koord_shadow.md
ENABLE_R1_PROGRESSIVE_CLIP = _os.environ.get(
    "ENABLE_R1_PROGRESSIVE_CLIP", "0") == "1"
ENABLE_V319H_CONTINUATION_GUARD = _os.environ.get(
    "ENABLE_V319H_CONTINUATION_GUARD", "0") == "1"
ENABLE_DIFFICULT_CASE_KOORD_REDIRECT = _os.environ.get(
    "ENABLE_DIFFICULT_CASE_KOORD_REDIRECT", "0") == "1"

# R1 progresywny — empirycznie kalibrowane z 7d replay (n=51 cases z cos<-0.3)
R1_PROGRESSIVE_CRITICAL_COS = float(_os.environ.get(
    "R1_PROGRESSIVE_CRITICAL_COS", "-0.7"))  # cos < -0.7 → drops antypodalne
R1_PROGRESSIVE_HEAVY_COS    = float(_os.environ.get(
    "R1_PROGRESSIVE_HEAVY_COS",    "-0.5"))  # cos < -0.5 → drops mocno apart
R1_PROGRESSIVE_MEDIUM_COS   = float(_os.environ.get(
    "R1_PROGRESSIVE_MEDIUM_COS",   "-0.3"))  # cos < -0.3 → drops lekko apart
R1_PROGRESSIVE_CRITICAL_VAL = float(_os.environ.get(
    "R1_PROGRESSIVE_CRITICAL_VAL", "-100.0"))
R1_PROGRESSIVE_HEAVY_VAL    = float(_os.environ.get(
    "R1_PROGRESSIVE_HEAVY_VAL",    "-60.0"))
R1_PROGRESSIVE_MEDIUM_VAL   = float(_os.environ.get(
    "R1_PROGRESSIVE_MEDIUM_VAL",   "-45.0"))

V319H_GUARD_COSINE_THRESHOLD = float(_os.environ.get(
    "V319H_GUARD_COSINE_THRESHOLD", "-0.3"))

# ── SELECTION VETO — RETIRED 2026-06-11 (ACK Adrian po digescie at#113) ──
# Shadow veto kierunkowego (2026-06-01) usunięty w całości: A2 reliability
# soft-score dowiózł cel (−55% breach na swapowanych, realized R6 14,0→8,7%),
# a werdykt 08.06 wykazał, że veto nadpisywałoby decyzje w większości legalne.
# Historia: eod_drafts/2026-06-08/SELECTION_VETO_VERDICT_2026-06-08.md +
# eod_drafts/2026-06-01/SELECTION_cross_direction_verdict.md.
# Load-aware selection SHADOW (2026-06-07) — log-only, PEŁNY roster.
# Counterfactual: kogo wybrałaby dystrybucja load-aware (najmniej obłożony
# kurier z całego rosteru `candidates`) vs argmax-best. ZERO zmiany zachowania.
# Walidacja offline modelem outcome + cascade harness (eod_drafts/2026-06-07/).
# Flaga default OFF (shadow-first). Aktywacja: override.conf dispatch-shadow.
ENABLE_LOADAWARE_SELECTION_SHADOW = _os.environ.get(
    "ENABLE_LOADAWARE_SELECTION_SHADOW", "0") == "1"
# A2 reliability soft-score (2026-06-07) — dźwignia A2 z audytu autonomii 03.06.
# Kara score ∝ nadwyżka breach_rate kuriera nad medianą floty (confidence-gated).
# Metoda zwalidowana offline: tools/a2_selection_shadow.py (−5..−9pp, better:worse 4.5:1).
# Default OFF -> inert. Flip: override.conf dispatch-shadow + restart po digescie at#113.
ENABLE_A2_RELIABILITY_SOFT_SCORE = _os.environ.get(
    "ENABLE_A2_RELIABILITY_SOFT_SCORE", "0") == "1"
# Coeff 60→100: decyzja Adriana 11.06 po digescie at#113 (σ=0.012 stabilny,
# marginalne swapy 60→100 niemal czysto pozytywne, np. 06-11 +23better/−2worse).
A2_RELIABILITY_COEFF = float(_os.environ.get("A2_RELIABILITY_COEFF", "100"))
# GPS-03/DATA-04 (2026-06-11): gradacja świeżości pozycji. pos_age_min
# (recent-fallback / store-rescue; None = żywy fix lub no_gps) dotąd NIE
# kosztował nic w score — replika sprzed 20 min rywalizowała jak świeży GPS.
# Dyskonto liczone ZAWSZE do bonus_gps_age_discount_shadow (lekcja #186),
# aplikacja za flagą (kanon flags.json, default OFF — kalibracja progów po
# rolloucie apki v2 / realnym wolumenie GPS; pokrewny odłożony GPS-02).
ENABLE_GPS_AGE_DISCOUNT = _os.environ.get("ENABLE_GPS_AGE_DISCOUNT", "0") == "1"
GPS_AGE_DISCOUNT_FREE_MIN = float(_os.environ.get("GPS_AGE_DISCOUNT_FREE_MIN", "5.0"))
GPS_AGE_DISCOUNT_PER_MIN = float(_os.environ.get("GPS_AGE_DISCOUNT_PER_MIN", "0.8"))
GPS_AGE_DISCOUNT_CAP = float(_os.environ.get("GPS_AGE_DISCOUNT_CAP", "12.0"))
# FRONT-B (2026-06-11): pickup_coords liczone NA ŻYWO z address.street panelu
# (geokod cache-first) zamiast zamrożonego restaurant_coords.json (bootstrap
# 11.04 nie podąża za zmianami adresu — incydent Raj/Grill Kebab 05.06).
# Drift cache↔live mierzony ZAWSZE (lekcja #186, log FRONT_B w watcher.log);
# selekcja live-first za flagą (kanon flags.json, OFF). Guardy: wpisy
# source=manual*/adrian_manual* autorytatywne (GEO-02 — często pusty street),
# firmowe konta (FIRMOWE_KONTO_ADDRESS_IDS) poza Front B (pickup w uwagach).
ENABLE_PICKUP_COORDS_FROM_PANEL = _os.environ.get(
    "ENABLE_PICKUP_COORDS_FROM_PANEL", "0") == "1"
PICKUP_COORDS_DRIFT_WARN_M = float(_os.environ.get(
    "PICKUP_COORDS_DRIFT_WARN_M", "150.0"))
A2_RELIABILITY_MIN_GAP = float(_os.environ.get("A2_RELIABILITY_MIN_GAP", "0.05"))
A2_RELIABILITY_FEED_PATH = _os.environ.get(
    "A2_RELIABILITY_FEED_PATH",
    "/root/.openclaw/workspace/dispatch_state/courier_reliability.json")
# R6BREACH-01/GATE-02 — RETIRED 2026-06-11 (Adrian: „duplikat R6 = R6BREACH,
# wytnij"). Guard nigdy nie zebrał danych (flaga OFF od commitu f64ff81,
# 0/2452 rekordów). Oś R6 pokrywają late-pickup gate / OBJ_R6_SOFT_DEADLINE /
# best_effort_r6_breach (OBJ F3) / A2; po flipie BUG-A też kara max_bag_time.
# Difficult case floor — kalibrowane: 2 maybe-regresje z replay miały scores
# post-fixes -55 i -56 (wszystkie kandydaci poniżej -30). Floor -30 = każdy
# kandydat poniżej tej wartości = "trudne geometrycznie" → KOORD redirect.
DIFFICULT_CASE_SCORE_FLOOR = float(_os.environ.get(
    "DIFFICULT_CASE_SCORE_FLOOR", "-30.0"))

# Path dedykowanego logu trudnych przypadków (różny od shadow_decisions.jsonl
# — tu są tylko KOORD redirects, materiał do późniejszej analizy / FIX-B
# kalibracji / Faza 6 klastry osiedli).
DIFFICULT_CASE_LOG_PATH = _os.environ.get(
    "DIFFICULT_CASE_LOG_PATH",
    "/root/.openclaw/workspace/scripts/logs/difficult_case_log.jsonl")

# Sprint OBJ F4 Krok 1 (2026-05-18, Opcja A): proxy pozycji kuriera no-gps.
# Krok 2 build_fleet_snapshot dla ostatniego picked_up ordera ustawiał
# cs.pos = delivery_coords — punkt gdzie kurier DOPIERO DOJEDZIE — więc model
# stawiał go w nieodwiedzonym jeszcze dropie. Realnie kurier jest W TRASIE,
# często bliżej kolejnego pickupu. Skażona macierz odległości → frozen window
# INFEASIBLE → kaskada retry/V3274-reject/greedy (diagnoza 474266, ~7,5k
# INFEASIBLE/dzień). Flaga ON: picked_up → pickup_coords (restauracja, gdzie
# kurier BYŁ o picked_up_at — punkt rzeczywisty, nie ekstrapolacja w przyszłość).
# Fail-soft: gdy brak pickup_coords → delivery_coords (zachowanie sprzed F4).
# Default OFF — env ON po replay-pass. Krok 2 (Opcja C, interpolacja
# pickup→delivery) osobno po shadow-verify. Design:
# eod_drafts/2026-05-18/obj_f4_courier_position_design.md
ENABLE_F4_COURIER_POS_PICKUP_PROXY = _os.environ.get(
    "ENABLE_F4_COURIER_POS_PICKUP_PROXY", "0") == "1"

# Sprint OBJ F4 Krok 2 (Opcja C, 2026-05-19): interpolacja pozycji kuriera
# bez świeżego GPS po nodze pickup→delivery. f = clamp(elapsed/eta_leg, 0, 1),
# gdzie elapsed = now − picked_up_at, eta_leg = OSRM pickup→delivery
# (`osrm_client.route` z cache). cs.pos = pickup + f·(delivery − pickup),
# pos_source = "last_picked_up_interp". Fail-soft (brak coords / brak ts /
# eta=0 / OSRM exception) → caller pada na Krok 1 (pickup_proxy) → legacy
# delivery. Flaga niezależna od Kroku 1: gdy obie ON, interp ma pierwszeństwo
# nad pickup_proxy. Default OFF — env ON po replay + shadow-verify Kroku 1
# (#54 PASS 2026-05-19 21:00 UTC). Hot-path resolvera: 1 wywołanie OSRM per
# kurier no-gps z picked_up — cache OSRM mityguje. Design:
# eod_drafts/2026-05-18/obj_f4_courier_position_design.md
ENABLE_F4_COURIER_POS_INTERP = _os.environ.get(
    "ENABLE_F4_COURIER_POS_INTERP", "0") == "1"

# TZ-FIX checkpointów (2026-06-26): `picked_up_at`/`delivered_at` w orders_state to
# NAIWNY czas Warsaw (panel Rutcom), a 3 miejsca w courier_resolver re-parsowały je
# jako UTC → dla świeżego odbioru elapsed/age UJEMNE → interpolacja pozycji (F4 Krok 2)
# + recent-activity fallback MARTWE (0/16984 wystąpień interp), a ZOMBIE-guard zaniżał
# wiek odbioru o offset Warszawy (~2h). Flaga ON → parse przez parse_panel_timestamp
# (naive→Warszawa) jak granica OrderSim → predykcja pozycji no-GPS ożywa. Default OFF =
# bajt-identyczne (legacy fromisoformat+UTC). Czytane przez courier_resolver._f4_flag
# (flags.json hot-reload + module-global fallback). Design+replay: sprint 2026-06-26.
ENABLE_CHECKPOINT_TS_WARSAW_PARSE = _os.environ.get(
    "ENABLE_CHECKPOINT_TS_WARSAW_PARSE", "0") == "1"

# Sprint OBJ F1 (2026-05-17): R6 soft upper bound w solverze TSP — CumulVar
# węzła delivery > pickup_anchor+35 → kara coeff×overshoot. Sprawia że solver
# respektuje R6 (35 min) gdy się da, a gdy R6-doomed minimalizuje przekroczenie
# (picked-up jedzenie front-loadowane). Default OFF (deploy bez zmiany → flip po
# shadow-verify). Coeff SKALIBROWANY 2026-05-18 sweepem obj_harness (1090 bundli):
# F1 nie jest tradeoffem — soft deadline tnie r6_breach/span/idle naraz; coeff
# nieczuły powyżej ~50, 100 = środek plateau. Default zrównany do unit-override.
ENABLE_OBJ_R6_SOFT_DEADLINE = _os.environ.get(
    "ENABLE_OBJ_R6_SOFT_DEADLINE", "0") == "1"
OBJ_R6_DEADLINE_PENALTY_COEFF = float(_os.environ.get(
    "OBJ_R6_DEADLINE_PENALTY_COEFF", "100"))

# ============================================================
# Sprint OBJ FRESH — świeżość odbioru w objective (2026-05-30)
# ============================================================
# Diagnoza (replay 2026-05-30, n=1627 food-only): objective TSP był ślepy na
# punktualność ODBIORU. Pickup ma tylko dolne ograniczenie (SetRange podbija do
# ready_at), zero kary za odbiór PO gotowości jedzenia. Solver spokojnie parkuje
# odbiór zajętego kuriera grubo po gotowości, bo każda DOSTAWA i tak ląduje przed
# soft-deadlinem. Skala: mediana luzu = +1 min (clamp), ALE ogon: ~31% odbiorów
# projektowanych >5 min po gotowości, ~18% >10 min, max ~50 min (case Sweet&Fit
# +7 = p75). Kara progowa celowana w ogon: aktywna dopiero gdy projektowany
# odbiór > ready_at + THRESHOLD (mediana clamped-to-ready zostaje nietknięta).
# Coeff w jednostkach SetCumulVarSoftUpperBound: kara = coeff×100 per min
# overshoot; 1 min jazdy = 1000 w arc-cost. Coeff=20 → 1 min nieświeżości ponad
# próg ≈ 2 min jazdy (gentle — łamie remisy sekwencji, nie dominuje R6=100).
# LIVE od 2026-05-30 (env ENABLE_OBJ_PICKUP_FRESHNESS=1 w serwisie); pomiar w
# cieniu = pre/post tail z plan.pickup_at w shadow_decisions.jsonl. Rollback =
# usuń env / ustaw 0 (bez redeploy kodu). Default w kodzie OFF (deploy-safe).
ENABLE_OBJ_PICKUP_FRESHNESS = _os.environ.get(
    "ENABLE_OBJ_PICKUP_FRESHNESS", "0") == "1"
OBJ_PICKUP_FRESHNESS_THRESHOLD_MIN = float(_os.environ.get(
    "OBJ_PICKUP_FRESHNESS_THRESHOLD_MIN", "8.0"))
OBJ_PICKUP_FRESHNESS_PENALTY_COEFF = float(_os.environ.get(
    "OBJ_PICKUP_FRESHNESS_PENALTY_COEFF", "20.0"))

# ============================================================
# Sprint OBJ FOOD-AGE — świeżość DOSTAWY w objective (2026-06-14)
# ============================================================
# Diagnoza BUG#5 (Jakub OL 14.06, replay na żywym silniku — courier-routing-bug-
# foodage-2026-06-14): silnik łańcuchował NIEGOTOWY odbiór przed GOTOWĄ dostawą,
# bo cel = arc(jazda) + span(makespan), a makespan IDENTYCZNY w obu kolejnościach
# (wąskie gardło = okno gotowości niegotowego odbioru, nie trasa) → span nic nie
# rozróżnia, cel redukuje się do „min kilometrów" → bliższy niegotowy odbiór
# pierwszy, kurier stoi jałowo, gotowe jedzenie wieziona zimna na końcu. R6
# soft-deadline NIE łapie: obie dostawy poniżej ready+sla → kara 0 w obu.
#
# Fix: człon food-age = liniowa kara za wiek niesionego/gotowego jedzenia
# (delivered − ready). REKONFIGURUJE istniejący delivery soft upper bound (ten
# sam prymityw co R6, wymiar Time → widzi REALNY harmonogram z czekaniem): gdy
# flaga ON, kotwica = czas gotowości (anchor−now, sla=0) zamiast ready+sla, coeff
# = gentle. Monotonicznie zawiera cel R6 (min food-age = min delivery-time = min
# breach). MUSI być na wymiarze Time (nie nowy wymiar bez czekania — pure-transit
# wariant odwraca sygnał: ignoruje 12-min postój który JEST przyczyną). Soft —
# nie wpływa na feasibility. ⚠ REDESIGN 2026-06-14 ADDITIVE: food-age NIE zastępuje
# R6 — to OSOBNY soft bound (wymiar FoodAge==Time w tsp_solver), ADDYTYWNY do R6.
# R6 (ready+sla, coeff 100) chroni SLA; food-age (ready, sla=0, gentle) nudguje
# świeżość TYLKO gdzie R6 obojętne. (Wersja ZASTĘPUJĄCA R6 regresowała SLA 9.4% /
# thermal −5.48 na replay n=891.) Coeff w jedn. SetCumulVarSoftUpperBound: kara =
# coeff×100 per min; 1 min jazdy = 1000 w arc-cost → coeff=3 ≈ 0.3 min jazdy per
# min food-age. COEFF=3.0 SKALIBROWANY sweepem 2026-06-14 (foodage_coeff_sweep.py,
# n=1200): tnie ogon regresji w dużych workach (bag≥3: 8@coeff6 → 6@coeff3)
# zachowując Jakub→B + thermal +2.22; wyższy coeff = więcej zasięgu ale więcej
# szkody bag≥3 i GORSZY thermal. Default OFF (deploy-safe). Workflow: OFF →
# offline replay (foodage_offline_replay_review.py, zero latencji) → flip za ACK.
ENABLE_OBJ_DELIVERY_FOOD_AGE = _os.environ.get(
    "ENABLE_OBJ_DELIVERY_FOOD_AGE", "0") == "1"
OBJ_DELIVERY_FOOD_AGE_COEFF = float(_os.environ.get(
    "OBJ_DELIVERY_FOOD_AGE_COEFF", "3.0"))
# Lewar latencji (2026-06-18): limit czasu warm-startowanego ON-solve hard-SLA.
# 100ms (pół z 200ms base) bo startuje z dobrego planu base; twarde ograniczenie
# gwarantuje SLA niezależnie od jakości → cięcie czasu nie regresuje SLA.
# Numeric-override (flags.json hot-reload) dla tuningu per-miasto/obciążenie.
OBJ_FOOD_AGE_HARD_SLA_ON_SOLVE_MS = float(_os.environ.get(
    "OBJ_FOOD_AGE_HARD_SLA_ON_SOLVE_MS", "100"))
# Forward shadow comparator (2026-06-14): gdy ON, feasibility_v2 re-liczy plan
# best/kandydatów ortools-multistop z food-age ON (thread-local override) i
# loguje rozbieżność OFF↔ON w metrics["food_age_shadow"] — bez zmiany decyzji
# produkcyjnej. Default OFF. Aktywacja: flip + restart dispatch-shadow.
ENABLE_OBJ_DELIVERY_FOOD_AGE_SHADOW = _os.environ.get(
    "ENABLE_OBJ_DELIVERY_FOOD_AGE_SHADOW", "0") == "1"

# ============================================================
# V3.28 FAZA 3 ścieżka A — time_matrix DWELL correction (2026-05-11)
# ============================================================
# OR-Tools time_matrix[i][j] = travel + DWELL_at_arriving_node. Aligns solver
# semantyka z _simulate_sequence pickup_at storage convention (post-DWELL).
# FAZA 0 audit (n=2767, 12 dni od V3.27.4 deploy) confirmed: bag>=2 reject
# rate 34-100% explained by DWELL accumulation not seen by solver. Quantitative
# model fits empirics w lockstep. Predicted post-fix: bag=2 34%→5-10%,
# bag=3 58%→15-25%, bag=4 86%→30-40% (residual ścieżki B bag>=4 calibration).
ENABLE_V328_TIME_MATRIX_DWELL = _os.environ.get(
    "ENABLE_V328_TIME_MATRIX_DWELL", "1") == "1"  # default True post FAZA 0 evidence

# ============================================================
# V3.27.4 Frozen czas_kuriera TSP time window (2026-04-27 wieczór)
# ============================================================
# Naprawia #469014 root cause (TASK F H2): TSP cost = czysta dystans ignorował
# czas_kuriera 16:55 dla Pani Pierożek, planował pickup 17:09 (chain math) bo
# 60-min hard close window pozwalał TSP planować pickup gdziekolwiek w
# [czas_kuriera, czas_kuriera+60].
#
# Mechanizm V3.27.4: dla orderów z committed czas_kuriera (czas_kuriera_warsaw
# != None), TSP time window = [czas_kuriera - 5, czas_kuriera + 5] hard.
# Per Adrian zasada: "czas_kuriera po przypisaniu = nietykalny" (R27 ±5
# margin). Detection logic Adrian's simple pattern: getattr(order,
# czas_kuriera_warsaw, None) is not None — niezależny od pochodzenia
# (first_acceptance lub manual panel change).
#
# Edge case: window_open < 0 (czas_kuriera blisko decision_ts) → clamp na 0
# (Ziomek może planować pickup od now do ck+5).
#
# Risk: minimal. Restricts TSP do permutacji respektujących R27 ±5 dla
# frozen orderów. Jeśli żadna permutacja feasible → kandydat infeasible
# (lepiej szukać innego kuriera niż naruszyć zadeklarowane czas_kuriera).
ENABLE_V3274_FROZEN_PICKUP_WINDOW = _os.environ.get(
    "ENABLE_V3274_FROZEN_PICKUP_WINDOW", "1") == "1"  # default True per Adrian — safety zasada
V3274_FROZEN_PICKUP_WINDOW_MIN = 5.0  # ±5 min od czas_kuriera dla committed orderów

# TIER-1 PICKUP-DEBIAS (2026-06-22) — czas_kuriera jest SYSTEMATYCZNIE OPTYMISTYCZNY
# o ~4.5 min (kurier dojeżdża później; zmierzone out-of-sample na 10 dniach: bias
# med 4.3 / sd 1.2; debias tnie spóźnienie ODBIORU −47% OOS, mediana→~0). PICKUP_DEBIAS_MIN
# = ile dodać do predykcji odbioru by „umówiony" był realistyczny. Konsumpcja: SHADOW
# (flaga `ENABLE_PICKUP_DEBIAS_SHADOW` w flags.json, hot-reload) — shadow_dispatcher
# loguje realistyczny target_pickup OBOK obecnego. ZERO zmiany decyzji/committed ck.
# Live-apply = osobny flag PO walidacji shadow. Risk shadow = zero (tylko pole w logu).
PICKUP_DEBIAS_MIN = float(_os.environ.get("PICKUP_DEBIAS_MIN", "4.5"))

# V3.28 (2026-05-09) — render-side commit priority dla Telegram trasa.
# Bug context (FAZA 0 audit): plan.pickup_at z greedy fallback po V3.27.4 reject
# pokazuje computed ETA chain ignorujący czas_kuriera commit. Render telegram_approver
# iteruje plan.pickup_at jako jedyne źródło → kurier widzi nieprawdziwą trasę
# (np. order 471744: panel commit 13:05 vs render 13:17 = +12 min divergence).
# Fix: render preferuje czas_kuriera_warsaw z bag_context dla committed bag-orders,
# fallback do plan.pickup_at gdy commit None (new orders, pre-acceptance).
# Visual: tilde marker `~HH:MM` dla source="eta", plain HH:MM dla source="commit".
ENABLE_V3274_RENDER_PICKUP_COMMIT_PRIORITY = _os.environ.get(
    "ENABLE_V3274_RENDER_PICKUP_COMMIT_PRIORITY", "1") == "1"  # default True
V3274_RENDER_DIVERGENCE_WARN_MIN = 5.0  # warn gdy |plan_eta - commit| > 5 min

# Floor ETA odbioru w linii „Kandydaci" propozycji do umówionego czas_kuriera
# (Adrian 2026-06-25): kandydat NIGDY nie pokazuje ETA PRZED umówionym (czasówka=czas
# restauracji, elastyk=czas Ziomka „najwcześniej"); dojazd PO umówionym = spóźnienie i
# zostaje. Parytet z konsolą/apką/widokiem restauracji (FLOOR_PICKUP_DISPLAY_TO_AGREED) —
# display-only, silnik/plan nietknięte. Łapie też pre_shift (eta = start zmiany).
# Flaga przeniesiona do flags.json (czytana przez flag() w telegram_approver, default True)
# — parytet wpięcia z bliźniakiem ENABLE_PROPOSAL_ETA_FLOOR_TO_PLAN (koniec env-frozen, 2026-06-25).

# V3.26 Fix 7 (2026-04-25 sobota) — same-restaurant grouping przed TSP.
# Adrian's specification: grupujemy ordery z tej samej restauracji TYLKO gdy
# czas_kuriera ±5 min AND drop quadrants compatible (same lub adjacent w
# BIALYSTOK_DISTRICT_ADJACENCY). Eliminates dual-pickup runs dla compatible
# orders (np. 2 ordery Mama Thai obie centrum gotowe w tym samym oknie).
# Default False — shadow validation period przed flip True.
# D.3 fala B (2026-07-02): KANON=flags.json. Było env-frozen default "1" (jednolicie
# ON) → literał True = stała-fallback steady-state + źródło odczytu dla konsumenta
# (route_simulator_v2:299 getattr tego atrybutu modułu — NIE zmieniany). PARA ATOMOWA
# z ENABLE_V326_OR_TOOLS_TSP (#13 double-insert; check_v326_pair_coherence przy migracji).
ENABLE_V326_SAME_RESTAURANT_GROUPING = True  # V3.27 flip 2026-04-25 wieczór: re-enabled post Bug X+Y+Z+latency fixes
V326_GROUPING_TIME_TOLERANCE_MIN = 5.0  # ±5 min czas_kuriera tolerance

# D.3 fala B: startup-sanity sprzężenia pary V326 (log-only). Przy zdrowym stanie
# (obie ON) = no-op; ostrzeże gdy ktoś w flags.json zostawi OR_TOOLS=OFF przy GROUPING=ON.
try:
    check_v326_pair_coherence()
except Exception:  # pragma: no cover — startup sanity nigdy nie psuje importu
    pass

# ============================================================
# V3.27 Bug Z fix (2026-04-25 wieczór) — bundle cross-quadrant SOFT penalty
# ============================================================
# Bug Z: bundle_level3 corridor logic + drop_proximity_factor scope tylko level1.
# Cross-restaurant bundle (level2/level3) NIE ma quadrant check → cross-quadrant
# bag (np. Bełzy N + Filipowicza SE) traktowany jako "po drodze" mimo 9 km zigzag.
#
# Reproduction: #468509 Chicago Pizza → Artyleryjska, bag Gabriel J z drop
# Bełzy(N) + Filipowicza(Kleosin SE), bundle_level3=True dev=0.21.
#
# Q5 SOFT mnożnik dla SCORE (NIE hard reject):
#   factor=0.0 (cross-quadrant) → score *= 0.1
#   factor=0.5 (adjacent) → score *= 0.7
#   factor=1.0 (same quadrant) → score *= 1.0
#
# Q5a Z-OWN-1 corridor mult: bonus_r4 (po drodze corridor bonus) *= min_factor
#   factor=0.0 → bonus_r4 = 0 (corridor bonus zeroed razem z bundle penalty)
#   factor=0.5 → bonus_r4 *= 0.5
#   factor=1.0 → bonus_r4 unchanged
#
# 'Unknown' zone treatment (Z2): traktuj jako 0.0 (defensive — coverage gap
# w BIALYSTOK_DISTRICTS streets dla wielu adresów: Bełzy, Czarnogórska,
# Skłodowskiej etc. Per Q4 NIE extend coverage w V3.27, defer V3.28 ticket).
#
# Default False — shadow validation. Flip True dopiero po Adrian ACK Krok 3.
# ============================================================
ENABLE_V327_BUG_FIXES_BUNDLE = _os.environ.get(
    "ENABLE_V327_BUG_FIXES_BUNDLE", "1") == "1"  # V3.27 flip 2026-04-25 wieczór: Bug Y tie-breaker + Bug Z bundle penalty + Z-OWN-1 corridor LIVE
V327_BUNDLE_CROSS_QUADRANT_SCORE_MULT = 0.1   # factor=0.0 → score *= 0.1
V327_BUNDLE_ADJACENT_SCORE_MULT = 0.7         # factor=0.5 → score *= 0.7
V327_BUNDLE_SAME_QUADRANT_SCORE_MULT = 1.0    # factor=1.0 → unchanged

# Z-02 (audyt 2026-06-10): sign-guard mnożnika Bug Z + rozdzielenie 'Unknown'
# od realnego cross-quadrant.
#   1. Mnożnik <1.0 na UJEMNYM score ODWRACA karę (×0.1 = 10× poprawa — najgorsze
#      geometrycznie bundle z ujemnym score wygrywały z lepszymi) → mnożymy
#      wyłącznie dodatni score.
#   2. 'Unknown' (luka pokrycia districts) to NIE dowód cross-quadrant — łagodny
#      mult 0.7 zamiast 0.1; realny cross-quadrant wśród ZNANYCH stref zostaje 0.1.
# Env default ON; runtime kill-switch hot-reload: flags.json ENABLE_V327_MULT_SIGN_GUARD=false.
ENABLE_V327_MULT_SIGN_GUARD = _os.environ.get(
    "ENABLE_V327_MULT_SIGN_GUARD", "1") == "1"
V327_BUNDLE_UNKNOWN_SCORE_MULT = float(_os.environ.get(
    "V327_BUNDLE_UNKNOWN_SCORE_MULT", "0.7"))


def bundle_score_multiplier(min_factor):
    """V3.27 Bug Z Q5: map min(drop_proximity_factor) → score multiplier.

    factor=0.0 → 0.1 (cross-quadrant SOFT penalty)
    factor=0.5 → 0.7 (adjacent SOFT penalty)
    factor=1.0 → 1.0 (same quadrant — no penalty)
    intermediate (np. 0.7 jeśli kiedyś dodamy) → linear interpolacja.
    """
    if min_factor is None:
        return V327_BUNDLE_SAME_QUADRANT_SCORE_MULT  # defensive default
    if min_factor <= 0.0:
        return V327_BUNDLE_CROSS_QUADRANT_SCORE_MULT
    if min_factor >= 1.0:
        return V327_BUNDLE_SAME_QUADRANT_SCORE_MULT
    # 0.5 → 0.7 ; intermediate values (linear)
    if min_factor <= 0.5:
        # 0.0..0.5 → 0.1..0.7 linear
        return V327_BUNDLE_CROSS_QUADRANT_SCORE_MULT + (
            (V327_BUNDLE_ADJACENT_SCORE_MULT - V327_BUNDLE_CROSS_QUADRANT_SCORE_MULT)
            * (min_factor / 0.5)
        )
    # 0.5..1.0 → 0.7..1.0 linear
    return V327_BUNDLE_ADJACENT_SCORE_MULT + (
        (V327_BUNDLE_SAME_QUADRANT_SCORE_MULT - V327_BUNDLE_ADJACENT_SCORE_MULT)
        * ((min_factor - 0.5) / 0.5)
    )


def min_drop_proximity_factor(zones):
    """V3.27 Bug Z helper: min pairwise drop_proximity_factor across zone list.

    Args:
        zones: list of zone names (str) — może zawierać 'Unknown'.

    Returns:
        min factor across all unique pairs. None gdy len(zones) < 2.
        'Unknown' traktowany jako 0.0 per Z2 defensive.
    """
    if not zones or len(zones) < 2:
        return None
    n = len(zones)
    min_f = 1.0
    for i in range(n):
        for j in range(i + 1, n):
            f = drop_proximity_factor(zones[i], zones[j])
            if f < min_f:
                min_f = f
    return min_f


def min_drop_proximity_factor_split(zones):
    """Z-02 (audyt 2026-06-10): jak min_drop_proximity_factor, ale rozdziela
    luki pokrycia ('Unknown'/None/pusta strefa) od realnego sygnału geometrycznego.

    Args:
        zones: list of zone names (str) — może zawierać 'Unknown'/None.

    Returns:
        (min_factor_known, has_unknown):
        min_factor_known — min pairwise factor po parach ZNANYCH stref,
            None gdy < 2 znanych stref (brak sygnału geometrycznego),
        has_unknown — True gdy w zones jest co najmniej jedna nieznana strefa.
    """
    if not zones:
        return None, False
    known = [z for z in zones if z and z != 'Unknown']
    has_unknown = len(known) < len(zones)
    if len(known) < 2:
        return None, has_unknown
    n = len(known)
    min_f = 1.0
    for i in range(n):
        for j in range(i + 1, n):
            f = drop_proximity_factor(known[i], known[j])
            if f < min_f:
                min_f = f
    return min_f, has_unknown


def apply_bundle_score_mult(final_score, mult, sign_guard_on=None):
    """Z-02 (audyt 2026-06-10): aplikacja mnożnika Bug Z z guardem znaku.

    Mnożnik <1.0 na UJEMNYM score odwraca karę (−80×0.1=−8 bije −50
    same-quadrant) → przy guardzie ON mnożymy wyłącznie dodatni score.

    Returns:
        (new_score, sign_guarded) — sign_guarded=True gdy mnożnik POMINIĘTY
        przez guard (score ujemny/zero); False przy aplikacji lub mult=1.0.
    """
    if sign_guard_on is None:
        sign_guard_on = ENABLE_V327_MULT_SIGN_GUARD
    if mult == 1.0:
        return final_score, False
    if sign_guard_on and final_score <= 0.0:
        return final_score, True
    return final_score * mult, False


# V3.26 STEP 6 (R-07 v2 CHAIN-ETA ENGINE) — Adrian Q&A 2026-04-24.
# Fundamental change: ETA kandydatów liczy chain walk przez unpicked orders
# w bagu z max(arrival, scheduled) propagacją. Flag-gated use, shadow
# metrics ALWAYS recorded (r07_chain_eta_min, r07_starting_point, etc).
# Replace root cause: synthetic pos (last_assigned_pickup) traktowany jako real.
ENABLE_V326_R07_CHAIN_ETA = _os.environ.get(
    "ENABLE_V326_R07_CHAIN_ETA", "0") == "1"
V326_R07_FRESH_GPS_MAX_AGE_MIN = 2      # GPS fresh threshold (Adrian ACK)
V326_R07_PICKUP_DURATION_MIN = 2         # MVP constant (Adrian ACK); V3.27 per-restaurant
V326_R07_NO_GPS_BUFFER_MIN = 5           # Case 4 no_gps_late buffer (Adrian ACK)
V326_R07_DEFAULT_PREP_MIN = 30           # fallback gdy scheduled=None
V326_R07_HAVERSINE_ROAD_MULT = 2.5       # empirical median 2.461 z 195 orders sample (2026-04-24 08:25)
V326_R07_OSRM_TIMEOUT_MS = 500           # Adrian ACK — fallback haversine jeśli OSRM > 500ms

# V3.26 STEP BUG-3 (R-OSRM-TRAFFIC) — post-OSRM traffic multiplier.
# Self-hosted OSRM (:5001) is free-flow only; Adrian's table (V326_OSRM_TRAFFIC_TABLE
# defined ~line 192) approximates Białystok rush corrections. Default False —
# 24h shadow obs first, Adrian flips True after recalibration with clean
# osrm_raw vs actual data. Flag=False: identical to current behavior, raw OSRM
# passthrough, zero downstream contract change. Stats logged hourly only when
# flag=True (no-op when False).
ENABLE_V326_OSRM_TRAFFIC_MULTIPLIER = _os.environ.get(
    "ENABLE_V326_OSRM_TRAFFIC_MULTIPLIER", "1") == "1"

# Daily Accounting module (V3.25): codzienne rozliczenie kurierów do arkusza
# Controlling / 'Obliczenia' tab. Osobny od dispatch engine, zero coupling na
# scoring/feasibility. Flag=False: main.py exits(0) przy starcie; dry-run path
# pisze JSON do /tmp zamiast Sheets. Flip=True po ACK dry-run weryfikacji 23.04.
ENABLE_DAILY_ACCOUNTING = True


def extension_penalty(planned_pickup_at, restaurant_requested_at):
    """V3.24-A: penalty za delay pickup kuriera vs restaurant-requested time.

    Args:
        planned_pickup_at: datetime — max(naive_eta, shift_start) dla kuriera
        restaurant_requested_at: datetime — czas_odbioru_timestamp (Warsaw TZ
            per CLAUDE.md). Dla czasówki = hard declaration, dla elastyk =
            created_at + czas_odbioru minut.

    Oba argumenty muszą być w tym samym TZ (oba aware lub oba naive Warsaw).
    TZ mismatch wywali się na subtraction — explicit TypeError preferowane
    nad silent wrong result.

    Returns:
        0 → extension ≤ 5 min (ideal match) LUB extension ≤ 0 (kurier
            wcześniej niż restauracja — R-NO-WASTE territory, handled
            przez V3.19j BUG-2 continuation bonus, nie V3.24)
        -10/-50/-100/-200 → gradient per V324_EXTENSION_PENALTY_TIERS
        None → hard reject signal (extension > 60 min), caller musi
            odrzucić kandydata (feasibility layer)
    """
    if planned_pickup_at is None or restaurant_requested_at is None:
        return 0  # incomplete data — conservative, no penalty, no reject
    # TZ fail-fast: naive datetime subtraction across zones daje silent wrong result.
    # Preferujemy explicit TypeError nad cichy bug.
    if planned_pickup_at.tzinfo is None:
        raise TypeError(
            "extension_penalty: planned_pickup_at must be tz-aware "
            "(got naive datetime)"
        )
    if restaurant_requested_at.tzinfo is None:
        raise TypeError(
            "extension_penalty: restaurant_requested_at must be tz-aware "
            "(got naive datetime)"
        )
    extension_min = (
        planned_pickup_at - restaurant_requested_at
    ).total_seconds() / 60.0
    if extension_min <= 0:
        return 0
    if extension_min > V324_HARD_REJECT_EXTENSION_OVER_MIN:
        return None
    for threshold_min, penalty in V324_EXTENSION_PENALTY_TIERS:
        if extension_min <= threshold_min:
            return penalty
    # Defensive fallback: should be unreachable (last tier = 60 min = hard reject border)
    return V324_EXTENSION_PENALTY_TIERS[-1][1]


# ════════════════════════════════════════════════════════════════════
# FIRMOWE KONTO UWAGI PARSER (2026-05-07 sprint)
# ────────────────────────────────────────────────────────────────────
# Konta firmowe (np. Nadajesz.pl id=161) zlecają zamówienia bez adresu
# restauracji w panel address fields — adres pickup'u jest w polu
# "uwagi" (free-text). Parser wyciąga ulicę+numer, geokoduje, wpisuje
# pickup_coords. Defense-in-depth: gate w dispatch_pipeline blokuje
# feasibility loop gdy pickup_coords=None (czytelny operator alert).
#
# Konfiguracja per-tenant ready (Restimo / Wolt Drive future):
# - FIRMOWE_KONTO_ADDRESS_IDS — lista address_id firmowych kont
# - ENABLE_UWAGI_ADDRESS_PARSER flag default True env-overridable
#
# Empirical fixture base: tests/fixtures/uwagi_firmowe.jsonl (25 sampli)
# Patterns: P1 STRUCTURED ~84%, P2 NARRATIVE ~12%, P3 COMPANY-ONLY ~8%
# (P3 = defense gate manual KOORD, brak adresu w uwagach).

FIRMOWE_KONTO_ADDRESS_IDS = frozenset({161})  # Nadajesz.pl firmowe konto

# R-PACZKI-FLEX (2026-05-20) — paczki vs jedzeniówki ground truth.
# 6 kont firmowych identyfikowanych przez address_id (zweryfikowane empirycznie
# events.db 2026-05-20): Nadajesz.pl firmowe (161), Dr Tusz (232), Dentomax (233),
# 3Giga (234), Interpap Polska (235), Orthdruk (236). Paczki nie mają deadline
# restauracyjnego (R-DECLARED-TIME nieaplikowalne, nic się nie psuje).
# Ziomek planuje je elastycznie wokół jedzeniówek z soft cap 2h pickup / 3h delivery
# liczonym od pojawienia się w panelu gastro (created_at_utc z normalize_order).
# WYJĄTEK: czasówki (order_type=='czasowka', prep_minutes>=60) trzymają konkretną
# porę bez względu na konto — R-DECLARED-TIME nadrzędne nad R-PACZKI-FLEX.
PACZKA_ADDRESS_IDS = frozenset({161, 232, 233, 234, 235, 236})
PACZKA_PICKUP_SOFT_CAP_MIN = 120.0    # 2h od created_at gastro
PACZKA_DELIVERY_SOFT_CAP_MIN = 180.0  # 3h od created_at gastro
PACZKA_FLEX_PENALTY_PER_MIN = 1.0     # liniowy, -1 punkt/min nad cap

# Flag default OFF — shadow mode pierwsze 24h, flip True przez flags.json hot-reload.
ENABLE_R_PACZKI_FLEX = _os.environ.get("ENABLE_R_PACZKI_FLEX", "0") == "1"

# PACZKA R6 THERMAL EXEMPT (Adrian 2026-06-15): firmowe paczki (Dr Tusz/tonery,
# Nadajesz.pl, PACZKA_ADDRESS_IDS) to NIE gorące jedzenie → NIE podlegają regule
# 35min (R6 termik). Per-order exempt: paczka NIE ustawia r6_max_bag_time/worst i
# NIE trafia do violations — także w MIESZANYM worku (różnica vs _paczki_only_mix,
# które wymagało CAŁEGO worka-paczek). Jedzeniówka w tym samym worku DALEJ ma 35min.
# Hot-reload via flags.json; konstanta-default False (kod inertny do flipu).
ENABLE_PACZKA_R6_THERMAL_EXEMPT = _os.environ.get("ENABLE_PACZKA_R6_THERMAL_EXEMPT", "0") == "1"

# F2 R1-WAVE-SCOPED DIRECTIONALITY (2026-05-24) — kierunkowość korytarza liczona
# tylko na dropach współistniejących z falą nowego ordera (feasibility_v2 po planie),
# zamiast na całym mieszanym bagu. Root cause korpusu eod_drafts/2026-05-24.
# Default OFF — flip True przez flags.json hot-reload; okno kilkudniowej walidacji.
ENABLE_R1_WAVE_SCOPED_DIRECTIONALITY = _os.environ.get(
    "ENABLE_R1_WAVE_SCOPED_DIRECTIONALITY", "1") == "1"  # L0.1 2026-07-01: default wyrównany do steady-state (json=True; konsument feasibility_v2 czyta getattr OR flag)

# F1 R1-CORRIDOR-GRADIENT (2026-05-24) — kara korytarza R1 jako gradient liniowy
# (0 przy cos=0 → -40 przy cos=-1) zamiast klifu (avg_cos ∈ (-0.5,0] → płaskie -35).
# Sensowne po F2 (czysty wave-scoped cosine). Default OFF — flags.json hot-reload.
ENABLE_R1_CORRIDOR_GRADIENT = _os.environ.get(
    "ENABLE_R1_CORRIDOR_GRADIENT", "0") == "1"

# F5 RETURN-TO-RESTAURANT (2026-05-24) — zakazany powrót do tej samej restauracji
# niosąc jej dowóz (reguła Adriana, Case B korpusu). Detekcja commit-aware w
# feasibility_v2.detect_return_to_restaurant; silna kara (NIE hard veto — gdy jedyny
# kandydat, dostawa > brak). Default OFF — flags.json hot-reload.
ENABLE_R_RETURN_TO_RESTAURANT_VETO = _os.environ.get(
    "ENABLE_R_RETURN_TO_RESTAURANT_VETO", "0") == "1"
RETURN_TO_RESTAURANT_PENALTY = float(
    _os.environ.get("RETURN_TO_RESTAURANT_PENALTY", "100.0"))
RETURN_TO_RESTAURANT_SAME_KM = float(
    _os.environ.get("RETURN_TO_RESTAURANT_SAME_KM", "0.08"))
RETURN_TO_RESTAURANT_GROUP_TOL_MIN = float(
    _os.environ.get("RETURN_TO_RESTAURANT_GROUP_TOL_MIN", "5.0"))

# 2026-05-20 — SLA pre-existing bypass (diagnoza 474863 / Gabryś).
# `plan.sla_violations` reject (feasibility_v2.py linia 679) odrzucał plany dla
# kuriera, którego picked_up order już PRZED `now` przekroczył 35min carry-time
# (kurier jeszcze nie zdążył dostarczyć, drive+dwell zostały > 35 min). Bug: ten
# reject odpalał się ZAWSZE — pre-existing breach trzymał kuriera całkowicie poza
# pool dla nowych orderów, mimo że Gabryś IDEALNIE bundlował 474858+474863 z tej
# samej restauracji (Goodboy). P3-D4 (linia 727) ma delta-logikę (`pu_pred >
# new_pickup_at` = nowy pickup robi detour → reject), ale ona uruchamia się PO
# SLA reject — nigdy nie dochodziła do głosu.
#
# Fix: jeśli WSZYSTKIE violations są picked_up orderami których plan dostarczy
# PRZED `plan.pickup_at[new_order]` (czyli nowy order ZERO wpływu na ich carry),
# bypass SLA reject — niech P3-D4 / per-order R6 / C2 dalej oceniają. New_order
# sam jako violation NIE bypass'uje (to spowodowane planem z nowym).
#
# Flag default ON: bug realny, fix konserwatywny (nie luźni twardych granic dla
# new_order, tylko nie blokuje pre-existing breaches które kurier i tak musi
# obsłużyć). Rollback: env=0 lub flags.json hot-reload.
ENABLE_SLA_PREEXISTING_BYPASS = _os.environ.get(
    "ENABLE_SLA_PREEXISTING_BYPASS", "1") == "1"


def is_paczka_order(order_dict) -> bool:
    """True jeśli order pochodzi z jednego z 6 kont paczkowych.
    Fail-safe: corrupt/None address_id → False (jedzeniówka, surowe R-35MIN-MAX apply).
    """
    if not isinstance(order_dict, dict):
        return False
    aid = order_dict.get("address_id")
    try:
        return int(aid) in PACZKA_ADDRESS_IDS
    except (TypeError, ValueError):
        return False


def is_paczka_flex_eligible(order_dict) -> bool:
    """True gdy paczka kwalifikuje się do R-PACZKI-FLEX (flex soft cap zamiast 35min hard).
    Czasówka (order_type=='czasowka') NIE jest flex — R-DECLARED-TIME nadrzędne.
    """
    if not is_paczka_order(order_dict):
        return False
    if not isinstance(order_dict, dict):
        return False
    return order_dict.get("order_type") != "czasowka"

# Last-resort fallback coords gdy parser uwag zawiedzie (P3 edge / malformed
# uwagi / geocode fail). Source: Adrian decision 2026-05-07 — DMS
# 53°07'56.0"N 23°10'06.4"E (~centrala/baza Nadajesz.pl, Białystok centrum).
# Architecture per Adrian wybór: parser PRIMARY → real geocode (Mickiewicza 50,
# Wyszyńskiego 2/75, etc.); fallback do tej lokalizacji gdy parser zwraca None
# albo geocode fail. Eliminuje BRAK KANDYDATÓW dla firmowych orderów (nawet P3
# edge dostaje real candidates pool zamiast operator KOORD manual).
FIRMOWE_KONTO_FALLBACK_COORDS = (53.13222, 23.16844)

ENABLE_UWAGI_ADDRESS_PARSER = _os.environ.get(
    "ENABLE_UWAGI_ADDRESS_PARSER", "1") == "1"

# Stop-list nazw firm/instytucji które wyglądają jak street ale nim nie są.
# Plausibility check secondary do "musi być cyfra w numerze". Lista
# rozszerzalna — patrz tests/fixtures/uwagi_firmowe.jsonl.
UWAGI_PARSER_COMPANY_STOPLIST = frozenset({
    'mali wojownicy', 'dzielne zuchy', 'drtusz', 'dentomax',
    'orthdruk', 'epaki', 'sempai', '7kick', '7 kick', 'magazyn flm',
    'street sport', 'matka polka hybrydowa', 'kanro ltd', 'pam bis',
    'apteka pod lwem', 'firma kinga', 'lakor', 'sprzęt agd',
    'studio galeria tattoo studio', 'nzoz dentos', 'puh red-bud',
    'jaglanka', 'jacek okułowicz', 'biegły rzeczoznawca',
    'redakcja niwa', 'garmond press', 'poczta polska', 'ziemkowska clinic',
    'firma ewtex', 'galeria', 'red chilli kebab', 'drapieżnik',
    '3giga', 'mali wojownicy', 'stomatologia zyta',
})

# Defense gate: parser_health morning calibration (companion fix dla
# false-positives 07.05 08:37/08:42 ZERO + 09:11 DELTA +100% przy 1→2).
PARSER_HEALTH_STUCK_MIN_HOUR_WARSAW = 9   # nie alert pre-09:00 Warsaw
PARSER_HEALTH_STUCK_MIN_BASELINE = 3       # min active orders dla STUCK
PARSER_HEALTH_DELTA_MIN_ABS_DIFF = 3       # min |curr-prev| dla DELTA

# R6 BAG_TIME pre-warning Telegram alert (sla_tracker._check_bag_time_alerts).
# Adrian decision 2026-05-07: domyślnie OFF — alert "Kurier wiezie zamówienie
# już >30 min" był noisem (Adrian sam monitoruje przez panel). Hot-reload via
# flags.json: ENABLE_BAG_TIME_ALERTS=true odwraca. Scan no-op gdy False.
# R6 hard reject downstream w feasibility_v2 (BAG_TIME_HARD_MAX_MIN=35) NIE
# dotknięty — algorytm dispatch dalej respektuje termiczny cap.
ENABLE_BAG_TIME_ALERTS = _os.environ.get(
    "ENABLE_BAG_TIME_ALERTS", "0") == "1"


# ─────────────────────────────────────────────────────────────────────────────
# Sprint 2 Etap 2.2 (2026-05-27): carry / bag-stack visibility feature.
# Forensic Agent D (/tmp/kebab_krol_diagnostic.md):
#   - Kebab Król R6 breach 22.5% w dinner peak (vs 7-8% baseline)
#   - Carry penalty mechanism = KK siedzi 15-30 min w torbie gdy kurier
#     dostarcza inną restaurację pierwszą (cross-restaurant bag chain).
# Feature: penalty proporcjonalny do ETA pickup nowego zlecenia gdy kurier ma
# już w torbie zlecenie Z INNEJ restauracji + ETA > threshold; hard reject gdy
# wiele chain stops + dinner peak + restauracja w CARRY_RISK_LIST.
# Default FLAG OFF — wymaga 14d shadow przed flip.
ENABLE_CARRY_CHAIN_PENALTY = _os.environ.get(
    "ENABLE_CARRY_CHAIN_PENALTY", "0") == "1"

# Coefficient calibration starting point (Agent D KK dinner carry ~15-30 min).
# Penalty (negative) = -COEFF * eta_pickup_min when chain detected.
# 1.5 × 15 min carry = -22.5 pkt; 1.5 × 30 min = -45 pkt. Sweep w shadow.
CARRY_CHAIN_PENALTY_COEFF = float(_os.environ.get(
    "CARRY_CHAIN_PENALTY_COEFF", "1.5"))

# ETA threshold (min) — gdy nowy pickup ETA <= próg, brak penalty (carry mały).
# Default 15: KK breach pattern Agent D pokazał carry 15-30 min jako problem.
CARRY_CHAIN_ETA_THRESHOLD_MIN = float(_os.environ.get(
    "CARRY_CHAIN_ETA_THRESHOLD_MIN", "15.0"))

# Hard reject thresholds — wiele "chain stops" w dinner peak + restauracja
# wysokiego ryzyka = HARD reject (feasibility-side bypass). Bag stops counted
# jako liczba DIFFERENT restauracji w bagu kuriera względem nowego pickup'u.
CARRY_CHAIN_HARD_REJECT_STOPS = int(_os.environ.get(
    "CARRY_CHAIN_HARD_REJECT_STOPS", "2"))

# Warsaw hour window dla hard reject (dinner peak; same okno co KK exclusion).
CARRY_CHAIN_DINNER_START_HOUR_WARSAW = int(_os.environ.get(
    "CARRY_CHAIN_DINNER_START_HOUR_WARSAW", "17"))
CARRY_CHAIN_DINNER_END_HOUR_WARSAW = int(_os.environ.get(
    "CARRY_CHAIN_DINNER_END_HOUR_WARSAW", "21"))

# Frozen set restauracji wysokiego ryzyka carry. Rozszerzalne. Start tylko KK.
# Lower-case normalized; matching case-insensitive substring (per KK fix Etap 2.1).
CARRY_RISK_LIST = frozenset({
    "kebab król",
})


def _norm_restaurant_for_carry_match(name) -> str:
    """Lower-case + strip dla matchingu CARRY_RISK_LIST. Defensive None/non-str."""
    if not name:
        return ""
    try:
        return str(name).strip().lower()
    except Exception:
        return ""


def is_carry_risk_restaurant(name) -> bool:
    """True gdy restaurant_name pasuje (substring case-insensitive) do CARRY_RISK_LIST.

    Substring match (nie exact) by łapać warianty "Kebab Król - Sienkiewicza 73"
    vs "Kebab Król 2" itd. Defensive: None / pusty / non-str → False.
    """
    norm = _norm_restaurant_for_carry_match(name)
    if not norm:
        return False
    return any(risk in norm for risk in CARRY_RISK_LIST)


def carry_chain_penalty(
    bag_restaurants,
    new_restaurant_name,
    eta_pickup_min,
    coeff=None,
    threshold_min=None,
):
    """Pure carry-chain penalty calculation. Returns (penalty, chain_stops, applied).

    Args:
        bag_restaurants: iterable nazw restauracji w bagu kuriera (bag_size_before).
            None values / pustki są filtrowane.
        new_restaurant_name: nazwa nowego pickup'u (case-insensitive porównanie).
        eta_pickup_min: predicted minutes do nowego pickup (>=0; gdy None → 0.0).
        coeff: penalty multiplier (default CARRY_CHAIN_PENALTY_COEFF).
        threshold_min: ETA below threshold → no penalty (default CARRY_CHAIN_ETA_THRESHOLD_MIN).

    Returns:
        (penalty: float, chain_stops: int, applied: bool)
        penalty <= 0 (negative gdy applied, 0.0 gdy no-op).
        chain_stops = liczba bag items z DIFFERENT restaurant niż new.
        applied = True gdy chain_stops>=1 AND eta > threshold.

    Pure: brak I/O, brak side-effectów, deterministyczne dla identycznych args.
    """
    if coeff is None:
        coeff = CARRY_CHAIN_PENALTY_COEFF
    if threshold_min is None:
        threshold_min = CARRY_CHAIN_ETA_THRESHOLD_MIN

    eta = 0.0
    try:
        eta = float(eta_pickup_min) if eta_pickup_min is not None else 0.0
    except (TypeError, ValueError):
        eta = 0.0

    new_norm = _norm_restaurant_for_carry_match(new_restaurant_name)
    chain_stops = 0
    for r in (bag_restaurants or []):
        bag_norm = _norm_restaurant_for_carry_match(r)
        if not bag_norm:
            continue
        if bag_norm != new_norm:
            chain_stops += 1

    if chain_stops <= 0:
        return 0.0, 0, False
    if eta <= float(threshold_min):
        return 0.0, chain_stops, False

    penalty = -float(coeff) * eta
    return penalty, chain_stops, True


def carry_chain_hard_reject(
    chain_stops,
    new_restaurant_name,
    now_utc=None,
    min_stops=None,
    dinner_start=None,
    dinner_end=None,
):
    """Pure hard-reject decision. Returns True gdy:
       chain_stops >= min_stops AND warsaw_hour ∈ [dinner_start, dinner_end) AND
       new_restaurant_name jest w CARRY_RISK_LIST.

    Defensive: now_utc=None → datetime.now(timezone.utc). Wszystkie configi
    overridable per call (testowalne) lub z module-level constants.
    """
    if min_stops is None:
        min_stops = CARRY_CHAIN_HARD_REJECT_STOPS
    if dinner_start is None:
        dinner_start = CARRY_CHAIN_DINNER_START_HOUR_WARSAW
    if dinner_end is None:
        dinner_end = CARRY_CHAIN_DINNER_END_HOUR_WARSAW

    if int(chain_stops or 0) < int(min_stops):
        return False
    if not is_carry_risk_restaurant(new_restaurant_name):
        return False

    now_utc = now_utc or datetime.now(timezone.utc)
    try:
        warsaw_hour = now_utc.astimezone(WARSAW).hour
    except Exception:
        return False
    return int(dinner_start) <= warsaw_hour < int(dinner_end)
