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
from contextlib import contextmanager
from pathlib import Path
from typing import Callable, Optional

from dispatch_v2 import common as C
from dispatch_v2.common import setup_logger
from dispatch_v2.geocoding_audit import log_geocode as _audit_log
from dispatch_v2 import geocode_verify as _gv

GMAPS_ENV = Path("/root/.openclaw/workspace/.secrets/gmaps.env")
CACHE_PATH = Path("/root/.openclaw/workspace/dispatch_state/geocode_cache.json")
RESTAURANT_CACHE_PATH = Path("/root/.openclaw/workspace/dispatch_state/restaurant_coords.json")
# Negatywny cache: adresy DETERMINISTYCZNIE odrzucone (verify_reject/bbox_reject) — patrz
# common.ENABLE_GEOCODE_NEGATIVE_CACHE. Klucz = _normalize(address, city). Wartość:
# {"reason": str, "cached_at": float}. TTL z GEOCODE_NEG_CACHE_TTL_SEC.
NEG_CACHE_PATH = Path("/root/.openclaw/workspace/dispatch_state/geocode_neg_cache.json")

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


def _load_cache(path: Path, *, strict: bool = False) -> dict:
    """Load cache JSON.

    Read paths remain fail-soft for availability.  A writer must pass
    ``strict=True``: replacing a temporarily unreadable/corrupt cache with an
    empty dict would turn one parse error into permanent data loss.
    """
    path = Path(path)
    if not path.exists():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            raise ValueError(f"cache root must be an object: {path}")
        return data
    except Exception:
        if strict:
            raise
        return {}


def _save_cache(path: Path, data: dict):
    """Atomically replace ``path``. Caller must hold ``_cache_file_lock``.

    A lock on a unique tempfile does not serialize writers, therefore locking
    deliberately lives in ``_mutate_cache`` and covers the whole fresh
    load -> mutate/merge -> fsync -> replace transaction.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = 0o600
    try:
        mode = path.stat().st_mode & 0o777
    except FileNotFoundError:
        pass
    fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent),
        prefix=f".{path.name}.tmp-",
        suffix=".json",
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            os.fchmod(f.fileno(), mode)
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp_path, path)
        _fsync_directory(path.parent)
    except BaseException:
        if os.path.exists(tmp_path):
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
        raise


def _fsync_directory(directory: Path) -> None:
    """Best-effort durability for the rename itself.

    Some filesystems do not support fsync on a directory.  The file content is
    already fsynced and atomically replaced at this point, so unsupported
    directory fsync is logged rather than reported as an uncommitted write.
    """
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
    dir_fd = None
    try:
        dir_fd = os.open(str(directory), flags)
        os.fsync(dir_fd)
    except OSError as exc:
        _log.warning("cache directory fsync unavailable path=%s: %r", directory, exc)
    finally:
        if dir_fd is not None:
            os.close(dir_fd)


def _cache_lock_path(path: Path) -> Path:
    """Stable lockfile shared by every process writing a given cache."""
    path = Path(path)
    return path.with_name(path.name + ".lock")


@contextmanager
def _cache_file_lock(path: Path):
    """Hold a per-cache cross-process LOCK_EX until after ``os.replace``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = _cache_lock_path(path)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o600)
    with os.fdopen(fd, "r+", encoding="utf-8") as lock_f:
        fcntl.flock(lock_f.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(lock_f.fileno(), fcntl.LOCK_UN)


def _mutate_cache(path: Path, mutate_fn: Callable[[dict], bool]) -> tuple[dict, bool]:
    """Canonical cache RMW transaction.

    ``mutate_fn`` receives the latest on-disk dict while the stable per-cache
    lock is held and returns True only when the dict should be committed.
    Returning False avoids a needless replace (notably for protected pins).
    """
    path = Path(path)
    with _cache_file_lock(path):
        cache = _load_cache(path, strict=True)
        changed = bool(mutate_fn(cache))
        if changed:
            _save_cache(path, cache)
        return cache, changed


def _put_cache_entry(
    path: Path,
    key: str,
    entry: dict,
    *,
    protect_pins: bool = False,
) -> tuple[dict, bool]:
    """Merge one entry into a fresh snapshot, optionally preserving a pin."""
    def _put(cache: dict) -> bool:
        if protect_pins and _is_pinned_entry(cache.get(key)):
            return False
        cache[key] = entry
        return True

    cache, changed = _mutate_cache(path, _put)
    return cache[key], changed


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

    `kept_legacy` = entries bez liczbowego `cached_at` oraz chronione piny
    (defensive — nie usuwamy bez sygnału).  Scan i delete są jednym RMW pod
    stałym per-cache lockfile, więc GC nie kasuje równoległego insertu.
    """
    if ttl_sec is None:
        _, ttl_sec, _, _ = _ttl_config()
    result = {"scanned": 0, "removed": 0, "kept_legacy": 0}

    def _gc(cache: dict) -> bool:
        result["scanned"] = len(cache)
        now = time.time()
        keys_to_del = []
        for key, entry in cache.items():
            if not isinstance(entry, dict) or _is_pinned_entry(entry):
                result["kept_legacy"] += 1
                continue
            cached_at = entry.get("cached_at")
            if not isinstance(cached_at, (int, float)):
                result["kept_legacy"] += 1
                continue
            if (now - float(cached_at)) >= ttl_sec:
                keys_to_del.append(key)
        for key in keys_to_del:
            del cache[key]
        result["removed"] = len(keys_to_del)
        return bool(keys_to_del)

    _mutate_cache(path, _gc)
    _log.info(
        "cache_gc_stale path=%s scanned=%d removed=%d kept_legacy=%d",
        Path(path).name,
        result["scanned"],
        result["removed"],
        result["kept_legacy"],
    )
    return result


def _neg_cache_enabled():
    return C.flag("ENABLE_GEOCODE_NEGATIVE_CACHE",
                  getattr(C, "ENABLE_GEOCODE_NEGATIVE_CACHE", True))


def _neg_cache_check(key: str):
    """True gdy `key` ma ŚWIEŻY wpis w neg-cache (adres deterministycznie nie-geokodowalny).
    Czyta pod _lock. Defensywny — błąd/wyłączone → False (czyli normalny geocode)."""
    if not _neg_cache_enabled():
        return False
    ttl = float(getattr(C, "GEOCODE_NEG_CACHE_TTL_SEC", 21600))
    try:
        with _lock:
            neg = _load_cache(NEG_CACHE_PATH)
        entry = neg.get(key)
        if not isinstance(entry, dict):
            return False
        cached_at = entry.get("cached_at")
        if not isinstance(cached_at, (int, float)):
            return False
        return (time.time() - float(cached_at)) < ttl
    except Exception:
        return False


def _neg_cache_put(key: str, reason: str):
    """Zapisz reject przez wspólną wieloprocesową transakcję cache."""
    if not _neg_cache_enabled():
        return
    try:
        _put_cache_entry(
            NEG_CACHE_PATH,
            key,
            {"reason": reason, "cached_at": time.time()},
        )
    except Exception as _e:
        _log.warning(f"neg_cache_put fail key={key!r}: {_e!r}")


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


# Pamięć pinezek z realnych dostaw GPS (2026-07-09) — zasilana niezależnie przez
# `tools/address_pin_aggregator.py` (timer co 5 min), OSOBNE pliki od
# geocode_cache.json. Dwie przestrzenie: dostawy (delivery_address) + restauracje
# (pickup_address) — sprawdzamy OBIE, bo `geocode()` geokoduje oba rodzaje tekstu.
_PIN_MEMORY_STORES = (
    Path("/root/.openclaw/workspace/dispatch_state/address_pins.json"),
    Path("/root/.openclaw/workspace/dispatch_state/restaurant_pins.json"),
)


def _pin_memory_lookup(address: str, city=None):
    """Szuka nauczonej pinezki (address_pin_memory) dla `address`. Zwraca dict
    entry (lat/lon/confidence/n_inliers/n_samples) albo None. Fail-soft — każdy
    wyjątek (brak pliku/importu) → None, nigdy nie wywraca geocode().

    Klucz w `address_pins.json` = `normalize_address(delivery_address)` — ale
    surowy tekst `delivery_address` z panelu CZASEM zawiera miasto ("Składowa 12
    Białystok"), CZASEM nie ("Składowa 12"), zależnie od zlecenia. Próbujemy
    kilku wariantów (bez miasta / z miastem doklejonym) — wzorzec identyczny do
    `courier_orders._geo_lookup` (apka też zgaduje warianty klucza)."""
    try:
        from dispatch_v2 import address_pin_memory as _apm
    except Exception as e:
        _log.warning(f"GEOCODE_PIN_MEMORY_IMPORT_FAIL: {e}")
        return None
    candidates = [address]
    if city:
        candidates.append(f"{address} {city}")
    norms = []
    for cand in candidates:
        norm = _apm.normalize_address(cand)
        if norm and norm not in norms:
            norms.append(norm)
    if not norms:
        return None
    for path in _PIN_MEMORY_STORES:
        try:
            store = _apm.load_store(str(path))
        except Exception:
            continue
        for norm in norms:
            entry = store.get(norm)
            if isinstance(entry, dict) and "lat" in entry and "lon" in entry:
                return entry
    return None


def _pin_memory_fallback(key: str, address: str, city, t_start: float, reason: str):
    """Ostatnia deska ratunku (2026-07-09) — gdy oficjalny geocode() nie da rady
    (neg_cache/verify_reject/bbox_reject/total fail), sprawdź czy adres ma już
    nauczoną pinezkę z realnych dostaw kurierów (address_pin_memory) ZANIM oddasz
    None. Ściśle addytywne: wołane TYLKO z miejsc, które i tak zwróciłyby None —
    nie może pogorszyć działającego geokodu, tylko wypełnić dziurę.

    SHADOW (ENABLE_GEOCODE_PIN_MEMORY_FALLBACK=False, default): liczy + loguje co
    by zwrócił, ale realnie oddaje None (istniejące zachowanie bez zmian).
    LIVE (flag True): zwraca (lat, lon) z pinezki, jeśli spełnia próg
    GEOCODE_PIN_MEMORY_MIN_INLIERS (odrzuca zbyt cienkie, pojedyncze próbki)."""
    entry = _pin_memory_lookup(address, city)
    if entry is None:
        return None
    try:
        lat, lon = float(entry["lat"]), float(entry["lon"])
    except (TypeError, ValueError, KeyError):
        return None
    confidence = entry.get("confidence", "low")
    n_inliers = entry.get("n_inliers", entry.get("n_samples", 0)) or 0
    min_inliers = getattr(C, "GEOCODE_PIN_MEMORY_MIN_INLIERS", 1)
    live = C.flag("ENABLE_GEOCODE_PIN_MEMORY_FALLBACK", C.ENABLE_GEOCODE_PIN_MEMORY_FALLBACK)
    meets_bar = n_inliers >= min_inliers
    tag = "pin_memory" if (live and meets_bar) else "pin_memory_shadow"
    _log.info(
        f"GEOCODE_PIN_MEMORY_{'LIVE' if (live and meets_bar) else 'SHADOW'} "
        f"key={key!r} reason={reason} confidence={confidence} n_inliers={n_inliers} "
        f"-> ({lat:.6f},{lon:.6f}) live_flag={live} meets_bar={meets_bar}"
    )
    _audit_log("address", address, city, lat, lon, tag,
               (time.perf_counter() - t_start) * 1000.0,
               error=None if (live and meets_bar) else f"shadow_{reason}")
    if live and meets_bar:
        return (lat, lon)
    return None


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


def _nominatim_fallback(address: str, city: Optional[str], timeout: float) -> Optional[tuple]:
    """Realny fallback tekstowy OSM/Nominatim, bounded do bboxu obszaru obsługi.

    Odpala się TYLKO gdy Google zawiódł (None) lub zwrócił out-of-bbox poison
    (gating w geocode()). Google nie ma w indeksie części białostockich ulic
    (np. „Proroka Eliasza", „Poniatowskiego" w Pieczurkach) → Nominatim trafia.
    Zwraca (lat, lon) lub None. Wynik i tak przechodzi przez bbox-guard callera."""
    # Guard: pusty/śmieciowy adres („—", sam numer, telefon) degeneruje query do
    # samego miasta → Nominatim zwróciłby centroid Białegostoku (fałszywy odzysk
    # → ciche mis-route). Wymagaj realnego tokenu ulicy (≥1 ciąg liter len≥3).
    if not re.search(r"[A-Za-zĄĆĘŁŃÓŚŹŻąćęłńóśźż]{3,}", address or ""):
        return None
    on, la0, la1, lo0, lo1 = _bbox_config()
    # viewbox = lon_min,lat_max,lon_max,lat_min (lewy-górny, prawy-dolny róg)
    viewbox = f"{lo0},{la1},{lo1},{la0}" if on else None
    try:
        coords = _gv.nominatim_geocode(
            address, city,
            timeout=getattr(C, "GEOCODE_NOMINATIM_TIMEOUT_S", 3.0),
            user_agent=getattr(C, "GEOCODE_NOMINATIM_USER_AGENT", "ziomek-dispatch/1.0"),
            viewbox=viewbox, bounded=bool(on))
    except Exception as e:
        _log.warning(f"NOMINATIM_FALLBACK_ERROR address={address!r}: {e}")
        return None
    _stats.setdefault("nominatim_fallback", 0)
    _stats["nominatim_fallback"] += 1
    return coords


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
            # FAZA 2 (item 5, 2026-07-09 parytet z geocode_restaurant): pin = ręcznie
            # zweryfikowany → ZAWSZE zwróć, nigdy nie re-geokoduj ani nie nadpisuj
            # (TTL/drift nie ruszają pinów). Wcześniej TYLKO geocode_restaurant miał
            # tę ochronę — adresy dostawy/odbioru jej nie miały (bliźniak niedopięty).
            if _is_pinned_entry(entry):
                _stats["hits"] += 1
                _audit_log("address", address, effective_city, entry["lat"], entry["lon"],
                           "cache_pin", (time.perf_counter() - t_start) * 1000.0)
                return (entry["lat"], entry["lon"])
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

    # NEGATYWNY cache (2026-06-26): adres wcześniej DETERMINISTYCZNIE odrzucony
    # (verify/bbox) → zwróć None bez sieci. Oszczędza zapytanie Google + weryfikację
    # i ucisza spam logów (był ~460 GEOCODE_VERIFY_REJECT/3h na tych samych adresach).
    if not streetless and _neg_cache_check(key):
        _stats.setdefault("neg_cache_hits", 0)
        _stats["neg_cache_hits"] += 1
        _audit_log("address", address, effective_city, None, None, "neg_cache",
                   (time.perf_counter() - t_start) * 1000.0, error="neg_cache_hit")
        _pm = _pin_memory_fallback(key, address, effective_city, t_start, "neg_cache")
        if _pm is not None:
            return _pm
        return None

    _stats["misses"] += 1

    # Google primary — explicit city w query
    result = _google_geocode(f"{address}, {effective_city}, Polska", timeout=timeout)
    source = "google" if result is not None else None
    if result is None:
        if C.flag("ENABLE_GEOCODE_NOMINATIM_FALLBACK", C.ENABLE_GEOCODE_NOMINATIM_FALLBACK):
            _nom = _nominatim_fallback(address, effective_city, timeout)
            if _nom is not None:
                result = _nom
                source = "nominatim_fallback"
        if result is None:
            result = _osrm_fallback(address)
            if result is not None:
                source = "osrm"

    if result is None:
        _stats["failures"] += 1
        _audit_log("address", address, effective_city, None, None, "none",
                   (time.perf_counter() - t_start) * 1000.0, error="google_and_osrm_failed")
        _pm = _pin_memory_fallback(key, address, effective_city, t_start, "google_and_osrm_failed")
        if _pm is not None:
            return _pm
        return None

    # Bbox guard: odrzuć out-of-bbox wynik PRZED cache write (geo-poison prevention,
    # zadanie #4). Caller dostaje None → istniejące defense gates (no_pickup_geocode).
    if not _in_service_bbox(result[0], result[1]):
        # Google zwrócił out-of-bbox (zwykle poison: miejscowość „Białystok" 22-540
        # na południu). Spróbuj Nominatim bounded ZANIM odrzucisz — odzyskuje realne
        # białostockie ulice, których Google nie ma w indeksie. Strictly additive.
        _nom = None
        if source == "google" and C.flag(
                "ENABLE_GEOCODE_NOMINATIM_FALLBACK", C.ENABLE_GEOCODE_NOMINATIM_FALLBACK):
            _nom = _nominatim_fallback(address, effective_city, timeout)
        if _nom is not None and _in_service_bbox(_nom[0], _nom[1]):
            _log.info(
                f"GEOCODE_NOMINATIM_RECOVERED address={address!r} city={effective_city!r} "
                f"google_oob=({result[0]:.5f},{result[1]:.5f}) → "
                f"osm=({_nom[0]:.5f},{_nom[1]:.5f})"
            )
            _stats.setdefault("nominatim_recovered", 0)
            _stats["nominatim_recovered"] += 1
            result = (_nom[0], _nom[1])
            source = "nominatim_fallback"
        else:
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
            # NIE neg-cache'ujemy bbox_reject — bywa TRANSIENTNY (poison Google z płd. Polski,
            # który Nominatim odzyskuje); blokada na TTL mogłaby zamknąć dobry adres. Neg-cache
            # tylko deterministyczny verify_reject (niżej).
            _pm = _pin_memory_fallback(key, address, effective_city, t_start, "bbox_reject")
            if _pm is not None:
                return _pm
            return None

    # FAZA 2 — warstwa weryfikacji poprawności (location_type + dzielnica +
    # cross-source). Shadow: liczy+loguje; ENFORCE: odrzuca „reject" → None
    # (jak bbox, caller dostaje no_pickup_geocode). „low" zawsze tylko log.
    _verdict = _run_verification(
        address, effective_city, result[0], result[1],
        result[2] if len(result) > 2 else {})
    if _verdict is not None and _verdict["confidence"] in ("reject", "low"):
        _enforce = C.flag("ENABLE_GEOCODE_VERIFICATION_ENFORCE",
                          C.ENABLE_GEOCODE_VERIFICATION_ENFORCE)  # hot-reload via flags.json
        _lvl = _log.error if _verdict["confidence"] == "reject" else _log.warning
        _lvl(
            f"GEOCODE_VERIFY_{_verdict['confidence'].upper()} address={address!r} "
            f"city={effective_city!r} coords=({result[0]:.5f},{result[1]:.5f}) "
            f"reasons={_verdict['reasons']} checks={_verdict['checks']} "
            f"enforce={_enforce}"
        )
        if _verdict["confidence"] == "reject" and _enforce:
            _stats.setdefault("verify_rejected", 0)
            _stats["verify_rejected"] += 1
            _audit_log("address", address, effective_city, result[0], result[1],
                       source, (time.perf_counter() - t_start) * 1000.0,
                       error="verify_reject")
            if not streetless:
                _neg_cache_put(key, "verify_reject")
            _pm = _pin_memory_fallback(key, address, effective_city, t_start, "verify_reject")
            if _pm is not None:
                return _pm
            return None

    if not streetless:
        try:
            _stored, _changed = _put_cache_entry(
                CACHE_PATH,
                key,
                {
                    "lat": result[0],
                    "lon": result[1],
                    "source": source,
                    "original": address,
                    "city": effective_city,
                    "cached_at": time.time(),
                },
                protect_pins=True,
            )
        except Exception as exc:
            # Cache jest optymalizacją, nie częścią publicznego kontraktu
            # geocode(). Strict writer nie zastąpi uszkodzonego pliku pustym
            # snapshotem; świeży, zweryfikowany wynik nadal zwracamy callerowi.
            _log.warning(
                "GEOCODE_CACHE_WRITE_FAIL path=%s key=%r; zwracam fresh coords, "
                "cache bez zmian: %r",
                CACHE_PATH, key, exc,
            )
        else:
            # Pin mógł powstać w innym procesie podczas zapytania sieciowego.
            # Re-check odbył się na świeżym snapshotcie pod LOCK_EX.
            if not _changed and _is_pinned_entry(_stored):
                _log.info(f"geocode: pin chroniony, NIE nadpisuję key={key!r}")
                return (_stored["lat"], _stored["lon"])

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

    try:
        _stored, _changed = _put_cache_entry(
            RESTAURANT_CACHE_PATH,
            key,
            {
                "lat": result[0],
                "lon": result[1],
                "name": name,
                "address": address,
                "city": effective_city,
                "cached_at": time.time(),
            },
            protect_pins=True,
        )
    except Exception as exc:
        _log.warning(
            "GEOCODE_CACHE_WRITE_FAIL path=%s key=%r; zwracam fresh restaurant "
            "coords, cache bez zmian: %r",
            RESTAURANT_CACHE_PATH, key, exc,
        )
    else:
        if not _changed and _is_pinned_entry(_stored):
            _log.info(f"geocode_restaurant: pin chroniony, NIE nadpisuję key={key!r}")
            return (_stored["lat"], _stored["lon"])

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
