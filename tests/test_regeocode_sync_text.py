"""Fix asymetrii gastro_edit.regeocode_and_update (ENABLE_REGEOCODE_SYNC_TEXT, 2026-06-29).

Po edycji adresu w gastro: gdy flaga ON, zapisuje delivery_address+delivery_city spójnie z
delivery_coords (koniec rozjazdu tekst↔pin, case 484269 Można≠Mroźna). OFF = zachowanie sprzed
fixu (tylko coords). geocoding/state_machine/common monkeypatchowane → deterministyczne.
"""
from __future__ import annotations

import gastro_edit as GE
from dispatch_v2 import common as C, geocoding, state_machine


def test_build_display_address():
    assert GE._build_display_address("Mroźna", "10", "23") == "Mroźna 10/23"
    assert GE._build_display_address("Sybiraków", "14", "1") == "Sybiraków 14/1"
    assert GE._build_display_address("Mroźna", "10", "") == "Mroźna 10"
    assert GE._build_display_address("Rynek Kościuszki", "", "") == "Rynek Kościuszki"
    # nr_domu już zawiera '/': nie dubluj apartamentu
    assert GE._build_display_address("Lesna", "10/23", "23") == "Lesna 10/23"
    # ulica zawsze na początku (district-parser bierze część przed numerem)
    assert GE._build_display_address("Mroźna", "10", "23").split()[0] == "Mroźna"


def _patch(monkeypatch, flag_on, cap):
    monkeypatch.setattr(geocoding, "geocode", lambda addr, city=None, **k: (53.1610167, 23.1261602))
    monkeypatch.setattr(state_machine, "upsert_order",
                        lambda oid, data, event=None: (cap.update(oid=oid, data=dict(data), event=event), data)[1])
    monkeypatch.setattr(C, "flag",
                        lambda name, default=False: flag_on if name == "ENABLE_REGEOCODE_SYNC_TEXT" else default)


def test_flag_on_writes_text_and_city(monkeypatch):
    cap = {}
    _patch(monkeypatch, True, cap)
    GE.regeocode_and_update("484269", "Mroźna 10", "Białystok", display_address="Mroźna 10/23")
    d = cap["data"]
    assert d["delivery_coords"] == [53.1610167, 23.1261602]
    assert d["delivery_address"] == "Mroźna 10/23"
    assert d["delivery_city"] == "Białystok"
    assert cap["event"] == "EDIT_REGEOCODE"


def test_flag_off_coords_only(monkeypatch):
    cap = {}
    _patch(monkeypatch, False, cap)
    GE.regeocode_and_update("484269", "Mroźna 10", "Białystok", display_address="Mroźna 10/23")
    assert cap["data"] == {"delivery_coords": [53.1610167, 23.1261602]}  # zachowanie sprzed fixu


def test_flag_on_empty_display_coords_only(monkeypatch):
    cap = {}
    _patch(monkeypatch, True, cap)
    GE.regeocode_and_update("1", "Mroźna 10", "Białystok", display_address="")
    assert "delivery_address" not in cap["data"]


def test_geocode_none_no_write(monkeypatch):
    cap = {}
    monkeypatch.setattr(geocoding, "geocode", lambda addr, city=None, **k: None)
    monkeypatch.setattr(state_machine, "upsert_order", lambda *a, **k: cap.update(called=True))
    monkeypatch.setattr(C, "flag", lambda name, default=False: True)
    assert GE.regeocode_and_update("1", "X", "Y", display_address="X 1") is None
    assert not cap.get("called")


def test_flag_read_fail_falls_back_to_coords_only(monkeypatch):
    cap = {}
    monkeypatch.setattr(geocoding, "geocode", lambda addr, city=None, **k: (53.1, 23.1))
    monkeypatch.setattr(state_machine, "upsert_order",
                        lambda oid, data, event=None: cap.update(data=dict(data)))

    def _boom(name, default=False):
        raise RuntimeError("flags down")
    monkeypatch.setattr(C, "flag", _boom)
    GE.regeocode_and_update("1", "Mroźna 10", "Białystok", display_address="Mroźna 10/23")
    assert cap["data"] == {"delivery_coords": [53.1, 23.1]}  # fail-soft: tylko coords
