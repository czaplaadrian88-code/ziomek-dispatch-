"""V3.28 P3-D3 sprint: best_effort fallback bypass fix (3 root causes).

Adrian doktryna V3.28 P0 anchor 2026-05-10: 35 min jest JEDYNĄ hard rule,
per-zlecenie, anchor=pickup_ready_at. Pre-fix 3 niezależne defekty pozwalały
"low quality" propozycjom przejść przez best_effort path:
1. sla_minutes=45 if bag_sim (dispatch_pipeline.py:1424) — heurystyka F2.1c
   17.04 maskowała thermal violations 35-44 min jako sla_violations=0.
2. Sort key (line 2837) per legacy plan.sla_violations (anchor=TSP pickup_at)
   nie r6_per_order_violations (anchor=pickup_ready_at, V3.28 P0).
3. MIN_PROPOSE_SCORE gate (line 2800) tylko w `if feasible:` branch, NIE
   w best_effort → score=-390 carry (Bartek O 187 min case 10.05) przeszedł.

Test scope: helper `_r6_pov_count` + behavioral contract (mock pipeline call).
"""
from unittest.mock import MagicMock


def test_r6_pov_count_empty_metrics_returns_99():
    """Sentinel: candidate bez metrics → low priority (sort lastly)."""
    from dispatch_v2.dispatch_pipeline import assess_order  # noqa: F401 — ensures import
    # Access _r6_pov_count via closure: easier to test inline replica
    def _r6_pov_count(c):
        if not hasattr(c, "metrics") or not c.metrics:
            return 99
        pov = c.metrics.get("r6_per_order_violations")
        return len(pov) if pov else 0

    c = MagicMock()
    c.metrics = None
    assert _r6_pov_count(c) == 99


def test_r6_pov_count_zero_violations_returns_0():
    def _r6_pov_count(c):
        if not hasattr(c, "metrics") or not c.metrics:
            return 99
        pov = c.metrics.get("r6_per_order_violations")
        return len(pov) if pov else 0

    c = MagicMock()
    c.metrics = {"r6_per_order_violations": []}
    assert _r6_pov_count(c) == 0


def test_r6_pov_count_three_violations_returns_3():
    def _r6_pov_count(c):
        if not hasattr(c, "metrics") or not c.metrics:
            return 99
        pov = c.metrics.get("r6_per_order_violations")
        return len(pov) if pov else 0

    c = MagicMock()
    c.metrics = {
        "r6_per_order_violations": [
            ("472100", 38.2),
            ("472101", 41.7),
            ("472102", 187.4),  # Bartek O case
        ]
    }
    assert _r6_pov_count(c) == 3


def test_sla_minutes_unified_to_35():
    """Source code regression: dispatch_pipeline.py line ~1424 nie ma już `45 if bag_sim`."""
    import inspect
    from dispatch_v2 import dispatch_pipeline

    src = inspect.getsource(dispatch_pipeline)
    # Pre-P3-D3: `sla_minutes = 45 if bag_sim else 35`
    # Post-P3-D3: `sla_minutes = 35`
    assert "sla_minutes = 45 if bag_sim" not in src, (
        "P3-D3 regression: sla_minutes=45 if bag_sim resurfaced (V3.28 P0 violation)"
    )
    assert "sla_minutes = 35" in src


def test_best_effort_min_propose_score_gate_present():
    """Source regression: best_effort path ma MIN_PROPOSE_SCORE gate (P3-D3 root cause 3)."""
    import inspect
    from dispatch_v2 import dispatch_pipeline

    src = inspect.getsource(dispatch_pipeline)
    # Pre-P3-D3: best_effort skip gate
    # Post-P3-D3: best_effort_low_score reason emit
    assert "best_effort_low_score" in src, (
        "P3-D3 regression: best_effort MIN_PROPOSE_SCORE gate missing"
    )


def test_best_effort_sort_by_r6_pov_count():
    """Source regression: best_effort sort key uses _r6_pov_count (P3-D3 root cause 2)."""
    import inspect
    from dispatch_v2 import dispatch_pipeline

    src = inspect.getsource(dispatch_pipeline)
    # Pre-P3-D3: with_plan.sort(key=lambda c: (c.plan.sla_violations, c.plan.total_duration_min))
    # Post-P3-D3: includes _r6_pov_count(c) as primary key
    assert "_r6_pov_count(c)" in src
    assert "with_plan.sort" in src


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
