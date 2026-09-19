# Repository governance

`main` is the production source branch. Repository settings must enforce the desired state in `.github/main-governance.json` before a production merge train starts.

## Required main-branch policy

- All changes enter `main` through a pull request.
- Required approvals stay at `0` so a single maintainer is not self-deadlocked. Review can still be requested voluntarily.
- Force-pushes are disabled.
- Branch deletion is disabled.
- The authoritative required check is `Validated SHA` from the consolidated `CI` workflow.
- Routing V2 drift validation runs inside the CI quality gate, so it cannot be bypassed before `Validated SHA` succeeds.

## Required-check names

GitHub branch protection and rulesets operate on the check-run context shown by GitHub. `.github/main-governance.json` records:

- `job`: the authoritative job name in repository workflow source; and
- `check_context`: the exact context that must be selected in GitHub repository settings.

The required GitHub context is `Validated SHA`.

Every required context must have a pull-request producer that is not suppressed by path filters or other trigger conditions for valid `main` pull requests. A required context that some pull requests can never emit creates a governance deadlock even when the context name itself is correct.

## Live activation procedure

A repository administrator must activate the policy in GitHub repository settings before the production merge train starts.

Use a branch ruleset or equivalent branch-protection rule that targets `main`, set enforcement to active, and configure all of the following:

1. Require changes to enter through a pull request.
2. Keep required approvals at `0`.
3. Require the exact status-check context `Validated SHA`.
4. Block force pushes.
5. Block deletion of `main`.

## Post-activation acceptance check

Treat activation as complete only after all of the following are true at the same time:

- the live rule targets `main`;
- its enforcement state is active;
- pull requests are required;
- required approvals are `0`;
- `Validated SHA` is required;
- force pushes are disallowed;
- deletion is disallowed; and
- a fresh documentation-only pull request against `main` still emits `Validated SHA` and cannot bypass it.

Using a non-routing pull request for this acceptance test is intentional: it proves that the required context is an unconditional governance gate rather than a check that disappears when a path filter does not match. Routing V2 drift remains covered because it is embedded in the CI quality job that `Validated SHA` depends on.

After activation, re-read the repository ruleset/branch state and compare the live settings with `.github/main-governance.json`. If any item differs, keep the production merge train blocked.

## Merge-train gate

Live governance activation is necessary but not sufficient for merging. After every successful merge to `main`, the next pull request must first be updated or merge-forwarded to the new `main` and must obtain fresh validation for its new exact head SHA. Do not reuse a green result from before `main` moved.

Production-sensitive lifecycle changes remain the final merge-train step. A real production-parity `publish=false` dry run is still required after that final change has been refreshed onto the final `main` and before any controlled `publish=true` cutover.

The exact current rollout order belongs in the operational tracking issue rather than in this durable governance document.

## Enforcement boundary

The JSON file is a desired-state contract, not a substitute for GitHub repository settings. A repository administrator must create or update a GitHub ruleset/branch-protection rule that applies these controls to `main`.

Before a production merge, verify all of these externally:

1. the applicable `main` ruleset/branch protection is active;
2. every `check_context` from `.github/main-governance.json` is configured as a required status check; and
3. every required context is emitted for a valid pull request that does not touch routing-sensitive paths.

If repository settings and `.github/main-governance.json` disagree, production merging is blocked until the live settings are corrected. Do not weaken CI or rename checks merely to satisfy a stale protection rule; update the governance contract and live settings together in a reviewed change.
