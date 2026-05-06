"""TASK A CZASÓWKI PROACTIVE — Telegram callback handlers (2026-05-05).

Sister-agent module to czasowka_proactive/state.py + observability.py.
This file owns the 3 callback handlers fired from telegram_approver router
fork on prefixes CZAS_TAK / CZAS_NIE / CZAS_CZEKAJ.

Callback data shape (set in telegram/templates.py format_czasowka_proposal +
format_czasowka_last_chance):
  CZAS_{ACTION}:{oid}:{cid}:{trigger_min}

Per spec (TASK A 2026-05-05):
  - TAK   → race-precheck (panel id_kurier still 26?), if free run subprocess
            gastro_assign(oid, name_lookup(cid), compute_assign_time(...),
            koordynator=False); state mutate decision="TAK" + final_assignment_*;
            edit message.
  - NIE   → state mutate decision="NIE" + excluded_candidates += [cid];
            kandydat NIE wraca w T-40 re-eval; edit message.
  - CZEKAJ → state mutate decision="CZEKAJ" (NO exclusion — kandydat może
            wrócić w T-40); edit message.

Master flag: CZASOWKA_PROACTIVE_ENABLED (default False, Adrian's hard rule).
Idempotency: any second click on already-decided trigger (decision != None)
returns "ℹ już zapisane" answerCallbackQuery + skip mutation.

Defensive imports (lazy): state.py i panel_client są importowane WEWNĄTRZ
funkcji handlera, NIE module-level — tak że telegram_approver bot startuje
mimo niepełnego sister-modułu (Lekcja #41 Python local imports shadow
globals — ale tu używamy całych modułów bez `from X import Y` aliasing).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Optional

from dispatch_v2.common import flag

_log = logging.getLogger("czasowka_proactive.handlers")

KOORDYNATOR_ID = "26"


# ============================================================
# Internal helpers — Telegram I/O wrappers (defensive)
# ============================================================

def _answer_cb(state: dict, cb: dict, feedback: str) -> None:
    """Wrapper around tg_request answerCallbackQuery. Defensive."""
    try:
        # Lazy import — telegram_approver.tg_request is the canonical sender.
        from dispatch_v2 import telegram_approver as ta
        ta.tg_request(
            state["token"], "answerCallbackQuery",
            {"callback_query_id": cb["id"], "text": feedback},
        )
    except Exception as e:
        _log.warning(
            f"_answer_cb fail: {type(e).__name__}: {e} feedback={feedback!r}"
        )


def _edit_message_text(state: dict, cb: dict, new_text: str) -> None:
    """editMessageText helper — replace original proposal text with decision summary.

    Strips inline keyboard implicitly (editMessageText resets reply_markup if
    not provided, ale dla pewności wysyłamy {"inline_keyboard": []}).
    Defensive: NIGDY raise.
    """
    try:
        from dispatch_v2 import telegram_approver as ta
        msg = cb.get("message") or {}
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        message_id = msg.get("message_id")
        if chat_id is None or message_id is None:
            _log.warning(
                f"_edit_message_text: missing chat_id/message_id "
                f"chat={chat_id} mid={message_id}"
            )
            return
        ta.tg_request(
            state["token"], "editMessageText",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "text": new_text,
                "reply_markup": {"inline_keyboard": []},
            },
        )
    except Exception as e:
        _log.warning(f"_edit_message_text fail: {type(e).__name__}: {e}")


def _edit_kb_strip(state: dict, cb: dict) -> None:
    """editMessageReplyMarkup z empty inline_keyboard — strip buttons.

    Used gdy nie chcemy zmienić tekstu (np. race lost — text edit wystarczy
    tylko gdy mamy nowy text). Defensive.
    """
    try:
        from dispatch_v2 import telegram_approver as ta
        msg = cb.get("message") or {}
        chat = msg.get("chat") or {}
        chat_id = chat.get("id")
        message_id = msg.get("message_id")
        if chat_id is None or message_id is None:
            return
        ta.tg_request(
            state["token"], "editMessageReplyMarkup",
            {
                "chat_id": chat_id,
                "message_id": message_id,
                "reply_markup": {"inline_keyboard": []},
            },
        )
    except Exception as e:
        _log.warning(f"_edit_kb_strip fail: {type(e).__name__}: {e}")


def _name_lookup(cid: str) -> str:
    """cid → 'Imię N.' resolution z telegram_approver.name_lookup.

    Lazy import (telegram_approver.py jest ciężki, NIE chcemy module-level dep).
    Returns 'K{cid}' fallback gdy lookup faili.
    """
    try:
        from dispatch_v2 import telegram_approver as ta
        return ta.name_lookup(cid, None)
    except Exception as e:
        _log.warning(f"_name_lookup fail cid={cid}: {type(e).__name__}: {e}")
        return f"K{cid}"


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _parse_raw_oid(raw_oid: str) -> Optional[tuple]:
    """Split '{oid}:{cid}:{trigger_min}' → (oid, cid, trigger_min:int).

    Returns None na malformed input.
    """
    parts = raw_oid.split(":")
    if len(parts) != 3:
        return None
    oid, cid, trigger_min_str = parts[0], parts[1], parts[2]
    try:
        trigger_min = int(trigger_min_str)
    except (TypeError, ValueError):
        return None
    if not oid or not cid:
        return None
    return oid, cid, trigger_min


def _trigger_record_for(rec: dict, trigger_min: int) -> Optional[dict]:
    """rec is a state["orders"][oid] dict. Returns triggers_fired[trigger_min]
    sub-dict or None gdy nie ma. trigger_min przechowywany jako string klucz
    (zgodnie ze schematem state.py)."""
    if not isinstance(rec, dict):
        return None
    triggers = rec.get("triggers_fired") or {}
    if not isinstance(triggers, dict):
        return None
    sub = triggers.get(str(trigger_min))
    if isinstance(sub, dict):
        return sub
    return None


# ============================================================
# 3 callback handlers — TAK / NIE / CZEKAJ
# ============================================================

def handle_czas_tak(state: dict, action: str, raw_oid: str, cb: dict) -> None:
    """CZAS_TAK callback — przypisz kuriera czasówce.

    raw_oid = '{oid}:{cid}:{trigger_min}'.

    Race detection: pre-check fetch_order_details (czy nadal id_kurier=26),
    jeśli nie → log RACE_LOST + skip panel assign + edit msg.
    Idempotent: drugi click w 100ms widzi triggers_fired[trigger_min].decision
    != None → "ℹ już zapisane".
    """
    if not flag("CZASOWKA_PROACTIVE_ENABLED", default=False):
        _answer_cb(state, cb, "wyłączone")
        return

    parsed = _parse_raw_oid(raw_oid)
    if parsed is None:
        _answer_cb(state, cb, "❌ malformed callback")
        _log.warning(f"handle_czas_tak: malformed raw_oid={raw_oid!r}")
        return
    oid, cid, trigger_min = parsed

    # Lazy import (Agent A may finish state.py later; defense in depth).
    try:
        from dispatch_v2.czasowka_proactive.state import locked_write_proposals_state
    except ImportError as e:
        _answer_cb(state, cb, f"❌ state module unavailable: {e}")
        _log.error(f"handle_czas_tak state import failed: {e}")
        return

    # ---------- Idempotency precheck ----------
    # Read-then-write under exclusive lock to avoid TOCTOU between two clicks.
    name = _name_lookup(cid)
    try:
        with locked_write_proposals_state() as proposals:
            orders = proposals.setdefault("orders", {})
            rec = orders.get(oid)
            if not isinstance(rec, dict):
                _answer_cb(state, cb, f"⚠ Brak rekordu czasówki #{oid}")
                _log.warning(f"handle_czas_tak: no rec for oid={oid}")
                return
            sub = _trigger_record_for(rec, trigger_min)
            if isinstance(sub, dict) and sub.get("decision") is not None:
                # Already decided — idempotent answer, no panel call, no edit.
                _answer_cb(state, cb, "ℹ już zapisane")
                _log.info(
                    f"handle_czas_tak idempotent oid={oid} trigger={trigger_min} "
                    f"prior_decision={sub.get('decision')}"
                )
                return

            # ---------- Race precheck (panel still id_kurier=26?) ----------
            # Adrian Z3 decision (2026-05-05): REJECT scenario gdy id_kurier=None
            # — anomalia, NIE expected state. Operator intent preserved (ktoś
            # zmienił stan w panelu = jest powód). Lekcja #71 "STOP, nie zgaduj".
            race_lost = False
            race_decision = ""  # RACE_LOST_ALREADY_ASSIGNED | REJECT_RACE_ID_KURIER_NONE | ""
            race_reason = ""
            cur_kid_str = ""
            try:
                from dispatch_v2 import panel_client
                raw = panel_client.fetch_order_details(str(oid))
                if raw is None:
                    race_lost = True
                    race_decision = "REJECT_RACE_FETCH_NONE"
                    race_reason = "fetch_returned_none"
                else:
                    cur_kid = raw.get("id_kurier")
                    cur_kid_str = str(cur_kid) if cur_kid is not None else ""
                    if cur_kid_str == "" or cur_kid_str == "None" or cur_kid_str == "0":
                        # id_kurier=None — anomalia, REJECT z log (Adrian Z3)
                        race_lost = True
                        race_decision = "REJECT_RACE_ID_KURIER_NONE"
                        race_reason = "id_kurier=None"
                    elif cur_kid_str != KOORDYNATOR_ID:
                        # Already assigned to inny real cid (Adrian manual lub automatic)
                        race_lost = True
                        race_decision = "RACE_LOST_ALREADY_ASSIGNED"
                        race_reason = f"id_kurier={cur_kid_str}"
                    # else: id_kurier == "26" → OK kontynuuj assign
            except Exception as e:
                # Defensive — proceed cautiously like auto_koord (NIE block).
                _log.warning(
                    f"handle_czas_tak race precheck fail oid={oid}: "
                    f"{type(e).__name__}: {e} — proceeding"
                )

            if race_lost:
                # Update state z race decision, NIE call subprocess.
                triggers = rec.setdefault("triggers_fired", {})
                key = str(trigger_min)
                existing = triggers.get(key) if isinstance(triggers, dict) else None
                base = dict(existing) if isinstance(existing, dict) else {}
                base.update({
                    "proposed_cid": cid,
                    "proposed_name": name,
                    "decision": race_decision,
                    "decision_ts": _now_utc().isoformat(),
                    "race_reason": race_reason,
                })
                triggers[key] = base
                # Two distinct edit messages per Adrian Z3 spec:
                if race_decision == "REJECT_RACE_ID_KURIER_NONE":
                    edit_text = (
                        f"⚠️ Czasówka #{oid} — stan się zmienił\n"
                        f"Order id_kurier=None (zostawiony w panelu).\n"
                        f"Ręczne dispatch wymagane."
                    )
                    cb_feedback = "⚠ id_kurier=None — manual"
                elif race_decision == "RACE_LOST_ALREADY_ASSIGNED":
                    edit_text = (
                        f"✅ Już przypisane przez panel: cid={cur_kid_str}\n"
                        f"Czasówka #{oid} (T-{trigger_min})."
                    )
                    cb_feedback = f"⚠ już przypisane (cid={cur_kid_str})"
                else:
                    edit_text = (
                        f"⚠ Czasówka #{oid} — fetch fail\n"
                        f"Reason: {race_reason}. Sprawdź panel."
                    )
                    cb_feedback = f"⚠ fetch_none"
                _answer_cb(state, cb, cb_feedback)
                _edit_message_text(state, cb, edit_text)
                _log.info(
                    f"handle_czas_tak race_decision={race_decision} oid={oid} "
                    f"cid={cid} reason={race_reason}"
                )
                return

            # ---------- Panel assignment via subprocess (no panel_client.assign_courier) ----------
            # compute_assign_time wymaga decision_record dict (best.travel_min +
            # pickup_ready_at). Tu brak takiego — używamy konserwatywnego
            # default 5 min (T-50/T-40 minutes-to-pickup już potencjalnie blisko).
            # Dispatcher_pipeline shadow w future Faza 7 może podać lepszy hint
            # via state record — defer.
            try:
                from dispatch_v2 import telegram_approver as ta
                # Default 5-min slot. Bez ranking decision_record, gastro_assign
                # przyjmuje --time int minutes (panel R27 ±5 acceptable).
                ok, msg = ta.run_gastro_assign(oid, name, 5, False)
            except Exception as e:
                ok, msg = False, f"{type(e).__name__}: {e}"
                _log.error(f"handle_czas_tak assign exception oid={oid}: {msg}")

            # ---------- State mutate ----------
            triggers = rec.setdefault("triggers_fired", {})
            key = str(trigger_min)
            existing = triggers.get(key) if isinstance(triggers, dict) else None
            base = dict(existing) if isinstance(existing, dict) else {}
            now_iso = _now_utc().isoformat()
            base.update({
                "proposed_cid": cid,
                "proposed_name": name,
                "decision": "TAK" if ok else "TAK_FAILED",
                "decision_ts": now_iso,
                "assign_response": (str(msg)[-400:] if msg is not None else None),
            })
            triggers[key] = base
            if ok:
                rec["final_assignment_cid"] = cid
                rec["final_assignment_ts"] = now_iso

    except Exception as e:
        _log.error(f"handle_czas_tak state write failed oid={oid}: {type(e).__name__}: {e}")
        _answer_cb(state, cb, f"❌ state error: {type(e).__name__}")
        return

    if ok:
        # Audit trail per Adrian Z3 (2026-05-05): kto kliknął widoczny w grupie
        clicker = (cb.get("from") or {}).get("first_name", "?")
        _answer_cb(state, cb, f"✅ {name}")
        _edit_message_text(
            state, cb,
            f"✅ Czasówka #{oid} → {name}\n"
            f"Przypisane przez {clicker} (T-{trigger_min})."
        )
    else:
        _answer_cb(state, cb, f"❌ assign: {str(msg)[:80]}")
        _edit_message_text(
            state, cb,
            f"❌ Czasówka #{oid} → {name}\n"
            f"Panel error (T-{trigger_min}): {str(msg)[:120]}"
        )
    _log.info(
        f"handle_czas_tak oid={oid} cid={cid} trigger={trigger_min} "
        f"name={name} ok={ok}"
    )


def handle_czas_nie(state: dict, action: str, raw_oid: str, cb: dict) -> None:
    """CZAS_NIE callback — wyklucz kandydata i przejdź do następnego (T-40 lub manual).

    State mutate: triggers_fired[trigger_min].decision = "NIE";
    excluded_candidates += [cid] (per-czasówka, NIE globalne — wraca w T-40
    NIE dla tego oid). Edit message.
    Idempotent: drugi click → "ℹ już zapisane".
    """
    if not flag("CZASOWKA_PROACTIVE_ENABLED", default=False):
        _answer_cb(state, cb, "wyłączone")
        return

    parsed = _parse_raw_oid(raw_oid)
    if parsed is None:
        _answer_cb(state, cb, "❌ malformed callback")
        _log.warning(f"handle_czas_nie: malformed raw_oid={raw_oid!r}")
        return
    oid, cid, trigger_min = parsed

    try:
        from dispatch_v2.czasowka_proactive.state import locked_write_proposals_state
    except ImportError as e:
        _answer_cb(state, cb, f"❌ state module unavailable: {e}")
        _log.error(f"handle_czas_nie state import failed: {e}")
        return

    name = _name_lookup(cid)
    feedback = ""
    try:
        with locked_write_proposals_state() as proposals:
            orders = proposals.setdefault("orders", {})
            rec = orders.get(oid)
            if not isinstance(rec, dict):
                _answer_cb(state, cb, f"⚠ Brak rekordu czasówki #{oid}")
                return
            sub = _trigger_record_for(rec, trigger_min)
            if isinstance(sub, dict) and sub.get("decision") is not None:
                _answer_cb(state, cb, "ℹ już zapisane")
                return

            triggers = rec.setdefault("triggers_fired", {})
            key = str(trigger_min)
            existing = triggers.get(key) if isinstance(triggers, dict) else None
            base = dict(existing) if isinstance(existing, dict) else {}
            base.update({
                "proposed_cid": cid,
                "proposed_name": name,
                "decision": "NIE",
                "decision_ts": _now_utc().isoformat(),
            })
            triggers[key] = base

            excluded = rec.setdefault("excluded_candidates", [])
            if isinstance(excluded, list) and cid not in excluded:
                excluded.append(cid)

            feedback = f"⏭ NIE — {name} wykluczony"
    except Exception as e:
        _log.error(f"handle_czas_nie state write failed oid={oid}: {type(e).__name__}: {e}")
        _answer_cb(state, cb, f"❌ state error: {type(e).__name__}")
        return

    _answer_cb(state, cb, feedback)
    _edit_message_text(
        state, cb,
        f"⏭ Czasówka #{oid} — NIE\n"
        f"{name} wykluczony (T-{trigger_min})."
    )
    _log.info(
        f"handle_czas_nie oid={oid} cid={cid} trigger={trigger_min} name={name}"
    )


def handle_czas_czekaj(state: dict, action: str, raw_oid: str, cb: dict) -> None:
    """CZAS_CZEKAJ callback — czekaj na T-40 re-eval.

    State mutate: triggers_fired[trigger_min].decision = "CZEKAJ"; NO
    exclusion (kandydat może wrócić w T-40 — tylko 'NIE' wyklucza).
    Edit message.
    Idempotent: drugi click → "ℹ już zapisane".
    """
    if not flag("CZASOWKA_PROACTIVE_ENABLED", default=False):
        _answer_cb(state, cb, "wyłączone")
        return

    parsed = _parse_raw_oid(raw_oid)
    if parsed is None:
        _answer_cb(state, cb, "❌ malformed callback")
        _log.warning(f"handle_czas_czekaj: malformed raw_oid={raw_oid!r}")
        return
    oid, cid, trigger_min = parsed

    try:
        from dispatch_v2.czasowka_proactive.state import locked_write_proposals_state
    except ImportError as e:
        _answer_cb(state, cb, f"❌ state module unavailable: {e}")
        _log.error(f"handle_czas_czekaj state import failed: {e}")
        return

    name = _name_lookup(cid)
    try:
        with locked_write_proposals_state() as proposals:
            orders = proposals.setdefault("orders", {})
            rec = orders.get(oid)
            if not isinstance(rec, dict):
                _answer_cb(state, cb, f"⚠ Brak rekordu czasówki #{oid}")
                return
            sub = _trigger_record_for(rec, trigger_min)
            if isinstance(sub, dict) and sub.get("decision") is not None:
                _answer_cb(state, cb, "ℹ już zapisane")
                return

            triggers = rec.setdefault("triggers_fired", {})
            key = str(trigger_min)
            existing = triggers.get(key) if isinstance(triggers, dict) else None
            base = dict(existing) if isinstance(existing, dict) else {}
            base.update({
                "proposed_cid": cid,
                "proposed_name": name,
                "decision": "CZEKAJ",
                "decision_ts": _now_utc().isoformat(),
            })
            triggers[key] = base
            # NO excluded_candidates mutation — kandydat może wrócić w T-40.
    except Exception as e:
        _log.error(f"handle_czas_czekaj state write failed oid={oid}: {type(e).__name__}: {e}")
        _answer_cb(state, cb, f"❌ state error: {type(e).__name__}")
        return

    _answer_cb(state, cb, f"⏳ Czekaj — re-eval T-40")
    _edit_message_text(
        state, cb,
        f"⏳ Czasówka #{oid} — CZEKAJ\n"
        f"{name} re-eval w T-40."
    )
    _log.info(
        f"handle_czas_czekaj oid={oid} cid={cid} trigger={trigger_min} name={name}"
    )
