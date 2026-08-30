# First public release checklist

## Repository contents

- [ ] `python scripts/repository_audit.py` passes on the exact staged tree.
- [ ] `git log --all -p` contains no real subscription URL, token, node credential, private controller, or generated config.
- [ ] Example and fixture hosts remain fictional/reserved and fixture identities contain no personal data.
- [ ] `config.yaml` and `subscriptions.yaml`, when added in a deployment repository, contain metadata only.
- [ ] License, CODE_OF_CONDUCT, CONTRIBUTING, SECURITY, and issue templates are correct for the owner.
- [ ] Replace `OWNER` placeholders in issue contact and README fixed-URL examples.

## Code and tests

- [ ] Fresh Python 3.11 and 3.12 environments install from lock files.
- [ ] Ruff lint and format checks pass.
- [ ] All non-integration tests pass.
- [ ] Deterministic double generation and `--check` pass.
- [ ] Repository audit passes after staging.
- [ ] Both pinned stable Mihomo versions pass load, startup, generated-candidate, and provider `HEAD` integration tests.
- [ ] Prerelease compatibility is allowed to fail and cannot reach publication.
- [ ] Version manifest and dependency locks have been reviewed for release date.

## GitHub settings

- [ ] Create the public source repository without importing another repository's history.
- [ ] Push the single audited initial commit.
- [ ] Enable branch protection and require all PR checks.
- [ ] Review Actions permissions; keep default read and grant write only to promotion.
- [ ] Enable Dependabot and private vulnerability reporting.
- [ ] Optionally pin Actions to full commit SHAs.
- [ ] Decide whether deployers should use a private template repository rather than a public fork.

## Fictional acceptance run

- [ ] Add only a completely fictional `CLASH_RELAY_SUBSCRIPTIONS` mapping.
- [ ] Run the production workflow and inspect the redacted report.
- [ ] Delete each fixture subscription in turn and confirm generation behavior.
- [ ] Add a new fixture subscription without changing Python.
- [ ] Add a fixture AI service using one service row and one rule file (a module Boolean is optional).
- [ ] Confirm empty optional pools route only to `REJECT`.
- [ ] Confirm general/bulk pools contain no residential, EMBY, high-multiplier, or chain node.
- [ ] Deliberately break candidate validation and confirm no new production Artifact/Release appears.

## Production deployment

- [ ] Add real URLs only through Actions Secrets.
- [ ] Review repository access before adding real subscription Secrets; candidate generation starts only after canonical declarations are committed.
- [ ] Leave Release and Gist disabled unless public credential exposure is intentionally accepted; enabling either also requires its backend variable and the exact acknowledgement variable.
- [ ] Import the validated file into a non-critical Mihomo/FlClash profile first.
- [ ] Verify service behavior independently of health-check reachability.
- [ ] Establish credential rotation and Artifact/Release retention procedures.
