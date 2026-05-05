"""TASK A CZASÓWKI PROACTIVE — state module tests (2026-05-05).

Coverage (12):
  Schema + atomic writes (4):
   1. test_state_initial_read_returns_empty_skeleton
   2. test_locked_write_creates_file_with_orders_dict
   3. test_atomic_write_persists_changes_across_reads
   4. test_corrupted_json_recovers_with_warn_returns_empty

  new_state_record (3):
   5. test_new_state_record_basic_fields
   6. test_new_state_record_falls_back_to_pickup_at_warsaw
   7. test_new_state_record_with_minimal_osrec

  cleanup_stale (4):
   8. test_cleanup_stale_removes_post_pickup_orders
   9. test_cleanup_stale_removes_finalized_orders_after_4h
  10. test_cleanup_stale_keeps_recent_orders
  11. test_cleanup_stale_handles_invalid_records

  Idempotency (1):
  12. test_locked_write_yields_same_dict_on_reentry

Custom-runner pattern (matches tests/test_shift_telegram_router.py).
"""
import sys
from pathlib import Path
sys.path.insert(0, "/root/.openclaw/workspace/scripts")

import json
import shutil
import tempfile
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from dispatch_v2.czasowka_proactive import state as cp_state


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


@contextmanager
def isolated_state():
    """Redirect cp_state.STATE_PATH + LOCK_PATH to a tmpdir for test scope.

    Mirrors tests/_shift_test_helpers.isolated_shift_state pattern. Snapshot
    of orig paths in __enter__ (NIE __init__) — Lekcja #71 prevents test
    cross-contamination when module-level constants change between init+enter.
    """
    tmpdir = tempfile.mkdtemp(prefix="cz_proactive_test_")
    orig_state = cp_state.STATE_PATH
    orig_lock = cp_state.LOCK_PATH
    try:
        cp_state.STATE_PATH = Path(tmpdir) / "czasowka_proposals_state.json"
        cp_state.LOCK_PATH = Path(str(cp_state.STATE_PATH) + ".lock")
        yield Path(tmpdir)
    finally:
        cp_state.STATE_PATH = orig_state
        cp_state.LOCK_PATH = orig_lock
        shutil.rmtree(tmpdir, ignore_errors=True)


def _now():
    return datetime.now(timezone.utc)


# ============================================================
# Schema + atomic writes
# ============================================================

def test_state_initial_read_returns_empty_skeleton():
    with isolated_state():
        st = cp_state.read_proposals_state()
        assert isinstance(st, dict), f"expected dict, got {type(st)}"
        assert "orders" in st, f"missing 'orders' key: {st!r}"
        assert st["orders"] == {}, f"expected empty orders, got {st['orders']!r}"


t("state_initial_read_returns_empty_skeleton", test_state_initial_read_returns_empty_skeleton)


def test_locked_write_creates_file_with_orders_dict():
    with isolated_state() as tmp:
        with cp_state.locked_write_proposals_state() as st:
            st["orders"]["470001"] = {"first_seen_ts": _now().isoformat()}
        # File should now exist + contain order
        assert cp_state.STATE_PATH.exists(), "STATE_PATH not created"
        with open(cp_state.STATE_PATH) as f:
            data = json.load(f)
        assert "470001" in data["orders"], f"order not persisted: {data!r}"
        assert "updated_at" in data, "updated_at not stamped on commit"


t("locked_write_creates_file_with_orders_dict", test_locked_write_creates_file_with_orders_dict)


def test_atomic_write_persists_changes_across_reads():
    with isolated_state():
        with cp_state.locked_write_proposals_state() as st:
            st["orders"]["470010"] = {"restaurant": "Mama Thai"}
        # Re-read via read_proposals_state
        st2 = cp_state.read_proposals_state()
        assert st2["orders"]["470010"]["restaurant"] == "Mama Thai", \
            f"value not persisted: {st2!r}"


t("atomic_write_persists_changes_across_reads", test_atomic_write_persists_changes_across_reads)


def test_corrupted_json_recovers_with_warn_returns_empty():
    with isolated_state():
        # Write malformed JSON directly
        cp_state.STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
        cp_state.STATE_PATH.write_text("{not valid json")
        st = cp_state.read_proposals_state()
        assert st == {"orders": {}, "updated_at": None}, \
            f"expected empty skeleton, got {st!r}"
        # locked_write should also recover gracefully
        with cp_state.locked_write_proposals_state() as st:
            st["orders"]["470020"] = {"x": 1}
        st2 = cp_state.read_proposals_state()
        assert "470020" in st2["orders"], "recovery did not allow write"


t("corrupted_json_recovers_with_warn_returns_empty", test_corrupted_json_recovers_with_warn_returns_empty)


# ============================================================
# new_state_record
# ============================================================

def test_new_state_record_basic_fields():
    osrec = {
        "czas_odbioru_timestamp": "2026-05-05T13:00:00+02:00",
        "courier_id": "26",
        "restaurant": "Mama Thai",
        "delivery_address": "Mickiewicza 17",
        "delivery_city": "Białystok",
    }
    now = datetime(2026, 5, 5, 10, 0, 0, tzinfo=timezone.utc)
    rec = cp_state.new_state_record("470100", osrec, now)
    assert rec["first_seen_ts"] == now.isoformat()
    assert rec["czas_odbioru_ts"] == "2026-05-05T13:00:00+02:00"
    assert rec["id_kurier_holding"] == "26"
    assert rec["restaurant"] == "Mama Thai"
    assert rec["delivery_address"] == "Mickiewicza 17"
    assert rec["delivery_city"] == "Białystok"
    assert rec["triggers_fired"] == {}
    assert rec["excluded_candidates"] == []
    assert rec["final_assignment_cid"] is None
    assert rec["final_assignment_ts"] is None


t("new_state_record_basic_fields", test_new_state_record_basic_fields)


def test_new_state_record_falls_back_to_pickup_at_warsaw():
    osrec = {
        "pickup_at_warsaw": "2026-05-05T14:00:00",
        # no czas_odbioru_timestamp
        "courier_id": "26",
        "restaurant": "Toriko",
    }
    now = datetime(2026, 5, 5, 11, 0, 0, tzinfo=timezone.utc)
    rec = cp_state.new_state_record("470101", osrec, now)
    assert rec["czas_odbioru_ts"] == "2026-05-05T14:00:00"
    assert rec["restaurant"] == "Toriko"


t("new_state_record_falls_back_to_pickup_at_warsaw", test_new_state_record_falls_back_to_pickup_at_warsaw)


def test_new_state_record_with_minimal_osrec():
    osrec = {}
    now = datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc)
    rec = cp_state.new_state_record("470102", osrec, now)
    assert rec["first_seen_ts"] == now.isoformat()
    assert rec["czas_odbioru_ts"] is None
    assert rec["id_kurier_holding"] == ""
    assert rec["restaurant"] is None
    assert rec["triggers_fired"] == {}


t("new_state_record_with_minimal_osrec", test_new_state_record_with_minimal_osrec)


# ============================================================
# cleanup_stale
# ============================================================

def test_cleanup_stale_removes_post_pickup_orders():
    """czas_odbioru_ts < now - 1h → remove."""
    now = datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc)
    state = {
        "orders": {
            "old_pickup": {
                "czas_odbioru_ts": (now - timedelta(hours=2)).isoformat(),
            },
            "fresh_pickup": {
                "czas_odbioru_ts": (now + timedelta(minutes=30)).isoformat(),
            },
            "edge_pickup": {
                # 30 min ago: NOT post-pickup-1h yet
                "czas_odbioru_ts": (now - timedelta(minutes=30)).isoformat(),
            },
        }
    }
    removed = cp_state.cleanup_stale(state, now)
    assert removed == 1, f"expected 1 removed, got {removed}"
    assert "old_pickup" not in state["orders"]
    assert "fresh_pickup" in state["orders"]
    assert "edge_pickup" in state["orders"]


t("cleanup_stale_removes_post_pickup_orders", test_cleanup_stale_removes_post_pickup_orders)


def test_cleanup_stale_removes_finalized_orders_after_4h():
    """final_assignment_ts < now - 4h → remove."""
    now = datetime(2026, 5, 5, 18, 0, 0, tzinfo=timezone.utc)
    state = {
        "orders": {
            "old_final": {
                "czas_odbioru_ts": (now + timedelta(minutes=30)).isoformat(),
                "final_assignment_ts": (now - timedelta(hours=5)).isoformat(),
            },
            "recent_final": {
                "czas_odbioru_ts": (now + timedelta(minutes=30)).isoformat(),
                "final_assignment_ts": (now - timedelta(hours=1)).isoformat(),
            },
        }
    }
    removed = cp_state.cleanup_stale(state, now)
    assert removed == 1, f"expected 1 removed, got {removed}"
    assert "old_final" not in state["orders"]
    assert "recent_final" in state["orders"]


t("cleanup_stale_removes_finalized_orders_after_4h", test_cleanup_stale_removes_finalized_orders_after_4h)


def test_cleanup_stale_keeps_recent_orders():
    """Active orders (pickup in future, no final assignment) → keep."""
    now = datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc)
    state = {
        "orders": {
            "active_1": {
                "czas_odbioru_ts": (now + timedelta(hours=1)).isoformat(),
                "final_assignment_ts": None,
            },
            "active_2": {
                "czas_odbioru_ts": (now + timedelta(minutes=10)).isoformat(),
                "final_assignment_ts": None,
            },
        }
    }
    removed = cp_state.cleanup_stale(state, now)
    assert removed == 0, f"expected 0 removed, got {removed}"
    assert len(state["orders"]) == 2


t("cleanup_stale_keeps_recent_orders", test_cleanup_stale_keeps_recent_orders)


def test_cleanup_stale_handles_invalid_records():
    """Non-dict records or bad timestamps should not crash; remove non-dict."""
    now = datetime(2026, 5, 5, 12, 0, 0, tzinfo=timezone.utc)
    state = {
        "orders": {
            "garbage": "not a dict",
            "no_ts": {"restaurant": "X"},  # no czas_odbioru_ts → keep
            "bad_ts": {"czas_odbioru_ts": "totally invalid"},  # parse fail → keep
        }
    }
    removed = cp_state.cleanup_stale(state, now)
    assert removed >= 1, f"expected at least 1 removed (garbage), got {removed}"
    assert "garbage" not in state["orders"]
    assert "no_ts" in state["orders"]
    assert "bad_ts" in state["orders"]


t("cleanup_stale_handles_invalid_records", test_cleanup_stale_handles_invalid_records)


# ============================================================
# Idempotency / reentry
# ============================================================

def test_locked_write_yields_same_dict_on_reentry():
    """Two sequential locked_write should see the previous write."""
    with isolated_state():
        with cp_state.locked_write_proposals_state() as st:
            st["orders"]["A"] = {"v": 1}
        with cp_state.locked_write_proposals_state() as st:
            assert "A" in st["orders"], f"prev write not visible: {st!r}"
            assert st["orders"]["A"]["v"] == 1
            st["orders"]["A"]["v"] = 2
            st["orders"]["B"] = {"v": 99}
        st_final = cp_state.read_proposals_state()
        assert st_final["orders"]["A"]["v"] == 2
        assert st_final["orders"]["B"]["v"] == 99


t("locked_write_yields_same_dict_on_reentry", test_locked_write_yields_same_dict_on_reentry)


# ============================================================
# Final report
# ============================================================
print("=" * 70)
print(f"PASSED: {passed}/{passed+failed}")
print(f"FAILED: {failed}/{passed+failed}")
print("=" * 70)
sys.exit(0 if failed == 0 else 1)
