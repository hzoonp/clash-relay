# Publishing and promotion

Publication is downstream of generation, runtime qualification, current-policy audit, and the pinned stable Mihomo matrix. A publisher receives already qualified and validated bytes and cannot influence node selection or routing policy.

## Public-repository production path

The production workflow is designed to run from a public repository without turning GitHub into credential storage. It permits real subscription Secrets on trusted `main` runs, but generated and qualified candidates remain only on the ephemeral GitHub-hosted runner until private publication to Cloudflare Workers KV.

Production deployment is restricted to `refs/heads/main`. Pull requests continue to use fictional sources and do not receive production subscription Secrets.

## Secret masking before generation

`CLASH_RELAY_SUBSCRIPTIONS` is intentionally one structured GitHub Secret so an arbitrary number of subscriptions can be declared without changing workflow YAML. The deployment job parses the mapping in memory and emits GitHub `::add-mask::` commands for every URL before subscription fetch begins.

No URL is written to tracked YAML, generated candidate YAML, public summaries, or GitHub artifacts. Application-level redaction remains in place as a second layer.

## Single-runner lifecycle

The credential-bearing candidate never crosses a GitHub Artifact boundary. One deployment job performs the complete sensitive lifecycle:

1. validate the public publication declaration before any production Secret is read;
2. register each subscription URL with `::add-mask::`;
3. generate `.work/private/generated.yaml` and its private build report;
4. run the production/source-isolation audit on the generated graph;
5. restore private scheduler history and AI qualification cache from Cloudflare KV;
6. download the primary pinned stable Mihomo core from `tools/mihomo-versions.json`;
7. run `qualify_candidate.py`, which copies the candidate through generated, browsing/transport, AI, and final private stages;
8. re-run the production and Routing V2 audits against the exact final candidate;
9. validate those exact bytes with every pinned stable Mihomo core through `validate_mihomo_matrix.py`;
10. for a dry run, emit only the privacy-safe production proof;
11. for publication, stage and read-back verify an immutable versioned release;
12. activate the exact validated bytes at the fixed client-facing KV key and commit release pointers;
13. persist AI cache and scheduler history as best-effort derived state;
14. render the publication proof and remove `.work/private`.

Qualification uses the generated private candidate, not the original subscription Secret. Temporary Mihomo controller and mixed ports bind to loopback only. Node names, servers, credentials, and per-node service results stay runner-local; public summaries contain aggregate outcomes only.

Mihomo failure output for a real candidate is redirected to runner-local files and deliberately not printed in the public Actions log. Those files are never uploaded.

## Unified staged qualification

P16 gives qualification one orchestration owner while retaining the existing browsing/transport and AI executors:

```text
.work/private/generated.yaml
  -> .work/private/stages/01-generated.yaml
  -> .work/private/stages/02-browsing-transport.yaml
  -> .work/private/stages/03-ai.yaml
  -> .work/private/config.yaml
```

The generator output is never modified in place by workflow orchestration. Each live qualification stage receives a private copy and the workflow validates only the explicit final artifact.

## Service-aware AI qualification

The production qualification gate does not require one node to satisfy all protected AI services. Each service is evaluated independently against its configured endpoint and accepted HTTP status range.

The country AI providers keep the union of nodes that qualify for at least one protected service. Hidden routing anchors then apply exact runtime-node filters:

```text
OpenAI rules -> OpenAI-qualified hidden route -> shared qualified country providers
Claude rules -> Claude-qualified hidden route -> shared qualified country providers
Gemini rules -> Gemini-qualified hidden route -> shared qualified country providers
other AI rules -> 人工智能 -> visible qualified country selectors / DIRECT
```

A rejected result for one service only removes the node from that service route. If one protected service has no qualified nodes, that service fails closed to `REJECT`. Publication stops only when the qualification infrastructure fails or no protected AI service has a qualified route under the existing policy.

## Cloudflare Workers KV

The default public-safe declaration is:

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

The Cloudflare publication gate refuses to run if Artifact, Release, or Gist is enabled at the same time.

GitHub Actions expects:

- Secret `CLOUDFLARE_API_TOKEN` with Workers KV edit/write permission;
- Variable `CLOUDFLARE_ACCOUNT_ID`;
- Variable `CLOUDFLARE_KV_NAMESPACE_TITLE`, for example `clash-relay-config`.

Cloudflare's Worker remains responsible for authenticated delivery to FlClash. The complete Worker profile URL is a bearer credential and must not be copied into GitHub.

## Versioned release transaction

Existing clients continue reading the configured fixed key, for example `production-config`. P17 adds private operational keys without changing the subscription URL:

```text
production-config.release-v1.<sha256>.config
production-config.release-v1.<sha256>.manifest
production-config.current-release-v1
production-config.previous-release-v1
production-config.previous-v1              # migration fallback
```

The release ID is the SHA-256 of the exact candidate bytes.

Publication performs these checks and writes:

1. write/read-back verify the immutable new config and manifest;
2. if an older production value exists, ensure those exact bytes also have an immutable release object;
3. preserve the legacy previous slot during migration;
4. update/read-back verify the fixed production key;
5. update previous-release pointer;
6. update current-release pointer as the commit marker.

If a release-pointer commit fails after the fixed production value changed, the publisher restores the previous exact production bytes and the old pointer state when a previous value exists. An ambiguous remote PUT response is followed by a read-back check before it is treated as failure.

Workers KV does not expose a multi-key transaction, so this is intentionally described as a **compensating transaction**, not as cross-key atomicity.

## Rollback

Manual rollback resolves `previous-release-v1` first and falls back to legacy `previous-v1` only for pre-P17 state.

Before activation, the preserved candidate must pass:

1. the **current** production/source-isolation audit;
2. the current Routing V2 contract audit;
3. current ACL4SSR fidelity checks performed by the production audit path;
4. every pinned stable Mihomo core from `tools/mihomo-versions.json`.

The rollback candidate is then activated through the same versioned release transaction. A historical config that parses successfully in Mihomo but violates today's source permissions cannot be restored.

## Derived scheduler/qualification state

AI qualification cache and scheduler history are optimization state, not production configuration. They are persisted after the versioned production release commits.

Their write steps are best effort. If a cache/history write fails, the workflow records a warning but keeps the successfully committed production release as successful. Later runs safely rebuild missing state through live qualification rather than confusing operators with a red deployment after production already changed.

## GitHub Artifact, Release, and Gist

The codebase retains publication-gate support for explicit non-default development cases, but the supported public production workflow contains no credential-bearing upload path for Artifact, Release, or Gist.

In Cloudflare KV mode:

- Actions Artifact must remain disabled;
- GitHub Release must remain disabled for generated production config;
- Gist must remain disabled.

The source-only GitHub release workflow remains separate from production configuration publication.

## Failure semantics

The fixed Cloudflare production key is updated only after generation, qualification, current-policy audit, and every pinned stable-core validation succeeds. Subscription errors, schema errors, graph errors, qualification infrastructure errors, current-policy failures, Mihomo rejection, missing namespace, invalid Cloudflare credentials, or release staging failures leave the previous production value in place.

When the client-facing production write succeeds but a later pointer commit fails, P17 attempts compensating restoration of the exact prior production bytes. Release objects are immutable and may remain staged even when activation fails; an unreferenced staged release is not client-visible.

Because Workers KV is eventually consistent, clients may briefly continue receiving an older successful value after a committed write. The workflow never intentionally publishes a candidate that has skipped qualification, current-policy audit, or the pinned stable-core matrix.
