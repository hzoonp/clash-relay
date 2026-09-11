# Production cutover runbook

Use this runbook only after the complete architecture-convergence change set is on the final candidate base and the exact candidate SHA has passed the authoritative CI gates.

## 1. Preconditions

- `main` is protected by the repository governance rule.
- The configured CI required-check context is green for the exact candidate.
- `Verify finalized Routing V2 graph` is required and green for the exact candidate.
- `publish.yml` still has one production mutation entrypoint: `scripts/run_production_release.py`.
- Automatic `push` remains dry-run; scheduled publication is reachable only through the schedule-specific `CLASH_RELAY_SCHEDULE_PUBLISH` gate.
- The current production value and rollback pointers are known-good before unattended publication is enabled.

## 2. Dry-run first

Automatic `push` executions are fail-closed to `publish=false`. Manual `workflow_dispatch` also defaults to `publish=false`. After the final production-lifecycle change is merged to `main`, its push-triggered production workflow is therefore the preferred first production-parity dry run. An operator may also dispatch the production workflow manually with `publish=false`.

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

## 3. Bootstrap publication

Only after the dry run is clean may an operator dispatch the canonical workflow with `publish=true`. This establishes or reconfirms a known-good production baseline before unattended publication is enabled for a fork.

Publication remains bound to the exact validated SHA.

Verify after commit:

- production proof succeeds;
- release manifest succeeds;
- production metrics are freshly published;
- scheduler observation is emitted only from those fresh metrics;
- current/previous release pointers are coherent;
- the client-facing production key resolves to the expected release bytes.

## 4. Guarded scheduled publication

The scheduled workflow runs every six hours at `17 */6 * * *` UTC and uses the same canonical lifecycle as manual `publish=true`. It does not bypass qualification, Promotion Guard, current-policy audits, the stable Mihomo matrix, exact-SHA binding, release read-back verification, or rollback readiness.

The authorized upstream `hzoonp/clash-relay` deployment is enabled when repository variable `CLASH_RELAY_SCHEDULE_PUBLISH` is unset or exact lowercase `true`. Setting the variable to `false` suspends unattended publication and returns scheduled runs to dry-run. Public forks default to dry-run and must explicitly set `CLASH_RELAY_SCHEDULE_PUBLISH=true` only after a successful manual dry-run and bootstrap publication.

`push` remains dry-run even if a publish-like environment value is present. Manual `workflow_dispatch` remains controlled solely by its `publish` input. Scheduled publication can be selected only by the schedule-specific gate.

Overlapping production workflows remain serialized with `cancel-in-progress: false`; never weaken this to make a later scheduled refresh overtake an in-flight lifecycle.

## 5. Stop and rollback conditions

Stop immediately on qualification rejection, Promotion Guard block, Mihomo validation failure, SHA mismatch, ambiguous publication state, or evidence of an undeclared write path. Do not retry by weakening a gate.

If a publication commits but post-commit observability degrades, preserve the committed-release truth and use the versioned previous-release rollback procedure only when rollback is actually required.

To suspend future scheduled mutation without changing the publication code path, set `CLASH_RELAY_SCHEDULE_PUBLISH=false`. Existing production bytes remain active; use the manual rollback workflow only when the active release itself must be reverted.
