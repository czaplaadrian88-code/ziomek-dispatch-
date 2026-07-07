"""T2.4 inkrement 2 — mode_observer (shadow-only would-be-mode).

Testuje: derywacja sygnałów z orders_state (L/queue/latency), persystencja FSM,
log would-be-mode. Ścieżki przez tmp (NIGDY prod state — landmine „testy piszą do PROD").
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone

from dispatch_v2 import mode_layer as M
from dispatch_v2.tools import mode_observer as OBS


def test_signals_from_state():
    now = datetime(2026, 5, 16, 14, 0, tzinfo=timezone.utc)
    orders = {}
    # 12 in-flight na 2 kurierów → L=6; 3 assigned w oknie z latencją 8 min
    for i in range(12):
        cid = "c1" if i < 6 else "c2"
        orders[str(i)] = {"status": "assigned" if i % 2 else "picked_up",
                          "courier_id": cid,
                          "assigned_at": (now - timedelta(minutes=5)).isoformat(),
                          "created_at_utc": (now - timedelta(minutes=13)).isoformat()}
    sig = M.mode_signals_from_state(orders, now, pending_count=11)
    assert sig.load_inflight_per_active == 6.0
    assert sig.queue_pending == 11
    assert sig.assign_latency_med_min == 8.0
    assert sig.defers_and_reassigns == 99  # brak capitulation fałszywej


def test_observer_persists_and_logs(tmp_path):
    now = datetime(2026, 5, 16, 14, 0, tzinfo=timezone.utc)
    orders = {str(i): {"status": "assigned", "courier_id": f"c{i % 2}",
                       "assigned_at": (now - timedelta(minutes=3)).isoformat(),
                       "created_at_utc": (now - timedelta(minutes=9)).isoformat()}
              for i in range(14)}
    op = tmp_path / "orders.json"
    op.write_text(json.dumps(orders), encoding="utf-8")
    pp = tmp_path / "pending.json"
    pp.write_text(json.dumps(list(range(12))), encoding="utf-8")
    sp = tmp_path / "state.json"
    lp = tmp_path / "log.jsonl"
    # 1. pomiar
    r1 = OBS.observe_once(now, str(op), str(pp), str(sp), str(lp))
    assert r1["signals"]["L"] == 7.0 and r1["signals"]["queue"] == 12
    assert sp.exists() and lp.exists()
    # 2. pomiar 12 min później — sustain 2-z-3 → S2 (persystencja stanu)
    r2 = OBS.observe_once(now + timedelta(minutes=12), str(op), str(pp), str(sp), str(lp))
    assert r2["mode"] == M.S2
    # log ma 2 wpisy
    lines = [json.loads(x) for x in lp.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == 2
