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
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Optional

from datetime import datetime, timezone

from dispatch_v2.common import (
    ENABLE_V326_OSRM_TRAFFIC_MULTIPLIER,
    ENABLE_OSRM_COORD_GUARD,
    HAVERSINE_ROAD_FACTOR_BIALYSTOK,
    ENABLE_V326_DISTANCE_BIN_TRAFFIC_BOOST,
    OSRM_INVALID_COORD_SENTINEL_MIN,
    OSRM_MAX_SNAP_KM,
    coords_in_bialystok_bbox,
    get_fallback_speed_kmh,
    get_time_bucket,
    get_traffic_multiplier,
    get_traffic_multiplier_v2,
    setup_logger,
)

OSRM_BASE = "http://localhost:5001"
CACHE_TTL_SECONDS = 60 * 60  # V3.26 R-07: 15→60min (Adrian ACK — Białystok skończony zestaw routes, 99% hit po 1h warm-up)
CACHE_MAX_SIZE = 5000

_log = setup_logger("osrm_client", "/root/.openclaw/workspace/scripts/logs/dispatch.log")
_route_cache: dict = {}  # {(from_key, to_key): (timestamp, result)}

# V3.27 latency parallel (2026-04-25): RLock dla thread-safe access do module-level
# state (cache, circuit breaker counters, hourly stats). RLock (reentrant) — bo
# `_apply_traffic_multiplier` może być wywołany wewnątrz `route()` lock holder.
_module_lock = threading.RLock()

# === CIRCUIT BREAKER (P0.5) ===
CIRCUIT_BREAKER_THRESHOLD = 3
CIRCUIT_BREAKER_COOLDOWN_S = 60

_osrm_failures: int = 0
_osrm_circuit_open_until: float = 0.0  # time.time() epoch

# === MP-#13 (2026-05-08): 3-warstwowy degraded mode (master plan TOP-15) ===
# Layer 1: cache age + degraded_since tracking — proxy dla "czy OSRM działa w tej chwili".
# Layer 2: alert entry/exit Telegram (send_admin_alert) at degraded transition.
# Layer 3: caller propagation — dispatch_pipeline.assess_order → PipelineResult.degraded_osrm.
#
# Differs od circuit_breaker: circuit_open_until ma cooldown ping-pong (re-opens po
# 60s jeśli kolejny call fail). degraded_since reflektuje continuous degradation period.
# Reset na pierwszy success po dowolnym fail/circuit cycle.
_osrm_last_success_ts: Optional[float] = None  # epoch ostatniego successfull HTTP call
_osrm_degraded_since: Optional[float] = None  # epoch entry into degraded state (None = healthy)
_osrm_degraded_alert_sent: bool = False  # dedup: jeden alert per degraded period (entry)
_osrm_recovery_alert_sent: bool = False  # dedup: jeden alert per recovery (NIE re-alert na flapping)

# === HOURLY METRICS (P0.5) ===
_osrm_stats: dict = {
    "calls_total": 0,
    "calls_fallback": 0,
    "circuit_opens": 0,
    # V3.26 BUG-3 STEP 1 — traffic multiplier hourly stats (no-op when flag=False)
    "traffic_mult_sum": 0.0,
    "traffic_mult_calls": 0,
    "traffic_mult_buckets": {},  # {"1.00": count, "1.10": count, ...}
    "hour_start": time.time(),
}


def _osrm_is_circuit_open() -> bool:
    # V3.27: read under RLock (consistent view z _osrm_record_failure writers).
    with _module_lock:
        return time.time() < _osrm_circuit_open_until


def _osrm_record_failure():
    global _osrm_failures, _osrm_circuit_open_until, _osrm_degraded_since, _osrm_degraded_alert_sent, _osrm_recovery_alert_sent
    fire_entry_alert = False
    with _module_lock:
        _osrm_failures += 1
        if _osrm_failures >= CIRCUIT_BREAKER_THRESHOLD:
            _osrm_circuit_open_until = time.time() + CIRCUIT_BREAKER_COOLDOWN_S
            _osrm_stats["circuit_opens"] += 1
            _log.warning(f"OSRM circuit OPEN after {_osrm_failures} failures, cooldown {CIRCUIT_BREAKER_COOLDOWN_S}s")
            # MP-#13 L1: enter degraded state on first circuit open; preserve initial entry ts
            if _osrm_degraded_since is None:
                _osrm_degraded_since = time.time()
                # MP-#13 L2: alert entry — once per continuous degraded period (dedup)
                if not _osrm_degraded_alert_sent:
                    fire_entry_alert = True
                    _osrm_degraded_alert_sent = True
                    _osrm_recovery_alert_sent = False  # arm recovery alert
    # Send alert outside lock (avoid blocking other callers on Telegram HTTP)
    if fire_entry_alert:
        _mp13_send_alert_safe(
            f"⚠ OSRM degraded — circuit OPEN po {CIRCUIT_BREAKER_THRESHOLD} kolejnych failurach. "
            f"Fallback haversine × road_factor + bucket-speed (~20% mniej precyzyjny routing). "
            f"Auto-recovery przy pierwszym successful HTTP call (cooldown {CIRCUIT_BREAKER_COOLDOWN_S}s)."
        )


def _osrm_record_success():
    global _osrm_failures, _osrm_last_success_ts, _osrm_degraded_since, _osrm_degraded_alert_sent, _osrm_recovery_alert_sent
    fire_recovery_alert = False
    degraded_duration_s = 0.0
    with _module_lock:
        _osrm_failures = 0
        _osrm_last_success_ts = time.time()
        # MP-#13 L1: exit degraded state on first success
        if _osrm_degraded_since is not None:
            degraded_duration_s = time.time() - _osrm_degraded_since
            _osrm_degraded_since = None
            _osrm_degraded_alert_sent = False  # arm next entry alert
            # MP-#13 L2: alert recovery — once per recovery (dedup against flapping)
            if not _osrm_recovery_alert_sent:
                fire_recovery_alert = True
                _osrm_recovery_alert_sent = True
    if fire_recovery_alert:
        _mp13_send_alert_safe(
            f"✅ OSRM recovery — z powrotem healthy mode po {int(degraded_duration_s)}s degraded. "
            f"Routing precision restored."
        )


def _mp13_send_alert_safe(msg: str) -> None:
    """MP-#13 L2: send Telegram alert defense-in-depth.

    Telegram unreachable / module not loaded / network fail → log warning ale
    NIE raise (osrm_client jest hot path, alert failure NIE może crashnąć route()).
    """
    try:
        from dispatch_v2 import telegram_utils
        telegram_utils.send_admin_alert(msg)
    except Exception as e:
        _log.warning(f"MP-#13 L2 alert send fail ({type(e).__name__}: {e}): {msg!r}")


def is_degraded() -> bool:
    """MP-#13 L1+L3: czy OSRM jest aktualnie w degraded mode (continuous period)?

    Returns True jeśli degraded_since wszedł w stan i NIE było jeszcze success.
    Caller (dispatch_pipeline.assess_order) propaguje do PipelineResult.degraded_osrm.

    Differs from `_osrm_is_circuit_open()` które reflectuje 60s cooldown ping-pong.
    Tu zwracamy True jeśli AKTUALNIE jesteśmy w degraded period (od ostatniego entry
    do następnego success).
    """
    with _module_lock:
        return _osrm_degraded_since is not None


def degraded_since_ts() -> Optional[float]:
    """MP-#13 L1: epoch when current degraded period started, None if healthy."""
    with _module_lock:
        return _osrm_degraded_since


def cache_age_s() -> Optional[float]:
    """MP-#13 L1: seconds since last successful OSRM HTTP call. None if never."""
    with _module_lock:
        if _osrm_last_success_ts is None:
            return None
        return time.time() - _osrm_last_success_ts


def _maybe_log_stats():
    # V3.27 latency parallel: dict mutation + read pod RLock dla concurrent safety.
    with _module_lock:
        elapsed = time.time() - _osrm_stats["hour_start"]
        if elapsed < 3600:
            return
        _log.info(
            f"OSRM hourly: total={_osrm_stats['calls_total']} "
            f"fallback={_osrm_stats['calls_fallback']} "
            f"circuit_opens={_osrm_stats['circuit_opens']}"
        )
        # Block 4D 2026-04-25: log traffic-mult stats always (shadow + live).
        if _osrm_stats["traffic_mult_calls"] > 0:
            avg = _osrm_stats["traffic_mult_sum"] / _osrm_stats["traffic_mult_calls"]
            buckets = dict(sorted(_osrm_stats["traffic_mult_buckets"].items()))
            mode = "live" if ENABLE_V326_OSRM_TRAFFIC_MULTIPLIER else "shadow"
            _log.info(
                f"OSRM traffic-mult hourly ({mode}): calls={_osrm_stats['traffic_mult_calls']} "
                f"avg_mult={avg:.3f} buckets={buckets}"
            )
        _osrm_stats["calls_total"] = 0
        _osrm_stats["calls_fallback"] = 0
        _osrm_stats["circuit_opens"] = 0
        _osrm_stats["traffic_mult_sum"] = 0.0
        _osrm_stats["traffic_mult_calls"] = 0
        _osrm_stats["traffic_mult_buckets"] = {}
        _osrm_stats["hour_start"] = time.time()


def _apply_traffic_multiplier(result: dict, now_utc: datetime) -> dict:
    """V3.26 BUG-3 STEP 1 + Block 4D 2026-04-25 instrumentation.

    Flag=False (SHADOW): records co BY zastosowano, BEZ mutation duration_s/min.
      - osrm_raw_duration_s/min: copy of duration_s
      - traffic_multiplier_shadow: mult that WOULD be applied (read-only)
      - duration_s/min: NIE zmienione (caller widzi raw OSRM)
      - hourly stats: incremented (continuous validation drift)
    Flag=True (LIVE): multiply duration_s/min in-place + record fields.
      - osrm_raw_duration_s/min: preserved raw
      - traffic_multiplier: applied mult
      - duration_s/min: multiplied
      - hourly stats: incremented

    Idempotency: detects existing osrm_raw_duration_s and re-multiplies from raw
      (so cached results are safe across hours).
    Stats inkrementowane ZAWSZE (shadow + live) → continuous drift validation.
    """
    if not result:
        return result
    raw_s = result.get("osrm_raw_duration_s", result.get("duration_s"))
    if raw_s is None:
        return result
    mult = get_traffic_multiplier(now_utc)

    # BUG-D V3.28+ shadow: per-distance-bin multiplier (additive boost in peak)
    distance_km = result.get("distance_km")
    mult_v2 = get_traffic_multiplier_v2(now_utc, distance_km)

    # Always record shadow fields (Block 4D instrumentation 2026-04-25)
    result["osrm_raw_duration_s"] = raw_s
    result["osrm_raw_duration_min"] = round(raw_s / 60, 1)
    # BUG-D shadow: record co BY zostalo applied gdyby v2 flag był ON
    result["traffic_multiplier_v2_shadow"] = mult_v2
    # V3.27 latency parallel: stats updates pod RLock (concurrent dict mutation safety).
    with _module_lock:
        _osrm_stats["traffic_mult_sum"] += mult
        _osrm_stats["traffic_mult_calls"] += 1
        key = f"{mult:.2f}"
        _osrm_stats["traffic_mult_buckets"][key] = (
            _osrm_stats["traffic_mult_buckets"].get(key, 0) + 1
        )

    if not ENABLE_V326_OSRM_TRAFFIC_MULTIPLIER:
        # SHADOW mode: record-only, NO mutation of duration_s/min
        result["traffic_multiplier_shadow"] = mult
        return result

    # LIVE mode: BUG-D conditional — v2 jeśli flag ON, inaczej v1 (legacy)
    applied_mult = mult_v2 if ENABLE_V326_DISTANCE_BIN_TRAFFIC_BOOST else mult
    adjusted_s = raw_s * applied_mult
    result["traffic_multiplier"] = applied_mult
    result["traffic_multiplier_v1"] = mult  # legacy v1 preserved for analytics
    result["duration_s"] = round(adjusted_s, 1)
    result["duration_min"] = round(adjusted_s / 60, 1)
    return result


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
    """Odleglosc w km. ll = (lat, lon).

    Fail-loud na None / sentinel (0,0): pre-fix te wartości dawały silent
    ~6285km (dystans Białystok→(0,0)) co maskowało brak geokodacji jako
    pickup_too_far. Lekcja #32: silent except = invisible bug.
    """
    if ll1 is None or ll2 is None:
        _log.error("haversine None coords: ll1=%r ll2=%r", ll1, ll2)
        raise ValueError(f"haversine: None coords (ll1={ll1!r}, ll2={ll2!r})")
    if ll1 == (0.0, 0.0) or ll2 == (0.0, 0.0):
        # tech-debt #20 Krok 1 instrumentacja: ramka wołającego — log wskaże
        # który z 8 call-site'ów wstrzykuje (0,0) (źródło transientne, nie ma
        # go w plikach stanu). Tylko gałąź błędu (~100×/dzień — koszt znikomy).
        import traceback
        _c = traceback.extract_stack(limit=2)[0]
        _log.error(
            "haversine sentinel (0,0): ll1=%r ll2=%r caller=%s:%d in %s()",
            ll1, ll2, _c.filename.rsplit("/", 1)[-1], _c.lineno, _c.name,
        )
        raise ValueError(f"haversine: sentinel (0,0) (ll1={ll1!r}, ll2={ll2!r})")
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
    # V3.27 latency parallel: cache read+expire-delete pod RLock.
    key = (_cache_key(from_ll), _cache_key(to_ll))
    with _module_lock:
        if key in _route_cache:
            ts, result = _route_cache[key]
            if time.time() - ts < CACHE_TTL_SECONDS:
                return result
            del _route_cache[key]
    return None


def _cache_set(from_ll: tuple, to_ll: tuple, result: dict):
    # V3.27 latency parallel: cache eviction+set pod RLock dla concurrent safety.
    key = (_cache_key(from_ll), _cache_key(to_ll))
    with _module_lock:
        if len(_route_cache) >= CACHE_MAX_SIZE:
            # Usun najstarsze 10%
            oldest = sorted(_route_cache.items(), key=lambda x: x[1][0])[: CACHE_MAX_SIZE // 10]
            for k, _ in oldest:
                del _route_cache[k]
        _route_cache[key] = (time.time(), result)


# === COORD POISON GUARD (Lekcja #140, 2026-05-21) ===
# Współrzędna (0,0)/None/cross-country NIE może cicho dać realistycznej trasy.
# OSRM snapuje (0,0) do krawędzi ekstraktu (~113 km, code:Ok) — fail-loud #81
# (haversine) tego NIE łapie, bo OSRM "succeeded". Tu chokepoint: route()/table().
_BBOX_CENTER = (53.1325, 23.1688)  # placeholder dla nieprawidłowej współrzędnej w table()
_coord_guard_log_count: int = 0


def _coord_guard_log(msg: str):
    """Loud, ale rate-limited (pełny log pierwsze 20×, potem co 100×)."""
    global _coord_guard_log_count
    _coord_guard_log_count += 1
    if _coord_guard_log_count <= 20 or _coord_guard_log_count % 100 == 0:
        _log.error("COORD_GUARD #%d: %s", _coord_guard_log_count, msg)


def _invalid_coord_result(now_utc: datetime) -> dict:
    """Wynik dla nieprawidłowej współrzędnej — jawnie infeasible, NIE realna trasa."""
    sentinel_min = OSRM_INVALID_COORD_SENTINEL_MIN
    return {
        "duration_s": round(sentinel_min * 60, 1),
        "distance_m": round(sentinel_min * 1000, 0),
        "duration_min": sentinel_min,
        "distance_km": round(sentinel_min, 2),
        "osrm_fallback": True,
        "coord_invalid": True,
    }


def route(from_ll: tuple, to_ll: tuple, use_cache: bool = True) -> dict:
    """Route od from_ll do to_ll. Zawsze zwraca dict (OSRM lub fallback, nigdy None).

    V3.26 BUG-3 STEP 1: traffic multiplier applied at every return path
    (post-cache, post-OSRM, post-fallback). Cache stores RAW values; multiplier
    is applied to a COPY after lookup so cached entries are time-bucket
    independent.

    Lekcja #140: guard wejściowy (bbox) + snap-distance — zła współrzędna →
    coord_invalid sentinel, nigdy cicha phantom-trasa.
    """
    now = datetime.now(timezone.utc)

    # Guard wejściowy: (0,0)/None/poza bbox → sentinel (NIE wysyłaj do OSRM,
    # bo (0,0) snapuje do krawędzi i wraca jako "prawidłowa" trasa ~117 min).
    if ENABLE_OSRM_COORD_GUARD and not (
        coords_in_bialystok_bbox(from_ll) and coords_in_bialystok_bbox(to_ll)
    ):
        _coord_guard_log(f"route invalid coord from={from_ll!r} to={to_ll!r} "
                         f"→ sentinel {OSRM_INVALID_COORD_SENTINEL_MIN}min")
        return _apply_traffic_multiplier(_invalid_coord_result(now), now)

    if use_cache:
        cached = _cache_get(from_ll, to_ll)
        if cached is not None:
            return _apply_traffic_multiplier(dict(cached), now)

    # Cache miss — realny HTTP call (lub fallback)
    # V3.27 latency parallel: stats inkrement pod RLock.
    with _module_lock:
        _osrm_stats["calls_total"] += 1
    _maybe_log_stats()

    # Circuit breaker — skip HTTP jeśli OSRM padł
    if _osrm_is_circuit_open():
        with _module_lock:
            _osrm_stats["calls_fallback"] += 1
        return _apply_traffic_multiplier(_haversine_fallback(from_ll, to_ll, now), now)

    # OSRM: lon,lat;lon,lat
    coords = f"{from_ll[1]},{from_ll[0]};{to_ll[1]},{to_ll[0]}"
    url = f"{OSRM_BASE}/route/v1/driving/{coords}?overview=false"
    try:
        with urllib.request.urlopen(url, timeout=3) as r:
            data = json.loads(r.read().decode())
        if data.get("code") != "Ok" or not data.get("routes"):
            _osrm_record_failure()
            with _module_lock:
                _osrm_stats["calls_fallback"] += 1
            return _apply_traffic_multiplier(_haversine_fallback(from_ll, to_ll, now), now)
        # Snap guard (Lekcja #140): jeśli OSRM musiał snapować waypoint > próg, to
        # punkt nie leży na mapie (np. (0,0)→6225 km) — code:Ok ale trasa fikcyjna.
        if ENABLE_OSRM_COORD_GUARD:
            _max_snap_m = OSRM_MAX_SNAP_KM * 1000.0
            _wps = data.get("waypoints") or []
            _bad_snap = next(
                (w.get("distance") for w in _wps
                 if isinstance(w, dict) and (w.get("distance") or 0) > _max_snap_m),
                None,
            )
            if _bad_snap is not None:
                _coord_guard_log(
                    f"route snap {round(_bad_snap/1000,1)}km > {OSRM_MAX_SNAP_KM}km "
                    f"from={from_ll!r} to={to_ll!r} → sentinel")
                return _apply_traffic_multiplier(_invalid_coord_result(now), now)
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
            _cache_set(from_ll, to_ll, result)  # store RAW (pre-multiplier)
        return _apply_traffic_multiplier(dict(result), now)
    except Exception as e:
        _log.warning(f"OSRM route fail: {e}")
        _osrm_record_failure()
        with _module_lock:
            _osrm_stats["calls_fallback"] += 1
        return _apply_traffic_multiplier(_haversine_fallback(from_ll, to_ll, now), now)


def table(origins: list, destinations: list) -> list:
    """Macierz czasów. Zawsze zwraca matrix (OSRM lub fallback, nigdy None).

    V3.26 BUG-3 STEP 1: traffic multiplier applied per cell, single `now`
    for the whole matrix call (consistent bucket).
    """
    now = datetime.now(timezone.utc)

    if not origins or not destinations:
        return []

    # Guard wejściowy (Lekcja #140): podmień nieprawidłowe współrzędne
    # ((0,0)/None/poza bbox) na _BBOX_CENTER (by OSRM/haversine nie snapowały do
    # krawędzi / nie crashowały), zapamiętaj maski → po policzeniu nadpisz każdą
    # komórkę dotykającą nieprawidłowego punktu sentinelem (jawnie infeasible).
    _valid_o = _valid_d = None
    if ENABLE_OSRM_COORD_GUARD:
        _vo = [coords_in_bialystok_bbox(o) for o in origins]
        _vd = [coords_in_bialystok_bbox(d) for d in destinations]
        if not (all(_vo) and all(_vd)):
            _bad = [o for o, v in zip(origins, _vo) if not v] + \
                   [d for d, v in zip(destinations, _vd) if not v]
            _coord_guard_log(f"table {len(_bad)} invalid coord(s) {_bad[:4]!r} "
                             f"→ sentinel cells {OSRM_INVALID_COORD_SENTINEL_MIN}min")
            _valid_o, _valid_d = _vo, _vd
            origins = [o if v else _BBOX_CENTER for o, v in zip(origins, _vo)]
            destinations = [d if v else _BBOX_CENTER for d, v in zip(destinations, _vd)]

    def _sentinel_invalid(matrix: list) -> list:
        """Nadpisz komórki dotykające nieprawidłowego punktu (Lekcja #140)."""
        if _valid_o is None:
            return matrix
        for i, row in enumerate(matrix):
            for j in range(len(row)):
                if i < len(_valid_o) and j < len(_valid_d) and not (_valid_o[i] and _valid_d[j]):
                    row[j] = _invalid_coord_result(now)
        return matrix

    # Cache miss — realny HTTP call (table nie ma cache)
    # V3.27 latency parallel: stats inkrement pod RLock.
    with _module_lock:
        _osrm_stats["calls_total"] += 1
    _maybe_log_stats()

    # Circuit breaker
    if _osrm_is_circuit_open():
        with _module_lock:
            _osrm_stats["calls_fallback"] += 1
        return _sentinel_invalid(_table_fallback(origins, destinations, now))

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
            with _module_lock:
                _osrm_stats["calls_fallback"] += 1
            return _sentinel_invalid(_table_fallback(origins, destinations, now))
        durations = data.get("durations") or []
        distances = data.get("distances") or [[0] * len(destinations) for _ in range(len(origins))]
        matrix = []
        for i, row in enumerate(durations):
            matrix_row = []
            for j, dur in enumerate(row):
                dist = distances[i][j] if i < len(distances) and j < len(distances[i]) else 0
                cell = {
                    "duration_s": dur,
                    "duration_min": round(dur / 60, 1) if dur else None,
                    "distance_m": dist,
                    "distance_km": round(dist / 1000, 2) if dist else 0,
                    "osrm_fallback": False,
                }
                matrix_row.append(_apply_traffic_multiplier(cell, now))
            matrix.append(matrix_row)
        _osrm_record_success()
        return _sentinel_invalid(matrix)
    except Exception as e:
        _log.warning(f"OSRM table fail: {e}")
        _osrm_record_failure()
        with _module_lock:
            _osrm_stats["calls_fallback"] += 1
        return _sentinel_invalid(_table_fallback(origins, destinations, now))


def _table_fallback(origins: list, destinations: list, now_utc: datetime) -> list:
    """Fallback matrix: haversine * road_factor per cell.

    V3.26 BUG-3 STEP 1: each cell goes through _apply_traffic_multiplier so
    fallback path is consistent with main OSRM path.
    """
    matrix = []
    for o in origins:
        row = []
        for d in destinations:
            row.append(_apply_traffic_multiplier(_haversine_fallback(o, d, now_utc), now_utc))
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
