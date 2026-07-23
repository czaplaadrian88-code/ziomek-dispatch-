# MAIN session rotation handoff — task229/230/231

Recorded: `2026-07-23`
Owner directive: transfer all information to a fresh session, update durable
state, and close the old sessions.

## Successor

- tmux: `main-successor-20260723-135014`
- pane at launch: `%246`
- runtime: `gpt-5.6-sol`, effort `ultra`
- prompt: `/tmp/MAIN_SUCCESSOR_CANDIDATE_BOOTSTRAP_20260723.txt`
- durable source: `/root/handover/MAIN_SUCCESSOR_CANDIDATE_HANDOFF_2026-07-23.md`
- handoff SHA-256 at launch:
  `a09ec64613f5c35295d836790e120832c2568ed5cbd281e06d0c25ba313ba5af`

The successor verified that hash and began supervising execution.

There is no `/var/lib/ziomek-main-control`, `ACTIVE_INSTALL.json`,
`CONTROL_STATE.json`, or `PROGRAM_LEASE.json` on the host. Therefore the
successor is mechanically `non-MAIN/internal-only`; the old owner-facing
session remains a relay only until an atomic lease/route commit is possible.
This distinction must not be represented as an already completed MAIN
promotion.

## 231 / control-plane R4

Worktree `/root/ziomek_main_recovery_20260722`, branch
`recovery/main-control-plane-20260722`, exact base/HEAD
`abcee883b81721c5d4881202e922f8457fddf970`.

Fresh Opus writer `r4-opus-20260723-1245`, model
`claude-opus-4-8/max`, independently fixed the two validated R3 defects and
released the write lane.

Current six hashes:

| artifact | SHA-256 |
|---|---|
| gate | `75fa985a4ebd3f841bfb3b093b2716e03fc8a924cccede67e8b9437349fb7833` |
| owner auth | `954e68d4122395b14cbaa4b2df78160003225d8b90ced0a4c630fbc2d226fb0b` |
| core | `8cce9f0cdbc9800943d7a288d5c9e16c2abf47a64ea0567891f264be170b4eec` |
| requirements | `e4288e36c50136eaf629e8b55111d30a2c3a61554be916a5215140a29a7fefbb` |
| lifecycle hook | `28fd834ff1b199e8593bdf87e4d4b138de43a5d786d9bd56017f2f0590e91169` |
| tests | `dc816ee11b4c356dae995ee9104e9284208da0502511660e1917da605b2167ed` |

Evidence:

- writer focused: `378 passed`;
- independent supervisor focused replay: `378 passed in 56.91s`;
- `git diff --check`: PASS;
- R3 dispute and independent reproductions:
  `/root/handover/MAIN_CONTROL_FINAL_REVIEW_R3_2026-07-23/`.

Fresh successor then found a blocking gate-manifest mismatch: the evidence
still declared the R3 core/tests hashes, so candidate-artifact equality was
`3/5`, not `5/5`. After tools became terminal, one bounded re-seal updated
exactly those two manifest hashes. The preimage remains at
`/tmp/MAIN_EMERGENCY_RECOVERY_GATE_R4_PRE_RESEAL_20260723T140036Z.md`
(`0600 root:root`); final equality is `5/5`, gate SHA is `75fa985a…7833`,
compile/import passed, and final-byte focused replay is `378 passed in
58.01s`.

Canonical tools regression completed against the verified package root:
`2363 passed, 1 known-baseline failed, 1 skipped, 9716 subtests passed in
918.90s`. The only failure is the established
`ASSET_HASH / ASSET-CODEX-AGENTS` node. R4 added exactly four passing tests
relative to R3; source hashes and diff-check remained stable. Generated root
bytecode cache was removed and the process census was clean.

The fresh successor is running the full `tests/` regression on these final
bytes. Exact-base comparison, unique R4 freeze and fresh exact Opus/Sol reviews
remain pending.

No install, live mutation, systemd change, promotion, commit, push, product
change, flag change, Sheets access, daily-accounting read, or
`dispatch-telegram` action occurred.

## 230 / F0 privacy-CI boundary

Worktree `/root/task230-ci-privacy-f0/dispatch_v2`, branch
`codex/task230-ci-privacy-f0-20260723`, base
`b46be6c76664673aaa5b2bf35676ca9c6cb6d21b`.

The chosen architecture is a deterministic sanitized archive produced inside
a trusted boundary and checked by an independent verifier. Partial clone plus
sparse checkout is not accepted as a privacy boundary. Exact source-only lane
directive: `/tmp/TASK230_F0_WRITE_LANE_GO_20260723.md`.

The writer stopped and released its lane at an atomic RED-first checkpoint:

- four untracked files only:
  `tools/ci_privacy/__init__.py` (`f29a4ab2…7164c`),
  `export_source.py` (`df9a2060…e337d`), `verify_source.py`
  (`7b5489a1…7abe4`) and `tests/test_ci_privacy_boundary.py`
  (`b02b05aa…90e3a`);
- `policy.json` and the document remain absent;
- required behavioral RED: `6 collected / 6 failed in 0.94s`;
- prior hermetic baseline: `16 passed in 1.68s`;
- diff-check PASS; zero pycache/background pytest;
- `WRITE_LANE_RELEASED`.

It never had authority for workflow, install, upload, live, commit, push, or
real protected-data reads.

## 229 / v6

Three preserved worktrees:

- dispatch: `/root/wt-contain-229-notify-router-20260722/dispatch_v2`;
- weekly: `/root/wt-task229-weekly-firefighter-20260723`;
- panel: `/root/wt-task229-notify-feed-retirement-panel-20260723`.

The refreshed independent map confirms eight remaining defect classes. Safe
serial order:

1. finish task230/F0 shared boundary;
2. weekly timestamp, directory binding, manifest/ExecStart, local-network and
   effective-unit closure plus timeout regression;
3. panel status endpoint, then frontend/cache oracle;
4. dispatch semantic raw-boundary ratchet consuming F0;
5. regenerate the night-guard manifest last;
6. integrate, run full suites, blind review, then separate live gates.

No task229 writer was active during rotation.

## Rollback and continuation

- Candidate source remains uncommitted in isolated worktrees.
- Closing tmux workers does not remove worktrees or source.
- Successor re-verifies exact hashes/status before any new write lane.
- Any source byte change invalidates the current hashes and requires a unique
  new freeze/review.
- Production remains unchanged and on HOLD.
