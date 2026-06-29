# R6BREACH-01 / GATE-02 — post-selekcyjny guard R6 (DESIGN, shadow-first)

**Data:** 2026-06-05 · **Front D / #1 ROI w audycie 06-03** · **Status: DESIGN — czeka na ACK przed edycją**

## Problem (root cause, zweryfikowany w żywym kodzie)

Zwycięzca propozycji = `feasible[0]` po wszystkich passach selekcji (`dispatch_pipeline.py`
~3549→3620). R6 (35-min hard od odbioru do dostawy) jest egzekwowane dla worków **TYLKO
jako SOFT** — kara w score (`_r6_soft_penalty`, `bonus_r6_soft_pen`) + objective
(`OBJ_R6_SOFT_DEADLINE`). Kandydat z wysokim score bazowym (bliskość/tier) **może wygrać
mimo `r6_max_bag_time_min > 35`**, nawet gdy w puli jest feasible kandydat ≤35.

**Empiria audytu:** 11,6% worków łamie R6 (vs 2,2% solo = 5,3×); **126 worków łamiących R6
idzie jako PROPOSE**; ~14% breach / 79 przypadków na 10 dni. Łamie R-35MIN-MAX (jedna z dwóch
nienaruszalnych reguł — [[feedback-two-hard-rules-defer-over-extend]]).

**Czego NIE robią istniejące mechanizmy:** soft-penalty/objective tylko *karzą w score* (da się
przebić); late-pickup tiering sortuje po *committed odbiorze* (nie po breachu DOSTAWY);
`best_effort_r6_redirect` działa tylko gdy **brak** feasible (best_effort) — nie gdy feasible
zwycięzca breachuje a obok jest czysty. **GATE-02 jest addytywny, nie duplikuje.**

## Fakty kodu (zweryfikowane, read-only)

- `BAG_TIME_HARD_MAX_MIN = 35` (`common.py:393`).
- `metrics["r6_max_bag_time_min"]` ustawiane dla **każdego** feasible kandydata (`feasibility_v2.py:857`).
- Wzorzec architektoniczny 1:1 = `SELECTION_VETO_SHADOW` (`dispatch_pipeline.py:3719`, helper
  `_selection_veto_winner:554`): liczy „co by wybrał alternatywny klucz", serializuje top-level,
  **NIGDY nie mutuje** `feasible`/`best`.
- Attach shadow-dictów: `_result_pf.<field> = ...` (~4116-4121). Serializacja: `getattr(result,
  "<field>", None)` w `shadow_dispatcher.py` (~676-700). Pola shadow są dołączane dynamicznie
  (brak `__slots__`) — mirror tej konwencji.

## Rozwiązanie — FAZA 1 (SHADOW, log-only, ZERO zmiany zachowania)

**Pure helper** (mirror `_selection_veto_winner`, testowalny):
```
_r6_breach_guard_winner(feasible, hard_max_min) -> (guard_winner, changed, reason, n_clean_alts)
  live = feasible[0]; lr6 = live.metrics["r6_max_bag_time_min"]
  if lr6 is None or lr6 <= hard_max:        -> (live, False, "live_within_r6", 0)
  clean = [c != live z r6_max_bag_time_min <= hard_max]
  if not clean:                              -> (live, False, "no_clean_alt", 0)
  guard = max(clean, key=score)              -> (guard, True, "r6_guard_applied", len(clean))
```

**Blok shadow** w `assess_order` zaraz po `SELECTION_VETO_SHADOW` (~3770), gated
`ENABLE_R6_BREACH_GUARD_SHADOW` (env, default OFF), try/except defensywny. Serializuje
`r6_breach_guard_shadow`: `changed`, `reason`, `n_clean_alts`, live_winner (cid/score/r6/bag_size/
pos_source), guard_winner (j.w.), `score_gap = live.score - guard.score`. Log `R6_BREACH_GUARD_SHADOW`.

**Wpięcie:** `_result_pf.r6_breach_guard_shadow = r6_breach_guard_shadow` (~4117) + 1 linia
`getattr` w `shadow_dispatcher.py` (~700). **Flaga** w `common.py` (env default "0", mirror veto).

**Pliki:** `common.py` (flaga), `dispatch_pipeline.py` (helper + blok + attach), `shadow_dispatcher.py`
(serializacja), `tests/test_r6_breach_guard_shadow.py` (8+ przypadków). `.bak` na 3 plikach.

## Walidacja przed jakimkolwiek live (FAZA 2 — osobny ACK)

Po kilku peakach z `ENABLE_R6_BREACH_GUARD_SHADOW=1`: replay `shadow_decisions.jsonl` →
changed-rate, jak często czysty ≤35 alt istnieje gdy zwycięzca breach, **co jest targetem swapu
(solo vs mniejszy worek vs inny worek)**, rozkład `score_gap`, `bag_size`. Dopiero wtedy decyzja
o live-flip (osobna flaga `ENABLE_R6_BREACH_GUARD`, osobny ACK; przy braku czystego alt → ścieżka
„odrocz odbiór / re-propozycja", nie KOORD-cisza, spójna z ALWAYS-PROPOSE).

## Ryzyko / rollback

FAZA 1 = log-only, flaga OFF → **zero wpływu na propozycje** do czasu dodania env w override.conf.
Rollback: usuń env z override.conf + restart dispatch-shadow (lub `.bak`). `dispatch-telegram`
NIE dotykany. Zgodne z Z2 (root cause + shadow-first) i Z3 (mirror sprawdzonego wzorca).
