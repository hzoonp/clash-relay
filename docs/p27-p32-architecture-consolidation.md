# P27-P32 Architecture Consolidation

This phase consolidates the production architecture after the rapid P14-P26 capability expansion. It intentionally preserves the existing versioned Cloudflare release/rollback transaction and focuses on reducing duplicate truth, duplicate topology logic, and workflow-owned business rules.

## The three canonical truths

Production code now has three explicit architectural ownership boundaries:

1. **PolicyContract = declaration truth**
   - `routing.contract` owns public selector names, automatic selector names, compatibility bindings, AI aliases, source bindings, and precedence edges.
   - A project that declares Routing V2 must declare `routing.contract`; production no longer silently mixes a partial YAML contract with Python defaults.
   - Legacy projects with no routing declaration at all retain a compatibility fallback.

2. **RuntimeGraph = topology truth**
   - Generated proxy groups, providers, proxy nodes, and controlled `dialer-proxy` edges are indexed and traversed through `RuntimeGraph`.
   - Production source-reachability auditing and manual-provider traversal no longer maintain private BFS/DFS implementations.
   - Unresolved runtime references in production reachability checks fail closed.

3. **ProductionPipeline = execution truth**
   - Pre-qualification audit, browsing/transport qualification, AI qualification, OpenAI client-path hardening, post-qualification audit, and aggregate-only summaries are orchestrated by one application service.
   - GitHub Actions remains transport/orchestration glue and must not embed Python business rules.
   - The proven immutable release transaction remains separate in `release_bundle.py`.

## P27 - Contract unification

`routing.contract` is authoritative whenever Routing V2 is declared. `routing_shadow.py` and Routing V2 audit consume semantic contract keys instead of defining another set of production selector literals.

CI includes an architecture audit that rejects known regressions such as hard-coded production public group names inside the routing shadow/audit layer.

## P28 - RuntimeGraph consolidation

`RuntimeGraph` now exposes common queries for:

- resolved graph traversal;
- reachable providers;
- deterministic provider traversal order;
- reachable subscription sources.

Production source-isolation auditing requires resolved graph references. A missing runtime target therefore cannot be interpreted as an empty source set and accidentally pass isolation checks.

## P29 - ProductionPipeline

The production candidate path is now conceptually:

```text
private generation
  -> pre-qualification composite audit
  -> browsing/transport qualification
  -> AI service qualification
  -> OpenAI client-path hardening
  -> post-qualification composite audit
  -> promotion guard
  -> pinned stable Mihomo matrix
  -> immutable Cloudflare release transaction
  -> best-effort derived state persistence
```

`ProductionPipeline` owns the composite audit and qualification sequence. Workflow steps pass files, credentials, and execution parameters; they do not re-implement the Python policy.

## P30 - Policy Model v2

Policy Model v2 is an **optional physical composition format**. The canonical root project can remain on the existing monolithic `policies.yaml` while forks may split large policy documents into fragments.

Example manifest:

```yaml
version: 2
fragments:
  runtime: policies/runtime.yaml
  classification: policies/classification.yaml
  qualification: policies/qualification.yaml
  topology: policies/topology.yaml
```

Rules:

- fragment paths must remain below the manifest directory;
- fragments contribute disjoint top-level policy sections;
- fragments may not declare their own `version` or nested `fragments` manifest;
- duplicate top-level sections fail closed;
- the composed document is validated against the existing canonical policy schema before any domain consumer receives it.

The loader normalizes v2 back into one canonical domain document. Generator, routing, qualification, and audit code therefore do not depend on the physical file layout.

## P31 - Production promotion guard

Passing syntax, source isolation, qualification, and Mihomo validation is necessary but not sufficient for replacing a healthy production configuration after an upstream subscription collapse.

Before release activation, production now compares the qualified candidate with the exact client-visible current production configuration. The default `promotion-guard.yaml` requires:

- candidate unique runtime-node inventory >= 50% of current production;
- candidate provider inventory >= 50% of current production;
- general, browsing, and AI source diversity >= 50% of the current production value;
- at least one source remains available for each of general, browsing, and AI.

A missing current production value is treated as a first release and is allowed. The guard emits aggregate counts and ratios only; it never publishes node names, servers, ports, credentials, or subscription URLs.

The promotion guard runs before the stable Mihomo matrix and before the immutable release transaction, so a blocked candidate cannot change the production key.

## P32 - Stabilization contract

CI now validates the supported Python range on 3.11, 3.12, and 3.13. Deterministic generation and the pinned stable Mihomo integration matrix continue to run on Python 3.12.

The architecture contract audit freezes the important boundaries introduced by this phase:

- routing shadow/audit consume `PolicyContract`;
- production reachability and builder provider traversal consume `RuntimeGraph`;
- the publish workflow delegates to `ProductionPipeline` and the Promotion Guard;
- publish workflow YAML contains no inline Python business program;
- Policy Model v2 normalization and the public promotion policy remain present.

## Compatibility and migration

P27-P32 is designed as a consolidation release rather than a behavior redesign:

- the six public FlClash scenarios remain unchanged;
- `subscription_1` remains browsing/AI-only and keeps the existing multiplier/label filtering contract;
- ACL4SSR fidelity, client-owned DNS, OpenAI route lock/client-path hardening, and source isolation remain fail closed;
- existing monolithic policy declarations remain valid;
- the client-facing Cloudflare production key and immutable release/rollback model are preserved;
- no automatic migration to Policy Model v2 is required.

The architectural target after this phase is simple: **one declaration truth, one topology truth, one production execution truth**.
