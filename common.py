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
        (0, 6, 1.0),
        (6, 8, 1.0),
        (8, 10, 1.1),
        (10, 12, 1.1),
        (12, 13, 1.2),
        (13, 15, 1.3),
        (15, 17, 1.6),   # peak Białystok
        (17, 19, 1.2),
        (19, 21, 1.1),
        (21, 24, 1.0),
    ],
    # V3.27 Bug X fix (2026-04-25 wieczór): split weekend → saturday/sunday.
    # Pre-fix: weekend=1.0 flat całą dobę → matrix=raw OSRM free-flow w sobotni
    # peak 16-21 → 30-50% pod-estymata timing (#468508/#468509 reproduction).
    # Adrian's decyzja: sobota peak 12-21 conservative (max 1.2), niedziela płaska 1.0.
    # Memory user: "Pn-Pt 11-14 + 17-20, sobota 16-21 (długi peak), niedziela TBD".
    "saturday": [   # SAT (weekday()==5)
        (0, 12, 1.0),
        (12, 15, 1.1),
        (15, 17, 1.2),
        (17, 21, 1.2),
        (21, 24, 1.0),
    ],
    "sunday": [     # SUN (weekday()==6) — drogi puste, zero peak weekend
        (0, 24, 1.0),
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

# Feature flags — V3.24-A flipped True 2026-04-22 B14b (post dinner peak).
# V3.24-B flipped True 2026-04-22 B14c + systemd timer enabled.
ENABLE_V324A_SCHEDULE_INTEGRATION = _os.environ.get(
    "ENABLE_V324A_SCHEDULE_INTEGRATION", "1") == "1"
ENABLE_V324B_CZASOWKA_SCHEDULER = _os.environ.get(
    "ENABLE_V324B_CZASOWKA_SCHEDULER", "1") == "1"

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
# Dropoff hard reject: planned_dropoff > shift_end + 5 min
# (parallel do V3.24-A V324_HARD_REJECT_DROPOFF_AFTER_SHIFT_MIN, V3.25
# zachowuje to ale flag-gated osobno dla rollout independence).
V325_DROPOFF_AFTER_SHIFT_HARD_MIN = 5

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
    'gold':  0.889,  # backtest 8,108 deliveries (Mateusz O, Bartek O, Gabriel)
    'std+':  1.056,  # backtest 4,837 (Jakub OL, Adrian R) — distance bias suspected
    'std':   1.000,  # baseline (always 1.0)
    'slow':  1.111,  # backtest 1,895 (Łukasz B, Michał Li, Artsem Km)
    'new':   1.300,  # policy default — n=739 empirical insufficient (Adrian Q&A "duuużo czasu")
}
# Score adjustment = (1.0 - multi) * SCORE_FACTOR.
# gold (0.889) → +5.55 score boost, slow (1.111) → -5.55 penalty, new (1.30) → -15.
V326_SPEED_SCORE_FACTOR = 50.0

# V3.26 STEP 3 (R-09 WAVE-GEOMETRIC-VETO) — refinement V3.19h BUG-2.
# Bug case (Adrian Q&A 22.04 Kacper Sa): wave_continuation +30 fire'uje gdy
# gap OK (free_at 5min after pickup wave#2) ALE drops rozrzucone na 2 końce
# miasta (>5km haversine). Veto bonus jeśli geographical incoherence.
ENABLE_V326_WAVE_GEOMETRIC_VETO = _os.environ.get(
    "ENABLE_V326_WAVE_GEOMETRIC_VETO", "1") == "1"
# Threshold km od last_drop do new_pickup powyżej którego BUG-2 bonus zostaje
# zveto'wany. 3.0 km = ~5 min ride w Bialymstoku — krzyżowanie ½ miasta.
V326_WAVE_VETO_KM_THRESHOLD = 3.0

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
ENABLE_V326_OR_TOOLS_TSP = _os.environ.get(
    "ENABLE_V326_OR_TOOLS_TSP", "1") == "1"  # V3.27 flip 2026-04-25 wieczór: re-enabled post Bug X+Y+Z+latency fixes
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
V3273_WAIT_COURIER_THRESHOLD_MIN = 5.0   # sweet spot ≤5 min (Adrian R27 ±5 margin)
V3273_WAIT_COURIER_FIRST_STEP_PENALTY = -10.0  # at wait=6 (first min above threshold)
V3273_WAIT_COURIER_PER_MIN_PENALTY = -5.0      # +5 penalty per min above wait=6
V3273_WAIT_COURIER_HARD_REJECT_MIN = 20.0      # wait >20 → HARD REJECT (infeasible)

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

# V3.26 Fix 7 (2026-04-25 sobota) — same-restaurant grouping przed TSP.
# Adrian's specification: grupujemy ordery z tej samej restauracji TYLKO gdy
# czas_kuriera ±5 min AND drop quadrants compatible (same lub adjacent w
# BIALYSTOK_DISTRICT_ADJACENCY). Eliminates dual-pickup runs dla compatible
# orders (np. 2 ordery Mama Thai obie centrum gotowe w tym samym oknie).
# Default False — shadow validation period przed flip True.
ENABLE_V326_SAME_RESTAURANT_GROUPING = _os.environ.get(
    "ENABLE_V326_SAME_RESTAURANT_GROUPING", "1") == "1"  # V3.27 flip 2026-04-25 wieczór: re-enabled post Bug X+Y+Z+latency fixes
V326_GROUPING_TIME_TOLERANCE_MIN = 5.0  # ±5 min czas_kuriera tolerance

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
