# S28-B — Kolejne inwarianty: ratchet entropii + reklasyfikacja slotów STALE

**Data:** 2026-07-07 · **Wykonawca:** tmux 28 · **Gałąź:** `s28b-inwarianty` (worktree `wt-s28b`, od `master@d808808`) · **Commit:** `a02b1e8`
**Zakres:** `tests/` + `ZIOMEK_INVARIANTS.md`. ZERO zmian silnika, flag, restartów. **Gotowe do merge.** Kontynuacja B2 (nie dubluję).

## Ustalenie metodyczne (dlaczego 1 nowy strażnik, nie 5)
B2 uzbroił 3 czyste seamy; weryfikacja na żywym repo pokazała, że **czyste seamy „regression-guard bez silnika" są niemal wyczerpane**. Pozostałe 🔴 sloty wymagają fali silnika (już xfail-ratchetowane przez B2 w `test_invariant_slots_l04`: INV-SRC-EQUAL-TREATMENT / INV-LIFE-LOADPLAN-PURE / INV-COH-R-DECLARED-siostrzany / INV-LAYER-re-assert-po-FEAS_CARRY) albo są w toku u tmux 15/27 (route-order) albo strukturalne/duże (clamp 13 klastrów, flag-registry — już ratchetowany conftest-strip). **Jakość ponad ilość (Z2)** — nie forsuję kruchych strażników. Zamiast tego: 1 solidny NOWY ratchet + reklasyfikacja 3 slotów, które dashboard trzymał 🔴 mimo że są armed.

## 1. NOWY strażnik — INV-POS-NO-PRODUCE ratchet entropii (🔴→🟡)
`tests/test_inv_pos_no_produce_ratchet.py` (4 testy). **Meta-reguła „entropia niżej" uczyniona wykonalną:** liczba miejsc-producentów pozycji-placeholder może TYLKO maleć.
- **Baseline zamrożony = 10:** 4× `or (0.0, 0.0)` (fallback braku geokodu, `dispatch_pipeline` 1622/3450/3452/3928) + 6× `.pos = BIALYSTOK_CENTER` (syntetyczna pozycja no_gps/pre_shift, `courier_resolver` 1074/1673/1682/1736/1776/1788). Asercja `<=` (kierunek malejący — usunięcie producenta przez L2.1 flip → obniż baseline).
- **Mutation-probe (RED):** syntetyczny nowy producent (`or (0.0,0.0)` oraz `.pos = BIALYSTOK_CENTER`) → wykryty; **guardy `!= (0.0,0.0)` NIE liczone** (dowód, że ratchet nie łapie obrony); **`.claude/worktrees` pominięte** (lekcja S28-A — inaczej producent sąsiedniej sesji fałszywie przebiłby baseline).
- **Zakres:** blokuje WZROST entropii producentów. NIE eliminuje istniejących — to L2.1 (`ENABLE_COORD_SENTINEL_INGEST_GUARD`, zbudowany, czeka na flip+ACK) + eliminacja fikcji no_gps (osobna fala, filar #3). Stąd 🟡 (nie 🟢).

## 2. Reklasyfikacja 3 slotów STALE 🔴→🟢 (z DOWODEM, nie deklaracją)
Dashboard trzymał 🔴, choć guardy istnieją i mają zęby (potwierdzenie ustalenia ubocznego B2). Zweryfikowane: **24 testy ZIELONE**, każdy z mutation-probe:
| Slot (był 🔴) | Strażnik | Co pilnuje (docstring) |
|---|---|---|
| INV-LAYER-HARD-BEFORE-SOFT (pełny/EMIT) | `test_split_layer_guard_l73` (L7.3) | `_assert_feasibility_first` re-assert na EMIT (`_split_layer_emit_assert`); flaga ON≠OFF bajt-parytet; mutation zdejmujący gardę → RED |
| INV-LAYER-NO-VERDICT-OUTSIDE-L5 | `test_split_layer_guard_l73` (L7.3) | jeden setter `_set_feasibility_verdict` z gardą warstwy; zapis werdyktu poza L5 → log naruszenia |
| INV-COH-R-DECLARED (chokepoint zapisu) | `test_r_declared_tripwire_l71` (L7.1) | `czas_kuriera ≥ czas_odbioru_timestamp` w JEDYNYM funnelu `state_machine.upsert_order`; TZ-naive=Warsaw; mutation kierunku nierówności → RED |

⚠ **Węższe siostrzane części POZOSTAJĄ 🔴** (uczciwie): re-assert PO `FEAS_CARRY_READMIT` + `_assert_r_declared_time` w SELEKCJI = xfail-RATCHET B2 SLOT 4/5 (wymagają fali silnika).

## Dashboard (ZIOMEK_INVARIANTS.md)
② warstwy `3/0/4 → 5/0/2`; ⑧ koherencja `1/0/2 → 2/0/1`; **RAZEM `~27/0/16 → ~30/0/13`**. Dodano 2 adnotacje datowane (S28-B) + nota „czyste seamy bez silnika niemal wyczerpane".

## Dowody (protokół #0 ETAP 4)
- `test_inv_pos_no_produce_ratchet` 4/4 PASS (wt-s28b, ZIOMEK_SCRIPTS_ROOT=pkgroot).
- STALE guardy `test_split_layer_guard_l73` + `test_r_declared_tripwire_l71` = 24 passed.
- **Pełna regresja `pytest tests/`: 4433 passed / 0 failed** (baseline kanon 4429 + 4 nowe ratchet-testy), 27 skipped, 8 xfailed, 2 xpassed.

## Zostaje 🔴 i DLACZEGO (mapa dla fal silnika)
Route-order ①/③ (tmux 15/27, deadline 07-10) · INV-SRC-EQUAL-TREATMENT · INV-FEAS-R6-ONE-SOURCE (strukturalne 1-źródło) · INV-FEAS-NO-DOUBLE-BOOK (K6 de-pile) · INV-LIFE-LOADPLAN-PURE · INV-LIFE-RECANON-PRUNE · INV-COH-CLAMP-CHOKEPOINT (13 klastrów) · INV-SEM-ETA-SPLIT · INV-POS-NO-PRODUCE (pełne 🟢=L2.1 flip). Wszystkie = fala silnika lub w toku u innego lane'u.

## Rollback
`git revert a02b1e8` (same testy + doc). Backup: gałąź niezmergowana.
