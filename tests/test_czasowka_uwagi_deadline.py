"""Testy czasówka-w-uwagach SHADOW (sesja 20, 2026-06-28, zlec. 484034 Sikorskiego).

Pokrywa ETAP4 DoD dla additywnego pola `delivery_deadline_uwagi`:
- parser jednostkowo (czysta ekstrakcja, recall + precyzja po Stage-2 broadening),
- BRAK REGRESJI RECALL vs parser-cień `bundle_calib_shadow` (na jego trafieniach mój zwraca to samo),
- nowe trafienia Stage-2 (odwrotna kolejność / słowa pomiędzy / stem / literówka),
- flaga ON≠OFF w `panel_client.normalize_order` (OFF = brak klucza = bajt-identyczny ingest),
- additywność: order_type/czas_kuriera NIE nadpisane (wzorzec #8),
- sanity-gate: deadline < pickup → dropniete w normalize_order (precyzja),
- pole DOCIERA do persistu (`state_machine.update_from_event` → upsert_order).
"""
import importlib.util
import os
from datetime import date, datetime, timezone
from unittest import mock
from zoneinfo import ZoneInfo

from dispatch_v2.czasowka_uwagi import parse_delivery_deadline
import dispatch_v2.panel_client as pc
from dispatch_v2 import state_machine as sm

WARSAW = ZoneInfo("Europe/Warsaw")
_FLAG = "ENABLE_CZASOWKA_UWAGI_DEADLINE_SHADOW"


def _hhmm(u, d=date(2026, 6, 28)):
    r = parse_delivery_deadline(u, d)
    return r.astimezone(WARSAW).strftime("%H:%M") if r else None


# ---------- parser: czysta ekstrakcja ----------

def test_parser_case_484034():
    dl = parse_delivery_deadline("Dania \r\nPiętro 1\r\nCzasówka na 17:10", date(2026, 6, 28))
    assert dl == datetime(2026, 6, 28, 17, 10, tzinfo=WARSAW).astimezone(timezone.utc)


def test_parser_classic_formats():
    assert _hhmm("czasowka 14") == "14:00"
    assert _hhmm("CZASÓWKA NA 16.30") == "16:30"
    assert _hhmm("Pizza 50 czasówka na 18 u klienta") == "18:00"
    assert _hhmm("Czasówka na 15.30, pod hotel ibis") == "15:30"


def test_parser_anchor_date_is_used():
    dl = parse_delivery_deadline("czasówka na 12:00", date(2026, 1, 2))
    assert dl.astimezone(WARSAW).date() == date(2026, 1, 2)


# ---------- Stage-2 broadening: nowe trafienia (recall) ----------

def test_parser_stage2_recall():
    """6 realnych przypadków, które wąski parser-cień gubił (z korpusu oracle)."""
    assert _hhmm("CZASOWKA BA 20:45)21:00\r\nBURGIR") == "20:45"            # literówka 'BA' + okno
    assert _hhmm("Pizza, czasówka u klienta na 12.15, lokal X 4 piętro") == "12:15"  # słowa pomiędzy
    assert _hhmm("Zamówienie czasowe na 18:50 u klienta") == "18:50"        # stem bez 'k'
    assert _hhmm("Karta 50cm na 19.30 czasowkaaaa") == "19:30"              # odwrotna kolejność
    assert _hhmm("Na 15:00 czasówka") == "15:00"                            # odwrotna kolejność
    assert _hhmm("na 20.20 u klienta czasówka") == "20:20"                  # odwrotna + słowa


def test_parser_comma_semicolon_separator():
    """Polski zapis czasu przecinkiem/średnikiem (realny w korpusie oracle)."""
    assert _hhmm("Pizza czasówka na 12,30 u klienta") == "12:30"
    assert _hhmm("Czasówka na 19,30 u klienta pizza") == "19:30"
    assert _hhmm("czasowka na 20;30") == "20:30"
    assert _hhmm("czasówka na 16,45 u klienta") == "16:45"


# ---------- parser: precyzja (nie łapać śmieci) ----------

def test_parser_precision_negatives():
    assert parse_delivery_deadline(None, date(2026, 6, 28)) is None
    assert parse_delivery_deadline("", date(2026, 6, 28)) is None
    assert _hhmm("Piętro 1, klatka B") is None
    assert _hhmm("4 piętro, domofon 12") is None          # liczby ale BRAK słowa-klucza
    assert _hhmm("Pizza 50 dań, gotówka") is None
    assert _hhmm("na 14.30") is None                       # czas ale brak słowa-klucza
    assert _hhmm("czasówka na 25") is None                 # godzina poza zakresem
    assert _hhmm("czasówka standardowa, bez godziny") is None


def test_parser_nie_wczesniej_is_not_deadline():
    """'nie wcześniej'/'nie przed' = ograniczenie NAJWCZEŚNIEJ (deliver NIE PRZED), nie deadline.
    Bez tego oracle liczył fałszywe 'late' (case 477952 'na 20 nie wcześniej' → dostawa 20:07 OK)."""
    assert _hhmm("Czasówka na 20 u klienta nie wcześniej bo nikogo nie będzie") is None
    assert _hhmm("czasówka na 18, nie przed 18") is None
    # kontrola: zwykły deadline dalej działa
    assert _hhmm("Czasówka na 18 u klienta") == "18:00"


# ---------- brak regresji recall vs parser-cień bundle_calib_shadow ----------

def _load_bundle_calib():
    p = os.path.join(os.path.dirname(__file__), "..", "tools", "bundle_calib_shadow.py")
    spec = importlib.util.spec_from_file_location("bundle_calib_shadow_parity", p)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_no_recall_loss_vs_shadow():
    """Na jednoznacznych trafieniach parsera-cienia mój zwraca TO SAMO (zero regresji recall).
    Mój jest SUPERSETEM (łapie też przypadki, których cień nie łapie — patrz test_parser_stage2_recall)."""
    bcs = _load_bundle_calib()
    d = date(2026, 6, 28)
    single_deadline_fixtures = [
        "Dania \r\nPiętro 1\r\nCzasówka na 17:10",
        "czasowka 14",
        "CZASÓWKA NA 16.30",
        "Czasówka na 15.30, pod hotel ibis",
        "brak deadline tutaj",
        None,
        "czasówka na 25",
    ]
    for u in single_deadline_fixtures:
        shadow = bcs._parse_deadline(u, d)
        if shadow is not None:
            assert parse_delivery_deadline(u, d) == shadow, f"recall-regresja vs cień dla {u!r}"


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
    assert "delivery_deadline_uwagi" not in n


def test_normalize_on_populates(monkeypatch):
    monkeypatch.setattr(pc, "flag", lambda name, default=False: name == _FLAG)
    n = pc.normalize_order(dict(_RAW_484034))
    assert n["delivery_deadline_uwagi"] == "2026-06-28T15:10:00+00:00"


def test_normalize_on_additive_no_overwrite(monkeypatch):
    monkeypatch.setattr(pc, "flag", lambda name, default=False: name == _FLAG)
    n = pc.normalize_order(dict(_RAW_484034))
    assert n["order_type"] == "elastic"
    assert n["czas_kuriera_hhmm"] == "17:05"


def test_normalize_on_no_deadline_is_none(monkeypatch):
    monkeypatch.setattr(pc, "flag", lambda name, default=False: name == _FLAG)
    n = pc.normalize_order(dict(_RAW_484034, uwagi="Piętro 1, brak czasu"))
    assert n["delivery_deadline_uwagi"] is None


def test_normalize_sanity_drops_deadline_before_pickup(monkeypatch):
    """Stage-2 sanity: deadline DOSTAWY przed odbiorem (artefakt) → None."""
    monkeypatch.setattr(pc, "flag", lambda name, default=False: name == _FLAG)
    # pickup 16:59, uwagi parsowane na 02:00 (np. "czasówka 2 ...") → przed pickupem → drop
    n = pc.normalize_order(dict(_RAW_484034, uwagi="czasówka 2 dania pod drzwi"))
    assert n["delivery_deadline_uwagi"] is None


# ---------- pole dociera do persistu ----------

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
        },
    }
    with mock.patch.object(sm, "upsert_order", side_effect=fake_upsert):
        sm.update_from_event(event)
    assert captured["event"] == "NEW_ORDER"
    assert captured["data"]["delivery_deadline_uwagi"] == "2026-06-28T15:10:00+00:00"
    assert captured["data"]["order_type"] == "elastic"
