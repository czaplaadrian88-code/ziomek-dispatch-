"""SP-B2-PLN (2026-06-11) — testy funkcji celu PLN (shadow).

V = 6,33 − koszt_km·Δkm − 14·P(breach) − 0,20·leżenie − opp·(blokada+czekanie);
P(breach) = σ(−5,746 + 0,297·km + 0,649·worek + 0,090·load).
Czysta telemetria (ENABLE_PLN_OBJECTIVE_SHADOW); użycie w decyzji = 🛑 ACK.
"""
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from dispatch_v2 import pln_objective as P
from dispatch_v2 import shadow_dispatcher

T_PEAK = datetime(2026, 6, 11, 13, 0, tzinfo=timezone.utc)   # 15:00 Wwa (peak 13-20)
T_OFF = datetime(2026, 6, 11, 4, 0, tzinfo=timezone.utc)     # 06:00 Wwa


@pytest.fixture(autouse=True)
def _fresh_vehicle_cache():
    P.reset_caches()
    yield
    P.reset_caches()


# ── P(breach) ──

def test_p_breach_calibration_points():
    # czysty intercept: km=0, worek=0, load=0 → σ(−5,746) ≈ 0,0032
    assert P.p_breach(0, 0, 0) == pytest.approx(1 / (1 + math.exp(5.746)), rel=1e-6)
    # marginalia z raportu: +1 worek = +0,649 logitu; +1 km = +0,297
    assert P.p_breach(5, 2, 2) > P.p_breach(5, 1, 2) > P.p_breach(4, 1, 2)
    # monotoniczność load
    assert P.p_breach(5, 1, 4) > P.p_breach(5, 1, 1)


def test_p_breach_example_magnitudes():
    # przykład #2 raportu: „9,1 km do worka-3" = 3 istniejące + nowe → worek=4
    # w logicie → P≈47%; „3,5 km worek-2" → worek=3 → P≈8%. (Logit liczy stan
    # PO dodaniu — w compute_pln_value to bag_before+1.)
    assert P.p_breach(9.1, 4, 2.0) == pytest.approx(0.47, abs=0.06)
    assert P.p_breach(3.5, 3, 2.0) == pytest.approx(0.08, abs=0.04)


# ── opp rate ──

def test_opp_rate_tiers():
    assert P.opp_rate(T_PEAK, 2.0) == P.PLN_OPP_PEAK
    assert P.opp_rate(T_OFF, 2.0) == P.PLN_OPP_OFF
    assert P.opp_rate(T_PEAK, 3.6) == P.PLN_OPP_OVERLOAD
    assert P.opp_rate(T_OFF, 3.6) == P.PLN_OPP_OVERLOAD


# ── vehicle ──

def test_vehicle_map_and_default(tmp_path, monkeypatch):
    p = tmp_path / "courier_vehicle.json"
    p.write_text(json.dumps({"509": "wlasne", "123": "firmowe"}), encoding="utf-8")
    monkeypatch.setattr(P, "COURIER_VEHICLE_PATH", str(p))
    P.reset_caches()
    own = P.compute_pln_value(cid="509", delta_km=5.0, bag_before=0, load=1.0,
                              travel_min=10.0, time_to_ready_min=10.0, now=T_OFF)
    firm = P.compute_pln_value(cid="123", delta_km=5.0, bag_before=0, load=1.0,
                               travel_min=10.0, time_to_ready_min=10.0, now=T_OFF)
    unknown = P.compute_pln_value(cid="999", delta_km=5.0, bag_before=0, load=1.0,
                                  travel_min=10.0, time_to_ready_min=10.0, now=T_OFF)
    assert own["pln_vehicle"] == "wlasne" and firm["pln_vehicle"] == "firmowe"
    # własne auto: 0 PLN/km → V wyżej o 0,90×5
    assert own["pln_v"] - firm["pln_v"] == pytest.approx(4.5, abs=0.01)
    assert unknown["pln_vehicle"] == "firmowe"


def test_vehicle_missing_file_default_firmowe(tmp_path, monkeypatch):
    monkeypatch.setattr(P, "COURIER_VEHICLE_PATH", str(tmp_path / "brak.json"))
    P.reset_caches()
    out = P.compute_pln_value(cid="1", delta_km=1.0, bag_before=0, load=0.0,
                              travel_min=5.0, time_to_ready_min=5.0, now=T_OFF)
    assert out["pln_vehicle"] == "firmowe"


# ── V — kierunek i komponenty ──

def test_v_prefers_close_busy_over_far_empty():
    """Przykład #3 raportu (Chinatown): exp(-km/5) nie umie zawetować 18 km."""
    far = P.compute_pln_value(cid="1", delta_km=20.2, bag_before=0, load=2.0,
                              travel_min=35.0, time_to_ready_min=10.0, now=T_PEAK)
    near = P.compute_pln_value(cid="2", delta_km=3.0, bag_before=1, load=2.0,
                               travel_min=5.0, time_to_ready_min=10.0, now=T_PEAK)
    assert near["pln_v"] > far["pln_v"]
    assert far["pln_v"] < 0 < near["pln_v"]


def test_lezenie_and_czekanie_split():
    # dojazd 20 > gotowość 5 → leżenie 15, czekanie 0
    a = P.compute_pln_value(cid="1", delta_km=3.0, bag_before=0, load=0.0,
                            travel_min=20.0, time_to_ready_min=5.0, now=T_OFF)
    assert a["pln_lezenie_min"] == 15.0 and a["pln_czekanie_min"] == 0.0
    # gotowość 20 > dojazd 5 → czekanie 15
    b = P.compute_pln_value(cid="1", delta_km=3.0, bag_before=0, load=0.0,
                            travel_min=5.0, time_to_ready_min=20.0, now=T_OFF)
    assert b["pln_czekanie_min"] == 15.0 and b["pln_lezenie_min"] == 0.0


def test_missing_inputs_return_none():
    assert P.compute_pln_value(cid="1", delta_km=None, bag_before=0, load=0.0,
                               travel_min=5.0, time_to_ready_min=5.0) is None
    assert P.compute_pln_value(cid="1", delta_km=3.0, bag_before=0, load=0.0,
                               travel_min=None, time_to_ready_min=5.0) is None


# ── serializer LOCATION A+B ──

def _ser_cand():
    return SimpleNamespace(
        courier_id="c1", name="T", score=66.0, plan=None,
        feasibility_verdict="MAYBE", feasibility_reason="ok", best_effort=False,
        metrics={"pln_v": 2.47, "pln_p_breach": 0.033, "pln_delta_km": 3.0,
                 "pln_vehicle": "firmowe", "pln_best_cid": "c2",
                 "pln_best_v": 4.1, "pln_vs_score_flip": True},
    )


def test_serializer_location_a_pln_fields():
    out = shadow_dispatcher._serialize_candidate(_ser_cand())
    assert out["pln_v"] == 2.47
    assert out["pln_p_breach"] == 0.033
    assert out["pln_vs_score_flip"] is True


def test_serializer_location_b_best_pln_fields():
    best = _ser_cand()
    result = SimpleNamespace(
        order_id="477001", restaurant="R", delivery_address="A",
        verdict="PROPOSE", reason="ok", best=best, candidates=[best],
        pickup_ready_at=datetime(2026, 6, 11, 12, 0, tzinfo=timezone.utc),
    )
    out = shadow_dispatcher._serialize_result(result, event_id="ev", latency_ms=1.0)
    assert out["best"]["pln_best_cid"] == "c2"
    assert out["best"]["pln_vs_score_flip"] is True
    assert out["best"]["pln_v"] == 2.47
