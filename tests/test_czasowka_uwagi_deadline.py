"""Testy czasówka-w-uwagach SHADOW (sesja 20, 2026-06-28, zlec. 484034 Sikorskiego).

Pokrywa ETAP4 DoD dla additywnego pola `delivery_deadline_uwagi`:
- parser jednostkowo (czysta ekstrakcja),
- PARYTET regexu z parserem-cieniem `tools/bundle_calib_shadow` (#15/#17 — żeby się nie rozjechały),
- flaga ON≠OFF w `panel_client.normalize_order` (OFF = brak klucza = bajt-identyczny ingest),
- additywność: order_type/czas_kuriera NIE nadpisane (wzorzec #8),
- pole DOCIERA do persistu (`state_machine.update_from_event` → upsert_order).
"""
import importlib.util
import os
from datetime import date, datetime, timezone
from unittest import mock
from zoneinfo import ZoneInfo

from dispatch_v2.czasowka_uwagi import parse_delivery_deadline, _DELIVERY_DEADLINE_RE
import dispatch_v2.panel_client as pc
from dispatch_v2 import state_machine as sm

WARSAW = ZoneInfo("Europe/Warsaw")
_FLAG = "ENABLE_CZASOWKA_UWAGI_DEADLINE_SHADOW"


# ---------- parser (czysta ekstrakcja) ----------

def test_parser_case_484034():
    """'Czasówka na 17:10' → 17:10 Warsaw = 15:10 UTC."""
    dl = parse_delivery_deadline("Dania \r\nPiętro 1\r\nCzasówka na 17:10", date(2026, 6, 28))
    assert dl == datetime(2026, 6, 28, 17, 10, tzinfo=WARSAW).astimezone(timezone.utc)


def test_parser_formats():
    d = date(2026, 6, 28)
    assert parse_delivery_deadline("czasowka 14", d).astimezone(WARSAW).hour == 14
    assert parse_delivery_deadline("na 14.30", d) is None  # bez słowa 'czasówka' → brak (precyzja)
    assert parse_delivery_deadline("CZASÓWKA NA 16.30", d).astimezone(WARSAW).strftime("%H:%M") == "16:30"


def test_parser_negatives():
    d = date(2026, 6, 28)
    assert parse_delivery_deadline(None, d) is None
    assert parse_delivery_deadline("", d) is None
    assert parse_delivery_deadline("Piętro 1, klatka B", d) is None
    assert parse_delivery_deadline("czasówka na 25", d) is None  # godz. poza zakresem


def test_parser_anchor_date_is_used():
    """Kotwica daty z argumentu (data odbioru), nie 'dziś'."""
    dl = parse_delivery_deadline("czasówka na 12:00", date(2026, 1, 2))
    assert dl.astimezone(WARSAW).date() == date(2026, 1, 2)


# ---------- PARYTET z bundle_calib_shadow (regex nie może dryfować) ----------

def _load_bundle_calib():
    p = os.path.join(os.path.dirname(__file__), "..", "tools", "bundle_calib_shadow.py")
    spec = importlib.util.spec_from_file_location("bundle_calib_shadow_parity", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_regex_pattern_parity_with_shadow():
    bcs = _load_bundle_calib()
    assert _DELIVERY_DEADLINE_RE.pattern == bcs._DEADLINE_RE.pattern


def test_parse_output_parity_with_shadow():
    bcs = _load_bundle_calib()
    d = date(2026, 6, 28)
    fixtures = [
        "Dania \r\nPiętro 1\r\nCzasówka na 17:10",
        "czasowka 14",
        "CZASÓWKA NA 16.30",
        "brak deadline tutaj",
        None,
        "czasówka na 25",
    ]
    for u in fixtures:
        assert parse_delivery_deadline(u, d) == bcs._parse_deadline(u, d), f"rozjazd parytetu dla {u!r}"


# ---------- flaga ON≠OFF w normalize_order (additywność) ----------

_RAW_484034 = {
    "id": "484034", "id_status_zamowienia": 2, "czas_odbioru": 30,
    "czas_odbioru_timestamp": "2026-06-28 16:59:00", "czas_kuriera": "17:05",
    "street": "Sikorskiego", "nr_domu": "5",
    "address": {"street": "Rest", "name": "Dania", "city": "Białystok"},
    "lokalizacja": {"name": "Białystok"},
    "uwagi": "Dania \r\nPiętro 1\r\nCzasówka na 17:10",
}


def test_normalize_off_no_key(monkeypatch):
    monkeypatch.setattr(pc, "flag", lambda name, default=False: False)
    n = pc.normalize_order(dict(_RAW_484034))
    assert "delivery_deadline_uwagi" not in n  # OFF → bajt-identyczny ingest


def test_normalize_on_populates(monkeypatch):
    monkeypatch.setattr(pc, "flag", lambda name, default=False: name == _FLAG)
    n = pc.normalize_order(dict(_RAW_484034))
    assert n["delivery_deadline_uwagi"] == "2026-06-28T15:10:00+00:00"


def test_normalize_on_additive_no_overwrite(monkeypatch):
    """order_type i czas_kuriera NIETKNIĘTE (wzorzec #8)."""
    monkeypatch.setattr(pc, "flag", lambda name, default=False: name == _FLAG)
    n = pc.normalize_order(dict(_RAW_484034))
    assert n["order_type"] == "elastic"          # prep 30 < 60 → wciąż elastic
    assert n["czas_kuriera_hhmm"] == "17:05"      # committed pickup nietknięty


def test_normalize_on_no_deadline_is_none(monkeypatch):
    monkeypatch.setattr(pc, "flag", lambda name, default=False: name == _FLAG)
    raw = dict(_RAW_484034, uwagi="Piętro 1, brak czasu")
    n = pc.normalize_order(raw)
    assert n["delivery_deadline_uwagi"] is None


# ---------- pole dociera do persistu (E2E granica state) ----------

def test_persist_carries_delivery_deadline():
    captured = {}

    def fake_upsert(oid, data, event=None):
        captured["data"] = data
        captured["event"] = event
        return data

    event = {
        "event_type": "NEW_ORDER",
        "order_id": "484034",
        "payload": {
            "restaurant": "Dania", "delivery_address": "Sikorskiego 5",
            "order_type": "elastic",
            "uwagi": "Czasówka na 17:10",
            "delivery_deadline_uwagi": "2026-06-28T15:10:00+00:00",
            # brak czas_kuriera → sanity-check przechodzi → główny persist (l.492)
        },
    }
    with mock.patch.object(sm, "upsert_order", side_effect=fake_upsert):
        sm.update_from_event(event)
    assert captured["event"] == "NEW_ORDER"
    assert captured["data"]["delivery_deadline_uwagi"] == "2026-06-28T15:10:00+00:00"
    assert captured["data"]["order_type"] == "elastic"  # additywne — nie nadpisane
