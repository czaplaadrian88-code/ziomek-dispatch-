"""Regresja: fallback OSM/Nominatim gdy Google zawiódł / zwrócił out-of-bbox (2026-06-15).

Root cause (strażak W25 → diagnoza geokodu): Google nie ma w indeksie części
białostockich ulic („Proroka Eliasza", „Poniatowskiego" w Pieczurkach) → dopasowuje
miejscowość „Białystok" 22-540 na południu z pewnością ROOFTOP → bbox-reject → te
zlecenia nigdy nie idą auto (zawsze KOORD). Fix: realny fallback Nominatim bounded do
bboxu obszaru obsługi (flaga ENABLE_GEOCODE_NOMINATIM_FALLBACK, default OFF). Replay:
92% bbox-rejectów wyeliminowanych, 0 poza-bbox.

Ten test chroni: (1) flaga OFF = zachowanie legacy (reject, Nominatim NIE wołany),
(2) flaga ON + Google out-of-bbox + Nominatim in-bbox → odzysk, (3) Google None +
Nominatim in-bbox → odzysk, (4) Nominatim też out-of-bbox → reject (bbox-guard trzyma),
(5) pusty/śmieciowy adres „—” → None (guard, NIE centroid miasta).
"""
import json
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import common, geocoding  # noqa: E402
from dispatch_v2 import geocode_verify as gv  # noqa: E402

POISON = (52.505, 22.694)     # Google out-of-bbox (miejscowość „Białystok" 22-540)
GOOD = (53.1325, 23.1035)     # Nominatim in-bbox (realna ulica Białystok)


def main():
    results = {"pass": 0, "fail": 0}

    def expect(label, cond, detail=""):
        if cond:
            print(f"  ✅ {label}"); results["pass"] += 1
        else:
            print(f"  ❌ {label}  {detail}"); results["fail"] += 1

    tmpdir = Path(tempfile.mkdtemp(prefix="nominatim_fallback_test_"))
    orig = {
        "cache": geocoding.CACHE_PATH,
        "google": geocoding._google_geocode,
        "osrm": geocoding._osrm_fallback,
        "nom": gv.nominatim_geocode,
        "verify": getattr(geocoding, "_run_verification", None),
        "city": common.CITY_AWARE_GEOCODING,
        "guard": common.ENABLE_GEOCODE_BBOX_GUARD,
        "flag": common.ENABLE_GEOCODE_NOMINATIM_FALLBACK,
        "flagfn": common.flag,
    }
    nom_calls = {"n": 0}

    def _read_cache(path):
        try:
            return json.loads(Path(path).read_text())
        except Exception:
            return {}

    def _mock_nom(in_bbox=True):
        def _f(*a, **k):
            nom_calls["n"] += 1
            return GOOD if in_bbox else POISON
        return _f

    try:
        common.CITY_AWARE_GEOCODING = True
        common.ENABLE_GEOCODE_BBOX_GUARD = True
        geocoding._osrm_fallback = lambda *a, **k: None
        geocoding._run_verification = lambda *a, **k: None  # izoluj warstwę verify
        # Prod czyta flagę przez C.flag(flags.json), NIE module-const → bez tego
        # test 1 „flaga OFF" jest bezskuteczny (flags.json live ma
        # ENABLE_GEOCODE_NOMINATIM_FALLBACK=true). Wymuś, by C.flag honorował
        # stałą ustawianą w tym teście; pozostałe flagi deleguj do oryginału.
        common.flag = (lambda name, default=False, _o=orig["flagfn"]:
                       common.ENABLE_GEOCODE_NOMINATIM_FALLBACK
                       if name == "ENABLE_GEOCODE_NOMINATIM_FALLBACK"
                       else _o(name, default))

        # TEST 1: flaga OFF → out-of-bbox Google → reject, Nominatim NIE wołany
        print("\n=== test 1: flaga OFF → legacy reject, brak Nominatim ===")
        common.ENABLE_GEOCODE_NOMINATIM_FALLBACK = False
        nom_calls["n"] = 0
        geocoding.CACHE_PATH = tmpdir / "off.json"
        geocoding._google_geocode = lambda *a, **k: POISON
        gv.nominatim_geocode = _mock_nom(in_bbox=True)
        r = geocoding.geocode("Witosa 26", city="Białystok")
        expect("flaga OFF → None (reject)", r is None, f"got {r!r}")
        expect("flaga OFF → Nominatim NIE wołany", nom_calls["n"] == 0, f"calls={nom_calls['n']}")

        # TEST 2: flaga ON + Google out-of-bbox + Nominatim in-bbox → odzysk
        print("\n=== test 2: flaga ON → Nominatim ratuje out-of-bbox ===")
        common.ENABLE_GEOCODE_NOMINATIM_FALLBACK = True
        nom_calls["n"] = 0
        geocoding.CACHE_PATH = tmpdir / "on_recover.json"
        geocoding._google_geocode = lambda *a, **k: POISON
        gv.nominatim_geocode = _mock_nom(in_bbox=True)
        r = geocoding.geocode("Eliasza 8", city="Białystok")
        expect("flaga ON → odzysk coords in-bbox", r is not None and abs(r[0] - GOOD[0]) < 1e-9,
               f"got {r!r}")
        cache = _read_cache(geocoding.CACHE_PATH)
        expect("odzyskany wynik zapisany do cache (source=nominatim_fallback)",
               any(v.get("source") == "nominatim_fallback" for v in cache.values()),
               f"cache={cache!r}")

        # TEST 3: flaga ON + Google None + Nominatim in-bbox → odzysk (ścieżka 1)
        print("\n=== test 3: flaga ON → Google None → Nominatim ===")
        nom_calls["n"] = 0
        geocoding.CACHE_PATH = tmpdir / "on_none.json"
        geocoding._google_geocode = lambda *a, **k: None
        gv.nominatim_geocode = _mock_nom(in_bbox=True)
        r = geocoding.geocode("Poniatowskiego 2", city="Białystok")
        expect("Google None → Nominatim odzysk", r is not None and abs(r[0] - GOOD[0]) < 1e-9,
               f"got {r!r}")

        # TEST 4: flaga ON + Nominatim też out-of-bbox → reject (bbox-guard trzyma)
        print("\n=== test 4: Nominatim out-of-bbox → reject (guard trzyma) ===")
        geocoding.CACHE_PATH = tmpdir / "on_nom_oob.json"
        geocoding._google_geocode = lambda *a, **k: POISON
        gv.nominatim_geocode = _mock_nom(in_bbox=False)
        r = geocoding.geocode("Coś Tam 9", city="Białystok")
        expect("Nominatim out-of-bbox → None (reject)", r is None, f"got {r!r}")
        expect("brak zapisu do cache po podwójnym reject",
               _read_cache(geocoding.CACHE_PATH) == {}, f"cache={_read_cache(geocoding.CACHE_PATH)!r}")

        # TEST 5: flaga ON + pusty/śmieciowy adres „—” → None (guard, NIE centroid)
        print("\n=== test 5: pusty adres „—” → None (guard ulicy) ===")
        nom_calls["n"] = 0
        geocoding.CACHE_PATH = tmpdir / "on_empty.json"
        geocoding._google_geocode = lambda *a, **k: POISON
        gv.nominatim_geocode = _mock_nom(in_bbox=True)
        r = geocoding.geocode("—", city="Białystok")
        expect("pusty adres → None", r is None, f"got {r!r}")
        expect("pusty adres → Nominatim NIE zwraca coords (guard ulicy)",
               nom_calls["n"] == 0, f"calls={nom_calls['n']}")
    finally:
        geocoding.CACHE_PATH = orig["cache"]
        geocoding._google_geocode = orig["google"]
        geocoding._osrm_fallback = orig["osrm"]
        gv.nominatim_geocode = orig["nom"]
        if orig["verify"] is not None:
            geocoding._run_verification = orig["verify"]
        common.CITY_AWARE_GEOCODING = orig["city"]
        common.ENABLE_GEOCODE_BBOX_GUARD = orig["guard"]
        common.ENABLE_GEOCODE_NOMINATIM_FALLBACK = orig["flag"]
        common.flag = orig["flagfn"]
        import shutil
        shutil.rmtree(tmpdir, ignore_errors=True)

    total = results["pass"] + results["fail"]
    print("\n" + "=" * 60)
    print(f"NOMINATIM_FALLBACK: {results['pass']}/{total} PASS")
    print("=" * 60)
    sys.exit(0 if results["fail"] == 0 else 1)


if __name__ == "__main__":
    main()
