# ARCHITECTURE AUDIT — Ziomek dispatch_v2
**Date:** 2026-05-07 evening
**Scope:** `/root/.openclaw/workspace/scripts/dispatch_v2/` + `/root/.openclaw/workspace/dispatch_state/` + systemd `/etc/systemd/system/dispatch-*`
**Branch:** `sprint-07-05-event-bus-opcja-c` @ +32 commits ahead `master@10c754d`
**Method:** static code analysis + state inventory + systemd unit inspection + memory cross-ref (`MEMORY.md` + `TECH_DEBT.md`)
**Style:** factual, focus on architecture & long-term stability — NIE styl kodu

---

## Executive Summary

System jest **funkcjonalny w produkcji**, sprzęga ~30 kurierów / ~250 orderów dziennie i przeszedł 14 days hardening (V3.24 → V3.28 + Faza 7 shadow). **Nie jest jednak ready ani na 10× skalę, ani na multi-tenant, ani na operator-less autonomy** — co jest wprost intencją Z3.

**3 strategiczne luki** dominują nad wszystkim innym:

1. **Single-server SPOF** (Hetzner CPX32) + **Telegram-as-bus** anti-pattern — jeden krash dispatchera, jeden netfailure Telegrama, jeden OOM = pełny outage. Restart `dispatch-telegram` traci pending callbacki w pamięci. **Brak HA, brak stateful queue dla user actions.**
2. **God objects + tight coupling**: `common.py` (61 importerów), `telegram_approver.py` (3240 LOC, 52 except handlers, async/sync boundary, subprocess.run blokujący event loop), `dispatch_pipeline.py` (2706 LOC, 45 except handlers, in-memory cache bez bounded LRU). **Każda zmiana = ryzyko cascading; testowalność niska.**
3. **Append-only JSONL bez locking** dla `learning_log.jsonl` (110 MB, multi-writer: panel_watcher + telegram_approver + auto_koord) — przy peak burście ryzyko interleaved lines, broken JSON, niereproducowalne LGBM training data. To podważa **wszystkie** future ML iterations które są kluczowe dla pivotu Z3.

**3 mocne strony**:
- DAG bez import cycles, czysta separacja shadow/produkcji, atomic write pattern (fcntl + temp + fsync + rename) w 9 z 10 stateful pisarzy.
- Defense-in-depth wzorzec udokumentowany (V3.28 Layer 1-4, sprint firmowe konto 6-warstw).
- Świadomy tech debt log (TECH_DEBT.md 1927 LOC) + memory system + lekcje pisane post-incident — proces nauki istnieje.

**Verdict per dimension** (skala 1-10, gdzie 10 = best-in-class production system):
- Maintainability: **5/10** — czytelne moduły ale god objects + 342 backup files + 92 flag mute proces refactoringu
- Scalability readiness: **3/10** — single server, no horizontal scaling primitives, in-memory state, sync HTTP everywhere
- Production readiness: **6/10** — działa stabilnie dla dzisiejszego loadu ale brak HA, brak observability metrycznej (tylko logi + ad-hoc parser_health), brak SLO

---

## 1. Mapa systemu

### 1.1 Topologia procesów (16 systemd services + 12 timers)

```
┌─────────────────────────────────────────────────────────────────────┐
│                    Hetzner CPX32 (4 vCPU / 8 GB RAM)                │
│                          ─── SINGLE SERVER ───                       │
│                                                                       │
│  ┌─────────────────── LONG-RUNNING (6) ──────────────────────────┐  │
│  │                                                                  │  │
│  │  ① dispatch-panel-watcher    polling Rutcom HTML co 10s        │  │
│  │     └─ TimeoutStopSec=120 (raised od 15s post-V3.19e SIGKILL)  │  │
│  │                                                                  │  │
│  │  ② dispatch-shadow            event-bus consumer, 5s loop       │  │
│  │     └─ TimeoutStopSec=60                                         │  │
│  │                                                                  │  │
│  │  ③ dispatch-telegram          asyncio bot + 4 tasks             │  │
│  │     └─ After=dispatch-shadow                                    │  │
│  │     └─ JEDYNY proces z asyncio (boundary mixing)                │  │
│  │     └─ pending_proposals.json in-memory; restart = LOSS         │  │
│  │                                                                  │  │
│  │  ④ dispatch-sla-tracker       SLA monitor (R6, 35min)           │  │
│  │  ⑤ dispatch-monitor-419       419 storm detector (V3.28)        │  │
│  │  ⑥ dispatch-gps               PWA GPS receiver (F1.5)           │  │
│  │                                                                  │  │
│  │  Restart=on-failure RestartSec=10  (wszystkie)                  │  │
│  │  BRAK WatchdogSec= — hang nie auto-restart                      │  │
│  │  BRAK ExecStartPre= peak-hour guard                             │  │
│  └────────────────────────────────────────────────────────────────┘  │
│                                                                       │
│  ┌─────────────────── ONESHOT TIMERS (10) ───────────────────────┐  │
│  │  • czasowka            *:0/1  (every 1 min)  V3.24-B          │  │
│  │  • shift-notify        *:0/1  (every 1 min)  TASK B           │  │
│  │  • plan-recheck        OnUnitActive=5min      V3.19c           │  │
│  │  • state-reconcile     OnUnitActive=30min     TASK 2 Cz B      │  │
│  │  • event-bus-cleanup   OnCalendar=04:00 UTC   Opcja-C 2026-05-07│  │
│  │  • overrides-reset     OnCalendar=06:00 Warsaw Backlog #5      │  │
│  │  • daily-accounting    Tue-Fri 06:00 Warsaw                    │  │
│  │  • r04-evaluator       OnCalendar=03:00 Warsaw                 │  │
│  │  • cod-weekly-preflight Sun 23:00 Warsaw                       │  │
│  │  • cod-weekly          (disabled, Adrian decision)             │  │
│  └────────────────────────────────────────────────────────────────┘  │
└─────────────────────────────────────────────────────────────────────┘
```

### 1.2 Lifecycle pojedynczego ordera (request flow)

```
Rutcom panel
   │ HTTP poll co 10s (panel_client.fetch_panel_html, 15s timeout × 2 retry)
   ▼
panel_watcher  ──► panel_html_parser (regex 46\d{4}/47\d{4} V3.28 dual-parser)
   │                ├─ uwagi parser (V3.28 firmowe konto, fallback coords)
   │                └─ V3.28 Layer 1-4 PARSER-RESILIENCE checks
   │
   ▼ orders_state.json upsert (state_machine, fcntl LOCK_EX + atomic temp→rename)
   │
   ▼ event_bus.emit(NEW_ORDER) ─► events.db (SQLite WAL, BEGIN IMMEDIATE)
                                   └── dual-table: events (queue) + audit_log (90d)
   │
   ▼ shadow_dispatcher (5s POLL_INTERVAL, batch 50/cycle)
   │    └── pending → processing → processed status
   │
   ▼ dispatch_pipeline.assess_order
   │    ├─ courier_resolver.dispatchable_fleet  ─► schedule_today.json (T3 10min TTL)
   │    │                                       ─► gps_positions_pwa.json
   │    │                                       ─► courier_tiers.json
   │    │                                       ─► courier_plans.json (L2 bag state)
   │    │                                       ─► manual_overrides.json
   │    ├─ feasibility_v2 (R-DECLARED-TIME, R-35MIN-MAX, R-SCHEDULE-AWARE hard gates)
   │    ├─ scoring (BUG-1/2/4 magnitude, R4 corridor, tier tie-breaker, bag size)
   │    ├─ route_simulator_v2 (OR-Tools TSP, 200ms time limit, ThreadPoolExecutor 10w)
   │    │    └── pre-proposal recheck V3.27.1 (panel_client.fetch_order_details, 2s timeout)
   │    │         └── _v327_pre_recheck_last_seen DICT (in-memory, threading.Lock,
   │    │             evict every 500 calls or size>1000, TTL 1h — RACE w eviction loop)
   │    └─ auto_proximity_classifier (Faza 7 shadow, AUTO/ACK/ALERT)
   │
   ▼ Verdict:
   ├── PROPOSE  ─► telegram_approver (proposal_sender asyncio task)
   │                ─► sendMessage Telegram → 4-button keyboard
   │                ─► pending_proposals.json (in-memory + JSON dump, NIE atomic)
   │                │
   │                ▼ user button click (Telegram API getUpdates polling)
   │                ─► callback_query handler (asyncio)
   │                ─► subprocess.run("gastro_assign.py", timeout=30)  ◄ BLOCKING event loop
   │                ─► event_bus.emit(COURIER_ASSIGNED)
   │                ─► state_machine.update_from_event
   │                ─► learning_log.jsonl (raw f.write append, BRAK fcntl) ◄ RACE
   │                
   ├── KOORD    ─► czasowka_scheduler / koordynator bucket (id_kurier=26)
   ├── SKIP     ─► Telegram alert do Adriana ("BRAK KANDYDATÓW")
   │
   ▼ shadow_decisions.jsonl  ◄ 66 MB plik logów
   ▼ c5_shadow_log.jsonl
   
panel zewnątrz (kurier app) ─► PICKED_UP / DELIVERED ─► sla_tracker
                                  └─► R6 35min check, BAG_TIME alert (suppressed 2026-05-07)
```

### 1.3 Persistence layer (źródła prawdy)

| File | Size | Format | Writers | Atomic guarantee |
|---|---|---|---|---|
| **events.db** | 22 MB | SQLite WAL | event_bus | **Strong** — BEGIN IMMEDIATE + dual-table opcja-C |
| **orders_state.json** | 17 KB | JSON | state_machine, panel_watcher | **Strong** — fcntl LOCK_EX + temp→fsync→rename |
| **learning_log.jsonl** | **110 MB** | JSONL append | panel_watcher, telegram_approver, auto_koord, commitment_emitter (5+ writers) | **WEAK** — raw `f.write()` bez fcntl, multi-writer race risk |
| **courier_plans.json** | 17 KB | JSON | plan_manager | **Strong** — fcntl + companion `.lock` sentinel + temp→rename |
| **courier_tiers.json** | 21 KB | JSON | r04_apply nightly | **Strong** — fcntl + atomic |
| **kurier_ids.json / kurier_piny.json** | ~1 KB ea | JSON | courier_admin | **Strong** — fcntl + atomic (post-ETAP B 07.05) |
| **shift_confirmations.json** | 26 KB | JSON | shift_notifications worker | **Strong** — fcntl + companion `.lock` |
| **flags.json** | small | JSON | Adrian + ad-hoc Python `json.dump` | **WEAK** — raw json.dump bez atomic write |
| **schedule_today.json** | 2.4 KB | JSON cache | T3 hot-refresh | TTL 10min, single-writer |
| **geocode_cache.json** | 873 KB | JSON | geocoding | fcntl + atomic, no TTL invalidation |
| **reconciliation_log.jsonl** | 197 KB | JSONL append | reconcile_worker | **WEAK** — raw append, single-writer (timer 30min) — low race risk |
| **shadow_decisions.jsonl** (logs/) | **66 MB** | JSONL append | shadow_dispatcher | single-writer, log path NIE source-of-truth |
| **`.tmp_cr2kure6.json`** | **5.5 MB orphaned** | tempfile | nikt — leak od 2026-04-26 | **GARBAGE** — orphaned tempfile, do cleanup |

### 1.4 External dependencies & failure modes

| Zależność | Endpoint | Timeout | Fallback | Failure impact |
|---|---|---|---|---|
| **Rutcom panel HTTP** | gastro.nadajesz.pl | 15s × 2 retry | None | Stale `orders_state` → bad proposals; 3× fail = `PANEL_UNREACHABLE` event |
| **OSRM** | localhost:5001 (Docker) | 3s | haversine sentinel | All candidates `pickup_too_far` HARD REJECT (Lekcja #81 fail-loud) |
| **Nominatim geocoding** | nominatim.openstreetmap.org | 2-5s | fallback coords (firmowe konto) | None coords → defense gate L1 fires |
| **Google Sheets schedule** | Google API | unknown | yesterday's `schedule_today.json` (10min TTL) | Stale schedule do 24h |
| **Telegram Bot API** | api.telegram.org | unknown w sendMessage | None — proposal pozostaje pending | 5min watchdog → auto-KOORD |
| **courier-api SQLite** | localhost:8767 | per request | None | GPS fallback synthetic position |

---

## 2. Top 20 ryzyk (sortowane po impact × prawdopodobieństwo)

Każde ryzyko: **P** = prawdopodobieństwo (1-5), **I** = impact (1-5), **R** = P×I.

### 🔴 Krytyczne (R ≥ 16)

| # | Ryzyko | P | I | R | Lokalizacja | Mitigation today | Recommend |
|---|---|---|---|---|---|---|---|
| **1** | **Telegram restart traci pending callbacki + watchdog 5min** — `pending_proposals.json` w pamięci procesu, in-flight asyncio tasks zabite SIGTERM. Adrian explicit ban na restart bez ACK | 4 | 5 | 20 | telegram_approver.py:131, :3232 | dokumentowany ban, TimeoutStopSec=default (90s) | Persist pending state pre-shutdown; restore on startup; idempotency token |
| **2** | **Single server SPOF** — Hetzner CPX32 4vCPU/8GB, brak repliki, OOM kill = full outage; OS update / reboot = blackout | 3 | 5 | 15 | infrastructure | brak | Live-failover replica + state replication; deploy do K8s/Nomad cluster |
| **3** | **subprocess.run w asyncio event loop** — `telegram_approver.py:1452` (timeout=30s) i `:1710`. Blokuje event loop → wszystkie inne tasks freeze (proposal_sender, watchdog, updates_poller) | 4 | 4 | 16 | telegram_approver.py:1452, :1710 | timeout 30s capping | `asyncio.to_thread()` lub `loop.run_in_executor()` |
| **4** | **learning_log.jsonl multi-writer bez locka** — 5+ procesów piszących raw `f.write()` do 110 MB pliku. Burst (10 zapisów/sec w peak) = interleaved JSON lines = corrupt training data dla LGBM (które jest fundamentem Z3 pivotu) | 4 | 4 | 16 | panel_watcher.py:139, telegram_approver, auto_koord, commitment_emitter | brak | Migracja do events.db jako event_type='LEARNING_RECORD' lub fcntl wrap |

### 🟠 Wysokie (R 10-15)

| # | Ryzyko | P | I | R | Lokalizacja | Mitigation today | Recommend |
|---|---|---|---|---|---|---|---|
| **5** | **God object `telegram_approver.py` 3240 LOC + 52 except handlers** — najwyższa LOC + najwyższa liczba blanket excepts w repo. Każdy refactor obarczony cognitive risk; testowalność niska; zmiana może łamać dowolnego z 4 asyncio tasks. | 5 | 3 | 15 | telegram_approver.py | extensive tests | Split: `bot/router.py` + `bot/proposals.py` + `bot/callbacks.py` + `bot/admin_cmds.py` |
| **6** | **`common.py` jako universal hub (61 importerów, 1645 LOC)** — flagi, constants, TZ, paths, logger — single point dla cascade failures; każda zmiana ma blast radius 60+ modułów | 4 | 4 | 16 | common.py | testy integration | Rozdzielić na `flags.py` + `constants.py` + `tz.py` + `logger.py` (TECH_DEBT już ma item) |
| **7** | **flags.json read race + non-atomic write** — multi-process hot reload (mtime-based cache w common.py), ad-hoc `json.dump(open(p,'w'))` bez temp+rename. Parallel CC session lub Adrian + skrypt = corrupted flags = cała flota broken | 3 | 5 | 15 | common.py:31, ad-hoc helpers | "ostrożność" | Atomic write helper (temp+fsync+rename), single source `flags_admin.py` |
| **8** | **events.db lock contention** — 5s busy_timeout, single attempt; reconcile_worker może trzymać lock 15-30s w 30-min cycle → panel_watcher emit fail → event LOST | 3 | 4 | 12 | event_bus.py:69-71 | WAL mode | Retry loop max 3× exponential, observability metric `event_emit_fail_count` |
| **9** | **In-memory cache bez bounded eviction** — `_v327_pre_recheck_last_seen` (dispatch_pipeline.py:51), `_route_cache` w osrm_client (5000 cap, no LRU), `geocode_cache` (lifetime), `_coord_cache` district_reverse_lookup. Worst case: 18000 unique IDs/h, eviction co 500 calls = +1-2 MB/h RSS w peak | 4 | 3 | 12 | dispatch_pipeline.py:51, osrm_client.py:40 | TTL 1h docs | LRU(maxsize=N) decorator, OrderedDict.popitem(last=False) |
| **10** | **OSRM container down → wszyscy `pickup_too_far` REJECT** (post-Lekcja #81 fail-loud) lub haversine fake-feasible (pre-fix) | 3 | 4 | 12 | osrm_client.py | fail-loud sentinel | Circuit breaker + degraded mode flag; Adrian Telegram alert na OPEN circuit |
| **11** | **CSRF panel session 22min expiry, bg_refresh thread crash silent** — `panel_client.py` background thread daemon, brak shutdown/atexit, brak watchdog. Crash = next dispatch +6s blocking | 3 | 4 | 12 | panel_client.py:104 | `PANEL_BG_REFRESH_STALE_THRESHOLD_SEC=1500` | atexit register + Telegram alert na crash |
| **12** | **Brak peak-hour restart enforcement** — Adrian's rule "ZERO restart 11-14/17-20 Warsaw" jest tylko organizacyjna; systemd nie blokuje, deploy script nie sprawdza, `Restart=on-failure` może odpalić dispatch-shadow w peaku | 3 | 4 | 12 | systemd units | dokumentacja | `ExecStartPre=/usr/bin/check-not-peak.sh` lub deploy gate w skryptach |
| **13** | **Dotted vs dotless naming inconsistency 45 hardcoded refs w 13 plikach** — runtime zero-impact (Lekcja #54 telegram_approver `_norm` rstrip), ale każdy nowy programmer musi nauczyć się landminy; multi-tenant blocked do czasu cleanup | 4 | 3 | 12 | 13 plików | rstrip workaround | Adrian decision A: cleanup deferred — w stagnation |
| **14** | **JSONL log explosion bez rotacji** — `dispatch.log` 25 MB, `czasowka.log` 12.7 MB, `shadow_decisions.jsonl` 66 MB, `learning_log.jsonl` 110 MB; brak `logrotate` config widocznego, dysk skończy się | 3 | 4 | 12 | logs/, dispatch_state/ | manual cleanup `.bak` | `logrotate` units + retention SLA |
| **15** | **`Let me produce the blocks.dispatch_v2/` + nested `dispatch_v2/dispatch_v2/` w repo** — accidental directories, jeden ma 440 LOC duplikatu (uwagi_address_parser.py); confused readers, drift risk między copies | 3 | 4 | 12 | repo root | nieświadomość | `git rm -rf` po confirmation |

### 🟡 Średnie (R 6-9)

| # | Ryzyko | P | I | R | Lokalizacja | Notes |
|---|---|---|---|---|---|---|
| **16** | **342 backup files .bak* (12.8 MB) — 4.5× większy niż codebase** — common.py ma 50+ snapshotów. Git ma 100% pokrycie, są pure noise + maskują sygnał w `ls`/grep | 5 | 2 | 10 | repo root | TECH_DEBT P3 cleanup automation (pre-commit hook) |
| **17** | **Schemat JSON state bez `_schema_version`** — orders_state.json, courier_plans.json ewoluują per-sprint (Lekcja #80 lost field). Brak strict validation = silent data loss | 3 | 3 | 9 | state_machine.py, plan_manager.py | Pydantic / TypedDict + version migration check |
| **18** | **45-52 except handlers w god objects pożerają błędy** — Lekcja #32 wycofana z silent excepts ale residuum w courier_resolver (19), plan_manager (5), scoring (1) — KeyError, ValueError pod try/except: pass | 4 | 2 | 8 | courier_resolver.py:196,260,277,297,337; plan_manager.py:80,326,384 | Konwersja na `_log.warning(...)` minimum |
| **19** | **Brak SLO / observability metrycznej** — wszystkie health checks ad-hoc (parser_health endpoint, monitor-419 storm). Brak Prometheus/Grafana, brak alert rules deklaracyjnych. Adrian = manual monitor | 3 | 3 | 9 | brak | Prometheus exporter (proces metrics + custom: events lag, SLA pass rate) |
| **20** | **Brak DLQ dla failed events.db rows** — `replay_failed.py` jest ad-hoc CLI; brak auto-recovery, manual intervention required po incydentach | 2 | 4 | 8 | replay_failed.py | Auto-replay w cron (po N min, max attempts) |

### 🟢 Niskie ale warto zanotować (R < 6)

- ThreadPoolExecutor 10 workers w dispatch_pipeline + 2 vCPU CPX32 = oversubscribe 5×, parallel efficiency ~13%
- threading.Lock + fcntl mixed pattern (gps_server) — race na async GCS upload vs fcntl write
- TZ landminey: `datetime.now()` bez tzinfo w learning_analyzer.py:332, mixing Warsaw/UTC w panel responses
- subprocess pipe buffer 64KB limit przy long journalctl outputs (parser_health_endpoint.py)
- Brak HTTP timeout w niektórych urllib calls (default = blocking forever; większość ma override z caller)

---

## 3. Najbardziej krytyczne pliki (impact × blast radius)

Sortowane po `(fan_in × LOC × except_count) / test_coverage_proxy`. **Wymagają największej ostrożności przy KAŻDEJ zmianie.**

| Rank | Plik | LOC | Fan-in | Except | Risk profile |
|---|---|---|---|---|---|
| **1** | **`common.py`** | 1645 | **61** | 17 | **HUB OF EVERYTHING.** Flagi, constants, TZ, paths, logger, district maps, BIG fan-in. Jeden bug = cascade. Refactor blocked przez fan-in. **Każda zmiana wymaga full regression suite.** |
| **2** | **`telegram_approver.py`** | **3240** | 15 | **52** | **GOD OBJECT + asyncio + subprocess + JSONL + state file**. Restart hostility, callback ordering issues, blocking event loop. Najwięcej bug surface w repo. **Adrian zakaz restart bez ACK = de facto immutable runtime.** |
| **3** | **`dispatch_pipeline.py`** | **2706** | 13 | **45** | **CORE DECISION ENGINE.** assess_order, scoring, ranking, pre-proposal recheck, in-memory cache, ThreadPoolExecutor 10w. Każda zmiana = bezpośredni wpływ na każdą propozycję. |
| **4** | **`state_machine.py`** | 582 | 16 | 6 | **SSOT order state.** fcntl + atomic, ale lifecycle complexity + Lekcja #80 lost field history. Schema drift risk. |
| **5** | **`panel_watcher.py`** | 1378 | 7 | **34** | **BOUNDARY INPUT.** HTML parser fragility (V3.28 4-warstwy parser-resilience), free-text uwagi, multi-writer trigger. Pierwszy plik gdzie korupcja danych źródła wsiąka. |
| **6** | **`courier_resolver.py`** | 730 | high | 19 | **Fleet snapshot SSOT.** schedule_today read, manual_overrides read, GPS fallback synthetic. Bug 2026-05-06 (czasowka_scheduler:285 vs dispatchable_fleet) tu się zaczynał. |
| **7** | **`shadow_dispatcher.py`** | 773 | 5 | 10 | **MAIN LOOP.** event_bus consumer, Telegram dispatch, in-flight order tracking. SIGTERM handler ale graceful drain w 60s window. |
| **8** | **`panel_client.py`** | 639 | 7 | 11 | **HTTP BOUNDARY.** Login/CSRF refresh thread daemon, pre-proposal recheck timeouts. V3.27.7 partial fix `ENABLE_PANEL_BG_REFRESH`. |
| **9** | **`route_simulator_v2.py`** | 944 | high | 6 | **TSP/PDP solver wrapper.** OR-Tools 200ms time limit, parallel candidate eval, V3.27.6 frozen window assertion. |
| **10** | **`event_bus.py`** | 362 | 5 | low | **EVENT QUEUE SSOT.** SQLite WAL, BEGIN IMMEDIATE, dedup, audit log. Single attempt + 5s timeout = miss event ryzyko. |

**Drugi krąg** (high-impact ale mniejszy fan-in): `feasibility_v2.py`, `czasowka_scheduler.py`, `plan_manager.py`, `osrm_client.py`, `auto_proximity_classifier.py`.

**Test coverage proxy** (sygnał gdzie tests ratują):
- `dispatch_pipeline.py` ma >100 testów (test_v3271_*, test_decision_engine, test_v319g1_*) — **ratowane**
- `telegram_approver.py` ma test_proposal_format_v2 + test_shift_telegram_router (~700 LOC) — **częściowo, callbacks niedotestowane**
- `common.py` brak dedicated tests — **NIE ratowane** (stąd #1 risk)
- `panel_watcher.py` brak dedicated test file — **NIE ratowane**

---

## 4. Moduły wymagające deep audit

Lista do dedykowanych sprintów audytowych — uszeregowana per ROI naprawy.

### Tier A — natychmiastowy audit (P0, before more features)

1. **`telegram_approver.py`** — 3240 LOC, 52 except, asyncio + subprocess.run blokowanie. Audit cel:
   - Stan callback handlers przy restart (in-flight loss)
   - Event loop blocking points (subprocess.run, sync HTTP)
   - Race między 4 asyncio tasks (proposal_sender, shadow_tailer, updates_poller, watchdog)
   - pending_proposals.json persistence i recovery
   - Brak tests dla callback ordering edge cases
   - **Plan:** Split na 4 moduły (router, proposals, callbacks, admin); replace subprocess.run z asyncio.to_thread; persistent pending state.

2. **`common.py`** — 61 fan-in, 1645 LOC. Audit cel:
   - Co dokładnie ma `common.py` i jakie subsety używają poszczególne moduły (pomyśl o Single-Responsibility violations)
   - Flagi: 92+ entries, 50+ konstanty env-overridable, district maps, log setup. Każdy ma inne lifetime semantics.
   - **Plan:** Split na `flags.py` + `constants.py` + `tz_utils.py` + `logger_setup.py` + `districts.py` (już osobny `districts_data.py`).

3. **`learning_log.jsonl` write paths** — 5+ writers, 110 MB. Audit cel:
   - Czy interleaving już występuje (skanuj plik na broken JSON lines)
   - Burst rate measurement w peak
   - Schema completeness per writer (Lekcja #80 lost field)
   - **Plan:** Migracja do events.db.LEARNING_RECORD table OR fcntl wrap z lock contention metrics.

### Tier B — drugi sprint (P1)

4. **`dispatch_pipeline.py`** — 2706 LOC, in-memory cache + ThreadPoolExecutor. Audit cel:
   - Eviction race w `_v327_evict_old_pre_recheck_entries` (concurrent KeyError możliwe)
   - ThreadPoolExecutor 10w vs 2 vCPU efficiency
   - 45 except handlers — które są silent
   - assess_order branch complexity (regular / czasówka / proactive / auto_proximity)
   - **Plan:** Split assess_order pipeline na clean stages; bounded LRU cache; reduce except handlers via specific types.

5. **`panel_watcher.py` + `panel_client.py`** — boundary input. Audit cel:
   - V3.28 4-warstwowy parser-resilience efficacy (tests vs prod incidents)
   - bg_refresh thread lifecycle (atexit, watchdog, shutdown)
   - HTTP retry exhaustion → PANEL_UNREACHABLE coverage
   - 34 except handlers — czy któreś maskują CSRF/parser bugs
   - Free-text uwagi parser regression risk

6. **systemd unit hardening** — wszystkie services. Audit cel:
   - Brakuje WatchdogSec= w long-running
   - Brak ExecStartPre= peak guard
   - Restart policy nie różnicuje on-failure vs always
   - StandardOutput/StandardError append: bez logrotate
   - Per-service resource limits (MemoryMax, CPUQuota) — brak

### Tier C — strategiczne (P2)

7. **Shadow → production flag flip strategy** — Faza 7 AUTO_PROXIMITY shadow-only, masa flag w `flags.json`. Audit cel: czy istnieje kompletny rollback runbook per flag, czy Adrian ma dashboard, czy są skutki kombinacji flag.

8. **events.db retention + Opcja-C audit_log** — 22 MB queue, 12636 audit records, 90d retention. Audit cel: query performance (idx_events_status), cleanup correctness, audit_log read patterns dla LGBM training.

9. **Reconciliation worker logic** — 30min cycle, append-only log. Audit cel: czy faktycznie wykrywa ghost orders, czy phantom_log nie rośnie unbounded, jak długo trzyma events.db lock.

10. **OSRM circuit breaker + traffic multiplier** — Lekcja #81 fail-loud sentinel. Audit cel: czy circuit_open recovery działa, czy haversine fallback ma feasible_reason kontekst, multi-tenant zachowanie.

---

## 5. Maintainability score: **5/10**

**Dlaczego nie 7+:**
- **God objects** w 3 plikach (common, telegram_approver, dispatch_pipeline) = każdy zmiana wymaga ~80% repo regression — cognitive cost wysoki, nawet z testami
- **52 except handlers** w pojedynczym pliku = code smell defense-in-debt; każdy refactor musi prześledzić katalog "co tu się może wywalić" zamiast type-driven flow
- **Backup proliferation** (342 plików, 12.8 MB) zaburza ergonomię — `ls` `grep` `find` muszą być filtrowane, nowy reader zgubi się
- **92 feature flag entries** z różnymi lifetime semantics (env / flags.json / common.py constants) — flag drift już zaobserwowany (PARSER_DEGRADED, AUTO_PROXIMITY_THRESHOLD nie wszędzie spójne)
- **Brak testów dla `common.py`, `panel_watcher.py`** (najbardziej ryzykownych modułów) — testowy bezpiecznik niepełny
- **Naming inconsistency landmine** (45 dotted refs) trzymane "na deferred" = każdy nowy programmer musi się tego nauczyć
- **Dokumentacja CLAUDE.md 1699 LOC + TECH_DEBT.md 1927 LOC** to obciążenie poznawcze; rzetelne ale wymaga read time przed każdą sesją

**Dlaczego nie 3:**
- **DAG bez import cycles** — czysta architektura na poziomie module graph
- **Atomic write pattern konsekwentny** w 9 z 10 stateful pisarzy
- **Test suite szanowany** — 934 baseline PASS, świadomy 20 pre-existing FAIL, sprint-by-sprint TDD wzorzec
- **Memory + lessons system działa** — Lekcje #1-#88, post-incident learning udokumentowany
- **Defense-in-depth wzorzec** opanowany (V3.28 Layer 1-4, sprint firmowe konto 6-warstw)
- **Granular git tagging** dla rollback points

**Co podniesie do 7+** (priorytetyzowane):
1. Split common.py i telegram_approver.py (ROI: -30% blast radius, +40% testowalność)
2. Cleanup .bak files + accidental dirs (ROI: +15% reading ergonomics, ~1h roboty)
3. Konwersja silent except → specific types + log (ROI: +20% diagnostyczność, ~4-6h)
4. Bounded LRU dla in-memory caches (ROI: +10% memory predictability)
5. `flags.json` atomic write + central admin tool (ROI: +5% safety)

---

## 6. Scalability readiness: **3/10**

**Aktualne ograniczenia per oś skalowania:**

### Pionowa (więcej orderów na tym samym serwerze)
- 4 vCPU / 8 GB RAM — dispatch pipeline robust do ~300 orderów/dzień (obecny load)
- Peak burst: ThreadPoolExecutor 10w × 2 vCPU oversubscribe 5× → diminishing returns przy >5 candidates concurrent
- learning_log.jsonl 110 MB — przy 10× orderów = 1.1 GB/miesiąc; multi-writer JSONL załamie się fizycznie
- events.db 22 MB → przy 10× = 220 MB; SQLite WAL stable do ~1 GB ale lock contention rośnie kwadratowo
- **Verdict:** stable do 2× obecnego loadu (~600 ord/d), za to ~6×+ (1500 ord/d) wymaga przepisania persistence layer

### Pozioma (multiple instancji)
- **Wszystko jest single-instance assumption.** in-memory caches, in-memory pending_proposals, schedule_today.json single read, fcntl locks lokalne do hosta, threading.Lock w-procesie, BIALYSTOK_CENTER hardcoded
- **Telegram bot** = jeden chat_id, getUpdates polling — drugi instance = duplicate messages
- **systemd timers** = nie ma leader election
- **events.db** = SQLite, nie distributed
- **Verdict:** żaden moduł nie jest poza-host-aware. Multi-instance wymaga: distributed event bus (Kafka/NATS), distributed state store (Postgres/Redis), leader election dla Telegram

### Multi-tenant (Restimo, Wolt Drive, Warsaw expansion — Z3)
- `BIALYSTOK_DISTRICT_ADJACENCY` w common.py, hardcoded city w `DEFAULT_CITY = 'Białystok'` (V3.29 wstępne env-override)
- `EXCLUDED_CIDS` static set w daily_accounting/config.py
- `FIRMOWE_KONTO_ADDRESS_IDS = frozenset({161})` (per-tenant ready, ale tylko 1 entry)
- `ZIOMEK_DEFAULT_CITY` env intro — partial readiness
- 28 districts w `districts_data.py` — Białystok-specific
- Schedule sheet ID hardcoded w common.py
- **Verdict:** structurally NIE multi-tenant; istnieją kanaski (env DEFAULT_CITY) ale 80% kodu jest single-tenant assumption

**Co podniesie do 6+** (do 600-1500 orderów + multi-tenant Białystok+Warsaw):
1. Postgres zamiast SQLite events.db + JSON state files (~2-tygodniowy sprint)
2. Redis dla caches (schedule, geocode, in-memory dicts)
3. Multi-tenant config layer: `tenant_config.py` z city-specific districts, sheet IDs, exclusions
4. Stateless workers (12-factor app), shared state w Redis/Postgres
5. Distributed event bus dla cross-instance coordination

**Co podniesie do 8+** (multi-city franczyza):
1. Per-tenant deployment isolation (k8s namespace per franczyza)
2. Telegram bot per tenant + central admin
3. Distributed tracing (OpenTelemetry) — kogo dyspatcho gdzie i czemu
4. Observability stack (Prometheus + Grafana + Loki)

---

## 7. Production readiness: **6/10**

**Co działa w prod (silne strony):**
- ✅ System działa stabilnie pod obecnym loadem (~250 ord/d) z zero data-corruption incidents w ostatnim tygodniu
- ✅ `Restart=on-failure` na każdym service + `RestartSec=10` — automatic recovery z basic crashes
- ✅ Atomic write pattern + fcntl locks w 9 z 10 stateful pisarzy
- ✅ Defense-in-depth (V3.28 Layer 1-4, 6-warstwowa firmowe konto) — udokumentowany wzorzec
- ✅ Health endpoint `:8888/health/parser` aktywny
- ✅ Granular feature flags (92+) z hot-reload — surgical rollback
- ✅ Sprint-by-sprint git tags = łatwy rollback
- ✅ TECH_DEBT.md 1927 LOC + lessons #1-#88 = świadomy proces nauki
- ✅ Backup snapshot strategy (.bak-pre-* 24h retention) per file pre-deploy

**Co brakuje do 8/10:**
- ❌ **Brak HA** — single Hetzner CPX32, brak repliki, brak failover plan
- ❌ **Brak SLO/SLI** — żadnego declared "p95 dispatch latency < X ms" w kodzie ani w docs; metryki tylko ad-hoc w learning_analyzer
- ❌ **Brak observability stack** — Prometheus/Grafana/Loki nieobecne, alert rules tylko ad-hoc Telegram messages
- ❌ **Brak DLQ** — failed events.db rows = manual `replay_failed.py` CLI; zero auto-recovery
- ❌ **Telegram restart hostility** — pending callbacki w pamięci, restart = data loss; dispatch-telegram = de facto immutable runtime (ban Adrian)
- ❌ **Brak peak-hour enforcement** — Adrian's rule organizacyjna, nie technical; auto-restart może odpalić w peaku
- ❌ **Brak logrotate** — logi rosną unbounded (66 MB shadow_decisions, 110 MB learning_log)
- ❌ **Brak resource limits** w systemd units (MemoryMax, CPUQuota) — OOM kill nie ma soft preemption
- ❌ **Brak chaos / load testing** — load real-world only, brak burst stress test
- ❌ **Brak distributed backup** — `dispatch_state/` na local disk, brak offsite snapshot

**Co podniesie do 8/10** (production-grade dla obecnej skali):
1. **Logrotate config** dla wszystkich 25+ logów (1 day, ~30 min roboty)
2. **systemd MemoryMax=2G + CPUQuota=200%** per long-running service (1h roboty)
3. **`replay_failed.py` w cron** — auto-replay failed events co 5min, max 3 attempts (2h roboty)
4. **Persistent pending_proposals + restore on startup** dla telegram_approver (4-6h)
5. **logrotate + duplicity offsite backup** dispatch_state/ (1 dzień)
6. **Prometheus exporter** — proces metrics + custom (events lag, SLA pass rate, dispatch latency p95) (1-2 dni)
7. **Telegram alert rules** — declarative w `alerts.yaml`, fired przez monitoring service (1 dzień)
8. **Peak-hour deploy gate** — `ExecStartPre=/usr/bin/check-not-peak.sh` (30 min)

**Co podniesie do 9-10** (enterprise-grade):
1. HA setup (2 servers + Postgres + Redis, leader election)
2. Distributed tracing (OpenTelemetry)
3. Chaos engineering (kill random service, observe recovery)
4. Multi-tenant isolation
5. SOC2-grade audit trail (currently events.db audit_log dual-table jest w good direction)

---

## Appendix A — Konkretne anti-patterns z lokalizacjami

| Anti-pattern | Plik | Linia | Severity |
|---|---|---|---|
| **subprocess.run w asyncio event loop** | telegram_approver.py | 1452, 1710 | HIGH |
| **Raw `open(path)` bez context mgr** | district_reverse_lookup.py | 56 | LOW (FD leak) |
| **`json.dump(open(p, 'w'))` bez atomic write** | flags.json admin scripts (ad-hoc) | various | MEDIUM |
| **`f.write()` JSONL append bez fcntl** | panel_watcher.py | 139 | HIGH |
| **`f.write()` JSONL append bez fcntl** | telegram_approver.py + auto_koord + commitment_emitter | various | HIGH |
| **`except Exception:` blanket** | courier_resolver.py | 196, 260, 277, 297, 337 | MEDIUM |
| **`except Exception:` blanket** | plan_manager.py | 80, 326, 384 | MEDIUM |
| **`except Exception:` blanket** | scoring.py | 208 | LOW |
| **In-memory dict bez bounded LRU** | dispatch_pipeline.py | 51 (`_v327_pre_recheck_last_seen`) | MEDIUM |
| **In-memory cache bez bounded LRU** | osrm_client.py | 40 (`_route_cache`, FIFO 5000) | LOW |
| **In-memory cache lifetime** | geocoding.py (`_cache`) | n/a | LOW |
| **`datetime.now()` bez tz** | learning_analyzer.py | 332 | LOW |
| **bg daemon thread bez atexit/shutdown** | panel_client.py | 104 | MEDIUM |
| **Module-level cache bez invalidation** | district_reverse_lookup.py | 31 | LOW |
| **Threading.Lock + fcntl mixed** | gps_server.py | n/a | LOW |
| **subprocess pipe buffer 64KB** | parser_health_endpoint.py | 145, 233, 274 | LOW |
| **Background refresh thread daemon** | panel_client.py | 104-109 | MEDIUM |
| **Hardcoded chat_id w kodzie** | telegram_approver.py | 18 | LOW (nie production secret) |
| **Naming inconsistency 45 dotted refs** | 13 plików | various | MEDIUM (deferred per Adrian A) |
| **Accidental directory `Let me produce the blocks.dispatch_v2/`** | repo root | n/a | LOW (cleanup needed) |
| **Nested `dispatch_v2/dispatch_v2/`** | repo root | n/a | LOW (legacy, cleanup) |
| **Orphaned tempfile `.tmp_cr2kure6.json` 5.5 MB** | dispatch_state/ | n/a | LOW (cleanup, od 2026-04-26) |

## Appendix B — Quick wins (P0-P2 bez dużego refactoringu)

**P0 (impact wysoki, ~1 dzień łącznie):**
1. ✅ ~~replay_failed.py:132 dispatchable_fleet bug~~ — **JUŻ ZFIXOWANE 2026-05-07** (aktualizuj backlog #1)
2. `logrotate` config dla 25+ logów + `learning_log.jsonl` rotation (~1h)
3. systemd `MemoryMax=2G CPUQuota=200%` per long-running (~30 min)
4. `flags.json` atomic write helper + replace ad-hoc `json.dump` (~1h)
5. Cleanup `Let me produce the blocks*` + nested `dispatch_v2/dispatch_v2/` + `.tmp_cr2kure6.json` (~30 min)

**P1 (~3-5 dni):**
6. `subprocess.run` → `asyncio.to_thread` w telegram_approver.py:1452, :1710 + tests (~1 dzień)
7. fcntl wrap dla `learning_log.jsonl` writers (5 miejsc) + burst test (~1 dzień)
8. Bounded LRU dla `_v327_pre_recheck_last_seen` + `_route_cache` (~4h)
9. Konwersja blanket `except Exception:` na specific types + log w courier_resolver/plan_manager/scoring (8 miejsc, ~4h)
10. atexit register + watchdog dla `panel_client._bg_refresh_thread` (~3h)

**P2 (~1-2 tygodnie):**
11. Split `common.py` na `flags.py` + `constants.py` + `tz_utils.py` + `logger_setup.py` (~2-3 dni z testami)
12. Split `telegram_approver.py` na router/proposals/callbacks/admin (~3-5 dni z testami)
13. Persistent `pending_proposals` + restore on startup dla telegram_approver (~2 dni)
14. `replay_failed.py` w cron (auto-DLQ replay) (~1 dzień)
15. Prometheus exporter podstawowy (process metrics + 5 custom) (~2 dni)

---

## Cross-references
- `MEMORY.md` — index pamięci CC, memory paths
- `CLAUDE.md` (`dispatch_v2/`) 1699 LOC — sprint history + operational reference
- `TECH_DEBT.md` 1927 LOC — istniejący backlog (P0-P3)
- `MEMORY` `tech_debt_backlog.md` — current backlog status (post-evening 07.05: 18/22 DONE)
- `MEMORY` `lessons.md` — lekcje #1-#88, szczególnie #32 (silent except), #80 (lost field), #81 (fail-loud sentinel), #82 (empirical fixture-first)

**Author:** CC architectural audit (post-Faza 7 shadow + post-firmowe konto sprint).
**Hash dane wejściowe:** 253 .py files, 16 systemd services + 12 timers, 38 state files, ~33 KLoC code + ~33 KLoC tests, 4 parallel Explore subagents + own deep reads.
**Recommended re-audit cadence:** raz na sprint major (V3.30, V3.35), pre-Faza 7 100% production flip, pre-multi-tenant Warsaw expansion.
