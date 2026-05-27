"""Tests for analyze_shadow_logs tool."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone

import pytest

_SCRIPTS_ROOT = "/root/.openclaw/workspace/scripts"
if _SCRIPTS_ROOT not in sys.path:
    sys.path.insert(0, _SCRIPTS_ROOT)

from dispatch_v2.tools import analyze_shadow_logs as SL


def _ts(now, **delta):
    return (now - timedelta(**delta)).isoformat()


@pytest.fixture
def now():
    return datetime(2026, 5, 27, 18, 0, tzinfo=timezone.utc)


def test_analyze_drive_calibration(tmp_path, now):
    log = tmp_path / "drive.jsonl"
    entries = [
        {"ts": _ts(now, hours=1), "raw_predicted": 10.0, "calibrated_predicted": 14.0, "actual": 15.0, "pos_source": "gps", "tier": "gold"},
        {"ts": _ts(now, hours=2), "raw_predicted": 8.0, "calibrated_predicted": 13.0, "actual": 14.0, "pos_source": "no_gps", "tier": "std"},
        {"ts": _ts(now, days=20), "raw_predicted": 5.0, "calibrated_predicted": 5.0, "actual": 5.0, "pos_source": "gps", "tier": "gold"},  # outside 7d cutoff
    ]
    log.write_text("\n".join(json.dumps(e) for e in entries))
    cutoff = now - timedelta(days=7)
    out = SL.analyze_drive_calibration(str(log), cutoff)
    assert out["n_lines"] == 2  # third filtered out
    assert out["median_raw"] in (5.0, 5.5, 6.0)
    assert out["median_cal"] == 1.0
    assert "gps" in out["per_pos"]


def test_analyze_drive_calibration_missing_file(tmp_path, now):
    cutoff = now - timedelta(days=7)
    out = SL.analyze_drive_calibration(str(tmp_path / "missing.jsonl"), cutoff)
    assert out["n_lines"] == 0


def test_analyze_carry_chain(tmp_path, now):
    log = tmp_path / "carry.jsonl"
    entries = [
        {"ts": _ts(now, hours=1), "would_block": True, "chain_depth": 2, "reason": "kk_dinner"},
        {"ts": _ts(now, hours=2), "would_block": False, "chain_depth": 1, "reason": "ok"},
        {"ts": _ts(now, hours=3), "would_block": True, "chain_depth": 3, "reason": "kk_dinner"},
    ]
    log.write_text("\n".join(json.dumps(e) for e in entries))
    cutoff = now - timedelta(days=7)
    out = SL.analyze_carry_chain(str(log), cutoff)
    assert out["n"] == 3
    assert out["would_block"] == 2
    assert out["would_block_rate"] == round(2 / 3, 4)
    assert out["depth_distribution"][2] == 1
    assert out["depth_distribution"][3] == 1
    assert out["reason_distribution"]["kk_dinner"] == 2


def test_analyze_generic_shadow(tmp_path, now):
    log = tmp_path / "c2.jsonl"
    entries = [
        {"ts": _ts(now, hours=1), "action": "PROPOSE", "verdict": "OK"},
        {"ts": _ts(now, hours=2), "action": "PROPOSE", "verdict": "KOORD"},
    ]
    log.write_text("\n".join(json.dumps(e) for e in entries))
    cutoff = now - timedelta(days=7)
    out = SL.analyze_generic_shadow(str(log), cutoff)
    assert out["n"] == 2
    assert out["actions"]["PROPOSE"] == 2


def test_cli_smoke(tmp_path):
    out = tmp_path / "summary.md"
    rc = SL.main([
        "--days", "7",
        "--out", str(out),
        "--drive-log", str(tmp_path / "x.jsonl"),
        "--c2-log", str(tmp_path / "x.jsonl"),
        "--c5-log", str(tmp_path / "x.jsonl"),
        "--carry-log", str(tmp_path / "x.jsonl"),
        "--quiet",
    ])
    assert rc == 0
    assert out.exists()
    content = out.read_text()
    assert "Shadow logs weekly summary" in content
    assert "carry_chain" in content


def test_percentile_edge():
    assert SL._percentile([], 0.5) is None
    assert SL._percentile([1], 0.5) == 1
    assert SL._percentile([1, 2, 3, 4, 5], 0.5) == 3


def test_median_edge():
    assert SL._median([]) is None
    assert SL._median([5]) == 5
    assert SL._median([1, 2, 3]) == 2
    assert SL._median([1, 2, 3, 4]) == 2.5
