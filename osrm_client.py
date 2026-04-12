"""OSRM Client - lokalny router (self-hosted Docker :5001).

Funkcje:
- route(from_ll, to_ll) -> dict {duration_s, distance_m, duration_min}
- table(origins, destinations) -> macierz czasow (batch routing)
- nearest(lat, lon) -> (lat, lon, name) - fallback do geocodingu
- haversine(ll1, ll2) -> km (czysta matematyka, zero requestow)

Cache in-memory: klucz = zaokraglone wspolrzedne (4 miejsca dziesietne ~11m)
TTL: 15 minut (korki sie zmieniaja)

Format wspolrzednych: tuple (lat, lon) - TAKA konwencja w calym kodzie dispatch_v2.
OSRM API ma odwrotnie (lon, lat) - zamiana wewnatrz klienta.
"""
import json
import math
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from datetime import datetime, timezone

from dispatch_v2.common import (
    HAVERSINE_ROAD_FACTOR_BIALYSTOK,
    get_fallback_speed_kmh,
    get_time_bucket,
    setup_logger,
)

OSRM_BASE = "http://localhost:5001"
CACHE_TTL_SECONDS = 15 * 60  # 15 minut
CACHE_MAX_SIZE = 5000

_log = setup_logger("osrm_client", "/root/.openclaw/workspace/scripts/logs/dispatch.log")
_route_cache: dict = {}  # {(from_key, to_key): (timestamp, result)}

# === CIRCUIT BREAKER (P0.5) ===
CIRCUIT_BREAKER_THRESHOLD = 3
CIRCUIT_BREAKER_COOLDOWN_S = 60

_osrm_failures: int = 0
_osrm_circuit_open_until: float = 0.0  # time.time() epoch

# === HOURLY METRICS (P0.5) ===
_osrm_stats: dict = {
    "calls_total": 0,
    "calls_fallback": 0,
    "circuit_opens": 0,
    "hour_start": time.time(),
}


def _osrm_is_circuit_open() -> bool:
    return time.time() < _osrm_circuit_open_until


def _osrm_record_failure():
    global _osrm_failures, _osrm_circuit_open_until
    _osrm_failures += 1
    if _osrm_failures >= CIRCUIT_BREAKER_THRESHOLD:
        _osrm_circuit_open_until = time.time() + CIRCUIT_BREAKER_COOLDOWN_S
        _osrm_stats["circuit_opens"] += 1
        _log.warning(f"OSRM circuit OPEN after {_osrm_failures} failures, cooldown {CIRCUIT_BREAKER_COOLDOWN_S}s")


def _osrm_record_success():
    global _osrm_failures
    _osrm_failures = 0


def _maybe_log_stats():
    elapsed = time.time() - _osrm_stats["hour_start"]
    if elapsed >= 3600:
        _log.info(
            f"OSRM hourly: total={_osrm_stats['calls_total']} "
            f"fallback={_osrm_stats['calls_fallback']} "
            f"circuit_opens={_osrm_stats['circuit_opens']}"
        )
        _osrm_stats["calls_total"] = 0
        _osrm_stats["calls_fallback"] = 0
        _osrm_stats["circuit_opens"] = 0
        _osrm_stats["hour_start"] = time.time()


def _haversine_fallback(from_ll: tuple, to_ll: tuple, now_utc: datetime) -> dict:
    """Fallback: haversine * road_factor, prędkość z bucketu korkowego."""
    h_km = haversine(from_ll, to_ll)
    road_km = h_km * HAVERSINE_ROAD_FACTOR_BIALYSTOK
    speed = get_fallback_speed_kmh(now_utc)
    bucket = get_time_bucket(now_utc)
    duration_s = road_km / speed * 3600
    return {
        "duration_s": round(duration_s, 1),
        "distance_m": round(road_km * 1000, 0),
        "duration_min": round(duration_s / 60, 1),
        "distance_km": round(road_km, 2),
        "osrm_fallback": True,
        "osrm_circuit_open": _osrm_is_circuit_open(),
        "time_bucket": bucket,
    }


def haversine(ll1: tuple, ll2: tuple) -> float:
    """Odleglosc w km. ll = (lat, lon)."""
    lat1, lon1 = ll1
    lat2, lon2 = ll2
    R = 6371.0
    phi1 = math.radians(lat1)
    phi2 = math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlam = math.radians(lon2 - lon1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlam / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _cache_key(ll: tuple) -> str:
    return f"{round(ll[0], 4)},{round(ll[1], 4)}"


def _cache_get(from_ll: tuple, to_ll: tuple) -> Optional[dict]:
    key = (_cache_key(from_ll), _cache_key(to_ll))
    if key in _route_cache:
        ts, result = _route_cache[key]
        if time.time() - ts < CACHE_TTL_SECONDS:
            return result
        del _route_cache[key]
    return None


def _cache_set(from_ll: tuple, to_ll: tuple, result: dict):
    if len(_route_cache) >= CACHE_MAX_SIZE:
        # Usun najstarsze 10%
        oldest = sorted(_route_cache.items(), key=lambda x: x[1][0])[: CACHE_MAX_SIZE // 10]
        for k, _ in oldest:
            del _route_cache[k]
    key = (_cache_key(from_ll), _cache_key(to_ll))
    _route_cache[key] = (time.time(), result)


def route(from_ll: tuple, to_ll: tuple, use_cache: bool = True) -> dict:
    """Route od from_ll do to_ll. Zawsze zwraca dict (OSRM lub fallback, nigdy None)."""
    now = datetime.now(timezone.utc)

    if use_cache:
        cached = _cache_get(from_ll, to_ll)
        if cached is not None:
            return cached

    # Cache miss — realny HTTP call (lub fallback)
    _osrm_stats["calls_total"] += 1
    _maybe_log_stats()

    # Circuit breaker — skip HTTP jeśli OSRM padł
    if _osrm_is_circuit_open():
        _osrm_stats["calls_fallback"] += 1
        return _haversine_fallback(from_ll, to_ll, now)

    # OSRM: lon,lat;lon,lat
    coords = f"{from_ll[1]},{from_ll[0]};{to_ll[1]},{to_ll[0]}"
    url = f"{OSRM_BASE}/route/v1/driving/{coords}?overview=false"
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            data = json.loads(r.read().decode())
        if data.get("code") != "Ok" or not data.get("routes"):
            _osrm_record_failure()
            _osrm_stats["calls_fallback"] += 1
            return _haversine_fallback(from_ll, to_ll, now)
        route0 = data["routes"][0]
        result = {
            "duration_s": route0["duration"],
            "distance_m": route0["distance"],
            "duration_min": round(route0["duration"] / 60, 1),
            "distance_km": round(route0["distance"] / 1000, 2),
            "osrm_fallback": False,
        }
        _osrm_record_success()
        if use_cache:
            _cache_set(from_ll, to_ll, result)
        return result
    except Exception as e:
        _log.warning(f"OSRM route fail: {e}")
        _osrm_record_failure()
        _osrm_stats["calls_fallback"] += 1
        return _haversine_fallback(from_ll, to_ll, now)


def table(origins: list, destinations: list) -> list:
    """Macierz czasów. Zawsze zwraca matrix (OSRM lub fallback, nigdy None)."""
    now = datetime.now(timezone.utc)

    if not origins or not destinations:
        return []

    # Cache miss — realny HTTP call (table nie ma cache)
    _osrm_stats["calls_total"] += 1
    _maybe_log_stats()

    # Circuit breaker
    if _osrm_is_circuit_open():
        _osrm_stats["calls_fallback"] += 1
        return _table_fallback(origins, destinations, now)

    all_points = origins + destinations
    coords = ";".join(f"{ll[1]},{ll[0]}" for ll in all_points)
    sources = ";".join(str(i) for i in range(len(origins)))
    dests = ";".join(str(i) for i in range(len(origins), len(all_points)))
    url = f"{OSRM_BASE}/table/v1/driving/{coords}?sources={sources}&destinations={dests}&annotations=duration,distance"
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            data = json.loads(r.read().decode())
        if data.get("code") != "Ok":
            _osrm_record_failure()
            _osrm_stats["calls_fallback"] += 1
            return _table_fallback(origins, destinations, now)
        durations = data.get("durations") or []
        distances = data.get("distances") or [[0] * len(destinations) for _ in range(len(origins))]
        matrix = []
        for i, row in enumerate(durations):
            matrix_row = []
            for j, dur in enumerate(row):
                dist = distances[i][j] if i < len(distances) and j < len(distances[i]) else 0
                matrix_row.append({
                    "duration_s": dur,
                    "duration_min": round(dur / 60, 1) if dur else None,
                    "distance_m": dist,
                    "distance_km": round(dist / 1000, 2) if dist else 0,
                    "osrm_fallback": False,
                })
            matrix.append(matrix_row)
        _osrm_record_success()
        return matrix
    except Exception as e:
        _log.warning(f"OSRM table fail: {e}")
        _osrm_record_failure()
        _osrm_stats["calls_fallback"] += 1
        return _table_fallback(origins, destinations, now)


def _table_fallback(origins: list, destinations: list, now_utc: datetime) -> list:
    """Fallback matrix: haversine * road_factor per cell."""
    matrix = []
    for o in origins:
        row = []
        for d in destinations:
            row.append(_haversine_fallback(o, d, now_utc))
        matrix.append(row)
    return matrix


def nearest(lat: float, lon: float) -> Optional[tuple]:
    """Najblizszy wezel drogowy. Zwraca (lat, lon, name) lub None.
    Uzywane jako fallback dla geocodingu."""
    url = f"{OSRM_BASE}/nearest/v1/driving/{lon},{lat}"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.loads(r.read().decode())
        if data.get("code") != "Ok" or not data.get("waypoints"):
            return None
        wp = data["waypoints"][0]
        snapped_lon, snapped_lat = wp["location"]
        name = wp.get("name") or ""
        return (snapped_lat, snapped_lon, name)
    except Exception as e:
        _log.warning(f"OSRM nearest fail: {e}")
        return None


def health_check() -> dict:
    """Szybki test OSRM - route Rukola -> Akademicka."""
    result = {"osrm_ok": False, "route_ok": False, "table_ok": False, "nearest_ok": False}
    # Route
    r = route((53.1325, 23.1688), (53.1158, 23.1611), use_cache=False)
    if r:
        result["osrm_ok"] = True
        result["route_ok"] = True
        result["sample_route"] = r
    # Table
    t = table([(53.1325, 23.1688), (53.1300, 23.1600)], [(53.1158, 23.1611)])
    if t:
        result["table_ok"] = True
        result["sample_table_shape"] = f"{len(t)}x{len(t[0]) if t else 0}"
    # Nearest
    n = nearest(53.1325, 23.1688)
    if n:
        result["nearest_ok"] = True
        result["sample_nearest"] = n
    return result
