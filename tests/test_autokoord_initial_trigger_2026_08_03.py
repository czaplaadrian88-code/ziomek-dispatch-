"""Root-fix gates for the one-life initial AUTO_KOORD trigger."""

import inspect

from dispatch_v2 import auto_koord
from dispatch_v2 import panel_watcher as pw
from dispatch_v2 import state_machine as sm


def _seed_order(tmp_path, monkeypatch, oid: str, *, prep_minutes: int) -> None:
    state_path = tmp_path / "orders_state.json"
    state_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sm, "_state_path", lambda: str(state_path))
    monkeypatch.setattr(sm, "flag", lambda _name, default=None: default)
    sm.upsert_order(
        oid,
        {
            "status": "planned",
            "order_type": (
                "czasowka" if prep_minutes >= 60 else "elastic"
            ),
            "prep_minutes": prep_minutes,
            "status_id": 2,
            "courier_id": None,
            "restaurant": "fixture",
            "pickup_address": "fixture",
            "delivery_address": "fixture",
        },
        event="TEST_FIXTURE",
    )


def _enable_auto_koord(monkeypatch) -> None:
    def runtime_flag(name, default=None):
        if name == "AUTO_KOORD_ON_NEW_ORDER_ENABLED":
            return True
        if name == "AUTO_KOORD_TELEGRAM_INFO_ENABLED":
            return False
        return default

    monkeypatch.setattr(pw.C, "flag", runtime_flag)


def test_non_czasowka_does_not_claim_or_execute(tmp_path, monkeypatch):
    oid = "autokoord-non-czasowka"
    _seed_order(tmp_path, monkeypatch, oid, prep_minutes=59)
    _enable_auto_koord(monkeypatch)
    attempts = []
    monkeypatch.setattr(
        auto_koord,
        "perform_auto_koord",
        lambda **kwargs: attempts.append(kwargs["order_id"]),
    )

    pw._trigger_initial_auto_koord_once(
        oid,
        trigger="new_order_time_contract_ready",
        stats={},
        fetch_details_fn=lambda _z: None,
    )

    assert attempts == []
    assert sm.AUTO_KOORD_INITIAL_ATTEMPT_FIELD not in sm.get_order_strict(oid)


def test_recovered_decision_uses_canonical_state_field_names():
    decision, reason = auto_koord.needs_auto_koord(
        {
            "prep_minutes": 60,
            "status_id": 2,
            "courier_id": None,
        },
        flag_enabled=True,
    )
    assert decision is True
    assert reason == "czasowka_unassigned"

    assigned, assigned_reason = auto_koord.needs_auto_koord(
        {
            "prep_minutes": 60,
            "status_id": 2,
            "courier_id": "492",
        },
        flag_enabled=True,
    )
    assert assigned is False
    assert "already_assigned" in assigned_reason

    cancelled, cancelled_reason = auto_koord.needs_auto_koord(
        {
            "prep_minutes": 60,
            "status_id": 9,
            "courier_id": None,
        },
        flag_enabled=True,
    )
    assert cancelled is False
    assert "already_cancelled" in cancelled_reason


def test_trigger_is_defensive_when_state_read_fails(monkeypatch):
    _enable_auto_koord(monkeypatch)
    monkeypatch.setattr(
        pw,
        "state_get_order_strict",
        lambda _oid: (_ for _ in ()).throw(RuntimeError("fixture read fail")),
    )

    # Contract: no dependency failure from this hook may escape into the tick.
    pw._trigger_initial_auto_koord_once(
        "autokoord-defensive",
        trigger="initial_time_contract_recovered",
        stats={},
        fetch_details_fn=lambda _z: None,
    )


def test_pending_trigger_cannot_repeat_ready_or_recovered_claim(
    tmp_path, monkeypatch
):
    """Trigger 3 is idempotent when either iteration-1 edge claimed first."""
    _enable_auto_koord(monkeypatch)
    attempts = []

    def perform(**kwargs):
        attempts.append(kwargs["order_id"])
        return {
            "success": True,
            "attempts": 1,
            "skipped": False,
            "reason": "ok",
            "panel_response": "fixture",
        }

    monkeypatch.setattr(auto_koord, "perform_auto_koord", perform)
    monkeypatch.setattr(
        auto_koord,
        "emit_event_log",
        lambda *_args, **_kwargs: None,
    )

    for initial_trigger in (
        "new_order_time_contract_ready",
        "initial_time_contract_recovered",
    ):
        oid = f"autokoord-claimed-{initial_trigger}"
        _seed_order(tmp_path, monkeypatch, oid, prep_minutes=431)
        pw._trigger_initial_auto_koord_once(
            oid,
            trigger=initial_trigger,
            stats={},
            fetch_details_fn=lambda _z: None,
        )
        marker = sm.get_order_strict(oid)[
            sm.AUTO_KOORD_INITIAL_ATTEMPT_FIELD
        ]

        pw._trigger_initial_auto_koord_once(
            oid,
            trigger="new_order_time_contract_pending",
            stats={},
            fetch_details_fn=lambda _z: None,
        )

        assert attempts.count(oid) == 1
        assert marker["trigger"] == initial_trigger
        assert sm.get_order_strict(oid)[
            sm.AUTO_KOORD_INITIAL_ATTEMPT_FIELD
        ] == marker


def test_single_trigger_and_cold_start_ratchet():
    diff_source = inspect.getsource(pw._diff_and_emit)
    helper_source = inspect.getsource(pw._trigger_initial_auto_koord_once)
    cold_start_source = inspect.getsource(pw._post_restart_cold_start_scan)

    # Trzy lifecycle edges, ale jeden policy/execution helper.
    assert diff_source.count("_trigger_initial_auto_koord_once(") == 3
    assert "perform_auto_koord(" not in diff_source
    assert helper_source.count("perform_auto_koord(") == 1

    pending_branch = diff_source.split(
        "if not _initialize_new_order_time_contract(zid, norm, result):",
        1,
    )[1].split("continue", 1)[0]
    assert "_trigger_initial_auto_koord_once(" in pending_branch
    assert 'trigger="new_order_time_contract_pending"' in pending_branch

    # Cold-start odtwarza realny assignment i celowo nigdy nie parkuje do 26.
    assert "_trigger_initial_auto_koord_once(" not in cold_start_source
    assert "_panel_cid == str(KOORDYNATOR_ID)" in cold_start_source

    # Event-created nie moze ponownie zostac fence'em decyzji.
    assert "if result.event_created and result.state_ready:" in diff_source
    event_created_tail = diff_source.split(
        "if result.event_created and result.state_ready:", 1
    )[1].split("_trigger_initial_auto_koord_once(", 1)[0]
    assert "AUTO_KOORD" not in event_created_tail
