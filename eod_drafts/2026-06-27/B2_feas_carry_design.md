# B2 (#483000) — feasible-path carry-aware re-admit — DESIGN (ETAP 0-3)

Data: 2026-06-27. ACK Adrian: build+replay; flip ON jeśli replay dowodzi progresu.
Cap nowego ordera = **40 min (Tier-3 cap-stretch)** — reuse istniejącej stałej
`BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN=40` (common.py:2533). Tier 1/2 trzymają R6=35 HARD.

## ETAP 0 — stan na żywo (zweryfikowany 27.06 ~21:10 UTC)
- git HEAD `65dadcd` (doc bug1), brak kolizji sesji.
- Baseline pytest: **3378 pass / 13 fail (pre-existing)** / 26 skip. 13 faili NIE dotyczy B2
  (courier_reliability ×8, flag_doc_coverage, objm_lexr6_select_faza2::flag_default_off,
  working_override ×3). Zapisane jako baseline.
- Shadow `ENABLE_FEAS_CARRY_BLIND_SHADOW=true` (flags.json) pod `dispatch-shadow`.
- Korpus `feas_carry_blind_shadow.jsonl` = 894 rek; FRESH n=596 redirect 55.5%
  regret med 11.6 min (baseline 25.06: 38.2%, n=178). Sygnał trzyma i rośnie.

## ETAP 1 — źródło (warstwa-przyczyna)
`feasibility_v2.check_feasibility_v2`:
- L1192-1209 `SLA_PREEXISTING_BYPASS`: wybacza najgorszy breach gdy NIESIONY (picked_up
  drop przed nowym pickupem, no detour). n_blocking==0 → continue; n_blocking>0 → HARD NO.
- L1212-1219 `R6_per_order_>35min`: assigned-not-picked / new_order >35 → HARD NO.
ASYMETRIA: carrying-forgiven przeżywa, blocking-na-nowym ginie → pula feasible bywa
GORSZY ocalały, lepszy (carry-inclusive) wycięty. Selekcja objm_lexr6 NIE łapie (działa
na niepustej feasible, bramka już wycięła lepszego). To jest #483000.

## ETAP 2 — HARD vs SOFT (P0)
Bramka = HARD (P0 feasibility-przed-scoring). NIE ruszamy WERDYKTU bramki (NO zostaje NO
dla downstream HARD-konsumentów). Zamiast tego: warstwa SELEKCJI dostaje carry-aware
re-admit (mirror best_effort), cap 40 = TWARDY sufit Tier-3. Nowy order >40 NIGDY nie
wraca. Tier 1 (czysta feasible) ma pierwszeństwo; re-admit wchodzi tylko gdy carry-inclusive
zwycięzca z (feasible ∪ readmit) ≠ feasible-only zwycięzca. Zgodne z R6/R-35MIN (HARD nietknięte)
i R-FLEET-LEVEL (lepszy flotowo carry-inclusive). Brak inwersji P-1..P-7 do cofnięcia.

## ETAP 3 — MAPA KOMPLETNOŚCI (klasa: selekcja/feasibility carry — bliźniaki RAZEM)
| Miejsce | Dotknięte? | Co |
|---|---|---|
| `_best_effort_objm_pick` (dispatch_pipeline:633) | N-D (wzorzec) | źródło prawdy lex_qual+cap — B2 mirror, NIE duplikat |
| `check_feasibility_v2` gate (feasibility_v2:1192-1219) | TAK (obserw.) | emit marker `feas_carry_readmit_eligible` + new-order bag-time (bez zmiany verdict) |
| selekcja feasible-path (assess_order, gdzie składana pula+pick) | TAK | re-admit pool: blocking-NO z new≤cap → carry-inclusive pick vs feasible-best, za flagą |
| greedy `route_simulator_v2` (sla_violations sort) | N-D + powód | nie bramkuje carry-asymetrią — sortuje plany 1 kuriera, nie pulę kandydatów |
| `plan_recheck` re-seq (sla_violations key) | N-D + powód | re-sekwencja 1 kuriera, nie selekcja puli — poza zakresem #483000 |
| serializer A+B `shadow_dispatcher` | TAK | prefix `feas_carry_` w `_AUTO_PROP_PREFIXES` LUB LOCATION A+B + test w jsonl |
| nowa flaga decyzyjna `ENABLE_FEAS_CARRY_READMIT` | TAK | ETAP4_DECISION_FLAGS + stała OFF + decision_flag/flag + 3 checkery + test ON≠OFF |
| stała cap | N-D (reuse) | `BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN=40` — ta sama co best_effort (parytet) |
| `_objm_lexr6_shadow` (zamrożony baseline) | N-D | nie ruszać |

## ETAP 4/5 — dowody (do wykonania)
- flaga ON≠OFF (test); metryka `feas_carry_readmit_*` w shadow_decisions.jsonl (assert);
  parytet z best_effort cap (test); pełna regresja vs baseline 3378; e2e assess_order.
- ETAP 5 replay ON↔OFF na korpusie 894: metryka docelowa = R6/spread/late floty NIE gorsze
  + carry-regret ↓ (≥20% materialność, NETTO). Pareto (nowy-order-late vs carry-late).
  Dowód POZYTYWNEGO wpływu → wtedy flip ON (ACK Adrian: „jak pewność progresu, flaga on").

## ETAP 6/7 — deploy/rollback
.bak→py_compile→test kanoniczny→git log -3→commit jawne pliki→restart dispatch-shadow.
Rollback: flaga=false (hot) / .bak / git revert.
