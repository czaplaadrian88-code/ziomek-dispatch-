"""S1 (2026-07-02) — konsolidacja 35-min HARD do JEDNEGO źródła z JAWNĄ kotwicą.

Testuje moduł `sla_anchor` + flagę `ENABLE_SLA_ANCHOR_UNIFIED` w 3 bliźniakach:
  - route_simulator_v2._count_sla_violations  (kotwica NOW),
  - feasibility_v2 SLA-loop                   (kotwica NOW, ta sama funkcja),
  - feasibility_v2 R6 per-order               (kotwica READY, r6_thermal_anchor),
  - plan_recheck._o2_key                       (dziedziczy plan.sla_violations).

Dowody: OFF = bajt-parytet decyzji; ON = te same decyzje + metryka obs
`sla_anchor_source`; twin-parytet (mutacja źródła psuje WSZYSTKICH konsumentów);
C13 mutacja ×2 na nowej ścieżce (próg, polaryzacja kotwicy); kompatybilność 4
kombinacji z L3 (ENABLE_PLAN_RECHECK_GATES).

Flagi PINOWANE monkeypatchem (wzorzec #9, flip-odporne na at-202/203 04.07).
Self-locate: standardowy import dispatch_v2 (root z conftest — env-overridable).
"""
from datetime import datetime, timezone, timedelta

import pytest

from dispatch_v2 import common as C
from dispatch_v2 import sla_anchor as SA
from dispatch_v2.feasibility_v2 import check_feasibility_v2
from dispatch_v2.route_simulator_v2 import OrderSim, simulate_bag_route_v2
from dispatch_v2 import route_simulator_v2 as rs


_NOW = datetime(2026, 4, 18, 17, 0, tzinfo=timezone.utc)


class _FakeMatrix:
    def __init__(self, d): self.d = d

    def __call__(self, a, b):
        n = len(a)
        return [[{"duration_s": self.d, "osrm_fallback": False} for _ in range(n)]
                for _ in range(n)]


class _FakeHav:
    def __call__(self, a, b): return 2.0


def _mock_osrm(duration_s=60):
    rs.osrm_client.table = _FakeMatrix(duration_s)
    rs.osrm_client.haversine = _FakeHav()


@pytest.fixture(autouse=True)
def _pin(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_V325_SCHEDULE_HARDENING", False, raising=False)
    _base = {"MAX_BAG_SANITY_CAP": 8}
    monkeypatch.setattr(C, "load_flags", lambda: dict(_base))
    _pin.base = _base
    return _base


def _flags(monkeypatch, **kv):
    d = {"MAX_BAG_SANITY_CAP": 8}
    d.update(kv)
    monkeypatch.setattr(C, "load_flags", lambda: dict(d))


def _new(oid="NEW", ready=None, status="assigned"):
    return OrderSim(order_id=oid, pickup_coords=(53.13, 23.15),
                    delivery_coords=(53.14, 23.16), status=status,
                    pickup_ready_at=ready)


def _picked(oid, ago_min, now=_NOW):
    return OrderSim(order_id=oid, pickup_coords=(53.12, 23.14),
                    delivery_coords=(53.15, 23.17), status="picked_up",
                    picked_up_at=now - timedelta(minutes=ago_min),
                    pickup_ready_at=now)


# ── sla_anchor: jednostkowe + C13 (próg, polaryzacja kotwicy) ─────────────────
def test_now_anchor_precedence_pickup_over_picked_over_now():
    """Polaryzacja/kolejność kotwicy NOW: plan pickup_at → picked_up_at → now.
    (C13 mutacja polaryzacji: zamiana kolejności → ten test PADA.)"""
    ready_dt = _NOW
    pu_plan = _NOW + timedelta(minutes=7)
    o_plan = _new(oid="A", ready=ready_dt)
    # w planie: kotwica = tsp pickup_at (nie now, nie picked_up)
    assert SA.now_anchor(o_plan, {"A": pu_plan}, _NOW) == pu_plan
    # picked_up poza planem: kotwica = picked_up_at (UTC)
    o_pick = _picked("B", ago_min=20)
    assert SA.now_anchor(o_pick, {}, _NOW) == o_pick.picked_up_at.astimezone(timezone.utc)
    # świeży, poza planem, nieodebrany: kotwica = now
    o_bare = _new(oid="C", ready=None)
    assert SA.now_anchor(o_bare, {}, _NOW) == _NOW


def test_exceeds_boundary_is_strict_gt():
    """C13 mutacja progu (`>`→`>=`): dokładnie 35 min NIE jest naruszeniem;
    35.01 jest. Kotwiczy próg jako STRICT `>` (1:1 z inline)."""
    anchor = _NOW
    at_35 = _NOW + timedelta(minutes=35)
    over_35 = _NOW + timedelta(minutes=35, seconds=1)
    assert SA.exceeds(at_35, anchor, 35.0) is False
    assert SA.exceeds(over_35, anchor, 35.0) is True
    assert abs(SA.elapsed_min(at_35, anchor) - 35.0) < 1e-9


def test_hard_minutes_reads_single_dial():
    assert SA.hard_minutes() == float(C.BAG_TIME_HARD_MAX_MIN)


def test_anchor_explicit_kind_rejects_unknown():
    with pytest.raises(ValueError):
        SA.anchor(_new(), kind="teraz", now=_NOW)


# ── OFF = bajt-parytet decyzji (reprezentatywne scenariusze) ──────────────────
_SCENARIOS = [
    ("solo_fresh", 60, [], _new(ready=_NOW)),
    ("solo_stale_ready", 60, [], _new(ready=_NOW - timedelta(minutes=40))),
    ("carried_picked_40", 300, [_picked("B1", 40)], _new(ready=_NOW)),
    ("carried_picked_36", 60, [_picked("B1", 36)], _new(ready=_NOW)),
    ("bag2_mixed", 600, [_picked("B1", 30), _new(oid="B2", ready=_NOW - timedelta(minutes=20))],
     _new(ready=_NOW)),
]


@pytest.mark.parametrize("name,dur,bag,new", _SCENARIOS)
def test_off_on_decision_parity(monkeypatch, name, dur, bag, new):
    """Werdykt+reason+sla_violations+r6_per_order IDENTYCZNE OFF vs ON (refaktor)."""
    _mock_osrm(dur)
    kw = dict(courier_pos=(53.0, 23.0), bag=list(bag), new_order=new, now=_NOW)
    _flags(monkeypatch, ENABLE_SLA_ANCHOR_UNIFIED=False)
    vo, ro, mo, po = check_feasibility_v2(**kw)
    _flags(monkeypatch, ENABLE_SLA_ANCHOR_UNIFIED=True)
    vn, rn, mn, pn = check_feasibility_v2(**kw)
    assert (vo, ro) == (vn, rn), f"{name}: decyzja OFF≠ON: {(vo,ro)} vs {(vn,rn)}"
    assert (po.sla_violations if po else None) == (pn.sla_violations if pn else None)
    assert mo.get("r6_per_order_violations") == mn.get("r6_per_order_violations")


# ── ON ≠ OFF: metryka obs sla_anchor_source obecna TYLKO pod ON ────────────────
def test_metric_present_only_under_on(monkeypatch):
    _mock_osrm(60)
    new = _new(ready=_NOW - timedelta(minutes=40))
    kw = dict(courier_pos=(53.0, 23.0), bag=[], new_order=new, now=_NOW)
    _flags(monkeypatch, ENABLE_SLA_ANCHOR_UNIFIED=False)
    _, _, mo, _ = check_feasibility_v2(**kw)
    _flags(monkeypatch, ENABLE_SLA_ANCHOR_UNIFIED=True)
    _, _, mn, _ = check_feasibility_v2(**kw)
    assert "sla_anchor_source" not in mo, "OFF nie może emitować metryki"
    assert isinstance(mn.get("sla_anchor_source"), dict)
    assert mn["sla_anchor_source"]["hard_dial_min"] == float(C.BAG_TIME_HARD_MAX_MIN)


# ── Twin-parytet: JEDNO źródło (mutacja now_anchor psuje WSZYSTKICH) ───────────
def test_single_source_mutation_propagates_to_all_twins(monkeypatch):
    """Pod ON _count_sla_violations (route_sim) ORAZ feasibility SLA-loop czytają
    TĘ SAMĄ funkcję sla_anchor.now_anchor. Zmiana źródła (kotwica→now) zmienia OBA:
    plan.sla_violations spada I reason przestaje być sla_violation."""
    _mock_osrm(60)
    _flags(monkeypatch, ENABLE_SLA_ANCHOR_UNIFIED=True)
    monkeypatch.setattr(C, "ENABLE_SLA_PREEXISTING_BYPASS", False, raising=False)
    bag = [_picked("B1", 40)]
    new = _new(ready=_NOW)
    kw = dict(courier_pos=(53.13, 23.15), bag=list(bag), new_order=new, now=_NOW)

    # baseline (prawdziwe źródło): picked_up 40 min → SLA łamie
    v0, r0, _, p0 = check_feasibility_v2(**kw)
    p_sim0 = simulate_bag_route_v2((53.13, 23.15), list(bag), new, now=_NOW, sla_minutes=35)
    assert r0.startswith("sla_violation") and p_sim0.sla_violations >= 1

    # mutacja ŹRÓDŁA: kotwica NOW → zawsze `now` (nie picked_up) → carry znika
    monkeypatch.setattr(SA, "now_anchor", lambda o, pk, now: now)
    v1, r1, _, p1 = check_feasibility_v2(**kw)
    p_sim1 = simulate_bag_route_v2((53.13, 23.15), list(bag), new, now=_NOW, sla_minutes=35)
    # OBA bliźniaki zareagowały na jedną zmianę źródła:
    assert p_sim1.sla_violations < p_sim0.sla_violations, "route_sim nie czyta źródła"
    assert not r1.startswith("sla_violation"), "feasibility SLA-loop nie czyta źródła"


# ── Kompatybilność 4 kombinacji: mine × L3 (ENABLE_PLAN_RECHECK_GATES) ─────────
@pytest.mark.parametrize("unified", [False, True])
@pytest.mark.parametrize("l3", [False, True])
def test_sla_count_stable_across_unified_x_l3(monkeypatch, unified, l3):
    """plan.sla_violations (konsumowane przez L3 compare-and-keep) jest STABILNE
    we wszystkich 4 kombinacjach — moja flaga = bajt-parytet, L3 nie dotyka liczenia
    sla. Gwarantuje poprawność mine×L3 przy flipie L3 (at-202/203, sob 04.07)."""
    _mock_osrm(300)
    _flags(monkeypatch, ENABLE_SLA_ANCHOR_UNIFIED=unified,
           ENABLE_PLAN_RECHECK_GATES=l3)
    bag = [_picked("B1", 40)]
    new = _new(ready=_NOW)
    p = simulate_bag_route_v2((53.0, 23.0), bag, new, now=_NOW, sla_minutes=35)
    # kanoniczna wartość (OFF/OFF) policzona raz dla porównania
    _flags(monkeypatch, ENABLE_SLA_ANCHOR_UNIFIED=False, ENABLE_PLAN_RECHECK_GATES=False)
    p_ref = simulate_bag_route_v2((53.0, 23.0), [_picked("B1", 40)], _new(ready=_NOW),
                                  now=_NOW, sla_minutes=35)
    assert p.sla_violations == p_ref.sla_violations
