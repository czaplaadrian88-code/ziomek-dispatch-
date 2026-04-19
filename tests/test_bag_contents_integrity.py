"""Regresja dla STRICT_BAG_RECONCILIATION TTL filter (V3.14 bag integrity fix).

Scenariusz bugu: 2026-04-19 15:17 Warsaw — propozycja #467117 Baanko pokazała
Michała Rom z 3-order bagiem (Arsenal Panteon, Trzy Po Trzy, Paradiso),
podczas gdy panel pokazywał tylko 2 ordery (Mama Thai, Raj). Pipeline
ufał `orders_state.status=assigned` dla orderów 12:09 UTC (3h+ stare),
panel_watcher reconcile lag 15-90 min.

Fix: `courier_resolver._bag_not_stale(order, now_utc)` — filter orderów
assigned/picked_up starszych niż BAG_STALE_THRESHOLD_MIN (90 min) bez picked_up.
"""
import importlib
import os
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import common, courier_resolver  # noqa: E402

WAW = ZoneInfo("Europe/Warsaw")


def _ts_iso(now, **delta):
    return (now + timedelta(**delta)).isoformat()


def _mock_state(orders, now=None):
    """orders: list[(oid, cid, status, age_min, extra_fields)]. age_min = minuty temu."""
    if now is None:
        now = datetime.now(timezone.utc)
    out = {}
    for o in orders:
        oid, cid, status = o[0], o[1], o[2]
        age = o[3] if len(o) > 3 else 30
        extra = o[4] if len(o) > 4 else {}
        ts = (now - timedelta(minutes=age)).isoformat()
        rec = {
            "courier_id": cid,
            "status": status,
            "order_id": oid,
            "delivery_coords": [53.13, 23.17],
            "pickup_coords": [53.14, 23.16],
            "assigned_at": ts,
            "updated_at": ts,
        }
        if status == "picked_up":
            rec["picked_up_at"] = ts
        rec.update(extra)
        out[oid] = rec
    return out


def _run(piny, names, state, gps=None):
    with mock.patch.object(courier_resolver, "_load_kurier_piny", return_value=piny), \
         mock.patch.object(courier_resolver, "_load_courier_names", return_value=names), \
         mock.patch.object(courier_resolver, "_load_gps_positions", return_value=gps or {}), \
         mock.patch("dispatch_v2.state_machine.get_all", return_value=state):
        return courier_resolver.build_fleet_snapshot()


def main():
    results = {"pass": 0, "fail": 0}

    def expect(label, cond, detail=""):
        if cond:
            print(f"  ✅ {label}")
            results["pass"] += 1
        else:
            print(f"  ❌ {label}  {detail}")
            results["fail"] += 1

    # Baseline flag state
    importlib.reload(common)
    importlib.reload(courier_resolver)
    assert common.STRICT_BAG_RECONCILIATION is True
    assert common.BAG_STALE_THRESHOLD_MIN == 90

    now = datetime.now(timezone.utc)
    piny = {}
    names = {"520": "Michał Rom", "518": "Michał Ro"}

    # ---------- TEST 1: bag_equals_panel_projection (fresh orders) ----------
    print("\n=== test 1: bag equals panel projection (fresh orders) ===")
    state = _mock_state([
        ("100", "520", "assigned", 10),
        ("101", "520", "assigned", 15),
    ], now=now)
    fleet = _run(piny, names, state)
    cs = fleet.get("520")
    expect("cid=520 present", cs is not None)
    expect(f"bag_size=2 (got {len(cs.bag) if cs else '?'})",
           cs is not None and len(cs.bag) == 2)

    # ---------- TEST 2: stale assigned filtered ----------
    print("\n=== test 2: stale assigned (>90min) filtered from bag ===")
    state = _mock_state([
        ("200", "520", "assigned", 30),   # fresh
        ("201", "520", "assigned", 120),  # stale
        ("202", "520", "assigned", 200),  # very stale
    ], now=now)
    fleet = _run(piny, names, state)
    bag_oids = {o["order_id"] for o in fleet["520"].bag}
    expect("fresh 200 in bag", "200" in bag_oids)
    expect("stale 201 filtered", "201" not in bag_oids)
    expect("very stale 202 filtered", "202" not in bag_oids)

    # ---------- TEST 3: fresh assigned stays ----------
    print("\n=== test 3: fresh assigned (30min) in bag ===")
    state = _mock_state([("300", "520", "assigned", 30)], now=now)
    fleet = _run(piny, names, state)
    expect("bag=1 fresh", len(fleet["520"].bag) == 1)

    # ---------- TEST 4: czasówka w przyszłości not filtered even if assigned_at stare ----------
    print("\n=== test 4: czasówka (pickup_at future) — legit assigned, not stale ===")
    future_pu = (now + timedelta(hours=1)).astimezone(WAW).isoformat()
    state = _mock_state([
        ("400", "520", "assigned", 120, {"pickup_at_warsaw": future_pu}),
    ], now=now)
    fleet = _run(piny, names, state)
    expect("czasówka w bagu mimo old assigned_at", len(fleet["520"].bag) == 1)

    # ---------- TEST 5: fresh picked_up stays ----------
    print("\n=== test 5: fresh picked_up (30min) in bag ===")
    state = _mock_state([("500", "520", "picked_up", 30)], now=now)
    fleet = _run(piny, names, state)
    expect("fresh picked_up in bag", len(fleet["520"].bag) == 1)

    # ---------- TEST 6: stale picked_up filtered ----------
    print("\n=== test 6: stale picked_up (>90min bez delivered) filtered ===")
    state = _mock_state([("600", "520", "picked_up", 120)], now=now)
    fleet = _run(piny, names, state)
    expect("stale picked_up filtered", len(fleet["520"].bag) == 0)

    # ---------- TEST 7: delivered status always excluded (regresja L218) ----------
    print("\n=== test 7: delivered status excluded regardless of TTL ===")
    state = _mock_state([
        ("700", "520", "assigned", 30),       # fresh, in bag
        ("701", "520", "delivered", 10),      # delivered, excluded
        ("702", "520", "returned_to_pool", 10),  # returned, excluded
    ], now=now)
    fleet = _run(piny, names, state)
    bag_oids = {o["order_id"] for o in fleet["520"].bag}
    expect("fresh 700 in bag", "700" in bag_oids)
    expect("delivered 701 NOT in bag", "701" not in bag_oids)
    expect("returned 702 NOT in bag", "702" not in bag_oids)

    # ---------- TEST 8: regression 467117 Michał Rom (3 stale + 2 fresh) ----------
    print("\n=== test 8: regression 467117 — 3 stale + 2 fresh → bag=2 ===")
    # Symuluje sytuację @ 15:26 Warsaw (13:26 UTC): Michał Rom ma w state
    # 3 "phantom" ordery z 12:09 (3h+ stare, status=assigned bo reconcile nie dogonił)
    # + 2 świeże zassignowane niedawno (Mama Thai, Raj).
    state = _mock_state([
        ("467015", "520", "assigned", 200),  # phantom (stale assigned z panel_initial)
        ("467053", "520", "assigned", 195),  # phantom
        ("467070", "520", "assigned", 190),  # phantom
        ("467099", "520", "assigned", 15),   # real fresh (Mama Thai)
        ("467108", "520", "assigned", 5),    # real fresh (Raj)
    ], now=now)
    fleet = _run(piny, names, state)
    bag_oids = {o["order_id"] for o in fleet["520"].bag}
    expect("phantom 467015 NOT in bag", "467015" not in bag_oids)
    expect("phantom 467053 NOT in bag", "467053" not in bag_oids)
    expect("phantom 467070 NOT in bag", "467070" not in bag_oids)
    expect("real 467099 Mama Thai IN bag", "467099" in bag_oids)
    expect("real 467108 Raj IN bag", "467108" in bag_oids)
    expect(f"bag_size=2 (got {len(fleet['520'].bag)})", len(fleet["520"].bag) == 2)

    # ---------- TEST 9: flag False preserves legacy (no TTL) ----------
    print("\n=== test 9: STRICT=False preserves legacy bag (no TTL) ===")
    orig_flag = common.STRICT_BAG_RECONCILIATION
    common.STRICT_BAG_RECONCILIATION = False
    try:
        state = _mock_state([
            ("900", "520", "assigned", 30),    # fresh
            ("901", "520", "assigned", 200),   # stale — but legacy keeps
        ], now=now)
        fleet = _run(piny, names, state)
        expect("legacy: stale 901 IN bag",
               "901" in {o["order_id"] for o in fleet["520"].bag})
        expect("legacy: bag_size=2", len(fleet["520"].bag) == 2)
    finally:
        common.STRICT_BAG_RECONCILIATION = orig_flag
    assert common.STRICT_BAG_RECONCILIATION is True

    # ---------- TEST 10: no timestamp defensive keep ----------
    print("\n=== test 10: no timestamp → defensive keep (lepiej false positive) ===")
    state = {
        "T10": {
            "courier_id": "520", "status": "assigned", "order_id": "T10",
            "delivery_coords": [53.13, 23.17], "pickup_coords": [53.14, 23.16],
            # no assigned_at/updated_at/picked_up_at
        },
    }
    fleet = _run(piny, names, state)
    expect("no timestamp in bag (defensive)",
           "T10" in {o["order_id"] for o in fleet["520"].bag})

    # ---------- TEST 11: threshold boundary (89 min = fresh, 91 min = stale) ----------
    print("\n=== test 11: threshold boundary (90 min) ===")
    state = _mock_state([
        ("B1", "520", "assigned", 89),   # just fresh
        ("B2", "520", "assigned", 91),   # just stale
    ], now=now)
    fleet = _run(piny, names, state)
    bag_oids = {o["order_id"] for o in fleet["520"].bag}
    expect("89 min IN bag (under threshold)", "B1" in bag_oids)
    expect("91 min NOT in bag (over threshold)", "B2" not in bag_oids)

    # ---------- TEST 12: no cross-courier contamination (sanity) ----------
    print("\n=== test 12: no cross-courier contamination (regression sanity) ===")
    state = _mock_state([
        ("X1", "520", "assigned", 20),
        ("X2", "518", "assigned", 20),
    ], now=now)
    fleet = _run(piny, names, state)
    oids_520 = {o["order_id"] for o in fleet["520"].bag}
    oids_518 = {o["order_id"] for o in fleet["518"].bag}
    expect("X1 in 520, not in 518", "X1" in oids_520 and "X1" not in oids_518)
    expect("X2 in 518, not in 520", "X2" in oids_518 and "X2" not in oids_520)

    # ---------- FINAL ----------
    total = results["pass"] + results["fail"]
    print()
    print("=" * 60)
    print(f"BAG_CONTENTS_INTEGRITY: {results['pass']}/{total} PASS")
    print("=" * 60)
    sys.exit(0 if results["fail"] == 0 else 1)


if __name__ == "__main__":
    main()
