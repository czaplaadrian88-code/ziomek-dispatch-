# HANDOFF — SPRINT A „PERF pod skalę: ogon p95 + budżet solvera OR-Tools + audyt TZ"
**Sesja-wykonawca: tmux 36. Data: 2026-07-08. Baseline: master `6e1af23` (kanon 4448/0).**
**Twój worktree (PRACUJ TYLKO TU): `/root/.openclaw/workspace/scripts/wt-perf-p95` (branch `perf/p95-ortools`).**

---

## 0. ZANIM COKOLWIEK TKNIESZ — PROTOKÓŁ #0 (obowiązkowy)
Wklej sobie na start `memory/ziomek-change-protocol.md` i przejdź ETAP 0→7. Skróty krytyczne:
- **ETAP 0:** `cd` do swojego worktree → potwierdź stan na żywo → **odpal baseline `pytest tests/` i potwierdź ZIELONE (≈4448/0)** ZANIM cokolwiek zmienisz. Bez zielonego baseline nie ruszasz.
- Fix **U ŹRÓDŁA**, nie łatka. SOFT nie osłabia HARD. **Dowody, nie deklaracje.** Zmiana częściowa = niezakończona.
- **Żaden flip/restart/flags.json bez ACK Adriana.** Ty budujesz i mierzysz w cieniu — flip należy do FLIPMASTERA (osobna sesja).

## 1. CEL (co i po co)
Utwardzić **szybkość i powtarzalność** liczenia decyzji pod obciążeniem — BEZ zmiany samych decyzji (kto dostaje zlecenie / jaki czas obiecujemy = nietknięte).
Trzy zadania:
- **A1 — Pomiar ogona peak p95** liczenia decyzji (osobne źródło niż naprawiony wcześniej p50). Read-only. Cel: wiedzieć gdzie realnie boli w szczycie.
- **A2 — Budżet solvera OR-Tools:** wprowadzić `deterministic_time_limit` (+ rozsądny sufit) zamiast budżetu „na zegarek". Motyw: tmux 31 wykazał ~1,7% podłogi niedeterminizmu replayu z wall-clock OR-Tools. Deterministyczny budżet = ta sama sytuacja → ta sama trasa → czyste dowody parytetu na przyszłość.
- **A3 — Audyt deployu TZ:** potwierdzić, że WSZYSTKIE fixy stref czasowych z FALA-1 są realnie na produkcji przed ~25.10 (część była staged-za-ACK). To audyt/weryfikacja, nie zmiana logiki. Wynik = lista „zdeployowane / brakuje" + rekomendacja.

## 2. ZAKRES PLIKÓW
**WOLNO:** warstwa solvera OR-Tools / greedy (config wywołania solvera), narzędzia pomiarowe w `tools/` (np. nowy `tools/perf_p95_*`), jednostki systemd/config TZ (audyt), dokumenty w `docs/`/`eod_drafts/`.
**NIE WOLNO (twarde granice anty-kolizyjne — patrz §3):**
- ⛔ `route_simulator_v2` — **TYLKO DO ODCZYTU.** Nie modyfikuj (pruning obalony przez tmux 31; to powierzchnia współdzielona ze Sprintem B i ze ścieżką insercji).
- ⛔ feasibility / scorer / `core/{gates,candidates,selection}.py` — należą do przyszłego sprintu insercji, nie tykaj.
- ⛔ Cokolwiek z ETA/obietnicą odbioru/dostawy — **kalibracja ETA pracuje w cieniu** (`eta_calib_*`, timer `dispatch-eta-calibration-tool`). Twój sprint NIE dotyka tej powierzchni.
- ⛔ `flags.json` — nie dotykasz (flip = FLIPMASTER + ACK).

## 3. WATCHPOINTY (dlaczego nie kolidujesz z resztą)
- Kalibracja ETA = cień na własnych plikach; Ty ruszasz solver/config/pomiar → rozłączne.
- Sprint B (inwarianty) siedzi w asercjach feasibility/geometrii. **Jeśli musisz dotknąć `global_allocate`, dotykasz TYLKO stałych configu solvera; asercje Sprintu B to inny region.** Koordynacja: osobne worktree, merge sekwencyjny (nie równolegle na master), commit po jawnych ścieżkach, backup przed nadpisaniem ([[feedback-multisession-shared-deploy]]).

## 4. BEZPIECZEŃSTWO ZMIAN
- A2 (OR-Tools) idzie **za NOWĄ flagą OFF** (np. `ENABLE_ORTOOLS_DET_TIME_LIMIT`, default OFF w kodzie; NIE dopisuj do flags.json). Dowód parytetu ON↔OFF na replayu ZANIM w ogóle zaproponujesz flip. Jeśli decyzje drgają materialnie → NIE flipujemy, zostaje w cieniu.
- A1/A3 = read-only, zero ryzyka.

## 5. DEFINICJA UKOŃCZENIA (DoD — dowody, nie deklaracje)
1. **Regresja pełna** `pytest tests/` z Twojego worktree — ZIELONA (≥4448/0), raport w handoffie.
2. A1: raport ogona p95 (źródło danych + liczby + gdzie boli) w `eod_drafts/2026-07-08/`.
3. A2: flaga OFF; **dowód parytetu ON↔OFF** (replay: ta sama decyzja lub udokumentowana, akceptowalna różnica) + pomiar niedeterminizmu przed/po; jeśli pozytyw → **karta flipu** (nie flipuj sam).
4. A3: tabela audytu TZ (fix → zdeployowany? → rekomendacja) + ewentualne `! bash` gotowe za-ACK.
5. **Commit PRZED końcem** (lekcja: `--force` skasował niezacommitowaną pracę). Push/merge = sekwencyjnie, po ACK.
6. Raport końcowy `eod_drafts/2026-07-08/S_PERF_raport.md`: co zrobione, dowody, co czeka na ACK Adriana.

## 6. GDY WĄTPLIWOŚĆ CO DO PRIORYTETÓW/INWERSJI → PYTAJ ADRIANA, NIE ZGADUJ.
