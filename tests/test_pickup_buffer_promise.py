"""PICKUP-BUFFER v4 (2026-07-06) — bufor OBIETNICY odbioru: BAZA + per-restauracja.

GO Adriana 06.07: baza 3 min (mediana jedzeniówki 8,2 − tolerancja 5; bez
paczek/czasówek, matched-only) + korekta per-restauracja dla stabilnych
odchyleńców (punktualne = jawne 0.0). Kubełki obciążenia/worka ODRZUCONE
danymi (0 zysku OOS). Pola addytywne — wewnętrzne eta_pickup_utc nietknięte.
Guard paczek (is_paczka_order) zdejmuje pola w _tick po stemplu address_id.
"""
import sys

import pytest

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import common as C
from dispatch_v2 import shadow_dispatcher as sd


class _Res:
    def __init__(self, restaurant=None):
        self.restaurant = restaurant


# --------------------------------------------------------------------------- #
# helper: baza + tabela per-restauracja
# --------------------------------------------------------------------------- #

def test_buffer_base_for_unknown_restaurant():
    assert C.pickup_buffer_min(None) == 3.0
    assert C.pickup_buffer_min("Nowa Knajpa XYZ") == 3.0


def test_buffer_restaurant_deviants_and_punctual_zero():
    assert C.pickup_buffer_min("Baanko") == 9.0
    assert C.pickup_buffer_min("Pizzeria 105 Galeria Biała") == 8.5
    # punktualna restauracja = JAWNE zero (kucharz nie przedłuża gotowania)
    assert C.pickup_buffer_min("Hacienda Pizza") == 0.0
    assert C.pickup_buffer_min("Street Mama Thai") == 0.0


def test_buffer_cap(monkeypatch):
    monkeypatch.setitem(C.PICKUP_BUFFER_RESTAURANT_TABLE, "Baanko", 99.0)
    assert C.pickup_buffer_min("Baanko") == C.PICKUP_BUFFER_MAX_MIN


# --------------------------------------------------------------------------- #
# serializer: pola promised ON≠OFF
# --------------------------------------------------------------------------- #

BEST_M = {"eta_pickup_utc": "2026-07-06T11:00:00+00:00"}


def _set_flag(monkeypatch, value):
    monkeypatch.setattr(
        sd.C, "flag",
        lambda name, default=False:
            value if name == "ENABLE_LOAD_AWARE_PICKUP_BUFFER" else default)


def test_promised_fields_on_base(monkeypatch):
    _set_flag(monkeypatch, True)
    f = sd._promised_pickup_fields(dict(BEST_M), _Res(None))
    # baza 3.0; 11:00 UTC = 13:00 Warsaw
    assert f["pickup_buffer_min"] == 3.0
    assert f["eta_pickup_promised_hhmm"] == "13:03"
    assert f["eta_pickup_promised_utc"].startswith("2026-07-06T11:03:00")


def test_promised_fields_restaurant_deviant(monkeypatch):
    _set_flag(monkeypatch, True)
    f = sd._promised_pickup_fields(dict(BEST_M), _Res("Baanko"))
    assert f["pickup_buffer_min"] == 9.0
    assert f["eta_pickup_promised_hhmm"] == "13:09"


def test_promised_fields_punctual_restaurant_no_fields(monkeypatch):
    _set_flag(monkeypatch, True)
    # bufor 0 → brak pól (stara obietnica bez zapasu)
    assert sd._promised_pickup_fields(dict(BEST_M), _Res("Hacienda Pizza")) == {}


def test_promised_fields_off_flag(monkeypatch):
    _set_flag(monkeypatch, False)
    assert sd._promised_pickup_fields(dict(BEST_M), _Res(None)) == {}


def test_promised_fields_fail_open_missing_eta(monkeypatch):
    _set_flag(monkeypatch, True)
    assert sd._promised_pickup_fields({}, _Res(None)) == {}
