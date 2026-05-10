# Sprint Pn 06.05.2026 — Plan (combined Geocoding + Faza 7 + TASK A debug)

**Status:** DRAFT (awaiting Adrian ACK pre-sprint kickoff)
**Created:** 2026-05-05 wieczór EOD
**Time-box:** ~7-8h CC + ~50 min Adrian
**Sprint scope:** Geocoding Phase 1 (4h, Components 1-3) + Faza 7 Plan B + TASK A debug combined (~3h shared evaluator divergence) + TASK A live observation triage (30-60 min)
**Pre-conditions:** `geocoding_adjacency_draft_2026-05-06.md` reviewed, Horodniany/Stanisławowo manual lat/lon supplied, `candidate_decisions_20260506.jsonl` morning fires available, `lekcja_74_candidate.md` (Wt EOD draft) lub adhoc note
**Rollback:** per-flag, zero restart wymagany dla większości komponentów

---

## Section 1 — Sprint scope inventory

Sprint Pn obejmuje 3 strumienie połączone wspólnym wątkiem **evaluator-vs-main divergence** (Lekcja #74 candidate dziś 16:23 UTC). 5/5 fires post-flag-flip 10:35 UTC: TASK A `czasowka_proactive` evaluator zwracał NO_CANDIDATE, ale main dispatcher faktycznie przypisał cid dla 3/3 oids w 6-19 min post-fire. Wniosek: oba evaluatory (LGBM Faza 7 z 100% `all_bag_zero` fallback + TASK A z 5/5 NO_CANDIDATE) eksponują systemic pattern — evaluator subset filter różni się od main candidate generation. **Wspólny upstream point** (candidate object construction, fleet_snapshot reuse, bag_size populacji) nadaje się do combined debug session.

| # | Task | Effort | Owner | Pre-condition |
|---|------|--------|-------|---------------|
| 1 | Adjacency map review (Adrian) | 15 min | Adrian | `geocoding_adjacency_draft_2026-05-06.md` |
| 2 | Horodniany + Stanisławowo manual lat/lon (Adrian) | 5 min | Adrian | none |
| 3 | TASK A live obs triage (czy nowe fires Pn rano) | 30-60 min | CC + Adrian | `candidate_decisions_20260506.jsonl` morning |
| 4 | Geocoding Phase 1 — Component 1 zones_registry | 1.5h | CC | adjacency map (#1) |
| 5 | Geocoding Phase 1 — Component 2 geocoding upgrade | 1h | CC | none |
| 6 | Geocoding Phase 1 — Component 3 drop-zone outside-city | 1h | CC | Component 1 (#4) |
| 7 | Geocoding Phase 1 — testy + py_compile + smoke | 30 min | CC | Components 1-3 |
| 8 | Faza 7+TASK A debug — code review (LGBM all_bag_zero + evaluator divergence) | 45 min | CC | none |
| 9 | Faza 7+TASK A debug — production data analysis | 60 min | CC | events.db read access |
| 10 | Faza 7+TASK A debug — instrumentation deploy + obs | 30 min | CC + Adrian | code review (#8) |
| 11 | Faza 7+TASK A debug — RC verification + raport | 30 min | CC | items 9-10 |
| 12 | Daily report Pn 06.05 + memory update | 30 min | CC | items 1-11 |

**Sumy:**
- Adrian: ~50 min (15 + 5 + 30 obs aktywne)
- CC: ~7h (4h Geocoding + ~3h combined debug)
- Total wall-clock: ~7-8h z lunch peak buffer

**Decyzja prioritization (jeśli sprint over-budget):**

1. MUST: Geocoding Phase 1 (Components 1-3 + tests) — bloker dla Cz/Pt sprintów
2. MUST: TASK A live obs triage — Lekcja #74 evidence continuation, czy nowe fires Pn rano dokumentują pattern
3. SHOULD: Faza 7+TASK A combined debug — defer do Cz 07.05 jeśli Block 3 over-time (ale ryzyko: Faza 7 re-baseline 15.05 wymaga 1-week obs po fix → defer = push re-baseline do 22.05)
4. MAY: instrumentation deploy (#10) — może zostać w design-only mode jeśli czasu mało

Jeśli Adrian unavailable Block 1: pivots — CC zaczyna od Block 5 (Faza 7+TASK A debug NIE wymaga Adrian dla #8 + #9) zamiast Geocoding (#4 wymaga adjacency map).

---

## Section 2 — Sprint sequencing

Pn 06.05 uwzględnia peak blackout windows (lunch 12-14 UTC = 14-16 Warsaw, dinner 17-20 UTC = 19-22 Warsaw). NIE deploy w peak. Block 4 = pure observation window dla lunch peak.

**Block 1 — Pre-flight (Adrian + CC, 30 min, 06:30-07:00 UTC):**

1. Adrian review adjacency map (15 min) — Option A strict 2km vs Option B liberal 4-5km decision; per-quadrant batch ACK; 5 auto-pairs ≤2km confirm; 119 borderline 2-5km Adrian per-pair Y/N
2. Adrian provide manual lat/lon dla Horodniany + Stanisławowo (5 min) — Adrian wpisuje koordynaty bezpośrednio do `geocoding_adjacency_draft_2026-05-06.md` Missing section
3. CC pre-flight diagnostic (10 min) — services 7/7 active, schedule cache fresh, `candidate_decisions_20260506.jsonl` growing (po 00:00 UTC auto-rotate), no ERROR/WARN ostatnie 12h, flags.json runtime check (CZASOWKA_PROACTIVE/T0/T40/T50=true z Wt)

**Block 2 — TASK A live obs triage (CC + Adrian, 30-60 min, 07:00-08:00 UTC):**

- CC reads `candidate_decisions_20260506.jsonl` post-rotate (Sr morning) + remaining `candidate_decisions_20260505.jsonl` post-flip 10:35 UTC tail; filter `source="czasowka_proactive"` + `ts >= 10:35 UTC`
- Per-fire analysis: oid, trigger_min (T-50/T-40/T0), evaluator verdict (NO_CANDIDATE vs proposal sent), main dispatcher action (cid przypisany w X min post-fire, lub kept w `id_kurier=26 Koordynator`)
- **Lekcja #74 evidence continuation:** czy nowe fires Pn rano (jeśli były) replicate Wt 5/5 NO_CANDIDATE pattern lub czy single-day artefakt? Cross-check evaluator output vs main pipeline w tym samym oknie czasowym (snapshot delta)
- Anomaly detection: czy proposals sensible? Bartek-level confirmation pattern? Lunch peak Wt regression?
- Output: TASK A live obs raport (~300 słów) + decision points dla Adrian
- Adrian decyduje: kontynuacja flag-on, rollback, lub adjustments dla T-50/T-40/T0

**Block 3 — Geocoding Phase 1 (CC, 4h, 08:00-12:00 UTC):**

- 08:00-09:30 — Component 1 zones_registry (1.5h) — hierarchical model (district→quadrant→region) z multi-tenant scaffolding (Białystok primary, Warsaw/Restimo placeholder); fix hardcoded "Białystok" w `dispatch_pipeline.py:421` oraz `geocoding.py`/`panel_client.py`; adjacency map zaaplikowana
- 09:30-10:30 — Component 2 geocoding upgrade (1h) — locality validation (raw.lokalizacja.name = source of truth zamiast city default), region-level rejection (jeśli Nominatim zwraca region>3km off-center → fail-loud), single-token fallback eliminate (Burger Station 'Station' anti-pattern z `feedback_nominatim_single_token_dangerous`)
- 10:30-11:30 — Component 3 drop-zone outside-city (1h) — feasibility gate (jeśli drop-zone outside Białystok bounding box → flag `outside_city=True` + radius multiplier 1.3-1.5×); integracja z Component 1
- 11:30-12:00 — Tests + py_compile + smoke (30 min) — 30+ nowych testów custom-runner (zones_registry: 12, geocoding upgrade: 10, outside-city: 8); smoke E2E na 5 reprezentatywnych adresach (Bagatela, Filipowicza, Horodniany, Wasilków, Kleosin); zero regression baseline pytest

**Block 4 — Lunch peak observation (12-14 UTC = 14-16 Warsaw):**

- Peak window — NIE deploy
- Continue obs TASK A live czasówki (jeśli były problemy w Block 2)
- Buffer dla unexpected issues (panel_watcher CSRF outage, schedule cache stale, etc.)
- Optional: code review (#8) jeśli wszystko stabilne — 30 min nie-deploy task

**Block 5 — Faza 7+TASK A combined debug (CC, ~3h, 14:00-17:00 UTC):**

- 14:00-14:45 — Code review (45 min) — `ml_inference.py:170-177` early-return dla bag=0 dominated pool + `czasowka_proactive/evaluator.py` `_filter_candidates_for_proposal` (TASK A NO_CANDIDATE upstream); cross-check obu paths z dispatch_pipeline candidate construction; potwierdzić że oba używają tego samego fleet_snapshot źródła (Lekcja #74 working hypothesis: shared upstream divergence point)
- 14:45-15:45 — Production data analysis (60 min) — events.db (lub shadow_log) read 502 emisji LGBM 02-05.05 + 5 NO_CANDIDATE fires TASK A 05.05 14:24-15:32 UTC + sample fresh fires Pn morning (jeśli były); per-decision pool composition (bag=0 count, bag>=1 count, evaluator filter rejection reason); cross-table evaluator vs main pipeline candidate sets dla 3 oids 470805/470821/470808
- 15:45-16:15 — Instrumentation deploy + obs (30 min) — flag-gated `LGBM_DEBUG_BAG_TRACE=False` + `CZASOWKA_DEBUG_FILTER_TRACE=False` defaulty; logger emits per-decision `bag_size_dist`, `early_return_reason`, `pool_eligible_count`, `evaluator_filter_rejected_count` + reason codes; deploy z explicit Adrian ACK; 15-min obs window post-flip
- 16:15-16:45 — RC verification + raport (30 min) — czy LGBM teraz emituje predictions dla bag>=1 pool? Czy TASK A evaluator filter ujawnia rejection reasons konsystentne z main dispatcher action? Raport `faza_7_plan_b_rc_2026-05-06.md` + `task_a_evaluator_divergence_rc_2026-05-06.md` (lub combined raport)

**Block 6 — Daily report + EOD (CC, 30 min, 17:00-17:30 UTC):**

- Sprint summary (deploys, decisions, follow-ups)
- Memory update (project_sprint_06_05 entry; Lekcja #74 promotion candidate→final jeśli evidence sufficient)
- Pending dla Adrian (Cz 07.05 TASK D pre-condition, Pt 08.05 (re-baseline status) lub re-baselined 15.05)
- Lekcje candidates inventory (jeśli ≥3)

**Total:** 06:30 → 17:30 UTC = 11h wall-clock z lunch peak buffer. Active CC effort ~7h, Adrian ~50 min.

---

## Section 3 — Risk register

5 risks identified, P × I matrix, mitigation per-risk:

| # | Risk | P × I | Mitigation |
|---|------|-------|------------|
| R1 | Geocoding Phase 1 architectural decision lock-in (zones_registry hierarchy multi-tenant scope) | M × H | Pre-Adrian align Sr rano (15 min slot pre-Block 3) — zones_registry hierarchical model schema OK pre-deploy? Multi-tenant scaffolding scope (placeholder vs fully-wired)? |
| R2 | TASK A debug requires hot path code change w shared evaluator/main pipeline upstream | L × H | Flag-gated instrumentation NIE deploy direct fix; shadow obs first (Block 5 instrumentation z `LGBM_DEBUG_BAG_TRACE` + `CZASOWKA_DEBUG_FILTER_TRACE`); fix decision deferred do Cz 07.05 po data analysis Block 5 |
| R3 | Sprint over-budget (>7h CC effort) | M × M | Block 5 (Faza 7+TASK A) jest LOW priority vs Block 3 (Geocoding) — defer Block 5 do Cz 07.05 jeśli Block 3 over-time; alternatywnie Block 5 → 1.5h skinny version (code review + data analysis only, instrumentation deploy do Cz) |
| R4 | Adrian unavailable Block 1 (adjacency review + manual lat/lon) | L × M | Defer Geocoding Phase 1 do popołudnia; CC pivots do Faza 7+TASK A debug morning (Block 5 nie wymaga Adrian dla #8 + #9); Block 3 przesunięty na 14:00-18:00 UTC po Adrian wraca |
| R5 | Lekcja #74 finding implies broader systemic issue niż single evaluator | M × M | Document each evaluator path (LGBM Faza 7 / TASK A czasowka_proactive / main V3.27 dispatch_pipeline) candidate generation; identify shared upstream point (fleet_snapshot, bag_state, Candidate object construction); cross-check dispatch_pipeline `_v327_eval_courier` vs evaluator subset filter w obu paths |

**Acceptance criteria:** sprint MUST nie regression na production (TASK A czasówka stable, dispatch-shadow zero crash), SHOULD wszystkie 3 strumienie tknięte (Geocoding LIVE, TASK A obs raport, Faza 7+TASK A combined instrumentation deployed lub explicitly deferred z reason).

---

## Section 4 — Pre-flight checklist

```markdown
## Pre-flight Pn 06.05 (CC, 5 min @ 06:30 UTC)

- [ ] Services 7/7 active:
  - dispatch-telegram.service (PID stable from yesterday's restart)
  - dispatch-shadow.service (active)
  - dispatch-panel-watcher.service (active)
  - dispatch-czasowka.timer (active, 1-min interval)
  - dispatch-shift-notify.timer (active, 1-min interval, 06:00 UTC pierwszy REAL T-60 START)
  - dispatch-state-reconcile.timer (active)
  - monitor-419.service (active or alert-only)
- [ ] Schedule cache fresh (<10 min mtime na schedule_cache.json)
- [ ] candidate_decisions_20260506.jsonl growing (post 00:00 UTC auto-rotate; expected fires after 06:00 UTC dla SHIFT T-60)
- [ ] No ERROR/WARN ostatnie 12h dispatch-telegram (`journalctl --since "12h ago" -p err`)
- [ ] No ERROR/WARN ostatnie 12h dispatch-shadow
- [ ] flags.json runtime check (czytelne JSON, NIE corrupted):
  - CZASOWKA_PROACTIVE_ENABLED=true
  - CZASOWKA_T0_ALERT_ENABLED=true, CZASOWKA_T40_ENABLED=true, CZASOWKA_T50_ENABLED=true (z Wt)
  - SHIFT_NOTIFY_ENABLED=true (z 04.05)
  - 5 SHIFT_* flagi true (z 04.05)
- [ ] Branch decision: stay sprint-05-05-tb-phase2-task-a (linear) lub new sprint-06-05?
  → Adrian decision Block 1 (jeśli Wt EOD merged Option B → master clean, new sprint branch zalecane)
- [ ] Backup files retention: yesterday's tags + commits w master (jeśli Option B merged Wt EOD; `git tag --list 'sprint-05-05-*' | wc -l` expect 8)
- [ ] Adrian present + adjacency map ready do review (geocoding_adjacency_draft_2026-05-06.md)
- [ ] Horodniany + Stanisławowo manual lat/lon ready do paste (5 min)
- [ ] Disk space check (>10% free dla shadow logs + tests)
- [ ] git status clean (nic uncommitted z Wt poza eod_drafts/2026-05-05/ jeśli Option A)
```

Jeśli któryś check fail: STOP-and-ack, NIE proceed do Block 1 zadań.

---

## Section 5 — Sprint outcome metrics

Definicja success Pn 06.05:

**MUST (deal-breakers — jeśli któryś fail, sprint nie zamknięty):**

1. Geocoding Phase 1 LIVE z Components 1-3 deployed (zones_registry + geocoding upgrade + outside-city); flag `ENABLE_GEOCODING_PHASE_1=True` flip explicit ACK po smoke OK
2. TASK A live obs raport (Lekcja #74 evidence continuation); Adrian decision na kontynuacja/rollback/adjustments
3. Faza 7+TASK A combined debug RC verified (per hipoteza: shared upstream divergence vs independent bugs); RC raport zapisany lub explicitly deferred z reason
4. 0 production incidents (zero crash dispatch-shadow lub dispatch-telegram, zero ERROR explosion w journalctl, zero rollback flag)

**SHOULD (jakość — sprint silniejszy jeśli osiągnięte):**

5. 30+ nowych testów Geocoding Phase 1 PASS (zones_registry 12 + geocoding upgrade 10 + outside-city 8); zero regression na 934 baseline pytest
6. Sprint w time-box ≤7h CC effort (jeśli >7h: Block 5 defer do Cz 07.05 zaakceptowany)
7. Memory + daily report updated (project_sprint_06_05 entry, daily_report_2026-05-06.md submitted)
8. Lekcja #74 candidate → final (Adrian ACK po Block 2 obs + Block 5 RC)

**MAY (nice-to-have — dodatkowe wartości):**

9. Faza 7 instrumentation flag flip post-RC verification (jeśli RC OK i Adrian ACK)
10. TASK D D.6 design ACK (Telegram welcome message — Adrian decide czwartek 07.05 vs V3.30); jeśli Block 5 buffer pozwala

**Sprint NOT done jeśli:** Geocoding Phase 1 NIE deployed (#1) lub TASK A obs raport NIE napisany (#2) lub combined debug RC NIE verified (#3) lub production regression (#4 fail).

---

## Section 6 — 5 unknowns Adriana przed sprint kickoff Pn 06.05

5 unknowns wymagających ACK przed Block 1 startu (najlepiej Pn rano @ 06:00 UTC = 08:00 lokalnie):

1. **Branch decision:** stay `sprint-05-05-tb-phase2-task-a` (linear) lub new `sprint-06-05-geocoding-faza7-debug`?
   - REC zależy od Wt EOD merge decision: jeśli Option B (merged dziś) → new sprint-06-05 branch z master baseline; jeśli Option A (defer Pt 08.05) → kontynuacja sprint-05-05 (linear, accumuluje TASK B + TASK A + Geocoding + Faza 7 debug)
   - Adrian wybór: A (linear sprint-05-05) lub B (new sprint-06-05 branch)?

2. **Sprint over-budget mitigation:** jeśli Block 3 (Geocoding) over-time (>4h zamiast 4h), defer Block 5 (combined debug) do Cz 07.05?
   - REC: TAK (Geocoding bloker dla Components 4-6, Faza 7 re-baseline 15.05 ma jeszcze 9 dni buffer)
   - Counter-argument: Lekcja #74 evidence requires fresh data analysis Pn rano — defer = push o 1 dzień, ale kolejny dzień fires może rozsuc context
   - Adrian wybór: A (defer Block 5) lub B (cut Block 3 scope dla Block 5) lub C (split — Block 5 skinny w Pn + full debug Cz)?

3. **Faza 7+TASK A instrumentation deploy method:** flag-gated `LGBM_DEBUG_BAG_TRACE=False` + `CZASOWKA_DEBUG_FILTER_TRACE=False` defaulty w common.py (deploy w prod z 15-min obs window) czy NIE deploy w prod (offline analysis tylko z events.db / shadow_log)?
   - REC: flag-gated default False + explicit flip ACK po code review (Lekcja #72 granular flag rollback pattern)
   - Adrian wybór: A (deploy flag-gated) lub B (offline-only analysis)?

4. **TASK A regression threshold:** jeśli wczorajsze fires (od 10:35 UTC) + Pn morning fires pokazują anomalies (>X% NO_CANDIDATE evaluator divergence vs main dispatcher), kiedy rollback flagi CZASOWKA_T50_ENABLED/T40_ENABLED?
   - REC: rollback jeśli ≥3 NO_CANDIDATE fires Pn dla orderów które main dispatcher faktycznie przypisał w 6-19 min post-fire (= Wt 5/5 pattern continues); single anomaly = adjust nie rollback
   - Adrian wybór: A (≥3 threshold) lub B (≥5 threshold) lub C (subjective Adrian call po Block 2)?

5. **Adjacency map decision method:** Option A (strict 2km + per-quadrant batch — REC z `geocoding_adjacency_draft_2026-05-06.md`) lub Option B (liberal 4-5km — więcej coverage ale więcej false-positives)?
   - REC: Option A (5 auto-pairs ≤2km bezpieczne, 119 borderline 2-5km wymagają per-pair confirm)
   - Counter-argument: Option B daje szerszą siatkę ale ryzyko Grabówka k. Bieszczad-style outliers (Lekcja #73 robust median + 30km outlier filter już mitiguje, ale per-pair confirm bezpieczniejszy)
   - Adrian wybór: A (strict) lub B (liberal) lub C (hybrid: strict primary + per-quadrant batch dla borderline)?

**Default behavior** jeśli Adrian unavailable Block 1: REC (A) per-decision, Block 1 pivots do Block 5 morning (Faza 7+TASK A debug code review + data analysis), Geocoding Phase 1 przesunięty popołudniu po Adrian wraca.

---

**End of plan.** Lekcja przeniosła się — sprint plans z 6 sekcjami (scope/sequencing/risks/preflight/metrics/decisions) są więcej actionable niż pure todo list. Reuse pattern dla Cz 07.05 + Pt 08.05.
