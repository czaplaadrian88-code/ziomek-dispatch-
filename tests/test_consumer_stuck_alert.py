"""Tests dla consumer_stuck_alert module — Sprint #37 v2 (2026-05-13).

Pure unit tests (no mocking — funkcje pure). Grupy:
A. compute_heartbeat (6)
B. evaluate_stuck_alert state transitions (15)
C. render_telegram_message (6)
D. StuckAlertConfig.from_env (4)
E. append_evaluation_log (3) + sanity (1)
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from dispatch_v2.monitoring.consumer_stuck_alert import (
    DEFAULT_EVALUATIONS_LOG_PATH,
    HeartbeatSnapshot,
    StuckAlertConfig,
    StuckAlertState,
    append_evaluation_log,
    compute_heartbeat,
    evaluate_stuck_alert,
    render_telegram_message,
)


def _make_config(**overrides):
    defaults = dict(
        consumer_id="test_consumer",
        consumer_display_name="Test Consumer",
        event_types=frozenset(["NEW_ORDER"]),
        age_threshold_sec=300,
        pending_threshold=100,
        pending_low_water=30,
        sustain_cycles=2,
        realert_interval_sec=1800,
        heartbeat_interval_sec=60,
        shadow_mode_only=False,
    )
    defaults.update(overrides)
    return StuckAlertConfig(**defaults)


def _stuck_snapshot(age=400.0, pending=150):
    return HeartbeatSnapshot(
        age_sec=age,
        pending=pending,
        worker_alive=False,
        is_stuck=True,
        is_recovered=False,
    )


# ====================== GROUP A: compute_heartbeat ======================

def test_age_sec_zero_when_last_processed_equals_now():
    cfg = _make_config()
    snap = compute_heartbeat(last_processed_ts=1000.0, now=1000.0, pending=0, config=cfg)
    assert snap.age_sec == 0.0


def test_age_sec_positive_when_last_processed_earlier():
    cfg = _make_config()
    snap = compute_heartbeat(last_processed_ts=100.0, now=500.0, pending=0, config=cfg)
    assert snap.age_sec == 400.0


def test_worker_alive_below_age_threshold():
    cfg = _make_config(age_threshold_sec=300)
    snap = compute_heartbeat(last_processed_ts=0.0, now=100.0, pending=0, config=cfg)
    assert snap.worker_alive is True
    assert snap.age_sec == 100.0


def test_is_stuck_requires_both_age_and_pending():
    cfg = _make_config(age_threshold_sec=300, pending_threshold=100)
    snap = compute_heartbeat(last_processed_ts=0.0, now=400.0, pending=50, config=cfg)
    assert snap.is_stuck is False


def test_is_stuck_true_when_both_above():
    cfg = _make_config(age_threshold_sec=300, pending_threshold=100)
    snap = compute_heartbeat(last_processed_ts=0.0, now=400.0, pending=150, config=cfg)
    assert snap.is_stuck is True


def test_is_recovered_when_pending_below_low_water():
    cfg = _make_config(pending_low_water=30)
    snap = compute_heartbeat(last_processed_ts=0.0, now=100.0, pending=10, config=cfg)
    assert snap.is_recovered is True


# ====================== GROUP B: evaluate_stuck_alert ======================

def test_initial_state_no_emit():
    cfg = _make_config()
    state = StuckAlertState()
    snap = HeartbeatSnapshot(age_sec=5.0, pending=50, worker_alive=True, is_stuck=False, is_recovered=False)
    emit, kind, new_state = evaluate_stuck_alert(state, snap, now=100.0, config=cfg)
    assert emit is False
    assert kind is None
    assert new_state.streak == 0
    assert new_state.alert_sent is False


def test_single_is_stuck_no_emit_streak_one():
    cfg = _make_config(sustain_cycles=2)
    state = StuckAlertState()
    emit, kind, new_state = evaluate_stuck_alert(state, _stuck_snapshot(), now=100.0, config=cfg)
    assert emit is False
    assert kind is None
    assert new_state.streak == 1
    assert new_state.alert_sent is False


def test_two_consecutive_stuck_triggers_enter():
    cfg = _make_config(sustain_cycles=2)
    _, _, s1 = evaluate_stuck_alert(StuckAlertState(), _stuck_snapshot(), now=100.0, config=cfg)
    emit, kind, s2 = evaluate_stuck_alert(s1, _stuck_snapshot(), now=160.0, config=cfg)
    assert emit is True
    assert kind == "ENTER"
    assert s2.alert_sent is True
    assert s2.last_alert_ts == 160.0
    assert s2.first_alert_ts == 160.0
    assert s2.streak == 2


def test_streak_resets_on_non_stuck_between():
    cfg = _make_config(sustain_cycles=3)
    _, _, s1 = evaluate_stuck_alert(StuckAlertState(), _stuck_snapshot(), now=100.0, config=cfg)
    assert s1.streak == 1
    healthy = HeartbeatSnapshot(age_sec=5.0, pending=80, worker_alive=True, is_stuck=False, is_recovered=False)
    _, _, s2 = evaluate_stuck_alert(s1, healthy, now=160.0, config=cfg)
    assert s2.streak == 0


def test_lekcja_112_single_event_flap_no_latch_reset():
    """Latched, age=0 ale pending=200 → NIE recovery. Latch zachowany."""
    cfg = _make_config()
    state = StuckAlertState(alert_sent=True, streak=2, last_alert_ts=100.0, first_alert_ts=100.0)
    snap = HeartbeatSnapshot(age_sec=0.0, pending=200, worker_alive=True, is_stuck=False, is_recovered=False)
    emit, kind, new_state = evaluate_stuck_alert(state, snap, now=200.0, config=cfg)
    assert emit is False
    assert kind is None
    assert new_state.alert_sent is True
    assert new_state.first_alert_ts == 100.0


def test_recovery_when_pending_drops_below_low_water():
    cfg = _make_config(pending_low_water=30)
    state = StuckAlertState(alert_sent=True, streak=5, last_alert_ts=100.0, first_alert_ts=100.0)
    snap = HeartbeatSnapshot(age_sec=5.0, pending=10, worker_alive=True, is_stuck=False, is_recovered=True)
    emit, kind, new_state = evaluate_stuck_alert(state, snap, now=500.0, config=cfg)
    assert emit is True
    assert kind == "RECOVERY"
    assert new_state.alert_sent is False
    assert new_state.streak == 0
    assert new_state.last_alert_ts == 0.0
    assert new_state.first_alert_ts == 0.0


def test_sustained_reminder_after_interval():
    cfg = _make_config(realert_interval_sec=1800)
    state = StuckAlertState(alert_sent=True, streak=2, last_alert_ts=100.0, first_alert_ts=100.0)
    emit, kind, new_state = evaluate_stuck_alert(state, _stuck_snapshot(), now=2000.0, config=cfg)
    assert emit is True
    assert kind == "SUSTAINED"
    assert new_state.last_alert_ts == 2000.0
    assert new_state.first_alert_ts == 100.0


def test_sustained_dedup_before_interval():
    cfg = _make_config(realert_interval_sec=1800)
    state = StuckAlertState(alert_sent=True, streak=2, last_alert_ts=100.0, first_alert_ts=100.0)
    emit, kind, _ = evaluate_stuck_alert(state, _stuck_snapshot(), now=500.0, config=cfg)
    assert emit is False
    assert kind is None


def test_recovery_precedence_over_sustained():
    cfg = _make_config()
    state = StuckAlertState(alert_sent=True, streak=2, last_alert_ts=100.0, first_alert_ts=100.0)
    snap = HeartbeatSnapshot(age_sec=400.0, pending=10, worker_alive=False, is_stuck=True, is_recovered=True)
    emit, kind, _ = evaluate_stuck_alert(state, snap, now=5000.0, config=cfg)
    assert emit is True
    assert kind == "RECOVERY"


def test_recovery_only_when_latched():
    cfg = _make_config(sustain_cycles=2)
    state = StuckAlertState(alert_sent=False, streak=1, last_alert_ts=0.0, first_alert_ts=0.0)
    snap = HeartbeatSnapshot(age_sec=400.0, pending=10, worker_alive=False, is_stuck=True, is_recovered=True)
    emit, kind, new_state = evaluate_stuck_alert(state, snap, now=200.0, config=cfg)
    assert emit is True
    assert kind == "ENTER"
    assert new_state.alert_sent is True


def test_enter_state_records_first_alert_ts_equal_last():
    cfg = _make_config(sustain_cycles=1)
    _, kind, state = evaluate_stuck_alert(StuckAlertState(), _stuck_snapshot(), now=777.0, config=cfg)
    assert kind == "ENTER"
    assert state.first_alert_ts == 777.0
    assert state.last_alert_ts == 777.0


def test_sustained_preserves_first_alert_ts():
    cfg = _make_config(realert_interval_sec=1800)
    state = StuckAlertState(alert_sent=True, streak=2, last_alert_ts=100.0, first_alert_ts=100.0)
    _, kind, new_state = evaluate_stuck_alert(state, _stuck_snapshot(), now=2000.0, config=cfg)
    assert kind == "SUSTAINED"
    assert new_state.first_alert_ts == 100.0


def test_recovery_clears_first_alert_ts():
    cfg = _make_config()
    state = StuckAlertState(alert_sent=True, streak=2, last_alert_ts=100.0, first_alert_ts=100.0)
    snap = HeartbeatSnapshot(age_sec=5.0, pending=10, worker_alive=True, is_stuck=False, is_recovered=True)
    _, kind, new_state = evaluate_stuck_alert(state, snap, now=500.0, config=cfg)
    assert kind == "RECOVERY"
    assert new_state.first_alert_ts == 0.0


def test_sustain_cycles_configurable():
    cfg = _make_config(sustain_cycles=3)
    state = StuckAlertState()
    _, kind1, s1 = evaluate_stuck_alert(state, _stuck_snapshot(), now=100.0, config=cfg)
    assert kind1 is None
    _, kind2, s2 = evaluate_stuck_alert(s1, _stuck_snapshot(), now=160.0, config=cfg)
    assert kind2 is None
    _, kind3, s3 = evaluate_stuck_alert(s2, _stuck_snapshot(), now=220.0, config=cfg)
    assert kind3 == "ENTER"
    assert s3.streak == 3


def test_no_emit_when_not_stuck_and_not_latched():
    cfg = _make_config()
    state = StuckAlertState()
    snap = HeartbeatSnapshot(age_sec=5.0, pending=50, worker_alive=True, is_stuck=False, is_recovered=False)
    emit, kind, new_state = evaluate_stuck_alert(state, snap, now=100.0, config=cfg)
    assert emit is False
    assert kind is None
    assert new_state.alert_sent is False


# ====================== GROUP C: render_telegram_message ======================

def test_enter_message_contains_consumer_display_name():
    cfg = _make_config(consumer_display_name="Ziomek shadow worker")
    state = StuckAlertState(alert_sent=True, streak=2, last_alert_ts=100.0, first_alert_ts=100.0)
    msg = render_telegram_message("ENTER", _stuck_snapshot(), state, cfg, now=100.0)
    assert "Ziomek shadow worker" in msg
    assert "STUCK (ENTER)" in msg


def test_enter_message_contains_event_types_label():
    cfg = _make_config(event_types=frozenset(["NEW_ORDER"]))
    msg = render_telegram_message("ENTER", _stuck_snapshot(), StuckAlertState(streak=2), cfg, now=0.0)
    assert "pending NEW_ORDER=" in msg


def test_enter_message_contains_pending_value():
    cfg = _make_config()
    snap = _stuck_snapshot(pending=150)
    msg = render_telegram_message("ENTER", snap, StuckAlertState(streak=2), cfg, now=0.0)
    assert "=150" in msg


def test_sustained_message_includes_elapsed_minutes():
    cfg = _make_config()
    state = StuckAlertState(alert_sent=True, streak=2, last_alert_ts=100.0, first_alert_ts=0.0)
    msg = render_telegram_message("SUSTAINED", _stuck_snapshot(), state, cfg, now=1800.0)
    assert "30 min" in msg


def test_recovery_message_includes_total_stuck_min():
    cfg = _make_config()
    state = StuckAlertState(alert_sent=True, streak=2, last_alert_ts=100.0, first_alert_ts=100.0)
    snap = HeartbeatSnapshot(age_sec=5.0, pending=10, worker_alive=True, is_stuck=False, is_recovered=True)
    msg = render_telegram_message("RECOVERY", snap, state, cfg, now=1900.0)
    assert "30 min" in msg
    assert "RECOVERED" in msg


def test_multiple_event_types_joined_with_plus_sorted():
    cfg = _make_config(event_types=frozenset(["COURIER_PICKED_UP", "COURIER_DELIVERED"]))
    msg = render_telegram_message("ENTER", _stuck_snapshot(), StuckAlertState(streak=2), cfg, now=0.0)
    assert "COURIER_DELIVERED+COURIER_PICKED_UP" in msg


# ====================== GROUP D: StuckAlertConfig.from_env ======================

def test_from_env_defaults_when_no_env_set(monkeypatch):
    for key in list(os.environ.keys()):
        if key.startswith("STUCK_ALERT_"):
            monkeypatch.delenv(key, raising=False)
    cfg = StuckAlertConfig.from_env(
        consumer_id="myc", consumer_display_name="My Consumer", event_types=frozenset(["X"])
    )
    assert cfg.age_threshold_sec == 300
    assert cfg.pending_threshold == 100
    assert cfg.pending_low_water == 30
    assert cfg.sustain_cycles == 2
    assert cfg.realert_interval_sec == 1800
    assert cfg.shadow_mode_only is False


def test_from_env_per_consumer_prefix_override(monkeypatch):
    monkeypatch.setenv("STUCK_ALERT_SLA_TRACKER_AGE_SEC", "600")
    monkeypatch.setenv("STUCK_ALERT_SLA_TRACKER_PENDING_THRESHOLD", "50")
    sla_cfg = StuckAlertConfig.from_env(
        consumer_id="sla_tracker",
        consumer_display_name="Ziomek SLA tracker",
        event_types=frozenset(["COURIER_PICKED_UP", "COURIER_DELIVERED"]),
    )
    shadow_cfg = StuckAlertConfig.from_env(
        consumer_id="shadow",
        consumer_display_name="Ziomek shadow worker",
        event_types=frozenset(["NEW_ORDER"]),
    )
    assert sla_cfg.age_threshold_sec == 600
    assert sla_cfg.pending_threshold == 50
    assert shadow_cfg.age_threshold_sec == 300
    assert shadow_cfg.pending_threshold == 100


def test_from_env_shadow_mode_only_truthy(monkeypatch):
    for truthy in ("1", "true", "yes", "on", "TRUE"):
        monkeypatch.setenv("STUCK_ALERT_X_SHADOW_MODE_ONLY", truthy)
        cfg = StuckAlertConfig.from_env(consumer_id="x", consumer_display_name="X", event_types=frozenset(["A"]))
        assert cfg.shadow_mode_only is True, f"truthy {truthy!r} should yield True"
    for falsy in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("STUCK_ALERT_X_SHADOW_MODE_ONLY", falsy)
        cfg = StuckAlertConfig.from_env(consumer_id="x", consumer_display_name="X", event_types=frozenset(["A"]))
        assert cfg.shadow_mode_only is False, f"falsy {falsy!r} should yield False"


def test_from_env_int_parsing_robust(monkeypatch):
    monkeypatch.setenv("STUCK_ALERT_SHADOW_PENDING_THRESHOLD", "50")
    cfg = StuckAlertConfig.from_env(
        consumer_id="shadow", consumer_display_name="X", event_types=frozenset(["A"])
    )
    assert cfg.pending_threshold == 50
    assert isinstance(cfg.pending_threshold, int)


# ====================== GROUP E: append_evaluation_log ======================

def test_append_evaluation_writes_jsonl_line(tmp_path):
    log_path = tmp_path / "evals.jsonl"
    cfg = _make_config(consumer_id="shadow", event_types=frozenset(["NEW_ORDER"]))
    snap = _stuck_snapshot()
    state_before = StuckAlertState(streak=1)
    state_after = StuckAlertState(alert_sent=True, streak=2, last_alert_ts=100.0, first_alert_ts=100.0)
    append_evaluation_log(
        snapshot=snap,
        state_before=state_before,
        state_after=state_after,
        emit=True,
        kind="ENTER",
        config=cfg,
        now=100.0,
        log_path=log_path,
    )
    assert log_path.exists()
    lines = log_path.read_text().splitlines()
    assert len(lines) == 1
    record = json.loads(lines[0])
    assert record["consumer_id"] == "shadow"
    assert record["event_types"] == ["NEW_ORDER"]
    assert record["emit"] is True
    assert record["kind"] == "ENTER"
    assert "snapshot" in record
    assert record["ts"] == 100.0
    assert record["shadow_mode_only"] is False


def test_append_evaluation_log_failure_silent():
    """Invalid path should NOT raise."""
    bad_path = Path("/proc/1/no/access/file.jsonl")
    try:
        append_evaluation_log(
            snapshot=_stuck_snapshot(),
            state_before=StuckAlertState(),
            state_after=StuckAlertState(),
            emit=False,
            kind=None,
            config=_make_config(),
            now=0.0,
            log_path=bad_path,
        )
    except Exception as e:
        pytest.fail(f"append_evaluation_log should NOT raise, got: {type(e).__name__}: {e}")


def test_append_evaluation_appends_not_overwrites(tmp_path):
    log_path = tmp_path / "evals.jsonl"
    cfg = _make_config()
    snap = _stuck_snapshot()
    for i in range(3):
        append_evaluation_log(
            snapshot=snap,
            state_before=StuckAlertState(),
            state_after=StuckAlertState(),
            emit=False,
            kind=None,
            config=cfg,
            now=float(i),
            log_path=log_path,
        )
    lines = log_path.read_text().splitlines()
    assert len(lines) == 3
    timestamps = [json.loads(line)["ts"] for line in lines]
    assert timestamps == [0.0, 1.0, 2.0]


def test_default_log_path_constant_correct():
    assert isinstance(DEFAULT_EVALUATIONS_LOG_PATH, Path)
    assert "dispatch_state" in str(DEFAULT_EVALUATIONS_LOG_PATH)
    assert "consumer_stuck_alert_evaluations.jsonl" in str(DEFAULT_EVALUATIONS_LOG_PATH)
