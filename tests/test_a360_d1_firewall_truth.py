"""A360-D1: finalny status odpowiedzialnosci dociera do ledgeru bez mutacji decyzji."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace as NS

from dispatch_v2 import auto_assign_executor
from dispatch_v2 import common as C
from dispatch_v2 import dispatch_pipeline as DP
from dispatch_v2 import live_eta_cache
from dispatch_v2 import shadow_dispatcher as SD
from dispatch_v2.core import invariant_firewall as FW
from dispatch_v2.tools import ledger_io


NOW = datetime(2026, 7, 11, 10, 0, tzinfo=timezone.utc)


def _policy():
    return FW.FirewallPolicy(
        r6_limit_min=35.0,
        sla_limit_min=35.0,
        r27_strict_limit_min=5.0,
        r27_overload_limit_min=10.0,
        overload_threshold=4.5,
        package_address_ids=(161, 232),
        package_thermal_exempt=True,
        sla_anchor_kind="now",
        always_propose_enabled=False,
    )


def _plan(*, elapsed_new=20.0):
    return NS(
        per_order_delivery_times={"B": 45.0, "N": elapsed_new},
        predicted_delivered_at={
            "B": NOW + timedelta(minutes=5),
            "N": NOW + timedelta(minutes=elapsed_new),
        },
        pickup_at={"N": NOW + timedelta(minutes=10)},
        sequence=["B", "N"],
        total_duration_min=55.0,
        strategy="a360_d1_synthetic",
        sla_violations=1,
        osrm_fallback_used=False,
    )


def _result():
    best_plan = _plan()
    alt_plan = _plan(elapsed_new=22.0)
    best = DP.Candidate(
        courier_id="synthetic-best",
        name="Synthetic Best",
        score=77.0,
        feasibility_verdict="MAYBE",
        feasibility_reason="synthetic",
        plan=best_plan,
        metrics={"loadgov_load_ewma": 2.0, "a360_d1_marker": "LOCATION_B"},
    )
    alternative = DP.Candidate(
        courier_id="synthetic-alt",
        name="Synthetic Alt",
        score=70.0,
        feasibility_verdict="MAYBE",
        feasibility_reason="synthetic",
        plan=alt_plan,
        metrics={"a360_d1_marker": "LOCATION_A"},
    )
    result = DP.PipelineResult(
        order_id="N",
        verdict="PROPOSE",
        reason="synthetic a360-d1",
        best=best,
        candidates=[best, alternative],
        pickup_ready_at=NOW,
        restaurant="Synthetic Restaurant",
        delivery_address="Synthetic Address",
        pool_total_count=2,
        pool_feasible_count=2,
    )
    fleet = {"synthetic-best": NS(bag=[{
        "order_id": "B",
        "status": "picked_up",
        "address_id": 1,
        "picked_up_at": (NOW - timedelta(minutes=40)).isoformat(),
    }])}
    result.rule_verdict = FW.evaluate_final(
        result,
        {"order_id": "N", "address_id": 1, "order_type": "elastic"},
        fleet,
        NOW,
        _policy(),
    )
    return result


def test_location_a_b_keep_candidate_parity_and_one_final_rule_verdict(monkeypatch):
    result = _result()
    best_obj = result.best
    plan_obj = result.best.plan
    verdict_before = result.verdict
    score_before = result.best.score
    sequence_before = list(result.best.plan.sequence)
    monkeypatch.setattr(C, "flag", lambda *_a, **_kw: False)

    record = SD._serialize_result(result, event_id="synthetic-event", latency_ms=1.0)

    assert record["rule_verdict"]["schema"] == "rule_verdict.v2"
    assert record["rule_verdict"]["status"] == FW.EXEMPT_PREEXISTING
    assert record["rule_verdict"]["physical_status"] == FW.VIOLATION
    assert record["best"]["a360_d1_marker"] == "LOCATION_B"
    assert record["alternatives"][0]["a360_d1_marker"] == "LOCATION_A"
    assert "rule_verdict" not in record["best"]
    assert "rule_verdict" not in record["alternatives"][0]

    assert result.verdict == verdict_before
    assert result.best is best_obj
    assert result.best.plan is plan_obj
    assert result.best.score == score_before
    assert result.best.plan.sequence == sequence_before


def test_real_tick_writes_v2_to_tmp_jsonl_and_canonical_reader_preserves_it(
        tmp_path, monkeypatch):
    result = _result()
    event = {
        "event_id": "synthetic-event",
        "order_id": "N",
        "created_at": NOW.isoformat(),
        "payload": {
            "order_id": "N",
            "address_id": 1,
            "pickup_coords": [53.13, 23.16],
            "delivery_coords": [53.14, 23.17],
        },
    }
    processed = []
    failed = []
    monkeypatch.setattr(SD.event_bus, "get_pending", lambda **_kw: [event])
    monkeypatch.setattr(
        SD.event_bus, "mark_processed", lambda event_id: processed.append(event_id) or True)
    monkeypatch.setattr(
        SD.event_bus, "mark_failed", lambda event_id, reason: failed.append((event_id, reason)))
    monkeypatch.setattr(SD, "dispatchable_fleet", lambda: [])
    monkeypatch.setattr(SD.state_machine, "get_all", lambda: {})
    monkeypatch.setattr(SD, "process_event", lambda *_a, **_kw: result)
    monkeypatch.setattr(SD, "_probe_same_restaurant_race", lambda *_a, **_kw: None)
    monkeypatch.setattr(C, "flag", lambda *_a, **_kw: False)
    monkeypatch.setattr(C, "decision_flag", lambda *_a, **_kw: False)
    monkeypatch.setattr(C, "ENABLE_PENDING_POOL", False)
    monkeypatch.setattr(C, "ENABLE_R_PACZKI_FLEX", False)
    monkeypatch.setattr(live_eta_cache, "upsert", lambda **_kw: None)
    monkeypatch.setattr(auto_assign_executor, "maybe_execute", lambda *_a, **_kw: None)

    shadow_path = tmp_path / "shadow_decisions.jsonl"
    stats = SD._tick(str(shadow_path), None)

    assert stats == {"processed": 1, "failed": 0, "skipped": 0}
    assert processed == ["synthetic-event"] and failed == []
    monkeypatch.setitem(ledger_io.LEDGER, "shadow", str(shadow_path))
    rows = list(ledger_io.iter_shadow_decisions(None))
    assert len(rows) == 1
    rv = rows[0]["rule_verdict"]
    assert rv["status"] == FW.EXEMPT_PREEXISTING
    assert rv["physical_status"] == FW.VIOLATION
    assert rv["preexisting_rule_variant_row_count"] == 2
    assert rv["count_unit"] == "rule_variant_rows"
    assert {row["provenance_stage"] for row in rv["violations"]} == {
        FW.PREEXISTING_STAGE,
    }


def test_plain_dict_v1_and_mixed_v1_v2_ledger_keep_status_dictionaries_separate(
        tmp_path, monkeypatch):
    legacy_v1 = {
        "schema": "rule_verdict.v1",
        "phase": "A_SHADOW",
        "status": "VIOLATION",
        "violations": [{"rule_id": "R6_THERMAL", "order_id": "legacy"}],
        "exceptions": [],
    }
    legacy_result = _result()
    legacy_result.rule_verdict = legacy_v1

    serialized_v1 = SD._serialize_rule_verdict(legacy_result)
    assert serialized_v1 == legacy_v1
    assert serialized_v1["status"] == "VIOLATION"
    assert "physical_status" not in serialized_v1
    assert "count_unit" not in serialized_v1

    serialized_v2 = SD._serialize_rule_verdict(_result())
    assert serialized_v2["schema"] == "rule_verdict.v2"
    assert serialized_v2["status"] == FW.EXEMPT_PREEXISTING
    assert serialized_v2["physical_status"] == FW.VIOLATION
    assert serialized_v2["count_unit"] == FW.COUNT_UNIT

    shadow_path = tmp_path / "mixed_shadow_decisions.jsonl"
    SD._append_decision(str(shadow_path), {
        "ts": NOW.isoformat(), "order_id": "legacy", "rule_verdict": serialized_v1,
    })
    SD._append_decision(str(shadow_path), {
        "ts": (NOW + timedelta(seconds=1)).isoformat(),
        "order_id": "current", "rule_verdict": serialized_v2,
    })
    monkeypatch.setitem(ledger_io.LEDGER, "shadow", str(shadow_path))
    rows = list(ledger_io.iter_shadow_decisions(None))
    assert [row["rule_verdict"]["schema"] for row in rows] == [
        "rule_verdict.v1", "rule_verdict.v2",
    ]
    assert rows[0]["rule_verdict"]["status"] == "VIOLATION"
    assert "physical_status" not in rows[0]["rule_verdict"]
    assert rows[1]["rule_verdict"]["status"] == FW.EXEMPT_PREEXISTING
    assert rows[1]["rule_verdict"]["physical_status"] == FW.VIOLATION
