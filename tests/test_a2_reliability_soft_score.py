"""Test A2 reliability soft-score (2026-06-07, dźwignia A2). Żywy hook scoringu,
flag-gated default OFF. Metoda 1:1 z tools/a2_selection_shadow.py. Walidacja:
delta poprawna + gating (confidence/min_gap/unknown) + flag OFF=inert + feed fail-safe
+ re-sort. Patrz memory ziomek-autonomy-cascade-verdict.
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
from dispatch_v2 import common, dispatch_pipeline as dp  # noqa: E402


def _c(cid, score, bag=0):
    return SimpleNamespace(courier_id=cid, score=score, metrics={"bag_size_before": bag})


def test_delta_formula():
    breach = {"1": 0.30, "2": 0.05}; conf = {"1": "high", "2": "high"}; fm = 0.067
    assert round(dp._a2_reliability_delta("1", breach, conf, fm, 60, 0.05), 1) == round(-60 * 0.233, 1)
    assert dp._a2_reliability_delta("2", breach, conf, fm, 60, 0.05) == 0.0   # poniżej mediany
    assert dp._a2_reliability_delta("9", breach, conf, fm, 60, 0.05) == 0.0   # nieznany cid


def test_low_confidence_gated():
    assert dp._a2_reliability_delta("1", {"1": 0.30}, {"1": "low"}, 0.067, 60, 0.05) == 0.0


def test_min_gap_gated():
    # gap 0.033 < 0.05 -> 0
    assert dp._a2_reliability_delta("1", {"1": 0.10}, {"1": "high"}, 0.067, 60, 0.05) == 0.0


def test_flag_off_no_change(monkeypatch):
    monkeypatch.setattr(common, "ENABLE_A2_RELIABILITY_SOFT_SCORE", False, raising=False)
    a = _c("1", 100); b = _c("2", 90)
    dp._a2_reliability_soft_score([a, b])
    assert a.score == 100 and b.score == 90


def test_flag_on_demotes_high_breach(monkeypatch):
    monkeypatch.setattr(common, "ENABLE_A2_RELIABILITY_SOFT_SCORE", True, raising=False)
    monkeypatch.setattr(common, "A2_RELIABILITY_COEFF", 60.0, raising=False)
    monkeypatch.setattr(common, "A2_RELIABILITY_MIN_GAP", 0.05, raising=False)
    monkeypatch.setattr(dp, "_load_courier_reliability",
                        lambda: ({"1": 0.30, "2": 0.05}, {"1": "high", "2": "high"}, 0.067))
    a = _c("1", 100); b = _c("2", 95)   # 1: gap 0.233 -> -14.0 -> 86 < 95 -> 2 wygrywa
    out = dp._a2_reliability_soft_score([a, b])
    assert out[0].courier_id == "2"
    assert round(a.score, 1) == round(100 - 60 * 0.233, 1)
    assert b.score == 95


def test_feed_missing_no_change(monkeypatch):
    monkeypatch.setattr(common, "ENABLE_A2_RELIABILITY_SOFT_SCORE", True, raising=False)
    monkeypatch.setattr(dp, "_load_courier_reliability", lambda: (None, None, None))
    a = _c("1", 100)
    dp._a2_reliability_soft_score([a])
    assert a.score == 100
