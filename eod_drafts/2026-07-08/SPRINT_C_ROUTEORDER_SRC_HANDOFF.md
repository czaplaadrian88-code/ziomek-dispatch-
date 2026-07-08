# HANDOFF — SPRINT C „INV-SRC-ROUTE-ORDER: jedno źródło kolejności trasy PRZEZ KONSTRUKCJĘ (nie flagi)"
**Sesja-wykonawca: tmux 38. Data: 2026-07-08. Baseline: master `8a13b77` (kanon ~4448/0).**
**Twój worktree (PRACUJ TYLKO TU): `/root/.openclaw/workspace/scripts/wt-routeorder-src` (branch `quality/inv-src-route-order`).**
**⏰ DEADLINE 07-10.07** — monitor `ziomek_time_route_monitor` wygasa; do tego czasu inwariant ma stać na KONSTRUKCJI, nie na wygasającym monitorze.

---

## 0. PROTOKÓŁ #0 (obowiązkowy)
Wklej `memory/ziomek-change-protocol.md`, ETAP 0→7. **ETAP 0:** cd do worktree → **zielony baseline `pytest tests/` (~4448/0) ZANIM cokolwiek zmienisz.** Fix U ŹRÓDŁA. **MAPA KOMPLETNOŚCI — wszystkie kopie danej klasy, bliźniacze ścieżki RAZEM.** Dowody, nie deklaracje. **Żaden flip/flags.json/drop-in bez ACK Adriana.**

## 1. PROBLEM (fakt z `ZIOMEK_INVARIANTS.md` l.23)
🔴 **INV-SRC-ROUTE-ORDER**: wymaga `proj(silnik)==proj(konsola)==proj(apka)` (równość porządku `[(typ, sorted(order_ids))]`). Dziś parytet jest **0/d** — ale trzymają go **FLAGI trust-canon ON wszędzie, NIE konstrukcja**. Problem KONSTRUKCYJNY: **4 kopie / 3 repa**. Monitor `ziomek_time_route_monitor` **wygasa 10.07** → po nim nic nie pilnuje, a rozdwojenie zostaje. To korzeń „carried-first naprawiane 10×".

## 2. CEL
Zamienić parytet-trzymany-flagami na **parytet-z-konstrukcji + twardy strażnik CI**:
- **C1:** zmapuj WSZYSTKIE miejsca liczące kolejność trasy (silnik `route_order.py`; konsola `panel/.../fleet_state.py:_build_route`; apka render trasy; wszelkie pozostałe kopie). Silnik `route_order.py` = KANON (Sprint 30 już zdedup 3→2).
- **C2:** dociągnij konsumentów do **delegacji do kanonu** (apka dominująca gałąź już delegowała — potwierdź; panel = domknij delegację). Cel: usunąć logikę-kopię, nie dołożyć kolejnej flagi.
- **C3:** **golden-fixture equivalence guard w CI** (test, który pada gdy `proj(silnik)≠proj(konsola)≠proj(apka)`) — to zastępuje wygasający monitor. Uzbrój slot INV-SRC-ROUTE-ORDER w `ZIOMEK_INVARIANTS.md`.

## 3. ZAKRES PLIKÓW
**WOLNO:** `dispatch_v2/route_order.py` (kanon, ostrożnie — Sprint 30 dowiódł 0-diff, nie psuj), konsola/panel render trasy (`fleet_state.py:_build_route`), apka render trasy (delegacja), `ZIOMEK_INVARIANTS.md`, testy golden w `tests/`, docs/eod_drafts.
**NIE WOLNO (granice anty-kolizyjne):**
- ⛔ `route_simulator_v2` — TYLKO ODCZYT.
- ⛔ Inwarianty/kod claim-ledger (Sprint B live-observuje), feasibility/scorer, config solvera OR-Tools (Sprint D), cokolwiek ETA/obietnica (kalibracja w cieniu).
- ⛔ `flags.json` i drop-iny flag app/panel — flip `ENABLE_ROUTE_ORDER_UNIFIED` = **tylko po parytecie + ACK** (nie flipuj sam).

## 4. WATCHPOINTY
- **⚠ REPO+DEPLOY panelu/apki WSPÓLNE z innymi sesjami** ([[feedback-multisession-shared-deploy]]): commit po jawnych ścieżkach, backup przed nadpisaniem, NIE cofaj cudzego live. (Żywe sesje: 34=ja/koordynacja, 39=Sprint D perf-pipeline, 40=Adrian.)
- Sprint D dotyka pipeline'u/kontencji silnika — Ty render/kolejność. Rozłączne. `route_order.py`+`fleet_state.py`=Twoje; pipeline concurrency=jego.

## 5. DoD (dowody)
1. Regresja `pytest tests/` ZIELONA (≥4448/0).
2. Mapa 4 kopii → ile zwiniętych do kanonu / ile zostaje z uzasadnieniem.
3. **Golden-fixture guard w CI** działa: pada gdy wstrzykniesz rozjazd (mutation-probe), przechodzi na parytecie. Slot INV-SRC-ROUTE-ORDER przełączony 🔴→🟢 w `ZIOMEK_INVARIANTS.md`.
4. Jeśli parytet potwierdzony → **karta flipu** `ENABLE_ROUTE_ORDER_UNIFIED` per powierzchnia (app/panel) — flip za ACK, NIE sam.
5. Commit PRZED końcem. Merge sekwencyjny po ACK. Raport `eod_drafts/2026-07-08/S_C_ROUTEORDER_raport.md`.

## 6. WĄTPLIWOŚĆ CO DO PRIORYTETÓW/INWERSJI → PYTAJ ADRIANA.
