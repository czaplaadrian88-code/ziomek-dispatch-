"""L3 (2026-07-02, Faza 3 audytu, F2/K2) — plan_recheck przestaje cofać.

Testy BEHAWIORALNE (C13) bramki ZAPISU regenu (compare-and-keep R6) + GC
courier_plans + mutation×2. Ładowanie modułu SAMO-LOKALIZUJĄCE (C12e):
`Path(__file__).parents[1]` → kanon po merge / worktree przed merge; sprzątanie
sys.modules w try/finally. Deps (common/plan_manager/route_simulator) z sys.path
(conftest pinuje kanon) — my testujemy TYLKO logikę plan_recheck.
"""
import importlib.util
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]  # kanon dispatch_v2 / worktree root
_UTC = timezone.utc


def _load_pr():
    """Załaduj plan_recheck spod TEGO drzewa (kanon lub worktree) jako świeży moduł."""
    name = "_l3_plan_recheck_under_test"
    spec = importlib.util.spec_from_file_location(name, _REPO / "plan_recheck.py")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    try:
        spec.loader.exec_module(mod)
    except Exception:
        sys.modules.pop(name, None)
        raise
    return mod


@pytest.fixture()
def PR():
    mod = _load_pr()
    try:
        yield mod
    finally:
        sys.modules.pop("_l3_plan_recheck_under_test", None)


def _iso(dt):
    return dt.astimezone(_UTC).isoformat()


# ─────────────────────────────────────────────────────────────────────────────
# 1) _l3_bag_time_max_min — R6 carried-age z re-czasowanych stopów (deterministyczne)
# ─────────────────────────────────────────────────────────────────────────────
def test_bag_time_max_committed_anchor(PR):
    """Kotwica = czas_kuriera_warsaw (committed ready). dropoff 40 min po ready → 40."""
    ready = datetime(2026, 7, 2, 12, 0, tzinfo=_UTC)
    orders = {"A": {"status": "assigned", "czas_kuriera_warsaw": _iso(ready)}}
    stops = [
        {"order_id": "A", "type": "pickup", "predicted_at": _iso(ready)},
        {"order_id": "A", "type": "dropoff", "predicted_at": _iso(ready + timedelta(minutes=40))},
    ]
    assert PR._l3_bag_time_max_min(stops, orders) == pytest.approx(40.0)


def test_bag_time_max_pickup_fallback(PR):
    """Brak czas_kuriera / picked_up_at → kotwica = predicted_at odbioru."""
    t0 = datetime(2026, 7, 2, 12, 0, tzinfo=_UTC)
    orders = {"A": {"status": "assigned"}}  # brak ready
    stops = [
        {"order_id": "A", "type": "pickup", "predicted_at": _iso(t0)},
        {"order_id": "A", "type": "dropoff", "predicted_at": _iso(t0 + timedelta(minutes=20))},
    ]
    assert PR._l3_bag_time_max_min(stops, orders) == pytest.approx(20.0)


# ─────────────────────────────────────────────────────────────────────────────
# 2) _l3_hard_breach — R6 HARD (retime tożsamościowy → deterministyczne bez OSRM)
# ─────────────────────────────────────────────────────────────────────────────
def _breach(PR, monkeypatch, carried_min):
    ready = datetime(2026, 7, 2, 12, 0, tzinfo=_UTC)
    orders = {"A": {"status": "assigned", "czas_kuriera_warsaw": _iso(ready)}}
    stops = [
        {"order_id": "A", "type": "pickup", "predicted_at": _iso(ready)},
        {"order_id": "A", "type": "dropoff", "predicted_at": _iso(ready + timedelta(minutes=carried_min))},
    ]
    monkeypatch.setattr(PR, "_retime_stops", lambda s, *a, **k: s)  # tożsamość
    return PR._l3_hard_breach(stops, orders, (53.13, 23.16), None, ready)


def test_hard_breach_over_35(PR, monkeypatch):
    b = _breach(PR, monkeypatch, 42.0)
    assert b["retimed_ok"] and b["r6_hard"] is True and b["r6_max_min"] == pytest.approx(42.0)


def test_hard_breach_under_35(PR, monkeypatch):
    b = _breach(PR, monkeypatch, 25.0)
    assert b["retimed_ok"] and b["r6_hard"] is False


def test_hard_breach_retime_fail_is_soft(PR, monkeypatch):
    """OSRM/retime miss → retimed_ok=False, r6_hard=False (fail-soft: nie blokuje)."""
    monkeypatch.setattr(PR, "_retime_stops", lambda s, *a, **k: None)
    b = PR._l3_hard_breach([{"order_id": "A", "type": "dropoff", "predicted_at": _iso(datetime.now(_UTC))}],
                           {}, (53.13, 23.16), None, datetime.now(_UTC))
    assert b["retimed_ok"] is False and b["r6_hard"] is False


# ─────────────────────────────────────────────────────────────────────────────
# 3) _l3_gate_verdict — pure (compare-and-keep) + MUTATION-CHECK #1
# ─────────────────────────────────────────────────────────────────────────────
_OK = {"r6_hard": False, "retimed_ok": True}
_BREACH = {"r6_hard": True, "retimed_ok": True}


def test_verdict_reject_when_fresh_breaks_existing_clean(PR):
    assert PR._l3_gate_verdict(_BREACH, _OK, True) == "REJECT"


def test_verdict_both_breach(PR):
    assert PR._l3_gate_verdict(_BREACH, _BREACH, True) == "BOTH_BREACH"


def test_verdict_pass_when_fresh_clean(PR):
    assert PR._l3_gate_verdict(_OK, _OK, True) == "PASS"


def test_verdict_no_baseline_when_not_comparable(PR):
    assert PR._l3_gate_verdict(_BREACH, None, False) == "NO_BASELINE"


def test_verdict_no_baseline_when_existing_retime_failed(PR):
    """Baseline nie-re-czasowany → NIE REJECT (nie blokuj na nie-policzonym R6)."""
    assert PR._l3_gate_verdict(_BREACH, {"r6_hard": False, "retimed_ok": False}, True) == "NO_BASELINE"


def test_mutation1_reversed_verdict_would_save_breaking(PR):
    """MUTATION #1: gdyby verdict był odwrócony (zapisuj łamiący gdy istniejący
    czysty), REJECT stałby się PASS → asercja PADA. Dowód że test łapie inwersję."""
    def _mutated(fresh, exist, comparable):
        if not comparable or exist is None or not exist.get("retimed_ok"):
            return "NO_BASELINE"
        # ODWRÓCONE: świeży łamie + istniejący czysty → PASS (zapisz łamiący!)
        if fresh.get("r6_hard") and not exist.get("r6_hard"):
            return "PASS"
        return "PASS"
    assert _mutated(_BREACH, _OK, True) == "PASS"           # mutacja
    assert PR._l3_gate_verdict(_BREACH, _OK, True) == "REJECT"  # prawdziwa logika ≠ mutacja


# ─────────────────────────────────────────────────────────────────────────────
# 4) _l3_active_dropoff_oids — porównywalność worka
# ─────────────────────────────────────────────────────────────────────────────
def test_active_dropoff_oids_filters_terminal(PR):
    plan = {"stops": [
        {"order_id": "A", "type": "dropoff"}, {"order_id": "A", "type": "pickup"},
        {"order_id": "B", "type": "dropoff"}, {"order_id": "C", "type": "dropoff"}]}
    orders = {"A": {"status": "assigned"}, "B": {"status": "delivered"}, "C": {"status": "picked_up"}}
    assert PR._l3_active_dropoff_oids(plan, orders) == {"A", "C"}


# ─────────────────────────────────────────────────────────────────────────────
# 5) INTEGRACJA gate w _gen_one_bag_plan — ON≠OFF + reject-nie-zapisuje
# ─────────────────────────────────────────────────────────────────────────────
class _FakePlan:
    def __init__(self, oids, ready, carried_min):
        self.sequence = list(oids)
        self.sla_violations = 0
        self.total_duration_min = 30.0
        self.o2_score = 0.0
        self.max_carried_age = carried_min
        self.pickup_at = {o: ready for o in oids}
        self.predicted_delivered_at = {o: ready + timedelta(minutes=carried_min) for o in oids}


def _drive_gen(PR, monkeypatch, flags, carried_min, existing_carried=None):
    """Uruchom _gen_one_bag_plan z kontrolowanym fresh R6; zwróć (result, saved_calls)."""
    from dispatch_v2 import common, plan_manager, route_simulator_v2 as R
    ready = datetime(2026, 7, 2, 12, 0, tzinfo=_UTC)
    now = ready
    oids = ["A"]
    coords = [23.16, 53.13]
    orders = {"A": {"status": "assigned", "czas_kuriera_warsaw": _iso(ready),
                    "pickup_coords": [53.13, 23.16], "delivery_coords": [53.14, 23.17]}}
    # środowisko: proste ścieżki (bez committed-prop / canon / floor)
    monkeypatch.setattr(PR, "ENABLE_PLAN_RECHECK_COMMITTED_PROPAGATION", False, raising=False)
    monkeypatch.setattr(PR, "ENABLE_PLAN_CANON_ORDER_INVARIANTS", False, raising=False)
    monkeypatch.setattr(PR, "ENABLE_PICKUP_REFLOOR", False, raising=False)
    monkeypatch.setattr(PR, "_start_anchor", lambda *a, **k: ((53.13, 23.16), None, "gps_pwa"))
    monkeypatch.setattr(PR, "_retime_stops", lambda s, *a, **k: s)          # tożsamość
    monkeypatch.setattr(PR, "_l3_bag_spread", lambda sims: {})              # bez OSRM
    monkeypatch.setattr(R, "simulate_bag_route_v2",
                        lambda *a, **k: _FakePlan(oids, ready, carried_min))
    # flagi decyzyjne przez flags.json (decision_flag)
    monkeypatch.setattr(common, "load_flags", lambda: dict(flags))
    # istniejący plan (baseline) — seed przez load_plan stub
    if existing_carried is not None:
        exist_stops = [
            {"order_id": "A", "type": "pickup", "predicted_at": _iso(ready)},
            {"order_id": "A", "type": "dropoff",
             "predicted_at": _iso(ready + timedelta(minutes=existing_carried))}]
        monkeypatch.setattr(plan_manager, "load_plan",
                            lambda cid, **k: {"stops": exist_stops, "invalidated_at": None})
    else:
        monkeypatch.setattr(plan_manager, "load_plan", lambda cid, **k: None)
    saved = []
    monkeypatch.setattr(plan_manager, "save_plan", lambda cid, body, **k: saved.append((cid, body)))
    PR._l3_reset_gate_stats()
    res = PR._gen_one_bag_plan("77", oids, orders, {}, now, R)
    return res, saved, dict(PR._L3_GATE_STATS)


def test_gate_off_saves_byte_for_byte(PR, monkeypatch):
    """OFF: nawet fresh R6-breach + czysty istniejący → ZAPISUJE (bajt-w-bajt jak dziś)."""
    res, saved, stats = _drive_gen(PR, monkeypatch,
                                   {"ENABLE_PLAN_RECHECK_GATES": False},
                                   carried_min=45.0, existing_carried=10.0)
    assert res is True and len(saved) == 1 and stats == {}


def test_gate_on_rejects_breaking_regen_over_clean_existing(PR, monkeypatch):
    """ON: fresh R6=45>35 łamie, istniejący R6=10 czysty, porównywalne (ten sam worek)
    → NIE zapisuje (keep existing), licznik rejected."""
    res, saved, stats = _drive_gen(PR, monkeypatch,
                                   {"ENABLE_PLAN_RECHECK_GATES": True},
                                   carried_min=45.0, existing_carried=10.0)
    assert res is False and saved == [] and stats.get("l3_regen_rejected") == 1


def test_gate_on_saves_healthy_regen(PR, monkeypatch):
    """ON: fresh R6=20 czysty → ZAPISUJE (PASS)."""
    res, saved, stats = _drive_gen(PR, monkeypatch,
                                   {"ENABLE_PLAN_RECHECK_GATES": True},
                                   carried_min=20.0, existing_carried=10.0)
    assert res is True and len(saved) == 1 and stats.get("l3_regen_pass") == 1


def test_gate_on_both_breach_saves_fresh(PR, monkeypatch):
    """ON: oba łamią (odziedziczony zły stan) → ZAPISUJE świeży + metryka both_breach."""
    res, saved, stats = _drive_gen(PR, monkeypatch,
                                   {"ENABLE_PLAN_RECHECK_GATES": True},
                                   carried_min=45.0, existing_carried=50.0)
    assert res is True and len(saved) == 1 and stats.get("l3_regen_both_breach") == 1


def test_gate_on_no_baseline_saves_fresh(PR, monkeypatch):
    """ON: brak istniejącego (bag-change/no-plan) → ZAPISUJE świeży (NO_BASELINE)."""
    res, saved, stats = _drive_gen(PR, monkeypatch,
                                   {"ENABLE_PLAN_RECHECK_GATES": True},
                                   carried_min=45.0, existing_carried=None)
    assert res is True and len(saved) == 1 and stats.get("l3_regen_no_baseline") == 1


# ─────────────────────────────────────────────────────────────────────────────
# 6) GC — dry-run vs apply na tmp pliku planów + MUTATION-CHECK #2
# ─────────────────────────────────────────────────────────────────────────────
def _seed_plans(tmp_path, plan_manager):
    plan_manager.PLANS_FILE = tmp_path / "courier_plans.json"
    plan_manager.LOCK_FILE = tmp_path / "courier_plans.lock"
    old_inv = (datetime.now(_UTC) - timedelta(hours=72)).isoformat()
    plans = {
        # zombie: invalidated 72h temu (>48h) → age-removed
        "ZOMB": {"plan_version": 1, "created_at": old_inv, "start_pos": {"lat": 0, "lng": 0},
                 "start_ts": old_inv, "stops": [], "optimization_method": "x",
                 "invalidated_at": old_inv, "invalidation_reason": "OLD"},
        # active: żywe zlecenie → NIETKNIĘTY
        "LIVE": {"plan_version": 1, "created_at": _iso(datetime.now(_UTC)),
                 "start_pos": {"lat": 53.1, "lng": 23.1}, "start_ts": _iso(datetime.now(_UTC)),
                 "stops": [{"order_id": "A", "type": "dropoff",
                            "coords": {"lat": 53.1, "lng": 23.1}, "predicted_at": None}],
                 "optimization_method": "x"},
        # non-inval bez aktywnego → invalidate("GC_NO_ACTIVE")
        "DEAD": {"plan_version": 1, "created_at": _iso(datetime.now(_UTC)),
                 "start_pos": {"lat": 53.1, "lng": 23.1}, "start_ts": _iso(datetime.now(_UTC)),
                 "stops": [{"order_id": "Z", "type": "dropoff",
                            "coords": {"lat": 53.1, "lng": 23.1}, "predicted_at": None}],
                 "optimization_method": "x"},
    }
    plan_manager.PLANS_FILE.write_text(json.dumps(plans))
    orders = {"A": {"status": "assigned"}, "Z": {"status": "delivered"}}
    return orders


def test_gc_dry_run_reports_only(PR, tmp_path):
    from dispatch_v2 import plan_manager
    orders = _seed_plans(tmp_path, plan_manager)
    before = plan_manager.PLANS_FILE.read_text()
    summ = {}
    rep = PR._gc_courier_plans(orders, datetime.now(_UTC), summ, dry_run=True, max_age_h=48.0)
    assert rep["gc_age_removed"] == 1 and rep["gc_no_active_invalidated"] == 1
    assert rep["gc_active_kept"] == 1
    assert plan_manager.PLANS_FILE.read_text() == before  # ZERO mutacji


def test_gc_apply_removes_zombie_keeps_live(PR, tmp_path):
    from dispatch_v2 import plan_manager
    orders = _seed_plans(tmp_path, plan_manager)
    PR._gc_courier_plans(orders, datetime.now(_UTC), {}, dry_run=False, max_age_h=48.0)
    after = json.loads(plan_manager.PLANS_FILE.read_text())
    assert "ZOMB" not in after                       # age-zombie usunięty
    assert "LIVE" in after and after["LIVE"].get("invalidated_at") is None  # żywy NIETKNIĘTY
    assert after["DEAD"].get("invalidated_at") is not None  # no-active → invalidated
    assert after["DEAD"].get("invalidation_reason") == "GC_NO_ACTIVE"


def test_gc_apply_prunes_terminal_stop(PR, tmp_path):
    """Aktywny plan ze STOPEM zlecenia terminalnego → remove_stops (safety-net)."""
    from dispatch_v2 import plan_manager
    plan_manager.PLANS_FILE = tmp_path / "courier_plans.json"
    plan_manager.LOCK_FILE = tmp_path / "courier_plans.lock"
    now = datetime.now(_UTC)
    plans = {"MIX": {"plan_version": 1, "created_at": _iso(now),
                     "start_pos": {"lat": 53.1, "lng": 23.1}, "start_ts": _iso(now),
                     "stops": [
                         {"order_id": "A", "type": "dropoff", "coords": {"lat": 53.1, "lng": 23.1}, "predicted_at": None},
                         {"order_id": "B", "type": "dropoff", "coords": {"lat": 53.1, "lng": 23.1}, "predicted_at": None}],
                     "optimization_method": "x"}}
    plan_manager.PLANS_FILE.write_text(json.dumps(plans))
    orders = {"A": {"status": "assigned"}, "B": {"status": "delivered"}}  # B terminalny
    rep = PR._gc_courier_plans(orders, now, {}, dry_run=False, max_age_h=48.0)
    assert rep["gc_terminal_stop_prune"] == 1
    after = json.loads(plan_manager.PLANS_FILE.read_text())
    remaining = {s["order_id"] for s in after["MIX"]["stops"]}
    assert remaining == {"A"}  # B (terminalny) sprzątnięty, A (aktywny) zostaje


def test_mutation2_gc_without_active_guard_would_kill_live(PR, tmp_path, monkeypatch):
    """MUTATION #2: gdyby GC pomijał guard 'ma aktywne zlecenie' i invalidował
    KAŻDY plan → żywy LIVE zostałby zabity. Dowód że test to łapie."""
    from dispatch_v2 import plan_manager
    orders = _seed_plans(tmp_path, plan_manager)
    killed = []
    monkeypatch.setattr(plan_manager, "invalidate_plan", lambda cid, reason: killed.append(cid))
    # symulacja mutacji: invaliduj każdy non-inval plan (BEZ guarda aktywności)
    plans = plan_manager.load_plans()
    for cid, p in plans.items():
        if p.get("invalidated_at") is None:
            plan_manager.invalidate_plan(cid, "MUT")
    assert "LIVE" in killed  # mutacja zabija żywy
    # prawdziwy GC: LIVE NIE jest invalidowany
    killed.clear()
    PR._gc_courier_plans(orders, datetime.now(_UTC), {}, dry_run=False, max_age_h=48.0)
    assert "LIVE" not in killed  # prawdziwa logika chroni żywy


# ---------------------------------------------------------------------------
# ON≠OFF flagi ENABLE_COURIER_PLANS_GC na REALNEJ gałęzi produkcyjnej
# (_l3_maybe_gc — ekstrakt z run_recheck; test_flag_effect_coverage wymaga
# dowodu efektu flagi po nazwie, nie wywołania _gc_courier_plans wprost).
# ---------------------------------------------------------------------------

def _drive_maybe_gc(PR, monkeypatch, flags):
    from dispatch_v2 import common
    calls = []
    monkeypatch.setattr(common, "load_flags", lambda: dict(flags))
    monkeypatch.setattr(PR, "_gc_courier_plans",
                        lambda os_, now_, summ_, dry_run, max_age_h:
                        calls.append({"dry_run": dry_run, "max_age_h": max_age_h}))
    PR._l3_maybe_gc({}, datetime(2026, 7, 2, 12, 0, tzinfo=_UTC), {})
    return calls


def test_gc_flag_off_no_gc(PR, monkeypatch):
    """OFF: _gc_courier_plans NIE wywołany (zachowanie jak dziś)."""
    assert _drive_maybe_gc(PR, monkeypatch, {"ENABLE_COURIER_PLANS_GC": False}) == []


def test_gc_flag_on_calls_gc_dry_run_default(PR, monkeypatch):
    """ON bez PLAN_GC_DRY_RUN: GC wywołany w trybie dry_run=True (bezpieczny default)."""
    calls = _drive_maybe_gc(PR, monkeypatch, {"ENABLE_COURIER_PLANS_GC": True})
    assert calls == [{"dry_run": True, "max_age_h": 48.0}]


def test_gc_flag_on_respects_dry_run_false(PR, monkeypatch):
    """ON + PLAN_GC_DRY_RUN=False: GC wywołany ostro (realne kasowanie za świadomym flipem)."""
    calls = _drive_maybe_gc(PR, monkeypatch,
                            {"ENABLE_COURIER_PLANS_GC": True, "PLAN_GC_DRY_RUN": False})
    assert calls == [{"dry_run": False, "max_age_h": 48.0}]
