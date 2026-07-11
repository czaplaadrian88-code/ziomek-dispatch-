# A360-A0 ETA-CALIBRATION-TRUTH

**Status:** COMPLETE BRANCH-ONLY / werdykt modelu `HOLD/UNBOUND` / zero operacji live  
**Branch:** `evidence/a360-a0-eta-calibration-truth`  
**Zamrozona baza:** `307242d44080d98dd38143d5feae9304f9198a30`  
**Worktree:** `/root/a360_eta0_wt/dispatch_v2`

## 1. Zakres i stan wejsciowy

Sprint naprawia potwierdzone findings `ALGO-01/02` w offline'owym narzedziu
`tools/eta_calibration`. Nie wpina ETA do silnika, nie zmienia HARD/SOFT,
`flags.json`, runtime state, timerow ani uslug. Sibling carrier
`/root/a360_eta0_wt/flags.json` pozostaje 0444 i nie jest modyfikowany.

ETAP 0 potwierdzil:

- worktree byl czysty, branch i HEAD zgodne z promptem;
- sesja pracuje w `tmux68`; `tmux69` i `tmux70` maja rozlaczne katalogi i
  write-sety;
- glowny checkout zachowuje cudza, chroniona zmiane
  `eod_drafts/2026-07-10/CLAIM_LEDGER_HARD_GATE_CARD.md`;
- runtime byl healthy, `NRestarts=0`, parser v2 healthy, `atq` zawieral tylko
  at-214; nie wykonano restartu, deployu, flipa ani zapisu live;
- zamrozony carrier: SHA-256
  `568436f3de693d048a73bf1a2ba5c23e65191a350ef2e566fa5b6f34ace848bf`;
- integratorowy baseline DEFAULT z tej samej bazy: **5126 passed, 27 skipped,
  8 xfailed, 2 xpassed, 0 failed**;
- targeted baseline A0 `[2026-07-11T20:40:33Z,20:40:35Z]`:
  **16 passed**.

## 2. Root cause

1. `features.py` wyprowadza `hour`, `slot`, `weekday`, `load` i `is_bundle` z
   faktycznego pickup/delivery. `prep_var_med` pochodzi z biezacego,
   niewersjonowanego snapshotu. L1 i L2 uzywaja tych pol jak cech serwowanych.
   Writer `ziomek_pred_calibration.py` nie utrwala w finalnym rekordzie
   kompletnego decision-time snapshotu godziny i bag size. To future-feature
   leakage oraz gwarantowany train/serve skew.
2. `calibrate.champion_challenger()` nie odtwarza poprzedniego modelu ani jego
   predykcji. Porownuje tylko dwa agregaty MAE z roznych holdoutow, bez wspolnego
   supportu i testu paired, a warunek `new_mae <= prev_mae * 1.02` promuje nawet
   model o 2% gorszy.
3. README i `docs/eta/03..05` przedstawiaja wyniki z tej metodologii jako
   uczciwa prawde. README jest w write-secie A0; pozostale dokumenty sa poza nim
   i zostaja jawnie oznaczone ponizej jako follow-up integratora.

## 3. Mapa kompletnosci przed edycja

| miejsce | rola | writer/consumer | dotkniete TAK/N-D | powod | test/dowod |
|---|---|---|---|---|---|
| `tools/eta_calibration/features.py` | feature-store | producer | TAK | jawnie rozdzielic outcome-only od decision-time | kontrakt kolumn + mutation |
| `tools/ziomek_pred_calibration.py` | snapshot predykcji | upstream writer | N-D | poza write-setem; finalny rekord nie daje wiarygodnego decision-time hour/load | source audit |
| Rutcom + `sla_log` | declared time i outcome | upstream producer | N-D | read-only; targety pozostaja labelami | synthetic/frozen replay |
| geocode cache + OSRM | dystans/free-flow | upstream producer | N-D | cechy dostepne z danych zlecenia w chwili decyzji | known-answer |
| `restaurant_meta.json` | biezacy prep snapshot | upstream producer | TAK (drop z modelu) | brak historycznego as-of dla holdoutu | mutation prep |
| `models.EmpiricalQuantileModel._ctx` | L1 features | consumer | TAK | usuwa outcome `slot/load`; zostawia decision-time | invariance mutation |
| `models.LGBMQuantileModel._row` | L2 features | consumer | TAK | jawna allowlista per leg | feature-name tripwire |
| `models.build_courier_history` | historia train-only | producer/consumer | TAK (guard) | targety wolno uzyc tylko jako historia z train | known-answer train-only |
| `evaluate.evaluate_leg` | challenger + baseline | consumer | TAK | zwraca model i zamrozony, wspolny support do promocji | paired oracle |
| `calibrate.champion_challenger` | promotion gate | consumer/writer | TAK | artifact + exact support + paired CI/p + prog configowy | worse-1/2%, mismatch, missing artifact |
| `eta_calib_{pickup,delivery}_map.json` | obecny champion | writer/future consumer | TAK | wersjonowany, odtwarzalny artifact; legacy/missing = HOLD | round-trip artifact |
| nowe `eta_calib_*_candidate.json` | niepromowany challenger | writer/human consumer | TAK | zachowuje evidence bez podmiany championa | tmp_path integration |
| `eta_calib_metrics.jsonl` | metryki dzienne | writer | TAK | zapisuje HOLD/PROMOTE i paired evidence aggregate-only | schema compatibility |
| `tools/health_scoreboard.py` | ostatnie MAE/coverage | consumer | N-D | poza write-setem; zachowujemy pola `legs.*` | istniejacy test konsumenta + full suite |
| `eta_calib_shadow.jsonl` | pred vs real | writer | TAK tylko feature parity | ma uzywac tego samego clean model contract; deduplikacja ALGO-03 poza A0 | targeted integration |
| `tools/eta_calibration/config.yaml` | progi i sciezki | producer | TAK | progi paired/min-support + candidate paths | config known-answer |
| `tools/eta_calibration/README.md` | operator | consumer | TAK | usuwa reklamowe `-52%/-20%`; stan `HOLD/UNBOUND` | review/rg |
| `docs/eta/03_projekt_kalibracji.md` | historyczny projekt | consumer | N-D | poza jawnym write-setem; skazone claims nie sa po A0 kanonem | wpis follow-up |
| `docs/eta/04_wdrozenie.md` | historyczny runbook | consumer | N-D | poza write-setem; nie wykonujemy instalacji/deployu | wpis follow-up |
| `docs/eta/05_walidacja.md` | stary werdykt | consumer | N-D | poza write-setem; werdykt zostaje wycofany w README i tym raporcie | wpis follow-up |
| unit/timer `dispatch-eta-calibration-tool` | scheduler | consumer | N-D | source unit bez zmian; nic nie instalujemy i nie restartujemy | ETAP 0 status only |
| HERMETIC-GUARD | granica testow | guard | N-D | nie oslabiac; wszystkie nowe outputy testowe przez `tmp_path` | DEFAULT + STRICT |

## 4. Implementacja i wynik

### 4.1 Decision-time feature contract

- `models.FEATURE_CONTRACT_VERSION = decision_time_v2` i jawne allowlisty per
  noga sa jedynym zrodlem cech serwowanych.
- L1 nie segmentuje juz po `slot/load`; L2 nie przyjmuje
  `hour/weekday/load/prep_var_med`. Zostaja: kurier, anonimowy klucz
  restauracji, typ czasowki, OSRM per delivery oraz historia kuriera liczona
  tylko z train.
- `features.FEATURE_PROVENANCE` jawnie klasyfikuje pola. Outcome
  `hour/slot/weekday/load/is_bundle/prep_var_med` pozostaje w store tylko do
  segmentacji po fakcie i nigdy nie przechodzi przez `_ctx/_row`.
- Mutation probe zmienia wszystkie powyzsze pola na skrajne wartosci i dowodzi
  identycznej predykcji L1 oraz L2.

### 4.2 Odtwarzalny artifact i promocja fail-closed

- Nowy `promotion.py` definiuje `eta_calib_model.v2`. Artifact zawiera pelny
  runtime model (L1 albo wszystkie boostery L2 wraz z mapowaniami), osobny model
  ewaluacyjny, zahaszowane klucze frozen supportu, fingerprint targetow,
  zapisane predykcje i integralnosc SHA-256.
- Przed porownaniem oba modele sa odtwarzane, predykcje championa musza zgadzac
  sie z artifactem, targety nie moga dryfowac, a challenger musi pokryc dokladnie
  te same rekordy. Jakikolwiek brak daje `HOLD`.
- Gate wymaga jednoczesnie minimalnego supportu, configowej poprawy MAE
  (pickup 12%, delivery 5%), paired bootstrap CI ponizej zera, Wilcoxona
  `p<0.05` i non-inferiority z marginesem 0%.
- Zniknal warunek `new_mae <= prev_mae * 1.02` oraz porownanie agregatow z
  roznych okien. Brak/legacy/uszkodzony artifact championa zawsze daje `HOLD`.
- Challenger zawsze trafia do osobnego `eta_calib_*_candidate.json`. Mapa
  championa jest atomowo podmieniana tylko po pelnym gate. Obecne mapy v1 sa
  nieodtwarzalne, wiec ten sprint celowo nie tworzy automatycznego pierwszego GO.
- Shadow uzywa tego samego wybranego clean modelu co ewaluacja. Schemat metryk
  zachowuje `legs.*` wymagane przez `health_scoreboard`, a dodatkowo zapisuje
  `instrument_status`, aggregate-only decyzje i hashe artifactow.

### 4.3 Bezpieczny replay

`replay.py` otwiera SQLite przez `mode=ro`, pseudonimizuje order/courier i
restauracje natychmiast w pamieci, usuwa coords i wypisuje tylko agregaty oraz
fingerprinty. Nie zapisuje DB, map ani logow. Ten sam skrypt zostal uruchomiony
raz z kodem bazy (legacy) i raz z kodem A0.

## 5. Frozen replay przed/po

Snapshot DB mial identyczny SHA-256 przed i po obu przebiegach:
`c16281e8aeef9fc86c6d863a610942b789f3eed9e275c48d37af90a0480ecb16`.

- korpus: 7296 rekordow; train 4088; holdout rows 3208;
- cut day: `2026-06-27`;
- corpus support fingerprint:
  `b72c46b04a80c81fd5f8a508391ef580d3f65f6dde659440c66bc76f1cda0ef7`.

| noga | eligible support (ten sam przed/po) | legacy outcome-leaky MAE | decision-time v2 MAE | koszt usuniecia wycieku |
|---|---|---:|---:|---:|
| pickup, n=3205 | `67f78ab7eea0f66261aa427bdf6fdb1e8c70509530a67cb7cf770865541ebd12` | 5,23 | 5,36 | +0,13 min / +2,5% |
| delivery, n=2609 | `711be1326692c70db3304c68bbe41f2c17ec50eb7b5cd9bc42231eb46c198f34` | 7,51 | 7,70 | +0,19 min / +2,5% |

Utrata `hour/load/slot/weekday/prep` pogarsza stary wynik o ok. 2,5% na obu
nogach. To jest oczekiwany koszt usuniecia informacji z przyszlosci, a nie
regresja do zamaskowania. Clean agregaty nadal sa nizsze od baseline'ow na ich
pelnych wspolnych podzbiorach (pickup: koordynator 6,94 i naiwny 5,48 przy
n=3205; delivery: naiwny 9,07 przy n=2609), lecz **nie sa dowodem promocji**.
Baseline silnika ma inny support (pickup n=2770, delivery n=2253), a obecny
champion nie ma artifactu v2. Werdykt pozostaje zatem `HOLD/UNBOUND`; nie
odtwarzamy reklamowego GO z README.

## 6. Testy, checkery i host-load

| bramka | wynik |
|---|---|
| targeted baseline | 16 passed `[20:40:33Z,20:40:35Z]` |
| targeted final DEFAULT | 30 passed |
| targeted final STRICT | 30 passed |
| mutation: future hour/load/slot/weekday/prep | zabita; predykcja clean pozostaje identyczna |
| mutation: support mismatch / target drift | `HOLD` |
| mutation: challenger 1% i 2% gorszy | oba `HOLD` |
| missing/legacy/corrupt artifact | `HOLD`; mapa championa nietknieta |
| known-answer material improvement | `PROMOTE` tylko przy exact support + paired gates |
| py_compile + import | PASS |
| `git diff --check` | PASS |
| flag lifecycle | 505/505 curated, 0 bledow |
| flag effect coverage | brak nowej luki (116/127 z testem, 11 zastany baseline) |
| canon static | PASS (`R6=35`, `R27=+/-5`, eskalacja 40) |
| entropy vs frozen base | parytet: files 398, #4=1, #7=11 live +4 instrument |
| pelny DEFAULT | **5126 passed, 27 skipped, 8 xfailed, 2 xpassed, 0 failed** |
| pelny STRICT | **5076 passed, 77 skipped, 8 xfailed, 2 xpassed, 0 failed** |

Host-load intervals zapisane dla sensitivity at-214; wszystkie ciezkie prace
byly serializowane przez `/tmp/ziomek_full_regression.lock`:

- paired replay #1: `[2026-07-11T21:04:20Z,21:04:29Z]`;
- paired replay z per-leg fingerprint: `[2026-07-11T21:08:53Z,21:09:01Z]`;
- full DEFAULT: `[2026-07-11T21:10:30Z,21:15:19Z]`, pytest 286,84 s;
- full STRICT: `[2026-07-11T21:15:19Z,21:19:47Z]`, pytest 266,77 s.

147 ostrzezen pytest jest identyczna, zastana klasa `PytestReturnNotNoneWarning`;
nie powstal nowy fail/skip/xfail/xpass.

## 7. Wydanie, stan live i rollback

Implementacyjny commit:
`2aaedcd` (`fix(eta): make calibration evidence decision-time truthful`).
`git show 2aaedcd --stat` zawiera wylacznie 12 jawnych plikow
`tools/eta_calibration/**`. Commit zostal pushniety do
`origin/evidence/a360-a0-eta-calibration-truth`; nie wykonano merge.

Raport jest osobnym commitem dokumentacyjnym na tej samej galezi. Nie wykonano i
nie wolno w tym lane wykonywac deployu, flipa, migracji, instalacji timera,
modyfikacji map live ani restartu. Runtime pozostaje na kodzie mastera, a stary
timer kalibracji nie laduje branchowego kodu.

Finalna weryfikacja read-only `2026-07-11T21:21:44Z`:

- `dispatch-shadow` PID 573430, `dispatch-panel-watcher` PID 3659486,
  `courier-api` PID 925329; wszystkie active/running, `NRestarts=0`;
- `dispatch-eta-calibration-tool.timer` active/waiting, `Result=success`;
- parser v2 healthy, errors/pending 0; `atq` tylko job 214;
- carrier nadal 0444 i SHA-256
  `568436f3de693d048a73bf1a2ba5c23e65191a350ef2e566fa5b6f34ace848bf`;
- glowny checkout zachowuje nietkniety cudzy
  `eod_drafts/2026-07-10/CLAIM_LEDGER_HARD_GATE_CARD.md`.

Rollback branchowego kodu: `git revert 2aaedcd`.
Nie ma rollbacku danych/runtime, bo niczego live nie zmieniono.

## 8. Otwarte decyzje i follow-up

1. Pierwszy seed championa v2 wymaga osobnego przegladu candidate artifactu i
   jawnej decyzji wlasciciela. Job pozostaje fail-closed i sam go nie wykona.
2. Historyczne `docs/eta/03_projekt_kalibracji.md`, `04_wdrozenie.md` i
   `05_walidacja.md` sa poza write-setem lane'u; nadal zawieraja skazone claims.
   Integrator powinien je oznaczyc jako archiwalne albo zaktualizowac osobnym
   committem. README i ten raport maja pierwszenstwo dla wyniku A0.
3. ALGO-03 (duplikaty append w `eta_calib_shadow.jsonl`) jest odrebna karta i
   nie zostala przy okazji ukryta ani rozszerzona.
