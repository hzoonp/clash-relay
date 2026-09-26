# Publishing and promotion

Publication is downstream of Public Config v2 validation, generation, runtime qualification, current-policy audit, Promotion Guard, and the manifest-driven stable Mihomo matrix. A publisher receives already qualified and validated bytes and cannot influence node selection or routing policy.

## Public-repository production path

The production workflow is designed to run from a public repository without turning GitHub into credential storage. Real subscription URLs are supplied only through trusted Secrets on `main`; generated and qualified candidates remain on the ephemeral runner until private publication to Cloudflare Workers KV.

Pull requests use fictional sources and do not receive production subscription Secrets. Automatic `push` remains hard-latched to dry-run mode. Manual `workflow_dispatch` also remains a dry run unless `publish=true` is explicitly selected. Scheduled publication is a separate intentional state controlled by `CLASH_RELAY_SCHEDULE_PUBLISH` and can never be inferred from a push or manual input.

The supported schedule runs every six hours and re-fetches current private subscriptions before executing generation, source audit, browsing/transport qualification, service qualification, declared client-path hardening, current-policy audit, Promotion Guard, and the complete stable Mihomo matrix. The authorized upstream `hzoonp/clash-relay` deployment publishes when `CLASH_RELAY_SCHEDULE_PUBLISH` is unset or exact lowercase `true`; setting it to `false` suspends unattended publication. Public forks remain dry-run unless they explicitly opt in with the repository variable set to exact lowercase `true` after their manual bootstrap.

Every publishing schedule run remains bound to the exact validated SHA. Any qualification rejection, SHA mismatch, Promotion Guard block, Mihomo rejection, ambiguous release transaction, or other mandatory-gate failure stops before activation. The same versioned rollback and compensating transaction semantics used by manual publication apply to scheduled publication.

If an explicitly or automatically published final candidate is already active, publication is idempotent: production bytes stay unchanged and the previous-release pointer is not rotated.

## Secret masking and privacy

`CLASH_RELAY_SUBSCRIPTIONS` is one structured GitHub Secret that maps logical `secret_name` values to private URLs. The production adapter masks resolved URLs before fetch begins. No real URL is written to tracked YAML, generated candidate YAML, public summaries, or GitHub release assets.

The credential-bearing candidate never crosses a GitHub Artifact boundary. Private stage files, reports, detailed Mihomo errors, scheduler state, AI cache, production metrics, and operational SLO state remain runner/KV-private and are removed from the runner lifecycle when no longer needed.

## Canonical lifecycle

The sensitive lifecycle is owned by in-process package application APIs:

```text
Public Config v2 + private Secrets
  -> subscription fetch / sanitize / NodeInventory
  -> PolicyCompiler -> RuntimeGraph -> MihomoSerializer
  -> generated current-policy audit
  -> browsing / transport qualification
  -> ServiceQualification registry
  -> declared service client-path hardening
  -> qualified current-policy audit
  -> fetch current production baseline
  -> Promotion Guard
  -> every stable core in tools/mihomo-versions.json
  -> immutable versioned release staging + read-back verification
  -> activate fixed client-facing production key
  -> fetch final HTTPS entry / exact digest / YAML / real Mihomo smoke
  -> commit current/previous release pointers
  -> production proof + best-effort derived state / metrics / SLO
```

Scripts are thin adapters. Python production stages do not launch sibling Python scripts or exchange business results through stdout/stderr JSON. Mihomo remains an explicit external-program boundary.

Dry-run execution intentionally omits the production-only baseline/Promotion Guard and write stages while preserving the candidate-generation, qualification, current-policy audit, and real-core validation path. Publishing scheduled execution uses the same full publication lifecycle as manual `publish=true`; it does not have a shortened or alternate publisher path.

## Cloudflare Workers KV

The default public-safe declaration keeps GitHub credential-bearing publication disabled and Cloudflare KV enabled:

```yaml
publishing:
  artifact: false
  github_release:
    enabled: false
    allow_sensitive_public_release: false
  gist:
    enabled: false
    allow_sensitive_unlisted_gist: false
  cloudflare_kv:
    enabled: true
    key: production-config
```

GitHub Actions expects:

- Secret `CLOUDFLARE_API_TOKEN` with narrowly scoped Workers KV write permission;
- Secret `CLASH_RELAY_PROFILE_URL` containing the complete fixed HTTPS client entry;
- Variable `CLOUDFLARE_ACCOUNT_ID`;
- Variable `CLOUDFLARE_KV_NAMESPACE_TITLE`;
- Variable `CLASH_RELAY_SCHEDULE_PUBLISH` when a public fork intentionally opts into scheduled publication or when an operator needs to suspend/restore the upstream scheduled path.

The complete Worker profile URL is a bearer credential. Store it only in the
`CLASH_RELAY_PROFILE_URL` GitHub Secret; never copy it into tracked files, logs,
workflow inputs or artifacts. Publish and rollback both require it. Dry-run does
not fetch the entry or require this secret.

## Final client-entry smoke

After the production KV value is activated and read-back verified, publication
fetches the configured entry directly over verified HTTPS, requires HTTP 200,
compares the exact decompressed response SHA-256 with the candidate, parses its
YAML, then runs the primary validated Mihomo core's configuration and startup
checks. Redirects are rejected. The Worker must serve the fixed production key
without rewriting YAML; a Worker that resolves `current-release-v1` instead is
not compatible with this activation order.

Bounded retries wait 2, 5, 10, 20 and 30 seconds after the first attempt for edge
propagation. Requests use `Cache-Control: no-cache`; configure Worker caching to
make the updated value visible within this window. A successful check proves
the runner's final-entry path, not every carrier or every edge location.

Only static outcomes and attempt counts are reported. URL, response content,
node identities, YAML errors and core diagnostics are suppressed. The exact
downloaded response and core validation copies are temporary and cleaned up.

Smoke failure restores the previous production bytes through the existing
compensation path before release pointers advance. Failed first publication
removes its newly activated production value and restores its prior pointer;
unconfirmed cleanup is reported as unknown. An unchanged release is checked
again without rotating history. KV compensation is still eventually consistent:
edge caches can briefly serve the attempted release, so this is not an atomic
cutover or proof that every client has already observed restoration.

`scripts/publish_release_bundle.py` and `clash-relay publish-cloudflare-kv` require
`--mihomo-bin` and use the same smoke and compensation path. These are activation
adapters for already audited candidates; normal publication should use the full
production lifecycle, including qualification, Promotion Guard and the stable
core matrix.

## Versioned release transaction

Existing clients continue reading the configured fixed key such as `production-config`. Private storage uses a stable storage-schema-v1 layout:

```text
production-config.release-v1.<sha256>.config
production-config.release-v1.<sha256>.manifest
production-config.current-release-v1
production-config.previous-release-v1
production-config.release-journal-v1
```

The `v1` suffix is the private storage schema version, not the clash-relay product major version. The release ID is the SHA-256 of the exact candidate bytes.

Publication:

1. writes or verifies the immutable new config object;
2. writes or verifies its exact immutable manifest;
3. ensures the current production bytes have a versioned immutable object when a current value exists;
4. updates and read-back verifies the fixed client-facing production key;
5. verifies the final client HTTPS entry, exact digest, YAML and real Mihomo load;
6. commits the previous-release pointer;
7. commits the current-release pointer.

There is no v2 `previous-v1` compatibility slot, write, or fallback.

If a pointer commit fails after client-visible bytes changed, the release layer attempts compensating restoration of the previous exact bytes and pointer state. An ambiguous remote PUT is followed by exact read-back before it is treated as failed. Workers KV is therefore described as a **compensating transaction**, not a cross-key atomic database transaction.

## Rollback

Manual rollback resolves only `previous-release-v1`. The pointer must reference exact immutable config bytes whose SHA-256 matches the release ID and whose manifest matches exactly.

Before activation, the historical candidate must pass the current safety contract:

1. current production/source-isolation audit;
2. current Routing V2 contract;
3. current ACL4SSR fidelity and OpenAI App route-lock requirements;
4. current OpenAI client-path audit with no historical-shape exemption;
5. every stable Mihomo core from `tools/mihomo-versions.json`.

A historical config that still parses in Mihomo but violates today's source permissions, routing contract, service hardening, or real-core matrix is not eligible for rollback. Rollback activates only through the same versioned release transaction used for normal production.

## Derived state and operational SLO

AI qualification cache and scheduler history are optimization state. Production metrics and operational SLO history are aggregate-only observability state. They persist after the production release commits and are best-effort.

A derived-state/SLO write failure cannot convert an invalid candidate into a valid publication and cannot falsely undo a committed validated release. Later runs rebuild missing derived state through live qualification.

## GitHub source releases

The source-only GitHub Release workflow is separate from production configuration publication. It reads the package version from `pyproject.toml`, requires matching `docs/releases/<version>.md`, checks out the exact reusable-workflow `Validated SHA`, and creates a source release only when that tag does not already exist.

Generated production configuration, subscription responses, Cloudflare KV data, scheduler/cache state, node-level results, metrics, and SLO state are never source-release assets.

## Failure semantics

Any failure before production activation leaves the previous production value active. This includes subscription/schema errors, source isolation violations, qualification rejection, current-policy drift, Promotion Guard rejection, Mihomo rejection, missing Cloudflare configuration, and immutable release-staging failure.

If activation has committed and a later proof/derived-state/SLO operation fails, the release remains committed and the failure is reported as post-commit observability degradation rather than misrepresented as a pre-publication safety failure.

## Read-only reconciliation after an ambiguous commit

When a release transaction reports an unknown commit state, preserve the exact candidate and the exact production bytes observed before that attempt in private storage. Run `clash-relay reconcile-release` with `--candidate` and `--previous` to read the production key and release pointers without publishing, retrying, or compensating. For an ambiguous first release, use `--first-release` instead of `--previous`.

The command reports `committed`, `not_committed`, or `unknown`. Only `committed` and `not_committed` are conclusive; an `unknown` result requires investigation and must not be treated as permission to retry mutation automatically.

## Immutable release retention

`release-journal-v1` is private derived state. It records each successfully observed immutable release ID and its first observation epoch; missing journal state is compatible with pre-journal releases and is initialized after a later successful publication. Journal persistence is best effort and cannot change the outcome of an already committed release.

`clash-relay plan-release-retention --retention-days 30` reads the journal and both live pointers, then emits a read-only candidate list and plan digest. It always protects `current-release-v1`, `previous-release-v1`, and IDs inside the retention window. It never deletes a key.

To execute a reviewed plan, save that exact JSON privately and run `clash-relay apply-release-retention --plan PRIVATE_PLAN.json --confirm-retention-delete`. The command recomputes the plan before mutation and rejects a stale digest. It deletes only each approved immutable config/manifest pair, then removes those IDs from the journal. An ambiguous delete response stops execution and leaves the journal unchanged for the affected remainder.

The supported production path is the manual `Apply immutable release retention` workflow. It requires the reviewed plan digest, `confirm=true`, the exact validated `main` SHA, and the same production concurrency group as publication and rollback. The workflow recreates the plan privately and refuses to delete when its digest has changed.
