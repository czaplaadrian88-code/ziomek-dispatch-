"""F2 R1-WAVE-SCOPED DIRECTIONALITY (2026-05-24).

Testuje realny blok w feasibility_v2 (po planie), mockując TYLKO
simulate_bag_route_v2 żeby deterministycznie kontrolować pickup_at /
predicted_delivered_at (bez zależności od OSRM). Archetypy z korpusu
eod_drafts/2026-05-24/ziomek_bad_picks_corpus.md:

- A: bag drop doręczony PRZED odbiorem nowego ordera → zbiór pusty →
     r1_avg_pairwise_cosine = None (brak kary korytarza, solo noga).
- C: bag drop przeciwny, doręczany PO odbiorze nowego → izolowana para
     przeciwna → cosine silnie ujemny (mocniejsza, słuszna kara).
- D: bag dropy w tym samym kierunku co nowy → cosine dodatni (brak
     fałszywej kary).
- Flag OFF: blok nie rusza metryk (r1ws_open_drop_count nieobecny).
"""
from datetime import datetime, timezone, timedelta

import pytest

from dispatch_v2 import feasibility_v2 as F
from dispatch_v2.route_simulator_v2 import OrderSim, RoutePlanV2

# Geometria z origin O=(53.13, 23.15):
O = (53.13, 23.15)          # pickup nowego ordera (= courier_pos w teście)
WEST = (53.13, 23.115)      # ~2.3 km na zachód
EAST = (53.13, 23.185)      # ~2.3 km na wschód
EAST2 = (53.13, 23.180)     # blisko EAST (ten sam kierunek)
NOW = datetime(2026, 5, 24, 12, 0, tzinfo=timezone.utc)


def _ord(oid, drop, pickup=O, status="assigned"):
    return OrderSim(order_id=oid, pickup_coords=pickup, delivery_coords=drop,
                    status=status)


def _plan(seq, pickup_at, delivered_at):
    return RoutePlanV2(
        sequence=seq, predicted_delivered_at=delivered_at, pickup_at=pickup_at,
        total_duration_min=30.0, strategy="ortools", sla_violations=0,
        osrm_fallback_used=False,
        per_order_delivery_times={o: 12.0 for o in seq},
    )


def _run(monkeypatch, bag, new_order, plan, flag_on=True):
    monkeypatch.setattr(F, "simulate_bag_route_v2", lambda *a, **k: plan)
    monkeypatch.setattr(F.C, "ENABLE_R1_WAVE_SCOPED_DIRECTIONALITY", flag_on,
                        raising=False)
    monkeypatch.setattr(F.C, "flag",
                        lambda name, default=False: flag_on
                        if name == "ENABLE_R1_WAVE_SCOPED_DIRECTIONALITY"
                        else default)
    # pomiń bramki grafiku (brak schedule w teście)
    monkeypatch.setattr(F.C, "ENABLE_V325_SCHEDULE_HARDENING", False,
                        raising=False)
    monkeypatch.setattr(F.C, "ENABLE_V324A_SCHEDULE_INTEGRATION", False,
                        raising=False)
    verdict, reason, metrics, plan_out = F.check_feasibility_v2(
        courier_pos=O, bag=bag, new_order=new_order, now=NOW,
    )
    return metrics


def test_case_a_sequential_pair_no_penalty(monkeypatch):
    """Bag drop doręczony PRZED odbiorem nowego → brak współistniejących → None."""
    bag = [_ord("B1", WEST)]
    new = _ord("N", EAST)
    plan = _plan(
        seq=["B1", "N"],
        pickup_at={"N": NOW + timedelta(minutes=10)},
        delivered_at={"B1": NOW + timedelta(minutes=5),     # PRZED odbiorem N
                      "N": NOW + timedelta(minutes=20)},
    )
    m = _run(monkeypatch, bag, new, plan)
    assert m["r1ws_open_drop_count"] == 0
    assert m["r1_avg_pairwise_cosine"] is None      # brak kary korytarza
    assert m["r1_new_drop_cosine"] is None
    # wholebag zachowany do porównania
    assert "r1_wholebag_avg_pairwise_cosine" in m


def test_case_c_opposite_pair_strong_penalty(monkeypatch):
    """Bag drop przeciwny, doręczany PO odbiorze nowego → cosine silnie ujemny."""
    bag = [_ord("B1", WEST)]
    new = _ord("N", EAST)
    plan = _plan(
        seq=["B1", "N"],
        pickup_at={"N": NOW + timedelta(minutes=5)},
        delivered_at={"B1": NOW + timedelta(minutes=15),    # PO odbiorze N
                      "N": NOW + timedelta(minutes=20)},
    )
    m = _run(monkeypatch, bag, new, plan)
    assert m["r1ws_open_drop_count"] == 1
    assert m["r1_avg_pairwise_cosine"] is not None
    assert m["r1_avg_pairwise_cosine"] < -0.8        # przeciwne kierunki
    assert m["r1_new_drop_cosine"] < -0.8


def test_case_d_same_direction_positive(monkeypatch):
    """Bag dropy w tym samym kierunku co nowy → cosine dodatni (brak fałszywej kary)."""
    bag = [_ord("B1", EAST), _ord("B2", EAST2)]
    new = _ord("N", EAST)
    plan = _plan(
        seq=["B1", "B2", "N"],
        pickup_at={"N": NOW + timedelta(minutes=5)},
        delivered_at={"B1": NOW + timedelta(minutes=15),
                      "B2": NOW + timedelta(minutes=18),
                      "N": NOW + timedelta(minutes=22)},
    )
    m = _run(monkeypatch, bag, new, plan)
    assert m["r1ws_open_drop_count"] == 2
    assert m["r1_avg_pairwise_cosine"] > 0.8         # zgodny korytarz


def test_flag_off_block_inert(monkeypatch):
    """Flag OFF → blok nie rusza metryk (r1ws_open_drop_count nieobecny)."""
    bag = [_ord("B1", WEST)]
    new = _ord("N", EAST)
    plan = _plan(
        seq=["B1", "N"],
        pickup_at={"N": NOW + timedelta(minutes=5)},
        delivered_at={"B1": NOW + timedelta(minutes=15),
                      "N": NOW + timedelta(minutes=20)},
    )
    m = _run(monkeypatch, bag, new, plan, flag_on=False)
    assert "r1ws_open_drop_count" not in m
    assert "r1_wholebag_avg_pairwise_cosine" not in m


def test_serializer_keys_present_both_locations():
    """Downstream serializer presence (reguła): nowe klucze w obu LOCATION + best dict."""
    import pathlib
    base = pathlib.Path(__file__).resolve().parent.parent
    shadow = (base / "shadow_dispatcher.py").read_text()
    pipe = (base / "dispatch_pipeline.py").read_text() + (base / "core" / "candidates.py").read_text()  # K11
    for key in ("r1_wholebag_avg_pairwise_cosine", "r1_wholebag_new_drop_cosine",
                "r1ws_open_drop_count"):
        assert shadow.count(key) >= 2, f"{key} musi być w LOCATION A i B shadow"
        assert key in pipe, f"{key} musi być w best dict dispatch_pipeline"
