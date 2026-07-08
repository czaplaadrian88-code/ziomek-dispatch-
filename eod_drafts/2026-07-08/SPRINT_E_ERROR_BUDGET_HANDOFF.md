# HANDOFF — SPRINT E „Tablica zdrowia + budżet błędu (SLO) — jeden pulpit prawdy o żywych flipach/cieniach"
**Sesja-wykonawca: tmux 40. Data: 2026-07-08. Baseline: master `8a13b77` (kanon ~4448/0).**
**Twój worktree (PRACUJ TYLKO TU): `/root/.openclaw/workspace/scripts/wt-error-budget` (branch `obs/error-budget-scoreboard`).**

---

## 0. PROTOKÓŁ #0 (obowiązkowy)
Wklej `memory/ziomek-change-protocol.md`, ETAP 0→7. **ETAP 0:** cd do worktree → **zielony baseline `pytest tests/` (~4448/0) ZANIM cokolwiek zmienisz.** Dowody, nie deklaracje. **Żaden flip/flags.json/restart/timer-install bez ACK Adriana.**

## 1. PROBLEM (po co)
W tej chwili u Ziomka DOJRZEWA równolegle mnóstwo rzeczy w cieniu/obserwacji: O2-K1 (werdykt ~09.07), K2 geometria, K3 claim-ledger, CHECK claim-ledger (log-loud live), kalibracja ETA (cień), A2 solver, oraz sprinty C (route-order) i D (pipeline). **Sygnały prawdy są porozrzucane po ≥6 logach** — nie ma JEDNEGO miejsca, które mówi „czy to wszystko jest netto na plus i czy budżet błędu się nie pali". Ten sprint to naprawia — **czysto obserwowalnościowo, bez tykania decyzji.**

## 2. CEL
Zbudować **standalone read-only agregator** (`tools/health_scoreboard.py` lub podobny) który:
- **E1 — Zbiera** kluczowe metryki z istniejących logów (READ-ONLY, w `dispatch_state/` chyba że wskazane inaczej):
  - `shadow_decisions.jsonl` (decyzje, KOORD rate, redirecty),
  - `eta_calib_metrics.jsonl` (on-time odbiór/dostawa, MAE, spoznien_pct),
  - `pending_global_resweep.jsonl` (`g_claim_ledger_breaches`, would_repropose),
  - `proposal_churn.log` (flicker %),
  - `night_guard_history.jsonl` (regresja/entropia),
  - `pickup_slip_monitor.jsonl` (poślizg odbioru).
- **E2 — Liczy budżet błędu / SLO burn** per metryka (definicje SLO zaproponuj rozsądnie i UDOKUMENTUJ; np. on-time ≥80%, breaches=0, churn poniżej progu, p95 poniżej progu) — ile budżetu zjedzone, trend.
- **E3 — Gdzie się DA: wpływ ON/OFF żywych flag** (np. porównanie okien przed/po flipie O2-K1/K2/K3/CHECK z logów) — jasno oznacz co jest miarodajne, a co za mało danych (NIE zmyślaj istotności).
- **E4 — Dzienna karta** `dispatch_state/health_scoreboard_card.md` (+ opcjonalnie JSON) z werdyktem 🟢/🟡/🔴 per metryka + „co wymaga uwagi Adriana".

## 3. ZAKRES PLIKÓW
**WOLNO:** NOWY plik/moduł w `tools/`, testy w `tests/`, docs/eod_drafts, zapis WYŁĄCZNIE własnej karty/raportu do `dispatch_state/health_scoreboard_*`.
**NIE WOLNO (granice anty-kolizyjne — czysto czytasz cudze):**
- ⛔ Modyfikować kolektorów/źródeł: `night_guard.py`, resweep, `eta_calibration`, churn monitor — **tylko CZYTASZ ich output**.
- ⛔ route_order/render (Sprint C), pipeline concurrency (Sprint D), config solvera OR-Tools (Sprint A), feasibility/claim-ledger (Sprint B), route_simulator, ETA/obietnica (kalibracja w cieniu).
- ⛔ `flags.json`, instalacja timera/usługi — timer proponujesz, instalacja = ACK Adriana.

## 4. WATCHPOINTY
- Jesteś czystym KONSUMENTEM — Twoja jedyna zmiana stanu to własna karta. Zero mutacji cudzych logów/silnika. To gwarantuje bezkolizyjność z C/D/A/B i cieniem ETA.
- Nie zgaduj istotności statystycznej — gdzie za mało danych, pisz „za mało danych", nie „poprawa".

## 5. DoD (dowody)
1. Regresja `pytest tests/` ZIELONA (≥4448/0) — Twój moduł + testy izolowane.
2. Karta wygenerowana z REALNYCH logów (nie mock) — pokaż przykład w raporcie.
3. Testy: agregacja liczy poprawnie na fixturach (w tym przypadki brzegowe: pusty log, brak pola, okno bez danych).
4. Timer (opcjonalny) = PRZYGOTOWANY, NIE zainstalowany (instalacja za ACK; wzór `dispatch-proposal-churn`).
5. Commit PRZED końcem. Merge sekwencyjny po ACK. Raport `eod_drafts/2026-07-08/S_E_HEALTH_raport.md`.

## 6. WĄTPLIWOŚĆ CO DO PRIORYTETÓW/DEFINICJI SLO → PYTAJ ADRIANA.
