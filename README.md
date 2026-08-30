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
trusted runner probes ChatGPT / Claude / Gemini independently per node
          ↓
keep the union of service-qualified nodes in country inventories
          ↓
hidden OpenAI / Claude / Gemini routes use only nodes qualified for that service
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

`services.yaml` remains empty. `policies.yaml` has one general pool plus seven country-scoped AI candidate pools under the same `general` module. This does not restore dedicated ChatGPT, Claude, or Gemini declaration-time service modules. Legacy Google Play, bulk, residential, EMBY, high-multiplier, and chain production declarations remain removed. Generic engine capabilities continue to be exercised in `tests/fixtures/project/`.

## AI qualification and country groups

AI candidates are deterministically classified from **node names**, not from GeoIP. Production currently recognizes SG, JP, US, HK, TW, and KR from common Chinese/English location names, airport codes, flags, and unambiguous short-code boundaries. Unrecognized nodes go to `OTHER`; the project does not guess their location or claim that a label proves the actual egress IP location.

Every subscription may provide AI **candidates**, but declaration-time eligibility is not enough. On trusted `main`, the workflow starts short-lived Mihomo processes, pins a temporary selector to each candidate node through the Core API, and then makes actual `HEAD` requests through that Mihomo mixed port to:

```text
https://chatgpt.com/
https://claude.ai/
https://gemini.google.com/
```

The three services are qualified **independently**. For each service, a node qualifies only when that service probe returns the configured accepted HTTP status range; production currently requires `200-399`. Network errors, timeouts, TLS failures, or a non-accepted response fail that node for that service without falsely declaring the whole node unusable for every AI service.

The final country AI inventories retain the **union** of nodes that qualify for at least one of OpenAI, Claude, or Gemini. Hidden service-specific routing anchors then filter those shared country providers so OpenAI traffic can use only OpenAI-qualified nodes, Claude traffic only Claude-qualified nodes, and Gemini traffic only Gemini-qualified nodes. The ordinary `节点选择` inventory is never pruned by AI results.

Candidates are sharded across a bounded number of isolated temporary Mihomo processes so hundreds of nodes do not have to be tested serially. Node names, servers, credentials, and per-node probe results are not printed to the public Actions log. Public diagnostics contain only aggregate counts such as accepted/rejected status totals, timeout/TLS/network error counts, selector failures, and service-qualified node counts.

After qualification, an empty country group is removed from `人工智能`. A service with zero qualified nodes fails closed through a hidden `REJECT` route while other successfully qualified services may still publish. Publication aborts only when **no node qualifies for any protected AI service** or when the qualification infrastructure itself cannot complete safely; in either case the previous Cloudflare KV value remains untouched.

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

The service-specific OpenAI / Claude / Gemini routes are hidden implementation groups, so service-aware qualification does not add extra user-facing selectors. The general inventory and AI country inventories use separate private inline providers so the trusted build can physically remove nodes that qualify for no protected AI service before final validation. The final credential-bearing YAML still exists only in the private publication path.

## Subscription-scoped policies

A subscription may declare an optional `max_node_multiplier`. The filter evaluates only explicit multiplier markers in node names, such as `2x`, `x2.5`, `3倍`, or `倍率:4`. A ceiling of `2.0` removes nodes explicitly marked above 2x before classification and provider generation; nodes without an explicit multiplier marker are retained rather than guessed.

Canonical production still restricts `subscription_1` to explicit generic-web and AI routes:

- ACL4SSR `ProxyGFWlist` may use `subscription_1`;
- protected OpenAI / Claude / Gemini traffic may use it only through the corresponding live-qualified hidden service route, while remaining ACL4SSR AI traffic continues through `人工智能`;
- `流媒体`, `国内服务`, Telegram, and final unmatched ordinary proxy traffic exclude `subscription_1`.

Ordinary source exclusions reuse the general inline provider through hidden Mihomo `exclude-filter` routing anchors. If a restricted route has no permitted proxy left, it fails closed to `REJECT`.

This is rule-routing policy, not process identification: it does not attempt to prove that the originating executable is a web browser.

## ACL4SSR rule model

ACL4SSR is pinned to an immutable commit rather than the moving `master` branch. Trusted generation fetches the configured Full fragments and embeds each one as a Mihomo inline classical rule provider. During private AI qualification, exact Claude and Gemini subsets are derived from that pinned AI payload and checked for upstream drift; the dedicated pinned OpenAI provider plus those derived subsets are placed before the generic AI rule so service-qualified routes take precedence.

```yaml
rule-providers:
  acl4ssr_ai:
    type: inline
    behavior: classical
    payload: [...]

rules:
  - RULE-SET,acl4ssr_openai,__CR_AI_SERVICE_OPENAI
  - RULE-SET,cr_ai_rules_claude,__CR_AI_SERVICE_CLAUDE
  - RULE-SET,cr_ai_rules_gemini,__CR_AI_SERVICE_GEMINI
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
      per-node, per-service AI qualification through temporary Mihomo mixed ports
            ↓
      keep service-qualified union + build hidden service routes + prune empty countries
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

CI runs schema/lint/unit/repository-safety checks on Python 3.11 and 3.12, byte-for-byte deterministic generation, and real Mihomo v1.19.30/v1.19.29 configuration/startup integration tests, including selector-to-mixed-port AI status validation and service-aware post-qualification routing validation. Production runs only from trusted `main` and creates no credential-bearing GitHub Artifact.

## License

MIT
