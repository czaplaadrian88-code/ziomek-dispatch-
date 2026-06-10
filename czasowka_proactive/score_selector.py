"""ETAP 5 KROK 1 (2026-06-10, audyt Z-05) — score-based selektor czasówek, SHADOW.

Problem Z-05: progi proaktywne km/drop_prox (V324B_CZASOWKA_IDEAL_*/GOOD_*) są
niespełnialne przy ~18% pokryciu GPS → czasówki spadały do FORCE_ASSIGN na T-40.
Ten moduł liczy COUNTERFACTUAL w oknach T-60/T-50 (40 < mins ≤ 60): „kogo wybrałby
selektor oparty o PEŁNY ranking assess_order + warunki jakościowe na istniejących
metrykach" — i TYLKO loguje wynik do czasowka_eval_log (pola sb_*). Zero zmiany
zachowania (KROK 1 = shadow; flip live = KROK 3 za flagą
CZASOWKA_PROACTIVE_SCORE_BASED po ACK Adriana na progi z kalibracji).

Warunki jakościowe (progi w flags.json, hot-reload):
  - score(best) ≥ CZASOWKA_PROACTIVE_MIN_SCORE        (start 30)
  - margin     ≥ CZASOWKA_PROACTIVE_MIN_MARGIN        (start 15)
      margin w semantyce E2/Z-10 (ENABLE_F7_MARGIN_FINAL_RANKING):
      score(best) − max(score POZOSTAŁYCH feasible MAYBE). Pula solo (1 feasible)
      → margin niezdefiniowany → reject `solo_pool` (strict, mirror klasyfikatora
      Fazy 7 gdzie solo=0.0 nie przechodzi; kalibracja skwantyfikuje koszt).
  - przewidywany wait kuriera pod restauracją ≤ CZASOWKA_PROACTIVE_MAX_WAIT_MIN
      (start 10; metryka v3273_wait_courier_max_min z planu best)
  - zero breachy R6 w planie (metrics.r6_per_order_violations puste)

Czyste funkcje, zero I/O — caller (czasowka_scheduler) resolves flagi i merguje
wynik do rekordu eval-loga. Lekcja #180: testy nie dotykają dysku.
"""
from __future__ import annotations

from typing import Any, List, Optional

from dispatch_v2.common import flag

# Defaulty progów (fallback gdy klucza brak w flags.json)
DEFAULT_MIN_SCORE = 30.0
DEFAULT_MIN_MARGIN = 15.0
DEFAULT_MAX_WAIT_MIN = 10.0

# Okno shadow: T-60/T-50 czyli FORCE_ASSIGN_MIN < mins ≤ EVAL_START_MIN
WINDOW_MIN_EXCL = 40.0
WINDOW_MAX_INCL = 60.0


def _is_maybe(c: Any) -> bool:
    return getattr(c, "feasibility_verdict", None) == "MAYBE"


def _score(c: Any) -> Optional[float]:
    s = getattr(c, "score", None)
    try:
        return float(s) if s is not None else None
    except (TypeError, ValueError):
        return None


def _cid(c: Any) -> str:
    return str(getattr(c, "courier_id", None) or getattr(c, "cid", None) or "")


def _candidates(eval_result: dict) -> List[Any]:
    """Pełny ranking z eval_czasowka — preferuj all_candidates_for_proactive."""
    cands = eval_result.get("all_candidates_for_proactive")
    if cands:
        return list(cands)
    out: List[Any] = []
    best = eval_result.get("best")
    if best is not None:
        out.append(best)
    out.extend(eval_result.get("alternatives") or [])
    return out


def evaluate_score_based(
    eval_result: dict,
    min_score: float = DEFAULT_MIN_SCORE,
    min_margin: float = DEFAULT_MIN_MARGIN,
    max_wait_min: float = DEFAULT_MAX_WAIT_MIN,
) -> dict:
    """Counterfactual: czy selektor score-based przypisałby czasówkę TERAZ?

    Kandydat = eval_result['best'] (faktyczny wybór silnika — ten sam, którego
    FORCE_ASSIGN użyje na T-40), gates na jego metrykach. Zwraca pola sb_*
    (sb = score-based) do merge'a w rekord eval-loga. NIGDY nie raises na
    poprawnym wejściu — brak danych → would_assign=False z powodem.
    """
    cands = _candidates(eval_result)
    feasible = [c for c in cands if _is_maybe(c)]
    best = eval_result.get("best")

    out = {
        "sb_would_assign": False,
        "sb_reject_reason": None,
        "sb_cid": None,
        "sb_name": None,
        "sb_score": None,
        "sb_margin": None,
        "sb_solo": False,
        "sb_best_is_score_top": None,
        "sb_wait_min": None,
        "sb_r6_violations": None,
        "sb_pool_feasible": len(feasible),
        "sb_thresholds": {
            "min_score": min_score,
            "min_margin": min_margin,
            "max_wait_min": max_wait_min,
        },
    }

    if best is None or not _is_maybe(best):
        out["sb_reject_reason"] = "no_maybe_best"
        return out

    best_cid = _cid(best)
    best_score = _score(best)
    out["sb_cid"] = best_cid
    out["sb_name"] = getattr(best, "name", None)
    out["sb_score"] = best_score

    # Metryki best (wait + R6) — zawsze logowane, nawet przy wcześniejszym reject.
    metrics = getattr(best, "metrics", None) or {}
    try:
        wait_min = float(metrics.get("v3273_wait_courier_max_min") or 0.0)
    except (TypeError, ValueError):
        wait_min = 0.0
    r6_pov = metrics.get("r6_per_order_violations") or []
    out["sb_wait_min"] = wait_min
    out["sb_r6_violations"] = len(r6_pov)

    # Margin E2/Z-10: score(best) − max(score pozostałych feasible).
    other_scores = [
        s for s in (_score(c) for c in feasible if _cid(c) != best_cid)
        if s is not None
    ]
    if other_scores:
        top_other = max(other_scores)
        margin = (best_score or 0.0) - top_other
        out["sb_margin"] = margin
        out["sb_best_is_score_top"] = (best_score or 0.0) >= top_other - 1e-9
    else:
        out["sb_solo"] = True
        out["sb_best_is_score_top"] = True

    # Gates w kolejności — pierwszy fail = reason (reszta metryk już zalogowana).
    if best_score is None or best_score < min_score:
        out["sb_reject_reason"] = "score_below_min"
        return out
    if out["sb_solo"]:
        out["sb_reject_reason"] = "solo_pool"
        return out
    if out["sb_margin"] < min_margin:
        out["sb_reject_reason"] = "margin_below_min"
        return out
    if wait_min > max_wait_min:
        out["sb_reject_reason"] = "wait_above_max"
        return out
    if len(r6_pov) > 0:
        out["sb_reject_reason"] = "r6_violations"
        return out

    out["sb_would_assign"] = True
    return out


def shadow_fields_for_eval(eval_result: dict, mins_to_pickup) -> dict:
    """Caller-facing hook dla czasowka_scheduler: flag-gate + window-gate.

    Zwraca {} (nic do merge'a) gdy: flaga CZASOWKA_PROACTIVE_SCORE_SHADOW off,
    brak minutes_to_pickup, albo poza oknem T-60/T-50 (40 < mins ≤ 60).
    Progi resolve'owane z flags.json (hot-reload — proces czasówki to oneshot,
    świeży odczyt per tick).
    """
    if not flag("CZASOWKA_PROACTIVE_SCORE_SHADOW", default=False):
        return {}
    if mins_to_pickup is None:
        return {}
    try:
        mins = float(mins_to_pickup)
    except (TypeError, ValueError):
        return {}
    if not (WINDOW_MIN_EXCL < mins <= WINDOW_MAX_INCL):
        return {}

    def _num_flag(name: str, default: float) -> float:
        raw = flag(name, default=default)
        try:
            return float(raw)
        except (TypeError, ValueError):
            return default

    return evaluate_score_based(
        eval_result,
        min_score=_num_flag("CZASOWKA_PROACTIVE_MIN_SCORE", DEFAULT_MIN_SCORE),
        min_margin=_num_flag("CZASOWKA_PROACTIVE_MIN_MARGIN", DEFAULT_MIN_MARGIN),
        max_wait_min=_num_flag("CZASOWKA_PROACTIVE_MAX_WAIT_MIN", DEFAULT_MAX_WAIT_MIN),
    )
