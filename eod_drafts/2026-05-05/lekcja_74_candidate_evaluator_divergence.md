---
name: Lekcja #74 — Evaluator-vs-Main-Dispatcher candidate divergence
description: Multiple evaluator paths (LGBM shadow, TASK A czasówka, main dispatcher V3.27) widzą fleet candidates inaczej; LGBM 100% all_bag_zero fallback, TASK A 100% NO_CANDIDATE w 5/5 fires gdy main znalazł kandydata 3/3 — pattern systemic; root cause TBD (threshold/filter/snapshot timing); debug sprint Pn 06.05
type: feedback
---

# Lekcja #74 — Evaluator-vs-Main-Dispatcher Candidate Divergence

## Pryncypium

Gdy multiple evaluator paths czytają pojedyncze prod data (fleet, candidates, scores) ale każdy buduje własny pool / własny filter / własny score threshold, **divergence systemic** = symptom architectural flaw, NIE bug pojedynczy.

**Reguła:** każdy evaluator path obok main dispatcher MUSI używać shared candidate generation upstream (np. wspólny `candidate_pool_provider.py`) z explicit divergence points (filter/threshold) udokumentowanymi i testable. Brak shared upstream = systemic divergence inevitable.

## Evidence (2026-05-05)

### LGBM Faza 6 shadow — 100% fallback

- **502 emisje LGBM_SHADOW** w 4 dni (02.05-05.05)
- **502/502 (100%)** hitting `all_bag_zero` early-return w `ml_inference.py:170-177`
- **ZERO real LGBM predictions**, **ZERO agreement data points** vs Ziomek
- Pre-condition matrix Faza 7 Pt 08.05: 2/4 fail → **NO-GO**
- Source: `eod_drafts/2026-05-05/faza_7_go_nogo_criteria.md`

### TASK A czasówka evaluator — 5/5 NO_CANDIDATE

5 fires post-flag-flip 10:35 UTC do 16:23 UTC dziś — wszystkie zwróciły `NO_CANDIDATE`, ale main dispatcher (V3.27 stack) znalazł kandydata 11-19 min później dla 3 z 3 unique oid:

| Time UTC | oid | TASK A trigger | TASK A | Main `COURIER_ASSIGNED` | Δ |
|----------|-----|----------------|--------|-------------------------|---|
| 14:24:46 | 470805 | T-50 | NO_CANDIDATE | 14:43:22 cid=471 | +18.6 min |
| 14:35:25 | 470805 | T-40 | NO_CANDIDATE | (already assigned) | — |
| 14:40:34 | 470821 | T-50 | NO_CANDIDATE | 14:51:49 cid=370 | +11.3 min |
| 15:21:08 | 470808 | T-50 | NO_CANDIDATE | 15:37:27 cid=508 | +16.3 min |
| 15:31:58 | 470808 | T-40 | NO_CANDIDATE | (already assigned) | — |

Ten sam fleet, ten sam oid, identical timing window — **różny outcome**.

## ⚠️ POST-LIVE-OBS REVISION 16:30 UTC — H-G hipoteza NEW

**KRYTYCZNE odkrycie z czasowka_eval_log.jsonl analysis:**

Po szczegółowej analizie eval_log dla 470852 (post-15:32 UTC, T-50 window 49-51 ±1 min powinien fire 15:41-15:43 UTC):

| ts | mins_to_pickup | scheduler decision |
|----|----------------|-------------------|
| 15:33:02 | 58.5 | WAIT "no MAYBE candidate" |
| 15:38:18 | 53.3 | WAIT |
| **15:43:44** | **47.8** | WAIT — JUMP **53.3 → 47.8** = **MISSED T-50 window** |
| 15:49:08 | 42.4 | WAIT |
| 15:54:23 | 37.2 | KOORD "≤40min + zero MAYBE" |

**Insight:** scheduler eval frequency dla pojedynczego oid ~5-5.5 min (nie per-minute). T-50 window ±1 min nie matchuje tej kadencji → **fires niedeterministyczne**.

**H-G hipoteza (WINNING candidate):** TASK A trigger detection (T-50/T-40 ±1 min tolerance) **nie matchuje** scheduler eval frequency ~5 min/oid → fires reliability ~50-70%. To NIE jest evaluator divergence (V3.24-B i TASK A widzą TO SAMO "no MAYBE candidate") — to jest **trigger window precision bug**.

**Dodatkowe finding:** TASK A "Brak kandydata" messages REDUNDANT z V3.24-B KOORD signal + Adrian manual dispatch flow. Adrian widząc panel + V3.24-B KOORD już ma actionable info; TASK A duplikuje + addsa noise w grupie ziomka.

**Fix path Pn 06.05 (3 options):**
- (A) **Increase tolerance:** `CZASOWKA_TRIGGER_TOLERANCE_MIN` 1 → 3-5 min (match scheduler eval frequency)
- (B) **Decouple from scheduler eval:** per-tick worker check (każda minuta) niezależnie od scheduler decision matrix
- (C) **Consider TASK A obsolete:** jeśli V3.24-B KOORD enough actionable signal → wyłączyć TASK A T-50/T-40, zachować TYLKO T-0 alert (real value-add)

**Original hipotezy H-D/E/F (threshold/filter/snapshot timing) DOWN-RANKED** post H-G discovery — V3.24-B legacy i TASK A widzą identyczny `no MAYBE candidate` w eval_log, więc H-D (threshold) + H-E (feasibility filter) + H-F (snapshot timing) nie są root cause.

## Pattern Recognition (legacy hipotezy — pre-H-G discovery)

Evaluator paths (LGBM shadow, TASK A czasówka) używają **DIFFERENT candidate set lub DIFFERENT filter** niż main dispatcher.

**Operacyjnie:**
- Adrian widzi w grupie ziomka "🚨 Brak kandydata" message po fires TASK A
- ALE main dispatcher faktycznie przypisuje kuriera 11-19 min potem
- Mental confusion + grupowy noise + nieoperacyjny signal (Adrian musiałby manualnie crosscheck z panelem)

**Architectural smell:** każdy evaluator buduje fleet snapshot / candidate pool / filter z różnymi parametrami:
- Stale param (`stale_max_sec`)
- Threshold (`MIN_PROPOSAL_SCORE=60`)
- Feasibility gate (`MAYBE only` vs `MAYBE+NO penalty`)
- Bag state lifecycle (czy `bag_size` propagated z live `bag_state.size`)
- Tick interval (TASK A 1-min cron, main per-event burst)

## Action items

- **Pn 06.05 debug sprint** — `eod_drafts/2026-05-05/faza_7_debug_plan_pn_06_05.md` combined LGBM + TASK A divergence (3-4h)
- **Hipoteza tree** 6 hipotez:
  - H-A bug LGBM bag_size → live propagation issue
  - H-B reality LGBM Fix C eliminuje bundle scenarios
  - H-C race LGBM bag_state lag
  - H-D threshold TASK A `CZASOWKA_MIN_PROPOSAL_SCORE=60` too restrictive
  - H-E feasibility TASK A `MAYBE` strict vs main `MAYBE+NO` permissive
  - H-F snapshot TASK A different fleet view than main
- **Bridge analysis:** H-A + H-F mogą mieć wspólny root cause (candidate generation divergence) → fix shared upstream
- **Interim mitigation Pn 06.05 morning:** `CZASOWKA_T0_ALERT_ENABLED=false` (flag flip 5s, T-0 NIE actionable signal — Adrian decision Section 6 Unknown #5 plan B)

## Anti-Patterns (do unikania)

1. **Multiple parallel evaluators bez shared candidate generator.** Każdy evaluator self-builds fleet snapshot + candidate pool. Divergence inevitable, debug pain heavy. Fix: extract `candidate_pool_provider.py` jako single source.
2. **Threshold/filter divergent bez explicit documentation.** TASK A `MIN_PROPOSAL_SCORE=60` ustawiony na "intuicja Adriana czerwiec 2025" bez audit czy main akceptuje 30-60. Decision criteria parity między evaluator a main MUSI być explicit + testable.
3. **Live messages do user (Telegram) na bazie evaluator state bez crosscheck z main dispatcher state.** Adrian widzi "Brak kandydata" choć main faktycznie przypisuje 5 min później. Fix: evaluator messages MUSZĄ mieć "main dispatcher will retry independently" disclaimer LUB zero messages dla NO_CANDIDATE outcomes (przekaż NO_CANDIDATE jako signal do learning_log NIE Telegram).
4. **Deploy nowy evaluator path bez integration test "compare to main dispatcher 100 historical decisions, agreement >=80%".** Faza 6 LGBM smoke test był 31ms per-decision unit test, NIE replay vs main. TASK A miał 58 unit tests ale ZERO compare-to-main integration. Fix: każdy nowy evaluator MUSI mieć replay test z explicit agreement target.

## Cross-Refs

- **Lekcja #71** "Decoupled State Lifecycles + Test Isolation" — same root cause class (decoupled lifecycles → divergence). Lekcja #74 jest extension: Lekcja #71 = state lifecycle divergence (test pollution), Lekcja #74 = candidate evaluation divergence (production paths).
- **Lekcja #58** "Z2 jakość ZAWSZE" — 6h multi-evaluator integration test może wydawać się over-engineering, ale eliminuje 3-4h debug sprint Pn 06.05 + 18h grupowy noise + Faza 7 NO-GO.
- **Lekcja #57** "pre-step verify feature parity training/production" — analogiczne: training/production divergence wykryte przed deploy. Ten sam wzorzec dla evaluator/main.
- **TASK A `czasowka_proactive` package** — `czasowka_proactive/evaluator.py` + `scheduler.py` (Pn 06.05 Krok 1B audit target)
- **Faza 6 LGBM shadow** — `ml_inference.py:170-177` `all_bag_zero` early-return (Pn 06.05 Krok 1A audit target)
- **`faza_7_design_spec_2026-05-02.md`** — Faza 7 design wymagał agreement>=75% gate; bez Lekcja #74 fix gate nigdy nieosiągalny

## Implementation Checklist (template dla nowych evaluator paths)

Przy dodawaniu evaluator obok main dispatcher:

- [ ] Shared `candidate_pool_provider.py` lub equivalent — single source dla fleet snapshot + Candidate construction
- [ ] Explicit divergence points udokumentowane (threshold/filter/scoring)
- [ ] Integration test "replay 100 historical decisions, agreement >=80%" PRZED deploy
- [ ] Live messages (Telegram) do usera MUSZĄ uwzględnić main dispatcher state lub mieć explicit disclaimer
- [ ] Observability: log every evaluator-vs-main divergence z `(oid, evaluator_verdict, main_verdict, delta_ms)` triplet
- [ ] Default OFF flag dla każdego evaluator (granular Lekcja #72)
- [ ] Rollback path: flag flip = 5s (zero restart)
- [ ] Disagreement learning: każdy `evaluator=NO, main=YES` case → learning_log entry dla post-hoc debug

## Operational Impact

- **Faza 7 GO target shifted:** Pt 08.05 → re-baseline Pt 15.05 (1-week obs post-fix). Może shift do 22.05 lub 12.06 (Section 5 decision matrix `faza_7_debug_plan_pn_06_05.md`).
- **TASK A wartość zerowa do post-debug:** 5/5 NO_CANDIDATE = każda fire = grupowy noise. Interim mitigation T0_ALERT off rekomendowane.
- **Architectural debt:** shared `candidate_pool_provider.py` jako module = ~1-2 day design + impl + migrate 3 evaluator paths (LGBM, TASK A, main). Decyzja TBD post-debug — może okazać się że H-D (threshold tuning) sufficient bez full architectural refactor.

## Status

- **Lekcja #74 candidate** — Adrian ACK przed promotion z candidate → final w memory.
- **Source documents:** `faza_7_debug_plan_pn_06_05.md`, `faza_7_go_nogo_criteria.md`, dziś live obs (events.db 14:24-15:32 UTC).
- **Promotion path:** post Pn 06.05 debug sprint, jeśli RC confirmed → final Lekcja #74 z confirmed root cause + fix path. Jeśli RC mixed/none → Lekcja #74 zostaje "open question" + redirect do architectural sprint (1-2 day separate session).
