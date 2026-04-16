# ZIOMEK V3.7 — Instrukcja projektu Gastro (16.04.2026)

**Zmiana vs V3.6:** F2.1b Decision Engine 3.0 COMPLETE — 3 hotfixy (step 6.1, 3.1, 4.1), 40 testów PASS, FAZA A + FAZA B live, +4 empirical milestones.

## Kim jestem i co budujemy

**Adrian Czapla**, owner NadajeSz (Białystok, ekspansja Warszawa), firma kurierska gastro-delivery. 30 aktywnych kurierów, 55 restauracji, ~1500-2000 orderów/tydz, revenue transport ~35-45k PLN/tydz + GMV cash 70-90k PLN/tydz.

Buduję **Ziomka** — autonomicznego AI dispatchera zastępującego ręczną pracę koordynatora.

## Stan systemu na 16.04.2026

### 6 serwisów live (systemd, wszystkie active)
- `dispatch-panel-watcher` — scraping panelu Rutcom co 20s
- `dispatch-sla-tracker` — monitoring SLA dostaw + **R6 BAG_TIME pre-warning** (F2.1b)
- `dispatch-shadow` — shadow dispatcher, **Decision Engine 3.0** (F2.1b)
- `dispatch-telegram` — bot @NadajeszBot, propozycje TAK/NIE/INNY/KOORD
- `dispatch-gps` — GPS PWA server (port 8766)
- `nginx` — reverse proxy 443 → 8766

### Git repo
- github.com/czaplaadrian88-code/ziomek-dispatch-
- Cron hourly push (backup)
- Ostatni tag: `pre-F2.1b-complete` (14 tagów rollback F2.1b)

### Telegram
- **Grupa ziomka** — Ty + Bartek Ołdziej + @NadajeszBot
- chat_id grupy: -5149910559
- personal_admin_id: 8765130486
- Privacy mode: DISABLED (bot widzi wszystkie wiadomości)
- Wolny tekst: "Mykyta nie pracuje" → wyklucza kuriera
- /status → stan serwisów + stats
- **Prywatny chat z @NadajeszBot** — R6 BAG_TIME alerty (od F2.1b)

## Jak Ziomek przypisuje zlecenia (FUNDAMENTALNE)

### Przepływ
1. `panel_watcher` scrape co 20s → nowe zlecenie → `event_bus` (events.db)
2. `shadow_dispatcher` pobiera event → `dispatch_pipeline.assess_order()`
3. Pipeline: `dispatchable_fleet()` → **feasibility R1-R7** → scoring → bundling → ranking → `shadow_decisions.jsonl`
4. `telegram_approver` wysyła [PROPOZYCJA] do Grupy ziomka
5. Adrian lub Bartek klikają TAK → `gastro_assign.py --id X --kurier Y --time Z`
6. Panel Rutcom przypisuje kuriera, wysyła czas do restauracji

### Format propozycji
[PROPOZYCJA] #465907
Naleśniki Jak Smok → Kombatantów 18/140
🕐 Odbiór: 21:12 (za 20 min)
🎯 Bartek O. (233.73) — 0.1 km, ETA 20:53 → deklarujemy 21:33  🟡 za 26 min  🔗 blisko: Restauracja Sioux (0.2km)  🔗 po drodze (0.1km)
🥈 Michał Rom (39.30) — 8.7 km, ETA 21:14 → deklarujemy 21:13  🟡 za 1 min
✓ feasible=2 best=123
TAK / NIE / INNY / KOORD

### Format alertu R6 (NEW F2.1b)
⚠️ BAG_TIME 32 min (limit 30)
#466123 Rany Julek → Liliowa 32h
Kurier: Marek (207) • picked up 13:45
Wysyłany do admin_id (prywatny chat), one-shot per order, set-then-send (flag=True przed send).

### Czas deklarowany (KRYTYCZNE)
- Panel ma dropdown: 5/10/15/20/25/30/35/40/45/50/55/60 min **od teraz**
- `time_param = ceil(max(travel_min, prep_remaining) / 5) * 5`
- `prep_remaining = (pickup_ready_at - now)` w minutach
- `pickup_ready_at = czas_odbioru_timestamp` z panelu (BEZ bufora prep_variance, F1.8g)
- Minimum: 5 min, Maximum: 60 min

### Pola czasowe panelu (KRYTYCZNE)
- `created_at` (UTC, suffix Z): kiedy złożono zlecenie
- `czas_odbioru` (int, minuty): czas przygotowania
- `czas_odbioru_timestamp` (Warsaw): najwcześniej kiedy restauracja chce kuriera = created_at + czas_odbioru
- `czas_kuriera` (HH:MM): zadeklarowany przez Ziomka czas przyjazdu kuriera
- **NIE dodajemy żadnych buforów do pickup_ready_at**

### Order types
- `czas_odbioru < 60` = elastyk → Ziomek proponuje kuriera
- `czas_odbioru >= 60` = czasówka → automatycznie do Koordynatora (id_kurier=26)

### Status mapping
- 2=nowe/nieprzypisane, 3=dojazd, 4=oczekiwanie, 5=odebrane, 6=opóźnienie, 7=doręczone, 8=nieodebrano, 9=anulowane
- Watcher ignoruje 7, 8, 9

## Pozycja kuriera — hierarchia 6 poziomów (F1.8d + F2.1b verified)

**NIGDY nie używaj pozycji bez sprawdzenia timestamp. NIGDY nie używaj pozycji >60 min.**

1. `gps_fresh` (<5 min) — najdokładniejsze
2. `last_picked_up_delivery` — ma picked_up → jedzie do delivery_coords
3. `last_assigned_pickup` — ma assigned → jedzie do pickup_coords
4. `last_delivered` (<30 min) — blisko delivery_coords
5. `last_picked_up_recent` (<30 min) — delivery_coords recent picked_up
6. `no_gps` — brak aktywności >60 min → synthetic BIALYSTOK_CENTER
7. `pre_shift` — zaczyna zmianę za ≤50 min → synthetic centrum + shift_start_min

**F2.1b hotfix step 4.1:** dla `no_gps`/`pre_shift`, R9 wait używa `effective_drive_min = max(drive_min, prep_remaining)` — spójne z post-loop normalization.

**ETA w propozycji = now + drive_min (haversine per kandydat, NIE pickup_ready_at dla wszystkich)**
**Każdy kandydat ma RÓŻNE ETA — identyczne ETA = BUG**

## Bundling — zasady operacyjne (F1.9 + F2.1b)

### Trzy poziomy
- **L1 same-restaurant** (+25 score): kurier ma w bagu `assigned` (NIE picked_up) zlecenie z tej samej restauracji
- **L2 nearby pickup** (+max 20): restauracja nowego zlecenia <1.5 km od restauracji w bagu (assigned only)
- **L3 corridor delivery** (+max 15): delivery nowego zlecenia w korytarzu 2.0 km od trasy kuriera

**L1/L2 tylko dla bag ze statusem `assigned` — kurier z `picked_up` już jedzie z jedzeniem, NIE wraca**

### Availability bonus
- 🟢 bag=0 → +10
- 🟡 kończy za <15 min → +8
- 🟠 kończy za 15-30 min → +5
- brak tagu → +0

### SLA
- Solo zlecenie: 35 min od pickup
- Bundle: 45 min od pickup (dane Bartka: 95% mieści się w 45 min)
- **R6 HARD 35 min w torbie** (F2.1b) — od pickup do predicted_delivered_at

### Route simulator (D19 obowiązuje)
- bag ≤ 3 → brute-force PDP-TSP
- bag ≥ 4 → greedy insertion O(N²)
- **Lock first stop**: kurier z picked_up → najpierw dostarcza, potem nowy pickup

## Bartek Gold Standard (F1.9) — parametry z 231 zleceń

| Reguła | Parametr | Wartość | Źródło |
|--------|----------|---------|--------|
| R1 | Max spread delivery w bundlu | 8.0 km | p90 empiryczne |
| R2 | Max odchylenie od korytarza | 2.5 km | p90+margin |
| R3 | Dynamic MAX_BAG (≤5km) | 5 | soft (nie hard) |
| R3 | Dynamic MAX_BAG (≤8km) | 5 | soft (nie hard) |
| R3 | Dynamic MAX_BAG (>8km) | 3 | soft (nie hard) |
| R4 | Free stop threshold | 0.5 km | median Bartka |
| R4 | Free stop bonus (≤0.5km) | +100 | weight 1.5 |
| R4 | Corridor bonus (≤1.5km) | +50 linear | |
| R4 | Corridor bonus (≤2.5km) | +20 linear | |
| R5 | Max pickup spread mixed-rest | 1.8 km | p100 Bartka |
| R5 | Max bag size (hard) | 8 | D3 common.py |

**Bartek vs flota: 58.1% bundling vs 31% avg, 17 min delivery vs 20 min avg**

## F2.1 Extensions — Decision Engine 3.0 (R6-R9, F2.1b 15.04.2026)

### R6 BAG_TIME (hard 35 + soft 30-35)

**Kalibracja empiryczna:** 743 delivered orderów 11-15.04.2026:
- p50: 15.1 min | p75: 23.0 | p90: 30.9 | p95: 35.6 | p99: 44.3 | max: 80.5
- 5.7% orderów > 35 min | 11.6% > 30 min

**Stałe:**
- `BAG_TIME_HARD_MAX_MIN = 35` (p95 obcina 5.7% thermal tail)
- `BAG_TIME_SOFT_MIN = 30` (p90, soft zone -8/min)
- `BAG_TIME_PRE_WARNING_MIN = 30` (sla_tracker Telegram alert)
- `BAG_TIME_SOFT_PENALTY_PER_MIN = 8`

**Enforcement:**
1. `feasibility_v2` — hard reject projekcji >35 min (reuse plan.predicted_delivered_at)
2. `dispatch_pipeline` — soft penalty 30-35 (reuse metrics.r6_max_bag_time_min)

### R7 Long-haul peak isolation

**Reguła:** ride_km > 4.5 AND hour ∈ [14,17] Warsaw AND bag niepusty → hard reject.

**Stałe (placeholder, F2.1c kalibracja):**
- `LONG_HAUL_DISTANCE_KM = 4.5`
- `LONG_HAUL_PEAK_HOURS_START = 14`
- `LONG_HAUL_PEAK_HOURS_END = 17`

**Monitoring triggerów kalibracji:**
- reject rate >20% w peak → bump threshold w górę
- reject rate <2% w peak → bump w dół
- cel: 5-10% reject rate w peak

### R8 Pickup span czasowy (DONE F2.1c, 16.04.2026)

**Planowane:** max 15 min T_KUR spread dla bundle=2, max 30 min dla bundle=3.

**Dlaczego odroczone:** `_bag_dict_to_ordersim` nie propaguje pickup_ready_at do OrderSim dla orderów w bagu. T_KUR znany tylko dla nowego ordera.

**Placeholdery common.py (unused do F2.1c):**
- `PICKUP_SPAN_HARD_BUNDLE2_MIN = 15`
- `PICKUP_SPAN_HARD_BUNDLE3_MIN = 30`
- `PICKUP_SPAN_SOFT_START_MIN = 7`
- `PICKUP_SPAN_SOFT_PENALTY_PER_MIN = 3`

### R9 Stopover + wait penalty

**R9 stopover** (differential):
bonus_r9_stopover = -len(bag) * STOPOVER_SCORE_PER_STOP   # -8 per stop

**R9 wait:**
wait_pred = max(0, (T_KUR - now) - effective_drive_min)
if wait_pred > 5:
bonus_r9_wait_pen = -(wait_pred - 5) * 6

**F2.1b hotfix step 4.1:** `effective_drive_min` zamiast raw `drive_min`:
- no_gps: `max(15, prep_remaining_min)` (spójne z post-loop linia 450)
- pre_shift: `shift_start_min` (spójne z linia 465)
- inne: `drive_min` bez zmian (GPS path)

### Kolejność egzekucji w feasibility_v2 (F2.1b)
1. bag size cap (MAX=8)
2. **R7 long-haul peak** (fast, no OSRM)
3. R1 delivery spread (≤8km)
4. R5 mixed-rest pickup (≤1.8km)
5. pickup_dist reach (≤15km)
6. shift_end guard (≥20min buffer)
7. simulate_bag_route_v2 (OSRM)
8. SLA violations (sla_minutes budget)
9. **R6 BAG_TIME hard** (≤35min, po simulate)
10. return MAYBE / NO

## Race condition guard (krytyczny regression)

`COURIER_PICKED_UP` handler w `state_machine.update_from_event` **CELOWO NIE resetuje** `bag_time_alerted` do False. Panel_watcher reconcile może reemit event po sla_tracker set flag=True — reset = duplicate alerts.

Reset odbywa się w: NEW_ORDER, COURIER_ASSIGNED, COURIER_DELIVERED, COURIER_REJECTED_PROPOSAL, ORDER_RETURNED_TO_POOL (5 z 6 handlerów).

**Regression guard:** `tests/test_decision_engine_f21.py::test_B11`.

## Grafik kurierów

- Google Sheets ID: `1Z5kSGUB0Tfl1TiUs5ho-ecMYJVz0-VuUctoq781OSK8`, gid `533254920`
- `fetch_schedule.py` — cron 06:00 i 08:00 Warsaw
- `schedule_utils.py` — `is_on_shift(panel_name, schedule)` → (bool, reason)
- Pre-shift: zaczyna za ≤50 min → dodawany do dispatchable z pos_source="pre_shift"
- Shift-end guard: kurier nie może dostać zlecenia jeśli zmiana kończy się przed pickup_ready_at

## Manual override (bot Telegram)

Pisz w Grupie ziomka:
- `"Mykyta nie pracuje"` → wyklucza do końca dnia
- `"Mykyta wrócił"` → przywraca
- `"reset"` → czyści wszystkie overrides
- Plik: `/root/.openclaw/workspace/dispatch_state/manual_overrides.json`

## Daily Stats Google Sheets (F2.0)

- Skrypt: `/root/.openclaw/workspace/scripts/dispatch_v2/daily_stats_sheets.py`
- Cron: `0 6 * * *` (06:00 UTC = 08:00 Warsaw)
- Arkusz: "Controlling" → zakładka "Średnie"
- Spreadsheet ID: `1Z5kSGUB0Tfl1TiUs5ho-ecMYJVz0-VuUctoq781OSK8`
- Service account: `ziomek@gen-lang-client-0704473813.iam.gserviceaccount.com`
- Struktura: godzina (9-23) × dni tygodnia, per dzień: [liczba zleceń] [/3] [Ziomek rekomendacja]

## Panel API — krytyczne fakty techniczne

- `time` w `przypisz-zamowienie` = **minuty od teraz jako integer**
- `czas_odbioru_timestamp` = **Warsaw TZ** (nie UTC)
- `created_at` = UTC (suffix Z)
- Detail endpoint: `POST /admin2017/new/orders/edit-zamowienie`, body: `_token + id_zlecenie`
- CookieJar nie thread-safe — `edit-zamowienie` calls MUSZĄ być sequential
- CSRF tokens required (Laravel)
- `id_kurier=26` = Koordynator (virtual bucket dla czasówek)
- Active courier IDs z `#showKurierzy` modal

## Scoring — formuła
final_score = S_dystans×0.30 + S_obciazenie×0.25 + S_kierunek×0.25 + S_czas×0.20
+ bundle_bonus (L1/L2/L3, weight 1.5)
+ availability_bonus (bag=0/15min/30min)
+ bonus_penalty_sum (R6_soft + R9_stopover + R9_wait_pen)   # F2.1b NEW

## Pliki i ścieżki
/root/.openclaw/workspace/scripts/
├── dispatch_v2/
│   ├── panel_watcher.py
│   ├── shadow_dispatcher.py           # 13-field enriched_metrics (F2.1b)
│   ├── dispatch_pipeline.py           # R9 stopover/wait + effective_drive_min (F2.1b step 4.1)
│   ├── courier_resolver.py            # hierarchia 6 pos_source levels
│   ├── feasibility_v2.py              # R1/R5 + R6 hard + R7 longhaul (F2.1b)
│   ├── route_simulator_v2.py          # TSP + lock first stop
│   ├── scoring.py
│   ├── telegram_approver.py
│   ├── telegram_utils.py              # send_admin_alert (F2.1b step 1)
│   ├── sla_tracker.py                 # R6 pre-warning + _parse_aware_utc (F2.1b step 6.1)
│   ├── state_machine.py               # bag_time_alerted race-safe (F2.1b step 5)
│   ├── geocoding.py
│   ├── common.py                      # F2.1 extensions (R6/R7/R8 placeholders/R9)
│   ├── daily_stats_sheets.py          # F2.0 raport dzienny
│   ├── tests/
│   │   └── test_decision_engine_f21.py # 40 testów F2.1b
│   └── docs/
│       ├── CLAUDE.md
│       ├── BARTEK_GOLD_STANDARD.md    # R1-R5 + F2.1 Extensions R6-R9
│       ├── TECH_DEBT.md               # F2.1b DONE + F2.1c backlog
│       └── ZIOMEK_V3_7_INSTRUKCJA.md  # THIS FILE
├── gastro_assign.py
├── gastro_login.py
├── fetch_schedule.py
└── schedule_utils.py
/root/.openclaw/workspace/dispatch_state/
├── events.db                          # SQLite event bus
├── orders_state.json                  # state zleceń
├── schedule_today.json                # grafik (dziś)
├── courier_names.json                 # panel_id → imię
├── manual_overrides.json              # ręczne wykluczenia
├── restaurant_meta.json               # dane restauracji
├── learning_log.jsonl                 # TAK/NIE/INNY decyzje
├── shadow_decisions.jsonl             # wszystkie decyzje shadow
└── bartek_gold/                       # analiza Bartka (231 orderów)

## F2.1b Empirical Milestones

1. **11:16:59 UTC** — bonus_l1 = 25.0 first prod (#466122 Rany Julek, Adrian R)
2. **12:56:04 UTC** — FAZA A deploy (sla_tracker + R6 pre-warning)
3. **~13:26 UTC** — first real R6 alert (#466154 @ 43.1min, admin_id confirmed)
4. **19:16:45 UTC** — #466290 Chicago Pizza R9 wait bug milestone (Patryk 5506, captured w test B13)

## Zasady współpracy (HARD)

- **"Pytaj nie zgaduj"** — każde zgadywanie = 10-30 min debug
- **Weryfikuj empirycznie** — sprawdź realny kod (grep/sed/cat) przed patchem
- **Styl patchowania:** `cp .bak-YYYYMMDD-HHMMSS` → str.replace (assert old in s) → py_compile → import check → test → rerun
- **NIE heredoki do produkcji.** Jedna operacja str.replace per Python heredoc
- **Dry-run z mockami przed restartem produkcji** — zawsze
- **NIE restartuj systemd bez py_compile + import check + zgody Adriana**
- **Stopniowy rollout** — faza A przed B, granular git tags jako rollback points
- **Claude Code approval: klikaj 1 Yes nigdy 2 "don't ask again"** — każda destrukcyjna komenda świadoma decyzja

## NIGDY

- Nie łam produkcji bez cp .bak-* + py_compile + testy
- Nie używaj jq (brak w systemie)
- Nie dodawaj prep_variance do pickup_ready_at (bufor wyłączony w F1.8g)
- Nie proponuj kuriera z picked_up jako bundle candidate (L1/L2)
- Nie używaj identycznego ETA dla wszystkich kandydatów
- Nie używaj pozycji GPS starszej niż 60 min jako realnej pozycji
- Nie startuj Fazy 2 przed 14 dni stabilnego Ziomka
- Nie używaj sed do edycji (tylko do odczytu)
- Nie czytaj .secrets/, .ssh/, .env, .pem, .key
- **Nie resetuj bag_time_alerted w COURIER_PICKED_UP handler** (F2.1b regression guard)

## ZAWSZE

- Warsaw TZ: `ZoneInfo("Europe/Warsaw")` jako WARSAW
- Atomic writes: temp → fsync → rename
- Update TECH_DEBT.md na koniec sesji
- Batch z explicite STOP po 5-8 krokach
- W commit messages referencjuj decyzje (D19, F1.x, F2.1b step X)
- /status w Telegramie jako primary health check
- Granular git tags per krok (`pre-F2.1X-stepN`)
- Per krok w sprincie: draft → ACK → cp .bak → edit → verify → commit

## Kontakt i dostępy

- Serwer: Hetzner CPX22, 178.104.104.138, Ubuntu 24.04, UTC
- Panel: gastro.nadajesz.pl (Laravel, CSRF)
- Bot dispatch: @NadajeszBot | Grupa: "Grupa ziomka" (-5149910559)
- Bot control: @GastroBot (NadajeszControlBot) port 8443 HTTPS
- GPS PWA: https://gps.nadajesz.pl (Let's Encrypt, nginx, PIN auth)
- Repo: github.com/czaplaadrian88-code/ziomek-dispatch-
- Telegram admin ID: 8765130486
- Routing: Google Maps Distance Matrix API
- Geocoding: Nominatim/OpenStreetMap (+ Google Geocoding dla miast poza Białystok)

## F2.1c priorytety (następny sprint, 3-5 dni)

**Grupa 1 — zamknięcie luk F2.1b:**
1. **R8 pickup_span timing rule** — ✅ DONE 16.04.2026 (F2.1c step 1). T_KUR propagation w `_bag_dict_to_ordersim`, hard cap 15/30 min, soft penalty 7-15 min. Observability: `r8_pickup_span_min` + `bonus_r8_soft_pen` w shadow_decisions.jsonl (step 2 retroactive serializer fix).
2. **`_parse()` unified fix + regression test SLA path** (~2h) — zamyka techdebt step 6.1

**Grupa 2 — learning analyzer + auto-approve:**
3. **`learning_analyzer.compute_agreement_per_bonus_layer()`** (~3h) — analiza 7-14 dni F2.1b data, per-rule stats
4. **`AUTO_APPROVE_ENABLED` flip** (~2h) — wymaga 200+ walidowanych decyzji + silent_agreement analyzer

**Grupa 3 — F2.2 candidates:**
5. `ANOMALY_DETECTION_ENABLED` (A1 prep_variance + A2 courier_delay)
6. Cascaded R6 alerts (30/35/40 escalation if needed)
7. `bag_time_alerted` jednorazowa migracja (currently lazy)
8. Pytest fixture infrastructure (B13 rozszerzenie)

**F2.1c scope rekomendowany:** Grupa 1 + Grupa 2 = ~10h. Grupa 3 do F2.2.

## Poza sprintami — biznesowy roadmap

### Priorytet #1: GPSLogger setup dla 7+ kurierów (ad-hoc, ~2-3h)
Gabriel, Grzegorz, Dariusz M, Szymon P, Adrian R, Mateusz O, Łukasz B. URL GPS: `https://178.104.104.138.nip.io:8765/gps?pin=PIN&lat=%LAT&lon=%LON&acc=%ACC`. 30s intervals, battery optimization OFF.

### Priorytet #2: Restimo API integration (pending biznesowo)
Blocker: minimum threshold klientów Restimo. Ustalić: quote-then-order vs direct order flow. Scope: FastAPI + OAuth2 + endpointy `POST /quote`, `POST /orders`, `GET /orders/{id}`, `POST /orders/{id}/cancel`. Shared PostgreSQL z Ziomkiem.

### Priorytet #3: Warsaw expansion (miesiąc+5)
Wymaga 2-4 tygodni stabilnej produkcji BB + learning analyzer dane + recrutement kurierów WAR + umowy restauracji.

## Narzędzia i dependencies

- Platform: OpenClaw 2026.3.27, Docker, Ubuntu 24.04 (Hetzner CPX22)
- AI model: GPT-5.4-mini (OpenAI), DeepSeek fallback
- GPS: Traccar (self-hosted Docker), GPSLogger Android (mendhak v136+)
- Panel: gastro.nadajesz.pl (Laravel)
- APIs: Google Maps Distance Matrix, Google Geocoding, Google Sheets (fetch 06:00, 08:00 daily)
- Communication: Telegram Bot API @NadajeszBot + @GastroBot
- Testing: plain Python scripts, zero pytest deps (40 testów w test_decision_engine_f21.py)
- Future stack: FastAPI + PostgreSQL + Celery (Restimo API)
