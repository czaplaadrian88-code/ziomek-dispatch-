"""Z-09 (audyt 2026-06-10) — luki serializacji shadow_decisions.

1. Łańcuch mnożnika Bug Z (v327_min_drop_factor / v327_bundle_score_mult /
   v327_score_pre_mult / v327_drop_zones_audit / v327_mult_sign_guarded) NIE był
   serializowany — brak prefiksu "v327_" w _AUTO_PROP_PREFIXES → kalibracja Z-02
   z logu niemożliwa.
2. late_pickup_* / new_pickup_* były explicit tylko w LOCATION A (alternatives);
   best (LOCATION B) ich nie miał — prefiksy wyrównują.
3. pos_from_store / pos_age_min: rescue ze store replay'uje pierwotny label
   pos_source ("gps") — bez tych pól nieodróżnialny od żywego fixa w logu.
"""
import sys
from datetime import datetime, timezone
from types import SimpleNamespace

sys.path.insert(0, '/root/.openclaw/workspace/scripts')

from dispatch_v2 import shadow_dispatcher


_METRICS = {
    "v327_min_drop_factor": 0.0,
    "v327_bundle_score_mult": 0.7,
    "v327_score_pre_mult": -42.5,
    "v327_min_drop_factor_known": None,
    "v327_unknown_zone_present": True,
    "v327_mult_sign_guarded": True,
    "v327_drop_zones_audit": {"new_zone": "Unknown", "bag_zones": ["Antoniuk"]},
    "late_pickup_max_min": 12.0,
    "late_pickup_committed_max": 8.0,
    "late_pickup_committed_breach": False,
    "new_pickup_late_min": 3.5,
    "pos_from_store": True,
    "pos_age_min": 17.3,
    "pos_source": "gps",
}


def _mk_candidate(cid="c1", score=50.0):
    return SimpleNamespace(
        courier_id=cid,
        name="Test",
        score=score,
        plan=None,
        feasibility_verdict="MAYBE",
        feasibility_reason="ok",
        best_effort=False,
        metrics=dict(_METRICS),
    )


def _mk_result(best, candidates):
    return SimpleNamespace(
        order_id="471000",
        restaurant="R",
        delivery_address="A",
        verdict="PROPOSE",
        reason="ok",
        best=best,
        candidates=candidates,
        pickup_ready_at=datetime(2026, 6, 10, 12, 0, tzinfo=timezone.utc),
    )


def test_location_a_alternatives_have_v327_and_pos_store_fields():
    out = shadow_dispatcher._serialize_candidate(_mk_candidate())
    assert out["v327_bundle_score_mult"] == 0.7
    assert out["v327_score_pre_mult"] == -42.5
    assert out["v327_mult_sign_guarded"] is True
    assert out["v327_drop_zones_audit"]["new_zone"] == "Unknown"
    assert out["pos_from_store"] is True
    assert out["pos_age_min"] == 17.3
    # late_pickup_* były już explicit w LOCATION A — regresja
    assert out["late_pickup_max_min"] == 12.0
    assert out["new_pickup_late_min"] == 3.5


def test_location_b_best_has_v327_late_pickup_and_pos_store_fields():
    best = _mk_candidate(cid="c1", score=80.0)
    alt = _mk_candidate(cid="c2", score=60.0)
    out = shadow_dispatcher._serialize_result(
        _mk_result(best, [best, alt]), event_id="ev1", latency_ms=10.0)
    b = out["best"]
    assert b is not None
    # v327_* przez _propagate_prefixed_metrics(out["best"], best_m)
    assert b["v327_bundle_score_mult"] == 0.7
    assert b["v327_min_drop_factor"] == 0.0
    assert b["v327_mult_sign_guarded"] is True
    assert b["v327_unknown_zone_present"] is True
    # late_pickup_*/new_pickup_* wyrównane do alternatives (sedno Z-09 pkt 3)
    assert b["late_pickup_max_min"] == 12.0
    assert b["late_pickup_committed_max"] == 8.0
    assert b["late_pickup_committed_breach"] is False
    assert b["new_pickup_late_min"] == 3.5
    # pos_from_store explicit (LOCATION B)
    assert b["pos_from_store"] is True
    assert b["pos_age_min"] == 17.3
    # alternatives nadal kompletne
    a = out["alternatives"][0]
    assert a["v327_bundle_score_mult"] == 0.7
    assert a["pos_from_store"] is True


def test_explicit_keys_not_clobbered_by_autoprop():
    """_propagate_prefixed_metrics pomija klucze już obecne — explicit wins."""
    best = _mk_candidate()
    out = shadow_dispatcher._serialize_result(
        _mk_result(best, [best]), event_id="ev2", latency_ms=5.0)
    # pos_source explicit w best inline — nie nadpisany, wartość z metrics
    assert out["best"]["pos_source"] == "gps"


def test_missing_fields_serialize_as_none_no_crash():
    """Stare decyzje / kandydaci bez nowych metryk → None, zero wyjątków."""
    cand = SimpleNamespace(
        courier_id="c9", name="Old", score=1.0, plan=None,
        feasibility_verdict="NO", feasibility_reason="x", best_effort=False,
        metrics={},
    )
    out = shadow_dispatcher._serialize_candidate(cand)
    assert out["pos_from_store"] is None
    assert out["pos_age_min"] is None
    assert "v327_bundle_score_mult" not in out  # auto-prop: brak klucza gdy brak w metrics
