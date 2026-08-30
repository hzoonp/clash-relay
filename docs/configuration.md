# Configuration reference

All declarations use `version: 1` and reject unknown properties through JSON Schema. Cross-file references receive additional semantic validation.

## `config.yaml`

### `runtime`

Maps directly to a deliberately small Mihomo runtime surface: mixed port, LAN binding, rule mode, log level, IPv6, delay behavior, profile persistence, and DNS. The generator never emits a private controller, controller secret, listeners, or tunnels.

### `modules`

A mapping of arbitrary module IDs to Booleans. Every service, pool, and chain references one module. Adding a data-driven service therefore requires one new Boolean but no Python change.

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
| `allow_file_subscription_urls` | test/local opt-in; disabled in production example |
| `reject_private_proxy_hosts` | reject literal private/special IP endpoints |
| `fail_on_required_subscription_error` | required-source failure gate |
| `node_name_prefix` | reserved output naming option |
| `generated_header` | stable generated-file comment |

### `publishing`

The public-repository production profile uses Cloudflare Workers KV as the only credential-bearing publisher. The example configuration therefore sets:

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

`publication-gate --mode cloudflare_kv` fails closed unless Artifact, GitHub Release, and Gist publication all remain disabled. Cloudflare account credentials and namespace identity are runtime deployment settings, not tracked YAML. GitHub Actions reads:

- Secret `CLOUDFLARE_API_TOKEN`;
- Variable `CLOUDFLARE_ACCOUNT_ID`;
- Variable `CLOUDFLARE_KV_NAMESPACE_TITLE`.

The API token is never passed to generation or Mihomo validation steps. The namespace is resolved by exact title at publication time and the validated bytes are written to the configured KV key.

## `subscriptions.yaml`

A subscription row contains no URL:

| Field | Meaning |
|---|---|
| `id` | stable unique machine ID |
| `display_name` | report-only human name |
| `enabled` | whether the source is resolved/fetched |
| `required` | source-level criticality |
| `secret_name` | key in secret bundle or environment |
| `priority` | stable processing and duplicate ownership; not quality |
| `on_error` | `fail` or `skip` |
| `allowed_uses` | business purposes the source contract allows |
| `allowed_countries` | explicit source-level country boundary |
| `default_capabilities` | explicit capabilities assigned to every node from this source |
| `default_cost_level` | default cost bucket |
| `node_metadata` | authoritative original-name override map |
| `name_rules` | optional auxiliary regex classifiers |

`node_metadata` may set country, add/remove capabilities, and change cost. Multiple capabilities are allowed.

Restricted capabilities are defined by `policies.yaml`. A name rule that adds one must set `allow_restricted_capabilities: true`; exact metadata and source defaults are already explicit declarations.

## Secret injection

Preferred GitHub Actions format:

```json
{
  "SUB_ONE": "<private URL>",
  "SUB_TWO": "<private URL>"
}
```

Store it as `CLASH_RELAY_SUBSCRIPTIONS`. Values may alternatively be YAML or objects with a `url` field. For local development:

```yaml
SUB_ONE: <private URL>
SUB_TWO:
  url: <private URL>
```

Save as an ignored file such as `.secrets/subscriptions.yaml` and pass `--secret-file`.

## `services.yaml`

Each AI service declares:

- `id`, `display_name`, and `module`;
- `source_use`;
- required and excluded capabilities;
- allowed cost levels;
- countries and fallback order;
- empty behavior;
- rule priority/file;
- probe URL, method, accepted status codes, interval, timeout, laziness, and tolerance.

The method is fixed to `HEAD` because the provider integration test verifies actual Mihomo behavior. `expected_status` accepts slash/comma/space-separated codes and inclusive ranges. Keep the expression within statuses supported by the pinned Mihomo matrix.

## `policies.yaml`

This file defines:

- capability vocabulary and restricted status;
- cost vocabulary;
- auxiliary country aliases;
- reusable probes;
- ordinary/special business pools;
- controlled chain entry and exit selectors.

Selector semantics:

1. source must allow `source_use`;
2. source must allow node country;
3. region must match unless `ANY`;
4. if `capabilities_any` is nonempty, at least one must match;
5. every `capabilities_all` must match;
6. no `excluded_capabilities` may match;
7. cost must be allowed.

## `rules/`

Rule files use structured rows rather than raw comma strings:

```yaml
version: 1
rules:
  - type: DOMAIN-SUFFIX
    value: example.invalid
  - type: IP-CIDR
    value: 192.0.2.0/24
    options: [no-resolve]
```

The generator attaches the correct public business group. `rules/direct.yaml` is always placed first, business rules are sorted by priority and ID, and one final `MATCH` is appended.
