# MULTI-CITY — inwentarz + `cities.json` + plan migracji (RECON, zero-behavior-change)

**Data:** 2026-07-02 · **Pas:** multi-city-recon (FALA-2 audyt Ziomka, tmux 11) · **Tryb:** READ-ONLY recon + design (ZERO edycji .py, ZERO flipów/restartów)
**Deliverables tego pasu:** `config/cities.json` (NOWY szkielet) + ten plan. Bazuje na i weryfikuje `eod_drafts/2026-07-02/AUDYT2/MULTICITY_inventory.md` (audyt 2.0, motyw #8).
**Kontekst strategiczny (Adrian):** ekspansja — Warszawa / Restimo Q3 2026 / Wolt Drive. Dziś nawet walidator-prawdy współrzędnych to bbox Białegostoku.

---

## 0. RAPORT — zweryfikowana liczba hardcode'ów

**Metoda:** grep + AST na worktree `fix/multicity` (kanon identyczny), wykluczone `tests/`, `eod_drafts/`. Zweryfikowałem, że KAŻDA kotwica z inwentarza 2.0 nadal istnieje (linie dryfnęły — patrz niżej).

| Miara | Liczba | Uwaga |
|---|---|---|
| Surowe wystąpienia `BIALYSTOK/Białystok/bialystok` w kodzie silnika (.py, bez testów/eod) | **631** | nośne + komentarze + replay/smoke data |
| Pliki .py silnika z tokenem miasta | **124** | j.w. (dużo w `tools/` = off-line) |
| Surowe wystąpienia centroidu `53.1x/23.1x` (.py silnika) | **583** | zdecydowana większość = coords w replay/smoke/testowych fixture, NIE nośne |
| OSRM `:5001`/`localhost:500x` (.py silnika) | **19** | infra single-extract |
| **NOŚNE miejsca (odsiew audytu 2.0, potwierdzone)** | **≈146 / ~37 plików** | **liczba przyjęta — moje grepy ją potwierdzają** (surowe 631/124 to zawyżenie o komentarze+dane) |

**Werdykt liczby:** metryka **~146 nośnych** jest wiarygodna. Surowy grep (631/124) NIE jest dobrą miarą — 583 z 631 „centroidów" to dane replay/smoke (`route((53.1325,23.1688), ...)` w `__main__`, korpusy testowe), nie stałe konfiguracyjne. Odsiew 2.0 (odrębne nośne miejsca w kodzie produkcyjnym) trzymam jako główny licznik do dashboardu entropii pod nazwą **`single-city-hardcodes ≈ 146`**.

**Weryfikacja kotwic (linie zdryfowały vs 2.0, symbole żyją):**
- `common.py`: `BIALYSTOK_BBOX_LAT/LON` → **663-664** (było 561-562), `coords_in_bialystok_bbox` → **667**, `HAVERSINE_ROAD_FACTOR_BIALYSTOK` → **637** (było 535), `GEOCODE_BBOX_*` → **1152-1156** (env), `BIALYSTOK_DISTRICT_ADJACENCY` → **1555**, `FIRMOWE_KONTO_FALLBACK_COORDS` → **3689**, `MAX_PICKUP_REACH_KM` → **583** (env), `LONG_HAUL_PEAK_HOURS_START/END` → **955-956**, `get_time_bucket` → **696**.
- `osrm_client.py`: `OSRM_BASE` → **43**, `_BBOX_CENTER=(53.1325,23.1688)` → **530**.
- `courier_resolver.py`: `BIALYSTOK_CENTER` def → **110** + iniekcje **1122,1592,1601,1655,1695,1707** (6× `cs.pos=`).
- `courier_api`: `config.py:204` OSRM, `delivery_town.py:38` `_HOME_KEY`, `courier_orders.py:118` klucz `", białystok"`.
- konsola: `config.py:67` osrm_url, `:131-134` geocode_bbox (WĘŻSZY niż silnik!), `:206` `ziomek_default_city`, `economics.py:57/59/61` road_factor/breach/CITY_HOME, `forecast21.py:60/237` peak/pogoda.
- tools MUTATORY: `invalidate_city_bugged_geocodes.py:27-28` bbox, `purge_streetless_geocode_keys.py:27` `CITY={białystok}`.
- mosty: `city_map.json` w papu+drtusz (2 kopie), `GASTRO_BASE` (3×), `COMPANIES` (11 firm).

**Pełny inwentarz per-plik/per-linia = `AUDYT2/MULTICITY_inventory.md` (nie duplikuję; tam 6 warstw + TOP-10 + POKRYCIE + JAWNE LUKI).** Ten plan = konsumuje inwentarz i dokłada **schemat configu + kolejność migracji + test parytetu**.

---

## 1. TOP-5 NAJTRUDNIEJSZYCH MIEJSC MIGRACJI (największy wysiłek/ryzyko)

Ranking po wysiłku inżynierskim (nie samym blast-radius — to jest w TOP-10 inwentarza):

1. **OSRM jednoregionowy graf (`:5001`)** — infra, nie stała. Migracja wymaga **multi-region extract LUB router-per-city wybierany po `city_id`** + routing zapytań przez właściwy endpoint we WSZYSTKICH 4 konsumentach (silnik/konsola/courier_api/tools). To nie edycja stałej — to zmiana topologii deploymentu. **Wysiłek: L. Ryzyko: wysokie** (ETA/committed-time cicho kłamią, jeśli źle).
2. **`city_id` jako kolumna 1. klasy w stanie/ledgerze** — dziś `orders_state.json`/`courier_plans.json`/gps NIE mają pola miasto; konsola ma `tenant_id` = FRANCZYZA, nie geografia; `city_id`/`region_id` NIE ISTNIEJE nigdzie. Wymaga: migracja schematu stanu + alembic konsoli + propagacja przez 10 warstw + backfill. **Wysiłek: L. Ryzyko: wysokie** (to fundament — bez niego reszta nie ma jak wybrać profilu).
3. **`districts_data.py` + kwadranty + adjacency + outside-zones** — 1376 linii danych dzielnicowych + graf sąsiedztwa + satelity, konsumowane przez bundling/drop-zone/reverse-lookup w ≥5 miejscach. Migracja = **plik dzielnic per-miasto** + konsumenci parametryzowani po `city_id`. Drugie miasto bez danych = wszystko „cross-quadrant" (kara ×0.1). **Wysiłek: L. Ryzyko: średnie** (degradacja cicha, nie crash).
4. **Mosty (city_map ×2 + COMPANIES + panel host)** — architektura single-market: 1 panel gastro, 1 flota, `COMPANIES` = twardy rejestr 11 firm z `pickup_rules` po ulicach Białegostoku, 2 identyczne `city_map.json` (bliźniak). Multi-city = **rejestr per-market** + rozstrzygnięcie: wspólna flota (Wolt-style) czy osobna? **Wysiłek: L. Ryzyko: wysokie (architektura + decyzja Adriana).**
5. **Kalibracja korka/prędkości (`V326_OSRM_TRAFFIC_TABLE` + `FALLBACK_BASE_SPEEDS_KMH` + artefakty `build_speed_tiers`/`courier_speed_build`)** — empiria Białystok (n=42494). Konsumowane przez ETA silnika. Migracja mechaniczna (per-city tabela), ale **wymaga DANYCH z 2. miasta** — do czasu ich zebrania profil 2. miasta = bootstrap z Białegostoku (świadomy kompromis). **Wysiłek: M. Ryzyko: średnie** (ETA cicho przekłamana).

---

## 2. SCHEMAT `config/cities.json` (opis — plik bez komentarzy)

Rejestr keyed by slug, `city_id` = obywatel 1. klasy. Wpis `bialystok` (id=1) wypełniony REALNYMI wartościami z kodu (migracja mechaniczna: stała w kodzie ⇐ pole w configu, fallback za flagą OFF).

```
schema_version   : int
default_city_id  : int                     # dziś 1 (bialystok)
cities[slug]:
  id                          : int (city_id, 1. klasa)
  slug, name, active, console_tenant_id, timezone
  center                      : {lat,lng}   # zastępuje BIALYSTOK_CENTER + osrm _BBOX_CENTER
  company_account_fallback_coords : {lat,lng}   # FIRMOWE_KONTO_FALLBACK_COORDS (161)
  bbox_metropolia             : {lat_min..lon_max}  # HARD poison-guard ±55km
  bbox_geocode_engine         : {...}       # HARD walidator geokodu (env dziś)
  bbox_geocode_console        : {...}       # UWAGA: WĘŻSZY niż silnik (rozjazd!)
  bbox_geocache_mutator       : {...}       # tools MUTATOR (destrukcyjny)
  osrm                        : {url_engine,url_console,url_courier_api,url_tools}
  calibration                 : {haversine_road_factor, max_urban_kmh,
                                 fallback_base_speeds_kmh, traffic_table_ref}
  peak_windows                : {engine_time_buckets, long_haul_peak,
                                 console_forecast_peak_hours, ops_window}
  geo_scale_thresholds        : {max_pickup_reach_km, bundle_max_deliv_spread_km,
                                 po_drodze_dist_km, osrm_max_snap_km, r5_detour_*}
  reglas_geo_business         : {breach_min, city_home_key}
  districts_ref               : {engine_module, symbols...}   # wskaźnik, nie kopia danych
  quadrant_score_mult, hosts, bridge_refs
_migration_notes.twins_same_value_multiple_places   # rejestr bliźniaków (te same wartości w N miejscach)
```

Każde pole ma `_source` (plik:linia) — żeby migracja była mechaniczna i audytowalna. Pola bliźniacze (ta sama wartość w N miejscach) mają listę WSZYSTKICH źródeł, żeby zmiana ruszyła je RAZEM (K1 — zgodnie z ZIOMEK_ARCHITECTURE §4 rejestr bliźniaków).

**Świadomy fakt: 3 różne bboxy** (metropolia ±55km / geocode-silnik 52.85-53.35 / geocode-konsola 53.00-53.25) to NIE błąd do ujednolicenia — mają różne role (poison-guard vs akceptacja geokodu vs Nominatim bounded). Config je ROZRÓŻNIA, nie scala.

---

## 3. PLAN MIGRACJI — etapy PRZED 2. miastem/Restimo

**Zasada naczelna (z Przykazania #0 + ZIOMEK_ARCHITECTURE F-1/F-5):** każdy etap zero-behavior-change buduje warstwę odczytu configu z **fallbackiem na dzisiejsze stałe za flagą OFF**; test parytetu = `city=bialystok z configu ≡ bajt-w-bajt dzisiejsze zachowanie`. Dopiero gdy CAŁY silnik czyta z configu (parytet zielony), dodanie 2. miasta = wpis w `cities.json` + dane (OSRM/dzielnice/kalibracja), zero zmian kodu.

### Etap 0 — LOADER + rejestr (zero-behavior-change, flaga OFF)
- Nowy `city_registry.py`: `load_cities()`, `get_city(city_id)`, `default_city()`. Czyta `config/cities.json`.
- Flaga `ENABLE_CITY_REGISTRY` (default OFF) w `ETAP4_DECISION_FLAGS` + `flag_registry.py`.
- **Zero konsumentów jeszcze.** Test: loader parsuje, `default_city().id==1`, wartości == dzisiejsze stałe (assert `get_city(1).road_factor == common.HAVERSINE_ROAD_FACTOR_BIALYSTOK`).
- Ryzyko: **zerowe** (nic nie czyta). Bliźniak: brak. To F-5 (jeden rejestr).

### Etap 1 — kalibracja SKALARNA (config-per-city, flaga OFF, parytet)
Najłatwiejsze bo już częściowo env-param (SCALE-01) i skalary, nie struktury:
- `road_factor`, `max_pickup_reach_km`, `bundle_max_deliv_spread_km`, `osrm_max_snap_km`, `po_drodze_dist_km`, `max_urban_kmh`, `breach_min`.
- Wzorzec: `C.HAVERSINE_ROAD_FACTOR_BIALYSTOK` → `get_city(cid).road_factor if FLAG else C.HAVERSINE_...`.
- **Bliźniaki RAZEM:** road_factor żyje w `common.py:637` + `tools/build_speed_tiers.py:64` + `tools/courier_speed_build.py` + konsola `economics.py:57`. Migruj CZWÓRKĘ w jednym etapie albo świadomie N-D (konsola = osobne repo, osobny loader — patrz Etap 6).
- Test parytetu: replay złotego korpusu, flaga ON vs OFF → bajt-identyczne (bialystok czyta te same liczby). Ryzyko: **niskie** (skalary).

### Etap 2 — bboxy + centroid + geo-skala (config-per-city, flaga OFF)
- `bbox_metropolia` → `coords_in_bialystok_bbox()` staje się `coords_in_city_bbox(ll, cid)`; **UWAGA chokepoint** — wołany w osrm_client/geocoding/feasibility/L2.1 ingest. Zmieniasz sygnaturę → tkniesz WSZYSTKICH wołających (mapa: inwentarz 2.0 §1a).
- `BIALYSTOK_CENTER` → `get_city(cid).center` w courier_resolver (6 iniekcji) + osrm `_BBOX_CENTER` + dispatch_pipeline fallback + chain_eta + bootstrap. **5 bliźniaków — RAZEM** (rejestr `_migration_notes.twins`).
- `bbox_geocode_engine` (env dziś → z configu), `FIRMOWE_KONTO_FALLBACK_COORDS`.
- ⚠ **Wymaga świadomości `city_id` w miejscu wywołania** — a tego dziś nie ma (Etap 4). Dopóki jest 1 miasto, `cid=default_city_id`. Test parytetu jw. Ryzyko: **średnie** (HARD-walidatory — błąd = infeasible/poison; parytet MUSI być bajt-w-bajt).

### Etap 3 — dane dzielnicowe / kwadranty (per-city plik danych, flaga OFF)
- `BIALYSTOK_DISTRICTS`/`_OUTSIDE_CITY_ZONES`/`_DISTRICT_ADJACENCY` → ładowane per `city_id` (plik `districts/{slug}.py` lub json). `districts_ref` w configu = wskaźnik.
- Konsumenci: `district_reverse_lookup.py`, `same_restaurant_grouper.py`, `common.py` quadrant logic — parametryzuj po cid.
- Test: bialystok z pliku == dzisiejszy słownik (dict-equality). Ryzyko: **średnie** (duże dane, ale degradacja cicha nie crash).

### Etap 4 — `city_id` w STANIE/LEDGERZE (FUNDAMENT — wymaga fali SERIAL na rdzeniu)
**To jest etap-fundament — bez niego Etapy 1-3 działają tylko na `default_city_id`.**
- Dodać `city_id` do: `orders_state.json` (per zlecenie), `courier_plans.json`, gps_positions (per kurier), konsola `Delivery`/parcel/weather (alembic + `city_id FK`).
- Propagacja: `panel_watcher` (wejście) stempluje `city_id` przy normalizacji → niesie przez feasibility/scoring/selekcję/kanon → powierzchnie.
- Backfill: istniejący stan = `city_id=1`.
- **To NIE zero-behavior-change dla schematu** (nowe pole), ale zero-behavior gdy wszystko=1. Wymaga: WorldState (F-1) niesie `city_id`; rdzeń SERIAL (nie równolegle z innymi pasami). Test: e2e assess_order z city_id=1 ≡ dziś; regresja pełna.
- Ryzyko: **wysokie** (dotyka wszystkich 10 warstw + 3 repa). Kolejność: PO Etapach 0-3 (profil gotowy), bo dopiero city_id pozwala wybrać profil per-zlecenie.

### Etap 5 — OSRM multi-region (infra, wymaga decyzji ops)
- `osrm.url_*` z configu per `city_id`; deploy multi-region extract LUB router-per-city.
- Konsumenci: `osrm_client.py:43`, `courier_api/config.py:204`, konsola `config.py:67`, `tools/freshness_shadow_monitor.py:34` — RAZEM.
- Ryzyko: **wysokie** (rusza kontenery/deploy — poza tym pasem; wymaga ACK + osobny sprint ops). Test: bialystok → ten sam `:5001`, ETA bajt-identyczne.

### Etap 6 — konsola + mosty (osobne repa, osobne loadery)
- Konsola: własny `city_registry` (nie importuje silnika przez granicę repo) czytający TEN SAM `cities.json` (współdzielony) LUB własny mirror. `tenant_id==1` zaszyte w warstwie ziomek (`history.py:489` + csv) → parametryzuj po city/tenant. `WeatherDaily` + `city_id`.
- Mosty: `city_map.json` (2 kopie → 1 źródło + import), `COMPANIES` per-market, panel host per-city. Decyzja: wspólna flota vs osobna.
- Ryzyko: **wysokie** (architektura wielorynkowa + decyzja biznesowa).

### Etap 7 — tools MUTATORY (ostatnie, bo destrukcyjne)
- `invalidate_city_bugged_geocodes.py` + `purge_streetless_geocode_keys.py` → bbox/token z configu per `city_id`; **guard: nie uruchamiaj cross-city** (mutator na geocache jednego miasta nie może dotknąć drugiego).
- Ryzyko: **średnie** (off-line, ale destrukcyjne jeśli źle) — inwariant „mutator działa tylko na swoje miasto".

---

## 4. KOLEJNOŚĆ + CO SERIAL / CO RÓWNOLEGLE

| Etap | Zależność | SERIAL na rdzeniu? | Zero-behavior (flaga OFF)? |
|---|---|---|---|
| 0 loader | — | nie (dodatek) | TAK (zero konsumentów) |
| 1 kalibracja skalarna | 0 | nie | TAK (parytet bajt) |
| 2 bbox/centroid | 0 | częściowo (chokepoint bbox) | TAK (parytet bajt) |
| 3 dzielnice | 0 | nie | TAK (dict-equality) |
| **4 city_id w stanie** | 0,(1-3 gotowe) | **TAK (rdzeń, fala serial)** | schemat=nowe pole, zachowanie=TAK gdy wszystko=1 |
| 5 OSRM multi-region | 4 | nie (infra) | TAK (bialystok=:5001) |
| 6 konsola+mosty | 4 | nie (osobne repa) | TAK per repo |
| 7 tools mutatory | 2 | nie | TAK |

**Rekomendacja kolejności:** 0 → 1 → 2 → 3 (wszystkie zero-behavior, flaga OFF, parytet bajt — bezpieczne, można równolegle-ish bo rozłączne pliki poza chokepointem bbox) → **4 (SERIAL, fundament)** → 5/6/7 (po fundamencie, każdy osobny sprint + ACK bo infra/repa/destrukcja). Etapy 0-3 NIE wymagają 2. miasta ani decyzji Adriana — czysty dług techniczny „na lata".

---

## 5. TEST PARYTETU (definicja „bialystok z configu ≡ dziś")

Dla każdego etapu 1-3 (i 5-7):
1. **Bajt-parytet replay:** złoty korpus `case_corpus` + `shadow_decisions.jsonl`; flaga ON (czyta config) vs OFF (stała w kodzie) → decyzje/ETA/kolejność stopów **bajt-identyczne** (bo bialystok w configu = te same liczby co stała). Zgodne z F-6 (golden replay = bramka).
2. **Assert wartości u ładowania:** `get_city(1).road_factor == C.HAVERSINE_ROAD_FACTOR_BIALYSTOK` itd. dla każdego zmigrowanego pola (łapie literówkę w configu).
3. **Inwariant strażnik:** `test_city_config_parity` — dla każdego pola w `cities['bialystok']` sprawdź, że == odpowiadająca stała żywego kodu (przez `_source`). Czerwony jeśli ktoś zmieni stałą a nie config (albo odwrotnie) → pilnuje bliźniaków K1.

Dla etapu 4 (city_id): e2e `assess_order` z `city_id=1` na realnych/replay case'ach ≡ dzisiejszy wynik + **pełna regresja `pytest tests/` vs baseline** (Przykazanie #0 ETAP 4).

---

## 6. OSRM — plan multi-instance/multi-profile (rozwinięcie Etapu 5)

Jeden graf per region = twardy fakt (`:5001` = ekstrakt Podlaskie). Warianty:
- **A. Multi-region extract, jeden serwer, wiele portów:** `bialystok→:5001`, `warszawa→:5002`. Config `osrm.url_engine` per city. Prosty, ale N serwerów OSRM (RAM ×N).
- **B. Router-per-city (fasada):** cienki `osrm_router.py` wybiera endpoint po `city_id`; reszta kodu woła `route(a,b,city_id)`. Preferowany — jeden chokepoint, zgodny z K1/F-1.
- **C. Jeden wielki ekstrakt (PL cały):** jeden `:5001` z całą Polską. Najprostszy kod (bez city_id w routingu!), ale RAM/rebuild-time rosną; traci izolację (coords międzymiastowe liczą absurdy bez guardu bbox).

**Rekomendacja:** **B (router-per-city)** — spina się z bbox-per-city (Etap 2) i city_id (Etap 4); jeden punkt zmiany endpointu; zachowuje izolację rynków. Wymaga ops (deploy 2. ekstraktu) — ACK Adriana.

---

## 7. CO WYMAGA DECYZJI ADRIANA (nie zgaduję)

1. **Wspólna flota czy osobna per miasto?** (Wolt-Drive-style pooling vs izolacja). Determinuje architekturę mostów (Etap 6) i czy `city_id` filtruje pulę kurierów. Inwentarz sugeruje hybrydę (config-per-city + city_id-w-danych, jeden deploy), ale to decyzja biznesowa.
2. **OSRM: wariant A/B/C** (§6). Rekomendacja B, ale rusza ops/RAM.
3. **`tenant_id` (franczyza) vs `city_id` (geografia) — relacja.** Dziś konsola miesza (tenant=firma). Czy miasto to wymiar OBOK tenanta, czy tenant=miasto? Determinuje Etap 4+6.
4. **Bootstrap 2. miasta bez danych kalibracji** — startować z profilu Białegostoku (road_factor/traffic/speeds) i re-kalibrować po zebraniu N dostaw? (rekomendacja: tak, świadomy kompromis + monitor rozjazdu).
5. **Priorytet vs reszta FALI-2** — czy Etapy 0-3 (czysty dług, zero-behavior) robimy teraz, czy czekamy aż 2. miasto/Restimo stanie się konkretną datą? (rekomendacja: 0-3 warto teraz — tanie, bezpieczne, kasują ~80 nośnych hardcode; 4-7 gdy jest data ekspansji).

---

## POKRYCIE / LUKI TEGO PASU
- **Zrobione:** weryfikacja 100% kotwic inwentarza 2.0 (grep+AST, linie zaktualizowane), własny licznik (631/124 surowe → potwierdza odsiew 146), `cities.json` z realnymi wartościami bialystok, schemat + plan 8-etapowy + test parytetu + OSRM warianty + 5 decyzji Adriana.
- **NIE robione (poza pasem, zgodnie z zakazem):** żadnej edycji .py, żadnego loadera, żadnego flipu. To recon+design.
- **Rezydualne (dziedziczę z inwentarza 2.0 „JAWNE LUKI"):** `common.py` L700-3706 grep/okna nie linia-po-linii; `geometry.py`/`pipeline_geometry.py`/`insertion_anchor.py` nie czytane liniowo; model LGBM cechy geo nie zaudytowane pod re-trening; panel gastro (Laravel) poza repo.
