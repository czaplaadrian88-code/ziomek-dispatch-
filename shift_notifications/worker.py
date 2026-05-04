"""TASK B SHIFT NOTIFICATIONS — worker entry point (2026-05-04).

Single tick (oneshot, every 1 min via systemd timer):
  1. Check master flag SHIFT_NOTIFY_ENABLED — if False → exit immediately.
  2. Load schedule (load_schedule from scripts/schedule_utils).
  3. Read state file (shift_confirmations.json).
  4. B.1 — T-60 SHIFT START notifications (window 55..65 min before start)
        with batch grouping for same 10-min slot (>=3 couriers → batch).
  5. B.2 — T-30 SHIFT START reminders (only for undecided couriers).
  6. B.3 — T-60 SHIFT END notifications (always individual).
  7. apply_unconfirmed_default — at scheduled_start with decision still None,
        record unconfirmed_default=True for downstream R-04 / dispatch consumer.

Hard rules:
  - All time math in Warsaw tz.
  - Schedule consumed only via schedule_utils.load_schedule() (NEVER read JSON).
  - cid resolved via kurier_ids.json (full_name or last name match).
  - Idempotency: state[*][key] present → skip re-send.
  - Templates imported lazily — sister agent owns templates.py; if missing,
    fall back to plain stubs so worker still runs.
"""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

# Make scripts/ a sibling for `import schedule_utils` (auto_koord pattern)
_SCRIPTS_DIR = "/root/.openclaw/workspace/scripts"
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from dispatch_v2.common import setup_logger, load_flags
from dispatch_v2.shift_notifications import state as state_mod
from dispatch_v2.shift_notifications import grouping as grouping_mod
from dispatch_v2.shift_notifications import telegram_send as telegram_send_mod
from dispatch_v2.shift_notifications.grouping import Candidate

# Module-level imports for tests to monkey-patch
load_schedule: Callable[[], Dict[str, Any]]
try:
    from schedule_utils import load_schedule as _load_schedule_real  # type: ignore
    load_schedule = _load_schedule_real
except Exception:  # pragma: no cover — keep importable even if scripts/ not on path
    def load_schedule() -> Dict[str, Any]:  # type: ignore[no-redef]
        return {}

WARSAW = ZoneInfo("Europe/Warsaw")
LOG_DIR = "/root/.openclaw/workspace/scripts/logs/"
_log = setup_logger("shift_notifications.worker", LOG_DIR + "shift_notifications.log")

KURIER_IDS_PATH = "/root/.openclaw/workspace/dispatch_state/kurier_ids.json"

# Module-level send shim — tests monkey-patch this to bypass Telegram.
tg_send_text_with_keyboard = telegram_send_mod.tg_send_text_with_keyboard

# Window tolerances around the T-60 / T-30 / T-0 / end T-60 anchors.
# 1-min tick + 5-min slack → catch shifts even if a tick misses.
T60_WINDOW_LOW_MIN = 55.0
T60_WINDOW_HIGH_MIN = 65.0
T30_WINDOW_LOW_MIN = 25.0
T30_WINDOW_HIGH_MIN = 35.0


# ----- helpers ------------------------------------------------------------


def _import_templates():
    """Lazy import — sister agent owns templates.py. Fall back to stubs."""
    try:
        from dispatch_v2.telegram import templates as t  # type: ignore
        return t
    except Exception:
        class _Stub:
            @staticmethod
            def format_shift_start_individual(courier: str, scheduled_time: str) -> str:
                return f"[stub] Cześć {courier} — Twoja zmiana o {scheduled_time}. Potwierdź start?"

            @staticmethod
            def format_shift_start_batch(couriers: list, scheduled_time: str) -> str:
                joined = ", ".join(couriers)
                return f"[stub] Zmiana {scheduled_time} — kurierzy: {joined}. Potwierdź każdy."

            @staticmethod
            def format_shift_reminder(courier: str, scheduled_time: str) -> str:
                return f"[stub] {courier} — przypominajka, zmiana o {scheduled_time}, potwierdź."

            @staticmethod
            def format_shift_end(courier: str, scheduled_time: str) -> str:
                return f"[stub] {courier} — koniec zmiany o {scheduled_time}, potwierdzasz?"

            @staticmethod
            def format_alert_courier_no_show(courier: str, scheduled_time: str) -> str:
                return f"[stub] ALERT: {courier} nie potwierdził startu {scheduled_time}."

        return _Stub


def _load_kurier_ids() -> Dict[str, str]:
    """Returns {full_name: cid_str}. Empty dict on failure (logged)."""
    try:
        with open(KURIER_IDS_PATH) as f:
            raw = json.load(f)
        return {str(k): str(v) for k, v in raw.items()}
    except FileNotFoundError:
        _log.warning(f"kurier_ids.json missing at {KURIER_IDS_PATH}")
        return {}
    except Exception as e:
        _log.warning(f"_load_kurier_ids fail: {type(e).__name__}: {e}")
        return {}


def resolve_cid(full_name: str, kurier_ids: Optional[Dict[str, str]] = None) -> Optional[str]:
    """Map schedule full_name → courier_id.

    Schedule keys are full names ('Bartek Ołdziej'). kurier_ids.json keys are
    panel display names ('Bartek O', 'Michał K'). Strategy:
      1. exact match
      2. case-insensitive 'first_word + first_letter_of_last_word' match
         e.g. 'Bartek Ołdziej' → key 'Bartek O'
      3. case-insensitive prefix on first 2 tokens
    Returns None if no match.
    """
    if not full_name:
        return None
    if kurier_ids is None:
        kurier_ids = _load_kurier_ids()
    if not kurier_ids:
        return None

    if full_name in kurier_ids:
        return kurier_ids[full_name]

    parts = full_name.strip().split()
    if not parts:
        return None
    first = parts[0]
    last = parts[-1] if len(parts) > 1 else ""
    cand = f"{first} {last[:1]}".strip() if last else first
    cand_lc = cand.lower()
    cand2_lc = f"{first} {last[:2]}".lower() if last else first.lower()

    for key, cid in kurier_ids.items():
        kl = key.lower()
        if kl == cand_lc:
            return cid
        if last and kl == cand2_lc:
            return cid
    # Fallback: first-name-only unique match (avoid ambiguity)
    first_lc = first.lower()
    matches = [cid for k, cid in kurier_ids.items() if k.lower() == first_lc]
    if len(matches) == 1:
        return matches[0]
    return None


def _parse_shift_dt(date_today: date, hhmm: str, *, end: bool = False) -> Optional[datetime]:
    """Parse 'HH:MM' on the given date in Warsaw tz. '24:00' → next-day 00:00 if end=True."""
    if not hhmm:
        return None
    try:
        if hhmm == "24:00":
            base = datetime.combine(date_today, datetime.min.time(), tzinfo=WARSAW)
            return base + timedelta(days=1) if end else base
        h, m = hhmm.split(":")
        return datetime(date_today.year, date_today.month, date_today.day,
                        int(h), int(m), tzinfo=WARSAW)
    except Exception as e:
        _log.warning(f"_parse_shift_dt fail for {hhmm!r}: {e}")
        return None


def _build_candidates_starting(
    schedule: Dict[str, Any],
    now: datetime,
    low_min: float,
    high_min: float,
    kurier_ids: Dict[str, str],
    state: dict,
    today_iso: str,
    *,
    skip_already: bool = True,
) -> List[Candidate]:
    """Couriers whose scheduled START is in [now+low_min, now+high_min]."""
    out: List[Candidate] = []
    seen_keys = set()
    for full_name, entry in (schedule or {}).items():
        if not entry or not isinstance(entry, dict):
            continue
        start_str = entry.get("start")
        if not start_str:
            continue
        shift_dt = _parse_shift_dt(now.date(), start_str)
        if shift_dt is None:
            continue
        delta_min = (shift_dt - now).total_seconds() / 60.0
        if not (low_min <= delta_min <= high_min):
            continue
        key = f"{today_iso}:{full_name}"
        if skip_already and key in state.get("start_notified", {}):
            continue
        if key in seen_keys:
            continue
        cid = resolve_cid(full_name, kurier_ids)
        if cid is None:
            state_mod.append_learning_log({
                "event": "UNMAPPED_COURIER_T60",
                "full_name": full_name,
                "scheduled": shift_dt.isoformat(),
            })
            continue
        seen_keys.add(key)
        out.append(Candidate(full_name=full_name, cid=cid, shift_dt=shift_dt))
    return out


def _build_candidates_ending(
    schedule: Dict[str, Any],
    now: datetime,
    low_min: float,
    high_min: float,
    kurier_ids: Dict[str, str],
    state: dict,
    today_iso: str,
) -> List[Candidate]:
    """Couriers whose scheduled END is in [now+low_min, now+high_min]."""
    out: List[Candidate] = []
    seen_keys = set()
    for full_name, entry in (schedule or {}).items():
        if not entry or not isinstance(entry, dict):
            continue
        end_str = entry.get("end")
        if not end_str:
            continue
        end_dt = _parse_shift_dt(now.date(), end_str, end=True)
        if end_dt is None:
            continue
        delta_min = (end_dt - now).total_seconds() / 60.0
        if not (low_min <= delta_min <= high_min):
            continue
        key = f"{today_iso}:{full_name}"
        if key in state.get("end_notified", {}):
            continue
        if key in seen_keys:
            continue
        cid = resolve_cid(full_name, kurier_ids)
        if cid is None:
            state_mod.append_learning_log({
                "event": "UNMAPPED_COURIER_T60_END",
                "full_name": full_name,
                "scheduled_end": end_dt.isoformat(),
            })
            continue
        seen_keys.add(key)
        out.append(Candidate(full_name=full_name, cid=cid, shift_dt=end_dt))
    return out


# ----- B.1 / B.2 / B.3 / defaults ----------------------------------------


def _format_hhmm(dt: datetime) -> str:
    return dt.astimezone(WARSAW).strftime("%H:%M")


def _send_start_individual(c: Candidate, templates) -> bool:
    text = templates.format_shift_start_individual(c.full_name, _format_hhmm(c.shift_dt))
    keyboard = [[
        {"text": "TAK", "callback_data": f"SHIFT_START_OK:{c.cid}"},
        {"text": "NIE", "callback_data": f"SHIFT_START_NO:{c.cid}"},
    ]]
    return tg_send_text_with_keyboard(text, keyboard)


def _send_start_batch(cands: List[Candidate], templates) -> bool:
    couriers = [c.full_name for c in cands]
    text = templates.format_shift_start_batch(couriers, _format_hhmm(cands[0].shift_dt))
    keyboard = []
    for c in cands:
        keyboard.append([
            {"text": f"TAK {c.full_name}", "callback_data": f"SHIFT_START_OK:{c.cid}"},
            {"text": f"NIE {c.full_name}", "callback_data": f"SHIFT_START_NO:{c.cid}"},
        ])
    return tg_send_text_with_keyboard(text, keyboard)


def _send_reminder(c: Candidate, templates) -> bool:
    text = templates.format_shift_reminder(c.full_name, _format_hhmm(c.shift_dt))
    keyboard = [[
        {"text": "TAK", "callback_data": f"SHIFT_REMINDER_OK:{c.cid}"},
        {"text": "NIE", "callback_data": f"SHIFT_REMINDER_NO:{c.cid}"},
    ]]
    return tg_send_text_with_keyboard(text, keyboard)


def _send_end(c: Candidate, templates) -> bool:
    text = templates.format_shift_end(c.full_name, _format_hhmm(c.shift_dt))
    keyboard = [[
        {"text": "Kończę", "callback_data": f"SHIFT_END_OK:{c.cid}"},
        {"text": "Przedłużam", "callback_data": f"SHIFT_END_EXT:{c.cid}"},
    ]]
    return tg_send_text_with_keyboard(text, keyboard)


def run_b1_start(
    schedule: Dict[str, Any],
    now: datetime,
    state: dict,
    kurier_ids: Dict[str, str],
    today_iso: str,
    batch_window_min: int,
    batch_min_couriers: int,
    templates,
) -> int:
    """B.1 — T-60 START. Returns count of notifications sent."""
    cands = _build_candidates_starting(
        schedule, now, T60_WINDOW_LOW_MIN, T60_WINDOW_HIGH_MIN,
        kurier_ids, state, today_iso,
    )
    if not cands:
        return 0
    buckets = grouping_mod.bucket_by_slot(cands, batch_window_min, batch_min_couriers)
    sent = 0
    notified_at = now.isoformat()
    for kind, members in buckets:
        if kind == "batch":
            ok = _send_start_batch(members, templates)
            for c in members:
                key = f"{today_iso}:{c.full_name}"
                state["start_notified"][key] = {
                    "cid": c.cid,
                    "scheduled": c.shift_dt.isoformat(),
                    "notified_at": notified_at,
                    "decision": None,
                    "confirmed_for_shift": None,
                    "batched": True,
                    "reminder_sent_at": None,
                    "unconfirmed_default": None,
                    "decided_at": None,
                    "decided_by": None,
                }
            if ok:
                sent += len(members)
        else:  # individual
            c = members[0]
            ok = _send_start_individual(c, templates)
            key = f"{today_iso}:{c.full_name}"
            state["start_notified"][key] = {
                "cid": c.cid,
                "scheduled": c.shift_dt.isoformat(),
                "notified_at": notified_at,
                "decision": None,
                "confirmed_for_shift": None,
                "batched": False,
                "reminder_sent_at": None,
                "unconfirmed_default": None,
                "decided_at": None,
                "decided_by": None,
            }
            if ok:
                sent += 1
    return sent


def run_b2_reminder(
    schedule: Dict[str, Any],
    now: datetime,
    state: dict,
    kurier_ids: Dict[str, str],
    today_iso: str,
    templates,
) -> int:
    """B.2 — T-30 REMINDER for couriers with decision=None."""
    sent = 0
    notified_at = now.isoformat()
    for full_name, entry in (schedule or {}).items():
        if not entry or not isinstance(entry, dict):
            continue
        start_str = entry.get("start")
        if not start_str:
            continue
        shift_dt = _parse_shift_dt(now.date(), start_str)
        if shift_dt is None:
            continue
        delta_min = (shift_dt - now).total_seconds() / 60.0
        if not (T30_WINDOW_LOW_MIN <= delta_min <= T30_WINDOW_HIGH_MIN):
            continue
        key = f"{today_iso}:{full_name}"
        rec = state.get("start_notified", {}).get(key)
        if not rec:
            # Couriers we never notified at T-60 (e.g. flag flipped on between
            # T-60 and T-30, or new schedule row). Skip — no record to gate.
            continue
        if rec.get("decision") is not None:
            continue
        if rec.get("reminder_sent_at"):
            continue
        cid = rec.get("cid") or resolve_cid(full_name, kurier_ids)
        if cid is None:
            continue
        c = Candidate(full_name=full_name, cid=str(cid), shift_dt=shift_dt)
        ok = _send_reminder(c, templates)
        if ok:
            rec["reminder_sent_at"] = notified_at
            sent += 1
    return sent


def run_b3_end(
    schedule: Dict[str, Any],
    now: datetime,
    state: dict,
    kurier_ids: Dict[str, str],
    today_iso: str,
    templates,
) -> int:
    """B.3 — T-60 END. Always individual (per spec) even if multiple couriers in same slot."""
    cands = _build_candidates_ending(
        schedule, now, T60_WINDOW_LOW_MIN, T60_WINDOW_HIGH_MIN,
        kurier_ids, state, today_iso,
    )
    if not cands:
        return 0
    sent = 0
    notified_at = now.isoformat()
    for c in cands:
        ok = _send_end(c, templates)
        key = f"{today_iso}:{c.full_name}"
        state["end_notified"][key] = {
            "cid": c.cid,
            "scheduled_end": c.shift_dt.isoformat(),
            "notified_at": notified_at,
            "decision": None,
            "shift_ending_confirmed": None,
            "shift_extended": None,
            "extended_until": None,
            "decided_at": None,
            "decided_by": None,
        }
        if ok:
            sent += 1
    return sent


def apply_unconfirmed_default(state: dict, now: datetime, today_iso: str) -> int:
    """At/after scheduled_start with decision=None → mark unconfirmed_default=True.
    Returns count of records updated."""
    flips = 0
    for key, rec in state.get("start_notified", {}).items():
        if not isinstance(key, str) or not key.startswith(f"{today_iso}:"):
            continue
        if not isinstance(rec, dict):
            continue
        if rec.get("decision") is not None:
            continue
        if rec.get("unconfirmed_default") is True:
            continue
        scheduled = rec.get("scheduled")
        if not scheduled:
            continue
        try:
            sched_dt = datetime.fromisoformat(scheduled)
        except ValueError:
            continue
        if sched_dt.tzinfo is None:
            sched_dt = sched_dt.replace(tzinfo=WARSAW)
        if now >= sched_dt:
            rec["unconfirmed_default"] = True
            state_mod.append_learning_log({
                "event": "SHIFT_UNCONFIRMED_DEFAULT",
                "full_name": key.split(":", 1)[1],
                "cid": rec.get("cid"),
                "scheduled": scheduled,
            })
            flips += 1
    return flips


# ----- main tick ----------------------------------------------------------


def main() -> int:
    """Single tick. Returns 0 on success, non-zero on hard error."""
    try:
        flags = load_flags()
    except Exception as e:
        _log.warning(f"load_flags failed: {type(e).__name__}: {e} — exit 0 (no-op)")
        return 0

    if not bool(flags.get("SHIFT_NOTIFY_ENABLED", False)):
        _log.debug("SHIFT_NOTIFY_ENABLED=False — exit no-op")
        return 0

    now = datetime.now(WARSAW)
    today_iso = now.date().isoformat()

    # Load schedule via module-level binding (test monkey-patchable)
    try:
        schedule = load_schedule()
    except Exception as e:
        _log.warning(f"load_schedule failed: {type(e).__name__}: {e}")
        return 0
    if not schedule:
        _log.debug("Empty schedule — exit no-op")
        return 0

    kurier_ids = _load_kurier_ids()
    templates = _import_templates()

    batch_window_min = int(flags.get("SHIFT_BATCH_WINDOW_MIN", 10))
    batch_min_couriers = int(flags.get("SHIFT_BATCH_MIN_COURIERS", 3))

    sub_b1 = bool(flags.get("SHIFT_NOTIFY_T60_START_ENABLED", False))
    sub_b2 = bool(flags.get("SHIFT_NOTIFY_T30_REMINDER_ENABLED", False))
    sub_b3 = bool(flags.get("SHIFT_NOTIFY_T60_END_ENABLED", False))

    sent_b1 = sent_b2 = sent_b3 = flips = 0
    with state_mod.locked_write_confirmations() as state:
        if sub_b1:
            sent_b1 = run_b1_start(schedule, now, state, kurier_ids, today_iso,
                                   batch_window_min, batch_min_couriers, templates)
        if sub_b2:
            sent_b2 = run_b2_reminder(schedule, now, state, kurier_ids, today_iso, templates)
        if sub_b3:
            sent_b3 = run_b3_end(schedule, now, state, kurier_ids, today_iso, templates)
        flips = apply_unconfirmed_default(state, now, today_iso)

    _log.info(
        f"shift_notify tick: B1={sent_b1} B2={sent_b2} B3={sent_b3} default_flips={flips}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
