# Session 274–280 close: Fable execution pin and canonical at cancellation

Timestamp: 2026-08-01 UTC
Actor: `codex-root-20260801`
Scope: review queue control-plane only; no dispatcher decision, flag or data
contract changed.

## Root cause and behavior change

The original at-job `#227` carried only a mutable path to `review_queue.py`.
Ledger notes recorded the expected bytes, but `at_gate.py` decoded argv and
executed the path without an execution-time comparison. A later modification
of the runner or the Panel lane identity in `QUEUE_STATUS.json` could therefore
escape the recorded release evidence.

The replacement adds two source-of-truth closures:

1. `pinned_review_queue_launcher.py` is invoked with literal expected hashes in
   the immutable scheduler argv. Before model contact it verifies its own bytes,
   runner, selftest, owner override, session-close supplement, stored preflight,
   the semantic queue-input fingerprint and the current canonical gate
   state/code/evidence. Any drift exits `97` before `review_queue run`.
2. `GateStore.cancel_at_job` plus `at_gate.py cancel` records an exact scheduler
   cancellation and gate supersession atomically. It rejects a wrong queue ID,
   stale gate version, unsupported gate state, missing postcondition or an
   implicit recovery of an already-removed job. This removes the false
   `MISSING_ALARM` writer instead of masking it in documentation.

## Writer / consumer completeness map

| place | role | writer / consumer | touched | reason / oracle |
|---|---|---|---|---|
| `tools/process_debt_gate.py::cancel_at_job` | canonical ledger writer | writer | yes | atomically sets at intent `CANCELLED`, gate `SUPERSEDED`, clears alarm and appends audited event under gate-version CAS |
| `tools/at_gate.py::cancel` | scheduler adapter | writer/consumer | yes | proves exact job presence/removal with `atq`/`atrm`, then calls the sole ledger writer |
| `tools/process_debt_gate.py::reconcile_at_jobs` | missing-job detector | consumer | no code change | terminal `CANCELLED` is already excluded from active statuses; real E2E proves #227 disappears from alarms |
| `tools/process_debt_collect.py` | active-job collector | consumer | N-D | already uses `list_at_jobs(active_only=True)`; `CANCELLED` is automatically excluded by canonical query |
| `render_open_gates` / `OPEN_GATES.md` | operator view | consumer | N-D | reads the gate state/alarm from the same ledger; regeneration is sufficient |
| `tests/test_process_debt_gate.py::test_reconcile_missing_at_job_sets_alarm_visible_in_view` | negative oracle + mutation ratchet | test | yes | wrong scheduler ID and stale CAS must fail; exact cancellation clears alarm and later reconcile stays clean |
| queue `pinned_review_queue_launcher.py` | execution-time release guard | consumer/executor | yes, external artifact | hash-bound scheduler argv; semantic fingerprint excludes only scheduler receipt fields |

No dispatcher feasibility, scoring, selection, plan, serializer, flag or
runtime-state writer consumes this contract.

## Exact queue identity after replacement

- active at-job: `#228`, `2026-08-02T10:10:00Z`;
- canonical gate:
  `release.sessions274-280-review-queue-pinned-20260802`,
  `READY_FOR_REVIEW` v3/event #428;
- launcher SHA:
  `62679bce5a1c3e5f5b4acd60309d7f797a19c6bd7898e0482f14fd7a2cc9c328`;
- runner SHA:
  `cf63775ae2f6f706cba9287680e7764013527599b88de0101eea39b553b5c0ce`;
- queue selftest SHA:
  `13476f310b354439dc69700fa055baad13845a7bb1a665e2cc3868740f4c9c99`;
- owner override v3 SHA:
  `936eaa968381d52f258de57426d432f208846b8e1eb8beb7b14e8b171028661b`;
- session-close supplement SHA:
  `9d1b59dbafaa8020e1e71cbd17ab7ef671eb0989fac797bd3b369914bf87b5fb`;
- preflight v2 SHA:
  `47d7bab1a6b081798bd987027d74b565bb39f3b7faf7d0cbf0a1d547151d3b01`;
- semantic queue-input fingerprint:
  `6037972641bf639fd48cd0173bd1c816cd8c5e8cc2611936ad092fd698cf68c6`;
- final scheduled `QUEUE_STATUS.json` SHA:
  `55b96c326a6b4c2299aa750acdd44f8c2208b2547f4e4be1b6244d628591e43b`.

Old at-job `#227` was removed only after #228 reached `READY_FOR_REVIEW`.
Its gate `release.sessions274-280-review-queue-20260802` is now
`SUPERSEDED` v10, its at intent is `CANCELLED`, and its alarm is false.
`at_gate.py reconcile` sees #228 and reports only the unrelated historical
#225 alarm.

## Tests and evidence

regresja: 6367 passed, 24 skipped, 8 xfailed, 0 failed; baseline behavior preserved
e2e: real at#227 cancellation receipt -> CANCELLED/SUPERSEDED/no alarm; reconcile sees #228 and not #227
pozytywny-wplyw: negative missing-job alarm becomes exact CANCELLED receipt while unrelated #225 alarm remains visible
rollback: git revert dedicated process-tool commit; never recreate superseded mutable-path at#227
N-D: dispatcher feasibility/scoring/selection/plan/serializers/flags — process scheduler contract only

- baseline focused: `18 passed`;
- final focused on exact source bytes: `18 passed in 1.56s`;
- queue selftest: `31/31 PASS`;
- queue full preflight: `PREFLIGHT_OK`, three frozen lanes unchanged;
- read-only launcher validation: `EXECUTION_PINS_OK`, gate v3;
- canonical full regression:
  `6367 passed, 24 skipped, 8 xfailed, 0 failed, 149 warnings in 353.66s`;
- `py_compile`: PASS for both process tools, queue runner, launcher and tests;
- `git diff --check`: PASS.

The known night-suite outcome debt is unchanged: the full pytest command is
green, while night manifest v36 still expects one `xfailed` outcome where the
canonical daily-reset writer makes the test pass. Gate
`release.q5-night-manifest-v36-outcome-drift-20260801` remains `BUILT_OFF`.

## Operations, safety and rollback

- no production service restart, deploy, flag flip, message send or runtime
  data migration;
- no Fable/model invocation;
- one scheduler replacement: #228 registered first, #227 removed second;
- one canonical cancellation receipt and ledger supersession;
- process-tool source SHA after the final formatting-only pass:
  `process_debt_gate.py=5c42e1bc…`, `at_gate.py=77e1fd67…`,
  `test_process_debt_gate.py=e74ddb09…`.

Code rollback is a normal `git revert` of the dedicated process-tool commit.
Do not restore at#227: its mutable-path design is intentionally superseded.
If #228 must be withdrawn, use only `at_gate.py cancel` with the exact job ID,
fresh gate version and verified queue postcondition. Restoring any production
effect remains outside this scope and requires a new owner ACK.
