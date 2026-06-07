"""F3 — natychmiastowa re-decyzja na override (redecide_courier).

Ziomek układa trasę od razu na zmianę worka (override/reassign), bez czekania na
5-min tick. Samo-bramkujące: NO-OP gdy ważny plan już pokrywa worek (ochrona
trasy z propozycji). Flaga OFF = zawsze no-op. Best-effort (nie rzuca).
"""
from dispatch_v2 import plan_recheck as PR
from dispatch_v2 import plan_manager


ORDERS = {
    "o1": {"courier_id": "9", "status": "picked_up"},
    "o2": {"courier_id": "9", "status": "assigned"},
    "x9": {"courier_id": "7", "status": "assigned"},  # inny kurier — ignorowany
}


def _patch_gen(monkeypatch, ret=True):
    calls = []
    def fake_gen(cid, oids, *a, **k):
        calls.append((cid, sorted(oids)))
        return ret
    monkeypatch.setattr(PR, "_gen_one_bag_plan", fake_gen)
    return calls


def test_flag_off_noop(monkeypatch):
    monkeypatch.setattr(PR, "ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE", False)
    calls = _patch_gen(monkeypatch)
    assert PR.redecide_courier("9", orders_state=ORDERS) is False
    assert calls == []


def test_generates_when_no_plan(monkeypatch):
    monkeypatch.setattr(PR, "ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE", True)
    monkeypatch.setattr(plan_manager, "load_plan", lambda c: None)
    monkeypatch.setattr(PR, "_load_gps_positions", lambda: {})
    calls = _patch_gen(monkeypatch, ret=True)
    assert PR.redecide_courier("9", orders_state=ORDERS) is True
    # policzył pełny worek kuriera 9 (bez zlecenia kuriera 7)
    assert calls == [("9", ["o1", "o2"])]


def test_noop_when_plan_covers_bag(monkeypatch):
    monkeypatch.setattr(PR, "ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE", True)
    covering = {"stops": [{"order_id": "o1"}, {"order_id": "o2"}], "invalidated_at": None}
    monkeypatch.setattr(plan_manager, "load_plan", lambda c: covering)
    calls = _patch_gen(monkeypatch)
    assert PR.redecide_courier("9", orders_state=ORDERS) is False
    assert calls == []  # NIE nadpisuje pokrywającego planu (np. propozycji)


def test_generates_when_plan_partial(monkeypatch):
    monkeypatch.setattr(PR, "ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE", True)
    partial = {"stops": [{"order_id": "o1"}], "invalidated_at": None}  # brak o2
    monkeypatch.setattr(plan_manager, "load_plan", lambda c: partial)
    monkeypatch.setattr(PR, "_load_gps_positions", lambda: {})
    calls = _patch_gen(monkeypatch, ret=True)
    assert PR.redecide_courier("9", orders_state=ORDERS) is True
    assert calls == [("9", ["o1", "o2"])]


def test_empty_bag_noop(monkeypatch):
    monkeypatch.setattr(PR, "ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE", True)
    calls = _patch_gen(monkeypatch)
    assert PR.redecide_courier("999", orders_state=ORDERS) is False
    assert calls == []


def test_never_raises(monkeypatch):
    monkeypatch.setattr(PR, "ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE", True)
    def boom(c):
        raise RuntimeError("load fail")
    monkeypatch.setattr(plan_manager, "load_plan", boom)
    # wyjątek wewnątrz → False, nie propaguje (best-effort dla hot-path panel_watcher)
    assert PR.redecide_courier("9", orders_state=ORDERS) is False
