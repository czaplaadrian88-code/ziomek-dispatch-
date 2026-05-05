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

from typing import List, Optional, Tuple


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


# ============================================================
# TASK A CZASOWKA PROACTIVE — proposal templates (2026-05-05)
# ============================================================
#
# 4 templates dla T-50 / T-40 / no-candidate / T-0 alert. Pure formatters,
# no I/O, no globals. Reuse mobile-readable style B.5: ~30 chars/line, single
# emoji marker, polski operational language. T-50 = 3 buttons (Tak/Nie/Czekaj),
# T-40 = 2 buttons (Tak/Nie — last chance, no Czekaj).
#
# callback_data format: "CZAS_{ACTION}:{oid}:{cid}:{trigger_min}"
# - ACTION ∈ {TAK, NIE, CZEKAJ}
# - oid = order_id (panel zlecenie id)
# - cid = courier_id (proposed candidate)
# - trigger_min ∈ {50, 40} (oryginalne okno, NIE re-eval offset)


def format_czasowka_proposal(
    oid: str,
    restaurant: str,
    pickup_hhmm: str,
    candidate_name: str,
    candidate_cid: str,
    score: float,
    trigger_min: int,
) -> Tuple[str, list]:
    """T-50 czasówka proposal — 3-button (Tak/Nie/Czekaj).

    Returns (text, inline_keyboard) for tg_send_text_with_keyboard.
    """
    text = (
        f"🕐 Czasówka T-{trigger_min} #{oid}\n"
        f"{restaurant} → odbiór {pickup_hhmm}\n"
        f"Kandydat: {candidate_name} (score {score:.0f})\n"
        f"Przypisać teraz?"
    )
    kb = [[
        {"text": "✅ Tak", "callback_data": f"CZAS_TAK:{oid}:{candidate_cid}:{trigger_min}"},
        {"text": "❌ Nie", "callback_data": f"CZAS_NIE:{oid}:{candidate_cid}:{trigger_min}"},
        {"text": "⏳ Czekaj", "callback_data": f"CZAS_CZEKAJ:{oid}:{candidate_cid}:{trigger_min}"},
    ]]
    return text, kb


def format_czasowka_last_chance(
    oid: str,
    restaurant: str,
    pickup_hhmm: str,
    candidate_name: str,
    candidate_cid: str,
    score: float,
) -> Tuple[str, list]:
    """T-40 LAST CHANCE — 2-button (no Czekaj — to ostatnia szansa).

    Returns (text, inline_keyboard).
    """
    text = (
        f"⚠ Czasówka T-40 LAST CHANCE #{oid}\n"
        f"{restaurant} → odbiór {pickup_hhmm}\n"
        f"Kandydat: {candidate_name} (score {score:.0f})\n"
        f"Decyzja teraz lub manual w panelu."
    )
    kb = [[
        {"text": "✅ Tak", "callback_data": f"CZAS_TAK:{oid}:{candidate_cid}:40"},
        {"text": "❌ Nie", "callback_data": f"CZAS_NIE:{oid}:{candidate_cid}:40"},
    ]]
    return text, kb


def format_czasowka_no_candidate(
    oid: str,
    restaurant: str,
    pickup_hhmm: str,
    trigger_min: int,
    next_check_ts: Optional[str] = None,
) -> str:
    """Info-only — brak kandydata MAYBE w oknie T-50/T-40."""
    nxt = f"\nNastępna ocena: {next_check_ts}" if next_check_ts else ""
    return (
        f"⏰ Czasówka T-{trigger_min} #{oid}\n"
        f"{restaurant} → odbiór {pickup_hhmm}\n"
        f"Brak kandydata (manual dispatch)." + nxt
    )


def format_czasowka_alert_unassigned(
    oid: str,
    restaurant: str,
    pickup_hhmm: str,
) -> str:
    """T-0 critical info-only — czasówka nadal u Koordynatora w momencie odbioru."""
    return (
        f"🚨 Czasówka #{oid} NIEPRZYPISANA @ T-0\n"
        f"{restaurant} → odbiór {pickup_hhmm}\n"
        f"Wymaga natychmiastowego manual dispatch."
    )
