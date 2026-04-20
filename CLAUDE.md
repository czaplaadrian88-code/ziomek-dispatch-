# CLAUDE.md — Dispatch V2 instruction for Claude Code sessions
# Update: 2026-04-20 (post-V3.19+V3.20)

## Quick context

Ziomek autonomous dispatcher, NadajeSz Białystok.
Server: Hetzner CPX22, UTC, 4GB RAM.
Repo: github.com/czaplaadrian88-code/ziomek-dispatch-

**Working directory (zawsze cd):**
`/root/.openclaw/workspace/scripts/dispatch_v2/`

## Live stack (2026-04-20)

**Deployed tags:** v319a/b/c (A+B+C+D)/d + v320 + master f22-v319-v320-complete @ 466a716

**Feature flags (common.py):**
```python
# V3.19a floor
ENABLE_PICKED_UP_DROP_FLOOR = True

# V3.19b plan_manager
ENABLE_SAVED_PLANS = True

# V3.19c shadow log
ENABLE_SAVED_PLANS_READ_SHADOW = True

# V3.19c timer
AUTO_INVALIDATE_STALE = False     # observational
ENABLE_GPS_DRIFT_INVALIDATION = False  # observational

# V3.19d read integration (sticky sequence)
ENABLE_SAVED_PLANS_READ = True

# V3.20 ghost detection
ENABLE_V320_PACKS_GHOST_DETECT = True
```

**Services:**
```bash
# Active:
dispatch-shadow
dispatch-panel-watcher
dispatch-telegram         # DO NOT RESTART without explicit user ACK
dispatch-sla-tracker
dispatch-gps
dispatch-plan-recheck.timer  # NEW (V3.19c, every 5min)

# Dispatch-state files:
/root/.openclaw/workspace/dispatch_state/
├── courier_plans.json          # V3.19b saved plans (atomic writes)
├── v319c_read_shadow_log.jsonl # V3.19c shadow diff log
└── plan_recheck_log.jsonl      # V3.19c timer output
```

## Core files (V3.19/V3.20 key locations)

- `common.py` — feature flags + constants (Bartek gold standard)
- `route_simulator_v2.py` — V3.19a floor + V3.19d base_plan extension
- `plan_manager.py` — NEW, saved plans (load/save/invalidate/advance/insert_stop_optimal)
- `plan_recheck.py` — NEW, V3.19c consistency + GPS drift timer
- `dispatch_pipeline.py` — V3.19d hook load_plan in assess_order
- `panel_watcher.py` — V3.15 packs fallback + V3.20 packs reverse ghost detect
- `bag_state.py` — core bag filter
- `state_machine.py` — upsert orders_state
- `telegram_approver.py` — DO NOT MODIFY without explicit ACK

## Rollback cheat sheet

**Full stack (nuclear):**
```bash
cd /root/.openclaw/workspace/scripts/dispatch_v2
git reset --hard f22-bag-reality-check-live-V3.18
systemctl restart dispatch-shadow dispatch-panel-watcher
systemctl disable --now dispatch-plan-recheck.timer
```

**Per-flag (surgical, 30 sekund):**
Edit common.py, set flag=False, restart odpowiedni service:
- V3.20 ghost → `ENABLE_V320_PACKS_GHOST_DETECT=False` + restart panel-watcher
- V3.19d read → `ENABLE_SAVED_PLANS_READ=False` + restart shadow
- V3.19c timer → `systemctl disable --now dispatch-plan-recheck.timer`
- V3.19c sub A+B → `ENABLE_SAVED_PLANS=False` + restart panel-watcher+shadow
- V3.19a floor → `ENABLE_PICKED_UP_DROP_FLOOR=False` + restart shadow
  (NIE rekomendowane — baseline safety)

## Hard constraints for any session

- Warsaw TZ: `ZoneInfo("Europe/Warsaw")` as WARSAW
- Atomic writes: temp → fsync → rename
- Feature flag + env kill-switch dla każdej decyzyjnej zmiany
- Per change: cp .bak → str_replace → py_compile → import check → test → commit → tag
- 433 baseline tests PASS przed każdym commit
- Zero `jq`. `sed` only for reading, not editing.
- NEVER restart dispatch-telegram without explicit user ACK
- NEVER modify wave_scoring.py without explicit ACK (Sprint C boundary)
- Gates: user ACK between major etapy (design → impl → deploy)

## Known issues / pre-existing failures

Pre-existing test failures (NOT regression, documented since V3.18):
- `test_cod_weekly` — 2 fails (gspread import error)
- `test_feasibility_integration` — 1 fail
- `test_reconcile_dry_run` — 1 fail
- `test_scoring_scenarios` — NameError (legacy test)

Total PASS: 433 (excluding 4 pre-existing).

## Open roadmap (post-V3.20)

- **V3.21** (~2h session): flip `ENABLE_WAVE_SCORING=True` (Sprint C finish)
  — unblocked when match rate shadow log >80% stable 24h
- **V3.22** (~1h session): flip `ENABLE_BUNDLE_VALUE_SCORING=True`
  — after V3.21 stable 24h
- **V3.19d3** (~2h session): periodic re-check auto-invalidate
  — after shadow log confirms invalidation heuristics
- **V3.20d** (~1h session if needed): `czas_doreczenia` propagation
  through state_machine (backup R2 mechanism via 5-file fix)
  — only if V3.20 packs reverse proves insufficient in real peak
- **Post-cleanup Monday**: .bak_v319* + .bak_v320 files (list in
  /tmp/v319_bak_cleanup_list.txt)
- **Gateway memory leak**: openclaw-gateway rises ~200MB/h;
  `docker compose restart openclaw-gateway` before peak if > 1.5GB

## Test command quick reference

```bash
cd /root/.openclaw/workspace/scripts/dispatch_v2

# Full regression (~2min)
pytest tests/ -v --tb=short --timeout=60 2>&1 | tail -30

# Fast (skip pre-existing failures)
pytest tests/ --ignore=tests/test_cod_weekly.py \
    --ignore=tests/test_feasibility_integration.py \
    --ignore=tests/test_reconcile_dry_run.py \
    --ignore=tests/test_scoring_scenarios.py \
    -v --timeout=60

# V3.19+V3.20 only (fast sanity)
pytest tests/test_v319* tests/test_v320* -v --timeout=60
```

## Session workflow recommendations

1. **Start:** read context files (/tmp/v319_v320_*, TECH_DEBT.md, this CLAUDE.md)
2. **Verify state:** `git log --oneline -5` + `systemctl is-active ...`
3. **Read-only audit before any edit** — understand current code
4. **Design in markdown** before coding — /tmp/v3XX_design.md
5. **User ACK** between design/impl/deploy
6. **Deploy observability** — 10 min minimum + journal grep errors
7. **Cleanup** — TECH_DEBT, MEMORY, /tmp artifacts, .bak preserved 24h
8. **Final report** — max 40 linii w chat, z rollback paths

## Memory protocol

When session involves significant architectural changes:
1. Update MEMORY project file: `project_f22_*.md`
2. Update this CLAUDE.md
3. Update TECH_DEBT.md
4. Git commit docs changes separately from code changes

## Cognitive fatigue protection

Session > 5h warnings:
- RSS claude > 1.2 GB → checkpoint + user alert
- Self-contradiction in your own statements → STOP, verify via grep
- Cannot remember if X was done → grep first, respond never assume
- User kontext switching → re-read /tmp/*_state.json before next task

If user asks "did you do X?" and you're not 100% sure → grep or cat
to verify BEFORE answering. Memory drift over long sessions is real.

---

# Changelog (V3.16 → V3.7, preserved for historical reference)

## V3.16 (2026-04-19 wieczór) — no_gps empty bag proposal selection demotion
- **Bug #467189** Rukola → Magazynowa 5/4 @ 15:10:07 UTC: BEST=Mateusz O (cid=413, no_gps, bag=0, score=+53.31), koordynator override → Bartek O. (cid=123). PANEL_OVERRIDE rate **19.6%** (18/92 propozycji last 1h45min). Proposed=413 Mateusz O 7× (avg score +64.8) — wszystkie no_gps empty.
- **Root cause**: `scoring.py` asymmetria — empty bag dostaje baseline ~82 punktów (s_obciazenie=100 × 0.25 + s_kierunek=100 × 0.25 + s_czas=100 × 0.20 = 70 bez penalty). Bag-kurierzy tracą -100 do -300 przez r8_soft_pen + r9_wait_pen + r9_stopover. **Pipeline nie karze no_gps fallback** (synthetic BIALYSTOK_CENTER + max(15, prep) travel).
- **Fix** (4 commits + 4 tagów, master `f22-proposal-selection-fix-live-V3.16`):
  - `ee61264` common — flag `ENABLE_NO_GPS_EMPTY_DEMOTE=True` + env override
  - `28442b9` dispatch_pipeline — inline demote logic po feasible.sort, przed final pick
  - `b4d2866` refactor — extract do module-level `_demote_blind_empty()` + `_is_blind_empty_cand()` + `_is_informed_cand()` (testowalne)
  - `83ffdcc` tests — `test_proposal_selection_v316.py` 25/25 PASS (12 sections)
- **Mechanizm**: jeśli top-1 feasible ma `pos_source in {no_gps,pre_shift,none}` AND `r6_bag_size==0` AND istnieje informed alt → reorder: informed first (stable), other middle, blind+empty last. Guard "all blind": jeśli wszyscy blind+empty → zostaw (empty shift edge).
- **Zero zmian w scoring.py / feasibility_v2.py / wave_scoring.py** — post-scoring layer, ortogonalny do Sprint C.
- **Interakcja V3.12-V3.15**: zero konfliktu. V3.15 packs_fallback + V3.16 demote się wzajemnie wzmacniają — V3.15 szybciej aktualizuje bag (Mateusz O przestaje być blind+empty), V3.16 demotuje gdy **naprawdę** jest blind+empty.
- **Regresja**: 245/245 baseline clean (137 legacy + 16 city + 26 availability + 25 bag + 16 V3.15 + 25 V3.16).

## V3.15 (2026-04-19 wieczór) — Missing-new-assignment lag fix (panel_packs fallback)
- **Bug 16:30 Warsaw**: propozycja #467164 pokazała Michała Li (cid=508, GPS aktywny) jako "🟢 wolny" mimo 4 orderów w bagu w panelu. Orders_state miał `cid=None` dla nich (467129/131/155).
- **Root cause**: `panel_client.parse_panel_html` zwraca `courier_packs {nick:[oid]}` — ground truth z HTML każdego ticku. Było to **dead data** — nigdzie niekonsumowane. `panel_watcher.reconcile` miał lag 15-90s dla emit `COURIER_ASSIGNED` w burst scenarios.
- **Scale (last 4h pre-fix)**: 15.8% propozycji z missing w any candidate, 5.7% w best. Per-courier: Gabriel 65.8%, Gabriel J 47.9%, Adrian R 42.6%. 219 missing events / 4h. 9/10 top couriers dotknięci.
- **Pre-req fix**: pre-existing `reassign_checked` UnboundLocalError od 2026-04-16 (7897 wystąpień) blokował cały `_diff_and_emit` co tick — naprawione przez przeniesienie init przed pętlę (commit `8343169`). Bez tego V3.15 packs fallback się nie uruchamiał.
- **Fix V3.15** (4 commits + 4 tagów, master `f22-panel-packs-fallback-live-V3.15`):
  - `42675f5` common — flag `ENABLE_PANEL_PACKS_FALLBACK=True` (default) + `PACKS_FALLBACK_MAX_PER_CYCLE=10`
  - `9b8cd72` panel_watcher — consumer section po reassignment, mismatch state.cid vs packs → fetch_details + emit COURIER_ASSIGNED (source=packs_fallback); guards na terminal/IGNORED_STATUSES/koordynator; ambiguous nick skip+warn
  - `6ce5730` tests — `test_assignment_lag_fix.py` 16/16 PASS (13 sections, fixture #467164 Michał Li)
  - `8343169` pre-req reassign_checked UnboundLocal fix
- **Live post-deploy (14:58:50 UTC)**: 13 PACKS_CATCHUP events w 5 min, 7 różnych kurierów. **Zero reassign_checked errors** od fixa.

## V3.14 (2026-04-19 późny wieczór) — Bag integrity / stale cache fix
- **Bug 15:17 Warsaw**: propozycja #467117 Baanko pokazała Michała Rom z 3-order bagiem (Arsenal Panteon, Trzy Po Trzy, Paradiso) — wszystkie delivered w panelu 1-3h wcześniej. Real panel bag = {467099 Mama Thai, 467108 Raj}.
- **Root cause**: `panel_watcher.reconcile` ma lag 15-90 min. Pipeline ufał `orders_state.status=assigned` bez TTL guard.
- **Shadow impact**: 36.3% propozycji last-4h miały phantom w BEST bag_context, 83.7% w jakimkolwiek kandydacie. 613 phantom entries / 4h.
- **Fix** (3 commits + 4 tagów, master `f22-bag-integrity-live`):
  - `e3065fd` common — flag `STRICT_BAG_RECONCILIATION=True` + `BAG_STALE_THRESHOLD_MIN=90`
  - `487ba9c` courier_resolver — `_bag_not_stale()` helper + filter w `build_fleet_snapshot:218`
  - `d3d3409` tests — `test_bag_contents_integrity.py` 25/25 PASS
- **Reguła TTL**: `status=assigned + updated_at >90 min + brak picked_up_at → STALE`. `status=picked_up + picked_up_at >90 min bez delivered` również stale.
- **Live post-deploy**: Michał Rom bag 3→1 (Paradiso 467070 z 12:09 UTC wykluczony). Fleet total 44→27.

## V3.13 (2026-04-19 wieczór) — Availability / PIN-space bug fix
- **Bug produkcyjny 14:00-14:08**: 8 propozycji #467070-#467077 pokazały identyczną trójkę "wolnych" kandydatów mimo że panel pokazywał każdego z 2-3 orderami.
- **Root cause**: `courier_resolver.build_fleet_snapshot:214` zawierał `piny.keys()` w `all_kids` — PIN-y 4-cyfrowe dodawane jako osobni kurierzy obok prawdziwych `courier_id`.
- **Shadow impact**: 46% propozycji w ostatnich 4h miało PHANTOM PIN jako best, 61% w 24h.
- **Fix** (3 commits + 4 tagów, master `f22-strict-bag-awareness-live`):
  - `1678d1f` common — flag `STRICT_COURIER_ID_SPACE=True`
  - `32be76a` courier_resolver — exclude `piny.keys()` z `all_kids` gdy flag True
  - `9b3e27f` tests — `test_panel_aware_availability.py` 26/26 PASS

## V3.12 (2026-04-19 południe) — City-Aware Geocoding Fix
- **Bug produkcyjny** (~10:53 Warsaw): #466975 Chicago Pizza→Kleosin fałszywie zbundlowane z #466978 Retrospekcja→Białystok jako "po drodze 0.3km" — realny dystans 5.33km.
- **Root cause 3-warstwowy**: panel_client nie parsował miasta klienta, `geocoding.geocode` hardcoded default, `_normalize` dokleił `, białystok` do cache key.
- **Fix** (5 commitów + 6 tagów, master `f22-city-aware-geocoding-live`):
  - `9fe0980` panel_client — `delivery_city` + `pickup_city` + `id_location_to` z raw
  - `af01fcc` common — flag `CITY_AWARE_GEOCODING=True`
  - `5d9754c` geocoding — signature `geocode(addr, city=None)`, fail-loud gdy None+flag
  - `c28daa6` callers — propagacja przez panel_watcher → shadow_dispatcher → state_machine
  - `b63c27e` tests — `test_city_aware_geocoding.py` 16/16 PASS

## V3.11.1 (2026-04-19 rano) — Telegram Transparency OPCJA A LIVE
- Commit A (`165fd38`): L2 label fix `🔗 blisko: X` → `🔗 po odbiorze z X → +Ykm` + 3 flagi
- Commit B (`1b87e79`): reason line + route section + downstream serializer checklist compliant
- Commit C DEFERRED: scoring breakdown

## V3.11 (2026-04-18 wieczór) — Sprint C skeleton COMPLETE
- 11 live wins w jednej sesji (P1 + C1 + audit docs + C2 + C3 + C4 + C5 + C6 + C7 + geocoding 8/12 + Telegram transparency MVP)
- 137/137 testów PASS
- Wszystkie feature flags F2.2 default False (current behavior preserved)
- Tag finalny: `f22-sprint-c-skeleton-complete`

## V3.10 (2026-04-18 popołudnie) — Sprint C day 1 closing
- 3 live wins: P1 TIMEOUT_SUPERSEDED, C1 per_order_delivery_times, geocoding 8/12

## V3.9 (2026-04-18 rano) — Post-F2.2-audit
- 7 raportów F2.2 w workspace/docs/
- 46,119 rows merged dataset (SCOPED 95.38% coverage, później 97.94% po geocoding)
- Architecture Spec dla Sprint C ready
- 108 kPLN/rok business case confirmed

## V3.8 (17.04.2026)
- F2.1d COD Weekly LIVE (Auto COD Transport w Wynagrodzenia Gastro)
- Courier App (Nadajesz.pl) LIVE — Kotlin+Compose, FastAPI backend :8767
- Panel admin GPS: https://gps.nadajesz.pl/panel

## V3.7 (16.04.2026)
- F2.1b Decision Engine 3.0 COMPLETE (R1-R9 rules)
- 40 testów bazowych, FAZA A+B live

---

# Decision Engine 3.0 rules (F2.1b baseline)

Reguły bazowe (Bartek Gold Standard):
- **R1** delivery spread ≤ 8km
- **R2-R4** corridor 2.5km, dynamic bag cap, free stop +100
- **R5** pickup spread ≤ 1.8km
- **R6** BAG_TIME hard ≤ 35 min + soft zone 30-35 (`BAG_TIME_HARD_MAX=35`, kalibracja z p95=35.6)
- **R7** long-haul peak isolation (>4.5km, 14-17 Warsaw)
- **R8** pickup_span czasowy — DEFERRED F2.1c
- **R9** stopover -8/stop + wait penalty (-6/min over 5)

---

# F2.2 Architecture reference

**Primary design doc:** `workspace/docs/F2.2_SECTION_4_ARCHITECTURE_SPEC_2026-04-18.md`

**Kluczowe findings empiryczne:**
- OVERLAP 4908 cases (mid-trip pickup dataset dla C6)
- Speed tier FAST: 9 kurierów (SINGLETON p90 metric)
- Strong transitions: 220 pairs
- Weak transitions: 180 pairs
- Food-court zero-distance: 16 pairs
- TIER_A missed same-restaurant: 2187/rok = **108 kPLN/rok** (sekcja 3.3)
- PEAK regime: 11 cells (Sunday 13-19h dominant)

**Feature flags stan docelowy (wiele obecnie już flipowane per V3.19/V3.20):**
```python
USE_PER_ORDER_GATE = False           # C2
ENABLE_C2_SHADOW_LOG = True          # C2 shadow ON
DEPRECATE_LEGACY_HARD_GATES = False  # C3
ENABLE_SPEED_TIER_LOADING = False    # C4
ENABLE_WAVE_SCORING = False          # C5 — V3.21 candidate
ENABLE_C5_SHADOW_LOG = True          # C5 shadow ON
ENABLE_MID_TRIP_PICKUP = False       # C6
ENABLE_PENDING_QUEUE_VIEW = False    # C7
ENABLE_BUNDLE_VALUE_SCORING = False  # V3.18 — V3.22 candidate
ENABLE_TRANSPARENCY_ROUTE = True     # LIVE od 2026-04-19
ENABLE_TRANSPARENCY_REASON = True    # LIVE od 2026-04-19
ENABLE_TRANSPARENCY_SCORING = True   # LIVE od 2026-04-19
```

**Sprint C file structure** (`scripts/dispatch_v2/`):
- `wave_scoring.py` — 6 features (C5)
- `speed_tier_tracker.py` — standalone nightly script (C4)
- `commitment_emitter.py` — C6 skeleton
- `pending_queue_provider.py` — C7 helper

---

# NIGDY (critical don'ts)

- Nie łam produkcji bez `cp .bak` + py_compile + testy
- Nie dodawaj `prep_variance` do `pickup_ready_at` (wyłączone F1.8g)
- Nie proponuj kuriera z `picked_up` jako bundle candidate (L1/L2)
- Nie używaj identycznego ETA dla wszystkich kandydatów
- Nie używaj GPS pozycji >60 min jako realnej
- **NIE restartuj `dispatch-telegram.service` bez explicit ACK** — bezpośrednio wysyła propozycje do bota
- Nie używaj `urllib.request.install_opener` z nowym CookieJar w `get_last_panel_position` (invaliduje main session → HTTP 419)
- `edit-zamowienie` calls sekwencyjnie, nie ThreadPoolExecutor (CookieJar thread-safety)

---

# Panel API reference (NadajeSz-specific)

### Order detail endpoint
- **POST** `/admin2017/new/orders/edit-zamowienie`
- Body: `_token + id_zlecenie`
- Returns: `{"zlecenie":{...}}`

### Order status mapping (`id_status_zamowienia`)
- 2 = nowe/nieprzypisane
- 3 = dojazd
- 4 = oczekiwanie pod restauracją
- 5 = odebrane
- 6 = opóźnienie
- 7 = doręczone
- 8 = nieodebrano (anulowane przez kuriera)
- 9 = anulowane

Panel watcher ignores statuses 7, 8, 9.

### Timestamp fields
- **`czas_odbioru_timestamp`** — Warsaw time (Europe/Warsaw, NOT UTC) — actual pickup time
- **`created_at`** — UTC (suffix Z)
- **`czas_odbioru`** — int prep minutes; **<60 = elastyk** (coordinator declares via 5-60 min dropdown); **≥60 = czasówka** (hard restaurant declaration, held in Koordynator id_kurier=26)
- **`czas_kuriera`** (top-level, HH:MM) — declared courier arrival at restaurant
- `dzien_odbioru` — pickup timestamp
- `czas_doreczenia` — delivery timestamp

### Key params
- **`time`** param w `/admin2017/new/orders/przypisz-zamowienie`: integer minutes from now (nie timestamp nie HH:MM)
- **`--keep-time`** flag musi re-fetch original `czas_odbioru` z `edit-zamowienie` i resend integer (sending `0` clears UI)

### Address extraction
- Restaurant address: `address.street`
- Restaurant name: `box_zam_name` from HTML

### Virtual courier
- `id_kurier=26` "Koordynator" = holding bucket dla scheduled orders (czasówka)

---

# Kontakty & infrastructure

### Serwer
- **IP:** 178.104.104.138 (Hetzner CPX22, Ubuntu 24.04, UTC)
- **Panel gastro:** gastro.nadajesz.pl (Laravel, CSRF tokens)
- **Panel admin GPS:** https://gps.nadajesz.pl/panel (admin/nadajesz2026), HTMX+Tailwind+Leaflet+SSE 5s

### Bots
- **@NadajeszBot** — proposals
- **@GastroBot / NadajeszControlBot** — stop/start control (port 8443 HTTPS)
- **Adrian Telegram ID:** 8765130486
- **Grupa ziomka:** -5149910559

### Ports
- 8443 HTTPS — NadajeszControlBot
- 8765 — legacy Traccar (fallback)
- 8766 — PWA gps_server (dead)
- 8767 — courier-api (active FastAPI)
- Nginx routing: /panel→:8767, /api/*→:8767, /gps→:8766 (legacy PWA), /apk/→static APK

### Runtime & services
- **AI runtime:** OpenClaw 2026.3.27 in Docker, model openai/gpt-5.4-mini (DeepSeek fallback)
- **Stop flag:** `/tmp/gastro_stop`
- **Exec approvals:** `openclaw approvals set` CLI (nie openclaw.json)

### APIs
- **Mapping:** Google Maps Distance Matrix API (active)
- **Geocoding:** Nominatim / OpenStreetMap (Google Geocoding API denied)
- **Schedule:** Google Sheets (Spreadsheet ID: `1Z5kSGUB0Tfl1TiUs5ho-ecMYJVz0-VuUctoq781OSK8`, gid `533254920`); fetch 06:00 i 08:00 daily
- **Courier App:**
  - APK https://gps.nadajesz.pl/apk/courier.apk
  - package `pl.nadajesz.courier`
  - Kotlin+Compose, Room 50k buffer
  - Upload coroutine 30s (NIE WorkManager)
  - Adaptive GPS 20/30/40s+50m
  - Watchdog WM 15min, BootReceiver→flag
  - Backend SQLite WAL, dual-write `gps_positions_pwa.json`
  - Auth: PIN `kurier_piny.json`, UUID token, 90min auto-logout

---

# Key learnings accumulated (V3.8 → V3.20)

### Infrastructure
- **Never restart systemd without `py_compile` and import check first**
- `jq` nie zainstalowany na serwerze — JSON manipulation musi być Python
- `urllib` CookieJar nie thread-safe — `edit-zamowienie` sekwencyjnie
- `get_last_panel_position` nigdy nie wolno wołać `urllib.request.install_opener` z nowym CookieJar (invaliduje main session → HTTP 419)
- Geocoding uses Nominatim/OpenStreetMap (Google denied; tylko Distance Matrix active)
- Subprocess calls z `gastro_scoring.py` muszą używać host path `/root/` nie Docker path `/home/node/`

### F2.2 Sprint C / V3.19/V3.20 specific
- **Every new metric w dispatch_pipeline/feasibility_v2 needs downstream consumer checklist**:
  1. shadow_dispatcher `_serialize_candidate` (location A)
  2. inline best serialization (location B)
  3. learning_analyzer readers
  4. test suite
- **Feature flags default False przy deploy** = zero production impact przy shadow mode
- **Rollout gap 24-48h między flag flips** = ryzyko cascade fail jest realne
- **Import chain analysis przed restart** — może okazać się że tylko 1 service wymaga restart
- **Plan manager atomic writes** (V3.19b) — fcntl lockfile + temp→fsync→rename, zero corruption w 9h production
- **Packs reverse lookup** (V3.20) — 7 guards defensive, 5 fetch_details budget/cycle, idempotent emit

### Process
- **"Pytaj nie zgaduj"** — pytaj gdy niejasne, zamiast zgadywać
- **Autonomic mode dopuszczalny** dla CC gdy jawnie zadeklarowany, z 4 explicit escalation triggers
- Granular git tags jako rollback points (`f22-{sprint}-{step}-{status}`)
- Per sesja minimum 3 `.bak` backups dla `rollback_plan`
- Warsaw TZ zawsze via `ZoneInfo("Europe/Warsaw")`
- Atomic writes via temp/fsync/rename
