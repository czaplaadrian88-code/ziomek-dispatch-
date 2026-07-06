"""core.scorer — interfejs Scorer jako strategia (K13, ADR-R06).

Kontrakt: `score_candidate(heuristic_score, candidate, decision_ctx) → ScorerVerdict`.
- `HeuristicScorer` = dzisiejsza suma kar/bonusów policzona przez łańcuch
  w core.candidates (final_score) — scorer jest dla niej TOŻSAMOŚCIĄ, więc
  bajt-parytet score'ów zachodzi z konstrukcji.
- `LgbmScorer` = wrapper ISTNIEJĄCEJ inferencji shadow
  (`ml_inference.predict_two_model_for_decision`) z fail-soft fallbackiem do
  heurystyki + flagą `fallback=True` (metryka `scorer_fallback` w shadow_decisions
  przez auto-serializację metrics). ⚠ Wybór LGBM jako PRIMARY = flip POZA zakresem
  programu refaktoru (tylko jawna decyzja Adriana; dziś inferencja pozostaje
  shadow/log-only jak dotąd).

Wpięcie: core.candidates, za flagą `ENABLE_SCORER_INTERFACE` (ETAP4, default OFF
= ścieżka 1:1 bez odczytu tego modułu). Wybór implementacji: klucz `SCORER_IMPL`
w flags.json ('heuristic' default | 'lgbm'); zmiana = decyzja Adriana.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional

from dispatch_v2 import common as C


@dataclass
class ScorerVerdict:
    score: float
    source: str          # 'heuristic' | 'lgbm'
    fallback: bool       # True = LGBM padł/pusty → zwrócono heurystykę
    detail: Optional[Dict[str, Any]] = None


class HeuristicScorer:
    """Obecna suma kar (final_score z core.candidates) — tożsamość."""
    name = "heuristic"

    def score_candidate(self, heuristic_score: float, candidate: Any = None,
                        decision_ctx: Optional[dict] = None) -> ScorerVerdict:
        return ScorerVerdict(score=float(heuristic_score), source=self.name, fallback=False)


class LgbmScorer:
    """Wrapper istniejącej inferencji dwumodelu (shadow). Fail-soft: KAŻDA awaria
    / brak modelu / pusty wynik → heurystyka + fallback=True (nigdy raise)."""
    name = "lgbm"

    def score_candidate(self, heuristic_score: float, candidate: Any = None,
                        decision_ctx: Optional[dict] = None) -> ScorerVerdict:
        try:
            from dispatch_v2 import ml_inference as _ml
            res = _ml.predict_two_model_for_decision(
                dict(decision_ctx or {}), [candidate] if candidate is not None else [])
            if res is not None and res.n_candidates_scored > 0 and res.winner_score is not None:
                return ScorerVerdict(score=float(res.winner_score), source=self.name,
                                     fallback=False,
                                     detail={"winner_cid": res.winner_cid,
                                             "regimes": dict(res.regime_counts or {})})
        except Exception:
            pass  # fail-soft — scorer NIGDY nie psuje decyzji
        return ScorerVerdict(score=float(heuristic_score), source=self.name, fallback=True)


def get_scorer():
    """Wybór strategii z flags.json (`SCORER_IMPL`, default 'heuristic').
    Nieznana wartość → heurystyka (fail-soft, bez wyjątku)."""
    try:
        impl = str(C.load_flags().get("SCORER_IMPL", "heuristic")).lower()
    except Exception:
        impl = "heuristic"
    if impl == "lgbm":
        return LgbmScorer()
    return HeuristicScorer()
