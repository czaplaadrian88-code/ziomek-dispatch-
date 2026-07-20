# C3-01 — crash-consistent event → state → downstream

Data: 2026-07-19 UTC
Status: kandydat kodowy, bez merge/push/deploy/restartu/mutacji live
Branch: `fix/audit-c3-durable-apply`
Worktree: `/root/wt-audit-c3-pkgroot/dispatch_v2`
Base: `486bac4682e8a5e7e696ba7efc4d5acbd08d62bc`
Companion repo: `/root/wt-audit-c3-courier-api`, branch
`fix/audit-c3-inbox-lock`, base
`fa249e678aa3e15641e6440b10a972df830010f5`
Model tier: `sol`
Effort: `xhigh` — P1 na współbieżnej granicy dwóch trwałych writerów; błąd
może pozostawić brak zlecenia lub phantom wpływający na feasibility.
Dokładny wariant dwóch procesów review v7: `gpt-5.6-sol`, `xhigh`,
potwierdzony przez nagłówek CLI każdego świeżego procesu. Główna sesja
zadeklarowała tier `sol` i effort `xhigh`; nie przypisuje sobie dokładniejszego
wariantu bez osobnej atestacji runtime.

## Addendum promocji 2026-07-20

Kandydat v7 został odtworzony jako trzy commity na bazie master
`c7bc8cd61778fab11009de66ce178118cadf689e`. Dwa pierwsze commity weszły bez
konfliktów. Trzeci miał konflikty wyłącznie w `panel_watcher.py` i
`plan_manager.py`; zachowano durable chokepoint C3, klasyfikator ręcznej zmiany
CK, flagowany `REASSIGN-RELEASE` w strict callbacku oraz masterowy no-op
`remove_stops` bez pustego bumpu wersji. Testy integracyjne zostały przepisane
z oczekiwania starego bezpośredniego helpera na realny durable downstream.

Skupione wyniki po integracji: `116 passed` (C3 + reassign-release),
`39 passed, 1 skipped` (MP-11), `147 passed, 1 xfailed` plus cztery
script-goldeny `18/18`, `8/8`, `23/23`, `11/11`; ochrona mastera: `15 passed`
(A8-2, CK-manual, sentinel, R04) oraz `40 passed` (rt-shadow).

Pełna regresja kanonicznym venv nie mogła zostać wykonana w tej sesji: granica
sandboxa i jawny zakaz zadania blokują `/root/.openclaw/**`; próba uruchomienia
interpretera zakończyła się `Permission denied` (exit 126). Systemowy mirror
z zależnościami w `/tmp` nie jest oracle baseline `5385/0` i został przerwany
po 84% (exit 130), ponieważ testy środowiskowe/guardowe nie zachowują parytetu
kanonicznego runtime. Z tego powodu addendum nie zastępuje wymaganej pełnej
regresji i nie ogłasza finalnego merge/deploy.

## Problem i root cause

Audyt runtime z sesji tmux 215 potwierdził C3-01: `panel_watcher` i
`auto_resync` wykonywały dual-write:

1. trwały `emit(event_id)`;
2. `if emitted_id:`;
3. `state_machine.update_from_event(...)`.

Po commicie eventu i błędzie zapisu `orders_state` następny tick dostawał
`None` z deduplikacji i nigdy nie ponawiał kroku 3. Trigger nie był
teoretyczny: ochronny `StateReadError` może wystąpić po niekompletnym odczycie,
a `CorruptedTimestampError` może zostać podniesiony po częściowo skutecznym
handlerze. `auto_resync` dodatkowo raportował taki przypadek jako sukces.

Pierwszy kandydat oparty tylko o postcondition został odrzucony przez dwóch
świeżych reviewerów. Reprodukcje wykazały: nieatomowy check-then-act, użycie
payloadu T2 do eventu T1, stale reuse statycznego ID, konflikt terminalny,
crash state→plan bez recovery, fałszywy sukces bezwyjątkowego no-op oraz zmianę
znaczenia liczników emisji. Finalny projekt usuwa te klasy u źródła.

Kolejny blind snapshot `7e0675df…` także został prawidłowo odrzucony. Świeży
reviewer współbieżności wykazał cztery luki: direct writer omijający lifecycle
lock, status T2 zamykający receipt T1 po wyjątku, starvation przy `LIMIT 100`
oraz uznanie istniejącego rekordu bez `updated_at` za niezmieniony. Każda
reprodukcja ma teraz osobny czerwono-zielony golden; ten snapshot nie został
promowany.

Pierwszy review późniejszego snapshotu `e7f80286…` również zakończył się
`CONFIRMED_DEFECT` (7 reprodukcji; verdict SHA-256
`b01fb0ca8bbaad62246456536e5ecc8256185521431cfe76aa952f7fb146e57b`).
Wykrył: exact marker przegrywający z oracle `superseded`, wersję zmienioną
ortogonalnym writerem błędnie uznawaną za wyparcie, fail-open reader produkcyjny,
odwrócenie FIFO po crashu state→receipt, starvation state przez backlog
downstream, post-commit lukę mirrora audit i brak `fsync` katalogu po rename.
Snapshot został odrzucony; wszystkie siedem klas ma w finalnym kandydacie
osobne goldeny. Review finalnego, ponownie przypiętego bundle'u jest osobną
bramką i nie dziedziczy tego werdyktu.

Ponownie przypięty snapshot `e705e534…` także został odrzucony przez świeży
review współbieżności (4 reprodukcje; verdict SHA-256
`88a3e57ab68609259a603da7e53a1064392cd0a5f6d55bed6b4076cad2f63509`).
Wykrył: nieidempotentny zapis `PANEL_AGREE/PANEL_OVERRIDE` przed późniejszym
błędem callbacku, odwrócenie FIFO dla identycznego `created_at`, starvation
101. wiersza przy zamrożonym zegarze oraz recovery nieznanej wersji oparte o
niemonotoniczny czas ścienny. Snapshot został odrzucony. Finalny kandydat ma
osobne goldeny wszystkich czterech klas i nie dziedziczy żadnego CLEAN.

Pierwszy kandydat oznaczony jako finalny (`b2997cff…`) również został
prawidłowo odrzucony przez świeży review (2 reprodukcje; verdict SHA-256
`2cfdeeb3ed268ed5d2f0af96a866b31180affff3669c2629e073dfff6895e6f8`).
Reviewer wykazał utratę jednokrotnej intencji T2, gdy starszy T1 nadal był
pending, oraz fałszywe zamknięcie T1 przy zgodnym efekcie i identycznym
`updated_at`, ale innym markerze lifecycle. Pin został unieważniony. Finalny
kandydat utrwala T2 z jawną zależnością od poprzednika, buduje pełny łańcuch
T1→T2→T3 i wymaga zgodności zarówno wersji, jak i markera; obie reprodukcje
oraz wariant trzech intencji mają osobne goldeny.

Następny dokładnie przypięty kandydat (pin SHA-256 `e91f58a0…`, diff SHA-256
`42f3df20…`) także został odrzucony, bez dziedziczenia wcześniejszych CLEAN.
Świeży reviewer wykazał dwie osiągalne luki (verdict SHA-256
`ddc1d42f1ce62bebe77f6b6488f99f4dfebe4990891b382c893229a88e93007d`):
queue consumer mógł pobrać durable event przed `orders_state`, a dwa kolejne
przesunięcia czasu o tę samą deltę współdzieliły `event_key` i drugie mogło
zostać uznane za retry pierwszego. Pin został unieważniony. Bieżący kandydat
bramkuje widoczność kolejki stanem receiptu oraz wiąże klucz zmiany czasu z
całym przejściem stare→nowe; obie reprodukcje mają współbieżne goldeny.

Kandydat `/tmp/c3_candidate_20260719_Y9OuOm.pin.json` również został
unieważniony. Trzy świeże review potwierdziły dalsze osiągalne defekty:
utracony adres w ścieżce disappeared→delivered, rozbieżne klucze assignment /
pickup / return twins, ponowne A po cyklu A→B→A zasłonięte przez stary callback,
stary marker DELIVERED po resurrection, poison-row dla trwale wadliwego czasu,
niepełne liczniki reconcile, usunięcie causal predecessora przez cleanup oraz
wyścigi copytruncate i `.1→.2` podczas skanu rotacji. Werdykty SHA-256:
`eb8a5363818c7301b3e6bfd8308f5cb3b240a10f6708cf93f2b489d9875c2e75`,
`ee41ab923ddf126bf5ee63cdd17c6bac084074bb52620e716ab295a8891df1eb`,
`a0c9639e26aa3a1721d606dd78b14db99cbe6063116c9c3d1aee3d47a4fea986`.
Żaden z tych verdictów nie jest CLEAN ani nie jest dziedziczony przez bieżący
kandydat. Wszystkie klasy mają teraz osobne reprodukcje; nowy pin powstaje
dopiero po niniejszej regresji i DoD.

Także przypięty później wariant v3 został odrzucony, mimo zielonej pełnej
regresji. Diff SHA-256:
`639411de5e6dceb9b96d8eebba039d98e6230cdc5635124385ef9aa186ca6140`,
pin SHA-256:
`4bc29e294d97ba64ace78157baeb2b2164c91c8d55c216c583ff622d19c00397`.
Dwa niezależne, świeże procesy `gpt-5.6-sol/xhigh` zwróciły
`CONFIRMED_DEFECT`, nie CLEAN (verdict SHA-256 odpowiednio
`195e7834494d7ca7c8e524c45fa03e868f4dfeea2be09fcfbc424643729af385`
i `f50d2297eb1f43e695bd1d1b33577e1ef5b927b4cd5444c12be226400d40f9da`).
Jedenaście potwierdzonych klas obejmowało: zgubienie różnego successora po
supersede T1, wykonanie starego callbacku po resurrection, nieidempotentny
retry kotwicy planu, poison-row `{}`, dedupe zależne od skończonej retencji
JSONL, flock przywiązany do rotowanego inode, reuse nazwy rotacji bez ENOENT,
nieoznaczoną próbę po migracji, utratę inboxu paczek, stale assignment callback
i return bez identyfikatora starego kuriera. Dodatkowe kontrpróby wykryły
łańcuch A→B→C oraz RETURN A→ASSIGN B przed callbackiem. Wszystkie te scenariusze
mają w bieżącym v4 osobne czerwono-zielone testy; v3 nie został zacommitowany.
Po poprawce ten sam read-only reviewer ponowił własne reprodukcje przeciw
working tree: A→B→C, RETURN→ASSIGN i A4 zakończyły się PASS (`6 passed`), a
cały plik C3 miał `81 passed`; reviewer nie zmieniał plików. To jest dowód
zamknięcia reprodukcji, nie finalny CLEAN wymagany do promocji v4.

Także pierwszy finalny pin v4 (candidate SHA-256
`08b7c23609b83415efb6d659d8ce63f888a0e77c68e7ad4af7204bae77504498`)
został prawidłowo zatrzymany. Świeży `gpt-5.6-sol/xhigh` reviewer B zwrócił
`CONFIRMED_DEFECT` (verdict SHA-256
`702f854a49958216e4dbf6c6e02e6cf398f875ecda3c7b1d71b8da2c40f9cb66`):
legacy source row mógł trwale blokować utworzenie outboxa, audit cleanup mógł
usunąć receipt nadal-pending queue eventu, producent inboxu paczek mógł dopisać
do inode usuniętego po rename oraz skan rotacji fsyncował ponownie otwartą
ścieżkę zamiast inode zawierającego match. Wszystkie cztery interleavingi
zostały odtworzone jako `7 failed`, a po poprawce dały `7 passed`. Drugi
reviewer starego pinu przeczytał cały pakiet, lecz jego transport wyczerpał
`5/5` reconnectów; proces został przerwany bez pliku werdyktu. Nie jest to
CLEAN ani dowód promocji, a stary pin został unieważniony przed jego końcem.

Połączony pin v5 również został unieważniony. Reviewer A zwrócił
`CONFIRMED_DEFECT` (verdict SHA-256
`578351b6c601effdae191d88f135772fd602f0f32493844e709c618d691e443e`):
bridge tworzył generację `K_v` zamiast podnieść rzeczywisty legacy `K`, brak
baseline tokenu mógł zatruć FIFO na stałe, writer courier-api nie fsyncował
katalogu i mógł dokleić nowy JSON do urwanego ogona. Reviewer B także zwrócił
`CONFIRMED_DEFECT` (verdict SHA-256
`f19c8151925129ac3d1354cb64a28e4f5ac717837b7c4b0723c611cbc688e82b`):
logrotate nie uczestniczył w namespace locku, gzip mógł zostać zaakceptowany
przed walidacją stopki CRC, starszy pending tego samego klucza zasłaniał exact
successora, a stary courier writer z rolling deployu omijał nowy lock. Osiem
unikalnych klas odtworzono jako czerwone goldeny; wszystkie są zielone po
poprawce. Żaden werdykt v5 nie jest CLEAN ani nie przechodzi na nowy pin.

Pin v6 również nie został promowany mimo zielonej regresji. Dwa świeże procesy
`gpt-5.6-sol/xhigh` zwróciły `CONFIRMED_DEFECT` (verdict SHA-256
`8f41ebd8682e879b30781d364b4984bcaea5cfa7c60767e16a0160940db18d93`
i `04cdbe1e640832bd51f2ff1794504c959b3d9ffbf072b7c7672eea307e90a9bd`).
Łącznie potwierdziły: usunięcie parcel archive mimo otwartego legacy fd,
poison-row JSON niebędący obiektem, akceptację pickup/delivery niewłaściwego
kuriera lub starej generacji, niedurable resurrection omijające chokepoint,
blokowanie niezależnego downstream przez nieznany token, błędne uznanie
dowolnego markera za legalny cykl, starvation niezależnych callbacków oraz
niebezpieczne `copytruncate` podczas rolling deployu. Każda klasa dostała
osobny czerwono-zielony golden; v6 i jego pin są historyczne.

Końcowa inwentaryzacja rotowanych JSONL ujawniła jeszcze przed nowym pinem, że
dziewięć aktywnych/timerowych writerów oraz nadal wykonywalna migracja używały
gołego `open(..., "a")`. Wrapper bez migracji tych producentów byłby pozorną
ochroną. Wszystkie 12 ścieżek z configu ma teraz jawny producer map i wspólny
appender, a statyczna bramka AST odrzuca powrót bare append w znanych
producentach. Globalny logrotate nie jest nadpisywany: JSONL ma osobny config,
unit, timer i state file poza `/etc/logrotate.d`, więc codzienny obrót systemu
nie blokuje hot-path writerów i nie może wykonać drugiego, odblokowanego obrotu.

## Rozwiązanie

- `event_bus.emit` i `emit_audit` zapisują event oraz rekord
  `state_apply_outbox` w tej samej transakcji SQLite `BEGIN IMMEDIATE`.
- Duplikat dokładnego legacy source eventu bez receiptu jest atomowo
  podnoszony do trwałego outboxa; upgrade weryfikuje typ/order/courier/payload
  i fail-loud odrzuca kolizję tego samego `event_id` z inną semantyką.
- Outbox przechowuje dokładny `state_event`, semantyczny `event_key`, oczekiwaną
  wersję, marker lifecycle, poprzednika przy kolejce one-shot i — dla recovery
  po nieudanym strict-read — hash surowej treści stanu oraz osobne fazy
  `state_status`/`downstream_status` i liczbę prób downstream.
- Faktyczny `event_id` ma deterministyczną generację zależną od `event_key`,
  wersji stanu i dokładnego payloadu. Ten sam retry jest stabilny, a legalny
  późniejszy cykl tego samego klucza dostaje nową generację.
- Wspólny reentrant lock jest mutexem wątkowym i `flock`iem między procesami;
  obejmuje odczyt wersji/outboxa, postcondition i apply do `orders_state`.
- Każdy kanoniczny mutator `orders_state` (nie tylko durable helper) bierze ten
  sam reentrant lock. Direct `update_from_event`, upsert, resurrect, touch,
  delete i prune nie mogą wejść między odczyt wersji a apply.
- Plan/recanon/learning wykonuje osobny cross-thread/cross-process consumer
  lock już po zwolnieniu locka stanu. Selektor zachowuje kolejność `rowid` dla
  tego samego orderu, ale między niezależnymi orderami wybiera najmniej
  próbowany gotowy callback; permanentny błąd A nie głodzi poprawnego B.
  `state=pending` nie uczestniczy w selekcji downstream. Wolny recanon/OSRM nie
  blokuje writerów `orders_state`, a reentrant callback nie wywołuje samego
  siebie rekursywnie.
- `orders_state` zapisuje atomowo marker ostatniego eventu globalnie i per typ.
  Marker per typ nie ginie po ortogonalnej zmianie, np. ASSIGNED → zmiana czasu.
- Oracle ma trzy wyniki: `applied`, `pending`, `superseded`. Stary terminal lub
  assignment nie może nadpisać nowszego stanu.
- Zmiana samego `updated_at` przy niezmienionym oczekiwanym markerze jest
  ortogonalnym RMW i nie gubi T1; zmiana globalnego durable markera dowodzi
  nowszego lifecycle commitu i blokuje stale overwrite.
- Produkcyjna granica durable używa strict readera. Historyczny read-only
  `get_order` nadal może zwrócić pusty fallback, ale nie może już nadać wersji
  oczekiwanej outboxa po uszkodzonym/brakującym pliku.
- Gdy pre-read stanu zawodzi, event nadal jest utrwalany z wersją
  `__STATE_READ_UNAVAILABLE__` oraz SHA-256 surowego pliku. Recovery stosuje
  event tylko przy bajtowo niezmienionym snapshotcie; zmiana treści pozostawia
  intencję pending fail-closed. Jeżeli po skutecznym recovery oczekiwany token
  nie istnieje już w żadnym wiarygodnym snapshotcie, row kończy się jawnie jako
  `state_token_indeterminate/superseded`, zamiast zatruwać causal lane.
  Mechanizm nie używa `updated_at`, mtime ani zegara ściennego.
- Po wyjątku lub bezwyjątkowym apply samo zgodne `status` nie wystarcza:
  wymagany jest exact marker eventu. Zgodny efekt z markerem/wersją T2
  klasyfikuje T1 jako superseded i nie uruchamia jego downstream.
- Drainer na początku cyklu watchera wznawia pending rows niezależnie od tego,
  czy pierwotny call-site jeszcze raz się pojawi. Faza stanu sortuje po liczbie
  prób, a potem `rowid`, więc permanentnie błędne rows nie głodzą nowszych nawet
  przy całkowicie zamrożonym zegarze. Applied
  rows oczekujące na downstream nie współdzielą LIMIT-u z lane'em state. Dopiero
  po tej fazie osobny consumer domyka causal FIFO per order po `rowid` i fair
  lane niezależnych orderów po `downstream_attempts,rowid`. Starszy
  `state=pending` nadal bramkuje swojego zależnego successora przez jawny
  predecessor, ale nie blokuje niezależnego gotowego callbacku. Błąd callbacku
  nie zatrzymuje writerów stanu.
- Przed emisją nowego typu lifecycle dla tego samego orderu helper najpierw
  próbuje domknąć najstarszy unresolved receipt. Jeżeli T1 nadal jest pending,
  bieżący one-shot T2 mimo to powstaje w tej samej wizycie i wskazuje ostatnią
  pending intencję jako `predecessor_event_id`. T3 zależy od T2, nie równolegle
  od T1, więc fair retry nie może odwrócić causal state order.
- Dla tego samego klucza istniejący `state=pending` successor ma pierwszeństwo
  przed starszym `state=applied/downstream=pending`. Retry wznawia exact nowe A
  zamiast tworzyć trzecie A. Gdy dwa odczyty przed persist zawiodły, recovery
  odróżnia realny cykl A→B→A od zwykłego retry: niepewny successor zwykłego
  retry jest trwale `superseded`, po czym domykany jest dokładnie stary callback.
- Przed utworzeniem generacji helper szuka dokładnego legacy source `K` bez
  outboxa i podnosi właśnie ten identyfikator. Exact pending successor ma
  pierwszeństwo przed starszym applied/downstream-pending, więc retry T2 nie
  tworzy sztucznego T3.
- Plan/recanon/learning są routowane jednym callbackiem na podstawie dokładnego
  trwałego eventu. Receipt eliminuje trwałą utratę w oknie state→downstream;
  plan/recanon pozostają at-least-once w skrajnym crashu po efekcie, a przed
  receiptem. Nieidempotentny learning ma osobny, bezterminowy ledger SQLite
  `durable_learning_projection` z unikalnym event-level effect ID; JSONL jest
  tylko materializowaną projekcją. Pierwszy committed record wygrywa nawet po
  zmianie plików propozycji i po wypadnięciu linii z 30 rotacji. Append używa
  stabilnego `<name>.append.lock`, `fsync` pliku i katalogu. Retry skanuje
  live+rotacje (w tym `.gz`) jako recovery projekcji, ale historia plikowa nie
  jest już oracle jednokrotności. Snapshot rotacji wiąże nazwę z dev/inode,
  size i mtime; ENOENT, podmiana inode lub ruch namespace wymuszają pełny retry,
  nigdy clean miss. Gzip jest konsumowany do EOF, więc match nie może ominąć
  walidacji CRC/ISIZE. Migracja traktuje istniejące callbacki jako próbę
  indeterminate, podczas gdy nowe rows jawnie startują od zera.
- Trwale wadliwy `state_event` jest odrzucany przed wspólnym commitem źródła i
  outboxa. Korupcja lub legacy poison-row jest terminalizowany z jawnym błędem,
  `downstream=skipped` i zamknięciem queue eventu, więc nie blokuje globalnego
  FIFO bez końca.
- Assignment i return utrwalają `previous_courier_id` w konkretnym
  `state_event` przed mutacją stanu. Callback nie zgaduje starego kuriera z
  późniejszego mutable current: łańcuch A→B→C czyści A i B, a opóźniony RETURN
  A po ASSIGN B nie usuwa nowego planu B. Stare callbacki assignment nadal
  wykonują event-local cleanup, ale nie zapisują planu nieaktualnego kuriera.
- Retry dostawy i zwrotu nie modyfikuje planu, jeżeli dokładne zlecenie zostało
  już usunięte. Stary callback nie może nadpisać nowszej kotwicy planu.
- Legacy helpery planu/learning zachowują domyślny tryb best-effort dla starych
  callerów, ale kanoniczny callback outboxa włącza tryb strict. Wewnętrzny błąd
  — także wewnątrz `recanon/redecide` — nie może zostać połknięty i fałszywie
  zamknąć receipt; zostaje `pending`.
- Liczniki `new/assigned/picked_up/delivered` nadal znaczą utworzenie eventu,
  nie recovery istniejącego eventu.
- `auto_resync` nie liczy failed/no-op jako sukces, rozróżnia superseded,
  pending downstream i błąd całego durable helpera per order. Gdy peer domknie
  downstream po naszym state transition, wynik nadal jest resynciem, nie dedupem.
- Mirrowane queue eventy (`PICKED_UP`/`DELIVERED`) zapisują queue, outbox i
  `audit_log` w jednym commicie. Legacy emit bez `state_event` zachowuje
  dotychczasowy best-effort mirror.
- `event_bus.get_pending` pokazuje legacy event bez outboxu od razu, lecz
  durable queue event dopiero po `state_status=applied`. Konsument nie może
  więc wykonać ani oznaczyć eventu przed trwałym stanem, nawet jeśli nie bierze
  lifecycle locka. Superseded queue receipt w tej samej transakcji zamyka
  event jako processed i zapisuje dedupe; surowy `get_pending_count` nadal
  obejmuje state-pending, aby alarm stuck nie stracił widoczności backlogu.
- Cleanup queue usuwa stary processed event, jego processed dedupe i zamknięty
  durable outbox w jednej transakcji. Nierozwiązany state/downstream receipt
  blokuje retencję całej trójki, więc nie powstaje okno orphan/collision między
  retencją 48 h kolejki i 90 d audit logu.
- Cleanup queue i audit nie usuwają zamkniętego predecessora wskazywanego przez
  nierozwiązane dziecko. Audit row, receipt i causal chain są chronione w jednej
  transakcji `BEGIN IMMEDIATE`.
- Klucze `CZAS_KURIERA_UPDATED` i `PICKUP_TIME_UPDATED` zachowują historyczny
  prefiks delta/suffix, ale dostają SHA-256 semantycznego przejścia stare→nowe.
  Retry identycznego przejścia ma ten sam klucz; kolejne `+5`, kolejne inne
  targety oraz powrót do wcześniejszego targetu tworzą odrębne intencje.
- Atomowy zapis `orders_state` wykonuje `fsync` pliku, rename oraz `fsync`
  katalogu. Recovery exact markera przed receiptem ponawia `fsync` katalogu;
  jego błąd pozostawia row pending.
- Lazy migracja outboxa jest addytywna, wykonywana w `BEGIN IMMEDIATE` i
  serializowana także między wątkami. Startujące równolegle procesy nie ścigają
  się na `ALTER TABLE expected_state_token`.
- Wszystkie assignment twins używają `{oid}_COURIER_ASSIGNED_{cid}_canonical`,
  pickup twins `{oid}_COURIER_PICKED_UP_canonical`, a panel/reconcile return
  `{oid}_ORDER_RETURNED_{reason}_canonical`. Payload delivered zachowuje razem
  `final_location` i `delivery_address`.
- Resurrection jest first-class durable eventem `ORDER_RESURRECTED`: tworzy
  nowy marker epoki, czyści marker DELIVERED i przechodzi przez ten sam outbox
  oraz downstream lock. Jeśli callback starej dostawy wygra wyścig, callback
  korekty naprawia lub invaliduje plan; jeśli korekta wygra, dostawa widzi stan
  aktywny i jest no-opem. Publiczna surowa ścieżka resurrection została usunięta
  z produkcyjnego call-site'u. Trwale wadliwe/suppressed time events kończą się
  `superseded`, więc nie zatruwają causal lane'u.
- Bliźniaki paczkowe `parcel_assign` i status inbox używają tego samego durable
  bridge; `COURIER_ASSIGNED` trafia do `emit_audit`, a historyczny brak mutacji
  `courier_plans` dla źródeł paczkowych pozostaje bez zmiany. Status inbox
  oraz producent w `courier-api` współdzielą stabilny `.lock`; writer bierze go
  przed otwarciem aktywnego inode, domyka partial write, fsyncuje plik i katalog,
  a consumer trzyma lock tylko przez krótki rename+fsync. Snapshot trafia do
  unikalnego `.pending.<ns>.<pid>` i pozostaje aż do terminalnego replayu.
  Przed unlinkiem consumer porównuje dev/inode archive z bezpośrednim skanem
  `/proc/*/fd`; każdy otwarty descriptor, błąd uprawnień lub niestabilny proces
  oznacza fail-closed i zachowanie archive. Marker procesu nie jest oracle.
  Nie istnieje już okno open-fd→rename→unlink; failed emit, nie-obiektowy JSON
  i append po snapshotcie pozostają retry-visible bez blokowania endpointu na
  czas downstream.
- Repo zawiera rozdzielone kandydaty rotacji. Globalny
  `deploy/dispatch-v2-logrotate.conf` obejmuje wyłącznie tradycyjne `.log`.
  Dwanaście JSONL używa rename+create (bez `copytruncate`) wyłącznie w
  `dispatch-v2-jsonl-logrotate.conf`, uruchamianym przez osobny unit/timer.
  Wrapper bierze wszystkie `<name>.append.lock`, a następnie fail-closed skanuje
  aktywne i rotowane dev/inode w `/proc/*/fd`. Writer legacy otwierający ścieżkę
  po skanie jest bezpieczny: zapisuje albo do nadal podlinkowanej rotacji, albo
  do nowego active. `delaycompress` i ponowny skan przed kolejnym obrotem
  chronią długo żyjący stary fd. Dedykowany state file i config poza
  `/etc/logrotate.d` eliminują podwójny, odblokowany obrót. Żywy
  config/unit/timer nie zostały zmienione bez osobnego ACK.

## Mapa kompletności ETAP 3

| Miejsce | Rola | Writer/consumer | Dotknięte | Powód | Test/dowód |
|---|---|---|---|---|---|
| `event_bus.emit` | queue event + outbox | writer | TAK | jedna transakcja event/outbox | rollback-insert queue golden |
| `event_bus.get_pending` | queue visibility | consumer boundary | TAK | durable event widoczny dopiero po state apply; legacy bez outboxu bez zmiany | paused-state concurrent golden + superseded/legacy golden |
| `event_bus.cleanup` | queue/outbox retention | writer | TAK | atomowe usunięcie zamkniętej trójki; unresolved blokuje cleanup | closed-vs-unresolved retention golden + istniejące mirror tests |
| `event_bus.cleanup_audit_log` | audit/outbox retention | writer | TAK | predecessor i jego audit row nie mogą zniknąć przed dzieckiem | audit causal-retention golden |
| `event_bus.emit_audit` | audit event + outbox | writer | TAK | ten sam kontrakt dla audit types | rollback-insert audit golden |
| `event_bus.state_apply_outbox` | durable receipt | writer/reader | TAK | exact payload, expected version+marker+token, predecessor, próby downstream i jawne fazy | additive/concurrent migration + SQLite assertions + drainer |
| `event_bus.durable_learning_projection` | trwały receipt efektu learning | writer/reader | TAK | jednokrotność nie zależy od retencji JSONL; first classification wins | rotation-history + classification-change goldeny |
| `event_bus.audit_log` mirror | 90-dniowa historia | writer | TAK | durable queue+outbox+mirror atomowo, legacy best-effort bez zmiany | atomic rollback + no-postcommit-gap goldeny |
| `state_machine.lifecycle_apply_lock` | concurrency boundary | writer guard | TAK | thread + process mutual exclusion, obowiązkowy dla wszystkich mutatorów | barrier + direct-writer golden |
| `state_machine.get_order_strict` | read→write/downstream boundary | reader | TAK | brak `{}` fail-open jako fałszywej wersji lub callback data | wiring + state/downstream strict-read goldeny |
| `state_machine.state_storage_token` | recovery snapshot | reader | TAK | hash treści zamiast niemonotonicznego zegara | future-clock + changed-token goldeny |
| `state_machine._atomic_write` | trwały JSON commit | writer | TAK | fsync pliku i katalogu przed receiptem | fd-kind spy + failed-fsync retry golden |
| `state_machine.lifecycle_downstream_lock` | plan/recanon causal lane | consumer guard | TAK | wolny downstream poza lockiem stanu, jedna egzekucja naraz; FIFO per order + fair niezależne ordery | slow-downstream/direct-writer + FIFO/fairness goldeny |
| `state_machine.event_effect_status` | postcondition FSM | reader | TAK | applied/pending/superseded z walidacją kuriera i generacji | tabela oracle + wrong-courier/stale-generation conflicts |
| `state_machine.update_from_event` | `orders_state` writer | writer | TAK | atomowy marker eventu globalny/per typ | marker recovery/mutation probe |
| `ORDER_RESURRECTED`, `order_fsm`, `panel_watcher` | durable korekta delivered | writer/consumer | TAK | korekta przechodzi przez outbox, marker epoki i naprawę planu po wyścigu | correction-vs-delivery race + FSM/resurrection regressions |
| `durable_event_apply` | wspólny protokół | orchestrator | TAK | generation, exact retry, token/marker recovery, predecessor chain, fair state + causal FIFO | goldeny pliku C3 |
| `panel_watcher` NEW_ORDER | lifecycle | writer | TAK | event/state przez chokepoint | read-failure recovery |
| initial/diff/reassign ASSIGNED | lifecycle + learning/plan | writer/consumer | TAK | recovery i exact courier/source | marker + istniejące testy panelu |
| packs/cold-start ASSIGNED | catch-up twins | writer/consumer | TAK | stabilny key, state payload parity | V3.15/cold-start tests |
| disappeared/reconcile/packs-ghost DELIVERED | canonical twins | writer/consumer | TAK | T1 nie może zostać zastąpione T2 | cross-writer exact-payload golden |
| panel/reconcile RETURNED | terminal twins | writer/consumer | TAK | state i plan removal odzyskiwalne | TASK2A + returned golden |
| reconcile/ground-truth PICKED_UP | pickup twins | writer/consumer | TAK | wspólny protokół i licznik emisji | watcher cluster |
| CK/PICKUP_TIME update | committed-time twins | writer/consumer | TAK | klucz obejmuje semantyczne przejście, reusable IDs dostają generacje | equal-delta/return-target golden + time tests + marker-per-type |
| `lifecycle_downstream` | plan/recanon/learning router | consumer | TAK | exact stored event, event-local previous courier, strict read/error propagation, durable receipt | crash-before-downstream + A→B→C + RETURN→ASSIGN + strict-read recovery |
| `core/jsonl_appender`, `core/jsonl_rotation` | JSONL namespace | writer/rotator | TAK | stabilny lock, partial-tail separator, durable batch, CRC-to-EOF i obrót pod tym samym lockiem | two-writer rotate + pathname-reuse + truncated-tail + plain/gzip + wrapper goldeny |
| plan/shadow/SLA/monitor/OBJ/ETA/enricher/drive-min/czasówka/geocode/migration writers | 12 rotowanych JSONL | writer | TAK | każdy realny producer uczestniczy w namespace locku; zachowane opcje serializacji i fail-soft | focused 181/1 + appender/drive 63/1 + AST producer gate |
| `plan_manager.advance_plan/remove_stops` | retry efektów planu | writer | TAK | stary callback nie nadpisuje kotwicy i nie bumpuje wersji po wcześniejszym usunięciu oid | newer-anchor + idempotent-remove goldeny |
| `shift_notifications.state` | drugi writer learning JSONL | writer | TAK | uczestniczy w tym samym namespace locku co panel/Telegram | shift + JSONL cluster |
| `plan_recheck.py` | recanon/redecide downstream | consumer | TAK | prywatny tryb strict odróżnia no-op od wewnętrznego wyjątku | strict read-error golden + pełna regresja |
| `auto_resync.auto_resync_phantoms` | phantom repair | writer | TAK | brak false success i audit-emitter parity | reconciliation script tests |
| `reconcile_worker` | runtime wiring | consumer | TAK | audit emitter + 3-state oracle + downstream | reconciliation tests |
| `reconcile_log` | schema działań | consumer | TAK | jawne failed/superseded/pending | round-trip/health tests |
| `parcel_assign`, `parcel_lane_merge` status inbox | parcel lifecycle twins | writer | TAK | durable bridge, retry archives, bezpośredni oracle `/proc` przed unlinkiem, malformed-row isolation | parcel focused + legacy-open-fd + non-object JSON + generation goldeny |
| `courier_api/main.py::_maybe_inbox_parcel_status` | parcel inbox producer | writer, companion repo | TAK | ten sam sidecar lock przed open/write/fsync zamyka cooperative open-fd→unlink race | cross-repo blocking golden + pełna suita courier-api |
| `parcel_lane_merge` NEW_ORDER/retire | state-first/bez-eventowy flow | writer | N-D | NEW_ORDER jest emitowany w każdym ticku już po state; retire nie ma badanej granicy event→state | parcel regression |
| `dispatch_pipeline` pre-recheck | osobny sygnał czasu | writer | N-D | brak badanego event→state guardu w aktualnym HEAD | grep + pełna regresja |
| `route_order.py`, `route_podjazdy.py` | routing | consumer | N-D | brak writerów event→state i brak zmiany kanonu trasy | pełna regresja |
| scoring/feasibility/selection | decyzja | consumer | N-D | brak zmiany HARD/SOFT, wag, filtrów i tie-breaków | pełna regresja |
| AUTO_KOORD zewnętrzny | polecenie panelowe | consumer | N-D | ma własny pre-check/retry i pozostaje pod istniejącą flagą; nie jest częścią spójności event→orders_state | istniejące auto_koord tests |
| dwa configi logrotate | global `.log` + dedykowany JSONL | config writer | TAK kod | JSONL poza global include; rename+create, brak `copytruncate` i podwójnego obrotu | oba `logrotate --debug` exit 0 + manifest/config golden |
| JSONL logrotate service/timer | scheduler rotacji | systemd writer | TAK kod | wszystkie namespace locki + otwarte-inode attestation, osobny state file | `systemd-analyze verify` exit 0 + open-fd/TOCTOU goldeny |
| flagi/fingerprint | config decyzji | consumer | N-D | brak nowej flagi i zmiany zachowania decyzji | checker 506/506 |
| produkcyjny `events.db` / `orders_state` | live data | writer | N-D live | implementacja migracji jest gotowa, ale niczego nie uruchomiono na live bez ACK | status operacyjny |

N-D: route_order.py — brak writerów granicy event→state; kanon HARD/SOFT i
sekwencja trasy pozostają bez zmian.

N-D: route_podjazdy.py — brak writerów granicy event→state i brak zmiany
display/planu; pełna regresja pokrywa konsumentów.

N-D: core/candidates.py — `plan_recheck` zmienia wyłącznie prywatną propagację
wyjątku do durable receipt; nie zmienia budowy kandydatów, scoringu ani metryk.

N-D: feasibility_v2.py — brak zmiany bramek HARD/SOFT, R6 i ich precedencji;
tryb strict `recanon/redecide` nie zmienia wyniku poprawnego wywołania.

N-D: route_simulator_v2.py — brak zmiany symulacji i kolejności; callback
planu jedynie przestaje maskować nieoczekiwany wyjątek w trybie outboxa.

N-D: sla_anchor.py — brak zmiany źródła lub semantyki kotwicy SLA; dotknięty
`plan_recheck` zachowuje wszystkie dotychczasowe ścieżki sukces/no-op.

## Dowody

regresja: 5263 passed, 0 failed, 77 skipped, 8 xfailed, 147 warnings w
`324.04 s`; baseline `5141 passed, 0 failed, 74 skipped, 8 xfailed` w
`281.36 s`. Trzy dodatkowe skipy to udokumentowane zegarowe self-skipy
`test_preshift_window`; suma przypadków wzrosła o 125, czyli wynik obejmuje
`+122 passed` i te trzy skipy, bez nowego xfaila.

e2e: prawdziwy SQLite queue/audit emit → atomowy outbox → atomowy
`orders_state` z markerem → downstream receipt → duplicate/drain recovery;
pełna suita przechodzi także przez istniejące assess_order, plan, reconcile i
serializer consumers.

pozytywny-wplyw: zmiana reliability nie ma flagi ON/OFF; replay realnego
defektu po staremu pozostawia pending state/no-op albo nadpisuje T1 przez T2,
a finalny golden odzyskuje exact event lub bezpiecznie klasyfikuje go jako
superseded. Mutation probe usuwa marker i daje czerwony test; finalny kod go
przechodzi.

rollback: `git revert <commit-C3-01>`, pełna suita, kontrolowany restart
`dispatch-panel-watcher`; addytywną tabelę pozostawić, a w razie potrzeby
odtworzyć `events.db` wyłącznie ze spójnego backupu SQLite `.backup`.

- Czysty baseline na tym samym base i kompletnym pkgroot:
  `5141 passed, 74 skipped, 8 xfailed, 0 failed` w 281.36 s.
- Bieżący kandydat v7 po naprawie wszystkich reprodukcji review, ta sama
  komenda z `HERMETIC_STRICT=1`:
  `5263 passed, 77 skipped, 8 xfailed, 0 failed` w 324.04 s.
- Delta przypadków: `+125`; wynik pass `+122`, trzy zegarowe self-skipy
  preshift, xfail bez zmiany, zero faila.
- Dokładne self-skipy: `test_preshift_window_penalty_2026_06_24.py` linie
  88, 100 i 110 (`unik wrapu północy`). Izolowany odczyt tego historycznego
  pliku wymaga dopisania `deploy_staging/scripts` do `PYTHONPATH`, bo sam test
  ma istniejącą zależność od kolejności importów; pełna suita i baseline mają
  ten sam kontrakt. Z poprawną ścieżką: `4 passed, 3 skipped`.
- Skupione migracje producerów/logrotate oraz ich konsumenci:
  `181 passed, 1 skip`; appender + drive-min po zachowaniu serializer parity:
  `63 passed, 1 skip`; wcześniejszy szerszy klaster watcher/event-bus/time/
  reconciliation/parcel/shift: `155 passed, 1 skip, 1 xfail`. Sam plik C3
  przed ostatnimi siedmioma goldenami miał `81 passed` (także niezależnie
  powtórzony przez read-only reviewera).
- Companion courier-api: pełna suita w kanonicznym courier `.venv` i z poprawnym
  `PYTHONPATH` obu pkgrootów `188 passed, 1 skipped`; sam nowy blocking golden
  był czerwony na starym writerze i zielony po wspólnym locku. Jeden przebieg
  w dispatch venv był nieważny (12 collection errors: brak FastAPI/uvicorn) i
  nie jest liczony jako test kandydata.
- Mutation probe: usunięcie `exact_marker` z warunku recovery daje oczekiwany
  fail `test_exact_marker_survives_orthogonal_lifecycle_event`; po przywróceniu
  ten sam nodeid przechodzi.
- Goldeny obejmują: emit success→state fail→retry, strict pre-read fail z
  utrwaleniem eventu, queue/audit rollback przy failu outbox insert, T1/T2
  cross-writer, crash state→downstream, dwa równoległe wątki, terminal↔terminal,
  ten sam terminal z innym payloadem, reusable semantic key, nonthrowing no-op,
  unknown-version vs newer state, marker po ortogonalnym evencie oraz błąd
  wewnątrz helpera planu pozostawiający receipt do skutecznego recovery.
- Goldeny po blind v5 obejmują dodatkowo: direct writer podczas version window,
  efekt T2 + wyjątek T1, fair retry 101. row po 100 permanentnych błędach,
  istniejący rekord bez `updated_at` oraz strict wyjątek `recanon`.
- Goldeny finalnego rozdzielenia lane'ów obejmują: wolny downstream, podczas
  którego bezpośredni writer kończy zapis stanu; causal FIFO dwóch receiptów;
  jawny `downstream_fn=None` domykający receipt jako kontrolowany no-op; oraz
  zakaz użycia takiego no-opa do pominięcia starszego realnego receipt.
- Goldeny po finalnym review obejmują dodatkowo: exact marker przed oracle
  `superseded`, crash-order T1→T2, 101 applied/downstream rows bez starvation
  state, ortogonalny waiting_at kontra konfliktujący marker T2, strict runtime
  reader, atomowy audit mirror, parent-dir fsync i pending po jego błędzie.
- Goldeny po review `88a3e57a…` obejmują dodatkowo: jeden learning record po
  retry późniejszego kroku, FIFO insert-order przy identycznym timestampie,
  fair 101. row przy zamrożonym zegarze, recovery po hash snapshotu niezależne
  od czasu, brak tokenu fail-closed, per-order T1 gate, strict downstream read,
  równoległą addytywną migrację oraz urwany tail JSONL.
- Goldeny po review `2cfdeeb3…` obejmują dodatkowo: utrwalenie jednokrotnego T2
  mimo nadal pending T1, causal chain T1→T2→T3, konflikt markera przy identycznym
  timestampie oraz idempotentny retry learning po rotacji plain i `.gz`.
- Goldeny po review `ddc1d42f…` obejmują dodatkowo: pauzę state apply po
  atomowym commicie i brak widoczności dla równoległego queue consumera,
  zachowanie legacy eventu bez outboxu, atomowe zamknięcie superseded queue
  eventu, dwie kolejne równe delty przy pierwszym downstream pending, parytet
  klucza pickup oraz atomową retencję closed/unresolved outboxa.
- Goldeny po trzech ostatnich odrzuconych review obejmują dodatkowo: A→B→A z
  dwoma błędami odczytu, zwykły retry bez duplikacji callbacku, widoczny pending
  successor tego samego klucza, resurrection przed recovery, invalid/suppressed
  time poison, oba cleanupy z predecessorem, adres delivered, canonical twins,
  `.1→.2` race oraz recovery status inbox paczek.
- Goldeny v4 po dwóch odrzuconych review final-v3 obejmują dodatkowo: różny T2
  po supersede T1, obsolete delivery po resurrection+T2, ochronę nowszej
  kotwicy planu, walidację przed commitem i migracyjne zamknięcie poison-row,
  ledger learning niezależny od 30 rotacji, first-classification-wins,
  stabilny namespace lock dwóch writerów, reuse numericznej nazwy z innym
  inode, migrację indeterminate attempt, failed emit/append-after-snapshot
  inboxu, stale assignment, return bez cid, A→B→C oraz RETURN→ASSIGN.
- Goldeny po odrzuconym pinie v4 obejmują dodatkowo: upgrade legacy queue
  pending i processed, upgrade legacy audit, fail-loud kolizję source ID,
  retencję zamkniętego receiptu do `events.status=processed`, fsync dokładnego
  rotowanego inode z re-snapshotem namespace oraz blokadę producenta inboxu
  przed open aż do końca rename/unlink boundary.
- `py_compile`: PASS w dispatch i courier-api.
- `ziomek-cto dod`: PASS mechaniczny na finalnym 44-plikowym diffie v7;
  odczytał regresję `5263/0/77/8`, E2E, pozytywny replay, kompletne N-D i
  rollback. Nie zastępuje przerwanego finalnego blind review, dlatego kandydat
  pozostaje HOLD.
- Pin v7: dispatch code/test patch SHA-256
  `239973851a574b6d1032ee8315ed453e4d26e4e907de31269fab34de7fc673a5`,
  courier patch SHA-256
  `b41f9d75ce2b855ab1cafbd9e41111254cfa613aac395a1ed5f50abcbee76f24`,
  pin SHA-256
  `474164bed4a2ffedac82bb2dac51ecaf9bfd12bc2603411b3c198eae69a7ac3f`.
  Blind driver zweryfikował pin i przepuścił dokładnie `SCOPE.json`,
  `dispatch.txt`, `courier.txt`, bez evidence autora.
- Dwa świeże procesy review v7 przeczytały znaczną część przypiętego bundle'u,
  ale zostały jawnie przerwane poleceniem ownera „stop, zacommituj podzbiór
  C3-01 teraz”. Oba zakończyły się kodem 1 po SIGINT i nie utworzyły finalnych
  plików verdict JSON. Nie są liczone jako `CLEAN`; commit pozostaje kandydatem
  HOLD do at#218/fali jakości.
- `git diff --check`: PASS.
- `tools/flag_lifecycle_check.py`: `506/506`, 0 błędów.
- Entropy dashboard: `dead-flag=0`, `sentinel-as-data=0`; brak nowej flagi.
- Repo-skill `litter`: brak kandydatów przecieku. Ostatni nocny guard ma ALERT
  wyłącznie `SUITE-CONTRACT-UNEXPECTED(25)` z powodu nowszych testów względem
  wspólnego manifestu; sama nocna suita miała `5191 passed, 0 failed`, a bieżąca
  pełna hermetyczna suita ma 0 failów. Chronionego dirty manifestu nie zmieniano.
- Oba configi przechodzą `logrotate --debug` exit 0; dedykowany config widzi
  dokładnie 12 JSONL, globalny nie zawiera żadnego JSONL. Service/timer
  przechodzą `systemd-analyze verify` exit 0.
- Testy użyły `DISPATCH_UNDER_PYTEST=1`, `HERMETIC_STRICT=1`, tymczasowego
  `DISPATCH_STATE_DIR/events.db` i read-only snapshotu `flags.json`. Nie było
  zapisu do produkcyjnego state/logów/flag.

## Migracja, wydanie, obserwacja i rollback

`state_apply_outbox` jest addytywną, idempotentną tabelą tworzoną lazy przez
`CREATE TABLE/INDEX IF NOT EXISTS`; istniejący kandydacki schemat dostaje
addytywnie kolumny marker/token/predecessor/licznik prób downstream.
Inicjalizacja używa thread locka i `BEGIN IMMEDIATE`, a test ośmiu równoległych
inicjalizatorów przechodzi. To nadal jest migracja runtime. Implementacja i
testy są GO w zleconym zakresie, ale utworzenie/ALTER tabeli na live, restart
lub deploy wymagają osobnego biznesowego ACK i wykonania przez aktywnego
MAIN/FLIPMASTER-a. Kandydat nie dotknął produkcyjnego `events.db`.

Przed wydaniem:

1. zsynchronizować base i ponowić pełną regresję;
2. wykonać spójny backup SQLite przez API `.backup`, backup kodu oraz kopie
   `/etc/logrotate.d/dispatch-v2`, unitów/timerów i stanu logrotate;
3. sprawdzić `py_compile`, import i wolne miejsce;
4. commitować jawny pathspec i utworzyć rollback point/tag;
5. uzyskać ACK na migrację/deploy/restart obu repozytoriów, atomową podmianę
   globalnego configu, instalację dedykowanego configu/unitu/timera i pierwszy
   obrót JSONL;
6. wdrożyć companion courier-api jako pierwszy i kontrolowanie go zrestartować;
   potwierdzić, że stary PID zakończył pracę i nie ma już otwartego fd parcel
   archive; bezpośredni oracle `/proc` zachowuje archive fail-closed do tego
   momentu;
7. wdrożyć dispatch i przeładować wszystkich aktywnych, długo żyjących
   producentów rotowanych JSONL (`dispatch-shadow`, panel watcher, SLA tracker
   oraz każdy aktywny odpowiednik); unity oneshot/timery załadują kod w następnym
   uruchomieniu. Nie re-enable i nie restartować świadomie wyłączonego
   `dispatch-telegram` bez osobnego ACK;
8. dopiero po producer-first rollout atomowo zastąpić
   `/etc/logrotate.d/dispatch-v2` wersją bez JSONL, zainstalować
   `/etc/logrotate-dispatch-v2-jsonl.conf` oraz dedykowany service/timer,
   wykonać daemon-reload i kontrolowany pierwszy obrót;
9. zweryfikować właściwe unity, PID/NRestarts/health, fingerprint, schemat tabeli,
   brak otwartych legacy fd, brak rosnącego pending backlogu, stan timera i logi
   `DURABLE_APPLY`.

Obserwacja po wydaniu: minimum 48 godzin. Co cykl należy śledzić
`durable_apply_seen/recovered/superseded/failed`, liczbę unresolved rows, wiek
najstarszego pending oraz zgodność event/state. Deadline werdyktu: 48 h po
faktycznym deployu; bez deployu zegar nie startuje.

Rollback przygotowany: wyłączenie dedykowanego timera, przywrócenie kopii
globalnego configu logrotate, revert obu commitów C3 (dispatch + courier-api),
obie pełne suity, daemon-reload, kontrolowany restart tych samych procesów i
health potwierdzający powrót. Tabeli nie należy
DROP-ować w rollbacku: jest addytywna i ignorowana przez stary kod. Versioned
event IDs są opaque dla istniejących konsumentów, a dodatkowe markery JSON są
wstecznie kompatybilne. W razie rollbacku eventy pozostają prawidłową historią,
a backup SQLite daje punkt odtworzenia bez niespójnej kopii WAL.

## Operacje live i chronione zmiany

Nie wykonano merge, push, deploy, restartu, zmiany flagi ani modyfikacji danych
runtime. Nie dotknięto chronionego
`daily_accounting/kurier_full_names.json`, dirty `ZIOMEK_BACKLOG.md` ani
`tools/night_guard_suite_manifest.json`, `eod_drafts/2026-07-16/` i
`eod_drafts/2026-07-19/` w głównym worktree. Główny worktree courier-api był
czysty i również nie został zmieniony. Shared backlog/memory/handoff pozostają
wyłączną własnością aktywnego MAIN-a.
