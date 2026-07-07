# S30-A — Route-order UNIFIKACJA: moduł `route_order.py` + TWARDY dowód 0-diff

> **Wykonawca tmux 30** · gałąź `routeorder/unify` (2 worktree: `wt-routeorder`
> dispatch_v2 + `wt-routeorder-courierapi` courier_api) · **flaga OFF, ZERO
> merge/flip/restart** · baseline testów: `4440 passed, 27 skipped, 8 xfailed,
> 2 xpassed` (kanon, pre-zmiana).

## 1. Co powstało (ETAP 1 — źródło, nie objaw)

| # | Plik | Zmiana | Klasa |
|---|---|---|---|
| 1 | `dispatch_v2/route_order.py` | **NOWY** — DOM reguły kolejności (promocja `route_podjazdy`). Logika przeniesiona VERBATIM + `order_route` alias + `repair_dropoffs_after_pickups` (scalony bliźniak) + `build_stop_sequence` (forma kroków dla konsumentów). PURE (stdlib). | kanon/render |
| 2 | `dispatch_v2/route_podjazdy.py` | re-eksport z `route_order` (alias wsteczny — apka/golden/narzędzia bez zmian). **Zero drugiej kopii.** | kanon/render |
| 3 | `dispatch_v2/plan_recheck.py` | `_repair_dropoffs_after_pickups` → **delegacja** do `route_order.repair` (klucz `type`). Bliźniak silnika SCALONY. | render helper |
| 4 | `courier_api/config.py` | flaga `ROUTE_ORDER_UNIFIED` (env `ENABLE_ROUTE_ORDER_UNIFIED`, **OFF**). | flaga (app world) |
| 5 | `courier_api/courier_orders.py` | `_reorder_steps_to_canon` (nowy) + gałęzie 2/3 (`ziomek_plan`, `optimize_route` NN) porządkują wg kanonu gdy flaga ON; OFF = legacy bajt-identyczne. Gałąź 1 (`console_podjazdy`) już delegowała do `route_podjazdy`(=route_order). | selekcja-render (app) |

**Panel (`fleet_state._build_route`) = B, TYLKO PRZYGOTOWANE** (cross-repo, osobny
deploy, sesje równoległe) — patrz `S30B`.

**Bliźniak repair (mapa kompletności):** `route_order.repair` = JEDNO źródło.
- silnik `plan_recheck._repair` → **deleguje** (scalone).
- apka `courier_orders._repair` → **ZOSTAJE lokalny** (świadomie): gałęzie 2/3 to
  fallback, który MUSI działać gdy `dispatch_v2` niedostępny (import route_podjazdy
  fail-loud → apka na lokalnej kopii). Związany testem parytetu (`test_repair_
  parity_vs_both_legacy_fuzz`) — nie może zdryfować. 3 kopie → 2 (silnik zdedupowany,
  apka-fallback test-związana). Fizyczna delegacja apki = follow-up po gwarancji importu.

## 2. DOWÓD 0-DIFF (twardy — `scratchpad/proof_0diff.py`, deterministyczny seed)

Projekcja porównania = `[(typ, sorted(order_ids))]` (ETA/coords/dwell WYŁĄCZONE —
kontrakt parytetu L6.A). Oracle = `route_podjazdy.py @ HEAD` (przed promocją,
sha256 `7a76b45…`, zamrożona kopia).

| Test | Zakres | Wynik |
|---|---|---|
| golden corpus | 25 case (17 syntetyk + 8 żywych z 05.07) — `new==oracle==expected_proj` | **0 rozjazdów** |
| żywe worki | orders_state × 4 kombinacje flag | 0 worków (noc — brak aktywnej floty; pokryte goldenem+fuzzem) |
| **fuzz `order_podjazdy`** | 6000 losowych worków × 4 kombinacje (plan_aware×trust_canon) = **24000** porównań vs oracle | **0 rozjazdów** |
| **fuzz `repair` vs plan_recheck legacy** | 8000 sekwencji przeplatanych (klucz `type`) | **0 rozjazdów** |
| **fuzz `repair` vs courier_orders legacy** | 16000 sekwencji (klucz `kind`, id str+int) | **0 rozjazdów** |
| `build_stop_sequence` == transformacja gałęzi 1 | 25 case | **0 rozjazdów** |
| re-eksport = ta sama funkcja | `RP.order_podjazdy IS RO.order_podjazdy`, repair, PICKUP_MERGE_MIN==10 | ✅ |

**Wniosek:** promocja jest BAJT-IDENTYCZNA (0 diff / 48000+ porównań). „warto" =
jedno źródło (route_order), koniec 4-kopiowego długu konstrukcyjnie (INV-SRC).
„bez regresji" = 0 diff dowiedziony fuzzem + goldenem + oracle @HEAD.

## 3. Semantyka flagi (3 światy — ADR-004)

`ENABLE_ROUTE_ORDER_UNIFIED` — **OFF default, oba światy**:
- **silnik** (`plan_recheck`): repair-twin scalony BEZ flagi (delegacja bajt-identyczna,
  udowodniona) — brak nowej flagi decyzyjnej w common.py/ETAP4/flags.json (świadomie:
  ON==OFF, więc to refaktor, nie decyzja; rollback = git revert).
- **apka** (`courier_api/config.py`, env `ENABLE_ROUTE_ORDER_UNIFIED`): gate'uje
  konwergencję kolejności gałęzi 2/3 (jedyna ZMIANA ZACHOWANIA). OFF = legacy.
- **panel** (przyszłe `PANEL_FLAG_ROUTE_ORDER_UNIFIED`): B, jeszcze niepodpięte.

## 4. Zakres — czego NIE ruszałem (bezkolizyjność)

- **calib_maps / eta_* / predykcja czasów** (tmux 29) — 0 edycji.
- **feasibility_v2-filtr / route_simulator-pruning** (tmux 31) — 0 edycji.
- **`plan_recheck._apply_canon_order_invariants`** (relax/lex/no-return = logika
  DECYZYJNA R6/SLA) — NIE ruszane (task 5). Tknięty TYLKO render-helper
  `_repair_dropoffs_after_pickups` (task 3, jawnie w zakresie).
- ETA/floory/monotonic apki i panelu — zostają lokalne (tylko KOLEJNOŚĆ delegowana).

## 5. Rollback (ETAP 7)

- Flaga app: `ENABLE_ROUTE_ORDER_UNIFIED=0` (default) → gałęzie 2/3 legacy.
- Kod: `git revert` na gałęzi `routeorder/unify` (nic nie zmergowane do master).
- Silnik repair: git revert edycji `plan_recheck.py` (delegacja → lokalne body).
