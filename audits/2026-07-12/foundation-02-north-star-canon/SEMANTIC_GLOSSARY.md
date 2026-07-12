# Słownik semantyczny Ziomka — kandydat Promptu 02

Status: **forensic reconstruction; nie jest jeszcze słownikiem autorytatywnym**. Definicje opisują dokładny zakres dowodu lub reguły. Gdy termin nie ma związanej semantyki produktowej, pozostaje `UNKNOWN`, zamiast otrzymać intuicyjne znaczenie.

## Obiekty decyzji i planowania

| Termin | Preferowana definicja | Status / claim | Synonimy dopuszczalne | Niebezpieczne użycie i skutek |
|---|---|---|---|---|
| `order` / zamówienie | Biznesowa jednostka dostawy, dla której system ocenia przydział i plan. | `PROPOSED_SYNTHESIS`; `SYN-010`, wspierające `NS-003` | zamówienie | `task` bez kwalifikatora może oznaczać stop, pracę lub zamówienie i fałszować liczebność. |
| `task` | Konkretna czynność lub węzeł wykonawczy, np. pickup albo dropoff; nie jest automatycznie całym zamówieniem. | `PROPOSED_SYNTHESIS`; `SYN-010` | zadanie, krok | Zamiana z `order` łączy obiekt biznesowy z krokiem trasy. |
| `courier` / kurier | Osoba wykonująca fizyczną trasę; obiekt runtime zawiera tylko jej obserwowalny stan, nie pełną prawdę o położeniu lub zdolności. | `PROPOSED_SYNTHESIS`; ontologia oparta na code domain i `BR-007` | kierowca, jeśli kontekst jasno mówi o kurierze | `courier class` nie może implikować termicznego limitu R6. |
| `candidate` | Ocena pary kurier + hipotetyczny plan dla jednego zamówienia, z osobnym feasibility, score i uzasadnieniem. | `IMPLEMENTED_ONLY`; `core/selection.py`, `IMP-001` | kandydat | Kandydat nie jest assignmentem ani zgodą na wykonanie. |
| `assignment` | Utrwalone przypisanie zamówienia do kuriera. | `PROPOSED_SYNTHESIS`; ETA v1 assignment anchor + `IMP-015` | przydział | Propozycja, `F7AGREE` lub ranking nie są same w sobie assignmentem. |
| `route` | Fizyczna, uporządkowana droga przez węzły pickup/dropoff. | `PROPOSED_SYNTHESIS`; route domain + `BR-010` | trasa | Route nie jest rankingiem kurierów ani samym planem czasowym. |
| `plan` | Modelowana, wersjonowalna sekwencja stopów wraz z czasami, constraintami i pochodzeniem wejść. | `PROPOSED_SYNTHESIS`; plan domain + `ARCH-007` | plan trasy | Predykcja planu nie jest actual outcome. |
| `sequence` / kolejność | Porządek stopów pickup/dropoff wewnątrz planu. | `PROPOSED_SYNTHESIS`; route/plan code + `BR-010`, `BR-011` | sekwencja, kolejność stopów | Nie używać jako synonimu kolejności kandydatów lub kolejności zamówień w UI. |
| `proposal` | Jawna rekomendacja kuriera/planu wraz ze statusem wykonalności, ryzykiem i uzasadnieniem. | `PROPOSED_SYNTHESIS`; `BR-008` | propozycja | Proposal nie musi oznaczać, że system może go automatycznie wykonać. |
| `execution` | Wywołanie efektu zmieniającego assignment/runtime po decyzji; wymaga osobnego execution authority dla danej klasy. | `PROPOSED_SYNTHESIS`; `IMP-013`, `LIVE-002`, `UNK-006` | wykonanie decyzji | Rekomendacja, klasyfikacja lub `would_auto_assign` nie są execution. |

## Hierarchia decyzji

| Termin | Preferowana definicja | Status / claim | Synonimy dopuszczalne | Niebezpieczne użycie i skutek |
|---|---|---|---|---|
| `feasibility` | Ocena, czy kandydat spełnia obowiązujące HARD w danym, jawnie nazwanym trybie; least-damage pozostaje `NO`, nawet gdy jest pokazywany. | `OWNER_CONFIRMED`; `BR-001`, `SYN-002` | wykonalność | `best effort` nie może po cichu stać się feasible. |
| `HARD` | Ograniczenie, którego normalny scoring nie może przehandlować. Osobny zatwierdzony tryb alarmowy jest innym kontraktem, nie SOFT waiverem. | `OWNER_CONFIRMED`; `BR-001` | twardy constraint | `hard-ish`, ogromna kara score i log-only tripwire nie są HARD enforcementem. |
| `SOFT` | Preferencja lub koszt używany dopiero po HARD; może zmieniać ranking, lecz nie legalizować naruszenia HARD. | `OWNER_CONFIRMED`; `BR-001`, `BR-006` | miękka reguła | SOFT nie może zostać nazwane veto tylko dlatego, że kara jest duża. |
| `penalty` | Jawny ujemny składnik funkcji wyboru albo koszt solvera. | `IMPLEMENTED_ONLY`; `IMP-007`, `IMP-010` | kara scoringowa | Kara `-1000` nadal jest SOFT i może ukrywać brak prawdziwego HARD. |
| `score` | Implementacyjna wartość porównująca kandydatów wewnątrz dopuszczonej warstwy. | `IMPLEMENTED_ONLY`; `IMP-001` | wynik rankingu | Score nie mierzy fizycznej jakości outcome i nie jest wyjaśnieniem operacyjnym. |
| `declared time` | Restauracyjna deklaracja najwcześniejszej gotowości/odbioru; system nie może obiecać kuriera wcześniej ani przepisać jej dla wygody innej reguły. | `OWNER_CONFIRMED`; `BR-004` | zadeklarowany czas, ready declaration | Nie mieszać z przewidywanym pickup, committed pickup ani actual pickup. |
| `committed pickup` | Uzgodniona po przypisaniu obietnica czasu odbioru, chroniona przed cichym nadpisaniem. Normalne odchylenie ±5 jest SOFT. | `OWNER_CONFIRMED`; `BR-005`, `BR-006` | umówiony odbiór | Nie jest physical pickup, declared time ani rolling display ETA. |
| `least-damage` | Najmniej szkodliwa znana opcja wybierana, gdy nie ma normalnie wykonalnej. | `CANON_CONFIRMED`; `BR-008`, `BR-011` | najmniejsza szkoda | Nie oznacza „wszystko wolno”. |
| `least-damage proposal state` | Jawna reprezentacja least-damage z zachowanym `feasibility=NO/ALERT`, ryzykiem i uzasadnieniem, bez domniemanej zgody na wykonanie. | `PROPOSED_SYNTHESIS`; `SYN-002` | stan propozycji najmniejszej szkody | Widoczność i execution authority są rozdzielne. |
| `Always-propose` | Przy istniejącej flocie pokaż feasible albo least-damage/ALERT; hold czasówki i fizyczny brak floty są odrębnymi, jawnymi stanami. | `OWNER_CONFIRMED`; `BR-008`, `BR-009` | zawsze pokaż propozycję | Nie utożsamiać z jedną flagą lub czterema przetestowanymi bypassami. |
| `fallback` | Jawne, deterministyczne zachowanie degradacyjne z nazwanym powodem i granicą wykonania. | `PROPOSED_SYNTHESIS`; `SYN-001`, `SYN-002`, `SYN-008` | ścieżka awaryjna | Cichy KOORD, fikcyjna pozycja albo heurystyka udająca truth nie są bezpiecznym fallbackiem. |

## Zdarzenia fizyczne, obserwowalne i KPI

| Termin | Preferowana definicja | Status / claim | Synonimy dopuszczalne | Niebezpieczne użycie i skutek |
|---|---|---|---|---|
| `physical pickup` | Faktyczne przejęcie zamówienia przez kuriera. Dokładny kanoniczny observable pozostaje niezwiązany. | `UNKNOWN`; `GT-004`, `UNK-001` | odebranie towaru, tylko gdy chodzi o fakt fizyczny | Zabronione dla click, arrival i last-inside. |
| `restaurant arrival` | Kwalifikowane GPS pojawienie się kuriera w geofence restauracji. | `IMPLEMENTED_ONLY`; `GT-002` | przyjazd do restauracji | Nie dowodzi pickup ani gotowości jedzenia. |
| `restaurant last-inside` | Ostatni wiarygodny punkt GPS nadal wewnątrz geofence wybranej wizyty. | `IMPLEMENTED_ONLY`; kontrakt ETA v1 `GT-003` | ostatni punkt wewnątrz | Nie nazywać departure, exit ani pickup. |
| `geofence exit` | Potwierdzone przejście inside→outside po wizycie. Kontrakt v1 nie ma kompletnego wersjonowanego eventu. | `UNKNOWN`; `GT-004`, `UNK-001` | wyjście z geofence | Nawet exit nie dowodzi possession bez dodatkowego kontraktu. |
| `delivery arrival` | Kwalifikowane GPS przybycie pod adres dostawy. | `IMPLEMENTED_ONLY`; kontrakt ETA v1 `GT-005` | przyjazd pod adres | Nie nazywać delivery completed ani customer handoff. |
| `customer handoff` | Faktyczne przekazanie zamówienia odbiorcy. Kanoniczny observable pozostaje niezwiązany. | `UNKNOWN`; `GT-006`, `UNK-002` | przekazanie klientowi | Delivery click i GPS arrival nie są automatycznie handoff. |
| `pickup click` / `delivery click` | Manualne lub aplikacyjne zdarzenie statusowe. | `OWNER_CONFIRMED`; `GT-010` | status click | Jest proxy procesu, nie physical ground truth. |
| `observable` | Zdarzenie bezpośrednio widziane przez nazwany sensor/proces z określonym source, confidence, czasem i lineage; zakres twierdzenia nie może być szerszy od sensora. | `PROPOSED_SYNTHESIS`; `SYN-006` | obserwowalne zdarzenie | „GPS truth” bez nazwania targetu nadinterpretuje dowód. |
| `proxy` | Sygnał skorelowany z celem, ale niedowodzący celu. | `OWNER_CONFIRMED`; `GT-001` | wskaźnik zastępczy | Proxy nie może uzupełniać brakującego observable i awansować modelu jako ground truth. |
| `ground truth` | Zdarzenie zgodne dokładnie z mierzoną semantyką, z provenance wystarczającym dla danego twierdzenia. GPS arrival może być truth tylko dla arrival. | `PROPOSED_SYNTHESIS`; `SYN-006`, z zakazem click-as-truth `GT-010` | prawda zdarzeniowa z kwalifikatorem | Zabronione gołe `truth`, `physical delivered` dla arrival i `real pickup` dla click. |
| `outcome` | Późniejszy, zweryfikowany wynik względem nazwanego celu i kohorty. | `CANON_CONFIRMED`; `LRN-001` | wynik rzeczywisty | Decyzja operatora nie jest automatycznie outcome. |
| `coverage` | Udział bazowej kohorty posiadający kwalifikowane observable, raportowany ze źródłem, brakami i policy. | `PROPOSED_SYNTHESIS`; `SYN-005`, `SYN-009`; reconstructability v1 `GT-009` | pokrycie | Nie wolno uogólniać selektywnego GPS na całą flotę bez analizy biasu. |

## ETA, SLA i czas

| Termin | Preferowana definicja | Status / claim | Synonimy dopuszczalne | Niebezpieczne użycie i skutek |
|---|---|---|---|---|
| `ETA` | Predykcja konkretnego, nazwanego eventu z określonym prediction anchor, kohortą i wersją. | `CANON_CONFIRMED`; `GT-008` | estymowany czas do `<event>` | Zabronione nieoznaczone „ETA accuracy” i porównanie różnych anchorów. |
| `assignment-time ETA` | Ostatnia kwalifikowana predykcja dostępna nie później niż decyzja assignment. | `IMPLEMENTED_ONLY`; `GT-007`, `GT-008` | ETA przy przypisaniu | Nie jest późniejszym rolling ETA. |
| `rolling display ETA` | Aktualizowana prognoza komunikacyjna po przypisaniu. | `CANON_CONFIRMED`; `GT-008` | bieżąca prognoza | Nie może cicho przepisać pierwotnego commitment. |
| `SLA` | Kontrakt na nazwanej parze start/end eventów i progu; bez nich termin jest niepełny. | `PROPOSED_SYNTHESIS`; `GT-008`, `CF-008` | poziom usługi, po zdefiniowaniu eventów | Gołe SLA w kodzie miesza ready, pickup, now, arrival i handoff. |
| `R6` / `R6 thermal interval` | Constraint świeżości z potwierdzoną polityką liczbową 35 normalnie / 40 tylko Alarm / nigdy klasa; dokładny produktowy `<start>→<end>` pozostaje nierozstrzygnięty. | `CONFLICTED`; liczby `BR-002`, `BR-003`; interval `CF-009`, `UNK-007`–`UNK-009` | R6, dopiero z nazwanym interwałem | Zabronione nieoznaczone twierdzenie „wiek termiczny”, które ukrywa ready vs pickup oraz arrival vs handoff. |
| `baseline r6_thermal_anchor` | Implementacyjna hybryda: picked-up→`picked_up_at`; inaczej `pickup_ready_at`→plan pickup→`now`; koniec to `predicted_delivered_at`. | `IMPLEMENTED_ONLY`; `IMP-020`, `IMP-021` | techniczna kotwica R6 na `c7de9f2` | Nie jest automatycznie kanonem produktu ani observed physical interval. |

## Interakcja człowieka i autonomia

| Termin | Preferowana definicja | Status / claim | Synonimy dopuszczalne | Niebezpieczne użycie i skutek |
|---|---|---|---|---|
| `operator correction` | Jawna zmiana decyzji/propozycji przez operatora; sygnał do analizy, nie automatyczna prawda. | `PROPOSED_SYNTHESIS`; `GT-001`, `LRN-002` | korekta operatora | Jeden case nie tworzy globalnej reguły; reason/scope należy zapisać. |
| `override` | Decyzja operatora różna od propozycji systemu. | `IMPLEMENTED_ONLY`; `GT-001` | PANEL_OVERRIDE | Nie dowodzi błędu modelu bez właściwego outcome. |
| `agree` | Akceptacja propozycji przez operatora. | `IMPLEMENTED_ONLY`; `GT-001` | PANEL_AGREE, jeśli faktycznie kotwiczy assignment | Nie dowodzi optymalności; `F7AGREE` może być tylko meta-ratingiem. |
| `alarm` / tryb alarmowy R6 | Automatyczna, per-decyzyjna Strategia 3 po niewykonalności Strategii 1 i 2; R6=40 dla wszystkich. | `CANON_CONFIRMED`; `BR-016` | tryb ratunkowy | Exact machine predicate/observability są luką kodu; R27 precedence pozostaje `UNK-003`. |
| `ALERT` | Jawna etykieta wyniku wymagającego uwagi lub ograniczonego wykonania, zawierająca problem, ryzyko i potrzebne działanie. | `CANON_CONFIRMED`; `BR-008`, `NS-009` | alert decyzji | Nie jest automatycznie trybem R6 ani zgodą execute. |
| `escalation` | Przekazanie człowiekowi jawnej decyzji/ryzyka, gdy dana klasa nie ma uprawnień wykonawczych lub wymaga wyjątku biznesowego. | `PROPOSED_SYNTHESIS`; `NS-006`, `UNK-006` | eskalacja | Cichy brak kandydata nie jest poprawną eskalacją. |
| `autonomy` | Zdolność samodzielnego podjęcia, a w jawnie zatwierdzonym zakresie także wykonania, konkretnej klasy decyzji bez rutynowej akceptacji człowieka. | `CANON_CONFIRMED`; `NS-002` | autonomia Ziomka | Kod executorów lub flaga nie nadają same uprawnienia. |
| `autonomy promotion` | Jawny awans uprawnień jednej klasy decyzji po spełnieniu dowodu, obserwowalności, kill-switcha i rollbacku. | `PROPOSED_SYNTHESIS`; `SYN-001` | awans autonomii | Nie jest flipem całego systemu ani automatycznym skutkiem dobrego replayu. |
| `intervention` | Zatwierdzenie, korekta, przejęcie lub zatrzymanie wykonania przez człowieka. | `PROPOSED_SYNTHESIS`; `NS-007`, `SYN-004` | interwencja człowieka | Nie mieszać z obserwacją albo biernym otrzymaniem ALERT-u. |
| `learning` | Kontrolowana zmiana predykcji lub zachowania na podstawie zweryfikowanych outcome i skategoryzowanego feedbacku, po dowodzie i z rollbackiem. | `CANON_CONFIRMED`; `LRN-001`–`LRN-003` | uczenie, kalibracja — tylko z kwalifikatorem | Nie oznacza auto-retrainingu, imitacji klików ani uogólnienia jednego case. |

## Przeciążone słowa wymagające kwalifikatora

| Słowo | Rozdzielone nazwy | Status | Dlaczego kwalifikator jest obowiązkowy |
|---|---|---|---|
| `tier` | `courier_capacity_class`; `courier_speed_profile`; `late_pickup_risk_level`; `escalation_level`; `alarm_mode` | `CONFLICTED`; `BR-003`, `CF-001` | Gołe `tier` historycznie połączyło klasę kuriera z 40-minutowym R6. |
| `class` | `courier_capacity_class`; `model_class`; `decision_risk_class` | `CONFLICTED`; `BR-003` | Żadna klasa kuriera nie daje przywileju R6. |
| `courier_capacity_class` | Kategoria pojemności worka/operacyjnej zdolności kuriera; nie zmienia termicznego R6. | `CANON_CONFIRMED`; `BR-003` | Mylenie z alarmem tworzy class privilege. |
| `courier_speed_profile` | Profil estymacyjny tempa/dwell używany do predykcji, nie normatywny limit świeżości. | `PROPOSED_SYNTHESIS`; `BR-003`, `IMP-014` | Użycie jako HARD bez osobnej decyzji osłabia R6. |
| `late_pickup_risk_level` | Implementacyjna kategoria ryzyka naruszenia committed pickup, używana do demotion/tiering. | `IMPLEMENTED_ONLY`; `IMP-007` | Nie jest klasą kuriera ani Alarmem. |
| `escalation_level` | Poziom jawności/obsługi problemu, np. normalny proposal kontra ALERT; nie zmienia automatycznie feasibility. | `PROPOSED_SYNTHESIS`; `BR-008`, `SYN-002` | Nie może ukrywać execute permission. |
| `alarm_mode` | Biznesowa Strategia 3 opisana w `BR-016`, nie wartość courier class ani sam cap best-effort. | `CANON_CONFIRMED`; `BR-016` | Mylenie z flagą shadow fałszuje effective state. |
| `pickup` | `declared_ready`; `committed_pickup`; `predicted_pickup`; `restaurant_arrival`; `restaurant_last_inside`; `confirmed_exit`; `physical_pickup`; `pickup_click` | `CONFLICTED`; `BR-004`–`BR-006`, `GT-002`–`GT-004` | Każdy wariant ma inny kontrakt i inny zakres dowodu. |
| `delivery` | `predicted_delivery`; `delivery_arrival`; `customer_handoff`; `delivery_click` | `CONFLICTED`; `GT-005`, `GT-006`, `UNK-002` | Arrival nie jest handoff, a click nie jest fizycznym outcome. |
| `truth` | `<event>_observable`; `<event>_proxy`; `<event>_ground_truth` | `CONFLICTED`; `GT-001`, `CF-005`, `CF-006` | Nazwa bez targetu ukrywa nadinterpretację sensora. |
| `fallback` | `input_hold`; `degraded_estimate`; `least_damage_no_alert`; `human_approval_required`; `zero_fleet`; `fleet_unassessable` | `PROPOSED_SYNTHESIS`; `SYN-008` | Każdy wariant ma inną wykonalność i granicę execute. |

## Terminy nadal nierozstrzygnięte

- `canonical pickup KPI event` — `UNKNOWN` (`UNK-001`).
- `canonical delivery KPI event` — `UNKNOWN` (`UNK-002`).
- `R27 ±10 in Alarm versus immutable/max ±5 commitment` — `CONFLICTED` (`UNK-003`). Trigger auto po S1+S2 i scope per-decision są potwierdzone (`BR-016`).
- `durable correction promotion act` — `UNKNOWN` (`UNK-004`).
- `KPI coverage and promotion threshold contract` — `UNKNOWN` (`UNK-005`).
- `ALERT/reassignment execute boundary` — `CONFLICTED` (`UNK-006`).
- `canonical R6 start event` — `CONFLICTED` (`CF-009`, `UNK-007`).
- `canonical R6 end event` — `UNKNOWN` (`UNK-008`).
- `ready-age versus in-vehicle R6 relationship` — `CONFLICTED` (`UNK-009`).
