"""V3.28 #33 — Worker stuck Telegram alert propagation (2026-05-11).

Audit 11.05 17:32 Warsaw ujawnił 6× V328_WORKER_STUCK CRITICAL log dziś, ALE
zero Telegram propagation. Worker stuck ~17:48 niewidoczny dla Adriana → manual
koord override required po fakcie. Pre-#33 only `_log.critical()` w heartbeat
loop — silent killer pattern (Lekcja #87).

Sprint #33 dodaje:
1. `_v328_should_emit_stuck_alert(is_stuck, alert_sent)` pure state machine helper
2. Telegram `send_admin_alert` propagation w main loop (defensive try/except)
3. Dedup ONCE per stuck cycle (mirror MP-#13 OSRM L2 pattern)
4. Auto-reset flag gdy worker recovers (re-arm next stuck cycle)
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2.shadow_dispatcher import _v328_should_emit_stuck_alert


# ---- Pure helper state machine (4 transitions) ----

def test_entry_stuck_first_time_emits():
    """Pierwszy heartbeat w stuck cycle → emit alert + set sent=True."""
    emit, new_sent = _v328_should_emit_stuck_alert(is_stuck=True, alert_sent=False)
    assert emit is True, "entry must emit"
    assert new_sent is True, "flag must be set"


def test_dedup_continued_stuck_no_emit():
    """Kolejne heartbeats w tym samym stuck cycle → no emit (dedup spam co 60s)."""
    emit, new_sent = _v328_should_emit_stuck_alert(is_stuck=True, alert_sent=True)
    assert emit is False, "dedup must suppress"
    assert new_sent is True, "flag stays set"


def test_recovery_resets_flag_no_emit():
    """Worker recovers (stuck=False) → flag reset, no emit (cichy reset)."""
    emit, new_sent = _v328_should_emit_stuck_alert(is_stuck=False, alert_sent=True)
    assert emit is False, "recovery must not emit"
    assert new_sent is False, "flag must reset for re-arm"


def test_healthy_idle_no_op():
    """Healthy (stuck=False, sent=False) → no-op, no emit, no state change."""
    emit, new_sent = _v328_should_emit_stuck_alert(is_stuck=False, alert_sent=False)
    assert emit is False
    assert new_sent is False


# ---- Full lifecycle integration (4 transitions w sequence) ----

def test_full_lifecycle_entry_dedup_recovery_re_entry():
    """Symuluje full stuck cycle: healthy → stuck → dedup spam → recovery → re-arm."""
    sent = False
    emits = []

    # Phase 1: healthy 3 heartbeats — no emits
    for _ in range(3):
        emit, sent = _v328_should_emit_stuck_alert(False, sent)
        emits.append(emit)
    assert emits == [False, False, False], f"healthy phase: {emits}"
    assert sent is False

    # Phase 2: stuck entry + 3 dedup heartbeats
    emits = []
    for is_stuck_val in [True, True, True, True]:
        emit, sent = _v328_should_emit_stuck_alert(is_stuck_val, sent)
        emits.append(emit)
    assert emits == [True, False, False, False], f"entry+dedup: {emits}"
    assert sent is True, "flag must be set during stuck"

    # Phase 3: recovery + 2 healthy heartbeats
    emits = []
    for _ in range(3):
        emit, sent = _v328_should_emit_stuck_alert(False, sent)
        emits.append(emit)
    assert emits == [False, False, False], f"recovery: {emits}"
    assert sent is False, "flag must be reset for re-arm"

    # Phase 4: NEW stuck cycle (re-arm proves correct) → emit again
    emit, sent = _v328_should_emit_stuck_alert(True, sent)
    assert emit is True, "re-arm: new stuck cycle must emit fresh alert"
    assert sent is True


# ---- Edge cases ----

def test_flapping_stuck_unstuck_pattern():
    """Synthetic flapping: stuck→not stuck→stuck→not stuck → 2 entry alerts max."""
    sent = False
    emit_count = 0
    for is_stuck_val in [True, False, True, False, True, False]:
        emit, sent = _v328_should_emit_stuck_alert(is_stuck_val, sent)
        if emit:
            emit_count += 1
    assert emit_count == 3, f"3 stuck entry transitions, got {emit_count} emits"


# ---- Custom-runner entry ----

if __name__ == "__main__":
    tests = [
        test_entry_stuck_first_time_emits,
        test_dedup_continued_stuck_no_emit,
        test_recovery_resets_flag_no_emit,
        test_healthy_idle_no_op,
        test_full_lifecycle_entry_dedup_recovery_re_entry,
        test_flapping_stuck_unstuck_pattern,
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
