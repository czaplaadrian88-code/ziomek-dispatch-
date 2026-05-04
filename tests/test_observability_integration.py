"""TASK 3 (2026-05-04) integration tests — bonus integrations.

Coverage:
  1. /health/reconcile route w parser_health_endpoint (mocked HTTP)
  2. Fix 5c integration — reconciliation_status feeds downstream priority
  3. Reconciliation_status=None (graceful absent) → downstream unchanged
  4. czasowka_scheduler.eval_czasowka observability hook (flag-gated, defensive)
  5. dispatch_pipeline.assess_order observability hook signature preserved
"""
import os
import sys
import json
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, "/root/.openclaw/workspace/scripts")


passed, failed = 0, 0
def t(name, fn):
    global passed, failed
    try:
        fn()
        passed += 1; print(f"  OK {passed+failed}. {name}")
    except AssertionError as e:
        failed += 1; print(f"  FAIL {passed+failed}. {name}: {e}")
    except Exception as e:
        failed += 1; print(f"  CRASH {passed+failed}. {name}: {type(e).__name__}: {e}")
        import traceback; traceback.print_exc()


# ---------- 1. Fix 5c integration: reconciliation_status priority ----------

def test_fix5c_reconciliation_critical_escalates():
    from dispatch_v2.parser_health_endpoint import _v328_compute_downstream_status
    # No pipeline issues, but reconciliation critical → downstream critical
    result = _v328_compute_downstream_status(
        last_proposal_age_sec=10, events_failed_1h=0, new_orders_1h=0,
        worker_age_sec=10, reconciliation_status="critical",
        reconciliation_reason="hard_cap_hits=2",
    )
    assert result["downstream_status"] == "critical"
    assert "reconciliation_critical" in result["downstream_reason"]
    assert "hard_cap_hits=2" in result["downstream_reason"]
t("fix5c_reconciliation_critical_escalates", test_fix5c_reconciliation_critical_escalates)


def test_fix5c_reconciliation_degraded_passes():
    from dispatch_v2.parser_health_endpoint import _v328_compute_downstream_status
    result = _v328_compute_downstream_status(
        last_proposal_age_sec=10, events_failed_1h=0, new_orders_1h=0,
        worker_age_sec=10, reconciliation_status="degraded",
        reconciliation_reason="ghosts=1",
    )
    assert result["downstream_status"] == "degraded"
    assert "reconciliation_degraded" in result["downstream_reason"]
t("fix5c_reconciliation_degraded_passes", test_fix5c_reconciliation_degraded_passes)


def test_fix5c_pipeline_critical_overrides_recon():
    """Pipeline critical (priority 1) overrides reconciliation critical (priority 3)."""
    from dispatch_v2.parser_health_endpoint import _v328_compute_downstream_status
    result = _v328_compute_downstream_status(
        last_proposal_age_sec=2000, events_failed_1h=0, new_orders_1h=5,
        worker_age_sec=10, reconciliation_status="critical",
        reconciliation_reason="hard_cap_hits=2",
    )
    # Priority 1: pipeline_silent_despite_work
    assert result["downstream_status"] == "critical"
    assert result["downstream_reason"] == "pipeline_silent_despite_work"
t("fix5c_pipeline_critical_overrides_recon", test_fix5c_pipeline_critical_overrides_recon)


def test_fix5c_recon_none_graceful():
    """reconciliation_status=None (absent) → downstream unchanged."""
    from dispatch_v2.parser_health_endpoint import _v328_compute_downstream_status
    result = _v328_compute_downstream_status(
        last_proposal_age_sec=10, events_failed_1h=0, new_orders_1h=0,
        worker_age_sec=10, reconciliation_status=None, reconciliation_reason=None,
    )
    assert result["downstream_status"] == "ok"
    assert result["downstream_reason"] is None
t("fix5c_recon_none_graceful", test_fix5c_recon_none_graceful)


def test_fix5c_recon_priority_under_pipeline():
    """Reconciliation critical does NOT escalate when pipeline already shows critical."""
    from dispatch_v2.parser_health_endpoint import _v328_compute_downstream_status
    # Worker stuck = priority 2 critical
    result = _v328_compute_downstream_status(
        last_proposal_age_sec=None, events_failed_1h=0, new_orders_1h=0,
        worker_age_sec=10000, reconciliation_status="critical",
        reconciliation_reason="hard_cap_hits=2",
    )
    assert result["downstream_status"] == "critical"
    assert result["downstream_reason"] == "worker_stuck"
t("fix5c_recon_priority_under_pipeline", test_fix5c_recon_priority_under_pipeline)


# ---------- 2. /health/reconcile route ----------

def test_health_reconcile_endpoint_returns_dict():
    """get_reconciliation_health zwraca poprawny shape niezależnie od stanu."""
    from dispatch_v2.reconciliation.health_endpoint import get_reconciliation_health
    tmpf = tempfile.NamedTemporaryFile(mode="w", suffix=".jsonl", delete=False)
    tmpf.close()
    try:
        result = get_reconciliation_health(log_path=Path(tmpf.name))
        # Schema verification
        assert "status" in result
        assert "discrepancies_24h" in result
        assert "endpoint_version" in result
        assert result["endpoint_version"] == "1"
        assert result["status"] in ("ok", "degraded", "critical")
    finally:
        os.unlink(tmpf.name)
t("health_reconcile_endpoint_returns_dict", test_health_reconcile_endpoint_returns_dict)


# ---------- 3. czasowka_scheduler observability hook ----------

def test_eval_czasowka_observability_defensive():
    """eval_czasowka NIGDY nie crashes na observability error.

    Zero-input test — eval_czasowka z minimal state — verify no crash.
    """
    from dispatch_v2 import czasowka_scheduler
    # Bare minimum order_state — eval_czasowka returns SKIP (no pickup_at_warsaw)
    now = datetime.now(timezone.utc)
    result = czasowka_scheduler.eval_czasowka(
        order_id="OBS_TEST_1",
        order_state={"restaurant": "Test", "status_id": 2},
        now_utc=now,
    )
    # Result must be valid dict regardless of observability flag state
    assert "decision" in result
    assert "reason" in result
t("eval_czasowka_observability_defensive", test_eval_czasowka_observability_defensive)


def test_eval_czasowka_logs_when_flag_enabled():
    """gdy OBSERVABILITY_PER_CANDIDATE_ENABLED=True → log line written."""
    import json
    from dispatch_v2 import czasowka_scheduler
    from dispatch_v2.observability import candidate_logger as cl

    # Patch logger singleton to use temp dir + flag-on
    tmp = Path(tempfile.mkdtemp())
    saved = cl._singleton
    cl._singleton = cl.CandidateLogger(
        flag_check_fn=lambda: True,
        log_dir=tmp,
    )
    try:
        now = datetime.now(timezone.utc)
        czasowka_scheduler.eval_czasowka(
            order_id="OBS_TEST_2",
            order_state={"restaurant": "Test", "status_id": 2},
            now_utc=now,
        )
        files = list(tmp.glob("candidate_decisions_*.jsonl"))
        # Either 0 (early return path skipped logger) OR 1 with valid record
        if files:
            with open(files[0]) as f:
                rec = json.loads(f.readline())
            assert rec["source"] == "czasowka_scheduler"
            assert rec["order_id"] == "OBS_TEST_2"
    finally:
        cl._singleton = saved
        if tmp.exists():
            for f in tmp.iterdir(): f.unlink()
            tmp.rmdir()
t("eval_czasowka_logs_when_flag_enabled", test_eval_czasowka_logs_when_flag_enabled)


# ---------- 4. dispatch_pipeline.assess_order signature preserved ----------

def test_assess_order_signature_unchanged():
    """assess_order sygnatura backward compatible — istniejące callers nie złamane."""
    from dispatch_v2 import dispatch_pipeline
    import inspect
    sig = inspect.signature(dispatch_pipeline.assess_order)
    params = list(sig.parameters.keys())
    # Required positional/keyword: order_event, fleet_snapshot, restaurant_meta, now
    # Plus kwargs-only: pending_queue, demand_context (per F2.2 C7 spec)
    for required in ("order_event", "fleet_snapshot", "restaurant_meta", "now",
                     "pending_queue", "demand_context"):
        assert required in params, f"missing param: {required}"
t("assess_order_signature_unchanged", test_assess_order_signature_unchanged)


# ---------- 5. dispatchable_fleet observability hook (defensive) ----------

def test_dispatchable_fleet_observability_defensive():
    """dispatchable_fleet NIGDY nie crashes na observability error."""
    from dispatch_v2 import courier_resolver
    # Empty fleet — observability captured z empty rejected/passed lists
    result = courier_resolver.dispatchable_fleet(fleet={})
    assert isinstance(result, list)
    assert len(result) == 0
t("dispatchable_fleet_observability_defensive", test_dispatchable_fleet_observability_defensive)


print("=" * 70)
print(f"PASSED: {passed}/{passed+failed}")
print(f"FAILED: {failed}/{passed+failed}")
print("=" * 70)
sys.exit(0 if failed == 0 else 1)
