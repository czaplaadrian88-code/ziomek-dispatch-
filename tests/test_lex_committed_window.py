"""P-1 lex-committed-window (handoff 2026-06-24): okno odbioru committed PRZED carried-first.

Reguła: carried-first wpychał niesione na front bezwarunkowo → odbiór committed (czas_kuriera)
lądował po dalekiej dostawie niesionego = spóźnienie. Lex-D: wśród perm precedence+NO-RETURN
+ feasible (carried ≤ R6=35, brak nowego R6, inna dostawa nie później >TOL vs baseline)
minimalizuje (naruszenia_okna, jazda, wiek_carried). Anchored na baseline → nie regresuje.
SHADOW loguje rozjazd; APPLY zmienia kolejność. OSRM zamockowany (haversine ~30 km/h).
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
def _mock_osrm(monkeypatch, tmp_path):
    monkeypatch.setattr(osrm_client, "table", _fake_table)
    # NIGDY nie pisz do produkcyjnego shadow jsonl z testów
    monkeypatch.setattr(P, "LEX_WINDOW_SHADOW_PATH", str(tmp_path / "lex_shadow.jsonl"))


NOW = datetime(2026, 6, 24, 13, 0, 0, tzinfo=timezone.utc)
START = (53.130, 23.150)

# A = niesiony (picked_up), dostawa DALEKO (carried-first ciągnie kuriera tam najpierw).
# B = przypisany, ODBIÓR pod startem, committed czas_kuriera = now+2 min (musi być wzięty już).
ORDERS = {
    "A": {"status": "picked_up", "picked_up_at": "2026-06-24 14:55:00",  # ~5 min temu (Warsaw)
          "czas_kuriera_warsaw": None,
          "pickup_coords": [53.130, 23.150], "delivery_coords": [53.100, 23.100]},  # daleko SW
    "B": {"status": "assigned",
          "czas_kuriera_warsaw": "2026-06-24T15:02:00+02:00",   # = now+2 min
          "pickup_coords": [53.1305, 23.1505], "delivery_coords": [53.131, 23.149]},
}


def _carried_first_stops():
    # carried-first: niesiony dropoff A na froncie, potem odbiór+dostawa B
    return [
        {"order_id": "A", "type": "dropoff", "dwell_min": 3.5},
        {"order_id": "B", "type": "pickup", "dwell_min": 1.0},
        {"order_id": "B", "type": "dropoff", "dwell_min": 3.5},
    ]


def _ids(seq):
    return [(s["order_id"], s["type"]) for s in seq]


def test_shadow_and_apply_off_is_noop(monkeypatch):
    monkeypatch.setattr(P, "ENABLE_LEX_COMMITTED_WINDOW_SHADOW", False)
    monkeypatch.setattr(P, "ENABLE_LEX_COMMITTED_WINDOW", False)
    out = P._lex_committed_window_reorder(_carried_first_stops(), ORDERS, START, NOW)
    assert _ids(out) == _ids(_carried_first_stops())   # bez obu flag = nic nie liczy


def test_apply_fixes_pickup_window(monkeypatch):
    monkeypatch.setattr(P, "ENABLE_LEX_COMMITTED_WINDOW", True)
    monkeypatch.setattr(P, "LEX_WINDOW_TOL_MIN", 5.0)
    out = P._lex_committed_window_reorder(_carried_first_stops(), ORDERS, START, NOW)
    o = _ids(out)
    # odbiór B PRZED dostawą niesionego A — kurier nie jedzie najpierw daleko (okno naprawione)
    assert o.index(("B", "pickup")) < o.index(("A", "dropoff")), \
        "lex-D: committed odbiór B przed daleką dostawą niesionego A"
    # niesiony A nadal dostarczony (dropoff obecny)
    assert ("A", "dropoff") in o


def test_shadow_only_does_not_change_order(monkeypatch):
    # SHADOW ON, APPLY OFF → liczy + loguje, ale kolejność NIE zmieniona
    monkeypatch.setattr(P, "ENABLE_LEX_COMMITTED_WINDOW_SHADOW", True)
    monkeypatch.setattr(P, "ENABLE_LEX_COMMITTED_WINDOW", False)
    monkeypatch.setattr(P, "LEX_WINDOW_SHADOW_PATH", "/tmp/_lex_window_test_shadow.jsonl")
    out = P._lex_committed_window_reorder(_carried_first_stops(), ORDERS, START, NOW)
    assert _ids(out) == _ids(_carried_first_stops()), "shadow-only NIE zmienia decyzji"


def test_no_carried_is_noop(monkeypatch):
    monkeypatch.setattr(P, "ENABLE_LEX_COMMITTED_WINDOW", True)
    orders = {k: dict(v) for k, v in ORDERS.items()}
    orders["A"]["status"] = "assigned"   # brak niesionych → nie ten przypadek
    orders["A"]["picked_up_at"] = None
    stops = [{"order_id": "A", "type": "pickup", "dwell_min": 1.0},
             {"order_id": "B", "type": "pickup", "dwell_min": 1.0},
             {"order_id": "A", "type": "dropoff", "dwell_min": 3.5},
             {"order_id": "B", "type": "dropoff", "dwell_min": 3.5}]
    out = P._lex_committed_window_reorder([dict(s) for s in stops], orders, START, NOW)
    assert _ids(out) == _ids(stops), "bez niesionych lex-window jest no-op (to domena Fix M)"
