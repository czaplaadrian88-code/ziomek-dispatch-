"""Tests for dispatch_v2.pending_pool."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

from dispatch_v2 import pending_pool as pp


@pytest.fixture(autouse=True)
def _patch_paths(tmp_path: Path, monkeypatch):
    """Redirect POOL_PATH, LOCK_PATH, LOG_PATH to tmp_path."""
    pool_path = tmp_path / "pending_pool.json"
    lock_path = tmp_path / "pending_pool.lock"
    log_path = tmp_path / "pending_pool_log.jsonl"
    monkeypatch.setattr(pp, "POOL_PATH", pool_path)
    monkeypatch.setattr(pp, "LOCK_PATH", lock_path)
    monkeypatch.setattr(pp, "LOG_PATH", log_path)
    # ensure parent exists
    pool_path.parent.mkdir(parents=True, exist_ok=True)
    yield


def _now_iso():
    return datetime.now(timezone.utc).isoformat()


def test_compute_freeze_at_pickup_minus_lead():
    """pickup_ready_at minus lead_min when created is earlier."""
    created = "2026-05-18T10:00:00+00:00"
    pickup = "2026-05-18T10:30:00+00:00"
    lead_min = 15.0
    freeze = pp.compute_freeze_at(created, pickup, lead_min)
    # pickup - 15min = 10:15
    expected = "2026-05-18T10:15:00+00:00"
    assert freeze == expected


def test_compute_freeze_at_created_later():
    """created is later than pickup - lead, so freeze = created."""
    created = "2026-05-18T10:20:00+00:00"
    pickup = "2026-05-18T10:30:00+00:00"
    lead_min = 15.0
    freeze = pp.compute_freeze_at(created, pickup, lead_min)
    # pickup - 15min = 10:15, created = 10:20 => max = 10:20
    expected = "2026-05-18T10:20:00+00:00"
    assert freeze == expected


def test_upsert_new_order():
    """upsert_order creates entry with defaults and freeze_at."""
    oid = "order1"
    created = "2026-05-18T09:00:00+00:00"
    pickup = "2026-05-18T09:30:00+00:00"
    pp.upsert_order(oid, created, pickup)
    pool = pp.load_pool()
    entry = pool.get(oid)
    assert entry is not None
    assert entry["order_id"] == oid
    assert entry["created_at"] == created
    assert entry["pickup_ready_at"] == pickup
    # freeze_at = max(created, pickup - lead_min)
    # default lead_min from common.FREEZE_LEAD_MIN (assume 15)
    from dispatch_v2 import common
    lead = common.FREEZE_LEAD_MIN
    from datetime import timedelta
    pickup_dt = datetime.fromisoformat(pickup.replace("Z", "+00:00"))
    created_dt = datetime.fromisoformat(created.replace("Z", "+00:00"))
    expected_freeze = max(created_dt, pickup_dt - timedelta(minutes=lead)).isoformat()
    assert entry["freeze_at"] == expected_freeze
    assert entry["churn_count"] == 0
    assert entry["frozen"] is False
    assert entry["frozen_at"] is None
    assert entry["removed_reason"] is None
    assert "updated_at" in entry


def test_upsert_existing_preserves_created_at():
    """upsert_order on existing entry does not overwrite created_at."""
    oid = "order2"
    created = "2026-05-18T08:00:00+00:00"
    pickup1 = "2026-05-18T08:30:00+00:00"
    pp.upsert_order(oid, created, pickup1)
    # now update with different pickup_ready_at and tentative_cid
    pickup2 = "2026-05-18T09:00:00+00:00"
    pp.upsert_order(oid, created, pickup2, tentative_cid="courier1")
    pool = pp.load_pool()
    entry = pool[oid]
    assert entry["created_at"] == created  # unchanged
    assert entry["pickup_ready_at"] == pickup2
    assert entry["tentative_cid"] == "courier1"


def test_remove_order():
    """remove_order deletes entry from pool."""
    oid = "order3"
    pp.upsert_order(oid, "2026-05-18T10:00:00+00:00", "2026-05-18T10:30:00+00:00")
    assert oid in pp.load_pool()
    pp.remove_order(oid, "test_reason")
    assert oid not in pp.load_pool()


def test_get_active_excludes_frozen():
    """get_active returns only entries where frozen is False."""
    oid1 = "active1"
    oid2 = "frozen1"
    pp.upsert_order(oid1, "2026-05-18T10:00:00+00:00", "2026-05-18T10:30:00+00:00")
    pp.upsert_order(oid2, "2026-05-18T10:00:00+00:00", "2026-05-18T10:30:00+00:00",
                    frozen=True, frozen_at=_now_iso())
    active = pp.get_active()
    active_ids = [e["order_id"] for e in active]
    assert oid1 in active_ids
    assert oid2 not in active_ids


def test_json_valid_after_upsert(tmp_path):
    """After upsert, the JSON file is valid and contains the entry."""
    pool_path = tmp_path / "pending_pool.json"
    pp.upsert_order("test", "2026-05-18T10:00:00+00:00", "2026-05-18T10:30:00+00:00")
    with open(pool_path, "r", encoding="utf-8") as fh:
        data = json.load(fh)
    assert isinstance(data, dict)
    assert "test" in data
