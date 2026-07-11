# A360-R0 REPLAY-TRUTH — karta wykonawcza

Status: EXECUTED, final review amendment; disposition HOLD do odczytu at-214.

Effort: `high`

## Problem i dowod

Historyczny replay Audytu 360 mial 23/210 roznic i 24 missy OSRM, ale gate
mieszal brak wejscia, brak nagranego OSRM oraz rzeczywista roznice decyzji.
Sprint 3 dodal provenance i paired replay, lecz nie zastapil niezaleznego,
zamrozonego oracle world replay. Bez tego R6/D1 nie maja wiarygodnego
mianownika ani informacji, czy zmienila sie decyzja, czy tylko brakowalo danych.

## G0 i ownership

- tmux: 62
- branch: `evidence/a360-r0-replay-truth`
- worktree: `/root/a360_r0_wt/dispatch_v2`
- base: tag `a360-wave1-closed-20260711`
- write allowlist:
  - `tools/world_replay.py`
  - `tools/world_replay_gate.py`
  - `tools/paired_flag_replay.py` — waskie rozszerzenie po finalnym review R0:
    tylko wywolanie wspolnego outer validatora przed `with_flag`; konieczne
    takze dla domyslnego at-214, bo `with_flag` normalizuje brak/invalid `flags`
    przez `dict(record.get("flags") or {})` przed `WR.replay_one`
  - `tests/test_paired_flag_replay_zp103.py` — test rozszerzenia paired:
    missing/invalid `flags`, callback count=0 i oba poprawne porzadki replay
  - dedykowane `tests/test_*world_replay*`
  - syntetyczny, bez-PII frozen fixture/golden
  - `eod_drafts/2026-07-11/A360_R0_REPLAY_TRUTH.md`
- read-only: `world_record.py`, `osrm_client.py` i zredagowane agregaty korpusow.
- poza zakresem: `core/`, pipeline, feasibility, scoring, selection, plan,
  flagi, unity, produkcyjny state i logi z identyfikatorami.

Wspolne backlogi i pamiec aktualizuje tylko integrator.

## Zachowanie po zakonczeniu

Kazdy rekord replay dostanie dokladnie jedna klase:

- `INPUT_MISS`
- `OSRM_MISS`
- `CRITICAL_DIFF`
- `SOFT_DIFF`
- `PARITY`

Raport poda staly mianownik, coverage, freshness i rozlaczne skip reasons.
Brak wejscia nie bedzie juz udawal roznicy decyzji. Dispatcher, scoring i
HARD/SOFT pozostana bajtowo bez zmian — sprint naprawia prawde instrumentu.

## Testy i pozytywny dowod

1. Frozen known-answer obejmujacy wszystkie piec klas.
2. Mutation probe klasyfikatora GREEN→RED.
3. Negatywna kontrola sieci i live fallbacku.
4. Dwa przebiegi tego samego korpusu daja identyczny wynik.
5. Temp paths dla recordu, ledgera i verdictu; brak PII w artefaktach.
6. Testy world-record/replay/gate, DEFAULT, STRICT, `diff --check` i entropy.
7. Paired: missing/invalid `flags` zatrzymuje rekord przed `with_flag`; custom
   callback z licznikiem zero jest dodatkowym testem kolejnosci/API. Poprawny
   rekord zachowuje parytet w obu kolejnosciach OFF→ON i ON→OFF.

## Ryzyka, rollback i bramki

- Ryzyko: nowy gate poprawi etykiety, ale nadal bedzie mial falszywy oracle.
  Kontrola: niezalezny frozen known-answer i mutation probe.
- Rollback: revert tylko tool/test/report; brak danych i uslug do cofania.
- `at-214` z 13.07 importuje replay tools. Development moze ruszyc, ale merge
  do mastera czeka na odczyt at-214 albo jawne zamrozenie kodu joba.
- Alertowanie/enforcement nocnego gate'a, flaga, deploy i restart sa poza tym
  sprintem i wymagaja osobnego ACK.
