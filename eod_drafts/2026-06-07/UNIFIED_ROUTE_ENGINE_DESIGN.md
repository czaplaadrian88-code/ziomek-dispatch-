# Zunifikowany silnik trasy — projekt + STATUS WDROŻENIA (2026-06-07)

## ✅ STATUS KOŃCOWY (2026-06-07 wieczór) — F1–F6 + F4b LIVE
Wszystko za flagami, shadow-first, replay+ACK per faza. Backupy `.bak-pre-*`.
- **GPS-free anchor** — LIVE (drop-in `gps-free-anchor.conf`). Kotwica czasowo-zdarzeniowa, świeży GPS pierwszeństwo.
- **F1** `ENABLE_PLAN_REAL_PICKED_UP_AT` (commit `010a0b2`) — realny picked_up_at → R6 chroni niesione. LIVE.
- **F2** `ENABLE_PLAN_SEQUENCE_LOCK` (commit `5cb837c`) — `bag_signature`; tick re-czasuje, decyzja tylko na zmianę worka. LIVE (koniec oscylacji: TICK2 0 decyzji/11 re-czasowań).
- **F3** `ENABLE_IMMEDIATE_REDECIDE_ON_OVERRIDE` (commit `0e85139`) — `redecide_courier` z panel_watcher. LIVE (potwierdzone `REDECIDE_ON_OVERRIDE cid=409` 1s po override).
- **F6** `ENABLE_PLAN_CANON_ORDER_INVARIANTS` (commit `a48d995`+`5e221ac`) — twarde niezmienniki (carried-first + committed) w DECYZJI i RE-CZASOWANIU + `_retime_stops`. LIVE. **DOWÓD: kanon==build_view 7/7 (0 rozjazdów); cid 503 niesione doręczone pierwsze.**
- **F4a** (repo courier_api, commit `1317abf`) — `GET /api/eta/orders` + `build_eta_map()`. Impl, NIE flipnięty (brak konsumenta; F4b czyta plik wprost).
- **F4b** (repo nadajesz_clone, commit `1fcc0a7`) — `canon_eta.py` + overlay w `list_deliveries`. LIVE (`PANEL_ENABLE_ZIOMEK_CANON_ETA=1`); 0 dopasowań do go-live mostu (DISPATCH_PUSH_LIVE=0).
- **F5** `BUILD_VIEW_TRUST_CANON_ORDER` (repo courier_api, commit `e8dbfab`) — ZBĘDNE po F6 (reorder build_view = no-op na poprawnym kanonie). Zostaje OFF, bez restartu courier-api.

**Efekt:** apka = kanon = panele, jedno źródło prawdy, poprawna kolejność (carried-first + odbiory wg committed). Rollback: flagi `=0` w drop-inach + daemon-reload (+restart panel-watcher).

---

# Zunifikowany silnik trasy — projekt (2026-06-07)

## Cel (słowa Adriana)
Trasa, którą ustala Ziomek, jest **jedynym źródłem prawdy** — tą samą widzi
koordynator (Telegram), kurier (apka) i restauracja (panel, ETA per zlecenie).
Ziomek **zawsze** układa trasę dla FAKTYCZNEGO worka — czy sam zaproponował, czy
worek złożył koordynator (override). Decyduje **raz na każdą zmianę worka**;
między zmianami tylko przesuwają się czasy (kurier jedzie / spóźnia się), punkty
NIE. Dziś (shadow) trasy nie są używane operacyjnie, ale mają być coraz lepsze i
gotowe na tryb autonomiczny.

## Stan zastany (zweryfikowany 2026-06-07) — trzy rozjechane prawdy
| Powierzchnia | Źródło ETA | Co to jest |
|---|---|---|
| Koordynator / Telegram | `pending_proposals[oid].decision_record.best.plan` (ortools, realny `picked_up_at`, bramka feasibility R6 35min) | **Trasa Ziomka** ✓ |
| Kurier / apka | `courier_plans.json` → `courier_api.build_view` | **plan_recheck `incremental`** — re-optymalizuje co tick, `picked_up_at=None` → R6 nie chroni niesionego → defer + oscylacja |
| Restauracja / panel-klon | adapter `route_estimate` OSRM + **płaskie +14 min**, zamrożone przy zamówieniu | **Naiwna heurystyka klonu** (ani Ziomek, ani plan_recheck) |
| Restauracja / realny gastro | brak forward-ETA; tylko `czas_kuriera` (committed odbiór) + reaktywne stemple | brak |

Liczby dnia: `_save_plan_on_assign` 1× / `invalidate_on_bag_change` 47× / `BAG_PLAN_GENERATED` 747×.
Wniosek: trasa Ziomka prawie nigdy nie jest utrwalana ani pokazywana; plan_recheck i klon liczą po swojemu.

## Diagnoza rdzenia
Silnik trasy (`route_simulator_v2.simulate_bag_route_v2`) jest **już wspólny** dla
propozycji i plan_recheck. Rozjazd bierze się z trzech rzeczy, NIE z samego solvera:
1. **INPUT**: plan_recheck buduje `OrderSim(picked_up_at=None)` (l.429), propozycja
   `_bag_dict_to_ordersim` daje realny `picked_up_at=parse_panel_timestamp(...)`
   (l.1661). Bez `picked_up_at` kara R6 deadline (`route_simulator_v2:1030-1040`)
   robi `continue` → niesione jedzenie bez deadline → defer.
2. **CADENCJA**: plan_recheck **re-optymalizuje SEKWENCJĘ co 5 min** (`near_pickup_regen`
   nawet dla pełnego ważnego planu) → oscylacja carried-first↔last. Powinien
   re-optymalizować tylko **przy zmianie worka**, między zmianami tylko **re-czasować**.
3. **WIELOŚĆ ŹRÓDEŁ**: trzy powierzchnie czytają trzy różne wyliczenia.

## Zasada projektowa: jeden kanon, dwa tryby aktualizacji
**Kanon = jeden obiekt trasy per kurier w `courier_plans.json`** (już współdzielony
store czytany przez build_view). Pisze go JEDEN silnik. Dwa tryby:
- **DECYZJA SEKWENCJI** (event = zmiana worka): Ziomek układa kolejność **raz**,
  dobrym obiektywem (realny `picked_up_at`, R6, carried-first). Zamraża.
- **RE-CZASOWANIE** (tick 5min): przelicza `predicted_at` **wzdłuż zamrożonej
  sekwencji** (OSRM od bieżącej kotwicy przez stałe stopy + clamp committed/picked_up).
  Punkty bez zmian. Bumpuje `retimed_at`, NIE wersję sekwencji.

To zabija oscylację (identyczny worek → tylko re-czasowanie) i rozjazd (jeden writer).

## Architektura

### A. Obiekt trasy (rozszerzenie schematu `courier_plans.json`)
Nowe pola w body planu:
- `bag_signature`: posortowany set aktywnych `order_id` w chwili decyzji sekwencji.
  Zmiana sygnatury = zmiana worka = trigger decyzji.
- `sequence_source`: `proposal` | `coordinator_regen` | `cold` (telemetria/audyt).
- `sequence_locked_at`: kiedy ostatnio decydowano kolejność.
- `retimed_at`: kiedy ostatnio przeliczono czasy (≠ sequence change).
- stopy: bez zmian struktury (`predicted_at` re-czasowane, `status_at_plan_time`,
  committed niezmienny).

### B. Jeden silnik — `route_authority` (nowy moduł, cienka fasada nad istniejącym)
```
compute_canonical_sequence(cid, bag_oids, orders_state, anchor) -> plan_body
    # buduje OrderSim z REALNYM picked_up_at (parse_panel_timestamp),
    #   pickup_ready_at = czas_kuriera_warsaw, realne coords;
    # anchor = _start_anchor (GPS-free: świeży GPS → event → committed) [JUŻ JEST];
    # simulate_bag_route_v2(..., earliest_departure=anchor_dep) z dobrym obiektywem
    #   (ENABLE_OBJ_R6_SOFT_DEADLINE=1 — JUŻ w unicie; teraz zadziała bo picked_up_at≠None);
    # zwraca sequence + predicted + pickup_at + bag_signature.

retime_canonical(cid, plan, orders_state, anchor) -> plan_body
    # ZACHOWUJE kolejność stopów z `plan`; przelicza predicted_at łańcuchem OSRM
    #   od anchor przez stałe stopy + clamp committed(pickup)/picked_up(floor);
    # NIE permutuje. Bumpuje retimed_at.
```
`compute_canonical_sequence` to to samo, co dziś robi `_gen_one_bag_plan`, ale z
realnym `picked_up_at` (= input ścieżki propozycji) → ta sama jakość trasy co Telegram.

### C. Model wyzwalania (event vs tick)
**Event (decyzja sekwencji)** — hooki JUŻ istnieją w `panel_watcher`
(`_save_plan_on_assign_signal` / `_invalidate_plan_on_bag_change` na l.845/952/983/1095/1611):
- **propozycja zaakceptowana** (proponowany == przypisany): użyj `best.plan.sequence`
  wprost (to już trasa Ziomka). Zapisz jako kanon z `bag_signature` (napraw coords
  0,0 — dociągnij z orders_state).
- **override / reassign / nowy bez propozycji**: zamiast tylko invalidować i czekać
  na tick → wywołaj `compute_canonical_sequence` **od razu** (Ziomek decyduje na
  każdej zmianie, jak chce Adrian). `sequence_source=coordinator_regen`.
- **pickup / delivery**: zmiana składu worka (dostawa wychodzi; pickup zmienia status
  carried) → re-decyzja reszty raz.

**Tick (plan_recheck 5 min) = TYLKO re-czasowanie + walidacja**:
- jeśli `bag_signature` planu == bieżący worek → `retime_canonical` (czasy świeże,
  kolejność stała). Zero permutacji, zero oscylacji.
- jeśli sygnatura ≠ (przegapiony event / cold) → `compute_canonical_sequence` raz.
- walidacja: usuń stopy zleceń nieaktywnych (jak dziś).

### D. CZTERY czytniki → jedno ETA
Jedna prawda widoczna w czterech miejscach: apka, Telegram, restauracja, panel admin.
- **Apka (kurier)**: `build_view` już czyta `courier_plans.json`. Zostaje czytnikiem.
  Usuwamy hack „carried-first przestawia kafel, ETA zostaje stare" (`courier_orders.py`
  `_prioritize_carried_dropoffs` na ścieżce ziomek_plan) — zbędny, bo kanon już
  front-loaduje niesione i re-czasowanie trzyma ETA świeże.
- **Koordynator/Telegram**: propozycja pokazuje `best.plan` (jest). Na override
  re-decyzja jest tą samą trasą — opcjonalnie wyemituj ją (poza zakresem MVP).
- **Restauracja (panel-klon `/app`)**: zamień naiwne `OSRM+14` (adapter
  `propose_assignment`, frozen przy zamówieniu) na **per-order `predicted_delivered_at`
  z kanonu**. Most istnieje: adapter ma `shadow_quote()` (subprocess venv Ziomka).
  Najprościej: read-endpoint w `courier_api` serwujący per-order ETA z `courier_plans.json`;
  klon czyta zamiast liczyć sam. Restauracja `/app` i operator `/operator` w klonie
  dzielą TO SAMO `Delivery.delivery_eta` → jedna podmiana zasila oba.
- **Panel administracyjny (operator `/operator`)**: POTWIERDZONE z Adrianem 2026-06-07
  = konsola operatora klonu (`Ops09LivePanel`/`LiveMap`). Czyta **to samo
  `Delivery.delivery_eta`** co restauracja `/app` (endpointy `/deliveries`,
  `tracking.py`/`deliveries.py`). Klon NIE ma osobnego widoku trasy ciągnącego
  `build_view`. → **#3 i #4 = JEDNO wpięcie**: podmiana zapisu `Delivery.delivery_eta`
  z naiwnego `OSRM+14` na per-order ETA z kanonu zasila oba panele naraz.
- **Realny gastro**: brak pola forward-ETA — osobny temat. POZA zakresem; odnotowane.

### Cztery powierzchnie — mapa wpięcia (po potwierdzeniu #4)
| # | Miejsce | Dziś | Po unifikacji |
|---|---|---|---|
| 1 | Apka (kurier) | build_view→courier_plans | bez zmian (czyta kanon); usunąć hack carried-first-keeps-stale |
| 2 | Telegram (koordynator) | best.plan | bez zmian (kanon przy propozycji) |
| 3 | Restauracja (klon `/app`) | `Delivery.delivery_eta` = OSRM+14 | F4: ingest per-order ETA z kanonu |
| 4 | Operator (klon `/operator`) | **to samo `Delivery.delivery_eta`** | F4: ten sam ingest (jedno pole, dwa panele) |

F4 wpięcie: read-endpoint w `courier_api` serwujący per-order `predicted_delivered_at`
+ `pickup_eta` z `courier_plans.json` (po mapowaniu zlecenie↔gastro id przez most
papu_dispatch_bridge); klon zasila `Delivery.{pickup_eta,delivery_eta}` z tego źródła
zamiast liczyć adapterem. Pojedyncza podmiana → restauracja i operator spójne z apką
i Telegramem.

### E. Jedyny writer
Wszystkie ścieżki (accept-propozycji, override-regen, tick-retime) idą przez
`route_authority` → `plan_manager.save_plan`. Koniec rozjechanych writerów
(`_save_plan_on_assign` „kadłubowy" + `_gen_one_bag_plan` „incremental” → jeden silnik).

## Niezmienniki (twarde)
1. Sekwencja zmienia się TYLKO na zmianie worka. Identyczny worek → tylko czasy.
2. `compute_canonical_sequence` zawsze dostaje realny `picked_up_at` → R6 chroni niesione.
3. Re-czasowanie nigdy nie permutuje (fail-safe „dostawa po odbiorze” zachowany).
4. Kurier spóźniony/poza trasą → dostawa wychodzi z worka = event = re-decyzja reszty
   („jedzie trasą Ziomka”, a gdy zboczy, Ziomek układa nową — zgodnie z Adrianem).
5. Anchor GPS-free (świeży GPS pierwszeństwo) — JUŻ wdrożone 2026-06-07.

## Ryzyka / edge
- **Brak `delivery_coords`** dla zlecenia → dziś `_gen_one_bag_plan` bailuje cały worek.
  Rozważyć degradację per-stop (stop bez ETA zamiast braku planu). Osobny ticket.
- **Latencja event-regen**: synchroniczna re-decyzja w panel_watcher hot-path — robić
  best-effort/try-except (jak inne hooki), nie blokować ticku panelu.
- **Spójność Telegram↔kanon na override**: propozycja była dla kuriera A, koordynator
  dał B — kanon B liczony świeżo; Telegram pokazał A. To OK (różne byty), ale audyt
  `sequence_source` to rozróżnia.
- **Migracja istniejących planów**: stare `incremental` bez `bag_signature` → pierwszy
  tick policzy sygnaturę i przejdzie w tryb retime.

## Rollout (fazami, shadow-first, ACK per faza, .bak+testy+replay)
- **F0** schemat: `bag_signature`/`sequence_source`/`retimed_at` + read-only liczenie
  sygnatury (zero zmian zachowania).
- **F1** input-unify: `_gen_one_bag_plan` dostaje realny `picked_up_at` → R6 chroni
  niesione. Replay: carried front-load (dowód 17:18→16:41 z 2026-06-07). Flaga.
- **F2** split decyzja/re-czasowanie w plan_recheck: tick re-czasuje przy zgodnej
  sygnaturze, decyduje tylko przy zmianie. Replay: zero oscylacji (sekwencja stała
  między tickami). Flaga.
- **F3** event-driven re-decyzja na override w panel_watcher (hook istnieje). Ziomek
  decyduje natychmiast, nie po 5 min.
- **F4** restauracja: klon czyta per-order ETA z kanonu zamiast `OSRM+14`.
- **F5** sprzątanie: usuń hack carried-first-keeps-stale w build_view; jeden writer.

Każda faza: flaga env (jak `ENABLE_GPS_FREE_ANCHOR`), backup, testy, replay na żywych
workach, ACK przed flipem. Telegram NIGDY restart bez ACK.

## Co już zrobione (fundament pod ten silnik)
- GPS-free anchor (`_start_anchor`) LIVE 2026-06-07 — kotwica czasowo-zdarzeniowa,
  świeży GPS pierwszeństwo. To krok F-bazowy (kanon liczy się bez GPS).
- Zdiagnozowane i potwierdzone: input `picked_up_at`, cadencja, trzy czytniki.
