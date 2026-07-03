# ZIOMEK V3.1 — SKILL.md MASTER (serwer, pełna wersja)

**Wersja:** 3.1
**Data:** 12.04.2026 wieczór
**Autor:** Adrian + Claude (sesje 11-12.04 + 4 audyty zewnętrzne)
**Status:** Master document — czytany na początku każdej sesji

---

## SEKCJA 0 — JAK UŻYWAĆ TEGO DOKUMENTU

Ten plik jest jedynym źródłem prawdy projektu Ziomek. Zastępuje wszystkie wcześniejsze wersje. Zawiera decyzje, reguły, architekturę, checklist wdrożenia i KPI.

**Na początku każdej nowej sesji Claude czyta w kolejności:**
1. **`docs/SKILL.md`** (ten plik) — master z decyzjami i regułami
2. **`docs/SYSTEM_FLOW.md`** — szczegóły operacyjne flow
3. **`docs/TECH_DEBT.md`** — bieżący stan wdrożeń, backlog
4. **`docs/FAZA_0_SPRINT.md`** — aktualny sprint plan (jeśli istnieje)

Potem: sanity check produkcji, potem pytanie "Adrian co priorytet dnia".

**Zasady współpracy:**
- Polski, konkretnie, bez lania wody
- "Pytaj nie zgaduj" — weryfikuj kod/dane przed patchem
- Mały patch (cp .bak → str.replace → py_compile → test → rerun), nie wielkie heredoki
- Dry-run z mockami przed restartem produkcji
- Krótkie updaty "✅ X zrobione, ⏳ Y w trakcie"
- Jakość > szybkość

---

## SEKCJA 1 — MISJA I CEL

**Cel biznesowy:** Ziomek zastępuje ręczną pracę koordynatora. Skalowanie Białystok → Warszawa → API dla aggregatorów (Restimo).

**Cele ilościowe (z analizy danych):**
- **+10% throughput** przez lepsze bundling (+185k PLN/rok Białystok)
- **+90k PLN/rok** oszczędności paliwa + throughput przez deadhead component (F1.X1)
- **+240k PLN/rok** przez prep_ready_at integration (F1.X3 — ratowanie kurierów przed staniem)
- **+150k PLN/rok** przez fix courier_resolver priority bug (P0.3)
- **+60k PLN/rok** przez gap_fill restaurant_meta (P0.7)
- **-24% violations** przez outlier detection + peripheral tier
- **+85% SLA baseline** na flocie (obecnie marzec 94%, styczeń 87%)
- **Zero violations >90 min** (data quality filter R9)

**Łączny potencjalny ROI rocznie (Białystok): ~+600-700k PLN. Warszawa (3x): ~+1.8M PLN.**

**Skala obecna (tydzień 16-22.03.2026):**
- 1537 doręczonych orderów/tydzień
- 35,810 PLN transport + 69,955 PLN GMV = ~105k PLN
- 27 aktywnych kurierów, 55 aktywnych restauracji
- Skala roczna: ~1.87M PLN transport, ~3.6M PLN GMV (Białystok)

---

## SEKCJA 2 — STAN OBECNY

### Co działa (od 12.04 wieczorem)

**Runtime:**
- Hetzner CPX22, Ubuntu 24.04, IP 178.104.104.138
- OpenClaw 2026.3.27, model `openai/gpt-5.4-mini` + DeepSeek fallback
- Telegram `@NadajeszBot` (dispatch), `@GastroBot` (sterowanie), admin 8765130486

**Serwisy systemd ACTIVE:**
- `dispatch-panel-watcher.service` — 20s reconcile
- `dispatch-sla-tracker.service` — 10s konsumer

**Wdrożone moduły (`/root/.openclaw/workspace/scripts/dispatch_v2/`):**
- ✅ common.py, event_bus.py, state_machine.py, panel_client.py
- ✅ panel_watcher.py, sla_tracker.py, scoring.py (v1)
- ✅ osrm_client.py, geocoding.py, geometry.py, traffic.py
- ✅ courier_resolver.py (z bug priority — P0.3)

**Do fix w Fazie 0:**
- 🔨 common.py — dynamiczny MAX_BAG_SIZE (P0.1)
- 🔨 scoring.py — time_penalty + R27 window (P0.2)
- 🔨 courier_resolver.py — priority bug (P0.3)
- 🔨 panel_watcher.py — pickup_coords null fix (P0.4)
- 🔨 osrm_client.py — haversine fallback (P0.5)

**Do przepisania:**
- 🗑️ route_simulator.py v1 → v2 PDP-TSP (Faza 1)
- 🗑️ feasibility.py v1 → v2 z R1/R3/R8/R20/R27 (Faza 1)

**Nie istnieje jeszcze:**
- 🆕 shadow_dispatcher.py (Faza 1)
- 🆕 dispatch_pipeline.py (Faza 1)
- 🆕 telegram_approver.py (Faza 1 — D15 enforcement)
- 🆕 gap_fill_restaurant_meta.py (P0.7)
- 🆕 compute_courier_stats.py (P1.1 szybki)
- 🆕 courier_ratings.py (Faza 2 pełny)
- 🆕 telegram_bot.py (Faza 4 bot kurierów)
- 🆕 scheduler.py, forecaster.py, restaurant_onboarding.py

### Stan danych

`/root/.openclaw/workspace/dispatch_state/`:
- ✅ `restaurant_coords.json` — 53 restauracje
- ⚠️ `restaurant_meta.json` — 27/53 (brakuje 26, fix w P0.7)
- ✅ `kurier_piny.json` — 38 kurierów
- ❌ `gps_positions.json` — UNUSABLE (klucze=imiona, dane 3h+ stare, fix w Fazie 5 PWA)

### Dokumentacja (stan na 12.04 wieczór)

- `docs/SKILL.md` — ten plik (V3.1)
- `docs/SYSTEM_FLOW.md` — flow operacyjny
- `docs/TECH_DEBT.md` — backlog + odroczone fixy
- `docs/FAZA_0_SPRINT.md` — aktualny sprint plan z 8 patchami

---

## SEKCJA 3 — DECYZJE ARCHITEKTONICZNE (D1-D16)

Wszystkie zatwierdzone przez Adriana, nie re-negocjujemy.

### D1 — Effective pickup time dla SLA
SLA od efektywnego odbioru. Dla `assigned`: od sim_arrival w restauracji. Dla `picked_up`: od `picked_up_at`.

### D2 — Pełny PDP-TSP
Brute-force permutacji z constraint pickup-before-delivery. Dla bag=4 (hard cap): ~46k permutacji, <100ms. Modeluje on-route bundling od dnia 1.

### D3 — prep_ready_at + prep_variance
Dla spóźnialskich restauracji Ziomek może wysłać kuriera +variance min później (ochrona D8). Restauracja i tak dostaje alert za łamanie SLA (D16 compromise).

### D4 — oldest_in_bag_min = 0 dla assigned
Kurier assigned nie wiezie jeszcze nic → time_penalty = 0. SLA startuje od picked_up. **FIX w P0.2.**

### D5 — JEDNOLITE SLA 35 min do października 2026
Premium tiery (20/15 min) odroczone — nie ma klientów płacących. Pole `sla_minutes` w modelu przygotowane, ale wszędzie = 35.

### D6 — Oceny kurierów 4 wymiary + consistency
Speed/Reliability/Quality/Discipline + Consistency. Tier A/B/C/D (1.05/1.00/0.92/0.75). Faza 2.

### D7 — Grupa Telegram z Ziomkiem
Broadcasty + komendy prywatne + @mention. NIE odpowiada na losowe rozmowy. Faza 4.

### D8 — BEZ WAITU
Kurier zawsze w ruchu. R27 enforcement (pickup window ±5 min). Paliwo w modelu biznesowym.

### D9 — Continuous routing
Sliding window 15 min future orders. Kurier dostaje pipeline, nie single assignment. Faza 9 VRPTW.

### D10 — Opcja 1 dziś → Opcja 2 jutro
Point-in-time PDP-TSP + future planned orders → VRPTW OR-Tools. Migracja in-place.

### D11 — Dyspozycje → Grafik
Środa 20:00 prośba → piątek 12:00 deadline → piątek 18:00 scheduler → piątek 20:00 propozycja → sobota 09:00 publikacja.

### D12 — Overbooking policy
Nadmiar = firma wygrywa (peak dla tier A/B). Niedobór = alert "szukamy pracowników".

### D13 (NOWE 12.04) — Premium SLA odroczone do października 2026
Nie implementujemy dziś martwego kodu. Wszystkie `sla_minutes = 35` jednolicie. Aktywacja gdy będzie first customer.

### D14 (NOWE 12.04) — Faza 0 przed Fazą 1
1.5-2 dni patchów wyłapanych przez 4 audyty (Gemini ×2, DeepSeek ×2). Start Fazy 1 dopiero po Fazie 0 zakończonej i zweryfikowanej.

### D15 (NOWE 12.04) — Shadow Mode = Ziomek imituje koordynatora
Do czasu API Rutcom: Ziomek proponuje w Telegramie → Adrian klika akceptację → **Ziomek loguje się jako koordynator (panel_client) i przypisuje w panelu**. Bez dodatkowej pracy Adriana. Po uzyskaniu API Rutcom: autonomiczny dispatch bez kliknięcia.

**Kluczowe:** Ziomek NIE wysyła "przypisz sam w panelu". Wyręcza całkowicie. Shadow mode = Ziomek wykonuje pracę koordynatora po akceptacji kliknięciem.

### D16 (NOWE 12.04) — Filozofia kontraktowa + ochrona kuriera
Dwa osobne zastosowania `prep_variance`:
1. **Monitoring & alerting:** mierzymy spóźnienia restauracji → alert biznesowy dla Adriana "Mama Thai 3 razy +10min w tygodniu, pogadaj z właścicielem"
2. **Operacyjny bufor kuriera:** dla restauracji z history spóźnień, Ziomek wysyła kuriera +variance min później (ochrona przed staniem D8)

Obie naraz. Restauracja dostaje presję biznesową, kurier nie stoi bezsensownie.

---

## SEKCJA 4 — 29 REGUŁ BIZNESOWYCH

### DISPATCHING (R1-R10)

**R1. Outlier detection — MANDATORY**
Nowy adres >3 km od najbliższego w bagu → odrzuć. **Eliminuje 61% violations.**

**R2. Closest-first in bag — HARD TSP**
Lexicographic cost: `(SLA_violations, total_duration, nearest_first_order)`.

**R3. Dynamic MAX_BAG_SIZE per kurier + warunki**
Bazowo z tieru (P0.1 — centralny config):
- Tier A → max 4
- Tier B → max 3
- Tier C/D → max 2
- Hard cap globalny: 4

Dodatkowo modyfikatory:
- `mutual_distance <2km` → pełny max tieru
- `mutual_distance 2-3km` → -1
- `mutual_distance >3km` → max 2
- Zima (grudzień-luty) → -1
- `natężenie == DUŻE` → -1

**R4. Free stop detector**
`delivery <500m od restauracji LUB kuriera` → `is_free_stop: true`, zero-cost addition.

**R5. On-route pickup bundling (UVP)**
Detour <1.5 km → dorzuć pickup po drodze. Wolt/Bolt tego nie robią.

**R6. Load-based alert**
`active_orders/active_couriers > 3.0 przez >10 min` → Telegram alert Adrianowi.

**R7. Golden hour buffer 18:30-20:00**
Scheduler trzyma 1 kuriera w rezerwie. Dispatcher nie przekracza load 2.5.

**R8. Peripheral city registry + SLA tier 45 min**
Wasilków, Nowodworce, Porosły, Kleosin, Choroszcz, Ignatki, Zaścianki, Krupniki, Grabówka, Klepacze, Horodniany, Karakule, Sobolewo → auto SLA 45 min + sugestia +5 PLN.

**R9. Data quality filter**
Delivery >90 min → `data_error`, excluded z metrics. #460727 Paradiso 421 min to błąd kliknięcia.

**R10. Winter mode**
Grudzień-luty: `winter_multiplier = 1.4x` weekendy, `1.1x` tydzień. MAX_BAG -1. Niedziela 13-20 +2 kurierów.

### KURIERZY (R11-R15)

**R11. MST per kurier**
- Bartek: 3.2 t/h stabilnie, 4.1 breaking (-9pp SLA)
- Mateusz O: 3.6 t/h stabilnie, 4.5 sensowny peak, 5.8 crash (-22pp SLA)
- Tier C/D default: 2.0 t/h
Scheduler i dispatcher NIE przekraczają MST + 15%.

**R12. Tier D blacklist w peak**
SLA <75% przez 5+ dni → automatycznie off-peak (17-22 weekend) wykluczony.

**R13. Consistency score**
`100 - stddev(daily_sla)`. Peak preferuje consistency >95.

**R14. Ramp-up godziny 1**
Pierwsza godzina zmiany: max bag 2. Godzina 2+: R3 standardowe.

**R15. Weekly courier report (Telegram prywatny)**
Niedziela 20:00: "Twoje oceny: speed 4.8, reliability 4.9, tier A, grafik 42h."

### RESTAURACJE (R16-R22)

**R16. Critical partner tracking** (>5% wolumenu)
Rany Julek, Grill Kebab, Chicago Pizza, Rukola Sienkiewicza.

**R17. Per-restaurant SLA baseline**
Spadek >5pp w 7 dni → alert.

**R18. Restaurant meta gap-filling + alerting biznesowy** (D16 compromise)
Skrypt P0.7 generuje draft meta + alerty. NIE bufory do zaniżania predykcji, tylko flagowanie biznesowe.

**R19. Cancellation rate monitoring** (>5% rolling 7 days)
Miejska Miska 12.9% zimą → interweniuj.

**R20. Per-restaurant bag cap**
Kumar's, Mama Thai, Baanko, Eatally → max 2.

**R21. Seasonal degradation tracking** (>10pp vs baseline)
Mama Thai 94% → 75% wymaga reakcji.

**R22. Restaurant onboarding — similarity-based**
Similar_to 3 istniejących → syntetyczny profile → 14-dniowy adaptive learning.

### FINANSOWE (R23-R26)

**R23. Dynamic pricing >15 km lub poza miasto** — +5 PLN sugestia
**R24. Per-restaurant revenue report** — cotygodniowo właścicielom
**R25. Fleet utilization tracking** — target >85% w ruchu
**R26. Weekly ROI report dla Adriana** — poniedziałek 08:00

### NOWE REGUŁY z sesji 12.04 (R27-R29)

**R27. Pickup time window constraint (±5 min)**

Gdy Ziomek rozważa dodanie orderu B do kuriera z orderem A w statusie `assigned`:

1. `sim_arrival_A` — kiedy kurier dotrze do restauracji A
2. `sim_arrival_B` — kiedy dotrze do B (po drodze lub po A)
3. **Twarde ograniczenia:**
   - `sim_arrival_A ≤ pickup_time_A + 5 min` (A nie może się opóźnić przez B)
   - `sim_arrival_B ≤ pickup_time_B + 5 min` (kurier nie stoi długo pod B)
4. **Miękka preferencja:** detour <3 min i B po drodze = super bierzemy
5. **Odrzucenie:** B wymagałby czekania >5 min pod restauracją → NO (D8 enforcement)

**Przykłady Adriana:**
- ✅ "Bartek odbiór A 19:15 centrum, o 19:05 Doner Kebab po drodze chce pickup 19:15-19:17" — detour <2 min, OK
- ❌ "Bartek odbiór A 19:15 centrum, restauracja B chce pickup 19:30 (+25 min)" — Bartek musiałby stać 13 min, NO
- ✅ "Bartek jedzie 10 min do A, Doner Kebab 1 min z drogi, nie po A tylko po drodze, OK"

**Implementacja:** w `feasibility_v2.py` funkcja `check_r27_window(courier, existing_bag, new_order)`.

**R28. Wave continuity preference**

Deadhead scoring uwzględnia fazę cyklu kuriera, nie tylko geograficzną odległość:

1. `deadhead_distance_km` — dystans do restauracji teraz
2. `wave_end_time` — kiedy kurier kończy aktualną falę
3. `wave_end_distance_to_pickup` — jak daleko kończy od restauracji nowego orderu
4. **Score:** kurier kończący blisko > kurier wolny teraz ale daleko

**Scenariusz Adriana:**
- Bartek: kończy falę za 35 min, 500m od Rukoli
- Mateusz: wolny za 20 min, 8 km od Rukoli

Klasyczny deadhead: Mateusz wygrywa (wolny szybciej).
Po R28: **Bartek wygrywa** bo:
- Total czas: Mateusz 20+15=35 min, Bartek 35+2=37 min → podobne
- Puste km: Mateusz 8 km, Bartek 0.5 km → Bartek dużo taniej
- Płynność fal zachowana

**Implementacja:** `S_deadhead = weighted(distance_now × 0.6 + wave_disruption × 0.4)` w scoring.py.

**R29. Best-effort proposal + alert (zamiast ciszy)**

Gdy feasibility zwraca NO dla WSZYSTKICH kurierów:
1. Znajdź kuriera z **najmniejszą karą SLA** (najmniej fatalnej decyzji)
2. Propozycja w Telegramie: ten kurier + explicit warning "SLA violation +X min, lepsze nie ma"
3. Telegram alert do Adriana: "Order Y → Z z violation +X min. Rozważ rezerwowego."
4. Log `impasse_log.jsonl` dla tygodniowego raportu

**Cel:** ZERO wiszących orderów. Każdy order ma propozycję w <5s. Irytacja z opóźnień nie rośnie wykładniczo.

**Implementacja:** w `dispatch_pipeline.py` po iteracji fleet, jeśli `all_infeasible == True` → pick `best_of_worst` + alert.

---

## SEKCJA 5 — NOWA ARCHITEKTURA V3.1

### Struktura modułów

```
/root/.openclaw/workspace/scripts/dispatch_v2/
├── common.py                    # 🔨 P0.1 — centralny MAX_BAG_SIZE per tier
├── event_bus.py                 # ✅ stable
├── state_machine.py             # ✅ stable (SLA 35 hardcoded zamierzone D13)
├── panel_client.py              # ✅ stable + login_and_assign (D15)
├── panel_watcher.py             # 🔨 P0.4 — pickup_coords null fix
├── sla_tracker.py               # ✅ stable
├── osrm_client.py               # 🔨 P0.5 — haversine fallback
├── geocoding.py                 # ✅ stable
├── geometry.py                  # ✅ stable
├── traffic.py                   # ⚠️ recalibrate peak 18-21
├── scoring.py                   # 🔨 P0.2 — time_penalty + R27 + S_deadhead (F1.X1)
├── courier_resolver.py          # 🔨 P0.3 — priority bug fix (PILNE)
│
│ ## DO PRZEPISANIA (Faza 1):
├── route_simulator.py v1        # 🗑️ → v2
├── feasibility.py v1            # 🗑️ → v2
│
│ ## NOWE MODUŁY:
├── route_simulator_v2.py        # 🆕 PDP-TSP + prep_variance pickup time
├── feasibility_v2.py            # 🆕 R1/R3/R8/R20/R27 + time-aware
├── dispatch_pipeline.py         # 🆕 pure function + R29 best-effort
├── shadow_dispatcher.py         # 🆕 runner systemd
├── telegram_approver.py         # 🆕 KLUCZOWE! D15 — Ziomek klika za Adriana
├── gap_fill_restaurant_meta.py  # 🆕 P0.7 — historical + alerty biznesowe
├── compute_courier_stats.py     # 🆕 P1.1 — szybki tier/MST nightly
├── courier_ratings.py           # 🆕 pełny 4-dim (Faza 2)
├── telegram_bot.py              # 🆕 bot kurierów (Faza 4)
├── scheduler.py                 # 🆕 OR-Tools (Faza 6)
├── forecaster.py                # 🆕 Prophet (Faza 6)
├── restaurant_onboarding.py     # 🆕 similarity (Faza 3)
├── natezenie_monitor.py         # 🆕 load alerts (Faza 7)
├── data_quality_filter.py       # 🆕 R9 (Faza 2)
│
└── docs/
    ├── SKILL.md                 # ten plik (V3.1)
    ├── SYSTEM_FLOW.md           # flow end-to-end
    ├── TECH_DEBT.md             # backlog + odroczone
    ├── FAZA_0_SPRINT.md         # aktualny sprint (do archiwizacji po zakończeniu)
    ├── BARTEK_GOLD_STANDARD.md  # 🆕 wzorce benchmark
    └── SCHEDULER_DESIGN.md      # 🆕 OR-Tools config (Faza 6)
```

### State persistent (rozszerzony o V3.1)

```
/root/.openclaw/workspace/dispatch_state/
├── orders_state.json              # stan orderów
├── events.db                      # event bus SQLite
├── geocode_cache.json             # Google cache
├── restaurant_coords.json         # 53 restauracje
├── restaurant_meta.json           # 53/53 po P0.7 (prep_variance + reliable)
├── restaurant_profiles.json 🆕    # onboarding + ramp-up (Faza 3)
├── kurier_piny.json               # 38 kurierów
├── gps_positions.json             # ⚠️ po migracji PWA GPS (Faza 5)
├── courier_ratings.json 🆕        # 4-dim + MST + tier (Faza 2)
├── courier_availability.json 🆕   # dyspozycje (Faza 6)
├── current_schedule.json 🆕       # grafik (Faza 6)
├── shadow_decisions.jsonl 🆕      # decyzje shadow
├── learning_log.jsonl 🆕          # D15 — Twoje akceptacje/overrides + powody
├── impasse_log.jsonl 🆕           # R29 best-effort decisions
├── restaurant_violations.jsonl 🆕 # spóźnienia restauracji (D16 monitoring)
├── complaints.jsonl 🆕            # skargi per kurier
├── natezenie_history.jsonl 🆕     # log co 2 min
├── forecast_errors.jsonl 🆕       # actual vs predicted
├── peripheral_registry.json 🆕    # mapping peryferyjnych adresów
└── historical_orders.parquet 🆕   # data lake ETL nightly
```

### Flow systemu V3.1 (z Telegram feedback loop D15)

```
1. Restauracja tworzy zlecenie w panelu
   └─> Panel: status 2, czas_odbioru_timestamp (Warsaw TZ)

2. panel_watcher wykrywa NEW_ORDER (20s interval)
   ├─> Inline geocoding jeśli pickup_coords null (P0.4 fix)
   ├─> Fallback: centroid Białystok + alert (P0.4)
   └─> Event emit: NEW_ORDER z pickup_coords + alertem

3. shadow_dispatcher konsumuje NEW_ORDER
   └─> dispatch_pipeline(event) → decision_dict

4. dispatch_pipeline dla każdego dispatchable kuriera:
   ├─> courier_resolver (P0.3 fix — aktywny bag > last_delivered)
   ├─> feasibility_v2:
   │   ├─> R1 outlier detection (>3km od najbliższego)
   │   ├─> R3 dynamic MAX_BAG_SIZE (per tier z common.py)
   │   ├─> R8 peripheral SLA tier 45 min
   │   ├─> R20 per-restaurant bag cap
   │   ├─> R27 pickup window ±5 min (NOWE)
   │   ├─> route_simulator_v2 PDP-TSP (z prep_variance)
   │   └─> return (feasible, predicted_violations, best_plan)
   ├─> scoring.py (P0.2 fix):
   │   ├─> S_dystans × 0.25
   │   ├─> S_obciazenie × 0.25
   │   ├─> S_kierunek × 0.20
   │   ├─> S_czas × 0.20 (time_penalty=0 dla assigned, D4 enforcement)
   │   ├─> S_deadhead × 0.10 (NOWY, F1.X1 + R28 wave continuity)
   │   └─> × tier_multiplier (A:1.05 / B:1.00 / C:0.92 / D:0.75)
   ├─> R29: jeśli wszyscy infeasible → pick best_of_worst + alert
   └─> Return best_courier z decision_details

5. shadow_decisions.jsonl → zapis decyzji

6. Telegram propozycja do Adriana (D15):
   ┌─────────────────────────────────────────┐
   │ 🆕 Order 465812 (Rukola → Sienkiewicza) │
   │ Proponuję: Bartek O. (score 4.2)        │
   │ ETA 22 min, w SLA ✅                    │
   │ Alternatywy: Gabriel 3.8, Mateusz 3.5   │
   │                                         │
   │ [✅ TAK Bartek] [🔄 INNY] [⏭ IGNORUJ]   │
   └─────────────────────────────────────────┘

7. Adrian klika → telegram_approver.py:
   ├─> [TAK] → panel_client.login_and_assign(order_id, courier_id)
   │           → learning_log.jsonl: {agreement: true}
   ├─> [INNY] → prosi o wybór + powód →
   │           panel_client.login_and_assign(order_id, chosen_courier_id)
   │           → learning_log.jsonl: {agreement: false, reason: "..."}
   └─> [IGNORUJ] → nic, order wisi do manual

8. panel_watcher wykrywa COURIER_ASSIGNED → state_machine update

9. Kurier odbiera (status 5) → reconcile PICKED_UP → event

10. Kurier dostarcza (status 7) → reconcile DELIVERED → event

11. sla_tracker konsumuje DELIVERED:
    ├─> delivery_time = delivered_at - picked_up_at
    ├─> SLA check (35 min hardcoded D13)
    └─> sla_log.jsonl

12. Restaurant violation tracking (D16):
    ├─> Sprawdź czy `picked_up_at - pickup_time > 0` (restauracja spóźniła)
    └─> restaurant_violations.jsonl dla cotygodniowego raportu

13. Nightly jobs (02:00):
    ├─> data_quality_filter R9 (>90 min exclude)
    ├─> compute_courier_stats.py (P1.1 szybki) → courier_ratings.json
    ├─> ETL do historical_orders.parquet
    ├─> forecaster retrain (niedziele)
    └─> Weekly report dla Adriana (poniedziałek 08:00)
```

---

## SEKCJA 6 — ROADMAPA WDROŻENIA V3.1 (CHECKLIST)

### FAZA 0 — Patche pre-shadow (1.5-2 dni, PRZED Fazą 1)

**Quick wins (łącznie ~1h):**

```
[ ] P0.1 — Dynamic MAX_BAG_SIZE per kurier tier
    Plik: common.py
    Zmiana: dodać TIER_BAG_LIMITS = {"A": 4, "B": 3, "C": 2, "D": 2}, GLOBAL_HARD_CAP = 4
    Update feasibility + scoring żeby importowały z common
    Test: grep MAX_BAG_SIZE → 1 definicja, 2 importy
    Estymacja: 1h (więcej niż pierwotne 5 min bo per-tier, nie stała)

[ ] P0.2 — Fix time_penalty assigned + R27 window
    Plik: scoring.py + feasibility_v2.py (nowy)
    Zmiana A (scoring): parametr bag_statuses w score_candidate, 
                        oldest_in_bag_min liczony tylko dla picked_up
    Zmiana B (feasibility): check_r27_window(courier, bag, new_order):
                           - sim_arrival_B ≤ pickup_time_B + 5 min
                           - sim_arrival_A ≤ pickup_time_A + 5 min
                           - jeśli B wymaga wait >5 min → NO
    Test: scenariusz Bartek assigned A 19:15 + nowy B pickup 19:40 → R27 odrzuca
    Estymacja: 30 min

[ ] P0.3 — Fix courier_resolver priority (PILNE)
    Plik: courier_resolver.py
    Zmiana: w build_fleet_snapshot kolejność fallback:
            (1) GPS fresh <5min
            (2) pozycja aktywnego bag (picked_up delivery lub assigned pickup)
            (3) last_delivered
            (4) last_picked_up
            (5) fallback
    Test: kurier z aktywnym bag ma pos_source: last_picked_up_delivery
    Estymacja: 20 min

[ ] P0.4 — Pickup_coords null fix z centroid fallback
    Plik: panel_watcher.py
    Zmiana: przed emit NEW_ORDER:
            if not pickup_coords:
                pickup_coords = get_coords_from_cache(address_id)
                if not pickup_coords:
                    pickup_coords = geocode_inline(restaurant_name)  # 1-2s
                if not pickup_coords:
                    pickup_coords = BIALYSTOK_CENTROID  # 53.13, 23.16
                    emit_alert_telegram("Order X brak coords, użyto fallback")
    Test: przez 1h produkcji zero NEW_ORDER z pickup_coords:null
    Estymacja: 25 min
```

**Core fixes (łącznie ~2h):**

```
[ ] P0.5 — OSRM haversine fallback
    Plik: osrm_client.py
    Zmiana: w table() try/except z timeout=3s, fallback:
            distance = haversine(p1, p2) * 1.4
            duration = distance / 25.0 * 3600  # 25 km/h average
            return {..., "fallback_used": True}
    Test: mock OSRM timeout → table() zwraca fallback + flaga
    Estymacja: 40 min

[ ] P0.6 — Weryfikacja prep_ready_at w panelu
    Task: SSH recon, wywołać fetch_order_details na 3 orderach
          (1 zwykły, 1 czasówka 60+, 1 peryferyjny)
    Pytanie: czy panel zwraca czas_odbioru_timestamp dla wszystkich,
             czy czasem tylko minutes (czas_odbioru)?
    Wynik: dokumentacja w TECH_DEBT + decyzja implementacyjna
    Estymacja: 30 min
```

**Historical analysis (~5h):**

```
[ ] P0.7 — gap_fill_restaurant_meta.py (D16 filozofia kompromis)
    Plik: nowy gap_fill_restaurant_meta.py
    Input: orders_state.json + sla_log.jsonl + ew. CSV historyczne
    Algorytm:
      Per restauracja z ostatnich 4 tygodni:
      1. avg_prep_time = mean(picked_up_at - created_at)
      2. declared_prep = mean(czas_odbioru deklarowany)
      3. prep_variance = max(0, avg_prep_time - declared_prep)
      4. reliable = (sla_compliance >= 92%)
      5. parking = default 2 (manual override potem)
      6. **Alert jeśli prep_variance > 5 min dla >30% orderów** → D16 monitoring
    Output:
      - Draft restaurant_meta.json (52 rekordy)
      - restaurant_violations_report.md (lista spóźnialskich z procentami)
    Test: Rukola → variance ≤ 5, reliable=true. Baanko → variance ≥ 8, reliable=false.
    Estymacja: 4-6h

[ ] P0.8 — Meta w route_simulator_v2 (inlined do Fazy 1)
    Plik: route_simulator_v2.py (nowy, w Fazie 1)
    Zmiana: pickup_service_time = 2 + get_meta(restaurant).prep_variance
            # Dla spóźnialskich: Bartek przyjeżdża +variance min później = nie czeka
            # Restauracja i tak dostaje alert za łamanie SLA (D16)
    Test: Mama Thai variance 8 → pickup_service_time = 10 min
    Estymacja: 0h dodatkowe (wchodzi z Fazą 1)
```

**Exit criteria Fazy 0:**
```
[ ] Wszystkie P0.1-P0.7 ukończone z testem PASS
[ ] docs/TECH_DEBT.md zaktualizowany
[ ] systemctl is-active wszystkie serwisy przez 4h+
[ ] Zero errors w logach przez 2h po ostatnim patchu
[ ] 0 NEW_ORDER z pickup_coords:null przez 1h
[ ] restaurant_meta.json ma 53 wpisy (po P0.7 akceptacji)
[ ] restaurant_violations_report.md wygenerowany i przeczytany przez Adriana
```

---

### FAZA 1 — Dispatcher core z Telegram loop (dni 1-3 po Fazie 0)

```
[ ] F1.1  route_simulator_v2.py — PDP-TSP brute-force (280-320 linii)
          - Multi-pickup support (wiele restauracji w bagu)
          - prep_ready_at + prep_variance w pickup service time
          - Test: 10 scenariuszy realnych
          
[ ] F1.2  feasibility_v2.py z modem normal/urgent/balanced/luxury
          - R1 outlier (>3km)
          - R3 dynamic MAX_BAG per tier
          - R8 peripheral SLA 45
          - R20 per-restaurant cap
          - R27 pickup window ±5 min
          
[ ] F1.3  scoring.py — refactor
          - 5 komponentów: dystans, obciążenie, kierunek, czas, deadhead
          - Wagi: 0.25/0.25/0.20/0.20/0.10
          - S_deadhead z R28 wave continuity
          - tier_multiplier (A:1.05/B:1.00/C:0.92/D:0.75)
          
[ ] F1.4  dispatch_pipeline.py — pure function
          - R29 best-effort (nigdy cisza)
          - impasse_log.jsonl dla best_of_worst
          
[ ] F1.5  shadow_dispatcher.py — systemd runner
          - Konsumpcja NEW_ORDER z event_bus
          - Wywołanie dispatch_pipeline
          - Zapis shadow_decisions.jsonl
          - Emit propozycji do telegram_approver
          
[ ] F1.6  telegram_approver.py — D15 KLUCZOWE
          - Handle /start, buttony inline
          - Po [TAK]: panel_client.login_and_assign(order, courier)
          - Po [INNY]: prompt wybór + powód
          - Zapis learning_log.jsonl
          - Error handling: jeśli panel login padnie → alert
          
[ ] F1.7  panel_client.py — dodać login_and_assign
          - Login jako Adrian (panel.env credentials)
          - POST na endpoint przypisania (TBD po recon Rutcom)
          - Jeśli endpoint nieznany → user-friendly error + Telegram
          
[ ] F1.8  Deploy shadow_dispatcher + telegram_approver
          - Systemd units
          - Monitor 24h (no panel writes do czasu pewności że login_and_assign działa)
          - Diff report: shadow_decisions vs learning_log (agreement rate)
          
[ ] F1.9  Tydzień shadow + diff report
          - Agreement rate >60% po 3 dniach = good
          - Agreement rate <50% po 3 dniach = problem scoringu, debug
```

---

### FAZA 2 — Data quality + ratings (dni 4-5)

```
[ ] F2.1  data_quality_filter.py — R9
[ ] F2.2  compute_courier_stats.py — szybki (P1.1)
          - Nightly skrypt agregujący delivered + sla_log
          - Output: tier, MST, consistency per kurier
          - courier_ratings.json basic
[ ] F2.3  courier_ratings.py — pełny 4-dim
          - Speed, Reliability, Quality, Discipline
          - Consistency bonus
          - Complaints integration (placeholder)
[ ] F2.4  Nightly job systemd 02:00
[ ] F2.5  Integration scoring z tier multiplier (już w Fazie 1 placeholder)
[ ] F2.6  R12 blacklist tier D w peak enforcement
```

---

### FAZA 3 — Restaurant intelligence (dni 6-7)

```
[ ] F3.1  restaurant_meta.json po akceptacji Adriana z P0.7
[ ] F3.2  R21 baseline SLA per restauracja (14-dniowa historia)
[ ] F3.3  restaurant_onboarding.py — schema profiles
[ ] F3.4  R22 similarity-based forecasting
[ ] F3.5  Ramp-up curve 14 dni adaptive
[ ] F3.6  Monitoring R17/R19/R21 alerts
[ ] F3.7  Critical partner tracking R16 (Rany Julek/Grill Kebab/Chicago/Rukola)
[ ] F3.8  Weekly restaurant_violations_report (D16) do Adriana
```

---

### FAZA 4 — Telegram bot kurierów (dni 8-10)

```
[ ] F4.1  Grupa "NadajeSz — Floty" + bot admin
[ ] F4.2  telegram_bot.py — mapping courier_id ↔ telegram_user (/start <pin>)
[ ] F4.3  Broadcast: alerty SLA @mention kuriera
[ ] F4.4  Broadcast: natężenie change (hysteresis 5 min)
[ ] F4.5  Komenda /ile, /pobranie, /mojgrafik, /mojaocena, /koniec, /problem
[ ] F4.6  R15 niedzielne weekly reports prywatnie
[ ] F4.7  R7 Golden hour alert 18:30-20:00
```

---

### FAZA 5 — GPS via PWA (dni 11-13)

```
[ ] F5.1  gps.nadajesz.pl + Nginx + Let's Encrypt
[ ] F5.2  Frontend HTML+JS: Geolocation API, polling 30s, PIN
[ ] F5.3  PWA manifest (add to homescreen)
[ ] F5.4  Backend FastAPI POST /gps
[ ] F5.5  gps_positions.json migracja klucze=courier_id
[ ] F5.6  courier_resolver integration: priority GPS fresh <5 min
[ ] F5.7  Instrukcja dla kurierów + screenshoty
[ ] F5.8  Pilot 2 kurierów 48h
[ ] F5.9  Rollout cała flota + deprecate Traccar
```

---

### FAZA 6 — Scheduler + dyspozycje (dni 14-17)

```
[ ] F6.1  Data lake ETL historical_orders.parquet
[ ] F6.2  Enrichment: pogoda, kalendarz PL, days_since_payday
[ ] F6.3  forecaster.py — Prophet baseline
[ ] F6.4  forecast_errors.jsonl kalibracja
[ ] F6.5  scheduler.py — OR-Tools constraint programming
[ ] F6.6  Constraints: prawo pracy, MST, tier, fair distribution
[ ] F6.7  Objective: minimize undercoverage*10 + overcoverage
[ ] F6.8  Telegram bot: środa 20:00 /dyspo
[ ] F6.9  Parser dyspozycji
[ ] F6.10 Piątek 18:00 cron scheduler generuje
[ ] F6.11 Piątek 20:00 propozycja do Adriana /zatwierdz /modyfikuj
[ ] F6.12 Sobota 09:00 publikacja do kurierów
[ ] F6.13 R6 load-based alert
```

---

### FAZA 7 — Natężenie + pricing (dni 18-19)

```
[ ] F7.1  natezenie_monitor.py — co 2 min + hysteresis 5 min
[ ] F7.2  Auto-ustawianie natężenia w panelu (endpoint TBD)
[ ] F7.3  R23 dynamic pricing sugestie >15km/poza miasto
[ ] F7.4  R24 per-restaurant revenue report (niedziela nocą)
[ ] F7.5  R25 fleet utilization tracking
[ ] F7.6  R26 weekly ROI report poniedziałek 08:00
```

---

### FAZA 8 — Shadow → Live migration (dni 20-21)

```
[ ] F8.1  Po 2 tygodniach shadow + learning_log analiza
[ ] F8.2  Jeśli agreement >85% → włączamy auto-approve dla czasówek
[ ] F8.3  Monitoring 48h agreement rate przy auto-approve
[ ] F8.4  Gradual rollout per kategoria (czasówki → zwykłe → peripheral)
[ ] F8.5  R12 blacklist tier D enforcement w live
[ ] F8.6  Fallback manual <30s timeout
[ ] F8.7  Monitoring dashboard
```

---

### FAZA 9 — VRPTW migration (dni 22-25)

```
[ ] F9.1  OR-Tools Routing Library integration
[ ] F9.2  Sliding window 15 min future orders
[ ] F9.3  Re-optymalizacja co 30-60s
[ ] F9.4  A/B test point-in-time vs VRPTW (1 tydzień)
[ ] F9.5  Migration in-place jeśli VRPTW lepszy
[ ] F9.6  Monitoring stale plans locked vs dynamic
```

---

### FAZA 10 — Restimo API (dni 26-35, nowy kanał przychodów)

```
[ ] F10.1 FastAPI setup + OAuth2
[ ] F10.2 POST /v1/quote
[ ] F10.3 POST /v1/orders → event_bus NEW_ORDER
[ ] F10.4 GET /v1/orders/{id}
[ ] F10.5 POST /v1/orders/{id}/cancel
[ ] F10.6 Webhooks (Celery + Redis)
[ ] F10.7 OpenAPI/Swagger
[ ] F10.8 Pilot Restimo 1 restauracja
[ ] F10.9 Rollout reszta aggregatorów
```

---

### FAZA 11 — Warszawa + wielomiastowość (miesiąc 2-3)

```
[ ] F11.1 Sharding state per miasto
[ ] F11.2 Config per miasto (traffic, SLA, MAX_BAG, okna fali)
[ ] F11.3 Wspólna baza kurierów multi-city
[ ] F11.4 Rekrutacja Warszawa + pierwsze restauracje
[ ] F11.5 Shadow Warszawa 1 tydzień → live
[ ] F11.6 Dashboard cross-city
```

---

### FAZA 12 — Dashboard koordynatora (miesiąc 3)

```
[ ] F12.1 React/Vue SPA + FastAPI backend
[ ] F12.2 Live mapa floty (GPS + bag vis)
[ ] F12.3 Live shadow decisions UX
[ ] F12.4 Statystyki + trends
[ ] F12.5 Real-time alerts
[ ] F12.6 Kontrola: start/stop, natężenie, shadow↔live
[ ] F12.7 Debug per order (historia + reasoning)
```

---

## SEKCJA 7 — PRZYKŁADY KANONICZNE (Bartek Gold Standard)

Test acceptance dla Ziomka. Jeśli Ziomek w tych sytuacjach daje inną decyzję niż Bartek — Ziomek jest zły.

### Przykład A — 5-pack Rukola (21:18, 10.04)

**Sytuacja:** Bartek, 5 orderów Rukola Sienkiewicza, pickup 21:18. Adresy: Kraszewskiego, Lewandowskiego, Waryńskiego, Leszczynowa, KEN 36b.

**Bartek zrobił:** Closest-first, wszystkie w SLA.

**Ziomek V3.1 powinien:** R3 dla tier A = max 4. Zwróci 4-pack dla Bartka + 1-pack dla innego kuriera. **UWAGA:** to różni się od Bartka w realu, ale jest bezpieczniej przy skali.

Albo: jeśli mutual_distance <2km dla wszystkich 5 → R3 pełny tier + override do 5 dla tier A z log warning "edge case".

### Przykład B — 3-pack Mama Thai (16:16, 10.04)

**Sytuacja:** Mama Thai, Depowa + Armii Krajowej + Antoniukowska.

**Bartek zrobił:** Wszystkie w SLA mimo unreliable restauracji.

**Ziomek V3.1 powinien:** R20 Mama Thai max 2. ALE jeśli bliskie adresy + prep_variance uwzględniony → override do 3 z log warning. Edge case dla tier A kuriera.

### Przykład C — Free stop HoNoTu (20:53→20:54)

**Bartek zrobił:** 1 min delivery, budynek obok.

**Ziomek:** R4 is_free_stop → zero-cost addition.

### Anty-przykład D — Gabriel Chinatown Pozioma (50 min)

**Gabriel zrobił:** 42 Pułku pierwsze (bliżej), Pozioma drugie = 50 min violation.

**Ziomek:** R1 outlier (Pozioma ~3km) → odrzuca bundle. ALBO R2 reverse (dalsze pierwsze) → Pozioma przed 42 Pułku → oba w SLA.

### Anty-przykład E — Mateusz O 5-pack Grill Kebab (2 violations)

**Mateusz zrobił:** 5-pack, 3 kierunki, 2 violations.

**Ziomek:** R3 dla tier A = max 4, R1 outlier → dzieli na 2 kurierów (3+2 centralne+peryferia).

### NOWY Przykład F (po R27) — Pickup window test

**Sytuacja:** Bartek ma A assigned, Rukola pickup 19:15. Doner Kebab nowy order, pickup 19:20, 1 km od Rukoli.

**Ziomek V3.1:** R27 check. sim_arrival_A = 19:15 (on time). sim_arrival_B = 19:17 (po drodze). Oba <= pickup + 5 min → OK, bundle.

**Sytuacja 2:** Bartek ma A 19:15. Baanko pickup 19:40 (+25 min). Zmuszenie Bartka by czekał 13 min = NO.

### NOWY Przykład G (po R28) — Wave continuity

**Sytuacja:** Nowy order Rukola. Bartek kończy falę za 35 min, 500m od Rukoli. Mateusz wolny za 20 min, 8km dalej.

**Ziomek V3.1:** R28 scoring. Total czas: Bartek 37 min, Mateusz 35 min → podobne. Puste km: Bartek 0.5, Mateusz 8 → Bartek dużo taniej. **Bartek wygrywa.** Płynność fal zachowana.

### NOWY Przykład H (po R29) — Best-effort

**Sytuacja:** Order Kumar's → Pogodna (peryferyjny) o niedzielnym peak 19:00. Wszyscy kurierzy pełne bagi, nikt feasible.

**Ziomek V3.1:** R29. Pick kurier z najmniejszą karą SLA (np. "Bartek +8 min violation"). Wysyła propozycję + Telegram alert "Order X → Bartek z violation +8 min, lepsze nie ma, rozważ rezerwowego". Nigdy nie wisi.

---

## SEKCJA 8 — SUCCESS METRICS (KPI)

### Metryki techniczne (codziennie)
- Uptime panel_watcher/sla_tracker/shadow/approver: >99.5%
- Event bus lag: <30s
- Shadow decision latency: <5s
- Shadow coverage: >95% NEW_ORDER
- **Zero NEW_ORDER z pickup_coords:null (po P0.4)**
- **Zero wiszących orderów po R29**
- **Zero "login panel failed" w learning_log (D15 enforcement)**

### Metryki operacyjne (tygodniowe)
- SLA floty: >93% wiosną, >88% zimą
- Violations: <7% wiosną, <12% zimą
- Avg delivery: <20 min main, <30 min peripheral
- Peak 17-22 throughput: >4.0 t/h
- Cancellation: <2%
- **Agreement rate Ziomek vs Adrian: >70% po tyg 1, >85% po tyg 2**
- **Restaurant violations (D16): weekly report dla Adriana**

### Metryki biznesowe (miesięczne)
- Revenue growth: +10% MoM Q2 2026
- GMV growth: proporcjonalny
- Restaurant retention: 100% critical partners
- Courier retention: >90% tier A+B 3+ mies
- ROI Ziomka: measurable w weekly reports

### Metryki shadow vs live
- Agreement rate: target >70% tyg 1, >85% tyg 2
- Ziomek-better cases: Ziomek proponował lepiej (historical SLA comparison)
- Adrian-better cases: Adrian lepszy (learning signals do scoringu)
- Unacceptable errors: 0% — Ziomek nigdy nie proponuje absurdu

---

## SEKCJA 9 — OSTRZEŻENIA I HARD RULES

**NIGDY:**
- Nie łam produkcji bez `cp .bak-YYYYMMDD-HHMMSS` + test
- Nie restartuj systemd bez py_compile + import check + dry-run
- Nie reintroduce chromedp — Python HTTP zostaje
- **Nie każ kurierowi czekać (D8)** — R27 enforcement ±5 min
- Nie licz SLA od `now` dla assigned (D1)
- Nie zakładaj picked_up_at ≠ None (fallback)
- Nie używaj `jq` (brak w systemie) — JSON przez Python
- Nie dodawaj `tools.telegram`/`tools.exec.approval` do openclaw.json (crash)
- Nie zapominaj Warsaw TZ dla panel timestamps
- **Nie hardcoduj MAX_BAG_SIZE** — centralny config w common.py (R3 dynamic)
- **Nie zawieszaj orderów bez propozycji** — R29 best-effort zawsze
- **Nie implementuj premium SLA dziś** (D13 odroczone)
- **Nie zaniżaj ETA dla restauracji "bo się spóźniają"** — D16: alerty biznesowe + ochrona kuriera (bufor kuriera), NIE zaniżanie predykcji restauracji
- **Nie wysyłaj Adrianowi "kliknij sam w panelu"** — D15: Ziomek loguje się i przypisuje sam po akceptacji w Telegramie

**ZAWSZE:**
- Czytaj SKILL.md + SYSTEM_FLOW + TECH_DEBT + FAZA_0_SPRINT na początku sesji
- Weryfikuj realny kod/dane przed patchem
- Atomic writes: temp → fsync → rename
- Warsaw TZ: `from zoneinfo import ZoneInfo; WARSAW = ZoneInfo("Europe/Warsaw")`
- Mock environment z tmpfs state dla testów
- Update TECH_DEBT.md na koniec sesji
- Data quality filter R9 przed każdym scoringu
- **R27 check przed każdym bundle decision**
- **R29 best-effort gdy feasibility all NO**
- **D15 enforcement: telegram_approver wykonuje przypisanie, nie prosi Adriana**
- **D16 dual use prep_variance: alerty + bufor kuriera**

---

## SEKCJA 10 — START KAŻDEJ NOWEJ SESJI (RYTUAŁ)

```bash
# 1. Source of truth (4 pliki docs)
cat /root/.openclaw/workspace/scripts/dispatch_v2/docs/SKILL.md
cat /root/.openclaw/workspace/scripts/dispatch_v2/docs/SYSTEM_FLOW.md
cat /root/.openclaw/workspace/scripts/dispatch_v2/docs/TECH_DEBT.md
cat /root/.openclaw/workspace/scripts/dispatch_v2/docs/FAZA_0_SPRINT.md 2>/dev/null || echo "no active sprint"

# 2. Production sanity check
systemctl is-active dispatch-panel-watcher dispatch-sla-tracker
systemctl is-active dispatch-shadow dispatch-telegram-approver 2>/dev/null || echo "phase 1 not started"
tail -3 /root/.openclaw/workspace/scripts/logs/watcher.log
tail -3 /root/.openclaw/workspace/scripts/logs/sla_tracker.log

# 3. State overview
python3 -c "
import sys; sys.path.insert(0, '/root/.openclaw/workspace/scripts')
from dispatch_v2 import state_machine
from collections import Counter
all_o = state_machine.get_all()
c = Counter(o.get('status','?') for o in all_o.values())
pu = sum(1 for o in all_o.values() if o.get('picked_up_at'))
print(f'State: {dict(c)}, with picked_up_at: {pu}')
"

# 4. Fleet
python3 -c "
import sys; sys.path.insert(0, '/root/.openclaw/workspace/scripts')
from dispatch_v2 import courier_resolver
fleet = courier_resolver.build_fleet_snapshot()
disp = courier_resolver.dispatchable_fleet(fleet)
print(f'Fleet: {len(fleet)}, dispatchable: {len(disp)}')
"

# 5. Shadow + learning log
tail -5 /root/.openclaw/workspace/dispatch_state/shadow_decisions.jsonl 2>/dev/null
tail -5 /root/.openclaw/workspace/dispatch_state/learning_log.jsonl 2>/dev/null

# 6. Agreement rate (po Fazie 1)
python3 << 'EOF' 2>/dev/null
import json
try:
    logs = [json.loads(l) for l in open('/root/.openclaw/workspace/dispatch_state/learning_log.jsonl')]
    agree = sum(1 for l in logs if l.get('agreement', False))
    print(f'Agreement: {agree}/{len(logs)} = {100*agree/len(logs):.1f}%' if logs else 'no data yet')
except Exception as e:
    print(f'learning_log: {e}')
EOF

# 7. Ratings top 5
python3 << 'EOF' 2>/dev/null
import json
try:
    r = json.load(open('/root/.openclaw/workspace/dispatch_state/courier_ratings.json'))
    for k, v in sorted(r.items(), key=lambda x: -x[1].get('rating_overall',0))[:5]:
        print(f'{v.get("name","?"):20s} tier {v.get("tier","?")} rating {v.get("rating_overall",0):.1f}')
except Exception as e:
    print(f'ratings: {e}')
EOF

# 8. Pytaj Adriana co priorytet dnia
```

---

## SEKCJA 11 — CO POMIJAMY / BACKLOG

- **Dynamic buttons per-restauracja** → FAZA 13+
- **LSTM/Deep learning forecast** → reevaluate po 6 mies (Prophet wystarczy)
- **Predictive positioning** → SKIP (Adrian: kurierzy zawsze jeżdżą)
- **Native driver app** → reevaluate po 6 mies (PWA wystarczy)
- **ML anomaly detection** → reevaluate po Q2
- **Auto-scheduling z preference learning** → FAZA 15+
- **MAX_PICKUP_REACH_KM optimization** → odroczone 2 miesiące, pasywna obserwacja
- **Dynamic timezone per miasto** → odroczone 12+ mies (ekspansja poza Polskę)
- **Premium SLA tiery 20/15 min** → odroczone do października 2026

---

## SEKCJA 12 — TELEFON I KONTAKT

Telegram Adrian: `8765130486` — pisz gdy:
- Production down (watcher/tracker/shadow/approver padł)
- Ziomek zaproponował absurd (guaranteed violation)
- Data quality alarm (>10 orderów >90 min w dniu)
- Critical partner SLA spadł >10pp w 3 dni
- Fleet utilization <60% przez >4h
- **Agreement rate <60% przez 3 dni** (problem scoringu)
- **telegram_approver nie może się zalogować do panelu** (D15 broken)

---

## KONIEC SKILL.md V3.1

**Wersjonowanie:**
- V1 (11.04 rano): dispatch_v2 fundament
- V2 (11.04 wieczór): shadow dispatcher plan
- V3 (12.04 rano): architektura po analizie 2000+ orderów + 26 reguł
- **V3.1 (12.04 wieczór): +4 audyty zewnętrzne + 3 nowe reguły (R27-R29) + 4 nowe decyzje (D13-D16) + Faza 0**

**Zmiany V3 → V3.1:**
- +R27 Pickup time window constraint
- +R28 Wave continuity preference
- +R29 Best-effort proposal
- +D13 Premium SLA odroczone
- +D14 Faza 0 przed Fazą 1
- +D15 Shadow Mode = Ziomek imituje koordynatora
- +D16 Filozofia kontraktowa + ochrona kuriera
- +Faza 0 (8 patchów wyłapanych przez audyty)
- +telegram_approver.py jako kluczowy moduł Fazy 1
- +learning_log.jsonl dla uczenia z akceptacji Adriana
- +restaurant_violations.jsonl dla D16 monitoring

**Następna sesja (13.04 lub później):**
1. Czytasz SKILL + SYSTEM_FLOW + TECH_DEBT + FAZA_0_SPRINT
2. Sanity check produkcji
3. Start Fazy 0 od P0.1 (unifikacja MAX_BAG_SIZE per tier)
4. Przechodzisz kolejno P0.2-P0.7
5. Exit criteria Fazy 0 spełnione → start Fazy 1

**Git po zakończeniu Fazy 0:**
```bash
cd /root/.openclaw/workspace/scripts/dispatch_v2
git add docs/SKILL.md docs/TECH_DEBT.md
git commit -m "SKILL v3.1: Faza 0 complete, ready for Faza 1"
```
