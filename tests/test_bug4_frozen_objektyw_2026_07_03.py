"""frozen-objektyw P0 (a-faithful) — DoD testy behawioralne (2026-07-03).

Domyka caveat schema-2: `frozen_total_duration`/`frozen_sla` NIE są już null w
żywym ticku — liczone TYM SAMYM silnikiem co fresh (`route_simulator_v2.
_simulate_sequence` + `_count_sla_violations`), reużywając rozgrzany cache OSRM.
Implementacja = `plan_recheck._score_frozen_objective` (reużywa pure-helpery oracle
`bug4_reseq_oracle` = jedno źródło, bliźniak-zero z offline `score_bag`).

Pokrycie DoD (projekt §4):
  (a) fast-path: identyczna sekwencja węzłów → frozen==fresh DOKŁADNIE + ZERO OSRM;
  (b) sekwencja różna: frozen policzony; kontrakt fresh_sla≤frozen_sla i
      fresh_total≤frozen_total+eps na worku gdzie fresh=optimum; +wariant STRICT
      (frozen materialnie gorszy) — nośny na mutację fast-path;
      +residual: naruszenie kontraktu = obj_tripwire True (NIE cichy drop);
  (c) PARYTET z oracle: live-scorer == `score_bag`→frozen_total w |Δ|<eps + równe sla;
  (d) fail-soft: brak mapowania stopu / wyjątek scoringu → frozen=null+nota, fresh
      nietknięty, tick zwraca None (nie pada);
  (e) parytet decyzji: wejścia niezmutowane, return None (log-only) mimo scoringu;
  (f) mutation-probe ×2: oś total→drive (parytet PADA); fast-path zawsze-fresh
      (materialność PADA).
"""
import copy
import json
from datetime import datetime, timezone, timedelta

from dispatch_v2 import plan_recheck as PR
from dispatch_v2 import common as C
from dispatch_v2 import route_simulator_v2 as R
from dispatch_v2 import osrm_client as OSRM
from dispatch_v2.tools import bug4_reseq_oracle as ORACLE

NOW = datetime(2026, 7, 3, 12, 0, 0, tzinfo=timezone.utc)
POS = (53.130, 23.160)
_EPS = 0.05

# 2-zleceniowy worek A,B (assigned) — coords w bbox Białegostoku (przechodzą guard).
_A_PICK = (53.110, 23.140)
_A_DROP = (53.120, 23.130)
_B_PICK = (53.120, 23.150)
_B_DROP = (53.135, 23.185)


def _mock_route(a, b, **k):
    """Deterministyczny leg: minuty ∝ |Δlat|+|Δlng| (stabilny, >0, symetryczny).
    OBIE metody (live-scorer i oracle) wołają TO SAMO → parytet exact; dwell>0 w
    silniku gwarantuje total≠drive (nośność probe osi)."""
    d = (abs(a[0] - b[0]) + abs(a[1] - b[1])) * 100.0
    return {"duration_min": round(d, 3), "duration_s": d * 60.0,
            "distance_m": d * 1000.0, "distance_km": d, "osrm_fallback": True}


def _sims():
    A = R.OrderSim(order_id="A", pickup_coords=_A_PICK, delivery_coords=_A_DROP,
                   status="assigned", pickup_ready_at=None, picked_up_at=None)
    B = R.OrderSim(order_id="B", pickup_coords=_B_PICK, delivery_coords=_B_DROP,
                   status="assigned", pickup_ready_at=None, picked_up_at=None)
    return {"A": A, "B": B}


def _orders():
    return {
        "A": {"status": "assigned", "pickup_coords": list(_A_PICK),
              "delivery_coords": list(_A_DROP), "courier_id": "99"},
        "B": {"status": "assigned", "pickup_coords": list(_B_PICK),
              "delivery_coords": list(_B_DROP), "courier_id": "99"},
    }


def _stops(order):
    """existing_plan.stops z (order_id,type) w zadanej kolejności + coords sim."""
    coord = {("A", "pickup"): _A_PICK, ("A", "dropoff"): _A_DROP,
             ("B", "pickup"): _B_PICK, ("B", "dropoff"): _B_DROP}
    return {"stops": [{"order_id": o, "type": t,
                       "coords": {"lat": coord[(o, t)][0], "lng": coord[(o, t)][1]}}
                      for (o, t) in order]}


class _FakePlan:
    """Świeży plan (mock) — objektyw+kolejność zdarzeń podstawiane per-test."""
    def __init__(self, total, sla, seq, events):
        self.total_duration_min = total
        self.sla_violations = sla
        self.sequence = seq
        self.pickup_at = {o: ts for (ts, o, kind) in events if kind == "pickup"}
        self.predicted_delivered_at = {o: ts for (ts, o, kind) in events if kind == "dropoff"}


def _events(order):
    """Zbuduj pickup_at/predicted_delivered_at tak, by fresh_labels = `order`."""
    return [(NOW + timedelta(minutes=10 * i), o, t) for i, (o, t) in enumerate(order)]


def _setup(monkeypatch, tmp_path, plan, route=_mock_route, drive=7.0):
    p = tmp_path / "bug4.jsonl"
    monkeypatch.setattr(PR, "_BUG4_RESEQ_SHADOW_PATH", str(p))
    monkeypatch.setattr(C, "flag",
                        lambda name, default=False: True if name == "ENABLE_BUG4_RESEQ_SHADOW" else default)
    monkeypatch.setattr(PR, "_start_anchor", lambda *a, **k: (POS, NOW, "gps_pwa"))
    monkeypatch.setattr(R, "simulate_bag_route_v2", lambda *a, **k: plan)
    monkeypatch.setattr(PR, "_osrm_drive_min_sum", lambda *a, **k: drive)
    monkeypatch.setattr(OSRM, "route", route)
    return p


def _run(monkeypatch, tmp_path, plan, existing, orders=None, route=_mock_route):
    p = _setup(monkeypatch, tmp_path, plan, route=route)
    ret = PR._bug4_reseq_shadow("99", ["A", "B"], existing, orders or _orders(),
                                {}, NOW, R, {})
    rec = json.loads(p.read_text().strip().splitlines()[-1])
    return ret, rec


# ── (a) FAST-PATH: identyczna sekwencja → frozen==fresh, ZERO OSRM ────────────
def test_fastpath_identical_sequence_zero_osrm(monkeypatch, tmp_path):
    order = [("A", "pickup"), ("A", "dropoff"), ("B", "pickup"), ("B", "dropoff")]
    plan = _FakePlan(33.75, 1, ["A", "B"], _events(order))
    calls = {"n": 0}

    def counting_route(a, b, **k):
        calls["n"] += 1
        return _mock_route(a, b, **k)
    ret, rec = _run(monkeypatch, tmp_path, plan, _stops(order), route=counting_route)
    assert ret is None
    assert rec["seq_differs"] is False              # trasa identyczna
    assert rec["frozen_total_duration"] == 33.75    # == fresh DOKŁADNIE
    assert rec["frozen_sla"] == 1
    assert calls["n"] == 0                           # ZERO OSRM ponad fresh (fast-path)
    assert "fast-path" in rec["obj_axis_note"]


# ── (b) SEKWENCJA RÓŻNA: kontrakt fresh≤frozen (fresh=optimum) ────────────────
def _opt_and_frozen(frozen_order):
    """Oracle: opt (brute-force) + frozen (dla podanej kolejności stopów)."""
    sims = _sims()
    seq_map = {("A", "pickup"): 1, ("A", "dropoff"): 2,
               ("B", "pickup"): 3, ("B", "dropoff"): 4}   # _build_nodes A,B order
    frozen_seq = [seq_map[s] for s in frozen_order]
    res = ORACLE.score_bag(POS, sims, frozen_seq, NOW)
    return res


def test_contract_fresh_le_frozen_when_fresh_optimum(monkeypatch, tmp_path):
    monkeypatch.setattr(OSRM, "route", _mock_route)   # ten SAM leg dla opt i loggera
    frozen_order = [("A", "pickup"), ("B", "pickup"), ("A", "dropoff"), ("B", "dropoff")]
    res = _opt_and_frozen(frozen_order)
    # fresh = OPTIMUM (co silnik faktycznie zwraca dla optymalnej sekwencji)
    fresh_labels_order = [("A", "pickup"), ("A", "dropoff"), ("B", "pickup"), ("B", "dropoff")]
    plan = _FakePlan(res["opt_total"], res["opt_sla"], res["opt_deliv_order"],
                     _events(fresh_labels_order))
    _, rec = _run(monkeypatch, tmp_path, plan, _stops(frozen_order))
    assert rec["seq_differs"] is True
    # kontrakt osi objektywu: fresh (opt) NIE gorszy od frozen
    assert rec["fresh_sla"] <= rec["frozen_sla"]
    assert rec["fresh_total_duration"] <= rec["frozen_total_duration"] + _EPS
    # tripwire NIE zapala się (kontrakt spełniony) — obj_tripwire False, nie residual
    assert ORACLE.obj_tripwire(rec) is False


def test_frozen_materially_worse_for_suboptimal_sequence(monkeypatch, tmp_path):
    """STRICT: dla ŚWIADOMIE gorszej sekwencji frozen frozen_total > fresh_total+eps.
    Nośne na mutację fast-path 'zawsze-fresh' (wtedy frozen==fresh → PADA)."""
    monkeypatch.setattr(OSRM, "route", _mock_route)   # ten SAM leg dla opt i loggera
    frozen_order = [("A", "pickup"), ("B", "pickup"), ("A", "dropoff"), ("B", "dropoff")]
    res = _opt_and_frozen(frozen_order)
    assert res["obj_delta_min"] > _EPS, "setup: wybierz sekwencję materialnie gorszą"
    fresh_labels_order = [("A", "pickup"), ("A", "dropoff"), ("B", "pickup"), ("B", "dropoff")]
    plan = _FakePlan(res["opt_total"], res["opt_sla"], res["opt_deliv_order"],
                     _events(fresh_labels_order))
    _, rec = _run(monkeypatch, tmp_path, plan, _stops(frozen_order))
    assert rec["frozen_total_duration"] > rec["fresh_total_duration"] + _EPS


def test_contract_violation_flagged_as_residual_not_dropped():
    """Kontrakt: gdy fresh GORSZY od frozen (suboptymalny OR-Tools) → obj_tripwire
    True = RESIDUAL do inspekcji, NIGDY cichy drop. Reader działa na polach loggera."""
    viol = {"schema": 2, "fresh_total_duration": 40.0, "fresh_sla": 0,
            "frozen_total_duration": 33.0, "frozen_sla": 0}
    assert ORACLE.obj_tripwire(viol) is True


# ── (c) PARYTET z oracle score_bag (jedno źródło) ────────────────────────────
def test_parity_live_scorer_equals_oracle_score_bag(monkeypatch, tmp_path):
    monkeypatch.setattr(OSRM, "route", _mock_route)
    frozen_order = [("A", "pickup"), ("B", "pickup"), ("A", "dropoff"), ("B", "dropoff")]
    sims = _sims()
    ft, fs, note = PR._score_frozen_objective(POS, sims, _stops(frozen_order), NOW)
    res = _opt_and_frozen(frozen_order)
    assert ft is not None and fs is not None
    assert abs(ft - res["frozen_total"]) < _EPS          # |Δ| < 0.05
    assert fs == res["frozen_sla"]                        # identyczne sla
    assert "a-faithful" in note


# ── (d) FAIL-SOFT ────────────────────────────────────────────────────────────
def test_failsoft_unmappable_stop_returns_null(monkeypatch, tmp_path):
    monkeypatch.setattr(OSRM, "route", _mock_route)
    # stop dla order_id spoza worka (X) → mapowanie na węzeł NIEMOŻLIWE → null+nota
    bad = {"stops": [{"order_id": "X", "type": "pickup",
                      "coords": {"lat": _A_PICK[0], "lng": _A_PICK[1]}},
                     {"order_id": "A", "type": "dropoff",
                      "coords": {"lat": _A_DROP[0], "lng": _A_DROP[1]}}]}
    ft, fs, note = PR._score_frozen_objective(POS, _sims(), bad, NOW)
    assert ft is None and fs is None
    assert "null" in note


def test_failsoft_scoring_exception_keeps_fresh_and_no_crash(monkeypatch, tmp_path):
    """Wyjątek w scoringu frozen → frozen=null+nota, fresh nietknięty, tick=None."""
    def boom(*a, **k):
        raise RuntimeError("injected OSRM/scorer failure")
    monkeypatch.setattr(ORACLE, "score_sequence", boom)
    order = [("A", "pickup"), ("B", "pickup"), ("A", "dropoff"), ("B", "dropoff")]
    fresh_order = [("A", "pickup"), ("A", "dropoff"), ("B", "pickup"), ("B", "dropoff")]
    plan = _FakePlan(30.0, 0, ["A", "B"], _events(fresh_order))
    ret, rec = _run(monkeypatch, tmp_path, plan, _stops(order))
    assert ret is None                               # tick NIE pada
    assert rec["fresh_total_duration"] == 30.0       # fresh nietknięty
    assert rec["fresh_sla"] == 0
    assert rec["frozen_total_duration"] is None       # frozen fail-soft null
    assert rec["frozen_sla"] is None
    assert "fail-soft" in rec["obj_axis_note"]


# ── (e) PARYTET DECYZJI: log-only, wejścia niezmutowane ──────────────────────
def test_decision_parity_inputs_not_mutated(monkeypatch, tmp_path):
    order = [("A", "pickup"), ("B", "pickup"), ("A", "dropoff"), ("B", "dropoff")]
    fresh_order = [("A", "pickup"), ("A", "dropoff"), ("B", "pickup"), ("B", "dropoff")]
    plan = _FakePlan(28.0, 0, ["A", "B"], _events(fresh_order))
    orders = _orders()
    existing = _stops(order)
    before = (copy.deepcopy(orders), copy.deepcopy(existing))
    ret, rec = _run(monkeypatch, tmp_path, plan, existing, orders=orders)
    assert ret is None                                       # log-only
    assert (orders, existing) == before                      # ZERO mutacji wejść
    assert rec["frozen_total_duration"] is not None          # a jednak policzony


# ── (f) MUTATION-PROBE ×2 (dowód nośności testów) ────────────────────────────
def test_mutation_axis_total_to_drive_breaks_parity(monkeypatch, tmp_path):
    """Gdyby scorer zwracał DRIVE zamiast TOTAL, parytet z oracle frozen_total PADA
    (dwell>0 → total≠drive). Dowód, że test parytetu jest nośny na oś."""
    monkeypatch.setattr(OSRM, "route", _mock_route)
    frozen_order = [("A", "pickup"), ("B", "pickup"), ("A", "dropoff"), ("B", "dropoff")]
    sims = _sims()
    real_ft, _fs, _n = PR._score_frozen_objective(POS, sims, _stops(frozen_order), NOW)
    res = _opt_and_frozen(frozen_order)
    # MUTANT: drive-oś (jak stary kłamiący przyrząd) — pobierz drive z oracle
    mutant_ft = res["frozen_drive"]
    assert abs(real_ft - res["frozen_total"]) < _EPS         # REAL: parytet OK
    assert abs(mutant_ft - res["frozen_total"]) >= _EPS      # MUTANT: parytet PADA
    assert res["frozen_total"] - res["frozen_drive"] > _EPS   # total>drive (dwell realny)


def test_mutation_fastpath_always_fresh_breaks_materiality(monkeypatch, tmp_path):
    """Gdyby scorer ZAWSZE kopiował fresh (nie liczył frozen), materialność (frozen
    gorszy od fresh dla złej sekwencji) PADA. Dowód nośności testu STRICT (b)."""
    frozen_order = [("A", "pickup"), ("B", "pickup"), ("A", "dropoff"), ("B", "dropoff")]
    res = _opt_and_frozen(frozen_order)
    fresh_total = res["opt_total"]
    real_frozen = res["frozen_total"]
    mutant_frozen = fresh_total                                # zawsze-fresh
    assert real_frozen > fresh_total + _EPS                    # REAL: materialny
    assert not (mutant_frozen > fresh_total + _EPS)            # MUTANT: PADA
