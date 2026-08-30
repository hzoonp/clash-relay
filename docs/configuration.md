# Configuration reference

All declarations use `version: 1` and reject unknown properties through JSON Schema. Cross-file references receive additional semantic validation.

## Canonical production profile

The real repository-root production configuration is intentionally minimal:

```yaml
modules:
  general: true
```

Production uses:

- four metadata-only rows in `subscriptions.yaml`, displayed as `订阅源 1` through `订阅源 4`;
- an empty `services.yaml`;
- one `general` pool in `policies.yaml`, displayed as `节点选择`;
- pinned ACL4SSR Full routing in `rules/acl4ssr.yaml`;
- Cloudflare Workers KV as the only credential-bearing publication backend.

Legacy dedicated production declarations for ChatGPT, Claude, Gemini, Google Play, bulk, residential, EMBY, high-multiplier, and chain routing have been removed. The generic engine still supports richer data-driven configurations; regression coverage for those capabilities lives under `tests/fixtures/project/` and is deliberately isolated from root production YAML.

## `config.yaml`

### `runtime`

Maps directly to a deliberately small Mihomo runtime surface: mixed port, LAN binding, rule mode, log level, IPv6, delay behavior, profile persistence, and DNS. The generator never emits a private controller, controller secret, listeners, or tunnels.

### `modules`

The schema permits arbitrary Boolean module IDs because the generator is reusable. Canonical production currently defines only:

```yaml
modules:
  general: true
```

A service, pool, chain, or ACL4SSR source with a module is active only when that module is enabled.

### `generation`

| Field | Behavior |
|---|---|
| `minimum_successful_subscriptions` | global fetch/parse success gate |
| `minimum_usable_nodes` | post-deduplication node gate |
| `fetch_timeout_seconds` | per-source timeout |
| `max_subscription_bytes` | compressed and decompressed payload ceiling |
| `invalid_proxy_policy` | `error` or `skip` one malformed proxy |
| `duplicate_policy` | `keep_first` or `error` on identical proxy fingerprints |
| `allow_http_subscription_urls` | local/legacy opt-in; HTTPS is default |
| `allow_file_subscription_urls` | test/local opt-in; disabled in production |
| `reject_private_proxy_hosts` | reject literal private/special IP endpoints |
| `fail_on_required_subscription_error` | required-source failure gate |
| `node_name_prefix` | stable generated node-name namespace option |
| `generated_header` | stable generated-file attribution/header option |

Canonical production requires at least one successful subscription and one usable node overall. Individual source failures are skipped because no one provider is treated as mandatory.

### `rule_sources`

Production enables:

```yaml
rule_sources:
  acl4ssr:
    enabled: true
    manifest: rules/acl4ssr.yaml
```

The manifest pins an immutable ACL4SSR commit. Its fetched fragments are converted at build time to inline classical Mihomo `rule-providers`; no remote rule-provider URL/path is emitted. See [Routing rules and ACL4SSR](rules.md).

### `publishing`

The public-repository production profile uses Cloudflare Workers KV as the only credential-bearing publisher:

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

`publication-gate --mode cloudflare_kv` fails closed unless Artifact, GitHub Release, and Gist publication remain disabled. GitHub Actions reads Cloudflare credentials only during the final publication step.

## `subscriptions.yaml`

A subscription row contains no URL. Canonical production keeps four rows whose visible names are `订阅源 1` through `订阅源 4` while their secret keys remain `SUBSCRIPTION_1_URL` through `SUBSCRIPTION_4_URL`.

Production rows use only the general contract:

```yaml
- id: subscription_1
  display_name: 订阅源 1
  enabled: true
  required: false
  secret_name: SUBSCRIPTION_1_URL
  priority: 100
  on_error: skip
  allowed_uses: [general]
  allowed_countries: [OTHER]
  default_capabilities: [general]
  default_cost_level: standard
```

The generic schema also supports:

| Field | Meaning |
|---|---|
| `id` | stable unique machine ID |
| `display_name` | human-readable source name |
| `enabled` | whether the source is fetched |
| `required` | source-level criticality |
| `secret_name` | key in secret bundle or environment |
| `priority` | deterministic processing and duplicate ownership |
| `on_error` | `fail` or `skip` |
| `allowed_uses` | purposes the source contract allows |
| `allowed_countries` | source-level country boundary |
| `default_capabilities` | capabilities assigned to every node from the source |
| `default_cost_level` | default cost bucket |
| `node_metadata` | exact original-name override map |
| `name_rules` | optional auxiliary regex classifiers |

## Secret injection

Preferred GitHub Actions format:

```json
{
  "SUBSCRIPTION_1_URL": "<private URL>",
  "SUBSCRIPTION_2_URL": "<private URL>",
  "SUBSCRIPTION_3_URL": "<private URL>",
  "SUBSCRIPTION_4_URL": "<private URL>"
}
```

Store the mapping as `CLASH_RELAY_SUBSCRIPTIONS`. These values are original provider URLs, not the protected Cloudflare Worker URL.

For local development, an ignored YAML secret file can be passed with `--secret-file`.

## `services.yaml`

Canonical production contains:

```yaml
version: 1
services: []
```

The generic schema remains capable of defining data-driven services with selectors, rule files, fallback order, and probe settings. Those generic examples are exercised under `tests/fixtures/project/` rather than root production.

## `policies.yaml`

Canonical production defines only:

- capability `general`;
- cost level `standard`;
- one connectivity probe;
- one `general` pool displayed as `节点选择`;
- no chains.

The pool uses `regions: [ANY]`, so one hidden `__CR_AUTO_GENERAL_ANY` url-test group is sufficient. No redundant public `Auto` selector or one-target fallback wrapper is generated.

The generic engine can still model additional capabilities, country aliases, probes, special pools, and chains. Selector semantics remain:

1. source must allow `source_use`;
2. source must allow node country;
3. region must match unless `ANY`;
4. if `capabilities_any` is nonempty, at least one must match;
5. every `capabilities_all` must match;
6. no `excluded_capabilities` may match;
7. cost must be allowed.

## `rules/`

Production root keeps:

- `rules/direct.yaml` for the small always-first local direct rules;
- `rules/acl4ssr.yaml` for the pinned ACL4SSR Full manifest and Chinese policy topology.

Old root business rule files for ChatGPT, Claude, Gemini, Google Play, bulk traffic, and EMBY have been removed. Equivalent generic regression fixtures exist only under `tests/fixtures/project/rules/`.

ACL4SSR rule data itself is not vendored into the repository. Trusted generation fetches the pinned upstream fragments, normalizes them, embeds them as inline `rule-providers`, and then validates the complete standalone YAML with both supported Mihomo versions.
