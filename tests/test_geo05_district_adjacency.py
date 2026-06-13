"""GEO-05 (audyt 2026-06-13): regresja spójności mapy BIALYSTOK_DISTRICT_ADJACENCY.

Pilnuje:
- symetrii (A sąsiad B ⇒ B sąsiad A),
- braku self-reference,
- wszystkie nazwy w adjacency są walidnymi dzielnicami (DISTRICTS ∪ OUTSIDE),
- locka fixa Mickiewicza↔Dojlidy Górne (fałszywe sąsiedztwo usunięte 2026-06-13),
- Dojlidy Górne = dokładnie {Dojlidy},
- sanity centroidowy (intra-city): żadna para sąsiednia nie jest absurdalnie
  daleko — próg z marginesem na duże dzielnice (5.0 km haversine centroidów).

Centroidy liczone empirycznie z geocode_cache.json (jeśli dostępny); gdy brak
cache → test centroidowy skipuje (pozostałe kontrole strukturalne działają zawsze).

Run: /root/.openclaw/venvs/dispatch/bin/python -m pytest tests/test_geo05_district_adjacency.py -q
"""
import json
import os
import statistics
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import common as C  # noqa: E402
from dispatch_v2.districts_data import (  # noqa: E402
    BIALYSTOK_DISTRICTS,
    BIALYSTOK_OUTSIDE_CITY_ZONES,
)

ADJ = C.BIALYSTOK_DISTRICT_ADJACENCY
VALID = set(BIALYSTOK_DISTRICTS) | set(BIALYSTOK_OUTSIDE_CITY_ZONES)
GEOCODE_CACHE = "/root/.openclaw/workspace/dispatch_state/geocode_cache.json"


# ── kontrole strukturalne (zawsze) ──────────────────────────────────────────

def test_adjacency_symmetric():
    """A sąsiad B ⇒ B sąsiad A (brak par asymetrycznych)."""
    asym = []
    for a, nbrs in ADJ.items():
        for b in nbrs:
            assert b in ADJ, f"{a} -> {b}: {b} nie jest kluczem w adjacency"
            if a not in ADJ[b]:
                asym.append((a, b))
    assert not asym, f"Pary asymetryczne: {asym}"


def test_no_self_reference():
    for a, nbrs in ADJ.items():
        assert a not in nbrs, f"{a} jest własnym sąsiadem"


def test_all_names_valid_districts():
    names = set(ADJ) | {b for nbrs in ADJ.values() for b in nbrs}
    bad = names - VALID
    assert not bad, f"Nazwy spoza DISTRICTS/OUTSIDE: {sorted(bad)}"


# ── lock fixa GEO-05 2026-06-13 ─────────────────────────────────────────────

def test_mickiewicza_not_adjacent_dojlidy_gorne():
    """Fałszywe sąsiedztwo usunięte: Dojlidy leży MIĘDZY nimi (4.38 km centroidów)."""
    assert "Dojlidy Górne" not in ADJ["Mickiewicza"], (
        "Mickiewicza↔Dojlidy Górne to fałszywe sąsiedztwo (link omija Dojlidy)"
    )
    assert "Mickiewicza" not in ADJ["Dojlidy Górne"]


def test_dojlidy_gorne_only_neighbor_is_dojlidy():
    """Dojlidy Górne (peryferia SE) graniczy realnie tylko z Dojlidy."""
    assert ADJ["Dojlidy Górne"] == {"Dojlidy"}, (
        f"Dojlidy Górne ma sąsiadów {ADJ['Dojlidy Górne']}, oczekiwano {{'Dojlidy'}}"
    )


# ── sanity centroidowy (data-driven, skip gdy brak cache) ────────────────────

def _empirical_centroids():
    if not os.path.exists(GEOCODE_CACHE):
        return None
    try:
        cache = json.load(open(GEOCODE_CACHE, encoding="utf-8"))
    except Exception:
        return None
    import collections
    pts = collections.defaultdict(list)
    for k, v in cache.items():
        if not isinstance(v, dict):
            continue
        lat, lon = v.get("lat"), v.get("lon")
        if lat is None or lon is None:
            continue
        if not C.coords_in_bialystok_bbox((lat, lon)):
            continue
        addr = v.get("original") or k.split(",")[0]
        parts = k.rsplit(",", 1)
        city = parts[1].strip() if len(parts) == 2 else "białystok"
        dz = C.drop_zone_from_address(addr, city if city else None)
        if dz and dz != "Unknown":
            pts[dz].append((lat, lon))
    cent = {}
    for d, ll in pts.items():
        if len(ll) >= 4:
            cent[d] = (
                statistics.median(x[0] for x in ll),
                statistics.median(x[1] for x in ll),
            )
    return cent


def test_intra_city_adjacency_centroid_sanity():
    """Żadna para sąsiednia intra-city nie ma centroidów dalej niż 5.0 km.

    Próg hojny (margines na duże dzielnice: Bema/Antoniuk/Bacieczki). Łapie
    grube błędy typu Mickiewicza↔Dojlidy Górne (4.38 km był OK, ale w paśmie
    >4 km muszą zostać tylko realnie duże dzielnice). >5.0 km intra-city =
    prawie na pewno fałszywe sąsiedztwo do przeglądu.
    """
    cent = _empirical_centroids()
    if cent is None:
        import pytest
        pytest.skip("geocode_cache.json niedostępny — sanity centroidowy pominięty")
    out = set(BIALYSTOK_OUTSIDE_CITY_ZONES)
    far = []
    seen = set()
    for a, nbrs in ADJ.items():
        for b in nbrs:
            key = tuple(sorted((a, b)))
            if key in seen:
                continue
            seen.add(key)
            if a in out or b in out:
                continue  # outside-city: centroid odrębnej miejscowości daleko (artefakt)
            if a in cent and b in cent:
                d = C.osrm_client.haversine(cent[a], cent[b]) if hasattr(C, "osrm_client") else None
                if d is None:
                    from dispatch_v2 import osrm_client
                    d = osrm_client.haversine(cent[a], cent[b])
                if d > 5.0:
                    far.append((round(d, 2), a, b))
    assert not far, f"Pary intra-city z centroidami >5.0 km (do przeglądu): {sorted(far, reverse=True)}"


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
