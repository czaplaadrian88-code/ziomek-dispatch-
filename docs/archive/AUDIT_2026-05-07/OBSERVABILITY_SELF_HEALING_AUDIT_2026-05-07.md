# 🛰️ Observability & Self-Healing Audit — Ziomek dispatch_v2

**Data:** 2026-05-07 wieczór
**Owner:** Adrian Czapla <ac@nadajesz.pl>
**Cel:** likwidacja klasy "Adrian zauważył przez przypadek po 4 dniach" (incident overrides-reset 03-07.05). Sprawić, by system **sam zgłaszał, że umiera**, zanim koordynator zauważy.

**Kontekst:** Tydzień 4 (cel ~30.05) Ziomek autonomous 90%+; Bartek 1-2h dispatch/dzień (z 8h). Bez observability framework — niemożliwe.

**Cross-ref:**
- `META_AUDIT_ROOT_CAUSES_ROADMAP_2026-05-07.md` — RC3 (observability covers anticipated failures only)
- `OPERATIONAL_RESILIENCE_AUDIT_2026-05-07.md` — Hot Spot #4 (silent crons), #10 (OSRM cache 60min), #17 (Haversine None silent)
- `STRATEGIC_RISK_SYNTHESIS_2026-05-07.md` — TOP-4/TOP-5 ranking
- Lekcje z `MEMORY/lessons.md`: #32 (silent except = invisible bug), #75 (Telegram leak 3-warstwowa obrona), #80 (boundary changes audit consumers), #81 (fail-loud sentinel cross-codebase)
- Tech debt backlog: dodaje 6 zadań (#22-#27)

---

## TL;DR

Cztery deliverables, jedno spójne podejście "jeden hook → cały system":

1. **A. `OnFailure=` framework** — generic instantiated unit `dispatch-onfailure-alert@.service` + drop-in dla 16 serwisów (1h pracy, zero risk). Alert ma 8 pól actionable + playbook map + dedup 30min.
2. **B. `cron_health.json` schema** — single-writer state z `expected_max_silence_s` / `actual_silence_s` / `stale: bool`; `parser_health_endpoint` rozszerzony o `/health/all` z agregacją worst-status-wins.
3. **C. OSRM/GPS Circuit Breakers** — 3-warstwowy degraded mode (per-entry age, alert na entry/exit circuit, caller propagation flag); Haversine fail-loud `ValueError` cross-codebase; GPS `STALE_THRESHOLD_MIN=5` / `REJECT_THRESHOLD_MIN=30`.
4. **D. Watchdog kod** — 4 pliki / ~260 LOC delegowane do AIDER (HARD RULE >30 LOC); spina A+B+C w jeden 5-min cron z `cron_health.json` writer + alert sender.

**Empirical baseline:** incident 03-07.05 (overrides-reset 4 dni martwy) byłby wykryty po **6h** (3× expected interval z `expected_max_silence_s=24h × 1.5`). To eliminuje cały klas "Adrian zauważył ręcznie".

---

## Stan obecny — recon

### Systemd inventory (16 services + 12 timers)

**Services (12 dispatch-*.service):**

| # | Unit | Type | Restart | OnFailure= | Komentarz |
|---|---|---|---|---|---|
| 1 | dispatch-shadow.service | simple | on-failure | ❌ brak | core proposal pipeline |
| 2 | dispatch-panel-watcher.service | simple | on-failure | ❌ brak | parser_health już istnieje per-stuck |
| 3 | dispatch-telegram.service | simple | on-failure | ❌ brak | NIE restart bez ACK Adrian |
| 4 | dispatch-sla-tracker.service | simple/oneshot | on-failure | ❌ brak | R6 BAG_TIME alerts |
| 5 | dispatch-gps.service | simple | on-failure | ❌ brak | gps_server :8766 |
| 6 | dispatch-r04-evaluator.service | oneshot | — | ❌ brak | daily 03:00 Warsaw |
| 7 | dispatch-event-bus-cleanup.service | oneshot | — | ❌ brak | events.db retention |
| 8 | dispatch-plan-recheck.service | oneshot | — | ❌ brak | V3.19c 5-min recheck |
| 9 | dispatch-shift-notify.service | oneshot | — | ❌ brak | TASK B Phase 1 LIVE od 04.05 |
| 10 | dispatch-cod-weekly.service | oneshot | — | ❌ brak | disabled, re-enable 11.05 |
| 11 | dispatch-daily-accounting.service | oneshot | — | ❌ brak | Tue-Fri+Mon 06:00 |
| 12 | dispatch-overrides-reset.service | oneshot | — | ❌ brak | **INCIDENT 03-07.05** |

**Timers (12):** `czasowka.timer` (1min), `shadow.timer` (jeśli istnieje), `plan-recheck.timer` (5min), `event-bus-cleanup.timer`, `r04-evaluator.timer` (03:00), `daily-accounting.timer` (06:00 Tue-Fri+Mon), `cod-weekly.timer` (Mon 08:00, disabled), `overrides-reset.timer` (06:00), `shift-notify.timer` (1min), `state-reconcile.timer` (15min), `sla-tracker.timer` (jeśli istnieje).

**LUKA KRYTYCZNA:** **0 / 12** serwisów ma `OnFailure=`. Brak `dispatch-cron-alert@.service` template. Incident overrides-reset 4 dni martwy (03-07.05) **niewidzialny** bez alertu — odkryty ręcznie przez analizę tech debt.

### Parser health endpoint (single component, gold standard)

**Plik:** `parser_health.py` + `parser_health_endpoint.py` (port 8888)

**Schema state (`parser_health.json`):**
```json
{
  "version": "1",
  "saved_at": "2026-05-07T22:14:18.167286+00:00",
  "init_count": 23,
  "cycles": [
    {"ts": "...", "cycle": 2331, "orders_in_panel": 223, "active_orders": 0,
     "n_assigned": 0, "n_new": 0, "n_delivered": 0, "had_error": false,
     "order_ids": [...], "active_ids": []}
  ],
  "last_alert_at": {
    "PARSER_STUCK": 1778182581.4,
    "PARSER_ZERO_OUTPUT": 1778136136.8,
    "PARSER_DELTA_SPIKE": 1778137899.4,
    "PARSER_ASYMMETRY": 1777721202.0
  }
}
```

**4-warstwowa anomaly detection:**
- L1 ZERO_OUTPUT (orders_in_panel=0 ≥3 cycles)
- L2 DELTA_SPIKE (delta poza [-30%, +50%])
- L3 STUCK (variance=0 ≥5 cycles, motion-aware suppress gdy motion_sum < 4)
- L4 HTTP `:8888/health/parser`

**Alert dedup:** DEBOUNCE_SECONDS=1800 (30min) per typ.

**To jest SINGLE COMPONENT.** Reszta systemu (15 unitów + 12 timerów) nie ma równoważnej observability — to klasyczne RC3.

### OSRM client (Hot Spot #10)

**Plik:** `osrm_client.py:1-36`
```python
OSRM_BASE = "http://localhost:5001"
CACHE_TTL_SECONDS = 60 * 60  # 60 min — V3.26 R-07
CACHE_MAX_SIZE = 5000
_route_cache = {}  # {(from_key, to_key): (timestamp, result)}
_module_lock = threading.RLock()  # V3.27 thread-safety

CIRCUIT_BREAKER_THRESHOLD = 3  # failures
CIRCUIT_BREAKER_COOLDOWN_S = 60
```

**Issues:**
- `_route_cache` ma timestamp **per cache** ale **NIE zwraca go w returnie** — caller nie wie czy 1s czy 58min stare
- Circuit breaker istnieje ale **silent fallback** — nikt się nie dowiaduje że ETA leci na Haversine
- Brak `degraded_since` watermark
- Brak alert na entry/exit circuit

### Haversine fallback (Hot Spot #17)

**Stan po sprincie 07.05 morning #4:** częściowo fix'd (`osrm_client.haversine` `ValueError` na None / (0,0) per Lekcja #81). ALE: cross-codebase audit **nie był pełny** — call-sites poza `dispatch_pipeline` mogą nadal wołać haversine z None bez guard'a.

**GPS staleness:** brak formalnego threshold. `gps_age >60min` używany jako "real position" → ETA fałszywe → ranking compromised. Empirical: V3.16 demote działa tylko dla `pos_source in {no_gps, pre_shift, none}`, ale stale GPS klasyfikowany jako `gps_active` mimo 60min wieku.

### Telegram alert pipeline

**`telegram_utils.send_admin_alert(text)`** (lines 23-55):
- L1 prod guard: `PYTEST_CURRENT_TEST` env block (Lekcja #75)
- single-shot `urlopen` przez `tg_request`
- **Brak `send_group_alert`** do grupy ziomka (-5149910559)
- **Brak rate-limit awareness** (Retry-After header ignored)
- **Brak severity formatting** (każdy alert plain text)

---

## A. SYSTEMD `OnFailure=` Framework

### A.1 Architektura — generic instantiated unit

**Wzorzec:** `dispatch-onfailure-alert@.service` jako template (`@`-suffix). `%i` = nazwa zawieszonego unit'a. Każdy z 16 unitów dostaje **jednolity** `OnFailure=dispatch-onfailure-alert@%n.service`. Zero duplikacji, zero drift.

**Co MUSI zawierać alert (Z2 actionable):**

| Pole | Wartość | Po co |
|---|---|---|
| **Service name** | `%i` (np. `dispatch-overrides-reset.service`) | Jednoznacznie identyfikuje co padło |
| **Result** | `systemctl show ... -p Result` (`signal`/`exit-code`/`timeout`/`oom-kill`) | Klasa awarii — różny playbook |
| **ExitCode + Signal** | `ExecMainStatus`, `ExecMainCode` | OOM (137) vs PermissionDenied vs ImportError (1) |
| **Last 10 logów** | `journalctl -u %i -n 10 --no-pager` | Stack trace bez logowania na serwer |
| **Last success** | `LastTriggerUSec` (timer) lub `ExecMainStartTimestamp` | "4 dni martwy" → "ALARM: cron nie odpalił od 03.05 06:00" |
| **Suggested action** | mapowanie unit→playbook (A.3) | Bartek wie "restart vs ignore vs eskaluj" |
| **Severity** | 🔴 CRITICAL / 🟡 WARN / 🔵 INFO | Adrian filtruje co budzi w nocy |
| **Dedup key** | `f"{unit}:{exit_code}"` | Zapobiega 30 alertom/min gdy serwis flapuje |

### A.2 Pliki — alert sender + drop-in dla wszystkich

**`/etc/systemd/system/dispatch-onfailure-alert@.service`** (nowy unit, ~22 LOC):

```ini
[Unit]
Description=Telegram alert dla zawieszonego %i
After=network.target

[Service]
Type=oneshot
EnvironmentFile=/root/.openclaw/workspace/scripts/.secrets/telegram.env
ExecStart=/root/.openclaw/venvs/dispatch/bin/python \
    -m dispatch_v2.observability.alert_onfailure %i
TimeoutStartSec=20
StandardOutput=journal
StandardError=journal
```

**Drop-in dla każdego z 16 serwisów** — `/etc/systemd/system/<unit>.d/onfailure.conf`:

```ini
[Unit]
OnFailure=dispatch-onfailure-alert@%n.service
OnFailureJobMode=replace-irreversibly
```

**Bulk deploy** (1 komenda):

```bash
for unit in $(systemctl list-units --type=service --no-legend 'dispatch-*.service' | awk '{print $1}'); do
  sudo mkdir -p "/etc/systemd/system/${unit}.d"
  sudo tee "/etc/systemd/system/${unit}.d/onfailure.conf" <<'EOF'
[Unit]
OnFailure=dispatch-onfailure-alert@%n.service
OnFailureJobMode=replace-irreversibly
EOF
done
sudo systemctl daemon-reload
```

### A.3 Playbook map (suggested action w alercie)

```python
PLAYBOOK = {
    "dispatch-shadow":              "🔴 RESTART: nie ma proposal pipeline. `systemctl restart`.",
    "dispatch-panel-watcher":       "🔴 RESTART: parser_health zaraz odpali ZERO_OUTPUT.",
    "dispatch-telegram":            "🟡 WAIT_ACK: NIE restart bez Adrian (hard rule).",
    "dispatch-overrides-reset":     "🔴 MANUAL_RUN: `systemctl start` + verify lifecycle.",
    "dispatch-czasowka":            "🟡 WAIT 1 tick: oneshot, fresh proces — flap OK do 3×.",
    "dispatch-shift-notify":        "🟡 WAIT 1 tick: jak czasowka.",
    "dispatch-r04-evaluator":       "🔵 IGNORE_TILL_03:05: daily, non-real-time.",
    "dispatch-daily-accounting":    "🔵 RETRY_06:05: idempotent, retry OK.",
    "dispatch-event-bus-cleanup":   "🔵 IGNORE: cleanup, low-impact.",
    "dispatch-sla-tracker":         "🟡 RESTART: alerts mute do czasu fix.",
    "dispatch-gps":                 "🔴 RESTART: bez GPS no_gps_fallback dominuje.",
    "dispatch-cod-weekly":          "🔵 NEXT_MONDAY: weekly, retry per-tydzień.",
    "dispatch-plan-recheck":        "🟡 IGNORE: 5min recheck, kolejny tick OK.",
    "dispatch-state-reconcile":     "🟡 RESTART: phantom backlog rośnie.",
}
```

### A.4 Format wiadomości Telegram (przykład)

```
🔴 [DISPATCH-OVERRIDES-RESET.SERVICE] FAILED

  Result:    timeout
  Exit:      143 (SIGTERM)
  Last run:  2026-05-03 06:00:01 UTC (4d 8h temu)
  Last ok:   2026-05-03 06:00:01 UTC

  Logs (last 5):
    May 07 06:00:00 systemd[1]: Starting...
    May 07 06:00:01 python[1234]: ImportError: ModuleNotFoundError
    May 07 06:00:01 python[1234]: Traceback...
    May 07 06:00:30 systemd[1]: Timeout, terminating

  Playbook: 🔴 MANUAL_RUN — `systemctl start` + verify lifecycle
  Hint:     `journalctl -u dispatch-overrides-reset -n 50`

  [dedup: dispatch-overrides-reset:143 cooldown 30min]
```

### A.5 Dlaczego ten wzorzec, nie alternatywy

- ❌ **Per-service unique `OnFailure=`** — duplikacja 16×, drift, każda zmiana w 16 miejscach
- ❌ **Hook w aplikacji** (try/except → send) — nie łapie OOM (signal=9), segfault, ImportError przy starcie, timeout systemd
- ✅ **Drop-in `OnFailure=` z generic template** — łapie **wszystko** (exit≠0, timeout, OOM, signal), zero zmian w kodzie biznesowym, jednolite we wszystkich 16, deploy bulk, rollback bulk

---

## B. `cron_health.json` Schema + Endpoint Aggregation

### B.1 Schema (`/root/.openclaw/workspace/dispatch_state/cron_health.json`)

```json
{
  "version": "1",
  "saved_at": "2026-05-07T22:14:18Z",
  "units": {
    "dispatch-overrides-reset.service": {
      "kind": "oneshot_timer",
      "schedule": "OnCalendar=*-*-* 06:00:00 Europe/Warsaw",
      "last_run_utc": "2026-05-07T04:00:01Z",
      "last_success_utc": "2026-05-07T04:00:01Z",
      "last_failure_utc": null,
      "last_duration_ms": 412,
      "consecutive_failures": 0,
      "expected_max_silence_s": 90000,
      "actual_silence_s": 64458,
      "stale": false,
      "metrics": {
        "names_cleared": 13,
        "rollback_triggered": false
      }
    },
    "dispatch-czasowka.timer": {
      "kind": "high_freq_timer",
      "schedule": "OnUnitActiveSec=60s",
      "last_run_utc": "2026-05-07T22:14:00Z",
      "last_success_utc": "2026-05-07T22:14:00Z",
      "consecutive_failures": 0,
      "expected_max_silence_s": 180,
      "actual_silence_s": 18,
      "stale": false,
      "metrics": {
        "evals_per_tick_avg_5min": 1.4,
        "koord_ratio_5min": 0.0,
        "emit_count_24h": 47
      }
    }
  }
}
```

### B.2 Dlaczego ta struktura

| Pole | Po co |
|---|---|
| **`expected_max_silence_s`** + **`actual_silence_s`** + **`stale: bool`** | **Trójka kluczowa.** Watchdog porównuje, alertuje gdy `actual > expected × 1.5`. To eliminuje incident 03-07.05 — overrides-reset z `expected=86400s` (24h) byłby `stale=true` po 32400s = 9h. |
| **`kind`** (`oneshot_timer` / `high_freq_timer` / `daemon`) | Różny próg alertu. Czasowka 1min flap nie alarm; overrides-reset 24h cisza = alarm. |
| **`consecutive_failures`** | Eskalacja: 1× WARN, 3× CRITICAL. |
| **`metrics`** per-unit | Productivity signal — czasowka 0 emit/24h to alarm choć timer "działa" (Lekcja #76: anomaly detection input semantics). |

### B.3 Single-writer dyscyplina (RC4)

**Reguła:** `cron_health.json` ma **jeden writer = watchdog**. Aplikacje **nie piszą bezpośrednio** — emitują `events.db` `CRON_HEARTBEAT`, watchdog konsumuje.

**Tabela writerów:**

| Source | Mechanizm | Owner |
|---|---|---|
| **systemd state** | `dbus`/`systemctl show` query co 60s przez watchdog | Watchdog cron (D) |
| **Aplikacja per-tick** | emit `events.db` `CRON_HEARTBEAT { unit, ok, duration_ms, metrics }` | Każdy oneshot timer w swoim `__main__` |
| **OnFailure hook** | `dispatch-onfailure-alert@.service` zapisuje `last_failure_utc` przed wysłaniem | Sekcja A |

To eliminuje JSONL corruption pattern (Lekcja Hot Spot #1, RC4).

### B.4 `parser_health_endpoint` aggregation — `/health/all`

Obecny endpoint zwraca tylko parser. Rozszerzenie:

```python
# parser_health_endpoint.py — nowy endpoint /health/all
def aggregate_health() -> dict:
    parser = get_parser_health_snapshot()
    cron = json.loads(Path(CRON_HEALTH_PATH).read_text())

    # Worst status wins
    units_status = []
    for unit, st in cron["units"].items():
        if st["consecutive_failures"] >= 3 or st["stale"]:
            units_status.append((unit, "critical"))
        elif st["consecutive_failures"] >= 1:
            units_status.append((unit, "degraded"))
        else:
            units_status.append((unit, "healthy"))

    overall = "healthy"
    if any(s == "critical" for _, s in units_status):
        overall = "critical"
    elif any(s == "degraded" for _, s in units_status) or parser["status"] != "healthy":
        overall = "degraded"

    return {
        "status": overall,
        "parser": parser,
        "cron": {
            "total_units": len(cron["units"]),
            "healthy": sum(1 for _, s in units_status if s == "healthy"),
            "degraded": sum(1 for _, s in units_status if s == "degraded"),
            "critical": sum(1 for _, s in units_status if s == "critical"),
            "stale_units": [u for u, s in cron["units"].items() if s["stale"]],
            "details": cron["units"],
        },
        "external": {
            "osrm": _osrm_circuit_status(),       # sekcja C
            "panel": _panel_login_status(),
            "sheets": _sheets_cache_age(),
        },
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }
```

**Zewnętrzny scrape:** UptimeRobot lub Healthchecks.io GET `/health/all` co 5 min. Status `degraded`/`critical` → email + SMS Adriana **niezależnie od Telegrama**. Defense-in-depth: Telegram down ≠ ślepy.

---

## C. OSRM & GPS Circuit Breakers — Degraded Mode

### C.1 Hot Spot #10 — OSRM stale 60min cache

**Stan obecny** (`osrm_client.py:1-36`):
- `CACHE_TTL_SECONDS = 3600` — 60 min, bez `cached_at` per entry zwracanego do callera
- `CIRCUIT_BREAKER_THRESHOLD=3`, `cooldown=60s` — istnieje, działa **silently**
- Gdy circuit open → fallback Haversine **bez sygnału w decyzji**

**Fix 3-warstwowy:**

#### L1. Per-entry timestamp + age signal w returnie

```python
def get_route(from_coords, to_coords) -> dict:
    key = (from_coords, to_coords)
    cached = _route_cache.get(key)
    if cached:
        ts, result = cached
        age_s = time.time() - ts
        if age_s < CACHE_TTL_SECONDS:
            result["_cache_age_s"] = age_s    # NEW: caller widzi
            result["_source"] = "cache"
            return result
    # ... fresh fetch
    result["_cache_age_s"] = 0
    result["_source"] = "osrm_live"
    return result
```

#### L2. Circuit breaker → degraded mode flag (NIE silent fallback)

```python
class OSRMState:
    failures = 0
    circuit_open_until = 0.0
    degraded_since = None

def get_route(...):
    if time.time() < OSRMState.circuit_open_until:
        if OSRMState.degraded_since is None:
            OSRMState.degraded_since = time.time()
            send_admin_alert(f"🔴 OSRM CIRCUIT OPEN — fallback Haversine LIVE od {datetime.now()}")
        return _haversine_fallback(from_coords, to_coords, degraded=True)

    try:
        result = _osrm_request(...)
        if OSRMState.degraded_since:
            duration = time.time() - OSRMState.degraded_since
            send_admin_alert(f"✅ OSRM RECOVERED po {duration:.0f}s")
            OSRMState.degraded_since = None
        OSRMState.failures = 0
        return result
    except Exception as e:
        OSRMState.failures += 1
        if OSRMState.failures >= CIRCUIT_BREAKER_THRESHOLD:
            OSRMState.circuit_open_until = time.time() + CIRCUIT_BREAKER_COOLDOWN_S
        log.error(f"OSRM_FAIL #{OSRMState.failures}: {e}")
        return _haversine_fallback(from_coords, to_coords, degraded=True)
```

#### L3. Caller propagation — flag w decision audit

```python
# dispatch_pipeline.py _v327_eval_courier
route = osrm_client.get_route(...)
if route.get("_source") == "haversine_degraded":
    decision_meta["degraded_osrm"] = True   # zapisz do shadow log
    flags["degraded_mode"] = True            # widoczne w Telegram alert
```

### C.2 Hot Spot #17 — Haversine None silent (Lekcja #81 reinforced)

**Stan po sprincie 07.05 morning #4:** częściowo fix'd. ALE cross-codebase audit nie był pełny.

**Fail-loud cross-codebase:**

```python
def haversine(coords1, coords2) -> float:
    if coords1 is None or coords2 is None:
        raise ValueError(f"haversine: None coords ({coords1}, {coords2}) — fix call-site")
    if coords1 == (0.0, 0.0) or coords2 == (0.0, 0.0):
        raise ValueError(f"haversine: (0,0) sentinel — fix call-site")
    # ... real math
```

**Audit komenda** (Lekcja #80 boundary changes universal):

```bash
grep -rn "haversine\|_haversine" /root/.openclaw/workspace/scripts/dispatch_v2/ | grep -v test
```

Każdy call-site dostaje guard PRZED haversine call:

```python
if pickup_coords is None:
    log.warning(f"V328_NONE_COORDS oid={oid} pickup=None — KOORD/no_pickup_geocode")
    return DECISION_KOORD(reason="no_pickup_geocode")
```

### C.3 GPS staleness — formal threshold

**Obecnie:** brak formalnego `GPS_MAX_AGE_MIN`. Stale GPS >60min używany jako "real" pozycja → ETA fałszywe.

**Fix:**

```python
# common.py
GPS_STALE_THRESHOLD_MIN = 5    # >5min = stale (flag w decision)
GPS_REJECT_THRESHOLD_MIN = 30  # >30min = treated as no_gps

# courier_resolver.py build_fleet_snapshot
gps_age_min = (now - gps_ts).total_seconds() / 60
if gps_age_min > GPS_REJECT_THRESHOLD_MIN:
    pos_source = "no_gps"           # synthetic BIALYSTOK_CENTER
elif gps_age_min > GPS_STALE_THRESHOLD_MIN:
    pos_source = "gps_stale"        # use coords ale flag w decision
    decision_meta["gps_stale_min"] = gps_age_min
```

**Flag-gate:** `ENABLE_V328_GPS_STALE_AGE` default False (shadow log) → flip True po 24h obs.

### C.4 Tabela degraded modes — co pozostaje akcjonowalne

| Komponent | Down | Degraded mode | Co działa |
|---|---|---|---|
| **OSRM** | Circuit open | Haversine + flag `degraded_osrm` | scoring kontynuuje, kandydaci niżej rankowani; Telegram pokazuje "⚠️ ETA approx" |
| **GPS** | gps_age >30min | `no_gps` synthetic position | bag>=1 ostro karany (V3.16 demote), no_gps empty bag → KOORD review |
| **Panel API** | Login fail | Stale `last_panel_state` (max 5 min) | Telegram alert "🔴 PANEL DOWN", dispatch zamrożony |
| **Sheets schedule** | API fail | Cached `schedule_today.json` z `_age` flag | feasibility kontynuuje, alert po 30 min |
| **events.db** | SQLite locked | Buffer w-memory (max 100 events) | persist na recovery |

**Reguła:** każdy degraded mode = **alert na entry** + **alert na exit**. Zero "silent corruption".

---

## D. Watchdog Implementation — AIDER Delegation

### D.1 Routing decision

Watchdog ~150-200 LOC realnie (oneshot cron, 5-min interval, czyta systemctl, pisze cron_health.json, alertuje stale, agreguje metrics). Plus tests ~50 LOC. Łącznie ~260 LOC. **HARD RULE: >30 LOC → AIDER.**

### D.2 Komenda dla Adriana (copy-paste ready)

```bash
cd /root/.openclaw/workspace/scripts/dispatch_v2

aider --model deepseek/deepseek-coder \
  --no-auto-commits \
  --file observability/__init__.py \
  --file observability/cron_health.py \
  --file observability/alert_onfailure.py \
  --file observability/watchdog.py \
  --file tests/test_cron_health_watchdog.py \
  --read parser_health.py \
  --read parser_health_endpoint.py \
  --read telegram_utils.py \
  --read common.py \
  --message "Stwórz moduł obserwowalności w dispatch_v2/observability/. Cztery pliki:

1. cron_health.py (~80 LOC):
   - CRON_HEALTH_PATH = '/root/.openclaw/workspace/dispatch_state/cron_health.json'
   - atomic_write_json() z fcntl + tempfile + os.replace (wzorzec dispatch_v2.manual_overrides)
   - load_state() / save_state(state) + ttl-aware merge
   - record_run(unit, success: bool, duration_ms: int, metrics: dict | None) — single-writer API
   - is_stale(unit_state) → bool (compare actual_silence_s > expected_max_silence_s * 1.5)
   - schema per sekcja B.1 audytu (kind, last_run_utc, last_success_utc, consecutive_failures, expected_max_silence_s, actual_silence_s, stale, metrics)

2. alert_onfailure.py (~50 LOC):
   - main(unit_name) wywoływane z dispatch-onfailure-alert@.service ExecStart
   - subprocess.run(['systemctl', 'show', unit_name, '-p', 'Result,ExecMainStatus,ExecMainCode,ActiveEnterTimestamp,InactiveEnterTimestamp'])
   - subprocess.run(['journalctl', '-u', unit_name, '-n', '10', '--no-pager'])
   - PLAYBOOK dict dla 14 dispatch-* serwisów z mapą action (RESTART/WAIT_ACK/MANUAL_RUN/IGNORE_TILL/RETRY)
   - format Telegram message per sekcja A.4 audytu (severity emoji per playbook prefix)
   - dedup via cron_health.last_alert_at[f'{unit}:{exit_code}'], 30-min cooldown
   - send_admin_alert(text) + cron_health.record_run(unit, success=False, ...)

3. watchdog.py (~80 LOC):
   - run_once() invocable via systemd oneshot timer (every 5 min)
   - subprocess.run(['systemctl', 'list-timers', '--all', '--no-pager'])
   - parse last/next per timer + active/failed status
   - dla każdego dispatch-*.service|timer: cross-check vs cron_health.units[name]
   - jeśli stale → cron_health.record_stale(unit) + send_admin_alert(f'🟡 STALE: {unit} silence {actual_silence_s}s')
   - per-unit metrics fetcher (np. dla overrides-reset: read manual_overrides.json mtime; dla czasowka: count entries w eval_log w ostatnie 5min)
   - emit tick metric do parser_health style (last_tick_utc, units_checked, alerts_emitted)

4. tests/test_cron_health_watchdog.py (~50 LOC):
   - test_record_run_success
   - test_record_run_failure_increments_consecutive
   - test_is_stale_threshold
   - test_atomic_write_resists_partial_fail (mock OSError)
   - test_alert_onfailure_dedup_30min
   - test_watchdog_run_once_idempotent (run twice, no double-alert)

Wymagania krzyżowe:
- import telegram_utils.send_admin_alert (już istnieje w dispatch_v2/)
- python 3.11, type hints, docstring per public function
- atomic writes wzorzec z dispatch_v2/manual_overrides.py (lockfile + fcntl.LOCK_EX + tempfile + os.replace)
- zero zewnętrznych deps poza stdlib + co już jest w dispatch_v2/
- testy custom-runner styl (jak dispatch_v2/daily_accounting/tests/run_all.py) bo pytest częściowo flaky
- fail-loud (Lekcja #81): brak silent except, każdy except loguje exception type + context
- ZERO mock telegram_utils — testy ustawiają PYTEST_CURRENT_TEST env (auto block per Lekcja #75)
- pre-flight: sprawdź czy CRON_HEALTH_PATH parent dir istnieje, atomic mkdir jeśli brak"
```

### D.3 Po wygenerowaniu — Adrian workflow

```bash
# 1. Backup
cd /root/.openclaw/workspace/scripts/dispatch_v2
mkdir -p observability
cp -r observability observability.bak-pre-watchdog-2026-05-07 2>/dev/null || true

# 2. py_compile + import check (per CLAUDE.md workflow rule)
/root/.openclaw/venvs/dispatch/bin/python -m py_compile \
  observability/cron_health.py observability/alert_onfailure.py observability/watchdog.py
/root/.openclaw/venvs/dispatch/bin/python -c \
  "from dispatch_v2.observability import cron_health, alert_onfailure, watchdog"

# 3. Testy
/root/.openclaw/venvs/dispatch/bin/python -m pytest tests/test_cron_health_watchdog.py -v

# 4. Smoke alert_onfailure standalone (NIE deploy systemd)
/root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.observability.alert_onfailure dispatch-shadow.service
# (powinno wysłać ✅ "service active" gdy shadow live)

# 5. Smoke watchdog
/root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.observability.watchdog
cat /root/.openclaw/workspace/dispatch_state/cron_health.json | head -50

# 6. Commit + tag (po ACK)
git add observability/ tests/test_cron_health_watchdog.py
git commit -m "observability: cron_health framework + watchdog + onfailure alert (sekcja A-D audit)"
git tag observability-watchdog-2026-05-XX
```

### D.4 Deploy systemd (po smoke)

```bash
# 1. Alert sender unit (raz)
sudo tee /etc/systemd/system/dispatch-onfailure-alert@.service <<'EOF'
[Unit]
Description=Telegram alert for failed %i

[Service]
Type=oneshot
EnvironmentFile=/root/.openclaw/workspace/scripts/.secrets/telegram.env
ExecStart=/root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.observability.alert_onfailure %i
TimeoutStartSec=20
EOF

# 2. Drop-iny dla każdego (bulk z A.2)
# 3. Watchdog timer
sudo tee /etc/systemd/system/dispatch-watchdog.service <<'EOF'
[Unit]
Description=Cron health watchdog (5-min sweep)

[Service]
Type=oneshot
ExecStart=/root/.openclaw/venvs/dispatch/bin/python -m dispatch_v2.observability.watchdog
EOF

sudo tee /etc/systemd/system/dispatch-watchdog.timer <<'EOF'
[Unit]
Description=Watchdog every 5 min

[Timer]
OnBootSec=2min
OnUnitActiveSec=5min
Persistent=true

[Install]
WantedBy=timers.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now dispatch-watchdog.timer
```

---

## E. Roadmap deploy (2 dni, peak-aware)

| Etap | Czas | Działania | Risk |
|---|---|---|---|
| **E0 pre-flight** | 30min | grep audit `haversine\|None coords` call-sites; lista 16 unitów; backup `cron_health.json` (jeśli istnieje) | 🟢 zero |
| **E1 OnFailure framework** | 1h | A.2 deploy `dispatch-onfailure-alert@.service` + drop-iny dla 14 serwisów; smoke test (`systemctl start nonexistent.service`) | 🟢 zero (drop-in tylko reaguje na fail) |
| **E2 cron_health + watchdog** | AIDER 1h + audit 1h | sekcja D — generuj, test, smoke, commit | 🟢 nowy moduł, off-path |
| **E3 OSRM degraded mode** | 2h | C.1 L1+L2+L3, test 1× synthetic OSRM down (docker stop osrm-server na 30s w off-peak) | 🟡 wpływa scoring, test off-peak |
| **E4 Haversine fail-loud** | 1h | C.2 audit 4-5 call-sites + guards | 🟡 jeśli jakikolwiek call-site lata na None coords ukryty bug → odsłonię alert (Z2 win) |
| **E5 GPS staleness threshold** | 1h | C.3 stałe + flag-gate `ENABLE_V328_GPS_STALE_AGE` default False (shadow log) → flip True po 24h obs | 🟡 może odrzucić więcej kandydatów; flag-gated |
| **E6 `/health/all` endpoint** | 30min | B.4 — agregacja, deploy, scrape UptimeRobot | 🟢 read-only |

**Total:** ~7h pracy, 1 sesja 9-16 (off-peak Pn-Pt) lub 2 sesje sob 9-16 + niedz 9-12.

**Deploy peak-aware:**
- E1 (drop-iny) — kiedykolwiek, daemon-reload bez restart
- E2 (watchdog) — kiedykolwiek
- E3 (OSRM degraded) — **OFF-PEAK ONLY** (sob rano lub niedz)
- E4 (haversine fail-loud) — OFF-PEAK
- E5 (GPS) — flag default False, deploy kiedykolwiek, flip OFF-PEAK
- E6 (endpoint) — kiedykolwiek

---

## F. Rollback procedury

### F.1 OnFailure framework rollback (5 min, surgical)

```bash
# Bulk remove drop-ins
for unit in $(systemctl list-units --type=service --no-legend 'dispatch-*.service' | awk '{print $1}'); do
  sudo rm -f "/etc/systemd/system/${unit}.d/onfailure.conf"
  sudo rmdir "/etc/systemd/system/${unit}.d" 2>/dev/null
done
sudo rm -f /etc/systemd/system/dispatch-onfailure-alert@.service
sudo systemctl daemon-reload
```

### F.2 Watchdog rollback (1 min)

```bash
sudo systemctl disable --now dispatch-watchdog.timer
sudo rm /etc/systemd/system/dispatch-watchdog.{timer,service}
sudo systemctl daemon-reload
# Code: git revert observability-watchdog-2026-05-XX
```

### F.3 OSRM degraded mode rollback (per-flag)

```bash
# Hot-reload flags.json
python3 -c "import json,os,tempfile; p='/root/.openclaw/workspace/scripts/flags.json'; d=json.load(open(p)); d['ENABLE_OSRM_DEGRADED_MODE']=False; fd,t=tempfile.mkstemp(dir=os.path.dirname(p)); open(fd,'w').write(json.dumps(d,indent=2,ensure_ascii=False)); os.replace(t,p)"
# Restart shadow + panel-watcher (off-peak only)
```

### F.4 GPS staleness rollback

```bash
# Flag-gate, hot-reload
python3 -c "...['ENABLE_V328_GPS_STALE_AGE']=False..."
# No restart needed (read-fresh per tick)
```

---

## G. Eskalacja → tech_debt_backlog.md

Po deployu dodać 6 zadań:

- **#22 P0** — OnFailure framework (E1) — jednorazowe, blokuje wszystkie inne fix'y observability
- **#23 P0** — cron_health + watchdog (E2) — single source of truth liveness
- **#24 P1** — OSRM circuit breaker degraded mode (E3) — kompletuje Hot Spot #10
- **#25 P1** — Haversine fail-loud cross-codebase (E4) — kompletuje Lekcja #81
- **#26 P2** — GPS staleness threshold flag (E5) — wymaga Adrian decyzję o thresholdach
- **#27 P2** — `/health/all` external scrape (E6) — wymaga decyzję UptimeRobot vs Healthchecks.io

---

## H. Memory writes (post-deploy)

Lekcje do `MEMORY/lessons.md`:

- **Lekcja #89 (NEW)** — OnFailure jako jednolity hook = jedyna obrona przed klasą silent cron failures; alert MUSI mieć playbook + last_success + stale_age, nie tylko "service failed".
  - **Why:** incident overrides-reset 03-07.05 (4 dni martwy bez sygnału) — odkryty ręcznie.
  - **How to apply:** każdy nowy systemd unit dispatch-* dostaje drop-in `OnFailure=dispatch-onfailure-alert@%n.service` w deploy procedurze.

- **Lekcja #90 (NEW)** — cron_health single-writer dyscyplina (RC4) — aplikacje emitują events.db `CRON_HEARTBEAT`, watchdog konsumuje. Eliminuje JSONL corruption pattern z 3-4 writerów bez locków.
  - **Why:** Hot Spot #1 learning_log triple-writer corruption; meta-audyt RC4 "Append-only logs without writer discipline".
  - **How to apply:** żadna aplikacja nie pisze bezpośrednio do `cron_health.json` — tylko watchdog. Aplikacje emitują przez `event_bus.emit()` z `AUDIT_TYPES`.

- **Lekcja #91 (NEW)** — Degraded mode = alert entry + alert exit (NIE silent fallback). OSRM circuit open bez alertu = "silent corruption" wzorzec; circuit open z alertem = system zachowuje observability mimo degradation.
  - **Why:** OSRM cache 60min stale + circuit silent fallback = ETA Haversine bez świadomości operatora; Hot Spot #10.
  - **How to apply:** każdy degraded mode (OSRM, GPS, Panel, Sheets, events.db) ma `degraded_since` watermark + alert na entry + alert na exit.

Memory files do update (per `MEMORY.md` index):
- `lessons.md` — add #89-#91
- `feedback_rules.md` — add "OnFailure mandatory dla nowych systemd unit dispatch-*" (universal po #22 deploy)
- `tech_debt_backlog.md` — add #22-#27 (P0/P1/P2 priorities)
- `sprint_timeline.md` — add CURRENT HANDOFF entry post-deploy

---

## I. Cross-ref artefakty

| Artefact | Path |
|---|---|
| **Ten audyt** | `dispatch_v2/AUDIT_2026-05-07/OBSERVABILITY_SELF_HEALING_AUDIT_2026-05-07.md` |
| Architecture audit | `dispatch_v2/AUDIT_2026-05-07/ARCHITECTURE_AUDIT_2026-05-07.md` |
| Concurrency audit | `dispatch_v2/AUDIT_2026-05-07/CONCURRENCY_DATA_INTEGRITY_AUDIT_2026-05-07.md` |
| Operational resilience | `dispatch_v2/AUDIT_2026-05-07/OPERATIONAL_RESILIENCE_AUDIT_2026-05-07.md` |
| Strategic risk | `dispatch_v2/AUDIT_2026-05-07/STRATEGIC_RISK_SYNTHESIS_2026-05-07.md` |
| Meta audit roadmap | `dispatch_v2/AUDIT_2026-05-07/META_AUDIT_ROOT_CAUSES_ROADMAP_2026-05-07.md` |
| Tech debt backlog | `MEMORY/tech_debt_backlog.md` |
| Sprint timeline | `MEMORY/sprint_timeline.md` |
| Lessons learned | `MEMORY/lessons.md` |

---

## J. Ostateczny sanity check

**Empirical baseline weryfikacja:** incident overrides-reset 03-07.05.
- Pre-fix: detection latency = **~96h** (4 dni, ręcznie)
- Post-fix (E1+E2): detection latency = **~9h** (`expected_max_silence_s=86400` × 1.5 → stale po 32400s = 9h, watchdog tick co 5min, alert via OnFailure jeśli systemd zauważy fail wcześniej)
- **Improvement: 10× szybsza detekcja**, plus wszystkie 16 unitów objętych jednolicie.

**Z1 vs Z2 tension:** ten sprint = Z2 win. ~7h pracy "infra plumbing" zamiast Faza 7 calibration. Ale **bez tego cel Tydzień 4 (90% autonomy) niewykonalny** — Adrian musi być active observer, czego eliminujemy. Per Z1: ASAP = velocity tygodniowa, NIE per-decision pressure. Tydzień traci 7h, zyskuje miesiące autonomicznych operacji.

**Z3 multi-tenant:** schema `cron_health.json` z `units` map + per-unit `kind` + `metrics` jest tenant-agnostic. Restimo / Wolt Drive / Warsaw expansion = N tenantów × M unitów = liniowo, nie kwadratowo. Watchdog pure read systemd state (zero tenant assumptions).

---

**Status audytu:** Design phase complete. Pending: ACK Adrian na sekwencję E0→E6, potem AIDER (sekcja D) jako pierwszy krok implementation.

**Owner sprintu:** TBD (Adrian decyzja).
**Estimated total effort:** ~7h pracy (1 sesja off-peak Pn-Pt 9-16, lub 2 sesje sob 9-16 + niedz 9-12).
**Critical path:** E1 (OnFailure) → E2 (watchdog) → E3-E5 (OSRM/GPS) → E6 (endpoint scrape).

---

**Last update:** 2026-05-07 wieczór (sprint observability audit complete)
