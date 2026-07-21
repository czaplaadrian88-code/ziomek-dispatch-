"""Choice-set + join hardening (owner ACK 2026-07-21).

Kontrakty:
- ENABLE_FULL_CHOICE_SET_LOG OFF usuwa pole, ON loguje pełną pulę sprzed top-N;
- każdy element full_pool_compact ma dokładnie sześć dozwolonych kluczy;
- LOCATION A i B używają tego samego projection helpera;
- ENABLE_LEARNING_LOG_DECISION_JOIN wiąże PANEL_AGREE/OVERRIDE z
  shadow.event_id, nie z późniejszym COURIER_ASSIGNED;
- stary learning_log bez klucza pozostaje czytelny.
"""
from __future__ import annotations

import ast
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

from dispatch_v2 import common as C
from dispatch_v2 import daily_briefing as DB
from dispatch_v2 import panel_watcher as PW
from dispatch_v2 import pending_proposals_store as PPS
from dispatch_v2 import shadow_dispatcher as SD
from dispatch_v2.dispatch_pipeline import PipelineResult


COMPACT_KEYS = {
    "cid",
    "score",
    "feasibility_verdict",
    "pos_source",
    "km_to_pickup",
    "r6_bag_size",
}


def _candidate(cid: str, *, score: float | None = None, verdict: str = "MAYBE"):
    ordinal = int(cid)
    return SimpleNamespace(
        courier_id=cid,
        name=f"Courier-{cid}",
        score=float(ordinal if score is None else score),
        feasibility_verdict=verdict,
        feasibility_reason="test",
        best_effort=False,
        plan=None,
        metrics={
            "pos_source": "gps" if ordinal % 2 else "bag_tail",
            "km_to_pickup": round(ordinal / 10.0, 1),
            "r6_bag_size": ordinal % 4,
        },
    )


def _result(best, top, full) -> PipelineResult:
    return PipelineResult(
        order_id="choice-order",
        verdict="PROPOSE",
        reason="test",
        best=best,
        candidates=top,
        full_pool_candidates=full,
        pickup_ready_at=None,
        restaurant="Test",
    )


def test_full_pool_flag_on_differs_from_off_and_keeps_all_candidates(monkeypatch):
    full = [_candidate(str(cid)) for cid in range(100, 120)]
    # Solo/OBJM-like best is a distinct final representation of a courier whose
    # rejected twin remains outside top-16 in the raw full pool.
    best = _candidate("118", score=999.0)
    result = _result(best, full[:16], full)

    monkeypatch.setattr(C, "ENABLE_FULL_CHOICE_SET_LOG", False)
    off = SD._serialize_result(result, "shadow-event-choice", 1.0)
    assert "full_pool_compact" not in off

    monkeypatch.setattr(C, "ENABLE_FULL_CHOICE_SET_LOG", True)
    on = SD._serialize_result(result, "shadow-event-choice", 1.0)
    compact = on["full_pool_compact"]

    assert len(compact) == 20
    assert compact[0]["cid"] == "118"
    assert compact[0]["score"] == 999.0
    assert len({str(row["cid"]) for row in compact}) == 20
    assert {str(row["cid"]) for row in compact} == {
        str(cid) for cid in range(100, 120)
    }
    assert all(set(row) == COMPACT_KEYS for row in compact)
    assert len(json.dumps(compact, ensure_ascii=False)) < 4000


def test_compact_projection_is_shared_by_serializer_locations_a_and_b(monkeypatch):
    candidate = _candidate("123", score=45.5, verdict="NO")
    result = _result(candidate, [candidate], [candidate])
    monkeypatch.setattr(C, "ENABLE_FULL_CHOICE_SET_LOG", True)

    compact = SD._serialize_candidate_compact(candidate)
    location_a = SD._serialize_candidate(candidate)
    location_b = SD._serialize_result(result, "shadow-event-ab", 1.0)["best"]

    for location in (location_a, location_b):
        assert location["courier_id"] == compact["cid"]
        assert location["score"] == compact["score"]
        assert location["feasibility"] == compact["feasibility_verdict"]
        assert location["pos_source"] == compact["pos_source"]
        assert location["km_to_pickup"] == compact["km_to_pickup"]
        assert location["r6_bag_size"] == compact["r6_bag_size"]


def test_every_selection_result_threads_the_full_pool():
    """Mutation guard: żaden early return w selection nie może zgubić pola."""
    source_path = Path(__file__).parents[1] / "core" / "selection.py"
    tree = ast.parse(source_path.read_text(encoding="utf-8"))
    calls = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "PipelineResult"
    ]
    assert calls
    missing = [
        call.lineno
        for call in calls
        if "full_pool_candidates" not in {kw.arg for kw in call.keywords}
    ]
    assert not missing, f"PipelineResult bez pełnej puli, linie: {missing}"


def test_pending_proposals_roundtrip_tolerates_additive_full_pool():
    record = {
        "event_id": "shadow-event-pending",
        "best": {"courier_id": "200", "score": 10.0},
        "full_pool_compact": [
            {
                "cid": "200",
                "score": 10.0,
                "feasibility_verdict": "MAYBE",
                "pos_source": "gps",
                "km_to_pickup": 1.0,
                "r6_bag_size": 0,
            }
        ],
    }
    entry = PPS.build_entry(
        record, datetime(2026, 7, 21, 8, 0, tzinfo=timezone.utc)
    )
    assert entry["decision_record"] == record


def test_learning_join_flag_on_differs_from_e1_off_contract():
    decision = {"event_id": "shadow-event-join"}

    off = PW._learning_record_identity_fields(
        decision,
        "assignment-event",
        decision_join_enabled=False,
    )
    on = PW._learning_record_identity_fields(
        decision,
        "assignment-event",
        decision_join_enabled=True,
    )

    assert off == {"lifecycle_event_id": "assignment-event"}
    assert on == {
        "lifecycle_event_id": "shadow-event-join",
        "assignment_lifecycle_event_id": "assignment-event",
    }


def _pending_record(oid: str, proposed: str, event_id: str) -> dict:
    now = datetime.now(timezone.utc)
    decision = {
        "event_id": event_id,
        "order_id": oid,
        "ts": now.isoformat(),
        "verdict": "PROPOSE",
        "restaurant": "Test",
        "best": {"courier_id": proposed, "score": 88.0},
        # Additive Part A field must not disturb the Part B reader.
        "full_pool_compact": [],
    }
    return {
        "sent_at": now.isoformat(),
        "decision_record": decision,
    }


def _learning_paths(tmp_path, monkeypatch, *, oid: str, proposed: str, event_id: str):
    pending = tmp_path / "pending.json"
    learning = tmp_path / "learning.jsonl"
    pending.write_text(
        json.dumps({oid: _pending_record(oid, proposed, event_id)}),
        encoding="utf-8",
    )
    monkeypatch.setattr(PW, "_PENDING_PROPOSALS_PATH", str(pending))
    monkeypatch.setattr(PW, "_LEARNING_LOG_PATH", str(learning))
    monkeypatch.setattr(C, "ENABLE_LEARNING_LOG_DECISION_JOIN", True)
    return learning


def test_panel_agree_carries_shadow_event_id_at_writer(tmp_path, monkeypatch):
    learning = _learning_paths(
        tmp_path,
        monkeypatch,
        oid="agree-order",
        proposed="200",
        event_id="shadow-event-agree",
    )

    PW._check_panel_agree("agree-order", "200", "panel_diff")

    record = json.loads(learning.read_text(encoding="utf-8"))
    assert record["action"] == "PANEL_AGREE"
    assert record["lifecycle_event_id"] == "shadow-event-agree"


def test_panel_override_carries_shadow_event_id_at_writer(tmp_path, monkeypatch):
    learning = _learning_paths(
        tmp_path,
        monkeypatch,
        oid="override-order",
        proposed="200",
        event_id="shadow-event-override",
    )

    PW._check_panel_override("override-order", "999", "panel_diff")

    record = json.loads(learning.read_text(encoding="utf-8"))
    assert record["action"] == "PANEL_OVERRIDE"
    assert record["lifecycle_event_id"] == "shadow-event-override"


def test_durable_join_dedupes_on_assignment_id(monkeypatch, tmp_path):
    """Zmiana publicznego joinu nie może osłabić retry outboxa."""
    from dispatch_v2 import event_bus as EB
    from dispatch_v2.core import jsonl_appender as JA

    record = {
        "action": "PANEL_AGREE",
        "lifecycle_event_id": "shadow-event-durable",
        "assignment_lifecycle_event_id": "assignment-event-durable",
    }
    projection = {
        "effect_id": "assignment-event-durable:panel_assignment_learning",
        "record": record,
        "projected_at": None,
    }
    captured = {}

    monkeypatch.setattr(
        EB,
        "prepare_durable_learning_projection",
        lambda *_args, **_kwargs: (projection, False),
    )
    monkeypatch.setattr(EB, "mark_durable_learning_projected", lambda _eid: True)
    monkeypatch.setattr(PW, "_durable_downstream_attempt", lambda *_a, **_k: 2)
    monkeypatch.setattr(PW, "_LEARNING_LOG_PATH", str(tmp_path / "learning.jsonl"))

    def capture_once(_path, _record, **kwargs):
        captured.update(kwargs)
        return True

    monkeypatch.setattr(JA, "append_jsonl_once", capture_once)
    PW._append_learning_record(
        record,
        "assignment-event-durable",
        _raise_on_error=True,
    )

    assert captured["dedupe_key"] == "assignment_lifecycle_event_id"
    assert captured["dedupe_value"] == "assignment-event-durable"
    assert captured["scan_rotated"] is True


def test_old_learning_record_without_join_key_still_reads(tmp_path):
    path = tmp_path / "learning.jsonl"
    ts = datetime(2026, 7, 21, 8, 0, tzinfo=timezone.utc)
    old = {
        "ts": ts.isoformat(),
        "order_id": "old-order",
        "action": "PANEL_OVERRIDE",
        "proposed_courier_id": "200",
        "actual_courier_id": "999",
    }
    path.write_text(json.dumps(old) + "\n", encoding="utf-8")

    rows = list(
        DB._iter_learning_in_range(
            str(path), ts - timedelta(minutes=1), ts + timedelta(minutes=1)
        )
    )
    assert rows == [old]
