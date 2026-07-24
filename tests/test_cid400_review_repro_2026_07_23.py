from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest
import schedule_utils

from dispatch_v2 import common as C
from dispatch_v2 import courier_availability as CA
from dispatch_v2 import courier_resolver as CR
from dispatch_v2 import manual_overrides as MO
from dispatch_v2 import state_machine as SM
from dispatch_v2.courier_resolver import CourierState


POS = (53.1325, 23.1688)


def _write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data), encoding="utf-8")


def _isolate_state(monkeypatch, tmp_path: Path) -> tuple[Path, Path]:
    state_dir = tmp_path / "dispatch_state"
    state_dir.mkdir()
    orders_path = state_dir / "orders_state.json"
    overrides_path = state_dir / "manual_overrides.json"
    _write_json(orders_path, {})
    _write_json(overrides_path, {})
    monkeypatch.setenv("DISPATCH_STATE_DIR", str(state_dir))
    monkeypatch.setattr(SM, "_state_path", lambda: str(orders_path))
    monkeypatch.setattr(MO, "OVERRIDES_PATH", str(overrides_path))
    return orders_path, overrides_path


def _patch_assignment_path(monkeypatch) -> None:
    monkeypatch.setattr(SM, "_observe_order_event", lambda _event: None)
    monkeypatch.setattr(SM, "get_order", lambda _oid: None)
    monkeypatch.setattr(
        SM,
        "decision_flag",
        lambda name: name == "ENABLE_CID_AVAILABILITY_CONTRACT",
    )
    monkeypatch.setattr(SM, "flag", lambda _name, default=False: default)
    monkeypatch.setattr(
        SM,
        "upsert_order",
        lambda _oid, data, event=None: dict(data),
    )
    monkeypatch.setattr(CR, "_load_courier_tiers", lambda: {})


def _delayed_assignment_event() -> dict:
    return {
        "event_type": "COURIER_ASSIGNED",
        "event_id": "evt-before-explicit-off",
        "created_at": "2026-07-23T11:59:00+00:00",
        "order_id": "O-DELAYED",
        "courier_id": "400",
        "payload": {},
    }


def test_explicit_off_is_not_revived_by_delayed_assignment(
    monkeypatch, tmp_path
):
    _orders_path, overrides_path = _isolate_state(monkeypatch, tmp_path)
    _patch_assignment_path(monkeypatch)
    CA.set_operator_availability(
        400,
        CA.AvailabilityState.OPERATOR_OFF,
        CA.AvailabilityProvenance.COORDINATOR_CONSOLE,
        path=str(overrides_path),
        at=datetime(2026, 7, 23, 12, 0, tzinfo=timezone.utc),
    )

    SM.update_from_event(_delayed_assignment_event())

    records, error = CA._operator_records(str(overrides_path))
    assert error is None
    assert records["400"]["state"] == "OPERATOR_OFF"
    assert records["400"]["provenance"] == "coordinator_console"


def test_assignment_does_not_report_success_when_availability_write_fails(
    monkeypatch, tmp_path
):
    _isolate_state(monkeypatch, tmp_path)
    _patch_assignment_path(monkeypatch)
    monkeypatch.setattr(
        CA,
        "set_operator_availability",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            OSError("synthetic availability write failure")
        ),
    )

    with pytest.raises(OSError, match="synthetic availability write failure"):
        SM.update_from_event(_delayed_assignment_event())


def test_console_writer_and_pool_resolver_share_effective_store(
    monkeypatch, tmp_path
):
    _orders_path, overrides_path = _isolate_state(monkeypatch, tmp_path)
    names_path = tmp_path / "grafik_full_names.json"
    _write_json(names_path, {"Courier Exact": 400})
    monkeypatch.setattr(C, "decision_flag", lambda name: True)
    monkeypatch.setattr(MO, "decision_flag", lambda name: True)
    monkeypatch.setattr(MO, "_resolve_cid", lambda _name: "400")
    monkeypatch.setattr(CR, "GRAFIK_FULL_NAMES_PATH", str(names_path))
    monkeypatch.setattr(
        schedule_utils,
        "load_schedule",
        lambda: {"Courier Exact": {"start": "00:00", "end": "23:59"}},
    )
    monkeypatch.setattr(schedule_utils, "is_schedule_stale", lambda: False)

    MO._do_exclude(MO.load(), "Courier Exact")
    records, error = CA._operator_records(str(overrides_path))
    assert error is None
    assert records["400"]["state"] == "OPERATOR_OFF"

    fleet = {
        "400": CourierState(
            courier_id="400",
            name="Courier Exact",
            pos=POS,
            pos_source="gps",
        )
    }
    assert CR.dispatchable_fleet(fleet) == []


def test_assignment_writer_and_resolver_cannot_diverge_on_effective_store(
    monkeypatch, tmp_path
):
    state_dir = tmp_path / "env_state"
    resolver_dir = tmp_path / "resolver_state"
    state_dir.mkdir()
    resolver_dir.mkdir()
    orders_path = state_dir / "orders_state.json"
    writer_overrides = state_dir / "manual_overrides.json"
    resolver_overrides = resolver_dir / "manual_overrides.json"
    names_path = tmp_path / "grafik_full_names.json"
    _write_json(orders_path, {})
    _write_json(writer_overrides, {})
    _write_json(resolver_overrides, {})
    _write_json(names_path, {"Courier Exact": 400})
    monkeypatch.setenv("DISPATCH_STATE_DIR", str(state_dir))
    monkeypatch.setattr(SM, "_state_path", lambda: str(orders_path))
    monkeypatch.setattr(MO, "OVERRIDES_PATH", str(resolver_overrides))
    monkeypatch.setattr(CR, "GRAFIK_FULL_NAMES_PATH", str(names_path))
    monkeypatch.setattr(C, "decision_flag", lambda name: True)
    monkeypatch.setattr(
        schedule_utils,
        "load_schedule",
        lambda: {"Courier Exact": None},
    )
    monkeypatch.setattr(schedule_utils, "is_schedule_stale", lambda: False)

    CA.set_operator_availability(
        400,
        CA.AvailabilityState.OPERATOR_ON,
        CA.AvailabilityProvenance.ASSIGNMENT_EVENT,
    )
    assert CA._operator_records(str(writer_overrides))[0]["400"]["state"] == (
        "OPERATOR_ON"
    )

    fleet = {
        "400": CourierState(
            courier_id="400",
            name="Courier Exact",
            pos=POS,
            pos_source="gps",
        )
    }
    assert [item.courier_id for item in CR.dispatchable_fleet(fleet)] == ["400"]
