"""Regresja Sprint OBJ F4 Krok 1 (Opcja A) — proxy pozycji kuriera no-gps.

Diagnoza 474266: kurier bez świeżego GPS dostawał `cs.pos = delivery_coords`
ostatniego picked_up ordera — punkt gdzie kurier DOPIERO DOJEDZIE. Model
stawiał go w nieodwiedzonym dropie → skażona macierz → frozen window
INFEASIBLE → kaskada. Krok 1: flaga `ENABLE_F4_COURIER_POS_PICKUP_PROXY`
flipuje proxy na `pickup_coords` (restauracja, gdzie kurier BYŁ o
picked_up_at — punkt rzeczywisty). Fail-soft: brak pickup_coords →
delivery_coords (zachowanie sprzed F4).

Design: eod_drafts/2026-05-18/obj_f4_courier_position_design.md
"""
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import courier_resolver  # noqa: E402

# Koordynaty wyraźnie rozdzielne — assert który punkt trafił do cs.pos.
PICKUP = [53.1400, 23.1600]
DELIVERY = [53.1100, 23.2200]
GPS = (53.1300, 23.1700)


def _state(status, now, *, with_pickup=True, with_delivery=True):
    ts = (now - timedelta(minutes=12)).isoformat()
    rec = {
        "courier_id": "520",
        "status": status,
        "order_id": "900",
        "assigned_at": ts,
        "updated_at": ts,
    }
    if with_pickup:
        rec["pickup_coords"] = list(PICKUP)
    if with_delivery:
        rec["delivery_coords"] = list(DELIVERY)
    if status == "picked_up":
        rec["picked_up_at"] = ts
    return {"900": rec}


def _run(state, flag_on, gps=None):
    with mock.patch.object(courier_resolver, "_load_kurier_piny", return_value={}), \
         mock.patch.object(courier_resolver, "_load_courier_names",
                           return_value={"520": "Test Kurier"}), \
         mock.patch.object(courier_resolver, "_load_gps_positions",
                           return_value=gps or {}), \
         mock.patch.object(courier_resolver, "ENABLE_F4_COURIER_POS_PICKUP_PROXY",
                           flag_on), \
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

    now = datetime.now(timezone.utc)

    # ---------- TEST 1: flaga OFF — picked_up → delivery_coords (legacy) ----------
    print("\n=== test 1: flag OFF — picked_up zostaje na delivery_coords ===")
    cs = _run(_state("picked_up", now), flag_on=False).get("520")
    expect("cid=520 present", cs is not None)
    expect(f"pos_source=last_picked_up_delivery (got {cs and cs.pos_source})",
           cs and cs.pos_source == "last_picked_up_delivery")
    expect(f"pos=DELIVERY (got {cs and cs.pos})",
           cs and tuple(cs.pos) == tuple(DELIVERY))

    # ---------- TEST 2: flaga ON — picked_up → pickup_coords ----------
    print("\n=== test 2: flag ON — picked_up flipuje na pickup_coords ===")
    cs = _run(_state("picked_up", now), flag_on=True).get("520")
    expect("cid=520 present", cs is not None)
    expect(f"pos_source=last_picked_up_pickup (got {cs and cs.pos_source})",
           cs and cs.pos_source == "last_picked_up_pickup")
    expect(f"pos=PICKUP (got {cs and cs.pos})",
           cs and tuple(cs.pos) == tuple(PICKUP))

    # ---------- TEST 3: flaga ON, brak pickup_coords → fail-soft delivery ----------
    print("\n=== test 3: flag ON, brak pickup_coords — fail-soft delivery_coords ===")
    cs = _run(_state("picked_up", now, with_pickup=False), flag_on=True).get("520")
    expect("cid=520 present", cs is not None)
    expect(f"pos_source=last_picked_up_delivery fallback (got {cs and cs.pos_source})",
           cs and cs.pos_source == "last_picked_up_delivery")
    expect(f"pos=DELIVERY fallback (got {cs and cs.pos})",
           cs and tuple(cs.pos) == tuple(DELIVERY))

    # ---------- TEST 4: assigned NIE ruszony przez F4 ----------
    print("\n=== test 4: assigned (nie picked_up) — flaga F4 bez wpływu ===")
    cs = _run(_state("assigned", now), flag_on=True).get("520")
    expect("cid=520 present", cs is not None)
    expect(f"pos_source=last_assigned_pickup (got {cs and cs.pos_source})",
           cs and cs.pos_source == "last_assigned_pickup")
    expect(f"pos=PICKUP (got {cs and cs.pos})",
           cs and tuple(cs.pos) == tuple(PICKUP))

    # ---------- TEST 5: świeży GPS wygrywa nad F4 proxy ----------
    print("\n=== test 5: świeży GPS ma priorytet — F4 nie dotyczy ===")
    fresh_gps = {"520": {"lat": GPS[0], "lon": GPS[1],
                         "timestamp": now.isoformat()}}
    cs = _run(_state("picked_up", now), flag_on=True, gps=fresh_gps).get("520")
    expect("cid=520 present", cs is not None)
    expect(f"pos_source=gps (got {cs and cs.pos_source})",
           cs and cs.pos_source == "gps")
    expect(f"pos=GPS (got {cs and cs.pos})",
           cs and tuple(cs.pos) == tuple(GPS))

    # ---------- TEST 6: POS_SOURCE_PRIORITY zawiera nowe źródło ----------
    print("\n=== test 6: POS_SOURCE_PRIORITY mapuje last_picked_up_pickup ===")
    pr = courier_resolver.POS_SOURCE_PRIORITY
    expect(f"last_picked_up_pickup priority=1 (got {pr.get('last_picked_up_pickup')})",
           pr.get("last_picked_up_pickup") == 1)

    print(f"\n{'='*50}")
    print(f"PASS: {results['pass']}  FAIL: {results['fail']}")
    return results["fail"] == 0


if __name__ == "__main__":
    sys.exit(0 if main() else 1)
