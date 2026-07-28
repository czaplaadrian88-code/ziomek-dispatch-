"""Etap 1 — trwały, miękki claim wiszących propozycji między tickami."""
from __future__ import annotations

import json
import inspect
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from dispatch_v2 import claim_ledger as CL
from dispatch_v2 import common as C
from dispatch_v2 import lifecycle_downstream as LD
from dispatch_v2 import panel_watcher as PW
from dispatch_v2 import pending_proposals_store as PPS
from dispatch_v2 import shadow_dispatcher as SD
from dispatch_v2.dispatch_pipeline import Candidate, PipelineResult


NOW = datetime(2026, 7, 28, 9, 12, 36, tzinfo=timezone.utc)


class _Courier:
    def __init__(self, cid: str):
        self.courier_id = cid
        self.name = cid
        self.bag = []


def _fleet():
    return {cid: _Courier(cid) for cid in ("A", "B", "C", "D")}


def _state_order(oid: str, pickup_index: int) -> dict:
    return {
        "order_id": oid,
        "status": "planned",
        "courier_id": None,
        "pickup_coords": [53.10 + pickup_index * 0.01, 23.10],
        "delivery_coords": [53.20, 23.20 + pickup_index * 0.01],
    }


def _pending_entry(oid: str, cid: str, sent_at: datetime) -> dict:
    return {
        "sent_at": sent_at.isoformat(),
        "expires_at": (sent_at + timedelta(minutes=30)).isoformat(),
        "decision_record": {
            "order_id": oid,
            "verdict": "PROPOSE",
            "best": {"courier_id": cid},
        },
    }


def _apply_from_pending(pending, state, now, *, mutant_empty=False):
    active = PPS.active_proposal_claims(
        {} if mutant_empty else pending,
        state,
        now,
        240,
    )
    fleet = _fleet()
    for claim in active:
        fleet = CL.tentative_assign(
            fleet,
            claim["cid"],
            claim["order"],
            commitment_level="proposed",
        )
    return fleet, active


def _oracle_0912(*, enabled: bool, mutant_empty: bool = False):
    """3 NEW_ORDER, 3 osobne ticki, odstęp 21 s, pule 1/2/2."""
    pending = {}
    state = {}
    winners = []
    proposed_seen = []
    pickup_coords = []
    allowed_per_tick = [("A",), ("A", "B"), ("A", "B")]
    for index, allowed in enumerate(allowed_per_tick):
        oid = f"o{index + 1}"
        now = NOW + timedelta(seconds=21 * index)
        state[oid] = _state_order(oid, index)
        pickup_coords.append(tuple(state[oid]["pickup_coords"]))
        fleet, _ = (
            _apply_from_pending(
                pending, state, now, mutant_empty=mutant_empty)
            if enabled
            else (_fleet(), [])
        )
        winner = min(
            allowed,
            key=lambda cid: (len(fleet[cid].bag), cid),
        )
        winners.append(winner)
        proposed_seen.append(
            sum(
                1
                for entry in fleet[winner].bag
                if entry.get("commitment_level") == "proposed"
            )
        )
        pending[oid] = _pending_entry(oid, winner, now)
    assert len(set(pickup_coords)) == 3
    if enabled:
        assert len(set(winners)) > 1
        assert any(count > 0 for count in proposed_seen[1:])
    else:
        assert winners == ["A", "A", "A"]
    return winners, proposed_seen


def _oracle_1004(*, mutant_empty: bool = False):
    """5 ticków: ten sam kurier widzi 0→1→2→3→4 claimów; TTL usuwa je."""
    pending = {}
    state = {}
    seen = []
    for index in range(5):
        oid = f"wave-{index + 1}"
        now = NOW + timedelta(seconds=30 * index)
        state[oid] = _state_order(oid, index)
        fleet, _ = _apply_from_pending(
            pending,
            state,
            now,
            mutant_empty=mutant_empty,
        )
        seen.append(len(fleet["A"].bag))
        pending[oid] = _pending_entry(oid, "A", now)
    assert seen == sorted(seen)
    assert seen == [0, 1, 2, 3, 4]
    expired_fleet, active = _apply_from_pending(
        pending,
        state,
        NOW + timedelta(seconds=240 + 5 * 30),
    )
    assert active == []
    assert expired_fleet["A"].bag == []
    return seen


def test_negative_oracle_0912_on_differs_from_off():
    assert _oracle_0912(enabled=False)[0] == ["A", "A", "A"]
    assert len(set(_oracle_0912(enabled=True)[0])) > 1


def _run_real_ticks(monkeypatch, tmp_path, *, persistence: bool, mutant_loader=False):
    """Minimalny E2E `_tick`: każdy call pobiera dokładnie jeden NEW_ORDER."""
    from dispatch_v2 import auto_assign_executor as AAE
    from dispatch_v2 import decision_eta_log as DEL

    pending = {}
    state = {}
    records = []
    index = {"value": 0}
    allowed = [("A",), ("A", "B"), ("A", "B")]

    def next_events(limit=None, event_types=None):
        i = index["value"]
        oid = f"tick-{i + 1}"
        state[oid] = _state_order(oid, i)
        return [{
            "event_id": f"event-{i + 1}",
            "order_id": oid,
            "payload": {
                **state[oid],
                "pickup_coords": state[oid]["pickup_coords"],
                "delivery_coords": state[oid]["delivery_coords"],
            },
        }]

    def fake_process(event, fleet, meta, now=None, **kwargs):
        i = index["value"]
        cid = min(allowed[i], key=lambda value: (len(fleet[value].bag), value))
        best = SimpleNamespace(
            courier_id=cid,
            name=cid,
            score=10.0,
            metrics={"bag_size_before": len(fleet[cid].bag)},
        )
        return SimpleNamespace(
            verdict="PROPOSE",
            reason="PROPOSE",
            best=best,
            candidates=[best],
            pool_feasible_count=len(allowed[i]),
            pool_total_count=4,
            would_auto_assign=False,
        )

    def fake_serialize(result, event_id, latency_ms):
        return {
            "event_id": event_id,
            "order_id": f"tick-{index['value'] + 1}",
            "verdict": result.verdict,
            "reason": result.reason,
            "best": {
                "courier_id": result.best.courier_id,
                "name": result.best.name,
                "proposal_claims_count": result.best.metrics.get(
                    "proposal_claims_count", 0),
                "bag_size_before": result.best.metrics.get(
                    "bag_size_before"),
            },
            "proposal_claims_relaxed": bool(
                getattr(result, "proposal_claims_relaxed", False)),
            "latency_ms": 0.0,
        }

    def fake_upsert(upserts, now):
        for oid, record in upserts:
            pending[str(oid)] = _pending_entry(
                str(oid),
                str(record["best"]["courier_id"]),
                now,
            )
        return len(upserts)

    monkeypatch.setattr(SD.event_bus, "get_pending", next_events)
    monkeypatch.setattr(SD.event_bus, "mark_processed", lambda *_a, **_k: True)
    monkeypatch.setattr(SD.event_bus, "mark_failed", lambda *_a, **_k: None)
    monkeypatch.setattr(SD, "dispatchable_fleet", lambda: list(_fleet().values()))
    monkeypatch.setattr(SD.state_machine, "get_all", lambda: dict(state))
    monkeypatch.setattr(SD, "process_event", fake_process)
    monkeypatch.setattr(SD, "_probe_same_restaurant_race", lambda *_a, **_k: None)
    monkeypatch.setattr(
        SD, "_always_propose_would_redirect_shadow", lambda *_a, **_k: None)
    monkeypatch.setattr(SD, "_serialize_result", fake_serialize)
    monkeypatch.setattr(
        SD, "_append_decision", lambda _path, record: records.append(dict(record)))
    monkeypatch.setattr(
        C,
        "decision_flag",
        lambda name: (
            persistence if name == "ENABLE_PROPOSAL_CLAIM_PERSISTENCE" else False
        ),
    )
    monkeypatch.setattr(
        C,
        "flag",
        lambda name, default=False: (
            True if name == "ENABLE_PENDING_PROPOSALS_WRITE"
            else 240 if name == "PROPOSAL_CLAIM_TTL_SEC"
            else default
        ),
    )
    monkeypatch.setattr(
        PPS,
        "load",
        lambda path=PPS.PENDING_PATH: {} if mutant_loader else dict(pending),
    )
    monkeypatch.setattr(PPS, "upsert_proposals", fake_upsert)
    monkeypatch.setattr(AAE, "maybe_execute", lambda *_a, **_k: None)
    monkeypatch.setattr(DEL, "record_pipeline_decision", lambda *_a, **_k: None)

    for i in range(3):
        index["value"] = i
        stats = SD._tick(str(tmp_path / "shadow.jsonl"), None)
        assert stats["processed"] == 1 and stats["failed"] == 0
    return records


def test_negative_oracle_0912_runs_three_real_separate_ticks(
    monkeypatch,
    tmp_path,
):
    off = _run_real_ticks(
        monkeypatch,
        tmp_path,
        persistence=False,
    )
    assert [row["best"]["courier_id"] for row in off] == ["A", "A", "A"]


def test_negative_oracle_0912_real_ticks_on_and_loader_mutation(
    monkeypatch,
    tmp_path,
):
    on = _run_real_ticks(
        monkeypatch,
        tmp_path,
        persistence=True,
    )
    assert len({row["best"]["courier_id"] for row in on}) > 1
    assert any(
        row["best"]["proposal_claims_count"] > 0 for row in on[1:]
    )
    assert all(row["best"]["bag_size_before"] == 0 for row in on)
    mutant = _run_real_ticks(
        monkeypatch,
        tmp_path,
        persistence=True,
        mutant_loader=True,
    )
    assert [row["best"]["courier_id"] for row in mutant] == ["A", "A", "A"]


def test_oracle_1004_monotonic_across_ticks_and_ttl_expiry():
    assert _oracle_1004() == [0, 1, 2, 3, 4]


def test_loader_neutralization_mutation_turns_both_oracles_red():
    with pytest.raises(AssertionError):
        _oracle_0912(enabled=True, mutant_empty=True)
    with pytest.raises(AssertionError):
        _oracle_1004(mutant_empty=True)


def test_soft_koord_guard_reassesses_without_claims(monkeypatch):
    with_claims = _fleet()
    with_claims = CL.tentative_assign(
        with_claims,
        "A",
        _state_order("old", 0),
        commitment_level="proposed",
    )
    without_claims = _fleet()
    calls = []

    def fake_process(event, fleet, meta, now=None, **kwargs):
        has_proposed = bool(CL.verify_proposal_claim_window(
            fleet, [{"cid": "A", "oid": "old"}]) == []
            and fleet["A"].bag)
        calls.append((has_proposed, kwargs.get("_record_world", True)))
        best = Candidate(
            courier_id="A",
            name="A",
            score=1.0,
            feasibility_verdict="MAYBE",
            feasibility_reason="ok",
            plan=None,
            metrics={"bag_size_before": len(fleet["A"].bag)},
        )
        return PipelineResult(
            order_id="new",
            verdict="KOORD" if has_proposed else "PROPOSE",
            reason="no_feasible" if has_proposed else "PROPOSE",
            best=None if has_proposed else best,
            candidates=[] if has_proposed else [best],
            pickup_ready_at=None,
            restaurant="R",
            delivery_address="D",
            pool_feasible_count=0 if has_proposed else 1,
            pool_total_count=1,
        )

    monkeypatch.setattr(SD, "process_event", fake_process)
    result, used = SD._assess_with_proposal_claim_relaxation(
        {"order_id": "new", "payload": {}},
        with_claims,
        without_claims,
        None,
        NOW,
        persistence_enabled=True,
    )
    assert result.verdict == "PROPOSE"
    assert result.proposal_claims_relaxed is True
    assert result.best.metrics["proposal_claims_count"] == 0
    assert used is without_claims
    assert calls == [(True, True), (False, True)]


def test_assigned_order_is_not_loaded_as_proposed_dedup():
    pending = {"o1": _pending_entry("o1", "A", NOW)}
    state = {
        "o1": {
            **_state_order("o1", 0),
            "status": "assigned",
            "courier_id": "A",
        }
    }
    assert PPS.active_proposal_claims(pending, state, NOW, 240) == []
    fleet, active = _apply_from_pending(pending, state, NOW)
    assert active == []
    assert fleet["A"].bag == []


def test_courier_assigned_downstream_removes_pending_after_consumers(
    tmp_path,
    monkeypatch,
):
    path = tmp_path / "pending_proposals.json"
    PPS.locked_set("o1", _pending_entry("o1", "A", NOW), str(path))
    PPS.locked_set("other", _pending_entry("other", "B", NOW), str(path))
    # Kanoniczny wzorzec hermetyczny: ścieżka pending resolwowana w call-time
    # z DISPATCH_STATE_DIR (jak state_machine) — monkeypatch env, nie stałej.
    monkeypatch.setenv("DISPATCH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(
        LD.state_machine,
        "get_order_strict",
        lambda oid: {"status": "assigned", "courier_id": "A"},
    )
    order = []
    monkeypatch.setattr(
        PW,
        "_check_panel_agree",
        lambda *a, **k: order.append("learning-agree"),
    )
    monkeypatch.setattr(
        PW,
        "_check_panel_override",
        lambda *a, **k: order.append("learning-override"),
    )
    monkeypatch.setattr(
        PW,
        "_save_plan_on_assign_signal",
        lambda *a, **k: order.append("plan"),
    )
    LD.apply({
        "event_type": "COURIER_ASSIGNED",
        "event_id": "assign-o1-A",
        "order_id": "o1",
        "courier_id": "A",
        "payload": {"source": "panel_diff"},
        "panel_learning_context": {},
    })
    assert order == ["learning-agree", "learning-override", "plan"]
    remaining = PPS.load(str(path))
    assert "o1" not in remaining
    assert "other" in remaining


def test_proposal_claim_window_ratchet_and_checker_error_policy(monkeypatch):
    claims = [
        {"cid": "A", "oid": "o1", "order": _state_order("o1", 0)},
        {"cid": "A", "oid": "o2", "order": _state_order("o2", 1)},
    ]
    fleet = _fleet()
    for claim in claims:
        fleet = CL.tentative_assign(
            fleet,
            claim["cid"],
            claim["order"],
            commitment_level="proposed",
        )
    assert CL.verify_proposal_claim_window(fleet, claims) == []
    assert len(CL.verify_proposal_claim_window(_fleet(), claims)) == 2

    monkeypatch.setattr(
        CL,
        "verify_proposal_claim_window",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    violation = CL.check_proposal_claim_window_guarded(_fleet(), claims)
    assert violation[0]["kind"] == "checker_error"
    assert violation[0]["exception_type"] == "RuntimeError"


def test_serializer_emits_explicit_fields_without_redefining_bag_size():
    best = Candidate(
        courier_id="A",
        name="A",
        score=1.0,
        feasibility_verdict="MAYBE",
        feasibility_reason="ok",
        plan=None,
        metrics={
            "bag_size_before": 3,
            "proposal_claims_count": 2,
        },
    )
    result = PipelineResult(
        order_id="o",
        verdict="PROPOSE",
        reason="PROPOSE",
        best=best,
        candidates=[best],
        pickup_ready_at=None,
        restaurant="R",
        delivery_address="D",
        pool_feasible_count=1,
        pool_total_count=1,
    )
    result.proposal_claims_relaxed = True
    out = SD._serialize_result(result, "event-o", 1.0)
    assert out["proposal_claims_count"] == 2
    assert out["proposal_claims_relaxed"] is True
    assert out["best"]["proposal_claims_count"] == 2
    assert out["best"]["bag_size_before"] == 3


def test_flags_registered_with_safe_defaults():
    registry = json.loads(
        (C.SCRIPTS_DIR / "dispatch_v2/tools/flag_lifecycle_registry.json").read_text()
    )["flags"]
    assert "ENABLE_PROPOSAL_CLAIM_PERSISTENCE" in C.ETAP4_DECISION_FLAGS
    assert C.ENABLE_PROPOSAL_CLAIM_PERSISTENCE is False
    assert C.PROPOSAL_CLAIM_TTL_SEC == 240
    assert registry["ENABLE_PROPOSAL_CLAIM_PERSISTENCE"]["default"] is False
    assert registry["PROPOSAL_CLAIM_TTL_SEC"]["default"] == 240


def test_tick_wires_existing_store_and_no_new_writer():
    text = inspect.getsource(SD._tick)
    helper = inspect.getsource(SD._apply_persistent_proposal_claims)
    assert "_apply_persistent_proposal_claims" in text
    assert "ENABLE_PROPOSAL_CLAIM_PERSISTENCE" in text
    assert "_pps.load" in helper
    assert "upsert_proposals" not in helper
    assert "locked_" not in helper
    # Jedyny writer ticku to zastany Opcja-B upsert; claim persistence tylko czyta.
    assert text.count("upsert_proposals") == 1
