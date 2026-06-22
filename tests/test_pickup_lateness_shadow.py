"""Testy detektora pickup_lateness_shadow.detect_late_pickups (czysta funkcja).

Wzór: tests/test_canon_order_invariants.py — fake orders_state + plans, brak I/O.
Scenariusz bazowy = realny case 2026-06-22 (Grzegorz Rogowski cid=500 /
Piwo Kaczka Sushi 482692, umówiony odbiór 20:36, prognoza dojazdu 20:52)."""
from datetime import datetime, timezone

from dispatch_v2 import pickup_lateness_shadow as M


def _utc(iso):
    return datetime.fromisoformat(iso).astimezone(timezone.utc)


# umówiony odbiór 20:36 Warsaw = 18:36 UTC; prognoza dojazdu 20:52 = 18:52 UTC
COMMITTED = "2026-06-22T20:36:00+02:00"
PREDICTED_LATE = "2026-06-22T18:52:00+00:00"      # +16 min late
PREDICTED_ONTIME = "2026-06-22T18:34:00+00:00"    # 2 min wcześniej

ORDERS = {
    "482692": {"status": "assigned", "restaurant": "Piwo Kaczka Sushi",
               "pickup_address": "Węglowa 1 (MUZEUM)",
               "czas_kuriera_warsaw": COMMITTED},
}


def _plan(predicted_iso, otype="pickup", oid="482692"):
    return {"500": {"stops": [
        {"order_id": oid, "type": otype, "predicted_at": predicted_iso},
    ]}}


def test_late_pickup_with_lead_fires():
    # asof 20:10 Warsaw → do odbioru 26 min (>=20), late 16 min (>=5) → FIRE
    ev = M.detect_late_pickups(ORDERS, _plan(PREDICTED_LATE), _utc("2026-06-22T20:10:00+02:00"))
    assert len(ev) == 1
    e = ev[0]
    assert e["cid"] == "500" and e["order_id"] == "482692"
    assert e["restaurant"] == "Piwo Kaczka Sushi"
    assert e["lateness_min"] == 16.0
    assert e["lead_min"] == 26.0
    assert e["committed_warsaw_hhmm"] == "20:36"
    assert e["predicted_warsaw_hhmm"] == "20:52"
    assert e["suggested_pickup_warsaw_hhmm"] == "20:52"
    assert e["is_alarm"] is True  # lead 26 >= 15 → alarm też


def test_close_lead_logged_as_badge_not_alarm():
    # asof 20:25 Warsaw → do odbioru 11 min (<15): badge SIĘ loguje (bez progu lead),
    # ale is_alarm=False (alarm/modal by NIE poszedł).
    ev = M.detect_late_pickups(ORDERS, _plan(PREDICTED_LATE), _utc("2026-06-22T20:25:00+02:00"))
    assert len(ev) == 1
    assert ev[0]["lead_min"] == 11.0
    assert ev[0]["is_alarm"] is False


def test_on_time_pickup_suppressed():
    ev = M.detect_late_pickups(ORDERS, _plan(PREDICTED_ONTIME), _utc("2026-06-22T20:10:00+02:00"))
    assert ev == []


def test_picked_up_skipped():
    o = {"482692": dict(ORDERS["482692"], status="picked_up")}
    ev = M.detect_late_pickups(o, _plan(PREDICTED_LATE), _utc("2026-06-22T20:10:00+02:00"))
    assert ev == []


def test_no_committed_time_skipped():
    o = {"482692": {"status": "assigned", "restaurant": "X"}}  # elastyk, brak czas_kuriera
    ev = M.detect_late_pickups(o, _plan(PREDICTED_LATE), _utc("2026-06-22T20:10:00+02:00"))
    assert ev == []


def test_dropoff_stop_ignored():
    ev = M.detect_late_pickups(ORDERS, _plan(PREDICTED_LATE, otype="dropoff"),
                               _utc("2026-06-22T20:10:00+02:00"))
    assert ev == []


def test_lateness_below_threshold_suppressed():
    # prognoza 20:39 = +3 min (<5) → brak
    ev = M.detect_late_pickups(ORDERS, _plan("2026-06-22T18:39:00+00:00"),
                               _utc("2026-06-22T20:10:00+02:00"))
    assert ev == []


def test_invalidated_plan_skipped():
    p = _plan(PREDICTED_LATE)
    p["500"]["invalidated_at"] = "2026-06-22T18:00:00+00:00"
    ev = M.detect_late_pickups(ORDERS, p, _utc("2026-06-22T20:10:00+02:00"))
    assert ev == []


def test_threshold_boundary_exactly_5_fires():
    # prognoza 20:41 = +5.0 min (== próg) → FIRE (>=)
    ev = M.detect_late_pickups(ORDERS, _plan("2026-06-22T18:41:00+00:00"),
                               _utc("2026-06-22T20:10:00+02:00"))
    assert len(ev) == 1 and ev[0]["lateness_min"] == 5.0
