"""Tests for faza7_daily_kpi tool."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

_SCRIPTS_ROOT = "/root/.openclaw/workspace/scripts"
if _SCRIPTS_ROOT not in sys.path:
    sys.path.insert(0, _SCRIPTS_ROOT)

from dispatch_v2.tools import faza7_daily_kpi as KPI


def _ts(now: datetime, **delta) -> str:
    return (now - timedelta(**delta)).isoformat()


@pytest.fixture
def now():
    return datetime(2026, 5, 27, 18, 0, tzinfo=timezone.utc)


@pytest.fixture
def fake_rows(now):
    """3 unique orders 24h ago (1 override), 7 unique 7d ago (3 override),
    14 unique 14d ago (7 override)."""
    rows = []
    # 24h: 3 orders
    for i in range(3):
        rows.append({
            "order_id": f"o24-{i}",
            "decision_ts": _ts(now, hours=2),
            "action": "PANEL_OVERRIDE" if i == 0 else "ACK",
            "auto_route": "AUTO" if i == 0 else "ACK",
            "outcome": {
                "picked_up_ts": _ts(now, hours=2),
                "delivered_ts": (now - timedelta(hours=2) + timedelta(minutes=20 + 20 * i)).isoformat(),
            },
        })
    # 7d span: 4 more orders
    for i in range(4):
        rows.append({
            "order_id": f"o7d-{i}",
            "decision_ts": _ts(now, days=4),
            "action": "PANEL_OVERRIDE" if i < 2 else "ACK",
            "auto_route": "ACK",
            "outcome": {
                "picked_up_ts": _ts(now, days=4),
                "delivered_ts": (now - timedelta(days=4) + timedelta(minutes=40 if i == 0 else 20)).isoformat(),
            },
        })
    # 14d span: 7 more orders
    for i in range(7):
        rows.append({
            "order_id": f"o14d-{i}",
            "decision_ts": _ts(now, days=10),
            "action": "PANEL_OVERRIDE" if i < 4 else "ACK",
            "auto_route": "ALERT",
            "outcome": {
                "picked_up_ts": _ts(now, days=10),
                "delivered_ts": (now - timedelta(days=10) + timedelta(minutes=20)).isoformat(),
            },
        })
    return rows


def test_override_rate_windows(fake_rows, now):
    tiers = {}
    k = KPI.kpi_override_rate(fake_rows, now, tiers)
    assert k["24h"]["total"] == 3
    assert k["24h"]["override"] == 1
    assert k["7d"]["total"] == 7
    assert k["7d"]["override"] == 3
    assert k["14d"]["total"] == 14
    assert k["14d"]["override"] == 7


def test_r6_breach_buckets(fake_rows, now):
    k = KPI.kpi_r6_breach(fake_rows, now)
    # o7d-0 has 40min delivery → breach (>35)
    # all others < 35 → no breach
    assert "ACK" in k
    # ACK route has o7d-0 (breach) + o7d-1/2/3 + o24-1/2 — 6 entries (some no breach)
    # at least 1 breach in ACK
    assert k["ACK"]["breach"] >= 1


def test_kebab_krol_dinner_split(now):
    rows = []
    # KK lunch 13:00 Warsaw = 11:00 UTC, no breach
    pu_lunch = (now.replace(hour=11, minute=0)).isoformat()
    dl_lunch = (now.replace(hour=11, minute=20)).isoformat()
    rows.append({
        "order_id": "kk-lunch-1",
        "decision_ts": _ts(now, hours=1),
        "restaurant": "Kebab Król",
        "outcome": {"picked_up_ts": pu_lunch, "delivered_ts": dl_lunch},
    })
    # KK dinner 18:00 Warsaw = 16:00 UTC, breach 50min
    pu_dinner = (now.replace(hour=16, minute=0)).isoformat()
    dl_dinner = (now.replace(hour=16, minute=50)).isoformat()
    rows.append({
        "order_id": "kk-dinner-1",
        "decision_ts": _ts(now, hours=1),
        "restaurant": "Kebab Król",
        "outcome": {"picked_up_ts": pu_dinner, "delivered_ts": dl_dinner},
    })
    k = KPI.kpi_kebab_krol(rows, now)
    assert k["lunch"]["n"] == 1
    assert k["lunch"]["breach"] == 0
    assert k["dinner"]["n"] == 1
    assert k["dinner"]["breach"] == 1
    assert k["dinner"]["rate"] == 1.0


def test_readiness_all_pass():
    override_kpi = {"7d": {"rate": 0.55}}
    drive_kpi = {"median_cal_bias": 5.0}
    kk_kpi = {"dinner": {"rate": 0.10}}
    r = KPI.faza7_readiness(override_kpi, drive_kpi, kk_kpi)
    assert r["all_pass"]


def test_readiness_override_blocks():
    override_kpi = {"7d": {"rate": 0.78}}  # baseline, blocks
    drive_kpi = {"median_cal_bias": 5.0}
    kk_kpi = {"dinner": {"rate": 0.10}}
    r = KPI.faza7_readiness(override_kpi, drive_kpi, kk_kpi)
    assert not r["all_pass"]
    assert not r["override_7d_below_60pct"]


def test_readiness_calib_soft_pass_when_missing():
    """Sprint 1 not LIVE yet → cal bias None should soft-pass."""
    override_kpi = {"7d": {"rate": 0.55}}
    drive_kpi = {"median_offset_min": None}
    kk_kpi = {"dinner": {"rate": 0.10}}
    r = KPI.faza7_readiness(override_kpi, drive_kpi, kk_kpi)
    assert r["calibration_bias_below_10min"]


def test_drive_min_calibration_no_log(tmp_path, now):
    """Empty/missing log returns zero counts (post-#21 Opcja B: algorithm-delta schema)."""
    missing = str(tmp_path / "missing.jsonl")
    out = KPI.kpi_drive_min_calibration(missing, now)
    assert out["n_total"] == 0
    assert out["median_offset_min"] is None
    assert out["ground_truth_available"] is False


def test_drive_min_calibration_with_entries(tmp_path, now):
    """Sprint 1 writer schema: raw_drive_min/calibrated_drive_min/offset_applied (no actual)."""
    log = tmp_path / "drive.jsonl"
    entries = [
        {"ts": _ts(now, hours=1), "raw_drive_min": 10.0, "calibrated_drive_min": 14.0, "offset_applied": 4.0, "floor_applied": False, "pos_source": "gps", "tier": "gold"},
        {"ts": _ts(now, hours=2), "raw_drive_min": 8.0, "calibrated_drive_min": 13.0, "offset_applied": 5.0, "floor_applied": True, "pos_source": "no_gps", "tier": "std"},
    ]
    log.write_text("\n".join(json.dumps(e) for e in entries))
    out = KPI.kpi_drive_min_calibration(str(log), now)
    assert out["n_total"] == 2
    assert out["ground_truth_available"] is False
    # offsets: 4, 5 → median 4.5; raws: 8, 10 → median 9.0; cals: 13, 14 → median 13.5
    assert out["median_offset_min"] == 4.5
    assert out["median_raw_min"] == 9.0
    assert out["median_calibrated_min"] == 13.5
    assert out["floor_applied_count"] == 1
    assert out["per_pos_source"]["gps"]["median_offset"] == 4.0
    assert out["per_pos_source"]["no_gps"]["median_offset"] == 5.0


def test_cli_writes_output(tmp_path, fake_rows):
    bf = tmp_path / "bf.jsonl"
    bf.write_text("\n".join(json.dumps(r) for r in fake_rows))
    out = tmp_path / "kpi.md"
    rc = KPI.main([
        "--date", "2026-05-27",
        "--out", str(out),
        "--backfill", str(bf),
        "--drive-log", str(tmp_path / "nope.jsonl"),
        "--whitelist", str(tmp_path / "nope.json"),
        "--tiers", str(tmp_path / "nope.json"),
        "--names", str(tmp_path / "nope.json"),
        "--quiet",
    ])
    assert rc == 0
    assert out.exists()
    content = out.read_text()
    assert "Faza 7 Daily KPI" in content
    assert "Override rate" in content
    assert "R6 breach" in content
    assert "Kebab Król" in content
    assert "readiness gate" in content
