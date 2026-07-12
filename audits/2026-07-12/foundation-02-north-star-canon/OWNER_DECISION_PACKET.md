# Owner Decision Packet

Tylko decyzje nieredukowalne biznesowo. Nie ma tu pytań o kod, flagi, testy ani aktualność dokumentów.

## OD-01 — Kanoniczny cel i event pickup

- `decision_id`: `OD-01-PICKUP-KPI`
- Pytanie: co produkt ma uznawać za zakończenie nogi pickup i kanoniczny KPI: zakończenie wizyty, potwierdzone wyjście czy faktyczne przejęcie zamówienia?
- Dlaczego technika nie rozstrzyga: obecne sensory mierzą arrival i last-inside; nie ma kompletnego confirmed exit ani possession event. Te eventy odpowiadają na różne pytania biznesowe (`UNK-001`).
- Co wiemy: `restaurant_last_inside_at` nie jest exit/pickup; click jest proxy; canonical event jest `unbound` (`GT-003`, `GT-004`, `GT-007`, `GT-010`, `GT-011`).
- Źródła: `docs/eta/06_ground_truth_contract.md@c7de9f2`; Prompt 02; ledger `UNK-001`.
- Realne opcje:
  1. `restaurant_last_inside_at` jako KPI wizyty, jawnie nie physical pickup;
  2. przyszły `confirmed_geofence_exit_at` jako KPI zakończenia wizyty;
  3. przyszły `possession_confirmed_at` jako KPI physical pickup;
  4. dwa osobne KPI: operacyjny exit i usługowy possession.
- Wpływ: wybór zmienia target ETA, kohortę, instrumentację i interpretację opóźnienia.
- Rekomendacja `PROPOSED_SYNTHESIS`: opcja 4; do czasu instrumentacji last-inside publikować wyłącznie pod własną nazwą.
- Pewność rekomendacji: wysoka co do rozdzielenia, średnia co do potrzeby dwóch docelowych KPI.
- Blokuje: physical pickup KPI i promocję kalibracji na tę semantykę.
- Inne fazy: ogólna konstytucja może powstać, ale karta ETA/pickup nie powinna być finalna.

## OD-02 — Kanoniczny cel i event delivery

- `decision_id`: `OD-02-DELIVERY-KPI`
- Pytanie: czy kanoniczny KPI delivery ma kończyć się przyjazdem pod adres, czy potwierdzonym przekazaniem klientowi?
- Dlaczego technika nie rozstrzyga: GPS potwierdza arrival, a nie handoff; click jest proxy (`UNK-002`).
- Co wiemy: app/server geofence dają `delivery_arrival_at`; baseline nie ma kompletnego `customer_handoff_at` (`GT-005`, `GT-006`).
- Źródła: `docs/eta/06_ground_truth_contract.md@c7de9f2`; ledger `UNK-002`.
- Realne opcje:
  1. arrival jako jedyny KPI;
  2. handoff jako jedyny KPI po nowej instrumentacji;
  3. dwa KPI: routing ETA do arrival oraz customer-service SLA do handoff.
- Wpływ: zmienia target modelu, przypisanie odpowiedzialności i sens „on time”.
- Rekomendacja `PROPOSED_SYNTHESIS`: opcja 3; nie scalać arrival z handoff.
- Pewność rekomendacji: wysoka.
- Blokuje: handoff KPI i autorytatywną ocenę pełnej dostawy.
- Inne fazy: nie blokuje ogólnej konstytucji; blokuje finalną kartę delivery KPI.

## OD-03 — Brama jakości KPI: coverage, missingness i koszt

- `decision_id`: `OD-03-KPI-GATE`
- Pytanie: po związaniu eventów, jakie minimalne coverage i polityka braków/no-GPS/package-unknown oraz jaka funkcja kosztu i próg jakości dopuszczają werdykt/promocję?
- Dlaczego technika nie rozstrzyga: coverage GPS jest selektywne, whole-map nie jest historycznie odtwarzalne, a historyczne P75/P80/20% dotyczy proxy (`GT-009`, `CF-008`, `UNK-005`).
- Co wiemy: każdy raport musi pokazać bazową kohortę, complete-case support i source-specific coverage; proxy nie wypełnia GPS.
- Źródła: `docs/eta/06_ground_truth_contract.md@c7de9f2`; `docs/eta/03,05@c7de9f2` jako wspólny historyczny lineage.
- Realne opcje:
  1. jedna globalna brama coverage/kosztu;
  2. osobne bramy per event/source/cohort;
  3. measurement-only bez promotion gate do czasu większego coverage.
- Wpływ: decyduje, kiedy wynik jest reprezentatywny i jaki błąd jest akceptowalny.
- Rekomendacja `PROPOSED_SYNTHESIS`: opcja 2 z fail-closed dla niewystarczającego lub tendencyjnego coverage; wartości liczbowe zatwierdzić dopiero po związaniu OD-01/OD-02 i raporcie supportu.
- Pewność rekomendacji: wysoka dla rozdzielenia; brak rekomendacji liczbowej bez danych.
- Blokuje: KPI-based promotion, nie rekonstrukcję North Star.
- Inne fazy: ogólne karty można projektować z bramą `UNBOUND/HOLD`, lecz nie finalizować kart ETA.

## OD-04 — Precedencja R27 w Alarmie

- `decision_id`: `OD-04-R27-ALARM-PRECEDENCE`
- Pytanie: czy Alarmowe R27 `±10` jest wyłącznie tolerancją planu/ALERT bez zmiany zapisanego commitment, czy wyjątkiem od reguły C6 „commit max ±5 i nietykalny”?
- Co **nie** jest pytaniem: Alarm jest automatyczny, per decyzja, po niewykonalności Strategii 1 i 2; R6 wynosi wtedy 40 dla wszystkich i nigdy według klasy kuriera (`BR-002`, `BR-016`).
- Dlaczego technika nie rozstrzyga: ten sam kanon mówi `±10 w Alarmie` w C5/§3a i `max ±5/nietykalny` w C6 (`CF-002`, `UNK-003`). Kodowe load 5/10 nie jest decyzją biznesową.
- Co wiemy: stored commitment nie może być cicho nadpisany; baseline nie ma kompletnego S3 enforcementu; dokładny machine predicate i observability są zadaniem technicznym, nie pytaniem do właściciela.
- Źródła: `memory:ZIOMEK_REGULY_KANON.md@ca55742b`, linie 64/73/123/129 kontra 124; Prompt 02.
- Realne opcje:
  1. commit zawsze max ±5; Alarm nie zmienia R27;
  2. commit pozostaje zapisany, ale Alarm dopuszcza plan/actual deviation do ±10 z jawnym ALERT-em;
  3. Alarm może także jawnie renegocjować commitment do ±10.
- Wpływ: zmienia ochronę obietnicy, feasibility planu i treść ALERT-u; nie zmienia R6 35/40.
- Rekomendacja `PROPOSED_SYNTHESIS`: opcja 2 — nie przepisywać commitment, a odchyłkę 5–10 traktować jako jawny Alarm breach wymagający właściwej execution authority.
- Pewność rekomendacji: średnia.
- Blokuje: finalną kartę R27/Alarm i automatyczne wykonanie planu z odchyłką ponad ±5.
- Inne fazy: nie blokuje ogólnej konstytucji ani technicznego odtworzenia triggera S1→S2→Alarm.

## OD-05 — Trwała korekta właściciela kontra incydent

- `decision_id`: `OD-05-CORRECTION-PROMOTION`
- Pytanie: jaki jawny akt odróżnia trwałą zmianę kanonu od korekty pojedynczego przypadku?
- Dlaczego technika nie rozstrzyga: źródła dają precedencję nowszej decyzji i zakaz automatycznego uogólnienia jednego case, ale nie definiują promotion act (`LRN-002`, `UNK-004`).
- Co wiemy: operator feedback jest hipotezą; właściciel może ustanowić regułę; jedna korekta nie wystarcza.
- Źródła: Prompt 02; `memory:ziomek-change-protocol.md@ca55742b`; `memory:lessons.md@ca55742b`.
- Realne opcje:
  1. tylko jawna wypowiedź normatywna ze scope, wyjątkami i effective date tworzy regułę;
  2. powtarzalne korekty tworzą propozycję, którą właściciel jawnie zatwierdza;
  3. próg liczbowy automatycznie promuje regułę.
- Wpływ: decyduje, co Codex/Ziomek może utrwalać i jak nie przeuczać się na incydencie.
- Rekomendacja `PROPOSED_SYNTHESIS`: połączenie 1+2; nigdy automatyczna opcja 3 bez ACK. Case-level correction musi mieć scope i wygasać lub pozostać label; kanon wymaga jawnego aktu promocji.
- Pewność rekomendacji: wysoka.
- Blokuje: konstytucyjny kontrakt aktualizacji kanonu.
- Inne fazy: Prompt 03 może powstać szkicowo, ale nie powinien zatwierdzać autonomii aktualizacji reguł bez tej decyzji.

## OD-06 — Execute boundary dla ALERT/least-damage i przerzutu

- `decision_id`: `OD-06-EXECUTION-BOUNDARY`
- Pytanie: dla każdej trudnej klasy decyzji czy Ziomek ma `execute+notify`, czy `recommend+wait-for-approval` — osobno dla przerzutu, wykonania planu Alarmowego i pozostałego least-damage?
- Co **nie** jest pytaniem: aktywacja trybu Alarmu R6 jest automatyczna, per decyzja, po niewykonalności Strategii 1 i 2 (`BR-016`).
- Dlaczego technika nie rozstrzyga: target mówi 100% decyzji i brak cichego fallbacku, aktualny kanon mówi jednocześnie, że przerzut zatwierdza człowiek; executor code i flaga nie nadają uprawnienia (`NS-002`, `BR-012`, `UNK-006`).
- Co wiemy: widoczność proposal/ALERT jest obowiązkowa; auto-assignment był OFF w oknie Promptu 01; przerzut jest obecnie human-gated.
- Źródła: kanon pamięci `ca55742b`; Prompt 02; Prompt01 runtime `14e7a5e`; baseline executors.
- Realne opcje:
  1. execute+notify dla wszystkich trudnych klas;
  2. approval-before-execute dla wszystkich;
  3. macierz per klasa z osobnym awansem i stop-lossem.
- Wpływ: definiuje realną rolę operatora, ryzyko automatycznej szkody i treść kart autonomii.
- Rekomendacja `PROPOSED_SYNTHESIS`: opcja 3; przerzut pozostaje approval-before-execute do osobnego dowodu i awansu. Aktywacja trybu Alarmu R6 pozostaje automatyczna zgodnie z `BR-016`; dopiero automatyczne wykonanie konkretnego planu alarmowego wymaga rozstrzygnięcia OD-04 i własnej karty. `>40` pozostaje niedopuszczalne niezależnie od wykonawcy.
- Pewność rekomendacji: wysoka dla macierzy, średnia dla przyszłego Alarm execute.
- Blokuje: finalne karty autonomii ALERT/przerzut/Alarm.
- Inne fazy: blokuje pełne zatwierdzenie Promptu 03; nie blokuje read-only przygotowania jego struktury.

## OD-07 — Kanoniczny interwał R6

- `decision_id`: `OD-07-R6-INTERVAL`
- Pytanie: jaki dokładnie fizyczny lub operacyjny interwał mierzy R6 — jaki jest event startu, jaki event końca i czy czas od gotowości oraz czas w pojeździe są jedną regułą czy dwoma osobnymi constraintami?
- Co **nie** jest pytaniem: próg wynosi 35 normalnie i 40 tylko w automatycznym Alarmie dla wszystkich; nigdy nie zależy od klasy kuriera (`BR-002`, `BR-003`, `BR-016`).
- Dlaczego technika nie rozstrzyga: tabela kanonu mówi „od odebrania”/„w aucie”, owner-correction lineage utrwala `pickup_ready_at` dla nieodebranego i `picked_up_at` dla odebranego, a baseline kończy interwał na predykcji planu (`CF-009`, `IMP-020`–`IMP-022`). Kod nie może wybrać intencji.
- Co wiemy: physical pickup i customer handoff nie mają kompletnego wersjonowanego observable; last-inside i delivery arrival dowodzą węższych zdarzeń (`GT-003`–`GT-006`).
- Źródła: `memory:ZIOMEK_REGULY_KANON.md@ca55742b` linie 63/153/165/194; `memory:ziomek-change-protocol.md@ca55742b` linia 43; `docs/eta/06_ground_truth_contract.md@c7de9f2`; baseline `r6_thermal_anchor`.
- Realne opcje:
  1. R6 = physical pickup → nazwany delivery event; ready-age jest osobną regułą oczekiwania/świeżości;
  2. R6 = food ready → nazwany delivery event; physical in-vehicle age jest osobną obserwacją lub constraintem;
  3. dwa równoległe, jawnie nazwane constraints: `food_ready_age` i `in_vehicle_age`, bez resetowania jednego w drugi;
  4. zachować hybrydę baseline: ready przed pickup, picked-up po pickup, z jawnym uzasadnieniem resetu.
- Dodatkowy wybór: end event musi zostać związany jako arrival albo handoff (może współdzielić wynik OD-02, ale nie musi).
- Wpływ: zmienia znaczenie HARD, feasibility, least-damage, oracle, KPI, observability i interpretację każdego naruszenia R6.
- Rekomendacja `PROPOSED_SYNTHESIS`: opcja 3 — rozdzielić dwa zjawiska i dopiero potem jawnie przypisać politykę 35/40 do właściwego constraintu; do czasu decyzji nie nazywać baseline hybrid anchor fizycznym R6. Rekomendacja nie proponuje nowej liczby.
- Pewność rekomendacji: wysoka dla rozdzielenia pojęć, niska dla przypisania progu bez decyzji właściciela.
- Blokuje: autorytatywną definicję R6, kartę R6/Alarm, oracle i naprawę anchor driftu.
- Inne fazy: nie blokuje review misji/HARD-before-SOFT ani analizy technicznej; blokuje finalną konstytucję i kartę autonomii dotyczącą R6.
