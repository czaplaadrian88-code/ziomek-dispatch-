"""feasibility_v2 - SLA-first check on top of route_simulator_v2.

Pipeline:
    fast filters (bag size, pickup reach, shift end)
        → simulate_bag_route_v2
        → SLA check via plan.sla_violations

Returns:
    (verdict, reason, metrics, plan)
    verdict ∈ {"MAYBE", "NO"}
    plan = RoutePlanV2 or None (None only when rejected by a fast filter)
"""
import json
import logging
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from typing import List, Tuple, Dict, Optional

from dispatch_v2 import osrm_client
from dispatch_v2 import common as C
from dispatch_v2 import prep_bias_anchor
from dispatch_v2 import effects_buffer as _EB  # K08 refaktoru: zapis shadow PO decyzji
from dispatch_v2.position_model import OriginTravelEstimate
from dispatch_v2.common import (
    ENABLE_C2_SHADOW_LOG,
    HAVERSINE_ROAD_FACTOR_BIALYSTOK,
    MAX_BAG_SANITY_CAP,
    USE_PER_ORDER_GATE,
    WARSAW,
)
from dispatch_v2.route_simulator_v2 import (
    OrderSim,
    RoutePlanV2,
    simulate_bag_route_v2,
    r6_thermal_anchor,   # INV-R6-ANCHOR-CONSISTENCY: wspólna kotwica termiczna R6
)

log = logging.getLogger(__name__)

C2_PER_ORDER_THRESHOLD_MIN = 35.0
C2_SHADOW_LOG_PATH = "/root/.openclaw/workspace/dispatch_state/c2_shadow_log.jsonl"
R6_BREACH_SHADOW_LOG_PATH = "/root/.openclaw/workspace/dispatch_state/r6_breach_shadow.jsonl"


# Hard cap per D3 MAX_BAG_SANITY_CAP (=8). F1.9b: R3 dynamic cap został
# zsoftowany po shadow-data 14.04 (za ostry, blokował Bartka na spread 7 km).
# Absolute hard block = sanity cap. R3 spread/dyn_cap nadal liczone jako
# telemetria w metrics, ale nie rejectują.
# SCALE-01: kanon wartości = flags.json (hot-reload, multi-city); stałe modułu
# zostają jako fallback gdy klucza brak (default = obecne produkcyjne 8 / 15 km).
# Konsumenci poniżej czytają przez _bag_sanity_cap() / _pickup_reach_km().
MAX_BAG_SIZE = MAX_BAG_SANITY_CAP
MAX_PICKUP_REACH_KM = float(getattr(C, "MAX_PICKUP_REACH_KM", 15.0))
SHIFT_END_BUFFER_MIN = 20
DEFAULT_SLA_MINUTES = 35


def _company_close_utc(now):
    """Koniec pracy FIRMY dla daty `now` (Warsaw): 23:00, a w pt/sb 24:00 (=00:00 dnia
    następnego). Zwraca aware UTC albo None (fail-soft). Salvage końca dnia (2026-06-18)."""
    try:
        from zoneinfo import ZoneInfo
        _w = ZoneInfo("Europe/Warsaw")
        loc = now.astimezone(_w)
        if loc.weekday() in (4, 5):  # piątek / sobota → 24:00
            close_loc = (loc + timedelta(days=1)).replace(
                hour=0, minute=0, second=0, microsecond=0)
        else:
            close_loc = loc.replace(hour=23, minute=0, second=0, microsecond=0)
        return close_loc.astimezone(timezone.utc)
    except Exception:
        return None


def _end_of_day_salvage(now):
    """(active, company_close_utc): czy jesteśmy w OSTATNIEJ GODZINIE pracy firmy ORAZ
    flaga ON. W tym oknie (zwykle jeden kurier) wolno zluzować twarde reguły końca zmiany
    — twardy warunek zostaje: ODBIÓR ≤ koniec pracy firmy (dostawa może wyjść później).
    Default OFF (decision_flag ENABLE_END_OF_DAY_SALVAGE). Fail-soft → (False, None)."""
    try:
        if not C.decision_flag("ENABLE_END_OF_DAY_SALVAGE"):
            return (False, None)
        close = _company_close_utc(now)
        if close is None:
            return (False, None)
        return ((close - timedelta(minutes=60)) <= now < close, close)
    except Exception:
        return (False, None)

# ===== BARTEK GOLD STANDARD thresholds (see docs/BARTEK_GOLD_STANDARD.md) =====
# R1: max delivery spread in bag (p90 of Bartek clean sample, n=47 bundles).
# L6.C2 (2026-07-04): alias kanonu C.MAX_DELIV_SPREAD_KM (scalenie 2 literałów 8.0;
# wartość niezmieniona = bajt-parytet; env-override przez MAX_DELIV_SPREAD_KM).
R1_MAX_DELIV_SPREAD_KM = C.MAX_DELIV_SPREAD_KM
# R3: dynamic cap — computed for telemetry only (F1.9b: no longer a hard block).
# Kept in metrics so we can observe what R3 WOULD have rejected.
R3_DYNAMIC_MAX = [(5.0, 5), (8.0, 4), (float("inf"), 3)]
# R5: mixed-restaurant pickup spread — p100 Bartek = 1.79 km.
R5_MAX_MIXED_PICKUP_SPREAD_KM = 2.5  # F2.1c: poluzowane z 1.8 (p100 Bartek) → 2.5 (akceptowalny mixed pickup spread)


def _bag_sanity_cap() -> int:
    """SCALE-01: bag sanity cap — flags.json (hot) → stała modułu common (=8)."""
    return int(C.load_flags().get("MAX_BAG_SANITY_CAP", C.MAX_BAG_SANITY_CAP))


def _pickup_reach_km() -> float:
    """SCALE-01: pickup-reach cap — flags.json (hot) → stała modułu common (=15 km)."""
    return float(C.load_flags().get("MAX_PICKUP_REACH_KM", C.MAX_PICKUP_REACH_KM))


def _road_km(a, b) -> float:
    """Haversine * Białystok road factor."""
    return osrm_client.haversine(a, b) * HAVERSINE_ROAD_FACTOR_BIALYSTOK


def _valid(coord) -> bool:
    # L2.1 sentinel-ingest (2026-07-01): flaga ON → kanoniczny walidator
    # (None/NaN/(0,0)/poza-bbox — 6 definicji sentinela → 1). OFF = legacy.
    if C.decision_flag("ENABLE_COORD_SENTINEL_INGEST_GUARD"):
        return C.coords_in_bialystok_bbox(coord)
    return bool(coord) and coord != (0.0, 0.0) and coord[0] != 0.0


def _parse_dt_utc(val):
    """ISO str / datetime → tz-aware UTC datetime, albo None."""
    if val is None:
        return None
    dt = val if isinstance(val, datetime) else None
    if dt is None:
        try:
            dt = datetime.fromisoformat(str(val).replace("Z", "+00:00"))
        except (TypeError, ValueError):
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def detect_return_to_restaurant(bag, new_order, plan,
                                same_rest_km: float = 0.08,
                                group_tol_min: float = 5.0):
    """F5 (2026-05-24) — wykrywa ZAKAZANY powrót do tej samej restauracji.

    Reguła Adriana: kurier nie odbiera z restauracji R, nie doręcza, i wraca do R
    po kolejny odbiór niosąc dowóz z R. Wrócić do R może tylko BEZ dowozu z R w bagu.

    Commit-aware (plan GRUPUJE odbiory po ETA → maskuje powrót wymuszony zamrożonym
    czas_kuriera wcześniejszego zlecenia, Case B 475698). Dla zlecenia B w bagu z TEJ
    SAMEJ restauracji (pickup_coords < same_rest_km):
      - realny odbiór B (picked_up_at / czas_kuriera commit / plan ETA) WCZEŚNIEJSZY
        od odbioru new_order o > group_tol_min → osobna wizyta (powrót),
      - B doręczany PO odbiorze new_order → dowóz z R wciąż w bagu na powrocie → ZAKAZANE.
    Zwraca order_id pierwszego takiego B, albo None.
    """
    np_coords = getattr(new_order, "pickup_coords", None)
    if not _valid(np_coords):
        return None
    t_np = _parse_dt_utc((plan.pickup_at or {}).get(new_order.order_id))
    if t_np is None:
        return None
    for b in bag:
        bp = getattr(b, "pickup_coords", None)
        if not _valid(bp):
            continue
        if osrm_client.haversine(bp, np_coords) >= same_rest_km:
            continue  # inna restauracja
        t_bp = (_parse_dt_utc(getattr(b, "picked_up_at", None))
                or _parse_dt_utc(getattr(b, "czas_kuriera_warsaw", None))
                or _parse_dt_utc((plan.pickup_at or {}).get(b.order_id)))
        t_bd = _parse_dt_utc((plan.predicted_delivered_at or {}).get(b.order_id))
        if t_bp is None or t_bd is None:
            continue
        gap_min = (t_np - t_bp).total_seconds() / 60.0
        if gap_min > group_tol_min and t_bd > t_np:
            return b.order_id
    return None


def _max_deliv_spread_km(bag, new_delivery) -> float:
    """Max pair-wise road km across all bag deliveries + new delivery."""
    coords = [b.delivery_coords for b in bag if _valid(b.delivery_coords)]
    if _valid(new_delivery):
        coords.append(new_delivery)
    if len(coords) < 2:
        return 0.0
    best = 0.0
    for i in range(len(coords)):
        for j in range(i + 1, len(coords)):
            d = _road_km(coords[i], coords[j])
            if d > best:
                best = d
    return best


def _dynamic_bag_cap(spread_km: float) -> int:
    for threshold, cap in R3_DYNAMIC_MAX:
        if spread_km <= threshold:
            return cap
    return R3_DYNAMIC_MAX[-1][1]


def _max_pickup_spread_from_bag(bag, new_pickup) -> float:
    """Max road km between new pickup and any bag pickup (skipping sentinels)."""
    if not _valid(new_pickup):
        return 0.0
    best = 0.0
    for b in bag:
        bp = b.pickup_coords
        if not _valid(bp):
            continue
        d = _road_km(bp, new_pickup)
        if d > best:
            best = d
    return best


def _detect_waves(
    bag, new_order, time_window_min: float = 12.0, space_threshold_km: float = 1.5
) -> List[List[str]]:
    """V3.28 P2 — wave detection (Adrian doktryna 2026-05-10).

    Wave = grupa orderów z `pickup_ready_at` w jednym oknie czasowym (±time_window_min)
    i pickup_coords w jednym korytarzu (≤space_threshold_km od poprzedniej w grupie).

    Filozofia: kurier robi atomic burst pickup → atomic burst drop, potem opcjonalnie
    kolejna fala. Bag z 1 falą = idealny ("linia/okrąg"). Bag z 2+ fal = OK jeśli
    inter-wave deadhead jest sensowny.

    Pomija picked_up bag orders (już mają punkt odbioru za sobą).

    Returns: List[List[order_id]] — każda lista to fala. Empty bag → [].
    """
    candidates = []
    for o in list(bag) + [new_order]:
        if getattr(o, "status", "assigned") == "picked_up":
            continue
        if getattr(o, "pickup_ready_at", None) is None:
            continue
        if not _valid(o.pickup_coords):
            continue
        candidates.append(o)
    if not candidates:
        return []
    candidates.sort(key=lambda o: o.pickup_ready_at)
    waves: List[List[str]] = [[candidates[0].order_id]]
    last = candidates[0]
    for o in candidates[1:]:
        dt = abs((o.pickup_ready_at - last.pickup_ready_at).total_seconds()) / 60.0
        dx = _road_km(o.pickup_coords, last.pickup_coords)
        if dt <= time_window_min and dx <= space_threshold_km:
            waves[-1].append(o.order_id)
        else:
            waves.append([o.order_id])
        last = o
    return waves


def _inter_wave_deadhead_km(waves: List[List[str]], all_orders) -> Tuple[float, float, int]:
    """Sum/max deadhead km between waves (drop_last_Wn → pickup_first_Wn+1).

    Approximation: gdy nie znamy faktycznego sequence dropów per fali, używamy
    pickup_coords pierwszego ordera w każdej fali jako proxy dla "punktu zwrotu".

    Returns: (total_deadhead_km, max_inter_wave_km, n_inter_wave_segments)
    """
    if len(waves) < 2:
        return (0.0, 0.0, 0)
    by_oid = {o.order_id: o for o in all_orders}
    total = 0.0
    mx = 0.0
    segs = 0
    for i in range(len(waves) - 1):
        w_now = waves[i]
        w_next = waves[i + 1]
        # Last drop tej fali ≈ pickup ostatniego ordera (proxy — nie znamy faktycznego drop sequence tu)
        end_oid = w_now[-1]
        start_oid = w_next[0]
        end_o = by_oid.get(end_oid)
        start_o = by_oid.get(start_oid)
        if end_o is None or start_o is None:
            continue
        # Lepszy proxy: deliv_coords ostatniego ordera w fali (gdzie kurier "wraca")
        # vs pickup_coords pierwszego ordera następnej fali
        end_pos = end_o.delivery_coords if _valid(end_o.delivery_coords) else end_o.pickup_coords
        start_pos = start_o.pickup_coords
        if not _valid(end_pos) or not _valid(start_pos):
            continue
        d = _road_km(end_pos, start_pos)
        total += d
        if d > mx:
            mx = d
        segs += 1
    return (round(total, 2), round(mx, 2), segs)


def check_per_order_35min_rule(
    plan: RoutePlanV2,
    threshold_min: float = C2_PER_ORDER_THRESHOLD_MIN,
) -> Tuple[bool, Dict]:
    """F2.2 C2: Per-order delivery time hard gate.

    Uses plan.per_order_delivery_times (populated by C1). Fail-closed on None.

    Returns:
        (passes, details) where passes=True if all orders <= threshold.
        details = {'violations': [(oid, elapsed), ...], 'max_elapsed', 'total_orders',
                   'per_order_data_available': bool}
    """
    details = {
        "violations": [],
        "max_elapsed": 0.0,
        "total_orders": 0,
        "per_order_data_available": False,
    }
    if plan.per_order_delivery_times is None:
        return (False, details)
    details["per_order_data_available"] = True
    details["total_orders"] = len(plan.per_order_delivery_times)
    for oid, elapsed in plan.per_order_delivery_times.items():
        if elapsed > details["max_elapsed"]:
            details["max_elapsed"] = round(float(elapsed), 2)
        if elapsed > threshold_min:
            details["violations"].append((oid, round(float(elapsed), 2)))
    passes = len(details["violations"]) == 0
    return (passes, details)


def _emit_r6_breach_shadow(new_order, worst_oid, worst_bt, violations, metrics,
                           bag_total=None, now=None, tier=None) -> None:
    """Append R6_HARD_REJECT event to dispatch_state/r6_breach_shadow.jsonl (log-only).

    Mierzy falszywe-odrzuty R6: offline join new_order_id -> sla_log (czy realnie
    dostarczono <=35 min). Zero wplywu na decyzje — append-only, fail-soft.

    14.06 TIER-AWARE: loguje `tier` kandydata + skalibrowany p80 bag-time dla
    bag<=6 (obserwowalnosc incl. gold-bag-5, ktora chcemy zwalidowac na zywo).
    `tier_cap` = 4 dla gold, 3 dla reszty (reguła z replay v3 tier-aware:
    gold bezpieczny @bag4, std/slow/new regresuja; gold-bag-5 zbieramy dalej).
    `within_tier_cap` = czy ten odrzut bylby w zasiegu live-gate. LOG-ONLY,
    gate live = osobna flaga (Krok 3). eta_quantile_calibrate fail-soft.
    """
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event_type": "R6_HARD_REJECT",
        "new_order_id": getattr(new_order, "order_id", None),
        "worst_oid": worst_oid,
        "worst_bag_time_min": round(float(worst_bt), 1),
        "n_violations": len(violations),
        "r6_max_bag_time_min": metrics.get("r6_max_bag_time_min"),
        "bag_total": bag_total,
        "tier": tier,
        "restaurant": getattr(new_order, "restaurant", None),
        "delivery_coords": getattr(new_order, "delivery_coords", None)
        or getattr(new_order, "drop_coords", None),
    }
    if bag_total is not None and bag_total <= 6:
        try:
            from dispatch_v2.calib_maps import eta_quantile_calibrate
            cal = eta_quantile_calibrate(worst_bt, now=now, quantile="p80")
            event["bag_time_calibrated_p80"] = cal
            event["would_pass_calibrated"] = (cal is not None and cal <= 35.0)
            _tcap = 4 if tier == "gold" else 3
            event["tier_cap"] = _tcap
            event["within_tier_cap"] = (bag_total <= _tcap)
        except Exception as _ce:
            event["bag_time_calibrated_p80"] = None
            log.warning(f"R6 breach shadow calib failed: {type(_ce).__name__}: {_ce}")
    # K08: sam ZAPIS diver-towany PO decyzję (event z ts zbudowany w miejscu
    # zdarzenia — semantyka czasu bez zmian); OFF/awaria → wprost jak dotąd.
    if not _EB.divert(_write_r6_breach_line, event):
        _write_r6_breach_line(event)


def _write_r6_breach_line(event) -> None:
    """K08: wydzielony writer r6_breach (treść 1:1 z poprzednim inline)."""
    try:
        with open(R6_BREACH_SHADOW_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            f.flush()
    except Exception as e:
        log.warning(f"R6 breach shadow log write failed: {e}")


def _emit_c2_shadow_diff_event(
    current_verdict: str,
    c2_passes: bool,
    c2_details: Dict,
    plan: RoutePlanV2,
    metrics: Dict,
    new_order_id: str,
    bag_size_before: int,
) -> None:
    """Append C2_SHADOW_DIFF event to dispatch_state/c2_shadow_log.jsonl.

    Only called when current verdict (with existing gates) differs from C2+existing combo.
    Zero impact on dispatch flow — log-only.
    """
    new_verdict = current_verdict if c2_passes else "NO"
    event = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "event_type": "C2_SHADOW_DIFF",
        "current_verdict": current_verdict,
        "new_verdict_if_c2_enabled": new_verdict,
        "c2_would_reject": not c2_passes,
        "per_order_data_available": c2_details["per_order_data_available"],
        "max_elapsed_min": c2_details["max_elapsed"],
        "total_orders": c2_details["total_orders"],
        "violations": c2_details["violations"],
        "per_order_delivery_times": dict(plan.per_order_delivery_times) if plan.per_order_delivery_times else None,
        "sequence": plan.sequence,
        "total_duration_min": plan.total_duration_min,
        "strategy": plan.strategy,
        "new_order_id": new_order_id,
        "bag_size_before": bag_size_before,
        "r6_max_bag_time_min": metrics.get("r6_max_bag_time_min"),
    }
    # K08: sam ZAPIS divertowany PO decyzję (ts eventu z miejsca zdarzenia).
    if not _EB.divert(_write_c2_shadow_line, event):
        _write_c2_shadow_line(event)


def _write_c2_shadow_line(event) -> None:
    """K08: wydzielony writer c2_shadow (treść 1:1 z poprzednim inline)."""
    try:
        with open(C2_SHADOW_LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False, default=str) + "\n")
            f.flush()
    except Exception as e:
        log.warning(f"C2 shadow log write failed: {e}")


def _fail12_storepos_strict_on() -> bool:
    """Z-06 (audyt 2026-06-10): hot-reload kill-switch strict store-pos w FAIL12.

    flags.json ENABLE_FAIL12_STOREPOS_STRICT (default: env const w common, ON).
    Defensive: błąd odczytu flag → env const.
    """
    try:
        return bool(C.flag(
            "ENABLE_FAIL12_STOREPOS_STRICT",
            default=bool(getattr(C, "ENABLE_FAIL12_STOREPOS_STRICT", True))))
    except Exception:
        return bool(getattr(C, "ENABLE_FAIL12_STOREPOS_STRICT", True))


def check_feasibility_v2(
    courier_pos: Optional[Tuple[float, float]],
    bag: List[OrderSim],
    new_order: OrderSim,
    shift_end: Optional[datetime] = None,
    shift_start: Optional[datetime] = None,  # V3.25 STEP B (R-01 PRE-CHECK)
    now: Optional[datetime] = None,
    pickup_ready_at: Optional[datetime] = None,
    sla_minutes: int = DEFAULT_SLA_MINUTES,
    base_sequence: Optional[List[str]] = None,  # V3.19d passthrough
    r07_chain_eta_utc: Optional[datetime] = None,  # V3.26 STEP 6 (R-07 v2) — chain_eta source of truth dla R-01 MANDATORY
    pos_source: Optional[str] = None,  # V3.28 ETAP 2 — pre_shift departure clamp gate
    available_from: Optional[datetime] = None,  # L4 2026-07-02 — jedno źródło max(now,shift_start) z courier_resolver
    courier_tier: Optional[str] = None,  # 2026-05-17 — tier-aware DWELL (tier_bag)
    schedule_source_stale: bool = False,  # D2 (audyt 2026-05-28) — grafik STALE → soft-degrade Gate 1
    pos_from_store: bool = False,  # Z-06 (audyt 2026-06-10) — pozycja odtworzona z last-known-pos store (≤25 min), NIE świeży fix tego ticku
    origin_travel: Optional[OriginTravelEstimate] = None,
) -> Tuple[str, str, Dict, Optional[RoutePlanV2]]:
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    metrics: Dict = {"bag_size_before": len(bag)}
    if origin_travel is not None:
        metrics.update({
            "origin_travel_provenance": origin_travel.provenance,
            "origin_road_km": origin_travel.road_km,
            "origin_drive_min_soft": origin_travel.drive_min_soft,
            "origin_drive_min_hard": origin_travel.drive_min_hard,
            "r1_origin_geometry_evaluable": False,
            "r5_origin_geometry_evaluable": False,
        })

    # === FAST FILTERS ===

    # D3 sanity cap (MAX_BAG_SIZE = MAX_BAG_SANITY_CAP = 8). R3 absolute cap
    # usunięty w F1.9b po shadow data — blokował Bartka na legit bundlach.
    # SCALE-01: cap z flags.json (hot, multi-city) z fallback do stałej =8.
    _bag_cap = _bag_sanity_cap()
    bag_after = len(bag) + 1
    if len(bag) >= _bag_cap:
        return ("NO", f"bag_full ({len(bag)}/{_bag_cap})", metrics, None)

    # === TWARDY cap worka per tier (Adrian 2026-06-18) — powyżej = patologia (B_load) ===
    # Metryka liczona ZAWSZE (shadow 'would-cap'); HARD reject TYLKO gdy flaga ON (flags.json, hot).
    # Tier nieznany -> default 6 (łapie tylko patologię 7+). Egzekwowane parami z przelewem-na-falę
    # + auto-przedłużeniem (zamiast KOORD). Rollback: flaga ENABLE_HARD_TIER_BAG_CAP=false.
    _hard_cap = C.HARD_TIER_BAG_CAP.get(courier_tier, C.HARD_TIER_BAG_CAP_DEFAULT)
    metrics["hard_tier_bag_cap"] = _hard_cap
    metrics["would_hard_cap"] = bag_after > _hard_cap
    if metrics["would_hard_cap"] and C.load_flags().get("ENABLE_HARD_TIER_BAG_CAP", False):
        return ("NO", f"hard_tier_bag_cap ({courier_tier or '?'} {bag_after}>{_hard_cap})", metrics, None)

    # Telemetria trasy (dawna R7 long-haul — reguła USUNIĘTA L6.C 2026-07-04, R6-K-B:
    # martwy REJECT za sentinelem 99 km od F2.1c, nieosiągalny w Białymstoku ~15 km).
    # Metryki r7_* ZOSTAJĄ — są NOŚNE: r7_bag_size czyta eta_calibration_logger +
    # tools/eta_truth_map (bramka Fali A), r7_ride_km czyta tools/fleet_t15_replay.
    # Usunięte razem z regułą: r7_is_longhaul (konsumował tylko serializer) +
    # LONG_HAUL_DISTANCE_KM (common). Przywrócenie reguły long-haul = NOWY sprint
    # z realnym progiem i pomiarem, nie odkomentowanie.
    if _valid(new_order.pickup_coords) and _valid(new_order.delivery_coords):
        r7_ride_km = _road_km(new_order.pickup_coords, new_order.delivery_coords)
        r7_warsaw_hour = now.astimezone(WARSAW).hour
        r7_in_peak = (
            C.LONG_HAUL_PEAK_HOURS_START
            <= r7_warsaw_hour
            <= C.LONG_HAUL_PEAK_HOURS_END
        )
        metrics["r7_ride_km"] = round(r7_ride_km, 2)
        metrics["r7_warsaw_hour"] = r7_warsaw_hour
        metrics["r7_in_peak"] = r7_in_peak
        metrics["r7_bag_size"] = len(bag)

    # R1 spread outlier — SOFT (NIE hard block, zweryfikowane audytem 2026-05-21).
    # Tu liczymy tylko metryki do telemetrii (learning_log); reject NIE następuje.
    # Egzekwowane jako kara scoringowa (dispatch_pipeline ~2191) + zerowanie bonusu
    # bundla "Fix C" (BUNDLE_MAX_DELIV_SPREAD_KM, dispatch_pipeline ~2369). Realne
    # twarde granice bundla = R6 (35min) + SLA. R3 dynamic cap również zsoftowany.
    if bag and _valid(new_order.delivery_coords):
        spread_km = _max_deliv_spread_km(bag, new_order.delivery_coords)
        metrics["deliv_spread_km"] = round(spread_km, 2)
        metrics["dynamic_bag_cap"] = _dynamic_bag_cap(spread_km)
        metrics["r3_soft_would_block"] = bag_after > metrics["dynamic_bag_cap"]
        if spread_km > R1_MAX_DELIV_SPREAD_KM:
            metrics["r1_violation_km"] = round(spread_km - R1_MAX_DELIV_SPREAD_KM, 2)
        else:
            metrics["r1_violation_km"] = 0.0
        # V3.28 P1 — R1 directionality (corridor cosine) — Adrian doktryna 2026-05-10.
        # Spread w km nie wystarcza: 8 km drops w jednym kierunku (Nowe Miasto cluster) =
        # OK trasa; 4 km drops w przeciwnych dzielnicach (Skorupy + Henrykowo) = bad.
        # Mierzymy "kierunkowość" jako średnia cosine similarity wektorów courier→drop.
        # avg_cos ≈ 1.0: wszystkie drops w tym samym kierunku (tight corridor)
        # avg_cos ≈ 0.0: drops w prostopadłych kierunkach
        # avg_cos ≈ -1.0: drops w przeciwnych kierunkach (opposite split)
        if _valid(courier_pos):
            drops_all: List[Tuple[float, float]] = []
            for b in bag:
                if _valid(b.delivery_coords):
                    drops_all.append(b.delivery_coords)
            drops_all.append(new_order.delivery_coords)
            if len(drops_all) >= 2:
                dirs: List[Tuple[float, float]] = []
                for d in drops_all:
                    vx = d[0] - courier_pos[0]
                    vy = d[1] - courier_pos[1]
                    n = (vx * vx + vy * vy) ** 0.5
                    if n > 1e-9:
                        dirs.append((vx / n, vy / n))
                if len(dirs) >= 2:
                    cos_sum = 0.0
                    pairs = 0
                    for i in range(len(dirs)):
                        for j in range(i + 1, len(dirs)):
                            cos_sum += dirs[i][0] * dirs[j][0] + dirs[i][1] * dirs[j][1]
                            pairs += 1
                    metrics["r1_avg_pairwise_cosine"] = round(
                        cos_sum / pairs if pairs else 0.0, 3
                    )
            # FIX 2 (2026-05-22): izolowany kierunek NOWEJ dostawy vs średni kierunek
            # dostaw bagu (NIE uśredniona para — outlier nie rozcieńczony przez spójne
            # dropy, jak Hallera 0.304 avg vs -0.39 izolowany) + dystans nowej dostawy od
            # centroidu dostaw bagu. Zasila R-09 oś nowej dostawy (dispatch_pipeline veto).
            _bag_drops_fix2 = [b.delivery_coords for b in bag if _valid(b.delivery_coords)]
            if _bag_drops_fix2:
                _cx = sum(d[0] for d in _bag_drops_fix2) / len(_bag_drops_fix2)
                _cy = sum(d[1] for d in _bag_drops_fix2) / len(_bag_drops_fix2)
                metrics["r1_new_drop_dist_km"] = round(
                    osrm_client.haversine((_cx, _cy), new_order.delivery_coords), 2
                )
                _bag_dirs2 = []
                for d in _bag_drops_fix2:
                    vx = d[0] - courier_pos[0]
                    vy = d[1] - courier_pos[1]
                    n = (vx * vx + vy * vy) ** 0.5
                    if n > 1e-9:
                        _bag_dirs2.append((vx / n, vy / n))
                if _bag_dirs2:
                    _mx = sum(v[0] for v in _bag_dirs2) / len(_bag_dirs2)
                    _my = sum(v[1] for v in _bag_dirs2) / len(_bag_dirs2)
                    _mn = (_mx * _mx + _my * _my) ** 0.5
                    nvx = new_order.delivery_coords[0] - courier_pos[0]
                    nvy = new_order.delivery_coords[1] - courier_pos[1]
                    _nn = (nvx * nvx + nvy * nvy) ** 0.5
                    if _mn > 1e-9 and _nn > 1e-9:
                        metrics["r1_new_drop_cosine"] = round(
                            (_mx / _mn) * (nvx / _nn) + (_my / _mn) * (nvy / _nn), 3
                        )

    # R5 mixed-restaurant pickup spread — same restaurant → spread=0 (no fire).
    if bag and _valid(new_order.pickup_coords):
        pickup_spread_km = _max_pickup_spread_from_bag(bag, new_order.pickup_coords)
        metrics["pickup_spread_km"] = round(pickup_spread_km, 2)
        if pickup_spread_km > R5_MAX_MIXED_PICKUP_SPREAD_KM:
            metrics["r5_violation_km"] = round(pickup_spread_km - R5_MAX_MIXED_PICKUP_SPREAD_KM, 2)
        else:
            metrics["r5_violation_km"] = 0.0
        # V3.28 P1 — R5 pickup detour per order — Adrian doktryna 2026-05-10.
        # Spread w km nie wystarcza: Wasilków pickup + Galeria Biała pickup → Iłłady drops
        # = pickup spread 5km > 2.5km próg, ale Galeria Biała JEST PO DRODZE z courier do
        # Wasilkowa, więc real detour ~0. Mierzymy "po drodze" jako:
        # detour_total = nearest-neighbor route(courier→all_pickups) - solo_route(courier→first_pickup).
        # Per-order detour = detour_total / n_pickups. <0.5 km = po drodze.
        if _valid(courier_pos):
            pickups_open: List[Tuple[float, float]] = []
            for b in bag:
                if _valid(b.pickup_coords) and getattr(b, "status", "assigned") != "picked_up":
                    pickups_open.append(b.pickup_coords)
            pickups_open.append(new_order.pickup_coords)
            if len(pickups_open) >= 2:
                # Solo baseline: courier → najbliższy pickup (greedy)
                solo_first = min(pickups_open, key=lambda p: _road_km(courier_pos, p))
                solo_km = _road_km(courier_pos, solo_first)
                # Multi route: nearest-neighbor sequencing all pickups
                remaining = list(pickups_open)
                cur = courier_pos
                multi_km = 0.0
                while remaining:
                    nxt = min(remaining, key=lambda p: _road_km(cur, p))
                    multi_km += _road_km(cur, nxt)
                    cur = nxt
                    remaining.remove(nxt)
                detour_total = max(0.0, multi_km - solo_km)
                metrics["r5_pickup_detour_total_km"] = round(detour_total, 2)
                metrics["r5_pickup_detour_per_order_km"] = round(
                    detour_total / len(pickups_open), 2
                )

    # V3.28 P2 — wave detection (Adrian doktryna 2026-05-10).
    # Cluster orderów po pickup_ready_at + pickup_coords. Bag z 1 falą = idealny
    # ("linia/okrąg"). Bag z 2+ fal = OK jeśli inter-wave deadhead sensowny.
    # NIE refactoryzujemy TSP tutaj — eksponujemy tylko metrykę dla scoring.
    waves = _detect_waves(bag, new_order)
    metrics["n_waves"] = len(waves)
    if len(waves) >= 2:
        deadhead_total, deadhead_max, n_segs = _inter_wave_deadhead_km(
            waves, list(bag) + [new_order]
        )
        metrics["inter_wave_deadhead_total_km"] = deadhead_total
        metrics["inter_wave_deadhead_max_km"] = deadhead_max
        metrics["inter_wave_n_segments"] = n_segs
    else:
        metrics["inter_wave_deadhead_total_km"] = 0.0
        metrics["inter_wave_deadhead_max_km"] = 0.0
        metrics["inter_wave_n_segments"] = 0

    # R8 (F2.1c) — pickup_span (T_KUR spread w bagu). SOFT — telemetria + kara
    # scoringowa (dispatch_pipeline ~2298), NIE hard reject (audyt 2026-05-21).
    # PICKUP_SPAN_HARD_* to próg kary, nie bramka feasibility.
    if bag:
        bag_size_after = len(bag) + 1
        pra_list = [b.pickup_ready_at for b in bag if b.pickup_ready_at is not None and b.status != "picked_up"]  # F2.1c hotfix: picked_up już odebrany, historyczny T_KUR nie liczy się do span
        if new_order.pickup_ready_at is not None:
            pra_list.append(new_order.pickup_ready_at)
        if len(pra_list) >= 2:
            span_min = (max(pra_list) - min(pra_list)).total_seconds() / 60.0
            metrics["r8_pickup_span_min"] = round(span_min, 1)
            hard_cap = (
                C.PICKUP_SPAN_HARD_BUNDLE3_MIN if bag_size_after >= 3
                else C.PICKUP_SPAN_HARD_BUNDLE2_MIN
            )
            if span_min > hard_cap:
                metrics["r8_violation_min"] = round(span_min - hard_cap, 2)
            else:
                metrics["r8_violation_min"] = 0.0
        else:
            metrics["r8_pickup_span_min"] = None  # graceful degradation

    pickup_dist_km = (
        float(origin_travel.road_km) if origin_travel is not None
        else osrm_client.haversine(courier_pos, new_order.pickup_coords)
    )
    metrics["pickup_dist_km"] = round(pickup_dist_km, 2)
    if origin_travel is not None:
        metrics["pickup_drive_min_hard"] = origin_travel.drive_min_hard
    # SCALE-01: pickup-reach cap z flags.json (hot, multi-city), fallback =15 km.
    if pickup_dist_km > _pickup_reach_km():
        return ("NO", f"pickup_too_far ({pickup_dist_km:.1f} km)", metrics, None)

    # V3.25 STEP B (R-01 SCHEDULE-HARDENING) — unconditional PRE-CHECK przed
    # scoring path. Fail-CLOSED: brak shift_end → HARD REJECT (NO_ACTIVE_SHIFT)
    # zamiast silent bypass H1 (pre-V3.25). Pickup vs shift window:
    #   pickup > shift_end → HARD REJECT PICKUP_POST_SHIFT
    #   pickup < shift_start - 30 min → HARD REJECT PRE_SHIFT_TOO_EARLY
    #   pickup ∈ [shift_start - 30, shift_start) → soft penalty -20 (warm-up)
    # Dropoff hard-reject zachowane w V3.24-A line ~386 (post-simulate).
    if C.ENABLE_V325_SCHEDULE_HARDENING:
        # V3.26 STEP 6 (R-07 v2) MANDATORY integration gdy flag True (Adrian ACK #5):
        # chain_eta jest source of truth dla R-01 schedule check. Konsystencja
        # priorytet — bez chain_eta R-01 używa pickup_ready_at (kurier arrive time
        # INNY niż restaurant ready time).
        if C.ENABLE_V326_R07_CHAIN_ETA and r07_chain_eta_utc is not None:
            pickup_ref = r07_chain_eta_utc
            metrics["v325_pickup_ref_source"] = "r07_chain_eta"
        else:
            pickup_ref = pickup_ready_at if pickup_ready_at is not None else now
            metrics["v325_pickup_ref_source"] = "pickup_ready_at"
        if pickup_ref.tzinfo is None:
            pickup_ref = pickup_ref.replace(tzinfo=timezone.utc)
        # Gate 1: brak shift_end → courier nie ma active shift mapping
        if shift_end is None:
            if C.ENABLE_D2_STALE_SCHEDULE_SOFT and schedule_source_stale:
                # D2 (audyt 2026-05-28): grafik wykryty jako STALE (awaria pliku, ten sam
                # 30min próg co shift_notifications.worker). Zamiast hard-reject CAŁEJ floty
                # NO_ACTIVE_SHIFT (BRAK KANDYDATÓW z powodu awarii, nie realnej niedostępności)
                # → soft-degrade: nakładamy penalty w scoring + pozwalamy przejść feasibility.
                # Brak okna shift → pomijamy Gate 2/3 (nie ma _shift_end do porównania).
                # Alert: polegamy na istniejącym STALE_SCHEDULE_AGE (shift_notifications.worker)
                # — D2 nie dubluje alertu, tylko soft-degraduje + loguje metrykę.
                metrics["d2_stale_schedule_soft"] = True
                metrics["d2_soft_penalty"] = C.D2_STALE_SCHEDULE_SOFT_PENALTY
            elif C.decision_flag("ENABLE_FAIL12_SCHEDULE_FAILOPEN") and (
                    len(bag) > 0
                    or (pos_source == "gps" and not (
                        pos_from_store and _fail12_storepos_strict_on()))):
                # FAIL-12 (audyt 2026-06-03): grafik padł/niepełny → shift_end=None mimo
                # że kurier FIZYCZNIE pracuje (ma bag LUB świeży GPS ten tick). Zamiast
                # hard-reject NO_ACTIVE_SHIFT (fail-CLOSED całej floty, precedens #471036)
                # → fail-OPEN: przepuść przez Gate 1. Bag/świeży GPS to twardy dowód pracy
                # niezależny od grafiku. R6 35min / SLA / post-shift egzekwowane dalej niżej.
                # Z-06 (audyt 2026-06-10): rescue z last-known-pos store replay'uje
                # pierwotny label "gps" — pozycja sprzed ≤25 min to NIE jest świeży fix
                # tego ticku, więc nie jest dowodem pracy → nie przechodzi gate'u
                # (flaga ENABLE_FAIL12_STOREPOS_STRICT, default ON). Bag wystarcza dalej.
                fail12_signal = "bag" if len(bag) > 0 else "gps"
                metrics["fail12_schedule_failopen"] = True
                metrics["fail12_signal"] = fail12_signal
                # Z2 anti-silent-failure: fail-OPEN MASKUJE realną awarię grafiku → GŁOŚNO.
                log.warning(
                    "FAIL12_SCHEDULE_FAILOPEN: shift_end=None ale kurier aktywny "
                    "(signal=%s bag=%d pos_source=%s) — fail-OPEN soft-degrade zamiast "
                    "NO_ACTIVE_SHIFT. SPRAWDŹ GRAFIK (Google Sheet awaria/niepełny?).",
                    fail12_signal, len(bag), pos_source,
                )
            else:
                # Z-06 obserwowalność: fail-open ZABLOKOWANY wyłącznie przez
                # store-pos strict (kurier przeszedłby gate na replayowanym "gps").
                if (C.decision_flag("ENABLE_FAIL12_SCHEDULE_FAILOPEN")
                        and pos_source == "gps"
                        and pos_from_store):
                    metrics["fail12_storepos_blocked"] = True
                    log.warning(
                        "FAIL12_STOREPOS_BLOCKED: shift_end=None, pos_source=gps "
                        "ale pozycja ze store (nie świeży fix) — fail-open odmówiony, "
                        "NO_ACTIVE_SHIFT (Z-06; kill-switch ENABLE_FAIL12_STOREPOS_STRICT=false).",
                    )
                metrics["v325_reject_reason"] = "NO_ACTIVE_SHIFT"
                return ("NO", "v325_NO_ACTIVE_SHIFT (cs.shift_end=None — brak schedule mapping)", metrics, None)
        else:
            # Normalize shift_end TZ
            _shift_end = shift_end.replace(tzinfo=timezone.utc) if shift_end.tzinfo is None else shift_end
            # Gate 2: pickup post-shift hard reject
            if pickup_ref > _shift_end:
                # END-OF-DAY SALVAGE: w ostatniej godzinie pracy firmy (zwykle jeden kurier)
                # pozwól wziąć zlecenie mimo końca JEGO zmiany — jeśli odbierze przed końcem
                # pracy firmy. Twardy warunek: pickup ≤ company_close. Dostawa może wyjść później.
                _salv, _close = _end_of_day_salvage(now)
                if _salv and _close is not None and pickup_ref <= _close:
                    metrics["end_of_day_salvage"] = True
                    metrics["end_of_day_salvage_close_iso"] = _close.isoformat()
                    metrics["end_of_day_salvage_pickup_excess_min"] = round(
                        (pickup_ref - _shift_end).total_seconds() / 60.0, 2)
                else:
                    excess = (pickup_ref - _shift_end).total_seconds() / 60.0
                    metrics["v325_pickup_post_shift_excess_min"] = round(excess, 2)
                    metrics["v325_reject_reason"] = "PICKUP_POST_SHIFT"
                    return (
                        "NO",
                        f"v325_PICKUP_POST_SHIFT (pickup {pickup_ref.strftime('%H:%M')} "
                        f"vs shift_end {_shift_end.strftime('%H:%M')}, excess +{excess:.1f}min)",
                        metrics, None,
                    )
            # Gate 3: pre-shift hard reject + soft penalty zone
            if shift_start is not None:
                _shift_start = shift_start.replace(tzinfo=timezone.utc) if shift_start.tzinfo is None else shift_start
                too_early_min = (_shift_start - pickup_ref).total_seconds() / 60.0
                if too_early_min > C.V325_PRE_SHIFT_HARD_REJECT_MIN:
                    metrics["v325_pre_shift_too_early_min"] = round(too_early_min, 2)
                    metrics["v325_reject_reason"] = "PRE_SHIFT_TOO_EARLY"
                    return (
                        "NO",
                        f"v325_PRE_SHIFT_TOO_EARLY (pickup {pickup_ref.strftime('%H:%M')} "
                        f"vs shift_start {_shift_start.strftime('%H:%M')}, before by {too_early_min:.1f}min)",
                        metrics, None,
                    )
                if 0 < too_early_min <= C.V325_PRE_SHIFT_HARD_REJECT_MIN:
                    # Pre-shift warm-up zone — soft penalty (kurier może zacząć ale otrzyma penalty w scoring).
                    metrics["v325_pre_shift_soft_penalty_min"] = round(too_early_min, 2)
                    metrics["v325_pre_shift_soft_penalty"] = C.V325_PRE_SHIFT_SOFT_PENALTY
                else:
                    metrics["v325_pre_shift_soft_penalty"] = 0
        # Gate 4: dropoff hard reject post-simulate (V3.25 explicit, mirrors V3.24-A
        # but flag-gated osobno) — patrz blok niżej dot. v325_dropoff_after_shift_check.

    if shift_end is not None:
        if shift_end.tzinfo is None:
            shift_end = shift_end.replace(tzinfo=timezone.utc)
        remaining_min = (shift_end - now).total_seconds() / 60.0
        metrics["shift_remaining_min"] = round(remaining_min, 1)
        # V3.24-A: legacy SHIFT_END_BUFFER_MIN=20 check skipowany gdy flag ON
        # (zastąpiony dokładniejszym post-simulate planned_dropoff > shift_end+5 check,
        # patrz niżej tuż po R6). Flag OFF → legacy behavior.
        if not C.ENABLE_V324A_SCHEDULE_INTEGRATION:
            if remaining_min < SHIFT_END_BUFFER_MIN:
                _salv_se, _ = _end_of_day_salvage(now)
                if not _salv_se:  # salvage końca dnia pomija bufor 20min
                    return ("NO", f"shift_ending ({remaining_min:.1f} min left)", metrics, None)
                metrics["end_of_day_salvage"] = True

    # === SLA SIMULATION ===

    if pickup_ready_at is not None and new_order.pickup_ready_at is None:
        new_order = replace(new_order, pickup_ready_at=pickup_ready_at)

    # V3.28 ETAP 2 (2026-05-08): pre_shift departure clamp. Pre_shift/no_gps
    # kurier z shift_start > now → simulate dostaje earliest_departure=shift_start.
    # Plan timestamps shift'owane od shift_start (eliminuje fikcyjny "kurier
    # startuje teraz" dla kuriera który jeszcze nie pracuje). Flag-gated.
    #
    # L4 (2026-07-02, F1): gdy ENABLE_AVAILABLE_FROM_SINGLE_SOURCE ON — konsumuj
    # available_from (=max(now,shift_start) policzone RAZ w courier_resolver)
    # ZAMIAST re-derywacji `shift_start>now && pos_source∈{pre_shift,no_gps}`.
    # Równoważne dla pre_shift (available_from=shift_start) i no_gps on-shift
    # (available_from=now → no-op); domyka też GPS-przed-zmianą (floor zależy od
    # available_from, nie etykiety pos_source). OFF → dokładnie stara ścieżka niżej.
    earliest_departure = None
    _af_single = (C.decision_flag("ENABLE_AVAILABLE_FROM_SINGLE_SOURCE")
                  and available_from is not None)
    if _af_single:
        _af = (available_from.replace(tzinfo=timezone.utc)
               if available_from.tzinfo is None else available_from)
        if _af > now:
            earliest_departure = _af
            metrics["earliest_departure_utc"] = earliest_departure.isoformat()
            metrics["pre_shift_clamp_applied"] = True
            metrics["af_clamp_applied"] = True
    elif (C.decision_flag("ENABLE_PRE_SHIFT_DEPARTURE_CLAMP")
            and shift_start is not None
            and pos_source in ("pre_shift", "no_gps")
            and shift_start > now):
        earliest_departure = shift_start
        metrics["earliest_departure_utc"] = earliest_departure.isoformat()
        metrics["pre_shift_clamp_applied"] = True

    # 2026-05-17 tier-aware DWELL + tempo → K15 (ADR-R03): parametryzacja z
    # JEDNEGO źródła dla silnika i re-planera = core.planner.tier_params
    # (semantyka silnika bez gate'a: dwell ZAWSZE tier-aware; wartości 1:1
    # z dawnym inline C.dwell_for_tier + C.speed_mult_for_tier). Wywołanie
    # simulate_bag_route_v2 niżej ZOSTAJE lokalnym symbolem feasibility_v2 —
    # świadome N-D: kontrakt monkeypatch setek testów; to ta sama funkcja.
    from dispatch_v2.core import planner as _k15_planner
    _dwell_pickup, _dwell_dropoff, _drive_speed_mult = \
        _k15_planner.tier_params(courier_tier)
    metrics["dwell_tier"] = courier_tier
    metrics["dwell_pickup_min"] = _dwell_pickup
    metrics["dwell_dropoff_min"] = _dwell_dropoff
    metrics["drive_speed_mult"] = _drive_speed_mult

    plan = simulate_bag_route_v2(
        courier_pos, bag, new_order, now=now, sla_minutes=sla_minutes,
        base_sequence=base_sequence, earliest_departure=earliest_departure,
        dwell_pickup=_dwell_pickup, dwell_dropoff=_dwell_dropoff,
        drive_speed_mult=_drive_speed_mult,
        origin_travel=origin_travel,
    )

    metrics["sequence"] = plan.sequence
    metrics["total_duration_min"] = plan.total_duration_min
    metrics["strategy"] = plan.strategy
    metrics["osrm_fallback_used"] = plan.osrm_fallback_used
    metrics["sla_violations_count"] = plan.sla_violations
    # O2 cap-Z RESEQ (2026-07-02, ENABLE_O2_CAPZ_RESEQ): feasibility ocenia RESEQ'owaną
    # sekwencję (plan z route_simulator już przeszedł reseq u źródła). Metryka obs decyzji
    # reseq — auto-serializacja L1.1 (deny-lista); obecna ⇔ flaga ON (ON≠OFF). Zero wpływu
    # OFF (plan.o2_capz=None). Trójka: route_simulator (źródło) + feasibility (ta linia) +
    # plan_recheck (dziedziczy przez _sweep→simulate_bag_route_v2).
    if getattr(plan, "o2_capz", None) is not None:
        metrics["o2_capz"] = plan.o2_capz

    # F2 R1-WAVE-SCOPED (2026-05-24) — kierunkowość korytarza liczona TYLKO na
    # dropach współistniejących z falą nowego ordera. Root cause korpusu
    # eod_drafts/2026-05-24/ziomek_bad_picks_corpus.md: r1_avg_pairwise_cosine /
    # r1_new_drop_cosine liczone na WSZYSTKICH dropach bagu z pozycji kuriera
    # (linie 328-387, PRZED planem) zanieczyszczają sygnał dropami, które zostaną
    # doręczone ZANIM zacznie się noga nowego ordera. Case A (Baanko+Rany Julek):
    # stare dropy Rukoli przeciwne → fałszywe -35. Case C: realna para przeciwna
    # (Wierzbowa↔Chrobrego) rozcieńczona już-doręczonym Sybirakowem → za słaba kara.
    # Tu: zbiór = bag drops z predicted_delivered_at >= pickup_at[new] (realnie
    # wiezione razem) + drop nowego; origin = pickup nowego ordera. <2 wektory
    # (np. Case A: brak współistniejących) → None → dispatch_pipeline: brak
    # kary/bonusu (solo noga). Stary wholebag-cosinus zachowany pod r1_wholebag_*
    # (porównanie w shadow przez okno walidacji). Flag default OFF — zero wpływu
    # gdy wyłączona (reguła kilkudniowej walidacji). Nadpisuje r1_avg_pairwise_cosine
    # / r1_new_drop_cosine → wszyscy konsumenci (bonus_r1_corridor, R-09 wave veto,
    # geo-blind path) dostają poprawioną wartość spójnie.
    if (getattr(C, "ENABLE_R1_WAVE_SCOPED_DIRECTIONALITY", False)
            or C.flag("ENABLE_R1_WAVE_SCOPED_DIRECTIONALITY", False)):
        _ws_origin = (new_order.pickup_coords
                      if _valid(new_order.pickup_coords) else courier_pos)
        _ws_new_pickup_t = plan.pickup_at.get(new_order.order_id)
        if (_valid(_ws_origin) and _ws_new_pickup_t is not None
                and _valid(new_order.delivery_coords)):
            _ws_open_drops: List[Tuple[float, float]] = []
            for _b in bag:
                _pd = plan.predicted_delivered_at.get(_b.order_id)
                if (_pd is not None and _pd >= _ws_new_pickup_t
                        and _valid(_b.delivery_coords)):
                    _ws_open_drops.append(_b.delivery_coords)
            metrics["r1ws_open_drop_count"] = len(_ws_open_drops)
            # zachowaj stare (wholebag) wartości do porównania before/after
            metrics["r1_wholebag_avg_pairwise_cosine"] = metrics.get(
                "r1_avg_pairwise_cosine")
            metrics["r1_wholebag_new_drop_cosine"] = metrics.get(
                "r1_new_drop_cosine")

            def _ws_unit(_o, _p):
                _vx = _p[0] - _o[0]
                _vy = _p[1] - _o[1]
                _n = (_vx * _vx + _vy * _vy) ** 0.5
                return (_vx / _n, _vy / _n) if _n > 1e-9 else None

            # avg pairwise cosine na {open_drops + new_drop} z origin
            _ws_dirs = [d for d in (_ws_unit(_ws_origin, _p)
                        for _p in (list(_ws_open_drops) + [new_order.delivery_coords]))
                        if d is not None]
            if len(_ws_dirs) >= 2:
                _ws_cs = 0.0
                _ws_pairs = 0
                for _i in range(len(_ws_dirs)):
                    for _j in range(_i + 1, len(_ws_dirs)):
                        _ws_cs += (_ws_dirs[_i][0] * _ws_dirs[_j][0]
                                   + _ws_dirs[_i][1] * _ws_dirs[_j][1])
                        _ws_pairs += 1
                metrics["r1_avg_pairwise_cosine"] = round(
                    _ws_cs / _ws_pairs if _ws_pairs else 0.0, 3)
            else:
                metrics["r1_avg_pairwise_cosine"] = None
            # new_drop_cosine: nowy drop vs średni kierunek open_drops z origin
            _ws_od_dirs = [d for d in (_ws_unit(_ws_origin, _p)
                           for _p in _ws_open_drops) if d is not None]
            _ws_nv = _ws_unit(_ws_origin, new_order.delivery_coords)
            if _ws_od_dirs and _ws_nv is not None:
                _ws_mx = sum(v[0] for v in _ws_od_dirs) / len(_ws_od_dirs)
                _ws_my = sum(v[1] for v in _ws_od_dirs) / len(_ws_od_dirs)
                _ws_mn = (_ws_mx * _ws_mx + _ws_my * _ws_my) ** 0.5
                if _ws_mn > 1e-9:
                    metrics["r1_new_drop_cosine"] = round(
                        (_ws_mx / _ws_mn) * _ws_nv[0]
                        + (_ws_my / _ws_mn) * _ws_nv[1], 3)
                else:
                    metrics["r1_new_drop_cosine"] = None
            else:
                metrics["r1_new_drop_cosine"] = None

    # F5 RETURN-TO-RESTAURANT (2026-05-24) — wykryj zakazany powrót do tej samej
    # restauracji niosąc jej dowóz (Case B korpusu). Metryka → kara w dispatch_pipeline.
    # Defense-in-depth (Lekcja #83): instrumentacja NIGDY nie przerywa feasibility.
    if (getattr(C, "ENABLE_R_RETURN_TO_RESTAURANT_VETO", False)
            or C.flag("ENABLE_R_RETURN_TO_RESTAURANT_VETO", False)):
        try:
            _rtr_oid = detect_return_to_restaurant(
                bag, new_order, plan,
                same_rest_km=getattr(C, "RETURN_TO_RESTAURANT_SAME_KM", 0.08),
                group_tol_min=getattr(C, "RETURN_TO_RESTAURANT_GROUP_TOL_MIN", 5.0),
            )
            metrics["return_to_restaurant_oid"] = _rtr_oid
            metrics["return_to_restaurant"] = _rtr_oid is not None
        except Exception as _rtr_e:
            log.warning(f"F5_RETURN_TO_RESTAURANT_FAIL {type(_rtr_e).__name__}: {_rtr_e}")

    # Sprint OBJ F0.3 (2026-05-17): metryki jakości planu (route_metrics) →
    # shadow_decisions (prefix objm_, whitelist serializera) + replay-capture
    # wejść solvera dla offline harnessu. Defense-in-depth (Lekcja #83):
    # instrumentacja NIGDY nie może przerwać feasibility — oba bloki try/except.
    try:
        from dispatch_v2.route_metrics import compute_plan_metrics as _cpm
        for _mk, _mv in _cpm(plan, _dwell_pickup).items():
            metrics[f"objm_{_mk}"] = _mv
    except Exception as _objm_e:
        log.warning(f"OBJ_METRICS_FAIL {type(_objm_e).__name__}: {_objm_e}")
    try:
        from dispatch_v2 import obj_replay_capture as _orc
        if origin_travel is None:
            _orc.capture(courier_pos, bag, new_order, now, _dwell_pickup,
                     _dwell_dropoff, courier_tier,
                     getattr(new_order, "order_id", None))
    except Exception as _orc_e:
        log.warning(f"OBJ_REPLAY_CAPTURE_HOOK_FAIL {type(_orc_e).__name__}: {_orc_e}")

    # Sprint OBJ FOOD-AGE SHADOW (2026-06-14): forward comparator BUG#5. Gdy flaga
    # shadow ON, plan produkcyjny = ortools multi-stop, a fix NIE jest jeszcze
    # flipnięty produkcyjnie — re-licz ten sam plan z food-age ON (thread-local
    # override, race-safe w ThreadPoolExecutor) i zaloguj rozbieżność OFF↔ON.
    # NIE zmienia decyzji (override tylko wokół re-computu). Defense-in-depth:
    # NIGDY nie przerywa feasibility (try/except). Gate ortools-multistop trzyma
    # koszt (zdublowany solve) wyłącznie tam gdzie fix może coś zmienić.
    try:
        if (C.decision_flag("ENABLE_OBJ_DELIVERY_FOOD_AGE_SHADOW")
                and not C.decision_flag("ENABLE_OBJ_DELIVERY_FOOD_AGE")
                and plan is not None and plan.strategy == "ortools"
                and plan.sequence and len(plan.sequence) >= 2):
            with C.food_age_override(True):
                _fa_plan = simulate_bag_route_v2(
                    courier_pos, bag, new_order, now=now, sla_minutes=sla_minutes,
                    base_sequence=base_sequence, earliest_departure=earliest_departure,
                    dwell_pickup=_dwell_pickup, dwell_dropoff=_dwell_dropoff,
                    drive_speed_mult=_drive_speed_mult,
                    origin_travel=origin_travel,
                )
            from dispatch_v2.route_metrics import compute_plan_metrics as _cpm_fa
            _on_m = _cpm_fa(_fa_plan, _dwell_pickup)

            def _stop_order(_p):
                # Pełna kolejność PRZYSTANKÓW (odbiory+dostawy interleaved). UWAGA:
                # plan.sequence to kolejność DOSTAW — w BUG#5 identyczna A vs B;
                # różnica jest w interleaving odbiorów → porównuj pełną kolejność.
                _ev = [(_t, "P", _o) for _o, _t in (_p.pickup_at or {}).items()]
                _ev += [(_t, "D", _o) for _o, _t in (_p.predicted_delivered_at or {}).items()]
                _ev.sort(key=lambda e: e[0])
                return [f"{_k}:{_o}" for _t, _k, _o in _ev]

            _off_order, _on_order = _stop_order(plan), _stop_order(_fa_plan)
            metrics["food_age_shadow"] = {
                "changed": _off_order != _on_order,
                "off_order": _off_order,
                "on_order": _on_order,
                "deliv_seq": list(plan.sequence),
                "on_strategy": _fa_plan.strategy,
                "off_thermal_max": metrics.get("objm_max_thermal_age_min"),
                "on_thermal_max": _on_m.get("max_thermal_age_min"),
                "off_idle": metrics.get("objm_idle_total_min"),
                "on_idle": _on_m.get("idle_total_min"),
                "off_span": metrics.get("objm_route_span_min"),
                "on_span": _on_m.get("route_span_min"),
                "off_r6_breach": metrics.get("objm_r6_breach_count"),
                "on_r6_breach": _on_m.get("r6_breach_count"),
                "off_r6_breach_max": metrics.get("objm_r6_breach_max_min"),
                "on_r6_breach_max": _on_m.get("r6_breach_max_min"),
                "off_sla_viol": plan.sla_violations,
                "on_sla_viol": _fa_plan.sla_violations,
                "coeff": getattr(C, "OBJ_DELIVERY_FOOD_AGE_COEFF", None),
            }
    except Exception as _fa_e:
        log.warning(f"FOOD_AGE_SHADOW_FAIL {type(_fa_e).__name__}: {_fa_e}")

    # Sprint OBJ F3 / BUG-5 (2026-05-18): pomiar R6 (metryki r6_*) PRZENIESIONY
    # PRZED sla-return. Pre-fix: kandydat odrzucony na plan-level sla_violations
    # robił return przed blokiem R6 → r6_max_bag_time_min / r6_bag_size /
    # r6_per_order_violations = null → _r6_pov_count (dispatch_pipeline best_effort)
    # widział 0, reason "r6_violations=0" KŁAMAŁ przy realnym 70-82 min carry
    # (diagnoza 474297). Pomiar nie ma return-ów i nie zależy od sla — bezpieczny
    # do przeniesienia. sla-CHECK i R6-hard-REJECT zostają w niezmienionej kolejności
    # (oba → NO; sla nadal pierwsze). Detal: eod_drafts/2026-05-18/obj_f3_bug5_design.md.

    # R6 (F2.1b + V3.28 ANCHOR FIX 2026-05-10) — BAG_TIME termiczny PER-ORDER hard cap.
    #
    # Doktryna Adriana 2026-05-10: 35 min jest JEDYNĄ twardą regułą, per-zlecenie.
    # Anchor selection (Lekcja #84 thermal anchor):
    #   - new_order: anchor = pickup_ready_at (real ready time z restauracji)
    #   - bag order, NOT yet picked_up: anchor = pickup_ready_at (jedzenie czeka od ready)
    #   - bag order, ALREADY picked_up: anchor = picked_up_at (real pickup), SOFT only
    #     (kurier już wiezie — nie odrzucamy z bagu, fizycznie nie ma sensu cofać)
    #
    # Dlaczego nie plan.pickup_at (jak pre-V3.28): TSP może projektować pickup later
    # niż ready_at (np. +37 min gdy kurier zajęty), maskując 70+ min real thermal.
    # Diagnoza 2026-05-10 472189: r6_max_bag_time=34 min "pass" while real thermal 70 min.
    # R-PACZKI-FLEX (2026-05-20): bypass R6 35min hard gate gdy CAŁY proponowany
    # bag (bag + new_order) składa się wyłącznie z paczek — paczki nie mają
    # termiki, nic się nie psuje. Jakakolwiek jedzeniówka w mixie → standardowy
    # 35min apply do wszystkich (jedzeniówka rządzi). Czasówka-paczka też
    # bypass R6 (paczka), czasówka rządzi tylko czasem pickupu nie delivery.
    def _is_paczka_sim(o):
        return C.is_paczka_order({
            "address_id": getattr(o, "address_id", None),
            "order_type": getattr(o, "order_type", None),
        })
    _paczki_only_mix = (
        (C.ENABLE_R_PACZKI_FLEX or C.flag("ENABLE_R_PACZKI_FLEX", False))
        and _is_paczka_sim(new_order)
        and all(_is_paczka_sim(_o) for _o in bag)
    )
    # S1 (2026-07-02): flaga JEDNEGO źródła kotwic 35-min (sla_anchor). OFF = inline
    # bajt-w-bajt; ON = te same decyzje + metryka obs `sla_anchor_source` (naruszenie
    # kotwicy READY [R6] i NOW [SLA] widoczne NIEZALEŻNIE → de-maskowanie L-TEATR-1/2).
    _sla_unified = C.flag("ENABLE_SLA_ANCHOR_UNIFIED",
                          getattr(C, "ENABLE_SLA_ANCHOR_UNIFIED", False))
    _sla_ready_breach: List[Tuple[str, float]] = []  # ready-anchor (R6) elapsed>HARD (obs)
    r6_max_bag_time = 0.0
    r6_worst_oid: Optional[str] = None
    r6_per_order_violations: List[Tuple[str, float]] = []
    r6_picked_up_violations: List[Tuple[str, float]] = []
    r6_paczka_exempt_oids: List[str] = []  # firmowe paczki wyłączone z reguły 35min (Adrian 2026-06-15)
    for o in list(bag) + [new_order]:
        pred = plan.predicted_delivered_at.get(o.order_id)
        if pred is None:
            # Lekcja #32 fail-loud: brak predicted = bug w simulator
            log.warning(
                f"R6 missing predicted_delivered_at oid={o.order_id} "
                f"bag_size={len(bag)} new_oid={new_order.order_id} — conservative skip"
            )
            continue
        if pred.tzinfo is None:
            pred = pred.replace(tzinfo=timezone.utc)
        is_new = o is new_order
        # INV-R6-ANCHOR-CONSISTENCY: wspólna kotwica termiczna R6 (1:1 z route_simulator
        # _compute_per_order_delivery_minutes). prep_bias (gate-stricter) nakładamy PO (niżej).
        anchor, anchor_src, is_picked = r6_thermal_anchor(o, is_new, plan.pickup_at, now)
        # FIRMOWE PACZKI (Adrian 2026-06-15): paczka/firmowe (Dr Tusz/tonery, Nadajesz.pl,
        # PACZKA_ADDRESS_IDS) to NIE gorące jedzenie → wyłączona z reguły 35min (R6 termik),
        # także w MIESZANYM worku. Nie ustawia r6_max/worst i nie trafia do violations.
        _o_paczka_exempt = (
            C.flag("ENABLE_PACZKA_R6_THERMAL_EXEMPT",
                   getattr(C, "ENABLE_PACZKA_R6_THERMAL_EXEMPT", False))
            and _is_paczka_sim(o))
        if _o_paczka_exempt and o.order_id not in r6_paczka_exempt_oids:
            r6_paczka_exempt_oids.append(o.order_id)
        # [C2] prep-bias anchor correction (flag ENABLE_PREP_BIAS_TABLE, default OFF).
        # Gdy kuchnia restauracji systematycznie zaniża deklarowany czas gotowości
        # (bias dodatni z prep_bias_table.json — zmierzony z czystego sygnału
        # "kurier-czekał"), przesuwamy kotwicę termiczną WCZEŚNIEJ → bag_time rośnie
        # → R6 bije wcześniej (ochrona świeżości, NIGDY bardziej liberalna; bias
        # ujemny klampowany do 0). Korygujemy TYLKO kotwice z deklarowanego ready
        # (pickup_ready_at / tsp_pickup_at); picked_up_at = realny pickup, nie estymata.
        if (anchor_src in ("pickup_ready_at", "tsp_pickup_at")
                and C.flag("ENABLE_PREP_BIAS_TABLE", False)):
            try:
                _shift_min, _bias_src = prep_bias_anchor.anchor_shift_min(
                    getattr(o, "restaurant", None))
                if _shift_min:
                    anchor = anchor + timedelta(minutes=_shift_min)
                    anchor_src = anchor_src + "+prep_bias"
                    metrics.setdefault("prep_bias_shifts", []).append({
                        "oid": o.order_id,
                        "shift_min": round(_shift_min, 2),
                        "bias_src": _bias_src,
                    })
            except Exception as _pbe:  # fail-soft: korekta nigdy nie wywala R6
                log.warning("prep_bias anchor correction failed oid=%s: %s: %s",
                            o.order_id, type(_pbe).__name__, _pbe)
        bag_time_min = (pred - anchor).total_seconds() / 60.0
        # S1 obs: surowe naruszenie kotwicy READY (>HARD dial) — widoczne niezależnie
        # od tego, czy SLA-loop niżej zrobi return wcześniej (de-maskowanie R6↔SLA).
        if _sla_unified and (not _o_paczka_exempt) and bag_time_min > C.BAG_TIME_HARD_MAX_MIN:
            _sla_ready_breach.append((o.order_id, round(bag_time_min, 1)))
        if (not _o_paczka_exempt) and bag_time_min > r6_max_bag_time:
            r6_max_bag_time = bag_time_min
            r6_worst_oid = o.order_id
        # D3-gold (Adrian 29.06 + OD-07, kod usunięty 2026-07-20): R6 = surowe 35
        # dla KAŻDEGO — dawna bramka quantile-p80 dla gold<=4 (14.06→18.07 OFF)
        # wycięta; historia: ZIOMEK_LOGIC_REFERENCE "Sprint D3-gold".
        # Per-order violation tracking (split picked-up vs not)
        # R-PACZKI-FLEX: skip tracking gdy paczki-only mix → hard reject linia
        # 693 nie aktywuje się (empty list). Soft zone niżej też respektuje.
        if bag_time_min > C.BAG_TIME_HARD_MAX_MIN and not _paczki_only_mix and not _o_paczka_exempt:
            if is_picked:
                r6_picked_up_violations.append((o.order_id, round(bag_time_min, 1)))
            else:
                r6_per_order_violations.append((o.order_id, round(bag_time_min, 1)))
    metrics["r6_max_bag_time_min"] = round(r6_max_bag_time, 1)
    metrics["r6_worst_oid"] = r6_worst_oid
    metrics["r6_is_solo"] = len(bag) == 0
    metrics["r6_bag_size"] = len(bag)
    metrics["r6_per_order_violations"] = r6_per_order_violations
    metrics["r6_picked_up_violations"] = r6_picked_up_violations
    if r6_paczka_exempt_oids:
        metrics["r6_paczka_exempt_oids"] = r6_paczka_exempt_oids
    # S1 (2026-07-02): metryka obserwabilności JEDNEGO źródła kotwic. Tylko ON (ON≠OFF).
    # ready = kotwica R6 (od gotowości), now = kotwica SLA (dostawy). now_* dopełnia
    # SLA-loop niżej (mutacja tego dictu = ta sama referencja w metrics). Auto-serializacja
    # L1.1 (deny-lista). NIE zmienia decyzji — czysta widoczność de-maskowania.
    if _sla_unified:
        metrics["sla_anchor_source"] = {
            "unified": True,
            "hard_dial_min": round(float(C.BAG_TIME_HARD_MAX_MIN), 1),
            "sla_minutes": sla_minutes,
            "ready_breach_oids": [oid for oid, _bt in _sla_ready_breach],
            "ready_breach_max_min": round(
                max([bt for _o, bt in _sla_ready_breach], default=0.0), 1),
            "now_breach_oids": [],
            "now_breach_max_min": 0.0,
        }
    # F2.2 C3 narrow (2026-04-18): R6 soft warning zone (30, 35] — metric-only.
    # R-PACZKI-FLEX: paczki-only mix bypass tej strefy (paczki bez termiki).
    # ── Z-21 (higiena 2026-06-13): RENAME r6_soft_penalty → r6_soft_penalty_c3_legacy ──
    # To pole (-3/min) jest MARTWE w produkcji: trafia tylko do scoring.score_candidate
    # kwargu r6_soft_penalty, który dodaje je do score WYŁĄCZNIE gdy
    # DEPRECATE_LEGACY_HARD_GATES=True (stała = False, nigdy nie flipnięta) — a live
    # caller (dispatch_pipeline:~2975) tego kwargu i tak NIE przekazuje. ŻYWA kara R6-soft
    # to dispatch_pipeline._r6_soft_penalty (-8/min, BAG_TIME_SOFT_PENALTY_PER_MIN) →
    # bonus_r6_soft_pen. Zmiana nazwy = tylko ujednoznacznienie (dwa różne -3 vs -8 nosiły
    # tę samą nazwę). Zero zmiany zachowania: martwa ścieżka pozostaje martwa.
    if 30.0 < r6_max_bag_time <= C.BAG_TIME_HARD_MAX_MIN and not _paczki_only_mix:
        metrics["r6_soft_penalty_c3_legacy"] = round(-3.0 * (r6_max_bag_time - 30.0), 2)
        metrics["r6_soft_zone_active"] = True
    else:
        metrics["r6_soft_penalty_c3_legacy"] = 0.0
        metrics["r6_soft_zone_active"] = False

    if plan.sla_violations > 0:
        # 2026-05-20 (diagnoza 474863 Gabryś) — SLA pre-existing bypass:
        # rozdziel violations na (a) "pre-existing" — picked_up order którego
        # plan dostarczy PRZED `plan.pickup_at[new_order]` (nowy order nie wpływa
        # na jego carry-time) vs (b) "blokujące" — new_order sam lub picked_up
        # którego dropoff plan robi PO new_order pickup (detour). Wzorzec P3-D4.
        violations_detail = []
        violations_pre_existing: List[dict] = []
        new_pickup_at_utc = plan.pickup_at.get(new_order.order_id)
        if new_pickup_at_utc is not None and new_pickup_at_utc.tzinfo is None:
            new_pickup_at_utc = new_pickup_at_utc.replace(tzinfo=timezone.utc)
        for o in list(bag) + [new_order]:
            pred = plan.predicted_delivered_at.get(o.order_id)
            if pred is None:
                continue
            # FIRMOWE PACZKI (Adrian 2026-06-15): paczka nie podlega regule 35min
            # także w bramce SLA — pomiń jako violation (spójnie z R6 termik exempt).
            if (C.flag("ENABLE_PACZKA_R6_THERMAL_EXEMPT",
                       getattr(C, "ENABLE_PACZKA_R6_THERMAL_EXEMPT", False))
                    and _is_paczka_sim(o)):
                continue
            _sla_ready_gate = C.flag("ENABLE_SLA_GATE_READY_ANCHOR",
                                     getattr(C, "ENABLE_SLA_GATE_READY_ANCHOR", False))
            if _sla_unified:
                from dispatch_v2 import sla_anchor as _SA
                if _sla_ready_gate:
                    # Krok 2 (2026-07-02, finding feas-r6-sla-anchor-gap): kotwica SLA
                    # NOW→READY (od gotowości) — bliźniak z _count_sla_violations, przez
                    # źródło sla_anchor kind='ready'. OFF = NOW-anchor (S1 bez zmian).
                    pu = _SA.anchor(o, kind="ready", now=now, plan_pickup_at=plan.pickup_at,
                                    is_new=(o is new_order))
                else:
                    pu = _SA.now_anchor(o, plan.pickup_at, now)
                elapsed_min = _SA.elapsed_min(pred, pu)
            else:
                if o.order_id in plan.pickup_at:
                    pu = plan.pickup_at[o.order_id]
                elif o.picked_up_at is not None:
                    pu = o.picked_up_at
                    if pu.tzinfo is None:
                        pu = pu.replace(tzinfo=timezone.utc)
                    pu = pu.astimezone(timezone.utc)
                else:
                    pu = now
                elapsed_min = (pred - pu).total_seconds() / 60.0
            # D3-gold (kod usunięty 2026-07-20): dawna kalibracja co-design gold≤4
            # przy ready-anchored SLA-gate wycięta razem z bramką R6-quantile
            # (OD-07: żaden wyjątek klasowy; historia w ZIOMEK_LOGIC_REFERENCE).
            if elapsed_min > sla_minutes:
                vd = {
                    "order_id": o.order_id,
                    "elapsed_min": round(elapsed_min, 1),
                    "over_sla_by_min": round(elapsed_min - sla_minutes, 1),
                }
                violations_detail.append(vd)
                # Pre-existing classification: picked_up order, drop PRZED new pickup
                is_picked_o = (o is not new_order) and (
                    getattr(o, "picked_up_at", None) is not None
                    or getattr(o, "status", None) == "picked_up"
                )
                if is_picked_o and new_pickup_at_utc is not None:
                    pred_utc = pred if pred.tzinfo else pred.replace(tzinfo=timezone.utc)
                    if pred_utc <= new_pickup_at_utc:
                        violations_pre_existing.append(vd)
        metrics["sla_violations"] = violations_detail
        # S1 obs: naruszenie kotwicy NOW (SLA) — niezależne od R6 ready-breach powyżej.
        if _sla_unified and isinstance(metrics.get("sla_anchor_source"), dict):
            metrics["sla_anchor_source"]["now_breach_oids"] = [
                v["order_id"] for v in violations_detail]
            metrics["sla_anchor_source"]["now_breach_max_min"] = round(
                max([v["elapsed_min"] for v in violations_detail], default=0.0), 1)
        metrics["sla_violations_pre_existing"] = violations_pre_existing
        n_blocking = len(violations_detail) - len(violations_pre_existing)
        metrics["sla_violations_blocking_count"] = n_blocking
        # Bypass tylko gdy WSZYSTKIE violations są pre-existing (kurier i tak je
        # ma niezależnie od nowego ordera). New-induced lub new_order sam → reject.
        if not violations_detail:
            # FIRMOWE PACZKI (Adrian 2026-06-15): wszystkie SLA-violations to paczki
            # (exempt) → brak realnej blokady; nie odrzucaj (max() na pustej = crash).
            pass
        elif (C.ENABLE_SLA_PREEXISTING_BYPASS
                and len(violations_detail) > 0
                and n_blocking == 0):
            log.info(
                f"SLA_PREEXISTING_BYPASS oid={new_order.order_id} "
                f"pre_existing={[v['order_id'] for v in violations_pre_existing]} "
                f"(plan dostarcza picked_up przed new pickup, no detour) — "
                f"continue feasibility"
            )
            # Nie return — niech P3-D4 / per-order R6 dalej oceniają
        else:
            worst = max(violations_detail, key=lambda v: v["over_sla_by_min"])
            return (
                "NO",
                f"sla_violation ({worst['order_id']} +{worst['elapsed_min']}min, over by {worst['over_sla_by_min']})",
                metrics,
                plan,
            )
    # V3.28 ANCHOR FIX: hard reject TYLKO za assigned-but-not-picked + new_order >35.
    # Picked_up orders są tracked ale NIE rejected (kurier kończy w drodze).
    if r6_per_order_violations:
        worst_oid, worst_bt = max(r6_per_order_violations, key=lambda v: v[1])
        if C.flag("ENABLE_R6_BREACH_SHADOW_LOG", False):
            _emit_r6_breach_shadow(new_order, worst_oid, worst_bt, r6_per_order_violations, metrics,
                                   bag_total=len(bag) + 1, now=now, tier=courier_tier)
        return (
            "NO",
            f"R6_per_order_>35min ({worst_oid} {worst_bt:.1f}min, "
            f"thermal anchor=ready_at; n_violations={len(r6_per_order_violations)})",
            metrics,
            plan,
        )

    # P3-D4 2026-05-11: picked_up R6 delta-based reject (Boboli 44 min case 10.05).
    # Adrian doktryna NEW 10.05 wieczór: picked_up tracking-only za luźna — gdy
    # nowy order CAUSES delay dla picked_up violation, reject. Heurystyka delta:
    # new pickup happening BEFORE picked_up delivery = detour → carry time wzrasta.
    # Jeśli new pickup po picked_up delivery, no impact (picked_up dostarczony pierwszy).
    metrics["r6_picked_up_delta_reject"] = False
    if r6_picked_up_violations:
        new_pickup_at = plan.pickup_at.get(new_order.order_id)
        if new_pickup_at is not None:
            if new_pickup_at.tzinfo is None:
                new_pickup_at = new_pickup_at.replace(tzinfo=timezone.utc)
            for pu_oid, pu_bt in r6_picked_up_violations:
                pu_pred = plan.predicted_delivered_at.get(pu_oid)
                if pu_pred is None:
                    continue
                if pu_pred.tzinfo is None:
                    pu_pred = pu_pred.replace(tzinfo=timezone.utc)
                if pu_pred > new_pickup_at:
                    # New pickup detour delays this picked_up delivery
                    metrics["r6_picked_up_delta_reject"] = True
                    return (
                        "NO",
                        f"R6_picked_up_delta_>35min ({pu_oid} {pu_bt:.1f}min; "
                        f"new pickup delays carry, n_picked_up_v={len(r6_picked_up_violations)})",
                        metrics,
                        plan,
                    )

    # V3.24-A: hard reject gdy planned dropoff nowego ordera > shift_end +
    # V324_HARD_REJECT_DROPOFF_AFTER_SHIFT_MIN (default 5 min). Precyzyjniejsze
    # niż legacy SHIFT_END_BUFFER_MIN=20 (który zbyt gruby — odrzucał kurierów
    # którzy zdążyliby solo order 3-min przed shift_end).
    if C.ENABLE_V324A_SCHEDULE_INTEGRATION and shift_end is not None:
        pred_new = plan.predicted_delivered_at.get(new_order.order_id)
        if pred_new is not None:
            if pred_new.tzinfo is None:
                pred_new = pred_new.replace(tzinfo=timezone.utc)
            excess_min = (pred_new - shift_end).total_seconds() / 60.0
            metrics["v324a_planned_dropoff_iso"] = pred_new.isoformat()
            metrics["v324a_dropoff_excess_min"] = round(excess_min, 2)
            if excess_min > C.V324_HARD_REJECT_DROPOFF_AFTER_SHIFT_MIN:
                # END-OF-DAY SALVAGE: w ostatniej godzinie pracy firmy dostawa MOŻE wyjść po
                # końcu zmiany kuriera (warunek to odbiór przed końcem dnia, nie dostawa) →
                # nie odrzucaj na tej podstawie. Odbiór-przed-close pilnuje Gate 2 wyżej.
                _salv_do, _ = _end_of_day_salvage(now)
                if not _salv_do:
                    return (
                        "NO",
                        f"v324a_dropoff_after_shift (dropoff {pred_new.strftime('%H:%M')} "
                        f"vs shift_end {shift_end.strftime('%H:%M')}, excess +{excess_min:.1f}min)",
                        metrics,
                        plan,
                    )
                metrics["end_of_day_salvage"] = True
                metrics["end_of_day_salvage_dropoff_excess_min"] = round(excess_min, 2)

    # F2.2 C2 — per-order 35min hard gate (shadow mode by default).
    # Current verdict at this point is MAYBE (survived all other gates).
    # check_per_order_35min_rule uses plan.per_order_delivery_times (C1 field).
    c2_passes, c2_details = check_per_order_35min_rule(plan)
    metrics["c2_passes"] = c2_passes
    metrics["c2_max_elapsed_min"] = c2_details["max_elapsed"]
    metrics["c2_violations_count"] = len(c2_details["violations"])
    metrics["c2_per_order_data_available"] = c2_details["per_order_data_available"]

    if ENABLE_C2_SHADOW_LOG and not c2_passes:
        _emit_c2_shadow_diff_event(
            current_verdict="MAYBE",
            c2_passes=c2_passes,
            c2_details=c2_details,
            plan=plan,
            metrics=metrics,
            new_order_id=new_order.order_id,
            bag_size_before=metrics.get("bag_size_before", 0),
        )

    if USE_PER_ORDER_GATE and not c2_passes:
        worst_oid, worst_elapsed = max(c2_details["violations"], key=lambda v: v[1]) \
            if c2_details["violations"] else ("?", c2_details["max_elapsed"])
        return (
            "NO",
            f"C2_per_order_35min_exceeded ({worst_oid} {worst_elapsed:.1f}min>{C2_PER_ORDER_THRESHOLD_MIN})",
            metrics,
            plan,
        )

    return ("MAYBE", "ok_sla_fits", metrics, plan)
