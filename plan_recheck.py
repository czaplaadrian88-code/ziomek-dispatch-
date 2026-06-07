"""plan_recheck — V3.19c sub C periodic consistency checker.

Standalone script. Reads courier_plans.json + orders_state.json. For each
non-invalidated plan, verifies invariants:
  1. Every stop.order_id exists in orders_state.
  2. Status of each order is 'assigned' or 'picked_up' (not delivered/
     cancelled/returned).
  3. Plan age (now - last_modified_at) under threshold.

Rozbieżności → structured log to plan_recheck_log.jsonl. Auto-invalidate
(AUTO_INVALIDATE_STALE=True env) gdy znaleziony delivered/cancelled order
w plan.

NIE re-optymalizuje TSP (deferred V3.19d — wymaga read integration).
NIE modyfikuje scoring path — read-only + optional invalidate.

Invocation: python3 -m dispatch_v2.plan_recheck (stdlib only, no deps).
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from dispatch_v2 import plan_manager

_log = logging.getLogger("plan_recheck")
if not _log.handlers:
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    _log.addHandler(handler)
    _log.setLevel(logging.INFO)

RECHECK_LOG_PATH = Path(
    "/root/.openclaw/workspace/dispatch_state/plan_recheck_log.jsonl"
)
ORDERS_STATE_PATH = Path(
    "/root/.openclaw/workspace/dispatch_state/orders_state.json"
)
GPS_PWA_PATH = Path(
    "/root/.openclaw/workspace/dispatch_state/gps_positions_pwa.json"
)

AUTO_INVALIDATE_STALE = os.environ.get("AUTO_INVALIDATE_STALE", "0") == "1"

# V3.19c sub D: GPS drift check.
# True → gdy kurier GPS > GPS_DRIFT_THRESHOLD_M od plan.start_pos i flag
# ENABLE_GPS_DRIFT_INVALIDATION → plan_manager.mark_stale(cid, "GPS_DRIFT").
# Default OFF — shadow observation tylko.
ENABLE_GPS_DRIFT_INVALIDATION = os.environ.get(
    "ENABLE_GPS_DRIFT_INVALIDATION", "0"
) == "1"
GPS_DRIFT_THRESHOLD_M = int(os.environ.get("GPS_DRIFT_THRESHOLD_M", "500"))
GPS_DRIFT_FRESHNESS_MIN = int(os.environ.get("GPS_DRIFT_FRESHNESS_MIN", "5"))

MAX_PLAN_AGE_MIN = int(os.environ.get("MAX_PLAN_AGE_MIN", "120"))

# KROK 2 (źródłowy fix bugu "apka pokazuje czas restauracji zamiast ustalonego"):
# dla każdego żywego pickupu w aktywnym planie, jeśli order ma ustalony
# czas_kuriera_warsaw (obietnica po odpowiedzi do restauracji) a predicted_at
# pickupu jest WCZEŚNIEJSZY (plan policzony zanim czas wpłynął) → podnieś plan do
# obietnicy i przesuń kolejne stopy. Monotoniczne, idempotentne. Default ON.
ENABLE_PICKUP_REFLOOR = os.environ.get("ENABLE_PICKUP_REFLOOR", "1") == "1"

# 2026-06-01 (apka pokazuje fallback_nn zamiast trasy Ziomka):
# gdy kurier MA realny worek (≥1 zlecenie assigned/picked_up w orders_state) ale
# NIE ma aktywnego planu w courier_plans.json (np. PANEL_OVERRIDE — koordynator
# przypisał innego kuriera niż Ziomek proponował, więc panel_watcher nie zapisał
# planu) → apka liczy własne geo-NN (fallback_nn). Ten pass gap-fill uruchamia
# realny planner Ziomka (route_simulator_v2) na FAKTYCZNYM worku kuriera i zapisuje
# plan, dzięki czemu apka pokazuje route_source=ziomek_plan z tą samą kolejnością
# i czasami. Tylko gap-fill (brak aktywnego planu) — istniejących planów NIE rusza,
# więc po zapisie kolejny tick pomija kuriera (zero churn). NIE dotyka Telegrama
# (zapis tylko do courier_plans.json czytanego przez apkę). Default ON.
ENABLE_PLAN_FOR_ACTUAL_BAG = os.environ.get(
    "ENABLE_PLAN_FOR_ACTUAL_BAG", "1"
) == "1"
# Powyżej tylu zleceń w worku → skip (za dużo wywołań OSRM × sweep designacji w
# oknie oneshot 120s); apka degraduje do fallback_nn jak dotychczas.
PLAN_FOR_ACTUAL_BAG_MAX = int(os.environ.get("PLAN_FOR_ACTUAL_BAG_MAX", "5"))
# Regeneracja planu BLISKO odbioru. Plan workowy generowany ~2h przed odbiorem i
# zamrażany (zero churn) front-loaduje odbiory: cel świeżości (R6 soft deadline)
# liczony względem „teraz" 2h wcześniej jest za luźny, by gryźć. Gdy najwcześniejszy
# nieodebrany odbiór wchodzi w to okno → odśwież plan, by cel liczył się względem
# czasu bliskiego wykonania (kurier dostaje trasę przeplataną, nie front-load).
# Diagnoza 2026-06-05 (replay: 84→12 naruszeń R6 na dzisiejszych workach).
ENABLE_PLAN_REGEN_NEAR_PICKUP = os.environ.get(
    "ENABLE_PLAN_REGEN_NEAR_PICKUP", "1"
) == "1"
PLAN_REGEN_NEAR_PICKUP_WINDOW_MIN = float(
    os.environ.get("PLAN_REGEN_NEAR_PICKUP_WINDOW_MIN", "45")
)
ACTIVE_STATUSES = frozenset({"assigned", "picked_up"})

TERMINAL_STATUSES = frozenset({"delivered", "cancelled", "returned_to_pool"})


def _haversine_m(p1: tuple, p2: tuple) -> float:
    """Distance in meters between 2 (lat, lng) pairs.

    Fail-loud guards (Lekcja #81 cross-codebase fail-loud sentinel):
    None / (0,0) → ValueError zamiast silent ~6285km drift fałszywy invalidate.
    """
    import math
    if p1 is None or p2 is None:
        raise ValueError(f"_haversine_m: None coords (p1={p1!r}, p2={p2!r})")
    if tuple(p1) == (0.0, 0.0) or tuple(p2) == (0.0, 0.0):
        raise ValueError(f"_haversine_m: sentinel (0,0) (p1={p1!r}, p2={p2!r})")
    lat1, lng1 = p1
    lat2, lng2 = p2
    R = 6371008.8
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    dphi = math.radians(lat2 - lat1)
    dlmb = math.radians(lng2 - lng1)
    a = math.sin(dphi / 2) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(dlmb / 2) ** 2
    return 2 * R * math.asin(math.sqrt(a))


def _load_gps_positions() -> Dict[str, Any]:
    if not GPS_PWA_PATH.exists():
        return {}
    try:
        with open(GPS_PWA_PATH) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception as e:
        _log.warning(f"gps_positions load fail: {e}")
        return {}


def _gps_drift_check(cid: str, plan: Dict[str, Any],
                     gps_positions: Dict[str, Any],
                     now: datetime) -> Optional[Dict[str, Any]]:
    """Return finding dict {drift_m, age_min, gps_pos, start_pos} if GPS fresh
    AND drift > threshold, else None.
    """
    gps = gps_positions.get(cid)
    if not gps:
        return None
    try:
        ts_str = gps.get("timestamp")
        if not ts_str:
            return None
        ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        age_min = (now - ts).total_seconds() / 60.0
    except Exception:
        return None
    if age_min < 0 or age_min > GPS_DRIFT_FRESHNESS_MIN:
        return None  # stale GPS not used for drift detection
    gps_lat = gps.get("lat")
    gps_lon = gps.get("lon")
    if gps_lat is None or gps_lon is None:
        return None
    sp = plan.get("start_pos") or {}
    sp_lat = sp.get("lat")
    sp_lng = sp.get("lng")
    if sp_lat is None or sp_lng is None:
        return None
    # Placeholder start_pos (0,0) — saved from V3.19b hook without coords
    if (sp_lat, sp_lng) == (0.0, 0.0):
        return None
    drift = _haversine_m((gps_lat, gps_lon), (sp_lat, sp_lng))
    if drift <= GPS_DRIFT_THRESHOLD_M:
        return None
    return {
        "drift_m": round(drift, 1),
        "gps_age_min": round(age_min, 1),
        "gps_pos": [gps_lat, gps_lon],
        "start_pos": [sp_lat, sp_lng],
    }


def _load_orders_state() -> Dict[str, Any]:
    if not ORDERS_STATE_PATH.exists():
        return {}
    try:
        with open(ORDERS_STATE_PATH) as fh:
            d = json.load(fh)
        return d if isinstance(d, dict) else {}
    except Exception as e:
        _log.warning(f"orders_state load fail: {e}")
        return {}


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _log_recheck_entry(entry: Dict[str, Any]) -> None:
    try:
        RECHECK_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(RECHECK_LOG_PATH, "a", encoding="utf-8") as fh:
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
    except Exception as e:
        _log.warning(f"recheck log write fail: {e}")


def _check_plan(cid: str, plan: Dict[str, Any],
                orders_state: Dict[str, Any],
                gps_positions: Dict[str, Any],
                now: datetime) -> Dict[str, Any]:
    """Return structured finding dict. issues list is empty when plan healthy."""
    issues: List[str] = []
    auto_invalidate_reason: Optional[str] = None

    stops = plan.get("stops") or []
    stop_oids = {str(s.get("order_id")) for s in stops}

    missing = []
    terminal = []
    for oid in stop_oids:
        rec = orders_state.get(oid)
        if not rec:
            missing.append(oid)
            continue
        st = rec.get("status")
        if st in TERMINAL_STATUSES:
            terminal.append((oid, st))

    if missing:
        issues.append(f"missing_in_orders_state:{','.join(missing)}")
    if terminal:
        issues.append(f"terminal_status:{','.join(f'{o}={s}' for o,s in terminal)}")
        auto_invalidate_reason = "ORDER_DELIVERED_ALL" if all(
            s == "delivered" for _, s in terminal
        ) else "ORDER_CANCELLED"

    # age check
    age_min = None
    try:
        lm = plan.get("last_modified_at")
        if lm:
            lm_dt = datetime.fromisoformat(lm.replace("Z", "+00:00"))
            if lm_dt.tzinfo is None:
                lm_dt = lm_dt.replace(tzinfo=timezone.utc)
            age_min = (now - lm_dt).total_seconds() / 60.0
            if age_min > MAX_PLAN_AGE_MIN:
                issues.append(f"stale_age:{age_min:.1f}min")
    except Exception:
        pass

    # V3.19c sub D: GPS drift check
    gps_drift = _gps_drift_check(cid, plan, gps_positions, now)
    if gps_drift:
        issues.append(f"gps_drift:{gps_drift['drift_m']}m")

    return {
        "ts": now.isoformat(),
        "cid": cid,
        "plan_version": plan.get("plan_version"),
        "age_min": round(age_min, 1) if age_min is not None else None,
        "stops_count": len(stops),
        "missing_orders": missing,
        "terminal_orders": [{"oid": o, "status": s} for o, s in terminal],
        "gps_drift": gps_drift,
        "issues": issues,
        "auto_invalidate_reason": auto_invalidate_reason,
    }


def _parse_dt(s: Optional[str]) -> Optional[datetime]:
    """ISO-8601 → aware UTC datetime. None gdy puste/nie-str/nie-parsuje.

    NIE używać dla naiwnych Warsaw timestampów (np. orders_state.picked_up_at
    "YYYY-MM-DD HH:MM:SS" bez offsetu — interpretacja jako UTC = błąd +2h).
    """
    if not s or not isinstance(s, str):
        return None
    try:
        v = s.strip()
        if v.endswith("Z"):
            v = v[:-1] + "+00:00"
        dt = datetime.fromisoformat(v)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt
    except ValueError:
        return None


def _coords_ok(c: Any) -> bool:
    return (isinstance(c, (list, tuple)) and len(c) == 2
            and c[0] is not None and c[1] is not None)


# --- Kotwica startu trasy: GPS-free (flota z założenia bez GPS) ---------------
# Fix GPS starszy niż próg traktujemy jak BRAK — nie kotwiczymy estymaty na
# pozycji sprzed godzin/dni. Trasę liczymy z tego, co Ziomek SAM zna: committed
# odbiorów + obserwowanych zdarzeń odbioru/doręczenia (czas + lokalizacja
# ostatniego przystanku). Tak liczy człowiek, gdy nikt nie ma GPS.
GPS_FRESH_MAX_MIN = float(os.environ.get("GPS_FRESH_MAX_MIN", "10"))
ENABLE_GPS_FREE_ANCHOR = os.environ.get("ENABLE_GPS_FREE_ANCHOR", "0") == "1"
_ANCHOR_EVENT_MAX_AGE_MIN = 360.0  # zdarzenia starsze niż 6h = inna zmiana


def _gps_age_min(gps: Dict[str, Any], now: datetime) -> Optional[float]:
    ts = _parse_dt((gps or {}).get("timestamp"))
    return None if ts is None else (now - ts).total_seconds() / 60.0


def _last_event_anchor(cid: str, orders_state: Dict[str, Any],
                       now: datetime) -> Optional[Tuple[Tuple[float, float], datetime]]:
    """Najświeższe realne zdarzenie kuriera → (pozycja, czas), bez GPS.

    Doręczenie (COURIER_DELIVERED) lub odbiór (COURIER_PICKED_UP) z bieżącej
    zmiany. Pozycja: coords dostawy/odbioru danego zlecenia (fallback na
    pickup_coords gdy delivery_coords brak). History `at` = ISO UTC (parsowalne),
    w przeciwieństwie do naiwnego picked_up_at. Zdarzenia >6h pomijamy.
    """
    best_at: Optional[datetime] = None
    best_pos: Optional[Tuple[float, float]] = None
    for rec in orders_state.values():
        if not isinstance(rec, dict) or str(rec.get("courier_id") or "") != cid:
            continue
        for h in rec.get("history", []) or []:
            ev = h.get("event")
            if ev not in ("COURIER_DELIVERED", "COURIER_PICKED_UP"):
                continue
            at = _parse_dt(h.get("at"))
            if at is None:
                continue
            if (now - at).total_seconds() / 60.0 > _ANCHOR_EVENT_MAX_AGE_MIN:
                continue
            loc = rec.get("delivery_coords") if ev == "COURIER_DELIVERED" else None
            if not _coords_ok(loc):
                loc = rec.get("pickup_coords")  # delivery niegeokodowane / odbiór
            if not _coords_ok(loc):
                continue
            if best_at is None or at > best_at:
                best_at, best_pos = at, (float(loc[0]), float(loc[1]))
    if best_at is None:
        return None
    return best_pos, best_at


def _earliest_committed_pickup_anchor(
        oids: List[str], orders_state: Dict[str, Any]
) -> Optional[Tuple[Tuple[float, float], datetime]]:
    """Brak zdarzeń (kurier jeszcze nic nie odebrał) → kotwica na NAJBLIŻSZYM
    committed odbiorze: pozycja = restauracja, czas = committed (twarda podłoga).
    """
    best: Optional[Tuple[Tuple[float, float], datetime]] = None
    for oid in oids:
        rec = orders_state.get(oid) or {}
        if rec.get("status") != "assigned":
            continue
        ck = _parse_dt(rec.get("czas_kuriera_warsaw"))
        pc = rec.get("pickup_coords")
        if ck is None or not _coords_ok(pc):
            continue
        if best is None or ck < best[1]:
            best = ((float(pc[0]), float(pc[1])), ck)
    return best


def _start_anchor(cid: str, oids: List[str], orders_state: Dict[str, Any],
                  gps_positions: Dict[str, Any], now: datetime
                  ) -> Optional[Tuple[Tuple[float, float], Optional[datetime], str]]:
    """(pos, earliest_departure, source) startu symulacji.

    GPS tylko gdy ŚWIEŻY (≤GPS_FRESH_MAX_MIN); inaczej kotwica zdarzeniowa
    (ostatni przystanek, start=teraz) lub — gdy nic nieodebrane — committed
    najbliższego odbioru (pozycja=restauracja, start=committed). None gdy nic
    policzalnego. Flaga OFF → wyłącznie GPS (zachowanie sprzed zmiany).
    """
    gps = gps_positions.get(cid) or {}
    glat, glon = gps.get("lat"), gps.get("lon")
    has_gps = glat is not None and glon is not None
    age = _gps_age_min(gps, now)
    gps_fresh = has_gps and age is not None and age <= GPS_FRESH_MAX_MIN

    if not ENABLE_GPS_FREE_ANCHOR:
        return ((float(glat), float(glon)), None, "gps_pwa") if has_gps else None
    if gps_fresh:
        return (float(glat), float(glon)), None, "gps_pwa"

    ev = _last_event_anchor(cid, orders_state, now)
    if ev is not None:
        return ev[0], None, "last_event"  # pozycja=ostatni przystanek, start=teraz
    cp = _earliest_committed_pickup_anchor(oids, orders_state)
    if cp is not None:
        return cp[0], cp[1], "committed_pickup"  # restauracja + committed jako floor
    # Ostatnia deska: stary GPS lepszy niż nic (np. wszystko assigned bez committed).
    if has_gps:
        return (float(glat), float(glon)), None, "gps_stale"
    return None


def _gen_one_bag_plan(cid: str, oids: List[str], orders_state: Dict[str, Any],
                      gps_positions: Dict[str, Any], now: datetime,
                      R: Any) -> bool:
    """Wygeneruj+zapisz plan Ziomka dla faktycznego worka kuriera.

    Zwraca True gdy zapisano, False gdy skip (worek za duży / brak GPS / brak
    coords / niekompletny plan). Wyjątki propagują do callera (per-courier guard).
    """
    if len(oids) > PLAN_FOR_ACTUAL_BAG_MAX:
        return False
    anchor = _start_anchor(cid, oids, orders_state, gps_positions, now)
    if anchor is None:
        return False  # ani (świeży) GPS, ani kotwica czasowa → nie ma od czego liczyć
    pos, anchor_departure, anchor_source = anchor

    sims: Dict[str, Any] = {}
    for oid in oids:
        rec = orders_state.get(oid) or {}
        dc = rec.get("delivery_coords")
        if not _coords_ok(dc):
            return False  # brak coords dostawy → fallback_nn (jak dotąd)
        status = rec.get("status")
        pc = rec.get("pickup_coords")
        if status != "picked_up" and not _coords_ok(pc):
            return False  # assigned bez coords odbioru → skip cały kurier
        pickup_coords = (float(pc[0]), float(pc[1])) if _coords_ok(pc) \
            else (float(dc[0]), float(dc[1]))  # picked_up: nieużywane (brak pickup-node)
        sims[oid] = R.OrderSim(
            order_id=oid,
            pickup_coords=pickup_coords,
            delivery_coords=(float(dc[0]), float(dc[1])),
            picked_up_at=None,  # naiwny Warsaw w orders_state → pomijamy; anchor=czas_kuriera
            status=status,
            pickup_ready_at=_parse_dt(rec.get("czas_kuriera_warsaw")),
        )

    # Sweep designacji new_order (route_simulator_v2 traktuje 1 order jako wstawiany)
    # → wybierz najlepszy plan deterministycznie (sla, dur, sequence).
    ordered = list(sims.keys())
    best = None
    for newoid in ordered:
        bag = [sims[o] for o in ordered if o != newoid]
        p = R.simulate_bag_route_v2(pos, bag, sims[newoid], now=now, sla_minutes=35,
                                    earliest_departure=anchor_departure)
        key = (p.sla_violations, round(p.total_duration_min, 3), tuple(p.sequence))
        if best is None or key < best[0]:
            best = (key, p)
    plan = best[1]

    # Stopy w REALNEJ kolejności czasowej (przeplot pickup/dropoff) — apka czyta
    # kolejność tablicy stops jako kolejność przejazdu (_plan_stop_sequence).
    events = []
    for oid in ordered:
        pu = plan.pickup_at.get(oid)
        if pu is not None:
            events.append((pu, "pickup", oid))
        dp = plan.predicted_delivered_at.get(oid)
        if dp is None:
            return False  # niekompletny plan — nie zapisujemy częściowego
        events.append((dp, "dropoff", oid))
    events.sort(key=lambda e: e[0])

    stops = []
    for t, kind, oid in events:
        rec = orders_state.get(oid) or {}
        coords = rec.get("pickup_coords") if kind == "pickup" else rec.get("delivery_coords")
        stops.append({
            "order_id": oid,
            "type": kind,
            "coords": {"lat": float(coords[0]), "lng": float(coords[1])},
            "scheduled_at": None,
            "predicted_at": t.isoformat(),
            "dwell_min": 1.0 if kind == "pickup" else 3.5,
            "status_at_plan_time": "picked_up" if rec.get("status") == "picked_up" else "assigned",
        })

    _gps = gps_positions.get(cid) or {}
    body = {
        "start_pos": {
            "lat": pos[0], "lng": pos[1],
            "source": anchor_source,
            "source_ts": _gps.get("timestamp") if anchor_source == "gps_pwa" else now.isoformat(),
        },
        "start_ts": now.isoformat(),
        "stops": stops,
        "optimization_method": "incremental",
    }
    plan_manager.save_plan(cid, body)
    _log.info(
        f"BAG_PLAN_GENERATED cid={cid} stops={len(stops)} seq={plan.sequence} "
        f"sla={plan.sla_violations} dur={plan.total_duration_min:.1f} anchor={anchor_source}"
    )
    return True


def _pickup_approaching(oids: List[str], orders_state: Dict[str, Any],
                        now: datetime) -> bool:
    """True gdy najwcześniejszy NIEODEBRANY odbiór w worku jest w oknie
    PLAN_REGEN_NEAR_PICKUP_WINDOW_MIN od teraz (lub już minął — spóźniony).

    Wtedy plan z pełnym pokryciem warto odświeżyć mimo zero-churn, by cel
    świeżości liczył się względem czasu bliskiego wykonania. Odbiory daleko w
    przyszłości (> okno) → False (zachowanie jak dotąd, brak churnu). Brak
    nieodebranych odbiorów (cały worek picked_up) → False (nic do odświeżenia
    pod kątem front-loadu odbiorów).
    """
    if not ENABLE_PLAN_REGEN_NEAR_PICKUP:
        return False
    soonest: Optional[datetime] = None
    for oid in oids:
        rec = orders_state.get(oid) or {}
        if rec.get("status") == "picked_up":
            continue
        ck = _parse_dt(rec.get("czas_kuriera_warsaw"))
        if ck is None:
            continue
        if soonest is None or ck < soonest:
            soonest = ck
    if soonest is None:
        return False
    delta_min = (soonest - now).total_seconds() / 60.0
    return delta_min <= PLAN_REGEN_NEAR_PICKUP_WINDOW_MIN


def _gap_fill_plans(orders_state: Dict[str, Any], plans: Dict[str, Any],
                    gps_positions: Dict[str, Any], now: datetime,
                    summary: Dict[str, Any]) -> None:
    """Dla kuriera z realnym workiem bez planu LUB z planem CZĘŚCIOWYM →
    wygeneruj plan Ziomka i zapisz, by apka pokazała ziomek_plan zamiast
    fallback_nn.

    Dwa przypadki regeneracji:
    1. brak aktywnego planu (PANEL_OVERRIDE — koordynator przypisał innego
       kuriera niż Ziomek proponował, więc panel_watcher nie zapisał planu);
    2. aktywny plan pokrywa tylko CZĘŚĆ realnego worka (część zapisana, potem
       doszło nowe zlecenie). courier_api/build_view renderuje ziomek_plan
       TYLKO przy pełnym pokryciu (worek ⊆ plan) — częściowy plan tam spada do
       fallback_nn. Regenerujemy, by ziomek_plan został autorytatywny.

    Plan z PEŁNYM pokryciem (worek ⊆ plan) NIE jest ruszany (zero churn —
    konwerguje: po regeneracji kolejny tick widzi pełne pokrycie i pomija).
    Worek > PLAN_FOR_ACTUAL_BAG_MAX → _gen_one_bag_plan bailuje przed OSRM,
    apka zostaje na spójnym fallbacku. Fail-soft per kurier. NIE dotyka
    Telegrama (zapis tylko do courier_plans.json czytanego przez apkę).
    """
    summary["bag_plans_generated"] = 0
    summary["bag_plans_skipped"] = 0
    summary["bag_plans_partial_regen"] = 0
    summary["bag_plans_near_pickup_regen"] = 0
    try:
        from dispatch_v2 import route_simulator_v2 as R
    except Exception as e:
        _log.warning(f"gap_fill import fail (skip pass): {e}")
        return

    bags: Dict[str, List[str]] = {}
    for oid, rec in orders_state.items():
        if not isinstance(rec, dict) or rec.get("status") not in ACTIVE_STATUSES:
            continue
        cid = str(rec.get("courier_id") or "")
        if not cid:
            continue
        bags.setdefault(cid, []).append(str(oid))

    for cid, oids in bags.items():
        existing = plans.get(cid)
        partial = False
        near_regen = False
        if existing is not None and existing.get("invalidated_at") is None \
                and existing.get("stops"):
            plan_ids = {str(s.get("order_id"))
                        for s in existing.get("stops", [])
                        if s.get("order_id") is not None}
            if set(oids) <= plan_ids:
                # Pełne pokrycie. Normalnie zero churn — ALE gdy odbiory się
                # zbliżają, odśwież plan, by cel świeżości (R6 soft deadline)
                # liczył się względem czasu bliskiego wykonania. Bez tego
                # zamrożony plan sprzed ~2h front-loaduje odbiory.
                if not _pickup_approaching(oids, orders_state, now):
                    continue  # odbiory daleko → nie nadpisuj (zero churn)
                near_regen = True
            else:
                partial = True  # plan częściowy → regeneruj na pełnym worku
        try:
            ok = _gen_one_bag_plan(cid, oids, orders_state, gps_positions, now, R)
        except Exception as e:
            summary["bag_plans_skipped"] += 1
            _log.warning(f"gap_fill cid={cid} fail: {type(e).__name__}: {e}")
            continue
        summary["bag_plans_generated" if ok else "bag_plans_skipped"] += 1
        if ok and partial:
            summary["bag_plans_partial_regen"] += 1
            _log.info(f"BAG_PLAN_PARTIAL_REGEN cid={cid} bag={len(oids)}")
        if ok and near_regen:
            summary["bag_plans_near_pickup_regen"] += 1
            _log.info(f"BAG_PLAN_NEAR_PICKUP_REGEN cid={cid} bag={len(oids)}")


def run_recheck() -> Dict[str, Any]:
    """Main entry point. Returns summary dict."""
    now = _now_utc()
    orders_state = _load_orders_state()
    plans = plan_manager.load_plans()

    summary = {
        "ts": now.isoformat(),
        "total_plans": 0,
        "active_plans": 0,
        "healthy": 0,
        "with_issues": 0,
        "auto_invalidated": 0,
    }

    gps_positions = _load_gps_positions()
    summary["gps_drift_detected"] = 0
    summary["gps_drift_invalidated"] = 0
    summary["pickup_refloored"] = 0

    findings: List[Dict[str, Any]] = []
    for cid, plan in plans.items():
        summary["total_plans"] += 1
        if plan.get("invalidated_at") is not None:
            continue
        summary["active_plans"] += 1
        # KROK 2: dosuń pickupy planu do ustalonego czas_kuriera (źródłowy fix).
        # refloor liczy deltę pod lockiem na świeżym pliku, więc przekazanie
        # nieaktualnego snapshotu planu jest bezpieczne (re-read wewnątrz).
        if ENABLE_PICKUP_REFLOOR:
            for s in plan.get("stops", []):
                if s.get("type") != "pickup":
                    continue
                oid = str(s.get("order_id"))
                order = orders_state.get(oid)
                kur = order.get("czas_kuriera_warsaw") if isinstance(order, dict) else None
                if not kur:
                    continue
                shifted_min = plan_manager.refloor_pickup(cid, oid, kur)
                if shifted_min > 0:
                    summary["pickup_refloored"] += 1
                    _log.info(
                        f"PICKUP_REFLOOR cid={cid} oid={oid} "
                        f"shift=+{shifted_min:.1f}min floor={kur}"
                    )
        finding = _check_plan(cid, plan, orders_state, gps_positions, now)
        if finding["issues"]:
            summary["with_issues"] += 1
            findings.append(finding)
            _log_recheck_entry(finding)
            if AUTO_INVALIDATE_STALE and finding.get("auto_invalidate_reason"):
                plan_manager.invalidate_plan(cid, finding["auto_invalidate_reason"])
                summary["auto_invalidated"] += 1
                _log.info(
                    f"AUTO_INVALIDATE cid={cid} reason={finding['auto_invalidate_reason']}"
                )
            if finding.get("gps_drift"):
                summary["gps_drift_detected"] += 1
                if ENABLE_GPS_DRIFT_INVALIDATION:
                    plan_manager.mark_stale(cid, "GPS_DRIFT")
                    summary["gps_drift_invalidated"] += 1
                    _log.info(
                        f"GPS_DRIFT_INVALIDATE cid={cid} drift={finding['gps_drift']['drift_m']}m"
                    )
        else:
            summary["healthy"] += 1

    # Gap-fill: kurierzy z realnym workiem ale bez aktywnego planu → plan Ziomka.
    if ENABLE_PLAN_FOR_ACTUAL_BAG:
        _gap_fill_plans(orders_state, plans, gps_positions, now, summary)

    _log.info(f"PLAN_RECHECK summary={summary}")
    return summary


if __name__ == "__main__":
    sys.exit(0 if run_recheck()["auto_invalidated"] == 0 or AUTO_INVALIDATE_STALE else 1)
