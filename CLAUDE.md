# ZIOMEK V3.5 — MASTER BRIEF (dla Claude Code, 14.04.2026)

**Ten plik czytasz JAKO PIERWSZE na początku każdej sesji.**

**Zmiana vs V3.3:** dodane wyniki red-team review (Gemini 3.1 PRO + DeepSeek-V3) — security TIER 0, decyzja architektoniczna Fazy 1, plan Krok 0-4.

## Kontekst biznesowy (pamięć operacyjna)

Adrian Czapla, NadajeSz Białystok (ekspansja Warszawa Q3 2026), 30 kurierów, 55 restauracji, 1500-2000 orderów/tydzień, revenue transport 35-45k PLN/tydz + GMV cash 70-90k PLN/tydz. Roczna skala 1.87M PLN transport, 3.6M PLN GMV.

**Faza 0 DONE 12.04.2026** — 8/8 patches, **10 commitów** (z V3.3 docs update).

### Kluczowa informacja biznesowa

**Big 4 jeden właściciel** — Chicago Pizza, Grill Kebab, Raj, Sweet Fit & Eat:
- Start: maj 2025
- Marzec 2026: 1489 orderów (Chicago 387, Grill 654, Raj 325, Sweet 123)
- Przychód marzec 2026: 31,623 zł transport + 74,324 zł GMV cash
- **~20-22% wolumenu Nadajesz od jednego decision-makera** = concentration risk
- Wszystkie 4 używają Symplex Bistro (POS)

**Analiza spadku marzec 2026:**
- YoY marzec 2026 lepszy niż 2025 w 5/7 dni tygodnia (+11% ogólnie)
- Poniedziałki -17% YoY
- Sezonowość luty→marzec: -30% w 2026 vs -10% naturalna (anomalia)
- Spadek pochodzi z 64 pozostałych restauracji, nie Big 4

## ✅ Stan po Fazie 1 (DONE 13-14.04.2026)

**Wszystkie Kroki 0-4 DONE.** Shadow dispatcher live, pierwsze propozycje Telegram dostarczone 13.04 23:05.

### Krok 0 — Security TIER 0 ✅ DONE (P0.5b `0f574c1`)
5 fixów per `docs/SECURITY_FIXES_TIER0.md`: HARD EXCLUSIONS (settings.json deny rules), state_machine retry+LOCK_SH, geocoding atomic_write_json, panel_client `_open_with_relogin`, .gitignore audit.

### Krok 1 — CC acceleration ✅ DONE
Allow-list + deny rules w `~/.claude/settings.json`, tmux 2 okna (claude + logs), `morning_brief.sh` + `evening_wrap.sh`.

### Krok 2 — Decyzja architektoniczna Fazy 1 ✅ DONE (D19)
Greedy insertion O(N) jako MVP + brute-force dla bag≤3 (`dd73048`). Spec `docs/FAZA_1_DECYZJA_ARCH.md`.

### Krok 3 — Git remote backup ✅ DONE
GitHub `czaplaadrian88-code/ziomek-dispatch-`, SSH key ed25519 deploy key, push 22 commitów, cron hourly push `/root/backups/git_push.log`.

### Krok 4 — Faza 1 ✅ DONE
5 modułów core (`dd73048`) + F1.2-F1.6 iteracje:
- **F1.1** core: `route_simulator_v2` + `feasibility_v2` + `dispatch_pipeline` + `shadow_dispatcher` + `telegram_approver`
- **F1.2** `courier_names.json` lookup (K207→Marek, K289→Grzegorz W fix)
- **F1.3** [PROPOZYCJA] enrichment — imiona + km + ETA pickup + delivery_address
- **F1.4a** `/status` komenda Telegram — stan systemu on-demand
- **F1.4b** daily_briefing.py (morning/evening) — odroczone F1.6
- **F1.4c** courier_ranking.py (top N SLA) — odroczone F1.6
- **F1.5** GPS PWA server — `https://gps.nadajesz.pl` + Let's Encrypt + nginx reverse proxy + PIN auth 4-cyfra
- **F1.6** `/status` 3-w-1 (bieżący + dziś + wczoraj + top 3 kurierów) + wyłączenie auto-briefing/ranking crons

### 6 serwisów produkcyjnych live
| Serwis | Cel |
|---|---|
| `dispatch-panel-watcher` | 20s poll panelu Rutcom |
| `dispatch-sla-tracker` | 10s SLA + delivery_time_minutes |
| `dispatch-shadow` | Shadow dispatcher — propozycje → Telegram |
| `dispatch-telegram` | Telegram approver + `/status` command |
| `dispatch-gps` | PWA GPS receiver (port 8766) |
| `nginx` | Reverse proxy 443 → 8766 + HTTP→HTTPS redirect |

### Kluczowe insighty Fazy 1
- **D19 greedy hybrid** działa — 76-78ms latency per decyzja (w peak), 30 kurierów × 5 orders/min = OK
- **`learning_log.jsonl` zbiera od 13.04 23:05** — pierwsza akcja: NIE dla #465834 (Grill Kebab, kurier 207). Agreement rate potrzebuje >100 propozycji dla meaningful metrics (min. tydzień shadow operation).
- **F1.2 courier_names.json** — bug `kurier_piny.json` (PIN-y jako klucze, nie courier_id) był root cause "K207" zamiast "Marek". Fix: odwrócenie `kurier_ids.json` → `{courier_id: name}`.
- **F1.5 GPS coverage** — legacy Traccar writer (`/root/gps_server.py` @reboot) nadal pisze `gps_positions.json` z imieniem jako key. PWA pisze osobny `gps_positions_pwa.json` z courier_id — `courier_resolver._load_gps_positions()` merge z PWA primary. **Fresh GPS <5min: 2/82** kurierów (peak migration w weekend po SMS/QR dystrybucji).

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
| state_machine.py | ✅ → 🔧 P0.5b retry | 6 commitment levels, delivery_coords w NEW_ORDER upsert |
| panel_client.py | ✅ → 🔧 P0.5b re-login | login HTTP Laravel |
| panel_watcher.py | ✅ P0.4 | inline geocoding delivery_address |
| sla_tracker.py | ✅ | 10s konsumer SLA |
| osrm_client.py | ✅ P0.5 | route/table z fallbackiem, circuit breaker |
| geocoding.py | ✅ P0.4 → 🔧 P0.5b atomic write | Google + cache, timeout parametryzowany |
| scoring.py | ✅ P0.1 | 4 komponenty, linear decay |
| courier_resolver.py | ✅ P0.3 | priority fix aktywny bag > last_delivered |
| route_simulator.py v1 | 🗑️ | Do przepisania v2 (greedy w Fazie 1) |
| feasibility.py v1 | 🗑️ | Do przepisania v2 |

### Nowe moduły Fazy 1 (Krok 4):
- route_simulator_v2.py (~300 linii) — **greedy insertion + brute-force fallback bag ≤ 3** + prep_variance
- feasibility_v2.py (~200 linii) — R1/R3/R8/R20/R27/D8
- dispatch_pipeline.py (~250 linii) — scoring + R28 + R29
- shadow_dispatcher.py (~350 linii) — systemd runner
- telegram_approver.py (~250 linii) — Telegram listen + learning_log

### State: /root/.openclaw/workspace/dispatch_state/

| Plik | Zawartość |
|---|---|
| orders_state.json | Stan orderów |
| events.db | Event bus SQLite |
| geocode_cache.json | Google cache (90% hit, 294 entries) — 🔧 P0.5b atomic |
| restaurant_coords.json | 53 restauracje |
| restaurant_meta.json | **68 restauracji, 113 KB** z P0.7 |
| shadow_decisions.jsonl | 🆕 Faza 1 |
| learning_log.jsonl | 🆕 Faza 1 |
| impasse_log.jsonl | 🆕 Faza 1 |

### Offline tools (POZA git repo): /root/.openclaw/workspace/scripts/tools/
- calibrate_road_factor.py (P0.5 baseline)
- gap_fill_restaurant_meta.py (P0.7, 595 linii, regen meta)

### Docs: /root/.openclaw/workspace/scripts/dispatch_v2/docs/
- (na poziomie wyżej: CLAUDE.md — TEN plik, master brief)
- CLAUDE_WORKFLOW.md — agent behavior spec (V3.4)
- TECH_DEBT.md — backlog + bug notes per patch
- FAZA_0_SPRINT.md — historia 8 patchów
- SYSTEM_FLOW.md — end-to-end flow
- DEMAND_ANALYSIS.md — 121k dowozów, hipotezy Fazy 6
- SKILL.md — szczegóły operatora
- 🆕 SECURITY_FIXES_TIER0.md — spec 5 fixów + checklist (Krok 0)
- 🆕 FAZA_1_DECYZJA_ARCH.md — greedy vs brute-force vs OR-Tools (Krok 2)

### Backups: /root/backups/
- dispatch_v2_POST_FAZA_0_20260412-194750.tar.gz (450K)
- dispatch_state_POST_FAZA_0_20260412-194750.tar.gz (301K)
- tools_POST_FAZA_0_20260412-194750.tar.gz (26K)

### Archive: /root/archive/p07_source/
- 9 CSV zestawienie_panel 52-60 + 1 merged (32 MB, ~24007 delivered orderów)

### Sekrety (HARD EXCLUSION dla CC): /root/.openclaw/workspace/.secrets/
- panel.env, gmaps.env, traccar.env

**CC NIGDY nie czyta tej ścieżki. Nigdy nie wkleja zawartości tych plików w odpowiedziach.**

### Systemd
- dispatch-panel-watcher.service — 20s ACTIVE ✅
- dispatch-sla-tracker.service — 10s ACTIVE ✅
- dispatch-shadow.service — 🆕 Faza 1 (Krok 4)
- dispatch-telegram-approver.service — 🆕 Faza 1 (Krok 4)

## Git history (22 commitów — Faza 0 + Faza 1)

```
842f961 F1.6: /status 3-w-1 + wyłączenie cron briefing/ranking
7af8ce1 F1.5: GPS PWA server z PIN + HTTPS via nginx + Let's Encrypt
535047c F1.4c: courier ranking dzienny 23:30
3afeae4 F1.4b followup: docs/CRON_SCHEDULE.md — infrastructure as code
23bfa7d F1.4b: daily briefing morning+evening Telegram (CRON_TZ=Warsaw)
2649ac7 F1.4a: /status komenda Telegram — stan systemu na żądanie
f7ff9eb F1.3: Enrichment formatu propozycji — km, ETA, adres, imiona
4b7d1b4 F1.2: courier_names.json lookup — K207 → Marek, K289 → Grzegorz W
2df098e F1.1 followup: TECH_DEBT notes po pierwszej propozycji Telegram
dd73048 F1.1: Faza 1 core modules (route_sim_v2+feasibility_v2+pipeline+shadow+approver)
0f574c1 P0.5b: Security TIER 0 hotfix (4 code fixes + .gitignore + spec note)
154fb08 docs: V3.4 update + add SECURITY_FIXES_TIER0 + FAZA_1_DECYZJA_ARCH
0c80dee docs: update CLAUDE.md to V3.3 + add docs/CLAUDE_WORKFLOW.md
7a60276 P0.8: Final cleanup + meta integration + FAZA 0 DONE
57a5d34 P0.7: gap_fill_restaurant_meta.py + restaurant_meta.json (68 rest)
bfd1dfc docs: add DEMAND_ANALYSIS.md
12285ef P0.6: RECON panel API (prep_ready_at nie istnieje)
15493ea P0.5: OSRM haversine fallback + circuit breaker
214fe17 P0.4: delivery_coords enrichment via geocoding
d3ee6aa P0.3: courier position priority fix + DRY parse
6d99416 docs: add Git workflow to CLAUDE.md
602b476 Initial commit
```

Repo: `/root/.openclaw/workspace/scripts/dispatch_v2/` (NIE wyżej).
**Remote:** `git@github.com:czaplaadrian88-code/ziomek-dispatch-.git` (deploy key, push co godzinę przez cron).

## restaurant_meta.json struktura (P0.7)

```
{
  "restaurants": {
    "<nazwa>": {
      "sample_n", "first_order", "last_order", "active", "volume_pct",
      "prep_variance_min": {median, p75, p90, mean, stddev, min, max},
      "waiting_time_sec": {median, p75, p90, mean, max, median_non_zero, ...},
      "extension_min": {median, p75, p90, mean, never_extended_pct, ...},
      "flags": {low_confidence, chronically_late, prep_variance_high, 
                unreliable, critical},
      "courier_sample": [top5],
      "delivery_addresses_sample": [top5],
      "last_updated": <iso>,
      "prep_variance_fallback_min", "waiting_time_fallback_sec",
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
    "total_delivered_orders": 23607, "unique_restaurants": 68, ...
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

## Decyzje architektoniczne D1-D19

**D1** — Effective pickup time dla SLA. Od efektywnego odbioru, nie od now.

**D2** — PDP-TSP z constraint pickup-before-delivery. ALE w Fazie 1 zaczynamy od greedy (D19), brute-force fallback bag ≤ 3.

**D3** — Dynamic MAX_BAG_SIZE. MAX_BAG_TSP_BRUTEFORCE=5, MAX_BAG_SANITY_CAP=8.

**D4** — oldest_in_bag_min = 0 dla assigned. SLA od picked_up.

**D5** — JEDNOLITE SLA 35 min do października 2026.

**D6** — Oceny 4 wymiary + consistency. Tier A/B/C/D z modyfikatorami (1.05/1.00/0.92/0.75).

**D7** — Grupa Telegram z Ziomkiem.

**D8** — BEZ WAITU. Kurier zawsze w ruchu.

**D9** — Continuous routing. Sliding window 15 min.

**D10** — PDP-TSP point-in-time → migracja do VRPTW OR-Tools w Fazie 9.

**D11** — Dyspozycje → Grafik. Środa 20:00 → Piątek 12:00 → 18:00 → 20:00 → Sobota 09:00.

**D12** — Overbooking policy. Nadmiar: tier A/B peak.

**D13** — Premium SLA odroczone do X.2026.

**D14** — Faza 0 przed Fazą 1. DONE 12.04.

**D15** — Shadow Mode = Ziomek imituje koordynatora.

**D16** — Filozofia kontraktu + ochrona kuriera. Bufor prep_variance.

**D17** — OSRM fallback 4-warstwowy (P0.5).

**D18** — delivery_coords od NEW_ORDER (P0.4).

**🆕 D19** — Greedy insertion w Fazie 1 (decyzja po Gemini review). route_simulator_v2 zaczyna od greedy O(N), brute-force tylko dla bag ≤ 3, OR-Tools migracja w Fazie 9. Powód: brute-force bag=5 × 30 kurierów × 5 orderów/min = 18000 obliczeń/min, GIL nie wytrzyma. Spec w `docs/FAZA_1_DECYZJA_ARCH.md`.

## 29 Reguł biznesowych (potwierdzone na 2000+ dowozach)

### Dispatching (R1-R10)
1. Outlier 3 km → odrzuć. -61% violations.
2. Closest-first lexicographic (SLA_violations, total_duration).
3. Dynamic MAX_BAG_SIZE (D3).
4. Free stop detector <500m → zero-cost.
5. On-route bundling (UVP). Detour <1.5 km.
6. Load-based alert. active_orders/active_couriers >3.0 przez >10 min.
7. Golden hour 18:30-20:00. 1 kurier rezerwa, load max 2.5.
8. Peripheral SLA 45 min. Wasilków/Nowodworce + sugestia +5 PLN.
9. Data quality filter. Delivery >90 min → data_error.
10. Winter mode. Gru-lut: weekend multiplier 1.4x, MAX_BAG -1.

### Kurierzy (R11-R15)
11. MST per kurier. Bartek 3.2 t/h, Mateusz O 3.6.
12. Tier D blacklist peak. SLA <75% przez 5+ dni → off-peak only.
13. Consistency score. 100 - stddev(daily_sla).
14. Ramp-up godzina 1. Pierwsza godzina: max bag 2.
15. Weekly courier report. Niedziela 20:00.

### Restauracje (R16-R22)
16. Critical partner >5% wolumenu → osobny monitoring.
17. Per-restaurant SLA baseline. Spadek >5pp w 7 dni → alert.
18. Meta gap-filling (P0.7 DONE). 68/68.
19. Cancellation monitoring >5% rolling 7 days → alert.
20. Per-restaurant bag cap. Kumar's/Mama Thai/Baanko/Eatally → max 2.
21. Seasonal degradation >10pp vs baseline → alert.
22. Restaurant onboarding similarity. 14-day adaptive.

### Finansowe (R23-R26)
23. Dynamic pricing dalekie >15 km → +5 PLN. +14k/rok.
24. Per-restaurant revenue report. Cotygodniowo.
25. Fleet utilization target >85%.
26. Weekly ROI Adrian. Poniedziałek 08:00 Telegram.

### Z sesji 12.04 (R27-R29)
27. Pickup window ±5 min. Detour >5 min wait → NO.
28. Wave continuity preference. Deadhead scoring z fazą cyklu.
29. Best-effort + alert. Nigdy wiszący order.

## Format Telegram [PROPOZYCJA] (compact, 600 chars)

**Happy-path:**
```
[#{order_id}] {time} → {addr}
{rest}, dekl. {dekl} (ready ~{ready})

🎯 {courier} ({score}) — {km} km, ETA {eta}, bag {b1}→{b2}
   trasa: {geo_trasa} ✓

🥈 {alt1} ({s1}) | {alt2} ({s2}) | {alt3} ({s3})

✓ R1 R3 R8 R20 R27 D8 | {rest}: prep {p}min, critical={c}

TAK / NIE / INNY / KOORD
```

**Best-effort (R29):**
```
[#{order_id}] ⚠️ {time} → {addr}
{rest}, dekl. {dekl} (+{viol} min SLA violation)

🎯 {courier} ({score} best_effort)
   bag {b1}→{b2}, trasa: {trasa}

🥈 {alt1} (+{m} min, bag {b}) | {alt2} ({reason})

❌ {fail1} | {fail2}
💡 {rest} {non_critical?}, -5 OK | KOORD?

TAK {first} / INNY / KOORD / SKIP
```

**Auto (Faza 8):**
```
[AUTO #{id}] {courier} @ {eta} → {rest}/{addr} ({score}) ✓
```

## ZAWSZE / NIGDY (z security)

### ZAWSZE
- CLAUDE.md + CLAUDE_WORKFLOW.md czytane na start sesji (przez sed-only-read)
- cp .bak-$(date +%Y%m%d-%H%M%S) przed patchem prod
- py_compile → import check → test → restart (3-etapowa walidacja)
- Atomic writes temp → fsync → rename
- Warsaw TZ: `from zoneinfo import ZoneInfo; WARSAW = ZoneInfo("Europe/Warsaw")`
- R9 data quality filter przed scoringu
- TECH_DEBT.md update na koniec sesji
- Po TAK Adriana w Telegram → Ziomek loguje się i przypisuje (D15)
- route()/table() zwracają dict/list — obsługuj osrm_fallback flag
- tmux dla długich sesji
- **Batch z explicite STOP po 5-8 krokach** (ponad 8 = CC traci kontekst)
- **W commit messages referencjuj Gemini/DeepSeek review** ("fix per DeepSeek #1.1")
- **Sed do odczytu, Python heredoc + str.replace + assert do edycji**

### NIGDY
- Nie łam prod bez cp .bak-* + py_compile + testy
- Nie restartuj systemd bez py_compile + import check + Adrian zgody
- Nie reintroduce chromedp — Python HTTP
- Nie każ kurierowi czekać (D8)
- Nie licz SLA od now dla assigned (D1)
- Nie jq (brak w systemie)
- Nie tools.telegram / tools.exec.approval w openclaw.json (crash)
- Nie hardcoduj MAX_BAG_SIZE (D3)
- Nie zawieszaj orderów (R29 best-effort)
- Nie implementuj premium SLA dzisiaj (D13)
- Nie zaniżaj ETA "bo się spóźniają" (D16: bufor)
- Nie używaj traffic.traffic_multiplier dla OSRM fallback (D17)
- Nie zaczynaj Fazy 2+ przed 14 dni stabilnego Ziomka
- Nie rozpraszaj się POS integration — to po Krokach 0-4 + stabilizacja
- **Nie czytaj /root/.openclaw/workspace/.secrets/, /root/.ssh/, *.env, *.pem, *.key** (HARD EXCLUSION)
- **Nie wklejaj zawartości plików .env/.secrets/ w odpowiedziach** nawet jeśli przypadkiem otworzysz
- **Nie używaj sed do edycji** (tylko odczyt)
- **Nie startuj Krok 4 (Faza 1) przed Krokami 0-3**
- **Nie pchaj autonomous mode bez CI/CD** (Gemini: zbyt niebezpieczne)

## Plan tygodni 1-4

**Tydzień 1 (13-19.04):** ✅ DONE 13-14.04 — Krok 0 (P0.5b TIER 0) + Krok 1 (CC acc) + Krok 2 (D19) + Krok 3 (git remote GitHub) + Krok 4 (F1.1-F1.6 Faza 1 live). 6 serwisów produkcyjnych aktywnych. Shadow dispatcher + GPS PWA deployed.

**Tydzień 2 (20-26.04):**
- **Learning analyzer** — `/learning poziom 2` (21.04): analiza `learning_log.jsonl` po 7 dniach shadow, agreement rate per kurier+restauracja, false-positive detection, scoring fine-tune
- **Auto-approve** — gdy agreement rate >85% dla danej kombinacji (kurier×restauracja) przez N propozycji → Ziomek przypisuje bez zapytania Adriana (R26)
- **Telegram security #1** (TIER 1) — rate limit Bot API, webhook secret, allowed_users whitelist
- **Rate limit #2** (TIER 1) — nginx burst=10 dla `/gps` (obecnie 5), może istnieje DoS concern
- **OSRM boundary #4** (TIER 1) — circuit breaker threshold calibration po realnym outage
- **Restimo API skeleton** — FastAPI endpoint `/v1/dispatch` (aggregator integration) — nowy kanał przychodów

**Tydzień 3 (27.04-3.05):**
- Hardening (circuit breakers per moduł, supervision tree)
- Faza 2 ratings (gwiazdki kurierów w UI, public transparency)
- 40 spotkań sales partnerów restauracji (target: 10 nowych)

**Tydzień 4 (4-10.05):**
- ROI boosters: R23 dynamic pricing (surge), R17/19/21 restaurant monitoring (late/cancel rate alerts), R6 natężenie auto-tune
- 40 spotkań sales + pierwszy signed Restimo contract

**Odsunięte poza tydzień 4:** POS integration (R31 Symplex Bistro — 4 Big Partner'zy), Faza 2 MKT ratings, Faza 6 scheduler predictions.

## Diagnostyka

**Quick check:** wyślij `/status` do `@NadajeszBot` — pełny dump 3-w-1
(serwisy + dziś + wczoraj + top 3 kurierów). Po F1.6 to **primary** channel.

**Manual CLI (morning_brief.sh, /root/):**

```bash
# 1. Wszystkie 6 serwisów (było 3, dodane shadow+telegram+gps+nginx)
systemctl is-active dispatch-panel-watcher dispatch-sla-tracker \
                    dispatch-shadow dispatch-telegram dispatch-gps nginx

# 2. Tail all active logs (20 ostatnich per)
tail -20 /root/.openclaw/workspace/scripts/logs/watcher.log
tail -20 /root/.openclaw/workspace/scripts/logs/sla_tracker.log
tail -20 /root/.openclaw/workspace/scripts/logs/shadow_dispatcher.log
tail -20 /root/.openclaw/workspace/scripts/logs/telegram_approver.log
tail -20 /root/.openclaw/workspace/scripts/logs/gps_server.log

# 3. State stats
python3 -c "
import sys; sys.path.insert(0, '/root/.openclaw/workspace/scripts')
from dispatch_v2 import state_machine
print(state_machine.stats())
"

# 4. Event bus + learning log
python3 -c "
import sys; sys.path.insert(0, '/root/.openclaw/workspace/scripts')
from dispatch_v2 import event_bus
print(event_bus.stats())
"
wc -l /root/.openclaw/workspace/dispatch_state/learning_log.jsonl

# 5. OSRM metrics
grep 'OSRM hourly' /root/.openclaw/workspace/scripts/logs/watcher.log | tail -5

# 6. GPS coverage
python3 -c "
import sys; sys.path.insert(0, '/root/.openclaw/workspace/scripts')
from dispatch_v2 import courier_resolver
gps = courier_resolver._load_gps_positions()
print(f'GPS entries: {len(gps)}')
"

# 7. Git
cd /root/.openclaw/workspace/scripts/dispatch_v2 && git log --oneline | head -10
tail -3 /root/backups/git_push.log

# 8. Manual briefing / ranking (cron wyłączony 14.04)
cd /root/.openclaw/workspace/scripts && TZ=Europe/Warsaw python3 -m dispatch_v2.daily_briefing evening --dry-run
cd /root/.openclaw/workspace/scripts && TZ=Europe/Warsaw python3 -m dispatch_v2.courier_ranking --dry-run
```

## Kontakt awaryjny

Adrian Telegram: 8765130486 — pisz gdy:
- Production down (dispatch-* services fail)
- Ziomek absurd proposal
- Data quality alarm
- Critical partner SLA -10pp w 3 dni
- Fleet utilization <60% przez >4h
- Agreement rate <60% przez 3 dni
- OSRM fallback rate >10% przez >15 min

## Pytaj nie zgaduj

- Struktury → `cat` / `python3 -c "import json; print(...)"`
- Sygnatury → `grep -n "def" plik.py`
- Czy istnieje → `find / -name "x*" 2>/dev/null | head`
- Stan → `systemctl` / tail logi
- Intencja Adriana → ZAPYTAJ

**Koszt zapytania: 5s. Koszt zgadnięcia źle: 10-30 min debug.**
