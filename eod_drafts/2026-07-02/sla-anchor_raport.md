# S1 — konsolidacja 35-min HARD (R6 carried-age ↔ SLA) do JEDNEGO źródła z JAWNĄ kotwicą

**Fala:** SERIAL S1 (rdzeń silnika). **Branch:** `fix/sla-anchor`. **Commit kodu:** `8362c37` (na `72f37c8`).
**Data:** 2026-07-02. **Autor:** Claude (Opus 4.8). **Flip flagi = OSOBNY ACK (nie w tej fali).**
**Spec:** guard-teatr_raport.md §4/§6 (L-TEATR-1/2) + O2_bramka_odczyt_raport.md §1b (finding `feas-r6-sla-anchor-gap`) + protokół „3 bliźniaki SLA-anchor RAZEM".

---

## 1. Wynik jednozdaniowy
35-minutowy HARD (R6 ready-anchor ↔ SLA now-anchor) skonsolidowany do JEDNEGO źródła `sla_anchor.py` z JAWNĄ kotwicą, wpiętego w 3 bliźniaki RAZEM za flagą `ENABLE_SLA_ANCHOR_UNIFIED` (default OFF); **OFF = decyzje BAJT-W-BAJT** (fuzz 400 case, 0 mismatch + regresja bez nowych failów), **ON = te same decyzje + metryka obs `sla_anchor_source`** która de-maskuje R6↔SLA **bez zmiany reason** (reason karmi decyzję downstream), co zdejmuje L-TEATR-1/2: `guard_mutation_probe` — `sla_threshold_999` **SURVIVED→KILLED** i `r6_per_order_disable` **KILLED**, niezależnie (behawioralne 5/5).

---

## 2. Kluczowa decyzja projektowa: de-maskowanie OBSERWABILNOŚCIĄ, nie reorderem
**Reorder R6-przed-SLA byłby zmianą DECYZJI, nie kosmetyką** — reason bramki jest konsumowany downstream:
`dispatch_pipeline.py:1247/1324` klasyfikuje kandydatów przez `r.startswith("sla_violation")` / `"R6_per_order"` / `"R6_picked_up_delta"` (`_feas_carry_readmit_pick` LIVE-selekcja + `_feas_carry_blind_shadow`); `auto_assign_gate:186` czyta `plan.sla_violations`. Zamiana kolejności bramek → inny reason → inna klasa `_kind()` → inny zbiór `blocking`/`best_rej`/promocja = **zmiana decyzji** (niedozwolone bez replayu+ACK, poza zakresem tej fali).

Dlatego pod ON: **werdykt + reason + `sla_violations` = identyczne jak OFF**, a de-maskowanie robi **metryka** `sla_anchor_source` — naruszenie kotwicy READY (R6) i NOW (SLA) jest NIEZALEŻNIE widoczne, więc każda bramka jest killable osobnym testem. To zgodne z protokołem „refaktor → dowód bajt-identyczności = pozytyw".

---

## 3. Mapa kompletności (klasa: Feasibility/HARD + SLA-anchor 35-min)

| # | Miejsce (symbol) | Kotwica | Próg (istniejąca stała) | Dotknięte? |
|---|---|---|---|---|
| 1 | `route_simulator_v2._count_sla_violations` | NOW | `sla_minutes` (=`DEFAULT_SLA_MINUTES`=35) | ✅ ON→`sla_anchor.now_anchor`+`elapsed_min`; OFF inline |
| 2 | `feasibility_v2` SLA-loop (~1178) | NOW | `sla_minutes` | ✅ ON→to samo źródło + `now_breach` do metryki; OFF inline |
| 3 | `feasibility_v2` R6 per-order (~1054/1234) | READY (`r6_thermal_anchor`) | `BAG_TIME_HARD_MAX_MIN`=35 | ✅ ON→`ready_breach` do metryki (kotwica już jednoźródłowa `r6_thermal_anchor`) |
| 4 | `plan_recheck._o2_key` / `_sweep` / committed (761/1925) | dziedziczy `plan.sla_violations` | — | ✅ **N-D bezpośredni** — konsumuje `plan.sla_violations` liczone przez #1; parytet gwarantowany przez #1 (test twin-parytetu + 4-combo×L3) |
| — | `route_simulator_v2._compute_per_order_delivery_minutes` (READY) | READY | — | N-D — nie HARD-gate (karmi per_order/C2/O2); kotwica READY = `r6_thermal_anchor` już single-source, re-eksport w `sla_anchor.ready_anchor` |

**Bliźniaki RAZEM (protokół):** #1+#2 dzieliły POWIELONĄ inline kotwicę NOW → teraz jedna funkcja `sla_anchor.now_anchor`. Kotwica READY była już jednoźródłowa. Próg: BEZ nowej stałej 35 — `now_anchor` bierze próg z paramu `sla_minutes` (=`DEFAULT_SLA_MINUTES`), R6 z `BAG_TIME_HARD_MAX_MIN`; `hard_minutes()` czyta ten dial (INV-FEAS-R6-ONE-SOURCE).

**Nowa flaga (Załącznik A):** ✅ stała OFF (`common.py`) + ✅ `ETAP4_DECISION_FLAGS` + ✅ czytana `C.flag(...)` (NIE os.environ) + ✅ metryka `sla_anchor_source` (auto-serializacja L1.1 deny-lista) + ✅ test ON≠OFF + ✅ `flag_doc_baseline.json` (świadomy dług, doc w kodzie+raporcie) + ✅ registry/etap4/doc-coverage/serializer testy zielone (20/20).

---

## 4. Dowody

### 4a. OFF = bajt-parytet (fuzz 400 case)
`scratchpad/parity_fuzz.py` — 400 losowych scenariuszy (bag 0-2, ready 0-60min wstecz, picked_up 5-50min, legi 60-2400s), OFF vs ON: **MISMATCH=0** na `(verdict, reason, plan.sla_violations, r6_per_order_violations)`. Metryka `sla_anchor_source`: obecna **ON=400/400, OFF=0/400** (ON≠OFF).

### 4b. Regresja pełna (my code, ZIOMEK env, flaga default OFF) vs env-baseline (kanon code, ten sam env)
Metoda: identyczny mechanizm env dla obu (symlink `pkgroot/dispatch_v2`→worktree vs →kanon), diff zbiorów failów.

| | passed | failed | skipped | xfailed |
|---|---|---|---|---|
| env-baseline (kanon code) | 3935 | 28 | 26 | 10 |
| **moje (worktree code)** | **3951** | **27** | 26 | 10 |

**Diff zbiorów failów:**
- **[A] Regres wniesiony przez mój kod = TYLKO `test_flag_effect_coverage::test_no_new_untested_decision_flag`** — artefakt PRZED-MERGE: checker hardkoduje `SCRIPTS=kanon`, więc widzi moją flagę w `common` (worktree) ale NIE mój plik testowy (jeszcze nie w kanon/tests). **Udowodnione: PASSES po merge** — `compute()` z widokiem worktree → `new_gap: []`, flaga wykryta jako `tested` (nazwa flagi w scalonych `tests/*.py`). Analogicznie do 23 pre-merge failów `test_courier_reliability` (hardcode ścieżki, klasa C12(e)).
- **[B] Resolved przez mój kod:** 2× L-TEATR (`test_r6_ready_breach_visible...`, `test_sla_only_boundary...`) — na kanon padają (brak metryki), u mnie zielone.
- Pozostałe 26 failów (`test_a2_selection_shadow` 15, `test_courier_reliability` 8, `script_run` 3) = **IDENTYCZNE w OBU runach** = artefakty env ZIOMEK-symlink (subprocess/script-runner + hardcode ścieżki). Potwierdzone osobnym runem ZIOMEK→KANON tych plików (17 failed na kodzie bazowym). **Nie mój kod.**

Bilans: +15 (nowy plik testów) +2 (L-TEATR resolved) −1 (flag_effect pre-merge) = +16 passed; 28−2+1=27 failed. Spójne.

### 4c. ON = pozytywny wpływ (de-maskowanie — probe przed/po)
`tools/guard_mutation_probe.py` (worktree code):

| Mutacja | Bramka | BEHAWIOR. przed (guard-teatr) | BEHAWIOR. po (S1) |
|---|---|---|---|
| `sla_threshold_999` (DEFAULT_SLA_MINUTES 35→999) | sla_violation | **SURVIVED** | **KILLED** |
| `r6_per_order_disable` (`if X`→`if False and X`) | R6_per_order | KILLED | **KILLED** |
| bagcap/pickup_far/hard_tier | (inne) | KILLED×3 | KILLED×3 |

**BEHAWIORALNE zabiły 5/5 (było 4/5).** `sla_threshold_999` KILLED niezależnie przez `test_sla_only_boundary_kills_threshold_under_unified` (picked_up 40min + `ENABLE_SLA_PREEXISTING_BYPASS=False` → czysty SLA-only, R6 nie dotyczy niesionego); `r6_per_order_disable` KILLED przez `test_r6_carried_age_isolated_hard_reject` (ready-anchor breach, SLA nie widzi). Żadna mutacja NIE jest łapana przez test drugiej = niezależna killowalność.

### 4d. Parytet bliźniaków (jedno źródło)
`test_single_source_mutation_propagates_to_all_twins`: mutacja `sla_anchor.now_anchor` → OBA (`route_sim._count_sla_violations` przez `plan.sla_violations` I `feasibility` SLA-loop przez reason) zmieniają się razem. Golden route-order **13/13** (4 testy zielone) — kolejność jazdy nietknięta.

### 4e. 4 kombinacje mine × L3 (at-202/203, sob 04.07)
`test_sla_count_stable_across_unified_x_l3` (parametryzowany {OFF,ON}×{L3 OFF,ON}): `plan.sla_violations` (konsumowane przez L3 compare-and-keep) **stabilne** we wszystkich 4 — moja flaga = bajt-parytet liczenia sla, `ENABLE_PLAN_RECHECK_GATES` (L3) nie dotyka liczenia sla, `ENABLE_AVAILABLE_FROM_SINGLE_SOURCE` (L4) też nie. Semantyka L3 `l3_regen_*`/bramki NIETKNIĘTA.

### 4f. C13 mutation ×2 na NOWEJ ścieżce
`test_exceeds_boundary_is_strict_gt` (próg: `>`→`>=` → PADA) + `test_now_anchor_precedence_pickup_over_picked_over_now` (polaryzacja/kolejność kotwicy → PADA). Suity: nowy plik **15/15**, behawioralny **15/15**.

---

## 5. Propozycja wpisu `flags.json` + doc (NIE wpisane do żywego — do deploy-ACK)
`flags.json` (dodać przy flipie, hot-reload):
```json
"ENABLE_SLA_ANCHOR_UNIFIED": false
```
Doc-snippet do `ZIOMEK_LOGIC_REFERENCE.md` (tabela flag; przy deploy):
> `ENABLE_SLA_ANCHOR_UNIFIED` — S1 2026-07-02. Konsolidacja 35-min HARD (R6 ready ↔ SLA now) do `sla_anchor.py` z jawną kotwicą; 3 bliźniaki RAZEM. **OFF = decyzje bajt-w-bajt; ON = te same decyzje + metryka obs `sla_anchor_source`** (ready/now-breach niezależnie widoczne → de-maskowanie L-TEATR-1/2 bez zmiany reason). Zdejmuje L-TEATR-1/2 (`guard_mutation_probe` 5/5). KANON=flags.json.

---

## 6. Plan flipu (za ACK, off-peak, ETAP 6-7)
1. Dopisać `"ENABLE_SLA_ANCHOR_UNIFIED": true` do `flags.json` (hot-reload; cross-proces: shadow/plan-recheck/czasowka czytają ten sam kanon).
2. Restart `dispatch-shadow` (żeby świeży proces; plan-recheck/panel-watcher hot-reload per tick).
3. Obserwacja 2 dni: `grep -c sla_anchor_source shadow_decisions.jsonl` > 0; parytet `sla_violations` vs baseline (brak dryfu werdyktów); metryka `ready_breach`/`now_breach` sensowna.
4. **Rollback HOT:** `"ENABLE_SLA_ANCHOR_UNIFIED": false` w `flags.json` (bez restartu; hot-reload) — bo OFF = bajt-parytet, powrót natychmiastowy i bezpieczny.
5. Post-flip cleanup (osobny sprint): usunąć inline gałęzie OFF (zostawić tylko źródło).

---

## 7. Ryzyka
- **flag_effect_coverage pre-merge red** = artefakt (checker hardkoduje kanon); PASSES po merge (dowód §4b [A]). Koordynator: regresja kanonu po merge będzie zielona dla tej pozycji.
- **conftest `_SCRIPTS_ROOT` env-overridable** (`ZIOMEK_SCRIPTS_ROOT`, default=KANON) — dodane dla walidacji worktree (C12(e)); default identyczny = **zero wpływu na bieg kanoniczny** koordynatora.
- **ON≠OFF = metryka, nie decyzja** — świadome (reason karmi decyzję downstream). Gdyby przyszła fala chciała reason-level de-maskowania / reorder → to ZMIANA DECYZJI, wymaga replayu ON↔OFF + ACK, osobny sprint.
- Flip NIE zmienia semantyki O2 (`ENABLE_O2_READY_ANCHOR_SWEEP` osobna flaga, nietknięta) ani at-202/203.
- Env-artefakty regresji (a2/courier_reliability/script_run pod ZIOMEK-symlink) = szum pomiaru pre-merge, znikają w kanonie (bez symlinka).

---

## 8. Deliverables
- Kod (commit `8362c37`): `sla_anchor.py` (NEW), `common.py`, `route_simulator_v2.py`, `feasibility_v2.py`, `tests/conftest.py`, `tests/test_feasibility_guards_behavioral.py` (L-TEATR xfail→pass), `tests/test_sla_anchor_unified.py` (NEW, 15), `tools/flag_doc_baseline.json`.
- Ten raport.
