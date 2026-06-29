"""Testy strażnika carried-first (read-only detektor).

Izolujemy LOGIKĘ STRAŻNIKA (routing reżimów + wykrycie carried-first) przez
monkeypatch dwóch funkcji silnika (`_start_anchor`, `_apply_canon_order_invariants`)
— ich poprawność testuje suite plan_recheck; tu sprawdzamy, że strażnik dobrze
klasyfikuje wynik. Zero I/O na żywych plikach (deps wstrzykiwane do evaluate()).
"""
from dispatch_v2.tools import carried_first_guard as G
from dispatch_v2 import plan_recheck as PR

ACTIVE = next(iter(PR.ACTIVE_STATUSES))


def _os(cid="492", oids=("A", "B")):
    return {oid: {"courier_id": cid, "status": ACTIVE} for oid in oids}


def _stops(seq):
    # seq = [(oid, type), ...] -> stops w formacie zapisanego planu
    return [{"order_id": o, "type": t, "coords": {"lat": 53.1, "lng": 23.1}} for o, t in seq]


def _run(monkeypatch, *, anchor, canon_seq, plans, orders_state=None):
    monkeypatch.setattr(PR, "_start_anchor", lambda *a, **k: anchor)
    if canon_seq is not None:
        monkeypatch.setattr(PR, "_apply_canon_order_invariants",
                            lambda stops, *a, **k: _stops(canon_seq))
    return G.evaluate(orders_state=orders_state or _os(),
                      gps_positions={}, plans=plans, write=False)


POS = ((53.1, 23.1), None, "gps_pwa")


def test_ok_when_plan_matches_canon(monkeypatch):
    saved = [("B", "pickup"), ("A", "dropoff"), ("B", "dropoff")]
    res = _run(monkeypatch, anchor=POS, canon_seq=saved,
               plans={"492": {"stops": _stops(saved)}})
    assert len(res) == 1
    assert res[0]["kind"] == "ok"
    assert res[0]["risk"] is False


def test_carried_first_detected(monkeypatch):
    # ZAPISANY plan: dowieź A przed odbiorem B (carried-first)
    saved = [("A", "dropoff"), ("B", "pickup"), ("B", "dropoff")]
    # KANON-z-pozycją: odbierz B PRZED dowiezieniem A
    canon = [("B", "pickup"), ("A", "dropoff"), ("B", "dropoff")]
    res = _run(monkeypatch, anchor=POS, canon_seq=canon,
               plans={"492": {"stops": _stops(saved)}})
    assert res[0]["kind"] == "carried_first"
    assert res[0]["risk"] is True
    assert res[0]["saved_seq"][0] == ["A", "dropoff"]


def test_canon_divergence_not_carried_first(monkeypatch):
    # plan ≠ kanon, ale NIE jest to „dostawa przed cudzym odbiorem"
    saved = [("A", "pickup"), ("B", "pickup"), ("A", "dropoff"), ("B", "dropoff")]
    canon = [("B", "pickup"), ("A", "pickup"), ("B", "dropoff"), ("A", "dropoff")]
    res = _run(monkeypatch, anchor=POS, canon_seq=canon,
               plans={"492": {"stops": _stops(saved)}})
    assert res[0]["kind"] == "canon_divergence"
    assert res[0]["risk"] is False


def test_no_position_is_risk(monkeypatch):
    res = _run(monkeypatch, anchor=None, canon_seq=None,
               plans={"492": {"stops": _stops([("A", "pickup")])}})
    assert res[0]["kind"] == "no_position"
    assert res[0]["risk"] is True


def test_no_plan_is_risk(monkeypatch):
    res = _run(monkeypatch, anchor=POS, canon_seq=None, plans={})
    assert res[0]["kind"] == "no_plan"
    assert res[0]["risk"] is True


def test_plan_invalidated_is_risk(monkeypatch):
    res = _run(monkeypatch, anchor=POS, canon_seq=None,
               plans={"492": {"stops": _stops([("A", "pickup")]),
                              "invalidated_at": "2026-06-29T22:00:00+00:00",
                              "invalidated_reason": "ORDER_DELIVERED_ALL"}})
    assert res[0]["kind"] == "plan_invalidated"
    assert res[0]["risk"] is True
    assert res[0]["invalidated_reason"] == "ORDER_DELIVERED_ALL"


def test_coverage_gap_is_risk(monkeypatch):
    # plan pokrywa tylko A, worek = {A,B}
    res = _run(monkeypatch, anchor=POS, canon_seq=None,
               plans={"492": {"stops": _stops([("A", "pickup"), ("A", "dropoff")])}})
    assert res[0]["kind"] == "coverage_gap"
    assert res[0]["risk"] is True
    assert res[0]["missing"] == ["B"]


def test_single_order_courier_skipped(monkeypatch):
    monkeypatch.setattr(PR, "_start_anchor", lambda *a, **k: POS)
    res = G.evaluate(orders_state=_os(oids=("A",)), gps_positions={},
                     plans={"492": {"stops": _stops([("A", "pickup")])}}, write=False)
    assert res == []  # 1-zleceniowy pomijany (carried-first dotyczy worków)


def test_carried_first_smell_pure():
    saved = [["A", "dropoff"], ["B", "pickup"], ["B", "dropoff"]]
    canon = [["B", "pickup"], ["A", "dropoff"], ["B", "dropoff"]]
    assert G._carried_first_smell(saved, canon) is True
    # gdy kanon też dowozi A przed odbiorem B → nie ma rozjazdu, brak smell
    assert G._carried_first_smell(saved, saved) is False
