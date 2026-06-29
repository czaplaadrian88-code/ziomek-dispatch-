"""FIX 2026-06-29 — load_plan pure-read (root cause oscylacji carried-first).

Dowód ON≠OFF: `load_plan(active_bag_oids=...)` przy niezgodności worka:
  - invalidate_on_mismatch=True  (legacy) → zwraca None I PERSYSTUJE invalidację
  - invalidate_on_mismatch=False (fix)    → zwraca None i NIE persystuje (plan żyje)

Race kontekst: pipeline-podgląd (`_soon_free_probe` / base_sequence) woła to
per-tick z workiem KANDYDATA; przy wyścigu z advance_plan po dostawie read
DARŁ cały plan (mylny ORDER_DELIVERED_ALL przy żywych stopach) → konsola mrugała
na carried-first. Pure-read kasuje to u źródła; autorytatywne unieważnienia
(advance_plan / panel_watcher BAG_CHANGED / plan_recheck) zostają nietknięte.
"""
import os
import sys
import tempfile
from pathlib import Path

import pytest

_HERE = os.path.dirname(os.path.abspath(__file__))
_SCRIPTS = os.path.abspath(os.path.join(_HERE, "..", ".."))
if _SCRIPTS not in sys.path:
    sys.path.insert(0, _SCRIPTS)

from dispatch_v2 import plan_manager as pm


@pytest.fixture
def isolated_pm(tmp_path, monkeypatch):
    monkeypatch.setattr(pm, "PLANS_FILE", tmp_path / "courier_plans.json")
    monkeypatch.setattr(pm, "LOCK_FILE", tmp_path / "courier_plans.lock")
    return pm


def _seed_plan(_pm, cid="999"):
    """Plan z dropoffami {A, B}; worek bieżący = {A} (B wypadł = mismatch)."""
    body = {
        "start_pos": {"lat": 53.13, "lng": 23.16, "source": "test", "source_ts": "2026-06-29T09:00:00+00:00"},
        "start_ts": "2026-06-29T09:00:00+00:00",
        "stops": [
            {"order_id": "A", "type": "pickup", "coords": {"lat": 53.13, "lng": 23.16}, "predicted_at": "2026-06-29T09:05:00+00:00"},
            {"order_id": "A", "type": "dropoff", "coords": {"lat": 53.14, "lng": 23.15}, "predicted_at": "2026-06-29T09:12:00+00:00"},
            {"order_id": "B", "type": "dropoff", "coords": {"lat": 53.15, "lng": 23.12}, "predicted_at": "2026-06-29T09:20:00+00:00"},
        ],
        "optimization_method": "incremental",
    }
    _pm.save_plan(cid, body)


def test_legacy_mismatch_persists_invalidation(isolated_pm):
    """OFF (legacy, invalidate_on_mismatch=True): mismatch → None + plan invalidated na dysku."""
    pm = isolated_pm
    _seed_plan(pm)
    out = pm.load_plan("999", active_bag_oids={"A"}, invalidate_on_mismatch=True)
    assert out is None                                   # plan nie do użycia dla tego worka
    raw = pm._read_raw()["999"]
    assert raw.get("invalidated_at") is not None         # PERSYSTUJE (stare zachowanie)
    assert raw.get("invalidation_reason") == "ORDER_DELIVERED_ALL"


def test_pure_read_mismatch_does_not_persist(isolated_pm):
    """ON (fix, invalidate_on_mismatch=False): mismatch → None ale plan ŻYJE (brak persystu)."""
    pm = isolated_pm
    _seed_plan(pm)
    out = pm.load_plan("999", active_bag_oids={"A"}, invalidate_on_mismatch=False)
    assert out is None                                   # nadal „nie używaj dla tego worka"
    raw = pm._read_raw()["999"]
    assert raw.get("invalidated_at") is None             # NIE persystuje (fix)
    assert raw.get("invalidation_reason") is None


def test_on_off_differ(isolated_pm):
    """Twardy dowód ON≠OFF na tym samym wejściu (różny stan persystencji)."""
    pm = isolated_pm
    _seed_plan(pm, cid="off")
    pm.load_plan("off", active_bag_oids={"A"}, invalidate_on_mismatch=True)
    off_state = pm._read_raw()["off"].get("invalidated_at")

    _seed_plan(pm, cid="on")
    pm.load_plan("on", active_bag_oids={"A"}, invalidate_on_mismatch=False)
    on_state = pm._read_raw()["on"].get("invalidated_at")

    assert off_state is not None and on_state is None    # ON≠OFF
    assert off_state != on_state


def test_matching_bag_returns_plan_both_modes(isolated_pm):
    """Regresja: gdy worek POKRYWA plan (brak mismatch) — zwraca plan w obu trybach, nic nie persystuje."""
    pm = isolated_pm
    for mode in (True, False):
        _seed_plan(pm, cid="ok")
        out = pm.load_plan("ok", active_bag_oids={"A", "B"}, invalidate_on_mismatch=mode)
        assert out is not None
        assert pm._read_raw()["ok"].get("invalidated_at") is None


def test_default_is_legacy(isolated_pm):
    """Default param = True (legacy) → zero zmiany zachowania gdy caller nie poda flagi."""
    pm = isolated_pm
    _seed_plan(pm)
    out = pm.load_plan("999", active_bag_oids={"A"})     # bez param
    assert out is None
    assert pm._read_raw()["999"].get("invalidated_at") is not None
