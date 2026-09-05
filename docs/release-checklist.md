# v2 release qualification checklist

This checklist is the human release review that complements the exact-SHA automated validation workflow. It must not introduce a second list of Mihomo versions or weaken any automated gate.

## Repository and public contract

- [ ] `python scripts/repository_audit.py` passes on the exact staged tree.
- [ ] `git log --all -p` contains no real subscription URL, token, node credential, private controller, or generated production config.
- [ ] Example and fixture hosts remain fictional/reserved and fixture identities contain no personal data.
- [ ] `config.yaml`, `subscriptions.yaml`, and `policies.yaml` are v2-only public declarations.
- [ ] `subscription_1` is limited to `allowed_uses: [browsing, ai]`.
- [ ] Explicit node multipliers strictly greater than `2.0` are rejected and exactly `2.0` remains eligible.
- [ ] Removed public v1 fields have no runtime compatibility aliases.
- [ ] Stale phase-only documentation is absent from the release tree.

## Exact-SHA validation

- [ ] The reusable validation workflow is bound to the exact candidate commit SHA.
- [ ] Python 3.11, 3.12, and 3.13 quality jobs pass.
- [ ] Hash-verified dependency installation passes.
- [ ] Ruff lint and format checks pass.
- [ ] Static typing for application boundaries passes.
- [ ] All non-integration tests and the coverage floor pass.
- [ ] Architecture, documentation, operational-SLO, service-qualification, supply-chain, and repository audits pass.
- [ ] Deterministic double generation and byte comparison pass.
- [ ] Routing V2 Drift Guard passes.
- [ ] Every stable Mihomo core declared in `tools/mihomo-versions.json` passes download verification, candidate validation, real startup, and provider `HEAD` integration tests.
- [ ] The final `Validated SHA` job succeeds.

## Production safety rehearsal

- [ ] A fresh fictional Fork path succeeds from declarations and Secrets through subscription I/O, compiler, qualification, serializer, Mihomo validation, and a `publish=false` production run.
- [ ] Promotion Guard allows an expected compatible candidate and blocks an intentionally unsafe candidate.
- [ ] Failure/chaos regression coverage includes subscription timeout/error payloads, empty inputs, corrupt derived state, ambiguous KV writes, and post-commit observability failures.
- [ ] Immutable Cloudflare KV release objects are read-back verified before activation.
- [ ] Pointer-commit failure restores the previous client-visible bytes when compensation is possible.
- [ ] Rollback resolves only the versioned previous-release pointer, validates exact SHA-256 bytes plus immutable manifest, applies the current policy audit, and passes the complete stable Mihomo matrix before activation.
- [ ] No legacy `previous-v1` rollback fallback exists in the v2 runtime.
- [ ] Operational SLO persistence failure is best-effort and cannot convert a successful release gate into a weaker publication path.

## GitHub settings

- [ ] Protect `main` and require the repository's release-authoritative status checks where repository administration access permits it.
- [ ] Restrict who can modify Actions workflows and production-relevant Python code.
- [ ] Keep default Actions permissions read-only except explicitly scoped release/publication jobs.
- [ ] Secret `CLASH_RELAY_SUBSCRIPTIONS` contains only the private subscription mapping.
- [ ] Secret `CLOUDFLARE_API_TOKEN` is a narrowly scoped Workers KV token.
- [ ] Variable `CLOUDFLARE_ACCOUNT_ID` is configured.
- [ ] Variable `CLOUDFLARE_KV_NAMESPACE_TITLE` resolves to the intended namespace.
- [ ] `PROFILE_TOKEN` is not stored in GitHub.

## Cloudflare and privacy

- [ ] Worker KV binding points to the intended namespace.
- [ ] Worker Secret `PROFILE_TOKEN` is high entropy and has a rotation procedure.
- [ ] Invalid profile tokens return a generic `404`.
- [ ] Valid profile-token requests read the fixed production key and return YAML with `Cache-Control: no-store` and noindex headers.
- [ ] Worker code and logs do not print complete request URLs, profile tokens, subscription URLs, node endpoints, or generated configuration bytes.
- [ ] Generated candidate bytes never appear in Actions Artifacts, GitHub Releases, Gists, Pages, commits, or public logs.
- [ ] Production metrics and operational SLO state remain bounded and aggregate-only.

## Source release

- [ ] `pyproject.toml` contains the intended release version.
- [ ] `docs/releases/<version>.md` exists and matches the package version.
- [ ] The source-release workflow checks out the exact `Validated SHA`.
- [ ] The GitHub Release is source-only and contains no private operational assets.
- [ ] The release tag points to the exact validated `main` commit.
