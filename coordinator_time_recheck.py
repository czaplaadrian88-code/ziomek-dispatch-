"""Kolejka WYMUSZONEGO odświeżenia czasów (czas_kuriera / pickup_at) z rutcomu na
ŻĄDANIE koordynatora (przycisk „Odśwież czas" w konsoli).

Po co: automatyczny re-check (panel_watcher ORDER-TIME RE-CHECK) świadomie pomija
elastyki w statusie `planned` (koszt) i blokuje cofnięcia czas_kuriera elastyka
(forward-only, anty-migotanie vs śmieciowe przeklepywanie gastro). Gdy KOORDYNATOR
RĘCZNIE zmieni czas w rutcomie i kliknie przycisk, to ŚWIADOMA akcja człowieka —
chcemy ściągnąć nowy czas dla DOWOLNEGO zlecenia (też planned) i w OBIE strony, bez
osłabiania automatu. Kliknięcie = dyskryminator „to nie śmieć, to decyzja".

Przepływ: panel (subprocess w venv Ziomka, jak courier_block) → `enqueue(oids)` →
panel_watcher raz na tick → `drain()` → bezwarunkowy re-check z `deliberate=True`
(omija scope + strażniki, source="coordinator_force" ∉ _CK_PASSIVE_SOURCES → przechodzi
w state_machine w obie strony). Atomic + flock + TTL (przeterminowany klik ignorowany).
Z3: jedno źródło prawdy zapisu (panel deleguje tu, nie dubluje logiki).
"""
from __future__ import annotations

import contextlib
import fcntl
import json
import os
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

QUEUE_PATH = "/root/.openclaw/workspace/dispatch_state/coordinator_time_recheck.json"
LOCK_PATH = QUEUE_PATH + ".lock"
DEFAULT_TTL_MIN = 5.0  # klik starszy niż to = „przeterminowany" (watcher mógł stać) → ignoruj


@contextlib.contextmanager
def _lockfile():
    """File lock dla atomic read-modify-write (panel pisze, watcher drenuje)."""
    fh = open(LOCK_PATH, "w")
    try:
        fcntl.flock(fh.fileno(), fcntl.LOCK_EX)
        yield
    finally:
        try:
            fcntl.flock(fh.fileno(), fcntl.LOCK_UN)
        except Exception:
            pass
        fh.close()


def _load() -> dict:
    if not os.path.exists(QUEUE_PATH):
        return {}
    try:
        with open(QUEUE_PATH, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _save(data: dict) -> None:
    """Atomic write (temp + fsync + rename). Caller trzyma lock."""
    p = Path(QUEUE_PATH)
    p.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="coordinator_time_recheck.", suffix=".tmp",
                               dir=str(p.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, QUEUE_PATH)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def enqueue(oids) -> int:
    """Dopisz oid(y) do kolejki ze stemplem teraz (UTC). Zwraca liczbę dopisanych.
    Wołane przez panel (subprocess) po kliknięciu „Odśwież czas". Idempotentne
    (ponowny klik nadpisuje stempel = odświeża TTL)."""
    oids = [str(o).strip() for o in (oids or []) if str(o).strip()]
    if not oids:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    with _lockfile():
        data = _load()
        for o in oids:
            data[o] = now
        _save(data)
    return len(oids)


def drain(ttl_min: float = DEFAULT_TTL_MIN) -> set:
    """Zwróć zbiór oid do WYMUSZENIA (świeższych niż TTL) i WYCZYŚĆ kolejkę
    (przeterminowane też wyrzuć). Wołane przez panel_watcher raz na tick pod lockiem.
    Świeże oid są zwracane DO przetworzenia w tym samym ticku."""
    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=ttl_min)
    fresh: set = set()
    with _lockfile():
        data = _load()
        if not data:
            return fresh
        for oid, ts in data.items():
            try:
                t = datetime.fromisoformat(str(ts))
                if t.tzinfo is None:
                    t = t.replace(tzinfo=timezone.utc)
                if t >= cutoff:
                    fresh.add(str(oid))
            except (ValueError, TypeError):
                continue
        _save({})  # świeże skonsumowane (zwrócone), przeterminowane wyrzucone
    return fresh
