"""GEO-02 guard (2026-06-06) — bootstrap nie nadpisuje ręcznych/pusty-street coords.

Dry-run GEO-02 wykazał 7 wpisów source=adrian_manual_* z PUSTYM street → blind
re-geocode zwracał środek Białegostoku (śmieć w bbox). Guard: PRESERVE takich wpisów.

Standalone executable. Weryfikuje:
1. is_preserved_source — adrian_manual_*/manual_override → True, google/None → False
2. manual_source_preserved — source=adrian_manual + pusty street → results == existing (NIE city-center)
3. empty_street_kept_prev — pusty street + prev google coords → preserved (NIE geocode)
4. empty_street_no_prev_failed — pusty street + brak prev → failed, NIE w results
5. normal_street_geocoded — realny street → geocode wynik (nowe coords + source=google)
6. geocode_fail_kept_prev — geocode None + prev → preserved (brak data-loss)
7. write_includes_preserved — results zawierają preserved (brak gubienia restauracji)
"""
import sys
sys.path.insert(0, '/root/.openclaw/workspace/scripts')
from dispatch_v2 import bootstrap_restaurants as B

CITY_CENTER = (53.13248859999999, 23.1688403)


def _fake_geocode(q):
    """Mock: pusty→centrum (śmieć), Wiejska→real, FAILME→None, inne→punkt."""
    if not (q or '').strip():
        return CITY_CENTER
    if 'Wiejska' in q:
        return (53.114585, 23.147187)
    if 'FAILME' in q:
        return None
    return (53.200000, 23.200000)


def _run(addresses, existing):
    orig = B.geocode
    B.geocode = _fake_geocode
    try:
        return B.geocode_all(addresses, existing)
    finally:
        B.geocode = orig


def test_is_preserved_source():
    assert B._is_preserved_source({"source": "adrian_manual_2026-05-21"}) is True
    assert B._is_preserved_source({"source": "manual_override"}) is True
    assert B._is_preserved_source({"source": "google"}) is False
    assert B._is_preserved_source({}) is False
    assert B._is_preserved_source(None) is False


def test_manual_source_preserved():
    existing = {"232": {"address_id": 232, "company": "Dr Tusz", "street": "",
                        "source": "adrian_manual_2026-05-21", "lat": 53.13133, "lng": 23.13880}}
    addresses = {"232": {"company": "Dr Tusz", "street": ""}}
    results, failed, preserved = _run(addresses, existing)
    assert 232 in results, "manual wpis wypadł z results"
    assert (results[232]["lat"], results[232]["lng"]) == (53.13133, 23.13880), "ręczne coords nadpisane!"
    assert results[232]["source"] == "adrian_manual_2026-05-21"
    assert abs(results[232]["lat"] - CITY_CENTER[0]) > 0.001, "to city-center śmieć!"
    assert any(p[0] == 232 and p[3] == "manual_source" for p in preserved)


def test_empty_street_kept_prev():
    existing = {"999": {"company": "X", "street": "", "source": "google", "lat": 53.10, "lng": 23.10}}
    addresses = {"999": {"company": "X", "street": ""}}
    results, failed, preserved = _run(addresses, existing)
    assert 999 in results and (results[999]["lat"], results[999]["lng"]) == (53.10, 23.10)
    assert any(p[0] == 999 and p[3] == "empty_street_kept_prev" for p in preserved)


def test_empty_street_no_prev_failed():
    addresses = {"888": {"company": "Y", "street": ""}}
    results, failed, preserved = _run(addresses, {})
    assert 888 not in results, "pusty street bez prev NIE powinien trafić do results (city-center)"
    assert any(f[0] == 888 for f in failed)


def test_normal_street_geocoded():
    addresses = {"3": {"company": "Retrospekcja", "street": "Wiejska 65", "post_code": "15-351", "city": "Białystok"}}
    results, failed, preserved = _run(addresses, {})
    assert 3 in results
    assert results[3]["source"] == "google"
    assert (results[3]["lat"], results[3]["lng"]) == (53.114585, 23.147187)
    assert not any(p[0] == 3 for p in preserved)


def test_geocode_fail_kept_prev():
    existing = {"77": {"company": "Z", "street": "FAILME 1", "source": "google", "lat": 53.05, "lng": 23.05}}
    addresses = {"77": {"company": "Z", "street": "FAILME 1"}}
    results, failed, preserved = _run(addresses, existing)
    assert 77 in results and (results[77]["lat"], results[77]["lng"]) == (53.05, 23.05), "geocode fail zgubił restaurację"
    assert any(p[0] == 77 and p[3] == "geocode_fail_kept_prev" for p in preserved)


def test_write_includes_preserved():
    existing = {"232": {"company": "Dr Tusz", "street": "", "source": "adrian_manual_2026-05-21", "lat": 53.13, "lng": 23.14}}
    addresses = {"232": {"company": "Dr Tusz", "street": ""}, "3": {"company": "Retro", "street": "Wiejska 65"}}
    results, failed, preserved = _run(addresses, existing)
    out = {str(aid): r for aid, r in results.items()}
    assert "232" in out and "3" in out, "preserved wypadł z zapisu (data-loss)!"


def main():
    tests = [
        ('is_preserved_source', test_is_preserved_source),
        ('manual_source_preserved', test_manual_source_preserved),
        ('empty_street_kept_prev', test_empty_street_kept_prev),
        ('empty_street_no_prev_failed', test_empty_street_no_prev_failed),
        ('normal_street_geocoded', test_normal_street_geocoded),
        ('geocode_fail_kept_prev', test_geocode_fail_kept_prev),
        ('write_includes_preserved', test_write_includes_preserved),
    ]
    print('=' * 60)
    print('GEO-02 bootstrap preserve-guard tests')
    print('=' * 60)
    passed = 0
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f'  ✅ {name}')
            passed += 1
        except AssertionError as e:
            print(f'  ❌ {name}: {e}')
            failed.append(name)
        except Exception as e:
            print(f'  ❌ {name}: UNEXPECTED {type(e).__name__}: {e}')
            failed.append(name)
    print('=' * 60)
    print(f'{passed}/{len(tests)} PASS')
    if failed:
        print(f'FAILED: {failed}')
        return 1
    return 0


if __name__ == '__main__':
    sys.exit(main())
