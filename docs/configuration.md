# Configuration reference

All declarations use `version: 1` and reject unknown properties through JSON Schema. Cross-file references receive additional semantic validation.

## Canonical production profile

The repository-root production declarations are intentionally narrow:

```yaml
modules:
  general: true
```

Production uses:

- four metadata-only rows in `subscriptions.yaml`;
- an empty `services.yaml`;
- one internal `general` node inventory in `policies.yaml`;
- seven AI country candidate inventories;
- pinned ACL4SSR Online Full routing in `rules/acl4ssr.yaml`;
- FlClash presentation-only containers that are never rule targets;
- Cloudflare Workers KV as the only credential-bearing publication backend.

The production contract is: **ACL4SSR owns every non-AI rule target and semantic policy group. FlClash presentation and live AI qualification are the only project-specific layers.**

## `config.yaml`

### `runtime`

Maps to the deliberately small Mihomo runtime surface: mixed port, LAN binding, rule mode, log level, IPv6, delay behavior, profile persistence, and DNS. Production does not emit a public controller, controller secret, listeners, or tunnels.

### `modules`

Canonical production enables only:

```yaml
modules:
  general: true
```

The schema remains reusable and permits other Boolean module IDs for isolated fixture projects.

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

### `rule_sources`

Production enables:

```yaml
rule_sources:
  acl4ssr:
    enabled: true
    manifest: rules/acl4ssr.yaml
```

The manifest pins an immutable ACL4SSR commit. Trusted generation fetches each configured fragment and embeds it as an inline classical Mihomo `rule-provider`; the final client profile contains no remote rule-provider URL/path.

The reusable schema still supports `excluded_sources` and `final_excluded_sources` for isolated/custom projects, but **canonical production declares neither**. Subscription-source policy therefore does not rewrite non-AI ACL4SSR routes.

See [Routing rules and ACL4SSR](rules.md).

### `publishing`

Production publishes credential-bearing output only to Cloudflare Workers KV:

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

`publication-gate --mode cloudflare_kv` fails closed unless the public GitHub publishing paths remain disabled.

## `subscriptions.yaml`

Tracked rows contain no URL. Their `secret_name` values resolve from `CLASH_RELAY_SUBSCRIPTIONS` or a local ignored secret mapping.

Canonical `subscription_1` is representative:

```yaml
- id: subscription_1
  display_name: 订阅源 1
  enabled: true
  required: false
  secret_name: SUBSCRIPTION_1_URL
  priority: 100
  on_error: skip
  allowed_uses: [general, ai]
  allowed_countries: ['*']
  default_capabilities: [general]
  default_cost_level: standard
  max_node_multiplier: 2.0
```

`max_node_multiplier` is an **admission** filter. It evaluates only explicit multiplier markers in the original node name. With a `2.0` ceiling, `2x` is retained, `2.01x` is rejected, and an unmarked node is retained rather than guessed.

`allowed_uses` is also an inventory-admission boundary. Canonical production does not convert it into application-specific ACL4SSR source exclusions after the node enters the general inventory. AI is the explicit exception because AI candidate admission and live service qualification are part of the scheduler itself.

Generic subscription fields:

| Field | Meaning |
|---|---|
| `id` | stable unique machine ID |
| `display_name` | human-readable source name |
| `enabled` | whether the source is fetched |
| `required` | source-level criticality |
| `secret_name` | key in secret bundle or environment |
| `priority` | deterministic processing and duplicate ownership |
| `on_error` | `fail` or `skip` |
| `allowed_uses` | inventories/purposes the source may enter |
| `allowed_countries` | source-level country boundary |
| `default_capabilities` | capabilities assigned to every node from the source |
| `default_cost_level` | default cost bucket |
| `max_node_multiplier` | optional maximum explicit node-name multiplier |
| `node_metadata` | exact original-name override map |
| `name_rules` | optional auxiliary regex classifiers |

## Secret injection

Preferred Actions Secret format:

```json
{
  "SUBSCRIPTION_1_URL": "<private URL>",
  "SUBSCRIPTION_2_URL": "<private URL>",
  "SUBSCRIPTION_3_URL": "<private URL>",
  "SUBSCRIPTION_4_URL": "<private URL>"
}
```

Store the mapping as `CLASH_RELAY_SUBSCRIPTIONS`. These are original provider URLs, not the Cloudflare Worker URL.

## `services.yaml`

Canonical production contains:

```yaml
version: 1
services: []
```

The generic schema can still define data-driven services for fixture/custom projects. Production AI scheduling is implemented through country candidate pools plus private post-generation qualification rather than declaration-time ChatGPT/Claude/Gemini service modules.

## `policies.yaml`

Canonical production defines:

- capability `general`;
- cost level `standard`;
- deterministic country aliases used for AI candidate classification;
- connectivity plus OpenAI/Claude/Gemini qualification probes;
- one internal general inventory named `__CR_GENERAL_INVENTORY`;
- seven AI candidate inventories: SG / JP / US / HK / TW / KR / OTHER;
- no production chains.

The internal general inventory is deliberately **not** the ACL4SSR `节点选择` policy. `rules/acl4ssr.yaml` materializes the actual ACL4SSR `节点选择`, `自动选择`, `手动切换`, country selectors, and other semantic policy groups over that shared provider-backed inventory.

Generic pool selection remains data-driven:

1. source must allow `source_use`;
2. source must allow node country;
3. region must match unless `ANY`;
4. if `capabilities_any` is nonempty, at least one must match;
5. every `capabilities_all` must match;
6. no `excluded_capabilities` may match;
7. cost must be allowed.

## `rules/direct.yaml`

Canonical production intentionally contains:

```yaml
version: 1
rules: []
```

There is no project-defined local/private-network rule prelude ahead of ACL4SSR. If such a rule is needed in the future, it must come from the pinned ACL4SSR model or be explicitly documented as a deliberate semantic exception.

## `rules/acl4ssr.yaml`

This file owns canonical non-AI routing behavior. It declares:

- exact pinned ACL4SSR repository/ref/license;
- Full rule-fragment order and targets;
- ACL4SSR semantic policy groups and member order;
- provider-backed manual/automatic/country selectors using the upstream regexes;
- `GEOIP,CN -> 全球直连`;
- `MATCH -> 漏网之鱼`;
- presentation-only `流媒体`, `国内服务`, and `更多策略` containers that are not rule targets.

The only intentional semantic extension is the `人工智能` scheduling layer: candidate country groups are live-qualified per service, and private post-processing inserts service-specific OpenAI/Claude/Gemini routes before the generic ACL4SSR AI rule.

The pinned Full lists also contain nine legacy `URL-REGEX` entries that Mihomo 1.19.x cannot express as classical rules. Canonical generation permits those omissions only when the exact same lines are explicitly commented out by ACL4SSR's own `Clash/Providers/*.yaml` files at the same pinned commit. The expected compatibility boundary is seven verified omissions from `Download`, one from `ChinaMedia`, and one from `ProxyMedia`; any unverified omission fails closed.

## Output visibility

After successful AI qualification, the intended FlClash top level is:

```text
节点选择
人工智能
流媒体
国内服务
更多策略
```

ACL4SSR semantic groups hidden under the presentation containers still receive their original rule traffic. AI country groups are hidden at the top level and appear only under `人工智能`.

## Validation

Canonical changes must pass schema validation, Python 3.11/3.12 quality checks, deterministic generation, repository audit, real fetch of the pinned ACL4SSR sources with exactly nine same-pin upstream-verified compatibility omissions and `unverified_legacy_rules == 0`, and Mihomo v1.19.30/v1.19.29 integration before the exact private candidate may be published.
