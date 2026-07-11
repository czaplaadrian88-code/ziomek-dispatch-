# A360-R0 REPLAY-TRUTH — raport wykonania

Status: **CODE READY, NOT MERGED, NOT DEPLOYED**  
Data: 2026-07-11 UTC  
Branch: `evidence/a360-r0-replay-truth`  
Worktree: `/root/a360_r0_wt/dispatch_v2`  
Base: `a360-wave1-closed-20260711` / `f679a88`  
Merge gate: odczyt werdyktu `at-214` albo jawne zamrozenie kodu joba.

## 1. Wynik

Instrument replay ma teraz jeden, rozlaczny wynik dla kazdego rekordu w stalym
mianowniku:

1. `INPUT_MISS`
2. `OSRM_MISS`
3. `CRITICAL_DIFF`
4. `SOFT_DIFF`
5. `PARITY`

Precedencja jest wykonywalnym kontraktem. Brak kompletnego frozen inputu
uniewaznia porownanie. Brak nagranego wywolania OSRM uniewaznia diff. Dopiero
kompletny rekord i kompletny OSRM moga byc ocenione jako roznica krytyczna,
roznica miekka albo parytet. Suma `class_counts` musi byc rowna `denominator`;
niespelnienie tego inwariantu przerywa narzedzie.

Zmiana nie dotyka dispatchera, `core/`, pipeline, feasibility, scoringu,
selection ani planu. HARD/SOFT, wybor kuriera, wynik decyzji i kolejnosc trasy
pozostaja bez zmian. Nie dodano flagi ani consumera runtime.

## 2. Root cause i naprawa

Stan bazowy mial cztery problemy wiarygodnosci:

- jeden rekord mogl jednoczesnie zwiekszyc `missy` i `roznice`;
- `wr0`, brak `now` i brak shadow znikaly z mianownika albo zyly poza wspolna
  taksonomia;
- CLI gate'a nie przyjmowal jawnej sciezki syntetycznego ledgera, wiec test
  pelnego toru musial mockowac reader;
- artefakt tekstowy wypisywal identyfikatory operacyjne i wartosci diffow.

Naprawa:

- czysty `world_replay.classify_replay()` jest jednym zrodlem pieciu klas;
- nowy skan stalego mianownika zachowuje niekompletne rekordy jako
  `INPUT_MISS` i osobno liczy `invalid_json`, `invalid_record`, `invalid_ts`,
  duplikaty oraz truncation;
- raport zawiera `input_pct`, `osrm_pct`, `oracle_pct`, freshness,
  `corpus_fingerprint` i rozlaczne powody `INPUT_MISS`;
- `--shadow-file` i `--as-of` pozwalaja przejsc caly tor deterministycznie na
  temp paths;
- artefakt ma tylko pseudonimowy `record_ref` oraz nazwy pol roznic, bez ID,
  adresow, GPS, nazwisk, wartosci score i wartosci diffow.

## 3. Mapa kompletnosci

| Miejsce | Rola | Dotkniete | Powod / dowod |
|---|---|---:|---|
| `tools/world_replay.py` | replay + klasyfikator | TAK | jedno zrodlo pieciu klas; sandbox planu |
| `tools/world_replay_gate.py` | mianownik, agregacja, verdict | TAK | coverage/freshness/rozlaczne klasy/redakcja |
| `tests/test_a360_world_replay_truth.py` | frozen oracle | TAK | known-answer, mutation, negative controls, determinizm |
| `tests/fixtures/world_replay_truth_frozen.json` | golden bez PII | TAK | wszystkie piec klas |
| stare dedykowane testy world replay | kompatybilnosc | TAK | zaktualizowany kontrakt stalego mianownika |
| `world_record.py` | producer | N-D | read-only zgodnie z karta |
| `osrm_client.py` | recorder/OSRM | N-D | read-only; fallback blokowany przez sandbox |
| `tools/paired_flag_replay.py` | consumer `at-214` | N-D | read-only; zachowany publiczny alias `CORE_FIELDS` |
| core/pipeline/feasibility/scoring/selection/plan | decyzja | N-D | jawnie poza allowlista i bez potrzeby zmiany |

## 4. Kontrole oracle, mutacja i negatywne

### Frozen known-answer

Syntetyczny fixture bez PII zawiera po jednym przypadku kazdej klasy. Przypadek
`OSRM_MISS` ma jednoczesnie sztuczna roznice krytyczna, co dowodzi precedencji:
wynik pozostaje wylacznie `OSRM_MISS`.

### Mutation probe

Po commicie `e896767` wykonano prawdziwa mutacje kodu:

```text
if osrm_misses -> if False and osrm_misses
```

Frozen oracle przeszedl GREEN -> RED: oczekiwany `OSRM_MISS` zostal blednie
sklasyfikowany jako `CRITICAL_DIFF`; pytest zakonczyl sie `rc=1`. Mutacje
odwrocono patchem, `git diff --exit-code` byl czysty, a ten sam test wrocil na
zielono. Nie uzyto checkout/reset do restauracji pracy.

### Negative controls

- rekord bez `live_inputs` dostaje `INPUT_MISS`; podstawiony `replay_one`, ktory
  rzuca przy jakiejkolwiek probie sieci/live fallbacku, nie zostal wywolany;
- rekord, ledger i verdict w CLI sa jawnie pod `tmp_path`; test asertuje brak
  `/dispatch_state/` w kazdej efektywnej sciezce;
- STRICT ujawnil, ze replay przekierowywal `courier_plans.json`, ale nie jego
  lock. Fix `93b3619` kieruje `PLANS_FILE` i `LOCK_FILE` razem do tmp; guard nie
  zostal oslabiony ani ominiety;
- pelna suita ujawnila publicznego consumera `CORE_FIELDS` w
  `paired_flag_replay`. Fix `1073733` zachowuje alias delegujacy do jednego
  zrodla `world_replay.CRITICAL_FIELDS`; test importowy chroni `at-214`.

### Determinizm i prywatnosc

Dwa przebiegi tego samego korpusu z ta sama kotwica `as_of` daja identyczny
dict i identyczny `corpus_fingerprint`. Test CLI potwierdza, ze output nie
zawiera syntetycznych ID ani wartosci `best_cid`; szczegoly sa ograniczone do
pseudonimu, klasy, powodu lub nazw pol diffu.

## 5. Testy

Baseline przed edycja:

- DEFAULT: **4941 passed, 24 skipped, 10 xfailed, 0 failed** w 128,28 s.

Po zmianie:

- focused world replay: **20 passed**;
- world-record/replay `HERMETIC_STRICT=1`: **34 passed**;
- replay + paired replay `HERMETIC_STRICT=1`: **45 passed**;
- DEFAULT: **4948 passed, 24 skipped, 10 xfailed, 0 failed** w 122,14 s;
- STRICT: **4898 passed, 74 skipped, 10 xfailed, 0 failed** w 105,76 s;
- `py_compile tools/world_replay.py tools/world_replay_gate.py`: PASS;
- import `world_replay`, `world_replay_gate`, `paired_flag_replay`: PASS;
- `git diff --check`: PASS.

Lista skip/xfail DEFAULT nie wzrosla wzgledem baseline. STRICT ma oczekiwana
zewnetrzna kwarantanne live-smoke; nie dodano skipa ani wpisu kwarantanny.

Entropy dashboard przed i po: 17 / ~13 / 25/49 / 1 / 7 / 13 / 11+4 / 10.
Zadna z osmiu metryk nie wzrosla. Dashboard ma historyczny status instrumentow,
wiec formalna reklasyfikacja wymaga integratora po odbiorze brancha; ten sprint
nie edytuje wspolnego backlogu ani pamieci.

## 6. Commity i wydanie

- `e896767` — rozlaczne klasy, staly mianownik, coverage/freshness, frozen oracle;
- `93b3619` — sprzezony sandbox pliku planu i locka;
- `1073733` — kompatybilnosc publicznego kontraktu paired replay / `at-214`.

Kod jest gotowy do review i pushu tylko na
`evidence/a360-r0-replay-truth`. **Nie merge'owac do mastera przed odczytem
`at-214`** z 13.07 lub jawnym zamrozeniem jego kodu. Nie bylo flipa, deployu,
restartu, migracji, zapisu live state ani zmiany timera/joba.

## 7. Rollback

Przed merge: pozostawic branch bez merge albo go odrzucic. Po przyszlym merge
rollback kodu/testow/raportu:

```bash
git revert <commit-raportu> 1073733 93b3619 e896767
```

Kolejnosc jest newest-first. Nie ma flagi, danych, migracji, uslugi ani restartu
do cofania. Nocny gate pozostaje informacyjny; alertowanie/enforcement jest poza
tym sprintem i nadal wymaga osobnego ACK.

## 8. Ochrona zmian i otwarte kroki

- Nie dotknieto wspolnego backlogu ani pamieci.
- Nie dotknieto live state, logow z identyfikatorami, flag, unitow ani `/etc`.
- Nie dotknieto `core/`, pipeline, feasibility, scoringu, selection ani planu.
- Nie dotknieto chronionego `daily_accounting/kurier_full_names.json` ani cudzych
  dirty plikow/worktree.
- Integrator po `at-214` wykonuje review, merge/disposition oraz aktualizacje
  wspolnych statusow. Dalszy `DecisionContext`, CORE-02/03 i konsumpcja
  nocnego verdictu TEST-03 pozostaja osobnymi inkrementami `Z-P1-04`.
