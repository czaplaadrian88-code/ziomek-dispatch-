"""Testy Fix M (reorder dropoffów w worku bez niesionych) + Fix K (współlokalny odbiór).

Fix M (Adrian 2026-06-24, Mateusz Ostapczuk 413 Skłodowska/Lipowa): worek BEZ niesionych
nie był re-sekwencjonowany (relax wymaga carried; sequence-lock wyłączył per-tick re-TSP),
więc zygzak z insert_stop_optimal zostawał (Skłodowska przed Lipową, choć Lipowa 0.26 km od
odbioru). `_reorder_noncarried_min_drive` permutuje TYLKO dostawy (odbiory nietknięte) do
min-jazdy pod warunkiem: brak nowego R6 + żadna dostawa nie później >TOL vs obecna.

Fix K (Adrian 2026-06-24, Kuba Olchowik 370 Rany Julek): kurier STOI pod restauracją z
której wiezie jedzenie (carried) i ma tam jeszcze nieodebrane zlecenie — reguła no-return
seeduje tę restaurację jako 'opuszczoną' i każe dowieźć niesione i WRÓCIĆ po odbiór.
Korekta: restauracja == pozycja kuriera NIE jest opuszczona → współlokalny odbiór wolno
wziąć od razu (jedna wizyta), bez powrotu.

OSRM zamockowany (haversine ~30 km/h) — deterministyczne, bez żywego serwera.
"""
import math
from datetime import datetime, timezone

import pytest

from dispatch_v2 import plan_recheck as P
from dispatch_v2 import osrm_client


def _hav_m(a, b):
    R = 6371000.0
    la1, lo1, la2, lo2 = map(math.radians, [a[0], a[1], b[0], b[1]])
    dla, dlo = la2 - la1, lo2 - lo1
    h = math.sin(dla / 2) ** 2 + math.cos(la1) * math.cos(la2) * math.sin(dlo / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


def _fake_table(pts_a, pts_b):
    return [[{"duration_s": _hav_m(a, b) / 8.333} for b in pts_b] for a in pts_a]


@pytest.fixture(autouse=True)
def _mock_osrm(monkeypatch):
    monkeypatch.setattr(osrm_client, "table", _fake_table)


def _ids(seq):
    return [(s["order_id"], s["type"]) for s in seq]


NOW = datetime(2026, 6, 24, 12, 55, tzinfo=timezone.utc)

# ---------------------------------------------------------------- FIX M (Mateusz)
# 483033 Skłodowska (dostawa daleko na płd), 483036 Lipowa (dostawa 0.26 km od odbioru).
MATEUSZ_ORDERS = {
    "483033": {"status": "assigned", "czas_kuriera_warsaw": None,
               "pickup_coords": [53.133384, 23.15401], "delivery_coords": [53.1246198, 23.1590983]},
    "483036": {"status": "assigned", "czas_kuriera_warsaw": None,
               "pickup_coords": [53.134203, 23.148828], "delivery_coords": [53.1342172, 23.1511622]},
}
MATEUSZ_START = (53.134203, 23.148828)  # kurier po odbiorze, pod Rany Julek/Lipowa


def _mateusz_stops():
    return [
        {"order_id": "483033", "type": "pickup", "dwell_min": 1.0},
        {"order_id": "483036", "type": "pickup", "dwell_min": 1.0},
        {"order_id": "483033", "type": "dropoff", "dwell_min": 3.5},   # Skłodowska FIRST (zygzak)
        {"order_id": "483036", "type": "dropoff", "dwell_min": 3.5},   # Lipowa SECOND
    ]


def test_fix_m_delivers_lipowa_before_sklodowska(monkeypatch):
    monkeypatch.setattr(P, "ENABLE_NONCARRIED_DROPOFF_REORDER", True)
    monkeypatch.setattr(P, "NONCARRIED_REORDER_DELAY_TOL_MIN", 10.0)
    out = P._reorder_noncarried_min_drive(_mateusz_stops(), MATEUSZ_ORDERS, MATEUSZ_START, NOW)
    o = _ids(out)
    # Lipowa (483036 dropoff) przed Skłodowską (483033 dropoff)
    assert o.index(("483036", "dropoff")) < o.index(("483033", "dropoff"))
    # odbiory NIETKNIĘTE (na swoich miejscach, pierwsze dwa)
    assert o[0] == ("483033", "pickup") and o[1] == ("483036", "pickup")


def test_fix_m_flag_off_is_noop(monkeypatch):
    monkeypatch.setattr(P, "ENABLE_NONCARRIED_DROPOFF_REORDER", False)
    out = P._reorder_noncarried_min_drive(_mateusz_stops(), MATEUSZ_ORDERS, MATEUSZ_START, NOW)
    assert _ids(out) == _ids(_mateusz_stops())   # zygzak zachowany


def test_fix_m_skips_bag_with_carried(monkeypatch):
    monkeypatch.setattr(P, "ENABLE_NONCARRIED_DROPOFF_REORDER", True)
    monkeypatch.setattr(P, "NONCARRIED_REORDER_DELAY_TOL_MIN", 10.0)
    orders = {k: dict(v) for k, v in MATEUSZ_ORDERS.items()}
    orders["483033"]["status"] = "picked_up"   # niesione → Fix M NIE rusza (carried-first/relax)
    orders["483033"]["picked_up_at"] = "2026-06-24 14:50:00"
    out = P._reorder_noncarried_min_drive(_mateusz_stops(), orders, MATEUSZ_START, NOW)
    assert _ids(out) == _ids(_mateusz_stops())   # bez zmian


def test_fix_m_delay_guard_blocks_big_shuffle(monkeypatch):
    # Mała tolerancja → przesunięcie Skłodowskiej za dostawę Lipowej (>TOL) odrzucone.
    monkeypatch.setattr(P, "ENABLE_NONCARRIED_DROPOFF_REORDER", True)
    monkeypatch.setattr(P, "NONCARRIED_REORDER_DELAY_TOL_MIN", 0.5)
    out = P._reorder_noncarried_min_drive(_mateusz_stops(), MATEUSZ_ORDERS, MATEUSZ_START, NOW)
    o = _ids(out)
    # zbyt ciasny guard → zostaje wejściowa kolejność dostaw (Skłodowska first)
    assert o.index(("483033", "dropoff")) < o.index(("483036", "dropoff"))


def test_fix_m_no_new_r6(monkeypatch):
    monkeypatch.setattr(P, "ENABLE_NONCARRIED_DROPOFF_REORDER", True)
    monkeypatch.setattr(P, "NONCARRIED_REORDER_DELAY_TOL_MIN", 6.0)
    out = P._reorder_noncarried_min_drive(_mateusz_stops(), MATEUSZ_ORDERS, MATEUSZ_START, NOW)
    # każdy order: odbiór przed dostawą zachowany
    for oid in ("483033", "483036"):
        ip = _ids(out).index((oid, "pickup")); idd = _ids(out).index((oid, "dropoff"))
        assert ip < idd


# ---------------------------------------------------------------- FIX K (Kuba)
# rocha (carried, Zapiecek), Kręta (carried, Rany Julek), Ciepła (assigned, odbiór Rany Julek).
# Kurier STOI pod Rany Julek (= start). Współlokalny odbiór Ciepły = NIE powrót.
RANY_JULEK = [53.134203, 23.148828]
KUBA_ORDERS = {
    "483024": {"status": "picked_up", "picked_up_at": "2026-06-24 14:36:00",
               "czas_kuriera_warsaw": None,
               "pickup_coords": [53.133, 23.1438], "delivery_coords": [53.1335018, 23.1450076]},
    "483027": {"status": "picked_up", "picked_up_at": "2026-06-24 14:51:00",
               "czas_kuriera_warsaw": None,
               "pickup_coords": RANY_JULEK, "delivery_coords": [53.1128117, 23.1460335]},
    "483038": {"status": "assigned", "czas_kuriera_warsaw": None,
               "pickup_coords": RANY_JULEK, "delivery_coords": [53.138936, 23.1632686]},
}
KUBA_START = (RANY_JULEK[0], RANY_JULEK[1])


def _kuba_carried_first_stops():
    # carried-first: dwie niesione dostawy na froncie, potem odbiór+dostawa Ciepły.
    return [
        {"order_id": "483024", "type": "dropoff", "dwell_min": 3.5},
        {"order_id": "483027", "type": "dropoff", "dwell_min": 3.5},
        {"order_id": "483038", "type": "pickup", "dwell_min": 1.0},
        {"order_id": "483038", "type": "dropoff", "dwell_min": 3.5},
    ]


def test_fix_k_grabs_colocated_pickup(monkeypatch):
    monkeypatch.setattr(P, "ENABLE_CARRIED_FIRST_RELAX", True)
    monkeypatch.setattr(P, "ENABLE_RELAX_COLOC_PICKUP", True)
    monkeypatch.setattr(P, "CARRIED_FIRST_RELAX_SOFT_MAX_MIN", 30.0)
    out = P._apply_canon_order_invariants(
        [dict(s) for s in _kuba_carried_first_stops()], KUBA_ORDERS, KUBA_START, NOW)
    o = _ids(out)
    # Ciepła odbiór (483038 pickup) wzięty ZANIM kurier opuści Rany Julek →
    # przed dostawą Kręty (483027), nie po niej (brak powrotu).
    assert o.index(("483038", "pickup")) < o.index(("483027", "dropoff"))


def test_fix_k_flag_off_keeps_return(monkeypatch):
    monkeypatch.setattr(P, "ENABLE_CARRIED_FIRST_RELAX", True)
    monkeypatch.setattr(P, "ENABLE_RELAX_COLOC_PICKUP", False)   # bez korekty
    out = P._apply_canon_order_invariants(
        [dict(s) for s in _kuba_carried_first_stops()], KUBA_ORDERS, KUBA_START, NOW)
    o = _ids(out)
    # bez korekty współlokalny odbiór NIE wskakuje przed niesione (no-return blokuje) →
    # odbiór Ciepły zostaje po obu niesionych dostawach (powrót).
    assert o.index(("483038", "pickup")) > o.index(("483027", "dropoff"))


def test_fix_k_real_return_still_blocked(monkeypatch):
    # Restauracja Ciepły NIE pod pozycją kuriera (kurier wyjechał) → to PRAWDZIWY powrót,
    # korekta NIE może go dopuścić (chroni regułę „nie wracaj po jedzeniu w aucie").
    monkeypatch.setattr(P, "ENABLE_CARRIED_FIRST_RELAX", True)
    monkeypatch.setattr(P, "ENABLE_RELAX_COLOC_PICKUP", True)
    monkeypatch.setattr(P, "CARRIED_FIRST_RELAX_SOFT_MAX_MIN", 30.0)
    far_start = (53.120, 23.170)   # kurier daleko od Rany Julek
    out = P._apply_canon_order_invariants(
        [dict(s) for s in _kuba_carried_first_stops()], KUBA_ORDERS, far_start, NOW)
    o = _ids(out)
    assert o.index(("483038", "pickup")) > o.index(("483027", "dropoff"))
