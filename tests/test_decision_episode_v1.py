"""Hermetyczne testy decision_episode_v1 — wylacznie dane syntetyczne."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from dispatch_v2.tools import decision_episode_v1 as de


POST = "2026-07-20T10:00:00Z"
PRE = "2026-07-18T10:00:00Z"


def _candidate(cid: str, name: str, score: float) -> dict:
    return {
        "courier_id": cid,
        "name": name,
        "score": score,
        "travel_min_cal": 7.5,
        "objm_r6_breach_max_min": 0.0,
        "plan": {
            "pickup_at": "2026-07-20T10:10:00Z",
            "total_duration_min": 25.0,
            "strategy": "synthetic",
            "predicted_delivered_at": {"O1": "2026-07-20T10:25:00Z"},
            "sequence": [{"address": "NIE MOZE TRAFIC DO OUTPUTU"}],
        },
        "bag_context": {"customer_phone": "NIE MOZE TRAFIC DO OUTPUTU"},
    }


def _shadow(
    *,
    ts: str = "2026-07-20T09:59:50Z",
    event_id: str = "O1_NEW_ORDER_first",
    order_id: str = "O1",
    best: dict | None = None,
    alternatives: list[dict] | None = None,
) -> dict:
    return {
        "ts": ts,
        "event_id": event_id,
        "order_id": order_id,
        "best": best or _candidate("C1", "Kurier Jeden", 10.0),
        "alternatives": alternatives or [_candidate("C2", "Kurier Dwa", 9.0)],
        "pool_total_count": 2,
        "pool_feasible_count": 2,
    }


def _learning(
    *,
    ts: str = POST,
    action: str = "PANEL_OVERRIDE",
    actual: str = "C2",
    panel_source: str = "panel_diff",
    lifecycle_event_id: str | None = "assign-O1-C2",
    decision: dict | None = None,
) -> dict:
    row = {
        "ts": ts,
        "action": action,
        "order_id": "O1",
        "proposed_courier_id": "C1",
        "actual_courier_id": actual,
        "panel_source": panel_source,
    }
    if lifecycle_event_id is not None:
        row["lifecycle_event_id"] = lifecycle_event_id
    if decision is not None:
        row["decision"] = decision
    return row


def _empty_events() -> dict:
    return {"assignments": [], "new_orders": [], "picked": [], "delivered": []}


def _assignment(
    *,
    ts: str = POST,
    event_id: str = "assign-O1-C2",
    courier_id: str = "C2",
    previous_cid: str | None = None,
) -> dict:
    payload = {}
    if previous_cid is not None:
        payload["previous_cid"] = previous_cid
    return {
        "event_id": event_id,
        "event_type": "COURIER_ASSIGNED",
        "order_id": "O1",
        "courier_id": courier_id,
        "payload": payload,
        "ts": de.parse_timestamp(ts),
    }


def _extract(
    learning: list[dict],
    shadows: list[dict],
    *,
    audit: list[dict] | None = None,
    assignments: list[dict] | None = None,
    outcomes: list[dict] | None = None,
    gps: list[dict] | None = None,
    picked: list[dict] | None = None,
    delivered: list[dict] | None = None,
) -> tuple[list[dict], dict]:
    events = _empty_events()
    events["assignments"] = assignments or []
    events["picked"] = picked or []
    events["delivered"] = delivered or []
    events["new_orders"] = [
        {
            "event_id": "O2_NEW_ORDER_first",
            "order_id": "O2",
            "ts": de.parse_timestamp("2026-07-20T10:05:00Z"),
        }
    ]
    return de.extract_decision_episodes(
        learning_records=learning,
        shadow_records=shadows,
        audit_records=audit or [],
        events=events,
        outcome_records=outcomes or [],
        gps_records=gps or [],
        restaurant_dwell={},
        courier_truth={},
    )


def test_direct_exact_episode_has_attested_actor_and_separate_proxies():
    shadow = _shadow()
    learning = _learning(decision=shadow)
    audit = [{
        "ts": POST,
        "mode": "live",
        "kind": "assign",
        "actor": "operator@nadajesz.pl",
        "order_id": "O1",
        "courier": "Kurier Dwa",
    }]
    outcomes = [{
        "order_id": "O1",
        "actual_cid": "C2",
        "picked_up_at": "2026-07-20T10:10:00Z",
        "delivered_at": "2026-07-20T10:30:00Z",
        "written_at": "2026-07-20T10:31:00Z",
    }]
    gps = [{
        "order_id": "O1",
        "courier_id": "C2",
        "physical_delivered_at": "2026-07-20T10:28:00Z",
        "button_delivered_at": "2026-07-20T10:30:00Z",
    }]
    picked = [{
        "event_id": "pickup-O1-C2",
        "event_type": "COURIER_PICKED_UP",
        "order_id": "O1",
        "courier_id": "C2",
        "payload": {},
        "ts": de.parse_timestamp("2026-07-20T10:10:00Z"),
    }]
    delivered = [{
        "event_id": "delivery-O1-C2",
        "event_type": "COURIER_DELIVERED",
        "order_id": "O1",
        "courier_id": "C2",
        "payload": {},
        "ts": de.parse_timestamp("2026-07-20T10:30:00Z"),
    }]

    episodes, _ = _extract(
        [learning],
        [shadow],
        audit=audit,
        assignments=[_assignment()],
        outcomes=outcomes,
        gps=gps,
        picked=picked,
        delivered=delivered,
    )
    episode = episodes[0]

    assert episode["category"] == "FIRST_ASSIGNMENT"
    assert episode["first_choice_eligible"] is True
    assert episode["decision_key_source"] == "lifecycle_event_id"
    assert episode["joins"]["assignment"]["method"] == "lifecycle_event_id"
    assert episode["joins"]["shadow"]["method"] == "learning.decision"
    assert episode["actor"] == "ATTESTED_CONSOLE"
    assert episode["actor_provenance"] == "ACTOR_ATTESTED_CONSOLE"
    assert episode["actor_id"].startswith("actor_sha256:")
    assert episode["recorded_pool"]["human_in_recorded_pool"] is True
    assert episode["human_candidate"]["courier_id"] == "C2"
    assert episode["outcomes"]["delivery_arrival_at"] == "2026-07-20T10:28:00Z"
    assert episode["outcomes"]["status_delivered_at"] == "2026-07-20T10:30:00Z"
    assert episode["outcomes"]["status_delivered_source"] == "events.db"
    assert episode["outcomes"]["r6_physical_possession_to_handoff_min"] is None
    assert episode["outcomes"]["truth_state"] == "HANDOFF_UNBOUND"
    assert episode["outcomes"]["windows"]["plus_15m"][
        "factual_proposed_courier_first_assignment_count"
    ] == 0
    assert episode["outcomes"]["windows"]["plus_15m"]["truth_class"] == "OBSERVED"
    assert episode["truth_class"] == "OBSERVED"
    assert episode["comparison_truth_class"] == "UNIDENTIFIABLE"
    assert "ACTOR_UNKNOWN" not in episode["missing_reasons"]
    assert "JOIN_AMBIGUOUS" not in episode["missing_reasons"]
    assert "OUTCOME_PROXY_ONLY" in episode["missing_reasons"]
    serialized = de.canonical_json(episode)
    assert "operator@nadajesz.pl" not in serialized
    assert "customer_phone" not in serialized
    assert "address" not in serialized


def test_pre_a8_reassign_outside_pool_is_hold_without_imputation():
    proposal_pre_cutoff = "2026-07-19T23:39:20Z"
    learning_post_cutoff = "2026-07-19T23:39:22Z"
    shadow = _shadow(ts=proposal_pre_cutoff, alternatives=[])
    # Jawnie usuwamy alternatywy: H=C9 nie istnieje w zapisanej puli.
    shadow["alternatives"] = []
    learning = _learning(
        ts=learning_post_cutoff,
        actual="C9",
        panel_source="panel_reassign",
        lifecycle_event_id="assign-O1-C9",
        decision=shadow,
    )
    assignment = _assignment(
        ts=learning_post_cutoff,
        event_id="assign-O1-C9",
        courier_id="C9",
        previous_cid="C8",
    )

    episode = _extract([learning], [shadow], assignments=[assignment])[0][0]

    assert episode["category"] == "REASSIGN"
    assert episode["first_choice_eligible"] is False
    assert episode["human_candidate"] is None
    assert episode["recorded_pool"]["human_in_recorded_pool"] is False
    assert episode["analysis_state"] == "HOLD"
    assert "PRE_A8_CONTAMINATED" in episode["missing_reasons"]
    assert "OUT_OF_RECORDED_POOL" in episode["missing_reasons"]
    assert "ACTOR_UNKNOWN" in episode["missing_reasons"]
    # Cutoff dotyczy chwili propozycji, nie opoznionego zapisu learning.
    assert episode["proposal_at"] == proposal_pre_cutoff
    assert episode["learning_at"] == learning_post_cutoff


def test_fallback_never_searches_past_ambiguous_latest_shadow():
    learning = _learning(
        action="PANEL_AGREE",
        actual="C1",
        lifecycle_event_id=None,
        decision=None,
    )
    learning["latency_s"] = 20.0
    # Dwa rekordy w tym samym najnowszym czasie = JOIN_AMBIGUOUS. Starszy,
    # wygodny rekord nie moze zostac wybrany przez "szukaj az znajdziesz".
    shadows = [
        _shadow(ts="2026-07-20T09:50:00Z", event_id="old"),
        _shadow(ts="2026-07-20T09:59:30Z", event_id="tie-a"),
        _shadow(ts="2026-07-20T09:59:30Z", event_id="tie-b"),
    ]
    assignments = [
        _assignment(ts="2026-07-20T09:59:55Z", event_id="a", courier_id="C1"),
        _assignment(ts="2026-07-20T10:00:05Z", event_id="b", courier_id="C1"),
    ]

    episode = _extract([learning], shadows, assignments=assignments)[0][0]

    assert episode["joins"]["assignment"]["status"] == "AMBIGUOUS"
    assert episode["joins"]["assignment"]["match_count"] == 2
    assert episode["joins"]["shadow"]["status"] == "AMBIGUOUS"
    assert episode["joins"]["shadow"]["match_count"] == 2
    assert episode["shadow_event_id"] is None
    assert episode["proposal_at"] == "2026-07-20T09:59:40Z"
    assert episode["human_candidate"] is None
    assert "JOIN_AMBIGUOUS" in episode["missing_reasons"]
    # Brak unikalnego shadow nie jest falszywie klasyfikowany jako H poza pula.
    assert "OUT_OF_RECORDED_POOL" not in episode["missing_reasons"]


def test_audit_legacy_normalization_is_narrow_and_test_actor_is_filtered():
    base = {
        "ts": POST,
        "mode": "live",
        "kind": None,
        "actor": "test@nadajesz.pl",
        "order_id": "O1",
        "courier": "Kurier Dwa",
        "command": "python3 /srv/gastro_assign.py --synthetic",
        "ok": True,
        "rc": 0,
    }
    assert de._effective_audit_assign(base) == "legacy_gastro_assign_signature"
    assert de._actor_status(base["actor"]) == ("filtered", None)

    for mutation in (
        {"mode": "shadow"},
        {"kind": "edit"},
        {"command": "python3 /srv/gastro_edit.py --synthetic"},
        {"ok": False, "rc": 1},
        {"command": None},
    ):
        record = dict(base)
        record.update(mutation)
        assert de._effective_audit_assign(record) is None

    # Sam mode=live bez pelnego podpisu legacy nigdy nie wystarcza.
    assert de._effective_audit_assign({"mode": "live"}) is None


def test_reassign_ignores_duplicate_same_courier_and_overlap_groups_by_z():
    learning = _learning(decision=_shadow())
    same_courier_duplicate = _assignment(
        ts="2026-07-20T09:59:59Z", event_id="duplicate", courier_id="C2"
    )
    episode = _extract(
        [learning],
        [_shadow()],
        assignments=[same_courier_duplicate, _assignment()],
    )[0][0]
    assert episode["category"] == "FIRST_ASSIGNMENT"

    rows = [
        {
            "episode_id": "a",
            "proposed_courier_id": "Z",
            "actual_courier_id": "H1",
            "assignment_at": "2026-07-20T10:00:00Z",
            "outcomes": {"overlap_group_id": None},
        },
        {
            "episode_id": "b",
            "proposed_courier_id": "Z",
            "actual_courier_id": "H2",
            "assignment_at": "2026-07-20T10:30:00Z",
            "outcomes": {"overlap_group_id": None},
        },
    ]
    de._assign_overlap_groups(rows)
    assert rows[0]["outcomes"]["overlap_group_id"] == rows[1]["outcomes"]["overlap_group_id"]


def test_colliding_shadow_key_is_explicitly_ambiguous_not_synthetic_lifecycle():
    shadow = _shadow()
    first = _learning(lifecycle_event_id=None, decision=shadow)
    second = _learning(
        ts="2026-07-20T10:00:10Z",
        action="PANEL_AGREE",
        actual="C1",
        lifecycle_event_id=None,
        decision=shadow,
    )
    episodes, technical = _extract(
        [first, second],
        [shadow],
        assignments=[
            _assignment(event_id="first"),
            _assignment(
                ts="2026-07-20T10:00:10Z", event_id="second", courier_id="C1"
            ),
        ],
    )

    assert len({row["episode_id"] for row in episodes}) == 2
    assert len({row["decision_key"] for row in episodes}) == 1
    assert technical["decision_key_collision_groups"] == 1
    assert technical["decision_key_collision_rows"] == 2
    assert all(
        row["joins"]["lifecycle_key"]["status"] == "AMBIGUOUS"
        and "JOIN_AMBIGUOUS" in row["missing_reasons"]
        for row in episodes
    )


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(
        "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows),
        encoding="utf-8",
    )


def _write_events_db(path: Path) -> None:
    connection = sqlite3.connect(path)
    connection.executescript(
        """
        CREATE TABLE audit_log (
            event_id TEXT, event_type TEXT, order_id TEXT, courier_id TEXT,
            payload TEXT, created_at TEXT
        );
        CREATE TABLE events (
            event_id TEXT, event_type TEXT, order_id TEXT, courier_id TEXT,
            payload TEXT, created_at TEXT, processed_at TEXT, status TEXT,
            attempt_count INTEGER, last_error TEXT, next_attempt_at TEXT,
            last_failed_at TEXT, dead_lettered_at TEXT, replay_count INTEGER,
            last_replayed_at TEXT, last_replay_reason TEXT
        );
        """
    )
    connection.execute(
        "INSERT INTO audit_log VALUES (?,?,?,?,?,?)",
        ("assign-O1-C2", "COURIER_ASSIGNED", "O1", "C2", "{}", POST),
    )
    connection.execute(
        "INSERT INTO events (event_id,event_type,order_id,created_at) VALUES (?,?,?,?)",
        ("O2_NEW_ORDER_first", "NEW_ORDER", "O2", "2026-07-20T10:05:00Z"),
    )
    connection.commit()
    connection.close()


def test_cli_is_rotation_aware_deterministic_and_writes_only_with_out(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
):
    shadow = _shadow()
    learning = _learning(decision=shadow)
    learning_path = tmp_path / "learning.jsonl"
    shadow_path = tmp_path / "shadow.jsonl"
    audit_path = tmp_path / "audit.jsonl"
    gps_path = tmp_path / "gps.jsonl"
    outcomes_path = tmp_path / "outcomes.jsonl"
    events_path = tmp_path / "events.db"
    # Rekord pre-A8 w rotacji dowodzi uzycia helpera .1.
    pre_shadow = _shadow(
        ts="2026-07-18T09:59:50Z", event_id="PRE_EVENT", order_id="PRE"
    )
    pre_learning = {
        "ts": PRE,
        "action": "PANEL_AGREE",
        "order_id": "PRE",
        "proposed_courier_id": "C1",
        "actual_courier_id": "C1",
        "decision": pre_shadow,
        "panel_source": "panel_diff",
    }
    _write_jsonl(Path(str(learning_path) + ".1"), [pre_learning])
    _write_jsonl(learning_path, [learning])
    _write_jsonl(Path(str(shadow_path) + ".1"), [pre_shadow])
    _write_jsonl(shadow_path, [shadow])
    _write_jsonl(audit_path, [])
    _write_jsonl(gps_path, [])
    _write_jsonl(outcomes_path, [])
    _write_events_db(events_path)

    argv = [
        "--learning", str(learning_path),
        "--shadow", str(shadow_path),
        "--audit", str(audit_path),
        "--events-db", str(events_path),
        "--gps", str(gps_path),
        "--outcomes", str(outcomes_path),
        "--restaurant-dwell", str(tmp_path / "missing-dwell.json"),
        "--courier-ground-truth", str(tmp_path / "missing-courier.json"),
        "--census-only",
    ]
    before = {path.name for path in tmp_path.iterdir()}
    assert de.main(argv) == 0
    first = capsys.readouterr().out
    assert de.main(argv) == 0
    second = capsys.readouterr().out
    after = {path.name for path in tmp_path.iterdir()}

    assert first == second
    assert before == after
    census = json.loads(first)
    assert census["cohorts"]["POST_A8"]["learning_actions_all"] == {
        "AGREE": 0,
        "OVERRIDE": 1,
        "total": 1,
    }
    assert census["cohorts"]["PRE_A8"]["learning_actions_all"]["AGREE"] == 1

    output = tmp_path / "explicit.json"
    assert de.main(argv + ["--out", str(output)]) == 0
    confirmation = json.loads(capsys.readouterr().out)
    assert output.is_file()
    assert confirmation["sha256"] == __import__("hashlib").sha256(
        output.read_bytes()
    ).hexdigest()
