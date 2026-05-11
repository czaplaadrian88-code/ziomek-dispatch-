"""V3.28 #33 + #35 — Worker stuck Telegram alert state machine (2026-05-11).

History:
- #33 (rano 11.05): dodał Telegram propagation + dedup latch (4 transitions).
  Pre-#33 alert był tylko log.critical → silent dla Adriana.
- #35 (wieczór 11.05): hysteresis + sustain + re-alert (Lekcja #112).
  Pre-#35 latch resetował się na pojedyncze is_stuck=False (jeden processed
  event flipuje age→0). Pod peak load pending=191 wisiało, worker przetwarzał
  1 event co ~5-10 min → spam alert co ~10 min. Root cause: mieszał klasy
  failure (WORKER_FROZEN vs BACKLOG_OVERLOAD) + recovery semantics niepoprawna.

Sprint #35 state machine (5-tuple return: emit, kind, sent, streak, last_ts):
1. ENTER — streak >= sustain_cycles (anti-flap)
2. SUSTAINED — re-alert co realert_interval_sec (operator reminder)
3. RECOVERY — pending <= low_water (hysteresis exit, NOT single is_stuck=False)
4. NO-OP — wszystkie inne (healthy / sub-sustain / dedup window / single-event flap)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2.shadow_dispatcher import (
    _v328_should_emit_stuck_alert,
    _v328_compute_heartbeat_state,
)


# Convenience defaults (mirror module defaults; explicit to keep tests deterministic).
SUSTAIN = 2
REALERT_SEC = 1800.0


def _call(is_stuck, is_recovered, sent, streak, last_ts, now=1000.0,
          sustain=SUSTAIN, realert=REALERT_SEC):
    return _v328_should_emit_stuck_alert(
        is_stuck, is_recovered, sent, streak, last_ts, now,
        sustain_cycles=sustain, realert_interval_sec=realert,
    )


# ---- ENTER: sustain anti-flap guard ----

def test_enter_requires_sustain_cycles():
    """Pojedynczy is_stuck=True NIE wywołuje ENTER (anti-flap)."""
    emit, kind, sent, streak, ts = _call(
        is_stuck=True, is_recovered=False, sent=False, streak=0, last_ts=0.0,
    )
    assert emit is False, "1 cycle nie powinien fire (sustain=2)"
    assert kind is None
    assert sent is False, "latch nie set przed sustain"
    assert streak == 1, "streak inc"


def test_enter_fires_at_sustain_threshold():
    """Drugi consecutive is_stuck → ENTER alert emitted."""
    emit, kind, sent, streak, ts = _call(
        is_stuck=True, is_recovered=False, sent=False, streak=1, last_ts=0.0, now=1000.0,
    )
    assert emit is True, "streak hit sustain=2 → emit"
    assert kind == "ENTER"
    assert sent is True, "latch set"
    assert streak == 2
    assert ts == 1000.0, "last_alert_ts updated"


def test_streak_resets_on_single_unstuck_cycle():
    """is_stuck=False kasuje streak (flap protection)."""
    emit, kind, sent, streak, ts = _call(
        is_stuck=False, is_recovered=False, sent=False, streak=1, last_ts=0.0,
    )
    assert emit is False
    assert streak == 0, "streak reset na flap"


# ---- RECOVERY: hysteresis (low_water), NOT is_stuck=False ----

def test_recovery_requires_low_water_not_is_stuck_false():
    """Pojedynczy is_stuck=False (age dropped przez 1 event) ALE pending wciąż wysoki → NIE recovery."""
    # Symulacja: worker processed 1 event (age=0 → is_stuck=False), ale pending=191 wciąż.
    emit, kind, sent, streak, ts = _call(
        is_stuck=False, is_recovered=False, sent=True, streak=5, last_ts=900.0,
    )
    assert emit is False, "single processed event ≠ recovery (Lekcja #112)"
    assert kind is None
    assert sent is True, "latch HOLD — backlog wciąż"
    assert streak == 0


def test_recovery_fires_when_pending_drops_below_low_water():
    """Pending spadło do low_water threshold → RECOVERY alert + latch reset."""
    emit, kind, sent, streak, ts = _call(
        is_stuck=False, is_recovered=True, sent=True, streak=15, last_ts=900.0,
    )
    assert emit is True
    assert kind == "RECOVERY"
    assert sent is False, "latch reset → re-arm"
    assert streak == 0
    assert ts == 0.0


def test_recovery_no_emit_without_prior_alert():
    """is_recovered=True ale latch nigdy nie set (nie było alertu) → no-op."""
    emit, kind, sent, streak, ts = _call(
        is_stuck=False, is_recovered=True, sent=False, streak=0, last_ts=0.0,
    )
    assert emit is False
    assert kind is None


# ---- SUSTAINED: re-alert reminder ----

def test_sustained_reminder_after_interval():
    """Latch set + still stuck + elapsed >= interval → SUSTAINED re-alert."""
    emit, kind, sent, streak, ts = _call(
        is_stuck=True, is_recovered=False, sent=True, streak=30, last_ts=0.0,
        now=1800.0, realert=1800.0,
    )
    assert emit is True
    assert kind == "SUSTAINED"
    assert sent is True
    assert ts == 1800.0, "last_ts updated dla next interval"


def test_sustained_no_emit_within_interval():
    """Latch set + still stuck ALE elapsed < interval → no emit (dedup)."""
    emit, kind, sent, streak, ts = _call(
        is_stuck=True, is_recovered=False, sent=True, streak=10, last_ts=1000.0,
        now=1500.0, realert=1800.0,  # elapsed=500 < 1800
    )
    assert emit is False
    assert kind is None
    assert sent is True


# ---- Regression test for Lekcja #112 spam scenario ----

def test_lekcja_112_peak_overload_no_spam():
    """Regression: peak overload scenario (pending=191) NIE generuje spam co 10 min.

    Pre-#35 (buggy): worker processuje 1 event co ~5-10 min → age oscyluje
    wokół 300s, latch flap-reset, alert co 10 min.

    Post-#35: latch trzymany aż pending<=low_water. Pod sustained backlog
    operator dostaje 1× ENTER + okresowe SUSTAINED reminders (nie spam).
    """
    sent = False
    streak = 0
    last_ts = 0.0
    emits = []
    # Symulujemy 30 heartbeatów (30 min) z flapping age (worker processuje
    # event co kilka cycles), pending wciąż wysoki (191 >> low_water=30).
    is_stuck_pattern = [
        True, True,           # cycles 1-2: streak hits sustain → ENTER
        False, True,          # cycle 3: 1 event processed, age=0; cycle 4: age znów >300s
        False, True, True,    # cycles 5-7
        False, True,
        False, True, True,
        True, True, True, True, True, True, True, True, True,  # sustained stuck
        True, True, True, True, True, True, True, True,
    ]
    now = 1000.0
    REALERT = 1800.0
    for i, is_s in enumerate(is_stuck_pattern):
        emit, kind, sent, streak, last_ts = _call(
            is_stuck=is_s,
            is_recovered=False,
            sent=sent, streak=streak, last_ts=last_ts,
            now=now, realert=REALERT,
        )
        if emit:
            emits.append((i, kind, now))
        now += 60.0  # heartbeat co 60s
    # Pre-#35 emitowałby ~3-5× ENTER w 30 min. Post-#35: 1× ENTER + max 1× SUSTAINED.
    kinds = [k for _, k, _ in emits]
    assert kinds.count("ENTER") == 1, f"max 1× ENTER pod sustained backlog, dostałem {kinds}"
    assert kinds.count("SUSTAINED") <= 1, f"max 1× SUSTAINED w 30 min (interval=1800s), dostałem {kinds}"
    assert "RECOVERY" not in kinds, "recovery nie powinien firować bez is_recovered=True"


def test_lekcja_112_recovery_after_backlog_clears():
    """Recovery scenario: po sustained stuck, pending finally drops → RECOVERY emit."""
    sent = True
    streak = 20
    last_ts = 1000.0
    # Symulujemy: backlog spadł, pending=25 ≤ low_water=30.
    emit, kind, sent, streak, last_ts = _call(
        is_stuck=False, is_recovered=True, sent=sent, streak=streak, last_ts=last_ts,
        now=2500.0,
    )
    assert emit is True
    assert kind == "RECOVERY"
    assert sent is False, "latch reset"
    assert streak == 0


# ---- _v328_compute_heartbeat_state new is_recovered field ----

def test_heartbeat_state_is_recovered_low_water():
    """pending<=low_water → is_recovered=True (hysteresis exit)."""
    s = _v328_compute_heartbeat_state(
        last_processed_ts=1000.0, now=1100.0, pending=20, pending_low_water=30,
    )
    assert s["is_recovered"] is True
    assert s["is_stuck"] is False  # pending<threshold tak czy siak


def test_heartbeat_state_is_recovered_above_low_water():
    """pending>low_water → is_recovered=False (still in hysteresis zone)."""
    s = _v328_compute_heartbeat_state(
        last_processed_ts=1000.0, now=1400.0, pending=50, pending_low_water=30,
    )
    assert s["is_recovered"] is False
    # age=400>300, pending=50<threshold(100) → is_stuck=False (AND gate)
    assert s["is_stuck"] is False


def test_heartbeat_state_is_stuck_peak_overload():
    """Klasyczny peak overload: age=310s, pending=191 → is_stuck=True, is_recovered=False."""
    s = _v328_compute_heartbeat_state(
        last_processed_ts=1000.0, now=1310.0, pending=191, pending_low_water=30,
    )
    assert s["is_stuck"] is True
    assert s["is_recovered"] is False
    assert s["worker_alive"] is False  # age >= 300


# ---- Custom-runner entry ----

if __name__ == "__main__":
    tests = [
        test_enter_requires_sustain_cycles,
        test_enter_fires_at_sustain_threshold,
        test_streak_resets_on_single_unstuck_cycle,
        test_recovery_requires_low_water_not_is_stuck_false,
        test_recovery_fires_when_pending_drops_below_low_water,
        test_recovery_no_emit_without_prior_alert,
        test_sustained_reminder_after_interval,
        test_sustained_no_emit_within_interval,
        test_lekcja_112_peak_overload_no_spam,
        test_lekcja_112_recovery_after_backlog_clears,
        test_heartbeat_state_is_recovered_low_water,
        test_heartbeat_state_is_recovered_above_low_water,
        test_heartbeat_state_is_stuck_peak_overload,
    ]
    passed = failed = 0
    for t in tests:
        try:
            t()
            print(f"  PASS  {t.__name__}")
            passed += 1
        except AssertionError as e:
            print(f"  FAIL  {t.__name__}: {e}")
            failed += 1
        except Exception as e:
            print(f"  ERROR {t.__name__}: {type(e).__name__}: {e}")
            failed += 1
    print(f"\n{passed}/{len(tests)} PASS, {failed} FAIL")
    sys.exit(0 if failed == 0 else 1)
