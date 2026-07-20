# Golden replay: deterministic REPLAY + fail-closed capture

Data: 2026-07-20. Branch/worktree: `work`, `/root/cx-jp2`; base HEAD
`c7bc8cd61778b6d89051553611b7694af3b914ca`. Bez commita, deployu, restartu,
flipu flag i operacji na stanie live.

## Wynik

Root cause potwierdzony w kodzie: pipeline tworzył `ThreadPoolExecutor` dla
kandydatów, a solver domyślnie kończył pracę budżetem wall-clock. Recorder OSRM
po cichu odrzucał wywołanie po limicie i nie oznaczał wyjątku wrappera.

- `tools/world_replay.py`: każdy rekord REPLAY wymusza istniejący
  `ENABLE_ORTOOLS_DET_TIME_LIMIT=ON`, offline ceiling `30000 ms` oraz
  sekwencyjny executor pod procesowym lockiem. Patche są restaurujące.
- `osrm_client.py` + `world_record.py`: kompletność recordera jest zwracana
  atomowo; wyjątek route/table, limit lub błąd recordera zapisuje
  `capture_status=INPUT_MISSING` z bezpiecznym kodem przyczyny.
- `tools/golden_decision_replay.py`: capture/replay miss pozostaje w mianowniku,
  daje werdykt `INPUT_MISSING`, ale nie zapisuje ani nie porównuje decyzji z
  sentinelem. `cross_differences_n=0`, `difference_samples=[]` dla takiego
  rekordu.

## Mapa kompletności

| Miejsce | Rola | Status |
|---|---|---|
| `osrm_client.py` route/table | writer wszystkich wejść OSRM | TAK |
| `world_record.py` | writer statusu rekordu | TAK |
| `tools/world_replay.py` | frozen-input consumer + det sandbox | TAK |
| `tools/golden_decision_replay.py` | worker, RAM i SQLite evaluator | TAK |
| `common.py` / `tsp_solver.py` | istniejący budżet det | N-D: reużyty bez zmiany |
| `world_replay_gate.py` | operacyjny sześciopolowy gate | N-D: dziedziczy wspólny validator |

## Dowody

- zmieniony klaster: `30 passed`;
- affected + sąsiedni replay/capture, `HERMETIC_STRICT=1`: `87 passed`;
- budżet flag/fingerprint + historyczny sequential replay: `9 passed`;
- night-guard: `5416` nodeidów, hash `713c0121…c4e`, identyczny z manifestem
  v17, zero alertów;
- golden selftest/mutation: `PASS`; flag lifecycle repo-hermetic: `0 błędów`;
- `py_compile`, `compileall`, `git diff --check`: PASS.

Pełne wymagane `5385/0` nie mogło zostać uczciwie wykonane w tym sandboxie:
systemowy Python nie ma `ortools`, `protobuf`, `absl`; instalacja do `/tmp`
nie powiodła się z powodu braku sieci, a użycie kanonicznego venv pod
`/root/.openclaw/**` było jawnie zabronione. Cztery solverowe testy dają wyłącznie
`ModuleNotFoundError: ortools`. Faza C/D pozostaje HOLD do zielonego biegu
kanonicznym venv.

Rollback przed live: revert ośmiu plików z tego diffu; brak danych/migracji i
brak potrzeby restartu. Stare rekordy bez `capture_status` pozostają zgodne.
