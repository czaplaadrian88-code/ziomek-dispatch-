"""G2b mierzy auto-routing tylko wtedy, gdy egzekutor AUTO jest aktywny."""

from datetime import datetime, timezone
import json

import pytest

from dispatch_v2.tools import objm_lexr6_canary_monitor as M


NOW = datetime(2026, 8, 2, 10, 31, tzinfo=timezone.utc)
SINCE = datetime(2026, 8, 2, 8, 31, tzinfo=timezone.utc)
BASE = {"koord_pct": 5.8, "ack_alert_pct": 89.1, "lat_p95": 1892.0}
CUR = {
    "n": 36,
    "n_sel": 36,
    "n_orders": 36,
    "koord_pct": 0.0,
    "koord_pct_sel": 0.0,
    "koord_eb": 0,
    "ack_alert_pct": 100.0,
    "auto_pct": 0.0,
    "lat_p50": 900.0,
    "lat_p95": 1556.0,
}
LOG = {
    "errors": 0,
    "reorders": 4,
    "reorder_oids": set(),
    "reorder_events": {},
}


def _gate(gates, name):
    return next(gate for gate in gates if gate[0] == name)


def _gates(auto_assign_on):
    flags = {
        "select_on": True,
        "shadow_on": False,
        "auto_assign_on": auto_assign_on,
    }
    return M.gates(CUR, LOG, flags, BASE, SINCE, NOW)


@pytest.mark.parametrize("value", [False, True])
def test_flag_state_reads_explicit_auto_assign_value(tmp_path, monkeypatch, value):
    flags_path = tmp_path / "flags.json"
    flags_path.write_text(json.dumps({"ENABLE_AUTO_ASSIGN": value}), encoding="utf-8")
    monkeypatch.setattr(M, "FLAGS", str(flags_path))

    assert M.flag_state()["auto_assign_on"] is value


@pytest.mark.parametrize("payload", [{}, {"ENABLE_AUTO_ASSIGN": "false"}])
def test_flag_state_does_not_invent_auto_assign_default(tmp_path, monkeypatch, payload):
    flags_path = tmp_path / "flags.json"
    flags_path.write_text(json.dumps(payload), encoding="utf-8")
    monkeypatch.setattr(M, "FLAGS", str(flags_path))

    assert M.flag_state()["auto_assign_on"] is None


def test_g2b_on_neq_off_and_other_gates_are_identical():
    gates_on = _gates(True)
    gates_off = _gates(False)

    assert _gate(gates_on, "G2b-auto-route")[1] == "STOP"
    assert _gate(gates_off, "G2b-auto-route") == (
        "G2b-auto-route",
        "INFO",
        "N/A (auto-assign OFF)",
    )
    assert gates_on != gates_off
    assert [gate for gate in gates_on if gate[0] != "G2b-auto-route"] == [
        gate for gate in gates_off if gate[0] != "G2b-auto-route"
    ]


def test_unknown_auto_assign_state_preserves_legacy_g2b_stop():
    assert _gate(_gates(None), "G2b-auto-route") == _gate(
        _gates(True), "G2b-auto-route"
    )
