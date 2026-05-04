"""TASK B SHIFT NOTIFICATIONS — Telegram router + templates tests (2026-05-04).

Coverage (14 obligatory):
  Templates (5):
    1. test_template_individual_start_starts_with_green
    2. test_template_individual_start_contains_courier_and_time
    3. test_template_batch_start_lists_all_couriers
    4. test_template_end_starts_with_red_circle
    5. test_template_alert_no_show_mentions_bartek

  Callback router (6):
    6. test_callback_router_existing_assign_unchanged (regression)
    7. test_callback_router_existing_inny_unchanged (regression)
    8. test_callback_router_existing_koord_unchanged (regression)
    9. test_callback_router_shift_start_ok_writes_confirmed
   10. test_callback_router_shift_start_no_triggers_alert_to_bartek
   11. test_callback_router_simultaneous_clicks_idempotent

  /koniec command (3):
   12. test_koniec_authorized_user_extends_to_ended
   13. test_koniec_unauthorized_user_silently_ignored
   14. test_koniec_disabled_when_flag_false

Custom-runner pattern (matches tests/test_auto_koord.py — no pytest dep).
"""
import sys
sys.path.insert(0, "/root/.openclaw/workspace/scripts")

import asyncio
import json
import os
import tempfile
import threading
from pathlib import Path

from dispatch_v2 import telegram_approver
from dispatch_v2.telegram import templates
from dispatch_v2.shift_notifications import state as shift_state


passed, failed = 0, 0


def t(name, fn):
    global passed, failed
    try:
        fn()
        passed += 1
        print(f"  OK {passed+failed}. {name}")
    except AssertionError as e:
        failed += 1
        print(f"  FAIL {passed+failed}. {name}: {e}")
    except Exception as e:
        failed += 1
        print(f"  CRASH {passed+failed}. {name}: {type(e).__name__}: {e}")
        import traceback
        traceback.print_exc()


# ---------------- helpers ----------------

class _StateOverride:
    """Context manager: redirect shift_state.STATE_FILE + LEARNING_LOG to tmp.

    Usage:
        with _StateOverride() as ov:
            ...  # ov.tmpdir / ov.state_file accessible
    """
    def __init__(self):
        self.tmpdir = None
        self.state_file_orig = shift_state.STATE_FILE
        self.learning_log_orig = shift_state.LEARNING_LOG

    def __enter__(self):
        self.tmpdir = tempfile.mkdtemp(prefix="shift_test_")
        shift_state.STATE_FILE = Path(self.tmpdir) / "shift_confirmations.json"
        shift_state.LEARNING_LOG = Path(self.tmpdir) / "learning_log.jsonl"
        return self

    def __exit__(self, exc_type, exc, tb):
        shift_state.STATE_FILE = self.state_file_orig
        shift_state.LEARNING_LOG = self.learning_log_orig
        # best-effort cleanup
        try:
            import shutil
            shutil.rmtree(self.tmpdir, ignore_errors=True)
        except Exception:
            pass


class _FlagOverride:
    """Patch telegram_approver.flag(name, default) for test scope."""
    def __init__(self, **kwargs):
        self.values = kwargs
        self.orig = telegram_approver.flag

    def __enter__(self):
        def fake_flag(name, default=False):
            return self.values.get(name, default)
        telegram_approver.flag = fake_flag
        return self

    def __exit__(self, exc_type, exc, tb):
        telegram_approver.flag = self.orig


def _seed_record(today_iso, full_name, cid, bucket="start_notified",
                 scheduled="2026-05-04T14:00:00+02:00", **extra):
    """Write a single record into bucket via locked_write_confirmations."""
    with shift_state.locked_write_confirmations() as conf:
        b = conf.setdefault(bucket, {})
        key = f"{today_iso}:{full_name}"
        b[key] = {
            "cid": cid,
            "scheduled": scheduled,
            "decision": None,
            "confirmed_for_shift": None,
        }
        b[key].update(extra)


def _read_state():
    return shift_state.read_confirmations()


def _make_cb(action, oid, admin_id="123456"):
    return {
        "id": "test_cb_id",
        "data": f"{action}:{oid}",
        "from": {"id": 8765130486, "first_name": "Adrian"},
        "message": {"chat": {"id": int(admin_id)}, "message_id": 999},
    }


def _make_state(admin_id="123456", pending=None):
    return {
        "token": "fake_token",
        "admin_id": admin_id,
        "pending": pending or {},
        "pending_path": "/tmp/fake_pending.json",
        "learning_log_path": "/tmp/fake_learning.jsonl",
    }


# Stub tg_request to capture all outgoing telegram calls
class _TGCapture:
    def __init__(self):
        self.calls = []
        self.orig = telegram_approver.tg_request

    def __enter__(self):
        def fake(token, method, payload=None, timeout=35):
            self.calls.append({"method": method, "payload": payload})
            return {"ok": True, "result": {"message_id": 1}}
        telegram_approver.tg_request = fake
        return self

    def __exit__(self, exc_type, exc, tb):
        telegram_approver.tg_request = self.orig


# Stub the shift_notifications.telegram_send.tg_send_text_with_keyboard
class _TGSendCapture:
    def __init__(self):
        self.calls = []
        from dispatch_v2.shift_notifications import telegram_send as ts
        self.module = ts
        self.orig = ts.tg_send_text_with_keyboard

    def __enter__(self):
        def fake(chat_id, text, keyboard=None):
            self.calls.append({"chat_id": chat_id, "text": text, "keyboard": keyboard})
            return True
        self.module.tg_send_text_with_keyboard = fake
        return self

    def __exit__(self, exc_type, exc, tb):
        self.module.tg_send_text_with_keyboard = self.orig


# ============================================================
# 1-5: TEMPLATES
# ============================================================

def test_template_individual_start_starts_with_green():
    out = templates.format_shift_start_individual("Mateusz O.", "14:00")
    assert out.startswith("🟢"), f"expected '🟢' prefix, got {out!r}"
t("template_individual_start_starts_with_green", test_template_individual_start_starts_with_green)


def test_template_individual_start_contains_courier_and_time():
    out = templates.format_shift_start_individual("Bartek O.", "16:30")
    assert "Bartek O." in out
    assert "16:30" in out
t("template_individual_start_contains_courier_and_time",
  test_template_individual_start_contains_courier_and_time)


def test_template_batch_start_lists_all_couriers():
    couriers = ["Adrian R.", "Mykyta K.", "Pavlo S."]
    out = templates.format_shift_start_batch(couriers, "14:00")
    for name in couriers:
        assert f"• {name}" in out, f"missing bullet for {name}: {out!r}"
    assert "14:00" in out
t("template_batch_start_lists_all_couriers", test_template_batch_start_lists_all_couriers)


def test_template_end_starts_with_red_circle():
    out = templates.format_shift_end("Gabriel J.", "22:00")
    assert out.startswith("🔴"), f"expected '🔴' prefix, got {out!r}"
t("template_end_starts_with_red_circle", test_template_end_starts_with_red_circle)


def test_template_alert_no_show_mentions_bartek():
    out = templates.format_alert_courier_no_show("Mykyta K.", "14:00")
    assert "Bartku" in out, f"alert must address Bartek, got {out!r}"
t("template_alert_no_show_mentions_bartek", test_template_alert_no_show_mentions_bartek)


# ============================================================
# 6-8: REGRESSION — existing prefixes ASSIGN/INNY/KOORD untouched
# ============================================================

def test_callback_router_existing_assign_unchanged():
    """ASSIGN:469087:413:15 must still flow through old path (state['pending'] lookup → 'Unknown' if missing)."""
    with _StateOverride(), _FlagOverride(SHIFT_NOTIFY_ENABLED=False), _TGCapture() as tg:
        st = _make_state()
        cb = _make_cb("ASSIGN", "469087:413:15")
        # Run via top-level dispatcher action="ASSIGN", oid="469087:413:15"
        asyncio.run(telegram_approver.handle_callback(st, "ASSIGN", "469087:413:15", cb))
        # Must NOT short-circuit on SHIFT branch — should reach pending lookup,
        # find no entry, send "Unknown order" feedback.
        feedback_calls = [c for c in tg.calls if c["method"] == "answerCallbackQuery"]
        assert len(feedback_calls) >= 1, f"expected answerCallbackQuery, got {tg.calls}"
        assert "Unknown order" in feedback_calls[0]["payload"]["text"], \
            f"expected ASSIGN to flow to legacy 'Unknown order' path, got {feedback_calls[0]}"
t("callback_router_existing_assign_unchanged", test_callback_router_existing_assign_unchanged)


def test_callback_router_existing_inny_unchanged():
    """INNY:wrong_direction:469087 must flow through legacy INNY parsing."""
    with _StateOverride(), _FlagOverride(SHIFT_NOTIFY_ENABLED=False), _TGCapture() as tg:
        st = _make_state()
        cb = _make_cb("INNY", "wrong_direction:469087")
        asyncio.run(telegram_approver.handle_callback(st, "INNY", "wrong_direction:469087", cb))
        feedback_calls = [c for c in tg.calls if c["method"] == "answerCallbackQuery"]
        assert len(feedback_calls) >= 1
        # No pending entry → "Unknown order #469087"
        assert "Unknown order" in feedback_calls[0]["payload"]["text"], \
            f"INNY should fall through to legacy path, got {feedback_calls[0]}"
t("callback_router_existing_inny_unchanged", test_callback_router_existing_inny_unchanged)


def test_callback_router_existing_koord_unchanged():
    """KOORD:469087 must flow through legacy KOORD path."""
    with _StateOverride(), _FlagOverride(SHIFT_NOTIFY_ENABLED=False), _TGCapture() as tg:
        st = _make_state()
        cb = _make_cb("KOORD", "469087")
        asyncio.run(telegram_approver.handle_callback(st, "KOORD", "469087", cb))
        feedback_calls = [c for c in tg.calls if c["method"] == "answerCallbackQuery"]
        assert len(feedback_calls) >= 1
        assert "Unknown order" in feedback_calls[0]["payload"]["text"], \
            f"KOORD should fall through to legacy path, got {feedback_calls[0]}"
t("callback_router_existing_koord_unchanged", test_callback_router_existing_koord_unchanged)


# ============================================================
# 9-11: SHIFT callback routing
# ============================================================

def test_callback_router_shift_start_ok_writes_confirmed():
    today_iso = telegram_approver._shift_today_iso()
    with _StateOverride(), _FlagOverride(SHIFT_NOTIFY_ENABLED=True), _TGCapture() as tg, _TGSendCapture() as tgsend:
        _seed_record(today_iso, "Mateusz O.", "413", scheduled="2026-05-04T14:00:00+02:00")
        st = _make_state()
        cb = _make_cb("SHIFT_START_OK", "413")
        asyncio.run(telegram_approver.handle_callback(st, "SHIFT_START_OK", "413", cb))
        # Verify state was mutated
        result = _read_state()
        rec = shift_state.find_record_for_cid(result["start_notified"], today_iso, "413")
        assert rec is not None, f"record missing post-callback: {result}"
        assert rec["decision"] is True, f"decision should be True, got {rec}"
        assert rec["confirmed_for_shift"] is True, f"confirmed_for_shift True expected, got {rec}"
        # No no-show alert sent
        assert len(tgsend.calls) == 0, f"no_show alert must NOT fire on OK, got {tgsend.calls}"
        # answerCallbackQuery fired
        assert any(c["method"] == "answerCallbackQuery" for c in tg.calls), \
            f"answerCallbackQuery missing: {tg.calls}"
t("callback_router_shift_start_ok_writes_confirmed",
  test_callback_router_shift_start_ok_writes_confirmed)


def test_callback_router_shift_start_no_triggers_alert_to_bartek():
    today_iso = telegram_approver._shift_today_iso()
    with _StateOverride(), _FlagOverride(SHIFT_NOTIFY_ENABLED=True), _TGCapture() as tg, _TGSendCapture() as tgsend:
        _seed_record(today_iso, "Mykyta K.", "999", scheduled="2026-05-04T14:00:00+02:00")
        st = _make_state(admin_id="123456")
        cb = _make_cb("SHIFT_START_NO", "999")
        asyncio.run(telegram_approver.handle_callback(st, "SHIFT_START_NO", "999", cb))
        result = _read_state()
        rec = shift_state.find_record_for_cid(result["start_notified"], today_iso, "999")
        assert rec is not None, f"record missing: {result}"
        assert rec["decision"] is False, f"decision should be False, got {rec}"
        assert rec["confirmed_for_shift"] is False
        # Alert was sent to Bartek (admin chat)
        assert len(tgsend.calls) == 1, f"no_show alert should fire once, got {tgsend.calls}"
        alert = tgsend.calls[0]
        assert "Mykyta K." in alert["text"], f"alert missing courier name: {alert}"
        assert "Bartku" in alert["text"], f"alert missing 'Bartku' addressee: {alert}"
        assert alert["chat_id"] == "123456", f"alert should go to admin chat, got {alert}"
t("callback_router_shift_start_no_triggers_alert_to_bartek",
  test_callback_router_shift_start_no_triggers_alert_to_bartek)


def test_callback_router_simultaneous_clicks_idempotent():
    """Two threads clicking SHIFT_START_OK — first wins, second sees 'już zapisane'."""
    today_iso = telegram_approver._shift_today_iso()
    with _StateOverride(), _FlagOverride(SHIFT_NOTIFY_ENABLED=True), _TGCapture() as tg, _TGSendCapture():
        _seed_record(today_iso, "Pavlo S.", "777")
        st = _make_state()
        # Click 1
        cb1 = _make_cb("SHIFT_START_OK", "777")
        telegram_approver._handle_shift_start_callback(st, "SHIFT_START_OK", "777", cb1)
        # Click 2 — should hit idempotent branch
        cb2 = _make_cb("SHIFT_START_OK", "777")
        telegram_approver._handle_shift_start_callback(st, "SHIFT_START_OK", "777", cb2)
        # Verify state — single mutation
        result = _read_state()
        rec = shift_state.find_record_for_cid(result["start_notified"], today_iso, "777")
        assert rec["decision"] is True
        # Verify second call answered "już zapisane"
        feedback_texts = [c["payload"]["text"] for c in tg.calls
                          if c["method"] == "answerCallbackQuery"]
        assert len(feedback_texts) == 2, f"expected 2 callback answers, got {feedback_texts}"
        assert "już zapisane" in feedback_texts[1] or "ℹ" in feedback_texts[1], \
            f"second click should see 'już zapisane', got {feedback_texts}"
t("callback_router_simultaneous_clicks_idempotent",
  test_callback_router_simultaneous_clicks_idempotent)


# ============================================================
# 12-14: /koniec command
# ============================================================

def test_koniec_authorized_user_extends_to_ended():
    today_iso = telegram_approver._shift_today_iso()
    with _StateOverride(), _FlagOverride(SHIFT_NOTIFY_ENABLED=True,
                                          MANUAL_KONIEC_COMMAND_ENABLED=True):
        _seed_record(today_iso, "Adrian R.", "555",
                     bucket="end_notified", shift_extended=True,
                     extended_until=today_iso + "T23:59:00")
        st = _make_state()
        msg = {"from": {"id": 8765130486}, "chat": {"id": 123456}, "text": "/koniec 555"}
        reply = telegram_approver._handle_koniec_command(st, msg, "/koniec 555")
        assert reply is not None, "expected reply for authorized user"
        assert "555" in reply, f"expected cid in reply, got {reply}"
        assert "✅" in reply or "ustawiony" in reply, f"expected success token, got {reply}"
        result = _read_state()
        rec = shift_state.find_record_for_cid(result["end_notified"], today_iso, "555")
        assert rec is not None, f"record missing post-/koniec: {result}"
        assert rec["shift_ending_confirmed"] is True
        assert rec["shift_extended"] is False
        assert rec.get("terminated_via_koniec_at") is not None
        assert rec.get("terminated_by") == "8765130486"
t("koniec_authorized_user_extends_to_ended", test_koniec_authorized_user_extends_to_ended)


def test_koniec_unauthorized_user_silently_ignored():
    today_iso = telegram_approver._shift_today_iso()
    with _StateOverride(), _FlagOverride(SHIFT_NOTIFY_ENABLED=True,
                                          MANUAL_KONIEC_COMMAND_ENABLED=True):
        _seed_record(today_iso, "Adrian R.", "555",
                     bucket="end_notified", shift_extended=True)
        st = _make_state()
        # Random non-authorized user_id
        msg = {"from": {"id": 11111}, "chat": {"id": 123456}, "text": "/koniec 555"}
        reply = telegram_approver._handle_koniec_command(st, msg, "/koniec 555")
        assert reply is None, f"unauthorized user should get None (silent), got {reply!r}"
        # State NOT mutated
        result = _read_state()
        rec = shift_state.find_record_for_cid(result["end_notified"], today_iso, "555")
        assert rec["shift_extended"] is True, f"state should be unchanged, got {rec}"
        assert rec.get("shift_ending_confirmed") is None or rec.get("shift_ending_confirmed") is False
t("koniec_unauthorized_user_silently_ignored", test_koniec_unauthorized_user_silently_ignored)


def test_koniec_disabled_when_flag_false():
    today_iso = telegram_approver._shift_today_iso()
    with _StateOverride(), _FlagOverride(SHIFT_NOTIFY_ENABLED=True,
                                          MANUAL_KONIEC_COMMAND_ENABLED=False):
        _seed_record(today_iso, "Adrian R.", "555",
                     bucket="end_notified", shift_extended=True)
        st = _make_state()
        # Even authorized sender — flag off → silent early exit
        msg = {"from": {"id": 8765130486}, "chat": {"id": 123456}, "text": "/koniec 555"}
        reply = telegram_approver._handle_koniec_command(st, msg, "/koniec 555")
        assert reply is None, f"disabled flag → None (silent), got {reply!r}"
        result = _read_state()
        rec = shift_state.find_record_for_cid(result["end_notified"], today_iso, "555")
        assert rec["shift_extended"] is True, f"state should be unchanged, got {rec}"
t("koniec_disabled_when_flag_false", test_koniec_disabled_when_flag_false)


# ============================================================
# Final report
# ============================================================
print("=" * 70)
print(f"PASSED: {passed}/{passed+failed}")
print(f"FAILED: {failed}/{passed+failed}")
print("=" * 70)
sys.exit(0 if failed == 0 else 1)
