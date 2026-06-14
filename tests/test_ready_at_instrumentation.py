"""Fala 1 fundament (2026-06-14): durable per-order log ready_at.

sla_tracker._check_restaurant_violations emituje rekord ready_at dla KAŻDEGO
odbioru (nie tylko violations), gdy ENABLE_READY_AT_INSTRUMENTATION=ON.
Telemetria-only, one-shot (ready_at_logged), fail-soft. Łączy declared(commit)
/arrived(waiting_at status4)/picked(real) → prep_bias offline.

Scenariusze:
- NIE-violation (wait≤5, brak waiting_at) → ready_at LOGOWANE (dowód: nie tylko ogon)
- waited (waiting_at status4, wait>5) → ready_basis=waited + prep_bias
- dedup: drugi skan nie dubluje
- flaga OFF → no-op
"""
import json

import pytest

from dispatch_v2 import sla_tracker


def _order(oid="479001", **over):
    o = {
        "order_id": oid,
        "status": "picked_up",
        "restaurant": "Mama Thai",
        "courier_id": "370",
        "order_type": "elastyk",
        "address_id": "149",
        "czas_kuriera_warsaw": "2026-06-10T12:00:00+02:00",  # commit 12:00
        "picked_up_at": "2026-06-10 12:03:00",               # picked 12:03 (≤5 → NIE violation)
    }
    o.update(over)
    return o


@pytest.fixture
def harness(monkeypatch, tmp_path):
    state = {"orders": [], "upserts": []}
    rpath = tmp_path / "ready_at_log.jsonl"
    vpath = tmp_path / "restaurant_violations.jsonl"

    def fake_get_by_status(status):
        return [o for o in state["orders"] if o.get("status") == status]

    def fake_upsert(oid, data, event=None):
        state["upserts"].append((oid, data, event))
        for o in state["orders"]:
            if o.get("order_id") == oid:
                o.update(data)
        return {}

    monkeypatch.setattr(sla_tracker, "get_by_status", fake_get_by_status)
    monkeypatch.setattr(sla_tracker, "upsert_order", fake_upsert)
    monkeypatch.setattr(sla_tracker, "READY_AT_LOG_PATH", rpath)
    monkeypatch.setattr(sla_tracker, "RESTAURANT_VIOLATIONS_PATH", vpath)
    monkeypatch.setattr(
        sla_tracker.C, "flag",
        lambda name, default=False: True
        if name in ("ENABLE_RESTAURANT_VIOLATIONS", "ENABLE_READY_AT_INSTRUMENTATION")
        else default,
    )
    state["rpath"] = rpath
    state["vpath"] = vpath
    return state


def _ready_entries(state):
    p = state["rpath"]
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines() if line.strip()]


def test_ready_at_logged_for_non_violation(harness):
    """Odbiór 3 min po commicie (NIE violation) → ready_at JEST logowane."""
    harness["orders"] = [_order()]
    sla_tracker._check_restaurant_violations()
    rows = _ready_entries(harness)
    assert len(rows) == 1, "ready_at musi logować NIE-violation (nie tylko ogon)"
    r = rows[0]
    assert r["order_id"] == "479001"
    assert r["wait_min"] == 3.0
    assert r["prep_bias_min"] == 3.0
    assert r["arrival_source"] == "commit_fallback"
    assert r["ready_basis"] == "no_arrival_signal"
    assert r["arrived_at_iso"] is None
    # set-then-write: flaga ready_at_logged ustawiona
    assert any(u[1].get("ready_at_logged") for u in harness["upserts"])


def test_ready_at_waited_with_status4(harness):
    """waiting_at status4 + odbiór 10 min po commicie → ready_basis=waited."""
    harness["orders"] = [_order(
        waiting_at="2026-06-10T12:01:00+02:00",
        picked_up_at="2026-06-10 12:10:00",
    )]
    sla_tracker._check_restaurant_violations()
    rows = _ready_entries(harness)
    assert len(rows) == 1
    r = rows[0]
    assert r["arrival_source"] == "status4"
    assert r["ready_basis"] == "waited"
    assert r["wait_min"] == 9.0          # picked 12:10 − max(12:00,12:01)=12:01
    assert r["prep_bias_min"] == 10.0    # picked 12:10 − commit 12:00
    assert r["arrived_at_iso"] is not None


def test_ready_at_dedup(harness):
    """Drugi skan nie dubluje (one-shot ready_at_logged)."""
    harness["orders"] = [_order()]
    sla_tracker._check_restaurant_violations()
    sla_tracker._check_restaurant_violations()
    assert len(_ready_entries(harness)) == 1


def test_ready_at_flag_off_noop(harness, monkeypatch):
    """ENABLE_READY_AT_INSTRUMENTATION=OFF → zero rekordów (violations dalej działa)."""
    monkeypatch.setattr(
        sla_tracker.C, "flag",
        lambda name, default=False: True
        if name == "ENABLE_RESTAURANT_VIOLATIONS" else default,
    )
    harness["orders"] = [_order()]
    sla_tracker._check_restaurant_violations()
    assert _ready_entries(harness) == []
