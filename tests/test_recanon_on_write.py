"""Testy RECANON-ON-WRITE (`plan_recheck.recanon_courier`).

Adrian 2026-06-23 („od podstaw nie łatać"): niezmienniki kanonu (carried-first
floor + odbiory wg committed + relax) były doklejane WYŁĄCZNIE przez 5-min tick.
Zapisy zdarzeniowe (odbiór/dostawa/przydział z panel_watcher) pisały plan BEZ tej
warstwy → niesione nie na froncie aż do ticku (case Piotr/Grzesiek 23.06).
`recanon_courier` re-egzekwuje kanon na ISTNIEJĄCYM planie natychmiast po zdarzeniu,
bez re-TSP (przez `_retime_one_bag_plan`). Self-gating + idempotentny.

OSRM zamockowany (haversine ~30 km/h), ścieżki plików izolowane do tmp — bez
zależności od żywego serwera/stanu produkcyjnego.
"""
import json
import math
import pathlib
from datetime import datetime, timezone

import pytest

from dispatch_v2 import plan_recheck as P
from dispatch_v2 import plan_manager as PM
from dispatch_v2 import osrm_client


def _hav_m(a, b):
    R = 6371000.0
    la1, lo1, la2, lo2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    dla, dlo = la2 - la1, lo2 - lo1
    h = math.sin(dla / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def _fake_table(pts_a, pts_b):
    return [[{"duration_s": _hav_m(a, b) / 8.333} for b in pts_b] for a in pts_a]


CID = "9999"
NOW = datetime(2026, 6, 23, 14, 49, 0, tzinfo=timezone.utc)

# C = niesione 53 min temu, daleki odbiór N committed za >2h → carried-first (dowóz pierwszy)
ORDERS = {
    "C": {"courier_id": CID, "status": "picked_up",
          "picked_up_at": "2026-06-23 13:56:00", "czas_kuriera_warsaw": "2026-06-23T13:51:00+02:00",
          "pickup_coords": [53.128, 23.155], "delivery_coords": [53.1375, 23.158], "restaurant": "Pruszynka"},
    "N": {"courier_id": CID, "status": "assigned",
          "czas_kuriera_warsaw": "2026-06-23T17:02:00+02:00",
          "pickup_coords": [53.131, 23.150], "delivery_coords": [53.120, 23.170], "restaurant": "Toriko"},
}

# Plan z BŁĘDEM (jak zapis zdarzeniowy): odbiór N na #1, niesione D_C w środku.
BAD_STOPS = [
    {"order_id": "N", "type": "pickup", "coords": {"lat": 53.131, "lng": 23.150},
     "predicted_at": "2026-06-23T13:03:00+00:00", "dwell_min": 1.0, "status_at_plan_time": "assigned"},
    {"order_id": "C", "type": "dropoff", "coords": {"lat": 53.1375, "lng": 23.158},
     "predicted_at": "2026-06-23T13:14:00+00:00", "dwell_min": 3.5, "status_at_plan_time": "picked_up"},
    {"order_id": "N", "type": "dropoff", "coords": {"lat": 53.120, "lng": 23.170},
     "predicted_at": "2026-06-23T13:35:00+00:00", "dwell_min": 3.5, "status_at_plan_time": "assigned"},
]


@pytest.fixture
def env(tmp_path, monkeypatch):
    ordp = tmp_path / "orders_state.json"
    ordp.write_text(json.dumps(ORDERS))
    monkeypatch.setattr(P, "ORDERS_STATE_PATH", str(ordp))
    monkeypatch.setattr(PM, "PLANS_FILE", pathlib.Path(tmp_path / "courier_plans.json"))
    monkeypatch.setattr(PM, "LOCK_FILE", pathlib.Path(tmp_path / "courier_plans.lock"))
    monkeypatch.setattr(osrm_client, "table", _fake_table)
    monkeypatch.setattr(P, "_load_gps_positions", lambda: {})
    monkeypatch.setattr(P, "ENABLE_RECANON_ON_WRITE", True)
    monkeypatch.setattr(P, "ENABLE_PLAN_CANON_ORDER_INVARIANTS", True)
    monkeypatch.setattr(P, "ENABLE_CARRIED_FIRST_RELAX", True)
    monkeypatch.setattr(P, "ENABLE_GPS_FREE_ANCHOR", True)
    return tmp_path


def _save_bad():
    PM.save_plan(CID, {"start_pos": {"lat": 53.131, "lng": 23.150, "source": "x"},
                       "start_ts": NOW.isoformat(), "stops": [dict(s) for s in BAD_STOPS],
                       "optimization_method": "incremental", "bag_signature": "C:1|N:0"})


def _order():
    return [(s["type"], s["order_id"]) for s in PM.load_plan(CID)["stops"]]


def test_recanon_floors_carried_at_event_time(env):
    _save_bad()
    assert _order()[0] == ("pickup", "N")              # przed: niesione NIE na froncie
    assert P.recanon_courier(CID, now=NOW, reason="pickup") is True
    assert _order()[0] == ("dropoff", "C")             # po: niesione (53 min, daleki odbiór) na #1


def test_flag_off_is_noop(env, monkeypatch):
    monkeypatch.setattr(P, "ENABLE_RECANON_ON_WRITE", False)
    _save_bad()
    v0 = PM.load_plan(CID)["plan_version"]
    assert P.recanon_courier(CID, now=NOW) is False
    assert PM.load_plan(CID)["plan_version"] == v0     # zero zapisu
    assert _order()[0] == ("pickup", "N")              # niezmienione


def test_recanon_idempotent(env):
    _save_bad()
    P.recanon_courier(CID, now=NOW)
    first = _order()
    P.recanon_courier(CID, now=NOW)
    assert _order() == first                            # brak oscylacji


def test_no_plan_returns_false(env):
    assert P.recanon_courier(CID, now=NOW) is False     # brak planu → decyzja należy do _gen/ticku


def test_partial_coverage_is_noop(env):
    # plan pokrywa tylko C; worek ma C+N → recanon NIE rusza (świeży przydział = tick/gen)
    PM.save_plan(CID, {"start_pos": {"lat": 53.131, "lng": 23.150, "source": "x"},
                       "start_ts": NOW.isoformat(),
                       "stops": [{"order_id": "C", "type": "dropoff",
                                  "coords": {"lat": 53.1375, "lng": 23.158}, "dwell_min": 3.5,
                                  "status_at_plan_time": "picked_up"}],
                       "optimization_method": "incremental", "bag_signature": "C:1"})
    v0 = PM.load_plan(CID)["plan_version"]
    assert P.recanon_courier(CID, now=NOW) is False
    assert PM.load_plan(CID)["plan_version"] == v0
