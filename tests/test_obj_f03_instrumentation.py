"""Testy instrumentacji sprint OBJ F0.3 (2026-05-17).

Pokrywa: objm_ w whitelist serializera, propagacja objm_ metryk, replay-capture
(flag-gating, round-trip pól, append, fail-safe).
"""
import json
from datetime import datetime, timezone

from dispatch_v2 import common as C
from dispatch_v2 import obj_replay_capture as orc
from dispatch_v2 import shadow_dispatcher as sd
from dispatch_v2.route_simulator_v2 import OrderSim

UTC = timezone.utc


def _order(oid, status="assigned"):
    o = OrderSim(
        order_id=oid,
        pickup_coords=(53.10, 23.10),
        delivery_coords=(53.20, 23.20),
        picked_up_at=datetime(2026, 5, 17, 16, 0, tzinfo=UTC)
        if status == "picked_up" else None,
        status=status,
        pickup_ready_at=datetime(2026, 5, 17, 16, 30, tzinfo=UTC),
    )
    o.czas_kuriera_warsaw = "2026-05-17T18:30:00+02:00"
    return o


# ─── serializer whitelist (encoding checklist: serializer) ───────────

def test_objm_keys_reach_ledger():
    # L1.1 (2026-07-01): whitelist prefiksów zastąpiona deny-listą —
    # objm_* trafia do ledgera z konstrukcji.
    base: dict = {}
    sd._propagate_prefixed_metrics(base, {"objm_probe": 1.0})
    assert base.get("objm_probe") == 1.0


def test_propagate_objm_metric():
    base: dict = {}
    sd._propagate_prefixed_metrics(base, {
        "objm_idle_total_min": 5.0,
        "objm_r6_breach_max_min": 12.0,
        "not_whitelisted": 99,
    })
    assert base["objm_idle_total_min"] == 5.0
    assert base["objm_r6_breach_max_min"] == 12.0
    # L1.1 (2026-07-01): deny-list — klucz spoza dawnej whitelisty TEŻ
    # trafia do ledgera (kontrakt ⑤: koniec cichych dziur widoczności).
    assert base.get("not_whitelisted") == 99


# ─── replay-capture ──────────────────────────────────────────────────

def test_capture_noop_when_flag_off(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "ENABLE_OBJ_REPLAY_CAPTURE", False)
    p = tmp_path / "cap.jsonl"
    orc.capture((53.1, 23.1), [_order("b1", "picked_up")], _order("new"),
                datetime(2026, 5, 17, 16, 0, tzinfo=UTC), 1.0, 3.0,
                "std", "new", path=str(p))
    assert not p.exists()


def test_capture_writes_and_roundtrips(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "ENABLE_OBJ_REPLAY_CAPTURE", True)
    p = tmp_path / "cap.jsonl"
    now = datetime(2026, 5, 17, 16, 0, tzinfo=UTC)
    orc.capture((53.15, 23.15), [_order("b1", "picked_up"), _order("b2")],
                _order("new"), now, 1.0, 3.0, "std+", "new", path=str(p))
    lines = p.read_text().strip().splitlines()
    assert len(lines) == 1
    rec = json.loads(lines[0])
    assert rec["order_id"] == "new"
    assert rec["tier"] == "std+"
    assert rec["courier_pos"] == [53.15, 23.15]
    assert rec["dwell_pickup"] == 1.0 and rec["dwell_dropoff"] == 3.0
    assert len(rec["bag"]) == 2
    assert rec["new_order"]["order_id"] == "new"
    b1 = next(o for o in rec["bag"] if o["order_id"] == "b1")
    assert b1["status"] == "picked_up"
    assert b1["picked_up_at"] is not None
    assert b1["czas_kuriera_warsaw"] == "2026-05-17T18:30:00+02:00"
    assert b1["delivery_coords"] == [53.20, 23.20]


def test_capture_appends_multiple(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "ENABLE_OBJ_REPLAY_CAPTURE", True)
    p = tmp_path / "cap.jsonl"
    now = datetime(2026, 5, 17, 16, 0, tzinfo=UTC)
    for i in range(3):
        orc.capture((53.1, 23.1), [], _order(f"n{i}"), now, 1.0, 3.0,
                    "std", f"n{i}", path=str(p))
    assert len(p.read_text().strip().splitlines()) == 3


def test_capture_failsafe_on_bad_input(tmp_path, monkeypatch):
    monkeypatch.setattr(C, "ENABLE_OBJ_REPLAY_CAPTURE", True)
    p = tmp_path / "cap.jsonl"
    # new_order=None + now=string — capture NIE może rzucić wyjątkiem do callera
    orc.capture((53.1, 23.1), [], None, "not-a-datetime", 1.0, 3.0,
                "std", "x", path=str(p))  # brak wyjątku = pass


def test_capture_roundtrip_via_harness_loader(tmp_path, monkeypatch):
    """Capture → obj_harness._ordersim_from_capture → OrderSim wierny."""
    monkeypatch.setattr(C, "ENABLE_OBJ_REPLAY_CAPTURE", True)
    p = tmp_path / "cap.jsonl"
    now = datetime(2026, 5, 17, 16, 0, tzinfo=UTC)
    orc.capture((53.15, 23.15), [_order("b1", "picked_up")], _order("new"),
                now, 1.0, 3.0, "std", "new", path=str(p))
    import sys
    sys.path.insert(0, "/root/.openclaw/workspace/scripts/dispatch_v2/tools")
    import obj_harness
    recs = obj_harness.load_capture(str(p))
    assert len(recs) == 1
    sim = obj_harness._ordersim_from_capture(recs[0]["new_order"])
    assert sim.order_id == "new"
    assert sim.delivery_coords == (53.20, 23.20)
    assert sim.czas_kuriera_warsaw == "2026-05-17T18:30:00+02:00"


if __name__ == "__main__":
    import sys
    import pytest
    sys.exit(pytest.main([__file__, "-v"]))
