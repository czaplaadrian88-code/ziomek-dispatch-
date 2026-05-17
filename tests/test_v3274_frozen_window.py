"""V3.27.4 Frozen czas_kuriera TSP time window tests (KROK 2, 2026-04-27).

4 unit + 1 integration #469014 + 8/8 sanity sweep regression (separate file).

E3 sprint 2026-05-17: frozen czas_kuriera = KOTWICA restauracyjna, nie box ±5.
Okno = [czas_kuriera - 5, V327_DROP_TIME_WINDOW_MAX_MIN] — dolna granica trzyma
pickup ~przy zadeklarowanym czasie (nie odbiera przed gotowością), górna LUŹNA
żeby reachability (kurier nie zdąży w ±5) NIE wywoływała INFEASIBLE i nie
kasowała optymalizacji OR-Tools (diagnoza order 474266).
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


from dispatch_v2 import common as C  # noqa: E402
from dispatch_v2.route_simulator_v2 import OrderSim, simulate_bag_route_v2  # noqa: E402


# Helper: mimics the TSP time window construction logic from
# route_simulator_v2 dla isolated unit test (no full TSP solve).
def _compute_pickup_time_window(ref, now, common_module):
    """Replicate route_simulator_v2 time_windows construction for a single
    pickup node. Returns (open_min, close_min) tuple lub None gdy ready=None."""
    ready = getattr(ref, "pickup_ready_at", None) if ref is not None else None
    if ready is None:
        return (0.0, common_module.V327_DROP_TIME_WINDOW_MAX_MIN)
    try:
        open_min = max(0.0, (ready - now).total_seconds() / 60.0)
        czas_kuriera_committed = (
            common_module.ENABLE_V3274_FROZEN_PICKUP_WINDOW
            and ref is not None
            and getattr(ref, "czas_kuriera_warsaw", None) is not None
        )
        if czas_kuriera_committed:
            # E3 2026-05-17: kotwica — dolna granica (open-5), górna LUŹNA.
            window_open = max(0.0, open_min - common_module.V3274_FROZEN_PICKUP_WINDOW_MIN)
            window_close = common_module.V327_DROP_TIME_WINDOW_MAX_MIN
            return (window_open, window_close)
        else:
            close_min = open_min + common_module.V327_PICKUP_TIME_WINDOW_CLOSE_MIN
            return (open_min, close_min)
    except Exception:
        return (0.0, common_module.V327_DROP_TIME_WINDOW_MAX_MIN)


# ─── Unit tests ───────────────────────────────────────────────

def test_frozen_window_committed_true():
    """E3: ck=16:55, now=16:30 → kotwica (20.0, V327_DROP_TIME_WINDOW_MAX_MIN).
    Dolna granica 20 = open(25)-5; górna luźna (reachability nie blokuje)."""
    now = datetime(2026, 4, 27, 14, 30, tzinfo=timezone.utc)  # 16:30 Warsaw
    ck = datetime(2026, 4, 27, 14, 55, tzinfo=timezone.utc)   # 16:55 Warsaw
    sim = OrderSim(
        order_id="469008",
        pickup_coords=(53.13, 23.16),
        delivery_coords=(53.14, 23.17),
        pickup_ready_at=ck,
    )
    sim.czas_kuriera_warsaw = ck.isoformat()  # marks as frozen
    open_close = _compute_pickup_time_window(sim, now, C)
    assert open_close == (20.0, C.V327_DROP_TIME_WINDOW_MAX_MIN), \
        f"Expected (20.0, {C.V327_DROP_TIME_WINDOW_MAX_MIN}), got {open_close}"


def test_frozen_window_committed_false():
    """czas_kuriera_warsaw=None → status quo 60-min window [open, open+60]."""
    now = datetime(2026, 4, 27, 14, 33, tzinfo=timezone.utc)
    ready = datetime(2026, 4, 27, 14, 57, tzinfo=timezone.utc)  # 16:57 Warsaw
    sim = OrderSim(
        order_id="469014_new",
        pickup_coords=(53.14, 23.18),
        delivery_coords=(53.15, 23.19),
        pickup_ready_at=ready,
    )
    sim.czas_kuriera_warsaw = None  # NEW order, czas_kuriera not yet declared
    open_close = _compute_pickup_time_window(sim, now, C)
    assert open_close[0] == 24.0, f"Expected open=24.0, got {open_close[0]}"
    assert open_close[1] == 84.0, f"Expected close=24+60=84, got {open_close[1]}"


def test_frozen_window_clamp_negative_open():
    """ck=16:33, now=16:30 → open_raw=3, window_open=max(0, 3-5)=0 (clamp).
    E3: górna granica luźna = V327_DROP_TIME_WINDOW_MAX_MIN."""
    now = datetime(2026, 4, 27, 14, 30, tzinfo=timezone.utc)
    ck = datetime(2026, 4, 27, 14, 33, tzinfo=timezone.utc)
    sim = OrderSim(
        order_id="frozen_close",
        pickup_coords=(53.13, 23.16),
        delivery_coords=(53.14, 23.17),
        pickup_ready_at=ck,
    )
    sim.czas_kuriera_warsaw = ck.isoformat()
    open_close = _compute_pickup_time_window(sim, now, C)
    # open_raw = 3.0, clamp window_open = max(0, 3-5) = 0.0; close = luźna
    assert open_close == (0.0, C.V327_DROP_TIME_WINDOW_MAX_MIN), \
        f"Expected (0.0, {C.V327_DROP_TIME_WINDOW_MAX_MIN}), got {open_close}"


def test_frozen_window_no_ready_at():
    """pickup_ready_at=None → drop fallback (0, 120)."""
    now = datetime(2026, 4, 27, 14, 30, tzinfo=timezone.utc)
    sim = OrderSim(
        order_id="no_ready",
        pickup_coords=(53.13, 23.16),
        delivery_coords=(53.14, 23.17),
        pickup_ready_at=None,
    )
    open_close = _compute_pickup_time_window(sim, now, C)
    assert open_close == (0.0, C.V327_DROP_TIME_WINDOW_MAX_MIN), \
        f"Expected (0, {C.V327_DROP_TIME_WINDOW_MAX_MIN}), got {open_close}"


# ─── Integration test #469014 ground truth ────────────────────

def test_integration_469014_pani_pierozek_frozen_window():
    """#469014 TASK F scenario — E3 zachowanie (sprint 2026-05-17):
    - Pani Pierożek (469008) committed czas_kuriera=16:55
    - Rany Julek (469014) candidate, decision_ts=16:33:22

    E3: frozen ck = kotwica. Okno = [open-5, V327_DROP_TIME_WINDOW_MAX_MIN]:
    - dolna granica ~16.63 min trzyma pickup ~przy zadeklarowanym 16:55
      (kurier nie odbiera przed gotowością restauracji)
    - górna granica LUŹNA — pickup wypadający 17:09 (36.1 min) MIEŚCI się w
      oknie; kotwica USTAWIA trasę, nie kasuje optymalizacji. Pre-E3 box ±5
      odrzucał ten plan → greedy ślepy (diagnoza 474266).
    """
    decision_ts = datetime(2026, 4, 27, 14, 33, 22, tzinfo=timezone.utc)
    pp_ck = datetime(2026, 4, 27, 14, 55, 0, tzinfo=timezone.utc)  # 16:55 Warsaw

    # Pani Pierożek bag order with committed czas_kuriera
    pp = OrderSim(
        order_id="469008",
        pickup_coords=(53.130, 23.165),  # approximate Białystok center
        delivery_coords=(53.135, 23.170),  # Zwierzyniecka 11
        pickup_ready_at=pp_ck,
        status="assigned",
    )
    pp.czas_kuriera_warsaw = pp_ck.isoformat()  # FROZEN

    # Time window calc
    open_close = _compute_pickup_time_window(pp, decision_ts, C)
    # open_raw = (14:55 - 14:33:22) / 60 = 21.6333 min
    # window_open = max(0, 21.63 - 5) = 16.63 (kotwica dolna)
    # window_close = V327_DROP_TIME_WINDOW_MAX_MIN (luźna)
    assert abs(open_close[0] - 16.633) < 0.1, f"Expected ~16.63, got {open_close[0]}"
    assert open_close[1] == C.V327_DROP_TIME_WINDOW_MAX_MIN, \
        f"Expected luźna górna {C.V327_DROP_TIME_WINDOW_MAX_MIN}, got {open_close[1]}"

    # E3: 17:09 pickup (36.1 min od decyzji) MIEŚCI się teraz w oknie kotwicy
    # — plan OR-Tools NIE jest odrzucany (pre-E3 box ±5 by go skasował).
    plan_pickup_at_17_09 = datetime(2026, 4, 27, 15, 9, 27, tzinfo=timezone.utc)
    pickup_min_from_decision = (plan_pickup_at_17_09 - decision_ts).total_seconds() / 60.0
    assert open_close[0] <= pickup_min_from_decision <= open_close[1], \
        f"Pickup {pickup_min_from_decision:.1f} powinien mieścić się w kotwicy {open_close}"


def test_integration_469014_new_order_status_quo():
    """Symetryczny test: NEW order Rany Julek (czas_kuriera=None) używa
    status quo 60-min window."""
    decision_ts = datetime(2026, 4, 27, 14, 33, 22, tzinfo=timezone.utc)
    rj_ready = datetime(2026, 4, 27, 14, 57, 57, tzinfo=timezone.utc)  # restaurant prep deadline

    rj = OrderSim(
        order_id="469014",
        pickup_coords=(53.135, 23.180),
        delivery_coords=(53.140, 23.185),
        pickup_ready_at=rj_ready,
        status="assigned",
    )
    rj.czas_kuriera_warsaw = None  # NEW order, courier hasn't accepted yet

    open_close = _compute_pickup_time_window(rj, decision_ts, C)
    # open_raw = (14:57:57 - 14:33:22) / 60 = 24.5833
    # close = 24.58 + 60 = 84.58 (status quo)
    assert abs(open_close[0] - 24.583) < 0.1, f"Expected ~24.58, got {open_close[0]}"
    assert abs(open_close[1] - 84.583) < 0.1, f"Expected ~84.58, got {open_close[1]}"


# ─── Regression: flag default check ──────────────────────────

def test_flag_default_true():
    """V3.27.4 flag default True per Adrian's safety preference."""
    assert C.ENABLE_V3274_FROZEN_PICKUP_WINDOW is True
    assert C.V3274_FROZEN_PICKUP_WINDOW_MIN == 5.0


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"FAIL {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{passed+failed} passed")
    sys.exit(0 if failed == 0 else 1)
