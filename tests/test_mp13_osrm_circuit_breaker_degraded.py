"""MP-#13 OSRM circuit breaker degraded mode 3-warstwowo (2026-05-08).

Per master plan TOP-15 #13 + audit OBSERVABILITY_SELF_HEALING C.2.
Layer separation:
  L1: cache_age_s, is_degraded, degraded_since_ts state tracking
  L2: Telegram alert entry/exit transitions z dedup (no flapping spam)
  L3: PipelineResult.degraded_osrm propagation + shadow_dispatcher serialization

Coverage:
  L1 state machine (8 tests):
    - is_degraded False initially
    - degraded_since None initially
    - cache_age_s None before any success
    - record_success sets last_success + cache_age_s monotonic
    - record_failure × 3 → circuit OPEN + degraded_since set
    - record_success post-degradation → exit + degraded_since cleared
    - subsequent failure burst re-enters degraded period
    - cache_age_s grows over time without success

  L2 alert transitions (4 tests):
    - entry alert fired ONCE on first transition to degraded
    - dedup: subsequent failures during continuous degradation NIE fire entry alert
    - recovery alert fired ONCE on first success post-degradation
    - flapping (degraded → recovery → degraded → recovery) re-arms alerts properly

  L3 propagation (3 tests):
    - PipelineResult has degraded_osrm field default False
    - assess_order populates degraded_osrm + cache_age_s + degraded_since_ts snapshot
    - shadow_dispatcher._serialize_result emits decision_meta dict with all 3 fields
"""
from __future__ import annotations

import time
from unittest.mock import patch, MagicMock

import pytest

from dispatch_v2 import osrm_client as oc


@pytest.fixture(autouse=True)
def _reset_osrm_state():
    """Reset osrm_client module state before each test (state is module-level)."""
    with oc._module_lock:
        oc._osrm_failures = 0
        oc._osrm_circuit_open_until = 0.0
        oc._osrm_last_success_ts = None
        oc._osrm_degraded_since = None
        oc._osrm_degraded_alert_sent = False
        oc._osrm_recovery_alert_sent = False
    yield
    with oc._module_lock:
        oc._osrm_failures = 0
        oc._osrm_circuit_open_until = 0.0
        oc._osrm_last_success_ts = None
        oc._osrm_degraded_since = None
        oc._osrm_degraded_alert_sent = False
        oc._osrm_recovery_alert_sent = False


# ---------------------------------------------------------------------------
# L1 — state machine
# ---------------------------------------------------------------------------


def test_l1_initial_state_healthy():
    assert oc.is_degraded() is False
    assert oc.degraded_since_ts() is None
    assert oc.cache_age_s() is None


def test_l1_record_success_sets_last_success_ts():
    t0 = time.time()
    oc._osrm_record_success()
    age = oc.cache_age_s()
    assert age is not None
    assert age >= 0
    assert age < 0.1  # just now


def test_l1_record_3_failures_opens_circuit_and_sets_degraded():
    for _ in range(3):
        oc._osrm_record_failure()
    assert oc.is_degraded() is True
    since = oc.degraded_since_ts()
    assert since is not None
    assert (time.time() - since) < 0.1


def test_l1_record_success_post_degradation_clears():
    for _ in range(3):
        oc._osrm_record_failure()
    assert oc.is_degraded() is True

    oc._osrm_record_success()
    assert oc.is_degraded() is False
    assert oc.degraded_since_ts() is None


def test_l1_subsequent_failure_burst_reenters_degraded():
    # First degraded period
    for _ in range(3):
        oc._osrm_record_failure()
    first_since = oc.degraded_since_ts()
    assert first_since is not None

    # Recovery
    oc._osrm_record_success()
    assert oc.is_degraded() is False

    # Wait epsilon — ensure new degraded_since_ts > first
    time.sleep(0.01)

    # Second degraded period
    for _ in range(3):
        oc._osrm_record_failure()
    second_since = oc.degraded_since_ts()
    assert second_since is not None
    assert second_since > first_since, "second degraded period should have later entry timestamp"


def test_l1_cache_age_grows_without_success():
    oc._osrm_record_success()
    age0 = oc.cache_age_s()
    time.sleep(0.05)
    age1 = oc.cache_age_s()
    assert age1 > age0


def test_l1_2_failures_below_threshold_no_degraded():
    """Below CIRCUIT_BREAKER_THRESHOLD=3, should NOT enter degraded."""
    oc._osrm_record_failure()
    oc._osrm_record_failure()
    assert oc.is_degraded() is False


def test_l1_record_success_resets_failure_counter():
    """Stress: 2 failures, success, then need 3 MORE failures to re-degrade (NIE 1)."""
    oc._osrm_record_failure()
    oc._osrm_record_failure()
    oc._osrm_record_success()
    oc._osrm_record_failure()
    assert oc.is_degraded() is False, "1 failure post-recovery should not re-degrade"
    oc._osrm_record_failure()
    assert oc.is_degraded() is False, "2 failures post-recovery should not re-degrade"
    oc._osrm_record_failure()
    assert oc.is_degraded() is True, "3 failures post-recovery should re-degrade"


# ---------------------------------------------------------------------------
# L2 — alert transitions
# ---------------------------------------------------------------------------


def test_l2_entry_alert_fired_once_on_transition_to_degraded():
    sent = []
    with patch.object(oc, "_mp13_send_alert_safe", side_effect=lambda msg: sent.append(msg)):
        for _ in range(3):
            oc._osrm_record_failure()
    assert len(sent) == 1, f"expected 1 alert (entry), got {len(sent)}: {sent}"
    assert "degraded" in sent[0].lower()
    assert "circuit OPEN" in sent[0]


def test_l2_dedup_subsequent_failures_during_continuous_degradation():
    """While degraded, additional failures should NOT spam entry alerts."""
    sent = []
    with patch.object(oc, "_mp13_send_alert_safe", side_effect=lambda msg: sent.append(msg)):
        for _ in range(3):
            oc._osrm_record_failure()
        # Now degraded. Pump more failures — re-opens circuit but degraded_since
        # is preserved. Alert MUST stay deduped.
        for _ in range(10):
            oc._osrm_record_failure()
    assert len(sent) == 1, f"expected 1 alert (dedup), got {len(sent)}: {sent}"


def test_l2_recovery_alert_fired_once():
    sent = []
    with patch.object(oc, "_mp13_send_alert_safe", side_effect=lambda msg: sent.append(msg)):
        for _ in range(3):
            oc._osrm_record_failure()
        # Entry alert already counted; reset list to see only recovery
        sent.clear()
        oc._osrm_record_success()
    assert len(sent) == 1, f"expected 1 recovery alert, got {len(sent)}: {sent}"
    assert "recovery" in sent[0].lower()
    assert "healthy" in sent[0].lower()


def test_l2_flapping_rearms_alerts():
    """degraded → recovery → degraded → recovery → both alerts fire each cycle."""
    sent = []
    with patch.object(oc, "_mp13_send_alert_safe", side_effect=lambda msg: sent.append(msg)):
        # Cycle 1
        for _ in range(3):
            oc._osrm_record_failure()
        oc._osrm_record_success()
        # Cycle 2
        for _ in range(3):
            oc._osrm_record_failure()
        oc._osrm_record_success()
    # Expected: 4 alerts (entry1, recovery1, entry2, recovery2)
    assert len(sent) == 4, f"expected 4 alerts (2 cycles), got {len(sent)}: {sent}"
    assert "degraded" in sent[0].lower()
    assert "recovery" in sent[1].lower()
    assert "degraded" in sent[2].lower()
    assert "recovery" in sent[3].lower()


# ---------------------------------------------------------------------------
# L3 — caller propagation
# ---------------------------------------------------------------------------


def test_l3_pipeline_result_has_degraded_osrm_field():
    from dispatch_v2.dispatch_pipeline import PipelineResult
    fields = PipelineResult.__dataclass_fields__
    assert "degraded_osrm" in fields
    assert "osrm_cache_age_s" in fields
    assert "osrm_degraded_since_ts" in fields
    # Default values
    assert fields["degraded_osrm"].default is False
    assert fields["osrm_cache_age_s"].default is None


def test_l3_assess_order_snapshots_degraded_state():
    """assess_order populates result.degraded_osrm via osrm_client.is_degraded()."""
    from dispatch_v2 import dispatch_pipeline as dp

    # Force degraded state
    for _ in range(3):
        oc._osrm_record_failure()
    assert oc.is_degraded() is True

    # Mock _assess_order_impl to return minimal PipelineResult
    minimal_result = dp.PipelineResult(
        order_id="test_oid",
        verdict="SKIP",
        reason="test",
        best=None,
        candidates=[],
        pickup_ready_at=None,
        restaurant=None,
    )
    with patch.object(dp, "_assess_order_impl", return_value=minimal_result):
        out = dp.assess_order({"order_id": "test_oid"}, {}, None)

    assert out.degraded_osrm is True, "expected degraded_osrm=True snapshot"
    assert out.osrm_degraded_since_ts is not None
    # cache_age_s might be None (no success yet) — that's correct


def test_l3_assess_order_healthy_state_propagates_false():
    """Healthy OSRM → degraded_osrm=False propagated."""
    from dispatch_v2 import dispatch_pipeline as dp

    oc._osrm_record_success()
    assert oc.is_degraded() is False

    minimal_result = dp.PipelineResult(
        order_id="ok_oid",
        verdict="PROPOSE",
        reason="test",
        best=None,
        candidates=[],
        pickup_ready_at=None,
        restaurant=None,
    )
    with patch.object(dp, "_assess_order_impl", return_value=minimal_result):
        out = dp.assess_order({"order_id": "ok_oid"}, {}, None)

    assert out.degraded_osrm is False
    assert out.osrm_degraded_since_ts is None
    assert out.osrm_cache_age_s is not None  # last_success was just set


def test_l3_shadow_dispatcher_serializes_decision_meta():
    """shadow_dispatcher._serialize_result emits decision_meta z 3 fields."""
    from dispatch_v2 import dispatch_pipeline as dp
    from dispatch_v2 import shadow_dispatcher as sd

    result = dp.PipelineResult(
        order_id="ser_oid",
        verdict="PROPOSE",
        reason="test",
        best=None,
        candidates=[],
        pickup_ready_at=None,
        restaurant=None,
        degraded_osrm=True,
        osrm_cache_age_s=42.0,
        osrm_degraded_since_ts=12345.6,
    )
    out = sd._serialize_result(result, event_id="e1", latency_ms=10.0)

    assert "decision_meta" in out
    meta = out["decision_meta"]
    assert meta["degraded_osrm"] is True
    assert meta["osrm_cache_age_s"] == 42.0
    assert meta["osrm_degraded_since_ts"] == 12345.6


def test_l3_shadow_dispatcher_decision_meta_defaults_when_healthy():
    """Healthy state → decision_meta degraded_osrm=False, ts None."""
    from dispatch_v2 import dispatch_pipeline as dp
    from dispatch_v2 import shadow_dispatcher as sd

    result = dp.PipelineResult(
        order_id="h_oid",
        verdict="PROPOSE",
        reason="test",
        best=None,
        candidates=[],
        pickup_ready_at=None,
        restaurant=None,
    )
    out = sd._serialize_result(result, event_id="e2", latency_ms=5.0)

    meta = out["decision_meta"]
    assert meta["degraded_osrm"] is False
    assert meta["osrm_cache_age_s"] is None
    assert meta["osrm_degraded_since_ts"] is None


# ---------------------------------------------------------------------------
# L2 defense-in-depth — telegram unreachable musi NIE crashnąć
# ---------------------------------------------------------------------------


def test_l2_alert_telegram_unreachable_does_not_raise():
    """telegram_utils.send_admin_alert raise → osrm_client._mp13_send_alert_safe NIE propagates."""
    with patch("dispatch_v2.telegram_utils.send_admin_alert", side_effect=ConnectionError("network down")):
        # Should not raise
        oc._mp13_send_alert_safe("test message")


def test_l2_alert_records_failure_does_not_crash_on_telegram_fail():
    """Even if telegram unreachable during transition, record_failure must complete."""
    with patch("dispatch_v2.telegram_utils.send_admin_alert", side_effect=RuntimeError("synthetic")):
        # Trigger entry alert path
        for _ in range(3):
            oc._osrm_record_failure()
    # State must still transition
    assert oc.is_degraded() is True
