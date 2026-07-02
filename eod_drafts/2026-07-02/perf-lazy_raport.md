# FALA SERIAL — perf compute-zawsze (finding E audytu 2.0) — RAPORT

**Data:** 2026-07-02 · **Branch:** `fix/perf-lazy` (worktree `/root/.openclaw/workspace/wt-perf-lazy`, baza `affe7ea`)
**Flaga:** `ENABLE_PERF_LAZY_MEMBERS` (INFRA, default OFF) · **Zasada:** zmieniamy CZAS, nie TREŚĆ decyzji (bajt-w-bajt)
**Status:** BUDOWA gotowa. Flip + restart = ACK Adriana rano (build-only, nic nie deployuję).

---

## 0. TL;DR

Zmierzyłem per-człon KTÓRE compute zjada stały narzut 665 ms (dziura #2 audytu: „podział 665 ms na człony — nieznany"). **Najgrubszy realny narzut NIE jest członem shadow — to infrastruktura odczytu:**
1. **flag-load stat-spam**: `flag()`/`decision_flag()` wołane ~700×/decyzję, KAŻDE robiło `FLAGS_PATH.stat()` (syscall). Przy 10 kandydatach w ThreadPoolExecutor = **~740 stat/decyzję**.
2. **plan-read pod fcntl-lockiem**: `load_plan` czytany PER KANDYDAT → N× pełny `_read_raw` (open+json.load) POD lockiem → wątki serializują się na lockfile (kontencja rośnie w peak).

Oba to klasa (iv) „to samo liczone N× w jednej decyzji" → dwa cache pod flagą `ENABLE_PERF_LAZY_MEMBERS`.
**Pomiar replay (fleet=10): p50 402→312 ms (−22,4%), mean −16,5%, p95 −17%.** Bajt-parytet ON↔OFF = 0 mismatch na ≥400 realnych zdarzeniach. Offline single-proc = DOLNA granica (peak: kontencja fcntl + stat między równoległymi decyzjami → więcej).

⚠ **Korekta narracji audytu:** hipoteza „~15 członów shadow liczonych ZAWSZE nawet przy fladze OFF" jest w większości NIEPOTWIERDZONA dla CIĘŻKICH członów — food-age (double-solve), single-LGBM, loadaware GATE-ują compute za flagą (skip gdy OFF). Ciężkie ON-człony (PLN whole-pool, objm-lexr6, lgbm-twomodel, best_effort, min_delivered, reserve) MAJĄ konsumentów (serializer→ledger→werdykt-tools) → nie da się ich „wygasić", tylko próbkować = decyzja produktowa Adriana (lista niżej).

---

## 1. KROK 1 — PROFIL per człon (cProfile, replay events.db, nice-19)

Harness: `tools/perf_lazy_harness.py` (replay REALNYCH NEW_ORDER z `dispatch_state/events.db`, flota syntezowana deterministycznie, seed stabilny międzyproc.). Ranking self-time (tottime) na 98 decyzjach fleet=10:

| # | Człon (funkcja) | self-time | ncalls | klasa | flaga live |
|---|---|---:|---:|---|---|
| 1 | OR-Tools TSP (`IndexToNode`+`time_cb`+`dist_cb`+`Solve`) | ~30,5 s / 45 s (**67%**) | 40M+ | **legit routing bag≥2** (NIE waste) | OR_TOOLS ON |
| 2 | `common.load_flags` (+ `Path.stat`/`posix.stat`/json) | ~4,2 s (**~9%**) | 69 660 (**~710/decyzję**) | **(iv) cache** | infra |
| 3 | `plan_manager._read_raw` (+`_locked` fcntl) | cumul 22,7 s / self 0,28 s | 1 258 (**~13/decyzję**) | **(iv) cache** | infra |
| 4 | `drop_zone_from_address` | 0,57 s | 1 264 | (iv) drobny (nowy-zone per kand.) | infra |
| — | człony shadow (PLN/objm/lgbm/min_deliv/reserve/best_effort) | rozproszone, O(pool) py | — | (ii) obs+konsument | ON |

**Wniosek:** stały narzut = (a) OR-Tools (legit), (b) infra-odczyt (flags stat + plan fcntl) = mój cel, (c) ON-człony shadow (mają konsumentów). Klasa (i) „gate członów za OFF-flagą" = **znikoma** (ciężkie OFF już gate'ują compute).

## 2. KROK 2 — MAPA KONSUMENTÓW top-N

| człon | kto czyta wynik | klasa | akcja |
|---|---|---|---|
| load_flags stat | każde `flag()`/`decision_flag()` — wartość identyczna z cache | (iv) cache'owalny | **IMPLEMENTED** (TTL 0.25s) |
| plan `_read_raw` | `_soon_free_probe` (2374) + base_sequence read (3922) — READ-only; writers osobno pod exclusive lock | (iv) cache'owalny | **IMPLEMENTED** (mtime-cache + deepcopy) |
| OR-Tools plan | `plan.sequence`→decyzja/kanon | legit | zostaje |
| PLN whole-pool (`ENABLE_PLN_OBJECTIVE_SHADOW` default True) | `pln_*` w ledgerze → werdykt-tools; objm-lexr6/E2 A/B (live select) | (ii) obs+konsument | **propozycja Adrianowi** (sampling) |
| lgbm-twomodel-shadow (ON) | `lgbm_twomodel_shadow` w ledgerze → eval ML | (ii) | propozycja |
| AUTON gate ×3 (strict+_d+_dprime) | `would_auto_assign*` → autonomia kalibracja | (ii) compute-always by design | propozycja (gate _d/_dprime) |
| food-age solve/shadow, single-LGBM, loadaware | (OFF live) compute JUŻ gate'owany | (i) — brak zysku | N-D (już gated) |

## 3. KROK 3 — IMPLEMENTACJA (za `ENABLE_PERF_LAZY_MEMBERS`, default OFF)

**A. `common.load_flags` — flag-load fast path.** ON: re-stat najwyżej co `PERF_FLAGS_STAT_TTL_S`=0.25s; w oknie TTL zwrot cache bez stat. Gate po EFEKTYWNEJ fladze (odświeżanej przy reloadzie JSON — bez rekurencji; wzorzec #9). Flags dict READ-ONLY (`.get()`) → zero ryzyka mutacji. OFF = stat co wywołanie (1:1 sprzed fali).

**B. `plan_manager.load_plan`/`load_plans` — read-cache.** ON: cache po `(mtime_ns, size)` NAD fcntl-lockiem (ciepły hit pomija fcntl+open+json.load); WYŁĄCZNIE ścieżki READ, zwrot przez `copy.deepcopy` (caller nie mutuje współdzielonego cache). WRITERS (save/invalidate/advance/insert) dalej surowy `_read_raw` pod exclusive lock i mutują świeży parse → `os.replace` bumpuje mtime → cache sam się unieważnia. OFF = fcntl-lock + `_read_raw` co wywołanie.

**Flaga:** stała `common.ENABLE_PERF_LAZY_MEMBERS=False` (env override test/harness), kanon = flags.json. **NIE w `ETAP4_DECISION_FLAGS`** (nie zmienia decyzji → checker `flag_effect_coverage` słusznie nie dotyczy; baseline'ów nie tknąłem). Doc: `ZIOMEK_LOGIC_REFERENCE.md` sekcja „FALA SERIAL perf compute-zawsze".

## 4. KROK 4 — DOWODY

**(a) BAJT-PARYTET ON vs OFF** — `tools/perf_lazy_harness.py parity`, serializacja REALNYM `_serialize_result`, wyklucza WYŁĄCZNIE pola czysto-czasowe (`latency_ms`, `r07_compute_latency_ms`, `osrm_cache_age_s`, `lgbm_*.evaluation_ts/latency_ms`, `ts` — wypisane jawnie). **Kontrola OFF vs OFF = IDENTYCZNE** (dowód że po wykluczeniu czasu harness jest deterministyczny; PYTHONHASHSEED=0). **OFFa vs ON = 0 mismatch** na 129 (flota 0/3/5/8/10/12).
**Skala ≥400: 580 realnych zdarzeń (PROPOSE 469 + KOORD 111, 0 błędów):**
- **decyzja (verdict + reason + best_cid + best_score) = 580/580 IDENTYCZNE ON↔OFF.**
- pełna serializacja z wykluczeniem pól czysto-czasowych: 578/580 identyczne; **2/580 różnią się WYŁĄCZNIE w `traffic_v2_shadow_route`** (geometria OSRM legów). To NIE moja zmiana — **kontrola OFF vs OFF też różni się (case 545 w OBU)** → to prawdziwa nondeterministyka **OR-Tools solve pod limitem 200 ms** (dok. „hituje 200ms ceiling EVERY call"; różna liczba iteracji run-to-run przy różnym obciążeniu CPU → inna, ale też-poprawna sekwencja worka). Moja zmiana (cache flag/planów) NIE MA ścieżki przyczynowej do geometrii routingu (syntetyczni kurierzy nie mają zapisanych planów → plan-cache inert; flag-cache nie dotyka OSRM).
- po dodatkowym wykluczeniu `traffic_v2_shadow_route` (uzasadnione kontrolą OFF/OFF): **0 mismatch / 580.**
> ⚠ Landmina złapana: builtin `hash()` seedu floty jest PYTHONHASHSEED-solony → dwa procesy generowały RÓŻNE floty (fałszywy „mismatch"). Fix: seed z `hashlib.md5` (stabilny międzyproc.). Bez kontroli OFF/OFF bym tego nie odróżnił od realnej regresji.

**(b) POMIAR przed/po** (replay, fleet=10, repeats=4, nice-19, baseline↔baseline2 stabilne 402↔401):
| config | p50 | mean | p95 |
|---|---:|---:|---:|
| baseline | 402 | 389 | 566 |
| flags-frozen | 339 (**−63**) | 349 | 516 |
| plans-cached | 355 (**−47**) | 364 | 558 |
| **both (=flaga ON)** | **312 (−90, −22,4%)** | 325 (−16,5%) | 470 (−17%) |

Top-3 odzyskanych: flags-stat (−63 ms), plan-fcntl+parse (−47 ms), stackują. Mechanizm potwierdzony: 200 odczytów flag → **1 stat** (ON) vs **200** (OFF).

**(c) MUTATION ×3** (`tools`→`scratchpad/mutation_check.py`, wzór C13 — zepsuj → guard PADA):
- M1 const cache-key (ignore mtime) → stale read → `test_plans_cache_busts_on_write` PADA ✅
- M2 bez deepcopy (return shared) → poison przecieka → `test_plans_deepcopy_isolates_cache` PADA ✅
- M3 TTL=0 (stat co raz) → ON stat=200≈OFF → `test_flags_fastpath_skips_restat` PADA ✅

**(d) REGRESJA + strażniki:** worktree `pytest tests/` = **4048 passed / 23 failed / 26 skipped / 9 xfailed / 2 xpassed**. 23 failed = ZNANE artefakty path-layout worktree (`test_a2_selection_shadow` ×15 + `test_courier_reliability` ×8) — **potwierdzone: po `git stash` moich zmian te same 23 padają** (niezależne od fali; moduły nie odwołują moich symboli; kod inert przy fladze OFF). Nowe `tests/test_perf_lazy_members.py` = **7/7 PASS** (w tym 3 mutation-guardy). S1/o2-capz/L7.5 nietknięte. Golden route-order: N-D (nie dotykam kolejności — tylko KIEDY czytam flagi/plany).

## 5. PLAN FLIPU (za ACK Adriana, off-peak)

1. Wpis do `flags.json`: `"ENABLE_PERF_LAZY_MEMBERS": true` (hot; ale efekt cache load_flags wymaga świeżego procesu → restart shadow dla pewności).
2. `systemctl restart dispatch-shadow` (+ ewentualnie `dispatch-plan-recheck`/`dispatch-panel-watcher` jeśli chcemy zysku też tam — te też czytają flagi/plany).
3. Pomiar `tools/perf_budget_report.py` (FALA-1) na oknie 30-min PRZED peakiem: p50/p95 live OFF↔ON.
4. Rollback HOT: `"ENABLE_PERF_LAZY_MEMBERS": false` (kolejny tick/proces czyta świeżo); twardo `git revert`.

## 6. RYZYKA / GRANICE

- **Staleness flag ≤0,25s / staleness planów do następnego mtime-bumpa** — mieści się w tolerancji hot-reload (dziś flags.json zmieniany rzadko, plany przez os.replace → mtime natychmiast). Gdyby ktoś polegał na sub-0,25s propagacji flagi w JEDNEJ decyzji — nie robi tego (decyzja i tak nie powinna czytać różnych wartości flagi w połowie).
- **Pomiar offline = DOLNA granica** peak-zysku (brak inter-decyzyjnej kontencji fcntl/stat w single-proc). Live p95 przed peakiem to potwierdzi/zaktualizuje.
- **PLN-shadow default True** (`ENABLE_PLN_OBJECTIVE_SHADOW`) = najgrubszy ON-człon O(pool) — realny kandydat na sampling, ale karmi werdykt-tools → **NIE ruszam, lista dla Adriana** (koszt ~O(pool) py/decyzję; sampling 1/N = decyzja produktowa + przegląd konsumentów ledgera).

## 7. PROPOZYCJE DLA ADRIANA (NIE implementuję — obs+konsument, decyzja produktowa)

| metryka/człon | koszt | konsumenci | rekomendacja |
|---|---|---|---|
| PLN whole-pool (`ENABLE_PLN_OBJECTIVE_SHADOW`) | O(pool) py/decyzję, default ON | `pln_*` ledger → werdykt-tools; objm-lexr6/E2 live-select | sampling 1/N compute gdy NIE potrzebne do live-select (wymaga rozdzielenia shadow-log od live-tie-break) |
| lgbm-twomodel-shadow | inference/decyzję, ON | ledger → eval ML | sampling 1/N (eval nie potrzebuje 100% pokrycia) |
| AUTON gate `_d`/`_dprime` | 2× evaluate/decyzję | `would_auto_assign_d/_dprime` → autonomia kalibracja | gate za flagą profilu D po zebraniu N (dziś telemetria pełna) |

---

**Pliki (worktree, absolutne):**
- `/root/.openclaw/workspace/wt-perf-lazy/common.py` — flag-load fast path
- `/root/.openclaw/workspace/wt-perf-lazy/plan_manager.py` — plan read-cache
- `/root/.openclaw/workspace/wt-perf-lazy/tests/test_perf_lazy_members.py` — 7 testów (3 mutation)
- `/root/.openclaw/workspace/wt-perf-lazy/tools/perf_lazy_harness.py` — profile/measure/parity
- `/root/.openclaw/workspace/wt-perf-lazy/ZIOMEK_LOGIC_REFERENCE.md` — doc flagi
