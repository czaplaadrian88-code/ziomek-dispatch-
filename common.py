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
# Flag controls whether downstream consumers (C5 wave_scoring) read the file.
# Currently False → C5 not yet integrated. Tracker can run standalone for data collection.
ENABLE_SPEED_TIER_LOADING = False

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
