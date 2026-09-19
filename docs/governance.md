# Repository governance

`main` is the production source branch. Repository settings must enforce the desired state in `.github/main-governance.json` before a production merge train starts.

## Required main-branch policy

- All changes enter `main` through a pull request.
- Required approvals stay at `0` so a single maintainer is not self-deadlocked. Review can still be requested voluntarily.
- Force-pushes are disabled.
- Branch deletion is disabled.
- One consolidated `CI` workflow emits both required GitHub check contexts.
- `Validate exact commit / Validated SHA` is the final exact-SHA authority.
- `Verify finalized Routing V2 graph` is a compatibility check context backed by the Routing V2 drift gate inside the CI quality job.

## Required-check names

GitHub branch protection and rulesets operate on the check-run context shown by GitHub. `.github/main-governance.json` records both the authoritative job and the exact check context.

The required contexts are:

- `Validate exact commit / Validated SHA`
- `Verify finalized Routing V2 graph`

Both are produced by the single `CI` workflow. The second context does not require a separate routing workflow: it succeeds only after the quality job, which contains the actual Routing V2 drift validation, succeeds.

Every required context must have a pull-request producer that is not suppressed by path filters or other trigger conditions for valid `main` pull requests.

## Live activation procedure

A repository administrator must activate the policy in GitHub repository settings before the production merge train starts.

Use a branch ruleset or equivalent branch-protection rule that targets `main`, set enforcement to active, and configure all of the following:

1. Require changes to enter through a pull request.
2. Keep required approvals at `0`.
3. Require `Validate exact commit / Validated SHA`.
4. Require `Verify finalized Routing V2 graph`.
5. Block force pushes.
6. Block deletion of `main`.

## Post-activation acceptance check

Treat activation as complete only after all of the following are true at the same time:

- the live rule targets `main`;
- its enforcement state is active;
- pull requests are required;
- required approvals are `0`;
- both required CI contexts above are required and emitted;
- force pushes are disallowed;
- deletion is disallowed; and
- a fresh documentation-only pull request against `main` still emits both contexts and cannot bypass them.

Routing V2 drift remains covered because the compatibility context depends on the quality job that executes `scripts/routing_shadow.py`.

After activation, re-read the repository ruleset/branch state and compare the live settings with `.github/main-governance.json`. If any item differs, keep the production merge train blocked.

## Merge-train gate

Live governance activation is necessary but not sufficient for merging. After every successful merge to `main`, the next pull request must first be updated or merge-forwarded to the new `main` and must obtain fresh validation for its new exact head SHA. Do not reuse a green result from before `main` moved.

Production-sensitive lifecycle changes remain the final merge-train step. A real production-parity `publish=false` dry run is still required after that final change has been refreshed onto the final `main` and before any controlled `publish=true` cutover.

## Enforcement boundary

The JSON file is a desired-state contract, not a substitute for GitHub repository settings. A repository administrator must create or update a GitHub ruleset/branch-protection rule that applies these controls to `main`.

Before a production merge, verify all of these externally:

1. the applicable `main` ruleset/branch protection is active;
2. every `check_context` from `.github/main-governance.json` is configured as a required status check; and
3. every required context is emitted for a valid pull request that does not touch routing-sensitive paths.

If repository settings and `.github/main-governance.json` disagree, production merging is blocked until the live settings are corrected.
