"""V3.27 Bug Z Step C+D+E tests — districts coverage extension + aliases.

Per Adrian DODATEK B:
- Step C: 3 priority mappings (Bełzy/Filipowicza/Curie-Skłodowska) — Nominatim HIGH
- Step D: street name aliases (M. → Marii, Skłodowskiej → Skłodowskiej-Curie etc.)
- Step E: best-effort 4 nowych streets z top-100 unmapped

Run: python3 tests/test_v327_districts_coverage.py
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import common as C  # noqa: E402


# ─────────────────────────────────────────────────────────
# Step C — Priority 3 mappings
# ─────────────────────────────────────────────────────────

def test_priority_belzy_to_bialostoczek():
    """Władysława Bełzy → Białostoczek (Nominatim 53.1526,23.1566 HIGH)."""
    cases = [
        "Władysława Bełzy 5/11",
        "Władysława Bełzy 12",
        "Bełzy 5",                      # alias
        "ul. Władysława Bełzy 1",       # prefix
        "Wł. Bełzy 8",                   # alias z initial
    ]
    for addr in cases:
        zone = C.drop_zone_from_address(addr, "Białystok")
        assert zone == "Białostoczek", \
            f"{addr!r} expected Białostoczek, got {zone}"


def test_priority_skłodowskiej_curie_to_piaski():
    """Marii Skłodowskiej-Curie → Piaski (Nominatim 53.1289,23.1586 HIGH).
    Removed from Centrum (was duplicated). All variants → Piaski via aliases.
    """
    cases = [
        "Marii Skłodowskiej-Curie 5",
        "Skłodowskiej-Curie 12",
        "M. Curie-Skłodowskiej 5",
        "Curie-Skłodowskiej 5",
        "Skłodowskiej 13/15",            # shorthand
        "M. Skłodowskiej-Curie 1",
        "Marii Curie-Skłodowskiej 8",   # inverted Polish name
    ]
    for addr in cases:
        zone = C.drop_zone_from_address(addr, "Białystok")
        assert zone == "Piaski", \
            f"{addr!r} expected Piaski, got {zone}"


def test_priority_filipowicza_białystok_to_dojlidy():
    """Feliksa Filipowicza Białystok → Dojlidy (Nominatim 53.0984,23.1338 HIGH).
    Distinct from Kleosin Filipowicza (handled by city-aware geocoding).
    """
    cases = [
        "Feliksa Filipowicza 5",
        "Feliksa Filipowicza 12/46",
        "Filipowicza 5",                 # alias gdy Białystok
        "F. Filipowicza 8",              # initial alias
    ]
    for addr in cases:
        zone = C.drop_zone_from_address(addr, "Białystok")
        assert zone == "Dojlidy", \
            f"{addr!r} (Białystok) expected Dojlidy, got {zone}"


def test_kleosin_filipowicza_preserved():
    """V3.27: city-aware geocoding preserved — Filipowicza w Kleosinie → Kleosin."""
    cases = [
        "Filipowicza 5",
        "Filipowicza 12/46",
        "Feliksa Filipowicza 5",
    ]
    for addr in cases:
        zone = C.drop_zone_from_address(addr, "Kleosin")
        assert zone == "Kleosin", \
            f"{addr!r} (Kleosin) expected Kleosin, got {zone}"


# ─────────────────────────────────────────────────────────
# Step D — Street name aliases
# ─────────────────────────────────────────────────────────

def test_normalizer_basic():
    """V3.27 _v327_normalize_street_for_matching basic cases."""
    f = C._v327_normalize_street_for_matching
    # Identity (no alias)
    assert f("sienkiewicza henryka 5") == "sienkiewicza henryka 5"
    # Alias hit
    assert f("skłodowskiej 13/15") == "skłodowskiej-curie marii 13/15"
    assert f("m. curie-skłodowskiej 5") == "skłodowskiej-curie marii 5"
    assert f("bełzy 5/11") == "władysława bełzy 5/11"
    assert f("filipowicza 5") == "feliksa filipowicza 5"
    # Empty
    assert f("") == ""
    # No number
    assert f("skłodowskiej") == "skłodowskiej-curie marii"


def test_aliases_dict_completeness():
    """All 3 priority streets have alias entries."""
    assert "skłodowskiej" in C.V327_STREET_ALIASES
    assert "m. curie-skłodowskiej" in C.V327_STREET_ALIASES
    assert "bełzy" in C.V327_STREET_ALIASES
    assert "filipowicza" in C.V327_STREET_ALIASES


# ─────────────────────────────────────────────────────────
# Step E — Best-effort 4 new streets
# ─────────────────────────────────────────────────────────

def test_best_effort_sudecka_skorupy():
    """Sudecka → Skorupy (Nominatim 53.1283,23.1971 HIGH)."""
    assert C.drop_zone_from_address("Sudecka 5", "Białystok") == "Skorupy"


def test_best_effort_bitwy_białostockiej():
    """Bitwy Białostockiej → Białostoczek (Nominatim 53.1486,23.1615 HIGH)."""
    assert C.drop_zone_from_address("Bitwy Białostockiej 5", "Białystok") == "Białostoczek"


def test_best_effort_depowa():
    """Depowa → Bema (Nominatim 53.1219,23.1284 HIGH)."""
    assert C.drop_zone_from_address("Depowa 5", "Białystok") == "Bema"


# ─────────────────────────────────────────────────────────
# Regression — existing streets unchanged
# ─────────────────────────────────────────────────────────

def test_regression_existing_mappings_unchanged():
    """V3.27 NIE zmienia istniejących mappings."""
    cases = [
        ("Sienkiewicza henryka 5", "Białystok", "Bojary"),  # exact match
        ("Mickiewicza 5", "Białystok", "Centrum"),
        ("Antoniukowska 30", "Białystok", "Antoniuk"),
        ("Lipowa 14", "Białystok", "Centrum"),
        ("Kraszewskiego 5", "Białystok", "Bojary"),
        ("Wierzbowa 5", "Białystok", "Antoniuk"),
    ]
    for addr, city, expected in cases:
        actual = C.drop_zone_from_address(addr, city)
        assert actual == expected, \
            f"REGRESSION {addr!r}/{city!r}: expected {expected}, got {actual}"


def test_outside_city_zones_unchanged():
    """V3.27 NIE łamie outside-city detection."""
    cases = [
        ("Filipowicza 5", "Kleosin", "Kleosin"),
        ("Niepodległości 5", "Choroszcz", "Choroszcz"),
        ("Białostocka 5", "Wasilków", "Wasilków"),
    ]
    for addr, city, expected in cases:
        actual = C.drop_zone_from_address(addr, city)
        assert actual == expected, \
            f"OUTSIDE-CITY REGRESSION {addr!r}/{city!r}: expected {expected}, got {actual}"


# ─────────────────────────────────────────────────────────
# #468509 reproduction post-coverage-fix
# ─────────────────────────────────────────────────────────

def test_proposal_468509_post_coverage():
    """#468509 reproduction post Step C+D+E:
    Bag drops: Bełzy 5/11 (Białostoczek N) + Filipowicza 12/46 city='Kleosin' (SW).
    New drop: Artyleryjska 2a/49 (Centrum CENTER).
    Min factor = 0.0 (N ↔ SW OPPOSITE per QUADRANT).
    """
    bełzy_zone = C.drop_zone_from_address("Bełzy 5/11", "Białystok")
    filipowicza_zone = C.drop_zone_from_address("Filipowicza 12/46", "Kleosin")
    artyleryjska_zone = C.drop_zone_from_address("Artyleryjska 2a/49", "Białystok")
    assert bełzy_zone == "Białostoczek"
    assert filipowicza_zone == "Kleosin"
    assert artyleryjska_zone == "Centrum"
    factor = C.min_drop_proximity_factor([artyleryjska_zone, bełzy_zone, filipowicza_zone])
    assert factor == 0.0, f"#468509: expected 0.0 (cross-quadrant), got {factor}"


def test_legitimate_same_quadrant_post_coverage():
    """Post-coverage: legitne same-district pair NIE penalty.
    Pre-V3.27: Bełzy → Unknown → 0.0 (false-positive penalty).
    Post-V3.27: Bełzy → Białostoczek, hipotetyczne sąsiednie → 1.0 (same district).
    """
    bełzy_zone = C.drop_zone_from_address("Bełzy 5/11", "Białystok")
    bitwy_zone = C.drop_zone_from_address("Bitwy Białostockiej 12", "Białystok")
    assert bełzy_zone == "Białostoczek"
    assert bitwy_zone == "Białostoczek"
    factor = C.min_drop_proximity_factor([bełzy_zone, bitwy_zone])
    assert factor == 1.0, \
        f"Legit same-district bundle: expected 1.0 no penalty, got {factor}"


if __name__ == "__main__":
    test_priority_belzy_to_bialostoczek()
    print("test_priority_belzy_to_bialostoczek: PASS")
    test_priority_skłodowskiej_curie_to_piaski()
    print("test_priority_skłodowskiej_curie_to_piaski: PASS")
    test_priority_filipowicza_białystok_to_dojlidy()
    print("test_priority_filipowicza_białystok_to_dojlidy: PASS")
    test_kleosin_filipowicza_preserved()
    print("test_kleosin_filipowicza_preserved: PASS")
    test_normalizer_basic()
    print("test_normalizer_basic: PASS")
    test_aliases_dict_completeness()
    print("test_aliases_dict_completeness: PASS")
    test_best_effort_sudecka_skorupy()
    print("test_best_effort_sudecka_skorupy: PASS")
    test_best_effort_bitwy_białostockiej()
    print("test_best_effort_bitwy_białostockiej: PASS")
    test_best_effort_depowa()
    print("test_best_effort_depowa: PASS")
    test_regression_existing_mappings_unchanged()
    print("test_regression_existing_mappings_unchanged: PASS")
    test_outside_city_zones_unchanged()
    print("test_outside_city_zones_unchanged: PASS")
    test_proposal_468509_post_coverage()
    print("test_proposal_468509_post_coverage: PASS")
    test_legitimate_same_quadrant_post_coverage()
    print("test_legitimate_same_quadrant_post_coverage: PASS")
    print("ALL 13/13 PASS")
