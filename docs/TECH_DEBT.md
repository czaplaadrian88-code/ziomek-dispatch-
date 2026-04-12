# TECH DEBT — Dispatch v2

Prowadzony na bieżąco. Wszystko co wymaga naprawy ale nie blokuje bieżącego priorytetu. Sprzątanie na koniec dnia.

## P0 — BLOKERY SHADOW DISPATCHER

- [ ] **sla_tracker nie konsumuje eventów** — delivered: 0 mimo 97 COURIER_DELIVERED w event_bus. Diagnoza: cursor/mark_processed, może inny path do events.db, może błąd w event_type filtrze.
- [ ] **Picked_up vs assigned w reconcile** — panel HTML nie rozróżnia statusu 3 vs 5 (brak data atrybutu), więc state nie wie kiedy kurier odebrał. Shadow time_penalty scoring potrzebuje `picked_up_at`. Rozwiązanie: próbkowanie 5 assigned orderów per cykl, jeśli `dzien_odbioru is not None` → emit PICKED_UP.
- [ ] **Backfill starych ~80 orderów z address_id=None** — istniejące assigned sprzed patcha watcher enrichment. Shadow pomija z MISSING_COORDS. Jednorazowy skrypt iterujący state i fetch_order_details.

## P1 — JAKOŚCIOWE

- [ ] **Dead code _diff_and_emit** — sekcja "zniknął z HTML" linie ~172-215, nigdy nie strzela bo panel trzyma wszystko. Usunąć po potwierdzeniu że reconcile stabilny (kilka dni).
- [ ] **kurier_piny.json niekompletny** — brakuje "Grzegorz" (bez W), panel go ma w operacji. Ręczne uzupełnienie albo auto-sync z panelu przez parser courier_packs.
- [ ] **MAX_BAG_SIZE=4 za mało** — Gabriel dziś 5/4 (dwie fale). Podnieść do 6 lub zmienić feasibility żeby nie odrzucał na bag_size >= max, tylko scoring dał 0 pkt.
- [ ] **orders_state.json — brak klucza wrapującego `orders`** — state top-level dict zamiast `{orders: {}, metadata: {}}`. Refactor wymaga migracji pliku.

## P2 — NICE TO HAVE

- [ ] **Git init dispatch_v2/** — dziś patche przez manual backup .bak-*, refactor na git commity dla diff+rollback
- [ ] **PWA GPS z PIN-em** — Dzień 7-8, osobny projekt, zastępuje GPSLogger per-telefon. Scenariusz A z pomysłu Adriana.
- [ ] **Trzy Po Trzy Sienkiewicza [190] vs Ramen Base [162]** — koordynaty różnią się o ~20 m, whitelist HARD niepotrzebnie dodana. Drobiazg kosmetyczny.
- [ ] **Pole `address.id == id_address` dla Nadajesz.pl [161]** — firmowe placeholder, skipowane przez bootstrap. Nie krytyczne.
- [ ] **state_machine.update_from_event** — explicit whitelist per event_type, dodawanie nowych pól wymaga patcha w N miejscach. Refactor na `**payload | known_overrides`.
- [ ] **prep_variance dla 26 brakujących restauracji** — meta ma 27, panel ma 53. Domyślnie 5 min, do dopisania ręcznie dla czasówek najczęstszych.
- [ ] **Panel Mama Thai ma błędny adres Kopernika 2** — rzeczywiście Kaczorowskiego 14 (rog budynku). Manual override w bootstrap zadziałał, ale źródłowy panel też do poprawy.
- [ ] **Bug w geocoding._normalize** — regex `r'/[^\s]+'` usuwa wszystko po pierwszym `/`. Dziś nie clashuje (53/53 unikalne), ale bomba zegarowa dla przyszłych duplicatów w restauracjach.

## NOTATKI OBSERWACYJNE (nie debt, ale do pamięci)

- Panel zwraca WSZYSTKIE ordery dnia w jednym HTML (467 dziś), rozróżnienie active/closed przez obecność `data-idkurier` w bloku
- `data-address_from/to` w HTML dla każdego orderu → darmowy lookup pickup/delivery adresów bez fetch_order_details
- GPS coverage dziś 3/12 realnych kurierów — fallback last-click z `dzien_odbioru`+`czas_doreczenia` rozwiązuje problem
- Kurierzy bez GPS teraz (11.04): Adrian R, Gabriel, Grzegorz W, Paweł SC, Mateusz Bro, Dariusz M, Michał Ro, Grzegorz, Koordynator
- `courier_packs` z parse_panel_html = ground truth dla bagów (state ma fikcję rozwiązaną przez reconcile)
- Rush hour Białystok 17-22, dziś zaczęli ok. 16

## KOREKTA scoring time_penalty (decyzja Adriana 11.04)

- [ ] **time_penalty próg 20→30 min**: obecna formuła `t = (oldest-20)/15` zbyt agresywna
  - Kurier z bagiem 25 min jest OK do dorzucenia paczki z tej samej fali
  - Nowa formuła: `t = clamp((oldest-30)/5, 0, 1); penalty = (t**2.5)*100`
  - Do 30 min: penalty = 0 (zero kary)
  - 30-35 min: stroma krzywa, pełna saturacja przy 35 min
  - Do zmiany przy włączaniu scoring.py do shadow pipeline (jutro)

## ODŁOŻONE 11.04 WIECZOREM (Blok 2 Shadow) — DO NAPRAWY PO SHADOW LIVE

### P0 - ważne ale nie blokujące shadow dzisiaj

- [ ] **courier_resolver fallback priority bug** — dla kuriera który ma jednocześnie delivered i aktywny bag (assigned/picked_up), fallback bierze last_delivered zamiast pozycji aktywnego baga. k400 ma bag=4 ale src=last_delivered. Fix: sprawdzić najpierw `picked_up_at` wśród bag orderów, potem `assigned_at`, dopiero potem szukać delivered.
- [ ] **Test leakage dry-run → prod state** — testy reconcile dołożyły T002/T003 do realnego orders_state.json bo istniejąca sekcja watchera "zniknął z HTML" wołała real update_from_event przez nie zamockowaną ścieżkę. Dziś cleanup ręczny, jutro porządne mock environment z tmpfs state.
- [ ] **Dead code w panel_watcher._diff_and_emit** — stara sekcja "zniknął z HTML" linie ~172-215, nigdy nie strzela w prod bo panel trzyma wszystkie ordery. Usunąć po 2-3 dniach stabilnego reconcile.
- [ ] **Edge case: order picked_up+delivered w jednym cyklu** — picked_up reconcile nie zdąży go złapać, sla_log ma null picked_up_at. Występuje dla ~3% orderów. Fix: jeśli delivered reconcile widzi order bez picked_up_at, robi dodatkowy emit COURIER_PICKED_UP z timestamp=dzien_odbioru.
- [ ] **gps_positions.json klucze = imiona, nie courier_id** — istniejący watcher Traccar zapisuje imiona kurierów jako klucze. Shadow ignoruje dziś (bo dane >3h), ale to blokuje prawdziwy GPS fallback. Fix: tabela lookup imię→id, albo migracja watchera na courier_id jako klucz.
- [ ] **GPS wszystkich kurierów dziś stare (>3h)** — jedyny świeży Bartek O. 14:03 (3h temu), reszta z 10.04. Traccar watcher prawdopodobnie nie uruchomiony dziś albo kurierzy nie mają GPSLogger aktywnego. **Do uruchomienia na Dzień 2 PWA GPS projektu.**
- [ ] **MAX_PICKUP_REACH_KM=15 w feasibility** — może być za mały dla Łap (18 km). Dziś test 5 pokazał że Łapy jako delivery przechodzą (pickup w centrum OK), ale jeśli pickup byłby w Łapach, feasibility by go odrzuciło. Możliwy refactor: per-order flag skip pickup reach check.

### P1 - jakościowe do post-stabilizacji

- [ ] **test_scoring_scenarios.py: 4 wystąpienia `check_feasibility` po usunięciu importu** — dead code w testach, do ręcznego cleanup. Usuń scenariusze 1-3 które używały starego API.
- [ ] **orders_state.json brak klucza wrapującego `orders`** — top-level dict. Refactor wymaga migracji pliku. Niewielki problem ale bolałby przy dodawaniu metadata.
- [ ] **state_machine.update_from_event — explicit whitelist per event_type** — dziś każdy dodany pole wymaga patcha w N miejscach. Refactor na declarative spec: {event_type: [fields_to_update]}.
- [ ] **utility module `coords.py`** — load_coords_as_latlng(), używany przez panel_watcher, backfill scripts, shadow. Zamiast duplikowania konwersji dict→tuple w każdym miejscu.
- [ ] **scoring.py direction check obok SLA simulation** — `s_kierunek` jest teraz osobną składową (waga 0.25) chociaż feasibility_v2 już liczy pełną trasę. Redundancja albo feature? Przemyśleć po pierwszych shadow decisions.
- [ ] **SLA violation "over by 0.5 min"** powinien być soft reject, nie hard — delivery 35.5 min vs 35 min to w praktyce SLA OK. Rozważyć bufor 2 min w feasibility.

### P2 - zaplanowane, mniejsze priorytety

- [ ] **On-route pickup bundling** — kurier jadący A→deliveryA po drodze bierze B (detour <1.5 km). Feature scoringu do dodania po pierwszych shadow decisions.
- [ ] **traffic_multiplier kalibracja empiryczna** — tygodniowo z sla_log.jsonl porównać actual_time / osrm_estimate, uaktualnić stałe MULT_PEAK/SHOULDER.
- [ ] **prep_variance dla 26 brakujących restauracji** — meta ma 27, panel 53. Domyślnie 5 min, dopisać operacyjnie.
- [ ] **bug geocoding._normalize regex `/[^\s]+`** — usuwa wszystko po `/`. Dziś OK (brak clash) ale bomba na przyszłość.
- [ ] **git init dispatch_v2/** — manualne .bak-* backupy. Zrobić po stabilizacji shadow.
- [ ] **PWA GPS z PIN-em** — projekt Dzień 7-8, zastępuje GPSLogger.

### P3 - monitoring i metryki (post-shadow)

- [ ] **Dashboard dispatcha** — ile decisions/godz, rozkład feasibility verdicts, rozkład scoring totals, % unique winners
- [ ] **Shadow vs Koordynator diff report** — jutro porównanie decisions shadow z realną decyzją koordynatora
- [ ] **Alerty Telegram** — gdy shadow proposes NIE dopasowany do rzeczywistości >2x pod rząd

## PARAMETRY BIZNESOWE (Adrian 11.04 17:45)

### KPI operacyjne (cele optymalizacji)
- **Throughput:** 3+ zlecenia/godzinę/kurier (tylko przy dobrej optymalizacji)
- **Bag size operacyjny:** 2-4 zleceń w fali (zależy od adresów)

### Mechanika "35 min SLA"
- SLA 35 min liczy się **od rzeczywistego picked_up_at**, nie od pojawienia się ordera
- Restauracja dostaje **zwrotkę z czasem odbioru** po przypisaniu → synchronizuje produkcję
- Dispatcher może "wrzucać zlecenie za 25 min" jeśli ma plan trasy pokazujący że kurier będzie za 25 min
- **Jedzenie nie stygnie** bo restauracja produkuje pod ETA kuriera, nie od razu
- **Implikacja:** shadow feasibility nie ma ograniczenia "pickup age" — tylko picked_up→delivered ≤35min

### "Fala dokleja się do końca poprzedniej"
- Nie czekamy aż fala X skończy żeby zacząć X+1
- Gdy kurier ma `remaining_duration` bieżącego baga = 15 min, wolno mu wrzucić nowy order Z którego pickup jest "po drodze" (5 min od last_delivery do pickup_Z)
- Warunek: przy przypisaniu **któryś inny kurier nie może być tam szybciej**
- Feasibility robi jeden tryb: "dokończ bag → pusty ruch → pickup/delivery Z" z hard constraint 35 min per order

### Kryterium wyboru kuriera dla nowego orderu (dispatcher logic)
- "Najbliżej czasowo" = min ETA dla pickup_Z wśród wszystkich feasible kurierów
- Tie-break: kto optymalizuje najlepiej całą trasę (min total_duration delta)
- Future (tech-debt): throughput-aware bonus dla kurierów w tempo 3+/h

### Do wyjaśnienia (rozmowa Adrian 11.04 ~17:45)
- Dokładny flow: klient → restauracja → panel → koordynator → kurier
- Moment "przypisania" w panelu (czas_odbioru_timestamp?) — jak się fizycznie ustawia "za 25 min"
- Zwrotka do restauracji — co widzi restauracja po przypisaniu
- Czasówki >60 min vs zwykłe ordery — różny flow?
- Aplikacja kuriera / jak kurier dostaje informację o przypisaniu
- Koordynator id=26 jako bucket czasówek — jak fizycznie działa

## P0.3 DISCOVERY (12.04)

- 12 kurierów w produkcji ma picked_up ordery bez delivery_coords
- Przykłady z logów: 471 (order 465443), 500 (465453), 511 (465460), 509 (465468)
- Konsekwencja: dla tych kurierów courier_resolver spada do last_assigned_pickup albo last_delivered
- P0.4 krytyczny - bez niego 20%+ dispatchable fleet ma pozycję z fallbacku zamiast z aktualnego ruchu
- P0.4 priorytet: następny po P0.3 (ZAMIAST czekać na harmonogram)
- NIE ZMIENIAJ kolejności — P0.4 pilny

## P0.4 NOTES (12.04)

- Forward-fix only: od teraz NEW_ORDER eventy dostają delivery_coords z geocoding (cache 90%, Google 10%)
- Backfill 80 starych orderów bez delivery_coords — P1 task po Fazie 0 (osobny skrypt, rate limit consideration)
- Geocode failure rate historycznie: 0% (294/294 successful). Przeglądać co miesiąc — jeśli >1% → dodać retry logic (3×20s)
- Timeout w watcher: 2s (vs Google default 5s). Burst 5 orderów × 2s = 10s max (cykl 20s OK)
- Architektura: timeout parametryzowany w geocoding.geocode() (nie ThreadPoolExecutor) — zero race conditions, zero zombie threads

## NATĘŻENIE jako P1 feature

- [ ] **Ziomek ustawia natężenie automatycznie** na podstawie `avg_load_per_courier`
  - `< 2.5` → małe, `2.5-4.5` → średnie, `> 4.5` → duże
  - Hysteresis: minimum 5 min na aktualnym poziomie przed zmianą
  - Update co 2 min
  - Endpoint w panelu do zmiany natężenia — do znalezienia (grep wokół "Natezenie" w panel HTML/JS)
  - Dzisiaj: tylko obserwacja i logging do `natezenie_history.jsonl`, nie zmienia globalnej zmiennej
  - Jutro: aktywne ustawianie po weryfikacji że Ziomek predykcje zgadzają się z operatorem

## P0.5 NOTES (12.04)

- Kalibracja: HAVERSINE_ROAD_FACTOR_BIALYSTOK=1.37 (206 delivered orders, median=1.371,
  std=0.354, P10-P90: 1.197-1.825). Raw data: dispatch_state/calibration_20260412_baseline.json.
  Histogram peak 1.08-1.60 (81% samples). Outliers (top 5 factor) = krótkie trasy <1km
  w centrum Białegostoku (jednokierunkowe uliczki). Walidacja fizyczna: długie trasy
  (Łapy 8-9km) → factor 1.08-1.12 → asymptotycznie do 1.0 (drogi proste poza miastem).

- 4 warstwy architektury: traffic-aware speeds (5 bucketów), empiryczny road factor,
  circuit breaker (3×fail → 60s skip), hourly metrics (INFO log co godzinę, nie
  spam warningów).

- Flaga per-cell: osrm_fallback + osrm_circuit_open + time_bucket (dla debugowania).
  Shadow dispatcher Fazy 1 będzie mógł alertować "decyzja z >X% fallback legs =
  niepewna".

- route() i table() zmieniły kontrakt: nigdy nie zwracają None (zawsze dict/list).
  Istniejący kod robi "if result is None → crash", teraz dostanie fallback zamiast
  None. Regresja zero - route_simulator i feasibility import OK.

P1 BACKLOG z P0.5:
- Multi-city calibration: HAVERSINE_ROAD_FACTOR_WARSZAWA przy ekspansji (inne miasto
  = inny grid uliczny = inny factor). Uruchom calibrate_road_factor.py z Warsaw
  orders.
- Circuit threshold kalibracja po pierwszym realnym OSRM outage. Obecnie 3 fails /
  60s cooldown - może być za ostre lub za luźne.
- Telegram alert gdy >10% fallback rate w godzinie (_osrm_stats analysis). Dopisz
  w Faza 4 gdy telegram_bot gotowy.
- Ewentualnie rozszerzenie bucketów speed jeśli dane pokażą że mamy np. piątek
  15-17 ≠ środa 15-17 (różne korki weekday).
- Backfill starych orderów bez delivery_coords - nadal P1 task po Fazie 0
  (unchanged from P0.4 notes).
