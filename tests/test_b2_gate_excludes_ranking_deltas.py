"""INCYDENT-FIX 2026-06-12 — bramka all_candidates_low_score bez delt rankingowych.

Po flipie SYNCWORKA/LOADGOV (11.06 14:28) kara -150 spychała całe pule pod
MIN_PROPOSE_SCORE=-100 → 92 nowe KOORD/30h (rate 15,6%→50%), łamiąc
ALWAYS-PROPOSE. Fix: `_gate_score_excluding_ranking_deltas` — bramka ocenia
score bez delt aplikowanych flagami decyzyjnymi (kara = ranking, nie cisza).
"""
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

import pytest

from dispatch_v2 import common as C
from dispatch_v2 import dispatch_pipeline as dp


def _cand(score, sync_delta=0.0, loadgov_delta=0.0):
    return SimpleNamespace(score=score, metrics={
        "bonus_sync_spread_shadow_delta": sync_delta,
        "bonus_loadgov_shadow_delta": loadgov_delta,
    })


@pytest.fixture
def _flags_on(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_BUNDLE_SYNC_SPREAD", True, raising=False)
    monkeypatch.setattr(C, "ENABLE_FLEET_LOAD_GOVERNOR", True, raising=False)


@pytest.fixture
def _flags_off(monkeypatch):
    monkeypatch.setattr(C, "ENABLE_BUNDLE_SYNC_SPREAD", False, raising=False)
    monkeypatch.setattr(C, "ENABLE_FLEET_LOAD_GOVERNOR", False, raising=False)


def test_incident_case_sync_delta_excluded(_flags_on):
    """Realny case 480207: score -127 z deltą -150 → bramka widzi +23 → PROPOSE."""
    g = dp._gate_score_excluding_ranking_deltas(_cand(-127.02, sync_delta=-150.0))
    assert g == pytest.approx(22.98, abs=0.01)
    assert g >= C.MIN_PROPOSE_SCORE


def test_both_deltas_excluded(_flags_on):
    g = dp._gate_score_excluding_ranking_deltas(
        _cand(-130.0, sync_delta=-80.0, loadgov_delta=-40.0))
    assert g == pytest.approx(-10.0)


def test_genuinely_bad_still_gated(_flags_on):
    """Score głęboko ujemny bez delt → bramka dalej łapie (semantyka -1047)."""
    g = dp._gate_score_excluding_ranking_deltas(_cand(-365.95, sync_delta=-150.0))
    assert g == pytest.approx(-215.95, abs=0.01)
    assert g < C.MIN_PROPOSE_SCORE


def test_flags_off_score_unchanged(_flags_off):
    """Flagi OFF → delta i tak nie była aplikowana do score → bez korekty."""
    g = dp._gate_score_excluding_ranking_deltas(_cand(-127.0, sync_delta=-150.0))
    assert g == -127.0


def test_missing_metrics_fail_soft(_flags_on):
    g = dp._gate_score_excluding_ranking_deltas(SimpleNamespace(score=-50.0, metrics=None))
    assert g == -50.0


def test_non_numeric_score_none(_flags_on):
    assert dp._gate_score_excluding_ranking_deltas(SimpleNamespace(score=None, metrics={})) is None
