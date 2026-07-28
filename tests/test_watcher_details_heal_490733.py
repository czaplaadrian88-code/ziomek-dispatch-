"""Regression for missing gastro details after cold-start catch-up (case 490733).

The cold-start path can legitimately create an assigned order before NEW_ORDER
has persisted its normalized addresses.  The watcher must later enrich that
existing record through the durable event boundary without replaying lifecycle
state or duplicating the NEW_ORDER normalization/geocoding policy.
"""
from __future__ import annotations

import inspect
import json
import sqlite3
from pathlib import Path

import pytest

from dispatch_v2 import panel_watcher as PW
from dispatch_v2 import event_bus as EB
from dispatch_v2 import state_machine as SM
from dispatch_v2.durable_event_apply import DurableApplyOutcome


def _parsed(*order_ids: str) -> dict:
    return {
        "order_ids": list(order_ids),
        "assigned_ids": set(order_ids),
        "unassigned_ids": [],
        "rest_names": {oid: f"Restaurant {oid}" for oid in order_ids},
        "courier_packs": {},
        "courier_load": {},
        "html_times": {},
        "closed_ids": set(),
        "pickup_addresses": {},
        "delivery_addresses": {},
    }


def _minimal_assigned(oid: str, *, cid: str = "400") -> dict:
    return {
        "order_id": oid,
        "status": "assigned",
        "commitment_level": "assigned",
        "courier_id": cid,
        "assigned_at": "2026-07-28T10:00:00+00:00",
        "first_seen": "2026-07-28T09:59:00+00:00",
        "source": "cold_start_scan",
        "last_lifecycle_event_id": f"{oid}_assignment_marker",
        "history": [{"at": "before", "event": "COURIER_ASSIGNED", "status": "assigned"}],
        "updated_at": "2026-07-28T10:00:01+00:00",
    }


def _detail_payload(oid: str) -> dict:
    return {
        "restaurant": f"Restaurant {oid}",
        "pickup_address": "Lipowa 1",
        "pickup_city": "Białystok",
        "delivery_address": "Sienkiewicza 2",
        "delivery_city": "Białystok",
        "pickup_at_warsaw": "2026-07-28T13:15:00+02:00",
        "prep_minutes": 20,
        "order_type": "normal",
        "status_id": 3,
        "first_seen": "MUST_NOT_REPLACE_FIRST_SEEN",
        "address_id": "77",
        "pickup_coords": [53.132, 23.161],
        "delivery_coords": [53.133, 23.171],
        "czas_kuriera_warsaw": "2026-07-28T13:20:00+02:00",
        "czas_kuriera_hhmm": "13:20",
        "uwagi": "bez sztućców",
        "uwagi_pickup_parsed": {"street": "Lipowa", "number": "1"},
        "decision_deadline": "2026-07-28T11:05:00+00:00",
        "zmiana_czasu_odbioru": False,
        "created_at_utc": "2026-07-28T10:55:00+00:00",
    }


@pytest.fixture(autouse=True)
def _reset_heal_retry_state(monkeypatch):
    PW._DETAILS_HEAL_RETRY_STATE.clear()
    monkeypatch.setattr(PW, "WATCHER_DETAILS_HEAL_MAX_ATTEMPTS", 5)


def _install_state(tmp_path: Path, monkeypatch, order: dict) -> Path:
    path = tmp_path / "orders_state.json"
    path.write_text(
        json.dumps({order["order_id"]: order}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(SM, "_state_path", lambda: str(path))
    return path


def test_cold_start_minimal_heals_once_and_preserves_lifecycle(tmp_path, monkeypatch):
    """Negative oracle: case 490733 must not remain permanently detail-less."""
    oid = "490733"
    before = _minimal_assigned(oid)
    state_path = _install_state(tmp_path, monkeypatch, before)
    payload = _detail_payload(oid)
    emitted = []

    monkeypatch.setattr(
        PW,
        "_build_order_details_payload",
        lambda *_args, **_kwargs: ({"order_type": "normal"}, dict(payload)),
    )

    def apply_real(
        event_type,
        *,
        order_id,
        courier_id=None,
        payload=None,
        state_payload=None,
        event_id,
        audit=False,
        old_plan_release_authorized=None,
    ):
        assert audit is True
        state_event = {
            "event_type": event_type,
            "order_id": order_id,
            "courier_id": courier_id,
            "payload": payload if state_payload is None else state_payload,
            "event_id": event_id,
        }
        emitted.append(state_event)
        SM.update_from_event(state_event)
        return DurableApplyOutcome(
            event_id,
            event_id,
            True,
            True,
            True,
            False,
            state_event=state_event,
        )

    monkeypatch.setattr(PW, "_emit_and_apply_state", apply_real)
    stats = {}
    PW._heal_missing_order_details(
        _parsed(oid),
        {oid: before},
        lambda _zid: {"id": oid},
        stats,
        now_monotonic=100.0,
    )

    assert len(emitted) == 1
    event = emitted[0]
    assert event["event_type"] == "ORDER_DETAILS_ENRICHED"
    assert event["event_id"].startswith(f"{oid}_ORDER_DETAILS_ENRICHED_")
    assert set(event["payload"]) == set(SM.ORDER_DETAILS_ENRICHMENT_FIELDS)
    assert "first_seen" not in event["payload"]
    assert "czas_kuriera_warsaw" not in event["payload"]
    assert stats == {"details_heal_attempted": 1, "details_healed": 1}

    after = json.loads(state_path.read_text(encoding="utf-8"))[oid]
    protected = (
        "status",
        "commitment_level",
        "courier_id",
        "assigned_at",
        "first_seen",
        "source",
        "last_lifecycle_event_id",
    )
    assert {key: after[key] for key in protected} == {
        key: before[key] for key in protected
    }
    assert "last_lifecycle_event_id_order_details_enriched" not in after
    assert after["restaurant"] == payload["restaurant"]
    assert after["pickup_address"] == payload["pickup_address"]
    assert after["delivery_address"] == payload["delivery_address"]
    assert after["pickup_coords"] == payload["pickup_coords"]
    assert after["delivery_coords"] == payload["delivery_coords"]

    bytes_after_first_apply = state_path.read_bytes()
    assert SM.update_from_event(event) == after
    assert state_path.read_bytes() == bytes_after_first_apply

    PW._heal_missing_order_details(
        _parsed(oid),
        SM.get_all(),
        lambda _zid: pytest.fail("complete order must not be fetched again"),
        stats := {},
        now_monotonic=200.0,
    )
    assert stats == {}
    assert len(emitted) == 1


def test_merge_only_event_never_overwrites_existing_detail(tmp_path, monkeypatch):
    oid = "490733"
    existing = {
        **_minimal_assigned(oid),
        "restaurant": "Operator-corrected restaurant",
    }
    _install_state(tmp_path, monkeypatch, existing)
    event = {
        "event_type": "ORDER_DETAILS_ENRICHED",
        "event_id": f"{oid}_ORDER_DETAILS_ENRICHED_existing-wins",
        "order_id": oid,
        "payload": _detail_payload(oid),
    }

    updated = SM.update_from_event(event)

    assert updated["restaurant"] == "Operator-corrected restaurant"
    assert updated["pickup_address"] == "Lipowa 1"
    assert updated["delivery_address"] == "Sienkiewicza 2"
    assert updated["status"] == "assigned"
    assert updated["courier_id"] == "400"


def test_real_durable_audit_event_is_idempotent(tmp_path, monkeypatch):
    """The real emit -> outbox -> state boundary closes once for one payload hash."""
    oid = "490733"
    state_path = _install_state(tmp_path, monkeypatch, _minimal_assigned(oid))
    events_db = tmp_path / "events.db"
    monkeypatch.setattr(EB, "_db_path", lambda: str(events_db))
    monkeypatch.setattr(EB, "_audit_log_initialized", False)
    monkeypatch.setattr(EB, "_state_apply_outbox_initialized", False)
    monkeypatch.setattr(EB, "_state_apply_outbox_db_path", None)
    monkeypatch.setattr(PW, "emit_audit", EB.emit_audit)
    monkeypatch.setattr(PW, "update_from_event", SM.update_from_event)
    monkeypatch.setattr(PW.lifecycle_downstream, "apply", lambda _event: None)

    payload = {
        key: value
        for key, value in _detail_payload(oid).items()
        if key in SM.ORDER_DETAILS_ENRICHMENT_FIELDS
        and value not in (None, "", [], {})
    }
    semantic_id = f"{oid}_ORDER_DETAILS_ENRICHED_fixturehash"
    first = PW._emit_and_apply_state(
        "ORDER_DETAILS_ENRICHED",
        order_id=oid,
        payload=payload,
        event_id=semantic_id,
        audit=True,
    )
    assert first.event_created is True
    assert first.state_ready is True
    assert first.state_transitioned is True
    bytes_after_first = state_path.read_bytes()

    second = PW._emit_and_apply_state(
        "ORDER_DETAILS_ENRICHED",
        order_id=oid,
        payload=payload,
        event_id=semantic_id,
        audit=True,
    )
    assert second.event_created is False
    assert second.state_ready is True
    assert second.state_transitioned is False
    assert second.event_id == first.event_id
    assert state_path.read_bytes() == bytes_after_first

    with sqlite3.connect(events_db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM audit_log "
            "WHERE event_type='ORDER_DETAILS_ENRICHED'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT state_status, downstream_status FROM state_apply_outbox"
        ).fetchone() == ("applied", "applied")


def test_none_response_retries_with_per_zid_backoff_and_attempt_cap(monkeypatch):
    oid = "490733"
    current = {oid: _minimal_assigned(oid)}
    fetches = []
    emits = []
    monkeypatch.setattr(PW, "WATCHER_DETAILS_HEAL_MAX_ATTEMPTS", 2)
    monkeypatch.setattr(PW, "_emit_and_apply_state", lambda *a, **kw: emits.append(kw))

    def unavailable(zid):
        fetches.append(zid)
        return None  # panel_client maps HTTP 419/fetch failure to None

    first = {}
    PW._heal_missing_order_details(
        _parsed(oid), current, unavailable, first, now_monotonic=100.0
    )
    assert first == {"details_heal_attempted": 1, "details_heal_retry": 1}
    assert fetches == [oid]
    assert emits == []

    in_backoff = {}
    PW._heal_missing_order_details(
        _parsed(oid), current, unavailable, in_backoff, now_monotonic=119.9
    )
    assert in_backoff == {"details_heal_backoff": 1}
    assert fetches == [oid]

    second = {}
    PW._heal_missing_order_details(
        _parsed(oid), current, unavailable, second, now_monotonic=120.0
    )
    assert second == {"details_heal_attempted": 1, "details_heal_retry": 1}
    assert fetches == [oid, oid]

    exhausted = {}
    PW._heal_missing_order_details(
        _parsed(oid), current, unavailable, exhausted, now_monotonic=10_000.0
    )
    assert exhausted == {"details_heal_exhausted": 1}
    assert fetches == [oid, oid]
    assert emits == []


def test_heal_budget_is_one_fetch_per_tick(monkeypatch):
    current = {
        "490733": _minimal_assigned("490733"),
        "490734": _minimal_assigned("490734", cid="401"),
    }
    fetched = []
    emitted = []
    monkeypatch.setattr(
        PW,
        "_build_order_details_payload",
        lambda zid, *_args, **_kwargs: (
            {"order_type": "normal"},
            _detail_payload(zid),
        ),
    )
    monkeypatch.setattr(
        PW,
        "_emit_and_apply_state",
        lambda event_type, **kwargs: (
            emitted.append((event_type, kwargs))
            or DurableApplyOutcome(
                kwargs["event_id"],
                kwargs["event_id"],
                True,
                True,
                True,
                False,
            )
        ),
    )

    PW._heal_missing_order_details(
        _parsed("490733", "490734"),
        current,
        lambda zid: fetched.append(zid) or {"id": zid},
        {},
        now_monotonic=100.0,
    )

    assert fetched == ["490733"]
    assert [item[1]["order_id"] for item in emitted] == ["490733"]


def test_unknown_visible_order_stays_exclusively_on_new_path():
    """HEAL is only for a known zid; a fresh zid must not be double-built."""
    stats = {}
    PW._heal_missing_order_details(
        _parsed("490736"),
        {},
        lambda _zid: pytest.fail("unknown zid belongs to NEW_ORDER path"),
        stats,
        now_monotonic=100.0,
    )
    assert stats == {}


def test_new_builder_preserves_frozen_non_firmowe_payload(monkeypatch):
    """Parity oracle for the pre-refactor inline NEW_ORDER builder."""
    oid = "490735"
    norm = {
        "restaurant": "Kuchnia Testowa",
        "pickup_address": "Lipowa 1",
        "pickup_city": "Białystok",
        "delivery_address": "Sienkiewicza 2",
        "delivery_city": "Białystok",
        "pickup_at_warsaw": "2026-07-28T13:15:00+02:00",
        "prep_minutes": 20,
        "order_type": "normal",
        "status_id": 3,
        "address_id": 999999,
        "czas_kuriera_warsaw": None,
        "czas_kuriera_hhmm": None,
        "uwagi": None,
        "decision_deadline": "2026-07-28T11:05:00+00:00",
        "zmiana_czasu_odbioru": False,
        "created_at_utc": "2026-07-28T10:55:00+00:00",
    }
    monkeypatch.setattr(PW, "normalize_order", lambda *_args: dict(norm))
    monkeypatch.setattr(
        PW,
        "_resolve_pickup_coords",
        lambda *_args, **_kwargs: ((53.132, 23.161), "fixture", 0.0),
    )
    monkeypatch.setattr(PW, "geocode", lambda *_args, **_kwargs: (53.133, 23.171))
    monkeypatch.setattr(PW, "now_iso", lambda: "2026-07-28T11:00:00+00:00")

    built_norm, payload = PW._build_order_details_payload(
        oid, {"id": oid}, "HTML fallback"
    )

    assert built_norm == norm
    assert payload == {
        "restaurant": "Kuchnia Testowa",
        "pickup_address": "Lipowa 1",
        "pickup_city": "Białystok",
        "delivery_address": "Sienkiewicza 2",
        "delivery_city": "Białystok",
        "pickup_at_warsaw": "2026-07-28T13:15:00+02:00",
        "prep_minutes": 20,
        "order_type": "normal",
        "status_id": 3,
        "first_seen": "2026-07-28T11:00:00+00:00",
        "address_id": "999999",
        "pickup_coords": [53.132, 23.161],
        "delivery_coords": [53.133, 23.171],
        "czas_kuriera_warsaw": None,
        "czas_kuriera_hhmm": None,
        "uwagi": None,
        "uwagi_pickup_parsed": None,
        "decision_deadline": "2026-07-28T11:05:00+00:00",
        "zmiana_czasu_odbioru": False,
        "created_at_utc": "2026-07-28T10:55:00+00:00",
    }


def test_mutation_guard_heal_branch_and_single_builder():
    """Deleting the heal branch or reintroducing a second builder must go RED."""
    diff_source = inspect.getsource(PW._diff_and_emit)
    builder_source = inspect.getsource(PW._build_order_details_payload)
    module_source = Path(PW.__file__).read_text(encoding="utf-8")

    assert "_heal_missing_order_details(" in diff_source
    assert "_build_order_details_payload(" in diff_source
    assert "normalize_order(" in builder_source
    assert module_source.count("normalize_order(raw,") == 1
