"""Migracja B2 (2026-07-18) — ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION
env-frozen → decision_flag/flags.json (KANON §9 B2: pw OFF vs tick ON = mruganie).

Kontrakty:
  1. ON≠OFF: gałąź committed tie-break w _gen_one_bag_plan (:~839) realnie
     zmienia zapisany plan, gdy committed-aware wariant wygrywa bez pogorszenia
     SLA (harness lustrzany do test_planner_k15 — wstrzyknięty fake R).
  2. Guard anty-regresyjny NIETKNIĘTY: committed-aware wariant pogarszający
     sla_violations NIE jest adoptowany (ON zachowuje baseline).
  3. Wiring D3 fala A: flaga w _D3_FALA_A_FLAGS (hot-reload w pw przez
     _refresh_d3_fala_a_flags), w ETAP4_DECISION_FLAGS, const-fallback w common
     = True (steady-state json); refresh nadpisuje TYLKO gdy klucz w flags.json
     (kontrakt: monkeypatch stałej przeżywa conftest-strip).
"""
import types
from datetime import datetime, timedelta, timezone

from dispatch_v2 import common as C
from dispatch_v2 import plan_recheck as PR
from dispatch_v2 import route_simulator_v2 as R2

NOW = datetime(2026, 7, 18, 12, 0, 0, tzinfo=timezone.utc)


def _flag_stub(overrides):
    real = C.flag

    def _f(name, default=None):
        if name in overrides:
            return overrides[name]
        return real(name, default)
    return _f


class _FakePlan:
    def __init__(self, oid, pickup_shift_min=0, sla_violations=0):
        t = NOW + timedelta(minutes=pickup_shift_min)
        self.sequence = [oid]
        self.pickup_at = {oid: t}
        self.predicted_delivered_at = {oid: t + timedelta(minutes=10)}
        self.sla_violations = sla_violations
        self.total_duration_min = 10.0 + pickup_shift_min
        self.max_carried_age = 0.0
        self.o2_score = None
        self.strategy = "fake"
        self.osrm_fallback_used = False


def _fake_R(ck_sla_violations=0):
    """Fake symulator: wariant committed-aware (sim z czas_kuriera_warsaw
    ustawionym przez gałąź) → plan z odbiorem +7 min; baseline → NOW."""
    def _sim(pos, bag, new, **kw):
        committed = getattr(new, "czas_kuriera_warsaw", None) is not None or any(
            getattr(b, "czas_kuriera_warsaw", None) is not None for b in (bag or []))
        oid = getattr(new, "order_id", "o1")
        if committed:
            return _FakePlan(oid, pickup_shift_min=7,
                             sla_violations=ck_sla_violations)
        return _FakePlan(oid, pickup_shift_min=0, sla_violations=0)

    return types.SimpleNamespace(
        OrderSim=lambda **kw: types.SimpleNamespace(**kw),
        DWELL_PICKUP_MIN=R2.DWELL_PICKUP_MIN,
        DWELL_DROPOFF_MIN=R2.DWELL_DROPOFF_MIN,
        simulate_bag_route_v2=_sim,
    )


def _run_gen(monkeypatch, committed_on, ck_sla_violations=0):
    fakeR = _fake_R(ck_sla_violations=ck_sla_violations)
    orders_state = {"o1": {
        "delivery_coords": [53.12, 23.15], "pickup_coords": [53.13, 23.16],
        "status": "assigned", "czas_kuriera_warsaw": "2026-07-18 14:07",
    }}
    monkeypatch.setattr(PR, "_start_anchor",
                        lambda cid, oids, os_, gps, now: ((53.13, 23.16), None, "test"))
    monkeypatch.setattr(PR, "ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION", committed_on)
    monkeypatch.setattr(PR, "ENABLE_PLAN_CANON_ORDER_INVARIANTS", False)
    monkeypatch.setattr(PR, "ENABLE_PICKUP_REFLOOR", False)
    saved = {}
    monkeypatch.setattr(PR.plan_manager, "save_plan",
                        lambda cid, body, **kw: saved.update(cid=cid, body=body))
    monkeypatch.setattr(C, "flag", _flag_stub({
        "ENABLE_PLANNER_UNIFIED": False,
        "ENABLE_PLANNER_UNIFIED_SHADOW": False,
        "ENABLE_PLAN_RECHECK_TIER_DWELL": False,
        "ENABLE_OBJ_O2_PRIMARY": False,
    }))
    ok = PR._gen_one_bag_plan("484", ["o1"], orders_state, {}, NOW, fakeR,
                              expected_version=0)
    return ok, saved


def _first_pickup_predicted_at(saved):
    stops = saved["body"]["stops"]
    pickups = [s for s in stops if s["type"] == "pickup"]
    assert pickups, f"brak stopu pickup w body: {stops}"
    return pickups[0]["predicted_at"]


def test_on_adopts_committed_aware_plan_off_keeps_base(monkeypatch):
    """ON≠OFF: committed-aware plan (odbiór +7 min, SLA nie gorsze) adoptowany
    TYLKO gdy flaga ON — zapisany plan różni się między ON i OFF."""
    ok_off, saved_off = _run_gen(monkeypatch, committed_on=False)
    assert ok_off is True and saved_off
    ok_on, saved_on = _run_gen(monkeypatch, committed_on=True)
    assert ok_on is True and saved_on
    t_off = _first_pickup_predicted_at(saved_off)
    t_on = _first_pickup_predicted_at(saved_on)
    assert t_off != t_on, "flaga ON musi zmieniac zapisany plan (ON≠OFF)"
    assert t_off == NOW.isoformat()
    assert t_on == (NOW + timedelta(minutes=7)).isoformat()


def test_on_guard_rejects_committed_plan_worsening_sla(monkeypatch):
    """Guard anty-regresyjny: committed-aware wariant z gorszym sla_violations
    NIE jest adoptowany nawet przy ON (plan == baseline)."""
    ok, saved = _run_gen(monkeypatch, committed_on=True, ck_sla_violations=1)
    assert ok is True and saved
    assert _first_pickup_predicted_at(saved) == NOW.isoformat()


def test_d3_wiring_hot_reload_and_registry(monkeypatch):
    """Flaga w liście D3 (hot-reload pw), w ETAP4, const=True; refresh
    nadpisuje globalę TYLKO gdy klucz obecny w flags.json."""
    name = "ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION"
    assert name in PR._D3_FALA_A_FLAGS
    assert name in C.ETAP4_DECISION_FLAGS
    assert getattr(C, name) is True  # const-fallback = steady-state json
    # klucz OBECNY w flags.json → refresh nadpisuje globalę modułu
    monkeypatch.setattr(PR, name, False)
    monkeypatch.setattr(C, "load_flags", lambda: {name: True})
    PR._refresh_d3_fala_a_flags()
    assert getattr(PR, name) is True
    # klucz NIEOBECNY (conftest-strip / pre-deploy) → monkeypatch przeżywa
    monkeypatch.setattr(PR, name, False)
    monkeypatch.setattr(C, "load_flags", lambda: {})
    PR._refresh_d3_fala_a_flags()
    assert getattr(PR, name) is False
