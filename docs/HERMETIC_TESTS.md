# Testy hermetyczne (Z-P2-07)

Odciecie suity `pytest` od ZYWEGO stanu hosta i sciezek produkcyjnych. Additywne —
aktywacja przez JEDEN plik `dispatch_v2/conftest.py`. **Rollback calosci = usun ten plik.**

## Po co

W repo dominuja sciezki wiazane na sztywno: **631** hardcode stalych modulowych
(`plan_manager.PLANS_FILE`, `courier_resolver.*_PATH`, ~30x `tools/STATE_DIR`, ...) +
**30** zamrozonych default-argow w sygnaturach. Samo `DISPATCH_STATE_DIR` izoluje tylko
`state_machine`. Jedyna warstwa lapiaca WSZYSTKIE klasy jednolicie to **prymitywy FS**.

## Dwa tryby

| Tryb | Jak wlaczyc | Co robi |
|---|---|---|
| **DEFAULT** | `pytest tests/` | WRITE-guard: blok zapisu do 3 zywych korzeni. Kwarantanna BIEGA (marker bez `-m` nie wyklucza). Wynik = IDENTYCZNY z baseline. |
| **STRICT** | `HERMETIC_STRICT=1 pytest tests/` | WRITE-guard + READ-blok zywego `dispatch_state` (symulacja braku katalogu → czytniki fail-soft `{}`). Kwarantanna SKIP. Dowod: "suita przechodzi bez dispatch_state". |

Przebieg hermetyczny bez kwarantanny (CI): `pytest tests/ -m "not nonhermetic"`.

## Zywe korzenie (denylist)

- ZAPIS blokowany (wszystkie tryby): `/root/.openclaw/workspace/dispatch_state`,
  `/root/.openclaw/workspace/scripts/logs`, plik `/root/.openclaw/workspace/scripts/flags.json`.
- ODCZYT blokowany (TYLKO STRICT): `/root/.openclaw/workspace/dispatch_state`.
- Wszystko inne (tmp, worktree, `__pycache__`, `/dev/null`, `/proc`, sandbox) = ALLOW.

## Guard — co patchuje

`tests/hermetic_support.install_guard()` podmienia na czas SESJI (autouse, undo na koncu):
`builtins.open` (tryb `w/a/x/+`), `os.open` (`O_WRONLY/RDWR/CREAT/TRUNC/APPEND`),
`os.replace`, `os.rename` (cel = `dst`). `shutil.move/copyfile` NIE patchowane —
dekomponuja sie do powyzszych prymitywow. Cel rozwiazywany przez `os.path.abspath` +
`realpath(parent)` (lapie dowiazania w zywy stan).

Zapis do zywego korzenia → `RuntimeError("HERMETIC-GUARD: ...")`. Odczyt w STRICT →
`FileNotFoundError` (symulacja braku katalogu).

## Sandbox state-dir

Root conftest przy imporcie tworzy `tempfile.mkdtemp(prefix="hermetic_state_")`, sieje
ANONIMOWYMI fixture'ami z `tests/fixtures/hermetic/*.json` (dane ZMYSLONE) i ustawia
`DISPATCH_STATE_DIR` — **tylko jesli nie ustawiony z zewnatrz**.

## Opt-out (swiadomy wyjatek)

- `ALLOW_PROD_STATE_IN_TEST=1` — wylacza guard (parytet z `state_machine._state_path`
  / `setup_logger`). Tylko dla jawnego read-only smoke na realnych danych.
- `pytest -m "not nonhermetic"` — wyklucza testy kwarantanny.

## Kwarantanna (live / nonhermetic)

Jawna lista `tests/hermetic_quarantine.json` (`match` = stem pliku / fragment nodeid +
`reason` + `modes`). Zewnetrzna (`pytest_collection_modifyitems`) — **zero edycji plikow
testow**. DEFAULT: marker `nonhermetic`. STRICT: skip z powodem.

## Znana luka — script-runnery subprocess

Script-style testy odpalane sa jako `python -m dispatch_v2.tests.<mod>` w OSOBNYM
interpreterze → NIE dziedzicza in-process guarda. Dziedzicza env (`DISPATCH_UNDER_PYTEST`,
sandbox `DISPATCH_STATE_DIR`, stripped `DISPATCH_FLAGS_PATH`) + guardy per-writer
(`state_machine` RAISE, `setup_logger` filter, `courier_resolver` last_pos). Wg mapy A4 =
**0 literal-write do produkcji** w runnerach. Pelne rozwiazanie (sitecustomize/import-hook
instalujacy guard przy starcie subprocesu) = OSOBNA FAZA za ACK.

## Znany limit — dowiazanie liscia

`resolve_target` realpathuje RODZICA, nie plik-lisc. Dowiazanie samego liscia wskazujace
w zywy stan nie zostanie zlapane (egzotyczne; model zagrozen = przypadkowy zapis testu
przez literal sciezke produkcyjna, ktora zawsze zawiera zywy prefiks).

## Subprocess-guard przez sitecustomize (2026-07-10, ACK Adrian)
Luka #1 (script-runnery subprocess poza in-process guardem) ZAMKNIĘTA: root-conftest
generuje katalog tmp z `sitecustomize.py` i stawia go na POCZĄTKU `PYTHONPATH` sesji —
każdy python-child (script-runner, `subprocess.run` w testach) importuje go na starcie
i instaluje TEN SAM guard (`hermetic_support.install_guard_subprocess`, bez undo — patch
na czas życia dziecka). Aktywny wyłącznie pod `DISPATCH_UNDER_PYTEST=1`; FAIL-OPEN;
opt-out per-run: `HERMETIC_SUBPROCESS_GUARD=0`. Produkcja nietknięta (env+katalog żyją
tylko w sesji pytest). Dowody: `test_subprocess_inherits_guard_blocks_live_write` (+2).
