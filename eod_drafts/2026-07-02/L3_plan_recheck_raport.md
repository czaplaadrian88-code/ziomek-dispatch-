# FALA L3 — „plan_recheck przestaje cofać" (F2/K2) — RAPORT

**Data:** 2026-07-02 · **Branch:** `fix/l3-plan-recheck` (worktree `/root/.openclaw/workspace/wt-l3`, base `ced8a88`)
**Właściciel:** sesja L3 (rdzeń seryjny). **Deploy = koordynator (tmux 9), ZA ACK.** Ta sesja: KOD+TESTY+DOWODY, ZERO deployu/flip/restartu.
**Pliki dotknięte:** `common.py` (2 flagi), `plan_recheck.py` (bramka A + GC B + helpery), `tests/test_l3_plan_recheck_gates.py` (nowy, 21 testów).

---

## 1. STAN ZASTANY (ETAP-0, re-grep na żywo 02.07)
- **Procesy/env (READ-ONLY `systemctl show`):** `dispatch-plan-recheck` = **ONESHOT**, timer `OnUnitActiveSec=5min` → **merge do mastera = kod ŻYWY ≤5 min BEZ restartu** (flaga hot z flags.json przez `decision_flag`). `dispatch-panel-watcher` = simple daemon (bliźniak recanon), OSOBNY env. Parytet drop-inów: plan-recheck ma `PLAN_SEQUENCE_LOCK`/`PLAN_RECHECK_COMMITTED_PROPAGATION`/`LIVE_ETA_REFRESH`; panel-watcher ma `RECANON_ON_WRITE`/`IMMEDIATE_REDECIDE_ON_*`; OBA: `PLAN_CANON_ORDER_INVARIANTS=1`, `CARRIED_FIRST_RELAX=1`, `GPS_FREE_ANCHOR=1`, `NONCARRIED_DROPOFF_REORDER=1`, `RELAX_COLOC_PICKUP=1`, `PLAN_REAL_PICKED_UP_AT=1`, `NO_RETURN_TO_DEPARTED_PICKUP=1`.
- **`courier_plans.json` (żywy snapshot):** **48 wpisów** (39 invalidated + 9 non-inval), najstarszy created `2026-04-20`, `ENABLE_LOAD_PLAN_PURE_READ` default True — mina rozbrojona, NIE ruszana.
- **Baseline regresji z worktree:** **3783 passed / 23 failed / 23 skipped / 11 xfailed** (101s). **23 FAILED = ARTEFAKT worktree-depth, NIE-L3:** `test_a2_selection_shadow.py` (15) + `test_courier_reliability.py` (8) liczą `MODULE_PATH=Path(__file__).parents[2]/"dispatch_v2"/…` zakładając głębokość KANONU `scripts/dispatch_v2/tests`; z `wt-l3/tests` → nieistniejąca ścieżka → custom `SkipTest` raportowany jako FAILED. **Na kanonie PASS** (moduł istnieje) — to autorytatywne 3806/0 koordynatora. Mój warunek regresji: **0 NOWYCH faili poza tymi 23.**

## 2. ŹRÓDŁO NIE OBJAW (ETAP-1) + MAPA KOMPLETNOŚCI (ETAP-3)
Punkt regeneracji = `_gen_one_bag_plan` (plan_recheck) → `_sweep()` woła `simulate_bag_route_v2` (objektyw O2/duration) **BEZ `check_feasibility_v2`** → nowa sekwencja może mieć GORSZY R6 (carried>35) niż istniejący plan tego samego worka („zigzag wraca" = K2). Fix U ŹRÓDŁA = bramka ZAPISU regenu w `_gen_one_bag_plan` tuż przed `save_plan` + GC w `run_recheck`.

| Klasa | Miejsca | Dotknięte? |
|---|---|---|
| **Regen-path (gate A)** | `_gen_one_bag_plan` przed `save_plan` (~837) | TAK — bramka compare-and-keep za flagą |
| **4 handlery recanon (twin C)** | panel_watcher assign 654 / deliver 698 / pickup 759 / return 726 | **N-D — JUŻ prune-before-recanon** (advance_plan/remove_stops/mark_picked_up PRZED recanon, P-5 `0426706`). twin=0 w event-path; tick-side terminal-prune (B-i) = additive safety-net TYCH SAMYCH funkcji |
| **load_plan side-effect (pure-read D)** | 8 callerów | **read-side-effect→0:** tylko 2 (dispatch_pipeline 2374/3922) przekazują `active_bag_oids` → gałąź invalidate-on-mismatch osiągalna, JUŻ za `ENABLE_LOAD_PLAN_PURE_READ`. Reszta (plan_recheck 1986/2037 redecide/recanon, panel_watcher 578, shadow-tools b_route/bundle_calib) NIE przekazuje `active_bag_oids` → gałąź side-effect NIEOSIĄGALNA = pure z konstrukcji. Mój gate A: `invalidate_on_mismatch=False` |
| **GC courier_plans (B)** | `run_recheck` po gap-fill | TAK — `_gc_courier_plans` przez istniejące `remove_stops`/`invalidate_plan`/`gc_invalidated` pod fcntl-lockiem |
| **Nowa flaga** | ETAP4_DECISION_FLAGS + stała common.py + `decision_flag()` | TAK — 2 flagi, wzór L4 |

## 3. ZMIANY (ETAP-2: SOFT nie osłabia HARD)
### A. `ENABLE_PLAN_RECHECK_GATES` — bramka ZAPISU regenu (compare-and-keep)
- Reject-dimension = **R6 carried-age >35** (jedyny sekwencyjno-czuły HARD live, feasibility_v2:~1127). **R1/R5 spread = SOFT live** (feasibility_v2:~499 „NIE hard block") → liczona TYLKO jako metryka przez LIVE `feasibility_v2._max_deliv_spread_km`/`_max_pickup_spread_from_bag` (import, NIE kopia) — **czynienie z SOFT bramki HARD byłoby inwersją ETAP-2, świadomie pominięte**. Floor committed = auto-egzekwowany przez `_retime_stops` clamp → metryka.
- `_l3_hard_breach(stops, orders_state, pos, anchor_departure, now)` — JEDEN helper dla fresh I existing: `_retime_stops` (LIVE, identyczne pos/anchor/now → porównywalne „stąd i teraz") → `_l3_bag_time_max_min` (kotwica picked_up_at/`_sim_picked_up_at` → czas_kuriera_warsaw → pickup_pred; doktryna r6_thermal_anchor). `retimed_ok=False` (OSRM-miss) → ocena niepewna → NIE odrzuca (fail-soft).
- `_l3_gate_verdict(fresh, exist, comparable)` — pure: **REJECT** (porównywalne, świeży łamie, istniejący NIE → NIE zapisuj, keep existing, `return False`) / **BOTH_BREACH** (oba łamią → zapisz świeży) / **PASS** (świeży czysty → zapisz) / **NO_BASELINE** (brak porównywalnego → zapisz). Comparable IFF existing (pure-read) not None ∧ active-dropoff-oids == set(oids).
- Liczniki `_L3_GATE_STATS` (reset/tick, fold do summary → log `PLAN_RECHECK`): `l3_regen_rejected/both_breach/pass/no_baseline` + log `L3_REGEN_REJECTED reason=r6 fresh_r6=.. exist_r6=.. spread=..`.
- **OFF = zapis regenu bajt-w-bajt** (żadnej ścieżki).

### B. `ENABLE_COURIER_PLANS_GC` (+ `PLAN_GC_DRY_RUN` default True, `PLAN_GC_MAX_AGE_H` default 48) — GC
`_gc_courier_plans` w `run_recheck` po gap-fill, WYŁĄCZNIE istniejące plan_manager API pod fcntl-lockiem (ZERO gołego json.dump):
- (i) terminal-stop prune: aktywny plan ze stopem oid terminalnego/brakującego (TERMINAL_STATUSES) → `remove_stops`.
- (ii-a) age-zombie: invalidated >max_age_h → `gc_invalidated`.
- (ii-b) non-inval bez aktywnego zlecenia → `invalidate_plan("GC_NO_ACTIVE")` (age-GC usunie w kolejnym cyklu — czysty lifecycle, bez raw-delete).
- `PLAN_GC_DRY_RUN=True` → TYLKO raport (0 mutacji). **OFF = brak GC jak dziś.**

## 4. DOWODY (ETAP-4/5)
- **Testy L3 (`tests/test_l3_plan_recheck_gates.py`): 21/21 PASS.** Behawioralne (C13): verdict 5 przypadków, `_l3_hard_breach` (ON/OFF R6), `_l3_bag_time_max_min` kotwice, integracja `_gen_one_bag_plan` **ON≠OFF** (OFF: fresh-breach+istniejący-czysty → ZAPISUJE bajt-w-bajt; ON: → NIE zapisuje, `l3_regen_rejected=1`; healthy→PASS+save; both-breach→save; no-baseline→save), GC dry-run vs apply (żywy NIETKNIĘTY, zombie usunięty, no-active→invalidated, terminal-stop pruned).
- **MUTATION-CHECK ×2:** #1 odwrócony verdict (zapisuj łamiący) → asercja PADA vs prawdziwe REJECT; #2 GC bez guarda aktywności zabija żywy LIVE → asercja PADA vs prawdziwe GC chroniące żywy. Oba udowodnione w teście.
- **Dowód #1 — dry-run GC na KOPII żywego `courier_plans.json` (48 wpisów, zero mutacji, determinizm 2×):** `gc_age_removed=26` (invalidated >48h) + `gc_no_active_invalidated=4` (cid 522/101/533/61) + `gc_active_kept=6` + `gc_terminal_stop_prune=0` (event-handlery nadążają). **zombie 48→6 aktywnych** (26 od razu + 4 no-active→invalidated→aged; 12 recent-invalidated wygasa po 48h). Kopia bajt-identyczna po dry-run.
- **Dowód #2 — replay bramki A na 13 golden route-order (każdy = własny baseline):** 4 PASS + 9 NO_BASELINE, **0 spurious REJECT** (gate = no-op na zdrowym/stabilnym planie, idempotentny), determinizm 2× identyczny. + syntetyczne łamiące (fresh 45 vs existing 10 → REJECT) w testach.
- **Dowód #3 — determinizm:** 2× ten sam input = ten sam werdykt (GC + gate replay).
- **Strażnicy zieloni bez zmian:** golden route-order + carried-first + recanon-P5 = **38 passed / 4 skipped**. Flag checkery (doc-coverage + registry) = 8 passed; ETAP4/module_const/fingerprint = 12 passed.
- **Regresja pełna (worktree, conftest pinuje kanon):** **3804 passed / 23 failed / 23 skipped / 11 xfailed** (97s) = baseline 3783 passed **+21 nowych L3** (wszystkie PASS). Zbiór 23 faili **IDENTYCZNY** z baseline (`diff` = 0) → **0 NOWYCH faili** (23 = te same artefakty worktree-depth, PASS na kanonie).
- **Flagi:** obie w ETAP4_DECISION_FLAGS + stała common.py=False + `decision_flag` default False; OFF = fingerprint bez zmiany zachowania.

## 5. DEPLOY ZA ACK (koordynator — sekwencja)
1. **Merge** `fix/l3-plan-recheck` → master. Regresja kanonu = 3806/0 (artefakty worktree znikają).
2. **Bramka A (osobno):** wpis `ENABLE_PLAN_RECHECK_GATES: false` → flags.json + **dokumentacja flagi w `ZIOMEK_LOGIC_REFERENCE.md`** (inaczej `test_flag_doc_coverage` zgłosi undocumented gdy flaga trafi do flags.json — wzór jak L4). Flip `true` → **plan-recheck łapie hot ≤5 min (oneshot, BEZ restartu)**. Obserwuj `L3_REGEN_REJECTED`/`l3_regen_*` w logu 2 dni → dowód pozytywu (rejected/d > 0 przy 0 regresji dostaw).
3. **GC B (osobno):** wpis `ENABLE_COURIER_PLANS_GC: false` + `PLAN_GC_DRY_RUN: true` + `PLAN_GC_MAX_AGE_H: 48` → flags.json. Flip `ENABLE_COURIER_PLANS_GC: true` z **DRY_RUN=true** → przegląd `GC_*` w logu (parytet z dowodem #1) → dopiero potem `PLAN_GC_DRY_RUN: false` (real). Wszystko hot (oneshot).
4. **panel-watcher:** BEZ restartu — twin-prune NIE wymagał zmiany panel_watcher (4 handlery już prune-before-recanon).
- **Rollback:** flaga `false` w flags.json = hot (≤5 min). Zero backupu kodu potrzebnego (OFF=bajt-w-bajt).

## 6. NASTĘPNE (co zostaje L5/L6/L7)
- **L5 (F4/Q2):** feasibility „nie zdąży→nie dostaje" na bag-CHANGE (gate A świadomie NIE rejectuje bag-change: brak porównywalnego baseline → NO_BASELINE→save; feasibility-per-order to L5, OUT of scope L3).
- Load-aware ETA poślizgu odbioru (F4) karmi R6-prawdę — poprawi jakość samego R6, którym bramka mierzy.
- twin(recanon) reassign-loser: zweryfikowane że shrinking event-handlery prune-before-recanon; ewentualny loser-path (przerzut) = domena L5/reassignment.
