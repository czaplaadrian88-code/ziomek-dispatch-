# 🏛 STATE OWNERSHIP & EVENT FLOW AUDIT — dispatch_v2

**Data:** 2026-05-07 wieczór
**Branch:** `sprint-07-05-event-bus-opcja-c` (+22 commits ahead master)
**Audytor:** CC
**Cel:** odporność na partial failures + wieloletni rozwój

---

## 1. State Ownership Map (TL;DR)

| Domena | Source of truth | Type | Writers | Readers | Sync | Consistency |
|---|---|---|---|---|---|---|
| **Order status (2-9)** | `orders_state.json` | JSON | `state_machine` + `panel_watcher` (via emit) + `reconciliation` | `dispatch_pipeline`, `courier_resolver`, `parser_health_layer3`, `czasowka_scheduler` | fcntl LOCK_EX + atomic temp→fsync→rename | **STRICT** |
| **Courier-order assignment** | `orders_state.json[oid].courier_id` | JSON | identyczne ↑ | identyczne ↑ | identyczne ↑ | **STRICT** |
| **Saved plan (TSP)** | `courier_plans.json` | JSON + `.lock` | `plan_manager` | `dispatch_pipeline`, `plan_recheck`, `route_simulator_v2` | fcntl + plan_version | **STRICT + CAS** |
| **Event queue** | `events.db` (SQLite WAL) | DB | `panel_watcher` + `state_machine` | `shadow_dispatcher` (NEW_ORDER), `sla_tracker` (PICKED_UP/DELIVERED), `learning_analyzer`, `r04_evaluator` | WAL + busy_timeout=5000 + `BEGIN IMMEDIATE` | **STRICT** (event_id PK + INSERT OR IGNORE) |
| **Decision audit trail** | `learning_log.jsonl` | JSONL append-only | **3 procesy**: shadow + panel-watcher + telegram | learning_analyzer, r04_evaluator, daily_briefing, validation_gate_lgbm | **brak locka** | **EVENTUAL — RYZYKO** ⚠️ |
| **Pending proposals** | `pending_proposals.json` | JSON | telegram_approver | dispatch + telegram | atomic write | STRICT |
| **Courier tier** | `courier_tiers.json` | JSON | `r04_apply.py` (cron 03:00 daily) | `courier_resolver` (mtime cache) | atomic write | per-process eventual (mtime check) |
| **Manual overrides** | `manual_overrides.json` | JSON | telegram (parse_command) + cron reset 06:00 | `courier_resolver:629` (fresh read per call) | atomic write + fresh-read | **strict-eventual** |
| **Schedule** | Google Sheets | API + cache | fetch_schedule.py (T3 hot-refresh) | courier_resolver | TTL 10 min | best-effort |
| **GPS** | `gps_positions_pwa.json` | JSON | `gps_server` HTTP POST | courier_resolver | atomic write | TTL 20 min |
| **Restaurant coords** | `restaurant_coords.json` | JSON | `geocoding.py` (when miss) | `panel_watcher._COORDS` (singleton, **NEVER reloaded**), `dispatch_pipeline:318` (re-read per call) | none for static dict | **STALE-PRONE** ⚠️ |
| **Geocode cache** | `geocode_cache.json` | JSON | `geocoding.py` | dispatch_pipeline | fcntl flock | **NO TTL — STALE-PRONE** ⚠️ |
| **OSRM route cache** | in-memory `_route_cache` | dict | `osrm_client` per-process | osrm_client | RLock | TTL 60 min, **NIE shared między procesami** |
| **Panel session** | `_session` singleton w `panel_client` | dict | bg_refresh + inline | wszystkie procesy używające panel_client | threading.Lock | **per-process, V3.27.7 incident pattern** |
| **Parser health rolling** | `parser_health.json` + deque | JSON + memory | parser_health (panel_watcher) | parser_health_endpoint :8888 | threading.Lock + atomic write | strict |
| **Roster (kurier_*.json)** | 4 plików | JSON | `courier_admin.add_new_courier` (atomic 4-file z rollback) | wszystkie procesy | rare write | strict |

---

## 2. Event Flow Diagram (steady state)

```
PANEL (NadajeSz)
   │ fetch HTML co ~10s
   ▼
panel_watcher [singleton process]
   │  diff_and_emit + reconcile + V3.20 packs_ghost_detect
   │  + V3.15 panel_packs fallback (15-90s lag fix)
   │  + L0 firmowe konto uwagi parser (07.05)
   │
   ├─emit──▶ event_bus (events.db SQLite WAL)
   │           │ event_id = {oid}_{type}_{ts_ms} (deterministic, INSERT OR IGNORE)
   │           │ FIFO ORDER BY created_at ASC
   │           │
   │           ├─consumer NEW_ORDER─────▶ shadow_dispatcher [singleton]
   │           │                            │  dispatchable_fleet() → assess_order()
   │           │                            │  → shadow_decisions.jsonl + learning_log
   │           │                            │  → mark_processed
   │           │                            ▼
   │           │                          telegram_approver [singleton, NIGDY restart bez ACK]
   │           │                            │  proposal sender → pending_proposals.json
   │           │                            │  callback handler → emit COURIER_ASSIGNED
   │           │                            │  + auto-KOORD watchdog 5 min timeout
   │           │
   │           ├─consumer PICKED_UP/DELIV─▶ sla_tracker [singleton]
   │           │                            │  state.upsert + sla_log + R6 BAG_TIME alert (suppressed 07.05)
   │           │                            ▼
   │           │                          mark_processed
   │           │
   │           └─AUDIT_TYPES──────────▶ audit_log table (90d retention)
   │
   ├─state.upsert── ▶ orders_state.json (atomic + fcntl, V3.27.5 Path B preserve terminal)
   │
   └─PANEL_OVERRIDE detect ─▶ learning_log.jsonl (append, brak locka)

CRON jobs (off-peak):
   r04_evaluator (03:00 Warsaw)        — czyta events.db + learning_log → tier_suggestions.json
   daily_accounting (06:00 Mon)        — czyta panel + Sheets writer
   overrides-reset (06:00 Warsaw)      — clear manual_overrides.json [3-7.05 silent fail!]
   event_bus_cleanup (04:00 UTC)       — purge processed events >48h, audit_log >90d
   plan_recheck (5 min loop)           — verify courier_plans consistency, GPS drift
   state-reconcile (30 min)            — phantom/ghost detector → reconcile_log
   shift-notify (1 min)                — TASK B T-60/T-30/T-60 worker
   czasowka-scheduler (1 min)          — proactive T-60/-50/-40 czasówka emit
```

---

## 3. Concurrency model — kto pisze równocześnie do czego

| Plik | Writers | Lock | Atomic | Risk |
|---|---|---|---|---|
| `events.db` | 2+ procesy | WAL + busy_timeout=5000 | BEGIN IMMEDIATE | LOW |
| `orders_state.json` | 3 (panel_watcher inline state_machine + reconcile + sla_tracker) | fcntl LOCK_EX | temp→fsync→rename | LOW |
| `learning_log.jsonl` | **3 procesy bez locka** | ❌ | ❌ append-only | **HIGH ⚠️** |
| `courier_plans.json` | 1+ (plan_manager z różnych procesów) | fcntl + version | temp→fsync→rename | LOW |
| `pending_proposals.json` | telegram_approver | fcntl LOCK_EX | atomic | LOW |
| `manual_overrides.json` | telegram + cron | brak (single writer) | atomic | LOW |
| `restaurant_coords.json` | geocoding | fcntl flock | atomic | LOW (write); **HIGH (stale read in panel_watcher singleton)** |
| `geocode_cache.json` | geocoding | fcntl flock | atomic | LOW (write); **HIGH (NO TTL)** |
| `flags.json` | manual + sprint-time scripts | brak | atomic | LOW |

---

## 4. Major Findings — szczegółowe scenariusze failure

Każdy znacznik: **[severity]** [P0=blocker / P1=likely incident / P2=tech debt / P3=cosmetic]

---

### F1. Learning_log triple-writer race — corrupt JSONL **[P1]**

**Dowód:** 3 procesy piszą bez locka:
- `panel_watcher.py:139` `with open(_LEARNING_LOG_PATH, "a") as f: f.write(...)`
- `telegram_approver.py:132+` (TAK/NIE/INNY/KOORD/TIMEOUT/F7AGREE)
- `shadow_dispatcher.py:610` (NEW_ORDER decision record)

**Avg bytes/linia: 6962** (zmierzone na produkcji, 110MB / 15843 linii). Linux PIPE_BUF = 4096 B → POSIX gwarantuje atomicity tylko ≤PIPE_BUF. **Linie >4KB mogą się przeplatać.**

**Scenariusz failure:** Telegram callback (ASSIGN) + panel_watcher PANEL_OVERRIDE detect + shadow NEW_ORDER zostają zapisane w tej samej milisekundzie podczas peak. Bytes write-call A 0..4095 trafia do bufora, write-call B 0..4095 dokleja, write-call A 4096..6962 dokleja. Wynik: pojedyncza JSONL linia z fragmentami 3 różnych eventów. `json.loads()` w `learning_analyzer`/`r04_evaluator`/`validation_gate_lgbm` — exception → linia skipped lub cały plik psuty.

**Impact production:**
- R-04 evaluator (cron 03:00) liczy peak metrics z corrupt linii — fałszywe tier promotions/demotions. Wykryte częściowo po Adrian re-run (`tier_suggestions.json` pre-fix vs post-fix mismatch).
- LGBM validation gate dostaje skewed agreement_rate.
- Daily briefing pokazuje fałszywą action distribution.
- **Silent corruption, brak detekcji** — JSONL parser gubi linie bez alarmu (brak invariant "linia per second X powinna istnieć").

**Fix:** dodać `fcntl.flock(LOCK_EX)` wokół `f.write(...)` w 3 writerach LUB skonsolidować do jednego writera (writer thread per-process drains queue, single-process pisarz logu). Krótkoterm: wyodrębnij `core/jsonl_appender.py` z fcntl wrapper, refactor 3 callsites — ~1h. Długoterm: dispatch wszystko do `events.db` audit_log (już istnieje per `AUDIT_EVENT_TYPES`), JSONL deprecate.

---

### F2. Cron failure silent — overrides-reset 03-07.05 4 dni martwy **[P0]**

**Dowód:** systemd unit `dispatch-overrides-reset.service` ma `Type=oneshot` + brak `OnFailure=` directive. Service file (zweryfikowany) NIE ma żadnego watchdog/alert path. `grep "OnFailure" /etc/systemd/system/dispatch-*.service` → **0 wyników** (żaden serwis).

**Real incident 03.05-07.05:** 4 dni silent skip — 13 nazw kurierów (Bartek O., Gabriel, Mateusz O gold + Adrian Cit std+) excluded z dispatch przez 4 dni. Adrian zauważył dopiero po analizie tech debt #5 wieczorem 07.05.

**Impact production:** każdy cron może umrzeć cicho. Na ten moment 9 timerów → 9 potencjalnych silent failures. R-04 evaluator (czyta z corrupt learning_log!) — gdy fail tier_suggestions stary 6+ dni. Daily accounting fail = brak rozliczenia tygodnia, audit niezgodny.

**Fix krótkoterm (~30 min):** dodać `OnFailure=dispatch-cron-alert@%n.service` template service który robi `telegram_utils.send_admin_alert` z `journalctl -u %i -n 50`. Plus weekly meta-watcher: `dispatch-cron-watchdog.timer` co 6h — sprawdź `LastTriggerUSec` każdego znanego cron-u, jeśli > 1.5× expected interval → alert.

**Fix długoterm:** każdy cron pisze `last_success_at` + `last_attempt_at` do `cron_health.json`; parser_health endpoint (już istnieje) ekspozuje status; `dispatch-monitor-419.service` (lub nowy `dispatch-cron-watchdog`) alertuje gdy stale.

**Z3 implication:** bez tego nie skalujesz multi-tenant. Restimo / Wolt Drive doda 6+ kolejnych cronów — bez watchdog 1-2 razy/miesiąc będzie incident.

---

### F3. `panel_watcher._COORDS` static singleton — stale po nowej restauracji **[P2]**

**Dowód:** `panel_watcher.py:67-78` ładuje `restaurant_coords.json` **raz na startup** w `_load_coords()`:
```python
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
_load_coords()  # <-- raz na import
```

Komentarz w kodzie: *"Lookup address_id -> coords, zaladowany raz przy starcie (hot-reload w razie potrzeby przez restart)"* — **explicit acknowledged, nie ma hot-reload**.

**Scenariusz:** Adrian onboarduje nową restaurację (Bartek → Manager Gastro task Q3 2026). Wpisuje address_id w panelu, geocoding (z `dispatch_pipeline:318` per-call read) zapisze do `restaurant_coords.json`. Ale `panel_watcher._COORDS` w pamięci NIE ma tego address_id → emit NEW_ORDER z `pickup_coords=None` → defense gate L1 firmowe-konto fallback fires (bo panel widzi None) → fałszywy fallback do Nadajesz.pl coords → propose błędny lub SUPPRESSED.

**Impact production:** każda nowa restauracja widzi **silent BRAK KANDYDATÓW lub fałszywe propozycje** dopóki ktoś nie zrestartuje `dispatch-panel-watcher`. W peak window restart blocked → użytkownik czeka godzin.

**Fix:** mtime-based reload w panel_watcher cycle — `if mtime(file) > _COORDS_LOADED_AT: _load_coords()`. ~10 LOC, 0 ryzyka. LUB usuń `_COORDS` global, używaj `dispatch_pipeline:318` pattern (per-call read).

---

### F4. Per-process cache divergence — courier_tiers, schedule, OSRM **[P2]**

**Dowód:** Każdy z 4 long-running procesów (shadow, panel-watcher, telegram, sla-tracker) ma własny module-level `_COURIER_TIERS_CACHE`, `_route_cache`, `_session` singleton. Gdy R-04 cron rebuilduje `courier_tiers.json` o 03:00:
- Proces A wywoła `_load_courier_tiers()` w 03:00:30, mtime check trigger reload — widzi nowe tiery
- Proces B wywoła w 03:01:00, mtime check trigger reload — widzi nowe tiery
- Proces A do 03:01:00 propose'uje używając SWOICH cached tierów (stare lub nowe)

**Scenariusz worst-case:** R-04 promote'uje Adrian Cit std → std+. Shadow widzi std+, telegram callback handler widzi std (cached). Decision context inconsistent — Adrian otrzymuje proposal o tier="std+", ale callback log zapisze action z tier="std" (z perspektywy telegram). Audit trail rozbieżny.

**Impact production:** krótkie (sekundy do 1 min) okna gdzie różne procesy mają różne views tej samej rzeczywistości. Większość zmian R-04 mało częsta, więc real impact LOW. Ale dla `manual_overrides` (Telegram /stop /wraca może być wielokrotnie/dzień): proces który właśnie startował proposal może wybrać kuriera którego inny proces właśnie wykluczył.

**Fix krótkoterm:** publish/subscribe via `events.db` typed event `CONFIG_RELOAD` (cid=null, payload=which_file) — każdy długi proces subskrybuje, na event invaliduje swój cache. ~3h.

**Fix długoterm (Z3 multi-tenant):** redis/lokalny ETCD dla shared config, każdy proces `WATCH` keys. Pre-condition: gdy migrujesz na K8s.

---

### F5. V3.27.7 panel_bg_refresh per-process — incident pattern może wrócić **[P1]**

**Dowód:** `panel_client.py:67` ma module-level `_session = {opener, csrf, ...}`. `start_bg_refresh()` thread spawned **per-process**. 30.04 incidenty (2× propose-flow stoppage 38min + 64min) miały dokładnie ten root cause: shadow + panel-watcher oba spawnowały bg thread → współbieżne re-login → panel server invalidate session → 419 storm.

**Aktualny stan:** disabled via `/etc/systemd/system/dispatch-{shadow,panel-watcher}.service.d/override.conf` `ENABLE_PANEL_BG_REFRESH=0`. Ale to override per-service. **Każdy nowy konsument `panel_client` musi pamiętać o override**, inaczej regression. Lekcja #47 service-scoped audit — nie ma compile-time guard.

**Scenariusz:** ktoś dodaje nowy serwis (np. `dispatch-cod-weekly` cron lub `dispatch-shift-notify`) który importuje `panel_client.start_bg_refresh()` (lub używa funkcji która sama spawn'uje). Bez override.conf → bg thread → kolizja session.

**Impact production:** identyczny jak 30.04 — 2× propose stoppage w peak.

**Fix:** module-level guard w `panel_client.start_bg_refresh()` — sprawdza env var `PANEL_BG_REFRESH_ENABLED` ALE z `default=False`. Wtedy "fail-closed" — nowy serwis musi explicit enable. Plus single-instance lockfile (`/run/panel-bg-refresh.lock`) — tylko jeden proces system-wide może mieć aktywny bg thread.

---

### F6. Geocode cache permanent — restaurant relo silent stale **[P2]**

**Dowód:** `geocoding.py:33` `geocode_cache.json` 853KB. Brak TTL, brak invalidation per-key (poza V3.12 city-aware fix przez manual `tools/invalidate_city_bugged_geocodes.py`).

**Real incident V3.12 (#466975 Chicago Pizza):** Kleosin order zbundlowany z Białystok jako 0.26km zamiast 5.33km — cache miał stary klucz bez miasta. Naprawiony per-row, ale CACHE NADAL ROŚNIE w nieskończoność.

**Scenariusz nowy:** restauracja przenosi się 500m. Geocode cache zwraca stare coords. Każdy bundling decision dla nowej lokacji = błędny dystans. Adrian nie ma sposobu detekcji oprócz "kurierzy narzekają".

**Impact:** kompounduje przez tygodnie. 1 restauracja relo / kwartał × wieloletni rozwój = stała degradacja jakości decisions.

**Fix:** dodać per-entry `cached_at` field. Reader sprawdza `now - cached_at < 30d` (30 dni TTL), inaczej re-geocode. Plus alarm gdy nowy lookup vs stary daje |Δ| > 200m (Adrian/Bartek manual decision: keep new lub manual override).

---

### F7. OSRM cache 60-min — peak korki stale **[P2]**

**Dowód:** `osrm_client.py:36` `CACHE_TTL_SECONDS = 3600` (60 min, było 15 min pre-V3.26). Traffic multiplier post-cache (V3.26 BUG-3) — multiplier napisany na cached `osrm_raw_duration_s`.

**Scenariusz:** Korek na ulicy Sienkiewicza powstaje o 14:30 Warsaw (wypadek). Cache miał route z 13:35 → 60-min TTL = nadal aktywny do 14:35. Bundling decisions używają outdated travel time, kurier dostaje propozycję która faktycznie wymaga +8 min real → przekroczenie R6 hard 35min → opóźniony delivery.

**Impact production:** cosmetic na sucho, real w peak gdy zmiana traffic patterns. Adrian nigdy nie wykryje bo "to korki, się dzieje".

**Fix:** redukcja TTL do 15 min dla peak buckets (11-14, 17-20, sat 16-21), 60 min dla off-peak. LUB external traffic API integration (kosztowne, deferred).

---

### F8. V3.27.5 race condition test coverage gap **[P1]**

**Dowód:** Path B w `state_machine.py:312-326` zweryfikowany — preserves picked_up/delivered status na COURIER_ASSIGNED revert. ALE explore agent znalazł że nie ma jednolitego test-replikującego race (`tests/test_assignment_race_v327_5.py` nie istnieje per agent search; tests/ ma test_route_simulator_c1, test_v319* etc., ale nie dedicated race test).

Bug rate pre-fix był 13.4% (185/1384 picked-up orders 7d). Post-fix: 0% obserwacyjnie ale brak strukturalnego testu.

**Scenariusz:** ktoś refactoruje `update_from_event()` (np. rename z V3.27.5 oznaczeń, dodanie nowego event_type) i niechcący usuwa `prev_status in ("picked_up", "delivered")` guard — testy nie wykryją regresji.

**Impact:** silent powrót 13.4% bug rate. Może być niewidoczny tygodniami.

**Fix:** dodać `tests/test_state_machine_race_v3275.py` z 4 scenariuszami:
1. PICKED_UP → COURIER_ASSIGNED (same cycle) → status preserved
2. DELIVERED → COURIER_ASSIGNED → status preserved
3. assigned → COURIER_ASSIGNED → status updated normalnie
4. multiple ASSIGNED w 5s — last-write-wins nie nadpisuje terminal

~30 min, blokuje regresję.

---

### F9. Shadow dispatcher restart — pending propozycje sieroty **[P1]**

**Dowód:** `shadow_dispatcher` event loop (linia 32-46) reads `event_bus.get_pending(NEW_ORDER)`. Restart między emit COURIER_ASSIGNED (state.upsert) a Telegram send: `pending_proposals.json` ma zapisaną propozycję, ale `dispatch-telegram` może już wysłać do bota lub jeszcze nie.

**Scenariusz:** Adrian restartuje `dispatch-shadow` o 17:55 UTC (peak Warsaw 19:55). `event_bus` ma 3 NEW_ORDER pending. shadow stop → 3 events nadal pending. shadow start → 5s pre-warm + ortools warmup → konsumuje 3 events, generuje propozycje. Telegram approver tail-uje shadow_decisions.jsonl — widzi nowe entries → wysyła. **Ale stary tail offset gubi się** (shadow_decisions.jsonl czytany od końca pliku vs kursor)?

Check: explore raporty mówią że pending_proposals.json atomic (survives restart), ale **callback queue od bota** NIE persistent — jeśli user kliknął ASSIGN przed restartem dispatch-telegram, callback może być zgubiony (Telegram bot retry'uje przez ~60s ale po tym daje up).

**Impact:** rzadkie. W peak deploy okna (Adrian żelaznie zachowuje), prawdopodobieństwo niskie. Ale Z3 multi-tenant — gdy 5 instancji equivalentów, 1 restart/dzień = 1-2 sieroty/tydz.

**Fix:** `pending_proposals.json` ma `expires_at` field — ekspirowane (5 min) auto-KOORD. Już istnieje. Dorzucić: na startup `telegram_approver` skanuje pending_proposals z `expires_at < now+30s` i forsuje auto-KOORD (zamiast czekać do timeout watchdog asyncio task). ~1h.

---

### F10. V3.20 packs_ghost_detect — duplicate COURIER_DELIVERED **[P2]**

**Dowód:** `panel_watcher.py:~1167` packs reverse lookup. Emit `COURIER_DELIVERED` z `event_id=f"{oid}_COURIER_DELIVERED_packs_ghost"`. Real DELIVERED z reconcile cycle ma event_id `{oid}_COURIER_DELIVERED_{ts_ms}`. **Różne event_id → 2 eventy w events.db audit_log dla tego samego order_id**.

state_machine handler dla DELIVERED jest idempotent (last-write-wins na order status) ale audit_log ma 2 entries — analytics dostają duplicate count.

**Scenariusz:** order #470123 odebrany → ghost detect 14:00 emit → 14:02 reconcile zauważa real → 2 audit entries. r04_evaluator computuje "delivered count per courier" z events.db audit_log → courier dostaje 2× credit za 1 dostawę → fałszywy tier promotion (deliv ≥ 50 wymóg, double-counted może przeskoczyć próg).

**Impact production:** R-04 tier metrics inflated dla active couriers. Stosunkowo rzadkie (V3.15 + V3.20 wcześniej rozwiązały większość lag), ale w incident-day może być +5-10 ghost duplicates.

**Fix:** event_id deterministic: `{oid}_COURIER_DELIVERED_canonical` (no timestamp, no source variant). Wszystkie warianty kolidują na PK → INSERT OR IGNORE chroni. Trade-off: tracisz audit "co wykryło delivered jako pierwsze" — dodaj separate `deliv_source` field w audit_log payload zamiast event_id variant.

---

### F11. Manual_overrides per-call read — OK, ale `EXCLUDE_KEYWORDS` hardcoded **[P3]**

**Dowód:** `courier_resolver.py:628-629`:
```python
from dispatch_v2 import manual_overrides
excluded = set(manual_overrides.get_excluded())
```

`get_excluded()` → `load()` → `open(OVERRIDES_PATH)` per-call = fresh read. ✅

**Real risk:** `EXCLUDE_KEYWORDS = ("nie pracuje", "wyklucz", "choruje", "nie ma")` w `manual_overrides.py:18` — Adrian dopisze nowy keyword "nieobecny" → potrzeba code change + deploy. Z3 multi-tenant (Restimo etc.) — różne języki, różne idiomy.

**Fix:** keywords do `flags.json`, hot-reload via `common.flag()`. ~30 min.

---

### F12. Schedule Sheets fail-open — stale 10 min może urosnąć do godzin **[P2]**

**Dowód:** `schedule_utils.py:95-154` (T3 hot-refresh): `load_schedule()` mtime check → trigger fetch jeśli age > TTL (10 min) → reload from disk. Fetch via subprocess `fetch_schedule.py`. **Fail-open:** jeśli fetch fail → stale cache continues.

**Scenariusz:** Google Sheets API down 2h. T3 cache wieczorny (sobota 19:00 Warsaw, peak 16-21). Adrian dodaje pre-shift 19:30 dla nowego kuriera w arkuszu. Fetch fails → cache stale 10 min → 30 min → 2h. Nowy kurier NIE widziany przez czas TTL+API_DOWN_TIME = potencjalnie godziny. Worker T-60 nie wyśle alertu start, kurier wlazł do peakowego flow bez schedule, courier_resolver da "brak w grafiku" reject.

**Impact production:** rzadkie ale real (Google API outages 2-3× rok). W weekend peak = krytyczne.

**Fix:** alert gdy `schedule_age > 30 min` (nadal fail-open dla dispatch, ale loud o problemie). Plus secondary: daily snapshot do `schedule_today_backup.json` — fallback gdy Google completely down.

---

### F13. GPS TTL 20 min — courier app crash niewidoczny **[P2]**

**Dowód:** `courier_resolver.py:399` GPS_FRESHNESS_MIN ~20 min. Older → fallback bag-based pos lub synthetic.

**Scenariusz:** Kurier ma bag (assigned 467999), wyłącza phone (bateria, restart), odpinają GPS. Pozycja stale 20 min. Po 20 min courier_resolver przełącza na "bag-based pos" = `last_assigned_pickup` lub `last_picked_up_delivery`. Ale w międzyczasie kurier odebrał (manual `[5]` w panelu kuriera offline, czy panel update'uje?) → state_machine widzi picked_up. Position synthesis używa `delivery_coords` (V3.19a) ale to "ostatni znany cel", nie real position. Adrian woła kuriera, dispatch widzi go w synthetic pos przy delivery_coords zamiast real (kurier może być przed restauracją drugiego pickup, ostatnio assigned 30 min temu).

**Impact:** propose fed bag-based pos = błędny ETA. Mid-shift offline GPS = 1-2 razy/dzień real.

**Fix:** dodać alert `GPS_STALE` event do event_bus gdy GPS_AGE > 25 min DLA active courier (bag>=1) — Adrian dostaje Telegram "Bartek O. GPS stale 27 min, sprawdź" → manual call kuriera. Już mamy `GPS_STALE` w EVENT_TYPES (event_bus.py:30) — kto emit'uje? Sprawdź czy w ogóle jest emit path. Jeśli nie — backbone defined ale path missing.

---

### F14. Reconciliation auto-resync HARD_CAP=5 — silent backlog **[P2]**

**Dowód:** explore agent: `reconciliation/reconcile_worker.py` HARD_CAP=5 resyncs/run, run co 30 min.

**Scenariusz:** Incident-day (V3.13 phantom PIN, V3.14 stale bag, etc.) generuje 50+ phantoms. 5/run × 48 runs/dzień = 240/dzień max. Phantom backlog może rosnąć szybciej niż reconcile drain. Phantoms persist → fałszywe BEST candidates z wycofanych orderów.

**Impact production:** nie real teraz (incident-rate niski), ale Z3 multi-tenant 10× — backlog expand.

**Fix:** dynamic HARD_CAP scaling — start z 5, przy detekcji backlog > 20 zwiększ do 20 dla tego runu. Plus alert gdy backlog > 50 after run.

---

### F15. `silent except` w courier_resolver — Lekcja #32 violations **[P2]**

**Dowód:** `grep "except Exception" courier_resolver.py` → 19 wystąpień, w tym kilka jako bezpieczny `defensywnie zachowaj` fail-open (linie 196, 277, 297, 337). Ale linia 459, 521, 538, 559, 639, 727 — bez kontekstu "defensywnie" w komentarzu. Wymaga ręcznego audytu każdego callsite.

Per Lekcja #32: "explicit log każdy except, NIGDY silent". Większość wymaga `_log.debug(...)` lub `_log.warning(...)` przy except.

**Scenariusz:** edge case w build_fleet_snapshot trapuje exception → kurier silently dropped z fleet → invisible wpływ na propose pool. Nikt nie widzi w logu, debugowanie incydentu pol-doby.

**Fix:** ręczny audyt 19 callsite × ~5 min = 1.5h. Każdy bez komentarza "defensywnie X" → dodać `_log.debug(f"{context}: {e}")` lub upgrade do `_log.warning` jeśli rare path.

---

### F16. Singleton drift między procesami — `_RESTAURANT_DISTRICT_CACHE` itp. **[P3]**

**Dowód:** dispatch_pipeline.py:47 `_V326_RESTAURANT_DISTRICT_CACHE = {}` — module-level dict per-process. Bez TTL, bez invalidation. Dorzucają się tylko entries.

**Memory leak risk:** jedna restauracja → jedna entry. 50 rest × 4 procesy = 200 entries, mała. Ale przez wieloletni rozwój + multi-tenant — 500 rest × 5 procesów = 2500 entries. Per restaurant ~200B = 500KB. Negligible. **NIE memory leak, ale stale data risk** — zmiana district mappingu przez `BIALYSTOK_DISTRICT_ADJACENCY` w common.py wymaga restart wszystkich procesów.

**Fix:** dorzucić invalidate w `flags.json` change handler (nie ma takiego — `flag()` reader hot-reloads, ale derived caches nie). Albo po prostu: invalidate cache przy każdym `_load_courier_tiers` mtime change (proxy "config zmieniony").

---

### F17. `IGNORED_FULL_NAMES` skiplist — JSON file, brak schema validation **[P3]**

**Dowód:** `shift_ignored_names.json` (07.05 NEW). `worker._load_ignored_names()` fail-open na FileNotFoundError, log warn na corrupt JSON.

**Scenariusz:** Adrian edytuje plik ręcznie (typo), sub-tag mismatched → `json.load` exception → empty set → nikt nie pomijany → false alert "Nowy kurier?" dla Daniel Malicki/Szymon Bawerna/Albert Dec.

**Impact:** cosmetic alert; Adrian może to oznaczyć ale denerwuje.

**Fix:** schema validation przy load (jsonschema library). Plus `dispatch-overrides-reset` style timer reset do default. Niski priorytet.

---

### F18. Replay tool używa post-fix code — może maskować bug w starym **[P2]**

**Dowód:** `replay_failed.py:131-145` (P0 fix 07.05) — replay przez `assess_order()` w current code. Output: PASS/FAIL/SKIP.

**Scenariusz:** Bug który był live między 14.04 a 22.04 (V3.19h-BUG2 magnitude) — replay z 18.04 incident przez post-22.04 code path → pokaże "PASS" bo bug już naprawiony. Real verdict 18.04 to FAIL. Post-mortem analysis błędna.

**Fix:** dodać `--replay-at-commit <sha>` flag — git checkout do tego commitu, replay tam. Albo: store decision_record snapshot (full inputs + outputs) w shadow_decisions, replay tools używa snapshot bez re-running pipeline. Już mamy shadow_decisions.jsonl jako audit trail — używać jako primary evidence.

---

### F19. Event_bus brak ordering guarantee **[P3]**

**Dowód:** `event_bus.get_pending(...)` używa `ORDER BY created_at ASC`. `created_at` to `now_iso()` z resolution sekundy (lub MILIsekundy?). Sprawdzić — `common.now_iso` zwykle ms.

**Scenariusz:** dwa eventy z tego samego cyklu panel_watcher (PICKED_UP + COURIER_ASSIGNED dla różnych orderów) — mogą mieć identyczny `created_at` → ordering nieprzewidywalny → consumer może przetworzyć w odwrotnej kolejności.

**Impact:** w obrębie różnych orderów — irrelevant. W obrębie tego samego order_id — V3.27.5 Path B chroni terminal status, więc OK.

**Fix:** dodać `seq_id` autoincrement w events table, secondary ORDER BY. Już bezpieczny per Path B, ale defensywny.

---

### F20. Brak compile-time gwarancji ownership **[P2]**

**Dowód:** każdy moduł może otworzyć `orders_state.json`, `manual_overrides.json` etc. Tylko convention (i fcntl) chroni multi-writer. Brak Python type system → brak ostrzeżenia że "ten plik powinien być pisany TYLKO przez state_machine".

**Scenariusz:** nowy moduł (np. `bolt_food_integration.py` Tydzień 4) implementer robi `open(orders_state_path, "w")` — bez fcntl, atomic writes. Race z state_machine → corrupt JSON → cały dispatch pad.

**Fix Z3:** wprowadzić `core/state_io.py` z **TYLKO** funkcjami `read_orders_state()`, `update_order(oid, fields, event_name)` etc. Wszystkie inne moduły MUSZĄ używać tych funkcji. Code review checklist: "czy nowy plik bezpośrednio otwiera state JSON?". Plus integration test który grep'uje codebase za `open.*orders_state\|open.*courier_plans`.

---

## 5. Najpilniejsze do zrobienia (priorytetyzacja)

| # | Severity | Effort | Wartość | Zalecenie |
|---|---|---|---|---|
| F2 | P0 | 30 min | krytyczne (cron silent fail) | **PIERWSZE** — `OnFailure=` template service + Telegram alert |
| F1 | P1 | 1h | high (audit trail corruption) | DRUGIE — fcntl wokół 3 learning_log writers |
| F8 | P1 | 30 min | medium-high (regression guard) | TRZECIE — V3.27.5 race test |
| F5 | P1 | 1h | high (production-proven incident pattern) | CZWARTE — fail-closed bg_refresh + lockfile |
| F3 | P2 | 30 min | medium (Z3 onboarding) | mtime-reload `_COORDS` |
| F9 | P1 | 1h | medium (peak deploy edge) | startup pending_proposals scan |
| F12 | P2 | 1h | medium (Sheets outage) | schedule_age alert + backup snapshot |
| F13 | P2 | 1h | medium (mid-shift GPS loss) | GPS_STALE event emit path |
| F4 | P2 | 3h | high Z3 | events.db CONFIG_RELOAD pub/sub |
| F20 | P2 | 4h | high Z3 | core/state_io.py centralizacja |
| F6, F7, F10, F11, F14-F19 | P2-P3 | razem ~6h | różne | iterational |

**Sumarycznie krytyczne (F1+F2+F8+F5):** ~3h pracy. Po tym system **dramatycznie odporniejszy** na partial failures.

---

## 6. Strategiczne rekomendacje (Z3 = buduj na lata)

1. **Deprecate `learning_log.jsonl`** w favor `events.db` audit_log. Już ma rolę (AUDIT_EVENT_TYPES). Jednolite query API, WAL safety, retention policy strukturalny. JSONL → 2-tygodniowe staging period, potem read-only freeze, później delete.

2. **Cron health framework** — każdy cron pisze `cron_health.json[name] = {last_attempt_at, last_success_at, last_error}`. `parser_health_endpoint` dorzuca `/health/cron`. Dashboard pokazuje wszystko-zielone/żółte/czerwone. Eliminuje całą klasę silent-fail bug-ów (F2 to przykład).

3. **State I/O centralization** (F20) — wszystkie reads/writes do JSON state przez `core/state_io.py`. Code review enforces. Bonus: można zamienić backend (JSON → SQLite → Postgres) bez touch w dispatch_pipeline.

4. **Per-process cache invalidation via events.db pub/sub** (F4) — `CONFIG_RELOAD` event, każdy long-running process subskrybuje. Eliminuje per-process drift.

5. **Replay infrastructure z snapshot** (F18) — shadow_decisions.jsonl jako primary, replay nigdy nie re-runs pipeline. Pozwala udzielić authoritative answer dla "co decision Ziomek miał o 14:23 dla order X" niezależnie od code drift.

6. **Multi-tenant readiness checklist** (Restimo / Wolt Drive Q3 2026):
   - Per-tenant `flags.json`, `kurier_*.json`, `restaurant_coords.json`, `courier_tiers.json`
   - Tenant-aware cron jobs (per-tenant timer instances)
   - Shared `events.db` z `tenant_id` column
   - Per-tenant Telegram bot creds + group chat

---

## 7. Co NIE jest problemem (false-positive sanity check)

- **event_bus idempotency** — solidnie zrobione, deterministyczne event_id + 2-warstwowa dedup. ✅
- **state_machine V3.27.5 Path B** — code preserves picked_up/delivered terminal status. ✅
- **plan_manager fcntl + CAS** — robust. ✅
- **manual_overrides hot-read** — fresh read per `dispatchable_fleet()` call. ✅
- **courier_admin.add_new_courier 4-file rollback** — transactional excellence. ✅
- **parser_health 4-layer defense** — V3.28 sprint, motion-aware, set-detection. ✅
- **firmowe konto 6-warstwowa defense** — 07.05 sprint, defense-in-depth. ✅

---

## Zamknięcie

System jest **dojrzały operacyjnie** — większość problemów już zaadresowana przez sprinty V3.x. **Główne ryzyko rezydualne:** (a) audit trail corruption (F1), (b) silent cron failures (F2), (c) per-process cache drift (F4), (d) hidden async dependencies w `panel_client` singleton (F5). To są **architektoniczne**, nie incident-driven — wymagają refactor sprintów ~10-12h razem dla pełnej remediacji.

**Najbliższy krok:** F2 (~30 min) eliminuje całą klasę silent-fail bug-ów. Najlepsze wartość/wysiłek w całym audycie.

---

## Cross-ref

- **Companion document:** `dispatch_v2/AUDIT_2026-05-07/ARCHITECTURE_AUDIT_2026-05-07.md` (god objects, fan-in, except count, 10 modułów Tier A/B/C)
- **Memory:** `lessons.md` #32 (silent except), #47 (service-scoped audit), #48 (recurring bug = incomplete fix), #71 (decoupled lifecycles), #75-#76 (Telegram leak + anomaly semantics), #80-#83 (firmowe konto sprint)
- **Tech debt:** `MEMORY/tech_debt_backlog.md` — 18/22 DONE, 5 pozostałe P2/P3
- **Re-audit cadence:** pre-Faza 7 100% flip / pre-multi-tenant Warsaw
