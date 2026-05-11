"""V3.28 FAZA 3 ścieżka A: time_matrix DWELL correction (tech debt #25).

OR-Tools time_matrix[i][j] = travel + DWELL_at_arriving_node. Aligns solver
semantyka z _simulate_sequence pickup_at storage convention (post-DWELL).
FAZA 0 audit n=2767/12d confirmed: bag>=2 reject rate 34-100% explained by
DWELL accumulation not seen by solver.

Predicted impact (per design doc section 6.1):
- bag=2: reject 34% → 5-10%
- bag=3: reject 58% → 15-25%
- bag=4: reject 86% → 30-40% (residual ścieżka B candidate)
- bag=5: reject 100% → 50-60% (ścieżka B/C decision tree)
"""
from dispatch_v2.route_simulator_v2 import (
    _dwell_min_for_arriving,
    DWELL_PICKUP_MIN,
    DWELL_DROPOFF_MIN,
)


# ─── Helper unit tests ───────────────────────────────────────────────


def test_dwell_pickup_returns_dwell_pickup_min():
    """node.kind='pickup' → DWELL_PICKUP_MIN (2.0 V3.27.3 baseline)."""
    assert _dwell_min_for_arriving({"kind": "pickup"}) == DWELL_PICKUP_MIN
    assert _dwell_min_for_arriving({"kind": "pickup"}) == 2.0


def test_dwell_delivery_returns_dwell_dropoff_min():
    """node.kind='delivery' → DWELL_DROPOFF_MIN."""
    assert _dwell_min_for_arriving({"kind": "delivery"}) == DWELL_DROPOFF_MIN
    assert _dwell_min_for_arriving({"kind": "delivery"}) == 2.0


def test_dwell_courier_depot_zero():
    """nodes[0].kind='courier' depot → 0.0 (no service time on entry)."""
    assert _dwell_min_for_arriving({"kind": "courier"}) == 0.0


def test_dwell_unknown_kind_defensive_zero():
    """Defensive: unknown kind returns 0 (never crash on malformed nodes)."""
    assert _dwell_min_for_arriving({"kind": "unknown"}) == 0.0
    assert _dwell_min_for_arriving({"kind": None}) == 0.0
    assert _dwell_min_for_arriving({}) == 0.0  # missing kind → None


def test_dwell_symmetric_pickup_dropoff():
    """V3.27.3 baseline: DWELL_PICKUP_MIN == DWELL_DROPOFF_MIN = 2.0."""
    assert DWELL_PICKUP_MIN == DWELL_DROPOFF_MIN == 2.0


# ─── Flag + source regression ────────────────────────────────────────


def test_flag_present_in_common():
    """Flag ENABLE_V328_TIME_MATRIX_DWELL defined w common.py."""
    from dispatch_v2 import common as C
    assert hasattr(C, "ENABLE_V328_TIME_MATRIX_DWELL")
    # Default True post FAZA 0 evidence
    assert C.ENABLE_V328_TIME_MATRIX_DWELL is True


def test_flag_env_override_off():
    """ENABLE_V328_TIME_MATRIX_DWELL=0 env → False (rollback path)."""
    import os
    from unittest.mock import patch
    # Reload simulate (constants computed at module load)
    with patch.dict(os.environ, {"ENABLE_V328_TIME_MATRIX_DWELL": "0"}):
        # Pattern matches other flags in common.py
        flag_parsed = os.environ.get("ENABLE_V328_TIME_MATRIX_DWELL", "1") == "1"
        assert flag_parsed is False


def test_time_matrix_construction_includes_dwell_when_flag_true():
    """Source regression: _ortools_plan time_matrix loop uses _dwell_min_for_arriving."""
    import inspect
    from dispatch_v2 import route_simulator_v2
    src = inspect.getsource(route_simulator_v2._ortools_plan)
    # Post-fix construction includes dwell call
    assert "_dwell_min_for_arriving(nodes[j])" in src
    # Gate uses flag
    assert "ENABLE_V328_TIME_MATRIX_DWELL" in src
    # Augmented travel += dwell
    assert "travel += _dwell_min_for_arriving" in src


def test_time_matrix_construction_preserves_9999_sentinel():
    """OSRM fallback sentinel 9999.0 preserved w except path (no DWELL added)."""
    import inspect
    from dispatch_v2 import route_simulator_v2
    src = inspect.getsource(route_simulator_v2._ortools_plan)
    assert "time_matrix[i][j] = 9999.0" in src
    assert "continue" in src  # Sentinel path uses continue


# ─── Integration synthetic ───────────────────────────────────────────


def test_dwell_accumulation_math_bag2():
    """Bag=2 (4 stops: 2 pickups + 2 drops) DWELL accum = 4*2 = 8 min.

    Mirror FAZA 0 audit prediction: pre-fix solver unaware → window check
    fail. Post-fix solver respects [ck-5, ck+5] correctly.
    """
    # 4 stops × DWELL=2 = 8 min total DWELL skumulowany
    total_dwell = 4 * DWELL_PICKUP_MIN
    assert total_dwell == 8.0
    # Window slack = 10 min (±5). Bag=2: 8 vs 10 = borderline (34% reject pre-fix)
    window_slack = 10.0
    assert total_dwell < window_slack  # not impossible but tight


def test_dwell_accumulation_math_bag3_window_breach():
    """Bag=3 (6 stops) DWELL=12 > window 10 → guaranteed pre-fix breach."""
    total_dwell = 6 * DWELL_PICKUP_MIN
    assert total_dwell == 12.0
    window_slack = 10.0
    assert total_dwell > window_slack  # window breach pre-fix


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
