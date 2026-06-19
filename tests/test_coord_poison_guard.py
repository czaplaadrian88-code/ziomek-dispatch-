"""Lekcja #140 — regression: bag-order coord (0,0)/None/far NIE może cicho dać
realistycznej trasy (phantom leg → false INFEASIBLE → wycięcie wolnych kurierów).

Bug 2026-05-21 (orders 475038/475045/475047, kurierzy 393/123/457): Street Mama
Thai / Dr Tusz / Interpap spoza restaurant_coords.json → pickup_coords=None →
dispatch_pipeline (0,0) → OSRM SNAPUJE (0,0) do krawędzi ekstraktu (~113 km,
code:Ok) → leg ~117-148 min dla trasy realnie ~5 min.

Defense-in-depth (każda warstwa testowana niezależnie):
  1. common.coords_in_bialystok_bbox — sanity bbox.
  2. osrm_client.route — guard wejściowy + snap → coord_invalid sentinel.
  3. osrm_client.table — komórki dotykające nieprawidłowego punktu = sentinel.
  4. dispatch_pipeline._bag_dict_to_ordersim — re-geokod zamiast (0,0).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # scripts/ na ścieżkę → pakiet dispatch_v2

from dispatch_v2 import common as C  # noqa: E402
from dispatch_v2 import osrm_client  # noqa: E402
from dispatch_v2 import dispatch_pipeline as DP  # noqa: E402

GALERIA = (53.121494, 23.176691)   # realny punkt w Białymstoku
MAMA_THAI = (53.12228, 23.1445556)


# --- Warstwa 1: bbox helper -------------------------------------------------
def test_bbox_rejects_sentinel_and_far():
    assert C.coords_in_bialystok_bbox(GALERIA) is True
    assert C.coords_in_bialystok_bbox((0.0, 0.0)) is False
    assert C.coords_in_bialystok_bbox(None) is False
    assert C.coords_in_bialystok_bbox((52.360982, 22.588546)) is False  # snap-pt (0,0)→edge
    assert C.coords_in_bialystok_bbox((52.2297, 21.0122)) is False       # Warszawa
    assert C.coords_in_bialystok_bbox(("x", "y")) is False


def test_bbox_accepts_metro_suburbs():
    assert C.coords_in_bialystok_bbox((53.1996, 23.2059)) is True  # Wasilków
    assert C.coords_in_bialystok_bbox((53.1389, 23.0086)) is True  # Choroszcz


# --- Warstwa 2: route() guard (BEZ sieci — guard wejściowy zwraca przed HTTP) -
def test_route_zero_coord_is_sentinel_not_phantom():
    r = osrm_client.route(GALERIA, (0.0, 0.0))
    assert r.get("coord_invalid") is True
    # KLUCZOWE: NIE ~117-148 min (snap), tylko jawny sentinel
    assert r["duration_min"] >= C.OSRM_INVALID_COORD_SENTINEL_MIN
    r2 = osrm_client.route((0.0, 0.0), GALERIA)
    assert r2.get("coord_invalid") is True
    r3 = osrm_client.route(GALERIA, None)
    assert r3.get("coord_invalid") is True


# --- Warstwa 3: table() guard ------------------------------------------------
def test_table_sentinels_cells_touching_invalid_point():
    pts = [GALERIA, (0.0, 0.0)]
    m = osrm_client.table(pts, pts)
    # każda komórka dotykająca indeksu 1 ((0,0)) = sentinel
    assert m[0][1].get("coord_invalid") is True
    assert m[1][0].get("coord_invalid") is True
    assert m[1][1].get("coord_invalid") is True
    # komórka valid×valid (Galeria→Galeria) NIE jest sentinelem
    assert not m[0][0].get("coord_invalid")


# --- Warstwa 4: bag re-geokod (root fix), deterministyczny monkeypatch --------
def test_bag_dict_regeocodes_missing_pickup(monkeypatch):
    from dispatch_v2 import geocoding
    monkeypatch.setattr(geocoding, "geocode_restaurant",
                        lambda name, addr="", city=None: MAMA_THAI)
    d = {"order_id": "X1", "status": "assigned", "restaurant": "Street Mama Thai",
         "pickup_coords": None, "delivery_coords": list(GALERIA)}
    sim = DP._bag_dict_to_ordersim(d)
    assert C.coords_in_bialystok_bbox(sim.pickup_coords)
    assert sim.pickup_coords != (0.0, 0.0)
    # repair zaokrągla do 6 dp
    assert tuple(sim.pickup_coords) == (round(MAMA_THAI[0], 6), round(MAMA_THAI[1], 6))


def test_bag_dict_zero_when_repair_fails_then_guard_catches(monkeypatch):
    from dispatch_v2 import geocoding
    monkeypatch.setattr(geocoding, "geocode_restaurant",
                        lambda name, addr="", city=None: None)
    monkeypatch.setattr(geocoding, "geocode",
                        lambda addr, city=None: None)
    d = {"order_id": "X2", "status": "assigned", "restaurant": "Nieznana",
         "pickup_coords": None, "delivery_coords": None}
    sim = DP._bag_dict_to_ordersim(d)
    # repair zawiódł → (0,0), ALE guard OSRM zamieni to na sentinel (nie phantom)
    assert tuple(sim.pickup_coords) == (0.0, 0.0)
    r = osrm_client.route(GALERIA, tuple(sim.pickup_coords))
    assert r.get("coord_invalid") is True
