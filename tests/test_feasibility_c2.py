"""F2.2 C2 tests — check_per_order_35min_rule helper.

Standalone executable. Zero kontaktu z real simulate_bag_route_v2 — constructs
RoutePlanV2 instances directly.
"""
import sys
from datetime import datetime, timezone

sys.path.insert(0, '/root/.openclaw/workspace/scripts')

from dispatch_v2.feasibility_v2 import (
    check_per_order_35min_rule,
    C2_PER_ORDER_THRESHOLD_MIN,
)
from dispatch_v2.route_simulator_v2 import RoutePlanV2


def _make_plan(per_order_times):
    """Minimal RoutePlanV2 with populated per_order_delivery_times."""
    now = datetime.now(timezone.utc)
    return RoutePlanV2(
        sequence=list(per_order_times.keys()) if per_order_times else [],
        predicted_delivered_at={},
        pickup_at={},
        total_duration_min=0.0,
        strategy="test",
        sla_violations=0,
        osrm_fallback_used=False,
        per_order_delivery_times=per_order_times,
    )


def test_c2_gate_accepts_all_under_35():
    plan = _make_plan({"o1": 20.0, "o2": 28.0, "o3": 32.0})
    passes, details = check_per_order_35min_rule(plan)
    assert passes is True, f"expected True, got {passes}"
    assert details["total_orders"] == 3
    assert details["max_elapsed"] == 32.0
    assert details["violations"] == []
    assert details["per_order_data_available"] is True
    return True


def test_c2_gate_rejects_when_any_over_35():
    plan = _make_plan({"o1": 25.0, "o2": 40.0})
    passes, details = check_per_order_35min_rule(plan)
    assert passes is False, f"expected False, got {passes}"
    assert details["max_elapsed"] == 40.0
    assert len(details["violations"]) == 1
    assert details["violations"][0] == ("o2", 40.0)
    return True


def test_c2_gate_fail_closed_on_none():
    """Fail-closed semantic: per_order_delivery_times=None → reject."""
    plan = _make_plan(None)
    passes, details = check_per_order_35min_rule(plan)
    assert passes is False, f"expected False (fail-closed), got {passes}"
    assert details["per_order_data_available"] is False
    assert details["total_orders"] == 0
    assert details["violations"] == []
    return True


def test_c2_gate_empty_dict_is_vacuously_true():
    """Empty dict = no orders = vacuously truth (no violations possible)."""
    plan = _make_plan({})
    passes, details = check_per_order_35min_rule(plan)
    assert passes is True, f"expected True (no orders = no violations), got {passes}"
    assert details["per_order_data_available"] is True  # field was computed, just empty
    assert details["total_orders"] == 0
    assert details["violations"] == []
    return True


def test_c2_threshold_boundary_exact_35():
    """Exactly 35.0 min → PASSES (strict > check, not >=)."""
    plan = _make_plan({"o1": 35.0})
    passes, details = check_per_order_35min_rule(plan)
    assert passes is True, f"35.0 is threshold boundary, must PASS; got {passes}"
    assert details["violations"] == []
    return True


def test_c2_threshold_boundary_over_35():
    """35.01 → FAILS."""
    plan = _make_plan({"o1": 35.01})
    passes, details = check_per_order_35min_rule(plan)
    assert passes is False, f"35.01 > 35.0 must FAIL; got {passes}"
    assert details["violations"] == [("o1", 35.01)]
    return True


def test_c2_multiple_violations_sorted_output():
    """Multiple violations tracked properly."""
    plan = _make_plan({"o1": 10.0, "o2": 36.0, "o3": 42.5, "o4": 25.0})
    passes, details = check_per_order_35min_rule(plan)
    assert passes is False
    assert details["max_elapsed"] == 42.5
    # Violations include o2 and o3 but not o1 or o4
    violation_oids = {v[0] for v in details["violations"]}
    assert violation_oids == {"o2", "o3"}, f"got {violation_oids}"
    return True


def test_c2_custom_threshold_40():
    """Threshold override to 40 — different violations."""
    plan = _make_plan({"o1": 36.0, "o2": 42.0})
    passes, details = check_per_order_35min_rule(plan, threshold_min=40.0)
    assert passes is False  # o2 still over 40
    assert details["violations"] == [("o2", 42.0)]  # o1 ok at 40-threshold
    # Reset threshold to 35 — now both violate
    passes35, details35 = check_per_order_35min_rule(plan)
    assert passes35 is False
    assert len(details35["violations"]) == 2
    return True


def main():
    tests = [
        ("c2_gate_accepts_all_under_35", test_c2_gate_accepts_all_under_35),
        ("c2_gate_rejects_when_any_over_35", test_c2_gate_rejects_when_any_over_35),
        ("c2_gate_fail_closed_on_none", test_c2_gate_fail_closed_on_none),
        ("c2_gate_empty_dict_is_vacuously_true", test_c2_gate_empty_dict_is_vacuously_true),
        ("c2_threshold_boundary_exact_35", test_c2_threshold_boundary_exact_35),
        ("c2_threshold_boundary_over_35", test_c2_threshold_boundary_over_35),
        ("c2_multiple_violations", test_c2_multiple_violations_sorted_output),
        ("c2_custom_threshold_40", test_c2_custom_threshold_40),
    ]
    print("=" * 60)
    print("F2.2 C2: check_per_order_35min_rule tests")
    print("=" * 60)
    passed = 0
    failed = []
    for name, fn in tests:
        try:
            fn()
            print(f"  ✅ {name}")
            passed += 1
        except AssertionError as e:
            print(f"  ❌ {name}: {e}")
            failed.append(name)
        except Exception as e:
            print(f"  ❌ {name}: UNEXPECTED {type(e).__name__}: {e}")
            failed.append(name)
    print("=" * 60)
    print(f"{passed}/{len(tests)} PASS")
    if failed:
        print(f"FAILED: {failed}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
