"""dispatch_pipeline - per-order assessment: feasibility → scoring → rank → verdict.

Input:  NEW_ORDER event dict + fleet snapshot + restaurant_meta.
Output: PipelineResult with ranked candidates and final verdict.

Verdicts:
    PROPOSE — best candidate is feasible, send to Telegram for approval
    KOORD   — early-bird (>=60 min ahead) OR R28 best_effort (no feasible, SLA compromise)
    SKIP    — no candidate with any plan (fleet empty / all fast-filter rejections).
              R29 says never hang; SKIP always alerts Adrian.
"""
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import List, Dict, Optional, Tuple, Any

from dispatch_v2.route_simulator_v2 import OrderSim, RoutePlanV2
from dispatch_v2.feasibility_v2 import check_feasibility_v2
from dispatch_v2 import scoring
from dispatch_v2.common import parse_panel_timestamp, WARSAW


EARLY_BIRD_THRESHOLD_MIN = 60
TOP_N_CANDIDATES = 5
DEFAULT_FLEET_PREP_VARIANCE_MIN = 13.0


@dataclass
class Candidate:
    courier_id: str
    name: Optional[str]
    score: float
    feasibility_verdict: str  # "MAYBE" | "NO"
    feasibility_reason: str
    plan: Optional[RoutePlanV2]
    metrics: Dict[str, Any] = field(default_factory=dict)
    best_effort: bool = False


@dataclass
class PipelineResult:
    order_id: str
    verdict: str  # "PROPOSE" | "KOORD" | "SKIP"
    reason: str
    best: Optional[Candidate]
    candidates: List[Candidate]
    pickup_ready_at: Optional[datetime]
    restaurant: Optional[str]


def get_pickup_ready_at(
    restaurant_name: Optional[str],
    pickup_at: Optional[datetime],
    now: datetime,
    meta: Optional[dict],
) -> Optional[datetime]:
    """Effective pickup-ready time per CLAUDE.md V3.4 (P0.7 restaurant_meta)."""
    if pickup_at is None:
        return None
    if pickup_at.tzinfo is None:
        pickup_at = pickup_at.replace(tzinfo=WARSAW)
    pickup_utc = pickup_at.astimezone(timezone.utc)

    pv = DEFAULT_FLEET_PREP_VARIANCE_MIN
    if meta is not None:
        fleet_median = meta.get("fleet_medians", {}).get(
            "fleet_prep_variance_median", DEFAULT_FLEET_PREP_VARIANCE_MIN
        )
        pv = fleet_median
        r = (meta.get("restaurants") or {}).get(restaurant_name or "")
        if r is not None:
            if r.get("flags", {}).get("low_confidence", False):
                pv = r.get("prep_variance_fallback_min", fleet_median)
            else:
                pv = (r.get("prep_variance_min") or {}).get("median", fleet_median)

    ready = pickup_utc + timedelta(minutes=pv)
    return max(now, ready)


def _bag_dict_to_ordersim(d: dict) -> OrderSim:
    picked = parse_panel_timestamp(d.get("picked_up_at"))
    status = d.get("status", "assigned")
    pickup_c = d.get("pickup_coords") or (0.0, 0.0)
    deliv_c = d.get("delivery_coords") or (0.0, 0.0)
    return OrderSim(
        order_id=str(d.get("order_id") or d.get("id") or ""),
        pickup_coords=tuple(pickup_c),
        delivery_coords=tuple(deliv_c),
        picked_up_at=picked,
        status="picked_up" if status == "picked_up" else "assigned",
    )


def _oldest_in_bag_min(bag: List[OrderSim], now: datetime) -> Optional[float]:
    ages: List[float] = []
    for o in bag:
        if o.picked_up_at is None:
            continue
        pu = o.picked_up_at
        if pu.tzinfo is None:
            pu = pu.replace(tzinfo=timezone.utc)
        ages.append((now - pu.astimezone(timezone.utc)).total_seconds() / 60.0)
    return max(ages) if ages else None


def assess_order(
    order_event: dict,
    fleet_snapshot: Dict[str, Any],
    restaurant_meta: Optional[dict] = None,
    now: Optional[datetime] = None,
) -> PipelineResult:
    if now is None:
        now = datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    order_id = str(order_event.get("order_id") or "")
    restaurant = order_event.get("restaurant")
    pickup_coords = tuple(order_event.get("pickup_coords") or (0.0, 0.0))
    delivery_coords = tuple(order_event.get("delivery_coords") or (0.0, 0.0))

    pickup_at_raw = order_event.get("pickup_at_warsaw") or order_event.get("pickup_at")
    pickup_at = parse_panel_timestamp(pickup_at_raw) if pickup_at_raw else None

    # Early bird → KOORD
    if pickup_at is not None:
        pu = pickup_at if pickup_at.tzinfo else pickup_at.replace(tzinfo=WARSAW)
        minutes_ahead = (pu.astimezone(timezone.utc) - now).total_seconds() / 60.0
        if minutes_ahead >= EARLY_BIRD_THRESHOLD_MIN:
            return PipelineResult(
                order_id=order_id,
                verdict="KOORD",
                reason=f"early_bird ({minutes_ahead:.0f} min ahead)",
                best=None,
                candidates=[],
                pickup_ready_at=None,
                restaurant=restaurant,
            )

    pickup_ready_at = get_pickup_ready_at(restaurant, pickup_at, now, restaurant_meta)

    new_order = OrderSim(
        order_id=order_id,
        pickup_coords=pickup_coords,
        delivery_coords=delivery_coords,
        status="assigned",
        pickup_ready_at=pickup_ready_at,
    )

    candidates: List[Candidate] = []
    for cid, cs in fleet_snapshot.items():
        courier_pos = getattr(cs, "pos", None)
        if courier_pos is None:
            continue
        bag_raw = getattr(cs, "bag", []) or []
        bag_sim = [_bag_dict_to_ordersim(b) for b in bag_raw]

        verdict, reason, metrics, plan = check_feasibility_v2(
            courier_pos=tuple(courier_pos),
            bag=bag_sim,
            new_order=new_order,
            shift_end=getattr(cs, "shift_end", None),
            now=now,
        )

        bag_drop_coords = [b.delivery_coords for b in bag_sim]
        oldest = _oldest_in_bag_min(bag_sim, now)
        score_result = scoring.score_candidate(
            courier_pos=tuple(courier_pos),
            restaurant_pos=pickup_coords,
            bag_drop_coords=bag_drop_coords or None,
            bag_size=len(bag_sim),
            oldest_in_bag_min=oldest,
        )

        candidates.append(Candidate(
            courier_id=str(cid),
            name=getattr(cs, "name", None),
            score=score_result["total"],
            feasibility_verdict=verdict,
            feasibility_reason=reason,
            plan=plan,
            metrics={**metrics, "score": score_result},
        ))

    # Feasible (MAYBE) → rank by score
    feasible = [c for c in candidates if c.feasibility_verdict == "MAYBE"]
    feasible.sort(key=lambda c: c.score, reverse=True)

    if feasible:
        top = feasible[:TOP_N_CANDIDATES]
        return PipelineResult(
            order_id=order_id,
            verdict="PROPOSE",
            reason=f"feasible={len(feasible)} best={top[0].courier_id}",
            best=top[0],
            candidates=top,
            pickup_ready_at=pickup_ready_at,
            restaurant=restaurant,
        )

    # R28 best_effort: NO candidates that still produced a plan (SLA-only rejections)
    with_plan = [c for c in candidates if c.plan is not None]
    with_plan.sort(key=lambda c: (c.plan.sla_violations, c.plan.total_duration_min))
    if with_plan:
        best = with_plan[0]
        best.best_effort = True
        return PipelineResult(
            order_id=order_id,
            verdict="KOORD",
            reason=f"best_effort (0 feasible, best_violations={best.plan.sla_violations})",
            best=best,
            candidates=with_plan[:TOP_N_CANDIDATES],
            pickup_ready_at=pickup_ready_at,
            restaurant=restaurant,
        )

    # R29 fallback: nothing landed — alert Adrian
    return PipelineResult(
        order_id=order_id,
        verdict="SKIP",
        reason=f"no_candidates (fleet_n={len(candidates)}) — ALERT ADRIAN",
        best=None,
        candidates=candidates,
        pickup_ready_at=pickup_ready_at,
        restaurant=restaurant,
    )
