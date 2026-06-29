# NOTKA dla sesji 18 (anti-lie / runtime-oracle audyt) — od sesji napraw, 2026-06-28

**Po co:** robisz reconcile findingów z `ZIOMEK_PLAN_NAPRAW_I_PRIORYTETY`. Trzy pozycje z tego planu
są JUŻ ZROBIONE (commit + test + measure-first), żebyś nie zgłaszał ich jako otwarte i nie pisał
sprzecznych fixów na te same miejsca. Wszystkie na `master`, repo współdzielone.

## ZROBIONE (na master, twoje fixy budują się na tym)
1. **F3 Step1** — `b32ef55` (tag `f3-hardrule-flags-etap4-2026-06-28`). Finding #5
   (`tests-etap4-registry-drift`): 6 LIVE flag TWARDYCH reguł → `ETAP4_DECISION_FLAGS` (42→48) +
   3 testy ON≠OFF. Runtime-neutralne. **Step2 (reszta dryfu ~68 + allowlista) ZOSTAJE — twoja domena
   „B ślepota" może to wyliczyć.**
2. **F2 Step1** — `1ed9ad7` (tag `f2-hardmetric-serialize-2026-06-28`). Finding
   `metser-post-shift-overrun` (P2) + `metser-end-of-day-salvage`: 2 prefiksy
   (`post_shift_overrun_`, `end_of_day_salvage`) → `_AUTO_PROP_PREFIXES` (twin A+B). **F2 Step2
   (batch `c2_/d2_/sla_violations_/shift_remaining_/r3_soft_`) ZOSTAJE — to wprost twoja „B ślepota".**
3. **B2** — `42ca8ae` (tag `b2-e2-equal-treatment-bucket-2026-06-28`). Finding
   `twin-pln-pure-resort-stale-bucket` (P2, „D kłamliwe źródła prawdy"): stale inline `_bucket` w
   `_pln_pure_resort` (LIVE) + `_objm_lexr6_shadow` (SHADOW) → wspólny `_selection_bucket`.
   Measure-first replay 10d: 49/378 E2-arm pick-flip, 100% przeciw no_gps/pre_shift.

## ⏳ NIE zdeployowane jeszcze (kod na master, czeka restart)
- **JEDEN restart `dispatch-shadow` OFF-PEAK** deployuje F2+B2 razem. Jeśli Ty go zrobisz pierwszy —
  weź pod uwagę że wciągnie F2+B2 (intencjonalne, ACK Adriana). Verify: `grep post_shift_overrun
  scripts/logs/shadow_decisions.jsonl` >0 (F2) + B2 pick-flip.

## Gdzie pełny status
`memory/ziomek-deep-audit-2026-06-27.md` → sekcja „STATUS NAPRAW (live)".

## Prośba
Jak twój audyt runtime-oracle wyliczy konkretne „ślepe/kłamliwe" miejsca dla **F2 Step2** i
**bliźniaków D1** (route divergence app↔konsola — faseta #3 już LIVE, ale enumeracja innych) —
zostaw je w spec; sesja napraw je weźmie po reconcile (nie dublujmy).
