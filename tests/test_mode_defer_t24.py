"""T2.4 inkrement 3 — S2-defer slot-search (advisory Tura 2, spec A2).

Pura logika `defer_search`: pętla slotów +5..90′, budżet ≤3 próby/Σ90′,
completion-guard (owner+deadline, zero sierot). feasibility WSTRZYKIWANA (zaślepka).
"""
from __future__ import annotations

from dispatch_v2 import mode_layer as M


def test_finds_first_feasible_slot():
    # feasible dopiero od 30′ po declared_ready
    def feas(slot):
        return "c9" if slot >= 100 + 30 else None
    p = M.defer_search("o1", created_min=90, declared_ready_min=100, now_min=105,
                       feasible_at=feas)
    assert p is not None
    assert p.slot_min == 130.0            # base=max(100,105)=105 → +5 kroki → 130
    assert p.owner == "c9"
    assert p.shift_min == 30.0            # 130 − declared_ready 100
    assert p.attempt == 1
    assert p.deadline_min == 180.0        # created 90 + 90 horizon


def test_no_slot_in_horizon_returns_none():
    p = M.defer_search("o2", created_min=90, declared_ready_min=100, now_min=105,
                       feasible_at=lambda s: None)
    assert p is None                       # → eskalacja S3/ALARM (nigdy sierota)


def test_budget_max_attempts():
    p = M.defer_search("o3", created_min=0, declared_ready_min=0, now_min=0,
                       feasible_at=lambda s: "c1", prev_attempts=3)
    assert p is None                       # budżet 3 wyczerpany


def test_budget_span_cap():
    # feasible dopiero późno; prev_span już 80 → +shift przekroczy 90 → None
    def feas(slot):
        return "c1" if slot >= 85 else None
    p = M.defer_search("o4", created_min=0, declared_ready_min=0, now_min=0,
                       feasible_at=feas, prev_span_min=80.0)
    assert p is None


def test_attempt_increments_with_prev():
    p = M.defer_search("o5", created_min=0, declared_ready_min=0, now_min=0,
                       feasible_at=lambda s: "c2", prev_attempts=1)
    assert p is not None and p.attempt == 2


def test_completion_guard_fields_present():
    p = M.defer_search("o6", created_min=10, declared_ready_min=20, now_min=25,
                       feasible_at=lambda s: "c3")
    assert p.owner == "c3" and p.deadline_min == 100.0 and p.reason == "S2_defer"


def test_format_defer_slot_proposal():
    from datetime import datetime, timezone
    # slot = 2026-07-07 12:30 Warsaw = 10:30 UTC → epoch-min
    slot_epoch = datetime(2026, 7, 7, 10, 30, tzinfo=timezone.utc).timestamp() / 60.0
    dl_epoch = datetime(2026, 7, 7, 11, 0, tzinfo=timezone.utc).timestamp() / 60.0
    p = M.DeferProposal(order_id="o1", slot_min=slot_epoch, shift_min=20.0, attempt=1,
                        owner="c5", deadline_min=dl_epoch)
    out = M.format_defer_slot_proposal(p)
    assert out["order_id"] == "o1"
    assert out["proposed_pickup_hhmm"] == "12:30"   # Warsaw
    assert out["deadline_hhmm"] == "13:00"
    assert out["shift_min"] == 20 and out["owner_courier"] == "c5"
    assert out["source"] == "S2_defer" and "12:30" in out["message"]


def test_format_defer_slot_proposal_none():
    assert M.format_defer_slot_proposal(None) is None
