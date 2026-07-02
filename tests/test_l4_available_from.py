"""L4 — jedno źródło dostępności kuriera `available_from = max(now, shift_start)`.

Fala L4 (F1/INV-SRC-AVAILABLE-FROM, audyt spójności 30.06 „odbiór przed startem
zmiany"): shift_start liczony/pomijany w 17 powierzchniach, tylko 4 z floorem.
L4 daje JEDNĄ definicję w courier_resolver, dziedziczoną przez konsumentów za
flagą `ENABLE_AVAILABLE_FROM_SINGLE_SOURCE` (default OFF = stare ścieżki bajt-w-bajt).

Pokrycie (C13 — behawioralne, nie tekstowe):
  A. źródło pure: available_from_from_shift_start — 4 przypadki (future/on-shift/unknown/naive)
  B. resolve_shift_start — 4 przypadki resolucji (normalny/puste godziny/brak wpisu/gps-przed-zmianą)
  C. dispatchable_fleet populuje cs.available_from (ON) / None (OFF)
  #1 candidate floor (_l4_floor_candidate_eta): podnosi eta do available_from, noop, brak-af
  #3 feasibility: ON clampuje do available_from (też GPS-przed-zmianą), OFF stara ścieżka; pre_shift pickup ≥ start; no_gps on-shift no-op
  #5 plan_recheck: ON anchor floored (pickup ≥ available_from) / OFF leak (pickup < shift_start)
  chokepoint: effective_pickup_at liczone+zapisane, deklaracja czas_kuriera NIENARUSZONA; OFF brak pola
  parytet bliźniaków: #1 floor == #3 earliest_departure == #5 anchor == available_from_from_shift_start
  MUTATION ×2: (i) max→min w źródle, (ii) usunięcie floora #5 — testy MUSZĄ PAŚĆ

Samo-lokalizacja (NIE hardcode worktree): repo = parents[2] (kanon scripts/ lub overlay).
Flaga włączana monkeypatchem stałej modułu (conftest wycina ETAP4 z tmp flags.json →
decision_flag spada na globals()[stała]); NIE przez flags.json.
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from dispatch_v2 import common as C
from dispatch_v2 import courier_resolver as CR
from dispatch_v2 import dispatch_pipeline as D
from dispatch_v2 import feasibility_v2 as F
from dispatch_v2 import plan_recheck as PR
from dispatch_v2 import plan_manager as PM
from dispatch_v2 import osrm_client
from dispatch_v2 import route_simulator_v2 as RS
from dispatch_v2.route_simulator_v2 import OrderSim

WAW = ZoneInfo("Europe/Warsaw")
FLAG = "ENABLE_AVAILABLE_FROM_SINGLE_SOURCE"


# ── fixtures ──────────────────────────────────────────────────────────────────
@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setattr(C, FLAG, True)


@pytest.fixture
def osrm_mock(monkeypatch):
    """Deterministyczny OSRM: 3 min / 1 km każda noga (pickup times przewidywalne)."""
    def _route(a, b, use_cache=True):
        return {"duration_s": 180, "distance_m": 1000, "duration_min": 3.0,
                "distance_km": 1.0, "osrm_fallback": False}

    def _table(origins, destinations):
        return [[{"duration_s": 180, "duration_min": 3.0, "distance_m": 1000,
                  "distance_km": 1.0, "osrm_fallback": False}
                 for _ in destinations] for _ in origins]
    monkeypatch.setattr(osrm_client, "route", _route)
    monkeypatch.setattr(osrm_client, "table", _table)


class _Cand:
    def __init__(self, metrics):
        self.metrics = dict(metrics)


# ── A. źródło pure — available_from_from_shift_start (4 przypadki) ────────────
def test_A_floor_future_shift_start():
    now = datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc)
    ss = now + timedelta(minutes=30)
    af, src = CR.available_from_from_shift_start(ss, now)
    assert af == ss and src == "shift_start"


def test_A_floor_on_shift_returns_now():
    now = datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc)
    af, src = CR.available_from_from_shift_start(now - timedelta(minutes=30), now)
    assert af == now and src == "now_on_shift"


def test_A_floor_unknown_when_shift_none():
    now = datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc)
    af, src = CR.available_from_from_shift_start(None, now)
    assert af == now and src == "unknown"


def test_A_floor_naive_shift_start_treated_utc():
    now = datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc)
    ss_naive = datetime(2026, 7, 2, 9, 10)  # bez tzinfo → UTC
    af, src = CR.available_from_from_shift_start(ss_naive, now)
    assert af == datetime(2026, 7, 2, 9, 10, tzinfo=timezone.utc) and src == "shift_start"


# ── B. resolve_shift_start — 4 przypadki resolucji ───────────────────────────
def _match_exact(name, sched):
    return name if name in sched else None


def test_B_resolve_normal_entry():
    sched = {"Jan Kowalski": {"start": "11:00", "end": "21:00"}}
    ss = CR.resolve_shift_start("Jan Kowalski", schedule=sched, match_courier_fn=_match_exact)
    assert ss is not None and ss.hour == 11 and ss.minute == 0


def test_B_resolve_empty_hours_failopen_none():
    # entry ISTNIEJE ale puste godziny (dzień wolny / literówka) → None (=unknown downstream)
    sched = {"Jan Kowalski": {"start": None, "end": None}}
    ss = CR.resolve_shift_start("Jan Kowalski", schedule=sched, match_courier_fn=_match_exact)
    assert ss is None


def test_B_resolve_no_entry_none():
    sched = {"Jan Kowalski": {"start": "11:00", "end": "21:00"}}
    ss = CR.resolve_shift_start("Ktoś Inny", schedule=sched, match_courier_fn=_match_exact)
    assert ss is None


def test_B_gps_before_shift_floors_to_shift_start_pos_agnostic():
    """GPS-włączony-przed-zmianą (case 10:59@11:00): available_from = shift_start
    NIEZALEŻNIE od pos_source (floor liczony od shift_start vs now, nie od etykiety)."""
    now = datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc)   # 11:00 Warsaw ~= now+? (tu UTC)
    ss = now + timedelta(minutes=1)                          # zmiana za 1 min (10:59 vs 11:00)
    af, src = CR.available_from_from_shift_start(ss, now)
    assert af == ss and src == "shift_start"  # floor > now → pickup nie przed startem


# ── C. dispatchable_fleet populuje available_from (ON) / None (OFF) ──────────
def _inject_fleet_courier(shift_start):
    cs = CR.CourierState(courier_id="TSRC")
    cs.pos = (53.132, 23.168)
    cs.pos_source = "gps"
    cs.name = "Src Tester"
    cs.shift_start = shift_start
    return {"TSRC": cs}


def _isolate_schedule(monkeypatch):
    """Puść dispatchable_fleet bez realnego grafiku/override: schedule={} →
    blok grafiku pominięty, cs.shift_start (wstrzyknięty) zostaje."""
    import schedule_utils as SU
    monkeypatch.setattr(SU, "load_schedule", lambda: {})
    monkeypatch.setattr(SU, "is_schedule_stale", lambda: False)
    from dispatch_v2 import manual_overrides as MO
    monkeypatch.setattr(MO, "get_excluded", lambda: set())
    monkeypatch.setattr(MO, "get_working", lambda: {})
    monkeypatch.setattr(MO, "get_excluded_cids", lambda: set())


def test_C_source_populates_available_from_when_on(monkeypatch, flag_on):
    _isolate_schedule(monkeypatch)
    now = datetime.now(timezone.utc)
    ss = now + timedelta(minutes=45)
    fleet = _inject_fleet_courier(ss)
    out = {c.courier_id: c for c in CR.dispatchable_fleet(fleet=fleet)}
    cs = out["TSRC"]
    assert cs.available_from == ss.astimezone(timezone.utc)
    assert cs.available_from_source == "shift_start"


def test_C_source_none_when_off(monkeypatch):
    _isolate_schedule(monkeypatch)
    monkeypatch.setattr(C, FLAG, False)
    now = datetime.now(timezone.utc)
    fleet = _inject_fleet_courier(now + timedelta(minutes=45))
    out = {c.courier_id: c for c in CR.dispatchable_fleet(fleet=fleet)}
    cs = out["TSRC"]
    assert cs.available_from is None and cs.available_from_source == "unset"


# ── #1 candidate floor (_l4_floor_candidate_eta) ─────────────────────────────
def test_c1_floor_raises_eta_to_available_from():
    now = datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc)
    af = now + timedelta(minutes=30)
    eta_naive = now + timedelta(minutes=5)   # kandydat liczył odbiór na now+5 (< af)
    c = _Cand({"available_from_utc": af.isoformat(),
               "eta_pickup_utc": eta_naive.isoformat()})
    raised = D._l4_floor_candidate_eta(c)
    assert c.metrics["eta_pickup_utc"] == af.isoformat()
    assert c.metrics["eta_drive_utc"] == af.isoformat()
    assert c.metrics["af_applied"] is True
    assert raised == pytest.approx(25.0, abs=0.05)  # 30 - 5
    assert c.metrics["af_floor_applied_min"] == pytest.approx(25.0, abs=0.05)


def test_c1_floor_noop_when_eta_after_available_from():
    now = datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc)
    af = now                                  # on-shift → available_from = now
    eta = now + timedelta(minutes=15)         # odbiór już po af
    c = _Cand({"available_from_utc": af.isoformat(), "eta_pickup_utc": eta.isoformat()})
    raised = D._l4_floor_candidate_eta(c)
    assert c.metrics["eta_pickup_utc"] == eta.isoformat()  # NIE obniżony
    assert c.metrics["af_applied"] is False
    assert raised == 0.0


def test_c1_floor_none_without_available_from():
    c = _Cand({"eta_pickup_utc": "2026-07-02T09:05:00+00:00"})  # brak available_from_utc
    assert D._l4_floor_candidate_eta(c) is None
    assert "af_applied" not in c.metrics  # nic nie tknięte


# ── #3 feasibility kill-test (ON≠OFF) ────────────────────────────────────────
def _order(oid="O3", pickup=(53.13, 23.16), drop=(53.15, 23.19), ready=None):
    return OrderSim(order_id=oid, pickup_coords=pickup, delivery_coords=drop,
                    status="new", pickup_ready_at=ready)


def test_c3_on_clamps_gps_courier_before_shift(osrm_mock, monkeypatch):
    """ON: GPS kurier ze startem w przyszłości → clamp do available_from (domknięcie
    „GPS-przed-zmianą" — stara ścieżka pomijała bo pos_source=gps)."""
    monkeypatch.setattr(C, FLAG, True)
    now = datetime(2026, 7, 2, 8, 30, tzinfo=timezone.utc)
    af = now + timedelta(minutes=20)     # start w przyszłości (warm-up ≤30 → nie hard-reject)
    se = now + timedelta(hours=10)       # shift_end obecny (inaczej NO_ACTIVE_SHIFT gate)
    v, r, m, plan = F.check_feasibility_v2(
        courier_pos=(53.132, 23.168), bag=[], new_order=_order(),
        shift_end=se, shift_start=af, now=now, pos_source="gps",
        available_from=af, sla_minutes=35)
    assert v == "MAYBE"
    assert m.get("pre_shift_clamp_applied") is True
    assert m.get("af_clamp_applied") is True
    assert m.get("earliest_departure_utc") == af.isoformat()
    assert plan is not None and plan.pickup_at["O3"] >= af


def test_c3_off_gps_courier_no_clamp(osrm_mock, monkeypatch):
    """OFF + stara flaga departure-clamp ON: GPS kurier NIE jest clampowany
    (pos_source∉{pre_shift,no_gps}) → dowód że ścieżki ON≠OFF różnią się na TYCH
    SAMYCH wejściach (ON wyżej clampuje gps, OFF nie)."""
    monkeypatch.setattr(C, FLAG, False)
    monkeypatch.setattr(C, "ENABLE_PRE_SHIFT_DEPARTURE_CLAMP", True)  # stara ścieżka aktywna
    now = datetime(2026, 7, 2, 8, 30, tzinfo=timezone.utc)
    af = now + timedelta(minutes=20)
    se = now + timedelta(hours=10)
    v, r, m, plan = F.check_feasibility_v2(
        courier_pos=(53.132, 23.168), bag=[], new_order=_order(),
        shift_end=se, shift_start=af, now=now, pos_source="gps",
        available_from=af, sla_minutes=35)
    assert v == "MAYBE"
    assert not m.get("pre_shift_clamp_applied")   # gps → stara ścieżka NIE clampuje
    assert not m.get("af_clamp_applied")


def test_c3_on_pre_shift_pickup_after_shift_start(osrm_mock, monkeypatch):
    monkeypatch.setattr(C, FLAG, True)
    now = datetime(2026, 7, 2, 8, 30, tzinfo=timezone.utc)
    af = now + timedelta(minutes=20)
    se = now + timedelta(hours=10)
    v, r, m, plan = F.check_feasibility_v2(
        courier_pos=(53.132, 23.168), bag=[], new_order=_order(),
        shift_end=se, shift_start=af, now=now, pos_source="pre_shift",
        available_from=af, sla_minutes=35)
    assert m.get("earliest_departure_utc") == af.isoformat()
    assert plan is not None and plan.pickup_at["O3"] >= af   # odbiór ≥ start zmiany


def test_c3_on_no_gps_on_shift_is_noop(osrm_mock, monkeypatch):
    """no_gps on-shift → available_from = now → available_from ≤ now → BEZ clamp (no-op)."""
    monkeypatch.setattr(C, FLAG, True)
    now = datetime(2026, 7, 2, 12, 0, tzinfo=timezone.utc)
    af = now  # on-shift = now
    se = now + timedelta(hours=6)
    v, r, m, plan = F.check_feasibility_v2(
        courier_pos=(53.132, 23.168), bag=[], new_order=_order(),
        shift_end=se, shift_start=now - timedelta(hours=2), now=now, pos_source="no_gps",
        available_from=af, sla_minutes=35)
    assert not m.get("af_clamp_applied")   # available_from=now → nie podnosi


# ── #5 plan_recheck — anchor floor / leak ────────────────────────────────────
def _seed_plan_inputs():
    now = datetime(2026, 7, 2, 8, 30, tzinfo=timezone.utc)
    af = datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc)   # shift 11:00 Warsaw (future)
    orders_state = {"O5": {"status": "assigned",
                           "pickup_coords": [53.130, 23.160],
                           "delivery_coords": [53.150, 23.190],
                           "czas_kuriera_warsaw": None}}  # None → refloor no-op (izoluj shift-floor)
    gps = {"T5": {"lat": 53.140, "lon": 23.170,
                  "ts": now.isoformat()}}  # świeży GPS → anchor_departure=None → base=now (leak bez floora)
    return now, af, orders_state, gps


def _run_gen_plan_capture(monkeypatch, now, af, orders_state, gps):
    captured = {}
    monkeypatch.setattr(PM, "save_plan",
                        lambda cid, body, **kw: captured.setdefault(cid, body))
    monkeypatch.setattr(CR, "resolve_available_from_by_cid",
                        lambda cid, now_utc=None, **kw: (af, "shift_start"))
    ok = PR._gen_one_bag_plan("T5", ["O5"], orders_state, gps, now, RS)
    return ok, captured


def _earliest_pickup(body):
    pk = [s for s in body.get("stops", []) if s.get("type") == "pickup"]
    return min(datetime.fromisoformat(s["predicted_at"]).astimezone(timezone.utc) for s in pk)


def test_c5_anchor_floored_on(osrm_mock, monkeypatch):
    monkeypatch.setattr(C, FLAG, True)
    now, af, os_, gps = _seed_plan_inputs()
    ok, captured = _run_gen_plan_capture(monkeypatch, now, af, os_, gps)
    assert ok and "T5" in captured
    assert _earliest_pickup(captured["T5"]) >= af  # NIE odclampowuje poniżej startu zmiany


def test_c5_leak_reproduced_off(osrm_mock, monkeypatch):
    """Dowód leaku bez L4: flaga OFF → anchor=now → odbiór planowany PRZED startem zmiany."""
    monkeypatch.setattr(C, FLAG, False)
    now, af, os_, gps = _seed_plan_inputs()
    ok, captured = _run_gen_plan_capture(monkeypatch, now, af, os_, gps)
    assert ok and "T5" in captured
    assert _earliest_pickup(captured["T5"]) < af  # leak: odbiór przed zmianą


# ── chokepoint — state_machine effective_pickup_at ───────────────────────────
def _assign_event(decl):
    return {"event_type": "COURIER_ASSIGNED", "order_id": "OCH", "courier_id": "C9",
            "source": "test",
            "payload": {"czas_kuriera_warsaw": decl, "czas_kuriera_hhmm": "11:00"}}


def test_chokepoint_effective_pickup_at_and_declaration_untouched(monkeypatch):
    monkeypatch.setattr(C, FLAG, True)
    from dispatch_v2 import state_machine as SM
    # deklaracja restauracji 11:00 Warsaw, ale kurier dostępny dopiero 11:30 (shift)
    decl = "2026-07-02T11:00:00+02:00"
    af = datetime(2026, 7, 2, 9, 30, tzinfo=timezone.utc)  # 11:30 Warsaw > deklaracja
    monkeypatch.setattr(CR, "resolve_available_from_by_cid",
                        lambda cid, now_utc=None, **kw: (af, "shift_start"))
    monkeypatch.setattr(SM, "get_order", lambda oid: {"status": "new"})
    captured = {}
    monkeypatch.setattr(SM, "upsert_order",
                        lambda oid, merged, event=None: captured.update(merged) or dict(merged))
    SM.update_from_event(_assign_event(decl))
    # effective_pickup_at = max(deklaracja 11:00, available_from 11:30) = 11:30
    assert "effective_pickup_at" in captured
    eff = datetime.fromisoformat(captured["effective_pickup_at"]).astimezone(timezone.utc)
    assert eff == af
    assert captured["effective_pickup_source"] == "available_from"
    # DEKLARACJA NIENARUSZONA (czas_kuriera trzymany bez zmian, R27 frozen)
    assert captured["czas_kuriera_warsaw"] == decl
    assert captured["czas_kuriera_hhmm"] == "11:00"


def test_chokepoint_off_no_effective_field(monkeypatch):
    monkeypatch.setattr(C, FLAG, False)
    from dispatch_v2 import state_machine as SM
    decl = "2026-07-02T11:00:00+02:00"
    monkeypatch.setattr(SM, "get_order", lambda oid: {"status": "new"})
    captured = {}
    monkeypatch.setattr(SM, "upsert_order",
                        lambda oid, merged, event=None: captured.update(merged) or dict(merged))
    SM.update_from_event(_assign_event(decl))
    assert "effective_pickup_at" not in captured  # flaga OFF → pole nie powstaje


# ── parytet bliźniaków #1↔#3↔#5 (ta sama wartość floora) ─────────────────────
def test_twin_parity_floor_value_identical(osrm_mock, monkeypatch):
    """#1 floor, #3 earliest_departure i #5 anchor MUSZĄ być === available_from_from_shift_start."""
    monkeypatch.setattr(C, FLAG, True)
    now = datetime(2026, 7, 2, 8, 30, tzinfo=timezone.utc)
    ss = now + timedelta(minutes=20)
    se = now + timedelta(hours=10)
    af_expected, _src = CR.available_from_from_shift_start(ss, now)

    # #3: earliest_departure metric
    _, _, m3, _ = F.check_feasibility_v2(
        courier_pos=(53.132, 23.168), bag=[], new_order=_order(),
        shift_end=se, shift_start=ss, now=now, pos_source="pre_shift",
        available_from=af_expected, sla_minutes=35)
    ed3 = datetime.fromisoformat(m3["earliest_departure_utc"]).astimezone(timezone.utc)

    # #1: floor target = eta ustawiony na available_from
    c = _Cand({"available_from_utc": af_expected.isoformat(),
               "eta_pickup_utc": now.isoformat()})
    D._l4_floor_candidate_eta(c)
    eta1 = datetime.fromisoformat(c.metrics["eta_pickup_utc"]).astimezone(timezone.utc)

    # #5: resolve_available_from_by_cid daje tę samą wartość
    monkeypatch.setattr(CR, "resolve_available_from_by_cid",
                        lambda cid, now_utc=None, **kw: (af_expected, "shift_start"))
    af5, _ = CR.resolve_available_from_by_cid("X", now)

    assert ed3 == af_expected == eta1 == af5.astimezone(timezone.utc)


# ── MUTATION ×2 (C13) — mutuj cel in-memory, POTWIERDŹ że realna asercja PADA ─
def test_mutation_source_max_to_min_detected(monkeypatch):
    """(i) max→min w źródle. Baseline (prawdziwa fn) spełnia kontrakt; mutant min()
    łamie go → TA SAMA asercja co test_A_floor_future PADA pod mutantem."""
    now = datetime(2026, 7, 2, 9, 0, tzinfo=timezone.utc)
    ss = now + timedelta(minutes=30)
    # baseline — prawdziwa funkcja spełnia kontrakt (sanity)
    af0, _ = CR.available_from_from_shift_start(ss, now)
    assert af0 == ss and _ == "shift_start"

    def _mutant_min(shift_start, now_utc=None):
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)
        if shift_start is None:
            return now_utc, "unknown"
        s = shift_start if shift_start.tzinfo else shift_start.replace(tzinfo=timezone.utc)
        return (min(s, now_utc), "shift_start")   # BUG: min zamiast max
    monkeypatch.setattr(CR, "available_from_from_shift_start", _mutant_min)
    # realna asercja kontraktu (jak test_A_floor_future) MUSI PAŚĆ pod mutantem:
    with pytest.raises(AssertionError):
        af_m, _s = CR.available_from_from_shift_start(ss, now)
        assert af_m == ss, "floor future musi zwrócić shift_start"


def test_mutation_remove_floor_c5_detected(osrm_mock, monkeypatch):
    """(ii) usunięcie floora w #5 (symulacja: flaga OFF = brak floora anchoru).
    Z floorem (ON) odbiór ≥ shift_start; bez floora (OFF) < shift_start → TA SAMA
    asercja co test_c5_anchor_floored_on PADA → strażnik wykrywa regres."""
    now, af, os_, gps = _seed_plan_inputs()
    # baseline: z floorem (ON) — kontrakt spełniony
    monkeypatch.setattr(C, FLAG, True)
    ok_on, cap_on = _run_gen_plan_capture(monkeypatch, now, af, os_, gps)
    assert ok_on and _earliest_pickup(cap_on["T5"]) >= af   # sanity: floor działa
    # mutacja: floor usunięty (OFF) → realna asercja „pickup ≥ af" PADA
    monkeypatch.setattr(C, FLAG, False)
    ok_off, cap_off = _run_gen_plan_capture(monkeypatch, now, af, os_, gps)
    assert ok_off
    with pytest.raises(AssertionError):
        assert _earliest_pickup(cap_off["T5"]) >= af, "bez floora odbiór przed startem = regres"


# ── guard: resolve shift_start dla on-shift-not-dispatchable ─────────────────
def test_guard_resolves_shift_start_for_nondispatchable_onshift(monkeypatch):
    """Strażnik przestaje być ślepy: kurier on-shift bez GPS (poza dispatchable_fleet)
    → resolve_shift_start dociąga jego shift_start z grafiku (nie 'unknown')."""
    from dispatch_v2.tools import pickup_floor_guard as G
    ss = datetime(2026, 7, 2, 11, 0, tzinfo=WAW)
    # dispatchable_fleet PUSTE (wieczór / brak GPS), plan cid=NONDISP nierozwiązany
    monkeypatch.setattr(CR, "dispatchable_fleet", lambda: [])
    # kanoniczny resolver zwraca shift_start dla tego cid (grafik po nazwie)
    monkeypatch.setattr(CR, "resolve_shift_start", lambda name, schedule=None, **kw: None)
    monkeypatch.setattr(CR, "resolve_shift_start_by_cid",
                        lambda cid, name=None, schedule=None: ss if str(cid) == "NONDISP" else None)
    fmap = G._load_fleet_map(plan_cids=["NONDISP"])
    assert fmap.get("NONDISP", {}).get("shift_start") == ss  # rozwiązany, NIE unknown


if __name__ == "__main__":
    sys.exit(pytest.main([__file__, "-v", "-rx"]))
