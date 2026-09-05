# Publishing and promotion

Publication is downstream of Public Config v2 validation, generation, runtime qualification, current-policy audit, Promotion Guard, and the manifest-driven stable Mihomo matrix. A publisher receives already qualified and validated bytes and cannot influence node selection or routing policy.

## Public-repository production path

The production workflow is designed to run from a public repository without turning GitHub into credential storage. Real subscription URLs are supplied only through trusted Secrets on `main`; generated and qualified candidates remain on the ephemeral runner until private publication to Cloudflare Workers KV.

Pull requests use fictional sources and do not receive production subscription Secrets. Push and scheduled events publish only through the release-authoritative exact-SHA validation chain. Manual `workflow_dispatch` remains a dry run unless `publish=true` is explicitly selected.

The supported schedule is a full production refresh, not a lighter synchronization path. It re-fetches current private subscriptions and executes the same generation, source audit, browsing/transport qualification, service qualification, declared client-path hardening, current-policy audit, Promotion Guard, stable Mihomo matrix, and versioned Cloudflare KV release transaction before client-visible bytes can change.

If the exact final candidate is already active, publication is idempotent: production bytes stay unchanged and the previous-release pointer is not rotated.

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
  -> commit current/previous release pointers
  -> production proof + best-effort derived state / metrics / SLO
```

Scripts are thin adapters. Python production stages do not launch sibling Python scripts or exchange business results through stdout/stderr JSON. Mihomo remains an explicit external-program boundary.

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
- Variable `CLOUDFLARE_ACCOUNT_ID`;
- Variable `CLOUDFLARE_KV_NAMESPACE_TITLE`.

The complete Worker profile URL is a bearer credential and must not be copied into GitHub.

## Versioned release transaction

Existing clients continue reading the configured fixed key such as `production-config`. Private storage uses a stable storage-schema-v1 layout:

```text
production-config.release-v1.<sha256>.config
production-config.release-v1.<sha256>.manifest
production-config.current-release-v1
production-config.previous-release-v1
```

The `v1` suffix is the private storage schema version, not the clash-relay product major version. The release ID is the SHA-256 of the exact candidate bytes.

Publication:

1. writes or verifies the immutable new config object;
2. writes or verifies its exact immutable manifest;
3. ensures the current production bytes have a versioned immutable object when a current value exists;
4. updates and read-back verifies the fixed client-facing production key;
5. commits the previous-release pointer;
6. commits the current-release pointer.

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
