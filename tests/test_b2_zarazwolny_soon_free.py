"""SP-B2-ZARAZWOLNY (2026-06-11, B2) — testy probe "zaraz-wolny".

61% busy-picków człowieka = kurier kończący ≤12 min. Probe czyta zapisany
plan kuriera (plan_manager) → {eligible, free_at_min, last_drop_coords}.
Telemetria soon_free_* zawsze; substytucja wejść za 🛑 ENABLE_SOON_FREE_CANDIDATE
(OFF). Serializer LOCATION A+B przez prefix soon_free_.
"""
import json
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from dispatch_v2 import dispatch_pipeline as dp
from dispatch_v2 import plan_manager
from dispatch_v2 import shadow_dispatcher

T0 = datetime(2026, 6, 11, 10, 0, tzinfo=timezone.utc)


def _write_plans(tmp_path, monkeypatch, plans: dict):
    """Podstaw tymczasowy courier_plans.json pod plan_manager."""
    p = tmp_path / "courier_plans.json"
    p.write_text(json.dumps(plans), encoding="utf-8")
    lock = tmp_path / "courier_plans.lock"
    lock.write_text("", encoding="utf-8")
    monkeypatch.setattr(plan_manager, "PLANS_FILE", p)
    monkeypatch.setattr(plan_manager, "LOCK_FILE", lock)
    return p


def _plan_doc(stops, invalidated=None):
    return {
        "plan_version": 2, "created_at": T0.isoformat(),
        "last_modified_at": T0.isoformat(), "start_pos": None,
        "start_ts": T0.isoformat(), "stops": stops,
        "optimization_method": "test", "bag_signature": "x",
        "retimed_at": None, "invalidated_at": invalidated,
        "invalidation_reason": None,
    }


def _drop(oid, minutes, lat=53.14, lng=23.17):
    return {"order_id": oid, "type": "dropoff",
            "coords": {"lat": lat, "lng": lng},
            "scheduled_at": None,
            "predicted_at": (T0 + timedelta(minutes=minutes)).isoformat(),
            "dwell_min": 2.0, "status_at_plan_time": "assigned"}


BAG = [{"order_id": "b1", "restaurant": "R"}, {"order_id": "b2", "restaurant": "S"}]


def test_probe_eligible_finishing_in_8_min(tmp_path, monkeypatch):
    _write_plans(tmp_path, monkeypatch, {"77": _plan_doc(
        [_drop("b1", 3), _drop("b2", 8, lat=53.15, lng=23.18)])})
    probe = dp._soon_free_probe("77", BAG, T0)
    assert probe is not None
    assert probe["eligible"] is True
    assert probe["free_at_min"] == 8.0
    assert probe["last_drop_coords"] == (53.15, 23.18)


def test_probe_not_eligible_above_12_min(tmp_path, monkeypatch):
    _write_plans(tmp_path, monkeypatch, {"77": _plan_doc(
        [_drop("b1", 3), _drop("b2", 25)])})
    probe = dp._soon_free_probe("77", BAG, T0)
    assert probe is not None and probe["eligible"] is False
    assert probe["free_at_min"] == 25.0


def test_probe_overdue_plan_clamped_to_zero(tmp_path, monkeypatch):
    _write_plans(tmp_path, monkeypatch, {"77": _plan_doc(
        [_drop("b1", -10), _drop("b2", -4)])})
    probe = dp._soon_free_probe("77", BAG, T0)
    assert probe["eligible"] is True and probe["free_at_min"] == 0.0


def test_probe_none_for_empty_bag_or_missing_plan(tmp_path, monkeypatch):
    _write_plans(tmp_path, monkeypatch, {})
    assert dp._soon_free_probe("77", [], T0) is None        # pusty bag
    assert dp._soon_free_probe("77", BAG, T0) is None       # brak planu


def test_probe_none_on_bag_mismatch(tmp_path, monkeypatch):
    """Plan z dropem spoza bag_oids → load_plan invaliduje → None (V3.19d)."""
    _write_plans(tmp_path, monkeypatch, {"77": _plan_doc(
        [_drop("b1", 3), _drop("OBCY", 8)])})
    assert dp._soon_free_probe("77", BAG, T0) is None


def test_probe_none_on_invalidated_plan(tmp_path, monkeypatch):
    _write_plans(tmp_path, monkeypatch, {"77": _plan_doc(
        [_drop("b1", 3), _drop("b2", 8)], invalidated=T0.isoformat())})
    assert dp._soon_free_probe("77", BAG, T0) is None


# ── serializer LOCATION A+B ──

def _ser_cand():
    return SimpleNamespace(
        courier_id="77", name="T", score=44.0, plan=None,
        feasibility_verdict="MAYBE", feasibility_reason="ok", best_effort=False,
        metrics={"soon_free_eligible": True, "soon_free_applied": False,
                 "soon_free_free_at_min": 8.0, "soon_free_last_drop_km": 1.42},
    )


def test_serializer_location_a_soon_free_fields():
    out = shadow_dispatcher._serialize_candidate(_ser_cand())
    assert out["soon_free_eligible"] is True
    assert out["soon_free_applied"] is False
    assert out["soon_free_free_at_min"] == 8.0
    assert out["soon_free_last_drop_km"] == 1.42


def test_serializer_location_b_best_soon_free_fields():
    best = _ser_cand()
    result = SimpleNamespace(
        order_id="475001", restaurant="R", delivery_address="A",
        verdict="PROPOSE", reason="ok", best=best, candidates=[best],
        pickup_ready_at=T0,
    )
    out = shadow_dispatcher._serialize_result(result, event_id="ev", latency_ms=1.0)
    assert out["best"]["soon_free_eligible"] is True
    assert out["best"]["soon_free_free_at_min"] == 8.0
