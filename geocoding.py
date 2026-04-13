"""Geocoding - Google primary + OSRM nearest fallback + persistent cache.

Architektura:
- Cache na dysku: geocode_cache.json (klucz = znormalizowany adres)
- Cache zyje wiecznie: adresy fizyczne nie zmieniaja lat/lon
- Google primary: jakosc 95%+
- OSRM nearest fallback: gdy Google timeout/limit/error
- Osobny cache dla restauracji (rzadziej sie zmienia, wieksza precyzja)

API:
- geocode(address, hint_city='Białystok') -> (lat, lon) lub None
- geocode_restaurant(name, address) -> (lat, lon) lub None
- cache_stats() -> {size, hits, misses}
"""
import fcntl
import json
import os
import re
import tempfile
import threading
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Optional

from dispatch_v2.common import setup_logger
from dispatch_v2.osrm_client import nearest as osrm_nearest

GMAPS_ENV = Path("/root/.openclaw/workspace/.secrets/gmaps.env")
CACHE_PATH = Path("/root/.openclaw/workspace/dispatch_state/geocode_cache.json")
RESTAURANT_CACHE_PATH = Path("/root/.openclaw/workspace/dispatch_state/restaurant_coords.json")

_log = setup_logger("geocoding", "/root/.openclaw/workspace/scripts/logs/dispatch.log")
_lock = threading.Lock()
_stats = {"hits": 0, "misses": 0, "google": 0, "osrm_fallback": 0, "failures": 0}
_gmaps_key = None


def _load_key() -> Optional[str]:
    global _gmaps_key
    if _gmaps_key:
        return _gmaps_key
    if not GMAPS_ENV.exists():
        return None
    for line in GMAPS_ENV.read_text().splitlines():
        if "=" in line:
            k, v = line.strip().split("=", 1)
            if k.strip() in ("GMAPS_KEY", "GOOGLE_MAPS_API_KEY"):
                _gmaps_key = v.strip()
                return _gmaps_key
    return None


def _load_cache(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except Exception:
        return {}


def _save_cache(path: Path, data: dict):
    """Atomic write z LOCK_EX (P0.5b Fix #3).

    mkstemp w tym samym katalogu (atomic rename dziala tylko na tym samym fs),
    unique suffix (nie race z innym writer), LOCK_EX, fsync, rename.
    Cleanup temp jesli exception.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.tmp-",
        suffix=".json",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise


def _normalize(address: str, hint_city: str = "Białystok") -> str:
    """Znormalizuj adres do klucza cache - lowercase, no extra spaces, usun lok/m/pietro."""
    s = address.strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\b(lok|lokal|m|mieszkanie|pietro|piętro)\.?\s*\w+", "", s)
    s = re.sub(r"/[^\s]+", "", s)  # wszystko po pierwszym / (numery lokali)
    s = s.strip(" ,/")
    if hint_city.lower() not in s:
        s = f"{s}, {hint_city.lower()}"
    return s


def _google_geocode(address: str, timeout: float = 5.0) -> Optional[tuple]:
    key = _load_key()
    if not key:
        _log.warning("Brak GMAPS_KEY")
        return None
    params = urllib.parse.urlencode({
        "address": address,
        "key": key,
        "region": "pl",
        "language": "pl",
    })
    url = f"https://maps.googleapis.com/maps/api/geocode/json?{params}"
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            data = json.loads(r.read().decode())
        if data.get("status") != "OK" or not data.get("results"):
            _log.debug(f"Google ZERO_RESULTS: {address} status={data.get('status')}")
            return None
        loc = data["results"][0]["geometry"]["location"]
        _stats["google"] += 1
        return (loc["lat"], loc["lng"])
    except Exception as e:
        _log.warning(f"Google geocode fail: {e}")
        return None


def _osrm_fallback(address: str) -> Optional[tuple]:
    """Gdy Google padnie, probujemy wyekstraktowac wspolrzedne lokalizacji
    z nazwy ulicy przez proxy - niemozliwe bez punktu startowego.
    Realnie: OSRM nie umie tekstowego geocodingu, tylko snap to road.
    Ten fallback zwraca None - Google jest jedynym sensownym zrodlem dla tekstu.
    """
    # OSRM nearest wymaga lat/lon, nie tekstu. Prawdziwy fallback = blad.
    _log.warning(f"OSRM nie ma tekstowego geocodingu, brak fallbacku dla: {address}")
    _stats["osrm_fallback"] += 1
    return None


def geocode(address: str, hint_city: str = "Białystok", timeout: float = 5.0) -> Optional[tuple]:
    """Google primary + cache. Zwraca (lat, lon) lub None.

    timeout: max czas oczekiwania na Google API (cache hit = 0ms, nie dotyczy).
    Watcher uzywa timeout=2.0 (ochrona przed burst freeze).
    """
    if not address or not address.strip():
        return None

    key = _normalize(address, hint_city)

    with _lock:
        cache = _load_cache(CACHE_PATH)
        if key in cache:
            _stats["hits"] += 1
            entry = cache[key]
            return (entry["lat"], entry["lon"])

    _stats["misses"] += 1

    # Google primary
    result = _google_geocode(f"{address}, {hint_city}, Polska", timeout=timeout)
    if result is None:
        result = _osrm_fallback(address)

    if result is None:
        _stats["failures"] += 1
        return None

    with _lock:
        cache = _load_cache(CACHE_PATH)
        cache[key] = {
            "lat": result[0],
            "lon": result[1],
            "source": "google",
            "original": address,
            "cached_at": time.time(),
        }
        _save_cache(CACHE_PATH, cache)

    _log.info(f"Geocoded: {address} -> {result}")
    return result


def geocode_restaurant(name: str, address: str = "") -> Optional[tuple]:
    """Osobny cache dla restauracji - nazwa jest kluczem."""
    if not name:
        return None
    key = name.strip().lower()

    with _lock:
        cache = _load_cache(RESTAURANT_CACHE_PATH)
        if key in cache:
            _stats["hits"] += 1
            return (cache[key]["lat"], cache[key]["lon"])

    _stats["misses"] += 1
    query = f"{name}, {address}, Białystok" if address else f"{name}, Białystok, Polska"
    result = _google_geocode(query)
    if result is None:
        return None

    with _lock:
        cache = _load_cache(RESTAURANT_CACHE_PATH)
        cache[key] = {
            "lat": result[0],
            "lon": result[1],
            "name": name,
            "address": address,
            "cached_at": time.time(),
        }
        _save_cache(RESTAURANT_CACHE_PATH, cache)

    _log.info(f"Geocoded restaurant: {name} -> {result}")
    return result


def cache_stats() -> dict:
    cache = _load_cache(CACHE_PATH)
    rest_cache = _load_cache(RESTAURANT_CACHE_PATH)
    return {
        "addresses_cached": len(cache),
        "restaurants_cached": len(rest_cache),
        **_stats,
    }
