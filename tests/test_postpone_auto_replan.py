"""Tests for postpone_sweeper module (Tech-debt #20)."""
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import postpone_sweeper  # noqa: E402


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_postponed(tmp_path, monkeypatch):
    """Replace POSTPONED_PATH with a temporary file."""
    p = tmp_path / "postponed_proposals.json"
    monkeypatch.setattr(postpone_sweeper, "POSTPONED_PATH", str(p))
    return p


@pytest.fixture
def tmp_pending(tmp_path, monkeypatch):
    """Replace PENDING_PROPOSALS_PATH with a temporary file."""
    p = tmp_path / "pending_proposals.json"
    monkeypatch.setattr(postpone_sweeper, "PENDING_PROPOSALS_PATH", str(p))
    return p


def _make_entry(
    postpone_count: int = 0,
    minutes_ahead: int = 0,
    oid: str = "12345",
    decision_record: dict = None,
) -> dict:
    """Helper to create a postponed entry dict."""
    now = datetime.now(timezone.utc)
    postponed_until = (now + timedelta(minutes=minutes_ahead)).isoformat()
    if decision_record is None:
        decision_record = {"order_event": {"order_id": oid, "restaurant": "Test"}}
    return {
        "postponed_until": postponed_until,
        "postpone_count": postpone_count,
        "decision_record": decision_record,
        "original_message_id": 100,
        "minutes": 10,
        "ts": now.isoformat(),
    }


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_sweeper_handles_missing_file_gracefully(tmp_postponed, tmp_pending):
    """run_once() returns stats with all zeros, no crash when file missing."""
    stats = postpone_sweeper.run_once()
    assert stats == {
        "checked": 0,
        "resolved": 0,
        "escalated": 0,
        "reemitted": 0,
        "skipped": 0,
        "errors": 0,
    }


def test_sweeper_skips_not_yet_expired(tmp_postponed, tmp_pending):
    """Entry with postponed_until = now+5min → skipped=1, file unchanged."""
    entry = _make_entry(minutes_ahead=5)
    postpone_sweeper._atomic_write_json(str(tmp_postponed), {"12345": entry})
    stats = postpone_sweeper.run_once()
    assert stats["skipped"] == 1
    assert stats["reemitted"] == 0
    # entry still present
    data = postpone_sweeper._load_json_safe(str(tmp_postponed), {})
    assert "12345" in data


def test_sweeper_resolves_when_cid_set(tmp_postponed, tmp_pending):
    """Expired entry with cid set → resolved=1, entry popped."""
    entry = _make_entry(minutes_ahead=-5)
    postpone_sweeper._atomic_write_json(str(tmp_postponed), {"12345": entry})

    # mock state_machine._read_state to return a cid
    mock_state = {"orders": {"12345": {"cid": "500"}}}
    with patch.object(postpone_sweeper, "state_machine") as mock_sm:
        mock_sm._read_state.return_value = mock_state
        stats = postpone_sweeper.run_once()

    assert stats["resolved"] == 1
    assert stats["checked"] == 1
    # entry removed
    data = postpone_sweeper._load_json_safe(str(tmp_postponed), {})
    assert "12345" not in data


def test_sweeper_escalates_when_count_reaches_max(tmp_postponed, tmp_pending):
    """Entry with postpone_count=2 expired → escalated=1, alert sent, entry popped."""
    entry = _make_entry(postpone_count=2, minutes_ahead=-5)
    postpone_sweeper._atomic_write_json(str(tmp_postponed), {"12345": entry})

    with patch.object(postpone_sweeper, "telegram_utils") as mock_tg:
        mock_tg.send_admin_alert = MagicMock()
        with patch.object(postpone_sweeper, "state_machine") as mock_sm:
            mock_sm._read_state.return_value = {"orders": {"12345": {"cid": None}}}
            stats = postpone_sweeper.run_once()

    assert stats["escalated"] == 1
    assert stats["checked"] == 1
    # alert called with POSTPONE_ESCALATED
    mock_tg.send_admin_alert.assert_called_once()
    alert_text = mock_tg.send_admin_alert.call_args[0][0]
    assert "POSTPONE_ESCALATED" in alert_text
    assert "12345" in alert_text
    # entry removed
    data = postpone_sweeper._load_json_safe(str(tmp_postponed), {})
    assert "12345" not in data


def test_sweeper_reemits_when_unassigned_and_below_max(
    tmp_postponed, tmp_pending
):
    """Entry count=1 expired, cid=None → reemitted=1, pending file updated."""
    entry = _make_entry(postpone_count=1, minutes_ahead=-5)
    postpone_sweeper._atomic_write_json(str(tmp_postponed), {"12345": entry})

    from types import SimpleNamespace
    mock_result = SimpleNamespace(verdict="PROPOSE")

    with patch.object(postpone_sweeper, "state_machine") as mock_sm:
        mock_sm._read_state.return_value = {"orders": {"12345": {"cid": None}}}
        with patch.object(postpone_sweeper, "courier_resolver") as mock_cr:
            mock_cr.dispatchable_fleet.return_value = {}
            with patch.object(postpone_sweeper, "dispatch_pipeline") as mock_dp:
                mock_dp.assess_order.return_value = mock_result
                stats = postpone_sweeper.run_once()

    assert stats["reemitted"] == 1
    assert stats["checked"] == 1
    # pending file has entry
    pending = postpone_sweeper._load_json_safe(str(tmp_pending), {})
    assert "12345" in pending
    assert pending["12345"]["reemitted_from_postpone"] is True
    # postponed entry removed
    data = postpone_sweeper._load_json_safe(str(tmp_postponed), {})
    assert "12345" not in data


def test_sweeper_idempotent_on_rerun(tmp_postponed, tmp_pending):
    """Second run after first processes all entries → all zeros."""
    entry = _make_entry(postpone_count=0, minutes_ahead=-5)
    postpone_sweeper._atomic_write_json(str(tmp_postponed), {"12345": entry})

    from types import SimpleNamespace
    mock_result = SimpleNamespace(verdict="PROPOSE")

    with patch.object(postpone_sweeper, "state_machine") as mock_sm:
        mock_sm._read_state.return_value = {"orders": {"12345": {"cid": None}}}
        with patch.object(postpone_sweeper, "courier_resolver") as mock_cr:
            mock_cr.dispatchable_fleet.return_value = {}
            with patch.object(postpone_sweeper, "dispatch_pipeline") as mock_dp:
                mock_dp.assess_order.return_value = mock_result
                stats1 = postpone_sweeper.run_once()
                stats2 = postpone_sweeper.run_once()

    assert stats1["reemitted"] == 1
    assert stats2 == {
        "checked": 0,
        "resolved": 0,
        "escalated": 0,
        "reemitted": 0,
        "skipped": 0,
        "errors": 0,
    }


def test_atomic_write_uses_replace(tmp_path):
    """_atomic_write_json writes valid JSON, temp file cleaned up."""
    p = tmp_path / "test.json"
    data = {"key": "value"}
    postpone_sweeper._atomic_write_json(str(p), data)
    assert p.exists()
    with open(p) as f:
        assert json.load(f) == data
    # no .tmp files left
    tmp_files = list(tmp_path.glob("*.tmp"))
    assert len(tmp_files) == 0


def test_sweeper_keeps_entry_on_assess_order_error(
    tmp_postponed, tmp_pending
):
    """When assess_order raises, entry NOT popped, errors incremented."""
    entry = _make_entry(postpone_count=0, minutes_ahead=-5)
    postpone_sweeper._atomic_write_json(str(tmp_postponed), {"12345": entry})

    with patch.object(postpone_sweeper, "state_machine") as mock_sm:
        mock_sm._read_state.return_value = {"orders": {"12345": {"cid": None}}}
        with patch.object(postpone_sweeper, "courier_resolver") as mock_cr:
            mock_cr.dispatchable_fleet.return_value = {}
            with patch.object(postpone_sweeper, "dispatch_pipeline") as mock_dp:
                mock_dp.assess_order.side_effect = ValueError("test error")
                stats = postpone_sweeper.run_once()

    assert stats["errors"] == 1
    assert stats["checked"] == 1
    # entry still present
    data = postpone_sweeper._load_json_safe(str(tmp_postponed), {})
    assert "12345" in data
