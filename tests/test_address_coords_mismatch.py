"""B2 — shadow-detektor rozjazdu TEKST↔PIN (ENABLE_ADDRESS_COORDS_MISMATCH_SHADOW).

Geocode wstrzykiwany (fake) → deterministyczne, niezależne od żywych danych/sieci.
Oracle = realny case 484269 (tekst „Można 10/23" vs zapisany pin Mroźna, ~4,26 km).
"""
from __future__ import annotations

import json

from dispatch_v2 import address_mismatch as AM
from dispatch_v2 import common as C

# Współrzędne z geocode_cache (realne):
_MOZNA = (53.13248859999999, 23.1688403)   # geokod tekstu „Można 10"
_MROZNA = (53.1610167, 23.1261602)          # zapisany delivery_coords 484269 (Mroźna 10)


def _fake_geocode_mozna(street, city=None):
    """Tekst 'Można...' → współrzędne Można; cokolwiek innego → te same coords (match)."""
    s = (street or "").lower()
    if s.startswith("można") or s.startswith("mozna"):
        return _MOZNA
    return _MROZNA  # tekst zgodny z pinem → brak rozjazdu


def _reset():
    AM._sweep_last_ts = 0.0
    AM._coords_logged = set()


def test_oracle_484269_fires():
    """Tekst 'Można 10/23' + pin Mroźna → rozjazd ~4262 m > 400 m → werdykt."""
    w = AM.check_text_coords("Można 10/23", "Białystok", list(_MROZNA),
                             geocode_fn=_fake_geocode_mozna)
    assert w is not None
    assert w["check"] == "text_coords"
    assert 4200 <= w["distance_m"] <= 4350     # empirycznie 4262 m
    assert w["used_coords"] == [round(_MROZNA[0], 6), round(_MROZNA[1], 6)]


def test_match_text_coords_no_fire():
    """Tekst geokoduje się dokładnie na pin → 0 m → None."""
    assert AM.check_text_coords("Mroźna 10/23", "Białystok", list(_MROZNA),
                                geocode_fn=_fake_geocode_mozna) is None


def test_within_threshold_no_fire():
    """Drobne drżenie geokodu < 400 m → None (anti-false-positive)."""
    near = (_MROZNA[0] + 0.002, _MROZNA[1])    # ~222 m
    assert AM.check_text_coords("Mroźna 10/23", "Białystok", list(_MROZNA),
                                geocode_fn=lambda s, city=None: near) is None


def test_missing_inputs_no_fire():
    assert AM.check_text_coords("", "Białystok", list(_MROZNA), geocode_fn=_fake_geocode_mozna) is None
    assert AM.check_text_coords("Można 10", "Białystok", None, geocode_fn=_fake_geocode_mozna) is None
    assert AM.check_text_coords("Można 10", "Białystok", list(_MROZNA),
                                geocode_fn=lambda s, city=None: None) is None


def test_geocode_exception_fail_soft():
    def _boom(street, city=None):
        raise RuntimeError("geocode down")
    assert AM.check_text_coords("Można 10", "Białystok", list(_MROZNA), geocode_fn=_boom) is None


def test_sweep_logs_and_dedup(tmp_path, monkeypatch):
    _reset()
    log = tmp_path / "mismatch.jsonl"
    monkeypatch.setattr(AM, "_SHADOW_LOG", log)
    state = {
        "484269": {"status": "picked_up", "delivery_address": "Można 10/23",
                   "delivery_coords": list(_MROZNA), "delivery_city": "Białystok"},
        "999999": {"status": "assigned", "delivery_address": "Mroźna 8/6",
                   "delivery_coords": list(_MROZNA), "delivery_city": "Białystok"},  # match → cisza
    }
    n = AM.maybe_sweep_text_coords(state, 1000.0, geocode_fn=_fake_geocode_mozna)
    assert n == 1                                  # tylko 484269
    rec = json.loads(log.read_text(encoding="utf-8").strip())
    assert rec["order_id"] == "484269" and rec["check"] == "text_coords"
    # drugi sweep po throttlu, ten sam stan → dedup, 0 nowych
    AM._sweep_last_ts = 0.0
    assert AM.maybe_sweep_text_coords(state, 2000.0, geocode_fn=_fake_geocode_mozna) == 0


def test_sweep_throttle(tmp_path, monkeypatch):
    _reset()
    monkeypatch.setattr(AM, "_SHADOW_LOG", tmp_path / "m.jsonl")
    state = {"484269": {"status": "picked_up", "delivery_address": "Można 10/23",
                        "delivery_coords": list(_MROZNA)}}
    assert AM.maybe_sweep_text_coords(state, 1000.0, geocode_fn=_fake_geocode_mozna) == 1
    # w obrębie 300 s → throttle, nawet inny (świeży) stan nie jest skanowany
    AM._coords_logged = set()
    assert AM.maybe_sweep_text_coords(state, 1200.0, geocode_fn=_fake_geocode_mozna) == 0


def test_sweep_skips_terminal_and_missing(tmp_path, monkeypatch):
    _reset()
    monkeypatch.setattr(AM, "_SHADOW_LOG", tmp_path / "m.jsonl")
    state = {
        "1": {"status": "delivered", "delivery_address": "Można 10", "delivery_coords": list(_MROZNA)},
        "2": {"status": "assigned", "delivery_address": "Można 10", "delivery_coords": None},
        "3": {"status": "assigned", "delivery_coords": list(_MROZNA)},  # brak tekstu
    }
    assert AM.maybe_sweep_text_coords(state, 1000.0, geocode_fn=_fake_geocode_mozna) == 0


def test_flag_default_off_present_on():
    """Absent → OFF (gate nie odpala); flags.json LIVE → ON (shadow zbiera)."""
    assert C.flag("ENABLE_ADDRESS_COORDS_MISMATCH_SHADOW_NONEXISTENT_XYZ") is False
    assert C.flag("ENABLE_ADDRESS_COORDS_MISMATCH_SHADOW") is True
