# ZIOMEK AI DISPATCHER - BACKLOG ROZWOJU

> Status: AKTYWNY - SPRINT 1 WDROZONY, OBSERWACJA SHADOW W TOKU
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
| Z-P0-01 | Kanon R6/R27/SLA i koncowy invariant firewall | Audyt 360: A360-FEAS-01 jest jedynym utrzymanym P1; A360-FEAS-02..05 ujawniaja nieustalona semantyke 35 HARD vs 40 ALARM/least-damage, `None`, per-order/suma i READY/in-bag. SPRI-04 zawyza telemetrie przez brak EXEMPT. | Najpierw D0 decision-prep, replay truth i D1 EXEMPT/VIOLATION; dopiero po ACK jedna spojna granica HARD oraz jawny ALERT always-propose. | L + 2 dni obserwacji | Decyzje B-01/B-02; bez flipa przed ACK | IN_PROGRESS - SHADOW + A360-D0/D1 PENDING |
| Z-P0-02 | Naprawa wieloprocesowego zapisu geocode cache | `flock` jest zakladany na unikalnym tempfile, wiec nie serializuje load-merge-save miedzy procesami. | Cache adresow, restauracji i negative cache przestanie gubic poprawne wpisy przy rownoleglym geokodowaniu. | M | Bez flipa; pelna regresja geocode | DONE - LIVE |
| Z-P0-03 | Przywrocenie zielonego baseline testow | REOPENED 10.07 po flipie parsera i Audycie 360: default 4846/1; STRICT 4792/6. TEST-11 czyta live `flags.json` i oczekuje historycznego `known-open`; TEST-12 to 5 script-tests z live reads poza aktualna kwarantanna. | Baseline bedzie deterministyczny: syntetyczne flags+systemd, naprawione testy mechanizmu, wydzielone jawne live-smoke nodeidy i STRICT 0 failed z lista skipow. | M | Zero zmian produkcyjnych; nie oslabiac HERMETIC-GUARD | REOPENED - TEST-11/12 |
| Z-P0-04 | CAS i wspolna granica planu — REOPENED 10.07 | Dispatcherowe call-site'y CAS sa LIVE, ale Audyt 360 potwierdzil pominiety writer panelu (SPRI-02/DANE-01), odrzucanie strategii solvera, rozjazd stops i null-duration=teleport (TRAS-01/02/03) oraz false-conflict touch_plan (SPRI-03). | Jeden cross-repo owner domknie decyzja→store→panel→apka: prawidlowa kolejnosc, fail-closed czas nogi, provenance/manual marker i CAS bez lost-update/resurrect. | L/XL | Po A360-H1; jeden lane PLAN; deploy readers-first/writer-second i restart za ACK | REOPENED - A360-P0 QUEUED |
| Z-P0-05 | Retry/DLQ dla eventow failed | Historycznie 106 `NEW_ORDER` ma status failed; brak attempt count, error i automatycznego retry. | Blad przejsciowy nie zgubi obslugi zlecenia; poison event trafi do DLQ z diagnoza i limitem prob. | L | Decyzja o retry policy | PROPOSED |
| Z-P0-06 | Bezpieczenstwo courier API — auth + ownership | Faza A rate-limit per-IP jest LIVE, lecz Audyt 360 potwierdzil brak ownership-guarda dla status/arrival/ground-truth/parcel (BEZP-02) i pozostawil decyzje UX katalogu pre-login (BEZP-04). | Wspolny guard order→CID zablokuje mutacje cudzej encji bez regresji wlasciciela; katalog ujawni tylko zakres zatwierdzony biznesowo. | M | Osobny worktree courier_api; deploy/restart API i UX za ACK | PHASE A LIVE; A360-S0 QUEUED |

### P1 - stabilnosc przed autonomia

| ID | Zadanie | Dowod / problem | Co zmieni sie po wykonaniu | Effort | Bramka |
|---|---|---|---|---:|---|
| Z-P1-01 | Formalny FSM zlecen | `state_machine` zna statusy, ale nie ma jednej mapy dozwolonych przejsc; zly pickup timestamp jest zastepowany `now()`. | Nielegalne przejscie i uszkodzony czas beda kwarantannowane zamiast po cichu zmieniac prawde SLA. | L | Kompatybilnosc replay historycznego |
| Z-P1-02 | Kanoniczny ground truth ETA i SLA | Raport GPS porownuje licznik dostaw i physical truth z roznych okien; klik panelu nie jest fizycznym przyjazdem. | Promocja ETA bedzie oparta na tym samym oknie, kohorcie i zdarzeniu fizycznym dla pickup/delivery. | L | Definicja KPI biznesowych |
| Z-P1-03 | Stage-level tracing i backpressure | Latencja decyzji: p95 ok. 2,02 s, max 7,19 s; rekord nie rozbija czasu na etapy. | Bedzie wiadomo, czy opoznia panel, flota, OSRM, solver, selekcja czy zapis; pojawia sie limit kolejki i budzet etapu. | M | Najpierw pomiar, potem optymalizacja |
| Z-P1-04 | Jawny `DecisionContext` i wiarygodny replay | Effects buffer obejmuje tylko czesc zapisow; Audyt 360 dodatkowo wykazal PARTIAL CORE-01, process-local rozjazdy CORE-02/03 i niekonsumowany gate TEST-03. | Pierwszy inkrement tool-only rozdzieli input-miss/OSRM-miss/soft-diff i dostanie known-answer+mutation; pozniej context usunie ukryte kanaly procesu. | XL | Po Z-P0-03 i disposition Sprintu 3; oracle osobno od silnika |
| Z-P1-05 | Kanoniczna tozsamosc kuriera — **DONE Faza A+B 2026-07-10** (pakiet `identity/`, walidator kolizji, onboarding 5-plikowy, backfill names 19→0, kanon pisowni z grafiku; delegacja 9× norm + scoring worker/panel_roster do registry — parity 177/177, golden 21417 par = 0 roznic; ODLOZONE: unifikacja profili ×10/×5 vs ×10/×10 [pomiar+ACK], Krok 4 czytelnicy plikow→registry, konsolidacja courier_api.db) | 121 aliasow mapuje sie do 65 CID; 54 CID maja wiele aliasow, 20 nie ma wpisu w `courier_names`. | Grafik, GPS, PIN, tier, plan i rozliczenia beda laczone przez CID z kontrolowanymi aliasami. | L | Migracja bez zmiany CID |
| Z-P1-06 | Prywatnosc i retencja world records/logow | Rekordy zawieraja adresy, nazwiska i GPS, maja `0644` i rosna o setki MB dziennie. | Dane beda pseudonimizowane lub szyfrowane, `0600`, kompresowane i usuwane wedlug retencji. | M | Decyzja B-05 |
| Z-P1-07 | Rejestr i cykl zycia flag — **FUNDAMENT DONE; FOLLOW-UP A360-FLAG-01/04** | Rejestr 504/504 i checker sa gotowe, ale carry-chain jest kluczem-wabikiem, a czesc flag behawioralnych nadal zyje poza JSON. | Najpierw decyzja retire-vs-unify; pozostawiona flaga dostanie realny consumer, ON!=OFF, fingerprint i nadal pozostanie OFF do osobnego ACK. | M | Po Z-P0-03 i disposition Sprintu 3; bez laczenia z flipem |
| Z-P1-08 | Reprodukowalne srodowisko zaleznosci | `requirements-dispatch-venv.txt` pinuje rdzen OR-Tools, ale nie pokrywa calego zestawu test/ML/API; API ma niepinowane wymagania. | Odtworzenie hosta i CI da te same wersje; aktualizacje beda wykonywane partiami z replayem. | M | Bez upgrade'u w tym samym kroku |
| Z-P1-09 | Jedna polityka czasu i testy DST | W kodzie pozostaja rozne zalozenia dla naive datetime; `sla_tracker` dokumentuje uspiony naive-Warsaw-as-UTC bug. | Wszystkie granice beda przyjmowac jawny typ czasu; testy pokryja DST, polnoc i rollover dnia. | L | Bez zmiany historycznych danych |
| Z-P1-10 | Restore game day i RTO/RPO | System jest jednowezlowy, a istnienie skryptow backupu nie dowodzi skutecznego odtworzenia. | Powstanie zmierzony, powtarzalny restore stanu, eventow i baz wraz z RTO/RPO i lista brakow. | M | Odczytowo na kopii |
| Z-P1-11 | Triage i disposition Audytu 360 | Pakiet ma 110 wpisow: 49 CONFIRMED, 4 REFUTED, 4 PARTIAL, 1 PLAUSIBLE, 52 UNVERIFIED; severity 1 P1/47 P2/58 P3/4 NONE. | Potwierdzone naprawy sa zgrupowane bez duplikatow, PARTIAL/PLAUSIBLE maja verify-first, UNVERIFIED tylko reprodukcje. | M | Decyzje HARD/SOFT, security i ops osobno | QUEUE WRITTEN ON AUDIT BRANCH; NOT MERGED |
| Z-P1-12 | Flow-liveness panelu, API i decyzji | OPS-02: krytyczne uslugi moga restartowac sie, lecz nie maja bezposredniego, zweryfikowanego alert route; sam PID nie wykrywa ciszy przeplywu. | Health panel/API i brak decyzji w peak beda mialy watermark, prog, ownera, consumer i kontrolowany negative control. | M | Kod/prep bez live; instalacja/restart za osobnym ACK |

### P2 - skalowanie i granice produktu

| ID | Zadanie | Dowod / problem | Co zmieni sie po wykonaniu | Effort | Bramka |
|---|---|---|---|---:|---|
| Z-P2-01 | Naprawa sygnalow mode layer S2/S3 | Observer nie dostarcza `s2_infeasible_rate`, ustawia defer count na 99; 1188/1188 obserwacji to S1. | Shadow rzeczywiscie sprawdzi trzy tryby i ich przejscia, bez aktywacji polityki. | M + 7 dni danych | Flip dopiero po osobnym ACK |
| Z-P2-02 | Wersjonowanie i odchudzenie schematu decyzji | `best` ma do 296 pol; rekord medianowo ok. 60 KB, max 2,27 MB. | Konsumenci dostana wersjonowany kontrakt; log operacyjny i korpus ML zostana rozdzielone. | XL | Migracja czytelnikow |
| Z-P2-03 | Stabilny adapter panelu / API integracyjne | Krytyczny odczyt i zapis korzysta z prywatnego HTML, regexow, CSRF i subprocessu. | Awaria lub zmiana panelu bedzie izolowana w adapterze z idempotency key, read-back i typed error. | XL | Kierunek partner API |
| Z-P2-04 | Konfiguracja miasta i tenanta | BBox, centrum, dzielnice, traffic i domyslne miasto sa bialostockie w wielu modulach. | Nowe miasto nie bedzie wymagalo kopiowania silnika ani ryzyka cross-city geocode. | XL | Decyzja B-04 |
| Z-P2-05 | Ewolucja plikow stanu do repozytorium danych | Krytyczny stan jest rozproszony po wielu JSON/JSONL; czesc plikow jest multi-writer. | Najpierw rejestr ownership/schema, potem selektywna migracja tylko plikow z realnym problemem skali lub transakcji. | XL | Bez big-bang rewrite |
| Z-P2-06 | Wiarygodny OSRM health i polityka cache | Health uznaje wynik fallback za zdrowy OSRM; eviction sortuje duzy cache pod globalnym lockiem. | Monitoring odrozni OSRM od fallbacku, a cache nie wywola naglego piku latencji. | M | Test awarii OSRM |
| Z-P2-07 | Hermetyczne testy i fixture danych — **FUNDAMENT DONE, DOMKNIECIE REOPENED 2026-07-10** (root `conftest.py`: sandbox, write/delete guard, subprocess guard i zewnetrzna kwarantanna pozostaja poprawne; pozniejszy STRICT ujawnil TEST-11/12: live `flags.json` + 5 script-tests czytajacych stan kurierow poza kwarantanna) | Guard chroni produkcje, ale read isolation i lista intencjonalnych live-smoke nie sa kompletne; poprzedni dowod STRICT=0 jest stale. | Testy mechanizmu dostana syntetyczne fixture, a ewentualne live smoke osobne nodeidy z konkretnym powodem; STRICT znow bedzie 0 failed bez oslabenia guarda. | M | Powiazane z Z-P0-03; kwarantanna tylko po nodeid+powod |
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
Kolejka jest zapisana na branchu `audit/ziomek-360-20260710`; do czasu osobnego
merge nie zmienia starszych statusow na `master`.

Pierwsza fala po bramce wlascicieli G0:

1. `A360-T0 TEST-TRUTH` (`Z-P0-03` + `Z-P2-07`) - pierwszy merge techniczny;
2. rownolegle `A360-S0 API-OWNERSHIP` (`Z-P0-06`) w osobnym repo/worktree;
3. rownolegle docs-only `A360-D0 R6-DECISION` (`Z-P0-01`), bez zmiany HARD;
4. pilne osobne okno `A360-I0 CREDENTIAL` (`INFRA-P0-01`) po jawnym ACK.

Nastepnie: replay oracle (`Z-P1-04`) + poprawa telemetrii EXEMPT/VIOLATION,
potem za decyzja i ACK najpierw jedyny P1 `A360-H1 R6-HARD`, dopiero jeden
cross-repo plan/CAS lane (`Z-P0-04`), best-effort i flag-carrier. Flow-liveness
(`Z-P1-12`), restore (`Z-P1-10`) i SBOM (`Z-P1-08`) maja rozdzielone tory,
ale operacje live osobne. Plan, HARD i flag-carrier nie biegna rownolegle.

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
  Faza B, po decyzji B-01/B-02, moze zablokowac lub eskalowac niedozwolony plan.
- **Czego nie zmieni w fazie A:** wyboru kuriera, kolejnosci trasy i werdyktu.
- **Koniec zadania:** komplet testow wyjatkow, replay bez roznic decyzyjnych oraz
  48 godzin danych shadow bez brakujacego lub sprzecznego werdyktu.
- **Effort:** L plus 2 dni obserwacji; wymaga B-01 i B-02 przed egzekwowaniem.

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

- **Na czym polega:** po pierwotnym domknieciu zadanie zostalo ponownie otwarte
  przez post-flip TEST-11 i piec live-read script-tests TEST-12.
- **Zakres pracy:** zsyntetyzowac `flags.json` i `SYSTEMD_DIR`, usunac historyczny
  kontrakt `USE_V2_PARSER known-open` z kodu/testu/lifecycle; piec script-tests
  rozdzielic na hermetyczne fixture i jawne live-smoke nodeidy (jesli nadal potrzebne).
- **Co zmieni w Ziomku:** nic w runtime; zmieni wiarygodnosc CI i lokalnej regresji.
- **Czego nie zmieni:** wartosci flag produkcyjnych ani manual overrides floty.
- **Koniec zadania:** default i `HERMETIC_STRICT=1` maja 0 failed, lista skipow jest
  jawna, a post-migration oracle klasyfikuje pozostaly env carrier parsera jako
  `json-overrides-env/open` bez czytania live plikow przez test mechanizmu.
- **Effort:** M; zero zmian runtime i zero oslabenia HERMETIC-GUARD.

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

- **Na czym polega:** zbudowanie jednej tabeli faktow laczacej decyzje, plan,
  fizyczny GPS arrival, klik statusu i koncowy wynik zlecenia.
- **Zakres pracy:** wspolne okna czasowe, definicje pickup/delivery, kohorty,
  coverage, wersja modelu i rozdzielenie click truth od physical truth.
- **Co zmieni w Ziomku:** modele i progi beda promowane na podstawie realnego
  dojazdu, a raport nie polaczy licznikow z roznych okresow.
- **Czego nie zmieni:** live ETA przed zatwierdzeniem nowej bramki promocji.
- **Koniec zadania:** reprodukowalny raport z jednym mianownikiem, lineage danych,
  MAE/bias/coverage per noga i test braku leakage.
- **Effort:** L; wymaga biznesowej definicji KPI.

### Z-P1-03 - Stage-level tracing i backpressure

- **Na czym polega:** zmierzenie czasu i kolejek kazdego etapu jednej decyzji.
- **Zakres pracy:** span dla pre-recheck, fleet, fan-out kandydatow, OSRM, solvera,
  selection, serializacji i efektow; histogramy oraz limit backlogu.
- **Co zmieni w Ziomku:** alarm wskaze konkretny etap, a naplyw zlecen nie stworzy
  nieograniczonej kolejki i coraz bardziej spoznionych decyzji.
- **Czego nie zmieni:** heurystyk ani solvera w pierwszej fazie pomiarowej.
- **Koniec zadania:** suma spanow zgadza sie z latency, dashboard pokazuje p50/p95/max
  per etap, a test przeciazenia potwierdza jawna polityke degradacji.
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
  metryka circuit breakera i przyrostowa albo probabilistyczna ewikcja.
- **Co zmieni w Ziomku:** monitoring nie oglosi zdrowia podczas awarii, a duzy cache
  nie zamrozi watkow decyzji podczas sortowania 50 tys. wpisow.
- **Czego nie zmieni:** poprawnego fallbacku haversine i traffic multiplier.
- **Koniec zadania:** chaos test OSRM down/slow/recovery i benchmark cache przy limicie.
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

## 6. Decyzje biznesowe blokujace prace

| ID | Decyzja potrzebna od Adriana | Dlaczego technika nie powinna zgadywac |
|---|---|---|
| B-01 | Czy R6 35 min jest bezwzglednym zakazem propozycji, czy ostrzezeniem dla czlowieka? | Obecne `ALWAYS_PROPOSE` przeczy opisowi HARD, ale moze realizowac swiadoma polityke operacyjna. |
| B-02 | Czy committed pickup ma limit 5 min zawsze, czy 10 min przy przeciazeniu? | Solver ma tryb loose 10 min, a selekcja nazywa >5 naruszeniem. |
| B-03 | Jakie warunki musza byc spelnione przed wlaczeniem auto-assign? | Potrzebny akceptowalny poziom ryzyka, kompensacja i wlasciciel incydentu. |
| B-04 | Czy celem jest drugi tenant/miasto w ciagu 12 miesiecy? | Od tego zalezy priorytet wydzielenia konfiguracji Bialegostoku. |
| B-05 | Jak dlugo wolno przechowywac dokladne adresy, GPS i world records? | Retencja i pseudonimizacja sa decyzja prawno-biznesowa. |
| B-06 | Czy kurier bez GPS moze dostac propozycje z pozycji syntetycznej? | To kompromis miedzy ciagloscia operacji a ryzykiem fikcyjnego ETA. |

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
  R6/R27 pozostaje wylaczone (`enforcement=NONE`); B-01/B-02 nadal sa otwarte.
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

## 8. Dalsza proponowana kolejnosc

1. Sprint 1: wdrozony; Z-P0-01 faza A pozostaje w obserwacji shadow do co najmniej
   `2026-07-12 06:10:36 UTC`.
2. Sprint 2: Z-P0-05, Z-P0-06, Z-P1-01.
3. Sprint 3: Z-P1-02, Z-P1-03, Z-P2-06.
4. Sprint 4: Z-P1-05, Z-P1-07, Z-P2-07 — **WYKONANY 2026-07-10** (wszystkie 3 karty DONE + follow-upy za ACK: kuracja rejestru, fix panel_packs, migracja+flip USE_V2_PARSER, identity Faza B; handoffy: `eod_drafts/2026-07-10/SPRINT4_HANDOFF.md` + `SPRINT4_SESJA_FULL_CLOSE.md`).
5. Sprint 5: Z-P1-04 i Z-P2-02 po ustabilizowaniu kontraktow.
6. Dalej: integracje, multi-city i migracje stanu wedlug decyzji B-03/B-04.

## 9. Zasady utrzymania backlogu

- Statusy: `PROPOSED`, `READY`, `IN_PROGRESS`, `BLOCKED`, `DONE`, `REJECTED`.
- Zadanie przechodzi do `READY` dopiero po ponownej weryfikacji dowodu w kodzie.
- `DONE` wymaga commitu, testow, dowodu produkcyjnego lub replay oraz rollbacku.
- Zamkniete zadanie zostaje w tym pliku w skroconej tabeli historii; nie usuwamy
  decyzji i przyczyn.
- Nowe zadanie nie wyprzedza P0 bez jawnej decyzji biznesowej.
- Ten plik opisuje kolejke. Szczegolowe raporty i dane pozostaja w dedykowanych
  dokumentach, aby backlog nie stal sie kolejnym wielkim archiwum sesji.
