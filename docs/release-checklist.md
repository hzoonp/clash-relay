# First public release checklist

## Repository contents

- [ ] `python scripts/repository_audit.py` passes on the exact staged tree.
- [ ] `git log --all -p` contains no real subscription URL, token, node credential, private controller, or generated config.
- [ ] Example and fixture hosts remain fictional/reserved and fixture identities contain no personal data.
- [ ] Tracked `config.yaml` and `subscriptions.yaml`, when added, contain metadata only.
- [ ] License, CODE_OF_CONDUCT, CONTRIBUTING, SECURITY, and issue templates are correct for the owner.

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

- [ ] Protect `main` and require all PR status checks.
- [ ] Restrict who can modify Actions workflows and production-relevant Python code.
- [ ] Keep default Actions permissions read-only.
- [ ] Secret `CLASH_RELAY_SUBSCRIPTIONS` contains only the private subscription mapping.
- [ ] Secret `CLOUDFLARE_API_TOKEN` is a narrowly scoped Workers KV edit/write token.
- [ ] Variable `CLOUDFLARE_ACCOUNT_ID` is configured.
- [ ] Variable `CLOUDFLARE_KV_NAMESPACE_TITLE` points to the intended namespace.
- [ ] `PROFILE_TOKEN` is not stored in GitHub.

## Cloudflare settings

- [ ] Worker KV binding points to the intended namespace.
- [ ] The namespace contains the `production-config` key or is ready for the first write.
- [ ] Worker Secret `PROFILE_TOKEN` is a high-entropy random value.
- [ ] Invalid profile tokens return a generic `404`.
- [ ] Valid profile token reads `production-config` and returns YAML.
- [ ] Worker responses use `Cache-Control: no-store` and noindex headers.
- [ ] Worker code and logs do not print the complete request URL or `PROFILE_TOKEN`.

## Fictional acceptance run

- [ ] Use a completely fictional `CLASH_RELAY_SUBSCRIPTIONS` mapping for the first end-to-end run.
- [ ] Confirm every individual derived subscription URL is registered with `::add-mask::` before generation.
- [ ] Confirm generated candidate bytes never appear in an Actions Artifact, Release, Gist, commit, or Pages asset.
- [ ] Confirm Mihomo v1.19.30 validates the candidate.
- [ ] Confirm Mihomo v1.19.29 validates the same candidate.
- [ ] Deliberately break a Mihomo validation and confirm Cloudflare KV is not updated.
- [ ] Deliberately use an invalid namespace title and confirm the prior KV value remains untouched.
- [ ] Confirm the public Actions log does not contain a real candidate or detailed Mihomo failure output.

## Production deployment

- [ ] Add real URLs only through `CLASH_RELAY_SUBSCRIPTIONS`.
- [ ] Confirm `publishing.artifact` is `false`.
- [ ] Confirm GitHub Release and Gist are disabled.
- [ ] Confirm `publishing.cloudflare_kv.enabled` is `true` and its key is correct.
- [ ] Run production only from trusted `main`.
- [ ] Import the Worker profile URL into a non-critical FlClash profile first.
- [ ] Verify URL refresh and service behavior independently of health-check reachability.
- [ ] Store the complete FlClash profile URL as a credential and establish a `PROFILE_TOKEN` rotation procedure.
- [ ] Rotate any credential that ever appeared in repository history, logs, Artifacts, Releases, Gists, screenshots, or support messages.
