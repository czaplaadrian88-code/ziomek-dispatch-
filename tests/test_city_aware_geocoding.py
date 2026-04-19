"""Regresja dla city-aware geocoding fix (2026-04-19).

Scenariusz bugu: Zlecenie #466975 Chicago Pizza → Kleosin (Kraszewskiego 10a/15)
fałszywie zbundlowane z #466978 Retrospekcja → Białystok (Kraszewskiego 21A/20)
jako "po drodze 0.3km". Realny dystans Kleosin↔Białystok ≈ 5.3km. Cache
mapował "kraszewskiego 10a, białystok" → coords Białystok zamiast Kleosin
(normalize dokleiła domyślny hint_city="Białystok").

Fix: panel_client parsuje lokalizacja.name jako delivery_city; geocode wymaga
city explicit (flag CITY_AWARE_GEOCODING=True). Ten test chroni regresję.
"""
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import common, geocoding, panel_client  # noqa: E402


def _hav_km(a, b):
    lat1, lon1 = a
    lat2, lon2 = b
    R = 6371
    dl = math.radians(lat2 - lat1)
    dln = math.radians(lon2 - lon1)
    x = (
        math.sin(dl / 2) ** 2
        + math.cos(math.radians(lat1)) * math.cos(math.radians(lat2)) * math.sin(dln / 2) ** 2
    )
    return 2 * R * math.asin(math.sqrt(x))


def main():
    results = {"pass": 0, "fail": 0}

    def expect(label, cond, detail=""):
        if cond:
            print(f"  ✅ {label}")
            results["pass"] += 1
        else:
            print(f"  ❌ {label}  {detail}")
            results["fail"] += 1

    # ---------- TEST 1: order_city_parsed_from_panel ----------
    print("\n=== test 1: order_city_parsed_from_panel (466975 Kleosin fixture) ===")
    raw_466975 = {
        "id": 466975,
        "id_status_zamowienia": 3,
        "street": "Kraszewskiego 10a",
        "nr_domu": "",
        "nr_mieszkania": "15",
        "czas_odbioru": "35",
        "czas_odbioru_timestamp": "2026-04-19 10:53:00",
        "created_at": "2026-04-19T08:53:00.000000Z",
        "id_kurier": 509,
        "id_location_to": 12,
        "address": {"id": 45, "name": "Chicago Pizza", "street": "Mickiewicza 1", "city": "Białystok"},
        "lokalizacja": {"id": 12, "name": "Kleosin"},
    }
    r = panel_client.normalize_order(raw_466975)
    expect("delivery_city from lokalizacja.name == 'Kleosin'", r["delivery_city"] == "Kleosin",
           f"got {r.get('delivery_city')!r}")
    expect("pickup_city from address.city == 'Białystok'", r["pickup_city"] == "Białystok",
           f"got {r.get('pickup_city')!r}")
    expect("id_location_to propagated", r["id_location_to"] == 12)

    # ---------- TEST 2: geocoder_requires_city (flag True fail loud) ----------
    print("\n=== test 2: geocoder requires city when flag True ===")
    orig_flag = common.CITY_AWARE_GEOCODING
    try:
        common.CITY_AWARE_GEOCODING = True
        result = geocoding.geocode("Kraszewskiego 10a/15")
        expect("geocode returns None without city", result is None,
               f"got {result!r}")

        # ---------- TEST 3: legacy_fallback when flag False ----------
        print("\n=== test 3: legacy fallback when flag False ===")
        common.CITY_AWARE_GEOCODING = False
        # Cache hit spodziewany dla starego klucza "kraszewskiego 14, białystok"
        r = geocoding.geocode("Kraszewskiego 14/13")
        expect("legacy mode uses Białystok default → cache hit",
               r is not None and 53.1 < r[0] < 53.2,
               f"got {r!r}")
    finally:
        common.CITY_AWARE_GEOCODING = orig_flag

    # ---------- TEST 4: cache_key_format_new_schema ----------
    print("\n=== test 4: cache key format distinguishes cities ===")
    k_kleo = geocoding._normalize("Kraszewskiego 10a/15", "Kleosin")
    k_bial = geocoding._normalize("Kraszewskiego 10a/15", "Białystok")
    expect("Kleosin key has 'kleosin' suffix", "kleosin" in k_kleo,
           f"k_kleo={k_kleo!r}")
    expect("Białystok key has 'białystok' suffix", "białystok" in k_bial,
           f"k_bial={k_bial!r}")
    expect("Kleosin key != Białystok key (no cache collision)", k_kleo != k_bial)

    # ---------- TEST 5: restaurant_vs_customer_city_differ (Warszawa-ready) ----------
    print("\n=== test 5: restaurant vs customer city can differ (multi-city) ===")
    raw_warszawa = {
        "id": 999999,
        "id_status_zamowienia": 3,
        "street": "Puławska",
        "nr_domu": "10",
        "nr_mieszkania": None,
        "czas_odbioru": "35",
        "czas_odbioru_timestamp": "2026-04-19 14:32:49",
        "created_at": "2026-04-19T11:57:49.000000Z",
        "id_kurier": 100,
        "id_location_to": 999,
        "address": {"id": 500, "name": "Pizza WWA", "street": "Puławska 1", "city": "Warszawa"},
        "lokalizacja": {"id": 999, "name": "Piaseczno"},
    }
    r = panel_client.normalize_order(raw_warszawa)
    expect("pickup_city=Warszawa, delivery_city=Piaseczno",
           r["pickup_city"] == "Warszawa" and r["delivery_city"] == "Piaseczno",
           f"pickup={r.get('pickup_city')!r} delivery={r.get('delivery_city')!r}")

    # ---------- TEST 6: empty_city_fallback ----------
    print("\n=== test 6: empty/None lokalizacja → delivery_city is None ===")
    for case_label, loc_val in [("None", None), ("empty dict", {}),
                                 ("empty name string", {"id": 1, "name": ""}),
                                 ("whitespace name", {"id": 1, "name": "   "})]:
        raw = {**raw_466975, "lokalizacja": loc_val}
        r = panel_client.normalize_order(raw)
        expect(f"delivery_city None when lokalizacja={case_label}",
               r["delivery_city"] is None,
               f"got {r.get('delivery_city')!r}")

    # ---------- TEST 7: kleosin_vs_bialystok_not_bundled (core regresja) ----------
    print("\n=== test 7: Kleosin ↔ Białystok Kraszewskiego distance >= 2.5km ===")
    # Hardcoded from real geocoding (w logach 2026-04-12 i cache)
    kleosin_10a = (53.10421, 23.11854)       # Kraszewskiego 10a, Kleosin (correct)
    bialystok_21a = (53.13779, 23.17564)     # J.I. Kraszewskiego 21A, Białystok (correct)
    bialystok_10a_WRONG = (53.13619, 23.17273)  # Kraszewskiego 10a — ZŁE, zcachowane na Białystok

    km_correct = _hav_km(kleosin_10a, bialystok_21a)
    km_wrong = _hav_km(bialystok_10a_WRONG, bialystok_21a)

    expect(f"correct Kleosin↔Białystok >= 2.5km (got {km_correct:.2f}km)",
           km_correct >= 2.5)
    expect(f"buggy Białystok↔Białystok was ~0.26km (got {km_wrong:.2f}km, confirms bug mechanism)",
           km_wrong < 0.5)
    expect("bug delta > 4.5km (realny ↔ błędny)",
           abs(km_correct - km_wrong) > 4.5,
           f"delta={abs(km_correct-km_wrong):.2f}")

    # ---------- FINAL ----------
    total = results["pass"] + results["fail"]
    print()
    print("=" * 60)
    print(f"CITY_AWARE_GEOCODING: {results['pass']}/{total} PASS")
    print("=" * 60)
    sys.exit(0 if results["fail"] == 0 else 1)


if __name__ == "__main__":
    main()
