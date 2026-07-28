"""T1/T6 AUTO-canary: commit-time freshness and independent heartbeat."""
from __future__ import annotations

import json
import inspect
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from dispatch_v2 import authority_card as AC
from dispatch_v2 import proposal_freshness as PF
from dispatch_v2.tools import auto_assign_monitor as M


NOW = datetime(2026, 7, 28, 8, 0, tzinfo=timezone.utc)


def _snapshot(**changes):
    value = {
        "schema": "commit_proposal.v1",
        "proposal_computed_at": NOW.isoformat(),
        "order_generation": "sha256:order",
        "fleet": {"generation": "sha256:fleet", "available_cids": ["101"]},
        "proposal": {"winner_cid": "101"},
        "winner": {
            "active_order_ids": [],
            "bag_size": 0,
            "route_generation": 7,
            "route_signature": "sha256:route",
        },
        "hard_valid": True,
        "code_git_sha": "a" * 40,
        "flag_fingerprint": "flags-v1",
        "signature": "sha256:proposal",
    }
    value.update(changes)
    return value


def test_commit_recheck_stale_dimensions_and_age_are_denied_without_latch():
    original = _snapshot()
    stale_original = _snapshot(
        proposal_computed_at=(NOW - timedelta(seconds=16)).isoformat()
    )
    assert PF.compare_commit_snapshots(
        stale_original, _snapshot(), NOW
    ) == (False, "commit_recheck_proposal_age")
    cases = [
        (
            _snapshot(fleet={"generation": "sha256:new", "available_cids": ["101"]}),
            "commit_recheck_fleet_generation",
        ),
        (
            _snapshot(
                winner={
                    **original["winner"],
                    "active_order_ids": ["OID-X"],
                    "bag_size": 1,
                }
            ),
            "commit_recheck_active_orders",
        ),
        (
            _snapshot(
                winner={**original["winner"], "route_generation": 8}
            ),
            "commit_recheck_route_generation",
        ),
    ]
    for fresh, expected in cases:
        ok, reason = PF.compare_commit_snapshots(original, fresh, NOW)
        assert ok is False
        assert reason == expected


def test_commit_recheck_identical_fresh_snapshot_passes():
    original = _snapshot()
    fresh = json.loads(json.dumps(original))
    ok, reason = PF.compare_commit_snapshots(original, fresh, NOW)
    assert (ok, reason) == (True, "ok")


def test_generation_comparison_is_mutation_oracle():
    original = _snapshot()
    fresh = _snapshot(
        winner={**original["winner"], "route_generation": 8}
    )
    assert PF.compare_commit_snapshots(original, fresh, NOW)[0] is False
    # Mutation control: deleting the changed dimension makes the oracle green.
    fresh["winner"]["route_generation"] = original["winner"]["route_generation"]
    assert PF.compare_commit_snapshots(original, fresh, NOW) == (True, "ok")


def test_r2_and_t1_use_same_snapshot_builder(monkeypatch):
    calls = []

    def shared(*args, **kwargs):
        calls.append((args, kwargs))
        return _snapshot()

    monkeypatch.setattr(PF, "build_decision_snapshot", shared)
    monkeypatch.setattr(PF.state_machine, "get_order_strict", lambda _oid: {})
    monkeypatch.setattr(PF, "_dispatchable_fleet", lambda: {})
    monkeypatch.setattr(PF, "_solve_fresh", lambda *_args: SimpleNamespace())
    episode = PF.prepare_assignment_episode(
        "OID-1", {}, assignment_observed_at=NOW
    )
    commit = PF.prepare_commit_recheck("OID-1", {}, now=NOW)
    assert len(calls) == 2
    assert episode["proposal"] == commit["proposal"]
    assert episode["fleet"] == commit["fleet"]


def test_missing_and_stale_heartbeat_are_fail_closed(tmp_path):
    path = tmp_path / "monitor-heartbeat.json"
    assert M.heartbeat_fresh(str(path), NOW) == (
        False,
        "monitor_heartbeat_stale",
    )
    M.write_heartbeat(
        str(path),
        {
            "ts": (NOW - timedelta(seconds=61)).isoformat(),
            "pid": 123,
            "checks": {},
        },
    )
    assert M.heartbeat_fresh(str(path), NOW)[0] is False
    M.write_heartbeat(
        str(path), {"ts": NOW.isoformat(), "pid": 123, "checks": {}}
    )
    assert M.heartbeat_fresh(str(path), NOW) == (True, "ok")


def test_monitor_counter_divergence_latches_and_writes_atomic_heartbeat(tmp_path):
    card_state = tmp_path / "card-state.json"
    auto_state = tmp_path / "auto-state.json"
    heartbeat = tmp_path / "monitor-heartbeat.json"
    shadow = tmp_path / "shadow.jsonl"
    AC.save_state(
        str(card_state),
        {
            **AC.empty_state(),
            "executed_total": 1,
            "executed_ts": [NOW.timestamp()],
            "in_flight": "OID-1",
            "pending_verification": ["OID-1"],
        },
    )
    auto_state.write_text(
        json.dumps({"executed_total": 0, "executed_order_ids": []}),
        encoding="utf-8",
    )
    shadow.write_text("", encoding="utf-8")

    result = M.run_cycle(
        now=NOW,
        heartbeat_path=str(heartbeat),
        authority_state_path=str(card_state),
        auto_state_path=str(auto_state),
        shadow_path=str(shadow),
    )
    assert result["checks"]["verdict"] == "ALARM"
    assert AC.load_state(str(card_state))["auto_off_latch"] is True
    assert heartbeat.exists()
    assert not list(tmp_path.glob("monitor-heartbeat.json.tmp.*"))
    assert json.loads(heartbeat.read_text(encoding="utf-8")) == result


def test_monitor_accepts_correlated_unknown_execution_budget(tmp_path):
    """F7: unknown konsumuje oba liczniki, więc nie tworzy fałszywej dywergencji."""
    card_state = tmp_path / "card-state.json"
    auto_state = tmp_path / "auto-state.json"
    heartbeat = tmp_path / "monitor-heartbeat.json"
    shadow = tmp_path / "shadow.jsonl"
    AC.save_state(
        str(card_state),
        {
            **AC.empty_state(),
            "executed_total": 1,
            "executed_ts": [NOW.timestamp()],
            "in_flight": "OID-U",
            "pending_verification": ["OID-U"],
            "auto_off_latch": True,
            "auto_off_reason": "runner_outcome_unknown",
            "auto_off_ts": NOW.isoformat(),
        },
    )
    auto_state.write_text(
        json.dumps({
            "executed_total": 1,
            "executed_order_ids": ["OID-U"],
            "assigned_orders": {"OID-U": NOW.timestamp()},
        }),
        encoding="utf-8",
    )
    shadow.write_text("", encoding="utf-8")

    result = M.run_cycle(
        now=NOW,
        heartbeat_path=str(heartbeat),
        authority_state_path=str(card_state),
        auto_state_path=str(auto_state),
        shadow_path=str(shadow),
    )

    assert "counter_divergence" not in result["checks"]["reasons"]
    assert result["checks"]["card_executed_total"] == 1
    assert result["checks"]["executor_executed_total"] == 1
    assert result["checks"]["reasons"] == ["latch_on"]


def test_monitor_uncovered_auto_executed_receipt_latches(tmp_path):
    card_state = tmp_path / "card-state.json"
    auto_state = tmp_path / "auto-state.json"
    heartbeat = tmp_path / "heartbeat.json"
    shadow = tmp_path / "shadow.jsonl"
    AC.save_state(str(card_state), AC.empty_state())
    auto_state.write_text(
        json.dumps({"executed_total": 0, "executed_order_ids": []}),
        encoding="utf-8",
    )
    shadow.write_text(
        json.dumps({
            "ts": NOW.isoformat(),
            "record_type": "auto_executed",
            "order_id": "OID-X",
        }) + "\n",
        encoding="utf-8",
    )
    result = M.run_cycle(
        now=NOW,
        heartbeat_path=str(heartbeat),
        authority_state_path=str(card_state),
        auto_state_path=str(auto_state),
        shadow_path=str(shadow),
    )
    assert "auto_executed_uncovered" in result["checks"]["reasons"]
    assert AC.load_state(str(card_state))["auto_off_latch"] is True


def test_lock_and_atomic_write_ratchet():
    lock_source = inspect.getsource(AC.state_lock)
    save_source = inspect.getsource(AC._save_state_unlocked)
    heartbeat_source = inspect.getsource(M.write_heartbeat)
    assert "fcntl.flock" in lock_source
    assert "os.replace" in save_source
    assert "os.fsync" in save_source
    assert "os.replace" in heartbeat_source
    assert "os.fsync" in heartbeat_source
