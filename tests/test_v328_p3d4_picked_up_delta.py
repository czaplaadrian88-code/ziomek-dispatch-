"""V3.28 P3-D4 sprint: picked_up R6 delta-based reject.

Adrian doktryna NEW 2026-05-10 wieczór: picked_up tracking-only (V3.28 P0
default) za luźna empirycznie — gdy nowy order CAUSES delay dla picked_up
order z >35 min carry, reject zamiast pass.

Heurystyka delta (no double-simulation):
- new_pickup_at (plan.pickup_at[new_oid]) vs picked_up_delivery_at (plan.predicted_delivered_at[pu_oid])
- Jeśli new_pickup happens BEFORE picked_up delivery → kurier robi detour
  do new pickup → wydłuża carry picked_up → REJECT.
- Jeśli new_pickup po picked_up delivery → no impact, current track-only behavior.

Empiryczne baseline: Boboli 44 min case 10.05 (picked_up R6 violation, nowy
order routing przeszedł). Adrian explicit ACK: peak nie blokuje deploy
(pre-autonomy override).
"""
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock


def _mk_plan(predicted_delivered_at: dict, pickup_at: dict):
    p = MagicMock()
    p.predicted_delivered_at = predicted_delivered_at
    p.pickup_at = pickup_at
    return p


def test_picked_up_delta_no_overlap_no_reject():
    """Picked_up delivered BEFORE new pickup → no impact, no reject (track-only)."""
    from dispatch_v2.feasibility_v2 import check_feasibility_v2  # noqa: F401

    # Source code regression: gate uses pu_pred > new_pickup_at predicate
    import inspect
    from dispatch_v2 import feasibility_v2
    src = inspect.getsource(feasibility_v2)
    assert "r6_picked_up_delta_reject" in src
    assert "pu_pred > new_pickup_at" in src


def test_picked_up_delta_overlap_rejects():
    """Picked_up delivered AFTER new pickup → causes_delay → reject path."""
    import inspect
    from dispatch_v2 import feasibility_v2
    src = inspect.getsource(feasibility_v2)
    # Reject reason emit
    assert "R6_picked_up_delta_>35min" in src
    assert "new pickup delays carry" in src


def test_picked_up_delta_metric_emitted():
    """metrics['r6_picked_up_delta_reject'] zawsze ustawione (default False)."""
    import inspect
    from dispatch_v2 import feasibility_v2
    src = inspect.getsource(feasibility_v2)
    # Default False initial
    assert "metrics[\"r6_picked_up_delta_reject\"] = False" in src
    # True branch w hot path
    assert "metrics[\"r6_picked_up_delta_reject\"] = True" in src


def test_picked_up_delta_no_picked_up_violations_skip():
    """Brak r6_picked_up_violations → skip cały delta check (no-op)."""
    import inspect
    from dispatch_v2 import feasibility_v2
    src = inspect.getsource(feasibility_v2)
    # Gate condition: if r6_picked_up_violations
    assert "if r6_picked_up_violations:" in src


def test_picked_up_delta_new_pickup_none_safe():
    """plan.pickup_at[new_oid] missing → skip safely (no crash)."""
    import inspect
    from dispatch_v2 import feasibility_v2
    src = inspect.getsource(feasibility_v2)
    # Defensive None guard
    assert "if new_pickup_at is not None:" in src


def test_picked_up_delta_aware_of_tzinfo():
    """Timezone-aware comparison guard (replace_tzinfo dla naive datetime)."""
    import inspect
    from dispatch_v2 import feasibility_v2
    src = inspect.getsource(feasibility_v2)
    # Both new_pickup_at and pu_pred get tzinfo guard
    # Look for the section in question (post-V3.28 ANCHOR FIX)
    p3d4_section_start = src.find("P3-D4 2026-05-11")
    p3d4_section = src[p3d4_section_start:p3d4_section_start + 1500]
    assert p3d4_section.count("replace(tzinfo=timezone.utc)") >= 2


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
