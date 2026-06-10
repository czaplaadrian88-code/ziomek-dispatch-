"""Acceptance (PANEL_AGREE/PANEL_OVERRIDE) w daily_briefing — ETAP 3 krok 3 (Z-03).

Syntetyczny learning_log w tmp_path (zero dotykania żywych plików — klasa #180).
"""
from __future__ import annotations

import json
import sys
from collections import Counter
from datetime import datetime, timedelta, timezone

import pytest

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import daily_briefing as db


NOW = datetime.now(timezone.utc)


def _iso(dt):
    return dt.isoformat()


def _agree(oid, tier="gold", ts=None, prep_min=20.0, peak=False):
    ts = ts or NOW
    if peak:  # 12:30 Warsaw = peak (11-14)
        ts = ts.astimezone(db.WARSAW).replace(hour=12, minute=30).astimezone(timezone.utc)
    else:     # 16:30 Warsaw = off
        ts = ts.astimezone(db.WARSAW).replace(hour=16, minute=30).astimezone(timezone.utc)
    created = ts - timedelta(minutes=5)
    return {
        "ts": _iso(ts),
        "order_id": oid,
        "action": "PANEL_AGREE",
        "proposed_courier_id": "515",
        "actual_courier_id": "515",
        "latency_s": 120.0,
        "proposed_score": 90.0,
        "proposal_verdict": "PROPOSE",
        "restaurant": "Testownia",
        "proposed_tier": tier,
        "pickup_ready_at": _iso(created + timedelta(minutes=prep_min)),
        "order_created_at": _iso(created),
        "source": "panel",
        "panel_source": "panel_diff",
    }


def _override(oid, tier="std", ts=None, prep_min=90.0, peak=True,
              components=None):
    ts = ts or NOW
    if peak:
        ts = ts.astimezone(db.WARSAW).replace(hour=18, minute=0).astimezone(timezone.utc)
    else:
        ts = ts.astimezone(db.WARSAW).replace(hour=15, minute=0).astimezone(timezone.utc)
    created = ts - timedelta(minutes=5)
    best = {
        "courier_id": "413",
        "score": 70.0,
        "dwell_tier": tier,
        "bonus_r4": 40.0,
        "bonus_r1_corridor": -6.0,
        "timing_gap_bonus": 8.0,
        "bonus_penalty_sum": 999.0,        # agregat — musi być pominięty
        "bonus_r4_raw": 80.0,              # *_raw — pominięty
        "bonus_r1_progressive_shadow_delta": -50.0,  # shadow — pominięty
        "best_effort": False,              # bool — pominięty
    }
    if components:
        best.update(components)
    return {
        "ts": _iso(ts),
        "order_id": oid,
        "action": "PANEL_OVERRIDE",
        "proposed_courier_id": "413",
        "actual_courier_id": "123",
        "panel_source": "panel_diff",
        "decision": {
            "order_id": oid,
            "verdict": "PROPOSE",
            "restaurant": "Testownia",
            "pickup_ready_at": _iso(created + timedelta(minutes=prep_min)),
            "order_created_at": _iso(created),
            "best": best,
        },
    }


def _write_log(tmp_path, recs):
    p = tmp_path / "learning_log.jsonl"
    p.write_text("\n".join(json.dumps(r, ensure_ascii=False) for r in recs) + "\n")
    return str(p)


def test_acceptance_line_basic():
    lc = Counter({"PANEL_AGREE": 3, "PANEL_OVERRIDE": 1})
    line = db._acceptance_line(lc)
    assert "3/4" in line and "75.0%" in line and "OVERRIDE: 1" in line


def test_acceptance_line_none_when_empty():
    assert db._acceptance_line(Counter({"TAK": 5})) is None


def test_dims_from_agree_record():
    r = _agree("1", tier="gold", prep_min=20.0, peak=True)
    tier, pora, typ = db._accept_rec_dims(r)
    assert (tier, pora, typ) == ("gold", "peak", "elastyk")


def test_dims_from_override_record_czasowka_offpeak():
    r = _override("2", tier="std", prep_min=90.0, peak=False)
    tier, pora, typ = db._accept_rec_dims(r)
    assert (tier, pora, typ) == ("std", "off", "czasówka")


def test_dims_tier_fallback_bug4():
    r = _override("3")
    del r["decision"]["best"]["dwell_tier"]
    r["decision"]["best"]["v319h_bug4_tier_cap_used"] = "slow/peak/2"
    assert db._accept_rec_dims(r)[0] == "slow"


def test_top_override_components_skips_aggregates():
    recs = [_override("4"), _override("5")]
    top = db._top_override_components(recs)
    keys = [k for k, _, _ in top]
    assert "bonus_r4" in keys                       # dominujący
    assert "bonus_penalty_sum" not in keys          # agregat
    assert "bonus_r4_raw" not in keys               # raw
    assert "bonus_r1_progressive_shadow_delta" not in keys  # shadow
    # ranking po |śr| — bonus_r4 (40.0) pierwszy
    assert keys[0] == "bonus_r4"
    assert top[0][1] == pytest.approx(40.0)
    assert top[0][2] == 2


def test_breakdown_lines_full(tmp_path):
    recs = [
        _agree("10", tier="gold", peak=True),
        _agree("11", tier="gold", peak=False),
        _agree("12", tier="std", peak=True),
        _override("13", tier="std", peak=True),
    ]
    path = _write_log(tmp_path, recs)
    start = NOW - timedelta(days=7)
    end = NOW + timedelta(days=1)
    lines = db._acceptance_breakdown_lines(path, start, end)
    text = "\n".join(lines)
    assert "Acceptance 7d" in text
    assert "3/4 = 75.0%" in text
    assert "gold 100% (2/2)" in text
    assert "std 50% (1/2)" in text
    assert "czasówka" in text and "elastyk" in text
    assert "bonus_r4" in text


def test_breakdown_empty_when_no_records(tmp_path):
    path = _write_log(tmp_path, [{"ts": _iso(NOW), "action": "TAK", "order_id": "1"}])
    lines = db._acceptance_breakdown_lines(
        path, NOW - timedelta(days=7), NOW + timedelta(days=1))
    assert lines == []


def test_breakdown_respects_time_window(tmp_path):
    old = dict(_agree("20"))
    old["ts"] = _iso(NOW - timedelta(days=30))
    path = _write_log(tmp_path, [old])
    lines = db._acceptance_breakdown_lines(
        path, NOW - timedelta(days=7), NOW + timedelta(days=1))
    assert lines == []
