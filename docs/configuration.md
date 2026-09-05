# Configuration reference

Canonical runtime declarations are **Public Config V2**: `config.yaml`, `subscriptions.yaml`, and a Policy Model v2 `policies.yaml` manifest all declare `version: 2`. Unknown properties, legacy v1 declarations, and cross-file reference errors fail closed. There is no runtime compatibility path for v1 public config.

The policy manifest owns exactly four fragments: `routing`, `scheduling`, `classification`, and `topology`. A physical monolithic Policy Model v1 file is no longer a runtime input; the policy-only offline migration helper remains `scripts/migrate_policy_v2.py`.

## Canonical production profile

Production enables one generator module:

```yaml
modules:
  general: true
```

The canonical profile contains:

- four metadata-only subscription declarations in `subscriptions.yaml`;
- Policy Model v2 domain fragments under `policies/`;
- one `general` inventory for non-browsing application routing;
- one `browsing` inventory for generic web routing;
- regional AI candidate inventories;
- pinned ACL4SSR Full routing data in `rules/acl4ssr.yaml`;
- live OpenAI / Claude / Gemini qualification before publication;
- Cloudflare Workers KV as the credential-bearing publication backend;
- DNS-independent HTTP/TLS/QUIC traffic sniffing for mobile routing identity.

The source-policy boundary is intentional: `subscription_1` may enter only `browsing` and `ai`; it may never enter `general`.

## `config.yaml`

`config.yaml` begins with `version: 2`. The remaining fields describe the deliberately small runtime, generation, rule-source, and publication surface.

### `runtime`

Maps to the deliberately small Mihomo runtime surface: mixed port, LAN binding, rule mode, log level, IPv6, delay behavior, profile persistence, DNS ownership, and optional sniffing. Production does not emit a public controller, controller secret, listeners, or tunnels.

`runtime.dns.mode: client` omits generated DNS state and leaves DNS behavior to the client environment. `managed` emits the declared DNS settings. Canonical production uses `client` while enabling HTTP Host, TLS SNI, and QUIC sniffing.

### `generation`

| Field | Behavior |
|---|---|
| `minimum_successful_subscriptions` | global fetch/parse success gate |
| `minimum_usable_nodes` | post-deduplication node gate |
| `fetch_timeout_seconds` | per-source timeout |
| `max_subscription_bytes` | compressed and decompressed payload ceiling |
| `invalid_proxy_policy` | `error` or `skip` one malformed proxy |
| `duplicate_policy` | `keep_first` or `error` on identical proxy fingerprints |
| `allow_http_subscription_urls` | explicit HTTP opt-in; HTTPS is default |
| `allow_file_subscription_urls` | test/local opt-in; disabled in production |
| `reject_private_proxy_hosts` | reject literal private/special IP endpoints |
| `fail_on_required_subscription_error` | required-source failure gate |
| `generated_header` | stable generated-file attribution/header option |

### `rule_sources`

Production enables the pinned ACL4SSR manifest:

```yaml
rule_sources:
  acl4ssr:
    enabled: true
    manifest: rules/acl4ssr.yaml
```

Trusted generation fetches the pinned fragments and embeds them as inline classical Mihomo rule providers, so the final profile has no runtime rule-provider URL/path.

## `subscriptions.yaml`

`subscriptions.yaml` begins with `version: 2`. Tracked rows contain no URL. Secret names resolve from `CLASH_RELAY_SUBSCRIPTIONS`, same-named environment variables, or an ignored local secret file.

Canonical `subscription_1`:

```yaml
- id: subscription_1
  display_name: 订阅源 1
  enabled: true
  required: false
  secret_name: SUBSCRIPTION_1_URL
  ingest_order: 100
  on_error: skip
  allowed_uses: [browsing, ai]
  allowed_countries: ['*']
  default_capabilities: [general]
  default_cost_level: standard
  max_node_multiplier: 2.0
```

Subscriptions 2-4 allow `[general, browsing, ai]`.

### `ingest_order`

`ingest_order` is a deterministic source-ingestion and duplicate-resolution ordering key. Lower values are processed first. It **does not** rank node quality, override live qualification, influence scheduler latency scoring, or grant routing preference. The former public name `priority` is not accepted by Public Config V2 because it implied semantics that the field never had.

### `max_node_multiplier`

This source-admission filter is evaluated against the original node name before classification and deduplication. Common explicit markers such as `2x`, `x2.5`, `3倍`, and `倍率:4` are recognized. With `max_node_multiplier: 2.0`, exactly `2x` is retained and values strictly greater than `2x` are rejected. Unmarked nodes are retained rather than assigned a guessed multiplier.

### `allowed_uses`

`allowed_uses` is a hard inventory permission boundary. Every generated pool has a `source_use`; node selection requires the source to allow that use. A `subscription_1` node therefore cannot become available to a general provider through lower latency, manual selection, fallback, or deduplication.

## Policy Model v2

`policies.yaml` is only the manifest:

```yaml
version: 2
fragments:
  routing: policies/routing.yaml
  scheduling: policies/scheduling.yaml
  classification: policies/classification.yaml
  topology: policies/topology.yaml
```

The fragment set is fixed and domain ownership is enforced. A known section in the wrong fragment fails closed.

### `policies/routing.yaml`

Owns Routing V2 scenario bindings, browsing/AI region policy, the download mode, and `routing.contract`. Public FlClash decisions and ACL4SSR binding/priority fidelity are declaration data here rather than Python constants.

### `policies/scheduling.yaml`

Owns scheduler history thresholds and all probe definitions. A topology pool references a probe by name; there is no separate generic service declaration file.

### `policies/classification.yaml`

Owns capability definitions, cost levels, and country-name classification aliases. Restricted capabilities require explicit opt-in when inferred from node names.

### `policies/topology.yaml`

Owns all pools and chains. A regular pool declares `source_use`, eligible regions and fallback order, capability filters, allowed cost levels, `on_empty`, a scheduling probe, and optional routing rules.

Generic pool selection is applied in this order:

1. source must allow `source_use`;
2. source must allow the node country;
3. region must match unless `ANY`;
4. `capabilities_any` must intersect if nonempty;
5. every `capabilities_all` value must match;
6. no `excluded_capabilities` value may match;
7. cost must be allowed.

A new configuration-only routed service is represented as a topology pool plus a scheduling probe when needed; it does not require a parallel service schema.

## Rules

`rules/acl4ssr.yaml` pins the ACL4SSR reference used by canonical production. `rules/direct.yaml` contains the project-owned direct-rule prelude. Pool-specific rule files are referenced from topology.

The production browsing boundary routes generic foreign-web traffic to `网页浏览`, while application-specific general rules and `MATCH` remain outside that inventory so subscription 1 cannot leak into non-browsing application traffic.

## Secret injection

Preferred Actions Secret value:

```json
{
  "SUBSCRIPTION_1_URL": "<private URL>",
  "SUBSCRIPTION_2_URL": "<private URL>",
  "SUBSCRIPTION_3_URL": "<private URL>",
  "SUBSCRIPTION_4_URL": "<private URL>"
}
```

Store the mapping as `CLASH_RELAY_SUBSCRIPTIONS`.

## Output visibility

Inventory wrapper groups whose names begin with `__CR_` are hidden implementation details. Automatic helper groups and country `url-test` groups are hidden at the top level. Actionable select groups such as `网页浏览`, `人工智能`, media/application policies, and `漏网之鱼` remain operable.

## Validation

Canonical changes must pass:

- Public Config V2 schema validation for `config.yaml` and `subscriptions.yaml`;
- Policy Model v2 manifest/fragment schema and semantic validation;
- source-use and strict `>2x` admission tests;
- Routing V2 / ACL4SSR drift guards;
- Python 3.11 / 3.12 / 3.13 Ruff, formatter, static typing, and unit tests;
- deterministic generation;
- repository and supply-chain safety audits;
- pinned ACL4SSR fidelity validation;
- every pinned stable Mihomo core plus real startup/provider integration tests.
