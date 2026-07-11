# A360-DEP0 SBOM — karta wykonawcza

Status: RUNNING w tmux64 od 2026-07-11 12:09 UTC.

Effort: `medium`

## Problem i dowod

Manifest dispatch przypina tylko rdzen, courier API ma nieprzypiete glowne
biblioteki, a ETA calibration uzywa zakresow dolnych. Nie ma kompletnej mapy
`proces → interpreter/venv → manifest → wersje runtime`. Audyt 360 nie wykonal
CVE scan; brak skanu nie jest dowodem braku podatnosci.

## G0 i ownership

- tmux: 64
- branch: `supply/a360-dep0-sbom`
- worktree: `/root/a360_dep0_wt/dispatch_v2`
- base: tag `a360-wave1-closed-20260711`
- write allowlist:
  - `tools/dependency_inventory.py`
  - `tests/test_dependency_inventory.py`
  - znormalizowany SBOM pod katalogiem raportowym Audytu 360
  - `eod_drafts/2026-07-11/A360_DEP0_SBOM.md`
- read-only input: manifesty dispatch/API/ML, metadane pakietow venv oraz
  `ExecStart` i sciezka interpretera z unitow.
- zakaz odczytu EnvironmentFile, `.env`, tokenow i danych runtime.
- bez zmian requirements, constraints, lockow, venv i pakietow systemowych.

Wspolne backlogi i pamiec aktualizuje tylko integrator.

## Zachowanie po zakonczeniu

Powstanie jedna mapa zaleznosci dla procesow, ktore faktycznie dzialaja na
hoscie. Jawne beda drift manifest-runtime, wynik `pip check`, direct/transitive/
unmanaged, licencje, EOL i CVE z timestampem oraz zrodlem. Ziomek nie zmieni
zachowania; wynik bedzie baza dla osobnych, malych sprintow upgrade.

## Testy i pozytywny dowod

1. Dwa przebiegi generatora daja identyczny znormalizowany SBOM.
2. Walidacja schematu i kompletnej mapy proces→venv.
3. `pip check` osobno per srodowisko i import smoke pakietow krytycznych.
4. Jawna klasyfikacja direct/transitive/unmanaged.
5. Licencje i CVE maja zrodlo, timestamp i confidence; brak wyniku = UNKNOWN.
6. Negatywna kontrola usuwa sciezki wrazliwe i dane runtime z artefaktu.
7. `diff --check`; brak zmian w istniejacych manifestach.

## Ryzyka, rollback i bramki

- Ryzyko: raport uzna brak CVE feedu za wynik czysty albo zapisze sciezke
  wrazliwa. Kontrola: fail-closed UNKNOWN i test redakcji.
- Rollback: revert tool/test/report oraz usuniecie tymczasowego venv skanera.
- Inwentaryzacja nie wymaga ACK. Kazdy upgrade, lock rewrite, instalacja pakietu
  lub zmiana produkcyjnego venv jest osobnym sprintem i osobna bramka.
