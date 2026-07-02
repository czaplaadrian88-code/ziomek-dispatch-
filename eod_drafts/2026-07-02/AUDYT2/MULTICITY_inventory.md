# AUDYT 2.0 Ziomka — MULTI-CITY READINESS (inwentarz założeń jedno-miastowych)

**Data:** 2026-07-02 · **Tryb:** READ-ONLY (zero edycji/env/restart/git) · **Lane:** inwentaryzacja hardcode single-city
**Kontekst strategiczny:** ekspansja Adriana (Warszawa / Restimo Q3 2026 / Wolt Drive). Dziś nawet walidator-prawdy współrzędnych to bbox Białegostoku.
**Zakres:** silnik `dispatch_v2/` (core+tools), konsola `nadajesz_clone/panel/backend/`, `courier_api/`, mosty (papu/parcel/drtusz), configi `/etc/systemd/system/dispatch-*`.

---

## 📊 METRYKA GŁÓWNA (do dashboardu entropii)

**`single-city-hardcodes` ≈ 146** (odrębne, nośne miejsca w kodzie PRODUKCYJNYM; konwencja liczenia niżej).

| Warstwa | Liczba nośnych miejsc | Charakter dominujący | Ryzyko |
|---|---|---|---|
| **Silnik rdzeń** (dispatch_v2 core, ~14 plików) | **~46** | HARD-walidator (bbox-poison) + fikcja pozycji `BIALYSTOK_CENTER` + kalibracja korków/dzielnic | 🔴 najwyższe |
| **Silnik tools/** (off-line replay/kalibracja) | **~24** | kalibracja + 2 MUTATORY geocache (bbox/token miasta) | 🟠 średnie (2 mutatory groźne) |
| **Konsola backend** | **~30** | default-miasto + kalibracja (ROAD_FACTOR/PEAK/pogoda) + `tenant_id==1` zaszyte | 🔴 wysokie |
| **courier_api** | **6** | default-miasto (`_HOME_KEY`, sufiks `, białystok`) + OSRM `:5001`; ZERO tenant/city | 🟠 średnie |
| **Mosty** (papu ~9 + drtusz ~12 + paczka ~17) | **~38** | single-market z natury: 1 panel gastro, `city_map.json`, `COMPANIES` | 🔴 wysokie (architektura) |
| **systemd** | **2** | brak parametru MIASTA w `Environment=`; tylko TZ `Europe/Warsaw` + okno 06–20 | 🟢 niskie |

**Konwencja liczenia:** liczę ODRĘBNE nośne miejsca (definicja stałej/tabeli LUB użycie niosące rolę) w kodzie produkcyjnym; wykluczam testy, `eod_drafts/`, linie czysto-komentarzowe. Klaster `BIALYSTOK_CENTER` (1 definicja + 6 iniekcji w courier_resolver) liczę jako 7 (każda iniekcja niezależnie degraduje). Surowy grep daje 173 wystąpienia `BIALYSTOK_*` w kodzie silnika (z komentarzami) i 42 zahardkodowane centroidy — metryka 146 to ich odsiew do nośnych.

---

## 1. SILNIK — RDZEŃ (najgroźniejsza warstwa)

### 1a. HARD-walidatory geograficzne (pękną/zafałszują GŁOŚNO albo cicho w całym rynku)

| plik:linia | fragment | ROLA | 2. miasto | wysiłek |
|---|---|---|---|---|
| `dispatch_v2/common.py:561-562` | `BIALYSTOK_BBOX_LAT=(52.6,53.7)` `BIALYSTOK_BBOX_LON=(22.3,24.1)` | **HARD-walidator** (filtr trucizny OSRM/GPS) | **cicho ŹLE → GŁOŚNO**: każda współrzędna 2. miasta poza bboxem = „trucizna" → `route()`/`table()` zwracają sentinel infeasible; kurier/adres 2. miasta niewidzialny dla routingu | **M** |
| `dispatch_v2/common.py:522` `coords_in_bialystok_bbox()` | walidator nazwany po mieście, wołany w osrm_client (route/table guard), geocoding, feasibility, L2.1 ingest | **HARD-walidator** (chokepoint) | jw. — jeden punkt dławienia całego rynku | M (część bbox) |
| `dispatch_v2/common.py:1050-1053` | `GEOCODE_BBOX_LAT_MIN=52.85 … LON_MAX=23.45` (env-override, default Białystok+~28km) | **HARD-walidator** (akceptacja geokodu `_in_service_bbox`) | **cicho ŹLE**: adres 2. miasta poza bboxem → geokod odrzucony (verify_reject/bbox_reject) → negatywny cache → „brak adresu" | **S** (już env-param!) |
| `dispatch_v2/common.py:535` | `HAVERSINE_ROAD_FACTOR_BIALYSTOK = 1.37` | kalibracja (fallback OSRM + tools) | cicho ŹLE: współczynnik siatki ulic Białegostoku; ETA fallback przesunięte | **S** |
| `dispatch_v2/osrm_client.py:43` | `OSRM_BASE = "http://localhost:5001"` | infra single-extract | **cicho ŹLE**: jeden lokalny ekstrakt mapy (Podlaskie); coords Warszawy liczone na grafie Białegostoku → absurd albo fallback haversine; ETA/committed-time kłamią bez błędu | **L** |
| `dispatch_v2/osrm_client.py:530` | `_BBOX_CENTER = (53.1325, 23.1688)` (placeholder w `table()`) | HARD-walidator (podmiana złych coords) | cicho ŹLE: złe coords 2. miasta „naprawiane" centrum Białegostoku | S |

### 1b. Fikcja pozycji `BIALYSTOK_CENTER` (klaster — kurier bez GPS/pre-shift)

`BIALYSTOK_CENTER = (53.1325, 23.1688)` powielony jako **niezależna stała w 4 plikach** + 6 iniekcji. To fizyczne serce fikcji „kurier bez pozycji = w centrum miasta".

| plik:linia | rola | 2. miasto |
|---|---|---|
| `dispatch_v2/courier_resolver.py:110` (def) + `:1114,1488,1497,1551,1591,1603` (6× `cs.pos = BIALYSTOK_CENTER`) | heurystyka (synthetic pos no_gps/pre_shift/rescue) | **cicho ŹLE**: kurier 2. miasta bez GPS „teleportowany" do centrum Białegostoku → km_to_pickup/ETA/feasibility liczone od złego punktu → propozycje losowe |
| `dispatch_v2/dispatch_pipeline.py:132` `_BIALYSTOK_CENTER_FALLBACK` + `:245-250` `(0,0)→center` | heurystyka | jw. — sentinel (0,0) naprawiany centrum Białegostoku |
| `dispatch_v2/chain_eta.py:28` + `:128,149` | heurystyka (ETA łańcuchowa fallback) | jw. — brak pozycji → centrum Białegostoku |
| `dispatch_v2/bootstrap_restaurants.py:19` + `:163` (dystans od centrum) | kalibracja/bootstrap | cicho ŹLE — dystans restauracji liczony od centrum Białegostoku |
| `dispatch_v2/common.py:3561` `FIRMOWE_KONTO_FALLBACK_COORDS=(53.13222,23.16844)` | default-coords (konto firmowe 161) | cicho ŹLE — fallback centrali Nadajesz.pl (Białystok) |

**Rola:** heurystyka, ale konsekwencja kaskadowa (feeduje feasibility+scoring+ETA). **Wysiłek: M** (parametryzacja centrum per-miasto + świadomość, którego miasta kurier dotyczy).

### 1c. Dane dzielnicowe / kwadranty / strefy (z natury jedno-miastowe)

| plik:linia | fragment | ROLA | 2. miasto | wysiłek |
|---|---|---|---|---|
| `dispatch_v2/districts_data.py:11` | `BIALYSTOK_DISTRICTS` — 28 osiedli z info.bialystok.pl, ulice→dzielnica (1376 linii) | heurystyka (drop-zone, kwadranty bundlowania) | **cicho ŹLE**: adres 2. miasta nie mapuje się na żadną dzielnicę → „outside city" → degradacja bundlowania po drodze | **L** |
| `dispatch_v2/districts_data.py:1290` | `BIALYSTOK_OUTSIDE_CITY_ZONES` (satelity: Wasilków/Choroszcz/Zabłudów…) | heurystyka | cicho ŹLE: satelity Białegostoku; miejscowości 2. rynku nieznane | M |
| `dispatch_v2/common.py:1441` | `BIALYSTOK_DISTRICT_ADJACENCY` (graf sąsiedztwa dzielnic) | heurystyka (bundle same/adjacent/cross) | cicho ŹLE: sąsiedztwo dzielnic Białegostoku; dla 2. miasta puste → wszystko „cross-quadrant" (kara ×0.1) | M |
| `dispatch_v2/common.py:3243-3284` | `V327_BUNDLE_{CROSS,ADJACENT,SAME}_QUADRANT_SCORE_MULT` (0.1/interp/1.0) | heurystyka | cicho ŹLE: mnożniki bazują na kwadrantach Białegostoku | S (mnożniki OK, dane kwadrantów nie) |
| `dispatch_v2/district_reverse_lookup.py` (131 linii) | reverse coords→dzielnica z BIALYSTOK_DISTRICTS | heurystyka | cicho ŹLE | S (konsument danych) |
| `dispatch_v2/same_restaurant_grouper.py` | grupowanie po dzielnicy/kwadrancie | heurystyka | cicho ŹLE | S |

### 1d. Kalibracja czasu/korków/prędkości (Białystok empirycznie)

| plik:linia | fragment | ROLA | 2. miasto | wysiłek |
|---|---|---|---|---|
| `dispatch_v2/common.py:542-548` | `FALLBACK_BASE_SPEEDS_KMH` (20-32 km/h, komentarz „peak korkowy Białegostoku") | kalibracja | cicho ŹLE: prędkości fallback siatki Białegostoku | S |
| `dispatch_v2/common.py:551-573` `get_time_bucket` | okna godzinowe rush 15-19/lunch 11-15 (weekday) | kalibracja | cicho ŹLE: krzywa doby jednego rynku | S |
| `dispatch_v2/common.py:603-655` | `V326_OSRM_TRAFFIC_TABLE` (weekday/saturday/sunday, n=42 494 dostaw Białystok) | kalibracja | **cicho ŹLE**: mnożniki korka per-godzina kalibrowane na Białystok; ETA 2. miasta systematycznie przekłamana | M |
| `dispatch_v2/common.py:750` | `V326_OSRM_DISTANCE_BIN_BOOST_PEAK` (TomTom Białystok, short 2.3/med 1.5/long 1.15) | kalibracja | cicho ŹLE | S |
| `dispatch_v2/common.py:853-854` | `LONG_HAUL_PEAK_HOURS_START=14 / END=17` | kalibracja (R7) | cicho ŹLE — okno peak; **UWAGA**: R7 faktycznie WYŁĄCZONE (`LONG_HAUL_DISTANCE_KM=99.0`, `common.py:852` — „4.5km za agresywne dla Białystoku") → dziś martwe, ale przy re-włączeniu w 2. mieście zadziała na złym oknie | S |
| `dispatch_v2/feasibility_v2.py:471-493` | R7 long-haul isolation w peak 14-17 (czyta C.LONG_HAUL_*) | kalibracja (dziś no-op) | cicho ŹLE gdy re-enabled | S |
| `dispatch_v2/drive_min_calibration.py` | prędkości/no_gps offset z korpusu Białystok | kalibracja (analiza) | obojętne (off-line) | S |
| `dispatch_v2/geocoding.py:255-256` | legacy fallback `city="Białystok"` gdy CITY_AWARE off | default-miasto | cicho ŹLE (flaga CITY_AWARE_GEOCODING=True mityguje) | S |
| `dispatch_v2/ml_inference.py` (2×) | cechy geo/dzielnica dla modelu | kalibracja | cicho ŹLE (model trenowany na Białystok) | M |
| `dispatch_v2/common.py:3456` | `FIRMOWE_KONTO_ADDRESS_IDS=frozenset({161})` | tenant-blind (1 konto) | obojętne/cicho — zaszyte 1 konto Białystok | S |

### 1e. Progi geo-skali (rozmiar miasta zaszyty w regułach)

| plik:linia | fragment | ROLA | 2. miasto | wysiłek |
|---|---|---|---|---|
| `dispatch_v2/common.py:481` | `MAX_PICKUP_REACH_KM=15.0` (env-param, SCALE-01) | HARD fast-filter „pickup_too_far" | cicho ŹLE: zasięg 15 km skrojony na Białystok; Warszawa większa | **S** (już env!) |
| `dispatch_v2/common.py:2332` | `BUNDLE_MAX_DELIV_SPREAD_KM=8.0` (env-param) | heurystyka (R1 spread) | cicho ŹLE: rozrzut dostaw dobrany do rozmiaru Białegostoku | S |
| `dispatch_v2/common.py:2397` | `PO_DRODZE_DIST_KM=2.0` | heurystyka (po-drodze) | cicho ŹLE | S |
| `dispatch_v2/common.py:2744` | `OSRM_MAX_SNAP_KM=5.0` (env-param) | HARD (snap guard) | cicho ŹLE (skala) | S |
| `dispatch_v2/common.py:2820-2828` | `R5_DETOUR_*_KM` (8.0/0.5/7.5, env-param) | heurystyka | cicho ŹLE (skala) | S |

**Uwaga pozytywna:** `EXCLUDE_BY_CID` NIE jest hardcode miasta — lista czytana runtime z `manual_overrides.get_excluded_cids()` (operator `/stop`, `courier_resolver.py:1444`). Statyczna `EXCLUDED_CIDS` istnieje tylko w `daily_accounting/config.py:7` (10 cidów: 21/23/26/61/207/284/354/426/476/498 — konta owner/tech/nieaktywni jednej floty) — to lista floty, nie miasta.
**Brak „blackout windows"** w sensie geograficznym/czasowym — słowo „blackout" w kodzie oznacza wyłącznie parser-degraded/OSRM-degraded (`parse_continuity_guard.py`), nie okno rynkowe.

---

## 2. SILNIK — `tools/` (off-line replay/kalibracja; ~24 miejsc)

Najgroźniejsze — **2 MUTATORY** operujące na wspólnym `geocode_cache.json`:

| plik:linia | fragment | ROLA | 2. miasto | wysiłek |
|---|---|---|---|---|
| `dispatch_v2/tools/invalidate_city_bugged_geocodes.py:27-28,70` | `BBOX_LAT=(52.85,53.35) BBOX_LON=(22.85,23.45)` → invaliduje coords POZA bbox | **HARD-walidator MUTATOR** | **cicho ŹLE — skasuje CAŁY geocache 2. miasta jako „corrupted"** | S |
| `dispatch_v2/tools/purge_streetless_geocode_keys.py:27,41` | `CITY={"białystok","bialystok"}` guard | **HARD-walidator MUTATOR** | **cicho ŹLE — tnie złe klucze** (token miasta nie pasuje) | S |
| `dispatch_v2/tools/build_speed_tiers.py:64,72` | `ROAD_FACTOR=1.37` + `MAX_KMH=90 # urban Białystok` | kalibracja (artefakt `speed_tiers` KONSUMOWANY przez silnik ETA) | cicho ŹLE — tiers z białostockim road-factor/capem | M |
| `dispatch_v2/tools/courier_speed_build.py:91` | `MAX_KMH=90 # urban Białystok` + `REF_KMH=28.2` | kalibracja (artefakt mnożniki per-kurier) | cicho ŹLE | M |
| `dispatch_v2/tools/freshness_shadow_monitor.py:34` | `OSRM_URL=localhost:5001` (biegnie w timerze) | config-market | cicho ŹLE | M |
| `dispatch_v2/tools/faza7_daily_kpi.py:55,90,155,228` | whitelist floty + `_peak_window`/`_in_excluded_window` (biegnie w timerze) | lista-market + kalibracja | cicho ŹLE — KPI na białostockich oknach/flocie | M |
| `dispatch_v2/tools/{analyze_traffic_v2_shadow,base_score_decompose,demand_forecast,eta_quantile_calib}.py` | okna peak 11-14/17-20, TomTom ratio | kalibracja | cicho ŹLE (analiza) | S |
| `dispatch_v2/tools/rule_deviation_report.py:35-36` | `R1_DELIV_SPREAD_KM=8.0 R5_PICKUP_SPREAD_KM=1.8` | kalibracja | cicho ŹLE (progi rozmiaru miasta) | S |
| `dispatch_v2/tools/{obj_econ_replay,obj_harness,sequential_replay,reassignment_shadow,osrm_fallback_smoke,roadfactor_gap}.py` | road_factor/coords/`city="Białystok"`/`", białystok"`/`coords_in_bialystok_bbox` | kalibracja/heurystyka (off-line) | cicho ŹLE / obojętne | S |

---

## 3. KONSOLA backend (`nadajesz_clone/panel/backend/app/`; ~30 miejsc)

| plik:linia | fragment | ROLA | 2. miasto | wysiłek |
|---|---|---|---|---|
| `app/core/config.py:67` | `osrm_url="http://127.0.0.1:5001"` | infra single-extract | **cicho ŹLE** — trasy/ETA/committed-time 2. miasta na mapie Białegostoku | M |
| `app/core/config.py:131-134` | `geocode_bbox_lat_min=53.00 … lng_max=23.40` (+`bounded=1` w geocode.py:140) | HARD-walidator | **cicho ŹLE** — Nominatim `bounded` zwraca PUSTO dla 2. miasta → „brak adresu" | S |
| `app/core/config.py:206` | `ziomek_default_city="Białystok"` | default-miasto | **cicho ŹLE** — brak town → stempluje Białystok | S |
| `app/services/economics.py:57,59,61,575` | `ROAD_FACTOR=1.37`, `BREACH_MIN=35.0`, `CITY_HOME="białystok"` + split satelitów | kalibracja + HARD reguła + default-miasto | **cicho ŹLE** — cały wolumen 2. miasta jako „satelita", P&L/breach przekłamane | S |
| `app/services/economics.py:158,200` | `city=… or "Białystok"` | default-miasto | cicho ŹLE | S |
| `app/services/forecast21.py:237,60,58-59` | `_BIALYSTOK=(53.13,23.16)` (pogoda), `PEAK_HOURS={17,18,19}`, `THROUGHPUT 2.5/2.7` | kalibracja | **cicho ŹLE** — prognoza obsady 2. miasta bierze pogodę+szczyt Białegostoku | S |
| `app/services/geocode.py:37,44,52-68` | `_viewbox/_in_bbox/_zone_hint` na bbox | default-miasto/heurystyka | cicho ŹLE — bias+bounded odcina 2. miasto | S |
| `app/services/quote.py:36,48` | `ZONE_II_KM=8.0 ZONE_II_SURCHARGE=20.0` | heurystyka/kalibracja | cicho ŹLE — próg strefy II na rozmiar Białegostoku (wycena publiczna) | S |
| `app/api/dispatch.py:132,159,186,405-407,449-526` | `KNOWN_TOWNS` seed + `("Białystok",)` default + `check_street_town` sugerujący „Białystok" (korpus 363 par tylko Białystok) | default-miasto/heurystyka | **cicho ŹLE** — ulica 2. miasta „silnie białostocka" → fałszywy alert „popraw na Białystok" | M |
| `app/integrations/ziomek/shadow_quote.py:41,15,329` | `_CITY_KMH=22.0`, `city=… or "Białystok"` | kalibracja + default-miasto | cicho ŹLE | S |
| `app/integrations/ziomek/delivery_town.py:38,175,191` | `_HOME_KEY="bialystok"` (dom vs satelita) | default-miasto | cicho ŹLE — 2. miasto zawsze „poza domem" | S |
| `app/integrations/ziomek/committed_time.py:22-32` | import `get_traffic_multiplier` z `dispatch_v2.common` | kalibracja (dziedziczy tabelę silnika) | cicho ŹLE (1:1 z silnikiem) | (poza konsolą) |
| `app/integrations/ziomek/history.py:489`, `courier_history_csv.py:126`, `customer_history_csv.py:266-269` | `tenant_id == 1` zaszyte | **tenant-blind (hardcode)** | **cicho ŹLE** — historia/ekonomia/rankingi nowego tenanta/miasta NIEWIDOCZNE | M |
| `app/models/weather.py:20` | `WeatherDaily` bez `city_id` | tenant-blind (geo) | cicho ŹLE — jedna tabela pogody dla wszystkich | M |
| `app/core/config.py:80,150` | `sms_link_base/panel_base_url="https://bialystok.nadajesz.pl"` | kosmetyka/infra | obojętne (subdomena) | S |

---

## 4. `courier_api` (`scripts/courier_api/`; 6 nośnych)

| plik:linia | fragment | ROLA | 2. miasto | wysiłek |
|---|---|---|---|---|
| `courier_api/config.py:204` | `OSRM_BASE="http://localhost:5001"` | infra single-extract | **cicho ŹLE** — ETA (`live_eta`/`_attach_fallback_eta`) 2. miasta znikają; trasa degraduje do linii prostej | L |
| `courier_api/delivery_town.py:38,176,192` | `_HOME_KEY="bialystok"` (rdzeń modułu) | default-miasto | **cicho ŹLE** — 2. miasto: doklejanie miasta do każdego adresu + płatny Google reverse-geocode per zlecenie | M |
| `courier_api/courier_orders.py:118` | klucz geocache `f"{variant}, białystok"` | default-miasto (fallback coords) | **cicho ŹLE** — klucz nie trafia w cache 2. miasta → `None` → pin/ETA/trasa znikają | S/M |
| `courier_api/courier_orders.py:536,539` | `delivery_town.enrich_address(...)` konsument | tenant-blind | cicho ŹLE (propaguje wynik) | S |

**Pozytyw:** `courier_orders.py:220` `_courier_position()` — **BRAK fikcji `BIALYSTOK_CENTER`** (zwraca `None` gdy brak GPS, poprawnie obsłużone). Ten dług jest TYLKO w silniku (`courier_resolver`), nie tu. Brak stałych bbox/road_factor; `haversine` = czysta geometria.

---

## 5. MOSTY (papu ~9 + drtusz ~12 + paczka ~17 = ~38; single-market z natury)

Wszystkie 3 mosty celują w **JEDEN panel gastro** (`www.gastro.nadajesz.pl`), jedną flotę, jeden silnik, jednego operatora Telegram, jedną mapę miejscowości.

| plik:linia | fragment | ROLA | 2. miasto | wysiłek |
|---|---|---|---|---|
| `papu_dispatch_bridge/city_map.json` **≡** `drtusz_bridge/city_map.json` (2 identyczne kopie) | 157 miejscowości aglomeracji białostockiej → id stref panelu | zaszyty-market | GŁOŚNO na spoza-listy (skip+Telegram); sama lista=Białystok | M |
| `papu_dispatch_bridge/config.py:22`, `drtusz_bridge/config.py:17`, `panel/…/config.py:251` | `GASTRO_BASE="https://www.gastro.nadajesz.pl"` (3×) | zaszyty-panel | cicho ŹLE — 2. miasto = ten sam panel/flota | S |
| `drtusz_bridge/config.py:36-124` | `COMPANIES` — 11 firm (cid 208/428/…/542 → rid 232-236/161) + `pickup_rules` po ulicach Białegostoku (grochowa/boruty/mickiewicza) | zaszyty-market | cicho ŹLE — twardy rejestr jednego rynku | L |
| `drtusz_bridge/config.py:136` | `NADAJESZ_KURIER_GASTRO_ID=94` | zaszyty-market | cicho ŹLE (jeden słownik kuriera) | S |
| `panel/…/ziomek/parcel_overlay.py:155` | „Cross-tenant (konsola=global flota)", brak filtra miasta | tenant-blind | **cicho ŹLE** — paczki wszystkich miast w jednej flocie, proponowane białostockim kurierom | L |
| `panel/…/ziomek/adapter.py:289`, `dispatch_push.py:111-113`, `shadow_quote.py:329` | default `town or "Białystok"` do assess_order/geokodu | default-miasto | **cicho ŹLE** — pusty town → adres dopasowany do białostockiej ulicy | S |
| `panel/…/ziomek/adapter.py:34` | `_BASE_LAT,_BASE_LNG=53.1325,23.1688` (tylko `if self.mock`) | kosmetyka (mock) | obojętne w prod | S |
| `papu_dispatch_bridge/restaurant_map.json` | 5×UUID Papu→gastro rid | zaszyty-market | GŁOŚNO (niezmapowana→skip+alert) | M |

**Czyste (miasto-agnostyczne):** `dispatch_v2/parcel_assign.py`, `parcel_lane_merge.py` — hydraulika na coords/oid, ale karmią tę samą jedną flotę.

---

## 6. systemd (`/etc/systemd/system/dispatch-*`)

**ŻADEN `Environment=` nie niesie parametru MIASTA** (brak bbox/CENTER/OSRM URL/nazwy/coords — zweryfikowane grepem). Jedyne założenia jedno-RYNKOWE:

| plik:linia | fragment | ROLA | 2. miasto | wysiłek |
|---|---|---|---|---|
| ~10 timerów (`cod-weekly*`, `daily-accounting`, `faza7-kpi`, `overrides-reset`, `restic-backup`, `r04-evaluator`…) | `OnCalendar=… Europe/Warsaw` | config-market (TZ) | obojętne w PL / cicho ŹLE poza PL | S |
| `dispatch-new-courier-watch.timer:5` | `OnCalendar=06..20:0/30 Europe/Warsaw` | config-market (okno godzin) | cicho ŹLE — zaszyte okno operacyjne 06–20 | S |

`ExecStart`/`WorkingDirectory` `/root/.openclaw/…` = single-install, nie miasto.

---

## 🧭 WERDYKT: tenant_id / tenant-świadomość

**NIEJEDNOZNACZNY — częściowo TAK w konsoli, całkowite NIE w silniku, ZERO city_id nigdzie.**

- **Silnik (`dispatch_v2`) + `courier_api`: brak JAKIEJKOLWIEK świadomości tenanta/miasta.** `grep tenant_id|city_id|region` → pusto. `orders_state.json` niesie tylko adres/coords/courier_id/status/czasy — **żadnego pola miasto/tenant/region**. Pozycje GPS kluczowane wyłącznie `courier_id`. Drugie miasto = drugi, NIEROZRÓŻNIALNY zbiór danych na tych samych ścieżkach `dispatch_state/*.json`.
- **Konsola: modele SĄ tenant-świadome** (`app/models/base.py:25-30` `TenantMixin` → `tenant_id NOT NULL FK`), ale **`tenant` = FRANCZYZA/FIRMA, NIE geografia.** `city_id`/`region_id` **NIE ISTNIEJE nigdzie** (potwierdzone w `alembic/versions/` i `app/models/`). `city` żyje wyłącznie jako wolny string bez FK (`Delivery.sender_city`). Co gorsza, warstwa ziomek **zaszywa `tenant_id==1`** (history/courier/customer_history) → nawet nowy tenant jest niewidoczny dla ingestu historii/ekonomii.
- **Brak jednego źródła „miasto/region obsługi".** Dziś nie ma sposobu, by zlecenie/kurier niósł swój rynek przez warstwy.

---

## 🗺️ MAPA PARAMETRYZACJI per warstwa + REKOMENDACJA

| Warstwa | Docelowy wzorzec |
|---|---|
| Stałe kalibracji (road_factor, tabela korków, speeds, peak, prędkości) | **config-per-city** (profil miasta w rejestrze `cities.json`) |
| Bbox/center/districts/quadranty/outside-zones | **config-per-city** (bbox+centroid per miasto; plik dzielnic per miasto) |
| Routing (OSRM) | **multi-region extract** LUB router-per-miasto wybierany po `city_id` |
| Stan/ledger (orders_state, Delivery, parcel, gps) | **`city_id`/`region_id` jako kolumna pierwszej klasy** |
| Progi geo-skali (MAX_PICKUP_REACH, spready) | **config-per-city** (już częściowo env-param przez SCALE-01) |
| Mosty (city_map, COMPANIES, panel host) | **rejestr per-market** (mapa miejscowości + firmy + panel host per rynek) |
| systemd | bez zmian (TZ per kraj; okna godzin → do configu miasta jeśli poza PL) |

**REKOMENDACJA (1 akapit):** Hybryda, NIE osobny deploy per miasto (traci pooling floty i cross-city, mnoży ops) i NIE sam tenant_id-w-danych (stałe geo to nie dane najemcy). Konkretnie: **(1)** wprowadzić rejestr `cities.json` keyed by `city_id` — profil per-miasto (bbox metropolii + bbox geokodu + centroid zamiast `BIALYSTOK_CENTER` + `road_factor` + tabela korków + okna peak + endpoint OSRM + plik dzielnic/kwadrantów + default city string); to naturalne rozszerzenie już rozpoczętego wzorca SCALE-01 (`MAX_BAG_SANITY_CAP`/`MAX_PICKUP_REACH_KM`/`EARLY_BIRD` env→flags.json). **(2)** dodać `city_id` jako kolumnę pierwszej klasy w stanie/ledgerze (orders_state, `Delivery`, parcel, gps_positions), aby każde zlecenie/kurier niósł swój rynek — dziś `tenant_id` w konsoli znaczy franczyzę, a silnik nie ma nic. **(3)** OSRM musi serwować ekstrakt multi-region (albo router per miasto wybierany po `city_id`); dzielnice/kwadranty stają się plikiem danych per-miasto. Efekt: **config-per-city dla kalibracji + city_id-w-danych dla routingu/atrybucji, na jednym wspólnym deployu i puli floty** tam gdzie to korzystne (Wolt-Drive-style). Fundament ten spina się z audytem spójności (K1 „brak jednego źródła" — tu brakuje jednego źródła MIASTA).

---

## 🔟 TOP-10 NAJGROŹNIEJSZYCH „CICHO ŹLE"

Ranking po (blast-radius × bezgłośność × prawdopodobieństwo trafienia w 2. mieście):

1. **`osrm_client.py:43` + `courier_api/config.py:204` + konsola `config.py:67` — OSRM `localhost:5001` (jeden ekstrakt Podlaskie).** Wszystkie trasy/ETA/committed-time/kolejność stopów 2. miasta liczone na mapie Białegostoku → absurd albo cichy fallback do linii prostej. Obietnice dostawy kłamią BEZ błędu. Najszerszy blast, w 3 warstwach.
2. **`common.py:561-562` `BIALYSTOK_BBOX` (poison-guard).** Chokepoint całego routingu: coords 2. miasta = „trucizna" → sentinel infeasible. Kurier/adres 2. miasta niewidzialny. Cicho przechodzi w „brak kandydatów".
3. **`courier_resolver.py:110+6×` `BIALYSTOK_CENTER` fikcja pozycji.** Kurier 2. miasta bez GPS/pre-shift „teleportowany" do centrum Białegostoku → km/ETA/feasibility od złego punktu → propozycje losowe. Powielone w 4 plikach (rozjazd bliźniaków).
4. **Konsola `geocode bbox bounded=1` (`config.py:131-134`+`geocode.py:140`).** Adresy 2. miasta → PUSTA odpowiedź Nominatim → cały pipeline adres→coords cicho pada; strona zamawiania „brak adresu".
5. **`tools/invalidate_city_bugged_geocodes.py:27` (+`purge_streetless…:27`) — MUTATORY na bboxie/tokenie miasta.** Uruchomione w 2. mieście uznają CAŁY rynek za „corrupted" i wyczyszczą/potną geocache. Destrukcyjne, ciche.
6. **`tenant_id==1` zaszyte w warstwie ziomek konsoli (`history.py:489`, `courier/customer_history_csv`).** Historia/ekonomia/rankingi nowego tenanta/miasta niewidoczne — ciche zero danych (ML/KPI/P&L 2. miasta puste).
7. **`economics.py:61,575` `CITY_HOME="białystok"` + split satelitów.** Cały wolumen 2. miasta traktowany jako „satelita", breach jako „artefakt promesy 35" → P&L i statystyki breach systematycznie przekłamane.
8. **`districts_data.py` `BIALYSTOK_DISTRICTS`/`_ADJACENCY`/`OUTSIDE_ZONES` + kwadranty.** Adres 2. miasta → brak dzielnicy → „outside city" i „cross-quadrant" (kara bundla ×0.1) → degradacja bundlowania po drodze, ciche gorsze trasy.
9. **`V326_OSRM_TRAFFIC_TABLE` + `FALLBACK_BASE_SPEEDS_KMH` (common.py) + artefakty `build_speed_tiers`/`courier_speed_build`.** Kalibracja korka/prędkości Białystok (n=42 494) konsumowana przez ETA → czas dojazdu 2. miasta cicho przekłamany (za optymistyczny lub pesymistyczny).
10. **`parcel_overlay.py:155` cross-tenant „global flota" + defaulty `"Białystok"` w geokodzie mostów (adapter/dispatch_push/shadow_quote).** Paczki/zlecenia dowolnego miasta wpadają do jednej floty i geokodują się jako białostockie — mieszanie rynków bez wyjątku/alarmu.

---

## POKRYCIE

**Silnik rdzeń (przeczytane w całości/kontekstowo):** `common.py` (L1-700 pełny + grep+okna L700-3706 dla wszystkich klas stałych), `osrm_client.py` (pełny), `districts_data.py` (struktura + granice), `geocoding.py` (bbox/default-city/normalize), `courier_resolver.py` (BIALYSTOK_CENTER def+iniekcje, excluded-cids source), `dispatch_pipeline.py` (grep BIALYSTOK_CENTER+fallback), `feasibility_v2.py` (R7 peak), `daily_accounting/config.py` (EXCLUDED_CIDS), `config.json`, `chain_eta.py`/`bootstrap_restaurants.py`/`district_reverse_lookup.py`/`same_restaurant_grouper.py`/`drive_min_calibration.py`/`ml_inference.py` (grep+kontekst). Weryfikacja braku: `blackout` (geo), `EXCLUDE_BY_CID` (runtime), `city_id`/`tenant` w silniku (pusto).
**Silnik tools/:** wszystkie 149 plików przeczesane wieloma zestawami wzorców (subagent) — geo/bbox/coords/peak/whitelist/OSRM/TZ.
**Konsola backend:** `config.py`, `services/{economics,forecast21,geocode,zone_mapping,zone_match,quote}.py`, `models/{base,dispatch,weather}.py`, `api/{dispatch,coordinator,public_tracking}.py`, `integrations/ziomek/{adapter,committed_time,delivery_town,dispatch_push,shadow_quote,history,courier_history_csv,customer_history_csv,parcel_*}.py`, `alembic/versions/` (grep city_id/region).
**courier_api:** wszystkie źródła `.py` (kluczowo `courier_orders.py`, `delivery_town.py`, `config.py`, `main.py`); `templates/*.html` (czyste).
**Mosty:** oba configi w całości, `city_map.json` (obie kopie identyczne), obie `parsing.py`, `bridge.py`/`panel.py`/`papu_client.py`/`panels.py`, `restaurant_map.json`, 3 moduły paczkowe konsoli + `parcel_assign.py`/`parcel_lane_merge.py`.
**systemd:** wszystkie unity `dispatch-*` `.service/.timer` + drop-iny `.d/*.conf` (Environment=/OnCalendar/ExecStart).

## JAWNE LUKI

- **`common.py` L700-3706 czytane grepem/oknami, nie linia-po-linii** — ryzyko rezydualne: pojedyncza stała geo zapisana nietypowo (np. inline coords w funkcji pomocniczej) mogła umknąć. Główne bloki (bbox/road_factor/traffic/districts/quadrant/firmowe/geo-skala) pokryte.
- **`geometry.py` (52 l.), `pipeline_geometry.py` (44 l.), `insertion_anchor.py` (152 l.)** — nie otwarte w całości; grep coords nie wykazał hardcode centrów, ale logika kwadrantów/geometrii nie zaudytowana liniowo.
- **Konsola `services/fleet.py` (FLT-02 heatmap/saturation per strefa), `geo_heatmap.py` (okna doby), `fleet_state.py` (~1200 l. — tylko fragmenty OSRM/ETA), `tracking_map.py`, `jobs/*`** — grep zrobiony, pełny odczyt nie.
- **Model danych runtime `dispatch_state/*.json`** — struktura (brak pola miasto) potwierdzona POŚREDNIO przez konsumpcję w kodzie, nie przez inspekcję żywych plików (read-only, nie chciałem dotykać).
- **Sam panel gastro (`www.gastro.nadajesz.pl`, Laravel)** — system docelowy mostów, poza tym repo; jego wewnętrzna obsługa miast/stref niewidoczna z audytowanego kodu.
- **`ml_inference.py` / model LGBM** — odnotowane 2 trafienia geo, ale nie zaudytowano zestawu cech modelu pod kątem, ile z nich to cechy dzielnicowe/geo Białegostoku (trening na 1 mieście = osobny temat re-treningu).
- **Baseline alembic konsoli `ebc6a1f9dfaf` (867+ l.)** — sprawdzony grepem pod `city_id/region`, nie odczytany w całości.
- **`WeatherDaily` bez `city_id`** potwierdzone, ale pełna mapa modeli konsoli pod kątem „geo-tabele bez wymiaru miasta" (poza weather) nie domknięta.
