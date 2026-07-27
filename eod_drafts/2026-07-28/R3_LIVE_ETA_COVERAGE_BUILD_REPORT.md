# R3 — uczciwe pokrycie źródeł live ETA

Status: **KANDYDAT SOURCE-ONLY / HOLD INTEGRATION**

Worktree: `/root/worktrees/dispatch_v2/active/20260728-r3-liveeta-fix-sol`

Branch deklarowany przez zlecenie: `fix/r3-liveeta-coverage-20260728`

Baza deklarowana przez zlecenie: `c18c7a0a8`

## Wynik

Naprawa ustanawia jeden kontrakt per stop:

- `live`: wyłącznie poprawny GPS o wieku `0..120 s`;
- `warm`: wyłącznie poprawny `last_event` o wieku `0..180 s`, za
  `ENABLE_LIVE_ETA_WARM_SOURCE`;
- `planned`: brak świeżej pozycji albo utrata pewnego łańcucha po błędzie stopu;
- stop bez geokodu pozostaje w snapshotcie z `eta_at=null` i
  `unpriced_reason`, nie kasując poprawnych stopów całej trasy.

Flaga jest default OFF. OFF zachowuje legacy snapshot bajt w bajt. Nie zmieniono
`flags.json`, procesów, usług ani żywego stanu.

## Root cause i mapa kompletności

| Miejsce | Rola | Writer / consumer | Dotknięte | Dowód / powód |
|---|---|---|---|---|
| `live_eta_daemon.build_routes` | budowa trasy | writer | TAK | legacy zerował `stops`, gdy jeden stop nie miał koordynatów |
| `live_eta_daemon._source_start` | wybór pozycji i źródła | writer | TAK | wcześniej `_start` ignorował timestamp GPS |
| `plan_recheck._last_event_anchor` | parser realnego zdarzenia | upstream writer/parser | N-D | istniejący kanoniczny parser i mapowanie zdarzenie→koordynaty; reużyty bez duplikowania |
| `live_eta.calculate_live_eta` | wycena i snapshot | writer | TAK | nowa ścieżka per-stop, legacy zachowane dla OFF |
| `live_eta.write_cycle` | atomowy publisher | writer | TAK | przekazuje kontrakt źródła, schema pozostaje addytywna |
| `common` + lifecycle registry | flaga i izolacja testów | writer/consumer | TAK | fallback False, ETAP4 strip, curated lifecycle |
| `tools/live_eta_coverage.py` | pomiar | read-only consumer | TAK | liczy LIVE/WARM/PLANNED/bez wyceny |
| `fleet_state.py`, `canon_eta.py`, courier API/UI | powierzchnie | zewnętrzni konsumenci | N-D | zachowanie pozostawione bez zmian zgodnie ze zleceniem; stare pole `orders` zachowane |
| silnik decyzji `dispatch_v2` | potencjalny nielegalny consumer | consumer | N-D + ratchet | AST test blokuje użycie snapshotu w decyzjach |

Kanonicznym ownerem progów, nazw źródeł i kalkulacji jest `live_eta.py`.
Daemon tylko wybiera wejście według tych stałych i publikuje wynik.

## Dowody RED-first i mutation

- Przed implementacją nowy plik R3: `9 failed, 1 passed`.
- Oracle izolacji: zły środkowy adres, poprawny stop przed i po nim nadal
  wyceniony.
- Oracle świeżości: GPS sprzed 14 h nie jest LIVE; granica 120 s jest domknięta.
- Dodatkowe tripwire: GPS i event z przyszłości nie są LIVE/WARM.
- OFF: dokładny, stabilny JSON legacy jest równy bajtowo.
- ON: WARM pozostaje `source=warm`, nigdy `live`.
- Mutation: przywrócenie all-or-nothing daje pusty snapshot i ponownie różni się
  od oracle.
- Hermetyczność: oba stany flagi pinowane w tymczasowym `flags.json`, bez odczytu
  flag hosta.

## Pomiar przed / po — deterministyczna symulacja

Fixture: trzy stopy jednego kuriera, środkowy pickup bez koordynatów, świeży GPS.

| Stan | Opublikowane | Wycenione | Źródła | Bez wyceny |
|---|---:|---:|---|---:|
| legacy / OFF | 0/3 | 0 | brak | 3 utracone przez all-or-nothing |
| R3 / ON | 3/3 | 2 | LIVE 1, WARM 0, PLANNED 2 | 1 `bad_coords` |

Miernik zwrócił `coverage_priced_pct=66.7`, `invalid_sources=0`.
To jest oracle defektu, nie estymacja dobowej produkcji. Baseline diagnozy dla
rzeczywistego dnia pozostaje: 43,2% stopów wycenionych, 41,1% rzeczywiście
świeżych według użytej w diagnozie granicy; 207 z 272 poprawnych stopów utracono
przez all-or-nothing, a maksymalny wiek zaakceptowanego GPS wyniósł 14 h.

## Testy i checkery

- Końcowy hermetyczny klaster R3 + bliźniaki trasy + lifecycle/doc/strip:
  `89 passed, 1 skipped`.
- Wcześniejszy porównywalny klaster na pustym testowym `flags.json`:
  baseline `30 passed, 3 failed`; po zmianie `42 passed, 3 failed`; lista trzech
  harness-faili bez zmiany, zatem delta regresji produktowych pusta.
- Po zbudowaniu prawidłowego pkgroot z 249 przypiętymi flagami trzy harness-faile
  zniknęły.
- `flag_lifecycle_check.py --skip-external`: 533/533 curated, 0 błędów.
- `flag_doc_coverage_check.py`: brak nowego driftu, baseline debt 64.
- `py_compile`: 4/4 zmienione pliki Python OK.
- Import check: `common`, `live_eta`, `live_eta_daemon`,
  `tools.live_eta_coverage` OK; fallback flagi False.
- Whitespace ratchet dla jawnych plików: brak końcowych spacji.

Pełna kanoniczna suita nie mogła zostać uruchomiona w tym sandboxie:
`/root/.openclaw/venvs/dispatch/bin/python` zwraca `Permission denied`, a systemowy
Python nie ma `ortools`. Metadane worktree `.git` również wskazują do
niedostępnego hostowego repo i każde `git status` kończy się `fatal: not a git
repository`. Jest to mechaniczna bramka CTO przed integracją, nie zielony wynik.

Seeder `tools/flag_lifecycle_seed.py --merge` został uruchomiony, lecz nie miał
dostępu do zewnętrznych światów panel/apka i wygenerował częściowy rejestr.
Wynik został odrzucony; przywrócono zweryfikowany preimage i dodano wyłącznie
kuratorowany wpis R3. Końcowy checker repo-hermetic jest zielony.

## Proponowany podział commitów CTO

Commity nie powstały, ponieważ metadane `.git` są poza sandboxem. Podział z
jawnymi pathspec:

1. `test(R3): pin honest live ETA source contract`
   — `tests/test_live_eta_coverage_r3.py`
2. `fix(R3): isolate per-stop ETA and reject stale positions`
   — `live_eta.py`, `live_eta_daemon.py`
3. `feat(R3): gate warm ETA source and add coverage meter`
   — `common.py`, `tools/flag_lifecycle_registry.json`,
   `tools/live_eta_coverage.py`
4. `docs(R3): record source contract and integration evidence`
   — `ZIOMEK_LOGIC_REFERENCE.md`, `ZIOMEK_BACKLOG.md`,
   `eod_drafts/2026-07-28/R3_LIVE_ETA_COVERAGE_BUILD_REPORT.md`

## Rollback i otwarte bramki

Rollback zachowania: utrzymać/bramkować
`ENABLE_LIVE_ETA_WARM_SOURCE=false`; ścieżka OFF jest przypięta bajtowo.
Rollback źródła po integracji: revert jawnych commitów R3.

Przed integracją:

1. CTO uruchamia pełną kanoniczną suitę w symlink-pkgroot i potwierdza pustą
   deltę względem własnego baseline.
2. CTO wykonuje commity jawnym pathspec i przegląd DoD/diff.
3. Bez porannego ACK ownera nie dodawać/nie flipować klucza w `flags.json`.
4. Przed flipem potwierdzić, że wszystkie powierzchnie renderują WARM osobno,
   ponieważ stare konsumery zachowano celowo bez zmiany.
5. Po ewentualnej aktywacji zebrać minimum 2 dni pomiaru per źródło i dopiero
   wtedy zamknąć gate `engine.live-eta-coverage-r3`.

## Wejście do mechanicznej bramki DoD

regresja: HOLD — pełna suita niedostępna w sandboxie; dostępny klaster 89 passed, 1 skipped

e2e: build_routes → calculate_live_eta → snapshot → coverage tool, 89 passed, 1 skipped

pozytywny-wplyw: ten sam fixture OFF 0/3 priced, ON 2/3 priced i 3/3 poprawnie oznaczone źródło

rollback: flaga `ENABLE_LIVE_ETA_WARM_SOURCE=false`; następnie jawny git revert R3

N-D: fleet_state.py — addytywna schema i jawne polecenie bez zmiany starego konsumenta

N-D: canon_eta.py — addytywna schema i jawne polecenie bez zmiany starego konsumenta

N-D: courier API/UI — osobna poranna weryfikacja etykiety WARM przed flipem
