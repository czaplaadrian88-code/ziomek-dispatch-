"""N5-S2 — committed-pickup punctuality penalty (regression tests, 2026-06-18).

Proves the penalty does what it's meant to, on the REAL production code paths:

  1-3. tsp_solver.solve_tsp_with_constraints (where the penalty primitive lives —
       SetCumulVarSoftUpperBound on committed pickup nodes). Fully deterministic:
       we hand the solver a controlled time matrix, so the trade-off is exact.
       - OFF (no penalties): solver minimises drive → slides the committed pickup
         late (visits the cheap non-committed pickup first).
       - ON  (penalty on the committed pickup): solver reorders to honour it,
         paying MORE total drive — a real trade-off, not a free win.
       - SOFT: an impossible-to-meet bound still returns a FEASIBLE plan, never
         None/INFEASIBLE (the hard-window 7500/day lesson — see N5_DESIGN.md).

  4. simulate_bag_route_v2 end-to-end with the live flag toggled via
     common.decision_flag (flags.json now carries the key, so patching the module
     constant would NOT work — we patch decision_flag, same as the replay did).
     Proves the WIRING: flag ON → route_simulator builds penalties from
     czas_kuriera_warsaw → the committed pickup is scheduled EARLIER than OFF.

Scenario (shared geometry), 5 stops, depot=courier index 0:
    0=courier  1=P_A(committed)  2=D_A  3=P_B(new)  4=D_B
  time matrix (min), FAR=50, only these legs cheap:
    B-first  [3,4,1,2] = 0->3(1)+3->4(1)+4->1(3)+1->2(1) = 6 ; P_A reached at 5
    A-first  [1,2,3,4] = 0->1(2)+1->2(1)+2->3(4)+3->4(1) = 8 ; P_A reached at 2
  => OFF picks B-first (cheaper, P_A late at 5); a penalty anchoring P_A early
     (bound < 5) forces A-first (P_A at 2) at the cost of +2 min total drive.
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from dispatch_v2 import common as C  # noqa: E402
from dispatch_v2 import tsp_solver  # noqa: E402
from dispatch_v2 import route_simulator_v2  # noqa: E402
from dispatch_v2.route_simulator_v2 import (  # noqa: E402
    OrderSim, simulate_bag_route_v2, set_committed_pickup_tolerance,
)

FAR = 50.0
# time matrix in minutes; index 0=courier 1=P_A 2=D_A 3=P_B 4=D_B
TMIN = [
    [0.0,  2.0, FAR,  1.0, FAR],   # courier -> *
    [FAR,  0.0, 1.0, FAR, FAR],    # P_A -> *
    [FAR, FAR,  0.0, 4.0, FAR],    # D_A -> *
    [FAR, FAR, FAR,  0.0, 1.0],    # P_B -> *
    [FAR, 3.0, FAR, FAR,  0.0],    # D_B -> *
]
PAIRS = [(1, 2), (3, 4)]  # (P_A,D_A), (P_B,D_B)


def _solve(committed_penalties):
    return tsp_solver.solve_tsp_with_constraints(
        num_stops=5,
        pickup_drop_pairs=PAIRS,
        distance_matrix_km=TMIN,
        time_matrix_min=TMIN,
        time_windows=None,            # isolate: committed handled ONLY by penalty
        pickup_committed_penalties=committed_penalties,
        time_limit_ms=200,
    )


# ─── 1. OFF baseline: committed pickup slid late ──────────────────────────────
def test_baseline_no_penalty_slides_committed_pickup_late():
    sol = _solve(None)
    assert sol is not None and sol.sequence, f"no solution: {sol}"
    seq = sol.sequence
    # B-first is cheaper → P_A (1) visited AFTER P_B (3)
    assert seq.index(1) > seq.index(3), \
        f"baseline should slide committed P_A after P_B, got {seq}"
    assert abs(sol.total_time_min - 6.0) < 0.5, \
        f"baseline shortest route ~6 min, got {sol.total_time_min}"


# ─── 2. ON: penalty reorders to honour committed pickup, paying more drive ────
def test_committed_penalty_reorders_to_honor_pickup():
    base = _solve(None)
    # bound_min=3 (< P_A's late cumul of 5 under B-first), coeff high
    pen = [None, (3.0, 1000.0), None, None, None]
    sol = _solve(pen)
    assert sol is not None and sol.sequence, f"no solution: {sol}"
    seq = sol.sequence
    # now P_A (1) visited BEFORE P_B (3)
    assert seq.index(1) < seq.index(3), \
        f"penalty should pull committed P_A before P_B, got {seq}"
    # real trade-off: honouring the committed pickup costs MORE total drive
    assert sol.total_time_min > base.total_time_min + 0.5, \
        f"ON route ({sol.total_time_min}) should exceed OFF ({base.total_time_min})"


# ─── 3. SOFT: impossible bound never produces INFEASIBLE ──────────────────────
def test_committed_penalty_is_soft_never_infeasible():
    # bound_min=0 is physically impossible to meet (P_A unreachable at t=0),
    # yet a SOFT upper bound must still return a feasible plan (just penalised).
    pen = [None, (0.0, 100000.0), None, None, None]
    sol = _solve(pen)
    assert sol is not None, "SOFT bound must NOT yield INFEASIBLE/None (7500/day lesson)"
    assert sol.sequence, f"expected a feasible sequence, got {sol.sequence}"
    # precedence still holds: each pickup before its drop
    for p, d in PAIRS:
        assert sol.sequence.index(p) < sol.sequence.index(d), \
            f"pickup {p} must precede drop {d}: {sol.sequence}"


# ─── 4. Flag wiring end-to-end through simulate_bag_route_v2 ───────────────────
COORDS = {
    "C":   (53.000, 23.000),
    "P_A": (53.010, 23.000),
    "D_A": (53.020, 23.000),
    "P_B": (53.030, 23.000),
    "D_B": (53.040, 23.000),
}
_POS = {COORDS["C"]: 0, COORDS["P_A"]: 1, COORDS["D_A"]: 2,
        COORDS["P_B"]: 3, COORDS["D_B"]: 4}


def _mock_osrm_table(points_a, points_b):
    """Deterministic legs from TMIN, keyed by coordinate (order-independent)."""
    return [[{"duration_s": TMIN[_POS[a]][_POS[b]] * 60.0, "osrm_fallback": False}
             for b in points_b] for a in points_a]


def _run_simulate(now, ck_iso):
    """One simulate_bag_route_v2 run; bag = committed A (assigned), new = B."""
    a = OrderSim(order_id="A", pickup_coords=COORDS["P_A"],
                 delivery_coords=COORDS["D_A"], status="assigned",
                 pickup_ready_at=now)
    a.czas_kuriera_warsaw = ck_iso
    b = OrderSim(order_id="B", pickup_coords=COORDS["P_B"],
                 delivery_coords=COORDS["D_B"], status="assigned",
                 pickup_ready_at=now)
    return simulate_bag_route_v2(COORDS["C"], [a], b, now=now)


def test_flag_wiring_on_schedules_committed_pickup_earlier_than_off():
    now = datetime(2026, 6, 16, 12, 0, 0, tzinfo=timezone.utc)
    # committed pickup A was due 3 min ago → bound = -3 + tol(5) = 2 (< late cumul)
    ck_iso = (now - timedelta(minutes=3)).isoformat()

    orig_flag = C.decision_flag
    orig_table = route_simulator_v2.osrm_client.table
    route_simulator_v2.osrm_client.table = _mock_osrm_table
    set_committed_pickup_tolerance(5.0)
    try:
        # OFF
        C.decision_flag = lambda n: False if n == "ENABLE_OBJ_COMMITTED_PICKUP_PENALTY" else orig_flag(n)
        plan_off = _run_simulate(now, ck_iso)
        # ON
        C.decision_flag = lambda n: True if n == "ENABLE_OBJ_COMMITTED_PICKUP_PENALTY" else orig_flag(n)
        plan_on = _run_simulate(now, ck_iso)
    finally:
        C.decision_flag = orig_flag
        route_simulator_v2.osrm_client.table = orig_table
        set_committed_pickup_tolerance(None)

    assert plan_off is not None and plan_on is not None
    assert "A" in plan_off.pickup_at and "A" in plan_on.pickup_at, \
        "committed order A must have a planned pickup in both plans"
    # OFF slides A late (after B); ON pulls A in earlier than OFF
    assert plan_off.pickup_at["A"] > plan_off.pickup_at["B"], \
        "OFF baseline should pick B before the committed A (the bug we fix)"
    assert plan_on.pickup_at["A"] < plan_off.pickup_at["A"], \
        f"flag ON must schedule committed A earlier: ON={plan_on.pickup_at['A']} OFF={plan_off.pickup_at['A']}"


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in sorted(globals().items())
             if k.startswith("test_") and callable(v)]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"FAIL {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{passed + failed} passed")
    sys.exit(0 if failed == 0 else 1)
