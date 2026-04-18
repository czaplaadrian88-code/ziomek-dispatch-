"""Tests for F2.2-prep P1: _classify_timeout_outcome + learning record shape.

Zero kontaktu z Telegram API, state_machine, learning_log plikiem.
Standalone executable.
"""
import sys

sys.path.insert(0, '/root/.openclaw/workspace/scripts')

from dispatch_v2.telegram_approver import _classify_timeout_outcome


def test_classify_overridden_by_later():
    """assigned / picked_up / delivered → OVERRIDDEN_BY_LATER."""
    cases = ['assigned', 'picked_up', 'delivered']
    for cur in cases:
        got = _classify_timeout_outcome(cur)
        assert got == 'OVERRIDDEN_BY_LATER', f"cur_status={cur!r}: got {got}"
    return True


def test_classify_awaiting_assignment():
    """planned → AWAITING_ASSIGNMENT (empirycznie 54.6% timeoutów)."""
    assert _classify_timeout_outcome('planned') == 'AWAITING_ASSIGNMENT'
    return True


def test_classify_order_cancelled():
    assert _classify_timeout_outcome('cancelled') == 'ORDER_CANCELLED'
    return True


def test_classify_expired_no_user_input():
    """Edge case: cur_status='new' (defensive; watchdog filters != 'new' but
    classifier is independent pure function reusable by future callers)."""
    assert _classify_timeout_outcome('new') == 'EXPIRED_NO_USER_INPUT'
    return True


def test_classify_unknown_state():
    """Unexpected cur_status values + None → UNKNOWN_STATE (logged warning in watchdog)."""
    for cur in ['foo', '', 'random_future_status', None]:
        got = _classify_timeout_outcome(cur)
        assert got == 'UNKNOWN_STATE', f"cur_status={cur!r}: got {got}"
    return True


def test_learning_record_shape_has_new_fields():
    """Simulate record dict construction (watchdog logic). Verify all 3 fields present.

    Mimics watchdog() at telegram_approver.py ~line 1208-1225.
    """
    cur_status = 'assigned'
    outcome = _classify_timeout_outcome(cur_status)
    record = {
        'ts': '2026-04-18T17:00:00+00:00',
        'order_id': 'TEST-123',
        'action': 'TIMEOUT_SUPERSEDED',           # backward-compat preserved
        'timeout_outcome': outcome,
        'timeout_outcome_detail': cur_status or 'unknown',
        'ok': True,
        'feedback': f'order już {cur_status} — silent skip',
        'decision': {'placeholder': True},
    }
    # Old field preserved for learning_analyzer
    assert record['action'] == 'TIMEOUT_SUPERSEDED'
    # New discriminator fields
    assert record['timeout_outcome'] == 'OVERRIDDEN_BY_LATER'
    assert record['timeout_outcome_detail'] == 'assigned'
    return True


def test_learning_record_unknown_detail_fallback():
    """When cur_status is None/empty → detail='unknown' not falsy."""
    cur_status = None
    outcome = _classify_timeout_outcome(cur_status)
    record = {
        'action': 'TIMEOUT_SUPERSEDED',
        'timeout_outcome': outcome,
        'timeout_outcome_detail': cur_status or 'unknown',
    }
    assert record['timeout_outcome'] == 'UNKNOWN_STATE'
    assert record['timeout_outcome_detail'] == 'unknown'
    return True


def main():
    tests = [
        ('classify_overridden_by_later', test_classify_overridden_by_later),
        ('classify_awaiting_assignment', test_classify_awaiting_assignment),
        ('classify_order_cancelled', test_classify_order_cancelled),
        ('classify_expired_no_user_input', test_classify_expired_no_user_input),
        ('classify_unknown_state', test_classify_unknown_state),
        ('learning_record_shape_has_new_fields', test_learning_record_shape_has_new_fields),
        ('learning_record_unknown_detail_fallback', test_learning_record_unknown_detail_fallback),
    ]
    print('=' * 60)
    print('F2.2-prep P1: _classify_timeout_outcome + record shape tests')
    print('=' * 60)
    passed = 0
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f'  ✅ {name}')
            passed += 1
        except AssertionError as e:
            print(f'  ❌ {name}: {e}')
            failed.append(name)
        except Exception as e:
            print(f'  ❌ {name}: UNEXPECTED {type(e).__name__}: {e}')
            failed.append(name)
    print('=' * 60)
    print(f'{passed}/{len(tests)} PASS')
    if failed:
        print(f'FAILED: {failed}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
