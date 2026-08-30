# clash-relay

[简体中文](README.zh-CN.md)

`clash-relay` is a deterministic, fail-closed Mihomo configuration builder designed to run safely from a **public GitHub repository**. Public YAML contains policy and subscription metadata only. Real subscription URLs stay in GitHub Actions Secrets, generated node credentials exist only on an ephemeral GitHub-hosted runner, and the validated `config.yaml` is published only to private Cloudflare Workers KV.

The generated file is standard Mihomo YAML. FlClash is only a consumer; no Python process, database, ASN database, daemon, or project-specific runtime is required on the client device.

> **Credential warning**
>
> The standalone generated configuration contains inline proxy credentials and must be treated as highest-sensitivity data. The supported public production workflow does **not** upload it to Actions Artifacts, Releases, Gists, commits, or Pages. FlClash reads it through a token-protected Cloudflare Worker URL.

## Security architecture

```text
Public GitHub repository
  ├─ config.yaml / subscriptions.yaml       public metadata only
  ├─ CLASH_RELAY_SUBSCRIPTIONS              GitHub Secret
  └─ CLOUDFLARE_API_TOKEN                   GitHub Secret
            ↓
      trusted main-branch Actions run
            ↓
      per-subscription ::add-mask::
            ↓
      fetch + parse + classify
            ↓
      generate private config.yaml
            ↓
      Mihomo v1.19.30 validation
            ↓
      Mihomo v1.19.29 validation
            ↓
      Cloudflare Workers KV
            ↓
      token-protected Worker URL
            ↓
           FlClash
```

The subscription Secret is present only during masking and generation. The Cloudflare API token is present only during the final publish step. Mihomo validation receives neither secret. The Worker `PROFILE_TOKEN` never enters GitHub.

See [Security model](docs/security.md) and [Publishing](docs/publishing.md).

## Quick start

### 1. Prepare Cloudflare

Create:

- a Workers KV namespace, for example `clash-relay-config`;
- a Worker bound to that namespace as `CONFIG_KV`;
- Worker Secret `PROFILE_TOKEN`;
- a narrowly scoped Cloudflare API token with Workers KV edit/write permission.

The Worker should return the KV key `production-config` only when the request path contains the correct `PROFILE_TOKEN`, and should return a generic `404` otherwise.

A typical FlClash URL is:

```text
https://<worker>.<workers-subdomain>.workers.dev/profile/<PROFILE_TOKEN>
```

Treat the complete URL as a bearer credential.

### 2. Configure GitHub

Repository **Secrets**:

```text
CLASH_RELAY_SUBSCRIPTIONS
CLOUDFLARE_API_TOKEN
```

Repository **Variables**:

```text
CLOUDFLARE_ACCOUNT_ID
CLOUDFLARE_KV_NAMESPACE_TITLE
```

`PROFILE_TOKEN` must not be stored in GitHub.

`CLASH_RELAY_SUBSCRIPTIONS` is one JSON or YAML mapping, so any number of subscriptions can be used:

```json
{
  "SUBSCRIPTION_1_URL": "<private subscription URL>",
  "SUBSCRIPTION_2_URL": "<private subscription URL>",
  "SUBSCRIPTION_3_URL": "<private subscription URL>",
  "SUBSCRIPTION_4_URL": "<private subscription URL>"
}
```

Do not put the Cloudflare Worker URL in this Secret. These values are the original provider subscription URLs.

### 3. Create public declarations

```bash
cp config.example.yaml config.yaml
cp subscriptions.example.yaml subscriptions.yaml
```

`config.yaml` and `subscriptions.yaml` may be committed to the public repository only if they contain no URL, token, username, password, private endpoint, or generated node credential.

Every `secret_name` in `subscriptions.yaml` must exactly match one key in `CLASH_RELAY_SUBSCRIPTIONS`.

Example:

```yaml
version: 1
subscriptions:
  - id: subscription_1
    display_name: Subscription 1
    enabled: true
    required: true
    secret_name: SUBSCRIPTION_1_URL
    priority: 100
    on_error: fail
    allowed_uses: [general, ai, bulk]
    allowed_countries: [US, JP, SG, OTHER]
    default_capabilities: [general]
    default_cost_level: standard
    node_metadata: {}
    name_rules: []
```

Adding more subscriptions requires adding more rows, not changing Python or workflow code.

### 4. Keep GitHub publication disabled

The public-safe production profile is:

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

The Cloudflare publication gate refuses to run if Artifact, Release, or Gist is enabled at the same time.

### 5. Run production

Commit the canonical declarations to trusted `main`, or manually dispatch **Generate, validate, and publish** from `main`.

The production job:

1. validates the public declarations and Cloudflare-only publication policy;
2. registers every derived subscription URL with `::add-mask::`;
3. generates one private candidate on the runner;
4. validates the exact candidate with Mihomo v1.19.30;
5. validates the same candidate with Mihomo v1.19.29;
6. writes the exact validated bytes to the configured Cloudflare KV namespace/key;
7. removes the private candidate after successful publication.

If any earlier step fails, Cloudflare is not updated and the previous successful value remains available.

## Node capability model

Node source and node capability are independent. A source may permit `general`, `ai`, `bulk`, `residential`, `emby`, `high_multiplier`, or `chain`, while individual nodes can receive exact metadata overrides.

Built-in capabilities:

| Capability | Purpose | Restricted |
|---|---|---:|
| `general` | ordinary browsing | no |
| `ai` | explicitly approved AI egress | no |
| `bulk` | sustained video/download/CDN traffic | no |
| `residential` | residential/home IP | yes |
| `emby` | dedicated EMBY route | yes |
| `high_multiplier` | expensive/high-ratio route | yes |
| `chain` | explicit second-hop exit | yes |

Restricted capabilities are opt-in. Empty optional pools route to `REJECT`; required pools stop the build. Unrelated business pools never silently borrow each other's nodes.

See [Configuration reference](docs/configuration.md).

## Subscription formats

The parser accepts common Clash/Mihomo YAML, proxy lists, inline provider payloads, plain/base64 URI lists, and common SS/SSR/VMess/VLESS/Trojan/HTTP/SOCKS5/Hysteria/Hysteria2/TUIC/AnyTLS forms. Remote provider URLs embedded inside an input subscription are not followed.

Unsafe schemes, URL userinfo, private proxy IP literals, oversized payloads, aliases/anchors, invalid ports, unsupported proxy types, and subscription-supplied routing controls receive explicit validation or sanitization.

## Local development

Python 3.11 or 3.12:

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.lock -e .
ruff check .
ruff format --check .
pytest -m "not integration"
python scripts/repository_audit.py
```

Use the fictional fixtures for local generation:

```bash
python scripts/make_fixture_sources.py
clash-relay generate \
  --config tests/fixtures/project/config.yaml \
  --subscriptions tests/fixtures/project/subscriptions.yaml \
  --services services.yaml \
  --policies policies.yaml \
  --secret-file .work/fixture-secrets.yaml \
  --output .work/config.yaml
```

## CI/CD behavior

Pull requests run only fictional CI:

1. schema, lint, unit, and repository-safety checks on Python 3.11 and 3.12;
2. byte-for-byte deterministic fixture generation;
3. real startup/provider integration tests on Mihomo v1.19.30 and v1.19.29.

Production runs only from trusted `main`. Real Mihomo failure output is redirected to runner-local files and is intentionally not printed into public Actions logs. No credential-bearing Artifact is created.

## FlClash

FlClash should use the Worker URL directly. The Worker retrieves the latest `production-config` from KV after validating `PROFILE_TOKEN`. The Worker URL remains stable while GitHub Actions replaces the KV value after each successful build.

Because the complete Worker URL is itself a credential, keep it private and rotate `PROFILE_TOKEN` if it is exposed.

## Project status

This is an initial public architecture. Current limitations include no DNS-resolution pinning against hostname rebinding during subscription fetches, build-time dependence on availability of the pinned ACL4SSR source when refreshing rules, and best-effort support for uncommon protocol extensions. Real Mihomo validation remains the final authority for proxy fields not modeled by the parser.

## License

MIT
