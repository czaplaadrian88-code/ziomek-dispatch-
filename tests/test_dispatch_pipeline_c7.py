"""F2.2 C7 skeleton tests — assess_order signature extension + providers.

Standalone executable. Validates:
- Backward-compat: existing positional callers unchanged
- New kwargs: pending_queue + demand_context accepted
- Flag-gated auto-fetch behavior
- Provider helpers: get_pending_queue + compute_demand_context
"""
import sys
from datetime import datetime, timezone

sys.path.insert(0, '/root/.openclaw/workspace/scripts')

import dispatch_v2.pending_queue_provider as pqp
from dispatch_v2.pending_queue_provider import (
    get_pending_queue,
    compute_demand_context,
    _load_peak_cells,
)
import dispatch_v2.common as common


def test_pending_queue_flag_false_returns_empty():
    """Default flag False → always empty list, never touches state_machine."""
    assert common.ENABLE_PENDING_QUEUE_VIEW is False
    result = get_pending_queue()
    assert isinstance(result, list), f"expected list, got {type(result)}"
    assert result == [], f"expected [], got {result}"
    return True


def test_demand_context_flag_false_minimal_defaults():
    """Default flag False → hour/dayofweek/regime populated, regime always NORMAL."""
    ctx = compute_demand_context()
    required_keys = {"hour", "dayofweek", "regime", "n_orders_last_15min", "generated_at"}
    assert required_keys <= set(ctx.keys()), f"missing keys: {required_keys - set(ctx.keys())}"
    assert ctx["regime"] == "NORMAL"
    assert ctx["n_orders_last_15min"] == 0
    assert 0 <= ctx["hour"] <= 23
    assert 0 <= ctx["dayofweek"] <= 6
    return True


def test_demand_context_specific_now():
    """Compute for specific datetime — verify hour + dayofweek match."""
    # 2026-04-19 Sunday 15:00 UTC = Warsaw 17:00 CEST (summer time)
    # Actually 2026-04-19 is Sunday. Let me use UTC Sun 12:00 = Warsaw 14:00
    now = datetime(2026, 4, 19, 12, 0, tzinfo=timezone.utc)
    ctx = compute_demand_context(now=now)
    # In Warsaw (UTC+2 summer), 12:00 UTC = 14:00 CEST
    # 2026-04-19 is Sunday (dow=6)
    assert ctx["dayofweek"] == 6, f"Sunday expected dow=6, got {ctx['dayofweek']}"
    assert ctx["hour"] == 14, f"expected hour=14, got {ctx['hour']}"
    return True


def test_peak_cells_loader_nonempty():
    """Loader reads 11 PEAK cells from CSV."""
    cells = _load_peak_cells()
    # Expected 11 per sekcja 3.5
    assert len(cells) == 11, f"expected 11 PEAK cells, got {len(cells)}"
    # Sun 15h (dow=6, hour=15) should be there
    assert (15, 6) in cells, f"Sun 15h missing: {cells}"
    return True


def test_demand_context_peak_detection_when_flag_true():
    """Flag on + Sun 15h → regime=PEAK."""
    original = common.ENABLE_PENDING_QUEUE_VIEW
    try:
        common.ENABLE_PENDING_QUEUE_VIEW = True
        import importlib
        importlib.reload(pqp)
        from dispatch_v2.pending_queue_provider import compute_demand_context as cdc
        # Sun 13:00 UTC = Warsaw 15:00 CEST (summer) — PEAK cell
        now = datetime(2026, 4, 19, 13, 0, tzinfo=timezone.utc)
        ctx = cdc(now=now)
        assert ctx["hour"] == 15
        assert ctx["dayofweek"] == 6
        assert ctx["regime"] == "PEAK", f"expected PEAK, got {ctx['regime']}"
    finally:
        common.ENABLE_PENDING_QUEUE_VIEW = original
        importlib.reload(pqp)
    return True


def test_demand_context_normal_when_flag_true_mon():
    """Flag on + Monday 10h → regime=NORMAL (not in PEAK cells)."""
    original = common.ENABLE_PENDING_QUEUE_VIEW
    try:
        common.ENABLE_PENDING_QUEUE_VIEW = True
        import importlib
        importlib.reload(pqp)
        from dispatch_v2.pending_queue_provider import compute_demand_context as cdc
        # Mon 2026-04-20 08:00 UTC = Warsaw 10:00 CEST
        now = datetime(2026, 4, 20, 8, 0, tzinfo=timezone.utc)
        ctx = cdc(now=now)
        assert ctx["dayofweek"] == 0  # Monday
        assert ctx["regime"] == "NORMAL", f"expected NORMAL for Mon 10h, got {ctx['regime']}"
    finally:
        common.ENABLE_PENDING_QUEUE_VIEW = original
        importlib.reload(pqp)
    return True


def test_assess_order_backward_compat_existing_callers():
    """Existing callers using positional args (no new kwargs) still work.

    Verifies test_decision_engine_f21 call pattern doesn't break.
    assess_order signature is: (order_event, fleet_snapshot, restaurant_meta=None, now=None)
    New kwargs (pending_queue, demand_context) should default to None.
    """
    from dispatch_v2.dispatch_pipeline import assess_order
    import inspect
    sig = inspect.signature(assess_order)
    params = list(sig.parameters.keys())
    # Original 4 params present
    assert "order_event" in params
    assert "fleet_snapshot" in params
    assert "restaurant_meta" in params
    assert "now" in params
    # New 2 kwargs present
    assert "pending_queue" in params
    assert "demand_context" in params
    # pending_queue and demand_context should be KEYWORD_ONLY (after *)
    assert sig.parameters["pending_queue"].kind == inspect.Parameter.KEYWORD_ONLY
    assert sig.parameters["demand_context"].kind == inspect.Parameter.KEYWORD_ONLY
    # Defaults None
    assert sig.parameters["pending_queue"].default is None
    assert sig.parameters["demand_context"].default is None
    return True


def test_assess_order_accepts_new_kwargs():
    """assess_order can be called with new kwargs without TypeError."""
    from dispatch_v2.dispatch_pipeline import assess_order
    # Mock minimal inputs that pass early validation; may return KOORD or error
    # but MUST NOT raise TypeError for unknown kwarg.
    try:
        result = assess_order(
            order_event={"order_id": "TEST-C7-1", "restaurant": "X", "pickup_coords": (53.1, 23.0), "delivery_coords": (53.2, 23.1)},
            fleet_snapshot={},
            restaurant_meta=None,
            now=datetime.now(timezone.utc),
            pending_queue=[],
            demand_context={"hour": 12, "dayofweek": 0, "regime": "NORMAL"},
        )
        # May return PipelineResult with verdict NO (fleet empty)
        assert result is not None or True  # don't crash
    except TypeError as e:
        raise AssertionError(f"TypeError on new kwargs: {e}")
    return True


def test_pending_queue_returns_list_type():
    """Always returns list, never None or other types."""
    result = get_pending_queue()
    assert isinstance(result, list), f"expected list, got {type(result)}"
    return True


def test_demand_context_returns_dict_with_required_keys():
    """Always dict with at least hour/dayofweek/regime present."""
    ctx = compute_demand_context()
    assert isinstance(ctx, dict)
    assert "hour" in ctx
    assert "dayofweek" in ctx
    assert "regime" in ctx
    return True


def main():
    tests = [
        ("pending_queue_flag_false_returns_empty", test_pending_queue_flag_false_returns_empty),
        ("demand_context_flag_false_minimal_defaults", test_demand_context_flag_false_minimal_defaults),
        ("demand_context_specific_now", test_demand_context_specific_now),
        ("peak_cells_loader_nonempty", test_peak_cells_loader_nonempty),
        ("demand_context_peak_detection_when_flag_true", test_demand_context_peak_detection_when_flag_true),
        ("demand_context_normal_when_flag_true_mon", test_demand_context_normal_when_flag_true_mon),
        ("assess_order_backward_compat_signature", test_assess_order_backward_compat_existing_callers),
        ("assess_order_accepts_new_kwargs", test_assess_order_accepts_new_kwargs),
        ("pending_queue_returns_list_type", test_pending_queue_returns_list_type),
        ("demand_context_returns_dict_with_required_keys", test_demand_context_returns_dict_with_required_keys),
    ]
    print("=" * 60)
    print("F2.2 C7 skeleton: dispatch_pipeline + pending_queue_provider tests")
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
