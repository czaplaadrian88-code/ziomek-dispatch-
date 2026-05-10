# Faza 7 Plan B — Combined Debug Sprint (LGBM `all_bag_zero` + TASK A `NO_CANDIDATE` divergence) — Pn 06.05.2026

**Sprint type:** debug + root cause verification (READ-ONLY na prod kod do RC; instrumentation flag-gated, post-ACK only)
**Time-box:** ~3-4h aktywne + 30 min buffer (extended z pierwotnego 2-3h LGBM-only po dziś-popołudniowym TASK A divergence finding)
**Pre-condition:** `dispatch-shadow.service` + `dispatch-czasowka.service` running, `candidate_decisions_*.jsonl` writes captured w real-time, events.db read access
**Adrian decision:** Plan B adopted 2026-05-05 (per `faza_7_go_nogo_criteria.md` + Agent #4 finding 100% fallback + dziś 14:24-15:32 UTC live obs TASK A divergence)
**Branch:** TBD (Unknown #1 — Section 6)
**Re-baseline target:** Pt 15.05.2026 (1-week obs post-fix; może shift z hipotezy mix scenario)

---

## Section 1: Context

### Faza 7 design recap

Faza 7 (`faza_7_design_spec_2026-05-02.md`) implementuje **Phase 2** architektury 4-phase BC + guardrails + continuous learning roadmap (`architektura_lgbm_continuous_learning.md`):

- **Flag flip:** `ENABLE_LGBM_PRIMARY=1` — LGBM staje się primary scorer, Ziomek demote do alt-comparator.
- **Winner promotion:** `dispatch_pipeline.py` post-feasible filter promuje LGBM top-1 jako BEST.
- **ALT Explorer:** Ziomek primary ranking renderowany jako "alt line" w Telegram gdy disagreement.
- **Telegram UX:** V3.19i reason buttons rozszerzone (`LGBM_AGREE_WRONG`, `LGBM_DISAGREE_OK`, `LGBM_DISAGREE_INTUITION`).
- **Fallback safety:** `LGBM_PRIMARY_FALLBACK_TO_ZIOMEK=1` default ON.
- **Rollback:** flip `ENABLE_LGBM_PRIMARY=0` → 5s revert.

### Pre-condition matrix (RAW, 2026-05-05 13:06 UTC)

Z `faza_7_go_nogo_criteria.md` Section 2:

| Metric | Target | Current | Status |
|--------|--------|---------|--------|
| Agreement rate (LGBM top1 == Ziomek top1) | >=75% | **N/A** (502/502 fallback) | **FAIL** |
| Fallback rate | <=10% | **100.0%** (502/502 `all_bag_zero`) | **CRITICAL FAIL** |
| Latency p95 LGBM eval | <=100ms | 0.04ms (early-return path only) | **PASS z asterisk** |
| Sample size | >=200 decisions | 502 emisje w 4 dni | **PASS** |

**Verdict raw:** **NO-GO** — 2/4 fail.

### TASK A live observation finding (NEW dziś 16:23 UTC)

5 fires post-flag-flip 10:35 UTC do 16:23 UTC, **wszystkie zwróciły `NO_CANDIDATE`** mimo że main dispatcher (V3.27 stack) znalazł kandydata 6-19 min później dla 3 z 3 oid:

| Time UTC | oid | TASK A trigger | TASK A verdict | Main dispatcher COURIER_ASSIGNED | Δ minut |
|----------|-----|----------------|----------------|----------------------------------|---------|
| 14:24:46 | 470805 | T-50 | NO_CANDIDATE | 14:43:22 → cid=471 | +18.6 |
| 14:35:25 | 470805 | T-40 | NO_CANDIDATE | (already assigned) | — |
| 14:40:34 | 470821 | T-50 | NO_CANDIDATE | 14:51:49 → cid=370 | +11.3 |
| 15:21:08 | 470808 | T-50 | NO_CANDIDATE | 15:37:27 → cid=508 | +16.3 |
| 15:31:58 | 470808 | T-40 | NO_CANDIDATE | (already assigned) | — |

**Pattern:** ten sam fleet, ten sam oid, identical timing window — różny outcome między evaluator (TASK A) a main dispatcher (V3.27 stack). 5/5 fires NO_CANDIDATE, 3/3 unique oid znalazły kandydata 11-19 min post-fire.

### Pattern recognition — wspólny architectural smell

Zarówno **LGBM Faza 6 shadow** jak i **TASK A czasówka evaluator** są równoległymi evaluator paths obok main dispatcher. Oba pokazują **systemic divergence**:

- LGBM 502/502 (100%) hitting `all_bag_zero` early-return → ZERO real predictions
- TASK A 5/5 (100%) hitting `NO_CANDIDATE` → ZERO actionable proposals
- Main dispatcher 3/3 oid → znajduje kandydata bez problemu

Sprint Pn 06.05 łączy oba debug streams jako jedną sesję — wspólna prawdopodobnie przyczyna **candidate generation lub filter logic divergence** między evaluator paths a main dispatcher (różne snapshoty fleet, różne thresholdy, różne feasibility criteria).

### Plan B Pn 06.05 + re-baseline 15.05

Adrian zaakceptował debug sprint Pn 06.05 ~3-4h (extended z 2-3h po dziś-popołudniowym TASK A finding). Re-baseline Faza 7 GO/NO-GO Pt 15.05 po 1-week obs post-fix. Faza 7 sprint Pt 08.05 deferred — nowe target zależy od mix hipotez (Section 5 decision matrix).

---

## Section 2: Hipoteza tree

6 hipotez podzielone na 2 grupy (LGBM + TASK A) plus bridge analysis na dole. Każda wymaga PROVEN/REJECTED evidence przed sprint exit.

### Grupa LGBM `all_bag_zero` (3 hipotezy)

#### H-A — Bug: `bag_size` field NIE poprawnie populated z candidate object

**Kod:** `ml_inference.py:170-177`:
```python
all_bag_zero = all((getattr(c, "bag_size", 0) or 0) == 0 for c in candidates)
if all_bag_zero:
    result.fallback_reason = "all_bag_zero"
    return result
```

**Hipoteza:** Candidate object w `dispatch_pipeline.py` post-feasible NIE ustawia `bag_size` field z live source (`bag_state.size`). Real bag>=1 candidates istnieją w `bag_state` ale na poziomie LGBM eval pokazują `bag_size=0`.

**Evidence wymagane:** grep `dispatch_pipeline.py` setter sites; sprawdzić czy `bag_size = bag_state.get_courier_bag(cid).size` lub stale field; cross-ref `bag_state.update()` lifecycle.

**Test:** instrument 10 candidates losowo, log `(c.bag_size, bag_state.size, fleet_snapshot.bag[cid])` triplet. Jeśli divergent >5% → BUG.

#### H-B — Reality: Fix C bundle cap eliminuje bundle scenarios

**Kod:** `dispatch_pipeline.py` Fix C (`v3_28_fix_c_live_2026-05-01.md`) — bundle deliv_spread hard cap 8km, FILOZ-3 gate.

**Hipoteza:** Fix C praktycznie eliminuje bundle scenarios — candidate pool dla NEW order to dominate empty (`bag_size=0`) couriers.

**Evidence:** SQL events.db count NEW_ORDER 7d; per-event policz max(bag_size) z candidate_decisions_*.jsonl. Jeśli >95% mają max_bag=0 → H-B confirmed.

**Cross-ref:** Faza 5 finding bundle bias 44.6% acc, n=648 only 1.2% test.

#### H-C — Race: bag_state propagation lag vs LGBM eval timing

**Hipoteza:** `bag_state.update()` ma lag w propagacji do struct'ów konsumowanych przez LGBM eval. Race: panel_watcher emit → state_machine update → bag_state.update() → fleet_snapshot rebuild → Candidate construct.

**Evidence:** instrument timestamps `bag_state.size last_updated_ts` per cid + `lgbm_eval_call_ts`. Jeśli >5% decisions mają delta_ms<0 → race confirmed.

**Bonus:** korelacja z ENABLE_PANEL_BG_REFRESH cykl 900s.

### Grupa TASK A `NO_CANDIDATE` divergence (3 hipotezy)

#### H-D — Threshold: `CZASOWKA_MIN_PROPOSAL_SCORE=60` too restrictive

**Kod:** `czasowka_proactive/evaluator.py` (do read confirm) `_filter_candidates_for_proposal` aplikuje minimum proposal score threshold; main dispatcher V3.27 może akceptować scores w 30-60 range gdy nic lepszego nie ma.

**Hipoteza:** Próg 60 odrzuca kandydata którego main dispatcher zaakceptuje bo brak wyższych alternatyw. Dla 3 oid (470805/470821/470808) main faktycznie przypisał cid (471, 370, 508) — czyli **byli kandydaci** w pool, tylko score < 60.

**Evidence:** Pull `candidate_decisions` dla każdego z 3 oid w window T-50 fire +/- 30 min. Histogram score per candidate. Jeśli main-assigned cid ma score 30-60 (poniżej TASK A threshold) → H-D confirmed.

**Test:** Symulacja replay TASK A evaluator z `CZASOWKA_MIN_PROPOSAL_SCORE=40` na 3 oid. Czy zwróciłaby cid który main wybrał?

#### H-E — Feasibility filter: TASK A wymaga `verdict==MAYBE` strict

**Kod:** TASK A evaluator filter wymagający feasibility `MAYBE` gate (NIE `NO`). Main dispatcher może dopuszczać `NO` z penalty (soft).

**Hipoteza:** TASK A evaluator hard-rejects kandydatów `verdict=NO` (np. R6 35min violations, R1 deliv_spread, R-DECLARED-TIME). Main dispatcher V3.27 widzi tych samych kandydatów ale akceptuje gdy gradient soft-penalty wystarcza (R-PRIORYTETÓW: waste > bliskość > R4).

**Evidence:** Pull feasibility_v2 verdicts dla candidates w window. Jeśli main-assigned cid ma `verdict=NO` z TASK A filter view ale `verdict=MAYBE` z main view → asymmetric filter.

**Risk:** różne instances feasibility_v2 wywoływane z różnymi context (np. czas_kuriera frozen for TASK A vs flexible for main).

#### H-F — Fleet snapshot timing: różne fleet snapshot

**Hipoteza:** TASK A czasówka tickuje co 1 min (lub własny interval), main dispatcher tickuje per-event burst-driven. Jeśli TASK A evaluator buduje fleet_snapshot z `panel_client.get_panel_snapshot(stale_max_sec=N)` z different stale param niż main → różne candidate sets visible.

**Evidence:** grep `czasowka_proactive/` dla fleet_snapshot construction. Compare `stale_max_sec` lub equivalent param vs `dispatch_pipeline.py`. Jeśli różne → confirmed.

**Test:** za każdym z 5 fires, dump fleet_snapshot CIDs visible vs main's CIDs visible @ same `event_ts +/- 5s`. Set difference.

### Bridge analysis (cross-grupa)

H-A/B/C i H-D/E/F mogą mieć **wspólny root cause**: dispatch_pipeline candidate generation diverges między evaluator paths. W szczególności:

- **H-A + H-F** parallel: oba zakładają że Candidate object lub fleet snapshot konsumowany przez evaluator NIE odpowiada main's view → wymagają audit single-source-of-truth na candidate generation.
- **H-D + H-E** komplementarne: filter logic w evaluator pre-rejectuje candidates których main akceptuje → wymagają audit decision criteria parity.
- **H-B + H-C** unique do LGBM: cechy specyficzne dla bag-aware ranking, mniej istotne dla TASK A scope (TASK A nie używa LGBM).

**Architectural finding (potential):** wymaga shared `candidate_generator.py` lub `candidate_pool_provider.py` modułu który zwraca **identical** Candidate set do każdego evaluator. Aktualnie każdy evaluator buduje własny pool z różnymi parametrami → divergence systemic.

---

## Section 3: Investigation plan

### Krok 1 — Code review (~45 min)

Reading-only, ZERO edits. Two focus areas równolegle (CC self-organize):

**1A: LGBM bag_size lifecycle**
- `ml_inference.py:160-178` — `all_bag_zero` predicate (baseline).
- `dispatch_pipeline.py` — Candidate object construction. Grep `bag_size=` lub `Candidate(` setter sites.
- `bag_state.py` — `update()` lifecycle + `get_courier_bag()` return type.
- `route_simulator_v2.py` — czy `_v327_eval_courier` overrides bag_size?
- `shadow_dispatcher.py` LOCATION A+B serializer — czy bag_size from same Candidate co LGBM eval?

**1B: TASK A evaluator vs main dispatcher**
- `czasowka_proactive/evaluator.py` — `_filter_candidates_for_proposal` lub equivalent. Identify thresholds + feasibility gates.
- `czasowka_proactive/scheduler.py` (lub trigger module) — fleet_snapshot construction.
- `dispatch_pipeline.py` — analogous candidate filter (V3.27 main path) — porównaj decision criteria.
- `feasibility_v2.py` — `MAYBE` vs `NO` semantics, czy context-dependent (czas_kuriera frozen vs flexible).
- `common.py` — flagi `CZASOWKA_*` (`CZASOWKA_MIN_PROPOSAL_SCORE`, `CZASOWKA_MAYBE_ONLY`, etc.)

**Output:** notes file `_faza_7_code_review_pn_06_05.md` — bag_size data flow diagram + TASK A vs main candidate filter comparison table.

### Krok 2 — Production data analysis (~60 min)

SQL + jsonl analysis na production logs.

**LGBM Queries:**
- **Query A:** `SELECT count(*) FROM events WHERE event_type='NEW_ORDER' AND ts >= '2026-05-02';` baseline.
- **Query B:** sample 50 oid z 502 LGBM_SHADOW emisji 02-05.05, parse candidate set, compute `max(c.bag_size)` distribution.
- **Query C:** dla każdego sampled oid, lookup `bag_state` snapshot @ event_ts vs Candidate.bag_size. Divergence histogram.
- **Query D:** count Fix C demote events 7d (`bundle_deliv_spread_cap_demote` lub equivalent).

**TASK A Queries:**
- **Query E:** Pull `candidate_decisions_2026-05-05.jsonl` for oid=470805 / 470821 / 470808 w window `T-50 fire ts +/- 5 min`. Compare candidate set + scores per oid.
  - Sub-question: **Czy TASK A evaluator widział cid 471 / 370 / 508 (main-assigned)?**
  - Jeśli TAK → score histogram, czy threshold problem (H-D)?
  - Jeśli NIE → fleet snapshot timing problem (H-F).
- **Query F:** Compare verdicts feasibility_v2 dla main-assigned cids vs TASK A view. Same `verdict` lub mismatch?
- **Query G:** Czas wywołania TASK A evaluator vs main dispatcher's NEW_ORDER processor — fleet_snapshot timestamp delta.

**Output:** `_faza_7_data_analysis_pn_06_05.md` — confirmed/rejected per hipoteza z evidence.

### Krok 3 — Instrumentation deploy (~30 min, conditional)

WYŁĄCZNIE jeśli Krok 1+2 sugerują H-A/H-C/H-F (timing-related) — wymagają live snapshot. Dla H-B/H-D/H-E sufficient z static data.

**Add debug log do `ml_inference.py:170` area:**
```python
if _common.LGBM_DEBUG_BAG_TRACE:
    log.debug(
        f"LGBM_BAG_TRACE oid={oid} all_bag_zero={all_bag_zero} "
        f"bag_sizes={[(c.cid, getattr(c, 'bag_size', 'MISSING')) for c in candidates]}"
    )
```

**Add debug log do `czasowka_proactive/evaluator.py:_filter_candidates_for_proposal`:**
```python
if _common.CZASOWKA_DEBUG_TRACE:
    log.debug(
        f"CZASOWKA_FILTER oid={oid} all_candidates={[c.cid for c in candidates]} "
        f"feasible={[c.cid for c in feasible]} threshold={score_threshold} "
        f"main_assigned_cid={main_cid_or_None}"
    )
```

**Flag-gated** `LGBM_DEBUG_BAG_TRACE=False` + `CZASOWKA_DEBUG_TRACE=False` default w `common.py` (~obok `ENABLE_LGBM_SHADOW`).

**Per-krok workflow:** backup → edit → py_compile → import check → tests → ACK → flag flip True → 24h shadow obs → flip back False post-debug.

**Rollback:** flag flip = 5s, no restart (per-tick read pattern, Lekcja #72).

### Krok 4 — Root cause verification (~30 min)

Per H-A/B/C/D/E/F confirmed:
- Document w `_faza_7_root_cause_pn_06_05.md` — confirmed hipoteza + evidence + recommended fix path + Faza 7 re-baseline impact.
- Cross-ref Section 5 decision matrix.
- Plus: TASK A interim mitigation decision (Section 6 Unknown #5).

---

## Section 4: Sprint logistyka

- **Czas:** Pn 06.05 ~3-4h aktywne (combined LGBM + TASK A debug; extended z original 2-3h LGBM-only). Plus 30 min buffer.
- **Pre-condition:** `dispatch-shadow.service` + `dispatch-czasowka.service` running, `candidate_decisions_*.jsonl` writes captured.
- **Time-box gates:**
  - Code review 45 min (1A LGBM + 1B TASK A parallel; STOP jeśli przekracza, pivot na data first)
  - Data analysis 60 min hard limit (LGBM Queries A-D + TASK A Queries E-G)
  - Instrumentation 30 min conditional (skip jeśli H-B/H-D/H-E sufficient)
  - RC verification 30 min (RC + interim mitigation decision)
  - Total ~165 min = ~2.75h + 30 min buffer = 3.25h sprint window
- **Branch decision:** stay sprint-05-05-tb-phase2-task-a vs new sprint-06-05-debug? — Unknown #1.
- **Rollback:**
  - instrumentation regresja → flag flip False, zero restart (Lekcja #72)
  - code review reveals broader scope (e.g. shared candidate_generator pivot) → STOP, ESCALATE multi-day investigation
- **READ-ONLY constraint:** prod kod tylko grep+read poza Krok 3.
- **venv:** `/root/.openclaw/venvs/dispatch/bin/python`
- **Tests baseline:** 934 PASS / 20 pre-existing FAIL. Krok 3 instrumentation MUSI utrzymać 934 PASS + +1-2 unit tests dla flag gate.
- **Post-sprint commits:** ZERO commits poza Krok 3 instrumentation (jeśli applied). Tag opcjonalny `faza-7-debug-instrumentation-2026-05-06` po ACK.

---

## Section 5: Decision matrix

| Hipoteza verified | Confirmation criteria | Action | Faza 7 re-baseline timeline |
|---|---|---|---|
| **H-A bug LGBM** | `c.bag_size != bag_state.size` >5% (Query C) | Fix dispatch_pipeline Candidate construction; live `bag_state.size` propagation. | 15.05 (Pt) — 1-week obs |
| **H-B reality LGBM** | <5% NEW_ORDER mają candidate bag>=1 (Query B) | Faza 7 design pivot. Opcje: (a) target 50% (subset), (b) ALT-EXPLORER advisory, (c) retrain v2 single-order quality signal. | 22.05 — 2-week pivot decision |
| **H-C race LGBM** | >5% decisions delta_ms<0 (instrumentation) | Sync primitive (mutex/queue) w bag_state→Candidate factory. | 22.05 — 1-week post sync fix |
| **H-D threshold TASK A** | Main accepts score 30-60 frequently (Query E histogram) | Lower `CZASOWKA_MIN_PROPOSAL_SCORE=60→40` lub adaptive (auto-tune % top main scores). | 12.05 (Pn) — 1-week obs lower threshold |
| **H-E feasibility TASK A** | Main MAYBE+NO mix (Query F mismatches) | Loose filter w evaluator (allow `verdict=NO` z penalty) OR tighten main analogously. Risk: brak decyzji "loose/tight" wymaga business rule check Adrian. | 12.05 (Pn) lub later jeśli rule conflict |
| **H-F snapshot TASK A** | Different fleet_snapshot CIDs (Query G set diff) | Sync evaluator z main → shared `candidate_pool_provider.py` module. | 22.05 — 2-week design + impl |
| **Mixed (H-A + H-F)** | Both verified — wspólny architectural smell | **Sequential debug:** najpierw cheaper fix (H-A), potem shared architecture (H-F). H-A może self-resolve H-F gdy używa shared upstream. | 29.05 — 4-week sequential |
| **Mixed (H-D + H-E)** | TASK A specific filter logic problem | Adrian decision: relax TASK A lub tighten main? Business rules trade-off. | 19.05 — 2-week post Adrian decision |
| **None confirmed** | <5% evidence each | ESCALATE — full architecture review LGBM + TASK A integration (1-2 day deep dive). | 12.06 — 4-week full review |

---

## Section 6: 5 unknowns dla Adriana przed sprint kickoff Pn 06.05

### Unknown #1 — Branch decision

**Context:** sprint-05-05-tb-phase2-task-a aktualnie zawiera 8 tagów + 5 LIVE deploys (TASK B Phase 2 + TASK A + Issue #1).

**Opcje:**
- (A) **Stay linear** — debug commits dorzucone do tej samej branchy. Branch = "kitchen sink" do 15.05.
- (B) **New `sprint-06-05-debug` (isolated)** — branch off od sprint-05-05 HEAD. Czystszy scope.

**Recommended:** (B) isolated. Justification: debug sprint discrete z explicit re-baseline gate, NIE semantically powiązana z TASK B/A.

### Unknown #2 — Instrumentation flag deployment

**Context:** Krok 3 wymaga `LGBM_DEBUG_BAG_TRACE` + `CZASOWKA_DEBUG_TRACE` flagi. Czy deploy w current branch (default OFF, hot toggle gdy Adrian w trakcie sesji) lub poczekać na separate sprint?

**Opcje:**
- (A) **Deploy w prod common.py default False** — toggleable hot via flags.json. Działa bez restart (per-tick fresh reads, Lekcja #72). Audit trail w log.
- (B) **NIE deploy w prod** — debug tylko w test fixture z synthetic candidates.

**Recommended:** (A). Lekcja #72 wspiera (zero restart, hot toggle). Test fixture nie da prawdziwych production timings dla H-C/H-F race scenarios.

### Unknown #3 — Re-baseline timing

**Context:** decision matrix daje 12.05 (H-D/E single fix) → 15.05 (H-A) → 22.05 (H-B/C/F) → 29.05 (mixed) → 12.06 (none).

**Opcje:**
- (A) **Hard 15.05** — fixed deadline. Akceptuje surowsze metrics jeśli H-B/C/F.
- (B) **Adaptive per hipoteza** (recommended) — date depends on confirmed root cause.

**Recommended:** (B). Z2 supremacy "jakość ZAWSZE". Re-baseline na słabej evidence ryzykuje second false-positive GO.

### Unknown #4 — Sprint over-budget Pn 06.05 (combined task)

**Context:** Pn 06.05 ma już Geocoding Phase 1 (4h, Components 1-3 — `daily_report_draft.md`). Combined debug ~3-4h. Total = 7-8h.

**Opcje:**
- (A) **Splitować dzień** — debug rano (3-4h), Geocoding popołudniu (4h). Risk: cognitive fatigue (Lekcja QA-8 4h+).
- (B) **Łączyć w 7-8h sprint** — explicit time-box per task, 30 min buffer. Adrian musi zatwierdzić rozszerzony budget.
- (C) **Defer Geocoding Phase 1 → Wt 07.05** — Pn 06.05 = tylko debug. TASK D Cz 07.05 → Pt 09.05.
- (D) **Defer debug → Cz 07.05** — Pn 06.05 = Geocoding. TASK D → Pt 09.05.

**Recommended:** (A) split z explicit 30 min lunch break + RSS checkpoint po Faza 7 debug end. Ale jeśli Adrian preferuje Z2 fresh-head dla critical debug → (C) defer Geocoding.

### Unknown #5 — TASK A interim mitigation Pn 06.05 morning (przed debug)

**Context:** od dziś 14:24 UTC, każdy fire TASK A wysyła do grupy "🚨 NO_CANDIDATE" lub "info-only T-0". Adrian/Bartek widzą message → reagują → później main dispatcher faktycznie przypisuje cid → mental confusion + grupowy noise.

**Opcje:**
- (A) **Wyłączyć T0_ALERT** (info-only NIE actionable) — `CZASOWKA_T0_ALERT_ENABLED=false` flag flip 5s. T-50/T-40 zostają (są actionable Tak/Nie).
- (B) **Wyłączyć cały TASK A** — `CZASOWKA_PROACTIVE_ENABLED=false` flag flip. Brak proposals do post-debug Pn 06.05 evening.
- (C) **Rebuild messages** bardziej helpful — "❌ Brak kandydata w naszym filtrze TASK A — main dispatcher prawdopodobnie znajdzie kogoś w 5-15 min". Code change w `templates.py`, restart wymagany dla Python module-level.
- (D) **Status quo do Pn 06.05** — Adrian/Bartek toleruje noise 18h, debug Pn 06.05 ustali RC.

**Recommended default:** (A) wyłączyć T0_ALERT (flag flip 5s, najmniejsza zmiana, T-0 to NIE actionable signal). T-50/T-40 zachowane bo TBD czy wszystkie 5/5 NO_CANDIDATE są systemic czy 3/3 sample artifact. Jeśli sobota 06.05 rano dalsze fires NO_CANDIDATE > 50% → opcja (B).

---

## Section 7: Sprint readiness checklist (CC self-check przed Pn 06.05)

- [ ] `_faza_7_code_review_pn_06_05.md` template (empty) gotowy
- [ ] `_faza_7_data_analysis_pn_06_05.md` template (empty) gotowy
- [ ] `_faza_7_root_cause_pn_06_05.md` template (empty) gotowy
- [ ] events.db read access verified — `sqlite3 /path/events.db "SELECT count(*) FROM events;"`
- [ ] `candidate_decisions_*.jsonl` paths confirmed (grep patches w `shadow_dispatcher.py`)
- [ ] Adrian Unknowns #1-#5 ACK before kickoff
- [ ] Time-box gates explicit (set 45/60/30/30 min limits jako alarm w TODO)

---

## Sources

- `eod_drafts/2026-05-05/faza_7_go_nogo_criteria.md` — pre-decision report z pre-condition matrix + 502 emisje stats
- `eod_drafts/2026-05-05/sprint_pn_06_05_plan.md` — Section 1 sprint scope inventory tasks 8-11 (Faza 7 debug stream)
- TASK A live obs raw (events.db dziś 14:24-15:32 UTC) — 5 fires NO_CANDIDATE samples
- Memory `faza_7_design_spec_2026-05-02.md` — Faza 7 architecture spec
- Memory `project_faza_6_lgbm_shadow_implementation_2026-05-01.md` — Faza 6 shadow + 6 fallback paths
- Memory `project_faza_5_lgbm_training_2026-05-01.md` — bundle bias 44.6% acc finding
- Memory `project_v3_28_fix_c_live_2026-05-01.md` — Fix C bundle deliv_spread cap LIVE
- Memory `architektura_lgbm_continuous_learning.md` — 4-phase roadmap
- `dispatch_v2/ml_inference.py:160-178` — `all_bag_zero` early-return path
- `dispatch_v2/dispatch_pipeline.py` — Candidate construction TBD (Krok 1A)
- `dispatch_v2/bag_state.py` — bag_state lifecycle TBD (Krok 1A)
- `dispatch_v2/czasowka_proactive/evaluator.py` — TASK A `_filter_candidates_for_proposal` TBD (Krok 1B)
- `dispatch_v2/feasibility_v2.py` — MAYBE/NO semantics TBD (Krok 1B)
- `dispatch_v2/common.py` — `LGBM_*` + `CZASOWKA_*` flag definitions
- `/etc/systemd/system/dispatch-shadow.service.d/override.conf` — `ENABLE_LGBM_SHADOW=1` LIVE od 02.05
- Companion: `eod_drafts/2026-05-05/lekcja_74_candidate_evaluator_divergence.md` (Lekcja #74 candidate)
