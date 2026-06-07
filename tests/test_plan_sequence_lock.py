"""F2 — sekwencja zamrożona, tick tylko re-czasuje.

bag_signature decyduje: ten sam worek (skład + picked_up) → re-czasowanie
(kolejność STAŁA); zmiana → re-decyzja. _retime_one_bag_plan zachowuje kolejność
stopów, odświeża predicted_at, clampuje committed na odbiorach.
"""
from datetime import datetime, timezone

from dispatch_v2 import plan_recheck as PR


# ---- bag_signature ----

def test_signature_sorted_and_encodes_picked_up():
    os_ = {"a": {"status": "assigned"}, "b": {"status": "picked_up"}}
    assert PR._bag_signature(["b", "a"], os_) == "a:0|b:1"


def test_signature_changes_on_pickup():
    before = PR._bag_signature(["a"], {"a": {"status": "assigned"}})
    after = PR._bag_signature(["a"], {"a": {"status": "picked_up"}})
    assert before != after  # odbiór = zmiana worka = re-decyzja


def test_signature_changes_on_membership():
    s1 = PR._bag_signature(["a"], {"a": {"status": "assigned"}})
    s2 = PR._bag_signature(["a", "b"], {"a": {"status": "assigned"}, "b": {"status": "assigned"}})
    assert s1 != s2


# ---- _retime_one_bag_plan ----

NOW = datetime(2026, 6, 7, 12, 0, 0, tzinfo=timezone.utc)


def _plan(stops, sig="sig"):
    return {"stops": stops, "optimization_method": "incremental", "bag_signature": sig}


def _orders():
    # dwa odbiory + dwie dostawy, wszystkie z coords
    return {
        "o1": {"status": "picked_up", "pickup_coords": [53.10, 23.10],
               "delivery_coords": [53.11, 23.11]},
        "o2": {"status": "assigned", "pickup_coords": [53.12, 23.12],
               "delivery_coords": [53.13, 23.13],
               "czas_kuriera_warsaw": "2026-06-07T16:00:00+02:00"},
    }


def _mock_osrm(monkeypatch, leg_s=300.0):
    import dispatch_v2.osrm_client as oc

    def table(origins, destinations):
        n = len(origins)
        return [[{"duration_s": leg_s} for _ in range(n)] for _ in range(n)]
    monkeypatch.setattr(oc, "table", table)


def test_retime_preserves_order_and_sets_times(monkeypatch):
    monkeypatch.setattr(PR, "_start_anchor", lambda *a, **k: ((53.1, 23.1), None, "gps_pwa"))
    _mock_osrm(monkeypatch)
    saved = {}
    monkeypatch.setattr(PR.plan_manager, "save_plan", lambda cid, body: saved.update(body))
    stops = [
        {"order_id": "o1", "type": "dropoff", "coords": {"lat": 0, "lng": 0}, "dwell_min": 3.5},
        {"order_id": "o2", "type": "pickup", "coords": {"lat": 0, "lng": 0}, "dwell_min": 1.0},
        {"order_id": "o2", "type": "dropoff", "coords": {"lat": 0, "lng": 0}, "dwell_min": 3.5},
    ]
    ok = PR._retime_one_bag_plan("9", _plan(stops), ["o1", "o2"], _orders(), {}, NOW)
    assert ok is True
    # kolejność STAŁA
    assert [(s["order_id"], s["type"]) for s in saved["stops"]] == \
        [("o1", "dropoff"), ("o2", "pickup"), ("o2", "dropoff")]
    # czasy ustawione + retimed_at
    assert all(s["predicted_at"] for s in saved["stops"])
    assert saved["retimed_at"] is not None
    assert saved["bag_signature"] == "sig"  # zachowana


def test_retime_clamps_committed_pickup(monkeypatch):
    monkeypatch.setattr(PR, "_start_anchor", lambda *a, **k: ((53.1, 23.1), None, "gps_pwa"))
    _mock_osrm(monkeypatch, leg_s=60.0)  # 1 min legi — bez clampu byłoby ~12:0x
    saved = {}
    monkeypatch.setattr(PR.plan_manager, "save_plan", lambda cid, body: saved.update(body))
    stops = [{"order_id": "o2", "type": "pickup", "coords": {"lat": 0, "lng": 0}, "dwell_min": 1.0}]
    PR._retime_one_bag_plan("9", _plan(stops), ["o2"], _orders(), {}, NOW)
    # committed 16:00 Warsaw = 14:00 UTC → predicted clampnięte do 14:00, nie ~12:01
    assert saved["stops"][0]["predicted_at"].startswith("2026-06-07T14:00")


def test_retime_false_without_coords(monkeypatch):
    monkeypatch.setattr(PR, "_start_anchor", lambda *a, **k: ((53.1, 23.1), None, "gps_pwa"))
    _mock_osrm(monkeypatch)
    os_ = {"o1": {"status": "picked_up", "delivery_coords": None}}
    stops = [{"order_id": "o1", "type": "dropoff", "coords": {"lat": 0, "lng": 0}}]
    assert PR._retime_one_bag_plan("9", _plan(stops), ["o1"], os_, {}, NOW) is False


def test_retime_false_without_anchor(monkeypatch):
    monkeypatch.setattr(PR, "_start_anchor", lambda *a, **k: None)
    stops = [{"order_id": "o1", "type": "dropoff", "coords": {"lat": 0, "lng": 0}}]
    assert PR._retime_one_bag_plan("9", _plan(stops), ["o1"], _orders(), {}, NOW) is False
