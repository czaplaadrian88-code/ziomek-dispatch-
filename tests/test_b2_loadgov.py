"""SP-B2-LOADGOV (2026-06-11) — testy load governora floty.

load = aktywne zlecenia (orders_state, nie-terminalne, świeże ≤3h) / aktywni
kurierzy; EWMA tau=15 min. Polityka za 🛑 ENABLE_FLEET_LOAD_GOVERNOR (OFF):
ewma>2,7 → kara -40 za dokładanie do bagów ≥3; ewma>3,5 → JEDEN alert
(hysteresis re-arm <3,0). Telemetria loadgov_* zawsze (LOCATION A+B).
"""
import json
import math
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from dispatch_v2 import common as C
from dispatch_v2 import dispatch_pipeline as dp
from dispatch_v2 import shadow_dispatcher

T0 = datetime(2026, 6, 11, 10, 0, tzinfo=timezone.utc)


@pytest.fixture(autouse=True)
def _fresh_loadgov_state(tmp_path, monkeypatch):
    dp._LOADGOV_STATE.update(ts=None, ewma=None, alert_armed=True)
    dp._LOADGOV_ORDERS_CACHE.update(mtime=None, count=None)
    yield
    dp._LOADGOV_STATE.update(ts=None, ewma=None, alert_armed=True)
    dp._LOADGOV_ORDERS_CACHE.update(mtime=None, count=None)


def _write_orders(tmp_path, monkeypatch, entries):
    p = tmp_path / "orders_state.json"
    p.write_text(json.dumps(entries), encoding="utf-8")
    monkeypatch.setattr(dp, "LOADGOV_ORDERS_STATE_PATH", str(p))
    dp._LOADGOV_ORDERS_CACHE.update(mtime=None, count=None)
    return p


def _entry(status, minutes_ago=10):
    return {"status": status,
            "updated_at": (T0 - timedelta(minutes=minutes_ago)).isoformat()}


# ── licznik aktywnych ──

def test_active_orders_counts_nonterminal_fresh(tmp_path, monkeypatch):
    _write_orders(tmp_path, monkeypatch, {
        "1": _entry("assigned"), "2": _entry("picked_up"),
        "3": _entry("planned"), "4": _entry("returned_to_pool"),
        "5": _entry("delivered"), "6": _entry("cancelled"),
        "7": _entry("assigned", minutes_ago=600),  # zalegający (>3h) — out
    })
    assert dp._loadgov_active_orders(T0) == 4


def test_active_orders_missing_file_none(tmp_path, monkeypatch):
    monkeypatch.setattr(dp, "LOADGOV_ORDERS_STATE_PATH", str(tmp_path / "brak.json"))
    dp._LOADGOV_ORDERS_CACHE.update(mtime=None, count=None)
    assert dp._loadgov_active_orders(T0) is None


# ── load + EWMA ──

def _fleet(n):
    return {str(i): SimpleNamespace() for i in range(n)}


def test_load_first_sample_equals_instant(tmp_path, monkeypatch):
    _write_orders(tmp_path, monkeypatch, {str(i): _entry("assigned") for i in range(30)})
    now_, ewma, orders, couriers = dp._loadgov_compute(_fleet(10), T0)
    assert now_ == 3.0 and ewma == 3.0
    assert orders == 30 and couriers == 10


def test_ewma_blends_with_tau(tmp_path, monkeypatch):
    _write_orders(tmp_path, monkeypatch, {str(i): _entry("assigned") for i in range(30)})
    dp._loadgov_compute(_fleet(10), T0)  # ewma=3.0
    # po 15 min load spada do 1.0 → alpha = 1-exp(-1) ≈ 0.632
    _write_orders(tmp_path, monkeypatch, {str(i): _entry("assigned") for i in range(10)})
    now2, ewma2, _, _ = dp._loadgov_compute(_fleet(10), T0 + timedelta(minutes=15))
    want = 0.632 * 1.0 + 0.368 * 3.0
    assert now2 == 1.0
    assert ewma2 == pytest.approx(want, abs=0.01)


def test_zero_couriers_fail_soft(tmp_path, monkeypatch):
    _write_orders(tmp_path, monkeypatch, {"1": _entry("assigned")})
    now_, ewma, orders, couriers = dp._loadgov_compute({}, T0)
    assert now_ is None and couriers == 0


# ── hysteresis alertu ──

def test_alert_fires_once_and_rearms():
    emit, armed = dp._loadgov_alert_transition(3.6, True)
    assert emit is True and armed is False
    emit, armed = dp._loadgov_alert_transition(3.8, armed)   # dalej wysoko — cisza
    assert emit is False and armed is False
    emit, armed = dp._loadgov_alert_transition(3.2, armed)   # między progami — cisza
    assert emit is False and armed is False
    emit, armed = dp._loadgov_alert_transition(2.8, armed)   # < re-arm → uzbrój
    assert emit is False and armed is True
    emit, armed = dp._loadgov_alert_transition(3.6, armed)   # znowu w górę → emit
    assert emit is True and armed is False


def test_alert_none_ewma_noop():
    assert dp._loadgov_alert_transition(None, True) == (False, True)


# ── serializer LOCATION A+B ──

def _ser_cand():
    return SimpleNamespace(
        courier_id="c1", name="T", score=50.0, plan=None,
        feasibility_verdict="MAYBE", feasibility_reason="ok", best_effort=False,
        metrics={"loadgov_load_now": 2.9, "loadgov_load_ewma": 2.84,
                 "loadgov_active_orders": 29, "loadgov_active_couriers": 10,
                 "bonus_loadgov_shadow_delta": -40.0},
    )


def test_serializer_location_a_loadgov_fields():
    out = shadow_dispatcher._serialize_candidate(_ser_cand())
    assert out["loadgov_load_ewma"] == 2.84
    assert out["loadgov_active_orders"] == 29
    assert out["bonus_loadgov_shadow_delta"] == -40.0


def test_serializer_location_b_best_loadgov_fields():
    best = _ser_cand()
    result = SimpleNamespace(
        order_id="476001", restaurant="R", delivery_address="A",
        verdict="PROPOSE", reason="ok", best=best, candidates=[best],
        pickup_ready_at=T0,
    )
    out = shadow_dispatcher._serialize_result(result, event_id="ev", latency_ms=1.0)
    assert out["best"]["loadgov_load_now"] == 2.9
    assert out["best"]["bonus_loadgov_shadow_delta"] == -40.0
