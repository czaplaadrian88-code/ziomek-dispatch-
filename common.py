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
