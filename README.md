# clash-relay

[简体中文](README.zh-CN.md)

`clash-relay` is a deterministic, fail-closed Mihomo configuration builder designed to run safely from a **public GitHub repository**. Public YAML contains policy and subscription metadata only. Real subscription URLs stay in GitHub Actions Secrets, generated node credentials exist only on an ephemeral GitHub-hosted runner, and the qualified and validated standalone `config.yaml` is published only to private Cloudflare Workers KV.

FlClash consumes the token-protected Worker URL. It does not need runtime access to GitHub or ACL4SSR.

> **Credential warning**
>
> The generated configuration contains inline proxy credentials and must be treated as highest-sensitivity data. The supported public production workflow does **not** upload it to Actions Artifacts, Releases, Gists, commits, or Pages.

## Canonical production profile

```text
4 private subscription URLs
          ↓
subscription-scoped admission policies
          ↓
general inventory + deterministic country classification from node names
          ↓
AI candidate pools: SG / JP / US / HK / TW / KR / OTHER
          ↓
trusted runner performs real ChatGPT / Claude / Gemini HTTP(S) requests per node
          ↓
keep only nodes that pass all three AI probes
          ↓
pinned ACL4SSR Online Full rules
          ↓
Mihomo v1.19.30 + v1.19.29
          ↓
Cloudflare Workers KV
          ↓
FlClash
```

Production still enables only:

```yaml
modules:
  general: true
```

`services.yaml` remains empty. `policies.yaml` has one general pool plus seven country-scoped AI candidate pools under the same `general` module. This does not restore dedicated ChatGPT, Claude, or Gemini service modules. Legacy Google Play, bulk, residential, EMBY, high-multiplier, and chain production declarations remain removed. Generic engine capabilities continue to be exercised in `tests/fixtures/project/`.

## AI qualification and country groups

AI candidates are deterministically classified from **node names**, not from GeoIP. Production currently recognizes SG, JP, US, HK, TW, and KR from common Chinese/English location names, airport codes, flags, and unambiguous short-code boundaries. Unrecognized nodes go to `OTHER`; the project does not guess their location or claim that a label proves the actual egress IP location.

Every subscription may provide AI **candidates**, but declaration-time eligibility is not enough. On trusted `main`, the workflow starts short-lived Mihomo processes, pins a temporary selector to each candidate node through the Core API, and then makes actual HTTP(S) requests through that Mihomo mixed port to:

```text
https://chatgpt.com/
https://claude.ai/
https://gemini.google.com/
```

A node survives only if all configured probes return an accepted HTTP status range; production currently requires `200-399` for all three. Network errors, timeouts, or a non-accepted response fail that node. This qualification changes only the AI pools; the ordinary `节点选择` inventory is not pruned by AI results.

Candidates are sharded across a bounded number of isolated temporary Mihomo processes so hundreds of nodes do not have to be tested serially. Node names, servers, credentials, and per-node probe results are not printed to the public Actions log; only aggregate counts are emitted.

After qualification, an empty country group is removed from `人工智能`. If every country is empty, publication fails closed and the previous Cloudflare KV value is left untouched.

The core visible groups remain:

```text
节点选择
人工智能
流媒体
国内服务
广告拦截
```

`人工智能` may additionally expose any non-empty subset of:

```text
AI · 新加坡
AI · 日本
AI · 美国
AI · 香港
AI · 台湾
AI · 韩国
AI · 其他地区
DIRECT
```

The general inventory and AI country inventories use separate private inline providers so the trusted build can physically remove non-qualified AI nodes before final validation. The final credential-bearing YAML still exists only in the private publication path.

## Subscription-scoped policies

A subscription may declare an optional `max_node_multiplier`. The filter evaluates only explicit multiplier markers in node names, such as `2x`, `x2.5`, `3倍`, or `倍率:4`. A ceiling of `2.0` removes nodes explicitly marked above 2x before classification and provider generation; nodes without an explicit multiplier marker are retained rather than guessed.

Canonical production still restricts `subscription_1` to explicit generic-web and AI routes:

- ACL4SSR `ProxyGFWlist` may use `subscription_1`;
- ACL4SSR `AI` and `OpenAi` may use it only through live-qualified AI country pools;
- `流媒体`, `国内服务`, Telegram, and final unmatched ordinary proxy traffic exclude `subscription_1`.

Ordinary source exclusions reuse the general inline provider through hidden Mihomo `exclude-filter` routing anchors. If a restricted route has no permitted proxy left, it fails closed to `REJECT`.

This is rule-routing policy, not process identification: it does not attempt to prove that the originating executable is a web browser.

## ACL4SSR rule model

ACL4SSR is pinned to an immutable commit rather than the moving `master` branch. Trusted generation fetches the configured Full fragments and embeds each one as a Mihomo inline classical rule provider:

```yaml
rule-providers:
  acl4ssr_ai:
    type: inline
    behavior: classical
    payload: [...]

rules:
  - RULE-SET,acl4ssr_ai,人工智能
  - GEOIP,CN,DIRECT,no-resolve
  - MATCH,<source-filtered-final-anchor>
```

The final profile remains standalone: generated rule providers contain no `url` or `path`, and FlClash/Mihomo does not fetch ACL4SSR at runtime. See [Routing rules and ACL4SSR](docs/rules.md).

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
      fetch + parse + admission filter + deduplicate
            ↓
      fetch pinned ACL4SSR fragments
            ↓
      generate private standalone YAML
            ↓
      per-node AI qualification through temporary Mihomo mixed ports
            ↓
      prune failed nodes and empty AI country groups
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

The subscription Secret is present only during masking and generation. AI qualification reads the already-generated private candidate and does not receive the original subscription Secret. The Cloudflare API token is present only during the final publish step. The Worker `PROFILE_TOKEN` never enters GitHub.

See [Security model](docs/security.md) and [Publishing](docs/publishing.md).

## GitHub configuration

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

`CLASH_RELAY_SUBSCRIPTIONS` is a JSON or YAML mapping:

```json
{
  "SUBSCRIPTION_1_URL": "<private subscription URL>",
  "SUBSCRIPTION_2_URL": "<private subscription URL>",
  "SUBSCRIPTION_3_URL": "<private subscription URL>",
  "SUBSCRIPTION_4_URL": "<private subscription URL>"
}
```

These are original provider URLs, not the Cloudflare Worker URL. Every `secret_name` in `subscriptions.yaml` must exactly match one mapping key. `PROFILE_TOKEN` must not be stored in GitHub.

## Cloudflare-only publication

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

The Cloudflare publication gate refuses to run if Artifact, Release, or Gist is enabled. If generation, AI qualification, or either Mihomo validation fails, KV is not updated and the previous successful value remains available.

## Generic engine vs. production declarations

The schemas and generator remain data-driven. Generic fixture coverage still includes capabilities such as `bulk`, `residential`, `emby`, `high_multiplier`, and `chain`. Production uses only the `general` module plus country-scoped AI candidate pools within that module, keeping the production routing surface focused while preserving reusable-engine regression coverage.

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

Generate the isolated fictional fixture with:

```bash
python scripts/make_fixture_sources.py
clash-relay generate \
  --config tests/fixtures/project/config.yaml \
  --subscriptions tests/fixtures/project/subscriptions.yaml \
  --services tests/fixtures/project/services.yaml \
  --policies tests/fixtures/project/policies.yaml \
  --secret-file .work/fixture-secrets.yaml \
  --output .work/config.yaml
```

CI runs schema/lint/unit/repository-safety checks on Python 3.11 and 3.12, byte-for-byte deterministic generation, and real Mihomo v1.19.30/v1.19.29 configuration/startup integration tests, including selector-to-mixed-port AI status validation. Production runs only from trusted `main` and creates no credential-bearing GitHub Artifact.

## License

MIT
