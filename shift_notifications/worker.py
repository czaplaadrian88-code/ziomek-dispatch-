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
import time
from datetime import datetime, date, timedelta
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple
from zoneinfo import ZoneInfo

# Make scripts/ a sibling for `import schedule_utils` (auto_koord pattern)
_SCRIPTS_DIR = "/root/.openclaw/workspace/scripts"
if _SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, _SCRIPTS_DIR)

from dispatch_v2.common import setup_logger, load_flags
from dispatch_v2.identity.normalize import score_worker_alias
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
    """Map schedule full_name → courier_id (score-based v2 — Tech debt #14, 2026-05-07).

    Schedule keys are full names ('Adrian Citko'). kurier_ids.json keys are
    panel display names ('Adrian Cit', 'Adrian R', 'Adrian'). Pre-#14 algo
    używał fixed-length prefixów (last[:1], last[:2]) → fallback na first-name
    unique match → bug: 'Adrian Citko' resolwowane na cid=21 (Adrian Czapla)
    zamiast 457 (Adrian Cit). Lekcja #78 problem.

    v2 strategy:
      1. Exact match (case-sensitive) → return cid
      2. Case-insensitive exact → return cid
      3. Score-based fallback: dla każdego alias z tym samym first-name compute
         similarity score:
           - alias = bare first-name (e.g. "Adrian"): score = 1
           - alias z surname:
             * schedule s_last starts with alias a_last → score = len(a_last) * 10
             * alias a_last starts with s_last (rare) → score = len(s_last) * 5
             * else → 0
         Pick highest score. Tie → ambiguous (return None). All-zero → None.
    """
    if not full_name:
        return None
    if kurier_ids is None:
        kurier_ids = _load_kurier_ids()
    if not kurier_ids:
        return None

    # 1. Exact match (case-sensitive)
    if full_name in kurier_ids:
        return kurier_ids[full_name]

    # 2. Case-insensitive exact match
    fn_lc = full_name.lower()
    for key, cid in kurier_ids.items():
        if key.lower() == fn_lc:
            return cid

    # 3. Score-based fallback
    parts = full_name.strip().split()
    if not parts:
        return None
    first_lc = parts[0].lower()

    scored: List[Tuple[int, str, str]] = []  # (score, cid, alias_key)
    for alias, cid in kurier_ids.items():
        # Delegate the ×10/×5 scoring rule to the canonical strategy (Z-P1-05
        # Faza B). Scores are identical; worker keeps the exact / case-insensitive
        # match, tie detection and RESOLVE_CID_AMBIGUOUS_* debug logging below.
        score = score_worker_alias(full_name, alias)
        if score > 0:
            scored.append((score, cid, alias))

    if not scored:
        return None

    scored.sort(key=lambda x: -x[0])
    best_score, best_cid, best_alias = scored[0]
    if len(scored) > 1 and scored[1][0] == best_score:
        # Tie → ambiguous. Log + return None (caller emits UNMAPPED_COURIER alert).
        try:
            # ETAP 3 krok 2 (2026-06-10): debug matchowania → match_debug log
            state_mod.append_match_debug_log({
                "event": "RESOLVE_CID_AMBIGUOUS_TIE",
                "full_name": full_name,
                "tied_aliases": [a for _, _, a in scored if _ == best_score],
                "tied_score": best_score,
            })
        except Exception:
            pass  # never fail resolve_cid on log error
        return None

    # Score-based winner picked. Log if >1 same-first-name candidate (audit calibration).
    if len(scored) > 1 or any(
        a.split() and a.split()[0].lower() == first_lc
        for a in kurier_ids.keys() if a != best_alias
    ):
        try:
            # ETAP 3 krok 2 (2026-06-10): debug matchowania → match_debug log
            state_mod.append_match_debug_log({
                "event": "RESOLVE_CID_AMBIGUOUS_RESOLVED",
                "full_name": full_name,
                "winner_alias": best_alias,
                "winner_score": best_score,
                "winner_cid": best_cid,
                "alternatives": [
                    {"alias": a, "score": s, "cid": c}
                    for s, c, a in scored[1:]
                ],
            })
        except Exception:
            pass
    return best_cid


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


def _is_garbage_name(name: str) -> bool:
    """Heurystyka: 'name' z grafiku to komentarz/notatka, nie imie kuriera.

    Filtruje:
    - przecinek (Opony, odpisac na maila carefleetu)
    - >4 slow (lozysko czy cos, zatkany spryskiwacz, sprawdzic klocki)
    - pierwsza litera mala (slychac lozysko, zapala sie check)

    NIE wykryje krotkich z duza pierwsza litera typu 'Aku pada' (akceptowalny
    false-negative; w grafiku takie wpisy zwykle maja entry=None i tak skip'ne).
    """
    if not name or not name.strip():
        return True
    s = name.strip()
    if "," in s:
        return True
    if len(s.split()) > 4:
        return True
    if s[0].islower():
        return True
    return False


IGNORED_NAMES_PATH = '/root/.openclaw/workspace/dispatch_state/shift_ignored_names.json'


def _load_ignored_names() -> set:
    """Returns set of full_names z shift_ignored_names.json (permanent inactive).
    Empty set on FileNotFoundError (fail-open) lub corrupt JSON (logged).
    File schema: {"names": ["Daniel Malicki", ...], "comment": "..."}
    """
    try:
        with open(IGNORED_NAMES_PATH) as f:
            data = json.load(f)
        names = data.get('names') if isinstance(data, dict) else data
        return {str(n) for n in (names or [])}
    except FileNotFoundError:
        return set()
    except Exception as e:
        _log.warning(f'_load_ignored_names fail: {type(e).__name__}: {e}')
        return set()


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
    ignored_names: set = frozenset(),
) -> List[Candidate]:
    """Couriers whose scheduled START is in [now+low_min, now+high_min]."""
    out: List[Candidate] = []
    seen_keys = set()
    for full_name, entry in (schedule or {}).items():
        if _is_garbage_name(full_name):
            continue
        if full_name in ignored_names:
            today_logged = state.setdefault('shift_ignored_logged', {}).setdefault(today_iso, [])
            if full_name not in today_logged:
                state_mod.append_learning_log({
                    'event': 'SHIFT_IGNORED',
                    'full_name': full_name,
                    'reason': 'permanent_inactive_skiplist',
                })
                today_logged.append(full_name)
                state_mod.save_state(state)
            continue
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
            # ETAP B: alert do Adriana z idempotencja per dzien
            today_alerts = state.setdefault("unmapped_alerts", {}).setdefault(today_iso, [])
            if full_name not in today_alerts:
                try:
                    from urllib.parse import quote
                    b64 = quote(full_name, safe="")
                    text = (
                        f"\U0001F195 Nowy kurier? Grafik ma '{full_name}' ale brak w kurier_ids.json.\n"
                        f"Aby dopisac: odpowiedz `/dopisz <cid> {full_name}` (np. `/dopisz 525 {full_name}`).\n"
                        f"PIN wygeneruje sie automatycznie."
                    )
                    keyboard = [[{"text": "❌ Pomin dzisiaj", "callback_data": f"NEWCOURIER:skip:{b64}"}]]
                    tg_send_text_with_keyboard(text, keyboard)
                    today_alerts.append(full_name)
                    state_mod.save_state(state)
                except Exception as e:
                    _log.warning(f"unmapped_courier_alert send fail: {type(e).__name__}: {e}")
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
    *,
    ignored_names: set = frozenset(),
) -> List[Candidate]:
    """Couriers whose scheduled END is in [now+low_min, now+high_min]."""
    out: List[Candidate] = []
    seen_keys = set()
    for full_name, entry in (schedule or {}).items():
        if _is_garbage_name(full_name):
            continue
        if full_name in ignored_names:
            today_logged = state.setdefault('shift_ignored_logged', {}).setdefault(today_iso, [])
            if full_name not in today_logged:
                state_mod.append_learning_log({
                    'event': 'SHIFT_IGNORED',
                    'full_name': full_name,
                    'reason': 'permanent_inactive_skiplist',
                })
                today_logged.append(full_name)
                state_mod.save_state(state)
            continue
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
    *,
    ignored_names: set = frozenset(),
) -> int:
    """B.1 — T-60 START. Returns count of notifications sent."""
    cands = _build_candidates_starting(
        schedule, now, T60_WINDOW_LOW_MIN, T60_WINDOW_HIGH_MIN,
        kurier_ids, state, today_iso,
        ignored_names=ignored_names,
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
    *,
    ignored_names: Optional[set] = None,
) -> int:
    """B.2 — T-30 REMINDER for couriers with decision=None."""
    if ignored_names is None:
        ignored_names = set()
    sent = 0
    notified_at = now.isoformat()
    for full_name, entry in (schedule or {}).items():
        if _is_garbage_name(full_name):
            continue
        if full_name in ignored_names:
            today_logged = state.setdefault('shift_ignored_logged', {}).setdefault(today_iso, [])
            if full_name not in today_logged:
                state_mod.append_learning_log({
                    'event': 'SHIFT_IGNORED',
                    'full_name': full_name,
                    'reason': 'permanent_inactive_skiplist',
                })
                today_logged.append(full_name)
                state_mod.save_state(state)
            continue
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
    *,
    ignored_names: set = frozenset(),
) -> int:
    """B.3 — T-60 END. Always individual (per spec) even if multiple couriers in same slot."""
    cands = _build_candidates_ending(
        schedule, now, T60_WINDOW_LOW_MIN, T60_WINDOW_HIGH_MIN,
        kurier_ids, state, today_iso,
        ignored_names=ignored_names,
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


# === MP-#15 (2026-05-08): Schedule staleness + daily backup ===

_MP15_STALE_THRESHOLD_SEC = 30 * 60  # 30 min per master plan TOP-15 #15
_MP15_ALERT_DEDUP_SEC = 30 * 60  # one alert per 30min (no spam)
_MP15_STATE_PATH = "/root/.openclaw/workspace/dispatch_state/mp15_schedule_staleness.json"

# Pkt 1 (2026-06-15): okno operacyjne alertu STALE_SCHEDULE_AGE.
# Root cause nocnych false-positive: schedule_today.json odświeża się leniwie
# (load_schedule TTL 10min) TYLKO gdy żywy konsument dispatchu go woła + cron
# 06:00/08:00. W nocy ruch zleceń = 0 → nikt nie woła → plik naturalnie starzeje
# się ponad 30min → alarm, mimo że nie ma zmian ani nowych kurierów do
# przegapienia ("nowi kurierzy nie widoczni" jest w nocy bezprzedmiotowe).
# Rozwiązanie: alarmuj tylko w oknie operacyjnym [START, END) Warsaw. Realny
# sygnał (Sheets API down rano/w dzień) zostaje; nocny szum znika. Env-override.
_MP15_STALE_ALERT_HOUR_START = int(os.environ.get("MP15_STALE_ALERT_HOUR_START", "6"))
_MP15_STALE_ALERT_HOUR_END = int(os.environ.get("MP15_STALE_ALERT_HOUR_END", "23"))


def _mp15_load_state() -> dict:
    """Load MP-#15 alert dedup state. Fail-open na empty dict."""
    try:
        with open(_MP15_STATE_PATH, "r", encoding="utf-8") as f:
            return json.load(f) or {}
    except FileNotFoundError:
        return {}
    except Exception as e:
        _log.warning(f"MP-#15 state load fail: {e} — fresh state")
        return {}


def _mp15_save_state(state: dict) -> None:
    """Atomic write MP-#15 alert state. Fail-loud (warning) na I/O error."""
    try:
        from dispatch_v2.core.jsonl_appender import _append_bytes  # reuse atomic primitive
        # Direct atomic write json (NIE append) — overwrite
        import tempfile
        target = Path(_MP15_STATE_PATH)
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".mp15_state_", suffix=".tmp", dir=str(target.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(state, f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, target)
        except Exception:
            try:
                os.unlink(tmp)
            except Exception:
                pass
            raise
    except Exception as e:
        _log.warning(f"MP-#15 state save fail: {type(e).__name__}: {e}")


def _mp15_check_schedule_staleness(now, today_iso: str) -> None:
    """MP-#15 (2026-05-08): alert gdy schedule_age > 30min. Dedup max 1×/30min.

    Czasówka rozróżnia:
      - schedule >30min stale → STALE_SCHEDULE_AGE alert (HIGH severity per OPS R6)
      - dispatch nadal działa (load_schedule fail-open per Lekcja #31)
      - alert mówi operatorowi "Sheets API rate-limit / network issue, possibly
        nowi kurierzy nie widoczni do następnego refresh"

    Defensive: NIGDY raise (worker krytyczny). Logged warnings na exception.

    Pkt 1 (2026-06-15): alarm tylko w oknie operacyjnym Warsaw
    [_MP15_STALE_ALERT_HOUR_START, _MP15_STALE_ALERT_HOUR_END). Poza nim
    (noc) stale grafiku jest nieszkodliwe — eliminuje nocne false-positive.
    """
    try:
        # Pkt 1: okno operacyjne — w nocy NIE alarmuj (no-op). `now` to czas
        # warszawski (datetime.now(WARSAW) z main()).
        try:
            hour = now.hour
        except Exception:
            hour = None
        if hour is not None and not (
            _MP15_STALE_ALERT_HOUR_START <= hour < _MP15_STALE_ALERT_HOUR_END
        ):
            return

        # Lazy import — tests mogą monkey-patch w schedule_utils
        try:
            from schedule_utils import schedule_age_sec
        except ImportError:
            return  # script-only environment

        age = schedule_age_sec()
        if age is None:
            # File missing — bigger problem niż stale, separate alert path future
            return
        if age <= _MP15_STALE_THRESHOLD_SEC:
            return

        # Stale → check dedup
        mp15_state = _mp15_load_state()
        last_alert = float(mp15_state.get("last_stale_alert_ts", 0))
        if time.time() - last_alert < _MP15_ALERT_DEDUP_SEC:
            return

        age_min = int(age / 60)
        msg = (
            f"⚠ STALE_SCHEDULE_AGE — grafik {age_min} min nie był odświeżany "
            f"(threshold: 30 min). Sheet API rate-limit lub network issue. "
            f"Dispatch nadal działa (fail-open) ALE nowi kurierzy mogą nie być "
            f"widoczni do następnego refresh. Sprawdź `journalctl -u dispatch-shift-notify` "
            f"+ Google Sheets dostępność."
        )
        try:
            from dispatch_v2 import telegram_utils
            telegram_utils.send_admin_alert(msg)
        except Exception as e:
            _log.warning(f"MP-#15 alert Telegram send fail: {type(e).__name__}: {e}")
            # Continue — log alert nawet jak Telegram unreachable
            _log.error(f"MP-#15 STALE_SCHEDULE_AGE: schedule age {age_min}min > 30min threshold (alert NOT sent — Telegram down)")
            return

        # Update dedup state
        mp15_state["last_stale_alert_ts"] = time.time()
        mp15_state["last_stale_age_min"] = age_min
        mp15_state["last_stale_at"] = now.isoformat()
        _mp15_save_state(mp15_state)
        _log.warning(f"MP-#15 STALE_SCHEDULE_AGE alerted: {age_min}min stale, dedup armed for 30min")
    except Exception as e:
        _log.warning(f"MP-#15 staleness check unexpected fail: {type(e).__name__}: {e}")


def _mp15_maybe_write_daily_backup(now, today_iso: str) -> None:
    """MP-#15 (2026-05-08): write schedule_today_backup.json once per day.

    Idempotent w obrębie dnia (state field `last_backup_date_iso`). Worker tickuje
    co minutę, więc pierwszy tick po 06:00 Warsaw zapisze (gdy SCHEDULE_FILE
    fresh post-fetch). Defensive: failure logged ale NIE blokuje workera.
    """
    try:
        mp15_state = _mp15_load_state()
        if mp15_state.get("last_backup_date_iso") == today_iso:
            return  # already backed up today

        # Trigger only after 06:00 Warsaw (SCHEDULE_FILE refresh window)
        if now.hour < 6:
            return

        try:
            from schedule_utils import write_schedule_today_backup
        except ImportError:
            return

        ok = write_schedule_today_backup()
        if ok:
            mp15_state["last_backup_date_iso"] = today_iso
            mp15_state["last_backup_at"] = now.isoformat()
            _mp15_save_state(mp15_state)
            _log.info(f"MP-#15 daily backup written for {today_iso}")
    except Exception as e:
        _log.warning(f"MP-#15 daily backup unexpected fail: {type(e).__name__}: {e}")


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

    # MP-#15 (2026-05-08): STALE_SCHEDULE_AGE alert + daily backup snapshot.
    # Wykonywane PRZED load_schedule() żeby alert odpalił nawet gdy schedule
    # nieaktualny ALE worker tickuje (Sheets API rate-limit, network issue).
    _mp15_check_schedule_staleness(now, today_iso)
    _mp15_maybe_write_daily_backup(now, today_iso)

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
    ignored_names = _load_ignored_names()
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
                                   batch_window_min, batch_min_couriers, templates,
                                   ignored_names=ignored_names)
        if sub_b2:
            sent_b2 = run_b2_reminder(schedule, now, state, kurier_ids, today_iso, templates,
                                      ignored_names=ignored_names)
        if sub_b3:
            sent_b3 = run_b3_end(schedule, now, state, kurier_ids, today_iso, templates,
                                 ignored_names=ignored_names)
        flips = apply_unconfirmed_default(state, now, today_iso)

    _log.info(
        f"shift_notify tick: B1={sent_b1} B2={sent_b2} B3={sent_b3} default_flips={flips}"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
