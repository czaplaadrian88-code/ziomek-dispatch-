"""Testy route_metrics.compute_plan_metrics (sprint OBJ F0, 2026-05-17).

Metryki jakości planu: route_span / idle / thermal / r6_breach. Pure functions —
testy na lekkich atrapach planu (SimpleNamespace), bez OSRM/solvera.
"""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

from dispatch_v2.route_metrics import compute_plan_metrics, R6_SLA_MIN

UTC = timezone.utc
T0 = datetime(2026, 5, 17, 12, 0, 0, tzinfo=UTC)


def _plan(total=0.0, per_order=None, arrival_at=None, pickup_at=None):
    return SimpleNamespace(
        total_duration_min=total,
        per_order_delivery_times=per_order,
        arrival_at=arrival_at or {},
        pickup_at=pickup_at or {},
    )


# ─── pusty plan ──────────────────────────────────────────────────────

def test_empty_plan_all_zero():
    m = compute_plan_metrics(_plan(), dwell_pickup_min=1.0)
    assert m == {
        "route_span_min": 0.0,
        "idle_total_min": 0.0,
        "max_thermal_age_min": 0.0,
        "r6_breach_max_min": 0.0,
        "r6_breach_count": 0,
    }


def test_span_from_total_duration():
    m = compute_plan_metrics(_plan(total=57.2), dwell_pickup_min=1.0)
    assert m["route_span_min"] == 57.2


# ─── thermal / R6 ────────────────────────────────────────────────────

def test_thermal_and_r6_breach():
    # a=20 OK, b=50 breach +15, c=36 breach +1 → max thermal 50, breach max 15, count 2
    m = compute_plan_metrics(
        _plan(per_order={"a": 20.0, "b": 50.0, "c": 36.0}), dwell_pickup_min=1.0
    )
    assert m["max_thermal_age_min"] == 50.0
    assert m["r6_breach_max_min"] == 15.0
    assert m["r6_breach_count"] == 2


def test_no_r6_breach_when_all_under_35():
    m = compute_plan_metrics(
        _plan(per_order={"a": 20.0, "b": 34.99}), dwell_pickup_min=1.0
    )
    assert m["r6_breach_max_min"] == 0.0
    assert m["r6_breach_count"] == 0
    assert m["max_thermal_age_min"] == 34.99


def test_r6_sla_constant_is_35():
    assert R6_SLA_MIN == 35.0


def test_per_order_none_safe():
    m = compute_plan_metrics(_plan(per_order=None), dwell_pickup_min=1.0)
    assert m["max_thermal_age_min"] == 0.0 and m["r6_breach_count"] == 0


# ─── idle ────────────────────────────────────────────────────────────

def test_idle_basic_wait():
    # przyjazd 12:00, pickup 12:10 (po wait+dwell), dwell 1.0 → idle = 10 - 1 = 9
    m = compute_plan_metrics(
        _plan(arrival_at={"a": T0}, pickup_at={"a": T0 + timedelta(minutes=10)}),
        dwell_pickup_min=1.0,
    )
    assert m["idle_total_min"] == 9.0


def test_idle_zero_when_no_wait():
    # pickup = arrival + dwell dokładnie → zero idle (kurier nie czekał)
    m = compute_plan_metrics(
        _plan(arrival_at={"a": T0}, pickup_at={"a": T0 + timedelta(minutes=1)}),
        dwell_pickup_min=1.0,
    )
    assert m["idle_total_min"] == 0.0


def test_idle_sums_multiple_pickups():
    m = compute_plan_metrics(
        _plan(
            arrival_at={"a": T0, "b": T0 + timedelta(minutes=30)},
            pickup_at={
                "a": T0 + timedelta(minutes=6),       # wait 5
                "b": T0 + timedelta(minutes=30 + 4),  # wait 3
            },
        ),
        dwell_pickup_min=1.0,
    )
    assert m["idle_total_min"] == 8.0


def test_idle_fix7_group_dedup():
    # super-pickup: 2 oidy dzielą TEN SAM przyjazd+pickup → liczone RAZ, nie 2×.
    arr = T0
    pu = T0 + timedelta(minutes=10)
    m = compute_plan_metrics(
        _plan(arrival_at={"a": arr, "b": arr}, pickup_at={"a": pu, "b": pu}),
        dwell_pickup_min=1.0,
    )
    assert m["idle_total_min"] == 9.0  # nie 18.0


def test_idle_ignores_picked_up_bag():
    # picked_up bag nie ma węzła pickup → brak w arrival_at → nie liczy się do idle
    m = compute_plan_metrics(
        _plan(per_order={"picked": 25.0}, arrival_at={}, pickup_at={}),
        dwell_pickup_min=1.0,
    )
    assert m["idle_total_min"] == 0.0


def test_idle_naive_datetime_coerced():
    # naive datetime (bez tz) — coerce do UTC, nie crash
    naive0 = datetime(2026, 5, 17, 12, 0, 0)
    m = compute_plan_metrics(
        _plan(arrival_at={"a": naive0},
              pickup_at={"a": naive0 + timedelta(minutes=10)}),
        dwell_pickup_min=1.0,
    )
    assert m["idle_total_min"] == 9.0


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
