"""F3 (audyt Ziomka 2026-06-28) — testy EFEKTU ON≠OFF dla LIVE flag rządzących
TWARDYMI regułami feasibility / plan-recheck, dopiętych do ETAP4_DECISION_FLAGS.

Domknięcie ETAP4-gap: HARD_TIER_BAG_CAP / R_RETURN_TO_RESTAURANT_VETO /
PLAN_RECHECK_TIER_DWELL były POZA rejestrem (poza fingerprint-parytetem cross-proces,
izolacją conftest i flag_registry) i bez testu efektu — klasa, która przepuściła
ENABLE_BEST_EFFORT_OBJM_R6_KEY. Pozostałe 3 flagi z F3 (PACZKA_R6_THERMAL_EXEMPT /
NO_GPS_EQUAL_TREATMENT / OBJM_LEXR6_SELECT) mają już własne testy efektu.

Wzorzec (lekcja #186 / test_gps_age_discount / test_paczka_r6_exempt): toggluj flagę
ON↔OFF i asertuj zmianę decyzji (verdict / reason / metryka / zmienna decyzyjna).
"""
import sys
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

import pytest

from dispatch_v2 import common as C
from dispatch_v2.feasibility_v2 import check_feasibility_v2
from dispatch_v2.route_simulator_v2 import OrderSim
from dispatch_v2 import route_simulator_v2 as rs


@pytest.fixture(autouse=True)
def _disable_v325_schedule(monkeypatch):
    # jak test_paczka_r6_exempt — hartowanie grafiku wymaga shift-info, off dla izolacji
    monkeypatch.setattr(C, "ENABLE_V325_SCHEDULE_HARDENING", False, raising=False)


class _FakeMatrix:
    def __init__(self, duration_s):
        self.duration_s = duration_s

    def __call__(self, points_a, points_b):
        n = len(points_a)
        return [[{"duration_s": self.duration_s, "osrm_fallback": False}
                 for _ in range(n)] for _ in range(n)]


def _mock_osrm(monkeypatch, duration_s=120):
    # monkeypatch (NIE bezpośrednie przypisanie) → auto-restore po teście; bezpośrednie
    # set rs.osrm_client.table wyciekał globalnie i psuł order-zależny test_working_override.
    monkeypatch.setattr(rs.osrm_client, "table", _FakeMatrix(duration_s))

    class _H:
        def __call__(self, a, b):
            return 2.0
    monkeypatch.setattr(rs.osrm_client, "haversine", _H())


def _load_flags_with(monkeypatch, overrides):
    """Zachowaj żywe (conftest-stripped) flags.json + nałóż override — nie gub
    pozostałych flag (check_feasibility_v2 czyta ich wiele przez load_flags/C.flag)."""
    _orig = C.load_flags
    monkeypatch.setattr(C, "load_flags", lambda: {**_orig(), **overrides})


def _force_cflag(monkeypatch, name, on):
    _orig = C.flag

    def fake(n, default=None):
        if n == name:
            return on
        return _orig(n, default)
    monkeypatch.setattr(C, "flag", fake)


def _bag_food(oid, picked_min_ago, now):
    return OrderSim(order_id=oid, pickup_coords=(53.12, 23.14),
                    delivery_coords=(53.15, 23.17), status="picked_up",
                    picked_up_at=now - timedelta(minutes=picked_min_ago))


def _new_food(oid, now):
    return OrderSim(order_id=oid, pickup_coords=(53.13, 23.15),
                    delivery_coords=(53.14, 23.16), status="assigned",
                    pickup_ready_at=now)


# ─── ENABLE_HARD_TIER_BAG_CAP (feasibility_v2:464, czytane przez load_flags) ──────
# Twardy cap LICZBY zleceń w worku per tier (slow=4). bag_after > cap → HARD NO.
def test_hard_tier_bag_cap_ON_rejects_over_cap(monkeypatch):
    _mock_osrm(monkeypatch)
    now = datetime(2026, 6, 28, 10, 0, tzinfo=timezone.utc)
    bag = [_bag_food(f"B{i}", 2, now) for i in range(4)]   # slow cap=4 → bag_after 5>4
    new = _new_food("NEW", now)
    _load_flags_with(monkeypatch, {"ENABLE_HARD_TIER_BAG_CAP": True})
    v, reason, m, _ = check_feasibility_v2(
        courier_pos=(53.0, 23.0), bag=bag, new_order=new, now=now, courier_tier="slow")
    assert m.get("would_hard_cap") is True
    assert v == "NO" and "hard_tier_bag_cap" in reason, f"ON powinien odrzucić cap-em; got {v}/{reason}"


def test_hard_tier_bag_cap_OFF_no_cap_reject(monkeypatch):
    _mock_osrm(monkeypatch)
    now = datetime(2026, 6, 28, 10, 0, tzinfo=timezone.utc)
    bag = [_bag_food(f"B{i}", 2, now) for i in range(4)]
    new = _new_food("NEW", now)
    _load_flags_with(monkeypatch, {"ENABLE_HARD_TIER_BAG_CAP": False})
    v, reason, m, _ = check_feasibility_v2(
        courier_pos=(53.0, 23.0), bag=bag, new_order=new, now=now, courier_tier="slow")
    assert m.get("would_hard_cap") is True               # metryka liczona ZAWSZE
    assert "hard_tier_bag_cap" not in reason, f"OFF NIE powinien odrzucać cap-em; got {v}/{reason}"


# ─── ENABLE_R_RETURN_TO_RESTAURANT_VETO (feasibility_v2:905) ──────────────────────
# Flaga gate'uje instrumentację metryki return_to_restaurant (kara potem w pipeline).
def _return_metric(monkeypatch, on, now):
    _mock_osrm(monkeypatch)
    bag = [_bag_food("CARRY", 5, now)]
    new = _new_food("NEW", now)
    _force_cflag(monkeypatch, "ENABLE_R_RETURN_TO_RESTAURANT_VETO", on)
    _, _, m, _ = check_feasibility_v2(
        courier_pos=(53.0, 23.0), bag=bag, new_order=new, now=now)
    return m


def test_return_veto_ON_sets_metric(monkeypatch):
    now = datetime(2026, 6, 28, 10, 0, tzinfo=timezone.utc)
    m = _return_metric(monkeypatch, True, now)
    assert "return_to_restaurant" in m, "ON powinien wystawić metrykę return_to_restaurant"


def test_return_veto_OFF_no_metric(monkeypatch):
    now = datetime(2026, 6, 28, 10, 0, tzinfo=timezone.utc)
    m = _return_metric(monkeypatch, False, now)
    assert "return_to_restaurant" not in m, "OFF NIE powinien wystawiać metryki (block pominięty)"


# ─── ENABLE_PLAN_RECHECK_TIER_DWELL (plan_recheck:668) ────────────────────────────
# ON → dwell tier-aware (gold dropoff 1.5); OFF → default route_simulator (3.5).
# Przechwytujemy dwell_dropoff przekazany do simulate_bag_route_v2 (l.703).
def _capture_dwell(monkeypatch, on):
    from dispatch_v2 import plan_recheck as PR
    from dispatch_v2 import route_simulator_v2 as R
    from dispatch_v2 import courier_resolver as CR
    now = datetime(2026, 6, 28, 12, 0, tzinfo=timezone.utc)

    monkeypatch.setattr(CR, "_load_courier_tiers", lambda: {"99": {"bag": {"tier": "gold"}}})
    monkeypatch.setattr(PR, "_start_anchor", lambda *a, **k: ((53.1, 23.1), now, "gps_pwa"))
    monkeypatch.setattr(C, "flag",
                        lambda name, default=False: on if name == "ENABLE_PLAN_RECHECK_TIER_DWELL" else default)

    cap = {}

    class _FakeP:
        sequence = ["A"]
        sla_violations = 0
        total_duration_min = 10.0
        max_carried_age = 0.0
        o2_score = None
        pickup_at = {}
        predicted_delivered_at = {}   # puste → _gen_one_bag_plan zwróci False przed zapisem (zero IO)

    def fake_sim(pos, bag, newo, **kw):
        cap["dwell_dropoff"] = kw.get("dwell_dropoff")
        cap["dwell_pickup"] = kw.get("dwell_pickup")
        return _FakeP()
    monkeypatch.setattr(R, "simulate_bag_route_v2", fake_sim)

    orders = {"A": {"status": "picked_up", "pickup_coords": [53.11, 23.14],
                    "delivery_coords": [53.12, 23.13], "courier_id": "99"}}
    PR._gen_one_bag_plan("99", ["A"], orders, {}, now, R)
    return cap


def test_plan_recheck_tier_dwell_ON_uses_tier_dwell(monkeypatch):
    cap = _capture_dwell(monkeypatch, on=True)
    assert cap["dwell_dropoff"] == C.dwell_for_tier("gold")[1]   # 1.5 (gold, rekalibr. 10.06)


def test_plan_recheck_tier_dwell_OFF_uses_default(monkeypatch):
    cap = _capture_dwell(monkeypatch, on=False)
    assert cap["dwell_dropoff"] == rs.DWELL_DROPOFF_MIN          # 3.5 default route_simulator


def test_plan_recheck_tier_dwell_ON_differs_from_OFF(monkeypatch):
    on = _capture_dwell(monkeypatch, on=True)["dwell_dropoff"]
    off = _capture_dwell(monkeypatch, on=False)["dwell_dropoff"]
    assert on != off, f"flaga musi zmieniać dwell dropoff; ON={on} OFF={off}"


# ─── Sanity rejestracji: 6 flag F3 w ETAP4 + stała-fallback ───────────────────────
def test_f3_six_flags_registered_with_const():
    for name in ("ENABLE_HARD_TIER_BAG_CAP", "ENABLE_PACZKA_R6_THERMAL_EXEMPT",
                 "ENABLE_R_RETURN_TO_RESTAURANT_VETO", "ENABLE_PLAN_RECHECK_TIER_DWELL",
                 "ENABLE_NO_GPS_EQUAL_TREATMENT", "ENABLE_OBJM_LEXR6_SELECT"):
        assert name in C.ETAP4_DECISION_FLAGS, f"{name} poza rejestrem ETAP4"
        assert hasattr(C, name), f"brak stałej-fallback common.{name}"
