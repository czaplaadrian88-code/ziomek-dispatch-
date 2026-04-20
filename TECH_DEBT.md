# TECH DEBT — Ziomek

## General rules (wpisane 2026-04-20)

### Flag bez konsumenta = `_PLANNED` suffix
Jeśli w `common.py` dodajesz feature flag ale consumer (kod który flagę czyta
w gałęzi decyzyjnej) nie istnieje jeszcze w prod — nazwa flagi MUSI kończyć się
na `_PLANNED`. Zapobiega footgun'om w roadmapie (flip flagi bez efektu bo brak
consumera). Przykład: `ENABLE_SPEED_TIER_LOADING_PLANNED` (2026-04-20: consumer
w `courier_resolver.build_fleet_snapshot` nie jest zaimplementowany, rename per
V3.19e pre-work).

Weryfikacja przy każdym dodawaniu flagi:
```bash
cd /root/.openclaw/workspace/scripts/dispatch_v2
grep -rn --include=\*.py --exclude=common.py --exclude=\*.bak\* <FLAG_NAME> .
```
Jeśli grep zwraca tylko `tests/` albo pusto → dodaj `_PLANNED` suffix.

## 2026-04-20 — pre-peak sesja

### P0 — GPS BACKGROUND TRACKING BROKEN (priorytet najwyższy)
- **Problem:** Courier APK (pl.nadajesz.courier) przestaje wysyłać GPS **natychmiast po zminimalizowaniu aplikacji** na wszystkich telefonach, od początku istnienia aplikacji
- **Wpływ biznesowy:**
  - Bartek Gold Standard (R1 8km p90) kalibrowany na stale positions
  - Cała hierarchia pos_source oparta na starych punktach (>60 min)
  - Kurierzy muszą trzymać apkę w foreground → UX problem, rozładowuje baterię, rozpraszanie
  - **V3.21 wave_scoring flip ZABLOKOWANY** do czasu fix'a (wave scoring mocno zależy od real-time GPS)
- **Prawdopodobne root causes (do weryfikacji post-peak):**
  - Brak foregroundServiceType="location" w AndroidManifest (Android 14 requirement)
  - FGS notification nie ustawiony jako ongoing() → Android kills po onStop()
  - Brak REQUEST_IGNORE_BATTERY_OPTIMIZATIONS dialog / whitelisting w Doze mode
  - Upload coroutine uwiązana do activity lifecycle zamiast FGS scope
  - WakeLock nie acquired podczas GPS polling
  - Room write skipping gdy process zabity przez Android
- **Fix:** sesja deep-dive + build APK + test na urządzeniu, **PO peakiem 20.04.2026 (16:00+)** lub w innym nie-peak oknie
- **Workaround dzisiaj:** kurierzy trzymają apkę otwartą w foreground (nie ideał, ale działa)
- **Referencja kodu:** /root/courier-app/ (Kotlin+Compose), package pl.nadajesz.courier, backend :8767

### P1 — 70 zombie orders w orders_state.json
- Wynik 11 restartów panel-watcher + 2× SIGKILL wczoraj podczas V3.19 deploy (17:37, 20:17 UTC)
- Stuby status=planned z history=[NEW_ORDER only], brak courier_id/assigned_at/picked_up_at/delivered_at
- Range oid: 466976-467159, first_seen 2026-04-19 08:31-14:25 UTC
- 0/70 w courier_plans.json stops (cross-ref OK)
- **Obecnie SAFE:** guard ENABLE_PENDING_QUEUE_VIEW=False (common.py:282) blokuje ich przed dispatch_pipeline
- **Stają się GROŹNE przy:**
  - V3.21 flip (C5 wave_scoring) — jeśli będzie wire-up z pending_queue
  - V3.22 flip (C7 pending_queue) — bezpośrednio otwiera gate
- Backup state: /tmp/state_backup_pre_cleanup_20260420_081544/
- **Fix (przed C5/C7 flip):**
  1. Hard filter w state_machine.get_by_status: exclude not courier_id and first_seen < now - STALE_TTL (6h)
  2. One-shot soft-mark script: status=expired + event STALE_CLEANUP dla 70 zombie (audit trail)
  3. (Opcjonalnie) reconcile fetch z panelu dla potwierdzenia (404/status=7/8/9)

### P2 — Strukturalny fix: reconcile-on-startup w panel_watcher
- Bez tego KAŻDY restart panel-watchera może produkować zombie (precedens: 70 w 1 dzień)
- Dodać do panel_watcher startup hook:
  - Find orders status=planned + history<=1 + first_seen > 6h
  - Fetch panel dla każdego oid
  - Update status jeśli panel potwierdza delivered/cancelled
  - Mark expired jeśli 404 w panelu
- Zapobiega akumulacji długu między deployami
- **Fix razem z P1** przed C5/C7 flip

### P3 — COD Weekly: auto-tworzenie bloku payday
- Obecnie co poniedziałek 08:00 UTC job failuje gdy brak kolumny z payday=+3 dni w row 1 arkusza
- Workaround: Adrian ręcznie dopisuje datę → 5 min/tydzień + ryzyko zapomnienia (restauracje nie dostaną wypłat)
- Telegram alert działa OK: "Target column fail: Brak bloku z payday=X. Dodaj ręcznie w arkuszu datę wypłaty"
- **Fix:** w /root/.openclaw/workspace/scripts/dispatch_v2/cod_weekly/run_weekly.py dodać auto-append bloku kolumn dla target payday jeśli nie istnieje
- Estymacja: 30 min + test dry-run

### P4 — CLAUDE.md + project memory: update procedury gateway restart
- Obecnie w CLAUDE.md: "docker compose restart openclaw-gateway" (niepełne, nie działa z CWD poza /root/openclaw)
- Poprawnie: "cd /root/openclaw && docker compose restart openclaw-gateway" LUB "docker restart openclaw-openclaw-gateway-1"
- Container name: double-prefix (project=openclaw, service=openclaw-gateway) -> name=openclaw-openclaw-gateway-1
- Compose file: /root/openclaw/docker-compose.yml
- **Fix:** edit CLAUDE.md + /root/.openclaw/memory/project_f22_v319_v320_complete.md

### P5 — Gateway memory leak weryfikacja
- Wczoraj (19.04): 6× OOM kill między 12:50-15:51 UTC (V3.19 deploy chaos, RSS 760-980 MiB)
- Dziś (20.04): growth rate ~8 MiB/h w idle (baseline 07:59 UTC: 1020 MiB -> 10:34 Warsaw: 1025 MiB)
- **Hipoteza:** leak był triggered przez 11 restartów + intensywny debug podczas deploy, NIE jest systemowy
- **Fix = obserwacja przez tydzień:**
  - Jeśli growth <20 MiB/h stabilnie -> zamknąć jako solved (closed-root-cause: deploy chaos)
  - Jeśli spike się powtórzy (>50 MiB/h w normalnej pracy) -> deep dive (Node heapdump, profiling)
- Threshold operacyjny: 1.5 GiB = restart przed peakiem
- Restart procedure: cd /root/openclaw && docker compose restart openclaw-gateway
