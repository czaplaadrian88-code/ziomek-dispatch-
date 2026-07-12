# Private ledger rollout — SOURCE/PREP only

Status: **NOT INSTALLED, NOT DEPLOYED**. This directory is a template for a
future ACK-gated rollout after at-214 and business decision B-05.

## Contract

- `ZIOMEK_PRIVATE_LEDGER_MODE=compat` is the default and preserves the legacy
  writer and payload shape.
- `mirror` is a reserved HOLD value and fails before either write. It cannot be
  activated until a retry-safe transaction/outbox prevents duplicate legacy
  rows after partial failure.
- `private` writes only the pseudonymised artifact. If key/config validation
  fails, no sensitive fallback is written; a minimal identifier-free 0600
  status record is attempted and the producer receives a fail-loud error.
- Required non-secret configuration: private root and pseudonym scope. The key
  carrier is an external regular file owned by the service user, mode 0600,
  one hardlink. Never place its value in Git, a unit, a report, or a fixture.
- Rotation is rename/reopen under the writer's stable lock. `copytruncate` is
  prohibited.
- Retention is metadata-only `would-delete`; the tool has no apply/delete
  option. B-05 must be decided before a separate deletion implementation.
- Both source service templates are manual metadata-only planning; there is no
  rotation timer or approved schedule. `dispatch-private-ledger-retention@.service` is manual;
  the instance value is the proposed number of days. It has no timer because
  scheduling before B-05 would imply an unapproved retention policy.

## Future ACK-gated sequence

1. Finish at-214 and freeze its corpus/provenance.
2. Provision a 0700 root and 0600 external key carrier without displaying it.
3. Run synthetic permission, malicious-input, concurrent append/rotate and
   old/new reader tests with the exact unit environment.
4. Before any dual-write rollout, design and approve a retry-safe outbox or
   transaction protocol; only then may the `mirror` HOLD be removed.
5. Migrate every active reader to the versioned decoder before considering
   `private`. Installation/deploy/restart requires explicit ACK. Keep
   compatibility rollback until the observation window closes.
6. Do not enable deletion. Run only `data_retention_plan` with an explicit
   proposed duration and present `would-delete` metadata for B-05.

## Rollback

Set mode back to `compat` and revert the source commit. No timer exists to
disable. The reader remains old/new compatible. Never restore a deleted corpus;
this rollout does not authorize deletion or live migration.
