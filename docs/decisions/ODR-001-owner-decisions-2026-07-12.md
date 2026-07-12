# ODR-001 — Owner decisions OD-01..OD-07 (2026-07-12)

Status: `OWNER_CONFIRMED`

Decision/effective date: `2026-07-12`

Scope: product semantics for Ziomek KPI, R6/R27, learning and autonomy.

Runtime effect: none; this record does not change code, flags, data or execution authority.

## Provenance

Primary source is the explicit product-owner message in the current 2026-07-12
session approving options OD-01..OD-07. Decision preparation is the forensic
Prompt 02 commit `bd4a4bf6adc5ec0bdc6a544f083b94af914b5819`, file
`audits/2026-07-12/foundation-02-north-star-canon/OWNER_DECISION_PACKET.md`.
The full canonical scope/exceptions record is
`memory/owner-decisions-od01-od07-2026-07-12.md`. Prompt 02 remains unchanged
as pre-decision evidence.

## Decisions

1. **OD-01 — pickup KPI:** keep restaurant exit and physical possession as two
   separate KPI events. Last-inside/click/status are named proxy only; exact
   event sources and gates remain `UNBOUND/HOLD`.
2. **OD-02 — delivery KPI:** routing arrival and customer handoff are separate.
   Arrival does not prove handoff and does not end R6.
3. **OD-03 — KPI gate:** fail closed independently for each explicit
   `event × source × cohort` cell. Insufficient support/provenance/coverage
   blocks that KPI verdict/promotion, not runtime dispatch. Numeric thresholds
   are set only after a data report; historical proxy thresholds are not canon.
4. **OD-04 — R27/Alarm:** stored commitment is immutable. With `Δ` relative to
   it: `|Δ|<=5 min` is the normal SOFT window; `5<|Δ|<=10` is only an explicit
   Alarm breach/`ALERT`; `|Δ|>10` is prohibited. Alarm does not rewrite or
   renegotiate commitment and does not itself grant execute.
5. **OD-05 — correction promotion:** `CASE_CORRECTION → RULE_CANDIDATE →
   OWNER_CONFIRMED_CANON`. Canon requires explicit owner approval with scope,
   exceptions, effective date and provenance. No case count, operator, model,
   code or agent can auto-promote a product rule.
6. **OD-06 — autonomy:** authority is a matrix per decision class. Transfer,
   execution of an Alarm plan and other least-damage/`ALERT` initially use
   `recommend + approval before execute`; every class is promoted independently
   after its own evidence/card and explicit owner decision. No class is promoted
   by this record. Automatic Alarm detection is not automatic plan execution.
7. **OD-07 — R6 interval:** `food_ready_age` and `in_vehicle_age` are separate.
   R6 applies only to in-vehicle age from physical possession to customer
   handoff: 35 min normally, 40 min only in automatic Alarm, for every courier
   class; `>40` is prohibited. `food_ready_age` gets no threshold or HARD/SOFT
   class here. The existing parcel exemption remains unchanged.

## Still unbound

- exact versioned event/source contracts for exit, possession and handoff;
- numeric KPI coverage/missingness/cost/promotion thresholds after the report;
- endpoint and policy for `food_ready_age`;
- complete autonomy taxonomy and per-class promotion/stop-loss criteria;
- any future explicit commitment-renegotiation contract;
- exact technical Alarm predicate, lifecycle and observability.

Implementation that currently uses ready/picked hybrid anchors, delivery
arrival as a terminal proxy, load-based 5/10 semantics or courier-class 40 is
implementation drift, not authority to reinterpret these decisions. Any change
requires a separate full Przykazanie #0 sprint and live ACK where applicable.
