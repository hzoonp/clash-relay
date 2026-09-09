# Production cutover runbook

Use this runbook only after the complete architecture-convergence change set is on the final candidate base and the exact candidate SHA has passed the authoritative CI gates.

## 1. Preconditions

- `main` is protected by the repository governance rule.
- The configured CI required-check context is green for the exact candidate.
- `Verify finalized Routing V2 graph` is required and green for the exact candidate.
- `publish.yml` still has one production mutation entrypoint: `scripts/run_production_release.py`.
- The canonical entrypoint keeps automatic `push` and `schedule` events in dry-run mode.
- The current production value and rollback pointers are known-good before cutover.

## 2. Dry-run first

Automatic `push` and `schedule` executions are fail-closed to `publish=false`. After the final production-lifecycle change is merged to `main`, its push-triggered production workflow is therefore the preferred first production-parity dry run. An operator may also dispatch the production workflow manually with `publish=false`.

A dry run may read private operational state needed for production-parity qualification, but it must not persist external state. Promotion Guard is publication-only because it compares against the current production baseline before an actual promotion; dry-run skips that gate while still executing the complete stable Mihomo matrix.

Expected zero-write outcomes:

- no release activation or Cloudflare KV production-config mutation;
- no AI qualification cache persistence;
- no scheduler history persistence;
- no production metrics persistence;
- no scheduler observation publication;
- no operational SLO persistence;
- no production failure metric persistence caused by a dry-run failure.

The lifecycle result must report `publication_status: dry-run`. Any evidence of persistent mutation is a stop condition.

## 3. Controlled publication

Only after the dry run is clean and publication is explicitly authorized may an operator dispatch the canonical workflow with `publish=true`. Automatic `push` and `schedule` events remain dry-run even if a publication-like input is present, so a merge or scheduled execution cannot become an implicit cutover.

Publication remains bound to the exact validated SHA.

Verify after commit:

- production proof succeeds;
- release manifest succeeds;
- production metrics are freshly published;
- scheduler observation is emitted only from those fresh metrics;
- current/previous release pointers are coherent;
- the client-facing production key resolves to the expected release bytes.

Do not re-enable unattended scheduled publication as part of this merge train. If unattended publication is desired after a stable cutover, activate it in a separate reviewed change with fresh exact-SHA validation and an explicit rollback plan.

## 4. Stop and rollback conditions

Stop immediately on qualification rejection, Promotion Guard block, Mihomo validation failure, SHA mismatch, ambiguous publication state, or evidence of an undeclared write path. Do not retry by weakening a gate.

If a publication commits but post-commit observability degrades, preserve the committed-release truth and use the versioned previous-release rollback procedure only when rollback is actually required.
