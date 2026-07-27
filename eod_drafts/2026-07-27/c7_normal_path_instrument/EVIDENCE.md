# C7 normal-path instrument — evidence 2026-07-27

## Zakres i stan

- Zadanie: log-only `c7_normal_path.v1`, warunek mierzalności przed flipem
  `ENABLE_POST_SHIFT_OVERRUN_PENALTY`.
- Worktree: `/root/worktrees/dispatch_v2/active/20260727-c7-instrument-fable`.
- Branch/base według zlecenia: `feat/c7-normal-path-instrument-20260727`,
  base `e7c0cc2ce`.
- Git nie jest mechanicznie dostępny w sandboxie. Zarówno `git status` jak i
  `git rev-parse HEAD` kończą się:
  `fatal: not a git repository: /root/.openclaw/workspace/scripts/dispatch_v2/.git/worktrees/20260727-c7-instrument-fable`.
- Zero zapisu do żywego stanu, restartu, deployu, flipu, merge, push i commitu.
- Wspólne `todo_master.md`, `sprint_timeline.md` i ledger są poza writable
  rootem sandboxa. Nie zostały zmienione. Gate `audit.c7-post-shift` pozostaje
  poprawnie `WAIT_DATA`, bo instrument nie jest wdrożony i nie zebrał danych.

## Root cause i hook

K1 dowiódł, że dotychczasowy decision log nie zawiera pełnej puli i metryk
potrzebnych do normalnego replayu C7. Hook jest w `dispatch_pipeline.py`
bezpośrednio przed `_select_position_model`: snapshot powstaje na pełnej
ocenionej puli przed `core.selection.select_and_emit` i jego `top[:16]`.
Rzeczywisty wynik jest liczony normalnie, a dopiero potem dwa izolowane
snapshoty przechodzą przez ten sam selektor z thread-local C7 OFF/ON.

Pełna mapa writerów/konsumentów i wyłączeń:
`docs/C7_NORMAL_PATH_INSTRUMENT.md`.

## Zmienione kontrakty

1. `common.py`
   - hot kill-switch `ENABLE_C7_NORMAL_PATH_LOG`, default OFF, ETAP4/fingerprint;
   - race-safe `post_shift_overrun_override`.
2. `c7_normal_path.py`
   - jedyny owner schematu, kopiowania puli, normalizacji C7, dwóch selekcji,
     parity oracle, stage trace, PII allowlist, code SHA/fingerprint i fail-safe.
3. `core/selection.py`
   - opcjonalny trace `score/OBJM/E2`;
   - best-effort low-score i difficult-case gate używają istniejącego
     `_gate_score_excluding_ranking_deltas`, więc C7 wybiera, ale nie zmienia
     verdictu przez rankingową deltę.
4. `auto_proximity_classifier.py`
   - jawne `emit_calibration_shadow=False` dla czystych ramion.
5. `dispatch_pipeline.py`
   - snapshot przed top-N i fail-safe attach po rzeczywistym wyniku.
6. `shadow_dispatcher.py`
   - jeden addytywny consumer w istniejącym decision recordzie; OFF nie zmienia
     shape.
7. `tools/flag_lifecycle_registry.json`
   - wpis flags.json/hot, default OFF, rollback bez restartu.

## RED-first i mutation

Pierwsze uruchomienie nowego testu zakończyło się na kolekcji:

```text
ImportError: cannot import name 'c7_normal_path' from 'dispatch_v2'
```

Po implementacji test mutation wymaga dokładnie wywołań `[False, True]` oraz
różnych zwycięzców przygotowanego przypadku. Usunięcie drugiego selektora,
zamiana ramion albo ciche zaakceptowanie mismatchu czerwieni test.

Focused final:

```text
tests/test_c7_normal_path_instrument.py
21 passed in 0.93s
```

Pokrycie: realny kanoniczny OFF parity; explicit mismatch; realny C7=ON
unieważnia oracle; dwie selekcje; normalizacja snapshotu z C7 ON; thread-local
równoległy OFF/ON; bezprocesowy odczyt SHA z worktree ref; fail-safe; no-PII;
ten sam winner + zmieniony margin/routing;
best-effort/difficult gate ratchet z always-propose; flaga/fingerprint/lifecycle;
funkcjonalny serializer OFF-shape/ON-payload.

## Klaster i narzędzia

Canonical venv jest niedostępny:

```text
/root/.openclaw/venvs/dispatch/bin/python: Permission denied
```

Zgodnie ze zleceniem uruchomiono klaster systemowym Pythonem 3.12 przez
hermetyczny symlink package-root i pusty testowy `flags.json`:

```text
test_c7_normal_path_instrument
test_post_shift_overrun_penalty_2026_06_24
test_objm_lexr6_unify_2026_06_25
test_gate_exclusions_completeness_f2
test_selection_k12
test_auto_proximity_classifier
test_f2_hardmetric_serialization
test_flag_registry_f3
test_flag_lifecycle_zp107
test_flag_effect_coverage

107 passed in 8.83s
```

`test_conftest_flag_strip_guard` nie jest miarodajny z pustym/syntetycznym
`flags.json`, ponieważ jego drugi ratchet wymaga dokładnego żywego zestawu
znanego długu. Nie zmieniono testu ani baseline’u. CTO ma uruchomić go w
kanonicznym harnessie.

Pozostałe bramki:

```text
python3 -m py_compile ...                         PASS
import sześciu dotkniętych modułów               PASS
python3 tools/flag_lifecycle_check.py             PASS, 0 błędów
JSON parse registry + empty fixture               PASS
trailing whitespace ratchet                      PASS
```

`python3 tools/entropy_dashboard.py` wykonano read-only. Narzędzie zgłosiło
`pliki żywego silnika: 0` w tym worktree/sandboxie, więc metryki 1–6/8
pozostały snapshotem `AUDIT-BASELINE`; auto-oracle sentinel pokazał 0. To nie
jest dowód spadku entropii i wymaga ponowienia przez CTO w kanonicznym rootcie.

Repo skill DoD został uruchomiony, ale zatrzymał się przed analizą diffu:

```text
BLAD: 'diff' to ani plik diff, ani ref gitowy z niepustym diffem vs master
(git: rc=128 fatal: not a git repository: .../.git/worktrees/20260727-c7-instrument-fable)
```

To pozostaje obowiązkową bramką CTO po odzyskaniu git metadata; nie jest
zaliczone jako PASS.

Repo skill `handoff` został uruchomiony i zwrócił szablon; sam driver zgodnie
z kontraktem niczego nie zapisuje. Lokalny snapshot awaryjny:
`/tmp/codex_handoff_2026-07-27_2024_c7_normal_path_instrument.md`,
SHA-256 `ee959291bd5cd801911b5608d1cbab98c124b5ebc35e32740a49441167bb13fc`.

Seeder został uruchomiony wymaganym
`tools/flag_lifecycle_seed.py --merge`. Przy ograniczonych źródłach sandboxa
próbował usunąć setki wpisów panel/systemd i zmienić 302 kuracje. Registry
przywrócono bajt-w-bajt z kopii o SHA-256
`29e5c6c18c37a05dccc7a6202977c634046bfab8dc054f0c68c98544bf8e4dd9`;
z wyniku seeda zachowano wyłącznie nowy wpis C7 i następnie go kuratowano.
Finalny checker: 526 flag, 0 błędów.

## Benchmark

Komenda: `tools/benchmark_c7_normal_path.py --iterations 80 --warmup 8`.
Wynik syntetycznego pełnego selektora:

```json
{"iterations":80,"pools":{"8":{"median_ms":1.926,"p95_ms":1.987,"max_ms":2.354},"16":{"median_ms":2.970,"p95_ms":3.335,"max_ms":3.709},"24":{"median_ms":3.540,"p95_ms":3.973,"max_ms":13.005}},"sampling_required":false}
```

Próg sampling-mode dla puli 16: p95 > 5 ms. Nie przekroczono, więc v1 zbiera
pełny korpus. Każdy rekord ma `prepare_ms`, `measurement_ms`, `overhead_ms`.

## Co CTO musi wykonać

1. Zweryfikować branch/base/status i cudzy WIP po odzyskaniu git metadata.
2. Uruchomić tę samą pełną suitę na base `e7c0cc2ce` i na tym worktree:
   `/root/.openclaw/venvs/dispatch/bin/python -m pytest tests/ -q`,
   z poprawnym symlink-package-root, `ZIOMEK_SCRIPTS_ROOT` i `PYTHONPATH`.
3. Porównać nodeidy fail/skip/xfail — nie tylko sumy — oraz uruchomić
   `HERMETIC_STRICT=1`.
4. Uruchomić `git diff --check`, DoD driver i niezależny blind review.
5. Nie merge/push/deploy/flipować bez następnej decyzji. Sam instrument pozostaje
   default OFF.

## Proponowane commity (niewykonane)

1. `test(c7): add red-first normal-path parity and mutation oracle`
   - `tests/test_c7_normal_path_instrument.py`
   - `tests/fixtures/c7_empty_flags.json`
2. `feat(c7): add fail-safe dual canonical normal-path instrument`
   - `c7_normal_path.py`
   - `common.py`
   - `core/selection.py`
   - `auto_proximity_classifier.py`
   - `dispatch_pipeline.py`
   - `shadow_dispatcher.py`
3. `docs(c7): register lifecycle, benchmark and hand off activation hold`
   - `tools/flag_lifecycle_registry.json`
   - `tools/benchmark_c7_normal_path.py`
   - `docs/C7_NORMAL_PATH_INSTRUMENT.md`
   - `ZIOMEK_BACKLOG.md`
   - ten plik

Po każdym commicie CTO powinien podać jawny pathspec; nie używać `git add -A`.
