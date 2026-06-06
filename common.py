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

# V3.28 ANCHOR FIX 2026-05-10 — Adrian doktryna: PROPOSE quality threshold.
# Gdy best.score < MIN_PROPOSE_SCORE → verdict=KOORD reason=all_candidates_low_score.
# Background: 2026-05-10 472189 PROPOSE Andrei score=-50 mimo Mateusz Bro alt -1047
# (best of bad). Operator override 89% — system proponuje gdy realnie wszyscy źli.
# Próg -100 = "tylko ekstremalne sub-optymalne (jak -1047) lecą do KOORD".
# Lekko ujemne propozycje (peak day rescue) zostają PROPOSE.
MIN_PROPOSE_SCORE = -100.0

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

# V3.28 Fix 6 (incident 03.05.2026): mass fail fallback heuristic.
# Gdy >=50% kurierów crash w _v327_pool (OR-Tools mass fail) → trigger
# simple proximity+tier heuristic. NIE używa OR-Tools więc nie crashuje.
# Default True (safety net). Env override: ENABLE_V328_MASS_FAIL_FALLBACK=0
# disable (mass fail wraca do silent NO_PROPOSE).
ENABLE_V328_MASS_FAIL_FALLBACK = _os.environ.get("ENABLE_V328_MASS_FAIL_FALLBACK", "1") == "1"
V328_MASS_FAIL_RATIO_THRESHOLD = float(_os.environ.get("V328_MASS_FAIL_RATIO_THRESHOLD", "0.5"))
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
        if ENABLE_C2_NEG_GAP_DECAY:
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

# Tier-aware DWELL (2026-05-17). Postój kuriera = OBSŁUGA stopu.
# E1 sprint 2026-05-17 (Adrian): postój pod restauracją to czysta obsługa
# (chwyć torbę) ~1 min — NIE czekanie na jedzenie (to liczy pickup_ready_at
# osobno). Stąd pickup = flat DWELL_PICKUP_FLAT_MIN dla WSZYSTKICH tierów.
# Dropoff (handoff u klienta) zostaje tier-aware: szybszy tier = krótszy postój.
# Klucze DWELL_BY_TIER = tier_bag (jak V326_SPEED_MULTIPLIER_MAP); wartości =
# DROPOFF min. Nieznany/None tier → DWELL_DEFAULT_MIN dropoff fallback. Pętla
# ucząca (eta_calibration_log.jsonl) dopreciezuje dropoff per tier.
DWELL_PICKUP_FLAT_MIN = 1.0  # E1 2026-05-17 — postój pod restauracją (obsługa)
DWELL_DEFAULT_MIN = 3.5  # dropoff fallback dla nieznanego tieru
DWELL_BY_TIER = {  # wartości = DROPOFF (handoff u klienta) per tier
    'gold': 2.5,
    'std+': 3.0,
    'std':  3.5,
    'slow': 4.0,
    'new':  4.0,
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
DRIVE_SPEED_MULT_BY_TIER = {
    'gold': 1.0,
    'std+': 1.0,
    'std':  1.0,
    'slow': 1.0,
    'new':  1.0,
}


def speed_mult_for_tier(tier):
    """Mnożnik tempa jazdy kuriera dla route_simulator leg_min.

    >1.0 = wolniej (dłuższe nogi trasy). Nieznany/None tier →
    DRIVE_SPEED_MULT_DEFAULT (1.0).
    """
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
BUNDLE_MAX_DELIV_SPREAD_KM = float(_os.environ.get(
    "BUNDLE_MAX_DELIV_SPREAD_KM", "8.0"))

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

# BUG F long-term (2026-05-26): klastry geograficzne (osiedla). Reguła Adriana:
# „Kraszewskiego i Wąska są blisko siebie na jednym osiedlu (Case D), szybkie
# do doręczenia, a później miałby najdalej na Jaroszówce". `districts_data.py`
# mapuje ulice na osiedla, ale TSP go ignoruje. Faza 1 = shadow metric only
# (zbieranie korpusu); sprint długoterminowy z osobnym planowaniem.
ENABLE_CLUSTER_DROP_GROUPING_METRIC = _os.environ.get(
    "ENABLE_CLUSTER_DROP_GROUPING_METRIC", "0") == "1"

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
    "ENABLE_COMMIT_DIVERGENCE_VERDICT_GATE", "1") == "1"
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

# ── SELECTION VETO SHADOW (2026-06-01) — diagnoza selekcji przeciw-kierunkowej ──
# Analiza replay 259 decyzji (eod_drafts/2026-06-01/SELECTION_cross_direction_verdict.md):
# przeciw-kierunkowi zwycięzcy NIE wygrywają na score (1/18) — wygrywają bo klucz
# selekcji (bucket informed>blind + late-pickup tier-2) NADPISUJE lepiej-skierowanego
# kandydata (10/18) lub brak nie-cross w puli (7/18 scarcity). Kara kierunku jest
# JUŻ mocna (cos<-0.7 ≈ -100) → wzmacnianie scoringu nieskuteczne. Lever = klucz
# selekcji. Ten shadow liczy „co by wybrał veto kierunkowe" OBOK live (ZERO zmiany
# zachowania) i serializuje rozjazd → kalibracja przed ewentualnym flipem.
# Flaga default OFF (shadow-first, jak late_pickup_shadow). Pomiar = grep
# SELECTION_VETO_SHADOW w shadow_decisions.jsonl przez kilka peaków.
ENABLE_SELECTION_VETO_SHADOW = _os.environ.get(
    "ENABLE_SELECTION_VETO_SHADOW", "0") == "1"
# R6BREACH-01 / GATE-02 SHADOW (2026-06-05): post-selekcyjny guard R6. Gdy LIVE
# zwycięzca łamie 35-min (r6_max_bag_time_min > BAG_TIME_HARD_MAX_MIN) a istnieje
# feasible kandydat ≤35 → guard wskazałby najlepszy-score czysty. SHADOW — NIGDY nie
# mutuje feasible/best (zero zmiany zachowania), tylko serializuje rozjazd → grep
# R6_BREACH_GUARD_SHADOW. Flaga default OFF (flip po kalibracji). Konsument:
# dispatch_pipeline._assess_order_impl (getattr C, default False).
ENABLE_R6_BREACH_GUARD_SHADOW = _os.environ.get(
    "ENABLE_R6_BREACH_GUARD_SHADOW", "0") == "1"
# Live winner z cos < BLOCK = „mocno przeciw-kierunkowy" → kandydat do veta.
SELECTION_VETO_COS_BLOCK = float(_os.environ.get(
    "SELECTION_VETO_COS_BLOCK", "-0.5"))
# Alternatywa „nie-cross" musi mieć cos > OK (lub None = solo/brak konfliktu kierunku).
SELECTION_VETO_COS_OK = float(_os.environ.get(
    "SELECTION_VETO_COS_OK", "-0.1"))
# True = veto przenosi tylko na kuriera ze ZNANĄ pozycją (informed) — bezpieczny dial
# (2 flipy/dzień, bez ryzyka no_gps/pre_shift). False = any (agresywny, 4 flipy, w tym
# na pustych/mniej pewnych). Replay 2026-06-01: informed-only flipuje do bag-aligned,
# any flipuje głównie do pustych (zlecenie solo zamiast cross-bundla).
SELECTION_VETO_INFORMED_ONLY = _os.environ.get(
    "SELECTION_VETO_INFORMED_ONLY", "1") == "1"

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

# F2 R1-WAVE-SCOPED DIRECTIONALITY (2026-05-24) — kierunkowość korytarza liczona
# tylko na dropach współistniejących z falą nowego ordera (feasibility_v2 po planie),
# zamiast na całym mieszanym bagu. Root cause korpusu eod_drafts/2026-05-24.
# Default OFF — flip True przez flags.json hot-reload; okno kilkudniowej walidacji.
ENABLE_R1_WAVE_SCOPED_DIRECTIONALITY = _os.environ.get(
    "ENABLE_R1_WAVE_SCOPED_DIRECTIONALITY", "0") == "1"

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
