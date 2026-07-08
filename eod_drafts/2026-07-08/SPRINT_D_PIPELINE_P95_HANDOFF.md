# HANDOFF — SPRINT D „Ogon peak-p95 z KONTENCJI pipeline'u decyzji (nie solver)"
**Sesja-wykonawca: tmux 39. Data: 2026-07-08. Baseline: master `8a13b77` (kanon ~4448/0).**
**Twój worktree (PRACUJ TYLKO TU): `/root/.openclaw/workspace/scripts/wt-pipeline-p95` (branch `perf/pipeline-contention-p95`).**

---

## 0. PROTOKÓŁ #0 (obowiązkowy)
Wklej `memory/ziomek-change-protocol.md`, ETAP 0→7. **ETAP 0:** cd do worktree → **zielony baseline `pytest tests/` (~4448/0) ZANIM cokolwiek zmienisz.** Fix U ŹRÓDŁA. Dowody, nie deklaracje. **Żaden flip/flags.json/restart bez ACK Adriana.**

## 1. PUNKT WYJŚCIA (dowód Sprintu A, 08.07)
Sprint A (perf/OR-Tools) udowodnił: **ogon peak-p95 czasu decyzji to NIE solver — solver=podłoga.** Realne źródło ogona = **flota/kontencja/współbieżność pipeline'u** (równoległe solve'y konkurują, serializacja etapów, blokady stanu floty). Detal: [[sprint-perf-p95-ortools-det-2026-07-08]] (re-framing A1). ⚠ Sprint A ma gałąź `perf/p95-ortools` NIEzmergowaną (config solvera) — Ty NIE tykasz configu solvera, robisz INNY lewar (kontencja).

## 2. CEL
- **D1 — PROFIL (read-only):** zmierz GDZIE w pipeline'u decyzji formuje się ogon p95 w peaku (równoległe solve'y? lock stanu floty? serializacja etapów? I/O? GC?). Wykorzystaj źródło ogona z A1. Wynik = raport „ogon siedzi w X".
- **D2 — LEWAR (za flagą OFF):** zaproponuj i zbuduj JEDNĄ bezpieczną redukcję kontencji na podstawie D1 (np. rozprzężenie etapu, ograniczenie równoległości, mniejszy lock, kolejkowanie) — **za NOWĄ flagą OFF**, z dowodem parytetu decyzji (współbieżność zmienia czas, NIE wynik).
- Cel biznesowy: silnik trzyma tempo nawet w najgorszych 5% chwil peaku → gotowość na większy wolumen / multi-city.

## 3. ZAKRES PLIKÓW
**WOLNO:** warstwa orkiestracji/współbieżności pipeline'u decyzji silnika, narzędzia pomiarowe `tools/perf_*`, docs/eod_drafts.
**NIE WOLNO (granice anty-kolizyjne):**
- ⛔ **Config solvera OR-Tools / `route_simulator_v2`** — solver=Sprint A (gałąź niezmergowana), route_simulator=TYLKO ODCZYT. Ty ruszasz KONTENCJĘ, nie solver ani symulator.
- ⛔ `route_order.py` i render konsoli/apki (`fleet_state.py:_build_route`) — to Sprint C.
- ⛔ Inwarianty/claim-ledger (Sprint B live), feasibility/scorer, cokolwiek ETA/obietnica (kalibracja w cieniu).
- ⛔ `flags.json` — flip za ACK.

## 4. WATCHPOINTY
- Gałąź z master `8a13b77`. Gdyby Sprint A (perf/p95-ortools) zmergował się w międzyczasie — zrób rebase; **nie edytujecie tych samych plików** (on solver-config, Ty kontencja).
- Sprint C rusza render/kolejność. Rozłączne. Współbieżność zmienia CZAS, nie decyzję — udowodnij parytet.

## 5. DoD (dowody)
1. Regresja `pytest tests/` ZIELONA (≥4448/0).
2. D1: raport profilu ogona p95 (gdzie, ile, dowód) w `eod_drafts/2026-07-08/`.
3. D2: flaga OFF; **dowód parytetu decyzji ON↔OFF** (replay: ta sama decyzja) + pomiar p95 przed/po. Jeśli pozytyw → **karta flipu** (nie flipuj sam). Jeśli lewar nieosiągalny bezpiecznie w tym pasie → **uczciwy negatyw** (jak Sprint 31) + wskazanie realnej dźwigni (vCPU / architektura) — to też wynik.
4. Commit PRZED końcem. Merge sekwencyjny po ACK. Raport `eod_drafts/2026-07-08/S_D_PIPELINE_raport.md`.

## 6. WĄTPLIWOŚĆ CO DO PRIORYTETÓW/INWERSJI → PYTAJ ADRIANA.
