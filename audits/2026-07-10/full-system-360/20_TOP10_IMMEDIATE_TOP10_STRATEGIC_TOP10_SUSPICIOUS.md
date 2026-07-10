# Top 10: natychmiastowe, strategiczne i podejrzane

To jest ranking triage, nie zgoda na zmianę live. `Natychmiastowe` oznacza
„przygotować osobny sprint/dowód”, a nie „flipnąć lub zrestartować teraz”.

## 10 najbliższych działań

| # | Finding | Stan | Pierwszy bezpieczny krok | Bramka |
|---:|---|---|---|---|
| 1 | FEAS-01 | CONFIRMED/P1 | karta konfliktu D3, rozdzielenie raw prediction od fizycznego outcome, replay OFF/ON | decyzja HARD + ACK przed flipem |
| 2 | TEST-11 + TEST-12 | CONFIRMED/P2 | izolować flags+systemd, usunąć historyczny kontrakt `known-open`; pięć script-tests naprawić fixturami, a rzeczywisty live smoke najpierw wydzielić do osobnego nodeid | bez zmiany live; STRICT 0 failed + jawna lista skipów |
| 3 | FEAS-02 | PARTIAL/P2 | fixture dla `None`, sumy worka i per-order; jawny oracle always-propose/ALERT | decyzja semantyki biznesowej |
| 4 | TRAS-01 + TRAS-02 | CONFIRMED/P2 | jeden sprint walidatora i serializacji stops; parity decyzja→plan→apka | deploy/restart dopiero po ACK |
| 5 | DANE-01 | CONFIRMED/P2 | objąć panelowy writer CAS-em i testem race z dispatcherem | cross-repo + deploy panelu |
| 6 | BEZP-02 | CONFIRMED/P2 | wspólny ownership guard i negatywne testy cudzej encji | deploy/restart API |
| 7 | CORE-01 | PARTIAL/P2 | rozdzielić input-miss, OSRM-miss i diff; golden mutation + coverage gate | bez zmiany decyzji w pierwszej fazie |
| 8 | FLAG-01 | CONFIRMED/P2 | podłączyć do `decision_flag()` i udowodnić ON≠OFF; pozostawić OFF | osobny sprint flagi |
| 9 | OPS-02 | CONFIRMED/P2 | projekt `OnFailure` + flow-liveness dla panelu i API | restart usług wymaga ACK |
| 10 | OPS-01 + OPS-05 | CONFIRMED/PARTIAL P2 | provider-side/network preflight i plan drugiej sesji dostępowej | sieć/SSH/restart wymagają ACK |

## 10 działań strategicznych

1. Jeden kontrakt `DecisionInputBundle` nagrywany przed decyzją i odtwarzany bez
   live fallbacków.
2. Jeden kontrakt planu z typowanymi stopami, wersją, provenance i CAS dla
   wszystkich repozytoriów.
3. Rejestr flag obejmujący kanon, carrier, efektywny proces, ownera i termin
   usunięcia historycznego nośnika.
4. Oracle hierarchy: known-answer → mutation tripwire → paired replay → shadow →
   live verdict.
5. Flow-liveness oparty o kanoniczny ledger decyzji, nie wyłącznie PID usługi.
6. Ownership/autoryzacja jako wspólny dependency endpointów API, nie lokalne
   `if` w wybranych handlerach.
7. Rotacja/retencja ledgera uzgodniona z writerem; bez `copytruncate` na źródle
   prawdy.
8. Powtarzalny restore game day z RTO/RPO, decryptem, strict SQL i smoke aplikacji.
9. SBOM/constraints per proces oraz kontrolowane upgrady z replayem i kontraktami.
10. Redukcja proces-globalnego stanu w `core` przez jawny context i bufor efektów
    z mierzalną trwałością.

## 10 podejrzeń, których nie wolno przedstawiać jako fakt

| Finding | Hipoteza | Wymagany dowód |
|---|---|---|
| CORE-04 | snapshot wejścia może powstawać po decyzji | fixture mutujący stan pomiędzy capture i decide |
| CORE-05 | żywe I/O może pozostać w `decide` | jawna mapa call graph + network deny |
| CORE-06 | kill przed flush może gubić efekty | fault injection wyłącznie w tmp state |
| FEAS-06 | kolejna flaga R6 może być env-latched | syntetyczny ON/OFF + consumer trace |
| FEAS-09 | polityka końca zmiany może nie odpowiadać werdyktowi | golden cases przed/po shift end |
| BLIZ-03 | re-timing może pomijać mnożnik tempa | parity na tym samym planie i macierzy tras |
| BLIZ-07 | wygaśnięcie monitora może zostawić ślepą plamkę | sprawdzenie job registry i aktywnego consumera |
| TRAS-05 | propozycja może nadpisywać live ETA | test dwóch writerów w izolowanym state |
| TRAS-07 | deterministyczność może zależeć od peak CPU | kontrolowany benchmark, bez live loadu |
| TRAS-09 | enumeracja może eksplodować dla większego worka | profil stage-by-stage na anonimowym korpusie |

Wszystkie pozycje z ostatniej tabeli pozostają `UNVERIFIED` do czasu wykonania
wskazanego oracle.
