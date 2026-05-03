"""V3.28 Fix 3 (incident 03.05.2026) — truthful HEARTBEAT + worker liveness tests.

Pure helper `_v328_compute_heartbeat_state(last_processed_ts, now, pending)`
testowany w izolacji.

Multi-signal stuck detection (Lekcja #66): age>threshold AND pending>threshold.
Quiet period (low pending, brak orderów do processu) NIE wyzwala alert.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import shadow_dispatcher as sd  # noqa: E402


def test_age_zero_just_processed():
    """now == last_processed_ts → age=0, worker_alive=True, is_stuck=False."""
    state = sd._v328_compute_heartbeat_state(last_processed_ts=1000.0, now=1000.0, pending=50)
    assert state["age_sec"] == 0.0
    assert state["worker_alive"] is True
    assert state["is_stuck"] is False


def test_normal_processing_recent():
    """age=60s (1 cycle), pending=200 → worker_alive=True, is_stuck=False (age below threshold)."""
    state = sd._v328_compute_heartbeat_state(last_processed_ts=1000.0, now=1060.0, pending=200)
    assert state["age_sec"] == 60.0
    assert state["worker_alive"] is True
    assert state["is_stuck"] is False  # age <= 300s


def test_stuck_age_high_pending_high_triggers_alert():
    """age=400s + pending=200 → is_stuck=True (multi-signal trigger)."""
    state = sd._v328_compute_heartbeat_state(last_processed_ts=1000.0, now=1400.0, pending=200)
    assert state["age_sec"] == 400.0
    assert state["worker_alive"] is False
    assert state["is_stuck"] is True


def test_quiet_period_low_pending_no_alert_per_lekcja_66():
    """age=400s ALE pending=50 (quiet period) → is_stuck=False (multi-signal NOT met).

    Lekcja #66: single weak signal (high age) + counter signal (low pending=
    nothing to process) = legitimate idle, NIE worker stuck.
    """
    state = sd._v328_compute_heartbeat_state(last_processed_ts=1000.0, now=1400.0, pending=50)
    assert state["age_sec"] == 400.0
    assert state["worker_alive"] is False  # age > 300, ale to NIE jest stuck
    assert state["is_stuck"] is False  # pending <= 100, brak signal "work pending"


def test_high_pending_short_age_no_alert():
    """age=100s ALE pending=500 (busy worker, just processed) → is_stuck=False."""
    state = sd._v328_compute_heartbeat_state(last_processed_ts=1000.0, now=1100.0, pending=500)
    assert state["age_sec"] == 100.0
    assert state["worker_alive"] is True
    assert state["is_stuck"] is False


def test_extreme_stuck_12h_high_pending():
    """Production incident scenario: 12h age + 14000 pending → is_stuck=True."""
    state = sd._v328_compute_heartbeat_state(
        last_processed_ts=1000.0,
        now=1000.0 + 12 * 3600,  # 12h later
        pending=14052,  # incident pending count
    )
    assert state["age_sec"] == 12 * 3600
    assert state["worker_alive"] is False
    assert state["is_stuck"] is True


def test_negative_age_clamped_to_zero():
    """now < last_processed_ts (clock skew edge) → age clamped to 0."""
    state = sd._v328_compute_heartbeat_state(last_processed_ts=1000.0, now=900.0, pending=200)
    assert state["age_sec"] == 0.0  # max(0.0, ...)
    assert state["worker_alive"] is True
    assert state["is_stuck"] is False


def test_threshold_boundary_exact_age_300_no_alert():
    """age=300 (exact boundary) → NOT stuck (strict > comparison)."""
    state = sd._v328_compute_heartbeat_state(last_processed_ts=1000.0, now=1300.0, pending=200)
    assert state["age_sec"] == 300.0
    assert state["worker_alive"] is False  # age < 300 → False, age == 300 → False (strict <)
    assert state["is_stuck"] is False  # strict > 300 required


def test_threshold_boundary_age_301_stuck_if_pending_high():
    """age=301 (just over) AND pending=101 → is_stuck=True."""
    state = sd._v328_compute_heartbeat_state(last_processed_ts=1000.0, now=1301.0, pending=101)
    assert state["age_sec"] == 301.0
    assert state["is_stuck"] is True


def test_pending_boundary_exact_100_no_alert():
    """age>300 + pending=100 (exact) → NOT stuck (strict > comparison)."""
    state = sd._v328_compute_heartbeat_state(last_processed_ts=1000.0, now=1400.0, pending=100)
    assert state["age_sec"] == 400.0
    assert state["is_stuck"] is False  # pending == 100, strict > needed


def test_returns_dict_with_3_keys():
    """Type guarantee: returns dict z exactly 3 keys."""
    state = sd._v328_compute_heartbeat_state(last_processed_ts=0.0, now=1.0, pending=0)
    assert isinstance(state, dict)
    assert set(state.keys()) == {"age_sec", "worker_alive", "is_stuck"}
    assert isinstance(state["age_sec"], float)
    assert isinstance(state["worker_alive"], bool)
    assert isinstance(state["is_stuck"], bool)


def test_thresholds_are_module_level_constants():
    """Verify module-level constants exist + reasonable defaults."""
    assert sd.V328_WORKER_STUCK_AGE_SEC == 300
    assert sd.V328_WORKER_STUCK_PENDING_THRESHOLD == 100
