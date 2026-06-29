"""Sprint 1 NO-GPS-EQUAL (Adrian 2026-06-29 „bez kary przed zmianą") — gate chirurgiczny.

Flaga ENABLE_PRE_SHIFT_EQUAL_NO_PENALTY (gate _apply_pre_shift_equal_gate):
  OFF (default) → no-op (kara zachowana).
  ON  → zdejmuje LEKKĄ karę NEAR (∝m, ≤~−30) i stałą feasibility (−20);
        ⚠ ZACHOWUJE FAR-veto (PRE_SHIFT_FAR_PEN ≈ −1000) — kurier daleko przed
        zmianą NIE bierze now-ordera (load-aware relaks = osobno, gradient sam).
Replay 29.06 wykrył że FAR-veto ma wartość ~−1000 (nie −20) → zdjęcie go = klient
czeka 40-60 min. Stąd gate rozróżnia NEAR (zdejmij) od FAR (zostaw).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import dispatch_pipeline as dp  # noqa: E402


def _flag(on):
    return (lambda name: (name == "ENABLE_PRE_SHIFT_EQUAL_NO_PENALTY") and on)


def test_flag_off_keeps_penalty_noop(monkeypatch):
    monkeypatch.setattr(dp.C, "decision_flag", _flag(False))
    metrics = {"v325_pre_shift_soft_penalty": -15.77}
    out = dp._apply_pre_shift_equal_gate(-15.77, metrics)
    assert out == -15.77
    assert metrics["v325_pre_shift_soft_penalty"] == -15.77
    assert "v325_pre_shift_penalty_suppressed" not in metrics


def test_flag_on_zeroes_near_penalty(monkeypatch):
    monkeypatch.setattr(dp.C, "decision_flag", _flag(True))
    metrics = {"v325_pre_shift_soft_penalty": -15.77}
    out = dp._apply_pre_shift_equal_gate(-15.77, metrics)          # NEAR ∝m
    assert out == 0.0
    assert metrics["v325_pre_shift_soft_penalty"] == 0.0
    assert metrics["v325_pre_shift_penalty_suppressed"] == -15.77


def test_flag_on_zeroes_fixed_v325_penalty(monkeypatch):
    monkeypatch.setattr(dp.C, "decision_flag", _flag(True))
    metrics = {}
    out = dp._apply_pre_shift_equal_gate(-20.0, metrics)           # stała feasibility
    assert out == 0.0
    assert metrics["v325_pre_shift_penalty_suppressed"] == -20.0


def test_flag_on_KEEPS_far_veto(monkeypatch):
    monkeypatch.setattr(dp.C, "decision_flag", _flag(True))
    far = float(dp.C.PRE_SHIFT_FAR_PEN)                            # ~-1000 load-aware veto
    metrics = {}
    out = dp._apply_pre_shift_equal_gate(far, metrics)
    assert out == far                                             # FAR-veto NIETKNIĘTY
    assert metrics.get("v325_pre_shift_far_veto_kept") == round(far, 2)
    assert "v325_pre_shift_penalty_suppressed" not in metrics


def test_on_vs_off_differ_on_near(monkeypatch):
    monkeypatch.setattr(dp.C, "decision_flag", _flag(False))
    off = dp._apply_pre_shift_equal_gate(-20.0, {})
    monkeypatch.setattr(dp.C, "decision_flag", _flag(True))
    on = dp._apply_pre_shift_equal_gate(-20.0, {})
    assert off == -20.0 and on == 0.0 and off != on


def test_zero_and_positive_safe(monkeypatch):
    monkeypatch.setattr(dp.C, "decision_flag", _flag(True))
    assert dp._apply_pre_shift_equal_gate(0.0, {}) == 0.0
    assert dp._apply_pre_shift_equal_gate(5.0, {}) == 5.0          # nie-kara nietknięta
