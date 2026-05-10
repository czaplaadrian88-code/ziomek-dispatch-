"""V3.28 P4 — coordinator activations tests (Adrian doktryna 2026-05-10 wieczór).

Triggers:
1. Auto na pierwszym COURIER_ASSIGNED (state_machine hook)
2. Manual TG `<nick> start/stop`
Reset 06:00 daily via manual_overrides_daily_reset.

Bartek O. (cid=123) ma `coordinator: true` w courier_tiers.json.
"""
from __future__ import annotations
import json
import os
import tempfile
import shutil
import pytest
from dispatch_v2 import coordinator_activations as ca


@pytest.fixture(autouse=True)
def _isolated_state(monkeypatch, tmp_path):
    """Każdy test ma własny activations file."""
    test_path = str(tmp_path / "coordinator_activations.json")
    monkeypatch.setattr(ca, "ACTIVATIONS_PATH", test_path)
    monkeypatch.setattr(ca, "LOCK_PATH", test_path + ".lock")
    yield


def test_initial_state_no_active():
    assert ca.get_all_active() == set()
    assert ca.is_coordinator_active("123") is False


def test_activate_returns_true_first_time():
    changed = ca.activate("123", source="first_assignment_472001")
    assert changed is True
    assert ca.is_coordinator_active("123") is True
    assert "123" in ca.get_all_active()


def test_activate_idempotent():
    ca.activate("123", source="first_assignment_472001")
    changed = ca.activate("123", source="first_assignment_472002")
    assert changed is False  # already active
    assert ca.is_coordinator_active("123") is True


def test_deactivate_returns_true_when_active():
    ca.activate("123", source="first_assignment_472001")
    changed = ca.deactivate("123", source="telegram_manual_8765130486")
    assert changed is True
    assert ca.is_coordinator_active("123") is False


def test_deactivate_idempotent_when_not_active():
    changed = ca.deactivate("123", source="manual")
    assert changed is False
    assert ca.is_coordinator_active("123") is False


def test_reset_clears_all_active():
    ca.activate("123", source="auto")
    ca.activate("999", source="manual")
    n = ca.reset_all(source="daily_test")
    assert n == 2
    assert ca.get_all_active() == set()


def test_history_preserved_after_deactivate():
    ca.activate("123", source="first_assignment_472001")
    ca.deactivate("123", source="manual_stop")
    data = ca._load_locked()
    assert "history" in data
    assert any(h.get("cid") == "123" for h in data["history"])


def test_invalid_cid_no_op():
    changed = ca.activate("", source="bad")
    assert changed is False
    changed = ca.activate("None", source="bad")
    assert changed is False


def test_activation_records_source_and_timestamp():
    ca.activate("123", source="first_assignment_472001")
    data = ca._load_locked()
    entry = data["active"]["123"]
    assert entry["source"] == "first_assignment_472001"
    assert "activated_at" in entry
    assert entry["activated_at"].endswith("+00:00")  # UTC iso
