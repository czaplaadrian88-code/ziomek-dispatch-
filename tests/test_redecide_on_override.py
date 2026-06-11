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


# ---- reason='pickup' (redecide po ODEBRANE, 2026-06-11) ----
# Case Gabriel cid=179: plan zdecydowany 5 s przed wpisem statusu 'odebrane'
# (reconcile lag ~1 min) zostawał z odbiorami przed niesionym do następnego
# 5-min ticku. Hook w _update_plan_on_picked_up woła redecide(reason='pickup');
# bramka: pokrycie NIE wystarcza, decyduje aktualność bag_signature.

def _sig(monkeypatch, value):
    monkeypatch.setattr(PR, "_bag_signature", lambda oids, st: value)


def test_pickup_flag_off_noop(monkeypatch):
    monkeypatch.setattr(PR, "ENABLE_IMMEDIATE_REDECIDE_ON_PICKUP", False)
    monkeypatch.setattr(PR, "ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE", True)  # nie dotyczy
    calls = _patch_gen(monkeypatch)
    assert PR.redecide_courier("9", orders_state=ORDERS, reason="pickup") is False
    assert calls == []


def test_pickup_redecides_on_stale_signature(monkeypatch):
    monkeypatch.setattr(PR, "ENABLE_IMMEDIATE_REDECIDE_ON_PICKUP", True)
    covering = {"stops": [{"order_id": "o1"}, {"order_id": "o2"}],
                "bag_signature": "o1:0|o2:0", "invalidated_at": None}
    monkeypatch.setattr(plan_manager, "load_plan", lambda c: covering)
    monkeypatch.setattr(PR, "_load_gps_positions", lambda: {})
    _sig(monkeypatch, "o1:1|o2:0")   # o1 właśnie odebrane → sygnatura inna
    calls = _patch_gen(monkeypatch, ret=True)
    assert PR.redecide_courier("9", orders_state=ORDERS, reason="pickup") is True
    assert calls == [("9", ["o1", "o2"])]


def test_pickup_noop_when_signature_current(monkeypatch):
    monkeypatch.setattr(PR, "ENABLE_IMMEDIATE_REDECIDE_ON_PICKUP", True)
    covering = {"stops": [{"order_id": "o1"}, {"order_id": "o2"}],
                "bag_signature": "o1:1|o2:0", "invalidated_at": None}
    monkeypatch.setattr(plan_manager, "load_plan", lambda c: covering)
    _sig(monkeypatch, "o1:1|o2:0")   # plan już zdecydowany PO odebraniu
    calls = _patch_gen(monkeypatch)
    assert PR.redecide_courier("9", orders_state=ORDERS, reason="pickup") is False
    assert calls == []


def test_override_keeps_coverage_noop_despite_stale_signature(monkeypatch):
    # Plan z propozycji NIE ma własnej bag_signature (zapis dziedziczy starą) —
    # override musi zostać przy semantyce 'pokrywa → no-op', inaczej redecide
    # nadpisywałby świeże trasy z propozycji.
    monkeypatch.setattr(PR, "ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE", True)
    covering = {"stops": [{"order_id": "o1"}, {"order_id": "o2"}],
                "bag_signature": None, "invalidated_at": None}
    monkeypatch.setattr(plan_manager, "load_plan", lambda c: covering)
    _sig(monkeypatch, "o1:1|o2:0")
    calls = _patch_gen(monkeypatch)
    assert PR.redecide_courier("9", orders_state=ORDERS, reason="override") is False
    assert calls == []
