# ZIOMEK V3.6 — Pełna dokumentacja techniczna (15.04.2026)

## 1. KIM JESTEŚMY

**Adrian Czapla**, owner NadajeSz (Białystok, ekspansja Warszawa).
- 30 aktywnych kurierów, 55 restauracji
- ~1500-2000 orderów/tydz
- Revenue transport ~35-45k PLN/tydz + GMV cash 70-90k PLN/tydz

**Ziomek** = autonomiczny AI dispatcher zastępujący ręczną pracę koordynatora.

---

## 2. ARCHITEKTURA SYSTEMU

### 2.1 Serwis live (6 serwisów systemd)

```
dispatch-panel-watcher   # scraping panelu Rutcom co 20s
dispatch-sla-tracker     # monitoring SLA dostaw
dispatch-shadow          # shadow dispatcher → shadow_decisions.jsonl
dispatch-telegram        # bot @NadajeszBot, propozycje TAK/NIE/INNY/KOORD
dispatch-gps             # GPS PWA server (port 8766)
nginx                    # reverse proxy 443 → 8766
```

Health check: `systemctl is-active dispatch-panel-watcher dispatch-sla-tracker dispatch-shadow dispatch-telegram dispatch-gps nginx`

### 2.2 Infrastruktura

- **Serwer**: Hetzner CPX22, 178.104.104.138, Ubuntu 24.04, UTC
- **Repo**: github.com/czaplaadrian88-code/ziomek-dispatch-
- **Cron hourly**: git push (backup)
- **Panel**: gastro.nadajesz.pl (Laravel, CSRF)
- **GPS PWA**: https://gps.nadajesz.pl (Let's Encrypt, nginx, PIN auth)
- **Routing**: Google Maps Distance Matrix API
- **Geocoding**: Nominatim/OpenStreetMap
- **Grafik**: Google Sheets ID `1Z5kSGUB0Tfl1TiUs5ho-ecMYJVz0-VuUctoq781OSK8`

### 2.3 Telegram

- **@NadajeszBot** — bot dyspozytorski (propozycje, TAK/NIE)
- **Grupa ziomka** — Ty + Bartek Ołdziej + @NadajeszBot
- chat_id grupy: `-5149910559`
- personal_admin_id: `8765130486`
- Privacy mode: **DISABLED** (bot widzi wszystkie wiadomości w grupie)

---

## 3. PRZEPŁYW DYSPOZYTORSKI (KROK PO KROKU)

```
Panel Rutcom
    ↓ (scraping co 20s)
panel_watcher.py
    ↓ (emit NEW_ORDER event)
events.db (SQLite event bus)
    ↓ (poll co 5s)
shadow_dispatcher.py
    ↓ (assess_order)
dispatch_pipeline.py
    ├── dispatchable_fleet() → filtruje kurierów (grafik, pos, shift_end)
    ├── scoring per kandydat (dystans, obciążenie, kierunek, czas)
    ├── bundling L1/L2/L3 (same-rest, nearby, corridor)
    ├── availability bonus (bag=0/15min/30min)
    └── feasibility_v2 (SLA, R1/R3/R5 Bartek rules)
    ↓ (PROPOSE/SKIP/KOORD)
shadow_decisions.jsonl
    ↓ (tail offset)
telegram_approver.py
    ↓ (sendMessage do grupy)
Grupa ziomka (Telegram)
    ↓ (klik TAK)
gastro_assign.py --id X --kurier Y --time Z
    ↓ (POST przypisz-zamowienie)
Panel Rutcom → kurier przypisany, restauracja dostaje czas
```

---

## 4. POLA CZASOWE PANELU (KRYTYCZNE)

### 4.1 Trzy czasy zlecenia w UI panelu

```
Złożenie (niebieski):  created_at = UTC → np. 13:29 Warsaw
Restauracja (czerwony): czas_odbioru_timestamp = najwcześniej kurier może przyjść
Kurier (zadeklarowany): czas_kuriera = co Ziomek wysłał do restauracji
```

### 4.2 Pola API

| Pole | Format | Znaczenie |
|------|--------|-----------|
| `created_at` | UTC, suffix Z | Kiedy złożono zlecenie |
| `czas_odbioru` | int (minuty) | Czas przygotowania |
| `czas_odbioru_timestamp` | Warsaw TZ | Najwcześniej kurier może przyjść = created_at + czas_odbioru |
| `czas_kuriera` | HH:MM | Zadeklarowany przez Ziomka czas przyjazdu |
| `zmiana_czasu_odbioru` | int 0/1 | Czy restauracja zmieniła czas |
| `zmiana_czasu_odbioru_kurier` | int 0/1 | Czy kurier zmienił czas |

### 4.3 Zasady (NIGDY nie łam)

- `pickup_ready_at = czas_odbioru_timestamp` — **BEZ żadnych buforów**
- NIE dodawaj `prep_variance` do `pickup_ready_at` (wyłączone w F1.8g)
- `created_at` jest UTC — konwertuj do Warsaw przed użyciem
- `czas_odbioru_timestamp` jest już w Warsaw

### 4.4 Czas deklarowany (time parameter)

Panel dropdown: **5/10/15/20/25/30/35/40/45/50/55/60 min od teraz** (tylko te wartości).

```python
time_param = ceil(max(travel_min, prep_remaining) / 5) * 5
prep_remaining = max(0, (pickup_ready_at - now).total_seconds() / 60)
time_param = max(5, min(60, time_param))
```

**target_pickup_at** zapisywany w decision_record — przy kliknięciu TAK przeliczamy świeżo:
```python
time_param = ceil((target_pickup_at - now_at_click) / 60 / 5) * 5
```

---

## 5. ORDER TYPES

| Typ | Warunek | Zachowanie |
|-----|---------|------------|
| Elastyk | `czas_odbioru < 60` | Ziomek proponuje kuriera |
| Czasówka | `czas_odbioru >= 60` | Auto do Koordynatora (id_kurier=26) |

### Status mapping
```
2 = nowe/nieprzypisane
3 = dojazd
4 = oczekiwanie pod restauracją
5 = odebrane (picked_up)
6 = opóźnienie
7 = doręczone
8 = nieodebrano (cancelled by courier)
9 = anulowane
```
Watcher ignoruje statusy: 7, 8, 9.

---

## 6. POZYCJA KURIERA — HIERARCHIA

**NIGDY nie używaj pozycji bez sprawdzenia timestamp.**
**NIGDY nie używaj pozycji starszej niż 60 min jako realnej pozycji.**

```
1. gps_fresh          GPS PWA <5 min → najdokładniejsze
2. last_picked_up_delivery  kurier ma picked_up → jedzie do delivery_coords
3. last_assigned_pickup     kurier ma assigned → jedzie do pickup_coords
4. last_delivered     ostatnie doręczenie <30 min temu → blisko delivery_coords
5. last_activity      aktywność 30-60 min temu → estymata z delivery_coords
6. no_gps             brak aktywności >60 min → synthetic BIALYSTOK_CENTER (53.1325, 23.1688)
7. pre_shift          zaczyna zmianę za ≤50 min → synthetic centrum + shift_start_min
```

### ETA w propozycji

- ETA = `now + drive_min` (haversine per kandydat)
- **Każdy kandydat ma RÓŻNE ETA** — identyczne ETA = BUG
- `drive_min` = haversine display (dla wyświetlania)
- `travel_min` = plan-based z route_simulator (dla compute_assign_time)
- Dla kuriera z bagiem: `travel_min` uwzględnia że musi najpierw dostarczyć obecne zlecenia

---

## 7. BUNDLING — ZASADY OPERACYJNE

### 7.1 Trzy poziomy

| Poziom | Warunek | Bonus | Tag |
|--------|---------|-------|-----|
| L1 same-restaurant | ta sama restauracja w bagu (assigned only) | +25 | 🔗 same: Nazwa |
| L2 nearby pickup | restauracja nowego <1.5 km od restauracji w bagu (assigned only) | max +20 | 🔗 blisko: Nazwa (Xkm) |
| L3 corridor delivery | delivery w korytarzu 2.0 km od trasy kuriera | max +15 | 🔗 po drodze (Xkm) |

**KRYTYCZNE: L1/L2 tylko dla bag ze statusem `assigned`.**
Kurier z `picked_up` już jedzie z jedzeniem — NIE wraca do restauracji.

### 7.2 Availability bonus

```
🟢 bag=0 → +10 (wolny)
🟡 kończy za <15 min → +8
🟠 kończy za 15-30 min → +5
brak tagu → +0 (zajęty >30 min)
```

### 7.3 SLA

- Solo zlecenie: **35 min** od pickup
- Bundle: **45 min** od pickup (dane Bartka: 95% mieści się w 45 min)

### 7.4 Route simulator (D19)

- bag ≤ 3 → brute-force PDP-TSP (wszystkie permutacje)
- bag ≥ 4 → greedy insertion O(N²)
- **Lock first stop**: kurier z `picked_up` → najpierw dostarcza obecne zlecenia, potem nowy pickup. NIE zawraca z jedzeniem w torbie.

---

## 8. BARTEK GOLD STANDARD (F1.9)

Źródło: analiza 231 zleceń Bartka O. z 5 dni + 1280 zleceń z całej floty.

### 8.1 Parametry empiryczne

| Reguła | Parametr | Wartość | Źródło |
|--------|----------|---------|--------|
| R1 | Max spread delivery w bundlu | **8.0 km** (hard block) | p90 Bartka |
| R2 | Max odchylenie od korytarza | **2.5 km** | p90+margin |
| R3 | Dynamic MAX_BAG | **SOFT** (nie hard block) | telemetria |
| R4 | Free stop threshold | **0.5 km** | median Bartka |
| R4 | Free stop bonus (≤0.5km) | **+100** (weight 1.5) | |
| R4 | Corridor bonus (≤1.5km) | +50 linear decay | |
| R4 | Corridor bonus (≤2.5km) | +20 linear decay | |
| R5 | Max pickup spread mixed-rest | **1.8 km** (hard block) | p100 Bartka |
| R5 | Max bag size (hard) | **8** | D3 common.py |

### 8.2 Bartek vs flota

| Metryka | Bartek | Flota avg |
|---------|--------|-----------|
| Bundling % | **58.1%** | 31% |
| Avg bag size | **2.40** | 2.14 |
| Avg delivery time | **17 min** | 20 min |

**Wniosek: Bartek bundluje 2× częściej i dostarcza 3 min szybciej mimo większych bagów.**

### 8.3 Implementacja

- `feasibility_v2.py` — R1 spread outlier (hard), R5 mixed-rest pickup (hard), R3 soft warning
- `dispatch_pipeline.py` — R4 free stop bonus, R2 corridor check
- `route_simulator_v2.py` — lock first stop, Closest-First tie-breaker

---

## 9. GRAFIK KURIERÓW

### 9.1 Źródło danych

- Google Sheets ID: `1Z5kSGUB0Tfl1TiUs5ho-ecMYJVz0-VuUctoq781OSK8`, gid `533254920`
- Skrypt: `fetch_schedule.py` — cron 06:00 i 08:00 Warsaw
- Plik: `dispatch_state/schedule_today.json`

### 9.2 Filtrowanie kurierów

```python
# schedule_utils.py
is_on_shift(panel_name, schedule) → (bool, reason)
match_courier(panel_name, schedule) → full_name lub None
```

### 9.3 Zasady

- **Pre-shift**: kurier zaczyna za ≤50 min → dodawany do dispatchable z `pos_source="pre_shift"`, `shift_start_min=N`
- **Pre-shift exclusion**: jeśli `shift_start_min > prep_remaining` → verdict=NO (nie zdąży)
- **Shift-end guard**: kurier nie może dostać zlecenia jeśli `pickup_ready_at > shift_end`
- **Deduplikacja**: gdy 2 courier_id mają to samo imię → zostaje ten z lepszym pos_source

---

## 10. MANUAL OVERRIDE

Pisz w Grupie ziomka (bot słucha wszystkich wiadomości):

```
"Mykyta nie pracuje"     → wyklucza do końca dnia
"Mykyta nie pracuje"     → też: choruje / nie ma / wyklucz
"Mykyta wrócił"          → przywraca (też: pracuje / jest / dodaj)
"reset"                  → czyści wszystkie overrides
```

Plik: `/root/.openclaw/workspace/dispatch_state/manual_overrides.json`

Odpowiedź bota:
```
✅ Mykyta K wykluczony do końca dnia
✅ Mykyta K przywrócony
✅ Reset — wszyscy kurierzy aktywni
❓ Nie rozumiem. Przykład: 'Mykyta nie pracuje' lub 'Mykyta wrócił'
```

---

## 11. SCORING — FORMUŁA

```
total_score = S_dystans×0.30 + S_obciazenie×0.25 + S_kierunek×0.25 + S_czas×0.20
            + bundle_bonus × 1.5
            + availability_bonus
```

Gdzie:
- `S_*` ∈ [0, 100]
- `bundle_bonus` = L1 (+25) lub L2 (max +20) + L3 (max +15) niezależnie
- `availability_bonus` = +10/+8/+5/0

---

## 12. DAILY STATS GOOGLE SHEETS (F2.0)

### 12.1 Konfiguracja

- Skrypt: `/root/.openclaw/workspace/scripts/dispatch_v2/daily_stats_sheets.py`
- Cron: `0 6 * * *` (06:00 UTC = 08:00 Warsaw)
- Arkusz: "Controlling" → zakładka "Średnie"
- Spreadsheet ID: `1Z5kSGUB0Tfl1TiUs5ho-ecMYJVz0-VuUctoq781OSK8`
- Service account: `ziomek@gen-lang-client-0704473813.iam.gserviceaccount.com`
- venv: `/root/.openclaw/venvs/sheets/bin/python3`

### 12.2 Struktura arkusza

Per tydzień = nowy blok wierszy (NIE kolumn):
```
[opcjonalny wiersz: "Kwiecień"] ← tylko gdy nowy miesiąc zaczyna się w tym tygodniu
godzina | pon DD.MM | /3 | Ziomek | wt DD.MM | /3 | Ziomek | ... | Średnia | Śr/3 | Śr Ziomek
9       |     4     |  2 |   1    |    7     |  3 |   2    | ... |   5.5   |  2   |   1.5
...
23      |     1     |  1 |   1    |    2     |  1 |   1    | ... |   1.5   |  1   |   1.0
```

25 kolumn łącznie (1 godzina + 7×3 dni + 3 średnie).

### 12.3 Kolumna Ziomek

Inteligentna rekomendacja ile kurierów potrzeba w danej godzinie:
- Źródło: `shadow_decisions.jsonl` — unikalne feasible courier_id per godzina
- Formuła: `max(ceil(n/3), ceil(n/avg_feasible))`
- Fallback (brak shadow): `ceil(n/2.4)` (Bartek benchmark)

### 12.4 Backfill

```bash
python3 /root/.openclaw/workspace/scripts/dispatch_v2/daily_stats_sheets.py --date 2026-04-13
python3 /root/.openclaw/workspace/scripts/dispatch_v2/daily_stats_sheets.py --dry-run  # test bez zapisu
```

---

## 13. PANEL API — KRYTYCZNE FAKTY

### 13.1 Przypisanie zlecenia

```
POST /admin2017/new/orders/przypisz-zamowienie
Body: _token + id_zlecenie + id_kurier + time

time = INTEGER minuty od teraz (NIE HH:MM, NIE timestamp)
```

### 13.2 Detail endpoint

```
POST /admin2017/new/orders/edit-zamowienie
Body: _token + id_zlecenie
Response: {"zlecenie": {...}}
```

### 13.3 Zasady CSRF/sesji

- CookieJar **nie jest thread-safe** — wszystkie `edit-zamowienie` calls MUSZĄ być sequential
- CSRF token wymagany w każdym POST
- `get_last_panel_position()` NIE może wywoływać `urllib.request.install_opener` z nowym CookieJar (unieważni sesję → 419)

### 13.4 Active couriers

Pobieranie z `#showKurierzy` modal:
```python
pattern = r'value="(\d+)"[^>]*class="input_activeJob">'
```
44 total kurierów w panelu.

---

## 14. PLIKI I ŚCIEŻKI

```
/root/.openclaw/workspace/scripts/
├── dispatch_v2/
│   ├── panel_watcher.py           # scraping panelu
│   ├── shadow_dispatcher.py       # konsumuje eventy → decyzje
│   ├── dispatch_pipeline.py       # scoring + ranking
│   ├── courier_resolver.py        # fleet snapshot + dispatchable_fleet()
│   ├── feasibility_v2.py          # SLA check + R1/R3/R5 Bartek rules
│   ├── route_simulator_v2.py      # TSP brute-force/greedy + lock first stop
│   ├── scoring.py                 # scoring formuła
│   ├── telegram_approver.py       # bot + propozycje + TAK/NIE
│   ├── geocoding.py               # Nominatim + cache
│   ├── common.py                  # stałe (WARSAW, HAVERSINE_ROAD_FACTOR_BIALYSTOK=1.37)
│   ├── daily_stats_sheets.py      # F2.0 raport dzienny → Google Sheets
│   ├── event_bus.py               # SQLite event bus
│   ├── state_machine.py           # stan zleceń
│   ├── panel_client.py            # klient HTTP panelu
│   ├── osrm_client.py             # haversine + OSRM routing
│   └── docs/
│       ├── CLAUDE.md              # główna dokumentacja techniczna
│       ├── BARTEK_GOLD_STANDARD.md # reguły R1-R5 z analizy danych
│       └── TECH_DEBT.md           # znane problemy
├── gastro_assign.py               # przypisanie zlecenia do kuriera
│   # Użycie: python3 gastro_assign.py --id ORDER_ID --kurier "Imię K." --time MINUTES
│   # --time akceptuje: integer minut LUB HH:MM (auto-detect)
│   # --koordynator: przypisuje do Koordynatora (id_kurier=26)
│   # --keep-time: zachowuje oryginalny czas z panelu
├── gastro_login.py                # login do panelu Rutcom
├── gastro_scoring.py              # helper scoring (legacy)
├── fetch_schedule.py              # pobiera grafik z Google Sheets
└── schedule_utils.py              # is_on_shift(), match_courier()

/root/.openclaw/workspace/dispatch_state/
├── events.db                      # SQLite event bus
├── state.json                     # stan zleceń (rolling ~4 dni)
├── schedule_today.json            # grafik kurierów (dziś)
├── courier_names.json             # panel_id → imię kuriera
├── manual_overrides.json          # ręczne wykluczenia kurierów
├── restaurant_meta.json           # historyczne dane restauracji (prep_variance)
├── learning_log.jsonl             # decyzje TAK/NIE/INNY/KOORD
├── shadow_decisions.jsonl         # wszystkie decyzje shadow (z metrykami)
├── gps_positions_pwa.json         # GPS PWA pozycje kurierów
└── bartek_gold/
    ├── analyze_bartek.py          # skrypt analizy
    ├── analysis.json              # wyniki analizy
    └── geocode_cache.json         # cache geocodingu

/root/.openclaw/venvs/
└── sheets/                        # venv dla gspread (daily_stats_sheets.py)
```

---

## 15. ZASADY PRACY Z KODEM (HARD — NIGDY NIE ŁAMAĆ)

### 15.1 Styl patchowania

```bash
# 1. Backup
cp plik.py plik.py.bak-$(date +%Y%m%d-%H%M%S)

# 2. Patch (Python heredoc)
python3 -c "
path = 'plik.py'
with open(path) as f: s = f.read()
old = '''...stary kod...'''
new = '''...nowy kod...'''
assert old in s, 'STOP: nie znaleziono'
s = s.replace(old, new, 1)
with open(path, 'w') as f: f.write(s)
print('OK')
"

# 3. Weryfikacja
python3 -m py_compile plik.py && echo "compile OK"
python3 -c "from dispatch_v2.plik import Klasa; print('import OK')"

# 4. Test
python3 -c "... dry-run test ..."

# 5. Restart (tylko po zgodzie Adriana)
systemctl restart dispatch-shadow
```

### 15.2 Reguły bezwzględne

- **"Pytaj nie zgaduj"** — przed nieoczywistą zmianą zapytaj Adriana
- **cp .bak** ZAWSZE przed edycją pliku produkcyjnego
- **py_compile** po każdej zmianie Python
- **NIE restartuj systemd** bez py_compile + import check + zgody Adriana
- **NIE używaj sed do edycji** (tylko do odczytu grep/sed)
- **NIE używaj jq** (nie ma w systemie — używaj Python)
- **Jedna operacja str.replace per heredoc**
- **assert old in s** przed każdym str.replace
- **NIE heredoki do produkcji** bezpośrednio

### 15.3 Atomic writes

```python
import tempfile, os
tmp = path + '.tmp'
with open(tmp, 'w') as f:
    f.write(data)
    f.flush()
    os.fsync(f.fileno())
os.rename(tmp, path)
```

### 15.4 Warsaw TZ

```python
from zoneinfo import ZoneInfo
WARSAW = ZoneInfo("Europe/Warsaw")
now_warsaw = datetime.now(WARSAW)
```

---

## 16. NIGDY (lista zakazów)

- Nie łam produkcji bez cp .bak + py_compile + testy
- Nie używaj jq (brak w systemie)
- Nie dodawaj prep_variance do pickup_ready_at (bufor wyłączony F1.8g)
- Nie proponuj kuriera z picked_up jako L1/L2 bundle candidate
- Nie używaj identycznego ETA dla wszystkich kandydatów (= BUG)
- Nie używaj pozycji GPS starszej niż 60 min jako realnej pozycji
- Nie startuj Fazy 2 przed 14 dni stabilnego Ziomka
- Nie używaj sed do edycji plików
- Nie czytaj .secrets/ .ssh/ .env .pem .key
- Nie restartuj serwisów bez zgody Adriana
- Nie commituj do crona bez zgody Adriana

---

## 17. ZAWSZE

- Warsaw TZ: `ZoneInfo("Europe/Warsaw")` jako WARSAW
- Atomic writes (temp → fsync → rename)
- Update TECH_DEBT.md na koniec sesji
- Batch z explicite STOP po 5-8 krokach
- W commit messages referencjuj decyzje (D19, F1.x, F2.x)
- `/status` w Telegramie jako primary health check
- Weryfikuj empirycznie — sprawdź realny kod (grep/cat) przed patchem

---

## 18. PRIORYTETY (tydzień 2-3, 15-27.04)

### Pilne
- [ ] **Learning analyzer** (po 200+ decyzjach) — analiza agreement rate, wagi scoringu
- [ ] **Auto-approve** score >0.90 — eliminuje 60-70% kliknięć (60s okno COFNIJ)
- [ ] **getUpdates crash guard** — sys.exit(1) po N failach → systemd restart
- [ ] **Telegram security** — weryfikacja chat_id członków grupy

### Tydzień 3
- [ ] **Restimo API skeleton** — FastAPI + OAuth2 (Wolt Drive model)
- [ ] **Circuit breakers** — Ziomek nie pada przy awarii OSRM/panel
- [ ] **R17/R19 restaurant monitoring** — alerty prep >5 min
- [ ] **Rutcom kontakt** — GPS kurierów przez panel API
- [ ] **GPS kurierów** — dogadać z Rutcom endpoint lub GPSLogger na telefonach firmowych

### Faza 2 (po 14 dniach stabilnego Ziomka)
- [ ] Auto-approve z learning
- [ ] Wielomiastowość (Warszawa)
- [ ] OR-Tools VRPTW

---

## 19. HISTORIA COMMITÓW (wybrane)

```
e84b25c F2.0: daily_stats_sheets.py Google Sheets raport dzienny
f24f0c9 F1.9b: R3 soft-only, ETA plan-based fix, R4 weight monitoring
4ea7a4d F1.9a: shadow_dispatcher telemetry R1/R3/R4/R5 metrics
dde032f F1.9: Bartek Gold Standard R1/R2/R3/R4/R5
3b832f3 F1.8f+g: shift_end guard + usunięcie bufora prep_variance
fd7fde0 F1.8e: pre_shift hard exclude
a8450d4 F1.8d: pos time-based degradation, ETA per-candidate drive_min
0e47dff F1.8c: bundling assigned-only, availability bonus, compute_assign_time
6522980 F1.8: bundling L1/L2/L3 same-rest/nearby/corridor SLA 45min
96f1cfc F1.8b: target_pickup_at absolutny — time_param świeży przy TAK
```

---

## 20. KONTAKT I DOSTĘPY

```
Serwer:       Hetzner CPX22, 178.104.104.138, Ubuntu 24.04, UTC
Panel:        gastro.nadajesz.pl (Laravel, CSRF)
Bot dispatch: @NadajeszBot
Grupa TG:     "Grupa ziomka" (chat_id: -5149910559)
Admin TG:     8765130486
GPS PWA:      https://gps.nadajesz.pl
Repo:         github.com/czaplaadrian88-code/ziomek-dispatch-
Routing:      Google Maps Distance Matrix API
Geocoding:    Nominatim/OpenStreetMap
Sheets SA:    ziomek@gen-lang-client-0704473813.iam.gserviceaccount.com
Sheets ID:    1Z5kSGUB0Tfl1TiUs5ho-ecMYJVz0-VuUctoq781OSK8
```

---

## 2026-04-15 UPDATE — F2.1b Decision Engine 3.0 deployed

Wersja doc zachowana jako V3.6 (nie V3.7) bo zmiana dotyczy warstwy reguł feasibility/scoring, nie architektury systemu — master doc pozostaje aktualny, reguły biznesowe rozszerzone o R6-R9 (patrz `docs/BARTEK_GOLD_STANDARD.md` sekcja "F2.1 Extensions").

### Sprint F2.1b — 10 kroków (13-15.04.2026)

- **Step 0:** enriched_metrics flat append (observability baseline dla L1/L2/R4/R6/R8/R9)
- **Step 1:** `telegram_utils.send_admin_alert` (R6 alert channel, minimalistyczny wrapper)
- **Step 2:** `common.py` F2.1 extensions (kalibracja 35/30/30 z empirycznego p95=35.6 prod)
- **Step 3:** `feasibility_v2` R6 BAG_TIME hard + R7 longhaul peak [R8 deferred F2.1c]
- **Step 4:** `dispatch_pipeline` penalties R6_soft + R9_stopover + R9_wait
- **Step 5:** `state_machine.bag_time_alerted` flag (5/6 handlers, COURIER_PICKED_UP race-safe)
- **Step 6:** `sla_tracker._check_bag_time_alerts` hook + courier_names cache
- **Step 6.1:** `_parse_aware_utc` hotfix (CONFIRMED prod: wszystkie `picked_up_at` są naive Warsaw, nie aware UTC)
- **Step 7:** test suite 38/38 PASS (A=regression, B=unit+race, C=bundle, D=edge, E=anti-pattern, F=sanity+smoke)
- **Step 8:** FAZA A restart sla-tracker → FAZA B restart dispatch-shadow → docs → final commit

### Deployment timeline

- **FAZA A:** 2026-04-15 12:56:04 UTC — `dispatch-sla-tracker` restart, **R6 pre-warning LIVE** (step 6.1 `_parse_aware_utc` hotfix post FAZA A initial)

**FAZA B deployment history** (3 restart waves, stopniowy rollout wykrył 3 retroactive hotfixe):

- **Initial restart (partial schema 4/13 fields)**: 2026-04-15 18:34:24 UTC
  - dispatch-shadow PID 1269384 start, step 4 penalties w runtime (bonus_penalty_sum present)
  - Step 3 R6/R7 telemetry metrics missing w shadow_decisions.jsonl (serializer whitelist gap)
- **Step 3.1 hotfix restart (serializer +9 R6/R7 keys)**: 2026-04-15 19:13:34 UTC
  - dispatch-shadow PID 1272109 start, schema 13/13 complete
  - Bug #466290 Chicago Pizza @ 19:16:45 UTC ujawnił R9 wait anomalia dla no_gps Patryk 5506 (-101.76)
- **Step 4.1 hotfix restart (R9 wait effective_drive_min dla no_gps/pre_shift)**: 2026-04-15 20:19:55 UTC
  - dispatch-shadow PID 1277022 start, **FAZA B COMPLETE** runtime state
  - effective_drive_min replikuje post-loop normalization linia 450/465 dla synthetic pos

**FAZA B COMPLETE:** 2026-04-15 20:19:55 UTC (step 4.1 = final runtime state, R6 hard + R7 longhaul peak + scoring penalties R6_soft/R9_stopover/R9_wait live w dispatch-shadow)

### Empirical milestones (observability step 0 live)

- **`bonus_l1=25.0` first production** — order #466122 Rany Julek, kurier 400 Adrian R, 2026-04-15 11:16:59 UTC. Pierwszy same-resto bundle z pełnym enriched_metrics breakdown zalogowany do shadow_decisions.jsonl.
- **R6 pre-warning first alert** — order #466154 `bag_time=43.1 min`, `alerted=True` (sla_tracker FAZA A live detected).

### Runtime state po F2.1b [post-FAZA B]

- **`feasibility_v2`:** R1-R5 (F1.9) + **R6 hard 35** + **R7 peak 14-17 Warsaw** + R3/R7 telemetry metrics
- **`dispatch_pipeline`:** L1/L2/R4 bonuses (F1.9) + **R6_soft penalty (30-35)** + **R9_stopover differential** + **R9_wait > 5min** + `bonus_penalty_sum` aggregate
- **`state_machine`:** `bag_time_alerted` flag z race-safe reset (5 z 6 handlerów, `COURIER_PICKED_UP` celowo nie resetuje)
- **`sla_tracker`:** R6 pre-warning scan co 10s + `_parse_aware_utc` dla naive Warsaw timestamps (legacy `_parse` nietknięty dla SLA check)
- **`shadow_decisions.jsonl`:** schema extended o `bonus_l1/l2/r4/r6_soft_pen/r9_stopover/r9_wait_pen/penalty_sum` + R6/R7 metrics telemetry

### Feature flags F2.1c TODO

- `AUTO_APPROVE_ENABLED=False` (blokada do 200+ walidowanych decyzji + silent_agreement analyzer)
- `ANOMALY_DETECTION_ENABLED=False` (blokada do `context.restaurant_prep_variance()` + `context.courier_recent_delay()` impl)
- R8 `PICKUP_SPAN_*` constants w miejscu ale unused (DEFERRED do F2.1c T_KUR propagation)

### Related docs

- `docs/BARTEK_GOLD_STANDARD.md` — F2.1 Extensions R6-R9 pełna spec + kalibracja empiryczna
- `docs/TECH_DEBT.md` — F2.1b resolved (verified_by table) + F2.1c backlog (8 items)
- `tests/test_decision_engine_f21.py` — 38 testów regression/unit/integration, plain Python (zero pytest)
