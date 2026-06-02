"""Gap-fill partial-coverage regeneration (2026-06-02).

courier_api/build_view renderuje ziomek_plan TYLKO przy pełnym pokryciu worka
(worek ⊆ plan); plan częściowy spada do fallback_nn. _gap_fill_plans regeneruje
plan gdy aktywny plan NIE pokrywa całego realnego worka, a pełne pokrycie zostawia
nietknięte (zero churn).

Mockuje _gen_one_bag_plan (omija OSRM/route_simulator_v2) i weryfikuje DECYZJĘ
o regeneracji + liczniki summary.
"""
import os
import sys

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from datetime import datetime, timezone

from dispatch_v2 import plan_recheck as pr


def _order(status="assigned", cid="123"):
    return {"status": status, "courier_id": cid}


def _plan(order_ids, invalidated=None):
    return {
        "invalidated_at": invalidated,
        "stops": [{"order_id": o, "type": "dropoff"} for o in order_ids],
    }


def _run(monkeypatch, orders_state, plans):
    """Zwraca (summary, lista cid przekazanych do _gen_one_bag_plan)."""
    calls = []

    def fake_gen(cid, oids, *a, **kw):
        calls.append((cid, sorted(oids)))
        return True  # udajemy udany zapis planu

    monkeypatch.setattr(pr, "_gen_one_bag_plan", fake_gen)
    summary = {}
    now = datetime(2026, 6, 2, 12, 0, tzinfo=timezone.utc)
    pr._gap_fill_plans(orders_state, plans, {}, now, summary)
    return summary, calls


def test_full_coverage_plan_not_touched(monkeypatch):
    """Plan pokrywa cały worek (worek ⊆ plan) → brak regeneracji (zero churn)."""
    orders = {"1001": _order(), "1002": _order()}
    plans = {"123": _plan(["1001", "1002"])}
    summary, calls = _run(monkeypatch, orders, plans)
    assert calls == []
    assert summary["bag_plans_generated"] == 0
    assert summary["bag_plans_partial_regen"] == 0


def test_plan_superset_of_bag_not_touched(monkeypatch):
    """Plan ma więcej zleceń niż realny worek (stale w planie) → nadal pełne
    pokrycie worka → brak regeneracji."""
    orders = {"1001": _order()}
    plans = {"123": _plan(["1001", "1002", "1003"])}
    summary, calls = _run(monkeypatch, orders, plans)
    assert calls == []
    assert summary["bag_plans_partial_regen"] == 0


def test_partial_plan_triggers_regen(monkeypatch):
    """Plan pokrywa tylko część worka → regeneracja na PEŁNYM worku + licznik."""
    orders = {"1001": _order(), "1002": _order(), "1003": _order()}
    plans = {"123": _plan(["1001", "1002"])}  # brak 1003
    summary, calls = _run(monkeypatch, orders, plans)
    assert calls == [("123", ["1001", "1002", "1003"])]
    assert summary["bag_plans_generated"] == 1
    assert summary["bag_plans_partial_regen"] == 1


def test_no_plan_regen_but_not_partial(monkeypatch):
    """Brak planu (PANEL_OVERRIDE) → regeneracja, ale NIE liczona jako partial."""
    orders = {"1001": _order(), "1002": _order()}
    plans = {}
    summary, calls = _run(monkeypatch, orders, plans)
    assert calls == [("123", ["1001", "1002"])]
    assert summary["bag_plans_generated"] == 1
    assert summary["bag_plans_partial_regen"] == 0


def test_invalidated_plan_treated_as_missing(monkeypatch):
    """Plan invalidated → traktowany jak brak planu → regeneracja (nie partial)."""
    orders = {"1001": _order()}
    plans = {"123": _plan(["1001"], invalidated="2026-06-02T11:00:00+00:00")}
    summary, calls = _run(monkeypatch, orders, plans)
    assert calls == [("123", ["1001"])]
    assert summary["bag_plans_partial_regen"] == 0


def test_terminal_orders_excluded_from_bag(monkeypatch):
    """Delivered/cancelled nie wchodzą do worka → pokrycie liczone tylko po
    aktywnych; plan na sam aktywny order = pełne pokrycie → brak regeneracji."""
    orders = {
        "1001": _order(),
        "1002": _order(status="delivered"),
    }
    plans = {"123": _plan(["1001"])}
    summary, calls = _run(monkeypatch, orders, plans)
    assert calls == []
    assert summary["bag_plans_partial_regen"] == 0
