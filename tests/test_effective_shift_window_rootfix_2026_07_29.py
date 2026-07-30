"""Negative oracles for the typed effective-shift-window root fix.

The fixtures are source-only and hermetic.  They deliberately freeze the
Warsaw clock around an overnight shift, and they never read production state.
"""
from __future__ import annotations

import ast
import json
import sys
import threading
import time
import types
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

from dispatch_v2 import common as C
from dispatch_v2 import courier_availability as CA
from dispatch_v2 import courier_resolver as CR
from dispatch_v2 import feasibility_v2 as F
from dispatch_v2 import plan_recheck as PR
from dispatch_v2.courier_resolver import CourierState
from dispatch_v2.route_simulator_v2 import OrderSim


ROOT = Path(__file__).parents[1]
WAW = ZoneInfo("Europe/Warsaw")
CID = "400"
NAME = "Courier Exact"
POS = (53.1325, 23.1688)
OVERNIGHT_NOW = datetime(2026, 7, 30, 0, 30, tzinfo=WAW)
OVERNIGHT_END = datetime(2026, 7, 30, 2, 0, tzinfo=WAW)
OVERNIGHT_ENTRY = {"start": "20:00", "end": "02:00"}


def _context(*, operator=None, now=OVERNIGHT_NOW, schedule=None):
    return CA.AvailabilityContext(
        operator_records={} if operator is None else {CID: operator},
        operator_error=None,
        legacy_working_by_cid={},
        legacy_error=None,
        schedule={NAME: OVERNIGHT_ENTRY} if schedule is None else schedule,
        schedule_error=None,
        schedule_names_by_cid={CID: NAME},
        identity_error=None,
        now=now,
        expiry_enabled=False,
    )


def _resolve(context, *, host_on_shift=False):
    return CA.resolve(
        context,
        CID,
        is_on_shift=lambda *_: (host_on_shift, "host fixture"),
        mins_to_shift_start=lambda _entry: 1170.0,
        pre_shift_window_min=60,
    )


def _assignment_record(at=OVERNIGHT_NOW):
    return {
        "state": CA.AvailabilityState.OPERATOR_ON.value,
        "provenance": CA.AvailabilityProvenance.ASSIGNMENT_EVENT.value,
        "updated_at": at.astimezone(timezone.utc).isoformat(),
    }


def _order(oid, pickup_at):
    return OrderSim(
        order_id=oid,
        pickup_coords=POS,
        delivery_coords=(53.14, 23.17),
        pickup_ready_at=pickup_at,
    )


def _write_store(path, payload):
    path.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def test_schedule_interval_is_parsed_once_and_overrides_wrong_host_boolean(
    monkeypatch,
):
    calls = []
    real_parser = CA.parse_shift_interval

    def counted_parser(value, **kwargs):
        calls.append((value, kwargs))
        return real_parser(value, **kwargs)

    monkeypatch.setattr(CA, "parse_shift_interval", counted_parser)
    decision = _resolve(
        _context(operator=_assignment_record()),
        host_on_shift=False,
    )

    assert len(calls) == 1
    assert decision.real_on_shift_now is True
    assert decision.schedule_interval is not None
    assert decision.schedule_interval.end_at == OVERNIGHT_END
    assert decision.effective_shift_window.end_at == OVERNIGHT_END
    assert (
        decision.effective_shift_window.end_status
        is CA.ShiftEndStatus.KNOWN
    )


def test_non_dispatchable_schedule_decision_still_exposes_known_end():
    before_window = datetime(2026, 7, 29, 18, 0, tzinfo=WAW)
    decision = _resolve(
        _context(now=before_window),
        host_on_shift=False,
    )

    assert decision.dispatchable is False
    assert decision.schedule_interval is not None
    assert decision.effective_shift_window.end_at == OVERNIGHT_END
    assert (
        decision.effective_shift_window.end_status
        is CA.ShiftEndStatus.KNOWN
    )


def test_cross_midnight_pool_and_hard_report_share_typed_end(
    monkeypatch,
    tmp_path,
):
    now_waw = datetime(2026, 7, 29, 23, 0, tzinfo=WAW)
    now_utc = now_waw.astimezone(timezone.utc)
    expected_end = datetime(2026, 7, 30, 2, 0, tzinfo=WAW)
    schedule = {NAME: OVERNIGHT_ENTRY}
    store = tmp_path / "manual_overrides.json"
    identity = tmp_path / "grafik_full_names.json"
    _write_store(
        store,
        {
            CA.STORE_KEY: {
                CID: _assignment_record(now_waw),
            }
        },
    )
    _write_store(identity, {NAME: int(CID)})
    monkeypatch.setenv("DISPATCH_STATE_DIR", str(tmp_path))
    monkeypatch.setattr(CR, "GRAFIK_FULL_NAMES_PATH", str(identity))
    monkeypatch.setattr(CR, "_load_courier_names", lambda: {CID: NAME})
    monkeypatch.setattr(
        C,
        "decision_flag",
        lambda name: name == "ENABLE_CID_AVAILABILITY_CONTRACT",
    )
    monkeypatch.setattr(
        C,
        "flag",
        lambda _name, default=None: default,
    )
    monkeypatch.setattr(C, "ENABLE_WORKING_OVERRIDE", True, raising=False)
    monkeypatch.setattr(
        C,
        "ENABLE_WORKING_OVERRIDE_GRAFIK_CAP",
        True,
        raising=False,
    )
    monkeypatch.setattr(C, "ENABLE_V325_SCHEDULE_HARDENING", True)
    monkeypatch.setattr(C, "ENABLE_V324A_SCHEDULE_INTEGRATION", True)
    fake_schedule_utils = types.ModuleType("schedule_utils")
    fake_schedule_utils.load_schedule = lambda: schedule
    fake_schedule_utils.match_courier = (
        lambda name, current: name if name in current else None
    )
    # Confirm the repo CID path ignores the known-wrong external boolean.
    fake_schedule_utils.is_on_shift = lambda *_: (False, "wrong host boolean")
    fake_schedule_utils.is_schedule_stale = lambda: False
    monkeypatch.setitem(sys.modules, "schedule_utils", fake_schedule_utils)

    class _FrozenDateTime(datetime):
        @classmethod
        def now(cls, tz=None):
            if tz is None:
                return now_utc.replace(tzinfo=None)
            return now_utc.astimezone(tz)

    monkeypatch.setattr(CR, "datetime", _FrozenDateTime)
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
    hard_window = CR.resolve_effective_shift_window_by_cid(
        CID,
        name=NAME,
        schedule=schedule,
        now=now_utc,
    )

    assert courier.shift_end == expected_end
    assert hard_window.end_at == courier.shift_end
    assert hard_window.interval == courier.effective_shift_window.interval

    breaches = PR._operator_pin_hard_report(
        CID,
        [
            {
                "type": "pickup",
                "order_id": "NEW",
                "predicted_at": (expected_end + timedelta(minutes=1)).isoformat(),
            }
        ],
        {},
        now=now_utc,
    )
    assert {
        "type": "grafik",
        "order_id": "NEW",
        "value": 1.0,
        "stop_type": "pickup",
    } in breaches

    # Removing the schedule makes the same assignment windowless. Existing bag
    # remains in the fleet; only a later NEW proposal is rejected by feasibility.
    fake_schedule_utils.load_schedule = lambda: {}
    carried = {"order_id": "CARRIED", "status": "picked_up"}
    windowless = CR.dispatchable_fleet(
        {
            CID: CourierState(
                courier_id=CID,
                name=NAME,
                pos=POS,
                pos_source="gps",
                bag=[carried],
            )
        }
    )[0]
    assert windowless.bag == [carried]
    assert (
        windowless.shift_end_status
        is CA.ShiftEndStatus.UNKNOWN_WINDOWLESS_ASSIGNMENT
    )
    assert windowless.shift_end is None


@pytest.mark.parametrize("schedule_hardening", [False, True])
def test_windowless_assignment_is_not_fail12_schedule_outage(
    monkeypatch,
    schedule_hardening,
):
    now = OVERNIGHT_NOW.astimezone(timezone.utc)
    decision = _resolve(
        _context(
            operator=_assignment_record(),
            schedule={},
        ),
        host_on_shift=False,
    )

    assert decision.dispatchable is True
    assert decision.effective_shift_window.end_at is None
    assert (
        decision.effective_shift_window.end_status
        is CA.ShiftEndStatus.UNKNOWN_WINDOWLESS_ASSIGNMENT
    )

    monkeypatch.setattr(
        C,
        "ENABLE_V325_SCHEDULE_HARDENING",
        schedule_hardening,
    )
    monkeypatch.setattr(C, "ENABLE_D2_STALE_SCHEDULE_SOFT", False)
    monkeypatch.setattr(C, "ENABLE_FAIL12_SCHEDULE_FAILOPEN", True)
    carried = _order("CARRIED", now - timedelta(minutes=5))
    carried.status = "picked_up"
    carried.picked_up_at = now - timedelta(minutes=5)
    verdict, reason, metrics, plan = F.check_feasibility_v2(
        courier_pos=POS,
        bag=[carried],
        new_order=_order("NEW", now + timedelta(minutes=10)),
        shift_end=None,
        shift_end_status=decision.effective_shift_window.end_status,
        now=now,
        pickup_ready_at=now + timedelta(minutes=10),
        pos_source="gps",
    )

    assert verdict == "NO"
    assert plan is None
    assert "GRAFIK_UNKNOWN" in reason
    assert metrics["grafik_unknown"] is True
    assert metrics["v325_reject_reason"] == "GRAFIK_UNKNOWN"
    assert "fail12_schedule_failopen" not in metrics
    assert "fail12_signal" not in metrics


def test_synthetic_pre_shift_updates_metadata_without_overwriting_real_gps():
    courier = CourierState(
        courier_id=CID,
        pos=POS,
        pos_source="gps",
    )

    CR._synthetic_pos_fallback(
        courier,
        "pre_shift",
        shift_start_min=37.5,
    )

    assert courier.pos == POS
    assert courier.pos_source == "gps"
    assert courier.shift_start_min == 37.5


def test_synthetic_pre_shift_relabels_existing_unknown_no_gps_sentinel():
    courier = CourierState(
        courier_id=CID,
        pos=CR.BIALYSTOK_CENTER,
        pos_source="no_gps",
    )

    CR._synthetic_pos_fallback(
        courier,
        "pre_shift",
        shift_start_min=37.5,
    )

    assert courier.pos == CR.BIALYSTOK_CENTER
    assert courier.pos_source == "pre_shift"
    assert courier.shift_start_min == 37.5


def test_typed_console_mutations_order_concurrent_on_then_stop(tmp_path):
    store = tmp_path / "manual_overrides.json"
    _write_store(store, {})
    on_at = datetime(2026, 7, 29, 18, 0, tzinfo=timezone.utc)
    stop_at = on_at + timedelta(seconds=1)
    on = CA.ConsoleAvailabilityMutation.on(
        CID,
        NAME,
        working_entry={
            "start": "20:00",
            "end": "24:00",
            "end_explicit": False,
            "name": NAME,
            "added_at": on_at.isoformat(),
        },
        operator_window={
            "start": "20:00",
            "end": "24:00",
            "end_explicit": False,
        },
        aliases=(NAME,),
        at=on_at,
    )
    stop = CA.ConsoleAvailabilityMutation.off(
        CID,
        NAME,
        at=stop_at,
    )
    barrier = threading.Barrier(2)

    def delayed_on():
        barrier.wait()
        time.sleep(0.03)
        return CA.commit_console_mutation(on, path=str(store))

    def early_stop():
        barrier.wait()
        return CA.commit_console_mutation(stop, path=str(store))

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = [
            pool.submit(delayed_on),
            pool.submit(early_stop),
        ]
        for future in results:
            future.result(timeout=3)

    payload = json.loads(store.read_text(encoding="utf-8"))
    assert payload[CA.STORE_KEY][CID]["state"] == "OPERATOR_OFF"
    assert CID in payload["excluded_cids"]
    assert CID not in payload["working"]


def test_console_mutation_retries_on_fresh_snapshot_conflict(
    monkeypatch,
    tmp_path,
):
    store = tmp_path / "manual_overrides.json"
    _write_store(store, {})
    mutation = CA.ConsoleAvailabilityMutation.off(
        CID,
        NAME,
        at=datetime(2026, 7, 29, 18, 0, tzinfo=timezone.utc),
    )
    real_read = CA._read_json_dict
    calls = 0

    def one_external_conflict(path):
        nonlocal calls
        calls += 1
        payload, error = real_read(path)
        if calls == 2:
            return {**payload, "external_generation": 1}, error
        return payload, error

    monkeypatch.setattr(CA, "_read_json_dict", one_external_conflict)
    result = CA.commit_console_mutation(
        mutation,
        path=str(store),
        max_attempts=3,
    )

    assert result.applied is True
    assert result.attempts == 2
    assert calls == 4
    payload = json.loads(store.read_text(encoding="utf-8"))
    assert payload[CA.STORE_KEY][CID]["state"] == "OPERATOR_OFF"


def test_mutation_dropping_windowless_status_reenables_fail12(monkeypatch):
    now = OVERNIGHT_NOW.astimezone(timezone.utc)
    monkeypatch.setattr(C, "ENABLE_V325_SCHEDULE_HARDENING", True)
    monkeypatch.setattr(C, "ENABLE_D2_STALE_SCHEDULE_SOFT", False)
    monkeypatch.setattr(C, "ENABLE_FAIL12_SCHEDULE_FAILOPEN", True)
    carried = _order("CARRIED", now - timedelta(minutes=5))
    carried.status = "picked_up"
    carried.picked_up_at = now - timedelta(minutes=5)

    _verdict, _reason, metrics, _plan = F.check_feasibility_v2(
        courier_pos=POS,
        bag=[carried],
        new_order=_order("NEW", now + timedelta(minutes=10)),
        shift_end=None,
        # Mutant: producer status dropped at the boundary.
        shift_end_status=None,
        now=now,
        pickup_ready_at=now + timedelta(minutes=10),
        pos_source="gps",
    )

    assert metrics["fail12_schedule_failopen"] is True
    assert metrics["fail12_signal"] == "bag"
    assert "grafik_unknown" not in metrics


def test_producer_status_reaches_both_canonical_feasibility_consumers():
    for relative in ("core/candidates.py", "core/selection.py"):
        tree = ast.parse((ROOT / relative).read_text(encoding="utf-8"))
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and (
                (
                    isinstance(node.func, ast.Name)
                    and node.func.id == "check_feasibility_v2"
                )
                or (
                    isinstance(node.func, ast.Attribute)
                    and node.func.attr == "check_feasibility_v2"
                )
            )
        ]
        assert len(calls) == 1
        keyword_names = {keyword.arg for keyword in calls[0].keywords}
        assert "shift_end_status" in keyword_names


def test_resolver_has_no_second_hhmm_parser_or_clock_arithmetic():
    source = (ROOT / "courier_resolver.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    guarded = {
        "_mins_to_shift_start",
        "_shift_start_dt",
        "_shift_end_dt",
        "effective_shift_end",
        "_operator_on_shift_window",
        "resolve_effective_shift_window_by_cid",
    }
    functions = {
        node.name: node
        for node in ast.walk(tree)
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name in guarded
    }

    assert guarded <= functions.keys()
    for name, node in functions.items():
        calls = list(ast.walk(node))
        assert not any(
            isinstance(item, ast.Call)
            and isinstance(item.func, ast.Attribute)
            and item.func.attr == "split"
            for item in calls
        ), name
        assert not any(
            isinstance(item, ast.Call)
            and isinstance(item.func, ast.Attribute)
            and item.func.attr == "replace"
            and any(keyword.arg in {"hour", "minute"} for keyword in item.keywords)
            for item in calls
        ), name


def test_synthetic_position_owner_guards_real_provenance_before_assignment():
    tree = ast.parse((ROOT / "courier_resolver.py").read_text(encoding="utf-8"))
    helper = next(
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.FunctionDef)
        and node.name == "_synthetic_pos_fallback"
    )
    guard = next(
        node
        for node in helper.body
        if isinstance(node, ast.If)
        and any(isinstance(item, ast.Return) for item in node.body)
    )
    position_assignments = [
        node
        for node in ast.walk(helper)
        if isinstance(node, ast.Assign)
        and any(
            isinstance(target, ast.Attribute)
            and isinstance(target.value, ast.Name)
            and target.value.id == "cs"
            and target.attr in {"pos", "pos_source"}
            for target in node.targets
        )
    ]

    assert len(position_assignments) == 2
    assert all(node.lineno > guard.lineno for node in position_assignments)
    assert any(
        isinstance(item, ast.Call)
        and isinstance(item.func, ast.Name)
        and item.func.id == "is_position_known"
        for item in ast.walk(guard.test)
    )
