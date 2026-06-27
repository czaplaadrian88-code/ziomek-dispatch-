"""global_alloc_store — dedykowany kanał globalnej alokacji DLA KONSOLI (Faza C).

Po audycie 27.06: NIE re-emitować globalnej alokacji do współdzielonego
`shadow_decisions.jsonl` (psułoby koord_cascade_monitor + bazę panelu = wieczna łatka).
Zamiast tego — wzorzec `live_eta_cache` (sprawdzony LIVE od 22.06): osobny mały plik
nadpisywany co tick przez resweep (proces poza gorącą ścieżką dispatchu), czytany przez
konsolę (feed.py overlay). `shadow_decisions.jsonl` zostaje CZYSTY.

Zawartość: {"written_at": iso, "proposals": {oid: decision_record}} gdzie decision_record =
wynik `shadow_dispatcher._serialize_result` (ten sam kształt co linia shadow_decisions →
feed.py `_proposal_from_decision` parsuje identycznie, zero nowej logiki renderu).

OVERWRITE per tick (nie merge): resweep zapisuje BIEŻĄCY zbiór wiszących; zlecenie które
przestało wisieć (przypisane) znika z pliku → konsola wraca do shadow_decisions dla niego.
written_at = bezpiecznik świeżości: gdy resweep padnie, konsola ignoruje stary plik.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

GLOBAL_ALLOC_PATH = "/root/.openclaw/workspace/dispatch_state/global_alloc.json"
DEFAULT_TTL_SEC = 120  # resweep co 60s → 2-min okno świeżości (pełne nadpisanie per tick)


def write(proposals: Dict[str, Any], now: datetime, path: str = GLOBAL_ALLOC_PATH) -> int:
    """Atomowo nadpisz plik bieżącym zbiorem propozycji. Zwraca liczbę wpisów.
    Fail-soft: błąd → 0 (NIGDY nie wywala resweep)."""
    try:
        payload = {"written_at": now.isoformat(), "proposals": proposals or {}}
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
        return len(proposals or {})
    except Exception:
        return 0


def _parse_iso(s: Any) -> Optional[datetime]:
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except (ValueError, TypeError):
        return None


def load_fresh(now: datetime, ttl_sec: int = DEFAULT_TTL_SEC,
               path: str = GLOBAL_ALLOC_PATH) -> Dict[str, Any]:
    """Wczytaj {oid: decision_record} TYLKO gdy plik świeży (written_at w oknie ttl).
    Stary/brak/uszkodzony → {} (konsola wraca do shadow_decisions). Fail-soft."""
    try:
        with open(path, encoding="utf-8") as f:
            d = json.load(f)
    except FileNotFoundError:
        return {}
    except Exception:
        return {}
    if not isinstance(d, dict):
        return {}
    wa = _parse_iso(d.get("written_at"))
    if wa is None:
        return {}
    if (now - wa).total_seconds() > ttl_sec:
        return {}
    props = d.get("proposals")
    return props if isinstance(props, dict) else {}
