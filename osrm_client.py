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
    get_distance_bin_v2,
    get_fallback_speed_kmh,
    get_time_bucket,
    get_traffic_multiplier,
    get_traffic_multiplier_v2,
    setup_logger,
    flag as _common_flag,
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

# === BUG-D Faza 2b — TLS per-request leg tracking (parallel-safe) ===
# Każdy thread w ThreadPoolExecutor ma własny `legs` list. Caller (_v327_eval_courier)
# inicjuje przez start_v2_request_tracking() przed evaluacją courier'a, odczytuje
# przez stop_v2_request_tracking() po zakończeniu. _apply_traffic_multiplier append'uje
# do TLS list ZAWSZE gdy tracking aktywny (legs is not None).
#
# Quality choice: TLS zamiast wrapping wszystkich OSRM call sites — OSRM jest wywoływany
# w wielu modułach (route_simulator_v2, feasibility_v2, chain_eta, dispatch_pipeline),
# wrapping byłby invasive. TLS isolation pod ThreadPoolExecutor jest inherent thread-safe.
_request_legs = threading.local()


def start_v2_request_tracking() -> None:
    """Inicjuj per-request leg tracking dla bieżącego thread. Idempotent."""
    _request_legs.legs = []


def stop_v2_request_tracking() -> Optional[list]:
    """Zakończ tracking, zwróć zebraną listę legs (lub None gdy nie startowane).

    Cleanup TLS żeby kolejne calls bez start nie zbierały śmieci.
    """
    legs = getattr(_request_legs, "legs", None)
    _request_legs.legs = None
    return legs


# === HOURLY METRICS (P0.5) ===
_osrm_stats: dict = {
    "calls_total": 0,
    "calls_fallback": 0,
    "circuit_opens": 0,
    # OSRM-TABLE-03 (2026-06-12) — per-cell cache table() hourly stats
    "table_cells_hit": 0,
    "table_cells_miss": 0,
    "table_full_hits": 0,
    "table_decomposed_calls": 0,
    "table_legacy_calls": 0,
    # V3.26 BUG-3 STEP 1 — traffic multiplier hourly stats (no-op when flag=False)
    "traffic_mult_sum": 0.0,
    "traffic_mult_calls": 0,
    "traffic_mult_buckets": {},  # {"1.00": count, "1.10": count, ...}
    # BUG-D Faza 2a 2026-05-28 — per-distance-bin shadow stats
    "traffic_mult_v2_sum": 0.0,
    "traffic_mult_v2_calls": 0,
    # per-bin breakdown: each value is {"count": int, "sum": float}
    "traffic_mult_v2_bins": {
        "short": {"count": 0, "sum": 0.0},
        "medium": {"count": 0, "sum": 0.0},
        "long": {"count": 0, "sum": 0.0},
        "none": {"count": 0, "sum": 0.0},  # distance_km missing (legacy path)
    },
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
        # OSRM-TABLE-03: hit-rate cache komórek table() (pomiar w logu)
        _tc_hit = _osrm_stats["table_cells_hit"]
        _tc_miss = _osrm_stats["table_cells_miss"]
        if _tc_hit + _tc_miss > 0:
            _log.info(
                f"OSRM table-cache hourly: cells_hit={_tc_hit} "
                f"cells_miss={_tc_miss} "
                f"hit_rate={_tc_hit / (_tc_hit + _tc_miss) * 100:.1f}% "
                f"full_hits={_osrm_stats['table_full_hits']} "
                f"decomposed={_osrm_stats['table_decomposed_calls']} "
                f"legacy={_osrm_stats['table_legacy_calls']} "
                f"cache_size={len(_table_cell_cache)}"
            )
            for _tk in ("table_cells_hit", "table_cells_miss", "table_full_hits",
                        "table_decomposed_calls", "table_legacy_calls"):
                _osrm_stats[_tk] = 0
        # Block 4D 2026-04-25: log traffic-mult stats always (shadow + live).
        if _osrm_stats["traffic_mult_calls"] > 0:
            avg = _osrm_stats["traffic_mult_sum"] / _osrm_stats["traffic_mult_calls"]
            buckets = dict(sorted(_osrm_stats["traffic_mult_buckets"].items()))
            mode = "live" if ENABLE_V326_OSRM_TRAFFIC_MULTIPLIER else "shadow"
            _log.info(
                f"OSRM traffic-mult hourly ({mode}): calls={_osrm_stats['traffic_mult_calls']} "
                f"avg_mult={avg:.3f} buckets={buckets}"
            )
        # BUG-D Faza 2a 2026-05-28: log v2 per-distance-bin stats (shadow always).
        if _osrm_stats["traffic_mult_v2_calls"] > 0:
            v2_avg = _osrm_stats["traffic_mult_v2_sum"] / _osrm_stats["traffic_mult_v2_calls"]
            bins_summary = {}
            for bin_name, bin_data in _osrm_stats["traffic_mult_v2_bins"].items():
                if bin_data["count"] > 0:
                    bins_summary[bin_name] = {
                        "n": bin_data["count"],
                        "avg": round(bin_data["sum"] / bin_data["count"], 3),
                    }
            v2_mode = "live" if ENABLE_V326_DISTANCE_BIN_TRAFFIC_BOOST else "shadow"
            _log.info(
                f"OSRM traffic-mult-v2 hourly ({v2_mode}): calls={_osrm_stats['traffic_mult_v2_calls']} "
                f"avg_mult_v2={v2_avg:.3f} bins={bins_summary}"
            )
        _osrm_stats["calls_total"] = 0
        _osrm_stats["calls_fallback"] = 0
        _osrm_stats["circuit_opens"] = 0
        _osrm_stats["traffic_mult_sum"] = 0.0
        _osrm_stats["traffic_mult_calls"] = 0
        _osrm_stats["traffic_mult_buckets"] = {}
        # BUG-D Faza 2a: reset v2 stats per hour
        _osrm_stats["traffic_mult_v2_sum"] = 0.0
        _osrm_stats["traffic_mult_v2_calls"] = 0
        for _bin in _osrm_stats["traffic_mult_v2_bins"].values():
            _bin["count"] = 0
            _bin["sum"] = 0.0
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
    # #12 audyt 28.06: fallback haversine ma duration JUŻ korkową (get_fallback_speed_kmh = bucket
    # korkowy 20-32 km/h, traffic w środku) → mnożenie get_traffic_multiplier DRUGI raz = podwójne
    # liczenie ruchu (~+25..49% w peaku gdy OSRM degraded → przeszacowany drive → over-konserwatywny
    # R6/feasibility → fałszywe odrzucenia). NIE jest to OSRM free-flow → poza traffic-drift stats
    # (te walidują drift OSRM↔traffic, nie fallback). Zwróć już-korkową duration bez mnożnika.
    if result.get("osrm_fallback"):
        result["osrm_raw_duration_s"] = raw_s
        result["osrm_raw_duration_min"] = round(raw_s / 60, 1)
        result["traffic_multiplier"] = 1.0
        result["traffic_multiplier_fallback_already_corked"] = True
        return result
    mult = get_traffic_multiplier(now_utc)

    # BUG-D V3.28+ shadow: per-distance-bin multiplier (additive boost in peak)
    distance_km = result.get("distance_km")
    mult_v2 = get_traffic_multiplier_v2(now_utc, distance_km)
    v2_bin = get_distance_bin_v2(distance_km)

    # Always record shadow fields (Block 4D instrumentation 2026-04-25)
    result["osrm_raw_duration_s"] = raw_s
    result["osrm_raw_duration_min"] = round(raw_s / 60, 1)
    # BUG-D shadow: record co BY zostalo applied gdyby v2 flag był ON
    result["traffic_multiplier_v2_shadow"] = mult_v2
    result["distance_bin_v2"] = v2_bin
    # V3.27 latency parallel: stats updates pod RLock (concurrent dict mutation safety).
    with _module_lock:
        _osrm_stats["traffic_mult_sum"] += mult
        _osrm_stats["traffic_mult_calls"] += 1
        key = f"{mult:.2f}"
        _osrm_stats["traffic_mult_buckets"][key] = (
            _osrm_stats["traffic_mult_buckets"].get(key, 0) + 1
        )
        # BUG-D Faza 2a: per-bin stats
        _osrm_stats["traffic_mult_v2_sum"] += mult_v2
        _osrm_stats["traffic_mult_v2_calls"] += 1
        _bin_stats = _osrm_stats["traffic_mult_v2_bins"][v2_bin]
        _bin_stats["count"] += 1
        _bin_stats["sum"] += mult_v2

    # BUG-D Faza 2b: per-request leg recording (TLS, parallel-safe, opt-in via caller)
    _tls_legs = getattr(_request_legs, "legs", None)
    if _tls_legs is not None:
        _tls_legs.append({
            "distance_km": distance_km,
            "raw_min": round(raw_s / 60, 2),
            "v1_mult": mult,
            "v2_mult": mult_v2,
            "bin": v2_bin,
        })

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


# === OSRM-TABLE-03 (Front C audytu 03.06, 2026-06-12): per-cell cache table() ===
# table() NIE miało cache — każdy kandydat liczył świeży HTTP N×N, mimo że pary
# pickup↔drop/drop↔drop są wspólne między kandydatami tego samego zlecenia i
# między tickami (Białystok = skończony zbiór par). Cache trzyma komórki RAW
# (PRZED traffic multiplierem) — multiplier liczony świeżo per call z bieżącym
# `now` (identycznie jak _route_cache w route()): ZERO zmiany wyników, bo
# surowe czasy OSRM są niezmienne w czasie (statyczna mapa).
# Kill-switch hot-reload: ENABLE_OSRM_TABLE_CELL_CACHE w flags.json (OFF =
# dokładnie stara ścieżka). Fallback/circuit-breaker NIGDY nie cache'owane.
TABLE_CACHE_MAX_SIZE = 50000  # komórki (pary), ~kilkanaście B/klucz — tanie
_table_cell_cache: dict = {}  # {(o_key, d_key): (timestamp, raw_cell)}


def _table_cache_enabled() -> bool:
    try:
        return bool(_common_flag("ENABLE_OSRM_TABLE_CELL_CACHE", False))
    except Exception:  # noqa: BLE001 — cache to optymalizacja, nie zależność
        return False


def _table_cache_get(o_ll: tuple, d_ll: tuple) -> Optional[dict]:
    key = (_cache_key(o_ll), _cache_key(d_ll))
    with _module_lock:
        if key in _table_cell_cache:
            ts, raw = _table_cell_cache[key]
            if time.time() - ts < CACHE_TTL_SECONDS:
                return raw
            del _table_cell_cache[key]
    return None


def _table_cache_set(o_ll: tuple, d_ll: tuple, raw_cell: dict):
    key = (_cache_key(o_ll), _cache_key(d_ll))
    with _module_lock:
        if len(_table_cell_cache) >= TABLE_CACHE_MAX_SIZE:
            oldest = sorted(_table_cell_cache.items(),
                            key=lambda x: x[1][0])[: TABLE_CACHE_MAX_SIZE // 10]
            for k, _ in oldest:
                del _table_cell_cache[k]
        _table_cell_cache[key] = (time.time(), raw_cell)


def _decompose_miss_rects(miss: list, n_o: int, n_d: int) -> list:
    """≤2 prostokąty pokrywające wszystkie brakujące komórki (czysta funkcja).

    R1 = wiersze W PEŁNI brakujące × wszystkie kolumny (typowo: wiersz kuriera
    ze świeżym GPS — jedyny nowy punkt vs poprzednie wywołania).
    R2 = pozostałe wiersze-z-missami × unia ich kolumn (typowo: kolumna kuriera).
    Dla zimnego cache: R1 = pełna macierz, R2 puste (≡ stare zachowanie).
    Pokrycie: każdy miss jest w wierszu pełnym (R1) albo w R2 z konstrukcji.
    Zwraca listę (row_idxs, col_idxs).
    """
    by_row: dict = {}
    for i, j in miss:
        by_row.setdefault(i, set()).add(j)
    full_rows = sorted(i for i, cols in by_row.items() if len(cols) == n_d)
    rects = []
    if full_rows:
        rects.append((full_rows, list(range(n_d))))
    rest = {i: cols for i, cols in by_row.items() if i not in set(full_rows)}
    if rest:
        rows = sorted(rest)
        cols = sorted(set().union(*rest.values()))
        rects.append((rows, cols))
    return rects


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

    # V3.27 latency parallel: stats inkrement pod RLock.
    with _module_lock:
        _osrm_stats["calls_total"] += 1
    _maybe_log_stats()

    # Circuit breaker
    if _osrm_is_circuit_open():
        with _module_lock:
            _osrm_stats["calls_fallback"] += 1
        return _sentinel_invalid(_table_fallback(origins, destinations, now))

    # OSRM-TABLE-03: ścieżka cache (flaga OFF → dokładnie legacy full call).
    if _table_cache_enabled():
        n_o, n_d = len(origins), len(destinations)
        cells = [[_table_cache_get(o, d) for d in destinations] for o in origins]
        miss = [(i, j) for i in range(n_o) for j in range(n_d) if cells[i][j] is None]
        with _module_lock:
            _osrm_stats["table_cells_hit"] += n_o * n_d - len(miss)
            _osrm_stats["table_cells_miss"] += len(miss)
        if not miss:
            with _module_lock:
                _osrm_stats["table_full_hits"] += 1
            matrix = [[_apply_traffic_multiplier(dict(c), now) for c in row]
                      for row in cells]
            return _sentinel_invalid(matrix)
        rects = _decompose_miss_rects(miss, n_o, n_d)
        fetched_cells = sum(len(r) * len(c) for r, c in rects)
        # Dekompozycja opłacalna tylko gdy realnie tnie macierz; inaczej legacy
        # full call (1 HTTP, też zasila cache).
        if fetched_cells < n_o * n_d:
            ok = True
            for r_idx, c_idx in rects:
                sub = _table_http([origins[i] for i in r_idx],
                                  [destinations[j] for j in c_idx])
                # guard wymiarów: code=Ok z niepełną macierzą → legacy path
                # (stara ścieżka nigdy nie rzucała — ta też nie może)
                if (sub is None or len(sub) != len(r_idx)
                        or any(len(row) != len(c_idx) for row in sub)):
                    ok = False
                    break
                for a, i in enumerate(r_idx):
                    for b, j in enumerate(c_idx):
                        cells[i][j] = sub[a][b]
                        _table_cache_set(origins[i], destinations[j], sub[a][b])
            if ok:
                with _module_lock:
                    _osrm_stats["table_decomposed_calls"] += 1
                matrix = [[_apply_traffic_multiplier(dict(c), now) for c in row]
                          for row in cells]
                return _sentinel_invalid(matrix)
            # częściowy fail dekompozycji → spadnij na legacy full call niżej
            # (te same failure semantics co przed OSRM-TABLE-03)

    raw = _table_http(origins, destinations)
    if raw is None:
        with _module_lock:
            _osrm_stats["calls_fallback"] += 1
        return _sentinel_invalid(_table_fallback(origins, destinations, now))
    with _module_lock:
        _osrm_stats["table_legacy_calls"] += 1
    if _table_cache_enabled():
        # enumerate(raw) nie origins — krótka/poszarpana odpowiedź (code=Ok,
        # durations niepełne) nie może rzucić (stara ścieżka nie rzucała)
        for i, row in enumerate(raw):
            if i >= len(origins):
                break
            for j, cell in enumerate(row):
                if j >= len(destinations):
                    break
                _table_cache_set(origins[i], destinations[j], cell)
    matrix = [[_apply_traffic_multiplier(dict(c), now) for c in row] for row in raw]
    return _sentinel_invalid(matrix)


def _table_http(origins: list, destinations: list) -> Optional[list]:
    """Surowy HTTP table → macierz komórek RAW (BEZ traffic multiplier) albo
    None przy jakimkolwiek fail (caller decyduje o fallbacku — identyczne
    failure semantics co przed OSRM-TABLE-03). Aktualizuje circuit-breaker."""
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
            return None
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
        return None


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


# ---------------------------------------------------------------------------
# K04 refaktor (2026-07-06, ADR-R04): rekorder wyników route/table dla
# world_record. Proces-globalny (NIE thread-local/contextvar — pula wątków
# kandydatów w assess NIE dziedziczy contextvarów, a _tick ocenia decyzje
# SEKWENCYJNIE, więc okno start→stop = dokładnie jedna decyzja). Łapie też
# cache-hity (nagrywamy to, co decyzja SKONSUMOWAŁA). Nieaktywny = zero
# narzutu poza jednym if. Fail-soft: błąd nagrywania nigdy nie psuje wyniku.
# ---------------------------------------------------------------------------
_WR_LOCK = threading.Lock()
_WR_ACTIVE = False
_WR_CALLS: list = []
_WR_MAX_CALLS = 5000


def world_record_start() -> None:
    global _WR_ACTIVE
    with _WR_LOCK:
        _WR_CALLS.clear()
        _WR_ACTIVE = True


def world_record_stop() -> list:
    global _WR_ACTIVE
    with _WR_LOCK:
        _WR_ACTIVE = False
        out = list(_WR_CALLS)
        _WR_CALLS.clear()
    return out


def _wr_log(kind: str, key, result) -> None:
    if not _WR_ACTIVE:
        return
    try:
        with _WR_LOCK:
            if _WR_ACTIVE and len(_WR_CALLS) < _WR_MAX_CALLS:
                _WR_CALLS.append({"kind": kind, "key": key, "result": result})
    except Exception:
        pass


_route_impl_k04 = route
_table_impl_k04 = table


def route(from_ll: tuple, to_ll: tuple, use_cache: bool = True) -> dict:  # noqa: F811 — świadome opakowanie K04
    res = _route_impl_k04(from_ll, to_ll, use_cache=use_cache)
    _wr_log("route", [list(from_ll or ()), list(to_ll or ())], res)
    return res


def table(origins: list, destinations: list) -> list:  # noqa: F811 — świadome opakowanie K04
    res = _table_impl_k04(origins, destinations)
    _wr_log("table", [[list(o or ()) for o in (origins or [])],
                      [list(d or ()) for d in (destinations or [])]], res)
    return res
