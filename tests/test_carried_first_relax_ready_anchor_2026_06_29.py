"""Test efektu ENABLE_CARRIED_FIRST_RELAX_READY_ANCHOR (2026-06-29, case Rećki cid 492).

3 bramki carried-first w plan_recheck (_relax_carried_first / _reorder_noncarried_min_drive /
_lex_committed_window_reorder) liczyły R6 czas-w-torbie PŁASKO od TSP-pickup → carried-first
oszukiwał R6 odraczając odbiór. ON = kotwica od GOTOWOŚCI (czas_kuriera), spójnie z
route_simulator.r6_thermal_anchor → odroczenie nie chowa wieku → pickup-first przechodzi.

Macierz OSRM zahardkodowana (realne coords Rećki/Mama Thai, złapana z osrm_client.table):
węzły 0=start(Mama Thai), 1=D214(Rećki), 2=P226(Mama Thai pickup), 3=D226(Brzozowa).
"""
from datetime import datetime, timezone

from dispatch_v2 import common as C
from dispatch_v2 import plan_recheck as PR
from dispatch_v2 import osrm_client

# duration w minutach (start==P226 to ten sam punkt → drive 0)
_MAT_MIN = [
    [0.0, 21.78, 0.0, 26.42],
    [20.38, 0.0, 20.38, 15.87],
    [0.0, 21.78, 0.0, 26.42],
    [25.64, 16.04, 25.64, 0.0],
]


def _fake_table(src, dst):
    return [[{"duration_s": v * 60.0} for v in row] for row in _MAT_MIN]


def _case():
    orders_state = {
        "484214": {  # NIESIONE (carried): Miejska Miska -> Rećki, odebrane 15:11
            "status": "picked_up",
            "delivery_coords": [53.1553384, 23.2197426],
            "pickup_coords": [53.131298, 23.161298],
            "czas_kuriera_warsaw": "2026-06-29T14:47:00+02:00",
            "picked_up_at": "2026-06-29 15:11:39",
        },
        "484226": {  # NOWY odbiór: Mama Thai -> Brzozowa, kurier STOI w restauracji
            "status": "assigned",
            "pickup_coords": [53.121879, 23.146168],
            "delivery_coords": [53.1842891, 23.2368356],
            "czas_kuriera_warsaw": "2026-06-29T14:49:00+02:00",
        },
    }
    seq = [
        {"type": "dropoff", "order_id": "484214"},
        {"type": "pickup", "order_id": "484226"},
        {"type": "dropoff", "order_id": "484226"},
    ]
    start_pos = (53.121879, 23.146168)  # kurier stoi w Mama Thai
    now = datetime(2026, 6, 29, 13, 13, 0, tzinfo=timezone.utc)
    return seq, orders_state, start_pos, now


def _fmt(seq):
    return [f"{s['type'][0]}{s['order_id'][-3:]}" for s in seq]


def test_r6_thermal_bag_helper_on_off():
    # dt=50 (dostawa min od now), bp=45 (odroczony odbiór), ready_rel=-24 (gotowe 24 min temu)
    # ON  = od gotowości: 50-(-24)=74 → >35 BREACH (odroczenie NIE chowa wieku)
    # OFF = in-bag: 50-45=5 → brak breachu (oszustwo odroczenia)
    assert PR._r6_thermal_bag_min(50.0, 45.0, -24.0, True) == 74.0
    assert PR._r6_thermal_bag_min(50.0, 45.0, -24.0, False) == 5.0
    # brak czasu gotowości → fallback in-bag niezależnie od flagi
    assert PR._r6_thermal_bag_min(50.0, 45.0, None, True) == 5.0


def test_relax_ENABLE_CARRIED_FIRST_RELAX_READY_ANCHOR_changes_decision(monkeypatch):
    monkeypatch.setattr(PR, "ENABLE_CARRIED_FIRST_RELAX", True)
    monkeypatch.setattr(osrm_client, "table", _fake_table)
    seq, ostate, start_pos, now = _case()

    tog = {"v": False}
    _orig = C.flag

    def _patched(name, default=None):
        if name == "ENABLE_CARRIED_FIRST_RELAX_READY_ANCHOR":
            return tog["v"]
        return _orig(name, default)

    monkeypatch.setattr(C, "flag", _patched)

    tog["v"] = False
    off = _fmt(PR._relax_carried_first([dict(s) for s in seq], ostate, start_pos, now))
    tog["v"] = True
    on = _fmt(PR._relax_carried_first([dict(s) for s in seq], ostate, start_pos, now))

    assert off != on, f"flaga bez efektu na decyzji: OFF={off} ON={on}"
    # ON daje kolejność zgodną z RZECZYWISTYM wykonaniem (Rećki 15:32 PRZED Mama Thai 15:42)
    assert on == ["p226", "d214", "d226"], f"ON={on}"
