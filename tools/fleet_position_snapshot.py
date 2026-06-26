#!/usr/bin/env python3
"""fleet_position_snapshot.py — offline narzędzie READ-ONLY, snapshotuje pozycje floty co ~2 min
do trwałego pliku JSONL (per-tick snapshot {cid, lat, lng, pos_source, bag_size, shift_end, …}).

NIE mutuje stanu, NIE woła Telegrama, NIE dotyka silnika (tylko pobiera dispatchable_fleet).
Fail-soft – pojedynczy błąd nie wywala procesu (read-only obserwator).
"""

import sys
sys.path.insert(0, "/root/.openclaw/workspace/scripts")

import json
import os
import logging
from datetime import datetime, timezone

from dispatch_v2 import courier_resolver as CR

_log = logging.getLogger("fleet_position_snapshot")

OUT_JSONL = "/root/.openclaw/workspace/dispatch_state/fleet_position_history.jsonl"


def _now_iso() -> str:
    """Zwraca bieżący czas UTC jako string ISO 8601."""
    return datetime.now(timezone.utc).isoformat()


def _row(tick_ts: str, cs) -> dict:
    """Tworzy rekord JSONL dla jednego kuriera na podstawie stanu CourierState."""
    # pozycja
    pos = getattr(cs, "pos", None)
    if isinstance(pos, (list, tuple)) and len(pos) >= 2:
        lat = pos[0]
        lng = pos[1]
    else:
        lat = None
        lng = None

    bag = getattr(cs, "bag", None)
    se = getattr(cs, "shift_end", None)
    shift_str = se.isoformat() if se is not None else None

    return {
        "tick_ts": tick_ts,
        "cid": str(getattr(cs, "courier_id", "")) or None,
        "name": getattr(cs, "name", None),
        "lat": lat,
        "lng": lng,
        "pos_source": getattr(cs, "pos_source", None),
        "pos_age_min": getattr(cs, "pos_age_min", None),
        "bag_size": len(bag) if bag is not None else None,
        "shift_end": shift_str,
    }


def _append_jsonl(rows: list, path: str = OUT_JSONL) -> None:
    """Dopisuje listę rekordów do pliku JSONL (jeden obiekt na linię). Fail-soft."""
    if not rows:
        return
    try:
        with open(path, "a", encoding="utf-8") as f:
            for r in rows:
                f.write(json.dumps(r, ensure_ascii=False) + "\n")
            f.flush()
            os.fsync(f.fileno())
    except Exception as e:
        _log.warning(f"_append_jsonl fail: {e}")


def run_once() -> dict:
    """Wykonuje jeden snapshot: pobiera flotę, zapisuje pozycje do JSONL.

    Zwraca słownik z podsumowaniem ticku.
    """
    tick_ts = _now_iso()
    # pobranie floty – dispatchable_fleet (enriched o shift_end)
    try:
        fleet = CR.dispatchable_fleet()
    except Exception as e:
        _log.warning(f"dispatchable_fleet failed: {e}")
        return {"error": "fleet_load", "tick_ts": tick_ts}

    rows = [_row(tick_ts, cs) for cs in fleet]
    _append_jsonl(rows, OUT_JSONL)

    # zliczamy pozycje, które pochodzą z prawdziwego GPS / ostatniej znanej lokalizacji
    trusted_sources = {
        "gps", "last_picked_up", "last_delivered", "last_assigned", "last_known", "store"
    }
    n_real = sum(1 for r in rows if r.get("pos_source") in trusted_sources)

    summary = {
        "tick_ts": tick_ts,
        "n_couriers": len(rows),
        "n_real_pos": n_real,
    }
    return summary


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    summary = run_once()
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
