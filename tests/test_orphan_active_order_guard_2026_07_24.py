import json

import pytest

from dispatch_v2 import state_machine as sm
from dispatch_v2.observability import data_alerts


def test_courier_assigned_cannot_create_missing_order(tmp_path, monkeypatch):
    """RED oracle: lifecycle assignment may update, never materialize an order."""
    state_path = tmp_path / "orders_state.json"
    state_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(sm, "_state_path", lambda: str(state_path))
    event = {
        "event_type": "COURIER_ASSIGNED",
        "event_id": "900138096_COURIER_ASSIGNED_536_canonical_mutation",
        "order_id": "900138096",
        "courier_id": "536",
        "payload": {"source": "parcel_assign"},
    }

    with pytest.raises(sm.MissingOrderPreconditionError):
        sm.update_from_event(event)

    assert json.loads(state_path.read_text(encoding="utf-8")) == {}


def test_active_order_schema_sensor_detects_incident_shape():
    orders = {
        "900138096": {
            "status": "assigned",
            "courier_id": "536",
            "assigned_at": "2026-07-22T18:47:51+00:00",
            "history": [{"event": "COURIER_ASSIGNED"}],
        },
        "489844": {
            "status": "assigned",
            "courier_id": "500",
            "commitment_level": 1,
            "restaurant": "fixture",
            "first_seen": "2026-07-24T10:00:00+00:00",
            "delivery_address": "fixture",
            "history": [
                {"event": "NEW_ORDER"},
                {"event": "COURIER_ASSIGNED"},
            ],
        },
    }

    signal = data_alerts.evaluate_active_order_schema(orders)

    assert signal.firing is True
    assert signal.value == 1.0
    assert signal.sample == 2
    assert "900138096" in signal.detail


def test_active_order_schema_sensor_mutation_oracle():
    valid = {
        "status": "assigned",
        "commitment_level": 1,
        "restaurant": "fixture",
        "first_seen": "2026-07-24T10:00:00+00:00",
        "delivery_address": "fixture",
        "history": [{"event": "NEW_ORDER"}],
    }
    broken = dict(valid)
    broken.pop("first_seen")

    assert data_alerts.evaluate_active_order_schema({"1": valid}).firing is False
    assert data_alerts.evaluate_active_order_schema({"1": broken}).firing is True
