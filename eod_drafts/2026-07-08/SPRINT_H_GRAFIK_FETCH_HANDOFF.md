# HANDOFF — SPRINT H (NAPRAWA) „Czerwony test bazowy: `test_grafik_fetch_schedule[fetch]` (bug H1 None-sort)"
**Sesja: tmux 43. Baseline: master `8760ee6`.**
**Worktree (TYLKO TU): `/root/.openclaw/workspace/scripts/wt-grafik-fetch` (branch `fix/grafik-fetch-nonesort`).**

## 0. PROTOKÓŁ #0
Wklej `memory/ziomek-change-protocol.md`. ETAP 0: cd do worktree → `pytest tests/` → **potwierdź, że jedyny FAIL to `test_grafik_fetch_schedule[fetch]`** (reszta zielona; pkgroot-symlink gotcha jak w [[sprint-perf-p95-ortools-det-2026-07-08]]). Fix U ŹRÓDŁA. Żaden restart/flip bez ACK.

## 1. PROBLEM
Baseline ma **1 czerwony test od dni**: `test_grafik_fetch_schedule[fetch]` — pada na KANONIE, nie-TZ. Klasa: **live↔staged drift `fetch_schedule.py`, None-sort** (gdy `now(None)` = naive UTC = to, co robi serwer w bugu „H1", sortowanie po polu None wybucha / daje zły wynik). Czerwony baseline **brudzi regresję KAŻDEGO sprintu** (wszyscy muszą tłumaczyć „1 pre-existing fail") — to dług do domknięcia u źródła.

## 2. CEL
- **H1:** zdiagnozuj None-sort w `fetch_schedule.py` (scripts-root; `dispatch_v2/../fetch_schedule.py`) — gdzie sort po polu, które bywa None przy `now(None)` naive UTC.
- **H2:** **fix u źródła** — sort None-bezpieczny / normalizacja czasu (spójnie z resztą: ZoneInfo/aware) tak, by zachowanie było poprawne ORAZ deterministyczne. Zsynchronizuj live↔staged (jeśli plik ma 2 kopie/drift — bliźniaki RAZEM).
- **H3:** test `test_grafik_fetch_schedule[fetch]` **ZIELONY**, bez osłabiania asercji (napraw kod, nie test).

## 3. ZAKRES / GRANICE
**WOLNO:** `fetch_schedule.py` (scripts-root; sprawdź czy jest bliźniak/staged — napraw OBA), `tests/test_grafik_fetch_schedule.py` (tylko jeśli asercja sama jest błędna — domyślnie NIE ruszaj asercji), docs.
**⚠ WATCHPOINT — grafik = domena współdzielona:** `fetch_schedule.py` bywa ruszane przez sesje grafiku/panelu ([[feedback-multisession-shared-deploy]]). Commit po jawnych ścieżkach, backup przed nadpisaniem, NIE cofaj cudzego. (Żywe teraz: 34=koordynacja, 41=F coords, 42=I cod-weekly.) Jeśli wykryjesz świeży cudzy diff w `fetch_schedule.py` — STOP i pytaj Adriana.
**⛔ NIE WOLNO:** silnik decyzyjny (solver/feasibility/route/pipeline/ETA), panel GRF-02 editor, `flags.json`, restart usług.

## 4. DoD
1. `pytest tests/` **0 FAIL** (czerwony test naprawiony u źródła; reszta bez regresji).
2. Fix None-sort udokumentowany + (jeśli drift) bliźniaki zsynchronizowane.
3. Commit PRZED końcem. Merge sekwencyjny po ACK. Raport `eod_drafts/2026-07-08/S_H_GRAFIK_FETCH_raport.md`.

## 5. WĄTPLIWOŚĆ / cudzy diff w grafiku → PYTAJ ADRIANA.
