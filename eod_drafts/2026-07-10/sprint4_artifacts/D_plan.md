# D (builder Z-P2-07) — RAPORT PLANU (FAZA 1, zero edycji)

Środowisko potwierdzone: worktree `/root/sprint4_wt/wt-hermetic/dispatch_v2`, branch
`sprint4/z-p2-07-hermetic` @ c2bde58, czysty. pytest **9.0.3**. `dispatch_v2/conftest.py`
NIE istnieje (wolne). `pkgroot_hermetic` jeszcze nie ma — tworzę w Fazie 2. Baseline cel:
**4710 passed / 24 skipped / 10 xfailed / 0 failed**.

---

## 1. WRITE-SET (wyłącznie NOWE pliki — zero edycji istniejących)

1. `dispatch_v2/conftest.py` — root conftest (jedyny punkt aktywacji; import PRZED tests/conftest).
2. `dispatch_v2/tests/hermetic_support.py` — implementacja: pure-klasyfikator celu, instalator
   guarda (patch prymitywów), fabryka sandbox-state, loader kwarantanny.
3. `dispatch_v2/tests/hermetic_quarantine.json` — jawna lista live/nonhermetic + powody + tryby.
4. `dispatch_v2/tests/test_hermetic_guard_zp207.py` — testy kontrolne (DoD).
5. `dispatch_v2/tests/fixtures/hermetic/{kurier_ids,courier_names,courier_tiers,orders_state}.json`
   — minimalne ANONIMIZOWANE fixture (zmyślone dane) do seeda sandboxa.
6. `dispatch_v2/docs/HERMETIC_TESTS.md` — instrukcja trybów (DEFAULT/STRICT/opt-out/rollback).
7. `dispatch_v2/eod_drafts/2026-07-10/SPRINT4_ZP207_HERMETIC_RAPORT.md` — raport końcowy.

Nazwy rozłączne z istniejącym `tests/test_hermetic_gate.py` (INNY temat — regression-lock 2 testów;
NIE nadpisuję) i z markerem `nonhermetic` (już zarejestrowany `tests/conftest.py:377` — REUŻYWAM,
nie rejestruję ponownie, by nie dublować linii markera).

---

## 2. PROJEKT GUARDA

### 2a. Root conftest — kolejność aktywacji (import-time → session)
- **import-time (najwcześniej):**
  - `os.environ.setdefault("DISPATCH_UNDER_PYTEST","1")` — idempotentnie; tests/conftest robi to
    samo później → no-op (identyczny wynik).
  - Fabryka sandbox: `tempfile.mkdtemp(prefix="hermetic_state_")` → kopia fixture z
    `tests/fixtures/hermetic/` → `os.environ.setdefault("DISPATCH_STATE_DIR", <sandbox>)`
    (TYLKO jeśli nie ustawiony z zewnątrz). Sandbox pod tmp = poza żywymi korzeniami.
    Best-effort `atexit` rmtree.
- **session autouse fixture** (`scope="session"`) — instaluje WRITE-GUARD przez własną
  `pytest.MonkeyPatch()` i `mp.undo()` na końcu sesji (kolekcja jest read-only per A4, więc guard
  w fazie testów wystarcza).

### 2b. Warstwa PRYMITYWÓW (patchowane — łapie wszystkie 3 klasy ścieżek jednolicie)
Bo 631 hardcode + 30 frozen default-arg → monkeypatch stałej/env NIE pokryje wszystkiego.
- `builtins.open` — tryby `w/a/x/+` = zapis; sam `r`/`rb` = odczyt.
- `os.open` — `flags & (O_WRONLY|O_RDWR|O_CREAT|O_TRUNC|O_APPEND)` = zapis; łapie `tempfile.mkstemp`
  i `Path.touch`.
- `os.replace`, `os.rename` — cel = **arg2 (dst)**; łapie idiom `mkstemp→replace` (dominujący
  atomic-write silnika: plan_manager `_atomic_write`, global_alloc_store, state_machine).
- **shutil.move/copyfile — NIE patchuję** (świadomie): dekomponują się do powyższych prymitywów
  (`copyfile`→`open('wb')`, `move`→`os.rename`/`copy2`), więc już pokryte. Mniejsza powierzchnia.
  Udokumentowane w raporcie.

### 2c. Logika klasyfikacji celu (pure fn `_classify(abspath, is_write, strict) -> ALLOW|BLOCK_WRITE|BLOCK_READ`)
**Model = DENYLIST** (blokuj tylko pod żywymi korzeniami; wszystko inne ALLOW) — dużo mniej
fałszywych trafień niż allowlist i zgodne z intencją A4.
- Żywe korzenie ZAPISU: `/root/.openclaw/workspace/dispatch_state`,
  `/root/.openclaw/workspace/scripts/logs`, plik `/root/.openclaw/workspace/scripts/flags.json`.
- Żywy korzeń ODCZYTU (tylko STRICT): `/root/.openclaw/workspace/dispatch_state`.
- Whitelist (nigdy nie blokuj, belt-and-suspenders): `/dev/null`,`/dev/*`,`/proc/*`,
  `tempfile.gettempdir()` subtree, sandbox `DISPATCH_STATE_DIR`, pytest basetemp. (Denylist i tak
  je przepuszcza — whitelist to tylko zabezpieczenie na wypadek sandboxa pod żywym korzeniem.)
- Rozwiązywanie celu: szybki pre-filtr substring → `os.path.abspath`+`normpath` (bez `realpath` —
  string-only, szybkie; model zagrożeń = przypadkowy zapis testu, nie adwersarz; `realpath` jako
  opcja hardeningu w raporcie). `file` będące int (fd)/nie-path → passthrough.
- BLOCK_WRITE → `raise RuntimeError("HERMETIC-GUARD: write to live <root>: <path> ... napraw:
  DISPATCH_STATE_DIR/monkeypatch; opt-out ALLOW_PROD_STATE_IN_TEST")`.
- BLOCK_READ (STRICT) → `raise FileNotFoundError(path)` — symuluje brak katalogu; czytniki
  produkcyjne są fail-soft (`except FileNotFoundError → {}`), więc suita przechodzi „bez dispatch_state".

### 2d. Tryby
- **DEFAULT** = tylko write-block pod żywymi korzeniami. Wg A4 realnych zapisów do prod = **0** →
  zero zmiany zachowania zielonych testów. Kwarantanna biega normalnie (marker bez `-m`).
- **STRICT** (`HERMETIC_STRICT=1`) = write-block + READ-block dispatch_state + SKIP kwarantanny.

---

## 3. PROJEKT KWARANTANNY (zewnętrzna, po nodeid — zero edycji plików testów)
- `hermetic_quarantine.json`: lista `{ "match": "<stem pliku>", "reason": "...", "modes": ["strict"] }`.
- `pytest_collection_modifyitems(config, items)` w root conftest: dla itemu, którego `path.stem`
  pasuje → `item.add_marker(pytest.mark.nonhermetic)`; jeśli STRICT → `item.add_marker(
  pytest.mark.skip(reason=...))`.
- **Startowa lista (z A4):**
  - `test_v325_pin_leak_defense` — live READ `courier_names.json` (waliduje cleanup PRODUKCJI;
    script-style/subprocess) — w DEFAULT czyta żywy plik i przechodzi (read nieblokowany); STRICT skip.
  - `test_route_order_live_parity` — wymaga venv panelu (`assert PANEL_PY.exists()`), ortogonalne.
  - `test_working_override_2026_06_01` — zależny od zegara (nieoznaczony nonhermetic dotąd).
  - + 2 już `@pytest.mark.nonhermetic` (istniejące) — zostają, moja lista ich nie dubluje ani nie rusza.
- DEFAULT: wszystkie dalej biegną (marker nie wyklucza bez `-m`) → baseline bez zmian.

---

## 4. PLAN DOWODÓW (test_hermetic_guard_zp207.py — WSZYSTKIE pass w DEFAULT)
Pure (mode-niezależne, deterministyczne — przechodzą w DEFAULT i STRICT):
1. write do żywego `dispatch_state` → `_classify`=BLOCK_WRITE.
2. write do tmp → ALLOW.
3. write do żywego `logs` i `flags.json` → BLOCK_WRITE.
4. STRICT read-decision: `_classify(read, live dispatch_state, strict=True)`=BLOCK_READ;
   `strict=False`=ALLOW (dowód logiki STRICT bez realnego open → pass w DEFAULT).
Integracyjne (guard sesyjny aktywny, tryb DEFAULT):
5. **NEGATYW**: `plan_manager.save_plan(cid, body)` BEZ monkeypatcha ścieżek (cel = ŻYWY
   `courier_plans.lock/json`; ignoruje DISPATCH_STATE_DIR bo hardcode) → `pytest.raises(RuntimeError,
   match="HERMETIC-GUARD")` (guard bije na `LOCK_FILE.touch` zanim dojdzie do zapisu) +
   `os.stat` żywego `courier_plans.json` mtime NIEZMIENIONY (stat przed/po; `os.stat` niepatchowany).
   Zgodne z C17 (wołam jak produkcja; izolacja przez stałą modułu, nie default-arg).
6. **POZYTYW**: `monkeypatch.setattr(plan_manager, "PLANS_FILE"/"LOCK_FILE", <tmp>)` → `save_plan`
   → plik ląduje w tmp (assert exists+treść), brak raise, żywy mtime niezmieniony.
7. **fail-soft**: `global_alloc_store.write(props, now)` z domyślną (żywą) ścieżką → zwraca `0`
   (guard bije wewnątrz, `except` połyka) + żywy `global_alloc.json` mtime/absent niezmieniony —
   dokumentuje, że guard chroni nawet fail-soft writery.
8. **kwarantanna**: loader parsuje `hermetic_quarantine.json` i zwraca oczekiwane stemy+powody;
   marker `nonhermetic` zarejestrowany (jak test_hermetic_gate). Aplikacja markera dowiedziona
   przebiegiem STRICT w raporcie.

**Przebieg dowodowy DEFAULT**: pełna suita `pytest tests/` (przez pkgroot symlink) MUSI dać
listę identyczną z baseline (0 failed, te same skip/xfail) + moje nowe testy passed. Robię diff
baseline-vs-po-conftest.

**Przebieg dowodowy STRICT**: `HERMETIC_STRICT=1 pytest tests/` → cel 0 failed (kwarantanna skip,
reszta bez żywego dispatch_state). Ewentualne faile „asercja na pustych danych" → dopisuję do
`hermetic_quarantine.json` z powodem i wyliczam w raporcie. Zapisuję liczby+listę skipów.

---

## 5. RYZYKA + ZNANE LUKI
- **R1 — globalny `DISPATCH_STATE_DIR` może przesunąć baseline.** 12 testów ustawia go samodzielnie
  (override function-scoped wygrywa), `test_prune_terminal_orders` NIE asertuje raise na unset →
  bezpieczny. Mitigacja: **empiryczny diff** baseline (bez conftest) vs po-conftest w Fazie 2;
  przy dryfcie — dostrajam seed fixture lub zawężam setdefault. Sandbox seedowany `{}`/anonim →
  czytniki fail-soft, brak nowych failów oczekiwany (A4: żaden zielony test nie czyta żywego
  orders_state przez `_state_path`, bo dziś RAISE).
- **R2 — nowy `dispatch_v2/conftest.py` może zmienić rootdir pytest.** Brak plików ini → rootdir
  bez zmian materialnych dla wyników (wpływa na nodeid/ini, nie na pass/fail). Weryfikacja: diff
  baseline; moje testy kontrolne przechodzą tylko gdy conftest aktywny (dowód załadowania).
- **R3 — patch `builtins.open` globalnie** mógłby łapać legalne zapisy. Denylist (tylko 3 żywe
  korzenie) → pytest cache/tmp/`__pycache__`/coverage/worktree poza korzeniami = ALLOW. Fixture
  `_isolate_flags_json` (tests/conftest): `shutil.copyfile(żywy flags.json→tmp)` — src=read
  flags.json (DEFAULT allow; STRICT read-block dotyczy TYLKO dispatch_state, nie flags.json),
  dst=tmp (allow) → izolacja flag działa dalej.
- **LUKA (udokumentować, NIE rozwiązywać) — script-runnery subprocess.** `ScriptRunItem` odpala
  `python -m dispatch_v2.tests.<mod>` w OSOBNYM interpreterze → NIE dziedziczy mojego in-process
  monkeypatcha. Dziedziczy env (`dict(os.environ)`): DISPATCH_UNDER_PYTEST, **mój sandbox
  DISPATCH_STATE_DIR**, stripped DISPATCH_FLAGS_PATH + per-writer guardy (state_machine RAISE,
  setup_logger filter, courier_resolver last_pos). Wg A4 = **0 literal-write do live** w
  runnerach → praktycznie bezpieczne. Pełne rozwiązanie (sitecustomize/import-hook instalujący
  guard przy starcie interpretera subprocess) = OSOBNA FAZA za ACK (inwazyjne — plik na
  PYTHONPATH dla wszystkich subprocesów).
- **Fałszywy zapis ujawniony w pełnej suicie** (gdyby A4 się myliło): jeśli plik nieochroniony →
  dopisuję do kwarantanny Z POWODEM + raport (NIE edytuję cudzego testu); jeśli chroniony →
  tylko kwarantanna+raport. Zero cichego łatania.

---

## 6. CZEGO NIE ROBIĘ
- Nie edytuję: `tests/conftest.py` (styk z sesją 53), `state_machine.py`, `common.py`,
  jakiegokolwiek istniejącego `test_*.py`, `test_hermetic_gate.py`, kanonu zapisowo.
- Nie dodaję `addopts`/`-m "not nonhermetic"` do configu (zmieniłoby baseline DEFAULT).
- Nie rejestruję markera `nonhermetic` po raz drugi (istnieje w tests/conftest).
- Nie ruszam istniejących 2 testów `@nonhermetic`.
- Nie instaluję sitecustomize/import-hook dla subprocess (osobna faza, ACK).
- Zero: systemctl, restart, zmian runtime, flag live, push. Żywe pliki tylko STAT (mtime dowód).
- Nie stage'uję cudzych niescommitowanych plików. Commit jawnymi ścieżkami (nie `-A`),
  stopka Co-Authored-By.

Rollback całości = `rm dispatch_v2/conftest.py` (1 plik dezaktywuje wszystko).

CZEKAM NA „GO" PRZED FAZĄ 2.
