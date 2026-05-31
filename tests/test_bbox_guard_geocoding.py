"""Regresja dla bbox guard geocodingu (geo-poison prevention, zadanie #4, 2026-05-30).

Scenariusz bugu: Google zwraca coords poza obszarem obsługi dla ambiguous/parser-artifact
query (np. "Witosa 26, Klepacze" → 52.505,22.694 ~70km od Białegostoku, albo sentinel
center-of-Poland 51.9194,19.1451). Bez walidacji w momencie geocode te trucizny lądowały
w cache NA STAŁE; jedyny downstream catch to R6 fail-safe (huge bag_time → KOORD).

Fix: `_in_service_bbox` odrzuca out-of-bbox wynik PRZED zapisem do cache w obu ścieżkach
(geocode + geocode_restaurant). Default bbox Białystok+okolice (lat 52.85-53.35,
lon 22.85-23.45), env-overridable, kill-switch ENABLE_GEOCODE_BBOX_GUARD. Ten test
chroni regresję: trucizna NIE wraca jako wynik i NIE jest cache'owana.
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import common, geocoding  # noqa: E402

POISON = (52.505, 22.694)        # "Witosa 26, Klepacze" — realna trucizna z cache
SENTINEL = (51.9194, 19.1451)    # center-of-Poland fallback Google (4x w cache)
GOOD = (53.1325, 23.1035)        # "Witosa 26, Białystok" — poprawny wynik in-bbox


def main():
    results = {"pass": 0, "fail": 0}

    def expect(label, cond, detail=""):
        if cond:
            print(f"  ✅ {label}")
            results["pass"] += 1
        else:
            print(f"  ❌ {label}  {detail}")
            results["fail"] += 1

    # ---------- TEST 1: _in_service_bbox unit ----------
    print("\n=== test 1: _in_service_bbox klasyfikuje coords ===")
    expect("in-bbox Białystok center → True", geocoding._in_service_bbox(*GOOD))
    expect("poison Klepacze → False", not geocoding._in_service_bbox(*POISON),
           f"coords={POISON}")
    expect("sentinel center-of-Poland → False", not geocoding._in_service_bbox(*SENTINEL),
           f"coords={SENTINEL}")
    expect("None coords → False (defensywnie poison)",
           not geocoding._in_service_bbox(None, None))

    # ---------- izolacja cache na temp + mock geocoderów ----------
    tmpdir = Path(tempfile.mkdtemp(prefix="bbox_guard_test_"))
    orig_cache = geocoding.CACHE_PATH
    orig_rest = geocoding.RESTAURANT_CACHE_PATH
    orig_google = geocoding._google_geocode
    orig_osrm = geocoding._osrm_fallback
    orig_city_flag = common.CITY_AWARE_GEOCODING
    orig_guard_flag = common.ENABLE_GEOCODE_BBOX_GUARD

    def _read_cache(path):
        try:
            return json.loads(Path(path).read_text())
        except Exception:
            return {}

    try:
        common.CITY_AWARE_GEOCODING = True
        common.ENABLE_GEOCODE_BBOX_GUARD = True
        geocoding._osrm_fallback = lambda *a, **k: None  # izoluj od OSRM

        # ---------- TEST 2: geocode out-of-bbox → None + brak cache write ----------
        print("\n=== test 2: geocode() trucizna odrzucona, NIE cache'owana ===")
        geocoding.CACHE_PATH = tmpdir / "addr_poison.json"
        geocoding._google_geocode = lambda *a, **k: POISON
        r = geocoding.geocode("Witosa 26", city="Białystok")
        expect("geocode trucizna → None", r is None, f"got {r!r}")
        expect("cache pusty po odrzuceniu trucizny", _read_cache(geocoding.CACHE_PATH) == {},
               f"cache={_read_cache(geocoding.CACHE_PATH)!r}")

        # ---------- TEST 3: geocode in-bbox → passthrough + cache write ----------
        print("\n=== test 3: geocode() poprawny wynik przechodzi + cache ===")
        geocoding.CACHE_PATH = tmpdir / "addr_good.json"
        geocoding._google_geocode = lambda *a, **k: GOOD
        r = geocoding.geocode("Witosa 26", city="Białystok")
        expect("geocode in-bbox → coords", r == GOOD, f"got {r!r}")
        cache = _read_cache(geocoding.CACHE_PATH)
        expect("in-bbox wynik zapisany do cache", len(cache) == 1 and
               any(abs(v.get("lat") - GOOD[0]) < 1e-9 for v in cache.values()),
               f"cache={cache!r}")

        # ---------- TEST 4: geocode_restaurant out-of-bbox → None + brak cache ----------
        print("\n=== test 4: geocode_restaurant() trucizna odrzucona ===")
        geocoding.RESTAURANT_CACHE_PATH = tmpdir / "rest_poison.json"
        geocoding._google_geocode = lambda *a, **k: SENTINEL
        r = geocoding.geocode_restaurant("Pizza Widmo", city="Białystok")
        expect("geocode_restaurant trucizna → None", r is None, f"got {r!r}")
        expect("restaurant cache pusty po odrzuceniu",
               _read_cache(geocoding.RESTAURANT_CACHE_PATH) == {},
               f"cache={_read_cache(geocoding.RESTAURANT_CACHE_PATH)!r}")

        # ---------- TEST 5: guard OFF → legacy passthrough (kill-switch działa) ----------
        print("\n=== test 5: ENABLE_GEOCODE_BBOX_GUARD=False → trucizna przechodzi ===")
        common.ENABLE_GEOCODE_BBOX_GUARD = False
        geocoding.CACHE_PATH = tmpdir / "addr_guard_off.json"
        geocoding._google_geocode = lambda *a, **k: POISON
        r = geocoding.geocode("Witosa 26", city="Białystok")
        expect("guard OFF → trucizna przechodzi (legacy)", r == POISON, f"got {r!r}")
        expect("guard OFF → trucizna zapisana (legacy)",
               len(_read_cache(geocoding.CACHE_PATH)) == 1,
               f"cache={_read_cache(geocoding.CACHE_PATH)!r}")
    finally:
        geocoding.CACHE_PATH = orig_cache
        geocoding.RESTAURANT_CACHE_PATH = orig_rest
        geocoding._google_geocode = orig_google
        geocoding._osrm_fallback = orig_osrm
        common.CITY_AWARE_GEOCODING = orig_city_flag
        common.ENABLE_GEOCODE_BBOX_GUARD = orig_guard_flag
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    # ---------- FINAL ----------
    total = results["pass"] + results["fail"]
    print()
    print("=" * 60)
    print(f"BBOX_GUARD_GEOCODING: {results['pass']}/{total} PASS")
    print("=" * 60)
    sys.exit(0 if results["fail"] == 0 else 1)


if __name__ == "__main__":
    main()
