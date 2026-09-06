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

## Enforcement boundary

The JSON file is a desired-state contract, not a substitute for GitHub repository settings. A repository administrator must create or update a GitHub ruleset/branch-protection rule that applies these controls to `main`.

Before a production merge, verify both of these externally:

1. the applicable `main` ruleset/branch protection is active; and
2. every `check_context` from `.github/main-governance.json` is configured as a required status check.

If repository settings and `.github/main-governance.json` disagree, production merging is blocked until the live settings are corrected. Do not weaken CI or rename checks merely to satisfy a stale protection rule; update the governance contract and live settings together in a reviewed change.
