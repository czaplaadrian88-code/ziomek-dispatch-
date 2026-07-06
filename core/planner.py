"""core.planner — K15 programu refaktoru (ADR-R03): wspólne planowanie worka.

PROBLEM (kontrakt ① — bliźniak „generatory planów"): parametryzacja symulatora
tier→(dwell_pickup, dwell_dropoff, drive_speed_mult) żyła w DWÓCH kopiach o
różnej semantyce: silnik (feasibility_v2 ~:835-845; dwell ZAWSZE tier-aware)
vs re-planer (plan_recheck._gen_one_bag_plan; dwell tier-aware TYLKO za flagą
ENABLE_PLAN_RECHECK_TIER_DWELL, inaczej defaulty symulatora). Każda przyszła
zmiana dwell/tempa musiała trafić w oba miejsca — klasa „fix w 1 z N bliźniaków".

TEN MODUŁ = JEDNO źródło obu semantyk + wspólne wejście symulacji:
  - tier_params(tier, recheck_dwell_gate=...) — parametry z jawnym przełącznikiem
    semantyki re-planera (flaga czytana HOT przez C.flag — koniec znaczenia
    env-rozjazdów procesów dla tej ścieżki);
  - plan_bag(...) — wspólne wywołanie simulate_bag_route_v2 z WSTRZYKIWALNYM
    simulate_fn (zachowuje kontrakty suity: feasibility patchuje własny symbol,
    plan_recheck dostaje R przez argument — obie ścieżki podają swój uchwyt).

ADOPCJA:
  - silnik: deleguje tier_params (parametry 1:1); wywołanie simulate zostaje
    lokalnym symbolem feasibility_v2 — ŚWIADOME N-D: setki testów patchują
    `feasibility_v2.simulate_bag_route_v2`; przepięcie = złamanie kontraktu
    suity bez zysku (to TA SAMA funkcja route_simulator_v2).
  - re-planer: za flagą ENABLE_PLANNER_UNIFIED (OFF = stary inline bajt-w-bajt)
    parametry+wywołanie idą przez ten moduł (simulate_fn = wstrzyknięte R);
    ENABLE_PLANNER_UNIFIED_SHADOW (przy głównej OFF) liczy parametry OBIEMA
    drogami i loguje rozjazd (planner_param_mismatch) — tani dowód parytetu
    na żywo bez podwójnej symulacji.

Flip obu flag = wyłącznie jawne TAK Adriana (kanon flags.json; consty OFF).
"""
from __future__ import annotations

from typing import Any, Callable, Optional, Tuple

from dispatch_v2 import common as C
from dispatch_v2 import route_simulator_v2 as _R2


def tier_params(courier_tier: Optional[str], *,
                recheck_dwell_gate: bool = False) -> Tuple[float, float, float]:
    """(dwell_pickup, dwell_dropoff, drive_speed_mult) dla tieru kuriera.

    recheck_dwell_gate=False (SILNIK): dwell ZAWSZE tier-aware
    (C.dwell_for_tier — semantyka feasibility_v2 od 2026-05-17).
    recheck_dwell_gate=True (RE-PLANER): dwell tier-aware TYLKO gdy flaga
    ENABLE_PLAN_RECHECK_TIER_DWELL (hot, C.flag); inaczej defaulty symulatora
    (R.DWELL_*) — dokładnie dotychczasowa semantyka plan_recheck (2026-06-26).
    speed_mult: wspólnie C.speed_mult_for_tier (flaga korekcji OFF → 1.0).
    """
    drive_speed_mult = C.speed_mult_for_tier(courier_tier)
    if recheck_dwell_gate and not C.flag("ENABLE_PLAN_RECHECK_TIER_DWELL", False):
        return _R2.DWELL_PICKUP_MIN, _R2.DWELL_DROPOFF_MIN, drive_speed_mult
    dwell_pickup, dwell_dropoff = C.dwell_for_tier(courier_tier)
    return dwell_pickup, dwell_dropoff, drive_speed_mult


def plan_bag(courier_pos, bag, new_order, now, *,
             sla_minutes: float,
             courier_tier: Optional[str] = None,
             base_sequence=None,
             earliest_departure=None,
             recheck_dwell_gate: bool = False,
             dwell_pickup: Optional[float] = None,
             dwell_dropoff: Optional[float] = None,
             drive_speed_mult: Optional[float] = None,
             simulate_fn: Optional[Callable[..., Any]] = None):
    """Wspólne wejście symulacji planu worka (silnik ORAZ re-planer).

    Parametry dwell/tempo: jawne argumenty wygrywają (caller już policzył —
    np. silnik, który loguje je do metrics); brakujące dociągane z tier_params.
    simulate_fn: uchwyt symulatora od CALLERA (kontrakt wstrzykiwania suity);
    None → kanoniczny route_simulator_v2.simulate_bag_route_v2.
    """
    if dwell_pickup is None or dwell_dropoff is None or drive_speed_mult is None:
        _dp, _dd, _mult = tier_params(courier_tier,
                                      recheck_dwell_gate=recheck_dwell_gate)
        dwell_pickup = _dp if dwell_pickup is None else dwell_pickup
        dwell_dropoff = _dd if dwell_dropoff is None else dwell_dropoff
        drive_speed_mult = _mult if drive_speed_mult is None else drive_speed_mult
    fn = simulate_fn if simulate_fn is not None else _R2.simulate_bag_route_v2
    return fn(courier_pos, bag, new_order, now=now, sla_minutes=sla_minutes,
              base_sequence=base_sequence, earliest_departure=earliest_departure,
              dwell_pickup=dwell_pickup, dwell_dropoff=dwell_dropoff,
              drive_speed_mult=drive_speed_mult)
