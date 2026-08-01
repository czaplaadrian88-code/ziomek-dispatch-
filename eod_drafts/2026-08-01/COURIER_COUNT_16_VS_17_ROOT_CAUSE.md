# Diagnoza root cause: licznik dostaw 16 zamiast 17

Data diagnozy: 2026-08-01 UTC

Zakres: read-only, bez zmiany kodu, danych runtime, flag, usług i APK

Gate: `courier.delivered-count-16-vs-17-20260801`

## Werdykt

Licznik nie pomylił się arytmetycznie. Jedna faktycznie wykonana dostawa
została wcześniej sklasyfikowana przez panel jako `status_id=9` (anulowane).
`panel_watcher` zamienił ją na `ORDER_RETURNED_TO_POOL`, a kanoniczny writer
`state_machine` ustawił `status=returned_to_pool` i wyczyścił `courier_id`.
Courier API liczy wyłącznie rekordy z jednoczesnym `status=delivered`, zgodnym
kurierem i dzisiejszym `delivered_at`, więc ten kurs nie mógł wejść do wyniku.

To jest rozjazd kontraktu lifecycle/rozliczenia wykonanej pracy u źródła, nie
błąd etykiety w Androidzie, cache, strefy czasowej ani tożsamości kuriera.

## Dowód konkretnego przypadku (bez PII i bez surowego ID zlecenia)

Robocza etykieta przypadku: `ORDER-MISSING-01`.

1. Aplikacja zapisała ręczne `odebrane` o `2026-08-01 14:31:34 Europe/Warsaw`.
2. O `14:39:50` panelowy tor `panel_diff` zobaczył `status_id=9` i trwale
   zastosował `ORDER_RETURNED_TO_POOL` z `reason=cancelled`; state i downstream
   mają status `applied`, bez błędu.
3. Mimo tego GPS kuriera po odbiorze dotarł pod zachowany adres dostawy:
   pierwsze wejście w 150 m o `14:53:19`, minimum `10.6 m` o `14:53:55`,
   sześć poprawnych punktów w promieniu 150 m do `14:59:05`; dokładność
   najlepszego punktu około `6 m`.
4. Najbliższy adres innego zlecenia tego kuriera był oddalony o `456.3 m`,
   więc postój 10.6 m od celu przez blisko sześć minut nie jest wyjaśniony
   doręczeniem innego kursu.
5. W chwili zgłoszenia `orders_state.json` zawierał 16 dzisiejszych rekordów
   spełniających filtr licznika. `ORDER-MISSING-01` miał `returned_to_pool`,
   `courier_id=null`, brak `delivered_at`; 16 + potwierdzony brakujący kurs = 17.

Jawna informacja ownera, że kurs został dowieziony, jest tu prawdą biznesową;
GPS niezależnie i bardzo silnie potwierdza przejazd oraz postój przy celu, ale
sam wjazd w geofence nie jest utożsamiany z fizycznym handoffem klientowi.

Po zgłoszeniu doszła osobna kolejna dostawa: panel nadał jej czas `20:22:40`,
a watcher zastosował `COURIER_DELIVERED` o `20:23:05`. Dlatego późniejszy
snapshot licznika pokazuje już 17. To nie naprawiło `ORDER-MISSING-01`; według
tego samego śladu operacyjnego bieżąca suma jest nadal zaniżona o jeden.

## Łańcuch przyczynowy i mapa kompletności

| Miejsce | Rola | Writer / consumer | Dotknięte | Dowód |
|---|---|---|---|---|
| panel NadajeSz | źródło lifecycle | writer statusu `9` | TAK | event `panel_diff`, `reason=cancelled` |
| `panel_watcher.py` — disappeared/reconcile | translator | `9 -> ORDER_RETURNED_TO_POOL` | TAK | oba bliźniacze tory mapują 9 na cancelled |
| `state_machine.py` | kanoniczny writer `orders_state` | ustawia `returned_to_pool`, czyści kuriera | TAK | postimage live + applied outbox |
| `panel_watcher.py` — kolejne cykle | recovery consumer | pomija `returned_to_pool` jako terminalny | TAK | główny diff i reconcile nie próbują późniejszego delivered |
| `courier_status_events` / `status_store.py` | staging aplikacji | statusy aplikacji nie zapisują panelu gastro | TAK | kontrakt modułu; dla przypadku brak statusu 7 |
| `courier_ground_truth.json` | pomiar | pickup/GPS są measurement-only | TAK | potwierdza odbiór i przejazd, ale nie zmienia lifecycle |
| `courier_orders.build_delivered` | kanoniczny filtr licznika | wymaga kuriera + `delivered` + dzisiejszego czasu | TAK | przypadek odpada na dwóch pierwszych warunkach i czasie |
| `courier_orders.build_summary` | agregator | `count = len(delivered)` | TAK | arytmetyka poprawna dla błędnego zbioru wejściowego |
| `/api/courier/orders` | serializer | przekazuje `summary` bez korekty | TAK | backend jest jedynym źródłem liczby dla klienta |
| `RouteStore.applyServerRoute` | cache Androida | zapisuje server `summary` | TAK | brak lokalnego przeliczenia kursów |
| `FinanceScreen` | UI | pokazuje `route.summary.count` | TAK | renderuje dokładnie zaniżony backendowy wynik |
| `earnings_history.record_day` | historyczny writer | utrwala ten sam rollup | TAK | historyczny kafel dziedziczy ten sam błąd |

## Odrzucone hipotezy

- Tożsamość: assignment, eventy aplikacji, GPS i lifecycle wskazują ten sam
  kanoniczny identyfikator kuriera.
- Data/TZ: wszystkie porównywane dostawy i filtr dnia są w poprawnym dniu
  `Europe/Warsaw`.
- Duplikat: brakujący cel jest przestrzennie odrębny od innych kursów.
- Cache/aplikacja: późniejszy poll odświeżył snapshot, lecz nie przywrócił
  brakującego kursu; zmieniła się tylko liczba przez nową dostawę.
- Błąd `len()`: agregator liczy prawidłowo; wadliwy jest upstreamowy zbiór.

## Co jest źródłową naprawą (nie wykonano w tej diagnozie)

Nie wolno dopisać `+1` ani override'u w UI. Trzeba rozdzielić i kanonicznie
związać dwa fakty: terminalny wynik zamówienia oraz kredyt za pracę kuriera.
Status `cancelled` po potwierdzonym pickupie nie może bez śladu kasować
kuriera z rozliczenia, gdy owner potwierdza wykonanie, a niezależne dane
operacyjne są z tym zgodne.

Przed implementacją wymagane są: decyzja jednego ownera kontraktu kredytu,
pełna mapa statusów 7/8/9 i ścieżek recovery, negatywny oracle dla
`picked_up -> cancelled -> physical delivery`, mutation test oraz ratchet
blokujący drugi writer/fallback. Ewentualna korekta historycznych danych,
deploy i restart pozostają osobnymi operacjami live wymagającymi ACK.

## Operacje wykonane

- tylko odczyt kodu, stanu JSON, SQLite w trybie read-only, GPS i durable outbox;
- zero modyfikacji danych runtime;
- zero deployu, restartu, flipa flag i wysyłki;
- PII, adresy, koordynaty i surowe ID zlecenia nie zostały zapisane w raporcie.
