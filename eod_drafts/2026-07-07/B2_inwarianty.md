# B2 — Dozbrojenie inwariantów-strażników (higiena/stabilność, dług P3)

**Data:** 2026-07-07 · **Agent:** B2 (higiena/stabilność) · **Gałąź:** `worktree-agent-aa67d97afe1632707` (worktree izolowany, ADR-007)
**Zakres:** DODANO regression-guards istniejącego POPRAWNEGO zachowania. **ZERO zmian silnika, ZERO flipów, flags.json nietknięty, brak restartów/Telegrama, brak merge do master.**

## Wynik jednym zdaniem
+3 strażniki (12 testów) na najważniejsze puste SEAMY w klasach FEASIBILITY/ALOKACJA — wszystkie ZIELONE na żywym kodzie (worktree + kanon), każdy z executed mutation-probe (RED przy regresji). Pełna regresja `pytest tests/`: **4396 passed** (baseline 4384 → +12), **0 NOWYCH failów**.

---

## Kluczowe ustalenie metodyczne (dlaczego tylko 3, nie 21)
Zadanie zakładało „~21 pustych slotów". **Weryfikacja na żywym repo pokazała, że dashboard `ZIOMEK_INVARIANTS.md` jest MIEJSCAMI NIEAKTUALNY** — spory kawał „🔴 SLOT" w moich obszarach priorytetowych JEST już uzbrojony pod innymi nazwami (fale L6.C/L7.x, po dacie ostatniej aktualizacji dashboardu):

| Slot w dashboardzie (🔴) | Realny stan | Strażnik |
|---|---|---|
| INV-LAYER-HARD-BEFORE-SOFT (EMIT, pełny) | **armed** (L7.3, ENABLE_SPLIT_LAYER_GUARD LIVE) | `test_split_layer_guard_l73` |
| INV-LAYER-NO-VERDICT-OUTSIDE-L5 | **armed** (L7.3, setter z gardą warstwy) | `test_split_layer_guard_l73` |
| INV-COH-R-DECLARED (chokepoint zapisu) | **armed** (L7.1 tripwire w state_machine) | `test_r_declared_tripwire_l71` |
| lex_qual K2 geometria (klucz) | **armed** | `test_l6c_geometry_claim` |
| R6/SLA HARD-bramki (behawioralnie, mutation-kills) | **armed** | `test_feasibility_guards_behavioral` |
| carried-first / no-return (ścieżka LIVE plan_recheck) | **armed** | `test_no_return_to_departed_pickup`, `test_carried_first_relax` |
| INV-COH-R-DECLARED (siostrzany `_assert_` w selekcji) | 🔴 xfail-RATCHET (wymaga ZMIANY silnika) | `test_invariant_slots_l04` SLOT 4 |
| INV-LAYER-HARD-BEFORE-SOFT (re-assert po FEAS_CARRY_READMIT) | 🔴 xfail-RATCHET (wymaga ZMIANY silnika) | `test_invariant_slots_l04` SLOT 5 |
| INV-SRC-EQUAL-TREATMENT / INV-LIFE-LOADPLAN-PURE | 🔴 xfail-RATCHET (wymaga ZMIANY silnika) | `test_invariant_slots_l04` SLOT 1/2 |
| INV-SEM-COUPLED-WRITE (delivery_coords ↔ addr, near-miss 484269) | **armed** | `test_delivered_sink_guard_2026_06_13` |

Wniosek: mandat „regression-guard istniejącego POPRAWNEGO zachowania, BEZ zmian silnika" wyklucza sloty xfail-RATCHET (kodują zachowanie JESZCZE NIEpoprawne → padłyby / wymagają fali silnika). Zostały **realnie puste SEAMY** w obszarach priorytetowych — je uzbroiłem. Jakość ponad ilość (Z2).

---

## Dodane strażniki (slot → test → co chroni → co by złamało)

### 1. INV-FEAS-R6-ONE-SOURCE (część: spójność DIAL-a) — `tests/test_inv_r6_dial_family.py` (4 testy)
- **Chroni:** rodzina TWARDYCH progów termicznych 35-min trzyma JEDEN dial: `BAG_TIME_HARD_MAX_MIN == DEFAULT_SLA_MINUTES == C2_PER_ORDER_THRESHOLD_MIN == O2_OVERAGE_CAP_MIN` (=35), a mechanizmy z INNYCH osi są oddzielone: eskalacja-3 `BEST_EFFORT_OBJM_NEW_ORDER_CAP_MIN` (40) DISTINCT/luźniejsza, `HARD_TIER_BAG_CAP` = oś liczby zleceń (≠35).
- **Co by złamało (mutation-probe wykonany, RED):** podbicie jednego dialu termicznego bez bliźniaków (np. SLA 35→40) → `len(set)≠1` RED; zrównanie eskalacji 40→35 z dialem → assert „distinct/looser" RED; wpisanie 35 do bag-size cap → RED.
- **Realna wartość:** dial-drift = bramka ≠ scoring/instrument = cichy rozjazd (dokładnie klasa bugów inwariantu). ⚠ **Referowany w dashboardzie „🟢 `test_overage_cap_equals_engine_dial`" NIE ISTNIAŁ w repo** — slot był naprawdę pusty.

### 2. INV-SRC-LEXQUAL / K2 (część: subordynacja SELEKTORA) — `tests/test_inv_lexqual_geometry_group_subordination.py` (4 testy)
- **Chroni:** geometria (SOFT tie-break `deliv_spread_km`, K2 żywy od 05.07) rozstrzyga WYŁĄCZNIE w obrębie grupy tier×bucket zwycięzcy score w `objm_lexr6.pick()`; kandydat z GORSZEGO tieru/bucketu z idealną geometrią I lepszym R6 NIE wygrywa (HARD > SOFT na warstwie selekcji, INV-LAYER-5).
- **Co by złamało (mutation-probe wykonany, RED):** gdyby `pick()` liczyło `min(lex_qual)` po CAŁYM `feasible` (bez `group_of(winner)`), kandydat z tier-2 z lepszą geometrią+czasem wygrałby → RED. Dowód: un-grouped `min` daje B, `pick()` (grouped) trzyma A.
- **Realna wartość:** uzupełnia `test_l6c_geometry_claim` (pilnuje KLUCZA lex_qual) o kontrakt SELEKTORA (grupa) — bezpośrednio „SOFT nie osłabia HARD" na warstwie selekcji K2.

### 3. carried-first (część: silnikowa ścieżka sticky) — `tests/test_inv_carried_first_lock_first.py` (4 testy)
- **Chroni:** `route_simulator_v2._sticky_sequence_plan` z niepustym workiem (`lock_first`) NIGDY nie enumeruje sekwencji z nowym ODBIOREM (ani dostawą, gdy new już odebrany) na czole trasy — „kurier z jedzeniem nie zawraca do nowej restauracji" (Z-RULE, „kryminał" 2026-06-13). Bliźniak silnikowy do `plan_recheck._coalesce_same_pickup_nodes` (pokrytego osobno).
- **Co by złamało (mutation-probe wykonany, RED):** usunięcie `if lock_first and p_pos == 0: continue` → sekwencja `[new_pickup, …]` (zawrót) → RED; usunięcie drugiego `continue` → dostawa new na czole → RED; zahardkodowanie `lock_first=True` → test pustego worka (lock warunkowy) RED.
- **Realna wartość:** ścieżka sticky (V3.19d saved-plans) była pusta w testach; carried-first było strzeżone tylko na renderze/plan_recheck, nie na SILNIKOWEJ enumeracji.

---

## Dowody (protokół #0, ETAP 4)
- **Zielone na żywym kodzie:** 12/12 pod harnessem worktree (pkgroot symlink, ADR-007) ORAZ 12/12 przeciw KANONOWI silnika (`ZIOMEK_SCRIPTS_ROOT` unset). Determinizm: flaga geometrii pinowana monkeypatchem (nie flags.json), stałe/funkcje wstrzykiwane, OSRM/coords niepotrzebne (stuby).
- **Mutation-probe (C11 oracle) — wykonany skryptem, wszystkie 3 guardy RED przy symulowanej regresji.** (skrypt jednorazowy w scratchpad, nie commitowany).
- **Pełna regresja `pytest tests/`:** baseline 4384 passed / 29 failed → z moimi plikami **4396 passed / 29 failed** (+12 passed, **zbiór failów IDENTYCZNY** = 0 nowych regresji). 27 skipped, 7 xfailed, 2 xpassed.
- **⚠ Nota o 29 „failed":** to ARTEFAKTY harnessu worktree (dual-path import przez symlink pkgroot) — te same pliki przechodzą ZIELONO przeciw kanonowi (zweryfikowane: `test_courier_reliability.py` 8/8 PASS kanonicznie vs 8/8 FAIL pod symlinkiem). NIE są pre-existing failami silnika ani skutkiem mojej zmiany (baseline miał je bez moich plików). Realny post-merge sygnał = kanon, tam czysto.

## Które sloty ZOSTAJĄ 🔴 i DLACZEGO
- **INV-COH-R-DECLARED (siostrzany `_assert_r_declared_time` w selekcji), INV-LAYER-HARD-BEFORE-SOFT (re-assert po FEAS_CARRY_READMIT), INV-SRC-EQUAL-TREATMENT (dryf bliźniaka `_SYNTH_POS`), INV-LIFE-LOADPLAN-PURE (default→pure-read):** xfail-RATCHET w `test_invariant_slots_l04` — kodują zachowanie JESZCZE NIEpoprawne, zdjęcie xfail = FALA ZMIANY SILNIKA (poza mandatem „bez zmian silnika").
- **INV-SRC-ROUTE-ORDER / INV-TWIN-ROUTE-ORDER (①/③):** w toku u sesji tmux 15 (deadline 07-10), rdzeń route-order — nie ruszam (ADR-007: rdzeń seryjnie, jeden właściciel).
- **INV-FLAG-REGISTRY (④), INV-TRUTH (⑤), INV-SEM-ETA-SPLIT (⑥), INV-COH-CLAMP-CHOKEPOINT (⑧):** strukturalne/duże (112 flag poza rejestrem; join z GPS-truth; 1-pole-2-role; 13 klastrów clampów) — wymagają zmiany silnika lub żywych danych, nie mieszczą się w „regression-guard istniejącego poprawnego zachowania".
- **INV-FEAS-R6-ONE-SOURCE (strukturalne 1-źródło L6.B2):** uzbroiłem SPÓJNOŚĆ wartości dialu; strukturalna unifikacja do jednego `sla_anchor` nadal otwarta (fala silnika).

## Pliki (bezwzględne ścieżki, w worktree)
- `tests/test_inv_r6_dial_family.py`
- `tests/test_inv_lexqual_geometry_group_subordination.py`
- `tests/test_inv_carried_first_lock_first.py`
- `ZIOMEK_INVARIANTS.md` (odhaczenie 3 seamów + nota o slotach STALE)
- `eod_drafts/2026-07-07/B2_inwarianty.md` (ten raport)

## Rekomendacje (poza mandatem B2)
1. Reklasyfikacja slotów STALE w `ZIOMEK_INVARIANTS.md` (INV-LAYER ②, INV-COH-R-DECLARED ⑧) → 🟢 z linkiem do L7.x — po weryfikacji/ACK (nie ruszam kanonu samowolnie).
2. INV-FEAS-R6-ONE-SOURCE strukturalne: jeden `sla_anchor` czytany przez wszystkie 4 dziury termiczne (fala serial, feasibility↔route_simulator↔plan_recheck RAZEM).
