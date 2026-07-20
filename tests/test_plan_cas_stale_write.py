"""Z-P0-04: deterministyczne CAS, inwalidacje i polityka keep-current."""
from datetime import datetime, timezone
import json

import pytest

from dispatch_v2 import plan_manager as PM
from dispatch_v2 import plan_recheck as PR
from dispatch_v2 import pending_proposals_store as PPS
from dispatch_v2 import panel_watcher as PW


def _body(tag: str):
    return {
        "start_pos": {"lat": 53.13, "lng": 23.15, "source": tag},
        "start_ts": "2026-07-09T12:00:00+00:00",
        "stops": [{
            "order_id": tag,
            "type": "dropoff",
            "coords": {"lat": 53.14, "lng": 23.16},
            "dwell_min": 1.0,
            "status_at_plan_time": "assigned",
        }],
        "optimization_method": "incremental",
    }


def _pending_record(expected_version):
    best = {
        "courier_id": "9",
        "pos_source": "gps",
        "plan": {
            "sequence": ["NEW"],
            "predicted_delivered_at": {
                "NEW": "2026-07-09T12:30:00+00:00",
            },
            "pickup_at": {},
            "strategy": "incremental",
        },
        "bag_context": [],
    }
    if expected_version is not None:
        best["plan_expected_version"] = expected_version
    return {"NEW": {"decision_record": {"best": best}}}


@pytest.fixture
def isolated_store(tmp_path, monkeypatch):
    monkeypatch.setattr(PM, "PLANS_FILE", tmp_path / "courier_plans.json")
    monkeypatch.setattr(PM, "LOCK_FILE", tmp_path / "courier_plans.lock")
    with PM._perf_plans_lock:
        PM._perf_plans_cache["key"] = None
        PM._perf_plans_cache["data"] = None
    return tmp_path


def test_writer_b_survives_stale_writer_a(isolated_store):
    """Przeplot: A czyta v1, B zapisuje v2, spozniony A nie klobruje B."""
    writer_a_version = PM.save_plan("9", _body("base"))["plan_version"]
    before_conflicts = PM.cas_conflicts_total()

    saved_b = PM.save_plan(
        "9", _body("writer-b"), expected_version=writer_a_version)
    assert saved_b["plan_version"] == 2

    with pytest.raises(PM.ConcurrencyError) as exc:
        PM.save_plan(
            "9", _body("writer-a-stale"),
            expected_version=writer_a_version,
        )

    final = PM.load_plan("9")
    assert final["plan_version"] == 2
    assert final["start_pos"]["source"] == "writer-b"
    assert exc.value.expected_version == 1
    assert exc.value.current_version == 2
    assert PM.cas_conflicts_total() == before_conflicts + 1


def test_newer_writer_survives_stale_invalidator(isolated_store):
    """Read v1 -> writer zapisuje v2 -> spozniona invalidacja v1 robi skip."""
    stale_version = PM.save_plan("9", _body("stale-view"))["plan_version"]
    saved_newer = PM.save_plan(
        "9", _body("newer-current"), expected_version=stale_version)
    before_conflicts = PM.cas_conflicts_total()

    with pytest.raises(PM.ConcurrencyError) as exc:
        PM.invalidate_plan(
            "9", "BAG_CHANGED", expected_version=stale_version)

    current = PM.load_plans()["9"]
    assert current["plan_version"] == saved_newer["plan_version"] == 2
    assert current["invalidated_at"] is None
    assert current["start_pos"]["source"] == "newer-current"
    assert exc.value.expected_version == 1
    assert exc.value.current_version == 2
    assert PM.cas_conflicts_total() == before_conflicts + 1


def test_pending_entry_preserves_plan_expected_version():
    now = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)
    record = {"best": {"plan_expected_version": 12}}
    entry = PPS.build_entry(record, now)
    assert entry["decision_record"]["best"]["plan_expected_version"] == 12


def test_panel_assignment_conflict_keeps_current_plan(
        isolated_store, tmp_path, monkeypatch):
    PM.save_plan("9", _body("newer-current"))  # current v1; proposal oczekuje v0
    pending = tmp_path / "pending.json"
    pending.write_text(json.dumps(_pending_record(0)), encoding="utf-8")
    monkeypatch.setattr(PW, "_PENDING_PROPOSALS_PATH", str(pending))
    monkeypatch.setattr(PW.C, "ENABLE_SAVED_PLANS", True)
    monkeypatch.setattr(PW.C, "decision_flag", lambda *a, **k: False)

    PW._save_plan_on_assign("NEW", "9")

    final = PM.load_plan("9")
    assert final["plan_version"] == 1
    assert final["start_pos"]["source"] == "newer-current"


def test_panel_assignment_without_token_is_safe_skip(
        isolated_store, tmp_path, monkeypatch):
    pending = tmp_path / "pending.json"
    pending.write_text(json.dumps(_pending_record(None)), encoding="utf-8")
    monkeypatch.setattr(PW, "_PENDING_PROPOSALS_PATH", str(pending))
    monkeypatch.setattr(PW.C, "ENABLE_SAVED_PLANS", True)

    PW._save_plan_on_assign("NEW", "9")

    assert PM.load_plans() == {}


def test_panel_bag_change_stale_snapshot_keeps_newer_covering_plan(
        isolated_store, monkeypatch):
    """Watcher przekazuje wersje odczytanego planu do invalidacji."""
    from dispatch_v2 import common

    PM.save_plan("9", _body("OLD"))
    stale_snapshot = PM.load_plan("9")
    PM.save_plan("9", _body("NEW"), expected_version=1)
    before_conflicts = PM.cas_conflicts_total()

    monkeypatch.setattr(common, "ENABLE_SAVED_PLANS", True)
    monkeypatch.setattr(common, "flag", lambda *a, **k: True)
    monkeypatch.setattr(PM, "load_plan", lambda *a, **k: stale_snapshot)

    PW._invalidate_plan_on_bag_change("NEW", "9")

    current = PM.load_plans()["9"]
    assert current["plan_version"] == 2
    assert current["invalidated_at"] is None
    assert current["start_pos"]["source"] == "NEW"
    assert PM.cas_conflicts_total() == before_conflicts + 1


def test_recheck_refreshes_after_refloor_and_stale_invalidation_skips(
        isolated_store, monkeypatch):
    """Recheck analizuje v2 po refloor; writer v3 przed invalidacja wygrywa."""
    initial = _body("ORDER")
    initial["stops"].insert(0, {
        "order_id": "ORDER",
        "type": "pickup",
        "coords": {"lat": 53.13, "lng": 23.15},
        "predicted_at": "2026-07-09T12:10:00+00:00",
        "dwell_min": 2.0,
        "status_at_plan_time": "assigned",
    })
    PM.save_plan("9", initial)

    checked_versions = []

    def _refloor(cid, oid, floor):
        assert PM.load_plans()[cid]["plan_version"] == 1
        PM.save_plan(cid, _body("refloored"), expected_version=1)
        return 5.0

    def _check(cid, plan, orders_state, gps_positions, now):
        checked_versions.append(plan["plan_version"])
        PM.save_plan(cid, _body("newer-current"), expected_version=2)
        return {
            "issues": ["terminal_status:ORDER=cancelled"],
            "auto_invalidate_reason": "ORDER_CANCELLED",
            "gps_drift": None,
        }

    monkeypatch.setattr(PR, "_refresh_d3_fala_a_flags", lambda: None)
    monkeypatch.setattr(PR, "_now_utc", lambda: datetime(
        2026, 7, 9, 12, 0, tzinfo=timezone.utc))
    monkeypatch.setattr(PR, "_load_orders_state", lambda: {
        "ORDER": {
            "status": "assigned",
            "courier_id": "9",
            "czas_kuriera_warsaw": "2026-07-09T14:20:00+02:00",
        },
    })
    monkeypatch.setattr(PR, "_load_gps_positions", lambda: {})
    monkeypatch.setattr(PR, "_check_plan", _check)
    monkeypatch.setattr(PR, "_log_recheck_entry", lambda finding: None)
    monkeypatch.setattr(PR, "_l3_maybe_gc", lambda *a, **k: None)
    monkeypatch.setattr(PR, "ENABLE_PICKUP_REFLOOR", True)
    monkeypatch.setattr(PR, "AUTO_INVALIDATE_STALE", True)
    monkeypatch.setattr(PR, "ENABLE_GPS_DRIFT_INVALIDATION", False)
    monkeypatch.setattr(PR, "ENABLE_PLAN_FOR_ACTUAL_BAG", False)
    monkeypatch.setattr(PR, "ENABLE_PLAN_RECHECK_LIVE_ETA_REFRESH", False)
    monkeypatch.setattr(PM, "refloor_pickup", _refloor)

    summary = PR.run_recheck()

    current = PM.load_plans()["9"]
    assert checked_versions == [2]
    assert current["plan_version"] == 3
    assert current["invalidated_at"] is None
    assert current["start_pos"]["source"] == "newer-current"
    assert summary["auto_invalidated"] == 0
    assert summary["plan_cas_conflicts"] == 1


@pytest.mark.parametrize("mutation", ["invalidate", "advance_last", "remove_last"])
def test_every_terminal_invalidation_bumps_version_and_blocks_stale_save(
        isolated_store, mutation):
    stale_version = PM.save_plan("11", _body("ORDER"))["plan_version"]

    if mutation == "invalidate":
        PM.invalidate_plan("11", "BAG_CHANGED")
    elif mutation == "advance_last":
        PM.advance_plan(
            "11", "ORDER", datetime.now(timezone.utc).isoformat())
    else:
        PM.remove_stops("11", "ORDER")

    invalidated = PM.load_plans()["11"]
    assert invalidated["invalidated_at"] is not None
    assert invalidated["plan_version"] == stale_version + 1
    if mutation == "remove_last":
        assert invalidated["invalidation_reason"] == "NO_STOPS_REMAINING"
        assert invalidated["invalidation_reason"] in PM.INVALIDATION_REASONS

    with pytest.raises(PM.ConcurrencyError):
        PM.save_plan(
            "11", _body("stale-resurrection"),
            expected_version=stale_version,
        )
    still_invalidated = PM.load_plans()["11"]
    assert still_invalidated["invalidated_at"] is not None
    assert still_invalidated["plan_version"] == stale_version + 1


def test_retime_conflict_never_falls_through_to_full_gen(monkeypatch):
    now = datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc)
    orders = {"O1": {"courier_id": "9", "status": "assigned"}}
    plans = {"9": {
        "plan_version": 4,
        "invalidated_at": None,
        "bag_signature": "same",
        "stops": [{"order_id": "O1", "type": "dropoff"}],
    }}
    generated = []

    monkeypatch.setattr(PR, "ENABLE_PLAN_SEQUENCE_LOCK", True)
    monkeypatch.setattr(PR, "_bag_signature", lambda *a, **k: "same")
    monkeypatch.setattr(
        PR, "_retime_one_bag_plan",
        lambda *a, **k: (_ for _ in ()).throw(
            PM.ConcurrencyError("9", 4, 5)),
    )
    monkeypatch.setattr(
        PR, "_gen_one_bag_plan",
        lambda *a, **k: generated.append((a, k)) or True,
    )

    summary = {}
    PR._gap_fill_plans(orders, plans, {}, now, summary)

    assert generated == []
    assert summary["bag_plans_retimed"] == 0
    assert summary["bag_plans_generated"] == 0
    assert summary["bag_plans_skipped"] == 1


@pytest.mark.parametrize("module_name", [
    "dispatch_v2.tools.bundle_calib_shadow",
    "dispatch_v2.tools.b_route_shadow",
])
def test_shadow_full_retsp_uses_zero_version_for_fresh_isolated_store(
        isolated_store, monkeypatch, module_name):
    """Oba timery B przekazuja CAS=0 po wyzerowaniu swojego temp store."""
    import importlib

    tool = importlib.import_module(module_name)
    calls = []

    def _gen(*args, **kwargs):
        calls.append((args, kwargs))
        return True

    monkeypatch.setattr(tool.P, "_gen_one_bag_plan", _gen)
    monkeypatch.setattr(tool.PM, "load_plan", lambda cid: {
        "stops": [{"order_id": "O1", "type": "dropoff"}],
    })

    result = tool._b_full_retsp(
        "9",
        ["O1"],
        {"O1": {"status": "picked_up"}},
        (53.13, 23.15),
        datetime(2026, 7, 9, 12, 0, tzinfo=timezone.utc),
    )

    assert result == [{"order_id": "O1", "type": "dropoff"}]
    assert len(calls) == 1
    assert calls[0][1]["expected_version"] == 0
