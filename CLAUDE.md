# ZIOMEK V3.3 — MASTER BRIEF (dla Claude Code, 13.04.2026)

**Ten plik czytasz JAKO PIERWSZE na początku każdej sesji.**

## Kontekst biznesowy (pamięć operacyjna)

Adrian Czapla, NadajeSz Białystok (ekspansja Warszawa Q3 2026), 30 kurierów, 55 restauracji, 1500-2000 orderów/tydzień, revenue transport 35-45k PLN/tydz + GMV cash 70-90k PLN/tydz (pobrania). Roczna skala 1.87M PLN transport, 3.6M PLN GMV.

**Faza 0 DONE 12.04.2026** — 8/8 patches, 9 commitów. Szczegóły commitów niżej w sekcji "Git history".

### Kluczowa informacja biznesowa (13.04 update)

**Big 4 jeden właściciel** — Chicago Pizza, Grill Kebab, Raj, Sweet Fit & Eat:
- Start: maj 2025
- Marzec 2026: 1489 orderów (Chicago 387, Grill 654, Raj 325, Sweet 123)
- Przychód marzec 2026: 31,623 zł transport + 74,324 zł GMV cash
- **~20-22% wolumenu Nadajesz od jednego decision-makera** = concentration risk
- Wszystkie 4 używają Symplex Bistro (POS)

**Analiza spadku marzec 2026:**
- YoY marzec 2026 lepszy niż 2025 w 5/7 dni tygodnia (+11% ogólnie)
- Poniedziałki -17% YoY (konkretny sygnał do zgłębienia)
- Sezonowość luty→marzec: -30% w 2026 vs -10% naturalna (anomalia)
- Spadek pochodzi z 64 pozostałych restauracji, nie Big 4
- "Globalny trend rynkowy" potwierdzony przez Adriana rozmowami z konkurencją

### Priorytety tygodni 1-4 (zaakceptowane 13.04)

1. **CC acceleration** (fundament)
2. **Ziomek shadow → auto-approve** (odzyskanie 4-6h/dzień Adrianowi)
3. **API Nadajesz** (FastAPI, OAuth2 — nowy kanał przychodów)
4. **ROI boosters** (dynamic pricing, restaurant monitoring)
5. **Hardening** (żeby Ziomek nie padł w peak)

**Odsunięte:** POS integration, Faza 2 (ratings), Faza 6 (scheduler).

## Runtime i infrastruktura

| Element | Wartość |
|---|---|
| Serwer | Hetzner CPX22, Ubuntu 24.04 |
| IP | 178.104.104.138 |
| Timezone serwera | UTC |
| Docker | openclaw-openclaw-gateway-1 (OpenClaw 2026.3.27) |
| Model AI | openai/gpt-5.4-mini primary, DeepSeek fallback |
| Panel | gastro.nadajesz.pl/admin2017/new/orders/zlecenia |
| Telegram dispatch | @NadajeszBot |
| Telegram sterowanie | @GastroBot |
| Telegram admin ID | 8765130486 |
| Claude Code | v2.1.104, Opus 4.6 1M, Claude Max, tmux 'backup' |

## Struktura plików

### Kod produkcyjny: /root/.openclaw/workspace/scripts/dispatch_v2/

| Plik | Status | Rola |
|---|---|---|
| common.py | ✅ P0.1+P0.3+P0.5 | config + WARSAW + parse_panel_timestamp + FALLBACK_BASE_SPEEDS_KMH + HAVERSINE_ROAD_FACTOR_BIALYSTOK + get_time_bucket |
| event_bus.py | ✅ | SQLite idempotent |
| state_machine.py | ✅ P0.2a+P0.3+P0.4 | 6 commitment levels, delivery_coords w NEW_ORDER upsert |
| panel_client.py | ✅ | login HTTP Laravel |
| panel_watcher.py | ✅ P0.4 | inline geocoding delivery_address |
| sla_tracker.py | ✅ | 10s konsumer SLA |
| osrm_client.py | ✅ P0.5 | route/table z fallbackiem, circuit breaker, hourly metrics |
| geocoding.py | ✅ P0.4 | Google + cache, timeout parametryzowany |
| scoring.py | ✅ P0.1 | 4 komponenty, linear decay |
| courier_resolver.py | ✅ P0.3 | priority fix aktywny bag > last_delivered |
| route_simulator.py v1 | 🗑️ | Do przepisania v2 w Fazie 1 |
| feasibility.py v1 | 🗑️ | Do przepisania v2 w Fazie 1 |

### Nowe moduły Fazy 1 (do utworzenia tydz 1):
- route_simulator_v2.py (~300 linii) — PDP-TSP + prep_variance
- feasibility_v2.py (~200 linii) — R1/R3/R8/R20/R27/D8
- dispatch_pipeline.py (~250 linii) — scoring + R28 + R29
- shadow_dispatcher.py (~350 linii) — systemd runner
- telegram_approver.py (~250 linii) — Telegram listen + learning_log

### State: /root/.openclaw/workspace/dispatch_state/

| Plik | Zawartość |
|---|---|
| orders_state.json | Stan orderów |
| events.db | Event bus SQLite |
| geocode_cache.json | Google cache (90% hit, 294 entries) |
| restaurant_coords.json | 53 restauracje |
| restaurant_meta.json | **68 restauracji, 113 KB** z P0.7 |
| shadow_decisions.jsonl | 🆕 Faza 1 |
| learning_log.jsonl | 🆕 Faza 1 |
| impasse_log.jsonl | 🆕 Faza 1 |

### Offline tools (POZA git repo): /root/.openclaw/workspace/scripts/tools/
- calibrate_road_factor.py (P0.5 baseline)
- gap_fill_restaurant_meta.py (P0.7, 595 linii, regen meta)

### Docs: /root/.openclaw/workspace/scripts/dispatch_v2/docs/
- CLAUDE.md — TEN plik, master brief
- CLAUDE_WORKFLOW.md — 🆕 agent behavior spec (DO UTWORZENIA 13.04)
- TECH_DEBT.md — backlog + bug notes per patch
- FAZA_0_SPRINT.md — historia 8 patchów
- SYSTEM_FLOW.md — end-to-end flow
- DEMAND_ANALYSIS.md — 121k dowozów, hipotezy Fazy 6

### Backups: /root/backups/
- dispatch_v2_POST_FAZA_0_20260412-194750.tar.gz (450K)
- dispatch_state_POST_FAZA_0_20260412-194750.tar.gz (301K)
- tools_POST_FAZA_0_20260412-194750.tar.gz (26K)
- dispatch_v2_PRE_CLAUDE_CODE_20260412-105004.tar.gz

### Archive: /root/archive/p07_source/
- 9 CSV zestawienie_panel 52-60 + 1 merged (32 MB, ~24007 delivered orderów)

### Sekrety: /root/.openclaw/workspace/.secrets/
- panel.env, gmaps.env, traccar.env

### Systemd
- dispatch-panel-watcher.service — 20s ACTIVE ✅
- dispatch-sla-tracker.service — 10s ACTIVE ✅
- dispatch-shadow.service — 🆕 Faza 1 (tydz 1)
- dispatch-telegram-approver.service — 🆕 Faza 1 (tydz 1)

## Git history (9 commitów)

```
57a5d34 P0.7: gap_fill_restaurant_meta.py + restaurant_meta.json (68 rest)
bfd1dfc docs: add DEMAND_ANALYSIS.md
12285ef P0.6: RECON panel API (prep_ready_at nie istnieje)
15493ea P0.5: OSRM haversine fallback + circuit breaker
214fe17 P0.4: delivery_coords enrichment via geocoding
d3ee6aa P0.3: courier position priority fix + DRY parse
6d99416 docs: add Git workflow to CLAUDE.md
602b476 Initial commit: pre-Claude-Code baseline
[7a60276] P0.8: Final cleanup + meta integration + FAZA 0 DONE
```

Repo: /root/.openclaw/workspace/scripts/dispatch_v2/ (NIE wyżej).

## restaurant_meta.json struktura (P0.7)

```
{
  "restaurants": {
    "<nazwa>": {
      "sample_n", "first_order", "last_order", "active", "volume_pct",
      "prep_variance_min": {median, p75, p90, mean, stddev, min, max},
      "waiting_time_sec": {median, p75, p90, mean, max, median_non_zero, 
                           p75_non_zero, non_zero_count, non_zero_pct},
      "extension_min": {median, p75, p90, mean, never_extended_pct, 
                        extended_count, shortened_count},
      "flags": {low_confidence, chronically_late, prep_variance_high, 
                unreliable, critical},
      "courier_sample": [top5],
      "delivery_addresses_sample": [top5],
      "last_updated": <iso>,
      "prep_variance_fallback_min",  // non-null only if low_confidence
      "waiting_time_fallback_sec",
      "extension_fallback_min"
    }
  },
  "fleet_medians": {
    "fleet_prep_variance_median": 13.0,
    "fleet_waiting_time_median_sec": 0,
    "fleet_extension_median_min": 7.0,
    "source_restaurants_n": 57
  },
  "metadata": {
    "total_delivered_orders": 23607,
    "unique_restaurants": 68,
    "reference_date", "source_csv", "computed_from", "computed_at",
    "min_sample_confident": 30,
    "active_window_days": 14,
    "critical_volume_pct": 5.0
  }
}
```

**Integracja w Fazie 1 route_simulator_v2:**
```python
def get_pickup_ready_at(restaurant_name, czas_odbioru_timestamp, now):
    r = meta["restaurants"].get(restaurant_name)
    if r is None:
        pv = meta["fleet_medians"]["fleet_prep_variance_median"]  # 13
    elif r["flags"]["low_confidence"]:
        pv = r["prep_variance_fallback_min"]  # fleet fallback
    else:
        pv = r["prep_variance_min"]["median"]
    
    pickup_ready = czas_odbioru_timestamp + timedelta(minutes=pv)
    return max(now, pickup_ready)
```

## Decyzje architektoniczne D1-D18 (ZATWIERDZONE, nie re-negocjowalne)

**D1 — Effective pickup time dla SLA.** SLA od efektywnego odbioru (assigned: sim_arrival restauracji), nie od now.

**D2 — Pełny PDP-TSP** z constraint pickup-before-delivery. Brute-force bag=5, <100ms.

**D3 — Dynamic MAX_BAG_SIZE (P0.1).** MAX_BAG_TSP_BRUTEFORCE=5 (performance), MAX_BAG_SANITY_CAP=8 (anomaly). Tier wpływa na scoring multiplier, NIE bag size. Wave size z kalkulacji SLA+TSP+mapy+kuriera.

**D4 — oldest_in_bag_min = 0 dla assigned.** SLA od picked_up.

**D5 — JEDNOLITE SLA 35 min do października 2026.** Premium tiery odroczone do first paying customer.

**D6 — Oceny 4 wymiary + consistency.** Speed/Reliability/Quality/Discipline + Consistency. Tier A/B/C/D z modyfikatorami (1.05/1.00/0.92/0.75).

**D7 — Grupa Telegram z Ziomkiem.** Broadcasty + komendy prywatne + @mention. NIE losowe rozmowy.

**D8 — BEZ WAITU.** Kurier zawsze w ruchu.

**D9 — Continuous routing.** Sliding window 15 min future orders. Kurier = pipeline, nie single assignment.

**D10 — Opcja 1 dziś → Opcja 2 jutro.** PDP-TSP point-in-time → migracja do VRPTW OR-Tools w Fazie 9.

**D11 — Dyspozycje → Grafik.** Środa 20:00 → Piątek 12:00 deadline → Piątek 18:00 scheduler → Piątek 20:00 propozycja → Sobota 09:00 publikacja.

**D12 — Overbooking policy.** Nadmiar: tier A/B peak, reszta off. Niedobór: alert "szukamy pracowników".

**D13 — Premium SLA odroczone do X.2026.**

**D14 — Faza 0 przed Fazą 1.** DONE 12.04.

**D15 — Shadow Mode = Ziomek imituje koordynatora.** Po akceptacji TAK w Telegram → Ziomek loguje się do panelu, przypisuje sam.

**D16 — Filozofia kontraktu + ochrona kuriera.** Restauracja deklaruje → MUSI dotrzymać. Alerty biznesowe ALE dla restauracji z historią spóźnień Ziomek wysyła kuriera z bufem (prep_variance).

**D17 — OSRM fallback 4-warstwowy (P0.5).** Traffic-aware speeds (5 bucketów korków), HAVERSINE_ROAD_FACTOR_BIALYSTOK=1.37, circuit breaker, hourly metrics.

**D18 — delivery_coords od NEW_ORDER (P0.4).** Inline geocoding w panel_watcher, timeout 2s, cache hit 90%.

## 29 Reguł biznesowych (potwierdzone na 2000+ dowozach)

### Dispatching (R1-R10)
1. **Outlier 3 km.** Nowy >3 km od najbliższego w bagu → odrzuć. -61% violations.
2. **Closest-first.** TSP lexicographic: (SLA_violations, total_duration).
3. **Dynamic MAX_BAG_SIZE** (D3).
4. **Free stop detector.** delivery <500m od restauracji/kuriera → zero-cost.
5. **On-route pickup bundling (UVP).** Detour <1.5 km → dorzuć. Wolt/Bolt nie.
6. **Load-based alert.** active_orders/active_couriers >3.0 przez >10 min.
7. **Golden hour 18:30-20:00.** 1 kurier rezerwa, load max 2.5.
8. **Peripheral SLA 45 min.** Wasilków/Nowodworce/Porosły/Kleosin/etc + sugestia +5 PLN.
9. **Data quality filter.** Delivery >90 min → data_error, excluded.
10. **Winter mode.** Gru-lut: weekend multiplier 1.4x, MAX_BAG -1.

### Kurierzy (R11-R15)
11. **MST per kurier.** Bartek 3.2 t/h, Mateusz O 3.6. Scheduler nie +15%.
12. **Tier D blacklist peak.** SLA <75% przez 5+ dni → off-peak only.
13. **Consistency score.** 100 - stddev(daily_sla). Niestabilny B < stabilny B.
14. **Ramp-up godzina 1.** Pierwsza godzina zmiany: max bag 2.
15. **Weekly courier report.** Niedziela 20:00 prywatnie.

### Restauracje (R16-R22)
16. **Critical partner.** >5% wolumenu → osobny monitoring.
17. **Per-restaurant SLA baseline.** Spadek >5pp w 7 dni → alert.
18. **Meta gap-filling (P0.7 DONE).** 68/68 restauracji.
19. **Cancellation monitoring.** >5% rolling 7 days → alert.
20. **Per-restaurant bag cap.** Kumar's/Mama Thai/Baanko/Eatally → max 2.
21. **Seasonal degradation.** >10pp vs baseline miesiąca → alert.
22. **Restaurant onboarding similarity.** Similar_to 3 istniejących → 14-day adaptive learning.

### Finansowe (R23-R26)
23. **Dynamic pricing dalekie.** >15 km lub poza miasto → sugestia +5 PLN. +14k/rok dodatkowej marży.
24. **Per-restaurant revenue report.** Cotygodniowo do właścicieli.
25. **Fleet utilization.** Target >85% w ruchu. <70% = problem.
26. **Weekly ROI Adrian.** Poniedziałek 08:00 Telegram.

### Z sesji 12.04 (R27-R29)
27. **Pickup window ±5 min.** Detour >5 min wait → NO (D8 enforcement).
28. **Wave continuity preference.** Deadhead scoring z fazą cyklu. Kurier kończący falę 500m od restauracji za 35 min > wolny za 20 min 8 km dalej.
29. **Best-effort + alert.** Feasibility NO all → najlepsza dostępna + "SLA violation +X, rezerwowy?". Nigdy wiszący order.

## Format Telegram [PROPOZYCJA] (compact, 600 chars target)

**Happy-path:**
```
[#{order_id}] {created_time} → {delivery_addr}
{restaurant}, dekl. {czas_odbioru_timestamp} (ready ~{pickup_ready_at})

🎯 {courier_name} ({score}) — {distance} km, ETA {eta}, bag {before}→{after}
   trasa: {trasa_geograficzna_pickup_points_delivery} ✓

🥈 {alt1} ({s1}) | {alt2} ({s2}) | {alt3} ({s3})

✓ R1 R3 R8 R20 R27 D8 | {restaurant}: prep {prep}min, critical={bool}

TAK / NIE / INNY / KOORD
```

**Best-effort (impasse, R29):**
```
[#{order_id}] ⚠️ {created_time} → {addr}
{restaurant}, dekl. {dekl} (+{sla_violation_min} min SLA violation)

🎯 {courier} ({score} best_effort)
   bag {before}→{after}, trasa: {trasa}

🥈 {alt1} (+{min1} min, bag {b1}) | {alt2} ({peripheral/bag/other})

❌ {R_fail1} | {R_fail2}
💡 {restaurant} {non_critical?}, -5 min OK | KOORD na później?

TAK {courier_first_name} / INNY / KOORD / SKIP
```

**Live auto-approve (Faza 8):**
```
[AUTO #{order_id}] {courier} @ {eta} → {restaurant}/{addr} ({score}) ✓
```

## ZAWSZE / NIGDY (hard rules)

### ZAWSZE
- CLAUDE_WORKFLOW.md czytane na start sesji
- cp .bak-$(date +%Y%m%d-%H%M%S) przed patchem prod pliku
- py_compile → import check → test → restart (3-etapowa walidacja)
- Atomic writes temp → fsync → rename dla produkcji
- Warsaw TZ: `from zoneinfo import ZoneInfo; WARSAW = ZoneInfo("Europe/Warsaw")`
- R9 data quality filter przed scoringu
- TECH_DEBT.md update na koniec sesji
- Po akceptacji TAK Adriana w Telegram → Ziomek loguje i przypisuje sam (D15)
- route()/table() zwracają dict/list (nie None) — obsługuj osrm_fallback flag
- tmux dla długich sesji CC (odporność na SSH disconnect)

### NIGDY
- Nie łam prod bez cp .bak-* + py_compile + testy
- Nie restartuj systemd bez py_compile + import check + Adrian zgody
- Nie reintroduce chromedp — Python HTTP (stale wycofane)
- Nie każ kurierowi czekać (D8)
- Nie licz SLA od now dla assigned (D1)
- Nie jq (brak w systemie)
- Nie tools.telegram / tools.exec.approval w openclaw.json (crash)
- Nie hardcoduj MAX_BAG_SIZE (D3)
- Nie zawieszaj orderów (R29 best-effort)
- Nie implementuj premium SLA dzisiaj (D13)
- Nie zaniżaj ETA "bo się spóźniają" (D16 zamiast: bufor)
- Nie używaj traffic.traffic_multiplier dla OSRM fallback (D17 własne buckety)
- Nie zaczynaj Fazy 2+ przed 14 dni stabilnego Ziomka (hardening first)
- **Nie rozpraszaj się POS integration — to po tygodniu 4 najwcześniej**

## Plan tygodni 1-4 (zaakceptowany 13.04)

**Tydzień 1 (13-19.04): CC acceleration + Ziomek shadow fundament**
- Dzień 1 (pon): Retention call Big 4 + CC acceleration (allow-list + CLAUDE_WORKFLOW.md + batche) + spec route_simulator_v2 + implementacja
- Dzień 2 (wt): feasibility_v2 + dispatch_pipeline + testy + commit F1.1-F1.2
- Dzień 3 (śr): shadow_dispatcher + telegram_approver + deploy na produkcji
- Dzień 4-5 (cz-pt): monitoring + tuning wag
- Weekend: ODPOCZYNEK + optional demo Big 4

Milestone T1: Ziomek shadow live, agreement >75%, CC akceleracja działa, Adrian ma 3-5h/dzień więcej na sprzedaż.

**Tydzień 2 (20-26.04): Auto-approve + API skeleton**
- Pon-wt: auto-approve high-confidence (Faza 8 mini) + Telegram approver w panelu (D15)
- Śr-czw: API Nadajesz FastAPI skeleton + OAuth2 + POST /v1/quote /orders
- Piątek: Restimo outreach + pitch "Ziomek + API ready"
- Weekend: monitoring auto-approve + pierwsze sprzedażowe

Milestone T2: 40-60% orderów auto, API demo-able, Adrian 6-7h/dzień sprzedaż.

**Tydzień 3 (27.04-3.05): Hardening + ratings quick**
- Pon-wt: Ziomek hardening (circuit breakers, failover, monitoring dashboard)
- Śr-czw: Faza 2 ratings quick version (nightly job, tier, scoring integration)
- Pt: 40 spotkań sprzedażowych
- Weekend: ODPOCZYNEK

Milestone T3: 99%+ uptime 14 dni, ratings aktywne, pierwszy nowy klient z sales pushu.

**Tydzień 4 (4-10.05): ROI boosters**
- R23 dynamic pricing +5 zł dalekie (implementacja 1 dzień)
- R17/R19/R21 restaurant monitoring alerty
- R6 load-based natężenie auto
- 40 kolejnych spotkań

Milestone T4: +5-8% marża transport, proaktywny restaurant monitoring, 2-3 nowych w pipeline.

## Diagnostyka (quick commands)

```bash
# Status
systemctl is-active dispatch-panel-watcher dispatch-sla-tracker dispatch-shadow
tail -20 /root/.openclaw/workspace/scripts/logs/shadow.log

# Event bus
python3 -c "
import sys; sys.path.insert(0, '/root/.openclaw/workspace/scripts')
from dispatch_v2 import event_bus
print(event_bus.stats())
"

# Agreement rate (Faza 1+)
python3 -c "
import json
logs = [json.loads(l) for l in open('/root/.openclaw/workspace/dispatch_state/learning_log.jsonl')]
agree = sum(1 for l in logs if l['ziomek_proposed'] == l['adrian_chose'])
print(f'Agreement: {agree}/{len(logs)} = {100*agree/len(logs):.1f}%')
"

# OSRM metrics (P0.5)
grep 'OSRM hourly' /root/.openclaw/workspace/scripts/logs/watcher.log | tail -5

# Git
cd /root/.openclaw/workspace/scripts/dispatch_v2 && git log --oneline | head -10
```

## Kontakt awaryjny

Adrian Telegram: 8765130486 — pisz gdy:
- Production down (dispatch-* services fail)
- Ziomek proponuje absurd (guaranteed SLA violation)
- Data quality alarm (>10 orderów >90 min w dniu)
- Critical partner SLA spadł >10pp w 3 dni
- Fleet utilization <60% przez >4h
- Agreement rate <60% przez 3 dni (scoring bug)
- OSRM fallback rate >10% przez >15 min (alert do zaimpl.)

## Pytaj nie zgaduj

Jeśli masz wątpliwości co do:
- Struktury danych → `cat` / `head` / `python3 -c "import json; print(...)"`
- Sygnatury funkcji → `grep -n "def funkcja" plik.py`  
- Czy coś istnieje → `find / -name "nazwa*" 2>/dev/null | head`
- Stanu produkcji → `systemctl` / tail logi
- Intencji Adriana → ZAPYTAJ, nie zgaduj

**Koszt zapytania: 5 sekund. Koszt zgadnięcia źle: 10-30 min debug.**
