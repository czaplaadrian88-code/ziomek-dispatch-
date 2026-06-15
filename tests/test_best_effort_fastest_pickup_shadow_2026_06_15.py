"""BEST-EFFORT FASTEST-PICKUP SHADOW (Adrian 2026-06-15) — log-only.

Selekcja „najszybszy odbiór → potem najszybszy dowóz" liczona w SHADOW obok realnego
wyboru (live = stary _best_effort_sort_key). ZERO zmiany zachowania do walidacji.
Pattern: helper functional + source-regression (że shadow nie nadpisuje `best`).
"""
import inspect
from datetime import datetime, timezone, timedelta

from dispatch_v2 import common, dispatch_pipeline as dp


def test_helper_exists():
    assert hasattr(dp, "_best_effort_fastest_pickup_key")


def test_common_flag_default_off():
    assert hasattr(common, "ENABLE_BEST_EFFORT_FASTEST_PICKUP_SHADOW")
    assert common.ENABLE_BEST_EFFORT_FASTEST_PICKUP_SHADOW is False


class _P:
    def __init__(self, pu, dv):
        self.pickup_at = {"O1": pu}
        self.predicted_delivered_at = {"O1": dv}


class _Cand:
    def __init__(self, cid, pu, dv):
        self.courier_id = cid
        self.plan = _P(pu, dv)
        self.pos_source = "gps"
        self.metrics = {}
        self.bag = []
        self.score = 0.0


def _neutralize_buckets(monkeypatch):
    monkeypatch.setattr(dp, "_is_informed_cand", lambda c: False)
    monkeypatch.setattr(dp, "_is_blind_empty_cand", lambda c: False)
    monkeypatch.setattr(dp, "_is_pre_shift_cand", lambda c: False)


def test_earliest_pickup_wins(monkeypatch):
    _neutralize_buckets(monkeypatch)
    now = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
    A = _Cand(111, now + timedelta(minutes=30), now + timedelta(minutes=50))
    B = _Cand(222, now + timedelta(minutes=10), now + timedelta(minutes=40))  # odbiór wcześniej
    winner = min([A, B], key=lambda c: dp._best_effort_fastest_pickup_key(c, "O1"))
    assert winner.courier_id == 222


def test_tie_pickup_then_earliest_delivery(monkeypatch):
    _neutralize_buckets(monkeypatch)
    now = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
    A = _Cand(111, now + timedelta(minutes=10), now + timedelta(minutes=55))
    B = _Cand(222, now + timedelta(minutes=10), now + timedelta(minutes=40))  # dowóz wcześniej
    winner = min([A, B], key=lambda c: dp._best_effort_fastest_pickup_key(c, "O1"))
    assert winner.courier_id == 222


def test_missing_plan_sorts_last(monkeypatch):
    _neutralize_buckets(monkeypatch)
    now = datetime(2026, 6, 15, 12, 0, tzinfo=timezone.utc)
    good = _Cand(111, now + timedelta(minutes=20), now + timedelta(minutes=45))
    bad = _Cand(222, None, None)  # brak ETA → +inf → na dół
    winner = min([good, bad], key=lambda c: dp._best_effort_fastest_pickup_key(c, "O1"))
    assert winner.courier_id == 111


def test_shadow_is_log_only_not_reassigning_best():
    """SHADOW NIE może nadpisać `best` — między `best = with_plan[0]` a blokiem shadow
    nie ma reassignacji best; blok pisze tylko do best.metrics[...]."""
    src = inspect.getsource(dp)
    i = src.find("FASTEST-PICKUP SHADOW (Adrian 2026-06-15)")
    assert i != -1
    section = src[i:i + 1400]
    assert 'best.metrics["best_effort_fastest_pickup_shadow"]' in section, "shadow musi pisać do metrics"
    # shadow liczy OSOBNY _fp_best; live best NIE jest do niego rebindowany
    assert "_fp_best = min(" in section, "shadow musi liczyć osobny _fp_best"
    assert "best = _fp_best" not in section, "shadow NIE nadpisuje live best"


def test_shadow_flag_guarded():
    src = inspect.getsource(dp)
    i = src.find("FASTEST-PICKUP SHADOW (Adrian 2026-06-15)")
    section = src[i:i + 600]
    assert "ENABLE_BEST_EFFORT_FASTEST_PICKUP_SHADOW" in section
