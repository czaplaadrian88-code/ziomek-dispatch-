"""R2: assignment-time truth and shadow-only proposal refresh.

The tests intentionally exercise the two mutation-sensitive seams:
* removing the fresh canonical solve makes the first test select the stale CID;
* removing the lifecycle-generation CAS makes the stale-generation test append.
"""
from __future__ import annotations

import inspect
import json
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from dispatch_v2 import common as C
from dispatch_v2 import proposal_freshness as PF
from dispatch_v2 import proposal_refresh as PR


def _candidate(cid: str, score: float):
    return SimpleNamespace(
        courier_id=cid,
        score=score,
        feasibility_verdict="MAYBE",
        metrics={},
    )


def _result(winner: str, runner_up: str = "CID-A"):
    best = _candidate(winner, 90.0)
    other = _candidate(runner_up, 74.0)
    return SimpleNamespace(
        order_id="OID-1",
        verdict="PROPOSE",
        best=best,
        candidates=[best, other],
        full_pool_candidates=[best, other],
        pool_total_count=2,
        pool_feasible_count=2,
        auto_route="ACK",
        auto_route_context={"score_margin": 16.0},
    )


def _fleet(*cids: str):
    return {
        cid: SimpleNamespace(
            courier_id=cid,
            bag=[],
            pos_source="gps",
            lat=53.0,
            lng=23.0,
            shift_start=None,
            shift_end=None,
        )
        for cid in cids
    }


def test_assignment_episode_resolves_now_instead_of_using_stale_proposal(monkeypatch):
    old_payload = {
        "order_id": "OID-1",
        "pickup_coords": [53.1, 23.1],
        "delivery_coords": [53.2, 23.2],
        "stale_proposal_cid": "CID-A",
    }
    seen = {}

    monkeypatch.setattr(PF, "_dispatchable_fleet", lambda: _fleet("CID-A", "CID-B"))

    def fresh_solve(order_event, fleet, now):
        seen["fleet"] = sorted(fleet)
        seen["old_value"] = order_event.get("stale_proposal_cid")
        return _result("CID-B")

    monkeypatch.setattr(PF, "_solve_fresh", fresh_solve)
    prepared = PF.prepare_assignment_episode(
        "OID-1", old_payload, assignment_observed_at=datetime.now(timezone.utc)
    )

    assert seen == {"fleet": ["CID-A", "CID-B"], "old_value": None}
    assert prepared["proposal"]["winner_cid"] == "CID-B"
    assert prepared["proposal"]["runner_up_cid"] == "CID-A"
    assert prepared["proposal"]["score_margin"] == 16.0
    assert prepared["fleet"]["available_cids"] == ["CID-A", "CID-B"]


def test_assignment_episode_commit_requires_exact_generation_under_lock(
    monkeypatch, tmp_path
):
    prepared = {
        "schema": "assignment_episode.v1",
        "order_id": "OID-1",
        "proposal_computed_at": datetime.now(timezone.utc).isoformat(),
        "fleet": {"generation": "sha256:test"},
        "proposal": {
            "winner_cid": "CID-B",
            "runner_up_cid": "CID-A",
            "score_margin": 16.0,
        },
    }
    current = {
        "status": "assigned",
        "courier_id": "CID-B",
        "assigned_at": "2026-07-28T01:00:00+00:00",
        "assignment_event_id": "assign-newer",
        "last_lifecycle_event_id_courier_assigned": "assign-newer",
    }
    entered = []

    @contextmanager
    def lock():
        entered.append("lock")
        yield

    monkeypatch.setattr(PF.state_machine, "lifecycle_apply_lock", lock)
    monkeypatch.setattr(PF.state_machine, "get_order_strict", lambda _oid: current)
    monkeypatch.setattr(PF, "ASSIGNMENT_EPISODE_PATH", tmp_path / "episodes.jsonl")

    assert PF.commit_assignment_episode(prepared, "assign-old", "CID-B") is False
    assert entered == ["lock"]
    assert not (tmp_path / "episodes.jsonl").exists()

    current["assignment_event_id"] = "assign-old"
    current["last_lifecycle_event_id_courier_assigned"] = "assign-old"
    assert PF.commit_assignment_episode(prepared, "assign-old", "CID-B") is True
    row = json.loads((tmp_path / "episodes.jsonl").read_text().strip())
    assert row["assignment_generation"] == "assign-old"
    assert row["actual_assigned_cid"] == "CID-B"
    assert row["agreement"] is True
    assert row["cas"]["matched"] is True


def test_assignment_episode_duplicate_generation_is_at_most_once(monkeypatch, tmp_path):
    prepared = {
        "schema": "assignment_episode.v1",
        "order_id": "OID-1",
        "proposal_computed_at": datetime.now(timezone.utc).isoformat(),
        "fleet": {"generation": "sha256:test"},
        "proposal": {"winner_cid": "CID-B"},
    }
    current = {
        "status": "assigned",
        "courier_id": "CID-B",
        "assigned_at": "2026-07-28T01:00:00+00:00",
        "assignment_event_id": "assign-1",
        "last_lifecycle_event_id_courier_assigned": "assign-1",
    }
    monkeypatch.setattr(PF.state_machine, "get_order_strict", lambda _oid: current)
    monkeypatch.setattr(PF, "ASSIGNMENT_EPISODE_PATH", tmp_path / "episodes.jsonl")

    assert PF.commit_assignment_episode(prepared, "assign-1", "CID-B") is True
    assert PF.commit_assignment_episode(prepared, "assign-1", "CID-B") is False
    assert len((tmp_path / "episodes.jsonl").read_text().splitlines()) == 1


def test_assignment_episode_durable_retry_is_not_late_backfill(monkeypatch):
    current = {
        "assignment_event_id": "assign-1",
        "last_lifecycle_event_id_courier_assigned": "assign-1",
    }
    monkeypatch.setattr(PF.state_machine, "get_order_strict", lambda _oid: current)
    monkeypatch.setattr(
        PF,
        "_solve_fresh",
        lambda *_a, **_kw: (_ for _ in ()).throw(
            AssertionError("retry must not re-solve a later fleet")
        ),
    )
    assert PF.prepare_assignment_episode(
        "OID-1", {}, expected_assignment_event_id="assign-1"
    ) is None


def test_panel_assignment_instrument_is_fail_safe(monkeypatch):
    from dispatch_v2 import panel_watcher as watcher

    sentinel = SimpleNamespace(event_id="assign-1", state_ready=True)
    monkeypatch.setattr(C, "decision_flag", lambda name: name == PF.FLAG)
    monkeypatch.setattr(
        PF, "prepare_assignment_episode", lambda *_a, **_kw: (_ for _ in ()).throw(
            RuntimeError("instrument down")
        )
    )
    monkeypatch.setattr(
        watcher.durable_event_apply, "emit_and_apply", lambda *_a, **_kw: sentinel
    )

    outcome = watcher._emit_and_apply_state(
        "COURIER_ASSIGNED",
        order_id="OID-1",
        courier_id="CID-B",
        state_payload={"source": "panel"},
        event_id="assign-1",
    )
    assert outcome is sentinel


def test_panel_assignment_prepares_before_apply_and_cas_commits_after(monkeypatch):
    from dispatch_v2 import panel_watcher as watcher

    sequence = []
    sentinel = SimpleNamespace(event_id="assign-actual", state_ready=True)
    prepared = {"schema": PF.SCHEMA, "order_id": "OID-1"}
    monkeypatch.setattr(C, "decision_flag", lambda name: name == PF.FLAG)

    def prepare(*_args, **kwargs):
        sequence.append(("prepare", kwargs["expected_assignment_event_id"]))
        return prepared

    def durable(*_args, **_kwargs):
        sequence.append(("durable", None))
        return sentinel

    def commit(value, event_id, cid):
        sequence.append(("commit", event_id, cid))
        assert value is prepared
        return True

    monkeypatch.setattr(PF, "prepare_assignment_episode", prepare)
    monkeypatch.setattr(PF, "commit_assignment_episode", commit)
    monkeypatch.setattr(watcher.durable_event_apply, "emit_and_apply", durable)

    outcome = watcher._emit_and_apply_state(
        "COURIER_ASSIGNED",
        order_id="OID-1",
        courier_id="CID-B",
        state_payload={"source": "panel"},
        event_id="assign-requested",
    )
    assert outcome is sentinel
    assert sequence == [
        ("prepare", "assign-requested"),
        ("durable", None),
        ("commit", "assign-actual", "CID-B"),
    ]


def test_refresh_only_after_fleet_and_winner_change_with_cooldown(
    monkeypatch, tmp_path
):
    now = datetime(2026, 7, 28, 1, 0, tzinfo=timezone.utc)
    monkeypatch.setattr(PR, "REFRESH_STATE_PATH", tmp_path / "refresh-state.json")
    monkeypatch.setattr(PR, "SHADOW_DECISIONS_PATH", tmp_path / "shadow.jsonl")

    # First observation only establishes a baseline.
    assert PR.record_refreshes(
        {"OID-1": _result("CID-A", "CID-B")},
        {"OID-1": {"cid": "CID-A"}},
        _fleet("CID-A"),
        now,
    ) == 0
    assert not (tmp_path / "shadow.jsonl").exists()

    # Fleet changed, but the canonical winner did not.
    assert PR.record_refreshes(
        {"OID-1": _result("CID-A", "CID-B")},
        {"OID-1": {"cid": "CID-A"}},
        _fleet("CID-A", "CID-B"),
        now + timedelta(seconds=10),
    ) == 0

    # Both fleet generation and winner changed: one shadow-only record.
    assert PR.record_refreshes(
        {"OID-1": _result("CID-B", "CID-A")},
        {"OID-1": {"cid": "CID-A"}},
        _fleet("CID-B"),
        now + timedelta(seconds=20),
    ) == 1
    rows = (tmp_path / "shadow.jsonl").read_text().splitlines()
    assert len(rows) == 1
    row = json.loads(rows[0])
    assert row["record_type"] == "proposal_refresh"
    assert row["verdict"] == "SHADOW_ONLY"
    assert row["proposal_refresh"]["previous_winner_cid"] == "CID-A"
    assert row["proposal_refresh"]["winner_cid"] == "CID-B"
    assert "best" not in row

    # A rapid second winner change is throttled.
    assert PR.record_refreshes(
        {"OID-1": _result("CID-C", "CID-B")},
        {"OID-1": {"cid": "CID-A"}},
        _fleet("CID-B", "CID-C"),
        now + timedelta(seconds=30),
    ) == 0
    assert len((tmp_path / "shadow.jsonl").read_text().splitlines()) == 1


def test_refresh_hook_is_fail_safe(monkeypatch):
    from dispatch_v2.tools import pending_global_resweep as pgr

    monkeypatch.setattr(C, "decision_flag", lambda name: name == PR.FLAG)
    monkeypatch.setattr(
        PR, "record_refreshes", lambda *_a, **_kw: (_ for _ in ()).throw(
            OSError("log unavailable")
        )
    )
    assert pgr._record_proposal_refresh_fail_safe(
        {}, {}, {}, datetime.now(timezone.utc)
    ) == 0


def test_r2_flags_are_off_strip_covered_and_fingerprinted(monkeypatch):
    assert PF.FLAG in C.ETAP4_DECISION_FLAGS
    assert PR.FLAG in C.ETAP4_DECISION_FLAGS
    assert C.ENABLE_ASSIGNMENT_EPISODE_LOG is False
    assert C.ENABLE_PROPOSAL_REFRESH is False
    monkeypatch.setattr(C, "load_flags", lambda: {})
    fingerprint = C.flag_fingerprint()
    assert "ENABLE_ASSIGNMENT_EPISODE_LOG=0" in fingerprint
    assert "ENABLE_PROPOSAL_REFRESH=0" in fingerprint


def test_cas_and_shadow_separation_source_ratchets():
    commit_source = inspect.getsource(PF.commit_assignment_episode)
    assert "lifecycle_apply_lock" in commit_source
    assert "assignment_event_id" in commit_source
    assert "last_lifecycle_event_id_courier_assigned" in commit_source

    refresh_source = inspect.getsource(PR._build_refresh_record)
    assert '"verdict": "SHADOW_ONLY"' in refresh_source
    assert '"record_type": "proposal_refresh"' in refresh_source
    assert '"best"' not in refresh_source
