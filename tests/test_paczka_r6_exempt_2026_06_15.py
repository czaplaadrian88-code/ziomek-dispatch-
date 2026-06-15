"""FIRMOWE PACZKI wyłączone z reguły 35min (Adrian 2026-06-15).

Reguła domenowa: firmowe paczki (Dr Tusz/tonery, Nadajesz.pl, PACZKA_ADDRESS_IDS)
to NIE gorące jedzenie → NIE podlegają R6 termik 35min ani bramce SLA 35min,
także w MIESZANYM worku. Jedzeniówka w tym samym worku DALEJ ma 35min (no regression).

Flaga ENABLE_PACZKA_R6_THERMAL_EXEMPT. Standalone + pytest.
"""
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, '/root/.openclaw/workspace/scripts')

from dispatch_v2.feasibility_v2 import check_feasibility_v2
from dispatch_v2.route_simulator_v2 import OrderSim
from dispatch_v2 import route_simulator_v2 as rs
from dispatch_v2 import common as _C
import pytest as _pytest


@_pytest.fixture(autouse=True)
def _disable_v325_schedule(monkeypatch):
    monkeypatch.setattr(_C, "ENABLE_V325_SCHEDULE_HARDENING", False, raising=False)


class FakeMatrix:
    def __init__(self, duration_s):
        self.duration_s = duration_s

    def __call__(self, points_a, points_b):
        n = len(points_a)
        row = lambda: [{"duration_s": self.duration_s, "osrm_fallback": False} for _ in range(n)]
        return [row() for _ in range(n)]


def _setup_mock(duration_s):
    rs.osrm_client.table = FakeMatrix(duration_s)

    class FakeHaversine:
        def __call__(self, a, b):
            return 2.0
    rs.osrm_client.haversine = FakeHaversine()


def _force_flag(monkeypatch, on):
    orig = _C.flag

    def fake(name, default=None):
        if name == "ENABLE_PACZKA_R6_THERMAL_EXEMPT":
            return on
        return orig(name, default)
    monkeypatch.setattr(_C, "flag", fake)


def _bag_item(oid, picked_min_ago, now, address_id=None):
    o = OrderSim(
        order_id=oid,
        pickup_coords=(53.12, 23.14),
        delivery_coords=(53.15, 23.17),
        status="picked_up",
        picked_up_at=now - timedelta(minutes=picked_min_ago),
    )
    if address_id is not None:
        o.address_id = address_id
    return o


def _new_food(oid, now):
    return OrderSim(
        order_id=oid,
        pickup_coords=(53.13, 23.15),
        delivery_coords=(53.14, 23.16),
        status="assigned",
        pickup_ready_at=now,
    )


def _assigned_item(oid, ready_min_ago, now, address_id=None):
    """Order PRZYPISANY (jeszcze nieodebrany) z gotowością N min temu → wysoka termika,
    ścieżka HARD-reject (assigned, nie picked_up)."""
    o = OrderSim(
        order_id=oid,
        pickup_coords=(53.12, 23.14),
        delivery_coords=(53.15, 23.17),
        status="assigned",
        pickup_ready_at=now - timedelta(minutes=ready_min_ago),
    )
    if address_id is not None:
        o.address_id = address_id
    return o


def test_paczka_high_thermal_exempt_ON(monkeypatch):
    """Flaga ON: paczka (addr=232 Dr Tusz) z termiką >35 w MIESZANYM worku NIE ustawia
    r6_max/worst, NIE trafia do violations, ląduje w r6_paczka_exempt_oids; jedzeniówka rządzi."""
    _force_flag(monkeypatch, True)
    _setup_mock(duration_s=120)  # 2-min nogi
    now = datetime(2026, 6, 15, 10, 0, tzinfo=timezone.utc)
    bag = [_bag_item("PACZKA1", picked_min_ago=55, now=now, address_id=232)]  # ~55min termik
    new_order = _new_food("FOOD_NEW", now)
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(53.0, 23.0), bag=bag, new_order=new_order, now=now,
    )
    assert "PACZKA1" in metrics.get("r6_paczka_exempt_oids", []), \
        f"paczka powinna być exempt; metrics={metrics.get('r6_paczka_exempt_oids')}"
    assert metrics.get("r6_worst_oid") != "PACZKA1", \
        f"paczka NIE powinna być worst; worst={metrics.get('r6_worst_oid')}"
    viol_oids = [v[0] for v in metrics.get("r6_per_order_violations", [])]
    assert "PACZKA1" not in viol_oids, f"paczka NIE w R6 violations; got {viol_oids}"
    assert metrics.get("r6_max_bag_time_min", 99) <= 35.0, \
        f"r6_max po wyłączeniu paczki powinno odzwierciedlać jedzeniówkę <=35; got {metrics.get('r6_max_bag_time_min')}"
    assert verdict != "NO", f"z paczką-exempt i świeżą jedzeniówką powinno być wykonalne; got {verdict}/{reason}"


def test_paczka_high_thermal_counts_OFF(monkeypatch):
    """Flaga OFF (legacy): paczka z termiką >35 DALEJ liczona — r6_max>35, worst=paczka."""
    _force_flag(monkeypatch, False)
    _setup_mock(duration_s=120)
    now = datetime(2026, 6, 15, 10, 0, tzinfo=timezone.utc)
    bag = [_bag_item("PACZKA1", picked_min_ago=55, now=now, address_id=232)]
    new_order = _new_food("FOOD_NEW", now)
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(53.0, 23.0), bag=bag, new_order=new_order, now=now,
    )
    assert "PACZKA1" not in metrics.get("r6_paczka_exempt_oids", []), "OFF: brak exempt"
    assert metrics.get("r6_max_bag_time_min", 0) > 35.0, \
        f"OFF: paczka 55min powinna dać r6_max>35; got {metrics.get('r6_max_bag_time_min')}"
    assert metrics.get("r6_worst_oid") == "PACZKA1", \
        f"OFF: paczka powinna być worst; got {metrics.get('r6_worst_oid')}"


def test_food_assigned_high_thermal_still_rejected_ON(monkeypatch):
    """NO REGRESSION: flaga ON, JEDZENIÓWKA przypisana (nie paczka) z termiką >35
    DALEJ twardo odrzucana — wyłączenie dotyczy WYŁĄCZNIE paczek."""
    _force_flag(monkeypatch, True)
    _setup_mock(duration_s=120)
    now = datetime(2026, 6, 15, 10, 0, tzinfo=timezone.utc)
    bag = [_assigned_item("FOOD_OLD", ready_min_ago=50, now=now, address_id=None)]
    new_order = _new_food("FOOD_NEW", now)
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(53.0, 23.0), bag=bag, new_order=new_order, now=now,
    )
    assert "FOOD_OLD" not in metrics.get("r6_paczka_exempt_oids", []), "jedzeniówka NIE jest exempt"
    assert metrics.get("r6_max_bag_time_min", 0) > 35.0, \
        f"jedzeniówka 50min DALEJ >35; got {metrics.get('r6_max_bag_time_min')}"
    assert verdict == "NO", f"jedzeniówka >35 (assigned) DALEJ odrzucana; got {verdict}/{reason}"


def test_paczka_assigned_high_thermal_not_rejected_ON(monkeypatch):
    """Flaga ON: paczka PRZYPISANA z termiką >35 NIE jest odrzucana (exempt z R6+SLA hard),
    odblokowuje przyjęcie świeżej jedzeniówki — sedno reguły Adriana."""
    _force_flag(monkeypatch, True)
    _setup_mock(duration_s=120)
    now = datetime(2026, 6, 15, 10, 0, tzinfo=timezone.utc)
    bag = [_assigned_item("PACZKA_ASG", ready_min_ago=50, now=now, address_id=232)]
    new_order = _new_food("FOOD_NEW", now)
    verdict, reason, metrics, plan = check_feasibility_v2(
        courier_pos=(53.0, 23.0), bag=bag, new_order=new_order, now=now,
    )
    assert "PACZKA_ASG" in metrics.get("r6_paczka_exempt_oids", []), \
        f"paczka assigned powinna być exempt; got {metrics.get('r6_paczka_exempt_oids')}"
    viol = [v[0] for v in metrics.get("r6_per_order_violations", [])]
    assert "PACZKA_ASG" not in viol, f"paczka NIE w R6 violations; got {viol}"
    assert verdict != "NO", f"paczka-exempt nie powinna blokować świeżej jedzeniówki; got {verdict}/{reason}"


if __name__ == "__main__":
    import types
    mp = types.SimpleNamespace(setattr=lambda o, n, v, raising=True: setattr(o, n, v))
    # prosty runner bez pytest fixture (monkeypatch ręczny)
    class MP:
        def __init__(self): self._undo = []
        def setattr(self, o, n, v, raising=True):
            self._undo.append((o, n, getattr(o, n, None))); setattr(o, n, v)
        def undo(self):
            for o, n, v in reversed(self._undo): setattr(o, n, v)
    for fn in [test_paczka_high_thermal_exempt_ON, test_paczka_high_thermal_counts_OFF, test_food_high_thermal_still_rejected_ON]:
        m = MP()
        _C.ENABLE_V325_SCHEDULE_HARDENING = False
        try:
            fn(m); print(f"PASS {fn.__name__}")
        finally:
            m.undo()
    print("ALL PASS")
