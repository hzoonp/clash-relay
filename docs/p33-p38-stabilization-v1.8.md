# P33-P38 stabilization and v1.8.0

P33-P38 turns the P27-P32 architecture into the stable production boundary used by v1.8.0. The client-facing Cloudflare KV key, six FlClash public scenario names, source permissions, ACL4SSR ordering, and versioned release transaction remain compatible.

## Architectural truths

Production has three explicit sources of truth:

1. **PolicyContract** owns routing declaration truth. `routing.contract` is mandatory for Routing V2 consumers; Python does not carry a shadow/default production contract.
2. **RuntimeGraph** owns generated topology truth. Reachability, provider traversal, and aggregate runtime inventory consume the same graph.
3. **ProductionPipeline** owns execution truth. GitHub Actions invokes one application entrypoint and does not independently orchestrate generation, qualification, promotion, validation, activation, or proof.

## P33 - complete production boundary

`.github/workflows/publish.yml` is a thin environment adapter. Its production business action is one command:

```text
python scripts/run_production_release.py
```

The application lifecycle is:

```text
generate
  -> load private derived state
  -> obtain pinned primary Mihomo
  -> pre-audit + qualification + post-audit
  -> fetch current production baseline (publish mode)
  -> Promotion Guard (publish mode)
  -> complete pinned stable Mihomo matrix
  -> existing versioned Cloudflare release transaction (publish mode)
  -> best-effort derived-state persistence
  -> existing production proof
  -> aggregate release manifest
```

The proven `release_bundle.py` transaction remains the activation/compensation implementation. P33 moves orchestration ownership; it does not invent a new remote transaction.

## P34 - canonical Policy Model v2

The canonical `policies.yaml` is now a physical v2 manifest:

```text
policies.yaml
  -> policies/routing.yaml
  -> policies/scheduling.yaml
  -> policies/classification.yaml
  -> policies/topology.yaml
```

`PolicyDocument` composes those disjoint physical fragments into the same validated canonical domain document consumed by existing generation and audit code. `scripts/migrate_policy_v2.py` provides a deterministic v1-to-v2 migration and verifies normalized equivalence before succeeding.

## P35 - remove routing fallback truth

The legacy Python `PolicyContract` defaults and Routing V2 default document are removed. Calling Routing V2 without an explicit `routing` declaration or without `routing.contract` fails closed. This prevents group names, binding targets, region aliases, and exclusions from silently diverging between YAML and Python.

## P36 - config-domain cleanup

Canonical production does not use the generic `services.yaml` abstraction. The file remains as an explicitly empty compatibility extension for existing forks, but CI prevents canonical production semantics from being added there. Service-specific AI admission continues to live in the qualification/routing policy model rather than becoming a second source of truth.

Deleting the generic Service extension entirely is intentionally deferred to a future major compatibility boundary because external forks may still use it.

## P37 - aggregate release manifest

Every successful lifecycle run builds a safe release manifest containing only aggregate/identity information:

- project version and Policy Model version;
- exact config SHA-256 and byte size;
- release ID and previous release ID when published;
- runtime group/provider/unique-node counts;
- source counts aggregated by use, without source IDs;
- qualification state;
- Promotion Guard state;
- validated Mihomo core list;
- UTC generation timestamp and Git commit SHA when available.

The manifest explicitly excludes node names, servers, ports, credentials, subscription URLs, probe endpoints, and child-process diagnostics. Its Markdown form is appended to the GitHub Actions step summary. The private candidate directory is removed in a `finally` block.

The immutable P17 release-object manifest is **not** changed, so historical rollback verification remains byte-compatible.

## P38 - v1.8.0 stabilization

v1.8.0 is the compatibility boundary for the architecture cleanup. CI continues to cover Python 3.11/3.12/3.13, deterministic fictional generation, architecture contracts, repository safety, and the pinned real-Mihomo stable matrix.

Before merging this work to `main`, the branch must pass the complete CI suite. Merging remains a separate production authorization because a `main` push can trigger publication.
