# 5b — GPS-geofence DOSTAWY: sprint build 2026-07-05 (protokół #0)

**GO Adriana:** 04.07 („leć z 5b geofence"). Design bazowy: `eod_drafts/2026-06-28/GPS5b_DELIVERY_GEOFENCE_DESIGN.md` (`09d46f9`).
**Cel:** fizyczna prawda PRZYJAZDU pod adres dostawy (`gps_arrived_at`) z realnego GPS apki — measurement-only, BEZ auto-statusu 7 (suwak zostaje ręczny). Odblokowuje: kalibrację oracle czasówek (kasuje ±3 min szumu button-press), przyszły dowód dla flipu O2 i realny pomiar feas_carry.

## ETAP 0 — stan na żywo (05.07 ~16:10 UTC)
- **courier-api** (`scripts/courier_api`, master `d03be13`, serwis `courier-api.service` :8767 active): `status_store.record_status` → `write_ground_truth` (flock+atomic, earliest-wins `picked_up_at`/`delivered_at`). `delivered_at` = pierwszy raport statusu 7 = RĘCZNY suwak.
- **Kontrakt 5a:** `dispatch_v2/courier_ground_truth.py::gps_delivered_at()` czyta `entry['delivered_at']`. **DECYZJA KONTRAKTOWA: NOWE pole `gps_arrived_at` obok — `delivered_at` NIETKNIĘTE** (semantyka „wręczone" ≠ „przybył pod adres"; konsumenci 5a bez zmian; additive = rollback trywialny).
- **Bliźniak panelsync:** `courier_api_panelsync/status_store.py` ma kopię `write_ground_truth`, ALE żywy serwis `courier-panel-sync` odpala `panel_sync.py` (nie importuje status_store) → kopia MARTWA dla ground_truth. Jedyny żywy writer = courier-api. N-D z powodem (nie dotykamy martwej kopii; nie wolno jej ożywić bez tego designu).
- **Walidator schematu:** `dispatch_v2/tools/validate_state_schema.py` waliduje ground_truth kształtem `dict_of_entries` + required_keys — nowe OPCJONALNE pole nie łamie (waliduje wyłącznie obecność wymaganych, bez allowlisty).
- **Apka** (`/root/courier-app`, master po merge PR #8 `dcea96e`, vc59/0.9.45 live soft): `AutoStatusEngine` kończy świadomie na 5; `OrderGeo` ma już `delLat/delLon`; wzorce do reuse: ENTER_RADIUS_M=150 / EXIT_RADIUS_M=230 (histereza) / MAX_ACCURACY_M=120 / ARRIVAL_DWELL_MS=30s (mapa `arrivedInsideAtMs`). `RouteStore` = DataStore (trwałe mapy localStatus/confirmedStatus + wzorzec bump/markConfirmed).
- **MULTI-SESJA (C1):** sesja 16 tmux = „Audyt i modernizacja apki" (DEBT-3, PR #8 zmergowany; następny obszar GpsUploader/PlanPoller = LocationService). Sesja 11 = Ziomek audyty (L6.C scalony `d8328b2`). `courier_api/main.py` DIRTY cudzym WIP (redakcja 422). ⇒ **build w worktree na masterze obu repo, commit na gałęziach feature, merge+restart+release SKOORDYNOWANE** (nie zgarniać cudzego WIP; release APK nie może wyprzedzić werdyktu vc59 — już zmergowany, ale kolejny release koordynować z 16).

## Projekt (measurement-only)
**Apka (feat/5b-delivery-geofence):**
1. `AutoStatusEngine`: nowa mapa `deliveryInsideAtMs` (wzorzec `arrivedInsideAtMs`). Dla zleceń `code in 5..6` (jedzenie w aucie; ≥7 stop): dobry fix (≤120 m acc) w promieniu ENTER 150 m adresu dostawy → `putIfAbsent(entry_ms)`; wyjazd poza EXIT 230 m przed dwellами → remove (przejazd obok NIE liczy się). Po dwell ≥30 s → **`gps_arrived_at = entry_ms/1000` earliest-wins** do RouteStore. Czysta funkcja `detectDeliveryArrivals(...)` (testowalna JVM, zero Androida) + bookkeeping w `evaluate()`.
2. `RouteStore`: trwałe mapy `gpsArrivedAt: Map<String, Long>` (epoch s) i `gpsArrivedReported: Map<String, Long>`; `recordGpsArrival(oid, epochSec): Boolean` (earliest-wins) + `markGpsArrivalReported(oid)`.
3. `CourierApi`: `ArrivalReportRequest(ts, lat, lon, accuracy)` + `POST api/courier/orders/{orderId}/arrival`.
4. `syncStatuses` (ten sam cykl retry): dla `gpsArrivedAt` bez `reported` → reportArrival; sukces → mark. Idempotentne (backend earliest-wins), retry przy braku sieci.
5. Testy JVM `AutoStatusEngineTest`: enter+dwell → arrival; drive-by (exit<30 s) → brak; słaby fix → brak; code<5 / ≥7 → brak; earliest-wins.

**Backend (feat/5b-gps-arrival):**
1. `config.py`: `ENABLE_GPS_ARRIVAL_INGEST` env, default ON (kill-switch, wzorzec ENABLE_DELIVERED_TOO_FAST_GUARD).
2. `status_store.py`: `write_gps_arrival(order_id, courier_id, arrived_at, received_at, accuracy)` — TEN SAM flock/atomic wzorzec; wpis: `gps_arrived_at` (earliest-wins), `gps_arrived_accuracy_m` (przy pierwszym zapisie), `gps_arrival_source="app_geofence"`. NIE dotyka `last_status_*`/`picked_up_at`/`delivered_at`. Zwraca bool (nowy zapis?).
3. `routes/arrival.py`: router `POST /api/courier/orders/{order_id}/arrival` (sesja wymagana jak status; kill-switch → 200 `{ok:true, ingest:false}`; log `[arrival]`). `main.py`: +2 linie include (wzorzec admin/fleet).
4. Testy `tests/test_gps_arrival.py` (GROUND_TRUTH_PATH → tmp): earliest-wins; nie nadpisuje delivered_at/statusów; kill-switch OFF nie pisze; accuracy zapisana raz.

**Konsument (dispatch_v2, master):**
1. `courier_ground_truth.py`: `gps_arrived_at(gt, oid)` + schema w docstringu.
2. `tools/czasowka_uwagi_oracle.py`: prawda dostawy = `gps_arrived_at` z ground_truth GDY jest, inaczej `delivered_at` (button-press) — z licznikiem pokrycia w output (obserwowalność wzrostu pokrycia po rollout).

## Mapa kompletności (tabela miejsce→dotknięte)
| Miejsce | Dotknięte? |
|---|---|
| apka AutoStatusEngine + RouteStore + CourierApi + sync | TAK (1-4 wyżej) |
| apka testy JVM | TAK |
| backend status_store + routes + config + testy | TAK |
| backend bliźniak panelsync/status_store | **N-D** — martwa kopia (serwis nie importuje); adnotacja w tym designie |
| dispatch_v2 reader + oracle | TAK |
| walidator schematu state | N-D — pole opcjonalne, kształt bez zmian (zweryfikowane) |
| serializer/decyzje silnika | N-D — measurement-only, ZERO wpływu na decyzje (żadna flaga decyzyjna) |
| systemd/unit | N-D — istniejący courier-api.service; restart przy merge |
| release APK | TAK — soft (bez --force), PO koordynacji z sesją 16 |

## Dowody (DoD)
- pytest courier_api: nowe testy + pełna suita = baseline (0 nowych failów).
- JVM testy apki: nowe + istniejące AutoStatusEngineTest zielone.
- E2E po deployu: realny wpis `gps_arrived_at` w courier_ground_truth.json (pokrycie = podzbiór z realnym GPS — jak auto-odbiór dziś).
- „Pozytywny wpływ" = refaktor-additive measurement: metryka docelowa = ISTNIENIE fizycznej prawdy przybycia (dziś 0/377). Werdykt pokrycia po ≥2 dniach danych.

## Rollback
- Backend: `ENABLE_GPS_ARRIVAL_INGEST=0` (env, restart) / revert commita — pole additive, konsumenci fail-soft.
- Apka: soft release poprzedniego APK / revert; brak wysyłki = zachowanie dzisiejsze.
- dispatch_v2: revert (reader/oracle additive, fail-soft na brak pola).
