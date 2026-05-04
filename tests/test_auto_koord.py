"""TASK 4 (2026-05-04) — Auto-KOORD on NEW_ORDER tests.

Coverage (10 obligatory):
  1. test_auto_koord_basic_flow — happy path success
  2. test_skip_elastyk_below_60min — prep <60 → not_czasowka
  3. test_skip_already_assigned — id_kurier set → skip
  4. test_skip_when_flag_disabled — flag false → skip
  5. test_retry_on_panel_fail (3x backoff)
  6. test_race_condition_pre_check — re-fetch shows assigned
  7. test_cancelled_order_skip — status_id=9 → skip
  8. test_log_event_structure — record schema
  9. test_telegram_info_message_format
 10. test_sequential_batch (multi-order safe)
"""
import sys
sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import auto_koord


passed, failed = 0, 0
def t(name, fn):
    global passed, failed
    try:
        fn()
        passed += 1; print(f"  OK {passed+failed}. {name}")
    except AssertionError as e:
        failed += 1; print(f"  FAIL {passed+failed}. {name}: {e}")
    except Exception as e:
        failed += 1; print(f"  CRASH {passed+failed}. {name}: {type(e).__name__}: {e}")
        import traceback; traceback.print_exc()


# ---------- 1. Happy path ----------

class FakeProc:
    def __init__(self, returncode=0, stdout="ok", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


def test_auto_koord_basic_flow():
    sub_calls = []
    def fake_sub(cmd, **kw):
        sub_calls.append(cmd)
        return FakeProc(returncode=0, stdout="[assign] ok")
    fake_fetch = lambda zid: {"id_kurier": None, "id_status_zamowienia": 2}
    fake_sleep = lambda s: None
    result = auto_koord.perform_auto_koord(
        order_id="O1", fetch_details_fn=fake_fetch,
        subprocess_fn=fake_sub, sleep_fn=fake_sleep,
    )
    assert result["success"] is True
    assert result["attempts"] == 1
    assert result["skipped"] is False
    assert len(sub_calls) == 1
    assert "--koordynator" in sub_calls[0]
    assert "O1" in sub_calls[0]
t("auto_koord_basic_flow", test_auto_koord_basic_flow)


# ---------- 2. Elastyk skip ----------

def test_skip_elastyk_below_60min():
    raw = {"id_kurier": None, "id_status_zamowienia": 2, "czas_odbioru": 30}
    decision, reason = auto_koord.needs_auto_koord(raw, flag_enabled=True)
    assert decision is False
    assert "not_czasowka" in reason
t("skip_elastyk_below_60min", test_skip_elastyk_below_60min)


def test_czasowka_60_inclusive():
    raw = {"id_kurier": None, "id_status_zamowienia": 2, "czas_odbioru": 60}
    decision, reason = auto_koord.needs_auto_koord(raw, flag_enabled=True)
    assert decision is True, f"60 min powinien być czasówka inclusive, got reason={reason}"
t("czas_odbioru=60 inclusive", test_czasowka_60_inclusive)


# ---------- 3. Already assigned ----------

def test_skip_already_assigned():
    raw = {"id_kurier": 393, "id_status_zamowienia": 2, "czas_odbioru": 90}
    decision, reason = auto_koord.needs_auto_koord(raw, flag_enabled=True)
    assert decision is False
    assert "already_assigned" in reason
t("skip_already_assigned", test_skip_already_assigned)


# ---------- 4. Flag disabled ----------

def test_skip_when_flag_disabled():
    raw = {"id_kurier": None, "id_status_zamowienia": 2, "czas_odbioru": 90}
    decision, reason = auto_koord.needs_auto_koord(raw, flag_enabled=False)
    assert decision is False
    assert reason == "flag_disabled"
t("skip_when_flag_disabled", test_skip_when_flag_disabled)


# ---------- 5. Retry on fail ----------

def test_retry_on_panel_fail():
    attempts_seen = []
    def fake_sub(cmd, **kw):
        attempts_seen.append(len(attempts_seen) + 1)
        return FakeProc(returncode=1, stdout="", stderr="panel_419")
    sleeps = []
    def fake_sleep(s): sleeps.append(s)
    fake_fetch = lambda zid: {"id_kurier": None, "id_status_zamowienia": 2}
    result = auto_koord.perform_auto_koord(
        order_id="O2", fetch_details_fn=fake_fetch,
        subprocess_fn=fake_sub, sleep_fn=fake_sleep,
    )
    assert result["success"] is False
    assert result["attempts"] == 3
    assert "all_retries_exhausted" in result["reason"]
    # Sleep dwie razy (5s + 15s) — NIE po ostatniej próbie
    assert sleeps == [5, 15], f"expected backoffs [5, 15] (NIE po ostatniej), got {sleeps}"
t("retry_on_panel_fail (3x backoff exponential)", test_retry_on_panel_fail)


def test_retry_succeeds_on_2nd_attempt():
    call_count = [0]
    def fake_sub(cmd, **kw):
        call_count[0] += 1
        if call_count[0] == 1:
            return FakeProc(returncode=1, stderr="transient_fail")
        return FakeProc(returncode=0, stdout="ok")
    fake_fetch = lambda zid: {"id_kurier": None, "id_status_zamowienia": 2}
    result = auto_koord.perform_auto_koord(
        order_id="O3", fetch_details_fn=fake_fetch,
        subprocess_fn=fake_sub, sleep_fn=lambda s: None,
    )
    assert result["success"] is True
    assert result["attempts"] == 2
t("retry_succeeds_2nd_attempt", test_retry_succeeds_on_2nd_attempt)


# ---------- 6. Race condition pre-check ----------

def test_race_condition_pre_check():
    # Pre-fetch shows order ALREADY assigned (between NEW_ORDER detection and our action)
    fake_fetch = lambda zid: {"id_kurier": 393, "id_status_zamowienia": 2}
    sub_calls = []
    def fake_sub(cmd, **kw): sub_calls.append(cmd); return FakeProc(0)
    result = auto_koord.perform_auto_koord(
        order_id="RACE1", fetch_details_fn=fake_fetch,
        subprocess_fn=fake_sub, sleep_fn=lambda s: None,
    )
    assert result["skipped"] is True
    assert "race_avoided_assigned_to_393" in result["reason"]
    assert len(sub_calls) == 0  # NO subprocess executed
t("race_condition_pre_check", test_race_condition_pre_check)


# ---------- 7. Cancelled skip ----------

def test_cancelled_order_skip():
    fake_fetch = lambda zid: {"id_kurier": None, "id_status_zamowienia": 9}
    sub_calls = []
    def fake_sub(cmd, **kw): sub_calls.append(cmd); return FakeProc(0)
    result = auto_koord.perform_auto_koord(
        order_id="CXL1", fetch_details_fn=fake_fetch,
        subprocess_fn=fake_sub, sleep_fn=lambda s: None,
    )
    assert result["skipped"] is True
    assert result["reason"] == "race_avoided_cancelled"
    assert len(sub_calls) == 0
t("cancelled_order_skip (race re-check)", test_cancelled_order_skip)


def test_cancelled_decision_skip():
    """needs_auto_koord directly skips status_id=9."""
    raw = {"id_kurier": None, "id_status_zamowienia": 9, "czas_odbioru": 90}
    decision, reason = auto_koord.needs_auto_koord(raw, flag_enabled=True)
    assert decision is False
    assert "already_cancelled" in reason
t("needs_auto_koord skips cancelled", test_cancelled_decision_skip)


# ---------- 8. Log event structure ----------

def test_log_event_structure():
    captured = []
    def log_fn(rec): captured.append(rec)
    order_state = {"prep_minutes": 90}
    result = {"success": True, "attempts": 1, "reason": "ok",
              "panel_response": "ok output", "skipped": False}
    auto_koord.emit_event_log("O8", order_state, result, log_fn=log_fn)
    assert len(captured) == 1
    rec = captured[0]
    for k in ("ts", "event", "order_id", "czas_odbioru_min", "panel_response",
              "attempts", "reason", "skipped"):
        assert k in rec, f"missing field: {k}"
    assert rec["event"] == "AUTO_KOORD_ASSIGNED"
    assert rec["order_id"] == "O8"

    # Failure event
    captured.clear()
    result = {"success": False, "attempts": 3, "reason": "all_retries_exhausted",
              "panel_response": "exit=1", "skipped": False}
    auto_koord.emit_event_log("O8b", order_state, result, log_fn=log_fn)
    assert captured[0]["event"] == "AUTO_KOORD_FAILED"
t("log_event_structure", test_log_event_structure)


# ---------- 9. Telegram info message ----------

def test_telegram_info_message_format():
    order_state = {
        "pickup_at_warsaw": "2026-05-04T10:04:18+02:00",
        "restaurant": "Maison du cafe",
        "delivery_address": "Brukowa 2",
    }
    success = {"success": True}
    msg = auto_koord.make_telegram_info_message(order_state, success)
    assert "10:04" in msg
    assert "Maison du cafe" in msg
    assert "Brukowa 2" in msg
    assert "Koordynator" in msg
    # Failure variant
    fail = {"success": False, "reason": "all_retries_exhausted", "attempts": 3}
    msg_fail = auto_koord.make_telegram_info_message(order_state, fail)
    assert "FAIL" in msg_fail
    assert "all_retries_exhausted" in msg_fail
t("telegram_info_message_format (success + fail variants)", test_telegram_info_message_format)


# ---------- 10. Sequential batch ----------

def test_sequential_batch_safe():
    """Multiple czasówki w 1 batch — function called sequentially, no shared state."""
    order_states = [
        {"id_kurier": None, "id_status_zamowienia": 2, "czas_odbioru": 90},
        {"id_kurier": None, "id_status_zamowienia": 2, "czas_odbioru": 65},
        {"id_kurier": 393, "id_status_zamowienia": 2, "czas_odbioru": 70},  # already assigned, skip
    ]
    decisions = [auto_koord.needs_auto_koord(o, flag_enabled=True) for o in order_states]
    assert decisions[0][0] is True
    assert decisions[1][0] is True
    assert decisions[2][0] is False
    assert "already_assigned" in decisions[2][1]
t("sequential_batch_decision_correctness", test_sequential_batch_safe)


print("=" * 70)
print(f"PASSED: {passed}/{passed+failed}")
print(f"FAILED: {failed}/{passed+failed}")
print("=" * 70)
sys.exit(0 if failed == 0 else 1)
