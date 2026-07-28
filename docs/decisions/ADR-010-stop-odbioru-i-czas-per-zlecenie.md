# ADR-010: tożsamość stopu odbioru i czas per zlecenie

- Status: accepted
- Data decyzji ownera: 2026-07-28
- Owner kontraktu: `dispatch_v2.route_order`

## Problem

Tryb `plan_aware` traktował kolejne pickupy z planu jako jeden podjazd bez
sprawdzenia rozrzutu committed. Dla worka 492 połączył w ten sposób 490836
(21:26) i 490832 (21:52). Powierzchnie prezentacji dziedziczyły jeden czas
fizycznego stopu i ukrywały dokładny czas zlecenia.

## Decyzja

1. Grupowanie fizycznych odbiorów zostaje. Każdy klaster, także pochodzący
   z planu, musi mieć wewnętrzny rozrzut committed nie większy niż stałe
   `PICKUP_MERGE_MIN=10`.
2. `route_order` jest jedynym ownerem membershipu. Stop niesie deterministyczne
   `stop_id` oraz `order_ids`; koordynaty nie uczestniczą w grupowaniu.
3. Stop nie ma prezentowanego czasu grupy. Każdy krok pickup niesie własne,
   nieprzekształcone `committed_at`, a karta zlecenia własne
   `pickup_committed_at`, oba ze źródłowego `czas_kuriera_warsaw`.
4. ETA trasy jest osobnym kontraktem. Wyjazd ze stopu może używać
   `max(arrival, latest_ready) + dwell`, ale nie może nadpisywać committed
   żadnego zlecenia.
5. `live_eta` konsumuje `stop_id` i membership. Nie scala stopów po samych
   koordynatach.

## Skutki i granice

- Zlecenia z tej samej restauracji w oknie do 10 minut nadal mogą tworzyć jeden
  fizyczny stop, lecz każde zachowuje własny wyświetlany czas.
- Zlecenia 21:26 i 21:52 muszą utworzyć dwa pickupy z różnymi `stop_id`.
- Panel backend i courier-api mają delegować do `route_order`; lokalne kopie
  grupowania są wygaszane.
- Android ma konsumować przekazany kontrakt. Bulk-confirm dla różnych
  `stop_id` pozostaje osobnym torem WB3-F1 i nie jest częścią tej zmiany.

## Dowody i rollback

Corpus golden zawiera przypadek 490836/490832 oraz legalną grupę 21:26/21:34.
Oracle mutacyjne czerwienią się po usunięciu guardu, zastąpieniu committed
minimum/maksimum grupy albo scaleniu stopów po koordynatach.

Rollback kodu to rewert tej zmiany we wszystkich trzech backendach i monitora
parytetu. Nie ma migracji danych ani flipu flagi. Deploy ekranów kierowców
wymaga osobnego ACK ownera i wykonania poza peakiem.
