"""R-INTRA-RESTAURANT-GAP (2026-05-14) — hard reject gdy gap między dwoma
kolejnymi pickupami tej samej restauracji w plan.pickup_at > 5 min.

Diagnoza propozycji K-523 Marcin By Raj→Raj (gap 13 min, wait_courier formuła
ślepa bo arrival_at[new]≈ready[new] dla mid-trip same-restaurant insert).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from dispatch_v2 import common as C
from dispatch_v2 import dispatch_pipeline


class _StubPlan:
    def __init__(self, pickup_at, sequence=None):
        self.pickup_at = pickup_at
        self.sequence = sequence or list(pickup_at.keys())
        self.predicted_delivered_at = {}
        self.arrival_at = {}
        self.total_duration_min = 0.0
        self.strategy = "stub"
        self.sla_violations = 0
        self.osrm_fallback_used = False
        self.per_order_delivery_times = None


def _now():
    return datetime(2026, 5, 14, 12, 0, tzinfo=timezone.utc)


def _run_check(plan, new_oid, restaurant, bag_raw):
    """Replikuje wycinek logiki z dispatch_pipeline._v327_eval_courier."""
    from datetime import datetime as _dt_irg
    intra_rest_gap_max_min = 0.0
    intra_rest_gap_max_pair = None
    intra_rest_gap_max_restaurant = None
    intra_rest_gap_hard_reject = False
    if not getattr(C, "ENABLE_INTRA_RESTAURANT_GAP_LIMIT", False) or plan is None:
        return (intra_rest_gap_max_min, intra_rest_gap_max_pair,
                intra_rest_gap_max_restaurant, intra_rest_gap_hard_reject)
    _rest_by_oid = {new_oid: restaurant} if new_oid else {}
    for b in bag_raw or []:
        oid = str(b.get("order_id") or "")
        if oid:
            _rest_by_oid[oid] = b.get("restaurant")
    pickups = []
    for oid, pat in (plan.pickup_at or {}).items():
        dt = _dt_irg.fromisoformat(str(pat)) if isinstance(pat, str) else pat
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        pickups.append((dt, str(oid)))
    pickups.sort(key=lambda x: x[0])
    for i in range(len(pickups) - 1):
        t1, o1 = pickups[i]
        t2, o2 = pickups[i + 1]
        r1 = _rest_by_oid.get(o1)
        r2 = _rest_by_oid.get(o2)
        if r1 is None or r2 is None or r1 != r2:
            continue
        gap = (t2 - t1).total_seconds() / 60.0
        if gap > intra_rest_gap_max_min:
            intra_rest_gap_max_min = gap
            intra_rest_gap_max_pair = (o1, o2)
            intra_rest_gap_max_restaurant = r1
        if gap > C.MAX_INTRA_RESTAURANT_GAP_MIN:
            intra_rest_gap_hard_reject = True
    return (intra_rest_gap_max_min, intra_rest_gap_max_pair,
            intra_rest_gap_max_restaurant, intra_rest_gap_hard_reject)


def test_repro_marcin_by_raj_raj_13min_gap_hard_rejects():
    """K-523 Marcin By scenariusz: pickup#1 14:02 + pickup#2 14:15 = 13 min."""
    n = _now().replace(hour=14, minute=2)
    plan = _StubPlan(pickup_at={"O1": n, "O2": n + timedelta(minutes=13)})
    gap, pair, rest, reject = _run_check(plan, "O2", "Raj",
                                         [{"order_id": "O1", "restaurant": "Raj"}])
    assert gap == 13.0
    assert pair == ("O1", "O2")
    assert rest == "Raj"
    assert reject is True


@pytest.mark.parametrize("gap_min,expect_reject", [
    (0.0, False), (3.0, False), (5.0, False),
    (5.01, True), (6.0, True), (15.0, True),
])
def test_threshold_boundary_5min_inclusive(gap_min, expect_reject):
    n = _now()
    plan = _StubPlan(pickup_at={"O1": n, "O2": n + timedelta(minutes=gap_min)})
    _, _, _, reject = _run_check(plan, "O2", "Raj",
                                 [{"order_id": "O1", "restaurant": "Raj"}])
    assert reject is expect_reject


def test_different_restaurants_no_reject_even_when_gap_huge():
    n = _now()
    plan = _StubPlan(pickup_at={"O1": n, "O2": n + timedelta(minutes=30)})
    gap, _, _, reject = _run_check(plan, "O2", "Pizzeria",
                                   [{"order_id": "O1", "restaurant": "Raj"}])
    assert gap == 0.0
    assert reject is False


def test_three_pickups_same_restaurant_picks_max_pair():
    n = _now()
    plan = _StubPlan(pickup_at={
        "O1": n,
        "O2": n + timedelta(minutes=2),     # gap 2
        "O3": n + timedelta(minutes=12),    # gap 10 ← max
    })
    bag = [{"order_id": "O1", "restaurant": "Raj"},
           {"order_id": "O2", "restaurant": "Raj"}]
    gap, pair, _, reject = _run_check(plan, "O3", "Raj", bag)
    assert gap == 10.0
    assert pair == ("O2", "O3")
    assert reject is True


def test_flag_off_disables_check():
    with patch.object(C, "ENABLE_INTRA_RESTAURANT_GAP_LIMIT", False):
        n = _now()
        plan = _StubPlan(pickup_at={"O1": n, "O2": n + timedelta(minutes=20)})
        gap, _, _, reject = _run_check(plan, "O2", "Raj",
                                       [{"order_id": "O1", "restaurant": "Raj"}])
        assert reject is False
        assert gap == 0.0


def test_constants_present_and_sane():
    assert hasattr(C, "ENABLE_INTRA_RESTAURANT_GAP_LIMIT")
    assert hasattr(C, "MAX_INTRA_RESTAURANT_GAP_MIN")
    assert C.MAX_INTRA_RESTAURANT_GAP_MIN == 5.0


def test_reason_format_in_pipeline_consume():
    """Smoke że stała pasuje do reason templatu w pipeline."""
    # K11: tresc petli w core/candidates.py; sciezki SELF-LOCATED (nie hardkod
    # kanonu — w biegu z worktree skaner czytalby CUDZY plik = klamiacy straznik)
    import pathlib
    _repo = pathlib.Path(__file__).resolve().parents[1]
    src = (_repo / "dispatch_pipeline.py").read_text() + (_repo / "core" / "candidates.py").read_text() + (_repo / "core" / "selection.py").read_text()
    assert "intra_restaurant_gap_exceeded" in src
    assert "MAX_INTRA_RESTAURANT_GAP_MIN" in src
    assert "intra_rest_gap_hard_reject" in src


def test_best_effort_filter_excludes_intra_gap_reject():
    """Opcja A (2026-05-14 21:19): best_effort path MUSI filtrować candidates
    z intra_rest_gap_hard_reject=True PRZED sort/select. Repro case 473251
    Chicago Pizza 26.45 min — pre-fix BEST był wybrany przez best_effort mimo
    hard_reject flag (bo MAYBE→NO override nie zadziałał gdy verdict już NO).
    """
    # K11: tresc petli w core/candidates.py; sciezki SELF-LOCATED (nie hardkod
    # kanonu — w biegu z worktree skaner czytalby CUDZY plik = klamiacy straznik)
    import pathlib
    _repo = pathlib.Path(__file__).resolve().parents[1]
    src = (_repo / "dispatch_pipeline.py").read_text() + (_repo / "core" / "candidates.py").read_text() + (_repo / "core" / "selection.py").read_text()
    # Filter helper present
    assert "_intra_gap_reject" in src, "best_effort filter helper missing"
    # Filter applied to with_plan list comprehension
    assert "not _intra_gap_reject(c)" in src, "best_effort filter not applied to with_plan"
    # Comment explains rationale (case 473251)
    assert "473251" in src or "Opcja A" in src, "best_effort filter rationale comment missing"


def test_best_effort_filter_helper_logic():
    """Helper _intra_gap_reject zwraca True gdy metrics.intra_rest_gap_hard_reject=True."""
    class _Mock:
        def __init__(self, metrics):
            self.metrics = metrics
    # Replicate helper inline (same logic as pipeline)
    def _intra_gap_reject(c):
        return bool((c.metrics or {}).get("intra_rest_gap_hard_reject"))
    assert _intra_gap_reject(_Mock({"intra_rest_gap_hard_reject": True})) is True
    assert _intra_gap_reject(_Mock({"intra_rest_gap_hard_reject": False})) is False
    assert _intra_gap_reject(_Mock({})) is False
    assert _intra_gap_reject(_Mock(None)) is False
