"""Sprint 1 NO-GPS-EQUAL (Adrian 2026-06-29 „bez kary przed zmianą").

Flaga ENABLE_PRE_SHIFT_EQUAL_NO_PENALTY (gate _apply_pre_shift_equal_gate):
  OFF (default) → kara score pre_shift zachowana (no-op, zwraca bonus bez zmian).
  ON  → kara wyzerowana (zwraca 0.0; metrics.v325_pre_shift_soft_penalty=0;
        metrics.v325_pre_shift_penalty_suppressed = oryginalna kwota dla obs).
„Kurier dotrze później" obsługuje LEGALNA ścieżka (clamp + R-LATE-PICKUP propozycja
przedłużenia do restauracji), NIE ukryta kara — to weryfikuje inny gate (feasibility),
tu pilnujemy że SAMA kara score znika ON i zostaje OFF (ON≠OFF).
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from dispatch_v2 import dispatch_pipeline as dp  # noqa: E402


def test_flag_off_keeps_penalty_noop(monkeypatch):
    monkeypatch.setattr(dp.C, "decision_flag", lambda name: False)
    metrics = {"v325_pre_shift_soft_penalty": -15.77}
    out = dp._apply_pre_shift_equal_gate(-15.77, metrics)
    assert out == -15.77                                    # bonus nietknięty
    assert metrics["v325_pre_shift_soft_penalty"] == -15.77  # metryka nietknięta
    assert "v325_pre_shift_penalty_suppressed" not in metrics


def test_flag_on_zeroes_penalty(monkeypatch):
    monkeypatch.setattr(dp.C, "decision_flag",
                        lambda name: name == "ENABLE_PRE_SHIFT_EQUAL_NO_PENALTY")
    metrics = {"v325_pre_shift_soft_penalty": -15.77}
    out = dp._apply_pre_shift_equal_gate(-15.77, metrics)
    assert out == 0.0                                       # kara zdjęta ze score
    assert metrics["v325_pre_shift_soft_penalty"] == 0.0    # spójność serializacji
    assert metrics["v325_pre_shift_penalty_suppressed"] == -15.77  # obs: ile zdjęto


def test_on_vs_off_differ(monkeypatch):
    # ON ≠ OFF na tej samej karze (gradient -20 z _pre_shift_gradient_penalty path)
    monkeypatch.setattr(dp.C, "decision_flag", lambda name: False)
    off = dp._apply_pre_shift_equal_gate(-20.0, {})
    monkeypatch.setattr(dp.C, "decision_flag",
                        lambda name: name == "ENABLE_PRE_SHIFT_EQUAL_NO_PENALTY")
    on = dp._apply_pre_shift_equal_gate(-20.0, {})
    assert off == -20.0 and on == 0.0
    assert off != on


def test_zero_penalty_safe(monkeypatch):
    # brak kary (in-shift, bonus 0) — gate ON nie wybucha, zwraca 0
    monkeypatch.setattr(dp.C, "decision_flag",
                        lambda name: name == "ENABLE_PRE_SHIFT_EQUAL_NO_PENALTY")
    metrics = {}
    out = dp._apply_pre_shift_equal_gate(0.0, metrics)
    assert out == 0.0
    assert metrics["v325_pre_shift_penalty_suppressed"] == 0.0
