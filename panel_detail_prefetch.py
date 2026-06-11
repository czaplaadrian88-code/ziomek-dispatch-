"""PANEL-SCRAPE-01 (Front C audytu 03.06, P0) — równoległy pre-fetch detali zleceń.

Problem: panel_watcher fetchuje detale (POST edit-zamowienie) SEKWENCYJNIE
~0.3 s/zlecenie na głównej sesji — mediana 42 fetchy/tick = ~12.6 s, p95 ticka
23.7 s > interwał 20 s (zmierzone 2026-06-12 na watcher.log). Przy 2x ruchu
ticki lecą back-to-back i opóźniają wykrycie nowego zlecenia o dziesiątki sekund.

Wzorzec bezpieczny (CLAUDE.md NIGDY: urllib CookieJar nie jest thread-safe,
edit-zamowienie na GŁÓWNEJ sesji musi zostać sekwencyjne):
  - pula NIEZALEŻNYCH sesji workerów — każdy wątek ma WŁASNY opener+CookieJar
    i WŁASNY login (panel_client._perform_login, kanoniczny flow). Zero
    kontaktu z panel_client._session / install_opener (landmine 419).
  - chunking deterministyczny: zids[i::workers] → jedna sesja = jeden wątek,
    żadna sesja nie jest współdzielona między wątkami.
  - wyniki scalane PO join (bez locków na hot-path).
  - deadline miękki: po PREFETCH_DEADLINE_SEC wątek przestaje fetchować,
    resztki = miss → sekwencyjny fallback w panel_watcher (stare zachowanie).

Semantyka cache per-tick:
  - zid W mapie (nawet z wartością None = panel odpowiedział bez 'zlecenie')
    → użyj wartości, NIE fetchuj ponownie.
  - zid POZA mapą (exception/HTTP error/deadline) → fallback do
    fetch_order_details na głównej sesji (identyczne retry semantics co dziś).

Kill-switch: ENABLE_PANEL_DETAIL_PREFETCH w flags.json (hot-reload; False →
prefetch zwraca pustą mapę = 100% sekwencyjny fallback, zachowanie sprzed
zmiany). Workerzy: PANEL_DETAIL_PREFETCH_WORKERS w flags.json (default 4,
clamp 1..8) — network-bound, 4 vCPU.
"""
from __future__ import annotations

import json
import logging
import threading
import time
import urllib.error
from typing import Dict, List, Optional, Tuple

from dispatch_v2 import common as C
from dispatch_v2 import panel_client as PC

_log = logging.getLogger("panel_detail_prefetch")

PREFETCH_DEADLINE_SEC = 12.0   # miękki deadline całego prefetchu (tick=20 s)
PREFETCH_FETCH_TIMEOUT = 8     # per-request (główna ścieżka używa 10)
MIN_BATCH_FOR_THREADS = 3      # poniżej — nie warto wątków, fallback sekwencyjny
SESSION_TTL_SEC = 1200         # spójnie z panel_client (20 min)


class _WorkerSession:
    """Niezależna sesja panelu dla jednego wątku prefetchu.

    Własny opener+CookieJar+csrf z pełnego loginu (_perform_login). Re-login
    przy 401/419 (raz per fetch) i po TTL. NIE dotyka panel_client._session.
    """

    def __init__(self, idx: int):
        self.idx = idx
        self.opener = None
        self.csrf: Optional[str] = None
        self.last_login_at = 0.0

    def _login(self) -> None:
        opener, _cj, csrf, _html = PC._perform_login()
        self.opener = opener
        self.csrf = csrf
        self.last_login_at = time.time()
        _log.info(f"prefetch worker[{self.idx}]: fresh login OK")

    def ensure(self) -> None:
        if self.opener is None or (time.time() - self.last_login_at) > SESSION_TTL_SEC:
            self._login()

    def fetch(self, zid: str, timeout: int = PREFETCH_FETCH_TIMEOUT) -> Optional[dict]:
        """Zwraca dict 'zlecenie' / None (legit brak). Rzuca przy twardym błędzie
        (caller traktuje jako miss → fallback)."""
        self.ensure()
        for attempt in range(2):
            req = PC._details_request(self.csrf, zid)
            try:
                raw = self.opener.open(req, timeout=timeout).read().decode(
                    "utf-8", errors="replace")
                return PC._extract_zlecenie(json.loads(raw))
            except urllib.error.HTTPError as he:
                if he.code in (401, 419) and attempt == 0:
                    _log.warning(
                        f"prefetch worker[{self.idx}]: HTTP {he.code} → re-login + retry")
                    self._login()
                    continue
                raise
        raise RuntimeError("unreachable")


_sessions: List[_WorkerSession] = []
_sessions_lock = threading.Lock()


def _get_sessions(n: int) -> List[_WorkerSession]:
    with _sessions_lock:
        while len(_sessions) < n:
            _sessions.append(_WorkerSession(len(_sessions)))
        return _sessions[:n]


def _run_chunk(session: _WorkerSession, zids: List[str], out: Dict[str, Optional[dict]],
               errors: List[str], deadline: float) -> None:
    """Jeden wątek = jedna sesja = jeden chunk. `out`/`errors` są per-chunk
    (scalane po join) — zero współdzielenia między wątkami."""
    for zid in zids:
        if time.time() > deadline:
            break  # resztki = miss → sekwencyjny fallback
        try:
            out[zid] = session.fetch(zid)
        except Exception as e:  # noqa: BLE001 — miss, fallback obsłuży
            errors.append(f"{zid}:{type(e).__name__}")


def prefetch_details(zids: List[str]) -> Tuple[Dict[str, Optional[dict]], dict]:
    """Równoległy pre-fetch detali. Zwraca (mapa zid→zlecenie|None, stats).

    Mapa zawiera TYLKO udane odpowiedzi (w tym legit None). Błędy → zid poza
    mapą → caller robi sekwencyjny fallback. Nigdy nie rzuca.
    """
    stats = {"prefetch_enabled": False, "prefetch_requested": 0,
             "prefetch_fetched": 0, "prefetch_errors": 0, "prefetch_s": 0.0}
    try:
        if not C.flag("ENABLE_PANEL_DETAIL_PREFETCH", False):
            return {}, stats
        uniq = list(dict.fromkeys(z for z in zids if z))
        stats["prefetch_enabled"] = True
        stats["prefetch_requested"] = len(uniq)
        if len(uniq) < MIN_BATCH_FOR_THREADS:
            return {}, stats

        try:
            workers = int(C.load_flags().get("PANEL_DETAIL_PREFETCH_WORKERS", 4))
        except Exception:  # noqa: BLE001
            workers = 4
        workers = max(1, min(8, min(workers, len(uniq))))

        t0 = time.time()
        deadline = t0 + PREFETCH_DEADLINE_SEC
        sessions = _get_sessions(workers)
        chunks = [uniq[i::workers] for i in range(workers)]
        outs: List[Dict[str, Optional[dict]]] = [{} for _ in range(workers)]
        errs: List[List[str]] = [[] for _ in range(workers)]
        threads = []
        for i in range(workers):
            t = threading.Thread(
                target=_run_chunk,
                args=(sessions[i], chunks[i], outs[i], errs[i], deadline),
                name=f"detail_prefetch_{i}", daemon=True,
            )
            t.start()
            threads.append(t)
        # join z marginesem na trwający request (deadline + timeout + slack)
        join_by = deadline + PREFETCH_FETCH_TIMEOUT + 2.0
        for t in threads:
            t.join(max(0.1, join_by - time.time()))

        merged: Dict[str, Optional[dict]] = {}
        for o in outs:
            merged.update(o)
        all_errs = [e for el in errs for e in el]
        stats["prefetch_fetched"] = len(merged)
        stats["prefetch_errors"] = len(all_errs)
        stats["prefetch_s"] = round(time.time() - t0, 1)
        if all_errs:
            _log.warning(
                f"prefetch: {len(all_errs)} błędów (miss→fallback): "
                f"{all_errs[:5]}{'…' if len(all_errs) > 5 else ''}")
        _log.info(
            f"prefetch: {len(merged)}/{len(uniq)} w {stats['prefetch_s']}s "
            f"(workers={workers})")
        return merged, stats
    except Exception as e:  # noqa: BLE001 — prefetch to optymalizacja, nie zależność
        _log.warning(f"prefetch fail (non-blocking, full fallback): "
                     f"{type(e).__name__}: {e}")
        stats["prefetch_errors"] = stats.get("prefetch_errors", 0) + 1
        return {}, stats
