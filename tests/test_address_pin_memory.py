"""Testy pamięci pinezek adresów (Etap 1) — robust-center, opcja B, atomic store.

Zero zależności od silnika dispatchu; sprawdza samą logikę doboru/utwardzania
pinezki i zapis magazynu.
"""
import os

from dispatch_v2 import address_pin_memory as apm

# Białystok — punkt bazowy + przesunięcia ~metry
BLAT, BLON = 53.1300000, 23.1400000


def _shift(lat, lon, dlat_m=0.0, dlon_m=0.0):
    """Przesuwa punkt o ~metry (przybliżenie dla Białegostoku)."""
    return (lat + dlat_m / 111_320.0, lon + dlon_m / (111_320.0 * 0.6))


def _s(lat, lon, accuracy=None, trigger="auto_geofence", ts=None, order_id=None):
    return {"lat": lat, "lon": lon, "accuracy": accuracy, "trigger": trigger,
            "ts": ts, "order_id": order_id}


# --- haversine ---------------------------------------------------------------
def test_haversine_zero_and_known():
    assert apm.haversine_m(BLAT, BLON, BLAT, BLON) == 0.0
    la, lo = _shift(BLAT, BLON, dlat_m=100.0)
    d = apm.haversine_m(BLAT, BLON, la, lo)
    assert 95.0 <= d <= 105.0  # ~100 m


# --- select_best_pin ---------------------------------------------------------
def test_single_sample_low_conf():
    best = apm.select_best_pin([_s(BLAT, BLON)])
    assert best is not None
    assert best["confidence"] == "low"
    assert best["n_samples"] == 1 and best["n_inliers"] == 1


def test_tight_cluster_high_conf():
    pts = [_s(*_shift(BLAT, BLON, d, d)) for d in (-5, 0, 5, 3)]
    best = apm.select_best_pin(pts)
    assert best["confidence"] == "high"
    assert best["n_inliers"] == 4
    assert apm.haversine_m(best["lat"], best["lon"], BLAT, BLON) < 15.0


def test_outlier_does_not_move_center():
    """Skupisko 3 + 1 odstrzał 'w biegu' 300 m dalej → odstrzał odrzucony."""
    pts = [_s(*_shift(BLAT, BLON, d, 0)) for d in (-4, 0, 4)]
    pts.append(_s(*_shift(BLAT, BLON, 300, 300)))  # daleki strzał
    best = apm.select_best_pin(pts)
    assert best["n_samples"] == 4 and best["n_inliers"] == 3
    assert apm.haversine_m(best["lat"], best["lon"], BLAT, BLON) < 10.0


def test_accuracy_reject():
    """Punkt o złej dokładności (200 m) nie psuje środka."""
    pts = [_s(*_shift(BLAT, BLON, d, 0), accuracy=10) for d in (-3, 0, 3)]
    bad_lat, bad_lon = _shift(BLAT, BLON, 250, 0)
    pts.append(_s(bad_lat, bad_lon, accuracy=200.0))
    best = apm.select_best_pin(pts)
    assert apm.haversine_m(best["lat"], best["lon"], BLAT, BLON) < 10.0


def test_geofence_preferred_over_manual():
    """3 geofence skupione + 2 ręczne rozrzucone → środek z geofence."""
    geo = [_s(*_shift(BLAT, BLON, d, 0), trigger="auto_geofence") for d in (-3, 0, 3)]
    man = [_s(*_shift(BLAT, BLON, 200, 0), trigger="manual"),
           _s(*_shift(BLAT, BLON, -200, 0), trigger="manual")]
    best = apm.select_best_pin(geo + man)
    assert best["source"] == "auto_geofence"
    assert apm.haversine_m(best["lat"], best["lon"], BLAT, BLON) < 10.0


def test_geofence_beats_trail_even_when_trail_more_numerous():
    """1 geofence + 5 trail rozrzuconych → pinezka z geofence (lepszy tier wygrywa)."""
    geo = [_s(BLAT, BLON, trigger="auto_geofence")]
    trail = [_s(*_shift(BLAT, BLON, 150 + 20 * i, 0), trigger="trail") for i in range(5)]
    best = apm.select_best_pin(geo + trail)
    assert best["source"] == "auto_geofence"
    assert apm.haversine_m(best["lat"], best["lon"], BLAT, BLON) < 5.0


def test_trail_only_never_high_confidence():
    """Nawet ciasne skupisko 3 trail = low (prawda-przyciskowa, nie fizyczna)."""
    trail = [_s(*_shift(BLAT, BLON, d, 0), trigger="trail") for d in (-2, 0, 2)]
    best = apm.select_best_pin(trail)
    assert best["source"] == "trail"
    assert best["confidence"] == "low"


def test_invalid_points_dropped():
    assert apm.select_best_pin([_s(0.0, 0.0), _s(None, None)]) is None
    best = apm.select_best_pin([_s(0.0, 0.0), _s(BLAT, BLON)])
    assert best["n_samples"] == 1


# --- normalizacja klucza (NIE address_id — recyklingowany) -------------------
def test_normalize_address():
    assert apm.normalize_address("  Rumiankowa  8   Białystok ") == "rumiankowa 8 białystok"
    # kod pocztowy usuwany
    assert "15-001" not in apm.normalize_address("15-001 Białystok Jurowiecka 11")
    # różne budynki NIE są scalane
    assert apm.normalize_address("Jana Pawła II 59") != apm.normalize_address("Jana Pawła II 59F")
    assert apm.normalize_address("") is None and apm.normalize_address(None) is None


# --- add_sample (opcja B: utwardzanie + rolling + dedup) ---------------------
KEY = "rumiankowa 8 białystok"


def test_add_sample_hardens_over_deliveries():
    store = {}
    # 1 dostawa = low; po 3 zgodnych = high
    apm.add_sample(store, "Rumiankowa 8 Białystok", _s(BLAT, BLON, order_id="o1", ts=1), now=1)
    assert store[KEY]["confidence"] == "low"
    for i, d in enumerate((2, -2), start=2):
        la, lo = _shift(BLAT, BLON, d, d)
        apm.add_sample(store, "Rumiankowa 8 Białystok", _s(la, lo, order_id=f"o{i}", ts=i), now=i)
    assert store[KEY]["confidence"] == "high"
    assert store[KEY]["address_text"] == "Rumiankowa 8 Białystok"
    assert store[KEY]["address_key"] == KEY


def test_add_sample_rolling_window():
    store = {}
    for i in range(apm.MAX_SAMPLES + 5):
        la, lo = _shift(BLAT, BLON, i % 5, 0)
        apm.add_sample(store, "Testowa 9", _s(la, lo, order_id=f"o{i}", ts=i), now=i)
    assert len(store["testowa 9"]["samples"]) == apm.MAX_SAMPLES


def test_add_sample_dedup_same_delivery():
    store = {}
    s = _s(BLAT, BLON, order_id="dup", ts=42)
    apm.add_sample(store, "Testowa 5", s, now=1)
    apm.add_sample(store, "Testowa 5", s, now=2)  # ta sama dostawa
    assert len(store["testowa 5"]["samples"]) == 1


def test_add_sample_ignores_invalid_point():
    store = {}
    apm.add_sample(store, "Testowa 5", _s(0.0, 0.0, order_id="z", ts=1), now=1)
    assert "testowa 5" not in store or not store["testowa 5"].get("samples")


def test_add_sample_ignores_blank_address():
    store = {}
    apm.add_sample(store, "  ", _s(BLAT, BLON, order_id="z", ts=1), now=1)
    assert store == {}


# --- store I/O ---------------------------------------------------------------
def test_store_roundtrip_atomic(tmp_path):
    p = os.path.join(str(tmp_path), "address_pins.json")
    store = {}
    apm.add_sample(store, "Rumiankowa 8 Białystok", _s(BLAT, BLON, order_id="o1", ts=1), now=1)
    apm.save_store(p, store)
    loaded = apm.load_store(p)
    assert loaded[KEY]["lat"] == store[KEY]["lat"]
    assert apm.load_store(os.path.join(str(tmp_path), "brak.json")) == {}


def test_public_pin_shape():
    store = {}
    apm.add_sample(store, "Rumiankowa 8 Białystok", _s(BLAT, BLON, order_id="o1", ts=1), now=1)
    pin = apm.public_pin(store[KEY])
    assert set(pin) == {"address_key", "address_text", "lat", "lon",
                        "confidence", "source", "deliveries", "updated_at"}
    assert apm.public_pin({}) is None
