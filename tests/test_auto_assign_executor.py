"""AUTON-01 — testy egzekutora auto-assign (szkielet ZA FLAGĄ, default OFF).

Kontrakt krytyczny: przy ENABLE_AUTO_ASSIGN=false maybe_execute NIE wykonuje
NIC (zero wywołań runnera/notifiera, zero I/O stanu). Bezpieczniki stanowe
(rate-cap, cooldown po PANEL_OVERRIDE) działają w chwili wykonania.
"""
import json
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from dispatch_v2 import common as C
from dispatch_v2 import auto_assign_executor as E

NOW = datetime(2026, 6, 13, 3, 0, tzinfo=timezone.utc)


def _record(verdict="PROPOSE", oid="480300", cid="101", name="Kurier Testowy",
            target_min=12):
    tgt = (NOW + timedelta(minutes=target_min)).isoformat()
    return {
        "verdict": verdict,
        "order_id": oid,
        "best": {"courier_id": cid, "name": name, "score": 55.0,
                 "target_pickup_at": tgt},
    }


def _result(would=True):
    return SimpleNamespace(would_auto_assign=would)


@pytest.fixture
def runner_spy():
    calls = []
    def runner(oid, name, minutes):
        calls.append((oid, name, minutes))
        return True, "ok"
    runner.calls = calls
    return runner


@pytest.fixture
def notify_spy():
    msgs = []
    def notify(text):
        msgs.append(text)
    notify.msgs = msgs
    return notify


@pytest.fixture
def state_path(tmp_path):
    return str(tmp_path / "auto_assign_state.json")


@pytest.fixture
def isolated_llog(tmp_path, monkeypatch):
    p = tmp_path / "learning_log.jsonl"
    p.write_text("")
    monkeypatch.setattr(E, "LEARNING_LOG_PATH", str(p))
    return p


# ---------------- killswitch OFF (kontrakt zerowego zachowania) ----------------

def test_flag_off_returns_none(runner_spy, notify_spy, state_path, isolated_llog):
    out = E.maybe_execute(_record(), _result(), {}, now=NOW,
                          assign_runner=runner_spy, notifier=notify_spy,
                          state_path=state_path)
    assert out is None
    assert runner_spy.calls == []
    assert notify_spy.msgs == []
    import os
    assert not os.path.exists(state_path)


def test_flag_off_even_when_would_auto(runner_spy, notify_spy, state_path,
                                       isolated_llog):
    assert C.decision_flag("ENABLE_AUTO_ASSIGN") is False
    out = E.maybe_execute(_record(), _result(would=True), {}, now=NOW,
                          assign_runner=runner_spy, notifier=notify_spy,
                          state_path=state_path)
    assert out is None and runner_spy.calls == []


# ---------------- flaga ON (patch stałej modułu — conftest wycina klucz) ----------------

@pytest.fixture
def flag_on(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_AUTO_ASSIGN", True)


def test_on_gate_false_returns_none(flag_on, runner_spy, notify_spy,
                                    state_path, isolated_llog):
    out = E.maybe_execute(_record(), _result(would=False), {}, now=NOW,
                          assign_runner=runner_spy, notifier=notify_spy,
                          state_path=state_path)
    assert out is None and runner_spy.calls == []


def test_on_record_not_propose_blocked(flag_on, runner_spy, notify_spy,
                                       state_path, isolated_llog):
    out = E.maybe_execute(_record(verdict="SUPPRESSED_FIRMOWE_KONTO"),
                          _result(), {}, now=NOW,
                          assign_runner=runner_spy, notifier=notify_spy,
                          state_path=state_path)
    assert out == {"blocked": "record_verdict_not_propose"}
    assert runner_spy.calls == []


def test_on_missing_name_blocked(flag_on, runner_spy, notify_spy,
                                 state_path, isolated_llog):
    rec = _record()
    rec["best"]["name"] = None
    out = E.maybe_execute(rec, _result(), {}, now=NOW,
                          assign_runner=runner_spy, notifier=notify_spy,
                          state_path=state_path)
    assert out == {"blocked": "missing_oid_cid_or_name"}


def test_on_happy_path_executes(flag_on, runner_spy, notify_spy,
                                state_path, isolated_llog):
    out = E.maybe_execute(_record(target_min=12), _result(), {}, now=NOW,
                          assign_runner=runner_spy, notifier=notify_spy,
                          state_path=state_path)
    assert out["executed"] is True
    assert runner_spy.calls == [("480300", "Kurier Testowy", 12)]
    assert len(notify_spy.msgs) == 1
    assert "AUTO-ASSIGN" in notify_spy.msgs[0]
    assert "480300" in notify_spy.msgs[0]


def test_on_target_in_past_gives_zero_minutes(flag_on, runner_spy, notify_spy,
                                              state_path, isolated_llog):
    E.maybe_execute(_record(target_min=-10), _result(), {}, now=NOW,
                    assign_runner=runner_spy, notifier=notify_spy,
                    state_path=state_path)
    assert runner_spy.calls[0][2] == 0


def test_on_runner_failure_no_state_but_notifies(flag_on, notify_spy,
                                                 state_path, isolated_llog):
    def failing(oid, name, minutes):
        return False, "exit=1 boom"
    out = E.maybe_execute(_record(), _result(), {}, now=NOW,
                          assign_runner=failing, notifier=notify_spy,
                          state_path=state_path)
    assert out["executed"] is False
    import os
    assert not os.path.exists(state_path)  # nieudane = nie liczy do rate-capu
    assert "nieudane" in notify_spy.msgs[0]


def test_on_runner_exception_fail_safe(flag_on, notify_spy, state_path,
                                       isolated_llog):
    def exploding(oid, name, minutes):
        raise RuntimeError("kaboom")
    out = E.maybe_execute(_record(), _result(), {}, now=NOW,
                          assign_runner=exploding, notifier=notify_spy,
                          state_path=state_path)
    assert out is None  # połknięte z WARN, nigdy nie rzuca


# ---------------- rate-cap ----------------

def test_rate_cap_blocks(flag_on, runner_spy, notify_spy, state_path,
                         isolated_llog, monkeypatch, tmp_path):
    monkeypatch.setattr(C, "AUTO_ASSIGN_MAX_PER_HOUR", 2)
    sp = str(tmp_path / "state.json")
    recent = [NOW.timestamp() - 100, NOW.timestamp() - 200]
    with open(sp, "w") as f:
        json.dump({"executed": recent}, f)
    out = E.maybe_execute(_record(), _result(), {}, now=NOW,
                          assign_runner=runner_spy, notifier=notify_spy,
                          state_path=sp)
    assert out == {"blocked": "rate_cap"}
    assert runner_spy.calls == []


def test_rate_cap_ignores_old_entries(flag_on, runner_spy, notify_spy,
                                      isolated_llog, monkeypatch, tmp_path):
    monkeypatch.setattr(C, "AUTO_ASSIGN_MAX_PER_HOUR", 2)
    sp = str(tmp_path / "state.json")
    old = [NOW.timestamp() - 4000, NOW.timestamp() - 5000]
    with open(sp, "w") as f:
        json.dump({"executed": old}, f)
    out = E.maybe_execute(_record(), _result(), {}, now=NOW,
                          assign_runner=runner_spy, notifier=notify_spy,
                          state_path=sp)
    assert out["executed"] is True


# ---------------- cooldown po PANEL_OVERRIDE ----------------

def _override_line(cid, ts):
    return json.dumps({
        "ts": ts.isoformat(), "order_id": "479999", "action": "PANEL_OVERRIDE",
        "proposed_courier_id": str(cid), "actual_courier_id": "999",
    }) + "\n"


def test_cooldown_blocks_recent_override(flag_on, runner_spy, notify_spy,
                                         state_path, isolated_llog):
    isolated_llog.write_text(_override_line("101", NOW - timedelta(minutes=15)))
    out = E.maybe_execute(_record(cid="101"), _result(), {}, now=NOW,
                          assign_runner=runner_spy, notifier=notify_spy,
                          state_path=state_path)
    assert out == {"blocked": "override_cooldown", "courier_id": "101"}
    assert runner_spy.calls == []


def test_cooldown_matches_actual_courier_too(flag_on, runner_spy, notify_spy,
                                             state_path, isolated_llog):
    line = json.dumps({
        "ts": (NOW - timedelta(minutes=5)).isoformat(),
        "action": "PANEL_OVERRIDE",
        "proposed_courier_id": "777", "actual_courier_id": "101",
    }) + "\n"
    isolated_llog.write_text(line)
    out = E.maybe_execute(_record(cid="101"), _result(), {}, now=NOW,
                          assign_runner=runner_spy, notifier=notify_spy,
                          state_path=state_path)
    assert out == {"blocked": "override_cooldown", "courier_id": "101"}


def test_cooldown_expired_override_passes(flag_on, runner_spy, notify_spy,
                                          state_path, isolated_llog,
                                          monkeypatch):
    monkeypatch.setattr(C, "AUTO_ASSIGN_OVERRIDE_COOLDOWN_MIN", 60.0)
    isolated_llog.write_text(_override_line("101", NOW - timedelta(minutes=90)))
    out = E.maybe_execute(_record(cid="101"), _result(), {}, now=NOW,
                          assign_runner=runner_spy, notifier=notify_spy,
                          state_path=state_path)
    assert out["executed"] is True


def test_cooldown_other_courier_passes(flag_on, runner_spy, notify_spy,
                                       state_path, isolated_llog):
    isolated_llog.write_text(_override_line("555", NOW - timedelta(minutes=5)))
    out = E.maybe_execute(_record(cid="101"), _result(), {}, now=NOW,
                          assign_runner=runner_spy, notifier=notify_spy,
                          state_path=state_path)
    assert out["executed"] is True


def test_cooldown_missing_log_fail_open(flag_on, runner_spy, notify_spy,
                                        state_path, monkeypatch, tmp_path):
    monkeypatch.setattr(E, "LEARNING_LOG_PATH", str(tmp_path / "nope.jsonl"))
    out = E.maybe_execute(_record(), _result(), {}, now=NOW,
                          assign_runner=runner_spy, notifier=notify_spy,
                          state_path=state_path)
    assert out["executed"] is True


# ---------------- ochrona przed testami / prod ----------------

def test_default_runner_refuses_under_pytest():
    ok, msg = E._default_assign_runner("480300", "Kurier", 5)
    assert ok is False
    assert msg == "blocked_pytest_context"


def test_state_writer_refuses_under_pytest(tmp_path):
    sp = str(tmp_path / "state.json")
    E._save_state(sp, {"executed": [1.0]})
    import os
    assert not os.path.exists(sp)  # writer odmawia pod PYTEST_CURRENT_TEST
