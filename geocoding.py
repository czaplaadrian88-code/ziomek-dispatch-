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

from dispatch_v2 import common as C
from dispatch_v2.common import setup_logger
from dispatch_v2.geocoding_audit import log_geocode as _audit_log
from dispatch_v2.osrm_client import nearest as osrm_nearest
from dispatch_v2 import geocode_verify as _gv

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


# ---------------------------------------------------------------------------
# A3 (audit STATE_OWNERSHIP F6 2026-05-07): cache TTL + drift detection helpers
# ---------------------------------------------------------------------------


def _ttl_config() -> tuple:
    """Lazy-load TTL flags z common.py. Returns (enabled, ttl_sec, drift_alert, drift_m).

    Defensywne: import fail → defaults (TTL ON 30d, drift alert OFF, 200m).
    """
    try:
        from dispatch_v2.common import (
            ENABLE_GEOCODE_CACHE_TTL as _ttl_on,
            GEOCODE_CACHE_TTL_DAYS as _ttl_days,
            ENABLE_GEOCODE_CACHE_DRIFT_ALERT as _drift_on,
            GEOCODE_CACHE_DRIFT_ALERT_M as _drift_m,
        )
        return (bool(_ttl_on), float(_ttl_days) * 86400.0, bool(_drift_on), float(_drift_m))
    except Exception:
        return (True, 30.0 * 86400.0, False, 200.0)


def _is_cache_entry_fresh(entry: dict, ttl_sec: float) -> bool:
    """Returns True jeśli entry jest świeży (NIE invalidate). Defensywnie:
    missing/corrupt `cached_at` → True (legacy entries protected — nie wymuszamy
    re-geocode masowego po deployu)."""
    cached_at = entry.get("cached_at")
    if not isinstance(cached_at, (int, float)):
        return True
    age_sec = time.time() - float(cached_at)
    if age_sec < 0:
        return True  # clock skew → defensive
    return age_sec < ttl_sec


def _bbox_config() -> tuple:
    """Lazy-load bbox guard flags z common.py. Returns
    (enabled, lat_min, lat_max, lon_min, lon_max). Import fail → guard ON z
    defaultową bbox Białystok+~28km (safe default — odrzuca oczywiste trucizny).
    """
    try:
        from dispatch_v2.common import (
            ENABLE_GEOCODE_BBOX_GUARD as _on,
            GEOCODE_BBOX_LAT_MIN as _la0,
            GEOCODE_BBOX_LAT_MAX as _la1,
            GEOCODE_BBOX_LON_MIN as _lo0,
            GEOCODE_BBOX_LON_MAX as _lo1,
        )
        return (bool(_on), float(_la0), float(_la1), float(_lo0), float(_lo1))
    except Exception:
        return (True, 52.85, 53.35, 22.85, 23.45)


def _in_service_bbox(lat: float, lon: float) -> bool:
    """True gdy (lat, lon) mieści się w bboxie obszaru obsługi (lub guard OFF).

    Guard OFF → zawsze True (legacy passthrough). Nie-liczbowe coords → False
    (defensywnie traktujemy jako poison)."""
    on, la0, la1, lo0, lo1 = _bbox_config()
    if not on:
        return True
    try:
        return la0 <= float(lat) <= la1 and lo0 <= float(lon) <= lo1
    except (TypeError, ValueError):
        return False


def _drift_meters(old_lat: float, old_lon: float, new_lat: float, new_lon: float) -> float:
    """Haversine distance w metrach między cache i nowym geocode result.
    Lokalna implementacja (no circular import dispatch_v2.geometry → osrm_client → ...).
    """
    import math
    R = 6371000.0  # earth radius m
    lat1, lat2 = math.radians(old_lat), math.radians(new_lat)
    dlat = lat2 - lat1
    dlon = math.radians(new_lon - old_lon)
    a = math.sin(dlat / 2) ** 2 + math.cos(lat1) * math.cos(lat2) * math.sin(dlon / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def cache_gc_stale(path: Path, ttl_sec: Optional[float] = None) -> dict:
    """A3: bulk GC offline cleanup. Returns {scanned, removed, kept_legacy}.

    `kept_legacy` = entries bez `cached_at` (defensive — nie usuwamy bez sygnału).
    Atomic save via _save_cache (LOCK_EX + tempfile + rename).
    """
    if ttl_sec is None:
        _, ttl_sec, _, _ = _ttl_config()
    with _lock:
        cache = _load_cache(path)
        scanned = len(cache)
        removed = 0
        kept_legacy = 0
        now = time.time()
        keys_to_del = []
        for key, entry in cache.items():
            cached_at = entry.get("cached_at")
            if not isinstance(cached_at, (int, float)):
                kept_legacy += 1
                continue
            if (now - float(cached_at)) >= ttl_sec:
                keys_to_del.append(key)
        for key in keys_to_del:
            del cache[key]
            removed += 1
        if removed > 0:
            _save_cache(path, cache)
    _log.info(f"cache_gc_stale path={path.name} scanned={scanned} removed={removed} kept_legacy={kept_legacy}")
    return {"scanned": scanned, "removed": removed, "kept_legacy": kept_legacy}


def _normalize(address: str, city: str) -> str:
    """Znormalizuj adres do klucza cache - lowercase, no extra spaces, usun lok/m/pietro.

    Key format: "<street>, <city>". city wymagany — callerzy `geocode()`
    rozwiązują to (city z panelu lub legacy "Białystok" gdy flag False).
    Stare entries z kluczem "street, białystok" pozostają kompatybilne —
    `geocode(addr, city="Białystok")` trafia w nie bez miss.
    """
    s = address.strip().lower()
    s = re.sub(r"\s+", " ", s)
    # Marker lokalu/mieszkania MUSI być zakończony numerem (lokal=zawsze cyfra).
    # BUG 2026-06-08: stary wzorzec `\b(...|m|...)\.?\s*\w+` zjadał nazwę KAŻDEJ
    # ulicy na „M" — „m"+„agazynowa" = całe słowo → „Magazynowa 3"/„Malachitowa 3"
    # kolidowały w kluczu „3, białystok" (113 zatrutych wpisów, same ulice M).
    # Wymóg `\d+` po markerze: „magazynowa" (m+a…) NIE pasuje, „m 3"/„m3"/„m.3" tak.
    s = re.sub(r"\b(?:mieszkanie|lokal|lok|piętro|pietro|m)\.?\s*\d+[a-z]?\b", "", s)
    s = re.sub(r"/[^\s]+", "", s)  # wszystko po pierwszym / (numery lokali)
    s = s.strip(" ,/")
    c = (city or "").strip().lower()
    if c and c not in s:
        s = f"{s}, {c}"
    return s


def _is_streetless_key(key: str, city: Optional[str]) -> bool:
    """True gdy znormalizowany klucz NIE zawiera nazwy ulicy — sam numer domu
    (np. „3, białystok"). Taki klucz koliduje między różnymi ulicami i jest
    przyczyną geo-poison (bug `m`-eating-M-streets 2026-06-08). Guard: takich
    kluczy NIE używamy do cache — zawsze świeży geocode + głośny log, żeby
    Ziomek NIGDY nie zwracał cudzych współrzędnych po cichu."""
    core = (key or "").strip().lower()
    c = (city or "").strip().lower()
    if c and core.endswith(c):
        core = core[:-len(c)]
    core = core.strip(" ,")
    return bool(re.fullmatch(r"\d+[a-z]?", core))


_PIN_SOURCE_MARKERS = (
    "adrian_manual", "manual_override", "manual_fix", "manual", "pinned",
    "panel_ground_truth", "ground_truth", "adrian_verified",
)


def _is_pinned_entry(entry: dict) -> bool:
    """FAZA 2 (item 5) — wpis ręcznie zweryfikowany (pin). Live re-geokod/TTL
    NIGDY nie może go nadpisać. Markery: cached_at='pinned:…' lub source z listą."""
    if not isinstance(entry, dict):
        return False
    ca = entry.get("cached_at")
    if isinstance(ca, str) and ca.lower().startswith("pinned"):
        return True
    src = str(entry.get("source") or "").lower()
    return any(m in src for m in _PIN_SOURCE_MARKERS)


def _districts_adjacent(d1: str, d2: str) -> bool:
    try:
        adj = C.BIALYSTOK_DISTRICT_ADJACENCY
    except Exception:
        return False
    return (d2 in adj.get(d1, set())) or (d1 in adj.get(d2, set()))


def _run_verification(address: str, city, lat: float, lon: float, meta: dict):
    """FAZA 2 — warstwa weryfikacji (items 2+3+4). Zwraca verdict dict lub None.

    Nominatim (drugie źródło) wołane TYLKO gdy items 2+3 już coś podejrzewają —
    oszczędność latencji + szacunek dla rate-limitu OSM. Fail-soft: każdy wyjątek
    → None (brak werdyktu, zero wpływu)."""
    if not getattr(C, "ENABLE_GEOCODE_VERIFICATION", False):
        return None
    try:
        meta = meta or {}

        def _expected(addr, cty):
            return C.drop_zone_from_address(addr, cty)

        def _actual(la, lo):
            from dispatch_v2.district_reverse_lookup import get_district_lookup
            return get_district_lookup().lookup(la, lo)

        kw = dict(
            location_type=meta.get("location_type"),
            partial_match=meta.get("partial_match", False),
            low_conf_location_types=getattr(
                C, "GEOCODE_LOW_CONFIDENCE_LOCATION_TYPES", frozenset()),
            district_check=getattr(C, "ENABLE_GEOCODE_DISTRICT_CHECK", True),
            expected_district_fn=_expected,
            actual_district_fn=_actual,
            districts_adjacent_fn=_districts_adjacent,
            cross_source_max_disagree_m=getattr(
                C, "GEOCODE_CROSS_SOURCE_MAX_DISAGREE_M", 400.0),
        )
        pre = _gv.verify(address, city, lat, lon,
                         cross_source=False, cross_source_coords=None, **kw)
        if pre["confidence"] == "ok" or not getattr(C, "ENABLE_GEOCODE_CROSS_SOURCE", False):
            return pre
        # escalate to second source only when suspicious
        nom = _gv.nominatim_geocode(
            address, city,
            timeout=getattr(C, "GEOCODE_NOMINATIM_TIMEOUT_S", 3.0),
            user_agent=getattr(C, "GEOCODE_NOMINATIM_USER_AGENT", "ziomek-dispatch/1.0"))
        return _gv.verify(address, city, lat, lon,
                          cross_source=True, cross_source_coords=nom, **kw)
    except Exception as e:
        _log.warning(f"GEOCODE_VERIFY_ERROR address={address!r}: {e}")
        return None


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
        top = data["results"][0]
        loc = top["geometry"]["location"]
        _stats["google"] += 1
        # FAZA 2 (item 2): nieś sygnały pewności — 3. element = meta dict.
        # location_type ROOFTOP/RANGE_INTERPOLATED = pewne; GEOMETRIC_CENTER/
        # APPROXIMATE = przybliżenie. partial_match = Google zgadywał ulicę.
        meta = {
            "location_type": top["geometry"].get("location_type"),
            "partial_match": bool(top.get("partial_match", False)),
        }
        return (loc["lat"], loc["lng"], meta)
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

    t_start = time.perf_counter()
    effective_city = _effective_city(city, f"geocode({address!r})")
    if not effective_city:
        _stats["failures"] += 1
        _audit_log("address", address, city, None, None, "none",
                   (time.perf_counter() - t_start) * 1000.0, error="no_city")
        return None

    key = _normalize(address, effective_city)

    # Geo-poison guard (2026-06-08): klucz bez ulicy = sam numer domu → koliduje
    # między ulicami. NIE czytamy/piszemy cache dla takiego klucza — zawsze świeży
    # Google, żeby nigdy nie zwrócić cudzych współrzędnych. Głośny ERROR = sygnał,
    # że normalizacja zdegenerowała (regresja regexu) — nie cichy bug.
    streetless = _is_streetless_key(key, effective_city)
    if streetless:
        _log.error(
            f"GEOCODE_STREETLESS_KEY address={address!r} city={effective_city!r} "
            f"key={key!r} — klucz bez ulicy, OMIJAM cache (świeży geocode, bez zapisu)"
        )

    # A3: TTL check + drift alert prep (przed cache hit decision)
    ttl_on, ttl_sec, _drift_on, _drift_m = _ttl_config()
    stale_old_coords = None  # populated jeśli cache hit ALE stale

    with _lock:
        cache = _load_cache(CACHE_PATH)
        if not streetless and key in cache:
            entry = cache[key]
            if not ttl_on or _is_cache_entry_fresh(entry, ttl_sec):
                _stats["hits"] += 1
                _audit_log("address", address, effective_city, entry["lat"], entry["lon"],
                           "cache", (time.perf_counter() - t_start) * 1000.0)
                return (entry["lat"], entry["lon"])
            # Stale: zachowaj old coords dla drift alert post re-geocode
            stale_old_coords = (entry["lat"], entry["lon"])
            _stats.setdefault("stale_invalidated", 0)
            _stats["stale_invalidated"] += 1
            _log.info(f"cache TTL invalidate key={key!r} age_d={((time.time() - entry.get('cached_at', 0)) / 86400.0):.1f}")

    _stats["misses"] += 1

    # Google primary — explicit city w query
    result = _google_geocode(f"{address}, {effective_city}, Polska", timeout=timeout)
    source = "google" if result is not None else None
    if result is None:
        result = _osrm_fallback(address)
        if result is not None:
            source = "osrm"

    if result is None:
        _stats["failures"] += 1
        _audit_log("address", address, effective_city, None, None, "none",
                   (time.perf_counter() - t_start) * 1000.0, error="google_and_osrm_failed")
        return None

    # Bbox guard: odrzuć out-of-bbox wynik PRZED cache write (geo-poison prevention,
    # zadanie #4). Caller dostaje None → istniejące defense gates (no_pickup_geocode).
    if not _in_service_bbox(result[0], result[1]):
        _stats.setdefault("bbox_rejected", 0)
        _stats["bbox_rejected"] += 1
        _stats["failures"] += 1
        _log.warning(
            f"GEOCODE_BBOX_REJECT address={address!r} city={effective_city!r} "
            f"coords=({result[0]:.6f},{result[1]:.6f}) source={source} — "
            f"poza bbox obsługi, NIE cache'uję"
        )
        _audit_log("address", address, effective_city, result[0], result[1],
                   source, (time.perf_counter() - t_start) * 1000.0, error="bbox_reject")
        return None

    # FAZA 2 — warstwa weryfikacji poprawności (location_type + dzielnica +
    # cross-source). Shadow: liczy+loguje; ENFORCE: odrzuca „reject" → None
    # (jak bbox, caller dostaje no_pickup_geocode). „low" zawsze tylko log.
    _verdict = _run_verification(
        address, effective_city, result[0], result[1],
        result[2] if len(result) > 2 else {})
    if _verdict is not None and _verdict["confidence"] in ("reject", "low"):
        _lvl = _log.error if _verdict["confidence"] == "reject" else _log.warning
        _lvl(
            f"GEOCODE_VERIFY_{_verdict['confidence'].upper()} address={address!r} "
            f"city={effective_city!r} coords=({result[0]:.5f},{result[1]:.5f}) "
            f"reasons={_verdict['reasons']} checks={_verdict['checks']} "
            f"enforce={C.ENABLE_GEOCODE_VERIFICATION_ENFORCE}"
        )
        if (_verdict["confidence"] == "reject"
                and C.ENABLE_GEOCODE_VERIFICATION_ENFORCE):
            _stats.setdefault("verify_rejected", 0)
            _stats["verify_rejected"] += 1
            _audit_log("address", address, effective_city, result[0], result[1],
                       source, (time.perf_counter() - t_start) * 1000.0,
                       error="verify_reject")
            return None

    if not streetless:
        with _lock:
            cache = _load_cache(CACHE_PATH)
            cache[key] = {
                "lat": result[0],
                "lon": result[1],
                "source": source,
                "original": address,
                "city": effective_city,
                "cached_at": time.time(),
            }
            _save_cache(CACHE_PATH, cache)

    # A3: drift alert gdy stale entry był re-geocoded i nowe coords różnią się
    # >threshold od cache. Opt-in flag (default OFF) — log WARN ujawnia
    # geographic instability (remont ulicy, zmiana numeracji, geocoder accuracy).
    if stale_old_coords is not None and _drift_on:
        drift_m = _drift_meters(stale_old_coords[0], stale_old_coords[1], result[0], result[1])
        if drift_m >= _drift_m:
            _log.warning(
                f"GEOCODE_DRIFT_ALERT key={key!r} drift={drift_m:.0f}m "
                f"old=({stale_old_coords[0]:.6f},{stale_old_coords[1]:.6f}) "
                f"new=({result[0]:.6f},{result[1]:.6f}) source={source}"
            )
            _stats.setdefault("drift_alerts", 0)
            _stats["drift_alerts"] += 1

    _audit_log("address", address, effective_city, result[0], result[1],
               source, (time.perf_counter() - t_start) * 1000.0)
    _log.info(f"Geocoded: {address} / city={effective_city} -> "
              f"({result[0]:.6f},{result[1]:.6f}) ({source})")
    # ZAWSZE 2-tuple (lat, lon) — meta (result[2]) jest wewnętrzna (weryfikacja),
    # callerzy oczekują (lat, lon). Cache-hit też zwraca 2-tuple → spójny typ.
    return (result[0], result[1])


def geocode_restaurant(name: str, address: str = "", city: Optional[str] = None) -> Optional[tuple]:
    """Osobny cache dla restauracji - nazwa jest kluczem.

    city: z `raw["address"]["city"]` (pole adresu restauracji). Wymagany gdy
    CITY_AWARE_GEOCODING=True. Bez niego geocoder miałby ryzyko źle rozwiązać
    ambiguous restaurant names (Warszawa-ready).
    """
    if not name:
        return None
    key = name.strip().lower()
    t_start = time.perf_counter()

    # A3: TTL check + drift alert prep dla restaurant cache
    ttl_on, ttl_sec, _drift_on, _drift_m = _ttl_config()
    stale_old_coords = None

    with _lock:
        cache = _load_cache(RESTAURANT_CACHE_PATH)
        if key in cache:
            entry = cache[key]
            # FAZA 2 (item 5): pin = ręcznie zweryfikowany → ZAWSZE zwróć, nigdy
            # nie re-geokoduj ani nie nadpisuj (TTL/drift nie ruszają pinów).
            if _is_pinned_entry(entry):
                _stats["hits"] += 1
                _audit_log("restaurant", name, entry.get("city"), entry["lat"], entry["lon"],
                           "cache_pin", (time.perf_counter() - t_start) * 1000.0)
                return (entry["lat"], entry["lon"])
            if not ttl_on or _is_cache_entry_fresh(entry, ttl_sec):
                _stats["hits"] += 1
                _audit_log("restaurant", name, entry.get("city"), entry["lat"], entry["lon"],
                           "cache", (time.perf_counter() - t_start) * 1000.0)
                return (entry["lat"], entry["lon"])
            stale_old_coords = (entry["lat"], entry["lon"])
            _stats.setdefault("stale_invalidated", 0)
            _stats["stale_invalidated"] += 1
            _log.info(f"restaurant cache TTL invalidate key={key!r}")

    _stats["misses"] += 1

    effective_city = _effective_city(city, f"geocode_restaurant({name!r})")
    if not effective_city:
        _audit_log("restaurant", name, city, None, None, "none",
                   (time.perf_counter() - t_start) * 1000.0, error="no_city")
        return None

    query = f"{name}, {address}, {effective_city}" if address else f"{name}, {effective_city}, Polska"
    result = _google_geocode(query)
    if result is None:
        _audit_log("restaurant", name, effective_city, None, None, "none",
                   (time.perf_counter() - t_start) * 1000.0, error="google_failed")
        return None

    # Bbox guard: restauracja poza bboxem = poison (dotknęłaby KAŻDEGO ordera z niej).
    if not _in_service_bbox(result[0], result[1]):
        _stats.setdefault("bbox_rejected", 0)
        _stats["bbox_rejected"] += 1
        _log.warning(
            f"GEOCODE_BBOX_REJECT (restaurant) name={name!r} city={effective_city!r} "
            f"coords=({result[0]:.6f},{result[1]:.6f}) — poza bbox obsługi, NIE cache'uję"
        )
        _audit_log("restaurant", name, effective_city, result[0], result[1],
                   "google", (time.perf_counter() - t_start) * 1000.0, error="bbox_reject")
        return None

    with _lock:
        cache = _load_cache(RESTAURANT_CACHE_PATH)
        # FAZA 2 (item 5): nie nadpisuj pinu (re-check pod lockiem — race-safe).
        _existing = cache.get(key)
        if _is_pinned_entry(_existing):
            _log.info(f"geocode_restaurant: pin chroniony, NIE nadpisuję key={key!r}")
            return (_existing["lat"], _existing["lon"])
        cache[key] = {
            "lat": result[0],
            "lon": result[1],
            "name": name,
            "address": address,
            "city": effective_city,
            "cached_at": time.time(),
        }
        _save_cache(RESTAURANT_CACHE_PATH, cache)

    # A3: drift alert dla restaurant cache (zmiana lokalizacji restauracji =
    # rzadkie ale silent jeśli się zdarzy)
    if stale_old_coords is not None and _drift_on:
        drift_m = _drift_meters(stale_old_coords[0], stale_old_coords[1], result[0], result[1])
        if drift_m >= _drift_m:
            _log.warning(
                f"GEOCODE_DRIFT_ALERT (restaurant) name={name!r} drift={drift_m:.0f}m "
                f"old=({stale_old_coords[0]:.6f},{stale_old_coords[1]:.6f}) "
                f"new=({result[0]:.6f},{result[1]:.6f})"
            )
            _stats.setdefault("drift_alerts", 0)
            _stats["drift_alerts"] += 1

    _audit_log("restaurant", name, effective_city, result[0], result[1],
               "google", (time.perf_counter() - t_start) * 1000.0)
    _log.info(f"Geocoded restaurant: {name} / city={effective_city} -> "
              f"({result[0]:.6f},{result[1]:.6f})")
    return (result[0], result[1])  # ZAWSZE 2-tuple (meta wewnętrzna)


def cache_stats() -> dict:
    cache = _load_cache(CACHE_PATH)
    rest_cache = _load_cache(RESTAURANT_CACHE_PATH)
    return {
        "addresses_cached": len(cache),
        "restaurants_cached": len(rest_cache),
        **_stats,
    }
