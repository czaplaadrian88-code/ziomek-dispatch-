"""Dzielony selektor OBJM-LEXR6 — kanoniczne lex-helpery + bucketowanie (P1#5, 2026-06-19).

R6-breach-primary leksykograficzna selekcja kandydata W OBRĘBIE grupy (tier × bucket)
zwycięzcy score. Wydzielone z `dispatch_pipeline._objm_lexr6_d2_pick` /
`_objm_lexr6_shadow`, które trzymały IDENTYCZNE kopie `_bucket`/`_objm`/`_lex_qual`
(„INTENCJONALNIE powiela... zamiast współdzielić", KB TODO objm-lexr6-unify).

Klasyfikatory (`late_pickup_tier`, `is_informed`, `is_blind_empty`, `is_pre_shift`)
są WSTRZYKIWANE przez wołającego — moduł NIE importuje `dispatch_pipeline`
(unika cyklicznego importu i trzyma logikę selekcji niezależną od pipeline).

⚠ ETAP unifikacji (2026-06-19): podpięty jest TYLKO `_objm_lexr6_d2_pick` (FAZA 2,
flaga ENABLE_OBJM_LEXR6_SELECT, NIE-zamrożona). `_objm_lexr6_shadow` jest ZAMROŻONY
pod walidację at#152 (24.06, „walidacji NIE ruszać") i NA RAZIE trzyma własne kopie
inline. Po PASS at#152 → przepiąć też cień na ten moduł (dokończenie objm-lexr6-unify).
Logika tutaj jest bajt-identyczna z obiema kopiami, więc to przepięcie będzie czyste.
"""
from __future__ import annotations

import dispatch_v2.common as C  # tylko flaga (decision_flag); common = liść, brak cyklu


def objm(c, k):
    """Metryka liczbowa kandydata `c` z `c.metrics[k]` jako float, albo None."""
    v = (getattr(c, "metrics", None) or {}).get(k)
    return float(v) if isinstance(v, (int, float)) else None


def lex_qual(c):
    """Klucz jakości leksykograficznej: (R6-breach → committed-late → new-pickup-late).
    Brak R6 → 9e9 (na koniec).

    Parytet post-shift (Adrian 2026-06-24, „robimy 3"): gdy
    ENABLE_POST_SHIFT_OVERRUN_PENALTY → prepend WIODĄCY term `post_shift_overrun_penalty`
    (kurier kończący PO zmianie spada — spójnie z `_best_effort_objm_pick`). Selektor
    feasible widzi nadwyżkę >grace tylko w end-of-day-salvage (poza tym v324a rejectuje
    dropoff>shift_end+5). Flaga OFF → krotka BAJT-IDENTYCZNA (3-elem.) jak dawne inline
    → zero zmian d2-picka + walidacja at#152 nietknięta. ON → 4-elem. (jednorodne w
    obrębie jednego min(), bo flaga stała na czas selekcji)."""
    r6 = objm(c, "objm_r6_breach_max_min")
    base = (r6 if r6 is not None else 9e9,
            objm(c, "late_pickup_committed_max") or 0.0,
            objm(c, "new_pickup_late_min") or 0.0)
    if C.decision_flag("ENABLE_POST_SHIFT_OVERRUN_PENALTY"):
        v = objm(c, "post_shift_overrun_penalty")
        return ((v if v is not None else 0.0),) + base
    return base


def bucket(c, *, is_informed, is_blind_empty, is_pre_shift, bucket_fn=None):
    """Bucket pozycyjny: informed→0, blind-empty/pre-shift→2, reszta→1.
    Klasyfikatory wstrzykiwane (funkcje przyjmujące kandydata).
    `bucket_fn` (opcjonalny, 2026-06-24) = JEDNO źródło prawdy bucketa z pipeline
    (`_selection_bucket`, equal-treatment-aware: no_gps/pre_shift po score gdy ON). Gdy
    podany — używany WPROST (spójność z główną selekcją); inaczej klasyfikatory (stare)."""
    if bucket_fn is not None:
        return bucket_fn(c)
    if is_informed(c):
        return 0
    if is_blind_empty(c) or is_pre_shift(c):
        return 2
    return 1


def group_of(feasible, winner, *, late_pickup_tier, is_informed, is_blind_empty,
             is_pre_shift, bucket_fn=None):
    """Kandydaci z `feasible` w tej samej grupie (late_pickup_tier, bucket) co `winner`."""
    def _tb(c):
        return (late_pickup_tier(c),
                bucket(c, is_informed=is_informed, is_blind_empty=is_blind_empty,
                       is_pre_shift=is_pre_shift, bucket_fn=bucket_fn))
    w_tb = _tb(winner)
    return [c for c in feasible if _tb(c) == w_tb]


def pick(feasible, *, late_pickup_tier, is_informed, is_blind_empty, is_pre_shift,
         bucket_fn=None):
    """Kandydat min(lex_qual) w grupie tier×bucket zwycięzcy score (feasible[0]).
    Zwraca feasible[0] gdy pusta grupa, None gdy puste `feasible`. `min` jest stabilny
    (pierwszy z najmniejszym kluczem) — kolejność `feasible` zachowana jak w dawnym inline.
    `bucket_fn` (opcjonalny) — equal-treatment-aware bucket z pipeline (patrz `bucket`)."""
    if not feasible:
        return None
    w0 = feasible[0]
    grp = group_of(feasible, w0, late_pickup_tier=late_pickup_tier,
                   is_informed=is_informed, is_blind_empty=is_blind_empty,
                   is_pre_shift=is_pre_shift, bucket_fn=bucket_fn)
    return min(grp, key=lex_qual) if grp else w0
