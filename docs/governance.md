# Repository governance

`main` is the production source branch. Repository settings must enforce the desired state in `.github/main-governance.json` before a production merge train starts.

## Required main-branch policy

- All changes enter `main` through a pull request.
- Required approvals stay at `0` so a single maintainer is not self-deadlocked. Review can still be requested voluntarily.
- Force-pushes are disabled.
- Branch deletion is disabled.
- The authoritative CI source job is `Validated SHA` from the reusable validation workflow. In the top-level `CI` workflow it is exposed to GitHub as the required-check context `Validate exact commit / Validated SHA` because the reusable workflow is called by the `Validate exact commit` job.
- The routing source job and GitHub required-check context are both `Verify finalized Routing V2 graph` from `Routing V2 Drift Guard`.

## Required-check names

GitHub branch protection and rulesets operate on the check-run context shown by GitHub, which is not always identical to the inner workflow job name. `.github/main-governance.json` therefore records both:

- `job`: the authoritative job name in repository workflow source; and
- `check_context`: the exact context that must be selected in GitHub repository settings.

Do not configure `Validated SHA` by itself as the CI required status check. The required GitHub context is `Validate exact commit / Validated SHA`.

## Live activation procedure

A repository administrator must activate the policy in GitHub repository settings before the production merge train starts.

Use a branch ruleset or equivalent branch-protection rule that targets `main`, set enforcement to active, and configure all of the following:

1. Require changes to enter through a pull request.
2. Keep required approvals at `0`.
3. Require these exact status-check contexts:
   - `Validate exact commit / Validated SHA`
   - `Verify finalized Routing V2 graph`
4. Block force pushes.
5. Block deletion of `main`.

Do not select the inner job name `Validated SHA` as a substitute for the full CI check context.

## Post-activation acceptance check

Treat activation as complete only after all of the following are true at the same time:

- the live rule targets `main`;
- its enforcement state is active;
- pull requests are required;
- required approvals are `0`;
- `Validate exact commit / Validated SHA` is required;
- `Verify finalized Routing V2 graph` is required;
- force pushes are disallowed;
- deletion is disallowed; and
- a fresh pull request against `main` shows both required checks and cannot bypass them.

After activation, re-read the repository ruleset/branch state and compare the live settings with `.github/main-governance.json`. If any item differs, keep the production merge train blocked.

## Merge-train gate

Live governance activation is necessary but not sufficient for merging. After every successful merge to `main`, the next pull request must first be updated or merge-forwarded to the new `main` and must obtain fresh validation for its new exact head SHA. Do not reuse a green result from before `main` moved.

Production-sensitive lifecycle changes remain the final merge-train step. A real production-parity `publish=false` dry run is still required after that final change has been refreshed onto the final `main` and before any controlled `publish=true` cutover.

The exact current rollout order belongs in the operational tracking issue rather than in this durable governance document.

## Enforcement boundary

The JSON file is a desired-state contract, not a substitute for GitHub repository settings. A repository administrator must create or update a GitHub ruleset/branch-protection rule that applies these controls to `main`.

Before a production merge, verify both of these externally:

1. the applicable `main` ruleset/branch protection is active; and
2. every `check_context` from `.github/main-governance.json` is configured as a required status check.

If repository settings and `.github/main-governance.json` disagree, production merging is blocked until the live settings are corrected. Do not weaken CI or rename checks merely to satisfy a stale protection rule; update the governance contract and live settings together in a reviewed change.
