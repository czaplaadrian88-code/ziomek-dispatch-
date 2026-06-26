"""Negatywny cache geokodowania (P1-latencja, 2026-06-26).

Dowód: adres deterministycznie odrzucony (verify_reject) NIE wywołuje sieci przy
kolejnym lookupie gdy flaga ON; przy OFF — wywołuje (parytet ON≠OFF). Chroni przed
regresją „460 jałowych GEOCODE_VERIFY_REJECT/3h" + jest źródłem zysku latencji.
"""
import time
import pytest

from dispatch_v2 import geocoding as G


@pytest.fixture
def isolated_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "CACHE_PATH", tmp_path / "pos.json")
    monkeypatch.setattr(G, "NEG_CACHE_PATH", tmp_path / "neg.json")
    yield


def _flag_stub(values):
    def _f(name, default=None):
        return values.get(name, default)
    return _f


# ---- helpery neg-cache ----

def test_neg_put_then_check_fresh(isolated_caches, monkeypatch):
    monkeypatch.setattr(G.C, "flag", _flag_stub({"ENABLE_GEOCODE_NEGATIVE_CACHE": True}))
    monkeypatch.setattr(G.C, "GEOCODE_NEG_CACHE_TTL_SEC", 21600, raising=False)
    G._neg_cache_put("ul. test 1|bialystok", "verify_reject")
    assert G._neg_cache_check("ul. test 1|bialystok") is True
    assert G._neg_cache_check("inny|bialystok") is False


def test_neg_check_expired_by_ttl(isolated_caches, monkeypatch):
    monkeypatch.setattr(G.C, "flag", _flag_stub({"ENABLE_GEOCODE_NEGATIVE_CACHE": True}))
    monkeypatch.setattr(G.C, "GEOCODE_NEG_CACHE_TTL_SEC", 0.0, raising=False)  # natychmiast stare
    G._neg_cache_put("k|bialystok", "verify_reject")
    time.sleep(0.01)
    assert G._neg_cache_check("k|bialystok") is False


def test_neg_check_disabled_flag_off(isolated_caches, monkeypatch):
    # zapis pod ON, odczyt pod OFF → False (flaga wyłącza całą warstwę)
    monkeypatch.setattr(G.C, "flag", _flag_stub({"ENABLE_GEOCODE_NEGATIVE_CACHE": True}))
    G._neg_cache_put("k|bialystok", "verify_reject")
    monkeypatch.setattr(G.C, "flag", _flag_stub({"ENABLE_GEOCODE_NEGATIVE_CACHE": False}))
    assert G._neg_cache_check("k|bialystok") is False


# ---- pełen flow geocode(): ON pomija sieć, OFF nie (parytet) ----

def _setup_reject_world(monkeypatch, neg_on):
    calls = {"google": 0}

    def fake_google(q, timeout=5.0):
        calls["google"] += 1
        return (53.13, 23.16, {})  # w bbox, ale weryfikacja odrzuci

    monkeypatch.setattr(G, "_google_geocode", fake_google)
    monkeypatch.setattr(G, "_in_service_bbox", lambda la, lo: True)
    monkeypatch.setattr(G, "_run_verification",
                        lambda *a, **k: {"confidence": "reject", "reasons": ["test"], "checks": {}})
    monkeypatch.setattr(G.C, "flag", _flag_stub({
        "ENABLE_GEOCODE_NEGATIVE_CACHE": neg_on,
        "ENABLE_GEOCODE_VERIFICATION_ENFORCE": True,
        "ENABLE_GEOCODE_NOMINATIM_FALLBACK": False,
        "ENABLE_GEOCODE_CACHE_TTL": False,
        "CITY_AWARE_GEOCODING": True,
    }))
    monkeypatch.setattr(G.C, "GEOCODE_NEG_CACHE_TTL_SEC", 21600, raising=False)
    return calls


def test_geocode_neg_cache_ON_skips_second_network(isolated_caches, monkeypatch):
    calls = _setup_reject_world(monkeypatch, neg_on=True)
    a, c = "Jana Pawła 56b/16", "Białystok"
    assert G.geocode(a, c) is None      # 1. raz: sieć + reject + zapis neg
    assert calls["google"] == 1
    assert G.geocode(a, c) is None      # 2. raz: neg-cache HIT → BEZ sieci
    assert calls["google"] == 1, "drugie wywołanie NIE powinno trafić do Google"


def test_geocode_neg_cache_OFF_recalls_network(isolated_caches, monkeypatch):
    calls = _setup_reject_world(monkeypatch, neg_on=False)
    a, c = "Jana Pawła 56b/16", "Białystok"
    assert G.geocode(a, c) is None
    assert calls["google"] == 1
    assert G.geocode(a, c) is None      # OFF → znów sieć (stare zachowanie)
    assert calls["google"] == 2, "przy OFF drugie wywołanie MUSI trafić do Google (parytet)"
