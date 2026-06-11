"""SP-B2-SYNCWORKA (2026-06-11, H1) — testy spreadu gotowości worka.

Kara gradientowa za ready_spread (max−min effective_ready niedoręczonych +
nowego): 0 @ ≤7, -30 @ 10, -80 @ 15, -150 @ ≥20 (liniowo między węzłami) +
zerowanie dodatnich bonusów bundlowych przy spreadzie >10 (wzór Fix C).
Flaga ENABLE_BUNDLE_SYNC_SPREAD default OFF — tu testujemy helpery (czysta
matematyka), efekt prep-bias za flagą oraz serializer LOCATION A+B.
"""
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from dispatch_v2 import calib_maps
from dispatch_v2 import common as C
from dispatch_v2 import dispatch_pipeline as dp
from dispatch_v2 import shadow_dispatcher

T0 = datetime(2026, 6, 11, 10, 0, tzinfo=timezone.utc)


def _bag_order(oid="b1", ready_min=0, picked_min=None, status="assigned"):
    return SimpleNamespace(
        order_id=oid,
        pickup_ready_at=T0 + timedelta(minutes=ready_min) if ready_min is not None else None,
        picked_up_at=T0 + timedelta(minutes=picked_min) if picked_min is not None else None,
        status=status,
    )


@pytest.fixture(autouse=True)
def _no_prep_bias_table(monkeypatch):
    """Default: konsumpcja prep-bias OFF (jak prod do flipu za ACK)."""
    monkeypatch.setattr(C, "ENABLE_PREP_BIAS_TABLE", False, raising=False)
    calib_maps.reset_caches()
    yield
    calib_maps.reset_caches()


# ── kara gradientowa ──

@pytest.mark.parametrize("spread,want", [
    (0.0, 0.0), (7.0, 0.0), (8.5, -15.0), (10.0, -30.0),
    (12.5, -55.0), (15.0, -80.0), (17.5, -115.0), (20.0, -150.0),
    (25.0, -150.0), (99.0, -150.0),
])
def test_sync_spread_penalty_knots(spread, want):
    assert dp._sync_spread_penalty(spread) == pytest.approx(want)


def test_sync_spread_penalty_garbage_is_zero():
    assert dp._sync_spread_penalty(None) == 0.0
    assert dp._sync_spread_penalty("x") == 0.0


# ── spread worka ──

def test_empty_bag_no_spread():
    assert dp._compute_sync_spread([], [], T0, "R", T0) == (None, 0)


def test_two_point_spread_new_vs_assigned():
    bag = [_bag_order("b1", ready_min=0)]
    spread, n = dp._compute_sync_spread(bag, [], T0 + timedelta(minutes=12), "R", T0)
    assert spread == 12.0 and n == 2


def test_picked_up_uses_picked_at_anchor():
    # odebrane 30 min PRZED gotowością nowego → spread 30 (jedzenie leży)
    bag = [_bag_order("b1", ready_min=-40, picked_min=-30, status="picked_up")]
    spread, n = dp._compute_sync_spread(bag, [], T0, "R", T0)
    assert spread == 30.0 and n == 2


def test_naive_datetimes_treated_as_utc():
    bag = [SimpleNamespace(order_id="b1", status="assigned",
                           pickup_ready_at=datetime(2026, 6, 11, 10, 0),
                           picked_up_at=None)]
    spread, n = dp._compute_sync_spread(bag, [], datetime(2026, 6, 11, 10, 8), "R", T0)
    assert spread == 8.0 and n == 2


def test_missing_ready_skipped_single_point_none():
    bag = [_bag_order("b1", ready_min=None)]
    spread, n = dp._compute_sync_spread(bag, [], T0, "R", T0)
    assert spread is None and n == 1


def test_three_orders_max_minus_min():
    bag = [_bag_order("b1", ready_min=0), _bag_order("b2", ready_min=18)]
    spread, n = dp._compute_sync_spread(bag, [], T0 + timedelta(minutes=5), "R", T0)
    assert spread == 18.0 and n == 3


def test_prep_bias_applied_only_when_table_flipped(tmp_path, monkeypatch):
    """Bias przesuwa effective_ready bag-assigned wg restauracji z bag_raw."""
    p = tmp_path / "bias.json"
    p.write_text(json.dumps({
        "global": {},
        "restaurants": {"wolna kuchnia": {"all": {"bias_med": 10.0, "n": 50, "std": 5.0}}},
    }), encoding="utf-8")
    monkeypatch.setattr(calib_maps, "PREP_BIAS_MAP_PATH", str(p))
    bag = [_bag_order("b1", ready_min=0)]
    bag_raw = [{"order_id": "b1", "restaurant": "Wolna Kuchnia"}]
    new_ready = T0 + timedelta(minutes=10)

    # OFF (default fixture): deklaracje → spread 10
    spread_off, _ = dp._compute_sync_spread(bag, bag_raw, new_ready, "Szybka", T0)
    assert spread_off == 10.0

    # ON: bag b1 ready 0 + bias 10 = 10; nowy (Szybka, bez biasu) = 10 → spread 0
    monkeypatch.setattr(C, "ENABLE_PREP_BIAS_TABLE", True, raising=False)
    calib_maps.reset_caches()
    spread_on, _ = dp._compute_sync_spread(bag, bag_raw, new_ready, "Szybka", T0)
    assert spread_on == 0.0


def test_picked_anchor_not_biased(tmp_path, monkeypatch):
    """picked_up_at to fakt — bias NIE dotyka odebranych."""
    p = tmp_path / "bias.json"
    p.write_text(json.dumps({
        "global": {"all": {"bias_med": 15.0, "n": 100, "std": 5.0}},
        "restaurants": {},
    }), encoding="utf-8")
    monkeypatch.setattr(calib_maps, "PREP_BIAS_MAP_PATH", str(p))
    monkeypatch.setattr(C, "ENABLE_PREP_BIAS_TABLE", True, raising=False)
    calib_maps.reset_caches()
    bag = [_bag_order("b1", ready_min=-20, picked_min=-20, status="picked_up")]
    # nowy: ready T0 + bias 15 → T0+15; picked anchor −20 → spread 35
    spread, _ = dp._compute_sync_spread(bag, [{"order_id": "b1", "restaurant": "X"}], T0, "Y", T0)
    assert spread == 35.0


# ── serializer LOCATION A+B (lekcja #109) ──

_SYNC_METRICS = {
    "sync_ready_spread_min": 12.5,
    "sync_spread_n": 3,
    "sync_spread_bundle_zeroed": True,
    "bonus_sync_spread": -55.0,
    "bonus_sync_spread_shadow_delta": -95.0,
}


def _mk_cand(cid="c1", score=50.0):
    return SimpleNamespace(
        courier_id=cid, name="T", score=score, plan=None,
        feasibility_verdict="MAYBE", feasibility_reason="ok",
        best_effort=False, metrics=dict(_SYNC_METRICS),
    )


def test_serializer_location_a_sync_fields():
    out = shadow_dispatcher._serialize_candidate(_mk_cand())
    assert out["sync_ready_spread_min"] == 12.5
    assert out["sync_spread_bundle_zeroed"] is True
    assert out["bonus_sync_spread"] == -55.0
    assert out["bonus_sync_spread_shadow_delta"] == -95.0


def test_serializer_location_b_best_sync_fields():
    best = _mk_cand()
    result = SimpleNamespace(
        order_id="473001", restaurant="R", delivery_address="A",
        verdict="PROPOSE", reason="ok", best=best, candidates=[best],
        pickup_ready_at=T0,
    )
    out = shadow_dispatcher._serialize_result(result, event_id="ev", latency_ms=1.0)
    assert out["best"]["sync_ready_spread_min"] == 12.5
    assert out["best"]["bonus_sync_spread_shadow_delta"] == -95.0
