"""Issue #1 (2026-05-05): SHIFT notification target chat routing tests.

Bug: shift_notifications worker wysyłał T-60 START/REMINDER + T-60 END do
Adrian DM (ADRIAN_CHAT_ID_FALLBACK=8765130486) zamiast do grupy ziomka
(-5149910559). Z3 konsystencja z czasówkami (TASK A → grupa).

Fix: flag-based hot-reload SHIFT_NOTIFY_TARGET_CHAT_ID w flags.json,
single source of truth w `_resolve_shift_notify_target_chat()`. Worker
NIE musi nic zmieniać — `_resolve_chat_id(None)` chains do nowego helpera.

Coverage (6 tests):
  Resolver:
    1. resolve_target_returns_flag_when_set
    2. resolve_target_falls_back_when_flag_missing
    3. resolve_target_falls_back_when_flag_zero
    4. resolve_target_falls_back_when_flag_string

  Callback router regression (group + DM auth paths preserved):
    5. callback_router_existing_shift_start_works_from_group
    6. callback_router_existing_shift_start_works_from_dm

Custom-runner pattern (mirror tests/test_shift_telegram_router.py).
"""
import sys
from pathlib import Path
sys.path.insert(0, "/root/.openclaw/workspace/scripts")
sys.path.insert(0, str(Path(__file__).resolve().parent))

import asyncio

from dispatch_v2 import telegram_approver
from dispatch_v2.shift_notifications import state as shift_state
from dispatch_v2.shift_notifications import telegram_send as ts_mod
from _shift_test_helpers import isolated_shift_state


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


# ---------------- helpers (mirror test_shift_telegram_router.py) ----------------


class _LoadFlagsOverride:
    """Patch dispatch_v2.shift_notifications.telegram_send's load_flags()
    import. Resolver does `from dispatch_v2.common import load_flags`
    inside the function body, so we patch dispatch_v2.common.load_flags."""

    def __init__(self, vals):
        self.vals = vals
        from dispatch_v2 import common as _c
        self.common_mod = _c
        self.orig = _c.load_flags

    def __enter__(self):
        self.common_mod.load_flags = lambda: dict(self.vals) if self.vals is not None else None
        return self

    def __exit__(self, exc_type, exc, tb):
        self.common_mod.load_flags = self.orig


class _LoadFlagsRaises:
    """Force load_flags() to raise → resolver should hit fallback path."""

    def __init__(self):
        from dispatch_v2 import common as _c
        self.common_mod = _c
        self.orig = _c.load_flags

    def __enter__(self):
        def boom():
            raise RuntimeError("simulated flags read failure")
        self.common_mod.load_flags = boom
        return self

    def __exit__(self, exc_type, exc, tb):
        self.common_mod.load_flags = self.orig


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


class _TGSendCapture:
    def __init__(self, return_value=True):
        self.calls = []
        self.return_value = return_value
        self.orig = ts_mod.tg_send_text_with_keyboard

    def __enter__(self):
        capture = self

        def fake(text, inline_keyboard, chat_id=None):
            capture.calls.append({"chat_id": chat_id, "text": text,
                                  "inline_keyboard": inline_keyboard})
            return capture.return_value

        ts_mod.tg_send_text_with_keyboard = fake
        return self

    def __exit__(self, exc_type, exc, tb):
        ts_mod.tg_send_text_with_keyboard = self.orig


def _seed_record(today_iso, full_name, cid, bucket="start_notified",
                 scheduled="2026-05-05T09:00:00+02:00", **extra):
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


def _make_state(admin_id="-5149910559", pending=None):
    return {
        "token": "fake_token",
        "admin_id": admin_id,
        "pending": pending or {},
        "pending_path": "/tmp/fake_pending.json",
        "learning_log_path": "/tmp/fake_learning.jsonl",
    }


# ============================================================
# 1-4: Resolver tests
# ============================================================

def test_resolve_target_returns_flag_when_set():
    """Flag SHIFT_NOTIFY_TARGET_CHAT_ID=-5149910559 → resolver returns -5149910559."""
    with _LoadFlagsOverride({"SHIFT_NOTIFY_TARGET_CHAT_ID": -5149910559}):
        result = ts_mod._resolve_shift_notify_target_chat()
        assert result == -5149910559, \
            f"expected -5149910559 (grupa ziomka), got {result}"
    # Also check chain via _resolve_chat_id(None)
    with _LoadFlagsOverride({"SHIFT_NOTIFY_TARGET_CHAT_ID": -5149910559}):
        # Clear env to ensure no env override interferes
        import os
        old_env_1 = os.environ.pop("TELEGRAM_CHAT_ID", None)
        old_env_2 = os.environ.pop("AUTO_KOORD_TG_CHAT_ID", None)
        try:
            chained = ts_mod._resolve_chat_id(None)
            assert chained == -5149910559, \
                f"_resolve_chat_id(None) should chain to flag value, got {chained}"
        finally:
            if old_env_1 is not None:
                os.environ["TELEGRAM_CHAT_ID"] = old_env_1
            if old_env_2 is not None:
                os.environ["AUTO_KOORD_TG_CHAT_ID"] = old_env_2
t("resolve_target_returns_flag_when_set", test_resolve_target_returns_flag_when_set)


def test_resolve_target_falls_back_when_flag_missing():
    """No flag in flags.json → ADRIAN_CHAT_ID_FALLBACK 8765130486."""
    with _LoadFlagsOverride({}):  # empty dict — no SHIFT_NOTIFY_TARGET_CHAT_ID key
        result = ts_mod._resolve_shift_notify_target_chat()
        assert result == ts_mod.ADRIAN_CHAT_ID_FALLBACK == 8765130486, \
            f"expected ADRIAN_CHAT_ID_FALLBACK 8765130486, got {result}"
t("resolve_target_falls_back_when_flag_missing",
  test_resolve_target_falls_back_when_flag_missing)


def test_resolve_target_falls_back_when_flag_zero():
    """Flag=0 (invalid sentinel) → fallback to ADRIAN_CHAT_ID_FALLBACK."""
    with _LoadFlagsOverride({"SHIFT_NOTIFY_TARGET_CHAT_ID": 0}):
        result = ts_mod._resolve_shift_notify_target_chat()
        assert result == ts_mod.ADRIAN_CHAT_ID_FALLBACK == 8765130486, \
            f"flag=0 should be invalid → fallback, got {result}"
t("resolve_target_falls_back_when_flag_zero",
  test_resolve_target_falls_back_when_flag_zero)


def test_resolve_target_falls_back_when_flag_string():
    """Flag='abc' (wrong type, not int) → fallback."""
    with _LoadFlagsOverride({"SHIFT_NOTIFY_TARGET_CHAT_ID": "abc"}):
        result = ts_mod._resolve_shift_notify_target_chat()
        assert result == ts_mod.ADRIAN_CHAT_ID_FALLBACK == 8765130486, \
            f"non-int flag should fallback, got {result}"
    # Bonus: load_flags() raising → fallback (defense-in-depth)
    with _LoadFlagsRaises():
        result = ts_mod._resolve_shift_notify_target_chat()
        assert result == ts_mod.ADRIAN_CHAT_ID_FALLBACK == 8765130486, \
            f"load_flags exception should fallback, got {result}"
t("resolve_target_falls_back_when_flag_string",
  test_resolve_target_falls_back_when_flag_string)


# ============================================================
# 5-6: Callback router regression (group + DM auth preserved)
# ============================================================

def test_callback_router_existing_shift_start_works_from_group():
    """Regression: group callback z state['admin_id']='-5149910559' → handler
    executes (chat_id == admin_id auth path). Confirms że Issue #1 routing fix
    do grupy NIE łamie callback handler — group callbacks były zawsze authorized
    via state['admin_id'] match (NIE KONIEC_AUTHORIZED_USER_IDS DM whitelist)."""
    today_iso = telegram_approver._shift_today_iso()
    with isolated_shift_state(), _FlagOverride(SHIFT_NOTIFY_ENABLED=True), \
         _TGCapture() as tg, _TGSendCapture():
        _seed_record(today_iso, "Bartek O.", "123",
                     scheduled="2026-05-05T09:00:00+02:00")
        # admin_id = grupa ziomka (post Issue #1 worker target = grupa)
        st = _make_state(admin_id="-5149910559")
        # callback przychodzi z grupy (chat.id == admin_id, klika dowolny z member np. Adrian DM-id)
        cb = {
            "id": "test_cb_id_group",
            "data": "SHIFT_START_OK:123",
            "from": {"id": 8765130486, "first_name": "Adrian"},
            "message": {"chat": {"id": -5149910559}, "message_id": 999},
        }
        asyncio.run(telegram_approver.handle_callback(st, "SHIFT_START_OK", "123", cb))
        # Handler MUSI execute — group chat == admin_id auth path
        result = _read_state()
        rec = shift_state.find_record_for_cid(result["start_notified"], today_iso, "123")
        assert rec is not None, f"record missing — handler nie executed: {result}"
        assert rec["decision"] is True, \
            f"decision powinno być True post-handler (group auth path), got {rec}"
        assert rec["confirmed_for_shift"] is True, \
            f"confirmed_for_shift True expected, got {rec}"
        # NIE może być '⛔ unauthorized' feedback
        feedback_calls = [c for c in tg.calls if c["method"] == "answerCallbackQuery"]
        assert len(feedback_calls) >= 1, f"expected answerCallbackQuery, got {tg.calls}"
        for c in feedback_calls:
            text = (c.get("payload") or {}).get("text", "")
            assert "unauthorized" not in text.lower(), \
                f"group callback NIE powinno być unauthorized: {c}"
t("callback_router_existing_shift_start_works_from_group",
  test_callback_router_existing_shift_start_works_from_group)


def test_callback_router_existing_shift_start_works_from_dm():
    """Regression: DM callback z user_id ∈ KONIEC_AUTHORIZED_USER_IDS
    (Adrian 8765130486 OR Bartek 8753482870) → both authorized via
    SHIFT_TASKB_PREFIXES whitelist (preserves Phase 2 fix 71affb2).

    Issue #1 changes only worker target, NIE callback auth — DM remains
    backward-compat dla testowania manual + jakikolwiek legacy notification."""
    today_iso = telegram_approver._shift_today_iso()
    # Sanity: oba user_id muszą być w whitelist
    assert 8765130486 in telegram_approver.KONIEC_AUTHORIZED_USER_IDS, \
        "Adrian 8765130486 should be authorized (TASK B Phase 2)"
    assert 8753482870 in telegram_approver.KONIEC_AUTHORIZED_USER_IDS, \
        "Bartek 8753482870 should be authorized (TASK B Phase 2)"

    # ---- Test pass 1: Adrian DM ----
    with isolated_shift_state(), _FlagOverride(SHIFT_NOTIFY_ENABLED=True), \
         _TGCapture() as tg, _TGSendCapture():
        _seed_record(today_iso, "Bartek O.", "111",
                     scheduled="2026-05-05T09:00:00+02:00")
        st = _make_state(admin_id="-5149910559")
        cb_adrian_dm = {
            "id": "test_cb_id_adrian_dm",
            "data": "SHIFT_START_OK:111",
            "from": {"id": 8765130486, "first_name": "Adrian"},
            "message": {"chat": {"id": 8765130486}, "message_id": 999},
        }
        asyncio.run(telegram_approver.handle_callback(
            st, "SHIFT_START_OK", "111", cb_adrian_dm))
        result = _read_state()
        rec = shift_state.find_record_for_cid(result["start_notified"], today_iso, "111")
        assert rec is not None, f"Adrian DM: record missing: {result}"
        assert rec["decision"] is True, f"Adrian DM: decision True expected, got {rec}"
        # No '⛔ unauthorized'
        for c in tg.calls:
            if c["method"] == "answerCallbackQuery":
                text = (c.get("payload") or {}).get("text", "")
                assert "unauthorized" not in text.lower(), \
                    f"Adrian DM callback NIE powinno być unauthorized: {c}"

    # ---- Test pass 2: Bartek DM ----
    with isolated_shift_state(), _FlagOverride(SHIFT_NOTIFY_ENABLED=True), \
         _TGCapture() as tg, _TGSendCapture():
        _seed_record(today_iso, "Bartek O.", "222",
                     scheduled="2026-05-05T09:00:00+02:00")
        st = _make_state(admin_id="-5149910559")
        cb_bartek_dm = {
            "id": "test_cb_id_bartek_dm",
            "data": "SHIFT_START_OK:222",
            "from": {"id": 8753482870, "first_name": "Bartek"},
            "message": {"chat": {"id": 8753482870}, "message_id": 1000},
        }
        asyncio.run(telegram_approver.handle_callback(
            st, "SHIFT_START_OK", "222", cb_bartek_dm))
        result = _read_state()
        rec = shift_state.find_record_for_cid(result["start_notified"], today_iso, "222")
        assert rec is not None, f"Bartek DM: record missing: {result}"
        assert rec["decision"] is True, f"Bartek DM: decision True expected, got {rec}"
        for c in tg.calls:
            if c["method"] == "answerCallbackQuery":
                text = (c.get("payload") or {}).get("text", "")
                assert "unauthorized" not in text.lower(), \
                    f"Bartek DM callback NIE powinno być unauthorized: {c}"
t("callback_router_existing_shift_start_works_from_dm",
  test_callback_router_existing_shift_start_works_from_dm)


# ============================================================
# Final report
# ============================================================
print("=" * 70)
print(f"PASSED: {passed}/{passed+failed}")
print(f"FAILED: {failed}/{passed+failed}")
print("=" * 70)
sys.exit(0 if failed == 0 else 1)
