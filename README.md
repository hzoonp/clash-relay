# clash-relay

[简体中文](README.zh-CN.md)

`clash-relay` is a deterministic Mihomo / FlClash configuration builder for merging multiple private subscriptions into one standalone `config.yaml` while preserving hard source-to-scenario permissions. Public GitHub files contain policy metadata only; real subscription URLs and generated proxy credentials never belong in Git history.

> **Credential warning**
>
> The generated `config.yaml` contains inline proxy credentials and must be treated as highest-sensitivity data. The supported production path publishes only a validated candidate to private Cloudflare Workers KV, not to public Artifacts, Releases, Gists, commits, or Pages.

## Production scenarios

Merging subscriptions does **not** mean every subscription can serve every application:

```text
SUBSCRIPTION_1_URL ──> drop explicit >2x nodes ──┬─> web browsing
                                                 └─> AI

SUBSCRIPTION_2_URL ───────────────────────────────┬─> general applications
SUBSCRIPTION_3_URL ───────────────────────────────┼─> web browsing
SUBSCRIPTION_4_URL ───────────────────────────────┴─> AI
```

Production invariants:

1. `subscription_1` allows only `browsing` and `ai`; it cannot enter `general`.
2. Nodes from `subscription_1` with an explicit multiplier above `2x` are dropped before classification, deduplication, or provider generation.
3. Exactly `2x` is allowed. Nodes with no explicit multiplier marker are retained rather than guessed.
4. YouTube, Netflix, Telegram, games, Microsoft services, media groups, downloads, and the final `MATCH` path use the `general` inventory, so they cannot select `subscription_1`.
5. Generic `ProxyGFWlist` traffic uses a dedicated `网页浏览` browsing inventory. AI uses dedicated AI inventories and service qualification.
6. Final `MATCH` remains `漏网之鱼 -> general`; unknown traffic never receives browsing-only permission merely because it might originate from a browser.

The project deliberately does not use browser process names as the security boundary because process matching is inconsistent across Android, iOS, Windows, macOS, and different Mihomo/FlClash deployments. The boundary is enforced by routing targets plus inventory admission.

## Data flow

```text
GitHub Secrets
  └─ CLASH_RELAY_SUBSCRIPTIONS
          ↓
fetch multiple subscriptions
          ↓
safe parsing / normalization
          ↓
subscription admission
  ├─ subscription_1: drop >2x
  └─ enforce allowed_uses
          ↓
deduplicate / classify country
          ↓
three logical inventories
  ├─ general   : subscription_2+
  ├─ browsing  : subscription_1+
  └─ ai        : subscription_1+
          ↓
pinned ACL4SSR rules + scenario routing
          ↓
OpenAI / Claude / Gemini live qualification
          ↓
Mihomo v1.19.30 + v1.19.29 validation
          ↓
Cloudflare Workers KV
          ↓
FlClash
```

## Subscription declarations

Tracked `subscriptions.yaml` contains no URL. The critical production declarations are:

```yaml
subscriptions:
  - id: subscription_1
    secret_name: SUBSCRIPTION_1_URL
    allowed_uses: [browsing, ai]
    max_node_multiplier: 2.0

  - id: subscription_2
    secret_name: SUBSCRIPTION_2_URL
    allowed_uses: [general, browsing, ai]
```

`subscription_3` and `subscription_4` use the same production permission model as subscription 2.

Common explicit multiplier markers are supported:

```text
HK 2x          -> keep
JP x2.0        -> keep
US 2.01x       -> drop
SG 3倍         -> drop
倍率: 4        -> drop
Unmarked       -> keep
```

If multiple explicit multiplier markers appear in one node name, the highest parsed value is used.

## Scenario inventories

`policies.yaml` creates hard boundaries with separate `source_use` values:

```text
general
  source_use: general
  subscription_1: denied

browsing
  source_use: browsing
  subscription_1: allowed

ai_*
  source_use: ai
  subscription_1: allowed
```

Selection checks `Node.source_allowed_uses` before a provider is generated. A low-latency subscription-1 node therefore cannot leak into general application routing through url-test, fallback, manual selection, or deduplication.

## Routing

`rules/acl4ssr.yaml` pins `ACL4SSR/ACL4SSR` at immutable commit:

```text
c498ae4911f15b19c5ceaef6f8737ca8705b4430
```

Most application rules retain their dedicated ACL4SSR Full targets. Production has two explicit scheduling extensions:

```text
ProxyGFWlist -> 网页浏览 -> browsing inventory
AI/OpenAI    -> 人工智能 -> AI inventories
```

Representative non-browsing routes continue through the general inventory:

```text
Telegram                     -> 电报消息
YouTube                      -> 油管视频
Netflix                      -> 奈飞视频
Epic/Origin/Sony/Steam/...   -> 游戏平台
ChinaMedia                   -> 国内媒体
ProxyMedia                   -> 国外媒体
Download                     -> 全球直连
MATCH                        -> 漏网之鱼
```

This makes subscription 1 structurally absent from those unauthorized scenario providers rather than relying on users not to select it.

## AI live qualification

AI candidates are deterministically classified by node name into SG / JP / US / HK / TW / KR / OTHER. A trusted runner starts a temporary Mihomo instance and probes through candidate nodes:

```text
https://chatgpt.com/
https://claude.ai/
https://gemini.google.com/
```

OpenAI, Claude, and Gemini qualify independently. Network errors, timeouts, TLS failures, or disallowed HTTP statuses fail that candidate for the service. Protected service routing fails closed instead of falling back to an unqualified node.

## GitHub Secrets

The preferred subscription secret is:

```text
CLASH_RELAY_SUBSCRIPTIONS
```

Its value may be JSON:

```json
{
  "SUBSCRIPTION_1_URL": "https://example.invalid/subscription-1",
  "SUBSCRIPTION_2_URL": "https://example.invalid/subscription-2",
  "SUBSCRIPTION_3_URL": "https://example.invalid/subscription-3",
  "SUBSCRIPTION_4_URL": "https://example.invalid/subscription-4"
}
```

Cloudflare KV publication also requires:

```text
Secret:   CLOUDFLARE_API_TOKEN
Variable: CLOUDFLARE_ACCOUNT_ID
Variable: CLOUDFLARE_KV_NAMESPACE_TITLE
```

Never write real subscription URLs into tracked YAML, README files, workflow arguments, or logs.

## Validation contract

Tests directly lock the production invariants:

```text
subscription_1 / 1x       -> keep
subscription_1 / 2x       -> keep
subscription_1 / 2.01x    -> drop
subscription_1 / unmarked -> keep

subscription_1 -> general  -> denied
subscription_1 -> browsing -> allowed
subscription_1 -> ai       -> allowed

ProxyGFWlist -> 网页浏览
YouTube/Netflix/Game/Download/MATCH -> not browsing
```

CI runs Ruff, unit tests, repository safety auditing, deterministic generation, and real Mihomo configuration/startup integration against two stable Mihomo versions on Python 3.11/3.12 where applicable.

## Local development

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.lock -e .
ruff check .
ruff format --check .
pytest -m "not integration"
python scripts/repository_audit.py
```

## Documentation

- [Configuration model](docs/configuration.md)
- [Architecture](docs/architecture.md)
- [ACL4SSR routing model](docs/rules.md)
- [Security model](docs/security.md)
- [Publishing](docs/publishing.md)
- [Release checklist](docs/release-checklist.md)
