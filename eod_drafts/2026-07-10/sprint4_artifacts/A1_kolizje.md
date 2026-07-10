# A1 — MAPA KOLIZJI SESJI (Sprint 4, dispatch_v2) — READ-ONLY

Data: 2026-07-10. Agent A1 (read-only mapper). Kanon: `/root/.openclaw/workspace/scripts/dispatch_v2`.
Kanon master **przesunął się c2bde58 → 3c43573** (commit docs Sprintu 2) i został **wypchnięty do origin/master**.
Baza worktree Sprint 3 i Sprint 4 = **c2bde58** — zawiera JUŻ kod Sprintu 2 (a384d46 retry/DLQ + c2bde58 FSM);
brakuje tylko commita docs 3c43573. Wszystkie 4 worktree S4 (identity/flags/hermetic/integration) = c2bde58, CZYSTE.

---

## A. TABELA: sesja → temat → pliki (EDIT vs READ) → ryzyko dla Sprint 4

| Sesja | Typ | Temat / stan | Pliki EDIT | Pliki READ | Repo | Ryzyko S4 |
|---|---|---|---|---|---|---|
| 34 | bash | martwa (claude exited, prompt bash) | — | — | — | **ŻADNE** |
| 35 | bash | martwa (claude exited, prompt bash) | — | — | — | **ŻADNE** |
| 44 | claude | Handover/dokumentacja serwera — **ZAKOŃCZONA/idle** | `/root/handover/{CO_TRZEBA_ZROBIC,MAPA_WIEDZY}.md`, `/etc/nginx` (git), ew. `MEMORY.md`/`/root/CLAUDE.md` | courier-app (smoke), Papu `restaurant_map.json` | poza dispatch_v2 | **ŻADNE** (tylko wspólny index memory) |
| 49 | bash | martwa (claude exited, prompt bash) | — | — | — | **ŻADNE** |
| 50 | claude | Panel grafik GRF-02 — **BLOKADA: limit tyg. do 12.07 10:00**, w połowie edycji | `nadajesz_clone/panel/frontend-shared/src/features/schedule/ScheduleBuilder.tsx`, panel backend flags | — | `nadajesz_clone/panel` | **ŻADNE** (inny repo) |
| 52 | codex | **ORCHESTRATOR** — zbriefował sesję 55 (lidera S4) przez tmux send-keys. Nowy pending prompt: „najobszerniejszy audyt Ziomka z max agentami" | dotąd read-only recon + send-keys→55 | recon całego dispatch_v2 | dispatch_v2 (read) | **SZARE** — audyt może odpalić wielu agentów r/w na tych samych plikach; teraz read-only |
| 53 | codex | **Sprint 3** ETA/SLA ground-truth + stage tracing/backpressure + OSRM/cache. **PRZED 1. EDYCJĄ, 0 commitów, worktree czysty** | (DOMNIEMANE, patrz §C-uwagi) | eta_truth_map.py, decision_outcomes.py, ledger_io.py, gps_delivery_validation_review.py, courier_ground_truth.py, osrm_client.py, shadow_dispatcher.py, dispatch_pipeline.py, candidates.py, selection.py, route_simulator_v2.py, feasibility_v2.py, features.py, evaluate.py, tests/test_osrm_table03_cache.py, tests/test_perf_budget_slo.py | sprint3_wt | **ŚREDNIE** — write-set rozłączny z S4 z wyjątkiem `tests/conftest.py` (ryzyko fixture) i `courier_ground_truth.py` |
| 54 | codex | **Sprint 2** retry/auth/FSM — **ZAKOŃCZONA, scommitowana + PUSHED do origin/master, IDLE** | (na master) event_bus.py, event_retry.py, migrations/event_retry_metadata.py, order_fsm.py, panel_watcher.py, parcel_lane_merge.py, replay_dead_letter.py, state_machine.py, tests/{test_event_retry_phase_a,test_order_fsm_zp101,test_parcel_lane_merge}.py, docs/{ARCHITECTURE,CODEMAP}.md, eod_drafts/2026-07-10/SPRINT2_*.md; courier_api auth; memory sprint_timeline.md+todo_master.md | — | dispatch_v2 + courier_api | **NISKIE** — zamknięte i na master; S4 dostaje przy rebase |
| 55 | claude | **Sprint 4 LEAD (nasz)** — Fable5/max/auto, robi mapę kolizji (Etap 0), 0 edycji | — | recon | sprint4_wt | — (to my) |
| codex | codex | świeży install codex, „Summarize recent commits" — trywialne, idle | — | read | — | **ŻADNE** |

---

## B. SKONSOLIDOWANA LISTA CHRONIONA (Sprint 4 NIE edytuje)

**Sprint 2 — na master, ZAMKNIĘTE (dostaniesz przy rebase, nie ruszać):**
- `event_bus.py`, `event_retry.py`, `migrations/event_retry_metadata.py`, `order_fsm.py`, `panel_watcher.py`,
  `parcel_lane_merge.py`, `replay_dead_letter.py`, `state_machine.py`
- `tests/test_event_retry_phase_a.py`, `tests/test_order_fsm_zp101.py`, `tests/test_parcel_lane_merge.py`
- `docs/ARCHITECTURE.md`, `docs/CODEMAP.md` (świeżo scommitowane w 3c43573)

**Sprint 3 — PRZED 1. edycją, DOMNIEMANY write-set (traktować jak chronione):**
- `eta_truth_map.py`, `decision_outcomes.py`, `ledger_io.py`, `gps_delivery_validation_review.py`,
  `courier_ground_truth.py`, `osrm_client.py`, `shadow_dispatcher.py`, `dispatch_pipeline.py`
- nowe/edytowane testy wokół: OSRM cache, perf budget, ETA truth, stage timing

**Kanon working tree — CUDZE niescommitowane (NIE stage'ować, NIE edytować):**
- `daily_accounting/kurier_full_names.json` (zastana zmiana użytkownika)
- `eod_drafts/2026-07-10/CLAIM_LEDGER_HARD_GATE_CARD.md` (praca sesji claim-ledger / invariants)

## LISTA SZARA (niepewne → traktować jak chronione)
- **`tests/conftest.py`** — S4 chce APPEND; Sprint 3 (przed edycją) tworzy nowe testy, może dodać fixture. Ostatni commit 2026-07-03, teraz nie churnowany, ale to jedyny wspólny plik-styk S3↔S4.
- **`courier_resolver.py`, `courier_ground_truth.py`** — domena „identity" już częściowo tu żyje; `courier_ground_truth.py` jest wprost w read/edit-secie Sprintu 3. Jeśli identity/ musi się wpiąć — NIE edytować tych plików.
- **`tests/test_hermetic_gate.py`** (+ 2× `*.bak-pre-hermetic-2026-06-21`) — istniejący „hermetic gate"; S4 hermetic ma być świadomy, nie nadpisywać. Nietknięty przez żywe sesje.
- **rejestr flag** (`flags.json` / `LOGIC_REFERENCE.md` / moduł flag) — jeśli flag-lifecycle musi dotknąć centralnego rejestru; Sprint 2 dołożył flagi FSM/retry, przyszłe sprinty też. Per plan S4=additive, więc OK.
- **index memory** (`MEMORY.md`, `sprint_timeline.md`, `todo_master.md`) — sesja 54 właśnie je zmieniła, sesja 44 dotykała MEMORY.md; dopiski S4 additywne, uważać na konflikt.

---

## C. WERDYKT — planowane ścieżki Sprint 4 (per ścieżka TAK/NIE koliduje + dowód)

1. **`dispatch_v2/identity/**` (nowy pakiet) — NIE koliduje.**
   Dowód: nie istnieje w kanonie (`ls` → brak) ani w żadnym z 15 worktree (sweep „clean of sprint4 paths");
   `grep courier_identity` = 0 trafień. Żadna żywa sesja nie tworzy identity/.
   ⚠ ZASTRZEŻENIE: NIE wpinać przez edycję `courier_ground_truth.py` (Sprint 3) ani `courier_resolver.py`.
   Pakiet additywny; wiring w silnik odłożyć na integrację / przez nowy adaptor.

2. **`tools/flag_lifecycle*` + `docs/flags/**` — NIE koliduje.**
   Dowód: `ls tools/` brak `flag_lifecycle*`, `docs/flags` nie istnieje; `grep flag_lifecycle` = 0. Żadna sesja nie pisze tam.
   ⚠ Jeśli lifecycle-checker MUSI edytować centralny rejestr flag (flags.json/LOGIC_REFERENCE) → strefa szara, rób additywnie.

3. **`tests/_hermetic*/hermetic*` — NIE koliduje (nazwa wolna).**
   Dowód: brak plików `_hermetic*`/`hermetic*` w tests/; `grep hermetic_guard` = 0.
   ⚠ Istnieje `tests/test_hermetic_gate.py` (2026-06-24) — INNY plik, nie kolizja nazwy; nie dotyka go żadna żywa sesja. Bądź świadomy istniejącego gate.

4. **`dispatch_v2/conftest.py` (root, nowy) — NIE koliduje jako plik.**
   Dowód: brak w kanonie i we wszystkich worktree.
   ⚠ Ma zasięg pytest dla CAŁEGO repo — przy merge do master wpłynie na suity Sprint 2/3 (baseline S3 = 4710 passed). Sekwencjonować integrację, nie zaskoczyć zielonego baseline S3.

5. **append do `tests/conftest.py` — SZARE / potencjalna kolizja.**
   Dowód: plik istnieje (17KB), ostatni commit 2026-07-03 (teraz nie churnowany); Sprint 2 go NIE ruszał;
   Sprint 3 przed 1. edycją, w transkrypcie NIE czytał conftest.py, ale tworzy nowe testy → nie można wykluczyć fixture.
   REKOMENDACJA: zamiast append do wspólnego pliku użyj **root-level `dispatch_v2/conftest.py`** (Twój, nowy) lub
   conftest w podkatalogu testów S4. Jeśli append konieczny — minimalny, na końcu pliku, i **uzgodnij z sesją 53 przed merge**.

---

## D. ANOMALIE

1. ⚠ **Sesja 54 (Sprint 2) commituje i PUSHUJE na origin/master PODCZAS sprintów.** Master ruszył c2bde58→3c43573 (obie repo: ziomek-dispatch + courier_api pushed). Baza S3/S4 (c2bde58) jest 1 commit za origin/master, ale kod Sprintu 2 jest już w c2bde58, więc S4 go ma. Rekomendacja: przy integracji **rebase S4 na origin/master (3c43573)** — różnica to tylko docs.
2. ⚠ **Kanon working tree = 2 niescommitowane CUDZE pliki:** `daily_accounting/kurier_full_names.json` (user) + `eod_drafts/2026-07-10/CLAIM_LEDGER_HARD_GATE_CARD.md` (claim-ledger). Żaden agent S4 nie może ich stage'ować (sesja 54 świadomie pominęła).
3. ⚠ **Sesja 52 (orchestrator) ma świeży pending: wielki audyt Ziomka z max agentami** — może wkrótce odpalić wielu agentów r/w na całym dispatch_v2. Wildcard nakładający się na S3/S4. Lider S4 powinien zsynchronizować się z sesją 52 zanim odpali pełny zespół pisarzy.
4. Sesja 50 (panel grafik) trafiła w limit tygodniowy (reset 12.07) w połowie edycji `ScheduleBuilder.tsx` — zamrożona, inny repo, zero wpływu na dispatch_v2.
5. Sub-agenci sesji 53 („waits for agents") — Sprint 3 ma własnych subagentów piszących w sprint3_wt; ich dokładny write-set niewidoczny w transkrypcie → write-set S3 = DOMNIEMANY z 3 root-cause'ów (ETA report miesza klik/fizykę + leakage; OSRM health uznaje fallback za sukces; cache kasuje wpisy przez pełny sort pod wspólnym lockiem).
6. Świeże cudze backupy w kanonie (2 szt., 2026-07-09): `geocoding.py.bak-pre-pin-memory-fallback`, `common.py.bak-pre-pin-memory-fallback` — z już-scommitowanego fixu geocode (9ab4592). Nieaktywne, bez związku ze ścieżkami S4.

## PODSUMOWANIE JEDNYM ZDANIEM
Wszystkie 5 planowanych ścieżek S4 jest **wolnych** (identity/, tools/flag_lifecycle*, docs/flags/**, tests/_hermetic*, root conftest.py); **jedyny realny styk to append do `tests/conftest.py` z sesją 53** — użyj własnego conftest zamiast wspólnego; nie edytuj `courier_ground_truth.py`/`courier_resolver.py` (Sprint 3 + rdzeń) ani plików Sprintu 2 na master.
