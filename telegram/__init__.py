"""dispatch_v2.telegram — Telegram surface package for shift notifications.

TASK B SHIFT NOTIFICATIONS (2026-05-04):
- templates.py — pure formatters for shift start/reminder/end/alert messages.

Sister package `dispatch_v2.shift_notifications` owns the worker (state, scheduler,
sender). This package only provides Telegram-side templates and is consumed by
`telegram_approver.py` callback handlers (SHIFT_START_*, SHIFT_REMINDER_*, SHIFT_END_*).
"""
__version__ = "0.1.0"
