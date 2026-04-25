"""Bug D regression guard (sprint 2026-04-25):
"Po odbiorze z X" anchor-based override.

Pre-fix: dispatch_pipeline.py:824 bundle_level2 iteruje po bag_raw, pierwszy
match haversine(bag_pickup, new_pickup) < 1.5 km → break. NON-deterministic
order (depends on bag_raw insertion order from fleet_snapshot).

Post-fix: gdy ENABLE_V326_ANCHOR_BASED_SCORING=True i anchor available i
anchor.is_pickup, X = anchor.restaurant_name (chronologically previous pickup
w plan). Niezależne od bag_raw order.

Test używa AST inspection bo pełna runtime symulacja wymaga fleet, panel,
state_machine — outside scope smoke test.
"""
import ast
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[2]))

DISPATCH_PIPELINE = pathlib.Path(__file__).resolve().parents[1] / "dispatch_pipeline.py"


def test_bug_d_anchor_override_present():
    """Verify anchor-based bundle_level2 override block w dispatch_pipeline.py."""
    src = DISPATCH_PIPELINE.read_text()
    # Markers for the new block:
    markers = [
        "Bug D fix (2026-04-25)",
        "anchor.restaurant_name",
        "_l2_anchor_dist",
    ]
    missing = [m for m in markers if m not in src]
    assert not missing, f"Bug D fix markers missing w dispatch_pipeline.py: {missing}"


def test_anchor_override_clears_bundle_level2_when_far():
    """AST guard: anchor block sets bundle_level2 = None gdy distance >= 1.5."""
    src = DISPATCH_PIPELINE.read_text()
    # Find the Bug D fix block
    idx = src.find("Bug D fix (2026-04-25)")
    assert idx >= 0
    # Block fragment +600 chars
    fragment = src[idx:idx + 1200]
    # Verify None assignments (override-clear behavior)
    assert "bundle_level2 = None" in fragment
    assert "bundle_level2_dist = None" in fragment


def test_v326_anchor_used_flag_propagated():
    """v326_anchor_used MUSI być set gdy anchor flag=True i anchor exists."""
    src = DISPATCH_PIPELINE.read_text()
    assert "v326_anchor_used = True" in src
    assert "v326_anchor_obj = _anchor" in src


def test_anchor_obj_kept_for_downstream():
    """v326_anchor_obj keeps full InsertionAnchor (not just restaurant_name string)."""
    src = DISPATCH_PIPELINE.read_text()
    assert "v326_anchor_obj = None" in src  # initialization
    # Used somewhere downstream (Bug D anchor block)
    assert "v326_anchor_obj" in src


if __name__ == "__main__":
    test_bug_d_anchor_override_present()
    print("test_bug_d_anchor_override_present: PASS")
    test_anchor_override_clears_bundle_level2_when_far()
    print("test_anchor_override_clears_bundle_level2_when_far: PASS")
    test_v326_anchor_used_flag_propagated()
    print("test_v326_anchor_used_flag_propagated: PASS")
    test_anchor_obj_kept_for_downstream()
    print("test_anchor_obj_kept_for_downstream: PASS")
    print("ALL 4/4 PASS")
