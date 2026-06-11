"""SP-B2-REPO (2026-06-11) — testy kosztu repozycjonowania (dead-head).

repo_km = km(drop poprzedzający nowy odbiór w PLANIE kandydata → nowy pickup);
odbiór przed dropami / pusty bag → None (km_to_pickup już wycenia — zero
podwójnego liczenia). Kara = -30 * min(1, km/4). Telemetria zawsze
(ENABLE_REPO_COST_SHADOW ON); aplikacja do score za 🛑 ENABLE_REPO_COST_LIVE.
"""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from dispatch_v2 import dispatch_pipeline as dp
from dispatch_v2 import shadow_dispatcher
from dispatch_v2.osrm_client import haversine

T0 = datetime(2026, 6, 11, 10, 0, tzinfo=timezone.utc)

# Białystok: ~1 km na 0.009° szerokości
P_REST = (53.1300, 23.1600)       # nowy pickup
P_DROP_FAR = (53.1660, 23.1600)   # ~4 km na północ
P_DROP_NEAR = (53.1390, 23.1600)  # ~1 km


def _bag(oid, drop_coords):
    return SimpleNamespace(
        order_id=oid, pickup_coords=(53.12, 23.15), delivery_coords=drop_coords,
        picked_up_at=None, status="assigned", pickup_ready_at=T0,
    )


def _plan(seq_times):
    """seq_times: dict oid -> (delivered_at | None). pickup_at dla 'new'."""
    delivered = {k: v for k, v in seq_times.items() if k != "new" and v is not None}
    return SimpleNamespace(
        sequence=list(seq_times.keys()),
        predicted_delivered_at=delivered,
        pickup_at={"new": seq_times.get("new")},
        arrival_at={},
    )


# ── kara ──

@pytest.mark.parametrize("km,want", [
    (None, 0.0), (0.0, 0.0), (1.0, -7.5), (2.0, -15.0),
    (3.56, -26.7), (4.0, -30.0), (9.0, -30.0),
])
def test_repo_penalty_scale(km, want):
    assert dp._repo_cost_penalty(km) == pytest.approx(want, abs=0.01)


# ── km dead-headu wg planu ──

def test_pickup_after_drop_measures_deadhead():
    """Plan: drop b1 (12:10) → pickup new (12:30) → repo_km = dist(b1.drop, pickup)."""
    bag = [_bag("b1", P_DROP_FAR)]
    plan = _plan({"b1": T0 + timedelta(minutes=10), "new": T0 + timedelta(minutes=30)})
    km, last_oid = dp._compute_repo_cost_km(bag, plan, "new", P_REST)
    assert last_oid == "b1"
    assert km == pytest.approx(haversine(P_DROP_FAR, P_REST), abs=0.05)
    assert km > 3.5  # ~4 km


def test_pickup_first_no_deadhead():
    """Nowy odbiór PRZED dropami (po drodze) → None (km_to_pickup wycenia)."""
    bag = [_bag("b1", P_DROP_FAR)]
    plan = _plan({"new": T0 + timedelta(minutes=5), "b1": T0 + timedelta(minutes=25)})
    km, last_oid = dp._compute_repo_cost_km(bag, plan, "new", P_REST)
    assert km is None and last_oid is None


def test_last_of_two_drops_used():
    """Dwa dropy przed odbiorem → liczy od PÓŹNIEJSZEGO (faktyczna końcówka)."""
    bag = [_bag("b1", P_DROP_FAR), _bag("b2", P_DROP_NEAR)]
    plan = _plan({
        "b1": T0 + timedelta(minutes=10),
        "b2": T0 + timedelta(minutes=20),
        "new": T0 + timedelta(minutes=35),
    })
    km, last_oid = dp._compute_repo_cost_km(bag, plan, "new", P_REST)
    assert last_oid == "b2"
    assert km == pytest.approx(haversine(P_DROP_NEAR, P_REST), abs=0.05)


def test_empty_bag_or_no_plan_none():
    assert dp._compute_repo_cost_km([], None, "new", P_REST) == (None, None)
    assert dp._compute_repo_cost_km([_bag("b1", P_DROP_FAR)], None, "new", P_REST) == (None, None)


def test_missing_pickup_at_fail_soft():
    bag = [_bag("b1", P_DROP_FAR)]
    plan = _plan({"b1": T0 + timedelta(minutes=10)})  # brak 'new' w pickup_at
    plan.pickup_at = {}
    assert dp._compute_repo_cost_km(bag, plan, "new", P_REST) == (None, None)


def test_naive_datetimes_normalized():
    bag = [_bag("b1", P_DROP_NEAR)]
    plan = _plan({"b1": datetime(2026, 6, 11, 10, 10), "new": datetime(2026, 6, 11, 10, 30)})
    km, last_oid = dp._compute_repo_cost_km(bag, plan, "new", P_REST)
    assert last_oid == "b1" and km is not None


# ── serializer LOCATION A+B ──

def _ser_cand():
    return SimpleNamespace(
        courier_id="c1", name="T", score=50.0, plan=None,
        feasibility_verdict="MAYBE", feasibility_reason="ok", best_effort=False,
        metrics={"repo_km": 3.56, "repo_last_drop_oid": "471888",
                 "bonus_repo_cost_shadow_delta": -26.7},
    )


def test_serializer_location_a_repo_fields():
    out = shadow_dispatcher._serialize_candidate(_ser_cand())
    assert out["repo_km"] == 3.56
    assert out["repo_last_drop_oid"] == "471888"
    assert out["bonus_repo_cost_shadow_delta"] == -26.7


def test_serializer_location_b_best_repo_fields():
    best = _ser_cand()
    result = SimpleNamespace(
        order_id="474001", restaurant="R", delivery_address="A",
        verdict="PROPOSE", reason="ok", best=best, candidates=[best],
        pickup_ready_at=T0,
    )
    out = shadow_dispatcher._serialize_result(result, event_id="ev", latency_ms=1.0)
    assert out["best"]["repo_km"] == 3.56
    assert out["best"]["bonus_repo_cost_shadow_delta"] == -26.7
