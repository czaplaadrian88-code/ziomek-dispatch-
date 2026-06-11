"""FRONT-B (2026-06-11) — pickup_coords na żywo z adresu panelu (shadow-first).

_resolve_pickup_coords: geokod liczony ZAWSZE (lekcja #186) + drift vs cache;
selekcja live-first wyłącznie pod flagą ENABLE_PICKUP_COORDS_FROM_PANEL.
Guard GEO-02: wpisy manual*/adrian_manual* autorytatywne (zero geokodu).
"""
import pytest

from dispatch_v2 import panel_watcher as pw

RAJ = (53.1322335, 23.1653257)
LIVE = (53.1330000, 23.1660000)   # ~90 m od RAJ


@pytest.fixture(autouse=True)
def _isolate(monkeypatch):
    monkeypatch.setattr(pw, "_maybe_reload_coords", lambda: False)
    monkeypatch.setattr(pw, "_COORDS", {"96": RAJ})
    monkeypatch.setattr(pw, "_COORDS_META", {"96": "bootstrap_2026-04-11"})
    monkeypatch.setattr(pw, "_FRONTB_DRIFT_WARNED", set())
    monkeypatch.setattr(pw, "decision_flag", lambda n: False)
    monkeypatch.setattr(pw, "load_flags", lambda: {})


def _geocode_stub(calls, result=LIVE):
    def _g(street, city=None, timeout=None):
        calls.append((street, city))
        return result
    return _g


def test_manual_source_authoritative_no_geocode(monkeypatch):
    monkeypatch.setattr(pw, "_COORDS_META", {"96": "manual_fix_bug2_2026-06-05"})
    calls = []
    monkeypatch.setattr(pw, "geocode", _geocode_stub(calls))
    coords, src, drift = pw._resolve_pickup_coords("96", "Kilińskiego 13", "Białystok")
    assert coords == RAJ and src == "cache_manual" and drift is None
    assert calls == []  # GEO-02: zero geokodu dla ręcznych


def test_adrian_manual_prefix_also_guarded(monkeypatch):
    monkeypatch.setattr(pw, "_COORDS_META", {"96": "adrian_manual_2026-05-21"})
    calls = []
    monkeypatch.setattr(pw, "geocode", _geocode_stub(calls))
    coords, src, _ = pw._resolve_pickup_coords("96", "", "Białystok")
    assert coords == RAJ and src == "cache_manual" and calls == []


def test_off_returns_cache_but_computes_drift(monkeypatch):
    calls = []
    monkeypatch.setattr(pw, "geocode", _geocode_stub(calls))
    coords, src, drift = pw._resolve_pickup_coords("96", "Kilińskiego 13", "Białystok")
    assert coords == RAJ and src == "cache"          # OFF → cache wygrywa
    assert calls == [("Kilińskiego 13", "Białystok")]  # ale geokod POLICZONY
    assert drift is not None and 50 < drift < 150     # ~90 m


def test_off_cache_miss_stays_none(monkeypatch):
    calls = []
    monkeypatch.setattr(pw, "geocode", _geocode_stub(calls))
    coords, src, drift = pw._resolve_pickup_coords("999", "Nowa 1", "Białystok")
    assert coords is None and src == "miss"  # OFF: zachowanie bez zmian
    assert calls  # live policzony (shadow), ale nieużyty


def test_on_prefers_live(monkeypatch):
    monkeypatch.setattr(pw, "decision_flag",
                        lambda n: n == "ENABLE_PICKUP_COORDS_FROM_PANEL")
    monkeypatch.setattr(pw, "geocode", _geocode_stub([]))
    coords, src, drift = pw._resolve_pickup_coords("96", "Kilińskiego 13", "Białystok")
    assert coords == LIVE and src == "panel_live"
    assert drift is not None


def test_on_geocode_fail_falls_back_to_cache(monkeypatch):
    monkeypatch.setattr(pw, "decision_flag",
                        lambda n: n == "ENABLE_PICKUP_COORDS_FROM_PANEL")
    monkeypatch.setattr(pw, "geocode", _geocode_stub([], result=None))
    coords, src, drift = pw._resolve_pickup_coords("96", "Kilińskiego 13", "Białystok")
    assert coords == RAJ and src == "cache" and drift is None


def test_on_cache_miss_uses_live(monkeypatch):
    monkeypatch.setattr(pw, "decision_flag",
                        lambda n: n == "ENABLE_PICKUP_COORDS_FROM_PANEL")
    monkeypatch.setattr(pw, "geocode", _geocode_stub([]))
    coords, src, _ = pw._resolve_pickup_coords("999", "Nowa 1", "Białystok")
    assert coords == LIVE and src == "panel_live"


def test_empty_street_no_geocode(monkeypatch):
    calls = []
    monkeypatch.setattr(pw, "geocode", _geocode_stub(calls))
    coords, src, drift = pw._resolve_pickup_coords("96", "", "Białystok")
    assert coords == RAJ and src == "cache" and drift is None
    assert calls == []


def test_drift_warns_once_per_aid(monkeypatch, caplog):
    far = (53.20, 23.30)  # kilka km → drift >> próg 150 m
    monkeypatch.setattr(pw, "geocode", _geocode_stub([], result=far))
    import logging
    with caplog.at_level(logging.WARNING, logger=pw._log.name):
        pw._resolve_pickup_coords("96", "Inna 1", "Białystok")
        pw._resolve_pickup_coords("96", "Inna 1", "Białystok")
    warns = [r for r in caplog.records if "FRONT_B drift" in r.getMessage()]
    assert len(warns) == 1  # anti-spam Z3: raz na proces per aid


def test_flag_in_etap4_canon():
    from dispatch_v2 import common as C
    assert "ENABLE_PICKUP_COORDS_FROM_PANEL" in C.ETAP4_DECISION_FLAGS
    assert "PICKUP_COORDS_DRIFT_WARN_M" in C.FLAGS_JSON_NUMERIC_OVERRIDES
    assert C.decision_flag("ENABLE_PICKUP_COORDS_FROM_PANEL") is False
