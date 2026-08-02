"""Load-bearing gate czasu odbioru i planowego Alarmu dla case 491870."""
from __future__ import annotations

import copy
import inspect
from datetime import datetime, timedelta, timezone

from dispatch_v2 import common as C
from dispatch_v2 import panel_watcher
from dispatch_v2 import pickup_lateness_shadow
from dispatch_v2 import plan_manager as PM
from dispatch_v2 import plan_recheck as PR
from dispatch_v2.core import pickup_time_rules as R


NOW = datetime(2026, 8, 2, 18, 0, tzinfo=timezone.utc)
COMMITTED = "2026-08-02T18:00:00+00:00"


def _pred(minutes: float) -> str:
    return (NOW + timedelta(minutes=minutes)).isoformat()


def _orders(status: str = "assigned"):
    return {
        "491870": {
            "status": status,
            "courier_id": "492",
            "czas_kuriera_warsaw": COMMITTED,
        }
    }


def _plan(minutes: float = 46.0):
    evaluation = R.evaluate_plan(
        "492",
        [{"order_id": "491870", "type": "pickup", "predicted_at": _pred(minutes)}],
        _orders(),
        NOW,
        source="test",
    )
    return {
        "stops": [
            {"order_id": "491870", "type": "pickup", "predicted_at": _pred(minutes)}
        ],
        "pickup_time_rules": evaluation,
    }


def test_alarm_boundary_is_strictly_greater_than_40_and_only_for_lateness():
    at_limit = R.evaluate_committed_pickup("A", COMMITTED, _pred(40.0))
    over_limit = R.evaluate_committed_pickup("A", COMMITTED, _pred(40.001))
    early = R.evaluate_committed_pickup("A", COMMITTED, _pred(-41.0))

    assert at_limit["plan_alarm"] is False
    assert over_limit["plan_alarm"] is True
    assert early["r27_strict_breach"] is True
    assert early["direction"] == "pickup_early"
    assert early["plan_alarm"] is False


def test_alarm_threshold_mutation_changes_oracle(monkeypatch):
    """Mutation: przesunięcie dialu 40→46 musi zgasić case +46."""
    assert R.evaluate_committed_pickup("491870", COMMITTED, _pred(46.0))["plan_alarm"] is True
    monkeypatch.setattr(C, "PICKUP_PLAN_ALARM_LATE_MIN", 46.0)
    assert R.evaluate_committed_pickup("491870", COMMITTED, _pred(46.0))["plan_alarm"] is False
    assert R.evaluate_committed_pickup("491870", COMMITTED, _pred(46.001))["plan_alarm"] is True


def test_naive_committed_is_warsaw_not_utc():
    # 20:00 Warsaw = 18:00 UTC; błędna interpretacja jako UTC dawałaby -120 min.
    row = R.evaluate_committed_pickup(
        "491870", "2026-08-02 20:00", "2026-08-02T18:46:00+00:00"
    )
    assert row["delta_min"] == 46.0
    assert row["plan_alarm"] is True


def test_evaluate_plan_emits_non_pii_alarm_contract_and_missing_is_explicit():
    orders = {
        **_orders(),
        "done": {"status": "picked_up", "czas_kuriera_warsaw": COMMITTED},
        "missing": {"status": "assigned", "czas_kuriera_warsaw": COMMITTED},
    }
    out = R.evaluate_plan(
        "492",
        [
            {"order_id": "491870", "type": "pickup", "predicted_at": _pred(46)},
            {"order_id": "done", "type": "pickup", "predicted_at": _pred(80)},
            {"order_id": "missing", "type": "pickup"},
            {"order_id": "491870", "type": "dropoff", "predicted_at": _pred(50)},
        ],
        orders,
        NOW,
        source="regen:operator_override",
    )

    assert out["schema"] == R.SCHEMA
    assert out["source"] == "regen:operator_override"
    assert out["evaluated_count"] == 1
    assert out["missing_order_ids"] == ["missing"]
    assert out["alarm_count"] == 1
    event = out["alarms"][0]
    assert event == {
        "event_class": "Alarm",
        "event_type": "PICKUP_COMMITTED_LATE",
        "channel": "coordinator_console",
        "requires_coordinator_confirmation": True,
        "courier_id": "492",
        "order_id": "491870",
        "committed_at": COMMITTED,
        "predicted_at": "2026-08-02T18:46:00+00:00",
        "lateness_min": 46.0,
        "threshold_min": 40.0,
    }
    assert not ({"restaurant", "address", "phone", "name"} & set(event))


def test_active_alarm_contract_mutations_and_terminal_state_are_fail_closed():
    plan = _plan()
    assert len(R.active_alarm_events(plan, _orders())) == 1

    for key, value in (
        ("event_class", "ALERT"),
        ("event_type", "OTHER"),
        ("channel", "owner_telegram"),
        ("requires_coordinator_confirmation", False),
        ("courier_id", "other"),
        ("lateness_min", 39.0),
        ("threshold_min", 35.0),
    ):
        mutated = copy.deepcopy(plan)
        mutated["pickup_time_rules"]["alarms"][0][key] = value
        assert R.active_alarm_events(mutated, _orders(), courier_id="492") == [], key

    terminal = _orders(status="picked_up")
    assert R.active_alarm_events(plan, terminal) == []
    reassigned = _orders()
    reassigned["491870"]["courier_id"] = "other"
    assert R.active_alarm_events(plan, reassigned, courier_id="492") == []
    missing_pickup = copy.deepcopy(plan)
    missing_pickup["stops"] = []
    assert R.active_alarm_events(missing_pickup, _orders()) == []
    invalidated = copy.deepcopy(plan)
    invalidated["invalidated_at"] = NOW.isoformat()
    assert R.active_alarm_events(invalidated, _orders()) == []


def test_refloor_recomputes_rules_in_same_plan_write(tmp_path, monkeypatch):
    monkeypatch.setattr(PM, "PLANS_FILE", tmp_path / "courier_plans.json")
    monkeypatch.setattr(PM, "LOCK_FILE", tmp_path / "courier_plans.lock")
    PM.save_plan(
        "492",
        {
            "start_pos": {"lat": 52.0, "lng": 20.0, "source": "test"},
            "start_ts": NOW.isoformat(),
            "stops": [
                {"order_id": "491870", "type": "pickup", "predicted_at": COMMITTED,
                 "coords": {"lat": 52.0, "lng": 20.0}}
            ],
            "optimization_method": "incremental",
            "pickup_time_rules": R.evaluate_plan(
                "492",
                [{"order_id": "491870", "type": "pickup", "predicted_at": COMMITTED}],
                _orders(),
                NOW,
                source="initial",
            ),
        },
    )

    shifted = PM.refloor_pickup(
        "492",
        "491870",
        _pred(46),
        orders_state=_orders(),
        now=NOW,
    )
    saved = PM.load_plan("492", invalidate_on_mismatch=False)
    assert shifted == 46.0
    assert saved["pickup_time_rules"]["source"] == "refloor"
    assert saved["pickup_time_rules"]["alarm_count"] == 1


def test_all_time_changing_writers_evaluate_after_final_transform_before_save():
    """Ratchet kompletności writerów: override, regen, retime i refloor."""
    gen = inspect.getsource(PR._gen_one_bag_plan)
    retime = inspect.getsource(PR._retime_one_bag_plan)
    assign = inspect.getsource(panel_watcher._save_plan_on_assign)
    refloor = inspect.getsource(PM.refloor_pickup)

    for source, writer in (("regen", gen), ("retime", retime),
                           ("assign_proposal", assign), ("refloor", refloor)):
        assert "pickup_time_rules" in writer, source
        assert "evaluate_plan" in writer, source
        assert f'\"{source}' in writer, source

    gen_eval = gen.index('body["pickup_time_rules"]')
    assert gen.index("_g4_final_validator") < gen_eval
    assert gen.index("pin_stops") < gen_eval
    assert gen.index("plan_manager.save_plan", gen_eval) > gen_eval

    retime_eval = retime.index('body["pickup_time_rules"]')
    assert retime.index("_g4_final_validator") < retime_eval
    assert retime.index("pin_stops") < retime_eval
    assert retime.index("plan_manager.save_plan", retime_eval) > retime_eval


def test_evaluator_has_no_telegram_or_notify_router_side_effect():
    source = inspect.getsource(R)
    assert "send_admin_alert" not in source
    assert "notify_router" not in source
    assert "telegram_utils" not in source


def test_pickup_lateness_shadow_uses_same_delta_contract():
    row = R.committed_pickup_delta(COMMITTED, _pred(46))
    assert pickup_lateness_shadow._parse_dt(COMMITTED) == R.parse_datetime(COMMITTED)
    assert row["delta_min"] == 46.0
