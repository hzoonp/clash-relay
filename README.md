# clash-relay

[简体中文](README.zh-CN.md)

`clash-relay` is a deterministic, fail-closed Mihomo configuration builder designed to run safely from a **public GitHub repository**. Public YAML contains policy and subscription metadata only. Real subscription URLs stay in GitHub Actions Secrets, generated node credentials exist only on an ephemeral GitHub-hosted runner, and the validated standalone `config.yaml` is published only to private Cloudflare Workers KV.

FlClash consumes the token-protected Worker URL. It does not need runtime access to GitHub or ACL4SSR.

> **Credential warning**
>
> The generated configuration contains inline proxy credentials and must be treated as highest-sensitivity data. The supported public production workflow does **not** upload it to Actions Artifacts, Releases, Gists, commits, or Pages.

## Canonical production profile

The repository's production declarations are intentionally smaller than the generic engine:

```text
4 private subscription URLs
  ├─ 订阅源 1
  ├─ 订阅源 2
  ├─ 订阅源 3
  └─ 订阅源 4
          ↓
 single general node inventory
          ↓
        节点选择
          ↓
pinned ACL4SSR Online Full rules
          ↓
17 Chinese visible policy groups
          ↓
Mihomo v1.19.30 + v1.19.29
          ↓
Cloudflare Workers KV
          ↓
FlClash
```

Production enables only:

```yaml
modules:
  general: true
```

`services.yaml` is empty in production and `policies.yaml` contains only the `general` node pool. Legacy production declarations for dedicated ChatGPT, Claude, Gemini, Google Play, bulk, residential, EMBY, high-multiplier, and chain pools have been removed. The generic engine still supports and tests those data-driven capabilities under the isolated `tests/fixtures/project/` tree.

## Visible FlClash groups

The canonical profile exposes exactly 17 Chinese groups:

```text
节点选择
直连
广告拦截
谷歌FCM
微软服务
苹果服务
电报消息
人工智能
网易音乐
游戏平台
油管视频
奈飞视频
巴哈姆特
哔哩哔哩
国内媒体
国外媒体
漏网之鱼
```

Four redundant selectors were removed or merged:

- `Auto` is unnecessary because production has one automatic anchor, `__CR_AUTO_GENERAL_ANY`;
- `App Purify` is merged into `广告拦截`;
- `Microsoft Bing` and `Microsoft OneDrive` are merged into `微软服务`.

Only `节点选择` owns proxy credentials through an inline proxy provider. ACL4SSR service/media groups are lightweight selectors and do not duplicate nodes.

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
  - GEOIP,CN,直连,no-resolve
  - MATCH,漏网之鱼
```

The final profile therefore remains standalone: generated rule providers contain no `url` or `path`, and FlClash/Mihomo does not fetch ACL4SSR at runtime. See [Routing rules and ACL4SSR](docs/rules.md).

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
      fetch + parse + deduplicate
            ↓
      fetch pinned ACL4SSR fragments
            ↓
      generate private standalone YAML
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

The Cloudflare publication gate refuses to run if Artifact, Release, or Gist is enabled. If generation or either Mihomo validation fails, KV is not updated and the previous successful value remains available.

## Generic engine vs. production declarations

The schemas and generator remain data-driven. Generic fixture coverage includes capabilities such as `ai`, `bulk`, `residential`, `emby`, `high_multiplier`, and `chain`, but those are not enabled or declared by the canonical production profile. This separation keeps production minimal without reducing regression coverage for the reusable engine.

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

CI runs schema/lint/unit/repository-safety checks on Python 3.11 and 3.12, byte-for-byte deterministic generation, and real Mihomo v1.19.30/v1.19.29 configuration/startup integration tests. Production runs only from trusted `main` and creates no credential-bearing GitHub Artifact.

## License

MIT
