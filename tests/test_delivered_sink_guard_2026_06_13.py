"""Regression dla FIX 2026-06-13 (sink guard COURIER_DELIVERED, B3/B5).

Bug (diagnoza 2026-06-13, Jakub OL cid=370): reconcile/panel_diff/packs_ghost
wołają update_from_event z payloadem {"timestamp": raw.get("czas_doreczenia")}
BEZ fallbacku i BEZ delivery_city.
  - payload.get("timestamp", now_iso()) zwracał None (dict.get default NIE działa
    dla klucza-z-wartoscia-None) -> delivered_at=null -> build_delivered wykluczal
    -> "Doreczone" puste + utarg 0.
  - brak delivery_city -> geocode no_city -> None NADPISYWAL dobre coords z
    NEW_ORDER -> piny mapy znikaly calej flocie (208/208 doreczonych coords=None).

Fix (state_machine.update_from_event, branch COURIER_DELIVERED):
  - delivered_at: payload.get("timestamp") or now_iso()   (lapie None-value)
  - delivery_coords: ustawiane TYLKO gdy geocode dal coords; inaczej pominiete
    (upsert_order MERGE'uje {**existing, **data} -> zachowuje istniejace).

Czerwony przed fixem, zielony po. Uruchom:
  /root/.openclaw/venvs/dispatch/bin/python -m pytest tests/test_delivered_sink_guard_2026_06_13.py -v
lub standalone:
  /root/.openclaw/venvs/dispatch/bin/python tests/test_delivered_sink_guard_2026_06_13.py
"""
import os
import sys
import tempfile
from pathlib import Path

_TMP = tempfile.mkdtemp(prefix="delivered_guard_")
os.environ["DISPATCH_STATE_DIR"] = _TMP
sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import state_machine  # noqa: E402
from dispatch_v2 import geocoding  # noqa: E402

GOOD_COORDS = [53.137686, 23.168566]
ADDR = "Al. 1000-lecia Panstwa Polskiego 8b Bialystok"


def _reset():
    p = str(state_machine._state_path())
    for f in (p, p + ".prev"):
        if os.path.exists(f):
            os.remove(f)


def _seed(oid, coords=GOOD_COORDS):
    state_machine.update_from_event({
        "event_type": "NEW_ORDER",
        "order_id": oid,
        "payload": {"restaurant": "Rukola", "delivery_address": ADDR,
                    "delivery_coords": coords},
    })
    state_machine.update_from_event({
        "event_type": "COURIER_ASSIGNED",
        "order_id": oid, "courier_id": "370", "payload": {},
    })
    seeded = state_machine.get_order(oid)
    assert seeded.get("delivery_coords") == coords, (
        f"seed nie zapisal coords: {seeded.get('delivery_coords')!r}")


def test_delivered_at_none_timestamp_falls_back():
    """B3: payload {'timestamp': None} -> delivered_at truthy (now_iso), nie None."""
    _reset()
    geocoding.geocode = lambda addr, city=None: None  # symuluj no_city fail
    _seed("guard_b3")
    state_machine.update_from_event({
        "event_type": "COURIER_DELIVERED", "order_id": "guard_b3", "courier_id": "370",
        "payload": {"timestamp": None, "delivery_address": ADDR, "final_location": ADDR},
    })
    o = state_machine.get_order("guard_b3")
    assert o["status"] == "delivered"
    assert o["delivered_at"], f"PRZED FIXEM None; delivered_at={o['delivered_at']!r}"


def test_coords_preserved_when_geocode_fails():
    """B5: geocode None NIE moze wyzerowac coords z NEW_ORDER."""
    _reset()
    geocoding.geocode = lambda addr, city=None: None  # no_city
    _seed("guard_b5")
    state_machine.update_from_event({
        "event_type": "COURIER_DELIVERED", "order_id": "guard_b5", "courier_id": "370",
        "payload": {"timestamp": None, "delivery_address": ADDR, "final_location": ADDR},
    })
    o = state_machine.get_order("guard_b5")
    assert o["delivery_coords"] == GOOD_COORDS, (
        f"PRZED FIXEM coords zniszczone; got {o['delivery_coords']!r}")


def test_real_timestamp_preserved():
    """Regresja: realny timestamp zachowany (nie nadpisany now_iso)."""
    _reset()
    geocoding.geocode = lambda addr, city=None: None
    _seed("guard_ts")
    state_machine.update_from_event({
        "event_type": "COURIER_DELIVERED", "order_id": "guard_ts", "courier_id": "370",
        "payload": {"timestamp": "2026-06-13 13:24:21", "delivery_address": ADDR,
                    "final_location": ADDR},
    })
    assert state_machine.get_order("guard_ts")["delivered_at"] == "2026-06-13 13:24:21"


def test_coords_updated_when_geocode_succeeds():
    """Gdy geocode daje coords (jest delivery_city) -> zapis nowych coords."""
    _reset()
    geocoding.geocode = lambda addr, city=None: (53.15, 23.156)
    _seed("guard_geo_ok", coords=[1.0, 2.0])
    state_machine.update_from_event({
        "event_type": "COURIER_DELIVERED", "order_id": "guard_geo_ok", "courier_id": "370",
        "payload": {"timestamp": "2026-06-13 13:24:21", "delivery_address": ADDR,
                    "delivery_city": "Bialystok", "final_location": ADDR},
    })
    assert state_machine.get_order("guard_geo_ok")["delivery_coords"] == [53.15, 23.156]


if __name__ == "__main__":
    fails = 0
    for _name, _fn in sorted(globals().items()):
        if _name.startswith("test_") and callable(_fn):
            try:
                _fn()
                print(f"  PASS  {_name}")
            except AssertionError as e:
                fails += 1
                print(f"  FAIL  {_name}: {e}")
    print("ALL PASS" if not fails else f"{fails} FAIL")
    sys.exit(1 if fails else 0)
