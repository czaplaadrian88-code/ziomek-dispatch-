"""Czysta geometria trasy wydzielona z dispatch_pipeline.py (B6 nacięcie, 2026-06-20).

Liściaste, w pełni czyste funkcje (tylko `math` + `typing`) — zero stanu modułu, zero
I/O, zero zależności od `common`/`scoring`/pipeline. Przeniesione 1:1 z
`dispatch_pipeline._point_to_segment_km` / `_min_dist_to_route_km` i re-importowane z
powrotem (call-site `dev = _min_dist_to_route_km(...)` NIETKNIĘTY) → zachowanie
identyczne, bramka = pełna suita pytest + test równoważności `test_pipeline_geometry`.

Pierwsze nacięcie monolitu dispatch_pipeline.py (6133 LOC) — wzorzec jak objm_lexr6:
moduł NIE importuje dispatch_pipeline (brak cyklu).
"""
import math
from typing import Optional


def _point_to_segment_km(p, a, b) -> float:
    """Najkrótsza odległość punktu p od odcinka [a, b] w km.
    Equirectangular projection — wystarczająca dla skali Białegostoku (<30 km)."""
    lat0 = (a[0] + b[0] + p[0]) / 3.0
    coslat = math.cos(math.radians(lat0))
    def to_xy(pt):
        return (pt[1] * coslat * 111.32, pt[0] * 111.32)
    ax, ay = to_xy(a)
    bx, by = to_xy(b)
    px, py = to_xy(p)
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return ((px - ax) ** 2 + (py - ay) ** 2) ** 0.5
    t = ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)
    t = max(0.0, min(1.0, t))
    proj_x = ax + t * dx
    proj_y = ay + t * dy
    return ((px - proj_x) ** 2 + (py - proj_y) ** 2) ** 0.5


def _min_dist_to_route_km(point, courier_pos, bag_dropoffs) -> Optional[float]:
    """Min dystans od punktu do polyline kurier→bag_dropoff_1→bag_dropoff_2...
    None gdy bag pusty lub brak coords."""
    if not bag_dropoffs:
        return None
    nodes = [courier_pos] + [d for d in bag_dropoffs if d]
    if len(nodes) < 2:
        return None
    return min(_point_to_segment_km(point, nodes[i], nodes[i+1]) for i in range(len(nodes)-1))
