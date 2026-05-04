"""TASK B SHIFT NOTIFICATIONS — worker package (2026-05-04).

Master flag SHIFT_NOTIFY_ENABLED defaults False — module is import-safe with
zero side effects when flags are off. Sister agent owns telegram callback
router + templates (`dispatch_v2/telegram/templates.py`) — DO NOT MODIFY.
"""

__version__ = "0.1.0"
