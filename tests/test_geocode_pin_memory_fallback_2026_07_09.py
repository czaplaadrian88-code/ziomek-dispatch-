"""Pin-memory fallback + parytet ochrony pinów address vs restaurant (2026-07-09).

Case: Składowa 12 (kurier Adrian Cit, cid 457) — oficjalny geocoder odrzucił adres
(verify_reject), mapa koordynatora "nie widziała" dostawy bo `delivery_coords=None`.
Dwa fixy:
  1. `geocode()` dostaje TĘ SAMĄ ochronę pinów co `geocode_restaurant()` (bliźniak
     był niedopięty — pin ręczny w cache dla adresu był re-geokodowywany po TTL).
  2. Gdy oficjalna ścieżka i tak zwróci None (neg_cache/verify_reject/bbox_reject/
     total fail) — ZANIM odda None, sprawdź `address_pin_memory` (adresy uczone z
     realnego GPS kurierów). SHADOW domyślnie (flag OFF): liczy+loguje, realnie
     nadal None. LIVE (flag ON + próg n_inliers): zwraca pinezkę.
"""
import json
import pytest

from dispatch_v2 import geocoding as G


@pytest.fixture
def isolated_caches(tmp_path, monkeypatch):
    monkeypatch.setattr(G, "CACHE_PATH", tmp_path / "pos.json")
    monkeypatch.setattr(G, "NEG_CACHE_PATH", tmp_path / "neg.json")
    monkeypatch.setattr(G, "_PIN_MEMORY_STORES",
                        (tmp_path / "address_pins.json", tmp_path / "restaurant_pins.json"))
    yield tmp_path


def _flag_stub(values):
    def _f(name, default=None):
        return values.get(name, default)
    return _f


def _write_pin_store(path, key, lat, lon, n_inliers=3, confidence="high"):
    path.write_text(json.dumps({
        key: {
            "address_key": key, "lat": lat, "lon": lon,
            "confidence": confidence, "n_samples": n_inliers, "n_inliers": n_inliers,
            "spread_m": 5.0, "source": "auto_geofence",
        }
    }, ensure_ascii=False))


# ---- Fix 1: ochrona pinów w geocode() (parytet z geocode_restaurant) ----

def test_geocode_pinned_entry_never_reregeocoded_even_when_stale(isolated_caches, monkeypatch):
    key = G._normalize("Składowa 12", "Białystok")
    G.CACHE_PATH.write_text(json.dumps({
        key: {"lat": 53.1164847, "lon": 23.126358, "source": "pinned_manual",
              "cached_at": "pinned:manual-2026-07-09"}
    }, ensure_ascii=False))

    calls = {"google": 0}
    monkeypatch.setattr(G, "_google_geocode", lambda *a, **k: calls.__setitem__("google", calls["google"] + 1) or (0.0, 0.0, {}))
    monkeypatch.setattr(G.C, "flag", _flag_stub({
        "ENABLE_GEOCODE_CACHE_TTL": True, "CITY_AWARE_GEOCODING": True,
    }))
    monkeypatch.setattr(G.C, "GEOCODE_CACHE_TTL_DAYS", 0.0, raising=False)  # wymusza "stale" gdyby nie pin

    lat, lon = G.geocode("Składowa 12", "Białystok")
    assert (lat, lon) == (53.1164847, 23.126358)
    assert calls["google"] == 0, "pin NIGDY nie wywołuje sieci, nawet gdy TTL każe re-geokodować"


def test_geocode_pinned_entry_not_overwritten_on_write_path(isolated_caches, monkeypatch):
    # symuluje race: pin istnieje w cache pod lockiem tuż przed zapisem świeżego wyniku
    key = G._normalize("Składowa 12", "Białystok")
    G.CACHE_PATH.write_text(json.dumps({
        key: {"lat": 53.1164847, "lon": 23.126358, "cached_at": "pinned:race-2026-07-09"}
    }, ensure_ascii=False))
    # direct unit test na wewnętrznym helperze użytym w write-path
    assert G._is_pinned_entry({"cached_at": "pinned:race-2026-07-09"}) is True
    assert G._is_pinned_entry({"cached_at": 12345.0}) is False
    assert G._is_pinned_entry(None) is False


def test_geocode_restaurant_pin_unaffected_by_address_fix(isolated_caches, monkeypatch):
    """Parytet: fix w geocode() nie psuje istniejącej ochrony w geocode_restaurant()."""
    monkeypatch.setattr(G, "RESTAURANT_CACHE_PATH", isolated_caches / "rest.json")
    key = "sweet fit & eat"
    G.RESTAURANT_CACHE_PATH.write_text(json.dumps({
        key: {"lat": 53.128, "lon": 23.152, "cached_at": "pinned:legacy"}
    }, ensure_ascii=False))
    calls = {"google": 0}
    monkeypatch.setattr(G, "_google_geocode", lambda *a, **k: calls.__setitem__("google", calls["google"] + 1) or (0.0, 0.0, {}))
    monkeypatch.setattr(G.C, "flag", _flag_stub({"CITY_AWARE_GEOCODING": True}))
    lat, lon = G.geocode_restaurant("Sweet Fit & Eat", city="Białystok")
    assert (lat, lon) == (53.128, 23.152)
    assert calls["google"] == 0


# ---- Fix 2: pin-memory fallback (shadow vs live) ----

def _setup_verify_reject_world(monkeypatch, pin_memory_flag):
    monkeypatch.setattr(G, "_google_geocode", lambda q, timeout=5.0: (53.1205151, 23.1232559, {}))
    monkeypatch.setattr(G, "_in_service_bbox", lambda la, lo: True)
    monkeypatch.setattr(G, "_run_verification",
                        lambda *a, **k: {"confidence": "reject", "reasons": ["district_mismatch"], "checks": {}})
    monkeypatch.setattr(G.C, "flag", _flag_stub({
        "ENABLE_GEOCODE_VERIFICATION_ENFORCE": True,
        "ENABLE_GEOCODE_NEGATIVE_CACHE": True,
        "ENABLE_GEOCODE_NOMINATIM_FALLBACK": False,
        "ENABLE_GEOCODE_CACHE_TTL": False,
        "CITY_AWARE_GEOCODING": True,
        "ENABLE_GEOCODE_PIN_MEMORY_FALLBACK": pin_memory_flag,
    }))
    monkeypatch.setattr(G.C, "GEOCODE_NEG_CACHE_TTL_SEC", 21600, raising=False)
    monkeypatch.setattr(G.C, "ENABLE_GEOCODE_PIN_MEMORY_FALLBACK", pin_memory_flag, raising=False)
    monkeypatch.setattr(G.C, "GEOCODE_PIN_MEMORY_MIN_INLIERS", 1, raising=False)


def test_verify_reject_shadow_mode_still_returns_none(isolated_caches, monkeypatch):
    _setup_verify_reject_world(monkeypatch, pin_memory_flag=False)
    from dispatch_v2 import address_pin_memory as apm
    key = apm.normalize_address("Składowa 12 Białystok")
    _write_pin_store(isolated_caches / "address_pins.json", key, 53.1161767, 23.1269167, n_inliers=1, confidence="low")

    result = G.geocode("Składowa 12", "Białystok")
    assert result is None, "SHADOW (flag OFF) — zachowanie identyczne jak przed fixem, mimo że pinezka istnieje"


def test_verify_reject_live_mode_uses_pin_memory(isolated_caches, monkeypatch):
    _setup_verify_reject_world(monkeypatch, pin_memory_flag=True)
    from dispatch_v2 import address_pin_memory as apm
    key = apm.normalize_address("Składowa 12 Białystok")
    _write_pin_store(isolated_caches / "address_pins.json", key, 53.1161767, 23.1269167, n_inliers=1, confidence="low")

    lat, lon = G.geocode("Składowa 12", "Białystok")
    assert (round(lat, 6), round(lon, 6)) == (53.116177, 23.126917)


def test_verify_reject_live_mode_below_confidence_bar_returns_none(isolated_caches, monkeypatch):
    _setup_verify_reject_world(monkeypatch, pin_memory_flag=True)
    monkeypatch.setattr(G.C, "GEOCODE_PIN_MEMORY_MIN_INLIERS", 3, raising=False)
    from dispatch_v2 import address_pin_memory as apm
    key = apm.normalize_address("Składowa 12 Białystok")
    _write_pin_store(isolated_caches / "address_pins.json", key, 53.1161767, 23.1269167, n_inliers=1, confidence="low")

    result = G.geocode("Składowa 12", "Białystok")
    assert result is None, "próg n_inliers=3 nie spełniony przez pinezkę n=1 → nadal None"


def test_pin_memory_checks_restaurant_store_too(isolated_caches, monkeypatch):
    """Fallback sprawdza OBIE przestrzenie (dostawy + restauracje) — geocode() jest
    wołany też dla adresów pickup (uwagi-parser firmowe konto)."""
    _setup_verify_reject_world(monkeypatch, pin_memory_flag=True)
    from dispatch_v2 import address_pin_memory as apm
    key = apm.normalize_address("Jarzębinowa 2A/1")
    _write_pin_store(isolated_caches / "restaurant_pins.json", key, 53.151179, 23.111787, n_inliers=5, confidence="high")

    lat, lon = G.geocode("Jarzębinowa 2A/1", "Białystok")
    assert (round(lat, 6), round(lon, 6)) == (53.151179, 23.111787)


def test_pin_memory_missing_store_files_fail_soft(isolated_caches, monkeypatch):
    """Brak plików pinezek (jeszcze nie uzbierane) — fail-soft, żaden wyjątek, zwykłe None."""
    _setup_verify_reject_world(monkeypatch, pin_memory_flag=True)
    assert not (isolated_caches / "address_pins.json").exists()
    assert not (isolated_caches / "restaurant_pins.json").exists()
    result = G.geocode("Nieznana Ulica 99", "Białystok")
    assert result is None


def test_pin_memory_lookup_normalizes_like_address_pin_memory(isolated_caches):
    from dispatch_v2 import address_pin_memory as apm
    _write_pin_store(isolated_caches / "address_pins.json", "składowa 12", 53.11, 23.12)
    entry = G._pin_memory_lookup("Składowa 12  ")
    assert entry is not None
    assert entry["lat"] == 53.11
    assert apm.normalize_address("Składowa 12  ") == "składowa 12"
