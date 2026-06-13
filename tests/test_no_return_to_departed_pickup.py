"""Z-RULE: nigdy nie wracaj z odebranym dowozem do restauracji już opuszczonej.

Regresja na realny incydent 2026-06-13 (kurier Bartek cid=123, restauracja Raj):
zlecenia 480295 (ck 17:56) i 480434 (ck 18:17) z TEJ SAMEJ restauracji (identyczne
pickup_coords), 21 min od siebie → poza oknem grupowania 5 min → sort wg committed
przeplatał między nimi dostawę 480295 (Kanonierska, 2.5 km od Raju) → kurier
odebrał, pojechał 2.5 km, wrócił 2.5 km po drugi order. Adrian: "to jest kryminał".

Fix: `_coalesce_same_pickup_nodes` (za flagą ENABLE_NO_RETURN_TO_DEPARTED_PICKUP)
ściąga drugi odbiór z Raju tuż za pierwszy → JEDNA wizyta. Detekcja
(`_detect_departed_pickup_revisit`) ZAWSZE ON (log), niezależnie od flagi.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import plan_recheck as P

# Realne współrzędne z dispatch_state/orders_state.json (2026-06-13).
RAJ = [53.1322335, 23.1653257]
RUKOLA = [53.121879, 23.146168]
KANONIERSKA = [53.1249428, 23.130209]      # dostawa 480295
KOPERNIKA = [53.122172, 23.1456362]        # dostawa 480434
WYSZYNSKIEGO = [53.1300688, 23.1433751]    # dostawa 480430

ORDERS_STATE = {
    "480295": {"restaurant_name": "Raj", "pickup_coords": RAJ,
               "delivery_coords": KANONIERSKA, "czas_kuriera_warsaw":
               "2026-06-13T17:56:00+02:00", "status": "assigned"},
    "480430": {"restaurant_name": "Rukola Kaczorowskiego", "pickup_coords": RUKOLA,
               "delivery_coords": WYSZYNSKIEGO, "czas_kuriera_warsaw":
               "2026-06-13T17:58:00+02:00", "status": "assigned"},
    "480434": {"restaurant_name": "Raj", "pickup_coords": RAJ,
               "delivery_coords": KOPERNIKA, "czas_kuriera_warsaw":
               "2026-06-13T18:17:00+02:00", "status": "assigned"},
}


def _stop(oid, kind):
    return {"order_id": oid, "type": kind}


def _backtrack_seq():
    """Sekwencja w której kurier WRACA do Raju (stan przed fixem)."""
    return [
        _stop("480295", "pickup"),    # Raj
        _stop("480295", "dropoff"),   # Kanonierska — opuszcza okolicę Raju
        _stop("480430", "pickup"),    # Rukola
        _stop("480430", "dropoff"),
        _stop("480434", "pickup"),    # Raj ZNOWU → powrót
        _stop("480434", "dropoff"),
    ]


def _seq_ids(seq):
    return [(s["order_id"], s["type"]) for s in seq]


def _pickup_idx(seq, oid):
    return next(i for i, s in enumerate(seq)
               if s["order_id"] == oid and s["type"] == "pickup")


def test_detect_fires_on_raj_backtrack():
    viol = P._detect_departed_pickup_revisit(_backtrack_seq(), ORDERS_STATE)
    assert len(viol) == 1
    first_idx, revisit_idx, oids = viol[0]
    assert set(oids) == {"480295", "480434"}
    assert revisit_idx - first_idx >= 2  # jest stop pośredni → realny powrót


def test_detect_silent_when_raj_pickups_adjacent():
    seq = [
        _stop("480295", "pickup"),
        _stop("480434", "pickup"),    # od razu drugi z Raju — jedna wizyta
        _stop("480295", "dropoff"),
        _stop("480434", "dropoff"),
    ]
    assert P._detect_departed_pickup_revisit(seq, ORDERS_STATE) == []


def test_detect_silent_for_distinct_restaurants():
    # Dwa różne pickupy (Raj, Rukola) z dostawą pomiędzy → NIE powrót.
    seq = [_stop("480295", "pickup"), _stop("480295", "dropoff"),
           _stop("480430", "pickup"), _stop("480430", "dropoff")]
    assert P._detect_departed_pickup_revisit(seq, ORDERS_STATE) == []


def test_coalesce_pulls_second_raj_forward():
    fixed = P._coalesce_same_pickup_nodes(_backtrack_seq(), ORDERS_STATE)
    # Po fixie oba odbiory z Raju sąsiadują → jedna wizyta, brak powrotu.
    i295 = _pickup_idx(fixed, "480295")
    i434 = _pickup_idx(fixed, "480434")
    assert abs(i295 - i434) == 1, _seq_ids(fixed)
    # I po fixie nie ma już naruszenia.
    assert P._detect_departed_pickup_revisit(fixed, ORDERS_STATE) == []


def test_coalesce_keeps_every_dropoff_after_its_pickup():
    fixed = P._coalesce_same_pickup_nodes(_backtrack_seq(), ORDERS_STATE)
    pidx = {s["order_id"]: i for i, s in enumerate(fixed) if s["type"] == "pickup"}
    for i, s in enumerate(fixed):
        if s["type"] == "dropoff":
            assert pidx[s["order_id"]] < i, ("dropoff przed swoim pickupem", _seq_ids(fixed))


def test_no_orders_lost_by_coalesce():
    before = _backtrack_seq()
    after = P._coalesce_same_pickup_nodes(before, ORDERS_STATE)
    assert sorted(_seq_ids(after)) == sorted(_seq_ids(before))


def test_canon_invariants_flag_off_keeps_seq_flag_on_coalesces(monkeypatch):
    stops = _backtrack_seq()

    # Flaga OFF: detekcja loguje, ale kolejność NIE zmieniona (zero ryzyka live).
    monkeypatch.setattr(P, "ENABLE_NO_RETURN_TO_DEPARTED_PICKUP", False)
    monkeypatch.setattr(P, "ENABLE_PLAN_CANON_ORDER_INVARIANTS", True)
    off = P._apply_canon_order_invariants(list(stops), ORDERS_STATE)
    assert P._detect_departed_pickup_revisit(off, ORDERS_STATE), "OFF zostawia powrót"

    # Flaga ON: powrót wyeliminowany.
    monkeypatch.setattr(P, "ENABLE_NO_RETURN_TO_DEPARTED_PICKUP", True)
    on = P._apply_canon_order_invariants(list(stops), ORDERS_STATE)
    assert P._detect_departed_pickup_revisit(on, ORDERS_STATE) == []
    assert abs(_pickup_idx(on, "480295") - _pickup_idx(on, "480434")) == 1
