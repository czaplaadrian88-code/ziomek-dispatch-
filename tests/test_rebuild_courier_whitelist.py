"""Tests for rebuild_courier_whitelist tool."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_SCRIPTS_ROOT = "/root/.openclaw/workspace/scripts"
if _SCRIPTS_ROOT not in sys.path:
    sys.path.insert(0, _SCRIPTS_ROOT)

from dispatch_v2.tools import rebuild_courier_whitelist as RW


@pytest.fixture
def fake_backfill(tmp_path):
    """Minimal backfill with 3 couriers: gold favorite, std overridden, slow new."""
    now = datetime.now(timezone.utc).isoformat()
    rows = []
    # courier 100 (gold favorite): 50 proposals, 10 overrides (20%, beats baseline)
    for i in range(50):
        rows.append({
            "order_id": f"o-fav-{i}",
            "decision_ts": now,
            "action": "PANEL_OVERRIDE" if i < 10 else "ACK",
            "proposed_courier_id": 100,
            "outcome": {
                "courier_id_final": "999" if i < 10 else "100",
                "picked_up_ts": now,
                "delivered_ts": (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat(),
            },
        })
    # courier 200 (std overridden hard): 100 proposals, 90 overrides
    for i in range(100):
        rows.append({
            "order_id": f"o-bad-{i}",
            "decision_ts": now,
            "action": "PANEL_OVERRIDE" if i < 90 else "ACK",
            "proposed_courier_id": 200,
            "outcome": {
                "courier_id_final": "999" if i < 90 else "200",
                "picked_up_ts": now,
                "delivered_ts": (datetime.now(timezone.utc) + timedelta(minutes=20)).isoformat(),
            },
        })
    # courier 300 (low data): 5 proposals
    for i in range(5):
        rows.append({
            "order_id": f"o-low-{i}",
            "decision_ts": now,
            "action": "ACK",
            "proposed_courier_id": 300,
            "outcome": {"courier_id_final": "300"},
        })
    p = tmp_path / "backfill.jsonl"
    p.write_text("\n".join(json.dumps(r) for r in rows))
    return str(p)


@pytest.fixture
def fake_state(tmp_path):
    tiers = {
        "100": {"name": "Favorite", "bag": {"tier": "gold"}, "inactive": False},
        "200": {"name": "Overridden", "bag": {"tier": "std"}, "inactive": False},
        "300": {"name": "LowData", "bag": {"tier": "new"}, "inactive": False},
        "999": {"name": "Forced", "bag": {"tier": "gold"}, "inactive": False},
    }
    names = {"100": "Favorite", "200": "Overridden", "300": "LowData", "999": "Forced"}
    load = {"2026-05-27": {"100": 50, "200": 100, "300": 5, "999": 100}}
    tp = tmp_path / "tiers.json"
    tp.write_text(json.dumps(tiers))
    np = tmp_path / "names.json"
    np.write_text(json.dumps(names))
    lp = tmp_path / "load.json"
    lp.write_text(json.dumps(load))
    return {"tiers": str(tp), "names": str(np), "load": str(lp)}


def test_aggregate_basic(fake_backfill, fake_state):
    agg = RW.aggregate(fake_backfill, fake_state["load"], days=14)
    # 3 distinct cid proposals (100, 200, 300)
    proposers = set()
    for props in agg["orders_proposed"].values():
        proposers.update(props)
    assert proposers == {"100", "200", "300"}
    assert len(agg["orders_action"]) == 155  # 50 + 100 + 5


def test_metrics_baseline(fake_backfill, fake_state):
    agg = RW.aggregate(fake_backfill, fake_state["load"], days=14)
    m = RW.per_courier_metrics(agg)
    # baseline override rate = 100 (overrides for cid200) + 10 (for cid100) = 100 unique override orders
    # / 155 unique orders. 90 unique orders for cid200 + 10 unique for cid100 = 100/155 = 64.5%
    assert 0.60 < m["baseline_override_rate"] < 0.70
    # cid 200 override rate = 90/100 = 90%
    assert m["n_proposed"]["200"] == 100
    assert m["n_override"]["200"] == 90


def test_classify_gold_beats_baseline(fake_backfill, fake_state):
    """Gold courier with 20% override vs baseline 64.5% beats baseline by 44pp >> 10pp threshold."""
    agg = RW.aggregate(fake_backfill, fake_state["load"], days=14)
    m = RW.per_courier_metrics(agg)
    tiers = json.loads(open(fake_state["tiers"]).read())
    names = json.loads(open(fake_state["names"]).read())
    buckets = RW.build_buckets(agg, m, tiers, names)
    cids_wl = {e["cid"] for e in buckets["WHITELIST"]}
    assert "100" in cids_wl, f"Expected gold favorite cid 100 in WHITELIST, got {cids_wl}"


def test_classify_overridden_blacklist(fake_backfill, fake_state):
    """std courier with 90% override vs baseline 64.5% is worse → BLACKLIST."""
    agg = RW.aggregate(fake_backfill, fake_state["load"], days=14)
    m = RW.per_courier_metrics(agg)
    tiers = json.loads(open(fake_state["tiers"]).read())
    names = json.loads(open(fake_state["names"]).read())
    buckets = RW.build_buckets(agg, m, tiers, names)
    cids_bl = {e["cid"] for e in buckets["BLACKLIST"]}
    assert "200" in cids_bl


def test_classify_low_data_insufficient(fake_backfill, fake_state):
    agg = RW.aggregate(fake_backfill, fake_state["load"], days=14)
    m = RW.per_courier_metrics(agg)
    tiers = json.loads(open(fake_state["tiers"]).read())
    names = json.loads(open(fake_state["names"]).read())
    buckets = RW.build_buckets(agg, m, tiers, names)
    cids_ins = {e["cid"] for e in buckets["INSUFFICIENT_DATA"]}
    assert "300" in cids_ins


def test_meets_strict_original():
    # 50 proposals, 10 override (20%), r6 5%, n=30
    assert RW.meets_strict_original(50, 10, 0.05, 30)
    # 50 proposals, 20 override (40%) — fail strict
    assert not RW.meets_strict_original(50, 20, 0.05, 30)
    # n_proposed too low
    assert not RW.meets_strict_original(40, 5, 0.05, 30)
    # r6 too high with significant sample
    assert not RW.meets_strict_original(50, 10, 0.20, 30)


def test_cli_writes_output(fake_backfill, fake_state, tmp_path):
    out = tmp_path / "wl.json"
    md = tmp_path / "wl.md"
    rc = RW.main([
        "--days", "14",
        "--out", str(out),
        "--backfill", fake_backfill,
        "--load", fake_state["load"],
        "--tiers", fake_state["tiers"],
        "--names", fake_state["names"],
        "--md", str(md),
        "--quiet",
    ])
    assert rc == 0
    assert out.exists()
    data = json.loads(out.read_text())
    assert "_meta" in data
    assert "WHITELIST" in data
    assert "criteria_rationale" in data["_meta"]
    assert "lens" in data["_meta"]
    assert md.exists()
    md_content = md.read_text()
    assert "Courier Whitelist" in md_content


def test_atomic_write_no_partial(tmp_path):
    out = tmp_path / "test.json"
    RW._atomic_write(str(out), '{"x": 1}')
    assert json.loads(out.read_text()) == {"x": 1}


def test_inactive_filtered(fake_backfill, tmp_path):
    tiers = {
        "100": {"name": "Fav", "bag": {"tier": "gold"}, "inactive": True},  # INACTIVE
        "200": {"name": "Over", "bag": {"tier": "std"}, "inactive": False},
        "300": {"name": "Low", "bag": {"tier": "new"}, "inactive": False},
        "999": {"name": "F", "bag": {"tier": "gold"}, "inactive": False},
    }
    names = {"100": "Fav", "200": "Over", "300": "Low"}
    load = {"2026-05-27": {"100": 50, "200": 100, "300": 5}}
    tp = tmp_path / "tiers.json"; tp.write_text(json.dumps(tiers))
    np = tmp_path / "names.json"; np.write_text(json.dumps(names))
    lp = tmp_path / "load.json"; lp.write_text(json.dumps(load))
    agg = RW.aggregate(fake_backfill, str(lp), days=14)
    m = RW.per_courier_metrics(agg)
    buckets = RW.build_buckets(agg, m, tiers, names)
    all_cids = {e["cid"] for b in buckets.values() for e in b}
    assert "100" not in all_cids, "Inactive courier must be filtered"
