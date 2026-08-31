# clash-relay

[简体中文](README.zh-CN.md)

`clash-relay` is a deterministic Mihomo / FlClash configuration builder that merges multiple private subscriptions into one standalone `config.yaml` while preserving hard source-to-scenario permissions. Public GitHub files contain policy metadata only; real subscription URLs and generated proxy credentials never belong in Git history.

> **Credential warning**
>
> The generated `config.yaml` contains inline proxy credentials and is highest-sensitivity data. Production publishes only validated bytes to private Cloudflare Workers KV, never to public Artifacts, Releases, Gists, commits, or Pages.

## Start with a fork

For a fresh fork, use the [Fork quickstart](docs/quickstart.md):

```text
Fork
  -> add CLASH_RELAY_SUBSCRIPTIONS
  -> add Cloudflare KV secret/variables
  -> manual dry-run (publish=false)
  -> inspect aggregate production proof
  -> publish=true
  -> optional dual-core validated rollback
```

The production pipeline also performs live browsing qualification, private anonymous scheduler history, independent OpenAI/Claude/Gemini qualification, end-to-end source reachability auditing, pinned ACL4SSR Online parity, and validation with Mihomo v1.19.30 plus v1.19.29. Different production bytes are preceded by a private previous-good snapshot.

## Public scenarios

FlClash exposes only six primary user decisions:

```text
代理选择
网页浏览
人工智能
流媒体
消息通讯
下载流量
```

ACL4SSR compatibility groups, regional helpers, automatic schedulers, and qualification runtime groups stay hidden. Public selectors do not attach proxy providers directly, so they do not expand raw runtime nodes in the UI.

## Source policy

Merging subscriptions does **not** grant every source access to every scenario:

```text
SUBSCRIPTION_1_URL
  ├─ explicit >2x       -> rejected
  ├─ EMBY-labelled      -> rejected
  ├─ browsing           -> allowed
  ├─ ai                 -> allowed
  └─ general/media/...  -> denied

SUBSCRIPTION_2+
  ├─ general
  ├─ browsing
  └─ ai
```

Production invariants:

1. `subscription_1` allows only `browsing` and `ai`; it cannot enter `general`.
2. EMBY-labelled subscription-1 nodes are rejected case-insensitively before inventory generation.
3. Explicit multipliers strictly above `2x` are rejected before classification, deduplication, and provider generation. Exactly `2x` and unmarked nodes remain eligible.
4. `流媒体`, `消息通讯`, `下载流量`, ACL compatibility selectors, and final `MATCH` use only the general inventory and cannot reach `subscription_1`.
5. Source reachability is audited before and after qualification rather than being left to user selection behavior.

## ACL4SSR fidelity

`rules/acl4ssr.yaml` pins:

```text
repository: ACL4SSR/ACL4SSR
ref: c498ae4911f15b19c5ceaef6f8737ca8705b4430
reference: Clash/config/ACL4SSR_Online.ini
```

Since P10, **ACL4SSR Online owns classification semantics; clash-relay owns source-safe inventories and scheduling**. The repository vendors the Online profile from the same immutable ref and CI/production auditing compares it mechanically instead of reinterpreting the ACL4SSR routing graph by hand.

Canonical classification order:

```text
LocalAreaNetwork -> 全球直连
UnBan            -> 全球直连
BanAD            -> 广告拦截
BanProgramAD     -> intentionally disabled
GoogleFCM        -> 谷歌FCM
GoogleCN         -> 全球直连
SteamCN          -> 全球直连
Microsoft        -> 微软服务
Apple            -> 苹果服务
Telegram         -> 消息通讯

AI / OpenAI      -> 人工智能    # clash-relay extension
ProxyMedia       -> 流媒体
Download         -> 下载流量    # clash-relay extension
ProxyLite        -> 网页浏览
ChinaDomain      -> 全球直连
ChinaCompanyIp   -> 全球直连
GEOIP,CN         -> 全球直连
MATCH            -> 漏网之鱼
```

The old canonical `ProxyGFWlist` substitution is removed. Standalone YouTube/Netflix/Game/Bilibili/ChinaMedia classifiers no longer interleave with the ACL4SSR Online baseline.

### Explicit deviations

- `BanProgramAD.list / 应用净化` remains **disabled** because it caused confirmed mobile image/CDN breakage. Basic `BanAD.list` remains enabled.
- AI/OpenAI runs before `ProxyMedia` so protected AI domains are not swallowed by the broad media list.
- `Download.list` runs before `ProxyLite` and targets `下载流量`.
- ACL4SSR's single-subscription raw-node `.*` wildcard is adapted to source-aware scenario selectors rather than copied literally, preserving multi-subscription source isolation.

Any additional classification deviation must be declared in the fidelity contract or CI and production auditing fail closed.

## Hidden ACL compatibility selectors

Default member order follows the pinned ACL4SSR Online profile:

```text
全球直连: DIRECT -> 代理选择 -> 自动选择
广告拦截: REJECT -> DIRECT
谷歌FCM: 代理选择 -> 全球直连 -> 自动选择
微软服务: 全球直连 -> 代理选择
苹果服务: 代理选择 -> 全球直连
漏网之鱼: 代理选择 -> 全球直连 -> 自动选择
```

These groups remain hidden and do not add top-level FlClash clutter.

## Browsing regional scheduling

`ProxyLite -> 网页浏览` keeps the P8 browsing inventory and live qualification model:

```text
网页自动
  ├─ US Stable -> US Reserve
  ├─ SG Stable -> SG Reserve
  ├─ JP Stable -> JP Reserve
  ├─ TW Stable -> TW Reserve
  ├─ KR Stable -> KR Reserve
  ├─ HK Stable -> HK Reserve
  └─ OTHER Stable -> OTHER Reserve
```

Default automatic region order is:

```text
US -> SG -> JP -> TW -> KR -> HK -> OTHER
```

Automatic mode crosses regions only when the entire preferred region is unavailable. A manually selected region never silently crosses to another country. Historical demotion is region-local and moves currently qualified nodes from Stable to Reserve without deleting automatic failover eligibility.

## AI qualification

The AI inventory is independent from general and browsing. Hong Kong is excluded before AI qualification. OpenAI, Claude, and Gemini are tested independently and fail closed per service with preference order:

```text
US -> SG -> JP -> TW -> KR -> OTHER
```

A protected service never falls back to an unqualified node.

## Data flow

```text
GitHub Secrets
  -> fetch subscriptions
  -> safe parse / normalize
  -> source admission (allowed_uses / EMBY / >2x)
  -> deduplicate / classify country
  -> general / browsing / ai inventories
  -> ACL4SSR Online classification + AI/Download extensions
  -> browsing qualification + regional Stable/Reserve
  -> OpenAI / Claude / Gemini qualification
  -> post-qualification reachability + ACL fidelity audit
  -> Mihomo v1.19.30 + v1.19.29
  -> previous-good snapshot
  -> Cloudflare Workers KV
  -> FlClash
```

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

CI and production gates lock these behaviors directly:

```text
subscription_1 -> general/media/messaging/download/final -> denied
subscription_1 -> browsing/ai                         -> allowed
subscription_1 EMBY                                   -> rejected
subscription_1 >2x                                    -> rejected

ProxyMedia -> 流媒体
Telegram   -> 消息通讯
Download   -> 下载流量
ProxyLite  -> 网页浏览
MATCH      -> 漏网之鱼
BanProgramAD -> disabled
```

The pipeline also verifies pinned ACL4SSR upstream/vendored parity, Ruff, Python 3.11/3.12 unit tests, repository safety auditing, deterministic generation, Routing V2 Drift Guard, and real Mihomo v1.19.30 / v1.19.29 startup integration.

## Local development

```bash
python -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-dev.lock -e .
ruff check .
ruff format --check .
pytest -m "not integration"
python scripts/audit_acl4ssr_fidelity.py
python scripts/repository_audit.py
```

## Documentation

- [Fork quickstart](docs/quickstart.md)
- [配置快速上手](docs/quickstart.zh-CN.md)
- [Configuration model](docs/configuration.md)
- [Architecture](docs/architecture.md)
- [ACL4SSR routing model](docs/rules.md)
- [Security model](docs/security.md)
- [Publishing](docs/publishing.md)
- [Versioning and compatibility](docs/versioning.md)
- [Release checklist](docs/release-checklist.md)
