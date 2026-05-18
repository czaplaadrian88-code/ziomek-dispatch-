"""Faza 4 (D5, 2026-05-18) — rekonstrukcja cs.bag z panel_packs ground-truth.

build_fleet_snapshot grupuje zlecenia po courier_id → gubi te z cid=None (lag
V3.15 reconcile). panel_packs_cache podaje prawdziwe mapowanie kurier→oid →
_reconstruct_bag_from_panel_packs odbudowuje cs.bag lookupem po order_id.

Coverage:
  1. Happy path — oid w state z cid=None → bag odbudowany, pos_source≠no_gps
  2. Fallback — oid spoza state → bag pusty, panel_packs_oids_signal zostaje
  3. No-op — kurier ma realny bag (cid pasuje) → rekonstrukcja pominięta
  4. Terminal — oid w state ze statusem delivered → pominięty (nie aktywny bag)
  5. Flag OFF — ENABLE_PANEL_PACKS_BAG_RECONSTRUCTION=false → brak rekonstrukcji
  6. picked_up → pos z delivery_coords (pos_source=last_picked_up_delivery)

Pattern: monkeypatch loaderów build_fleet_snapshot. Custom runner.
"""
import os
import sys
from datetime import datetime, timezone

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from dispatch_v2 import courier_resolver as cr
from dispatch_v2 import state_machine

passed = 0
failed = 0


def check(label, cond, detail=""):
    global passed, failed
    if cond:
        passed += 1
        print(f"  OK {passed}. {label}")
    else:
        failed += 1
        print(f"  FAIL {passed + failed}. {label} {detail}")


_NOW = datetime.now(timezone.utc).isoformat()


def _run_snapshot(orders_state, packs, names=None, flag_on=True):
    """Wywołuje build_fleet_snapshot z zpatchowanymi loaderami. Zwraca fleet."""
    names = names or {"515": "Szymon P"}
    orig = {
        "get_all": state_machine.get_all,
        "piny": cr._load_kurier_piny,
        "names": cr._load_courier_names,
        "gps": cr._load_gps_positions,
        "tiers": cr._load_courier_tiers,
        "packs": cr._load_panel_packs_cache,
        "flag": cr.flag,
    }
    state_machine.get_all = lambda: orders_state
    cr._load_kurier_piny = lambda: {}
    cr._load_courier_names = lambda: names
    cr._load_gps_positions = lambda: {}
    cr._load_courier_tiers = lambda: {}
    cr._load_panel_packs_cache = lambda: (datetime.now(timezone.utc), packs, 10.0)
    cr.flag = lambda name, default=False: (
        flag_on if name == "ENABLE_PANEL_PACKS_BAG_RECONSTRUCTION"
        else orig["flag"](name, default))
    try:
        return cr.build_fleet_snapshot()
    finally:
        state_machine.get_all = orig["get_all"]
        cr._load_kurier_piny = orig["piny"]
        cr._load_courier_names = orig["names"]
        cr._load_gps_positions = orig["gps"]
        cr._load_courier_tiers = orig["tiers"]
        cr._load_panel_packs_cache = orig["packs"]
        cr.flag = orig["flag"]


print("=== Faza 4: panel_packs bag reconstruction ===")

# Test 1 — happy path: oid w state z cid=None → odbudowa
fleet = _run_snapshot(
    orders_state={"9001": {"status": "assigned", "courier_id": None,
                           "assigned_at": _NOW,
                           "pickup_coords": [53.10, 23.10],
                           "delivery_coords": [53.20, 23.20]}},
    packs={"Szymon P": ["9001"]},
)
cs = fleet.get("515")
check("1a. kurier 515 w fleet", cs is not None)
check("1b. bag odbudowany — 1 zlecenie", cs is not None and len(cs.bag) == 1)
check("1c. bag[0] order_id=9001", cs is not None and cs.bag
      and cs.bag[0].get("order_id") == "9001")
check("1d. bag[0] courier_id skorygowany na 515", cs is not None and cs.bag
      and cs.bag[0].get("courier_id") == "515")
check("1e. bag_from_panel_packs=True", cs is not None and cs.bag_from_panel_packs is True)
check("1f. pos_source=last_assigned_pickup (nie no_gps)",
      cs is not None and cs.pos_source == "last_assigned_pickup")
check("1g. pos = pickup_coords", cs is not None and cs.pos == (53.10, 23.10))

# Test 2 — fallback: oid spoza state → brak odbudowy, sygnał zostaje
fleet = _run_snapshot(
    orders_state={},
    packs={"Szymon P": ["9999"]},
)
cs = fleet.get("515")
check("2a. bag pusty (oid spoza state nieodtwarzalny)",
      cs is not None and len(cs.bag) == 0)
check("2b. panel_packs_oids_signal zachowany jako fallback",
      cs is not None and cs.panel_packs_oids_signal == ["9999"])
check("2c. bag_from_panel_packs=False", cs is not None and cs.bag_from_panel_packs is False)
check("2d. pos_source=no_gps (fallback)", cs is not None and cs.pos_source == "no_gps")

# Test 3 — no-op: kurier ma realny bag (cid pasuje) → rekonstrukcja pominięta
fleet = _run_snapshot(
    orders_state={"9002": {"status": "assigned", "courier_id": "515",
                           "assigned_at": _NOW,
                           "pickup_coords": [53.11, 23.11],
                           "delivery_coords": [53.21, 23.21]}},
    packs={"Szymon P": ["9002", "9001"]},
)
cs = fleet.get("515")
check("3a. bag z orders_state (1 zlecenie)", cs is not None and len(cs.bag) == 1)
check("3b. bag_from_panel_packs=False (bag już niepusty → skip)",
      cs is not None and cs.bag_from_panel_packs is False)

# Test 4 — terminal status pominięty
fleet = _run_snapshot(
    orders_state={"9003": {"status": "delivered", "courier_id": None,
                           "delivered_at": _NOW,
                           "pickup_coords": [53.12, 23.12],
                           "delivery_coords": [53.22, 23.22]}},
    packs={"Szymon P": ["9003"]},
)
cs = fleet.get("515")
check("4a. bag pusty (delivered nie należy do aktywnego bagu)",
      cs is not None and len(cs.bag) == 0)
check("4b. bag_from_panel_packs=False", cs is not None and cs.bag_from_panel_packs is False)

# Test 5 — flag OFF → brak rekonstrukcji
fleet = _run_snapshot(
    orders_state={"9001": {"status": "assigned", "courier_id": None,
                           "assigned_at": _NOW,
                           "pickup_coords": [53.10, 23.10],
                           "delivery_coords": [53.20, 23.20]}},
    packs={"Szymon P": ["9001"]},
    flag_on=False,
)
cs = fleet.get("515")
check("5a. flag OFF → bag NIE odbudowany", cs is not None and len(cs.bag) == 0)
check("5b. flag OFF → panel_packs_oids_signal nadal ustawiony (fallback)",
      cs is not None and cs.panel_packs_oids_signal == ["9001"])
check("5c. flag OFF → bag_from_panel_packs=False",
      cs is not None and cs.bag_from_panel_packs is False)

# Test 6 — picked_up → pos z delivery_coords
fleet = _run_snapshot(
    orders_state={"9004": {"status": "picked_up", "courier_id": None,
                           "picked_up_at": _NOW,
                           "pickup_coords": [53.13, 23.13],
                           "delivery_coords": [53.23, 23.23]}},
    packs={"Szymon P": ["9004"]},
)
cs = fleet.get("515")
check("6a. bag odbudowany (picked_up)", cs is not None and len(cs.bag) == 1)
check("6b. pos_source=last_picked_up_delivery",
      cs is not None and cs.pos_source == "last_picked_up_delivery")
check("6c. pos = delivery_coords", cs is not None and cs.pos == (53.23, 23.23))

print("\n" + "=" * 60)
print(f"FAZA 4 PANEL_PACKS BAG RECONSTRUCTION: {passed}/{passed + failed} PASS"
      if failed == 0 else
      f"FAZA 4: {passed}/{passed + failed} PASS, {failed} FAIL")
print("=" * 60)

if failed:
    sys.exit(1)
