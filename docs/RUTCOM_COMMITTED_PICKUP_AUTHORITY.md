# Rutcom committed pickup authority

Status: kandydat v22 niewdrożony, kod ciemny, nowa flaga domyślnie `OFF`.
Owner 2026-08-01 polecił docelowo włączyć ją `ON`, ale wdrożenie kodu,
kontrolowane restarty i weryfikacja runtime pozostają osobną operacją live.

V21 wiąże pierwszy zapis czasu z trwałym intentem zapisanym w tej
samej transakcji co `NEW_ORDER`. Przy ON aggregate shell nie zawiera
kanonicznego pickup/CK, ale zachowuje niezmienny pierwotny tuple jako jawnie
pending receipt. Receipt jest następnie wiązany exact event-id z niezależnym,
zastosowanym rekordem outbox pierwotnego `NEW_ORDER`; self-hash nie stanowi
samodzielnego authority. Jeden czysty resolver materializuje z niego dokładnie jeden
`PICKUP_TIME_UPDATED`; ten sam atomowy writer zapisuje pickup+CK+HH:MM i usuwa
pending intent. Crash ani flip ON→OFF nie może więc zastąpić pierwotnego 19:21
późniejszym statusowym restampem 19:16. Zwykły restart tick konsumuje trwały
intent przed sprawdzeniem bieżącej tablicy i bez ponownego fetchu restauracji,
a dopiero potem może oceniać świeży panel.

V22 domyka trzy luki wykryte przez dwa review v21. Recovery pending initial
intentu odbywa się zaraz po pierwszym odczycie state i przed każdym writerem
lifecycle, a późny bliźniaczy recovery został usunięty. Jeden czysty
`project_time_event_order` projektuje post-event aggregate dla state writera
i forward preflightu, więc zmiana `prep_minutes` 20→60 nie może utworzyć
rozszczepionej prawdy pickup/CK. Pre-proposal zamraża jeden immutable
`CommittedPickupPolicySnapshot` przed asynchronicznym HTTP i przekazuje go aż
do legacy albo durable apply: OFF→ON nie nadaje authority rozpoczętej operacji,
a ON→OFF domyka już rozpoczęty event bez ponownego odczytu żywych flag.

Preflight uznaje kontrakt za gotowy wyłącznie po aware ISO, zgodnym HH:MM,
jednej wartości pickup/CK, kompletnej provenance i braku pending intentu.
Przed flipem oba dokładne unity writerów muszą być mechanicznie loaded+inactive,
a następnie muszą zakończyć się wszystkie oczekujące time-writery czasówki
oraz każdy czasowy `NEW_ORDER`, także już snapshot-bound i sanitizowany. Wyjątek
forward-rolloutu dotyczy wyłącznie poprawnie związanego eventu jawnego elastyka;
code revert nadal pozostaje konserwatywny. Wersjonowany CK lub pickup po legalnym
prune agregatu kończy się `superseded`, nie `pending`.

## Root cause i granica kontraktu

Rutcom wystawia dwa równoległe pola czasu: `czas_kuriera` oraz `pickup_at`.
Przed zmianą trzy ścieżki znały fragmenty semantyki tych pól: watcher,
pre-proposal re-check i state machine. Pasywny guard poprawnie blokował
historyczne statusowe re-stampy, ale nie umiał odróżnić od nich nowego,
umówionego z restauracją ruchu do przodu. W incydencie 491578 dlatego 52 razy
stłumił 19:16→19:21. Aplikacja nie wyliczyła 19:16 — odczytała stary stan.

Po zmianie obowiązuje jedna granica:

1. `committed_pickup_authority.py` jest jedynym ownerem polityki. Z pełnego
   snapshotu tworzy albo brak decyzji, albo `PICKUP_TIME_UPDATED` wraz z
   `committed_pickup_authority.v1` proofem.
2. `committed_pickup_apply.py` jest jedynym trwałym transportem eventów czasu:
   przed outboxem tłumaczy legalny raw CK na kanoniczny pickup, a potem wykonuje
   outbox → apply state → lifecycle downstream. Producent nie zapisuje stanu
   ani nie uznaje samego enqueue za sukces. Historyczny durable raw CK jest
   terminalnie odrzucany, zamiast tworzyć drugi transport wewnątrz state.
3. `_pickup_time_event_status` oraz handler `PICKUP_TIME_UPDATED` w
   `state_machine.py` są jedyną bramką apply i jedynym writerem sprzężonych pól.
   Ponownie odtwarzają politykę z proofu, sprawdzają CAS/generację i atomowo
   zapisują pickup, CK, HH:MM, monotoniczną rewizję oraz provenance.
4. `event_effect_status` uznaje durable event za zastosowany dopiero po
   dokładnej zgodności pickup+CK+HH:MM+provenance+revision+semantic event key.
   Exact claim kolejki albo exact outbox attestation zachowuje autoryzację
   rozpoczętej transakcji po flipie flagi, ale nie omija CAS, generacji ani
   postcondition i nie autoryzuje żadnej nowej decyzji. Exact rekord outboxa
   jest wznawiany przed ponowną walidacją snapshotu wejściowego, bo poprawny
   state apply sam przesuwa rewizję i nie może unieważnić własnego retry.
5. Wszystkie nowe nie-authority eventy czasu przy aktywnym rolloucie niosą
   wspólną kopertę `time_update_cas.v1`: status, kuriera, assignment generation
   i monotoniczną rewizję właściwego pola. Committed pickup wiąże jednocześnie
   rewizję pickup i CK, więc cykl CK A→C→A nie może odtworzyć starego claimu.
   Ten sam helper buduje snapshot, ten
   sam oracle rozstrzyga handler oraz retry, a event key wiąże całą generację.
   Każdy zachowany ślad częściowo uszkodzonej koperty jest zarezerwowany i
   failuje closed zamiast degradować się do legacy.

## Reguły autorytetu

- Automatyczny Rutcom forward jest możliwy tylko przy nowej fladze `ON`, dla
  czasówki w lokalnym stanie `planned`/`assigned`, bez dowodu odbioru/dostawy,
  przy Rutcom `status_id=2`. Nowy CK musi być większy od bieżącego CK, nie
  wcześniejszy od pickup, różnić się o co najmniej wspólne 3 minuty i nadal
  leżeć w przyszłości w chwili obserwacji.
- Ręczna krawędź markera `zmiana_czasu_odbioru: false→true` używa istniejącej
  flagi manual passthrough i jawnego mapowania statusów: `planned→2`,
  `assigned→3/4/6`. Może korygować czas w obie strony.
- Przycisk koordynatora może autoryzować korektę w obie strony tylko przez
  świeży, OID-bound receipt `coordinator_time_recheck.v4` obecny w kanonicznej
  kolejce. Receipt jednorazowo claimuje dokładny event; crash retry zwraca ten
  sam event, druga korekta nie może użyć tego samego kliknięcia. Claimed head
  jest niezmienny aż do exact ACK. Ponowny klik w tym oknie staje się jednym
  coalesced successorem i dopiero po terminalizacji poprzednika jest promowany
  do nowej generacji. Sam
  `deliberate=True` ani dobrze wyglądający słownik nie są dowodem. Każdy
  wymuszony legacy/elastyk event czasu także dostaje exact claim **przed**
  pierwszym side effectem. Po terminalnym ACK neutralny claim promuje świeżą
  kontynuację tego samego kliknięcia, aby drugie równoległe pole dostało osobny
  claim zamiast zginąć po częściowym apply. Stary rekord `oid→timestamp` jest
  atomowo podnoszony do v4 ze źródłem `legacy_coordinator_queue`, które nie może
  autoryzować committed czasu czasówki.
- Obserwacja pola Rutcom `pickup_at` przechodzi przez ten sam owner. Jeśli po
  przyjęciu CK 19:21 ten sam response niesie stary równoległy baseline 19:16,
  resolver blokuje go jako `parallel_pickup_snapshot_stale`, więc kolejny tick
  nie może cofnąć naprawy. Ogólny receipt odświeżenia nie jest wyborem pola i
  nie znosi tej blokady; jawna korekta musi przejść przez CK/manual marker albo
  nową wartość pickup różną od zapamiętanego starego baseline.
  Dotyczy to również pierwszego przejścia `null→wartość`: brak starego pickupu
  nie jest już osobną ścieżką legacy ani obejściem authority.
- Kolejny legalny CK-derived forward korzysta z zapamiętanego panel baseline,
  więc 19:21→19:26 nie jest blokowane przez równoległe pole pickup nadal
  pokazujące 19:16. Chwile są porównywane semantycznie po timezone.
- Każdy event wiąże OID, oba pola kuriera obserwacji, assignment generation,
  pickup revision, CK revision, źródło, status, obserwowany pickup baseline,
  stary CK jako parę ISO+HH:MM i pełną treść zmiany. Jeden kanoniczny kontrakt
  pól sprzężonych wiąże również stare i nowe `prep_minutes`,
  `decision_deadline` oraz `zmiana_czasu_odbioru`; CAS i exact postcondition
  obejmują każde pole, które writer atomowo zmienia. Rewalidacja porównuje stary CK proofu z
  aktualnym state; bliźniak pre-proposal bierze ten baseline z aktualnego state,
  nie ze starego `OrderSim`/worka.
  State machine wiąże proof z rzeczywistą klasą czasówki oraz porównuje dokładny
  payload i dozwolony zbiór pól koperty z ponownie wygenerowanym eventem;
  podrobiona etykieta, alias lifecycle ani prawidłowy proof z dopisanym
  prep/deadline/courierem nie przejdą.
- `picked_up_at`/`delivered_at`, nieaktywny stan, stara generacja kuriera,
  assignmentu lub pickup revision, niespójne ISO/HH:MM i przegrany CAS są
  fail-closed. Revision blokuje także cykl ABA A→C→A. Każdy legalny writer
  pickup — authority i legacy — przesuwa ten sam wewnętrzny revision fence,
  również zanim pierwszy event authority zdąży wejść do state.
- Authority-derived pickup zawsze lustrzy CK niezależnie od starej flagi
  `ENABLE_PICKUP_TIME_MIRRORS_CK`; zwykły legacy pickup nadal zachowuje jej
  dotychczasową semantykę. Legalny legacy zapis czyści stare provenance.
- Watcher odtwarza claimed exact event przed nowym fetch/diff i potwierdza
  receipt dopiero po terminalnym durable wyniku oraz przez CAS całego rekordu.
  Terminalność nie pochodzi z obiektu zwrotnego próby: exact outbox musi mieć
  `state_status=superseded` albo parę `state_status=applied` i
  `downstream_status=applied`. Awaria callbacku pozostawia claim do retry.
  Crash po claimie nie zmienia treści intencji, brak nowej delty nie może
  skasować claimed rekordu, a ponowny klik nie jest kasowany przez ACK starszego.
- Uszkodzony lub częściowy unclaimed receipt, w tym orphan successor, nie jest
  work itemem ani authority, ale pozostaje poison-evidence. Nie może zostać
  cicho usunięty przez TTL, zwrócony przez runtime API, ACK-nięty ani nadpisany
  kolejnym kliknięciem; rollback audit widzi go jako jawny blocker.
- Recovery claimed eventów iteruje po całej trwałej kolejce przed pętlą po
  `current_state`. Claim legalnie usuniętego/pruned OID nadal trafia więc do
  wspólnego oracle i terminalnego outboxa; obecność zlecenia w bieżącym
  snapshotcie nie jest autorytetem istnienia transakcji.
- Jeżeli po claimie legalny writer wygra revision/assignment CAS, exact event
  jest najpierw utrwalany z atestacją, a wspólny oracle kończy go jako
  `superseded`; nie nadpisuje nowej prawdy i nie więzi headu kolejki. Tak samo
  terminalizuje claim po długim crashu, gdy zamknięty rekord został już legalnie
  usunięty ze state. Błąd odczytu pozostaje odróżnialnym `pending`.
- Nowa flaga `OFF` zachowuje zwykły Rutcom pickup na dotychczasowej ścieżce
  legacy i z tym samym wynikiem biznesowym, nawet jeśli istniejąca flaga
  ręcznego markera jest `ON`; jedynym addytywnym polem jest wspólny,
  monotoniczny `pickup_time_revision`. Gdy obie authority flags są `OFF`,
  pre-proposal nie wykonuje nawet nowego odczytu state i zachowuje emit/apply/
  scoring bez nowego outboxa/callbacku.
- Przy fladze `ON` CK czasówki nie może zmienić równoległy raw
  `CZAS_KURIERA_UPDATED`, którego wspólny resolver nie autoryzuje. Obejmuje to
  oba stare CK-only writery: `COURIER_ASSIGNED` i `first_acceptance`, także gdy
  dotychczasowy CK jest pusty. Assignment nadal zapisuje kuriera, ale czas może
  powstać wyłącznie przez sprzężony `PICKUP_TIME_UPDATED`. Historyczne
  `coordinator_edit`, `first_acceptance` i `ziomek_late_extension` są jawnie
  wygaszone dla czasówki w `RETIRED_CZASOWKA_CK_ONLY_SOURCES`; dwa zewnętrzne
  źródła nie mają producenta na HEAD, a `first_acceptance` pozostaje tylko dla
  elastyka i exact OFF-parity. Przy `OFF` stare ścieżki zachowują legacy.
- Jeden artifact oracle rezerwuje całą klasę **dowodów authority** po
  **obecności klucza**, nie po jego truthiness: proof, revision/baseline,
  event key, receipt, source/observed_source, provenance i exact attestation.
  Kanoniczny marker w `event_id`/`event_id_hint` także jest częścią tożsamości.
  Usunięcie etykiety lub ustawienie pozostałego klucza na `null` nie może
  zdegradować eventu do legacy fallbacku. Ogólne markery wykonania downstream
  (`saved_plans_authorized`, invalidation/reclaim) nie są same w sobie dowodem
  authority: sealer wiąże je exact attestation, ale ich obecność nie może
  przeklasyfikować poprawnego legacy pickupu. Bezpośredni event z osieroconym
  artefaktem authority jest terminalnie odrzucany.
- `coordinator_force` jest zarezerwowany bez receiptu wyłącznie po rozpoznaniu
  rzeczywistej klasy czasówki. Ten sam source pozostaje legalnym, świadomym
  legacy pickupem elastyka; polityka receipt nie może wyciekać między klasami.
- Durable bridge najpierw zamraża wszystkie markery autoryzacji downstream,
  potem sealer wiąże hashem pełną trwałą kopertę poza `event_id` i samą
  attestation, a dopiero potem zapisuje outbox. Zmiana któregokolwiek markera
  po zapisie unieważnia attestation i kończy event fail-closed.
- Zwykły `PICKUP_TIME_UPDATED` przy fladze `OFF` zachowuje dokładnie historyczny
  durable event key. Addytywna wewnętrzna revision nie jest dopisywana jako
  `null` do digestu legacy, więc istniejący predeploy outbox nie rozwidla się na
  drugi event ani drugi downstream callback.
- Nowa lub nieatestowana decyzja authority wymaga jednocześnie aktywnej nowej
  flagi i passive guarda; niespójna konfiguracja jest fail-closed. Wyjątek nie
  jest fallbackiem polityki: już exact-zclaimowana intencja koordynatora albo
  exact-utrwalony rekord outboxa kończy tę samą rozpoczętą transakcję po hot
  rollbacku. Rollback blokuje nowe decyzje, nie rozcina zapisu w połowie.
- Pre-proposal przekazuje świeży CK do scoringu tylko, gdy docelowa wartość
  faktycznie stała się kanoniczna. Suppression nie może wyciec do symulacji.

## Mapa kompletności writerów i konsumentów

| Miejsce | Rola | Writer / consumer | Dotknięte | Uzasadnienie i bramka |
|---|---|---|---|---|
| `panel_client.normalize_order` | ingest jednego response Rutcom | producer CK/pickup/marker/status | N-D | Już zwraca sprzężony snapshot; oracle korzysta z produkcyjnego schematu, bez nowego parsera. |
| panel/console koordynatora | klik „Odśwież czas” | producer żądania | N-D | Istniejący caller nadal woła `enqueue`; semantyka dowodu została scentralizowana poniżej. |
| `coordinator_time_recheck.py` | trwała kolejka intencji | writer/reader receiptu | TAK | v5 rozdziela immutable audit `requested_at` od epoki wykonania `eligible_at`; successor zaczyna TTL dopiero po exact ACK poprzednika. Czytnik zachowuje kompatybilność v4. Immutable claimed head, exact-event claim, bounded continuation, claim-aware drain, poison/corrupt retention i rollback fence pozostają wspólne; odwrócony/partial zegar nie jest wykonywany ani usuwany. Projekcja v5→legacy przy rollbacku używa `eligible_at`, nigdy wcześniejszego `requested_at`, więc świeżo promowany successor nie wygasa natychmiast po cofnięciu kodu. V18 przed każdym re-clickiem weryfikuje exact claimed head i istniejącego successora tym samym oraclem co recovery/ACK; poison pozostaje bajtowo bez zmian. Prepare rollbacku jest transakcją projection+fence: każdy błąd przywraca exact bajty kolejki, a cleanup usuwa wyłącznie własny fence. |
| `panel_watcher._diff_czas_kuriera` | cykliczna obserwacja CK | producer eventu | TAK | Deleguje do resolvera, nie ma własnej polityki; `null→wartość` i `wartość→wartość` mają ten sam authority path, a raw first-acceptance istnieje tylko w parytecie OFF. Wspólny próg szumu 3 min obowiązuje delty z baseline i bliźniaka. Przy rolloucie emituje wspólny status/courier/assignment/revision CAS dla CK. |
| `panel_watcher._diff_pickup_time` | równoległa obserwacja pickup | producer eventu | TAK | Deleguje do resolvera pickup zarówno dla `null→wartość`, jak i `wartość→wartość`, i blokuje stale baseline po CK-derived authority. |
| `panel_watcher._claim_forced_time_event` / `_apply_time_update_event` / `_diff_and_emit` / `_post_restart_cold_start_scan` | watcher transport/recovery | consumer eventu | TAK | Każdy force event jest claimowany przed side effectem; claimed kolejka jest odtwarzana przed `current_state`, a ACK zależy od exact terminalnego rekordu outbox state+downstream. V21 zapisuje pierwotny initial/cold-start tuple jako pending intent w durable `NEW_ORDER`. V22 odzyskuje ten intent natychmiast po pierwszym `state_get_all`, przed heal/assignment/pickup/terminal; stary późny consumer został usunięty. Nieudany receipt izoluje własny OID na bieżący tick, a znane OID-y nie wracają jako fałszywy `NEW_ORDER`. Recovery pozostaje niezależne od board-presence i detail fetch. Broadcast/audit zachowuje raw payload, a tylko projekcja state jest sanitizowana. OFF przed rozpoczęciem zachowuje dokładny legacy tuple. |
| `dispatch_pipeline._v327_emit_pre_recheck_event` | pre-proposal re-check | bliźniaczy producer | TAK | Ten sam resolver/transport/event key, bieżący courier lane i CK baseline z aktualnego state; wynik scoringowy wyłącznie po apply; oba flags OFF = exact legacy bez nowego odczytu state. V22 zamraża jeden request-scoped policy snapshot przed HTTP i przekazuje dokładnie ten sam obiekt przez fetch, emit i legacy/durable apply. Przy rolloucie używa tego samego CK CAS buildera co watcher. |
| `committed_pickup_authority.py` | polityka CK i pickup | jedyny policy/CAS owner | TAK | Incydent 491578, ochrona 483023, próg 3 min, kolejny forward, kontekstowy receipt koordynatora, rzeczywista klasa czasówki, oba courier IDs, assignment/pickup-revision/CK-revision/old-CK CAS, pełny proof/koperta/key, presence-based artifact i durable-row oracle. V21 dodaje exact schema/hash/OID binding initial intentu, jeden resolver zwracający najwyżej jeden event i complete-contract wymagający braku pending receiptu. V22 dodaje immutable exact-bool policy snapshot oraz jeden `project_time_event_order`, wspólny dla state i preflightu. Oba forward classifiery przyjmują kanoniczne `is_czasowka`; jawna etykieta elastyka nie może ominąć `prep_minutes>=60`. Code revert pozostaje fail-closed. |
| `committed_pickup_apply.py` | kanoniczna granica/outbox/apply/lifecycle | jedyny transport eventów czasu | TAK | Tłumaczy legalny raw CK przed outboxem, używa strict state read przed kanonizacją, odrzuca malformed CAS przed outboxem, wiąże generację w event key, weryfikuje proof i seal'uje pełną trwałą kopertę po zamrożeniu markerów downstream. Exact pending initial intent pozwala dokończyć wyłącznie ten sam event po ON→OFF, ale authority wymaga też niezależnego, zastosowanego outboxa pierwotnego `NEW_ORDER`, wskazanego przez `last_lifecycle_event_id_new_order`; spójnie przeliczony self-hash bez receiptu failuje closed. V22 przyjmuje wyłącznie exact request policy z pre-proposal source i nie czyta flag ponownie podczas trwałego apply. |
| `durable_event_apply.emit_and_apply` / `resume_exact` / `is_terminal_outcome` | durable bridge i recovery rozpoczętej transakcji | metadata writer / exact receipt consumer | TAK | Opcjonalny sealer działa po finalnych markerach i przed outboxem; resume wznawia tylko wskazany rekord, a terminal oracle wymaga superseded albo applied+downstream applied przed ACK. COURIER_ASSIGNED zamraża dwa exact bool snapshoty polityki CK, więc handler i oracle nie rozjeżdżają się przy hot flipie; brak/korupcja failuje closed dla CK, nie dla assignmentu. |
| `event_bus.list_unfinished_state_applies` | pełny rollback audit | consumer outboxa | TAK | Bez limitu zwraca każdy układ poza jawnie terminalnym; nieznany/corrupt status i malformed state_event są widoczne i blokują revert kodu. |
| `state_machine.CZAS_KURIERA_UPDATED` / `COURIER_ASSIGNED` | legacy defense-in-depth dla raw CK | consumer/delegat | TAK | Bez `event_id` deleguje do kanonu; raw CK sources są jawnie wygaszone przy ON. Assignment i jego terminalny oracle używają tego samego resolvera oraz durable snapshotu flag: kurier zawsze jest zapisany, a równoległy CK wyłącznie według jednej receipt-bound decyzji. Historyczny durable raw row jest terminalnie superseded. |
| `state_machine._pickup_time_event_status` | apply/recovery oracle | consumer wszystkich pickup eventów | TAK | Wspólny freeze, CAS, courier/assignment/revision i exact postcondition dla authority i legacy; receipt source jest blokowany bez proofu tylko dla czasówki, więc deliberate elastyk zachowuje legacy apply; ABA fail-closed. |
| `state_machine.PICKUP_TIME_UPDATED` | kanon czasu | jedyny state writer | TAK | Atomowy pickup+CK+HH:MM+revision+provenance oraz consume pending initial intentu; gdy intent jest pending, każdy sibling/legacy writer bez exact `committed_new_order_time_intent_id` jest odrzucany i nie może wyczyścić receiptu. V22 klasyfikuje wynik na post-event aggregate projektowany wspólnym helperem, więc pickup zmieniający `prep_minutes` 20→60 nie ominie mirroru czasówki. Authority mirror jest niezależny od starej flagi. `NEW_ORDER` zapisuje tylko shell i niekanoniczny receipt, nigdy drugi committed tuple. |
| `state_machine.event_effect_status` | durable recovery | consumer postcondition | TAK | Pending/applied/superseded bez fałszywego sukcesu po częściowym zapisie. Po legalnym prune oba typy eventów czasu z pełnym `time_update_cas.v1` kończą się `superseded`, bo nie mogą odtworzyć usuniętego agregatu. |
| lifecycle downstream / plan invalidation | odświeżenie widoków i planu | consumer eventu | TAK | Jeden istniejący callback po durable apply; usunięto martwy, duplikujący touch w pipeline. |
| `plan_manager`, `plan_recheck` | plan i ETA | consumer kanonicznego state | N-D | Nie czytają raw Rutcom; test committed propagation pozostaje zielony. |
| `route_simulator_v2`, `route_order`, `route_podjazdy` | trasa | consumer pickup/CK | N-D | Algorytm i progi bez zmian; dostaje atomowo nową prawdę wejściową. |
| `feasibility_v2`, `core/candidates`, `core/selection`, `objm_lexr6` | HARD/selekcja | consumer stanu | N-D | Brak nowej reguły HARD/SOFT, rankingu i tie-break; nie są writerami czasu ani czytelnikami raw CK. |
| `czasowka_scheduler`, `auto_assign_gate` | scheduling/dispatch | consumer pickup | N-D | Dziedziczą kanon; progi 60 i polityka auto-assign bez zmian. |
| courier API serializer | kontrakt aplikacji | consumer CK/HH:MM | N-D | Czyta sprzężone pola ze state; brak render override lub drugiego writera. |
| Android `RouteLogic` | prezentacja kurierowi | consumer API | N-D | Zachowuje istniejącą precedencję; 19:21 pochodzi z jednego kanonicznego zapisu. |
| `common.py`, lifecycle registry/checkery | rollout/fingerprint | owner flagi | TAK | Decision flag, const default OFF, rejestr i effect coverage; predeploy source to `common.py-const`. Obie authority flags są przypięte do `committed_pickup_apply`, `dispatch_pipeline`, `state_machine` i rollback tool; forward flag dodatkowo do `durable_event_apply` oraz `panel_watcher`. AST equality rozpoznaje import/module/local aliases i named arguments oraz zatrzymuje brakującego lub ukrytego consumera. |
| `tools/rutcom_committed_authority_rollback.py` | rollback/roll-forward | operator gate | TAK | Wymaga OFF każdej flagi z kanonicznej listy authority, terminalnego authority outboxa, bezpiecznej kolejki i mechanicznego quiesce. `forward-status --quiesced` schema v4 odczytuje z systemd exact unity `dispatch-panel-watcher.service` i `dispatch-shadow.service`; oba muszą być loaded+inactive. Dalej wymaga ciemnej flagi, pustej kolejki, zera wszystkich oczekujących CK/pickup writerów czasówki, zera każdego czasowego `NEW_ORDER` (także receipt-bound), zera pre-v4 force eventów, zera pre-v16 assignmentów oraz zera aktywnych kontraktów, które nie przechodzą wspólnego complete-contract validatora. V22 projektuje post-event aggregate przed klasyfikacją pickup writera; event promujący `prep_minutes` do klasy czasówki nie może ominąć bramki. Oba classifiery używają kanonicznego `is_czasowka`; marker receipt blokuje code revert. Kompatybilność jest bramką wydania, nie fallbackiem runtime. |
| logi/outbox/provenance | audyt i recovery | consumer zdarzenia | TAK | Pełny proof key, revision i exact attestation pozwalają mierzyć/retry bez nowego źródła prawdy. |
| testy incident/ratchet/twins/rollback | bramka regresji | verifier | TAK | Dwa review v21 znalazły trzy klasy luk v22; MAIN odtworzył je jako 4/4 RED. V22 ma 4/4 PASS, dwa dodatkowe hot-flip oracles, pięć mutation kills z exact restore, 226/226 focused, 456/456 broad i pełną regresję 6713 passed / 0 failed. Ratchet przypina wczesne recovery, jedną projekcję post-event, brak live reread polityki, writerów pending intentu, oba classifiery i exact systemd quiescence. Dwa świeże final-byte review v22 pozostają bramką przed live. |

## Dowody przed wydaniem

- Dwa niezależne review exact-byte v21 poprawnie zatrzymały live: authority verdict
  SHA `9f0c69be82b500848c503157843564ec0dd52ea2548d8a35b72731eae9e885bb`,
  rollout/completeness SHA
  `bc86f8dd7c1b644e363df1cccc9cf85376cb555ead4075122a96d34815fc7ddf`;
  oba `CONFIRMED_DEFECT`. Cztery odtworzone oracles były 4/4 RED przed v22 i
  4/4 PASS po nim; dwa oracles hot-flipu, pięć mutation kills, exact restore,
  226/226 focused i 456/456 broad są zielone. Pełna hermetyczna regresja na
  base `b8bf3f8d3`: 6713 passed, 74 skipped, 8 xfailed, 153 warnings i 0 failed
  w 637,32 s. Evidence: `eod_drafts/2026-08-01/RUTCOM_V22_DOD_EVIDENCE.txt`.
  Dwa świeże review final-byte v22 pozostają bramką przed live.
- Dwa niezależne review exact-byte v20 poprawnie zatrzymały live. MAIN
  odtworzył osiem unikalnych ustaleń jako 8/8 RED: board-dependent recovery,
  self-hash bez niezależnego outbox receiptu, sibling writer konsumujący intent,
  sanitizowany broadcast, niepełny code-revert fence, dwa obejścia
  `prep_minutes>=60` i deklaratywne quiesce. Kandydat v21 ma 8/8 PASS, osiem
  mutation kills, exact restore pięciu modułów i szeroki klaster 367/367.
  Pełna hermetyczna regresja na base `b8bf3f8d3`: 6706 passed, 74 skipped,
  8 xfailed, 153 warnings, 0 failed w 479,28 s. Pierwszy, nieważny przebieg
  bez worktree env dał trzy path-mixing fail; po poprawie środowiska te same
  nodeidy mają 3/3 PASS, a pełny przebieg jest zielony. Dwa świeże review
  final-byte v21 pozostają bramką przed live.
- Dwa niezależne review exact-byte v19 poprawnie zatrzymały live. Authority
  verdict SHA `7ffd3b6493cd62c5e848e96e8e57e2bd190bb8b764db84b304bce6fe63563b15`,
  completeness verdict SHA
  `11cf4fe79f3ebe83a02167c3eb74133d19ae171297d097b44e47aea12b524a20`;
  oba `CONFIRMED_DEFECT`. MAIN odtworzył cztery ustalenia jako cztery czerwone
  oracles: utratę initial tuple między `NEW_ORDER` i writerem, ponowny odczyt
  live flagi rozcinający rozpoczętą transakcję, pominięcie pending legacy
  pickup w preflighcie oraz false-green sanitizowanego `NEW_ORDER` ze
  snapshotem ON.
- Kandydat v20: każdy z czterech oracles jest zielony. Sześć kontrolowanych
  mutacji — usunięcie durable intentu, authorization receiptu po ON→OFF,
  atomowego consume, obu preflight fence'ów oraz rzeczywistego recovery ticka
  — czerwieniło dokładnie właściwy test. Po exact restore wspólny zestaw ma
  7/7 PASS, a szeroki klaster dotkniętych warstw 592/592 PASS. Tamper 19:21
  bez przeliczenia hash kończy się `invalid_new_order_time_intent`; restart
  przy świeżym restampie 19:16 odzyskuje niezmienne 19:21. Pełna hermetyczna
  regresja v20: 6617 passed, 74 skipped, 8 xfailed, 149 warnings, 0 failed w
  594,91 s; profil skip/xfail/warnings identyczny z v19, delta +7 testów. Dwa
  świeże final-byte review v20 pozostają bramką przed live.
- Dwa niezależne review exact-byte v18 poprawnie zatrzymały live. Authority
  verdict SHA `19de319abc4764be7327d4bcc3828707aba66907e31ad8143ac7d39bdfcc42ae`,
  completeness verdict SHA
  `d5080f8d1cbf7a9dca1bd25d9f03f54d045ad43149c094cb899e189354721247`;
  oba driver-check OK i `CONFIRMED_DEFECT`. Findings odtworzono przed fixem.
  V19 ma siedem skutecznych mutation probes, exact restore 30/30, focused
  432/432 i pełną hermetyczną regresję 6610 passed, 74 skipped, 8 xfailed,
  149 warnings, 0 failed w 466,95 s na base `49aed3215`.
- Dwa niezależne review exact-byte v17 poprawnie zatrzymały live. Authority
  verdict SHA `3ac4fbd0b06ab4cc6c59620c2ad9d8f86a8cca7456e85a331aa9295337d8ef5c`,
  completeness verdict SHA
  `4d316178ae84c812b8cde933203d404d2bb9cf21fde7ca96f65c5b98d299f9ac`.
  MAIN odtworzył wszystkie cztery klasy; przed fixami było 6 FAIL i jeden
  wymagany PASS kontroli niezwiązanego outboxa. V18 ma 7/7 PASS, sześć
  skutecznych mutation probes, focused 376/376 i pełną hermetyczną regresję
  6599 passed, 74 skipped, 8 xfailed, 149 warnings, 0 failed w 452,70 s na
  aktualnym base `49aed3215`.
- Dwa niezależne review exact-byte v16 poprawnie zatrzymały live. Authority
  verdict SHA `730a1d688556605d586fd7492810af1d22aaddd7c8055f831225143938e7ac47`,
  completeness verdict SHA
  `b7240d1656050c3e6c307d313bf054e87e8656503ff17c26f2de6306f500b82f`.
  MAIN odtworzył ich trzy klasy jako sześć czerwonych przypadków: rollback
  successora liczony od `requested_at`, pickup/cold-start omijające pełny
  authority contract oraz niedomknięte pre-v16 assignmenty i niepełny state
  niewidoczne dla preflightu.
- Kandydat v17: te same przypadki mają 6/6 PASS. Pięć mutacji odwracających
  kolejno epokę rollbacku, wspólny pickup owner, pełny cold-start init,
  pre-v16 assignment classifier i incomplete-state classifier dało
  2F/2F/2F/1F/1F. Po exact restore wspólny zestaw ma 8/8 PASS. Szeroki klaster
  miał 528 PASS i jeden oczekiwany fail semantycznego hash-pinu po dodaniu
  dwóch legalnych preflight consumerów; po niezależnej weryfikacji dokładnych
  lokalizacji ratchet ma 22/22 PASS. Pełna hermetyczna regresja v17:
  6576 passed, 74 skipped, 8 xfailed, 149 warnings, 0 failed w 450,57 s.
- Kandydat v16: finalny focused klaster 399/399 PASS; findings review v15 były
  4/4 RED przed fixem, a dodatkowy oracle MAIN chroni exact first-acceptance
  OFF parity. Pięć mutation probes dało 1F/2F/2F/1F/2F, po exact-byte restore
  zestaw kontrolny ma 8/8 PASS. Pełna hermetyczna regresja v16:
  6561 passed, 74 skipped, 8 xfailed, 149 warnings, 0 failed w 433,93 s.
- Dwa niezależne review v15 poprawnie zatrzymały live. Wykryły rozjazd
  assignment handler/oracle, bypass resolvera przez null first-acceptance,
  wygasanie successora za claimed headem oraz utratę implicit tożsamości
  czasówki po sprzężonym prep drop. V16 zamyka je u ownerów policy/outbox/state/
  queue, bez fallbacku runtime.
- Finalny pre-review v15: pełna hermetyczna regresja 6544 passed,
  74 skipped, 8 xfailed, 149 warnings, 0 failed w 452,38 s. Profil
  skip/xfail/warnings jest identyczny z v14/base; delta +17 względem v14.
- Dwa niezależne review v14 poprawnie zatrzymały live. Jeden reviewer wykazał
  CK ABA starego claimu; drugi siedem klas: niepełny CAS/postcondition pól
  sprzężonych, `delta=None`, partial CK downgrade, utratę poison receiptu,
  literalny bypass rejestru i brak forward-deploy preflightu. Wszystkie 12
  negatywnych przypadków czerwieniło przed fixem. Kontrolowana mutacja sześciu
  ownerów dała 12 fail/2 pass; po exact restore klaster ma 14/14 green.
- Finalny v14: pełna hermetyczna regresja 6527 passed, 74 skipped, 8 xfailed,
  149 warnings, 0 failed w 429,50 s. Profil skip/xfail/warnings jest identyczny
  z v13 i base; delta +17 względem v13 oraz +178 względem base `e23592b02`.
- Dwa niezależne review v13 poprawnie zatrzymały live i wskazały dziewięć klas:
  brak wspólnego CAS legacy pickup/claimed CK, fail-soft read przed
  kanonizacją, drift handler/oracle wygaszonego raw writera, null-attestation,
  częściowe czyszczenie provenance, nieatomowy rollback oraz luki skanerów
  aliasów i statycznego `join`. V14 zamyka je u ownerów policy/state/outbox/
  rollback/registry. Szeroki klaster dał 350 pass, a ratchet po świadomym
  odświeżeniu jednego zmienionego kontraktu 20/20; cztery dodatkowe
  mutation-proby czerwienią i zostały przywrócone do exact SHA-256.
- Finalny v13: pełna hermetyczna regresja 6510 passed, 74 skipped, 8 xfailed,
  149 warnings, 0 failed w 405,67 s. Czysty base `e23592b02` ma 6349 passed
  przy identycznym profilu skip/xfail/warnings; delta +161.
- Dwa niezależne review v12 poprawnie zatrzymały live i wykryły osiem klas:
  oscylację niezmienionego sprzecznego snapshotu, brak CK CAS w pickup proofie,
  rozbieżne definicje terminalności outboxa, nieograniczoną continuation jednego
  kliknięcia, niepełny CAS claimed legacy pickup, claim więziony po prune OID,
  hot-OFF/code-revert niechroniący aktywnego provenance oraz brak rollback readera
  w symbolicznym rejestrze flag. Każda klasa została odtworzona jako RED na v12.
  V13 usuwa przyczyny u wspólnych ownerów policy/state/outbox/queue/rollback,
  bez nowego fallbacku; wszystkie osiem oracles jest zielonych w pełnej suicie.
- Blind review v11 poprawnie zatrzymały live. Odtworzone findings: receipt mógł
  cofnąć CK-derived 19:21 przez stary pickup 19:16; legacy force event nie miał
  durable claimu i receipt mógł zniknąć po state apply/downstream error; manual
  flaga miała niepełną mapę readerów; stare CK-only sources nie były jawnie
  wygaszone. V12 zamyka te klasy u ownera policy/queue/registry. Zarzut, że
  każdy raw CK powinien być bezpieczny dla code revertu, został odrzucony jako
  sprzeczny z brakiem orders_state contextu: to świadomy konserwatywny `HOLD`,
  a podstawowym rollbackiem pozostaje hot `false`.

- Oracle incydentu: `OFF` zachowuje suppression, `ON` prowadzi 19:16→19:21
  z `authority=rutcom_forward_commitment`; 483023, post-pickup, stale generation,
  spoofed proof i parallel stale pickup pozostają blokowane.
- Finalny rozszerzony klaster v11 authority/queue/outbox/state/watcher/
  rollback/flag: 336 passed. Pełna regresja final-byte: 6482 passed,
  74 skipped, 8 xfailed, 149 warnings, 0 failed w 388,91 s; czysty base
  `e23592b02`: 6349 passed przy identycznych skip/xfail/warnings, delta +133.
- Jedenaście świeżych mutacji v11 czerwieni po: powrocie z key-presence do
  truthiness, usunięciu revision key, pominięciu durable event ID, pominięciu
  row event key, otwarciu pustego rowa, bezkontekstowym zarezerwowaniu
  `coordinator_force`, sprawdzeniu tylko forward flag przy release oraz
  usunięciu seed gate, zmianie aliasowego readu, dodaniu ukrytego czwartego
  consumera i ponownym uznaniu ogólnego markera downstream za authority.
  Wszystkie mutowane moduły wróciły do dokładnych SHA-256 kandydata.
- `py_compile`, import check, lifecycle 550/550, flag hygiene/effect/doc checks,
  `git diff --check` są zielone. Entropy dashboard jest identyczny z base.
- Blind review v1–v5 zatrzymywały promocję. Dwa review v5 znalazły sześć
  unikalnych defektów, a MAIN niezależnie siódmy: drugi durable raw transport,
  legacy ABA przed pierwszym authority apply, coordinator fallback przy `OFF`,
  alias bypass ratchetu, retry po state-apply przed ACK, bezpośredni forged
  `coordinator_force` oraz przerwanie claimed transakcji przez passive `OFF`.
  V6 zamknęła wszystkie, lecz świeże dwa review v6 ponownie wydały
  `CONFIRMED_DEFECT`. Pięć unikalnych luk zostało niezależnie odtworzonych:
  re-click nadpisywał claim, normalized `observed_source` omijał reserved guard,
  legacy event key dryfował przez nowe pole `null`, kompatybilny drain kasował
  claim bez apply, a prosty revert nie rozumiał trwałych artefaktów. V7 zamyka
  je jednym kontraktem generacji kolejki, wspólnym source oracle, exact legacy
  key i mechanicznym rollback gate. Dwa świeże review v7 wykryły kolejne realne
  luki: claim więziony po przegranym CAS/prune, ACK przed downstream, fence
  mylony z rollback receiptem, niepełny classifier oraz obejścia AST. V8 zamyka
  je jednym terminalnym outbox oracle, fence-last receipt i statycznym resolverem.
  Dwa świeże review v8 wykryły recovery zależne od current state, brak old-CK
  CAS, równoległy CK-only writer, fail-open corrupt rollback audit i keywordowy
  bypass ratchetu. V9 zamknęła te luki, lecz dwa świeże review v9 ponownie
  wydały `CONFIRMED_DEFECT`: rollback ignorował manual authority flag, stripped
  forward artefakty degradowały się do legacy, oba CK-only writery miały lukę
  pustego baseline, attestation nie wiązała markerów downstream, a ratchet
  rezerwował zbyt wąską klasę artefaktów. V10 zamknęła je wspólnymi ownerami,
  ale dwa świeże review v10 wykryły null/remaining-key degradation, utratę
  durable row identity, release-fence sprawdzający tylko jedną flagę,
  bezkontekstowe zablokowanie poprawnego elastyka i fałszywą mapę consumerów.
  V11 zamknęła je jednym presence+identity contractem, row-level rollback
  classifierem, kontekstowym source oracle i AST-equality registry gate. Dwa
  review v11 wykryły wyżej opisane luki. V12 je zamknęła, lecz dwa review v12
  wykryły osiem kolejnych klas opisanych wyżej. Review v18 zatrzymały live z
  czterema klasami opisanymi w dowodach v19. Review v19 znalazły kolejne cztery
  klasy zamknięte w v20, a review v20 osiem kolejnych klas zamkniętych w v21;
  v21 wymaga nowego podwójnego `CLEAN` exact final-byte przed live.

## Rollout, obserwacja i rollback

- `ENABLE_CZASOWKA_RUTCOM_FORWARD_AUTHORITY` pozostaje predeploy `OFF`.
- Jawne polecenie ownera z bieżącej sesji obejmuje docelowy flip i wymagane
  kontrolowane restarty. Po dwóch `CLEAN`: quiesce wyłącznie writerów
  panel-watcher i shadow, zielony `forward-status --quiesced`, backup, deploy jawnego
  commita oraz `py_compile`/import check przy zatrzymanych writerach. Następnie
  flaga jest ustawiana atomowo na `true` jeszcze przed uruchomieniem nowych
  procesów. Dopiero potem start panel-watcher i shadow oraz health/PID/
  `NRestarts`/fingerprint/replay/smoke. Ta kolejność nie zostawia okna, w którym
  nowy kod mógłby utrwalić legacy `NEW_ORDER` przy fladze OFF.
- Forward deploy nie wymaga migracji danych, ale po quiesce starych writerów
  musi przejść `python3 tools/rutcom_committed_authority_rollback.py
  forward-status --quiesced`: oba exact unity systemd muszą być loaded+inactive,
  flaga OFF, kolejka pusta, zero oczekujących CK/pickup
  writerów czasówki, zero czasowych `NEW_ORDER`, zero niedomkniętych pre-v4 raw
  eventów koordynatora, zero unfinished pre-v16 assignmentów bez snapshotów
  polityki i zero aktywnych niepełnych kontraktów czasówki. Schema intentu,
  provenance i outbox są addytywne, a runtime nie
  dostaje kompatybilności przez source-label fallback. Hot rollback zachowania
  to flaga `false` i jest podstawowym rollbackiem.
  Najnowszy read-only preflight wykazał pustą kolejkę, zero unfinished outboxa,
  zero aktywnych committed state i zero aktywnych niepełnych kontraktów; wcześniejsze
  dwa legacy rekordy zakończyły się naturalnie, bez migracji i renderowego
  obejścia. `safe_for_forward_deploy=false` wynika już wyłącznie z aktywności obu
  writerów. Po dwóch `CLEAN` trzeba je kontrolowanie quiesce'ować i powtórzyć
  preflight; dopiero wynik zielony otwiera deploy i flip.
- Revert kodu do czytnika pre-v4 jest osobną operacją i **nie może** być prostym
  `git revert`. Po OFF wszystkich flag z kanonicznej listy authority oraz
  potwierdzeniu fingerprintu trzeba quiesce'ować
  writerów, doprowadzić exact claim/outbox do terminalnego stanu i uruchomić:
  `python3 tools/rutcom_committed_authority_rollback.py status`. Następnie
  `prepare --apply --quiesced --queue-backup <trwała-ścieżka>` zakłada trwały
  fence, robi exact backup 0600 i konwertuje wyłącznie nieclaimowane v4 receipts
  do timestampów czytelnych przez stary kod. Każdy claim, corrupt rekord albo
  niedomknięty authority outbox daje `HOLD`; wtedy kod v22 zostaje, flaga OFF.
  Dopiero `safe_for_code_revert=true` zezwala na jawny revert i kontrolowany
  restart za ACK. Po roll-forward do v22 fence zwalnia się wyłącznie przez
  `release-fence --apply --v4-code-active` przy OFF i pustym authority outboxie.
  SQLite przed operacją nadal wymaga backupu przez API `.backup`.
  `dispatch-telegram` pozostaje nietknięty.
- Obserwacja po aktywacji minimum 48 h: applied/suppressed per reason, retry
  outboxa, stale-generation/post-pickup, receipt ACK i brak różnic pickup↔CK.
