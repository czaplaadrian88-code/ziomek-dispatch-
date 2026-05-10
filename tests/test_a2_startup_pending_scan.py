"""A2 startup pending_proposals scan regression coverage (2026-05-08).

Per audit `AUDIT_2026-05-07/STATE_OWNERSHIP_EVENT_FLOW_AUDIT_2026-05-07.md` F9:
sieroty pending z expires_at w przeszłości (po crash/restart) muszą zostać
auto-processed PRZED launch workers, żeby eliminować window operatorskiej
confusion (do ~10s watchdog sleep).

Coverage:
  - empty pending → no-op summary
  - all fresh (none expired) → no-op summary
  - 1 expired status="planned" → TIMEOUT path (alert + log + remove)
  - 1 expired status="assigned" → TIMEOUT_SUPERSEDED path (silent + log + remove, no alert)
  - mixed expired+fresh → only expired processed, fresh remains
  - corrupt expires_at → graceful skip (continue, others process)
  - state_machine load fail → graceful (treat all as TIMEOUT)
  - _process_expired_pending happy paths SUPERSEDED + TIMEOUT
"""
import asyncio
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, MagicMock

import pytest

from dispatch_v2 import telegram_approver as ta
from dispatch_v2 import state_machine as sm_module


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_entry(oid: str, expires_at: datetime, sent_at: datetime = None) -> dict:
    sent_at = sent_at or (expires_at - timedelta(minutes=5))
    return {
        "order_id": oid,
        "message_id": 1000 + int(oid),
        "sent_at": sent_at.isoformat(),
        "expires_at": expires_at.isoformat(),
        "decision_record": {
            "ts": sent_at.isoformat(),
            "event_id": f"{oid}_TEST",
            "order_id": oid,
            "verdict": "PROPOSE",
            "best": {"courier_id": "100", "name": "Test K"},
        },
    }


def _make_state(pending: dict, tmp_path) -> dict:
    return {
        "token": "test_token",
        "admin_id": "8765130486",
        "pending": pending,
        "pending_path": str(tmp_path / "pending.json"),
        "learning_log_path": str(tmp_path / "learning.jsonl"),
    }


# ---------------------------------------------------------------------------
# _startup_scan_pending_expired
# ---------------------------------------------------------------------------


def test_empty_pending_no_op(tmp_path):
    state = _make_state({}, tmp_path)
    summary = asyncio.run(ta._startup_scan_pending_expired(state))
    assert summary == {"total": 0, "expired": 0, "processed": 0, "superseded": 0, "timeout": 0}


def test_all_fresh_no_expired(tmp_path):
    now = datetime.now(timezone.utc)
    pending = {
        "100": _make_entry("100", now + timedelta(minutes=4)),
        "101": _make_entry("101", now + timedelta(minutes=3)),
    }
    state = _make_state(pending, tmp_path)
    summary = asyncio.run(ta._startup_scan_pending_expired(state))
    assert summary["total"] == 2
    assert summary["expired"] == 0
    assert summary["processed"] == 0
    assert state["pending"] == pending


def test_one_expired_new_status_timeout_path(tmp_path):
    """status='new' = real brak decyzji → TIMEOUT alert + log."""
    now = datetime.now(timezone.utc)
    pending = {"200": _make_entry("200", now - timedelta(minutes=2))}
    state = _make_state(pending, tmp_path)

    fake_tg = MagicMock()
    with patch.object(sm_module, "get_all", return_value={"200": {"status": "new"}}):
        with patch.object(ta, "tg_request", fake_tg):
            with patch.object(ta, "append_learning") as mock_learn:
                summary = asyncio.run(ta._startup_scan_pending_expired(state))

    assert summary["expired"] == 1
    assert summary["processed"] == 1
    assert summary["timeout"] == 1
    assert summary["superseded"] == 0
    assert "200" not in state["pending"]
    fake_tg.assert_called_once()
    args, _kwargs = fake_tg.call_args
    assert "Timeout #200" in args[2]["text"]
    mock_learn.assert_called_once()
    assert mock_learn.call_args[0][1]["action"] == "TIMEOUT_SKIP"


def test_one_expired_assigned_superseded_path(tmp_path):
    now = datetime.now(timezone.utc)
    pending = {"300": _make_entry("300", now - timedelta(minutes=1))}
    state = _make_state(pending, tmp_path)

    fake_tg = MagicMock()
    with patch.object(sm_module, "get_all", return_value={"300": {"status": "assigned"}}):
        with patch.object(ta, "tg_request", fake_tg):
            with patch.object(ta, "append_learning") as mock_learn:
                summary = asyncio.run(ta._startup_scan_pending_expired(state))

    assert summary["expired"] == 1
    assert summary["processed"] == 1
    assert summary["superseded"] == 1
    assert summary["timeout"] == 0
    assert "300" not in state["pending"]
    fake_tg.assert_not_called()
    mock_learn.assert_called_once()
    assert mock_learn.call_args[0][1]["action"] == "TIMEOUT_SUPERSEDED"
    assert mock_learn.call_args[0][1]["timeout_outcome"] == "OVERRIDDEN_BY_LATER"


def test_mixed_expired_and_fresh(tmp_path):
    now = datetime.now(timezone.utc)
    pending = {
        "400": _make_entry("400", now - timedelta(seconds=30)),
        "401": _make_entry("401", now + timedelta(minutes=4)),
        "402": _make_entry("402", now - timedelta(minutes=10)),
    }
    state = _make_state(pending, tmp_path)

    fake_get_all = {
        "400": {"status": "new"},        # real brak decyzji → TIMEOUT
        "402": {"status": "delivered"},  # już obsłużone → SUPERSEDED
    }
    with patch.object(sm_module, "get_all", return_value=fake_get_all):
        with patch.object(ta, "tg_request", MagicMock()):
            with patch.object(ta, "append_learning"):
                summary = asyncio.run(ta._startup_scan_pending_expired(state))

    assert summary["total"] == 3
    assert summary["expired"] == 2
    assert summary["processed"] == 2
    assert summary["timeout"] == 1
    assert summary["superseded"] == 1
    assert "400" not in state["pending"]
    assert "402" not in state["pending"]
    assert "401" in state["pending"]


def test_corrupt_expires_at_graceful_skip(tmp_path, caplog):
    now = datetime.now(timezone.utc)
    bad_entry = {"order_id": "500", "expires_at": "INVALID-ISO-FORMAT", "decision_record": {}}
    good_entry = _make_entry("501", now - timedelta(minutes=1))
    pending = {"500": bad_entry, "501": good_entry}
    state = _make_state(pending, tmp_path)

    with patch.object(sm_module, "get_all", return_value={"501": {"status": "planned"}}):
        with patch.object(ta, "tg_request", MagicMock()):
            with patch.object(ta, "append_learning"):
                with caplog.at_level("WARNING"):
                    summary = asyncio.run(ta._startup_scan_pending_expired(state))

    # corrupt entry stays in pending (skipped, not processed)
    assert "500" in state["pending"]
    # good entry processed
    assert "501" not in state["pending"]
    assert summary["expired"] == 1
    assert summary["processed"] == 1
    assert any("parse expires_at fail" in r.message and "500" in r.message for r in caplog.records)


def test_state_machine_load_fail_treats_as_timeout(tmp_path, caplog):
    now = datetime.now(timezone.utc)
    pending = {"600": _make_entry("600", now - timedelta(minutes=2))}
    state = _make_state(pending, tmp_path)

    # state_machine import succeeds but get_all() raises
    fake_tg = MagicMock()
    with patch.object(sm_module, "get_all", side_effect=RuntimeError("DB locked")):
        with patch.object(ta, "tg_request", fake_tg):
            with patch.object(ta, "append_learning") as mock_learn:
                with caplog.at_level("WARNING"):
                    summary = asyncio.run(ta._startup_scan_pending_expired(state))

    # without state_machine context → treated as TIMEOUT (no cur_status to detect supersede)
    assert summary["timeout"] == 1
    assert summary["superseded"] == 0
    fake_tg.assert_called_once()
    assert mock_learn.call_args[0][1]["action"] == "TIMEOUT_SKIP"
    assert any("state_machine load fail" in r.message for r in caplog.records)


# ---------------------------------------------------------------------------
# _process_expired_pending direct tests
# ---------------------------------------------------------------------------


def test_process_expired_pending_superseded(tmp_path):
    now = datetime.now(timezone.utc)
    entry = _make_entry("700", now - timedelta(minutes=1))
    state = _make_state({"700": entry}, tmp_path)
    state_all = {"700": {"status": "picked_up"}}

    with patch.object(ta, "tg_request", MagicMock()) as mock_tg:
        with patch.object(ta, "append_learning") as mock_learn:
            action = asyncio.run(
                ta._process_expired_pending(state, "700", entry, now, state_all)
            )

    assert action == "SUPERSEDED"
    assert "700" not in state["pending"]
    mock_tg.assert_not_called()
    assert mock_learn.call_args[0][1]["timeout_outcome"] == "OVERRIDDEN_BY_LATER"


def test_process_expired_pending_timeout(tmp_path):
    now = datetime.now(timezone.utc)
    entry = _make_entry("800", now - timedelta(minutes=1))
    state = _make_state({"800": entry}, tmp_path)
    state_all = {}  # no state_machine context → TIMEOUT path

    with patch.object(ta, "tg_request", MagicMock()) as mock_tg:
        with patch.object(ta, "append_learning") as mock_learn:
            action = asyncio.run(
                ta._process_expired_pending(state, "800", entry, now, state_all)
            )

    assert action == "TIMEOUT"
    assert "800" not in state["pending"]
    mock_tg.assert_called_once()
    assert mock_learn.call_args[0][1]["action"] == "TIMEOUT_SKIP"
