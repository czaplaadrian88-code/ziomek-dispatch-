# ZIOMEK — ARCHITEKTURA MULTI-CITY + PLAYBOOK NOWEGO MIASTA

> Dokument decyzyjny + instrukcja uruchomienia miasta #2. Prostym polskim. Każda rekomendacja oparta o findingi z audytu (cytuję `plik:linia` lub id findingu). Rzeczy niepewne oznaczam **[NIEPEWNE]**.

---

## 1. MODEL NAJEMCY (kluczowa decyzja): jak uruchomić miasto #2

### Co mówią findingi (twardy grunt pod decyzję)
Silnik trzyma **globalny, mutowalny stan w module-level singletonach** i w jednym płaskim katalogu:
- `osrm_client._route_cache` / `_table_cache` — globalne dicty kluczowane tylko po zaokrąglonym lat/lon (`routing-infra`, `traffic-cache-finite-route-set-assumption`).
- `common.load_flags()/load_config()` — mtime-cache na sztywnych ścieżkach `scripts/config.json` + `scripts/flags.json` (`notif-singleton-config-flags-state`).
- `panel_client._session` + `_session_lock` + jeden plik `panel_session_cache.json` — dwa panele w jednym procesie biją się o jeden CookieJar/CSRF (`panel-tenant-api`).
- **433 sztywnych literałów `/root/.openclaw/workspace/dispatch_state`** w 243 plikach; NIE ma jednej zmiennej `STATE_ROOT` (`infra-state-dir-433-hardcoded-literals`).
- Locki per-plik w jednym wspólnym katalogu (`infra-global-file-locks-shared-dir`).

Wniosek z tego jest jednoznaczny i **powtarza się w 7 różnych lane'ach**: in-process multi-tenancy = ogromny, ryzykowny refaktor (trzeba by przewlec `tenant_id` przez setki call-site'ów i znamespace'ować każdy plik stanu, każdy cache, każdy lock).

### Trzy opcje

**Opcja A — pełna izolacja: osobny proces + osobny stan + osobny config + osobny OSRM per miasto**
- Izolacja: ★★★★★ (awaria/korupcja stanu miasta A nie dotyka B; osobne restarty; osobny blast-radius).
- Koszt infra: średni (każde miasto = własny zestaw ~60 usług systemd + kontener OSRM + RAM na mapę).
- Refaktor: **mały** — kod zostaje wspólny, parametryzujemy tylko ścieżki/URL-e przez env. Wykorzystuje istniejące haki (`DISPATCH_FLAGS_PATH` działa, `DISPATCH_STATE_DIR` częściowo).
- Ryzyko cross-tenant: **zerowe** (fizyczny rozdział plików/procesów).
- Czas do 2. miasta: **najkrótszy realnie osiągalny**.

**Opcja B — jeden multi-tenant proces z `tenant_id` wszędzie**
- Izolacja: ★ (jeden crash kładzie wszystkie miasta; jeden lock serializuje cały ruch — `infra-global-file-locks-shared-dir` wprost ostrzega przed „throughput collapse").
- Koszt infra: niski (jeden proces).
- Refaktor: **ogromny i wysokiego ryzyka** — namespace na każdym z 433 literałów stanu, na każdym singletonie/cache/locku, na PIN-space, na order-id (zid). Sprzeczne z `Przykazaniem #0` (zmiana częściowa = niezakończona, a tu klas zmian są dziesiątki).
- Ryzyko cross-tenant: **wysokie** (PIN collision → kurier B loguje się do kontekstu A; zid collision → order 483504 jednego miasta nadpisuje drugie; jeden geocode_cache miesza rozkłady ulic dwóch miast).
- Czas do 2. miasta: długi.

**Opcja C — hybryda: wspólny KOD, per-city deploy/config/state (proces-per-miasto na wspólnym repo)**
- To w praktyce Opcja A „z dyscypliną": jedno repo, jeden artefakt kodu, a różnicuje go tylko per-city plik env + per-city katalog stanu + osobny kontener OSRM. Systemd jako szablony `name@<city>.service` karmione `/etc/ziomek/<city>.env`.
- Łączy zalety A (izolacja, mały refaktor) z DRY na poziomie kodu (jedna baza kodu, jedna ścieżka regresji/testów).

### ✅ REKOMENDACJA: **Opcja C (proces-per-miasto, wspólny kod)**

Uzasadnienie wprost z findingów — **co najmniej 6 niezależnych lane'ów rekomenduje proces-per-miasto jako „path of least resistance"**: `panel-tenant-api` („Process-per-city is the realistic tenancy architecture"), `calibration-ml-bootstrap` („PROCESS-PER-CITY is the path of least resistance"), `state-infra-deploy` („Recommended tenancy model: PROCESS-PER-CITY"), `traffic-speed`, `business-rules`, `notifications-coordinator`. Powód jest strukturalny: stałe są czytane **przy imporcie modułu**, więc izolacja per-proces jest naturalna i darmowa — a in-process multi-tenancy walczyłaby z każdym singletonem i lockiem.

Konsekwencja: **NIE budujemy `tenant_id`**. Budujemy jeden korzeń konfiguracji (`CITY_ENV` / `STATE_ROOT` / `CONFIG_PATH` / `FLAGS_PATH` / `OSRM_BASE` / `PANEL_BASE_URL`) i odpalamy osobny komplet usług per miasto. `tenant_id` jest implicit = „który proces".

⚠️ Jedyny obszar, gdzie proces-per-miasto NIE wystarcza, to **przyszły warehouse danych do ML** (jeśli kiedyś zlejemy `shadow_decisions.jsonl` wszystkich miast do jednego treningu — wtedy surowe `zid` aliasują się między miastami, `panel-order-id-keyspace-collision` → P3). Rozwiązanie: stemplować `city_id` przy zapisie eventu, klucz złożony `(city_id, zid)`. To robota offline, nie blokuje startu.

---

## 2. CO GLOBALNE vs PER-MIASTO (warstwa configu)

### Zostaje WSPÓLNE (kod + uniwersalne reguły — identyczne w każdym mieście)
- **Fizyka jedzenia**: R6 cap 35 min (`BAG_TIME_HARD_MAX_MIN`), rampa 30→35, tier-aware stretch 35/40, R27 ±5 zamrożony pickup, `DWELL_PICKUP_FLAT_MIN=1.0`, `drive_min FLOOR_MIN=8`, `MAX_KMH=90/MIN_KMH=2` sanity (`rules-r6-thermal-35min-universal`, `traffic-speed` universal).
- **Algorytmy/mechanizmy**: haversine, kd-tree, `same_restaurant_grouper` (przyjmuje resolver jako argument — wzorzec do naśladowania, `geo-group-time-tolerance-universal`), `classify_trajectory`, taksonomia relacji (SAME/SIMILAR/SIDEWAYS/OPPOSITE), wagi scoringu 0.30/0.25/0.25/0.20, parser adresów PL (regex), `validation_gate_lgbm`, `add_new_courier` (silnik bootstrapu rostera).
- **Kontrakt panelu gastro**: `STATUS_MAP`, `IGNORED_STATUSES`, ścieżki `/admin2017/*`, parsowanie `list-users`, semantyka pól (`czas_kuriera` HH:MM, timestampy) — uniwersalne **dopóki miasto jest na tym samym software gastro** (`panel-status-map-platform-universal`). Inny panel = nowy adapter (robota dev, nie config).
- **Schemat tierów** gold/std+/std/slow/new (nazwy uniwersalne; przypisanie per-miasto).
- **Strefa czasowa** Europe/Warsaw — uniwersalna dla franczyzy w PL (`time-warsaw-timezone-global` → NOT-A-BLOCKER; P3 dopiero przy ekspansji zagranicznej).
- **Czasówka triggery [60,50,40] min** — pickup-relative, już w flags.json, uniwersalne (`time-czasowka-triggers-universal`).

### Kształt pliku config PER-MIASTO (proponowany `city/<slug>/config.json` + `<slug>.env`)

```
city/<slug>/
├── <slug>.env              # systemd EnvironmentFile — same ścieżki/URL-e
│   STATE_ROOT=/var/ziomek/<slug>/state
│   DISPATCH_STATE_DIR=$STATE_ROOT
│   DISPATCH_FLAGS_PATH=/var/ziomek/<slug>/flags.json
│   DISPATCH_CONFIG_PATH=/var/ziomek/<slug>/config.json   # ⚠ HAK DO DODANIA (common.py:12 dziś sztywny)
│   OSRM_BASE=http://localhost:<port>                      # ⚠ HAK DO DODANIA (osrm_client.py:43 literał)
│   PANEL_BASE_URL=https://<panel>                         # ⚠ HAK DO DODANIA (panel_client.py:36 literał)
│   PANEL_ENV=/var/ziomek/<slug>/.secrets/panel.env
│   GEOCODE_BBOX_LAT_MIN/MAX, GEOCODE_BBOX_LON_MIN/MAX     # ✅ JUŻ env-driven
│   METRO_BBOX_LAT_MIN/MAX, METRO_BBOX_LON_MIN/MAX         # ⚠ HAK DO DODANIA (common.py:431 literał)
│
├── config.json             # geo + panel + telegram + ścieżki
│   geo.city_center=[lat,lon]            # zastępuje BIALYSTOK_CENTER (5 kopii!)
│   geo.return_anchor=[lat,lon]          # RYNEK_KOSCUSZKI
│   geo.haversine_road_factor=1.37       # cold-start, potem uczone
│   geo.default_city_name, home_town_key
│   panel.koordynator_cid                # zastępuje 26 (≥5 plików)
│   panel.base_url, panel.credentials_path
│   telegram.group_chat_id, personal_admin_id, bot_token,
│            authorized_user_ids[], coordinator_dm_user_id, shift_notify_chat_id
│   schedule.operating_day_start/close, late_close_dows, peak_windows[]
│   accounting.spreadsheet_id, excluded_cids[], owner_cid, tech_cid, company_id
│   firmowe.account_address_ids[], fallback_coords, uwagi_stoplist[]   # puste dla nowego miasta
│
├── geo/                     # paczka geo (statyczna, raz zbudowana)
│   districts.json           # street→district (zastępuje districts_data.py)
│   district_adjacency.json, quadrant_map.json + opposite_pairs
│   outside_city_zones.json, street_aliases.json
│
├── flags.json              # kopia uniwersalnego szablonu + per-city override + ID-ki
├── traffic/                # KALIBROWANE Z DANYCH (cold-start = kopia Białystoku)
│   traffic_table.json (V326 curve), fallback_speeds.json, dist_bin_boost.json
└── state/                  # dispatch_state per miasto (puste na starcie)
    restaurant_coords.json, geocode_cache.json, kurier_{ids,piny}.json,
    courier_tiers.json, orders_state.json, events.db, *.lock, logs/, models/<slug>/
```

**Trzy warstwy wartości** (zgodnie z `universal_vs_percity`):
1. **Uniwersalne** → zostają stałymi w kodzie (warstwa default; nowe miasto NIE musi ich podawać).
2. **Per-miasto cold-start fixed** → wpisywane ręcznie przy onboardingu (geo bbox, center, koordynator_cid, godziny pracy, telegram ID, excluded_cids). Bez uczenia.
3. **Per-miasto kalibrowane z danych** → start od defaultu/kopii Białystoku, dostrajane po N dniach shadow (traffic table, road_factor, dist_decay, tier caps, dwell, modele ML).

---

## 3. PLAYBOOK NOWEGO MIASTA (krok po kroku)

### Faza 0 — Provisioning infra (cold-start, 1–2 dni, BEZ uczenia)
1. **OSRM**: pobierz ekstrakt OSM województwa nowego miasta (Geofabrik `.osm.pbf`) → `osrm-extract/partition/customize` → odpal dedykowany kontener na osobnym porcie → `OSRM_BASE` w env. (`routing-osrm-single-instance-podlaskie-map` P0 — bez tego ZERO tras).
2. **Geo bboxy** (razem, z inwariantem `GEOCODE_BBOX ⊂ METRO_BBOX`, pilnowanym przez `test_geo_bbox_consistency`):
   - `METRO_BBOX` = pudło ~±55 km wokół centrum (poison-filter, `geo-metro-bbox-hardcoded-poison-filter` P0).
   - `GEOCODE_BBOX` = pudło ~±28 km (obszar obsługi). **Jeśli zapomnisz — każdy poprawny geocode jest odrzucony → KOORD storm** (`geo-geocode-service-bbox-default` P2 ale „katastrofalne-ale-trywialne").
   - `city_center` = jeden geocode centroidu (zastępuje BIALYSTOK_CENTER w 5 plikach — mapa kompletności!).
3. **Panel gastro**: provisioning konta/subdomeny → `PANEL_BASE_URL` + `panel.env` (login/hasło) w per-city secrets (`routing-panel-host-single-tenant` P0). Odczytaj `koordynator_cid` z panelu (≠26!) — inaczej padają czasówki, auto-KOORD i accounting (`couriers-coordinator-virtual-cid-26`, `notif-koordynator-virtual-cid-26` P1).
4. **STATE_ROOT**: pusty katalog per miasto + env (`STATE_ROOT`, `DISPATCH_STATE_DIR`, `DISPATCH_FLAGS_PATH`, `DISPATCH_CONFIG_PATH`). (`infra-state-dir-433` P0 — wymaga wcześniejszego refaktoru `common.state_path()`, patrz Faza A roadmapy).
5. **Telegram**: nowy bot w BotFather + grupa koordynatorów → `bot_token` w per-city secrets, `group_chat_id`/`authorized_user_ids`/`coordinator_dm_user_id` w config (`notif-bot-token-secrets` P0, `notif-koniec-authorized-user-ids` P1). **Bez tego ryzyko cichego DM do Adriana** (`notif-adrian-chat-fallback-literal`).
6. **systemd**: szablony `dispatch-*@<city>.service` z `EnvironmentFile=/etc/ziomek/<city>.env` (`infra-systemd-units-single-instance` P0).

### Faza 1 — Dane geo i restauracje (cold-start, dane statyczne)
7. **Paczka geo**: zescrapuj rejestr osiedli nowego miasta (odpowiednik info.bialystok.pl) lub OSM `admin_level=10` → `districts.json` + ręcznie/centroidami `district_adjacency.json` + `quadrant_map.json` (z bearingu centroidów) + `outside_city_zones.json`. (`geo-bialystok-districts-table-hardcoded` P1, `geo-district-adjacency-graph-hardcoded` P1). **To MOAT** — bez tego Ziomek jeździ „jak turysta": bundling/po-drodze/korytarze spadają do trybu czysto geometrycznego (degradacja, nie crash).
8. **Restauracje**: `bootstrap_restaurants.py --city <slug>` przeciw eksportowi adresów z panelu miasta → geocode → `restaurant_coords.json`; method_per_entry (strict/token/alias) auto-derywowany. Whitelisty food-courtów startują puste, uzupełniane z dry-run raportu (`rest-coords-json-global-single-tenant` P0, `rest-bootstrap-*`).
9. **Roster**: startuje PUSTY. `add_new_courier` / `/nowy <cid> <imię>` / auto-pairing z grafiku → `kurier_ids/piny/tiers`. PIN-space resetuje się per miasto (`couriers-global-roster-state-files`, `couriers-shared-pin-space`). Wszyscy startują jako `std`/`new` (`couriers-tier-ground-truth-named-cids`).

### Faza 2 — Cold-start kalibracji (kopiuj prior Białystoku)
10. **Traffic/speed**: skopiuj `V326_OSRM_TRAFFIC_TABLE`, `FALLBACK_BASE_SPEEDS_KMH`, `haversine_road_factor=1.37`, `peak_windows=11-14/17-20` jako prior (`traffic-*`, `business-rules`). Działa od pierwszego ticku, ETA jedynie lekko obciążone.
11. **ML/calib**: WSZYSTKO OFF (LGBM_PRIMARY OFF, DRIVE_MIN OFF, AUTO_PROXIMITY OFF, prep/eta/residual SHADOW) — silnik regułowy prowadzi, brak modeli NIE blokuje (`ml-*` cold-start safety). Tabele calib (eta_quantile, prep_bias) nieobecne → fail-soft None → czysty baseline.

### Faza 3 — Shadow i walidacja gotowości (zanim go-live)
12. **Ile dni shadow**: traffic table / road_factor / dist_decay → **14–30 dni** żywych dostaw zanim rekalibrujesz (`traffic-v326`, `routing-osrm-traffic-table`). Tier caps / dwell → **30–60 dni**. Modele LGBM → **~10–30k zalogowanych decyzji (~3–6 tygodni)** + `validation_gate_lgbm` GO/NO-GO przed flipem.
13. **Walidacja gotowości do go-live** (checklist):
    - OSRM zwraca sensowne trasy na próbce in-city (health_check z lokalnymi koordami — `routing-healthcheck`).
    - Inwariant `GEOCODE_BBOX ⊂ METRO_BBOX` zielony, 0 fałszywych poison-reject na próbce realnych adresów.
    - `koordynator_cid` poprawny: czasówki (>60 min) są wykrywane (≠0).
    - Roster ma realnych kurierów z PIN-ami, telegram trafia do właściwej grupy (test wiadomości).
    - Regresja CAŁEGO Ziomka (`pytest tests/`) zielona vs baseline (Przykazanie #0 ETAP 4).
    - `restaurant_coords.json` pokrywa restauracje miasta (brak `pickup_coords=None`).

---

## 4. CO Z KALIBRACJĄ I ML PRZY ZERO DANYCH (cold-start strategy)

Strategia: **uniwersalne defaulty/prior → shadow → flip dopiero gdy metryka dojrzeje**. Struktura jest już bezpieczna (`calibration-ml-bootstrap`: „cold-start safety is structurally good").

| Warstwa | Cold-start (dzień 0) | Dojrzewanie | Flip gdy |
|---|---|---|---|
| Selekcja/feasibility | **Silnik regułowy** (R6, scoring, bbox) — działa od razu | — | zawsze ON |
| Traffic table (V326) | Kopia Białystoku LUB flat 1.0–1.2 | 14–30 dni predicted-vs-actual per godzina (pipeline GATE-B / `monitor_recalib_oos`) | OOS walidacja zielona |
| haversine_road_factor | 1.37 (typ. PL mid-city) | ~200–500 dostaw, regresja road_km/haversine_km | po zebraniu próby |
| dist_decay_km | wg promienia (mały 5 / metro 12) | ~30 dni, z rozkładu odległości | gdy p90 mapuje na ~30-40 score |
| tier caps / dwell / speed mult | neutralne (mult 1.0, dwell flat 3.5) LUB kopia Białystoku | 30–60 dni, p90 per tier/pora | po rekalibracji |
| LGBM ranker + ETA-residual | **OFF (shadow)**, modele nieobecne → fail-soft | 10–30k decyzji, retrain per miasto | `validation_gate_lgbm` GO + ACK |
| AUTO_PROXIMITY | OFF | ≥1 tydz. shadow | po kalibracji progów |

⚠️ **Ważna pułapka ML** (`ml-raw-latlon-features` P1): modele używają **absolutnych lat/lon jako feature** — splity drzew są bez sensu poza pudłem Białystoku. „Re-pointowanie modelu" NIE wystarcza, trzeba **retrenować per miasto**, a najlepiej przy okazji zmienić feature engineering na **city-relative** (dist-to-pickup zamiast surowego lat/lon), żeby modele generalizowały. To jest ten moment, gdzie MOAT (jakość „weterana") wraca dopiero po retreningu — przez pierwsze tygodnie nowe miasto jeździ na samym silniku regułowym (działa, ale bez warstwy ML quality).

**Zasada uczenia** (z MEMORY): ucz do **OUTCOME** (realny R6), NIE do zgody człowieka (~17,5% = szum). Bramka kod/flip = człowiek + ACK.

---

## 5. ROADMAPA FAZOWA: minimum żeby ruszyć 2. miasto → pełny multi-tenant

Każda faza przez **Przykazanie #0** (stan na żywo + testy bazowe zielone → fix u źródła → mapa kompletności bliźniaczych ścieżek → dowody nie deklaracje → regresja całego Ziomka → backup→py_compile→test→ACK→1 restart → rollback gotowy).

### FAZA A — „Najmniejszy cut: jeden korzeń stanu i konfiguracji" (fundament, blokuje wszystko)
**Cel**: zamienić sztywne literały na env-haki, mirrorując istniejący wzorzec (`DISPATCH_FLAGS_PATH` już działa, `GEOCODE_BBOX_*` już działa).
1. `common.state_path(name)` + env `STATE_ROOT/DISPATCH_STATE_DIR` → migracja **433 literałów** w 243 plikach (dziś tylko 15 honoruje hak). **Mapa kompletności**: orders_state, courier_plans, courier_tiers, kurier_ids/piny, restaurant_coords, geocode_cache, events.db, courier_api.db, fleet_analytics.db, locki, logi, models/. (`infra-state-dir-433`, `infra-global-sqlite-dbs`, `infra-global-file-locks`).
2. `DISPATCH_CONFIG_PATH` env (mirror FLAGS_PATH) — `common.py:12` (`infra-config-json-not-env-overridable` P0).
3. `OSRM_BASE` z env — `osrm_client.py:43` (`routing-osrm`/`infra-osrm` P0).
4. `PANEL_BASE_URL` + `PANEL_ENV` z env — `panel_client.py:36-37` (`panel-base-url`/`panel-single-credential` P0).
5. `METRO_BBOX_*` z env (mirror GEOCODE_BBOX) — `common.py:431` (`geo-metro-bbox`/`traffic-bialystok-bbox` P0).

**Zależność**: A jest warunkiem wstępnym dla B, C, D. Bez A nie da się fizycznie odpalić 2. procesu bez kolizji plików.

### FAZA B — „Tożsamości i identyfikatory per-miasto" (P0/P1 do poprawnego działania)
6. `city_center` (1 klucz, 5 call-site'ów — mapa kompletności: courier_resolver, chain_eta, bootstrap_restaurants, dispatch_pipeline, osrm_client) (`routing-bialystok-center-fallback`).
7. `koordynator_cid` (≠26, ≥5 plików: panel_client, czasowka_scheduler, auto_koord, courier_resolver, build_v319h) (`couriers-coordinator-virtual-cid-26`).
8. Telegram: bot token + grupa + authorized_users + coordinator_dm — de-duplikacja literałów (-5149910559 / 8765130486 / 8753482870) do JEDNEGO źródła w config (`infra-telegram-chat-ids`, `notif-*`).
9. `excluded_cids/owner/tech/company_id` + `spreadsheet_id` accounting per miasto (`couriers-excluded-cids`, `acct-excluded-cids-roster-sheet`).
10. systemd szablony `@<city>` (`infra-systemd-units`).

### FAZA C — „Paczka geo per-miasto" (P1, MOAT quality)
11. Wyekstrahuj `districts/adjacency/quadrant/outside_zones/aliases` ze stałych modułowych do `city/<slug>/geo/*.json`, ładowane przez obiekt **CityGeo** wstrzykiwany (wzorzec już istnieje w `same_restaurant_grouper`/`classify_trajectory`, które przyjmują resolver jako argument!). Napraw `drop_zone_from_address` żeby czytał `city.home_name` zamiast literału `'białystok'` (`geo-drop-zone-home-city-literal`).
12. `home_town_key` w `address_mismatch.py` + **bliźniak panelu** `app/api/dispatch.py:check_street_town` (zmieniać RAZEM — docstring ostrzega) (`geo-address-mismatch-home-town`).

### FAZA D — „Kalibracja i ML per-miasto" (P1/P2, dojrzewa z czasem)
13. `traffic_table/fallback_speeds/dist_bin_boost/road_factor` do per-city plików (rozszerz wzorzec SCALE-01 z flags.json, który celowo NIE objął tabel traffic) (`traffic-v326`, `business-rules`).
14. `models/<city_id>/...` layout + per-city `courier_tiers`, `eta_quantile`, `prep_bias`, `eta_residual`; timery kalibracji scope'owane per miasto (`tenancy-global-model-state-paths` P0, `calib-*`).
15. Retrain LGBM per miasto + (zalecane) feature engineering na city-relative.

### FAZA E — „Konsolidacja peak-windows i hartowanie" (P1 higiena + skala)
16. **Skonsoliduj PEAK_WINDOWS** — dziś ≥7 plików + 3 niespójne zestawy godzin (11-14/17-20, 11-15/15-19, 14-17). Najpierw JEDEN helper/źródło, potem per-miasto (`rules-peak-windows-hardcoded-scattered`, `business-rules` „worst tenancy smell"). Inaczej nowe miasto cicho dziedziczy stałe okno w pliku, który ktoś pominął.
17. Self-hosted Nominatim (przed skalą > 1 pilota — publiczny endpoint łamie ToS przy wielu miastach) (`routing-nominatim-shared-public-endpoint`).
18. Stempel `city_id` na eventach/logach dla przyszłego warehouse ML (`panel-order-id-keyspace-collision`).

**Minimum żeby ruszyć miasto #2 (najmniejszy cut)** = Faza A + Faza B. Wtedy miasto #2 STOI i dyspozytuje (silnik regułowy, geo w trybie geometrycznym). Faza C dokłada MOAT geo, Faza D dokłada MOAT ML. Faza E to higiena pod skalę 3+.

---

## 6. RYZYKA I PUŁAPKI

1. **Cross-tenant leak przez współdzielony stan** — jeśli ktoś odpali 2 miasta na tym samym `dispatch_state/` zanim Faza A skończona: `restaurant_coords.json` jednego miasta nadpisuje drugie, `geocode_cache` miesza rozkłady ulic (psuje detektor street↔town), `restaurant` cache key to **goły lowercase nazwy** (NIE city-qualified, `routing-geocode-cache-global-state`) → kolizja przy tej samej nazwie restauracji. **Mitygacja**: proces-per-miasto + osobny STATE_ROOT (kolizja fizycznie niemożliwa).

2. **Współdzielony `flags.json`/`config.json`** — jedna globalna flaga ON dotyka wszystkich miast; ID telegramowe Białystoku wmieszane w flagi. **Mitygacja**: per-city flags (hak `DISPATCH_FLAGS_PATH` już istnieje, trzeba tylko ustawić + wydzielić ID-ki do notify-config); rozdziel „uniwersalne toggle" (default w kodzie) od „per-city overlay".

3. **Single-instance OSRM** — jeden kontener z mapą podlaskie + coord-guard, który traktuje koordy spoza Białegostoku jako truciznę → miasto #2 NIEROUTOWALNE nawet gdyby miało mapę. **Mitygacja**: osobny kontener + ekstrakt per miasto + `OSRM_BASE` z env + `METRO_BBOX` z env. **Decyzja [NIEPEWNE]**: jeden kontener per miasto (czysta izolacja, więcej RAM) vs jeden scalony ekstrakt wielowojewódzki (taniej, ale bbox/center i tak per-miasto). Rekomendacja: **kontener per miasto** dla izolacji blast-radius, spójnie z proces-per-miasto.

4. **Single-instance panel** — jeden CookieJar/CSRF/session-cache; dwa panele w jednym procesie biją się o jeden lock. **Mitygacja**: proces-per-miasto + per-city `panel_session_cache` pod STATE_ROOT.

5. **PIN collision** (P2, ale realny przy współdzieleniu) — 4-cyfrowy PIN to login do apki kuriera; pula 9000 kurczy się i grozi logowaniem kuriera B do kontekstu A. **Mitygacja**: per-city `kurier_piny.json` (auth apki kierowany na backend miasta).

6. **Pułapki „mapy kompletności" (Przykazanie #0)** — kilka wartości jest **zduplikowanych w wielu plikach** i trzeba zmieniać RAZEM: `BIALYSTOK_CENTER` (5 kopii), `koordynator_cid=26` (≥5 plików), `haversine_road_factor` (6+ plików, w tym rozjazd 1.37 vs 1.3 w scoring.py), `home_town 'białystok'` (silnik + bliźniak panelu), peak-windows (≥7 plików). Zmiana częściowa = niezakończona.

7. **Fałszywe poczucie konfigurowalności** — `config.json` ma klucz `timezone`, który **nie jest konsumowany** (`infra-warsaw-tz-hardcoded-config-unused`); `ZIOMEK_DEFAULT_CITY` istnieje ale geo go ignoruje (`geo-default-city-env-already-config`); `DIST_DECAY_BY_CITY` to tylko komentarz. Nie ufaj „że jest env" — sprawdzaj czy faktycznie czytane.

8. **Locki i throughput** — przy multi-tenant-single-process (Opcja B) wszystkie miasta serializują się przez jeden lock per plik = zapaść wydajności. To dodatkowy argument za proces-per-miasto (locki izolują się same pod osobnym STATE_ROOT).

---

### TL;DR dla Adriana
Uruchom miasto #2 jako **osobny komplet usług (proces-per-miasto)** na **tym samym kodzie**, różnicowany per-city env + katalogiem stanu + własnym OSRM + własnym kontem panelu/botem telegram. **NIE buduj `tenant_id`** — to ogromny ryzykowny refaktor, a kod i tak czyta stałe przy imporcie, więc izolacja per-proces jest darmowa. Najmniejszy cut to **Faza A (jeden korzeń stanu/config + 5 env-haków) + Faza B (tożsamości per-miasto)** — wtedy miasto stoi i dyspozytuje silnikiem regułowym. MOAT (geo + ML) dochodzi w Fazie C/D: geo paczka cold-start (statyczna), kalibracja i modele **uczone z danych 14–60 dni shadow** zanim flip, wszystko przez Przykazanie #0.