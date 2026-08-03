"""A-6 SECURITY P0 (2026-08-02) — rate-limit endpointu GPS `gps_server` POST /gps.

DEFEKT ŹRÓDŁOWY: endpoint POST /gps (PWA, port 8766, `gps_server.do_POST`) NIE miał
ŻADNEGO limitu częstości. Zalogowany PIN-em klient mógł zalać writer setkami pozycji
na sekundę → obciążenie kanonicznego writera (`gps_pwa_store`), telemetrii i I/O,
oraz zaśmiecenie feedu pozycji. Bliźniak `courier_api` ma limit LOGOWANIA
(`auth.py` pin_attempts, per-kurier/IP/global) — ale to limit prób PIN, nie floodu
pozycji, i dotyczy innego procesu (mapa kompletności w raporcie).

FIX: sliding-window limiter PER-KURIER (primary — bezpośrednio dławi „flood pozycji"
danego kuriera) ORAZ PER-IP (secondary, best-effort — gruby bezpiecznik na naiwny
flood z jednego źródła), ZA flagą `ENABLE_GPS_RATE_LIMIT` (default OFF = legacy,
ZERO limitu, bajt-parytet zachowania `do_POST`). Progi >> legalna kadencja
(watchPosition co 30 s ≈ 2/min/kurier) → legalny ruch NIGDY nie dławiony; flood
(dziesiątki+/min) dławiony HTTP 429.

In-memory (proces `gps_server` żyje długo, stdlib-only, `ThreadingMixIn`) + `Lock`.
Stan ginie z procesem (limit floodu, nie trwały rejestr) — wystarcza; trwały audyt
prób = domena `courier_api`. NIE loguje PIN-u.

Uwaga proxy: za nginx `client_address` to IP proxy → per-IP staje się limitem
zbiorczym. `gps_server` liczy IP best-effort (X-Forwarded-For leftmost gdy peer
loopback), więc per-IP pozostaje ~per-klient; per-KURIER (post-auth) jest kontrolą
autorytatywną i niewrażliwą na spoofing XFF.
"""
import threading
import time
from collections import defaultdict, deque
from typing import Optional, Tuple

# ── progi (nagłówek ~10x nad legalną kadencją watchPosition 30 s) ────────────
PER_COURIER_MAX = 20        # zdarzeń w oknie / kurier
PER_COURIER_WINDOW_S = 60
PER_IP_MAX = 60            # zdarzeń w oknie / IP (kilku kurierów za NAT)
PER_IP_WINDOW_S = 60

SCOPE_IP = "ip"
SCOPE_COURIER = "courier"


class SlidingWindowLimiter:
    """Prosty licznik sliding-window per klucz. `allow` rejestruje zdarzenie i
    zwraca False gdy okno pełne. `now` wstrzykiwalny → deterministyczne testy."""

    def __init__(self):
        self._events = defaultdict(deque)  # key -> deque[monotonic ts]
        self._lock = threading.Lock()

    def allow(self, key, max_events: int, window_s: float,
              now: Optional[float] = None) -> bool:
        now = time.monotonic() if now is None else float(now)
        cutoff = now - window_s
        with self._lock:
            dq = self._events[key]
            while dq and dq[0] <= cutoff:
                dq.popleft()
            if len(dq) >= max_events:
                return False
            dq.append(now)
            return True

    def reset(self) -> None:
        with self._lock:
            self._events.clear()


# Współdzielony limiter procesu `gps_server`. Testy wstrzykują własny (izolacja).
_limiter = SlidingWindowLimiter()


def rate_limit_enabled() -> bool:
    """Odczyt `ENABLE_GPS_RATE_LIMIT` z `flags.json` (hot-reload). Fail-soft → OFF."""
    try:
        from dispatch_v2.common import flag
        # Literalna nazwa — wymóg walidatora rejestru flag (literal_flag_calls).
        return bool(flag("ENABLE_GPS_RATE_LIMIT", False))
    except Exception:
        return False


def check(courier_id: Optional[str], ip: Optional[str], *,
          enabled: Optional[bool] = None,
          now: Optional[float] = None,
          limiter: Optional[SlidingWindowLimiter] = None) -> Optional[str]:
    """Zwraca None gdy dozwolone; nazwę scope (`'ip'`/`'courier'`) gdy ZDŁAWIONE.

    Sprawdza tylko te wymiary, dla których podano klucz (courier_id/ip nie-None) —
    `gps_server` woła per-IP przed auth (courier_id=None) i per-kurier po auth
    (ip=None). enabled: None → czytaj flagę; True/False → wymuś (testy).

    OFF → zawsze None (legacy, zero dławienia, bajt-parytet `do_POST`)."""
    on = rate_limit_enabled() if enabled is None else bool(enabled)
    if not on:
        return None
    lim = limiter if limiter is not None else _limiter
    if ip is not None and not lim.allow(
            (SCOPE_IP, str(ip)), PER_IP_MAX, PER_IP_WINDOW_S, now=now):
        return SCOPE_IP
    if courier_id is not None and not lim.allow(
            (SCOPE_COURIER, str(courier_id)), PER_COURIER_MAX,
            PER_COURIER_WINDOW_S, now=now):
        return SCOPE_COURIER
    return None


def limits_snapshot() -> Tuple[int, int, int, int]:
    """(per_courier_max, per_courier_window_s, per_ip_max, per_ip_window_s) — do
    asercji/telemetrii bez sięgania po stałe modułu wprost."""
    return (PER_COURIER_MAX, PER_COURIER_WINDOW_S, PER_IP_MAX, PER_IP_WINDOW_S)
