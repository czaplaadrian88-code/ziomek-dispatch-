# Multi-Tenant Readiness & Scalability Audit — Z3 Strategy

**Data:** 2026-05-07 wieczór
**Scope:** Scenariusz 10× wzrostu ruchu (3000 orderów/d) + wejście do Warszawy (nowy tenant) + Restimo + Wolt Drive integration
**Owner:** CC architectural review (request: Adrian)
**Cross-ref:**
- `ARCHITECTURE_AUDIT_2026-05-07.md` — całościowa mapa systemu (16 services + 12 timers)
- `CONCURRENCY_DATA_INTEGRITY_AUDIT_2026-05-07.md` — Hot Spot #1 (`learning_log.jsonl`) + #11 (`flags.json`) + SQLite WAL contention
- `META_AUDIT_ROOT_CAUSES_ROADMAP_2026-05-07.md` — RC1-RC6 + 6-mies roadmapa
- `STATE_OWNERSHIP_EVENT_FLOW_AUDIT_2026-05-07.md` — state ownership emergent
- `STRATEGIC_RISK_SYNTHESIS_2026-05-07.md` — top 20 ryzyk P×I
- `OPERATIONAL_RESILIENCE_AUDIT_2026-05-07.md` — liveness contracts
- `TELEGRAM_APPROVER_GOD_OBJECT_ASYNC_AUDIT_2026-05-07.md` — telegram refactor

**Routing decision:** SELF (architectural strategy / decision engine task per CLAUDE.md routing rule).

**Scope review — interpretacja "Hot Spot" w pytaniu Adriana:**
- **"Hot Spot #4"** = `_COORDS` w `panel_watcher.py:67` (Adrian explicit named — module-level cache dict bez TTL).
- **"Hot Spot #8"** interpretowany jako drugi hot in-memory cache class — dwa najmocniejsze kandydaty: **schedule cache T3** (10-min hot-refresh `load_schedule()`, single Sheet ID hardcoded) i **OSRM cache** (60min TTL, RLock-protected). Analizuję oba jako jeden cache-class problem (B.4).

---

## Spis treści

1. [TL;DR (4 linie)](#tldr-4-linie)
2. [A. Hardcoded Limits — multi-tenant blocker inventory](#a-hardcoded-limits--multi-tenant-blocker-inventory)
3. [B. Cache Drift — Hot Spot #4 + #8 + CONFIG_RELOAD design](#b-cache-drift--hot-spot-4--8--config_reload-design)
4. [C. SQLite Lock Contention — moment krytyczny przy 10× scaling](#c-sqlite-lock-contention--moment-krytyczny-przy-10-scaling)
5. [D. Resource Constraints — systemd MemoryMax/CPUQuota](#d-resource-constraints--systemd-memorymax--cpuquota)
6. [E. Must-fix przed Restimo (priority lista)](#e-must-fix-przed-restimo-priority-lista)
7. [F. Tabela `tenants` w docelowym Postgresie](#f-tabela-tenants-w-docelowym-postgresie)
8. [Decyzja routing + co dalej](#decyzja-routing--co-dalej)

---

## TL;DR (4 linie)

1. **Multi-tenant blocker count = 23 hardcoded** odwołań do BIAŁYSTOK + 3 hardcoded ID-y (Adrian=21, Bartek=123, Koordynator=26) + 1 frozenset `FIRMOWE_KONTO_ADDRESS_IDS={161}` + 1 hardcoded Sheet ID + 1 hardcoded Telegram chat (-5149910559). Każdy = osobny dotyk przy wprowadzaniu Restimo/Warsaw.
2. **Cache drift TERAZ jest realny:** `_COORDS` w panel_watcher.py:67 ładuje się **raz na boot** bez TTL i bez reloadu — nowa restauracja wymaga `systemctl restart dispatch-panel-watcher` (peak-blackout violation). Przy 10× ruchu i 3 tenantach jeden plik = N tenants × cache copy w pamięci, **synchronizacja ZERO**.
3. **SQLite WAL umrze w okolicy 30–50 sustained writes/sec** (NIE 100) — przy obecnym `cleanup()` 100K-row DELETE wisi exclusive lock 5-15s; w 10× ruchu cleanup wisi ~50-150s, busy_timeout=5000ms zwraca `OperationalError` w Telegramie. Szacunkowy moment krytyczny: **~700-900 orderów/d/tenant** lub **3 tenants × 250/d**.
4. **Server JEST W STANIE KRYTYCZNYM JUŻ TERAZ** (94% RAM, 100% swap, 95% disk), ZERO MemoryMax/CPUQuota w żadnym systemd unit. Multi-tenant 10× ZAMORDUJE box w pierwszym peaku. **Hetzner upgrade z CPX22→CPX32 nie wystarczy** — potrzeba CPX42 albo dedykowany Postgres host PRZED Restimo.

---

## A. Hardcoded Limits — multi-tenant blocker inventory

### A.1 Geo/city scope (23 wystąpienia, 13 plików)

| Symbol | Lokalizacja | Klasa |
|---|---|---|
| `BIALYSTOK_CENTER = (53.1325, 23.1688)` | `courier_resolver.py:33`, `chain_eta.py:28`, `bootstrap_restaurants.py:19`, `tests/test_v319f_pipeline.py:27` | **Coords constant** — duplikowany 4×, zero abstrakcji |
| `BIALYSTOK_DISTRICTS` (28 + 4 outside-city) | `districts_data.py:11`, `common.py:690`, `route_simulator_v2.py:190`, `ml_inference.py:577` | **Geo schema** — drop zone resolution; każde miasto ma inną listę osiedli |
| `BIALYSTOK_DISTRICT_ADJACENCY` (~74 pairs) | `common.py:695`, `dispatch_pipeline.py:380,429`, `same_restaurant_grouper.py:175`, `ml_inference.py:577` | **Adjacency graph** — corridor + bundle scoring; PER-CITY |
| `HAVERSINE_ROAD_FACTOR_BIALYSTOK = 1.37` | `common.py:140`, `feasibility_v2.py:23,61`, `dispatch_pipeline.py:25,1559,1661`, `osrm_client.py:28,171` | **Per-city physical constant** — Warsaw inna gęstość zabudowy/dróg |
| `LONG_HAUL_DISTANCE_KM = 99.0` (V3.27 R7 "wyłączone") | `common.py:295` | Constant nazwany pod Białystok ("było za agresywne dla Białystoku") |
| `DEFAULT_CITY = os.environ.get('ZIOMEK_DEFAULT_CITY', 'Białystok')` | `dispatch_pipeline.py:56` | **Single env knob** — jedyne miejsce z env override; reszta hardcoded |
| `geocode(addr, city="Białystok")` fallback | `geocoding.py:163`, `panel_watcher.py:485`, `czasowka_proactive/state.py:21` | Hardcoded fallback gdy `CITY_AWARE_GEOCODING=False` |
| `bbox Białystok+15km` | `tools/invalidate_city_bugged_geocodes.py:26` | Hardcoded box dla cleanup tool |

**Multi-tenant verdict:** żadna struktura `cities/` ani `tenants/` — każdy kawałek geo wpisany płasko w globalny moduł. **Wprowadzenie Warsaw wymaga refactoru ~13 plików**, nie nowej konfigi.

### A.2 ID-y hardcoded (sieci stosunkowo małe, ale rozproszone)

| Symbol | Lokalizacja | Klasa |
|---|---|---|
| `FIRMOWE_KONTO_ADDRESS_IDS = frozenset({161})` | `common.py:1603` (komentarz "per-tenant ready" ale **frozenset hardcoded**) | Address_id specyficzny dla Nadajesz Białystok; Restimo ma inny system address ID |
| `FIRMOWE_KONTO_FALLBACK_COORDS = (53.13222, 23.16844)` | `common.py:1612` | Coords centrali Nadajesz.pl Białystok (53°07'56"N 23°10'06"E) |
| `EXCLUDED_CIDS = {21, 23, 26, 61, 207, 284, 354, 426, 476, 498}` | `daily_accounting/config.py` | **PER-TENANT** lista; Restimo ma inne ID |
| `id_kurier=26` Koordynator virtual | hardcoded w panel mapping (`project_overview.md` doc) | Virtual courier ID dla czasówek — Restimo ma inny holding bucket |
| `Adrian Telegram ID = 8765130486`, `Bartek = 8753482870`, `Grupa = -5149910559` | `flags.json:KONIEC_AUTHORIZED_USER_IDS`, code comments | **PER-TENANT** chat & ops users |
| `Sheet ID 1Z5kSGUB0Tfl1TiUs5ho-ecMYJVz0-VuUctoq781OSK8` + `gid 533254920` | hardcoded jako konstant w `match_courier`/schedule loader | Per-tenant grafik kurierów |

**Multi-tenant verdict:** łącznie ok. **30+ atomowych wartości** hardcoded — przy Restimo trzeba zrobić nie nowy plik konfiguracji (którego nie ma), tylko **wymyślić warstwę konfiguracji**. To jest brakująca abstrakcja `tenants/<name>/config.{py,json}`.

### A.3 Co Z3 JUŻ ZROBIŁO (chwała)
- `ENABLE_*` flagi wszystkie env-overridable + `flags.json` hot-reload — to jest **JEDYNY** dojrzały punkt konfiguracji multi-tenant.
- `flags.json` umie boolean per-flag flip; brakuje: per-tenant scope (jeden `flags.json` = jeden tenant teraz).

---

## B. Cache Drift — Hot Spot #4 + #8 + CONFIG_RELOAD design

### B.1 `_COORDS` w `panel_watcher.py:67` — pełna analiza

```python
_COORDS_PATH = "/root/.openclaw/workspace/dispatch_state/restaurant_coords.json"
_COORDS = {}
def _load_coords():
    global _COORDS
    try:
        with open(_COORDS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        _COORDS = {str(k): (v["lat"], v["lng"]) for k, v in data.items() if "lat" in v and "lng" in v}
    except Exception as e:
        _log.warning(f"_load_coords fail: {e}")
        _COORDS = {}
_load_coords()  # ← raz, na imporcie modułu
```

**Klasa problemu:**
1. **Brak TTL** — żadna pętla nie sprawdza mtime pliku.
2. **Brak invalidation** — dodanie restauracji = `restart dispatch-panel-watcher` (peak-blackout violation 11-14, 17-20 Pn-Pt + 16-21 Sb).
3. **Brak per-tenant scope** — `_COORDS` to single global dict; multi-tenant da kolizję `address_id` (różne tenantsy reuse'ują numerację).
4. **Współbieżność z innymi consumerami** — `_COORDS` używany w `panel_watcher:461,1141`. Inne procesy (`shadow_dispatcher`, `czasowka_scheduler`) mają **swoją kopię** lub nie mają wcale (różne entry pointy → drift).
5. **Lekcja #80 reinforcement** — pole produkowane w jednym miejscu, konsumowane w wielu, **bez audytu spójności**. Dokładnie ten sam pattern co czasowka_scheduler:285 fix 06.05.

### B.2 Wpływ na 10× scaling (3000 orderów/d, 3 tenants)
- **Na poziomie pamięci:** restaurant_coords.json = 26KB obecnie. 10× (300+ restauracji × 3 tenants) = ~250KB. Dla `_COORDS` dict to ~2-3MB w RAM **per process** — dziś `dispatch-panel-watcher` + `dispatch-shadow` + `dispatch-czasowka` × 3 tenants = **9 procesów × 3MB = 27MB** zduplikowanej pamięci. Trywialne ale nieelegant.
- **Na poziomie operacyjnym (gorzej):** każda nowa restauracja Restimo = restart kaskady serwisów = **utrata in-flight events** (per peak window hard rule). To jest **prawdziwy blocker**, nie pamięć.

### B.3 Propozycja: `CONFIG_RELOAD` event przez `events.db`

**Architektura (eksportowalna multi-tenant):**

```python
# core/config_reloader.py — NOWY shim
class ConfigReloader:
    """Subskrybuje events.db CONFIG_RELOAD events.
    Każdy proces wywołuje reload_callback() na pasujący scope."""

    def __init__(self, scopes: set[str], callbacks: dict[str, Callable]):
        # scopes = {"coords", "schedule", "tiers", "flags"}
        # callbacks = {"coords": _load_coords, "schedule": load_schedule.cache_clear, ...}
        self.scopes = scopes
        self.callbacks = callbacks
        self._last_seen_event_id = self._get_max_event_id()

    def poll(self) -> int:  # wołane co 1-3s w main loop
        new = event_bus.fetch_after(
            event_type="CONFIG_RELOAD",
            after_event_id=self._last_seen_event_id
        )
        for ev in new:
            scope = ev.payload.get("scope")
            tenant = ev.payload.get("tenant", "default")
            if scope in self.scopes and tenant == os.environ["TENANT_ID"]:
                self.callbacks[scope]()
                _log.info(f"CONFIG_RELOAD applied: {scope} (tenant={tenant})")
        if new:
            self._last_seen_event_id = new[-1].event_id
        return len(new)
```

**Trigger emit (telegram cmd, admin script):**
```python
# /reload_coords command handler
event_bus.emit("CONFIG_RELOAD", payload={
    "scope": "coords",
    "tenant": os.environ["TENANT_ID"],
    "triggered_by": user_id,
    "reason": "added_restaurant_id_472"
})
```

**Wlepienie w panel_watcher main loop:**
```python
# panel_watcher main loop, co 3s i tak ticka
if cycle_count % 5 == 0:  # ~ co 15s
    config_reloader.poll()
```

**Kluczowe gwarancje:**
- **Cross-process broadcast** — events.db jest jedynym IPC bus już używanym, dodatkowy event type = zero infra cost.
- **Per-tenant scope** — payload.tenant filter eliminuje cross-tenant noise.
- **Idempotent** — `_last_seen_event_id` prevent double-apply.
- **Audit trail** — każdy reload logged z `triggered_by` (debug + Lekcja #54 type problems gone).
- **Observable** — CONFIG_RELOAD widoczny w `audit_log` + dashboard.

**Effort:** ~3-4h (impl 60 LOC + tests 80 LOC + 3-callsite migration: panel_watcher coords, schedule cache, tier_suggestions cache).

**Side benefits Z3:**
- Eliminuje większość peak-blackout restartów (każdy `restart dispatch-X` po config change → reload event).
- Pattern eksportowalny — Mailek może subskrybować `CONFIG_RELOAD` dla `golden_v3.md` segments.
- Postgres migracja: zamień `events.db` na `LISTEN/NOTIFY` w PG — implementacja kontraktu się nie zmienia.

### B.4 Schedule + OSRM cache — analiza (Hot Spot #8 candidate)

| Cache | TTL | Invalidation | Multi-tenant ready |
|---|---|---|---|
| `load_schedule()` (T3 hot-refresh) | 10 min | mtime-driven | NIE — single Sheet ID hardcoded |
| `osrm_client cache` | 60 min (V3.26) | naturalny (TTL) | TAK (pure function input → output) |
| `tier_suggestions.json` reader | brak | brak (dziś `r04_evaluator` regenerator daily 03:00) | NIE — single file |
| `geocoding cache` | persistent JSON | invalidate via `tools/invalidate_city_bugged_geocodes.py` | NIE — bbox hardcoded |

**OSRM cache** jest jedynym cache który się **prawdziwie skaluje** (pure function semantics). Reszta wymaga `CONFIG_RELOAD` + per-tenant path prefix.

---

## C. SQLite Lock Contention — moment krytyczny przy 10× scaling

### C.1 Empiryczny baseline (07.05.2026)

| Metric | Value |
|---|---|
| events.db size | 22 MB |
| events count | 2,405 |
| audit_log count | 12,636 (5.3× events) |
| processed_events | 2,405 |
| audit_log dominuje storage | 2.34 MB (44% DB size) |
| `busy_timeout` | 5,000 ms |
| WAL mode | ON |

### C.2 Realny throughput dziś vs 10×

**Dziś (~250 orderów/d Białystok):**
- ~10 events/order × 250 = **2,500 events/d** = 0.029 ev/s avg
- Peak burst (lunch 11-14 + dinner 17-20) ~5× avg = **~0.15 ev/s sustained, ~3-5 ev/s peak** (multi-event per dispatch decision)
- audit_log: 5.3× events = **0.8 wpisów/s peak**
- **Łącznie: ~4-6 writes/s peak**

**10× (3000 orderów/d):**
- 30,000 events/d = 0.35 ev/s avg
- Peak burst 5× = **1.7 ev/s sustained, 30-50 ev/s peak** (kilka tenantów uderza równocześnie)
- audit_log: 5.3× = **160-265 wpisów/s peak**
- **Łącznie: ~190-315 writes/s peak**

**Adrian's "100 zapisów/sek" — nie jest abstrakcyjne**: to liczba peak-burst dla 10× gdy 3 tenants uderzają w to samo okno + audit log.

### C.3 Moment krytyczny — kalkulacja

**Trzy poziomy degradacji:**

| Throughput | Stan | Manifestacja |
|---|---|---|
| <10 writes/s | OK | Obecny stan; `busy_timeout=5000` nigdy nie fires |
| 10-30 writes/s | YELLOW | `cleanup()` w peaku zaczyna powodować sporadyczne `OperationalError` w Telegram callbacks (1-3% miss rate); nie widoczne dla operatora |
| 30-50 writes/s | ORANGE | Reconcile worker batch INSERT trzyma writer lock 5-15s; każdy współbieżny `event_bus.emit` w callbackach **przekracza busy_timeout** → permanent event loss bez retry decorator. **Tutaj zaczynają się dziury w audit trail**. |
| 50-100 writes/s | RED | WAL append się stacza, fsync queue rośnie, dysk syscall latency >100ms; każdy emit() jest ~50-50 czy się zmieści |
| >100 writes/s | DEAD | SQLite WAL nie obsłuży tego sustained; 5s busy_timeout raise ~80% callerów; system efektywnie nieoperacyjny w peak |

**Moment krytyczny dla Białystok solo:** ~700-900 orderów/d (×3-4 obecnego).
**Moment krytyczny dla 3 tenants (BIA + WAW + REST):** ~250 orderów/d/tenant × 3 = 750/d **łącznie** — dokładnie tam gdzie Adrian celuje w Q3.

### C.4 Wąskie gardła w 5 najgorszych scenariuszach

| Scenariusz | Czas trzymania writer lock | Częstotliwość | Krytyczność dla 10× |
|---|---|---|---|
| `cleanup()` 100K-row DELETE (retention 90d audit) | 5-15s dziś, **50-150s w 10×** | dzienne | **CRITICAL — peak-aware guard mandatory** |
| Reconcile worker batch INSERT 200 ghost cleanup | 1-5s dziś, **10-30s w 10×** | rzadkie | HIGH — wymaga batch size cap + commit-per-N |
| WAL checkpoint mid-batch (4MB threshold) | <100ms dziś, **500ms-2s w 10×** | implicit | MED — `wal_autocheckpoint` tunable |
| Disk I/O saturation (CPX22 IO peak) | 1-10s już dziś | rzadkie | **CRITICAL** w 10× bez upgrade Hetzner |
| fsync na pełnym disk (95% full!) | 5-30s | rzadkie ale nieuchronne | **CRITICAL — disk full TODAY** |

**Wniosek:** SQLite WAL **nie wytrzyma 100/sec sustained**. Wytrzyma 30-50/sec w optimal disk + peak-aware cleanup. Powyżej tego trzeba PG.

### C.5 Konkretne mitigacje pre-Postgres (2-3 tygodnie effort)

1. **`busy_timeout` 5000 → 15000ms** (1-line) — tolerancja 3× longer holdów. Trade-off: więcej CPU spin.
2. **`event_bus.emit()` retry decorator** (Backlog #22, 30 min, P0) — `@with_retry(3, exp_backoff=[100, 500, 2000ms])` na callbackach Telegram.
3. **`cleanup()` peak-aware guard** (Backlog #25, 15 min, P0) — `if _is_peak_window(): return 0` — eliminuje 80% długich holdów w peakach.
4. **`PRAGMA synchronous=NORMAL`** (1-line) — szybszy WAL append; minimalny crash-safety regression dla append-only audit.
5. **`PRAGMA wal_autocheckpoint=10000`** (z 1000 default) — checkpoint co 10K stron zamiast co 1K → mniej burst exclusive locków.
6. **Batch size cap reconcile worker** — `commit-per-50-rows` zamiast monolithic transaction.

**Po tych 6 mitigations:** SQLite WAL bezpiecznie do **40-60 writes/s peak** = wystarczy dla 2 tenants. Dla 3+ tenants Postgres mandatory.

---

## D. Resource Constraints — systemd MemoryMax / CPUQuota

### D.1 Stan dziś (KRYTYCZNY)

```
Memory:  7.6Gi total / 6.6Gi used / 394Mi free  → 87% RAM saturation
Swap:    4.0Gi / 4.0Gi  → 100% swap full (THRASHING zone)
Disk:    38G / 34G used → 95% disk full
Load:    1.31 (4 vCPU)
```

**Audyt 32 systemd units:**
```
grep -E "MemoryMax|CPUQuota|MemoryHigh|TasksMax|LimitNOFILE" /etc/systemd/system/dispatch-*
→ ZERO matches
```

**Każdy proces ma unbounded memory growth.** Pierwszy OOM zabije losowy proces (OOM killer), zwykle ten z największym RSS — co dziś jest `dispatch-shadow` (ortools warm-up) lub `dispatch-panel-watcher` (cache + active orders cache). Po zabiciu: `Restart=on-failure` próbuje 10s później → ten sam OOM → **kaskadowy fail z 3-5 service'ów w 1-2 min**, podczas peak.

### D.2 Profile per-service — propozycja MemoryMax / CPUQuota

Bazuję na obecnym RSS (`systemctl status` typical ~150-300MB dla większości, panel-watcher peakuje ~600MB pod ortools warm-up):

| Service | Typ | MemoryMax | CPUQuota | TasksMax | LimitNOFILE | Uzasadnienie |
|---|---|---|---|---|---|---|
| `dispatch-shadow` | daemon | **1.5G** | **150%** (1.5 vCPU) | 256 | 4096 | ortools warm-up + parallel ThreadPoolExecutor 10 workers; potrzebuje headroom |
| `dispatch-panel-watcher` | daemon | **800M** | **80%** | 128 | 2048 | cache + active orders + HTTP session pool; mniej ortools |
| `dispatch-telegram` | daemon | **600M** | **50%** | 128 | 2048 | Telegram bot + callback queue; nie OR-Tools |
| `dispatch-czasowka` | oneshot timer | **400M** | **80%** | 64 | 1024 | Per-tick fresh proces, bag size limited |
| `dispatch-shift-notify` | oneshot timer | **300M** | **40%** | 64 | 512 | Mała skrzynka, T-60/T-30/T-60 worker |
| `dispatch-sla-tracker` | daemon | **300M** | **30%** | 64 | 1024 | Lekka pętla |
| `dispatch-gps` | daemon | **400M** | **40%** | 128 | 2048 | GPS upload aggregator |
| `dispatch-monitor-419` | daemon | **150M** | **20%** | 32 | 256 | Lekki monitor |
| `dispatch-r04-evaluator` | oneshot daily | **600M** | **100%** | 64 | 1024 | Heavy compute raz dziennie |
| `dispatch-state-reconcile` | timer | **400M** | **60%** | 64 | 1024 | Batch DB ops |
| `dispatch-event-bus-cleanup` | timer | **300M** | **40%** | 32 | 512 | DELETE batch op |
| `dispatch-overrides-reset` | daily timer | **100M** | **20%** | 16 | 256 | Trywialny reset script |
| `dispatch-daily-accounting` | daily timer | **800M** | **100%** | 64 | 1024 | gspread read/write batch |
| `dispatch-cod-weekly` | weekly timer | **800M** | **100%** | 64 | 1024 | gspread bulk |
| `dispatch-plan-recheck` | timer | **300M** | **40%** | 32 | 512 | V3.19c plan re-check |
| `mailek-telegram-listener` | daemon | **600M** | **50%** | 128 | 2048 | Mailek inbound |

**Suma MemoryMax (worst-case wszystkie hit limit):** ~7.0GB. **Headroom kernel + cache:** 600MB. **Realistyczny working set (typical):** ~3.5GB. Mieści się w 8GB CPX32, **NIE mieści się w obecnym 8GB CPX32 dziś** bo serwer jest już zatłoczony innymi rzeczami (gateway, etc.).

### D.3 Konkretny drop-in pattern

```ini
# /etc/systemd/system/dispatch-shadow.service.d/resource_limits.conf
[Service]
MemoryMax=1500M
MemoryHigh=1200M
CPUQuota=150%
TasksMax=256
LimitNOFILE=4096
# Restart policy hardening
Restart=on-failure
RestartSec=15
StartLimitBurst=3
StartLimitIntervalSec=300
# OOM handling — preferuj swoje "nice" raise zamiast unbounded:
OOMScoreAdjust=200
# Watchdog defense (pre-existing TimeoutStopSec=60 zachowane):
WatchdogSec=120
```

**Kluczowe wnioski:**
- `MemoryHigh < MemoryMax` — soft limit przed OOM, daje memory pressure feedback (memcg backpressure).
- `OOMScoreAdjust=200` — ten proces dostaje OOM kill BEFORE kernel-critical processes.
- `StartLimitBurst=3` — po 3 fails w 5 min systemd przestaje restartować → alert do Telegrama (nie kaskada).

### D.4 Pre-Restimo deployment plan (1-2h)

```bash
# Step 1: per-service drop-in z limitami
mkdir -p /etc/systemd/system/dispatch-shadow.service.d/
# write resource_limits.conf per powyższa tabela
# … powtórz dla 16 serwisów

# Step 2: daemon-reload + per-service restart (off-peak only)
systemctl daemon-reload
# Restart 1×: dispatch-shadow + panel-watcher + telegram (peak-blackout sensitive!)
# Reszta: oneshot timers absorbują na następnym ticku

# Step 3: 24h obserwacja
journalctl -u dispatch-* | grep -iE "memory|killed|oom|cgroup"

# Step 4: alert hook
# Drop-in OnFailure=alert-cgroup-failure@%i.service → telegram alert
```

---

## E. Must-fix przed Restimo (priority lista)

**Total ~15-20h, 3-4 sesje.**

| Pri | Item | Effort | Tag |
|---|---|---|---|
| **P0-INFRA** | Disk cleanup do <80% (restaurant_coords backup, .bak files >24h, learning_log.jsonl truncate >30d) | 1h | `disk-cleanup-pre-restimo` |
| **P0-INFRA** | Hetzner upgrade CPX22→CPX42 (8 vCPU, 16GB) — CPX32 niewystarczy dla 3 tenants | manualne 30min | `hetzner-cpx42-upgrade` |
| **P0-SAFETY** | systemd MemoryMax/CPUQuota dla 16 serwisów (sekcja D) | 1.5h | `systemd-resource-limits` |
| **P0-SAFETY** | `event_bus.emit()` retry decorator (Backlog #22) | 30 min | `event-bus-retry-callbacks` |
| **P0-SAFETY** | `cleanup()` peak-aware guard (Backlog #25) | 15 min | `event-bus-cleanup-peak-aware` |
| **P1-MULTI-TENANT** | `core/tenant_config.py` shim + `tenants/<name>/config.py` per-tenant scope | 4h | `tenant-config-scaffold` |
| **P1-MULTI-TENANT** | Refactor 23 BIAŁYSTOK refs → `tenant.geo.center / districts / adjacency / road_factor` | 6h (AIDER) | `geo-tenant-scoped` |
| **P1-MULTI-TENANT** | `_COORDS` + schedule + tier cache → `CONFIG_RELOAD` event subscriber (sekcja B.3) | 3h | `config-reload-event-subscriber` |
| **P1-DB** | `core/jsonl_appender.py` (Backlog #23) — pre-rec dla audit log integrity | 3h | `jsonl-appender-atomic` |
| **P2-DB** | Postgres infra setup (CPX42 same-host) + dual-write events.db (Faza 1 z META audit) | 8h sprint | `pg-faza-1-dual-write` |
| **P2-OBSERVE** | Per-service cgroup memory dashboard + alert na MemoryHigh hits | 2h | `cgroup-observability` |

**Kolejność deployowania:**
1. **Tydzień 1 (08-14.05):** P0-INFRA (disk + Hetzner upgrade) + P0-SAFETY (systemd + event_bus retry + cleanup peak-aware)
2. **Tydzień 2-3 (15-28.05):** P1-MULTI-TENANT (tenant_config + geo refactor + CONFIG_RELOAD)
3. **Tydzień 4 (29.05-04.06):** P1-DB jsonl_appender + Postgres setup
4. **Q3 ramp:** P2-DB Postgres dual-write + flip read paths

**Budżet:** ~20-25h dispatch + Adrian decision on Hetzner upgrade. **Bez tego pierwszy peak Restimo zabija box.**

---

## F. Tabela `tenants` w docelowym Postgresie

```sql
-- core/sql/0001_tenants_schema.sql
-- Tenant = jeden klient (Białystok / Warsaw / Restimo). Każdy ma własne:
--   geo, kurierów, restauracje, schedule, Telegram, business rules.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE tenants (
    -- Identity
    tenant_id           TEXT PRIMARY KEY,                    -- 'bialystok' | 'warsaw' | 'restimo_bia'
    display_name        TEXT NOT NULL,                       -- 'NadajeSz Białystok'
    status              TEXT NOT NULL DEFAULT 'active'
                            CHECK (status IN ('active','paused','offboarded')),

    -- Provenance
    onboarded_at        TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    offboarded_at       TIMESTAMPTZ,
    owner_email         TEXT NOT NULL,                       -- 'ac@nadajesz.pl'

    -- Geo bindings (per-tenant)
    city                TEXT NOT NULL,                       -- 'Białystok' | 'Warszawa'
    country             TEXT NOT NULL DEFAULT 'PL',
    timezone            TEXT NOT NULL DEFAULT 'Europe/Warsaw',
    center_coords       POINT NOT NULL,                      -- (53.1325, 23.1688) Białystok center
    bounding_box        BOX NOT NULL,                        -- ((min_lat,min_lon),(max_lat,max_lon))
    haversine_road_factor NUMERIC(4,3) NOT NULL DEFAULT 1.37,-- per-city physical const

    -- Source platform binding
    source_platform     TEXT NOT NULL                        -- 'rutcom' | 'restimo' | 'wolt_drive'
                            CHECK (source_platform IN ('rutcom','restimo','wolt_drive','custom')),
    panel_base_url      TEXT NOT NULL,                       -- 'https://gastro.nadajesz.pl'
    panel_credentials_secret_ref TEXT NOT NULL,              -- pointer do .secrets/{tenant}.env

    -- Operational ID-y (per-tenant)
    koordynator_courier_id  INTEGER,                         -- Bialystok=26 (czasówka holding)
    firmowe_konto_address_ids INTEGER[],                     -- {161} dla BIA Nadajesz.pl
    firmowe_konto_fallback_coords POINT,                     -- (53.13222, 23.16844)

    -- Schedule integration
    schedule_provider   TEXT NOT NULL DEFAULT 'google_sheets'
                            CHECK (schedule_provider IN ('google_sheets','restimo_api','none')),
    schedule_sheet_id   TEXT,                                -- '1Z5kSGUB0Tfl...' BIA
    schedule_sheet_gid  TEXT,                                -- '533254920'
    schedule_ttl_seconds INTEGER NOT NULL DEFAULT 600,       -- 10 min hot-refresh

    -- Telegram bindings
    telegram_group_chat_id  BIGINT,                          -- -5149910559 BIA group
    telegram_bot_username   TEXT,                            -- '@NadajeszBot'
    telegram_authorized_user_ids BIGINT[] NOT NULL DEFAULT '{}',

    -- Business rules tunables (per-tenant override)
    business_rules      JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Schema:
    --   { "r35min_max": 35,
    --     "r1_delivery_spread_km": 8.0,
    --     "r5_pickup_spread_km": 1.8,
    --     "r6_bag_time_hard_max_min": 35,
    --     "wave_matrix": { "gold_off_peak": [2,4], ... },
    --     "peak_windows": { "weekday": ["11:00-14:00","17:00-20:00"], "saturday": ["16:00-21:00"] }
    --   }

    -- Feature flags scope
    flags               JSONB NOT NULL DEFAULT '{}'::jsonb,
    -- Subset of global flags overridable per-tenant.
    -- Lookup precedence: flags[tenant_id] OR flags[default] OR env OR const.

    -- Resource quotas (op limit per-tenant)
    quota_orders_per_day_soft INTEGER,                        -- alert threshold
    quota_orders_per_day_hard INTEGER,                        -- reject above
    quota_writes_per_sec_burst INTEGER NOT NULL DEFAULT 10,   -- rate limit

    -- Audit & compliance
    data_retention_days INTEGER NOT NULL DEFAULT 90,
    pii_masking_level   TEXT NOT NULL DEFAULT 'standard'
                            CHECK (pii_masking_level IN ('none','standard','strict')),

    -- Soft-delete + audit trail
    created_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at          TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_by          TEXT NOT NULL DEFAULT 'system'
);

CREATE INDEX idx_tenants_status         ON tenants(status) WHERE status = 'active';
CREATE INDEX idx_tenants_source         ON tenants(source_platform);
CREATE INDEX idx_tenants_city_country   ON tenants(city, country);

-- Per-tenant geo extension table (1:N — districts to dużo)
CREATE TABLE tenant_districts (
    tenant_id       TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    district_name   TEXT NOT NULL,                           -- 'Centrum', 'Antoniuk', etc.
    district_data   JSONB NOT NULL,                          -- streets, center_coords, etc.
    is_outside_city BOOLEAN NOT NULL DEFAULT FALSE,
    PRIMARY KEY (tenant_id, district_name)
);

CREATE TABLE tenant_district_adjacency (
    tenant_id       TEXT NOT NULL REFERENCES tenants(tenant_id) ON DELETE CASCADE,
    district_a      TEXT NOT NULL,
    district_b      TEXT NOT NULL,
    PRIMARY KEY (tenant_id, district_a, district_b),
    CHECK (district_a < district_b)  -- canonical ordering, brak duplikatu (a,b)+(b,a)
);

-- Wszystkie istniejące tabele MUSZĄ dostać `tenant_id` jako prefix klucza:
--   orders.tenant_id, events.tenant_id, audit_log.tenant_id, courier_id (PK = tenant_id+cid)
-- Row-level security ENABLED:
ALTER TABLE orders ENABLE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON orders
    USING (tenant_id = current_setting('app.current_tenant'));
-- Każda sesja PG ustawia: SET app.current_tenant = 'bialystok';
```

**Kluczowe decyzje schematyczne:**

1. **`tenant_id` jako TEXT (nie UUID/INT)** — czytelne w logach, prefix do ścieżek (`/tenants/bialystok/...`), debugowalne.
2. **Soft-delete (`status='offboarded'`)** zamiast DELETE — Restimo offboardowanie ≠ data deletion (compliance).
3. **`business_rules JSONB`** — uniknia sztywnego schematu na 21 reguł × 3 tenants × peak/off-peak — JSON daje elastyczność per-tenant tuning bez migracji.
4. **`tenant_districts` + `adjacency` jako separate tables** — Białystok 28 + 4 outside, Warsaw może mieć 60+. Skala wymaga JOIN-friendly.
5. **Row-Level Security (RLS)** — hard-enforce isolation w bazie, nie na poziomie aplikacji. Catastrophic case: bug w `WHERE tenant_id=...` → PG hard-blocks zamiast leak.
6. **`quota_*` columns** — runtime rate limiting per-tenant; gdy Restimo skoczy do 5000/d a my mamy 3000 capacity, drop graceful zamiast totalna degradacja.
7. **`flags JSONB` per-tenant** — scope flags hot-reload (CONFIG_RELOAD event z sekcji B.3 staje się PG `LISTEN/NOTIFY` w tej fazie).

**Bootstrap row dla Białystoku (existing system):**
```sql
INSERT INTO tenants VALUES (
    'bialystok', 'NadajeSz Białystok', 'active',
    '2024-01-01', NULL, 'ac@nadajesz.pl',
    'Białystok', 'PL', 'Europe/Warsaw',
    POINT(53.1325, 23.1688),
    BOX(POINT(52.95, 22.85), POINT(53.30, 23.50)),
    1.37,
    'rutcom', 'https://gastro.nadajesz.pl', 'bialystok',
    26, ARRAY[161]::INTEGER[], POINT(53.13222, 23.16844),
    'google_sheets', '1Z5kSGUB0Tfl1TiUs5ho-ecMYJVz0-VuUctoq781OSK8', '533254920', 600,
    -5149910559, '@NadajeszBot', ARRAY[8765130486, 8753482870]::BIGINT[],
    '{"r35min_max":35,"r1_spread_km":8.0,"r5_pickup_km":1.8,"r6_bag_time_max":35,"peak_windows":{"weekday":["11:00-14:00","17:00-20:00"],"saturday":["16:00-21:00"]}}'::jsonb,
    '{}'::jsonb,
    NULL, NULL, 10,
    90, 'standard',
    NOW(), NOW(), 'system'
);
```

---

## Decyzja routing + co dalej

**DECISION: SELF** wykonana — ten dokument jest architektonicznym audytem (strategy task per CLAUDE.md routing rule).

**Następne kroki — recommendation Z3:**

1. **Dziś / jutro (08.05)** — disk cleanup (P0): `find /root/.openclaw -name "*.bak-*" -mtime +1 -delete` + truncate `learning_log.jsonl >30d` + audit `events.db` reduces. **Bez tego dysk pęknie w 2-3 dni.**
2. **Jutro / pojutrze (08-09.05)** — systemd MemoryMax/CPUQuota deployment (16 services, off-peak window). Effort 1.5h. **Jedyna obrona przed kaskadowym OOM.**
3. **Tydzień 1 (10-14.05)** — Hetzner upgrade decyzja + event_bus retry decorator + cleanup peak-aware (Backlog #22, #25). Effort łącznie ~3h impl.
4. **Tydzień 2-3 (15-28.05)** — `core/tenant_config.py` scaffold + 23 BIAŁYSTOK refs refactor (AIDER, deepseek-coder; pełen plan ~6h impl) + `CONFIG_RELOAD` event subscriber.
5. **Tydzień 4+ (Q3 prep)** — Postgres infra Faza 1 dual-write z META audit roadmap; zacznij na CPX42 same-host.

**Następna sesja CC:** mogę bezpośrednio wystartować od **disk cleanup audyt** + **systemd resource limits draft** w jednym sprincie ~2h. To ratuje box przed kolejnym piątkowym peakiem.

---

**Cross-ref do istniejących audytów:**
- Sekcja B.3 (CONFIG_RELOAD) komplementarna do `core/state_io.py` consolidation (META audit M4)
- Sekcja C (SQLite scaling) extends `CONCURRENCY_DATA_INTEGRITY_AUDIT` sekcja 3
- Sekcja F (tenants schema) extends `META_AUDIT` Migration M1 (Postgres canonical store)
- Sekcja D (resource limits) extends `OPERATIONAL_RESILIENCE_AUDIT` (liveness contracts)

**Audit complete.**
