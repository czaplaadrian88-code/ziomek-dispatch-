"""State Machine zlecen - jedyne zrodlo prawdy o stanie kazdego zlecenia.

Kluczowe wlasciwosci:
- Atomic writes: temp -> fsync -> rename
- File lock: fcntl.flock zapobiega race condition miedzy procesami
- History per zlecenie: pelny audit trail
- Integracja z event bus: update_from_event() konsumuje eventy
- Statusy: planned -> assigned -> picked_up -> delivered (+ returned_to_pool)
- Commitment levels: planned / assigned / arrived_at_pickup / picked_up / en_route / near_delivery
"""
import fcntl
import json
import os
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from dispatch_v2.common import load_config, now_iso, setup_logger

# Zamkniete statusy zlecenia
ORDER_STATUSES = {
    "planned",          # widoczne, jeszcze nieprzypisane
    "assigned",         # przypisane kurierowi (propozycja zatwierdzona)
    "picked_up",        # kurier odebral z restauracji
    "delivered",        # dostarczone
    "returned_to_pool", # wrocilo do puli (partial split / tear-down)
    "cancelled",        # anulowane (klient/restauracja)
}

# Commitment levels (6 poziomow, opinia #6)
COMMITMENT_LEVELS = {
    "planned": 1.0,
    "assigned": 1.2,
    "arrived_at_pickup": 1.5,
    "picked_up": 2.0,
    "en_route_delivery": 2.5,
    "near_delivery": 3.0,
}

_log = setup_logger("state_machine", "/root/.openclaw/workspace/scripts/logs/dispatch.log")


def _state_path() -> str:
    return load_config()["paths"]["orders_state"]


@contextmanager
def _locked_write():
    """Kontekst: otwiera lock file, trzyma exclusive lock, zwraca sciezke state file.
    Dopiero po yield mozna zapisywac atomic."""
    state_path = Path(_state_path())
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = str(state_path) + ".lock"
    lock_fd = open(lock_path, "w")
    try:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_EX)
        yield state_path
    finally:
        fcntl.flock(lock_fd.fileno(), fcntl.LOCK_UN)
        lock_fd.close()


def _atomic_write(path: Path, data: dict):
    """Zapis temp -> fsync -> rename (atomic na POSIX)."""
    tmp_fd, tmp_path = tempfile.mkstemp(
        dir=str(path.parent), prefix=".tmp_", suffix=".json"
    )
    try:
        with os.fdopen(tmp_fd, "w") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.rename(tmp_path, path)
    except Exception:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise


def _read_state() -> dict:
    """Czyta state (bez locka - tylko read). Zwraca {} jesli nie istnieje."""
    path = Path(_state_path())
    if not path.exists():
        return {}
    try:
        with open(path) as f:
            return json.load(f)
    except json.JSONDecodeError as e:
        _log.error(f"JSONDecodeError w {path}: {e}. Zwracam pusty state.")
        return {}


def get_all() -> dict:
    """Zwraca caly state. Uzywaj ostroznie - kopiuj jesli modyfikujesz."""
    return _read_state()


def get_order(order_id: str) -> Optional[dict]:
    """Zwraca pojedyncze zlecenie lub None."""
    return _read_state().get(order_id)


def get_by_status(status: str) -> list:
    """Zwraca liste zlecen w danym statusie."""
    state = _read_state()
    return [o for o in state.values() if o.get("status") == status]


def get_by_courier(courier_id: str, statuses: Optional[list] = None) -> list:
    """Zwraca zlecenia przypisane kurierowi. Opcjonalny filtr statusow."""
    state = _read_state()
    result = [o for o in state.values() if o.get("courier_id") == courier_id]
    if statuses:
        result = [o for o in result if o.get("status") in statuses]
    return result


def upsert_order(order_id: str, data: dict, event: Optional[str] = None) -> dict:
    """Dodaje lub aktualizuje zlecenie. Zapisuje history entry.
    Zwraca zaktualizowany rekord."""
    with _locked_write() as path:
        state = _read_state()
        existing = state.get(order_id, {})
        merged = {**existing, **data, "order_id": order_id}

        # History
        history = existing.get("history", [])
        if event:
            history.append({"at": now_iso(), "event": event, "status": merged.get("status")})
        merged["history"] = history
        merged["updated_at"] = now_iso()

        state[order_id] = merged
        _atomic_write(path, state)
        _log.info(f"upsert {order_id} status={merged.get('status')} event={event}")
        return merged


def set_status(order_id: str, status: str, extra: Optional[dict] = None, event: Optional[str] = None) -> Optional[dict]:
    """Zmiana statusu + dodatkowe pola."""
    if status not in ORDER_STATUSES:
        raise ValueError(f"Nieznany status: {status}. Dozwolone: {ORDER_STATUSES}")
    data = {"status": status}
    if extra:
        data.update(extra)
    return upsert_order(order_id, data, event=event)


def update_from_event(event: dict) -> Optional[dict]:
    """Konsumuje event z event busa i aktualizuje state machine.
    Zwraca zaktualizowany rekord lub None."""
    etype = event["event_type"]
    oid = event.get("order_id")
    payload = event.get("payload", {})
    if not oid:
        return None

    if etype == "NEW_ORDER":
        return upsert_order(oid, {
            "status": "planned",
            "commitment_level": "planned",
            "restaurant": payload.get("restaurant"),
            "pickup_address": payload.get("pickup_address"),
            "delivery_address": payload.get("delivery_address"),
            "pickup_time_minutes": payload.get("pickup_time_minutes"),
            "first_seen": payload.get("first_seen", now_iso()),
            "address_id": payload.get("address_id"),
            "pickup_coords": payload.get("pickup_coords"),
            "delivery_coords": payload.get("delivery_coords"),
            "pickup_at_warsaw": payload.get("pickup_at_warsaw"),
            "prep_minutes": payload.get("prep_minutes"),
            "order_type": payload.get("order_type"),
        }, event="NEW_ORDER")

    if etype == "COURIER_ASSIGNED":
        return upsert_order(oid, {
            "status": "assigned",
            "commitment_level": "assigned",
            "courier_id": event.get("courier_id"),
            "assigned_at": now_iso(),
            "proposed_delivery_time": payload.get("proposed_time"),
        }, event="COURIER_ASSIGNED")

    if etype == "COURIER_PICKED_UP":
        picked = payload.get("timestamp", now_iso())
        # expected_delivery_by = picked + 35 min (SLA)
        try:
            # panel timestamps sa naive Warsaw, dorzuc UTC jako fallback
            if "T" in picked or "Z" in picked:
                picked_dt = datetime.fromisoformat(picked.replace("Z", "+00:00"))
            else:
                # "2026-04-11 18:01:47" = naive Warsaw
                from zoneinfo import ZoneInfo
                picked_dt = datetime.strptime(picked, "%Y-%m-%d %H:%M:%S").replace(tzinfo=ZoneInfo("Europe/Warsaw"))
        except Exception:
            picked_dt = datetime.now(timezone.utc)
        expected = (picked_dt + timedelta(minutes=35)).isoformat()
        pickup_coords = payload.get("pickup_coords")
        update_fields = {
            "status": "picked_up",
            "commitment_level": "picked_up",
            "picked_up_at": picked,
            "expected_delivery_by": expected,
            "assigned_check_ts": now_iso(),
        }
        if pickup_coords:
            update_fields["pickup_coords"] = pickup_coords
        return upsert_order(oid, update_fields, event="COURIER_PICKED_UP")

    if etype == "COURIER_DELIVERED":
        deliv_addr = payload.get("delivery_address") or payload.get("final_location")
        deliv_coords = None
        if deliv_addr:
            try:
                from dispatch_v2.geocoding import geocode
                r = geocode(deliv_addr)
                if r:
                    deliv_coords = [round(float(r[0]), 6), round(float(r[1]), 6)]
            except Exception as _e:
                pass  # geocode fail nie blokuje zapisu delivered
        return upsert_order(oid, {
            "status": "delivered",
            "commitment_level": "planned",  # reset, kurier wolny
            "delivered_at": payload.get("timestamp", now_iso()),
            "final_location": payload.get("final_location"),
            "delivery_address": deliv_addr,
            "delivery_coords": deliv_coords,
        }, event="COURIER_DELIVERED")

    if etype == "ORDER_RETURNED_TO_POOL":
        return upsert_order(oid, {
            "status": "returned_to_pool",
            "commitment_level": "planned",
            "courier_id": None,
            "return_reason": payload.get("reason"),
        }, event="ORDER_RETURNED_TO_POOL")

    if etype == "COURIER_REJECTED_PROPOSAL":
        # Wraca do planned, bez kuriera
        return upsert_order(oid, {
            "status": "planned",
            "commitment_level": "planned",
            "courier_id": None,
            "last_rejected_by": event.get("courier_id"),
            "rejection_reason": payload.get("reason"),
        }, event="COURIER_REJECTED_PROPOSAL")

    # Pozostale eventy nie zmieniaja stanu zlecen
    return None


def touch_check_cursor(order_id: str) -> bool:
    """Cicha aktualizacja cursora round-robin dla round-robin watchera.
    Ustawia assigned_check_ts=now_iso dla ordera. Nie loguje historii.
    Uzywane przez panel_watcher picked_up reconcile do rotacji candidate'ow.
    Zwraca True jesli order istnial, False inaczej."""
    with _locked_write():
        state = _read_state()
        if order_id not in state:
            return False
        state[order_id]["assigned_check_ts"] = now_iso()
        _atomic_write(Path(_state_path()), state)
        return True


def delete_order(order_id: str) -> bool:
    """Fizyczne usuniecie (tylko do testow lub purge)."""
    with _locked_write() as path:
        state = _read_state()
        if order_id in state:
            del state[order_id]
            _atomic_write(path, state)
            _log.info(f"delete {order_id}")
            return True
        return False


def compute_oldest_picked_up_age_min(bag, now_utc):
    """Wiek (minuty) najstarszego ordera w statusie 'picked_up' w bagu kuriera.

    Implementacja D4 V3.1: SLA kuriera liczy sie od picked_up_at (nie od assigned_at).
    Ordery w statusie 'assigned' nie karcony time_penalty w scoringu - kurier ich
    jeszcze nie ma fizycznie, restauracja jeszcze prepuje.

    Parsowanie timestampow: akceptowane formaty:
      1. datetime z tzinfo
      2. ISO string "YYYY-MM-DDTHH:MM:SS+HH:MM" lub z "Z"
      3. naive Warsaw "YYYY-MM-DD HH:MM:SS" (format panelu gastro.nadajesz.pl)

    Args:
        bag: lista dict orderow (np. z get_by_courier). Kazdy order ma min. "status".
             Dla statusu "picked_up" wymagany jest "picked_up_at".
        now_utc: datetime z tzinfo UTC. Caller MUSI podac - zero ukrytych defaults
                 dla deterministycznosci (replay historical data, A/B testy).

    Returns:
        float minut lub None gdy bag nie ma zadnego ordera w statusie "picked_up"
        z poprawnym picked_up_at timestampem.

    Raises:
        ValueError: gdy now_utc jest naive (bez tzinfo).

    Example:
        >>> from datetime import datetime, timezone, timedelta
        >>> now = datetime(2026, 4, 12, 12, 0, 0, tzinfo=timezone.utc)
        >>> bag = [
        ...     {"status": "picked_up", "picked_up_at": "2026-04-12T11:45:00+00:00"},
        ...     {"status": "assigned"},
        ... ]
        >>> compute_oldest_picked_up_age_min(bag, now)
        15.0
    """
    if now_utc is None:
        raise ValueError("now_utc required - caller must pass explicit timestamp")
    if now_utc.tzinfo is None:
        raise ValueError("now_utc must be timezone-aware (got naive datetime)")

    if not bag:
        return None

    now_utc_norm = now_utc.astimezone(timezone.utc)
    oldest_age_min = None

    for order in bag:
        if not isinstance(order, dict):
            continue
        if order.get("status") != "picked_up":
            continue
        picked_ts = order.get("picked_up_at")
        if not picked_ts:
            continue

        picked_dt = _parse_picked_up_at(picked_ts)
        if picked_dt is None:
            continue

        age_min = (now_utc_norm - picked_dt).total_seconds() / 60.0
        if oldest_age_min is None or age_min > oldest_age_min:
            oldest_age_min = age_min

    return oldest_age_min


def _parse_picked_up_at(value):
    """Wrapper na common.parse_panel_timestamp dla kompatybilnosci wewnetrznej."""
    from dispatch_v2.common import parse_panel_timestamp
    return parse_panel_timestamp(value)


def stats() -> dict:
    """Statystyki state machine."""
    state = _read_state()
    by_status = {}
    by_courier = {}
    for o in state.values():
        s = o.get("status", "unknown")
        by_status[s] = by_status.get(s, 0) + 1
        c = o.get("courier_id")
        if c and s in ("assigned", "picked_up"):
            by_courier[c] = by_courier.get(c, 0) + 1
    return {
        "total": len(state),
        "by_status": by_status,
        "active_per_courier": by_courier,
    }
