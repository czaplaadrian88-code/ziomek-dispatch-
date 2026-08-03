# RUTCOM committed pickup authority — raport kandydata v27, 2026-08-03

## Wynik

Przyczyna incydentu 491578 została usunięta u źródła w kandydacie kodu.
Aplikacja Jakuba nie obliczyła 19:16: wyświetliła stary kanoniczny
`czas_kuriera`, ponieważ watcher i pre-proposal potraktowały nowy, umówiony w
Rutcom czas 19:21 jak pasywny re-stamp i stłumiły go 52 razy.

Fix nie dotyka renderu. Jeden czysty resolver klasyfikuje oba równoległe pola
Rutcom. Granica transportu przed zapisem outboxa zamienia legalny CK na jeden
kanoniczny `PICKUP_TIME_UPDATED`. Jeden handler state atomowo zapisuje pickup,
CK, HH:MM, monotoniczną rewizję oraz provenance. Plan, scoring po potwierdzonym
apply i aplikacja dziedziczą tę samą prawdę.

## Co domknięto w v27

Dwa blind review exact-byte v26 ponownie poprawnie zatrzymały live. Authority
verdict ma SHA-256
`e5bb7976a00f6d515cbfd35b7a5a47a3d0822e96ae33ece3aac07bf32fc47fdd`,
a rollout verdict
`d0a0be2e7da40efef5efdf26ec7d2d2ccde6c82fb85fd3925a54ef2ee84b5472`;
oba przeszły mechaniczny checker jako `CONFIRMED_DEFECT`. MAIN niezależnie
odtworzył wszystkie findings na exact bajtach V26:

1. early branch brakującego agregatu zwracał `pending` dla raw coordinator
   eventu bez claimu, zamiast terminalnie wygasić nieautoryzowaną pracę;
2. receipt z `eligible_at` do 30 sekund w przyszłości mógł trafić do worklisty,
   zostać claimowany i uzyskać authority przed własną epoką;
3. promocja successora przy cofnięciu zegara zapisywała
   `eligible_at < requested_at`, tworząc trwały poison;
4. oid-only `drain()` i `drain_with_receipts()` ACK-owały nieclaimowane
   kanoniczne v4/v5/v6 przed exact durable consumerem;
5. rollback projection zamieniał trwały receipt na scalar z nowym TTL, więc
   opóźniona aktywacja starego kodu mogła skasować pracę mimo zielonego
   preflightu;
6. rollback fence nie był wspólną bramką upgrade, cleanup/drain i ACK, więc
   kolejka mogła zmienić się po exact snapshotcie.

V27 usuwa te przyczyny w istniejących ownerach. Missing-state używa tego samego
`_legacy_time_claim_gate` co wszystkie pozostałe CAS-y i writery. Gotowość
kolejki oraz policy authority wymagają ścisłego `eligible_at <= now` i
`observed_at >= eligible_at`, bez lokalnego skew grantu. Successor zachowuje
immutable click audit, a jego epoka wykonania jest `max(now, prior
eligible_at)`. Kompatybilny oid-only drain konsumuje wyłącznie historyczny
scalar; każdy v4/v5/v6 czeka na exact claim, durable apply i ACK. Jeden
`_queue_mutation_fence` jest wywoływany przez enqueue, upgrade, claim,
pending-cleanup/drain i ACK dla obu rodzajów fence. Rollback zachowania nadal
jest natychmiastowym hot `OFF`, lecz rollback kodu do pre-v4 jest legalny tylko
po opróżnieniu kolejki, exact backupie pustego snapshotu i założeniu fence —
bez konkurencyjnego lifetime ownera i bez TTL rebase.

Negatywny baseline przed fixem miał 11 failed / 2 passed. Po fixie targeted
oracles mają 22/22, szeroki klaster trzech plików 265/265, a osobny positive
control potwierdza scalar legacy drain. Siedem kontrolowanych mutacji ponownie
czerwieniło właściwe bramki: 2F, 1F, 1F, 1F, 3F, 2F i 1F. Exact restore
przywrócił SHA źródeł, ponowny targeted przebieg ma 14/14, a `py_compile`,
import i `git diff --check` są zielone. Pełna hermetyczna regresja ma
6781 passed / 74 skipped / 8 xfailed / 153 warnings / 0 failed w 460,43 s —
dokładnie +8 PASS względem V26 przy identycznym profilu non-pass. DoD oraz dwa
świeże review exact-byte V27 pozostają przed live. Produkcja, procesy i flaga nadal są
nietknięte/OFF; V26 commit
`7266686b29a5bcc0e6e3f9948574d55afef2c8dc` pozostaje odrzucony.

### Mapa kompletności v27

| Miejsce | Rola | Dotknięte | Dowód |
|---|---|---|---|
| `state_machine.event_effect_status` | terminal oracle brakującego agregatu | TAK | oba raw typy, missing claim=`superseded`, read-error=`pending`, writer nie zapisuje |
| `committed_pickup_authority._valid_coordinator_receipt` | authority causal fence | TAK | `observed_at >= eligible_at`, future-in-old-skew oracle i mutation kill |
| `coordinator_time_recheck._receipt_ready` | jedyny queue readiness owner | TAK | strict local eligibility, future retention, bez grantującej tolerancji |
| `coordinator_time_recheck.ack_receipts` | successor promotion / exact ACK | TAK | monotoniczna epoka mimo backward clock; immutable click audit |
| `coordinator_time_recheck.drain*` | compatibility consumer | TAK | scalar-only positive control; trzy negatywne canonical drain oracles |
| `coordinator_time_recheck._queue_mutation_fence` | wspólny owner mutacji | TAK | enqueue/upgrade/claim/pending/ACK, forward i rollback parity |
| `coordinator_time_recheck._legacy_rollback_projection` | code-revert gate | TAK | każdy niepusty scalar/v4/v5/v6 blokuje; zero TTL projection/rebase |
| watcher/apply/outbox/state | exact durable konsumenci | N-D | istniejący claim/apply/ACK pozostaje jednym transportem; brak fallbacku |
| plan/scoring/serializer/apka | konsumenci kanonicznego state | N-D | brak UI override, nowego writera lub zmiany HARD/SOFT |
| testy/ratchet/mutation | antyregresja | TAK | 11F/2P baseline, 22/22 targeted, 265/265 broad, siedem skutecznych mutacji |

## Co domknięto w v26

Dwa blind review exact-byte v25 poprawnie zatrzymały live. Rollout verdict ma
SHA-256 `2f49acc5bb02913a38ebeb95d480e3e4a035b9afac76f79c54cd8c6aade9e0d6`,
a authority verdict
`b577691081c584da284be93fac0c9e0ee7302c27e3d47efced1faf5cb46d6dd9`;
oba zostały mechanicznie sprawdzone jako `CONFIRMED_DEFECT`. MAIN niezależnie
odtworzył wszystkie findings i prześledził ich wspólnych konsumentów:

1. pełna wersjonowana koperta raw `coordinator_force` CK/pickup mogła przejść
   CAS bez dokładnego claimu kolejki;
2. wersjonowany pickup CAS omijał lifecycle fence i przy `ON` mógł zmienić czas
   elastyka już po odbiorze;
3. poprawny, jeszcze nieclaimowany receipt znikał po pięciu minutach, gdy
   watcher stał albo zamówienia chwilowo nie było na tablicy;
4. niezależny pięciominutowy zegar authority tłumił starszy receipt nawet wtedy,
   gdy kolejka nadal dowodziła ważnego kliknięcia;
5. rollback do pre-v4 projektował stary `eligible_at` do scalara z TTL, więc
   legalna trwała praca mogła zniknąć zaraz po zmianie czytnika;
6. przyszły/not-ready receipt mógł być przy rollbacku przepisany jako gotowy.

V26 naprawia te przyczyny w istniejących ownerach. Jeden
`_legacy_time_claim_gate` jest wspólną bramką obu raw typów dla terminalnego
oracle i bezpośrednio przed writerem state. Brak claimu daje `superseded`;
nieczytelna kolejka daje retryable `pending`, ale handler nigdy nie zapisuje.
`time_event_cas_status` ma jeden lifecycle fence niezależnie od wersji koperty.
Exact live queue membership jest jedynym trwałym lease'em receiptu v4/v5/v6 od
enqueue do claim/ACK, dlatego policy owner nie ma już konkurencyjnego limitu
wieku. Czas nadal blokuje zdarzenia z przyszłości. `pending_with_receipts`
usuwa przez TTL wyłącznie historyczny scalar bez `request_id`; poprawne stare,
future i corrupt receipts pozostają fail-closed jako audyt. Rollback po exact
backupie rebazuje gotową trwałą pracę na czas migracji do starego czytnika, a
future receipt jawnie blokuje operację.

Negatywne oracles odtworzyły sześć klas, po fixie focused ma 15/15, a szeroki
klaster dotkniętych ścieżek 257/257. Sześć mutacji ponownie czerwieniło testy:
4F po usunięciu claim gate, 1F/1P po przywróceniu lifecycle bypassu, 3F po
przywróceniu TTL kolejki, 1F po przywróceniu TTL authority, 1F po cofnięciu
rollback rebase i 1F po usunięciu future blockera. Exact restore przywrócił
identyczne SHA-256. Pełna hermetyczna regresja: 6773 passed, 74 skipped,
8 xfailed, 153 warnings, 0 failed w 463,30 s; profil non-pass jest identyczny z
v25, a delta to +12 testów. `py_compile`, import, `git diff --check`, lifecycle
557/557 i automatyczne sentinele entropii są zielone. Produkcja, procesy i flaga
pozostały nietknięte/OFF. Późniejsze dwa review v26 odrzuciły ten kandydat;
ich findings i root fix opisuje sekcja V27 powyżej.

### Mapa kompletności v26

| Miejsce | Rola | Dotknięte | Dowód |
|---|---|---|---|
| `state_machine._legacy_time_claim_gate` i oba handlery czasu | transport authority / state writer | TAK | jeden fail-closed exact-claim gate; missing/read-error dla CK i pickup; oracle oraz writer parity |
| `committed_pickup_authority.time_event_cas_status` | policy/CAS owner | TAK | wspólny lifecycle fence dla versioned i unversioned pickup; OFF/ON post-pickup oracle |
| `committed_pickup_authority._valid_coordinator_receipt` | authority lease consumer | TAK | queue membership jest lease'em; tylko causal future-skew fence, bez drugiego TTL |
| `coordinator_time_recheck` enqueue/pending/verify/claim/ACK | trwały queue owner | TAK | v4/v5/v6 nie wygasa wiekiem; legacy scalar zachowuje TTL; stale/future/corrupt retention i exact ACK oracles |
| `coordinator_time_recheck` code rollback projection | rollback writer/gate | TAK | exact backup + rebase starej gotowej pracy; future receipt blokuje zamiast nabyć gotowość |
| watcher/apply/outbox/recovery | konsumenci exact claimu | N-D | używają wspólnej kolejki i state gate; brak drugiej polityki, schematu lub writera |
| plan/scoring/serializer/apka | konsumenci state | N-D | brak render override; dziedziczą atomowy kanoniczny pickup/CK |
| testy/ratchet/mutation | bramka antyregresji | TAK | 15 focused, 257 broad, sześć mutation kills, pełna suita 6773/6773 |

## Co domknięto w v25

Dwa blind review exact-byte v24 ponownie prawidłowo zatrzymały live. Authority
verdict ma SHA-256
`babaa445730fe40293813c85d72ea17075f25a890371c57b50f78856c0f8e79c`, a
rollout verdict
`49a279efbc4bf51b46f197f6478800f6609ccae7027bbcc1ca93557fd222e2ef`;
oba wydały `CONFIRMED_DEFECT`. MAIN niezależnie odtworzył wszystkie cztery
unikalne findings. Coordinator pickup dla rzeczywistego elastyka miał
hardcoded klasę czasówki, więc przy `OFF` legalne odświeżenie znikało, a przy
`ON` dostawało obcy authority. Legacy fallback obu pól brał CAS/politykę z
późniejszego panel ticku zamiast immutable v6 click receiptu, co po hot flipie
zmieniało wersjonowanie i otwierało ABA na crash retry. Forward rollout
odrzucał poprawny jawny receipt elastyka v6 z click-time forward `ON`, mimo że
pełny snapshot czyni go flip-independent. Standardowy i audit cleanup mogły
usunąć jedyny źródłowy outbox `NEW_ORDER` z pending initial intentem przed jego
materializacją.

V25 usuwa te przyczyny u czterech istniejących ownerów. State resolver najpierw
projektuje klasę zlecenia; coordinator receipt nie może jej zmienić, a elastyka
oddaje jedynemu legacy writerowi. W watcherze jeden
`_time_event_transaction_policy` wybiera policy ownera całej transakcji: zwykły
tick zachowuje snapshot sprzed I/O, deliberate v6 używa exact click lease przez
CK, pickup, claim, apply i replay. Pre-policy v4/v5 zachowują historyczną
semantykę i nadal muszą być zdrenowane przed flipem. Rollout akceptuje obie
wartości forward poprawnego explicit-elastic v6, lecz nadal failuje closed dla
braku lub korupcji snapshotu. Jeden wspólny SQL retention guard jest używany
przez oba cleanup writery i zwalnia źródłowy initial-intent outbox dopiero po
applied `PICKUP_TIME_UPDATED` z exact intent ID tego samego OID.

Przed fixem zestaw review miał 8 czerwonych przypadków i jeden zielony control;
po fixie 13/13 nowych/zmienionych oracles przechodzi. Cztery mutation probes
ponownie czerwieniły odpowiednio 1, 4, 1 i 1 test po usunięciu klasyfikacji,
receipt-owned CAS, poprawnego rollout classifiera i retention guarda; exact
restore przywrócił identyczne SHA-256 wszystkich czterech modułów. Pełna
kanoniczna regresja: 6761 passed, 74 skipped, 8 xfailed, 153 warnings i 0 failed
w 459,67 s. `py_compile`, import, `git diff --check` i lifecycle 557/557 są
zielone; sentinel entropy pozostaje 0 i nie doszła flaga ani próg. Produkcja,
flagi i procesy pozostają nietknięte. Następna bramka to dwa świeże review v25
na jednym zamrożonym exact-byte commicie.

### Mapa kompletności v25

| Miejsce | Rola | Dotknięte | Dowód |
|---|---|---|---|
| `state_machine.resolve_czasowka_pickup_observation` | classifier/policy owner consumer | TAK | projekcja realnej klasy przed receipt policy/claim; brak hardcoded `is_czasowka=True`; OFF i ON elastic oracles |
| `panel_watcher` CK/pickup/claim/apply/replay | producer i durable transaction consumer | TAK | jeden helper wybiera tick snapshot albo exact v6 click lease; cztery hot-flip/CAS oracles i statyczny ratchet wszystkich callsite'ów |
| rollback `forward-status` | rollout classifier | TAK | poprawny explicit-elastic v6 jest stabilny dla obu booleanów forward; malformed/pre-policy nadal blokują |
| `event_bus.cleanup` / `cleanup_audit_log` | dwa writery retencji | TAK | wspólny exact-intent release oracle; wrong intent nie zwalnia, exact applied consumer zwalnia |
| initial intent producer/state consumer | źródło i materializacja receiptu | N-D | istniejący schema/ID kontrakt wykorzystany bez drugiego writera i migracji danych |
| plan/scoring/serializer/apka | konsumenci state | N-D | brak render override; dziedziczą ten sam kanoniczny state |
| testy/ratchet/mutation | bramka antyregresji | TAK | 13/13 focused, 4 mutation kills, pełna suita 6761/6761 |

## Co domknięto w v24

Dwa blind review exact-byte v23 ponownie poprawnie zatrzymały live. Authority
verdict `58f422b07f239730ad0dd7a279238e7cddafa210384bb742fdf16091aee00cd3`
i rollout verdict
`b5443d5d1aff1569097529b446eef125c128308bde09c8ea2565f26be4085710`
wydały `CONFIRMED_DEFECT`. MAIN odtworzył trzy czerwone przypadki: coordinator
CK używał klasy starego agregatu zamiast projekcji `prep_minutes` 20→60,
forward preflight przepuszczał pre-policy receipt elastyka bez dowodu OFF, a
durable authority event nie zachowywał policy lease i po crashu wracał do live
flag. Własny audyt przed freeze wykrył czwarty oracle: ręczna flaga plus sam
claim mogły zbyt szeroko zastąpić click-time forward `OFF`.

V24 ustanawia v6 kolejki jako jedyną kopertę zdolną do authority. Snapshot
`coordinator_queue` powstaje pod tym samym flockiem co enqueue i idzie bez
reinterpretacji przez receipt, claim, proof, outbox, apply i recovery. V4/v5
są nadal czytelne dla ciemnego legacy elastyka, lecz nie mogą nabyć authority.
Claim pozostaje journalem: coordinator wymaga exact `forward AND passive`, a
manual-marker flag nie ma tu znaczenia. Durable event zapisuje pełny policy
snapshot; state recovery nie czyta live flag, a brak/korupcja/zły producer
failuje closed. Preflight ignoruje tylko poprawny v6 z własnym dowodem OFF,
natomiast code revert blokuje każdy policy-bound v6.

Trzy findings review były RED na exact v23 i są zielone po fixie. Dodatkowy
claim-oracle również przeszedł RED→GREEN. Główny klaster queue/apply/rollback
ma 272/272, a ratchet/mutation 28/28. Pełna finalna regresja ma 6752 passed,
74 skipped, 8 xfailed, 153 warnings i 0 failed w 462,43 s. Produkcja, flagi i
procesy nadal pozostają nietknięte; dwa świeże review v24 są następną bramką.

### Mapa kompletności v24

| Miejsce | Rola | Dotknięte | Dowód |
|---|---|---|---|
| `coordinator_time_recheck` | owner kolejki/click-time lease | TAK | schema v6, exact producer, continuation zachowuje lease, malformed jako poison |
| `committed_pickup_authority` | jeden policy owner | TAK | post-observation class i `coordinator=forward AND passive` |
| `committed_pickup_apply` | durable boundary | TAK | coordinator/NEW_ORDER policy z exact receiptu, snapshot w outboxie, zero live reread |
| `state_machine` | resolver/recovery/coupled writer | TAK | claim/outbox używa policy lease; OFF/missing/corrupt fail-closed |
| rollback/preflight | release gate | TAK | tylko v6 OFF elastic wyjątkiem; v4/v5 i policy-bound code revert blokują |
| flag lifecycle | mechaniczna mapa readerów | TAK | coordinator queue jest realnym symbolic consumerem, merge seed zachowuje kurację |
| plan/scoring/serializer/apka | konsumenci state | N-D | brak render override, nowego writera i zmiany HARD/SOFT |

## Co domknięto w v23

Dwa blind review v22 ponownie prawidłowo zatrzymały live. Authority verdict
`ba2e70835ee1d95fbc0caf4fde2e364b2bb1ce19d12d6233e379943f70fac12a` i
rollout verdict
`3591101bad6e5293febd86ddf1db96401742b35d54352049f7335593752e9aa8`
wydały `CONFIRMED_DEFECT`. Po deduplikacji sześć klas obejmowało późny odczyt
flagi NEW_ORDER, nieprzeniesiony panelowy snapshot, klasyfikację sprzed zmiany
prep, pozorne `--quiesced`, brak assignment snapshotu w rollback classifierze
i zbyt szeroką blokadę kolejki. MAIN odtworzył osiem czerwonych przypadków,
łącznie z enqueue między preflightem i flipem oraz brakującą mapą consumera.

V23 ustanawia jeden trwały policy lease dla obu producerów. Snapshot jest
chwytany przed mutable I/O, związany exact producer/source, zapisany w raw
evencie i czytany przez handler/oracle po crashu. Wspólny projector klasyfikuje
post-observation aggregate. Rollout ma trwały UUID+SHA forward fence pod tym
samym flockiem co wszystkie mutatory kolejki, a operator CLI reprobuje exact
unity i wiąże release z efektywnym ON albo jawnym abortem OFF.

Po fixie integracja ma 428/428, queue+rollback 111/111, ratchet 27/27 i flagi
33/33. Pierwsza pełna suita miała 6740 pass i jeden słuszny fail AST consumer
mapy; po naprawie źródłowego specu i `seed --merge` finalny wynik to 6741
passed, 74 skipped, 8 xfailed, 153 warnings, 0 failed w 465,95 s. Produkcja
pozostaje bez zmian/OFF. Dwa świeże final-byte `CLEAN` są kolejną bramką.

### Mapa kompletności v23

| Miejsce | Rola | Dotknięte | Dowód |
|---|---|---|---|
| authority/apply/state | jeden policy owner, durable transport i coupled writer | TAK | exact producer/source lease, post-observation projection, crash/hot-flip oracles |
| panel_watcher/dispatch_pipeline | dwa producenci obserwacji | TAK | capture przed I/O, ten sam obiekt/event przez resolver i apply |
| coordinator_time_recheck | jedyny owner kolejki | TAK | UUID+SHA fence blokuje każdy mutator pod jednym flockiem |
| rollback tool | release gate | TAK | exact writer reprobe, valid fence, flag-bound exact release |
| lifecycle registry | mechaniczna mapa flag | TAK | AST/spec/test/registry 557/557 po kontrolnym failu |
| plan/scoring/serializer/apka | konsumenci state | N-D | brak render override, brak konkurencyjnego writera |

## Co domknięto w v22

Dwa niezależne review final-byte v21 ponownie prawidłowo zatrzymały promocję.
Werdykt authority ma SHA-256
`9f0c69be82b500848c503157843564ec0dd52ea2548d8a35b72731eae9e885bb`,
a rollout/completeness
`bc86f8dd7c1b644e363df1cccc9cf85376cb555ead4075122a96d34815fc7ddf`;
oba wydały `CONFIRMED_DEFECT`. MAIN odtworzył trzy klasy jako cztery czerwone
oracles: restart recovery initial intentu następował dopiero po writerze
assignment/pickup; pickup writer i forward preflight klasyfikowały stary agregat
sprzed własnej zmiany `prep_minutes`; a pre-proposal ponownie czytał żywe flagi
po asynchronicznym HTTP i mógł zmienić semantykę requestu w locie.

V22 usuwa przyczyny w istniejących ownerach. Pending initial receipt odzyskuje
się bezwarunkowo zaraz po pierwszym odczycie state i przed każdym writerem
lifecycle; nieudany receipt izoluje tylko własny OID, a stary późny recovery
został usunięty. Jeden czysty `project_time_event_order` projektuje dokładnie te
same sprzężone pola, które zapisze handler, i jest wspólnym kontraktem writerów
state oraz forward preflightu. Pre-proposal tworzy jeden immutable
`CommittedPickupPolicySnapshot` przed HTTP i przekazuje ten sam obiekt przez
fetch, klasyfikację, legacy apply lub durable authority apply. OFF→ON nie nadaje
authority rozpoczętej operacji, a ON→OFF domyka już rozpoczęty, zapieczętowany
event bez ponownej interpretacji flag.

Cztery oracles review były 4/4 RED przed fixem i 4/4 PASS po nim. Dwa dodatkowe
oracles transakcji flag potwierdzają obie strony hot-flipu. Pięć kontrolowanych
mutacji — wyłączenie wczesnego recovery, obu projekcji agregatu oraz obu stron
snapshotu flag — dało właściwy FAIL; po każdym restore chroniony plik wrócił do
identycznego SHA-256. Klaster bezpośredni ma 226/226 PASS, rozszerzony klaster
outbox/queue/flag ma 456/456 PASS. Pełna kanoniczna regresja na base
`b8bf3f8d3` ma 6713 passed, 74 skipped, 8 xfailed, 153 warnings i 0 failed w
637,32 s. Compile, import i `diff --check` są zielone; lifecycle repo/live ma
557/557, hygiene 271/271 i zero sierot, effect coverage nie ma nowej luki, a
entropia pozostaje bez pogorszenia.

Read-only live preflight po naturalnej terminalizacji ma pustą kolejkę, zero
unfinished outboxa i zero aktywnych niepełnych kontraktów. Jedyną przyczyną
`safe_for_forward_deploy=false` jest teraz poprawnie wykryte działanie dwóch
writerów: `dispatch-panel-watcher.service` i `dispatch-shadow.service`.
Produkcja oraz flaga forward nadal pozostają bez zmian/OFF. Przed kontrolowanym
quiesce, deployem, restartem i flipem nadal wymagane są dwa świeże review CLEAN
na dokładnych finalnych bajtach v22.

### Mapa kompletności v22

| Miejsce | Rola | Writer / consumer | Dotknięte | Dowód |
|---|---|---|---|---|
| `panel_watcher._diff_and_emit` | recovery/lifecycle | jedyny consumer pending initial intentu | TAK | recovery przed heal/assignment/pickup/terminal; stary późny consumer usunięty |
| `committed_pickup_authority.project_time_event_order` | wspólna projekcja | policy owner pól sprzężonych | TAK | state writer i forward preflight używają jednego helpera; dwie mutation kills |
| `state_machine.PICKUP_TIME_UPDATED` | kanoniczny state writer | writer pickup+CK+prep/type | TAK | klasyfikacja post-event; elastyk 20→czasówka 60 nie tworzy split truth |
| rollback `forward-status` | bramka wydania | consumer unfinished time eventów | TAK | post-event klasyfikacja blokuje pickup promujący czasówkę |
| `dispatch_pipeline` | producent pre-proposal | owner request-scoped policy | TAK | jeden snapshot przed HTTP, ten sam obiekt w fetch/emit/apply, zero live reread |
| `committed_pickup_apply` / `state_machine` | durable i legacy apply | konsumenci snapshotu | TAK | ON→OFF finish, OFF→ON no-authority, source/type fail-closed |
| outbox/queue/flagi/checkery | bliźniaki i release | consumers/ratchets | TAK | 456/456 broad, lifecycle 557/557, hygiene 271/271, pełna suita 6713/6713 |
| plan/scoring/feasibility/serializer/apka | konsumenci state | consumers | N-D | kontrakt wejściowy bez zmiany; nadal brak render override i drugiego writera |

## Co domknięto w v21

Dwa niezależne review exact-byte v20 ponownie prawidłowo zatrzymały promocję.
MAIN odtworzył wszystkie unikalne ustalenia jako osiem czerwonych oracles:
recovery pending initial intentu zależało od obecności zlecenia na aktualnej
tablicy; self-hash można było spójnie przeliczyć bez powiązania z pierwotnym
outboxem `NEW_ORDER`; bliźniaczy legacy writer mógł zużyć pending intent;
broadcast `NEW_ORDER` dostawał sanitizowaną zamiast źródłowej koperty; rollback
nie widział receipt-bound `NEW_ORDER`; oba classifiery rolloutu przepuszczały
jawny elastyk z kanonicznym `prep_minutes>=60`; a forward gate przyjmował
deklarację quiesce bez mechanicznego sprawdzenia obu writerów.

V21 zamyka te klasy w istniejących ownerach kontraktu. Broadcast i audit
zachowują surową kopertę wejściową, a wyłącznie projekcja state jest
sanityzowana. Recovery trwałego intentu wykonuje się przed sprawdzeniem bieżącej
tablicy i nie potrzebuje ponownego fetchu restauracji. Apply wiąże intent przez
`last_lifecycle_event_id_new_order` z dokładnym, niezależnym i już zastosowanym
rekordem outbox `NEW_ORDER`; brak lub podmiana receipt failuje closed. Dopóki
initial intent jest pending, state przyjmuje wyłącznie odpowiadający mu event
authority, a receipt jest czyszczony tylko przez ten exact writer. Oba
classifiery używają kanonicznego `is_czasowka`, więc etykieta elastyka nie może
przykryć `prep_minutes>=60`. Rollback rezerwuje klasę receipt-bound
`NEW_ORDER`, a `forward-status` schema v4 wymaga `--quiesced` i odczytuje z
systemd dokładnie `dispatch-panel-watcher.service` oraz
`dispatch-shadow.service`; oba muszą być loaded i inactive.

Negatywny zestaw był 8/8 RED przed zmianą i 8/8 PASS po niej. Osiem niezależnych
mutacji odwracających kolejno receipt binding, recovery przed tablicą, surowy
broadcast, sibling-writer guard, oba classifiery `prep_minutes>=60`, rollback
receipt-bound `NEW_ORDER` i mechaniczne quiesce ponownie czerwieniło właściwe
oracles. Po exact restore pięć głównych modułów wróciło do zapisanych SHA-256.
Szeroki klaster siedmiu dotkniętych plików testowych ma 367/367 PASS. Pełna
hermetyczna regresja na zintegrowanym base `b8bf3f8d3` ma 6706 passed,
74 skipped, 8 xfailed, 153 warnings i 0 failed w 479,28 s. Pierwszy przebieg
bez `ZIOMEK_SCRIPTS_ROOT`/`PYTHONPATH` został jawnie odrzucony jako błędna
konfiguracja (6703 pass, 3 fail przez skan produkcyjnego drzewa); te same trzy
nodeidy na poprawnym środowisku mają 3/3 PASS i pełny poprawny przebieg jest
zielony. Produkcja nie została zmieniona, flaga pozostała OFF, a dwa review
final-byte v21 następnie wykryły trzy klasy domknięte wyżej w v22.

### Mapa kompletności v21

| Miejsce | Rola | Writer / consumer | Dotknięte | Dowód |
|---|---|---|---|---|
| `panel_watcher._emit_and_apply_state` | ingest/audit/state projection | writer raw broadcast i sanitized state | TAK | oracle surowej koperty oraz mutation raw→sanitized |
| `panel_watcher._diff_and_emit` | restart/recovery | consumer pending initial intentu | TAK | recovery przed board/fetch, przypadek zlecenia nieobecnego na tablicy |
| durable outbox `NEW_ORDER` + `committed_pickup_apply` | niezależny receipt i granica apply | writer/consumer identity | TAK | exact event-id, typ, snapshot, intent i `state_status=applied` |
| `state_machine.NEW_ORDER` / `PICKUP_TIME_UPDATED` | shell i jedyny committed writer | writer/consumer pending intentu | TAK | sibling writer odrzucony, exact authority atomowo konsumuje receipt |
| `committed_pickup_authority` + rollback tool | klasyfikacja i bramki forward/revert | policy/consumer outboxa | TAK | kanoniczne `is_czasowka`, receipt-bound `NEW_ORDER`, schema v4 |
| panel-watcher i shadow systemd | producenci obserwacji | writerzy runtime | TAK | `--quiesced` wymaga loaded+inactive obu exact unitów |
| plan/scoring/feasibility/serializer/apka | konsumenci kanonicznego state | consumers | N-D | kontrakt wejściowy bez zmiany; brak nowego writera i render override |
| flaga/rejestr/checkery/ratchet | rollout i antyregresja | owner/verify | TAK | lifecycle 557/557, hygiene 271/271, zero nowego driftu |

## Co domknięto w v20

Dwa niezależne review exact-byte v19 poprawnie zatrzymały live. Authority
verdict ma SHA
`7ffd3b6493cd62c5e848e96e8e57e2bd190bb8b764db84b304bce6fe63563b15`,
a completeness verdict
`11cf4fe79f3ebe83a02167c3eb74133d19ae171297d097b44e47aea12b524a20`;
oba wydały `CONFIRMED_DEFECT`. MAIN odtworzył cztery klasy jako 4/4 RED:

- `NEW_ORDER` zamrażał ON, lecz initializer ponownie czytał live flagę, więc
  ON→OFF mógł przywrócić surowy CK zamiast dokończyć rozpoczętą decyzję;
- pierwotny initial tuple znikał między durable shellem a inicjalizatorem, więc
  po crashu świeży statusowy restamp 19:16 mógł zostać uznany za nowy kanon;
- forward preflight nie blokował pending legacy `PICKUP_TIME_UPDATED` czasówki;
- sanitizowany czasowy `NEW_ORDER` ze snapshotem ON był fałszywie uznawany za
  gotowy do flipu, mimo że nie miał jeszcze materializowanego kontraktu.

V20 usuwa wspólną przyczynę: brak trwałej tożsamości initial decyzji. Przed
sanityzacją watcher buduje immutable `committed_pickup.new_order_intent.v1`,
wiąże go SHA-256 z OID, pełnym pierwotnym tuple, statusem i czasem obserwacji i
utrwala w tej samej transakcji co shell `NEW_ORDER`. Jeden czysty resolver nie
czyta flag runtime i z tego receiptu zwraca najwyżej jeden kanoniczny
`PICKUP_TIME_UPDATED`. Exact receipt autoryzuje dokończenie wyłącznie tej samej
rozpoczętej transakcji po ON→OFF; nie otwiera nowych obserwacji. State zapisuje
pickup+CK+HH:MM+provenance i usuwa pending intent atomowo. Restart tick najpierw
odzyskuje receipt, a dopiero potem ocenia świeży panel, więc restamp 19:16 nie
zastępuje umówionego 19:21. Forward fence obejmuje wszystkie pending CK/pickup
writery i każdy czasowy `NEW_ORDER`; code rollback pozostaje fail-closed.

Po fixie cztery oracles są zielone. Tamper 19:21 bez przeliczenia hash daje
`invalid_new_order_time_intent`, a realny restart tick przy obu flagach legacy
OFF odzyskuje pierwotne 19:21 mimo świeżego 19:16. Sześć celowych mutacji
czerwieniło dokładnie właściwy test; po exact restore zestaw ma 7/7 PASS, a
szeroki klaster dotkniętych warstw 592/592 PASS. Pełna regresja i dwa świeże
review final-byte v20 pozostają ostatnią bramką kodową przed operacją live.
Pierwsza pełna hermetyczna regresja v20 ma 6617 passed, 74 skipped, 8 xfailed,
149 warnings i 0 failed w 594,91 s; profil skip/xfail/warnings jest identyczny
z v19, a delta wynosi +7 testów.

## Co domknięto w v19

Dwa świeże review exact-byte v18 poprawnie zatrzymały live. MAIN odtworzył
wszystkie findings na zamrożonym commicie `46f221e94`: initialny `NEW_ORDER`
i cold start nadal potrafiły utrwalić pickup oraz CK jako dwie prawdy, a
statusowy re-stamp CK mógł zostawić inicjalizację bez legalnego domknięcia;
forward preflight uznawał każdy niepusty tuple za kompletny, więc przepuszczał
rozjazd lub malformed ISO/HH:MM; bezkontekstowy raw-CK classifier fałszywie
blokował jawny event elastyka; po legalnym prune w pełni wersjonowany, jeszcze
nieclaimowany event czasu pozostawał `pending` na zawsze.

V19 usuwa przyczyny w ownerach ingestu, kontraktu, preflightu i durable oracle.
`NEW_ORDER` niesie trwały snapshot polityki; przy ON tworzy tylko aggregate
shell bez surowych pól czasu, po czym CK i pickup przechodzą przez jeden
resolver i jeden `PICKUP_TIME_UPDATED` writer. Ten sam forward flag wymusza oba
detektory recovery nawet przy starych kill-switchach OFF, dlatego crash między
shell a inicjalizatorem domyka się na kolejnym ticku. OFF zachowuje dokładny
legacy tuple. Jeden walidator pełnego kontraktu sprawdza aware ISO, projekcję
HH:MM, zgodność pickup↔CK i kompletną tożsamość provenance. Forward gate
rozróżnia kontekstowo tylko w pełni związany, poprawny raw CK jawnego elastyka;
code revert pozostaje konserwatywny dla całej klasy raw CK. Osobna bramka
blokuje niedomknięty pre-v19 czasowy `NEW_ORDER`. Wspólny oracle terminalizuje
oba wersjonowane typy eventów czasu po usunięciu agregatu.

Siedem celowych mutacji zostało zabitych przez właściwe oracles: wyłączenie
sanityzacji, inicjalizatora, walidacji split tuple, kontekstowego forward gate,
bramki starego `NEW_ORDER`, terminalizacji po prune oraz recovery obu
detektorów dało odpowiednio 1F/1F/1F/1F/1F/2F/1F+1P. Po każdym restore cztery
moduły wróciły do exact SHA-256; zestaw mutacyjny ma 30/30 PASS, a rozszerzony
klaster 432/432 PASS. Pełna kanoniczna regresja `HERMETIC_STRICT=1` ma
6610 passed, 74 skipped, 8 xfailed, 149 warnings i 0 failed w 466,95 s — profil
skip/xfail/warnings identyczny z v18, delta +11 testów.

Read-only `forward-status` o 2026-08-02T16:31Z wykazał pustą kolejkę i zero
unfinished outboxa, ale dwie aktywne legacy czasówki z rozbieżnym pickup/CK,
więc prawidłowo zwrócił `safe_for_forward_deploy=false`. Nie wykonano migracji,
flipu, restartu ani deployu. Live pozostaje na HOLD do naturalnej terminalizacji
tych rekordów, ponownego preflightu po quiesce i dwóch świeżych `CLEAN` v19.

## Co domknięto w v18

Dwa świeże review exact-byte v17 poprawnie wydały `CONFIRMED_DEFECT`, więc
v17 nie została wdrożona. MAIN niezależnie potwierdził cztery przyczyny:

- `forward-status` policzył unfinished authority/raw-CK outbox, lecz nie
  włączył tego wyniku do `safe_for_forward_deploy`; flip mógł zmienić
  terminalność już utrwalonej pracy;
- nowy klik koordynatora za claimed headem nie weryfikował exact claimu ani
  istniejącego successora i mógł nadpisać zachowane poison evidence;
- preflight aktywnego stanu duplikował klasyfikację czasówki i pomijał legacy
  `prep_minutes>=60` bez `order_type` i kuriera;
- nowa czasówka utworzona przed pojawieniem się czasu Rutcom nie miała
  legalnej krawędzi z `pickup=None`: oba równoległe pola były tłumione, więc
  pełny kontrakt nie mógł się sam domknąć po włączeniu flagi.

V18 usuwa te przyczyny u istniejących ownerów. Forward gate wymaga zera całej
kanonicznej klasy unfinished authority rows. Kolejka przed zapisem weryfikuje
exact claimed head i successor tym samym oraclem co recovery/ACK, pozostawiając
korupcję bajtowo bez zmian. Preflight deleguje do
`common.is_czasowka_order`. Policy owner traktuje wyłącznie rzeczywisty
`None` jako legalny causal baseline pierwszego snapshotu; niepusty wadliwy czas
nadal failuje closed. Pierwszy pełny tuple Rutcom jest w jednym ticku
kanonizowany do proof-bound `PICKUP_TIME_UPDATED`, a zapamiętany równoległy
pickup baseline nie może odwrócić wyniku.

Negatywne oracles obu review dały przed fixami łącznie 6 FAIL i jeden wymagany
PASS kontroli false-positive; po fixach wspólny zestaw ma 7/7 PASS. Sześć
kontrolowanych mutacji — usunięcie authority-row gate, walidacji claimu,
walidacji successora, kanonicznego klasyfikatora oraz obu legalnych krawędzi
pierwszego snapshotu — ponownie czerwieni po jednym właściwym teście. Po
exact-byte restore klaster dotkniętych warstw ma 376/376 PASS. Pełna
hermetyczna regresja na aktualnym masterze `49aed3215` ma 6599 passed,
74 skipped, 8 xfailed, 149 warnings i 0 failed w 452,70 s. Produkcja i flaga
pozostają bez zmian; przed live wymagane są dwa całkowicie świeże `CLEAN` na
zamrożonych bajtach v18.

## Co domknięto w v17

Dwa świeże review exact-byte v16 poprawnie wydały `CONFIRMED_DEFECT`, więc
v16 nie została wdrożona. MAIN niezależnie odtworzył wszystkie ustalenia jako
sześć czerwonych przypadków należących do trzech klas przyczyn:

- projekcja v5→legacy przy rollbacku brała niezmienny czas kliknięcia
  `requested_at`, a nie epokę wykonania `eligible_at`; świeżo promowany
  successor mógł przez to wygasnąć natychmiast po cofnięciu kodu;
- pickup `null→wartość` omijał authority, a cold start bez lokalnego rekordu
  potrafił zapisać cienki `COURIER_ASSIGNED` bez wcześniejszego pełnego
  `NEW_ORDER`; traciły się klasa czasówki i sprzężone pola pickup/CK;
- `forward-status` nie widział unfinished pre-v16 assignmentów z CK bez
  trwałych snapshotów polityki ani aktywnego, niepełnego kontraktu czasówki.

V17 usuwa te przyczyny u ownerów kolejki, ingestu i bramki wydania. Rollback
projektuje `eligible_at`, zachowując `requested_at` wyłącznie jako immutable
audit. Każdy nowy pickup, także `null→wartość`, przechodzi przez ten sam
resolver. Cold start bez state najpierw buduje i trwale aplikuje pełny
`NEW_ORDER`, sprawdza `state_ready`, a dopiero potem emituje assignment.
Preflight schema v3 failuje closed na obu klasach starego długu, ale jawne
oracles wykluczają fałszywe blokady dla pełnej czasówki, terminalnego rekordu,
elastyka i paczki.

Sześć reprodukcji było czerwonych przed fixem i ma 6/6 PASS po nim. Pięć
kontrolowanych mutacji odwracających kolejno epokę rollbacku, pickup ownera,
cold-start init oraz oba preflight classifiery dało 2F/2F/2F/1F/1F; po
exact-byte restore wspólny zestaw ma 8/8 PASS. Szeroki klaster miał 528 PASS i
jeden oczekiwany fail ratchetu po dodaniu dwóch legalnych consumerów; po
niezależnym sprawdzeniu dokładnych lokalizacji semantyczny pin został
zaktualizowany, a pełny ratchet ma 22/22 PASS. Pełna hermetyczna regresja v17:
6576 passed, 74 skipped, 8 xfailed, 149 warnings, 0 failed w 450,57 s.
Produkcja i flaga pozostają bez zmian; przed live wymagane są dwa świeże
`CLEAN` na zamrożonych bajtach v17.

## Co domknięto w v16

Dwa świeże review exact-byte v15 poprawnie wydały `CONFIRMED_DEFECT`, dlatego
v15 nie została wdrożona. MAIN niezależnie odtworzył cztery przyczyny jako
dokładnie cztery czerwone oracles przed zmianą:

- handler `COURIER_ASSIGNED` wygaszał równoległy CK przy nowym authority, ale
  terminalny oracle nadal wymagał jego zapisu; pierwsza durable próba zostawała
  `pending` mimo poprawnie zapisanego assignmentu;
- `null→wartość` w watcherze miało osobny early return i omijało kanoniczny
  resolver oraz queue-bound receipt koordynatora;
- successor zaparkowany za nieprzeterminowującym się claimem zużywał swój
  pięciominutowy TTL podczas oczekiwania i znikał natychmiast po promocji;
- legacy czasówka rozpoznana tylko przez `prep_minutes>=60` mogła w tym samym
  committed zapisie dostać niższy prep, utracić tożsamość i ponownie otworzyć
  stare CK-only writery.

V16 usuwa te przyczyny w ownerach kontraktu. Jeden czysty resolver rozstrzyga
CK niesiony przez assignment, a durable event zamraża dokładne booleany obu
flag dla handlera i postcondition, więc hot flip nie rozcina jednej próby.
Watcher kieruje zarówno pierwszy snapshot CK, jak i kolejne zmiany przez ten
sam resolver; legalny pełny `null/null` baseline jest jawnie dozwolony, lecz
każda częściowa para nadal failuje closed. `order_type=czasowka` weszło do
kanonicznej mapy pól sprzężonych, więc proof, CAS, writer i postcondition
materializują tożsamość atomowo z pickup+CK. Receipt v5 rozdziela niezmienny
`requested_at` od `eligible_at`; v4 pozostaje czytalne, a successor dostaje
nową epokę wykonania dopiero po exact ACK poprzednika. Odwrócony zegar lub
niepełna koperta pozostają jako poison evidence.

Niezależny przegląd MAIN wykrył jeszcze lukę parytetu: pierwszy automatyczny CK
przy fladze OFF po przejściu przez resolver był tłumiony. Kanoniczny resolver
zwraca teraz `NOT_APPLICABLE` wyłącznie dla source `first_acceptance` i flagi
OFF, co oddaje zapis dokładnie staremu writerowi; ten sam response przy ON jest
atomowym authority eventem. Bez warunku w watcherze ani state.

Rozszerzony klaster incident/queue/ratchet/outbox/flag ma 399/399 PASS.
Pięć rzeczywistych mutation probes dało kolejno 1F, 2F, 2F, 1F i 2F po usunięciu
oracle assignmentu, tożsamości czasówki, legalnego null baseline i nowej epoki
successora oraz po zamianie OFF handoffu na suppression. Po restore SHA
chronionych plików wróciły bajt w bajt, a exact zestaw mutacyjny ma 8/8 PASS.
Pełna hermetyczna regresja v16: 6561 passed, 74 skipped, 8 xfailed,
149 warnings, 0 failed w 433,93 s. Produkcja pozostaje nietknięta; przed live
wymagane są dwa nowe `CLEAN` na zamrożonych bajtach v16.

## Co domknięto w v15

Dwa hash-bound review v14 wydały `CONFIRMED_DEFECT`, więc v14 nie została
wdrożona. MAIN niezależnie odtworzył wszystkie ustalenia jako czerwone oracles:

- exact claim wiązał wartość CK, lecz nie monotoniczną generację CK, więc cykl
  A→C→A po hot `OFF` pozwalał staremu claimowi wrócić;
- CAS i postcondition nie obejmowały `decision_deadline` oraz
  `zmiana_czasu_odbioru`, a postcondition także `prep_minutes`, mimo atomowego
  zapisu tych pól;
- legalny `first_acceptance` bez baseline miał `delta_min=None` i wywracał
  generator durable key;
- częściowa koperta CK mogła utracić schema/status/revision, zachować
  courier/assignment identity i spaść do legacy;
- malformed unclaimed receipt lub orphan successor znikał przy skanie TTL;
- literalny `decision_flag("...")` omijał symbolic consumer ratchet;
- dark deploy nie miał mechanicznej bramki dla niedomkniętych raw
  `coordinator_force` eventów sprzed v4.

V15 usuwa przyczyny u wspólnych ownerów. Committed event wiąże pickup i CK
revision; jedna mapa pól sprzężonych zasila proof, CAS, payload, state writer i
exact postcondition; `None` ma osobną stabilną domenę klucza; każdy zachowany
ślad CK identity rezerwuje kopertę. Kolejka zatrzymuje poison evidence i nie
pozwala go użyć, ACK-nąć ani nadpisać. Scanner rozpoznaje symbol, alias oraz
dokładny literal. Forward deploy po quiesce wymaga `forward-status` z flagą OFF,
pustą kolejką i zerem starych raw eventów — bez runtime fallbacku.

Nowe oracles przed fixem dały 12/12 fail, po fixie 12/12 pass; pełny klaster
czterech kontraktów ma 199 pass. Kontrolowana wielomutacja sześciu ownerów dała
12 fail i 2 pass; po przywróceniu exact SHA klaster wrócił do 14/14. Pełna
hermetyczna regresja final-byte: 6544 passed, 74 skipped, 8 xfailed,
149 warnings, 0 failed w 452,38 s. Produkcja pozostaje nietknięta; v15 czeka na
dwa całkowicie świeże `CLEAN` exact-byte.

## Co domknięto w v14

Dwa świeże, hash-bound review v13 wydały `CONFIRMED_DEFECT`; v13 nie została
wdrożona. Dziewięć unikalnych klas zostało odtworzonych i zamkniętych u
wspólnych ownerów, bez kolejnego fallbacku:

- `time_update_cas.v1` wiąże status, kuriera, assignment generation oraz
  monotoniczną rewizję każdego nowego zwykłego eventu CK/pickup; ten sam helper
  zasila watcher i pre-proposal, ten sam oracle handler i durable retry, a key
  wiąże dokładną generację;
- częściowa lub uszkodzona koperta CAS jest zarezerwowana presence-based i
  odrzucana przed outboxem, zamiast spaść do legacy;
- historyczny pickup v13 jest podnoszony do wspólnego CAS, a stary exact claim
  CK dostaje konserwatywny old-value fence; stale generation i ABA są
  terminalnie `superseded`;
- granica raw CK czyta state strict przed kanonizacją, więc przejściowy błąd
  odczytu nie utrwali legalnej intencji jako niekanonicznego raw eventu;
- jeden helper opisuje emeryturę raw CK writerów zarówno dla handlera, jak i
  postcondition, więc żaden event odrzucany przez handler nie zostaje wiecznie
  `pending`;
- obecny klucz attestation jest walidowany również dla `null`, a czyszczenie
  provenance używa pełnego artifact oracle zamiast jednego głównego pola;
- projection+fence rollbacku jest jedną transakcją z exact-byte restore;
  cleanup usuwa wyłącznie własny fence, nigdy artefakt utworzony w wyścigu;
- lifecycle scanner rozpoznaje import/module/local aliases i named arguments,
  a ratchet writerów/producerów rozpoznaje statyczne `join`.

Nowe oracles są zielone, szeroki klaster dotkniętych ścieżek dał 350 pass, a
ratchet po świadomym odświeżeniu jednego zmienionego kontraktu 20/20. Cztery
kontrolowane mutacje (CAS downgrade, CK ABA revision, obcy fence i alias/static
join) czerwieniły właściwe testy; pliki wróciły do exact SHA-256. Pełna
hermetyczna regresja v14: 6527 passed, 74 skipped, 8 xfailed, 149 warnings,
0 failed w 429,50 s. Produkcja nadal pozostaje nietknięta; dwa świeże `CLEAN`
exact final-byte są ostatnią bramką przed commitem i live.

## Co domknięto w v13

Dwa niezależne review v12 wydały `CONFIRMED_DEFECT`, więc v12 nie została
wdrożona. MAIN odtworzył wszystkie osiem ustaleń jako czerwone oracles na
dokładnym artefakcie v12, a następnie usunął przyczyny we wspólnych ownerach:

- niezmieniony sprzeczny response nie oscyluje już między pickup i CK;
- każdy pickup event, także claimed legacy, wiąże stary CK, stary pickup,
  courier, assignment generation i pickup revision przez CAS;
- `event_bus` jest jednym ownerem dozwolonych par terminalnych; apply, recovery,
  ACK kolejki i rollback używają tej samej definicji;
- jeden klik koordynatora ma skończoną głębokość continuation i może obsłużyć
  najwyżej dwa istniejące pola czasu, nigdy tworzyć nieskończonego łańcucha;
- claim legacy po legalnym prune/missing OID kończy się terminalnym
  `superseded`, zamiast wisieć bez końca;
- aktywne provenance w state blokuje code revert fail-closed, a po hot `OFF`
  nieautoryzowany raw CK nadal nie może rozciąć sprzężonego pickup+CK;
- rollback tool jest jawnym symbolicznym consumerem obu authority flags, a
  nie ukrytym aliasem poza lifecycle registry.

Pełna hermetyczna regresja v13: 6510 passed, 74 skipped, 8 xfailed,
149 warnings, 0 failed w 405,67 s. Błędny bieg bez worktree env został jawnie
unieważniony: checker mieszał kod kandydata z produkcyjnym katalogiem testów.
Powtórzenie z `ZIOMEK_SCRIPTS_ROOT`, `PYTHONPATH` i `HERMETIC_STRICT=1` jest
zielone. Produkcja nadal pozostaje nietknięta; dwa świeże `CLEAN` exact-byte są
ostatnią bramką przed commitem i live.

## Co domknięto w v12

Dwa świeże blind review v11 poprawnie zatrzymały wdrożenie. MAIN niezależnie
odtworzył ich ustalenia i poprawił kontrakt u jego ownerów:

- ogólny receipt „odśwież” nie może już przywrócić zapamiętanego stale
  `pickup_at=19:16`, gdy CK-derived commitment wynosi 19:21; receipt potwierdza
  odczyt, ale nie rozstrzyga sprzecznych pól Rutcom;
- każdy wymuszony legacy/elastyk event czasu dostaje exact durable claim przed
  side effectem. Pending downstream zostawia claim do replay, a po terminalnym
  ACK świeża continuation obsługuje drugie równoległe pole osobnym eventem;
- świeży historyczny `oid→timestamp` jest podnoszony do v4 ze źródłem, które
  nie może autoryzować committed czasu czasówki; częściowy event z samym
  committed key nie może zapisać poison claimu;
- lifecycle registry przypina ten sam rzeczywisty zbiór trzech aliasowych
  readerów dla flag forward i manual;
- `coordinator_edit`, `first_acceptance` i `ziomek_late_extension` są jawnie
  wygaszone jako CK-only sources czasówki przy ON. Dwa zewnętrzne źródła nie
  mają producenta na HEAD; `first_acceptance` pozostaje dla elastyka/OFF.

Konserwatywny code-revert blocker dla każdego unfinished raw CK pozostaje
celowy: rollback bez orders_state nie potrafi dowieść, że row jest elastykiem.
To świadomy koszt dostępności code revertu, nie false authority; hot rollback
nowej funkcji nadal polega na ustawieniu flagi `false`.

Negatywne oracles v12 przed zmianą dały trzy potwierdzone fail oraz brak
symbolicznej mapy manual flag. Osiem nowych mutation probes czerwieni po
odwróceniu każdej ochrony i wraca do identycznych SHA źródeł. Pierwsza pełna
suita v12 dała 6489 pass i jeden kontrolny fail hash-ratchetu: dokładna mapa
potwierdziła po jednym nowym, legalnym użyciu typów eventu w classifierze
kolejki. Hash został zaktualizowany dopiero po sprawdzeniu obu lokalizacji.
Finalna pełna regresja ma 6493 passed, 74 skipped, 8 xfailed, 149 warnings,
0 failed w 397,19 s. Profil skip/xfail/warnings jest identyczny z base
`e23592b02`; delta testów kandydata wynosi +144. Dwa całkowicie świeże `CLEAN`
exact final-byte pozostają ostatnią bramką live.

## Co domknięto w v11

Dwa świeże blind review v10 wydały `CONFIRMED_DEFECT`, więc v10 nie została
wdrożona. MAIN odtworzył wszystkie findings i dodatkowo znalazł utratę
tożsamości durable rowa przy częściowo wyczyszczonym `state_event`. V11 domyka
jedną granicę zamiast dodawać wyjątki:

- schemat authority jest rezerwowany po obecności klucza, nawet gdy wartość to
  `null`; obejmuje revision/baseline, provenance i exact attestation. Ogólne
  markery wykonania downstream są objęte sealem, ale nie nadają authority
  zwykłemu legacy pickupowi;
- marker kanonicznego committed eventu w `event_id`, `event_id_hint` albo
  outbox `event_key` zachowuje semantykę po utracie payloadu;
- rollback klasyfikuje pełny row outboxa i failuje closed dla pustego JSON,
  braku bindingu event/OID, mismatchu i raw CK; release fence czyta całą
  kanoniczną listę authority flags;
- `coordinator_force` bez receiptu jest rezerwowany dopiero po potwierdzeniu
  klasy czasówki, więc prawidłowy deliberate pickup elastyka nadal się stosuje;
- lifecycle registry mapuje trzy faktyczne aliasowe readery flagi. AST wymaga
  dokładnej równości zbioru, więc zarówno brakujący, jak i ukryty czwarty
  consumer zatrzymuje re-seed.

Negatywny klaster przed fixem miał 17 fail oraz błąd importu nowego row oracle.
Pierwsza pełna suita v11 znalazła jeszcze trzy regresje wspólnej przyczyny:
ogólny marker wykonania downstream był błędnie uznany za dowód authority.
Klasyfikator zawężono u źródła, dodano negatywny oracle i mutation probe.
Finalny szeroki klaster ma 336/336, pełna regresja 6482/6482, a jedenaście
mutacji zostało zabitych z bajtowym powrotem źródeł. Dwa całkowicie świeże
blind review v11 pozostawały obowiązkową bramką i poprawnie zatrzymały live.

## Co domknięto w v10

Dwa świeże blind review v9 poprawnie zatrzymały promocję. Sześć luk zostało
niezależnie odtworzonych i zamkniętych wspólnymi ownerami kontraktu:

- rollback kodu czyta jedną kanoniczną listę wszystkich flag zdolnych tworzyć
  authority event i nie dopuści revertu, gdy manual albo forward writer działa;
- jeden generic artifact oracle rezerwuje proof, key, receipt, source,
  provenance i attestation, więc stripped event nie degraduje się do legacy;
- przy authority ON oba stare CK-only writery — assignment i first_acceptance —
  są wygaszone także przy pustym CK; assignment nadal zapisuje kuriera;
- durable bridge zamraża wszystkie markery downstream, potem sealer hashuje
  pełną trwałą kopertę, a dopiero potem zapisuje outbox;
- ratchet przypina kanoniczne flagi, generic artifact guard i kolejność sealera.

Mutation pomijająca sealer ujawniła dodatkowo słabość samego oracle: brak
attestation mógł wyglądać jak prawidłowo odrzucona attestation. Test wymaga
teraz najpierw poprawnej pristine koperty, więc celowe usunięcie sealera także
czerwieni.

## Co domknięto w v9

Dwa świeże blind review v8 poprawnie zatrzymały promocję. MAIN odtworzył pięć
luk i poprawił je u źródła:

- exact claim jest odtwarzany przez pełny tick watchera przed iteracją po
  `current_state`; legalny prune OID nie więzi już headu ani successora;
- proof wiąże obserwowany old CK jako parę ISO+HH:MM z aktualnym state.
  Pre-proposal buduje ten baseline z bieżącego state, nie ze starego worka;
- przy authority ON istniejącego CK czasówki nie może nadpisać drugi raw
  CK-only writer. Pusty realny `first_acceptance` i pełna ścieżka OFF zachowują
  legacy parytet;
- rollback audit zwraca każdy układ outboxa poza jawnie terminalnym. Brak lub
  uszkodzony state_event oraz częściowy authority source są blockerem;
- writer ratchet rozpoznaje `dict(committed_pickup_authority=...)`, a drugi,
  niezależny semantic counter liczy również `ast.keyword.arg`.

Jedna część review została niezależnie skorygowana: malformed JSON nie znikał
z samej listy — wracał jako `state_event=None` — ale classifier uznawał go za
bezpieczny. Skutek biznesowy review (fałszywe prawo do code rollbacku) był więc
realny, choć dokładne miejsce drugiej połowy przyczyny było inne.

## Odtworzony proces incydentu

1. OID 491578 miał lokalny pickup/CK `19:15:58`, prezentowany jako 19:16.
2. O 18:50:24 nastąpiło przypisanie kuriera.
3. Najpóźniej o 18:50:57 Rutcom zwracał CK 19:21 przy aktywnym statusie 2.
4. `panel_re_check` i `pre_proposal_recheck` widziały 19:16→19:21, ale guard
   znał wyłącznie „marker ręczny” albo „pasywny re-stamp” i emitował
   `CK_PASSIVE_SUPPRESSED`.
5. Do 19:06:56 powstały 52 suppression: 42 watcher i 10 pre-proposal.
6. Nie powstał `PICKUP_TIME_UPDATED`, więc API i Android poprawnie odczytały
   stary stan 19:16. Rany Julek 19:26 szedł odrębną ścieżką elastyka.

Z danych wynika, że Rutcom miał 19:21 w ciągu 33 sekund od przypisania. Nie ma
audytu interfejsu pozwalającego uczciwie wskazać dokładną akcję człowieka, więc
naprawa nie opiera się na takim domyśle.

## Co domknięto w v8

Dwa świeże blind review v7 niezależnie zatrzymały promocję. Wszystkie findings
zostały odtworzone jako dziewięć czerwonych oracle'ów przed zmianą. V8 domyka
kontrakt transakcyjny, rollback i ratchet u źródła:

- exact claim, który przegra revision/assignment CAS po zclaimowaniu, trafia do
  outboxa i kończy się `superseded`; nie nadpisuje nowszego writera, nie więzi
  headu, a coalesced successor jest promowany po exact ACK;
- claim po długim crashu i legalnym prune terminalnego zlecenia kończy się tak
  samo; strict-read failure nadal pozostaje retryable `pending`;
- ACK opiera się wyłącznie na dokładnym rekordzie SQLite: superseded albo
  state applied + downstream applied. Samo `state_ready` nie kasuje kliknięcia
  po awarii callbacku;
- rollback fence jest ostatnim commitem prepare i zawiera ścieżkę oraz SHA-256
  exact backupu i projekcji. Status rewaliduje oba pliki; sam marker, uszkodzony
  backup albo błąd backupu nigdy nie daje `safe_for_code_revert`;
- rollback classifier failuje closed dla każdego unfinished raw CK, top-level
  authority attestation i uszkodzonego state_event;
- AST ratchet rozwiązuje teraz aliasy, konkatenacje i statyczne f-stringi, więc
  drugi writer/producer nie może ukryć chronionego symbolu prostą składnią.

## Co domknięto w v7

Oba świeże blind review v6 wydały `CONFIRMED_DEFECT`, więc mimo zielonej pełnej
suity promocja poprawnie pozostała na `HOLD`. MAIN niezależnie odtworzył pięć
unikalnych luk. V7 zamyka wspólny kontrakt trwałości i rollbacku:

- claim kolejki jest niezmiennym headem; ponowny klik tworzy coalesced
  successora, a exact ACK poprzednika atomowo go promuje zamiast kasować;
- kompatybilne `drain()`/`drain_with_receipts()` nie widzą claimed transakcji,
  więc nie mogą jej ACK-ować bez zastosowania exact eventu;
- jeden reserved-source oracle sprawdza `source` i `observed_source`, dlatego
  zdjęcie authority/proofu z normalized coordinator pickup nie degraduje go do
  legacy writera;
- OFF legacy pickup zachowuje dokładny event key z base; wewnętrzna revision nie
  dopisuje pola `null` do historycznego digestu;
- rollback kodu ma pełny, nielimitowany audit unfinished outboxa, trwały fence
  kolejki, exact backup 0600, fail-closed konwersję v4→legacy oraz kontrolowany
  release fence po roll-forward. Claim, corrupt rekord lub dowolny unfinished
  authority row blokuje revert; podstawowym rollbackiem pozostaje hot OFF.

## Co domknięto w v6

Oba blind review v5 wydały `CONFIRMED_DEFECT`, więc promocja ponownie pozostała
na `HOLD`. Wskazały sześć unikalnych defektów; MAIN niezależnie wykrył siódmy.
V6 zamyka je u źródła:

- wszystkie realne producery kanonizują CK przed outboxem; historyczny durable
  raw CK jest terminalnie superseded i nie tworzy drugiego transportu;
- revision fence należy do kanonicznego pickup, więc każdy legalny writer
  authority lub legacy przesuwa go i zamyka ABA także przed pierwszym apply;
- `coordinator_force` jest źródłem zarezerwowanym: brak receiptu, proofu albo
  flaga `OFF` nie może spaść do legacy fallbacku ani wejść bezpośrednio w state;
- exact outbox jest wznawiany przed rewalidacją starego snapshotu, dzięki czemu
  crash po state apply, ale przed ACK, domyka dokładnie ten sam event;
- exact claim przechodzi granicę claim→outbox również po wyłączeniu passive
  guarda; rollback nadal blokuje każdą nową decyzję;
- ratchet rozwiązuje aliasy stringów użyte przez `ast.Name` i dodatkowo przypina
  liczność semantycznych literałów, więc drugi writer/producer nie ukryje się
  za stałą.

## Co domknięto wcześniej w v5

Oba blind review v4 wydały `CONFIRMED_DEFECT`, więc promocja pozostała na
`HOLD`. Zgłosiły łącznie dziesięć findings: osiem unikalnych luk kodowych oraz
tę samą sprzeczność dokumentacji recovery. Wszystkie zostały zweryfikowane w
kodzie i domknięte w v5.

V5 dodatkowo:

- odtwarza exact claimed event przed nowym fetch/diff i ACK-uje go dopiero po
  terminalnym durable wyniku, więc brak nowej delty nie gubi intencji;
- wiąże proof z rzeczywistą klasą czasówki oraz dokładnym dozwolonym zbiorem pól
  koperty, bez możliwości dopisania aliasu lifecycle;
- wymaga queue-bound receiptu dla każdej korekty `coordinator_force`;
- przy nowej fladze `OFF` zostawia zwykły Rutcom pickup na legacy path, nawet
  gdy istniejąca flaga ręcznego markera jest `ON`;
- przy obu authority flags `OFF` pre-proposal nie czyta nowego state;
- stosuje jeden próg szumu 3 minuty w watcherze i bliźniaku;
- ratchet producentów wykrywa literal, subscript, keyword/update, `setdefault`
  i konstruktor `dict()`;
- rozróżnia nowe decyzje, które wymagają rzeczywistej flagi i passive guarda,
  od już exact-zclaimowanej lub exact-utrwalonej transakcji. Ta druga kończy
  się po hot rollbacku, aby restart nie pozostawił połowy zapisu.

Ochrony z v4 pozostają: raw CK nie może sam potwierdzić canonical eventu,
receipt schema v2 jest odrzucona, claimed receipt nie wygasa do exact ACK,
a lifecycle lock chroni atomowy revision CAS. V12 doprecyzowuje, że ogólny
queue-bound receipt **nie** może nadpisać znanego stale parallel baseline;
mutation zdjęcia dekoratora nadal czerwieni test.

Ochrony z v3 pozostają:

- drugi legalny forward, np. 19:21→19:26, przechodzi mimo starego równoległego
  `pickup_at`; porównanie baseline jest semantyczne po chwili, nie po stringu;
- `observation.courier_id`, courier generation, assignment generation i
  monotoniczna `pickup_time_revision` muszą odpowiadać bieżącemu state;
- revision fence blokuje ABA: stary A→B nie może wrócić po A→C→A;
- pre-proposal bierze lane z bieżącego state, nie ze starego worka symulacji;
- oba authority flags `OFF` prowadzą pre-proposal dokładnie starą ścieżką emit/
  apply/scoring, bez nowego outboxa lub callbacku; zwykły pickup zachowuje wynik
  legacy, a addytywna rewizja przesuwa wspólny fence;
- receipt v4 jest rekordem kanonicznej kolejki, jednorazowo claimuje dokładny
  event, przeżywa crash retry i ma exact ACK; dobrze wyglądający słownik poza
  kolejką, inny OID lub druga korekta nie są autorytetem;
- zwykły bool nie zamraża autoryzacji. Po utrwaleniu intencji robi to exact
  outbox attestation związane SHA całego event core i rzeczywistym rekordem
  SQLite; dzięki temu recovery kończy rozpoczęty event po późniejszym OFF;
- event key hashuje pełny efekt i proof, więc różne observed_at/prep/receipt nie
  aliasują się do jednej intencji;
- legalny raw CK jest kanonizowany przed outboxem, a nie dopiero wewnątrz state;
  trwały event, marker, postcondition i downstream mają jeden typ;
- nowa lub nieatestowana authority jest fail-closed, jeśli passive guard jest
  OFF; exact claim/outbox kończy tylko już rozpoczętą transakcję;
- AST ratchet wykrywa literal dict, subscript, update i setdefault writery oraz
  dokładną liczbę wejść do jedynego `_guarded_write` funnelu.

Ochrony z v2 pozostają: stary pickup nie cofa CK-derived authority, stłumiony CK
nie wycieka do scoringu, proof wiąże pełny payload, mirror authority nie zależy
od starej flagi, legalny legacy pickup czyści stare provenance, a postcondition
wymaga pełnego pickup+CK+HH:MM+provenance+revision.

Pełny kontrakt i mapa writerów/konsumentów są w
`docs/RUTCOM_COMMITTED_PICKUP_AUTHORITY.md`.

## Dowody bramki

- Review v20: dwa niezależne werdykty `CONFIRMED_DEFECT`; osiem unikalnych
  klas odtworzonych 8/8 RED, zero live. V21 ma 8/8 PASS, osiem mutation kills,
  exact restore pięciu modułów, szeroki klaster 367/367 oraz pełną hermetyczną
  regresję 6706 passed, 74 skipped, 8 xfailed, 153 warnings, 0 failed w
  479,28 s na base `b8bf3f8d3`. Pierwszy przebieg bez worktree env był
  świadomie nieważny (3 path-mixing fail); poprawny pełny przebieg jest zielony.
- Review v19: authority verdict SHA
  `7ffd3b6493cd62c5e848e96e8e57e2bd190bb8b764db84b304bce6fe63563b15`
  i completeness verdict SHA
  `11cf4fe79f3ebe83a02167c3eb74133d19ae171297d097b44e47aea12b524a20`;
  oba `CONFIRMED_DEFECT`, cztery findings odtworzone RED, zero live.
- Pre-review v20: 4/4 findings green; sześć mutation kills, exact restore 7/7,
  szeroki klaster 592/592; py_compile/import/diff-check i checkery lifecycle,
  hygiene, effect, docs oraz entropy bez nowej luki. Pełna hermetyczna regresja
  ma 6617 passed, 74 skipped, 8 xfailed, 149 warnings, 0 failed w 594,91 s;
  profil identyczny z v19, delta +7 testów.
- Review v18: authority verdict SHA
  `19de319abc4764be7327d4bcc3828707aba66907e31ad8143ac7d39bdfcc42ae`
  i completeness verdict SHA
  `d5080f8d1cbf7a9dca1bd25d9f03f54d045ad43149c094cb899e189354721247`;
  oba driver-check OK i `CONFIRMED_DEFECT`, zero live.
- Finalny pre-review v19: 6610 passed, 74 skipped, 8 xfailed, 149 warnings,
  0 failed w 466,95 s na base `49aed3215`; siedem mutation kills, exact
  restore 30/30 i focused 432/432. Read-only preflight live ma pustą kolejkę i
  outbox, lecz dwie aktywne niepełne czasówki, więc flip pozostaje na HOLD.
- Review v17: authority verdict SHA
  `3ac4fbd0b06ab4cc6c59620c2ad9d8f86a8cca7456e85a331aa9295337d8ef5c`
  i completeness verdict SHA
  `4d316178ae84c812b8cde933203d404d2bb9cf21fde7ca96f65c5b98d299f9ac`;
  oba driver-check OK i `CONFIRMED_DEFECT`, zero live.
- Finalny pre-review v18: 6599 passed, 74 skipped, 8 xfailed, 149 warnings,
  0 failed w 452,70 s na base `49aed3215`; negatywny oracle 6F+1P przed i
  7/7 po, sześć mutation probes po 1F, exact restore oraz focused 376/376.
- Review v16: authority verdict SHA
  `730a1d688556605d586fd7492810af1d22aaddd7c8055f831225143938e7ac47`
  i completeness verdict SHA
  `b7240d1656050c3e6c307d313bf054e87e8656503ff17c26f2de6306f500b82f`;
  oba driver-check OK i `CONFIRMED_DEFECT`, zero live.
- Finalny pre-review v17: 6576 passed, 74 skipped, 8 xfailed, 149 warnings,
  0 failed w 450,57 s; negatywny oracle 6F przed/6P po, pięć mutation probes
  2F/2F/2F/1F/1F, exact restore 8/8 i ratchet 22/22.
- Finalny pre-review v16: 6561 passed, 74 skipped, 8 xfailed, 149 warnings,
  0 failed w 433,93 s; focused 399/399, mutation restore 8/8.
- Finalny pre-review v15: 6544 passed, 74 skipped, 8 xfailed, 149 warnings,
  0 failed w 452,38 s; v14 miała 6527 pass przy identycznym profilu.
- Review v14: authority verdict SHA
  `da50ecaca167125d8cb63b88a0c5345345b0b1cd3c6626badccfb6a4ee44d56d`
  i completeness verdict SHA
  `45cb20c57057812e3e925e9ac844d95574c692787c69b4ebf227793f26a43d11`;
  oba driver-check OK i `CONFIRMED_DEFECT`, zero live.
- V15: 12 czerwonych przypadków przed fixem, kontrolowana mutacja 12 fail/2
  pass, exact restore SHA oraz 14/14 green. Ratchet po weryfikacji dwóch nowych
  legalnych event-type sites ma 20/20.
- Finalny v13: pełna hermetyczna regresja 6510 passed, 74 skipped, 8 xfailed,
  149 warnings, 0 failed w 405,67 s. Base `e23592b02` ma 6349 passed przy
  identycznym profilu skip/xfail/warnings, delta +161.
- Finalny historyczny v12: szeroki klaster 329/329; pełna regresja
  6493 passed, 74 skipped, 8 xfailed, 149 warnings, 0 failed w 397,19 s;
  dwa review v12 zatrzymały promocję przed live.
- Negatywny oracle odtwarza 491578. `OFF` = dokładna ścieżka legacy, `ON` =
  kanoniczne 19:21 z `rutcom_forward_commitment`.
- Guard oracle: 483023 (16:22→15:04), post-pickup, stale courier/assignment/
  revision, cross-OID/forged/reused receipt, spoofed/tampered proof, ABA i
  parallel stale pickup pozostają zablokowane.
- Finalny rozszerzony klaster v11 authority/queue/outbox/state/watcher/
  rollback/flag: 336 passed. Szeroki klaster v12 dotkniętych warstw: 329 passed.
- Pierwsza pełna suita po v10 review ujawniła 3 fail wspólnej przyczyny przy
  6479 pass; po zawężeniu klasyfikatora i dodaniu oracle pełna regresja
  final-byte v11 ma 6482 passed, 74 skipped, 8 xfailed, 149 warnings, 0 failed
  w 388,91 s. Czysty base `e23592b02`: 6349 passed przy identycznych
  skip/xfail/warnings; delta +133.
- Jedenaście świeżych mutation probes v11, osiem v12 oraz osiem reprodukcji
  review v13 czerwieni po celowym
  cofnięciu każdej
  nowej ochrony; po przywróceniu mutowane pliki mają identyczne SHA-256 jak
  kandydat. Wcześniejsze oracles v2–v10 pozostają zielone.
- `py_compile` i import zmienionych modułów są zielone; `git diff --check`
  zielony.
- Lifecycle 557/557; hygiene 271/271 i zero orphan; effect/docs bez nowej luki
  przy poprawnym worktree env; merge-seed jest idempotentny.
- Fingerprint poprawnie raportuje nową flagę jako `REGISTRY-ONLY` przed
  deployem/restartem. Entropy: dead-flag/drift 1, sentinel 0, bez pogorszenia.

## Rollout i rollback

Flaga `ENABLE_CZASOWKA_RUTCOM_FORWARD_AUTHORITY` ma const default `OFF` i nie
istnieje jeszcze jako aktywny klucz produkcyjnego `flags.json`. Adrian wydał
ACK na docelowy stan `ON` i wymagane kontrolowane restarty, lecz kod nie został
wdrożony, procesy nie były
restartowane, a runtime nie został zmieniony.

Operacja live wymaga backupu, quiesce wyłącznie panel-watcher i shadow,
wdrożenia jawnego commita oraz `py_compile`/import check przy zatrzymanych
writerach. `fence-forward --apply --quiesced` zakłada exact UUID+SHA fence i
wykonuje pełny preflight. Przy `ready=true` flaga jest ustawiana atomowo na
`true`, po czym `release-forward-fence --apply --quiesced --authority-active
--fence-id <id>` sprawdza efektywny ON i zwalnia wyłącznie ten receipt. Dopiero
potem start, health/PID/NRestarts/fingerprint i replay/smoke 491578.
`nadajesz-panel` nie wymaga restartu: jego subprocess enqueue jest blokowany
przez fence. `dispatch-telegram` nie jest częścią zakresu.

Rollback zachowania jest hot: nowa flaga `false`. Revert kodu pre-v4 wymaga
OFF każdej flagi z kanonicznej listy authority, quiesce, terminalnego authority
outboxa, zweryfikowanego fence-last receiptu,
trwałego backupu i mechanicznej bramki
`tools/rutcom_committed_authority_rollback.py`; prosty revert jest zabroniony.
Forward deploy nie wymaga migracji danych, ale po zatrzymaniu starych writerów
musi przejść fenced preflight schema v4: mechanicznie
potwierdzone loaded+inactive `dispatch-panel-watcher.service` i
`dispatch-shadow.service`, flaga OFF, pusta kolejka,
ważny forward fence, zero blokujących rekordów kolejki, zero pending writerów
CK/pickup czasówki, zero czasowych `NEW_ORDER`, zero
unfinished pre-v4 raw eventów, zero pre-v16 assignmentów z CK bez snapshotów
polityki oraz zero aktywnych niepełnych kontraktów czasówki. Dopiero potem wolno
podmienić kod.
Po ON wymagane jest co
najmniej 48 godzin obserwacji applied/suppressed, outbox retry, receipt claim/
ACK, post-pickup/stale-generation/revision i spójności pickup↔CK.

## Identyfikacja kandydata

- Wersja: v27, po pełnej regresji, przed dwoma świeżymi review exact-byte
- Branch: `fix/rutcom-committed-provenance-v20-20260802`
- Worktree: `/root/worktrees/dispatch_v2/active/20260802-rutcom-v17-integration-pkgroot/dispatch_v2`
- Poprzedni odrzucony kandydat v26: `7266686b29a5bcc0e6e3f9948574d55afef2c8dc`
- Zintegrowany produkcyjny master: `64f773ddc`
- Produkcja: bez zmian; zero deployu, restartu, migracji i flipu.
