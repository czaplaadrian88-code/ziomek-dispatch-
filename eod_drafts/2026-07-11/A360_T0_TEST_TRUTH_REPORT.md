# A360-T0 TEST-TRUTH — raport wykonawcy

Data: 2026-07-11 UTC

Tryb: wyłącznie offline/test-tooling; zero operacji live

Branch: `review/a360-t0-test-truth`

Worktree: `/root/a360_t0_wt/dispatch_v2`

Base: `70af4fa` (`audit360-base-20260710`, `origin/master` w chwili startu)

Commit implementacji: `4e782e8`

## Wynik

TEST-11 i TEST-12 z Audytu 360 są domknięte na tej gałęzi:

- semantyczne testy `flag_registry` dostają jawne syntetyczne wejścia:
  `common.py`, `flags.json`, katalog systemd i code-roots;
- raport rejestru pokazuje provenance wejść;
- historyczny wyjątek `USE_V2_PARSER=known-open` został usunięty po migracji i
  flipie 2026-07-10; pozostawiony env-carrier jest jawnie klasyfikowany jako
  `json-overrides-env/open`;
- osobny read-only smoke na nośnikach hosta ma dokładny nodeid i jest pomijany
  wyłącznie w STRICT;
- pięć klas niehermetycznych odczytów TEST-12 używa fixture: aliasów CID,
  tierów, `kurier_ids` panel-watchera i kompletu źródeł identity dla dwóch
  script-testów manual overrides;
- guard hermetyczny nie został osłabiony, a TEST-12 nie trafiły do kwarantanny.

Zachowanie runtime, decyzje, HARD/SOFT, flagi produkcyjne, dane i usługi nie są
zmieniane.

## Mapa kompletności

| Miejsce | Rola | Writer/consumer | Dotknięte | Powód | Dowód |
|---|---|---|---|---|---|
| `tools/flag_registry.py` | mechanizm TEST-11 | czytelnik jawnych źródeł | TAK | DI + provenance, bez ukrytego live fallbacku w testach semantycznych | `test_flag_registry_f3` + mutation-probe |
| lifecycle checker/registry | metadata po flipie parsera | checker + rejestr | TAK | usunięcie nieaktualnego `known_drift` | checker 504/504, 0 błędów |
| `tests/test_flag_registry_f3.py` | oracle TEST-11 | test mechanizmu | TAK | syntetyczne flags/systemd/common + dokładny live-smoke | default/STRICT |
| pięć klas odczytów TEST-12 | oracle hermetyczności | script-testy | TAK | anonimowe, deterministyczne fixture | pełny STRICT 0 failed |
| `tests/hermetic_quarantine.json` | granica live-smoke | kolektor nodeidów | TAK | jeden dokładny wpis rejestru; bez blanket patternu | STRICT: dokładnie 1 nowy skip |
| `core/`, feasibility, selection, plan, serializer | runtime/decisions | produkcja | N-D | sprint test-tooling; brak konsumenta runtime zmiany | diff base..HEAD |
| flags.json, dispatch_state, usługi | live state | produkcja | N-D | zakaz operacji live | brak zapisów/restartów/deployu |

## Testy

Środowisko worktree: kod z `PYTHONPATH=/root/a360_t0_wt`; współdzielony katalog
configu wskazany przez `ZIOMEK_SCRIPTS_ROOT=/root/.openclaw/workspace/scripts`.
Żaden test nie dostał zgody na zapis do produkcji.

- klaster celowany default: **46 passed**;
- klaster celowany STRICT: **45 passed, 1 skipped** — jedyny skip to
  `tests/test_flag_registry_f3.py::test_build_registry_smoke_live`;
- pełna suita default: **4851 passed, 24 skipped, 10 xfailed, 0 failed**;
- pełna suita `HERMETIC_STRICT=1`: **4801 passed, 74 skipped, 10 xfailed,
  0 failed**;
- różnica STRICT względem default: 50 dokładnie raportowanych skipów; naprawione
  TEST-12 biegną i przechodzą;
- `tools/flag_lifecycle_check.py --repo-hermetic`: **504/504 curated, 0 błędów**;
- `py_compile`, oba JSON-y oraz `git diff --check`: PASS.

Pierwszy próbny pełny bieg z błędnym `ZIOMEK_SCRIPTS_ROOT=/root/a360_t0_wt`
został odrzucony jako nieważny: worktree nie zawiera sąsiedniego `flags.json`,
więc starsze testy dostały brak configu. Po ustawieniu poprawnego podziału
`PYTHONPATH=worktree`, `ZIOMEK_SCRIPTS_ROOT=kanoniczny katalog configu` pełne
default i STRICT są zielone. Nie wymagało to zmiany kodu.

## Mutation / negative control

Po zacommitowaniu implementacji tymczasowo zmieniono
`build_registry(... flags_path=...)`, aby ignorował jawny `flags_path`.
`tests/test_flag_registry_f3.py::test_post_migration_json_overrides_env_is_open`
przeszedł GREEN -> RED (`USE_V2_PARSER` zniknął z issues). Mutację cofnięto przez
odwrotny patch; klaster ponownie 46/46, a worktree był czysty przed raportem.

## Ryzyka i rollback

- Zwykła pełna suita nadal używa współdzielonego read-only configu przez istniejący
  kontrakt `tests/conftest.py`; A360-T0 usuwa ukrytą zależność TEST-11 i stanowe
  zależności TEST-12, nie przepisuje całego historycznego harnessu.
- Liczba skipów może zmienić się o testy zegarowe; oceniono pełną listę, nie samą
  sumę.
- Rollback przed merge: nie scalać brancha. Po merge: `git revert 4e782e8` oraz
  revert commita raportowego; restart nie jest potrzebny.

## Operacje live / handoff

Nie wykonano flipa, migracji, zapisu danych runtime, restartu, deployu ani odczytu
sekretów/PII. Enforcement pozostaje bez zmian. Shared backlog i repo pamięci są
celowo nietknięte zgodnie z G0 — aktualizuje je integrator po odbiorze lane'a.
Chronione/cudze dirty pliki w innych worktree pozostały nietknięte.
