"""V3.27.5 Path A — _bag_dict_to_ordersim defense-in-depth.

Naprawia chain bug TASK H (#469099 root cause level 2): _bag_dict_to_ordersim
używał tylko field 'status' do detection picked_up, ignorując picked_up_at.
Przy state inconsistency (status=assigned + picked_up_at SET, post-Path B
state_machine bug downstream lub future bug), OrderSim.status="assigned" →
pickup-node added do TSP graph dla picked-up orderów.

Path A treats picked_up_at != None as canonical signal regardless of status field.
"""
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))


from dispatch_v2.dispatch_pipeline import _bag_dict_to_ordersim  # noqa: E402


def test_path_a_status_assigned_no_picked_up_at():
    """Standard new order: status=assigned + picked_up_at=None → assigned."""
    d = {
        "order_id": "test1",
        "status": "assigned",
        "picked_up_at": None,
        "pickup_coords": (53.13, 23.16),
        "delivery_coords": (53.14, 23.17),
    }
    sim = _bag_dict_to_ordersim(d)
    assert sim.status == "assigned"
    assert sim.picked_up_at is None


def test_path_a_status_picked_up_with_picked_up_at():
    """Standard picked_up flow: status=picked_up + picked_up_at SET → picked_up."""
    d = {
        "order_id": "test2",
        "status": "picked_up",
        "picked_up_at": "2026-04-27 20:48:43",
        "pickup_coords": (53.13, 23.16),
        "delivery_coords": (53.14, 23.17),
    }
    sim = _bag_dict_to_ordersim(d)
    assert sim.status == "picked_up"
    assert sim.picked_up_at is not None


def test_path_a_FIX_status_assigned_picked_up_at_SET():
    """V3.27.5 Path A FIX: state inconsistency case (TASK H scenario).

    status=assigned + picked_up_at SET (post-COURIER_ASSIGNED revert pre Path B,
    or future state_machine bug) → Path A treats jako picked_up via picked_up_at."""
    d = {
        "order_id": "test3",
        "status": "assigned",  # BUG state (pre-Path B)
        "picked_up_at": "2026-04-27 20:44:10",  # picked_up canonical signal
        "pickup_coords": (53.13, 23.16),
        "delivery_coords": (53.14, 23.17),
    }
    sim = _bag_dict_to_ordersim(d)
    assert sim.status == "picked_up", \
        f"Path A FIX FAIL: picked_up_at SET → simulator should treat as picked_up, got status={sim.status}"
    assert sim.picked_up_at is not None


def test_path_a_status_picked_up_no_picked_up_at():
    """Edge: status=picked_up but picked_up_at=None (rare data corruption?).
    Should still be treated as picked_up (status field signal preserved)."""
    d = {
        "order_id": "test4",
        "status": "picked_up",
        "picked_up_at": None,
        "pickup_coords": (53.13, 23.16),
        "delivery_coords": (53.14, 23.17),
    }
    sim = _bag_dict_to_ordersim(d)
    assert sim.status == "picked_up"


def test_path_a_integration_469087_replay():
    """#469087 replay z TASK H: state ma status='assigned' + picked_up_at SET
    (post-revert bug). Path A correctly classifies jako picked_up."""
    d = {
        "order_id": "469087",
        "status": "assigned",  # BUG state (revert by COURIER_ASSIGNED panel_diff)
        "picked_up_at": "2026-04-27 20:44:10",  # canonical signal
        "courier_id": "515",
        "restaurant": "Miejska Miska",
        "delivery_address": "aleja JP II 61C/85",
        "pickup_coords": (53.130, 23.165),
        "delivery_coords": (53.135, 23.170),
        "czas_kuriera_warsaw": "2026-04-27T20:43:00+02:00",
        "pickup_at_warsaw": "2026-04-27T20:31:36+02:00",
    }
    sim = _bag_dict_to_ordersim(d)
    # KEY ASSERTION: pickup-node will NIE be added do TSP bo status=picked_up
    assert sim.status == "picked_up", \
        f"V3.27.5 Path A: bag with picked_up_at SET → status=picked_up regardless of bug field"


if __name__ == "__main__":
    import traceback
    tests = [v for k, v in globals().items() if k.startswith("test_") and callable(v)]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"PASS {t.__name__}")
            passed += 1
        except Exception as e:
            print(f"FAIL {t.__name__}: {e}")
            traceback.print_exc()
            failed += 1
    print(f"\n{passed}/{passed+failed} passed")
    sys.exit(0 if failed == 0 else 1)
