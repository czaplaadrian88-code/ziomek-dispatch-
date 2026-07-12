# Audit360 — kolejne trzy bezkolizyjne sprinty po SEC0 / E0 / DATA0

Status: **PREPARED, NOT STARTED**. Data: 2026-07-12 UTC. Ta karta nie jest
zgodą na operacje live. Jeden integrator pozostaje FLIPMASTEREM.

## Dlaczego te trzy są teraz najwyżej

- `at-214` jest jedynym zaplanowanym werdyktem i ma termin 2026-07-13
  12:15 UTC. Bez jego uczciwego odczytu nie wolno integrować R0/D1 ani
  przechodzić do H1.
- SEC0 potwierdził realną, a nie hipotetyczną ekspozycję hosta. Source audytora
  jest gotowy, lecz sama ochrona sieci i credentialu nie została wykonana.
- E0 udowodnił, że prosty retry/FSM nie daje exactly-once całemu przepływowi.
  Następny krok musi usunąć przyczynę: brak wspólnej koperty, outboxa i receipts
  per consumer.

Write-sety są rozłączne: V214 zapisuje wyłącznie evidence/docs, SEC1 dotyka
hosta/courier API/manifestu, E1 dotyka event/FSM/state. Ciężkie pełne testy E1
muszą być poza oknem odczytu V214 i zapisane jako host-load. Operacje live SEC1
nie mogą biec równolegle z V214 ani z innym deployem/restartem.

## 1. A360-V214 CANARY-DISPOSITION — priorytet P0, effort `high`

### Problem i dowód

Job 214 jest zaplanowany na 2026-07-13 12:15 UTC. Ma rozliczyć 48-godzinny
canary wraz z jawnie zarejestrowanymi przedziałami obciążenia pełnymi testami.
R0, D1 i późniejszy H1 pozostają HOLD, dopóki wynik nie ma sprawdzonego
oracla, denominatora, freshness i sensitivity z/bez tych przedziałów.

### Zakres

- najpierw odczyt najnowszego istniejącego outputu joba/monitora, bez budowania
  równoległego źródła prawdy;
- read-only corpus i zredagowane agregaty; bez surowych order/courier/GPS;
- walidacja denominatora, coverage, freshness, host-load sensitivity,
  known-answer i mutation tripwire;
- raport disposition `GO`, `HOLD` albo `NO-GO` dla R0/D1 oraz lista braków;
- aktualizacja rejestru jobów, backlogu i handoffu przez integratora.

### Co zmieni po zakończeniu

Sam sprint nie zmieni zachowania produkcyjnego. Zmieni jakość decyzji o
wydaniu: albo odblokuje kontrolowaną integrację R0/D1 i przygotowanie H1, albo
zatrzyma ją z konkretnym dowodem zamiast intuicji.

### Ryzyko, testy i rollback

Ryzykiem jest fałszywy zielony wynik po zmieszaniu canary z host-load albo
zmniejszeniu mianownika. Testem jest powtórzenie na tym samym frozen corpusie,
sensitivity z/bez zarejestrowanych przedziałów i mutation, która musi zmienić
werdykt. Rollback dokumentu to jawny revert; brak flagi, migracji, restartu i
deployu. Read-only odczyt nie wymaga biznesowego ACK, lecz każdy późniejszy
merge/flip/HARD wymaga własnej bramki.

## 2. A360-SEC1 HOST-REMEDIATION — priorytet P0, effort `max`

### Problem i dowód

Audyt SEC0 zwraca `HOLD`: 8767 jest publiczne na IPv4, 9222 na IPv4 i IPv6,
nie ma udowodnionej skutecznej reguły INPUT/DOCKER-USER, a provider firewall
jest `UNKNOWN`. Otwartą pozycją pozostaje również rotacja poświadczenia.

### Zakres

- potwierdzony source manifest i immutable image dla browsera;
- patch bindu courier API i publikacji kontenera do loopback/approved path;
- provider firewall oraz host deny dla IPv4 i IPv6 bez utraty bezpiecznej
  ścieżki administracyjnej;
- nowa rewizja poświadczenia, skoordynowanie wszystkich konsumentów i usunięcie
  fallbacku do starego carriera;
- backup kodu, unitów, manifestu i reguł; metadane carriera bez odczytu treści;
- druga działająca sesja administracyjna, health i allowed/denied z niezależnej
  sieci, rollback drill bez przywracania starej wartości.

### Co zmieni po zakończeniu

Direct public access do 8767/9222 przestanie być dostępną drogą. Działać ma
wyłącznie zatwierdzona ścieżka proxy/tunelu, a stare poświadczenie będzie
unieważnione. Nie zmieni to scoringu, planu, HARD/SOFT ani wyboru kuriera.

### Ryzyko, testy i rollback

Ryzyka to lockout administratora, przerwa API, utrata CDP i niespójna rotacja.
Dlatego sprint ma twardy **BLOCKED BEFORE START** do jawnego ACK na provider,
firewall, rotację, recreate/deploy i kontrolowane restarty. Wymaga maintenance
window, drugiej sesji i ownera manifestu. Testy: source/targeted przed zmianą,
allowed+denied IPv4/IPv6 po każdej warstwie, proxy/API/CDP health, PID,
NRestarts i ponowny audyt. Rollback utrzymuje deny i loopback, wraca do
kompatybilnego kodu/immutable image, ale credential zawsze przechodzi na
kolejną nową rewizję — nigdy na ujawnioną starą wartość.

## 3. A360-E1 DURABLE EVENT OUTBOX — priorytet P0, effort `max`

### Problem i dowód

E0 ma zielone testy źródłowe, ale jego review wykazał, że globalny state
receipt nie potwierdza efektów per consumer. Audit-only event może nie dostać
DLQ, coordinator activation może zajść przed receipt, dwa wykonawcy
PICKED/DELIVERED mogą pominąć follow-up, a produkcyjne envelope nie ma wspólnego
`event_id` i `created_at`. Włączenie obecnego E0 mogłoby więc nadal zgubić albo
powtórzyć pracę.

### Zakres

- jedna kanoniczna koperta DB+state: jawne `event_id`, `created_at`, source i
  policy/version bez fallbacku `now()`;
- jeden owner redukcji stanu oraz durable failure journal dla audit-only;
- transakcyjny outbox dla efektów zewnętrznych i receipts per consumer dla
  planu, SLA i pozostałych follow-upów;
- trwały kontrakt idempotency/retention z ograniczeniem wzrostu receipts bez
  utraty deduplikacji;
- ujednolicenie panel/parcel/reconciliation/SLA/rebuild/replay oraz jawna
  migracja dry-run; żadnego workera ON ani live apply w tym sprincie;
- testy crash-window przed/po każdym zapisie i efekcie, race dwóch konsumentów,
  stale ordering, retry po restarcie, retention, mutation i pełna regresja.

### Co zmieni po zakończeniu

Na branchu przygotowawczym zachowanie live pozostanie bez zmian. Po osobnym
merge'u, migracji i aktywacji za ACK Ziomek będzie mógł wznowić błąd
przejściowy bez powtarzania nielegalnego przejścia, zachować odrzucony event w
trwałej kwarantannie i nie zgubić planu/SLA między zapisem stanu a crashem.

### Ryzyko, testy i rollback

To zmiana granicy transakcyjnej wielu call-site'ów. Największe ryzyka to
podwójny efekt, utrata follow-upu, nieograniczony wzrost tabel oraz zła
kolejność czasu. Praca startuje branch-only z default OFF, po świeżym rebase na
aktualny master i bez merge'u przed disposition V214. Schemat jest wyłącznie
addytywny, idempotentny, transakcyjny i ma dry-run. Rollback źródła to jawny
revert przy OFF; nie usuwa się tabel ani DLQ/outbox evidence. Flaga, migracja
runtime, worker, deploy i restart wymagają później osobnego ACK. Zmiana nie
dotyka relacji HARD/SOFT.

## Kolejność wykonania

1. E1 może rozpocząć wyłącznie source/branch work od razu; pełne testy poza
   oknem pomiarowym i z rejestracją host-load.
2. V214 wykonuje się dopiero po pojawieniu się zaplanowanego outputu i jako
   pierwszy odczyt w swoim oknie. Nie uruchamia się wtedy ciężkich testów.
3. SEC1 czeka na jawny ACK i zatwierdzone maintenance window; jego operacje
   live zaczynają się dopiero po zabezpieczeniu wyniku V214.
4. Wspólne backlogi, memory, merge, tagi i wydanie prowadzi jeden integrator.
