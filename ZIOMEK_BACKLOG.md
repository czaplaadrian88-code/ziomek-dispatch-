# ZIOMEK AI DISPATCHER - BACKLOG ROZWOJU

> Status: AKTYWNY - SPRINT 1 WDROZONY, OBSERWACJA SHADOW W TOKU; AUDIT360
> DR1A/OPS0 SOURCE-ONLY W MASTERZE, D1/R0 HOLD DO AT-214; A0/I1/N0
> WDROZONE I ZWERYFIKOWANE LIVE, N0 FOLLOW-UP `0891b06` OK; ETA PROMOCJA
> HOLD; SEC0+SEC1 AUDITOR SOURCE W MASTERZE, HOST NADAL HOLD; E0+E1/DATA0
> BRANCH-COMPLETE I OFF; V214 WAIT/PENDING DO AT-214; OD-01..OD-07 ORAZ
> ODR-002 AUTHORITY OWNERSHIP OWNER_CONFIRMED 2026-07-12;
> IMPLEMENTACJA/KPI-NUMBERS/KARTA RUNTIME NADAL HOLD; MAILEK M-P0-01
> LIVE, DWUDNIOWY WERDYKT OCZEKUJE NA AT-215 (2026-07-15 09:25 UTC);
> M-P0-02 CEIDG/PANORAMA LIVE `527fd59`, WERDYKT DWÓCH BIEGÓW AT-216
> 15.07 08:10 UTC; M-P1-01 WSPÓLNA POLITYKA PERSONALIZACJI AUDYTU
> LIVE `2b5a616`; KOREKTA 13 FALSZYWYCH FLAG LIVE APPLIED 15:25 UTC,
> BACKUP+ROLLBACK READY, POSTCHECK 13/13; OBSERWACJA POLICY DO 15.07
> Data utworzenia: 2026-07-09
> Zakres: Ziomek Dispatcher, stan runtime, aplikacja kuriera i granice integracyjne
> Wlasciciel biznesowy: Adrian
> Wlasciciel techniczny: Tech Lead / agent realizujacy zaakceptowane zadanie

## 1. Cel dokumentu

Ten plik jest repozytoryjnym punktem wejscia do prac nad Ziomkiem. Zawiera tylko
zadania, ktore po audycie nadal wymagaja wykonania lub jawnej decyzji. Historyczne
opisy w `TECH_DEBT.md`, `ZIOMEK_MASTER_KB.md`, `docs/archive/` i pamieci Claude sa
materialem zrodlowym, ale nie wyznaczaja kolejnosci wykonania tego backlogu.

Utworzenie i aktualizacja tego dokumentu nie zmienia zachowania produkcyjnego.
Zmiana zachowania Ziomka nastapi dopiero po osobnej akceptacji konkretnego zadania.

## 2. Kontrakt wspolpracy przed kazdym zadaniem

Przed rozpoczeciem kazdej pracy agent przedstawia:

1. Problem i dowod, ze nadal istnieje.
2. Zakres plikow, uslug i danych, ktorych dotknie.
3. Co dokladnie zmieni sie w zachowaniu Ziomka po zakonczeniu.
4. Ryzyka, zaleznosci, plan testow i sposob rollbacku.
5. Czy zadanie wymaga decyzji biznesowej, flagi, restartu lub deployu.

Bez tego opisu nie zaczynamy edycji. Flip flagi, restart, deploy, migracja danych i
zmiana relacji HARD/SOFT zawsze wymagaja osobnego ACK po pokazaniu gotowego wyniku.

## 3. Priorytety i effort

Priorytety:

- **P0** - ryzyko zlej decyzji, utraty/spojnosci danych, bezpieczenstwa lub brak
  wiarygodnego baseline. Praca przed nowymi funkcjami.
- **P1** - wysokie ryzyko powtarzalnego incydentu albo bloker bezpiecznej autonomii.
- **P2** - skalowanie, upraszczanie architektury i przygotowanie integracji.
- **P3** - higiena, redukcja entropii i kosztu utrzymania.

Effort agenta obejmuje diagnoze, implementacje, testy, replay i raport. Nie obejmuje
czasu oczekiwania na dane produkcyjne ani ACK:

| Rozmiar | Effort agenta | Typowy zakres |
|---|---:|---|
| XS | do 30 min | odczyt, korekta dokumentu, pojedynczy checker |
| S | 1-2 h | lokalny fix z testami |
| M | 3-5 h | kilka call-site'ow, testy integracyjne |
| L | 6-10 h | zmiana kontraktu miedzy modulami, replay |
| XL | 2-4 dni agenta | migracja lub refaktor wielu procesow |

Estymaty maja niepewnosc ok. 35%. Po diagnozie wstepnej agent aktualizuje effort
przed rozpoczeciem implementacji.

## 4. Kolejka wykonawcza

### P0 - najpierw

| ID | Zadanie | Dowod / problem | Co zmieni sie po wykonaniu | Effort | Bramka | Status |
|---|---|---|---|---:|---|---|
| Z-P0-01 | Kanon R6/R27/SLA i koncowy invariant firewall | OD-04/OD-07 rozstrzygnely intencje: R6=`in_vehicle_age` possession→handoff 35/40 Alarm; R27 commitment immutable, `5<|Δ|<=10` jawny breach, `|Δ|>10` zakaz. Baseline ma inne anchory i niepelny Alarm/Always-propose. | Osobny sprint zwiąże physical eventy, wszystkie bliźniaki, replay i execution authority bez zmiany decyzji przez samą dokumentację. | L + 2 dni obserwacji | Semantyka OWNER_CONFIRMED; event binding, implementacja, replay, flip i live ACK nadal HOLD | OWNER DECISION DONE 12.07; R0 TECH ACCEPT/HOLD `1b38447`; D1 branch `e193f2a`; nic nie flipnięto |
| Z-P0-02 | Naprawa wieloprocesowego zapisu geocode cache | `flock` jest zakladany na unikalnym tempfile, wiec nie serializuje load-merge-save miedzy procesami. | Cache adresow, restauracji i negative cache przestanie gubic poprawne wpisy przy rownoleglym geokodowaniu. | M | Bez flipa; pelna regresja geocode | DONE - LIVE |
| Z-P0-03 | Przywrocenie zielonego baseline testow | REOPENED 10.07 po flipie parsera i Audycie 360: default 4846/1; STRICT 4792/6. TEST-11 czytal live `flags.json`, a TEST-12 mial piec klas live reads i dwa ukryte prod-write. | Baseline jest deterministyczny: syntetyczne flags/systemd/state, dokladny live-smoke i tripwire anty-prod bez oslabenia guarda. | M | Zero zmian produkcyjnych; rollback = revert test-only fix-forward | DONE — `4e782e8` + T0 fix-forward z brancha `f015c9f`; tmux57 CLOSED |
| Z-P0-04 | CAS i wspolna granica planu — REOPENED 10.07 | Dispatcherowe call-site'y CAS sa LIVE, ale Audyt 360 potwierdzil pominiety writer panelu (SPRI-02/DANE-01), odrzucanie strategii solvera, rozjazd stops i null-duration=teleport (TRAS-01/02/03) oraz false-conflict touch_plan (SPRI-03). | Jeden cross-repo owner domknie decyzja→store→panel→apka: prawidlowa kolejnosc, fail-closed czas nogi, provenance/manual marker i CAS bez lost-update/resurrect. | L/XL | Po A360-H1; jeden lane PLAN; deploy readers-first/writer-second i restart za ACK | REOPENED - A360-P0 QUEUED |
| Z-P0-05 | Retry/DLQ dla eventow failed | Historycznie 106 `NEW_ORDER` ma status failed; brak attempt count, error i automatycznego retry. | Blad przejsciowy nie zgubi obslugi zlecenia; poison event trafi do DLQ z diagnoza i limitem prob. | L | Decyzja o retry policy | SOURCE/PREP E0+E1 branch complete: E0 `5dd4c80`, E1 kod `c9d02b4`+`5044911`, final branch `66a2591`; envelope/outbox/receipts/journal/recovery gotowe i default OFF. Merge/worker/policy/migracja/live HOLD; retencja `order_state` czeka na checkpoint |
| Z-P0-06 | Bezpieczenstwo courier API — auth + ownership | Rate-limit per-IP i wspolny ownership guard status/arrival/ground-truth/payment sa LIVE; BEZP-04 pozostaje osobna decyzja UX. | Foreign/missing/malformed dostaja identyczne 403 przed order-specific I/O; owner zachowuje kontrakt. | M | Wydane za ACK 11.07; rollback przywraca BEZP-02, preferowany fix-forward | DONE/LIVE `320aa0e`, API master `fa249e6`; 186/186 predeploy, 19/19 postrestart, PID 925329/NRestarts0/health PASS |
| Z-P0-07 | R4-GOVERNANCE: podpisana karta execution authority i runtime gate | ODR-002 rozstrzyga owner-only promotion, zakaz samopromocji i fail-closed card check. Dzisiaj nie ma autorytatywnej podpisanej karty sprawdzanej przed każdym execution entry pointem. | Candidate może powstać izolowanie; docelowo każda egzekucja sprawdzi wersjonowaną kartę, a brak/błąd da `recommend-only`/`HOLD`; automatyczna degradacja nie umożliwi re-promocji. | XL | Każda zmiana karty/schema/parsera/gate/policy/ochrony = R4; evidence hash + niezależny review + owner-only approval/signature + deterministic apply; osobny ACK live | OWNER DECISION DONE 12.07 (`ODR-002`); implementacja/karta/runtime ZERO, HOLD. Aktywny lease `docs/chief-engineer/**` nietknięty i musi zrekoncyliować nowe źródło. |
| M-P0-01 | Mailek: idempotentna wysyłka follow-up po granicy SQLite/SMTP | 13.07 SMTP i IMAP zakończyły się sukcesem, ale lock SQLite zostawił draft pending, więc automat mógł wysłać go drugi raz. | Schema v6 zapisuje trwały claim i Message-ID przed SMTP; niepewny skutek jest HOLD bez auto-retry, a legacy draft został zrekonsyliowany po zgodnym dowodzie SMTP+IMAP. | M + 2 runy obserwacji | LIVE `b4cdcbb`, rollback tag `mailek-pre-followup-idempotency-v6`; finalny read-only verifier at-215 15.07 09:25 UTC | LIVE; schema/integrity/cron smoke PASS, listener PID 2431013/NRestarts0; operacyjny DoD czeka na werdykt dwóch runów. |
| M-P0-02 | Mailek: fail-closed discovery CEIDG i bezpieczny fallback podaży | 13.07 cron dał `raw=0/rc=0` mimo 4 błędów HTTP; CEIDG i Panorama mapowały awarię/protocol drift na zdrowe zero, a `ready_for_drafting` spadło do 27. | Jawne `success/empty/partial/unavailable`; CLI rc=3 dla źródła niepełnego; cron może uruchomić tylko `ceidg→panorama`, nigdy Apify, i nie odpala drugiego writera po częściowym insercie. | M + 2 dni obserwacji | LIVE `527fd59`, docs postimage `e961291`, rollback tag `mailek-pre-ceidg-resilience-20260713`; read-only verifier at-216 15.07 08:10 UTC | LIVE po ACK: 220/220 pre i postdeploy, mutation oracle + backend/import smoke PASS; listener PID 2431013/NRestarts0, bez restartu/migracji/flagi; operacyjny DoD czeka na dwa naturalne biegi. |

### P1 - stabilnosc przed autonomia

| ID | Zadanie | Dowod / problem | Co zmieni sie po wykonaniu | Effort | Bramka |
|---|---|---|---|---:|---|
| Z-P1-01 | Formalny FSM zlecen | `state_machine` zna statusy, ale nie ma jednej mapy dozwolonych przejsc; zly pickup timestamp jest zastepowany `now()`. | Nielegalne przejscie i uszkodzony czas beda kwarantannowane zamiast po cichu zmieniac prawde SLA. | L | SOURCE/PREP E0+E1 branch complete: formalny graf, kanoniczna koperta, failure journal, outbox i receipts per consumer sa gotowe na branchach. ON/merge nadal HOLD do policy, workera, checkpointu, migracji i osobnego ACK |
| Z-P1-02 | Kanoniczny ground truth ETA i SLA | OD-01/OD-02 rozdzielily exit/possession i arrival/handoff; OD-03 wymaga fail-closed gate per event/source/cohort. Fizyczne source/event contracts i liczby nadal nie istnieja. | Faza A raportuje tylko nazwane eventy/proxy i lokalny support; żadna komórka bez bramy nie promuje KPI/modelu. | L | Semantyka OWNER_CONFIRMED; physical bindings + raport danych + późniejsze liczby, champion v2 i promotion nadal `HOLD/UNBOUND` |
| Z-P1-03 | Stage-level tracing i backpressure | Latencja decyzji: p95 ok. 2,02 s, max 7,19 s; rekord nie rozbijal czasu na etapy. | Faza A mierzy queue/fleet/OSRM/solver/selection/write; nie wlacza limitu kolejki, budzetu ani backpressure. | M | **LIVE SHADOW CANARY ON od 2026-07-11 10:27 UTC**; at-214, werdykt po 48 h |
| Z-P1-04 | Jawny `DecisionContext` i wiarygodny replay | Effects buffer obejmuje tylko czesc zapisow; Audyt 360 dodatkowo wykazal PARTIAL CORE-01, process-local rozjazdy CORE-02/03 i niekonsumowany gate TEST-03. | R0 rozdziela INPUT_MISS/OSRM_MISS/CRITICAL/SOFT/PARITY, waliduje frozen input i zuzywa OSRM najwyzej raz; pozniej context usunie ukryte kanaly procesu. | XL | R0 TECH ACCEPT `1b38447`, kod NOT MERGED do at-214; narrow/partial, surplus recorded OSRM nadal rezyduum |
| Z-P1-05 | Kanoniczna tozsamosc kuriera — **DONE Faza A+B 2026-07-10** (pakiet `identity/`, walidator kolizji, onboarding 5-plikowy, backfill names 19→0, kanon pisowni z grafiku; delegacja 9× norm + scoring worker/panel_roster do registry — parity 177/177, golden 21417 par = 0 roznic; ODLOZONE: unifikacja profili ×10/×5 vs ×10/×10 [pomiar+ACK], Krok 4 czytelnicy plikow→registry, konsolidacja courier_api.db) | 121 aliasow mapuje sie do 65 CID; 54 CID maja wiele aliasow, 20 nie ma wpisu w `courier_names`. | Grafik, GPS, PIN, tier, plan i rozliczenia beda laczone przez CID z kontrolowanymi aliasami. | L | Migracja bez zmiany CID |
| Z-P1-06 | Prywatnosc i retencja world records/logow | Rekordy zawieraja adresy, nazwiska i GPS, maja `0644` i rosna o setki MB dziennie. | Dane beda pseudonimizowane lub szyfrowane, `0600`, kompresowane i usuwane wedlug retencji. | M | SOURCE/PREP branch complete `a6ca337`; `compat` default, private/mirror/migracja/delete HOLD do reader matrix, outbox, at-214, B-05 i ACK |
| Z-P1-07 | Rejestr i cykl zycia flag — **FUNDAMENT DONE; FOLLOW-UP A360-FLAG-01/04** | Rejestr 505/505 i checker sa gotowe, ale carry-chain jest kluczem-wabikiem, a czesc flag behawioralnych nadal zyje poza JSON. | Najpierw decyzja retire-vs-unify; pozostawiona flaga dostanie realny consumer, ON!=OFF, fingerprint i nadal pozostanie OFF do osobnego ACK. | M | Po R0/D1; bez laczenia z flipem |
| Z-P1-08 | Reprodukowalne srodowisko zaleznosci | `requirements-dispatch-venv.txt` pinuje rdzen OR-Tools, ale API ma 4 niepinowane wymagania; CVE/EOL nie maja zatwierdzonego feedu. | DEP0 daje przenosny config i deterministyczna mape 6/6 procesow→venv→manifest→runtime; aktualizacje osobnymi sprintami. | M | DEP0 DONE `53730e9`; pip-check/import PASS, CVE/EOL UNKNOWN; zero zmian venv/manifestow |
| Z-P1-09 | Jedna polityka czasu i testy DST | W kodzie pozostaja rozne zalozenia dla naive datetime; `sla_tracker` dokumentuje uspiony naive-Warsaw-as-UTC bug. | Wszystkie granice beda przyjmowac jawny typ czasu; testy pokryja DST, polnoc i rollover dnia. | L | Bez zmiany historycznych danych |
| Z-P1-10 | Restore game day i RTO/RPO | Istnienie backupu nie dowodzi odtworzenia; real provenance/decrypt obu DB/app smoke i service RTO/RPO nadal nie sa udowodnione. | DR0 daje fail-closed source/fake: strict SQL, manifest, budgety, provenance i cleanup; DR1A przygotowuje carrier/app-smoke, DR1B wykona real game-day. | M | DR0 SOURCE ACCEPT `d873f0b`; DR1A SOURCE/FAKE IN MASTER w `a360-wave3-safe-source-integrated-20260711`, C32 fixed, NOT INSTALLED/NOT EXECUTED; real verify/artifact/drill/RTO = DR1B HOLD / NOT DONE |
| Z-P1-11 | Triage i disposition Audytu 360 | Pakiet ma 110 wpisow: 49 CONFIRMED, 4 REFUTED, 4 PARTIAL, 1 PLAUSIBLE, 52 UNVERIFIED; severity 1 P1/47 P2/58 P3/4 NONE. | Potwierdzone naprawy sa zgrupowane bez duplikatow, PARTIAL/PLAUSIBLE maja verify-first, UNVERIFIED tylko reprodukcje. | M | DONE — pakiet, walidator i kolejka zintegrowane; decyzje HARD/SOFT, security i ops osobno |
| Z-P1-12 | Flow-liveness panelu, API i decyzji | OPS-02: krytyczne uslugi moga restartowac sie, lecz nie maja bezposredniego, zweryfikowanego alert route; sam PID nie wykrywa ciszy przeplywu. | Health panel/API i brak decyzji w peak beda mialy watermark, prog, ownera, consumer i kontrolowany negative control. | M | Kod/prep bez live; instalacja/restart za osobnym ACK |
| M-P1-01 | Mailek: jeden kanon personalizacji Validator/Q1/audyt | 13/18 dzisiejszych auto-maili CEIDG mialo falszywy jedyny powod `notes_overlap_low=1`: audyt duplikowal tokenizacje i twardy prog 2, gdy dwa upstream gate'y kanonicznie wymagaja dla CEIDG 1. | Wspolny helper utrzymuje `CEIDG=1`, inne/brak roli=2, zero-overlap nadal fail; read-only replay daje 5/13 legacy kontra 18/0 candidate. | S + 2 dni obserwacji | LIVE policy `2b5a616`. Po jawnym ACK data apply z brancha `e34c7ac`: backup API integrity OK, CAS 13/13, flags+reasons clear, audited_at 13/13 preserved, rollback per-row READY; pre/post 240/240. Nat. audyt 15:30 candidates0, kohort nadal clean. Bez deployu/restartu/crona/flagi; obserwacja policy do 15.07 |

### P2 - skalowanie i granice produktu

| ID | Zadanie | Dowod / problem | Co zmieni sie po wykonaniu | Effort | Bramka |
|---|---|---|---|---:|---|
| Z-P2-01 | Naprawa sygnalow mode layer S2/S3 | Observer nie dostarcza `s2_infeasible_rate`, ustawia defer count na 99; 1188/1188 obserwacji to S1. | Shadow rzeczywiscie sprawdzi trzy tryby i ich przejscia, bez aktywacji polityki. | M + 7 dni danych | Flip dopiero po osobnym ACK |
| Z-P2-02 | Wersjonowanie i odchudzenie schematu decyzji | `best` ma do 296 pol; rekord medianowo ok. 60 KB, max 2,27 MB. | Konsumenci dostana wersjonowany kontrakt; log operacyjny i korpus ML zostana rozdzielone. | XL | Migracja czytelnikow |
| Z-P2-03 | Stabilny adapter panelu / API integracyjne | Krytyczny odczyt i zapis korzysta z prywatnego HTML, regexow, CSRF i subprocessu. Papu exact-marker recovery jest LIVE, ale kontrakt exactly-once po zewnetrznym POST nadal nie istnieje. | Awaria lub zmiana panelu bedzie izolowana w adapterze z idempotency key, read-back i typed error. | XL | I1 DONE/LIVE `b2c65b2`; szerszy kierunek partner API i exactly-once otwarte |
| Z-P2-04 | Konfiguracja miasta i tenanta | BBox, centrum, dzielnice, traffic i domyslne miasto sa bialostockie w wielu modulach. | Nowe miasto nie bedzie wymagalo kopiowania silnika ani ryzyka cross-city geocode. | XL | Decyzja B-04 |
| Z-P2-05 | Ewolucja plikow stanu do repozytorium danych | Krytyczny stan jest rozproszony po wielu JSON/JSONL; czesc plikow jest multi-writer. | Najpierw rejestr ownership/schema, potem selektywna migracja tylko plikow z realnym problemem skali lub transakcji. | XL | Bez big-bang rewrite |
| Z-P2-06 | Wiarygodny OSRM health i polityka cache | Health uznawal fallback za zdrowy OSRM; eviction nadal sortuje duzy cache pod globalnym lockiem. | Faza A rozdziela upstream/cache/fallback i mierzy contention/eviction; optymalizacja polityki cache pozostaje otwarta dla zachowania parytetu decyzji. | M | FAZA A health/telemetry DONE; optymalizacja eviction otwarta |
| Z-P2-07 | Hermetyczne testy i fixture danych — **DONE/LIVE + N0 FOLLOW-UP 2026-07-12** (root `conftest.py`: sandbox+write/delete/subprocess guard; STRICT; N0 manifest v5 5183 nodeidy; prywatny writer historii utrzymuje 0600; systemd E2E 5155/24/8/0XPASS/0fail, contract OK) | Czesc testow czytala zywe logi, aliasy i exclusion state hosta; pierwszy zwykly N0 poprawnie zatrzymal 11 nowych testow SEC0 poza manifestem i ujawnil replace 0644. | Testy dzialaja deterministycznie bez zapisu do produkcji; nocny guard nie przyjmie zmiany denominatora ani hard-error jako zielonego baseline, a historia pozostaje prywatna. | L | DONE/LIVE `0891b06`; timer active, next 13.07 01:15 UTC; raport `AUDIT360_N0_LIVE_AND_NEXT_LANES_LAUNCH.md` |
| Z-P2-08 | Least privilege dla uslug | Wiele demonow dziala jako root; hardening systemd jest nierowny. | Kompromitacja jednego procesu dostanie mniejszy zakres zapisu i odczytu sekretow. | XL | Migracja sciezek i ownership |

### P3 - redukcja entropii

| ID | Zadanie | Co zrobic | Effort |
|---|---|---|---:|
| Z-P3-01 | Backupy i martwy kod | Zweryfikowac grepem i usunac dopiero po ACK stare `.bak-*`, martwe helpery oraz nieuzywane galezie. | M |
| Z-P3-02 | Konsolidacja dokumentacji | Oznaczyc archiwum, zindeksowac decyzje i usunac sprzeczne wskazniki aktualnego stanu bez kasowania historii. | M |
| Z-P3-03 | Monitoring vs observability | Ustalic ownership i wspolne konwencje metryk, alertow i health checks. | M |
| Z-P3-04 | Redukcja broad exceptions | Zaczac od hot path: klasy bledow, reason codes, licznik fail-soft; bez masowej mechanicznej zamiany. | L |

## 4A. Audyt 360 - kolejka napraw i fale bezkolizyjne

Punkt wykonawczy: `eod_drafts/2026-07-10/AUDIT360_REPAIR_SPRINT_QUEUE.md`.
Pakiet Audytu 360 i kolejka sa czescia integracji wave-1 close na aktualnej
bazie Sprintu 3.

Pierwsza fala po bramce wlascicieli G0 — zamknieta 2026-07-11:

1. `A360-T0 TEST-TRUTH` (`Z-P0-03` + `Z-P2-07`) — DONE, test/docs integrated;
2. `A360-S0 API-OWNERSHIP` (`Z-P0-06`) — DONE/LIVE `320aa0e`, API master `fa249e6`;
3. `A360-D0 R6-DECISION` (`Z-P0-01`) — DONE docs-only `c241507`;
4. pilne osobne okno `A360-I0 CREDENTIAL` (`INFRA-P0-01`) po jawnym ACK.

Wave 2: R0 TECH ACCEPT/HOLD at-214 `1b38447` (docs-only w masterze), DR0 source
ACCEPT `d873f0b` z DR1 HOLD oraz DEP0 DONE `53730e9`. ENGINE lock Sprintu 1
zwolniono read-only: 37 znanych blobow, 0 unikalnego WIP, worktree nietkniety.
Wave 3: D1 `e193f2a` (`ultra`, branch-only, merge HOLD do at-214). DR1A i OPS0
zintegrowano source/tool-only do mastera w `a360-wave3-safe-source-integrated-20260711`:
DR1A ma fix C32, ale jest NOT INSTALLED/NOT EXECUTED i DR1B HOLD; OPS0 jest
manual-only, bez timera/konsumenta, profil UNKNOWN. Zero flag, danych, systemd,
realnego restore, tuningu i restartu. H1 nadal czeka na at-214 i integracje
R0+D1. B-01/B-02 zostały semantycznie zamknięte 12.07 przez ODR-001, ale nie
autoryzują merge, enforcement ani live.
Raport: `eod_drafts/2026-07-11/AUDIT360_WAVE3_SAFE_SOURCE_INTEGRATION.md`.

Start 2026-07-11 20:36 UTC: trzy najwyzej priorytetowe lane'y, ktore nie sa
zablokowane przez at-214 ani decyzje B-01/B-02, uruchomiono rownolegle i
branch-only:

1. `A360-A0 ETA-CALIBRATION-TRUTH` (`ALGO-01/02`, tmux68, effort max) — usuwa
   future-feature leakage i naprawia porownanie championa z challengerem na
   wspolnym holdoucie;
2. `A360-I1 PAPU-BRIDGE-RECOVERY` (`INTE-03`, tmux69, effort high) — domyka
   idempotentny recovery po sukcesie submitu bez jednoznacznego read-back;
3. `A360-N0 NIGHT-GUARD-TRUTH` (`TEST-04/05`, tmux70, effort high) — wprowadza
   wersjonowany, fail-closed mianownik suity i rozlicza bezpanskie XPASS.

Write-sety sa rozlaczne, wspolne backlogi i pamiec sa zastrzezone dla
integratora. Lane'y nie maja zgody na merge, live state, flagi, timery, deploy
ani restart. Kanoniczny baseline przed startem: 5126 passed, 27 skipped,
8 xfailed, 2 xpassed, 0 failed. Karta startu i rollback:
`eod_drafts/2026-07-11/AUDIT360_PARALLEL_SAFE_LANES_LAUNCH.md`.

Odbior 2026-07-11 21:32 UTC: wszystkie trzy lane'y sa clean, push parity i
branchowy DoD. A0 kod `2aaedcd`, finalny HEAD `a0322f8`; uczciwy replay po
usunieciu future leakage pogorszyl MAE o ok. 2,5%, wiec model pozostaje
`HOLD/UNBOUND` i nie ma promocji. I1 `dca2715` daje idempotentny exact-marker
recovery bez resubmitu; deploy mostu pozostaje HOLD do osobnego ACK. N0 kod
`f6a2e4e`, finalny HEAD `53d8446` usuwa 2 XPASS i daje fail-closed suite
manifest; source deploy przed kolejnym nocnym biegiem pozostaje osobna bramka.
Nie wykonano merge, flag, danych, deployu ani restartu. Raport odbioru:
`eod_drafts/2026-07-11/AUDIT360_PARALLEL_SAFE_LANES_CLOSE.md`.

Wydanie 2026-07-11 21:46-22:43 UTC za biezacym ACK Adriana domknelo zakres
live. Kodowy punkt wydania dispatch jest na `4c351d5`, tag
`a360-a0-n0-live-verified-20260711`; Papu workspace master/origin jest na
`b2c65b2`, tag `a360-papu-recovery-live-20260711`. N0 po dwoch fix-forward
lukach znalezionych przez dokladny systemd E2E ma finalnie 5140 passed,
27 skipped, 8 xfailed, 0 XPASS/failed, verdict `OK`, timer active. A0 wykonal
kontrolowany kalibrator: oba championy zachowaly hashe, candidate zapisany,
promocja `false/HOLD`. I1 wykonal kontrolowany oneshot; zastany brak markera ma
jawny hold, po 3 probach zero resubmitu i zero dispatched. Flagi silnika i stale
procesy shadow/watcher/API nie byly restartowane. Pelny dowod, backupy i
rollback: `eod_drafts/2026-07-11/AUDIT360_A0_I1_N0_LIVE_CLOSE.md`.

Kolejne trzy rozlaczne sprinty wystartowaly 2026-07-11 23:11 UTC i zostaly
odebrane 2026-07-12 jako **SOURCE/PREP CLOSED, ZERO LIVE**:

1. `A360-SEC0 HOST-BOUNDARY-CREDENTIAL` (`max`) — branch `c30d4ed`, source
   audytora zintegrowany do mastera `c47031b`; werdykt hosta nadal HOLD, bo
   publiczne 8767/9222 i provider UNKNOWN wymagaja osobnego SEC1+ACK;
2. `A360-E0 EVENT-RELIABILITY-FSM` (`max`) — branch kod `b2a6027`, finalny
   HEAD/push `5dd4c80`; Retry/DLQ i formalny FSM default OFF, lecz review
   wykazal brak durable envelope/outbox/receipts, wiec merge/ON sa HOLD do E1;
3. `A360-DATA0 PRIVATE-LEDGER-RETENTION` (`high`) — branch `a6ca337`; `compat`
   zachowuje byte parity, private 0600 i would-delete sa source-only, mirror i
   apply/delete sa HOLD.

Swiezy baseline `23:05:55Z..23:10:40Z`: 5143 passed, 24 skipped, 8 xfailed,
0 failed/XPASS; trzy zegarowe preshift self-skipy przeszly do pass. Finalne
SEC0: DEFAULT 5154/24/8, STRICT 5104/74/8; DATA0: DEFAULT 5175/24/8, STRICT
5125/74/8; E0: DEFAULT 5189/24/8, STRICT 5139/74/8. Zero failed/XPASS. Launch:
`eod_drafts/2026-07-12/AUDIT360_SEC0_E0_DATA0_LAUNCH.md`; karta projektowa:
`eod_drafts/2026-07-11/AUDIT360_NEXT_THREE_SPRINTS.md`. SEC0 potrzebuje ACK na
sieci/credential/restart; DATA0 nie dotyka live corpus przed at-214 i nie usuwa
danych przed B-05. Odbior:
`eod_drafts/2026-07-12/AUDIT360_SEC0_E0_DATA0_CLOSE.md`.

Kolejne trzy lane'y uruchomiono 2026-07-12 08:34 UTC na wspolnej bazie
`0891b06`, w osobnych worktree i bez zgody na wspolne pliki lub nowe live:

1. tmux74 `A360-V214 CANARY-DISPOSITION` (`high`), branch
   `evidence/a360-v214-canary-disposition` — lekki preflight i WAIT do realnego
   outputu joba 214; bez wczesniejszego uruchomienia lub live replayu;
2. tmux75 `A360-SEC1 HOST-REMEDIATION` (`max`), branch
   `security/a360-sec1-host-remediation` — SOURCE/PREP; firewall/provider/bind/
   credential/kontener/restart live nadal twardo zablokowane do osobnego ACK;
3. tmux76 `A360-E1 DURABLE EVENT OUTBOX` (`max`), branch
   `reliability/a360-e1-durable-outbox` — branch-only envelope, failure journal,
   outbox i receipts; bez worker/migracji/flagi/deployu/restartu live.

Karta zakresu, wpływu, testów i rollbacku:
`eod_drafts/2026-07-12/AUDIT360_NEXT_THREE_AFTER_SEC0_E0_DATA0.md`.
Launch i N0 live:
`eod_drafts/2026-07-12/AUDIT360_N0_LIVE_AND_NEXT_LANES_LAUNCH.md`.

Odbior 2026-07-12: SEC1 jest clean/pushed `44f80bc3`, a jego manualny auditor
i evidence contracts sa w masterze `c9a946c`, tag
`a360-sec1-source-integrated-20260712`. Finalny live audit nadal daje osiem
findingow i `HOLD`; nie wykonano firewalla, bindu, providera, credentialu,
kontenera ani restartu. E1 ma source `c9d02b4`+`5044911`, final branch
`66a2591`: kanoniczna koperta, outbox↔receipts/attempts z CAS, journal,
recovery, reducer i dry-run
migracji. `DURABLE_EVENT_OUTBOX_ENABLED=False`; brak workera/policy/live schema,
a retencja nie usuwa historii `order_state` bez przyszlego checkpointu. V214
pozostaje WAIT/PENDING do joba 214, ale jego branch `5375d8d` i snapshot 0600
sa trwale zapisane, wiec tmux74 zamknieto. Tmux75 i tmux76 takze zamknieto po
clean/push/snapshot; tmux58 pozostal FLIPMASTEREM/biezacym integratorem. Raport:
`eod_drafts/2026-07-12/AUDIT360_SEC1_SOURCE_LIVE_AND_E1_BRANCH_CLOSE.md`.

Domkniecie zachowanego `tmux50` 2026-07-12: WIP panelu GRF-02 zostal
zrekoncyliowany i zapisany na `nadajesz_clone/coordinator-console` jako
`5924e19`. Ikona przy godzinie nie usuwa juz calego dnia: backend atomowo skraca
ciagla zmiane o jedna godzine tylko od poczatku/konca, zachowuje auto i odrzuca
srodek zamiast cicho tracic kilka godzin. Focused 20/20, Vite build PASS, panel
full 1089 pass + 1 zastany alembic-baseline fail, mutation-probe RED→GREEN.
Po jawnym ACK Adriana kod jest LIVE od `2026-07-12 15:14:24 UTC`: rsync świeżego
builda `/admin/`, dokładnie jeden restart tylko `nadajesz-panel.service`, PID
`683706`, NRestarts0, health/admin 200, nowy asset `index-CB3bgZBR.js`, endpoint
bez tokenu 401 direct+public i zero warningów journalu. Zero flipa, migracji i
zmian danych; tag `grf02-hour-trim-live-verified-20260712` jest na origin.
Rollback runtime = backup
`/var/www/html/admin-panel.bak-grf02-hourtrim-20260712T151212Z`; nie wykonywać
wholesale `git revert 5924e19`, bo commit zawiera też wcześniej LIVE WIP z 09.07.
Obserwacja do 14.07 15:15 UTC, bez nowego timera/at-joba. Raport:
`eod_drafts/2026-07-12/GRF02_TMUX50_CLOSE.md`.

Tmux50 zamknieto po utrwaleniu WIP, ale pre-close ujawnil near-miss: poczatkowy
Claude/limit zmienil sie na `command=codex`, `attached=1`, a kill nie zostal
anulowany. Kontrola po zdarzeniu potwierdzila zero utraconych zmian plikowych
GRF-02 i ten sam zestaw obcych dirty, ale proces/scrollback sesji zakonczono.
Protokol rozszerzono o C54: zmiana tozsamosci pane, aktywna komenda lub attached
blokuje sprzatanie do ponownego audytu.

Foundation audit Prompt 01/02 zamkniety trwale 2026-07-12: docs-only branche
`codex/audit-prompt-01-20260712T140957Z` @ `14e7a5e` oraz
`codex/audit-prompt-02-20260712T153736Z` @ `bd4a4bf` sa clean, wypchniete na
origin z parity 0/0 i nie zostaly scalone. Prompt 03 nie wystartowal; Prompt 02
zachowuje historyczny status PARTIAL/READY_AFTER_OWNER_DECISIONS. Nowszy
ODR-001 z 12.07 zamyka OD-01..OD-07 jako OWNER_CONFIRMED; forensic artefakty
pozostają niezmienione, a Prompt 03 nadal nie wystartował.
Zamkniecie tmux nie usuwa zachowanych worktree. Raport:
`eod_drafts/2026-07-12/AUDIT_PROMPT01_02_SESSION_CLOSE.md`.

Cleanup follow-up: tmux77 zamknieto po push/parity. Tmux78 NIE zamknieto, bo
swiezy C54 wykryl nowe zatwierdzone decyzje OD-01..OD-07 i aktywny zapis w tej
sesji; nowsza praca ownera anulowala kill. Tmux58 ma zostac ubity dopiero po
wyslaniu finalnego potwierdzenia z biezacej sesji.

52 `UNVERIFIED` nie sa zadaniami naprawczymi. Cztery `REFUTED` pozostaja
zamkniete. Pelne disposition wszystkich CONFIRMED/PARTIAL/PLAUSIBLE, aktualne
dirty locki, file families, testy i rollback sa w punkcie wykonawczym powyzej.

## 5. Szczegolowe karty zadan

Ponizsze karty opisuja zakres docelowy. Przed implementacja agent ponownie
weryfikuje problem i przedstawia plan konkretnego kroku zgodnie z sekcja 2.

### Z-P0-01 - Kanon R6/R27/SLA i invariant firewall

- **Na czym polega:** spisanie jednej, wykonywalnej macierzy regul dla wybranego
  planu po wszystkich etapach rankingu, `best_effort` i `ALWAYS_PROPOSE`.
- **Zakres pracy:** wspolny `RuleVerdict`, identyfikatory regul, obsluga wyjatkow
  (paczki, czasowki, przeciazenie), serializacja wyniku i replay historyczny.
- **Co zmieni w Ziomku:** faza A tylko ujawni finalne naruszenia w kazdej decyzji.
  Faza B może powstać dopiero po związaniu eventów OD-07, mapie wszystkich
  bliźniaków, replay i osobnym ACK; B-01/B-02 są semantycznie zamknięte.
- **Czego nie zmieni w fazie A:** wyboru kuriera, kolejnosci trasy i werdyktu.
- **Koniec zadania:** komplet testow wyjatkow, replay bez roznic decyzyjnych oraz
  48 godzin danych shadow bez brakujacego lub sprzecznego werdyktu.
- **Effort:** L plus 2 dni obserwacji; semantyka jest OWNER_CONFIRMED, ale
  event binding, implementacja i egzekwowanie nadal wymagają pełnego #0.

### Z-P0-02 - Wieloprocesowy zapis geocode cache

- **Na czym polega:** zastapienie blokady unikalnego tempfile stalym lockfile'em
  wspolnym dla wszystkich procesow zapisujacych dany cache.
- **Zakres pracy:** jedna transakcja `LOCK_EX -> load -> merge -> fsync -> replace`
  dla cache adresow, restauracji i negative cache; zachowanie ochrony pinow.
- **Co zmieni w Ziomku:** rownolegle geokodowania przestana kasowac wzajemnie
  poprawne wpisy, wiec kolejne decyzje nie beda ponawiac utraconej pracy sieciowej.
- **Czego nie zmieni:** istniejacych wspolrzednych, algorytmu wyboru i polityki bbox.
- **Koniec zadania:** wieloprocesowy test odtwarza lost-update na starej wersji i
  dowodzi zachowania obu wpisow po naprawie; pelna regresja geocode jest zielona.
- **Effort:** M; bez flipa i bez migracji danych.

### Z-P0-03 - Zielony baseline testow

- **Na czym polega:** usuniecie pieciu znanych faili bez zmiany zachowania produkcji.
- **Zakres pracy:** wpisanie `ENABLE_GEOCODE_PIN_MEMORY_FALLBACK` do wymaganego
  rejestru/dokumentacji oraz odizolowanie `get_excluded_cids()` w testach working.
- **Co zmieni w Ziomku:** nic w runtime; zmieni wiarygodnosc CI i lokalnej regresji.
- **Czego nie zmieni:** wartosci flag produkcyjnych ani manual overrides floty.
- **Koniec zadania:** pelna suita ma 0 failed, a trzy testy working przechodza przy
  dowolnej zawartosci zywego `manual_overrides.json`.
- **Effort:** S; pierwsze zadanie Sprintu 1.

### Z-P0-04 - CAS i stale write planow

**Reopened przez Audyt 360 (10.07):** poprzedni DONE obejmowal writerow
dispatchera, ale nie zywy writer panelu. Ten sam sprint musi objac SPRI-02/03,
TRAS-01/02/03, BLIZ-02 i DANE-01; samo dodanie `expected_version` albo samo
poszerzenie allow-listy strategii byloby zmiana czesciowa.

- **Na czym polega:** uzycie istniejacego `expected_version` we wszystkich
  produkcyjnych cyklach odczyt-obliczenie-zapis planu.
- **Zakres pracy:** przenoszenie wersji z `load_plan`, obsluga konfliktu w watcherze
  i rechecku, metryka konfliktow oraz ponowne obliczenie albo bezpieczny skip.
- **Co zmieni w Ziomku:** starszy regen nie nadpisze planu uwzgledniajacego nowsze
  przypisanie, pickup lub zmiane worka.
- **Czego nie zmieni:** normalnego wyniku zapisu bez konfliktu.
- **Koniec zadania:** deterministyczny test dwoch writerow potwierdza, ze nowszy plan
  przezywa; polityka konfliktu jest jawna i monitorowana.
- **Effort:** L; przed kodem trzeba zaakceptowac retry vs keep-current.

### Z-P0-05 - Retry i DLQ eventow

**Stan 2026-07-11 23:11 UTC:** RUNNING w tmux72 jako wspolny lane
`A360-E0 EVENT-RELIABILITY-FSM` z Z-P1-01. Istniejaca branchowa Faza A
`7eda1b0` + `32745f9` nie jest wdrozona i musi przejsc semantic review na
aktualnym masterze; nie wolno osobno wlaczyc retry bez grafu FSM.

- **Na czym polega:** rozdzielenie bledow przejsciowych od trwalych oraz dodanie
  kontrolowanego ponawiania zamiast koncowego statusu `failed` bez kontekstu.
- **Zakres pracy:** `attempt_count`, `last_error`, `next_attempt_at`, backoff,
  maksymalna liczba prob, dead-letter queue, alert i narzedzie replay.
- **Co zmieni w Ziomku:** chwilowa awaria panelu, geocode lub stanu nie zgubi eventu;
  poison event bedzie widoczny i odseparowany.
- **Czego nie zmieni:** idempotentnych event ID i kolejnosci poprawnie obsluzonych
  zdarzen; retry nie moze tworzyc podwojnych przypisan.
- **Koniec zadania:** test crash-restart-retry, test poison eventu i metryki wieku
  kolejki; zero duplikatow efektow po replay.
- **Effort:** L; wymaga uzgodnienia limitow i backoffu.

### Z-P0-06 - Ochrona logowania kurierow

**Stan po Sprincie 2 i Audycie 360:** rate-limit per-IP jest LIVE, ale karta
pozostaje otwarta jako `A360-S0 API-OWNERSHIP`. BEZP-02 wymaga wspolnego guarda
order→CID dla status/arrival/ground-truth/parcel. BEZP-04 jest osobna decyzja UX
o minimalnym katalogu przed logowaniem; nie wolno jej domknac przypadkowym 401.

- **Na czym polega:** ograniczenie enumeracji floty i masowego blokowania PIN-ow.
- **Zakres pracy:** limity per CID, per IP i globalne, poprawne zaufanie do adresu
  klienta za proxy, alert burst, jednolita odpowiedz bledu i przeglad `/api/couriers`.
- **Co zmieni w Ziomku:** pojedynczy klient nie zablokuje logowania calej floty, a
  atak lub wadliwa wersja aplikacji zostanie szybko wykryta.
- **Czego nie zmieni:** poprawnego logowania, aktywnych sesji i CID kurierow.
- **Koniec zadania:** test rozproszonego brute-force, test lockoutu i test poprawnego
  klienta za reverse proxy; gotowy runbook odblokowania.
- **Effort:** M; wymaga przegladu nginx i decyzji o UX listy kurierow.

### Z-P1-01 - Formalny FSM zlecen

**Stan 2026-07-11 23:11 UTC:** RUNNING razem z Z-P0-05 w jednym lane A360-E0,
tmux72, branch-only. Retry policy, kompatybilnosc historycznego replayu,
migracja live i uruchomienie workera sa osobnymi bramkami.

- **Na czym polega:** zdefiniowanie dozwolonych przejsc statusu i warunkow dla
  kazdego typu eventu zamiast samych nazw statusow.
- **Zakres pracy:** mapa `from -> event -> to`, wymagane pola, idempotentne
  powtorzenia, kwarantanna nielegalnych eventow i odrzucenie zlego timestampu.
- **Co zmieni w Ziomku:** delivered nie wroci do picked/returned, a niepelny event
  nie stworzy fantomowego zlecenia ani fikcyjnego czasu SLA.
- **Czego nie zmieni:** poprawnych przejsc i zachowan potrzebnych do reconcile.
- **Koniec zadania:** testy calego grafu, replay historii bez utraty legalnych zdarzen
  oraz osobny licznik odrzuconych przejsc.
- **Effort:** L; wdrozenie etapowe najpierw shadow/log-only.

### Z-P1-02 - Ground truth ETA i SLA

- **Na czym polega:** zbudowanie wersjonowanej tabeli faktów bez aliasowania
  restaurant exit z possession ani delivery arrival z customer handoff.
- **Zakres pracy:** wspolne okna czasowe, jawne physical events i proxy,
  kohorty, coverage, lineage oraz osobna fail-closed brama per event/source/cohort.
- **Co zmieni w Ziomku:** modele i progi beda promowane na podstawie realnego
  dojazdu, a raport nie polaczy licznikow z roznych okresow.
- **Czego nie zmieni:** live ETA przed zatwierdzeniem nowej bramki promocji.
- **Koniec zadania:** reprodukowalny raport z jawnym mianownikiem każdej komórki,
  lineage, MAE/bias/coverage per event/source/cohort i test braku leakage.
- **Stan Fazy A 2026-07-10:** implementacja i read-only replay gotowe na
  `sprint3/eta-observability-osrm`; exact physical event bindings pozostają
  `unbound`, a klasyfikacja paczek ma niepelne coverage.
- **Owner update 2026-07-12:** OD-01/02/07 wiążą semantykę eventów, OD-03
  strukturę bram. Progi liczbowe pozostają `UNBOUND` do raportu danych.
- **Stan A360-A0 2026-07-11:** source jest w masterze `4c351d5`, kontrolowany
  bieg live przeszedl, lecz uczciwa bramka zwrocila `HOLD`; oba champion maps
  pozostaly bajtowo bez zmian. Kandydat nie moze zostac promowany bez artifactu
  championa v2 i zatwierdzonego KPI.
- **Effort:** L; semantyka KPI jest rozstrzygnięta, ale wymaga event binding,
  raportu danych i późniejszego jawnego związania liczb przed promocją.

### Z-P1-03 - Stage-level tracing i backpressure

- **Na czym polega:** zmierzenie czasu i kolejek kazdego etapu jednej decyzji.
- **Zakres pracy:** span dla pre-recheck, fleet, fan-out kandydatow, OSRM, solvera,
  selection, serializacji i efektow; histogramy. Limit backlogu jest osobna faza
  dopiero po pomiarze i decyzji o polityce degradacji.
- **Co zmieni w Ziomku:** alarm wskaze konkretny etap, a naplyw zlecen nie stworzy
  nieograniczonej kolejki i coraz bardziej spoznionych decyzji.
- **Czego nie zmieni:** heurystyk ani solvera w pierwszej fazie pomiarowej.
- **Koniec zadania:** suma rozlacznych spanow zgadza sie z latency, raport pokazuje
  p50/p95/max per etap, a sidecar ma wiarygodny mianownik i coverage utraty.
- **Stan Fazy A 2026-07-11:** kontrakty `decision_timing.v1` i
  `decision_stage_timing.v1` sa LIVE SHADOW za flaga obserwacyjna ON od
  `10:27:12 UTC`. Logrotate `daily/rotate 30/maxsize 100M` jest zainstalowany,
  sidecar ma 0600, a pierwszy join mial 1/1 coverage i zero utraty. Canary trwa
  minimum 48 h; at-214 wykona werdykt 13.07 12:15 UTC. Paired replay ma zero
  roznic krytycznych, ale nie pelne byte-parity pola `pool_feasible+reason`.
- **Effort:** M; optymalizacje sa osobnymi zadaniami po pomiarze.

### Z-P1-04 - DecisionContext i efekty uboczne

- **Na czym polega:** zastapienie proces-globalnych buforow jawnym kontekstem jednej
  decyzji przekazywanym przez pipeline i watki kandydatow.
- **Zakres pracy:** snapshot flag/czasu, recorder OSRM, efekty, diagnostyka, ID
  decyzji i commit efektow dopiero po finalnym werdykcie.
- **Co zmieni w Ziomku:** dwie rownolegle decyzje nie wymieszaja tolerancji, logow,
  calli OSRM ani efektow; replay bedzie blizszy deterministycznemu.
- **Czego nie zmieni:** funkcji scoringowych poza sposobem dostarczania zaleznosci.
- **Koniec zadania:** test rownoleglych decyzji z rozdzielonymi recorderami oraz
  parity replay dla sekwencyjnego ruchu.
- **Effort:** XL; wykonywac po tracingu, malymi migracjami call-site'ow.

### Z-P1-05 - Kanoniczna tozsamosc kuriera

- **Na czym polega:** utworzenie jednego rekordu kuriera, w ktorym CID jest kluczem,
  a nazwy z grafiku, panelu, GPS i aplikacji sa wersjonowanymi aliasami.
- **Zakres pracy:** schema, walidator kolizji, migracja czytelnikow, narzedzie
  onboarding/offboarding oraz raport brakujacych tierow i nazw.
- **Co zmieni w Ziomku:** ta sama osoba nie rozpadnie sie na kilka encji, a alias nie
  przypisze grafiku, PIN-u lub planu innemu kurierowi.
- **Czego nie zmieni:** istniejacych CID i historycznych rozliczen.
- **Koniec zadania:** zero konfliktow w biezacym rejestrze, test alias collision i
  jeden lookup uzywany przez flote, API oraz grafik.
- **Effort:** L; migracja etapowa z raportem zgodnosci.

### Z-P1-06 - Prywatnosc i retencja logow

**Stan 2026-07-11 23:11 UTC:** sprint `A360-DATA0` RUNNING w tmux73. Development
branch-only zaczyna od mapy wszystkich writerow i atomicznego 0600. Live
corpus pozostaje nietkniety do at-214; delete jest zabroniony do decyzji B-05.

- **Na czym polega:** klasyfikacja danych wrazliwych i ograniczenie dostepu oraz
  czasu przechowywania world records, GPS i decyzji.
- **Zakres pracy:** `0600`, pseudonimizacja ID, redakcja adresow, kompresja,
  rotacja, retencja, rejestr odbiorcow i procedura usuniecia.
- **Co zmieni w Ziomku:** wyciek lokalnego konta lub archiwum ujawni mniej danych,
  a dysk nie bedzie rosl bez ograniczenia.
- **Czego nie zmieni:** zdolnosci replay; potrzebne pola diagnostyczne zostana w
  kontrolowanym, ograniczonym zbiorze.
- **Koniec zadania:** test uprawnien, zmierzona redukcja rozmiaru, udany replay z
  pseudonimizowanego rekordu i zaakceptowana retencja B-05.
- **Effort:** M.

### Z-P1-07 - Rejestr i cykl zycia flag

- **Na czym polega:** nadanie kazdej fladze jednego kanonu, wlasciciela i planu
  przejscia `planned -> shadow -> live -> retired`.
- **Zakres pracy:** maszynowy rejestr, check CI, data review, default, procesy
  konsumujace, rollback i warunek usuniecia; inwentaryzacja trzech swiatow.
- **Co zmieni w Ziomku:** nowa flaga nie ominie testow i dokumentacji, a martwe
  eksperymenty nie beda wiecznie zwiekszac liczby kombinacji zachowania.
- **Czego nie zmieni:** wartosci istniejacych flag w pierwszym kroku.
- **Koniec zadania:** 100% aktywnych flag w rejestrze, checker bez baseline'owych
  wyjatkow i lista flag do osobnego, kontrolowanego retirementu.
- **Effort:** L; nie laczyc z masowym flipem.

### Z-P1-08 - Reprodukowalne zaleznosci

- **Na czym polega:** objecie manifestem wszystkich srodowisk, nie tylko rdzenia
  OR-Tools w `requirements-dispatch-venv.txt`.
- **Zakres pracy:** osobne grupy runtime/test/ML/API, piny lub constraints, skrypt
  odtworzenia venv, raport licencji/CVE i procedura kontrolowanego upgrade'u.
- **Co zmieni w Ziomku:** nowy host i CI uruchomia te same wersje, a aktualizacja
  biblioteki nie nastapi przypadkiem przy instalacji innego narzedzia.
- **Czego nie zmieni:** wersji produkcyjnych podczas samej inwentaryzacji.
- **Koniec zadania:** czyste srodowisko przechodzi import smoke i pelna suite;
  `pip freeze` nie zawiera niewyjasnionych pakietow runtime.
- **Effort:** M; upgrady wykonuje sie pozniej pojedynczymi partiami.

### Z-P1-09 - Polityka czasu i DST

- **Na czym polega:** ustalenie UTC dla obliczen i Europe/Warsaw tylko na jawnych
  granicach danych panelowych oraz prezentacji.
- **Zakres pracy:** kanoniczne parsery, typy aware, usuniecie lokalnych zalozen
  naive-as-UTC/Warsaw oraz testy DST, polnocy, rollover i `24:00`.
- **Co zmieni w Ziomku:** ten sam timestamp nie przesunie sie o 1-2 godziny zalezne
  od sciezki kodu, a zmiana czasu nie popsuje grafiku ani SLA.
- **Czego nie zmieni:** zapisanej historii; migracja danych wymagalaby osobnego planu.
- **Koniec zadania:** test matrix stref i formatow oraz zero lokalnych parserow w
  krytycznej sciezce poza zatwierdzonym adapterem.
- **Effort:** L; praca falami, nie globalny rewrite.

### Z-P1-10 - Restore game day i RTO/RPO

- **Na czym polega:** faktyczne odtworzenie systemu z backupu w odizolowanym miejscu.
- **Zakres pracy:** stan JSON/JSONL, SQLite/Postgres, sekrety referencyjne, kolejnosc
  startu uslug, checksums, test spojnosci i pomiar czasu.
- **Co zmieni w Ziomku:** bedzie znany realny czas i zakres utraty danych po awarii,
  zamiast zalozenia, ze sam fakt istnienia backupu wystarcza.
- **Czego nie zmieni:** produkcji; cwiczenie korzysta z kopii i nie przelacza ruchu.
- **Koniec zadania:** zapisane RTO/RPO, udany smoke odtworzonego dispatchu i lista
  recznych krokow do usuniecia w kolejnym cwiczeniu.
- **Effort:** M.

### Z-P1-11 - Triage Audytu 360

- **Na czym polega:** jeden disposition dla wszystkich 110 wpisow bez mieszania
  `CONFIRMED` z 52 hipotezami `UNVERIFIED`.
- **Zakres pracy:** wykonany w
  `eod_drafts/2026-07-10/AUDIT360_REPAIR_SPRINT_QUEUE.md`: mapowanie do kart,
  fale, collision locks, DoD i rollback.
- **Koniec zadania planistycznego:** kazdy CONFIRMED/PARTIAL/PLAUSIBLE jest w
  pakiecie wykonawczym albo verify-first; REFUTED zamkniete; UNVERIFIED ma tylko
  reprodukcje. Merge planu do master pozostaje osobnym krokiem.

### Z-P1-12 - Flow-liveness panelu, API i decyzji

- **Na czym polega:** odroznienie `proces zyje` od `przeplyw biznesowy dziala`.
- **Zakres pracy:** direct health panelu i courier API, watermark ostatniej
  decyzji/zdarzenia, cisza w peak, owner, severity, runbook i realny consumer
  alertu. Kod/prep i instalacja live sa osobnymi etapami.
- **Co zmieni w Ziomku:** cicha awaria kanalu koordynatora, API albo produkcji
  decyzji zostanie wykryta bez czekania na telefon od czlowieka.
- **Czego nie zmieni:** decyzji dispatchera ani dostepnosci uslug przed osobnym
  wdrozeniem.
- **Koniec zadania:** kontrolowany negative control czerwieni health i dociera
  zatwierdzonym kanalem; recovery wraca na zielono; false-positive ma pomiar.
- **Effort:** M; instalacja i restart wymagaja osobnego ACK.

### Z-P2-01 - Sygnaly mode layer

- **Na czym polega:** zasilenie FSM rzeczywistym infeasible rate, defer/reassign i
  poprawnym loadem liczonym wzgledem calej aktywnej floty.
- **Zakres pracy:** definicje sygnalow, wspolne okna, deduplikacja queue, replay
  przejsc S1/S2/S3 i karta false-positive/false-negative.
- **Co zmieni w Ziomku:** observer zacznie testowac caly pomysl mode layer, a nie
  tylko pozostawac w S1; polityka live nadal bedzie wylaczona.
- **Czego nie zmieni:** limitow R6/R27 i decyzji produkcyjnych przed flipem.
- **Koniec zadania:** syntetyczny test kazdego przejscia i minimum 7 dni shadow z
  wyjasnionymi przejsciami lub dowodem, ze progi sa nieosiagalne.
- **Effort:** M plus 7 dni obserwacji.

### Z-P2-02 - Schemat i rozmiar decyzji

- **Na czym polega:** rozdzielenie malego kontraktu operacyjnego od diagnostyki,
  cech ML i pelnych alternatyw.
- **Zakres pracy:** `DecisionEnvelope` z wersja, wymagane pola, rozszerzenia,
  osobny feature/debug record, budzet bajtow i migracja czytelnikow.
- **Co zmieni w Ziomku:** zapis decyzji bedzie tanszy i stabilniejszy, a dashboard
  nie bedzie przypadkiem zalezal od wewnetrznego pola heurystyki.
- **Czego nie zmieni:** tresci potrzebnej do audytu; trafi do wlasciwego strumienia.
- **Koniec zadania:** kontraktowe testy konsumentow, kompatybilny reader N/N-1 i
  uzgodniony limit rozmiaru z alarmem.
- **Effort:** XL; migracja etapowa.

### Z-P2-03 - Adapter panelu i API integracyjne

- **Na czym polega:** zamkniecie scrapowania HTML/CSRF i subprocessow za jednym
  interfejsem domenowym.
- **Zakres pracy:** `PanelGateway`, typed errors, timeouty, retry, idempotency key,
  read-back, contract tests i mozliwosc podmiany na partnerskie API.
- **Co zmieni w Ziomku:** zmiana HTML nie rozsypie wielu modulow, a zapis bedzie
  mial jeden kontrolowany wynik `confirmed/unknown/rejected`.
- **Czego nie zmieni:** samego panelu w pierwszej fazie; adapter opakuje istniejacy tor.
- **Koniec zadania:** wszystkie krytyczne call-site'y przez gateway, fault-injection
  401/419/timeout oraz brak podwojnego przypisania po retry.
- **Effort:** XL; kierunek zgodny z roadmapa API.

### Z-P2-04 - Konfiguracja miasta i tenanta

- **Na czym polega:** wydzielenie Bialegostoku z kodu do jawnego `CityConfig` i
  partycjonowanie danych per tenant.
- **Zakres pracy:** bbox, centrum, timezone, traffic, dzielnice, geocoder, OSRM,
  identyfikatory, state paths i test cross-tenant isolation.
- **Co zmieni w Ziomku:** uruchomienie drugiego miasta nie bedzie wymagalo forkowania
  silnika ani ryzyka uzycia bialostockiego centrum w innym miescie.
- **Czego nie zmieni:** obecnego deploymentu Bialegostoku przy identycznej konfiguracji.
- **Koniec zadania:** parity replay dla Bialegostoku i syntetyczny drugi tenant bez
  dostepu do jego kurierow, cache i zamowien.
- **Effort:** XL; priorytet zalezy od B-04.

### Z-P2-05 - Ewolucja plikow stanu

- **Na czym polega:** decyzja oparta na pomiarze, ktore pliki rzeczywiscie wymagaja
  transakcyjnego store zamiast kolejnych lokalnych lockow.
- **Zakres pracy:** rejestr owner/readers/writers/schema/retention, metryki wielkosci
  i churnu, wybor kandydatow oraz migracja dual-read/dual-write jednego store naraz.
- **Co zmieni w Ziomku:** krytyczne multi-writer dane dostana transakcje i indeksy,
  bez ryzykownego przepisywania wszystkich prostych plikow.
- **Czego nie zmieni:** plikow, dla ktorych atomic JSON jest wystarczajacy.
- **Koniec zadania:** zaakceptowana macierz ownership i zakonczona migracja pierwszego
  kandydata z parity oraz rollbackiem.
- **Effort:** XL; zakaz big-bang rewrite.

### Z-P2-06 - OSRM health i cache

- **Na czym polega:** odroznienie odpowiedzi OSRM od wyniku awaryjnego oraz usuniecie
  kosztownej pelnej ewikcji cache pod globalnym lockiem.
- **Zakres pracy:** source/degraded w wyniku route/table, bezposredni probe backendu,
  metryki circuit breakera, contention i ewikcji. Zmiana polityki ewikcji wymaga
  osobnego dowodu identycznego retained-key set i zachowania podczas awarii.
- **Co zmieni w Ziomku:** monitoring nie oglosi zdrowia podczas awarii, a duzy cache
  nie zamrozi watkow decyzji podczas sortowania 50 tys. wpisow.
- **Czego nie zmieni:** poprawnego fallbacku haversine i traffic multiplier.
- **Koniec zadania:** chaos test OSRM down/slow/recovery, benchmark cache przy
  limicie i oddzielenie direct upstream truth od process-local cache/CB.
- **Stan Fazy A 2026-07-10:** health/provenance/telemetria i testy awarii sa gotowe
  na izolowanej galezi. Legacy batch-10% sort pozostaje dla parytetu decyzji;
  optymalizacja spike'u nie jest zakonczona.
- **Effort:** M.

### Z-P2-07 - Hermetyczne testy i fixture

- **Na czym polega:** odciecie suity od biezacego stanu hosta i produkcyjnych sciezek.
- **Zakres pracy:** dependency injection sciezek, autouse guard zapisu, anonimowe
  fixture logow/aliasow/overrides oraz osobna kategoria testow live read-only.
- **Co zmieni w Ziomku:** nic w runtime; CI i lokalny wynik beda powtarzalne, a test
  nie zmieni przypadkiem produkcyjnej flagi lub orders state.
- **Czego nie zmieni:** mozliwosci uruchomienia jawnego smoke na zywych danych.
- **Koniec zadania:** suita przechodzi bez `/root/.openclaw/workspace/dispatch_state`,
  a guard celowo zabija probe probujacy pisac do produkcji.
- **Effort:** L; kontynuacja Z-P0-03.

### Z-P2-08 - Least privilege uslug

- **Na czym polega:** odebranie demonom niepotrzebnego dostepu roota i ujednolicenie
  hardeningu systemd.
- **Zakres pracy:** osobni uzytkownicy/grupy, ownership katalogow, `ReadWritePaths`,
  `ProtectSystem`, sekrety, network access i plan etapowych restartow.
- **Co zmieni w Ziomku:** przejecie pojedynczej uslugi da napastnikowi mniejszy zakres
  plikow, sekretow i procesow.
- **Czego nie zmieni:** API i logiki dispatchu; zmienia granice OS.
- **Koniec zadania:** `systemd-analyze security`, smoke kazdej uslugi i dowod, ze
  proces nie moze zapisac poza zadeklarowanymi katalogami.
- **Effort:** XL; wdrazac usluga po usludze.

### Z-P3-01 - Backupy i martwy kod

- **Na czym polega:** zbudowanie listy artefaktow bez konsumentow i bez wartosci
  rollbackowej, a dopiero potem kontrolowane usuniecie.
- **Zakres pracy:** import graph, grep systemd/cron/docs, wiek i rozmiar `.bak-*`,
  lista galezi oraz osobny ACK dla kazdej grupy.
- **Co zmieni w Ziomku:** mniej falszywych zrodel prawdy i szybsza nawigacja po repo.
- **Czego nie zmieni:** aktywnego kodu i historii Git.
- **Koniec zadania:** raport kandydatow, dowod braku referencji, backup/tag i czysta
  suita po usunieciu.
- **Effort:** M.

### Z-P3-02 - Konsolidacja dokumentacji

- **Na czym polega:** oznaczenie, ktore pliki sa kanonem, snapshotem albo archiwum.
- **Zakres pracy:** indeks startowy, status/data/wlasciciel, link do tego backlogu,
  przeniesienie starych planow do archive bez utraty historii.
- **Co zmieni w Ziomku:** nowa sesja nie wykona ponownie zamknietego zadania ani nie
  uzna kwietniowego opisu produkcji za aktualny.
- **Czego nie zmieni:** logiki i zachowanej historii decyzji.
- **Koniec zadania:** jeden punkt startowy, checker martwych linkow i brak dwoch
  dokumentow deklarujacych sie jako aktualny backlog.
- **Effort:** M.

### Z-P3-03 - Monitoring i observability

- **Na czym polega:** ustalenie jednej konwencji dla health, metryk, alertow i
  narzedzi diagnostycznych obecnie rozdzielonych miedzy dwa katalogi.
- **Zakres pracy:** ownership, nazwy metryk, severity, runbook URL, deduplikacja
  checkerow i plan docelowej struktury bez natychmiastowych przenosin.
- **Co zmieni w Ziomku:** alert bedzie mial wlasciciela, znaczenie i procedure,
  zamiast kilku podobnych wskaznikow o roznych definicjach.
- **Czego nie zmieni:** progow biznesowych bez osobnej akceptacji.
- **Koniec zadania:** katalog sygnalow z definicjami i zero dublujacych alertow dla
  tego samego incydentu.
- **Effort:** M.

### Z-P3-04 - Redukcja broad exceptions

- **Na czym polega:** usuwanie najbardziej niebezpiecznych `except Exception` i
  silent `pass` na podstawie krytycznosci sciezki, nie mechanicznego grep-rewrite.
- **Zakres pracy:** ranking hot path, typed errors, reason codes, fallback jawny w
  wyniku, licznik bledow i test fault-injection dla kazdego poprawionego miejsca.
- **Co zmieni w Ziomku:** awaria nie zamieni sie po cichu w `no_candidates`, pusty
  cache lub domyslny czas; operator dostanie prawdziwa przyczyne degradacji.
- **Czego nie zmieni:** celowych, zmierzonych fallbackow utrzymujacych dzialanie.
- **Koniec zadania:** zamknieta lista najwyzszego ryzyka, brak silent failure w
  wybranym hot path i metryka kazdego fallbacku.
- **Effort:** L na pierwsza fale; kolejne fale osobno.

## 6. Decyzje biznesowe i ich bramki

Najświeższe rozstrzygnięcia właściciela są w
`docs/decisions/ODR-001-owner-decisions-2026-07-12.md`. Status
`OWNER_CONFIRMED` zamyka semantykę, ale nie zastępuje raportu danych, implementacji,
replay ani ACK live.

| ID | Decyzja / status | Granica, której technika nie może zgadywać |
|---|---|---|
| B-01 | **ROZSTRZYGNIĘTE OD-07/OD-06:** R6 to possession→handoff, 35 normalnie / 40 tylko Alarm / >40 zakaz. Always-propose pokazuje feasible albo jawny `NO/ALERT`; widoczność nie daje feasible/execute. Trudny plan początkowo `recommend+approval`. | Do technicznego związania: physical events, pełny Alarm predicate/lifecycle, wszystkie bliźniaki i oracle. Bez tego enforcement/flip = HOLD. |
| B-02 | **ROZSTRZYGNIĘTE OD-04:** stored commitment niezmienny; `|Δ|<=5` normalnie, `5<|Δ|<=10` tylko jawny Alarm breach/`ALERT`, `|Δ|>10` niedopuszczalne. | Kodowy tryb 5/10 nie może przepisać commitment ani nadać execute. Ewentualna renegocjacja wymaga osobnej przyszłej decyzji. |
| B-03 | **CZĘŚCIOWO ROZSTRZYGNIĘTE OD-06:** authority jest macierzą per klasa; przerzut, Alarm-plan i least-damage startują `recommend+approval`, awansują niezależnie. | Pełna lista klas, dowód, liczby, stop-loss i kryteria awansu pozostają do kart autonomii i osobnych decyzji; żadna klasa nie została teraz awansowana. |
| B-04 | Czy celem jest drugi tenant/miasto w ciagu 12 miesiecy? | Od tego zalezy priorytet wydzielenia konfiguracji Bialegostoku. |
| B-05 | Jak dlugo wolno przechowywac dokladne adresy, GPS i world records? | Retencja i pseudonimizacja sa decyzja prawno-biznesowa. |
| B-06 | Czy kurier bez GPS moze dostac propozycje z pozycji syntetycznej? | To kompromis miedzy ciagloscia operacji a ryzykiem fikcyjnego ETA. |
| B-07 | **SEMANTYKA ROZSTRZYGNIĘTA OD-01/02/03/07:** osobne exit+possession oraz arrival+handoff; R6 possession→handoff; gate fail-closed per event/source/cohort. **LICZBY ODŁOŻONE** do raportu danych. | Exact source/event binding, coverage/missingness/cost i progi promocji pozostają `UNBOUND/HOLD`; nie odzyskiwać liczb z historycznego proxy. |
| B-08 | **ROZSTRZYGNIETE dla canary 2026-07-11:** `daily/rotate 30/maxsize 100M`; drift krytyczny=0, miss mismatch=0, miękki `pool_feasible/reason` <=1%; zero `STAGE_TIMING_SIDECAR_LOST`, p95 `service_wall_ms` <=2500 ms, p95 appendu ledgera <=5 ms, bez wzrostu `NRestarts`. | Jawne polecenie wdrozenia live jest ACK na proponowana retencje i obserwacje. Przekroczenie progu = HOLD i hot rollback flagi; nie zmienia to ETA, backpressure ani decyzji silnika. |
| B-09 | **ROZSTRZYGNIĘTE ODR-002:** tylko właściciel zwiększa execution authority. Codex może przygotować evidence/candidate/izolowaną implementację i eval/shadow/canary w granicach bieżącej karty, ale nie może zmienić karty/gate'a/evala/progu i zatwierdzić własnej promocji. | Technicznie `UNBOUND/HOLD`: schema i położenie karty, podpis/trust root, klasy/ID, evidence bundle/hash, niezależność review, safe-state per klasa i pełny runtime entry-point inventory. Każdy z tych mechanizmów jest R4-GOVERNANCE; ODR nie jest execution authority. |

## 7. Sprint 1

### Nazwa

**Sprint 1 - Fundament spojnosci decyzji i stanu**

### Cel

Usunac potwierdzone ryzyka lost-update i stale-write, przywrocic wiarygodny baseline
testow oraz zbudowac neutralny decyzyjnie pomiar koncowych naruszen R6/R27.

Sprint nie wlacza auto-assign, nie flipuje polityki R6/R27 i nie wymaga restartu
produkcyjnego przed osobnym przedstawieniem wynikow oraz ACK.

### Status wykonania

**Stan na 2026-07-10 06:10:36 UTC: wdrozony ponownie; 48-godzinna obserwacja
shadow w toku.**

- **Implementacja - wykonana 2026-07-09:** Z-P0-03, Z-P0-02, Z-P0-04 i Z-P0-01
  faza A ukonczono w izolowanym worktree. Pelna suita: **4579 passed, 0 failed,
  27 skipped, 8 xfailed, 2 xpassed**. Replay 50 WR1 wykazal **0 roznic
  krytycznych**; zastane roznice miekkie byly identyczne na niezmienionym HEAD.
- **Pierwszy deploy - wykonany po ACK 2026-07-09:** zsynchronizowano jawna liste
  36 plikow; SHA-256 zrodlo/live **36/36 zgodnych**. Restart dwoch uslug byl juz
  w toku, gdy nadeszla instrukcja wstrzymania dalszych restartow; oba procesy
  zdazyly zaladowac kod.
- **Pelny rollback - wykonany po osobnym ACK 2026-07-10:** przywrocono 30
  nadpisanych plikow, usunieto szesc nowych artefaktow Sprintu i po osobnym ACK
  zrestartowano `dispatch-shadow` oraz `dispatch-panel-watcher`.
- **Ponowny deploy - wykonany po jednoznacznym `deploy i restart ack`
  2026-07-10:** ponownie wdrozono te sama liste 36 plikow; SHA-256 **36/36
  zgodnych**, AST **11/11 OK**, `git diff --check` OK. Obie uslugi sa
  active/running bez restart-loopa, a wlasciwe timery sa active/waiting.
- Nie wykonano flipa flag ani migracji lub zmiany danych runtime. Egzekwowanie
  R6/R27 pozostaje wylaczone (`enforcement=NONE`). B-01/B-02 zostały później
  semantycznie zamknięte przez ODR-001 12.07; historyczny Sprint 1 ich nie wdrażał.
- Rollback pozostaje dostepny w `/root/sprint1_rollback_20260709_2140`.
  Szczegoly sa w `eod_drafts/2026-07-09/SPRINT1_FUNDAMENT_SPOJNOSCI_RAPORT.md`
  oraz `SPRINT1_DEPLOY_AND_ROLLBACK_REPORT.md` w tym samym katalogu.

### Zakres i effort

| Kolejnosc | Zadanie | Rezultat | Effort agenta | Status |
|---:|---|---|---:|---|
| 1 | Z-P0-03 - zielony baseline | 0 failed; nowa flaga w rejestrze i testy working override odciete od zywego state | 1-2 h | WYKONANE - LIVE |
| 2 | Z-P0-02 - geocode RMW | Staly lockfile, transakcja load-merge-save i test wieloprocesowego lost-update | 3-5 h | WYKONANE - LIVE |
| 3 | Z-P0-04 - CAS planow | Wszystkie produkcyjne zapisy przekazuja wersje; test wymuszonego konfliktu i jawna polityka retry/keep | 6-10 h | WYKONANE - LIVE |
| 4 | Z-P0-01 faza A - firewall shadow | Jedno miejsce raportujace finalne R6/R27/SLA bez zmiany werdyktu | 4-6 h | WYKONANE - LIVE SHADOW; OBSERWACJA 48 H |
| 5 | Regresja, replay i raport | Pelna suita, replay world records, porownanie decyzji i instrukcja rollbacku | 3-5 h | TECHNICZNIE WYKONANE; OBSERWACJA LIVE OTWARTA |

**Laczny effort:** 17-28 godzin agenta, czyli ok. 3-4 dni pracy technicznej.

**Obserwacja operacyjna:** okno 48 godzin rozpoczelo sie po ponownym deployu i
zaladowaniu kodu przez obie uslugi, tj. `2026-07-10 06:10:36 UTC`. Przy ciaglosci
uslug i poprawnych danych najwczesniejszy koniec okna to
`2026-07-12 06:10:36 UTC`. Do tego czasu operacyjne Definition of Done pozostaje
otwarte; nie wolno proponowac egzekwowania firewalla wylacznie na podstawie
samego deployu.

### Definition of Done Sprintu 1

- Pelna suita ma 0 nowych i 0 znanych faili.
- Test geocode odtwarza stary lost-update i przechodzi po naprawie.
- Test planow odtwarza przeplot writer A/B i dowodzi braku stale overwrite.
- Replay nie zmienia wyboru kuriera ani werdyktu przez shadow firewall.
- Kazde naruszenie ma `order_id`, rule id, wartosc, limit, tryb i powod wyjatku.
- Nie powstaje nowa flaga bez rejestru, testu ON/OFF i daty usuniecia.
- Przed restartem lub flipem agent przedstawia osobny opis zmiany produkcyjnej.

**Stan DoD na 2026-07-10:** kryteria implementacyjne, pelna regresja, test
lost-update, test CAS i neutralnosc decyzyjna replayu sa spelnione. Otwarte
pozostaje wylacznie zebranie i ocena pelnych 48 godzin danych shadow: kompletnosc
`rule_verdict.v1`, wymagane pola violations, jawne `missing_reasons`, brak wplywu
firewalla na decyzje oraz obserwacja `plan_cas_conflicts`, latency i bledow
geocode/cache.

### Poza zakresem Sprintu 1

- Egzekwowanie R6/R27.
- Auto-assign.
- Migracja JSON do bazy.
- Refaktor calego pipeline lub `DecisionContext`.
- Mode layer, multi-city i publiczne API partnerow.

## 8. Sprint 3 - Faza A prawdy ETA i obserwowalnosci

**Stan na 2026-07-11: LIVE SHADOW CANARY ON od `10:27:12 UTC`. Audyt i
TEST-TRUTH sa domkniete, logrotate jest zainstalowany, a pierwszy E2E jest
zielony. Werdykt po pelnym oknie 48 h wykona at-214 13.07 o 12:15 UTC.**

- Branch `sprint3/eta-observability-osrm`, worktree
  `/root/sprint3_wt/dispatch_v2`, base
  `c2bde5894976eea9e186336453d8bcaeec1d2489`.
- Wczorajszy commit implementacyjny `e48b21e` zostal zintegrowany przez release
  `d9a456c`; master fast-forward do `fa26c80`, privacy-fix replayu `292c9cd`.
  Flaga zostala ustawiona atomowo i wykonano jeden restart tylko shadow. Nie ma
  migracji ani zmiany danych biznesowych.
- Historyczny baseline: **4710 passed, 24 skipped, 10 xfailed**; czysty aktualny
  master: **4762 passed, 27 skipped, 10 xfailed**. Finalna regresja:
  **4851 passed, 27 skipped, 10 xfailed** (+89 testow, bez nowego fail/skip/
  xfail). Paired oracle jest w `769dbfa`; komplet commitow przygotowania jest
  zapisany w raporcie Sprintu 3.
- `ENABLE_STAGE_TIMING_OBSERVATION=true` jest LIVE SHADOW; fallback pozostaje
  OFF. Logrotate `daily/rotate 30/maxsize 100M` jest zainstalowany, sidecar 0600.
  Pierwszy tick: 1/1 valid, missing/orphan/duplicate/incomplete = 0,
  `service_wall_ms=2121,608`, append ledgera `0,644 ms`.
- Zwykla podmiana live flags nie testuje historycznego replayu, bo world record
  odtwarza wlasny snapshot. Wersjonowany paired replay jawnie wstrzyknal flage:
  **808 porownan, 0 roznic krytycznych, 4 miekkie
  `pool_feasible+reason`, 0 miss mismatch**. Byte-parity calego payloadu nie
  ogloszono; canary pozostaje bramka.
- Read-only ETA replay ma historyczny bazowy mianownik 188, package coverage
  94,330% i complete-case obu nog 41,489%. OD-01/02/07 rozstrzygnęły semantykę,
  ale exact physical event/source contracts i liczbowe bramy OD-03 nadal są
  `UNBOUND/HOLD`; replay nie może promować KPI na starym proxy.
- Direct OSRM probe potwierdzil sukces route/table/nearest. Stan cache/CB CLI
  jest jawnie `process_local`; polityka ewikcji pozostala legacy dla parytetu.
- Z-P1-02 Faza A, Z-P1-03 Faza A oraz health/telemetria Z-P2-06 przeszly review.
  Sesja 54 i audyt sa zakonczone; ACK live oraz B-08 sa rozstrzygniete. Otwarta
  pozostaje optymalizacja eviction i co najmniej 48 h canary observability.
- Kompletny raport i rollback:
  `eod_drafts/2026-07-10/SPRINT3_PHASE_A_REPORT.md`.

## 9. Dalsza proponowana kolejnosc

1. Sprint 1: wdrozony; Z-P0-01 faza A pozostaje w obserwacji shadow do co najmniej
   `2026-07-12 06:10:36 UTC`.
2. Nastepny P0: `A360-SEC0` granica hosta/credential, potem wspolny
   `A360-E0` dla Z-P0-05+Z-P1-01. Z-P0-06 ownership jest juz DONE/LIVE;
   pre-login UX pozostaje osobna decyzja.
3. Sprint 3: Faza A **LIVE SHADOW CANARY ON**; obserwacja od 11.07 10:27 UTC,
   at-214 po oknie 48 h; ETA nadal offline/unbound, eviction nadal otwarta.
4. Sprint 4: Z-P1-05, Z-P1-07, Z-P2-07 — **WYKONANY 2026-07-10** (wszystkie 3 karty DONE + follow-upy za ACK: kuracja rejestru, fix panel_packs, migracja+flip USE_V2_PARSER, identity Faza B; handoffy: `eod_drafts/2026-07-10/SPRINT4_HANDOFF.md` + `SPRINT4_SESJA_FULL_CLOSE.md`).
5. Rownolegle branch-only po odczycie kolizji at-214: `A360-DATA0` dla
   Z-P1-06; zadnego delete przed B-05.
6. Sprint 5: Z-P1-04 i Z-P2-02 po ustabilizowaniu kontraktow.
7. Dalej: integracje, multi-city i migracje stanu wedlug decyzji B-03/B-04.

## 10. Historia zamknięta

| ID | Zakres | Wynik | Dowód / rollback | Status |
|---|---|---|---|---|
| Z-ACC-01 | Historyczne rozliczenia dzienne Google Sheets | Filtr rozliczeń wyklucza tylko trwałe konta techniczne, nazwa rozliczeniowa pochodzi z canonical `identity`, a wpis ma trwały klucz `CID+okres` w kolumnie S. Legacy duplicate jest porównywany po H/P: zgodny jest pomijany, rozbieżny zatrzymuje zapis albo wymaga jawnego trybu korekty. | `c10e1eb` + `c244d0b`; finalnie 5188 testów, 0 fail/error, 32 skip; E2E korekty 2 wierszy H/P/S potwierdzone niezależnym odczytem panel→arkusz. Rollback kodu: tag `daily-accounting-pre-c10e1eb`; danych: historia wersji arkusza sprzed 2026-07-13 10:13 UTC. | **DONE/LIVE 2026-07-13** |

## 11. Zasady utrzymania backlogu

- Statusy: `PROPOSED`, `READY`, `IN_PROGRESS`, `BLOCKED`, `DONE`, `REJECTED`.
- Zadanie przechodzi do `READY` dopiero po ponownej weryfikacji dowodu w kodzie.
- `DONE` wymaga commitu, testow, dowodu produkcyjnego lub replay oraz rollbacku.
- Zamkniete zadanie zostaje w tym pliku w skroconej tabeli historii; nie usuwamy
  decyzji i przyczyn.
- Nowe zadanie nie wyprzedza P0 bez jawnej decyzji biznesowej.
- Ten plik opisuje kolejke. Szczegolowe raporty i dane pozostaja w dedykowanych
  dokumentach, aby backlog nie stal sie kolejnym wielkim archiwum sesji.
