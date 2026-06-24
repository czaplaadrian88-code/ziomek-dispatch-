"""Floor-at-birth: _gen_one_bag_plan musi rodzić plan z odbiorem ≥ committed
czas_kuriera (case Orthdruk/Michał K 24.06: konsola pokazywała 9:36 vs umówione
10:15 bo regenerowany plan był surowy do następnego ticku refloor).

Testuje czysty helper `plan_recheck._floor_pickups_to_committed` (in-memory mirror
plan_manager.refloor_pickup)."""
import sys
from datetime import datetime, timezone

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from dispatch_v2 import plan_recheck as PR


def _iso(h, m):
    # Warsaw +02:00 ISO (jak czas_kuriera_warsaw / predicted_at)
    return f"2026-06-24T{h:02d}:{m:02d}:00+02:00"


def _stop(oid, kind, h, m):
    return {"order_id": oid, "type": kind, "predicted_at": _iso(h, m)}


def _mins(a_iso, b_iso):
    a = datetime.fromisoformat(a_iso).astimezone(timezone.utc)
    b = datetime.fromisoformat(b_iso).astimezone(timezone.utc)
    return (a - b).total_seconds() / 60.0


def test_orthdruk_pickup_floored_to_committed_and_delivery_shifts():
    # przyjazd odbioru 9:36, committed 10:15 → floor +39 min; dostawa 10:00 → +39
    stops = [_stop("482945", "pickup", 9, 36), _stop("482945", "dropoff", 10, 0)]
    orders = {"482945": {"czas_kuriera_warsaw": _iso(10, 15)}}
    out = PR._floor_pickups_to_committed(stops, orders)
    # odbiór dosunięty do 10:15 (porównanie chwili, niezależnie od offsetu zapisu)
    assert abs(_mins(out[0]["predicted_at"], _iso(10, 15))) < 0.001
    # dostawa przesunięta o tę samą deltę (10:00 + 39 = 10:39)
    assert abs(_mins(out[1]["predicted_at"], _iso(10, 39))) < 0.001


def test_noop_when_pickup_after_committed():
    # przyjazd 10:39 już ≥ committed 10:15 → bez zmian (kurier spóźniony, honest)
    stops = [_stop("482945", "pickup", 10, 39), _stop("482945", "dropoff", 11, 0)]
    orders = {"482945": {"czas_kuriera_warsaw": _iso(10, 15)}}
    before = [s["predicted_at"] for s in stops]
    out = PR._floor_pickups_to_committed(stops, orders)
    assert [s["predicted_at"] for s in out] == before


def test_noop_when_no_committed():
    stops = [_stop("x", "pickup", 9, 0), _stop("x", "dropoff", 9, 30)]
    orders = {"x": {}}
    before = [s["predicted_at"] for s in stops]
    out = PR._floor_pickups_to_committed(stops, orders)
    assert [s["predicted_at"] for s in out] == before


def test_sub_minute_delta_noop():
    # delta < 60s → no-op (anty-churn, mirror refloor min_delta_sec)
    stops = [_stop("x", "pickup", 10, 15), _stop("x", "dropoff", 10, 45)]
    stops[0]["predicted_at"] = "2026-06-24T10:14:30+02:00"  # 30s przed committed
    orders = {"x": {"czas_kuriera_warsaw": _iso(10, 15)}}
    out = PR._floor_pickups_to_committed(stops, orders)
    assert out[0]["predicted_at"] == "2026-06-24T10:14:30+02:00"


def test_cascade_multi_pickup_bag():
    # 2 odbiory za-wczesne + 2 dostawy; każdy floor kaskaduje na resztę
    stops = [
        _stop("A", "pickup", 9, 36),
        _stop("B", "pickup", 9, 50),
        _stop("A", "dropoff", 10, 5),
        _stop("B", "dropoff", 10, 20),
    ]
    orders = {
        "A": {"czas_kuriera_warsaw": _iso(10, 15)},
        "B": {"czas_kuriera_warsaw": _iso(10, 16)},
    }
    out = PR._floor_pickups_to_committed(stops, orders)
    # A: 9:36→10:15 (+39). B po kaskadzie: 9:50+39=10:29, ale committed 10:16 < 10:29 → zostaje 10:29
    assert abs(_mins(out[0]["predicted_at"], _iso(10, 15))) < 0.001
    assert abs(_mins(out[1]["predicted_at"], _iso(10, 29))) < 0.001
    # żaden odbiór nie wcześniej niż jego committed
    for s in out:
        if s["type"] == "pickup":
            ck = orders[s["order_id"]]["czas_kuriera_warsaw"]
            assert _mins(s["predicted_at"], ck) >= -0.001
