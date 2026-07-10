# Współbieżność, stan i awarie

## Wzorce pozytywne

- stan orderów: wspólny lock RMW, atomic replace i guard regresji;
- plany: lock i CAS w głównych writerach dispatchera;
- geocode: stały lockfile oraz merge pod blokadą;
- JSONL: append pod lockiem, czytniki tolerują torn tail;
- SQLite: WAL i timeouty.

## Otwarte klasy

1. `SPRI-02/DANE-01`: writer planu w panelu jest poza mapą CAS dispatchera.
2. `manual_overrides`: odzyskany audyt podał sprzeczne wnioski o liczbie writerów;
   wymaga jawnej mapy wszystkich procesów przed kwalifikacją.
3. Effects buffer odsuwa efekty do końca decyzji; twardy kill przed flush może je
   zgubić, a read-after-write w tej samej decyzji wymaga procesowego mirrora.
4. Process-local EWMA/cache/CB nie jest wspólnym stanem świata; różne procesy
   mogą widzieć inną historię mimo identycznego ordera.
5. `copytruncate` ma niezerowe okno utraty appendów; rotację kanonicznego ledgera
   należy docelowo uzgodnić z writerem.

## Scenariusze awarii do przyszłych reprodukcji

- writer A odczytuje plan V, writer B zapisuje V+1, A próbuje zapisać;
- panelowy ręczny route ściga się z pickup/deliver i nie może wskrzesić stopu;
- kill po decyzji, przed flush efektów;
- rotacja JSONL podczas intensywnego appendu;
- restart procesu między zewnętrznym submit a zapisem dedup mostu.

W tym audycie nie wykonywano kill/fault injection na live. Reprodukcje z indeksu
25 muszą użyć tmp state i anonimowych fixture.
