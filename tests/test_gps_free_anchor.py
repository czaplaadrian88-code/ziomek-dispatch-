"""GPS-free anchor dla plan_recheck — flota z założenia bez GPS.

Świeży GPS ma pierwszeństwo; brak/stale GPS → kotwica zdarzeniowa (ostatni
przystanek) lub committed najbliższego odbioru. Flaga OFF = zachowanie sprzed.
"""
from datetime import datetime, timedelta, timezone

from dispatch_v2 import plan_recheck as PR

NOW = datetime(2026, 6, 7, 14, 0, 0, tzinfo=timezone.utc)  # fixed, deterministyczne


def _gps(ts_min_ago, lat=53.13, lon=23.16):
    return {"lat": lat, "lon": lon,
            "timestamp": (NOW - timedelta(minutes=ts_min_ago)).isoformat()}


def _set_flag(on):
    PR.ENABLE_GPS_FREE_ANCHOR = on


# ---- pierwszeństwo świeżego GPS ----

def test_fresh_gps_wins():
    _set_flag(True)
    a = PR._start_anchor("9", [], {}, {"9": _gps(2)}, NOW)
    assert a is not None and a[2] == "gps_pwa" and a[1] is None


def test_stale_gps_not_used_when_event_exists():
    _set_flag(True)
    orders = {"o1": {"courier_id": "9", "status": "picked_up",
                     "pickup_coords": [53.14, 23.15],
                     "history": [{"event": "COURIER_PICKED_UP",
                                  "at": (NOW - timedelta(minutes=20)).isoformat()}]}}
    a = PR._start_anchor("9", ["o1"], orders, {"9": _gps(5000)}, NOW)
    assert a[2] == "last_event"
    assert a[0] == (53.14, 23.15)


def test_flag_off_keeps_gps_only():
    _set_flag(False)
    # stary GPS + zdarzenia — flaga OFF i tak bierze GPS (zachowanie sprzed)
    orders = {"o1": {"courier_id": "9", "status": "picked_up",
                     "pickup_coords": [53.14, 23.15],
                     "history": [{"event": "COURIER_PICKED_UP",
                                  "at": (NOW - timedelta(minutes=20)).isoformat()}]}}
    a = PR._start_anchor("9", ["o1"], orders, {"9": _gps(5000)}, NOW)
    assert a[2] == "gps_pwa"
    _set_flag(True)


def test_no_gps_no_event_no_committed_returns_none():
    _set_flag(True)
    a = PR._start_anchor("9", [], {}, {}, NOW)
    assert a is None


# ---- kotwica zdarzeniowa ----

def test_last_event_picks_most_recent():
    orders = {
        "old": {"courier_id": "9", "status": "delivered",
                "delivery_coords": [53.10, 23.10],
                "history": [{"event": "COURIER_DELIVERED",
                             "at": (NOW - timedelta(minutes=40)).isoformat()}]},
        "new": {"courier_id": "9", "status": "delivered",
                "delivery_coords": [53.20, 23.20],
                "history": [{"event": "COURIER_DELIVERED",
                             "at": (NOW - timedelta(minutes=5)).isoformat()}]},
    }
    pos, at = PR._last_event_anchor("9", orders, NOW)
    assert pos == (53.20, 23.20)  # najświeższe wygrywa


def test_last_event_ignores_stale_over_6h():
    orders = {"o": {"courier_id": "9", "status": "delivered",
                    "delivery_coords": [53.10, 23.10],
                    "history": [{"event": "COURIER_DELIVERED",
                                 "at": (NOW - timedelta(hours=7)).isoformat()}]}}
    assert PR._last_event_anchor("9", orders, NOW) is None


def test_delivered_without_coords_falls_back_to_pickup():
    orders = {"o": {"courier_id": "9", "status": "delivered",
                    "delivery_coords": None, "pickup_coords": [53.15, 23.15],
                    "history": [{"event": "COURIER_DELIVERED",
                                 "at": (NOW - timedelta(minutes=10)).isoformat()}]}}
    pos, _ = PR._last_event_anchor("9", orders, NOW)
    assert pos == (53.15, 23.15)


def test_other_courier_events_ignored():
    orders = {"o": {"courier_id": "8", "status": "delivered",
                    "delivery_coords": [53.10, 23.10],
                    "history": [{"event": "COURIER_DELIVERED",
                                 "at": (NOW - timedelta(minutes=5)).isoformat()}]}}
    assert PR._last_event_anchor("9", orders, NOW) is None


# ---- kotwica committed (nic nieodebrane) ----

def test_committed_pickup_anchor_when_no_events():
    _set_flag(True)
    orders = {
        "a": {"courier_id": "9", "status": "assigned", "pickup_coords": [53.13, 23.16],
              "czas_kuriera_warsaw": "2026-06-07T16:35:00+02:00"},
        "b": {"courier_id": "9", "status": "assigned", "pickup_coords": [53.12, 23.12],
              "czas_kuriera_warsaw": "2026-06-07T16:30:00+02:00"},
    }
    a = PR._start_anchor("9", ["a", "b"], orders, {}, NOW)
    assert a[2] == "committed_pickup"
    assert a[0] == (53.12, 23.12)          # najbliższy committed (16:30) wygrywa
    assert a[1] is not None                # earliest_departure ustawione
