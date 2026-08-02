# TIME-C Faza 1 — kontrakt wersji trasy i ETA

## Przyczyna źródłowa

`live_eta_snapshot.json` identyfikował cykl i kuriera, lecz nie identyfikował
planu ani sekwencji stopów, dla której policzono ETA. Readery sprawdzały tylko
świeżość zegarową. Po zapisie nowego planu stary, nadal świeży snapshot mógł
więc zostać nałożony po `order_id` na nową kolejność. To był latentny wyścig
old-ETA/new-plan; nie był rozjazdem algorytmów `route_order`.

## Jeden właściciel kontraktu

Właścicielem jest `dispatch_v2.route_order`:

- domena preimage: `ziomek.route_sequence.v1`;
- element fizyczny: `kind`, posortowany i odduplikowany membership `order_ids`
  oraz wyprowadzony z nich `stop_id`;
- kolejność elementów fizycznych jest zachowana;
- kolejne kroki per-zlecenie opisujące ten sam fizyczny stop są zwijane;
- JSON UTF-8 ma sortowane klucze i separatory `,`/`:`, wynik to SHA-256 hex.

`route_sequence_hash()` istnieje wyłącznie w `route_order.py`. Producent i
konsumenci wołają tę samą funkcję. Wspólna brama
`live_eta.bind_snapshot_to_route()` sprawdza hash i `plan_version`; readery nie
mają własnego porównania ani fallbackowej definicji hasha.

Snapshot pozostaje addytywnie zgodny ze schema v1 i dostaje:

- `plan_version` — wersję aktywnego planu lub `null` bez aktywnego planu;
- `sequence_hash` — hash dokładnie opublikowanej sekwencji stopów.

## Zachowanie flagi

| Stan | Snapshot bez/poprzedniej wersji | ETA powierzchni | Status |
|---|---|---|---|
| OFF | legacy pass-through | bez zmiany wartości względem starego kodu | `unchecked` |
| ON, zgodny | przyjęty | live ETA | `matched` |
| ON, inny hash | odrzucony | istniejący fallback do aktywnego planu albo brak | `sequence_hash_mismatch` |
| ON, ten sam hash, inny plan | odrzucony | istniejący fallback do aktywnego planu albo brak | `plan_version_mismatch` |
| ON, brak kontraktu | odrzucony | istniejący fallback do aktywnego planu albo brak | `unversioned_snapshot` |

Bliźniacze flagi są domyślnie OFF:
`ROUTE_ETA_VERSION_CHECK` (panel) i
`ENABLE_ROUTE_ETA_VERSION_CHECK` (courier-api). Muszą być promowane i cofane
razem, za osobnym ACK ownera i restartem właściwych usług. Faza 1 nie wykonuje
flipa, restartu ani deployu.

## Mapa kompletności

| Miejsce | Rola | Writer/consumer | Dotknięte | Powód / test |
|---|---|---|---|---|
| `route_order.py` | kanon fizycznej sekwencji | owner | TAK | jedna definicja i golden hash; parytet physical↔expanded |
| `live_eta_daemon.build_routes` | budowa trasy wejściowej | writer DTO A | TAK | niesie aktualny `plan_version` i hash dokładnej sekwencji |
| `live_eta.calculate_live_eta/write_cycle` | snapshot/store atomowy | writer DTO B | TAK | publikuje oba pola; odrzuca podany hash sprzeczny ze stopami |
| `live_eta.read_latest/read_all/eta_for` | surowe readery | consumer | N-D | zgodność wsteczna; brama jest osobnym chokepointem przed `eta_for` |
| `live_eta.bind_snapshot_to_route` | kontrola generacji | owner policy | TAK | OFF pass-through, ON fail-closed; negatywny oracle + mutation |
| `plan_manager.save_plan` | kanoniczny commit planu | writer planu | TAK | log planu oznacza writer role; plan_version już monotoniczny |
| `plan_manager.invalidate/touch/advance/remove/insert/gc` | pozostali mutatorzy planu | writer planu | N-D | już bumpują/modyfikują generację; następny cykl próbuje nowy kontrakt, stary jest odrzucany |
| `panel_watcher`, `plan_recheck`, `dispatch_pipeline` | callery mutatorów | writer pośredni | N-D | delegują do `plan_manager`; brak konkurencyjnego hasha |
| `b_route_shadow`, `bundle_calib_shadow`, replay | plan temp | writer-observer | N-D | przekierowany `PLANS_FILE`; role plan logu rozróżnia `observer` |
| `decision_eta_log.py` | append-only historia | writer metryki | TAK | rekord per order×fizyczny stop×cykl: timestamp/value/version/hash/writer |
| `decision_eta_coverage.py`, kalibracje GPS | istniejący log | consumer metryki | N-D | nowy rekord zachowuje pełny `decision_eta.v1`; źródło nie wchodzi do mianownika decyzji |
| `decision_eta_timeline.py` | historia operatora | consumer | TAK | czyta live + `.N[.gz]`, domyślnie ukrywa observerów, filtr order/kind/since |
| `jsonl_rotation.py` + logrotate config | retencja | boundary | TAK (ratchet) | istniejący log ma daily/maxsize 100M/30 rotacji/compress; rename, bez copytruncate |
| `panel fleet_state.read_fleet` | route-card i worek | consumer | TAK | jedna brama przed overlayem; DTO niesie current/snapshot version/hash/status |
| `panel fleet_state.read_orders` | order-list | consumer | TAK | kontrakt liczony raz per cid na request; cache lokalny tylko w obrębie odczytu |
| `panel canon_eta.canon_eta_map` | tracking, deliveries, history ingest | consumer | TAK | odrzuca mismatch przed projekcją; istniejący plan fallback |
| `panel api/coordinator.py` | endpointy/poll | serializer | N-D | serializuje `read_fleet/read_orders`; brak osobnego ETA writera/cache |
| frontend route-card/order-list | render | consumer UI | N-D | Faza 2; Faza 1 korzysta z istniejącego `planned`/pustego fallbacku i statusu backendu |
| `courier_orders.build_view_from_snapshots` | trasa kuriera | consumer/DTO | TAK | guard po finalnym `stop_sequence`; DTO niesie oba kontrakty i status |
| `courier_orders.build_eta_map` | `/api/eta/orders` | consumer | TAK | guard przed projekcją per-order; plan fallback pozostaje |
| `courier_orders.read_bound_eta` | route-geometry ETA | consumer | TAK | usuwa bezpośredni, niechroniony odczyt z `main.py`; legacy OFF, ON fail-closed |
| `courier_api main.py /api/courier/orders` | REST serializer | serializer | TAK | przekazuje `sequence_hash`, snapshot version/hash i status |
| `courier_api main.py /api/courier/route-geometry` | lekki ETA endpoint | serializer | TAK | korzysta ze wspólnego bound readera, nie z surowego snapshotu |
| `courier_api main.py /api/courier/plan-version` | polling | consumer generacji | N-D | już niesie plan_version; nie serwuje ETA |
| `courier-app CourierApi.kt`, `RouteStore`, `PlanPoller`, UI | Android | consumer | N-D | jawnie poza Fazą 1; Moshi ignoruje nowe pola, obecny kontrakt pozostaje zgodny |
| flag lifecycle seeder/registry/checker | lifecycle | boundary | TAK | dwustronny twin, default OFF, `seed --merge`, checker 0 błędów |
| logrotate runtime install/timer | retencja live | operacja live | N-D/HOLD | źródło konfiguracji istnieje, lecz instalacji live brak; ten sprint ma zakaz live |

## Historia i retencja

Nie powstał drugi log. `record_live_eta_cycle()` rozszerza istniejący
`decision_eta_log.jsonl`. Zapis następuje dopiero po atomowej publikacji nowego
cyklu (nie przy cache-hit tego samego cyklu), używa współdzielonego durable
appendera i nie zawiera nazw, adresów ani współrzędnych.

Źródłowa polityka retencji była już przygotowana: daily, `maxsize 100M`, 30
rotacji, compress+delaycompress i bezstratny rename pod namespace lockiem.
Read-only audyt hosta wykazał jednak brak zainstalowanego timera/konfiguracji.
Instalacja pozostaje jawnym długiem wydaniowym, bo Faza 1 ma zakaz live.

## Rollback kandydata

Przed deployem: oba enforcementy pozostają OFF. Po przyszłym flipie rollback to
ustawienie obu flag na OFF i kontrolowany restart panelu/courier-api za ACK.
Pola snapshotu i DTO są addytywne, więc starsi readerzy je ignorują. Kod można
cofnąć osobnymi commitami/tagami każdego repo; nie ma migracji danych.
