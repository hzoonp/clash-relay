# Configuration reference

All declarations use `version: 1` and reject unknown properties through JSON Schema. Cross-file references receive additional semantic validation.

## Canonical production profile

Production enables one module:

```yaml
modules:
  general: true
```

The canonical profile contains:

- four metadata-only subscriptions in `subscriptions.yaml`;
- an empty `services.yaml`;
- one `general` inventory for non-browsing application routing;
- one `browsing` inventory for generic web routing;
- seven AI country/region candidate inventories;
- pinned ACL4SSR Full routing data in `rules/acl4ssr.yaml`;
- live OpenAI / Claude / Gemini qualification before publication;
- Cloudflare Workers KV as the credential-bearing publication backend.
- DNS-independent HTTP/TLS/QUIC traffic sniffing for more complete mobile routing identity.

The production source-policy boundary is intentional: `subscription_1` may enter only `browsing` and `ai`; it may never enter `general`.

## `config.yaml`

### `runtime`

Maps to the deliberately small Mihomo runtime surface: mixed port, LAN binding, rule mode, log level, IPv6, delay behavior, profile persistence, and DNS. Production does not emit a public controller, controller secret, listeners, or tunnels.

#### DNS ownership

`runtime.dns.mode` controls whether clash-relay owns DNS configuration:

- `client` omits the generated `dns` block and `store-fake-ip`, leaving DNS behavior to FlClash/Mihomo and the client environment. Canonical production uses this mode for mobile CDN compatibility.
- `managed` emits the declared DNS settings exactly as before. Configurations that omit `mode` retain the legacy managed behavior.

P11 changes DNS ownership only; ACL4SSR routing, source isolation, qualification, and scenario schedulers are unchanged.

#### Traffic sniffing

`runtime.sniffer` is independent from DNS ownership. Canonical production keeps `runtime.dns.mode: client` while enabling HTTP Host, TLS SNI, and QUIC sniffing. This lets Mihomo recover domain identity for pure-IP or otherwise opaque application connections so the existing ACL4SSR/scenario graph can classify them more accurately.

Canonical production uses:

```yaml
runtime:
  dns:
    mode: client
  sniffer:
    enabled: true
    force_dns_mapping: false
    parse_pure_ip: true
    sniff:
      http:
        ports: [80, '8080-8880']
        override_destination: true
      tls:
        ports: [443, 8443]
      quic:
        ports: [443, 8443]
```

`force_dns_mapping` remains disabled because P12 does not reintroduce Fake-IP or managed DNS. Configurations that omit `runtime.sniffer` keep the legacy no-sniffer output. Port ranges are fail-closed and must be ordered within `1..65535`.

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

The manifest pins an immutable ACL4SSR commit. Trusted generation fetches configured fragments and embeds them as inline classical Mihomo rule providers, so the final profile has no runtime rule-provider URL/path.

Canonical production does not need `excluded_sources` to protect `subscription_1`. The stronger boundary is applied before provider generation: `allowed_uses` prevents the source from entering the `general` inventory at all.

## `subscriptions.yaml`

Tracked rows contain no URL. Secret names resolve from `CLASH_RELAY_SUBSCRIPTIONS`, same-named environment variables, or an ignored local secret file.

Canonical `subscription_1`:

```yaml
- id: subscription_1
  display_name: 订阅源 1
  enabled: true
  required: false
  secret_name: SUBSCRIPTION_1_URL
  priority: 100
  on_error: skip
  allowed_uses: [browsing, ai]
  allowed_countries: ['*']
  default_capabilities: [general]
  default_cost_level: standard
  max_node_multiplier: 2.0
```

Canonical subscriptions 2-4 use:

```yaml
allowed_uses: [general, browsing, ai]
```

### `max_node_multiplier`

This is a source admission filter evaluated against the original node name before classification and deduplication. Common explicit markers such as `2x`, `x2.5`, `3倍`, and `倍率:4` are recognized.

With `max_node_multiplier: 2.0`:

```text
1x       keep
2x       keep
2.01x    reject
3倍      reject
unmarked keep
```

An unmarked node is retained rather than assigned a guessed multiplier. When multiple explicit markers are present, the highest parsed value is used.

### `allowed_uses`

`allowed_uses` is a hard inventory permission boundary. Every generated pool has a `source_use`; node selection requires the source to allow that use.

For production:

```text
subscription_1: browsing, ai
subscription_2: general, browsing, ai
subscription_3: general, browsing, ai
subscription_4: general, browsing, ai
```

This means a `subscription_1` node cannot become available to a general provider through lower latency, manual selection, fallback, or deduplication.

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

## `services.yaml`

Canonical production contains:

```yaml
version: 1
services: []
```

Production AI scheduling is implemented through AI candidate pools plus trusted post-generation service qualification rather than declaration-time service modules.

## `policies.yaml`

Canonical production defines three logical use classes.

### General inventory

```yaml
- id: general
  display_name: __CR_GENERAL_INVENTORY
  source_use: general
```

Only sources that allow `general` may enter it. `subscription_1` therefore cannot appear in general application routing.

### Browsing inventory

```yaml
- id: browsing
  display_name: __CR_BROWSING_INVENTORY
  source_use: browsing
```

This is the only non-AI inventory that admits `subscription_1`.

### AI inventories

The seven AI pools use `source_use: ai` and cover SG / JP / US / HK / TW / KR / OTHER. They admit `subscription_1` and other sources that explicitly allow AI use.

Generic pool selection is applied in this order:

1. source must allow `source_use`;
2. source must allow the node country;
3. region must match unless `ANY`;
4. `capabilities_any` must intersect if nonempty;
5. every `capabilities_all` value must match;
6. no `excluded_capabilities` value may match;
7. cost must be allowed.

## `rules/acl4ssr.yaml`

The manifest pins:

```text
ACL4SSR/ACL4SSR@c498ae4911f15b19c5ceaef6f8737ca8705b4430
```

Most ACL4SSR Full application targets remain unchanged. Production intentionally adds a browsing scheduling boundary:

```text
ProxyGFWlist -> 网页浏览 -> browsing inventory
```

AI remains the other explicit scheduling extension:

```text
AI / OpenAI -> 人工智能 -> AI inventories
```

Representative application-specific routes remain on the general inventory:

```text
Telegram                   -> 电报消息
YouTube                    -> 油管视频
Netflix                    -> 奈飞视频
Epic/Origin/Sony/Steam/... -> 游戏平台
ChinaMedia                 -> 国内媒体
ProxyMedia                 -> 国外媒体
Download                   -> 全球直连
MATCH                      -> 漏网之鱼
```

`MATCH` deliberately stays on the general path. An unmatched connection cannot safely be classified as “web browsing” across all supported clients, so fail-open routing to the browsing inventory would violate the subscription-1 permission boundary.

The project does not use process-name matching as the canonical browsing boundary because process metadata is not portable across Android, iOS, Windows, macOS, and different Mihomo deployment modes.

## `rules/direct.yaml`

Canonical production intentionally contains:

```yaml
version: 1
rules: []
```

There is no project-defined local/private-network rule prelude ahead of the pinned routing model.

## Output visibility

Inventory wrapper groups whose names begin with `__CR_` are hidden implementation details. Automatic helper groups such as `自动选择`, `网页自动`, and country `url-test` groups are also hidden at the top level. Actionable select groups such as `网页浏览`, `人工智能`, `手动切换`, media/application policies, and `漏网之鱼` remain operable.

## Validation

Canonical changes must pass:

- JSON Schema and cross-file semantic validation;
- multiplier parsing and strict `> 2x` admission tests;
- source-use tests proving subscription 1 is excluded from `general` and admitted to `browsing`/`ai`;
- routing contract tests proving `ProxyGFWlist -> 网页浏览` while application-specific rules and `MATCH` remain outside the browsing inventory;
- Python 3.11/3.12 Ruff and unit tests;
- deterministic generation;
- repository safety audit;
- pinned ACL4SSR compatibility validation;
- Mihomo v1.19.30 and v1.19.29 integration tests before publication.
