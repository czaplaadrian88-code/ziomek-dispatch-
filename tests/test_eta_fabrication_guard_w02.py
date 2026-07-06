"""W0.2 — bezpiecznik fabrykacji ETA (advisory Faza 6.2, werdykt E-1).

Testuje:
 - detekcję (shadow, compute-always): pred_carry balonuje vs robust_ref → eta_unreliable=True;
   legalna dostawa → False; poniżej podłogi 60′ → False; brak floora → None.
 - serializacja LOCATION A+B: sygnały w best.metrics + top-level result.
 - flaga ON≠OFF: OFF = czysta obserwacja (verdict/reason/defer_hint nietknięte);
   ON = defer_hint + reason `eta_unreliable_defer` przy KOORD (NIGDY KOORD z fabrykatem).

Robust_ref liczony fizycznym osrm freeflow — tu monkeypatchujemy `osrm_client.route`
by wstrzyknąć deterministyczny floor (zero sieci, zero zależności od żywego OSRM).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from dispatch_v2 import common as C
from dispatch_v2 import dispatch_pipeline as DP


class _Plan:
    def __init__(self, pred_deliv_map):
        self.predicted_delivered_at = pred_deliv_map


class _Cand:
    def __init__(self, cid="447"):
        self.courier_id = cid
        self.name = f"K{cid}"
        self.score = 10.0
        self.metrics = {}
        self.plan = None


def _mk_result(oid, ready, pred_deliv, verdict="PROPOSE"):
    best = _Cand()
    best.plan = _Plan({oid: pred_deliv})
    r = DP.PipelineResult(
        order_id=oid, verdict=verdict, reason="base", best=best,
        candidates=[best], pickup_ready_at=ready, restaurant="R",
    )
    return r


@pytest.fixture
def freeflow_20(monkeypatch):
    """robust_ref ≈ 20 (drive) + service 12 + slack 5 = 37 min."""
    monkeypatch.setattr(DP, "_robust_eta_ref_min",
                        lambda pc, dc, now: 37.0)


def _order_ev():
    return {"pickup_coords": [53.13, 23.16], "delivery_coords": [53.14, 23.17]}


# ── detekcja (shadow, compute-always) ────────────────────────────────

def test_fabrication_detected(freeflow_20):
    now = datetime(2026, 5, 16, 12, 0, tzinfo=timezone.utc)
    # pred_carry = 180 min vs robust_ref 37 → ratio 4.9 > 2.5 ∧ >60 → UNRELIABLE
    r = _mk_result("1", now, now + timedelta(minutes=180))
    DP._eta_fabrication_check(r, _order_ev(), now)
    assert r.eta_unreliable is True
    assert r.eta_unreliable_meta["ratio"] > 2.5
    assert r.best.metrics["eta_unreliable"] is True
    assert r.best.metrics["eta_robust_ref_min"] == 37.0


def test_legit_delivery_not_flagged(freeflow_20):
    now = datetime(2026, 5, 16, 12, 0, tzinfo=timezone.utc)
    # pred_carry 70 vs ref 37 → ratio 1.9 < 2.5 → NIE fabrykacja (mimo >60)
    r = _mk_result("1", now, now + timedelta(minutes=70))
    DP._eta_fabrication_check(r, _order_ev(), now)
    assert r.eta_unreliable is False


def test_below_floor_not_flagged(freeflow_20):
    now = datetime(2026, 5, 16, 12, 0, tzinfo=timezone.utc)
    # pred_carry 40 ≤ podłoga 60 → False bez liczenia robust_ref
    r = _mk_result("1", now, now + timedelta(minutes=40))
    DP._eta_fabrication_check(r, _order_ev(), now)
    assert r.eta_unreliable is False


def test_no_robust_ref_is_none(monkeypatch):
    now = datetime(2026, 5, 16, 12, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(DP, "_robust_eta_ref_min", lambda pc, dc, now: None)
    r = _mk_result("1", now, now + timedelta(minutes=180))
    DP._eta_fabrication_check(r, _order_ev(), now)
    assert r.eta_unreliable is None  # brak pewnego floora → nie osądzamy


def test_early_bird_long_wait_not_flagged_uses_carry(freeflow_20):
    """Total-based flagowałoby early-bird (długi legalny WAIT); carry-based NIE."""
    now = datetime(2026, 5, 16, 8, 0, tzinfo=timezone.utc)
    ready = now + timedelta(hours=2)  # odbiór za 2h (early-bird)
    pred_deliv = ready + timedelta(minutes=25)  # carry 25 = legalne
    r = _mk_result("1", ready, pred_deliv)
    DP._eta_fabrication_check(r, _order_ev(), now)
    assert r.eta_unreliable is False  # carry 25 < podłoga → nie fabrykacja


# ── flaga ON≠OFF (aktywny routing) ───────────────────────────────────

def test_flag_off_shadow_only(freeflow_20, monkeypatch):
    monkeypatch.setattr(C, "flag",
                        lambda name, default=None: False if name == "ENABLE_ETA_FABRICATION_GUARD" else default)
    now = datetime(2026, 5, 16, 12, 0, tzinfo=timezone.utc)
    r = _mk_result("1", now, now + timedelta(minutes=180), verdict="KOORD")
    DP._eta_fabrication_check(r, _order_ev(), now)
    assert r.eta_unreliable is True        # detekcja działa (shadow)
    assert r.eta_defer_hint is None        # ale NIE rusza routingu
    assert r.reason == "base"              # verdict/reason nietknięte


def test_flag_on_routes_koord_to_defer(freeflow_20, monkeypatch):
    monkeypatch.setattr(C, "flag",
                        lambda name, default=None: True if name == "ENABLE_ETA_FABRICATION_GUARD" else default)
    now = datetime(2026, 5, 16, 12, 0, tzinfo=timezone.utc)
    r = _mk_result("1", now, now + timedelta(minutes=180), verdict="KOORD")
    DP._eta_fabrication_check(r, _order_ev(), now)
    assert r.eta_unreliable is True
    assert r.eta_defer_hint is True                    # NIGDY KOORD z fabrykatem
    assert "eta_unreliable_defer" in r.reason
    assert r.best.metrics.get("eta_koord_fabrication_flagged") is True


def test_flag_on_propose_gets_defer_hint_no_reason_mangle(freeflow_20, monkeypatch):
    monkeypatch.setattr(C, "flag",
                        lambda name, default=None: True if name == "ENABLE_ETA_FABRICATION_GUARD" else default)
    now = datetime(2026, 5, 16, 12, 0, tzinfo=timezone.utc)
    r = _mk_result("1", now, now + timedelta(minutes=180), verdict="PROPOSE")
    DP._eta_fabrication_check(r, _order_ev(), now)
    assert r.eta_defer_hint is True
    assert r.reason == "base"  # PROPOSE reason nietknięty (marker tylko przy KOORD)
