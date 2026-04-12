"""Courier resolver - fleet snapshot z GPS + fallback last-click.

Priorytet zrodel pozycji kuriera:
1. Traccar GPS (swieze < 5 min)
2. Ostatnia aktywnosc w state (delivered/picked_up/assigned w tej kolejnosci)
3. Domyslny pin z kurier_piny.json
4. None = skip w dispatchu

Pure dataclass-based, lazy-load GPS aby nie blokowac dispatchu gdy Traccar offline.
"""
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dispatch_v2.common import setup_logger, now_iso
from dispatch_v2 import state_machine

_log = setup_logger("courier_resolver", "/root/.openclaw/workspace/scripts/logs/courier_resolver.log")

KURIER_PINY_PATH = "/root/.openclaw/workspace/dispatch_state/kurier_piny.json"
GPS_POSITIONS_PATH = "/root/.openclaw/workspace/dispatch_state/gps_positions.json"
GPS_FRESHNESS_MIN = 5  # GPS nowszy niz 5 min = aktualny
TRACCAR_URL = os.environ.get("TRACCAR_URL", "http://localhost:8082")
TRACCAR_USER = os.environ.get("TRACCAR_USER", "")
TRACCAR_PASS = os.environ.get("TRACCAR_PASS", "")


@dataclass
class CourierState:
    courier_id: str
    pos: Optional[Tuple[float, float]] = None       # aktualna lokalizacja (lat, lon)
    pos_source: str = "none"                         # gps | last_delivered | last_picked_up | last_assigned | pin | none
    pos_age_min: Optional[float] = None              # sekund/60 od pomiaru
    bag: List[Dict] = field(default_factory=list)    # ordery w bagu (jako dict z state)
    shift_end: Optional[datetime] = None             # koniec zmiany (None = nieznane)
    name: Optional[str] = None                       # czytelna nazwa z kurier_piny

    def to_dict(self):
        return {
            "courier_id": self.courier_id,
            "pos": list(self.pos) if self.pos else None,
            "pos_source": self.pos_source,
            "pos_age_min": round(self.pos_age_min, 1) if self.pos_age_min is not None else None,
            "bag_size": len(self.bag),
            "bag_oids": [o.get("order_id") or o.get("id") for o in self.bag],
            "name": self.name,
        }


def _load_kurier_piny() -> Dict:
    try:
        with open(KURIER_PINY_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        _log.warning(f"_load_kurier_piny fail: {e}")
        return {}


def _load_gps_positions() -> Dict:
    """gps_positions.json - cache z Traccar watchera.
    
    UWAGA: klucze to IMIONA (np. "Bartek O."), nie courier_id.
    Na dzisiaj (11.04) ignorujemy (nie ma tabeli lookup imie->id),
    bo dzisiejsze dane i tak maja >3h latency. Fallback last-click dziala bez GPS.
    TODO: tabela lookup imie->id albo migracja Traccar watchera na courier_id.
    """
    # Na dzis zwracamy pusty dict - gps fallback nieuzywany
    return {}


def _latest_order_by_event(orders: List[Dict], event_field: str) -> Optional[Dict]:
    """Zwraca order z najpozniejszym event_field (delivered_at/picked_up_at/assigned_at)."""
    filtered = [o for o in orders if o.get(event_field)]
    if not filtered:
        return None
    return max(filtered, key=lambda o: o.get(event_field, ""))


def build_fleet_snapshot(
    include_koordynator: bool = False,
) -> Dict[str, CourierState]:
    """Buduje snapshot wszystkich kurierow z ich aktualna pozycja i bagiem.

    Returns:
        dict courier_id -> CourierState
    """
    state = state_machine.get_all()
    piny = _load_kurier_piny()
    gps = _load_gps_positions()
    now_utc = datetime.now(timezone.utc)

    # Grupuj ordery per kurier
    per_courier: Dict[str, List[Dict]] = {}
    for oid, o in state.items():
        kid = o.get("courier_id")
        if not kid:
            continue
        if str(kid) == "26" and not include_koordynator:
            continue
        o = dict(o, order_id=oid)
        per_courier.setdefault(str(kid), []).append(o)

    fleet: Dict[str, CourierState] = {}

    # Dla kazdego kuriera w pinach LUB majacego ordery
    all_kids = set(per_courier.keys()) | set(str(k) for k in piny.keys())

    for kid in all_kids:
        orders = per_courier.get(kid, [])
        active_bag = [o for o in orders if o.get("status") in ("assigned", "picked_up")]

        cs = CourierState(courier_id=kid)
        cs.bag = active_bag
        # kurier_piny.json ma format {id_str: "Nazwa"} - tylko mapa nazw
        pin_name = piny.get(kid)
        if pin_name is None and kid.isdigit():
            pin_name = piny.get(int(kid))
        if isinstance(pin_name, str):
            cs.name = pin_name

        # 1. GPS fresh
        gps_entry = gps.get(kid)
        if gps_entry:
            gps_ts = gps_entry.get("timestamp")
            try:
                gps_dt = datetime.fromisoformat(gps_ts.replace("Z", "+00:00")) if gps_ts else None
            except Exception:
                gps_dt = None
            if gps_dt:
                age_min = (now_utc - gps_dt).total_seconds() / 60.0
                if age_min < GPS_FRESHNESS_MIN:
                    cs.pos = (float(gps_entry["lat"]), float(gps_entry["lon"]))
                    cs.pos_source = "gps"
                    cs.pos_age_min = age_min
                    fleet[kid] = cs
                    continue

        # 2. Last delivered (najswiezszy)
        last_del = _latest_order_by_event(orders, "delivered_at")
        if last_del and last_del.get("delivery_coords"):
            cs.pos = tuple(last_del["delivery_coords"])
            cs.pos_source = "last_delivered"
            fleet[kid] = cs
            continue

        # 3. Last picked_up (kurier w drodze do klienta - uzyj delivery jako target)
        last_pu = _latest_order_by_event(orders, "picked_up_at")
        if last_pu and last_pu.get("delivery_coords"):
            cs.pos = tuple(last_pu["delivery_coords"])
            cs.pos_source = "last_picked_up_delivery"
            fleet[kid] = cs
            continue
        if last_pu and last_pu.get("pickup_coords"):
            cs.pos = tuple(last_pu["pickup_coords"])
            cs.pos_source = "last_picked_up_pickup"
            fleet[kid] = cs
            continue

        # 4. Last assigned (kurier jedzie do restauracji)
        last_as = _latest_order_by_event(orders, "assigned_at")
        if last_as and last_as.get("pickup_coords"):
            cs.pos = tuple(last_as["pickup_coords"])
            cs.pos_source = "last_assigned_pickup"
            fleet[kid] = cs
            continue

        # 5. None - skip w dispatchu (brak danych, nie mozemy scorowac)
        cs.pos_source = "none"
        fleet[kid] = cs

    return fleet


def dispatchable_fleet(fleet: Optional[Dict[str, CourierState]] = None) -> List[CourierState]:
    """Zwraca tylko kurierow ktorych mozna scorowac (maja pozycje)."""
    if fleet is None:
        fleet = build_fleet_snapshot()
    return [cs for cs in fleet.values() if cs.pos is not None]
