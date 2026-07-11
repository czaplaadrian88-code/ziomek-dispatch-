# A360-R0 REPLAY-TRUTH — raport wykonania

Status: **DEVELOPMENT COMPLETE, DISPOSITION HOLD, NOT MERGED, NOT DEPLOYED**
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
kompletny rekord i brak zaobserwowanego OSRM miss pozwalaja ocenic roznice
krytyczna, roznice miekka albo parytet. Suma `class_counts` musi byc rowna
`denominator`; niespelnienie tego inwariantu przerywa narzedzie.

Zmiana nie dotyka dispatchera, `core/`, pipeline, feasibility, scoringu,
selection ani planu. HARD/SOFT, wybor kuriera, wynik decyzji i kolejnosc trasy
pozostaja bez zmian. Nie dodano flagi ani consumera runtime.

## 2. Root cause i naprawa

Stan bazowy i kolejne niezalezne odbiory wykazaly problemy wiarygodnosci:

- jeden rekord mogl jednoczesnie zwiekszyc `missy` i `roznice`;
- `wr0`, brak `now` i brak shadow znikaly z mianownika albo zyly poza wspolna
  taksonomia;
- CLI gate'a nie przyjmowal jawnej sciezki syntetycznego ledgera, wiec test
  pelnego toru musial mockowac reader;
- artefakt tekstowy wypisywal identyfikatory operacyjne i wartosci diffow.
- niezalezny odbior na `2844e43` wykazal, ze gate uznawal kazdy dict
  `live_inputs`, takze `{}` i partial, za kompletny. `_serve_live_inputs`
  wykonywal wtedy zero lub tylko czesc patchy i przez szerokie `except`
  pozostawial domyslne sciezki A2, planu/locka i map kalibracji. Wczesniejsza
  deklaracja "brak live fallbacku" obejmowala tylko brak calego `live_inputs`
  i byla zbyt szeroka.
- `OsrmReplayer._take` nie konsumowal ostatniego elementu kolejki i powtarzal
  go bez konca. Jedna nagrana odpowiedz i dwa identyczne wywolania dawaly dwie
  odpowiedzi oraz `misses=0`, wbrew known-answer Audytu 360 wymagajacemu RED
  dla extra OSRM call.

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
- wspolny `world_replay.validate_replay_record()` jest wywolywany przez gate,
  bezposredni `replay_one`, CLI i paired przed shadow join, importami,
  `with_flag` i replayem;
- minimalny kontrakt wr1 wymaga `reliability`, `plans`, `eta_quantile` i
  `prep_bias` jako dict, `loadgov` jako list/tuple dlugosci 4 oraz obecnego
  `k07` jako dict albo `None`. Brak i zly typ maja stabilne, rozlaczne reasons
  `missing_live_input:<key>` i `invalid_live_input:<key>`;
- `_serve_live_inputs` waliduje dict przed pierwszym patchem i nie polyka
  bledow przekierowania. Kompletny snapshot kieruje wszystkie pliki oraz lock
  planu do jednego temp sandboxu; partial nie dotyka importera ani sciezki.
- outer wr1 wymaga niepustego `order_id`, parsowalnych `ts` i `now`, znanego
  `schema=wr1`, dictow `order_event/fleet/flags` oraz `osrm_calls` jako listy;
- kazdy **serwowany** wynik route/table jest konsumowany najwyzej raz przez
  FIFO `pop(0)`. Extra runtime call tego samego klucza po wyczerpaniu kolejki
  zwraca sentinel, dopisuje miss i ma klase `OSRM_MISS`, bez sieci i bez
  fallbacku haversine.

Jawne rezyduum: niewykorzystane surplus recorded OSRM calls nie sa dzis
flagowane. Nie blokuje to wymaganego kontraktu extra-runtime-call/reuse, ale
instrument pozostaje **narrow/partial**; raport nie deklaruje bit-for-bit ani
full trust.

Nie walidujemy glebiej elementow `osrm_calls` ani wewnetrznych schematow
`order_event/fleet/flags`; to swiadome ograniczenie R0, nie deklaracja pelnej
walidacji calego wr1.

## 3. Mapa kompletnosci

| Miejsce | Rola | Dotkniete | Powod / dowod |
|---|---|---:|---|
| `tools/world_replay.py` | replay + klasyfikator | TAK | jedno zrodlo pieciu klas; sandbox planu |
| `tools/world_replay_gate.py` | mianownik, agregacja, verdict | TAK | coverage/freshness/rozlaczne klasy/redakcja |
| `tests/test_a360_world_replay_truth.py` | frozen oracle | TAK | known-answer, mutation, negative controls, determinizm |
| `tests/fixtures/world_replay_truth_frozen.json` | golden bez PII | TAK | wszystkie piec klas |
| `tests/test_world_replay_k06.py` | direct replay | TAK | kompletny legalny snapshot wr1 |
| testy gate K17/schema | kompatybilnosc | TAK | kompletny legalny snapshot, semantyka bucketow bez zmian |
| `tests/test_paired_flag_replay_zp103.py` | paired / at-214 | TAK | invalid flags callback=0; legalny parytet w obu porzadkach |
| `world_record.py` | producer | N-D | read-only zgodnie z karta |
| `osrm_client.py` | recorder/OSRM | N-D | read-only; fallback blokowany przez sandbox |
| `tools/paired_flag_replay.py` | consumer `at-214` | TAK | waska prewalidacja przed normalizacja `with_flag`; karta rozszerzona jawnie |
| `A360_R0_REPLAY_TRUTH_CARD.md` | kontrakt zakresu | TAK | jawne uzasadnienie rozszerzenia paired allowlisty |
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

Po fixie `df4556a` wykonano druga prawdziwa mutacje: tymczasowo usunieto
`loadgov` z `REQUIRED_LIVE_INPUT_KEYS`. Dedykowany test przeszedl
**GREEN -> RED** (`KeyError: loadgov`, pytest `rc=1`) i po przywroceniu wpisu
patchem wrocil **RED -> GREEN** (1 passed). Mutacja nie trafila do commita.

Po fixie OSRM wykonano trzecia prawdziwa mutacje: `_take` przywracal reuse
ostatniego elementu przez `seq.pop(0) if len(seq) > 1 else seq[0]`. Direct i
actual gate negative control przeszly **2 GREEN -> 2 RED** (brak
`replay_miss`) i po przywroceniu jednokrotnego `pop(0)` wrocily **2 GREEN**.

Po outer/paired prevalidation wykonano czwarta prawdziwa mutacje: usunieto
`flags` ze wspolnego zbioru walidowanych dictow. Przypadki missing i `flags=[]`
weszly do forbidden callbacku (po jednym wywolaniu), dajac **2 GREEN -> 2 RED**.
Po przywroceniu `flags` oba wrocily na GREEN. Zadna mutacja nie trafila do
commita.

### Negative controls

- brak calego `live_inputs`, pusty dict, brak kazdego z szesciu kluczy i zly
  typ kazdego pola dostaja stabilny `INPUT_MISS`; podstawiony forbidden
  `replay_one` ma zero wywolan;
- bezposredni `replay_one` podnosi `IncompleteReplayInput`, a CLI zwraca rc=2
  z reason przed replayem;
- outer invalid `order_id/ts/now/schema/order_event/fleet/flags/osrm_calls`
  jest klasyfikowany wspolnym reason; gate invalid `osrm_calls` ma zero shadow
  joinow i zero wywolan replay;
- paired odrzuca brak `flags` i `flags=[]` przed `with_flag` z custom callback
  count=0. Callback jest dodatkowym testem kolejnosci/API; koniecznosc fixu dla
  domyslnego at-214 wynika z tego, ze `with_flag` normalizuje invalid/missing
  `flags` przez `dict(record.get("flags") or {})` przed `WR.replay_one`;
- legalny paired record zachowuje dokladny parytet oraz kolejnosc OFF->ON i
  ON->OFF;
- jedna nagrana odpowiedz OSRM jest zwracana raz; drugi identyczny call daje
  sentinel i miss. Actual `replay_one` zwraca `misses=1`, a actual gate
  klasyfikuje rekord wylacznie jako `OSRM_MISS`;
- negative control `_serve_live_inputs` dla `{}` i partial `{"plans": {}}`
  ma zero patchy i pozostawia sztuczna sciezke `/dispatch_state/...` bez zmian;
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

Po pierwotnej zmianie (przed odbiorem blockera):

- focused world replay: **20 passed**;
- world-record/replay `HERMETIC_STRICT=1`: **34 passed**;
- replay + paired replay `HERMETIC_STRICT=1`: **45 passed**;
- DEFAULT: **4948 passed, 24 skipped, 10 xfailed, 0 failed** w 122,14 s;
- STRICT: **4898 passed, 74 skipped, 10 xfailed, 0 failed** w 105,76 s;
- `py_compile tools/world_replay.py tools/world_replay_gate.py`: PASS;
- import `world_replay`, `world_replay_gate`, `paired_flag_replay`: PASS;
- `git diff --check`: PASS.

Po fix-forward walidacji `live_inputs`:

- focused replay/gate/paired: **55 passed**;
- STRICT cluster world-record/replay/gate/paired: **63 passed**;
- DEFAULT: **4963 passed, 27 skipped, 10 xfailed, 0 failed** w 124,97 s;
- pierwszy pelny STRICT: **1 failed, 4912 passed, 77 skipped, 10 xfailed**;
  jedyny fail byl poza zakresem w zegarowym
  `test_f4_k2_interp_elapsed_zero_at_pickup`;
- izolowany rerun tego testu STRICT: **1 passed**;
- powtorzony pelny STRICT: **4913 passed, 77 skipped, 10 xfailed, 0 failed**
  w 107,90 s;
- `py_compile`, import wspolnego walidatora i `git diff --check`: PASS.

Po outer validation, braku reuse OSRM i paired prevalidation (final):

- focused replay/gate/paired: **71 passed**;
- STRICT cluster world-record/replay/gate/paired: **79 passed**;
- DEFAULT: **4979 passed, 27 skipped, 10 xfailed, 0 failed** w 121,29 s;
- STRICT: **4929 passed, 77 skipped, 10 xfailed, 0 failed** w 108,22 s;
- `py_compile` trzech tools i import wspolnego validatora: PASS;
- entropy dashboard read-only: **17 / ~13 / 25/49 / 1 / 7 / 13 / 11+4 / 10**;
  zadna z osmiu metryk nie wzrosla;
- finalny `git diff --check f679a88..HEAD`: wykonywany po finalnym docs commit.

Nie zmieniono zadnego markera skip/xfail ani kwarantanny. Roznica +3 skip w obu
pelnych suitach wzgledem poprzedniego biegu jest zgodna ze znana zegarowa
wariancja suity; raport nie przypisuje jej zmianie R0 bez osobnego dowodu.

Entropy dashboard przed i po: 17 / ~13 / 25/49 / 1 / 7 / 13 / 11+4 / 10.
Zadna z osmiu metryk nie wzrosla. Dashboard ma historyczny status instrumentow,
wiec formalna reklasyfikacja wymaga integratora po odbiorze brancha; ten sprint
nie edytuje wspolnego backlogu ani pamieci.

## 6. Commity i wydanie

- `e8967671056551c1da5a5bc62655117827775693` — rozlaczne klasy, staly
  mianownik, coverage/freshness, frozen oracle;
- `93b361920755722c25a573574ab2557820b85353` — sprzezony sandbox pliku planu
  i locka;
- `107373383b089784e383dff6e1709c07d006aa88` — kompatybilnosc publicznego
  kontraktu paired replay / `at-214`;
- `3e48a49199174f478a3f5d0d6be42e297de08624` — pierwotny raport replay-truth;
- `2844e4369935fa88de27f18bea1203c950b02956` — usuniecie trailing whitespace
  z raportu integracyjnego;
- `df4556a3e4e86c371614800455ddf4dd4bc8240f` — fail-closed walidacja
  kompletnego `live_inputs` przed replayem;
- `38ae681e9633974e933b6367cd7ffbcc6f72e229` — raport dowodow fail-closed
  `live_inputs`;
- `6f9f06a776806235537fa6b60ceb86615e63623f` — outer validator,
  brak reuse zaserwowanego OSRM i paired prevalidation;
- `114be3a8788658b105df7f19c582e9d8b06b8a0b` — finalny test paired flags i
  obu poprawnych porzadkow;
- `HEAD` — finalny docs commit raport+karta; jego pelny SHA jest autorytatywnie
  podany w finalnym handoffie i `git log` (commit nie moze zawierac wlasnego SHA).

Development jest zakonczony na `evidence/a360-r0-replay-truth`, ale disposition
pozostaje **HOLD**. **Nie merge'owac do mastera przed odczytem `at-214`** z
13.07 lub jawnym zamrozeniem jego kodu. Nie bylo flipa, deployu, restartu,
migracji, zapisu live state ani zmiany timera/joba.

Integrator moze scalic/cherry-picknac do mastera wylacznie finalny docs commit
(`HEAD`: raport+karta). Commity kodu i testow R0 pozostaja tylko na branchu do
werdyktu `at-214`.

## 7. Rollback

Przed merge: pozostawic branch bez merge albo go odrzucic. Po przyszlym merge
rollback kodu/testow/raportu/karty:

```bash
git revert HEAD 114be3a8788658b105df7f19c582e9d8b06b8a0b 6f9f06a776806235537fa6b60ceb86615e63623f 38ae681e9633974e933b6367cd7ffbcc6f72e229 df4556a3e4e86c371614800455ddf4dd4bc8240f 2844e4369935fa88de27f18bea1203c950b02956 3e48a49199174f478a3f5d0d6be42e297de08624 107373383b089784e383dff6e1709c07d006aa88 93b361920755722c25a573574ab2557820b85353 e8967671056551c1da5a5bc62655117827775693
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
