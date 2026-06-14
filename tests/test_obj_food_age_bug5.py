"""Sprint OBJ FOOD-AGE (2026-06-14) — człon świeżości DOSTAWY w objective.

BUG#5 (Jakub OL 14.06, courier-routing-bug-foodage-2026-06-14): silnik OR-Tools
łańcuchował NIEGOTOWY odbiór przed GOTOWĄ dostawą (kurier stał jałowo, gotowe
jedzenie wieziona zimna na końcu), bo cel = arc + span, a makespan identyczny w
obu kolejnościach → cel redukował się do „min kilometrów". R6 soft-deadline NIE
łapał (obie dostawy < ready+sla → kara 0 w obu).

Fix: flaga ENABLE_OBJ_DELIVERY_FOOD_AGE rekonfiguruje delivery soft upper bound
z R6 (anchor ready+sla, coeff 100) na food-age (anchor = czas gotowości, sla=0,
gentle coeff) — liniowa kara za wiek niesionego jedzenia, na wymiarze Time
(widzi realny harmonogram z czekaniem).

Tests:
- FA-T1: flag OFF → kolejność bez zmian (A: niegotowy odbiór 2-gi = bug baseline,
         guard że OFF nie rusza produkcji).
- FA-T2: flag ON → gotowa dostawa PRZED niegotowym odbiorem (B: fix).
- FA-T3: flag rekonfiguruje kotwicę delivery_soft_deadlines (ON bez SLA-grace).
"""
from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import patch
from zoneinfo import ZoneInfo

from dispatch_v2 import common, tsp_solver
from dispatch_v2 import route_simulator_v2 as rs

_W = ZoneInfo("Europe/Warsaw")


def _w(h, m):
    return datetime(2026, 6, 14, h, m, tzinfo=_W).astimezone(timezone.utc)


def _jakub_case():
    """Realny worek Jakuba 14.06: gotowa dostawa (Rukola→Piastowska, ck 12:57) +
    niegotowy odbiór (Paradiso→Xawerego, ck 13:14, gotowe dopiero 13:14)."""
    o581 = rs.OrderSim(
        "480581", (53.137686, 23.168566), (53.1320984, 23.1915573),
        None, "assigned", pickup_ready_at=_w(12, 57))
    o568 = rs.OrderSim(
        "480568", (53.126106, 23.162215), (53.1485181, 23.1976805),
        None, "assigned", pickup_ready_at=_w(13, 14))
    o581.czas_kuriera_warsaw = _w(12, 57).isoformat()
    o568.czas_kuriera_warsaw = _w(13, 14).isoformat()
    courier_pos = (53.137686, 23.168566)
    return courier_pos, [o581], o568, _w(12, 57)


def _ordered_events(plan):
    ev = [(t, "PICKUP", o) for o, t in plan.pickup_at.items()]
    ev += [(t, "DROP", o) for o, t in plan.predicted_delivered_at.items()]
    ev.sort(key=lambda e: e[0])
    return ev


def test_fa_t1_flag_off_keeps_legacy_order():
    """Flag OFF → silnik dalej wybiera A (niegotowy odbiór Paradiso jako 2-gi
    przystanek) — baseline buga, gwarancja braku regresu produkcji."""
    courier_pos, bag, new_order, now = _jakub_case()
    with patch.object(common, "ENABLE_OBJ_DELIVERY_FOOD_AGE", False):
        plan = rs.simulate_bag_route_v2(courier_pos, bag, new_order, now=now, sla_minutes=35)
    assert plan.strategy == "ortools", f"oczekiwano ścieżki OR-Tools, got {plan.strategy}"
    second = _ordered_events(plan)[1]
    assert second[2] == "480568" and second[1] == "PICKUP", (
        f"flag OFF: 2-gi przystanek powinien zostać odbiorem Paradiso (bug), got {second}")


def test_fa_t2_flag_on_delivers_ready_before_unready_pickup():
    """Flag ON → gotowa dostawa Piastowska (480581) PRZED odbiorem niegotowego
    Paradiso (480568). To kolejność którą kurier wybrał ręcznie."""
    courier_pos, bag, new_order, now = _jakub_case()
    with patch.object(common, "ENABLE_OBJ_DELIVERY_FOOD_AGE", True):
        plan = rs.simulate_bag_route_v2(courier_pos, bag, new_order, now=now, sla_minutes=35)
    assert plan.strategy == "ortools", f"oczekiwano ścieżki OR-Tools, got {plan.strategy}"
    ev = _ordered_events(plan)
    drop_ready = next(i for i, e in enumerate(ev) if e[1] == "DROP" and e[2] == "480581")
    pickup_unready = next(i for i, e in enumerate(ev) if e[1] == "PICKUP" and e[2] == "480568")
    assert drop_ready < pickup_unready, (
        f"flag ON: gotowa dostawa 480581 powinna być przed niegotowym odbiorem "
        f"480568; sekwencja={[(e[1], e[2]) for e in ev]}")


def _capture_solver(captured):
    def _stub(**kwargs):
        captured.append(kwargs)
        return None
    return _stub


def test_fa_t3_food_age_is_additive_to_r6():
    """Food-age ON: R6 (delivery_soft_deadlines) NIEZMIENIONE + OSOBNE
    delivery_food_age_penalties (kotwica gotowości, sla=0). Food-age DODAJE drugi
    bound, NIE zastępuje R6 (poprzedni redesign zastępujący regresował SLA 9.4%)."""
    courier_pos, bag, new_order, now = _jakub_case()

    cap_off: list = []
    with patch.object(common, "ENABLE_OBJ_DELIVERY_FOOD_AGE", False), \
         patch.object(common, "ENABLE_OBJ_R6_SOFT_DEADLINE", True), \
         patch.object(tsp_solver, "solve_tsp_with_constraints", _capture_solver(cap_off)):
        rs.simulate_bag_route_v2(courier_pos, bag, new_order, now=now, sla_minutes=35)

    cap_on: list = []
    with patch.object(common, "ENABLE_OBJ_DELIVERY_FOOD_AGE", True), \
         patch.object(common, "ENABLE_OBJ_R6_SOFT_DEADLINE", True), \
         patch.object(tsp_solver, "solve_tsp_with_constraints", _capture_solver(cap_on)):
        rs.simulate_bag_route_v2(courier_pos, bag, new_order, now=now, sla_minutes=35)

    def _vals(cap, key):
        lst = cap[0].get(key)
        return [v for v in lst if v is not None] if lst else []

    # 1. R6 (delivery_soft_deadlines) IDENTYCZNE OFF↔ON — food-age go nie rusza
    r6_off = sorted(v[0] for v in _vals(cap_off, "delivery_soft_deadlines"))
    r6_on = sorted(v[0] for v in _vals(cap_on, "delivery_soft_deadlines"))
    assert r6_off == r6_on and len(r6_on) == 2, "R6 musi zostać nietknięte przez food-age"
    assert {v[1] for v in _vals(cap_on, "delivery_soft_deadlines")} == {common.OBJ_R6_DEADLINE_PENALTY_COEFF}

    # 2. food-age = OSOBNA lista, tylko ON, coeff food-age
    assert cap_off[0].get("delivery_food_age_penalties") is None
    fa = _vals(cap_on, "delivery_food_age_penalties")
    assert len(fa) == 2
    assert {v[1] for v in fa} == {common.OBJ_DELIVERY_FOOD_AGE_COEFF}
    # 3. food-age bound (ready, sla=0) < R6 deadline (ready+sla) → dwukawałkowa kara
    assert max(v[0] for v in fa) < min(r6_on), \
        f"food-age (ready) musi być < R6 (ready+sla); fa={fa} r6={r6_on}"


# ─── Forward shadow comparator: thread-local override ──────────────────

def test_fa_t4_food_age_override_toggles_and_restores():
    """food_age_override wymusza flagę per-wątek i przywraca (też po wyjątku)."""
    assert common.decision_flag("ENABLE_OBJ_DELIVERY_FOOD_AGE") is False
    with common.food_age_override(True):
        assert common.decision_flag("ENABLE_OBJ_DELIVERY_FOOD_AGE") is True
    assert common.decision_flag("ENABLE_OBJ_DELIVERY_FOOD_AGE") is False
    try:
        with common.food_age_override(True):
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    assert common.decision_flag("ENABLE_OBJ_DELIVERY_FOOD_AGE") is False, \
        "override musi się przywrócić nawet po wyjątku"


def test_fa_t5_override_is_the_live_toggle_comparator_uses():
    """Komparator liczy wariant ON przez food_age_override(True) — pod override
    silnik zwraca B, bez/OFF → A. Dowód że override steruje realnym planem."""
    courier_pos, bag, new_order, now = _jakub_case()

    with common.food_age_override(True):
        plan_on = rs.simulate_bag_route_v2(courier_pos, bag, new_order, now=now, sla_minutes=35)
    plan_off = rs.simulate_bag_route_v2(courier_pos, bag, new_order, now=now, sla_minutes=35)

    on_second = _ordered_events(plan_on)[1]
    off_second = _ordered_events(plan_off)[1]
    assert on_second[1] == "DROP" and on_second[2] == "480581", \
        f"override(True) → B (2-gi = dostawa Piastowska); got {on_second}"
    assert off_second[1] == "PICKUP" and off_second[2] == "480568", \
        f"bez override → A (2-gi = odbiór Paradiso); got {off_second}"
    # interleaving się różni mimo identycznej kolejności DOSTAW (deliveries-only)
    on_order = [(e[1], e[2]) for e in _ordered_events(plan_on)]
    off_order = [(e[1], e[2]) for e in _ordered_events(plan_off)]
    assert on_order != off_order
    assert list(plan_on.sequence) == list(plan_off.sequence), \
        "kolejność DOSTAW identyczna w A i B — dowód że 'changed' musi patrzeć na interleaving"
