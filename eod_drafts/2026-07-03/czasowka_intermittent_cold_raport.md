# Czasówka INTERMITTENT-COLD (P1 z 02.07) — WERDYKT: REFUTED (kłamiący przyrząd) + fix log-hygiene u źródła

**Sesja:** tmux 14 (rano 03.07) · **Zadanie 2 handoffu** `HANDOFF_tmux14_rano.md` · **Protokół:** ETAP 0→7.

## TL;DR
**Silnik czasówki jest ZDROWY — w 100% ticków liczy poprawnymi flagami z flags.json.**
„INTERMITTENT-COLD 22-40%" to był kłamiący przyrząd (klasa C9, kolejny po 6 z min sesji 02-03.07):
testy pytest pisały `FLAG_FINGERPRINT proc=czasowka` z conftest-owo **odartym** flags.json (= defaulty)
prosto do żywego `logs/czasowka.log`, a `flag_fingerprint_check` liczył je jako emisje serwisu.
Fix u źródła w `common.setup_logger` (1 miejsce → leczy ~34 moduły z module-level PROD-loggerem).

## Dowód (ETAP 0, korelacja pełnego loga z journalem systemd)

Okno: `logs/czasowka.log` 2026-07-03 00:00:26 → 05:49, **695 linii FLAG_FINGERPRINT**, journal
`dispatch-czasowka.service` w tym samym oknie: **334 starty**.

| pomiar | wynik |
|---|---|
| unikalne sekundy WARM (flagi = flags.json) | **334** — pokrywają się ze startami serwisu **334/334** (±3 s); 668 linii = dokładnie 2/tick (dubel StreamHandler→systemd-append + FileHandler ten sam plik) |
| klastry COLD (≥15 flag = defaulty) | **9** (po 3 linie: 8× w 01:27–01:56 = nocna regresja pytest; 1× 05:36:33) |
| klastry COLD pokrywające się ze startem serwisu | **0/9** — journal: o 05:36:33 serwis NIE startował (ticki 05:36:06 i 05:37:11) |

Wniosek: **każda cold-linia pochodziła od obcego procesu**, żadna od serwisu. „22-40%" z nocy
= proporcja zależna od tego, ile pytest-u biegło w oknie pomiaru, nie od flag-loadu silnika.

## Mechanizm zanieczyszczenia (ETAP 1 — źródło, nie objaw)
1. `czasowka_scheduler.py:68` (i ~34 inne moduły): module-level `setup_logger(..., sztywna PROD-ścieżka)`
   → FileHandler podpinany **przy imporcie**, w KAŻDYM procesie (też pytest).
2. Testy (`test_v324b_czasowka_scheduler*.py`, 6 wywołań `main()`) biegną z flags.json odartym z flag
   decyzyjnych (conftest `_isolate_flags_json` / `DISPATCH_FLAGS_PATH=stripped`) → `flag_fingerprint()`
   = defaulty → linia „cold" w PROD-logu.
3. `flag_fingerprint_check` czyta log = wierzy, że to serwis. (Tłumaczy też nocne 1/6313 plan-recheck
   i pojedyncze skażenia innych logów — ta sama klasa.)

## Fix (commit — patrz git): 1 źródło, 3 pliki + testy
- **`common.py`**: `_file_log_blocked_under_test()` + `_ProdFileLogTestFilter` na FileHandlerze
  (per-REKORD, bo pytest ustawia `PYTEST_CURRENT_TEST` dopiero w fazie testu, po imporcie) +
  `FileHandler(delay=True)` (test nawet nie tworzy pliku). Opt-out: `ALLOW_FILE_LOG_IN_TEST=1`
  (wzorzec 1:1 z guardem telegram_utils L1). **PROD nietknięty** (brak markerów env → pisze jak dotąd).
- **`tests/conftest.py`**: `os.environ.setdefault("DISPATCH_UNDER_PYTEST", "1")` — marker na CAŁĄ
  sesję (łapie emisje import-time); subprocesy script-runner dziedziczą przez `dict(os.environ)`
  (+ ScriptRunItem i tak ustawia `PYTEST_CURRENT_TEST`).
- **`tools/flag_fingerprint_check.py`**: opis findingu INTERMITTENT-COLD uzupełniony o obowiązkową
  korelację z journalem PRZED orzeczeniem o silniku (anty-powtórka tej eskalacji).
- **`tests/test_setup_logger_test_hygiene.py`** (NOWY, 5 testów): blocked-under-pytest / opt-out /
  symulacja PROD w subprocesie bez markerów (pisze!) / marker sesji / precedens opt-outu.

## Dowody DoD
- Testy 5/5 PASS; **mutacja M1** (guard martwy) → `test_file_log_blocked_under_pytest` **RED** → restore → GREEN.
- **E2E na realnej warstwie:** bieg polluterów (`test_v324b_czasowka_scheduler*`, 27 testów) →
  licznik fingerprintów w PROD-logu **707→707, zero nowych cold** (przed fixem: każdy bieg dokładał
  klaster 3 cold-linii — nocne dane = ON≠OFF).
- `flag_fingerprint_check` po fixie: **INTERMITTENT-COLD zniknął** (zostały znane COVERAGE-GAP
  stale panel-watcher — rekoncyliacja przy najbliższym restarcie, poza pasem — + benign JSON-DRIFT poison).
- Pełna regresja: wynik w komicie / trackerze (baseline 4096/1-flaky).

## Konsekwencje / korekty stanu wiedzy
- **Handoff tmux14 zad. 2 „czasówki część czasu ignorują flipy" = NIEPRAWDA** — flipy docierają w 100% ticków.
- Raport `l01-registry_raport.md` §4 „ESKALACJA" — wyjaśnione; tool sam był ofiarą klasy, którą surface'ował.
- Deploy: **zero restartów potrzebnych** (czasówka oneshot; zmiana dotyczy wyłącznie zachowania POD TESTAMI;
  serwisy long-running w prod nie mają markerów env → identyczne zachowanie).
- Rollback: `git revert <commit>` albo `.bak-pre-loghygiene-2026-07-03` (common.py / conftest.py / tool).
- Drobiazg odnotowany POZA pasem: serwis dubluje każdą linię czasowka.log (StreamHandler→systemd append
  + FileHandler ten sam plik) — kandydat na osobny mini-temat (wolumen logów), NIE ruszany tu.
