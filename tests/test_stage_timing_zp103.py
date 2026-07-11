"""Z-P1-03 Faza A: kontrakt stage timing (obserwacja, zero backpressure).

Oracle rozdziela addytywny wall-time od nakladajacej sie pracy watkow.
Wszystkie zapisy ida do tmp; awaria sidecara nie moze zmienic decyzji.
"""
from __future__ import annotations

import json
import stat
import types
from datetime import datetime, timezone

import pytest

from dispatch_v2.observability import stage_timing as ST
from dispatch_v2 import dispatch_pipeline as DP
from dispatch_v2 import shadow_dispatcher as SD
from dispatch_v2 import common as C
from dispatch_v2 import route_simulator_v2 as RS


class FakeClock:
    def __init__(self):
        self.value = 0

    def __call__(self):
        return self.value

    def advance_ms(self, value):
        self.value += int(value * 1_000_000)


def _candidate(cid="1"):
    return DP.Candidate(
        courier_id=cid,
        name=f"K{cid}",
        score=1.0,
        feasibility_verdict="MAYBE",
        feasibility_reason="ok",
        plan=None,
        metrics={},
    )


def _result(best, candidates):
    return DP.PipelineResult(
        order_id="o1",
        verdict="PROPOSE",
        reason="ok",
        best=best,
        candidates=candidates,
        pickup_ready_at=None,
        restaurant="R",
    )


def test_wall_reconciliation_uses_unrounded_clock():
    clock = FakeClock()
    trace = ST.DecisionTrace(clock_ns=clock)
    with ST.bind(trace):
        with ST.span("prepare_wall_ms"):
            clock.advance_ms(10)
        with ST.span("pre_recheck_wall_ms"):
            clock.advance_ms(5)
        with ST.span("fanout_setup_wall_ms"):
            clock.advance_ms(2)
        with ST.span("fanout_wall_ms"):
            clock.advance_ms(20)
        with ST.span("post_pool_wall_ms"):
            clock.advance_ms(3)
        with ST.span("selection_wall_ms"):
            clock.advance_ms(4)
        trace.record_ms("impl_wall_ms", 44)
        trace.record_ms("effects_flush_wall_ms", 1)
        trace.record_ms("post_hooks_wall_ms", 2)
        trace.record_ms("assess_wall_ms", 47)

    out = trace.snapshot()
    assert out["pipeline_parts_sum_ms"] == 44.0
    assert out["pipeline_unattributed_ms"] == 0.0
    assert out["assess_parts_sum_ms"] == 47.0
    assert out["assess_unattributed_ms"] == 0.0


def test_parallel_work_sum_is_not_added_to_fanout_wall():
    clock = FakeClock()
    trace = ST.DecisionTrace(clock_ns=clock)
    with ST.candidate_scope(trace, "1"):
        clock.advance_ms(10)
        ST.record_work("pre_recheck", 2)
        ST.record_work("osrm", 4, source="upstream")
        ST.record_work("solver", 3)
    with ST.candidate_scope(trace, "2"):
        clock.advance_ms(20)
        ST.record_work("osrm", 7, source="cache")
        ST.record_work("solver", 5)
    trace.record_ms("fanout_wall_ms", 20)

    out = trace.snapshot()
    assert out["fanout_wall_ms"] == 20.0
    assert out["candidate_work_sum_ms"] == 30.0
    assert out["candidate_work_max_ms"] == 20.0
    assert out["osrm_calls"] == 2
    assert out["candidate_pre_recheck_calls"] == 1
    assert out["candidate_pre_recheck_work_sum_ms"] == 2.0
    assert out["candidate_pre_recheck_work_max_ms"] == 2.0
    assert out["osrm_work_sum_ms"] == 11.0
    assert out["solver_work_sum_ms"] == 8.0
    assert out["fanout_parallelism_factor"] == 1.5


def test_osrm_cache_details_reach_top_level_without_double_subtract():
    clock = FakeClock()
    trace = ST.DecisionTrace(clock_ns=clock)
    with ST.candidate_scope(trace, "1"):
        clock.advance_ms(10)
        ST.record_work("osrm", 10, source="cache")
        ST.record_work("osrm_cache_lock_wait", 2, cache="route")
        ST.record_work("osrm_cache_eviction", 1, cache="route", evicted=500)

    candidate = trace.candidate_snapshot("1")
    out = trace.snapshot()
    assert candidate["exclusive_ms"] == 0.0  # 10 wall - 10 OSRM, nie -13
    assert out["osrm_cache_lock_wait_calls"] == 1
    assert out["osrm_cache_lock_wait_work_sum_ms"] == 2.0
    assert out["osrm_cache_lock_wait_work_max_ms"] == 2.0
    assert out["osrm_cache_lock_wait_tags"] == {"cache": {"route": 1}}
    assert out["osrm_cache_eviction_calls"] == 1
    assert out["osrm_cache_eviction_work_sum_ms"] == 1.0
    assert out["osrm_cache_eviction_tags"]["evicted"] == {"500": 1}


def test_candidate_contexts_do_not_mix():
    clock = FakeClock()
    a = ST.DecisionTrace(clock_ns=clock)
    b = ST.DecisionTrace(clock_ns=clock)
    with ST.candidate_scope(a, "A"):
        clock.advance_ms(3)
        ST.record_work("osrm", 1)
        with ST.candidate_scope(b, "B"):
            clock.advance_ms(5)
            ST.record_work("solver", 2)
        ST.record_work("solver", 1)

    assert a.candidate_snapshot("A")["osrm"]["calls"] == 1
    assert a.candidate_snapshot("A")["solver"]["calls"] == 1
    assert b.candidate_snapshot("B")["osrm"]["calls"] == 0
    assert b.candidate_snapshot("B")["solver"]["calls"] == 1


def test_candidate_timing_reaches_both_serializers():
    clock = FakeClock()
    trace = ST.DecisionTrace(clock_ns=clock)
    with ST.candidate_scope(trace, "1"):
        clock.advance_ms(10)
        ST.record_work("osrm", 4, source="upstream")
    with ST.candidate_scope(trace, "2"):
        clock.advance_ms(6)
        ST.record_work("solver", 2)

    best, alt = _candidate("1"), _candidate("2")
    result = _result(best, [best, alt])
    trace.attach(result)
    record = SD._serialize_result(result, "evt", 123.4)

    assert record["timing"]["schema"] == ST.SCHEMA
    assert record["best"]["candidate_timing"]["osrm"]["calls"] == 1
    assert record["alternatives"][0]["candidate_timing"]["solver"]["calls"] == 1


def test_assess_wrapper_attaches_timing_after_decision(monkeypatch):
    result = _result(None, [])
    result.verdict = "SKIP"
    seen = {}

    def fake_impl(*_args, **kwargs):
        seen["trace"] = kwargs.get("_timing_trace")
        return result

    monkeypatch.setattr(DP, "_assess_order_impl", fake_impl)
    monkeypatch.setattr(
        DP.C, "flag",
        lambda name, default=False: (
            True if name == "ENABLE_STAGE_TIMING_OBSERVATION" else default),
    )
    monkeypatch.setattr(DP._EB, "begin", lambda: False)
    monkeypatch.setattr(DP, "_eta_fabrication_check", lambda *_a, **_kw: None)
    monkeypatch.setattr(DP, "_attach_final_rule_verdict", lambda *_a, **_kw: None)
    out = DP.assess_order({"order_id": "o1"}, {}, now=datetime.now(timezone.utc))
    assert out is result
    assert isinstance(seen["trace"], ST.DecisionTrace)
    assert out.stage_timing["schema"] == ST.SCHEMA
    assert "impl_wall_ms" in out.stage_timing


def test_assess_timing_attach_failure_never_changes_ready_decision(monkeypatch):
    result = _result(None, [])
    result.verdict = "SKIP"
    monkeypatch.setattr(DP, "_assess_order_impl", lambda *_a, **_kw: result)
    monkeypatch.setattr(
        DP.C, "flag",
        lambda name, default=False: (
            True if name == "ENABLE_STAGE_TIMING_OBSERVATION" else default),
    )
    monkeypatch.setattr(DP._EB, "begin", lambda: False)
    monkeypatch.setattr(DP, "_eta_fabrication_check", lambda *_a, **_kw: None)
    monkeypatch.setattr(DP, "_attach_final_rule_verdict", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        ST.DecisionTrace, "attach",
        lambda *_a, **_kw: (_ for _ in ()).throw(RuntimeError("observer failed")),
    )
    out = DP.assess_order(
        {"order_id": "o1"}, {}, now=datetime.now(timezone.utc))
    assert out is result
    assert out.verdict == "SKIP"
    assert out.stage_timing is None


def test_assess_kill_switch_off_has_legacy_shape_and_no_collector(monkeypatch):
    result = _result(None, [])
    result.verdict = "SKIP"
    seen = {}

    def fake_impl(*_args, **kwargs):
        seen["trace"] = kwargs.get("_timing_trace")
        return result

    class ForbiddenTrace:
        def __init__(self, *_args, **_kwargs):
            raise AssertionError("OFF must not construct DecisionTrace")

    monkeypatch.setattr(DP, "_assess_order_impl", fake_impl)
    monkeypatch.setattr(DP.C, "flag", lambda *_a, **_kw: False)
    monkeypatch.setattr(DP._EB, "begin", lambda: False)
    monkeypatch.setattr(DP, "_eta_fabrication_check", lambda *_a, **_kw: None)
    monkeypatch.setattr(DP, "_attach_final_rule_verdict", lambda *_a, **_kw: None)
    monkeypatch.setattr(ST, "DecisionTrace", ForbiddenTrace)

    out = DP.assess_order(
        {"order_id": "o1"}, {}, now=datetime.now(timezone.utc))

    assert out is result and out.verdict == "SKIP"
    assert seen["trace"] is None
    assert out.stage_timing is None
    assert "timing" not in SD._serialize_result(out, "evt", 1.0)


def test_assess_kill_switch_hot_on_to_off_is_snapshotted_per_call(monkeypatch):
    enabled = iter((True, False))
    flag_reads = []
    traces = []

    def fake_flag(name, default=False):
        if name == "ENABLE_STAGE_TIMING_OBSERVATION":
            flag_reads.append(name)
            return next(enabled)
        return default

    def fake_impl(*_args, **kwargs):
        traces.append(kwargs.get("_timing_trace"))
        result = _result(None, [])
        result.verdict = "SKIP"
        return result

    monkeypatch.setattr(DP.C, "flag", fake_flag)
    monkeypatch.setattr(DP, "_assess_order_impl", fake_impl)
    monkeypatch.setattr(DP._EB, "begin", lambda: False)
    monkeypatch.setattr(DP, "_eta_fabrication_check", lambda *_a, **_kw: None)
    monkeypatch.setattr(DP, "_attach_final_rule_verdict", lambda *_a, **_kw: None)

    on = DP.assess_order({"order_id": "on"}, {})
    off = DP.assess_order({"order_id": "off"}, {})

    assert len(flag_reads) == 2
    assert isinstance(traces[0], ST.DecisionTrace) and traces[1] is None
    assert on.stage_timing["schema"] == ST.SCHEMA
    assert off.stage_timing is None


def test_observation_kill_switch_is_registered_fingerprinted_and_fail_closed():
    name = ST.OBSERVATION_FLAG
    assert C.ENABLE_STAGE_TIMING_OBSERVATION is False
    assert name in C.TEST_ISOLATED_INFRA_FLAGS
    assert name in C._FINGERPRINT_EXTRA_FLAGS
    assert f"{name}=" in C.flag_fingerprint()

    def broken_reader(*_args, **_kwargs):
        raise OSError("flags unavailable")

    assert ST.observation_enabled(broken_reader, True) is False
    with ST.observation_scope(True):
        assert ST.observation_enabled(broken_reader, False) is True
    with ST.observation_scope(False):
        assert ST.observation_enabled(lambda *_a, **_kw: True, True) is False


def test_real_route_simulator_records_solver_work(monkeypatch):
    def table(origins, destinations):
        return [
            [{"duration_s": 300.0, "duration_min": 5.0,
              "distance_m": 2000.0, "distance_km": 2.0,
              "osrm_fallback": False}
             for _ in destinations]
            for _ in origins
        ]

    monkeypatch.setattr(RS.osrm_client, "table", table)
    monkeypatch.setattr(C, "ENABLE_V326_OR_TOOLS_TSP", True)
    now = datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc)
    bag = [RS.OrderSim(
        order_id="B", pickup_coords=(53.13, 23.16),
        delivery_coords=(53.12, 23.17), status="picked_up",
        picked_up_at=now,
    )]
    new = RS.OrderSim(
        order_id="N", pickup_coords=(53.135, 23.165),
        delivery_coords=(53.125, 23.175), status="assigned",
    )
    trace = ST.DecisionTrace()
    with ST.candidate_scope(trace, "1"):
        plan = RS.simulate_bag_route_v2(
            (53.13, 23.16), bag, new, now=now, sla_minutes=35)
    assert plan is not None
    assert trace.candidate_snapshot("1")["solver"]["calls"] >= 1


@pytest.mark.parametrize(
    ("value", "expected", "skew"),
    [
        ("2026-07-10T09:59:59+00:00", 1000.0, False),
        ("2026-07-10T09:59:59Z", 1000.0, False),
        ("2026-07-10T10:00:01", -1000.0, True),
        ("bad", None, False),
        (None, None, False),
    ],
)
def test_event_age_is_signed_and_clock_skew_is_explicit(value, expected, skew):
    now = datetime(2026, 7, 10, 10, 0, tzinfo=timezone.utc)
    age, got_skew = ST.event_age_ms(value, now)
    assert age == expected
    assert got_skew is skew


def test_sidecar_write_is_joinable_and_fail_soft(tmp_path):
    path = tmp_path / "shadow.stage_timings.jsonl"
    ref = ST.event_ref("e1")
    rows = [{"schema": ST.SIDECAR_SCHEMA, "event_ref": ref,
             "service_wall_ms": 12.0}]
    assert ST.append_sidecar_rows(path, rows) == 1
    saved = json.loads(path.read_text().strip())
    assert saved["event_ref"] == ref
    assert "event_id" not in saved and ref != "e1"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def broken(*_args, **_kwargs):
        raise OSError("disk full")

    assert ST.append_sidecar_rows(path, rows, append_fn=broken) == 0


def test_sidecar_path_is_unique_for_shadow_ledger(tmp_path):
    shadow = tmp_path / "shadow_decisions.jsonl"
    assert ST.sidecar_path(shadow) == tmp_path / "shadow_decisions.stage_timings.jsonl"


def _wire_tick(monkeypatch, tmp_path, *, sidecar_fail=False,
               stage_enabled=True, depth_calls=None, stage_flag_reads=None,
               suffix=""):
    created = datetime.now(timezone.utc).isoformat()
    events = [
        {"event_id": f"e{suffix}{i}", "order_id": f"o{suffix}{i}",
         "created_at": created,
         "payload": {"pickup_coords": [53.13, 23.16],
                     "delivery_coords": [53.14, 23.17]}}
        for i in (1, 2)
    ]
    acked = []
    monkeypatch.setattr(SD.event_bus, "get_pending", lambda **_kw: list(events))
    def pending_count(**_kw):
        if depth_calls is not None:
            depth_calls.append(True)
        return 7

    monkeypatch.setattr(SD.event_bus, "get_pending_count", pending_count)
    monkeypatch.setattr(SD.event_bus, "mark_processed",
                        lambda eid: acked.append(eid) or True)
    monkeypatch.setattr(SD, "dispatchable_fleet", lambda: [])
    monkeypatch.setattr(SD.state_machine, "get_all", lambda: {})
    monkeypatch.setattr(
        SD, "process_event",
        lambda *_a, **_kw: types.SimpleNamespace(
            verdict="PROPOSE", best=None,
            stage_timing=(
                {"schema": ST.SCHEMA, "assess_wall_ms": 1.0}
                if stage_enabled else None)),
    )
    monkeypatch.setattr(SD, "_probe_same_restaurant_race", lambda *_a, **_kw: None)
    monkeypatch.setattr(
        SD, "_serialize_result",
        lambda _result, eid, latency_ms: {
            **{"event_id": eid, "verdict": "PROPOSE", "best": None,
               "latency_ms": round(latency_ms, 1)},
            **({"timing": _result.stage_timing}
               if isinstance(_result.stage_timing, dict) else {}),
        })

    def flag(name, default=False):
        if name == "ENABLE_STAGE_TIMING_OBSERVATION":
            if stage_flag_reads is not None:
                stage_flag_reads.append(True)
            return stage_enabled
        return False

    monkeypatch.setattr(SD.C, "flag", flag)
    monkeypatch.setattr(SD.C, "decision_flag", lambda *_a, **_kw: False)
    monkeypatch.setattr(SD.C, "ENABLE_PENDING_POOL", False, raising=False)
    if sidecar_fail:
        monkeypatch.setattr(SD._ST, "append_sidecar_rows", lambda *_a, **_kw: 0)
    shadow = tmp_path / f"shadow_decisions{suffix}.jsonl"
    stats = SD._tick(str(shadow), None)
    return stats, shadow, acked


def test_tick_batch_writes_honest_post_append_sidecar(tmp_path, monkeypatch):
    stats, shadow, acked = _wire_tick(monkeypatch, tmp_path)
    assert stats == {"processed": 2, "failed": 0, "skipped": 0}
    assert acked == ["e1", "e2"]

    decisions = [json.loads(line) for line in shadow.read_text().splitlines()]
    timing_rows = [
        json.loads(line)
        for line in ST.sidecar_path(shadow).read_text().splitlines()
    ]
    decision_rows = [row for row in timing_rows if row.get("scope") == "decision"]
    tick_rows = [row for row in timing_rows if row.get("scope") == "tick"]
    assert [row["event_ref"] for row in decision_rows] == [
        ST.event_ref("e1"), ST.event_ref("e2")]
    assert all("event_id" not in row and "order_id" not in row
               for row in timing_rows)
    assert [row["queue"]["batch_index"] for row in decision_rows] == [0, 1]
    assert all(row["queue"]["panel_ingress_ms"] is None for row in decision_rows)
    assert all(row["queue"]["panel_ingress_missing_reason"] ==
               "no_pre_fetch_anchor" for row in decision_rows)
    assert [row["phase"] for row in tick_rows] == ["open", "complete"]
    assert tick_rows[1]["queue"]["depth_sample"] == 7
    assert tick_rows[1]["queue"]["depth_sample_source"] == "query"
    assert tick_rows[1]["queue"]["depth_sample_atomic"] is False
    assert tick_rows[1]["outcomes"] == {
        "processed": 2, "failed": 0, "skipped": 0,
        "attempted": 2, "decision_rows_buffered": 2}
    assert len({row["tick_ref"] for row in timing_rows}) == 1
    assert [row["event_ref"] for row in decisions] == [
        row["event_ref"] for row in decision_rows]
    assert ["queue_tick_timing" in row for row in decisions] == [True, False]
    assert decisions[0]["queue_tick_timing"]["depth_sample"] == 7
    assert decisions[0]["queue_tick_timing"]["tick_ref"] == tick_rows[0]["tick_ref"]
    assert all(row["ledger_append_wall_ms"] >= 0 for row in decision_rows)
    # Causality: actual append duration exists only in the post-append sidecar.
    assert all("ledger_append_wall_ms" not in row["timing"] for row in decisions)
    assert all(row["service_wall_ms"] >= row["ledger_append_wall_ms"]
               for row in decision_rows)


def test_tick_open_precedes_fleet_snapshot_failure(tmp_path, monkeypatch):
    created = datetime.now(timezone.utc).isoformat()
    monkeypatch.setattr(SD.event_bus, "get_pending", lambda **_kw: [{
        "event_id": "e1", "order_id": "o1", "created_at": created,
        "payload": {},
    }])
    monkeypatch.setattr(SD.event_bus, "get_pending_count", lambda **_kw: 1)
    monkeypatch.setattr(
        SD.C, "flag",
        lambda name, default=False: (
            True if name == "ENABLE_STAGE_TIMING_OBSERVATION" else default),
    )
    monkeypatch.setattr(
        SD, "dispatchable_fleet",
        lambda: (_ for _ in ()).throw(RuntimeError("fleet unavailable")))
    shadow = tmp_path / "shadow_decisions.jsonl"
    with pytest.raises(RuntimeError, match="fleet unavailable"):
        SD._tick(str(shadow), None)
    rows = [
        json.loads(line)
        for line in ST.sidecar_path(shadow).read_text().splitlines()
    ]
    assert len(rows) == 1
    assert rows[0]["scope"] == "tick" and rows[0]["phase"] == "open"


def test_tick_sidecar_failure_does_not_change_decision_or_ack(tmp_path, monkeypatch):
    stats, shadow, acked = _wire_tick(
        monkeypatch, tmp_path, sidecar_fail=True)
    assert stats["processed"] == 2 and stats["failed"] == 0
    assert acked == ["e1", "e2"]
    assert len(shadow.read_text().splitlines()) == 2
    assert not ST.sidecar_path(shadow).exists()


def test_tick_kill_switch_hot_on_to_off_has_off_parity(tmp_path, monkeypatch):
    on_depth_calls = []
    on_flag_reads = []
    _, shadow_on, _ = _wire_tick(
        monkeypatch, tmp_path, stage_enabled=True,
        depth_calls=on_depth_calls, stage_flag_reads=on_flag_reads,
        suffix="_on")
    assert len(on_flag_reads) == 1
    assert len(on_depth_calls) == 1
    assert ST.sidecar_path(shadow_on).exists()
    assert all(
        "timing" in row and "event_ref" in row and "queue_timing" in row
        for row in map(json.loads, shadow_on.read_text().splitlines())
    )

    off_depth_calls = []
    off_flag_reads = []
    stats, shadow_off, acked = _wire_tick(
        monkeypatch, tmp_path, stage_enabled=False,
        depth_calls=off_depth_calls, stage_flag_reads=off_flag_reads,
        suffix="_off")
    assert len(off_flag_reads) == 1
    assert stats == {"processed": 2, "failed": 0, "skipped": 0}
    assert acked == ["e_off1", "e_off2"]
    assert off_depth_calls == []
    assert not ST.sidecar_path(shadow_off).exists()
    off_rows = [json.loads(line) for line in shadow_off.read_text().splitlines()]
    assert all(
        not ({"timing", "event_ref", "queue_timing", "queue_tick_timing"}
             & row.keys())
        for row in off_rows
    )
