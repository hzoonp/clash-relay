# Publishing and promotion

Publication is intentionally downstream of generation and AI qualification. A publisher receives already qualified and validated bytes and cannot influence node selection or policy.

## Public-repository production path

The production workflow is designed to run from a public repository without turning GitHub into credential storage. It permits real subscription Secrets on trusted `main` runs, but the generated `config.yaml` remains only on the ephemeral GitHub-hosted runner until publication to Cloudflare Workers KV.

Production deployment is restricted to `refs/heads/main`. Pull requests continue to run fictional validation only.

## Secret masking before generation

`CLASH_RELAY_SUBSCRIPTIONS` is intentionally one structured GitHub Secret so an arbitrary number of subscriptions can be declared without changing workflow YAML. Because individual URLs are values derived from that bundle, the deployment job parses the mapping in memory and emits GitHub `::add-mask::` commands for every URL before any subscription fetch begins.

No URL is written to tracked YAML, generated candidate YAML, or build reports. Application-level redaction remains in place as a second layer.

## Single-runner lifecycle

The credential-bearing candidate never crosses a GitHub Artifact boundary. One deployment job performs the complete sensitive lifecycle:

1. validate the public publication declaration before any production Secret is read;
2. register each subscription URL with `::add-mask::`;
3. resolve subscriptions and generate `.work/private/config.yaml` once;
4. statically validate the candidate during generation;
5. download and verify pinned Mihomo v1.19.30 for private AI qualification;
6. shard AI candidate providers across bounded temporary Mihomo processes, select each candidate node through the local Core API, and independently issue the configured ChatGPT / Claude / Gemini `HEAD` requests through that process's local mixed port;
7. collect a separate qualified-node set for OpenAI, Claude, and Gemini; retain the union in AI country inventories, prune empty country inventories, and build hidden service-specific routes that filter those shared providers to only the nodes qualified for that service;
8. fail an individual service closed to a hidden `REJECT` route when that service has no qualified nodes; stop publication entirely only if no node qualifies for any protected AI service or the qualification infrastructure cannot complete safely;
9. validate the exact service-qualified candidate with pinned Mihomo v1.19.30;
10. validate those same qualified candidate bytes with pinned Mihomo v1.19.29;
11. only after both stable cores pass, provide the Cloudflare API token to the final publication step;
12. resolve the configured Workers KV namespace by exact title and write the exact candidate to the configured key;
13. remove the private candidate after successful publication. On any earlier failure, the ephemeral runner is destroyed without updating Cloudflare.

AI qualification receives the generated private candidate, not the original subscription Secret. Temporary Mihomo controller and mixed ports bind to loopback only. Node names, servers, credentials, and per-node service results stay runner-local; the public summary contains aggregate probe outcomes and qualified counts only.

Mihomo failure output for a real candidate is redirected to runner-local files and deliberately not printed in the public Actions log. Those files are never uploaded.

## Service-aware AI qualification

The production qualification gate does not require one node to satisfy all three AI services. Each protected service is evaluated independently against its own configured endpoint and accepted HTTP status range.

The country AI providers keep the union of nodes that qualify for at least one protected service. Hidden routing anchors then apply exact runtime-node filters:

```text
OpenAI rules → OpenAI-qualified hidden route → shared qualified country providers
Claude rules → Claude-qualified hidden route → shared qualified country providers
Gemini rules → Gemini-qualified hidden route → shared qualified country providers
other AI rules → 人工智能 → visible qualified country selectors / DIRECT
```

The dedicated pinned ACL4SSR OpenAI rule provider and strict Claude/Gemini subsets derived from the immutable pinned ACL4SSR AI payload are placed before the generic AI rule. If the pinned AI payload no longer contains the expected Claude/Gemini subset, rewriting fails closed and requires review rather than silently changing service classification.

A `403`, timeout, TLS error, or other rejected result for one service does not prove that a node is unusable for every other AI service. It only removes that node from the affected service route. The accepted status range itself is not widened to make publication succeed.

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

The publisher lists namespaces with the Cloudflare API, requires exactly one exact title match, enforces the 25 MiB KV value limit, and writes the validated bytes to the configured key. It logs only non-sensitive publication metadata such as byte count and SHA-256 digest.

Cloudflare's Worker remains responsible for authenticated delivery to FlClash. The recommended endpoint pattern is:

```text
https://<worker>.<workers-subdomain>.workers.dev/profile/<PROFILE_TOKEN>
```

`PROFILE_TOKEN` must be a Worker Secret and must not be copied into GitHub. The complete URL is a bearer credential.

## GitHub Artifact, Release, and Gist

The codebase retains legacy publication-gate support for Artifact, Release, and Gist for explicit non-default use cases, but the supported public production workflow contains no credential-bearing upload path for any of them.

In Cloudflare KV mode:

- Actions Artifact must remain disabled;
- GitHub Release must remain disabled;
- Gist must remain disabled.

This is enforced both by configuration and by workflow regression tests.

## Failure semantics

Cloudflare is updated only after generation, AI qualification, and both pinned stable-core validations succeed. A subscription fetch error, schema failure, graph error, zero service-qualified AI nodes across all protected services, AI probe infrastructure failure, service-routing rewrite failure, Mihomo rejection, missing namespace, invalid Cloudflare credentials, or KV API failure leaves the previously stored `production-config` untouched.

A failure of one AI node for one service does not abort the whole build. That node is excluded only from the affected service route; it can remain available to another protected AI service if it independently passes that service's probe. AI country inventories retain the union of service-qualified nodes, and a country with no surviving qualified node is removed from the visible `人工智能` selector.

If one protected service has zero qualified nodes, only that service route fails closed to `REJECT`. Publication aborts only if all protected AI service sets are empty or if the qualification infrastructure itself cannot complete safely.

Because Workers KV is a distributed eventually consistent store, clients may briefly continue to receive an older successful value after a new write. The workflow never intentionally publishes an unqualified or unvalidated candidate.
