"""DELIVERED RESURRECTION (2026-06-29, case Pizzeria 105 cid 492).

ORDER_RESURRECTED: cofnięcie z 'delivered' do aktywnego — koordynator RĘCZNIE przywrócił status
w gastro po błędnym 'doręczone' z apki (skok 6→7). Świadomie bypassuje Path-B terminal-preserve.
Flaga ENABLE_DELIVERED_RESURRECTION gatuje DETEKCJĘ w panel_watcher (panel-integration; tu
testujemy rdzeń eventu + rejestrację flagi).
"""
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

_TMP_DIR = tempfile.mkdtemp(prefix="resurrect_test_")
os.environ["DISPATCH_STATE_DIR"] = _TMP_DIR

from dispatch_v2 import state_machine  # noqa: E402
from dispatch_v2 import common as C    # noqa: E402


def _resurrect(order_id, new_status, courier_id):
    return state_machine.update_from_event({
        "event_type": "ORDER_RESURRECTED",
        "event_id": f"{order_id}_ORDER_RESURRECTED_{new_status}_test",
        "order_id": order_id,
        "courier_id": courier_id,
        "payload": {
            "new_status": new_status,
            "reason": "panel_status_restored",
            "source": "panel_status_restored",
        },
    })


def _reset():
    p = state_machine._state_path()
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "w") as f:
        f.write("{}")


def test_resurrect_delivered_to_active():
    _reset()
    state_machine.upsert_order(
        "999",
        {"status": "delivered", "courier_id": "492",
         "delivered_at": "2026-06-29T12:13:55+00:00", "final_location": "X"},
        event="COURIER_DELIVERED")
    out = _resurrect("999", "picked_up", "492")
    assert out is not None
    o = state_machine.get_order("999")
    assert o["status"] == "picked_up"      # wskrzeszone z delivered
    assert o["delivered_at"] is None       # czyszczone (znów aktywne)
    assert o["final_location"] is None
    assert o["courier_id"] == "492"
    # ślad w historii
    assert any(h.get("event") == "ORDER_RESURRECTED" for h in o.get("history", []))


def test_resurrect_noop_when_not_delivered():
    _reset()
    state_machine.upsert_order("998", {"status": "picked_up", "courier_id": "492"},
                               event="COURIER_PICKED_UP")
    assert _resurrect("998", "picked_up", "492") is None  # no-op
    assert state_machine.get_order("998")["status"] == "picked_up"


def test_ENABLE_DELIVERED_RESURRECTION_registered_off():
    # Flaga gatuje detekcję w panel_watcher; stała-fallback OFF (KANON=flags.json).
    assert C.ENABLE_DELIVERED_RESURRECTION is False
    assert "ENABLE_DELIVERED_RESURRECTION" in C.ETAP4_DECISION_FLAGS
