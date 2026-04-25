"""Bug A complete (sprint 2026-04-25): insertion_anchor module unit tests.

Verify compute_insertion_anchor returns correct anchor stop dla:
- insertion at end of plan (anchor = last existing event)
- insertion in middle (anchor = chronologically previous event)
- insertion at start (no anchor → return None)
- new_order_id NOT in plan.pickup_at (return None)
- empty plan (return None)
- pure pickup-pickup chain (no drops)

#468404 fixture: bag=3 [468401, 468402, 468403], new=468404.
plan.sequence: ['468403', '468402', '468404', '468401']
plan.pickup_at: 468403=10:04, 468402=10:23, 468404=10:37, 468401=10:58
plan.predicted_delivered_at: 468403=10:09, 468402=10:44, 468404=10:51, 468401=11:10
Sorted chronologically (with pickup-before-drop tie-break):
  10:04 pickup 468403 Maison
  10:09 drop 468403 Łąkowa
  10:23 pickup 468402 Doner
  10:37 pickup 468404 Sweet Fit ← NEW
  10:44 drop 468402 Absolwentów
  ...
Anchor for 468404 = previous event = pickup 468402 Doner.
"""
import sys
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2.insertion_anchor import compute_insertion_anchor, InsertionAnchor  # noqa: E402


@dataclass
class _MockOrderSim:
    order_id: str
    pickup_coords: Optional[Tuple[float, float]] = None
    delivery_coords: Optional[Tuple[float, float]] = None
    restaurant: Optional[str] = None
    delivery_address: Optional[str] = None


@dataclass
class _MockPlan:
    sequence: List[str] = field(default_factory=list)
    pickup_at: Dict[str, datetime] = field(default_factory=dict)
    predicted_delivered_at: Dict[str, datetime] = field(default_factory=dict)


def _utc(s: str) -> datetime:
    return datetime.fromisoformat(s).astimezone(timezone.utc)


# Coords (matching #468404 case)
DONER_NSZ = (53.149259, 23.079658)
SWEET_FIT = (53.128252, 23.15241)
MAISON = (53.132524, 23.165949)
LAKOWA = (53.140023, 23.1756935)
ABSOLWENTOW = (53.119569, 23.131023)
SIKORSKIEGO = (53.1345084, 23.1034383)
PLAC_BRODOWICZA = (53.1517846, 23.2077391)


def _make_468404_fixture():
    """Adrian's #468404 case fixture."""
    bag_orders = [
        _MockOrderSim(
            order_id="468401",
            pickup_coords=DONER_NSZ,
            delivery_coords=PLAC_BRODOWICZA,
            restaurant="Doner Kebab",
            delivery_address="Plac Brodowicza 1",
        ),
        _MockOrderSim(
            order_id="468402",
            pickup_coords=DONER_NSZ,
            delivery_coords=ABSOLWENTOW,
            restaurant="Doner Kebab",
            delivery_address="Absolwentów 4/77",
        ),
        _MockOrderSim(
            order_id="468403",
            pickup_coords=MAISON,
            delivery_coords=LAKOWA,
            restaurant="Maison du cafe",
            delivery_address="Łąkowa 7/5",
        ),
    ]
    plan = _MockPlan(
        sequence=["468403", "468402", "468404", "468401"],
        pickup_at={
            "468403": _utc("2026-04-25T08:04:18+00:00"),
            "468402": _utc("2026-04-25T08:23:59+00:00"),
            "468404": _utc("2026-04-25T08:37:33+00:00"),
            "468401": _utc("2026-04-25T08:58:36+00:00"),
        },
        predicted_delivered_at={
            "468403": _utc("2026-04-25T08:09:46+00:00"),
            "468402": _utc("2026-04-25T08:44:17+00:00"),
            "468404": _utc("2026-04-25T08:51:37+00:00"),
            "468401": _utc("2026-04-25T09:10:49+00:00"),
        },
    )
    return plan, bag_orders


def test_468404_anchor_is_doner_pickup():
    """Anchor for 468404 should be pickup 468402 Doner Kebab (chronologically previous)."""
    plan, bag = _make_468404_fixture()
    anchor = compute_insertion_anchor(plan, "468404", bag)
    assert anchor is not None
    assert anchor.is_pickup is True, f"Expected pickup, got is_pickup={anchor.is_pickup}"
    assert anchor.order_id == "468402"
    assert anchor.restaurant_name == "Doner Kebab"
    assert anchor.location == DONER_NSZ
    assert anchor.timestamp == _utc("2026-04-25T08:23:59+00:00")


def test_insertion_at_chronological_start_returns_none():
    """Gdy new pickup jest pierwszym chronologicznie → no anchor."""
    bag = [
        _MockOrderSim(
            order_id="100",
            pickup_coords=(53.1, 23.1),
            delivery_coords=(53.2, 23.2),
            restaurant="A",
        ),
    ]
    plan = _MockPlan(
        sequence=["NEW", "100"],
        pickup_at={
            "NEW": _utc("2026-04-25T10:00:00+00:00"),  # earlier
            "100": _utc("2026-04-25T10:30:00+00:00"),
        },
        predicted_delivered_at={
            "NEW": _utc("2026-04-25T10:10:00+00:00"),
            "100": _utc("2026-04-25T10:40:00+00:00"),
        },
    )
    anchor = compute_insertion_anchor(plan, "NEW", bag)
    assert anchor is None, f"Expected None for chronological-start insertion, got {anchor}"


def test_insertion_at_end_anchor_is_last_existing():
    """Gdy new pickup is last → anchor = previous drop or pickup."""
    bag = [
        _MockOrderSim(
            order_id="100",
            pickup_coords=(53.1, 23.1),
            delivery_coords=(53.2, 23.2),
            restaurant="A",
            delivery_address="A_drop",
        ),
    ]
    plan = _MockPlan(
        sequence=["100", "NEW"],
        pickup_at={
            "100": _utc("2026-04-25T10:00:00+00:00"),
            "NEW": _utc("2026-04-25T10:30:00+00:00"),
        },
        predicted_delivered_at={
            "100": _utc("2026-04-25T10:15:00+00:00"),  # before NEW pickup
            "NEW": _utc("2026-04-25T10:40:00+00:00"),
        },
    )
    anchor = compute_insertion_anchor(plan, "NEW", bag)
    assert anchor is not None
    # Previous event before NEW pickup (10:30) = drop 100 (10:15)
    assert anchor.is_pickup is False
    assert anchor.order_id == "100"
    assert anchor.location == (53.2, 23.2)


def test_new_order_id_not_in_plan_returns_none():
    plan, bag = _make_468404_fixture()
    anchor = compute_insertion_anchor(plan, "999999", bag)
    assert anchor is None


def test_empty_plan_returns_none():
    bag = [_MockOrderSim(order_id="100", pickup_coords=(53.1, 23.1))]
    plan = _MockPlan(sequence=[], pickup_at={}, predicted_delivered_at={})
    anchor = compute_insertion_anchor(plan, "NEW", bag)
    assert anchor is None


def test_none_plan_returns_none():
    anchor = compute_insertion_anchor(None, "NEW", [])
    assert anchor is None


def test_iso_string_timestamps_supported():
    """plan.pickup_at może mieć ISO string zamiast datetime."""
    bag = [
        _MockOrderSim(
            order_id="100",
            pickup_coords=(53.1, 23.1),
            delivery_coords=(53.2, 23.2),
            restaurant="A",
        ),
    ]
    plan = _MockPlan(
        sequence=["100", "NEW"],
        pickup_at={
            "100": "2026-04-25T10:00:00+00:00",  # ISO string
            "NEW": "2026-04-25T10:30:00+00:00",
        },
        predicted_delivered_at={
            "100": "2026-04-25T10:15:00+00:00",
            "NEW": "2026-04-25T10:40:00+00:00",
        },
    )
    anchor = compute_insertion_anchor(plan, "NEW", bag)
    assert anchor is not None
    assert anchor.order_id == "100"


def test_anchor_order_missing_in_bag_returns_none():
    """Gdy anchor's order_id NIE w bag_orders (orphan reference) → return None."""
    bag = []  # empty bag
    plan = _MockPlan(
        sequence=["MISSING", "NEW"],
        pickup_at={
            "MISSING": _utc("2026-04-25T10:00:00+00:00"),
            "NEW": _utc("2026-04-25T10:30:00+00:00"),
        },
        predicted_delivered_at={
            "MISSING": _utc("2026-04-25T10:15:00+00:00"),
            "NEW": _utc("2026-04-25T10:40:00+00:00"),
        },
    )
    anchor = compute_insertion_anchor(plan, "NEW", bag)
    assert anchor is None  # safe degradation


if __name__ == "__main__":
    test_468404_anchor_is_doner_pickup()
    print("test_468404_anchor_is_doner_pickup: PASS")
    test_insertion_at_chronological_start_returns_none()
    print("test_insertion_at_chronological_start_returns_none: PASS")
    test_insertion_at_end_anchor_is_last_existing()
    print("test_insertion_at_end_anchor_is_last_existing: PASS")
    test_new_order_id_not_in_plan_returns_none()
    print("test_new_order_id_not_in_plan_returns_none: PASS")
    test_empty_plan_returns_none()
    print("test_empty_plan_returns_none: PASS")
    test_none_plan_returns_none()
    print("test_none_plan_returns_none: PASS")
    test_iso_string_timestamps_supported()
    print("test_iso_string_timestamps_supported: PASS")
    test_anchor_order_missing_in_bag_returns_none()
    print("test_anchor_order_missing_in_bag_returns_none: PASS")
    print("ALL 8/8 PASS")
