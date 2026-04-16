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

## P0.6 RECON RESULTS (12.04)

### GŁÓWNY WNIOSEK P0.6

Pytanie: czy panel Rutcom zwraca prep_ready_at?
Odpowiedź: NIE. 50 pól w zlecenie + 2 top-level (zlecenie, czas_kuriera) — zero
pól z semantyką "fizycznie gotowe w kuchni". Panel wie tylko deklarację przy
złożeniu + kiedy kurier kliknął "odebrane".
Decyzja Fazy 1: prep_ready_at_estimate = czas_odbioru_timestamp +
prep_variance(restauracja). prep_variance liczymy w P0.7 z historical
dzien_odbioru - czas_odbioru_timestamp per id_address.
Bonus: pole czas_kuriera (top-level, HH:MM) wygląda jak wartość z dropdownu
koordynatora. Weryfikacja semantyki w P1.

**Cel:** ustalić czy panel Rutcom zwraca `prep_ready_at` (moment gdy jedzenie
faktycznie gotowe) w odpowiedzi na POST /admin2017/new/orders/edit-zamowienie.

**Próbka:** 10 orderów, statusy 2/3/5/7, różne restauracje. Dump:
`/tmp/p06_order_details_sample.json`.

**Schema panel response:**
- 2 klucze top-level: `zlecenie` (dict[50]) + `czas_kuriera` (str HH:MM)
- 50 pól w `zlecenie` (UNION ze wszystkich 10 sample)
- Pola zagnieżdżone: `address` (metadata restauracji), `lokalizacja` (strefa miasto)

**DECYZJA: `prep_ready_at` NIE ISTNIEJE.** Zero pól z nazwą
`ready`/`prep`/`gotowe`/`kuchnia`/`kitchen`/`done`. Restauracja NIE komunikuje
panelowi momentu ukończenia przygotowania.

**Jedyne czasowe pola związane z odbiorem:**
- `czas_odbioru` (str int min) — deklaracja restauracji przy zamówieniu ("40 min
  na przygotowanie"). Ustawiana raz, nie aktualizowana.
- `czas_odbioru_timestamp` (Warsaw naive) — `created_at + czas_odbioru min`.
  Planowana godzina odbioru, NIE faktyczna gotowość. Koordynator może ręcznie
  edytować (flag `zmiana_czasu_odbioru`).
- `dzien_odbioru` (Warsaw naive) — FAKTYCZNY pickup (kurier kliknął odebrane).
  None dla new/assigned, filled dla picked_up/delivered.
- `czas_doreczenia` (Warsaw naive) — faktyczny delivered.
- `czas_kuriera` (TOP-level, str HH:MM) = **DEKLAROWANY CZAS PRZYJAZDU KURIERA
  DO RESTAURACJI**. Dwa źródła ustawienia:
  (a) koordynator przy przypisaniu kuriera w panelu głównym wybiera z dropdownu
      5/10/.../60min → staje się `czas_kuriera`
  (b) kurier przy AKCEPTACJI zlecenia na panelu `/admin2017/kurier2` może
      JEDNORAZOWO "przedłużyć" zlecenie (zmienić `czas_kuriera` raz). Po
      akceptacji kurier NIE modyfikuje tego pola ad hoc w trakcie realizacji.

  Ta wartość jest wysyłana restauracji w zwrotce ("kurier będzie o HH:MM").
  Kontrakt z restauracją ±5min liczy się OD `czas_kuriera` (nie od
  `czas_odbioru_timestamp`).

  Obserwacje z 10 sample:
  - 8/10: `czas_kuriera ≈ czas_odbioru_timestamp ±1min` (brak przedłużenia)
  - 465215: `czas_kuriera = czas_odbioru_timestamp +16.05 min` (przedłużenie)
  - 465274: `czas_kuriera = czas_odbioru_timestamp +16.95 min` (przedłużenie)

  Z samego API nie odróżnimy czy przedłużenie zrobił koordynator przy
  przypisaniu, czy kurier przy akceptacji — obie akcje dają identyczny rezultat.

  Historical `(czas_kuriera - czas_odbioru_timestamp)` per restauracja = sygnał
  ile średnio jest przedłużane = **DODATKOWY input dla P0.7 prep_variance**.

**Flagi okołoprepowe (bez realnego contentu dziś):**
- `indywidual_time` (int 0/1) — 1 w 1/10 sample (czasówka 465584, 86 min).
  Hipoteza: flag dla manualnie zatwierdzonych czasówek. Weryfikacja w P1 na
  >50 sample czy koreluje z czas_odbioru >= 60 czy z innym kryterium.
- `zmiana_czasu_odbioru` / `zmiana_czasu_odbioru_kurier` — oba 0 we wszystkich
  10 próbkach. Flagi manualnej korekty, rzadkie.
- `is_odbior_status` (int 0/1) — duplikat `id_status_zamowienia >= 5` (po
  pickup). Redundantne.

**Implikacje dla Fazy 1 (route_simulator_v2 + scoring):**
1. **Real `prep_ready_at` nie do odzyskania z panelu.** Musimy go oszacować
   heurystycznie: `prep_ready_at_estimate = czas_odbioru_timestamp +
   prep_variance(restaurant)`.
2. **P0.7 `gap_fill_restaurant_meta.py` KRYTYCZNY** — bez `prep_variance` per
   restauracja estymata zjada D8 ("kurier czeka"). Filozofia D16: NIE bufor na
   czasówki, ALE alert biznesowy "restauracja X regularnie +N min po
   czas_odbioru_timestamp".
3. **Shadow dispatcher wywoła** route_simulator_v2 z `pickup_ready_at =
   max(now, czas_odbioru_timestamp + prep_variance_restauracji)`. Jeśli
   predicted_arrival < pickup_ready_at → kurier czeka, penalty w scoringu.
4. **Kalibracja `prep_variance`:** per `id_address` restauracji z historical
   `dzien_odbioru - czas_odbioru_timestamp` delta. Wymagane ≥30 delivered
   orderów per restauracja dla wiarygodnej mediany. Restauracje bez
   wystarczającej próbki (sample_n < 30) → `prep_variance = fleet_median`
   (globalna mediana spóźnień jako bezpieczny default). Flag
   `low_confidence=True` dla alertowania w Fazie 1 że dane są prowizoryczne.
   Fallback 0 złamałby D8 od pierwszego dnia dla nowych restauracji.
5. **Bonus dla Fazy 1:** możemy mierzyć `dzien_odbioru - czas_odbioru_timestamp`
   per restauracja live i trigger alert Telegram gdy delta > 10 min przez
   3 ordery pod rząd (D16 data quality).

**P0.7 ACTION ITEMS (unchanged):**
- Napisać `tools/gap_fill_restaurant_meta.py` — policzyć `prep_variance` median,
  P50/P75/P90 per `id_address` z `sla_log.jsonl` + dzisiejsze delivered orders
  (364 w state).
- Meta dict: `{id_address: {prep_variance_min: float, sample_n: int,
  p75_min: float, last_updated: iso}}`.
- Sanity: restauracje z `prep_variance > 15 min` → flag "chronically_late" do
  operacyjnej listy.

**TECH_DEBT z P0.6:**
- [ ] **`indywidual_time=1` jako sygnał czasówki** — pewniejsze niż
  `czas_odbioru >= 60` (V3.1 threshold). Rozważyć w Fazie 1 zamiast threshold.
- [ ] **Zero flag "restauracja już gotowa"** — nie ma way żeby ziomek wiedział,
  że Rany Julek już zawołał kuriera przed `czas_odbioru_timestamp`. Hipotetyczna
  P3 integracja przez restaurant-panel API lub button "gotowe" w panelu.

## P0.7 DONE (12.04)

**Co zrobione:**
- Nowy offline tool: `/root/.openclaw/workspace/scripts/tools/gap_fill_restaurant_meta.py`
  (595 linii, stdlib only, POZA git repo dispatch_v2 zgodnie z V3.2)
- Wygenerowany `/root/.openclaw/workspace/dispatch_state/restaurant_meta.json`
  (115807 B = 113.1 KB, 68 restauracji)
- Source: `/tmp/zestawienie_all.csv` (9 plików panel CSV merged, 24007
  delivered orderów, 76 dni: 2026-01-26 → 2026-04-12)

**Struktura output (clean, zero redundancji):**
```
{
  "restaurants": {
    "<nazwa_restauracji>": {
      "sample_n", "first_order", "last_order", "active", "volume_pct",
      "prep_variance_min": {median, p75, p90, mean, stddev, min, max, sample_n},
      "waiting_time_sec": {median_sec, p75_sec, p90_sec, mean_sec, max_sec,
                           median_non_zero_sec, p75_non_zero_sec,
                           non_zero_count, non_zero_pct, sample_n},
      "extension_min": {median_min, p75_min, p90_min, mean_min,
                        never_extended_pct, extended_count, extended_pct,
                        shortened_count, sample_n},
      "flags": {low_confidence, chronically_late, prep_variance_high,
                unreliable, critical},
      "courier_sample": [top 5],
      "delivery_addresses_sample": [top 5],
      "last_updated": <iso>,
      "prep_variance_fallback_min", "waiting_time_fallback_sec",
      "extension_fallback_min"   // nie-None tylko gdy low_confidence
    }
  },
  "fleet_medians": {
    "fleet_prep_variance_median": 13.0,
    "fleet_waiting_time_median_sec": 0,
    "fleet_extension_median_min": 7.0,
    "source_restaurants_n": 57
  },
  "metadata": {
    "total_delivered_orders": 23607,
    "unique_restaurants": 68,
    "reference_date", "source_csv", "computed_from", "computed_at",
    "min_sample_confident": 30,
    "active_window_days": 14,
    "critical_volume_pct": 5.0
  }
}
```

**Kluczowe findings biznesowe:**

- **68 unikalnych restauracji** (55 z restaurant_coords.json + 13
  zagonionych/niszowych z historii)
- **62 active** (last order ≤14d), **6 inactive**
- **4 critical** (>5% volume):
  - Grill Kebab 9.45% (2231 orderów)
  - Rany Julek 8.85% (2089)
  - Chicago Pizza 6.47% (1527)
  - Rukola Sienkiewicza 5.60% (1322)
  - **Razem 30.37% wolumenu** w 4 partnerach (long tail po to)
- **19 prep_variance_high** (median prep_variance >15 min) — **28% flotu
  kłamie w deklaracji** o średnio 15-29 min
- **11 low_confidence** (sample_n<30) → fleet_median fallback active
- **0 chronically_late** po suppress (Bar Słoneczny, Ziemniaczek miały
  waiting_median>5min ale są low_confidence → suppressed)
- **0 unreliable** po suppress
- **Fleet medians** (z 57 restauracji sample_n≥30):
  - prep_variance_median = **13 min** (typowa restauracja deklaruje 13 min
    za krótko niż realny prep)
  - waiting_time_median_sec = **0 s** (mediana wszystkich czekań = 0)
  - extension_median_min = **7 min** (koordynator typowo przedłuża o 7 min)

**Kluczowy insight: system koordynatora absorbuje prep_variance.**
77.5% orderów zero wait mimo 28% restauracji `prep_variance_high`. Koordynator
aktywnie kompensuje ekstensją `czas_kuriera` (~54% orderów extended >1 min).
Faza 1 Ziomek musi replikować tę kompensację: `pickup_ready_at_estimate =
czas_odbioru_timestamp + prep_variance.median(restauracja)` (lub fallback).

**Flagi (z suppress dla low_confidence zgodnie z R16/R17 filozofia — zero
false-positive alertów na <30 sample):**
- `low_confidence` (sample_n<30) — 11 restauracji
- `chronically_late` (waiting_median>300s AND NOT low_confidence) — 0
- `prep_variance_high` (prep_median>15min AND NOT low_confidence) — 19
- `unreliable` (waiting_p75>600s AND NOT low_confidence) — 0
- `critical` (volume_pct>5%) — 4 (NIE suppressed, volume zawsze wiarygodne)

**Fallback dla low_confidence (sample_n<30):**
- `prep_variance_fallback_min`: 13 (fleet median)
- `waiting_time_fallback_sec`: 0
- `extension_fallback_min`: 7

**Backupy safety net w dispatch_state/:**
- `restaurant_meta.json.bak-PRE-P07-20260412-190421` — V3.1 oryginał (2081 B)
- `restaurant_meta.json.bak-20260412-192448` — 3c smoke test (redundant
  per-restaurant source_csv/computed_from, 123093 B)
- `restaurant_meta.json` — **current clean struct** (115807 B, 113.1 KB)

**Walidacja (7/7 etapów PASS):**
- py_compile OK
- Dry-run: zero write, pełen raport stdout
- Real run: `/tmp/test_meta_clean.json` 115807 B
- Clean struct: per-restaurant 15 kluczy (zero source_csv/computed_from),
  metadata 9 kluczy (ma source_csv + computed_from — provenance raz)
- Production rewrite: auto-backup + atomic write
- Diff verify: 276 linii diff, tylko `last_updated` + `computed_at`
  (zero strukturalnych regresji)
- Readback sanity: 68 restauracji, fleet_medians identyczne z 3c

**TECH_DEBT z P0.7:**
- [ ] **indywidual_time=1 jako sygnał czasówki** — P1, rozważyć zamiast
  threshold `czas_odbioru>=60` (już w P0.6 TECH_DEBT, ale re-raising)
- [ ] **Klucz meta = nazwa+pickup_address dla multi-filia brands** — P2.
  Dziś Grill Kebab (4 filie) agregowany jako jedno wpis → uśredniony
  prep_variance nie odróżnia Barszczańskiej od Ogrodowej. Unblock przy
  ekspansji Warszawy albo gdy Białystok dostanie 2. filię krytycznego
  partnera.
- [ ] **Uzupełnić `restaurant_coords.json` o per-filia entries** — P2.
  Prerequisite dla klucza multi-filia.
- [ ] **Heurystyka filia-detection** — P2. `delivery_coords` klienta →
  najbliższa filia (spośród coords) → użyj per-filia meta.
- [ ] **Naming convention bak files** — standardize `PRE-{patch}` vs
  `AUTO-{ts}`. Obecnie mixed (`.bak-PRE-P07-*` vs `.bak-*`). Drobne.
- [ ] **P0.8 cleanup** — `rm /tmp/p07_test.py /tmp/zestawienie_*.csv
  /tmp/test_meta*.json /tmp/p06_order_details_sample.json /tmp/p07_diff*.txt
  /tmp/p07_analysis.md /tmp/demand_analysis_backup.md`

## P0.8 DONE (12.04) — Final cleanup + meta integration note

**Meta integration note (dla Fazy 1 route_simulator_v2):**

`route_simulator_v2.py` (Faza 1) wczytuje `restaurant_meta.json` przy starcie.
Scoring i PDP-TSP korzystają z `prep_variance.median` per restauracja
dla obliczenia `pickup_ready_at`:

```python
# W route_simulator_v2:
meta = load_restaurant_meta()  # /root/.openclaw/workspace/dispatch_state/restaurant_meta.json

def get_pickup_ready_at(restaurant_name, czas_odbioru_timestamp, now):
    r = meta["restaurants"].get(restaurant_name)
    if r is None:
        # Nieznana restauracja (świeżo onboardowana) → fleet defaults
        prep_variance = meta["fleet_medians"]["fleet_prep_variance_median"]
    elif r["flags"]["low_confidence"]:
        # Za mało sample → fleet fallback
        prep_variance = r["prep_variance_fallback_min"]
    else:
        # Standard case — używamy median restauracji
        prep_variance = r["prep_variance_min"]["median"]

    pickup_ready = czas_odbioru_timestamp + timedelta(minutes=prep_variance)
    return max(now, pickup_ready)
```

Scoring penalty gdy `predicted_arrival < pickup_ready_at`:
- Kurier przyjedzie za wcześnie → będzie czekał → D8 violation
- Penalty proporcjonalny do waiting time

**Restart strategia (po Fazie 1):**
Meta jest plikiem JSON, nie SQL. Reload co N minut (np. 60) w
route_simulator_v2 zapewni że nowo zregenerowane meta (po onboardingu nowej
restauracji) zostanie podchwycone bez restartu systemd.

**Regen cadence:**
- Po onboardingu nowej restauracji (ręczne regen)
- Co tydzień (nightly job? — decyzja dla Fazy 2)
- Przy ekspansji Warszawy (nowy CSV export + per-city meta)

**Co zrobione w P0.8:**
- Archive source CSV → `/root/archive/p07_source/` (10 plików, ~32 MB —
  safety net dla regen restaurant_meta.json)
- Cleanup `/tmp` roboczych plików (p07_test.py, test_meta*, p06/p07
  diff/analysis/draft, demand_analysis_backup)
- Meta integration note dla Fazy 1 (ta sekcja)
- Final snapshots w `/root/backups/` (dispatch_v2 + dispatch_state + tools)

## F1.1 DONE (13.04) — Faza 1 core modules live

**Commit:** `dd73048`

**Co zrobione (5 modułów Fazy 1, live na produkcji):**
- `route_simulator_v2.py` — PDP-TSP z prep_variance
- `feasibility_v2.py` — R1/R3/R8/R20/R27/D8 constraints
- `dispatch_pipeline.py` — scoring + R28 + R29
- `shadow_dispatcher.py` — systemd runner (`dispatch-shadow.service` active)
- `telegram_approver.py` — Telegram listen + learning_log (`dispatch-telegram.service` active)

**Pierwsza propozycja Telegram dostarczona 13.04.2026 ~23:05.**
Shadow mode LIVE, Adrian ręcznie akceptuje decyzje → Ziomek imituje koordynatora.

**Review ref:** D19 FAZA_1_DECYZJA_ARCH.md (greedy hybrid)

---

## F1.1 FOLLOW-UP TECH_DEBT (13.04 po live run)

### P1 — wykryte przy pierwszych propozycjach Telegram

- [ ] **courier_names.json gap** — propozycja pokazuje `K207` zamiast `"Grzegorz W"`.
  State ma courier_id, ale brakuje lookup table id→imię. Fix: wygeneruj
  `courier_names.json` z `kurier_piny.json` albo z panel scrape
  `/admin2017/new/admin/kurierzy`. Blocker: Ziomek wygląda "surowo" w Telegramie.

- [ ] **`shadow_dispatcher._serialize_result` enrichment** — obecny output ma
  tylko courier_id + score + reason. Brakuje:
  - `total_km` (suma dystansu trasy)
  - `eta_delivery_min` (ETA doręczenia)
  - `pickup_address` + `delivery_address` (human-readable, nie coords)
  - `route_stops[]` (kolejność pickup→delivery per order w bagu)
  Telegram message bez tego jest mało informatywny dla Adriana.

- [ ] **GPS lookup: `gps_positions.json` klucze=imiona, nie courier_id** —
  istniejący Traccar watcher zapisuje imiona kurierów jako klucze (legacy).
  Shadow dispatcher czyta courier_id → fail lookup → fallback do
  last_delivered position. Fix: migracja watchera Traccar na courier_id.
  **DEPENDS ON:** courier_names.json (bez tego nie ma way zmapować).

- [ ] **`kurier_piny.json` vs state `courier_id`: różne ID spaces** —
  `kurier_piny.json` ma 4-cyfrowe PIN-y (np. `1234`), state ma 3-cyfrowe
  courier_id z panelu (np. `508`). To są DWA osobne identyfikatory — PIN
  kurier→app login, courier_id panel→state. Rozwiązanie: trzymać oba w
  `courier_names.json` jako `{courier_id, name, pin}` tuple. Bez tego każdy
  fix (GPS, imię w Telegramie) wymaga osobnego mappingu.

### P1 — tydzień 2 dependency

- [x] **PWA GPS z PIN-em** — ✅ DONE 13.04 jako F1.5 (`7af8ce1`). Deployed:
  `dispatch_v2/gps_server.py` (stdlib http.server), `https://gps.nadajesz.pl`
  (nginx + Let's Encrypt cert), PIN auth 4-cyfra z `kurier_piny.json`,
  `gps_positions_pwa.json` separate file (courier_id keys), merge PWA
  primary + Traccar legacy fallback w `courier_resolver._load_gps_positions()`.

---

## ✅ FAZA 1 DONE (13-14.04.2026)

**12 commitów Fazy 1** (od `dd73048` F1.1 do `842f961` F1.6) — shadow dispatcher
live od 13.04 23:05, pierwsza propozycja Telegram dostarczona.

### P0.5b DONE ✅ (TIER 0 pre-Faza-1 blocker)
Commit `0f574c1` — 4 code fixes + .gitignore + spec note:
- Fix 1: HARD EXCLUSIONS dla allow-list CC (settings.json deny rules: 16 reguł Bash+Read)
- Fix 2: state_machine._read_state 3 retry + fcntl LOCK_SH
- Fix 3: geocoding._save_cache → atomic mkstemp + LOCK_EX + fsync
- Fix 4: panel_client._open_with_relogin wrapper (401/419) dla fetch_order_details
- Fix 5: .gitignore audit + cleanup — BRAK tracked secrets

### F1.1-F1.6 DONE ✅
- **F1.1** `dd73048` — Faza 1 core 5 modułów (route_simulator_v2 PDP-TSP greedy hybrid,
  feasibility_v2 R1/R3/R8/R20/R27/D8, dispatch_pipeline scoring + verdict,
  shadow_dispatcher systemd runner, telegram_approver long-poll async)
- **F1.2** `4b7d1b4` — `courier_names.json` lookup (44 entries z odwrócenia
  kurier_ids.json), fix name=None w propozycjach (K207 → Marek, K289 → Grzegorz W)
- **F1.3** `f7ff9eb` — [PROPOZYCJA] enrichment: imiona + km do pickup (haversine ×
  1.37 road factor) + ETA (fleet_speed traffic bucket) + delivery_address + per-alt km
- **F1.4a** `2649ac7` — `/status` komenda Telegram (systemctl status, stats state, agreement rate)
- **F1.4b** `23bfa7d` + `3afeae4` — `daily_briefing.py` (morning wczoraj + evening dziś)
- **F1.4c** `535047c` — `courier_ranking.py` (top N SLA z sla_log.jsonl + gwiazdki)
- **F1.5** `7af8ce1` — GPS PWA server (`dispatch_v2/gps_server.py`, port 8766,
  stdlib http.server, dark PWA HTML 4.5KB), nginx + HTTPS `gps.nadajesz.pl`,
  Let's Encrypt cert + pre/post/renew hooks, `courier_resolver._load_gps_positions`
  merge PWA primary + legacy fallback
- **F1.6** `842f961` — `/status` 3-w-1 (bieżący + dziś + wczoraj + top 3 wczoraj),
  wyłączenie cron daily_briefing + courier_ranking (on-demand > push per Adrian preference)

### Deployment state po Fazie 1
- **6 serwisów systemd:** `dispatch-panel-watcher`, `dispatch-sla-tracker`,
  `dispatch-shadow`, `dispatch-telegram`, `dispatch-gps`, `nginx`
- **HTTPS endpoint:** `https://gps.nadajesz.pl` (Let's Encrypt, renewal hooks OK)
- **Cron:** 7 entries (fetch_schedule pre/post, git push hourly, reboot hooks) —
  briefing/ranking wyłączone w F1.6
- **Git:** 22 commitów pushed do `github.com/czaplaadrian88-code/ziomek-dispatch-`

---

## F1 FOLLOW-UP TECH_DEBT (wykryte 13-14.04)

### P1 — po tygodniu shadow (dotyczy agreement rate fine-tune)

- [ ] **delivery_address w NEW_ORDER payload** — F1.3 serializer nagłówek Telegram
  używa `result.delivery_address`, ale **nie zweryfikowałem** czy watcher emituje
  to pole w `NEW_ORDER.payload`. Jeśli nie, Telegram pokaże `→ —`. Check: po
  następnej żywej propozycji sprawdzić `shadow_decisions.jsonl[-1].delivery_address`.

- [ ] **GPS coverage < 5%** — F1.5 live deploy + merge OK, ale wiadomość do
  kurierów z PIN + link `https://gps.nadajesz.pl` nie wysłana. Fresh GPS
  (<5min): 2/82 (tylko ci 2 co są w Traccar żywy). Action: dystrybucja PIN
  per kurier (SMS/Telegram group/fizyczne kartki z QR code). Bez tego PWA jest
  dead code.

- [ ] **`courier_resolver` fallback order** dla GPS — obecnie PWA primary,
  legacy fallback. Co jeśli **PWA stale** (>5min) ale legacy jeszcze fresh?
  Obecnie PWA always wins nawet gdy stale. Fix: dodać freshness check per
  source, wybierz najfreshszy.

- [ ] **`kurier_piny.json` vs `kurier_ids.json`** — dwa osobne ID spaces
  (PIN 4-cyfra vs courier_id 3-4 cyfra), zero referential integrity. F1.2 fix
  przez odwrócenie kurier_ids, ale jeśli admin doda nowego kuriera tylko w
  jednym pliku → niespójność. Propozycja: **`couriers.json` jako single source
  of truth** `{courier_id: {name, pin, phone?, active}}`, migration script
  + update kurier_piny/kurier_ids w tym samym commit.

### P1 — po monitorze

- [ ] **Agreement rate meaningful threshold** — pierwsza propozycja 13.04 23:05
  była `action=NIE` przez Adriana. Nie wiemy czy NIE bo scoring zły czy
  operational (kurier just delivered). Potrzeba >100 propozycji i breakdown
  (learning_log feedback details) żeby policzyć realny agreement. Target:
  **>85% przez 24h** = auto-approve trigger.

- [ ] **`learning_log.jsonl` format** — obecnie `{ts, order_id, action, ok,
  feedback, decision}` dict. Brakuje: `courier_chosen_by_koordynator`
  (jeśli Adrian wybrał INNY), `reason_nie` (dlaczego odrzucił). Bez tego
  learning analyzer w tygodniu 2 nie może policzyć false-positive per
  scoring dimension.

- [ ] **Shadow latency monitoring** — F1.1 `latency_ms` w każdym decision,
  ale nikt nie agreguje. Add: sla_tracker reads last N shadow_decisions,
  alert Telegram gdy p95 > 500ms.

---

## LEARNING — zaplanowane

### Poziom 2 — 21.04.2026 (po 7 dniach shadow)

**Cel:** analizować `learning_log.jsonl` + `shadow_decisions.jsonl` po pełnym
tygodniu shadow operation. Potrzeba min **100 propozycji** dla meaningful stats.

**Metryki:**
- Agreement rate global + per kurier + per restauracja + per godzina
- Top 10 false-positive decisions (Ziomek wybrał X, Adrian wybrał Y — Y powinien być w top3)
- Scoring dimensions correlation z NIE (czy high `prep_variance_high` restauracje
  mają wyższy NIE rate? czy `waiting_time` predicts odrzucenie?)
- Kuriery chronically rejected (Adrian zawsze wybiera INNY nawet gdy Ziomek ranked top) — red flag

**Deliverable:** `tools/learning_analyzer.py` (jednorazowy offline, stdlib only),
raport `docs/LEARNING_REPORT_20260421.md` z recommendations dla scoring fine-tune.

### Poziom 3 — Miesiąc 2 (koniec kwietnia / początek maja)

**Cel:** Pre-auto-approve go/no-go decision. Baseline post-tuning (po poziomie 2
fixes) + A/B test Ziomek vs current koordynator operation.

**Metryki:**
- Agreement rate > 85% per (kurier, restauracja) pair = auto-approve dozwolony
  (pominięcie Telegram approval, direct Rutcom assign przez `gastro_assign.py`)
- Fleet utilization delta (Ziomek vs baseline)
- SLA violation count (powinno być ≤ baseline)
- Kurier satisfaction (odczuli że "system lepiej rozdziela")

**Deliverable:** Shadow → Semi-auto → Full-auto rollout plan per (kurier, restauracja).

## Znane problemy do naprawy (stan 14.04.2026 wieczór)

### PILNE
- [ ] Bartek O. GPS nieaktywny od 6 dni — brak świeżej pozycji, pozycja syntetyczna
- [ ] Timeout dla zleceń przypisanych przez koordynatora (silent fix zrobiony ale monitoring brakuje)
- [ ] Kurierzy bez GPS — travel_min niedokładny, deklaracje mogą być błędne

### DO ZROBIENIA (tydzień 2-3)
- [ ] Learning analyzer — po 200+ decyzjach (cel 21.04)
- [ ] Auto-approve score >0.90 (eliminuje 60-70% kliknięć)
- [ ] Telegram security — weryfikacja chat_id członków grupy
- [ ] Rate limiting panel_watcher — backoff przy błędach HTTP
- [ ] getUpdates crash guard — sys.exit(1) po N failach (systemd restartuje)
- [ ] Restimo API skeleton — FastAPI + OAuth2
- [ ] prep_variance monitoring — alerty gdy restauracja spóźnia się >10 min
- [ ] Rutcom kontakt — GPS kurierów przez panel API

### ARCHITEKTURA (Faza 2+)
- [ ] cs.heading / kierunek jazdy kuriera (wymaga GPS history)
- [ ] OR-Tools VRPTW (Faza 9)
- [ ] Wielomiastowość Warszawa (miesiąc 5+)

## 2026-04-14 — Sesja wieczorna (F2.2)

### Crash guard (telegram_approver.py)
- fail_count licznik + exponential backoff (5s×N, cap 60s)
- sys.exit(1) po 10 consecutive failach → systemd auto-restart
- try/except wokół całego tg_request call

### Learning Analyzer (learning_analyzer.py)
- 7 sekcji + [8] silent agreement via events.db
- Wynik: 21.3% agreement rate — NIEMIARODAJNY (dane z okresu timing debug)
- Jutro re-run na czystych danych → decyzja scoring recalibration vs auto-approve
- session_stats keys: proposed/accepted/rejected/alerts/delays/czasowki (nie delivered_today)
- Kurier 9279 (Michał K.) — potencjalnie over-nominated, wymaga weryfikacji

### Telegram security (F2.2)
- handle_callback: dodany chat_id filter (cb.message.chat.id == admin_id)
- Nieautoryzowane callbacki: answerCallbackQuery "unauthorized" + warning log
- from_id logowany przy każdym callbacku → zbieramy user_id Bartka automatycznie

### NLP assistant (F2.2)
- "pomoc/help" → statyczny tekst z komendami
- "kto pracuje" → czyta schedule_today.json z dysku per-request
- "ile zleceń" → czyta session_stats z state.json (zwraca ? — brak daily counter)
- unhandled msg → log z from_id (zbieramy user_id Bartka)

### TODO następna sesja
- [ ] Whitelist user_id: po pierwszym TAK Bartka → from_id w logu → dodać do whitelisty
- [ ] Re-run learning_analyzer rano na czystych danych
- [ ] "ile zleceń" → sensowne dane gdy będzie daily counter w events.db
- [ ] /tmp/gastro_stop → osobny task: check w shadow_dispatcher przed każdą propozycją

---

## F2.1b step 6 — sla_tracker._parse() naive→UTC timestamp bug

**Lokalizacja:** `sla_tracker.py:_parse()` linie ~26-35
**Odkryte:** 2026-04-15 podczas discovery kroku #6 (R6 BAG_TIME pre-warning)
**CONFIRMED w produkcji:** 2026-04-15 ~12:27 UTC (FAZA A restart sla_tracker po commit step 6)
**Workaround wdrożony:** 2026-04-15 ~12:50 UTC (krok 6.1: dedykowany `_parse_aware_utc()`)

**Bug:** `_parse()` dla naive ISO stringów `"YYYY-MM-DD HH:MM:SS"` (format emitowany przez panel Rutcom w Warsaw TZ) wywołuje `datetime.fromisoformat(...)` które sukcesuje i zwraca **naive datetime bez tzinfo**. Fallback `strptime.replace(tzinfo=timezone.utc)` **nigdy nie jest dotykany** — fromisoformat sukcesuje pierwszy. Fallback path zakłada UTC (błędne dla Warsaw), ale kod i tak tam nie dociera. Naive ścieżka nie jest "rzadka", jest **główna**.

**Stan produkcji (zweryfikowany 15.04.2026 FAZA A):** wszystkie `picked_up_at` w `orders_state.json` są naive Warsaw stringami `"YYYY-MM-DD HH:MM:SS"`. Zero explicit TZ suffix. 9/9 picked_up orderów w prodzie dało crash przy `(now_utc - picked_dt)`. Mój wcześniejszy draft założył "naive rzadko aktywna" — błędne, **reality jest odwrotna**.

**Dlaczego SLA check działa mimo bugu — coincidence, nie intencja:**

```python
# sla_tracker.py:57-61 (oryginalny kod process(COURIER_DELIVERED))
p, d = _parse(picked_ts), _parse(delivered_ts)
if p and d:
    dmin = round((d - p).total_seconds() / 60, 1)
    sla_ok = dmin <= 35
```

**OBIE** daty są parsowane tym samym `_parse()` → **obie naive** (bez tzinfo). `(d - p)` na dwóch naive zwraca **poprawny timedelta** — różnica nie zależy od tzinfo gdy obie w tej samej (niezdefiniowanej) strefie. `dmin` numerycznie correct, `sla_ok` działa. **Szczęśliwa koincydencja**: naive-vs-naive = correct, aware-vs-naive = TypeError.

R6 `_check_bag_time_alerts` robi `(now_utc - picked_dt)` gdzie `now_utc = datetime.now(timezone.utc)` (**aware**) vs `picked_dt` (naive) → `TypeError: can't subtract offset-naive and offset-aware datetimes` per każdy picked_up order.

**Workaround w step 6.1:** dedykowany `_parse_aware_utc()` tylko dla R6 path. Naive traktuje jako Warsaw-local, konwertuje do UTC, zawsze zwraca aware UTC. `_parse()` nietknięty — SLA check zachowuje bit-for-bit legacy zachowanie. Osobny tag `pre-F2.1b-step6-1` dla rollback.

**Pełny fix plan (F2.1c lub krok #8):**

1. **Analiza SLA ścieżki (sanity check):** grep w `sla_log.jsonl` na brakujące/ujemne `delivery_time_minutes`:
   ```bash
   python3 -c "
   import json
   n_total = n_none = n_negative = n_outlier = 0
   for line in open('/root/.openclaw/workspace/scripts/logs/sla_log.jsonl'):
       r = json.loads(line); n_total += 1
       d = r.get('delivery_time_minutes')
       if d is None: n_none += 1
       elif d < 0: n_negative += 1
       elif d > 120: n_outlier += 1
   print(f'total={n_total} none={n_none} negative={n_negative} >120min={n_outlier}')
   "
   ```
   Jeśli n_negative == 0 i n_outlier rozsądne → naive-vs-naive jest zawsze correct, bug w _parse() jest martwy dla SLA path.

2. **Jednolity parser:** wymienić `_parse()` na wariant `_parse_aware_utc()`. Retest SLA check — weryfikacja że `dmin` nie zmienia wartości dla historycznych rekordów (różnica powinna być 0.0 min bo obie strony używają tej samej TZ offset, który po konwersji do UTC kasuje się w odejmowaniu).

3. **Test regresyjny:** odtworzenie 1000 `COURIER_DELIVERED` z prawdziwymi timestampami z `events.db`, compare `dmin` przed/po fix. Zero tolerancji różnic > 0.1 min.

4. **Deprecate `_parse()`:** usuwamy po sukcesie retestu. Zostawiamy tylko `_parse_aware_utc()` przemianowane na `_parse()`.

**Risk jeśli nie naprawimy (motivacja priority):**
- Każda przyszła reguła sla_tracker porównująca timestamp vs `now_utc` będzie crashować tą samą ścieżką (R8 hard? R9 monitoring? restaurant_violation notifier?) — już dziedziczylibyśmy ten koszt w F2.1c bez workaround.
- Future developer piszący nowy check **reuse `_parse()`** na bazie "skoro SLA check go używa i działa" → duplicate tego samego bugu, kolejny hotfix.
- `_parse_aware_utc()` jest **workaround, nie fix** — koszt utrzymania dwóch parserów z rozbudowanym docstringiem wyjaśniającym różnicę. Przy każdym cleanup refactorze "remove duplicate parser" kusi żeby usunąć jeden z nich.

**Scope:** F2.1c lub krok #8. Workaround działa w F2.1b krok 6.1.

---

## F2.1b DONE — resolved / in-progress / backlog (2026-04-15)

Sprint F2.1b Decision Engine 3.0 — **10 kroków** (step 0-7 committed + step 8 in-progress), **9 tagów** rollback, 3 dni pracy 13-15.04.

### ✅ Resolved (verified)

| Item | Commit | Verified by |
|---|---|---|
| enriched_metrics flat append (bonus_l1/l2 + R6/R8/R9 placeholders) | `b7c5592` step 0 | Test F10 smoke + prod observation order #466122 bonus_l1=25.0 @ 2026-04-15 11:16:59 UTC |
| `telegram_utils.send_admin_alert` minimalistyczny wrapper | `54bfc46` step 1 | Manual test `send_admin_alert('[test F2.1b step 1] telegram_utils OK')` → Telegram ok=True delivered |
| `common.py` F2.1 extensions (R6-R9 + auto_approve placeholders) | `6acef7a` step 2 | Test F7/F8/F9 constants + smoke 5 modułów all_imports_OK |
| `feasibility_v2` R6 BAG_TIME hard + R7 longhaul peak (R8 deferred) | `c185559` step 3 | Test A1-A6 regression + B1-B6 unit R6/R7 + A3 R3 soft-only invariant |
| `dispatch_pipeline` penalties R6_soft + R9_stopover + R9_wait | `c370b77` step 4 | Test F4/F5/F6 formuły matematyczne + shadow_dispatcher schema prop step 0 |
| `state_machine.bag_time_alerted` flag (5/6 handlers race-safe) | `2fbba6c` step 5 | Test B11 race condition guard (CRITICAL, regression blocker dla future refactorów) + dry-run 4 transitions |
| `sla_tracker._check_bag_time_alerts` R6 pre-warning hook | `2859462` step 6 | Dry-run monkey-patch state + fake send (1 alert, flag set, gate works) |
| `sla_tracker._parse_aware_utc` naive Warsaw hotfix | `a4f9c5d` step 6.1 | Test B7-B10 parse 4 formats + prod observation order #466154 alerted=True @ 12:56:04 UTC restart |
| Test suite 38 testów (A/B/C/D/E/F) | `b722b86` step 7 | Runner `38 passed, 0 failed`, exit code 0, zero debug iteracji |

**FAZA A deployment:** `dispatch-sla-tracker` restart 2026-04-15 12:56:04 UTC, R6 pre-warning LIVE, zero `TypeError` w logach, 1 empirical alert delivered.

### ⏳ In-progress (step 8 w trakcie)

- [ ] FAZA B: `dispatch-shadow` restart → R6 hard + R7 + penalties w runtime
- [ ] Post-restart observation 30 min: reject rate per reguła, score distribution drift, 3× services active
- [ ] Final commit + tag `pre-F2.1b-complete`

### 📋 F2.1c backlog (odroczone z F2.1b)

- [x] **R8 pickup_span czasowy** — ✅ DONE F2.1c step 1 (16.04.2026). T_KUR propagation w `_bag_dict_to_ordersim` + hard cap 15/30 min (bundle=2/3) + soft penalty 7-15 min (-3/min). Tag: `pre-f21c-r8-complete`. Observability retroactive F2.1c step 2 (analogicznie do F2.1b step 3.1 serializer gap): `r8_pickup_span_min` dodany do shadow_dispatcher `_serialize_candidate` + inline best block.
- [ ] **`_parse()` unified fix + test regresyjny SLA path** — aktualnie workaround `_parse_aware_utc` dla R6, legacy `_parse` dla SLA `delivery_time_minutes`. Pełny fix wymaga retestu 1000 COURIER_DELIVERED na przed/po compare `dmin`. Patrz sekcja "F2.1b step 6 — `_parse()` naive→UTC" wyżej.
- [ ] **`AUTO_APPROVE_ENABLED` flip** — blocker: learning_log zdominowany przez `TIMEOUT_SUPERSEDED` (242/249 wpisów w pre-F21b oknie 38h). Wymaga refactor watchdog timeout logic + 200+ walidowanych decyzji + silent_agreement analyzer porównujący actual panel kurier vs proposed.
- [ ] **`ANOMALY_DETECTION_ENABLED` flip** (A1 `restaurant_prep_variance` + A2 `courier_recent_delay`) — wymaga implementacji `context.*` metod bazujących na `restaurant_meta.json` + `shadow_decisions.jsonl`.
- [ ] **`learning_analyzer.compute_agreement_per_bonus_layer()`** — teraz że bonus_l1/l2/r4 są w shadow_decisions.jsonl (step 0), można kalibrować L1=25 tuning per real data po 2-3 dniach akumulacji. Pre-F21b raport sekcja 3 była "niemożliwa" — od step 0 staje się możliwa.
- [ ] **Cascaded R6 alerts (30/35/40 min eskalacja)** — obecnie one-shot per order. Jeśli dane pokażą że alerty 30 min są ignorowane do 40+ min, rozważyć cascaded. Flag off default.
- [ ] **`bag_time_alerted` jednorazowa migracja** — aktualnie lazy przez `.get(..., False)`. Stare picked_up ordery (9 w prodzie @ step 8 pre-flight) mają `alerted=MISSING`. Stopniowa naturalna migracja przez kolejne update eventy — no script needed.
- [ ] **Full end-to-end `dispatch_pipeline.assess_order` smoke test** — wymaga >5 mocków (CourierState, fleet_snapshot, restaurant_meta, osrm, events). Defer do F2.2 gdy dodamy pytest fixture infrastructure. Obecny smoke F10 testuje feasibility+simulate+plan integration, pokrywa 80% stack bez assess_order.

### 🎯 F2.1b sprint metrics

- **9 kroków committed** (step 0/1/2/3/4/5/6/6.1/7) + **step 8 in-progress** (docs + FAZA B + final)
- **9 tagów rollback** (`pre-F2.1b-step0/1/2/3/4/5/6/6-1/7` = 9 tags pre-step-8)
- **1 hotfix w trakcie deploymentu** (step 6.1 `_parse_aware_utc`, CONFIRMED in prod przez reality check FAZA A restart — wszystkie 9/9 picked_up orderów miało naive Warsaw timestamps)
- **38 testów** unit/integration, **38/38 PASS** pierwszy run, zero debug iteracji
- **1 regression guard test B11** (race condition, krytyczny dla przyszłych refactorów `state_machine`)
- **2 empirical prod milestones** (`bonus_l1=25.0` @ #466122 + R6 alert @ #466154)
- **~1100 linii** kodu/testów/docs dodanych
- **Zero rollback**, **zero downtime serwisów** przez cały sprint

## F2.1c — 2026-04-16

### DONE
- R8 pickup_span T_KUR propagation (dispatch_pipeline.py: _bag_dict_to_ordersim)
- R8 hard cap feasibility_v2.py: 15 min bundle=2, 30 min bundle=3
- R8 soft penalty wliczone do bonus_penalty_sum i final_score
- 4 testy R8 (test_r8_hard_bundle2_reject/pass, test_r8_hard_bundle3_reject, test_r8_graceful_none)
- 44/44 testów PASS, zero regresji
- commit fa4920d, tag pre-f21c-r8-complete

### REMAINING F2.1c
- #1: _parse() unified fix + regression test SLA path (techdebt z F2.1b step 6.1)
- #2: learning_analyzer.compute_agreement_per_bonus_layer() (wymaga 200+ decyzji ~3-5 dni)
- #3: AUTO_APPROVE_ENABLED flip (wymaga learning_analyzer)

### MONITORING
- r8_pickup_span_min, bonus_r8_soft_pen w shadow_decisions.jsonl od lunchu 11:00 Warsaw
- Weryfikacja po 14:00 Warsaw: ile R8 rejectów, rozkład span_min

### KNOWN
- pickup_at_warsaw None dla orderów bez deklarowanego czasu → r8_pickup_span_min=None (graceful)
- R8 soft penalty kalibracja (PICKUP_SPAN_SOFT_START_MIN=7, PENALTY=3/min) — do weryfikacji empirycznej F2.2

### F2.1c step 2 — R8 observability retroactive ✅ (16.04.2026)

**Tag:** `pre-f21c-step2-complete`

**Problem:** Analogicznie do F2.1b step 3.1 serializer gap. Po deploy F2.1c step 1 (R8 logic), audit wykrył że `shadow_dispatcher._serialize_candidate` (L106-121) + inline best serialization (L197-212) mają `bonus_r8_soft_pen` ale brakuje surowej metryki `r8_pickup_span_min`. Bez niej niemożliwy empirical monitoring ile spanów widzi system + learning_analyzer dostaje niepełne dane dla R8 calibration.

**Fix:**
- `shadow_dispatcher.py` L115 (miejsce A, _serialize_candidate): dodano `"r8_pickup_span_min": m.get("r8_pickup_span_min")` między R7 metrics a penalties
- `shadow_dispatcher.py` L207 (miejsce B, inline best): identycznie z `best_m`
- Komentarze inline updated

**Docs sync (DEFERRED F2.1c → DONE):**
- `ZIOMEK_V3_7_INSTRUKCJA.md` L174 (heading) + L359 (F2.1c priorytet #1)
- `BARTEK_GOLD_STANDARD.md` L208 (tabela) + L254 (detail section)

**Verification:**
- `grep -n "DEFERRED F2.1c" docs/` → 0 matches (wszystko DONE)
- `grep -n "r8_pickup_span_min" shadow_dispatcher.py` → 2 matches (A + B)
- Post-restart dispatch-shadow: fresh PROPOSE records zawierają `r8_pickup_span_min`

**Lesson (pattern dla F2.2+):** każda nowa metryka w `dispatch_pipeline` albo `feasibility_v2` wymaga checklist downstream consumers:
1. Czy `shadow_dispatcher._serialize_candidate` ma klucz? (miejsce A)
2. Czy inline best serialization ma klucz? (miejsce B)
3. Czy `learning_analyzer` czyta nowy klucz? (F2.1c scope)
4. Czy test suite pokrywa end-to-end path?

Bez tej checklist pattern powtarza się: F2.1b step 3.1 (9 kluczy R6/R7) → F2.1c step 2 (1 klucz R8).

## F2.1c sesja 2 — 2026-04-16

### DONE
- Step 3: _parse() dead code (strptime fallback) usunięty
- _parse() docstring: techdebt primary path (naive→naive) udokumentowany → F2.2
- 3 regression testy: test_parse_naive_is_naive_documented_techdebt, iso_aware_passthrough, none_empty
- 47 testów PASS, commit daad541, tag pre-F2.1c-step3-complete
- dispatch-sla-tracker restarted, active

### LEARNING ANALYZER AUDIT (discovery C)
- 420 rekordów (2.5 dnia), n=10 human decisions (TAK:8, NIE:2)
- NIE semantyka: "pozostaje w puli" — nie "scoring zły", tylko "czekam na lepszą opcję"
- 84% silent override = normalne przy braku GPS + koordynator też estymuje pozycję
- AUTO_APPROVE: score≥98 precision=100% ale n=2 NIE → za mało. Wróć za tydzień przy 50+ TAK/NIE
- per_bonus_layer: odsuń do F2.2 (n=10 human decisions bez wartości statystycznej)

### REMAINING F2.1c → de facto F2.2
- _parse() primary path (fromisoformat naive→naive) — pełny fix z retestem SLA delivery_time_minutes
- learning_analyzer per_bonus_layer — wymaga 50+ human TAK/NIE
- AUTO_APPROVE flip — wymaga per_bonus_layer

### NEXT ACTION
- GPSLogger onboarding: 1 kurier jako pilot (Gabriel lub najbardziej aktywny z 7)
- Zbieraj TAK/NIE aktywnie przez tydzień (cel: 50+ decisions)

## F2.1c sesja 3 — 2026-04-16

### DONE
- panel_watcher reassignment detection (commit aabb5d3)
  - Bug: `was_assigned and is_assigned_now` nie był obsługiwany → stary courier_id w state
  - Fix: elif blok z budżetem MAX_REASSIGN_PER_CYCLE=5 fetchów/cykl
  - Emituje COURIER_ASSIGNED z source=panel_reassign
  - Root cause: bag_size=0 dla kuriera z 2 orderami → "wolny" label błędny

### MONITORING
- journalctl -u dispatch-panel-watcher | grep REASSIGNED
- Pierwsze REASSIGNED logi potwierdzą fix w akcji

### REMAINING F2.1c → F2.2
- _parse() primary path (fromisoformat naive→naive) — pełny fix z retestem SLA
- learning_analyzer per_bonus_layer — wymaga 50+ human TAK/NIE
- AUTO_APPROVE flip — wymaga per_bonus_layer

## F2.1c sesja 4 — 2026-04-16

### DONE
- panel_watcher reassignment detection (aabb5d3) — COURIER_ASSIGNED source=panel_reassign
- R5 pickup spread 1.8→2.5km (fb847b3) — odblokuje 27% marginalnych bundli
- 47 testów PASS, zero regresji

### F2.2 FEATURE: Wave Routing (ZAPISANE)
Kurier robi 3-4 dowozy w pierwszej fali (jeden kierunek), po czym wraca do centrum po kolejną falę.
Ostatni punkt fali + nowy order "po drodze do centrum" = realny koszt niższy niż bag_size/spread sugeruje.
Ziomek tego nie widzi — liczy tylko statyczny spread i bag_size.
Przykład: Mateusz O (466343) — Rukola Kaczorowska → Elewatorska 27a to 19 min z ostatniego punktu,
po drodze do centrum. Ziomek dał SKIP (R1/R5), koordynator przydzielił ręcznie do przepełnionego kuriera.
Fix wymaga: wykrywanie kierunku powrotu kuriera po ostatnim picked_up → bonus za ordery na trasie powrotu.
Sprint F2.2 (po zebraniu danych z R5=2.5 + reassignment fix).

### REMAINING → F2.2
- Wave routing bonus (kierunek powrotu kuriera)
- SOLO fallback gdy no_candidates (zamiast SKIP → KOORD)
- _parse() primary path naive fix z retestem SLA
- learning_analyzer per_bonus_layer (wymaga 50+ TAK/NIE)
- AUTO_APPROVE flip

## F2.1c sesja 5 — 2026-04-16

### DONE
- Wave routing v2 (94dfba0): post_wave detection w dispatch_pipeline
  - Warunek: all_picked_up + pos_source != gps + free_at_min <= 15 → wave_bonus=15
  - Warunek: all_picked_up + pos_source != gps + free_at_min <= 30 → wave_bonus=8
  - Stałe: RYNEK_KOSCUSZKI=(53.1324,23.1489), POST_WAVE_FREE_MAX_MIN=15
  - Logika w pipeline (nie resolver) — ma dostęp do free_at_min
  - 44/44 testy PASS
- R5 pickup spread 1.8→2.5km (fb847b3) — 27% więcej bundle candidates
- panel_watcher reassignment detection (aabb5d3)
- Insight z analizy Bartka O (49 orderów, 24 fale, 10.04.2026):
  Kurier nakłada fale — ma kolejną gotową zanim skończy poprzednią.
  Ciągłe 4/4, gapy między falami 8-28 min (lub ujemne = overlap).
  Punkt powrotu = Rynek Kościuszki (centrum Białystoku).

### MONITORING wave routing
- shadow_decisions.jsonl: wave_bonus > 0 records po lunchu
- Czy post_wave kurierzy dostają więcej propozycji?
- False positives: kurier daleko od centrum ale dostaje Rynek → monitoruj

### REMAINING → F2.2
- Wave routing kalibracja (POST_WAVE_FREE_MAX_MIN=15 empiryczny — może wymagać korekty)
- SOLO fallback gdy no_candidates (zamiast SKIP → KOORD z best solo option)
- _parse() primary path naive fix z retestem SLA
- learning_analyzer per_bonus_layer (wymaga 50+ TAK/NIE)
- AUTO_APPROVE flip

## F2.1c sesja 6 — 2026-04-16

### DONE
- HOTFIX R8 (243c892): wyklucz picked_up z pra_list — historyczny T_KUR zawyżał pickup_span 80-128min → false rejects
- SOLO fallback (ca4e8c3): R29 SKIP→PROPOSE solo (pusty bag, ignoruje R1/R5/R8) + KOORD gdy zero solo candidates
  Zero SKIP od teraz — zawsze PROPOSE lub KOORD
- Wave routing v2 (94dfba0): post_wave bonus 15/8 dla mid-wave kurierów
- R5 1.8→2.5km (fb847b3)
- panel_watcher reassignment detection (aabb5d3)

### MONITORING
- shadow_decisions.jsonl: solo_fallback=True records — ile solo propozycji?
- wave_bonus > 0 records — czy wave routing działa?
- KOORD (no_solo_candidates) — ile absolutnych fallbacków?

### REMAINING → F2.2
- Solo fallback scoring: teraz używa prostego (100 - dist*10) — wymaga pełnego score pipeline
- Wave routing kalibracja POST_WAVE_FREE_MAX_MIN=15
- _parse() primary path naive fix z retestem SLA
- learning_analyzer per_bonus_layer (wymaga 50+ TAK/NIE)
- AUTO_APPROVE flip
- GPSLogger onboarding 7 kurierów

## F2.1c sesja 7 — 2026-04-16

### DONE
- best_effort verdict KOORD→PROPOSE (cea00e0) — Telegram widzi best_effort propozycje
- SOLO fallback (ca4e8c3) — zero SKIP, zawsze PROPOSE lub KOORD
- HOTFIX R8 (243c892) — wyklucz picked_up z pra_list
- Wave routing v2 (94dfba0)
- R5 1.8→2.5km (fb847b3)
- panel_watcher reassignment (aabb5d3)

### STAN F2.1c — KOMPLETNY
Wszystkie zaplanowane sprinty F2.1c zrealizowane + 4 hotfixy produkcyjne.
System nie milczy — zawsze PROPOSE lub KOORD do Telegram.

### REMAINING → F2.2
- best_effort w Telegram: oznaczyć flagą w wiadomości (wizualnie odróżnić od normalnej propozycji)
- Solo fallback scoring: teraz prosty (100 - dist*10) → pełny score pipeline
- Wave routing kalibracja POST_WAVE_FREE_MAX_MIN=15
- _parse() primary path naive fix z retestem SLA
- learning_analyzer per_bonus_layer (wymaga 50+ TAK/NIE)
- AUTO_APPROVE flip
- GPSLogger onboarding 7 kurierów
