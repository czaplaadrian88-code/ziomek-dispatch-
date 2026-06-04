"""STATE-RMW-02 — testy prune_terminal_orders.

Izolacja OBOWIĄZKOWA: DISPATCH_STATE_DIR=tmp (state_machine._state_path raises
pod pytest jeśli zwrócona ścieżka produkcyjna — Lekcja #75, incydent 2026-05-18).
"""
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from dispatch_v2 import state_machine as sm


def _iso(hours_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(hours=hours_ago)).isoformat()


def _rec(status, hours_ago=None, **extra):
    r = {"status": status, **extra}
    if hours_ago is not None:
        r["updated_at"] = _iso(hours_ago)
    return r


@pytest.fixture
def state_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("DISPATCH_STATE_DIR", str(tmp_path))
    return tmp_path


def _write(state_dir: Path, state: dict):
    (state_dir / "orders_state.json").write_text(json.dumps(state), encoding="utf-8")


def _read(state_dir: Path) -> dict:
    return json.loads((state_dir / "orders_state.json").read_text(encoding="utf-8"))


def test_prunes_old_terminal_keeps_recent_and_active(state_dir):
    _write(state_dir, {
        "old1": _rec("delivered", hours_ago=20),
        "old2": _rec("delivered", hours_ago=48),
        "ret":  _rec("returned_to_pool", hours_ago=30),
        "canc": _rec("cancelled", hours_ago=15),
        "recent": _rec("delivered", hours_ago=2),     # terminalne ale świeże → zostaje
        "active_assigned": _rec("assigned", hours_ago=50),   # aktywne → ZOSTAJE mimo wieku
        "active_picked": _rec("picked_up", hours_ago=99),
    })
    rep = sm.prune_terminal_orders(retention_hours=12, dry_run=False)
    assert rep["pruned_count"] == 4          # old1/old2/ret/canc
    assert rep["active_count"] == 2
    after = _read(state_dir)
    assert set(after.keys()) == {"recent", "active_assigned", "active_picked"}


def test_active_never_pruned_even_when_oldest(state_dir):
    _write(state_dir, {
        "a": _rec("assigned", hours_ago=1000),
        "p": _rec("picked_up", hours_ago=1000),
        "pl": _rec("planned", hours_ago=1000),
    })
    rep = sm.prune_terminal_orders(retention_hours=1, dry_run=False)
    assert rep["pruned_count"] == 0
    assert rep["active_count"] == 3
    assert set(_read(state_dir).keys()) == {"a", "p", "pl"}


def test_dry_run_does_not_write(state_dir):
    _write(state_dir, {
        "old1": _rec("delivered", hours_ago=20),
        "old2": _rec("delivered", hours_ago=99),
        "act":  _rec("assigned", hours_ago=1),
    })
    before = _read(state_dir)
    rep = sm.prune_terminal_orders(retention_hours=12, dry_run=True)
    assert rep["dry_run"] is True
    assert rep["pruned_count"] == 2
    assert _read(state_dir) == before          # ZERO zapisu
    assert not (state_dir / "orders_state.json.prev").exists()  # brak .prev = nie pisał


def test_skips_terminal_without_updated_at(state_dir):
    _write(state_dir, {
        "no_ts": {"status": "delivered"},               # brak updated_at
        "bad_ts": {"status": "delivered", "updated_at": "nonsense"},
        "good": _rec("delivered", hours_ago=99),
    })
    rep = sm.prune_terminal_orders(retention_hours=12, dry_run=False)
    assert rep["skipped_no_updated_at"] == 2
    assert rep["pruned_count"] == 1
    assert set(_read(state_dir).keys()) == {"no_ts", "bad_ts"}


def test_no_candidates_no_write(state_dir):
    _write(state_dir, {
        "r1": _rec("delivered", hours_ago=2),
        "r2": _rec("cancelled", hours_ago=5),
    })
    before = _read(state_dir)
    rep = sm.prune_terminal_orders(retention_hours=12, dry_run=False)
    assert rep["pruned_count"] == 0
    assert _read(state_dir) == before
    assert not (state_dir / "orders_state.json.prev").exists()


def test_all_three_terminal_statuses_pruned(state_dir):
    _write(state_dir, {
        "d": _rec("delivered", hours_ago=20),
        "c": _rec("cancelled", hours_ago=20),
        "r": _rec("returned_to_pool", hours_ago=20),
        "a": _rec("assigned", hours_ago=20),
    })
    rep = sm.prune_terminal_orders(retention_hours=12, dry_run=False)
    assert rep["pruned_count"] == 3
    assert set(_read(state_dir).keys()) == {"a"}


def test_report_shape(state_dir):
    _write(state_dir, {"d": _rec("delivered", hours_ago=99), "a": _rec("assigned", hours_ago=1)})
    rep = sm.prune_terminal_orders(retention_hours=12, dry_run=True)
    for k in ("old_count", "active_count", "pruned_count", "new_count",
              "retention_hours", "dry_run", "skipped_no_updated_at", "sample"):
        assert k in rep
    assert rep["old_count"] == 2
    assert rep["new_count"] == rep["old_count"] - rep["pruned_count"]
