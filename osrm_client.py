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

from dispatch_v2.common import setup_logger

OSRM_BASE = "http://localhost:5001"
CACHE_TTL_SECONDS = 15 * 60  # 15 minut
CACHE_MAX_SIZE = 5000

_log = setup_logger("osrm_client", "/root/.openclaw/workspace/scripts/logs/dispatch.log")
_route_cache: dict = {}  # {(from_key, to_key): (timestamp, result)}


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


def route(from_ll: tuple, to_ll: tuple, use_cache: bool = True) -> Optional[dict]:
    """Route od from_ll do to_ll. Zwraca {duration_s, distance_m, duration_min} lub None."""
    if use_cache:
        cached = _cache_get(from_ll, to_ll)
        if cached is not None:
            return cached

    # OSRM: lon,lat;lon,lat
    coords = f"{from_ll[1]},{from_ll[0]};{to_ll[1]},{to_ll[0]}"
    url = f"{OSRM_BASE}/route/v1/driving/{coords}?overview=false"
    try:
        with urllib.request.urlopen(url, timeout=5) as r:
            data = json.loads(r.read().decode())
        if data.get("code") != "Ok" or not data.get("routes"):
            return None
        route0 = data["routes"][0]
        result = {
            "duration_s": route0["duration"],
            "distance_m": route0["distance"],
            "duration_min": round(route0["duration"] / 60, 1),
            "distance_km": round(route0["distance"] / 1000, 2),
        }
        if use_cache:
            _cache_set(from_ll, to_ll, result)
        return result
    except Exception as e:
        _log.warning(f"OSRM route fail: {e}")
        return None


def table(origins: list, destinations: list) -> Optional[list]:
    """Macierz czasow miedzy origins a destinations (batch, 1 request).

    Zwraca list[list[dict]]: matrix[i][j] = route od origins[i] do destinations[j].
    Zwraca None przy bledzie.
    """
    if not origins or not destinations:
        return None
    all_points = origins + destinations
    coords = ";".join(f"{ll[1]},{ll[0]}" for ll in all_points)
    sources = ";".join(str(i) for i in range(len(origins)))
    dests = ";".join(str(i) for i in range(len(origins), len(all_points)))
    url = f"{OSRM_BASE}/table/v1/driving/{coords}?sources={sources}&destinations={dests}&annotations=duration,distance"
    try:
        with urllib.request.urlopen(url, timeout=10) as r:
            data = json.loads(r.read().decode())
        if data.get("code") != "Ok":
            return None
        durations = data.get("durations") or []
        distances = data.get("distances") or [[0] * len(destinations)] * len(origins)
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
                })
            matrix.append(matrix_row)
        return matrix
    except Exception as e:
        _log.warning(f"OSRM table fail: {e}")
        return None


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
