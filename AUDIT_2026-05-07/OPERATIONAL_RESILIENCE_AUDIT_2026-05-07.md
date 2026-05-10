# Operational Resilience Audit — Ziomek (07.05.2026)

**Scope:** dispatch_v2 + dispatch_state + 5 long-running services + 10 oneshot timers
**Method:** 3 równoległe Explore agenty (External API / State persistence / Observability) + memory cross-check
**Verdict:** ⚠️ system **NIE jest "operacyjnie niezniszczalny"** — 3× CRITICAL chicken-egg / silent-fail / restart-loss patterns

---

## TL;DR — 3 najgorsze ryzyka (zrób przed Faza 7 Etap 2 ramp 30%)

| # | Ryzyko | Symptom realny | Severity |
|---|--------|----------------|----------|
| **A** | **Telegram callback state lost post-restart** | Adrian klika "Akceptuj" na propozycję pre-restart → bot 404 callback expired → koordynator manualnie | 🔴 CRITICAL |
| **B** | **Cron timer silent death** (`overrides_reset` 03→07.05 4 dni) | 13 names EXCLUDED 4 dni (Bartek/Gabriel/Mateusz O gold) — Adrian sam zauważył przeglądając log | 🔴 CRITICAL |
| **C** | **Telegram bot down = brak detekcji** (chicken-egg) | Bot crashnie → nie da rady wysłać alertu o sobie → cisza, godziny zanim Adrian zauważy | 🔴 CRITICAL |

Pozostałe 9 HIGH/MED → patrz sekcja 4 niżej.

---

## 1. Risk matrix per oś z briefu

### 1.1 Partial failures (jedna komponenta down, reszta żyje)
- **Panel API down** → `panel_watcher` exception caught + WARN log + **brak alertu** (panel_client.py:207-221, 2 retry no backoff). Pipeline kontynuuje na stale state. Po >5 min stale fetch_age = **brak detekcji**, brak `STALE_PANEL_AGE` alert. **HIGH.**
- **OSRM down** → `osrm_client.py` ma circuit breaker (threshold=3, cooldown=60s) ALE Haversine fallback → `if coords is None: silent 6285km` (brak fail-loud sentinel). Scoring zwala kompletnie złe odległości jako "valid". **HIGH (silent corruption).**
- **Sheets API down/rate-limit** → `schedule_utils.load_schedule()` fail-open na cache (good), ALE brak `STALE_SCHEDULE_AGE` warn → schedule 8h stary = `is_on_shift()` nie widzi nowych shiftów = dispatcher pomija aktywnego kuriera. **HIGH.**
- **Telegram down** → 35s timeout single-shot (telegram_approver.py:161-170), brak retry/exponential. `send_admin_alert` też single-shot — Adrian alert nigdy nie dochodzi pod outage. **HIGH** (chicken-egg ryzyko C).
- **events.db lock contention** → WAL+busy_timeout=5000ms ✓ ale brak retry-on-busy w 200+ concurrent emit. Burst peak = silent skip. **MED.**

### 1.2 API degradation (slow, 5xx, 429)
- **Brak rate-limit awareness** dla Telegram (29 msg/sec hardcap API), Sheets (100 req/100sec), Google Geocoding (50 qps). Nie czytamy `Retry-After` headera (telegram_approver.py:161). Burst = banhammer od Telegrama na 1h+. **HIGH.**
- **Brak exponential backoff** w żadnej z 5 integracji. Linear retry → spam zewnętrznego API → eskalacja banu. **HIGH.**
- **Panel slow (60s+)** → fixed 15s timeout (panel_client.py:207) → fallback IGNORED_STATUSES → false-empty parse → cascade jak brak orderów. **MED.**

### 1.3 Parser failures
- **Layer 1-4 PARSER-RESILIENCE LIVE** (V3.28). Motion-aware ≥4, hour-of-day suppression <09:00 Warsaw. ✓ działa.
- **GAP po Lekcji #76:** active_ids fix był rdzenny (closed_ids exclude). ALE `parse_panel_html` zwraca empty dict gdy parser malfunction (panel_html_parser.py:47-75) → `unassigned_ids=[]` → dispatch myśli "0 orderów" zamiast "parser broken". **HIGH (cascade).**
- **v2 → v1 fallback silent** (panel_html_parser.py:319-324) — loguje ERROR ale zwraca v1 z hardcoded `46\d{4}` regex bug → 47XXXX miss bez alertu. **MED.**
- **Layer 4 endpoint na :8888 LIVE z motion-aware mirror** (commit `50f05b2` 07.05). Endpoint dashboard-friendly. ✓
- **#7 false-neg test DONE** (commit `03a4bdf`). Real stuck wykryje. ✓

### 1.4 Stale data
- **Brak age timestamps na cache snapshots:**
  - `panel_html` cache → **brak** `_fetched_at`. Caller nie wie 1s vs 1h.
  - `osrm_cache` TTL=60min (osrm_client.py:36) ale brak `cached_at` per entry — drift 60min latent.
  - `schedule_today.json` TTL=10min ale fail-open bez `staleness_warning` po N min.
  - `geocode_cache.json` **forever TTL** — adresy budynków renowowanych = silent zły coords.
- **HIGH**: dispatch_pipeline nie ma sygnału "działam na danych >X min starych".

### 1.5 Worker crashes
- **Telegram callback in-memory:** `telegram_approver` pending callbacks NIE persistowane na dysk → restart = wszystkie pending TAK/NIE/INNY buttons = 404. **CRITICAL (ryzyko A).**
- **Pre-recheck cache lost:** `dispatch_pipeline._v327_pre_recheck_last_seen: Dict` → restart = 5 min false-fresh propozycji. **MED.**
- **parser_health rolling window:** restart `panel_watcher` → 10-cycle window resetuje → false positives 2-3 min post-restart. **LOW** (suppressed przez motion-aware).
- **shadow_dispatcher heartbeat:** in-memory `last_processed_ts` — brak `/health/shadow` endpointu = stuck shadow = niewidzialny. **HIGH.**

### 1.6 Restart scenarios
- **systemd `Restart=always` policy** dla long-running (zakładane, nie zweryfikowane per service). Brak max-restart-loop watchdog → process flapuje co 5s = niewidoczne dla nikogo poza journalctl. **MED.**
- **Atomic writes:** `manual_overrides.py:37-45`, `state_machine.py:128-141`, `plan_manager.py:66-85`, `learning_analyzer.py:85-87` — wzorzec Lekcji #14 ✓.
- **Plain writes (NIE atomic):**
  - `pending_proposals.json` (telegram_approver, queue corruption ryzyko) — **HIGH**
  - `gps_server.py:77-82` — `write+flush` bez fsync — **MED** (rare update OK)
  - `flags.json` — plain — **LOW** (cold-reload tolerant)
- **Stale lock files:** `.lock` files nigdy nie czyszczone post-crash. fcntl release on process death OK, ale `manual_overrides.json.lock` wymaga `dispatch-overrides-reset` 06:00 → jeśli crash w środku save → następny tick czeka. **MED.**

### 1.7 Queue corruption
- **`pending_proposals.json` plain write** (telegram_approver, podejrzewane wg agent — wymaga verify file:line) — multi-process race (czasowka + shadow + telegram_approver) = truncated JSON mid-shift. **HIGH.**
- **events.db (Opcja C split queue/audit 07.05)** WAL ✓ + `INSERT OR IGNORE` na deterministic event_id (event_bus.py:98) = idempotency ✓.
- **`pending_callbacks` (callback button state)** — **NIE PERSISTOWANE WCALE**. Restart = drop all. **CRITICAL.**

### 1.8 Temporary network failures
- **Single-shot urlopen** wszędzie (panel_client, telegram_approver, geocoding, osrm_client). Brak `urllib3.Retry` adapter. **HIGH.**
- **Nie sprawdzamy `errno.ECONNRESET` vs `errno.ETIMEDOUT`** dla różnicy "transient vs permanent" → jednolity exception handler → 1s blip = full retry zamiast 50ms backoff. **MED.**

### 1.9 Inconsistent external state (Panel zmienił, my nie wiemy)
- **`zmiana_czasu_odbioru` flag persistowany** w state machine (commit `73b3172` #19b 07.05) ✓ ALE **bez UPDATE_ORDER event** = mid-life flip detection wymaga osobnego ticketu.
- **`decision_deadline`** persistowany (#19a) ale **brak SLA alert consumer** = koordynator może przekroczyć window niewidocznie. **MED.**
- **State reconcile drift**: `dispatch-state-reconcile.timer` (15min tick) loguje do `reconciliation_log.jsonl` ale **brak alertu** na drift count > N. **MED.**
- **Panel diff vs reconcile race** (V3.27.5 Path B incident) — nadal latent jeśli oba ticki w tej samej sekundzie.

### 1.10 Telegram / manual override chaos
- **`/stop` `/wraca` `/dopisz` `/pin` `/koniec`** — wszystkie writes do `manual_overrides.json` przez **`manual_overrides.py.save()`** (atomic ✓).
- **Lifecycle `/stop = "do końca dnia"`** wymuszany przez `dispatch-overrides-reset.timer` 06:00 Warsaw — **CRITICAL ryzyko B**: timer może umrzeć cicho (3-7.05 4-dniowy outage). Zero watchdog.
- **Multi-operator race** (Adrian + Bartek równocześnie `/stop Bartek O`) — fcntl LOCK_EX serializuje ✓ ALE brak audit trail "kto kogo i kiedy" w samym `manual_overrides.json` (tylko learning_log).
- **Free-text NIE wykonuje akcji** (R-OPERATOR-COMMENT) ✓ tylko log.
- **Nadal bez `/status`** — operator nie ma snapshot "co aktualnie LIVE / co stale / co excluded". **HIGH (operational visibility gap).**

---

## 2. Degraded mode / Fallback mechanisms — co ISTNIEJE vs co BRAKUJE

| Komponent | Degraded mode? | Fallback? | Recovery autonomous? |
|-----------|----------------|-----------|----------------------|
| Panel API | ❌ brak | stale cache (silent) | ✓ przy następnym fetch |
| Telegram bot | ❌ brak | ❌ brak | ❌ chicken-egg |
| OSRM | ✓ Haversine | ✓ ale silent None=6285km | ✓ circuit breaker |
| Sheets schedule | ✓ fail-open cache | ✓ ale brak warn | ✓ przy fetch retry |
| Geocoding | ✓ Google→OSRM nearest | ✓ cascade | ✓ |
| Parser | ✓ V3.28 Layer 1-4 | ✓ motion-aware | ✓ |
| events.db | ❌ brak | brak | ✓ WAL recovery |
| State machine | ❌ brak readonly | ❌ | ❌ |
| Czasówka | ✓ KOORD fallback | ✓ defense gates L1/L2 | ✓ |
| Faza 7 auto-route | ✓ shadow only obecnie | ✓ ALERT escalation | ✓ |

**Brakuje:** **MASTER KILL SWITCH** — jeden flag w `flags.json` typu `OPERATIONAL_MODE` z trzema wartościami `NORMAL / DEGRADED / SAFE`:
- NORMAL → wszystko jak teraz
- DEGRADED → wszystkie propose → KOORD, telegram alerts only, brak auto-route, brak czasówki dispatch
- SAFE → emergency. Bot listens only `/wraca`. Zero proactive sends.

---

## 3. Health / observability / alerting — luki krytyczne

### 3.1 Health endpoints
- ✅ `:8888/health/parser` LIVE (motion-aware, hour-of-day suppression, downstream alert path mirror) — commit `50f05b2`
- ❌ **Brak `/health/shadow`** — shadow_dispatcher stuck = niewidzialny. ortools tsp_solver 200ms soft timeout może hang.
- ❌ **Brak `/health/telegram`** — bot down = chicken-egg
- ❌ **Brak `/health/state-machine`** — events.db consumer lag niewidzialny
- ❌ **Brak `/health/timers`** — zero watchdog dla 10 oneshot timers (overrides_reset incident dowód)

### 3.2 Alerting
- **send_admin_alert dedup per type:** parser_health 30min × 4 typy ✓; detector_419 5min ✓; health_endpoint 30min critical / 60min degraded ✓.
- **Single channel:** wszystkie alerty → Adrian DM. Bartek nie dostaje nawet shift notifications jako alert. **HIGH** (Adrian = single point of failure operator).
- **Brak severity routing:** `info/warn` mieszane z `critical` → desensytyzacja po incident 17/dzień (06.05 noc).
- **Brak sigtest** — żaden test "hipotezy że alert dotrze gdy Telegram ma 50% packet loss".

### 3.3 Logs / observability depth
- ✅ JSON structured: `learning_log`, `eval_log`, `czasowka_eval_log`, `auto_koord_log`, `reconciliation_log`, `plan_recheck_log`, `c5_shadow_log`, `tier_evolution`, `shadow_decisions` — bogato.
- ❌ **Brak log retention/rotation policy** dla `.jsonl` w `dispatch_state/`. `learning_log` 110KB OK, ale 100+ propozycji/d × 365d = 40MB+/rok per typ. **MED** (disk eventually).
- ❌ **Brak Prometheus / Grafana** — metryki tylko via custom Python `learning_analyzer.py`. Adrian nie ma realtime dashboardu poza Telegramem.
- ❌ **Brak time-series** — `:8888/health/parser` zwraca snapshot, brak history endpoint.

### 3.4 Operational visibility
- ❌ **Brak `/status` Telegram command** — Adrian musi wejść w panel + curl `:8888/health/parser` ręcznie żeby wiedzieć "co LIVE".
- ❌ **Brak fleet snapshot** — kto online, kto pauza, kto excluded — rozproszony po 4 plikach.
- ❌ **Brak last-action timeline** — "ostatnie 10 propozycji + decyzja + latency" jednym rzutem.

---

## 4. Pełna lista znalezionych ryzyk (12 pozycji)

| # | Ryzyko | Plik:Line (verify needed*) | Severity | Effort fix |
|---|--------|---------------------------|----------|------------|
| **R1** | Telegram callback state in-memory only — restart = 404 click | telegram_approver.py pending_proposals + callbacks | 🔴 CRITICAL | 2-3h |
| **R2** | Cron timer silent death (overrides_reset 4-day outage) | dispatch-overrides-reset.timer + 9 inne | 🔴 CRITICAL | 2h |
| **R3** | Telegram bot down chicken-egg — no parallel watchdog | dispatch-telegram + brak side-channel | 🔴 CRITICAL | 1h |
| **R4** | Haversine None/(0,0) silent 6285km | osrm_client.py ~haversine fallback | 🔴 CRITICAL | 30min |
| **R5** | pending_proposals.json plain write → queue corruption | telegram_approver | 🟠 HIGH | 30min |
| **R6** | Schedule fail-open silent stale (no STALE_SCHEDULE warn) | schedule_utils.py:95-135 | 🟠 HIGH | 30min |
| **R7** | No `/health/shadow` — stuck shadow invisible | dispatch_pipeline + parser_health_endpoint | 🟠 HIGH | 1h |
| **R8** | No retry/exp-backoff Panel/Telegram/OSRM | 5 callsite single urlopen | 🟠 HIGH | 2-3h |
| **R9** | No rate-limit awareness (Retry-After header) | telegram_approver, geocoding, schedule | 🟠 HIGH | 1h |
| **R10** | No `/status` operational fleet snapshot | new Telegram cmd | 🟠 HIGH | 1.5h |
| **R11** | Geocode cache forever-TTL (renovated buildings stale coords) | geocoding.py | 🟡 MED | 1h |
| **R12** | Log retention .jsonl unbounded | dispatch_state/*.jsonl | 🟡 MED | 1h |

\* część file:line z agent reports — przed implementacją **mandatory verify** (Lekcja #80 boundary changes audit).

---

## 5. Proponowane safety switches (flags.json)

```json
{
  "OPERATIONAL_MODE": "NORMAL",            // NORMAL | DEGRADED | SAFE
  "ENABLE_GRACEFUL_DEGRADE": true,
  "PANEL_API_CIRCUIT_BREAKER_ENABLED": true,
  "TELEGRAM_RETRY_EXPONENTIAL_ENABLED": true,
  "OSRM_HAVERSINE_FAIL_LOUD_ENABLED": true,    // crash zamiast silent 6285km
  "STALE_DATA_AGE_WARN_MIN": 15,                // emit STALE_* event po N min
  "MAX_RESTART_LOOP_PER_HOUR": 5,               // watchdog → SAFE mode
  "AUTO_ROUTE_KILL_SWITCH": false               // Faza 7 instant rollback
}
```

Każdy hot-reload (już LIVE w `load_flags()`). `OPERATIONAL_MODE=DEGRADED` → instant fallback bez restart.

---

## 6. Emergency fallback modes (state machine 3-mode)

```
NORMAL ──[panel_5xx ≥3 OR telegram_429 ≥3 OR osrm_circuit_open]──> DEGRADED
DEGRADED ──[manual /wraca OR all_systems_green ≥10min]──> NORMAL
DEGRADED ──[restart_loop ≥5/h OR critical_assert]──> SAFE
SAFE ──[manual Adrian only]──> NORMAL
```

| Mode | Dispatch propose | Czasówka emit | Faza 7 auto | Telegram alerts |
|------|------------------|---------------|-------------|------------------|
| NORMAL | ✓ | ✓ | ACK + auto | full |
| DEGRADED | ✓ ale always KOORD verdict + ALERT severity | ✓ ale always KOORD | OFF | only critical |
| SAFE | ❌ | ❌ | ❌ | only `/wraca` ack |

Implementation: 1 helper `is_operational_mode_at_least(NORMAL)` w `dispatch_pipeline.assess_order` na samym wejściu.

---

## 7. Autonomous recovery mechanisms (watchdogi)

### 7.1 Cron timer heartbeat watchdog (R2 fix)
NEW: `dispatch_state/job_heartbeats.json` (atomic write per timer fire):
```json
{"overrides_reset": {"last_run_utc": "2026-05-08T04:00:01Z", "duration_ms": 412},
 "czasowka_tick": {"last_run_utc": "2026-05-07T18:32:00Z", "duration_ms": 87},
 ...}
```
NEW timer: `dispatch-heartbeat-watchdog.timer` (5min) → script:
```python
for job, threshold_min in EXPECTED_INTERVALS.items():
    age_min = (now - heartbeats[job].last_run_utc).total_seconds() / 60
    if age_min > 2 * threshold_min:
        send_admin_alert(f"⚠️ {job} heartbeat stale {age_min:.0f} min", severity="critical")
```

### 7.2 Telegram bot side-channel (R3 fix)
2nd watchdog **poza** dispatch-telegram (dispatch-shadow lub osobny tiny service):
- `curl https://api.telegram.org/bot$TOKEN/getMe` co 60s
- Jeśli getMe fail >3× → alert via SMS gateway / email (out-of-band)
- Alternatywa: 2-bot setup — bot A (operacyjny) + bot B (heartbeat-only) wzajemnie sprawdzają

### 7.3 Restart loop detector
```python
# w start każdego long-running service
restart_count = read_restart_log_last_hour()
if restart_count > MAX_RESTART_LOOP_PER_HOUR:
    write_flag("OPERATIONAL_MODE", "SAFE")
    send_admin_alert("🚨 SAFE MODE: restart loop detected")
    sys.exit(0)
```

### 7.4 Stale data auto-warn
W każdym `load_*()` callsite:
```python
data, fetched_at = load_with_meta(path)
age_min = (now - fetched_at).total_seconds() / 60
if age_min > STALE_DATA_AGE_WARN_MIN:
    emit_event("STALE_DATA_WARN", {"path": path, "age_min": age_min})
```

### 7.5 Pending callback persistence (R1 fix)
`dispatch_state/pending_callbacks.jsonl` (append-only, atomic line-write):
```json
{"callback_id": "abc123", "msg_id": 6416, "oid": 471186, "decision_pending": true, "ts": "..."}
```
- Hydrate na startup `dispatch-telegram` → re-register handlers
- Cleanup po expiry (5min) lub ack
- Watchdog: pending >10min → auto-KOORD escalation

---

## 8. Operational dashboard — minimum viable

### 8.1 Telegram `/status` command (R10)
```
🤖 Ziomek Status @ 18:42:13 UTC

PARSER: ✓ healthy (last_fetch 4s, 12 active orders)
SHADOW: ✓ running (last_proposal 8s, p95=312ms)
TELEGRAM: ✓ connected (queue=0 pending)
RECONCILE: ✓ 14min ago (drift=0)
TIMERS: ✓ all fresh (overrides_reset 14h ago)

FLEET: 12 online / 3 paused / 47 roster
OPERATIONAL_MODE: NORMAL
LAST 3 PROPOSALS:
  • #471309 → cid=179 (47min good) ✓ accepted
  • #471289 → cid=179 (force_assign 39min) ✓
  • #471252 → KOORD early_bird ⚠️
```

### 8.2 Endpoint consolidation
Rozszerzyć `:8888/health/parser` → `:8888/health` (master) zwraca:
```json
{
  "parser": {...},
  "shadow": {"last_proposal_age_sec": 8, "latency_p95_ms": 312, "alive": true},
  "telegram": {"connected": true, "queue_size": 0, "last_msg_age_sec": 3},
  "reconcile": {"last_tick_age_min": 14, "drift_count": 0},
  "timers": {"overrides_reset": {"age_h": 14, "fresh": true}, ...},
  "operational_mode": "NORMAL",
  "stale_data": []
}
```

### 8.3 Alert dashboard (cheap HTML)
Static `monitoring/dashboard.html` z 5s SSE refresh — Last 24h alert timeline (type, severity, dedup count). Zero deps.

---

## 9. Alerting strategy — 3-channel routing

| Severity | Channel | Recipients | Dedup |
|----------|---------|------------|-------|
| **CRITICAL** | Adrian DM + Bartek DM + grupa ziomka | Adrian, Bartek, Adrian-2 (SMS gateway out-of-band) | 5min |
| **HIGH** | Adrian DM | Adrian | 30min |
| **MED** | Grupa ziomka | Adrian + Bartek (passive) | 60min |
| **LOW** | learning_log only | none | none |

Reklasyfikacja istniejących alertów:
- `PARSER_STUCK` → CRITICAL
- `PARSER_DELTA_SPIKE` → MED (już często false-pos)
- `TIMER_HEARTBEAT_STALE` → CRITICAL (R2)
- `TELEGRAM_BOT_DOWN` → CRITICAL (out-of-band)
- `OSRM_DOWN_5MIN+` → HIGH
- `R6_BAG_TIME_OVER` → HIGH (obecnie suppressed flag-OFF)
- `STALE_PANEL_AGE >5min` → MED
- `OVERRIDE_LIFECYCLE_DRIFT` → MED (overrides_reset stale)

---

## 10. Action plan (priorytetyzowany)

### P0 — zrób przed Faza 7 Etap 2 ramp 30% (~15.05)
1. **R3** Telegram bot side-channel watchdog (1h) — chicken-egg kill
2. **R2** Cron timer heartbeat watchdog (2h) — overrides_reset class blunder
3. **R1** Pending callback persistence (2-3h) — restart loss
4. **R4** Haversine fail-loud sentinel (30min) — silent corruption
5. **R7** `/health/shadow` endpoint + master `/health` consolidation (1h)

**Total P0:** ~7-8h (1 dzień focused)

### P1 — zrób przed master merge gate (10.05) lub T2 (11-17.05)
6. **R5** pending_proposals.json atomic write (30min)
7. **R10** `/status` Telegram command (1.5h) — zero-cost ops visibility
8. **R6** Schedule staleness warn + STALE_SCHEDULE_AGE event (30min)
9. **R8** Retry/exponential backoff helper `core/http_retry.py` + 5 callsite refactor (2-3h) — mirror Mailek `core/atomic_io.py` pattern
10. **R9** Rate-limit awareness `Retry-After` header parsing (1h)

**Total P1:** ~5-6h

### P2 — Q3 cleanup
11. **R11** Geocode cache age field + 30-day rolling invalidation (1h)
12. **R12** Log rotation policy (logrotate config + 90-day archive) (1h)
13. Operational mode 3-state state machine (`NORMAL/DEGRADED/SAFE`) (3-4h) — duża integracja

**Total P2:** ~5-6h

### Cumulative: ~17-20h pracy, 5-7 sesji ~3h każda

---

## 11. Ślepe plamy audytu (czego NIE potwierdziłem)

1. **systemd Restart= policy** per service (zakładam `Restart=always` ale nie weryfikowałem każdego unit file)
2. **MemoryMax / CPUQuota** — czy są limity systemd → silent OOM kill
3. **Hetzner backup retention** — czy `dispatch_state/` jest w backup, jak długo
4. **Telegram callback race** — `callback_data` z 3h-old pending button + simultaneous nowa propozycja na same oid
5. **events.db Opcja C consumer ack** — czy `mark_processed` faktycznie atomic z `get_pending` (event_bus.py:165-184 deklaruje, nie verify)
6. **flags.json hot-reload timing** — które procesy re-load per tick vs co N sekund (race window dla flag flip)
7. **panel_client CSRF token rotation** — V3.27.7 background refresh thread, nie weryfikowałem czy survive tygodniowy session
8. **OSRM Docker container restart sensitivity** — co dzieje się gdy `osrm-server` Docker restartuje w środku peak (5-10s unreachable)
9. **GPS server :8766** — legacy czy active, czy ma health check
10. **Cross-cascade test** — np. Google Geocoding down → OSRM nearest fallback → OSRM circuit open → Haversine None — czy testowane jako sekwencja

Każdy z tych punktów to realistycznie ~30min weryfikacji + ewentualny ticket.

---

## 12. Rekomendacja overall

**System jest "operacyjnie wytrzymały" na pojedyncze awarie** dzięki:
- Atomic writes wzorcowi (Lekcja #14 w 5+ miejsc)
- WAL+busy_timeout SQLite
- Parser resilience V3.28 4 layers
- Circuit breaker OSRM
- fcntl locks na shared state

**Ale NIE jest "niezniszczalny" z powodu:**
- 3 chicken-egg / silent-fail / restart-loss patterns (R1/R2/R3) bez detekcji
- Brak master kill switch + degraded mode state machine
- Single-channel alerting (Adrian SPOF)
- Brak side-channel watchdog Telegrama

**Najtańsza droga do "operacyjnie niezniszczalny":**
- P0 (7-8h) + P1 (5-6h) = **~13-14h pracy** = 3-4 sesje
- Zwraca ~95% gain. P2 to nice-to-have Q3.

**Sugerowana sekwencja:** R3 → R2 → R1 → R4 → R7 → master `/health` → `/status` cmd → reszta P1.

**Gate:** ACK Adrian na rosnący backlog (~14h dodaję do 5 P2/P3 już pending z `tech_debt_backlog.md`). Czekam na decyzję czy dodać do backlogu jako #22-#33 czy część bundle'ować z istniejącymi sprintami.
