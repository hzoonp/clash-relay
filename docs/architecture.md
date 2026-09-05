# Architecture

## Design boundaries

`clash-relay` is a build and production-promotion tool, not a proxy runtime. Its responsibility ends after emitting, qualifying, validating, and privately promoting a standard standalone Mihomo configuration. It does not manage FlClash device state, operate a traffic database, infer commercial provider identity, or inspect user traffic.

The production path is staged and fail closed:

1. **Declaration loading** validates Public Config v2 (`config.yaml`, `subscriptions.yaml`) plus the Policy Model v2 `policies.yaml` manifest and its routing, scheduling, classification, and topology fragments.
2. **Secret resolution** maps logical `secret_name` values to URLs from `CLASH_RELAY_SUBSCRIPTIONS`, a same-name environment variable, or an ignored local secret file.
3. **Fetch** permits HTTPS by default, rejects URL userinfo and private IP literals, bounds transfer/decompression size, validates redirect destinations, and returns UTF-8 text.
4. **Parse/sanitize** accepts common Clash/Mihomo YAML and proxy URIs, rejects YAML aliases, validates proxy shape, and strips user-controlled chaining/interface/routing fields.
5. **Classification** combines source defaults, optional name rules, country aliases, and authoritative exact-node metadata into an immutable node model.
6. **Eligibility selection** applies source use, country, capability any/all/exclude, and cost constraints. `ingest_order` is deterministic ingestion/deduplication order only.
7. **Policy compilation** constructs the runtime draft from declarations and `NodeInventory`, applies ACL4SSR group semantics, source exclusions, manual provider exposure, and browsing regional hardening, validates the public surface, then freezes the completed topology as one `RuntimeGraph`.
8. **Mihomo serialization** converts only the final compiled `RuntimeGraph` into a detached Mihomo mapping. Builder performs no topology mutation after this boundary.
9. **Generated/current-policy audit** verifies Routing V2 and source-to-scenario contracts against the concrete graph/candidate.
10. **Browsing/transport qualification** admits only live candidates into automatic browsing/general transport inventories and may use the bounded typed transient retry policy.
11. **Service qualification** iterates the ordered `ServiceQualification` registry. Provider-specific probe/cache/diagnostic/route behavior stays behind the implementation boundary.
12. **Declared client-path hardening** applies optional service-specific local-runtime hardening through the same registry, without provider branches in the main pipeline.
13. **Qualified/current-policy audit** revalidates the exact final candidate.
14. **Promotion Guard** rejects unsafe topology/config drift before publication.
15. **Real-core matrix validation** tests the exact final candidate against every stable Mihomo core declared by `tools/mihomo-versions.json`.
16. **Versioned promotion** stages and read-back verifies an immutable private release, updates the fixed client-facing Cloudflare KV key, and commits release pointers with compensation when a pointer commit fails.
17. **Post-commit proof/derived state** records only safe aggregate proof, scheduler/cache state, metrics, and operational SLO outcomes. Best-effort observability cannot weaken a publication gate.

## Canonical data flow

```text
Declarations
  -> Subscription I/O
  -> NodeInventory
  -> PolicyCompiler
  -> RuntimeGraph
  -> qualification
  -> Qualified Graph
  -> MihomoSerializer
  -> config.yaml
  -> audit / Promotion Guard / real Mihomo
  -> immutable private release
```

Four concepts are deliberately separate:

- **Declarations** describe desired policy and source permissions. Policy Model v2 is physically split into routing, scheduling, classification, and topology fragments.
- **`NodeInventory`** is the sanitized, classified, deduplicated set of eligible source nodes supplied to policy compilation.
- **`RuntimeGraph`** is the final normalized pre-qualification topology produced by `PolicyCompiler` and the topology truth for traversal/audit.
- **`CandidateArtifact`** identifies immutable logical transitions between generated and qualified stages.

`PolicyCompiler` is the only builder-facing topology constructor. `generator.py` is a low-level draft-construction primitive used by the compiler; its mutable mapping is not a serialized artifact and must not escape the compiler. `MihomoSerializer` is the only boundary that turns the final graph into the candidate mapping consumed by validation and YAML rendering.

Canonical production names and Routing V2 fidelity rules are data under `routing.contract`; validation code consumes the contract rather than embedding production selector names or region exclusions as parallel Python policy.

## Application boundaries

Internal Python orchestration is in-process and typed. Scripts are adapters that parse CLI/environment input, call package application APIs, and render safe output. Python application layers do not launch sibling Python scripts or exchange business results through stdout/stderr JSON.

True external programs remain explicit subprocess boundaries. Mihomo is the principal example: the project downloads a pinned official binary, validates its asset identity, then uses the core as the runtime authority for load/start/provider integration tests.

## Module responsibilities

| Module | Responsibility |
|---|---|
| `policy_document.py` | require and compose Policy Model v2 |
| `config_loader.py` | schema loading and cross-file semantics |
| `secrets.py` | URL injection only |
| `fetch.py` | bounded network/local fixture reads |
| `subscription_parser.py` / `uri_parser.py` | untrusted subscription extraction and sanitization |
| `classify.py` / `selector.py` | immutable node classification and pure eligibility predicates |
| `generator.py` | low-level deterministic runtime-draft construction |
| `policy_compiler.py` | authoritative topology compilation and graph freezing |
| `runtime_graph.py` | normalized topology traversal/reachability and immutable stage model |
| `mihomo_serializer.py` | detached serialization of the compiled graph |
| `policy_contract.py` | declarative Routing V2 contract |
| `routing_v2_audit.py` | current contract versus concrete graph audit |
| `qualification_pipeline.py` | provider-agnostic qualification orchestration |
| `service_qualification.py` | typed service extension contract and ordered provider registry |
| `ai_application.py` | service-qualification application orchestration using the registry |
| `validator.py` | output graph, reference, isolation, and leakage validation |
| `mihomo.py` / `mihomo_matrix_application.py` | actual core validation and manifest-driven stable matrix |
| `production_lifecycle.py` | canonical production application sequencing |
| `production_application.py` | typed production package services |
| `promotion_guard.py` | pre-publication safety comparison |
| `release_bundle.py` | immutable release staging, activation pointers, compensation, versioned rollback reads |
| `operational_slo.py` / `slo_application.py` | aggregate-only bounded attempt outcomes and SLO persistence |
| `publication.py` / `publishers/` | publication safety and private output backend |
| `cli.py` | local/CI command surface |

## Compilation graph

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

For a regular pool:

```text
Public SELECT (one implementation target)
  -> hidden FALLBACK (ordered countries/regions)
    -> hidden URL-TEST AUTO
      -> one inline provider
        -> eligible runtime proxies
```

The provider and AUTO layer share the pool's data-driven probe definition. Mihomo makes live choices only within nodes that already passed static business eligibility and any required production qualification.

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

Every subscription-supplied `dialer-proxy` is removed first. Static validation permits the field only in generated chain-exit providers and only when it references the generated entry AUTO group.

## Production candidate stages

```text
compiled RuntimeGraph
  -> serialized generated.yaml
  -> 01-generated.yaml
  -> 02-browsing-transport.yaml
  -> 03-ai.yaml
  -> 04-service-client-path.yaml
  -> config.yaml
  -> current-policy audit
  -> Promotion Guard
  -> pinned stable Mihomo matrix
  -> immutable release
  -> production key
  -> current/previous release pointers
```

All stage files remain under the private runner directory and are deleted in lifecycle cleanup. GitHub Artifact/Release/Gist are not used for credential-bearing output.

## Determinism

Stable generated output comes from:

- schema-constrained plain data;
- sorted source processing by `(ingest_order, id)`;
- fingerprint-based deduplication;
- stable candidate ordering and runtime names;
- explicit dictionary insertion order;
- alias-free YAML output;
- no timestamps or environment-specific values in the generated candidate;
- one compiler-to-serializer topology boundary.

Qualification is intentionally environment-sensitive because it measures live service/transport eligibility. The generated pre-qualification artifact remains byte-deterministic and CI generates it twice and compares exact bytes.

## Trust and release boundaries

Public declarations are malformed-capable input. Subscriptions are fully untrusted. Secrets are trusted only as opaque URLs and never as configuration fragments. Qualification operates only on the private candidate. Mihomo is the syntax/runtime authority after project validation. Publication backends cannot alter generation or bypass current-policy, Promotion Guard, or real-core validation.

Cloudflare Workers KV does not provide a multi-key transaction. The release layer uses verified immutable objects plus compensating writes rather than claiming cross-key atomicity. Rollback reads only the versioned previous-release pointer and requires matching SHA-256 bytes and manifest; the removed legacy `previous-v1` slot is not a v2 runtime fallback.

CI publication is bound to an exact validated commit SHA. The source-only GitHub Release is created from that same SHA and never contains private production configuration or derived operational state.
