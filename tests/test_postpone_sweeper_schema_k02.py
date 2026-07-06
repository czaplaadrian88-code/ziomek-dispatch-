"""K02 (program refaktoru, 2026-07-06) — postpone_sweeper: schema-mismatch.

Bug (deep-audit 27.06 #1.8, zweryfikowany na HEAD): `_read_state()` zwraca
PŁASKI dict {oid: rec} (state_machine.py ~331), a pole kuriera to "courier_id"
(żywy orders_state, sample: courier_id='520'). Stary odczyt
`orders_state.get("orders", {}).get(oid)` + `current.get("cid")` NIGDY nie
znajdował rekordu → gałąź POSTPONE_RESOLVED strukturalnie nieosiągalna →
przy re-enable postpone sweeper wpisywałby DUPLIKAT propozycji dla
żywo-przypisanego zlecenia.

Testy:
- charakteryzujący (zielony PRZED i PO fixie): pusty postponed → pełny no-op
  (dzisiejsze zachowanie live, bo postponed_proposals.json = {}),
- odtwarzający bug (CZERWONY przed fixem, zielony po): przypisane zlecenie
  musi dać POSTPONE_RESOLVED i NIE wołać assess_order,
- koordynator id=26 = nieprzypisane (sentinel zachowany).

Izolacja ścieżek PROD (C17/#10): POSTPONED_PATH i PENDING_PROPOSALS_PATH
monkeypatchowane na tmp_path; telegram wyciszony; state_machine._read_state
podmieniony — test nie dotyka żadnego żywego pliku.
"""
import json
from datetime import datetime, timedelta, timezone

import pytest

import dispatch_v2.postpone_sweeper as ps
from dispatch_v2 import dispatch_pipeline as dp_mod  # K09: cel mocka assess_order (fasada core.decide)


def _entry(now, count=0, expired_min=5, oid="484999"):
    return {
        "postponed_until": (now - timedelta(minutes=expired_min)).isoformat(),
        "postpone_count": count,
        "decision_record": {"order_event": {"order_id": oid}},
    }


@pytest.fixture
def iso(tmp_path, monkeypatch):
    post = tmp_path / "postponed_proposals.json"
    pend = tmp_path / "pending_proposals.json"
    monkeypatch.setattr(ps, "POSTPONED_PATH", str(post))
    monkeypatch.setattr(ps, "PENDING_PROPOSALS_PATH", str(pend))
    monkeypatch.setattr(ps.telegram_utils, "send_admin_alert", lambda *a, **k: None)
    return post, pend


def test_pusty_postponed_pelny_noop(iso):
    """Charakteryzujący: dzisiejsze zachowanie LIVE (postponed={}) = no-op."""
    stats = ps.run_once(now=datetime.now(timezone.utc))
    assert stats == {
        "checked": 0, "resolved": 0, "escalated": 0,
        "reemitted": 0, "skipped": 0, "errors": 0,
    }


def test_przypisane_zlecenie_daje_resolved_bez_reemitu(iso, monkeypatch):
    """ODTWARZAJĄCY BUG: płaski stan + courier_id → musi być RESOLVED.

    Na kodzie sprzed K02: resolved=0 (gałąź nieosiągalna), assess_order
    wołany dla żywo-przypisanego zlecenia (przygotowanie duplikatu propozycji).
    """
    now = datetime.now(timezone.utc)
    post, _ = iso
    post.write_text(json.dumps({"484999": _entry(now)}))

    monkeypatch.setattr(
        ps.state_machine, "_read_state",
        lambda: {"484999": {"order_id": "484999", "courier_id": "123"}},
    )
    assess_called = {}
    monkeypatch.setattr(
        dp_mod, "assess_order",
        lambda *a, **k: assess_called.setdefault("hit", True),
    )
    monkeypatch.setattr(ps.courier_resolver, "dispatchable_fleet", lambda *a, **k: {})

    stats = ps.run_once(now=now)

    assert stats["resolved"] == 1, "przypisane zlecenie MUSI dać POSTPONE_RESOLVED"
    assert "hit" not in assess_called, "assess_order NIE może być wołany dla przypisanego"
    assert json.loads(post.read_text()) == {}, "wpis musi zniknąć z postponed"


def test_koordynator_26_traktowany_jako_nieprzypisane(iso, monkeypatch):
    """Sentinel koordynatora (id_kurier=26) = worek trzymający, NIE przypisanie.

    Zlecenie u '26' z licznikiem >= MAX_POSTPONE_COUNT idzie w eskalację
    (nie w resolved) — zachowanie sentinela z oryginalnego kodu utrwalone.
    """
    now = datetime.now(timezone.utc)
    post, _ = iso
    post.write_text(json.dumps({"485000": _entry(now, count=ps.MAX_POSTPONE_COUNT, oid="485000")}))

    monkeypatch.setattr(
        ps.state_machine, "_read_state",
        lambda: {"485000": {"order_id": "485000", "courier_id": "26"}},
    )
    monkeypatch.setattr(dp_mod, "assess_order", lambda *a, **k: None)
    monkeypatch.setattr(ps.courier_resolver, "dispatchable_fleet", lambda *a, **k: {})

    stats = ps.run_once(now=now)

    assert stats["resolved"] == 0
    assert stats["escalated"] == 1
    assert json.loads(post.read_text()) == {}
