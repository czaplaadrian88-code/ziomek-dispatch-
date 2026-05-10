# 🎯 MASTER DEPLOY PLAN — Konsolidacja 9 audytów z 07.05.2026

**Data:** 2026-05-07 wieczór (deszyfracja po wszystkich 9 audytach z dnia)
**Branch:** `sprint-07-05-event-bus-opcja-c` +32 commits ahead `master@10c754d`
**Tylko-CC plik (zero kodu, sam plan).** Master merge gate: 10.05. Faza 7 100% target: ~31.05.
**Cel:** zamiast 40 sygnałów × 9 dokumentów → jeden plan z datami / właścicielami / ACK gate'ami.

**Źródła (9):** META, ARCHITECTURE, STRATEGIC_RISK_SYNTHESIS, TELEGRAM_APPROVER_GOD_OBJECT, CONCURRENCY_DATA_INTEGRITY, OBSERVABILITY_SELF_HEALING, OPERATIONAL_RESILIENCE, STATE_OWNERSHIP_EVENT_FLOW, MULTI_TENANT_SCALABILITY.

---

## ZADANIE 4: MASTER EXECUTIVE SUMMARY (1 strona, ~290 słów)

Dziewięć audytów mówi jedno: **dispatch_v2 jest inżyniersko świetny per-fix, ale architektonicznie ad-hoc per-system**. 40 sygnałów (R1-R20 z ARCHITECTURE × F1-F20 ze STATE_OWNERSHIP) to **objawy 6-7 strukturalnych klas** (RC1 filesystem-as-IPC, RC2 cache invalidation as afterthought, RC3 observability tylko anticipated, RC4 append-only logs no discipline, RC5 ownership emergent, RC6 replay re-runs current code, RC7 god objects). Punktowy fix każdego = klasa wraca przy następnym serwisie/tenancie/refactor. **Nie potrzebujemy 40 sprintów — potrzebujemy 6 architectural migrations** (Postgres, Redis, state_io.py, event sourcing, liveness contract, multi-tenant config).

**Co MUSI być w tym tygodniu (08-14.05, ~6h pracy):** (1) **disk cleanup** — server jest 95% full, 100% swap, pęknie w 2-3 dni; (2) **OnFailure systemd framework + watchdog** — overrides-reset martwy 4 dni cicho 03-07.05, ten incident jest blueprintem klasy; (3) **systemd MemoryMax/CPUQuota** dla 16 unitów — pierwszy OOM zabije losowy proces, kaskadowy fail w 2 min; (4) **event_bus retry decorator + cleanup peak-aware** — 45min total, eliminuje permanent event loss z BUSY 5s w callbackach; (5) **Hetzner upgrade decyzja** — CPX22→CPX32 (już was) niewystarczy dla Restimo, trzeba CPX42.

**Co GROZI jeśli nic:** kolejny silent cron timer umrze (10 timerów × niezerowe prawdopodobieństwo / kwartał) i nikt nie zauważy 4-7 dni; pierwszy peak Restimo (Q3) zabije box jednoczesnym memory pressure + SQLite 100 writes/s + multi-tenant cache drift; **najbardziej realne — disk full w okresie 09-12.05** prowadzi do incident w peak.

**Co JUŻ jest dobrze:** event_bus deterministic + INSERT OR IGNORE + WAL ✓; state_machine V3.27.5 Path B ✓; plan_manager fcntl + CAS ✓; courier_admin 4-file atomic z rollback ✓; parser_health 4-layer V3.28 ✓; firmowe konto 6-warstwowa defense 07.05 ✓. Tych nie ruszać.

**Pierwsze 30 minut:** F2 cron-watchdog (OnFailure template + drop-in dla 14 unitów). Najlepsze value/effort w całym 6-mies programie.

---

## ZADANIE 1: TOP-15 MASTER DEPLOY PLAN (08.05 → 04.06)

**Reguła konsolidacji:** zadanie pojawiające się w ≥2 audytach z różnymi effortami → bierzemy **najszerszy scope** (większy effort), ale wskazujemy że minimum (mniejszy effort) daje 80% gain.

---

### 🚦 PRE-DEPLOY GATE (universal, NON-NEGOTIABLE per każde zadanie TOP-15)

**Reguła Z2 jakość (Adrian explicit hard rule):** żaden deploy z TOP-15 NIE rusza bez następującej sekwencji. Kolejność identyczna z `dispatch_v2/CLAUDE.md` workflow:

1. **Draft + ACK** — propose change w czacie, wait Adrian ACK pre-implementation (dla zadań z `ACK?=TAK`).
2. **Backup** — `cp <file> <file>.bak-pre-<task>-2026-05-XX` per każdy edytowany plik (24h retention rule).
3. **Implementation** — edit (CC sam) lub AIDER `deepseek/deepseek-coder` (per CLAUDE.md HARD RULE >30 LOC → AIDER).
4. **`py_compile` clean** — `/root/.openclaw/venvs/dispatch/bin/python -m py_compile <touched_files>` zero errors.
5. **Import check** — `python -c "from dispatch_v2 import <touched_modules>"` no `ImportError`/`ModuleNotFoundError`.
6. **Sprint-specific tests** — nowe testy zadania (per `Acceptance Criteria` `[TEST]` markers) ALL PASS.
7. **🔴 BASELINE REGRESSION SUITE PASS — MANDATORY** — `pytest tests/ --ignore=test_cod_weekly.py --ignore=test_feasibility_integration.py --ignore=test_reconcile_dry_run.py --ignore=test_scoring_scenarios.py --ignore=test_feasibility_c3.py --ignore=test_decision_engine_f21.py -v --timeout=60 --tb=short` → expected **~934 PASS / ~20 pre-existing FAIL** (lista pre-existing fails w `dispatch_v2/CLAUDE.md` "Known issues / pre-existing failures"). **Każde NEW failure = STOP, root-cause, NIE deploy.**
8. **Commit + tag** — `git commit -m "<task-name>: <summary>"` + `git tag <task-name>-2026-05-XX` jako rollback waypoint.
9. **Pre-deploy ACK** — Adrian explicit ACK w czacie (dla zadań z `ACK?=TAK`); peak window = MANDATORY override ACK (per Lekcja #34).
10. **Restart** (jeśli `Restart?=TAK`) — off-peak only chyba że Adrian explicit override (vide 07.05 firmowe konto sprint).
11. **Verify** — Acceptance Criteria `[CMD]`/`[GREP]` checks zaraz po restart.
12. **Stop for ACK** — wait Adrian potwierdzenie smoke 5-10 min postdeploy.
13. **`[SMOKE 24h]`** observation — po 24h zweryfikuj `[SMOKE 24h]` items z Acceptance Criteria + journalctl grep ERROR/WARNING delta vs baseline.

**Pomijanie tej kolejności = Adrian ACK MANDATORY w czacie z explicit override + reason** (NIE silent skip, vide Lekcja #75 3-warstwowa obrona).

**Cumulative tests w TOP-15:** ~25-40 nowych `tests/test_*.py` plików (per Acceptance Criteria). Baseline rośnie 934 → ~970 do końca W4. Każdy sprint commit zachowuje ~20 pre-existing FAIL invariant (lista w CLAUDE.md), zero NEW regressions.

---

**Notacja:**
- **Restart?** = czy wymaga restart długotrwałego serwisu (off-peak only).
- **ACK?** = wymaga explicit ACK Adrian w czacie zanim deploy.
- **W** = numer tygodnia (W1=08-14.05, W2=15-21.05, W3=22-28.05, W4=29.05-04.06 pre-Faza 7 100% flip).
- **Effort** to chunk pracy (impl + test + smoke), bez czekania ACK.
- **Pełne `Acceptance Criteria` (4-7 sprawdzalnych checklist items per zadanie) w sekcji "ACCEPTANCE CRITERIA per zadanie TOP-15" pod tabelą.** Konwencja: `[CMD]`/`[GREP]`/`[TEST]`/`[SMOKE 24h]`.

| # | Zadanie | P | Effort | Blokowane przez | Blokuje | Restart? | ACK? | Owner | W | Źródło audyt |
|---|---|---|---|---|---|---|---|---|---|---|
| **1** | **Disk cleanup** (.bak >24h, accidental dirs `Let me produce the blocks*` + nested `dispatch_v2/dispatch_v2/`, orphan `.tmp_cr2kure6.json` 5.5MB, `learning_log.jsonl` truncate >30d, audit `events.db` reduces) | P0 | 1h | — | #2 (decyzja Hetzner po reduce) | NIE | NIE | **CC + Adrian** (CC drafts script, Adrian ACK pre-delete) | W1 | MULTI_TENANT P0-INFRA, ARCH P0#5, STRATEGIC top-5 |
| **2** | **Hetzner upgrade decision + execute** (CPX32→CPX42; current CPX32 87% RAM/100% swap pre-Restimo niewystarczy) | P0 | manual 30min | #1 (po cleanup wiemy realne RAM/disk) | #3 (limity zależą od headroom) | TAK (re-IP, off-peak weekend) | **TAK** | **Adrian** (Hetzner Cloud Console manual) | W1 | MULTI_TENANT D, ARCH (production 6/10) |
| **3** | **systemd MemoryMax/CPUQuota + WatchdogSec + StartLimitBurst** dla 16 dispatch-* unitów (drop-in `resource_limits.conf`) | P0 | 1.5h impl + bulk deploy | #2 (po Hetzner upgrade) | — | TAK kaskadowo (per-service off-peak) | **TAK** | **CC + Adrian** (CC drop-iny, Adrian deploy + per-service restart ACK) | W1 | MULTI_TENANT D, ARCH P0#3, STRATEGIC top-5 |
| **4** | **OnFailure systemd framework + cron_health watchdog + onfailure_alert** (4 pliki: `observability/cron_health.py`, `alert_onfailure.py`, `watchdog.py`, tests; ~260 LOC AIDER deepseek-coder + drop-iny dla 14 unitów) | P0 | 30min minimum (sam template) → 3-4h pełen scope | — | #14 (cron_health.json potrzebny dla `/health/all`) | NIE (drop-iny via `daemon-reload`) | **TAK** | **AIDER+CC** (AIDER ~260 LOC, CC orchestrate + smoke; >30 LOC HARD RULE) | W1 | META top-1, OBSERVABILITY A+B+D, OPS R2, STATE_OWNERSHIP F2, STRATEGIC top-1 |
| **5** | **`event_bus.emit()` retry decorator + `cleanup()` peak-aware guard** (`@with_retry(3, exp_backoff=[100,500,2000])` na 5 callsite Telegram callback handlers + `if _is_peak_window(): return 0` w cleanup) | P0 | 45min total (30+15) | — | — | TAK (shadow + telegram off-peak) | **TAK** | **CC** (~25 LOC retry + ~5 LOC cleanup; ≤30 LOC SELF) | W1 | CONCURRENCY P0×2, MULTI_TENANT P0-SAFETY |
| **6** | **`flags.json` atomic write helper + ad-hoc `json.dump(open(p,'w'))` replace** (`flags_admin.py` central tool, eliminuje "Adrian + parallel CC = corrupt flagi") | P0 | 1h | — | — | NIE | NIE | **AIDER+CC** (~50 LOC `flags_io.py` + ~30 LOC CLI tool + tests; >30 LOC) | W1 | ARCH P0#4 (R-7), STRATEGIC top-4 |
| **7** | **Haversine fail-loud cross-codebase audit + guards** (`grep -rn "haversine\|_haversine"` w dispatch_v2; każdy call-site dostaje guard PRZED call: `if coords is None or coords==(0,0): raise ValueError`) | P0 | 1h (audit ~30min + 4-5 guard fixes ~30min) | — | #13 (OSRM degraded mode integruje fail-loud) | TAK (shadow + panel-watcher off-peak) | **TAK** | **CC + Adrian** (CC audit grep + 4-5 mini guards ≤30 LOC, Adrian ACK na callsite list) | W2 | OPS R4, OBSERVABILITY C.2, STATE_OWNERSHIP partial firmowe konto, Lekcja #81 cross-codebase |
| **8** | **Logrotate config dla 25+ logów** (`learning_log.jsonl` 110MB, `shadow_decisions.jsonl` 66MB, `dispatch.log` 25MB, `czasowka.log` 12.7MB; `/etc/logrotate.d/dispatch-v2` z `daily / rotate 14 / size 50M / compress`) | P0 | 1h | #1 (po cleanup wiemy które z .jsonl są live vs orphan) | — | NIE (logrotate USR1 signal) | NIE | **CC** (config file, no Python code) | W2 | ARCH P0#2, STRATEGIC top-2, OPS R12 |
| **9** | **Telegram side-channel watchdog** (out-of-band: 2nd tiny serwis `dispatch-tg-heartbeat.timer` co 60s `getMe`; ≥3× fail → SMS Adrian; **wymaga decyzji #6 SMS gateway**) | P0 | 1h impl + provider config | Decyzja #6 SMS gateway | — | NIE (nowy unit) | **TAK** | **AIDER+CC** (~60 LOC heartbeat loop + SMS provider client + tests; >30 LOC) | W2 | OPS R3 (chicken-egg kill) |
| **10** | **`_shutdown_drain()` w `main_async` try/finally + 10 silent killer except handlers** w `telegram_approver.py` (Kategoria A z TELEGRAM_APPROVER §4: lines 79, 260, 384, 400, 1017, 1161, 1290, 1715, 1729, 2686) | P0 | 1h (drain) + 3-4h (10 except) = ~5h | — | — | TAK (telegram, peak override per Adrian ACK jak 07.05) | **TAK MANDATORY** | **CC + Adrian** (CC ~30 LOC drain + 10 mini except fixes ~3 LOC each = ~60 LOC łącznie ale rozproszone ≤30 LOC per fix; Adrian MANDATORY ACK telegram restart) | W2 | TELEGRAM_APPROVER §2+§4 |
| **11** | **`core/jsonl_appender.py` (cross-process atomic JSONL append) + 3-callsite migration** (`panel_watcher.py:139`, `telegram_approver.py:1257`, `shadow_dispatcher._append_decision`; AIDER ~100 LOC: `os.O_APPEND` single-write + `fcntl.flock LOCK_EX`) | P1 | 3-4h | Decyzja #2 strategy ACK (replace lub migrate-to-events.db) | — | TAK (3 serwisy off-peak + telegram peak override ACK) | **TAK** | **AIDER+CC** (~100 LOC core + ~80 LOC tests + 3-callsite migration; AIDER explicit per CONCURRENCY §4) | W3 | CONCURRENCY §4, META RC4, STATE_OWNERSHIP F1, ARCH R-4, MULTI_TENANT P1-DB |
| **12** | **`_COORDS` mtime reload w panel_watcher cycle + V3.27.5 race test** (`panel_watcher.py:67-78` mtime check co 15s; `tests/test_state_machine_race_v3275.py` 4 scenariusze) | P1 | 1h total (30+30) | — | — | TAK (panel-watcher off-peak) | NIE | **AIDER+CC** (~10 LOC mtime + ~50 LOC race test = ~60 LOC; >30 LOC trigger AIDER) | W3 | META top-5 quick win, STATE_OWNERSHIP F3 + F8 |
| **13** | **OSRM circuit breaker degraded mode (3-warstwowo)** (L1 `_cache_age_s` w returnie, L2 alert na entry/exit `degraded_since`, L3 caller propagation `decision_meta["degraded_osrm"]=True`) | P1 | 2h | #7 (Haversine guards są pre-rec) | — | TAK (shadow off-peak) | **TAK** | **AIDER+CC** (~80 LOC circuit + ~50 LOC tests; AIDER) | W3 | OBSERVABILITY C.1, STATE_OWNERSHIP F7, OPS implicit R8 |
| **14** | **`/health/all` + `/health/shadow` endpoint consolidation** (rozszerzyć `:8888/health/parser` o aggregator `worst-status-wins` z `cron_health.json` + shadow heartbeat + telegram queue + reconcile drift) | P1 | 1.5h | #4 (cron_health.json gotowy) | — | TAK (panel-watcher endpoint, off-peak) | NIE | **AIDER+CC** (~60 LOC aggregator + ~40 LOC tests; AIDER) | W4 | OBSERVABILITY B.4, OPS R7+R10 (master /health) |
| **15** | **Schedule staleness warn + `STALE_SCHEDULE_AGE` event + `/status` Telegram cmd** (alert gdy `schedule_age > 30 min`; daily snapshot `schedule_today_backup.json`; Telegram cmd format z OPS §8.1) | P1 | 30min + 1.5h = 2h | #14 (master `/health` daje data dla `/status`) | — | TAK (telegram peak override ACK) | **TAK** | **AIDER+CC** (~30 LOC staleness + ~80 LOC `/status` cmd + tests; AIDER) | W4 | OPS R6 + R10, STATE_OWNERSHIP F12 |

**Sumaryczny effort:**
- W1 (08-14.05): ~7-8h (1 + 0.5 + 1.5 + 3-4 + 0.75 + 1)
- W2 (15-21.05): ~7-8h (1 + 1 + 1 + 5)
- W3 (22-28.05): ~6-7h (3-4 + 1 + 2)
- W4 (29.05-04.06): ~3.5h (1.5 + 2)

**Total ~24-27h impl** (4 sprinty po 6-7h, każdy 1-2 dni). Plus AIDER ~5-6h spread. Plus ACK windows ~12h spread. **Realny calendarian: 4 tygodnie**, ale można skompresować do 2 tygodni przy daily focus.

---

## ACCEPTANCE CRITERIA per zadanie TOP-15

Każde zadanie DONE gdy **wszystkie** items zaznaczone. Brak "częściowo" — zadanie jest *not started / in progress / completed*. Smoke verification ZAWSZE 24h post-deploy (Lekcja #34 peak observation), ACK po smoke.

**Konwencja:** `[ ]` = not done, `[x]` = done. `[CMD]` = komenda do uruchomienia żeby zweryfikować. `[GREP]` = expected pattern w pliku/logu. `[TEST]` = pytest target. `[SMOKE 24h]` = obserwacja w produkcji 24h.

---

### #1 — Disk cleanup pre-Restimo (P0)

- [ ] `[CMD] df -h /root` → disk usage < 80% (pre-fix: 95%)
- [ ] `[CMD] find /root -name "*.bak-*" -mtime +1 | wc -l` → 0 (lub explicit whitelist Adrian z komentarzem)
- [ ] `[CMD] ls /root/.openclaw/workspace/scripts/dispatch_v2/ | grep -E "(Let me produce|dispatch_v2/dispatch_v2)"` → 0 results
- [ ] `[CMD] find /root/.openclaw/workspace/dispatch_state -name ".tmp_*"` → 0 results (orphan tempfile gone)
- [ ] `[CMD] wc -l /root/.openclaw/workspace/dispatch_state/learning_log.jsonl` → linie tylko z ostatnich 30 dni (oldest line ts > now-30d)
- [ ] `[CMD] du -sh /root/.openclaw/workspace/scripts/dispatch_v2/` < pre-cleanup size (oczekiwane -10MB+ z .bak)
- [ ] `[CMD] sqlite3 events.db 'SELECT count(*) FROM audit_log'` < pre-cleanup (po reduce starszych >90d)

---

### #2 — Hetzner upgrade decision + execute (P0)

**Rollback:** Hetzner Cloud Console → Server `dispatch-prod` → Snapshots → "Restore from `pre-cpx42-upgrade-2026-05-XX`" (snapshot taken pre-upgrade w step 1). Re-IP nowy round, ~10min downtime. Per-service `systemctl is-active` post-restore + `journalctl --since "5min"` → 0 errors.

- [ ] `[CMD] nproc` → expected vCPU count (4 dla CPX32 / 8 dla CPX42)
- [ ] `[CMD] free -h` → RAM total expected (8Gi CPX32 / 16Gi CPX42)
- [ ] `[CMD] systemctl is-active dispatch-shadow dispatch-panel-watcher dispatch-telegram dispatch-sla-tracker dispatch-gps dispatch-monitor-419` → 6× active
- [ ] `[CMD] journalctl -u dispatch-shadow --since "10 minutes ago" | grep -ci ERROR` → 0
- [ ] `[CMD] journalctl -u dispatch-shadow --since "10 minutes ago" | grep latency_ms | tail -10` → p95 < pre-upgrade baseline (CPX32: ~624ms; CPX42 expected ~300-400ms)
- [ ] `[SMOKE 24h]` Adrian Telegram: 1 propozycja end-to-end ASSIGN → success log w `learning_log.jsonl`
- [ ] `[SMOKE 24h]` Brak OOM kill: `journalctl --since "24h" | grep -i "killed\|oom"` → 0 hit per dispatch-* service

---

### #3 — systemd MemoryMax/CPUQuota (P0)

**Rollback:** `for svc in $(systemctl list-units --type=service --no-legend 'dispatch-*.service' | awk '{print $1}'); do sudo rm -f "/etc/systemd/system/${svc}.d/resource_limits.conf"; done; sudo systemctl daemon-reload` (no restart wymagany — limity wracają do `=infinity` na hot-reload). Telegram peak override jeśli OOM kill już się dzieje.

- [ ] `[CMD] for svc in $(systemctl list-units --type=service --no-legend 'dispatch-*.service' | awk '{print $1}'); do echo $svc:; systemctl show $svc -p MemoryMax,CPUQuota,WatchdogSec,StartLimitBurst | grep -v "=infinity\|=0" || echo MISSING; done` → wszystkie 16 mają wszystkie 4 properties set (zero MISSING)
- [ ] `[CMD] systemctl show dispatch-shadow -p MemoryMax,CPUQuota` → MemoryMax=1500M, CPUQuota=150% (lub configured per profile §D.2)
- [ ] `[CMD] systemctl show dispatch-telegram -p MemoryMax,CPUQuota` → MemoryMax=600M, CPUQuota=50%
- [ ] `[SMOKE 24h] journalctl --since "24h" | grep -iE "memory.high|memory.max|cgroup.*kill|OOMKilled"` → 0 hard limits hit (MemoryHigh w warning OK pod peak)
- [ ] `[SMOKE 24h]` Po peak window 11-14 + 17-20: `systemctl status dispatch-* | grep "Active:" | grep -v "active (running)\|active (waiting)"` → 0 failed/inactive
- [ ] `[CMD] cat /etc/systemd/system/dispatch-shadow.service.d/resource_limits.conf` → contains MemoryHigh + WatchdogSec + StartLimitBurst + OOMScoreAdjust

---

### #4 — OnFailure framework + cron_health watchdog + alert (P0)

- [ ] `[CMD] systemctl cat dispatch-onfailure-alert@.service` → exists, ExecStart wskazuje na `python -m dispatch_v2.observability.alert_onfailure %i`
- [ ] `[CMD] for svc in $(systemctl list-units --type=service --no-legend 'dispatch-*.service' | awk '{print $1}' | grep -v onfailure); do test -f "/etc/systemd/system/${svc}.d/onfailure.conf" || echo MISSING $svc; done` → 0 MISSING (drop-in dla każdego z 14 dispatch-*)
- [ ] `[CMD] systemctl is-active dispatch-watchdog.timer` → active
- [ ] `[CMD] cat /root/.openclaw/workspace/dispatch_state/cron_health.json | python -c "import json,sys; d=json.load(sys.stdin); print(len(d['units']))"` → 14+
- [ ] `[CMD]` Smoke deliberate fail: `sudo systemctl start nonexistent-test.service` (pre-stage broken unit) → Telegram alert dociera w <30s z 8 polami (Service/Result/Exit/LastSuccess/Logs/Playbook/Severity/Hint)
- [ ] `[TEST] pytest tests/test_cron_health_watchdog.py -v` → 6/6 PASS (record_run_success, record_run_failure_increments, is_stale_threshold, atomic_write_resists_partial_fail, alert_dedup_30min, watchdog_run_once_idempotent)
- [ ] `[SMOKE 24h]` Backfill test: kill `overrides_reset.timer` → po 9h (1.5× expected_max_silence 24h × 1.5 / 4) `cron_health.json` ma `stale=true` + Telegram STALE alert

---

### #5 — event_bus.emit() retry decorator + cleanup() peak-aware (P0)

**Rollback:** `cd /root/.openclaw/workspace/scripts/dispatch_v2 && git revert event-bus-retry-cleanup-peak-aware-2026-05-XX --no-edit && sudo systemctl restart dispatch-shadow` (off-peak); `dispatch-telegram` restart **wymaga Adrian explicit ACK**. Cleanup timer auto-pickup w next 04:00 UTC tick (no immediate restart needed).

- [ ] `[GREP] grep -A5 "def cleanup" event_bus.py` → contains `if _is_peak_window(): return 0` jako first non-docstring statement
- [ ] `[GREP] grep -B1 "_handle_assign_callback\|_handle_inny_callback\|_handle_koord_callback\|_handle_koniec_callback\|_handle_poprawa_callback" telegram_approver.py | grep "@with_retry"` → 5 hits (każdy callback ma decorator)
- [ ] `[TEST]` Mock `_is_peak_window=True` + cleanup() call → returns 0, 0 rows deleted
- [ ] `[TEST]` Mock SQLite BUSY 2× → 3rd success → caller widzi success (zero permanent loss); Mock 5× BUSY → final raise OperationalError z context
- [ ] `[TEST] pytest tests/test_event_bus_retry.py tests/test_cleanup_peak_aware.py -v` → all PASS
- [ ] `[SMOKE 24h] journalctl -u dispatch-telegram --since "24h" | grep -iE "retry|BUSY"` → log entries z successful retry, 0 unrecoverable "permanent event loss" warnings
- [ ] `[SMOKE 24h]` cleanup execution log: `journalctl -u dispatch-event-bus-cleanup --since "7 days"` → 0 runs w peak windows (wszystkie skipped lub off-peak)

---

### #6 — flags.json atomic write helper (P0)

- [ ] `[CMD] test -f /root/.openclaw/workspace/scripts/dispatch_v2/core/flags_io.py && echo OK` → OK
- [ ] `[CMD] test -f /root/.openclaw/workspace/scripts/dispatch_v2/flags_admin.py && echo OK` → OK
- [ ] `[CMD] python -m dispatch_v2.flags_admin set TEST_FLAG_DUMMY true` → success; `cat flags.json | grep TEST_FLAG_DUMMY` → exists; `python -m dispatch_v2.flags_admin del TEST_FLAG_DUMMY` cleanup
- [ ] `[GREP] grep -rn "json.dump.*flags" /root/.openclaw/workspace/scripts/ | grep -v core/flags_io.py | grep -v test` → 0 results (wszystkie ad-hoc helpers replaced)
- [ ] `[TEST]` Concurrent write test: 4 procesy × 100 updates równocześnie → final flags.json valid JSON, all 400 updates present (no lost-update race)
- [ ] `[TEST] pytest tests/test_flags_io.py -v` → all PASS
- [ ] `[SMOKE 24h]` Brak corrupt flags.json incident (vide pre-fix risk: parallel CC + Adrian)

---

### #7 — Haversine fail-loud cross-codebase (P0)

**Rollback:** `cd /root/.openclaw/workspace/scripts/dispatch_v2 && git revert haversine-fail-loud-cross-codebase-2026-05-XX --no-edit && sudo systemctl restart dispatch-shadow dispatch-panel-watcher` (off-peak only). Pre-fix `osrm_client.haversine` zachowywała `(0,0)` → 6285km — to znów stanie się "feature" (Lekcja #81 regression). Soft fallback: `cp osrm_client.py.bak-pre-haversine-fail-loud-2026-05-XX osrm_client.py` jeśli git revert skomplikowany cross-callsite.

- [ ] `[CMD] grep -rn "haversine\|_haversine" /root/.openclaw/workspace/scripts/dispatch_v2/ | grep -v test | grep -v "\.bak"` → identified 4-5 callsites (oczekiwane: osrm_client.haversine + courier_resolver + route_simulator_v2 + scoring + dispatch_pipeline)
- [ ] `[GREP]` Każdy callsite ma guard PRZED haversine call: `grep -B5 "haversine(" <plik> | grep -E "if.*None|if.*\(0.*0\)|raise ValueError"` → guard present
- [ ] `[TEST] pytest tests/test_haversine_fail_loud.py -v` → all PASS (incl. test: `haversine(None, valid)` → `ValueError`; `haversine((0,0), valid)` → `ValueError`)
- [ ] `[CMD] python -c "from dispatch_v2.osrm_client import haversine; haversine(None, (53.13, 23.16))"` → ValueError raised (NIE 6285km)
- [ ] `[SMOKE 24h] journalctl -u dispatch-shadow --since "24h" | grep "6285"` → 0 hits (silent 6285km bug class eliminated)
- [ ] `[SMOKE 24h]` Pre-fix sanity: 0 nowych "BRAK KANDYDATÓW pickup_too_far" (Lekcja #81 cross-codebase complete)

---

### #8 — Logrotate config (P0)

- [ ] `[CMD] test -f /etc/logrotate.d/dispatch-v2 && echo OK` → OK
- [ ] `[CMD] grep -c "rotate\|compress\|size" /etc/logrotate.d/dispatch-v2` → ≥3 patterns (rotate 14, size 50M, compress)
- [ ] `[CMD] sudo logrotate -d /etc/logrotate.d/dispatch-v2` → debug shows 25+ paths z poprawnym plan (no errors)
- [ ] `[CMD] sudo logrotate -f /etc/logrotate.d/dispatch-v2 && ls -la /root/.openclaw/workspace/scripts/logs/*.gz | head -5` → at least 5 rotated archives
- [ ] `[CMD] du -sh /root/.openclaw/workspace/scripts/logs/` < pre-rotation baseline
- [ ] `[CMD] du -sh /root/.openclaw/workspace/dispatch_state/learning_log.jsonl` post-rotation reasonable (<50M lub configured cap)
- [ ] `[SMOKE 7d] cron daily logrotate run` → archives accumulate, oldest <14d retention

---

### #9 — Telegram side-channel watchdog out-of-band (P0)

- [ ] `[CMD] systemctl cat dispatch-tg-heartbeat.timer` → exists, OnUnitActiveSec=60s
- [ ] `[CMD] systemctl is-enabled dispatch-tg-heartbeat.timer` → enabled; `systemctl is-active` → active
- [ ] `[CMD] test -f /root/.openclaw/workspace/scripts/.secrets/sms.env && echo OK` → OK (provider creds)
- [ ] `[GREP] grep -A3 "def heartbeat" dispatch_v2/observability/tg_heartbeat.py` → contains `getMe` call + 3× consecutive fail threshold + SMS send
- [ ] `[CMD]` Smoke deliberate kill: `sudo systemctl stop dispatch-telegram; sleep 240` → ≥1 SMS na Adrian phone z "TG_DOWN" message; po `start` → SMS "TG_RECOVERED"
- [ ] `[CMD]` Restore dispatch-telegram (Adrian ACK); `journalctl -u dispatch-tg-heartbeat --since "1h"` shows 4 events: down_1, down_2, down_3, recovery
- [ ] `[SMOKE 24h]` 0 false positives (każdy SMS = real outage)

---

### #10 — _shutdown_drain + 10 silent killer except (P0)

**Rollback:** `cd /root/.openclaw/workspace/scripts/dispatch_v2 && cp telegram_approver.py.bak-pre-shutdown-drain-silent-killers-2026-05-XX telegram_approver.py && /root/.openclaw/venvs/dispatch/bin/python -m py_compile telegram_approver.py`. **Restart `dispatch-telegram` MANDATORY ACK Adrian** w czacie (peak override jeśli incident). Alternative: `git revert telegram-shutdown-drain-silent-killers-2026-05-XX --no-edit`. **HARD RULE:** zero restart bez Adrian ACK, NIE silent.

- [ ] `[GREP] grep -A10 "async def main_async" telegram_approver.py | grep -E "try:|finally:|_shutdown_drain"` → wszystkie 3 patterns present
- [ ] `[GREP] grep -n "async def _shutdown_drain" telegram_approver.py` → 1 hit; function logs success + flush count
- [ ] `[CMD]` SIGTERM mid-mutation test: spawn telegram_approver, mock state mutation; SIGTERM 50ms post-mutation; restart; verify `pending_proposals.json` reflects post-mutation state
- [ ] `[GREP]` Linie 79, 260, 384, 400, 1017, 1161, 1290, 1715, 1729, 2686 — każda dostała `_log.warning(f"...{var1}={...} ...{var2}={...} {type(e).__name__}: {e}")` (10 hits przez `grep -A2 "except Exception" telegram_approver.py | grep -c "_log\."` ≥10 silent killers)
- [ ] `[TEST] pytest tests/test_telegram_shutdown_drain.py -v` → PASS
- [ ] `[TEST] pytest tests/test_telegram_silent_except_*.py -v` → 10 PASS (każdy except handler ma test)
- [ ] `[SMOKE 24h] journalctl -u dispatch-telegram --since "24h" | grep -E "WARNING.*(oid|cb_id|action)" | wc -l` → ≥0 entries (Z2 win — invisible bugs become visible bo wcześniej silent; oczekiwane 5-15 entries/d ujawniających real edge cases)

---

### #11 — core/jsonl_appender.py + 3-callsite migration (P1)

**Rollback:** Per-callsite (ostatni-deployed-first): `git revert jsonl-appender-callsite3-shadow-2026-05-XX jsonl-appender-callsite2-telegram-2026-05-XX jsonl-appender-callsite1-panel-watcher-2026-05-XX --no-edit && sudo systemctl restart dispatch-shadow dispatch-panel-watcher` (off-peak); **`dispatch-telegram` MANDATORY Adrian ACK**. `core/jsonl_appender.py` może zostać (no callers = no harm). Pre-fix: silent JSONL corruption wraca, 3-callsite raw `f.write(...)` nadal zapisują.

- [ ] `[CMD] test -f /root/.openclaw/workspace/scripts/dispatch_v2/core/jsonl_appender.py && echo OK` → OK
- [ ] `[GREP] grep -n "from dispatch_v2.core.jsonl_appender import append_jsonl\|from .core.jsonl_appender import append_jsonl" panel_watcher.py telegram_approver.py shadow_dispatcher.py` → 3 hits
- [ ] `[GREP]` Linia panel_watcher.py:139 zastąpiona `append_jsonl(_LEARNING_LOG_PATH, override_rec)`; podobnie telegram_approver.py:1257 i shadow_dispatcher._append_decision
- [ ] `[GREP] grep -nE "with open.*\.jsonl.*\"a\"\)" panel_watcher.py telegram_approver.py shadow_dispatcher.py | grep -v test | grep -v "\.bak"` → 0 results (wszystkie raw `f.write` migrated)
- [ ] `[TEST] pytest tests/test_jsonl_appender.py -v` → 7/7 PASS (incl. concurrent_appenders_no_interleaving 4-process × 100 records × 50KB stress)
- [ ] `[SMOKE 24h] python -c "import json; bad=0; [bad := bad+1 for l in open('/root/.openclaw/workspace/dispatch_state/learning_log.jsonl') if l.strip() and not (lambda: json.loads(l))()]"` → 0 JSONDecodeError (pre-fix: random 1-3 corrupt lines/tydz)
- [ ] `[SMOKE 24h]` Burst test in peak: `wc -l learning_log.jsonl` count delta = expected emits count (no silent drops)

---

### #12 — _COORDS mtime reload + V3.27.5 race test (P1)

**Rollback:** `cd /root/.openclaw/workspace/scripts/dispatch_v2 && cp panel_watcher.py.bak-pre-coords-mtime-2026-05-XX panel_watcher.py && sudo systemctl restart dispatch-panel-watcher` (off-peak). Race test plik `tests/test_state_machine_race_v3275.py` może zostać (no execution at runtime, only `pytest`). Pre-fix: nowa restauracja silent BRAK KANDYDATÓW do manual restart.

- [ ] `[GREP] grep -A20 "_COORDS_LOADED_AT\|_load_coords" panel_watcher.py | grep -E "mtime|getmtime"` → mtime check present w cycle
- [ ] `[CMD]` Runtime test: `cp restaurant_coords.json restaurant_coords.json.bak && python -c "import json; d=json.load(open('restaurant_coords.json')); d['999'] = {'lat':53.0, 'lng':23.0}; json.dump(d, open('restaurant_coords.json','w'))"; sleep 30; journalctl -u dispatch-panel-watcher --since "1m" | grep "_load_coords"` → reload triggered; restore backup
- [ ] `[CMD] test -f /root/.openclaw/workspace/scripts/dispatch_v2/tests/test_state_machine_race_v3275.py && echo OK` → OK
- [ ] `[TEST] pytest tests/test_state_machine_race_v3275.py -v` → 4/4 PASS (PICKED_UP_preserve, DELIVERED_preserve, normal_assigned_update, multiple_ASSIGNED_5s_no_overwrite)
- [ ] `[CMD]` Causal verification: `git stash push state_machine.py:312-326`; `pytest tests/test_state_machine_race_v3275.py` → ≥1 FAIL; `git stash pop` → 4/4 PASS
- [ ] `[SMOKE 24h]` Brak rzadkiego "panel_watcher restart-needed" message po dodaniu nowej restauracji

---

### #13 — OSRM circuit breaker degraded mode 3-warstwowo (P1)

**Rollback (soft, hot-reload, no restart):** `python3 -c "import json,os,tempfile; p='/root/.openclaw/workspace/scripts/flags.json'; d=json.load(open(p)); d['ENABLE_OSRM_DEGRADED_MODE']=False; fd,t=tempfile.mkstemp(dir=os.path.dirname(p)); open(fd,'w').write(json.dumps(d,indent=2,ensure_ascii=False)); os.replace(t,p)"` — circuit breaker idzie back do "silent fallback" mode (bez alertów ale działający).
**Rollback (hard):** `git revert osrm-circuit-breaker-degraded-2026-05-XX --no-edit && sudo systemctl restart dispatch-shadow` (off-peak only).

- [ ] `[GREP] grep "_cache_age_s\|_source" osrm_client.py | wc -l` → ≥4 hits (set + return)
- [ ] `[GREP] grep "degraded_since\|CIRCUIT OPEN\|RECOVERED" osrm_client.py` → 3+ hits (alert path on entry/exit)
- [ ] `[GREP] grep "degraded_osrm\|degraded_mode" dispatch_pipeline.py` → ≥2 hits (decision_meta + flags propagation)
- [ ] `[CMD]` Off-peak smoke: `docker stop osrm-server; sleep 30; docker start osrm-server` → 2 Telegram alerts ("CIRCUIT OPEN" entry + "RECOVERED po Xs" exit); `grep "degraded_osrm.*true" shadow_decisions.jsonl | tail -10` → entries during outage window
- [ ] `[GREP]` Telegram propose w outage window pokazuje "⚠️ ETA approx" line
- [ ] `[TEST] pytest tests/test_osrm_circuit_breaker.py -v` → all PASS
- [ ] `[SMOKE 24h]` Brak silent fallback (każde circuit-open ma alert w obu kierunkach)

---

### #14 — /health/all + /health/shadow endpoint consolidation (P1)

**Rollback:** `git revert health-aggregator-2026-05-XX --no-edit && sudo systemctl restart dispatch-panel-watcher` (off-peak; endpoint hostowany w `parser_health_endpoint.py` w panel-watcher proces). Adrian: w UptimeRobot dashboard → Monitor `/health/all` → Pause/Delete (żeby nie spamować "Down" alertami po endpoint zniknie).

- [ ] `[CMD] curl -s :8888/health/all | python -m json.tool | grep -E "parser|cron|shadow|telegram|external|status"` → 6 keys present
- [ ] `[CMD] curl -s :8888/health/shadow | python -m json.tool | grep -E "last_proposal_age_sec|latency_p95_ms|alive"` → 3 keys present
- [ ] `[CMD] curl -s :8888/health/all | python -c "import json,sys; d=json.load(sys.stdin); print(d['status'])"` → "healthy" / "degraded" / "critical" (worst-status-wins)
- [ ] `[CMD]` UptimeRobot configured: dashboard pokazuje monitor `/health/all` co 5min, status "Up" (Adrian verify w panelu UptimeRobot)
- [ ] `[CMD]` Stop test: `sudo systemctl stop dispatch-shadow; sleep 90; curl -s :8888/health/all | grep status` → "critical"; `start` → "healthy" w 60s
- [ ] `[TEST] pytest tests/test_health_endpoint_aggregator.py -v` → all PASS
- [ ] `[SMOKE 24h]` UptimeRobot history zero false-positive (każdy "Down" = real degraded mode)

---

### #15 — Schedule staleness warn + STALE_SCHEDULE_AGE event + /status Telegram cmd (P1)

**Rollback:** `git revert schedule-staleness-status-cmd-2026-05-XX --no-edit && cp schedule_utils.py.bak-pre-staleness-2026-05-XX schedule_utils.py && cp telegram_approver.py.bak-pre-status-cmd-2026-05-XX telegram_approver.py`. **`dispatch-telegram` restart MANDATORY Adrian ACK** (peak override). `schedule_today_backup.json` daily snapshot zostaw (no harm — nieużywany bez staleness logic). Pre-fix: schedule fail-open silent stale, brak `/status` (Adrian musi curl :8888 ręcznie).

- [ ] `[GREP] grep -A5 "schedule_age\|STALE_SCHEDULE_AGE" schedule_utils.py` → emit logic present
- [ ] `[CMD] test -f /root/.openclaw/workspace/dispatch_state/schedule_today_backup.json && echo OK` → OK (daily snapshot)
- [ ] `[CMD]` Adrian Telegram: `/status` → response w <3s z formatem OPS §8.1 (parser/shadow/telegram/reconcile/timers/fleet/last_3_proposals); mobile-readable
- [ ] `[CMD]` Mock Sheets API down test: `sudo iptables -A OUTPUT -d sheets.googleapis.com -j DROP; sleep 35min` → Telegram alert "STALE_SCHEDULE_AGE 35min"; dispatch nadal działa (fail-open verified); restore: `iptables -D OUTPUT -d sheets.googleapis.com -j DROP`
- [ ] `[GREP] grep "STALE_SCHEDULE_AGE" /root/.openclaw/workspace/dispatch_state/events.db` (via sqlite query) → ≥1 event during outage simulation
- [ ] `[TEST] pytest tests/test_schedule_staleness.py tests/test_status_telegram_cmd.py -v` → all PASS
- [ ] `[SMOKE 24h]` Adrian używa `/status` minimum 1× → format readable, dane spójne z panel + journalctl manual cross-check

---

## ZADANIE 2: DECISION REGISTER DLA ADRIANA

Format: **Decyzja → Rekomendacja CC → Alternatywy/skutek braku → Deadline → Źródło**.

Wszystkie 14 decyzji wymagają ACK Adriana (techniczne CC sam podejmuje, te są strategiczne).

### Decyzja #1 — Hetzner upgrade CPX22→CPX32 vs CPX32→CPX42

- **Co:** CPX22 (€7.99/m, 2 vCPU, 4GB) → CPX32 (€13.99/m, 4 vCPU, 8GB) [JUŻ WAS od 27.04 wg dispatch_v2/CLAUDE.md] → **CPX42 (€?, 8 vCPU, 16GB)?**
- **Rekomendacja CC:** **CPX42 PRZED Restimo onboarding (Q3 prep)**, NIE czekać do 1-go peakuwypadu. Obecny stan: 87% RAM, 100% swap, 95% disk — pierwszy multi-tenant peak zabije box.
- **Alternatywy / skutek braku:** zostać na CPX32, akceptować systemd MemoryMax jako "soft preemption". Działa solo. **Pierwszy peak Restimo = OOM kaskada w 2 min** (3 procesy × 600MB working set ÷ 8GB CPX32 ÷ kernel + cache + 4 inne = za ciasno).
- **Deadline:** 14.05 (przed flip Faza 7 100% target 31.05) lub Q3 (przed Restimo onboarding) — wybrać która.
- **Źródło:** MULTI_TENANT D + STRATEGIC TOP-3 + ARCH R-2

### Decyzja #2 — Postgres migration: Q2 (zaraz post-Faza 7) vs Q3 (przed Restimo)

- **Co:** META M1 + MULTI_TENANT C + CONCURRENCY §5: migrate `learning_log.jsonl` + `orders_state.json` + `events.db` audit_log → Postgres. Effort 140-160h, 3 mies kalendarzowo.
- **Rekomendacja CC:** **Q3 prep (lipiec 2026), nie wcześniej.** Powód: M1 Postgres bez M4 `core/state_io.py` (1tydz pre-rec) = utopijny. Sekwencja: T4 Faza 7 100% → 1mc bake → state_io.py refactor → DUAL-WRITE Postgres. Jeśli zaczniesz teraz, Faza 7 ramp slipnie.
- **Alternatywy:**
  - **(a)** Zacznij Q2 (post-31.05): 4-mc sprint dispatch focus na PG, ZERO inne strategic. Multi-tenant Q3 ready.
  - **(b)** Q3 prep (~01.07): 6 tyg Postgres pre-Restimo. Restimo onboarding rusza Q3 mid-late.
  - **(c)** **DEFER do Q4** (skutek braku decyzji): Restimo onboarding na obecnym SQLite stack — pierwszy peak odsłoni RC1+RC4+RC5 naraz w prod.
- **Deadline:** 25.05 (pre-Faza 7 100% flip — żeby nie kolidował kalendarz)
- **Źródło:** META M1, MULTI_TENANT P2-DB, CONCURRENCY §5

### Decyzja #3 — Split telegram_approver.py: przed Faza 7 100% flip czy po?

- **Co:** TELEGRAM_APPROVER §3 split na 5 modułów (state/render/admin_status/proposals/router). Krok 1-3 (state/render/admin_status) low-risk extracts (~6h, restart-deferred bundling). Krok 4 (proposals/router) wymaga **dedicated restart `dispatch-telegram` + 5-min smoke window + ACK gate**.
- **Rekomendacja CC:** **Krok 1-3 PO Faza 7 100% flip (czerwiec 2026), Krok 4 DEFER do Q3 prep przed Postgres.** Powód: 3240 LOC `telegram_approver.py` jest "de facto immutable runtime" per Adrian hard rule — refactor podczas Faza 7 ramp = ryzyko paraliżu w peak.
- **Alternatywy:**
  - **(a)** Krok 1-3 w W3-W4 (pre-Faza 7 flip): restart-deferred bundling z TASK A, niski risk.
  - **(b)** **CC rec** — Krok 1-3 czerwiec, Krok 4 lipiec.
  - **(c)** Wszystko DEFER: ryzyko że common.py + telegram_approver kompoundują refactor cost (2026-koniec roku staje się niemożliwy).
- **Deadline:** 25.05 (decyzja kierunku)
- **Źródło:** TELEGRAM_APPROVER §3, ARCH Tier A audit

### Decyzja #4 — Redis przed Postgres czy po (M2 timing)?

- **Co:** META M2: Redis (~1-2 tyg) — distributed locks + pub/sub + leader election. **Pre-rec dla:** `panel_bg_refresh` single-instance (eliminuje V3.27.7 incident pattern), CONFIG_RELOAD broadcast (zamiast events.db pub/sub fallback).
- **Rekomendacja CC:** **Po Postgres (M2 dopiero gdy Postgres LIVE).** Powód: events.db `CONFIG_RELOAD` event (B.3 z MULTI_TENANT) JUŻ rozwiązuje 80% RC2 bez nowej infra. Redis daje cross-host coordination — niepotrzebne dopóki single-server.
- **Alternatywy:**
  - **(a)** Redis NIGDY (do scenariusza multi-host k8s deployment, prawdopodobnie 2027+).
  - **(b)** **CC rec** — Redis post-Postgres Q4.
- **Deadline:** Decyzja luźna (Q3 review)
- **Źródło:** META M2, STATE_OWNERSHIP F4

### Decyzja #5 — Peak-hour restart enforcement: technical (`ExecStartPre=`) czy organizacyjny?

- **Co:** Adrian's rule "ZERO restart 11-14/17-20 Pn-Pt + 16-21 Sb" jest dziś **organizacyjna**. Brak `ExecStartPre=/usr/bin/check-not-peak.sh` ⇒ `Restart=on-failure` może odpalić `dispatch-shadow` w środku peak.
- **Rekomendacja CC:** **Technical gate dla `Restart=on-failure` PLUS Adrian explicit override flag.** Mechanizm: drop-in `peak_guard.conf` z `ExecStartPre=` script który czyta `/run/dispatch_peak_override` (Adrian touch'uje gdy `Restart=on-failure` jest jawnie OK, np. 07.05 firmowe konto deploy).
- **Alternatywy:**
  - **(a)** Sam `ExecStartPre=` bez override — sztywna blokada, ale `firmowe konto sprint 07.05` typ override niemożliwy.
  - **(b)** **CC rec** — script + override flag.
  - **(c)** Zostawić organizacyjne — następny incident Adrian zrobi restart w peak gdy zapomni.
- **Deadline:** Razem z #3 systemd resource limits (W1)
- **Źródło:** ARCH R-12, OPS section 1.6, STATE_OWNERSHIP §6

### Decyzja #6 — Log retention SLA + zewnętrzny scrape

- **Co:** OBSERVABILITY B.4 + OPS R12: external monitoring na `/health/all`. Plus retention dla 25+ logów (`learning_log.jsonl` 110MB, `shadow_decisions.jsonl` 66MB).
- **Rekomendacja CC:**
  - **Retention:** 7d full + 30d sampled (1/10 lines via cron filter). Powód: shadow_decisions ML training value < 7d typowo; learning_log full do reconcile drifts wystarczy 7d.
  - **External scrape:** **UptimeRobot free tier** (50 monitors, 5-min interval) — zero cost, wystarczy. Healthchecks.io ($5/m self-host) tylko jak chcesz pull-mode "service must check in every X" (cleaner dla cron timers ale duplikacja z #4).
- **Alternatywy:**
  - **(a) Retention:**
    - 24h full → po 24h hard delete (compliance-friendly ale stratny ML)
    - **CC rec 7d full + 30d sampled**
    - 30d full (40MB+/typ/30d ≈ 1GB+ per typ — disk pressure)
  - **(b) Monitoring:**
    - **CC rec UptimeRobot free**
    - Healthchecks.io self-hosted ($5/m)
    - Grafana Cloud free tier (10K metrics — overkill na health pings)
- **Deadline:** Razem z #8 logrotate (W2)
- **Źródło:** OBSERVABILITY B.4, OPS R12+R10, ARCH R-14

### Decyzja #7 — SMS gateway dla side-channel alertów Telegram-down

- **Co:** OPS R3 chicken-egg: Telegram bot down → admin alert via Telegram = gone. Side-channel watchdog (#9 z TOP-15) wymaga out-of-band: SMS / email / Healthchecks.io ping.
- **Rekomendacja CC:** **OVH SMS API** (~0.04 PLN/SMS, 1-2 alertów/mies × 1zł/rok). Alternatywnie email z Postfix relay (free, ale 30s+ latency vs SMS 5s). Healthchecks.io przy okazji #6 — daje "no-news = ALERT" który eliminuje większość chicken-egg.
- **Alternatywy:**
  - **(a) Healthchecks.io** (free tier, SMS gateway included w $5/m plan)
  - **(b)** **CC rec OVH SMS API** (cheap + Polish carrier reliability, niezależne od Healthchecks.io)
  - **(c)** Twilio (~$0.05/SMS, US-centric, lepszy dla DR)
  - **(d)** Pominąć — następny Telegram outage = "Adrian zauważy po 4-7 dniach" (vide #4 incident z OnFailure)
- **Deadline:** Razem z #9 (W2)
- **Źródło:** OPS R3 + section 7.2

### Decyzja #8 — Per-tenant config: prep przed Restimo czy przy onboardingu?

- **Co:** MULTI_TENANT A + B.3: `core/tenant_config.py` shim + `tenants/<name>/config.py` + 23 BIAŁYSTOK refs refactor (10h impl) + CONFIG_RELOAD event subscriber (3h).
- **Rekomendacja CC:** **PREP przed Restimo, ~Tydzień 2-3 czerwca (post-Faza 7 100%).** Powód: refactor 23 BIA refs to AIDER deepseek-coder ~6h impl + 4h tests — Restimo onboarding w obliczu nieskonsolidowanego state to scenariusz "1 tydzień onboardingu rozciąga się na miesiąc". Pre-prep zwraca 5× ROI w Restimo onboarding speed.
- **Alternatywy:**
  - **(a)** **CC rec** — prep czerwiec, Restimo Q3 mid (lipiec).
  - **(b)** Onboardowanie Restimo z hardcoded BIA fallback — **debugowanie cross-tenant bugs przez 2 mies**.
- **Deadline:** 25.05 (decyzja kierunku z #2 PG)
- **Źródło:** MULTI_TENANT A + E

### Decyzja #9 — DEFER do Q3: które rekomendacje (capacity constraint)?

- **Co:** Adrian capacity dispatch focus na T-4 calibration + Faza 7 ramp + Daily Q&A + sprint-by-sprint deploys = ~80% / tydz. Strategy reading 7h MASTER_DEPLOY = ~10%. Zostaje ~10% na NOWE deploy (TOP-15). Realnie zmieścimy ~6h/tydz, czyli 4 tyg = 24h ≈ TOP-15.
- **Rekomendacja CC:** **TOP-15 = wszystko co realnie zmieścimy do 04.06. DEFER do Q3 prep:**
  - M1 Postgres dual-write (140-160h)
  - M3 Event sourcing decisions (Q4 chyba 2027)
  - M5 pełne liveness contracts (TOP-15 #4 = MVP, pełen scope dopiero Q3)
  - Split telegram_approver Krok 4 (proposals/router) — Q3
  - Multi-tenant tenant_config (Q3 prep, czerwiec)
  - Naming inconsistency 45 dotted refs cleanup (Q3 prep)
- **Alternatywy:**
  - **(a)** **CC rec** — TOP-15 = 4 tyg, reszta Q3.
  - **(b)** Try-to-fit-all w 8 tyg → realny burnout + decline jakości.
- **Deadline:** Decyzja overall sprintu (08.05 minimum, dziś)
- **Źródło:** META 6-mc roadmap, MULTI_TENANT E, ARCH P0-P2 list

### Decyzja #10 — `dispatch-cod-weekly` re-enable 11.05 (poniedziałek)?

- **Co:** Per project_overview snapshot 06.05 — `dispatch-cod-weekly` disabled, Adrian zaplanowane re-enable 11.05 (poniedziałek 8:00). **Bez OnFailure (#4) re-enable = ryzyko silent stop COD reconciliation tygodnia.**
- **Rekomendacja CC:** **Re-enable PO #4 OnFailure deploy (W1 koniec).** Sekwencja: 14.05 (sob/niedz) deploy OnFailure framework → 18.05 (poniedziałek) re-enable cod-weekly. Daje 1 tydzień buffer na alerting verify.
- **Alternatywy:**
  - **(a)** Re-enable 11.05 jak zaplanowane, OnFailure deploy 14.05 — 3 dni window silent fail risk.
  - **(b)** **CC rec re-enable 18.05** — alerting first.
  - **(c)** DEFER cod-weekly do Q3 — tydzień 11.05 manual reconciliation by Bartek.
- **Deadline:** 10.05 (przed master merge gate)
- **Źródło:** project_overview, OPS section 1.4 (cod_weekly disabled)

### Decyzja #11 — AUTO_PROXIMITY ramp timing 30% → 100% target ~31.05

- **Co:** Faza 7 Etap 0 LIVE shadow od 06.05 wieczór. Spec target: ramp 30% → 100% do 31.05 = 25 dni shadow obs + ramp.
- **Rekomendacja CC:** **Trzymać target 31.05 ALE warunkowo:** TOP-15 #1-#10 (P0) MUSZĄ być deployed przed flip 100%. Jeśli W1+W2 slipnie, ramp 100% slipnie do 07.06. Powód: Faza 7 100% bez OnFailure framework + retry decorator + side-channel watchdog = single peak failure paraliżuje system bez detekcji.
- **Alternatywy:**
  - **(a)** **CC rec** — flip 31.05 conditional na P0 done.
  - **(b)** Slip do 15.06 — buy 2 tygodnie buffer.
  - **(c)** Force flip 31.05 niezależnie — skutek: pierwszy nieoczekiwany incident Faza 7 paraliżuje (Adrian sprawnie przez 4h widzi `Restart=on-failure` flapping).
- **Deadline:** 25.05 (gate decision pre-flip)
- **Źródło:** project_overview T4, dispatch_v2/CLAUDE.md

### Decyzja #12 — Master merge gate 10.05: +32 commits jednorazowo vs partycjonowane?

- **Co:** Branch `sprint-07-05-event-bus-opcza-c` ma +32 commits ahead `master@10c754d`. Master merge gate aktywny 10.05.
- **Rekomendacja CC:** **Squash do 5-7 logical groups + merge jednorazowy 10.05 wieczór (off-peak).** Grupy: (1) parser_health structural fix (4 commits), (2) firmowe konto 6-warstwowa defense (5 commits), (3) Faza 7 Etap 0 shadow LIVE (1 commit), (4) ETAP B kurier roster (kilka), (5) tech debt cleanup (5-6 commits), (6) audit panel/repo wieczór 07.05 (kilka), (7) sprint mockup v2 (kilka).
- **Alternatywy:**
  - **(a)** **CC rec squash 5-7 logical** — master history czytelne, łatwy rollback per-group.
  - **(b)** Wszystko `git merge --no-ff` — 32 commits w master, rollback trudny.
  - **(c)** Cherry-pick selektywnie — czas duży.
- **Deadline:** 10.05 (master merge gate)
- **Źródło:** sprint_timeline, dispatch_v2/CLAUDE.md

### Decyzja #13 — Naming inconsistency 45 dotted refs cleanup

- **Co:** ARCH R-13: 45 hardcoded dotted refs w 13 plikach ("Mateusz Cz." etc.). Adrian decyzja A "deferred". Multi-tenant ZABLOKOWANE do cleanup.
- **Rekomendacja CC:** **Cleanup pre-Restimo (Q3 prep, czerwiec).** Powód: każdy nowy programmer / nowa CC sesja musi nauczyć się landminy. Restimo sprint = +5 nowych "kierowników" ze swoją nomenklaturą = trampolina dla bugu Lekcji #54-style.
- **Alternatywy:**
  - **(a)** **CC rec cleanup czerwiec** (Q3 prep, AIDER ~3h refactor).
  - **(b)** Maintain status quo — co kwartał +1 incident.
- **Deadline:** 30.06 (pre-Restimo)
- **Źródło:** ARCH R-13, sprint_timeline

### Decyzja #14 — Capacity sprint vs strategy: 7h reading czy 7h deploy?

- **Co:** Adrian dziś (07.05 wieczór) skończył 9 audytów × 25KB każdy = 225KB tekstu strategicznego. Konsolidacja TOP-15 (ten plik) = ~24-27h impl. **Czy Adrian/CC mają capacity wykonać TOP-15 w 4 tyg + Faza 7 ramp + sprint-by-sprint?**
- **Rekomendacja CC:** **Tak, ale tylko w trybie "1 sprint TOP-15 / tydzień + 1 sprint regular / tydzień".** TOP-15 W1 (~7h) + sprint regular Faza 7 calibration (~4h) = ~11h/tydz Adrian + ~14h/tydz CC. Bardziej tego nie ma sensu próbować.
- **Alternatywy:**
  - **(a)** **CC rec — TOP-15 strict + regular sprint każdy tydzień** (4 tyg cycle).
  - **(b)** TOP-15 spread do 6-8 tyg, slip Faza 7 100% do 15.06.
  - **(c)** Skip TOP-15 P1 (#11-#15), zrobić tylko P0 (#1-#10) w W1-W2 = ~14h, slip Faza 7 do 31.05 niezmiennie. Saver-friendly.
- **Deadline:** Decyzja teraz (08.05 rano)
- **Źródło:** Wszystkie 9 audytów

---

## ZADANIE 3: ZAKTUALIZOWANY BACKLOG (NEW #-y)

**Stan obecny tech_debt_backlog.md (per MEMORY.md):** 18/22 DONE post-evening 07.05. Pozostałe 5 P2/P3: #6 R-04 Step 2-3 (gate 14.05), #17 firmowe_konto_company_addresses.json static map, #18 geocoding_log.jsonl audit trail, #20 POSTPONE proper auto-replan, #21 F7AGREE row vs 4-button strict UX collision.

**Numery #1-#22 zajęte. Następny wolny: #23.**

**Audyty proponowały konfliktowe numery (#22-#27 trzy razy nadpisane).** Konsoliduję w **#23-#37** (15 nowych zadań) jednym ciągiem:

```
### #23 [Z2] Disk cleanup pre-Restimo — orphan files + .bak retention + accidental dirs (P0)
Effort: 1h. Pattern: bulk find+delete (`Let me produce the blocks*` accidental dir, nested
`dispatch_v2/dispatch_v2/`, `.tmp_cr2kure6.json` 5.5MB, .bak >24h). Truncate `learning_log.jsonl`
>30d zachowując ostatnie 30 dni dla reconcile. Audit `events.db` reduces.
Dotyczy: `/root/.openclaw/workspace/`. Zapobiega: disk full incident 09-12.05.
Cross-ref: MULTI_TENANT P0-INFRA, ARCH P0#5, STRATEGIC top-5.

### #24 [Z2] systemd MemoryMax/CPUQuota/WatchdogSec/StartLimitBurst dla 16 unitów (P0)
Effort: 1.5h impl + bulk deploy. Pattern: drop-in `resource_limits.conf` per unit, profile
per-service (shadow=1.5G/150%, panel-watcher=800M/80%, telegram=600M/50%, oneshot timers
300-400M/40-100%). Zapobiega: kaskadowy OOM kill 3-5 procesów w 1-2 min podczas peak.
Cross-ref: MULTI_TENANT D, ARCH P0#3, STRATEGIC top-5.

### #25 [Z2] OnFailure systemd framework + cron_health watchdog + alert (P0)
Effort: 30min minimum (sam template) → 3-4h pełen scope (AIDER deepseek-coder ~260 LOC + tests).
Pattern: generic instantiated unit `dispatch-onfailure-alert@.service` + drop-in `OnFailure=`
dla 14 dispatch-* serwisów + watchdog timer 5min cron_health.json writer. Aler ma 8 pól actionable
(Result/Exit/LastSuccess/Logs/Playbook/Severity/DedupKey/Hint). 
Dotyczy: 14 systemd unit drop-iny + nowy moduł `observability/`. 
Zapobiega: incident overrides-reset 03-07.05 4 dni cicho — detection latency 96h → 9h (10× szybsza).
Cross-ref: META top-1, OBSERVABILITY A+B+D, OPS R2, STATE_OWNERSHIP F2, STRATEGIC top-1.

### #26 [Z2] event_bus.emit() retry decorator + cleanup() peak-aware guard (P0)
Effort: 45min total (30 retry + 15 cleanup). Pattern: `@with_retry(3, exp_backoff=[100,500,2000])`
na 5 callsite Telegram callback handlers + `if _is_peak_window(): return 0` early-exit w cleanup
(skip 11-14, 17-20 Pn-Pt + 16-21 Sb).
Dotyczy: `event_bus.py` cleanup, `telegram_approver.py` _handle_assign/inny/koord/koniec/poprawa_callback.
Zapobiega: permanent event loss przy SQLITE_BUSY 5s w peak callbacks (worst-case 30 SKIP/h).
Cross-ref: CONCURRENCY P0×2, MULTI_TENANT P0-SAFETY.

### #27 [Z2] flags.json atomic write helper + flags_admin.py central tool (P0)
Effort: 1h. Pattern: `core/flags_io.py` z `update_flag(key, value)` (lockfile + tempfile + os.replace),
replace ad-hoc `json.dump(open(p,'w'))` w 4-5 callsite. CLI tool `flags_admin.py set FLAG VALUE`.
Dotyczy: ad-hoc Python helpers. Zapobiega: "Adrian + parallel CC = corrupt flagi" race (R-7 ARCH).
Cross-ref: ARCH P0#4, STRATEGIC top-4.

### #28 [Z2] Haversine fail-loud cross-codebase audit + guards (P0)
Effort: 1h (audit ~30min `grep -rn "haversine\|_haversine"` + 4-5 guard fixes ~30min). Pattern:
każdy call-site dostaje `if coords is None or coords == (0,0): raise ValueError` PRZED haversine.
Dotyczy: `osrm_client.py:haversine` + 4-5 callsites poza dispatch_pipeline (sprawdzić
courier_resolver, route_simulator_v2, scoring). Zapobiega: silent 6285km bug class (firmowe konto
sprint #4 częściowo fix, ten audit kompletuje per Lekcja #81 cross-codebase).
Cross-ref: OPS R4, OBSERVABILITY C.2, Lekcja #81.

### #29 [Z2] Logrotate config dla 25+ logów (P0)
Effort: 1h. Pattern: `/etc/logrotate.d/dispatch-v2` z `daily / rotate 14 / size 50M / compress`
dla 25+ paths (`learning_log.jsonl` 110MB, `shadow_decisions.jsonl` 66MB, `dispatch.log` 25MB,
`czasowka.log` 12.7MB, eod_drafts/*, ml_data_prep/*). USR1 signal dla rotation pickup.
Dotyczy: `/root/.openclaw/workspace/scripts/logs/` + `dispatch_state/*.jsonl`.
Zapobiega: dysk skończy się przy 10× orderów (1.1GB/mies/typ).
Cross-ref: ARCH P0#2, STRATEGIC top-2, OPS R12.

### #30 [Z2] Telegram side-channel watchdog out-of-band (P0)
Effort: 1h impl + provider config. Pattern: nowy `dispatch-tg-heartbeat.timer` co 60s `getMe`,
≥3× fail → SMS Adrian via OVH API (decyzja #6 SMS gateway). Eliminuje chicken-egg "Telegram down
→ alert via Telegram = gone".
Dotyczy: NEW serwis. Zapobiega: 4-7 dni silent Telegram outage (vide overrides-reset class).
Cross-ref: OPS R3 + section 7.2.

### #31 [Z2] _shutdown_drain telegram_approver + 10 silent killer except (P0)
Effort: 1h drain + 3-4h 10 except = ~5h total. Pattern: 
- `_shutdown_drain()` async w `main_async` try/finally — final `save_pending(state['pending'])` flush.
- 10 except handlers Kategoria A z TELEGRAM_APPROVER §4: lines 79, 260, 384, 400, 1017, 1161, 1290, 1715, 1729, 2686. Każdy dostaje `_log.warning(f"context oid={...} cb_id={...} exc={...}")` per Lekcja #32.
Dotyczy: `telegram_approver.py`. Zapobiega: SIGTERM race window 50µs (KeyError w callback)
+ 10 invisible bug class.
Cross-ref: TELEGRAM_APPROVER §2+§4, Lekcja #32.

### #32 [Z3] core/jsonl_appender.py — atomic JSONL append cross-process (P1)
Effort: 3-4h (impl ~60 LOC + tests ~80 LOC + 3-callsite migration). Pattern: `os.O_APPEND` single
os.write loop + `fcntl.flock LOCK_EX` (cross-process serialization), `max_bytes=1MB` sanity guard,
`EINTR` retry. Migracja `panel_watcher.py:139`, `telegram_approver.py:1257`, `shadow_dispatcher._append_decision`.
Eliminuje silent corruption 30-40% linii w `learning_log.jsonl` (>8192B BufferedWriter chunking).
Dotyczy: NEW `core/jsonl_appender.py` + 3 callsites. Zapobiega: corrupt training data dla LGBM
(Z3 fundament). Pre-rec dla M1 Postgres dual-write.
Cross-ref: META top-2, CONCURRENCY §4, STATE_OWNERSHIP F1, ARCH R-4, MULTI_TENANT P1-DB.

### #33 [Z3] _COORDS mtime reload w panel_watcher cycle + V3.27.5 race test (P1)
Effort: 1h total (30 _COORDS + 30 race test). Pattern:
- `panel_watcher.py:67-78` mtime check w cycle co 15s — reload jeśli mtime zmieniona.
- `tests/test_state_machine_race_v3275.py` 4 scenariusze (PICKED_UP→COURIER_ASSIGNED preserve, 
  DELIVERED→preserve, normal assigned→update, multiple ASSIGNED in 5s).
Dotyczy: `panel_watcher.py`, `tests/`. Zapobiega: nowa restauracja silent BRAK KANDYDATÓW
do ręcznego restart panel-watcher (peak-blackout violation) + 13.4% bug rate regresja niewidoczna.
Cross-ref: META top-5 quick win, STATE_OWNERSHIP F3 + F8.

### #34 [Z2] OSRM circuit breaker degraded mode 3-warstwowo (P1)
Effort: 2h. Pattern:
- L1 `_cache_age_s` w returnie (caller widzi czy 1s czy 58min stale).
- L2 alert na entry/exit `degraded_since` watermark (`🔴 OSRM CIRCUIT OPEN` + `✅ OSRM RECOVERED po Xs`).
- L3 caller propagation `decision_meta["degraded_osrm"]=True` + `flags["degraded_mode"]=True` w shadow log.
Dotyczy: `osrm_client.py`, `dispatch_pipeline.py:_v327_eval_courier`. Zapobiega: silent ETA Haversine
bez świadomości operatora (Hot Spot #10).
Cross-ref: OBSERVABILITY C.1, STATE_OWNERSHIP F7.

### #35 [Z2] /health/all + /health/shadow endpoint consolidation (P1)
Effort: 1.5h. Pattern: rozszerzyć `:8888/health/parser` o aggregator `worst-status-wins` z
`cron_health.json` + shadow heartbeat (`last_proposal_age_sec`, `latency_p95_ms`) + telegram queue
+ reconcile drift + `external` (osrm_circuit_status, panel_login_status, sheets_cache_age).
External scrape UptimeRobot free na `/health/all` co 5min.
Dotyczy: `parser_health_endpoint.py`. Zapobiega: ślepe na shadow stuck + telegram down (chicken-egg).
Cross-ref: OBSERVABILITY B.4, OPS R7+R10.

### #36 [Z2] Schedule staleness warn + STALE_SCHEDULE_AGE event + /status Telegram cmd (P1)
Effort: 30min schedule warn + 1.5h /status cmd = 2h. Pattern:
- Alert gdy `schedule_age > 30min` (nadal fail-open dla dispatch, ale loud).
- Daily snapshot `schedule_today_backup.json` (fallback gdy Google completely down).
- `/status` Telegram cmd format z OPS §8.1 (parser/shadow/telegram/reconcile/timers/fleet/last_3_proposals).
Dotyczy: `schedule_utils.py` + `telegram_approver.py:handle_message`. Zapobiega: weekend Sheets API
outage 2-3× rok = brak detekcji + Adrian musi entry w panel + curl :8888 ręcznie.
Cross-ref: OPS R6 + R10, STATE_OWNERSHIP F12.

### #37 [Z3] core/state_io.py boundary consolidation (P2, Q3 prep)
Effort: 4-5h. Pattern: single shim `state_io.py` z `read_orders_state`, `update_order(oid, fields,
event_name)`, `read_courier_plans`, `update_plan(...)`. Wszystkie inne moduły MUSZĄ używać tych
funkcji. CI grep test: `open.*orders_state.*"w" poza state_io = fail build`. Pre-rec dla M1 Postgres
(zamień backend z JSON na PG bez dotyk callsite).
Dotyczy: NEW `core/state_io.py` + refactor ~10 callsite (state_machine, panel_watcher, reconcile,
sla_tracker). Zapobiega: nowy moduł (Bolt Food, Restimo) doda swoje `open(orders_state, "w")` →
race + corruption gwarantowana w T+6 mc.
Cross-ref: META M4, STATE_OWNERSHIP F20, CONCURRENCY §6, MULTI_TENANT B.3.
```

**Sumarycznie 15 nowych zadań #23-#37.** P0 = 9 (#23-#31), P1 = 7 (#32-#36 = 5, ale #36 to combo 2 mini → policzmy 8 P0 + 6 P1 + 1 P2). P2 = 1 (#37 Q3 prep).

---

## ZADANIE 5: CROSS-AUDIT SPRZECZNOŚCI

**TELEGRAM_APPROVER_GOD_OBJECT §6 jawnie koryguje META audit:**
- META Risk #3 P×I=16 "subprocess.run blokuje event loop" → KOREKTA: subprocess.run JEST wrapped w `await asyncio.to_thread(...)` przy każdym callsite (linia 2995, 3010, 3028 dla `run_gastro_assign`; 1877 dla `format_status`). Realne ryzyko = thread pool slot occupancy. Skorygowane P×I~6 (P2 nie P0).
- META Risk #1 P×I=20 "pending_proposals 2 writers" → KOREKTA: single-writer (`telegram_approver._save_pending` linia 1247 ATOMIC tempfile+fsync+os.replace). Audit pomieszał `save_plan_on_assign` (plan_manager, inny plik `courier_plans.json`) z `save_pending` (telegram, `pending_proposals.json`). Real gap = brak `_shutdown_drain()` w `main_async` (race window 50µs). Skorygowane P×I~8 (P0 ale fix to shutdown drain ~30min, NIE DB migration ~2 dni).

**Pozostałe sprzeczności (NOWE, nie w TELEGRAM_APPROVER §6):**

### Sprzeczność #1: PIPE_BUF jako mechanizm corruption JSONL

**STATE_OWNERSHIP §F1 line 111** twierdzi:
> "Avg bytes/linia: 6962 (zmierzone na produkcji, 110MB / 15843 linii). Linux PIPE_BUF = 4096 B → POSIX gwarantuje atomicity tylko ≤PIPE_BUF. Linie >4KB mogą się przeplatać."

**CONCURRENCY §1a-b line 45** koryguje (cytat):
> "Teza w ticket: 'PIPE_BUF=4096, avg line=6962 B → interleaving.' **Faktycznie:** PIPE_BUF (4096 B na Linux) gwarantuje atomicity **tylko dla pipes/FIFOs** (POSIX.1-2017 §3.265). Dla regular files **nie istnieje** PIPE_BUF-klasy gwarancja. (...) Linijka >8192 B → wiele `os.write` calls → wiele inter-syscall okien."

**Status:** STATE_OWNERSHIP §F1 jest **NIESKORYGOWANY**. CONCURRENCY §1a-b explicit odpowiada na tezę, ale META, ARCH, STRATEGIC nadal cytują "PIPE_BUF" jako mechanizm.

**Implikacja:** mechanizm korupcji to **BufferedWriter chunking (8192B default)**, NIE PIPE_BUF. To wpływa na threshold (linie >8192B czyli 30-40% nie >4KB czyli ~50%) ale NIE na fix (jsonl_appender z fcntl.flock + os.O_APPEND single os.write loop działa poprawnie w obu interpretacjach).

**Akcja:** w komentarzach do #32 (`core/jsonl_appender.py`) wyraźnie cytować CONCURRENCY §1a-b semantykę, nie STATE_OWNERSHIP §F1.

### Sprzeczność #2: Effort estimates dla OnFailure framework

**3 audyty dają 3 różne numery:**
- STATE_OWNERSHIP §F2: **30 min** (sam OnFailure template — MVP)
- OPS §R2: **2h** (OnFailure + cron_health.json basic + restart_loop_detector)
- OBSERVABILITY §A+B+D: **3-4h** (full deliverable: OnFailure + watchdog + cron_health + onfailure_alert + tests, AIDER ~260 LOC + audit)

**Status:** NIE jest to sprzeczność — **różny scope per audit**. Ale risk = Adrian widzi "30 min" i myśli że to całość, podczas gdy realnie potrzebuje 3-4h dla pełnego deliverable.

**Akcja:** w #25 (OnFailure framework) explicit: "minimum 30 min daje 80% gain (sam template + 14 drop-iny), pełen scope 3-4h obejmuje watchdog + cron_health.json + tests".

### Sprzeczność #3: ARCH scalability "stable do 2× obecnego (~600 ord/d)" vs MULTI_TENANT moment krytyczny "700-900 orderów/d/tenant"

**ARCH §6 line 336:**
> "stable do 2× obecnego loadu (~600 ord/d), za to ~6×+ (1500 ord/d) wymaga przepisania persistence layer"

**MULTI_TENANT §C.3:**
> "Moment krytyczny dla Białystok solo: ~700-900 orderów/d (×3-4 obecnego)."

**Status:** **NIE jest to sprzeczność** — różne metryki:
- ARCH §6 = vertical scaling stability (CPU + RAM + ThreadPoolExecutor 10w × 2 vCPU oversubscribe).
- MULTI_TENANT §C.3 = SQLite WAL write contention (30-50 sustained writes/sec moment krytyczny).

Spójne: obecnie ~250 ord/d, ARCH stable do ~600 (CPU/RAM bound), MULTI_TENANT critical do ~700-900 (DB bound). Bottleneck przejdzie z CPU na DB pomiędzy 600-900.

**Akcja:** brak (oba poprawne).

### Sprzeczność #4: dispatch_v2/CLAUDE.md "CPX22→CPX32 EXECUTED 27.04 wieczór" vs MULTI_TENANT "obecnie KRYTYCZNY 87% RAM 100% swap"

**dispatch_v2/CLAUDE.md line 645:**
> "Hetzner upgrade CPX22→CPX31 niedziela rano 26.04 (...). 4 vCPU / 8 GB RAM (...). EXECUTED 27.04 wieczór ✓"

**MULTI_TENANT §D.1:**
> "Memory: 7.6Gi total / 6.6Gi used / 394Mi free → 87% RAM saturation. Swap: 4.0Gi / 4.0Gi → 100% swap full."

**Status:** **NIE jest to sprzeczność** — CPX32 used now (8GB RAM = 7.6Gi total). 87% saturation TO JEST na CPX32. MULTI_TENANT §D.2 explicit dyskusyjnie podaje że dla 3 tenantów potrzeba **CPX42** (16GB), bo CPX32 jest już ciasny solo.

**Akcja:** brak (Decision #1 powyżej rekomenduje CPX42 pre-Restimo).

### Verdict końcowy

**Po fact-check 9 audytów wewnętrznie SPÓJNYCH** poza:
- **Sprzeczność #1 (PIPE_BUF):** ARCH/META/STATE_OWNERSHIP cytują PIPE_BUF, CONCURRENCY explicit koryguje. Praktyka deploy nie jest dotknięta (#32 jsonl_appender działa w obu interpretacjach), ale akademicko corner-case należy poprawić.
- **Sprzeczność #2 (OnFailure effort):** różny scope, NIE faktyczna sprzeczność.
- **Pozorne #3, #4:** różne metryki / kontekst, NIE sprzeczność.

**Podsumowanie:** 9 audytów po korektach z TELEGRAM_APPROVER §6 + uwzględnieniu CONCURRENCY §1a-b PIPE_BUF correction = **wewnętrznie spójne**. Jeden niezakończony szczegół semantyczny (PIPE_BUF vs BufferedWriter chunking) NIE wpływa na deploy strategy.

---

## Cross-ref artefakty

| Artefact | Path |
|---|---|
| **TEN PLAN** | `dispatch_v2/AUDIT_2026-05-07/MASTER_DEPLOY_PLAN_2026-05-07.md` |
| Architecture audit | `ARCHITECTURE_AUDIT_2026-05-07.md` |
| Concurrency audit | `CONCURRENCY_DATA_INTEGRITY_AUDIT_2026-05-07.md` |
| Meta-audit roadmap | `META_AUDIT_ROOT_CAUSES_ROADMAP_2026-05-07.md` |
| Multi-tenant scalability | `MULTI_TENANT_SCALABILITY_AUDIT_2026-05-07.md` |
| Observability self-healing | `OBSERVABILITY_SELF_HEALING_AUDIT_2026-05-07.md` |
| Operational resilience | `OPERATIONAL_RESILIENCE_AUDIT_2026-05-07.md` |
| State ownership / event flow | `STATE_OWNERSHIP_EVENT_FLOW_AUDIT_2026-05-07.md` |
| Strategic risk synthesis | `STRATEGIC_RISK_SYNTHESIS_2026-05-07.md` |
| Telegram approver god object | `TELEGRAM_APPROVER_GOD_OBJECT_ASYNC_AUDIT_2026-05-07.md` |
| Tech debt backlog (memory) | `MEMORY/tech_debt_backlog.md` (post-evening 07.05: 18/22 DONE; po deploy TOP-15 dodaje #23-#37) |
| Sprint timeline (memory) | `MEMORY/sprint_timeline.md` |
| Lessons learned (memory) | `MEMORY/lessons.md` (#1-#88 + 3 NEW post-deploy: #89 OnFailure mandatory, #90 cron_health single-writer, #91 degraded mode entry+exit alert) |

---

## Re-audit cadence

- **Pre-Faza 7 100% flip (~Tydzień 4, ~30.05):** zweryfikuj #23-#31 (P0) DONE. Decyzja gate #11 (force flip vs slip).
- **Pre-Restimo onboarding (Q3 2026):** zweryfikuj M1 (Postgres) + M2 (Redis) + M5 (liveness contract) LIVE. Decyzje #2 + #4 + #8 mandatory ACK.
- **Post-Restimo W2 (Q3 2026):** re-run pełen audyt — które z 6-7 RC klas dalej żyją? Empiryczny test 6-mc roadmapy.

---

**Status dokumentu:** complete. Pending: ACK Adrian na 14 decyzji + sekwencja TOP-15 deploy.

**Pierwszy ruch dziś:** zaczyna się od **#23 disk cleanup (1h)** + **decyzja #1 Hetzner upgrade**. Po tym W1 sekwencja #24-#28 w ciągu 5-7 dni.

**Author:** CC consolidation (post 9-audit synthesis 07.05.2026 wieczór).
**Effort konsolidacji:** 1 sesja CC, ~1h. Zaoszczędzona praca Adriana czytania 9 audytów × 25KB = ~3h read time + niespójny mental model.
