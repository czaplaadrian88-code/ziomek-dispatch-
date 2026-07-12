# ODR-002 — Autonomy authority ownership (2026-07-12)

Status: `OWNER_CONFIRMED`

Decision/effective date: `2026-07-12`

Scope: governance of execution authority for every Ziomek decision class,
including promotion, automatic demotion, authoritative cards and runtime gates.

Runtime effect: none. This record does not create, sign, modify or activate an
authoritative card, and it does not change code, flags, data, policy gates,
services or any class's current execution authority.

## Provenance

Primary source is the explicit product-owner message in the current 2026-07-12
session titled `CANONICAL GOVERNANCE DECISION — AUTONOMY AUTHORITY OWNERSHIP`.
ODR-002 refines the governance boundary introduced by ODR-001 OD-06. ODR-001
continues to define authority per decision class; this record defines who may
increase it and the minimum promotion and runtime-enforcement contract.

## Decision

1. The product owner is the only authority that may increase execution
   authority for any decision class.
2. Codex may collect evidence, recommend promotion, prepare a candidate patch,
   implement in isolation and run eval, shadow and canary work only within the
   authority granted by the current card.
3. Codex may not approve its own promotion, directly change the authoritative
   card, change a policy gate to obtain authority, change an eval or threshold
   and use that changed instrument to approve the same promotion, or treat a
   descriptive document as execution authority.
4. Every increase of autonomy requires all of: an explicit owner decision, a
   content-bound evidence bundle with a hash, independent verification,
   owner-only approval or signature, and deterministic application of the
   approved change.
5. Autonomy may decrease automatically after a stop-loss, guardrail or
   kill-switch breach, or when no credible policy is available. Automatic
   demotion never creates a reciprocal right to re-promote.
6. Before every execution, runtime must validate the current signed card
   independently of prompts, descriptive documentation and the model's belief.
7. A missing or invalid card, signature, version or required field fails closed
   to `recommend-only` or `HOLD`, according to the class's explicit safe-state
   contract. No fallback may infer authority from code, a flag or prior state.
8. Changes to a card, its schema, parser, runtime gate, promotion policy or any
   protective mechanism are `R4-GOVERNANCE`.

The owner-only rule has no promotion exception. The only asymmetric automatic
path is the decrease described in point 5. A demotion never authorizes a later
increase. The valid signed card is the runtime authority artifact; this ODR and
all other descriptive records remain normative inputs, not executable grants.

## Required technical properties before implementation can become authoritative

- one versioned card schema with stable decision-class identifiers;
- an owner-controlled trust root, signing/approval method, rotation and
  revocation contract;
- a hashed evidence-bundle schema and an explicit independence rule for review;
- deterministic, atomic application with provenance, rollback and replay-safe
  version handling;
- runtime validation at every execution entry point, not only at startup;
- class-specific mapping of invalid authority to `recommend-only` or `HOLD`;
- automatic demotion paths that are monotonic, observable and unable to
  re-promote a class;
- negative and mutation tests for missing, stale, future, malformed, unsigned,
  wrongly signed and wrong-class cards, plus attempts to self-promote by
  changing the card, gate, eval or threshold.

## Still unbound

- authoritative card location and exact schema;
- signature algorithm, key custody, rotation, revocation and recovery;
- complete decision-class taxonomy and stable IDs;
- evidence-bundle format, hash algorithm and reviewer-independence criteria;
- exact per-class `recommend-only` versus `HOLD` safe state;
- stop-loss and guardrail thresholds;
- deterministic apply and rollback mechanism;
- complete inventory of runtime execution entry points and migration from the
  current no-card baseline.

These are technical and business follow-ups, not permission to infer a design
or authority. Codex may prepare candidates in isolation, but an authoritative
change or live enforcement requires the full R4-GOVERNANCE path above.
