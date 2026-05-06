"""TASK A CZASÓWKI PROACTIVE SCHEDULER (2026-05-05).

T-50 / T-40 trigger detection + state + observability for time-bound
orders held by Koordynator (id_kurier=26 per TASK 4 LIVE 2026-05-04).

Master flag CZASOWKA_PROACTIVE_ENABLED defaults False — module is
import-safe with zero side effects when flags off.

Sister agent owns:
  - dispatch_v2/telegram/templates.py (czasówka templates additions)
  - dispatch_v2/telegram_approver.py (callback router czasówka_propose:* etc.)
DO NOT modify those — Agent B scope.
"""

__version__ = "0.1.0"
