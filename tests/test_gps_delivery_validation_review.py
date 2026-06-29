"""Test #5b linijki dostawy — gps_delivery_validation_review.

Pokrywa: znak delty (klik PO fizycznym = dodatnia), klasyfikację pewności
(n_in>=2 + w promieniu = high), budowę gps_delivery_truth.jsonl, fail-soft
gdy brak buttona. Read-only tool — test integracyjny przez temp-pliki.
"""
import json
import sys
from pathlib import Path

from dispatch_v2.tools import gps_delivery_validation_review as R


def test_to_epoch_iso_and_naive_warsaw():
    # ISO z offsetem
    e1 = R.to_epoch("2026-06-10T12:13:55+00:00")
    # naive Warsaw (ten sam moment to 14:13:55 Warsaw = 12:13:55 UTC latem)
    e2 = R.to_epoch("2026-06-10 14:13:55")
    assert e1 is not None and e2 is not None
    assert abs(e1 - e2) < 1.0  # ten sam moment fizyczny
    assert R.to_epoch(None) is None
    assert R.to_epoch("") is None


def _run(tmp_path, dwell, state, since=None):
    cd = tmp_path / "customer_dwell.json"
    os_ = tmp_path / "orders_state.json"
    truth = tmp_path / "gps_delivery_truth.jsonl"
    verdict = tmp_path / "verdict.txt"
    cd.write_text(json.dumps(dwell), encoding="utf-8")
    os_.write_text(json.dumps(state), encoding="utf-8")
    argv = ["prog", "--customer-dwell", str(cd), "--orders-state", str(os_),
            "--out-truth", str(truth), "--out-verdict", str(verdict), "--write"]
    if since:
        argv += ["--since", since]
    old = sys.argv
    sys.argv = argv
    try:
        rc = R.main()
    finally:
        sys.argv = old
    assert rc == 0
    rows = [json.loads(l) for l in truth.read_text(encoding="utf-8").splitlines() if l.strip()]
    return rows


def test_delta_sign_and_confidence(tmp_path):
    # klik (delivered_at) 2 min PO fizycznym przyjeździe → delta dodatnia
    dwell = {
        "479644": {  # high conf: n_in=3, w promieniu
            "_source": "gps_geofence", "courier_id": "400",
            "arrived_at_customer": "2026-06-10 14:13:55",  # Warsaw
            "delivery_address": "Test 1", "_n_in_geofence": 3, "_min_dist_m": 8,
            "_radius_m": 80.0, "dwell_min": 4.0, "delivered_day": "2026-06-10",
        },
        "479999": {  # low conf: n_in=1
            "_source": "gps_geofence", "courier_id": "401",
            "arrived_at_customer": "2026-06-10 15:00:00",
            "_n_in_geofence": 1, "_min_dist_m": 70, "_radius_m": 80.0,
            "delivered_day": "2026-06-10",
        },
        "skip_nofix": {"_source": "gps_no_fix", "courier_id": "402"},
    }
    state = {
        "479644": {"delivered_at": "2026-06-10 14:15:55"},  # +2 min
        "479999": {"delivered_at": "2026-06-10 15:00:30"},  # +0.5 min
    }
    rows = _run(tmp_path, dwell, state)
    by = {r["order_id"]: r for r in rows}
    assert "skip_nofix" not in by  # nie-geofence pominięte
    assert by["479644"]["delta_button_minus_physical_min"] == 2.0
    assert by["479644"]["confidence"] == "high"
    assert by["479999"]["confidence"] == "low"  # n_in=1
    assert by["479999"]["delta_button_minus_physical_min"] == 0.5


def test_fail_soft_no_button(tmp_path):
    # brak delivered_at → delta None, ale wiersz fizyczny nadal zapisany
    dwell = {
        "500001": {"_source": "gps_geofence", "courier_id": "400",
                   "arrived_at_customer": "2026-06-10 14:13:55",
                   "_n_in_geofence": 2, "_min_dist_m": 10, "_radius_m": 80.0,
                   "delivered_day": "2026-06-10"},
    }
    rows = _run(tmp_path, dwell, {})  # pusty orders_state
    assert len(rows) == 1
    assert rows[0]["delta_button_minus_physical_min"] is None
    assert rows[0]["physical_delivered_at"] is not None


def test_since_window_filter(tmp_path):
    dwell = {
        "old1": {"_source": "gps_geofence", "courier_id": "400",
                 "arrived_at_customer": "2026-06-01 14:00:00",
                 "_n_in_geofence": 2, "_min_dist_m": 10, "_radius_m": 80.0,
                 "delivered_day": "2026-06-01"},
        "new1": {"_source": "gps_geofence", "courier_id": "400",
                 "arrived_at_customer": "2026-06-28 14:00:00",
                 "_n_in_geofence": 2, "_min_dist_m": 10, "_radius_m": 80.0,
                 "delivered_day": "2026-06-28"},
    }
    rows = _run(tmp_path, dwell, {}, since="2026-06-15")
    ids = {r["order_id"] for r in rows}
    assert ids == {"new1"}  # old1 odfiltrowane przez --since
