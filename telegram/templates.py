"""Pure formatter functions for SHIFT NOTIFICATIONS (TASK B 2026-05-04).

Mobile-readable: ~30 chars/line, single emoji marker, no trailing whitespace.
All texts in Polish to match Adrian/Bartek operational language.

Public API (5 functions):
    format_shift_start_individual(courier, scheduled_time)
    format_shift_start_batch(couriers, scheduled_time)
    format_shift_reminder(courier, scheduled_time)
    format_shift_end(courier, scheduled_time)
    format_alert_courier_no_show(courier, scheduled_time)

NOTE: pure functions — no I/O, no globals. Safe to import anywhere.
"""
from __future__ import annotations

from typing import List


def format_shift_start_individual(courier: str, scheduled_time: str) -> str:
    """T-60min start notification dla pojedynczego kuriera."""
    return (
        "🟢 Start zmiany za 60 min\n"
        f"{courier}\n"
        f"Godzina: {scheduled_time}\n"
        "Potwierdzasz?"
    )


def format_shift_start_batch(couriers: List[str], scheduled_time: str) -> str:
    """T-60min start notification dla batch (>=2 kurierów na tę samą godzinę)."""
    body = "\n".join(f"• {c}" for c in couriers)
    return (
        f"🟢 Start zmiany {scheduled_time} (za ~60 min)\n"
        f"{body}\n"
        "Każdy klika swój przycisk:"
    )


def format_shift_reminder(courier: str, scheduled_time: str) -> str:
    """Reminder po brak odpowiedzi na initial T-60min start ping."""
    return (
        "⏰ Przypomnienie\n"
        f"{courier}\n"
        f"Zmiana: {scheduled_time}\n"
        "Potwierdzasz?"
    )


def format_shift_end(courier: str, scheduled_time: str) -> str:
    """T-60min end notification — kończysz czy zostajesz?"""
    return (
        "🔴 Koniec zmiany za 60 min\n"
        f"{courier}\n"
        f"Godzina: {scheduled_time}\n"
        "Kończysz czy zostajesz?"
    )


def format_alert_courier_no_show(courier: str, scheduled_time: str) -> str:
    """Alert do Bartka gdy kurier kliknął ❌ NIE PRZYJDZIE na start."""
    return (
        "⚠ NIE PRZYJDZIE\n"
        f"{courier}\n"
        f"Zmiana miała być: {scheduled_time}\n"
        "Bartku — zorganizuj zastępstwo."
    )
