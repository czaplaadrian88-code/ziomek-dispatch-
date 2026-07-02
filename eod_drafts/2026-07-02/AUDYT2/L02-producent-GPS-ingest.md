# AUDYT 2.0 — PAS L02: producent GPS / ingest pozycji kuriera

**Data:** 2026-07-02 (read-only, produkcja)
**Pas:** L02 — PAS 0.E — `gps_server.py` / `dispatch-gps` jako rzekomy PRODUCENT prawdy o pozycji kuriera (górny bieg rodziny K5 sentineli).
**Tryb:** READ-ONLY. Jedyny zapis = ten plik.
**Metoda:** lektura kodu + grep + pomiar na żywej bazie `courier_api.db` (SELECT-only, `mode=ro`), logi, systemctl/journalctl.

---

## TL;DR (co jest naprawdę)

1. **Audytowany „producent" `gps_server.py` (PWA, port 8766, `dispatch-gps`) jest MARTWY jako źródło pozycji.** Zero POST-ów `/gps` w logu; serwuje tylko stronę HTML + skany botów. `gps_positions.json` (legacy Traccar :8765) = `{}`.
2. **PRAWDZIWY producent = `courier_api` `/api/gps/batch` (port 8767, apka Android).** Żywy wpis w `gps_positions_pwa.json` ma `"source":"android"`; 100% z 170 412 wierszy `gps_history` to `source=android`.
3. **Świeży fix L2.1 (`ENABLE_COORD_SENTINEL_INGEST_GUARD`, flip 01.07 21:29) wpięto w 8 miejsc — WSZYSTKIE w silniku `dispatch_v2` (martwy PWA + read-side konsumentów), ZERO w `courier_api`/`courier_api_panelsync`.** Guard trafił w martwe źródło i w read-side, ale **NIE w żywy producent u ZAPISU.** (Odpowiedź na pytanie zadania: pokrywa `gps_server` POST + read-side, **nie** pokrywa żywej ścieżki producenta.)
4. **Ale feed jest empirycznie CZYSTY:** w 170 412 wierszach `gps_history` — **0 razy (0,0), 0 near-zero, 0 poza-bbox, 0 NaN/inf, 0 ts z przyszłości/starożytności.** Apka Android nie emituje sentineli. Luka na producencie = **LATENTNA (0/dzień dziś)**, nie pożar.
5. **Rodzina K5 (0,0)/BIALYSTOK_CENTER NIE pochodzi z feedu GPS.** Wszystkie realne trafienia guardów są na `pickup_coords`/`delivery_coords` (geokod restauracji → (0,0)/NaN/Warszawa) i `last_drop_coords` (plan-placeholder). GPS jest tu ofiarą narracji, nie źródłem. Ground-truth: read-side guard GPS (`gps-load`) nie odpalił ani razu.

**Materialność nagłówka:** producent bez bbox-guarda = **0 sentineli/dzień** (proxy: 170k wierszy historii, ground-truth bazy). Realne ofiary (0,0) 8/dzień (30.06) i 28/dzień (01.07) — źródło = geokod pickup + plan-placeholder, **nie GPS** (dowód: `l21_flip_review.py` docstring + treść trafień guardów).

---

## 1. Kto jest producentem — mapa ścieżek

| Ścieżka | Proces / port | Stan | Pisze do | Bbox-guard u zapisu? |
|---|---|---|---|---|
| PWA `gps_server.py` `POST /gps` | `dispatch-gps` :8766 | **MARTWY** (0 POST/d) | `gps_positions_pwa.json` (`source=pwa`) | **TAK** (L2.1, `gps_server.py:343`) — ale ruch ~0 |
| Apka Android `POST /api/gps/batch` | `courier-api` :8767 | **ŻYWY** (2242 pkt/d) | `gps_positions_pwa.json` (`source=android`) + SQLite `gps_history` | **NIE** |
| Twin `courier_api_panelsync` | `courier-panel-sync` (timer, oneshot) | **UŚPIONY** (inactive) | ten sam kod, te same pliki | **NIE** |
| Legacy Traccar `/root/gps_server.py` | `@reboot` :8765 | żywy proces, **0 danych** | `gps_positions.json` (key=imię) = `{}` | **NIE** (skrypt spoza repo) |
| GC nocny | cron 04:50 `--apply` | żywy | prune obu plików (TTL 24h) | n/d |

**Dowody:**
- `gps_server.py:40` `PORT = 8766`; `systemctl` `dispatch-gps` active, ale `logs/gps_server.log` = 0 linii `GPS ` (tylko `GET /` + skany `wp-content`/`favicon`). Wpięcie L2.1: `gps_server.py:105-114` (`_ingest_coords_ok`) + `:343` (do_POST). Chroni ~0% ruchu.
- Żywy wpis: `gps_positions_pwa.json` → `{"492": {..., "source":"android","name":"Jakub W"}}`.
- `courier_api/main.py:299` `@app.post("/api/gps/batch")` → `:330` `insert_history_batch` + `:339` `update_pwa_position`. `courier_api/config.py:32` `GPS_SOURCE = "android"`.
- Twin: `courier_api_panelsync/main.py:3` opisuje `/api/gps/batch`; `courier_api_panelsync/config.py:6-24` te same ścieżki+source; `systemctl is-active courier-panel-sync` = **inactive** (static, TriggeredBy timer — reflektor statusów, nie odbiornik GPS).
- Legacy: `crontab` `@reboot nohup python3 /root/gps_server.py`; `gps_positions.json` = `{}`.

---

## 2. Walidacja/sanityzacja coords na WEJŚCIU żywego producenta

**Jedyna walidacja = Pydantic `GpsPoint` (`courier_api/main.py:85-94`):**
```
lat: float = Field(..., ge=-90, le=90)
lon: float = Field(..., ge=-180, le=180, alias="lng")
ts:  int | str
```
- Range check **całej Ziemi**, brak bbox metropolii Białystok.
- Endpoint `gps_batch` (`main.py:314-346`): waliduje **tylko `ts`** (`_parse_ts_to_epoch`, `:317` — zły ts → skip). `lat`/`lon` przechodzą 1:1 (`:321-322`) → `update_pwa_position`/`insert_history_batch`.
- `gps_writer.update_pwa_position` (`gps_writer.py:57-73`) i `insert_history_batch` (`:78-118`): zero range/bbox/(0,0) check — round + write.

**Test empiryczny akceptacji (venv courier_api, ograniczenia 1:1 z `GpsPoint`):**
| Wejście | Wynik |
|---|---|
| **(0,0)** | **ACCEPTED** (przechodzi Pydantic → wchodzi do store'a) |
| NaN | REJECTED (`ge/le` odrzuca, bo `nan>=-90` = False) |
| inf | REJECTED |
| out-of-bbox (Warszawa 52.23,21.01) | **ACCEPTED** (poprawny punkt Ziemi) |
| Białystok 53.13,23.16 | ACCEPTED |

**Wniosek:** na żywym producencie **(0,0) i dowolny poprawny punkt spoza metropolii (np. Warszawa) wchodzą do `gps_positions_pwa.json` i `gps_history` bez filtra.** NaN/inf blokuje Pydantic. `coords_in_bialystok_bbox` (`common.py:522`, bbox LAT 52.6-53.7 / LON 22.3-24.1) **nie jest wpięty w tę ścieżkę** (grep w `courier_api/*.py` = 0 trafień).

---

## 3. Czy to się dzieje? (materialność — pomiar bazy)

Pomiar `gps_history` (SQLite `courier_api.db`, `mode=ro`, 170 412 wierszy, 2025-04-17 → 2026-07-01):

| Metryka | Wartość | Interpretacja |
|---|---|---|
| exact (0,0) | **0** | apka nie wysyła sentinela zerowego |
| near-(0,0) (\|lat\|,\|lon\|<0.01) | **0** | — |
| poza-bbox (nie 52.6-53.7 / 22.3-24.1) | **0** | żaden fix nie wyszedł poza metropolię |
| ts przyszłość(>now+1h)/starożytność(<2024) | **0 / 0** | zegar klienta sanity-OK |
| `source` | 100% `android` | jedyny żywy producent |
| pkt/dzień (7 dni) | ~2242 | wolumen feedu |
| aktywni kurierzy/dzień (7 dni) | 4 | mała flota testowa (GPS jeszcze nie norma) |
| accuracy avg / max | 17,1 m / 2865 m | feed generalnie dokładny |
| accuracy >500 m | 257 (0,15%) | rzadki śmieć, ale **wpisywany i używany** (brak floor u ingest) |

**Guardy L2.1 od flipu (logi):** 47 trafień `COORD_INGEST_GUARD` — **wszystkie to SYNTETYCZNE PROBY testowe** (`l21_flip_review.py:32` `_TEST_MARKERS = ("L21A","L21B","L21C","L21D","cid=C1","cid=C515","oid=N1")` — dokładnie te markery: `cid=C1`, `cid=C515`, `tick X1`, `L21A/B`). **Żaden realny sentinel GPS nie został odfiltrowany.** Treść realnych/probowych trafień dotyczy `pickup_coords`/`delivery_coords`/`last_drop_coords` — **nigdy pozycji GPS kuriera** (`gps-load` read-guard nie odpalił).

**Realna materialność rodziny (0,0)** (z `l21_flip_review.py` docstring, dowód niezależny): 30.06 = **8 ofiar**, 01.07 = **28 ofiar** (432 zdarzenia V328 sentinel-eject). **Źródło = geokod pickup + plan-placeholder, nie feed GPS.** To domena innych pasów (L2.1 pickup/plan), nie producenta GPS.

---

## 4. TTL / świeżość / dual-write

- **Producent nie stempluje świeżości ani TTL** — po prostu przestaje pisać, gdy apka milknie. To poprawne: świeżość liczy konsument.
- Konsument: `courier_resolver.py:79` `GPS_FRESHNESS_MIN = 5` (fix >5 min = nieaktualny) + `:126` `LAST_KNOWN_POS_TTL_MIN = 25.0` (store `courier_last_pos.json`, wpis >25 min → ignoruj → no_gps). To jest ten „TTL 25 min" z zadania — **istnieje i działa** (`:213` clamp `age >= TTL`).
- **GC:** `tools/gps_positions_gc.py` cron 04:50 `--apply`, TTL 24h, atomic, fail-safe (nieparsowalny ts zostaje). Docstring (`:6-7`) potwierdza: „courier_resolver liczy świeżość przy użyciu, GPS_FRESHNESS_MIN=5". Stąd mały `pwa.json` (stare wpisy prune'owane nocą).
- **Dual-write nie jest transakcyjny:** `gps_history` (SQLite) + `gps_positions_pwa.json` (JSON) pisane osobno (`main.py:330` i `:339`). Crash między nimi → rozjazd (drobny; kolejny batch nadpisuje). Zapis JSON = **read-modify-write CAŁEGO pliku** pod `threading.Lock` (per-proces) — cross-proces (courier_api / gps_server / GC) tylko atomic-rename tmp, **nie chroni przed lost-update** przy jednoczesnym RMW. W praktyce w dzień pisze tylko courier_api (PWA martwy, GC o 04:50) → realny wyścig ~0. GC świadomie wybiera 04:50 (komentarz `:11-13`).

---

## 5. Odporność na awarie (producent) — monitoring

- **`monitoring/gps_feed_health.py` istnieje, ale jest DOMYŚLNIE WYŁĄCZONY i NIESZCHEDULOWANY.** Docstring: detektor „cała flota traci świeży GPS (:8766/:8767 down w peaku) → cicha degradacja scoringu, BEZ sygnału do człowieka"; `GpsFeedAlertConfig.enabled=False` → flaga `GPS_FEED_ALERT_ENABLED=false` → caller short-circuit (zero logu). Brak timera w `/etc/systemd/system` i w `dispatch_v2/systemd`.
- To **świadomy park** (audyt 06.03: „brak GPS to CELOWY stan testowy… GPS będzie normą dopiero przy autonomicznym starcie"). Nie bug, ale **realna dziura na moment przełączenia autonomii**: jeśli courier-api padnie w peaku, cała flota schodzi na pozycje zastępcze (BIALYSTOK_CENTER/last-pos) **bez alertu**.
- `courier-api`: `NRestarts=0`, up 15h — feed stabilny.

---

## 6. Ustalenia (severity)

### P2 — L2.1 ingest-guard wpięty w MARTWY producent, nie w żywy (niekompletność zmiany + latentna mina)
- **Gdzie:** guard w `gps_server.py:343` (PWA, 0 POST/d) + read-side (`courier_resolver.py:537`, `state_machine.py:435`, `feasibility_v2.py:116`, `dispatch_pipeline.py:239/2397`, `shadow_dispatcher.py:1091`, `panel_watcher.py:486`). **Brak w `courier_api/main.py:315` / `gps_writer.py:57` / `insert_history_batch` i w twinie `courier_api_panelsync`.**
- **Dlaczego to niekompletność:** Przykazanie #0 wymaga „bliźniacze ścieżki RAZEM" i „1 walidator coords u ingest". Żywy producent i jego twin nie dostały walidatora u zapisu; zasada single-source-of-validation złamana — każdy nowy konsument surowego store'a dziedziczy ryzyko od nowa. Komentarz `courier_resolver.py:535` sam przyznaje, że „ingest gps_server łapie tylko nowe POSTy" — autor wiedział, że PWA to wąska ścieżka, i podparł się read-side; ale **żywego producenta nie objął**.
- **Materialność:** 0 sentineli/dzień DZIŚ (170k wierszy, ground-truth). Mina na skali/autonomii: przy większej flocie poprawny-ale-poza-bbox punkt (np. kurier w Warszawie/ekspansja, glitch fused-provider) przechodzi Pydantic i wchodzi do `gps_history`.
- **Mitygacja istniejąca:** read-side guard `courier_resolver.py:537` filtruje `pwa.json` po odczycie (flaga ON) — **główna ścieżka konsumpcji przez silnik jest kryta.** Dlatego P2, nie P1.
- **Rekomendacja:** wpiąć `coords_in_bialystok_bbox` u ŹRÓDŁA w `courier_api/gps_writer.py` (odrzucaj punkt przed `update_pwa_position`/`insert_history_batch`, licz jako `duplicate`/`rejected`) + ten sam guard w twinie `courier_api_panelsync/gps_writer.py` (RAZEM). Protokół Ziomka (backup→py_compile→test→ACK→restart courier-api off-peak). Uwaga: to inny venv (`courier_api/.venv`) i inny import niż `dispatch_v2.common` — trzeba lokalnej kopii predykatu lub wspólnego minimalnego modułu.

### P2 — `gps_history` (SQLite) pisany bez guarda, a czytany poza read-side silnika
- **Gdzie:** `fleet_aggregator.py:183-196` (`SELECT lat,lon … FROM gps_history`, potem `haversine_km` na surowych coords, `:234`) + `gps-delivery-validation` czytają `gps_history` **bez** przejścia przez `courier_resolver._load_gps_positions` guard.
- **Dlaczego istotne:** to jedyna realna ścieżka, którą sentinel producenta (gdyby wystąpił) ominąłby read-side guard i zatruł panel-mapę floty / walidację dostaw.
- **Materialność:** 0/dzień (0 sentineli w historii). Latentne.
- **Rekomendacja:** ten sam ingest-guard na `insert_history_batch` rozwiązuje u źródła (P2 wyżej) — wtedy `gps_history` z definicji czyste. Alternatywnie: filtr w `fetch_gps_points`.

### P3 — brak floor accuracy u ingest (śmieć >500 m wpisywany i używany)
- **Gdzie:** brak progu accuracy w `gps_batch`/`gps_writer`; `gps_quality.py` (accuracy>150m, teleport) jest **SHADOW-only** (`gps_quality_shadow.jsonl` → `filter_active:false`).
- **Materialność:** 257/170 412 = 0,15% fixów >500 m (max 2865 m) trafia do store'a jako „pozycja". Konsument nie ma twardego odrzutu — degraduje kierunek/po-drodze/ETA.
- **Rekomendacja:** rozważyć flip `gps_quality` z shadow na enforce (osobny pas/temat) — nie mieszać z bbox-guardem.

### P3 — twin `courier_api_panelsync` = uśpiona kopia bez guarda
- **Gdzie:** `courier_api_panelsync/{main.py,gps_writer.py,config.py}` — 1:1 endpoint `/api/gps/batch` + `update_pwa_position`, ten sam `gps_positions_pwa.json`/`gps_history`, bez bbox-guarda. Serwis `inactive` (timer/oneshot, reflektor statusów).
- **Materialność:** 0 dziś (nie serwuje GPS). Mina, jeśli kiedyś aktywowany jako drugi odbiornik.
- **Rekomendacja:** objąć tym samym fixem RAZEM z `courier_api` (parytet bliźniaków), albo świadomie usunąć martwy endpoint GPS z panelsync.

### P2/INFO — `gps_feed_health` wyłączony i nieszschedulowany (brak alertu „feed padł")
- **Gdzie:** `monitoring/gps_feed_health.py` (`enabled=False`, `GPS_FEED_ALERT_ENABLED=false`, brak timera).
- **Materialność:** nie policzone (tylko istnienie stanu). Świadomy park do czasu autonomii, ale przy autonomicznym starcie = cicha degradacja bez sygnału, gdy courier-api/feed padnie w peaku.
- **Rekomendacja:** wpisać na listę pre-autonomia „go-live": włączyć detektor + timer + kalibracja denominatora (active_ids), zanim GPS stanie się normą. Nie ruszać teraz.

### INFO — dual-write nietransakcyjny + cross-proces RMW `pwa.json`
- **Gdzie:** `main.py:330` vs `:339`; `gps_writer._atomic_write_json` (per-proces `threading.Lock`).
- **Materialność:** realny wyścig ~0 (jeden dzienny writer). Rekomendacja: brak akcji poza świadomością; gdyby PWA kiedyś ożył jako drugi żywy writer — potrzebny cross-proces lock na `pwa.json`.

### INFO — legacy `/root/gps_server.py` (:8765, Traccar) `@reboot`, poza repo, bez guarda
- **Gdzie:** `crontab @reboot`; pisze `gps_positions.json` (key=imię), obecnie `{}`.
- **Materialność:** 0 danych. Kryty na read (merge w `courier_resolver` przechodzi przez guard `:537`). Rekomendacja: brak; odnotować, że to 4. potencjalny producent poza `dispatch_v2`.

---

## 7. Uczciwe rozgraniczenie dowód vs hipoteza
- **Ground-truth (baza):** 0 sentineli/(0,0)/poza-bbox/NaN/future-ts w 170 412 wierszach `gps_history`. Feed producenta jest czysty. To pomiar, nie deklaracja.
- **Ground-truth (kod+test):** (0,0) i Warszawa przechodzą `GpsPoint` (test w venv); guard L2.1 nie wpięty w `courier_api` (grep=0).
- **Ground-truth (logi):** 47 trafień guarda = syntetyczne proby (markery z `l21_flip_review.py`); realny GPS `gps-load` guard nie odpalił.
- **Hipoteza z lektury:** ryzyko poza-bbox przy skali/ekspansji (kurier w Warszawie) — poprawny punkt Ziemi przechodzi Pydantic; niepotwierdzone empirycznie (0 dziś), ale mechanicznie możliwe.
- **Korekta premisy zadania:** teza „gps_server = górny bieg rodziny K5 (0,0)" **nie potwierdzona** — feed GPS nie niesie (0,0); rodzina (0,0) pochodzi z geokodu pickup/delivery i plan-placeholderów (inne pasy). Producent GPS jest w tej sprawie czysty.
