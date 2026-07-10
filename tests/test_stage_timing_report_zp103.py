"""Reader/histogram dla Z-P1-03 decision_stage_timing.v1."""
from datetime import datetime, timezone

from dispatch_v2.tools import stage_timing_report as R


def _row(event_id, service, assess, queue_age, depth):
    return {
        "schema": "decision_stage_timing.v1",
        "scope": "decision",
        "event_ref": event_id,
        "tick_ref": "tick-a",
        "service_wall_ms": service,
        "event_e2e_ms": service + queue_age,
        "event_ack_ok": True,
        "queue": {
            "event_age_at_start_ms": queue_age,
            "event_clock_skew": False,
            "batch_wait_ms": depth,
            "panel_ingress_ms": None,
            "panel_ingress_missing_reason": "no_pre_fetch_anchor",
        },
        "timing": {
            "schema": "decision_timing.v1",
            "assess_wall_ms": assess,
            "fanout_wall_ms": assess - 1,
            "candidate_pre_recheck_work_sum_ms": 0.5,
            "candidate_work_sum_ms": assess * 2,
        },
        "service_unattributed_ms": 0.1,
    }


def _tick(phase, **queue):
    return {
        "schema": "decision_stage_timing.v1",
        "scope": "tick",
        "phase": phase,
        "tick_ref": "tick-a",
        "queue": queue,
        "outcomes": {"attempted": 3, "processed": 3, "failed": 0,
                     "skipped": 0, "decision_rows_buffered": 3},
    }


def test_report_percentiles_histogram_and_coverage():
    records = [
        _row("a", 10, 5, 100, 1),
        _row("b", 20, 10, 200, 2),
        _row("c", 30, 15, 300, 3),
        {"schema": "old", "event_id": "legacy"},
        _tick("open", depth_sample=3),
        _tick("complete", depth_sample=3, fleet_snapshot_ms=4,
              tick_processing_wall_ms=50),
    ]
    ledger = [
        {"event_ref": ref, "timing": {"schema": "decision_timing.v1"}}
        for ref in ("a", "b", "c", "missing")
    ]
    rep = R.build_report_from_records(records, ledger)
    assert rep["coverage"]["seen"] == 6
    assert rep["coverage"]["decision_rows"] == 3
    assert rep["coverage"]["valid"] == 3
    assert rep["coverage"]["invalid"] == 0
    assert rep["coverage"]["tick_open_rows"] == 1
    assert rep["coverage"]["tick_complete_rows"] == 1
    assert rep["coverage"]["incomplete_ticks"] == 0
    assert rep["coverage"]["ledger_join"]["matched_rows"] == 3
    assert rep["coverage"]["ledger_join"]["missing_sidecar_rows"] == 1
    assert rep["stages"]["service_wall_ms"]["p50"] == 20.0
    assert rep["stages"]["service_wall_ms"]["p95"] == 30.0
    assert rep["stages"]["service_wall_ms"]["max"] == 30.0
    assert rep["stages"]["timing.candidate_pre_recheck_work_sum_ms"]["n"] == 3
    assert sum(rep["stages"]["service_wall_ms"]["histogram"].values()) == 3
    assert rep["queue"]["depth_sample"]["n"] == 1
    assert rep["queue"]["depth_sample"]["max"] == 3.0
    assert rep["queue"]["fleet_snapshot_ms"]["n"] == 1
    assert rep["tick_outcomes"]["attempted"] == 3
    assert rep["quality"]["event_ack_ok"] == {
        "true": 3, "false": 0, "missing": 0}
    assert rep["quality"]["event_clock_skew"] == {
        "true": 0, "false": 3, "missing": 0}
    assert rep["quality"]["panel_ingress"] == {"no_pre_fetch_anchor": 3}


def test_report_handles_old_or_partial_rows_without_fabricating_zeroes():
    records = [
        {"schema": "decision_stage_timing.v1", "scope": "decision", "event_ref": "x"},
        {"schema": "decision_stage_timing.v1", "scope": "tick",
         "phase": "open", "tick_ref": "unclosed"},
        {"schema": "legacy", "service_wall_ms": 999},
    ]
    rep = R.build_report_from_records(records)
    assert rep["coverage"]["decision_rows"] == 1
    assert rep["coverage"]["valid"] == 0
    assert rep["coverage"]["invalid"] == 0
    assert rep["coverage"]["untrusted_decision_rows"] == 1
    assert rep["coverage"]["incomplete_ticks"] == 1
    assert rep["coverage"]["ledger_join"]["enabled"] is False
    assert rep["stages"]["service_wall_ms"]["n"] == 0


def test_nearest_rank_is_deterministic():
    vals = [50, 10, 30, 20, 40]
    assert R.percentiles(vals) == {
        "n": 5, "p50": 30.0, "p95": 50.0, "max": 50.0,
        "histogram": {"<=1": 0, "(1,5]": 0, "(5,10]": 1, "(10,25]": 1,
                      "(25,50]": 3, "(50,100]": 0, "(100,250]": 0,
                      "(250,500]": 0, "(500,1000]": 0,
                      "(1000,2000]": 0, "(2000,5000]": 0, ">5000": 0},
    }


def test_osrm_sources_and_duplicate_orphan_refs_are_explicit():
    row = _row("dup", 10, 5, 1, 1)
    row["timing"]["osrm_tags"] = {
        "source": {"upstream": 2, "cache": 1, "fallback": 1}}
    rep = R.build_report_from_records(
        [row, dict(row), _tick("open"), _tick("complete")],
        [{"event_ref": "dup", "timing": {"schema": "decision_timing.v1"}}],
    )
    assert rep["osrm_source_counts"] == {
        "cache": 2, "fallback": 2, "upstream": 4}
    join = rep["coverage"]["ledger_join"]
    assert join["duplicate_sidecar_refs"] == 1
    assert join["orphan_sidecar_rows"] == 1


def test_ledger_anchored_window_uses_grace_without_boundary_false_loss():
    since = datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc)
    until = datetime(2026, 7, 10, 11, 0, tzinfo=timezone.utc)
    target = _row("target", 12, 6, 10, 1)
    target.update(ts="2026-07-10T11:00:01Z", tick_ref="tick-target")
    context = _row("context", 99, 99, 99, 9)
    context.update(ts="2026-07-10T10:00:01Z", tick_ref="tick-context")
    complete = _tick("complete", depth_sample=1)
    complete.update(ts="2026-07-10T11:00:02Z", tick_ref="tick-target")
    records = [target, context, complete]
    ledger = [
        {"ts": "2026-07-10T10:59:59Z", "event_ref": "target",
         "timing": {"schema": "decision_timing.v1"}},
        {"ts": "2026-07-10T09:59:59Z", "event_ref": "context",
         "timing": {"schema": "decision_timing.v1"}},
    ]
    rep = R.build_report_from_records(
        records, ledger, since=since, until=until, grace_seconds=5)
    assert rep["coverage"]["ledger_join"]["matched_rows"] == 1
    assert rep["coverage"]["ledger_join"]["missing_sidecar_rows"] == 0
    assert rep["coverage"]["ledger_join"]["orphan_sidecar_rows"] == 0
    assert rep["coverage"]["valid"] == 1
    assert rep["coverage"]["tick_complete_rows"] == 1
    assert rep["coverage"]["complete_without_open"] == 1
    assert rep["stages"]["service_wall_ms"]["max"] == 12.0
    assert rep["window"]["semantics"] == "[since, until)"


def test_partial_tick_never_enters_percentiles_and_invariants_are_loud():
    row = _row("partial", 500, 400, 1, 1)
    row["tick_ref"] = "partial-tick"
    open_row = _tick("open")
    open_row["tick_ref"] = "partial-tick"
    rep = R.build_report_from_records(
        [row, open_row],
        [{"event_ref": "partial", "timing": {"schema": "decision_timing.v1"}}],
    )
    assert rep["coverage"]["untrusted_decision_rows"] == 1
    assert rep["coverage"]["incomplete_ticks"] == 1
    assert rep["stages"]["service_wall_ms"]["n"] == 0

    broken_complete = _tick("complete")
    broken_complete["outcomes"] = {
        "attempted": 3, "processed": 2, "failed": 0, "skipped": 0,
        "decision_rows_buffered": 1,
    }
    broken = R.build_report_from_records([broken_complete])
    assert broken["coverage"]["tick_outcome_invariant_violations"] == 2


def test_clock_skew_ack_failure_and_depth_fallback_are_quality_not_latency():
    row = _row("quality", 10, 5, -5000, 1)
    row["event_ack_ok"] = False
    row["queue"]["event_clock_skew"] = True
    complete = _tick(
        "complete", depth_sample=4, depth_sample_source="batch_fallback",
        depth_sample_atomic=False, oldest_event_clock_skew=True)
    complete["outcomes"]["decision_rows_buffered"] = 1
    complete["outcomes"]["processed"] = 1
    complete["outcomes"]["attempted"] = 1
    rep = R.build_report_from_records([row, _tick("open"), complete])
    assert rep["queue"]["event_age_at_start_ms"]["n"] == 0
    assert rep["quality"]["event_ack_ok"] == {
        "true": 0, "false": 1, "missing": 0}
    assert rep["quality"]["event_clock_skew"]["true"] == 1
    assert rep["quality"]["depth_sample_source"] == {"batch_fallback": 1}
    assert rep["quality"]["depth_sample_atomic"]["false"] == 1
    assert rep["quality"]["oldest_event_clock_skew"]["true"] == 1
