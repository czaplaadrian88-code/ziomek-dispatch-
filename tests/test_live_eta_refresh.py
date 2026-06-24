"""A (2026-06-24): plan_recheck dosyła świeże ETA z aktualnego planu do
live_order_eta.json, żeby cache nie zamarzał między decyzjami Ziomka."""
import importlib
from pathlib import Path

from dispatch_v2 import plan_recheck
from dispatch_v2 import live_eta_cache


def _redirect_cache(tmp_path, monkeypatch):
    f = tmp_path / "live_order_eta.json"
    monkeypatch.setattr(live_eta_cache, "CACHE_FILE", Path(f))
    return f


def _plan(stops, invalidated=False):
    d = {"stops": stops}
    if invalidated:
        d["invalidated_at"] = "2026-06-24T19:00:00+00:00"
    return d


def test_refresh_extracts_delivery_and_pickup(tmp_path, monkeypatch):
    _redirect_cache(tmp_path, monkeypatch)
    monkeypatch.setattr(plan_recheck, "ENABLE_PLAN_RECHECK_LIVE_ETA_REFRESH", True)
    plans = {
        "370": _plan([
            {"type": "pickup", "order_id": "483135", "predicted_at": "2026-06-24T19:30:00+00:00"},
            {"type": "dropoff", "order_id": "483135", "predicted_at": "2026-06-24T19:36:00+00:00"},
        ]),
    }
    summary = {}
    plan_recheck._refresh_live_eta_from_plans(plans, summary)
    assert summary["live_eta_refreshed"] == 1
    got = live_eta_cache.load(max_age_min=60)
    assert got["483135"]["delivery_iso"] == "2026-06-24T19:36:00+00:00"
    assert got["483135"]["pickup_iso"] == "2026-06-24T19:30:00+00:00"
    assert got["483135"]["courier_id"] == "370"


def test_flag_off_is_noop(tmp_path, monkeypatch):
    f = _redirect_cache(tmp_path, monkeypatch)
    monkeypatch.setattr(plan_recheck, "ENABLE_PLAN_RECHECK_LIVE_ETA_REFRESH", False)
    plan_recheck._refresh_live_eta_from_plans({"370": _plan([
        {"type": "dropoff", "order_id": "1", "predicted_at": "2026-06-24T19:36:00+00:00"},
    ])}, {})
    assert not f.exists()


def test_invalidated_plan_skipped(tmp_path, monkeypatch):
    _redirect_cache(tmp_path, monkeypatch)
    monkeypatch.setattr(plan_recheck, "ENABLE_PLAN_RECHECK_LIVE_ETA_REFRESH", True)
    summary = {}
    plan_recheck._refresh_live_eta_from_plans({
        "370": _plan([{"type": "dropoff", "order_id": "1", "predicted_at": "2026-06-24T19:36:00+00:00"}], invalidated=True),
    }, summary)
    assert summary["live_eta_refreshed"] == 0
    assert live_eta_cache.load(max_age_min=60) == {}


def test_freshens_stale_entry(tmp_path, monkeypatch):
    """Wpis zamrożony (stary decided_at) zostaje odświeżony nowym decided_at=now
    i nową wartością dostawy — sedno fixu (case 22:05 → plan)."""
    _redirect_cache(tmp_path, monkeypatch)
    monkeypatch.setattr(plan_recheck, "ENABLE_PLAN_RECHECK_LIVE_ETA_REFRESH", True)
    # zasiej stary, „zamrożony" wpis 22:05 z odległej decyzji
    live_eta_cache.upsert({"483135": "2026-06-24T20:05:01+00:00"},
                          {"483135": "2026-06-24T19:31:00+00:00"}, "370")
    # plan ma poprawne 19:36 → refresh nadpisuje
    plan_recheck._refresh_live_eta_from_plans({"370": _plan([
        {"type": "pickup", "order_id": "483135", "predicted_at": "2026-06-24T19:30:00+00:00"},
        {"type": "dropoff", "order_id": "483135", "predicted_at": "2026-06-24T19:36:00+00:00"},
    ])}, {})
    got = live_eta_cache.load(max_age_min=60)
    assert got["483135"]["delivery_iso"] == "2026-06-24T19:36:00+00:00"  # nie 20:05
