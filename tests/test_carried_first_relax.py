"""Testy guarded carried-first relax (_relax_carried_first / _apply_canon_order_invariants).

Reguła (Adrian 2026-06-22, case Sioux→Wierzbowa): carried-first wolno rozluźnić
TYLKO gdy krótsza trasa spełnia 5 twardych guardów: (1) trzyma niesione jedzenie
≤SOFT_MAX, (2) nie opóźnia innej DOSTAWY >DELAY_TOL, (3) nie opóźnia ODBIORU
>DELAY_TOL (jedzenie nie czeka pod restauracją), (4) nie dodaje przekroczenia R6,
(5) NO-RETURN — nie wraca do restauracji, z której kurier już wiezie jedzenie
(carried), ani nie rozbija odbiorów jednej restauracji (Adrian: „kurier nie może
wracać do tej samej restauracji z jedzeniem na samochodzie, resztę może odbierać").
Inaczej zostaje carried-first. Replay zero-harm: eod_drafts/2026-06-22/.

OSRM zamockowany (haversine → s przy ~30 km/h) — testy deterministyczne, bez
zależności od żywego serwera.
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
    # ~30 km/h => 8.333 m/s
    return [[{"duration_s": _hav_m(a, b) / 8.333} for b in pts_b] for a in pts_a]


@pytest.fixture(autouse=True)
def _mock_osrm(monkeypatch):
    monkeypatch.setattr(osrm_client, "table", _fake_table)


NOW = datetime(2026, 6, 22, 16, 3, 30, tzinfo=timezone.utc)
POS = (53.1152557, 23.1459371)

# Sioux→Wierzbowa case: Goodboy (carried, fresh) + Sioux + Kaczka.
SIOUX_ORDERS = {
    "482648": {"status": "picked_up", "czas_kuriera_warsaw": "2026-06-22T17:59:00+02:00",
               "picked_up_at": "2026-06-22T16:02:49+00:00",
               "pickup_coords": [53.115336, 23.14607], "delivery_coords": [53.1436471, 23.1303087]},
    "482646": {"status": "assigned", "czas_kuriera_warsaw": "2026-06-22T18:10:00+02:00",
               "pickup_coords": [53.132786, 23.15775], "delivery_coords": [53.1402896, 23.0997278]},
    "482657": {"status": "assigned", "czas_kuriera_warsaw": "2026-06-22T18:42:00+02:00",
               "pickup_coords": [53.143468, 23.175526], "delivery_coords": [53.1249474, 23.1700788]},
}


def _carried_first_stops():
    return [
        {"order_id": "482648", "type": "dropoff", "coords": {"lat": 53.1436471, "lng": 23.1303087}, "dwell_min": 3.5},
        {"order_id": "482646", "type": "pickup", "coords": {"lat": 53.132786, "lng": 23.15775}, "dwell_min": 1.0},
        {"order_id": "482646", "type": "dropoff", "coords": {"lat": 53.1402896, "lng": 23.0997278}, "dwell_min": 3.5},
        {"order_id": "482657", "type": "pickup", "coords": {"lat": 53.143468, "lng": 23.175526}, "dwell_min": 1.0},
        {"order_id": "482657", "type": "dropoff", "coords": {"lat": 53.1249474, "lng": 23.1700788}, "dwell_min": 3.5},
    ]


def _ids(seq):
    return [(s["order_id"], s["type"]) for s in seq]


def test_flag_off_is_noop(monkeypatch):
    monkeypatch.setattr(P, "ENABLE_CARRIED_FIRST_RELAX", False)
    # D.3 fala A (2026-07-02): pozostałe re-orderery kanonu (LEX-okno, non-carried
    # reorder) domyślnie True po migracji do flags.json → pinujemy do pre-migracyjnego
    # test-defaultu (False), by test izolował „carried-first bez żadnego relaxera".
    # Parytet LEX/non-carried mają własne testy (test_lex_committed_window / route_reorder).
    monkeypatch.setattr(P, "ENABLE_LEX_COMMITTED_WINDOW", False)
    monkeypatch.setattr(P, "ENABLE_LEX_COMMITTED_WINDOW_SHADOW", False)
    monkeypatch.setattr(P, "ENABLE_NONCARRIED_DROPOFF_REORDER", False)
    stops = _carried_first_stops()
    out = P._apply_canon_order_invariants([dict(s) for s in stops], SIOUX_ORDERS, POS, NOW)
    # carried-first preserved: niesione 482648 dropoff zostaje na froncie
    assert out[0]["order_id"] == "482648" and out[0]["type"] == "dropoff"


def test_relax_on_fixes_sioux(monkeypatch):
    monkeypatch.setattr(P, "ENABLE_CARRIED_FIRST_RELAX", True)
    monkeypatch.setattr(P, "CARRIED_FIRST_RELAX_SOFT_MAX_MIN", 20.0)
    stops = _carried_first_stops()
    out = P._apply_canon_order_invariants([dict(s) for s in stops], SIOUX_ORDERS, POS, NOW)
    # Sioux (482646) pickup wskakuje PRZED dostawę niesionego 482648 (po drodze)
    assert _ids(out)[0] == ("482646", "pickup")
    pos_pick = _ids(out).index(("482646", "pickup"))
    pos_carry_drop = _ids(out).index(("482648", "dropoff"))
    assert pos_pick < pos_carry_drop


def test_relax_keeps_carried_within_soft_max(monkeypatch):
    """Niesione jedzenie po relaksie nie może przekroczyć SOFT_MAX (świeżość)."""
    monkeypatch.setattr(P, "ENABLE_CARRIED_FIRST_RELAX", True)
    monkeypatch.setattr(P, "CARRIED_FIRST_RELAX_SOFT_MAX_MIN", 20.0)
    out = P._relax_carried_first(_carried_first_stops(), SIOUX_ORDERS, POS, NOW)
    # policz czas dowozu niesionego 482648 wzdłuż wynikowej sekwencji (synthetic OSRM)
    t = 0.0
    prev = POS
    carry = None
    pa = P._parse_dt(SIOUX_ORDERS["482648"]["picked_up_at"]).timestamp() / 60.0
    nowm = NOW.timestamp() / 60.0
    for s in out:
        c = (s["coords"]["lat"], s["coords"]["lng"])
        t += _hav_m(prev, c) / 8.333 / 60.0
        prev = c
        if s["order_id"] == "482648" and s["type"] == "dropoff":
            carry = (nowm + t) - pa
            break
        t += s["dwell_min"]
    assert carry is not None and carry <= 20.0 + 1e-6


def test_old_carried_food_stays_front(monkeypatch):
    """Niesione jedzenie JUŻ stare (>SOFT_MAX) — relaks nie może go odświeżyć →
    fallback do carried-first (front), bez zygzaka."""
    monkeypatch.setattr(P, "ENABLE_CARRIED_FIRST_RELAX", True)
    monkeypatch.setattr(P, "CARRIED_FIRST_RELAX_SOFT_MAX_MIN", 20.0)
    orders = {k: dict(v) for k, v in SIOUX_ORDERS.items()}
    # picked_up 40 min temu => i tak >20 min do dowozu
    orders["482648"]["picked_up_at"] = "2026-06-22T15:23:00+00:00"
    out = P._relax_carried_first(_carried_first_stops(), orders, POS, NOW)
    assert _ids(out)[0] == ("482648", "dropoff")   # zostaje na froncie (ASAP)


def test_no_change_when_already_optimal(monkeypatch):
    """Worek gdzie carried-first JEST najkrótszy → brak zmiany mimo flagi ON."""
    monkeypatch.setattr(P, "ENABLE_CARRIED_FIRST_RELAX", True)
    # niesione bardzo blisko startu, nowy odbiór daleko w przeciwną stronę
    orders = {
        "A": {"status": "picked_up", "czas_kuriera_warsaw": None, "picked_up_at": "2026-06-22T16:00:00+00:00",
              "pickup_coords": [53.115, 23.146], "delivery_coords": [53.116, 23.147]},
        "B": {"status": "assigned", "czas_kuriera_warsaw": None,
              "pickup_coords": [53.20, 23.30], "delivery_coords": [53.21, 23.31]},
    }
    stops = [
        {"order_id": "A", "type": "dropoff", "coords": {"lat": 53.116, "lng": 23.147}, "dwell_min": 3.5},
        {"order_id": "B", "type": "pickup", "coords": {"lat": 53.20, "lng": 23.30}, "dwell_min": 1.0},
        {"order_id": "B", "type": "dropoff", "coords": {"lat": 53.21, "lng": 23.31}, "dwell_min": 3.5},
    ]
    out = P._relax_carried_first([dict(s) for s in stops], orders, POS, NOW)
    assert _ids(out) == _ids(stops)   # bez zmiany


def test_relax_deterministic(monkeypatch):
    monkeypatch.setattr(P, "ENABLE_CARRIED_FIRST_RELAX", True)
    monkeypatch.setattr(P, "CARRIED_FIRST_RELAX_SOFT_MAX_MIN", 20.0)
    a = P._relax_carried_first(_carried_first_stops(), SIOUX_ORDERS, POS, NOW)
    b = P._relax_carried_first(_carried_first_stops(), SIOUX_ORDERS, POS, NOW)
    assert _ids(a) == _ids(b)


def test_no_carried_returns_input(monkeypatch):
    monkeypatch.setattr(P, "ENABLE_CARRIED_FIRST_RELAX", True)
    orders = {k: dict(v) for k, v in SIOUX_ORDERS.items()}
    orders["482648"]["status"] = "assigned"   # nic niesionego
    stops = [
        {"order_id": "482648", "type": "pickup", "coords": {"lat": 53.115336, "lng": 23.14607}, "dwell_min": 1.0},
        {"order_id": "482648", "type": "dropoff", "coords": {"lat": 53.1436471, "lng": 23.1303087}, "dwell_min": 3.5},
        {"order_id": "482646", "type": "pickup", "coords": {"lat": 53.132786, "lng": 23.15775}, "dwell_min": 1.0},
        {"order_id": "482646", "type": "dropoff", "coords": {"lat": 53.1402896, "lng": 23.0997278}, "dwell_min": 3.5},
    ]
    out = P._relax_carried_first([dict(s) for s in stops], orders, POS, NOW)
    assert _ids(out) == _ids(stops)   # bez niesionego = funkcja nie rusza kolejności


# --- NO-RETURN guard (Adrian 2026-06-22): relax nie wraca do restauracji z carried ---

def test_detect_seeds_carried_restaurant():
    """Gdy kurier wiezie jedzenie z restauracji R (carried), KAŻDY odbiór z R w trasie
    = powrót — wykrywany TYLKO po podaniu carried_rest_keys (jedzenie w aucie)."""
    os_ = {"Y": {"pickup_coords": [53.1327, 23.1577], "restaurant_name": "R"},
           "Dx": {"delivery_coords": [53.20, 23.20]}}
    R = P._pickup_rest_key({"type": "pickup", "order_id": "Y"}, os_)
    seq = [{"order_id": "Dx", "type": "dropoff"}, {"order_id": "Y", "type": "pickup"}]
    assert P._detect_departed_pickup_revisit(seq, os_) == []          # bez seedu = brak powrotu
    viol = P._detect_departed_pickup_revisit(seq, os_, {R})           # carried R w aucie
    assert len(viol) == 1 and viol[0][2][1] == "Y" and viol[0][0] < 0


def test_relax_refuses_return_to_carried_restaurant(monkeypatch):
    """Krótsza trasa = odebrać Y (z R) zanim dowieziesz carried X (też z R). To powrót
    z jedzeniem w aucie → strażnik odrzuca, zostaje carried-first (bez guarda relax
    wybrałby [Yp,Xd,Yd] jako ~2.5× krótsze)."""
    monkeypatch.setattr(P, "ENABLE_CARRIED_FIRST_RELAX", True)
    R = [53.1327, 23.1577]
    orders = {
        "X": {"status": "picked_up", "picked_up_at": "2026-06-22T16:02:30+00:00",
              "pickup_coords": R, "delivery_coords": [53.1150, 23.1460]},
        "Y": {"status": "assigned",
              "pickup_coords": R, "delivery_coords": [53.1140, 23.1450]},
    }
    stops = [
        {"order_id": "X", "type": "dropoff", "dwell_min": 3.5},
        {"order_id": "Y", "type": "pickup", "dwell_min": 1.0},
        {"order_id": "Y", "type": "dropoff", "dwell_min": 3.5},
    ]
    out = P._relax_carried_first([dict(s) for s in stops], orders, (53.1300, 23.1560), NOW)
    assert _ids(out) == [("X", "dropoff"), ("Y", "pickup"), ("Y", "dropoff")]


def test_relax_allows_pickup_other_restaurant_before_carried(monkeypatch):
    """Y z INNEJ restauracji niż carried X → wolno odebrać Y przed dostawą X (to nie
    powrót). Strażnik blokuje TYLKO powrót do restauracji carried — resztę można brać."""
    monkeypatch.setattr(P, "ENABLE_CARRIED_FIRST_RELAX", True)
    orders = {
        "X": {"status": "picked_up", "picked_up_at": "2026-06-22T16:02:30+00:00",
              "pickup_coords": [53.1327, 23.1577], "delivery_coords": [53.1150, 23.1460]},
        "Y": {"status": "assigned",
              "pickup_coords": [53.1305, 23.1565], "delivery_coords": [53.1140, 23.1450]},
    }
    stops = [
        {"order_id": "X", "type": "dropoff", "dwell_min": 3.5},
        {"order_id": "Y", "type": "pickup", "dwell_min": 1.0},
        {"order_id": "Y", "type": "dropoff", "dwell_min": 3.5},
    ]
    out = P._relax_carried_first([dict(s) for s in stops], orders, (53.1300, 23.1560), NOW)
    assert _ids(out)[0] == ("Y", "pickup")


def test_relax_preserves_same_restaurant_bundle(monkeypatch):
    """KLUCZOWE (pytanie Adriana): łączenie dowozów z JEDNEJ restauracji (Y+Z z R, jedna
    wizyta) NIE jest blokowane. Strażnik zabrania tylko POWROTU (osobnej 2. wizyty), nie
    bundlingu — Y i Z zostają odbierane razem (sąsiednie pickupy = jedna wizyta)."""
    monkeypatch.setattr(P, "ENABLE_CARRIED_FIRST_RELAX", True)
    R = [53.1327, 23.1577]
    orders = {
        "X": {"status": "picked_up", "picked_up_at": "2026-06-22T16:02:30+00:00",
              "pickup_coords": [53.1200, 23.1400], "delivery_coords": [53.1150, 23.1460]},
        "Y": {"status": "assigned", "pickup_coords": R, "delivery_coords": [53.1400, 23.1300]},
        "Z": {"status": "assigned", "pickup_coords": R, "delivery_coords": [53.1410, 23.1310]},
    }
    stops = [
        {"order_id": "X", "type": "dropoff", "dwell_min": 3.5},
        {"order_id": "Y", "type": "pickup", "dwell_min": 1.0},
        {"order_id": "Z", "type": "pickup", "dwell_min": 1.0},
        {"order_id": "Y", "type": "dropoff", "dwell_min": 3.5},
        {"order_id": "Z", "type": "dropoff", "dwell_min": 3.5},
    ]
    out = P._relax_carried_first([dict(s) for s in stops], orders, POS, NOW)
    ids = _ids(out)
    assert sorted(ids) == sorted(_ids(stops))           # nic nie zgubione
    iyp, izp = ids.index(("Y", "pickup")), ids.index(("Z", "pickup"))
    assert abs(iyp - izp) == 1                            # Y i Z w JEDNEJ wizycie (bundling OK)
