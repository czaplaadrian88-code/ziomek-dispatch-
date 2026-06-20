#!/usr/bin/env python3
"""
[#3] Testy luki makespan 200ms vs 2000ms (READ-ONLY tool). Czysta logika progów.

Uruchom:
  /root/.openclaw/venvs/dispatch/bin/python -m pytest \\
      dispatch_v2/tests/test_warmstart_gap.py -q
"""
import sys

sys.path.insert(0, "/root/.openclaw/workspace/scripts")
sys.path.insert(0, "/root/.openclaw/workspace/scripts/dispatch_v2/eod_drafts/2026-06-17")

from dispatch_v2.tools.warmstart_gap import (  # noqa: E402
    significant, pctl, is_greedy, SIG_ABS_MIN, SIG_REL)


# ---- próg istotności luki ----
def test_significant_absolute_threshold():
    # gap 1.5 min > 1.0 → istotna niezależnie od makespanu
    assert significant(1.5, 60.0) is True
    # gap 0.5 min, makespan 60 → 0.83% < 5% i < 1 min → NIE
    assert significant(0.5, 60.0) is False


def test_significant_relative_threshold():
    # gap 0.8 min na makespanie 10 → 8% > 5% → istotna mimo <1 min
    assert significant(0.8, 10.0) is True
    # gap 0.8 min na makespanie 100 → 0.8% < 5% i < 1 min → NIE
    assert significant(0.8, 100.0) is False


def test_significant_boundary_exact():
    # dokładnie próg nie liczy się jako istotny (ostre >). Makespan=10 → 5%=0.5min
    # (poniżej progu absolutnego 1 min, więc testuje SAM próg względny):
    assert significant(SIG_ABS_MIN, 1000.0) is False          # ==1.0 min, rel ~0.1%
    assert significant(SIG_REL * 10.0, 10.0) is False         # ==5% (0.5min) dokładnie
    assert significant(SIG_REL * 10.0 + 0.001, 10.0) is True  # tuż powyżej 5%


def test_significant_none_and_zero_guard():
    assert significant(None, 60.0) is False
    assert significant(1.5, None) is False
    assert significant(1.5, 0.0) is False
    # ujemna luka (2000 GORSZY — nie powinno się zdarzyć przy tym samym heurystyku,
    # ale guard): gap<0 nie jest „istotną luką na korzyść 2000"
    assert significant(-2.0, 60.0) is False


# ---- percentyle ----
def test_pctl_basic():
    xs = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10]
    assert pctl(xs, 0.5) == 6        # indeks int(10*0.5)=5 → wartość 6
    assert pctl(xs, 0.9) == 10
    assert pctl([], 0.5) == 0.0
    assert pctl([42], 0.5) == 42


# ---- detekcja greedy fallback ----
class _P:
    def __init__(self, strategy, sequence=("a",)):
        self.strategy = strategy
        self.sequence = list(sequence)


def test_is_greedy_detects_fallback_strategies():
    assert is_greedy(_P("greedy_fallback")) is True
    assert is_greedy(_P("greedy")) is True
    assert is_greedy(_P("ortools_rejected_v3274")) is True
    assert is_greedy(_P("ortools")) is False
    assert is_greedy(_P("bruteforce")) is False
    # pusta sekwencja = brak realnego planu = traktuj jak fallback
    assert is_greedy(_P("ortools", sequence=())) is True


def test_is_greedy_case_insensitive():
    assert is_greedy(_P("GREEDY_FALLBACK")) is True
    assert is_greedy(_P("Greedy")) is True


# ---- regresja kierunku werdyktu: progi spójne z opisem ----
def test_thresholds_are_documented_values():
    assert SIG_ABS_MIN == 1.0
    assert SIG_REL == 0.05


if __name__ == "__main__":
    import pytest
    sys.exit(pytest.main([__file__, "-q"]))
