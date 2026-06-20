"""Test równoważności wydzielonej geometrii trasy (B6 nacięcie, 2026-06-20).

Chroni: (1) zachowanie czystej geometrii (point→segment, min→route), (2) WIRING —
że `dispatch_pipeline._min_dist_to_route_km` to TEN SAM obiekt co
`pipeline_geometry._min_dist_to_route_km` (re-import, zero duplikatu/rozjazdu).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import pipeline_geometry as G  # noqa: E402

A = (53.10, 23.10)
B = (53.10, 23.20)          # ten sam lat → odcinek „poziomy"
MID_NORTH = (53.11, 23.15)  # nad środkiem, +0.01° lat


def test_point_on_endpoint_is_zero():
    assert G._point_to_segment_km(A, A, B) < 1e-9


def test_perpendicular_distance_is_latitude_offset():
    # dla odcinka o stałym lat odległość prostopadła = Δlat * 111.32 km (coslat-niezależna)
    d = G._point_to_segment_km(MID_NORTH, A, B)
    assert abs(d - 0.01 * 111.32) < 0.02, d


def test_degenerate_segment_is_point_distance():
    # a == b → dystans punkt→a; równy temu co do zdegenerowanego b
    p = (53.13, 23.17)
    assert abs(G._point_to_segment_km(p, A, A) - G._point_to_segment_km(p, A, A)) < 1e-9
    assert G._point_to_segment_km(p, A, A) > 0


def test_projection_clamps_beyond_endpoint():
    # punkt za końcem B (ten sam lat, lon dalej) → rzut przycięty do B
    beyond = (53.10, 23.30)
    via_seg = G._point_to_segment_km(beyond, A, B)
    to_b = G._point_to_segment_km(beyond, B, B)   # dystans do samego B
    assert abs(via_seg - to_b) < 1e-9, (via_seg, to_b)


def test_min_dist_to_route_none_cases():
    assert G._min_dist_to_route_km((53.1, 23.1), A, []) is None
    assert G._min_dist_to_route_km((53.1, 23.1), A, [None]) is None  # po filtrze < 2 węzły


def test_min_dist_to_route_single_segment():
    d = G._min_dist_to_route_km(MID_NORTH, A, [B])
    assert abs(d - 0.01 * 111.32) < 0.02, d


def test_min_dist_to_route_takes_minimum_over_segments():
    # trasa kurier A → B → C; punkt tuż przy 1. segmencie ma mniejszy dystans niż do 2.
    C = (53.20, 23.20)
    near_first = (53.105, 23.15)
    d = G._min_dist_to_route_km(near_first, A, [B, C])
    assert d is not None and d < 1.0


def test_wiring_dispatch_pipeline_reexports_same_objects():
    # dowód nacięcia: pipeline importuje wydzielone funkcje (ten sam obiekt, nie kopia)
    from dispatch_v2 import dispatch_pipeline as DP
    assert DP._min_dist_to_route_km is G._min_dist_to_route_km
    assert DP._point_to_segment_km is G._point_to_segment_km
