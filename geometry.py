"""Helpers geometryczne - haversine, bearing, angle, bag_centroid.

Wyciagniete ze starego feasibility.py (11.04) bo uzywane przez scoring.py
dla soft direction penalty. Nowy feasibility.py uzywa osrm_client + route_simulator
i nie potrzebuje tych helpersow.
"""
import math
from typing import List, Optional, Tuple


def haversine_km(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """Odleglosc w kilometrach. Punkty = (lat, lon)."""
    R = 6371.0
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlat = lat2 - lat1
    dlon = lon2 - lon1
    h = math.sin(dlat/2)**2 + math.cos(lat1)*math.cos(lat2)*math.sin(dlon/2)**2
    return 2 * R * math.asin(math.sqrt(h))


def bearing_deg(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    """Kurs w stopniach z punktu a do b (0=N, 90=E, 180=S, 270=W)."""
    lat1, lon1 = math.radians(a[0]), math.radians(a[1])
    lat2, lon2 = math.radians(b[0]), math.radians(b[1])
    dlon = lon2 - lon1
    x = math.sin(dlon) * math.cos(lat2)
    y = math.cos(lat1)*math.sin(lat2) - math.sin(lat1)*math.cos(lat2)*math.cos(dlon)
    return (math.degrees(math.atan2(x, y)) + 360) % 360


def angle_between(brg1: float, brg2: float) -> float:
    """Kat miedzy dwoma kursami (0-180)."""
    diff = abs(brg1 - brg2) % 360
    return min(diff, 360 - diff)


def bag_centroid(bag_coords: List[Tuple[float, float]]) -> Optional[Tuple[float, float]]:
    """Srodek geometryczny paczek w bagu. None jesli pusty."""
    if not bag_coords:
        return None
    lat = sum(c[0] for c in bag_coords) / len(bag_coords)
    lon = sum(c[1] for c in bag_coords) / len(bag_coords)
    return (lat, lon)
