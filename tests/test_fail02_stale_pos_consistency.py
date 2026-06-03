"""Regresja dla FAIL-02 (audyt autonomii 2026-06-03): spójność stale-filtra
między cs.bag a cs.pos w build_fleet_snapshot.

Scenariusz bugu: kurier odbiera jedzenie (status=picked_up), po czym znika
(telefon padł / porzucił zmianę). Panel nie zmienia statusu na 8/9. Po
BAG_STALE_THRESHOLD_MIN (90 min):
  - cs.bag jest pusty (filtr _bag_not_stale na linii 537-541) → kurier "WOLNY"
  - ALE cs.pos był liczony z NIEFILTROWANEGO active_bag_orders (linia 597) →
    zamrożone delivery/pickup coords porzuconego zlecenia → fałszywa bliskość →
    wysoki score → kurier-widmo dostawał NOWE zlecenia.

Fix: active_bag_orders używa tego samego _bag_not_stale co active_bag. Po
filtrze porzucony kurier ma pos_source='no_gps' (BIALYSTOK_CENTER) → trafia
do _demote_blind_empty (bucket 2, pod aktywnych) zamiast udawać bliskiego.

Uruchom:
    /root/.openclaw/venvs/dispatch/bin/python -m pytest tests/test_fail02_stale_pos_consistency.py -v
albo standalone:
    /root/.openclaw/venvs/dispatch/bin/python tests/test_fail02_stale_pos_consistency.py
"""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import courier_resolver  # noqa: E402

# Zamrożone coords porzuconego zlecenia (blisko restauracji → fałszywa bliskość)
FROZEN_DELIVERY = [53.135, 23.175]
FROZEN_PICKUP = [53.140, 23.160]


def _state_one_picked_up(age_min: int, now=None):
    """Stan: jeden kurier (cid=999) z jednym orderem picked_up sprzed age_min."""
    if now is None:
        now = datetime.now(timezone.utc)
    ts = (now - timedelta(minutes=age_min)).isoformat()
    return {
        "477999": {
            "courier_id": "999",
            "status": "picked_up",
            "order_id": "477999",
            "delivery_coords": FROZEN_DELIVERY,
            "pickup_coords": FROZEN_PICKUP,
            "assigned_at": ts,
            "updated_at": ts,
            "picked_up_at": ts,
        }
    }


def _run(state, gps=None):
    with mock.patch.object(courier_resolver, "_load_kurier_piny", return_value={}), \
         mock.patch.object(courier_resolver, "_load_courier_names", return_value={"999": "Widmo Test"}), \
         mock.patch.object(courier_resolver, "_load_gps_positions", return_value=gps or {}), \
         mock.patch.object(courier_resolver, "_load_courier_tiers", return_value={}), \
         mock.patch("dispatch_v2.state_machine.get_all", return_value=state):
        return courier_resolver.build_fleet_snapshot()


def test_abandoned_courier_not_positioned_at_frozen_coords():
    """FAIL-02: porzucony kurier (picked_up 120min, brak GPS) NIE może być
    pozycjonowany na zamrożonych coords zlecenia — musi spaść do no_gps."""
    fleet = _run(_state_one_picked_up(age_min=120))
    cs = fleet.get("999")
    assert cs is not None, "kurier zniknął ze snapshotu (nieoczekiwane)"
    # Bag pusty (stale filter już to robił przed fixem)
    assert len(cs.bag) == 0, f"bag powinien być pusty (stale), jest {len(cs.bag)}"
    # KLUCZOWE: pozycja NIE z zamrożonego zlecenia
    assert cs.pos_source == "no_gps", (
        f"REGRESJA FAIL-02: pos_source={cs.pos_source} (oczekiwano 'no_gps'); "
        f"pos={cs.pos} — porzucony kurier dostał zamrożoną pozycję = widmo"
    )
    assert tuple(cs.pos) == tuple(courier_resolver.BIALYSTOK_CENTER), (
        f"pos={cs.pos} powinno = BIALYSTOK_CENTER (no_gps fallback)"
    )
    assert tuple(cs.pos) != tuple(FROZEN_DELIVERY), "pos = zamrożone delivery (BUG)"
    assert tuple(cs.pos) != tuple(FROZEN_PICKUP), "pos = zamrożone pickup (BUG)"


def test_fresh_picked_up_still_positioned_at_bag():
    """Regresja-guard: świeży picked_up (10min) NADAL pozycjonowany realnie
    z worka (nie zepsuliśmy normalnego przypadku)."""
    fleet = _run(_state_one_picked_up(age_min=10))
    cs = fleet.get("999")
    assert cs is not None
    assert len(cs.bag) == 1, "świeży worek powinien zostać w bagu"
    assert cs.pos_source != "no_gps", (
        f"REGRESJA: świeży picked_up spadł do no_gps (pos_source={cs.pos_source})"
    )
    assert cs.pos_source.startswith("last_picked_up"), (
        f"świeży picked_up powinien pozycjonować z worka, jest {cs.pos_source}"
    )
    assert tuple(cs.pos) != tuple(courier_resolver.BIALYSTOK_CENTER)


def test_bag_and_pos_use_same_staleness_definition():
    """Invariant: gdy bag jest pusty PRZEZ stale-filter, pozycja też nie może
    pochodzić z bagu (jedna definicja 'aktywnego ordera' dla bag i pos)."""
    for age in (10, 89, 120, 240):
        fleet = _run(_state_one_picked_up(age_min=age))
        cs = fleet["999"]
        bag_empty = len(cs.bag) == 0
        pos_from_bag = cs.pos_source.startswith("last_picked_up") or cs.pos_source.startswith("last_assigned")
        assert not (bag_empty and pos_from_bag), (
            f"age={age}min: bag pusty ale pos z bagu ({cs.pos_source}) = niespójność FAIL-02"
        )


def main():
    results = {"pass": 0, "fail": 0}
    for fn in (
        test_abandoned_courier_not_positioned_at_frozen_coords,
        test_fresh_picked_up_still_positioned_at_bag,
        test_bag_and_pos_use_same_staleness_definition,
    ):
        try:
            fn()
            print(f"  ✅ {fn.__name__}")
            results["pass"] += 1
        except AssertionError as e:
            print(f"  ❌ {fn.__name__}: {e}")
            results["fail"] += 1
        except Exception as e:
            print(f"  💥 {fn.__name__}: {type(e).__name__}: {e}")
            results["fail"] += 1
    print(f"\n{results['pass']} PASS / {results['fail']} FAIL")
    return 1 if results["fail"] else 0


if __name__ == "__main__":
    sys.exit(main())
