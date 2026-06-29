"""Faza 2 Etap 3b — przydział paczki do orders_state (NIE gastro)."""
from dispatch_v2 import parcel_assign as pa


def test_assign_parcel_ok(monkeypatch):
    monkeypatch.setattr(pa, "_resolve_cid", lambda n: 536)
    monkeypatch.setattr(pa.state_machine, "get_all",
                        lambda: {"900138096": {"source": "parcel", "status": "planned"}})
    emitted, updated = [], []
    monkeypatch.setattr(pa.event_bus, "emit",
                        lambda *a, **k: emitted.append(k.get("event_id")) or "e")
    monkeypatch.setattr(pa.state_machine, "update_from_event", lambda ev: updated.append(ev))
    ok, msg = pa.assign_parcel("900138096", "Szymon P", "15")
    assert ok and "PARCEL_ASSIGN_OK" in msg and "cid=536" in msg
    assert updated[0]["event_type"] == "COURIER_ASSIGNED"
    assert updated[0]["courier_id"] == "536" and updated[0]["order_id"] == "900138096"
    assert emitted == ["900138096_COURIER_ASSIGNED_parcel_536"]


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
