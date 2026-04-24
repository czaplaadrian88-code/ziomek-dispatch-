"""V3.26 STEP 6 (R-07 v2 CHAIN-ETA ENGINE) — 15 test cases per Adrian spec.

Pure function tests z injected osrm/haversine mocks. Zero network.
"""
import importlib
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import common, chain_eta  # noqa: E402


class _Ord:
    def __init__(self, oid, status, pu, scheduled):
        self.order_id = oid
        self.status = status
        self.pickup_coords = pu
        self.pickup_ready_at = scheduled


def _mk_osrm(distance_map):
    """distance_map: {(from, to): drive_min}. Returns callable."""
    def fn(a, b):
        k1 = (tuple(a), tuple(b))
        k2 = (tuple(b), tuple(a))
        if k1 in distance_map:
            return distance_map[k1]
        if k2 in distance_map:
            return distance_map[k2]
        return None  # fallback trigger
    return fn


def _mk_hav(distance_map):
    def fn(a, b):
        k1 = (tuple(a), tuple(b))
        k2 = (tuple(b), tuple(a))
        if k1 in distance_map:
            return distance_map[k1]
        if k2 in distance_map:
            return distance_map[k2]
        return 1.0
    return fn


# Fixed locations
POS_GPS = (53.14, 23.16)
SIOUX_PU = (53.132, 23.158)
MAMA_PU = (53.122, 23.146)
KEBAB_PU = (53.115, 23.165)
PROPOSAL_PU = MAMA_PU


def main():
    results = {"pass": 0, "fail": 0}
    def expect(label, cond, detail=""):
        if cond:
            print(f"  ✅ {label}")
            results["pass"] += 1
        else:
            print(f"  ❌ {label}  {detail}")
            results["fail"] += 1

    now = datetime(2026, 4, 24, 10, 0, 0, tzinfo=timezone.utc)
    importlib.reload(common)

    # --- T1: GPS fresh, wcześnie przed scheduled ---
    print("\n=== T1 GPS fresh, wcześnie — max=scheduled ===")
    # now 10:00, Sioux scheduled 10:10, proposal Mama Thai scheduled 10:20
    # courier 5 min od Sioux pickup
    osrm = _mk_osrm({
        (POS_GPS, SIOUX_PU): 5.0,
        (SIOUX_PU, MAMA_PU): 3.0,
    })
    hav = _mk_hav({(POS_GPS, MAMA_PU): 1.0})
    bag = [_Ord("s1", "assigned", SIOUX_PU, now + timedelta(minutes=10))]
    r = chain_eta.compute_chain_eta(
        courier_pos=POS_GPS, pos_source="gps", pos_age_min=1,
        bag_orders=bag,
        proposal_pickup_coords=MAMA_PU,
        proposal_scheduled_utc=now + timedelta(minutes=20),
        now_utc=now,
        osrm_drive_min=osrm, haversine_km=hav,
    )
    # Effective Sioux = max(10:05, 10:10) = 10:10
    # + 2 pickup + 3 drive = 10:15 → max(10:15, 10:20) = 10:20
    expected = now + timedelta(minutes=20)
    expect("T1 effective_eta == scheduled 10:20",
           r.effective_eta_utc == expected, f"got {r.effective_eta_utc}")
    expect("T1 starting_point == 'gps'", r.starting_point == "gps")

    # --- T2: GPS fresh, spóźniony (scheduled propagated) ---
    print("\n=== T2 GPS fresh, late na Sioux → scheduled propagated ===")
    # drive 10 min do Sioux, Sioux scheduled za 2 min
    osrm2 = _mk_osrm({
        (POS_GPS, SIOUX_PU): 10.0,
        (SIOUX_PU, MAMA_PU): 3.0,
    })
    bag = [_Ord("s1", "assigned", SIOUX_PU, now + timedelta(minutes=2))]
    r = chain_eta.compute_chain_eta(
        courier_pos=POS_GPS, pos_source="gps", pos_age_min=1,
        bag_orders=bag,
        proposal_pickup_coords=MAMA_PU,
        proposal_scheduled_utc=now + timedelta(minutes=20),
        now_utc=now, osrm_drive_min=osrm2, haversine_km=hav,
    )
    # Effective Sioux = max(now+10, now+2) = now+10
    # + 2 + 3 = now+15 → max(now+15, now+20) = now+20
    expect("T2 effective_eta == now+20 (scheduled)",
           r.effective_eta_utc == now + timedelta(minutes=20),
           f"got {r.effective_eta_utc}")

    # --- T3: GPS fresh, duży delay, scheduled dawno minął ---
    print("\n=== T3 GPS fresh, duży delay, scheduled minął → chain arrival ===")
    osrm3 = _mk_osrm({(POS_GPS, SIOUX_PU): 15.0, (SIOUX_PU, MAMA_PU): 3.0})
    bag = [_Ord("s1", "assigned", SIOUX_PU, now - timedelta(minutes=5))]  # minął 5 min temu
    r = chain_eta.compute_chain_eta(
        courier_pos=POS_GPS, pos_source="gps", pos_age_min=1,
        bag_orders=bag,
        proposal_pickup_coords=MAMA_PU,
        proposal_scheduled_utc=now - timedelta(minutes=10),  # też minął
        now_utc=now, osrm_drive_min=osrm3, haversine_km=hav,
    )
    # Effective Sioux = max(now+15, now-5) = now+15
    # + 2 + 3 = now+20 → max(now+20, now-10) = now+20
    expect("T3 effective_eta == now+20 (chain arrival)",
           r.effective_eta_utc == now + timedelta(minutes=20))

    # --- T4: Brak GPS, po scheduled, status != picked_up ---
    print("\n=== T4 No GPS, po scheduled → scheduled + 5 min buffer ===")
    bag = [_Ord("s1", "assigned", SIOUX_PU, now - timedelta(minutes=11))]
    osrm4 = _mk_osrm({(SIOUX_PU, MAMA_PU): 5.0})
    r = chain_eta.compute_chain_eta(
        courier_pos=SIOUX_PU, pos_source="last_assigned_pickup", pos_age_min=None,
        bag_orders=bag,
        proposal_pickup_coords=MAMA_PU,
        proposal_scheduled_utc=now + timedelta(minutes=12),
        now_utc=now, osrm_drive_min=osrm4, haversine_km=hav,
    )
    # Sioux effective = scheduled(-11) + 5 = now-6
    # + 2 + 5 = now+1 → max(now+1, now+12) = now+12
    expect("T4 starting_point == 'no_gps_buffer'",
           r.starting_point == "no_gps_buffer")
    expect("T4 effective_eta == now+12 (scheduled)",
           r.effective_eta_utc == now + timedelta(minutes=12))

    # --- T5: Brak GPS, przed scheduled ---
    print("\n=== T5 No GPS, przed scheduled → scheduled (bez bufora) ===")
    bag = [_Ord("s1", "assigned", SIOUX_PU, now + timedelta(minutes=10))]
    r = chain_eta.compute_chain_eta(
        courier_pos=SIOUX_PU, pos_source="last_assigned_pickup", pos_age_min=None,
        bag_orders=bag,
        proposal_pickup_coords=MAMA_PU,
        proposal_scheduled_utc=now + timedelta(minutes=20),
        now_utc=now, osrm_drive_min=osrm4, haversine_km=hav,
    )
    # Sioux effective = scheduled (now+10) — bez bufora
    # + 2 + 5 = now+17 → max(now+17, now+20) = now+20
    expect("T5 starting_point == 'scheduled'", r.starting_point == "scheduled")
    expect("T5 effective_eta == now+20", r.effective_eta_utc == now + timedelta(minutes=20))

    # --- T6: Pusty bag, GPS fresh ---
    print("\n=== T6 Pusty bag + GPS fresh → direct drive ===")
    osrm6 = _mk_osrm({(POS_GPS, MAMA_PU): 7.0})
    r = chain_eta.compute_chain_eta(
        courier_pos=POS_GPS, pos_source="gps", pos_age_min=1,
        bag_orders=[],
        proposal_pickup_coords=MAMA_PU,
        proposal_scheduled_utc=now + timedelta(minutes=5),
        now_utc=now, osrm_drive_min=osrm6, haversine_km=hav,
    )
    # Direct 7 min → max(now+7, now+5) = now+7
    expect("T6 starting_point == 'gps'", r.starting_point == "gps")
    expect("T6 effective_eta == now+7", r.effective_eta_utc == now + timedelta(minutes=7))

    # --- T7: Pusty bag, no GPS, last_known_pos ---
    print("\n=== T7 Pusty bag + no GPS + last_known → last_known_fallback ===")
    osrm7 = _mk_osrm({(POS_GPS, MAMA_PU): 9.0})
    r = chain_eta.compute_chain_eta(
        courier_pos=POS_GPS, pos_source="last_picked_up_delivery", pos_age_min=None,
        bag_orders=[],
        proposal_pickup_coords=MAMA_PU,
        proposal_scheduled_utc=now + timedelta(minutes=20),
        now_utc=now, osrm_drive_min=osrm7, haversine_km=hav,
    )
    expect("T7 starting_point == 'last_known_fallback'",
           r.starting_point == "last_known_fallback")
    expect("T7 effective_eta == now+20 (scheduled)",
           r.effective_eta_utc == now + timedelta(minutes=20))

    # --- T8: Bag z 3 unpicked + proposal, GPS fresh — full chain ---
    print("\n=== T8 Bag 3 unpicked, chain kumulacja ===")
    osrm8 = _mk_osrm({
        (POS_GPS, SIOUX_PU): 3.0,
        (SIOUX_PU, KEBAB_PU): 4.0,
        (KEBAB_PU, MAMA_PU): 5.0,
    })
    bag = [
        _Ord("o1", "assigned", SIOUX_PU, now + timedelta(minutes=5)),
        _Ord("o2", "assigned", KEBAB_PU, now + timedelta(minutes=10)),
    ]
    r = chain_eta.compute_chain_eta(
        courier_pos=POS_GPS, pos_source="gps", pos_age_min=1,
        bag_orders=bag,
        proposal_pickup_coords=MAMA_PU,
        proposal_scheduled_utc=now + timedelta(minutes=15),
        now_utc=now, osrm_drive_min=osrm8, haversine_km=hav,
    )
    # Sioux eff = max(now+3, now+5) = now+5
    # + 2 + 4 = now+11 → max(now+11, now+10) = now+11 (Kebab eff)
    # + 2 + 5 = now+18 → max(now+18, now+15) = now+18 (Mama Thai)
    expect("T8 effective_eta == now+18", r.effective_eta_utc == now + timedelta(minutes=18),
           f"got {r.effective_eta_utc}")
    expect("T8 chain_details len == 3 (2 bag + proposal)",
           len(r.chain_details) == 3, f"got {len(r.chain_details)}")

    # --- T9: Bag mixed (2 picked_up + 1 unpicked) ---
    print("\n=== T9 Mixed 2 picked_up + 1 unpicked ===")
    bag = [
        _Ord("o1", "picked_up", SIOUX_PU, now - timedelta(minutes=10)),
        _Ord("o2", "picked_up", KEBAB_PU, now - timedelta(minutes=5)),
        _Ord("o3", "assigned", SIOUX_PU, now + timedelta(minutes=5)),
    ]
    r = chain_eta.compute_chain_eta(
        courier_pos=POS_GPS, pos_source="gps", pos_age_min=1,
        bag_orders=bag,
        proposal_pickup_coords=MAMA_PU,
        proposal_scheduled_utc=now + timedelta(minutes=15),
        now_utc=now, osrm_drive_min=osrm8, haversine_km=hav,
    )
    # Tylko o3 w chain (pozostałe picked_up → skipped)
    expect("T9 chain_details len == 2 (o3 + proposal)",
           len(r.chain_details) == 2)

    # --- T10: GPS stale > 2 min ---
    print("\n=== T10 GPS age > 2 min → treated as no GPS ===")
    bag = [_Ord("o1", "assigned", SIOUX_PU, now + timedelta(minutes=5))]
    r = chain_eta.compute_chain_eta(
        courier_pos=POS_GPS, pos_source="gps", pos_age_min=5,  # stale
        bag_orders=bag,
        proposal_pickup_coords=MAMA_PU,
        proposal_scheduled_utc=now + timedelta(minutes=15),
        now_utc=now, osrm_drive_min=osrm8, haversine_km=hav,
    )
    expect("T10 starting_point != 'gps' (stale)",
           r.starting_point != "gps", f"got {r.starting_point}")

    # --- T11: OSRM timeout (returns None) → haversine × mult ---
    print("\n=== T11 OSRM timeout → haversine × 2.5 fallback ===")
    def osrm_none(a, b): return None
    hav11 = _mk_hav({(POS_GPS, MAMA_PU): 2.0})  # 2 km haversine
    r = chain_eta.compute_chain_eta(
        courier_pos=POS_GPS, pos_source="gps", pos_age_min=1,
        bag_orders=[],
        proposal_pickup_coords=MAMA_PU,
        proposal_scheduled_utc=now + timedelta(minutes=1),
        now_utc=now, osrm_drive_min=osrm_none, haversine_km=hav11,
    )
    # Fallback: 2 km × 2.5 = 5 min → max(now+5, now+1) = now+5
    expect("T11 fallback haversine used",
           r.effective_eta_utc == now + timedelta(minutes=5),
           f"got {r.effective_eta_utc}")
    expect("T11 warning fired", any("fallback haversine" in w or "OSRM" in w for w in r.warnings),
           f"warnings: {r.warnings}")

    # --- T12: proposal scheduled None → fallback now+30 ---
    print("\n=== T12 proposal scheduled=None → fallback ===")
    osrm12 = _mk_osrm({(POS_GPS, MAMA_PU): 5.0})
    r = chain_eta.compute_chain_eta(
        courier_pos=POS_GPS, pos_source="gps", pos_age_min=1,
        bag_orders=[],
        proposal_pickup_coords=MAMA_PU,
        proposal_scheduled_utc=None,
        now_utc=now, osrm_drive_min=osrm12, haversine_km=hav,
    )
    # Default 30 → max(now+5, now+30) = now+30
    expect("T12 fallback scheduled now+30",
           r.effective_eta_utc == now + timedelta(minutes=30))
    expect("T12 warning scheduled None",
           any("scheduled=None" in w for w in r.warnings))

    # --- T13: R-05 tier=gold multiplier 0.889 ---
    print("\n=== T13 R-05 gold multiplier 0.889 ===")
    osrm13 = _mk_osrm({(POS_GPS, MAMA_PU): 10.0})
    r = chain_eta.compute_chain_eta(
        courier_pos=POS_GPS, pos_source="gps", pos_age_min=1,
        bag_orders=[],
        proposal_pickup_coords=MAMA_PU,
        proposal_scheduled_utc=now,
        now_utc=now, osrm_drive_min=osrm13, haversine_km=hav,
        speed_multiplier=0.889,
    )
    # drive 10 × 0.889 = 8.89 min → effective = now+8.89
    actual = (r.effective_eta_utc - now).total_seconds() / 60.0
    expect("T13 drive × 0.889 (8.89 min)",
           abs(actual - 8.89) < 0.01, f"got {actual:.3f} min")

    # --- T14: chain_details integration (no R-06 impact — separate) ---
    print("\n=== T14 chain_details populated (format check) ===")
    bag = [_Ord("o1", "assigned", SIOUX_PU, now + timedelta(minutes=5))]
    r = chain_eta.compute_chain_eta(
        courier_pos=POS_GPS, pos_source="gps", pos_age_min=1,
        bag_orders=bag,
        proposal_pickup_coords=MAMA_PU,
        proposal_scheduled_utc=now + timedelta(minutes=10),
        now_utc=now, osrm_drive_min=osrm8, haversine_km=hav,
    )
    expect("T14 chain_details has 'order_id' key",
           all('order_id' in d for d in r.chain_details))
    expect("T14 chain_details has '__proposal__' last",
           r.chain_details[-1].get('order_id') == '__proposal__')
    expect("T14 chain_details has 'source' keys",
           all('source' in d for d in r.chain_details))

    # --- T15: delta_vs_naive_min computed ---
    print("\n=== T15 delta_vs_naive_min populated ===")
    expect("T15 delta_vs_naive_min is a number",
           isinstance(r.delta_vs_naive_min, (int, float)),
           f"got {r.delta_vs_naive_min!r}")

    print(f"\n=== summary: {results['pass']} pass, {results['fail']} fail ===")
    return 0 if results["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
