# Owner decisions OD-01..OD-07 — recording report

Date: `2026-07-12`

Mode: documentation/canon only; no implementation

Status: `OWNER_CONFIRMED`

## Problem and evidence

Prompt 02 reconstructed North Star and left seven irreducible product questions
open. Its forensic commit `bd4a4bf6adc5ec0bdc6a544f083b94af914b5819`
correctly recorded the pre-decision state as `PARTIAL / READY_AFTER_OWNER_DECISIONS`.
The owner has now explicitly approved OD-01..OD-07, so leaving them only in chat
would create a new canon/runtime/documentation split.

## Recorded decisions

- OD-01: separate restaurant-exit and physical-possession KPI.
- OD-02: separate routing-arrival and customer-handoff KPI.
- OD-03: fail-closed KPI gates per `event × source × cohort`; numeric gates only
  after a data report.
- OD-04: commitment immutable; normal `|Δ|<=5`, explicit Alarm breach/`ALERT`
  for `5<|Δ|<=10`, and `|Δ|>10` prohibited.
- OD-05: `CASE_CORRECTION → RULE_CANDIDATE → OWNER_CONFIRMED_CANON`; no
  automatic promotion.
- OD-06: authority matrix per class; transfer, Alarm-plan execution and other
  least-damage begin `recommend+approval`, then promote independently.
- OD-07: separate `food_ready_age` and `in_vehicle_age`; R6 is only possession
  to customer handoff, 35 normally / 40 only Alarm / never courier class.

Full scope, exceptions and provenance are in
`docs/decisions/ODR-001-owner-decisions-2026-07-12.md` and the memory record
`owner-decisions-od01-od07-2026-07-12.md`.

## Deliberate boundaries

- No exact sensor/event schema for exit, possession or handoff was invented.
- No numeric coverage, missingness, cost or promotion threshold was copied from
  historical proxy reports.
- `food_ready_age` received no threshold or HARD/SOFT class.
- No complete autonomy taxonomy or promotion threshold was invented.
- Automatic Alarm detection was not converted into automatic plan execution.
- Existing parcel R6 exemption was preserved, not re-decided.
- Forensic Prompt 01/02 branches and files were not rewritten or merged.

## Documentation reconciliation

Updated normative documentation distinguishes intent from current implementation:

- ODR index plus ODR-001;
- ADR-001 HARD-before-SOFT semantics;
- ADR-003 Always-propose visibility versus execution authority;
- architecture, invariants and Definition of Done references;
- backlog decision statuses and a new implementation hold boundary.

The memory repository receives the authoritative owner record, canon v1.1,
plain-language mirror, protocol C56, index and handoff. Existing forensic claims
remain historical evidence; the new owner record supersedes their open status.

## Isolation and ownership

- Dispatch docs worktree: `/root/dispatch_owner_decisions_20260712T172510Z`,
  branch `codex/docs-owner-decisions-20260712T172510Z`, base `a84d4c8`.
- Memory worktree: `/root/memory_owner_decisions_20260712T172510Z`, branch
  `codex/owner-decisions-20260712T172510Z`, base `e20a0f4`.
- Original dispatch master had the pre-existing modified
  `eod_drafts/2026-07-10/CLAIM_LEDGER_HARD_GATE_CARD.md`; it was not read,
  staged, changed or copied.
- Tmux 58 and 78 were active at preflight; no session was killed or interrupted.

## Known implementation/documentation drift — not fixed here

- baseline hybrid ready/picked anchors do not implement R6 possession→handoff;
- physical possession and customer handoff events are not yet bound;
- load-based or other 5/10 behavior cannot rewrite commitment or grant execute;
- courier-class/best-effort 40 cannot be treated as product authority;
- Always-propose implementation is partial and must distinguish visibility from
  feasible/executable authority;
- full per-class autonomy matrix and KPI gate numbers remain future work.

## Validation and safety

Static-only validation: `git diff --check`, link/reference grep, contradiction
scan and independent semantic/adversarial review. Product tests were intentionally
not run because no product code or runtime behavior changed.

No product code, tests, flags, models, prompts, configuration, state, DB, cache,
queue, raw production records, secrets or PII were read or changed. No external
network, push before review, deploy, restart, migration, timer, daemon or flip.

## Gate

The seven owner questions no longer block design of the Codex constitution and
autonomy cards. Prompt 03 was not started in this sprint. Future cards must keep
numeric KPI gates and per-class promotion criteria explicitly `UNBOUND/HOLD`
until their own evidence and owner decisions exist.
