"""K15 (ADR-R03) — wspólny Planner: testy jednostkowe + integracja plan_recheck.

Kontrakty:
  1. tier_params semantyka SILNIKA == dawny inline feasibility (dwell ZAWSZE
     tier-aware, mult z speed_mult_for_tier) — parytet z konstrukcji, ale
     przybity testem na wypadek dryfu.
  2. tier_params semantyka RE-PLANERA == dawny inline plan_recheck (dwell za
     flagą ENABLE_PLAN_RECHECK_TIER_DWELL, inaczej defaulty symulatora).
  3. plan_bag = czysty passthrough gdy parametry jawne (zero odczytu
     tier_params) + dociąganie z tier_params gdy brak + honorowanie
     wstrzykniętego simulate_fn (kontrakt suity).
  4. Integracja _gen_one_bag_plan: OFF = stara ścieżka (fake R dostaje
     defaulty R.DWELL_*), ON = parametry z core.planner (sentinel przez
     monkeypatch tier_params) i wywołanie przez plan_bag → simulate_fn=R.
  5. SHADOW: główna OFF + shadow ON + rozjazd parametrów → WARNING
     PLANNER_PARAM_MISMATCH; zgodność → cisza.
Flagi czytane realnym mechanizmem (monkeypatch C.flag — jak reszta suity).
"""
import types
from datetime import datetime, timezone

import pytest

from dispatch_v2 import common as C
from dispatch_v2 import plan_recheck as PR
from dispatch_v2 import route_simulator_v2 as R2
from dispatch_v2.core import planner


NOW = datetime(2026, 7, 6, 12, 0, 0, tzinfo=timezone.utc)


def _flag_stub(overrides):
    real = C.flag

    def _f(name, default=None):
        if name in overrides:
            return overrides[name]
        return real(name, default)
    return _f


# ── 1+2: tier_params — obie semantyki 1:1 z dawnymi inline'ami ──────────────

@pytest.mark.parametrize("tier", ["gold", "std", "slow", None])
def test_tier_params_engine_semantics_matches_inline(tier):
    dp, dd, mult = planner.tier_params(tier)
    exp_dp, exp_dd = C.dwell_for_tier(tier)
    assert (dp, dd) == (exp_dp, exp_dd)
    assert mult == C.speed_mult_for_tier(tier)


def test_tier_params_recheck_gate_off_uses_simulator_defaults(monkeypatch):
    monkeypatch.setattr(planner.C, "flag",
                        _flag_stub({"ENABLE_PLAN_RECHECK_TIER_DWELL": False}))
    dp, dd, mult = planner.tier_params("gold", recheck_dwell_gate=True)
    assert (dp, dd) == (R2.DWELL_PICKUP_MIN, R2.DWELL_DROPOFF_MIN)
    assert mult == C.speed_mult_for_tier("gold")


def test_tier_params_recheck_gate_on_uses_tier_dwell(monkeypatch):
    monkeypatch.setattr(planner.C, "flag",
                        _flag_stub({"ENABLE_PLAN_RECHECK_TIER_DWELL": True}))
    dp, dd, _ = planner.tier_params("gold", recheck_dwell_gate=True)
    assert (dp, dd) == C.dwell_for_tier("gold")


# ── 3: plan_bag — passthrough + dociąganie + simulate_fn ────────────────────

def test_plan_bag_explicit_params_pure_passthrough(monkeypatch):
    def _boom(*a, **k):
        raise AssertionError("tier_params NIE może być czytane przy jawnych parametrach")
    monkeypatch.setattr(planner, "tier_params", _boom)
    captured = {}

    def fake_sim(pos, bag, new, **kw):
        captured.update(kw, pos=pos, bag=bag, new=new)
        return "PLAN"
    out = planner.plan_bag((53.1, 23.1), ["b"], "n", NOW, sla_minutes=35,
                           earliest_departure=None, dwell_pickup=1.5,
                           dwell_dropoff=2.5, drive_speed_mult=0.9,
                           simulate_fn=fake_sim)
    assert out == "PLAN"
    assert captured["dwell_pickup"] == 1.5
    assert captured["dwell_dropoff"] == 2.5
    assert captured["drive_speed_mult"] == 0.9
    assert captured["sla_minutes"] == 35
    assert captured["now"] is NOW
    assert captured["pos"] == (53.1, 23.1)


def test_plan_bag_derives_missing_params_from_tier_params(monkeypatch):
    monkeypatch.setattr(planner, "tier_params",
                        lambda tier, recheck_dwell_gate=False: (7.0, 8.0, 0.5))
    captured = {}

    def fake_sim(pos, bag, new, **kw):
        captured.update(kw)
        return "P"
    planner.plan_bag((0, 0), [], "n", NOW, sla_minutes=35,
                     courier_tier="gold", simulate_fn=fake_sim)
    assert (captured["dwell_pickup"], captured["dwell_dropoff"],
            captured["drive_speed_mult"]) == (7.0, 8.0, 0.5)


def test_plan_bag_default_simulate_is_canonical():
    assert planner._R2.simulate_bag_route_v2 is R2.simulate_bag_route_v2


# ── 4+5: integracja _gen_one_bag_plan (chirurgiczne monkeypatche) ───────────

class _FakePlan:
    def __init__(self, oid):
        self.sequence = [oid]
        self.pickup_at = {oid: NOW}
        self.predicted_delivered_at = {oid: NOW}
        self.sla_violations = 0
        self.total_duration_min = 10.0
        self.max_carried_age = 0.0
        self.o2_score = None
        self.strategy = "fake"
        self.osrm_fallback_used = False


def _fake_R(captured):
    def _sim(pos, bag, new, **kw):
        captured.append(dict(kw))
        return _FakePlan(getattr(new, "order_id", "o1"))

    return types.SimpleNamespace(
        OrderSim=lambda **kw: types.SimpleNamespace(**kw),
        DWELL_PICKUP_MIN=R2.DWELL_PICKUP_MIN,
        DWELL_DROPOFF_MIN=R2.DWELL_DROPOFF_MIN,
        simulate_bag_route_v2=_sim,
    )


def _run_gen(monkeypatch, flag_overrides, tier_params_fn=None):
    captured = []
    fakeR = _fake_R(captured)
    orders_state = {"o1": {
        "delivery_coords": [53.12, 23.15], "pickup_coords": [53.13, 23.16],
        "status": "assigned", "czas_kuriera_warsaw": None,
    }}
    monkeypatch.setattr(PR, "_start_anchor",
                        lambda cid, oids, os_, gps, now: ((53.13, 23.16), None, "test"))
    monkeypatch.setattr(PR, "ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION", False)
    monkeypatch.setattr(PR, "ENABLE_PLAN_CANON_ORDER_INVARIANTS", False)
    monkeypatch.setattr(PR, "ENABLE_PICKUP_REFLOOR", False)
    saved = {}
    monkeypatch.setattr(PR.plan_manager, "save_plan",
                        lambda cid, body, **kw: saved.update(
                            cid=cid, body=body,
                            expected_version=kw.get("expected_version")))
    monkeypatch.setattr(PR._C_k15 if hasattr(PR, "_C_k15") else C, "flag",
                        _flag_stub(flag_overrides), raising=False)
    monkeypatch.setattr(C, "flag", _flag_stub(flag_overrides))
    if tier_params_fn is not None:
        monkeypatch.setattr(planner, "tier_params", tier_params_fn)
    ok = PR._gen_one_bag_plan(
        "484", ["o1"], orders_state, {}, NOW, fakeR,
        expected_version=0,
    )
    return ok, captured, saved


def test_gen_off_uses_legacy_inline_defaults(monkeypatch):
    ok, captured, saved = _run_gen(monkeypatch, {
        "ENABLE_PLANNER_UNIFIED": False,
        "ENABLE_PLANNER_UNIFIED_SHADOW": False,
        "ENABLE_PLAN_RECHECK_TIER_DWELL": False,
    })
    assert ok is True and saved
    assert captured, "fake R.simulate musi być wywołane"
    assert captured[0]["dwell_pickup"] == R2.DWELL_PICKUP_MIN
    assert captured[0]["dwell_dropoff"] == R2.DWELL_DROPOFF_MIN


def test_gen_on_params_come_from_core_planner(monkeypatch):
    sentinel = (4.25, 5.75, 0.77)
    ok, captured, saved = _run_gen(
        monkeypatch,
        {"ENABLE_PLANNER_UNIFIED": True},
        tier_params_fn=lambda tier, recheck_dwell_gate=False: sentinel,
    )
    assert ok is True and saved
    assert captured[0]["dwell_pickup"] == sentinel[0]
    assert captured[0]["dwell_dropoff"] == sentinel[1]
    assert captured[0]["drive_speed_mult"] == sentinel[2]


def test_gen_on_calls_through_injected_R(monkeypatch):
    """ON: symulacja idzie przez plan_bag, ale FIZYCZNIE wykonuje wstrzyknięte
    R.simulate (kontrakt wstrzykiwania zachowany) — dowód: captured niepuste
    przy plannerze podmienionym tak, by NIE znał kanonicznego symulatora."""
    monkeypatch.setattr(planner._R2, "simulate_bag_route_v2",
                        lambda *a, **k: (_ for _ in ()).throw(
                            AssertionError("kanoniczny symulator NIE może być użyty")))
    ok, captured, _ = _run_gen(monkeypatch, {"ENABLE_PLANNER_UNIFIED": True})
    assert ok is True and captured


def test_shadow_mismatch_logs_warning(monkeypatch, caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger=PR._log.name):
        ok, _, _ = _run_gen(
            monkeypatch,
            {"ENABLE_PLANNER_UNIFIED": False,
             "ENABLE_PLANNER_UNIFIED_SHADOW": True,
             "ENABLE_PLAN_RECHECK_TIER_DWELL": False},
            tier_params_fn=lambda tier, recheck_dwell_gate=False: (99.0, 99.0, 99.0),
        )
    assert ok is True
    assert any("PLANNER_PARAM_MISMATCH" in r.message for r in caplog.records)


def test_shadow_agreement_is_silent(monkeypatch, caplog):
    import logging
    with caplog.at_level(logging.WARNING, logger=PR._log.name):
        ok, _, _ = _run_gen(
            monkeypatch,
            {"ENABLE_PLANNER_UNIFIED": False,
             "ENABLE_PLANNER_UNIFIED_SHADOW": True,
             "ENABLE_PLAN_RECHECK_TIER_DWELL": False},
        )
    assert ok is True
    assert not any("PLANNER_PARAM_MISMATCH" in r.message for r in caplog.records)


def test_flags_registered_etap4_with_module_consts():
    assert "ENABLE_PLANNER_UNIFIED" in C.ETAP4_DECISION_FLAGS
    assert "ENABLE_PLANNER_UNIFIED_SHADOW" in C.ETAP4_DECISION_FLAGS
    assert C.ENABLE_PLANNER_UNIFIED is False
    assert C.ENABLE_PLANNER_UNIFIED_SHADOW is False
