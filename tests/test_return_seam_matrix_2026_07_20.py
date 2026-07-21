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
    sweeper = {"enabled": False}
    real_drain_pending = PW.durable_event_apply.drain_pending
    monkeypatch.setattr(
        PW,
        "decision_flag",
        lambda name: (
            old_release["enabled"]
            if name == "ENABLE_REASSIGN_OLD_PLAN_RELEASE"
            else sweeper["enabled"]
            if name == "ENABLE_STATE_OUTBOX_SWEEPER"
            else False
        ),
    )
    monkeypatch.setattr(PW, "flag", lambda *_a, **_kw: False)
    monkeypatch.setattr(C, "flag", lambda *_a, **_kw: False)
    monkeypatch.setattr(
        PW.durable_event_apply,
        "drain_pending",
        lambda **kwargs: (
            real_drain_pending(**kwargs)
            if sweeper["enabled"]
            else {
                "seen": 0,
                "state_ready": 0,
                "downstream": 0,
                "superseded": 0,
                "failed": 0,
                "completed": 0,
            }
        ),
    )

    # _remove_stops_on_return stays real.  Its recanon import is replaced with
    # a tiny observer so importing this lifecycle-layer test cannot reach the
    # route solver or OR-Tools.
    recanons = []
    fake_plan_recheck = ModuleType("dispatch_v2.plan_recheck")

    def record_recanon(
        cid,
        *,
        reason,
        _raise_on_error=False,
        _enabled_by_receipt=None,
        _expected_order_generation=None,
    ):
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

    def record_remove_stops(cid, oid, **kwargs):
        cid = str(cid)
        oid = str(oid)
        remove_calls.append((cid, oid))
        if cid in fail_remove_for:
            raise RuntimeError("synthetic snapshot plan write failure")
        return real_remove_stops(cid, oid, **kwargs)

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
        sweeper=sweeper,
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
        "snapshot_cleanup_retry",
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
            (SNAPSHOT_CID, "return", True),
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
            (SNAPSHOT_CID, "return", True),
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
            (SNAPSHOT_CID, "return", True),
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

    elif case == "snapshot_cleanup_retry":
        seam.fail_remove_for.add(SNAPSHOT_CID)
        seam.run(RAW_CID)

        first = seam.outcomes[-1]
        receipt = seam.receipt(first.event_id)
        assert first.event_created is True
        assert first.downstream_executed is False
        assert first.failure_stage == "downstream"
        assert receipt["state_status"] == "applied"
        assert receipt["downstream_status"] == "pending"
        assert receipt["state_event"]["payload"][
            "return_snapshot_cleanup_courier_id"
        ] == SNAPSHOT_CID
        assert seam.remove_calls == [
            (RAW_CID, ORDER_ID),
            (SNAPSHOT_CID, ORDER_ID),
        ]
        assert len(seam.writes) == 1
        assert any(
            s["order_id"] == ORDER_ID
            for s in seam.plans()[SNAPSHOT_CID]["stops"]
        )

        seam.fail_remove_for.clear()
        with sqlite3.connect(seam.events_db) as conn:
            conn.execute(
                "UPDATE state_apply_outbox "
                "SET updated_at='2000-01-01T00:00:00+00:00' "
                "WHERE event_id=?",
                (first.event_id,),
            )
        seam.sweeper["enabled"] = True
        sweep = seam.PW._sweep_state_apply_outbox()

        assert sweep["state_outbox_sweeper_completed"] == 1
        assert sweep["durable_downstream_recovered"] == 1
        assert seam.receipt(first.event_id)["downstream_status"] == "applied"
        assert seam.remove_calls == [
            (RAW_CID, ORDER_ID),
            (SNAPSHOT_CID, ORDER_ID),
            (RAW_CID, ORDER_ID),
            (SNAPSHOT_CID, ORDER_ID),
        ]
        assert len(seam.writes) == 2
        assert [
            s["order_id"] for s in seam.plans()[SNAPSHOT_CID]["stops"]
        ] == [f"keep-{SNAPSHOT_CID}"]

        effects_after_sweep = (
            list(seam.remove_calls),
            len(seam.writes),
            list(seam.recanons),
        )
        seam.run(RAW_CID)
        duplicate = seam.outcomes[-1]
        assert duplicate.event_created is False
        assert duplicate.downstream_executed is False
        assert (
            seam.remove_calls,
            len(seam.writes),
            seam.recanons,
        ) == effects_after_sweep, "closed receipt must remain side-effect free"

    else:  # pragma: no cover - parametrization is exhaustive
        raise AssertionError(case)


@pytest.mark.parametrize("tick_authorized", [False, True])
def test_return_snapshot_and_receipt_share_one_flag_snapshot(
    isolated_return_seam, monkeypatch, tick_authorized
):
    """Hot-flip w środku emisji nie może rozdzielić snapshot CID od markera."""
    seam = isolated_return_seam
    release_reads = []

    def tick_flag(name):
        if name == "ENABLE_REASSIGN_OLD_PLAN_RELEASE":
            release_reads.append(name)
            return tick_authorized
        return False

    monkeypatch.setattr(seam.PW, "decision_flag", tick_flag)

    seam.run(RAW_CID)

    receipt = seam.receipt(seam.outcomes[-1].event_id)["state_event"]
    assert release_reads == ["ENABLE_REASSIGN_OLD_PLAN_RELEASE"]
    assert receipt.get("return_previous_cleanup_authorized") is tick_authorized
    assert (
        receipt["payload"].get("return_snapshot_cleanup_courier_id")
        == SNAPSHOT_CID
    ) is tick_authorized


def test_return_retry_after_effect_before_receipt_is_plan_write_idempotent(
    isolated_return_seam, monkeypatch
):
    """At-least-once callback po crash-window nie powtarza mutacji planu."""
    seam = isolated_return_seam
    seam.old_release["enabled"] = True
    real_mark = seam.EB.mark_state_apply_downstream
    mark_attempts = {"count": 0}

    def fail_first_receipt_mark(event_id):
        mark_attempts["count"] += 1
        if mark_attempts["count"] == 1:
            return False
        return real_mark(event_id)

    monkeypatch.setattr(
        seam.EB, "mark_state_apply_downstream", fail_first_receipt_mark
    )
    seam.run(RAW_CID)

    first = seam.outcomes[-1]
    assert first.failure_stage == "downstream"
    assert seam.receipt(first.event_id)["downstream_status"] == "pending"
    assert len(seam.writes) == 2
    for cid in (RAW_CID, SNAPSHOT_CID):
        assert [s["order_id"] for s in seam.plans()[cid]["stops"]] == [
            f"keep-{cid}"
        ]

    # Legalna nowsza generacja: order jest znow przypisany do snapshot CID,
    # a jego nowy plan ponownie zawiera ten sam stop. Fixture zapisuje stan
    # po ASSIGNED bez uruchamiania drugiego lifecycle callbacku w tym tescie.
    seam.SM.upsert_order(
        ORDER_ID,
        {
            "status": "assigned",
            "commitment_level": "assigned",
            "courier_id": SNAPSHOT_CID,
            "last_lifecycle_event_id": "assign-after-return-callback",
            "last_lifecycle_event_id_courier_assigned": (
                "assign-after-return-callback"
            ),
        },
        event="TEST_NEW_ASSIGNMENT_GENERATION",
    )
    newer_plans = seam.plans()
    newer_plans[SNAPSHOT_CID]["stops"].insert(
        0, {"order_id": ORDER_ID, "type": "dropoff"}
    )
    newer_plans[SNAPSHOT_CID]["plan_version"] += 1
    seam.plans_path.write_text(json.dumps(newer_plans), encoding="utf-8")

    with sqlite3.connect(seam.events_db) as conn:
        conn.execute(
            "UPDATE state_apply_outbox "
            "SET updated_at='2000-01-01T00:00:00+00:00' WHERE event_id=?",
            (first.event_id,),
        )
    seam.sweeper["enabled"] = True
    sweep = seam.PW._sweep_state_apply_outbox()

    assert sweep["state_outbox_sweeper_completed"] == 1
    assert seam.receipt(first.event_id)["downstream_status"] == "applied"
    assert len(seam.writes) == 2, "retry nie moze ponownie zapisac planu"
    assert any(
        stop["order_id"] == ORDER_ID
        for stop in seam.plans()[SNAPSHOT_CID]["stops"]
    ), "stary RETURN nie moze usunac stopa nowszej generacji"
    assert seam.remove_calls == [
        (RAW_CID, ORDER_ID),
        (SNAPSHOT_CID, ORDER_ID),
        (RAW_CID, ORDER_ID),
    ]
    # Callback jest świadomie at-least-once: retry powtarza recanon RAW_CID,
    # żeby crash po remove_stops, ale przed recanonem nie zgubil drugiej fazy.
    # Snapshot CID jest juz aktywna nowsza generacja, wiec stary callback go
    # nie dotyka.
    assert len(seam.recanons) == 3


def test_return_persisted_snapshot_cleanup_survives_flag_off_before_retry(
    isolated_return_seam,
):
    """OFF blokuje nowe wpisy, lecz exact receipt ON musi zostac domkniety."""
    seam = isolated_return_seam
    seam.old_release["enabled"] = True
    seam.fail_remove_for.add(SNAPSHOT_CID)
    seam.run(RAW_CID)

    first = seam.outcomes[-1]
    assert seam.receipt(first.event_id)["downstream_status"] == "pending"
    assert any(
        stop["order_id"] == ORDER_ID
        for stop in seam.plans()[SNAPSHOT_CID]["stops"]
    )

    seam.fail_remove_for.clear()
    seam.old_release["enabled"] = False
    with sqlite3.connect(seam.events_db) as conn:
        conn.execute(
            "UPDATE state_apply_outbox "
            "SET updated_at='2000-01-01T00:00:00+00:00' WHERE event_id=?",
            (first.event_id,),
        )
    seam.sweeper["enabled"] = True
    sweep = seam.PW._sweep_state_apply_outbox()

    assert sweep["state_outbox_sweeper_completed"] == 1
    assert seam.receipt(first.event_id)["downstream_status"] == "applied"
    assert not any(
        stop["order_id"] == ORDER_ID
        for stop in seam.plans()[SNAPSHOT_CID]["stops"]
    ), "OFF nie moze uciac cleanupu autoryzowanego w exact receipt"


def test_return_cleans_raw_current_and_snapshot_courier_provenance(
    isolated_return_seam,
):
    """Wyścig A(snapshot)->B(current), raw=C nie może zgubić planu B."""
    seam = isolated_return_seam
    current_cid = "300"
    seam.old_release["enabled"] = True
    seam.SM.upsert_order(
        ORDER_ID,
        {
            "status": "assigned",
            "commitment_level": "assigned",
            "courier_id": current_cid,
        },
        event="TEST_CURRENT_CID_CHANGED_AFTER_SNAPSHOT",
    )
    plans = seam.plans()
    plans[current_cid] = _plan(current_cid)
    seam.plans_path.write_text(json.dumps(plans), encoding="utf-8")

    seam.run(RAW_CID)

    receipt = seam.receipt(seam.outcomes[-1].event_id)
    assert receipt["state_event"]["previous_courier_id"] == current_cid
    assert seam.remove_calls == [
        (RAW_CID, ORDER_ID),
        (current_cid, ORDER_ID),
        (SNAPSHOT_CID, ORDER_ID),
    ]
    for cid in (RAW_CID, current_cid, SNAPSHOT_CID):
        assert all(
            stop["order_id"] != ORDER_ID
            for stop in seam.plans()[cid]["stops"]
        )


def test_return_retry_replays_recanon_after_flag_off_if_remove_was_durable(
    isolated_return_seam, monkeypatch
):
    """Flip OFF po remove nie moze zamknac receipt bez brakujacego recanonu."""
    seam = isolated_return_seam
    seam.old_release["enabled"] = True
    recanon_attempts = []
    failed_snapshot = {"value": False}

    def fail_snapshot_recanon_once(
        cid,
        *,
        reason,
        _raise_on_error=False,
        _enabled_by_receipt=None,
        _expected_order_generation=None,
    ):
        recanon_attempts.append((str(cid), reason, bool(_raise_on_error)))
        if str(cid) == SNAPSHOT_CID and not failed_snapshot["value"]:
            failed_snapshot["value"] = True
            raise RuntimeError("synthetic crash after snapshot remove")
        return True

    monkeypatch.setattr(
        sys.modules["dispatch_v2.plan_recheck"],
        "recanon_courier",
        fail_snapshot_recanon_once,
    )
    seam.run(RAW_CID)

    first = seam.outcomes[-1]
    assert first.failure_stage == "downstream"
    assert seam.receipt(first.event_id)["downstream_status"] == "pending"
    assert len(seam.writes) == 2

    seam.old_release["enabled"] = False
    with sqlite3.connect(seam.events_db) as conn:
        conn.execute(
            "UPDATE state_apply_outbox "
            "SET updated_at='2000-01-01T00:00:00+00:00' WHERE event_id=?",
            (first.event_id,),
        )
    seam.sweeper["enabled"] = True
    sweep = seam.PW._sweep_state_apply_outbox()

    assert sweep["state_outbox_sweeper_completed"] == 1
    assert seam.receipt(first.event_id)["downstream_status"] == "applied"
    assert len(seam.writes) == 2, "retry remove_stops pozostaje store no-op"
    assert recanon_attempts == [
        (RAW_CID, "return", True),
        (SNAPSHOT_CID, "return", True),
        (RAW_CID, "return", True),
        (SNAPSHOT_CID, "return", True),
    ]


def test_return_cleanup_race_cas_preserves_new_generation(
    isolated_return_seam, monkeypatch
):
    """Nowy plan po prechecku wygrywa CAS; retry zamyka receipt bez usunięcia."""
    seam = isolated_return_seam
    seam.old_release["enabled"] = True
    monkeypatch.setattr(
        seam.PW,
        "flag",
        lambda name, *args, **kwargs: (
            name == "ENABLE_INVALIDATE_PLAN_ON_BAG_CHANGE"
        ),
    )
    monkeypatch.setattr(
        seam.C,
        "flag",
        lambda name, *args, **kwargs: (
            name == "ENABLE_INVALIDATE_PLAN_ON_BAG_CHANGE"
        ),
    )
    real_cleanup = seam.PW._remove_stops_on_return
    raced = {"value": False}

    def assign_and_write_new_plan_before_cleanup(cid, oid, **kwargs):
        if not raced["value"]:
            raced["value"] = True
            seam.SM.upsert_order(
                ORDER_ID,
                {
                    "status": "assigned",
                    "commitment_level": "assigned",
                    "courier_id": SNAPSHOT_CID,
                    "last_lifecycle_event_id": "assign-raced-return",
                    "last_lifecycle_event_id_courier_assigned": (
                        "assign-raced-return"
                    ),
                },
                event="TEST_RACING_ASSIGNMENT",
            )
            plans = seam.plans()
            plans[SNAPSHOT_CID]["plan_version"] += 1
            seam.plans_path.write_text(json.dumps(plans), encoding="utf-8")
        return real_cleanup(cid, oid, **kwargs)

    monkeypatch.setattr(
        seam.PW,
        "_remove_stops_on_return",
        assign_and_write_new_plan_before_cleanup,
    )
    seam.run(SNAPSHOT_CID)

    outcome = seam.outcomes[-1]
    plan = seam.plans()[SNAPSHOT_CID]
    assert outcome.failure_stage == "downstream"
    assert seam.receipt(outcome.event_id)["downstream_status"] == "pending"
    assert plan["invalidated_at"] is None
    assert any(str(s.get("order_id")) == ORDER_ID for s in plan["stops"])
    assert seam.SM.get_order_strict(ORDER_ID)["courier_id"] == SNAPSHOT_CID

    with sqlite3.connect(seam.events_db) as conn:
        conn.execute(
            "UPDATE state_apply_outbox "
            "SET updated_at='2000-01-01T00:00:00+00:00' WHERE event_id=?",
            (outcome.event_id,),
        )
    seam.sweeper["enabled"] = True
    sweep = seam.PW._sweep_state_apply_outbox()
    assert sweep["state_outbox_sweeper_completed"] == 1
    assert seam.receipt(outcome.event_id)["downstream_status"] == "applied"
    assert any(
        str(s.get("order_id")) == ORDER_ID
        for s in seam.plans()[SNAPSHOT_CID]["stops"]
    )


def test_return_cleanup_race_flag_off_cas_preserves_new_generation(
    isolated_return_seam, monkeypatch
):
    """CAS chroni nową generację także gdy receipt nie autoryzuje repair."""
    seam = isolated_return_seam
    seam.old_release["enabled"] = True
    real_cleanup = seam.PW._remove_stops_on_return
    raced = {"value": False}

    def assign_and_write_new_plan_before_cleanup(cid, oid, **kwargs):
        if not raced["value"]:
            raced["value"] = True
            seam.SM.upsert_order(
                ORDER_ID,
                {
                    "status": "assigned",
                    "commitment_level": "assigned",
                    "courier_id": SNAPSHOT_CID,
                    "last_lifecycle_event_id": "assign-raced-return-off",
                    "last_lifecycle_event_id_courier_assigned": (
                        "assign-raced-return-off"
                    ),
                },
                event="TEST_RACING_ASSIGNMENT_OFF",
            )
            plans = seam.plans()
            plans[SNAPSHOT_CID]["plan_version"] += 1
            seam.plans_path.write_text(json.dumps(plans), encoding="utf-8")
        return real_cleanup(cid, oid, **kwargs)

    monkeypatch.setattr(
        seam.PW,
        "_remove_stops_on_return",
        assign_and_write_new_plan_before_cleanup,
    )
    seam.run(SNAPSHOT_CID)

    outcome = seam.outcomes[-1]
    receipt = seam.receipt(outcome.event_id)
    plan = seam.plans()[SNAPSHOT_CID]
    assert receipt["state_event"][
        "invalidate_plan_on_bag_change_authorized"
    ] is False
    assert outcome.failure_stage == "downstream"
    assert receipt["downstream_status"] == "pending"
    assert plan["invalidated_at"] is None
    assert any(
        str(stop.get("order_id")) == ORDER_ID for stop in plan["stops"]
    )
    assert seam.SM.get_order_strict(ORDER_ID)["courier_id"] == SNAPSHOT_CID

    with sqlite3.connect(seam.events_db) as conn:
        conn.execute(
            "UPDATE state_apply_outbox "
            "SET updated_at='2000-01-01T00:00:00+00:00' WHERE event_id=?",
            (outcome.event_id,),
        )
    seam.sweeper["enabled"] = True
    sweep = seam.PW._sweep_state_apply_outbox()
    assert sweep["state_outbox_sweeper_completed"] == 1
    assert seam.receipt(outcome.event_id)["downstream_status"] == "applied"
