# Forgotten Bugs/Verdicts Sweep — Ziomek ecosystem (2026-07-19)

Read-only investigation. Sources: `memory/{todo_master,sprint_timeline,tech_debt_backlog,shadow-jobs-registry}.md`,
wielki-audyt 2026-07-18/19 (`04_FINDINGS.md`, `05b_RUNTIME_DEEP_AUDIT_2026-07-19.md`, `09_OWNER_DECISIONS_REQUIRED.md`,
`COVERAGE_LEDGER.md`, `findings.json`), `dispatch_v2/eod_drafts/2026-07-0[2-9]` + `2026-07-1[0-9]`, cross-checked against
live state: `flags.json`, `git log`/`git merge-base`, `systemctl`, `atq`, direct file reads, and one live security-audit tool run.

Excluded per brief (handled today / conscious owner HOLD, not forgotten): OD-A1/A7/A8/A9/A8-2/C3-01 (19.07 decisions,
queued with a plan), C7/OD-A8 post-shift-penalty, cod-weekly stale marker, known BUG-C flags.json mine, CE-003 HOLD,
autonomy/governance OFF, at#217-220 armed jobs, cto-communicator/cto-agent LIVE.

Verification tags: **[LIVE]** = I independently re-verified against running system/repo just now. **[DOC]** = confirmed by
tracing documents to their end with no closure found, not independently re-derived from running system. **[1-SRC]** =
single sub-agent's read, not cross-checked by me.

## Top 5 (all [LIVE]-verified, most severe)

1. **Host-boundary exposure still live, unremediated 7 days.** `courier-api.service` binds `0.0.0.0:8767` (confirmed via `ss -tlnp`, pid 925329), `ufw` is `INACTIVE`, and `iptables` INPUT/DOCKER-USER v4+v6 all show `NO_TARGET_DENY_RULE` (confirmed via `tools/host_boundary_audit.py --live` run just now → `"verdict":"HOLD"`, 8 findings). Flagged 2026-07-12 (`A360_SEC0_HOST_BOUNDARY_REPORT.md` + `A360_SEC1_...` with a full runbook + 12-point ACK checklist). Zero mention in MEMORY.md after 12.07, zero remediation evidence supplied (all 7 receipt fields `NOT_SUPPLIED`). **Outward-facing, most severe finding of the sweep.**

2. **H1 "R6-HARD" enforcement — the single P1 item of the entire 110-finding Audit 360 — stalled with one domino down, three still up.** Chain: at-214 canary read (✅ finally done 18.07, 5 days late — `ziomek-parity-audit-2026-07-18.md:32` "V214 domknięte") → **R0 replay-truth branch `1b38447` still NOT merged** (`git merge-base --is-ancestor 1b38447 master` fails; last commit 2026-07-11) → **D1 firewall-exempt-truth branch `e193f2a` still NOT merged** (same check fails; last commit 2026-07-11) → B-01/B-02 business decisions (see #3 — B-02 was never actually decided, just silently defaulted). Master HEAD is `486bac4` (2026-07-19), 8 days of zero merge activity on these two branches despite the blocking precondition clearing.

3. **R-04 tier-suggestion telemetry has been 100% blind since inception, flag ON the whole time.** `shadow_dispatcher.py` writes `"r04": _r04_field_for_cid(...)` at two serializer locations (~468, ~895); live check of the last 2000 lines of `logs/shadow_decisions.jsonl` (today) shows **4258/4258 = `"r04": null`, zero non-null**. Per `05b_RUNTIME_DEEP_AUDIT_2026-07-19.md` §A8 (A8-1), root cause is a key mismatch (lookup expects `courier_id` inside a `metrics` dict that no producer populates). This P1-class "monitoring truth is fake" bug was found by the audit, downgraded to P2 on review, and then dropped entirely — it appears in neither `todo_master.md` nor the priority Dyspozycja, only inside 05b's own text.

## Nos. 4-5 — also independently verified

4. **`BEST_EFFORT_ESC_TIER2_MAX_FREE_MIN`: flags.json says 90, code's own fallback says 30 — a 3x live/intended mismatch nobody ever turned into a decision.** Confirmed: `flags.json` line 205 = `90`; `common.py:3349` = `float(_os.environ.get(..., "30"))`. `A360_D0_R6_DECISION_PREP.md` (11.07) names this exact gap as sub-decision **B-02** and warns explicitly that "effective 90 cannot be treated as a decision merely because it's LIVE" — 8 days later, still no ACK, still running on the un-blessed value. This is the same class of bug as the known "BUG C flags.json≠code" mine, just a numeric constant instead of a boolean gate.

5. **DATA0 privacy-retention branch built "SOURCE COMPLETE" 11.07, never merged — live data confirmed still world-readable.** Branch `privacy/a360-data0-ledger-retention` exists, not merged (last commit 2026-07-12 00:20). Direct check right now: `logs/shadow_decisions.jsonl` (67MB, actively growing) is `-rw-r--r--` (0644, world-readable) while its own newer sibling `logs/shadow_decisions.stage_timings.jsonl` is `-rw-------` (0600) — i.e. remediation was applied inconsistently to only *some* files. All 5 `dispatch_state/world_record/world_record-*.jsonl` (up to 227MB, full historical route/decision data) are also `0644`.

## Full candidate table

| # | Co | Gdzie/kiedy zgłoszone | Dowód że nadal otwarte | Sev | Rekomendacja |
|---|---|---|---|---|---|
| 1 | Host-boundary: 8767 public, ufw inactive, 0 deny rules | `A360_SEC0/SEC1` 12.07 | **[LIVE]** `host_boundary_audit.py --live`→HOLD; `ss -tlnp`; `ufw status`=inactive | **P1** | Run the ready 12.07 runbook this week; someone must own the ACK checklist |
| 2 | H1 R6-HARD chain: R0/D1 branches unmerged post-gate-clear | Audit 360, 10-12.07 | **[LIVE]** `git merge-base` both fail; branches static since 11.07 | **P1** | Assign an owner to merge R0+D1 now that at-214 is clear; re-open B-01/B-02 explicitly |
| 3 | R-04 telemetry 100% null despite flag ON since ~01.05 | `05b` A8-1, 19.07 | **[LIVE]** 4258/4258 null in live log today | **P1** | One-line fix (courier_id key) or kill the dead experiment; currently silently lying |
| 4 | `BEST_EFFORT_ESC_TIER2_MAX_FREE_MIN` flags.json=90 vs code=30, never ACK'd (B-02) | `A360_D0_R6_DECISION_PREP.md` 11.07 | **[LIVE]** grep confirms both values | **P2** | Force an explicit decision, close the silent-default gap |
| 5 | DATA0 privacy retention half-applied; shadow_decisions/world_record still 0644 | `A360_SEC0/E0/DATA0` 11-12.07 | **[LIVE]** perms checked today | **P2** | Merge `privacy/a360-data0-ledger-retention` or explicitly decide against it |
| 6 | BUG-D Faza 2c (`ENABLE_V326_DISTANCE_BIN_TRAFFIC_BOOST`) — rediscovered 3×, fixed 0× | 28.05 plan, re-found 27.06 + 28.06 audits | **[LIVE]** flag absent from flags.json entirely; tool `analyze_traffic_v2_shadow.py` exists unused | P2 | Either run the calibration once and flip/close, or delete the dead tool+flag |
| 7 | E1 durable event outbox (106 historical `NEW_ORDER=failed`, no retry/DLQ) | `AUDIT360_SEC1_..._E1_BRANCH_CLOSE.md` 12.07, "SOURCE COMPLETE" | **[LIVE]** branch `reliability/a360-e1-durable-outbox` confirmed unmerged since 12.07 | P2 | Merge or re-scope; backlog of failed orders never confirmed drained |
| 8 | FAIL-03 K2-live step never taken (only K2-shadow exists) | 05.06 plan named K2 as "the lever" | **[LIVE]** grep: no `K2_PROPOSE`/`K2_LIVE` flag anywhere in code | P2 | 6+ weeks shadow-only; either flip via ACK+restart or explicitly park |
| 9 | objm-lexr6 Faza 2 PLN-tiebreak decision — flag never even created | 17.06, decision due ~24.06 | **[LIVE]** grep: `ENABLE_OBJM_LEXR6_PLN_TIEBREAK` zero hits anywhere | P2 | Decide or drop; a month past its own deadline with nothing built |
| 10 | REPO dead-head cost penalty — still shadow-only past its 3-7d window | Bartek 2.0 roadmap, 11.06, due ~14-18.06 | **[LIVE]** flags.json: `ENABLE_REPO_COST_SHADOW=true`, `..._LIVE=false` | P2 | 5+ weeks late vs the other 3 sibling verdicts (all resolved) |
| 11 | BUG-A (bag-time Σ) + R5-detour escalation 4.0→8.0zł/km — ACK window 18-19.06 blown | `todo_master.md` calendar row, empty status | **[LIVE]** flags.json: `ENABLE_BAG_TIME_FAIRNESS_SCORING=false`, `R5_DETOUR_PENALTY_PER_KM=4.0` | P2 | Revisit — a month past the only bramka row in the calendar with no status |
| 12 | `event_bus.py`/`postpone_sweeper.py` concurrent-writer races (A10-02/04/05/06) | `05b` §A10, 19.07 | **[DOC]**, partial **[LIVE]** spot-check (flock(LOCK_SH) helper + separate LOCK_EX RMW comment both present — picture more nuanced than "zero lock", worth a closer read) | P2 | Have the owner of `05b` walk through with fresh eyes; queued only as generic "backlog higiena" today |
| 13 | F-26 TOCTOU in `state_machine.py` preserve-terminal (~802-816) — same mechanism as historical V3.27.5 Path B incident | `04_FINDINGS.md` F-26 | **[LIVE]** code read: `get_order` (unlocked read) feeds `new_status` decision later written elsewhere | P2 | Promised backlog entry never made (`todo_master.md`/`08_REFACTOR_WAVES.md` both grepped clean) |
| 14 | `ENABLE_OBJ_DELIVERY_FOOD_AGE` workflow (OFF→shadow→replay→flip) never actually started | ACK'd 14.06, `courier-routing-bug-foodage` | **[LIVE]** flags.json: both live AND shadow variant still `false` | P2 | Step 1 (shadow) never flipped either — over a month post-ACK |
| 15 | Rolling re-optimization "Faza 1" stuck behind an unrepeated Gate-0 canary | 21.05 Gate-0 FAILED (bug), fix same day, re-run promised | **[DOC]** no re-run logged anywhere through 06-14, silent since | P2 | Feature sits dormant 8+ weeks; re-run the gate or shelve formally |
| 16 | Day-7 KPI review for 4 "latent" couriers (Gabriel 179 etc.), routine scheduled 03.06 20:00 | `tech_debt_backlog.md` 03.06 | **[DOC]** no closing mention found in 3 trackers | P2 | Cheap to close — just needs someone to run the review once |
| 17 | TomTom PoC cleanup (2 crons + API key) pending since verdict ~22.06 | `todo_master.md` 22.06 | **[DOC]** | P3 | Housekeeping only |
| 18 | `gastro_edit.py` duplicates `nr_domu` on console address edit (proven on live order 484269) | `AUDYT2/SWEEP_write_hands.md` 01-02.07 | **[1-SRC]** | P3 | Small, concrete repro exists — cheap fix |
| 19 | `cron_health.json` permanently shows 3 healthy jobs as "failed" (missing `record_run_success`) | `AUDYT2/L07` 01-02.07 | **[1-SRC]** | P3 | Cosmetic but pollutes the health dashboard's credibility |
| 20 | `koord_cascade_monitor.py` doesn't read rotated logs (sibling tool already got this fix via SP-B2-LOGROT) | `AUDYT2/L07` 01-02.07 | **[1-SRC]** | P3 | Port the existing `_rotated_logs.py` pattern |
| 21 | Courier name canonicalization cid 370 (Kuba/Jakub), cid 376 (SC/Ściepko) left undecided while neighbor item closed same day | `SPRINT4_ZP105_IDENTITY_RAPORT.md` 10.07 | **[1-SRC]** | P3 | Cosmetic, both resolvers already hit correct CID |
| 22 | Oldest unclosed loop in the whole sweep: Adrian's visual sign-off on bag≥1 pickup-extension Telegram mockup | `sprint_timeline.md` 07.05 | **[DOC]** | P3 | Feature has been live 2.5 months regardless; just needs someone to say "looks fine" |

## Borderline — NOT counted as "forgotten" (nuance, not omission)

- **OD-01..OD-07** (owner-confirmed 12.07 governance/KPI-binding decisions) remain `otwarte` — but this is *visibly* tracked at the top of `MEMORY.md` ("Prompt 03 nie rozpoczęty") and the 17.07 owner call **explicitly** ranked P1-P6 stability work above autonomy/governance. This is a documented, standing deprioritization, not something nobody is watching. One nuance worth flagging to Adrian: **OD-07 was partially cherry-picked** (the D3-gold numeric piece, 18.07) while the harder "event-binding" half of the same decision (possession vs handoff) was left behind without anyone noting the split — worth a sentence in the next OD review, not a fire alarm.
- **V214/at-214 canary** — Agent research initially flagged this as "never read." Direct verification found it actually **was** read 18.07 (`ziomek-parity-audit-2026-07-18.md:32`, PASS, "V214 domknięte") — 5 days late, but done. Correcting the record here; the real remaining gap is items #2 above (R0/D1 merges), not the canary readout itself.

## Summary counts
- **P1 (live-verified, most severe): 3** — host-boundary exposure, H1/R0/D1 merge chain, R-04 blind telemetry.
- **P2: 13** (rows 4-16).
- **P3: 6** (rows 17-22, hygiene/cosmetic).
- Total candidates surfaced and evidence-checked: **22**, plus 2 borderline items explicitly not counted (documented conscious deprioritization / one corrected false lead).
