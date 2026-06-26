"""Bug #4 reseq shadow — test ON≠OFF (flaga ENABLE_BUG4_RESEQ_SHADOW).
Mockuje OSRM + solver, sprawdza: OFF = brak zapisu, ON = rekord z deltą drive."""
import json
from datetime import datetime, timezone

from dispatch_v2 import plan_recheck as PR
from dispatch_v2 import common as C
from dispatch_v2 import route_simulator_v2 as R

NOW = datetime(2026, 6, 26, 19, 16, tzinfo=timezone.utc)


class _FakePlan:
    sla_violations = 0
    total_duration_min = 20.0
    sequence = ["A", "B"]
    pickup_at = {"A": datetime(2026, 6, 26, 19, 30, tzinfo=timezone.utc),
                 "B": datetime(2026, 6, 26, 19, 40, tzinfo=timezone.utc)}
    predicted_delivered_at = {"A": datetime(2026, 6, 26, 19, 35, tzinfo=timezone.utc),
                              "B": datetime(2026, 6, 26, 19, 50, tzinfo=timezone.utc)}


def _orders():
    return {
        "A": {"status": "assigned", "pickup_coords": [53.11, 23.14],
              "delivery_coords": [53.12, 23.13], "courier_id": "99",
              "czas_kuriera_warsaw": "2026-06-26T21:16:00+02:00"},
        "B": {"status": "assigned", "pickup_coords": [53.12, 23.15],
              "delivery_coords": [53.13, 23.18], "courier_id": "99",
              "czas_kuriera_warsaw": "2026-06-26T21:29:00+02:00"},
    }


def _existing():
    return {"stops": [
        {"order_id": "A", "type": "pickup", "coords": {"lat": 53.11, "lng": 23.14}},
        {"order_id": "B", "type": "pickup", "coords": {"lat": 53.12, "lng": 23.15}},
        {"order_id": "A", "type": "dropoff", "coords": {"lat": 53.12, "lng": 23.13}},
        {"order_id": "B", "type": "dropoff", "coords": {"lat": 53.13, "lng": 23.18}},
    ]}


def _setup(monkeypatch, tmp_path, flag_on, fresh=25.0, frozen=30.0):
    p = tmp_path / "bug4.jsonl"
    monkeypatch.setattr(PR, "_BUG4_RESEQ_SHADOW_PATH", str(p))
    monkeypatch.setattr(C, "flag",
                        lambda name, default=False: flag_on if name == "ENABLE_BUG4_RESEQ_SHADOW" else default)
    monkeypatch.setattr(PR, "_start_anchor",
                        lambda *a, **k: ((53.1, 23.1), NOW, "gps_pwa"))
    monkeypatch.setattr(R, "simulate_bag_route_v2", lambda *a, **k: _FakePlan())
    calls = {"i": 0}

    def fake_sum(start, coords):
        i = calls["i"]
        calls["i"] += 1
        return fresh if i == 0 else frozen  # 1. wywołanie = fresh, 2. = frozen
    monkeypatch.setattr(PR, "_osrm_drive_min_sum", fake_sum)
    return p


def test_flag_off_no_write(monkeypatch, tmp_path):
    p = _setup(monkeypatch, tmp_path, flag_on=False)
    summary = {}
    PR._bug4_reseq_shadow("99", ["A", "B"], _existing(), _orders(), {}, NOW, R, summary)
    assert not p.exists()
    assert "bug4_shadow_evals" not in summary


def test_flag_on_writes_delta(monkeypatch, tmp_path):
    p = _setup(monkeypatch, tmp_path, flag_on=True, fresh=25.0, frozen=30.0)
    summary = {}
    PR._bug4_reseq_shadow("99", ["A", "B"], _existing(), _orders(), {}, NOW, R, summary)
    assert p.exists()
    rec = json.loads(p.read_text().strip())
    assert rec["fresh_drive_min"] == 25.0
    assert rec["frozen_drive_min"] == 30.0
    assert rec["delta_min"] == 5.0
    assert rec["cid"] == "99"
    assert summary["bug4_shadow_evals"] == 1


def test_single_order_skipped(monkeypatch, tmp_path):
    p = _setup(monkeypatch, tmp_path, flag_on=True)
    summary = {}
    PR._bug4_reseq_shadow("99", ["A"], _existing(), _orders(), {}, NOW, R, summary)
    assert not p.exists()


def test_per_tick_cap(monkeypatch, tmp_path):
    p = _setup(monkeypatch, tmp_path, flag_on=True)
    summary = {"bug4_shadow_evals": PR._BUG4_RESEQ_SHADOW_MAX_PER_TICK}
    PR._bug4_reseq_shadow("99", ["A", "B"], _existing(), _orders(), {}, NOW, R, summary)
    assert not p.exists()  # cap osiągnięty → no-op
