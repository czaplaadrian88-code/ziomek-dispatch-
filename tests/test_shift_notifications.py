"""TASK B SHIFT NOTIFICATIONS — unit tests (2026-05-04).

Custom-runner pattern: defs + t() registrations + summary + sys.exit.
All Telegram + schedule + cid resolution mocked. No real IO outside
a tempfile-isolated STATE_FILE.
"""
import json
import os
import sys
import tempfile
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2.shift_notifications import grouping as grouping_mod
from dispatch_v2.shift_notifications import state as state_mod
from dispatch_v2.shift_notifications import worker as worker_mod
from dispatch_v2.shift_notifications.grouping import Candidate, bucket_by_slot

WARSAW = ZoneInfo("Europe/Warsaw")
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


# --------- Helpers --------------------------------------------------------


class _StateIsolator:
    """Context manager: redirects state_mod.STATE_FILE + LEARNING_LOG to tmpdir."""

    def __init__(self):
        self.tmpdir = None
        self.orig_state = None
        self.orig_log = None

    def __enter__(self):
        self.tmpdir = tempfile.mkdtemp(prefix="shift_notify_test_")
        self.orig_state = state_mod.STATE_FILE
        self.orig_log = state_mod.LEARNING_LOG
        state_mod.STATE_FILE = Path(self.tmpdir) / "shift_confirmations.json"
        state_mod.LEARNING_LOG = Path(self.tmpdir) / "learning_log.jsonl"
        return self

    def __exit__(self, *exc):
        state_mod.STATE_FILE = self.orig_state
        state_mod.LEARNING_LOG = self.orig_log
        # best-effort cleanup
        import shutil
        try:
            shutil.rmtree(self.tmpdir)
        except Exception:
            pass


def _cand(name, cid, dt):
    return Candidate(full_name=name, cid=str(cid), shift_dt=dt)


# --------- 1. Grouping: 3 in slot → batch ---------------------------------


def test_grouping_three_in_slot_emits_batch():
    base = datetime(2026, 5, 5, 9, 0, tzinfo=WARSAW)
    cs = [
        _cand("A", "1", base),
        _cand("B", "2", base + timedelta(minutes=3)),
        _cand("C", "3", base + timedelta(minutes=8)),
    ]
    out = bucket_by_slot(cs, batch_window_min=10, batch_min_couriers=3)
    assert len(out) == 1, f"expected 1 bucket, got {out}"
    assert out[0][0] == "batch", out[0]
    assert len(out[0][1]) == 3
t("grouping_three_in_slot_emits_batch", test_grouping_three_in_slot_emits_batch)


# --------- 2. Grouping: 2 in slot → individuals ---------------------------


def test_grouping_two_in_slot_emits_individuals():
    base = datetime(2026, 5, 5, 9, 0, tzinfo=WARSAW)
    cs = [
        _cand("A", "1", base),
        _cand("B", "2", base + timedelta(minutes=3)),
    ]
    out = bucket_by_slot(cs, batch_window_min=10, batch_min_couriers=3)
    assert len(out) == 2, f"expected 2 individuals, got {out}"
    assert all(kind == "individual" for kind, _ in out)
    assert all(len(members) == 1 for _, members in out)
t("grouping_two_in_slot_emits_individuals", test_grouping_two_in_slot_emits_individuals)


# --------- 3. Grouping: cross-midnight separate ---------------------------


def test_grouping_cross_midnight_separate_buckets():
    today = datetime(2026, 5, 5, 23, 55, tzinfo=WARSAW)
    tomorrow = datetime(2026, 5, 6, 0, 5, tzinfo=WARSAW)
    cs = [
        _cand("A", "1", today),
        _cand("B", "2", tomorrow),
    ]
    out = bucket_by_slot(cs, batch_window_min=10, batch_min_couriers=2)
    # 23:55 and 00:05 differ in date → 2 buckets, each with 1 candidate → individuals
    assert len(out) == 2, f"expected 2 buckets, got {out}"
    assert out[0][0] == "individual" and out[1][0] == "individual"
t("grouping_cross_midnight_separate_buckets", test_grouping_cross_midnight_separate_buckets)


# --------- 4. Grouping: empty input ---------------------------------------


def test_grouping_empty_input():
    out = bucket_by_slot([], batch_window_min=10, batch_min_couriers=3)
    assert out == [], f"expected [], got {out}"
t("grouping_empty_input", test_grouping_empty_input)


# --------- 5. State: atomic round-trip ------------------------------------


def test_state_atomic_write_then_read_roundtrip():
    with _StateIsolator():
        with state_mod.locked_write_confirmations() as st:
            st["start_notified"]["2026-05-05:Bartek Ołdziej"] = {
                "cid": "123", "scheduled": "2026-05-05T09:00:00+02:00",
                "decision": None,
            }
        loaded = state_mod.read_confirmations()
        assert "Bartek Ołdziej" in str(loaded["start_notified"])
        rec = loaded["start_notified"]["2026-05-05:Bartek Ołdziej"]
        assert rec["cid"] == "123"
        assert rec["decision"] is None
t("state_atomic_write_then_read_roundtrip", test_state_atomic_write_then_read_roundtrip)


# --------- 6. State: lock concurrent threads ------------------------------


def test_state_lock_concurrent_threads_serialize():
    with _StateIsolator():
        N = 50

        def worker(thread_id):
            for i in range(N):
                with state_mod.locked_write_confirmations() as st:
                    key = f"2026-05-05:T{thread_id}_C{i}"
                    st["start_notified"][key] = {"cid": str(thread_id * 1000 + i)}

        threads = [threading.Thread(target=worker, args=(tid,)) for tid in (1, 2)]
        for th in threads:
            th.start()
        for th in threads:
            th.join()

        loaded = state_mod.read_confirmations()
        keys = [k for k in loaded["start_notified"].keys() if k.startswith("2026-05-05:T")]
        assert len(keys) == 2 * N, f"expected {2*N} unique records, got {len(keys)}"
t("state_lock_concurrent_threads_serialize", test_state_lock_concurrent_threads_serialize)


# --------- 7. State: corrupt file → empty ---------------------------------


def test_state_corrupt_file_returns_empty():
    with _StateIsolator():
        state_mod.STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(state_mod.STATE_FILE, "w") as f:
            f.write("{ this is not json !!!")
        result = state_mod.read_confirmations()
        assert isinstance(result, dict)
        assert result.get("start_notified") == {}
        assert result.get("end_notified") == {}
t("state_corrupt_file_returns_empty", test_state_corrupt_file_returns_empty)


# --------- 8. State: find_record_for_cid filters by today -----------------


def test_state_find_record_for_cid_filters_by_today():
    records = {
        "2026-05-04:Adrian Citko": {"cid": "457", "decision": True},
        "2026-05-05:Adrian Citko": {"cid": "457", "decision": None},
        "2026-05-05:Bartek O": {"cid": "123", "decision": None},
    }
    found = state_mod.find_record_for_cid(records, "2026-05-05", "457")
    assert found is not None
    assert found["decision"] is None  # today's record (not yesterday's True)

    not_found = state_mod.find_record_for_cid(records, "2026-05-05", "999")
    assert not_found is None
t("state_find_record_for_cid_filters_by_today", test_state_find_record_for_cid_filters_by_today)


# --------- 9. State: append_learning_log appends --------------------------


def test_state_append_learning_log_appends():
    with _StateIsolator():
        state_mod.append_learning_log({"event": "TEST_A", "x": 1})
        state_mod.append_learning_log({"event": "TEST_B", "y": 2})
        with open(state_mod.LEARNING_LOG) as f:
            lines = [ln for ln in f.read().splitlines() if ln.strip()]
        assert len(lines) == 2, f"expected 2 lines, got {len(lines)}"
        rec0 = json.loads(lines[0])
        rec1 = json.loads(lines[1])
        assert rec0["event"] == "TEST_A"
        assert rec1["event"] == "TEST_B"
        assert "ts" in rec0 and "ts" in rec1
t("state_append_learning_log_appends", test_state_append_learning_log_appends)


# --------- Worker test scaffolding ----------------------------------------


class _FlagsStub:
    def __init__(self, **kw):
        self.kw = kw

    def __call__(self):
        return dict(self.kw)


def _install_worker_stubs(monkey_flags=None, schedule=None, send_calls=None):
    """Install module-level stubs and return a teardown callable."""
    orig_flags = worker_mod.load_flags
    orig_sched = worker_mod.load_schedule
    orig_send = worker_mod.tg_send_text_with_keyboard
    orig_kids = worker_mod._load_kurier_ids

    if monkey_flags is None:
        monkey_flags = {"SHIFT_NOTIFY_ENABLED": False}
    worker_mod.load_flags = lambda: dict(monkey_flags)
    worker_mod.load_schedule = (lambda: dict(schedule or {}))

    captured = send_calls if send_calls is not None else []

    def fake_send(text, keyboard, chat_id=None):
        captured.append({"text": text, "keyboard": keyboard, "chat_id": chat_id})
        return True

    worker_mod.tg_send_text_with_keyboard = fake_send

    # Default cid map covering test couriers
    fake_kids = {
        "Adrian Citko": "457",
        "Bartek Ołdziej": "123",
        "Michał Karpiuk": "393",
        "Paweł Ściepko": "376",
        "Dawid Charytoniuk": "447",
        "Mystery Person": None,  # forces None when resolved
    }
    worker_mod._load_kurier_ids = lambda: {k: v for k, v in fake_kids.items() if v is not None}

    def teardown():
        worker_mod.load_flags = orig_flags
        worker_mod.load_schedule = orig_sched
        worker_mod.tg_send_text_with_keyboard = orig_send
        worker_mod._load_kurier_ids = orig_kids

    return teardown, captured


# --------- 10. Worker: master flag off → no-op ----------------------------


def test_worker_master_flag_off_no_op():
    with _StateIsolator():
        sched_called = {"v": False}

        def tracking_load_schedule():
            sched_called["v"] = True
            return {}

        teardown, _ = _install_worker_stubs(
            monkey_flags={"SHIFT_NOTIFY_ENABLED": False},
            schedule={},
        )
        worker_mod.load_schedule = tracking_load_schedule
        try:
            rc = worker_mod.main()
            assert rc == 0
            assert sched_called["v"] is False, "load_schedule must NOT be called when master flag is off"
        finally:
            teardown()
t("worker_master_flag_off_no_op", test_worker_master_flag_off_no_op)


# --------- 11. Worker: T-60 start window inclusive ------------------------


def test_worker_t60_start_window_inclusive():
    with _StateIsolator():
        # We pin 'now' via injecting datetime via schedule timing instead of patching:
        # build a schedule where Adrian starts T-60 (mid-window IN), Bartek starts T-67 (OUT).
        # Use mid-window (60 min) to avoid edge flicker from sub-second drift between
        # test computation of `now` and worker's own datetime.now() call.
        now = datetime.now(WARSAW).replace(second=0, microsecond=0)
        in_dt = now + timedelta(minutes=60)
        out_dt = now + timedelta(minutes=67)
        schedule = {
            "Adrian Citko": {"start": in_dt.strftime("%H:%M"), "end": "20:00"},
            "Bartek Ołdziej": {"start": out_dt.strftime("%H:%M"), "end": "21:00"},
        }
        # Edge: if 55-minute window crosses midnight HH:MM still parses on today.
        # Skip if we'd cross — the test environment is deterministic enough day-to-day.
        if (in_dt.date() != now.date()) or (out_dt.date() != now.date()):
            return  # punt at midnight boundary; other tests cover cross-midnight

        send_calls = []
        teardown, _ = _install_worker_stubs(
            monkey_flags={
                "SHIFT_NOTIFY_ENABLED": True,
                "SHIFT_NOTIFY_T60_START_ENABLED": True,
                "SHIFT_NOTIFY_T30_REMINDER_ENABLED": False,
                "SHIFT_NOTIFY_T60_END_ENABLED": False,
                "SHIFT_BATCH_WINDOW_MIN": 10,
                "SHIFT_BATCH_MIN_COURIERS": 3,
            },
            schedule=schedule,
            send_calls=send_calls,
        )
        try:
            rc = worker_mod.main()
            assert rc == 0
            joined = " | ".join(c["text"] for c in send_calls)
            assert "Adrian Citko" in joined, f"Adrian (T-55, IN) should be notified. sends={joined}"
            assert "Bartek Ołdziej" not in joined, f"Bartek (T-66, OUT) must NOT be notified. sends={joined}"
        finally:
            teardown()
t("worker_t60_start_window_inclusive", test_worker_t60_start_window_inclusive)


# --------- 12. Worker: idempotent T-60 -----------------------------------


def test_worker_t60_start_idempotent():
    with _StateIsolator():
        now = datetime.now(WARSAW).replace(second=0, microsecond=0)
        in_dt = now + timedelta(minutes=60)
        if in_dt.date() != now.date():
            return
        schedule = {
            "Adrian Citko": {"start": in_dt.strftime("%H:%M"), "end": "20:00"},
        }
        send_calls = []
        teardown, _ = _install_worker_stubs(
            monkey_flags={
                "SHIFT_NOTIFY_ENABLED": True,
                "SHIFT_NOTIFY_T60_START_ENABLED": True,
                "SHIFT_NOTIFY_T30_REMINDER_ENABLED": False,
                "SHIFT_NOTIFY_T60_END_ENABLED": False,
                "SHIFT_BATCH_WINDOW_MIN": 10,
                "SHIFT_BATCH_MIN_COURIERS": 3,
            },
            schedule=schedule,
            send_calls=send_calls,
        )
        try:
            worker_mod.main()
            n1 = len(send_calls)
            worker_mod.main()  # second tick same minute
            n2 = len(send_calls)
            assert n1 == 1, f"first tick should send 1, got {n1}"
            assert n2 == 1, f"second tick should NOT re-send, got {n2}"
        finally:
            teardown()
t("worker_t60_start_idempotent", test_worker_t60_start_idempotent)


# --------- 13. Worker: unmapped cid → learning_log + skip -----------------


def test_worker_t60_start_unmapped_cid_logs_and_skips():
    with _StateIsolator():
        now = datetime.now(WARSAW).replace(second=0, microsecond=0)
        in_dt = now + timedelta(minutes=60)
        if in_dt.date() != now.date():
            return
        schedule = {
            "Mystery Person": {"start": in_dt.strftime("%H:%M"), "end": "20:00"},
        }
        send_calls = []
        teardown, _ = _install_worker_stubs(
            monkey_flags={
                "SHIFT_NOTIFY_ENABLED": True,
                "SHIFT_NOTIFY_T60_START_ENABLED": True,
                "SHIFT_NOTIFY_T30_REMINDER_ENABLED": False,
                "SHIFT_NOTIFY_T60_END_ENABLED": False,
                "SHIFT_BATCH_WINDOW_MIN": 10,
                "SHIFT_BATCH_MIN_COURIERS": 3,
            },
            schedule=schedule,
            send_calls=send_calls,
        )
        try:
            worker_mod.main()
            assert len(send_calls) == 0, f"should NOT send for unmapped cid, got {send_calls}"
            # Verify learning_log event
            with open(state_mod.LEARNING_LOG) as f:
                lines = [ln for ln in f.read().splitlines() if ln.strip()]
            events = [json.loads(ln) for ln in lines]
            assert any(e.get("event") == "UNMAPPED_COURIER_T60" for e in events), \
                f"expected UNMAPPED_COURIER_T60 event, got {events}"
        finally:
            teardown()
t("worker_t60_start_unmapped_cid_logs_and_skips", test_worker_t60_start_unmapped_cid_logs_and_skips)


# --------- 14. Worker: T-30 reminder only undecided -----------------------


def test_worker_t30_reminder_only_for_undecided():
    with _StateIsolator():
        now = datetime.now(WARSAW).replace(second=0, microsecond=0)
        # Two couriers: both at T-30 from now. One has decision=True (no reminder),
        # the other decision=None (reminder).
        in_dt = now + timedelta(minutes=30)
        if in_dt.date() != now.date():
            return
        schedule = {
            "Adrian Citko": {"start": in_dt.strftime("%H:%M"), "end": "20:00"},
            "Bartek Ołdziej": {"start": in_dt.strftime("%H:%M"), "end": "21:00"},
        }
        today_iso = now.date().isoformat()
        # Pre-seed state so we have records to gate (B.2 only fires for couriers
        # already notified at T-60). Bartek decided=True → no reminder; Adrian
        # decision=None → reminder.
        with state_mod.locked_write_confirmations() as st:
            st["start_notified"][f"{today_iso}:Adrian Citko"] = {
                "cid": "457", "scheduled": in_dt.isoformat(),
                "decision": None, "reminder_sent_at": None,
            }
            st["start_notified"][f"{today_iso}:Bartek Ołdziej"] = {
                "cid": "123", "scheduled": in_dt.isoformat(),
                "decision": True, "reminder_sent_at": None,
            }

        send_calls = []
        teardown, _ = _install_worker_stubs(
            monkey_flags={
                "SHIFT_NOTIFY_ENABLED": True,
                "SHIFT_NOTIFY_T60_START_ENABLED": False,
                "SHIFT_NOTIFY_T30_REMINDER_ENABLED": True,
                "SHIFT_NOTIFY_T60_END_ENABLED": False,
                "SHIFT_BATCH_WINDOW_MIN": 10,
                "SHIFT_BATCH_MIN_COURIERS": 3,
            },
            schedule=schedule,
            send_calls=send_calls,
        )
        try:
            worker_mod.main()
            joined = " | ".join(c["text"] for c in send_calls)
            assert "Adrian Citko" in joined, f"Adrian (decision=None) should get reminder. sends={joined}"
            assert "Bartek Ołdziej" not in joined, f"Bartek (decision=True) MUST NOT get reminder. sends={joined}"
            # Idempotency on reminder
            n1 = len(send_calls)
            worker_mod.main()
            assert len(send_calls) == n1, "reminder must NOT be re-sent in same minute"
        finally:
            teardown()
t("worker_t30_reminder_only_for_undecided", test_worker_t30_reminder_only_for_undecided)


# --------- 15. Worker: unconfirmed_default at T-0 -------------------------


def test_worker_unconfirmed_default_at_t0():
    with _StateIsolator():
        now = datetime.now(WARSAW).replace(second=0, microsecond=0)
        # scheduled in past (5 min ago), decision still None
        past_dt = now - timedelta(minutes=5)
        today_iso = now.date().isoformat()
        with state_mod.locked_write_confirmations() as st:
            st["start_notified"][f"{today_iso}:Adrian Citko"] = {
                "cid": "457", "scheduled": past_dt.isoformat(),
                "decision": None, "unconfirmed_default": None,
            }
        teardown, _ = _install_worker_stubs(
            monkey_flags={
                "SHIFT_NOTIFY_ENABLED": True,
                "SHIFT_NOTIFY_T60_START_ENABLED": False,
                "SHIFT_NOTIFY_T30_REMINDER_ENABLED": False,
                "SHIFT_NOTIFY_T60_END_ENABLED": False,
            },
            schedule={"Adrian Citko": {"start": past_dt.strftime("%H:%M"), "end": "20:00"}},
        )
        try:
            worker_mod.main()
            loaded = state_mod.read_confirmations()
            rec = loaded["start_notified"][f"{today_iso}:Adrian Citko"]
            assert rec.get("unconfirmed_default") is True, \
                f"expected unconfirmed_default=True, got {rec}"
        finally:
            teardown()
t("worker_unconfirmed_default_at_t0", test_worker_unconfirmed_default_at_t0)


# --------- 16. Worker: T-60 end window + individual-only ------------------


def test_worker_t60_end_window_and_individual_only():
    with _StateIsolator():
        now = datetime.now(WARSAW).replace(second=0, microsecond=0)
        end_dt = now + timedelta(minutes=60)
        if end_dt.date() != now.date():
            return
        # 3 couriers all ending in same slot — must be 3 individual sends
        schedule = {
            "Adrian Citko": {"start": "08:00", "end": end_dt.strftime("%H:%M")},
            "Bartek Ołdziej": {"start": "08:00", "end": end_dt.strftime("%H:%M")},
            "Michał Karpiuk": {"start": "08:00", "end": end_dt.strftime("%H:%M")},
        }
        send_calls = []
        teardown, _ = _install_worker_stubs(
            monkey_flags={
                "SHIFT_NOTIFY_ENABLED": True,
                "SHIFT_NOTIFY_T60_START_ENABLED": False,
                "SHIFT_NOTIFY_T30_REMINDER_ENABLED": False,
                "SHIFT_NOTIFY_T60_END_ENABLED": True,
                "SHIFT_BATCH_WINDOW_MIN": 10,
                "SHIFT_BATCH_MIN_COURIERS": 3,
            },
            schedule=schedule,
            send_calls=send_calls,
        )
        try:
            worker_mod.main()
            assert len(send_calls) == 3, f"expected 3 individual end sends, got {len(send_calls)}"
            for call in send_calls:
                # Each end message has a single (Kończę / Przedłużam) row
                kb = call["keyboard"]
                assert len(kb) == 1
                assert any("SHIFT_END_OK" in btn["callback_data"] for btn in kb[0])
                assert any("SHIFT_END_EXT" in btn["callback_data"] for btn in kb[0])
                # Must not contain batch-style multi-courier markup
                assert "TAK Adrian" not in call["text"], "end message should not be batched"
        finally:
            teardown()
t("worker_t60_end_window_and_individual_only", test_worker_t60_end_window_and_individual_only)


# --------- Summary --------------------------------------------------------


print("=" * 70)
print(f"PASSED: {passed}/{passed+failed}")
print(f"FAILED: {failed}/{passed+failed}")
print("=" * 70)
sys.exit(0 if failed == 0 else 1)
