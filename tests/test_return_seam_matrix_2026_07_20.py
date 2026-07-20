"""RETURN seam: panel snapshot release composed with durable legacy cleanup.

The test intentionally enters through ``panel_watcher._diff_and_emit`` and the
real ``_emit_and_apply_state``/outbox/lifecycle callback.  Only recanon itself
is represented by a recorder, so this panel/lifecycle test never imports the
solver (and therefore never needs OR-Tools).
"""

from __future__ import annotations

import copy
import json
import sqlite3
import sys
from types import ModuleType, SimpleNamespace

import pytest


ORDER_ID = "990001"
SNAPSHOT_CID = "100"
RAW_CID = "200"


def _parsed_without_order() -> dict:
    return {
        "order_ids": [],
        "assigned_ids": set(),
        "unassigned_ids": [],
        "rest_names": {},
        "courier_packs": {},
        "courier_load": {},
        "html_times": {},
        "closed_ids": set(),
        "pickup_addresses": {},
        "delivery_addresses": {},
    }


def _returned_raw(cid: str) -> dict:
    return {
        "id": int(ORDER_ID),
        "id_kurier": int(cid) if cid else None,
        "id_status_zamowienia": 9,
        "street": "Testowa",
        "nr_domu": "1",
        "czas_odbioru": "35",
        "czas_odbioru_timestamp": "2026-07-20 12:00:00",
        "created_at": "2026-07-20T10:00:00.000000Z",
        "address": {
            "id": 1,
            "name": "Test",
            "street": "Testowa",
            "city": "Bialystok",
        },
        "lokalizacja": {"id": 1, "name": "Bialystok"},
    }


def _plan(cid: str) -> dict:
    return {
        "courier_id": cid,
        "plan_version": 3,
        "invalidated_at": None,
        "stops": [
            {"order_id": ORDER_ID, "type": "dropoff"},
            {"order_id": f"keep-{cid}", "type": "dropoff"},
        ],
    }


@pytest.fixture(autouse=True)
def _block_real_telegram_sends():
    """This seam has no Telegram path; avoid its unrelated import-time I/O."""
    yield


@pytest.fixture
def isolated_return_seam(tmp_path, monkeypatch):
    """Real SQLite/state/plan stores, isolated exactly like the C3 fixture."""
    import dispatch_v2
    from dispatch_v2 import common as C

    # panel_watcher creates its production logger at import time.  Keep the
    # import hermetic even in a clone whose production log parent is read-only.
    real_setup_logger = C.setup_logger
    monkeypatch.setattr(
        C,
        "setup_logger",
        lambda name, log_file=None: real_setup_logger(name, None),
    )

    from dispatch_v2 import event_bus as EB
    from dispatch_v2 import panel_detail_prefetch as PDP
    from dispatch_v2 import panel_watcher as PW
    from dispatch_v2 import parse_continuity_guard as PCG
    from dispatch_v2 import plan_manager as PM
    from dispatch_v2 import state_machine as SM

    events_db = tmp_path / "events.db"
    state_path = tmp_path / "orders_state.json"
    plans_path = tmp_path / "courier_plans.json"

    with sqlite3.connect(events_db) as conn:
        conn.executescript(
            """
            CREATE TABLE events (
                event_id TEXT PRIMARY KEY,
                event_type TEXT NOT NULL,
                order_id TEXT,
                courier_id TEXT,
                payload TEXT,
                created_at TEXT NOT NULL,
                processed_at TEXT,
                status TEXT DEFAULT 'pending'
            );
            CREATE INDEX idx_events_status ON events(status);
            CREATE TABLE processed_events (
                event_id TEXT PRIMARY KEY,
                processed_at TEXT NOT NULL
            );
            """
        )

    snapshot = {
        ORDER_ID: {
            "order_id": ORDER_ID,
            "status": "assigned",
            "commitment_level": "assigned",
            "courier_id": SNAPSHOT_CID,
            "updated_at": "2026-07-20T10:00:00+00:00",
            "delivery_address": "Testowa 2",
            "delivery_coords": [53.1, 23.1],
            "history": [],
        }
    }
    state_path.write_text(json.dumps(snapshot), encoding="utf-8")
    plans_path.write_text(
        json.dumps(
            {
                SNAPSHOT_CID: _plan(SNAPSHOT_CID),
                RAW_CID: _plan(RAW_CID),
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(EB, "_db_path", lambda: str(events_db))
    monkeypatch.setattr(EB, "_audit_log_initialized", False)
    monkeypatch.setattr(EB, "_state_apply_outbox_initialized", False)
    monkeypatch.setattr(EB, "_state_apply_outbox_db_path", None)
    monkeypatch.setattr(SM, "_state_path", lambda: str(state_path))
    monkeypatch.setattr(PW, "emit", EB.emit)
    monkeypatch.setattr(PW, "emit_audit", EB.emit_audit)
    monkeypatch.setattr(PW, "update_from_event", SM.update_from_event)

    monkeypatch.setattr(PM, "PLANS_FILE", plans_path)
    monkeypatch.setattr(PM, "LOCK_FILE", tmp_path / "courier_plans.lock")
    monkeypatch.setattr(C, "ENABLE_SAVED_PLANS", True)

    # Hold the panel snapshot stale across retries, while the durable path uses
    # the real state store.  That is the production seam under examination.
    monkeypatch.setattr(PW, "state_get_all", lambda: copy.deepcopy(snapshot))
    monkeypatch.setattr(PW, "fetch_order_details", lambda *_a, **_kw: None)
    monkeypatch.setattr(PW, "touch_check_cursor", lambda *_a, **_kw: None)
    monkeypatch.setattr(PW, "_ignored_ids", set())
    monkeypatch.setattr(
        PW.durable_event_apply,
        "drain_pending",
        lambda **_kw: {
            "seen": 0,
            "state_ready": 0,
            "downstream": 0,
            "superseded": 0,
            "failed": 0,
        },
    )
    monkeypatch.setattr(
        PCG,
        "evaluate",
        lambda *_a, **_kw: {"freeze_new": False, "suspicious": False},
    )
    monkeypatch.setattr(
        PDP,
        "prefetch_details",
        lambda *_a, **_kw: ({}, {"prefetch_enabled": False}),
    )

    old_release = {"enabled": False}
    monkeypatch.setattr(
        PW,
        "decision_flag",
        lambda name: (
            old_release["enabled"]
            if name == "ENABLE_REASSIGN_OLD_PLAN_RELEASE"
            else False
        ),
    )
    monkeypatch.setattr(PW, "flag", lambda *_a, **_kw: False)
    monkeypatch.setattr(C, "flag", lambda *_a, **_kw: False)

    # _remove_stops_on_return stays real.  Its recanon import is replaced with
    # a tiny observer so importing this lifecycle-layer test cannot reach the
    # route solver or OR-Tools.
    recanons = []
    fake_plan_recheck = ModuleType("dispatch_v2.plan_recheck")

    def record_recanon(cid, *, reason, _raise_on_error=False):
        recanons.append((str(cid), reason, bool(_raise_on_error)))
        return True

    fake_plan_recheck.recanon_courier = record_recanon
    monkeypatch.setitem(sys.modules, "dispatch_v2.plan_recheck", fake_plan_recheck)
    monkeypatch.setattr(
        dispatch_v2, "plan_recheck", fake_plan_recheck, raising=False
    )

    remove_calls = []
    writes = []
    fail_remove_for = set()
    real_remove_stops = PM.remove_stops
    real_write_raw = PM._write_raw

    def record_remove_stops(cid, oid):
        cid = str(cid)
        oid = str(oid)
        remove_calls.append((cid, oid))
        if cid in fail_remove_for:
            raise RuntimeError("synthetic snapshot plan write failure")
        return real_remove_stops(cid, oid)

    def record_write_raw(data):
        writes.append(copy.deepcopy(data))
        return real_write_raw(data)

    monkeypatch.setattr(PM, "remove_stops", record_remove_stops)
    monkeypatch.setattr(PM, "_write_raw", record_write_raw)

    outcomes = []
    real_emit_and_apply = PW._emit_and_apply_state

    def record_emit_and_apply(*args, **kwargs):
        outcome = real_emit_and_apply(*args, **kwargs)
        outcomes.append(outcome)
        return outcome

    monkeypatch.setattr(PW, "_emit_and_apply_state", record_emit_and_apply)

    seam = SimpleNamespace(
        C=C,
        EB=EB,
        PM=PM,
        PW=PW,
        SM=SM,
        events_db=events_db,
        state_path=state_path,
        plans_path=plans_path,
        snapshot=snapshot,
        old_release=old_release,
        fail_remove_for=fail_remove_for,
        remove_calls=remove_calls,
        writes=writes,
        recanons=recanons,
        outcomes=outcomes,
    )

    def run(raw_cid: str):
        monkeypatch.setattr(
            PW,
            "fetch_order_details",
            lambda *_a, **_kw: _returned_raw(raw_cid),
        )
        return PW._diff_and_emit(_parsed_without_order(), csrf="test")

    seam.run = run
    seam.plans = lambda: json.loads(plans_path.read_text(encoding="utf-8"))
    seam.receipt = EB.get_state_apply_outbox
    return seam


@pytest.mark.parametrize(
    "case",
    [
        "flag_off_raw_diff",
        "flag_on_raw_same",
        "flag_on_raw_empty",
        "flag_on_raw_diff",
        "pending_retry_closed_duplicate",
        "snapshot_cleanup_gap",
    ],
)
def test_return_seam_matrix(case, isolated_return_seam, monkeypatch):
    seam = isolated_return_seam
    seam.old_release["enabled"] = case != "flag_off_raw_diff"

    if case == "flag_off_raw_diff":
        seam.run(RAW_CID)

        assert seam.outcomes[0].event_created is True
        assert seam.outcomes[0].downstream_executed is True
        assert seam.remove_calls == [(RAW_CID, ORDER_ID)]
        assert len(seam.writes) == 1
        assert [s["order_id"] for s in seam.plans()[RAW_CID]["stops"]] == [
            f"keep-{RAW_CID}"
        ]
        assert any(
            s["order_id"] == ORDER_ID
            for s in seam.plans()[SNAPSHOT_CID]["stops"]
        ), "OFF: snapshot-only cleanup must not run"
        assert seam.recanons == [(RAW_CID, "return", True)]

    elif case == "flag_on_raw_same":
        seam.run(SNAPSHOT_CID)

        assert seam.remove_calls == [
            (SNAPSHOT_CID, ORDER_ID),
            (SNAPSHOT_CID, ORDER_ID),
        ]
        assert len(seam.writes) == 1, "second remove_stops is a store no-op"
        assert [
            s["order_id"] for s in seam.plans()[SNAPSHOT_CID]["stops"]
        ] == [f"keep-{SNAPSHOT_CID}"]
        # Current policy: recanon runs twice.  Idempotence is deliberately at
        # plan_manager.remove_stops; the helper recanons after both calls.
        assert seam.recanons == [
            (SNAPSHOT_CID, "return", True),
            (SNAPSHOT_CID, "return", False),
        ]

    elif case == "flag_on_raw_empty":
        seam.run("")

        outcome = seam.outcomes[0]
        receipt = seam.receipt(outcome.event_id)
        assert receipt["state_event"]["previous_courier_id"] == SNAPSHOT_CID
        assert seam.remove_calls == [
            (SNAPSHOT_CID, ORDER_ID),
            (SNAPSHOT_CID, ORDER_ID),
        ]
        assert len(seam.writes) == 1
        assert seam.recanons == [
            (SNAPSHOT_CID, "return", True),
            (SNAPSHOT_CID, "return", False),
        ]

    elif case == "flag_on_raw_diff":
        seam.run(RAW_CID)

        assert seam.remove_calls == [
            (RAW_CID, ORDER_ID),
            (SNAPSHOT_CID, ORDER_ID),
        ]
        assert len(seam.writes) == 2
        for cid in (RAW_CID, SNAPSHOT_CID):
            assert [s["order_id"] for s in seam.plans()[cid]["stops"]] == [
                f"keep-{cid}"
            ]
        assert seam.recanons == [
            (RAW_CID, "return", True),
            (SNAPSHOT_CID, "return", False),
        ]

    elif case == "pending_retry_closed_duplicate":
        real_update = seam.SM.update_from_event
        update_attempts = {"count": 0}

        def fail_first_state_apply(event):
            update_attempts["count"] += 1
            if update_attempts["count"] == 1:
                raise seam.SM.StateReadError("synthetic guarded write rejection")
            return real_update(event)

        monkeypatch.setattr(seam.PW, "update_from_event", fail_first_state_apply)

        seam.run(RAW_CID)
        first = seam.outcomes[-1]
        first_receipt = seam.receipt(first.event_id)
        assert first.event_created is True
        assert first.state_ready is False
        assert first.downstream_executed is False
        assert first_receipt["state_status"] == "pending"
        assert first_receipt["downstream_status"] == "pending"
        assert seam.remove_calls == []
        assert seam.writes == []

        seam.run(RAW_CID)
        retry = seam.outcomes[-1]
        assert retry.event_id == first.event_id
        assert retry.event_created is False
        assert retry.state_ready is True
        assert retry.downstream_executed is True
        assert seam.remove_calls == [
            (RAW_CID, ORDER_ID),
            (SNAPSHOT_CID, ORDER_ID),
        ]
        assert len(seam.writes) == 2

        effects_after_retry = (
            list(seam.remove_calls),
            len(seam.writes),
            list(seam.recanons),
        )
        seam.run(RAW_CID)
        closed_duplicate = seam.outcomes[-1]
        assert closed_duplicate.event_id == first.event_id
        assert closed_duplicate.event_created is False
        assert closed_duplicate.state_ready is True
        assert closed_duplicate.downstream_executed is False
        assert (
            seam.remove_calls,
            len(seam.writes),
            seam.recanons,
        ) == effects_after_retry, "closed duplicate must not repeat either cleanup"

    elif case == "snapshot_cleanup_gap":
        seam.fail_remove_for.add(SNAPSHOT_CID)
        seam.run(RAW_CID)

        first = seam.outcomes[-1]
        receipt = seam.receipt(first.event_id)
        assert first.event_created is True
        assert first.downstream_executed is True
        assert receipt["state_status"] == "applied"
        assert receipt["downstream_status"] == "applied"
        assert seam.remove_calls == [
            (RAW_CID, ORDER_ID),
            (SNAPSHOT_CID, ORDER_ID),
        ]
        assert len(seam.writes) == 1
        assert any(
            s["order_id"] == ORDER_ID
            for s in seam.plans()[SNAPSHOT_CID]["stops"]
        )

        seam.run(RAW_CID)
        duplicate = seam.outcomes[-1]
        assert duplicate.event_created is False
        assert duplicate.downstream_executed is False
        # znana luka, poza receipt: snapshot-cleanup runs after the durable
        # callback is closed; its swallowed failure is not retried by duplicate.
        assert seam.remove_calls == [
            (RAW_CID, ORDER_ID),
            (SNAPSHOT_CID, ORDER_ID),
        ]
        assert any(
            s["order_id"] == ORDER_ID
            for s in seam.plans()[SNAPSHOT_CID]["stops"]
        )

    else:  # pragma: no cover - parametrization is exhaustive
        raise AssertionError(case)
