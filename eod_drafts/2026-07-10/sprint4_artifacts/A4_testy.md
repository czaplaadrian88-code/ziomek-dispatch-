# A4 — Mapa: suita testowa vs żywy stan hosta (Z-P2-07)

READ-ONLY. Nic nie edytowane, zero git/systemctl. Środowisko: pytest **9.0.3**, venv dispatch, **528** plików w tests/. `tests/conftest.py` istnieje (17 KB); **root `dispatch_v2/conftest.py` NIE istnieje** (wolne do utworzenia).

---

## A. LICZBY

### Testy — pliki z host-paths (klasyfikacja 151)
| Klasa | Ile | Znaczenie |
|---|---|---|
| host-path łącznie | **151** | ref. do `/root/.openclaw` / `dispatch_state` / `logs` / `expanduser` |
| self-izolujące (monkeypatch/tmp/DISPATCH_STATE_DIR) | **82** | tworzą własny tmp — bezpieczne w STRICT |
| kosmetyka (string/komentarz/asercja, BEZ I/O) | **65** | nie dotykają FS |
| realny read-I/O host-path, bez guardu | **4** | patrz D — praktycznie 1 twardy fail |
| write-primitive gdziekolwiek w pliku | 49 | ale... |
| **write do LIVE literału** (`open('/root/...','w')`, `os.replace(..,live)`) | **0** | żaden test nie pisze do produkcji przez literał |

Rozbicie self-izolacji (82): DISPATCH_STATE_DIR=5 · monkeypatch.setattr=53 · tmp_path/tmpdir=56.

### Kod produkcyjny — klasy wiązania ścieżek
| Klasa | Ile | Przykłady |
|---|---|---|
| (b) hardcode stała modułowa (col-1) | **631** | plan_manager.PLANS_FILE, courier_resolver.*_PATH, ~30× tools/STATE_DIR, notify_router._STATE_DIR |
| (c) default-arg w sygnaturze (C17, zamrożony) | **30** | pending_proposals_store ×8, global_alloc_store ×2, r04_evaluator ×2, parser_health, district_reverse_lookup |
| (a) env-override | **~3 klucze** | DISPATCH_FLAGS_PATH, DISPATCH_STATE_DIR, TG_HEARTBEAT_STATE_PATH (+A2_RELIABILITY_FEED_PATH) |
| (d) late-bound w ciele fn (patchowalne) | **1** | state_machine._state_path() |

**Wniosek:** 631 hardcode + 30 default-arg dominują; env-DI pokrywa ~3 punkty. **Samo ustawienie `DISPATCH_STATE_DIR` NIE izoluje suity** — honoruje je tylko state_machine + 5 plików testowych.

---

## B. common.py — jak zdefiniowane ścieżki + istniejące env-override

```
common.py:12  SCRIPTS_DIR = Path("/root/.openclaw/workspace/scripts")          # HARDCODE, brak override
common.py:13  CONFIG_PATH = SCRIPTS_DIR / "config.json"
common.py:17  FLAGS_PATH  = Path(os.environ.get("DISPATCH_FLAGS_PATH") or (SCRIPTS_DIR/"flags.json"))  # MA override
common.py:3582 A2_RELIABILITY_FEED_PATH = _os.environ.get("A2_RELIABILITY_FEED_PATH", ".../courier_reliability.json")  # MA override
```
- **NIE ma kanonicznej `STATE_DIR` w common.py.** Każdy moduł ma własną hardcode (`STATE_DIR = "/root/.openclaw/workspace/dispatch_state"`), stąd 631 duplikatów.
- **Jest env-override dla state-dir, ale TYLKO w `state_machine._state_path()`** (state_machine.py:258): `override_dir = os.environ.get("DISPATCH_STATE_DIR")` → late-bound + **RAISE pod pytest** gdy brak izolacji (:271, opt-out `ALLOW_PROD_STATE_IN_TEST=1`). To wzorzec-wzór (gold standard).
- **setup_logger JUŻ ma guard** (common.py:834-886): `_file_log_blocked_under_test()` czyta DISPATCH_UNDER_PYTEST / PYTEST_CURRENT_TEST (opt-out ALLOW_FILE_LOG_IN_TEST); FileHandler `delay=True` + `_ProdFileLogTestFilter` → pod pytestem **nie pisze** do żywych logów.

**Odpowiedź na pytanie leada:** DI przez env dla dispatch_state JEST możliwe bez edycji runtime **tylko dla state_machine**. Dla reszty (631 hardcode + 30 default-arg) env nie wystarczy — trzeba guardu na warstwie prymitywów FS.

---

## C. Import-time — zagrożenia

**Udowodnione empirycznie (subprocess, DISPATCH_STATE_DIR=pusty tmp, DISPATCH_UNDER_PYTEST=1, tylko import):**
15 ciężkich modułów (common, state_machine, plan_manager, courier_resolver, shadow_dispatcher, dispatch_pipeline, global_alloc_store, geocoding, sla_tracker, world_record, feasibility_v2, scoring, panel_watcher, telegram_approver, event_bus) → **wszystkie OK, ZERO zapisów** do fake-state.

- **Brak import-time state-I/O.** Stałe ścieżek są leniwe (str/Path); wszystkie read/write są w ciałach funkcji.
- Jedyny import-time dotyk FS: **~29 modułów** robi module-level `setup_logger(..., "logs/*.log")` → `mkdir` katalogu **logs** (nie dispatch_state), exist_ok, bez write (delay+filter). Nieszkodliwe.
- **Kolekcja pytest NIE wywali się bez dispatch_state.** Faily (jeśli będą) = runtime.

---

## D. Szacunek STRICT (brak dispatch_state) + pliki CHRONIONE

**Kolekcja: 0 failów** (import-safe, dowód wyżej).

**Runtime — czytniki produkcyjne fail-SOFT:** plan_manager / pending_proposals_store / global_alloc_store / ledger_io / manual_overrides zwracają `{}`/`None` na brak pliku (`if not exists / except FileNotFoundError`). Brak pliku → puste dane, **NIE kaskada FileNotFoundError**.

**Twarde faile z 4 nie-guardowanych czytników:**
| Plik | Zachowanie w STRICT |
|---|---|
| test_v325_pin_leak_defense.py:111 | `open(".../courier_names.json")` twardo → **FAIL** (script-style/subprocess; nonhermetyczny z założenia — waliduje cleanup PRODUKCJI) |
| test_route_order_live_parity.py | skipif + `assert PANEL_PY.exists()` → SKIP/fail na venv panelu (ortogonalny do dispatch_state) |
| test_v325_step_a_r02.py | module-level `pytest.skip` → SKIP (open :62 nieosiągalny) |
| test_v3273_wait_courier.py:176 | `.exists()` + try/except → PASS (graceful) |

**Realny twardy fail przypisany do braku dispatch_state ≈ 1 plik** (test_v325_pin_leak_defense). Reszta: SKIP/fallback lub asercje na pustych danych (mismatch, nie crash). Pełne wyliczenie asercji-na-danych wymaga przebiegu STRICT (poza mandatem) — ograniczone górnie liczbą kosmetyk+self-izolacji, więc realnie **jednocyfrowe**.

**Pliki CHRONIONE (Sprint 2/3) — D nie edytuje:**
- Jawne: test_event_retry_phase_a.py, test_order_fsm_zp101.py, test_parcel_lane_merge.py — **żaden nie ma host-path** (czyste, poza ryzykiem).
- Wzorce eta/sla/osrm/gps_delivery/decision_outcomes/stage/backpressure/tracing: 13 plików istnieje.
- **Chronione ∩ host-path = 3**, WSZYSTKIE self-izolujące (bezpieczne w STRICT):
  - test_sla_preexisting_bypass.py (READ, kosmetyka)
  - test_eta_load_aware_l51.py (WRITE→tmp)
  - test_etap4_flag_unification.py (WRITE→tmp)
- **→ Zewnętrzna kwarantanna NIEpotrzebna dla chronionych** (nie łamią się). Uwaga: write-guard **musi whitelistować tmp**, żeby nie fałszywie łapać ich zapisów do tmp_path.

**Markery (Task 5):** tylko **2** testy `@pytest.mark.nonhermetic` (marker zarejestrowany conftest.py:377). **Brak `addopts`/`-m 'not nonhermetic'` gdziekolwiek** — baseline biega WSZYSTKO, łącznie z nonhermetic. 1 znany nieoznaczony nonhermetic: test_working_override_2026_06_01.py:197 (zależny od zegara).

---

## E. Rekomendacja architektury dla D

### Nowy root `dispatch_v2/conftest.py` (NIE append do tests/conftest.py)
Bezpieczniejsze, bo:
1. **Blast radius** — nowy guard/DI odseparowany od 17 KB tests/conftest (rollback = usuń 1 plik).
2. **Kolejność** — root conftest importowany **przed** tests/conftest → env + guard instalują się najwcześniej (przed autouse tamtymi).
3. **Rozdział odpowiedzialności** — tests/conftest już właściciel flag/telegram/osrm/script-runner; nowy plik: state-dir DI + write-guard + kwarantanna.
4. Ryzyko: dwa autouse conftesty — trzymać idempotencję (`os.environ.setdefault`, nie nadpisywać DISPATCH_UNDER_PYTEST) i ordering (root pierwszy).

### Guard zapisu — patchować na warstwie PRYMITYWÓW FS (nie stałe modułowe)
Bo 631 hardcode + 30 default-arg → monkeypatch stałej ani env NIE pokryją wszystkiego (default-arg zamrożony w def-time; env honoruje tylko state_machine). Jedyna warstwa łapiąca WSZYSTKIE klasy jednolicie:
- **builtins.open** (tryb w/a/x/+) — łapie większość zapisów, json.dump(open(...)), FileHandler.
- **os.replace + os.rename** — łapie idiom **mkstemp→replace** (tmp tworzony przez os.open, więc builtins.open go NIE złapie; ale cel os.replace = ścieżka LIVE → tu blok). **Krytyczne** — to dominujący atomic-write idiom w silniku.
- **os.open** — łapie tempfile.mkstemp(dir=STATE_DIR) i low-level.
- (opcjonalnie shutil.move/copy).

**Logika guarda:** resolve abspath celu; jeśli pod LIVE `dispatch_state` (`/root/.openclaw/workspace/dispatch_state`) lub live `logs` i NIE pod tmp → **RAISE** (albo redirect). Whitelist: tmp_path/pytest-tmp, izolowana kopia flags, /dev/null.

**Dodatkowo:** ustaw `DISPATCH_STATE_DIR=<tmp>` globalnie (autouse/session) — auto-izoluje state_machine (jedyny honorujący) + 5 env-aware testów; ale to tylko uzupełnienie, backstopem jest guard FS.

**Test kontrolny „zapis do produkcji zablokowany" (dowód):**
- NEGATYW: wywołaj prod-writer bez monkeypatcha (np. `plan_manager.save_plan(...)` albo `global_alloc_store.write(...)`) i asertuj że guard RAISE na cel LIVE.
- POZYTYW: z guardem ON prod-writery lądują w tmp; `stat(mtime)` żywego pliku niezmieniony.

### Wydzielenie „live read-only" jawnie
- Oznacz nonhermetic i wytnij z hermetycznej suity: test_v325_pin_leak_defense (waliduje PROD courier_names.json), test_route_order_live_parity (venv panelu), test_working_override_2026_06_01 (zegar).
- **Mechanizm bez edycji plików chronionych/live**: root conftest `pytest_collection_modifyitems` — **dostępny w pytest 9.0.3 (potwierdzone)** — dodaje `skip`/`nonhermetic` po nodeid. Uruchamianie hermetyczne: `pytest -m "not nonhermetic"` (dziś NIGDZIE nie ustawione — trzeba dodać do addopts/CI).

---

## Artefakty (pełne listy)
`A4_testy_hostpaths_all.txt` (151) · `A4_testy_selfisolating.txt` (82) · `A4_testy_cosmetic.txt` (65) · `A4_testy_directREAD_io.txt` (4) · `A4_testy_writeprim_files.txt` (49) · `A4_testy_write_LIVE.txt` (0) · `A4_testy_protected_hostpath.txt` (3) · `A4_testy_protected_patterns.txt` · `A4_prod_hostpaths_files.txt` (406) · `A4_prod_defaultarg_paths.txt` (30) · `A4_prod_modules_klasy.txt` (podsumowanie klas).
