"""live_eta_cache — świeże per-zlecenie ETA dostawy/odbioru z KAŻDEJ decyzji Ziomka.

Problem (2026-06-22, Adrian): apka kuriera + konsola koordynatora pokazują czas
dostawy z `courier_plans.json` (zapisany plan, odświeżany przez plan_recheck z
lagiem ≤5 min i dla PRZYPISANEGO kuriera). Telegram pokazuje świeży
`predicted_delivered_at` z bieżącej decyzji → DOBRE czasy, aktualizowane przy
każdej propozycji ("nowe propozycje = nowe czasy starych zleceń"). Rozjazd: te
same dane, różne źródło — powierzchnie czytają gorsze/lagujące.

Fix: shadow_dispatcher po każdej decyzji upsertuje tu świeże predicted_delivered_at
(to SAMO źródło co trasa Telegrama). Apka (`build_view`) i konsola (`_build_route`)
czytają ten cache dla czasu dostawy — wszystkie 3 powierzchnie spójne.

Kontrakt:
  upsert(predicted_delivered_at, pickup_at, courier_id)  — writer (silnik).
  load() -> {oid: {...}}                                   — reader (apka/konsola).
  get(oid, max_age_min=...) -> dict|None                   — reader pojedynczego oid.

Plik: dispatch_state/live_order_eta.json. Atomic (temp+fsync+rename). Fail-soft:
każdy błąd → no-op (writer) / {} (reader). NIE wpływa na decyzję/score/feasibility.
Wpis nieświeższy niż TTL (default 20 min) jest ignorowany przy odczycie (reader),
a stary stan jest pruned przy zapisie żeby plik nie puchł.
"""

from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, Optional

CACHE_FILE = Path("/root/.openclaw/workspace/dispatch_state/live_order_eta.json")

# Wpisy starsze niż to (od `decided_at`) są ignorowane przy odczycie i usuwane
# przy zapisie. 20 min = bezpieczny bufor ponad cykl decyzji (czasówka/plan-recheck),
# a zlecenie nieaktualizowane >20 min i tak jest po dostawie albo stale.
DEFAULT_TTL_MIN = 20.0


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_iso(s) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def _read_raw() -> Dict[str, dict]:
    try:
        with open(CACHE_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except Exception:
        return {}


def _atomic_write(data: Dict[str, dict]) -> None:
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(CACHE_FILE.parent), prefix=".live_eta_", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, CACHE_FILE)
    except Exception:
        try:
            os.unlink(tmp)
        except Exception:
            pass
        raise


def upsert(
    predicted_delivered_at: Optional[dict],
    pickup_at: Optional[dict] = None,
    courier_id=None,
    ttl_min: float = DEFAULT_TTL_MIN,
) -> bool:
    """Wpisz świeże czasy dostaw/odbiorów z bieżącej decyzji. Latest-wins per oid.

    predicted_delivered_at / pickup_at: {oid: iso_utc}. courier_id: proponowany kurier.
    Zwraca True gdy zapisano, False gdy nic do zapisania / błąd (fail-soft).
    Stare wpisy (decided_at starszy niż ttl_min) są usuwane przy okazji.
    """
    if not predicted_delivered_at:
        return False
    try:
        now = _now_utc()
        now_iso = now.isoformat()
        pickup_at = pickup_at or {}
        data = _read_raw()
        # prune stale
        cutoff = now.timestamp() - ttl_min * 60.0
        for k in list(data.keys()):
            d = data.get(k) or {}
            dt = _parse_iso(d.get("decided_at"))
            if dt is None or dt.timestamp() < cutoff:
                data.pop(k, None)
        # upsert fresh
        cid = None if courier_id is None else str(courier_id)
        for oid, deliv_iso in predicted_delivered_at.items():
            if not deliv_iso:
                continue
            data[str(oid)] = {
                "delivery_iso": deliv_iso,
                "pickup_iso": pickup_at.get(oid),
                "courier_id": cid,
                "decided_at": now_iso,
            }
        _atomic_write(data)
        return True
    except Exception:
        return False


def load(max_age_min: float = DEFAULT_TTL_MIN) -> Dict[str, dict]:
    """Zwróć {oid: entry} z wpisami świeższymi niż max_age_min. Fail-soft → {}."""
    try:
        data = _read_raw()
        if not data:
            return {}
        cutoff = _now_utc().timestamp() - max_age_min * 60.0
        out = {}
        for oid, entry in data.items():
            if not isinstance(entry, dict):
                continue
            dt = _parse_iso(entry.get("decided_at"))
            if dt is not None and dt.timestamp() >= cutoff:
                out[str(oid)] = entry
        return out
    except Exception:
        return {}


def get(oid, max_age_min: float = DEFAULT_TTL_MIN) -> Optional[dict]:
    """Świeży wpis dla jednego oid albo None. Fail-soft."""
    try:
        data = _read_raw()
        entry = data.get(str(oid))
        if not isinstance(entry, dict):
            return None
        dt = _parse_iso(entry.get("decided_at"))
        if dt is None:
            return None
        if dt.timestamp() < _now_utc().timestamp() - max_age_min * 60.0:
            return None
        return entry
    except Exception:
        return None
