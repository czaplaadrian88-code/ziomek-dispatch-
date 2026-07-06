"""PICKUP-BUFFER (2026-07-06) — load-aware bufor OBIETNICY odbioru.

Decyzja Adriana 06.07 (review pickup-slip 04.07, n=1324): powierzchnia =
OBIETNICA DECYZYJNA. Pokrycie: helper C.pickup_buffer_min (kubełki 1:1 z
tools/pickup_slip_monitor), pola serializera eta_pickup_promised_* ON≠OFF,
fail-open na brakach danych. Wewnętrzne eta_pickup_utc ma zostać NIETKNIĘTE
(wzorzec #8) — pola są addytywne.
"""
import sys

import pytest

sys.path.insert(0, "/root/.openclaw/workspace/scripts")

from dispatch_v2 import common as C
from dispatch_v2 import shadow_dispatcher as sd


class _Res:
    def __init__(self, pf):
        self.pool_feasible_count = pf


# --------------------------------------------------------------------------- #
# helper: kubełki identyczne z pickup_slip_monitor (luzno>=5 / srednio 2-4 /
# ciasno<=1; solo = bag_after 1)
# --------------------------------------------------------------------------- #

def test_buffer_buckets_match_monitor_semantics():
    # v2 (Adrian 06.07): efektywny bufor = mediana(matched-only) − tolerancja 5
    assert C.pickup_buffer_min(1, 1) == 11.0   # ciasno solo (16−5)
    assert C.pickup_buffer_min(0, 1) == 11.0   # ciasno solo (pf=0)
    assert C.pickup_buffer_min(1, 3) == 6.0    # ciasno bundle (11−5)
    assert C.pickup_buffer_min(2, 1) == 11.0   # srednio solo (16−5)
    assert C.pickup_buffer_min(4, 2) == 7.0    # srednio bundle (12−5)
    assert C.pickup_buffer_min(5, 1) == 3.5    # luzno solo (8.5−5)
    assert C.pickup_buffer_min(9, 4) == 2.5    # luzno bundle (7.5−5)


def test_buffer_fail_open_and_cap(monkeypatch):
    assert C.pickup_buffer_min(None, 1) == 0.0
    assert C.pickup_buffer_min(3, None) == 0.0
    assert C.pickup_buffer_min("x", 1) == 0.0
    monkeypatch.setitem(C.PICKUP_BUFFER_TABLE, ("ciasno", "solo"), 99.0)
    assert C.pickup_buffer_min(1, 1) == C.PICKUP_BUFFER_MAX_MIN


# --------------------------------------------------------------------------- #
# serializer: pola promised ON≠OFF
# --------------------------------------------------------------------------- #

BEST_M = {"eta_pickup_utc": "2026-07-06T11:00:00+00:00", "r6_bag_size": 0}


def _set_flag(monkeypatch, value):
    monkeypatch.setattr(
        sd.C, "flag",
        lambda name, default=False:
            value if name == "ENABLE_LOAD_AWARE_PICKUP_BUFFER" else default)


def test_promised_fields_on(monkeypatch):
    _set_flag(monkeypatch, True)
    f = sd._promised_pickup_fields(dict(BEST_M), _Res(1))
    # ciasno (pf=1) × solo (bag_after=0+1) → +11 min; 11:00 UTC = 13:00 Warsaw
    assert f["pickup_buffer_min"] == 11.0
    assert f["eta_pickup_promised_hhmm"] == "13:11"
    assert f["eta_pickup_promised_utc"].startswith("2026-07-06T11:11:00")


def test_promised_fields_off_flag(monkeypatch):
    _set_flag(monkeypatch, False)
    assert sd._promised_pickup_fields(dict(BEST_M), _Res(1)) == {}


def test_promised_fields_fail_open(monkeypatch):
    _set_flag(monkeypatch, True)
    # brak pool_feasible → bufor 0 → brak pól (stara obietnica)
    assert sd._promised_pickup_fields(dict(BEST_M), _Res(None)) == {}
    # brak eta_pickup_utc → brak pól
    assert sd._promised_pickup_fields({"r6_bag_size": 0}, _Res(1)) == {}
    # brak r6_bag_size → bag_after None → bufor 0 → brak pól
    assert sd._promised_pickup_fields(
        {"eta_pickup_utc": "2026-07-06T11:00:00+00:00"}, _Res(1)) == {}


def test_promised_fields_bundle_bucket(monkeypatch):
    _set_flag(monkeypatch, True)
    m = dict(BEST_M, r6_bag_size=2)  # bag_after=3 → bundle; pf=7 → luzno → +2.5
    f = sd._promised_pickup_fields(m, _Res(7))
    assert f["pickup_buffer_min"] == 2.5
    assert f["eta_pickup_promised_hhmm"] == "13:02"
