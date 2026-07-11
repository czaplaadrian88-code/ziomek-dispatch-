# AUDIT360 — odbior A0/I1/N0 branch-only

Data odbioru: 2026-07-11 21:32 UTC

Status wspolny: **TECH COMPLETE NA BRANCHACH / ZERO MERGE I ZERO LIVE**.

## 1. Wynik sprintow

| Sprint | Branch / finalny HEAD | Wynik techniczny | Status wydania |
|---|---|---|---|
| A360-A0 ETA-CALIBRATION-TRUTH | `evidence/a360-a0-eta-calibration-truth` / `a0322f8` | Usunieto future-feature leakage; promotion gate wymaga tego samego supportu, odtwarzalnego championa i paired evidence. Kod `2aaedcd`. | `HOLD/UNBOUND`: brak artifactu championa v2, zero promocji i zero podpiecia ETA. |
| A360-I1 PAPU-BRIDGE-RECOVERY | `integration/a360-i1-papu-bridge-recovery` / `dca2715` | 2xx/niepewny submit bez `panel_zid` trafia do exact-marker recovery; zero resubmitu, missing/ambiguous przechodzi do jawnego hold. | Deploy mostu wymaga osobnego ACK i postdeploy read-back; kod nie jest w live checkout. |
| A360-N0 NIGHT-GUARD-TRUTH | `quality/a360-n0-night-guard-truth` / `53d8446` | Fail-closed manifest przypina 5170 nodeidow/outcome; hard-error nie staje sie baseline; dwa food-age XPASS zastapiono deterministycznym kontraktem. Kod `f6a2e4e`. | Source deploy wymaga osobnego ACK; timer 12.07 01:15 UTC bez deployu uruchomi stary kod. |

Wszystkie branche sa clean, maja HEAD rowny upstreamowi i pozostaja poza
masterem. Tmux68/69/70 sa idle na finalnych podsumowaniach i nie maja
pozostawionych testow w tle.

## 2. Dowody i testy

### A0

- targeted DEFAULT/STRICT: 30/30;
- full DEFAULT: 5126 passed, 27 skipped, 8 xfailed, 2 xpassed, 0 failed;
- full STRICT: 5076 passed, 77 skipped, 8 xfailed, 2 xpassed, 0 failed;
- frozen support byl identyczny przed/po; MAE pickup 5,23 -> 5,36, delivery
  7,51 -> 7,70. Pogorszenie ok. 2,5% jest jawnym kosztem usuniecia informacji
  z przyszlosci, nie wynikiem do ukrycia;
- niezalezny odbior wykryl trzy trailing whitespace w branchowym raporcie.
  Tmux68 poprawil je docs-only w `a0322f8`; finalny
  `git diff --check 307242d..HEAD` jest zielony.

### I1

- systemowy Python 3.12 compile/import PASS;
- targeted 11/11, mutation recovery poprawnie RED, finalnie GREEN;
- syntetyczne testy pokrywaja missing, ambiguous, crash/restart, legacy
  `panel_zid=None`, unknown submit i backward at-most-once;
- full dispatch N-D po potwierdzonym braku importera/callera mostu w silniku;
- rezyduum: pelne exactly-once przez zewnetrzny POST wymagaloby idempotency
  key/ACK po stronie Papu. Nie zgadywano nowego kontraktu biznesowego.

### N0

- targeted 45/45;
- final DEFAULT: 5139 passed, 27 skipped, 8 xfailed, 0 xpassed, 0 failed;
- final STRICT: 5089 passed, 77 skipped, 8 xfailed, 0 xpassed, 0 failed;
- py_compile/import, lifecycle 505/505, entropy i mutation probes PASS;
- `git diff --check 307242d..HEAD` jest zielony;
- proceduralny near-miss: pomocnicze `pgrep -a` pokazalo argumenty wlasnych
  procesow testowych, bez sekretow/PII. Klasa jest juz objeta regula C32; nie
  powstala nowa luka protokolu.

## 3. Sensitivity at-214

Nowe obciazenie hosta bylo serializowane globalnym flockiem. Dokladne
przedzialy:

- N0: `[2026-07-11T20:41:24Z,2026-07-11T21:08:53Z]`;
- A0 paired replay: `[21:04:20Z,21:04:29Z]` i `[21:08:53Z,21:09:01Z]`;
- A0 full DEFAULT: `[21:10:30Z,21:15:19Z]`;
- A0 full STRICT: `[21:15:19Z,21:19:47Z]`.

Konserwatywna unia do sensitivity: `[2026-07-11T20:41:24Z,21:19:47Z]`.
I1 nie uruchamial pelnej suity i nie dodal ciezkiego przedzialu. Atq nadal ma
wylacznie job 214; nie utworzono nowego at/timera.

## 4. Stan produkcji po odbiorze

O 21:32 UTC:

- dispatch-shadow PID 573430, dispatch-panel-watcher PID 3659486 i courier-api
  PID 925329 sa active/running, `NRestarts=0`;
- parser v2 healthy, error_count=0, pending=0, downstream ok;
- `flags.json` ma niezmieniony mtime 10:27:12 UTC i mode 0600;
- Papu bridge oneshot o 21:28 zakonczyl sie success na starym kodzie;
- night-guard service zachowuje znany old-source failed z 11.07; nie wykonano
  reset-failed ani recznego startu;
- ETA timer i Papu timer sa active/waiting; niczego nie restartowano.

Zero merge kodu lane'ow, flipa, danych, migracji, HTTP testowego, deployu,
instalacji unity, daemon-reloadu i restartu. Chronione dirty
`CLAIM_LEDGER_HARD_GATE_CARD.md` oraz workspace `restaurant_map.json` pozostaly
nietkniete.

## 5. Bramki i rollback

- A0: nie promowac modelu. Pierwszy seed artifactu v2 wymaga osobnego review i
  jawnej decyzji; stare `docs/eta/03..05` wymagaja osobnej korekty archiwalnej.
- I1: po ACK osobny deploy, kontrolowany tick/read-back i monitoring braku
  duplikatow. Rollback `git revert dca2715` plus ponowny deploy starego source.
- N0: po ACK source-only deploy bez zmiany unity i bez restartu; po najblizszym
  timerze odczytac schema v2/consumer. Rollback `git revert f6a2e4e` i source
  deploy poprzedniej wersji.
- R0/D1/H1 pozostaja na dotychczasowych HOLD; te sprinty nie zmienily ich
  bramek ani provenance at-214.
