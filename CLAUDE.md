# CLAUDE.md — Dispatch V2 instruction for Claude Code sessions
# Update: 2026-04-25 wieczór (V3.27 sprint complete + Phase 1 latency fix)

# ═══════════════════════════════════════════════════════════════════
# STATE 25.04.2026 wieczór (V3.27 sprint complete, Phase 1 verified)
# ═══════════════════════════════════════════════════════════════════

## Latest deploy

- **Sprint V3.27 zakończony ~19:00 Warsaw** (Bug X+Y+Z + latency parallel + districts coverage + Phase 1 latency fix)
- 9 commits + 9 V3.27 tags chronologically (zob. niżej)
- Phase 1 verification: 4/5 proposals <500ms target (mediana ~375ms, p95 ~624ms)
- Hetzner CPX22→CPX31 hardware upgrade DEFERRED Adrian's task (niedziela rano off-peak window)
- Tag closing: `v327-sprint-complete-stable-2026-04-25`

## Active flags V3.27 LIVE (post Phase 1 deploy)

- `ENABLE_V326_ANCHOR_BASED_SCORING=True` (V3.26 Bug A complete + Bug D)
- `ENABLE_V326_PO_DRODZE_STRICT=True` (V3.26 Bug C)
- `ENABLE_V326_OSRM_TRAFFIC_MULTIPLIER=True` (V3.26 Block 4E + V3.27 weekend buckety LIVE)
- `ENABLE_V326_OR_TOOLS_TSP=True` (V3.27 flip post Phase 1)
- `ENABLE_V326_SAME_RESTAURANT_GROUPING=True` (V3.27 flip post Phase 1)
- `ENABLE_V327_BUG_FIXES_BUNDLE=True` (Bug Y tie-breaker + Bug Z penalty + Z-OWN-1 corridor)
- `ENABLE_V325_SCHEDULE_HARDENING=True`
- `ENABLE_V326_R07_CHAIN_ETA=False` (skreślony — plan already chain-aware)

## V3.27 fundamental changes (no flag, baked-in)

- `V326_OSRM_TRAFFIC_TABLE` split sat/sun (Bug X — sobota peak 12-21 ×1.2)
- `dispatch_pipeline.py:1119` drive_min OSRM-first (Bug X secondary path)
- `route_simulator_v2.py` ThreadPoolExecutor parallel + nested closure `_v327_eval_courier`
- `osrm_client.py` `threading.RLock _module_lock` (concurrent cache safety)
- `V326_OR_TOOLS_TIME_LIMIT_MS = 200` RESTORED (post parallel)
- `V327_MIN_OR_TOOLS_BAG_AFTER = 2` (Phase 1A+G shortcut: bag<=1 → bruteforce fast path)
- `shadow_dispatcher.py` ortools warm-up at startup (Phase 1F — saves 153.5ms cold first-thread)
- 11 V3.27 street aliases dict (`V327_STREET_ALIASES`) + 7 NEW district streets (Bełzy/Skłodowskiej/Filipowicza+aliases/Sudecka/Bitwy Białostockiej/Depowa)
- `_v327_normalize_street_for_matching()` — alias canonicalization w `drop_zone_from_address`

## V3.27 sprint tags (chronologically, newest at bottom)

1. `v327-fix-bug-x-traffic-mult-2026-04-25` (`0c4d92e`) — weekend buckety + drive_min OSRM
2. `v327-fix-bug-z-bundle-soft-penalty-2026-04-25` (`369d46f`) — cross-quadrant penalty + corridor mult
3. `v327-fix-latency-parallel-2026-04-25` (`46051d6`) — ThreadPoolExecutor + RLock + time_limit=200
4. `v327-implementation-complete-2026-04-25` (`3457a5f`) — pre-existing test fix
5. `v327-fix-bug-y-tie-breaker-shortest-first-2026-04-25` (`8c8b427`) — Q8 Opcja 3 tie-breaker
6. `v327-fix-districts-coverage-2026-04-25` (`70b7c04`) — 3 priority + 4 best-effort + aliases
7. `v327-hotfix-filipowicza-mapping-2026-04-25` (`6161c40`) — Adrian local: Dojlidy → Nowe Miasto
8. `v327-flag-flip-final-2026-04-25` (`8525364`) — flip 3 flagi True
9. `v327-phase1-latency-fix-2026-04-25` (`aa029bb`) — skip OR-Tools bag<=1 + warm-up imports

## OPEN ITEMS (post V3.27)

### V3.28 tickets (planned)

- **V3.28-INFRA-HETZNER-UPGRADE**: CPX22 (2 vCPU/4GB) → CPX31 (4 vCPU/8GB) — Adrian's task, off-peak window. Expected p95 250-300ms (parallel scaling 2x→4x). Cost +6EUR/mies. Future-proofing dla Warsaw expansion.
- **V3.28-DISTRICTS-LONG-TAIL**: 638 streets unique observed (long-tail post top-100 coverage 97%). Defer based on shadow log usage post-flip.
- **V3.28-ALEJA-PARSER-FRAGMENT**: drop_address parser zwraca "Aleja"/"aleja" jako standalone street name dla "Aleja Jana Pawła II" — fragment artifact. 19+ events/30d w 2 wariantach.
- **V3.28-SUPRASLSKA-OUTSIDE-CITY**: ulica w Wasilkowie, defer outside-city stream handling.
- **V3.28-FEASIBILITY-C3-V325-FIXTURE**: 4 pre-existing test fails (`v325_NO_ACTIVE_SHIFT` context) — fixture cleanup.

## DEFER (niedziela 26.04+ Adrian decision)

- Hetzner upgrade scheduling (Adrian Cloud Console action)
- Daily Q&A Wave 1 review (zaległe od 24.04)
- Peak monitoring sobota tomorrow lunch (Pn-Pt 11-14)
- Plik wiedzy #5 V3.27 sprint history

## ANULOWANE (do odwołania, V3.26)

- ❌ R-07 CHAIN-ETA flip (chain_eta pesymistyczny vs plan)
- ❌ R-08 PICKUP-EXTENSION-NEGOTIATION
- ❌ R-12 RESTAURANT-HOLDING
- ❌ R-04 hardcoded 30-days graduation (replaced multi-gate schema)

## DEFER (niedziela 26.04+)

- Bug F weekend mult bump (empirical post-peak)
- Daily Q&A Wave 1 review (zaległe od 24.04)
- R-04 Graduation Schema implementation
- Pre-canned reason codes
- /help handler fix
- sla-tracker fix vs kill decision
- V326-PICKUP-COORDS-MISMATCH (12.4km cache gap)
- V326-C2-TZ-DEFENSIVE-CLEANUP (40+ files)

## ANULOWANE (do odwołania)

- ❌ R-07 CHAIN-ETA flip (chain_eta pesymistyczny vs plan)
- ❌ R-08 PICKUP-EXTENSION-NEGOTIATION
- ❌ R-12 RESTAURANT-HOLDING
- ❌ R-04 hardcoded 30-days graduation (replaced multi-gate schema)

## Adrian's strategic principle (memory 25.04)

> "Przy decyzjach architektonicznych ZAWSZE wybieram rozwiązanie najlepsze
> jakościowo i pod skalowanie na duży system w przyszłości (Warsaw, Restimo,
> Wolt Drive, full autonomy). Nigdy pragmatic shortcuts typu
> --break-system-packages, hardcoded values dla speed."

## Test gap (Lekcja #24)

`test_latency_under_300ms_p95` testował 1× TSP call, dał false confidence że
performance OK. Per-proposal cycle robi 10× TSP call sequential = 2000ms.

**Reguła:** performance tests MUSZĄ symulować full lifecycle (per-proposal 10
candidates), NIE per-component.

**V3.27 ADDED:** `test_v327_proposal_lifecycle_latency_slow.py` (2 tests, full
lifecycle p95 + race conditions).

## Lekcja #25 (V3.27 NEW): mental simulation może być naivny

Hipoteza Bug Y "Bug X self-resolves": traffic_multiplier global value preserves
ratio between permutations → tied permutations remain tied. Mental simulation
verified to z code paths podczas Krok 2.2 (NIE w shadow). Adrian's Q8 YAGNI
prevented proactive tie-breaker; faktycznie NIE self-resolved → osobny fix.

**Reguła:** mental simulation hipotezy musi być VERIFY przed implementacją —
checking solver/algorithm mechanics nie tylko intuition.

## Lekcja #26 (V3.27 NEW): domain knowledge > LLM/API confidence

Filipowicza mapping: Nominatim API → Dojlidy (HIGH confidence). Adrian local
knowledge → Nowe Miasto. Adrian wins.

**Reguła:** Adrian's local knowledge zawsze trumps external API confidence
(zob. Lekcja #5 + #19). Concrete bindings (cid↔name, street↔district)
require Adrian confirm.

## Lekcja #27 (V3.27 NEW): hardware oversubscription dla parallel

ThreadPoolExecutor 10 workers × 200ms OR-Tools time_limit / 2 vCPU = 4-5x
oversubscription → parallel efficiency 13.4% (close to physical 2-core limit).
Software fixes (skip OR-Tools bag<2) reduce work, ale fundamentally bottleneck
to hardware.

**Reguła:** parallel scaling target wymaga vCPU >= worker count / 4 (lub
problem-specific solver call rate). CPX22 niewystarczające dla 10 OR-Tools
workers; CPX31+ rekomendowane Warsaw expansion.

## Key files (V3.27 sprint)

- `/root/.openclaw/venvs/dispatch/` — dedykowany venv (ortools 9.15.6755)
- `/tmp/v327_diagnose_2026-04-25.md` — Krok 1 diagnoza Bug X+Y+Z + latency
- `/tmp/v327_implementation_2026-04-25.md` — Krok 2-4 implementation summary
- `/tmp/v327_latency_diagnosis_2026-04-25.md` — D1-D5 latency root cause
- `/tmp/v327_top100_streets_used.txt` — top traffic streets analysis
- `dispatch_v2/tsp_solver.py` — Fix 6 OR-Tools (flag True LIVE)
- `dispatch_v2/same_restaurant_grouper.py` — Fix 7 grouping (flag True LIVE)
- `dispatch_v2/route_simulator_v2.py` — Phase 1 shortcut + Bug Y tie-breaker
- `dispatch_v2/dispatch_pipeline.py` — drive_min OSRM-first + Bug Z penalty + parallel
- `dispatch_v2/osrm_client.py` — RLock module-wide
- `dispatch_v2/common.py` — V326_OSRM_TRAFFIC_TABLE sat/sun + V3.27 helpers + flag
- `dispatch_v2/districts_data.py` — 7 NEW streets (V3.27 inline comments)
- `dispatch_v2/shadow_dispatcher.py` — ortools warm-up at startup

## Continuation w nowym chacie Claude

Adrian zakończył obecny chat 25.04 16:30 z powodu LLM accumulated errors.
Nowy Claude w wieczorem dostanie:
- Plik wiedzy #4 v2 (sprint history 25.04 + rollback)
- Updated instrukcja projektu (post-rollback)
- Handover prompt z planem Bug X+Y+latency diagnosis

# ═══════════════════════════════════════════════════════════════════
# (legacy 24.04 content kontynuacja poniżej)
# ═══════════════════════════════════════════════════════════════════

## Latest session handover (READ FIRST)

Przy starcie nowej sesji **przeczytaj w kolejności**:

1. **`workspace/docs/SESSION_CLOSE_2026-04-22.md`** — pełen snapshot stanu: feature flags, git tags, audit metrics, open tickets, Telegram log. **Single source of truth dla "co było zrobione kiedy".**
2. **`workspace/docs/REGULY_BIZNESOWE_2026-04-22.md`** — ground truth dla scoringu/feasibility (HARD + SOFT gradient reguły).
3. **Ten plik (CLAUDE.md)** — setup, rollback, procedures, pull-requests rules.
4. **TECH_DEBT.md** — open tickets V3.19j, V3.24, V3.19i, V3.23, dashboard bugs.

Sesja close 22.04 zawiera: audit V3.19h complete (44k records, top-5 44.22%),
V3.24-SCHEDULE discovered as pilny blocker (Albert Dec pre-shift hotfix via
`manual_overrides`), V3.19j tickets ready dla Bartek validation.

## Quick context

Ziomek autonomous dispatcher, NadajeSz Białystok.
Server: Hetzner CPX22, UTC, 4GB RAM.
Repo: github.com/czaplaadrian88-code/ziomek-dispatch-

**Working directory (zawsze cd):**
`/root/.openclaw/workspace/scripts/dispatch_v2/`

## Live stack (2026-04-24)

**Deployed tags (latest):**
- `v325-daily-accounting-flag-on` @ f969862 — Daily Accounting LIVE (2026-04-24)
- `v326-bug3-step1-traffic-multipliers` @ 28aaf25 — OSRM traffic shadow (flag=False)
- `v326-hotfix-parser-bugs-2026-04-24` @ a93d1c4
- Baseline: `f22-v319-v320-complete` @ 466a716

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

# V3.25 Daily Accounting (2026-04-24 LIVE)
ENABLE_DAILY_ACCOUNTING = True

# V3.26 OSRM traffic (shadow, flag=False)
ENABLE_V326_OSRM_TRAFFIC_MULTIPLIER = False  # env-overridable
```

**Services:**
```bash
# Active long-running:
dispatch-shadow
dispatch-panel-watcher
dispatch-telegram         # DO NOT RESTART without explicit user ACK
dispatch-sla-tracker
dispatch-gps

# Timers:
dispatch-plan-recheck.timer         # V3.19c, every 5min
dispatch-daily-accounting.timer     # V3.25, Tue..Fri+Mon 06:00 Warsaw
dispatch-cod-weekly.timer           # F2.1d, Mon 08:00 Warsaw
dispatch-czasowka.timer

# Dispatch-state files:
/root/.openclaw/workspace/dispatch_state/
├── courier_plans.json          # V3.19b saved plans (atomic writes)
├── v319c_read_shadow_log.jsonl # V3.19c shadow diff log
├── plan_recheck_log.jsonl      # V3.19c timer output
├── kurier_ids.json             # canonical aliases (no dots since 2026-04-24)
└── kurier_piny.json            # PIN→alias (no dots since 2026-04-24)
```

## Core files (V3.19/V3.20/V3.25 key locations)

- `common.py` — feature flags + constants (Bartek gold standard)
- `route_simulator_v2.py` — V3.19a floor + V3.19d base_plan extension
- `plan_manager.py` — NEW, saved plans (load/save/invalidate/advance/insert_stop_optimal)
- `plan_recheck.py` — NEW, V3.19c consistency + GPS drift timer
- `dispatch_pipeline.py` — V3.19d hook load_plan in assess_order
- `panel_watcher.py` — V3.15 packs fallback + V3.20 packs reverse ghost detect
- `bag_state.py` — core bag filter
- `state_machine.py` — upsert orders_state
- `telegram_approver.py` — DO NOT MODIFY without explicit ACK
- `daily_accounting/` — V3.25 isolated module (Obliczenia tab writer). Entry:
  `python3 -m dispatch_v2.daily_accounting.main [--dry-run] [--target-date]`.
  Runs via `/root/.openclaw/venvs/sheets/bin/python3` (gspread). Tests:
  `python3 -m dispatch_v2.daily_accounting.tests.run_all` (21 tests, custom
  runner — pytest nieinstalowany w env).

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
- V3.25 Daily Accounting → `ENABLE_DAILY_ACCOUNTING=False` + `systemctl disable --now dispatch-daily-accounting.timer`.
  Service oneshot, fresh proces per run — zero restart needed poza timer disable.

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

## Q&A Lessons Learned (22.04.2026)

Po sesji Q&A 22.04 Adrian+Claude wyekstrahowali następujące meta-lessons
które CC MUSI przestrzegać w przyszłych sesjach.

### LESSON-QA-8: Cognitive drift w długich sesjach

Po 4h sesji Claude popełnił 3 błędy w Case #12 (czasówka vs elastyk,
hold-up description, wpływ innego ordera). Adrian musiał korygować.

**Zasada dla CC:**
- Po 4h sesji: obligatory STOP + re-grep kluczowych faktów z CLAUDE.md + TECH_DEBT.md
- Po 6h sesji: STOP + propose session close
- Dalsze Q&A: max 3 cases per batch, między batches re-verify context
- RSS >1.2GB → automatic checkpoint + alert user

### LESSON-QA-9: Operational awareness > scoring quality

Naprawa systemu informacyjnego (Ziomek wie co się dzieje operacyjnie) ma
WYŻSZY priorytet niż tuning algorytmu scoring. User experience =
psychological trust. Scoring -0.05 RMSE nic nie da jeśli Ziomek nie wie
że kurier został zwolniony.

**Zasada dla CC:**
- Priorytetyzuj reguły "widzenia rzeczywistości" (R-03 Telegram parser,
  R-02 courier sync) przed "lepsze liczenie" (R-05 speed multiplier,
  R-09 wave veto)
- Przed implementacją scoring tuning: potwierdź że Ziomek ma full
  visibility operational state

### LESSON-QA-10: Rule gradient nie threshold

Case #4 pokazał że binary rule "nowy=skip" zawodzi gdy nowy ma obiektywną
przewagę +63 points. Gradient z 3 buckets (high/medium/low advantage)
działa lepiej.

**Zasada dla CC:**
- Nowa reguła scoring/feasibility: domyślnie gradient (3-5 buckets),
  nie binary threshold
- Binary dozwolony tylko dla HARD rejects (safety, collision, post-shift)
- Przy gradient: documentuj 3+ buckets z przykładami gdy każdy fires

### LESSON-QA-11: Mapping CID concrete wymaga Adrian verify

CC Faza A zmapował cid=522 na "Grzegorz Rogowski" — błąd, to **Szymon
Sadowski**. Q&A ujawnił.

**Zasada dla CC:**
- Przed committem dowolnego cid→nazwa mapping: STOP + ask Adrian
- Audyty CC są good-enough dla patterns/statistical, ale concrete
  bindings (cid↔name, shift↔courier, restaurant↔dedicated_courier)
  wymagają Adrian confirm

### LESSON-QA-12: Screenshoty paneli + mapy = game-changer Q&A

Adrian dostarczył screenshots rutcom panelu dla Jakub OL/Michał K./Szymon Sa
+ mapy Google 5-stop route Case #8. To odblokowało insight R-06
MULTI-STOP-TRAJECTORY.

**Zasada dla CC:**
- Przy pattern analysis: jeśli tylko text log → request screenshot/map
  from Adrian
- Multi-stop decisions nie można zaudytować bez wizualizacji trajectory

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

# Business rules reference (GROUND TRUTH dla scoringu/feasibility)

**Primary business rules doc:** `workspace/docs/REGULY_BIZNESOWE_2026-04-22.md`

Formalne reguły biznesowe Ziomka spisane po sesji 22.04.2026 (Q&A audytu V3.19h
+ live peak observations). **Każda zmiana scoringu/feasibility/feature flagi
musi respektować te reguły.** W szczególności:

- **R-DECLARED-TIME** (HARD) — `czas_kuriera ≥ czas_odbioru_timestamp` zawsze
- **R-35MIN-MAX** (HARD) — max 35 min delivery od pickup (R6 gate)
- **R-NO-WASTE** (SOFT gradient) — BUG-2 magnitude per tabela (V3.19j-BUG2-MAGNITUDE)
- **R-PRIORYTETÓW-DECYZYJNYCH** — hierarchia: waste → bliskość → R4 → tier → bag
- **R-FLEET-LEVEL** — optymalizuj flotę, nie pojedynczy order
- **R-SCHEDULE-AWARE** (implicit 22.04, formalize w V3.24) — Ziomek sprawdza grafik

Encoding checklist (dla każdej reguły): kod + tests + shadow_dispatcher
serializer (LOCATION A+B) + learning_analyzer readers + dashboard rendering.
Brak któregokolwiek = niewidoczny bug.

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
ENABLE_SPEED_TIER_LOADING_PLANNED = False  # C4 — PLANNED (brak consumera)
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
