"""ALWAYS-PROPOSE ON SATURATION (Adrian 2026-06-15).

Dyrektywa: gdy nie da się dotrzymać 35min, Ziomek NIE milczy (KOORD) — proponuje
najlepszego dostępnego (best_effort, banner ⚠️), choćby dostawa >35min. Koordynator
nadpisze. Cel: pełna autonomia (Z1), Ziomek radzi sobie nie gorzej od człowieka.
KOORD ZOSTAJE tylko gdy: early_bird (za wcześnie) lub PUSTA pula (brak kandydata).

Pattern = source-regression (jak test_best_effort_r6_redirect / test_obj_f3*):
bramki głęboko w assess_order — sprawdzamy guard + helper + kontrakt flagi w źródle;
behawior end-to-end weryfikowany LIVE po flipie (shadow log). Plus funkcjonalny helper.
"""
import inspect

from dispatch_v2 import common, dispatch_pipeline


def test_helper_exists_and_reads_flag():
    assert hasattr(dispatch_pipeline, "_always_propose_on")


def test_common_flag_contract_default_off():
    """Konstanta istnieje; default OFF (env unset → '0') — kod inertny do flipu."""
    assert hasattr(common, "ENABLE_ALWAYS_PROPOSE_ON_SATURATION")
    assert common.ENABLE_ALWAYS_PROPOSE_ON_SATURATION is False


def test_helper_off_returns_false(monkeypatch):
    monkeypatch.setattr(common, "flag", lambda name, default=None: default if name == "ENABLE_ALWAYS_PROPOSE_ON_SATURATION" else common.flag(name, default))
    monkeypatch.setattr(common, "ENABLE_ALWAYS_PROPOSE_ON_SATURATION", False, raising=False)
    assert dispatch_pipeline._always_propose_on() is False


def test_helper_on_returns_true(monkeypatch):
    monkeypatch.setattr(common, "flag", lambda name, default=None: True if name == "ENABLE_ALWAYS_PROPOSE_ON_SATURATION" else default)
    assert dispatch_pipeline._always_propose_on() is True


def _gate_section(src, anchor, span=400):
    i = src.find(anchor)
    assert i != -1, f"nie znaleziono kotwicy: {anchor}"
    return src[i:i + span]


def test_all_four_silence_gates_have_always_propose_guard():
    """Wszystkie 4 bramki ciszy mają `not _always_propose_on()` w warunku."""
    src = inspect.getsource(dispatch_pipeline)
    # 1) feasible all_candidates_low_score
    s1 = _gate_section(src, "all_candidates_low_score (best=")
    # warunek bramki jest PRZED reason — szukamy guardu w oknie przed nią
    pre1 = src[max(0, src.find("all_candidates_low_score (best=") - 600):src.find("all_candidates_low_score (best=")]
    assert "_always_propose_on()" in pre1, "brak guardu w all_candidates_low_score"
    # 2) best_effort_r6_breach_v2
    pre2 = src[max(0, src.find("best_effort_r6_breach_v2 (best=") - 600):src.find("best_effort_r6_breach_v2 (best=")]
    assert "_always_propose_on()" in pre2, "brak guardu w best_effort_r6_breach_v2"
    # 3) best_effort_r6_breach (OBJ F3)
    pre3 = src[max(0, src.find('best_effort_r6_breach (best=') - 600):src.find('best_effort_r6_breach (best=')]
    assert "_always_propose_on()" in pre3, "brak guardu w best_effort_r6_breach"
    # 4) best_effort_low_score
    pre4 = src[max(0, src.find("best_effort_low_score (best=") - 600):src.find("best_effort_low_score (best=")]
    assert "_always_propose_on()" in pre4, "brak guardu w best_effort_low_score"


def test_early_bird_NOT_guarded():
    """early_bird ZOSTAJE KOORD (za wcześnie, wraca do puli) — NIE objęty always-propose.
    K10: bramka przeniesiona do core.gates — skanujemy jej źródło."""
    from dispatch_v2.core import gates as _gates_mod
    src = inspect.getsource(_gates_mod)
    i = src.find('reason=f"early_bird (')
    assert i != -1
    pre = src[max(0, i - 400):i]
    assert "_always_propose_on()" not in pre, "early_bird NIE powinien mieć always-propose guardu"
