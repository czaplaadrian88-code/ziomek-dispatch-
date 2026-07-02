# Sprint O2 cap-Z (build-only) — raport

**Fala:** SERIAL O2 cap-Z (rdzeń silnika). **Branch:** `fix/o2-capz` (worktree `/root/.openclaw/workspace/wt-o2-capz`).
**Data:** 2026-07-02. **Autor:** Claude (Opus 4.8, 1M). **Flip = OSOBNY ACK Adriana po werdykcie S1 (~04.07); ta fala = BUDOWA, flagi default OFF, zero flipów/restartów/push.**

---

## 0. TL;DR
Dwie NOWE flagi (default OFF, bajt-w-bajt) OBOK istniejącej `ENABLE_O2_READY_ANCHOR_SWEEP` (nietknięta):
- **Krok 1 `ENABLE_O2_CAPZ_RESEQ`** — wąska reguła Opcji 3: preferuj przeplot zmniejszający overage świeżości TYLKO gdy detour≤8 ∧ carried≤20 ∧ argmin overage(gain≥2) ∧ sla nie gorsze; brak → kolejność BEZ ZMIAN. **Replay korpusu: engine-improved 7.3%** (vs review 7.9%), genuine-regres 0, detour p90 5.06 ≤ cap. **OFF = bajt-parytet vs kanon (fuzz 500/0 mismatch).**
- **Krok 2 `ENABLE_SLA_GATE_READY_ANCHOR`** — bramka SLA 35-min NOW→READY przez `sla_anchor.py`. **Replay: pure ready-anchor = 0 flipów werdyktu / ΔNO=0** (R6-gate już ready-anchoruje → tylko re-atrybucja reason, 48% reason-class churn); z live QUANTILE = +4% gold NO→MAYBE (co-design zgodności SLA↔R6-gate). REALNA zmiana decyzji → flip OSOBNO, po przeglądzie `_kind()` downstream.
- **Krok 3** re-collect λ=0 — instrukcja przygotowana, NIE wykonana.
- **Regresja: 0 realnych** (worktree 24 „fail" = 23 base-invariant symlink-artefakty [stash-proof] + 1 pre-merge flag_effect [PASS po merge, new_gap=[]]). Golden route-order 13/13, O2 sweep + sla_anchor testy zielone.

---

## 1. ETAP 0 — semantyka Z/X/Y (CYTAT ŹRÓDŁA, nie z głowy)

**Definicja Opcji 3** = kolektor `tools/bundle_calib_shadow.py` + werdykt `dispatch_state/bundle_calib_review_verdict_2026-07-02.txt`:

- **Z (cap-Z=20)** = `bundle_calib_shadow._max_carried_age` (l.441-450): `max` wieku po NIESIONYCH (`status=='picked_up'`) zleceniach = `delivered − ready`, `ready=min(czas_kuriera, picked_up_at)`. `under_z[z]` = argmin-O2 przeplot pod capem `max_carried_age ≤ z` (l.483-503). **Rekomendacja review Z=20** = „najmniejszy cap dający ≥2% policy-improved = max ochrona niesionego" (verdict txt l.21).
- **X/Y (detour)** = `bundle_calib_review._calib_under_z` (l.150-152): `detour = drive_min(under_z) − drive_min(served)` (drive-only OSRM, bez dwell/wait). Review dla Z=20: **detour med 0.04 / p90 7.93 min** (verdict JSON `caps.20`). **→ silnik: `O2_CAPZ_DETOUR_MAX_MIN=8`** = p90 zaokrąglone w górę (utrzymuje ~90% improved, tnie patologiczny ogon).
- **Overage** = O2 objektyw = `Σ max(0, age_ready − 35)` (`bundle_calib_shadow._walk_calib` l.293; parytet z `route_simulator_v2._compute_o2_metrics`). GATE review = overage-ONLY (parytet z dźwignią silnika `o2_score`; verdict l.1).
- **Materialność** `O2_CAPZ_MIN_GAIN_MIN=2` = `MATERIAL_O2_MIN` review (próg „improved").
- **Caveat λ (ORACLE-CAVEATS):** kolektor `under_z` liczy klucz `overage + 1.5·czas_late` (λ) → overage kandydata ≥ overage-argmin silnika → replay KONSERWATYWNY (silnik dowiózłby ≥ tyle; kierunek bezpieczny, brak fałszywego GO). Domknięcie = Krok 3 (re-collect λ=0).

**Stan flag (ETAP 0, efektywny w procesie):** `ENABLE_O2_READY_ANCHOR_SWEEP` = OFF (brak w flags.json, brak drop-inu). `ENABLE_SLA_ANCHOR_UNIFIED` = **ON od 14:07** (S1 LIVE, flags.json). `ENABLE_ETA_QUANTILE_R6_BAGCAP` = **ON** (flags.json true). Baseline regresji kanonu: 3993 passed / 0 failed / 26 skip / 9 xfail + 2 xpass (=11) — ZIELONY, zgodny z zadaniem.

---

## 2. Mapa kompletności (klasa: selekcja/feasibility + SLA-anchor)

| Miejsce | Krok 1 (cap-Z reseq) | Krok 2 (SLA ready-anchor) | Uwaga |
|---|---|---|---|
| `route_simulator_v2.simulate_bag_route_v2` (ogon) | ✅ `_capz_reseq_plan` na WYBRANYM planie — **pokrywa greedy+ortools+bruteforce+sticky-skip** (jedno źródło, obie ścieżki selekcji) | — | finding rst-greedy-step15-not-o2 domknięty tu (reseq re-ewaluuje pełną permutację niezależnie od tego jak greedy budował seq) |
| `route_simulator_v2._plan_from_sequence` | ✅ `drive_min` (drive-only, parytet z bundle_calib) | — | uniwersalny builder (ortools też przez niego) |
| `route_simulator_v2._enumerate_valid_plans` (NOWE, ekstrakcja z `_bruteforce_plan`) | ✅ pula kandydatów (carried-first via lock_first) | — | `_bruteforce_plan` = `_select(_enumerate(...))` → **byte-parytet bruteforce** |
| `route_simulator_v2._count_sla_violations` | — | ✅ ON→`sla_anchor.anchor(kind='ready')`; OFF→now (S1) | bliźniak SLA #1 |
| `feasibility_v2` (po `simulate_bag_route_v2`, l.848) | ✅ metryka obs `o2_capz`→`metrics` (auto-serial L1.1); reguła DZIEDZICZONA (feasibility ocenia reseq'owany plan) | — | |
| `feasibility_v2` SLA-loop (~1203) | — | ✅ ON→ready-anchor + co-design QUANTILE (gold≤4 p80 przed check) | bliźniak SLA #2 |
| `plan_recheck._sweep` / `_o2_key` | ✅ **N-D — DZIEDZICZY** (każdy `p` z `simulate_bag_route_v2` już reseq'owany) | ✅ **N-D — DZIEDZICZY** (`plan.sla_violations` z `_count_sla_violations`) | bliźniak #3; parytet gwarantowany wspólnym źródłem (single-source, ZERO duplikacji) |
| `common.py` | ✅ flaga ETAP4 + stała OFF + `O2_CAPZ_{Z_MIN,DETOUR_MAX_MIN,MIN_GAIN_MIN,MAX_STOPS}` | ✅ flaga ETAP4 + stała OFF | |
| `_compute_o2_metrics` / `ENABLE_O2_READY_ANCHOR_SWEEP` | **N-D — NIETKNIĘTE** (cap-Z reseq liczy własne paczka-świadome metryki `_capz_bag_metrics`, osobno) | — | istniejąca flaga bez zmian zachowania (test O2 sweep 25/25 zielony) |
| paczki | ✅ `_is_paczka_ordersim` (spójne z feasibility, gated `ENABLE_PACZKA_R6_THERMAL_EXEMPT`) → pominięte w overage/carried | ✅ paczki pominięte w SLA-loop (`continue` przed anchorem) | finding feas-o2-paczka-blind jawnie |

**Greedy vs ortools OBA:** reseq w ogonie `simulate_bag_route_v2` = po wyborze planu KAŻDEJ strategii → obie ścieżki pokryte jednym źródłem (nie per-ścieżka łatka).

---

## 3. Dowody

### 3a. OFF = bajt-parytet (fuzz 500 vs KANON)
`scratchpad/capz_parity_fuzz.py` — 500 seeded worków (bag 0-3, mix carried/assigned, ready ±, deterministyczny OSRM), worktree(flagi OFF) vs kanon, pola decyzji `(sequence, sla_violations, total_duration_min, strategy)`: **MISMATCH = 0**. `drive_min`/`o2_capz` = NOWE pola (kanon nie ma) → OFF nie wpływa na decyzję. `ENABLE_SLA_GATE_READY_ANCHOR` OFF (S1 unified ON) — `sla_violations` identyczne = potwierdza OFF-parytet `_count_sla_violations`.

### 3b. Krok 1 ON = replay korpusu (kierunek ~ review)
`scratchpad/capz_replay.py` na `bundle_calib_shadow.jsonl` (3049 unikalnych multi-worków, 2865 z under_z), guardy Z=20 ∧ detour≤8 ∧ gain≥2:
- **engine-improved: 222 (7.3%)** vs review **7.9%** (różnica = 23 worki z detour>8 uciete capem — kierunek zgodny, NIE „istotnie odbiega").
- **med gain O2: 10.5 min** (review 10.4). **detour med −1.63 / p90 5.06 min** ≤ cap 8 (review uncapped p90 7.93).
- **genuine-regres: 0** — 57 λ-artefaktów (kandydat overage > served, wina λ=1.5 kolektora) ODFILTROWANYCH guardem gain≥2 (silnik overage-only ich nie adoptuje).
- Silnik KONSERWATYWNY vs review (carried-first lock + detour cap + λ-caveat) → engine improved ≤ review = kierunek bezpieczny.

### 3c. Krok 2 ON = OSOBNY replay ON↔OFF (feasibility, S1 unified ON)
`scratchpad/capz_krok2_replay.py` — 500/400 seeded worków, `check_feasibility_v2` ready-gate OFF vs ON:
- **PURE ready-anchor (QUANTILE wymuszony OFF): 0 flipów werdyktu, ΔNO=0.** R6-gate już ready-anchoruje (`r6_thermal_anchor`) → SLA-gate switch NIE dokłada rejectów, tylko **re-atrybuuje reason** (`sla_violation`↔`R6_per_order`): **48% reason-class churn** → wpływa na downstream `_kind()` (best-effort/promocja).
- **Z live QUANTILE ON: 20 flipów (4%) — WSZYSTKIE gold NO→MAYBE, ΔNO=−20.** Co-design QUANTILE (gold≤4 p80 kalibracja `_sla_gate_elapsed` przed check) czyni SLA-gate ZGODNYM z R6-gate (który gold≤4 już odzyskuje via QUANTILE). Debug: OFF rejectuje gold na SLA-gate (now-anchor, bez QUANTILE) mimo że R6-gate go odzyskuje → NIESPÓJNOŚĆ; ON usuwa ją (`eta_quantile_calibrate` capuje duże ETA do ~36-38).
- **Werdykt Kroku 2:** verdict-neutralny sam z siebie (0 flipów pure), ale 48% reason-churn = REALNA zmiana decyzji downstream + interakcja QUANTILE = gold recovery. **NIE bundlować z Krokiem 1.**

### 3d. Testy jednostkowe + mutation + kombinacje
`tests/test_o2_capz_reseq_2026_07_02.py` (**20/20 zielone**): OFF→o2_capz=None; ON swap→lower-overage argmin + A-first (ON≠OFF); cap-Z hard filter; detour block + within-cap adopt; sla-not-worse; min-gain; size-guard; paczka; metryka; kombinacje OFF-inertność {L3×L4} (at-202/203) + ON-kompozycja PACZKA/QUANTILE; Krok 2 ready-anchor source + co-design no-crash.
- **Mutation ×2 (C13, behawioralny kill):** wyłączenie cap-Z (`mca>z`→False) → `test_capz_hard_filter_blocks_over_z` PADA ✓; wyłączenie detour (`detour>max`→False) → `test_detour_cap_blocks` PADA ✓ (+ `blocked_by_cap` metryka = 2. niezależny kill).
- **Parytet bliźniaków:** single-source `_capz_reseq_plan` (feasibility+plan_recheck dziedziczą przez `simulate_bag_route_v2`) — ZERO duplikacji logiki → mutacja źródła zmienia OBA z definicji.
- **Golden route-order 13/13** (`test_route_order_golden.py` 4/4), **O2 sweep 7/7 + sla_anchor 18/18** — nietknięte.

### 3e. Regresja pełna (worktree vs kanon, ten sam mechanizm symlink)
| | passed | failed |
|---|---|---|
| kanon-symlink (baseline) | 4005 | 0 |
| worktree (mój kod, flagi OFF) + 20 nowych | 3988 (+20 capz) | 24 |

**Diff fail-setów → 24 „fail" worktree = 0 realnych regresji:**
- **23** (`test_a2_selection_shadow` 15 + `test_courier_reliability` 8) = `SkipTest("moduł nie istnieje")` — hardcode ABSOLUTNEJ ścieżki modułu pod symlinkiem (klasa C12(e)). **Stash-proof:** worktree BEZ moich zmian = te SAME 23 fail (base-invariant, nie mój kod).
- **1** `test_flag_effect_coverage::test_no_new_untested_decision_flag` = pre-merge artefakt (checker hardkoduje `SCRIPTS`/`TESTS`=kanon; widzi moje flagi ETAP4 w worktree common, ale NIE mój plik testu w kanon/tests). **Udowodnione PASS po merge:** `compute()` z `TESTS=worktree/tests` → `new_gap: []` (analogia S1 [A]).

---

## 4. Propozycja flags.json + plan flipu DWUETAPOWY (za ACK)

**flags.json (NIE wpisane — deploy za ACK):**
```json
"ENABLE_O2_CAPZ_RESEQ": false,
"ENABLE_SLA_GATE_READY_ANCHOR": false
```
(przy flipie zmienić na `true`; hot-reload. Można też stroić `O2_CAPZ_Z_MIN`/`O2_CAPZ_DETOUR_MAX_MIN`/`O2_CAPZ_MIN_GAIN_MIN` z flags.json — czytane `C.flag`.)

**Krok 1 flip (`ENABLE_O2_CAPZ_RESEQ`) — PIERWSZY, osobno:**
1. Sekwencja: merge `fix/o2-capz`→kanon (regresja post-merge zielona: flag_effect PASS) → `"ENABLE_O2_CAPZ_RESEQ": true` w flags.json (hot) → restart `dispatch-shadow` (świeży proces; plan-recheck/panel-watcher hot per tick).
2. Obserwacja (2 dni): `grep -c '"o2_capz"' shadow_decisions.jsonl` > 0 (flaga żyje); `applied>0` na workach carried; brak wzrostu R6 breach / `sla_violations` vs baseline; latencja p95 (enumeracja ON — patrz ryzyko).
3. Rollback HOT: `"ENABLE_O2_CAPZ_RESEQ": false` (bez restartu; OFF=bajt-parytet → natychmiast bezpiecznie).

**Krok 2 flip (`ENABLE_SLA_GATE_READY_ANCHOR`) — OSOBNY ACK, PO Kroku 1:**
1. **Prereq:** przegląd downstream `_kind()` (dispatch_pipeline:1247/1324 — `startswith("sla_violation")`/`"R6_per_order"`) — 48% reason-churn zmienia klasyfikację best-effort/promocji; potwierdzić że re-atrybucja NIE psuje `_feas_carry_readmit_pick`/`auto_assign_gate`.
2. `ENABLE_SLA_ANCHOR_UNIFIED` MUSI być ON (jest). Flip `"ENABLE_SLA_GATE_READY_ANCHOR": true` → restart shadow → obserwacja: reason-mix w shadow_decisions, ΔNO (spodziewane ~0 pure + gold recovery via QUANTILE).
3. Rollback HOT: flaga false.

**⚠ Sprzężenia (C3):** Krok 2 sprzężony z `ENABLE_ETA_QUANTILE_R6_BAGCAP` (live ON — gold recovery) + `ENABLE_PACZKA_R6_THERMAL_EXEMPT`. Krok 1 NIE dotyka `ENABLE_O2_READY_ANCHOR_SWEEP` (zostaje OFF; rekomendacja: NIE flipować surowego sweepu — cap-Z reseq go zastępuje w intencji). Sobota 04.07: at-202 (`ENABLE_PLAN_RECHECK_GATES`) + at-203 (`ENABLE_AVAILABLE_FROM_SINGLE_SOURCE`) — kombinacje OFF-inertne przetestowane (test 4-combo).

---

## 5. Krok 3 (PRZYGOTOWANY, NIE wykonany) — re-collect λ=0

**Cel:** podnieść replay proxy→ground-truth (usuwa caveat λ; kolektor `under_z` liczy klucz `overage + 1.5·czas_late` → overage kandydata zawyżony dla worków `czas_late>0`).

**Gdzie żyje stała:** `tools/bundle_calib_shadow.py:76` — `LAMBDA_CZAS = float(os.environ.get("BUNDLE_CALIB_LAMBDA_CZAS", "1.5"))`. Kolektor odpalany przez timer `dispatch-bundle-calib-shadow` (env z drop-inu/`Environment=` serwisu).

**Instrukcja deploy-za-ACK (zero wykonania tu):**
1. Dodać `Environment=BUNDLE_CALIB_LAMBDA_CZAS=0` do serwisu kolektora (drop-in `/etc/systemd/system/dispatch-bundle-calib-shadow.service.d/lambda-zero.conf`) → `systemctl daemon-reload`. **UWAGA:** dial env-frozen per-proces (wzorzec #9) — ustawić w env PROCESU kolektora, nie tylko modułu.
2. **NIE mieszać z żywym korpusem:** przełączyć OUT_JSONL na świeży plik (np. `bundle_calib_shadow_l0.jsonl`) ALBO wyczyścić stan `bundle_calib_shadow_state.json` — inaczej λ=1.5 i λ=0 rekordy zmieszane (skażenie).
3. Zbierać okno ≥2-3 dni napływu (worki multi-order carried), potem odpalić `tools/bundle_calib_review.py` (wskazać nowy CORPUS) → nowy werdykt ground-truth (overage bez λ-zawyżenia).
4. Porównać engine-improved% świeżego werdyktu z 7.3% (obecny konserwatywny) — powinien być ≥ (λ=0 nie zawyża overage kandydata → więcej feasible/improved).
5. Opcjonalne (kierunek już bezpieczny bez tego) — NIE blokuje flipu Kroku 1.

---

## 6. Ryzyka
- **Latencja Kroku 1 przy ON:** reseq enumeruje permutacje (bounded `O2_CAPZ_MAX_STOPS=8`) w `simulate_bag_route_v2`, wołanym per-kandydat w feasibility. OFF = zero kosztu (early return). Przy flipie: zmierzyć p95 w shadow PRZED peakiem; dla bag_after≤3 bruteforce i tak enumeruje (~zero extra), dla ortools+większych = dodatkowa enumeracja. Mitygacja gdyby bolało: obniżyć `O2_CAPZ_MAX_STOPS` lub gate `bag_after` (env).
- **Krok 2 reason-churn 48%:** re-atrybucja `sla_violation`↔`R6_per_order` przy verdict-neutralności — realnie zmienia downstream `_kind()` klasyfikację (best-effort/promocja). Wymaga przeglądu przed flipem (patrz §4 Krok 2 prereq).
- **λ-caveat (Krok 1):** replay 7.3% konserwatywny (dolne oszacowanie); Krok 3 domyka. Kierunek już bezpieczny.
- **Proxy replay Kroku 2:** syntetyczne bagi (nie ground-truth dostaw) → liczby kierunkowe; przed flipem real-shadow (log-only ready-anchor obok) dla twardych liczb.
- **`ENABLE_O2_READY_ANCHOR_SWEEP` los:** zostaje OFF/nietknięta; rekomendacja — NIE flipować (surowy sweep łamie carried-first wg review); cap-Z reseq = jej bezpieczny następca.

---

## 7. Deliverables
- Kod (branch `fix/o2-capz`): `route_simulator_v2.py` (RoutePlanV2 +drive_min/o2_capz, `_plan_from_sequence` drive-only, `_enumerate_valid_plans` ekstrakcja, `_is_paczka_ordersim`/`_capz_bag_metrics`/`_capz_reseq_plan`, wiring w `simulate_bag_route_v2`, `_count_sla_violations` ready-anchor), `feasibility_v2.py` (metryka o2_capz + SLA-loop ready-anchor + QUANTILE co-design), `plan_recheck.py` (komentarz dziedziczenia), `common.py` (2 flagi ETAP4 + stałe OFF + 4 stałe O2_CAPZ_*), `tests/test_o2_capz_reseq_2026_07_02.py` (NEW, 20), `ZIOMEK_LOGIC_REFERENCE.md` (2 wpisy flag).
- Ten raport. Skrypty dowodowe: `scratchpad/{capz_parity_fuzz,capz_replay,capz_krok2_replay,smoke_capz}.py`.
