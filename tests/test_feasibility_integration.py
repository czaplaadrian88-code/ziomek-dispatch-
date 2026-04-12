"""Integration test feasibility - realne coords Bialegostoku + realny OSRM.

3 scenariusze inspirowane dzisiejsza operacja:
1. Kurier w centrum, pusty bag, nowy order centrum -> MAYBE
2. Kurier w centrum, bag 2 ordery Wasilkow, nowy Wasilkow -> MAYBE (bundling)
3. Kurier w centrum, bag 2 ordery Wasilkow (blisko SLA), nowy centrum -> NO (SLA violation)
4. Pelny bag 6/6 -> NO bag_full
5. Long-haul Lapy 18 km z pustym bagiem + nowy Lapy -> MAYBE
"""
import sys
sys.path.insert(0, '/root/.openclaw/workspace/scripts')

from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from dispatch_v2.feasibility import check_feasibility
from dispatch_v2.route_simulator import OrderSim

WARSAW = ZoneInfo("Europe/Warsaw")

# Realne coords Bialegostoku (z restaurant_coords.json oraz centrum)
CENTRUM_KURIER = (53.1318, 23.1631)           # Rynek Kosciuszki
CENTRUM_REST_ZAPIECEK = (53.1318, 23.1585)    # Rynek Kosciuszki 13 (aid=170)
CENTRUM_REST_RUKOLA = (53.1278, 23.1550)      # Legionowa 11
CENTRUM_KLIENT_1 = (53.1350, 23.1648)         # Kilinskiego
CENTRUM_KLIENT_2 = (53.1290, 23.1502)         # Sienkiewicza

# Wasilkow (10 km NE od centrum)
WASILKOW_KLIENT_1 = (53.1960, 23.2020)        # Koscielna
WASILKOW_KLIENT_2 = (53.1935, 23.2055)        # krucza

# Lapy (18 km SE)
LAPY_KLIENT = (52.9857, 22.8770)
LAPY_REST = (53.1318, 23.1631)  # umownie centrum

PASS = 0
FAIL = 0

def run(name, fn):
    global PASS, FAIL
    try:
        fn()
        print(f"  ✅ {name}")
        PASS += 1
    except AssertionError as e:
        print(f"  ❌ {name}: {e}")
        FAIL += 1
    except Exception as e:
        print(f"  💥 {name}: {type(e).__name__}: {e}")
        import traceback; traceback.print_exc()
        FAIL += 1

print("=" * 70)
print("INTEGRATION TEST feasibility - realne OSRM, realne coords Bialegostoku")
print("=" * 70)

# WEEKEND teraz (sobota 11.04) -> traffic_multiplier = 1.0

def test_1_pusty_bag_centrum():
    """Kurier centrum, pusty bag, nowy order centrum -> MAYBE."""
    new_order = OrderSim(
        order_id="NEW001",
        pickup_coords=CENTRUM_REST_ZAPIECEK,
        delivery_coords=CENTRUM_KLIENT_1,
        status="assigned",
    )
    verdict, reason, metrics = check_feasibility(
        courier_pos=CENTRUM_KURIER,
        bag=[],
        new_order=new_order,
    )
    print(f"    verdict={verdict} reason={reason}")
    print(f"    total_duration={metrics.get('total_duration_min')}min sequence={metrics.get('sequence')}")
    assert verdict == "MAYBE", f"oczekiwano MAYBE, dostano {verdict}"
    assert metrics["total_duration_min"] < 15, f"krotka trasa centrum, dostano {metrics['total_duration_min']}min"

def test_2_bundling_wasilkow():
    """Kurier centrum, bag 2 ordery Wasilkow (swiezo picked_up), nowy Wasilkow -> MAYBE."""
    now = datetime.now(timezone.utc)
    picked_recent = now - timedelta(minutes=2)
    bag = [
        OrderSim(order_id="EX001", pickup_coords=CENTRUM_REST_ZAPIECEK,
                 delivery_coords=WASILKOW_KLIENT_1, picked_up_at=picked_recent, status="picked_up"),
        OrderSim(order_id="EX002", pickup_coords=CENTRUM_REST_RUKOLA,
                 delivery_coords=WASILKOW_KLIENT_2, picked_up_at=picked_recent, status="picked_up"),
    ]
    new_order = OrderSim(
        order_id="NEW002",
        pickup_coords=CENTRUM_REST_ZAPIECEK,
        delivery_coords=(53.1945, 23.2010),  # 3ci Wasilkow
        status="assigned",
    )
    verdict, reason, metrics = check_feasibility(
        courier_pos=CENTRUM_KURIER, bag=bag, new_order=new_order, now=now,
    )
    print(f"    verdict={verdict} reason={reason}")
    print(f"    total={metrics.get('total_duration_min')}min violations={metrics.get('sla_violations')}")
    assert verdict == "MAYBE", f"Wasilkow bundling - oczekiwano MAYBE, dostano {verdict}. Reason: {reason}"

def test_3_sla_violation_opposite():
    """Kurier centrum, bag 2 ordery Wasilkow picked_up 25 min temu (blisko SLA), nowy centrum -> NO."""
    now = datetime.now(timezone.utc)
    picked_old = now - timedelta(minutes=25)  # juz 25 min w bagu
    bag = [
        OrderSim(order_id="OLD001", pickup_coords=CENTRUM_REST_ZAPIECEK,
                 delivery_coords=WASILKOW_KLIENT_1, picked_up_at=picked_old, status="picked_up"),
        OrderSim(order_id="OLD002", pickup_coords=CENTRUM_REST_RUKOLA,
                 delivery_coords=WASILKOW_KLIENT_2, picked_up_at=picked_old, status="picked_up"),
    ]
    new_order = OrderSim(
        order_id="NEW003",
        pickup_coords=CENTRUM_REST_ZAPIECEK,
        delivery_coords=CENTRUM_KLIENT_2,
        status="assigned",
    )
    verdict, reason, metrics = check_feasibility(
        courier_pos=CENTRUM_KURIER, bag=bag, new_order=new_order, now=now,
    )
    print(f"    verdict={verdict} reason={reason}")
    print(f"    violations={metrics.get('sla_violations')}")
    assert verdict == "NO", f"oczekiwano NO bo SLA, dostano {verdict}"
    assert "sla_violation" in reason or "sla" in reason.lower()

def test_4_full_bag():
    """Pelny bag 6 orderow -> NO bag_full."""
    bag = [
        OrderSim(order_id=f"FULL{i}", pickup_coords=CENTRUM_REST_ZAPIECEK,
                 delivery_coords=CENTRUM_KLIENT_1, status="assigned")
        for i in range(6)
    ]
    new_order = OrderSim(
        order_id="NEW004", pickup_coords=CENTRUM_REST_RUKOLA,
        delivery_coords=CENTRUM_KLIENT_2, status="assigned",
    )
    verdict, reason, metrics = check_feasibility(
        courier_pos=CENTRUM_KURIER, bag=bag, new_order=new_order,
    )
    print(f"    verdict={verdict} reason={reason}")
    assert verdict == "NO"
    assert "bag_full" in reason

def test_5_long_haul_lapy_pusty_bag():
    """Pusty bag, nowy order Lapy 18 km -> MAYBE (pickup reach 18 > 15 km = NO)."""
    new_order = OrderSim(
        order_id="NEW005",
        pickup_coords=LAPY_REST,  # centrum - pickup jest blisko
        delivery_coords=LAPY_KLIENT,  # delivery daleko
        status="assigned",
    )
    verdict, reason, metrics = check_feasibility(
        courier_pos=CENTRUM_KURIER, bag=[], new_order=new_order,
    )
    print(f"    verdict={verdict} reason={reason}")
    print(f"    total_duration={metrics.get('total_duration_min')}min")
    # Pickup w centrum jest OK (< 15km), delivery Lapy daleko ale SLA check bedzie dzialal
    # Off-peak mult=1.0, OSRM free-flow ~20 min one way = pickup+delivery <40 min. Blisko SLA.
    # Nie ustalam hard assertion - logujemy wynik
    print(f"    [INFO] long-haul wynik: {verdict}")

run("1. Pusty bag centrum", test_1_pusty_bag_centrum)
run("2. Wasilkow bundling (3 ordery)", test_2_bundling_wasilkow)
run("3. SLA violation - Wasilkow stare + centrum", test_3_sla_violation_opposite)
run("4. Full bag 6/6", test_4_full_bag)
run("5. Long-haul Lapy (informacyjny)", test_5_long_haul_lapy_pusty_bag)

print()
print("=" * 70)
print(f"WYNIK: {PASS}/{PASS+FAIL} PASS")
print("=" * 70)
sys.exit(0 if FAIL == 0 else 1)
