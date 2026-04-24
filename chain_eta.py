"""V3.26 STEP 6 (R-07 v2): Chain-ETA Engine.

Adrian's model (Q&A 2026-04-24): 'Ziomek liczy trasę od czasu kiedy ma ustalone
tam być'. Per pickup w bagu, effective_time = max(realistic_arrival, scheduled).
Chain walk przez unpicked orders → final effective_eta dla proposal candidate.

Replaces naive `drive_min = osrm(courier.pos → proposal.pickup)` direct calc.
Root cause fix dla case oid=467919 (Szymon Sa synthetic pos treated as real).

Pure function z injected deps (osrm_drive_min, haversine_km) — testable.
Flag-gated use: common.ENABLE_V326_R07_CHAIN_ETA. Shadow metrics ALWAYS recorded.

MVP (Opcja B per Adrian ACK 2026-04-24 08:50):
- Status filter: 'picked_up'/'delivered'/'cancelled'/'returned_to_pool' → SKIP
- 'assigned'/'planned'/default → Case 4 standard (scheduled + buffer jeśli late)
- V3.27 R-07 Extension: panel_status granularity (status 4 waiting_at_restaurant
  → effective=now, status 6 delay → scheduled+10min) — deferred po 7+ dni Q&A.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
import logging

from dispatch_v2 import common as C

log = logging.getLogger("chain_eta")

BIALYSTOK_CENTER = (53.1325, 23.1688)

# Statuses które traktujemy jako "juz poza chain" — skip z unpicked
_SKIP_STATUSES = frozenset({'picked_up', 'delivered', 'cancelled', 'returned_to_pool'})


@dataclass
class ChainETAResult:
    effective_eta_utc: datetime
    starting_point: str
    chain_details: List[Dict[str, Any]] = field(default_factory=list)
    total_chain_min: float = 0.0
    delta_vs_naive_min: float = 0.0
    warnings: List[str] = field(default_factory=list)
    truncated_count: int = 0


def compute_chain_eta(
    courier_pos: Optional[Tuple[float, float]],
    pos_source: Optional[str],
    pos_age_min: Optional[float],
    bag_orders: List[Any],
    proposal_pickup_coords: Tuple[float, float],
    proposal_scheduled_utc: Optional[datetime],
    now_utc: datetime,
    osrm_drive_min: Callable[[Tuple[float, float], Tuple[float, float]], Optional[float]],
    haversine_km: Callable[[Tuple[float, float], Tuple[float, float]], float],
    speed_multiplier: float = 1.0,
    default_prep_min: Optional[int] = None,
) -> ChainETAResult:
    """Compute chain ETA dla proposal candidate.

    Args:
        courier_pos: (lat, lon) lub None.
        pos_source: 'gps'|'last_assigned_pickup'|'last_picked_up_delivery'|'no_gps'|'post_wave'|None.
        pos_age_min: wiek GPS (lower = fresher).
        bag_orders: lista z atrybutami status, pickup_coords, pickup_ready_at, order_id.
        proposal_pickup_coords: new order pickup coords.
        proposal_scheduled_utc: new order pickup_ready_at (UTC aware).
        now_utc: decision_ts.
        osrm_drive_min: callable (from_ll, to_ll) → min lub None na error/timeout.
        haversine_km: callable (from_ll, to_ll) → km (fallback).
        speed_multiplier: R-05 tier multiplier (1.0 std, 0.889 gold, 1.111 slow, 1.3 new).
        default_prep_min: fallback gdy proposal_scheduled=None (default z common.V326_R07_DEFAULT_PREP_MIN).

    Returns ChainETAResult.
    """
    if default_prep_min is None:
        default_prep_min = int(getattr(C, 'V326_R07_DEFAULT_PREP_MIN', 30))
    fresh_gps_max_age = float(getattr(C, 'V326_R07_FRESH_GPS_MAX_AGE_MIN', 2))
    pickup_dur = float(getattr(C, 'V326_R07_PICKUP_DURATION_MIN', 2))
    no_gps_buf = float(getattr(C, 'V326_R07_NO_GPS_BUFFER_MIN', 5))
    hav_mult = float(getattr(C, 'V326_R07_HAVERSINE_ROAD_MULT', 2.5))

    warnings: List[str] = []

    if now_utc.tzinfo is None:
        now_utc = now_utc.replace(tzinfo=timezone.utc)

    def safe_drive(from_ll, to_ll):
        """OSRM drive_min z haversine × mult fallback. Zwraca float min."""
        if from_ll is None or to_ll is None:
            return 0.0
        try:
            t = osrm_drive_min(from_ll, to_ll)
            if t is not None and t >= 0:
                return float(t) * speed_multiplier
            warnings.append("OSRM returned None/negative, fallback haversine")
        except Exception as e:
            warnings.append(f"OSRM error {type(e).__name__}, fallback haversine")
        try:
            hv = haversine_km(from_ll, to_ll)
            return float(hv) * hav_mult * speed_multiplier
        except Exception as e2:
            warnings.append(f"haversine fallback failed: {type(e2).__name__}")
            return 0.0

    # Proposal scheduled fallback
    if proposal_scheduled_utc is None:
        proposal_scheduled_utc = now_utc + timedelta(minutes=default_prep_min)
        warnings.append(f"proposal.scheduled=None, fallback now+{default_prep_min}min")
    elif proposal_scheduled_utc.tzinfo is None:
        proposal_scheduled_utc = proposal_scheduled_utc.replace(tzinfo=timezone.utc)

    # KROK 1: unpicked filter (MVP Opcja B — status string based)
    unpicked = [o for o in (bag_orders or [])
                if getattr(o, 'status', None) not in _SKIP_STATUSES]

    # Naive reference
    naive_pos = courier_pos if courier_pos is not None else BIALYSTOK_CENTER
    naive_drive = safe_drive(naive_pos, proposal_pickup_coords)
    naive_eta = now_utc + timedelta(minutes=naive_drive)
    naive_total_min = (naive_eta - now_utc).total_seconds() / 60.0

    chain_details: List[Dict[str, Any]] = []

    def fresh_gps():
        return (pos_age_min is not None and pos_age_min <= fresh_gps_max_age
                and pos_source == 'gps')

    # KROK 2: starting point
    if not unpicked:
        # Case A: empty bag / all picked_up already
        if fresh_gps():
            start_pos = courier_pos
            starting_point = 'gps'
        elif courier_pos is not None:
            start_pos = courier_pos
            starting_point = 'last_known_fallback'
        else:
            start_pos = BIALYSTOK_CENTER
            starting_point = 'empty_bag_center'
            warnings.append("no position data, fallback BIALYSTOK_CENTER")
        drive_to_proposal = safe_drive(start_pos, proposal_pickup_coords)
        arrival = now_utc + timedelta(minutes=drive_to_proposal)
        effective_eta = max(arrival, proposal_scheduled_utc)
        total_chain_min = (effective_eta - now_utc).total_seconds() / 60.0
        delta = total_chain_min - naive_total_min
        return ChainETAResult(
            effective_eta_utc=effective_eta,
            starting_point=starting_point,
            chain_details=[],
            total_chain_min=total_chain_min,
            delta_vs_naive_min=delta,
            warnings=warnings,
            truncated_count=0,
        )

    # Case B: chain walk
    first = unpicked[0]
    first_pu = getattr(first, 'pickup_coords', None)
    first_scheduled = getattr(first, 'pickup_ready_at', None)
    if first_scheduled is None:
        first_scheduled = now_utc + timedelta(minutes=default_prep_min)
        warnings.append(f"bag[0].scheduled=None, fallback now+{default_prep_min}min")
    elif first_scheduled.tzinfo is None:
        first_scheduled = first_scheduled.replace(tzinfo=timezone.utc)

    if fresh_gps():
        drive_to_first = safe_drive(courier_pos, first_pu)
        realistic_arrival = now_utc + timedelta(minutes=drive_to_first)
        effective_time = max(realistic_arrival, first_scheduled)
        source = 'gps'
        starting_point = 'gps'
    else:
        # Case 4/5 — no fresh GPS
        if now_utc > first_scheduled:
            effective_time = first_scheduled + timedelta(minutes=no_gps_buf)
            source = 'no_gps_buffer'
            starting_point = 'no_gps_buffer'
            log.info(
                f"R-07 no_gps_late scheduled={first_scheduled.isoformat()} "
                f"+{no_gps_buf}min buffer (pos_source={pos_source})"
            )
        else:
            effective_time = first_scheduled
            source = 'scheduled'
            starting_point = 'scheduled'

    chain_details.append({
        'order_id': str(getattr(first, 'order_id', '?')),
        'effective_time_utc': effective_time.isoformat(),
        'source': source,
        'drive_min_to': None,  # first has no "from"
    })
    prev_coords = first_pu

    # KROK 3: middle unpicked
    for order in unpicked[1:]:
        leaving = effective_time + timedelta(minutes=pickup_dur)
        next_pu = getattr(order, 'pickup_coords', None)
        drive_next = safe_drive(prev_coords, next_pu)
        arrival_next = leaving + timedelta(minutes=drive_next)
        sched = getattr(order, 'pickup_ready_at', None)
        if sched is None:
            sched = now_utc + timedelta(minutes=default_prep_min)
            warnings.append("bag[mid].scheduled=None, fallback")
        elif sched.tzinfo is None:
            sched = sched.replace(tzinfo=timezone.utc)
        new_eff = max(arrival_next, sched)
        src = 'chain' if arrival_next >= sched else 'scheduled'
        chain_details.append({
            'order_id': str(getattr(order, 'order_id', '?')),
            'effective_time_utc': new_eff.isoformat(),
            'source': src,
            'drive_min_to': round(drive_next, 2),
        })
        effective_time = new_eff
        prev_coords = next_pu

    # KROK 4: final hop to proposal
    leaving = effective_time + timedelta(minutes=pickup_dur)
    drive_proposal = safe_drive(prev_coords, proposal_pickup_coords)
    proposal_arrival = leaving + timedelta(minutes=drive_proposal)
    effective_eta = max(proposal_arrival, proposal_scheduled_utc)
    chain_details.append({
        'order_id': '__proposal__',
        'effective_time_utc': effective_eta.isoformat(),
        'source': 'chain' if proposal_arrival >= proposal_scheduled_utc else 'scheduled',
        'drive_min_to': round(drive_proposal, 2),
    })

    # KROK 5: delta + truncation
    total_chain_min = (effective_eta - now_utc).total_seconds() / 60.0
    delta = total_chain_min - naive_total_min

    truncated_count = 0
    if len(chain_details) > 5:
        truncated_count = len(chain_details) - 5
        chain_details = chain_details[:5]
        log.warning(
            f"R-07 chain_details truncated from {truncated_count + 5} to 5 entries "
            f"(pos_source={pos_source}, unpicked={len(unpicked)})"
        )

    return ChainETAResult(
        effective_eta_utc=effective_eta,
        starting_point=starting_point,
        chain_details=chain_details,
        total_chain_min=total_chain_min,
        delta_vs_naive_min=delta,
        warnings=warnings,
        truncated_count=truncated_count,
    )
