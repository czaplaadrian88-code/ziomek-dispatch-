#!/usr/bin/env python3
"""Testy `reassignment_forward_shadow.py` — v2 FORWARD shadow przerzutów (READ-ONLY).

Mockujemy `DP.assess_order` i `C.flag` przez monkeypatch — ZERO realnego
assess_order / OSRM / sieci. PipelineResult i Candidate udawane przez
types.SimpleNamespace. Flota = dict cid→SimpleNamespace (pos_source/bag/tier_bag).
"""
import sys
import types
from datetime import datetime, timezone

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import common as C
from dispatch_v2 import dispatch_pipeline as DPMOD  # K09: mock siedzi na dispatch_pipeline (fasada core.decide robi call-time lookup)
from dispatch_v2.tools import reassignment_forward_shadow as RFS

_N = datetime(2026, 6, 22, 12, 0, 0, tzinfo=timezone.utc)


# ---- helpery budujące fake'i ----

def _cand(cid, score):
    """Fake Candidate (assess_order zwraca takie w .best / .candidates)."""
    return types.SimpleNamespace(courier_id=cid, score=score)


def _result(best, candidates, verdict="PROPOSE", pool_feasible_count=3):
    """Fake PipelineResult zwracany przez DP.assess_order."""
    return types.SimpleNamespace(
        best=best,
        candidates=candidates,
        verdict=verdict,
        pool_feasible_count=pool_feasible_count,
    )


def _cs(cid, pos_source="gps", bag=None, tier_bag="std"):
    """Fake CourierState (atrybuty czytane przy budowie rekordu shadow)."""
    return types.SimpleNamespace(
        courier_id=cid,
        pos_source=pos_source,
        bag=list(bag) if bag is not None else [],
        tier_bag=tier_bag,
    )


def _fleet(*states):
    return {str(cs.courier_id): cs for cs in states}


def _rec(oid="o1", cid="A"):
    """Minimalny rekord orders_state (status assigned, coords obecne)."""
    return {
        "order_id": oid,
        "courier_id": cid,
        "status": "assigned",
        "restaurant": "Pizza Test",
        "pickup_coords": [53.13, 23.16],
        "delivery_coords": [53.14, 23.17],
    }


# ============================ run_once: flaga OFF ============================

def test_run_once_flag_off(monkeypatch):
    monkeypatch.setattr(C, "flag", lambda n, d=False: False)
    assert RFS.run_once(now=_N) == {"skipped": "flag_off"}


# ============================ evaluate_order ============================

def _patch_assess(monkeypatch, result):
    """Podstaw dispatch_pipeline.assess_order pod stałą wartość; flaga ON na wszelki.
    (K09: RFS woła fasadę core.decide → call-time lookup na dispatch_pipeline.)"""
    monkeypatch.setattr(C, "flag", lambda n, d=False: True)
    monkeypatch.setattr(DPMOD, "assess_order", lambda *a, **k: result)


def test_evaluate_order_reassign_when_b_beats_a_by_margin(monkeypatch):
    # B wygrywa o >= margin (15) → would_reassign True, best_cid=B
    res = _result(best=_cand("B", 80.0), candidates=[_cand("A", 60.0), _cand("B", 80.0)])
    _patch_assess(monkeypatch, res)
    fleet = _fleet(_cs("A", bag=[{"order_id": "o1"}]), _cs("B", pos_source="gps", bag=[], tier_bag="gold"))
    out = RFS.evaluate_order(_rec("o1", "A"), "A", fleet, now=_N, margin=15.0)
    assert out is not None
    assert out["would_reassign"] is True
    assert out["best_cid"] == "B"
    assert out["a_in_pool"] is True
    assert out["delta_score"] == 20.0
    assert out["b_tier"] == "gold"


def test_evaluate_order_no_reassign_when_a_wins(monkeypatch):
    # A jest best → would_reassign False
    res = _result(best=_cand("A", 90.0), candidates=[_cand("A", 90.0), _cand("B", 70.0)])
    _patch_assess(monkeypatch, res)
    fleet = _fleet(_cs("A", bag=[{"order_id": "o1"}]), _cs("B"))
    out = RFS.evaluate_order(_rec("o1", "A"), "A", fleet, now=_N, margin=15.0)
    assert out is not None
    assert out["would_reassign"] is False
    assert out["best_cid"] == "A"


def test_evaluate_order_no_reassign_when_delta_below_margin(monkeypatch):
    # B best ale delta (10) < margin (15) → would_reassign False
    res = _result(best=_cand("B", 70.0), candidates=[_cand("A", 60.0), _cand("B", 70.0)])
    _patch_assess(monkeypatch, res)
    fleet = _fleet(_cs("A", bag=[{"order_id": "o1"}]), _cs("B"))
    out = RFS.evaluate_order(_rec("o1", "A"), "A", fleet, now=_N, margin=15.0)
    assert out is not None
    assert out["would_reassign"] is False
    assert out["delta_score"] == 10.0


def test_evaluate_order_reassign_when_a_absent_from_pool(monkeypatch):
    # A nie ma w kandydatach (a_in_pool False) a best=B → would_reassign True
    res = _result(best=_cand("B", 55.0), candidates=[_cand("B", 55.0), _cand("C", 40.0)])
    _patch_assess(monkeypatch, res)
    fleet = _fleet(_cs("A", bag=[{"order_id": "o1"}]), _cs("B"))
    out = RFS.evaluate_order(_rec("o1", "A"), "A", fleet, now=_N, margin=15.0)
    assert out is not None
    assert out["would_reassign"] is True
    assert out["best_cid"] == "B"
    assert out["a_in_pool"] is False
    assert out["a_score"] is None
    assert out["delta_score"] is None


def test_evaluate_order_none_when_best_none(monkeypatch):
    # best=None → brak feasible kandydata → evaluate_order zwraca None
    res = _result(best=None, candidates=[])
    _patch_assess(monkeypatch, res)
    fleet = _fleet(_cs("A", bag=[{"order_id": "o1"}]))
    assert RFS.evaluate_order(_rec("o1", "A"), "A", fleet, now=_N, margin=15.0) is None


def test_evaluate_order_none_when_no_oid(monkeypatch):
    # brak order_id → None (przed wywołaniem assess_order)
    monkeypatch.setattr(C, "flag", lambda n, d=False: True)
    fleet = _fleet(_cs("A"))
    assert RFS.evaluate_order({"status": "assigned"}, "A", fleet, now=_N) is None


# ============================ _fleet_without_order ============================

def test_fleet_without_order_removes_O_and_does_not_mutate_original():
    # O wyjęte z worka holdera w kopii; oryginalny CourierState.bag nietknięty.
    orig_bag = [{"order_id": "o1"}, {"order_id": "o2"}]
    cs_a = _cs("A", bag=orig_bag)
    fleet = _fleet(cs_a, _cs("B"))
    out = RFS._fleet_without_order(fleet, "o1", "A")
    # kopia: holder ma worek bez o1
    assert [RFS._bag_oid(b) for b in out["A"].bag] == ["o2"]
    # oryginał NIETKNIĘTY (długość bag bez zmian)
    assert len(fleet["A"].bag) == 2
    assert len(cs_a.bag) == 2
    # to inny obiekt CourierState (płytka kopia przez _copy.copy)
    assert out["A"] is not cs_a


def test_fleet_without_order_holder_absent_returns_shallow_copy():
    # holder nie w flocie → zwraca dict(fleet) bez zmian (no-op).
    fleet = _fleet(_cs("B", bag=[{"order_id": "x"}]))
    out = RFS._fleet_without_order(fleet, "o1", "A")
    assert out == fleet
    assert out is not fleet


def test_fleet_without_order_oid_not_in_bag_keeps_state_object():
    # oid nie ma w worku → bag bez zmiany → ten sam obiekt CourierState (brak kopii).
    cs_a = _cs("A", bag=[{"order_id": "o2"}])
    fleet = _fleet(cs_a)
    out = RFS._fleet_without_order(fleet, "o1", "A")
    assert out["A"] is cs_a
    assert len(out["A"].bag) == 1


# ============================ _active_assigned_orders ============================

def test_active_assigned_orders_filtering():
    orders = {
        "ok1": _rec("ok1", "9"),                                  # GOOD
        "picked": {**_rec("picked", "9"), "status": "picked_up"},  # zły status
        "delivered": {**_rec("delivered", "9"), "status": "delivered"},
        "no_cid": {**_rec("no_cid"), "courier_id": None},          # cid None
        "empty_cid": {**_rec("empty_cid"), "courier_id": ""},      # cid ""
        "koord": {**_rec("koord"), "courier_id": "26"},            # Koordynator
        "no_pickup": {**_rec("no_pickup", "9"), "pickup_coords": None},
        "no_delivery": {**_rec("no_delivery", "9"), "delivery_coords": None},
        "not_dict": "junk",                                        # nie-dict
    }
    out = RFS._active_assigned_orders(orders)
    assert [oid for oid, _, _ in out] == ["ok1"]
    assert out[0][1] == "9"  # cid jako str


def test_active_assigned_orders_cid_coerced_to_str():
    orders = {"i": {**_rec("i"), "courier_id": 9}}  # int cid
    out = RFS._active_assigned_orders(orders)
    assert out == [("i", "9", orders["i"])]


# ============================ _state_to_order_event ============================

def test_state_to_order_event_copies_fields_and_drops_none():
    rec = {
        "order_id": "o1",
        "restaurant": "Pizza Test",
        "pickup_coords": [53.13, 23.16],
        "delivery_coords": [53.14, 23.17],
        "czas_kuriera_warsaw": "12:30",
        "prep_minutes": None,        # None → drop
        "status": "assigned",        # spoza _EVENT_FIELDS → drop
        "courier_id": "A",           # spoza _EVENT_FIELDS → drop
    }
    ev = RFS._state_to_order_event(rec)
    assert ev["order_id"] == "o1"
    assert ev["restaurant"] == "Pizza Test"
    assert ev["pickup_coords"] == [53.13, 23.16]
    assert ev["delivery_coords"] == [53.14, 23.17]
    assert ev["czas_kuriera_warsaw"] == "12:30"
    assert "prep_minutes" not in ev   # None odrzucone
    assert "status" not in ev         # poza whitelist
    assert "courier_id" not in ev
    # tylko pola z whitelisty
    assert set(ev).issubset(set(RFS._EVENT_FIELDS))


# ============================ _bag_oid ============================

def test_bag_oid_variants():
    assert RFS._bag_oid({"order_id": "o9"}) == "o9"
    assert RFS._bag_oid({"id": 5}) == "5"          # fallback na id, str
    assert RFS._bag_oid({}) == ""                   # brak → pusty


# ============================ stała FLAG / DEFAULT_MARGIN ============================

def test_module_constants():
    assert RFS.FLAG == "ENABLE_REASSIGNMENT_FORWARD_SHADOW"
    assert RFS.DEFAULT_MARGIN == 15.0


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
