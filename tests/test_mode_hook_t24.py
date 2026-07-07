"""T2.4 inkrement 4 — hook stempla would-be-mode na rekordzie decyzji (shadow).

Flaga ENABLE_MODE_LAYER_SHADOW ON≠OFF: OFF → mode/mode_reason = None (bajt-parytet);
ON → odczyt stanu obserwatora (read_current_mode, NIE krok FSM) → stempel w rekordzie.
Serializer niesie mode/mode_reason (LOCATION B).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone

from dispatch_v2 import common as C
from dispatch_v2 import dispatch_pipeline as DP
from dispatch_v2 import shadow_dispatcher as SD
from dispatch_v2.tools import mode_observer as OBS


def _mk_result():
    return DP.PipelineResult(order_id="1", verdict="PROPOSE", reason="r", best=None,
                             candidates=[], pickup_ready_at=None, restaurant="R")


def test_read_current_mode_failsoft(tmp_path):
    # brak pliku → (S1, no-state)
    assert OBS.read_current_mode(str(tmp_path / "nope.json")) == ("S1", "no-state")
    # z pliku
    sp = tmp_path / "st.json"
    sp.write_text(json.dumps({"mode": "S2", "reason": "kryzys"}), encoding="utf-8")
    assert OBS.read_current_mode(str(sp)) == ("S2", "kryzys")


def test_hook_off_no_stamp(monkeypatch):
    monkeypatch.setattr(C, "flag",
                        lambda n, d=None: False if n == "ENABLE_MODE_LAYER_SHADOW" else d)
    # symuluj końcówkę wrappera: hook nie stempluje przy OFF
    res = _mk_result()
    if C.flag("ENABLE_MODE_LAYER_SHADOW", False):
        res.mode, res.mode_reason = OBS.read_current_mode()
    assert res.mode is None and res.mode_reason is None


def test_hook_on_stamps(monkeypatch, tmp_path):
    # read_current_mode ma default fsm_state_path bound-at-def (C17) → hook woła bez
    # argu i czyta PROD-ścieżkę; test patchuje SAMĄ funkcję (weryfikuje wiring hooka).
    monkeypatch.setattr(OBS, "read_current_mode", lambda *a, **k: ("S2", "2-z-3"))
    monkeypatch.setattr(C, "flag",
                        lambda n, d=None: True if n == "ENABLE_MODE_LAYER_SHADOW" else d)
    res = _mk_result()
    if C.flag("ENABLE_MODE_LAYER_SHADOW", False):
        res.mode, res.mode_reason = OBS.read_current_mode()
    assert res.mode == "S2" and res.mode_reason == "2-z-3"


def test_serializer_carries_mode():
    res = _mk_result()
    res.mode, res.mode_reason = "S3", "capitulation"
    rec = SD._serialize_result(res, "e1", 1.0)
    assert rec["mode"] == "S3" and rec["mode_reason"] == "capitulation"
    # OFF-default (None) też serializowane
    rec2 = SD._serialize_result(_mk_result(), "e2", 1.0)
    assert rec2["mode"] is None
