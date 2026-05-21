"""Testy dispatch_v2.pending_pool_sweeper (Faza 0 reconciliation + obserwacja)."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dispatch_v2 import pending_pool as pp
from dispatch_v2 import pending_pool_sweeper as sw


@pytest.fixture(autouse=True)
def _patch_paths(tmp_path: Path, monkeypatch):
    """Przekieruj ścieżki puli na tmp_path — izolacja od produkcyjnego dispatch_state."""
    monkeypatch.setattr(pp, "POOL_PATH", tmp_path / "pending_pool.json")
    monkeypatch.setattr(pp, "LOCK_PATH", tmp_path / "pending_pool.lock")
    monkeypatch.setattr(pp, "LOG_PATH", tmp_path / "pending_pool_log.jsonl")
    yield


def _fake_state(status_by_oid: dict):
    """Zwraca podmiankę state_machine.get_order opartą o dict {oid: status}."""
    def _get(oid):
        s = status_by_oid.get(str(oid))
        return {"status": s} if s is not None else None
    return _get


def _upsert(oid, created, pickup):
    pp.upsert_order(oid, created, pickup)


# ── reconciliation ──────────────────────────────────────────────────

@pytest.mark.parametrize("status,expect_reason", [
    ("assigned", "assigned_in_panel"),
    ("picked_up", "picked_up"),
    ("delivered", "delivered"),
    ("cancelled", "cancelled"),
    ("returned_to_pool", "returned_to_pool"),
])
def test_reconciliation_removes_resolved(status, expect_reason, monkeypatch):
    """Zlecenie ze statusem rozwiązanym → usunięte z puli z właściwym powodem."""
    _upsert("o1", "2026-05-17T10:00:00+00:00", "2026-05-17T10:50:00+00:00")
    monkeypatch.setattr(sw.state_machine, "get_order", _fake_state({"o1": status}))
    now = datetime(2026, 5, 17, 10, 30, tzinfo=timezone.utc)
    counts = sw.sweep(now=now)
    assert "o1" not in pp.load_pool()
    assert counts["removed"].get(expect_reason) == 1


def test_reconciliation_keeps_planned(monkeypatch):
    """Zlecenie status=planned (wciąż pending) → zostaje w puli."""
    _upsert("o2", "2026-05-17T10:00:00+00:00", "2026-05-17T10:50:00+00:00")
    monkeypatch.setattr(sw.state_machine, "get_order", _fake_state({"o2": "planned"}))
    now = datetime(2026, 5, 17, 10, 30, tzinfo=timezone.utc)
    sw.sweep(now=now)
    assert "o2" in pp.load_pool()


def test_reconciliation_keeps_unknown(monkeypatch):
    """Zlecenie nieobecne w state_machine (lag) → zostaje (stuck-guard złapie sieroty)."""
    _upsert("o3", "2026-05-17T10:00:00+00:00", "2026-05-17T10:50:00+00:00")
    monkeypatch.setattr(sw.state_machine, "get_order", _fake_state({}))
    now = datetime(2026, 5, 17, 10, 30, tzinfo=timezone.utc)
    sw.sweep(now=now)
    assert "o3" in pp.load_pool()


# ── stuck-guard (TYLKO sieroty) ─────────────────────────────────────

def test_stuck_removes_orphan_order(monkeypatch):
    """SIEROTA: brak rekordu w state_machine + >STUCK_AFTER_MIN po pickup → stuck."""
    _upsert("o4", "2026-05-17T10:00:00+00:00", "2026-05-17T10:50:00+00:00")
    monkeypatch.setattr(sw.state_machine, "get_order", _fake_state({}))  # sierota
    # pickup 10:50 + 45 min = 11:35 → now 11:40 jest po progu
    now = datetime(2026, 5, 17, 11, 40, tzinfo=timezone.utc)
    counts = sw.sweep(now=now)
    assert "o4" not in pp.load_pool()
    assert counts["stuck"] == 1


def test_planned_old_order_not_stuck(monkeypatch):
    """REGRESJA Gate 0: zlecenie `planned` dawno po nominalnym pickup (czasówka)
    NIE jest stuck — wciąż legalnie czeka, pula je poprawnie odzwierciedla."""
    _upsert("o4b", "2026-05-17T10:00:00+00:00", "2026-05-17T10:50:00+00:00")
    monkeypatch.setattr(sw.state_machine, "get_order", _fake_state({"o4b": "planned"}))
    # 50 min po nominalnym pickup_ready — w starym kodzie = stuck (bug)
    now = datetime(2026, 5, 17, 11, 40, tzinfo=timezone.utc)
    counts = sw.sweep(now=now)
    assert "o4b" in pp.load_pool()
    assert counts["stuck"] == 0


def test_stuck_not_triggered_before_threshold(monkeypatch):
    """Sierota tuż po pickup, przed progiem stuck → zostaje."""
    _upsert("o5", "2026-05-17T10:00:00+00:00", "2026-05-17T10:50:00+00:00")
    monkeypatch.setattr(sw.state_machine, "get_order", _fake_state({}))  # sierota
    now = datetime(2026, 5, 17, 11, 0, tzinfo=timezone.utc)  # 10 min po pickup
    counts = sw.sweep(now=now)
    assert "o5" in pp.load_pool()
    assert counts["stuck"] == 0


# ── obserwacja freeze-crossing ──────────────────────────────────────

def test_freeze_cross_logged_once_in_window(monkeypatch):
    """freeze_cross logowany gdy now w oknie [freeze_at, freeze_at+1.5min)."""
    _upsert("o6", "2026-05-17T10:00:00+00:00", "2026-05-17T11:00:00+00:00")
    monkeypatch.setattr(sw.state_machine, "get_order", _fake_state({"o6": "planned"}))
    fz = datetime.fromisoformat(pp.load_pool()["o6"]["freeze_at"])
    # freeze_at = pickup 11:00 − 15 min = 10:45
    counts = sw.sweep(now=fz + timedelta(seconds=30))
    assert counts["freeze_cross"] == 1
    assert "o6" in pp.load_pool()  # Faza 0: log only, NIC nie emituje, zlecenie zostaje


def test_freeze_cross_not_logged_outside_window(monkeypatch):
    """Poza oknem (długo po freeze_at) freeze_cross już się NIE loguje."""
    _upsert("o7", "2026-05-17T10:00:00+00:00", "2026-05-17T11:00:00+00:00")
    monkeypatch.setattr(sw.state_machine, "get_order", _fake_state({"o7": "planned"}))
    fz = datetime.fromisoformat(pp.load_pool()["o7"]["freeze_at"])
    counts = sw.sweep(now=fz + timedelta(minutes=10))
    assert counts["freeze_cross"] == 0
