"""Faza 2 Etap 3 — merger paczek do orders_state + strażnik watchera (pomija source=parcel).

Flaga ON≠OFF, create/keep/retire, oraz dowód że watcher NIE prefetchuje paczek (twin gastro
nietknięty). Izolowane (monkeypatch state_machine/flag/snapshot), bez sieci/plików.
"""
from dispatch_v2 import panel_watcher as pw
from dispatch_v2 import parcel_lane_merge as plm

_PARCEL = {"order_id": 900000005, "source": "parcel", "status": "planned",
           "pickup_coords": [53.13, 23.16], "delivery_coords": [53.12, 23.14]}


# ── merger ────────────────────────────────────────────────────────────────
def test_merge_flag_off_noop(monkeypatch):
    monkeypatch.setattr(plm.C, "flag", lambda n, d=False: False)
    assert plm.run() == {"enabled": False}


def test_merge_stale_or_missing_snapshot(monkeypatch):
    monkeypatch.setattr(plm.C, "flag", lambda n, d=False: True)
    monkeypatch.setattr(plm, "_apply_status_inbox", lambda: 0)  # osobny test; chroni guard _state_path
    monkeypatch.setattr(plm, "_load_snapshot", lambda: None)
    assert plm.run() == {"enabled": True, "snapshot": "missing_or_stale", "status_applied": 0}


def test_merge_creates_new_parcel(monkeypatch):
    monkeypatch.setattr(plm.C, "flag", lambda n, d=False: True)
    monkeypatch.setattr(plm, "_apply_status_inbox", lambda: 0)  # osobny test; chroni guard _state_path
    monkeypatch.setattr(plm, "_load_snapshot", lambda: {"900000005": _PARCEL})
    monkeypatch.setattr(plm.sm, "get_all", lambda: {})
    created = []
    monkeypatch.setattr(plm.sm, "upsert_order", lambda oid, e, event=None: created.append((oid, event)))
    monkeypatch.setattr(plm.sm, "set_status", lambda *a, **k: None)
    emitted = []
    monkeypatch.setattr(plm.event_bus, "emit",
                        lambda et, order_id=None, payload=None, event_id=None: emitted.append((et, order_id, event_id)) or event_id)
    stats = plm.run()
    assert stats["created"] == 1 and created == [("900000005", "PARCEL_LANE_NEW")]
    # NEW_ORDER wyemitowany → shadow_dispatcher zaproponuje paczkę
    assert stats["emitted"] == 1
    assert emitted == [("NEW_ORDER", "900000005", "900000005_NEW_ORDER_parcel")]


def test_merge_keeps_existing_no_clobber(monkeypatch):
    """Paczka już w stanie (silnik mógł dodać courier_id) → NIE re-upsert."""
    monkeypatch.setattr(plm.C, "flag", lambda n, d=False: True)
    monkeypatch.setattr(plm, "_apply_status_inbox", lambda: 0)  # osobny test; chroni guard _state_path
    monkeypatch.setattr(plm, "_load_snapshot", lambda: {"900000005": _PARCEL})
    monkeypatch.setattr(plm.sm, "get_all",
                        lambda: {"900000005": {"source": "parcel", "status": "assigned", "courier_id": 7}})
    upserts = []
    monkeypatch.setattr(plm.sm, "upsert_order", lambda *a, **k: upserts.append(a))
    monkeypatch.setattr(plm.sm, "set_status", lambda *a, **k: None)
    monkeypatch.setattr(plm.event_bus, "emit", lambda *a, **k: None)  # już wyemitowany wcześniej
    stats = plm.run()
    assert stats["kept"] == 1 and stats["created"] == 0 and upserts == []


def test_merge_retires_gone_parcel(monkeypatch):
    """Paczka zniknęła ze snapshotu (anulowana/usunięta) → terminalna (sprzątanie)."""
    monkeypatch.setattr(plm.C, "flag", lambda n, d=False: True)
    monkeypatch.setattr(plm, "_apply_status_inbox", lambda: 0)  # osobny test; chroni guard _state_path
    monkeypatch.setattr(plm, "_load_snapshot", lambda: {})
    monkeypatch.setattr(plm.sm, "get_all",
                        lambda: {"900000005": {"source": "parcel", "status": "planned"}})
    monkeypatch.setattr(plm.sm, "upsert_order", lambda *a, **k: None)
    retired = []
    monkeypatch.setattr(plm.sm, "set_status", lambda oid, st, event=None: retired.append((oid, st)))
    stats = plm.run()
    assert stats["retired"] == 1 and retired == [("900000005", "cancelled")]


def test_merge_leaves_gastro_alone(monkeypatch):
    """Sprzątanie dotyka TYLKO source=parcel — gastro w stanie nietknięte."""
    monkeypatch.setattr(plm.C, "flag", lambda n, d=False: True)
    monkeypatch.setattr(plm, "_apply_status_inbox", lambda: 0)  # osobny test; chroni guard _state_path
    monkeypatch.setattr(plm, "_load_snapshot", lambda: {})
    monkeypatch.setattr(plm.sm, "get_all",
                        lambda: {"484000": {"status": "planned"}})  # gastro, brak source
    monkeypatch.setattr(plm.sm, "upsert_order", lambda *a, **k: None)
    touched = []
    monkeypatch.setattr(plm.sm, "set_status", lambda oid, st, event=None: touched.append(oid))
    stats = plm.run()
    assert stats["retired"] == 0 and touched == []


# ── strażnik watchera ───────────────────────────────────────────────────────
def test_watcher_prefetch_skips_parcels():
    """_build_prefetch_candidates POMIJA source=parcel, ale gastro spoza HTML NADAL bierze."""
    parsed = {"order_ids": ["111"], "assigned_ids": set()}
    state = {
        "900000005": {"status": "planned", "source": "parcel"},  # paczka → pominąć
        "222": {"status": "planned"},                            # gastro spoza HTML → prefetch
    }
    out = pw._build_prefetch_candidates(parsed, state, set(), False, False, False)
    assert "900000005" not in out      # strażnik działa
    assert "222" in out                # twin gastro nietknięty


# ── Etap 3c: inbox statusów z apki → orders_state ──────────────────────────
def test_apply_status_inbox(monkeypatch, tmp_path):
    """5=odebrane→PICKED_UP, 7=doręczone→DELIVERED, 3=ignorowane. Idempotent po event_id."""
    (tmp_path / "parcel_status_inbox.jsonl").write_text(
        '{"oid":"900138096","status_code":5,"cid":61,"ts":111}\n'
        '{"oid":"900138096","status_code":7,"cid":61,"ts":222}\n'
        '{"oid":"900138096","status_code":3,"cid":61,"ts":333}\n', encoding="utf-8")
    monkeypatch.setattr(plm.sm, "_state_path", lambda: str(tmp_path / "orders_state.json"))
    emitted, applied = [], []
    monkeypatch.setattr(plm.event_bus, "emit",
                        lambda et, order_id=None, courier_id=None, event_id=None:
                        emitted.append((et, event_id)) or event_id)
    monkeypatch.setattr(plm.sm, "update_from_event", lambda ev: applied.append(ev))
    assert plm._apply_status_inbox() == 2          # 5+7; 3 pominięte
    assert [ev["event_type"] for ev in applied] == [
        "COURIER_PICKED_UP", "COURIER_DELIVERED"
    ]
    assert all(ev["payload"] == {"source": "parcel_status_inbox"} for ev in applied)
    # Faza A tylko ujawnia brak kontraktu czasu; nie zamienia e.ts na timestamp
    # bez decyzji o jednostce/semantyce ani nie przywraca fallbacku now().
    assert all("timestamp" not in ev["payload"] for ev in applied)
    assert ("COURIER_PICKED_UP", "900138096_COURIER_PICKED_UP_111") in emitted


def test_apply_status_inbox_idempotent(monkeypatch, tmp_path):
    """event_bus.emit zwraca None (już wyemitowane) → NIE aplikuj ponownie."""
    (tmp_path / "parcel_status_inbox.jsonl").write_text(
        '{"oid":"900138096","status_code":5,"cid":61,"ts":111}\n', encoding="utf-8")
    monkeypatch.setattr(plm.sm, "_state_path", lambda: str(tmp_path / "orders_state.json"))
    monkeypatch.setattr(plm.event_bus, "emit", lambda *a, **k: None)
    applied = []
    monkeypatch.setattr(plm.sm, "update_from_event", lambda ev: applied.append(ev))
    assert plm._apply_status_inbox() == 0 and applied == []


def test_apply_status_inbox_rotates_when_large(monkeypatch, tmp_path):
    """Po przetworzeniu, gdy inbox > próg → rotacja do .1 (świeży powstanie przy append)."""
    inbox = tmp_path / "parcel_status_inbox.jsonl"
    inbox.write_text('{"oid":"900000005","status_code":5,"cid":61,"ts":1}\n', encoding="utf-8")
    monkeypatch.setattr(plm.sm, "_state_path", lambda: str(tmp_path / "orders_state.json"))
    monkeypatch.setattr(plm.event_bus, "emit", lambda *a, **k: "e")
    monkeypatch.setattr(plm.sm, "update_from_event", lambda ev: None)
    monkeypatch.setattr(plm, "INBOX_MAX_BYTES", 0)   # wymuś rotację
    plm._apply_status_inbox()
    assert not inbox.exists()                         # zrotowany
    assert (tmp_path / "parcel_status_inbox.jsonl.1").exists()


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-q"]))
