"""Wspólne narzędzia: config loader, logger, paths."""
import json
import logging
import os
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

SCRIPTS_DIR = Path("/root/.openclaw/workspace/scripts")
CONFIG_PATH = SCRIPTS_DIR / "config.json"
FLAGS_PATH = SCRIPTS_DIR / "flags.json"

_config_cache = None
_config_mtime = 0
_flags_cache = None
_flags_mtime = 0


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
    """Hot-reload flags.json jesli sie zmienil."""
    global _flags_cache, _flags_mtime
    mtime = FLAGS_PATH.stat().st_mtime
    if _flags_cache is None or mtime > _flags_mtime:
        with open(FLAGS_PATH) as f:
            _flags_cache = json.load(f)
        _flags_mtime = mtime
    return _flags_cache


def flag(name: str, default=False) -> bool:
    """Szybki odczyt flagi z hot-reload."""
    return load_flags().get(name, default)


def now_utc():
    return datetime.now(timezone.utc)


def now_iso():
    return now_utc().isoformat()


def setup_logger(name: str, log_file: str = None):
    """Prosty logger z file handlerem."""
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
        fh = logging.FileHandler(log_file)
        fh.setFormatter(fmt)
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
MAX_BAG_SANITY_CAP = 8


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
HAVERSINE_ROAD_FACTOR_BIALYSTOK = 1.37

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

# ─── R7 (H4): Long-haul isolation w peak hours ───
# Placeholder — brak danych empirycznych na ride_distance w shadow_decisions.
# Post-deploy monitoring: jeśli R7 trigger rate > 20% w peak 14-17, próg za niski.
LONG_HAUL_DISTANCE_KM = 99.0  # F2.1c: R7 wyłączone — 4.5km było za agresywne dla Białystoku
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

# ─── A1/A2: Anomaly detection (flag off, implementacja w F2.1c) ───
# Wymaga implementacji context.restaurant_prep_variance() i
# context.courier_recent_delay() na bazie restaurant_meta.json / shadow_decisions.jsonl.
RESTAURANT_PREP_VARIANCE_HARD_MIN = 15
COURIER_RECENT_DELAY_HARD_MIN = 10
COURIER_CIRCUIT_BREAK_PENALTY = 25
ANOMALY_DETECTION_ENABLED = False

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

# C4: speed_tier_tracker.py produces courier_speed_tiers.json (nightly).
# _PLANNED suffix marks flag bez zaimplementowanego konsumenta w prod.
# Consumer w courier_resolver.build_fleet_snapshot (arch spec 3.3 CourierState.speed_tier)
# nie istnieje — flip tej flagi na True NIE ma efektu bez implementacji.
# Rename 2026-04-20 V3.19e pre-work: TECH_DEBT rule "flag bez konsumenta = _PLANNED suffix".
ENABLE_SPEED_TIER_LOADING_PLANNED = False

# Future flags (C3, C5-C7), default False at deploy:
DEPRECATE_LEGACY_HARD_GATES = False  # C3: R1/R5/R6/R7/R8 → soft penalties
ENABLE_WAVE_SCORING = False           # C5: wave_scoring.py module

# C5 shadow mode: observational diff logging regardless of ENABLE_WAVE_SCORING.
# When True, wave_scoring computes adjustment and emits C5_SHADOW_DIFF event
# to dispatch_state/c5_shadow_log.jsonl when adjustment magnitude > threshold.
ENABLE_C5_SHADOW_LOG = True

ENABLE_MID_TRIP_PICKUP = False        # C6: state_machine rewake for overlap
ENABLE_PENDING_QUEUE_VIEW = False     # C7: dispatch_pipeline signature change

# ============================================================
# Telegram Transparency OPCJA A flags (2026-04-19)
# Redesign propozycji — Adrian chce rozumieć CZEMU ten kurier i
# JAKĄ TRASĘ wykona. L2 label "blisko: X" był mylący (sugeruje
# że kurier odbiera z X, a to bundling do istniejącej fali).
# ============================================================
ENABLE_TRANSPARENCY_ROUTE = True       # Route section (pickupy then drops) w propozycji
ENABLE_TRANSPARENCY_REASON = True      # Natural-language reason line (czemu ten kurier)
ENABLE_TRANSPARENCY_SCORING = True     # Score decomposition (baza + wave + bundle)

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
ENABLE_PANEL_IS_FREE_AUTHORITATIVE = _os.environ.get("ENABLE_PANEL_IS_FREE_AUTHORITATIVE", "1") == "1"
ENABLE_BUNDLE_VALUE_SCORING = _os.environ.get("ENABLE_BUNDLE_VALUE_SCORING", "0") == "1"

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
    'Mickiewicza':    {'Centrum', 'Dojlidy', 'Kawaleryjskie', 'Piaski',
                       'Piasta II', 'Skorupy', 'Bojary', 'Dojlidy Górne'},
    'Sienkiewicza':   {'Wygoda', 'Bojary', 'Centrum', 'Białostoczek',
                       'Wasilków', 'Jaroszówka'},
    # E/SE Dojlidy kierunek
    'Dojlidy':        {'Skorupy', 'Mickiewicza', 'Dojlidy Górne', 'Centrum'},
    'Dojlidy Górne':  {'Dojlidy', 'Mickiewicza'},
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


def drop_zone_from_address(addr, city=None):
    """V3.19h BUG-1: address + city → district name.

    Outside-city wykrywane z `city` field (miejscowość_docelowa z CSV).
    Białystok: match po ulicy w BIALYSTOK_DISTRICTS (prefix/substring match).
    Fallback: 'Unknown' gdy brak confident match.
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
    # Strip leading "ul." / "al." / "pl." prefix
    for prefix in ('ul. ', 'al. ', 'aleja ', 'plac ', 'pl. '):
        if addr_lc.startswith(prefix):
            addr_lc = addr_lc[len(prefix):]
            break
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
      < 0 → full bonus (anticipation — pickup przed last drop)
      0-10 inclusive → linear decay (0 → 30, 10 → 0)
      > 10 → 0
    """
    if gap_min is None:
        return 0.0
    if gap_min < 0:
        return BUG2_WAVE_CONTINUATION_BONUS
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

# Feature flags — default False (pre-deploy observational)
ENABLE_V324A_SCHEDULE_INTEGRATION = _os.environ.get(
    "ENABLE_V324A_SCHEDULE_INTEGRATION", "0") == "1"
ENABLE_V324B_CZASOWKA_SCHEDULER = _os.environ.get(
    "ENABLE_V324B_CZASOWKA_SCHEDULER", "0") == "1"


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
