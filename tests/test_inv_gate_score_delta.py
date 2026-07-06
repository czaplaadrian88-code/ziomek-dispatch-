"""INV-GATE-SCORE-DELTA (audyt 2026-06-24, spec odporności §6.A2): każda delta RANKINGOWA
dopisywana do `final_score` (flag-gated) MUSI być wyłączona z bramki MIN_PROPOSE/KOORD przez
`_GATE_RANKING_DELTA_EXCLUSIONS` — inaczej kara rankingowa po cichu wpływa na werdykt (była
luka: r1_progressive −45..−100 + v319h ŻYWE, nie wyłączone). Strażnik: rejestr ⇄ final_score.
"""
import inspect
import re

import dispatch_v2.dispatch_pipeline as DP
from dispatch_v2.core import candidates as _k11c  # K11: cialo petli per-kurier (skan obu zrodel)

_PAT = re.compile(
    r'if C\.decision_flag\("(\w+)"\):[ \t]*\n[ \t]*final_score = final_score \+ (\w+)')


def _final_score_flag_deltas():
    """Pary (flaga, zmienna) dla flag-gated single-delt dodawanych do final_score."""
    return _PAT.findall((inspect.getsource(DP) + inspect.getsource(_k11c)))


def test_every_flag_gated_final_score_delta_is_excluded():
    pairs = _final_score_flag_deltas()
    assert pairs, "brak flag-gated final_score delt — struktura/regex zmienione, sprawdź ręcznie"
    registry_flags = {f for f, _ in DP._GATE_RANKING_DELTA_EXCLUSIONS}
    missing = [(f, v) for f, v in pairs if f not in registry_flags]
    assert not missing, (
        f"delty rankingowe dopisywane do final_score, ale BRAK w _GATE_RANKING_DELTA_EXCLUSIONS "
        f"(cicho wpłyną na bramkę MIN_PROPOSE/KOORD): {missing}")


def test_registry_metric_key_equals_final_score_var():
    # bramka odejmuje metrics[key]; final_score dodaje `var`; metrics zapisuje var pod key.
    # key MUSI == nazwa zmiennej, inaczej bramka odejmuje co innego niż dodano.
    by_flag = dict(_final_score_flag_deltas())
    for flag, key in DP._GATE_RANKING_DELTA_EXCLUSIONS:
        if flag in by_flag:
            assert key == by_flag[flag], (
                f"{flag}: bramka wyłącza metrics['{key}'] ale final_score dodaje "
                f"'{by_flag[flag]}' — rozjazd, bramka odejmie złą wartość")


def test_live_gaps_now_covered():
    # r1_progressive + v319h (ŻYWE, były luką) MUSZĄ być w rejestrze
    flags = {f for f, _ in DP._GATE_RANKING_DELTA_EXCLUSIONS}
    assert "ENABLE_R1_PROGRESSIVE_CLIP" in flags
    assert "ENABLE_V319H_CONTINUATION_GUARD" in flags


def test_gate_excludes_live_delta(monkeypatch):
    # funkcjonalnie: gdy flaga ON i metrics ma deltę, gate-score = score − delta
    class _C:
        score = -90.0
        metrics = {"bonus_r1_progressive_shadow_delta": -45.0}
    monkeypatch.setattr(DP.C, "decision_flag",
                        lambda f: f == "ENABLE_R1_PROGRESSIVE_CLIP")
    # score -90 (poniżej? nie), ale z karą -45 było -90; gate wyłącza -45 → -90-(-45) = -45
    gs = DP._gate_score_excluding_ranking_deltas(_C())
    assert abs(gs - (-45.0)) < 1e-6, f"gate powinien wyłączyć -45 karę → -45, jest {gs}"
