# 5b — GPS-geofence DOSTAWY (fizyczna prawda doręczenia) — projekt (ETAP 0/1 done, build za ACK)

**Autor:** sesja 20, 2026-06-28 (~23:10 UTC) · **Kontekst:** #5 audytu (delivered=klik, 0/377 GPS). 5a=walidator (sesja 18, czyta `courier_ground_truth.gps_delivered_at`). **5b = PRODUCENT tej prawdy.**
**Status:** DESIGN. Cross-repo (apka Android `/root/courier-app` + backend courier-api). Build za ACK + koordynacja z sesją 18 (kontrakt ground_truth).

## ETAP 0/1 — ustalenia na żywo
- **Dlaczego backend-geofence NIE wystarczy:** `fleet_position_history` = **tylko 5,3% realnego `gps`** (reszta `last_picked_up_interp` 40%, `last_assigned_pickup` 34%, `pre_shift`/`last_delivered`/`no_gps`). Geofence na interpolacji = potwierdzanie własnego zgadywania Ziomka (circular). **Prawda fizyczna musi przyjść z APKI** (real device GPS).
- **Apka JUŻ geofence'uje — ale tylko ODBIÓR.** `AutoStatusEngine.kt` (`/root/courier-app`, git master, CZYSTE, brak kolizji): maszyna stanów sterowana GPS, status **3→4→5** (dojazd→geofence restauracji ENTER 150m/EXIT 230m, accuracy-gate 120m, per-zlecenie, OSRM, bramka czasu odbioru). **`OrderGeo` NIESIE JUŻ `delLat`/`delLon`** (współrzędne dostawy) — zero nowego plumbingu.
- **ROOT przyczyny „delivered=klik":** `AutoStatusEngine` **ŚWIADOMIE kończy na 5** — „Doręczenie (7) domyka kurier RĘCZNIE (suwak), auto nie odpala". Cała maszyneria geofence istnieje; **po prostu nie jest użyta do punktu DOSTAWY.**

## Projekt 5b (measurement-first, niskie ryzyko)
**Apka:** w `AutoStatusEngine` na każdym fixie GPS dolicz geofence punktu DOSTAWY (`delLat/delLon`, te same ENTER_RADIUS_M + accuracy-gate co odbiór) → zarejestruj **`gps_arrived_at`** (epoch pierwszego wjazdu w promień dostawy) per zlecenie. **NIE auto-odpalamy statusu 7** (zostaje ręczny suwak — „blisko budynku" ≠ „wręczone klientowi"; auto-7 byłoby przedwczesne). Wysyłka: `CourierApi` dodaje `gps_arrived_at` do raportu statusu (idempotentnie, jak istniejące syncStatuses).
**Backend (courier-api — JEDYNY writer `courier_ground_truth.json`):** odbiera `gps_arrived_at` → zapis do ground_truth (`status_store.write_ground_truth`). **⚠ KONTRAKT z 5a sesji 18** (`courier_ground_truth.gps_delivered_at` czyta `entry['delivered_at']` epoch) — **uzgodnić: nowe pole `gps_arrived_at` vs populacja `delivered_at`.** To jedyny touchpoint cross-sesja → koordynować z 18 PRZED zmianą courier-api.
**Konsumenci:** 5a (18, walidacja), oracle czasówki (mój — podmieni button-press `delivered_at` na `gps_arrived_at` gdy dostępne → kasuje 17,6pp szumu ±3min → 2% staje się mierzalne PEWNIE).

## Dlaczego measurement-only (nie auto-confirm-7)
- Geofence-arrival = przybycie pod adres, NIE potwierdzenie wręczenia → auto-7 fałszywie domykałby (piętra, recepcje, „zostaw pod drzwiami"). Ręczny 7 jest świadomy.
- Measurement-only daje CZYSTĄ prawdę fizyczną (cel #5) przy ZERO ryzyka UX/regresji statusów. Auto-confirm-7 = osobny, większy krok PÓŹNIEJ, jeśli dane pokażą że geofence-arrival jest wiarygodny.

## Pokrycie / ograniczenia
- Działa dla zleceń, gdzie apka ma realny GPS przy dostawie (ten sam podzbiór co działający dziś auto-odbiór). Częściowe, ale to JEDYNE realne źródło fizycznej prawdy (vs 5% backend). Pokrycie rośnie z niezawodnością GPS apki (osobny wątek: czemu tylko 5% realnego GPS dociera — apka uruchomiona? upload? bateria? — do zbadania, root #5).
- Precyzja: `delLat/delLon` geokodowane (audyt: „geocode-centroid fałszywe 0km") → geofence radius musi tolerować błąd geokodu; raportować accuracy.

## Mapa kompletności (cross-repo)
| Warstwa | Plik | Co |
|---|---|---|
| Apka — geofence dostawy | `AutoStatusEngine.kt` (~+ delivery branch, reuse ENTER_RADIUS/accuracy) | `gps_arrived_at` per order, BEZ auto-7 |
| Apka — wysyłka | `CourierApi.kt` (`StatusReportRequest`) | dolóż pole, idempotentnie |
| Apka — testy | `AutoStatusEngineTest.kt` (JVM, istnieje) | geofence dostawy ENTER/EXIT/accuracy/per-order |
| Backend — odbiór+zapis | courier-api `status_store.write_ground_truth` | zapis `gps_arrived_at` (**kontrakt z 18**) |
| Konsument | `tools/czasowka_uwagi_oracle.py` (mój) | prefer `gps_arrived_at` > button-press delivered_at |
| Release | APK soft (bez `--force`, wg `courier-app` procesu) | ETAP 6 za ACK |

## Brama (PRZED buildem)
1. **Koordynacja z sesją 18** — kontrakt ground_truth (nowe pole vs `delivered_at`); courier-api to obszar #5. NIE dotykać courier-api/ground_truth bez uzgodnienia (C1).
2. **ACK Adriana** na: (a) measurement-only vs auto-confirm-7, (b) cross-repo build+release apki.
3. Decyzja: czy przy okazji badać root „5% realnego GPS" (osobny, większy wątek).

## Rollback
Apka: rewert commita + soft release poprzedniego APK. Backend: pole additywne (czyta opcjonalnie) → brak = zachowanie dzisiejsze.
