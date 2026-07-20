# Golden DECISION replay

`golden_decision_replay.py` is the behavioral gate required before Phase C/D
engine refactors. It compares two committed revisions on one frozen
`world_record` corpus and proves equality of the complete decision object, not
only the six-field projection used by the operational `world_replay_gate`.

It is additive and build-only. It does not merge, deploy, restart services,
change flags, or write to production state.

## What is compared

For every certifiable `wr1+` record, the worker replaces only
`world_replay._extract` and lets the existing replay sandbox call the real
`dispatch_pipeline.assess_order`. The returned `PipelineResult` is recursively
canonicalized to sorted, compact UTF-8 JSON and compared as exact bytes.

The snapshot includes:

- every dataclass field and every dynamic attribute on `PipelineResult`;
- the selected candidate and the complete candidate list;
- exact floats (no three-decimal rounding);
- complete route plans, metrics, redirects, rule verdicts, and nested values;
- container type markers for objects, mappings, tuples, and sets.

Class/module names are not encoded, so moving a dataclass without changing its
data is not a behavioral difference.

The only exclusions are explicitly post-decision/runtime telemetry:

- result: `stage_timing`, `osrm_cache_age_s`, `osrm_degraded_since_ts`;
- candidate metrics: `candidate_timing`, `r07_compute_latency_ms`;
- LGBM shadow telemetry: `evaluation_ts`, `latency_ms`,
  `feature_compute_ms`, `inference_ms`.

Adding another volatile value does not silently expand this list. It makes the
run unstable or unsupported until the field is investigated and deliberately
classified.

## Stability and fail-closed rules

The corpus is read and frozen once before either revision runs. Each revision
is materialized with `git archive` and imported in its own child process. Each
revision runs the complete four-case stability matrix (eight passes total):

1. chronological order, `PYTHONHASHSEED=0`;
2. reverse order, `PYTHONHASHSEED=0`;
3. chronological order, `PYTHONHASHSEED=1`;
4. reverse order, `PYTHONHASHSEED=1`.

Inside every record replay, `world_replay` additionally forces the existing
OR-Tools deterministic solution budget, a 30 s offline wall-clock ceiling, and
sequential candidate evaluation. These overrides live only in the replay
process and are restored after the record.

The multi-gigabyte source corpus is never held as parsed Python objects. It is
canonicalized to a temporary JSONL stream one record at a time; workers also
decode one record at a time. The reverse pass stores only byte offsets of lines.
Worker artifacts are streamed into a temporary SQLite evaluator: only the two
baseline decision sets are stored as disk-backed BLOBs, while the six stability
passes are compared one row at a time. The eight full artifacts are never held
in Python memory together. The small diagnostic loader refuses files over
64 MiB so it cannot accidentally become the certification path.
For a bounded `--max-n` run, selection stops after observing the first record
beyond the limit and reports `scan_complete=false` plus `truncated=true`.

The three independent comparison passes on each side detect order-sensitive
caches, hash-order dependence, their interaction, unrecorded randomness, and
other self-instability. The verdict is:

- `PARITY`: both revisions are internally stable and every decision byte is
  identical;
- `DIFFS`: both revisions are stable, but at least one decision differs;
- `UNSTABLE`: at least one revision does not reproduce itself;
- `INPUT_MISSING`: capture was marked incomplete or replay requested an OSRM
  call absent from the recording. The record stays in the denominator, but its
  sentinel result is never compared as a decision and therefore cannot create
  a false diff;
- `ERROR`: worker/import/artifact error, other replay exception, or record set
  mismatch;
- `EMPTY_CORPUS`: no certifiable record.

Only `PARITY` exits with code `0`. No command can bless a changed baseline.

Malformed/non-object corpus rows, conflicting duplicate records, and unsupported
decision types also fail closed.
Reports contain only aggregate counts, commit hashes, hashes of record identity,
decision hashes, and changed JSON paths. Dynamic mapping keys in those paths are
also SHA-256-redacted. Raw order IDs, courier IDs, addresses, and decision values
stay in an automatically deleted temporary directory.

## Commands

Use the canonical dispatch virtual environment. From a clean committed
candidate worktree:

```bash
/root/.openclaw/venvs/dispatch/bin/python3 \
  tools/golden_decision_replay.py --selftest
```

Bounded engineering run:

```bash
/root/.openclaw/venvs/dispatch/bin/python3 \
  tools/golden_decision_replay.py \
  --repo . \
  --before <pre-refactor-commit> \
  --after <candidate-commit> \
  --record-dir /root/.openclaw/workspace/dispatch_state/world_record \
  --max-n 250 \
  --out /tmp/golden-decision-replay.json
```

For the per-seam Protocol #0 proof, omit `--max-n` (or give an explicitly
approved fixed window with `--since`/`--until`) so the whole selected corpus is
certified. The report records the exact corpus SHA-256 and whether selection was
truncated.

Example acceptance condition:

```text
verdict=PARITY
corpus.n > 0
corpus.truncated=false
cross_differences_n=0
before_unstable_n=0
after_unstable_n=0
errors={}
input_missing_records={}
osrm_miss_records={}
```

## Scope boundary

This harness is evidence for “the two committed revisions return identical
decision objects on this recorded corpus.” It does not prove correctness on
states absent from the corpus, authorize Phase C/D, replace the full test suite,
replace shadow observation, or permit merge/deploy without the required ACK.
