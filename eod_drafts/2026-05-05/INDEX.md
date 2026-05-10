# EOD Drafts INDEX — 2026-05-05

> **POST-LIVE-OBS UPDATE 16:30 UTC — Lekcja #74 H-G hipoteza dodana:** czasowka_eval_log analysis pokazuje że scheduler eval frequency ~5 min/oid (per-oid) JUMPS mins=53.3→47.8 dla 470852 — **MISSED T-50 window 49-51 ±1 min**. To znaczy że TASK A trigger detection ma **window precision bug** — fires niedeterministyczne, depend on scheduler eval timing. Lekcja #74 H-G: *"Scheduler eval frequency mismatch z TASK A T-50/T-40 window precision (±1 min) → fires reliability ~50-70%"*. Plus V3.24-B legacy i TASK A widzą TO SAMO ("no MAYBE candidate") — NIE divergence z main, tylko **TASK A redundant z V3.24-B KOORD signal + Adrian manual dispatch flow**. Fix Pn 06.05: tolerance 1→3-5 OR per-tick decoupling OR consider TASK A obsolete jeśli V3.24-B KOORD enough actionable.

Drafts catalog dla integration end-of-day przez main CC.

## Files

| # | File | Purpose |
|---|------|---------|
| 1 | `daily_report_draft.md` | ~600w daily report — sprint summary, bug fixes, features, activations, diagnostics, sequencing tygodnia, time budget, pending |
| 2 | `memory_project_sprint_05_05.md` | Memory project entry z frontmatter `type: project`; audit trail (commits + tagi) + statystyki + activations + pending |
| 3 | `memory_pointer_line.md` | 1-line pointer entry do dorzucenia jako linia 1 w MEMORY.md (auto-memory) |
| 4 | `claude_md_taskd_taske_wytyczna.md` | 3 podsekcje do `dispatch_v2/CLAUDE.md` — TASK D + TASK E + Wytyczna #1 (4-checkbox pre-implementation review) |
| 5 | `lekcja_72_candidate.md` | Lekcja #72 candidate — granular flag-based rollback; frontmatter `type: feedback`; cross-refs #71/#58/#34 |
| 6 | `geocoding_adjacency_draft_2026-05-06.md` | Phase 1 Component 5 input (Sr 06.05) — auto-build z `geocode_cache.json`+`districts_data.py`; 5 auto-pairs ≤2km + 119 borderline 2-5km + 28 inter-satellite ≤3km; updated z **Adrian Decision Method** sekcją (Option A strict 2km + per-quadrant batch REC vs Option B liberal 4-5km) + Missing section dla Horodniany/Stanisławowo manual lat/lon |
| 7 | `lekcja_73_robust_median_pattern.md` | Lekcja #73 candidate — robust median + 30km outlier filter centroid pattern; reusable dla multi-region scaling (Warsaw/Restimo/Bolt); evidence Grabówka k. Bieszczad + Zaścianki w Pomorzu auto-detected |
| 8 | `_adjacency_compute.py` | Helper script — compute satellite + district centroids z geocode_cache + haversine distance pairs; outputs `_adjacency_data.json` |
| 9 | `_adjacency_data.json` | Raw adjacency data (centroids + distance pairs) generated z `_adjacency_compute.py`; consumed by `_build_draft_md.py` |
| 10 | `_build_draft_md.py` | Helper script — render `_adjacency_data.json` jako `geocoding_adjacency_draft_2026-05-06.md` z tabelami |
| 11 | `task_d_courier_api_audit.md` | TASK D pre-condition audit (Cz 07.05) — courier-api endpoint discovery; **CRITICAL FINDING:** NIE MA register endpointu, courier-api jest READ-ONLY consumerem `kurier_ids.json`+`kurier_piny.json`; D.4/D.5 redesign 4-step→3-step (JSON files-only); 5 unknowns dla Adriana z recommended defaults |
| 12 | `integration_commands.sh` | EOD bash script — Adrian executes ~19:00 UTC po ACK Lekcji; 3 manual steps embedded (MEMORY pointer prepend, CLAUDE.md append, daily report cp) + auto cp lekcje + git commit + tag `sprint-05-05-end-of-day-2026-05-05`; defensive pre-flight check + conditional Lekcja #73 add |
| 13 | `eod_checklist.md` | Adrian's pre-exec checklist (5-10 min review + 5 min exec) — drafts review checkboxes + Adrian decisions (Lekcja promotion Y/N, Bartek DM, Sprawa #1, branch merge timing) + pending agent drafts inventory + exec sequence + contingencies |
| 14 | `sprawa_1_response_template.md` | Sprawa #1 9 mappings response (5 unmapped + 4 partial) deploy ~17:00 UTC orthogonal do EOD flow |
| 15 | `_sprawa1_test_*.txt` (5 files) | Sprawa #1 parser test inputs — happy path / missing tier / duplicate cid / typo name / tier case |
| 16 | `faza_7_go_nogo_criteria.md` | Pre-decision report Faza 7 GO/NO-GO (target Pt 08.05); pre-condition matrix + observed shadow data 502 emisji 02-05.05 (100% all_bag_zero fallback, 0 real LGBM predictions, 0 agreement data points); rekomendacja **NO-GO + plan B debug sequence** (ENABLE_LGBM_SHADOW=1 LIVE od 02.05 via override.conf, ale `ml_inference.py:170-177` early-return dla bag=0 dominated pool); 5 unknowns dla Adriana |
| 17 | `czasowka_observability_monitor.py` | TASK A multi-source observability monitor (state file + candidate_decisions + learning_log + journalctl czasowka/telegram); CLI `--watch` (poll 30s, print on change) lub `--snapshot` (one-shot); READ-ONLY, ~340 LoC |
| 18 | `czasowka_snapshot_11_30.txt` | Pre-fire baseline snapshot przed pierwszym REAL T-50 fire dziś wieczorem (#470756 ~16:21 UTC) — 0 orders w state file (nie istnieje yet), 55 historical czasowka_proactive entries z poranka, 0 ERROR/WARN ani w czasowka ani telegram service journal (5-min window) |
| 19 | `czasowka_observability_README.md` | Operating instructions dla monitora — start/stop watcher, output redirection (tee/nohup), change-detection signature, expected first-fire flow (5 kroków visibility), ograniczenia (tail 100 lines learning_log, no Telegram alert, per-day jsonl rotation auto-handled) |
| 20 | `branch_merge_plan.md` | Branch `sprint-05-05-tb-phase2-task-a` merge plan + EOD timing decision; 5 sekcji (state today / 3 options A=Pt 08.05 invalidated / B=EOD REC / C=Sr 06.05 / merge mechanics --no-ff / risks 8-row table / recommendation); REC Option B (Faza 7 deferred 15.05 invalidates Pt 08.05 plan + Lekcja #74 wymaga debug branch path); Adrian decision wymagany przed `integration_commands.sh` execute |
| 21 | `faza_7_debug_plan_pn_06_05.md` | **Plan B adopted** — combined debug sprint Pn 06.05 (~3-4h), extended z LGBM-only po dziś-popołudniowym TASK A divergence finding; 6 hipotez (LGBM H-A bug / H-B reality / H-C race + TASK A H-D threshold / H-E feasibility / H-F snapshot timing) + bridge analysis na shared candidate generation; 4-step investigation (code review 45min + data 60min + instrumentation 30min conditional + RC 30min); decision matrix 9 outcome paths z re-baseline timeline 12.05-12.06; 5 unknowns Adriana w tym TASK A interim mitigation T0_ALERT off Pn 06.05 morning |
| 22 | `lekcja_74_candidate_evaluator_divergence.md` | **NEW Lekcja #74 candidate** — evaluator-vs-main-dispatcher candidate divergence pattern (LGBM 100% all_bag_zero 502/502 + TASK A 100% NO_CANDIDATE 5/5 dziś vs main 3/3 znalazł 11-19 min post-fire); pryncypium shared candidate generator MANDATORY przy multiple evaluator paths; 4 anti-patterns (no shared upstream / undocumented threshold divergence / live messages bez crosscheck / deploy bez compare-to-main integration test) + implementation checklist template (8 checkboxes); cross-ref Lekcja #71 state lifecycle / #58 Z2 / #57 training-prod parity; Adrian ACK pending |
| 23 | `sprint_pn_06_05_plan.md` | Combined sprint plan Pn 06.05 (Geocoding Phase 1 4h Components 1-3 + Faza 7+TASK A debug ~3h shared evaluator divergence + TASK A live obs triage 30-60 min); 6 sekcji (scope/sequencing/risks/preflight/metrics/decisions); time-box 7-8h CC + 50 min Adrian; 6 bloków 06:30-17:30 UTC z lunch peak observation 12-14 UTC; 5 unknowns Adrian (branch decision, over-budget mitigation, instrumentation deploy method, TASK A regression threshold, adjacency Option A/B/C); Lekcja #74 evidence continuation Block 2 |
| 24 | `task_d_d6_welcome_message_design.md` | TASK D D.6 candidate design (~1500w, Z2) — bot DM do nowego kuriera z PIN+APK po D.4 atomic; 6 sekcji: user flow + edge cases (`/start` nieosiągnięty, blocked, msg buried) + TG bot DM mechanika z 3 chat_id discovery paths (A pre-`/start` capture do `kurier_chat_ids_pending.json` RECOMMENDED, B Adrian fallback DM z copy-paste body, C QR deep-link defer V3.30) + welcome template PL ~12 linii (🚀💪 emoji, PIN bold, APK URL, panel URL, Adrian DM CTA) + impl spec (NEW `onboarding/welcome_message.py` ~120 LoC `format_welcome_message`+`send_welcome` reuse `tg_send_text_with_keyboard`; MOD `migrations/migrate_couriers_*.py` step 4 best-effort post-D.4 zero rollback gdy fail; MOD `telegram_approver.py` D.7 bundle pre-`/start` whitelist gate; flags `ENABLE_D6_WELCOME=false` + `WELCOME_FALLBACK_TO_ADRIAN=true`; NEW `dispatch_state/kurier_chat_ids.json`+`_pending.json`) + 10 testów custom-runner (`test_format_welcome_*` × 3, `test_send_welcome_*` × 3, `test_pending_chat_ids_capture`, `test_d6_flag_off_skip`, `test_atomic_no_rollback`, `test_observability_log`) + Section 6 5 unknowns Adriana (#1 scope Cz 07.05 vs V3.30 → REC implement, #2 D.7 bundle → REC bundle A, #3 template → REC keep iterate, #4 fallback scope → REC rozszerzony SMS+WhatsApp, #5 retry → REC single+alert); estimate 1.5-2h pure D.6 + 1h D.7 bundle = 2.5h fits Cz 07.05 D-day budget 4-5h |
| 25 | `coherence_check_report.md` | Coherence cross-validation 22 plików w `eod_drafts/2026-05-05/`; 5 sekcji (file inventory / cross-refs validation / sprint metrics / sprint plan / final approval); verdict READY FOR EOD INTEGRATION z 1 minor follow-up (114 vs 105/98 tests recount Pn 06.05) + 1 optional wording fix daily_report Pt 08.05 entry; INDEX entries 23-25 dodane post coherence-check |

## Main CC Integration Commands

**Recommended path (post-ACK):** `bash eod_drafts/2026-05-05/integration_commands.sh`
po review `eod_checklist.md` (file 13). Manual cmds poniżej zostawione jako reference / fallback gdy script-based path nie pasuje.

### Daily report

```bash
# Submit do daily reports flow ~19:00-19:30 UTC
cp eod_drafts/2026-05-05/daily_report_draft.md \
   workspace/docs/daily_reports/2026-05-05.md
# review + send via standard pipeline
```

### Memory updates (auto-memory MEMORY.md)

```bash
# Step 1: dorzuć pointer line jako 1. linia w MEMORY.md
# (manual edit; treść w memory_pointer_line.md)

# Step 2: utwórz project memory file
cp eod_drafts/2026-05-05/memory_project_sprint_05_05.md \
   ~/.claude/projects/-root/memory/project_sprint_05_05_2026-05-05.md

# Step 3: utwórz lekcja #72 file (po Adrian ACK)
cp eod_drafts/2026-05-05/lekcja_72_candidate.md \
   ~/.claude/projects/-root/memory/lekcja_72_2026-05-05.md

# Step 4: utwórz lekcja #73 file (po Adrian ACK)
cp eod_drafts/2026-05-05/lekcja_73_robust_median_pattern.md \
   ~/.claude/projects/-root/memory/lekcja_73_2026-05-05.md
```

### CLAUDE.md additions

**TASK D revised 3-step** post Agent #3 audit (`task_d_courier_api_audit.md`) — see updated `claude_md_taskd_taske_wytyczna.md`. Key changes: 4-store atomic → 3-store atomic (courier-api READ-ONLY consumer, no register endpoint), D.5 GPS integration → D.5 smoke verify, NEW D.6 candidate (Telegram welcome message), sprint estimate 4-5h → ~3h.

```bash
# Append 3 sekcje do dispatch_v2/CLAUDE.md (Roadmap / Working Process)
# Treść w claude_md_taskd_taske_wytyczna.md
```

### Commit message (suggested)

```
docs(eod): 2026-05-05 sprint close — TASK B Phase 2 + TASK A + Issue #1

- daily report (5 deploys + 8 tagów + 114 tests)
- memory project entry sprint-05-05
- memory pointer line update
- CLAUDE.md TASK D + TASK E + Wytyczna #1
- Lekcja #72 candidate granular flag rollback

Branch: sprint-05-05-tb-phase2-task-a (NIE master merge yet)
```

## Split Decision

- **Daily report:** submit oddzielnym komitem (lub zero-commit jeśli docs flow nie wymaga).
- **Memory + CLAUDE.md:** osobny commit po Adrian ACK Lekcji #72.
- **Lekcja #72:** wait for explicit ACK przed promotion z candidate → final.
- **Branch merge:** REC **Option B (EOD dziś)** per `branch_merge_plan.md` — Faza 7 GO/NO-GO Pt 08.05 deferred 15.05 invalidates oryginal merge gate; Lekcja #74 candidate wymaga debug branch path. Adrian decision A/B/C w `integration_commands.sh` interactive prompt.

## Time Stamps

- Drafts created: 2026-05-05 ~10:50 UTC
- Daily report submission: 19:00-19:30 UTC
- Sprawa #1 deploy: ~17:00 UTC (orthogonal — script już gotowy, Agent B pre-build)
- Memory + CLAUDE.md integration: po EOD daily report submission
