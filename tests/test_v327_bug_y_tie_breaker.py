"""V3.27 Bug Y tie-breaker tests.

Adrian's decision Q8 Opcja 3: shortest first drop wins gdy 2+ permutacje
mają |total_duration_diff| < 2 min od leader.

Reasoning: "lepiej żeby jedno zamówienie jechało 3min, drugie 15min, niż
jedno 13min, drugie 20min" — user satisfaction.

Reproduction case #468508 mental simulation: pre-X tied permutacje (49.3=49.3),
post-X też tied (59.2=59.2 z global mult). Tie-breaker rozdziela arbitrary tie.

Run: python3 tests/test_v327_bug_y_tie_breaker.py
"""
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import route_simulator_v2 as RS  # noqa: E402
from dispatch_v2.route_simulator_v2 import RoutePlanV2  # noqa: E402

UTC = timezone.utc
NOW = datetime(2026, 4, 25, 14, 0, tzinfo=UTC)


def _mk_plan(sequence, total_min, sla_violations=0, deliveries_offset_min=None):
    """Build mock RoutePlanV2 z explicit timestamps dla pierwszego drop."""
    delivered = {}
    if deliveries_offset_min:
        for oid, off in deliveries_offset_min.items():
            delivered[oid] = NOW + timedelta(minutes=off)
    else:
        for i, oid in enumerate(sequence):
            delivered[oid] = NOW + timedelta(minutes=(i + 1) * 5)
    return RoutePlanV2(
        sequence=sequence,
        predicted_delivered_at=delivered,
        pickup_at={},
        total_duration_min=total_min,
        strategy="bruteforce",
        sla_violations=sla_violations,
        osrm_fallback_used=False,
    )


# ─────────────────────────────────────────────────────────
# Helper _first_drop_arrival_min
# ─────────────────────────────────────────────────────────

def test_first_drop_arrival_basic():
    """Pierwszy drop arrival od now."""
    plan = _mk_plan(["o1", "o2"], 30.0,
                    deliveries_offset_min={"o1": 5.0, "o2": 25.0})
    assert RS._first_drop_arrival_min(plan, NOW) == 5.0


def test_first_drop_arrival_empty_sequence():
    """Plan bez sequence → inf (defensive)."""
    plan = RoutePlanV2(
        sequence=[],
        predicted_delivered_at={},
        pickup_at={},
        total_duration_min=0.0,
        strategy="empty",
        sla_violations=0,
        osrm_fallback_used=False,
    )
    assert RS._first_drop_arrival_min(plan, NOW) == float("inf")


def test_first_drop_arrival_missing_delivered_at():
    """Plan z sequence ale brak predicted_delivered_at[first] → inf."""
    plan = RoutePlanV2(
        sequence=["o1"],
        predicted_delivered_at={},  # missing
        pickup_at={},
        total_duration_min=10.0,
        strategy="x",
        sla_violations=0,
        osrm_fallback_used=False,
    )
    assert RS._first_drop_arrival_min(plan, NOW) == float("inf")


# ─────────────────────────────────────────────────────────
# Selector with flag OFF
# ─────────────────────────────────────────────────────────

def test_selector_flag_off_returns_leader_unchanged():
    """Default behavior (flag False): return primary-sort leader regardless of ties."""
    # Plan A: total=10 min, first_drop @ 5 min
    # Plan B: total=10.5 min, first_drop @ 1 min ← shorter first drop
    plan_a = _mk_plan(["a"], 10.0, deliveries_offset_min={"a": 5.0})
    plan_b = _mk_plan(["b"], 10.5, deliveries_offset_min={"b": 1.0})
    with patch("dispatch_v2.common.ENABLE_V327_BUG_FIXES_BUNDLE", False):
        result = RS._select_best_with_tie_breaker([plan_a, plan_b], NOW)
    assert result is plan_a, "Flag OFF: leader (lower total) wins, NIE tie-breaker"


def test_selector_flag_on_no_ties_returns_leader():
    """Flag ON ale brak ties (diff > threshold): leader wins."""
    plan_a = _mk_plan(["a"], 10.0, deliveries_offset_min={"a": 5.0})
    plan_b = _mk_plan(["b"], 15.0, deliveries_offset_min={"b": 1.0})  # diff=5 > 2 threshold
    with patch("dispatch_v2.common.ENABLE_V327_BUG_FIXES_BUNDLE", True):
        result = RS._select_best_with_tie_breaker([plan_a, plan_b], NOW)
    assert result is plan_a, "No ties (diff > 2 min): leader wins"


def test_selector_flag_on_ties_picks_shortest_first_drop():
    """Flag ON, 2 ties (|diff|<2 min): shortest first drop arrival wins."""
    plan_a = _mk_plan(["a"], 10.0, deliveries_offset_min={"a": 13.0})  # leader by primary
    plan_b = _mk_plan(["b"], 11.0, deliveries_offset_min={"b": 3.0})   # tied (diff=1<2), faster first drop
    with patch("dispatch_v2.common.ENABLE_V327_BUG_FIXES_BUNDLE", True):
        result = RS._select_best_with_tie_breaker([plan_a, plan_b], NOW)
    assert result is plan_b, \
        f"Tied (diff<2): expected B (first_drop=3min), got {result.sequence}"


def test_selector_three_ties_picks_minimum_first_drop():
    """3 plans all tied: pick one with minimum first_drop_arrival."""
    p1 = _mk_plan(["x"], 10.0, deliveries_offset_min={"x": 8.0})
    p2 = _mk_plan(["y"], 11.0, deliveries_offset_min={"y": 4.0})  # ← min first drop
    p3 = _mk_plan(["z"], 11.5, deliveries_offset_min={"z": 6.0})
    with patch("dispatch_v2.common.ENABLE_V327_BUG_FIXES_BUNDLE", True):
        result = RS._select_best_with_tie_breaker([p1, p2, p3], NOW)
    assert result is p2, f"3 ties: expected p2 (4min), got {result.sequence}"


def test_selector_diff_just_above_threshold_no_tie():
    """diff = 2.0 (boundary, NOT < 2): leader wins, NIE tie-breaker."""
    plan_a = _mk_plan(["a"], 10.0, deliveries_offset_min={"a": 13.0})
    plan_b = _mk_plan(["b"], 12.0, deliveries_offset_min={"b": 3.0})  # diff=2.0 exact
    with patch("dispatch_v2.common.ENABLE_V327_BUG_FIXES_BUNDLE", True):
        result = RS._select_best_with_tie_breaker([plan_a, plan_b], NOW)
    assert result is plan_a, "diff=2.0 NIE w przedziale (<2), leader wins"


def test_selector_different_sla_violations_no_tie_break():
    """Tie-breaker apply TYLKO gdy sla_violations equal. Mniej violations always wins."""
    plan_a = _mk_plan(["a"], 10.0, sla_violations=0,
                      deliveries_offset_min={"a": 13.0})
    plan_b = _mk_plan(["b"], 10.5, sla_violations=1,
                      deliveries_offset_min={"b": 1.0})  # better first drop ALE 1 sla viol
    with patch("dispatch_v2.common.ENABLE_V327_BUG_FIXES_BUNDLE", True):
        result = RS._select_best_with_tie_breaker([plan_a, plan_b], NOW)
    assert result is plan_a, "Different sla_violations: NIE apply tie-breaker"


def test_selector_empty_plans_returns_none():
    assert RS._select_best_with_tie_breaker([], NOW) is None


def test_selector_single_plan_returns_it():
    plan = _mk_plan(["x"], 10.0)
    assert RS._select_best_with_tie_breaker([plan], NOW) is plan


# ─────────────────────────────────────────────────────────
# Mental simulation #468508
# ─────────────────────────────────────────────────────────

def test_proposal_468508_skłodowskiej_first_wins_post_tie_breaker():
    """Mental simulation #468508 reproduction:
    Pre-fix: 2 permutacje tied (Czarnogórska-first 49.3 min, Skłodowskiej-first 49.3 min).
    Post-X traffic mult: 1.2 × 49.3 = 59.16 → STILL TIED (global ratio).
    Post-Bug-Y tie-breaker: Skłodowskiej-first wygrywa bo jej arrival ~3 min,
    Czarnogórska arrival ~13 min.
    """
    # Czarnogórska-first plan: total=59.16, first drop @ 13 min
    plan_czarnogorska = _mk_plan(
        ["468497", "468499", "468508"],  # Czarnogórska, Skłodowskiej, new
        59.16, sla_violations=0,
        deliveries_offset_min={"468497": 13.0, "468499": 24.0, "468508": 49.0},
    )
    # Skłodowskiej-first plan: total=59.16, first drop @ 3 min
    plan_skłodowskiej = _mk_plan(
        ["468499", "468497", "468508"],  # Skłodowskiej, Czarnogórska, new
        59.16, sla_violations=0,
        deliveries_offset_min={"468499": 3.0, "468497": 18.0, "468508": 49.0},
    )

    with patch("dispatch_v2.common.ENABLE_V327_BUG_FIXES_BUNDLE", True):
        result = RS._select_best_with_tie_breaker(
            [plan_czarnogorska, plan_skłodowskiej], NOW,
        )
    assert result is plan_skłodowskiej, \
        f"#468508 mental sim: Skłodowskiej-first should win (3min vs 13min first drop), " \
        f"got {result.sequence}"


# ─────────────────────────────────────────────────────────
# Backward compat
# ─────────────────────────────────────────────────────────

def test_helper_module_exports():
    """V3.27 helpers exported."""
    assert hasattr(RS, "_select_best_with_tie_breaker")
    assert hasattr(RS, "_first_drop_arrival_min")
    assert hasattr(RS, "V327_TIE_BREAKER_THRESHOLD_MIN")
    assert RS.V327_TIE_BREAKER_THRESHOLD_MIN == 2.0


if __name__ == "__main__":
    test_first_drop_arrival_basic()
    print("test_first_drop_arrival_basic: PASS")
    test_first_drop_arrival_empty_sequence()
    print("test_first_drop_arrival_empty_sequence: PASS")
    test_first_drop_arrival_missing_delivered_at()
    print("test_first_drop_arrival_missing_delivered_at: PASS")
    test_selector_flag_off_returns_leader_unchanged()
    print("test_selector_flag_off_returns_leader_unchanged: PASS")
    test_selector_flag_on_no_ties_returns_leader()
    print("test_selector_flag_on_no_ties_returns_leader: PASS")
    test_selector_flag_on_ties_picks_shortest_first_drop()
    print("test_selector_flag_on_ties_picks_shortest_first_drop: PASS")
    test_selector_three_ties_picks_minimum_first_drop()
    print("test_selector_three_ties_picks_minimum_first_drop: PASS")
    test_selector_diff_just_above_threshold_no_tie()
    print("test_selector_diff_just_above_threshold_no_tie: PASS")
    test_selector_different_sla_violations_no_tie_break()
    print("test_selector_different_sla_violations_no_tie_break: PASS")
    test_selector_empty_plans_returns_none()
    print("test_selector_empty_plans_returns_none: PASS")
    test_selector_single_plan_returns_it()
    print("test_selector_single_plan_returns_it: PASS")
    test_proposal_468508_skłodowskiej_first_wins_post_tie_breaker()
    print("test_proposal_468508_skłodowskiej_first_wins_post_tie_breaker: PASS")
    test_helper_module_exports()
    print("test_helper_module_exports: PASS")
    print("ALL 13/13 PASS")
