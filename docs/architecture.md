# Architecture

## Design boundaries

`clash-relay` is a build tool, not a proxy runtime. Its responsibility ends after emitting and validating a standard standalone Mihomo configuration. It does not manage client state, run on FlClash devices, operate a database, infer commercial provider identity, or maintain an old repository's compatibility surface.

The pipeline is intentionally one-way:

1. **Declaration loading** validates `config.yaml`, `subscriptions.yaml`, `services.yaml`, `policies.yaml`, and rule files against JSON Schema and cross-file semantic constraints.
2. **Secret resolution** maps logical `secret_name` values to URLs from `CLASH_RELAY_SUBSCRIPTIONS`, a same-name environment variable, or an ignored local secret file.
3. **Fetch** permits HTTPS by default, rejects URL userinfo and private IP literals, bounds transfer/decompression size, validates redirect destinations, and returns UTF-8 text.
4. **Parse/sanitize** accepts common Clash/Mihomo YAML and proxy URIs, rejects YAML aliases, validates proxy shape, and strips user-controlled chaining/interface/routing fields.
5. **Classification** combines source defaults, optional name rules, country aliases, and authoritative exact-node metadata into an immutable node model.
6. **Eligibility selection** applies source use, country, capability any/all/exclude, and cost constraints. Source priority is deterministic ordering only.
7. **Generation** creates inline providers and layered hidden groups programmatically. Runtime proxy names include scope, source ID, and a fingerprint suffix.
8. **Static validation** verifies provider non-emptiness, unique names, reference closure, cycle absence, public layering, rule targets, controlled chain references, and URL-secret non-leakage.
9. **Real-core validation** runs Mihomo config test and startup smoke against a temporary validation copy with collision-free local ports.
10. **Promotion** distributes the exact validated bytes through a backend-independent gate.

## Module responsibilities

| Module | Responsibility |
|---|---|
| `config_loader.py` | schema loading and cross-file semantics |
| `secrets.py` | URL injection only |
| `fetch.py` | bounded network/local fixture reads |
| `uri_parser.py` | common proxy URI decoding |
| `subscription_parser.py` | untrusted subscription extraction and sanitization |
| `classify.py` | country/capability/cost assignment and deduplication |
| `selector.py` | pure business eligibility predicates |
| `generator.py` | deterministic Mihomo graph construction |
| `validator.py` | output graph, reference, isolation, and leakage validation |
| `mihomo.py` | actual core load/start tests |
| `publication.py` | publication safety gates |
| `publishers/` | optional output backends, isolated from generation |
| `cli.py` | local/CI command surface |

## Scheduling graph

For a regular service or pool:

```text
Public SELECT (one implementation target)
  -> hidden FALLBACK (ordered countries/regions)
    -> hidden URL-TEST AUTO
      -> one inline provider
        -> qualified runtime proxies
```

The provider and AUTO layer share the service's data-driven probe definition. Mihomo makes live choices only within nodes that already passed static business eligibility.

### Empty behavior

- `on_empty: error`: generation fails before a candidate is written.
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

## Determinism

Stable output comes from:

- schema-constrained plain data;
- sorted source processing by `(priority, id)`;
- fingerprint-based deduplication;
- stable candidate ordering;
- deterministic runtime names;
- explicit dictionary insertion order;
- alias-free YAML output;
- no timestamps or environment-specific values in the candidate.

The report includes a SHA-256 of candidate bytes. CI generates twice and compares bytes, then exercises `--check`.

## Trust boundaries

Public declarations are still treated as malformed-capable input. Subscriptions are fully untrusted. Secrets are trusted only as opaque URLs and never as configuration fragments. Mihomo is the syntax/runtime authority after project validation. Publication backends cannot alter generation or bypass validation.
