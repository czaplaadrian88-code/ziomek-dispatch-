# ZIOMEK — MULTI-CITY INVENTORY (co jest zaszyte pod Białystok)

> Inwentarz do przejścia przez zespół uruchamiający miasto #2. Findingi zostały **zdeduplikowane** (ten sam constant bywał zgłoszony przez kilku audytorów w różnych „lane") i **poziom blokera = wynik weryfikacji** (corrected_blocker tam, gdzie audyt drugiego rzędu skorygował). Cytaty `plik:linia` zachowane.

---

## 0. Podsumowanie

- **Łącznie ~150 zgłoszeń → ~70 odrębnych sprzężeń** po deduplikacji. Reszta to ten sam constant widziany z wielu warstw (np. `BIALYSTOK_CENTER`, `common.py:431` bbox, `V326_OSRM_TRAFFIC_TABLE`, globalne `dispatch_state/`).
- **Twardych P0 (bez tego nie postawisz 2. miasta): ~8 odrębnych** — wszystkie sprowadzają się do DWÓCH decyzji: (a) **infrastruktura routingu** (OSRM + bbox), (b) **architektura tenancy** (globalne `dispatch_state/` + `config.json` + `flags.json` + panel + sekrety + systemd).
- **P1 (blokuje skalę/jakość = MOAT): ~20** — to głównie „wiedza weterana-koordynatora": dzielnice/sąsiedztwa, krzywa ruchu, okna szczytu, roster kurierów + tiery, model LGBM.
- **NAJTWARDSZE 5 (od tego zacznij):**
  1. **OSRM = jedna instancja z mapą Podlasia** (`osrm_client.py:43`) — miasto #2 nie policzy ŻADNEGO czasu dojazdu.
  2. **Metro „poison bbox" Białystok** (`common.py:431-432`) — każda współrzędna spoza pudełka odrzucona jako trucizna → flota niewykonalna.
  3. **Globalny `dispatch_state/` (jeden namespace, ~433 literały ścieżek)** — `restaurant_coords.json`, `orders_state`, sqlite, roster, modele, kalibracje. Brak `STATE_ROOT`.
  4. **`config.json` NIE env-selectowalny** (`common.py:12`) — naturalny nośnik per-miasto, a przypięty na sztywno.
  5. **Tabela dzielnic + graf sąsiedztwa** (`districts_data.py`, `common.py:1298`) — MOAT „która okolica z którą się łączy"; bez tego silnik dowozi jak turysta, nie weteran.

---

## 1. BLOKERY P0 — bez tego nie postawisz 2. miasta

| Obszar | Co zaszyte | plik:linia | Klucz config per-miasto | Cold-start / learned |
|---|---|---|---|---|
| Routing OSRM | Jedna instancja `localhost:5001` z ekstraktem Podlasia; brak grafu dróg innego miasta | `osrm_client.py:43`,`585`,`749`; `/root/osrm/podlaskie-latest.osrm.*` | `city.osrm.base_url` + `city.osrm.extract` | Cold-start fixed: pobierz `.osm.pbf` województwa (Geofabrik), `osrm-extract/partition/customize`, osobny kontener+port |
| Geo „poison bbox" | `BIALYSTOK_BBOX_LAT=(52.6,53.7)`/`LON=(22.3,24.1)` literały; każdy GPS/coord OSRM/geokod spoza pudełka → sentinel/odrzut | `common.py:431-432`,`435`; `osrm_client.py:560`,`646`; `courier_resolver.py:215`; `dispatch_pipeline.py:2994-2996` | `city.service_metro_bbox{lat,lon}` (musi być nadzbiorem geocode bbox — invariant `tests/test_geo_bbox_consistency.py:42`) | Cold-start fixed: pudełko ~±55 km wokół centrum |
| Stan globalny (state root) | `restaurant_coords.json` + cały `dispatch_state/` (orders_state, plany, tiery, logi, sqlite) = jeden namespace, ~433 literały ścieżek, brak `STATE_ROOT` | `geocoding.py:37`; `panel_watcher.py:83`; `bootstrap_restaurants.py:17`; `common.py:2848`; `dispatch_pipeline.py:73`+ | `DISPATCH_STATE_DIR=/var/ziomek/<city>/state` przez jeden helper `common.state_path(name)` | Cold-start: pusty katalog per miasto; silnik zapełnia od 1. ticku. **Wymaga refaktoru** (zastąpienie literałów helperem) |
| `config.json` nie env-selectowalny | Kanoniczne ścieżki + timezone + telegram przypięte do jednego pliku bez selektora | `common.py:11-12`; `scripts/config.json` | `DISPATCH_CONFIG_PATH` (wzór jak `FLAGS_PATH`) | Cold-start fixed: per-miasto `config.json`; mała zmiana kodu |
| Bazy sqlite | `courier_api.db`, `events.db`, `fleet_analytics.db` współdzielone, kluczowane gastro-id jednego tenanta | `dispatch_state/*.db`; `config.json` | ścieżki DB pod `STATE_ROOT/<city>/` | Cold-start: świeże puste DB na 1. uruchomieniu |
| Panel gastro (host+creds) | `BASE_URL="https://www.gastro.nadajesz.pl"` literał + jeden `.secrets/panel.env`; cały ingest/roster/assign w jednym tenancie | `panel_client.py:36-37`,`232`; `panel_roster.py:37`,`98` | `panel.base_url` + `panel.credentials_path` (+ filtr tenanta, jeśli wspólny panel) | External setup: konto/subdomena gastro per miasto. *(uwaga: ścieżka `/admin2017/*` jest uniwersalna — tylko host/creds per-miasto)* |
| systemd (~60 unitów) | Po jednym `dispatch-shadow/panel-watcher/gps/plan-recheck`; brak templatingu | `/etc/systemd/system/dispatch-*.service` | unity szablonowe `dispatch-shadow@<city>.service` + `EnvironmentFile=/etc/ziomek/<city>.env` | External setup: env-file per miasto, zależny od pkt. powyżej |
| Telegram bot + notyfikacje | Jeden token = jeden bot = jedno miasto; cały stack notyfikacji na globalnym `config.json`/`flags.json`/sekretach | `telegram_approver.py:146`,`4247`; `notify_router.py:46`; `.secrets/telegram.env` | per-miasto `.secrets/telegram.env` + `telegram.*` w config per-miasto | External: rejestracja bota w BotFather + grupa per miasto |
| Modele ML + kalibracje (ścieżki) | Wszystkie artefakty modelu/tier/kalibracji = jeden globalny plik; brak klucza tenanta | `ml_inference.py:43`,`47`; `eta_residual_infer.py:29`; `calib_maps.py:62`; `prep_bias_anchor.py:35` | `per_city.paths.{model_root,dispatch_state_root}` z kontekstu `CITY_ID` | External: model OFF na starcie, ścieżki pod state-root miasta |

**Wniosek architektoniczny:** wszystkie powyższe P0 (poza OSRM + bbox) to JEDNA decyzja — **process-per-city**: jeden proces/kontener/VM na miasto z własnym `DISPATCH_STATE_DIR` / `DISPATCH_FLAGS_PATH` / `DISPATCH_CONFIG_PATH` / panel-creds. Dwa „seamy" już istnieją (`DISPATCH_STATE_DIR` w `state_machine.py:180` — tylko orders_state; `DISPATCH_FLAGS_PATH` w `common.py:16`). Do dołożenia: `DISPATCH_CONFIG_PATH`, parametryzacja `panel BASE_URL`, helper `state_path()` zamiast 433 literałów, templated systemd.

---

## 2. P1 — blokuje skalę/jakość (MOAT „dowozi jak weteran")

### 2a. Wiedza geograficzna (sąsiedztwa / korytarze)
| Co zaszyte | plik:linia | Klucz config | Cold-start / learned |
|---|---|---|---|
| Tabela 28 osiedli / ~638 ulic (street→district), tylko literały frozensets | `districts_data.py:11-1285` | `config/cities/<city>/districts.json` (CityGeo registry per tenant) | External: zescrapuj oficjalny rejestr osiedli (analog info.bialystok.pl) lub OSM admin_level=10; statyczne dane municypalne, ~1-2 dni analityka |
| Graf sąsiedztwa dzielnic (ręcznie kurowany, GEO-05) | `common.py:1298-1360` | `config/cities/<city>/district_adjacency.json` (już param `adjacency_map`) | Cold-start: auto z centroidów (próg ~2,5 km, `_adjacency_compute.py`) + przegląd ops; refine 14-30 dni z udanych bundli |
| Mapa kwadrantów/kompasu (OPPOSITE vs SIDEWAYS) | `districts_data.py:1302-1335` | `config/cities/<city>/quadrant_map.json` LUB liczenie bearingu z centroidów | Cold-start: bearing z centroidów dnia 1 (bez okna nauki) |
| `drop_zone_from_address` literał `'białystok'` jako home-city, ignoruje `ZIOMEK_DEFAULT_CITY` | `common.py:1437-1454`,`1449` | `city.home_name` (lowercase), spięty ze źródłem `ZIOMEK_DEFAULT_CITY` | Cold-start fixed; **konieczne ALE niewystarczające** — bez tabeli dzielnic i tak `'Unknown'` |

> Wszystkie 4 degradują „miękko" (Unknown → mnożnik ~0,7, nie hard-reject), więc miasto #2 wstaje, ale traci kredyt bundla, korytarze i weryfikację geokodu — czyli **istotę franczyzowanej wartości**.

### 2b. Realizm ETA (czas dojazdu)
| Co zaszyte | plik:linia | Klucz config | Cold-start / learned |
|---|---|---|---|
| `V326_OSRM_TRAFFIC_TABLE` — godzinowa krzywa ruchu z 42 494 dostaw Białegostoku (15-17 ×1,55 itd.) | `common.py:516-568`,`571` | `city.traffic_table{weekday,saturday,sunday}` | Cold-start: krzywa Białystok jako prior LUB płaska 1,0-1,2; learned 14-30 dni (pipeline GATE-B / `monitor_recalib_oos`) |
| `HAVERSINE_ROAD_FACTOR_BIALYSTOK=1.37` (+ rozjazd 1.3 w `scoring.py:206`) — w 6+ plikach | `common.py:405`; `osrm_client.py:373`; `dispatch_pipeline.py:3802`,`3904`,`6024`; `scoring.py:206` | `city.haversine_road_factor` (jedno źródło, zabij twin 1.3) | Cold-start 1,30-1,40; learned po ~200-500 dostawach (median road_km/haversine_km) |

### 2c. Okna czasowe (szczyty)
| Co zaszyte | plik:linia | Klucz config | Cold-start / learned |
|---|---|---|---|
| Okna szczytu 11-14/17-20 rozsiane w ≥7 plikach bez single-source (+ nie zgadzają się z sob. 16-21 z `project_overview`) | `common.py:1247`; `auto_proximity_classifier.py:227`; `calib_maps.py:87`; `daily_briefing.py:119`; `eta_residual_infer.py:129`,`190`; `event_bus.py:101` | `PEAK_WINDOWS` per-miasto per-daytype, **jeden helper** importowany przez wszystkich | Cold-start: defaulty PL; learned 30 dni z histogramu gęstości zamówień. **Najpierw konsoliduj do 1 czytnika, potem per-miasto** |

### 2d. Roster kurierów / tiery (per-miasto z definicji)
| Co zaszyte | plik:linia | Klucz config | Cold-start / learned |
|---|---|---|---|
| Globalne pliki rosteru (`kurier_ids/piny/tiers`) bez namespace tenanta — kolizja cid/alias/PIN | `courier_admin.py:21-26`; `courier_resolver.py:70`,`377`; `new_courier_pairing.py:55-57`; `courier_ranking.py:30` | `DISPATCH_STATE_ROOT/<city>/...` (część decyzji tenancy) | Cold-start: pusty roster, zapełnia się z gastro + auto-pairing |
| `EXCLUDED_CIDS` — nazwani Białystoccy (owner 21, tech 23, koord 26, retired) + `BAR_ELJOT_COMPANY_ID=27` | `daily_accounting/config.py:7-20` | `per_city.config{excluded_cids,owner_cid,tech_cid,coordinator_cid,company_id}` | Cold-start fixed na onboardingu; lista nieaktywnych rośnie operacyjnie |
| Wirtualny koordynator `cid=26` zaszyty w ≥6 modułach (czasówka/auto-koord/accounting) | `panel_client.py:51`; `czasowka_scheduler.py:41`,`128`,`133`; `auto_koord.py:31`; `courier_resolver.py:791`; `build_v319h_courier_tiers.py:121` | `panel.koordynator_cid` (jedno źródło dla wszystkich 6 miejsc) | Cold-start fixed: odczyt id konta „Koordynator" z panelu miasta. **Bez tego czasówka+auto-koord+accounting milcząco padają** |
| `BIALYSTOK_CENTER=(53.1325,23.1688)` jako syntetyczna pozycja no-GPS/pre-shift — 5 kopii literału | `courier_resolver.py:110`,`1464`,`1527`,`1567`,`1579`; `chain_eta.py:28`; `bootstrap_restaurants.py:19`; `dispatch_pipeline.py:132` | `city.center_coords` (jedno źródło, 5 call-site) | Cold-start fixed: centroid miasta. *(no_gps km jest laundrowane do fleet-avg, ale pre_shift/working_override — NIE → kurier „teleportowany" o setki km)* |
| Auto-pairing czyta jeden roster gastro + jeden arkusz grafiku | `new_courier_pairing.py:48`,`64`,`281`; `panel_roster.py:37`; `fetch_schedule.py:12-13` | per-miasto `gastro base_url` + `schedule_spreadsheet_id` + ścieżki dispatch_state | External: własne konto gastro + arkusz grafiku per miasto |

### 2e. Model ML (MOAT behawioralny)
| Co zaszyte | plik:linia | Klucz config | Cold-start / learned |
|---|---|---|---|
| Ranker LGBM (v1.1 + twomodel) trenowany w 100% na historii Białegostoku | `ml_inference.py:43`,`57` | `per_city.ml.lgbm_model_dir` + `enable_lgbm_primary` (default OFF) | Cold-start: ML OFF, rządzą reguły (fail-soft jest); learned: retrain po ~10-30k decyzji (~3-6 tyg shadow) + `validation_gate_lgbm` przed flipem |
| Absolutne lat/lon Białegostoku jako surowe cechy modelu (splity drzewa bez sensu poza pudełkiem) | `ml_inference.py:341`,`415` | fix inżynierii cech: cechy **relatywne** (dist-to-pickup), nie absolutne lat/lon | Must-learn per miasto; rozważ przebudowę feature-set |
| Cechy dzielnic + graf sąsiedztwa zasilają model | `ml_inference.py:423`,`605`; `common.py:1298` | jak 2a (district map + adjacency per miasto) | External setup + retrain ze słownikiem dzielnic miasta |
| `courier_tiers.json` roster + `tier_ground_truth_cids` = jedna flota Białystok | `ml_inference.py:47`; `eta_residual_infer.py:31`; `dispatch_state/courier_tiers.json` | `per_city.fleet.courier_tiers_path` (schemat tierów globalny) | Cold-start: wszyscy `std`/`new`; learned przez bramki r04 30 dni |
| Strefa „death zone"/peak-meal-window + TZ Europe/Warsaw jako cechy modelu | `calib_maps.py:73`; `auto_proximity_classifier.py:43`,`98-100`; `ml_inference.py:35`,`68` | `per_city.locale.timezone` + `peak_lunch/dinner/high_risk_hours` | Cold-start: TZ na onboardingu (P0 jeśli inna strefa), okna z defaultów → learned |

### 2f. Tenancy notyfikacji / flag (drugorzędne wobec P0, ale per-miasto)
| Co zaszyte | plik:linia | Klucz config | Cold-start |
|---|---|---|---|
| `flags.json` globalny — ~200 flag + tożsamości Białegostoku (chat/user id) w jednym pliku | `common.py:16`; `flags.json` | `DISPATCH_FLAGS_PATH` per miasto (hook istnieje) + rozdziel uniwersalne toggle od overlay tożsamości | Cold-start: szablon uniwersalny + override id |
| Operatorzy Telegram (Adrian+Bartek) jako default `KONIEC_AUTHORIZED_USER_IDS` / `BARTEK_USER_ID` DM-routing | `telegram_approver.py:69`,`3375`; `flags.json` | `telegram.authorized_user_ids` + `coordinator_dm_user_id` (rename z „BARTEK") | Cold-start: id operatorów miasta po `/start` |
| ID grupy/chatów telegramowych duplikowane w kodzie + config + flags (>6 miejsc) | `parser_health_endpoint.py:69`; `czasowka_scheduler.py:36`; `shift_notifications/telegram_send.py:55`; `czasowka_proactive/evaluator.py:50` | `config.json telegram.*` jako JEDYNE źródło, usuń literały fallback | Cold-start fixed na onboardingu |
| `daily_accounting` — arkusz Google + company id + roster wykluczeń (off dispatch path, ale psuje wypłaty) | `daily_accounting/config.py:3`,`7`,`22` | `per_city accounting{spreadsheet_id,excluded_cids,owner_company_id}` | External: arkusz per miasto |
| `panel-no-tenant-id` — brak dyskryminatora tenanta w warstwie panelu (decyzja architektoniczna) | `panel_client.py:36`; `panel_watcher.py`; `state_machine.py:173` | przyjmij process-per-city; `DISPATCH_TENANT_ID` do logów/telegrama | Cold-start: slug per miasto w manifeście |

### 2g. Pojedyncze P1 scoringowe
- **`DIST_DECAY_KM=5.0`** (`scoring.py:27-30`) — decay dopasowany do 0-15 km Białegostoku; team sam zauważył „Warszawa ~12" (komentarz `DIST_DECAY_BY_CITY`). Dla podobnego miasta P2; dla metropolii P1 (komponent dystansu spłaszcza się). Klucz `city.scoring.dist_decay_km`. *(weryfikacja w lane time-windows skorygowała do P2 dla miast porównywalnych — patrz §3)*
- **`bootstrap_restaurants.py:16-17`** — brak parametru `--city`, jeden input/output; uruchomienie dla miasta #2 nadpisuje coords #1. Klucz: arg `--city <slug>`.

---

## 3. P2/P3 — degraduje / kosmetyka (grupowane)

**P2 — kalibracje „degradują, nie łamią" (R6 i OSRM nadal chronią):**
- Prędkości fallback + bucket godzin (`common.py:455-486`), `traffic-fallback-base-speeds` — TYLKO przy OSRM-down. `city.fallback_speeds_kmh` + `time_buckets`. Cold-start: wartości Białegostoku jako default PL.
- `V326_SPEED_MULTIPLIER_MAP` / `DWELL_BY_TIER` / `DRIVE_SPEED_MULT_BY_TIER` (`common.py:2031`,`2057`,`2097`) — score-only/dwell, std pinned 1.0. Defaulty współdzielone, recalib 14-30 dni z outcome.
- Tier-cap matrix + peak split (`common.py:1219-1237`) — SOFT penalty, R6 backstopuje. `per_city.fleet.tier_cap_matrix`.
- Distance-bin boost TomTom (`common.py:620`) — shadow/OFF, 8 segmentów Białegostoku. Cold-start: pusta lista.
- `R1/R5/R3` spready bundla (`feasibility_v2.py:90-95`) — SOFT, fit do Bartka. `BUNDLE_SPREADS` per miasto.
- `address_mismatch` home-city `'bialystok'` (`address_mismatch.py:64-66`,`90`,`102`) + **bliźniak LIVE w panelu** `nadajesz_clone/.../api/dispatch.py:503-526` — shadow po stronie silnika, ale panel user-facing. `city.home_town_key` w OBU. Twin-path completeness!
- Godziny zamknięcia firmy 23:00/24:00 fri-sat (`feasibility_v2.py:56-67`) — za flagą OFF (salvage). `COMPANY_HOURS` per miasto.
- Gate poranny czasówki 09:10 (`common.py:1722-1723`) — tylko sub-flow czasówki. `operating_day_start`.
- Firmowe konto: `FIRMOWE_KONTO_ADDRESS_IDS={161}`, `FALLBACK_COORDS` HQ Nadajesz, stoplist firm (`common.py:3305`,`3410`,`3418`) — feature B2B Białegostoku; dla nowego miasta pusty/inert. `firmowe.*` per miasto (puste = off).
- Most drtusz infra (URL-e Nadajesz, `id=94`, city_map 158 miast) (`drtusz_bridge/config.py:15-17`,`152`) — opcjonalny produkt poza dispatch_v2. Per-miasto JSON, jeśli uruchamiany.
- `restaurant_company_mapping.json` (69 restauracji) — **auto-regeneruje** się z panelu+arkusza miasta (`panel_dropdown_scrape_v2`), realne sprzężenie to 5 stałych w `cod_weekly/config.py`. Back-office, nie dispatch.
- Nominatim publiczny endpoint (`geocode_verify.py:39`) — przy wielu miastach złamie ToS 1 req/s. `city.geocoder.nominatim_url` (self-hosted przed skalą).
- `prep_bias` / `eta_quantile_map` / `rest_freq` / `drive_min OFFSET_TABLE` / ETA-residual boostery — wszystko fail-soft (None/baseline), per-miasto retrain. `per_city.calib.*_path`.
- Logi globalny katalog (`osrm_client.py:46`) — degraduje obserwowalność. `LOG_DIR` pod state-root.
- `geocode-default-city` literał `'Białystok'` (`geocoding.py:447`) — za kill-switchem CITY_AWARE (default ON, panel wins). `geo.default_city`.
- `RYNEK_KOSCUSZKI` return-anchor (`common.py:743-747`), street aliases (`common.py:1374-1391`), outside-city-zones (`districts_data.py:1290`) — część per-miasto geo bundle.

**P3 — wąskie/inert/kosmetyka:**
- `BUG4_TIER_CAP_MATRIX` peak-calibrated (skorygowane P3 — SOFT + R6 backstop + HARD cap default OFF).
- `calib_maps` Warsaw time-slots (skorygowane P3 — env-overridable + shadow fail-soft; „warsaw" = timezone PL, nie miasto).
- OSRM cache TTL/size (`osrm_client.py:44`,`457`) — tuning, degraduje gracefully (skorygowane P3/NOT-A-BLOCKER).
- `shift_notifications` okno 6-23 (`worker.py:678-679`) — już env-overridable, alert-noise only.
- `geocode_neg_cache`/restaurant cache key bare-name (`geocoding.py:641`) — kolizja TYLKO przy wspólnym procesie; one-liner (kwalifikuj kluczem miasta).
- `panel-order-id (zid)` keyspace — kolizja tylko przy wspólnym `orders_state`; pod process-per-city niemożliwa; per-miasto stempel `city_id` w warstwie analytics.
- Nazwani cid w komentarzach (`common.py:1919`; `courier_resolver.py:114`), help-text z nazwiskami (`telegram_approver.py:263`) — kosmetyka.
- Bridge companies drtusz (`drtusz_bridge/config.py:36-124`) — poza MOAT, opcjonalny.
- `uwagi_company_stoplist`, `carry_risk_list` kebab król (`common.py:3418`,`3484`), R7 long-haul 14-17/99km (`common.py:719-724`), wave_scoring DEAD CSV (`wave_scoring.py:82`) — inert/feature-OFF; cold-start pusty/disabled.
- `bootstrap` whitelisty duplikatów + `MANUAL_COORDS_OVERRIDE` (`bootstrap_restaurants.py:28-51`) — `--force` omija; per-miasto `bootstrap_overrides.json`.
- `r04_schema.json` — JUŻ external config (`r04_evaluator.py:36`).
- TZ Europe/Warsaw w notify (`notify_router.py:42`) — uniwersalne dla PL.
- Bot @-handles / personal_admin (`config.json`) — tożsamość, config-driven.
- Healthcheck OSRM coords Białegostoku (`osrm_client.py:816`) — diagnostyka, false „unhealthy".
- `WARSAW tz` config-key NIE konsumowany (`common.py:357`) — false confidence; wepnij dopiero przy ekspansji zagranicznej.

---

## 4. KALIBROWANE Z DANYCH (uczone per-miasto, NIE hardcode)

| Co | Shadow / okno nauki | Cold-start default |
|---|---|---|
| `V326_OSRM_TRAFFIC_TABLE` krzywa godzinowa ruchu | 14-30 dni (GATE-B / `monitor_recalib_oos`, n≥500/bucket) | krzywa Białystok jako prior LUB płaska 1,0-1,2 |
| `HAVERSINE_ROAD_FACTOR` 1,37 | ~200-500 dostaw (median road_km/haversine_km, metoda geo04) | 1,30-1,40 (PL mid-city) |
| `DIST_DECAY_KM` | ~30 dni (decay tak, by p90 dist → ~30-40 score) | 5 (małe) / 8 (mid) / 12 (metro) wg promienia |
| Prędkości fallback + bucket godzin | ~14-30 dni median solo-leg km/h per bucket | 20-32 km/h (Białystok), refine |
| `SPEED_MULTIPLIER_MAP` / `DWELL_BY_TIER` per tier | ~14-30 dni z `eta_calibration_log` / outcome | std=1.0, gold 0.89/slow 1.11 (defaulty policy) |
| Tier-cap matrix (orders/wave per tier per pora) | ~4-8 tyg p90 fal | literały Białegostoku (łapią tylko 7+ patologię) |
| Bundle spready R1/R5/R3 | tygodnie, p90 feasible-bundle spread | wg promienia miasta |
| Peak windows (szczyt popytu) | ~14-30 dni histogram arrival per godz/daytype | 11-14/17-20 (wzorzec PL) |
| Adjacency dzielnic | 14-30 dni z udanych bundli (po starcie z centroidów) | auto z centroidów (próg ~2,5 km) + przegląd ops |
| Ranker LGBM + ETA-residual + prep-bias + eta-quantile + rest_freq | ~3-6 tyg shadow (10-30k decyzji), bramka `validation_gate_lgbm` | OFF / baseline (fail-soft) |
| Tiery kurierów (cid→tier) | 30-60 dni `courier_reliability` throughput + r04 | wszyscy `std`/`new`; owner może ręcznie zaseedować gwiazdy |

> **Zasada (z MEMORY):** ucz do OUTCOME (realny R6), NIE do agreement człowieka (~17,5% szum). Bramka kod/flip = człowiek.

---

## 5. UNIWERSALNE (zostają globalne — NIE ruszać)

| Reguła | plik:linia | Dlaczego globalna |
|---|---|---|
| **R6 / SLA 35 min in-bag (food-cooling)** | `common.py:685`; `feasibility_v2.py:38`,`53`; `scoring.py:31-32` | Fizyka stygnięcia jedzenia — identyczna w każdym mieście. Strefa soft 30→35 ta sama. (Tier-aware 35 T1/2 / 40 T3 = polityka jakości, też uniwersalna) |
| **R27 ±5 min frozen committed window** | `common.py:3023` | Uniwersalna kurtuazja/zaufanie do umówionego czasu |
| Wagi scoringu 0.30/0.25/0.25/0.20 + `HARD_TIER_BAG_CAP` | `scoring.py:22`,`25`; `common.py:1235` | Filozofia dispatchu, nie geografia |
| `FLOOR_MIN=8` (parking+dwell+handover) w drive_min calib | `drive_min_calibration.py` | Fizyczny floor, nie per-miasto |
| Czasówka triggers `[60,50,40]` min (pickup-relative) | `flags.json`; `czasowka_proactive/evaluator.py:56` | Lead-time przed umówionym pickupem — uniwersalny; już w flags.json |
| `MAX_BAG_SANITY_CAP=8` | `common.py:351` | Praktycznie uniwersalny (łapie patologię) |
| `MAX_PICKUP_REACH_KM` | `common.py:344` | Per-miasto, ale JUŻ flags.json hot-reload (wzór do naśladowania) |
| Schemat tierów (gold/std+/std/slow/new, A/B/UNK) | — | Uniwersalny; tylko CZŁONKOSTWO per-miasto |
| `validation_gate_lgbm`, `add_new_courier`, grouper time-tolerance 5 min, parsery panelu (`STATUS_MAP`, `/admin2017/*`) | `validation_gate_lgbm.py`; `courier_admin.py:63`; `same_restaurant_grouper.py:28`; `panel_html_parser.py` | Mechanizmy/algorytmy uniwersalne — działają niezmienione po wstrzyknięciu per-miasto danych. **Wzorzec do reszty geo-warstwy.** Parser panelu uniwersalny *dopóki* franczyza używa gastro |
| Timezone `Europe/Warsaw` | `common.py:357` | Uniwersalny dla franczyzy POLSKIEJ (cała PL = Europe/Warsaw). P3-future tylko przy ekspansji zagranicznej |
| Country `'Polska'` w query geokodu | `geocoding.py:515` | Uniwersalny PL; per-miasto dopiero cross-border |

---

## 6. OBALONE / już-konfigurowalne (nie marnuj pracy)

| Finding | Werdykt | Dowód |
|---|---|---|
| `ZIOMEK_DEFAULT_CITY` env „pod-konsumowane" | **NOT-A-BLOCKER** — env działa, jest test `test_default_city.py`; tylko geo-warstwa go nie czyta (osobny P1) | `dispatch_pipeline.py:60`,`837`,`941` |
| `GEOCODE_BBOX_*` service bbox = „hardcoded P1" | **już-configurowalne (P2/P3)** — wszystkie 5 to `os.environ.get` z komentarzem „Multi-tenant Warsaw: bbox env-overridable per deploy" | `common.py:906-910` |
| `FIRMOWE_KONTO_FALLBACK_COORDS` = P3 bloker | **NOT-A-BLOCKER** — odpala tylko dla `address_id∈{161}`; dla miasta #2 ścieżka inert | `common.py:3305`,`3410`,`945` |
| `time-warsaw-timezone` = P2 | **NOT-A-BLOCKER** dla franczyzy PL (cała Polska = jedna strefa) | `common.py:357` |
| `czasowka-triggers` = sprzężenie miasta | **REFUTED** — czyta `flags.json` przez `load_flags()`, pickup-relative, uniwersalne | `czasowka_proactive/evaluator.py:56-58` |
| `restaurant_company_mapping.json` = „100% błędny, do wymiany" | **overstated → P2** — auto-rebuild z panelu+arkusza miasta przed każdym `--write` | `cod_weekly/run_weekly.py:80-103`; mapping `:160-174` |
| `MAX_PICKUP_REACH_KM=15` / `MAX_BAG_SANITY_CAP=8` | **NOT-A-BLOCKER** — JUŻ flags.json (SCALE-01), wzorzec docelowy | `common.py:344`,`351` |
| OSRM cache TTL „finite route set" | **REFUTED** — komentarz, nie sprzężenie; klucz cache = czyste coords, degraduje gracefully | `osrm_client.py:44`,`420` |
| `calib_maps` „Warsaw" time-slots | **overstated → P3** — „warsaw" = timezone Europe/Warsaw (cała PL), shadow fail-soft, ścieżki env-overridable | `calib_maps.py:50`,`54-61`,`73-93` |
| `r04_schema.json` | **już config** — bramki w external JSON, swap nie code-edit | `r04_evaluator.py:36` |
| `address-geocode` adres `'Białystok'` append | **częściowo obalone** — primary path bierze city z panelu (CITY_AWARE default ON); literał tylko za kill-switchem | `geocoding.py:436-447`; `common.py:864` |

---

### Kolejność prac dla launch-teamu (rekomendacja)
1. **Decyzja tenancy = process-per-city** → dołóż `DISPATCH_CONFIG_PATH`, helper `state_path()`, templated systemd, parametryzuj panel `BASE_URL`+creds. (zdejmuje WIĘKSZOŚĆ P0 jednym ruchem)
2. **OSRM + bbox** → per-city ekstrakt + `city.service_metro_bbox` (mirror wzoru env z `GEOCODE_BBOX_*`).
3. **Geo bundle per-miasto** (`config/cities/<city>/`): districts + adjacency + quadrant + outside-zones + aliases + `city.home_name` + `city.center_coords`. (odblokowuje MOAT i cechy LGBM)
4. **Roster/tożsamości**: `coordinator_cid`, `excluded_cids`, telegram per-miasto, panel/grafik per-miasto.
5. **Kalibracje od zera** (§4) — cold-start z defaultów Białegostoku jako prior, learned 14-60 dni przez istniejące pipeline'y; ML OFF do graduacji bramką.