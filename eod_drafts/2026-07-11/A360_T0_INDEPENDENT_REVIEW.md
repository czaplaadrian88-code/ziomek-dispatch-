# A360-T0 TEST-TRUTH — niezależny odbiór commita `4e782e8`

Data: 2026-07-11 UTC

Tryb: offline/test-tooling; zero operacji live

Branch: `review/a360-t0-test-truth`

Werdykt: **CONDITIONAL ACCEPT / HOLD MERGE** — dwie udowodnione luki TEST-12
naprawione; końcowe 0-fail wymaga disposition równoległej integracji Sprintu 3

## Zakres odbioru

Odbiór wykonano względem bazy `70af4fa`. Zweryfikowano pełny diff 11 plików:
narzędzie rejestru flag, lifecycle metadata/checker, testy TEST-11, pięć klas
TEST-12 oraz jeden dokładny wpis kwarantanny dla jawnego live-smoke. Diff nie
dotyka `core/`, feasibility, scoringu, selekcji, planu, `flags.json`, runtime
state ani usług.

## Ustalenia

1. TEST-11 używa jawnych syntetycznych wejść `common.py`, `flags.json`, katalogu
   systemd i code-roots. Domyślne ścieżki hosta pozostają wyłącznie trybem
   operatorskim/live-smoke.
2. Pozostały carrier `USE_V2_PARSER` jest raportowany jako
   `json-overrides-env/open`; nieaktualny wyjątek `known-open` został usunięty
   po migracji i flipie z 2026-07-10.
3. Pięć klas TEST-12 dostało deterministyczne fixture aliasów, tierów,
   `kurier_ids` i identity. W STRICT testy wykonują się i przechodzą — nie
   zostały ukryte kwarantanną.
4. Kwarantanna dostała tylko dokładny nodeid jawnego read-only smoke
   `test_flag_registry_f3.py::test_build_registry_smoke_live`, z konkretnym
   powodem. HERMETIC-GUARD nie został osłabiony.
5. Szczegółowa kontrola anty-prod ujawniła dwie luki zamaskowane przez fail-soft:
   `test_resolve_cid_score_based` próbował pisać żywy
   `courier_match_debug.jsonl` poza lokalnymi context managerami, a
   `test_v320_packs_ghost` próbował drenować produkcyjny lockfile
   `coordinator_time_recheck`. HERMETIC-GUARD blokował oba zapisy, lecz kod
   testowany połykał wyjątki, więc testy pozostawały zielone.
6. Fix-forward kieruje oba efekty do procesowych katalogów tymczasowych. Wszystkie
   pięć TEST-12 ma jawny tripwire efektywnej ścieżki/fixture anty-prod.
7. Nie znaleziono zmiany zachowania runtime ani relacji HARD/SOFT.

## Niezależne dowody

- zmienione testy: **33 passed** DEFAULT;
- zmienione testy STRICT: **32 passed, 1 skipped** — wyłącznie jawny live-smoke;
- guard + registry/lifecycle cluster: **44 passed**;
- lifecycle checker repo-hermetic: **504/504 curated, 0 błędów**;
- przed zewnętrznym driftem nośnika pełna suita DEFAULT: **4851 passed,
  24 skipped, 10 xfailed, 0 failed**;
- przed zewnętrznym driftem nośnika pełna suita `HERMETIC_STRICT=1`:
  **4801 passed, 74 skipped, 10 xfailed, 0 failed**;
- `py_compile`, JSON i `git diff --check`: PASS.
- pięć TEST-12 z `ZIOMEK_SCRIPTS_ROOT` wskazującym worktree bez sąsiedniego
  `flags.json` i bez produkcyjnego `dispatch_state`: **5 passed**.

Pełne wyniki odtworzono z rozdzieleniem:
`PYTHONPATH=/root/a360_t0_wt` dla kodu worktree oraz
`ZIOMEK_SCRIPTS_ROOT=/root/.openclaw/workspace/scripts` dla współdzielonego,
read-only configu zgodnie z kontraktem harnessu.

## Kontrola negatywna

Po potwierdzeniu czystego worktree tymczasowo zmieniono
`build_registry(... flags_path=...)`, aby ignorował jawny `flags_path`.
`test_post_migration_json_overrides_env_is_open` przeszedł GREEN -> RED:
oczekiwany wpis `USE_V2_PARSER` zniknął z issues. Mutację cofnięto odwrotnym
patchem; test wrócił do GREEN, a worktree był czysty przed zapisaniem raportu.

Dla każdego z pięciu TEST-12 wykonano osobny probe przez usunięcie/przekierowanie
jego fixture na produkcyjny nośnik. Każdy przeszedł GREEN -> RED w STRICT:

- aliasy CID: odczyt żywego `kurier_ids.json` został zablokowany;
- tiery: żywy `courier_tiers.json` stał się niedostępny i oracle loadera padł;
- packs ghost: brak interceptora `kurier_ids` wywołał RED;
- oba parsery manual overrides: wyłączenie kompletu trzech źródeł identity
  wywołało odpowiednio 12 i 25 faili wewnętrznych.

Probe aliasów odsłonił dodatkowo próby zapisu debug-logu, a probe packs ghost —
próby zapisu lockfile'a force-recheck. Po fix-forward i odwróceniu wszystkich
mutacji DEFAULT, STRICT oraz STRICT bez kanonicznego config/state dały **5/5**.

## Zewnętrzny drift podczas odbioru

Po fix-forward pełna suita zobaczyła nowy klucz
`ENABLE_STAGE_TIMING_OBSERVATION` we współdzielonym `flags.json`. Klucz nie
istniał podczas pierwszych pełnych zielonych przebiegów i należy do integracji
Sprintu 3 w tmux 60. Zamrożony kod T0 nie zawiera jeszcze jego rejestracji w
`ETAP4_DECISION_FLAGS`, więc ratchet poprawnie czerwieni:

- natywny DEFAULT po fixie: **4850 passed, 1 failed, 24 skipped, 10 xfailed**;
- natywny STRICT po fixie: **4800 passed, 1 failed, 74 skipped, 10 xfailed**;
- jedyny fail w obu: `test_no_new_unstripped_flags_ratchet` z dokładnie jednym
  nowym kluczem `ENABLE_STAGE_TIMING_OBSERVATION`;
- po diagnostycznym wyłączeniu wyłącznie tego zewnętrznego nodeidu:
  DEFAULT **4850 passed, 0 failed, 1 deselected**; STRICT **4800 passed,
  0 failed, 1 deselected**.

Lane T0 nie ma prawa edytować tego testu, `common.py`, współdzielonego
`flags.json` ani worktree Sprintu 3. Integrator musi najpierw przyjąć albo cofnąć
carrier Sprintu 3, a następnie powtórzyć pełny DEFAULT+STRICT. Do tego czasu
merge T0 pozostaje HOLD mimo braku drugiej regresji.

## Ryzyka, rollback i live

- Zastane ostrzeżenia pytest oraz historyczne skip/xfail są poza zakresem T0;
  oceniono listę, nie samą sumę.
- Fix-forward dotyka wyłącznie pięciu dozwolonych plików TEST-12; kod produkcyjny,
  rejestr, lifecycle, kwarantanna i runtime pozostają nietknięte.
- Nie naprawiano ani nie maskowano zewnętrznego ratcheta Sprintu 3; jego
  disposition należy do tmux 60/integratora.
- Rollback przed merge: nie scalać lane'a. Po merge: revert commita fix-forward
  z tego odbioru, a dla całego T0 dodatkowo `git revert 4e782e8`.
- Nie wykonano flipa, migracji, zapisu runtime, restartu, deployu ani odczytu
  sekretów. Shared backlog i repo pamięci pozostają dla integratora G0.
