# TECH DEBT — Ziomek

**V3.27.5 closed 27.04 wieczór late (hotfix TASK H), V3.28 backlog**
- Last sprint: V3.27.5 (27.04 wieczór late, 3 tags, 2 fixes LIVE — Path A + Path B)
- Latest tag: `v3275-sprint-stable-2026-04-27`

# ═══════════════════════════════════════════════════════════════════
# SPRINT V3.27.5 27.04.2026 STATUS (close ~22:00 Warsaw — hotfix TASK H)
# ═══════════════════════════════════════════════════════════════════

## ✅ RESOLVED 2026-04-27 wieczór late (V3.27.5 hotfix sprint)

### V3.27.5 Path B — state_machine COURIER_ASSIGNED preserve terminal states
- ✅ Root cause (TASK H diagnoza #469099 wieczór): COURIER_ASSIGNED handler
  unconditionally setting status="assigned" → panel_diff post-PICKED_UP nadpisywał
  status="picked_up" → "assigned" w race ~12-18s.
- ✅ Bug rate: 13.4% (185/1384 picked-up orders w 7 dni) — systematyczny.
- ✅ Fix (`state_machine.py:288-330`): get_order(oid) before merge, if prev_status
  in (picked_up, delivered) preserve, log WARNING. courier_id i czas_kuriera
  fields nadal updateowane (legitimate re-assignment).
- ✅ Commit `1cdd195`, tag `v3275-path-b-state-preserve-terminal-2026-04-27`.
- ✅ Tests: 4/4 PASS (3 unit + 1 integration #469087 events.db replay).

### V3.27.5 Path A — _bag_dict_to_ordersim defense-in-depth
- ✅ Root cause level 2: simulator interface używał tylko status field.
- ✅ Fix (`dispatch_pipeline.py:923-933`): is_picked_up = (status=="picked_up")
  OR (picked_up_at is not None). picked_up_at jest canonical signal.
- ✅ Replikuje existing best practice w feasibility_v2.py + sla_tracker.py.
- ✅ Commit `d07629f`, tag `v3275-path-a-defense-in-depth-2026-04-27`.
- ✅ Tests: 5/5 PASS (4 unit + 1 integration #469087).

### V3.27.5 PRE-FIX VERIFY (TASK H KROK 1) — Q1-Q5 findings
- ✅ Q1: Path A scope minimal but complete (all simulator-level consumers
  protected via OrderSim boundary).
- ✅ Q2: Path B target jedyny critical handler (COURIER_ASSIGNED).
  NEW_ORDER theoretical concern → V3.28 backlog.
- ✅ Q3: counter-pattern confirmed (feasibility_v2 + sla_tracker already
  use picked_up_at preferred).
- ✅ Q4: panel_watcher cycle race condition characterized.
- ✅ Q5: bug rate 13.4% systematyczny (>10% threshold).

## V3.28 BACKLOG additions z V3.27.5 PRE-FIX VERIFY

### courier_resolver.py:232-233 status field bug (different concern)
- Pattern similar do TASK H bug ale w courier_resolver `is_picked = ... status==picked_up`
- Different concern: courier position determination, NIE bag pickup-node
- Effort 30 min (defensive fix similar do Path A pattern)
- Priority: MEDIUM (NIE blocker dla TASK H, ale defense-in-depth pożyteczne)

### NEW_ORDER preserve terminal handler (defensive)
- state_machine NEW_ORDER theoretically mógłby nadpisać picked_up jeśli re-emit
- Rare per panel_watcher idempotency — never observed
- Effort 30 min (mirror Path B pattern dla NEW_ORDER)
- Priority: LOW (defensive, not blocking)

### COURIER_UN_PICKED event type
- Edge case: jeśli Adrian INTENTIONALLY un-picks order w panelu, current Path B
  blockuje status revert ze wszystkimi paneldif COURIER_ASSIGNED.
- Solution: explicit `COURIER_UN_PICKED` event type dla legitimate un-pick flow
- Effort 1-2h (event emit z panel_watcher + handler w state_machine)
- Priority: LOW (rare scenario, current Path B WARNING log captures audit trail)

# ═══════════════════════════════════════════════════════════════════
# SPRINT V3.27.3 + V3.27.4 27.04.2026 STATUS (close ~22:00 Warsaw)
# ═══════════════════════════════════════════════════════════════════
- ~~Pending~~ ✅ **EXECUTED 27.04 wieczór**: Hetzner upgrade CPX22→CPX32
  (Adrian's task, off-peak post sprint close ~20:30 Warsaw). Verify: `nproc=4`,
  `free -h: 7.6Gi` (was 2 vCPU + 4 GB).
- Next sprint: V3.28 (post-Hetzner stable, lunch peak validation 28.04 12-14)

# ═══════════════════════════════════════════════════════════════════
# SPRINT V3.27.3 + V3.27.4 27.04.2026 STATUS (close ~22:00 Warsaw)
# ═══════════════════════════════════════════════════════════════════

## ✅ RESOLVED 2026-04-27 wieczór (V3.27.3 + V3.27.4 sprint)

### V3.27.3 Task 2 — DWELL rollback 3.0 → 2.0
- ✅ Root cause: V3.27.2 DWELL bump (2.0/1.0 → 3.0/3.0) over-estimated stop
  time → ETA inflation, displaceujące dobrych kandydatów (Adrian's domain obs).
- ✅ Fix: rollback do symmetric 2.0/2.0 baked-in. Net effect bag 6 stops -6 min.
- ✅ Commit `b157b32`, tag `v3273-dwell-rollback-2026-04-27`.

### V3.27.3 TASK B — Wait courier penalty bag>=1 (TASK 1 hypothesis B fix)
- ✅ Root cause (TASK 1 raport diagnozy #468945 Andrei): V327 wait penalty
  używał `pickup_at - ready_at` anchor (= restaurant wait, ~DWELL constant)
  zamiast `max(0, ready - arrival_at)` (courier idle wait).
- ✅ Fix: nowy `compute_wait_courier_penalty(wait_min, bag_size)` helper
  z linear gradient -10 first step, -5/min powyżej, hard reject >20.
- ✅ Conditional: bag_size >= 1 (kurier z dowóz w aucie, jedzenie stygnie).
- ✅ Internal API: `RoutePlanV2.arrival_at` field added, `_simulate_sequence`
  return signature extended.
- ✅ Commit `9d313dd` + flag flip `e48e5bc`, tag `v3273-task-b-flag-flip-stable`.
- ✅ LIVE: Andrei #469089 bag=1 → -21.88 penalty (Ramen Base, max 6.49min).

### V3.27.4 — Frozen czas_kuriera TSP time window R27 ±5
- ✅ Root cause (TASK F deep dive #469014 Pani Pierożek 17:09): TSP cost =
  czysta dystans, time windows hard close +60min pozwalał TSP planować pickup
  gdziekolwiek w `[czas_kuriera, czas_kuriera+60]`. Dla orderów z committed
  czas_kuriera (po first_acceptance) = naruszenie nietykalności.
- ✅ Fix: dla orderów z `czas_kuriera_warsaw != None` → time window
  `[czas_kuriera - 5, czas_kuriera + 5]` hard. Status quo dla NEW orderów.
- ✅ Detection: simple `getattr(order, czas_kuriera_warsaw, None) is not None`.
- ✅ Edge case: window_open < 0 → clamp na 0.
- ✅ Flag default True per Adrian (safety zasada egzekwowana natychmiast).
- ✅ Commit `fc7b69e`, tag `v3274-frozen-pickup-window-2026-04-27`.

### V3.27.3 TASK G — Weekday traffic multipliers update (Adrian's domain)
- ✅ Combined dataset analysis (TASK G raport, n=2,681) pokazał systemic bias
  ~1.7× w observed_mult (DWELL underestimate + OSRM Białystok underspeed +
  multi-leg contamination). Bias-adjusted weekday matches current 9/12 buckets.
- ✅ Adrian's manual values applied to 5 buckets:
  13-14: 1.3 → 1.2; 14-15: 1.3 → 1.2; 15-16: 1.6 → 1.5;
  16-17: 1.6 → 1.3 (largest); 20-21: 1.1 → 1.0.
- ✅ Sat/Sun unchanged (Adrian: artefakt multi-leg, nie real traffic).
- ✅ Commit `b1fe6af`, tag `v3273-taskg-mnozniki-2026-04-27`.

### V3.27.3 Observability fix (post TASK B flag flip detected)
- ✅ Detected gap: `shadow_dispatcher._AUTO_PROP_PREFIXES` whitelist NIE miał
  `v3273_` ani `v3274_` → 5 V3.27.3 fields NIE propagated.
- ✅ Fix: 1-line edit, dodać `v3273_` + `v3274_` do prefixes tuple.
- ✅ Verified post-restart: 6/6 V3.27.3 fields w shadow log #469089.
- ✅ Commit `89050aa`, tag `v3273-observability-fix`.

## V3.28 BACKLOG ADDITIONS (post V3.27.3 sprint)

### Real-time traffic API integration
- Adrian's strategiczne pytanie 27.04 wieczór: czy 3 alternatywy?
  1. Google Distance Matrix API (paid, real-time accurate, accuracy znana)
  2. OSRM + paid traffic feed (mid-cost, kontrola self-host)
  3. Crowd-source GPS-based traffic (free, własne GPS data, wymaga modeling)
- Decision deferred do post-V3.27.4 lunch peak validation 28.04.

### Methodology improvements per TASK G raport
- DWELL true measurement (instrument courier app dla actual handover times)
- Single-leg filter dla historical (panel state at pickup, NIE heuristic overlap)
- OSRM Białystok calibration (controlled test routes z known speeds)
- Geocode coverage extension (89% rows lost na address lookup, dodać outskirts)
- Pre-requisites pełnej traffic mult recalibration

### V3.28 future: TSP cost function lateness term
- Path 2 z TASK F raport recommended fix paths
- 4-6h impl, complex (OR-Tools cost callback z time slack penalty)
- Defer post-Hetzner upgrade (CPX22 already saturated)

# ═══════════════════════════════════════════════════════════════════
# SPRINT V3.27 25.04.2026 STATUS (close ~19:00 Warsaw)
# ═══════════════════════════════════════════════════════════════════

## ✅ RESOLVED 2026-04-25 wieczór (V3.27 sprint)

### Bug X — TSP timing under-estimation 30-50%
- ✅ Root cause: V326_OSRM_TRAFFIC_TABLE["weekend"]=1.0 flat całą dobę. Sobota peak 16-21 → matrix=raw OSRM free-flow.
- ✅ Fix: split weekend → saturday/sunday list buckety. Sobota peak 12-21 max ×1.2, niedziela płaska. (commit `0c4d92e`, tag `v327-fix-bug-x-traffic-mult-2026-04-25`)
- ✅ Secondary path drive_min OSRM-first (zamiast haversine/fleet_speed_kmh fallback)

### Bug Y — TSP picks suboptimal sequence  
- ✅ Mental simulation verified: traffic_mult global value preserves ratio → tied permutations remain tied
- ✅ Fix: shortest first drop tie-breaker (Q8 Opcja 3) — gdy |total_diff|<2min ties → secondary sort by first_drop_arrival_min ASC. (commit `8c8b427`, tag `v327-fix-bug-y-tie-breaker-shortest-first-2026-04-25`)
- ✅ #468508 mental sim verified: Skłodowskiej-first wygrywa post-tie-breaker (3min vs 13min first drop)

### Bug Z — Cross-quadrant bundle SOFT penalty + corridor mult
- ✅ Hipoteza Z2 verified: drop_proximity_factor LIVE TYLKO bundle_level1 (same restaurant), cross-restaurant level3 brak quadrant check
- ✅ Z-OWN-1 corridor: bundle_level3 `_min_dist_to_route_km` rysuje wielki corridor cross-quadrant bag drops → false positive "po drodze"
- ✅ Fix Q5: SOFT mult final_score *= 0.1 (cross) / 0.7 (adj) / 1.0 (same)
- ✅ Fix Q5a: bonus_r4 *= min(drop_proximity_factor) — corridor zeroed dla cross-quadrant bag
- (commit `369d46f`, tag `v327-fix-bug-z-bundle-soft-penalty-2026-04-25`)

### Districts coverage expansion (V3.27 Bug Z follow-up)
- ✅ Top 100 streets coverage: 90/100 → 97/100
- ✅ 3 priority mappings (Nominatim HIGH verified):
  - Władysława Bełzy → Białostoczek
  - Marii Skłodowskiej-Curie → Piaski (removed duplicate w Centrum)
  - Feliksa Filipowicza → **Nowe Miasto** (Adrian local knowledge override Nominatim Dojlidy)
- ✅ 4 best-effort streets: Sudecka→Skorupy, Bitwy Białostockiej→Białostoczek, Depowa→Bema (cap 7/50)
- ✅ V327_STREET_ALIASES dict (11 entries) + `_v327_normalize_street_for_matching()` helper
- ✅ City-aware geocoding preserved (Filipowicza+Kleosin → Kleosin)
- (commits `70b7c04` + `6161c40`, tags `v327-fix-districts-coverage` + `v327-hotfix-filipowicza-mapping`)

### Latency parallel + Phase 1 optimization
- ✅ ThreadPoolExecutor parallel ThreadPoolExecutor wrapping fleet eval loop, OSRM RLock cache thread-safety, V326_OR_TOOLS_TIME_LIMIT_MS RESTORED 50→200ms (commit `46051d6`, tag `v327-fix-latency-parallel-2026-04-25`)
- ✅ Phase 1 fixes (Adrian Option B post root cause diagnosis):
  - Skip OR-Tools dla bag<=1 (V327_MIN_OR_TOOLS_BAG_AFTER=2) — bruteforce fast path 1-24 perms <5ms
  - Ortools warm-up at startup (saves 153.5ms cold first-thread)
  - (commit `aa029bb`, tag `v327-phase1-latency-fix-2026-04-25`)
- ✅ Phase 1 verification: 4/5 proposals <500ms target. p50 ~375ms (vs pre-Phase1 ~515ms = -140ms / -27%). p95 ~624ms (1 outlier bag>=2 case).

### Other V3.27 wins
- ✅ Pre-existing test_v319h_bug1_drop_proximity #27 fix (flag default mismatch, V3.19h LIVE od 21.04)

## ✅ RESOLVED 2026-04-25 (V3.26 sprint)

- ✅ **Bug A** km_to_pickup chronological last_drop → anchor-based incremental (commit `3b93bf3`, LIVE flag True)
- ✅ **Bug A** scoring decay martwy kod (3km saturate 0) → decay 5 (commit `c1020ef`, LIVE no-flag)
- ✅ **Bug A** rationale display "-km*5" mylące → real s_dystans contribution (LIVE)
- ✅ **Bug B** event_bus CZAS_KURIERA_UPDATED rejected by allowlist → added (commit `615b60e`, LIVE)
- ✅ **Bug C** "po drodze" geographic-only → time + intervening + direction strict (commit `34ff770`, LIVE flag True)
- ✅ **Bug D** "po odbiorze z X" first geographic match → anchor pre-insertion (commit `b17bd36`, LIVE flag True)
- ✅ **V3.19g1** incomplete deployment (event_bus allowlist) — Bug B fix
- ✅ **R-09** NameError haversine (~960 errors/dobę pre-fix) — commit `a70a914` night sprint 24/25.04
- ✅ **C1** Solo Fallback dead od V3.25 — commit `bb74bfe` night sprint
- ✅ **ortools** dependency installation Ubuntu 24.04 — venv migration commit `d20ff90`
- ✅ **Block 4** OSRM Traffic Multipliers FLIP LIVE 08:12 (commit `6813059`)
- ✅ **H1** serializer auto-prop v325/v326 keys (commit `7dee94a`)
- ✅ **H2** R-06 trajectory bag=1 fix (commit `74e9f80`, shadow flag)
- ✅ **B#M3** chain_eta haversine fallback × traffic_mult (commit `14f5efa`)
- ✅ **Block 4D** _apply_traffic_multiplier always-record shadow fields (commit `a3eb391`)

## ✅ ZAMKNIĘTE V3.27 (Fix 6 + Fix 7 re-flipped LIVE post Phase 1)

- ✅ **Fix 6** OR-Tools TSP solver — flag flipped True 17:39 UTC (`v327-flag-flip-final`); Phase 1 shortcut bag>=2 only (`aa029bb`)
- ✅ **Fix 7** Same-restaurant grouping — flag flipped True 17:39 UTC

## 🚨 OPEN — V3.28 backlog (sorted by priority)

### V3.28-INFRA-HETZNER-UPGRADE-CPX32 [HIGH, niedziela 26.04]

- **Owner**: Adrian (niedziela rano 8-10 Warsaw, off-peak window)
- **Effort**: 30-45 min (snapshot + rescale + verify)
- **Cost**: +6 EUR/mies (CPX22 €7.99 → CPX32 €13.99)
- **Why CPX32 (NIE CPX31)**:
  - CPX31 deprecated (Hetzner discontinued)
  - CPX32 newer AMD EPYC Genoa, niższa cena per vCPU
  - 4 vCPU, 8 GB RAM, 160 GB SSD
- **Procedure**:
  1. Snapshot pre-upgrade (Hetzner Cloud Console)
  2. Rescale CPX22 → CPX32 (panel)
  3. Verify: `nproc=4`, `free=8GB`, all systemd services active
  4. Latency benchmark first 5 proposals — expected p95 ~250-300ms
- **Expected improvements**:
  - Parallel efficiency 13.4% → ~25-30%
  - p95 latency ~624ms → ~250-300ms
  - Warsaw expansion ready

### V3.28-R04-GRADUATION-SCHEMA [HIGH, 3-4h]

- Adrian mandate od 24.04 + Lekcja #27 strategic principle (jakość + skalowanie)
- Multi-gate metrics-based promotion (NIE hardcoded 30 days dla wszystkich kurierów)
- **Gates**: `new → standard_trial → standard → standard_plus → gold`
  - **Gate 1** (new → standard_trial): >=15 deliveries + >=5 dni + median time <=35 min
  - **Gate 2** (standard_trial → standard): >=100 deliveries + >=20 dni + override_rate <25%
  - **Gates 3-4**: MANUAL Adrian/Bartek ACK
- **Implementation**:
  - Nightly job `dispatch-tier-review.timer` 3:00 Warsaw
  - Reads completed_deliveries per cid, days_active, override_rate
  - Telegram alert "Kurier X gotowy na promotion"
  - Adrian ACK → apply tier change w `courier_tiers.json`

### V3.28-DISTRICTS-LONG-TAIL [MEDIUM, 2-3h]

- 638 streets observed in shadow log post-V3.27 coverage extension (top-100 = 97/100)
- Identify top 200 by frequency, mass-map z Nominatim + Adrian ACK batch
- Expected coverage 97/100 → 99+/100 top traffic
- Effort: ~3h (auto-mapping + Adrian verify high-traffic)

### V3.28-PRE-CANNED-REASON-CODES [MEDIUM, 2-3h]

- Telegram dropdown UI dla Daily Q&A reason codes
- 7 reason codes:
  1. `WAVE_CONTINUATION_MISSED`
  2. `TRAJECTORY_MISMATCH`
  3. `SCHEDULE_OVERRIDE`
  4. `PICKUP_COLLISION`
  5. `DRIVER_QUALITY_MISMATCH`
  6. `FLEET_BALANCE_OFF`
  7. `OTHER`

### V3.28-FEASIBILITY-C3-V325-FIXTURE [MEDIUM, 1-2h]

- 4 pre-existing test fails `test_feasibility_c3.py` (`v325_NO_ACTIVE_SHIFT` test fixture)
- Same root cause jak `test_decision_engine_f21` (V3.25 schedule hardening fixture issue)

### V3.28-ALEJA-PARSER-FRAGMENT [LOW, 30 min]

- drop_address parser zwraca "Aleja"/"aleja" jako standalone street name dla "Aleja Jana Pawła II"
- 19+ events/30d w 2 wariantach (parser artifact, NIE ulica per se)
- Fix: parser regex enhanced multi-token preserved

### V3.28-SUPRASLSKA-OUTSIDE-CITY [LOW, 1h]

- Supraślska street głównie w Wasilkowie (per Nominatim)
- Outside-city stream handling — needs separate flow (city='Wasilków' input)

### V3.28-HELP-HANDLER-FIX [LOW, 15 min]

- Telegram /help command nie odpowiada (od V3.26)
- Trywialny fix routing handler

### V3.28-SLA-TRACKER-DECISION [LOW, 30 min]

- sla-tracker service stopped 24.04
- Decision: fix vs kill (Adrian's call — czy wciąż wartościowy)

### V3.28-PICKUP-COORDS-MISMATCH [LOW, 1-2h]

- 12.4km gap restaurant_coords cache vs pipeline (V326)
- Defer non-firing edge case

### V3.28-C2-TZ-DEFENSIVE-CLEANUP [LOW, 1-2h]

- 40+ files non-firing ale code quality (TZ defensive boilerplate)
- Cleanup gdy refactor okazja

### V3.28-BUG-Y-PER-SEGMENT-MULTIPLIERS [LOW conditional, 1h obs]

- Tie-breaker resolved arbitrary tie-break, ale per-segment traffic mult byłby fundamental fix
- **Conditional: SKIP jeśli post-Hetzner p95 <300ms** (tie-breaker enough)
- OR-Tools meta-heuristic może też różnicować — observation post-flip

### V3.28-OSRM-COMPOSE [HIGH, escalated 2026-04-26]

- OSRM `osrm-server` container obecnie standalone (`docker run` ad-hoc), NIE w żadnym compose file
- Po V3.27.1 sesja 1 OSRM fix (Docker `unless-stopped`), persistent ale brak reproducible IaC
- **Escalated**: `docker logs --since` ma bug po `docker start` exited container — observability gap
- Effort: 1-2h (tworzy `osrm-compose.yml` lub dorzuca do `/root/openclaw/docker-compose.yml`, dodaje monitoring)
- Conditional: post V3.27.1 sesja 1 + 2 stable

### V3.28-EVENT-BUS-CONSUMER-STUCK [MEDIUM, 2-4h diagnose+fix] — ESCALATED 2026-04-26 sesja 1 Krok 7

- Empirical observation 2026-04-26 sesja 1 Krok 7 post-restart shadow PID 19450:
  consumer PRZERABIA 1 event (processed counter 6664 → 6665), potem znów się zacina.
- Hipoteza initial "SQLite-persisted bug, restart NIE pomaga" OBALONA via heartbeat
  data 16:00→16:06 (Lekcja #5 + #19: empirical hypothesis revision):
  ```
  16:00:28: pending:8852 / processed:6664   ← post-restart (immediately)
  16:01:29: pending:8856 / processed:6665   ← +1 (consumer ALIVE first cycle!)
  16:02:29: pending:8857 / processed:6665   ← stuck znowu
  16:03→16:06: stuck na 6665 (5 min flat)
  ```
- To NIE SQLite-persisted bug — to **intermittent consumer thread crash/deadlock
  po pierwszym cycle**.
- pending rośnie ~1/min, w 24h ≈ 1500+ stuck. Main loop proposals NIE blokowane
  (proposes działają OK), ale event_bus consumer wymaga kolejnego restart żeby
  obsłużyć następny event.
- Możliwe przyczyny do diagnozy:
  1. Consumer thread crash po wyjątku (no auto-restart, no supervision)
  2. SQLite write lock contention (consumer reader vs main writer)
  3. Race condition: process event, fail, no retry, zaciska
  4. Worker thread death + brak supervisor pattern
- Rekomendowana diagnostyka:
  - stderr logging w consumer thread (capture exceptions silently swallowed)
  - thread heartbeat counter (osobny od `totals.processed` żeby distinguish
    main loop alive vs consumer alive)
  - SQLite lock metrics (`pragma busy_timeout`, busy retries)
- Tymczasowa mitigacja: brak (main loop działa OK, proposals nie blokowane).
  Workaround conditionally: dispatch-shadow restart 1×/dzień jako band-aid
  (każdy restart procesuje 1 event). NIE rekomendowane na dłużej.
- Effort: 2-4h thread debug + fix
- Conditional: V3.28 EARLY tygodnia (priority MEDIUM po empirical findings)

### V3.28-CLEANUP-BAK-FILES [LOW, 30 min]

- 15+ `.bak_v319*` / `.bak_v320` / `.bak_v3271*` / `.bak_v317b` files w `dispatch_v2/`
- Pre-existing debris z sprintów V3.17-V3.27.1
- Effort: 30 min audit + git rm (zachować recent .bak ~7 dni)
- Conditional: po V3.27.1 stable 2 tygodnie

### V3.28-MIGRATE-TESTS-PYTEST [LOW, 2-4h]

- 20+ test files używają standalone script pattern (`sys.exit(0 if failed+errors==0 else 1)` w module body bez `if __name__ == "__main__":` guard)
- Pytest collection crash przy import (V3.27.1 sesja 1 dodał pytest do venv ale nie konwertował testów)
- Effort: 2-4h (per-file refactor: dodać `if __name__:` guard, ewentualnie konwertować na `def test_*` pytest-style)
- Conditional: rozważyć przed V4.0

### V3.28-CLEANUP-LEGACY-WAIT-PEN [LOW, 30 min]

- V3.27.1 sesja 1 zachowała legacy `bonus_r9_wait_pen_legacy` calculation w dispatch_pipeline dla A/B comparison shadow log
- Po V3.27.1 stable 2 tygodnie + ENABLE_V327_WAIT_PENALTY=True validated → cleanup legacy block
- Effort: 30 min (remove `_legacy` block + serialization fields, update tests)
- Conditional: V3.27.1 + 14 dni stable

### V3.28-BUG2-INTEGRATION-TEST [LOW, 30 min]

- V3.27.1 sesja 1 testy weryfikują że time_windows constraint passed do solvera (mock)
- BRAKUJE: integration test z realnym OR-Tools solve (bez mock) który asserts że bag scenario z pickup_ready_at 14:25/14:30/14:43+new 14:30 → solver output sequence NIE ma Chicago Pizza (ready 14:30) na 4-tej pozycji
- Effort: ~30 min (sesja 2 jutro pre-flip — Adrian's mental log)
- Conditional: sesja 2 V3.27.1

### V3.28-TEST-V319A-PICKED-UP-FLOOR-EDGE [LOW, 30-60 min]

- `test_v319a_picked_up_floor.py` 13/15 PASS (2 FAIL: real GPS drive-based ETA delta edge case)
- Pre-existing pre-V3.27.1, verified identical via git stash 2026-04-26
- Effort: 30-60 min diagnoza + fix
- Conditional: V3.28+ po stable V3.27.1 flip

### V3.28-TEST-V319D-BASE-SEQUENCE-EDGE [LOW, 30-60 min]

- `test_v319d_read_integration.py` 12/14 PASS (2 FAIL: base_sequence passthrough scenario — feasibility_v2 plan not None)
- Pre-existing pre-V3.27.1, verified identical via git stash 2026-04-26
- Effort: 30-60 min
- Conditional: V3.28+

### V3.28-TEST-V319H-BUG2-EDGE [LOW, 15-30 min]

- `test_v319h_bug2_wave_continuation.py` 22/23 PASS (1 FAIL: wave assertion edge case)
- Pre-existing pre-V3.27.1, verified identical via git stash 2026-04-26
- Effort: 15-30 min (small scope)
- Conditional: V3.28+

### V3.28-TEST-V319H-BUG4-FLAG-DEFAULT [LOW, 15-30 min]

- `test_v319h_bug4_tier_cap_matrix.py` 29/30 PASS (1 FAIL: BUG-4 flag default check)
- Pre-existing pre-V3.27.1, verified identical via git stash 2026-04-26
- Effort: 15-30 min (small scope)
- Conditional: V3.28+

### #19 — V3.27.1 sesja 4 PRE-WARM-LOGIN [HIGH, 5 min, FIX JUTRO 9:00]

- **Discovered sesja 3 Krok 6.5+ diagnose**: panel_client login refresh 22-min
  cykl (CSRF expiry) blokuje proposal latency 6-7s przy każdym refresh.
  Pre-V3.27.1 panel_client używany async w panel_watcher (off path). V3.27.1
  pre-proposal-recheck używa sync w dispatch_pipeline → propaguje login latency.
- **Empirical 1h post-flip**: 3/6 outliers (50% niedzielne low traffic, n=6),
  100% korelacja z 3 login refresh events. Math projection lunch peak: 50-100
  proposals/h × 3 logins/h = **3-6% outliers** (acceptable).
- **Fix**: w `shadow_dispatcher.py` startup po imports + ortools warm-up, dodać:
  ```python
  panel_client.login(force=True)  # eliminate first-proposal cold login penalty
  ```
- Effort: 5 min implementation + 1 test
- Conditional: V3.27.1 sesja 4 jutro 27.04 9:00-9:30, przed lunch peak validation

### #20 — V3.28 BACKGROUND-LOGIN-REFRESH-THREAD [DEFERRED post-Hetzner CPX32]

- **Status update 27.04 wieczór**: DEFERRED — post-Hetzner CPX32 alone gives
  headroom (4 vCPU + 7.6 GB vs prior 2 vCPU + 4 GB). Pre-warm login (#19) +
  CPX32 latency parallelism = sufficient bez background refresh thread.
- **Strategic fix (oryginal spec)**: async thread refresh CSRF co 18 min (przed expiry) →
  zero blocking proposal latency ever
- **Why DEFERRED**: pre-warm login (#19) eliminates first-proposal-cold-login.
  Co 22 min jeden proposal nadal trafi login refresh. ALE: CPX32 4-vCPU
  parallelism + 2x throughput cap = single 5-7s outlier per cycle nie blocks
  dispatch (parallel candidates evaluations). Lunch peak 28.04 da empirical
  signal czy outliers visible w p95.
- Effort: 30-60 min implementation + tests (jeśli reaktywowane)
- Conditional reaktywacja: V3.28 jeśli post-CPX32 lunch peak nadal >5% p95
  outliers blocking proposals.

### V3.28-VENV-REQUIREMENTS-OUTSIDE-REPO [LOW, 30-60 min]

- `/root/.openclaw/venvs/dispatch/requirements.txt` poza dispatch_v2 git repo
  (`fatal: ... is outside repository at .../dispatch_v2`)
- V3.27.1 sesja 1 Krok 1.5 dodał pytest+pytest-mock+pytest-asyncio do venv jako
  side-effect, ale plik nie version-controlled
- Następny rebuild venv zapomni te dependencies → custom test runner pattern
  pozostanie wymuszony albo manual install repeat
- Adrian decision options dla V3.28+:
  (a) Move venv requirements do `dispatch_v2/requirements-venv.txt` (tracked
      w głównym repo)
  (b) Setup separate `/root/.openclaw/venvs` git repo z requirements
  (c) Document w CLAUDE.md "post-venv-rebuild manual install pytest+mock+asyncio"
- Effort: 30-60 min (depending na opcję)
- Conditional: V3.28+ infra cleanup sprint

### #17 — V3.27.1 sesja 3 BUG-1-FIX [✅ RESOLVED 2026-04-26 sesja 3 commit 8e75827]

- **Bug 1 root cause (zdiagnozowane Krok 5 ROLLBACK 2026-04-26 19:10 UTC)**:
  `_v327_safe_fetch_czas_kuriera` w dispatch_pipeline.py używa
  `fresh.get("czas_kuriera_warsaw") or fresh.get("czas_kuriera")` na surowym
  `panel_client.fetch_order_details()` response.
- **Reality**: panel API zwraca raw `'zlecenie'` z `czas_kuriera="19:14"`
  (HH:MM string), klucz `czas_kuriera_warsaw` (ISO) NIE istnieje w raw —
  jest computed downstream przez `panel_client.normalize_order(raw)` (merge
  daty + HH:MM + TZ Warsaw).
- **Effect post-flip**: helper zwracał HH:MM jako "ISO", payload `new_ck_iso="19:14"`,
  `_verify_czas_kuriera_consistency` sanity FAIL → 5+ ERROR linii w state_machine
  + każdy emit `skipping persist`. Plus latency 6949ms (vs baseline 730ms).
- **✅ RESOLVED sesja 3 commit `8e75827`**: helper return Tuple (iso, hhmm) +
  `panel_client.normalize_order(fresh)` po fetch + emit z OBIEMA polami.
  10/10 testów PASS z REAL panel schema (Lekcja #28). Zero state_machine sanity
  errors w 1h post-flip (vs sesja 2 5+ ERROR/restart).
- **Fix**: w `_v327_safe_fetch_czas_kuriera`:
  ```python
  fresh = panel_client.fetch_order_details(oid, timeout=int(timeout))
  if fresh is None: return None
  norm = panel_client.normalize_order(fresh)  # ← KEY FIX
  return norm.get("czas_kuriera_warsaw") if norm else None
  ```
- Plus: `_v327_emit_pre_recheck_event` MUSI wypełnić **oba** pola payload —
  `new_ck_iso` (ISO) AND `new_ck_hhmm` (HH:MM) — bo state_machine sanity
  wymaga obu.
- **Test gap (Lekcja #28 — NEW)**: 9 unit testów PASS bo mock fixture zwracał
  fake klucz `{"czas_kuriera_warsaw": "<ISO>"}` którego real API NIE ma.
  Integration test z REAL `panel_client.normalize_order(raw)` flow wymagany
  przed re-flip.
- Effort: ~30-60 min implementacja + integration test + smoke test (real panel)
- Conditional: V3.27.1 sesja 3 DZIŚ wieczór ~21:00-22:00 Warsaw

### #18 — V3.27.2 STOP-OVERHEAD [✅ RESOLVED REVISED — DWELL bump direct, sesja 3 commit 8e75827]

- **Sesja 3 schema discovery**: `route_simulator_v2.py:34-35` already miało
  `DWELL_PICKUP_MIN=2.0` + `DWELL_DROPOFF_MIN=1.0` hardcoded — Adrian initially
  nie wiedział. Revised decision: bump bezpośrednio (NIE flag-gated overhead),
  to jest correction wartości od dawna w kodzie.
- **✅ RESOLVED**: `DWELL_PICKUP_MIN: 2.0 → 3.0`, `DWELL_DROPOFF_MIN: 1.0 → 3.0`
  per Adrian's domain knowledge kurier+koordynator. Net effect: bag 6 stops
  (3p+3d) = +9 min real ETA. NIE flag-gated. 8/8 sprint-touched testy PASS bez
  zmian w asserts.

### #18-OBSOLETE — Original V3.27.2 STOP-OVERHEAD plan (PRE-DISCOVERY)

- **Adrian initial decision 2026-04-26 wieczór**: każdy pickup/drop = 2 min real
  overhead nieuwzględniony w current ETA estymacji.
- **Implikacja**: bag z 6 stops (3 pickup + 3 drop) = +12 min real vs ETA
  predykcja → systematic under-estimation, błędne SLA decisions.
- **Fix**: dodać do `common.py`:
  ```python
  ENABLE_V327_STOP_OVERHEAD = _os.environ.get("ENABLE_V327_STOP_OVERHEAD", "0") == "1"
  V327_PICKUP_OVERHEAD_MIN = 2.0
  V327_DROP_OVERHEAD_MIN = 2.0
  ```
- Wpięcie w `_simulate_sequence` (route_simulator_v2.py) per stop dodać
  overhead do `total_duration_min` + `predicted_delivered_at` + `pickup_at`.
- Tests: 5 cases (disabled baseline, enabled solo bag=1, bag=3, bag=6 Bartek
  peak, edge case empty plan).
- Atomic flip RAZEM z 3 flagami V3.27.1 sesji 2 (= **4 flag total**) po
  Bug 1 fix wieczór.
- Effort: ~1h implementacja + 5 testów + integration verify
- Conditional: V3.27.2 sesja 3 DZIŚ wieczór ~22:00-23:00 Warsaw

## 🧪 TEST GAP (Lekcja #24 — RESOLVED in V3.27)

- ✅ `test_v327_proposal_lifecycle_latency_slow.py` (2 tests) — full lifecycle p95 + race conditions

## 📚 LEKCJE V3.27 (added)

- **#25** Mental simulation może być naivny (traffic_mult global value preserves ratio — Bug Y NIE self-resolves)
- **#26** Domain knowledge > LLM/API confidence (Filipowicza Adrian override Nominatim)
- **#27** Hardware oversubscription dla parallel (CPX22 niewystarczająca dla 10-worker OR-Tools)
- **#29** (V3.27.1 sesja 3 NEW) Sync calls w hot path mogą ujawnić latency
  niewidoczną off-path. Pre-V3.27.1 `panel_client.login()` refresh (~6s co
  22 min) był niewidoczny w proposal latency bo używany async w panel_watcher.
  V3.27.1 pre-proposal-recheck wprowadziła go synchronously w dispatch_pipeline
  → exposed architectural overhead (50% outliers w 1h niedzielne low traffic,
  100% korelacja z login refresh events). **NIE V3.27.1 algorithmic bug, NIE
  Bug 1 fix issue** (Bug 1 fix verified zero state_machine errors). **Reguła**:
  każda new sync call do external service w hot path wymaga audit istniejącego
  service'u behavior (login refresh, connection pool, timeout, retry, periodic
  blocking operations). Fix path: pre-warm at startup (#19 sesja 4) + background
  refresh thread (#20 V3.28).

- **#28** Mock tests passed ale integration FAIL (V3.27.1 sesja 2 Bug 1):
  9 unit testów `test_v3271_pre_proposal_recheck.py` użyło mock fixture
  `{"czas_kuriera_warsaw": "<ISO>"}` z **wymyślonym kluczem** którego real
  `panel_client.fetch_order_details` raw response NIE zwraca. False
  confidence — atomic flag flip → IMMEDIATE rollback (latency 6949ms +
  state_machine sanity FAIL). **Reguła**: integration tests z real API
  flow (lub realistic fixture matching production schema) wymagane dla
  helper'ów wywołujących external API. Atomic separation kod load (Krok 3)
  vs flag flip (Krok 4-5) **uratowała sprint** — czysta detekcja root cause,
  fast rollback path bez utraty innych komponentów (BUG-2, Wait penalty,
  A/B schema). Reguła Adrian's Plik wiedzy #18 ("Empirical validation >
  unit test") + Lekcja #24 (full lifecycle test) potwierdzona empirically.

## ⏳ OPEN — NIEDZIELA 26.04+

- ⏳ Bug F weekend mult ×1.0 → empirical bump (post-peak data 25.04)
- ⏳ R-04 Graduation Schema implementation (3-4h, multi-gate metrics)
- ⏳ Pre-canned reason codes Telegram dropdown (2-3h)
- ⏳ Daily Q&A Wave 1 review zaległe od 24.04 (Adrian solo, ~30-45 min)
- ⏳ /help handler fix (15 min)
- ⏳ sla-tracker decision (fix vs kill — service stopped 24.04)
- ⏳ V326-PICKUP-COORDS-MISMATCH (12.4km gap restaurant_coords cache vs pipeline)
- ⏳ V326-C2-TZ-DEFENSIVE-CLEANUP (40+ files non-firing ale code quality)
- ⏳ dispatch-telegram restart (natural redeploy — Telegram label "X km do {anchor}" + zaktualizowany format manifestuje dopiero po restart)

## ❌ ANULOWANE

- ❌ R-07 CHAIN-ETA flip (Adrian decision: plan IS already chain-aware via route_simulator, chain_eta pesymistyczny)
- ❌ R-08 PICKUP-EXTENSION-NEGOTIATION (Adrian: same rules dla wszystkich restauracji)
- ❌ R-12 RESTAURANT-HOLDING (Adrian: bez sensu)
- ❌ R-04 hardcoded 30-days graduation (replaced multi-gate metrics schema)

# ═══════════════════════════════════════════════════════════════════
# (legacy content od 2026-04-20 kontynuacja poniżej)
# ═══════════════════════════════════════════════════════════════════

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

### Parse wrapper layer: log unhandled top-level keys
Parse wrappery (panel_client, gps_client, etc.) które projektują PODZBIÓR pól
z API response MUSZĄ logować unhandled top-level keys (debug level wystarczy).
Invisible data loss jest kosztowniejszy niż verbose log — precedens: Finding #1
V3.19f (`panel_client.fetch_order_details:289` zwracał `raw.get("zlecenie")` i
wywalał top-level `czas_kuriera` przez całą historię pipeline, blokując
czas_kuriera propagation do decision-making).

Wzorzec (panel_client.fetch_order_details po V3.19f):
```python
_known_top = {"zlecenie"}        # expected, handled elsewhere
_handled = {"czas_kuriera"}       # explicitly propagated
for k, v in parsed.items():
    if k in _known_top: continue
    if k in _handled: zlecenie[k] = v
    else: _log.debug(f"unhandled top-level key '{k}'")
```

### Deferred tickets

#### V3.25-SLA-TRACKER-TZ — naive/aware datetime subtraction error co 10s (pre-existing)
Dyskowiony podczas V3.25 Daily Accounting smoke 2026-04-24: `dispatch-sla-tracker`
loguje **co 10s** error:
```
[ERROR] sla_tracker: loop: can't subtract offset-naive and offset-aware datetimes
```
Od co najmniej 20:00 UTC 2026-04-24, pewnie dużo dłużej (widoczne od momentu
pre-restart check). NIE spowodowane Daily Accounting flipem — mój restart
sla-tracker (przy dot-removal invalidation) tylko zachował ten error (pre-existing).
**Priority:** medium — SLA tracker sam cykluje, R6 alerts mogą być silent ruinowane.
Wymaga grep `_parse` vs `_parse_aware_utc` w `sla_tracker.py` + fix mixed TZ arithmetic.

**STATUS UPDATE 2026-04-24 20:36 UTC:** service **STOPPED** (`systemctl stop
dispatch-sla-tracker`) per Adrian D2(a) decision (session marathon 25.04 evening).
Rationale: error co 10s = ~8640/dobę noise; R6 alerts partial functionality
nie warte log pollution + cognitive overhead przy debug. Stopped do fix next
session (30-45 min scope: diagnose `_parse` vs `_parse_aware_utc`, mixed TZ
arithmetic). Impact stopped service: R6 bag_time alerts (>30min threshold)
NIE fire — acceptable 24h bo operational coverage przez panel + Adrian visual
monitoring. Priority escalated: medium → **HIGH** (LIVE capability missing).

#### V3.25-SLA-TRACKER-UNIT-DRIFT — unit file on-disk różni się od załadowanego → **RESOLVED 2026-04-25**
**STATUS RESOLVED 2026-04-25 sprint Block 3B:** `systemctl daemon-reload`
wykonany 09:11 Warsaw — unit drift warning zniknął, brak restartów żadnego
service. Sprawdzenie post-reload: dispatch-sla-tracker `Warning:` cleared.
Service wciąż inactive (D2(a) decision), drift naprawiony bezboleśnie.

#### V3.25-DOTS-CLEANUP — 45 hardcoded dotted refs w 13 plikach (deferred, low priority)
Po flipie Daily Accounting (2026-04-24) usunęliśmy kropki z `kurier_ids.json` i
`kurier_piny.json`: `"Bartek O."` → `"Bartek O"`, `"Michał K."` → `"Michał K"`
(source of truth: grafik Adriana). W kodzie projektu pozostało **45 hardcoded
dotted references w 13 plikach** (głównie test fixtures + 3 live runtime
miejsca: `telegram_approver.py:161` prompt, `build_v319h_courier_tiers.py:29-30`
komentarz, `courier_resolver.py:486` komentarz).

**Runtime impact = 0**: `telegram_approver._norm()` ma `rstrip(".,;:")` w
prefix-match → user input `"Bartek O."` normalized do `"bartek o"` → match
z fresh JSON `"Bartek O"` bez kropki. Parser funkcjonalnie OK.

**Pełna lista** przy czyszczeniu: `grep -rn --include='*.py' -E '(Bartek O\.|Michał K\.)' /root/.openclaw/workspace/scripts/dispatch_v2/ | grep -v .bak`
daje 45 hitów w:
- `tests/test_v325_pin_leak_defense.py` (11) — regression defense fixture
- `tests/test_v326_hotfix_parser.py` (8)
- `tests/smoke_telegram_buttons_freetext.py` (5)
- `tests/test_v325_step_d_r03.py` (4)
- `tests/test_v326_step1_r11.py` (3)
- `telegram_approver.py` (3) — prompt + sort comment
- `tests/test_v325_step_a_r02.py`, `test_speed_tier_tracker.py`,
  `test_panel_aware_availability.py` (2 each)
- `build_v319h_courier_tiers.py` (2), `tests/test_v326_step2_r05.py`,
  `test_v325_step_c_r04.py`, `courier_resolver.py` (1 each)

**Koszt:** ~1-2h selective edit (test fixtures najbardziej ryzykowne —
`test_v325_pin_leak_defense` definiuje hermetic scenario z kropką dla
phantom PIN leak — NIE rippować bez re-run test).
**Priority:** low. Podnieść tylko przy większym refactoringu telegram
parser albo gdy nowy kurier dostanie kropkę w panelu Adriana.

#### V326-R09-NAMEERROR — osrm_client not defined, R-09 wave veto DEAD in prod (CRITICAL) → **FIXED 2026-04-25**
**STATUS:** FIXED in commit `a70a914` + tag `v326-hotfix-r09-nameerror-2026-04-25`
(sprint 2026-04-25 late-night). Wariant A zastosowany — L1239 `osrm_client.haversine(`
→ `haversine(` (spójne z L818/969/985). Deployment pending dispatch-shadow restart
Block 4 tego samego sprintu.

`dispatch_pipeline.py:1239` używa `osrm_client.haversine(...)` ale module-level
import na linii 28 to TYLKO `from dispatch_v2.osrm_client import haversine`
(sama funkcja, nie moduł). `osrm_client` na L1239 = undefined → NameError
łapany przez except → `log.warning("V326_WAVE_VETO compute fail …")` na L1252.

**Impact:** R-09 WAVE-GEOMETRIC-VETO (flag True od 2026-04-23 21:12 UTC, commit
b2ccbd0) **NIGDY nie fire'uje** — każda próba compute crashes. Wave continuation
bonus BUG-2 (`bonus_bug2_continuation` +30pts) nie jest vetowany nawet w
geometrii SIDEWAYS/OPPOSITE > 3.0km. Cały feature effectively DEAD w shadow+prod.

**Scale:**
- First journal error: **2026-04-24 08:38:59 UTC** (earliest in current journal,
  journal rotated — bug istnieje od R-09 flag flip 2026-04-23 21:12:16 UTC)
- Since shadow restart 2026-04-24 12:26:02 UTC: **864 errors w 7h 25min**
- Rate: **~117/h ≈ 2800/dobę** przy normalnym load, peak może 3000+

**Hypothesis cross-module coupling z BUG3-STEP1 DISPROVEN:** BUG3 deploy
2026-04-24 12:25:50 UTC (commit 28aaf25), ale bug zaobserwowany 3h 46min
wcześniej (08:38:59). Oba bugi niezależne.

**Fix (trivial, ~5 min):** Line 1239 — zamień `osrm_client.haversine(` na
`haversine(` (już zaimportowana na L28). Albo dodaj `import dispatch_v2.osrm_client as osrm_client`
na L28 i zostaw 1239 jak jest. Drugie safer bo nie zmienia więcej nic, ale
pierwsze bardziej consistent z resztą pliku (L872 import w function body też
importuje function-level: `haversine as _hav`).

**Regression risk po fix:** R-09 zacznie REALNIE vetować wave_continuation
bonus w shadow → shadow decisions zmienią się w ~5% proposals (wstępna
estymacja — tam gdzie wave_continuation bonus był +30 a km_from_last_drop >3).
Flag ENABLE_V326_WAVE_GEOMETRIC_VETO=True → shadow selection może się zmienić
natychmiast. **Proponuję pre-fix:** (a) flip flag False + fix code + observe
shadow nowe decisions 24h → (b) flip True po confirmation że veto działa jak
intended. Albo surgical: fix + monitor pierwsze 100 proposals po restart dla
R-09 fire rate.

**Priority:** HIGH — R-09 była designed jako critical veto path (prevent
koordynator complaints), obecnie 0% efektywność. Fix scope ~30 min (edit +
test + deploy shadow → monitor → flip).

**Test:** `pytest tests/test_v326_step3_r09.py -v` (ma fixture używającą
`common.V326_WAVE_VETO_KM_THRESHOLD`). Upewnij się że test mock'uje
haversine lub używa real function call.

**Blast radius:** dispatch-shadow (primary) + żaden other service (R-09 lives
w dispatch_pipeline). Restart: `systemctl restart dispatch-shadow` (ACK Adrian).

#### V3.26-SMOKE-TEST-T5-REGRESSION — 5 failures w smoke_telegram_buttons_freetext
Po Run 2026-04-24 ~20:30 UTC `python3 tests/smoke_telegram_buttons_freetext.py`:
- **5/~40 FAIL** (T#5 "max 3 przyciski" + ASSIGN callback format)
- **9/9 PASS** w T#6 `test_parse_known_names` (broader coverage, unrelated)

Fail cases:
```
t1='✅ Marek 10min'          (T#5 Case 1 — button label prefix check)
t2='✅ Grzegorz 20min'       (T#5 Case 1)
cb1=ASSIGN:466700:207:10     (T#5 Case 7 — callback format)
cb2=ASSIGN:466700:289:20     (T#5 Case 7)
valid cand → ASSIGN:X:207:12 (T#5 Case 6 — valid cand)
```

Hipoteza robocza: regression od commit **2271810** `v326-hotfix-button-label-2026-04-24`
(button label formula alignment z compute_assign_time, max(travel, prep)).
Test expected stale format, prod zmieniony po hotfix.

**Alternatywa:** callback format mogł się zmienić w a93d1c4 (v326 parser hotfix
`(cid=N)` format) — check git log -p dla test scenarios.

**Priority:** medium — prod działa (hotfix LIVE), tylko test out of sync.
Fix: diff prod `_format_assign_label` + `_build_callback_data` vs test
expected, update test fixtures. ~30 min. Blocks: commit test backfill
`smoke_telegram_buttons_freetext.py` diff z `test_parse_known_names` (9 cases
PASS ale commit blocked bo overall FAIL). Odłożone do jutra 2026-04-25.

**Test backfill dyskusja:** mój diff `test_parse_known_names` jest SAFE
(oczywiście PASS), commit blocked tylko pre-existing FAIL w innych testach.
Opcja: commit test backfill + osobny ticket T#5 fix. Opcja lepsza: fix T#5
+ commit cały clean file naraz. Wybrać jutro.

#### V3.25-DOT-VERIFY-SMOKE — empirical dot-normalization end-to-end (pending 25.04 evening)
Post-V3.25 dot removal z kurier_ids.json + kurier_piny.json (tylko "Bartek O.",
"Michał K." → dotless). Parser normalization via `rstrip(".,;:")` teoretycznie
handles user input z kropką, ale NIE zweryfikowane empirically.

**Test plan (2026-04-25 evening):**
1. Adrian → @NadajeszBot: "bartek o nie pracuje" — expected: exclude Bartek O.
   (cid=123), confirm `(cid=123)`.
2. Adrian → @NadajeszBot: "bartek o. nie pracuje" (Z KROPKĄ) — expected: exclude
   SAME Bartek O. (cid=123), normalized match.
3. Adrian → @NadajeszBot: "michał k pauza" — expected: pause Michał K. (cid=393).
4. Adrian → @NadajeszBot: "michał k. pauza" (Z KROPKĄ) — expected: pause SAME
   Michał K. (cid=393).

**Dependency:** dispatch-telegram.service restart (natural redeploy albo
Adrian ACK po fix innego ticketu). Do restart parser ma stale cache z pre-dot
removal (courier_names dict loaded at startup), ale rstrip powinien fire
nawet ze stale cache bo normalization przed lookup.

**If FAIL:** rollback `cp kurier_ids.json.bak-pre-dot-removal-2026-04-24
kurier_ids.json` + piny + naprawić parser edge case. Reversal scope: git revert
5 commits Daily Accounting bundle.

**Priority:** HIGH (pre-condition for any future courier name change).
Scope: 10 min live test + 15 min rollback jeśli FAIL.

#### V3.26-PANEL-PARSER-DOT-AUDIT — verify parse_panel_html normalizes courier names
Panel NadajeSz wysyła kurier names w HTML (kolumna "Kurier" w ticket view).
Jeśli panel wyświetla "Bartek O." z kropką, `panel_client.parse_panel_html`
musi match na `kurier_ids.json` keys ("Bartek O" bez kropki). Jeśli match
jest exact-string, bez rstrip normalization — panel_watcher nie będzie
emit COURIER_ASSIGNED dla Bartek O./Michał K. until panel UI zmieni format.

**Akcja:** grep `parse_panel_html` + callers, verify normalization layer
(strip/rstrip/lower przed dict lookup). Jeśli exact match → dodać normalize
wrapper + unit test.

**Priority:** MEDIUM — ryzyko silent breakage dla 2 kurierów. Scope: ~45 min
audit + optional fix. Powiązane z V3.25-DOT-VERIFY-SMOKE.

#### V326-C1-SOLO-FALLBACK — shift_start/shift_end missing w solo_fallback call (CRITICAL) → **FIXED 2026-04-25**
**STATUS:** FIXED in commit `bb74bfe` + tag `v326-hotfix-c1-solo-fallback-2026-04-25`
(sprint 2026-04-25 late-night). Deployment pending dispatch-shadow restart Block 4.

**Bug:** `dispatch_pipeline.py:1599-1605` solo_fallback wywoływał `check_feasibility_v2`
**bez** `shift_start=`/`shift_end=` kwargs. Z `ENABLE_V325_SCHEDULE_HARDENING=True`
(live od 23.04) funkcja hardening path (feasibility_v2.py:302) zwraca
`NO + v325_NO_ACTIVE_SHIFT (cs.shift_end=None — brak schedule mapping)` dla
KAŻDEGO candidate w fallback → `solo_best=None` → KOORD override na każde
fallback call. Efektywnie 100% fallback → manual assign.

**Fix:** dodano 2 linie (L1603-1604):
```python
shift_end=getattr(cs, "shift_end", None),
shift_start=getattr(cs, "shift_start", None),
```
Wzorzec identyczny z main call site L910-911. `cs` już w scope (pętla L1594).

**Test:** `tests/test_c1_solo_fallback_shift_params.py` — AST guard który parsuje
dispatch_pipeline.py i asserts że wszystkie call sites `check_feasibility_v2`
mają oba kwargi. Regression guard dla przyszłych refactorów. PASS.

**Live verify post-restart Block 4:**
- journalctl -u dispatch-shadow grep `NO_ACTIVE_SHIFT` — rate powinien spaść
  (przed: każdy fallback, po: tylko candidates z real brakiem schedule mapping)
- journalctl -u dispatch-shadow grep `solo_fallback` — fires przy real need,
  solo_best assigned zamiast None

**Scope discovery:** Bug znaleziony cross-review 2026-04-25 (Gemini 3.5 Pro +
Deepseek arbiter). Nie był w TECH_DEBT pre-sprintu — nowy finding B#C1.

**Blast radius:** dispatch-shadow (primary). Brak interakcji z telegram/panel-watcher.

#### V326-H1-SERIALIZER-DROPS — 14+ kluczy v325/v326 droppowane do learning_log → **FIXED 2026-04-25**
**STATUS:** FIXED in commit `7dee94a` + tag `v326-fix-h1-serializer-2026-04-25`
(sprint 2026-04-25 sobota Block 1). Deployed dispatch-shadow restart 07:14 UTC.

**Bug:** `shadow_dispatcher._serialize_candidate` (LOCATION A — alts) +
`_serialize_result.best` (LOCATION B — best) trzymały **hardcoded explicit
key list** dla output dict. Pipeline regularnie dodaje nowe v325_/v326_ keys
do `cand.metrics` (np. v325_reject_reason w feasibility_v2:301, v326_speed_*
w dispatch_pipeline:304-306, v326_fleet_* w :252-254), ale serializer nigdy
ich nie propagował. Cross-review B#H1.

**Lista zgubionych kluczy (14):** v325_pickup_ref_source, v325_reject_reason,
v325_pickup_post_shift_excess_min, v325_pre_shift_soft_penalty,
v325_pre_shift_too_early_min, v325_new_courier_penalty,
v325_new_courier_advantage, v325_new_courier_flag, v326_fleet_bag_avg,
v326_fleet_load_delta, v326_fleet_load_adjustment, v326_speed_tier_used,
v326_speed_multiplier, v326_speed_score_adjustment.

**Fix (~30 lines):** helper `_propagate_prefixed_metrics(base, metrics)` w
shadow_dispatcher.py iteruje po `metrics.items()` i dodaje keys z prefiksami
`("v325_", "v326_", "v319_", "r07_", "bonus_", "rule_")` które NIE są
already w `base`. Wywoływany w obu locations po dict literal.

**Existing explicit fields TAKE PRECEDENCE** — auto-prop pomija `if k in base`,
nie nadpisuje hardcoded values.

**Test:** `tests/test_h1_serializer_propagation.py` 4/4 PASS — propagation,
unknown prefix not propagated, explicit field precedence, None metrics handled.

**Live verify post-restart:** confirmed dispatch-shadow restart 07:14 UTC
healthy (0 errors, 0 V326_WAVE_VETO compute fail, memory 13.5M). Empirical
v325/v326 keys propagation pending pierwszy NEW_ORDER event (Saturday morning
low traffic — last decision 24.04 21:28). Unit test confirms logic;
post-deploy entry expected w shadow_decisions.jsonl po pierwszej decision.

**Blast radius:** dispatch-shadow (primary). Zero decision change — wyłącznie
obserwowalność (learning_log entries dostają więcej kluczy).

#### V326-BUG-A-ANCHOR-SCORING — km_to_pickup chronological-last-drop misleading → **FIXED 2026-04-25 (LIVE flip 14:20)**
**STATUS:** FIXED in commits `c1020ef` (decay 3→5) + `3b93bf3` (7-component complete) +
`291b5a3` (flag flip True). Tag `v326-fix-bug-a-anchor-complete-2026-04-25`.
**Bug:** `dispatch_pipeline.py:973` `km_to_pickup_haversine = haversine(plan.sequence[-1].drop, new_pickup) * 1.37`
— chronological last drop ≠ insertion anchor. Adrian's #468404 case: km_to_pickup=15.91km z Plac
Brodowicza (Choroszcz, far) NIE z Doner Kebab (anchor). Plus rationale display `-km*5` heuristic
mylił operatorów (-79 dla 15.91km) gdy real impact ~0.15pt.
**Fix:** 7 components — insertion_anchor.py NEW module + dispatch_pipeline.py distance source +
DIST_DECAY_KM 3→5 + rationale (s_dystans actual contribution loss vs ideal) + Telegram label
"X km do {anchor_restaurant}" + flag-gated + tests (8 unit tests insertion_anchor + 6 dist_decay).
**LIVE post-flip 14:20:** anchor_used=True 4/5 fresh proposals, bliskosc rationale -19.95 dla km≈4.3.

#### V326-BUG-B-EVENT-BUS-EVENT-TYPES — V3.19g1 incomplete deployment → **FIXED 2026-04-25 (LIVE)**
**STATUS:** FIXED in commit `615b60e`. Tag `v326-fix-bug-b-event-bus-2026-04-25`.
**Bug:** `event_bus.py:21` EVENT_TYPES set NIE zawierał "CZAS_KURIERA_UPDATED". panel_watcher
emituje event (V3.19g1), state_machine ma handler (line 316), ALE event_bus.py:76 raises
ValueError "Nieznany event_type" → state_machine NIGDY dostał update → orders_state stale czas_kuriera.
**Evidence pre-fix:** watcher.log 2026-04-24 11:49+ 10× consecutive errors.
**Fix:** 1-line addition do EVENT_TYPES set. Trivial.
**LIVE post-restart 13:20:** zero "Nieznany event_type" errors.

#### V326-BUG-C-PO-DRODZE-STRICT — geometric-only check, missing time + intervening → **FIXED 2026-04-25 (LIVE flip 14:20)**
**STATUS:** FIXED in commit `34ff770` + flip `291b5a3`. Tag `v326-fix-bug-c-po-drodze-strict-2026-04-25`.
**Bug:** `dispatch_pipeline.py:850` `bundle_level3 = dev<2.0km` (geometric only). Adrian #468461
case: Maison 1.02km od Sweet Fit fires "po drodze" mimo 33min apart + 2 intervening stops.
**Fix:** flag-gated strict mode dodaje time proximity (±10min) + intervening_stops_count=0 (gdy
plan + anchor available). Configurable thresholds w common.py: PO_DRODZE_DIST_KM,
PO_DRODZE_TIME_DIFF_MIN, PO_DRODZE_MAX_INTERVENING.

#### V326-BUG-D-PO-ODBIORZE-ANCHOR — first geographic match → anchor-based → **FIXED 2026-04-25 (LIVE flip 14:20)**
**STATUS:** FIXED in commit `b17bd36` + flip `291b5a3`. Tag `v326-fix-bug-d-anchor-2026-04-25`.
**Bug:** `dispatch_pipeline.py:824` bundle_level2 iteruje po bag_raw, pierwszy geographic match.
NON-deterministic. Adrian #468404: "po odbiorze z Maison +1.02km" mylące bo Maison to chronologically
3rd pickup w plan, NIE poprzedni.
**Fix:** anchor-based via insertion_anchor module (Bug A reuse). Flag-gated.
**LIVE:** insertion_idx=0 cases → bundle_level2 cleared (zachowuje "no message" UX).

#### V326-VENV-DISPATCH-SETUP — dedicated venv dla dispatch_v2 + ortools → **DONE 2026-04-25**
**STATUS:** DONE in commit `d20ff90`. Tag `v326-venv-dispatch-setup-2026-04-25`.
Adrian's strategic decision (Opcja B): long-term clean dependency isolation zamiast
`--break-system-packages` debt. Created `/root/.openclaw/venvs/dispatch/` Python 3.12.3 + ortools
9.15.6755 + numpy/pandas/protobuf etc. Migrated 7 systemd units (dispatch-shadow, panel-watcher,
telegram, czasowka, gps, plan-recheck, sla-tracker) z `/usr/bin/python3` na venv interpreter.
Backupy unit files w `systemd_backups_2026-04-25/`. Pinned versions w `requirements-dispatch-venv.txt`.
**LIVE:** restart 13:20 confirms venv loaded, 7/7 dispatch_v2 modules import OK.

#### V326-FIX-6-OR-TOOLS-TSP — replace bruteforce + greedy z OR-Tools → **FIXED 2026-04-25 (LIVE flip 14:20, latency tune 14:44)**
**STATUS:** FIXED in commits `0902728` (module + integration) + `fb11fcc` (flag flip) +
`5623d39` (latency tune 200→50ms). Tags `v326-fix-tsp-or-tools-2026-04-25`,
`v326-fix-or-tools-latency-regression-2026-04-25`.
**Bug:** greedy zigzag pattern dla bag>3 (Adrian #468404: 3 zigzags centrum↔Antoniuk).
**Fix:** NEW tsp_solver.py module z OR-Tools constraint programming. Pickup-and-delivery problem
z time-bounded 50ms search per kandydat. Fallback do greedy gdy INFEASIBLE. Strategy field "ortools".
**Latency observation:** post-flip 200ms × 10 candidates = 2000ms regression. Tune 50ms → ~580ms p95
acceptable. Sequential per-candidate (parallel execution defer dla future optimization).

#### V326-FIX-7-SAME-RESTAURANT-GROUPING — pre-pass przed TSP → **FIXED 2026-04-25 (LIVE flip 14:20)**
**STATUS:** FIXED in commits `dd642ea` + flip `fb11fcc`. Tag
`v326-fix-same-restaurant-grouping-2026-04-25`.
**Bug:** 2 ordery same restaurant (np. Doner Kebab 468401+468402) z compatible czas/quadrant
nie były groupowane przed TSP → 2 osobne pickup runs (zigzag). Adrian's #468404 NIE jest grupowany
(24min apart + distant) — confirms NIE false positive grouping.
**Fix:** NEW same_restaurant_grouper.py (~190 lines) z group_orders_by_restaurant + greedy partial
grouping dla 3+ orders. Integration w simulate_bag_route_v2 (super-pickup nodes z group_oids
attribute). _simulate_sequence super-pickup zapisuje pickup_at[oid] dla wszystkich w grupie z
single DWELL_PICKUP_MIN.

#### V326-H2-R06-BAG1-FIX — R-06 trajectory blocked dla bag=1 → **FIXED 2026-04-25 (shadow)**
**STATUS:** FIXED (shadow) in commit `74e9f80` + tag
`v326-fix-h2-r06-bag1-shadow-2026-04-25` (sprint 2026-04-25 sobota Block 2).
**Flag default False — flip pending 24h shadow obs.**

**Bug:** `dispatch_pipeline.py:158` (post-fix line 164) hardcoded
`if bag_size < 2 or pos_source == "no_gps":` w `_v326_multistop_trajectory`.
Komentarz "R-06 multi-stop fires tylko gdy chain effect, bag=1 nie ma
'ostatniego' dropu" był **semantycznie błędny** — bag=1 MA last drop, tylko
bag=0 nie ma. Cross-review A#2.1.

**Impact:** 30-50% candidates z bag=1 NIGDY nie dostają R-06 trajectory bonus.
Single-bag couriers near restaurant z chain-trajectory potential wykluczeni
od bonus optimization.

**Fix (flag-gated):** dodano `ENABLE_V326_R06_BAG1_FIX` w common.py
(default False). Threshold `_r06_min_bag = 1 if FLAG else 2`,
`if bag_size < _r06_min_bag:` zamiast `< 2`. Default behavior IDENTYCZNE
pre-fix (bag<2 skip) → zero shadow disruption do flipu.

**Plan Adriana proponował semantyczny variant (`<=2` z threshold=0/2)** który
zmieniłby behavior dla bag=2 (`2<=2` → skip vs original `2<2` → pass).
Refactored na `<` z `_r06_min_bag=1/2` żeby zachować pre-fix semantykę dla
bag>=2. Udokumentowane w commit message.

**Live verify post-restart:** confirmed shadow restart 07:14 UTC healthy.
Flag False default, behavior identyczne pre-fix. **Action required jutro:**
flip flag True po 24h shadow obs (oczekiwany +30-50% R-06 fire rate dla bag=1).

**Test:** brak nowego dedykowanego testu (flag False = identical behavior,
runtime AST guard tracking unchanged). H1 + C1 regression PASS post-edit.

**Blast radius:** dispatch-shadow (primary) gdy flag=True. Default=False:
zero impact.

#### V326-C2-TZ-DEFENSIVE-CLEANUP — LOW (not firing, verified 2026-04-25)
**Klasa:** LOW (code quality, NOT active bug).

**Scope:** ~40 wystąpień `replace(tzinfo=timezone.utc)` w 14 plikach (grep full
`dispatch_v2/*.py` 2026-04-25). Największe clusters:
- `dispatch_pipeline.py` 10 miejsc
- `feasibility_v2.py` 8 miejsc (w tym :304/:318/:340 flaggowane przez cross-review
  arbiter jako CRITICAL)
- `route_simulator_v2.py` 6, `telegram_approver.py` 6, `shadow_dispatcher.py` 4,
  `chain_eta.py` 4, inne po 1-2

**Bug fires ONLY dla Warsaw local naive** (`czas_odbioru_timestamp`, `czas_kuriera`,
`shift_end`). Dla UTC naive (`datetime.utcnow()` / parsed ISO UTC bez Z tag)
→ `replace(tzinfo=timezone.utc)` jest CORRECT.

**Verification 2026-04-25 STEP 3A (sprint):**
- `courier_resolver.py:_shift_end_dt` (L542-558) buduje z `datetime.now(WAW)` +
  `.replace(hour=..., minute=...)` — `.replace(hour=...)` NIE niszczy tzinfo.
  Zwracany `shift_end` jest **AWARE Warsaw** na każdej path.
- Identycznie `_shift_start_dt` (L525-539).
- Defensive code w `feasibility_v2.py:304/318/340` + `dispatch_pipeline.py:924`
  `shift_end.replace(tzinfo=timezone.utc) if shift_end.tzinfo is None else shift_end`
  — branch `is None` NIGDY nie fire live → używa `else shift_end` as-is Warsaw
  aware → Python auto-converts przy porównaniu z UTC → porównania poprawne.
- **Bug NIE fires na prod.** Defensive code redundantny ale KOREKT.

**Cross-review context:** Gemini 3.5 Pro + Deepseek arbiter oznaczyli C2 jako
CRITICAL z confidence HIGH, ale arbiter sam napisał "verification wymaga
courier_resolver.py którego nie udostępniono". Sprint prompt traktował jako
CRITICAL bez respect tego zastrzeżenia. **Sytuacja opisana w Lekcji #19.**

**Rekomendacja:** per-plik audit w V3.28+ sprint (nie hotfix). Fix wzorzec:
```python
if var.tzinfo is None:
    _var = var.replace(tzinfo=C.WARSAW).astimezone(timezone.utc)
else:
    _var = var.astimezone(timezone.utc)
```
Per-miejsce analysis WYMAGANA (które `var` to Warsaw naive vs UTC naive) —
blind replace-all zniszczy paths gdzie UTC-tagging był correct.

**Priority:** LOW. Zero operational impact (bug nie fires). Cleanup opportunity
przy większym refactoringu TZ handling.

#### V326-SLA-TRACKER-TZ-PICKED-DT — MEDIUM (real bug, service stopped)
**Klasa:** MEDIUM (service degraded, R6 bag_time alerts off od 24.04 20:36 UTC).

**Plik:** `sla_tracker.py:95` (docstring confirms bug istnieje) + `:177`
`picked_dt = picked_dt.replace(tzinfo=timezone.utc)`. `picked_dt` pochodzi
z panelu (Warsaw local naive, `czas_odbioru_timestamp`) → tagging UTC =
+2h offset w CEST. Prawdopodobne źródło `"can't subtract offset-naive and
offset-aware datetimes"` crashu (service stopped 20:36 UTC 24.04).

**NOT FIXED 2026-04-25 sprint** — explicit Adrian D2(a) decision:
- Wymaga decyzji biznesowej: czy R6 bag_time alerts w ogóle potrzebne
  (5 dni bez alertu, nikt nie zauważył = sygnał że feature może być do kill)
- Restart service po 24h+ stopped = nieprzewidywalne side effects
- Adrian = zmęczony, decyzja wymaga świeżego mózgu
- `picked_dt` fix ≠ complete sla-tracker fix — trzeba sprawdzić czy są inne
  anti-patterny (grep całego sla_tracker.py przed commit resources)

**Scope osobnej sesji:**
1. Decision: fix OR kill feature?
2. Jeśli fix: grep całego sla_tracker.py dla innych Warsaw-naive paths
3. Fix + unit test (naive→Warsaw→UTC conversion)
4. Restart dispatch-sla-tracker.service
5. 24h shadow verify (R6 alerts fire sensibly, no new crash errors)

**Priority:** MEDIUM (service degraded ale no operational impact — panel
coverage manual przez Adriana/koordynatorów). Kill-or-fix decision owned
by Adrian.

#### V3.19g — przedłużenia czas_kuriera trigger plan invalidation (deferred)
Gdy panel zmienia `czas_kuriera` po COURIER_ASSIGNED (np. coordinator "+15min"
button), courier_plans.json saved plan dla danego cid może mieć stale predicted
times. V3.19f zapisuje update przy kolejnym COURIER_ASSIGNED emit, ale plan nie
jest invalidated reactively. Full handling wymaga analizy:
- V3.19b plan_manager write hooks (invalidate_plan gdy pickup_ready zmienione?)
- V3.19d sticky sequence race conditions (re-run simulator gdy pickup_ready shift)
- Koszt implementacji 3-4h + regression risk na V3.19b/d stack.
**Priority:** low. Podnieść gdy V3.19f stable 2 tyg + metric pokazuje potrzebę.

### V3.19e + V3.19f LIVE w shadow mode flag=True (2026-04-20 20:08 UTC)
- `ENABLE_V319E_PRE_PICKUP_BAG=True` default (commit 4676b8c + tag v319ef-shadow-flip-live)
- `ENABLE_CZAS_KURIERA_PROPAGATION=True` default (same commit)
- Dispatch-shadow + panel-watcher PID post-flip: 2015775 / 2015777
- Dispatch-telegram NIE restartowany (off-air, koordynacja ręczna)
- Pierwsza real propozycja post-flip: oid=467526 @ 20:12:07, wszystkie 3 nowe
  klucze (v319e_r1_prime_hypothetical + czas_kuriera_warsaw + czas_kuriera_hhmm)
  OBECNE w serialized best. Zero errors.
- Real traffic side-by-side NIE UKOŃCZONE (low volume post-peak). Planowane
  jutro lunch peak 11-14 Warsaw per `/tmp/v319ef_v319g_jutro_handover.md`.

### V3.19g BAG cap discovery DONE (2026-04-20)
- 6-mo dataset `/root/v319g_dataset/*.csv`, 44,315 → 40,790 normalized rows, 42 couriers.
- Gold tier identified: Bartek O. / Mateusz O / Krystian / Gabriel (OPW_p90≥4).
- Raport: `/tmp/v319g_bag_cap_discovery.md` (301 linii).
- Preview: `/tmp/v319g_courier_tiers_preview.json` (37 eligible).
- **Design + impl PENDING** — jutrzejsza sesja (po side-by-side V3.19e/f).

### Outstanding tickets post-dzień-dzisiejszy
- **APK GPS** (MEDIUM, user: "na razie działa, nie ruszamy"). AndroidManifest ma
  defensive fixes, 4/8 kurierów działa; 4/8 bez GPS. Deferred — nie blokuje V3.19e/f.
- **Silent flags** — 1 renamed do `_PLANNED` (2026-04-20), pozostałe 3 OK
  (`ENABLE_TRANSPARENCY_SCORING`, `ENABLE_BUNDLE_VALUE_SCORING`, `ENABLE_PANEL_IS_FREE_AUTHORITATIVE`).
- **639 delivered bez delivery_coords** (30% historical). Fix: geocoding retry
  w state_machine + backfill script. Priority: low.
- **46 delivered bez delivered_at** — data integrity, fallback to updated_at
  na readerach. Priority: low.
- **V3.21 wave_scoring flip** — blocked na V3.19e/f production stable + BAG cap tiering.

### V3.19h 3 flags LIVE (2026-04-20 23:53 shadow → 2026-04-21 flip)

**Status update 2026-04-21:** 3 flags (BUG-1/2/4) flipped to True default
(commit 08de9fa). Live od 2026-04-20 22:30 UTC.

**Audit completed 2026-04-21** (replay-based on 6-mo CSV, 44k orders):
- **Stage 1** (~19:20 UTC): name resolution fix + feasibility gate fix
  (/tmp/v319h_audit/*.bak-pre-s1). Match top-1=4.79%, top-5=18.96%.
- **Stage 2 EXTREME** (~20:30 UTC per Adrian ACK): R4 bundle + R1/R5/R8
  adaptive + V3.19f pickup ladder. TSP/V3.19e SKIP (świadoma decyzja —
  TSP w audit historycznym = artificial scenario, prod V3.19d plans nie
  są w dataset).
- Dashboard: `/tmp/v319h_audit/dashboard.html` (exploratory Q&A tool)
- Exec summary: `/tmp/v319h_audit/EXEC_SUMMARY.md`
- **Decyzja produkcyjna:** HOLD V3.19h live. Replay fidelity bias
  (cold-start bag=0 candidates dominate bez pełnej TSP integracji)
  uniemożliwia produkcyjny go/no-go signal z tego audytu. Kolejny
  audit z live `shadow_decisions.jsonl` danymi sugerowany w 2 tyg.

### V3.19h shadow deploy (historical)

3 MVP implementations w shadow mode z `dispatch-shadow` restart
(panel-watcher nietknięty od 2026-04-20 17:17, dispatch-telegram
nietknięty od 2026-04-19 16:19).

| Bug | Commit | Tag | Flag default | Tests |
|---|---|---|---|---|
| BUG-4 tier×pora cap matrix | 4d1b609 | v319h-bug4-tier-cap-matrix-impl | ENABLE_V319H_BUG4_TIER_CAP_MATRIX=False | 49 (30+19) |
| BUG-1 SR × drop_proximity_factor | 5fe81fe | v319h-bug1-drop-proximity-impl | ENABLE_V319H_BUG1_DROP_PROXIMITY_FACTOR=False | 50 (32+18) |
| BUG-2 wave continuation bonus | a65bfb3 | v319h-bug2-wave-continuation-impl | ENABLE_V319H_BUG2_WAVE_CONTINUATION=False | 23 |

**Shadow deploy tag:** v319h-3bugs-shadow-deploy (smoke test green 2026-04-20 23:58 UTC).

**Zero behavior change przy deploy** — wszystkie 3 flagi False default.
Flip planowany na jutrzejszy lunch peak side-by-side 11-14 Warsaw 2026-04-21.

**7 nowych pól serializowanych:**
- BUG-4: `v319h_bug4_tier_cap_used`, `v319h_bug4_cap_violation`, `bonus_bug4_cap_soft`
- BUG-1: `v319h_bug1_drop_proximity_factor`, `v319h_bug1_sr_bundle_adjusted`
- BUG-2: `v319h_bug2_interleave_gap_min`, `v319h_bug2_continuation_bonus`

**Generated artifacts:**
- `dispatch_state/courier_tiers.json` (43 couriers, Gabriel cap_override per ACK)
- `dispatch_v2/districts_data.py` (28 osiedli Białegostoku + 4 outside-city)
- `dispatch_v2/build_v319h_courier_tiers.py` (one-off tier regenerator)

**Regression baseline:** 644 asserts PASS w 39 plikach (522 pre-V3.19h + 122 new).

### Session closures 2026-04-21

- **Albert Dec mapping:** PIN 8770 → cid=414 (kurier_piny.json updated,
  confirmed w shadow dispatcher SHADOW PROPOSE best=414 multiple events
  14:41-17:52 UTC). Courier-api auth logs empty 12h (APK possibly
  offline, not blocking).
- **Parser free-text disabled:** `ENABLE_TELEGRAM_FREETEXT_ASSIGN=0`
  default (commit 82b96f7). OPERATOR_COMMENT logging code present
  (`telegram_approver.py` × 5 occurrences). 0 entries w
  `learning_log.jsonl` since flip — Bartek nie pisał free-text w 12h,
  parser fix NOT_TESTED w realnych warunkach (brak event, nie fail).
- **V3.19g1 hotfix:** live (commit 16cf921 — removed local import of
  normalize_order in _diff_and_emit, unblocks shadow log).
- **Lekcje sesja:**
  - Python local import shadow globals (feedback_python_local_import_shadow.md)
  - CC overnight audit pivot do reduced-fidelity acceptable z honest caveats
  - CSV-based replay dla 6-mo ≠ production-grade audit (brak
    live shadow_decisions, TSP plans, courier_plans.json snapshots)

## V3.25 Sprint — 4 CRITICAL (23.04.2026, ~7h)

Z Q&A session 22.04. Pełen plik reguł (gdy Adrian upload):
`/tmp/v324_qa_rules_extracted_2026-04-22.md`.

### R-01 SCHEDULE-HARDENING (2h) — CRITICAL

V3.24-A niedeterminizm: cid bez mapping pass-through, dropoff >
shift_end+5min soft, pickup post-shift czasem przechodzi.

**Fix:** unconditional PRE-CHECK w `feasibility_v2.py`:
- cid not in kurier_ids.json → HARD REJECT
- No active shift → HARD REJECT
- Pickup < shift_start - 30min → HARD REJECT (PRE_SHIFT_BEYOND_TOLERANCE)
- Dropoff > shift_end + 5min → HARD REJECT (DROPOFF_POST_SHIFT)
- Pickup > shift_end → HARD REJECT (PICKUP_POST_SHIFT)

**Flag:** `ENABLE_V325_SCHEDULE_HARDENING=False` → shadow 30min → flip.

**Rollback:** flag False + restart dispatch-shadow.

### R-02 COURIER-SYNC + DISTRICTS-SCRAPE (2.5h) — CRITICAL

**Courier sync (3 nowi):**
- cid=522 = **Szymon Sadowski** (potwierdzony Q&A — NIE Grzegorz Rogowski
  jak CC Faza A błędnie zmapował, lesson QA-11)
- Kuba Olchowik (cid TBD — panel scrape)
- Grzegorz Rogowski (cid TBD — panel scrape)

**Tier changes:**
- Kuba OL (370) → Standard+ (z Standard)
- Krystian (61) → inactive=True (permanent OFF)

**Districts:** scrape http://www.info.bialystok.pl/osiedla/N/obiekt.php
N=1..28, diff z `districts_data.py`, update jeśli diff.

**Files affected:** kurier_ids.json, kurier_piny.json, courier_tiers.json,
schedule_utils.PANEL_TO_SCHEDULE, districts_data.py

**Rollback:** git revert + restart dispatch-shadow.

### R-03 TELEGRAM-OPS-PARSER (2h) — CRITICAL

**New file:** `telegram_ops_parser.py` + `/etc/systemd/system/dispatch-telegram-ops.{service,timer}`
(1 min tick).

**Komendy na grupie -5149910559:**
- `/zwolnij <cid>` — permanent exclude (manual_overrides_excluded.json)
- `/zostaje <cid> <hh:mm>` — dynamic shift extension (manual_overrides_extended.json)
- `/wraca <cid>` — zdjęcie blacklist/pauzy
- `/pauza <cid> <min>` — temporary pause (manual_overrides_paused.json)

**Auth:** only AUTHORIZED_OPS = [Adrian_telegram_id, Bartek_telegram_id]

**Integration:** `feasibility_v2.py` reads 3 override files PRE schedule check.

**Albert Dec migration:** wywal `COURIER_414_BLACKLIST_UNTIL` z quick patch,
zastąp wpisem w manual_overrides_excluded.json.

**Rollback:** `systemctl disable --now dispatch-telegram-ops.timer` +
git revert.

### R-04 NEW-COURIER-CAP gradient (0.5h) — CRITICAL

**Fix:** gradient penalty w `scoring.py` post-base-score:
- tier != "new" → 0
- bag_size >= 2 → -9999 (HARD SKIP)
- advantage >= 50 → -10
- advantage 20-50 → -30
- advantage < 20 → -50

**Flag:** `ENABLE_V325_NEW_COURIER_CAP=False` → shadow → flip.

**Rollback:** flag False.

---

## V3.26 Backlog — 7 HIGH (28-31.04, ~28h)

- R-05 SPEED-MULTIPLIER (6-10h, backtest 40k dataset)
- R-06 MULTI-STOP-TRAJECTORY (4-6h)
- R-07 PICKUP-COLLISION-CHECK (3-4h)
- R-08 PICKUP-EXTENSION-NEGOTIATION (5-6h, + Adrian tolerance table)
- R-09 WAVE-CONTINUATION-GEOMETRIC-VETO (2h)
- R-10 FLEET-LOAD-BALANCING (3h)
- R-11 TRANSPARENCY-DECISION-RATIONALE (4h)

## V3.27+ Backlog — 7 MEDIUM (maj)

R-12 restaurant-holding-detection, R-13 dedicated-courier,
R-14 natural-wave-continuation, R-15 match-source-attribution,
R-16 recent-delivery-decrement, R-17 tier-dynamic-assignment,
R-18 districts-complete-sync

## LOW Backlog (po Q4)

R-19 late-evening-simple-mode, R-20 post-wave-pos-downgrade,
R-21 extended-shift-awareness

---

## Success metrics V3.25 → V3.26 → V3.27

- Baseline post V3.19h: PANEL_OVERRIDE 81%
- **Post V3.25 cel:** <60% (4/10 Q&A cases resolved)
- **Post V3.26 cel:** <16% (8/10 Q&A cases resolved)
- **Post V3.27 cel:** <10% + wysoki trust

---

## 2026-04-22 — V3.19h live data analysis (C2/C3 validation)

Post-peak validation sesja. 26h live data (21.04 08:55 → 22.04 15:01 UTC).
Dane źródłowe: `scripts/logs/shadow_decisions.jsonl` (N=272 post-flip PROPOSE
effective) + `dispatch_state/learning_log.jsonl` (N=446 entries, 262
semi-strict outcomes). Methodology semi-strict (TIMEOUT_SUPERSEDED rozwiązany
przez orders_state proposed-vs-actual). Raporty:
- `/tmp/v319h_c2_clean_rates_2026-04-22.md` (clean rates + per-bug isolation)
- `/tmp/v319h_c3_quick_findings_2026-04-22.md` (over-promote + neg score + BUG-4 sub)

### ✅ V3.19h LIVE 21-22.04 → NIE rollback

Override rate post-flip **81.30%** (213/262) vs baseline-mixed (14-20.04)
**89.19%** (883/990). **+8pp improvement**, nie regresja.

Absolute 81% > target <25% jest **strukturalne** — workflow coordinator
bypassuje Telegram (TAK explicit=0, ASSIGN_DIRECT=2, w >95% cases silent
panel assign przed SLA timeout). Target <25% nieosiągalny via V3.19h alone;
wymaga osobnej inicjatywy (operator UX tool albo re-definicja metryki).

**Decyzja:** V3.19h flags stay True (BUG-1/2/4 default=True). Sample
n=259 effective. Zero modyfikacji produkcji z C2/C3 wniosków.

### 🟡 V3.19j-BUG2-MAGNITUDE — PRIORITY #1 (confirmatory signal)

C2 per-bug isolation: **BUG-2 fired (N=197) override rate 82.7% vs not_fired
(N=65) 76.9% → Δ +5.8pp**. Binary +30 bonus za szeroko rozdany — gradient
tabela per Adrian Q&A 22.04 (już w spec wyżej w tym pliku).

**Działania (bez zmian z poprzedniej definicji ticketu):**
- Implementacja `bug2_wave_continuation_bonus(gap_min)` gradient table
- Audit re-run z nowym bonus, expected BUG-2 fires drop 13% → 5-8%
- Top R4 klastry score breakdown rebalanced
- **WALIDACJA Z BARTKIEM przed implementation**

**Est:** 4-6h. **Blocking:** brak. **Status:** top priority post-V3.24.

### 🟡 V3.19j-BUG4-MAGNITUDE — NEW MEDIUM

C3-Q3 sub-isolation schema correction (cap_violation = **int** 0/1/2, nie bool):
**cap_violation > 0 (N=20) override rate 90.0% vs cap_violation == 0 (N=228)
83.3% → Δ +6.7pp**. V3.19h **correctly identifies overload** ale
`bonus_bug4_cap_soft` penalty magnitude niewystarczający — kurier z violation
dalej wygrywa scoring.

Tier×pora distribution (shadow, N=247 non-cold):
- `std/peak/4`: 107 (43%)
- `std/normal/3`: 85 (34%)
- `std+/peak/5` + `std+/normal/4`: 31 (13%)
- `gold/*`: 10 (4%) ← tylko 2 `gold/peak/6`
- `std/peak/3` + `std/off_peak/2`: 16

**Propozycja:** gradient penalty based on cap_violation count:
- violation=1: `-30` pkt (obecny range ~<-20)
- violation=2: `-50` pkt
- violation≥3: `-80` pkt (hard signal)

**Est:** 3-4h (function change w common + tests + audit re-run).
**Blocking:** brak; sekwencyjnie po V3.19j-BUG2-MAGNITUDE.

### 🟡 V3.19k-SCORE-FLOOR — NEW MEDIUM

C3-Q2 finding: **80/274 = 29.2% propozycji post-flip z score < 0** (threshold
acceptable noise = 5%). Top 5 worst scores:

| # | oid | score | proposed | actual | pos_source |
|---|---|---|---|---|---|
| 1 | 467795 | -446.46 | 515 Szymon P | 414 | **pre_shift** |
| 2 | 467747 | -411.70 | 414 Albert Dec | 393 | last_assigned_pickup |
| 3 | 467725 | -311.48 | 470 Piotr Zaw | 370 | last_assigned_pickup |
| 4 | 467724 | -302.78 | 470 Piotr Zaw | 470 (match) | last_assigned_pickup |
| 5 | 467539 | -292.35 | 457 Adrian Cit | 457 (match) | last_picked_up_delivery |

Case #1 `pos_source=pre_shift + score -446` duplikuje V3.24-SCHEDULE
uzasadnienie. Cases #4/#5 match actual==proposed mimo score -300 → coordinator
musiał zaakceptować (solo viable albo no alt).

**Propozycja:** hard floor `score < -150` trigger KOORD albo dodatkowy warning
line w Telegram. Precedent: V3.16 `_demote_blind_empty` inline post-scoring layer.

**Decision pending:** 7-dniowy backtest historical shadow_decisions na
expected behavior change przed hard block commit.

**Est:** 2-3h backtest + 2-3h implementation. **Blocking:** brak.

### 🟡 V3.19l-TIER-PROMOTE-INVESTIGATION — NEW LOW

C3-Q1 finding: top 10 proposed couriers per-oid dedup (N=274):

| cid | name | n_prop | % all | match_rate |
|---|---|---|---|---|
| 414 | Albert Dec | 55 | 20.1% | 18.2% |
| 470 | Piotr Zaw | 36 | 13.1% | 27.8% |
| 400 | Adrian R | 35 | 12.8% | 20.0% |
| 514 | Tomasz Ch | 31 | 11.3% | 19.4% |
| 393 | Michał K. | 23 | 8.4% | 30.4% |

Top 5 = **65.7%** wszystkich propozycji. **Zero Goldów w top 5** (Bartek O.
cid=123, Mateusz O cid=413, Krystian, Gabriel). Mateusz O #10 z 3.6%
udziałem. Match rates top 5: 18-30% — żaden top courier >30% match.

**Hipoteza:** scoring underweight Gold tier albo BUG-4 tier×pora cap
matrix za silnie ogranicza Goldów (std/peak/4 vs gold/peak/6 — delta cap=2 ale
bonus_bug4_cap_soft pref dla std). Analogicznie feasibility może pref
informed-pos candidates (last_picked_up_delivery vs gold z post_wave).

**Zakres (discovery):** 
- Per-tier match_rate audit w window post-flip
- BUG-4 cap_used distribution per tier
- Score distribution per tier (raw + penalty)

**Est:** 2-3h discovery. **Blocking:** brak. **NIE blokuje V3.24.**

### 🔴 V3.24-SCHEDULE-INTEGRATION — PRIORITY #1 BLOCKING

Podwójne uzasadnienie z C3:
- **Q1:** Albert Dec 414 = **20.1%** wszystkich propozycji (55/274), match 18.2%
- **Q2 case #1:** oid=467795 score=-446 pos_source=**pre_shift** (kurier
  przed zmianą, scoring syntetyczny cold-start bez walidacji grafiku)

Existing ticket wyżej w tym pliku (sekcja "V3.24-SCHEDULE") pokrywa problem.
Est 1.5-2 dni. **Start jutro.**

**UWAGA operacyjna:** po deploy V3.24 zdjąć Albert blacklist z
`manual_overrides.json` w tym samym kroku. Backup już istnieje:
`manual_overrides.json.bak-pre-albert-2026-04-22`.

---

## 2026-04-22 — session closure (audit V3.19h Q&A + live peak)

> **Ground truth dla wszystkich poniższych ticketów:**
> `/root/.openclaw/workspace/docs/REGULY_BIZNESOWE_2026-04-22.md`
>
> Formalne reguły biznesowe Ziomka (HARD + SOFT gradient + hierarchia
> priorytetów). Każdy V3.19j/V3.24+ ticket MUSI je respektować. Zmiana
> scoringu/feasibility bez zgodności z regułami = rework.
>
> **Pełen session handover (feature flags, git tags, audit metrics,
> Telegram log, open items):**
> `/root/.openclaw/workspace/docs/SESSION_CLOSE_2026-04-22.md`
>
> Read BEFORE touching any ticket — zawiera context co było zrobione
> kiedy + dlaczego oraz prerequisites dla next session (post-peak
> cleanup checklist + Bartek validation pending).

### V3.24-SCHEDULE — Schedule Integration (PILNY, HIGH priority)

**Problem (discovered 22.04 10:59):**
Ziomek proponuje kurierów poza ich godzinami pracy. Case live #467723 —
Albert Dec (K414) zaproponowany jako feasible kandydat o 10:59 mimo że
Albert pracuje od 12:00.

**Root cause:**
`courier_resolver.dispatchable_fleet` MA schedule check (uses
`schedule_today.json` + `PRE_SHIFT_WINDOW_MIN=50`), ale window 50min
to za szeroko. Albert przy shift_start=12:00 jest pre_shift-allowed
już od 11:10. Shadow @ 11:53 Warsaw: `PROPOSE best=414` = legit per
code ale niepożądane z Adrian perspective. Scoring/feasibility nie
re-sprawdza grafiku przed inclusion — polega tylko na fleet roster.

**Akcje:**
1. **Quick patch (deployed 22.04 ~13:00 UTC):** `manual_overrides.json`
   excluded list += "Albert Dec". `dispatchable_fleet:550-551` hard
   skip. Zero restart (manual_overrides.get_excluded re-loads per call).
   Backup: `manual_overrides.json.bak-pre-albert-2026-04-22`. Remove
   after 12:00 Warsaw (manual or Adrian via Telegram bot command).
2. **Properly V3.24:** Shorten `PRE_SHIFT_WINDOW_MIN` default → 15-20 min,
   OR make per-courier configurable. Sheets fetch już jest
   (schedule gid 533254920 w Spreadsheet `1Z5kSGUB0Tfl1TiUs5ho-ecMYJVz0-VuUctoq781OSK8`,
   load 06:00 i 08:00). Integracja feasibility: kurier feasible tylko
   w aktualnej zmianie (hard gate), gradient tolerance dla
   pre_shift <15 min z penalty.
3. **Cold-start tolerance refactor:** kurier 0-15 min do start =
   kandydat z -5 penalty; 15-30 min = z -15 penalty; >30 min = skip.

**Estimated effort:** 1.5-2 dni (window tuning + per-courier config +
feasibility integration + tests).

**Blocking:** brak — niezależny od V3.19j.

---

### V3.19j-BUG2-MAGNITUDE — BUG-2 magnitude tuning (HIGH priority)

**Problem (discovered 22.04 Q&A audytu):**
`common.bug2_wave_continuation_bonus(gap_min)` daje +30 binary dla
każdego `gap<0`, niezależnie od magnitude. Ekstremalny overlap
(gap=-44min, kurier dowozi przez 44 min po pickup ready) dostaje ten
sam bonus co mały overlap (gap=-7min, realistic interleave).

**Adrian rule (z Q&A):**
- gap 0 do -5min = ideal (pełen +30)
- gap -5 do -15min = bardzo dobry (+25)
- gap -15 do -30min = OK (+15)
- gap -30 do -45min = możliwe ale słabsze (+5)
- gap -45 do -60min = unikamy (-10)
- gap < -60min = bad (-30)

**UWAGA:** gradient, nie threshold. Próg NIE eliminuje kandydata —
tylko zmniejsza/odwraca bonus. Adrian: "im mniejszy waste tym lepszy,
ALE może być nawet 40 min jeśli najlepszy kandydat".

**Implementacja:**
```python
def bug2_wave_continuation_bonus(gap_min: float) -> float:
    if gap_min >= 0:
        return 0.0  # waste, nie anticipation
    abs_gap = abs(gap_min)
    if abs_gap <= 5:   return 30.0
    elif abs_gap <= 15: return 25.0
    elif abs_gap <= 30: return 15.0
    elif abs_gap <= 45: return 5.0
    elif abs_gap <= 60: return -10.0
    else:               return -30.0
```

**Validation:** re-run audit z nowym bonus, expect:
- BUG-2 fires drop from 13% (v5 post-feasibility-fix) → ~5-8%
- Top R4 klastry score breakdown rebalanced (extreme overlap kandydaci
  spadają w ranking)
- Match top-1 boost +1-2pp expected

**Estimated effort:** 4-6h (function change + tests + audit re-run + validation).

---

### V3.19j-DISTANCE-WEIGHT — Reweight road→restaurant penalty (MEDIUM priority)

**Problem (discovered 22.04 Q&A case #423809):**
W decyzjach gdzie 2+ kandydatów ma akceptowalny BUG-2 overlap
(`|gap|<15min`), Ziomek systematically chooses far candidate z marginal
timing improvement nad close candidate z adequate timing.

**Example:** Adrian Ba (1.96km, gap=-8min) TOTAL=148.64. Mateusz Bro
(5.16km, gap=-4min) TOTAL=209.59. Mateusz wygrał głównie przez
timing_gap +25 vs +15 (10pkt różnicy), ale road 5.16km vs 1.96km
nie miało wystarczającej penalty.

**Adrian rule (priorytet decyzyjny):**
1. **Najpierw:** kurier nie może DUŻO przedłużać czasu dla restauracji
   (BUG-2 magnitude)
2. **Potem:** bliskość do restauracji (road→restaurant)
3. **Potem:** R4 corridor (drop "po drodze")

**Implementacja:** nonlinear road_to_restaurant_penalty:
- 0-1km: 0
- 1-2km: -2 pkt/km
- 2-4km: -5 pkt/km
- 4-6km: -10 pkt/km
- 6+ km: -15 pkt/km

Apply jako tie-breaker po BUG-2 magnitude check.

**Validation:** re-run audit, expect decisions w "all-OK timing" zone
shift to closer candidates.

**Estimated effort:** 3-4h.

---

### V3.19i — Operator interface refactor (MEDIUM priority, deferred)

**Problem:** Ziomek ma 3 interfejsy odpowiedzi: zielony (zatwierdź) /
INNY / KOORD. Free-text "jakub ol ma po drodze" → "❓ Nie rozumiem."
Operator komentarze nie są przyswajalne podczas live peak.

**Akcje:**
1. Reaction handler 👍/👎 (message_reaction allowed_updates).
2. Re-design parsera: `/assign K414`, `/koord`, `/swap K414 K207`,
   `/skip`, `/stop`, `/koment <text>` komendy.
3. Multi-operator support (Adrian + Bartek concurrent).
4. **Dodano 22.04:** Pre-canned reasons — przy klik NIE/KOORD pojawia
   się dropdown ("za daleko" / "extreme overlap" / "kurier nie pracuje"
   / "inny lepszy").

**Estimated effort:** 1-2 dni.

---

### V3.23 — Czasówki proposal mode (HIGH priority, spec ready)

Spec gotowy w `/mnt/user-data/outputs/V3.23_CZASOWKI_SPEC.md` (485 L) —
wymaga deploy do `/root/.openclaw/workspace/docs/V3.23_CZASOWKI_SPEC_2026-04-21.md`
+ git tag `v323-spec-v1`.

Implementation **blocked na V3.24** (Schedule Integration) — bez
grafiku Ziomek nie wie kto jest dostępny dla czasówki.

---

### Dashboard v5.1 bugs (LOW priority, audit-only, zamknięte 22.04)

Discovered w Q&A audytu 22.04, **wszystkie naprawione w dashboard v5.1**:

- **Z2-A ACTUAL dup w alternatives** — dashboard mkCandCard dodaje
  "SAME PERSON as Alt #X" w ACTUAL panel + "SAME PERSON as ACTUAL
  panel above" w alt card gdy ⭐.
- **Z2-B Outcome threshold mismatch** — thresholds per spec sekcja 5.2:
  GOOD ≤5, OK 5-15, BAD 15-30, CRITICAL >30 OR cancelled. Było
  GOOD≤20 (my optimistic interpretation for urban travel). Re-classify
  + 43,397 counterfactual est_outcome labels auto-updated.
- **Z2-C Scoring TOTAL display mismatch** — dashboard ukrywał
  `r9_stopover`, `r9_wait_pen`, `R1/R5/R8 soft`, `base_total`
  breakdown, `bonus_l2`. Manual trace #424327: TOTAL math CORRECT w
  data; tylko display incomplete. Fix: `mkCandCard` teraz renderuje
  WSZYSTKIE non-zero components.

Zero prod impact — tylko `/tmp/v319h_audit/` dashboard rendering.

---

### Albert Dec assignment (DONE 21-22.04)

**Status:** ✅ deployed.

- PIN 8770 w kurier_piny.json + kurier_ids.json (commit
  `courier-albert-dec-pin-deployed-21apr`).
- Tier "std" w courier_tiers.json (added 22.04 ~09:00 UTC,
  cap_override peak=3 conservative for new courier).
- GPS opcjonalne (cold_start pos jeśli brak).
- Live verified 22.04 11:53 Warsaw: K414 pojawił się w shadow
  propozycji (best=414).

**Open issue:** schedule respect — Albert proposed pomimo godzin pracy
12:00+. Quick patch blacklist via `manual_overrides.excluded`
(deployed 22.04 ~13:00 UTC). Properly w **V3.24-SCHEDULE**.

---

### Lekcje techniczne dodane 22.04

**Lekcja #10 — Adrian rule changes mid-Q&A.** W Q&A audytu Adrian
zmienił interpretację swojej własnej reguły 3 razy w 30 min
(Mateusz/Marek/Adrian Ba po kolei preferowany). **Reguła:** Q&A na
complex business cases nie da spójnego signal w 1 sesji. Wymaga 2-3
iteracji (Adrian + Bartek razem) zanim reguła się stabilizuje. Active
learning loop NIE jest one-shot — ongoing process miesięcy.

**Lekcja #11 — Replay reconstruction has fundamental limits.** Roster
bias (3-day → ±3h fix), gap interpretation (BUG-2 binary signal),
missing scoring components (dashboard render bug) — żaden nie jest
"fundamental bug Ziomka", wszystko **artefakty replay
reconstruction**. **Reguła:** backtest ≠ production validation. Audit
jako research tool dla pattern discovery. Verdict produkcyjny =
live data only.

**Lekcja #12 — Adrian's domain knowledge > statistical inference.**
Audyt v5 sugerował "BUG-2 dinner_peak Grill Kebab/Rany Julek to top
kontrowersyjne klastry." Adrian w 30 sekund: "Albert pracuje od 12,
to bug." CC nie miał tego signal. **Adrian operational knowledge >>
historical analysis.** **Reguła:** live operational decisions Adriana
> każdy backtest verdict. Ziomek active learning = Adrian (+ Bartek)
decisions in production, nie historical Q&A.

**Lekcja #19 — Audit findings z "verify" flag = hipotezy, nie bugi.**
Cross-review arbiter z 2026-04-25 (Gemini 3.5 Pro + Deepseek) oznaczył
C2 TZ handling jako CRITICAL na równi z C1. Arbiter wyraźnie napisał
"verification wymaga pliku `courier_resolver.py` którego nie
udostępniono" — ale w sprint prompcie zastrzeżenie zostało zignorowane
i C2 traktowany na równi z C1 jako CRITICAL fix do natychmiastowego
deployu (~45 min scope).

STEP 3A verification (live code grep `_shift_end_dt`/`_shift_start_dt`)
ujawniła że loader ZAWSZE zwraca aware Warsaw datetime (`datetime.now(WAW)`
+ `.replace(hour=...)` zachowuje tzinfo). Defensive code
w `feasibility_v2.py:304/318/340` branch `is None` nigdy nie fire →
bug NOT FIRES live. CC escape hatch (sytuacja A "finding niepasujący
do wzorca") uratowała ~45 min wasted work + dodatkowe scope creep risk
(~40+ matchów w 14 plikach gdyby rozszerzać blindly).

**Reguła:** Przed committem resources do "CRITICAL" fix z audit findings,
MUSI być STEP 3A verification (live data grep / shell introspection /
journal pattern match) że bug faktycznie fires w produkcji. Confidence
arbitra + confidence audytu ≠ verification. Arbiter "verify" flag ZAWSZE
honorowany jako HARD STOP przed committem resources. Sprint prompt pisząc
"CRITICAL" bez weryfikacji = premature commitment.

**Aplikacja:** każdy audit-sourced bug w TECH_DEBT musi mieć pole
`STEP 3A status: VERIFIED_FIRES / VERIFIED_NOT_FIRES / UNVERIFIED`.
UNVERIFIED = medium/low do re-verification sprint, nie hotfix.

### V3.19h deferred tickets

- **BUG-3 directional efficiency** — NOT_CONFIRMED z haversine proxy. Re-verify
  za ~2 tygodnie z real GPS tracks (OSRM route replay per wave).
- **4 kurierów 0% GPS** (Kacper Sa 502, Adrian Cit 457, Szymon P 515, Gabriel Je 517)
  — MEDIUM priority, właściciel "działa na razie". Deep-dive APK session later.
- **639 delivered bez delivery_coords** — 30% backfill target. Low priority.
- **V3.19g przedłużenia czas_kuriera invalidation** — blocked na V3.19h stable.
- **V3.21 wave_scoring flip** — blocked na V3.19h production stable + real GPS.
- **Panel-watcher SIGKILL fix** — timeout `TimeoutStopSec=120s` zastosowany
  (ba8792e), waiting natural restart aby apply (panel-watcher uptime 3h+
  od 2026-04-20 20:08:54, celowo zachowany clean).

### V3.19h bonus stack boundary monitoring (2026-04-21)
Max positive bonus stack realistic scenario po V3.19h impl:
- bonus_l1 (L1 same-rest) = 25 (max przy BUG-1 factor=1.0)
- bonus_l2 (L2 nearby pickup) = 20 max
- bonus_bug2_continuation (BUG-2) = 30 max
- timing_gap_bonus = 25 max
- **Total = 100 — boundary OK na dziś.**

R4 standalone = 150 (Bartek Gold weight 1.5 × raw 100 max) — pre-existing,
nie w V3.19h scope. Może dominować scoring gdy bundle_level3 TIER_A.

**Monitoring:** przy kolejnych dodatkach bonus (BUG-3 directional, V3.21
wave_scoring features, V3.22 BUNDLE_VALUE_SCORING) revisit cap. Może
trzeba:
- Podnieść cap do 150 (+50 headroom)
- Wprowadzić scaling / capping mechanizm (np. max positive sum = const)

Monitor post-flip: grep realnych score distributions w shadow_decisions.jsonl
co tydzień. Gdy median > 80 albo p99 > 150 → signal rosnącego bonus bloat.

### V3.19ef systemd timeout fix LIVE (2026-04-20)
Precedens: V3.19e restart 2026-04-20 17:17 UTC → panel-watcher SIGKILL bo
default TimeoutStopSec=15s za krótki (fetch_order_details HTTP timeouts +
cookie jar cleanup wymagają dłużej przy graceful SIGTERM).

Fix (daemon-reload only, zero service restart):
- `/etc/systemd/system/dispatch-panel-watcher.service`: TimeoutStopSec=15 → 120s.
- `/etc/systemd/system/dispatch-shadow.service`: explicit TimeoutStopSec=60s
  (było default 90s; graceful SIGTERM handler shadow loop ze sleep 5s wystarczy
  mniej niż default).
- Backup: `/etc/systemd/system/dispatch-*.service.bak-pre-v319ef-timeout`.
- Nowe timeouty zadziałają przy następnym naturalnym restarcie.

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
