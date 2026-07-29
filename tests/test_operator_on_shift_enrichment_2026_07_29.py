"""OPERATOR_ON keeps authority while one semantic window feeds HARD gates.

The regression is not merely a missing ``shift_end``.  Copying the exact
schedule window onto every OPERATOR_ON decision is also wrong:

* a future schedule start makes Gate 3 reject a courier whom the operator
  explicitly started now;
* an ended schedule end makes Gate 2 reject a legitimate second shift;
* assignment events do not carry the console's explicit/default working
  window, so inventing one would be a new business policy.

The oracle crosses the real boundary:
console/assignment writer -> CID availability -> exact schedule metadata ->
dispatchable fleet -> feasibility -> history/freshness.  Time is frozen in
Europe/Warsaw and no live state is read or written.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
import schedule_utils

from dispatch_v2 import common as C
from dispatch_v2 import courier_availability as CA
from dispatch_v2 import courier_resolver as CR
from dispatch_v2 import feasibility_v2 as F
from dispatch_v2 import manual_overrides as MO
from dispatch_v2 import proposal_freshness as PF
from dispatch_v2.courier_resolver import CourierState
from dispatch_v2.observability import candidate_logger as CL
from dispatch_v2.route_simulator_v2 import OrderSim
from dispatch_v2.tools import fleet_position_snapshot as FPS


ROOT = Path(__file__).parents[1]
CID = "400"
NAME = "Courier Exact"
POS = (53.1325, 23.1688)
WAW = ZoneInfo("Europe/Warsaw")
FROZEN_WAW = datetime(2026, 7, 29, 20, 0, tzinfo=WAW)
FROZEN_UTC = FROZEN_WAW.astimezone(timezone.utc)
ACTIVE_ENTRY = {"start": "10:00", "end": "22:00"}
FUTURE_ENTRY = {"start": "22:00", "end": "23:00"}
ENDED_ENTRY = {"start": "10:00", "end": "18:00"}


class _FrozenDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        if tz is None:
            return FROZEN_UTC.replace(tzinfo=None)
        return FROZEN_UTC.astimezone(tz)


def _write_json(path: Path, value: dict) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _setup_sources(
    monkeypatch,
    tmp_path,
    schedule,
    *,
    schedule_on_shift: bool,
    names=None,
) -> Path:
    overrides = tmp_path / "manual_overrides.json"
    identity = tmp_path / "grafik_full_names.json"
    _write_json(overrides, {})
    _write_json(identity, {NAME: int(CID)} if names is None else names)
    monkeypatch.setenv("DISPATCH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(C, "ENABLE_CID_AVAILABILITY_CONTRACT", True)
    monkeypatch.setattr(C, "ENABLE_OPERATOR_AVAILABILITY_EXPIRY", False)
    monkeypatch.setattr(C, "ENABLE_WORKING_OVERRIDE", True, raising=False)
    monkeypatch.setattr(
        C, "ENABLE_WORKING_OVERRIDE_GRAFIK_CAP", True, raising=False
    )
    monkeypatch.setattr(MO, "OVERRIDES_PATH", str(overrides))
    monkeypatch.setattr(CR, "GRAFIK_FULL_NAMES_PATH", str(identity))
    monkeypatch.setattr(CR, "datetime", _FrozenDateTime)
    monkeypatch.setattr(MO, "datetime", _FrozenDateTime)
    monkeypatch.setattr(CA, "datetime", _FrozenDateTime)
    monkeypatch.setattr(schedule_utils, "load_schedule", lambda: schedule)
    monkeypatch.setattr(schedule_utils, "is_schedule_stale", lambda: False)
    monkeypatch.setattr(
        schedule_utils,
        "match_courier",
        lambda name, current: name if name in current else None,
    )
    monkeypatch.setattr(
        schedule_utils,
        "is_on_shift",
        lambda *_: (schedule_on_shift, "frozen fixture"),
    )
    monkeypatch.setattr(MO, "_all_name_to_cid", lambda: {NAME: int(CID)})
    monkeypatch.setattr(MO, "_load_name_to_cid", lambda: {NAME: int(CID)})
    monkeypatch.setattr(CL, "_singleton", CL.CandidateLogger())
    return overrides


def _console_on(overrides: Path, text: str = f"{NAME} pracuje") -> dict:
    action, _message = MO._do_include(
        MO.load(),
        NAME,
        text,
        add_to_grafik=True,
    )
    assert action == "include"
    records, error = CA._operator_records(str(overrides))
    assert error is None
    return records[CID]


def _assignment_on(
    overrides: Path,
    *,
    at: datetime = FROZEN_UTC,
) -> dict:
    record = CA.set_operator_availability(
        CID,
        CA.AvailabilityState.OPERATOR_ON,
        CA.AvailabilityProvenance.ASSIGNMENT_EVENT,
        path=str(overrides),
        at=at,
    )
    assert record is not None
    return record


def _fleet() -> dict[str, CourierState]:
    return {
        CID: CourierState(
            courier_id=CID,
            name="display-name-is-not-an-identity-source",
            pos=POS,
            pos_source="gps",
        )
    }


def _decision(
    overrides: Path,
    identity: Path,
    schedule: dict,
    *,
    now: datetime = FROZEN_UTC,
):
    context = CA.load_context(
        schedule,
        overrides_path=str(overrides),
        grafik_names_path=str(identity),
        now=now,
        expiry_enabled=False,
    )
    return CA.resolve(
        context,
        CID,
        is_on_shift=schedule_utils.is_on_shift,
        mins_to_shift_start=lambda _: None,
        pre_shift_window_min=60,
    )


def _order(order_id: str, pickup_at: datetime) -> OrderSim:
    return OrderSim(
        order_id=order_id,
        pickup_coords=POS,
        delivery_coords=(53.14, 23.17),
        pickup_ready_at=pickup_at,
    )


def _feasibility(
    monkeypatch,
    courier: CourierState,
    pickup_at: datetime,
    *,
    bag=None,
):
    monkeypatch.setattr(C, "ENABLE_V325_SCHEDULE_HARDENING", True)
    monkeypatch.setattr(C, "ENABLE_D2_STALE_SCHEDULE_SOFT", False)
    monkeypatch.setattr(C, "ENABLE_FAIL12_SCHEDULE_FAILOPEN", True)
    monkeypatch.setattr(F, "_end_of_day_salvage", lambda _now: (False, None))
    order = _order("ORDER-NEW", pickup_at)
    return F.check_feasibility_v2(
        courier_pos=courier.pos,
        bag=[] if bag is None else bag,
        new_order=order,
        shift_start=courier.shift_start,
        shift_end=courier.shift_end,
        now=FROZEN_UTC,
        pickup_ready_at=pickup_at,
        pos_source=courier.pos_source,
        pos_from_store=False,
    )


def test_red_future_grafik_console_on_uses_operator_start_not_schedule_gate3(
    monkeypatch, tmp_path
):
    overrides = _setup_sources(
        monkeypatch,
        tmp_path,
        {NAME: FUTURE_ENTRY},
        schedule_on_shift=False,
    )
    record = _console_on(overrides)
    courier = CR.dispatchable_fleet(_fleet())[0]

    assert record["operator_window"]["start"] == "20:00"
    assert courier.shift_start == FROZEN_WAW
    assert courier.shift_end == FROZEN_WAW.replace(hour=23)
    verdict, reason, metrics, _plan = _feasibility(
        monkeypatch,
        courier,
        FROZEN_WAW + timedelta(minutes=10),
    )
    assert metrics.get("v325_reject_reason") != "PRE_SHIFT_TOO_EARLY"
    assert "PRE_SHIFT_TOO_EARLY" not in reason
    assert verdict in {"YES", "MAYBE"}


def test_red_ended_grafik_console_on_keeps_second_shift_to_operator_end(
    monkeypatch, tmp_path
):
    overrides = _setup_sources(
        monkeypatch,
        tmp_path,
        {NAME: ENDED_ENTRY},
        schedule_on_shift=False,
    )
    record = _console_on(overrides)
    courier = CR.dispatchable_fleet(_fleet())[0]

    assert record["operator_window"]["end"] == "24:00"
    assert record["operator_window"]["end_explicit"] is False
    assert courier.shift_start == FROZEN_WAW
    assert courier.shift_end == FROZEN_WAW.replace(
        hour=0, minute=0
    ) + timedelta(days=1)
    verdict, reason, metrics, _plan = _feasibility(
        monkeypatch,
        courier,
        FROZEN_WAW + timedelta(hours=1),
    )
    assert metrics.get("v325_reject_reason") != "PICKUP_POST_SHIFT"
    assert "PICKUP_POST_SHIFT" not in reason
    assert verdict in {"YES", "MAYBE"}


def test_red_console_explicit_end_is_canonical_cid_window(
    monkeypatch, tmp_path
):
    overrides = _setup_sources(
        monkeypatch,
        tmp_path,
        {NAME: ENDED_ENTRY},
        schedule_on_shift=False,
    )
    record = _console_on(overrides, f"{NAME} pracuje do 22")
    decision = _decision(
        overrides,
        tmp_path / "grafik_full_names.json",
        {NAME: ENDED_ENTRY},
    )
    courier = CR.dispatchable_fleet(_fleet())[0]

    assert record["operator_window"] == {
        "start": "20:00",
        "end": "22:00",
        "end_explicit": True,
        "added_at": FROZEN_UTC.isoformat(),
        "provenance": "coordinator_console",
    }
    assert decision.operator_window == record["operator_window"]
    assert decision.operator_since == FROZEN_UTC
    assert courier.shift_end == FROZEN_WAW.replace(hour=22)


def test_red_same_console_action_has_cid_contract_on_off_window_parity(
    monkeypatch, tmp_path
):
    overrides = _setup_sources(
        monkeypatch,
        tmp_path,
        {NAME: ENDED_ENTRY},
        schedule_on_shift=False,
    )
    _console_on(overrides, f"{NAME} pracuje od 20 do 22")

    monkeypatch.setattr(C, "ENABLE_CID_AVAILABILITY_CONTRACT", False)
    legacy = CR.dispatchable_fleet(_fleet())[0]
    monkeypatch.setattr(C, "ENABLE_CID_AVAILABILITY_CONTRACT", True)
    cid_contract = CR.dispatchable_fleet(_fleet())[0]

    assert cid_contract.shift_start == legacy.shift_start == FROZEN_WAW
    assert cid_contract.shift_end == legacy.shift_end == FROZEN_WAW.replace(hour=22)
    assert cid_contract.pos_source == legacy.pos_source == "gps"


def test_console_dual_projection_identity_mismatch_fails_closed(
    monkeypatch, tmp_path
):
    overrides = _setup_sources(
        monkeypatch,
        tmp_path,
        {},
        schedule_on_shift=False,
        names={},
    )
    monkeypatch.setattr(MO, "_all_name_to_cid", lambda: {NAME: 400})
    monkeypatch.setattr(MO, "_load_name_to_cid", lambda: {NAME: 401})

    payload = MO.load()
    payload_before = json.dumps(payload, ensure_ascii=False, sort_keys=True)
    bytes_before = overrides.read_bytes()
    action, message = MO._do_include(
        payload,
        NAME,
        f"{NAME} pracuje",
        add_to_grafik=True,
    )
    records, error = CA._operator_records(str(overrides))

    assert action == "include"
    assert "nie znam cid" in message.lower()
    assert error is None
    assert records == {}
    assert MO.get_working() == {}
    assert overrides.read_bytes() == bytes_before
    assert json.dumps(payload, ensure_ascii=False, sort_keys=True) == payload_before


def test_console_window_survives_later_assignment_and_expiry_rejects_pool(
    monkeypatch, tmp_path
):
    overrides = _setup_sources(
        monkeypatch,
        tmp_path,
        {},
        schedule_on_shift=False,
        names={},
    )
    console = _console_on(overrides, f"{NAME} pracuje do 21")
    assignment = _assignment_on(
        overrides,
        at=FROZEN_UTC + timedelta(minutes=30),
    )

    assert assignment["provenance"] == "assignment_event"
    assert assignment["operator_window"] == console["operator_window"]
    assert assignment["operator_window"]["provenance"] == "coordinator_console"

    after_end = FROZEN_UTC + timedelta(hours=1, minutes=1)

    class _AfterEndDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return after_end.replace(tzinfo=None)
            return after_end.astimezone(tz)

    monkeypatch.setattr(CR, "datetime", _AfterEndDateTime)
    monkeypatch.setattr(CA, "datetime", _AfterEndDateTime)
    assert CR.dispatchable_fleet(_fleet()) == []
    decision = _decision(
        overrides,
        tmp_path / "grafik_full_names.json",
        {},
        now=after_end,
    )
    assert decision.dispatchable is False
    assert decision.detail == "operator_window_ended"


@pytest.mark.parametrize("explicit_end", ["21", "23"])
def test_active_schedule_keeps_baseline_precedence_over_explicit_operator_end(
    monkeypatch, tmp_path, explicit_end
):
    overrides = _setup_sources(
        monkeypatch,
        tmp_path,
        {NAME: ACTIVE_ENTRY},
        schedule_on_shift=True,
    )
    _console_on(overrides, f"{NAME} pracuje do {explicit_end}")
    _assignment_on(
        overrides,
        at=FROZEN_UTC + timedelta(minutes=30),
    )

    expected_end = FROZEN_WAW.replace(hour=22)
    for contract_enabled in (False, True):
        monkeypatch.setattr(
            C, "ENABLE_CID_AVAILABILITY_CONTRACT", contract_enabled
        )
        courier = CR.dispatchable_fleet(
            {
                CID: CourierState(
                    courier_id=CID,
                    name=NAME,
                    pos=POS,
                    pos_source="gps",
                )
            }
        )[0]
        hard_report_end = CR.resolve_effective_shift_end_by_cid(
            CID,
            name=NAME,
            schedule={NAME: ACTIVE_ENTRY},
            now=FROZEN_UTC,
        )
        assert courier.shift_end == expected_end
        assert hard_report_end == expected_end


def test_active_schedule_stays_dispatchable_after_shorter_operator_window_ends(
    monkeypatch, tmp_path
):
    overrides = _setup_sources(
        monkeypatch,
        tmp_path,
        {NAME: ACTIVE_ENTRY},
        schedule_on_shift=True,
    )
    _console_on(overrides, f"{NAME} pracuje do 21")
    after_operator_end = FROZEN_WAW.replace(hour=21, minute=30)

    class _AfterOperatorEndDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return after_operator_end.astimezone(timezone.utc).replace(
                    tzinfo=None
                )
            return after_operator_end.astimezone(tz)

    monkeypatch.setattr(CR, "datetime", _AfterOperatorEndDateTime)
    monkeypatch.setattr(CA, "datetime", _AfterOperatorEndDateTime)
    for contract_enabled in (False, True):
        monkeypatch.setattr(
            C, "ENABLE_CID_AVAILABILITY_CONTRACT", contract_enabled
        )
        courier = CR.dispatchable_fleet(
            {
                CID: CourierState(
                    courier_id=CID,
                    name=NAME,
                    pos=POS,
                    pos_source="gps",
                )
            }
        )[0]
        hard_report_end = CR.resolve_effective_shift_end_by_cid(
            CID,
            name=NAME,
            schedule={NAME: ACTIVE_ENTRY},
            now=after_operator_end,
        )
        assert courier.shift_end == FROZEN_WAW.replace(hour=22)
        assert hard_report_end == FROZEN_WAW.replace(hour=22)


def test_future_operator_window_preserves_legacy_pre_shift_metadata_on_off(
    monkeypatch, tmp_path
):
    overrides = _setup_sources(
        monkeypatch,
        tmp_path,
        {},
        schedule_on_shift=False,
        names={},
    )
    _console_on(overrides, f"{NAME} pracuje od 22 do 23")

    outcomes = []
    for contract_enabled in (False, True):
        monkeypatch.setattr(
            C, "ENABLE_CID_AVAILABILITY_CONTRACT", contract_enabled
        )
        courier = CR.dispatchable_fleet(
            {
                CID: CourierState(
                    courier_id=CID,
                    name=NAME,
                    pos=None,
                    pos_source="none",
                )
            }
        )[0]
        outcomes.append(
            (
                courier.shift_start,
                courier.shift_end,
                courier.shift_start_min,
                courier.pos_source,
            )
        )

    assert outcomes[0] == outcomes[1]
    assert outcomes[0] == (
        FROZEN_WAW.replace(hour=22),
        FROZEN_WAW.replace(hour=23),
        120.0,
        "pre_shift",
    )


def test_global_schedule_helpers_keep_pre_candidate_overnight_semantics(
    monkeypatch,
):
    midnight_waw = datetime(2026, 7, 30, 0, 30, tzinfo=WAW)

    class _MidnightDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return midnight_waw.astimezone(timezone.utc).replace(tzinfo=None)
            return midnight_waw.astimezone(tz)

    monkeypatch.setattr(CR, "datetime", _MidnightDateTime)
    entry = {"start": "20:00", "end": "02:00"}

    assert CR._shift_start_dt(entry) == midnight_waw.replace(
        hour=20, minute=0
    )
    assert CR._shift_end_dt(entry) == midnight_waw.replace(hour=2, minute=0)
    assert CR._mins_to_shift_start(entry) == 1170.0


@pytest.mark.parametrize(
    "clock",
    [
        "２０:００",
        "²:00",
    ],
)
def test_operator_window_clock_is_ascii_only_and_fail_soft(clock):
    from dispatch_v2 import shift_interval

    value = {
        "start": clock,
        "end": "23:00",
        "end_explicit": True,
        "added_at": FROZEN_UTC.isoformat(),
        "provenance": "coordinator_console",
    }
    assert (
        shift_interval.parse_shift_interval(
            value,
            require_metadata=True,
            expected_provenance="coordinator_console",
        )
        is None
    )


@pytest.mark.parametrize(
    "bad_window",
    [
        {
            "start": "20:00",
            "end": "not-a-time",
            "end_explicit": True,
            "added_at": FROZEN_UTC.isoformat(),
            "provenance": "coordinator_console",
        },
        {
            "start": "20:00",
            "end": "21:00",
            "end_explicit": True,
            "added_at": FROZEN_UTC.isoformat(),
            "provenance": "assignment_event",
        },
    ],
)
def test_malformed_operator_window_is_per_cid_fail_closed(
    monkeypatch, tmp_path, bad_window
):
    overrides = _setup_sources(
        monkeypatch,
        tmp_path,
        {},
        schedule_on_shift=False,
        names={},
    )
    _write_json(
        overrides,
        {
            CA.STORE_KEY: {
                CID: {
                    "state": "OPERATOR_ON",
                    "provenance": "assignment_event",
                    "updated_at": FROZEN_UTC.isoformat(),
                    "operator_window": bad_window,
                }
            }
        },
    )
    decision = _decision(
        overrides,
        tmp_path / "grafik_full_names.json",
        {},
    )
    assert decision.state is CA.AvailabilityState.OPERATOR_ON
    assert decision.dispatchable is False
    assert decision.detail == "operator_window_invalid"


@pytest.mark.parametrize(
    "bad_window",
    [
        {"start": "25:00", "end": "26:00", "end_explicit": True},
        {"start": "20:00", "end": "21:00", "end_explicit": "yes"},
        {"start": "20:00", "end": "24:01", "end_explicit": True},
    ],
)
def test_writer_rejects_malformed_window_without_touching_payload(
    monkeypatch, tmp_path, bad_window
):
    overrides = _setup_sources(
        monkeypatch,
        tmp_path,
        {},
        schedule_on_shift=False,
        names={},
    )
    before = overrides.read_bytes()
    with pytest.raises(ValueError, match="invalid operator window"):
        CA.set_operator_availability(
            CID,
            CA.AvailabilityState.OPERATOR_ON,
            CA.AvailabilityProvenance.COORDINATOR_CONSOLE,
            path=str(overrides),
            at=FROZEN_UTC,
            operator_window=bad_window,
        )
    assert overrides.read_bytes() == before


def test_assignment_cannot_launder_existing_malformed_window(
    monkeypatch, tmp_path
):
    overrides = _setup_sources(
        monkeypatch,
        tmp_path,
        {},
        schedule_on_shift=False,
        names={},
    )
    _write_json(
        overrides,
        {
            CA.STORE_KEY: {
                CID: {
                    "state": "OPERATOR_ON",
                    "provenance": "coordinator_console",
                    "updated_at": FROZEN_UTC.isoformat(),
                    "operator_window": {
                        "start": "20:00",
                        "end": "broken",
                        "end_explicit": True,
                        "added_at": FROZEN_UTC.isoformat(),
                        "provenance": "coordinator_console",
                    },
                }
            }
        },
    )
    before = overrides.read_bytes()
    with pytest.raises(ValueError, match="existing operator window is invalid"):
        _assignment_on(
            overrides,
            at=FROZEN_UTC + timedelta(minutes=30),
        )
    assert overrides.read_bytes() == before


def test_midnight_interval_is_anchored_in_added_at_and_expires_next_day(
    monkeypatch, tmp_path
):
    overrides = _setup_sources(
        monkeypatch,
        tmp_path,
        {},
        schedule_on_shift=False,
        names={},
    )
    added_waw = datetime(2026, 7, 29, 23, 30, tzinfo=WAW)
    record = CA.set_operator_availability(
        CID,
        CA.AvailabilityState.OPERATOR_ON,
        CA.AvailabilityProvenance.COORDINATOR_CONSOLE,
        path=str(overrides),
        at=added_waw,
        operator_window={
            "start": "23:30",
            "end": "01:00",
            "end_explicit": True,
        },
    )
    before_end = datetime(2026, 7, 30, 0, 30, tzinfo=WAW)
    active = _decision(
        overrides,
        tmp_path / "grafik_full_names.json",
        {},
        now=before_end,
    )
    assert record is not None
    assert active.dispatchable is True
    assert active.operator_interval is not None
    assert active.operator_interval.start_at == added_waw
    assert active.operator_interval.end_at == datetime(
        2026, 7, 30, 1, 0, tzinfo=WAW
    )

    ended = _decision(
        overrides,
        tmp_path / "grafik_full_names.json",
        {},
        now=datetime(2026, 7, 30, 1, 1, tzinfo=WAW),
    )
    assert ended.dispatchable is False
    assert ended.detail == "operator_window_ended"


def test_console_dual_projection_is_one_atomic_write(monkeypatch, tmp_path):
    overrides = _setup_sources(
        monkeypatch,
        tmp_path,
        {},
        schedule_on_shift=False,
        names={},
    )
    writes = []
    real_atomic = CA._atomic_write

    def counted_atomic(path, data):
        writes.append(json.loads(json.dumps(data)))
        return real_atomic(path, data)

    monkeypatch.setattr(CA, "_atomic_write", counted_atomic)
    action, _message = MO._do_include(
        MO.load(),
        NAME,
        f"{NAME} pracuje do 22",
        add_to_grafik=True,
    )

    assert action == "include"
    assert len(writes) == 1
    written = writes[0]
    assert written["working"][CID]["end"] == "22:00"
    assert written[CA.STORE_KEY][CID]["operator_window"]["end"] == "22:00"

    writes.clear()
    MO._do_exclude(MO.load(), NAME)
    assert len(writes) == 1
    stopped = writes[0]
    assert CID in stopped["excluded_cids"]
    assert CID not in stopped["working"]
    assert stopped[CA.STORE_KEY][CID]["state"] == "OPERATOR_OFF"

    writes.clear()
    MO._do_include(MO.load(), NAME, f"{NAME} jest", add_to_grafik=False)
    assert len(writes) == 1
    neutral = writes[0]
    assert CID not in neutral["excluded_cids"]
    assert CID not in neutral[CA.STORE_KEY]


@pytest.mark.parametrize("contract_enabled", [False, True])
def test_every_console_write_uses_one_locked_atomic_owner_independent_of_flag(
    monkeypatch, tmp_path, contract_enabled
):
    overrides = _setup_sources(
        monkeypatch,
        tmp_path,
        {},
        schedule_on_shift=False,
        names={},
    )
    monkeypatch.setattr(
        C, "ENABLE_CID_AVAILABILITY_CONTRACT", contract_enabled
    )
    writes = []
    real_atomic = CA._atomic_write

    def counted_atomic(path, data):
        writes.append(json.loads(json.dumps(data)))
        return real_atomic(path, data)

    monkeypatch.setattr(CA, "_atomic_write", counted_atomic)
    action, _message = MO._do_include(
        MO.load(),
        NAME,
        f"{NAME} pracuje do 22",
        add_to_grafik=True,
    )

    assert action == "include"
    assert len(writes) == 1
    assert writes[0]["working"][CID]["end"] == "22:00"
    if contract_enabled:
        assert writes[0][CA.STORE_KEY][CID]["state"] == "OPERATOR_ON"
    else:
        assert writes[0].get(CA.STORE_KEY, {}) == {}


@pytest.mark.parametrize("contract_enabled", [False, True])
def test_generic_save_never_erases_concurrent_assignment_independent_of_flag(
    monkeypatch, tmp_path, contract_enabled
):
    overrides = _setup_sources(
        monkeypatch,
        tmp_path,
        {},
        schedule_on_shift=False,
        names={},
    )
    monkeypatch.setattr(
        C, "ENABLE_CID_AVAILABILITY_CONTRACT", contract_enabled
    )
    stale = MO.load()
    CA.set_operator_availability(
        CID,
        CA.AvailabilityState.OPERATOR_ON,
        CA.AvailabilityProvenance.ASSIGNMENT_EVENT,
        path=str(overrides),
        at=FROZEN_UTC,
    )
    stale["working"] = {
        "401": {
            "start": "20:00",
            "end": "24:00",
        }
    }
    MO.save(stale)

    records, error = CA._operator_records(str(overrides))
    assert error is None
    assert records[CID]["state"] == "OPERATOR_ON"


def test_stale_generic_save_rejects_concurrent_legacy_lost_update(
    monkeypatch, tmp_path
):
    overrides = _setup_sources(
        monkeypatch,
        tmp_path,
        {},
        schedule_on_shift=False,
        names={},
    )
    stale = MO.load()
    fresh = MO.load()
    fresh["excluded"] = ["other-writer"]
    MO.save(fresh)

    stale["working"] = {"401": {"start": "20:00", "end": "24:00"}}
    with pytest.raises(RuntimeError, match="concurrent manual overrides"):
        MO.save(stale)

    current = MO.load()
    assert current["excluded"] == ["other-writer"]
    assert current["working"] == {}


def test_repo_reset_entrypoint_uses_canonical_owner_and_logs_counts_only(
    monkeypatch, tmp_path, capsys
):
    from dispatch_v2 import manual_overrides_daily_reset as reset_entrypoint

    overrides = _setup_sources(
        monkeypatch,
        tmp_path,
        {},
        schedule_on_shift=False,
        names={},
    )
    _write_json(
        overrides,
        {
            "excluded": ["sensitive-name"],
            "excluded_cids": ["400"],
            "working": {"400": {"start": "20:00", "end": "24:00"}},
            CA.STORE_KEY: {
                CID: {
                    "state": "OPERATOR_ON",
                    "provenance": "assignment_event",
                    "updated_at": FROZEN_UTC.isoformat(),
                }
            },
        },
    )
    monkeypatch.setattr(
        reset_entrypoint,
        "_reset_coordinator_activations",
        lambda: 0,
    )

    assert reset_entrypoint.main() == 0
    output = capsys.readouterr().out
    assert "excluded=1" in output
    assert "excluded_cids=1" in output
    assert "working=1" in output
    assert "sensitive-name" not in output
    assert CID not in output

    current = json.loads(overrides.read_text(encoding="utf-8"))
    assert current["excluded"] == []
    assert current["excluded_cids"] == []
    assert current["working"] == {}
    assert current[CA.STORE_KEY][CID]["state"] == "OPERATOR_ON"
    assert os.stat(overrides).st_mode & 0o777 == 0o600


def test_mutation_dropping_preserved_window_recreates_post_end_pool_bug(
    monkeypatch, tmp_path
):
    overrides = _setup_sources(
        monkeypatch,
        tmp_path,
        {},
        schedule_on_shift=False,
        names={},
    )
    _console_on(overrides, f"{NAME} pracuje do 21")
    _assignment_on(
        overrides,
        at=FROZEN_UTC + timedelta(minutes=30),
    )
    after_end = FROZEN_UTC + timedelta(hours=1, minutes=1)
    real = _decision(
        overrides,
        tmp_path / "grafik_full_names.json",
        {},
        now=after_end,
    )
    assert real.dispatchable is False

    raw = json.loads(overrides.read_text(encoding="utf-8"))
    raw[CA.STORE_KEY][CID].pop("operator_window")
    _write_json(overrides, raw)
    mutant = _decision(
        overrides,
        tmp_path / "grafik_full_names.json",
        {},
        now=after_end,
    )
    assert mutant.dispatchable is True


def test_red_gate3_is_reachable_after_gate2_for_explicit_operator_start(
    monkeypatch, tmp_path
):
    overrides = _setup_sources(
        monkeypatch,
        tmp_path,
        {},
        schedule_on_shift=False,
        names={},
    )
    _console_on(overrides, f"{NAME} pracuje od 22 do 23")
    courier = CR.dispatchable_fleet(_fleet())[0]

    assert courier.shift_start == FROZEN_WAW.replace(hour=22)
    assert courier.shift_end == FROZEN_WAW.replace(hour=23)
    verdict, reason, metrics, _plan = _feasibility(
        monkeypatch,
        courier,
        FROZEN_WAW + timedelta(minutes=10),
    )
    assert verdict == "NO"
    assert metrics["v325_reject_reason"] == "PRE_SHIFT_TOO_EARLY"
    assert "PICKUP_POST_SHIFT" not in reason


def test_assignment_event_carries_since_but_does_not_invent_console_window(
    monkeypatch, tmp_path
):
    overrides = _setup_sources(
        monkeypatch,
        tmp_path,
        {NAME: FUTURE_ENTRY},
        schedule_on_shift=False,
    )
    record = _assignment_on(overrides)
    decision = _decision(
        overrides,
        tmp_path / "grafik_full_names.json",
        {NAME: FUTURE_ENTRY},
    )
    courier = CR.dispatchable_fleet(_fleet())[0]

    assert "operator_window" not in record
    assert decision.provenance is CA.AvailabilityProvenance.ASSIGNMENT_EVENT
    assert decision.operator_since == FROZEN_UTC
    assert decision.operator_window is None
    assert courier.shift_start == FROZEN_UTC
    assert courier.shift_end is None

    carried = _order("ORDER-CARRIED", FROZEN_UTC - timedelta(minutes=5))
    carried.status = "picked_up"
    carried.picked_up_at = FROZEN_UTC - timedelta(minutes=5)
    _verdict, reason, metrics, _plan = _feasibility(
        monkeypatch,
        courier,
        FROZEN_UTC + timedelta(minutes=10),
        bag=[carried],
    )
    assert metrics["fail12_signal"] == "bag"
    assert metrics.get("v325_reject_reason") not in {
        "PRE_SHIFT_TOO_EARLY",
        "PICKUP_POST_SHIFT",
    }
    assert "PRE_SHIFT_TOO_EARLY" not in reason
    assert "PICKUP_POST_SHIFT" not in reason


def test_assignment_event_on_active_exact_schedule_uses_schedule_window_e2e(
    monkeypatch, tmp_path
):
    overrides = _setup_sources(
        monkeypatch,
        tmp_path,
        {NAME: ACTIVE_ENTRY},
        schedule_on_shift=True,
    )
    _assignment_on(overrides)
    courier = CR.dispatchable_fleet(_fleet())[0]

    assert courier.shift_start == FROZEN_WAW.replace(hour=10)
    assert courier.shift_end == FROZEN_WAW.replace(hour=22)
    pickup_after_shift = courier.shift_end + timedelta(minutes=1)
    verdict, reason, metrics, _plan = _feasibility(
        monkeypatch,
        courier,
        pickup_after_shift,
    )
    assert verdict == "NO"
    assert "v325_PICKUP_POST_SHIFT" in reason
    assert metrics["v325_reject_reason"] == "PICKUP_POST_SHIFT"

    history_row = FPS._row("2026-07-29T18:00:00+00:00", courier)
    assert history_row["shift_end"] == courier.shift_end.isoformat()
    with_window = PF._fleet_snapshot({CID: courier})["generation"]
    without_window = PF._fleet_snapshot(
        {CID: replace(courier, shift_end=None)}
    )["generation"]
    assert with_window != without_window


@pytest.mark.parametrize(
    ("schedule", "names", "context_error"),
    [
        ({NAME: ACTIVE_ENTRY}, {}, None),
        ({NAME: ["not", "an", "entry"]}, {NAME: int(CID)}, None),
        ({NAME: ACTIVE_ENTRY}, {NAME: int(CID)}, "schedule_error"),
        ({NAME: ACTIVE_ENTRY}, {NAME: int(CID)}, "identity_error"),
    ],
)
def test_broken_schedule_never_supplies_exact_schedule_metadata(
    monkeypatch, tmp_path, schedule, names, context_error
):
    overrides = _setup_sources(
        monkeypatch,
        tmp_path,
        schedule,
        schedule_on_shift=False,
        names=names,
    )
    _assignment_on(overrides)
    identity = tmp_path / "grafik_full_names.json"
    context = CA.load_context(
        schedule,
        overrides_path=str(overrides),
        grafik_names_path=str(identity),
        now=FROZEN_UTC,
        expiry_enabled=False,
    )
    if context_error is not None:
        context = replace(context, **{context_error: "broken_source"})
    decision = CA.resolve(
        context,
        CID,
        is_on_shift=schedule_utils.is_on_shift,
        mins_to_shift_start=lambda _: None,
        pre_shift_window_min=60,
    )

    assert decision.state is CA.AvailabilityState.OPERATOR_ON
    assert decision.dispatchable is True
    assert decision.schedule_entry is None
    assert decision.operator_since == FROZEN_UTC


def test_operator_authority_survives_schedule_load_failure(monkeypatch, tmp_path):
    overrides = _setup_sources(
        monkeypatch,
        tmp_path,
        {NAME: ACTIVE_ENTRY},
        schedule_on_shift=False,
    )
    _assignment_on(overrides)
    identity = tmp_path / "grafik_full_names.json"
    context = CA.load_context(
        None,
        schedule_error="read_failed",
        overrides_path=str(overrides),
        grafik_names_path=str(identity),
        now=FROZEN_UTC,
        expiry_enabled=False,
    )
    decision = CA.resolve(
        context,
        CID,
        is_on_shift=lambda *_: (_ for _ in ()).throw(AssertionError("unused")),
        mins_to_shift_start=lambda _: (_ for _ in ()).throw(AssertionError("unused")),
        pre_shift_window_min=60,
    )

    assert decision.state is CA.AvailabilityState.OPERATOR_ON
    assert decision.dispatchable is True
    assert decision.schedule_entry is None
    assert decision.operator_since == FROZEN_UTC


def test_effective_shift_end_is_safe_for_missing_exact_entry():
    assert CR.effective_shift_end(None, None, True, True) is None
    assert CR.effective_shift_end(None, None, False, False) is None


def test_mutation_naive_schedule_projection_recreates_future_gate3_bug(
    monkeypatch, tmp_path
):
    overrides = _setup_sources(
        monkeypatch,
        tmp_path,
        {NAME: FUTURE_ENTRY},
        schedule_on_shift=False,
    )
    _console_on(overrides)
    real = CR.dispatchable_fleet(_fleet())[0]
    assert real.shift_start == FROZEN_WAW

    monkeypatch.setattr(
        CR,
        "_operator_on_shift_window",
        lambda availability, *_args, **_kwargs: (
            CR._shift_start_dt(availability.schedule_entry),
            CR.effective_shift_end(
                None, availability.schedule_entry, True, True
            ),
        ),
    )
    mutant = CR.dispatchable_fleet(_fleet())[0]
    verdict, _reason, metrics, _plan = _feasibility(
        monkeypatch,
        mutant,
        FROZEN_WAW + timedelta(minutes=10),
    )
    assert mutant.shift_start == FROZEN_WAW.replace(hour=22)
    assert verdict == "NO"
    assert metrics["v325_reject_reason"] == "PRE_SHIFT_TOO_EARLY"


def test_mutation_explicit_first_recreates_active_schedule_precedence_bug(
    monkeypatch, tmp_path
):
    overrides = _setup_sources(
        monkeypatch,
        tmp_path,
        {NAME: ACTIVE_ENTRY},
        schedule_on_shift=True,
    )
    _console_on(overrides, f"{NAME} pracuje do 21")
    fleet = {
        CID: CourierState(
            courier_id=CID,
            name=NAME,
            pos=POS,
            pos_source="gps",
        )
    }
    real = CR.dispatchable_fleet(fleet)[0]
    assert real.shift_end == FROZEN_WAW.replace(hour=22)

    def explicit_first(availability, _is_on_shift, _cap):
        assert availability.operator_interval is not None
        return (
            availability.operator_interval.start_at,
            availability.operator_interval.end_at,
        )

    monkeypatch.setattr(CR, "_operator_on_shift_window", explicit_first)
    mutant = CR.dispatchable_fleet(
        {
            CID: CourierState(
                courier_id=CID,
                name=NAME,
                pos=POS,
                pos_source="gps",
            )
        }
    )[0]
    assert mutant.shift_end == FROZEN_WAW.replace(hour=21)
    assert mutant.shift_end != real.shift_end


def test_mutation_bypassing_grafik_cap_recreates_ended_gate2_bug(
    monkeypatch, tmp_path
):
    overrides = _setup_sources(
        monkeypatch,
        tmp_path,
        {NAME: ENDED_ENTRY},
        schedule_on_shift=False,
    )
    _console_on(overrides)
    real = CR.dispatchable_fleet(_fleet())[0]
    assert real.shift_end > FROZEN_WAW

    monkeypatch.setattr(
        CR,
        "effective_shift_end",
        lambda _operator, schedule, _on_shift, _cap: CR._shift_end_dt(schedule),
    )
    mutant = CR.dispatchable_fleet(_fleet())[0]
    verdict, _reason, metrics, _plan = _feasibility(
        monkeypatch,
        mutant,
        FROZEN_WAW + timedelta(hours=1),
    )
    assert mutant.shift_end == FROZEN_WAW.replace(hour=18)
    assert verdict == "NO"
    assert metrics["v325_reject_reason"] == "PICKUP_POST_SHIFT"


def test_structural_ratchet_one_window_owner_no_parser_or_legacy_reader():
    production = [
        path
        for path in ROOT.rglob("*.py")
        if "tests" not in path.parts
        and ".codex-test-pkgroot" not in path.parts
    ]
    owners = []
    for path in production:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                if node.name == "effective_shift_end":
                    owners.append(path.relative_to(ROOT).as_posix())
    assert owners == ["courier_resolver.py"]

    availability_source = (ROOT / "courier_availability.py").read_text(
        encoding="utf-8"
    )
    assert "def _shift_start_dt" not in availability_source
    assert "def _shift_end_dt" not in availability_source
    availability_tree = ast.parse(availability_source)
    imports = [
        node
        for node in ast.walk(availability_tree)
        if isinstance(node, (ast.Import, ast.ImportFrom))
    ]
    assert all(
        "manual_overrides"
        not in (
            node.module
            if isinstance(node, ast.ImportFrom)
            else ",".join(alias.name for alias in node.names)
        )
        for node in imports
    )
    resolve_node = next(
        node
        for node in ast.walk(availability_tree)
        if isinstance(node, ast.FunctionDef) and node.name == "resolve"
    )
    assert all(
        not (isinstance(node, ast.Name) and node.id == "working")
        for node in ast.walk(resolve_node)
    )
    interval_source = (ROOT / "shift_interval.py").read_text(encoding="utf-8")
    assert interval_source.count("def parse_shift_interval(") == 1
    assert "parse_shift_interval(" in availability_source

    resolver_source = (ROOT / "courier_resolver.py").read_text(encoding="utf-8")
    assert resolver_source.count("def _operator_on_shift_window(") == 1
    assert "def _entry_shift_interval(" not in resolver_source
    assert "parse_shift_interval" not in resolver_source
    assert "availability.operator_window" in resolver_source
    assert "availability.operator_since" in resolver_source
    hard_report_start = resolver_source.index(
        "def resolve_effective_shift_end_by_cid("
    )
    hard_report_end = resolver_source.index(
        "\ndef _shift_end_dt(", hard_report_start
    )
    hard_report_source = resolver_source[hard_report_start:hard_report_end]
    assert "manual_overrides" not in hard_report_source
    assert "get_working" not in hard_report_source
    assert "_operator_on_shift_window(" in hard_report_source

    manual_source = (ROOT / "manual_overrides.py").read_text(encoding="utf-8")
    assert "os.replace(" not in manual_source
    include_start = manual_source.index("def _do_include(")
    include_end = manual_source.index("def _do_exclude(", include_start)
    include_source = manual_source[include_start:include_end]
    assert "operator_window=" in include_source
    assert "at=" in include_source
    assert include_source.count("commit_console_projection(") == 1
    assert "set_operator_availability(" not in include_source
    assert "save(data)" not in include_source

    exclude_source = manual_source[include_end:]
    assert exclude_source.count("commit_console_projection(") == 1
    assert "reset_legacy_fields(" in manual_source

    reset_source = (ROOT / "manual_overrides_daily_reset.py").read_text(
        encoding="utf-8"
    )
    assert "reset_legacy_fields(" in reset_source
    assert "os.replace(" not in reset_source
    assert "prev_working" not in reset_source
    assert "excluded_cids {" not in reset_source


def test_external_daily_reset_writer_must_join_canonical_store_owner():
    reset_path = Path(
        "/root/.openclaw/workspace/scripts/manual_overrides_daily_reset.py"
    )
    assert reset_path.exists()
    assert reset_path.is_file()
    assert os.access(reset_path, os.R_OK)
    raw = reset_path.read_bytes()
    assert hashlib.sha256(raw).hexdigest() == (
        "12e4161424ccb16b2b5cb61b4dbc74904dfe3e442fc32f1da1efeb512444d462"
    )
    source = raw.decode("utf-8")
    if (
        "reset_legacy_fields(" not in source
        or "os.replace(" in source
    ):
        pytest.xfail(
            "HOLD_LIVE: exact attested host reset writer still bypasses "
            "the shared availability lock"
        )
    assert (
        "reset_legacy_fields(" in source
        and "os.replace(" not in source
    )
