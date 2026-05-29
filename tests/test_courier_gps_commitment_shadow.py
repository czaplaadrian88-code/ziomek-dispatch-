"""Faza 2b SHADOW — testy reconcile (fakt GPS vs commitment Ziomka).

Czysta funkcja, bez I/O — nie dotyka orders_state.json ani plików.
"""
import os
import sys
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import courier_gps_commitment_shadow as shadow

GT_PICK = 1_716_984_000          # epoch odbioru wg GPS
NOW = GT_PICK + 600              # 10 min później


def _state_ts(epoch):
    """String w stylu orders_state (Warsaw naive)."""
    return datetime.fromtimestamp(epoch, shadow.WARSAW).strftime("%Y-%m-%d %H:%M:%S")


def _types(recs):
    return [r["divergence_type"] for r in recs]


def test_pickup_ahead_when_state_still_assigned():
    gt = {"100": {"courier_id": "400", "picked_up_at": GT_PICK, "last_status_code": 5}}
    state = {"100": {"courier_id": "400", "commitment_level": "assigned", "status": "assigned"}}
    recs = shadow.reconcile(gt, state, NOW)
    assert _types(recs) == ["GPS_PICKUP_AHEAD"]
    assert recs[0]["would_apply"] is True
    assert recs[0]["gps_ahead_sec"] == 600


def test_in_sync_pickup_no_record():
    gt = {"100": {"courier_id": "400", "picked_up_at": GT_PICK, "last_status_code": 5}}
    state = {"100": {"courier_id": "400", "commitment_level": "picked_up",
                     "status": "picked_up", "picked_up_at": _state_ts(GT_PICK + 30)}}
    assert shadow.reconcile(gt, state, NOW) == []


def test_pickup_timing_divergence():
    gt = {"100": {"courier_id": "400", "picked_up_at": GT_PICK, "last_status_code": 5}}
    state = {"100": {"courier_id": "400", "commitment_level": "picked_up",
                     "status": "picked_up", "picked_up_at": _state_ts(GT_PICK + 300)}}
    recs = shadow.reconcile(gt, state, NOW)
    assert _types(recs) == ["GPS_PICKUP_TIMING"]
    assert recs[0]["timing_delta_sec"] == 300
    assert recs[0]["would_apply"] is False


def test_delivered_ahead():
    gt = {"100": {"courier_id": "400", "picked_up_at": GT_PICK,
                  "delivered_at": GT_PICK + 400, "last_status_code": 7}}
    state = {"100": {"courier_id": "400", "commitment_level": "picked_up", "status": "picked_up"}}
    recs = shadow.reconcile(gt, state, NOW)
    assert _types(recs) == ["GPS_DELIVERED_AHEAD"]
    assert recs[0]["would_apply"] is True


def test_courier_mismatch():
    gt = {"100": {"courier_id": "999", "picked_up_at": GT_PICK, "last_status_code": 5}}
    state = {"100": {"courier_id": "400", "commitment_level": "assigned", "status": "assigned"}}
    recs = shadow.reconcile(gt, state, NOW)
    assert _types(recs) == ["COURIER_MISMATCH"]
    assert recs[0]["would_apply"] is False


def test_orphan_when_order_missing():
    gt = {"100": {"courier_id": "400", "picked_up_at": GT_PICK, "last_status_code": 5}}
    recs = shadow.reconcile(gt, {}, NOW)
    assert _types(recs) == ["GPS_ORPHAN"]


def test_only_dojazd_skipped():
    # status 3 (dojazd) / 4 (odbior) bez picked_up_at/delivered_at → brak twardego faktu
    gt = {"100": {"courier_id": "400", "last_status_code": 3}}
    state = {"100": {"courier_id": "400", "commitment_level": "assigned", "status": "assigned"}}
    assert shadow.reconcile(gt, state, NOW) == []


def test_flag_enabled_passthrough():
    gt = {"100": {"courier_id": "400", "picked_up_at": GT_PICK}}
    state = {"100": {"courier_id": "400", "commitment_level": "assigned", "status": "assigned"}}
    recs = shadow.reconcile(gt, state, NOW, flag_enabled=True)
    assert recs[0]["flag_enabled"] is True


def test_parse_state_ts_naive_and_iso():
    assert shadow._parse_state_ts("2026-05-29 14:16:48") is not None
    assert shadow._parse_state_ts("2026-05-29T14:16:48+02:00") is not None
    assert shadow._parse_state_ts("") is None
    assert shadow._parse_state_ts(None) is None
