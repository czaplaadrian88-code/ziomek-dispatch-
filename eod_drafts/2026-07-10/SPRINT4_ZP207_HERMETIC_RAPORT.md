# Sprint 4 — Z-P2-07 Hermetyczne testy i fixture — RAPORT

Data: 2026-07-10. Builder: agent D (worktree `sprint4/z-p2-07-hermetic`, baza c2bde58).
Status: **GOTOWE** — DEFAULT identyczny z baseline (poza 2 ujawnionymi prod-writerami),
STRICT 0 failed/0 errors, wszystko zacommitowane. Additywne: aktywacja = 1 plik
`dispatch_v2/conftest.py`; **rollback = usun ten plik**.

## 1. Co zrobione (architektura)

Odciecie suity pytest od zywego stanu hosta na warstwie PRYMITYWOW FS (nie stalych
modulowych — bo 631 hardcode + 30 zamrozonych default-arg ich nie pokryja).

- **Root `dispatch_v2/conftest.py`** — jedyny punkt aktywacji, ladowany PRZED
  `tests/conftest.py` (nie modyfikuje go). Ustawia `DISPATCH_UNDER_PYTEST=1`
  (idempotentnie) + sandbox `DISPATCH_STATE_DIR` (tmp, seed anonim. fixture, tylko
  gdy nieustawiony) + instaluje write/read-guard (autouse session) + hook kwarantanny.
- **`tests/hermetic_support.py`** — czysty klasyfikator `classify()` (DENYLIST: blok
  tylko pod 3 zywymi korzeniami; reszta ALLOW), `install_guard()` patchuje
  `builtins.open` (w/a/x/+), `os.open` (O_WRONLY/RDWR/CREAT/TRUNC/APPEND),
  `os.replace`, `os.rename` (cel=dst) oraz **klase DELETE: `os.unlink`+`os.remove`**
  (Path.unlink idzie przez os.unlink — potwierdzone; kasowanie zywego = mutacja
  produkcji, blok jak zapis). Utwardzenie `realpath(parent)` (lapie dowiazania w
  zywy stan). Fabryka sandboxa + loader kwarantanny.
- **`tests/hermetic_quarantine.json`** — 24 wpisy (jawne, z powodami), matchowane po
  stem/nodeid w `pytest_collection_modifyitems` (zero edycji plikow testow).
- **`tests/test_hermetic_guard_zp207.py`** — 9 testow kontrolnych (DoD).
- **`tests/fixtures/hermetic/*.json`** — 4 anonimowe fixture (dane ZMYSLONE).
- **`docs/HERMETIC_TESTS.md`** — instrukcja trybow.

Zywe korzenie: ZAPIS blok (wszystkie tryby) = `dispatch_state` + `scripts/logs` +
`flags.json`; ODCZYT blok (TYLKO STRICT, per doprecyzowanie lidera) = `dispatch_state`.

## 2. Tryby

| Tryb | Aktywacja | Zachowanie |
|---|---|---|
| DEFAULT | `pytest tests/` | write-guard; kwarantanna live-read biega (marker bez -m nie wyklucza) |
| STRICT | `HERMETIC_STRICT=1 pytest tests/` | write+read-guard(dispatch_state); kwarantanna SKIP |

## 3. Wyniki — DEFAULT vs baseline (cel: 0 failed, identycznosc)

| Bieg | passed | skipped | xfailed | failed | errors |
|---|---|---|---|---|---|
| **Baseline** (bez conftest, ten sam pkgroot) | 4710 | 24 | 10 | **0** | 0 |
| **DEFAULT** (z conftest, finalny) | 4717 | 27 | 10 | **0** | 0 |

Diff non-passed (comm PRE vs POST, po SKIPPED) = **wylacznie +3 SKIPPED** (3 ujawnione
prod-mutatory panel_packs, nizej). Wszystkie 24 bazowe skipy obecne (0 usunietych),
10 xfaili niezmienione (summary). Passed +7 = +10 nowych testow kontrolnych − 3
przeniesione pass→skip.

**Dowod braku zapisu do produkcji:** po pelnym biegu DEFAULT `courier_plans.json`
mtime NIEZMIENIONY (1783675410 przed i po). `find dispatch_state -name 'hermetic_probe_*'
-o -name 'test_panel_packs.*' -o -name '*.tmp'` = **PUSTE** (guard zablokowal kazdy zapis
testu). Swieze mtime na `global_alloc.json`/`panel_packs_cache.json` = zywe serwisy
produkcji (ciagli pisarze), NIE moje testy.

### 3a. ZNALEZIONY prod-writer (guard ujawnil to, co A4 pominal)

`tests/test_panel_packs_signal_v328.py` — helper `_write_packs_cache` robi
`tempfile.mkstemp(dir=ZYWY dispatch_state)` + `os.replace(tmp, panel_packs_cache.json)`
→ **nadpisywal PRODUKCYJNY cache falszywymi danymi** przy kazdym biegu suity.
`PANEL_PACKS_CACHE_PATH` = hardcode, ignoruje `DISPATCH_STATE_DIR`. A4 (grep literalny)
to pominal, bo sciezka budowana przez `dir=`, nie `open('/root/...','w')`.
Trojka testow, jeden root cause (`PANEL_PACKS_CACHE_PATH` hardcode ignoruje `DISPATCH_STATE_DIR`):
- `test_load_cache_fresh`, `test_load_cache_stale` → `mkstemp(dir=live)+os.replace` na ZYWY
  cache → guard ZAPIS BLOKUJE.
- `test_cache_missing_returns_none` → `os.unlink` na ZYWYM cache (kasowanie produkcji) →
  guard klasy DELETE BLOKUJE (dodany w follow-up: `os.unlink`/`os.remove`).
- Wszystkie 3 → **kwarantanna (skip default+strict)** z powodem (matchowane per-nodeid;
  2 czyste testy w tym pliku, `test_max_age_constant`/`test_courier_state_has_panel_packs_fields`,
  biegaja normalnie).
- **Rekomendacja U ZRODLA (owner, obszar panel_watcher — NIE edytujemy)**: pisz/kasuj w tmp /
  honoruj DISPATCH_STATE_DIR → wtedy 3 testy wychodza z kwarantanny.

## 4. Wyniki — STRICT (cel: 0 failed bez dispatch_state)

| Bieg | passed | skipped | xfailed | failed | errors |
|---|---|---|---|---|---|
| STRICT (pre-kwarantanna live-read) | 4668 | 43 | 10 | 21 | 11 |
| **STRICT (finalny, po follow-up DELETE)** | **4668** | **76** | **10** | **0** | **0** |

21 fail + 11 error w 1. biegu = tylko klasa **LIVE READ-ONLY** (read-block na
`dispatch_state` → `FileNotFoundError`, np. `r04_schema.json`, zywe kurier_ids/
courier_names, zywy orders_state/health/log). Wszystkie dopisane do kwarantanny
`modes:["strict"]` (przechodza w DEFAULT — read dozwolony). 76 skip = 24 bazowe + 3
panel_packs (default+strict) + kwarantanna live-read + testy z wlasnym „skip gdy brak
danych" (poprawne zachowanie hermetyczne).
**0 failed / 0 errors → DoD „suita przechodzi bez dispatch_state" spelnione.**

## 5. Kwarantanna (25 wpisow) — kategorie

- **LIVE READ-ONLY, skip TYLKO w STRICT** (biega w DEFAULT): `test_v325_pin_leak_defense`,
  `test_route_order_live_parity`, `test_working_override_2026_06_01` (3 startowe) +
  ujawnione read-only: caly `test_r04_v2_evaluator` (r04_schema.json), 9× pin_gps
  (TestResolveCourier/TestPinCommandHandler/TestGpsInstructionHandler — zywe aliasy),
  3× state_schema_validator (zywy orders_state/baseline), 2× health_all_aggregator,
  test_roadfactor_gap/prep_variance/prep_bias (real_log/real_meta), eta_residual_infer
  (zywy eta store). Matchowane per-nodeid (bez over-skip hermetycznych sasiadow).
- **PROD-MUTATOR ujawniony, skip w DEFAULT+STRICT**: `test_panel_packs_signal_v328`
  ::test_load_cache_fresh/stale (ZAPIS zywego panel_packs_cache.json) +
  ::test_cache_missing_returns_none (DELETE zywego cache).

## 6. Testy kontrolne (dowod DoD) — 10/10 pass w DEFAULT

Pure klasyfikator (write-live BLOCK / logs+flags BLOCK / tmp ALLOW / STRICT read
dispatch_state BLOCK a logs ALLOW). NEGATYW: `plan_manager.save_plan` wycelowany w
PROBE pod zywym dispatch_state → `RuntimeError` HERMETIC-GUARD + plik NIE powstaje
(deterministyczne, bez zaleznosci od mtime produkcji/wyciekow). POZYTYW: ten sam writer
z monkeypatch stalej modulu (C17) → laduje w tmp. FAIL-SOFT: `global_alloc_store.write`
(domyslna zywa sciezka) → zwraca 0, zywy nietkniety. **DELETE: `os.unlink`/`os.remove`/
`Path.unlink` na sonde pod zywym dispatch_state → RAISE; w tmp dziala.** Loader
kwarantanny + rejestracja markera.

## 7. Znane luki (udokumentowane, NIE rozwiazane w tej fazie)

1. **Script-runnery subprocess** — `ScriptRunItem` odpala `python -m dispatch_v2.tests.X`
   w OSOBNYM interpreterze → NIE dziedziczy in-process guarda. Dziedziczy env
   (DISPATCH_UNDER_PYTEST, sandbox DISPATCH_STATE_DIR, stripped DISPATCH_FLAGS_PATH) +
   guardy per-writer (state_machine RAISE, setup_logger, courier_resolver last_pos).
   A4 = 0 literal-write w runnerach. Pelne rozwiazanie (sitecustomize/import-hook) =
   OSOBNA FAZA za ACK.
2. **Primityw DELETE** — `os.unlink`+`os.remove` (w tym `Path.unlink`) OBJETE guardem
   (follow-up). Blast radius zweryfikowany: pelny DEFAULT z guardem DELETE ujawnil TYLKO
   `test_panel_packs...test_cache_missing` (pozostale 47 testow z unlink/remove celuje w
   tmp/monkeypatch). Poza spec zostaje `os.rmdir` (kasowanie katalogu — rzadkie, brak
   przypadkow w suicie).
3. **Dowiazanie LISCIA** — `realpath` tylko RODZICA. Dowiazanie samego pliku-liscia w
   zywy stan nie zlapane (egzotyczne; model zagrozen = literal sciezka produkcyjna).
4. **flags.json symlink w pkgroot** — obecny i poprawny (`$PKG/flags.json ->
   flags.snapshot.json`); zweryfikowany dla baseline I DEFAULT (spojne). Sygnalizowana
   przez lidera „nieobecnosc" byla stanem przejsciowym/nieaktualnym.

## 8. Rollback

`rm dispatch_v2/conftest.py` (1 plik dezaktywuje calosc). Reszta plikow jest bezczynna
bez conftestu (support/quarantine/fixtures nie sa importowane, control-test wymaga
conftestu do przejscia — bez niego by sie nie zebral inaczej).

## 9. Srodowisko biegow

`ZIOMEK_SCRIPTS_ROOT=/root/sprint4_wt/pkgroot_hermetic
/root/.openclaw/venvs/dispatch/bin/python -m pytest tests/ -p no:cacheprovider`
(pkgroot: `dispatch_v2 -> worktree`, `flags.json -> flags.snapshot.json`). pytest 9.0.3.
