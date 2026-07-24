"""R-POOL-TRUTH / CID400 — oracle, mutation probe i ratchet kontraktu."""
from __future__ import annotations

import ast
import json
from dataclasses import replace
from pathlib import Path

import schedule_utils

from dispatch_v2 import common as C
from dispatch_v2 import courier_availability as CA
from dispatch_v2 import courier_resolver as CR
from dispatch_v2 import manual_overrides as MO
from dispatch_v2 import state_machine as SM
from dispatch_v2.courier_resolver import CourierState


ROOT = Path(__file__).parents[1]
POS = (53.1325, 23.1688)


def _write_json(path: Path, data: dict) -> None:
    path.write_text(
        json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def _enable_contract(monkeypatch, tmp_path, schedule):
    overrides = tmp_path / "manual_overrides.json"
    names = tmp_path / "grafik_full_names.json"
    _write_json(overrides, {})
    _write_json(names, {"Courier Exact": 400})
    monkeypatch.setattr(C, "ENABLE_CID_AVAILABILITY_CONTRACT", True)
    # R-POOL-TRUTH: jeden efektywny store — writer domyślny i resolver liczą path
    # z DISPATCH_STATE_DIR, więc harness izoluje go do tmp (== overrides).
    monkeypatch.setenv("DISPATCH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(MO, "OVERRIDES_PATH", str(overrides))
    monkeypatch.setattr(CR, "GRAFIK_FULL_NAMES_PATH", str(names))
    monkeypatch.setattr(schedule_utils, "load_schedule", lambda: schedule)
    monkeypatch.setattr(schedule_utils, "is_schedule_stale", lambda: False)
    return overrides


def _fleet():
    return {
        "400": CourierState(
            courier_id="400",
            name="wrong-display-name-is-not-an-identity-fallback",
            pos=POS,
            pos_source="gps",
        )
    }


def test_negative_oracle_empty_schedule_without_on_is_out(
    monkeypatch, tmp_path
):
    _enable_contract(monkeypatch, tmp_path, {"Courier Exact": None})
    assert CR.dispatchable_fleet(_fleet()) == []


def test_negative_oracle_empty_schedule_with_operator_on_is_in(
    monkeypatch, tmp_path
):
    overrides = _enable_contract(
        monkeypatch, tmp_path, {"Courier Exact": None}
    )
    CA.set_operator_availability(
        "400",
        CA.AvailabilityState.OPERATOR_ON,
        CA.AvailabilityProvenance.COORDINATOR_CONSOLE,
        path=str(overrides),
    )
    got = CR.dispatchable_fleet(_fleet())
    assert [courier.courier_id for courier in got] == ["400"]


def test_full_schedule_keeps_existing_on_shift_behavior(monkeypatch, tmp_path):
    _enable_contract(
        monkeypatch,
        tmp_path,
        {"Courier Exact": {"start": "00:00", "end": "23:59"}},
    )
    got = CR.dispatchable_fleet(_fleet())
    assert [courier.courier_id for courier in got] == ["400"]
    assert got[0].shift_start is not None
    assert got[0].shift_end is not None


def test_unknown_data_error_is_not_off_planned(tmp_path):
    overrides = tmp_path / "manual_overrides.json"
    names = tmp_path / "grafik_full_names.json"
    _write_json(overrides, {})
    _write_json(names, {"Courier Exact": 400})
    ctx_unknown = CA.load_context(
        None,
        schedule_error="read_failed",
        overrides_path=str(overrides),
        grafik_names_path=str(names),
    )
    unknown = CA.resolve(
        ctx_unknown,
        400,
        is_on_shift=lambda *_: (False, "unused"),
        mins_to_shift_start=lambda _: None,
        pre_shift_window_min=60,
    )
    ctx_off = replace(
        ctx_unknown,
        schedule={"Courier Exact": None},
        schedule_error=None,
    )
    off = CA.resolve(
        ctx_off,
        400,
        is_on_shift=lambda *_: (False, "unused"),
        mins_to_shift_start=lambda _: None,
        pre_shift_window_min=60,
    )
    assert unknown.state is CA.AvailabilityState.UNKNOWN_DATA_ERROR
    assert off.state is CA.AvailabilityState.OFF_PLANNED
    assert unknown.state is not off.state


def test_operator_off_overrides_full_schedule(tmp_path):
    overrides = tmp_path / "manual_overrides.json"
    names = tmp_path / "grafik_full_names.json"
    _write_json(overrides, {})
    _write_json(names, {"Courier Exact": 400})
    CA.set_operator_availability(
        400,
        CA.AvailabilityState.OPERATOR_OFF,
        CA.AvailabilityProvenance.COORDINATOR_CONSOLE,
        path=str(overrides),
    )
    ctx = CA.load_context(
        {"Courier Exact": {"start": "00:00", "end": "23:59"}},
        overrides_path=str(overrides),
        grafik_names_path=str(names),
    )
    decision = CA.resolve(
        ctx,
        400,
        is_on_shift=lambda *_: (True, "on_shift"),
        mins_to_shift_start=lambda _: None,
        pre_shift_window_min=60,
    )
    assert decision.state is CA.AvailabilityState.OPERATOR_OFF
    assert decision.dispatchable is False


def test_console_on_off_and_neutral_use_the_canonical_writer(
    monkeypatch, tmp_path
):
    overrides = _enable_contract(monkeypatch, tmp_path, {})
    monkeypatch.setattr(MO, "_all_name_to_cid", lambda: {"Courier Exact": 400})
    monkeypatch.setattr(MO, "_load_name_to_cid", lambda: {"Courier Exact": 400})

    MO._do_include(MO.load(), "Courier Exact", "", add_to_grafik=True)
    assert CA._operator_records(str(overrides))[0]["400"]["state"] == "OPERATOR_ON"

    MO._do_exclude(MO.load(), "Courier Exact")
    assert CA._operator_records(str(overrides))[0]["400"]["state"] == "OPERATOR_OFF"

    MO._do_include(MO.load(), "Courier Exact", "", add_to_grafik=False)
    assert "400" not in CA._operator_records(str(overrides))[0]


def test_stale_legacy_save_cannot_erase_concurrent_assignment_on(
    monkeypatch, tmp_path
):
    overrides = _enable_contract(monkeypatch, tmp_path, {})
    stale_legacy = MO.load()
    CA.set_operator_availability(
        400,
        CA.AvailabilityState.OPERATOR_ON,
        CA.AvailabilityProvenance.ASSIGNMENT_EVENT,
        path=str(overrides),
    )
    stale_legacy["working"] = {"123": {"start": "12:00", "end": "24:00"}}
    MO.save(stale_legacy)
    records, error = CA._operator_records(str(overrides))
    assert error is None
    assert records["400"]["state"] == "OPERATOR_ON"


def test_legacy_daily_reset_does_not_clear_persistent_operator_state(
    monkeypatch, tmp_path
):
    overrides = _enable_contract(monkeypatch, tmp_path, {})
    CA.set_operator_availability(
        400,
        CA.AvailabilityState.OPERATOR_ON,
        CA.AvailabilityProvenance.ASSIGNMENT_EVENT,
        path=str(overrides),
    )
    action, _message = MO.parse_command("reset")
    records, error = CA._operator_records(str(overrides))
    assert action == "reset"
    assert error is None
    assert records["400"]["state"] == "OPERATOR_ON"


def test_successful_assignment_writes_operator_on_after_order_commit(
    monkeypatch,
):
    calls = []
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
        lambda oid, data, event=None: (
            calls.append(("order", oid, event)) or dict(data)
        ),
    )
    monkeypatch.setattr(CR, "_load_courier_tiers", lambda: {})
    monkeypatch.setattr(
        CA,
        "set_operator_availability",
        lambda cid, state, provenance, at=None: calls.append(
            ("availability", str(cid), state.value, provenance.value)
        ),
    )
    result = SM.update_from_event(
        {
            "event_type": "COURIER_ASSIGNED",
            "order_id": "O-CID400",
            "courier_id": "400",
            "payload": {},
        }
    )
    assert result["courier_id"] == "400"
    assert calls == [
        ("order", "O-CID400", "COURIER_ASSIGNED"),
        ("availability", "400", "OPERATOR_ON", "assignment_event"),
    ]


def test_mutation_probe_removing_operator_fact_reproduces_empty_day_bug(
    tmp_path,
):
    overrides = tmp_path / "manual_overrides.json"
    names = tmp_path / "grafik_full_names.json"
    _write_json(overrides, {})
    _write_json(names, {"Courier Exact": 400})
    CA.set_operator_availability(
        400,
        CA.AvailabilityState.OPERATOR_ON,
        CA.AvailabilityProvenance.ASSIGNMENT_EVENT,
        path=str(overrides),
    )
    ctx = CA.load_context(
        {"Courier Exact": None},
        overrides_path=str(overrides),
        grafik_names_path=str(names),
    )
    real = CA.resolve(
        ctx,
        400,
        is_on_shift=lambda *_: (False, "empty"),
        mins_to_shift_start=lambda _: None,
        pre_shift_window_min=60,
    )
    mutant = CA.resolve(
        replace(ctx, operator_records={}),
        400,
        is_on_shift=lambda *_: (False, "empty"),
        mins_to_shift_start=lambda _: None,
        pre_shift_window_min=60,
    )
    assert real.dispatchable is True
    assert mutant.state is CA.AvailabilityState.OFF_PLANNED
    assert mutant.dispatchable is False


def test_ratchet_single_store_writer_and_single_pool_consumer():
    production_files = [
        path
        for path in ROOT.rglob("*.py")
        if "tests" not in path.parts
    ]
    store_owners = [
        path.relative_to(ROOT).as_posix()
        for path in production_files
        if "availability_by_cid" in path.read_text(encoding="utf-8")
    ]
    assert store_owners == ["courier_availability.py"]

    writer_calls = []
    resolver_calls = []
    for path in production_files:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if not isinstance(node, ast.Call):
                continue
            func = node.func
            if isinstance(func, ast.Attribute):
                if func.attr == "set_operator_availability":
                    writer_calls.append(path.relative_to(ROOT).as_posix())
                if func.attr == "resolve" and isinstance(func.value, ast.Name):
                    if func.value.id == "_availability":
                        resolver_calls.append(path.relative_to(ROOT).as_posix())
    assert set(writer_calls) == {"manual_overrides.py", "state_machine.py"}
    assert resolver_calls == ["courier_resolver.py"]


def test_flag_contract_is_etap4_off_default():
    assert "ENABLE_CID_AVAILABILITY_CONTRACT" in C.ETAP4_DECISION_FLAGS
    assert C.ENABLE_CID_AVAILABILITY_CONTRACT is False
    registry = json.loads(
        (ROOT / "tools" / "flag_lifecycle_registry.json").read_text(
            encoding="utf-8"
        )
    )
    entry = registry["flags"]["ENABLE_CID_AVAILABILITY_CONTRACT"]
    assert entry["current_snapshot"]["flags.json"] is False
    assert entry["lifecycle"] == "planned"
    # Review-only cross-check: review_artifacts/flags.json to artefakt REVIEW, którego
    # NIE ma (i nie powinno być) w produkcji. Hermetyczny guard — w live po prostu pomija
    # (kontrakt i tak pokryty przez kod-default + registry powyżej); w kontekście review dalej sprawdza.
    review_path = ROOT / "review_artifacts" / "flags.json"
    if review_path.exists():
        review_flags = json.loads(review_path.read_text(encoding="utf-8"))
        assert review_flags["ENABLE_CID_AVAILABILITY_CONTRACT"] is False
