"""TASK A CZASÓWKI PROACTIVE — T-50/T-40 trigger detection (2026-05-05).

Public API:
  maybe_fire_trigger(oid, osrec, mins_to_pickup, eval_result, now_utc)
    → returns trigger_min (int) if fire happened, None otherwise.

Flag gates (defaults all False per Adrian's hard rule):
  CZASOWKA_PROACTIVE_ENABLED       — master kill switch
  CZASOWKA_T50_ENABLED             — per-trigger granularity
  CZASOWKA_T40_ENABLED
  CZASOWKA_T0_ALERT_ENABLED        — T-0 unassigned alert (info-only)
  CZASOWKA_TRIGGERS_MIN            — list, default [50, 40] (extensible)
  CZASOWKA_MIN_PROPOSAL_SCORE      — int, default 60
  CZASOWKA_TRIGGER_TOLERANCE_MIN   — int, default 1 (window: |mins-trigger|<=tol)

Sister-agent (Agent B) provides templates:
  - format_czasowka_proposal(oid, restaurant, pickup_hhmm, candidate_name,
                              candidate_cid, score, trigger_min)
                              → (text, inline_keyboard) for T-50 (3 buttons)
  - format_czasowka_last_chance(oid, restaurant, pickup_hhmm, candidate_name,
                                 candidate_cid, score)
                                 → (text, inline_keyboard) for T-40 (2 buttons)
  - format_czasowka_no_candidate(oid, restaurant, pickup_hhmm, trigger_min,
                                  next_check_ts)
                                  → text (info-only, no keyboard)
  - format_czasowka_alert_unassigned(oid, restaurant, pickup_hhmm)
                                      → text (T-0 critical, info-only)

Defensive: import failures in templates fall back to a minimal stub so
the module is import-safe even before Agent B deploys templates.
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, List, Optional, Tuple
from zoneinfo import ZoneInfo

from dispatch_v2.common import flag, load_flags, setup_logger

LOG_DIR = "/root/.openclaw/workspace/scripts/logs/"
_log = setup_logger("czasowka_proactive.evaluator", LOG_DIR + "czasowka_proactive.log")

WARSAW = ZoneInfo("Europe/Warsaw")

# Adrian Z3 decision (2026-05-05): czasówka proposals → grupa ziomka.
# Backup naturalny (Adrian + Bartek + grupa widzą), Bartek philosophy
# ground truth wbudowana w flow, race-loss handler "first responder wins".
# Future: gdy Bartek zostanie Manager Gastro (Tydzień 4+), flag flip do DM
# bez code change — V3.29 backlog.
CZASOWKA_PROPOSAL_CHAT_ID = -5149910559


# ---------- internal helpers ----------

def _get_triggers_min() -> List[int]:
    """Read CZASOWKA_TRIGGERS_MIN from flags; default [50, 40]. Always returns
    a list of ints sorted DESC (T-50 evaluated before T-40)."""
    raw = load_flags().get("CZASOWKA_TRIGGERS_MIN", [50, 40])
    if not isinstance(raw, list):
        return [50, 40]
    out = []
    for x in raw:
        try:
            out.append(int(x))
        except (TypeError, ValueError):
            continue
    if not out:
        return [50, 40]
    return sorted(out, reverse=True)


def _get_int_flag(name: str, default: int) -> int:
    raw = flag(name, default=default)
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _format_pickup_hhmm(osrec: dict) -> str:
    """Best-effort HH:MM Warsaw render of pickup timestamp."""
    raw = (
        osrec.get("pickup_at_warsaw")
        or osrec.get("czas_odbioru_timestamp")
        or osrec.get("pickup_at")
    )
    if not raw:
        return "?"
    try:
        s = str(raw).replace("Z", "+00:00")
        dt = datetime.fromisoformat(s)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=WARSAW)
        return dt.astimezone(WARSAW).strftime("%H:%M")
    except Exception:
        return "?"


def _candidate_cid(c: Any) -> str:
    cid = getattr(c, "courier_id", None) or getattr(c, "cid", None) or ""
    return str(cid)


def _candidate_name(c: Any) -> str:
    return getattr(c, "name", None) or getattr(c, "panel_name", None) or "?"


def _candidate_score(c: Any) -> Optional[float]:
    s = getattr(c, "score", None)
    try:
        return float(s) if s is not None else None
    except (TypeError, ValueError):
        return None


def _is_maybe(c: Any) -> bool:
    v = getattr(c, "feasibility_verdict", None)
    return v == "MAYBE"


def _filter_candidates(
    eval_result: dict,
    excluded_cids: set,
    score_threshold: float,
) -> List[Any]:
    """Build feasible candidate list from eval_result['best'] + ['alternatives'].

    Filters: feasibility_verdict == MAYBE, courier_id NOT in excluded_cids,
             score >= threshold. Stable ordering (best-first if MAYBE,
             then alternatives in input order).

    F3 (2026-05-06): if flag CZASOWKA_PROACTIVE_USE_ALL_CANDIDATES is True,
    use eval_result['all_candidates_for_proactive'] as primary source.
    """
    if flag("CZASOWKA_PROACTIVE_USE_ALL_CANDIDATES", default=False):
        cands = eval_result.get("all_candidates_for_proactive", [])
    else:
        cands: List[Any] = []
        best = eval_result.get("best") if isinstance(eval_result, dict) else None
        if best is not None:
            cands.append(best)
        alts = eval_result.get("alternatives") if isinstance(eval_result, dict) else None
        if alts:
            cands.extend(alts)

    out = []
    for c in cands:
        if not _is_maybe(c):
            continue
        cid = _candidate_cid(c)
        if cid in excluded_cids:
            continue
        score = _candidate_score(c)
        if score is None or score < score_threshold:
            continue
        out.append(c)
    return out


# ---------- lazy template imports (Agent B-owned) ----------

def _stub_proposal(
    oid, restaurant, pickup_hhmm, name, cid, score, trigger_min
) -> Tuple[str, list]:
    text = (
        f"⏳ Czasówka {oid} pickup {pickup_hhmm}\n"
        f"{restaurant or '?'}\n"
        f"Propozycja: {name} (cid={cid}) score={score}\n"
        f"[STUB T-{trigger_min}]"
    )
    return text, [
        [
            {"text": "TAK", "callback_data": f"czasowka_propose_tak:{oid}:{cid}:{trigger_min}"},
            {"text": "NIE", "callback_data": f"czasowka_propose_nie:{oid}:{cid}:{trigger_min}"},
            {"text": "CZEKAJ", "callback_data": f"czasowka_propose_czekaj:{oid}:{cid}:{trigger_min}"},
        ]
    ]


def _stub_last_chance(oid, restaurant, pickup_hhmm, name, cid, score) -> Tuple[str, list]:
    text = (
        f"🚨 OSTATNIA SZANSA czasówka {oid} pickup {pickup_hhmm}\n"
        f"{restaurant or '?'}\n"
        f"Propozycja: {name} (cid={cid}) score={score}\n"
        f"[STUB T-40]"
    )
    return text, [
        [
            {"text": "TAK", "callback_data": f"czasowka_propose_tak:{oid}:{cid}:40"},
            {"text": "NIE", "callback_data": f"czasowka_propose_nie:{oid}:{cid}:40"},
        ]
    ]


def _stub_no_candidate(oid, restaurant, pickup_hhmm, trigger_min, next_check_ts) -> str:
    return (
        f"ℹ️ Czasówka {oid} ({restaurant or '?'}) pickup {pickup_hhmm}: "
        f"brak feasible kandydatów na T-{trigger_min}, retry {next_check_ts}\n[STUB]"
    )


def _stub_alert_unassigned(oid, restaurant, pickup_hhmm) -> str:
    return (
        f"🔥 ALERT czasówka {oid} ({restaurant or '?'}) pickup {pickup_hhmm} "
        f"NIEPRZYPISANA — koordynator MUSI zadziałać RĘCZNIE\n[STUB]"
    )


def _resolve_templates():
    """Lazy resolve Agent B's templates with fallback stubs."""
    try:
        from dispatch_v2.telegram import templates as _t
        proposal = getattr(_t, "format_czasowka_proposal", _stub_proposal)
        last_chance = getattr(_t, "format_czasowka_last_chance", _stub_last_chance)
        no_candidate = getattr(_t, "format_czasowka_no_candidate", _stub_no_candidate)
        alert_unassigned = getattr(
            _t, "format_czasowka_alert_unassigned", _stub_alert_unassigned
        )
        return proposal, last_chance, no_candidate, alert_unassigned
    except Exception:
        return _stub_proposal, _stub_last_chance, _stub_no_candidate, _stub_alert_unassigned


# ---------- public API ----------

def maybe_fire_trigger(
    oid: str,
    osrec: dict,
    mins_to_pickup: Optional[float],
    eval_result: dict,
    now_utc: datetime,
) -> Optional[int]:
    """Check if T-50/T-40 trigger window matches mins_to_pickup.
    On match: propose candidate via Telegram + persist state.

    Returns trigger_min (int) if a trigger fired (proposal sent or NO_CANDIDATE
    info sent), or T-0 alert fired. None otherwise.

    Defensive: on any internal error logs a warning and returns None.
    """
    # 1. Master kill switch
    if not flag("CZASOWKA_PROACTIVE_ENABLED", default=False):
        return None

    # T-0 alert path (unassigned at pickup time, special-case)
    if (
        flag("CZASOWKA_T0_ALERT_ENABLED", default=False)
        and mins_to_pickup is not None
        and abs(mins_to_pickup) <= _get_int_flag("CZASOWKA_TRIGGER_TOLERANCE_MIN", 1)
    ):
        cid_holding = str(
            osrec.get("courier_id") or osrec.get("id_kurier") or ""
        )
        if cid_holding == "26" or cid_holding == "":
            return _fire_t0_alert(oid, osrec, now_utc)

    # 2. Skip if no minutes (no pickup ts) or pickup already passed.
    if mins_to_pickup is None:
        return None
    if mins_to_pickup < 0:
        return None  # post-pickup window (T-0 handled above)

    # 3. Determine triggers + tolerance.
    triggers = _get_triggers_min()
    tolerance = _get_int_flag("CZASOWKA_TRIGGER_TOLERANCE_MIN", 1)

    # 4. Find first matching trigger (DESC iter — T-50 wins before T-40).
    trigger_min: Optional[int] = None
    for t in triggers:
        if abs(mins_to_pickup - t) <= tolerance:
            trigger_min = t
            break
    if trigger_min is None:
        return None

    # 5. Per-trigger flag gate (granular).
    per_trigger_flag = f"CZASOWKA_T{trigger_min}_ENABLED"
    if not flag(per_trigger_flag, default=False):
        _log.debug(f"czasowka_proactive: oid={oid} T-{trigger_min} per-trigger flag OFF")
        return None

    # 6. Idempotent: skip if already fired this trigger for this order.
    try:
        from dispatch_v2.czasowka_proactive.state import (
            locked_write_proposals_state,
            new_state_record,
        )
    except Exception as e:
        _log.warning(f"czasowka_proactive: state import fail oid={oid}: {e}")
        return None

    score_threshold = float(_get_int_flag("CZASOWKA_MIN_PROPOSAL_SCORE", 60))
    fired = False

    try:
        with locked_write_proposals_state() as st:
            orders = st.setdefault("orders", {})
            rec = orders.get(oid)
            if rec is None:
                rec = new_state_record(oid, osrec, now_utc)
                orders[oid] = rec

            triggers_fired = rec.setdefault("triggers_fired", {})
            if str(trigger_min) in triggers_fired:
                # Already fired this tick for this trigger — idempotent skip.
                return None

            excluded_cids = set(
                str(x) for x in rec.get("excluded_candidates", []) or []
            )

            # 7. Filter candidates.
            feasible = _filter_candidates(eval_result, excluded_cids, score_threshold)

            # Templates resolve once.
            (
                fmt_proposal,
                fmt_last_chance,
                fmt_no_candidate,
                _fmt_alert,
            ) = _resolve_templates()

            pickup_hhmm = _format_pickup_hhmm(osrec)
            restaurant = osrec.get("restaurant")

            # Lazy import telegram_send so module is import-safe in tests w/o env.
            try:
                from dispatch_v2.shift_notifications.telegram_send import (
                    tg_send_text_with_keyboard,
                )
            except Exception as e:
                _log.warning(f"czasowka_proactive: tg_send import fail oid={oid}: {e}")
                tg_send_text_with_keyboard = None

            # Lazy import observability hook.
            try:
                from dispatch_v2.czasowka_proactive.observability import (
                    log_proactive_trigger,
                )
            except Exception:
                log_proactive_trigger = None

            if not feasible:
                # NO_CANDIDATE info-only path.
                next_trigger = _next_trigger_after(trigger_min, triggers)
                next_check_ts = (
                    f"T-{next_trigger}" if next_trigger is not None else "—"
                )
                text = fmt_no_candidate(
                    oid, restaurant, pickup_hhmm, trigger_min, next_check_ts
                )
                if tg_send_text_with_keyboard is not None:
                    try:
                        tg_send_text_with_keyboard(
                            text, [], chat_id=CZASOWKA_PROPOSAL_CHAT_ID
                        )
                    except Exception as e:
                        _log.warning(f"czasowka_proactive: tg send fail oid={oid}: {e}")
                triggers_fired[str(trigger_min)] = {
                    "ts": now_utc.isoformat(),
                    "proposed_cid": None,
                    "proposed_name": None,
                    "score": None,
                    "decision": "NO_CANDIDATE",
                    "decision_ts": now_utc.isoformat(),
                }
                if log_proactive_trigger:
                    log_proactive_trigger(
                        oid=oid,
                        trigger_min=trigger_min,
                        candidates=[],
                        picked=None,
                        decision_verdict="NO_CANDIDATE",
                        now_utc=now_utc,
                        excluded_cids=excluded_cids,
                        score_threshold=score_threshold,
                    )
                fired = True
            else:
                # Propose top feasible candidate.
                pick = feasible[0]
                pick_cid = _candidate_cid(pick)
                pick_name = _candidate_name(pick)
                pick_score = _candidate_score(pick)
                if trigger_min == 50:
                    text, kb = fmt_proposal(
                        oid, restaurant, pickup_hhmm, pick_name, pick_cid,
                        pick_score, trigger_min,
                    )
                elif trigger_min == 40:
                    text, kb = fmt_last_chance(
                        oid, restaurant, pickup_hhmm, pick_name, pick_cid, pick_score,
                    )
                else:
                    # Extension trigger (e.g., T-70/T-60/T-45) → reuse 3-button proposal.
                    text, kb = fmt_proposal(
                        oid, restaurant, pickup_hhmm, pick_name, pick_cid,
                        pick_score, trigger_min,
                    )

                if tg_send_text_with_keyboard is not None:
                    try:
                        tg_send_text_with_keyboard(
                            text, kb, chat_id=CZASOWKA_PROPOSAL_CHAT_ID
                        )
                    except Exception as e:
                        _log.warning(f"czasowka_proactive: tg send fail oid={oid}: {e}")

                triggers_fired[str(trigger_min)] = {
                    "ts": now_utc.isoformat(),
                    "proposed_cid": pick_cid,
                    "proposed_name": pick_name,
                    "score": pick_score,
                    "decision": None,  # set later by callback router (Agent B)
                    "decision_ts": None,
                }
                if log_proactive_trigger:
                    log_proactive_trigger(
                        oid=oid,
                        trigger_min=trigger_min,
                        candidates=feasible,
                        picked=pick,
                        decision_verdict="PROPOSED",
                        now_utc=now_utc,
                        excluded_cids=excluded_cids,
                        score_threshold=score_threshold,
                    )
                fired = True
    except Exception as e:
        _log.warning(
            f"czasowka_proactive: maybe_fire_trigger oid={oid} fail "
            f"{type(e).__name__}: {e}"
        )
        return None

    return trigger_min if fired else None


def _next_trigger_after(current: int, triggers: List[int]) -> Optional[int]:
    """Given current trigger and DESC-sorted triggers list, return the next
    smaller trigger (e.g. current=50 → 40). None if current is the last."""
    smaller = [t for t in triggers if t < current]
    if not smaller:
        return None
    return max(smaller)


def _fire_t0_alert(oid: str, osrec: dict, now_utc: datetime) -> Optional[int]:
    """Special-case T-0 alert: czasówka still unassigned (id_kurier=26 or empty)
    at pickup_at moment. Idempotent via triggers_fired['0']. Info-only.
    """
    try:
        from dispatch_v2.czasowka_proactive.state import (
            locked_write_proposals_state,
            new_state_record,
        )
    except Exception:
        return None

    try:
        with locked_write_proposals_state() as st:
            orders = st.setdefault("orders", {})
            rec = orders.get(oid)
            if rec is None:
                rec = new_state_record(oid, osrec, now_utc)
                orders[oid] = rec
            triggers_fired = rec.setdefault("triggers_fired", {})
            if "0" in triggers_fired:
                return None  # idempotent

            (_p, _lc, _nc, fmt_alert) = _resolve_templates()
            pickup_hhmm = _format_pickup_hhmm(osrec)
            text = fmt_alert(oid, osrec.get("restaurant"), pickup_hhmm)

            try:
                from dispatch_v2.shift_notifications.telegram_send import (
                    tg_send_text_with_keyboard,
                )
                tg_send_text_with_keyboard(
                    text, [], chat_id=CZASOWKA_PROPOSAL_CHAT_ID
                )
            except Exception as e:
                _log.warning(f"czasowka_proactive: T0 alert tg fail oid={oid}: {e}")

            triggers_fired["0"] = {
                "ts": now_utc.isoformat(),
                "proposed_cid": None,
                "proposed_name": None,
                "score": None,
                "decision": "ALERT_T0",
                "decision_ts": now_utc.isoformat(),
            }

            try:
                from dispatch_v2.czasowka_proactive.observability import (
                    log_proactive_trigger,
                )
                log_proactive_trigger(
                    oid=oid, trigger_min=0, candidates=[], picked=None,
                    decision_verdict="ALERT_T0", now_utc=now_utc,
                    excluded_cids=set(), score_threshold=None,
                )
            except Exception:
                pass
        return 0
    except Exception as e:
        _log.warning(f"czasowka_proactive: T0 alert fail oid={oid}: {e}")
        return None
