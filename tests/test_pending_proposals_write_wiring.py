#!/usr/bin/env python3
"""Test wpięcia Opcji B w shadow_dispatcher._tick: flaga ON zapisuje PROPOSE do
pending_proposals (przez store), OFF = no-op. Granice _tick mockowane."""
import sys, types
sys.path.insert(0, "/root/.openclaw/workspace/scripts")
from dispatch_v2 import shadow_dispatcher as SD
from dispatch_v2 import pending_proposals_store as PPS


def _wire(monkeypatch, flag_on, verdict="PROPOSE"):
    captured = {"upserts": None}
    ev = {"event_id": "e1", "order_id": "o1",
          "payload": {"pickup_coords": [53.1, 23.1], "delivery_coords": [53.2, 23.2]}}
    monkeypatch.setattr(SD.event_bus, "get_pending", lambda **k: [ev])
    monkeypatch.setattr(SD.event_bus, "mark_processed", lambda *a, **k: None)
    monkeypatch.setattr(SD.event_bus, "mark_failed", lambda *a, **k: None)
    monkeypatch.setattr(SD, "dispatchable_fleet", lambda: [])
    monkeypatch.setattr(SD.state_machine, "get_all", lambda: {})
    monkeypatch.setattr(SD, "process_event", lambda ev, fleet, meta, now=None: types.SimpleNamespace(verdict=verdict))
    monkeypatch.setattr(SD, "_serialize_result",
                        lambda result, eid, lat: {"verdict": verdict, "best": {"courier_id": "A"}})
    monkeypatch.setattr(SD, "_append_decision", lambda *a, **k: None)
    # neutralizuj poboczne haki (pending_pool, auto_assign, probe) — fail-soft i tak,
    # ale ucinamy I/O; flagi przez C.flag
    monkeypatch.setattr(SD.C, "flag",
                        lambda n, d=False: True if n == "ENABLE_PENDING_PROPOSALS_WRITE" and flag_on else d)
    monkeypatch.setattr(SD.C, "ENABLE_PENDING_POOL", False, raising=False)

    def _fake_upsert(upserts, now, **k):
        captured["upserts"] = [(o, r.get("verdict")) for (o, r) in upserts]
        return len(list(captured["upserts"]))
    monkeypatch.setattr(PPS, "upsert_proposals", _fake_upsert)
    SD._tick("/tmp/_x_shadow.jsonl", None)
    return captured


def test_flag_on_writes_propose(monkeypatch):
    cap = _wire(monkeypatch, flag_on=True, verdict="PROPOSE")
    assert cap["upserts"] == [("o1", "PROPOSE")]


def test_flag_off_noop(monkeypatch):
    cap = _wire(monkeypatch, flag_on=False, verdict="PROPOSE")
    assert cap["upserts"] is None   # store nigdy nie wołany


def test_flag_on_skips_non_propose(monkeypatch):
    cap = _wire(monkeypatch, flag_on=True, verdict="KOORD")
    assert cap["upserts"] is None   # KOORD nie idzie do pending_proposals

