# HANDOFF — SPRINT F (NAPRAWA) „Źródło (0,0)/null koordynatów — COORD_GUARD łapie ~25/2h"
**Sesja: tmux 41. Baseline: master `8760ee6` (kanon ~4448/0 + 1 pre-existing grafik-fail = NIE Twój).**
**Worktree (TYLKO TU): `/root/.openclaw/workspace/scripts/wt-zero-coords` (branch `fix/zero-coords-source`).**

## 0. PROTOKÓŁ #0
Wklej `memory/ziomek-change-protocol.md`. ETAP 0: cd do worktree → **zielony baseline `pytest tests/` ZANIM zmieniasz** (⚠ test worktree = pkgroot symlink + `ZIOMEK_SCRIPTS_ROOT`; patrz gotchy w [[sprint-perf-p95-ortools-det-2026-07-08]]). Fix **U ŹRÓDŁA**, nie łatka na guardzie. Dowody, nie deklaracje. Żaden flip/restart bez ACK.

## 1. PROBLEM
`COORD_GUARD` (w `common.py`/`osrm_client.py`/`dispatch_pipeline.py`) łapie współrzędne **(0,0)/null** ~25×/2h. Guard = SIATKA BEZPIECZEŃSTWA (objaw), nie lekarstwo. Ktoś/coś **emituje (0,0)** — trzeba znaleźć i naprawić ŹRÓDŁO (ingest/parsowanie pozycji: fetch panelu? parsowanie zamówienia? pozycja kuriera?), żeby guard przestał mieć co łapać.

## 2. CEL
- **F1:** namierz ŹRÓDŁO (0,0)/null — gdzie współrzędna po raz pierwszy powstaje jako (0,0) (log `data_alerts`/COORD_GUARD + trace w górę). Udokumentuj klasę (brak geokodu? puste pole? domyślka?).
- **F2:** **fix u źródła** — nie emituj (0,0): albo poprawny geokod/pozycja, albo jawny „brak pozycji" obsłużony właściwą ścieżką (np. last-known-pos, patrz `courier_resolver`), NIE cichy (0,0). COORD_GUARD ma zostać jako backstop, ale przestać się odpalać na tej klasie.
- **F3:** dowód: liczba trafień COORD_GUARD na tej klasie **spada do ~0** (pomiar przed/po na żywym/replay logu).

## 3. ZAKRES / GRANICE
**WOLNO:** ingest/parsowanie współrzędnych u źródła, `courier_resolver` (jeśli to pozycja kuriera), miejsce emisji (0,0), testy, docs.
**⛔ NIE WOLNO:** logika feasibility/scorer (Sprint B claim-ledger żyje tam), **współbieżność/orkiestracja `dispatch_pipeline`** (to był pas Sprintu D — Ty tykasz TYLKO ścieżkę współrzędnych, nie concurrency), `route_simulator` (read-only), route_order/render (Sprint C), config solvera (Sprint A), ETA/obietnica (kalibracja w cieniu), `flags.json`.

## 4. DoD
1. Regresja `pytest tests/` zielona (≥ baseline, 1 grafik-fail cudzy zostaje).
2. Źródło (0,0) zdiagnozowane + fix u źródła + test (mutation-probe: bez fixu emituje (0,0), z fixem nie).
3. Dowód spadku trafień COORD_GUARD tej klasy → ~0.
4. Commit PRZED końcem. Merge sekwencyjny po ACK. Raport `eod_drafts/2026-07-08/S_F_ZEROCOORDS_raport.md`.

## 5. WĄTPLIWOŚĆ → PYTAJ ADRIANA.
