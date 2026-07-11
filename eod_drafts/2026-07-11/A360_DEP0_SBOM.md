# A360-DEP0 SBOM — raport wykonania

Status: **DONE jako inwentaryzacja read-only; CVE i EOL = UNKNOWN**

Branch: `supply/a360-dep0-sbom`

Base: `a360-wave1-closed-20260711` (`f679a88`)

Snapshot metadanych: `2026-07-11T12:14:29Z`

## Werdykt

Powstała deterministyczna mapa sześciu faktycznie aktywnych procesów do dwóch
interpreterów, trzech manifestów i wersji runtime. Oba środowiska przechodzą
`pip check`, a wszystkie krytyczne importy przechodzą smoke. Nie ma brakującego
pakietu zadeklarowanego w manifestach ani wymagania poza zadeklarowanym zakresem.

To **nie jest werdykt bezpieczeństwa zależności**. Nie użyto zwalidowanego feedu
CVE ani EOL, dlatego globalny i każdy pakietowy status to jawne `UNKNOWN`, nigdy
`CLEAN`. Nie wykonano żadnego upgrade'u, instalacji, przebudowy locka ani zmiany
venv.

## Mapa procesów

| Proces | Środowisko | Interpreter | Manifest |
|---|---|---|---|
| `dispatch-shadow.service` | dispatch | `$VENV_ROOT/dispatch/bin/python` | `requirements-dispatch-venv.txt` + ETA calibration |
| `dispatch-panel-watcher.service` | dispatch | `$VENV_ROOT/dispatch/bin/python` | jw. |
| `dispatch-gps.service` | dispatch | `$VENV_ROOT/dispatch/bin/python` | jw. |
| `dispatch-sla-tracker.service` | dispatch | `$VENV_ROOT/dispatch/bin/python` | jw. |
| `dispatch-monitor-419.service` | dispatch | `$VENV_ROOT/dispatch/bin/python` | jw. |
| `courier-api.service` | courier-api | `$COURIER_API_ROOT/.venv/bin/python` | courier API `requirements.txt` |

Źródłem mapy były wyłącznie pola `ExecStart` oraz ścieżki interpreterów. Nie
odczytywano plików środowiskowych, danych runtime ani danych uwierzytelniających.

## Wyniki per środowisko

| Środowisko | Python | Pakiety | Direct / transitive / unmanaged | Manifest-runtime | `pip check` | Import smoke |
|---|---:|---:|---:|---|---|---|
| dispatch | 3.12.3 | 30 | 12 / 18 / 0 | 13 `SATISFIED` | PASS | 6/6 PASS |
| courier-api | 3.12.3 | 30 | 4 / 26 / 0 | 4 `UNPINNED` | PASS | 3/3 PASS |

Krytyczne importy dispatch: `lightgbm`, `numpy`, `ortools`, `pandas`, `scipy`,
`yaml`. Courier API: `fastapi`, `pydantic`, `uvicorn`.

Najważniejszy drift ma charakter reprodukowalności, nie bieżącej niespójności:
manifest courier API deklaruje cztery biblioteki bez pinów, podczas gdy runtime
ma konkretne wersje. Manifest dispatch i zakresy ETA są zgodne z runtime.

## Licencje, CVE i EOL

- Licencja z metadanych pakietu: dispatch 17 znanych / 13 `UNKNOWN`; courier API
  6 znanych / 24 `UNKNOWN`. `UNKNOWN` nie jest automatycznie konfliktem licencji.
- CVE: `UNKNOWN`, źródło `no_local_validated_feed`, confidence `none`.
- EOL: `UNKNOWN`, źródło `no_local_validated_feed`, confidence `none`.
- Brak feedu lub brak pola metadanych nie jest interpretowany jako brak ryzyka.

Osobny sprint może podłączyć zatwierdzony, wersjonowany snapshot feedu i politykę
licencyjną. Nie należy łączyć tego z automatycznym upgrade'em.

## Determinizm, schema i redakcja

- dwa przebiegi z tym samym timestampem dały identyczny plik;
- SHA-256 obu wyników:
  `60ed632a93b12efc3de8b455bd21863e3840179b5adfe57e1f9618c92b2b95c9`;
- schema: `a360-dependency-inventory/v1`;
- mapowanie proces→środowisko jest fail-closed;
- ścieżki robocze i venv są aliasowane; chronione klasy ścieżek są odrzucane;
- negatywna kontrola redakcji przechodzi;
- klasyfikacja `direct/transitive/unmanaged` jest jawna i testowana.

Artefakt maszynowy:
`eod_drafts/2026-07-11/audit360_artifacts/A360_DEP0_SBOM.json`.

## Testy

- `tests/test_dependency_inventory.py`: **5 passed**;
- pełna kanoniczna regresja `pytest tests/ -q`: **4946 passed, 24 skipped,
  10 xfailed, 0 failed**;
- deterministyczny generator ×2: **byte-identical**;
- `pip check`: dispatch PASS, courier-api PASS;
- import smoke: dispatch 6/6, courier-api 3/3;
- zmiany istniejących manifestów: **0**;
- produkcja, flagi, runtime, unity i interpretery: **0 zmian**.

## Ryzyka i dalsze kroki

1. Courier API pozostaje niepinowane — naprawa wymaga osobnego sprintu z
   constraint/lock designem i testem kompatybilności.
2. Licencje z metadanych są niekompletne; wymagają osobnej polityki i źródła.
3. CVE/EOL wymagają zaakceptowanego feedu z timestampem i provenance. Do tego
   czasu status pozostaje `UNKNOWN`.
4. Inwentaryzacja obejmuje dozwolone manifesty dispatch/API/ML i procesy z nimi
   powiązane. Nie rozszerza zakresu na panel, systemowe pakiety ani kontenery.

## Rollback

Rollback nie dotyka produkcji: wykonać jawny `git revert <commit-A360-DEP0>`.
Usuwa to generator, test, JSON SBOM i ten raport. Pliki tymczasowe w `/tmp` można
usunąć niezależnie; nie istnieje tymczasowy venv skanera. Nie ma migracji,
restartu, sekwencji usług, flagi ani danych do cofania.
