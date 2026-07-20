"""Faza 2 Etap 3b — przydział paczki do orders_state (NIE gastro)."""
from dispatch_v2 import parcel_assign as pa


def test_assign_parcel_ok(monkeypatch):
    monkeypatch.setattr(pa, "_resolve_cid", lambda n: 536)
    monkeypatch.setattr(pa.state_machine, "get_all",
                        lambda: {"900138096": {"source": "parcel", "status": "planned"}})
    calls = []

    def durable_ok(event_type, **kwargs):
        calls.append((event_type, kwargs))
        return pa.durable_event_apply.DurableApplyOutcome(
            event_id="parcel-event-v1",
            event_key=kwargs["event_key"],
            event_created=True,
            state_ready=True,
            state_transitioned=True,
            downstream_executed=True,
        )

    monkeypatch.setattr(pa.durable_event_apply, "emit_and_apply", durable_ok)
    ok, msg = pa.assign_parcel("900138096", "Szymon P", "15")
    assert ok and "PARCEL_ASSIGN_OK" in msg and "cid=536" in msg
    event_type, kwargs = calls[0]
    assert event_type == "COURIER_ASSIGNED"
    assert kwargs["courier_id"] == "536" and kwargs["order_id"] == "900138096"
    assert kwargs["event_key"] == "900138096_COURIER_ASSIGNED_536_canonical"
    assert kwargs["emit_fn"] is pa.event_bus.emit_audit


def test_assign_parcel_reports_durable_pending(monkeypatch):
    monkeypatch.setattr(pa, "_resolve_cid", lambda _name: 536)
    monkeypatch.setattr(
        pa.state_machine,
        "get_all",
        lambda: {"900138096": {"source": "parcel", "status": "planned"}},
    )
    monkeypatch.setattr(
        pa.durable_event_apply,
        "emit_and_apply",
        lambda _event_type, **kwargs: pa.durable_event_apply.DurableApplyOutcome(
            event_id="parcel-event-v1",
            event_key=kwargs["event_key"],
            event_created=True,
            state_ready=False,
            state_transitioned=False,
            downstream_executed=False,
            failure_stage="state_apply",
        ),
    )

    ok, msg = pa.assign_parcel("900138096", "Szymon P")

    assert ok is False
    assert "utrwalony do retry" in msg
    assert "state_apply" in msg


def test_assign_parcel_unknown_courier(monkeypatch):
    monkeypatch.setattr(pa, "_resolve_cid", lambda n: None)
    ok, msg = pa.assign_parcel("900138096", "Nieznany")
    assert not ok and "nie znaleziono kuriera" in msg


def test_assign_parcel_not_in_state(monkeypatch):
    monkeypatch.setattr(pa, "_resolve_cid", lambda n: 536)
    monkeypatch.setattr(pa.state_machine, "get_all", lambda: {})
    ok, msg = pa.assign_parcel("900138096", "Szymon P")
    assert not ok and "nie ma w orders_state" in msg


def test_assign_parcel_refuses_non_parcel(monkeypatch):
    """Bezpiecznik: nie tknij zwykłego zlecenia gastro tą ścieżką."""
    monkeypatch.setattr(pa, "_resolve_cid", lambda n: 536)
    monkeypatch.setattr(pa.state_machine, "get_all",
                        lambda: {"484000": {"status": "planned"}})  # brak source=parcel
    ok, msg = pa.assign_parcel("484000", "Szymon P")
    assert not ok and "nie jest paczką" in msg


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
