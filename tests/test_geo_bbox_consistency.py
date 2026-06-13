"""GEO-06/07 (audyt 2026-06-13): inwariant spójności dwóch bboxów Białegostoku.

Dwa bboxy są CELOWO różne (NIE błąd):
  - BIALYSTOK_BBOX_LAT/LON = metropolia ±55 km (filtr trucizny OSRM/GPS)
  - GEOCODE_BBOX_*         = obszar obsługi +~28 km (filtr akceptacji geokodu)

Inwariant kanoniczny (ten test go pilnuje):
  GEOCODE_BBOX ⊂ BIALYSTOK_BBOX (ścisłe podzbiór) — inaczej geocoding mógłby
  zaakceptować punkt, który OSRM zaraz odrzuci jako truciznę.

Plus: realne miejscowości dispatchu mieszczą się w OBU; (0,0)/cross-country
odrzucone przez metropolię.

Run: /root/.openclaw/venvs/dispatch/bin/python -m pytest tests/test_geo_bbox_consistency.py -q
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import common as C  # noqa: E402

# Publiczne centra realnych miejscowości dispatchu (lat, lon).
DISPATCH_TOWNS = {
    "Wasilków": (53.2017, 23.2069),
    "Choroszcz": (53.1389, 22.9908),
    "Zabłudów": (53.0089, 23.3403),
    "Łapy": (52.9889, 22.8744),
    "Kleosin": (53.0894, 23.1736),
    "Supraśl": (53.2056, 23.3389),
    "Ignatki": (53.0556, 23.0939),
    "Centrum Białystok": (53.1325, 23.1688),
}


def _in_geocode_bbox(lat, lon):
    return (C.GEOCODE_BBOX_LAT_MIN <= lat <= C.GEOCODE_BBOX_LAT_MAX
            and C.GEOCODE_BBOX_LON_MIN <= lon <= C.GEOCODE_BBOX_LON_MAX)


def test_geocode_bbox_is_strict_subset_of_metropolia():
    """Inwariant kanoniczny: GEOCODE_BBOX ⊂ BIALYSTOK_BBOX."""
    lat_lo, lat_hi = C.BIALYSTOK_BBOX_LAT
    lon_lo, lon_hi = C.BIALYSTOK_BBOX_LON
    assert lat_lo <= C.GEOCODE_BBOX_LAT_MIN, "geocode lat_min poza metropolią (dół)"
    assert C.GEOCODE_BBOX_LAT_MAX <= lat_hi, "geocode lat_max poza metropolią (góra)"
    assert lon_lo <= C.GEOCODE_BBOX_LON_MIN, "geocode lon_min poza metropolią (lewo)"
    assert C.GEOCODE_BBOX_LON_MAX <= lon_hi, "geocode lon_max poza metropolią (prawo)"


def test_dispatch_towns_inside_both_bboxes():
    """Żaden realny adres dispatchu nie jest błędnie wycinany przez żaden bbox."""
    bad_metro = [t for t, (la, lo) in DISPATCH_TOWNS.items()
                 if not C.coords_in_bialystok_bbox((la, lo))]
    bad_geo = [t for t, (la, lo) in DISPATCH_TOWNS.items()
               if not _in_geocode_bbox(la, lo)]
    assert not bad_metro, f"Miejscowości poza metropolią bbox: {bad_metro}"
    assert not bad_geo, f"Miejscowości poza geocode bbox: {bad_geo}"


def test_geocode_accepted_implies_metropolia_accepted():
    """Konsekwencja inwariantu: cokolwiek w geocode-bbox jest w metropolii.

    Próbkowanie siatki po geocode-bbox — każdy punkt musi przejść metropolię.
    """
    la0, la1 = C.GEOCODE_BBOX_LAT_MIN, C.GEOCODE_BBOX_LAT_MAX
    lo0, lo1 = C.GEOCODE_BBOX_LON_MIN, C.GEOCODE_BBOX_LON_MAX
    steps = 6
    bad = []
    for i in range(steps + 1):
        for j in range(steps + 1):
            la = la0 + (la1 - la0) * i / steps
            lo = lo0 + (lo1 - lo0) * j / steps
            if _in_geocode_bbox(la, lo) and not C.coords_in_bialystok_bbox((la, lo)):
                bad.append((round(la, 4), round(lo, 4)))
    assert not bad, f"Punkty w geocode-bbox NIE w metropolii: {bad}"


def test_poison_rejected_by_metropolia():
    """(0,0) i cross-country odrzucone przez metropolia bbox."""
    assert not C.coords_in_bialystok_bbox((0.0, 0.0))
    assert not C.coords_in_bialystok_bbox((52.2297, 21.0122))  # Warszawa
    assert not C.coords_in_bialystok_bbox(None)


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
