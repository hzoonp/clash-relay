# Architecture

## Design boundaries

`clash-relay` is a build and production-promotion tool, not a proxy runtime. Its responsibility ends after emitting, qualifying, validating, and privately promoting a standard standalone Mihomo configuration. It does not manage FlClash device state, operate a traffic database, infer commercial provider identity, or inspect user traffic.

The production pipeline is staged and fail closed:

1. **Declaration loading** validates `config.yaml`, `subscriptions.yaml`, the Policy Model v2 `policies.yaml` manifest and its four owned fragments, plus rule files against JSON Schema and cross-file semantic constraints. Physical Policy Model v1 is not a runtime input; `scripts/migrate_policy_v2.py` is the offline migration path.
2. **Secret resolution** maps logical `secret_name` values to URLs from `CLASH_RELAY_SUBSCRIPTIONS`, a same-name environment variable, or an ignored local secret file.
3. **Fetch** permits HTTPS by default, rejects URL userinfo and private IP literals, bounds transfer/decompression size, validates redirect destinations, and returns UTF-8 text.
4. **Parse/sanitize** accepts common Clash/Mihomo YAML and proxy URIs, rejects YAML aliases, validates proxy shape, and strips user-controlled chaining/interface/routing fields.
5. **Classification** combines source defaults, optional name rules, country aliases, and authoritative exact-node metadata into an immutable node model.
6. **Eligibility selection** applies source use, country, capability any/all/exclude, and cost constraints. Source priority is deterministic ordering only.
7. **Policy compilation** constructs the private runtime draft from declarations and the node inventory, applies ACL4SSR group semantics, source exclusions, manual provider exposure, and browsing regional hardening, validates the browsing public surface, then freezes the completed topology as one `RuntimeGraph`.
8. **Mihomo serialization** converts only that final compiled `RuntimeGraph` into a detached Mihomo mapping. Builder performs no topology mutation after this boundary.
9. **RuntimeGraph/current-policy audit** verifies Routing V2 and source-to-scenario contracts against the concrete final graph.
10. **Qualification pipeline** copies the generated private candidate through browsing/transport and AI qualification stages, producing one explicit final candidate instead of mutating the compiler output across unrelated workflow steps.
11. **Static/current-policy audit** is repeated against the exact qualified candidate.
12. **Real-core matrix validation** tests the exact final candidate against every pinned stable Mihomo core in `tools/mihomo-versions.json`.
13. **Versioned promotion** stages and read-back verifies an immutable private release, updates the fixed client-facing Cloudflare KV key, and commits release pointers with compensation when a pointer commit fails.

## Canonical models

There are four deliberately separate concepts:

- **Declarations** describe desired policy and source permissions. Policy Model v2 is physically split into `routing`, `scheduling`, `classification`, and `topology` fragments.
- **Node inventory** is the sanitized, classified, deduplicated set of eligible source nodes supplied to policy compilation.
- **`RuntimeGraph`** is the final normalized pre-qualification topology produced by `PolicyCompiler` and the topology truth for traversal/audit.
- **`CandidateArtifact`** identifies immutable logical transitions between generated and qualified stages.

`PolicyCompiler` is the only builder-facing topology constructor. The legacy-named low-level `generator.py` module is an internal draft-construction primitive used by the compiler; its mapping is not a serialized artifact and must not escape the compiler. `MihomoSerializer` is the only boundary that turns the final compiled `RuntimeGraph` into the candidate mapping consumed by validation and YAML rendering.

The canonical production names and Routing V2 fidelity rules are data under `routing.contract` in the routing policy fragment. Core validation code consumes that contract rather than embedding production group names or region exclusions as Python constants.

## Module responsibilities

| Module | Responsibility |
|---|---|
| `policy_document.py` | require and compose Policy Model v2 into the canonical semantic policy document |
| `config_loader.py` | schema loading and cross-file semantics |
| `secrets.py` | URL injection only |
| `fetch.py` | bounded network/local fixture reads |
| `uri_parser.py` | common proxy URI decoding |
| `subscription_parser.py` | untrusted subscription extraction and sanitization |
| `classify.py` | country/capability/cost assignment and deduplication |
| `selector.py` | pure business eligibility predicates |
| `generator.py` | low-level deterministic mutable draft construction, callable only from `PolicyCompiler` |
| `policy_compiler.py` | authoritative pre-qualification topology compilation and graph freezing |
| `runtime_graph.py` | normalized topology traversal/reachability and immutable stage model |
| `mihomo_serializer.py` | detached serialization of the final compiled `RuntimeGraph` |
| `policy_contract.py` | declarative production Routing V2 contract |
| `routing_v2_audit.py` | current contract versus concrete RuntimeGraph audit |
| `qualification_pipeline.py` | browsing/transport then AI qualification orchestration |
| `validator.py` | output graph, reference, isolation, and leakage validation |
| `mihomo.py` | actual core load/start tests |
| `mihomo_matrix.py` | pinned validation-core source of truth |
| `release_bundle.py` | immutable release staging, activation pointers, compensation, rollback reads |
| `publication.py` | publication safety gates |
| `publishers/` | private output backends, isolated from generation |
| `cli.py` | local/CI command surface |

## Compilation and scheduling graph

The one-way pre-qualification path is:

```text
Declarations + NodeInventory
  -> PolicyCompiler
    -> base runtime draft
    -> ACL4SSR group semantics
    -> source exclusion pass
    -> manual provider exposure pass
    -> browsing hardening pass
    -> final RuntimeGraph
  -> MihomoSerializer
    -> detached Mihomo candidate
```

For a regular pool inside that graph:

```text
Public SELECT (one implementation target)
  -> hidden FALLBACK (ordered countries/regions)
    -> hidden URL-TEST AUTO
      -> one inline provider
        -> qualified runtime proxies
```

The provider and AUTO layer share the pool's data-driven probe definition from the scheduling policy domain. Mihomo makes live choices only within nodes that already passed static business eligibility.

### Empty behavior

- `on_empty: error`: compilation fails before a candidate is serialized.
- `on_empty: reject`: no provider is emitted; the hidden implementation group contains only `REJECT`.

No pool references another business pool as an implicit fallback.

### Controlled chain

A chain has two independent selectors:

```text
public Chain
  -> hidden chain fallback
    -> hidden exit AUTO
      -> inline exit provider
        -> exit proxy copy with dialer-proxy=<hidden entry AUTO>

hidden entry AUTO
  -> inline entry provider
    -> ordinary eligible first hops
```

Every subscription-supplied `dialer-proxy` is removed first. Static validation permits the field only in `cr_chain_exit_*` providers and only when it references a generated `__CR_CHAIN_ENTRY_AUTO_*` group.

## Production candidate stages

The sensitive production path is explicit:

```text
compiled RuntimeGraph
  -> serialized generated.yaml
  -> 01-generated.yaml
  -> 02-browsing-transport.yaml
  -> 03-ai.yaml
  -> config.yaml
  -> current-policy audit
  -> pinned stable Mihomo matrix
  -> immutable release
  -> production key
  -> release pointers
```

All stage files remain under the private runner directory and are deleted at the end. GitHub Artifact/Release/Gist are not used for credential-bearing output.

## Determinism

Stable generated output comes from:

- schema-constrained plain data;
- sorted source processing by `(priority, id)`;
- fingerprint-based deduplication;
- stable candidate ordering;
- deterministic runtime names;
- explicit dictionary insertion order;
- alias-free YAML output;
- no timestamps or environment-specific values in the generated candidate;
- a single compiler-to-serializer topology boundary.

Qualification is intentionally environment-sensitive because it measures live service/transport eligibility. The generated pre-qualification artifact remains byte-deterministic and CI generates it twice and compares bytes.

## Trust boundaries

Public declarations are still treated as malformed-capable input. Subscriptions are fully untrusted. Secrets are trusted only as opaque URLs and never as configuration fragments. Qualification executors operate only on the private generated candidate. Mihomo is the syntax/runtime authority after project validation. Publication backends cannot alter generation or bypass current-policy and core validation.

Cloudflare Workers KV does not provide a multi-key transaction. The release layer therefore uses verified immutable objects plus compensating writes rather than claiming cross-key atomicity. The fixed client-facing production key remains compatible with existing subscription URLs.

See [`p14-p18.md`](p14-p18.md) for the stabilization contract and release/rollback details.
