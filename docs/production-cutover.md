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

For an ambiguous release commit, preserve the exact candidate and pre-attempt production bytes privately, then run the read-only `clash-relay reconcile-release` command. A result of `unknown` is a stop condition; the command never writes, retries, or compensates.

The lifecycle runs that read-only comparison before private cleanup. If the result remains unknown or the read fails, it retains only `candidate.yaml`, optional `previous.yaml`, and `metadata.json` in `.work/recovery/<attempt>/` with private permissions. Use those exact files with `reconcile-release --candidate ... --previous ...` (or `--first-release` when `baseline_known` is true and `previous_present` is false). The metadata's `review_by_epoch` is a seven-day operator review target; removal is manual after reconciliation. A hosted Actions runner is ephemeral, so its local recovery directory does not survive the job; never upload this directory as a public artifact.

For hosted runs, the private immutable KV release objects and manifests are the durable evidence. The safe failure diagnostic exposes the candidate release ID and, once the transaction captured its actual pre-attempt baseline, the previous release ID. Run `clash-relay reconcile-release --candidate-release-id SHA --previous-release-id SHA` or `--first-release` when the diagnostic explicitly reports a null previous ID. This command verifies both immutable objects and manifests before comparing the production key and pointers. If the error occurred while staging, the actual baseline may not yet have been captured; use the private inventory and live pointer evidence to investigate, and do not infer a first release from a missing previous ID. KV reads can lag, so an unknown result requires a later read-only comparison.

If a publication commits but post-commit observability degrades, preserve the committed-release truth and use the versioned previous-release rollback procedure only when rollback is actually required.

To suspend future scheduled mutation without changing the publication code path, set `CLASH_RELAY_SCHEDULE_PUBLISH=false`. Existing production bytes remain active; use the manual rollback workflow only when the active release itself must be reverted.

## 6. Immutable release retention

Run `plan-release-retention` first and review its digest and candidate count. Apply a reviewed plan only through the manual `Apply immutable release retention` workflow with the exact digest and `confirm=true`. It shares the production concurrency group, revalidates the exact `main` SHA, recreates the plan privately, and stops if the digest differs. A partial or ambiguous delete leaves the journal unchanged; reconcile the actual KV state before preparing another plan.

Run `clash-relay audit-release-inventory` for a read-only comparison of listed immutable objects with the journal, both pointers, and the client-facing production value. `unrecorded_unprotected_release_ids` identifies objects for review, including failed staging and post-commit journal failures. This is an observation, not a deletion authorization: KV listings and reads can lag. Repeat after propagation, verify object contents and live references, and prepare a separately reviewed cleanup procedure before deleting anything. The retention command never deletes unjournaled objects.
