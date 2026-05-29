"""Test SHADOW probe race Baanko-type (same-restaurant co-arrival).

Probe jest logging-only — testujemy klasyfikację orphan vs visible-not-proposed
oraz że nie rzuca i respektuje flagę. Zero wpływu na decyzję.
"""
import sys
import types
import logging
from datetime import datetime, timezone, timedelta

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from dispatch_v2 import shadow_dispatcher as SD


def _now_iso(delta_s=0):
    return (datetime.now(timezone.utc) - timedelta(seconds=delta_s)).isoformat()


def _courier(cid, bag_oids, pos_source="gps"):
    return types.SimpleNamespace(
        courier_id=cid,
        pos_source=pos_source,
        bag=[{"order_id": o} for o in bag_oids],
    )


def _result(restaurant, best_cid):
    best = types.SimpleNamespace(courier_id=best_cid, metrics={}) if best_cid else None
    return types.SimpleNamespace(restaurant=restaurant, best=best)


def _grab(caplog):
    return [r.getMessage() for r in caplog.records if "SAME_REST_RACE_PROBE" in r.getMessage()]


def test_orphan_sibling_not_in_bag(caplog):
    """Sibling assigned do C, ale NIE w bagu C (courier_id None) → orphan=True."""
    state = {
        "476815": {"restaurant": "Baanko", "status": "assigned",
                   "courier_id": None, "first_seen": _now_iso(10),
                   "assigned_at": _now_iso(2)},
    }
    fleet = {"441": _courier("441", [])}  # Sylwia pusta — sibling nie podlinkowany
    res = _result("Baanko", "484")        # proponowany Andrei K
    with caplog.at_level(logging.INFO, logger="shadow_dispatcher"):
        SD._probe_same_restaurant_race("476816", res, fleet, state)
    msgs = _grab(caplog)
    assert msgs, "probe powinien zalogować przy sibling co-arrival"
    assert "orphan=True" in msgs[0]


def test_visible_but_not_proposed(caplog):
    """Sibling w bagu C, C w puli, ale best != C → visible_not_proposed=True."""
    state = {
        "476815": {"restaurant": "Baanko", "status": "assigned",
                   "courier_id": "441", "first_seen": _now_iso(15),
                   "assigned_at": _now_iso(5)},
    }
    fleet = {"441": _courier("441", ["476815"], pos_source="pre_shift"),
             "484": _courier("484", ["a", "b", "c"])}
    res = _result("Baanko", "484")
    with caplog.at_level(logging.INFO, logger="shadow_dispatcher"):
        SD._probe_same_restaurant_race("476816", res, fleet, state)
    msgs = _grab(caplog)
    assert msgs
    assert "visible_not_proposed=True" in msgs[0]
    assert "orphan=False" in msgs[0]


def test_no_sibling_no_log(caplog):
    """Brak same-restaurant siblinga → cisza."""
    state = {"999": {"restaurant": "Inna Pizza", "status": "assigned",
                     "courier_id": "441", "first_seen": _now_iso(5)}}
    fleet = {"441": _courier("441", ["999"])}
    res = _result("Baanko", "484")
    with caplog.at_level(logging.INFO, logger="shadow_dispatcher"):
        SD._probe_same_restaurant_race("476816", res, fleet, state)
    assert not _grab(caplog)


def test_stale_sibling_skipped(caplog):
    """Sibling z R ale sprzed >120s → nie liczony (nie race)."""
    state = {"476000": {"restaurant": "Baanko", "status": "assigned",
                        "courier_id": "441", "first_seen": _now_iso(600),
                        "assigned_at": _now_iso(600)}}
    fleet = {"441": _courier("441", ["476000"])}
    res = _result("Baanko", "484")
    with caplog.at_level(logging.INFO, logger="shadow_dispatcher"):
        SD._probe_same_restaurant_race("476816", res, fleet, state)
    assert not _grab(caplog)


def test_flag_off_noop(caplog, monkeypatch):
    """Flaga off → probe nic nie loguje."""
    monkeypatch.setattr(SD.C, "flag", lambda name, default=False:
                        False if name == "ENABLE_SAME_RESTAURANT_RACE_PROBE" else default)
    state = {"476815": {"restaurant": "Baanko", "status": "assigned",
                        "courier_id": None, "first_seen": _now_iso(5)}}
    fleet = {"441": _courier("441", [])}
    res = _result("Baanko", "484")
    with caplog.at_level(logging.INFO, logger="shadow_dispatcher"):
        SD._probe_same_restaurant_race("476816", res, fleet, state)
    assert not _grab(caplog)


def test_never_raises_on_garbage(caplog):
    """Defensywność: śmieciowy input → brak wyjątku (try/except wewnątrz)."""
    SD._probe_same_restaurant_race("476816", _result(None, None), {}, {})
    SD._probe_same_restaurant_race(None, _result("Baanko", "1"), None, {"x": "notadict"})
    # brak raise = pass
