"""Regression: one canonical Aleja Jana Pawła II across geocoding layers.

Historical failure for bare ``Jana Pawła II 47`` combined two false signals:
the district matcher guessed Zawady and Nominatim received a prefix-less query
which disagreed with Google's correct point by 4.6 km.  No coordinate is part
of the fix; official street identity and house-number ranges are the oracle.
"""
import json
import sys
import time
from types import SimpleNamespace

import pytest

from dispatch_v2 import common as C
from dispatch_v2 import geocoding as G


@pytest.mark.parametrize(
    "raw",
    [
        "Jana Pawła II 47",
        "Aleja Jana Pawła II 47",
        "al. Jana Pawła II 47",
        "ul. Jana Pawła II 47",
    ],
)
def test_aleja_variants_share_canonical_address_and_cache_key(raw):
    assert C.canonicalize_geocode_address(raw) == "aleja jana pawła ii 47"
    assert G._normalize(raw, "Białystok") == "aleja jana pawła ii 47, białystok"


@pytest.mark.parametrize(
    ("address", "district"),
    [
        ("Jana Pawła II 47", "Młodych"),
        ("Aleja Jana Pawła II 47", "Młodych"),
        ("Aleja Jana Pawła II 54", "Antoniuk"),
        ("Aleja Jana Pawła II 59B/26", "Leśna Dolina"),
        ("Aleja Jana Pawła II 72/22", "Wysoki Stoczek"),
        ("Aleja Jana Pawła II 92", "Bacieczki"),
        ("Plac Jana Pawła II", "Centrum"),
        ("pl. Jana Pawła II", "Centrum"),
    ],
)
def test_official_prefix_and_house_ranges_select_district(address, district):
    assert C.drop_zone_from_address(address, "Białystok") == district


def test_known_avenue_without_official_building_range_is_not_guessed():
    assert C.drop_zone_from_address("Jana Pawła II 49", "Białystok") == "Unknown"


def test_existing_v327_aliases_feed_the_same_geocode_canonicalizer():
    assert C.canonicalize_geocode_address("ul. Bełzy 5/11") == "władysława bełzy 5/11"
    assert (
        C.canonicalize_geocode_address("M. Curie-Skłodowskiej 5")
        == "skłodowskiej-curie marii 5"
    )


def test_verifier_uses_mlodych_oracle_and_canonical_cross_source(monkeypatch):
    synthetic = (53.10, 23.10)
    seen = []

    def fake_nominatim(address, city, **kwargs):
        seen.append(address)
        return synthetic

    lookup_module = SimpleNamespace(
        get_district_lookup=lambda: SimpleNamespace(
            lookup=lambda lat, lon: "Młodych"
        )
    )
    monkeypatch.setitem(sys.modules, "dispatch_v2.district_reverse_lookup", lookup_module)
    monkeypatch.setattr(G._gv, "nominatim_geocode", fake_nominatim)
    monkeypatch.setattr(C, "ENABLE_GEOCODE_VERIFICATION", True)
    monkeypatch.setattr(C, "ENABLE_GEOCODE_DISTRICT_CHECK", True)
    monkeypatch.setattr(C, "ENABLE_GEOCODE_CROSS_SOURCE", True)

    verdict = G._run_verification(
        "Jana Pawła II 47",
        "Białystok",
        *synthetic,
        {"location_type": "ROOFTOP", "partial_match": True},
    )

    assert verdict["confidence"] == "low"  # soft partial_match only; not reject
    assert verdict["checks"]["expected_district"] == "Młodych"
    assert verdict["checks"]["actual_district"] == "Młodych"
    assert not any(reason.startswith("district ") for reason in verdict["reasons"])
    assert not any(reason.startswith("cross_source ") for reason in verdict["reasons"])
    assert seen == ["aleja jana pawła ii 47"]


def test_bare_47_passes_enforced_verification_and_uses_canonical_sources(
    monkeypatch, tmp_path
):
    """End-to-end reproducer: old negative key is bypassed and no reject occurs."""
    synthetic = (53.10, 23.10)
    google_queries = []
    nominatim_addresses = []

    cache_path = tmp_path / "geocode_cache.json"
    neg_cache_path = tmp_path / "geocode_neg_cache.json"
    # A live deployment can still contain the historical rejection.  The new
    # canonical key must not inherit poison from the old prefix-less key.
    neg_cache_path.write_text(
        json.dumps(
            {
                "jana pawła ii 47, białystok": {
                    "reason": "verify_reject",
                    "cached_at": time.time(),
                }
            }
        ),
        encoding="utf-8",
    )

    def fake_google(query, timeout=5.0):
        google_queries.append(query)
        # partial_match forces the verifier to consult the second source.
        return (*synthetic, {"location_type": "ROOFTOP", "partial_match": True})

    def fake_nominatim(address, city, **kwargs):
        nominatim_addresses.append(address)
        return synthetic

    lookup_module = SimpleNamespace(
        get_district_lookup=lambda: SimpleNamespace(
            lookup=lambda lat, lon: "Młodych"
        )
    )

    monkeypatch.setattr(G, "CACHE_PATH", cache_path)
    monkeypatch.setattr(G, "NEG_CACHE_PATH", neg_cache_path)
    monkeypatch.setattr(G, "_google_geocode", fake_google)
    monkeypatch.setattr(G._gv, "nominatim_geocode", fake_nominatim)
    monkeypatch.setattr(G, "_audit_log", lambda *args, **kwargs: None)
    monkeypatch.setitem(sys.modules, "dispatch_v2.district_reverse_lookup", lookup_module)
    monkeypatch.setattr(C, "ENABLE_GEOCODE_VERIFICATION", True)
    monkeypatch.setattr(C, "ENABLE_GEOCODE_DISTRICT_CHECK", True)
    monkeypatch.setattr(C, "ENABLE_GEOCODE_CROSS_SOURCE", True)

    def fake_flag(name, default=False):
        if name == "ENABLE_GEOCODE_VERIFICATION_ENFORCE":
            return True
        if name == "ENABLE_GEOCODE_NEGATIVE_CACHE":
            return True
        return default

    monkeypatch.setattr(C, "flag", fake_flag)

    assert G.geocode("Jana Pawła II 47", "Białystok") == synthetic
    assert google_queries == ["aleja jana pawła ii 47, Białystok, Polska"]
    assert nominatim_addresses == ["aleja jana pawła ii 47"]

    stored = json.loads(cache_path.read_text(encoding="utf-8"))
    assert set(stored) == {"aleja jana pawła ii 47, białystok"}


def test_legacy_positive_pin_survives_alias_key_migration(monkeypatch, tmp_path):
    cache_path = tmp_path / "geocode_cache.json"
    cache_path.write_text(
        json.dumps(
            {
                "jana pawła ii 47, białystok": {
                    "lat": 53.10,
                    "lon": 23.10,
                    "source": "manual_override",
                    "cached_at": "pinned:test",
                }
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(G, "CACHE_PATH", cache_path)
    monkeypatch.setattr(G, "_audit_log", lambda *args, **kwargs: None)
    monkeypatch.setattr(
        G,
        "_google_geocode",
        lambda *args, **kwargs: pytest.fail("legacy positive pin must avoid network"),
    )

    assert G.geocode("Jana Pawła II 47", "Białystok") == (53.10, 23.10)
    assert set(json.loads(cache_path.read_text(encoding="utf-8"))) == {
        "jana pawła ii 47, białystok"
    }


def test_restaurant_address_twin_uses_canonical_avenue(monkeypatch, tmp_path):
    queries = []

    def fake_google(query, timeout=5.0):
        queries.append(query)
        return (53.10, 23.10, {"location_type": "ROOFTOP"})

    monkeypatch.setattr(G, "RESTAURANT_CACHE_PATH", tmp_path / "restaurants.json")
    monkeypatch.setattr(G, "_google_geocode", fake_google)
    monkeypatch.setattr(G, "_audit_log", lambda *args, **kwargs: None)

    assert G.geocode_restaurant(
        "Restauracja testowa", "Jana Pawła II 47", "Białystok"
    ) == (53.10, 23.10)
    assert queries == [
        "Restauracja testowa, aleja jana pawła ii 47, Białystok"
    ]
