"""Regresja dla STRICT_COURIER_ID_SPACE fix (2026-04-19 availability bug).

Scenariusz bugu: 2026-04-19 14:00-14:08 Warsaw — 8 propozycji
(#467070-#467077) pokazały identyczną trójkę "wolnych" kandydatów
(Michał Ro cid=5333-PIN, Aleksander G, Gabriel J) mimo że panel pokazywał
każdego z 2-3 orderami w bagach. Root cause: build_fleet_snapshot dodawał
keys z kurier_piny.json (PIN-y 4-digit) jako osobnych kurierów → duplikaty
z pustym bagiem → no_gps fallback → fałszywa propozycja.

Fix: `common.STRICT_COURIER_ID_SPACE = True` (default) wyklucza piny.keys()
z fleet snapshot courier_id space. PIN pozostaje name-lookup fallback.
"""
import importlib
import os
import sys
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import common, courier_resolver  # noqa: E402


def _mock_state(orders):
    """Build a state_machine.get_all() return dict from list of (oid, cid, status) tuples."""
    return {
        oid: {
            "courier_id": cid,
            "status": status,
            "order_id": oid,
            "delivery_coords": [53.13, 23.17],
            "pickup_coords": [53.14, 23.16],
            "assigned_at": "2026-04-19T12:00:00+00:00",
        }
        for oid, cid, status in orders
    }


def _run_with_patches(piny, names, state, ids=None, gps=None):
    """Call build_fleet_snapshot with patched loaders."""
    ids = ids or {}
    gps = gps or {}
    with mock.patch.object(courier_resolver, "_load_kurier_piny", return_value=piny), \
         mock.patch.object(courier_resolver, "_load_courier_names", return_value=names), \
         mock.patch.object(courier_resolver, "_load_gps_positions", return_value=gps), \
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

    # Ensure flag True baseline
    orig_flag_env = os.environ.pop("STRICT_COURIER_ID_SPACE", None)
    importlib.reload(common)
    importlib.reload(courier_resolver)
    assert common.STRICT_COURIER_ID_SPACE is True

    # ---------- TEST 1: PIN not in fleet snapshot ----------
    print("\n=== test 1: PIN not in fleet snapshot (STRICT=True) ===")
    piny = {"5333": "Michał Ro", "2824": "Michał Rom", "9999": "Phantom Courier"}
    names = {"518": "Michał Ro", "520": "Michał Rom"}
    state = _mock_state([
        ("467015", "520", "assigned"),
        ("467070", "520", "assigned"),
    ])
    fleet = _run_with_patches(piny, names, state)
    expect("cid=5333 (PIN) NOT in fleet", "5333" not in fleet)
    expect("cid=2824 (PIN Michał Rom) NOT in fleet", "2824" not in fleet)
    expect("cid=9999 (PIN Phantom) NOT in fleet", "9999" not in fleet)
    expect("cid=518 (real) in fleet", "518" in fleet)
    expect("cid=520 (real) in fleet with bag=2", "520" in fleet and len(fleet["520"].bag) == 2)

    # ---------- TEST 2: No PIN duplicates (one Michał Ro, not two) ----------
    print("\n=== test 2: no PIN duplicates for same courier ===")
    michal_ro_entries = [cid for cid, cs in fleet.items() if cs.name == "Michał Ro"]
    expect("exactly 1 Michał Ro in fleet", len(michal_ro_entries) == 1,
           f"got {michal_ro_entries}")
    expect("Michał Ro entry uses real cid=518", michal_ro_entries == ["518"])

    # ---------- TEST 3: Courier with active bag has bag_size>0 (not free) ----------
    print("\n=== test 3: courier with active bag not flagged as free ===")
    state_with_bag = _mock_state([
        ("467015", "520", "assigned"),
        ("467053", "520", "assigned"),
        ("467070", "520", "picked_up"),
    ])
    fleet = _run_with_patches(piny, names, state_with_bag)
    cs_520 = fleet.get("520")
    expect("cid=520 in fleet", cs_520 is not None)
    expect(f"cid=520 bag_size==3 (got {len(cs_520.bag) if cs_520 else '?'})",
           cs_520 is not None and len(cs_520.bag) == 3)

    # ---------- TEST 4: No-GPS courier with bag gets pos from bag, not BIALYSTOK_CENTER ----------
    print("\n=== test 4: no_gps courier with bag uses bag pos (not synthetic) ===")
    # bag present → pos_source should be last_assigned_pickup or last_picked_up_delivery
    expect("pos_source NOT 'no_gps' when bag exists",
           cs_520.pos_source in ("last_assigned_pickup", "last_picked_up_delivery"),
           f"got {cs_520.pos_source}")
    expect("pos NOT BIALYSTOK_CENTER (synthetic)",
           tuple(cs_520.pos) != courier_resolver.BIALYSTOK_CENTER,
           f"got {cs_520.pos}")

    # ---------- TEST 5: PIN-only courier (no real cid) disappears under STRICT ----------
    print("\n=== test 5: PIN-only courier (no real cid mapping) skipped ===")
    piny_phantom = {"5333": "Michał Ro", "9999": "Phantom Only PIN"}
    names_limited = {"518": "Michał Ro"}  # 9999 has no real cid
    state_michal = _mock_state([("467015", "518", "assigned")])
    fleet = _run_with_patches(piny_phantom, names_limited, state_michal)
    expect("PIN-only phantom 9999 absent from fleet", "9999" not in fleet)
    expect("Michał Ro (518) still present", "518" in fleet)

    # ---------- TEST 6: regression 467070-467077 (fleet shape matches post-fix) ----------
    print("\n=== test 6: regression 467070-477 — real couriers with bags, no phantom PIN ===")
    # Fixture: mirrors state 14:09:50 UTC (post panel_watcher catch-up)
    piny_real = {
        "5333": "Michał Ro",
        "2824": "Michał Rom",
        "4657": "Bartek O.",
        "3286": "Gabriel",
    }
    names_real = {
        "518": "Michał Ro",
        "520": "Michał Rom",
        "387": "Aleksander G",
        "503": "Gabriel J",
        "400": "Adrian R",
        "123": "Bartek O.",
    }
    state_real = _mock_state([
        ("467015", "520", "assigned"),
        ("467053", "520", "assigned"),
        ("467070", "520", "assigned"),
        ("467052", "518", "assigned"),
        ("467076", "518", "assigned"),
        ("467077", "518", "assigned"),
        ("467005", "387", "assigned"),
        ("467062", "387", "assigned"),
        ("467045", "503", "assigned"),
        ("467065", "503", "assigned"),
    ])
    fleet = _run_with_patches(piny_real, names_real, state_real)
    for phantom_cid in ["5333", "2824", "4657", "3286"]:
        expect(f"phantom PIN cid={phantom_cid} NOT in fleet", phantom_cid not in fleet)
    for real_cid, expected_bag in [("518", 3), ("520", 3), ("387", 2), ("503", 2)]:
        cs = fleet.get(real_cid)
        expect(f"real cid={real_cid} has bag_size={expected_bag}",
               cs is not None and len(cs.bag) == expected_bag,
               f"got bag={len(cs.bag) if cs else '?'}")

    # ---------- TEST 7: legacy flag preserves old behavior ----------
    # Uwaga: dedup L353-371 w courier_resolver usuwa duplikaty po `cs.name`
    # (priority by pos_source). Pod legacy gdy Michał Ro 518 ma bag, dedup
    # usuwa phantom 5333. Żeby przetestować legacy behavior bez dedup,
    # używamy PIN-only courier bez real cid/name collision.
    print("\n=== test 7: flag False preserves legacy (PIN-only courier in fleet) ===")
    orig_flag = common.STRICT_COURIER_ID_SPACE
    piny_phantom = {"9999": "PhantomOnly Courier"}
    names_empty = {"518": "Michał Ro"}  # no Phantom in names
    state_empty = _mock_state([("467015", "518", "assigned")])
    # Under STRICT → no 9999
    common.STRICT_COURIER_ID_SPACE = True
    fleet_strict = _run_with_patches(piny_phantom, names_empty, state_empty)
    expect("STRICT=True: PIN-only 9999 NOT in fleet", "9999" not in fleet_strict)
    # Under legacy → 9999 in fleet
    common.STRICT_COURIER_ID_SPACE = False
    try:
        fleet_legacy = _run_with_patches(piny_phantom, names_empty, state_empty)
        expect("STRICT=False: PIN-only 9999 IN fleet (legacy)",
               "9999" in fleet_legacy,
               f"fleet keys: {sorted(fleet_legacy.keys())}")
        expect("STRICT=False: cid=518 (real) also in fleet", "518" in fleet_legacy)
    finally:
        common.STRICT_COURIER_ID_SPACE = orig_flag
    assert common.STRICT_COURIER_ID_SPACE is True

    # ---------- TEST 8: delivered/cancelled status clears bag ----------
    print("\n=== test 8: status delivered/cancelled NOT in active_bag ===")
    state_mixed = _mock_state([
        ("100", "520", "assigned"),
        ("101", "520", "picked_up"),
        ("102", "520", "delivered"),      # should not count
        ("103", "520", "returned_to_pool"),  # should not count
    ])
    # Panel-level cancelled in state_machine maps to 'cancelled' or 'returned_to_pool'
    # panel_client STATUS_MAP 8=nieodebrano/9=anulowane — in orders_state these become
    # 'cancelled' (not in normal lifecycle); only assigned+picked_up are active_bag.
    fleet = _run_with_patches({}, {"520": "Michał Rom"}, state_mixed)
    cs = fleet.get("520")
    expect("active bag excludes delivered/returned",
           cs is not None and len(cs.bag) == 2,
           f"got bag_size={len(cs.bag) if cs else '?'}")

    # ---------- TEST 9: defensive warning when PIN leaks to names (defense-in-depth) ----------
    print("\n=== test 9: defensive warning when PIN leaks to per_courier/names ===")
    # PIN 5333 intentionally placed in names (bug elsewhere)
    piny_leaked = {"5333": "Michał Ro"}
    names_leaked = {"5333": "Michał Ro (PIN LEAKED)", "518": "Michał Ro"}
    import io
    import logging
    log_buf = io.StringIO()
    handler = logging.StreamHandler(log_buf)
    handler.setLevel(logging.WARNING)
    courier_resolver._log.addHandler(handler)
    try:
        fleet = _run_with_patches(piny_leaked, names_leaked, {})
        handler.flush()
        out = log_buf.getvalue()
        expect("warning logged when PIN in names (leak detection)",
               "PIN leaked into courier_id space" in out,
               f"log output: {out!r}")
    finally:
        courier_resolver._log.removeHandler(handler)

    # Restore env if had prior value
    if orig_flag_env is not None:
        os.environ["STRICT_COURIER_ID_SPACE"] = orig_flag_env
        importlib.reload(common)
        importlib.reload(courier_resolver)

    # ---------- FINAL ----------
    total = results["pass"] + results["fail"]
    print()
    print("=" * 60)
    print(f"PANEL_AWARE_AVAILABILITY: {results['pass']}/{total} PASS")
    print("=" * 60)
    sys.exit(0 if results["fail"] == 0 else 1)


if __name__ == "__main__":
    main()
