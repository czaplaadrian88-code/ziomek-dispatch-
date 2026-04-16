"""Courier resolver - fleet snapshot z GPS + fallback last-click.

Priorytet zrodel pozycji kuriera (V3.1 P0.3):
1. Traccar GPS (swieze < 5 min)
2. Aktywny bag (picked_up > assigned, najnowszy timestamp)
3. Last delivered (TYLKO gdy bag pusty)
4. None = skip w dispatchu

Pure dataclass-based, lazy-load GPS aby nie blokowac dispatchu gdy Traccar offline.
"""
import json
import os
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from dispatch_v2.common import setup_logger, now_iso, parse_panel_timestamp, DT_MIN_UTC, RYNEK_KOSCUSZKI
from dispatch_v2 import state_machine

_log = setup_logger("courier_resolver", "/root/.openclaw/workspace/scripts/logs/courier_resolver.log")

KURIER_PINY_PATH = "/root/.openclaw/workspace/dispatch_state/kurier_piny.json"
COURIER_NAMES_PATH = "/root/.openclaw/workspace/dispatch_state/courier_names.json"
KURIER_IDS_PATH = "/root/.openclaw/workspace/dispatch_state/kurier_ids.json"
GPS_POSITIONS_PATH = "/root/.openclaw/workspace/dispatch_state/gps_positions.json"
GPS_POSITIONS_PWA_PATH = "/root/.openclaw/workspace/dispatch_state/gps_positions_pwa.json"
GPS_FRESHNESS_MIN = 5  # GPS nowszy niz 5 min = aktualny

# Synthetic pos dla kuriera bez GPS i bez historii zleceń (no_gps fallback).
# Nie wykluczamy go z dispatchu; dispatch_pipeline normalizuje km_to_pickup
# do średniej floty, a travel_min do max(prep, 15 min).
BIALYSTOK_CENTER = (53.1325, 23.1688)

# Pre-shift: kurier którego zmiana zaczyna się w ciągu N min może już dostać
# propozycję — czas deklarowany uwzględnia jego shift_start.
PRE_SHIFT_WINDOW_MIN = 50

# Priorytet źródeł pozycji — niższe = lepsze. Używane do dedupliacji
# kurierów występujących pod kilkoma courier_id (legacy + panel).
POS_SOURCE_PRIORITY = {
    "gps": 0,
    "last_picked_up_delivery": 1,
    "last_assigned_pickup": 2,
    "last_delivered": 3,
    "last_picked_up_recent": 3,  # tier z last_delivered (świeże <30 min)
    "no_gps": 4,
    "pre_shift": 5,
    "none": 6,
    None: 6,
}
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
    shift_start_min: Optional[float] = None          # minuty od now do startu zmiany (pre_shift)
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
    """kurier_piny.json = {PIN_4digit: name} (legacy, ID space różny od courier_id).

    UWAGA: keys to PIN-y, nie courier_id. Większość `piny.get(courier_id)`
    zwraca None. Zachowane jako fallback dla backwards compat.
    """
    try:
        with open(KURIER_PINY_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        _log.warning(f"_load_kurier_piny fail: {e}")
        return {}


def _load_courier_names() -> Dict:
    """courier_names.json = {courier_id_str: name} (P0.5b F1.1 fix).

    Zbudowany z odwrócenia kurier_ids.json. Primary source dla name lookup
    w build_fleet_snapshot. Bez tego Telegram propozycje pokazują raw K<id>
    zamiast imienia.
    """
    try:
        with open(COURIER_NAMES_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        return {}
    except Exception as e:
        _log.warning(f"_load_courier_names fail: {e}")
        return {}


def _load_gps_positions() -> Dict:
    """Merge GPS positions — PWA primary, legacy Traccar fallback (F1.5).

    Returns: {courier_id_str: {lat, lon, accuracy, timestamp, source, name?}}

    Źródła:
    - gps_positions_pwa.json: {courier_id: {...}} — PWA server (F1.5, fresh)
    - gps_positions.json: {name: {...}} — legacy Traccar (imiona jako key)

    Merge strategy:
    1. Load PWA — klucze już są courier_id (direct)
    2. Load legacy — mapuj name → courier_id via kurier_ids.json
    3. PWA wygrywa przy konflikcie (newer data, clean format)
    """
    merged: Dict = {}

    # 1. PWA primary (courier_id keys)
    try:
        with open(GPS_POSITIONS_PWA_PATH) as f:
            pwa = json.load(f)
        for cid, rec in pwa.items():
            merged[str(cid)] = rec
    except FileNotFoundError:
        pass
    except Exception as e:
        _log.warning(f"_load_gps_positions PWA fail: {e}")

    # 2. Legacy fallback (name keys → courier_id via kurier_ids)
    try:
        with open(KURIER_IDS_PATH) as f:
            name_to_id = json.load(f)
    except Exception:
        name_to_id = {}

    try:
        with open(GPS_POSITIONS_PATH) as f:
            legacy = json.load(f)
        for name, rec in legacy.items():
            cid = name_to_id.get(name)
            if cid is None:
                continue
            cid_str = str(cid)
            if cid_str in merged:
                continue  # PWA primary wins
            merged[cid_str] = rec
    except FileNotFoundError:
        pass
    except Exception as e:
        _log.warning(f"_load_gps_positions legacy fail: {e}")

    return merged


def _latest_order_by_event(orders: List[Dict], event_field: str) -> Optional[Dict]:
    """Zwraca order z najpozniejszym event_field (delivered_at/picked_up_at/assigned_at)."""
    filtered = [o for o in orders if o.get(event_field)]
    if not filtered:
        return None
    return max(filtered, key=lambda o: o.get(event_field, ""))


def _bag_sort_key(o: dict) -> tuple:
    """Klucz sortowania orderow w aktywnym bagu: picked_up > assigned, nowszy > starszy.

    Zwraca tuple (status_priority, parsed_datetime) dla stabilnego sortowania.
    Module-level: alokacja raz, wolany N razy bez GC pressure.
    """
    is_picked = 1 if o.get("status") == "picked_up" else 0
    ts_raw = o.get("picked_up_at") if is_picked else o.get("assigned_at")
    ts_dt = parse_panel_timestamp(ts_raw) or DT_MIN_UTC
    return (is_picked, ts_dt)


def build_fleet_snapshot(
    include_koordynator: bool = False,
) -> Dict[str, CourierState]:
    """Buduje snapshot wszystkich kurierow z ich aktualna pozycja i bagiem.

    Returns:
        dict courier_id -> CourierState
    """
    state = state_machine.get_all()
    piny = _load_kurier_piny()
    names = _load_courier_names()
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

    # Dla kazdego kuriera w names/pinach LUB majacego ordery
    all_kids = set(per_courier.keys()) | set(names.keys()) | set(str(k) for k in piny.keys())

    for kid in all_kids:
        orders = per_courier.get(kid, [])
        active_bag = [o for o in orders if o.get("status") in ("assigned", "picked_up")]

        cs = CourierState(courier_id=kid)
        cs.bag = active_bag
        # Name lookup: courier_names.json (primary, correct ID space) → kurier_piny (legacy fallback)
        name = names.get(kid)
        if name is None and kid.isdigit():
            name = names.get(str(int(kid)))  # normalize leading zeros etc.
        if name is None:
            pin_name = piny.get(kid)
            if pin_name is None and kid.isdigit():
                pin_name = piny.get(int(kid))
            if isinstance(pin_name, str):
                name = pin_name
        if isinstance(name, str):
            cs.name = name

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

        # 2. AKTYWNY BAG priorytet (picked_up > assigned, najnowszy wygrywa)
        #    picked_up -> delivery_coords (kurier wiezie do klienta)
        #    assigned -> pickup_coords (kurier jedzie odebrac)
        #    Iteracja malejaco: jesli najnowszy broken -> probuj kolejny
        active_bag_orders = [o for o in orders if o.get("status") in ("picked_up", "assigned")]

        # 2b. POST_WAVE: brak GPS + wszystkie ordery picked_up (zero assigned)
        #     Kurier jest w trakcie dostawy fali — zaraz wraca do centrum.
        #     Pozycja referencyjna = Rynek Kościuszki (centrum Białystoku).
        if active_bag_orders and all(
            o.get("status") == "picked_up" for o in active_bag_orders
        ):
            cs.pos = RYNEK_KOSCUSZKI
            cs.pos_source = "post_wave"
            fleet[kid] = cs
            continue
        if active_bag_orders:
            sorted_bag = sorted(active_bag_orders, key=_bag_sort_key, reverse=True)
            resolved = False
            for order in sorted_bag:
                if order.get("status") == "picked_up":
                    if order.get("delivery_coords"):
                        cs.pos = tuple(order["delivery_coords"])
                        cs.pos_source = "last_picked_up_delivery"
                        resolved = True
                        break
                    _log.warning(
                        f"courier {kid} picked_up order {order.get('order_id')} "
                        f"bez delivery_coords - data quality alert (P0.4)"
                    )
                else:  # assigned
                    if order.get("pickup_coords"):
                        cs.pos = tuple(order["pickup_coords"])
                        cs.pos_source = "last_assigned_pickup"
                        resolved = True
                        break
                    _log.warning(
                        f"courier {kid} assigned order {order.get('order_id')} "
                        f"bez pickup_coords - data quality alert (P0.4)"
                    )
            if resolved:
                fleet[kid] = cs
                continue

        # 3. Recent activity (delivered_at lub picked_up_at < 30 min temu).
        #    Wymagamy ŚWIEŻEGO eventu — stary delivered (np. sprzed 6 dni jak
        #    Bartek bez aktualnego GPS) NIE jest dobrym estymatem pozycji.
        RECENT_MAX_MIN = 30
        best_age = float("inf")
        best_pos = None
        best_source = None
        for o in orders:
            delivery_c = o.get("delivery_coords")
            if not delivery_c:
                continue
            for ts_key, source_label in [
                ("delivered_at", "last_delivered"),
                ("picked_up_at", "last_picked_up_recent"),
            ]:
                ts_str = o.get(ts_key)
                if not ts_str:
                    continue
                try:
                    ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                except Exception:
                    continue
                if ts.tzinfo is None:
                    ts = ts.replace(tzinfo=timezone.utc)
                age = (now_utc - ts).total_seconds() / 60.0
                if age < 0 or age >= RECENT_MAX_MIN:
                    continue
                if age < best_age:
                    best_age = age
                    best_pos = tuple(delivery_c)
                    best_source = source_label

        if best_pos is not None:
            cs.pos = best_pos
            cs.pos_source = best_source
            cs.pos_age_min = best_age
            fleet[kid] = cs
            continue

        # 4. no_gps fallback: kurier wolny, brak GPS i brak historii.
        #    Dajemy syntetyczną pozycję = centrum miasta. Dispatch_pipeline
        #    nadpisze km_to_pickup średnią floty i travel_min = max(prep, 15).
        cs.pos = BIALYSTOK_CENTER
        cs.pos_source = "no_gps"
        fleet[kid] = cs

    # Dedup: gdy 2+ courier_id mają to samo imię (np. legacy 4657 + panel 123
    # dla "Bartek O."), zostaje wpis z lepszym pos_source.
    by_name: Dict[str, str] = {}
    for kid in list(fleet.keys()):
        cs = fleet[kid]
        if not cs.name:
            continue
        existing_kid = by_name.get(cs.name)
        if existing_kid is None:
            by_name[cs.name] = kid
            continue
        existing = fleet[existing_kid]
        cur_p = POS_SOURCE_PRIORITY.get(cs.pos_source, 99)
        ex_p = POS_SOURCE_PRIORITY.get(existing.pos_source, 99)
        if cur_p < ex_p:
            del fleet[existing_kid]
            by_name[cs.name] = kid
        else:
            del fleet[kid]

    return fleet


def _mins_to_shift_start(entry: Optional[dict]) -> Optional[float]:
    """Z entry grafiku → minuty od now (Warsaw) do startu zmiany.
    Dodatnie = jeszcze nie zaczął, ujemne = już po. None = brak danych."""
    start_str = (entry or {}).get("start")
    if not start_str or ":" not in start_str:
        return None
    try:
        from zoneinfo import ZoneInfo
        WAW = ZoneInfo("Europe/Warsaw")
        now_w = datetime.now(WAW)
        h, m = start_str.split(":")
        start_dt = now_w.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
        return (start_dt - now_w).total_seconds() / 60.0
    except Exception:
        return None


def _shift_end_dt(entry: Optional[dict]) -> Optional[datetime]:
    """Z entry grafiku → datetime końca zmiany (Warsaw aware)."""
    end_str = (entry or {}).get("end")
    if not end_str or ":" not in end_str:
        return None
    try:
        from zoneinfo import ZoneInfo
        WAW = ZoneInfo("Europe/Warsaw")
        now_w = datetime.now(WAW)
        if end_str == "24:00":
            base = now_w.replace(hour=0, minute=0, second=0, microsecond=0)
            return base + timedelta(days=1)
        h, m = end_str.split(":")
        end_dt = now_w.replace(hour=int(h), minute=int(m), second=0, microsecond=0)
        # Jeśli zmiana skończyła się "wczoraj" (np. now=01:00, end=23:00), nadal
        # interpretujemy jako today (przeszłość — feasibility wykluczy)
        return end_dt
    except Exception:
        return None


def dispatchable_fleet(fleet: Optional[Dict[str, CourierState]] = None) -> List[CourierState]:
    """Zwraca tylko kurierow ktorych mozna scorowac (maja pozycje i sa na zmianie
    LUB zaczynają zmianę w ciągu PRE_SHIFT_WINDOW_MIN minut)."""
    import sys as _sys
    _sys.path.insert(0, "/root/.openclaw/workspace/scripts")
    try:
        from schedule_utils import load_schedule, is_on_shift, match_courier
        schedule = load_schedule()
    except Exception as _e:
        _log.warning(f"schedule load failed: {_e} — skip filtrowania")
        schedule = {}
        match_courier = None
        is_on_shift = None
    try:
        from dispatch_v2 import manual_overrides
        excluded = set(manual_overrides.get_excluded())
    except Exception as _e:
        _log.warning(f"manual_overrides load failed: {_e}")
        excluded = set()
    if fleet is None:
        fleet = build_fleet_snapshot()
    result = []
    for cs in fleet.values():
        if cs.pos is None:
            continue
        if cs.name and cs.name in excluded:
            _log.debug(f"skip {cs.name} ({cs.courier_id}): manual override")
            continue
        if schedule and cs.name:
            full_name = match_courier(cs.name, schedule)
            if full_name is None:
                _log.debug(f"skip {cs.name} ({cs.courier_id}): brak w grafiku")
                continue
            entry = schedule.get(full_name)
            if entry is None:
                _log.debug(f"skip {cs.name} ({cs.courier_id}): nie pracuje dziś")
                continue
            on_shift, reason = is_on_shift(cs.name, schedule)
            # Set shift_end z grafiku (potrzebne do feasibility shift_end check)
            cs.shift_end = _shift_end_dt(entry)
            if not on_shift:
                # Pre-shift window: kurier zaczyna w ciągu PRE_SHIFT_WINDOW_MIN
                # → dopuszczamy z synthetic pos i znacznikiem pre_shift.
                mins = _mins_to_shift_start(entry)
                if mins is not None and 0 < mins <= PRE_SHIFT_WINDOW_MIN:
                    cs.pos_source = "pre_shift"
                    cs.pos = BIALYSTOK_CENTER
                    cs.shift_start_min = mins
                    _log.debug(f"pre_shift {cs.name} ({cs.courier_id}): za {mins:.0f} min")
                else:
                    _log.debug(f"skip {cs.name} ({cs.courier_id}): {reason}")
                    continue
        result.append(cs)
    return result
