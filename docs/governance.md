# Repository governance

`main` is the production source branch. Repository settings must enforce the desired state in `.github/main-governance.json` before a production merge train starts.

## Required main-branch policy

- All changes enter `main` through a pull request.
- Required approvals stay at `0` so a single maintainer is not self-deadlocked. Review can still be requested voluntarily.
- Force-pushes are disabled.
- Branch deletion is disabled.
- The required CI status is `Validated SHA` from the `CI` workflow. That job depends on quality, Python compatibility, deterministic generation, and the pinned stable Mihomo matrix, so it is the authoritative aggregate CI gate.
- The required routing status is `Verify finalized Routing V2 graph` from `Routing V2 Drift Guard`.

## Enforcement boundary

The JSON file is a desired-state contract, not a substitute for GitHub repository settings. A repository administrator must create or update a GitHub ruleset/branch-protection rule that applies these controls to `main`.

Before a production merge, verify both of these externally:

1. the applicable `main` ruleset/branch protection is active; and
2. the two required checks are configured with the exact workflow job names from the repository.

If repository settings and `.github/main-governance.json` disagree, production merging is blocked until the live settings are corrected. Do not weaken CI or rename checks merely to satisfy a stale protection rule; update the governance contract and live settings together in a reviewed change.
