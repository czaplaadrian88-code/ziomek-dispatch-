# A360-T0 TEST-TRUTH — niezależny odbiór commita `4e782e8`

Data: 2026-07-11 UTC

Tryb: offline/test-tooling; zero operacji live

Branch: `review/a360-t0-test-truth`

Werdykt: **ACCEPT — brak udowodnionej luki w `4e782e8`**

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
5. Nie znaleziono zmiany zachowania runtime ani relacji HARD/SOFT.

## Niezależne dowody

- zmienione testy: **33 passed** DEFAULT;
- zmienione testy STRICT: **32 passed, 1 skipped** — wyłącznie jawny live-smoke;
- guard + registry/lifecycle cluster: **44 passed**;
- lifecycle checker repo-hermetic: **504/504 curated, 0 błędów**;
- pełna suita DEFAULT: **4851 passed, 24 skipped, 10 xfailed, 0 failed**;
- pełna suita `HERMETIC_STRICT=1`: **4801 passed, 74 skipped, 10 xfailed,
  0 failed**;
- `py_compile`, JSON i `git diff --check`: PASS.

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

## Ryzyka, rollback i live

- Zastane ostrzeżenia pytest oraz historyczne skip/xfail są poza zakresem T0;
  oceniono listę, nie samą sumę.
- Nie udowodniono podstawy do dodatkowego fixu; zgodnie z zakresem nie dodano
  zmian kodu ani testów ponad `4e782e8`.
- Rollback przed merge: nie scalać `4e782e8`. Po merge: `git revert 4e782e8`.
- Nie wykonano flipa, migracji, zapisu runtime, restartu, deployu ani odczytu
  sekretów. Shared backlog i repo pamięci pozostają dla integratora G0.
