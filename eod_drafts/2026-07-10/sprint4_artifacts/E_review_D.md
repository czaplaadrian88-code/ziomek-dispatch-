# E — Review BRANCH D (sprint4/z-p2-07-hermetic, 3c8175a + 46421f5)

**WERDYKT: APPROVE** — guard hermetyczny czysto additywny (1-plik aktywacja), poprawny klasyfikator denylist, DEFAULT bajt-zgodny z baseline poza 3 ujawnionymi prod-mutatorami, STRICT 0 failed/0 error (DoD spełnione), zero śladów testowych w żywym stanie. Znaleziony REALNY bug produkcyjny którego A4 nie złapał. Brak P0/P1. Drobiazgi = staleness liczników w raporcie.

## Zakres (a) — OK
- `git diff --numstat c2bde58..HEAD`: 10 plików, KAŻDY `+X/-0`. Zero modyfikacji istniejących. **NIE dotknięto `test_panel_packs_signal_v328.py` ani `tests/conftest.py`** (root `dispatch_v2/conftest.py` to NOWY, inny plik). Follow-up 46421f5 edytował tylko 4 WŁASNE pliki D. Write-set == D_plan §1.
- Rollback = `rm dispatch_v2/conftest.py` (1 plik).

## Jakość guarda (b) — OK, solidny
- **Idempotencja env:** `setdefault(DISPATCH_UNDER_PYTEST)` + DISPATCH_STATE_DIR ustawiany TYLKO gdy nieustawiony z zewnątrz. Install guard kopiuje `_ORIG` raz (`if not _ORIG`). MonkeyPatch undo na teardown sesji.
- **Klasyfikacja (pure `classify`)** dokładnie wg spec: ZAPIS+DELETE blok pod dispatch_state/scripts/logs/flags.json we WSZYSTKICH trybach; ODCZYT blok TYLKO w STRICT TYLKO dispatch_state (logs NIE). Potwierdzone testami pure 1-4.
- **Whitelist:** /dev*, /proc*, tmp subtree, sandbox DISPATCH_STATE_DIR.
- **realpath(parent)** — utwardzenie (łapie symlink rodzica w żywy stan); leaf-symlink NIE realpathowany = ZNANY LIMIT udokumentowany (model = przypadkowy literal, nie adwersarz).
- **Edge-case'y — wszystkie fail-open, guard nie rzuca na dziwne wejścia:** bytes-path → decode surrogateescape, fail → None → ALLOW; fd (int) → `os.fspath` TypeError → None → ALLOW; **dir_fd podany → resolved=None → ALLOW** (nie próbuje abspath fd-relative, świadomie); pusty path → None. `os.replace/rename` cel=arg2 (dst). `os.unlink==os.remove` (Path.unlink→os.unlink). `os.rmdir` poza spec (udokumentowane).
- **Czas suity:** DEFAULT 119s vs baseline 125s — narzut pomijalny (w szumie).

## DEFAULT niezależny (c) — OK
- Mój bieg wt-hermetic (pkgroot_hermetic): **4717 passed / 27 skipped / 10 xfailed / 0 failed** (119s).
- **Diff skipów vs baseline (comm): w baseline-nie-w-DEFAULT = PUSTE (24 bazowe zachowane); w DEFAULT-nie-w-baseline = DOKŁADNIE 3× `test_panel_packs_signal_v328.py` (HERMETIC-QUARANTINE).** 10 xfail bez zmian. Każda delta uzasadniona wpisem kwarantanny z powodem.

## STRICT niezależny (d) — OK, DoD spełnione
- **HERMETIC_STRICT=1: 4668 passed / 76 skipped / 10 xfailed / 0 failed / 0 error** (107s). „Suita bez dispatch_state" = **0 failed/0 error**. (grep „50 SKIPPED lines" = pytest zwija duplikaty przez `[N]`; suma testów=76.)

## Testy kontrolne behawioralne (e) — OK (10/10)
- pure klasyfikator (1-4); **NEGATYW:** `plan_manager.save_plan` na PROBE pod żywym dispatch_state → `RuntimeError(HERMETIC-GUARD)` + plik/lock NIE powstają (probe unikalny — brak flaky/pollution); **POZYTYW:** ten sam writer monkeypatch stałej modułu (C17) → ląduje w tmp, żywy mtime niezmieniony; **FAIL-SOFT:** `global_alloc_store.write` → guard bije wewnątrz, `except`→0, żywy nietknięty; **DELETE:** `os.unlink/os.remove/Path.unlink` na sondę → RAISE ×3, tmp działa. Loader kwarantanny + marker.

## Kwarantanna per-nodeid (f) — OK, brak over-skip
- panel_packs (3) i pin_gps (10) matchowane po pełnym nodeid → NIE zdejmują sąsiadów. **`test_panel_packs_signal_v328.py` = 5 testów: 3 quarantine-skip, 2 czyste (`test_max_age_constant`/`test_courier_state_has_panel_packs_fields`) BIEGAJĄ.** Whole-file (stem) tylko dla plików w całości live-zależnych (test_v325_pin_leak_defense/route_order_live_parity/working_override/r04_v2_evaluator, reasony to potwierdzają). Każdy wpis ma `reason` (asercja w teście).

## Brak śladów testowych w żywym stanie (g) — OK
- Po DEFAULT: `hermetic_probe_*` = **0**, `test_panel_packs*`/`hermetic_state_*` = **0**. Distinctive fixture strings („Testowy Kurier A"/„Testowa Restauracja"/„ul. Testowa 1"/„Koordynator (test)") = **0 plików**. Wcześniejsze trafienia „999001" = szum numeryczny (ułamki sekund `.999001`, epoch `1778999001`, długość GPS `23.09199900`), NIE fixtura. `panel_packs_cache.json` świeży mtime = żywy panel-watcher (panel_packs testy SKIP → mój bieg go nie tknął). Guard skutecznie zablokował każdy zapis testu.

## Docs + raport (h) — zgodne, 2 stale liczniki
- HERMETIC_TESTS.md + raport spójne z kodem/wynikami (DEFAULT 4717/27, STRICT 4668/76/0/0, denylist, 2 tryby, opt-out, znane luki). Ujawnienie prod-writera opisane uczciwie z rekomendacją U ŹRÓDŁA dla ownera (bez cichego łatania protected testu).

## Znalezisko procesowe (POZYTYW)
Guard ujawnił REALNY prod-pollution: `test_panel_packs_signal_v328._write_packs_cache` robił `mkstemp(dir=ŻYWY dispatch_state)+os.replace` na produkcyjny `panel_packs_cache.json` + `os.unlink` na żywy (PANEL_PACKS_CACHE_PATH hardcode ignoruje DISPATCH_STATE_DIR). **A4 (grep literalny) to pominął** (ścieżka przez `dir=`). D zgodnie z własnym contingency: kwarantanna+raport (NIE edytuje protected testu) + follow-up dodał guard klasy DELETE by złapać `os.unlink`.

## Znaleziska
- **P0: brak. P1: brak.**
- **P2 / drobiazgi (nie blokują):**
  1. Raport staleness nagłówków: §1 „9 testów kontrolnych" (faktycznie 10), §3 „24 wpisy" kwarantanny (faktycznie 25). Sekcje autorytatywne §5 (25) / §6 (10/10) poprawne — follow-up dodał 10. test + 25. wpis, nie odświeżył wszystkich liczb. Czysta kosmetyka.
  2. Deklaracja lidera STRICT „75 skip" vs faktyczne **76** (raport też 76, mój bieg 76) — „75" było przybliżeniem. Informacyjnie.
  3. Znane luki uczciwie udokumentowane i ŚWIADOMIE ODŁOŻONE (nie defekty): subprocess script-runnery nie dziedziczą in-process guarda (A4=0 literal-write, złagodzone env+per-writer guardy) — reszta luki hermetyczności; leaf-symlink; os.rmdir. Pełne domknięcie subprocess (sitecustomize/import-hook) = osobna faza ZA ACK — warto by Adrian wiedział że to residual gap.
