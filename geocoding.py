"""Geocoding - Google primary + OSRM nearest fallback + persistent cache.

Architektura:
- Cache na dysku: geocode_cache.json (klucz = znormalizowany adres + miasto)
- Cache zyje wiecznie: adresy fizyczne nie zmieniaja lat/lon
- Google primary: jakosc 95%+
- OSRM nearest fallback: gdy Google timeout/limit/error
- Osobny cache dla restauracji (rzadziej sie zmienia, wieksza precyzja)

API:
- geocode(address, city=None) -> (lat, lon) lub None
  CITY_AWARE_GEOCODING=True (default) → city wymagany, fail loud bez niego.
  False (legacy kill-switch) → fallback do "Białystok".
- geocode_restaurant(name, address, city=None) -> (lat, lon) lub None
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


def _normalize(address: str, city: str) -> str:
    """Znormalizuj adres do klucza cache - lowercase, no extra spaces, usun lok/m/pietro.

    Key format: "<street>, <city>". city wymagany — callerzy `geocode()`
    rozwiązują to (city z panelu lub legacy "Białystok" gdy flag False).
    Stare entries z kluczem "street, białystok" pozostają kompatybilne —
    `geocode(addr, city="Białystok")` trafia w nie bez miss.
    """
    s = address.strip().lower()
    s = re.sub(r"\s+", " ", s)
    s = re.sub(r"\b(lok|lokal|m|mieszkanie|pietro|piętro)\.?\s*\w+", "", s)
    s = re.sub(r"/[^\s]+", "", s)  # wszystko po pierwszym / (numery lokali)
    s = s.strip(" ,/")
    c = (city or "").strip().lower()
    if c and c not in s:
        s = f"{s}, {c}"
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


def _effective_city(city: Optional[str], context: str) -> Optional[str]:
    """Resolve city per flag. Zwraca effective_city lub None gdy fail-loud mode."""
    if city and city.strip():
        return city.strip()
    try:
        from dispatch_v2.common import CITY_AWARE_GEOCODING as _flag
    except Exception:
        _flag = True  # safe default — flag not importable = assume strict
    if _flag:
        _log.warning(f"{context}: brak city (CITY_AWARE_GEOCODING=True → None)")
        return None
    return "Białystok"  # legacy kill-switch


def geocode(address: str, city: Optional[str] = None, timeout: float = 5.0) -> Optional[tuple]:
    """Google primary + cache. Zwraca (lat, lon) lub None.

    city: miasto klienta (z panel_client.delivery_city). Wymagany gdy
    CITY_AWARE_GEOCODING=True (default). Gdy flag False → fallback do "Białystok".

    timeout: max czas oczekiwania na Google API (cache hit = 0ms, nie dotyczy).
    Watcher uzywa timeout=2.0 (ochrona przed burst freeze).
    """
    if not address or not address.strip():
        return None

    effective_city = _effective_city(city, f"geocode({address!r})")
    if not effective_city:
        _stats["failures"] += 1
        return None

    key = _normalize(address, effective_city)

    with _lock:
        cache = _load_cache(CACHE_PATH)
        if key in cache:
            _stats["hits"] += 1
            entry = cache[key]
            return (entry["lat"], entry["lon"])

    _stats["misses"] += 1

    # Google primary — explicit city w query
    result = _google_geocode(f"{address}, {effective_city}, Polska", timeout=timeout)
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
            "city": effective_city,
            "cached_at": time.time(),
        }
        _save_cache(CACHE_PATH, cache)

    _log.info(f"Geocoded: {address} / city={effective_city} -> {result}")
    return result


def geocode_restaurant(name: str, address: str = "", city: Optional[str] = None) -> Optional[tuple]:
    """Osobny cache dla restauracji - nazwa jest kluczem.

    city: z `raw["address"]["city"]` (pole adresu restauracji). Wymagany gdy
    CITY_AWARE_GEOCODING=True. Bez niego geocoder miałby ryzyko źle rozwiązać
    ambiguous restaurant names (Warszawa-ready).
    """
    if not name:
        return None
    key = name.strip().lower()

    with _lock:
        cache = _load_cache(RESTAURANT_CACHE_PATH)
        if key in cache:
            _stats["hits"] += 1
            return (cache[key]["lat"], cache[key]["lon"])

    _stats["misses"] += 1

    effective_city = _effective_city(city, f"geocode_restaurant({name!r})")
    if not effective_city:
        return None

    query = f"{name}, {address}, {effective_city}" if address else f"{name}, {effective_city}, Polska"
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
            "city": effective_city,
            "cached_at": time.time(),
        }
        _save_cache(RESTAURANT_CACHE_PATH, cache)

    _log.info(f"Geocoded restaurant: {name} / city={effective_city} -> {result}")
    return result


def cache_stats() -> dict:
    cache = _load_cache(CACHE_PATH)
    rest_cache = _load_cache(RESTAURANT_CACHE_PATH)
    return {
        "addresses_cached": len(cache),
        "restaurants_cached": len(rest_cache),
        **_stats,
    }
